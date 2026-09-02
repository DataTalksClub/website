from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from deploy.contracts import ReleaseContractError, validate_source_sha
from deploy.deployment_targets import SELECTED_TARGET

#: The Terraform example variables of the selected deployment target's root.  The
#: path follows the target, and the hostname and robots value the check demands
#: come from the reviewed target rather than from the file being verified, so
#: parameterising the check does not make it vacuous.
TARGET_TERRAFORM_VARS_PATH = f"{SELECTED_TARGET.terraform_root}/terraform.tfvars.example"
#: Retained name for the same path.
DEVELOPMENT_TERRAFORM_VARS_PATH = TARGET_TERRAFORM_VARS_PATH

REQUIRED_TERRAFORM_PATHS = (
    "modules/django-website/compute.tf",
    "modules/django-website/edge.tf",
    TARGET_TERRAFORM_VARS_PATH,
    "tests/fixtures/website-production/main.tf",
)
#: The workloads ``modules/django-website`` registers, each of which must run on
#: the target's declared CPU architecture.
TASK_DEFINITION_COUNT = 3
TRUSTED_REVISION = "origin/main"
TRUSTED_REMOTES = frozenset(
    {
        "git@github.com:DataTalksClub/aws-infra.git",
        "https://github.com/DataTalksClub/aws-infra.git",
    }
)


@dataclass(frozen=True, slots=True)
class TerraformSeoEvidence:
    commit: str
    cache_behavior_count: int
    #: The ``task_cpu_architecture`` the trusted Terraform source declares for
    #: the selected target.  Read back rather than assumed so the architecture
    #: the release pipeline builds cannot drift from the applied stack.
    task_cpu_architecture: str


