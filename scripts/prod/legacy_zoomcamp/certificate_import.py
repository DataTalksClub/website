"""Import one cohort's historical certificates.

Reads plaintext ``email,name`` graduate exports and, where available, the
hosted certificate PDF URL from the current-format ``graduates.json``.  The
email picks the same real-email-backed account ``identity.py`` uses for
scoring; the stored certificate name is always a freshly generated
placeholder, never the real one.

Matching is deliberately conservative (audit REL-17): a display name is the
only field the two artifacts share, and two different graduates can share one.
A certificate URL is attached only when the normalized name is unique on both
sides -- one graduate and one certificate.  An ambiguous name matches nothing:
the enrollment's existing certificate stays untouched and the case is counted
in the result for a reviewed explicit mapping, never guessed.  The result
carries aggregate counts and bounded codes only -- never a name or address.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass

from courses.models import Cohort

from .editions import EditionSource
from .identity import (
    anonymous_display_name,
    get_or_create_enrollment,
    get_or_create_learner,
    sha1_hex,
)


@dataclass(frozen=True, slots=True)
class CertificateImportResult:
    graduates_seen: int
    certificate_urls_matched: int
    #: Graduates whose normalized name existed in the certificate sources but
    #: was ambiguous (shared with another graduate, or carrying several
    #: certificates), so nothing was attached.  A reviewed explicit mapping,
    #: not a name guess, resolves these.
    graduates_blocked_ambiguous_name: int = 0
    #: Certificate-source names that carried more than one URL; under the old
    #: last-wins dictionary these silently overwrote each other.
    source_names_with_multiple_certificates: int = 0


def _load_certificate_candidates(certificates_json: tuple) -> dict[str, list[str]]:
    """Every certificate URL per normalized name, collisions preserved."""

    by_name: dict[str, list[str]] = {}
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
                by_name.setdefault(name.lower(), []).append(url)
    return by_name


def import_edition_certificates(cohort: Cohort, edition: EditionSource) -> CertificateImportResult:
    certificate_candidates = _load_certificate_candidates(edition.certificates_json)

    # Collect each graduate once (the upstream key is sha1(email), so a
    # graduate listed twice is one person), then group by normalized display
    # name: a name two graduates share must never match a certificate at all,
    # because either of them could be the right owner (audit REL-17).
    graduates: dict[str, tuple[str, str]] = {}
    for csv_path in edition.certificate_csvs:
        with csv_path.open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                email = (row.get("email") or "").strip()
                name = (row.get("name") or "").strip()
                if not email:
                    continue
                graduates.setdefault(sha1_hex(email), (email, name))

    graduate_keys_by_name: dict[str, list[str]] = {}
    for source_key, (_email, name) in graduates.items():
        graduate_keys_by_name.setdefault(name.lower(), []).append(source_key)

    source_names_with_multiple_certificates = sum(
        1 for candidates in certificate_candidates.values() if len(candidates) > 1
    )

    graduates_seen = 0
    urls_matched = 0
    blocked_ambiguous = 0

    for source_key, (email, name) in graduates.items():
        graduates_seen += 1
        user, _ = get_or_create_learner(source_key, email)
        enrollment, _ = get_or_create_enrollment(user, cohort)

        normalized_name = name.lower()
        candidates = certificate_candidates.get(normalized_name, [])
        certificate_url = ""
        if candidates:
            unambiguous = (
                len(candidates) == 1 and len(graduate_keys_by_name.get(normalized_name, [])) == 1
            )
            if unambiguous:
                certificate_url = candidates[0]
            else:
                # Either this name is shared with another graduate or the
                # certificate sources disagree about its URL.  Attaching the
                # last-seen URL, as this used to, could hand one graduate
                # another's certificate; the existing value stays and the
                # case waits for a reviewed explicit mapping.
                blocked_ambiguous += 1

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
        graduates_blocked_ambiguous_name=blocked_ambiguous,
        source_names_with_multiple_certificates=source_names_with_multiple_certificates,
    )


__all__ = ["CertificateImportResult", "import_edition_certificates"]
