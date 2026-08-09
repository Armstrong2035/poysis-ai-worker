"""
NetworkStack — VPC with public + isolated subnets across 2 AZs.

Cost optimisation: NO NAT Gateway.
  - ECS tasks run in PUBLIC subnets with assigned public IPs — outbound internet
    works directly, no NAT Gateway needed. Saves ~$35/month.
  - RDS stays in ISOLATED subnets (no internet route in either direction).
  - Trade-off: tasks have a public IP, but the security group only allows inbound
    from the ALB, so the exposure is the same as before.

To add a NAT Gateway later (e.g. for compliance), change nat_gateways=1,
move ECS tasks back to PRIVATE_WITH_EGRESS subnets in app_stack.py, and
remove assign_public_ip=True from the Fargate service.
"""
import aws_cdk as cdk
from aws_cdk import aws_ec2 as ec2
from constructs import Construct


class NetworkStack(cdk.Stack):
    def __init__(self, scope: Construct, id: str, **kwargs):
        super().__init__(scope, id, **kwargs)

        self.vpc = ec2.Vpc(
            self,
            "PoisysVpc",
            max_azs=2,
            nat_gateways=0,  # no NAT GW — ECS tasks use public subnets instead
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    name="Public",
                    subnet_type=ec2.SubnetType.PUBLIC,
                    cidr_mask=24,
                ),
                # Isolated subnet for RDS — no route to/from internet at all.
                ec2.SubnetConfiguration(
                    name="Isolated",
                    subnet_type=ec2.SubnetType.PRIVATE_ISOLATED,
                    cidr_mask=24,
                ),
            ],
        )

        # VPC Flow Logs → CloudWatch (helps debug connectivity issues)
        self.vpc.add_flow_log(
            "FlowLog",
            traffic_type=ec2.FlowLogTrafficType.REJECT,
        )

        cdk.CfnOutput(self, "VpcId", value=self.vpc.vpc_id)
