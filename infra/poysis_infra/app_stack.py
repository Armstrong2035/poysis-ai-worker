"""
AppStack — ECR, ECS Fargate service, ALB, CloudWatch logs, EventBridge cron.

Architecture (cost-optimised):
  Internet → ALB (public subnet, port 80) → ECS Fargate task (PUBLIC subnet, port 8000)
                                                  ↓
                                             RDS Postgres (isolated subnet, port 5432)

  ECS tasks run in PUBLIC subnets with assigned public IPs. This eliminates the
  NAT Gateway (~$35/mo) while keeping the same effective security — inbound is
  still gated by the ALB and the ECS security group only allows traffic from the ALB.

Task sizing:
  1 vCPU / 2 GB RAM — ~$30/month (down from $60 for 2vCPU/4GB).
  The app is mostly I/O-bound (LLM API calls, DB queries). BERTopic/UMAP/HDBSCAN
  clustering runs infrequently and is single-threaded per workspace. If OOM kills
  appear in CloudWatch during large snapshot jobs, bump to cpu=2048/memory=4096.

Auto-scaling:
  Min 1 task, Max 3 tasks. Scale out when average CPU > 70% for 2 minutes.

EventBridge cron:
  Fires every 6 hours → Lambda → POST /consolidation/sync.
"""
import json
import aws_cdk as cdk
from aws_cdk import (
    aws_ec2 as ec2,
    aws_ecr as ecr,
    aws_ecs as ecs,
    aws_ecs_patterns as ecs_patterns,
    aws_iam as iam,
    aws_logs as logs,
    aws_rds as rds,
    aws_secretsmanager as sm,
    aws_events as events,
    aws_events_targets as targets,
    aws_lambda as lambda_,
)
from constructs import Construct


