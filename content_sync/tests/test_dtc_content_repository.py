from __future__ import annotations

import hashlib
import io
import os
import socket
import subprocess
import threading
import time
from pathlib import Path
from unittest.mock import patch

from django.core.management import CommandError, call_command
from django.test import TestCase

from content_sync.dtc_content.repository import (
    DtcContentCheckoutError,
    verify_dtc_content_checkout,
)

from .helpers import fixture_checkout


def _git(root: Path, *arguments: str) -> str:
    environment = {
        **os.environ,
        "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
        "GIT_AUTHOR_NAME": "Fixture Author",
        "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
        "GIT_COMMITTER_NAME": "Fixture Author",
    }
    result = subprocess.run(
        ("git", "-C", str(root), *arguments),
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )
    return result.stdout.strip()


def _git_blob(root: Path, commit: str, path: str) -> bytes:
    return subprocess.run(
        ("git", "-C", str(root), "show", f"{commit}:{path}"),
        check=True,
        capture_output=True,
    ).stdout


def _initialize_checkout(root: Path) -> str:
    script = root / "scripts" / "validate_content.py"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(
        "from pathlib import Path\nPath('source-code-was-executed').write_text('unsafe')\n",
        encoding="utf-8",
    )
    _git(root, "init", "--initial-branch=main")
    _git(root, "remote", "add", "origin", "https://github.com/DataTalksClub/content.git")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "Fixture content")
    return _git(root, "rev-parse", "HEAD")


