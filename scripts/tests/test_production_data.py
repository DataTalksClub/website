from __future__ import annotations

from scripts.production_data import DEFAULT_DATASET_ROOT, build_parser


def test_production_data_parser_keeps_seed_and_rehearsal_commands_separate() -> None:
    assert DEFAULT_DATASET_ROOT == ".tmp/production-prep-dataset"
    assert build_parser().parse_args(["dataset"]).command == "dataset"
    assert build_parser().parse_args(["bootstrap"]).command == "bootstrap"
    assert (
        build_parser()
        .parse_args(
            [
                "local",
                "--database",
                "db",
                "--course-checkout-root",
                "src",
                "--fresh",
                "--",
                "--extra-option",
            ]
        )
        .command
        == "local"
    )
    assert build_parser().parse_args(["verify", "--database", "db"]).command == "verify"
