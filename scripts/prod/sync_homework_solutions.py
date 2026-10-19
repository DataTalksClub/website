#!/usr/bin/env python3
"""Sync private solutions to an explicit database target; content never lands on disk.

Git-synchronized. Requires existing cohort/homework rows and a read-only repository
contents token in ZOOMCAMP_SOLUTIONS_TOKEN, or an external private --checkout.
"""

import argparse
import base64
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import requests
import yaml
from django.db import DatabaseError, transaction
from django.utils.dateparse import parse_datetime
from django.utils.timezone import is_aware

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.prod.target import add_target_arguments, configure_target  # noqa: E402

SYNC_MODEL = "git-synchronized"
BOOTSTRAPS_EMPTY_DATABASE = False


REPOSITORY = "DataTalksClub/zoomcamp-solutions"
MAX_FILE_BYTES = 2_000_000
MAX_TOTAL_BYTES = 20_000_000
MAX_SOLUTIONS = 200
PATH_PATTERN = re.compile(r"([a-z0-9][a-z0-9-]*)/([0-9]{4})/([a-z0-9][a-z0-9-]*)/answers.yaml")


class SolutionSyncError(ValueError):
    """Only fixed diagnostics: never include source content or provider responses."""


class UniqueKeyLoader(yaml.SafeLoader):
    pass


def _unique_mapping(loader, node):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node)
        if not isinstance(key, str) or key in result:
            raise SolutionSyncError("Invalid or duplicate manifest key.")
        result[key] = loader.construct_object(value_node)
    return result


UniqueKeyLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _unique_mapping)


def _bounded(raw):
    if len(raw) > MAX_FILE_BYTES:
        raise SolutionSyncError("Source file exceeds the size limit.")
    return raw.decode("utf-8")