class AppStack(cdk.Stack):
    def __init__(
        self,
        scope: Construct,
        id: str,
        vpc: ec2.Vpc,
        rds_instance: rds.DatabaseInstance,
        rds_secret: sm.Secret,
        app_secrets: sm.Secret,
        **kwargs,
    ):
        super().__init__(scope, id, **kwargs)

        # ── ECR Repository ──────────────────────────────────────────────────
        self.ecr_repo = ecr.Repository(
            self,
            "EcrRepo",
            repository_name="poysis-worker",
            image_scan_on_push=True,
            # Keep the last 10 images; older ones are cleaned up automatically.
            lifecycle_rules=[
                ecr.LifecycleRule(
                    max_image_count=10,
                    description="Keep last 10 images",
                )
            ],
            removal_policy=cdk.RemovalPolicy.RETAIN,
        )

        # ── CloudWatch Log Group ─────────────────────────────────────────────
        log_group = logs.LogGroup(
            self,
            "AppLogs",
            log_group_name="/ecs/poysis-worker",
            retention=logs.RetentionDays.ONE_MONTH,
            removal_policy=cdk.RemovalPolicy.DESTROY,
        )

        # ── ECS Cluster ──────────────────────────────────────────────────────
        cluster = ecs.Cluster(
            self,
            "EcsCluster",
            vpc=vpc,
            cluster_name="poysis",
            container_insights=True,
        )

        # ── Task Execution Role (pulls images, writes logs) ───────────────────
        execution_role = iam.Role(
            self,
            "TaskExecutionRole",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AmazonECSTaskExecutionRolePolicy"
                )
            ],
        )
        # Allow pulling secrets to inject as env vars
        app_secrets.grant_read(execution_role)
        rds_secret.grant_read(execution_role)

        # ── Task Role (runtime permissions for the container) ─────────────────
        task_role = iam.Role(
            self,
            "TaskRole",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
        )
        # Bedrock: invoke models (embeddings + LLMs)
        task_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "bedrock:InvokeModel",
                    "bedrock:InvokeModelWithResponseStream",
                ],
                resources=["*"],
            )
        )
        # Secrets Manager: read secrets at runtime (for any dynamic reads)
        app_secrets.grant_read(task_role)
        rds_secret.grant_read(task_role)

        # ── Task Definition ──────────────────────────────────────────────────
        task_def = ecs.FargateTaskDefinition(
            self,
            "TaskDef",
            cpu=1024,              # 1 vCPU  — ~$30/mo (down from 2vCPU/$60)
            memory_limit_mib=2048, # 2 GB RAM — matches db.t4g.small
            execution_role=execution_role,
            task_role=task_role,
            family="poysis-worker",
        )

        # Build the environment dict — non-secret config only.
        # Secrets are injected separately via secrets= so they never appear
        # in the task definition JSON in plaintext.
        environment = {
            "PORT": "8000",
            "PYTHONUNBUFFERED": "1",
            "AWS_REGION": self.region,
            "AWS_BEDROCK_REGION": self.region,
            "BEDROCK_OPENAI_BASE": f"https://bedrock-runtime.{self.region}.amazonaws.com/openai/v1",
            "BEDROCK_CHAT_MODEL": "openai.gpt-5.6-luna",
        }

        # Helper to reference a key inside a JSON secret
        def _secret_val(secret: sm.Secret, key: str) -> ecs.Secret:
            return ecs.Secret.from_secrets_manager(secret, key)

        # All secret env vars injected from Secrets Manager at container start.
        secrets_env = {
            # AI
            "OPENAI_API_KEY":        _secret_val(app_secrets, "OPENAI_API_KEY"),
            "BEDROCK_API_KEY":       _secret_val(app_secrets, "BEDROCK_API_KEY"),
            "GEMINI_API_KEY":        _secret_val(app_secrets, "GEMINI_API_KEY"),
            "DEEPSEEK_API_KEY":      _secret_val(app_secrets, "DEEPSEEK_API_KEY"),
            "LLAMA_CLOUD_API_KEY":   _secret_val(app_secrets, "LLAMA_CLOUD_API_KEY"),
            # Google OAuth
            "GOOGLE_CLIENT_ID":      _secret_val(app_secrets, "GOOGLE_CLIENT_ID"),
            "GOOGLE_CLIENT_SECRET":  _secret_val(app_secrets, "GOOGLE_CLIENT_SECRET"),
            "GOOGLE_REDIRECT_URI":   _secret_val(app_secrets, "GOOGLE_REDIRECT_URI"),
            # YouTube
            "YOUTUBE_API_KEY":       _secret_val(app_secrets, "YOUTUBE_API_KEY"),
            "YT_DLP_PROXY":          _secret_val(app_secrets, "YT_DLP_PROXY"),
            # Nango
            "NANGO_BASE_URL":        _secret_val(app_secrets, "NANGO_BASE_URL"),
            "NANGO_SECRET_KEY":      _secret_val(app_secrets, "NANGO_SECRET_KEY"),
            # App URLs
            "CLIENT_URL":            _secret_val(app_secrets, "CLIENT_URL"),
            "MCP_SERVER_URL":        _secret_val(app_secrets, "MCP_SERVER_URL"),
            "WORKER_BASE_URL":       _secret_val(app_secrets, "WORKER_BASE_URL"),
            # Email
            "RESEND_API_KEY":        _secret_val(app_secrets, "RESEND_API_KEY"),
            # Cron guard
            "CONSOLIDATION_SYNC_KEY": _secret_val(app_secrets, "CONSOLIDATION_SYNC_KEY"),
            # Admin
            "POYSIS_ADMIN_USER_IDS": _secret_val(app_secrets, "POYSIS_ADMIN_USER_IDS"),
            "SEED_WORKSPACE_ID":     _secret_val(app_secrets, "SEED_WORKSPACE_ID"),
            "POYSIS_SEED_USER_ID":   _secret_val(app_secrets, "POYSIS_SEED_USER_ID"),
            "WORKSPACE_ID":          _secret_val(app_secrets, "WORKSPACE_ID"),
            "OPENROUTER_API_KEY":    _secret_val(app_secrets, "OPENROUTER_API_KEY"),
            "VERTEX_API_KEY":        _secret_val(app_secrets, "VERTEX_API_KEY"),
            # RDS — username, password, host, port, dbname all from one secret
            "DB_HOST":     ecs.Secret.from_secrets_manager(rds_secret, "host"),
            "DB_PORT":     ecs.Secret.from_secrets_manager(rds_secret, "port"),
            "DB_NAME":     ecs.Secret.from_secrets_manager(rds_secret, "dbname"),
            "DB_USER":     ecs.Secret.from_secrets_manager(rds_secret, "username"),
            "DB_PASSWORD": ecs.Secret.from_secrets_manager(rds_secret, "password"),
        }

        container = task_def.add_container(
            "AppContainer",
            image=ecs.ContainerImage.from_ecr_repository(
                self.ecr_repo, tag="latest"
            ),
            environment=environment,
            secrets=secrets_env,
            logging=ecs.LogDrivers.aws_logs(
                stream_prefix="poysis",
                log_group=log_group,
            ),
            health_check=ecs.HealthCheck(
                command=["CMD-SHELL", "curl -f http://localhost:8000/ping || exit 1"],
                interval=cdk.Duration.seconds(30),
                timeout=cdk.Duration.seconds(10),
                retries=3,
                start_period=cdk.Duration.seconds(60),
            ),
        )
        container.add_port_mappings(ecs.PortMapping(container_port=8000))

        # ── Security Group for ECS tasks ─────────────────────────────────────
        ecs_sg = ec2.SecurityGroup(
            self,
            "EcsSg",
            vpc=vpc,
            description="Poysis ECS tasks",
            allow_all_outbound=True,  # outbound for LLM APIs, Nango, YouTube, etc.
        )

        # ── ALB + Fargate Service ────────────────────────────────────────────
        self.fargate_service = ecs_patterns.ApplicationLoadBalancedFargateService(
            self,
            "FargateService",
            cluster=cluster,
            task_definition=task_def,
            desired_count=1,
            service_name="poysis-worker",
            public_load_balancer=True,
            listener_port=80,
            # Tasks run in PUBLIC subnets — no NAT Gateway needed.
            # assign_public_ip=True gives each task a public IP for outbound traffic.
            task_subnets=ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PUBLIC
            ),
            assign_public_ip=True,
            security_groups=[ecs_sg],
            health_check_grace_period=cdk.Duration.seconds(120),
            circuit_breaker=ecs.DeploymentCircuitBreaker(rollback=True),
        )

        # Lock down inbound on the ECS SG — only the ALB may reach port 8000.
        # (ALB SG is available after the service construct is created above.)
        ecs_sg.add_ingress_rule(
            ec2.Peer.security_group_id(
                self.fargate_service.load_balancer.connections.security_groups[0].security_group_id
            ),
            ec2.Port.tcp(8000),
            "ALB → ECS task port 8000",
        )

        # ALB health check matches the /ping endpoint
        self.fargate_service.target_group.configure_health_check(
            path="/ping",
            healthy_http_codes="200",
            interval=cdk.Duration.seconds(30),
            timeout=cdk.Duration.seconds(10),
            healthy_threshold_count=2,
            unhealthy_threshold_count=3,
        )

        # Allow ECS tasks to reach RDS
        rds_instance.connections.allow_from(
            ecs_sg, ec2.Port.tcp(5432), "ECS → RDS"
        )

        # ── Auto-scaling ─────────────────────────────────────────────────────
        scaling = self.fargate_service.service.auto_scale_task_count(
            min_capacity=1, max_capacity=3
        )
        scaling.scale_on_cpu_utilization(
            "CpuScaling",
            target_utilization_percent=70,
            scale_in_cooldown=cdk.Duration.seconds(120),
            scale_out_cooldown=cdk.Duration.seconds(60),
        )

        # ── EventBridge cron → sync endpoint ────────────────────────────────
        # A small Lambda calls POST /consolidation/sync every 6 hours.
        # The CONSOLIDATION_SYNC_KEY is read from Secrets Manager at Lambda
        # invocation time via an env var injected at deploy.
        sync_lambda = lambda_.Function(
            self,
            "SyncCron",
            runtime=lambda_.Runtime.PYTHON_3_11,
            handler="index.handler",
            code=lambda_.Code.from_inline(
                """
import json, os, urllib.request
import boto3

def handler(event, context):
    url = os.environ["WORKER_URL"] + "/consolidation/sync"
    secret = boto3.client("secretsmanager").get_secret_value(
        SecretId=os.environ["APP_SECRET_ARN"]
    )["SecretString"]
    key = json.loads(secret)["CONSOLIDATION_SYNC_KEY"]
    req = urllib.request.Request(
        url,
        method="POST",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        data=b"{}",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            print(f"sync OK: {resp.status}")
    except Exception as e:
        print(f"sync error: {e}")
        raise
"""
            ),
            environment={
                # WORKER_URL is the ALB DNS — set after first deploy.
                # Update this env var once you have the ALB DNS name from the
                # CfnOutput below, then redeploy just this stack.
                "WORKER_URL": f"http://{self.fargate_service.load_balancer.load_balancer_dns_name}",
                "APP_SECRET_ARN": app_secrets.secret_arn,
            },
            timeout=cdk.Duration.seconds(60),
        )
        app_secrets.grant_read(sync_lambda)

        events.Rule(
            self,
            "SyncSchedule",
            schedule=events.Schedule.rate(cdk.Duration.hours(6)),
            description="Trigger Poysis consolidation sync every 6 hours",
            targets=[targets.LambdaFunction(sync_lambda)],
        )

        # ── Outputs ───────────────────────────────────────────────────────────
        cdk.CfnOutput(
            self,
            "AlbDns",
            value=self.fargate_service.load_balancer.load_balancer_dns_name,
            description="ALB DNS — use this as your WORKER_BASE_URL",
        )
        cdk.CfnOutput(
            self,
            "EcrRepoUri",
            value=self.ecr_repo.repository_uri,
            description="ECR repo URI for docker push",
        )
        cdk.CfnOutput(
            self,
            "EcsClusterName",
            value=cluster.cluster_name,
            description="ECS cluster name for CI/CD deploy step",
        )
        cdk.CfnOutput(
            self,
            "EcsServiceName",
            value=self.fargate_service.service.service_name,
            description="ECS service name for CI/CD deploy step",
        )
