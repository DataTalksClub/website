from __future__ import annotations

from scripts.content import build_parser


def test_content_parser_exposes_explicit_workflows() -> None:
    assert build_parser().parse_args(["sources"]).command == "sources"
    assert build_parser().parse_args(["pull-plan"]).command == "pull-plan"
    assert build_parser().parse_args(["checkouts"]).command == "checkouts"
    assert build_parser().parse_args(["pull"]).command == "pull"
    assert build_parser().parse_args(["checkout"]).command == "checkout"
    assert build_parser().parse_args(["drift"]).command == "drift"
