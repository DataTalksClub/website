from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import boto3  # type: ignore[import-untyped]
import requests
from botocore import UNSIGNED  # type: ignore[import-untyped]
from botocore.config import Config  # type: ignore[import-untyped]
from botocore.exceptions import ClientError  # type: ignore[import-untyped]

from deploy.contracts import ReleaseContractError

DENIAL_CODES = {"AccessDenied", "AccessDeniedException"}
WRONG_AUDIENCE_DENIAL_CODES = {"InvalidIdentityToken"}
AUDIENCES = {"sts.amazonaws.com", "dtc.invalid.example"}
ROLE_PATTERN = re.compile(
    r"^arn:aws:iam::817685572750:role/website-sandbox-github-(?:publisher|deployer)$"
)


def fetch_github_oidc_token(audience: str) -> str:
    request_url = os.environ.get("ACTIONS_ID_TOKEN_REQUEST_URL", "")
    request_token = os.environ.get("ACTIONS_ID_TOKEN_REQUEST_TOKEN", "")
    if not request_url.startswith("https://") or not request_token:
        raise ReleaseContractError("GitHub OIDC request context is unavailable")
    try:
        response = requests.get(
            request_url,
            headers={"Authorization": f"bearer {request_token}"},
            params={"audience": audience},
            timeout=15,
        )
        response.raise_for_status()
        token = response.json().get("value")
    except Exception as error:
        raise ReleaseContractError(
            f"GitHub OIDC token request failed ({type(error).__name__})"
        ) from error
    if not isinstance(token, str) or token.count(".") != 2 or len(token) < 100:
        raise ReleaseContractError("GitHub OIDC token response is invalid")
    return token


def prove_wrong_claim_denied(
    *,
    role_arn: str,
    audience: str,
    claim_label: str,
    probe_id: str,
    token_fetcher: Callable[[str], str] = fetch_github_oidc_token,
    sts: Any | None = None,
) -> None:
    if not ROLE_PATTERN.fullmatch(role_arn):
        raise ReleaseContractError("wrong-claim probe role ARN is not exact")
    if audience not in AUDIENCES:
        raise ReleaseContractError("wrong-claim probe audience is not reviewed")
    if claim_label not in {
        "environment-subject-to-main-role",
        "main-subject-to-environment-role",
        "wrong-audience-to-main-role",
    }:
        raise ReleaseContractError("wrong-claim probe label is not reviewed")
    if not re.fullmatch(r"[0-9]{1,12}", probe_id):
        raise ReleaseContractError("wrong-claim probe ID is invalid")

    token = token_fetcher(audience)
    client = sts or boto3.client(
        "sts",
        region_name="eu-west-1",
        config=Config(signature_version=UNSIGNED),
    )
    try:
        unexpected = client.assume_role_with_web_identity(
            RoleArn=role_arn,
            RoleSessionName=f"website-denied-probe-{probe_id}",
            WebIdentityToken=token,
            DurationSeconds=900,
        )
    except ClientError as error:
        code = str(error.response.get("Error", {}).get("Code", ""))
        denied = code in DENIAL_CODES or (
            audience != "sts.amazonaws.com" and code in WRONG_AUDIENCE_DENIAL_CODES
        )
        if not denied:
            raise ReleaseContractError(
                f"wrong-claim denial was not proven ({code or 'unknown'})"
            ) from error
    except Exception as error:
        raise ReleaseContractError(
            f"wrong-claim denial probe failed ({type(error).__name__})"
        ) from error
    else:
        if isinstance(unexpected, dict):
            unexpected.clear()
        raise ReleaseContractError("wrong OIDC claim unexpectedly assumed the application role")
    finally:
        token = ""

    print(
        json.dumps(
            {
                "audience": audience,
                "claim": claim_label,
                "result": "denied",
                "role_arn": role_arn,
                "timestamp": datetime.now(UTC).isoformat(),
            },
            sort_keys=True,
        )
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Prove a reviewed wrong GitHub OIDC claim")
    parser.add_argument("--role-arn", required=True)
    parser.add_argument("--audience", choices=sorted(AUDIENCES), required=True)
    parser.add_argument("--claim-label", required=True)
    parser.add_argument("--probe-id", required=True)
    return parser


def main() -> None:
    arguments = build_parser().parse_args()
    try:
        prove_wrong_claim_denied(
            role_arn=arguments.role_arn,
            audience=arguments.audience,
            claim_label=arguments.claim_label,
            probe_id=arguments.probe_id,
        )
    except ReleaseContractError as error:
        print(f"OIDC claim probe failed safely: {error}", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
