# Development legacy-identifier boundary

`web.dtcdev.click` was the development environment; its `sandbox/website` stack
was destroyed on 2026-09-02 and the hostname no longer resolves. `sandbox` was
that environment's former repository-facing name. Issue
[#93](https://github.com/DataTalksClub/website/issues/93) changed current
application, workflow, API, test, and operator terminology to development.
Issue [#94](https://github.com/DataTalksClub/website/issues/94) owns any later
state-safe physical infrastructure and OIDC-trust rename; the replacement
`dev.datatalks.club` service is not built yet, and
`deploy/development_target.py` selects between the reviewed hostnames.

## Retired compatibility identifiers

The following values named that deployment. They stay byte-exact in the release
tooling and in the recorded evidence that verified against them, and they must
not be rewritten to fit a future development environment:

- Terraform root/state prefix `sandbox/website`, including its state key and lock;
- state bucket `datamailer-sandbox-817685572750-us-east-1-tfstate`;
- physical `website-sandbox*` AWS resources, ARNs, secret paths, log groups, and KMS alias;
- GitHub environment `sandbox` and the OIDC subject ending in `environment:sandbox`;
- physical resource-tag value `Environment=sandbox`; and
- bootstrap role/session identifiers `phone-aws-sandbox-role` and `phone-sandbox-*`.

Current Python code consumes these values through the retired
`website-sandbox` deployment target in `deploy/deployment_targets.py`. That
profile is retained rather than deleted: the Gate-B and release evidence in this
repository was verified against exactly these values, so deleting it would
orphan that history. It is marked retired, which means it can still be read but
can never be selected for a deployment —
`DTC_DEPLOYMENT_TARGET=website-sandbox` fails closed.

`deploy/legacy_development_compatibility.py` survives only as the module path
`.github/workflows/ci.yml` invokes; nothing else should import it. The workflow
reads only `DEVELOPMENT_*` GitHub variables; its four exact GitHub-environment
bindings remain literal compatibility declarations because GitHub resolves them
before any checked-out code can run. No Terraform, state, AWS resource, tag,
IAM, GitHub-environment, or OIDC-trust mutation is part of #93.

## Reviewed deployment targets

`deploy/deployment_targets.py` holds every reviewed deployment target as a named
profile: account, region, hostname, physical resource namespace, Django settings
module, `DTC_ENVIRONMENT`, canonical origin, robots policy, desired counts,
Terraform root and state bucket, and secret-ARN shapes. `DTC_DEPLOYMENT_TARGET`
only selects a member of that in-code allowlist, so a mistyped or injected value
fails at import instead of widening the boundary. Every gate in `deploy/` — the
role-profile variable validation, the release record, the task-definition
contract, the deployed smoke origin, and the Terraform SEO source check — reads
its expectations from the selected target and still fails closed, naming the
value that disagreed.

Identifiers AWS generates at apply time (KMS key ARN, hosted zone id, subnet and
security-group ids, target-group ARN) are never invented in code. A target
either pins the reviewed value it was verified against — the retired sandbox
does — or declares the strict shape a supplied value must have, scoped to that
target's own account, region and namespace. Supplying another reviewed target's
pinned identifier is rejected by name.

## Frozen evidence

The following classes retain their original bytes and wording. Readers may
label them legacy evidence but must not rewrite or regenerate them:

- captured inventories in `_docs/compatibility/*.jsonl`;
- `deploy/gate_b_binding_seed.json`, `deploy/gate_b_execution_contract.json`,
  `deploy/gate_b_manifest.json`, and their hash-bound evidence reader/tests;
- prior Actions runs, artifacts, release records, task-definition ARNs, SHAs,
  issue evidence, and Git history; and
- the Gate-B assembler/operator and OIDC probe adapters that verify those exact
  historical schemas and source hashes.

Until 2026-09-02 these files were digest-pinned twice: by the `frozen_hashes`
map in `core/tests/test_deployment_workflow.py` and by the whole-file digests in
the retired development-terminology allowlist. With the allowlist gone,
`frozen_hashes` alone still pins `deploy/gate_b_evidence.py`,
`deploy/gate_b_manifest.json`, `core/tests/test_gate_b_evidence.py`,
`deploy/oidc_probe.py`, `deploy/oidc_claim_probe.py`,
and `core/tests/test_deployment_oidc_probe.py`. It does not pin
`deploy/gate_b_binding_seed.json`, `deploy/gate_b_execution_contract.json`,
`deploy/gate_b_assembler.py`, `deploy/gate_b_operator.py`,
or `core/tests/test_gate_b_operator.py`, which now carry only
this instruction. Retiring the Gate-B evidence set — each file together with its
`frozen_hashes` entry — is its own reviewed change, not an incidental edit.

The old specification and runbook paths remain only as small link notices.
Canonical operator material is in
[`08-aws-development-terraform.md`](../specs/08-aws-development-terraform.md)
and [`development-release.md`](../runbooks/development-release.md).
