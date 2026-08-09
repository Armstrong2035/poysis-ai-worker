"""
SecretsStack — all application secrets in AWS Secrets Manager.

Each secret is a single JSON object so the ECS task definition can
inject individual keys as environment variables using the `valueFrom`
field pointing to `secret-arn:KEY::`.

Secrets are created with placeholder values. After `cdk deploy`, populate
real values with:

    aws secretsmanager put-secret-value \\
        --secret-id poysis/app \\
        --secret-string '{"OPENAI_API_KEY":"sk-...", ...}'

Or use the AWS Console → Secrets Manager → poysis/app → Retrieve secret value → Edit.
"""
import aws_cdk as cdk
from aws_cdk import aws_secretsmanager as sm
from constructs import Construct


class SecretsStack(cdk.Stack):
    def __init__(self, scope: Construct, id: str, **kwargs):
        super().__init__(scope, id, **kwargs)

        # Single secret containing all app-level keys. Storing them together
        # keeps IAM policy surface minimal (one ARN to grant) and makes the
        # ECS task definition readable (one secretsFrom block).
        self.app_secrets = sm.Secret(
            self,
            "AppSecrets",
            secret_name="poysis/app",
            description="All Poysis AI Worker application secrets",
            generate_secret_string=sm.SecretStringGenerator(
                secret_string_template="""{
                    "OPENAI_API_KEY": "REPLACE_ME",
                    "BEDROCK_API_KEY": "REPLACE_ME",
                    "GEMINI_API_KEY": "REPLACE_ME",
                    "DEEPSEEK_API_KEY": "REPLACE_ME",
                    "LLAMA_CLOUD_API_KEY": "REPLACE_ME",
                    "GOOGLE_CLIENT_ID": "REPLACE_ME",
                    "GOOGLE_CLIENT_SECRET": "REPLACE_ME",
                    "GOOGLE_REDIRECT_URI": "REPLACE_ME",
                    "YOUTUBE_API_KEY": "REPLACE_ME",
                    "YT_DLP_PROXY": "REPLACE_ME",
                    "NANGO_BASE_URL": "REPLACE_ME",
                    "NANGO_SECRET_KEY": "REPLACE_ME",
                    "CLIENT_URL": "REPLACE_ME",
                    "MCP_SERVER_URL": "REPLACE_ME",
                    "WORKER_BASE_URL": "REPLACE_ME",
                    "RESEND_API_KEY": "REPLACE_ME",
                    "CONSOLIDATION_SYNC_KEY": "REPLACE_ME",
                    "POYSIS_ADMIN_USER_IDS": "REPLACE_ME",
                    "SEED_WORKSPACE_ID": "REPLACE_ME",
                    "POYSIS_SEED_USER_ID": "REPLACE_ME",
                    "WORKSPACE_ID": "REPLACE_ME",
                    "OPENROUTER_API_KEY": "REPLACE_ME",
                    "VERTEX_API_KEY": "REPLACE_ME"
                }""",
                generate_string_key="_unused",
            ),
        )

        cdk.CfnOutput(
            self,
            "AppSecretsArn",
            value=self.app_secrets.secret_arn,
            description="ARN of the poysis/app secret - needed for ECS task role",
        )
