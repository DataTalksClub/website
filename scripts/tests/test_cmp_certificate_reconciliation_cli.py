from scripts.prod.reconcile_cmp_enrollment_certificates import _parser


def test_certificate_reconciliation_is_dry_run_by_default() -> None:
    args = _parser().parse_args(
        [
            "--database",
            ".tmp/example.sqlite3",
            "--source",
            ".tmp/source.sqlite3",
            "--source-course",
            "ai-dev-tools-2025",
            "--target-cohort",
            "ai-dev-tools-zoomcamp-2025",
        ]
    )

    assert args.apply is False


def test_certificate_reconciliation_requires_explicit_apply() -> None:
    args = _parser().parse_args(
        [
            "--database",
            ".tmp/example.sqlite3",
            "--source",
            ".tmp/source.sqlite3",
            "--source-course",
            "ai-dev-tools-2025",
            "--target-cohort",
            "ai-dev-tools-zoomcamp-2025",
            "--apply",
        ]
    )

    assert args.apply is True
