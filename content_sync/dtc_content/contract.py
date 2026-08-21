from __future__ import annotations

import re
from dataclasses import dataclass

from content.models import ContentSource
from content.ownership import DTC_CONTENT_SOURCE_ID
from content.services import CreateContentSource

ACCEPTED_CONTENT_COMMIT = "e29f56ce70bd997171a78a9f0facc9354797f421"
ACCEPTED_CONTENT_TREE = "c82b0c6ff462dcdd7140f03f2e7d884ed10ff8fa"
ORIGINAL_MIGRATION_COMMIT = "373bef2912342ece1d2a2d2a9395aa3417243283"
LEGACY_SOURCE_COMMIT = "ee43d3fa0929faf691178d79f19528e6f15a83e5"
MIGRATION_SHA256 = "dd78a343a5f387a74afa914fc6c7e19790e202aa5d6fa9aba08bfda5995c5f86"
REPAIRED_BASELINE_COMMIT = "b9a40ba974fdef67ee3a2a70f114734f2581033c"
REPAIRED_BASELINE_TREE = "701fa3f7aa35973e65736a188161c480982f1cb3"
REPAIRED_BASELINE_CI_RUN = "https://github.com/DataTalksClub/content/actions/runs/31307270515"
REPAIR_MANIFEST_PATH = "repairs/2026-08-09-missing-media.yaml"
REPAIR_MANIFEST_SHA256 = "80d3014c47bf57de792473fc1da8f7569daeb55107688c3485153f773948d3aa"
REPLACEMENT_ATTESTATION_SHA256 = "0ec3dc878ae63e8b95967bd8f6427196c8797747f52cf5c1dafc5e3da4b831a0"
REPAIR_COMPLETION_REFERENCE = (
    "https://github.com/DataTalksClub/content/issues/2#issuecomment-5230937281"
)
EDITORIAL_OVERLAY_PATH = "editorial-overlays/2026-08-10-podcast-descriptions.yaml"
EDITORIAL_OVERLAY_SHA256 = "63969508134e8b2ef3c8471e9c8dbccc96842fcfc25225fe02e1ed5a4f5926f6"
EDITORIAL_OVERLAY_ISSUE = "https://github.com/DataTalksClub/content/issues/3"
EDITORIAL_OVERLAY_CREATED = "2026-08-10"
SOURCE_CI_RUN = "https://github.com/DataTalksClub/content/actions/runs/31365358459"
# The checked projection's own digests.  They move whenever the build writes a
# different artifact, and each move is a reviewed event: this pair was last
# regenerated when the article bodies stopped being flattened to plain text and
# started carrying their illustrations, tables, code samples and links.
PROJECTION_MANIFEST_SHA256 = "55aac4182f73d53df9a3071c6e4f989888f4aae3a1228f9c4187916f4831faf0"
PROJECTION_TREE_SHA256 = "56e3d79cadf5994c7eddf614d75ab2cdca3fea07771cb3930f674e6ddcf8a9c0"
PROJECTION_PODCASTS_SHA256 = "723cbac24b2b175390227984fdade52612b86606b85bb94ed3aba29439431d54"
ACCEPTED_BUNDLE_SHA256 = "d314b64a2bd94c24cf06afb55a1bae5557a31f1e7cc605ed44fa3379ffff0d17"
ACCEPTED_ADOPTED_SOURCE_SET_SHA256 = (
    "e42cd488f9431dd2770df505d58b14c54543c4134e2f560c325d37dbd94c9d71"
)
ACCEPTED_COMPARISON_SHA256 = "04cc29484e82f1262bf5acbc8c2e815e5d9675ef799893307b26e04cf90b4886"
PARSER_VERSION = "dtc-content-v4"
RENDERING_VERSION = "dtc-content-html-v4"
SCHEMA_VERSION = 3

