"""
AuthStack — Amazon Cognito user pool for application authentication.

Replaces Supabase Auth (deleted project) as the identity provider for the
Next.js apps (studio, developer, marketplace) and the worker.

Design notes:
  - Email is the sign-in alias (matches the old Supabase username behaviour).
  - Email confirmation is NOT required: the old Supabase pool had email
    confirmation disabled, so sign-up returns a session immediately. Cognito
    confirms the user right away when email is not auto-verified, which keeps
    the same first-run flow. A Pre Sign-up trigger is not needed.
  - One public web client serves all three apps. A single audience keeps the
    worker's JWT verification to one client id and gives cross-subdomain SSO
    for free (the cookie domain is set in the frontend, not here).
  - No client secret: the client runs in the browser (SPA), which cannot
    protect a SECRET_HASH.
  - Auth flows are left at the CDK default, which is
    ALLOW_USER_SRP_AUTH + ALLOW_REFRESH_TOKEN_AUTH + ALLOW_CUSTOM_AUTH
    (no ALLOW_USER_PASSWORD_AUTH), so raw passwords never leave the client
    and Amplify's SRP sign-in and token refresh both work.
  - Refresh-token rotation is intentionally NOT enabled yet. Amplify v6
    refreshes via the REFRESH_TOKEN_AUTH flow, which rotation disables. Turn
    rotation on only after the frontend is verified against
    GetTokensFromRefreshToken.
"""

import aws_cdk as cdk
from aws_cdk import aws_cognito as cognito
from aws_cdk import aws_lambda as lambda_
from constructs import Construct


class AuthStack(cdk.Stack):
    def __init__(self, scope: Construct, id: str, **kwargs):
        super().__init__(scope, id, **kwargs)

        self.user_pool = cognito.UserPool(
            self,
            "UserPool",
            user_pool_name="poysis-users",
            self_sign_up_enabled=True,
            sign_in_aliases=cognito.SignInAliases(email=True),
            standard_attributes=cognito.StandardAttributes(
                email=cognito.StandardAttribute(required=True, mutable=True),
            ),
            # No auto-verified email → users are confirmed at sign-up, matching
            # the previous "email confirmation disabled" behaviour. Forgot-password
            # still delivers a code to the email on file regardless.
            auto_verify=cognito.AutoVerifiedAttrs(email=False),
            password_policy=cognito.PasswordPolicy(
                min_length=8,
                require_lowercase=True,
                require_uppercase=True,
                require_digits=True,
                require_symbols=False,
            ),
            account_recovery=cognito.AccountRecovery.EMAIL_ONLY,
            deletion_protection=True,
            removal_policy=cdk.RemovalPolicy.RETAIN,
        )

        # Auto-confirm users at sign-up (email confirmation disabled, matching
        # the previous Supabase pool). Without this a user is left UNCONFIRMED
        # and cannot sign in until a code that never arrives is entered.
        pre_sign_up = lambda_.Function(
            self,
            "PreSignUp",
            runtime=lambda_.Runtime.NODEJS_20_X,
            handler="index.handler",
            code=lambda_.Code.from_inline(
                """
exports.handler = async (event) => {
  event.response.autoConfirmUser = true;
  event.response.autoVerifyEmail = true;
  return event;
};
"""
            ),
        )
        self.user_pool.add_trigger(
            cognito.UserPoolOperation.PRE_SIGN_UP, pre_sign_up
        )

        # Public SPA client shared by studio / developer / marketplace.
        # No OAuth/hosted UI — custom pages use Amplify's SRP flow instead, so
        # disable the CDK's dummy OAuth defaults (example.com callback).
        self.web_client = self.user_pool.add_client(
            "WebClient",
            user_pool_client_name="poysis-web",
            generate_secret=False,
            prevent_user_existence_errors=True,
            enable_token_revocation=True,
            disable_o_auth=True,
        )
        # Set the auth flows explicitly. The Next.js apps call Cognito from
        # server actions (USER_PASSWORD_AUTH is a legitimate server-side flow),
        # so include it alongside SRP. Refresh is needed for session renewal.
        # The L2 AuthFlow enum has no refresh entry, so use the L1 escape hatch.
        self.web_client.node.default_child.explicit_auth_flows = [
            "ALLOW_USER_SRP_AUTH",
            "ALLOW_USER_PASSWORD_AUTH",
            "ALLOW_REFRESH_TOKEN_AUTH",
            "ALLOW_CUSTOM_AUTH",
        ]

        self.issuer = (
            f"https://cognito-idp.{self.region}.amazonaws.com/{self.user_pool.user_pool_id}"
        )
        self.jwks_url = f"{self.issuer}/.well-known/jwks.json"

        cdk.CfnOutput(self, "UserPoolId", value=self.user_pool.user_pool_id)
        cdk.CfnOutput(
            self,
            "WebClientId",
            value=self.web_client.user_pool_client_id,
            description="Public app client id (worker AUTH_AUDIENCE and frontend userPoolWebClientId)",
        )
        cdk.CfnOutput(self, "Issuer", value=self.issuer)
        cdk.CfnOutput(self, "JwksUrl", value=self.jwks_url)
