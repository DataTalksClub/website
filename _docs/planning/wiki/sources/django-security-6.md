# Security in Django 6.0

Locator: https://docs.djangoproject.com/en/6.0/topics/security/

Accessed: 2026-08-07

## Summary

Official Django security guidance provides the baseline for the public forms, Studio sessions, and deployment settings.

## Claims

- [FACT django-security-6] Django provides CSRF defenses when middleware and template tokens are used correctly.
- [FACT django-security-6] Production still requires HTTPS, secure cookie settings, host validation, correct proxy configuration, and safe handling of user-provided content.

## Limitations

- [INFERENCE django-security-6] Framework defaults do not replace application-level authorization, rate limits, auditing, output sanitization, or secret management.
