#!/usr/bin/env python3
"""Generate a deterministic CycloneDX SBOM for locked Rust and npm packages.

Exact-version crate license metadata is fetched from crates.io unless present in
an offline cache. Python requirements are identified as unresolved: their version
ranges are not represented as a resolved deployment environment.
"""
from __future__ import annotations

import argparse
import base64
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import tomllib
from urllib.parse import quote
from urllib.request import Request, urlopen
import uuid

CARGO_REGISTRY = "registry+https://github.com/rust-lang/crates.io-index"
CACHE_SCHEMA = "osiris.crate-license-cache.v1"
USER_AGENT = "osiris-eye-dependency-sbom (https://github.com/Osiris6900/osiris-eye)"


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def empty_cache() -> dict:
    return {"schema": CACHE_SCHEMA, "crates": {}}


def load_cache(path: Path) -> dict:
    if not path.exists():
        return empty_cache()
    document = json.loads(path.read_text())
    if not isinstance(document, dict) or document.get("schema") != CACHE_SCHEMA:
        raise ValueError(f"{path}: unsupported crate metadata cache")
    if not isinstance(document.get("crates"), dict):
        raise ValueError(f"{path}: crates must be a mapping")
    return document


def cargo_packages(path: Path) -> tuple[list[dict], list[dict]]:
    document = tomllib.loads(path.read_text())
    packages = document.get("package")
    if not isinstance(packages, list):
        raise ValueError(f"{path}: Cargo lockfile must contain package entries")
    local, registry = [], []
    for package in packages:
        name, version = package.get("name"), package.get("version")
        if not isinstance(name, str) or not isinstance(version, str):
            raise ValueError(f"{path}: every package needs text name/version")
        source = package.get("source")
        if source is None:
            local.append(package)
            continue
        if source != CARGO_REGISTRY:
            raise ValueError(f"{path}: unsupported Cargo source for {name}@{version}: {source}")
        checksum = package.get("checksum")
        if not isinstance(checksum, str) or len(checksum) != 64:
            raise ValueError(f"{path}: missing SHA-256 checksum for {name}@{version}")
        try:
            bytes.fromhex(checksum)
        except ValueError as error:
            raise ValueError(f"{path}: invalid checksum for {name}@{version}") from error
        registry.append(package)
    return local, registry


def fetch_crate(name: str, version: str) -> dict:
    url = f"https://crates.io/api/v1/crates/{quote(name, safe='')}/{quote(version, safe='')}"
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urlopen(request, timeout=30) as response:
        document = json.load(response)
    metadata = document.get("version") if isinstance(document, dict) else None
    if not isinstance(metadata, dict):
        raise ValueError(f"crates.io returned no version metadata for {name}@{version}")
    if metadata.get("crate") != name or metadata.get("num") != version:
        raise ValueError(f"crates.io returned mismatched metadata for {name}@{version}")
    return {
        "license": metadata.get("license"),
        "license_file": metadata.get("license_file"),
        "checksum": metadata.get("checksum"),
        "source": url,
    }


def resolve_crate_metadata(packages: list[dict], cache: dict, offline: bool) -> dict[str, dict]:
    cached = cache["crates"]
    missing = [p for p in packages if f"{p['name']}@{p['version']}" not in cached]
    if offline and missing:
        names = ", ".join(f"{p['name']}@{p['version']}" for p in missing[:10])
        raise ValueError(f"offline crate cache is missing {len(missing)} package(s): {names}")
    if missing:
        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = {
                executor.submit(fetch_crate, p["name"], p["version"]): p for p in missing
            }
            for future, package in futures.items():
                key = f"{package['name']}@{package['version']}"
                try:
                    cached[key] = future.result()
                except Exception as error:
                    raise ValueError(f"could not retrieve {key}: {error}") from error

    resolved = {}
    for package in packages:
        key = f"{package['name']}@{package['version']}"
        metadata = cached[key]
        if metadata.get("checksum") != package["checksum"]:
            raise ValueError(f"crate checksum mismatch for {key}")
        has_license = isinstance(metadata.get("license"), str) and metadata["license"].strip()
        has_license_file = (
            isinstance(metadata.get("license_file"), str) and metadata["license_file"].strip()
        )
        if not has_license and not has_license_file:
            raise ValueError(f"crate license metadata missing for {key}")
        resolved[key] = metadata
    return resolved


