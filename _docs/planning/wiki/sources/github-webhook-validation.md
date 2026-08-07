# Validating GitHub webhook deliveries

Locator: https://docs.github.com/en/webhooks/using-webhooks/validating-webhook-deliveries

Accessed: 2026-08-07

## Summary

Official GitHub guidance defines webhook authenticity requirements for content refresh triggers.

## Claims

- [FACT github-webhook-validation] GitHub webhook payloads must be verified against `X-Hub-Signature-256` with HMAC-SHA256 before processing.
- [FACT github-webhook-validation] The webhook secret must be high entropy, kept outside source control, and compared using a constant-time operation.
- [INFERENCE github-webhook-validation] Delivery IDs should be stored with a uniqueness constraint so retries cannot enqueue duplicate sync work.

## Limitations

- [INFERENCE github-webhook-validation] Authorization for fetching private repositories and the content sync transaction model are separate concerns.
