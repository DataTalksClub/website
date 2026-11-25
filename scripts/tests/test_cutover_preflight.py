import contextlib
import copy
import io
import json
import subprocess
import unittest
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import patch

from scripts import cutover_preflight as preflight


def good_document() -> dict[str, Any]:
    document: dict[str, Any] = {
        "source_sha": "a" * 40,
        "version": "test",
        "noindex": True,
        "apex_cutover_enabled": False,
        "allowed_hosts": sorted(preflight.APEX_HOSTS),
        "probes": [],
    }
    for mode in ["off", "simulated_on"]:
        for host in ["datatalks.club", "prod.datatalks.club"]:
            for path in ["/courses", "/robots.txt", "/accounts/login/", "/__cutover_missing__"]:
                indexed = (
                    mode == "simulated_on"
                    and host == "datatalks.club"
                    and path in {"/courses", "/robots.txt"}
                )
                document["probes"].append(
                    {
                        "mode": mode,
                        "host": host,
                        "path": path,
                        "status": 404 if path == "/__cutover_missing__" else 200,
                        "robots": None if indexed else "noindex, nofollow",
                        "disallow_all": not indexed if path == "/robots.txt" else None,
                        "location": None,
                    }
                )
    document["probes"].append(
        {
            "mode": "off",
            "host": "www.datatalks.club",
            "path": "/courses?source=preflight",
            "status": 301,
            "location": "https://datatalks.club/courses?source=preflight",
        }
    )
    return document


def good_baseline() -> dict[str, Any]:
    end = datetime.now(UTC).date() - timedelta(days=3)
    return {
        "property": "sc-domain:datatalks.club",
        "dataState": "final",
        "startDate": (end - timedelta(days=27)).isoformat(),
        "endDate": end.isoformat(),
        "rows": [{"clicks": 3, "impressions": 20, "ctr": 0.15, "position": 5}],
    }