def license_field(metadata: dict) -> list[dict]:
    if isinstance(metadata.get("license"), str) and metadata["license"].strip():
        return [{"expression": metadata["license"]}]
    return [{"license": {"name": f"SEE LICENSE IN {metadata['license_file']}"}}]


def purl(ecosystem: str, name: str, version: str | None = None) -> str:
    if ecosystem == "npm" and name.startswith("@"):
        scope, separator, package = name.partition("/")
        if not separator or not package:
            raise ValueError(f"invalid scoped npm package: {name}")
        value = f"pkg:npm/{quote(scope, safe='')}/{quote(package, safe='')}"
    else:
        value = f"pkg:{ecosystem}/{quote(name, safe='')}"
    return value if version is None else f"{value}@{quote(version, safe='')}"


def npm_hash(integrity: str) -> dict:
    candidates = {}
    for token in integrity.split():
        algorithm, separator, encoded = token.partition("-")
        if not separator or algorithm not in ("sha512", "sha256", "sha1"):
            continue
        try:
            decoded = base64.b64decode(encoded, validate=True).hex()
        except ValueError as error:
            raise ValueError("invalid npm integrity base64") from error
        candidates[algorithm] = decoded
    for algorithm, cyclonedx_name in (("sha512", "SHA-512"), ("sha256", "SHA-256"), ("sha1", "SHA-1")):
        if algorithm in candidates:
            return {"alg": cyclonedx_name, "content": candidates[algorithm]}
    raise ValueError("npm package needs a supported SHA-512/SHA-256/SHA-1 integrity hash")


def npm_packages(path: Path) -> tuple[dict, list[dict]]:
    document = json.loads(path.read_text())
    if document.get("lockfileVersion") != 3 or not isinstance(document.get("packages"), dict):
        raise ValueError(f"{path}: expected npm lockfileVersion 3 with packages")
    packages = document["packages"]
    root = packages.get("")
    if not isinstance(root, dict):
        raise ValueError(f"{path}: missing root package")
    resolved = []
    seen = set()
    for package_path, package in packages.items():
        if not package_path:
            continue
        name = package.get("name") or package_path.rsplit("node_modules/", 1)[-1]
        version = package.get("version")
        if not isinstance(name, str) or not isinstance(version, str):
            raise ValueError(f"{path}: invalid package at {package_path}")
        if (name, version) in seen:
            raise ValueError(f"{path}: duplicate npm identity {name}@{version}; deduplication review required")
        seen.add((name, version))
        license_name = package.get("license")
        if not isinstance(license_name, str) or not license_name.strip():
            raise ValueError(f"{path}: license missing for {name}@{version}")
        resolved.append((package_path, name, version, package))
    return root, resolved


def application(
    ecosystem: str, name: str, version: str | None, properties: list[dict] | None = None
) -> dict:
    reference = purl(ecosystem, name, version)
    component = {
        "type": "application",
        "bom-ref": reference,
        "name": name,
        "purl": reference,
        "properties": properties or [],
    }
    if version is not None:
        component["version"] = version
    return component


