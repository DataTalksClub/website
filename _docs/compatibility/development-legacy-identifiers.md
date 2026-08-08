# Development legacy-identifier boundary

`web.dtcdev.click` is the development environment. `sandbox` was its former
repository-facing name. Issue [#93](https://github.com/DataTalksClub/website/issues/93)
changes current application, workflow, API, test, and operator terminology to
development. Issue [#94](https://github.com/DataTalksClub/website/issues/94)
owns any later state-safe physical infrastructure and OIDC-trust rename.

## Live compatibility identifiers

The following values still identify the same live development deployment and
must remain byte-exact until #94:

- Terraform root/state prefix `sandbox/website`, including its state key and lock;
- state bucket `datamailer-sandbox-817685572750-us-east-1-tfstate`;
- physical `website-sandbox*` AWS resources, ARNs, secret paths, log groups, and KMS alias;
- GitHub environment `sandbox` and the OIDC subject ending in `environment:sandbox`;
- physical resource-tag value `Environment=sandbox`; and
- bootstrap role/session identifiers `phone-aws-sandbox-role` and `phone-sandbox-*`.

Current Python code consumes these values through
`deploy/legacy_development_compatibility.py`. The workflow reads only
`DEVELOPMENT_*` GitHub variables; its four exact GitHub-environment bindings
remain literal compatibility declarations because GitHub resolves them before
any checked-out code can run. No Terraform, state, AWS resource, tag, IAM,
GitHub-environment, or OIDC-trust mutation is part of #93.

## Frozen evidence

The following classes retain their original bytes and wording. Readers may
label them legacy evidence but must not rewrite or regenerate them:

- timestamped reports in `_docs/audits/`;
- captured inventories in `_docs/compatibility/*.jsonl`;
- `deploy/gate_b_binding_seed.json`, `deploy/gate_b_execution_contract.json`,
  `deploy/gate_b_manifest.json`, and their hash-bound evidence reader/tests;
- prior Actions runs, artifacts, release records, task-definition ARNs, SHAs,
  issue evidence, and Git history; and
- the Gate-B assembler/operator and OIDC probe adapters that verify those exact
  historical schemas and source hashes.

The old specification and runbook paths remain only as small link notices.
Canonical operator material is in
[`08-aws-development-terraform.md`](../specs/08-aws-development-terraform.md)
and [`development-release.md`](../runbooks/development-release.md).