class CutoverPreflightTests(unittest.TestCase):
    def test_certificate_wildcard_covers_www_but_not_the_apex(self):
        self.assertTrue(preflight.certificate_covers_apex(["datatalks.club", "www.datatalks.club"]))
        self.assertTrue(preflight.certificate_covers_apex(["datatalks.club", "*.datatalks.club"]))
        self.assertFalse(preflight.certificate_covers_apex(["*.datatalks.club"]))

    def test_aws_mutations_are_rejected_before_execution(self):
        with patch.object(preflight, "command") as command:
            with self.assertRaises(ValueError):
                preflight.aws("cloudfront", "associate-alias")
            command.assert_not_called()

    def test_host_exclusion_cannot_pass_host_scoped_indexing(self):
        self.assertFalse(
            preflight.forwards_viewer_host(
                {
                    "HeaderBehavior": "allExcept",
                    "Headers": {"Items": ["Host"]},
                }
            )
        )
        self.assertFalse(preflight.forwards_viewer_host({"HeaderBehavior": "none"}))
        self.assertTrue(preflight.forwards_viewer_host({"HeaderBehavior": "allViewer"}))

    def test_command_failure_does_not_leak_stderr(self):
        result = subprocess.CompletedProcess([], 1, "", "Gate closed PRIVATE_SENTINEL")
        with patch.object(subprocess, "run", return_value=result):
            with self.assertRaisesRegex(RuntimeError, r"^AWS Gate closed \(HTTP403\)$"):
                preflight.command(["aws"])

    def test_switch_requires_off_default_and_restrictive_prod(self):
        self.assertTrue(preflight.indexing_ready(good_document()))
        for key, value in [("noindex", False), ("apex_cutover_enabled", True)]:
            document = good_document()
            document[key] = value
            self.assertFalse(preflight.indexing_ready(document))
        document = good_document()
        for probe in document["probes"]:
            if probe["host"] == "prod.datatalks.club":
                probe.update(robots=None, disallow_all=False)
        self.assertFalse(preflight.indexing_ready(document))

    def test_empty_stale_incomplete_or_nonnumeric_baseline_is_blocked(self):
        self.assertTrue(preflight.baseline_ready(good_baseline()))
        updates: list[dict[str, Any]] = [
            {"rows": []},
            {"dataState": "all"},
            {"endDate": "2020-01-01"},
            {"rows": [{"clicks": "unknown"}]},
        ]
        for update in updates:
            self.assertFalse(preflight.baseline_ready({**good_baseline(), **update}))

    def run_main(
        self,
        *,
        document=None,
        baseline=None,
        malformed_ssh=False,
        gate_closed=False,
        edge_policy=None,
    ):
        document = good_document() if document is None else document
        baseline = good_baseline() if baseline is None else baseline

        def aws(*args):
            if gate_closed:
                raise RuntimeError("AWS Gate closed (HTTP403)")
            return {
                "get-caller-identity": {"Account": "387546586013"},
                "get-distribution-config": {
                    "DistributionConfig": {
                        "ViewerCertificate": {"ACMCertificateArn": "certificate"},
                        "DefaultCacheBehavior": {
                            "OriginRequestPolicyId": "all-viewer",
                            **({"ResponseHeadersPolicyId": "edge"} if edge_policy else {}),
                        },
                    }
                },
                "get-response-headers-policy": {
                    "ResponseHeadersPolicy": {
                        "ResponseHeadersPolicyConfig": edge_policy,
                    }
                },
                "describe-certificate": {
                    "Certificate": {
                        "Status": "ISSUED",
                        "SubjectAlternativeNames": sorted(preflight.APEX_HOSTS),
                    }
                },
                "get-origin-request-policy": {
                    "OriginRequestPolicy": {
                        "OriginRequestPolicyConfig": {
                            "HeadersConfig": {"HeaderBehavior": "allViewer"}
                        }
                    }
                },
            }[args[1]]

        def command(args, **kwargs):
            if "docker ps" in args[-1]:
                return "nax9srpks6z7w1jgpebiuwuo-20261125T000000\n"
            if "docker inspect" in args[-1]:
                return json.dumps(
                    {
                        "traefik.http.routers.apex.rule": "Host(`datatalks.club`)",
                        "traefik.http.routers.www.rule": "Host(`www.datatalks.club`)",
                    }
                )
            return "PREFLIGHT_JSON=" + ("broken" if malformed_ssh else json.dumps(document))

        output = io.StringIO()
        with (
            patch.object(preflight, "aws", side_effect=aws),
            patch.object(preflight, "command", side_effect=command),
            patch("pathlib.Path.read_text", return_value=json.dumps(baseline)),
            patch(
                "sys.argv",
                ["preflight", "--expected-source-sha", "a" * 40, "--gsc-baseline", "baseline.json"],
            ),
            contextlib.redirect_stdout(output),
        ):
            status = preflight.main()
        return status, json.loads(output.getvalue())

    def test_complete_good_fixture_passes_with_identity_and_timestamp(self):
        status, report = self.run_main()
        self.assertEqual(status, 0)
        self.assertEqual(report["source_sha"], "a" * 40)
        self.assertIsNotNone(datetime.fromisoformat(report["collected_at"]).tzinfo)
        self.assertTrue(
            all("expected" in check and "observed" in check for check in report["checks"])
        )
        self.assertIn("edge apex indexing policy", [check["name"] for check in report["checks"]])

    def test_known_unsafe_complete_fixture_cannot_pass(self):
        document = copy.deepcopy(good_document())
        document.update(noindex=False, apex_cutover_enabled=True)
        status, _ = self.run_main(document=document, baseline={**good_baseline(), "rows": []})
        self.assertEqual(status, 1)

    def test_baseline_never_echoes_unreviewed_private_row_data(self):
        baseline = good_baseline()
        baseline["rows"][0]["keys"] = ["PRIVATE_SENTINEL"]
        status, report = self.run_main(baseline=baseline)
        self.assertEqual(status, 1)
        self.assertNotIn("PRIVATE_SENTINEL", json.dumps(report))
        baseline = good_baseline()
        baseline["rows"][0]["clicks"] = True
        self.assertFalse(preflight.baseline_ready(baseline))

    def test_gate_or_malformed_ssh_returns_structured_blocker(self):
        for options in [{"gate_closed": True}, {"malformed_ssh": True}]:
            status, report = self.run_main(**options)
            self.assertEqual(status, 1)
            self.assertTrue(any(check["status"] == "blocked" for check in report["checks"]))

    def test_edge_cannot_strip_origin_robots_or_leak_header_values(self):
        for section in ("CustomHeadersConfig", "RemoveHeadersConfig"):
            status, report = self.run_main(
                edge_policy={
                    section: {
                        "Items": [{"Header": "X-Robots-Tag", "Value": "PRIVATE_SENTINEL"}],
                    }
                }
            )
            self.assertEqual(status, 1)
            self.assertNotIn("PRIVATE_SENTINEL", json.dumps(report))
