"""Environment hardening shared by local-review commands and browsing."""

from __future__ import annotations

from collections.abc import MutableMapping

LOCAL_REVIEW_PROVIDER_ENVIRONMENT = {
    "DATAMAILER_URL": "",
    "DATAMAILER_API_KEY": "",
    "DATAMAILER_CLIENT": "",
    "DATAMAILER_AUDIENCE": "",
    "DATAMAILER_FROM_EMAIL": "",
    "DATAMAILER_STRICT": "0",
    "DATAMAILER_TIMEOUT_SECONDS": "0",
    "DATAMAILER_TRANSACTIONAL_DRY_RUN": "1",
    "DATAMAILER_WEBHOOK_TOKEN": "",
    "DATAMAILER_IMPORT_S3_BUCKET": "",
    "DATAMAILER_IMPORT_S3_PREFIX": "",
    "DATAMAILER_IMPORT_URL_EXPIRES_SECONDS": "0",
    "DATAMAILER_IMPORT_S3_REGION": "",
    "DATAMAILER_SYNC_ON_USER_CREATE": "0",
    "DATAMAILER_OUTBOX_DISPATCH_IMMEDIATELY": "0",
    "AWS_ACCESS_KEY_ID": "",
    "AWS_SECRET_ACCESS_KEY": "",
    "AWS_SESSION_TOKEN": "",
    "AWS_SECURITY_TOKEN": "",
    "AWS_PROFILE": "",
    "AWS_DEFAULT_PROFILE": "",
    "AWS_WEB_IDENTITY_TOKEN_FILE": "",
    "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI": "",
    "AWS_CONTAINER_CREDENTIALS_FULL_URI": "",
    "AWS_EC2_METADATA_DISABLED": "true",
    "AWS_REGION": "",
    "AWS_DEFAULT_REGION": "",
    "AWS_CONFIG_FILE": "",
    "AWS_SHARED_CREDENTIALS_FILE": "",
    "AWS_ROLE_ARN": "",
    "AWS_ROLE_SESSION_NAME": "",
    "CLOUDWATCH_APP_METRIC_REGION": "",
    "EMAIL_URL": "",
    "EMAIL_HOST": "",
    "EMAIL_HOST_USER": "",
    "EMAIL_HOST_PASSWORD": "",
    "EMAIL_PORT": "0",
    "EMAIL_USE_TLS": "0",
    "EMAIL_USE_SSL": "0",
}


def disable_local_review_provider_environment(
    environment: MutableMapping[str, str],
) -> None:
    """Overwrite provider discovery inputs before local settings load `.env`."""

    environment.update(LOCAL_REVIEW_PROVIDER_ENVIRONMENT)
