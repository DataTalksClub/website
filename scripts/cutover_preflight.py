"""Read-only apex preflight. Exit 1 means blocked; this tool never flips traffic."""

import argparse
import json
import math
import re
import shlex
import subprocess
from datetime import UTC, date, datetime
from pathlib import Path

DISTRIBUTION = "E1VNMGFFIXBOXX"
APEX_HOSTS = {"datatalks.club", "www.datatalks.club"}
READ_ACTIONS = {
    ("sts", "get-caller-identity"),
    ("cloudfront", "get-distribution-config"),
    ("cloudfront", "get-origin-request-policy"),
    ("cloudfront", "get-response-headers-policy"),
    ("acm", "describe-certificate"),
}
REMOTE_PROBE = """from django.conf import settings
from django.test import Client, override_settings
import json
result={
 'source_sha':settings.SOURCE_SHA,'version':settings.VERSION,
 'allowed_hosts':settings.ALLOWED_HOSTS,'noindex':settings.NOINDEX,
 'apex_cutover_enabled':getattr(settings,'APEX_CUTOVER_ENABLED',None),'probes':[]}
def probe(mode,host,path):
 r=Client().get(path,HTTP_HOST=host,secure=True)
 result['probes'].append({
  'mode':mode,'host':host,'path':path,'status':r.status_code,
  'robots':r.get('X-Robots-Tag'),'location':r.get('Location'),
  'disallow_all':b'Disallow: /\\n' in r.content if path=='/robots.txt' else None})
for host in ['prod.datatalks.club','datatalks.club']:
 for path in ['/courses','/robots.txt']: probe('off',host,path)
probe('off','www.datatalks.club','/courses?source=preflight')
with override_settings(APEX_CUTOVER_ENABLED=True):
 for host in ['prod.datatalks.club','datatalks.club']:
  for path in ['/courses','/robots.txt','/accounts/login/','/__cutover_missing__']:
   probe('simulated_on',host,path)
print('PREFLIGHT_JSON='+json.dumps(result))
"""


def command(args, **kwargs):
    result = subprocess.run(args, capture_output=True, text=True, timeout=90, **kwargs)
    if result.returncode:
        reason = (
            "AWS Gate closed (HTTP403)" if "Gate closed" in result.stderr else "Read command failed"
        )
        raise RuntimeError(reason)
    return result.stdout


def aws(*args):
    if tuple(args[:2]) not in READ_ACTIONS:
        raise ValueError("Only allowlisted AWS reads are permitted")
    return json.loads(command(["aws", "--profile", "main", *args, "--output", "json"]))


def forwards_viewer_host(headers):
    behavior = headers.get("HeaderBehavior")
    names = {value.lower() for value in headers.get("Headers", {}).get("Items", [])}
    return (
        behavior in {"allViewer", "allViewerAndWhitelistCloudFront"}
        or (behavior == "whitelist" and "host" in names)
        or (behavior == "allExcept" and "host" not in names)
    )


def certificate_covers_apex(names):
    names = {name.lower() for name in names}
    return "datatalks.club" in names and bool({"www.datatalks.club", "*.datatalks.club"} & names)


def indexing_ready(document):
    def probe(mode, host, path):
        return next(
            (
                p
                for p in document["probes"]
                if (p["mode"], p["host"], p["path"]) == (mode, host, path)
            ),
            {},
        )

    def restricted(p):
        return p.get("status") == 200 and "noindex" in (p.get("robots") or "").lower()

    if document.get("noindex") is not True or document.get("apex_cutover_enabled") is not False:
        return False
    for host in ["prod.datatalks.club", "datatalks.club"]:
        if not restricted(probe("off", host, "/courses")):
            return False
        if not probe("off", host, "/robots.txt").get("disallow_all"):
            return False
    for path in ["/courses", "/robots.txt"]:
        apex = probe("simulated_on", "datatalks.club", path)
        if apex.get("status") != 200 or apex.get("robots"):
            return False
        if path == "/robots.txt" and apex.get("disallow_all") is not False:
            return False
        if not restricted(probe("simulated_on", "prod.datatalks.club", path)):
            return False
    if not probe("simulated_on", "prod.datatalks.club", "/robots.txt").get("disallow_all"):
        return False
    for path in ["/accounts/login/", "/__cutover_missing__"]:
        if "noindex" not in (probe("simulated_on", "datatalks.club", path).get("robots") or ""):
            return False
    return True


