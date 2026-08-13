"""Write a deterministic, redacted artifact for the security evidence gate.

The artifact is deliberately generated from values that never leave this
process.  The scanner then proves both that the artifact exists and that the
redaction policy removed every canary before publication.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from core.redaction import redact

SECURITY_CANARIES = (
    "synthetic-secret-canary",
    "synthetic-email@example.invalid",
    "synthetic-token-canary",
)


def build_canary_artifact() -> dict[str, object]:
    # Keep each publication surface explicit so the gate cannot silently
    # regress to proving only one generic dictionary path.
    payload = {
        "browser": {"rendered_text": SECURITY_CANARIES[0]},
        "log": {"message": SECURITY_CANARIES[1]},
        "metric": {"label": SECURITY_CANARIES[2]},
        "trace": {"attribute": SECURITY_CANARIES[0]},
        "audit": {"metadata": {"email": SECURITY_CANARIES[1]}},
        "webhook": {"body": SECURITY_CANARIES[2]},
        "csv": {"cell": SECURITY_CANARIES[0]},
    }
    redacted = redact(payload, canaries=SECURITY_CANARIES)
    serialized = json.dumps(redacted, sort_keys=True)
    if any(canary in serialized for canary in SECURITY_CANARIES):
        raise RuntimeError("redaction canary escaped into the evidence artifact")
    return {
        "surfaces": redacted,
        "canary_count": len(SECURITY_CANARIES),
        "schema_version": 1,
        "status": "pass",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = build_canary_artifact()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