class DtcContentRepositoryVerificationTests(TestCase):
    def test_exact_clean_checkout_passes_without_executing_source_code(self) -> None:
        with fixture_checkout() as root:
            commit = _initialize_checkout(root)
            result = verify_dtc_content_checkout(root, expected_commit=commit)
            self.assertEqual(result.commit_sha, commit)
            self.assertEqual(result.bundle.counts["media"], 7)
            self.assertFalse((root / "source-code-was-executed").exists())

            output = io.StringIO()
            call_command(
                "verify_dtc_content",
                checkout=root,
                expected_commit=commit,
                stdout=output,
            )
            rendered = output.getvalue()
            self.assertIn('"status":"PASS"', rendered)
            self.assertIn(f'"commit_sha":"{commit}"', rendered)
            self.assertNotIn(str(root), rendered)
            self.assertNotIn("Fixture segmentation article", rendered)

    def test_checkout_local_fsmonitor_is_never_executed(self) -> None:
        with fixture_checkout() as root:
            commit = _initialize_checkout(root)
            marker = root.parent / "checkout-fsmonitor-was-executed"
            fsmonitor = root / ".git" / "fixture-fsmonitor"
            fsmonitor.write_text(
                f"#!/bin/sh\ntouch '{marker}'\nprintf '0\\n'\n",
                encoding="utf-8",
            )
            fsmonitor.chmod(0o700)
            _git(root, "config", "core.fsmonitor", str(fsmonitor))

            result = verify_dtc_content_checkout(root, expected_commit=commit)

            self.assertEqual(result.commit_sha, commit)
            self.assertFalse(marker.exists())

    def test_missing_promisor_blob_rejects_without_lazy_fetch_or_helper_execution(self) -> None:
        with fixture_checkout() as root:
            commit = _initialize_checkout(root)
            selected_path = "articles/2020-11-29-segmentation.md"
            blob_sha = _git(root, "rev-parse", f"{commit}:{selected_path}")
            blob_path = root / ".git" / "objects" / blob_sha[:2] / blob_sha[2:]
            self.assertTrue(blob_path.is_file())

            credential_marker = root.parent / "credential-helper-was-executed"
            credential_helper = root.parent / "credential-helper"
            credential_helper.write_text(
                f"#!/bin/sh\ntouch '{credential_marker}'\nexit 1\n",
                encoding="utf-8",
            )
            credential_helper.chmod(0o700)

            connection_accepted = threading.Event()
            stop_listener = threading.Event()
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
                listener.bind(("127.0.0.1", 0))
                listener.listen()
                listener.settimeout(0.05)
                proxy_port = listener.getsockname()[1]

                def observe_connections() -> None:
                    while not stop_listener.is_set():
                        try:
                            connection, _address = listener.accept()
                        except TimeoutError:
                            continue
                        except OSError:
                            return
                        connection_accepted.set()
                        connection.close()

                observer = threading.Thread(target=observe_connections, daemon=True)
                observer.start()
                try:
                    _git(root, "config", "extensions.partialClone", "origin")
                    _git(root, "config", "remote.origin.promisor", "true")
                    _git(root, "config", "remote.origin.partialclonefilter", "blob:none")
                    _git(root, "config", "http.proxy", f"http://127.0.0.1:{proxy_port}")
                    _git(root, "config", "credential.helper", str(credential_helper))
                    blob_path.unlink()

                    started = time.monotonic()
                    with self.assertRaises(DtcContentCheckoutError) as raised:
                        verify_dtc_content_checkout(root, expected_commit=commit)
                    elapsed = time.monotonic() - started
                finally:
                    stop_listener.set()
                    observer.join(timeout=1)

            self.assertEqual(raised.exception.code, "git_tree_inventory_invalid")
            self.assertLess(elapsed, 5)
            self.assertFalse(observer.is_alive())
            self.assertFalse(connection_accepted.is_set())
            self.assertFalse(credential_marker.exists())

    def test_committed_export_attributes_cannot_transform_materialized_blob_bytes(self) -> None:
        token = b"$Format:%<(23,trunc)%H$"
        self.assertEqual(len(token), 23)
        article_path = "articles/2020-11-29-segmentation.md"
        svg_path = "images/podcast/badges/spotify.svg"
        with fixture_checkout() as root:
            _initialize_checkout(root)
            article = root / article_path
            article.write_bytes(article.read_bytes() + b"\n" + token + b"\n")
            (root / ".gitattributes").write_text(
                "articles/* export-subst\n",
                encoding="utf-8",
            )
            svg = root / svg_path
            svg.write_bytes(svg.read_bytes().replace(b"</svg>", b"<!--" + token + b"--></svg>"))
            (root / "images" / ".gitattributes").write_text(
                "podcast/**/*.svg export-subst\n",
                encoding="utf-8",
            )
            _git(root, "add", ".")
            _git(root, "commit", "-m", "Add same-length archive substitutions")
            commit = _git(root, "rev-parse", "HEAD")
            expected_article = _git_blob(root, commit, article_path)
            expected_svg = _git_blob(root, commit, svg_path)

            verified = verify_dtc_content_checkout(root, expected_commit=commit)

        article_document = next(
            document
            for document in verified.bundle.documents
            if document.source_path == article_path
        )
        article_lines = expected_article.decode("utf-8").splitlines(keepends=True)
        closing = next(
            index for index, line in enumerate(article_lines[1:], 1) if line.strip() == "---"
        )
        self.assertEqual(article_document.raw_body, "".join(article_lines[closing + 1 :]))
        self.assertEqual(article_document.checksum, hashlib.sha256(expected_article).hexdigest())
        self.assertIn(token.decode("ascii"), article_document.raw_body)
        svg_asset = next(asset for asset in verified.bundle.assets if asset.source_path == svg_path)
        self.assertEqual(svg_asset.size, len(expected_svg))
        self.assertEqual(svg_asset.checksum, hashlib.sha256(expected_svg).hexdigest())

    def test_wrong_origin_sha_dirty_root_and_symlink_fail_closed(self) -> None:
        with fixture_checkout() as root:
            commit = _initialize_checkout(root)
            _git(root, "remote", "set-url", "origin", "https://example.com/not-content.git")
            with self.assertRaises(DtcContentCheckoutError) as raised:
                verify_dtc_content_checkout(root, expected_commit=commit)
            self.assertEqual(raised.exception.code, "checkout_origin_mismatch")

        with fixture_checkout() as root:
            commit = _initialize_checkout(root)
            with self.assertRaises(DtcContentCheckoutError) as raised:
                verify_dtc_content_checkout(root, expected_commit="0" * 40)
            self.assertEqual(raised.exception.code, "checkout_commit_mismatch")

        with fixture_checkout() as root:
            commit = _initialize_checkout(root)
            (root / "README.md").write_text("dirty", encoding="utf-8")
            with self.assertRaises(DtcContentCheckoutError) as raised:
                verify_dtc_content_checkout(root, expected_commit=commit)
            self.assertEqual(raised.exception.code, "checkout_dirty")

        with fixture_checkout() as root:
            commit = _initialize_checkout(root)
            with self.assertRaises(DtcContentCheckoutError) as raised:
                verify_dtc_content_checkout(root / "articles", expected_commit=commit)
            self.assertEqual(raised.exception.code, "checkout_root_mismatch")

        with fixture_checkout() as root:
            commit = _initialize_checkout(root)
            link = root.parent / "content-link"
            link.symlink_to(root, target_is_directory=True)
            try:
                with self.assertRaises(DtcContentCheckoutError) as raised:
                    verify_dtc_content_checkout(link, expected_commit=commit)
                self.assertEqual(raised.exception.code, "checkout_directory_invalid")
            finally:
                link.unlink()

    def test_management_command_requires_explicit_verified_checkout(self) -> None:
        with self.assertRaises(CommandError):
            call_command(
                "verify_dtc_content",
                checkout=Path(".tmp/does-not-exist"),
                expected_commit="0" * 40,
            )

        with fixture_checkout() as root:
            commit = _initialize_checkout(root)
            with self.assertRaises(DtcContentCheckoutError) as relative_error:
                verify_dtc_content_checkout(Path("relative-checkout"), expected_commit=commit)
            self.assertEqual(relative_error.exception.code, "checkout_path_not_absolute")

            with self.assertRaises(DtcContentCheckoutError) as malformed_error:
                verify_dtc_content_checkout(root, expected_commit="main")
            self.assertEqual(malformed_error.exception.code, "expected_commit_invalid")

            (root / "untracked.txt").write_text("untracked", encoding="utf-8")
            with self.assertRaises(DtcContentCheckoutError) as untracked_error:
                verify_dtc_content_checkout(root, expected_commit=commit)
            self.assertEqual(untracked_error.exception.code, "checkout_dirty")

        with fixture_checkout() as root:
            commit = _initialize_checkout(root)
            _git(root, "remote", "remove", "origin")
            with self.assertRaises(DtcContentCheckoutError) as missing_origin_error:
                verify_dtc_content_checkout(root, expected_commit=commit)
            self.assertEqual(missing_origin_error.exception.code, "checkout_origin_mismatch")

    def test_management_command_bounds_media_worker_startup_failure(self) -> None:
        canary = "sensitive-worker-canary"
        with fixture_checkout() as root:
            commit = _initialize_checkout(root)
            with (
                patch("content_sync.dtc_content.media._PARALLEL_MEDIA_MIN_ITEMS", 1),
                patch("content_sync.dtc_content.media.current_process") as process,
                patch("content_sync.dtc_content.media.os.process_cpu_count", return_value=2),
                patch(
                    "content_sync.dtc_content.media.get_all_start_methods",
                    return_value=["fork"],
                ),
                patch(
                    "content_sync.dtc_content.media.ProcessPoolExecutor",
                    side_effect=RuntimeError(canary),
                ),
                self.assertRaises(CommandError) as raised,
            ):
                process.return_value.daemon = False
                call_command(
                    "verify_dtc_content",
                    checkout=root,
                    expected_commit=commit,
                )

        self.assertEqual(
            str(raised.exception),
            "media_validation_worker_failed:images/books/20201214-ml-bookcamp/cover.jpg",
        )
        self.assertNotIn(canary, str(raised.exception))

    def test_ignored_and_hidden_worktree_bytes_cannot_enter_verified_bundle(self) -> None:
        with fixture_checkout() as root:
            commit = _initialize_checkout(root)
            baseline = verify_dtc_content_checkout(root, expected_commit=commit).bundle
            relative = (
                "images/posts/2022-07-12-building-data-science-team/"
                "how-to-build-data-science-team-cover.jpg"
            )
            injected = root / relative
            injected.parent.mkdir(parents=True, exist_ok=True)
            injected.write_bytes(b"not-source-content")
            (root / ".git" / "info" / "exclude").write_text(f"{relative}\n", encoding="utf-8")
            self.assertEqual(_git(root, "status", "--porcelain=v1", "--untracked-files=all"), "")
            verified = verify_dtc_content_checkout(root, expected_commit=commit)
            self.assertEqual(verified.bundle.bundle_sha256, baseline.bundle_sha256)
            self.assertEqual(verified.bundle.counts["media"], 7)

        for index_flag, delete_worktree_file in (
            ("--assume-unchanged", False),
            ("--skip-worktree", False),
            ("--skip-worktree", True),
        ):
            with (
                self.subTest(
                    index_flag=index_flag,
                    delete_worktree_file=delete_worktree_file,
                ),
                fixture_checkout() as root,
            ):
                commit = _initialize_checkout(root)
                baseline = verify_dtc_content_checkout(root, expected_commit=commit).bundle
                relative = "articles/2020-11-29-segmentation.md"
                _git(root, "update-index", index_flag, relative)
                article = root / relative
                if delete_worktree_file:
                    article.unlink()
                else:
                    article.write_text(
                        article.read_text(encoding="utf-8").replace(
                            "Fixture segmentation article",
                            "Locally altered but hidden article",
                        ),
                        encoding="utf-8",
                    )
                self.assertEqual(
                    _git(root, "status", "--porcelain=v1", "--untracked-files=all"), ""
                )
                verified = verify_dtc_content_checkout(root, expected_commit=commit)
                self.assertEqual(verified.bundle.bundle_sha256, baseline.bundle_sha256)
                title = next(
                    document.title
                    for document in verified.bundle.documents
                    if document.content_kind == "article"
                )
                self.assertEqual(title, "Fixture segmentation article")

    def test_index_worktree_divergence_is_dirty_and_commit_modes_are_fail_closed(self) -> None:
        with fixture_checkout() as root:
            commit = _initialize_checkout(root)
            article = root / "articles" / "2020-11-29-segmentation.md"
            article.write_text(article.read_text(encoding="utf-8") + "\nstaged\n", encoding="utf-8")
            _git(root, "add", "articles/2020-11-29-segmentation.md")
            article.write_text(article.read_text(encoding="utf-8") + "worktree\n", encoding="utf-8")
            with self.assertRaises(DtcContentCheckoutError) as raised:
                verify_dtc_content_checkout(root, expected_commit=commit)
            self.assertEqual(raised.exception.code, "checkout_dirty")

        with fixture_checkout() as root:
            _initialize_checkout(root)
            relative = "articles/2020-11-29-segmentation.md"
            (root / relative).chmod(0o755)
            _git(root, "add", relative)
            _git(root, "commit", "-m", "Retain legacy executable mode")
            commit = _git(root, "rev-parse", "HEAD")
            verified = verify_dtc_content_checkout(root, expected_commit=commit)
            self.assertEqual(verified.bundle.counts["articles"], 1)

        with fixture_checkout() as root:
            _initialize_checkout(root)
            relative = "images/books/20201214-ml-bookcamp/cover.jpg"
            _git(root, "rm", relative)
            (root / relative).symlink_to("preview.jpg")
            _git(root, "add", relative)
            _git(root, "commit", "-m", "Unsafe committed symlink")
            commit = _git(root, "rev-parse", "HEAD")
            with self.assertRaises(DtcContentCheckoutError) as raised:
                verify_dtc_content_checkout(root, expected_commit=commit)
            self.assertEqual(raised.exception.code, "git_tree_entry_mode_invalid")

        with fixture_checkout() as root:
            _initialize_checkout(root)
            head = _git(root, "rev-parse", "HEAD")
            relative = "images/posts/committed-gitlink"
            _git(root, "update-index", "--add", "--cacheinfo", f"160000,{head},{relative}")
            _git(root, "commit", "-m", "Unsafe committed gitlink")
            commit = _git(root, "rev-parse", "HEAD")
            _git(root, "update-index", "--skip-worktree", relative)
            with self.assertRaises(DtcContentCheckoutError) as raised:
                verify_dtc_content_checkout(root, expected_commit=commit)
            self.assertEqual(raised.exception.code, "git_tree_entry_mode_invalid")