def baseline_ready(document):
    try:
        start, end = (
            date.fromisoformat(document["startDate"]),
            date.fromisoformat(document["endDate"]),
        )
        rows = document["rows"]
        return (
            document["property"] in {"sc-domain:datatalks.club", "https://datatalks.club/"}
            and document["dataState"] == "final"
            and not document.get("dimensions")
            and (end - start).days == 27
            and 0 <= (datetime.now(UTC).date() - end).days <= 7
            and isinstance(rows, list)
            and len(rows) == 1
            and set(rows[0]) == {"clicks", "impressions", "ctr", "position"}
            and all(
                isinstance(rows[0][key], (int, float))
                and not isinstance(rows[0][key], bool)
                and math.isfinite(rows[0][key])
                and rows[0][key] >= 0
                for key in ["clicks", "impressions", "ctr", "position"]
            )
        )
    except (KeyError, TypeError, ValueError, IndexError):
        return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gsc-baseline", type=Path)
    parser.add_argument("--expected-source-sha")
    args = parser.parse_args()
    checks = []
    source_sha = None

    def check(name, passed, expected, observed):
        checks.append(
            {
                "name": name,
                "status": "pass" if passed else "blocked",
                "expected": expected,
                "observed": observed,
            }
        )

    def failed(name, error):
        reason = str(error) if isinstance(error, RuntimeError) else type(error).__name__
        check(name, False, "Successful bounded read with valid evidence", reason)

    try:
        identity = aws("sts", "get-caller-identity")
        if identity["Account"] != "387546586013":
            raise RuntimeError("AWS account is not MAIN")
        config = aws("cloudfront", "get-distribution-config", "--id", DISTRIBUTION)[
            "DistributionConfig"
        ]
        arn = config["ViewerCertificate"]["ACMCertificateArn"]
        cert = aws(
            "acm", "describe-certificate", "--region", "us-east-1", "--certificate-arn", arn
        )["Certificate"]
        check(
            "ACM SANs",
            cert["Status"] == "ISSUED" and certificate_covers_apex(cert["SubjectAlternativeNames"]),
            "Issued certificate covers apex and www",
            {"arn": arn, "status": cert["Status"], "sans": cert["SubjectAlternativeNames"]},
        )
        policy_id = config["DefaultCacheBehavior"].get("OriginRequestPolicyId")
        policy = (
            aws("cloudfront", "get-origin-request-policy", "--id", policy_id)[
                "OriginRequestPolicy"
            ]["OriginRequestPolicyConfig"]
            if policy_id
            else {}
        )
        check(
            "origin Host policy",
            forwards_viewer_host(policy.get("HeadersConfig", {})),
            "Viewer Host reaches host-scoped application policy",
            {"policy_id": policy_id, "headers": policy.get("HeadersConfig")},
        )
        headers_id = config["DefaultCacheBehavior"].get("ResponseHeadersPolicyId")
        headers = (
            aws("cloudfront", "get-response-headers-policy", "--id", headers_id)[
                "ResponseHeadersPolicy"
            ]["ResponseHeadersPolicyConfig"]
            if headers_id
            else {}
        )
        injected = [
            h["Header"].lower() for h in headers.get("CustomHeadersConfig", {}).get("Items", [])
        ]
        removed = [
            h["Header"].lower() for h in headers.get("RemoveHeadersConfig", {}).get("Items", [])
        ]
        check(
            "edge apex indexing policy",
            "x-robots-tag" not in injected and "x-robots-tag" not in removed,
            "Origin robots headers preserved without unconditional edge injection",
            {
                "policy_id": headers_id,
                "injected_header_names": injected,
                "removed_header_names": removed,
            },
        )
    except (
        RuntimeError,
        OSError,
        subprocess.SubprocessError,
        ValueError,
        KeyError,
        TypeError,
    ) as error:
        failed("AWS ACM and origin policy", error)
    try:
        names = command(
            ["ssh", "-o", "BatchMode=yes", "contabo-a", 'sudo docker ps --format "{{.Names}}"']
        ).splitlines()
        web = next(
            (n for n in names if re.fullmatch(r"nax9srpks6z7w1jgpebiuwuo-[A-Za-z0-9]+", n)), None
        )
        if not web:
            raise RuntimeError("No production web container found")
        output = command(
            [
                "ssh",
                "contabo-a",
                "sudo docker exec -i " + shlex.quote(web) + " python manage.py shell",
            ],
            input=REMOTE_PROBE,
        )
        document = json.loads(
            next(
                row.split("=", 1)[1]
                for row in output.splitlines()
                if row.startswith("PREFLIGHT_JSON=")
            )
        )
        source_sha = document["source_sha"]
        expected = args.expected_source_sha
        check(
            "source identity",
            bool(expected and re.fullmatch(r"[a-f0-9]{40}", expected) and source_sha == expected),
            expected,
            source_sha,
        )
        check(
            "ALLOWED_HOSTS",
            APEX_HOSTS <= set(document["allowed_hosts"]),
            sorted(APEX_HOSTS),
            document["allowed_hosts"],
        )
        check(
            "apex-only robots switch",
            indexing_ready(document),
            "Default off; simulated opt-in indexes only public apex successes",
            document,
        )
        www = next(p for p in document["probes"] if p["host"] == "www.datatalks.club")
        check(
            "www to apex 301",
            www["status"] == 301
            and www["location"] == "https://datatalks.club/courses?source=preflight",
            "301 preserving ordinary query",
            www,
        )
        labels = json.loads(
            command(
                [
                    "ssh",
                    "contabo-a",
                    "sudo docker inspect "
                    + shlex.quote(web)
                    + ' --format "{{json .Config.Labels}}"',
                ]
            )
        )
        rules = [
            v
            for k, v in labels.items()
            if k.startswith("traefik.http.routers.") and k.endswith(".rule")
        ]
        check(
            "Traefik apex/www rules",
            all(any("Host(`" + h + "`)" in rule for rule in rules) for h in APEX_HOSTS),
            sorted(APEX_HOSTS),
            rules,
        )
    except (
        RuntimeError,
        OSError,
        subprocess.SubprocessError,
        ValueError,
        KeyError,
        TypeError,
        StopIteration,
    ) as error:
        failed("origin application probes", error)
    try:
        if not args.gsc_baseline:
            raise RuntimeError("No GSC baseline supplied")
        document = json.loads(args.gsc_baseline.read_text())
        valid = baseline_ready(document)
        check(
            "GSC 28-day baseline",
            valid,
            "Recent 28 complete days, final site aggregate with metrics",
            (
                {
                    key: document[key]
                    for key in ["property", "startDate", "endDate", "dataState", "rows"]
                }
                if valid
                else "Invalid aggregate baseline; content withheld"
            ),
        )
    except (RuntimeError, OSError, ValueError, TypeError, AttributeError) as error:
        failed("GSC 28-day baseline", error)
    print(
        json.dumps(
            {
                "read_only": True,
                "collected_at": datetime.now(UTC).isoformat(),
                "distribution": DISTRIBUTION,
                "source_sha": source_sha,
                "checks": checks,
            },
            indent=2,
        )
    )
    return 0 if all(c["status"] == "pass" for c in checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
