from __future__ import annotations

import argparse
import json
from pathlib import Path

from deploy.development_seo_policy import verify_trusted_terraform_source


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Read-only verification of a pinned aws-infra development SEO policy",
    )
    parser.add_argument("--repository", required=True, type=Path)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--expected-commit", required=True)
    arguments = parser.parse_args()
    evidence = verify_trusted_terraform_source(
        arguments.repository,
        revision=arguments.revision,
        expected_commit=arguments.expected_commit,
    )
    print(
        json.dumps(
            {
                "cache_behavior_count": evidence.cache_behavior_count,
                "commit": evidence.commit,
                "status": "PASS",
                "task_cpu_architecture": evidence.task_cpu_architecture,
            },
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
