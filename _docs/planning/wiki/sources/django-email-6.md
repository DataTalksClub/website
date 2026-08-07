# Sending email with Django 6.0

Locator: https://docs.djangoproject.com/en/6.0/topics/email/

Accessed: 2026-08-07

## Summary

Official Django email documentation defines the framework abstraction used below the application's durable outbox.

## Claims

- [FACT django-email-6] Django supports pluggable email backends and multipart messages with plain-text and HTML alternatives.
- [FACT django-email-6] The console and in-memory backends support development and automated testing without sending real email.
- [INFERENCE django-email-6,aisl-reference] Django's email abstraction does not itself provide durable asynchronous delivery, idempotency, retries, or provider event tracking; those remain application services.

## Limitations

- [FACT django-email-6] Provider-specific authentication, bounce/complaint handling, quotas, and webhook behavior are outside this source.
