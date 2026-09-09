"""The release runbook describes the live pipeline (audit REL-19 acceptance).

Every deploy command in ``_docs/runbooks/release-deployments.md`` must map to
a live workflow or CLI, the inventory's owner and test files must exist, and
the two superseded runbooks must be labelled archived evidence. These checks
fail when the path moves and the runbook does not move with it.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

from deploy.deployment_targets import DEPLOYMENT_TARGETS

ROOT = Path(__file__).resolve().parents[2]
RUNBOOK = ROOT / "_docs" / "runbooks" / "release-deployments.md"
ARCHIVED = ("production-deployment-bootstrap.md", "development-release.md")


def runbook_text() -> str:
    return RUNBOOK.read_text(encoding="utf-8")


def repo_paths_mentioned(text: str) -> set[str]:
    """Repo-relative paths the runbook names as files."""

    candidates = set(re.findall(r"`(\.github/workflows/[\w.-]+)`", text))
    candidates |= set(re.findall(r"`(deploy/[\w./-]+)`", text))
    candidates |= {f"_docs/runbooks/{name}" for name in ARCHIVED}
    return {path for path in candidates if not path.endswith(".py) ")}


def test_every_documented_entry_point_exists() -> None:
    text = runbook_text()
    missing = sorted(path for path in repo_paths_mentioned(text) if not (ROOT / path).exists())
    assert not missing, f"runbook names paths that do not exist: {missing}"


def test_the_inventorys_test_files_exist() -> None:
    inventory = runbook_text().split("## Command inventory")[1]
    referenced = set(re.findall(r"`((?:ci|core|scripts)/tests/[\w/.-]+\.py)`", inventory))
    assert referenced, "the command inventory must name its tests"
    missing = sorted(path for path in referenced if not (ROOT / path).exists())
    assert not missing, f"inventory names test files that do not exist: {missing}"


def test_the_runbook_names_one_owner_per_concern() -> None:
    text = runbook_text()
    assert "`deploy/deploy_website.sh`" in text
    assert "`deploy/deployment_targets.py`" in text
    assert "deploy.ci_verdict" in text
    assert "recovery_receipt.py" in text
    # The legacy controller is documented as retained, not as the supported
    # path (whitespace-normalized so markdown line wraps do not matter).
    assert "deploy/cli.py" in text
    flattened = " ".join(text.split())
    assert "**not** the supported deployment path" in flattened


def test_the_runbook_documents_both_workflows_and_the_dev_then_prod_order() -> None:
    text = runbook_text()
    assert "deploy-dev.yml" in text
    assert "deploy-prod.yml" in text
    assert "confirm_production" in text
    dev = text.index("deploy-dev.yml")
    prod = text.index("deploy-prod.yml")
    assert dev < prod, "the supported order is dev first, then production"


def test_archived_runbooks_are_labelled_as_evidence() -> None:
    for name in ARCHIVED:
        text = (ROOT / "_docs" / "runbooks" / name).read_text(encoding="utf-8")
        assert "ARCHIVED EVIDENCE" in text, name
        assert "release-deployments.md" in text, name


def test_named_targets_match_the_registry() -> None:
    text = runbook_text()
    for name, target in DEPLOYMENT_TARGETS.items():
        if target.retired:
            assert name in text, f"the retired target {name} must stay documented"
            assert "retired" in text
        else:
            assert name in text, f"the deployable target {name} must be documented"


def test_an_offline_profile_example_reaches_the_validation_boundary() -> None:
    """The documented profile command runs offline and fails closed."""

    good = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "deploy.deployment_targets", "profile"],
        capture_output=True,
        text=True,
        cwd=ROOT,
        env={"DTC_DEPLOYMENT_TARGET": "website-development", "PATH": "/usr/bin:/bin"},
    )
    assert good.returncode == 0, good.stderr
    assert "CLUSTER_NAME=website-production" in good.stdout

    retired = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "deploy.deployment_targets", "profile"],
        capture_output=True,
        text=True,
        cwd=ROOT,
        env={"DTC_DEPLOYMENT_TARGET": "website-sandbox", "PATH": "/usr/bin:/bin"},
    )
    assert retired.returncode != 0
