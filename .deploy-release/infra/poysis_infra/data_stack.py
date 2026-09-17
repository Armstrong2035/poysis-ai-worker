"""
DataStack — RDS PostgreSQL 16 with pgvector extension.

Instance sizing:
  db.t4g.small — 2 vCPU, 2 GB RAM. ~$25/month (down from $50 for medium).
  pgvector HNSW queries are index-driven and work fine with 2 GB.
  Upgrade to db.t4g.medium if you see memory pressure in CloudWatch
  (FreeableMemory < 200 MB consistently).

  work_mem reduced to 32 MB to fit within the 2 GB budget:
  32 MB × 10 typical connections = 320 MB, leaving ~1.7 GB for shared_buffers
  and OS page cache.

Storage:
  100 GB gp3 with autoscaling to 500 GB. gp3 has free provisioned IOPS up to
  3000 IOPS at no extra cost vs gp2.

Multi-AZ: disabled for cost. Enable for production SLAs (multi_az=True).
Backups: 7-day automated backup window.
"""
import aws_cdk as cdk
from aws_cdk import (
    aws_ec2 as ec2,
    aws_rds as rds,
    aws_secretsmanager as sm,
)
from constructs import Construct


class DataStack(cdk.Stack):
    def __init__(self, scope: Construct, id: str, vpc: ec2.Vpc, **kwargs):
        super().__init__(scope, id, **kwargs)

        # Security group: allow Postgres from the VPC CIDR. ECS tasks run in
        # public subnets (no NAT Gateway), so the simplest dependency-safe rule
        # is a CIDR-based ingress — no cross-stack SG reference, no cycle.
        self.rds_sg = ec2.SecurityGroup(
            self,
            "RdsSg",
            vpc=vpc,
            description="Allow Postgres from within the VPC",
            allow_all_outbound=False,
        )
        self.rds_sg.add_ingress_rule(
            ec2.Peer.ipv4(vpc.vpc_cidr_block),
            ec2.Port.tcp(5432),
            "VPC to RDS 5432",
        )
        # RDS master credentials — auto-generated and stored in Secrets Manager
        self.rds_secret = rds.DatabaseSecret(
            self,
            "RdsSecret",
            username="poysis",
            secret_name="poysis/rds",
        )

        # Parameter group with pgvector settings.
        # shared_preload_libraries must include vector for the extension to load.
        param_group = rds.ParameterGroup(
            self,
            "PgParams",
            engine=rds.DatabaseInstanceEngine.postgres(
                version=rds.PostgresEngineVersion.VER_16_4
            ),
            description="Poysis - pgvector enabled",
            parameters={
                "shared_preload_libraries": "pg_stat_statements",
                "max_parallel_workers_per_gather": "2",  # reduced for 2 GB instance
                # 32 MB × 10 connections = 320 MB — fits comfortably in 2 GB.
                "work_mem": "32768",  # KB → 32 MB
            },
        )

        self.rds_instance = rds.DatabaseInstance(
            self,
            "RdsInstance",
            engine=rds.DatabaseInstanceEngine.postgres(
                version=rds.PostgresEngineVersion.VER_16_4
            ),
            instance_type=ec2.InstanceType.of(
                ec2.InstanceClass.T4G, ec2.InstanceSize.SMALL  # 2 GB RAM — ~$25/mo
            ),
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PRIVATE_ISOLATED
            ),
            security_groups=[self.rds_sg],
            credentials=rds.Credentials.from_secret(self.rds_secret),
            database_name="poysis",
            parameter_group=param_group,
            # Storage
            allocated_storage=100,
            max_allocated_storage=500,
            storage_type=rds.StorageType.GP3,
            storage_encrypted=True,
            # Backups — 1 day: the account is on AWS Free Tier, which caps automated
            # backup retention below the default 7-day setting. 1 day keeps automated
            # backups without incurring backup-storage charges. Raise once off free tier.
            backup_retention=cdk.Duration.days(1),
            deletion_protection=True,
            # Monitoring
            enable_performance_insights=True,
            monitoring_interval=cdk.Duration.seconds(60),
            # Cost — no Multi-AZ for now; set multi_az=True for production SLAs
            multi_az=False,
            # On destroy: keep the data (safety net)
            removal_policy=cdk.RemovalPolicy.RETAIN,
        )

        cdk.CfnOutput(
            self,
            "RdsEndpoint",
            value=self.rds_instance.db_instance_endpoint_address,
            description="RDS Postgres endpoint hostname",
        )
        cdk.CfnOutput(
            self,
            "RdsSecretArn",
            value=self.rds_secret.secret_arn,
            description="ARN of the RDS master credentials secret",
        )