def build_sbom(cargo_lock: Path, npm_lock: Path, pyproject: Path, cache: dict, offline: bool) -> dict:
    local_cargo, registry_cargo = cargo_packages(cargo_lock)
    crate_metadata = resolve_crate_metadata(registry_cargo, cache, offline)
    npm_root, locked_npm = npm_packages(npm_lock)
    python_project = tomllib.loads(pyproject.read_text()).get("project", {})
    python_name = python_project.get("name")
    python_version = python_project.get("version")
    if not isinstance(python_name, str) or not isinstance(python_version, str):
        raise ValueError(f"{pyproject}: Python project name/version missing")

    lock_hashes = {
        "apps/web/package-lock.json": sha256_file(npm_lock),
        "services/p2p-node/Cargo.lock": sha256_file(cargo_lock),
        "services/quant-api/pyproject.toml": sha256_file(pyproject),
    }
    serial_material = "\n".join(f"{key}:{value}" for key, value in sorted(lock_hashes.items()))
    components = []
    for package in local_cargo:
        components.append(application("generic", package["name"], package["version"]))
    components.append(application("npm", npm_root["name"], npm_root["version"]))
    components.append(application(
        "generic", python_name, python_version,
        [
            {"name": "osiris:dependency-resolution", "value": "unresolved"},
            {"name": "osiris:requires-python", "value": python_project.get("requires-python", "")},
            {
                "name": "osiris:declared-dependencies",
                "value": json.dumps(python_project.get("dependencies", []), separators=(",", ":")),
            },
        ],
    ))

    for package in sorted(registry_cargo, key=lambda p: (p["name"], p["version"])):
        key = f"{package['name']}@{package['version']}"
        reference = purl("cargo", package["name"], package["version"])
        components.append({
            "type": "library",
            "bom-ref": reference,
            "name": package["name"],
            "version": package["version"],
            "scope": "required",
            "purl": reference,
            "licenses": license_field(crate_metadata[key]),
            "hashes": [{"alg": "SHA-256", "content": package["checksum"]}],
            "externalReferences": [{
                "type": "distribution",
                "url": f"https://crates.io/api/v1/crates/{quote(package['name'], safe='')}/{quote(package['version'], safe='')}/download",
            }],
        })
    for _, name, version, package in sorted(locked_npm, key=lambda item: (item[1], item[2])):
        reference = purl("npm", name, version)
        components.append({
            "type": "library",
            "bom-ref": reference,
            "name": name,
            "version": version,
            "scope": "optional" if package.get("dev") or package.get("optional") else "required",
            "purl": reference,
            "licenses": [{"expression": package["license"]}],
            "hashes": [npm_hash(package["integrity"])],
            "externalReferences": [{"type": "distribution", "url": package["resolved"]}],
        })

    references = [component["bom-ref"] for component in components]
    if len(references) != len(set(references)):
        raise ValueError("duplicate CycloneDX bom-ref detected")
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "serialNumber": f"urn:uuid:{uuid.uuid5(uuid.NAMESPACE_URL, serial_material)}",
        "version": 1,
        "metadata": {
            "component": application("generic", "osiris-eye", None, [
                {"name": f"osiris:input-sha256:{key}", "value": value}
                for key, value in sorted(lock_hashes.items())
            ] + [{"name": "osiris:python-deployment-resolution", "value": "unresolved"}]),
        },
        "components": components,
    }


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--crate-cache", type=Path,
        default=Path.home() / ".cache/osiris-eye/crate-license-cache.json",
    )
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    try:
        cache = load_cache(args.crate_cache)
        sbom = build_sbom(
            root / "services/p2p-node/Cargo.lock",
            root / "apps/web/package-lock.json",
            root / "services/quant-api/pyproject.toml",
            cache,
            args.offline,
        )
        serialized = json.dumps(sbom, indent=2, sort_keys=True) + "\n"
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized)
        if not args.offline:
            args.crate_cache.parent.mkdir(parents=True, exist_ok=True)
            args.crate_cache.write_text(json.dumps(cache, indent=2, sort_keys=True) + "\n")
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        raise SystemExit(str(error)) from error
    print(
        f"Wrote {args.output} with {len(sbom['components'])} components; "
        "Python deployment dependencies remain unresolved"
    )


if __name__ == "__main__":
    main()
