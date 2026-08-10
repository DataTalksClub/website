from __future__ import annotations

import os
import shutil
import struct
import subprocess
import tempfile
import zlib
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from content_sync.dtc_content.repository import VerifiedCheckout, verify_dtc_content_checkout

PROJECT_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures" / "dtc_content"
SCRATCH_ROOT = PROJECT_ROOT / ".tmp" / "issue-103-tests"


def jpeg_bytes(*, comment: bytes = b"") -> bytes:
    comment_segment = (
        b"\xff\xfe" + (len(comment) + 2).to_bytes(2, "big") + comment if comment else b""
    )
    return b"".join(
        (
            b"\xff\xd8",
            b"\xff\xdb\x00\x43\x00" + (b"\x01" * 64),
            b"\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00",
            b"\xff\xc4\x00\x14\x00\x01" + (b"\x00" * 15) + b"\x00",
            b"\xff\xc4\x00\x14\x10\x01" + (b"\x00" * 15) + b"\x00",
            comment_segment,
            b"\xff\xda\x00\x08\x01\x01\x00\x00\x3f\x00",
            b"\x3f",
            b"\xff\xd9",
        )
    )


def progressive_jpeg_bytes(*, precision: int = 8) -> bytes:
    return b"".join(
        (
            b"\xff\xd8",
            b"\xff\xdb\x00\x43\x00" + (b"\x01" * 64),
            b"\xff\xc2\x00\x0b" + bytes((precision,)) + b"\x00\x01\x00\x01\x01\x01\x11\x00",
            b"\xff\xc4\x00\x14\x00\x01" + (b"\x00" * 15) + b"\x00",
            b"\xff\xda\x00\x08\x01\x01\x00\x00\x00\x00",
            b"\x3f",
            b"\xff\xd9",
        )
    )


def _png_chunk(chunk_type: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + chunk_type
        + payload
        + struct.pack(">I", zlib.crc32(chunk_type + payload) & 0xFFFFFFFF)
    )


def png_bytes() -> bytes:
    return b"".join(
        (
            b"\x89PNG\r\n\x1a\n",
            _png_chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0)),
            _png_chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00\xff")),
            _png_chunk(b"IEND", b""),
        )
    )


def gif_bytes() -> bytes:
    return (
        b"GIF89a\x01\x00\x01\x00\x80\x00\x00"
        b"\x00\x00\x00\xff\xff\xff"
        b",\x00\x00\x00\x00\x01\x00\x01\x00\x00"
        b"\x02\x02D\x01\x00;"
    )


MEDIA_BYTES = {
    (
        "images/posts/2025-08-11-tab-1-how-to-build-blood-cell-classifier-for-"
        "cancer-prediction-case-study-from-ml-zoomcamp/image10.gif"
    ): gif_bytes(),
    "images/posts/2024-11-11-llm-zoomcamp/llm-zoomcamp-certificate.jpeg": jpeg_bytes(
        comment=b"fixture-jpeg"
    ),
    "images/podcast/open-source-turned-into-career-and-startup-creation.jpg": jpeg_bytes(
        comment=b"fixture-podcast"
    ),
    "images/posts/2022-10-02-naming-variables-in-machine-learning/image1.png": png_bytes(),
    "images/books/20201214-ml-bookcamp/cover.jpg": jpeg_bytes(comment=b"fixture-book-cover"),
    "images/books/20201214-ml-bookcamp/preview.jpg": jpeg_bytes(comment=b"fixture-book-preview"),
}


@contextmanager
def fixture_checkout() -> Iterator[Path]:
    SCRATCH_ROOT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="checkout-", dir=SCRATCH_ROOT) as temporary:
        root = Path(temporary) / "content"
        shutil.copytree(FIXTURE_ROOT, root)
        for relative, data in MEDIA_BYTES.items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        yield root


def initialize_fixture_repository(root: Path, *, salt: str = "") -> str:
    environment = {
        **os.environ,
        "GIT_AUTHOR_DATE": "2000-01-01T00:00:00Z",
        "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
        "GIT_AUTHOR_NAME": "Fixture Author",
        "GIT_COMMITTER_DATE": "2000-01-01T00:00:00Z",
        "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
        "GIT_COMMITTER_NAME": "Fixture Author",
    }
    if (root / ".git").is_dir():
        result = subprocess.run(
            ("git", "-C", str(root), "rev-parse", "HEAD"),
            check=True,
            capture_output=True,
            text=True,
            env=environment,
        )
        return result.stdout.strip()
    subprocess.run(
        ("git", "-C", str(root), "init", "--initial-branch=main"),
        check=True,
        capture_output=True,
        env=environment,
    )
    subprocess.run(
        (
            "git",
            "-C",
            str(root),
            "remote",
            "add",
            "origin",
            "https://github.com/DataTalksClub/content.git",
        ),
        check=True,
        capture_output=True,
        env=environment,
    )
    subprocess.run(
        ("git", "-C", str(root), "add", "."),
        check=True,
        capture_output=True,
        env=environment,
    )
    subprocess.run(
        ("git", "-C", str(root), "commit", "-m", f"Fixture content {salt}".rstrip()),
        check=True,
        capture_output=True,
        env=environment,
    )
    result = subprocess.run(
        ("git", "-C", str(root), "rev-parse", "HEAD"),
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )
    return result.stdout.strip()


def verify_fixture_checkout(root: Path, *, salt: str = "") -> VerifiedCheckout:
    commit = initialize_fixture_repository(root, salt=salt)
    return verify_dtc_content_checkout(root, expected_commit=commit)
