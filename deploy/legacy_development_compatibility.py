"""Retained entry-point path for the deployment-target validation CLI.

The reviewed deployment targets, their physical identifiers, and every gate that
compares a deployment against them now live in :mod:`deploy.deployment_targets`.
This module is kept only because ``.github/workflows/ci.yml`` invokes
``python -m deploy.legacy_development_compatibility <role-profile>`` and reads
``DEPLOYER_ROLE_ARN`` from this path; retiring the path is a workflow change,
not a Python one.  Nothing in this repository should import it: import
:mod:`deploy.deployment_targets` instead.
"""

from __future__ import annotations

from deploy.deployment_targets import SELECTED_TARGET, main

#: The selected deployment target's exact deployer role ARN.  The workflow reads
#: this to prove a main-ref claim cannot assume the deployer role.
DEPLOYER_ROLE_ARN = SELECTED_TARGET.deployer_role_arn

__all__ = ["DEPLOYER_ROLE_ARN", "main"]


if __name__ == "__main__":
    main()
