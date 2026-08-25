"""Import one cohort's historical certificates.

Reads plaintext ``email,name`` graduate exports and, where available, the
hosted certificate PDF URL from the current-format ``graduates.json``
(matched back to a graduate by name -- the only field both files share). The
email picks the same real-email-backed account ``identity.py`` uses for
scoring; the stored certificate name is always a freshly generated
placeholder, never the real one.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass

from courses.models import Cohort

from .editions import EditionSource
from .identity import anonymous_display_name, get_or_create_enrollment, get_or_create_learner, sha1_hex


@dataclass(frozen=True, slots=True)
class CertificateImportResult:
    graduates_seen: int
    certificate_urls_matched: int


def _load_certificate_urls_by_name(certificates_json: tuple) -> dict[str, str]:
    by_name: dict[str, str] = {}
    for path in certificates_json:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for entry in raw:
            variables = entry.get("variables", {})
            name = variables.get("text", {}).get("name", "").strip()
            url = variables.get("links", {}).get("certificate-id", "").strip()
            if name and url:
                by_name[name.lower()] = url
    return by_name


def import_edition_certificates(cohort: Cohort, edition: EditionSource) -> CertificateImportResult:
    certificate_urls_by_name = _load_certificate_urls_by_name(edition.certificates_json)

    seen_keys: set[str] = set()
    graduates_seen = 0
    urls_matched = 0

    for csv_path in edition.certificate_csvs:
        with csv_path.open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                email = (row.get("email") or "").strip()
                name = (row.get("name") or "").strip()
                if not email:
                    continue

                source_key = sha1_hex(email)
                if source_key in seen_keys:
                    continue
                seen_keys.add(source_key)
                graduates_seen += 1

                user, _ = get_or_create_learner(source_key, email)
                enrollment, _ = get_or_create_enrollment(user, cohort)

                certificate_url = certificate_urls_by_name.get(name.lower(), "")
                if certificate_url:
                    urls_matched += 1

                update_fields = []
                if not enrollment.certificate_name:
                    enrollment.certificate_name = anonymous_display_name()
                    update_fields.append("certificate_name")
                if certificate_url and enrollment.certificate_url != certificate_url:
                    enrollment.certificate_url = certificate_url
                    update_fields.append("certificate_url")
                if update_fields:
                    enrollment.save(update_fields=update_fields)

    return CertificateImportResult(
        graduates_seen=graduates_seen,
        certificate_urls_matched=urls_matched,
    )


__all__ = ["CertificateImportResult", "import_edition_certificates"]
