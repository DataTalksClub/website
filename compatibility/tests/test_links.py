from __future__ import annotations

from compatibility.links import ReferenceSeverity, validate_reference_graph
from compatibility.models import PageMetadata, Reference, ReferenceKind
from compatibility.target import TargetObservation


def observation(
    url: str,
    *,
    status: int = 200,
    metadata: PageMetadata | None = None,
) -> TargetObservation:
    return TargetObservation(
        requested_url=url,
        raw_network_reference=url.removeprefix("https://datatalks.club"),
        status=status,
        final_url=url,
        response_count=1,
        transfer_bytes=0,
        metadata=metadata or PageMetadata(),
    )


def test_resolves_internal_asset_form_and_literal_percent_encoded_fragment() -> None:
    source = observation(
        "https://datatalks.club/source",
        metadata=PageMetadata(
            references=(
                Reference(
                    ReferenceKind.ASSET,
                    "https://datatalks.club/assets/logo.png",
                ),
                Reference(
                    ReferenceKind.FORM_ACTION,
                    "https://datatalks.club/submit?next=%2Fok",
                ),
                Reference(
                    ReferenceKind.INTERNAL_LINK,
                    "https://datatalks.club/docs/Exact/#Caf%C3%A9",
                ),
            )
        ),
    )
    target = observation(
        "https://datatalks.club/docs/Exact/",
        metadata=PageMetadata(fragments=("Café",)),
    )
    asset = observation("https://datatalks.club/assets/logo.png")
    form = observation("https://datatalks.club/submit?next=%2Fok", status=405)

    assert (
        validate_reference_graph(
            (source, target, asset, form),
            internal_hosts=frozenset({"datatalks.club"}),
        )
        == ()
    )


def test_reports_bad_targets_redirects_assets_and_case_sensitive_fragments() -> None:
    source = observation(
        "https://datatalks.club/source",
        metadata=PageMetadata(
            references=(
                Reference(
                    ReferenceKind.ASSET,
                    "https://datatalks.club/assets/missing.png",
                ),
                Reference(
                    ReferenceKind.INTERNAL_LINK,
                    "https://datatalks.club/missing#Exact",
                ),
                Reference(
                    ReferenceKind.INTERNAL_LINK,
                    "https://datatalks.club/target#exact",
                ),
            )
        ),
    )
    target = observation(
        "https://datatalks.club/target",
        metadata=PageMetadata(fragments=("Exact",)),
    )

    problems = validate_reference_graph(
        (source, target),
        internal_hosts=frozenset({"datatalks.club"}),
    )

    assert {problem.code for problem in problems} == {
        "asset_missing",
        "fragment_missing",
        "internal_target_missing",
    }


def test_external_urls_are_never_fetched_and_optional_policy_stays_visible() -> None:
    exact = "https://external.example/resource?utm_source=A+B&x=%20&x="
    source = observation(
        "https://datatalks.club/source",
        metadata=PageMetadata(references=(Reference(ReferenceKind.EXTERNAL_LINK, exact),)),
    )

    problems = validate_reference_graph(
        (source,),
        internal_hosts=frozenset({"datatalks.club"}),
        optional_external_urls=frozenset({exact}),
    )

    assert len(problems) == 1
    assert problems[0].code == "optional_external_not_fetched"
    assert problems[0].severity is ReferenceSeverity.WARNING
    assert problems[0].target_url == exact