def github_snapshot(ref="main"):
    token = os.environ.get("ZOOMCAMP_SOLUTIONS_TOKEN")
    if not token:
        raise SolutionSyncError("ZOOMCAMP_SOLUTIONS_TOKEN is required.")
    if not re.fullmatch(r"[a-zA-Z0-9._-]+", ref):
        raise SolutionSyncError("Invalid source ref.")
    session = requests.Session()
    session.headers.update(
        {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
    )

    def get(path):
        response = session.get(
            f"https://api.github.com/repos/{REPOSITORY}" + (f"/{path}" if path else ""),
            timeout=30,
            allow_redirects=False,
            stream=True,
        )
        with response:
            if response.status_code != 200:
                raise SolutionSyncError("Private repository request failed.")
            chunks = []
            size = 0
            for chunk in response.iter_content(65536):
                size += len(chunk)
                if size > MAX_TOTAL_BYTES:
                    raise SolutionSyncError("Repository response exceeds the size limit.")
                chunks.append(chunk)
            return json.loads(b"".join(chunks))

    if get("").get("private") is not True:
        raise SolutionSyncError("Solutions repository must be private.")
    commit = get(f"commits/{ref}")["sha"]
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise SolutionSyncError("Invalid source commit.")
    tree = get(f"git/trees/{commit}?recursive=1")
    if tree.get("truncated"):
        raise SolutionSyncError("Repository tree is incomplete.")
    entries = {item["path"]: item for item in tree["tree"]}

    def read(path):
        item = entries.get(path, {})
        if item.get("mode") != "100644" or item.get("type") != "blob":
            raise SolutionSyncError("Required source file is missing or not a regular file.")
        if item.get("size", MAX_FILE_BYTES + 1) > MAX_FILE_BYTES:
            raise SolutionSyncError("Source file exceeds the size limit.")
        blob = get(f"git/blobs/{item['sha']}")
        if blob.get("encoding") != "base64":
            raise SolutionSyncError("Unsupported source encoding.")
        return _bounded(base64.b64decode(blob["content"]))

    return commit, entries, read


def checkout_snapshot(checkout, ref="HEAD"):
    """Read immutable Git objects, never working files or symlink targets."""
    root = Path(checkout).resolve()

    def git(*args):
        return subprocess.check_output(["git", "-C", str(root), *args], stderr=subprocess.DEVNULL)

    commit = git("rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}").decode().strip()
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise SolutionSyncError("Invalid source commit.")
    entries = {}
    for record in git("ls-tree", "-rz", commit).split(b"\0"):
        if record:
            meta, path = record.split(b"\t", 1)
            mode, kind, sha = meta.decode().split()
            entries[path.decode()] = {"mode": mode, "type": kind, "sha": sha}

    def read(path):
        item = entries.get(path, {})
        if item.get("mode") != "100644" or item.get("type") != "blob":
            raise SolutionSyncError("Required source file is missing or not a regular file.")
        if int(git("cat-file", "-s", item["sha"])) > MAX_FILE_BYTES:
            raise SolutionSyncError("Source file exceeds the size limit.")
        return _bounded(git("cat-file", "blob", item["sha"]))

    return commit, entries, read


def _manifest(raw):
    value = yaml.load(raw, Loader=UniqueKeyLoader)
    required = {"schema_version", "cohort", "homework_slug", "published", "publish_at", "answers"}
    if not isinstance(value, dict) or set(value) != required:
        raise SolutionSyncError("Invalid answers manifest fields.")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise SolutionSyncError("Unsupported answers schema.")
    if not isinstance(value["cohort"], str) or not re.fullmatch(r"[a-zA-Z0-9_-]+", value["cohort"]):
        raise SolutionSyncError("Invalid cohort identifier.")
    if not isinstance(value["homework_slug"], str) or not re.fullmatch(
        r"[a-zA-Z0-9_-]+", value["homework_slug"]
    ):
        raise SolutionSyncError("Invalid homework slug.")
    if type(value["published"]) is not bool:
        raise SolutionSyncError("Publication flag must be a boolean.")
    date = value["publish_at"]
    if date is not None:
        if not isinstance(date, str):
            raise SolutionSyncError("Publication date must be a quoted ISO timestamp.")
        date = parse_datetime(date)
        if date is None or not is_aware(date):
            raise SolutionSyncError("Publication date must include a timezone.")
    answers = value["answers"]
    if not isinstance(answers, list) or not answers or len(answers) > 200:
        raise SolutionSyncError("Answers must be a nonempty bounded list.")
    ids = set()
    for answer in answers:
        if not isinstance(answer, dict) or set(answer) != {"question_id", "answer"}:
            raise SolutionSyncError("Invalid answer fields.")
        key = answer["question_id"]
        if not isinstance(key, str) or not re.fullmatch(r"[a-zA-Z0-9_-]+", key) or key in ids:
            raise SolutionSyncError("Answer question IDs must be unique stable identifiers.")
        if not isinstance(answer["answer"], str) or not answer["answer"].strip():
            raise SolutionSyncError("Answer text is required.")
        ids.add(key)
    return value, date


def sync_snapshot(snapshot):
    with transaction.atomic():
        return _sync_snapshot(snapshot)


def _sync_snapshot(snapshot):
    from courses.models.homework import Homework
    from courses.models.homework_solution import HomeworkSolution

    commit, entries, read = snapshot
    paths = sorted(path for path in entries if path.endswith("/answers.yaml"))
    if not paths or len(paths) > MAX_SOLUTIONS:
        raise SolutionSyncError("Source must contain a bounded nonempty set of solutions.")
    seen = set()
    total_bytes = 0
    for path in paths:
        match = PATH_PATTERN.fullmatch(path)
        if match is None:
            raise SolutionSyncError("Invalid solution source path.")
        course, year, _directory = match.groups()
        manifest_raw = read(path)
        markdown = read(path.removesuffix("answers.yaml") + "solution.md")
        total_bytes += len(manifest_raw.encode()) + len(markdown.encode())
        if total_bytes > MAX_TOTAL_BYTES or not markdown.strip():
            raise SolutionSyncError("Invalid solution content size.")
        manifest, publish_at = _manifest(manifest_raw)
        homework = Homework.objects.filter(
            course__course__slug=course,
            course__year=int(year),
            course__identifier=manifest["cohort"],
            slug=manifest["homework_slug"],
        ).first()
        if homework is None or homework.pk in seen:
            raise SolutionSyncError("Missing or duplicate homework target.")
        seen.add(homework.pk)
        existing = HomeworkSolution.objects.filter(homework=homework).first()
        if existing and existing.source_path != path:
            raise SolutionSyncError("Solution target is already owned by another source path.")
        HomeworkSolution.objects.update_or_create(
            homework=homework,
            defaults={
                "markdown": markdown,
                "answers": manifest["answers"],
                "published": manifest["published"],
                "publish_at": publish_at,
                "source_path": path,
                "source_commit": commit,
            },
        )
    # A complete authoritative snapshot also revokes removed records.
    HomeworkSolution.objects.exclude(homework_id__in=seen).delete()
    return len(seen), commit


def sync_solutions(*, checkout=None, ref="main"):
    try:
        snapshot = checkout_snapshot(checkout, ref) if checkout else github_snapshot(ref)
        return sync_snapshot(snapshot)
    except (
        ValueError,
        TypeError,
        KeyError,
        RecursionError,
        UnicodeError,
        OSError,
        subprocess.CalledProcessError,
        requests.RequestException,
        yaml.YAMLError,
        DatabaseError,
    ):
        raise SolutionSyncError(
            "Private solutions sync failed; no database changes applied."
        ) from None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    add_target_arguments(parser)
    parser.add_argument("--checkout")
    parser.add_argument("--ref", default="main")
    args = parser.parse_args(argv)
    configure_target(parser, args, scratch_database_only=True)
    try:
        count, commit = sync_solutions(checkout=args.checkout, ref=args.ref)
    except SolutionSyncError as error:
        parser.exit(1, str(error) + "\n")
    print(f"Synced {count} solution(s) at {commit}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
