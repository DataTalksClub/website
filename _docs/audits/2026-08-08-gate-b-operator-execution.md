# Gate B operator execution audit

Date: 2026-08-08

## Decision

Issue #84 remains the authoritative pure-offline policy and readback validator for the #81 Gate B
evidence. It is necessary but cannot be used as an operator procedure by itself. Gate B must use
the separately reviewed binding seed, execution contract, bounded operator, and offline assembler
introduced by #85. Those components make the acquisition-to-summary chain deterministic without
changing the #84 manifest, validator, workflow, or OIDC probes.

This audit authorizes no provider call. A later PM decision must bind the merged source, green
runs, repository configuration, contract hashes, and operator pre-contract before one Gate B
capture may start.

## Why the previous procedure was not executable

The #84 runbook listed the accepted AWS and GitHub command forms, then required an operator to
select fields, interpret errors, create envelopes, and expand 90 IAM simulator rows manually. The
validator could reject a malformed filtered claim, but it did not consume the raw command status,
stdout, or stderr that supposedly produced that claim. Four consequences followed:

1. no production code proved that a filtered bundle was derived from the complete capture;
2. no production renderer proved that all 90 atomic simulator requests were executed exactly;
3. `locked=false` did not prove the exact S3 `HeadObject` `404` result; and
4. the AWS Gate operator identity could not be known before the credential provider created it.

Manual expansion or an ad-hoc parser would have contradicted the same no-ad-hoc rule intended to
make the evidence conclusive. Gate B therefore remained held after #84 closed.

## Sealed AWS Gate identity

The installed AWS Gate credential provider assumes exact role
`arn:aws:iam::817685572750:role/phone-aws-sandbox-role` for 900 seconds. Its session name is created
from the vendor request and matches `phone-sandbox-[0-9a-f]{8}`. The credential response contains
temporary credentials and expiration, but not the resulting STS ARN or user ID. Running the
credential provider independently for every AWS CLI process could therefore create different
operator sessions.

The #85 operator resolves the accepted credential process exactly once and keeps its result only
in memory. With those frozen credentials, its first AWS call is `sts:GetCallerIdentity`. Before
any resource or GitHub read, it requires:

- account `817685572750`;
- ARN `arn:aws:sts::817685572750:assumed-role/phone-aws-sandbox-role/<session>`;
- user ID `AROA34YO3VSHI2OCVBKTW:<same-session>`; and
- the exact accepted session grammar.

The response must have 840 through 900 seconds of remaining lifetime at acquisition. The private
AWS Gate environment file is never read into Python, hashed, logged, or persisted: a reviewed
mode/owner/link check opens it without following the final symlink, and that exact descriptor is
held through the sole credential subprocess.

The accepted provider serializes an aware UTC expiration with Python `datetime.isoformat()`, so
its canonical response ends in `+00:00`. The execution contract therefore code-pins exactly the
second-precision UTC encodings `YYYY-MM-DDTHH:MM:SS+00:00` and
`YYYY-MM-DDTHH:MM:SSZ`. The operator applies that explicit grammar before calendar parsing,
normalizes either form to the same aware UTC instant, and rejects every other offset, precision,
separator, whitespace, trailing, partial, or malformed representation. This compatibility rule
does not change the inclusive 840-to-900-second lifetime, 120-second reserve, single resolution,
or no-persistence boundary.

It seals that returned triple into the existing #84 bindings envelope, creates the file
exclusively at mode `0600`, and runs the unchanged binding validator. Every later AWS child gets
the same in-memory credentials. GitHub children receive no AWS credential variables. A second
credential resolution, refresh, session mismatch, expiration, identity failure, or binding
failure is `STOP`; no other provider call is permitted.

The full dynamic session is intentionally not guessed or wildcarded before credential vending.
PM instead pre-accepts the exact account, parent role ARN, role unique ID, session grammar, AWS
Gate source, and forbidden application roles. The first STS result converts that bounded parent
contract into one exact capture identity.

## Frozen binding and execution graph

`deploy/gate_b_binding_seed.json` stores only accepted non-secret bootstrap/output facts:
CloudFront, target group, task-definition revisions, six secret ARNs, network identifiers, six DNS
records, the exact seven/eighteen GitHub variable maps, branch policy, source hashes, and the
operator parent contract. It contains no credential, secret value, origin header, state body,
creation timestamp, live-discovery instruction, or unresolved placeholder.

The six normalized full Route 53 records have canonical SHA-256
`c224be2350342c319c09d07ec1672fd867b48d30c7a8b7587b78758b5e2ebda8`. The separate
`4cadb0505d61e04a7e652b7f2c2e303bfa573407a65dffc30d9fbf6d2708b0e7` digest and 1,242-byte
length are retained only as provenance for the earlier full-record capture; the normalized digest
does not claim to recompute or replace that source artifact.