EDITORIAL_OVERLAY_TARGETS = (
    "podcasts/_s12e08.yaml",
    "podcasts/data-team-roles.yaml",
    "podcasts/machine-learning-data-science-interview-prep.yaml",
    "podcasts/s22e06-from-black-box-systems-to-augmented-decision-making.yaml",
    "podcasts/s22e07-reinventing-career-in-tech.yaml",
    "podcasts/s22e08-building-pet-health-tech-ml-sensors-and-dog-behavior-data.yaml",
    "podcasts/s23e01-ai-engineering-skill-stack-agents-llmops-and-how-to-ship-ai-products.yaml",
    "podcasts/s23e02-foundations-of-analytics-engineer-role-skills-scope-and-modern-practices.yaml",
    "podcasts/s23e03-future-of-ai-agents.yaml",
    "podcasts/s23e04-how-to-become-ai-engineer-after-career-break.yaml",
    "podcasts/s23e05-inside-ai-engineer-role-tools-skills-and-career-path.yaml",
    (
        "podcasts/s23e06-data-engineer-career-in-2026-roles-specializations-and-"
        "what-companies-look-for.yaml"
    ),
    "podcasts/s23e07-understanding-ai-engineer-role.yaml",
    "podcasts/s23e09-starting-data-conference-data-makers-fest-story.yaml",
    "podcasts/s24e01-competitions-beyond-kaggle-leaderboard.yaml",
    "podcasts/s24e03-from-notebook-to-production-building-end-to-end-ai-systems.yaml",
    "podcasts/s24e04-from-genai-pilots-to-production.yaml",
    "podcasts/s24e05-ai-adoption-in-enterprise-beyond-writing-code.yaml",
    "podcasts/s24e06-how-to-build-ai-that-actually-ships-in-production.yaml",
)

ACCEPTED_COUNTS = {
    "articles": 55,
    "podcasts": 205,
    "podcast_transcripts": 203,
    "books": 98,
    "media": 815,
}


@dataclass(frozen=True, slots=True)
class DtcContentAdapterContract:
    stable_id: str = DTC_CONTENT_SOURCE_ID
    display_name: str = "DataTalks.Club editorial content"
    repository_owner: str = "DataTalksClub"
    repository_name: str = "content"
    branch: str = "main"
    mount_path: str = "/"
    adapter_type: str = PARSER_VERSION
    schema_version: int = SCHEMA_VERSION
    parser_version: str = PARSER_VERSION
    rendering_version: str = RENDERING_VERSION
    path_allowlist: tuple[str, ...] = (
        "articles/*.md",
        "podcasts/*.yaml",
        "podcasts/transcripts/*.yaml",
        "books/*.yaml",
        "images/posts/**",
        "images/podcast/**",
        "images/books/**",
        "migration.yaml",
        REPAIR_MANIFEST_PATH,
        EDITORIAL_OVERLAY_PATH,
    )
    freshness_target_minutes: int = 14
    max_files: int = 2_000
    max_source_bytes: int = 200_000_000
    max_release_bytes: int = 400_000_000
    max_file_bytes: int = 8_000_000
    max_yaml_depth: int = 32
    max_yaml_nodes: int = 100_000
    max_diagnostics: int = 50
    max_validation_seconds: float = 60.0

    @property
    def repository_https_url(self) -> str:
        return "https://github.com/DataTalksClub/content"

    @property
    def repository_clone_url(self) -> str:
        return f"{self.repository_https_url}.git"

    def create_source_command(self, *, enabled: bool = False) -> CreateContentSource:
        return CreateContentSource(
            stable_id=self.stable_id,
            display_name=self.display_name,
            repository_owner=self.repository_owner,
            repository_name=self.repository_name,
            branch=self.branch,
            path_allowlist=self.path_allowlist,
            adapter_type=self.adapter_type,
            mount_path=self.mount_path,
            enabled=enabled,
            max_files=self.max_files,
            max_bytes=self.max_release_bytes,
            freshness_target_minutes=self.freshness_target_minutes,
        )

    def validate_source(self, source: ContentSource) -> None:
        actual = (
            source.stable_id,
            source.repository_owner,
            source.repository_name,
            source.branch,
            source.mount_path,
            source.adapter_type,
            tuple(source.path_allowlist),
            source.freshness_target_minutes,
            source.max_files,
            source.max_bytes,
        )
        expected = (
            self.stable_id,
            self.repository_owner,
            self.repository_name,
            self.branch,
            self.mount_path,
            self.adapter_type,
            self.path_allowlist,
            self.freshness_target_minutes,
            self.max_files,
            self.max_release_bytes,
        )
        if actual != expected:
            raise ValueError("content source does not match the registered dtc-content contract")

    def validate_commit(self, value: str) -> str:
        if re.fullmatch(r"[0-9a-f]{40}", value) is None:
            raise ValueError("source commit must be a full lowercase Git SHA")
        return value


DTC_CONTENT_CONTRACT = DtcContentAdapterContract()

ADAPTER_REGISTRY: dict[str, DtcContentAdapterContract] = {
    DTC_CONTENT_CONTRACT.adapter_type: DTC_CONTENT_CONTRACT,
}
