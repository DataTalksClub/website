"""Podcast parser: episodes and transcripts from the structured content repository.

The record rules are the projection builder's preferred-mode podcast rules
(``_main_records``): the two-phase resource resolution against the complete
episode catalogue and the transcript conventions of both source layouts —
the current seasonal layout with the episode-adjacent
``{stem}-transcript.yaml`` and the pre-reorg flat layout with
``podcasts/transcripts/<slug>.yaml``.  The staged adapter and its
``verify_dtc_content`` contract remain the pinned-corpus gate.
"""

from pathlib import PurePosixPath

from community_base.content_sync.orchestration import UpsertResult
from community_base.content_sync.parsers import SourceItem, register_parser

from content.podcast_routes import podcast_canonical_path
from scripts import build_public_projection as builder

from . import base

SOURCE_SLUG = "dtc-content"
CONTENT_KIND = "podcast"
REPOSITORY = "DataTalksClub/content"
PODCASTS_ROOT = "podcasts"
_TRANSCRIPT_SUFFIX = "-transcript.yaml"


class PodcastsParser:
    def discover(self, checkout, source):
        if source.slug != SOURCE_SLUG:
            return []
        base.activate(checkout)
        episode_paths = sorted(path for path in checkout.files() if self._is_episode(path))
        # Records are built with empty resources first and resolved against the
        # complete catalogue afterwards, mirroring the builder's two-phase rule
        # for root-relative episode links.
        prepared = [self._prepare(checkout, path) for path in episode_paths]
        records = [entry[2] for entry in prepared]
        items = []
        for relative, checksum, record, raw_resources, source_name in prepared:
            record["resources"] = builder._podcast_resources(
                raw_resources,
                source_name=source_name,
                podcast_records=records,
            )
            items.append(
                SourceItem(
                    key=record["slug"],
                    path=relative,
                    data={"record": record, "checksum": checksum},
                )
            )
        items.sort(key=lambda item: item.key)
        return items

    def upsert(self, item, source, media):
        record = item.data["record"]
        image = record["image_source"]
        if image and not image.startswith(("http://", "https://")):
            media.upload(base.active_checkout(), image.lstrip("/"), source)
        document, action = base.upsert_document(
            source,
            content_kind=CONTENT_KIND,
            stable_key=record["slug"],
            slug=record["slug"],
            title=record["title"],
            summary=record["description"],
            public_path=record["public_path"],
            source_path=str(item.path),
            checksum=item.data["checksum"],
            record=record,
        )
        return UpsertResult(document, action)

    def soft_delete_missing(self, seen_keys, source):
        if source.slug != SOURCE_SLUG:
            return 0
        return base.delete_missing(source, CONTENT_KIND, seen_keys)

    @staticmethod
    def _is_episode(path: PurePosixPath) -> bool:
        # The flat layout stores every transcript under ``podcasts/transcripts/``;
        # nothing in that directory is an episode.
        return (
            path.parts[:1] == (PODCASTS_ROOT,)
            and "transcripts" not in path.parts
            and path.suffix == ".yaml"
            and not path.name.endswith(_TRANSCRIPT_SUFFIX)
            and not path.name.startswith("_")
        )

    def _prepare(self, checkout, path):
        """Parse one episode into its record with ``resources`` still empty."""

        relative = path.as_posix()
        checksum = base.checksum_of(checkout, relative)
        raw = builder._load_yaml(base.snapshot_path(checkout, relative))
        if not isinstance(raw, dict):
            base.fail("podcast record rejected", relative)
        slug = builder._safe_key(raw.get("slug"), field="podcast slug")
        legacy_path = builder._string(raw.get("legacy_path"), field="podcast path", maximum=500)
        if legacy_path != f"/podcast/{slug}.html":
            base.fail("podcast route mismatch", relative)
        transcript, transcript_provenance = self._transcript(checkout, path, raw, slug)
        raw_links = raw.get("links")
        if raw_links is not None and not isinstance(raw_links, dict):
            base.fail("podcast links rejected", relative)
        links: dict[str, str] = {}
        if raw_links:
            for label, value in sorted(raw_links.items()):
                if value == "TODO":
                    continue
                safe = builder._safe_url(value, field=f"podcast link {label}")
                if safe:
                    provider = builder._canonical_podcast_platform_key(label)
                    if provider in links:
                        base.fail("duplicate podcast platform provider", relative)
                    links[provider] = builder._canonical_podcast_platform_url(provider, safe)
        record = {
            "slug": slug,
            "public_path": podcast_canonical_path(slug),
            "title": builder._title_from_record(raw, slug),
            "short": builder._string(
                raw.get("short"), field="podcast short", maximum=500, optional=True
            ),
            "description": builder._string(
                raw.get("description") or raw.get("intro"),
                field="podcast description",
                maximum=20_000,
                optional=True,
            ),
            "season": builder._positive_integer(raw.get("season"), field="podcast season"),
            "episode": builder._positive_integer(raw.get("episode"), field="podcast episode"),
            "published": builder._string(
                raw.get("dateadded"), field="podcast date", maximum=50, optional=True
            ),
            "guests": builder._safe_key_list(raw.get("guests"), field="podcast guest"),
            "links": links,
            "resources": [],
            "video": builder._podcast_video(raw, links, source_name=path.name),
            "transcript": transcript,
            "transcript_provenance": transcript_provenance,
            "image_source": builder._string(
                raw.get("image"), field="podcast image", maximum=500, optional=True
            ),
            "provenance": builder._provenance(
                repository=REPOSITORY,
                revision=checkout.commit_sha,
                source_path=relative,
                source_key=slug,
                checksum=checksum,
            ),
        }
        return relative, checksum, record, raw.get("resources"), path.name

    def _transcript(self, checkout, path, raw, slug):
        transcript_path = raw.get("transcript")
        if not transcript_path:
            return [], None
        # The source repository's reorg moved episodes under season directories
        # and renamed each transcript alongside its episode; the pre-reorg flat
        # layout kept them under ``podcasts/transcripts/<slug>.yaml``.  Both
        # conventions are accepted here, mirroring the source repository's own
        # stated compatibility policy and the staged adapter's corpus
        # acceptance; the projection record shape is the same either way.
        if transcript_path == f"{path.stem}-transcript.yaml":
            transcript_relative = (path.parent / transcript_path).as_posix()
        elif transcript_path == f"transcripts/{slug}.yaml":
            transcript_relative = (
                PurePosixPath(PODCASTS_ROOT) / "transcripts" / f"{slug}.yaml"
            ).as_posix()
        else:
            base.fail("podcast transcript mismatch", path.as_posix())
        transcript_record = builder._load_yaml(base.snapshot_path(checkout, transcript_relative))
        if not isinstance(transcript_record, dict) or transcript_record.get("podcast") != slug:
            base.fail("podcast transcript rejected", path.as_posix())
        segments = transcript_record.get("segments")
        if not isinstance(segments, list):
            base.fail("podcast transcript rejected", path.as_posix())
        transcript: list[dict] = []
        for segment in segments:
            if (
                not isinstance(segment, dict)
                or not set(segment).issubset({"header", "line", "sec", "time", "who"})
                or bool(segment.get("header")) == bool(segment.get("line"))
            ):
                base.fail("podcast transcript segment rejected", path.as_posix())
            if segment.get("header"):
                transcript.append(
                    {
                        "header": builder._string(
                            segment["header"],
                            field="podcast transcript header",
                            maximum=2_000,
                        )
                    }
                )
                continue
            sec = segment.get("sec")
            if sec is not None and (isinstance(sec, bool) or not isinstance(sec, (int, float))):
                base.fail("podcast transcript timestamp rejected", path.as_posix())
            transcript.append(
                {
                    "line": builder._string(
                        segment["line"],
                        field="podcast transcript line",
                        maximum=20_000,
                    ),
                    **({"sec": sec} if sec is not None else {}),
                    **(
                        {
                            "time": builder._string(
                                segment["time"],
                                field="podcast transcript time",
                                maximum=50,
                            )
                        }
                        if segment.get("time")
                        else {}
                    ),
                    **(
                        {
                            "who": builder._string(
                                segment["who"],
                                field="podcast transcript speaker",
                                maximum=1_000,
                            )
                        }
                        if segment.get("who")
                        else {}
                    ),
                }
            )
        transcript_provenance = builder._provenance(
            repository=REPOSITORY,
            revision=checkout.commit_sha,
            source_path=transcript_relative,
            source_key=slug,
            checksum=base.checksum_of(checkout, transcript_relative),
        )
        return transcript, transcript_provenance


register_parser(CONTENT_KIND, PodcastsParser())