`deploy/gate_b_execution_contract.json` binds that seed to the unchanged #84 manifest, validator,
and simulator matrix. It owns exact structured argv specifications, phases, mapper identifiers,
expected results, file stems, limits, and hashes. The complete graph contains 174 evidence
operations:

- 58 AWS readbacks, including the first STS identity call;
- 26 exact-key GitHub reads; and
- 90 one-principal, one-action, one-resource IAM simulations.

The operator accepts no arbitrary executable, command, action, resource, policy input, caller
override, environment override, retry, or resume. It uses `shell=False`, fixed argument arrays,
bounded output and time, a fixed repository working directory, and a sanitized environment. The
AWS CLI graph is the reviewed acquisition surface; the operator does not call boto3 or botocore.
The credential Python, credential script, AWS virtual-environment interpreter, AWS entry point,
and GitHub binary are opened and verified before credential vending, then executed from those held
inodes. The AWS interpreter keeps the accepted virtual-environment path as `argv[0]`, preserving
its reviewed module context while the kernel executes the held interpreter descriptor. Only exact
graph argv arrays are authorized. The credential argv can run once, only STS can run before the
binding validator seals the identity, and each remaining graph argv can run once with its exact
working directory, timeout, and environment. Interrupt, timeout, selector failure, first phase
failure, or oversized output kills and reaps every active child process group before the worker
pool is joined.

Exact-key requests and fixed inventories do not discover or select a binding. They prove that the
already accepted seed still names the returned resource. A mismatch stops the capture and never
refreshes the seed from live AWS.

## Raw capture and deterministic assembly

Every graph node has one unique stdout, stderr, and status file below a single mode-`0700`
repository `.tmp/` capture directory. Files are created exclusively, without symlink traversal,
at mode `0600`. Status records bind the capture ID, command ID, argv hash, graph hash, time bounds,
exit code, and exact stdout/stderr hashes without recording an environment value. The online
operator parses once for immediate phase gating; the offline assembler independently re-parses
the exact base64-wrapped bytes and is the final assembly authority. Status time is bound to the
timestamp in the capture ID, the 900-second capture window, the 31-second command envelope, and
the identity/readback/simulator phase order. The exact raw inventory must be complete; missing,
extra, duplicate, mixed, stale, oversized,
wrong-mode, or symlinked evidence is `STOP`.
The capture clock is checked immediately before and immediately after credential vending, so a
stale post-credential clock stops before the STS identity command.

Provider projections are frozen per command. The CloudFront and Secrets Manager CLI requests
filter sensitive or value-bearing fields before persistence. ECR image details, ECS task ARNs, and
target-health entries are reduced by literal CLI projections to bounded count plus input-type and
pagination proofs; the assembler requires an array source, nonnegative integer count, and no
continuation token. Each other known field is either consumed or explicitly discarded by its
fixed selector; an unknown field or ambiguous cardinality stops. The assembler writes only
exclusive private outputs beneath the validated capture and has no network, subprocess, boto,
Terraform, or GitHub API capability.
Route 53 accepts only the exact record array plus an optional nonempty AWS CLI `NextToken`; native
service pagination fields, malformed tokens, and a record differing from the sealed full record stop.
Before online or standalone `PASS`, the operator re-reads `bindings.json` and
`bindings.result.json` and requires the sealed files to parse exactly as the recomputed documents.

Only five nonzero results are accepted, each bound to its exact service, operation, and target:

| Evidence | Exact accepted code |
| --- | --- |
| S3 bucket policy | `NoSuchBucketPolicy` |
| S3 state lock `HeadObject` | `404` |
| ECR all-zero digest | `ImageNotFoundException` |
| ECR repository policy | `RepositoryPolicyNotFoundException` |
| ECR registry-v2 policy | `RegistryPolicyNotFoundException` |

For the lock, `403`, `NoSuchKey`, generic `NotFound`, success, transport failure, or any other code
is `STOP`. Each Secrets Manager resource-policy request must instead succeed for the exact secret
and omit `ResourcePolicy`; an error, null/empty policy member, or policy body stops.

The assembler deterministically creates the unchanged bindings, policies, resources, and
simulator envelope shapes, invokes the unchanged #84 validators in order, and creates the final
summary. A safe execution attestation binds the seed, graph, raw inventory, envelopes, results,
summary, exact counts, and sealed parent role. Only the final filtered summary, attestation, and
hashes may reach GitHub. Raw JSON, provider messages, policy bodies, credentials, tokens, secret
values, state content, custom headers, and sensitive paths remain private and must never be posted.

## Gate boundary

Any failure consumes the single-use Gate B authorization and stops without retry or Gate C. A
Gate B `PASS` is only evidence for a new independent PM review. It does not authorize OIDC, a
workflow dispatch, secrets, image publication, tasks, services, release, deployment, Terraform,
or #70 continuation. `SANDBOX_AUTO_DEPLOY=false` remains mandatory throughout.
