# Issue #141 traceability

| Requirement | Product/architecture authority | Implementation/evidence |
| --- | --- | --- |
| Browser headers, secure cookies, CSRF/CORS, safe errors, private/no-store | Specs 01, 06, 07 | `core.middleware.ResponsePolicyMiddleware`, `website.settings.*`, `core.tests.test_non_identity_security`, response-policy tests |
| Request/body/JSON limits and safe method handling | Specs 06, 07, 10 | `core.middleware.RequestBoundaryMiddleware` (seekable ASGI size/mismatch check, bounded stream replay, fail-closed WSGI chunked/unknown lengths), `core.security.validate_json_shape`, API/webhook negative tests |
| XSS, unsafe URL/content, traversal/symlink, SSRF | Specs 03, 07, 10 | `content.services` sanitizer, content/repository/importer guards, `core.security` URL/path validators and tests |
| CSV/formula injection and mass-assignment denial | Specs 05, 06, 07 | `core.security.neutralize_csv_formula`, owning API/Studio field registries and #86/#87 decision-free fixtures |
| Webhook forgery/replay and bounded errors | Specs 01, 03, 07, 10 | `api.views.datamailer_webhook_validation`, idempotent event model, `data.tests.test_datamailer_webhook*` |
| Redacted logs/metrics/traces/audits/browser artifacts | Specs 06, 07, 10 | `core.redaction`, `core.audit`, `course_management.observability`, redaction canary tests, `make security-artifact-scan` |
| Dependency/container/security checks | Specs 01, 07, 10 | frozen `uv.lock`, non-root `Dockerfile`, `pip-audit` lock-digest artifact, cryptography advisory remediation, `make security-check`, `make verification-container` |
| Threat/control ownership and identity split | Issues #63/#31/#86/#87; identity #20/#61; high-risk #28/#32/#33 | [`non-identity-threat-control-matrix.md`](non-identity-threat-control-matrix.md); unresolved identity/high-risk rows are explicit hand-offs |

This document does not accept OIDC, MFA, break-glass, reauthentication, or
high-risk action semantics.  Those remain owned by the identity/high-risk
issues and must be verified separately.
