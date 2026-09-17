#!/usr/bin/env python3
"""
Poysis AI Worker — AWS CDK entrypoint.

Deploy:
    cd infra
    pip install -r requirements.txt
    cdk bootstrap aws://ACCOUNT_ID/us-east-1
    cdk deploy --all
"""
import aws_cdk as cdk
from poysis_infra.network_stack import NetworkStack
from poysis_infra.data_stack import DataStack
from poysis_infra.secrets_stack import SecretsStack
from poysis_infra.app_stack import AppStack

app = cdk.App()

env = cdk.Environment(
    account=app.node.try_get_context("account"),
    region=app.node.try_get_context("region") or "us-east-1",
)

network = NetworkStack(app, "PoisysNetwork", env=env)
secrets = SecretsStack(app, "PoisysSecrets", env=env)
data = DataStack(app, "PoisysData", vpc=network.vpc, env=env)
AppStack(
    app,
    "PoisysApp",
    vpc=network.vpc,
    rds_secret=data.rds_secret,
    app_secrets=secrets.app_secrets,
    env=env,
)

app.synth()
