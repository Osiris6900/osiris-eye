#!/usr/bin/env python3
"""Query OSV for every exact locked Cargo/npm package and retain a JSON report.

When the hashed Python deployment lock (`services/quant-api/requirements-lock.txt`)
is supplied, its exact PyPI pins are queried too and the report records them as
scanned. Without the lock, Python version ranges are reported as unresolved and
are never represented as a scanned deployment environment.

OSV matches are advisory evidence, not reachability analysis or an independent
security audit.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import time
import tomllib
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from generate_dependency_sbom import cargo_packages, npm_packages
from python_lock import LOCK_RELATIVE_PATH, parse_hashed_lock

SCHEMA = "osiris.dependency-advisory-report.v1"
MAINTENANCE_SCHEMA = "osiris.osv-maintenance-only.v1"
API_BASE = "https://api.osv.dev/v1"
USER_AGENT = "osiris-eye-dependency-advisory-scan (https://github.com/Osiris6900/osiris-eye)"
COMMIT_RE = re.compile(r"[0-9a-f]{40}\Z")
ADDITIONAL_CARGO_LOCKS = ("crates/native-feed/Cargo.lock", "crates/risk-kernel/Cargo.lock")


def cargo_lock_inputs(root: Path) -> dict[str, Path]:
    return {name: root / name for name in ADDITIONAL_CARGO_LOCKS}


def input_files(cargo_lock, npm_lock, pyproject, python_lock, additional_cargo_locks):
    files = {
        "services/p2p-node/Cargo.lock": cargo_lock,
        "apps/web/package-lock.json": npm_lock,
        "services/quant-api/pyproject.toml": pyproject,
    }
    if python_lock is not None:
        files[LOCK_RELATIVE_PATH] = python_lock
    for relative, path in (additional_cargo_locks or {}).items():
        if (not isinstance(relative, str) or not relative.strip()
                or Path(relative).is_absolute() or ".." in Path(relative).parts
                or relative in files or relative == LOCK_RELATIVE_PATH):
            raise ValueError("invalid additional Cargo lock identity")
        files[relative] = path
    return files


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def locked_packages(
    cargo_lock: Path,
    npm_lock: Path,
    python_lock: Path | None = None,
    additional_cargo_locks: dict[str, Path] | None = None,
) -> list[dict[str, str]]:
    by_crate = {}
    for lock in [cargo_lock, *(additional_cargo_locks or {}).values()]:
        _, records = cargo_packages(lock)
        for record in records:
            key = record["name"], record["version"]
            if key in by_crate and by_crate[key]["checksum"] != record["checksum"]:
                raise ValueError(f"conflicting Cargo checksum for {key[0]}@{key[1]}")
            by_crate[key] = record
    crates = list(by_crate.values())
    _, locked_npm = npm_packages(npm_lock)
    packages = [
        {"ecosystem": "crates.io", "name": package["name"], "version": package["version"]}
        for package in crates
    ] + [
        {"ecosystem": "npm", "name": name, "version": version}
        for _, name, version, _ in locked_npm
    ]
    if python_lock is not None:
        packages += [
            {"ecosystem": "PyPI", "name": entry.normalized_name, "version": entry.version}
            for entry in parse_hashed_lock(python_lock)
        ]
    unique = {(p["ecosystem"], p["name"], p["version"]): p for p in packages}
    return [unique[key] for key in sorted(unique)]


def load_maintenance_only(path: Path) -> dict[str, dict[str, str]]:
    document = json.loads(path.read_text())
    if not isinstance(document, dict) or document.get("schema") != MAINTENANCE_SCHEMA:
        raise ValueError(f"{path}: unsupported maintenance-only advisory configuration")
    advisories = document.get("advisories")
    if not isinstance(advisories, dict):
        raise ValueError(f"{path}: advisories must be a mapping")
    for advisory_id, metadata in advisories.items():
        if not isinstance(advisory_id, str) or not advisory_id:
            raise ValueError(f"{path}: advisory IDs must be non-empty strings")
        if not isinstance(metadata, dict) or not isinstance(metadata.get("reason"), str) \
                or not metadata["reason"].strip():
            raise ValueError(f"{path}: {advisory_id} needs a non-empty reason")
    return advisories


class OsvClient:
    def __init__(self, opener: Callable | None = None, retries: int = 3) -> None:
        self.opener = opener or urlopen
        self.retries = retries

    def _json(self, url: str, payload: dict | None = None) -> Any:
        data = None if payload is None else json.dumps(payload, separators=(",", ":")).encode()
        request = Request(
            url,
            data=data,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": USER_AGENT,
            },
        )
        for attempt in range(self.retries):
            try:
                with self.opener(request, timeout=45) as response:
                    return json.load(response)
            except HTTPError as error:
                retryable = error.code == 429 or 500 <= error.code < 600
                if not retryable or attempt + 1 == self.retries:
                    raise ValueError(f"OSV request failed with HTTP {error.code}: {url}") from error
            except (URLError, TimeoutError) as error:
                if attempt + 1 == self.retries:
                    raise ValueError(f"OSV request failed: {url}: {error}") from error
            time.sleep(0.25 * (2 ** attempt))
        raise AssertionError("unreachable")

    def querybatch(self, packages: list[dict[str, str]], batch_size: int = 100) -> list[tuple[dict[str, str], str]]:
        if not 1 <= batch_size <= 1000:
            raise ValueError("batch size must be between 1 and 1000")
        matches: list[tuple[dict[str, str], str]] = []
        for start in range(0, len(packages), batch_size):
            batch = packages[start:start + batch_size]
            queries = [
                {"package": {"ecosystem": p["ecosystem"], "name": p["name"]}, "version": p["version"]}
                for p in batch
            ]
            document = self._json(f"{API_BASE}/querybatch", {"queries": queries})
            results = document.get("results") if isinstance(document, dict) else None
            if not isinstance(results, list) or len(results) != len(batch):
                raise ValueError("OSV querybatch returned a missing or misaligned result list")
            for package, result in zip(batch, results):
                seen, tokens = set(), set()
                while True:
                    if not isinstance(result, dict):
                        raise ValueError("OSV querybatch returned a non-object result")
                    vulnerabilities = result.get("vulns", [])
                    if not isinstance(vulnerabilities, list):
                        raise ValueError("OSV querybatch returned invalid vulnerabilities")
                    for vulnerability in vulnerabilities:
                        advisory_id = vulnerability.get("id") if isinstance(vulnerability, dict) else None
                        if not isinstance(advisory_id, str) or not advisory_id:
                            raise ValueError("OSV querybatch returned a vulnerability without an ID")
                        if advisory_id not in seen:
                            matches.append((package, advisory_id))
                            seen.add(advisory_id)
                    token = result.get("next_page_token", "")
                    if not isinstance(token, str):
                        raise ValueError("OSV returned an invalid page token")
                    if not token:
                        break
                    if token in tokens or len(tokens) >= 100:
                        raise ValueError("OSV pagination repeated or exceeded its bound")
                    tokens.add(token)
                    result = self._json(f"{API_BASE}/query", {
                        "package": {"ecosystem": package["ecosystem"], "name": package["name"]},
                        "version": package["version"], "page_token": token,
                    })
        return matches

    def vulnerability(self, advisory_id: str) -> dict:
        document = self._json(f"{API_BASE}/vulns/{quote(advisory_id, safe='')}")
        if not isinstance(document, dict) or document.get("id") != advisory_id:
            raise ValueError(f"OSV returned a mismatched advisory record for {advisory_id}")
        aliases = document.get("aliases", [])
        if not isinstance(aliases, list) or not all(isinstance(alias, str) and alias for alias in aliases):
            raise ValueError(f"OSV returned invalid aliases for {advisory_id}")
        return document


def _record_view(record: dict) -> dict:
    return {
        "id": record["id"],
        "url": f"https://osv.dev/vulnerability/{quote(record['id'], safe='')}",
        "aliases": sorted(set(record.get("aliases", []))),
        "summary": record.get("summary"),
        "published": record.get("published"),
        "modified": record.get("modified"),
        "severity": record.get("severity", []),
        "database_specific": record.get("database_specific", {}),
        "affected": record.get("affected", []),
    }


def group_matches(
    matches: list[tuple[dict[str, str], str]],
    records: dict[str, dict],
    maintenance_only: dict[str, dict[str, str]],
) -> list[dict]:
    by_package: dict[tuple[str, str, str], list[str]] = defaultdict(list)
    package_by_key = {}
    for package, advisory_id in matches:
        key = (package["ecosystem"], package["name"], package["version"])
        package_by_key[key] = package
        if advisory_id not in by_package[key]:
            by_package[key].append(advisory_id)

    findings = []
    for key in sorted(by_package):
        matched_ids = sorted(by_package[key])
        parent = {node: node for node in matched_ids}

        def find(node: str) -> str:
            parent.setdefault(node, node)
            while parent[node] != node:
                parent[node] = parent[parent[node]]
                node = parent[node]
            return node

        def union(left: str, right: str) -> None:
            left_root, right_root = find(left), find(right)
            if left_root != right_root:
                parent[right_root] = left_root

        for advisory_id in matched_ids:
            if advisory_id not in records:
                raise ValueError(f"missing OSV record for {advisory_id}")
            for alias in records[advisory_id].get("aliases", []):
                union(advisory_id, alias)

        components: dict[str, set[str]] = defaultdict(set)
        for node in parent:
            components[find(node)].add(node)
        matched_components: dict[str, list[str]] = defaultdict(list)
        for advisory_id in matched_ids:
            matched_components[find(advisory_id)].append(advisory_id)

        package_findings = []
        for root, group_matched_ids in matched_components.items():
            all_ids = sorted(components[root])
            maintenance_ids = sorted(set(all_ids).intersection(maintenance_only))
            classification = "maintenance-only" if maintenance_ids else "vulnerability"
            finding = {
                "ecosystem": key[0],
                "name": key[1],
                "version": key[2],
                "classification": classification,
                "ids": all_ids,
                "matched_ids": sorted(group_matched_ids),
                "records": [_record_view(records[advisory_id]) for advisory_id in sorted(group_matched_ids)],
            }
            if maintenance_ids:
                finding["maintenance_only_evidence"] = [
                    {"id": advisory_id, "reason": maintenance_only[advisory_id]["reason"]}
                    for advisory_id in maintenance_ids
                ]
            package_findings.append(finding)
        findings.extend(sorted(package_findings, key=lambda finding: finding["ids"]))
    return findings


def build_report(
    cargo_lock: Path,
    npm_lock: Path,
    pyproject: Path,
    maintenance_only: dict[str, dict[str, str]],
    source_commit: str,
    client: OsvClient,
    queried_at: str | None = None,
    batch_size: int = 100,
    python_lock: Path | None = None,
    additional_cargo_locks: dict[str, Path] | None = None,
) -> dict:
    if not COMMIT_RE.fullmatch(source_commit):
        raise ValueError("source commit must be a full lowercase 40-character Git SHA")
    files = input_files(cargo_lock, npm_lock, pyproject, python_lock, additional_cargo_locks)
    inputs = {name: {"sha256": sha256_file(path)} for name, path in files.items()}
    packages = locked_packages(cargo_lock, npm_lock, python_lock, additional_cargo_locks)
    matches = client.querybatch(packages, batch_size)
    record_ids = sorted({advisory_id for _, advisory_id in matches})
    records = {advisory_id: client.vulnerability(advisory_id) for advisory_id in record_ids}
    findings = group_matches(matches, records, maintenance_only)
    timestamp = queried_at or datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    counts = defaultdict(int)
    for package in packages:
        counts[package["ecosystem"]] += 1
    python_project = tomllib.loads(pyproject.read_text()).get("project", {})
    if python_lock is not None:
        python_coverage = {
            "status": "scanned",
            "packages": counts["PyPI"],
            "lock": LOCK_RELATIVE_PATH,
            "declared_requirements": python_project.get("dependencies", []),
        }
        python_limitation = (
            "The Python scan covers only the exact pins in the hashed deployment lock; "
            "development and CI environments installed from pyproject ranges are outside its scope."
        )
    else:
        python_coverage = {
            "status": "unresolved",
            "packages": 0,
            "declared_requirements": python_project.get("dependencies", []),
            "reason": "pyproject.toml contains version ranges, not a supported resolved and hashed deployment lock; Python packages were not queried.",
        }
        python_limitation = "Python deployment dependencies remain unresolved and were not scanned."
    if inputs != {name: {"sha256": sha256_file(path)} for name, path in files.items()}:
        raise ValueError("dependency inputs changed during advisory scan")
    return {
        "schema": SCHEMA,
        "source": {
            "repository": "Osiris6900/osiris-eye",
            "commit": source_commit,
            "inputs": inputs,
        },
        "query": {
            "service": "OSV",
            "querybatch_url": f"{API_BASE}/querybatch",
            "vulnerability_url_template": f"{API_BASE}/vulns/{{id}}",
            "queried_at": timestamp,
            "batch_size": batch_size,
            "batches": (len(packages) + batch_size - 1) // batch_size,
        },
        "coverage": {
            "crates.io": {"status": "scanned", "packages": counts["crates.io"]},
            "npm": {"status": "scanned", "packages": counts["npm"]},
            "PyPI": python_coverage,
        },
        "summary": {
            "queried_packages": len(packages),
            "raw_advisory_matches": len(matches),
            "affected_packages": len({(f["ecosystem"], f["name"], f["version"]) for f in findings}),
            "advisory_groups": len(findings),
            "vulnerability_groups": sum(f["classification"] == "vulnerability" for f in findings),
            "maintenance_only_groups": sum(f["classification"] == "maintenance-only" for f in findings),
        },
        "findings": findings,
        "limitations": [
            "OSV matching is point-in-time advisory evidence, not reachability analysis, proof of exploitation, or an independent security audit.",
            "A package with no returned match is not proven free of vulnerabilities.",
            python_limitation,
            "This report does not complete license/redistribution, artifact-provenance, or reproducible-build review.",
        ],
    }


def validate_report(
    report: dict,
    cargo_lock: Path,
    npm_lock: Path,
    pyproject: Path,
    python_lock: Path | None = None,
    additional_cargo_locks: dict[str, Path] | None = None,
) -> None:
    if not isinstance(report, dict) or report.get("schema") != SCHEMA:
        raise ValueError("checked-in advisory report has an unsupported schema")
    source = report.get("source", {})
    if source.get("repository") != "Osiris6900/osiris-eye":
        raise ValueError("advisory report has an unexpected source repository")
    if not isinstance(source.get("commit"), str) or not COMMIT_RE.fullmatch(source["commit"]):
        raise ValueError("advisory report source commit must be a full lowercase Git SHA")

    packages = locked_packages(cargo_lock, npm_lock, python_lock, additional_cargo_locks)
    package_keys = {(p["ecosystem"], p["name"], p["version"]) for p in packages}
    expected_counts = defaultdict(int)
    for package in packages:
        expected_counts[package["ecosystem"]] += 1
    files = input_files(cargo_lock, npm_lock, pyproject, python_lock, additional_cargo_locks)
    expected_hashes = {name: sha256_file(path) for name, path in files.items()}
    inputs = source.get("inputs", {})
    for path, digest in expected_hashes.items():
        if inputs.get(path, {}).get("sha256") != digest:
            raise ValueError(f"advisory report is stale for {path}; regenerate it")
    if set(inputs) != set(files):
        raise ValueError("advisory report input inventory does not match required locks")

    coverage = report.get("coverage", {})
    for ecosystem in ("crates.io", "npm"):
        ecosystem_coverage = coverage.get(ecosystem, {})
        if ecosystem_coverage.get("status") != "scanned" \
                or ecosystem_coverage.get("packages") != expected_counts[ecosystem]:
            raise ValueError(f"advisory report package coverage is stale for {ecosystem}")
    python_coverage = coverage.get("PyPI", {})
    python_project = tomllib.loads(pyproject.read_text()).get("project", {})
    if python_lock is None:
        if python_coverage.get("status") != "unresolved" or python_coverage.get("packages") != 0:
            raise ValueError("Python resolution status must remain explicit and unscanned")
    else:
        if python_coverage.get("status") != "scanned" \
                or python_coverage.get("packages") != expected_counts["PyPI"]:
            raise ValueError("advisory report package coverage is stale for PyPI")
        if python_coverage.get("lock") != LOCK_RELATIVE_PATH:
            raise ValueError("advisory report Python lock path is unexpected")
    if python_coverage.get("declared_requirements") != python_project.get("dependencies", []):
        raise ValueError("advisory report Python declarations are stale")

    query = report.get("query", {})
    timestamp = query.get("queried_at")
    try:
        parsed_timestamp = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as error:
        raise ValueError("advisory report query timestamp is invalid") from error
    if parsed_timestamp.tzinfo is None or parsed_timestamp.utcoffset() is None:
        raise ValueError("advisory report query timestamp must include a timezone")
    batch_size = query.get("batch_size")
    if type(batch_size) is not int or not 1 <= batch_size <= 1000:
        raise ValueError("advisory report batch size is invalid")
    if query.get("batches") != (len(packages) + batch_size - 1) // batch_size:
        raise ValueError("advisory report batch count is inconsistent")

    findings = report.get("findings")
    if not isinstance(findings, list):
        raise ValueError("advisory report findings must be a list")
    affected_packages = set()
    raw_matches = 0
    classifications = defaultdict(int)
    for finding in findings:
        if not isinstance(finding, dict):
            raise ValueError("advisory report contains a non-object finding")
        key = (finding.get("ecosystem"), finding.get("name"), finding.get("version"))
        if key not in package_keys:
            raise ValueError(f"advisory report finding is not in the locked inputs: {key}")
        affected_packages.add(key)
        classification = finding.get("classification")
        if classification not in ("vulnerability", "maintenance-only"):
            raise ValueError("advisory report finding has an invalid classification")
        classifications[classification] += 1
        ids = finding.get("ids")
        matched_ids = finding.get("matched_ids")
        if not isinstance(ids, list) or not ids \
                or not all(isinstance(value, str) and value for value in ids) \
                or ids != sorted(set(ids)):
            raise ValueError("advisory report finding IDs must be sorted, unique and non-empty")
        if not isinstance(matched_ids, list) or not matched_ids \
                or not all(isinstance(value, str) and value for value in matched_ids) \
                or matched_ids != sorted(set(matched_ids)) or not set(matched_ids).issubset(ids):
            raise ValueError("advisory report matched IDs are inconsistent")
        raw_matches += len(matched_ids)
        records = finding.get("records")
        if not isinstance(records, list) \
                or [record.get("id") for record in records if isinstance(record, dict)] != matched_ids \
                or len(records) != len(matched_ids):
            raise ValueError("advisory report records do not match its matched IDs")
        evidence = finding.get("maintenance_only_evidence")
        if classification == "maintenance-only":
            if not isinstance(evidence, list) or not evidence \
                    or not all(isinstance(item, dict) and item.get("id") in ids
                               and isinstance(item.get("reason"), str) and item["reason"].strip()
                               for item in evidence):
                raise ValueError("maintenance-only findings require explicit evidence")
        elif evidence is not None:
            raise ValueError("vulnerability findings cannot have maintenance-only evidence")

    expected_summary = {
        "queried_packages": len(packages),
        "raw_advisory_matches": raw_matches,
        "affected_packages": len(affected_packages),
        "advisory_groups": len(findings),
        "vulnerability_groups": classifications["vulnerability"],
        "maintenance_only_groups": classifications["maintenance-only"],
    }
    summary = report.get("summary")
    if summary != expected_summary:
        raise ValueError("advisory report summary is inconsistent with its findings and lock inputs")


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-commit", help="full Git SHA whose lock inputs are being scanned")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--check", type=Path, help="validate a retained report offline; never query OSV")
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument(
        "--maintenance-only",
        type=Path,
        default=root / "config/osv_maintenance_only.json",
    )
    parser.add_argument(
        "--python-lock",
        type=Path,
        default=root / LOCK_RELATIVE_PATH,
        help="hashed Python deployment lock; must exist so PyPI pins are scanned",
    )
    parser.add_argument("--fail-on-vulnerability", action="store_true")
    args = parser.parse_args()
    if args.check and (args.output or args.source_commit):
        parser.error("--check cannot be combined with --output or --source-commit")
    if not args.check and (not args.output or not args.source_commit):
        parser.error("scanning requires --source-commit and --output")
    if not args.python_lock.is_file():
        raise SystemExit(
            f"missing {args.python_lock}; regenerate it with scripts/resolve_python_lock.py "
            "so the Python deployment environment can be scanned"
        )
    try:
        extra_locks = cargo_lock_inputs(root)
        files = input_files(root / "services/p2p-node/Cargo.lock", root / "apps/web/package-lock.json",
                            root / "services/quant-api/pyproject.toml", args.python_lock, extra_locks)
        source_commit = args.source_commit
        if args.check:
            report = json.loads(args.check.read_text())
            source_commit = report.get("source", {}).get("commit")
        if not isinstance(source_commit, str) or not COMMIT_RE.fullmatch(source_commit):
            raise ValueError("source commit must be a full lowercase 40-character Git SHA")
        committed_inputs = {}
        for relative, path in files.items():
            committed = subprocess.check_output(["git", "show", f"{source_commit}:{relative}"],
                                                cwd=root, stderr=subprocess.PIPE, timeout=10)
            committed_inputs[relative] = {"sha256": hashlib.sha256(committed).hexdigest()}
            if committed_inputs[relative]["sha256"] != sha256_file(path):
                raise ValueError(f"source commit does not match dependency input: {relative}")
        if not args.check:
            report = build_report(
                root / "services/p2p-node/Cargo.lock",
                root / "apps/web/package-lock.json",
                root / "services/quant-api/pyproject.toml",
                load_maintenance_only(args.maintenance_only),
                args.source_commit,
                OsvClient(),
                batch_size=args.batch_size,
                python_lock=args.python_lock,
                additional_cargo_locks=extra_locks,
            )
        validate_report(report, root / "services/p2p-node/Cargo.lock", root / "apps/web/package-lock.json",
                        root / "services/quant-api/pyproject.toml", args.python_lock, extra_locks)
        if report["source"]["inputs"] != committed_inputs:
            raise ValueError("source commit does not match scanned dependency inputs")
        if not args.check:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        raise SystemExit(str(error)) from error
    summary = report["summary"]
    print(
        f"{'Verified ' + str(args.check) if args.check else 'Wrote ' + str(args.output)}: {summary['queried_packages']} packages, "
        f"{summary['vulnerability_groups']} vulnerability groups, "
        f"{summary['maintenance_only_groups']} maintenance-only groups; "
        f"PyPI {report['coverage']['PyPI']['status']} ({report['coverage']['PyPI']['packages']} packages)"
    )
    if args.fail_on_vulnerability and summary["vulnerability_groups"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