def _git(repository: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise ReleaseContractError("trusted Terraform source is unavailable")
    return result.stdout


def read_trusted_terraform_source(
    repository: Path,
    *,
    revision: str,
    expected_commit: str,
) -> tuple[str, dict[str, str]]:
    validate_source_sha(expected_commit)
    if not repository.is_dir():
        raise ReleaseContractError("Terraform repository path is not a directory")
    validate_trusted_repository_identity(
        remote=_git(repository, "config", "--get", "remote.origin.url").strip(),
        revision=revision,
    )
    commit = _git(repository, "rev-parse", f"{revision}^{{commit}}").strip()
    if commit != expected_commit:
        raise ReleaseContractError("Terraform revision does not match the expected commit")
    sources = {
        path: _git(repository, "show", f"{commit}:{path}") for path in REQUIRED_TERRAFORM_PATHS
    }
    return commit, sources


def validate_trusted_repository_identity(*, remote: str, revision: str) -> None:
    if revision != TRUSTED_REVISION:
        raise ReleaseContractError("Terraform verification requires exact origin/main")
    if remote not in TRUSTED_REMOTES:
        raise ReleaseContractError("Terraform repository identity is not trusted")


def _strip_comments(source: str) -> str:
    output: list[str] = []
    index = 0
    in_string = False
    escaped = False
    line_comment = False
    block_comment = False
    while index < len(source):
        character = source[index]
        following = source[index + 1] if index + 1 < len(source) else ""
        if line_comment:
            if character == "\n":
                line_comment = False
                output.append(character)
            index += 1
            continue
        if block_comment:
            if character == "*" and following == "/":
                block_comment = False
                output.extend("  ")
                index += 2
                continue
            output.append("\n" if character == "\n" else " ")
            index += 1
            continue
        if in_string:
            output.append(character)
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            index += 1
            continue
        if character == '"':
            in_string = True
            output.append(character)
            index += 1
            continue
        if character == "#" or (character == "/" and following == "/"):
            line_comment = True
            output.append(" ")
            index += 1 if character == "#" else 2
            continue
        if character == "/" and following == "*":
            block_comment = True
            output.extend("  ")
            index += 2
            continue
        output.append(character)
        index += 1
    if block_comment:
        raise ReleaseContractError("Terraform block comment is unclosed")
    return "".join(output)


def _block(source: str, header: str, *, start: int = 0) -> str:
    match = re.search(rf"(?m)^\s*{re.escape(header)}\s*\{{", source[start:])
    if match is None:
        raise ReleaseContractError(f"Terraform block is missing: {header}")
    opening = start + match.end() - 1
    depth = 0
    in_string = False
    escaped = False
    for index in range(opening, len(source)):
        character = source[index]
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
        elif character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                return source[opening + 1 : index]
    raise ReleaseContractError(f"Terraform block is unclosed: {header}")


def _all_blocks(source: str, header: str) -> tuple[str, ...]:
    results: list[str] = []
    cursor = 0
    pattern = re.compile(rf"(?m)^\s*{re.escape(header)}\s*\{{")
    while (match := pattern.search(source, cursor)) is not None:
        absolute = match.start()
        body = _block(source, header, start=absolute)
        results.append(body)
        cursor = absolute + match.end() - match.start() + len(body) + 2
    return tuple(results)


def _require_assignment(source: str, name: str, value_pattern: str, error: str) -> None:
    assignments = re.findall(
        rf"(?m)^[ \t]*{re.escape(name)}[ \t]*=[ \t]*(.+?)[ \t]*$",
        source,
    )
    if len(assignments) != 1 or re.fullmatch(value_pattern, assignments[0]) is None:
        raise ReleaseContractError(error)


def validate_terraform_target_architecture(
    target_vars: str,
    compute: str,
) -> str:
    """Return the declared CPU architecture, proving it reaches every task.

    An image built for the other architecture is accepted by ECS and then dies
    on start with nothing useful in its log, so the pipeline derives what it
    builds from this value.  Reading ``task_cpu_architecture`` alone would not
    be enough: the check also proves that every task definition's
    ``runtime_platform`` is bound to that variable, so a stack cannot declare
    one architecture and register another.
    """

    _require_assignment(
        target_vars,
        "task_cpu_architecture",
        f'"{re.escape(SELECTED_TARGET.task_cpu_architecture)}"',
        "deployment target task CPU architecture differs",
    )
    platforms = _all_blocks(compute, "runtime_platform")
    if len(platforms) != TASK_DEFINITION_COUNT:
        raise ReleaseContractError("every workload task definition must declare a runtime platform")
    for platform in platforms:
        _require_assignment(
            platform,
            "cpu_architecture",
            r"var\.task_cpu_architecture",
            "task runtime platform is detached from task_cpu_architecture",
        )
        _require_assignment(
            platform,
            "operating_system_family",
            '"LINUX"',
            "task runtime platform is not Linux",
        )
    return SELECTED_TARGET.task_cpu_architecture


def validate_terraform_seo_source(
    sources: dict[str, str],
    *,
    commit: str,
) -> TerraformSeoEvidence:
    validate_source_sha(commit)
    if set(sources) != set(REQUIRED_TERRAFORM_PATHS):
        raise ReleaseContractError("Terraform source set is incomplete")
    compute = _strip_comments(sources["modules/django-website/compute.tf"])
    edge = _strip_comments(sources["modules/django-website/edge.tf"])
    target_vars = _strip_comments(sources[TARGET_TERRAFORM_VARS_PATH])
    production = _strip_comments(sources["tests/fixtures/website-production/main.tf"])

    task_cpu_architecture = validate_terraform_target_architecture(target_vars, compute)
    _require_assignment(
        target_vars,
        "hostname",
        f'"{re.escape(SELECTED_TARGET.hostname)}"',
        "deployment target viewer host differs",
    )
    _require_assignment(
        target_vars,
        "robots_header_value",
        '"noindex, nofollow"' if SELECTED_TARGET.robots_noindex else "null",
        "deployment target robots value differs",
    )
    distributions = re.findall(
        r'(?m)^\s*resource\s+"aws_cloudfront_distribution"\s+"[^"\n]+"\s*\{',
        edge,
    )
    if len(distributions) != 1:
        raise ReleaseContractError("Terraform must contain exactly one CloudFront distribution")
    distribution = _block(edge, 'resource "aws_cloudfront_distribution" "this"')
    _require_assignment(distribution, "aliases", r"\[var\.hostname\]", "viewer aliases differ")

    response_policy = _block(
        edge,
        'resource "aws_cloudfront_response_headers_policy" "security"',
    )
    custom = _block(response_policy, 'dynamic "custom_headers_config"')
    _require_assignment(
        custom,
        "for_each",
        r"var\.robots_header_value\s*==\s*null\s*\?\s*\[\]\s*:\s*\[var\.robots_header_value\]",
        "edge robots header conditional differs",
    )
    items = _block(custom, "items")
    _require_assignment(items, "header", '"X-Robots-Tag"', "edge robots header differs")
    _require_assignment(items, "override", "true", "edge robots header does not override")
    _require_assignment(
        items,
        "value",
        r"custom_headers_config\.value",
        "edge robots value is detached",
    )
    if "cache_control" in response_policy.casefold():
        raise ReleaseContractError("edge response policy overrides application Cache-Control")

    disabled_cache = _block(edge, 'resource "aws_cloudfront_cache_policy" "disabled"')
    for ttl in ("min_ttl", "default_ttl", "max_ttl"):
        _require_assignment(disabled_cache, ttl, "0", f"{ttl} is not zero")
    origin_policy = _block(
        edge,
        'resource "aws_cloudfront_origin_request_policy" "all_viewer"',
    )
    forwarding = {
        "cookies_config": ("cookie_behavior", '"all"'),
        "headers_config": ("header_behavior", '"allViewer"'),
        "query_strings_config": ("query_string_behavior", '"all"'),
    }
    for block_name, (field, value) in forwarding.items():
        _require_assignment(
            _block(origin_policy, block_name),
            field,
            value,
            f"{field} is not forwarded",
        )

    behaviors = _all_blocks(distribution, "default_cache_behavior") + _all_blocks(
        distribution, "ordered_cache_behavior"
    )
    if not behaviors:
        raise ReleaseContractError("CloudFront has no cache behavior")
    for behavior in behaviors:
        _require_assignment(
            behavior,
            "cache_policy_id",
            r"aws_cloudfront_cache_policy\.disabled\.id",
            "cache behavior is not zero TTL",
        )
        _require_assignment(
            behavior,
            "origin_request_policy_id",
            r"aws_cloudfront_origin_request_policy\.all_viewer\.id",
            "cache behavior does not forward viewer context",
        )
        _require_assignment(
            behavior,
            "response_headers_policy_id",
            r"aws_cloudfront_response_headers_policy\.security\.id",
            "cache behavior lacks robots response policy",
        )

    _require_assignment(
        production,
        "robots_header_value",
        "null",
        "production policy fixture enables the non-indexable robots header",
    )
    return TerraformSeoEvidence(
        commit=commit,
        cache_behavior_count=len(behaviors),
        task_cpu_architecture=task_cpu_architecture,
    )


def verify_trusted_terraform_source(
    repository: Path,
    *,
    revision: str,
    expected_commit: str,
) -> TerraformSeoEvidence:
    commit, sources = read_trusted_terraform_source(
        repository,
        revision=revision,
        expected_commit=expected_commit,
    )
    return validate_terraform_seo_source(sources, commit=commit)
