"""FAQ parser: the course FAQ from ``DataTalksClub/faq``.

One published document per course (the course is one public page at
``/faq/<course>.html``), mirroring the reviewed FAQ import the staged
pipeline carried (``scripts/prod/import_faq.py``).  The record carries the
course's section tree -- section names and order from the course's
``_metadata.yaml``, questions from ``_questions/<course>/<section>/`` sorted
by their declared ``sort_order`` -- plus each question's raw markdown body
and declared image list, so the reader keeps rendering the bodies at request
time exactly as the staged rows did.
"""

import json
from pathlib import Path, PurePosixPath

from community_base.content_sync.orchestration import UpsertResult
from community_base.content_sync.parsers import SourceItem, register_parser

from content.faq_data import _QUESTION_ID, _faq_question_slug
from scripts import build_public_projection as builder

from . import base

SOURCE_SLUG = "dtc-faq"
CONTENT_KIND = "faq"
REPOSITORY = "DataTalksClub/faq"
QUESTIONS_ROOT = "_questions"


class FaqParser:
    def discover(self, checkout, source):
        if source.slug != SOURCE_SLUG:
            return []
        base.activate(checkout)
        items = [self._item(checkout, course) for course in self._courses(checkout)]
        items.sort(key=lambda item: item.key)
        return items

    def upsert(self, item, source, media):
        record = item.data["record"]
        checkout = base.active_checkout()
        for image in record["declared_images"]:
            media.upload(checkout, image, source)
        document, action = base.upsert_document(
            source,
            content_kind=CONTENT_KIND,
            stable_key=record["course"],
            slug=record["course"],
            title=record["course_name"],
            summary="",
            public_path=f"/faq/{record['course']}.html",
            source_path=record["source_path"],
            checksum=item.data["checksum"],
            record=record,
        )
        return UpsertResult(document, action)

    def soft_delete_missing(self, seen_keys, source):
        if source.slug != SOURCE_SLUG:
            return 0
        return base.delete_missing(source, CONTENT_KIND, seen_keys)

    def _courses(self, checkout) -> list[str]:
        courses = []
        for relative in checkout.files():
            path = PurePosixPath(str(relative))
            if path.parts[:1] != (QUESTIONS_ROOT,) or path.name != "_metadata.yaml":
                continue
            if len(path.parts) != 3:
                continue
            courses.append(path.parts[1])
        return sorted(courses)

    def _item(self, checkout, course: str) -> SourceItem:
        source_path = f"{QUESTIONS_ROOT}/{course}/_metadata.yaml"
        checksum = base.checksum_of(checkout, source_path)
        snapshot = base.snapshot_path(checkout, source_path)
        metadata = builder._load_yaml(snapshot)
        if not isinstance(metadata, dict) or not metadata.get("course_name"):
            base.fail("faq course metadata rejected", source_path)
        sections = []
        declared_images: list[str] = []
        question_count = 0
        names = metadata.get("sections")
        if not isinstance(names, list) or not names:
            base.fail("faq course without sections", source_path)
        for section in names:
            if not isinstance(section, dict) or not section.get("id") or not section.get("name"):
                base.fail("faq section metadata rejected", source_path)
            questions = []
            for relative in checkout.files():
                path = PurePosixPath(str(relative))
                if path.parts[:3] != (QUESTIONS_ROOT, course, section["id"]):
                    continue
                if len(path.parts) != 4 or path.suffix != ".md" or path.name.startswith("_"):
                    continue
                questions.append(self._question(checkout, course, path, declared_images))
            questions.sort(key=lambda question: (question["sort_order"], question["filename"]))
            for question in questions:
                del question["filename"]
            question_count += len(questions)
            if not questions:
                # The reviewed import published only sections that carry
                # questions; an empty section renders nothing and would change
                # the published section counts for no reader.
                continue
            sections.append(
                {
                    "id": str(section["id"]),
                    "name": builder._string(section["name"], field="faq section name", maximum=500),
                    "comment": builder._string(
                        section.get("comment"), field="faq section comment", maximum=2_000,
                        optional=True,
                    ),
                    "questions": questions,
                }
            )
        if not question_count:
            base.fail("faq course without questions", source_path)
        record = {
            "course": course,
            "course_name": builder._string(
                metadata["course_name"], field="faq course name", maximum=500
            ),
            "slack_channel": builder._string(
                metadata.get("slack_channel"), field="faq slack channel", maximum=200,
                optional=True,
            ),
            "sections": sections,
            "declared_images": sorted(set(declared_images)),
            "source_path": source_path,
            "provenance": builder._provenance(
                repository=REPOSITORY,
                revision=checkout.commit_sha,
                source_path=source_path,
                source_key=course,
                checksum=checksum,
            ),
        }
        # The record derives from the metadata file plus every question file and
        # its declared images, so the change-detection checksum covers the whole
        # derived record, not just the metadata file it started from.
        item_checksum = builder._sha256_bytes(
            (checksum + "|" + json.dumps(record, sort_keys=True, ensure_ascii=False)).encode()
        )
        return SourceItem(
            key=course, path=source_path, data={"record": record, "checksum": item_checksum}
        )

    def _question(self, checkout, course: str, path: PurePosixPath, declared_images: list[str]):
        relative = path.as_posix()
        metadata, body = builder._frontmatter(base.snapshot_path(checkout, relative))
        question_id = str(metadata.get("id") or "")
        if not _QUESTION_ID.fullmatch(question_id):
            base.fail("faq question id rejected", relative)
        question = str(metadata.get("question") or "")
        if not question:
            base.fail("faq question without a question line", relative)
        sort_order = builder._positive_integer(metadata.get("sort_order"), field="faq sort order")
        images = []
        for image in metadata.get("images") or []:
            if not isinstance(image, dict) or not image.get("id") or not image.get("path"):
                base.fail("faq question image declaration rejected", relative)
            image_path = str(image["path"])
            if not self._exists(checkout, image_path):
                base.fail("faq question image missing from the checkout", relative)
            if image_path not in declared_images:
                declared_images.append(image_path)
            images.append(
                {
                    "id": str(image["id"]),
                    "description": builder._string(
                        image.get("description"), field="faq image description", maximum=500,
                        optional=True,
                    ),
                    "path": image_path,
                }
            )
        return {
            "id": question_id,
            "slug": _faq_question_slug(path.name),
            "question": builder._string(question, field="faq question", maximum=2_000),
            "sort_order": sort_order,
            "images": images,
            "body": body,
            "filename": path.name,
        }

    @staticmethod
    def _exists(checkout, relative: str) -> bool:
        try:
            base.snapshot_path(checkout, relative)
        except Exception:
            return False
        return Path(base.snapshot_path(checkout, relative)).is_file()


register_parser(CONTENT_KIND, FaqParser())
