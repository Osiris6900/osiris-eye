#!/usr/bin/env python3
"""Reproduce retained SBOM bytes from an explicitly selected Git commit, offline.

Checks an artifact directory or downloaded ZIP without extracting it. CI run
identity and the expected archive digest must come from a trusted caller; this
does not authenticate GitHub, sign a report, or approve dependency/release risk.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import stat
import subprocess
import tempfile
import zipfile
from pathlib import Path

from generate_dependency_sbom import build_sbom, load_cache, pypi_license_map

ROOT = Path(__file__).resolve().parents[1]
FILES = {"dependency-sbom.json", "dependency-sbom.json.sha256", "dependency-sbom-provenance.json"}
INPUTS = (
    "apps/web/package-lock.json", "crates/native-feed/Cargo.lock",
    "crates/risk-kernel/Cargo.lock", "services/p2p-node/Cargo.lock",
    "services/quant-api/pyproject.toml", "services/quant-api/requirements-lock.txt",
)
GENERATOR = "scripts/generate_dependency_sbom.py"
CRATE_METADATA = "docs/CRATE_LICENSE_PROVENANCE.json"
PYTHON_METADATA = "docs/PYTHON_LICENSE_PROVENANCE.json"
SHA40 = re.compile(r"[0-9a-f]{40}")
SHA256 = re.compile(r"[0-9a-f]{64}")
LIMIT = 10 * 1024 * 1024


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def document(data: bytes) -> dict:
    def invalid_constant(value):
        raise ValueError(f"non-finite JSON constant: {value}")

    value = json.loads(data, object_pairs_hook=unique_object, parse_constant=invalid_constant)
    if not isinstance(value, dict):
        raise TypeError("JSON evidence must be an object")
    return value


def read_artifact(path: Path, *, archive: bool, expected_digest: str | None = None,
                  expected_files: set[str] | None = None) -> tuple[dict, str | None]:
    names = FILES if expected_files is None else expected_files
    if path.is_symlink():
        raise ValueError("artifact path must not be a symlink")
    if not archive:
        if not path.is_dir() or {p.name for p in path.iterdir()} != names:
            raise ValueError("artifact directory must contain exactly the expected evidence files")
        result = {}
        for name in sorted(names):
            member = path / name
            if not stat.S_ISREG(member.lstat().st_mode) or member.stat().st_size > LIMIT:
                raise ValueError("evidence files must be bounded regular files")
            result[name] = member.read_bytes()
        return result, None
    if not expected_digest or not SHA256.fullmatch(expected_digest):
        raise ValueError("a trusted expected archive SHA-256 is required")
    if not path.is_file() or path.stat().st_size > LIMIT:
        raise ValueError("archive must be a bounded regular file")
    raw = path.read_bytes()
    actual_digest = digest(raw)
    if actual_digest != expected_digest:
        raise ValueError("downloaded archive digest mismatch")
    # Read from the same bytes that were hashed; never reopen or extract the ZIP.
    from io import BytesIO
    with zipfile.ZipFile(BytesIO(raw)) as bundle:
        members = bundle.infolist()
        if len(members) != len(names) or {p.filename for p in members} != names:
            raise ValueError("archive must contain exactly the expected unique evidence files")
        for member in members:
            mode = member.external_attr >> 16
            if member.file_size > LIMIT or member.flag_bits & 1 or stat.S_IFMT(mode) not in (0, stat.S_IFREG):
                raise ValueError("archive members must be bounded, unencrypted regular files")
        return {member.filename: bundle.read(member) for member in members}, actual_digest


def git(repository: Path, *arguments: str) -> bytes:
    return subprocess.check_output(["git", *arguments], cwd=repository, stderr=subprocess.PIPE, timeout=30)


def verify(files: dict[str, bytes], repository: Path, commit: str, candidate: str,
           run_id: str, run_attempt: str) -> dict:
    """Return only verified facts; malformed or mismatched evidence raises."""
    if not SHA40.fullmatch(commit) or not SHA40.fullmatch(candidate):
        raise ValueError("full lowercase checkout and candidate commit SHAs are required")
    if not re.fullmatch(r"[1-9][0-9]*", run_id) or not re.fullmatch(r"[1-9][0-9]*", run_attempt):
        raise ValueError("positive CI run ID and attempt are required")
    if set(files) != FILES or any(not isinstance(value, bytes) or len(value) > LIMIT for value in files.values()):
        raise ValueError("all three bounded evidence files are required")
    sbom = document(files["dependency-sbom.json"])
    provenance = document(files["dependency-sbom-provenance.json"])
    tree = git(repository, "rev-parse", f"{commit}^{{tree}}").decode().strip()
    source = {name: git(repository, "show", f"{commit}:{name}")
              for name in (*INPUTS, GENERATOR, CRATE_METADATA, PYTHON_METADATA)}
    cache = document(source[CRATE_METADATA])
    canonical_cache = (json.dumps(cache, indent=2, sort_keys=True) + "\n").encode()
    expected = {
        "schema": "osiris.dependency-sbom-provenance.v1",
        "candidate_commit": candidate, "checkout_commit": commit, "checkout_tree": tree,
        "checkout_dirty": False, "generator_sha256": digest(source[GENERATOR]),
        "crate_license_metadata_sha256": digest(canonical_cache),
        "python_license_evidence_sha256": digest(source[PYTHON_METADATA]),
        "input_sha256": {name: digest(source[name]) for name in INPUTS},
        "sbom_sha256": digest(files["dependency-sbom.json"]),
        "ci_run_id": run_id, "ci_run_attempt": run_attempt,
        "signature_verified": False, "licences_independently_reviewed": False,
        "release_gate_accepted": False, "live_trading_authorized": False,
    }
    for key, value in expected.items():
        if type(provenance.get(key)) is not type(value) or provenance[key] != value:
            raise ValueError(f"provenance.{key} differs from expected source/run/evidence")
    sidecar = (expected["sbom_sha256"] + "  dependency-sbom.json\n").encode()
    if files["dependency-sbom.json.sha256"] != sidecar:
        raise ValueError("SBOM digest sidecar mismatch")
    # Recompute from Git objects, including all three Rust roots. A tampered
    # SBOM with a newly computed self-consistent hash must still be rejected.
    with tempfile.TemporaryDirectory(prefix="osiris-sbom-source-") as temporary:
        root = Path(temporary)
        for name, data in source.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        lock = root / "services/quant-api/requirements-lock.txt"
        reproduced = build_sbom(
            root / "services/p2p-node/Cargo.lock", root / "apps/web/package-lock.json",
            root / "services/quant-api/pyproject.toml", load_cache(root / CRATE_METADATA), True,
            lock, pypi_license_map(root / PYTHON_METADATA, lock),
            additional_cargo_locks={name: root / name for name in INPUTS if name.startswith("crates/")},
        )
    serialized = (json.dumps(reproduced, indent=2, sort_keys=True) + "\n").encode()
    if files["dependency-sbom.json"] != serialized or sbom != reproduced:
        raise ValueError("SBOM does not reproduce from the selected commit's complete locked graph")
    count = provenance.get("component_count")
    if type(count) is not int or count != len(reproduced["components"]):
        raise ValueError("provenance.component_count mismatch")
    return {
        "checkout_commit": commit, "candidate_commit": candidate, "checkout_tree": tree,
        "candidate_tree_matches_checkout": git(repository, "rev-parse", f"{candidate}^{{tree}}").decode().strip() == tree,
        "ci_run_id": run_id, "ci_run_attempt": run_attempt, "component_count": count,
        "files_sha256": {name: digest(data) for name, data in sorted(files.items())},
        "input_sha256": expected["input_sha256"],
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--archive", type=Path)
    source.add_argument("--artifact-dir", type=Path)
    parser.add_argument("--archive-sha256")
    parser.add_argument("--repository", type=Path, default=ROOT)
    parser.add_argument("--commit", required=True, help="Expected actual checkout SHA, not a moving branch")
    parser.add_argument("--candidate-commit", help="Expected PR head when different from the test merge checkout")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--run-attempt", required=True)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    if args.report and args.report.exists():
        parser.error("report exists; refusing overwrite")
    if args.artifact_dir and args.archive_sha256:
        parser.error("--archive-sha256 requires --archive")
    report = {
        "schema": "osiris.sbom-artifact-verification.v1", "passed": False,
        "release_gate_accepted": False, "live_trading_authorized": False,
        "independent_review_verified": False, "ci_identity_authenticated": False,
    }
    try:
        files, archive_digest = read_artifact(args.archive or args.artifact_dir,
            archive=bool(args.archive), expected_digest=args.archive_sha256)
        report.update(verify(files, args.repository, args.commit, args.candidate_commit or args.commit,
                             args.run_id, args.run_attempt))
        report.update(passed=True, archive_sha256=archive_digest)
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, RecursionError,
            subprocess.SubprocessError, zipfile.BadZipFile) as error:
        report["error"] = str(error)
    serialized = json.dumps(report, indent=2, sort_keys=True) + "\n"
    print(serialized, end="")
    if args.report:
        with args.report.open("x", encoding="utf-8") as output:
            output.write(serialized)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
