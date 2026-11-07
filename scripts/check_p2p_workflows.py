#!/usr/bin/env python3
"""Validate workflow YAML/Bash and enforce the CI action-pinning ratchet.

The audit never executes job code. Existing mutable action references are
recorded in a baseline; new references or increased occurrence counts fail.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import re
import subprocess

import yaml


FULL_COMMIT_SHA = re.compile(r"[0-9a-fA-F]{40}")
PINNED_DOCKER_ACTION = re.compile(r"docker://\S+@sha256:[0-9a-fA-F]{64}")
BASELINE_SCHEMA = "osiris.ci-action-pin-baseline.v1"


class UniqueKeyLoader(yaml.SafeLoader):
    """Reject duplicate mapping keys rather than silently losing a job or gate."""


def unique_mapping(loader: UniqueKeyLoader, node: yaml.MappingNode, deep: bool = False):
    mapping = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise yaml.constructor.ConstructorError(
                "workflow mapping", node.start_mark, f"duplicate key: {key}", key_node.start_mark
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


UniqueKeyLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, unique_mapping)


def load_workflow(path: Path) -> dict:
    workflow = yaml.load(path.read_text(), Loader=UniqueKeyLoader)
    if not isinstance(workflow, dict) or not isinstance(workflow.get("jobs"), dict):
        raise ValueError(f"{path}: workflow must define a jobs mapping")
    return workflow


def action_pin_error(uses: str) -> str | None:
    """Return why a `uses` target is mutable, or None for local/pinned targets."""
    value = uses.strip()
    if not value:
        return "uses reference must not be empty"
    if value.startswith("./"):
        return None
    if value.startswith("docker://"):
        if PINNED_DOCKER_ACTION.fullmatch(value):
            return None
        return "Docker action must use an immutable @sha256:<64 hex> digest"
    target, separator, revision = value.rpartition("@")
    if not separator or not target or not revision or any(c.isspace() for c in value):
        return "remote action must use <owner>/<repo>[/path]@<full commit SHA>"
    if FULL_COMMIT_SHA.fullmatch(revision):
        return None
    return f"remote action revision {revision!r} is not a full 40-character commit SHA"


def unpinned_action_references(path: Path) -> list[str]:
    """Collect mutable remote actions and job-level reusable workflows."""
    workflow = load_workflow(path)
    references = []
    for job_name, job in workflow["jobs"].items():
        if not isinstance(job, dict):
            raise ValueError(f"{path}: invalid job {job_name}")
        if "uses" in job:
            uses = job["uses"]
            if not isinstance(uses, str):
                raise ValueError(f"{path}: job {job_name} uses must be text")
            if action_pin_error(uses):
                references.append(uses)
        steps = job.get("steps", [])
        if not isinstance(steps, list):
            raise ValueError(f"{path}: job {job_name} steps must be a list")
        for step_index, step in enumerate(steps, 1):
            if not isinstance(step, dict) or "uses" not in step:
                continue
            uses = step["uses"]
            if not isinstance(uses, str):
                raise ValueError(f"{path}: job {job_name} step {step_index} uses must be text")
            if action_pin_error(uses):
                references.append(uses)
    return references


def audit_action_pins(paths: list[Path], baseline_path: Path) -> Counter[str]:
    """Allow baseline debt to shrink, but reject new or more frequent mutable refs."""
    document = json.loads(baseline_path.read_text())
    if not isinstance(document, dict) or document.get("schema") != BASELINE_SCHEMA:
        raise ValueError(f"{baseline_path}: unsupported action-pin baseline schema")
    allowed = document.get("allowed_unpinned")
    if not isinstance(allowed, dict):
        raise ValueError(f"{baseline_path}: allowed_unpinned must be a mapping")
    for reference, maximum in allowed.items():
        if (
            not isinstance(reference, str)
            or not reference
            or reference != reference.strip()
            or action_pin_error(reference) is None
        ):
            raise ValueError(f"{baseline_path}: invalid unpinned baseline entry {reference!r}")
        if type(maximum) is not int or maximum < 0:
            raise ValueError(f"{baseline_path}: baseline count for {reference} must be a non-negative integer")

    actual: Counter[str] = Counter()
    for path in paths:
        actual.update(unpinned_action_references(path))
    failures = []
    for reference, count in sorted(actual.items()):
        maximum = allowed.get(reference)
        if maximum is None:
            failures.append(f"new unpinned reference {reference} occurs {count} time(s)")
        elif count > maximum:
            failures.append(
                f"unpinned reference {reference} occurs {count} times; baseline allows {maximum}"
            )
    if failures:
        details = "\n".join(f"- {failure}" for failure in failures)
        raise ValueError(
            "CI action-pinning ratchet failed; use immutable revisions instead of expanding the baseline:\n"
            + details
        )
    return actual


def validate_workflow(path: Path) -> int:
    workflow = load_workflow(path)
    checked = 0
    for job_name, job in workflow["jobs"].items():
        if not isinstance(job, dict):
            raise ValueError(f"{path}: invalid job {job_name}")
        default_shell = job.get("defaults", {}).get("run", {}).get(
            "shell", workflow.get("defaults", {}).get("run", {}).get("shell", "bash")
        )
        for step_index, step in enumerate(job.get("steps", []), 1):
            if not isinstance(step, dict) or "run" not in step:
                continue
            script = step["run"]
            if not isinstance(script, str):
                raise ValueError(f"{path}: job {job_name} step {step_index} run must be text")
            shell = step.get("shell", default_shell)
            if not isinstance(shell, str) or shell.split()[0] not in ("bash", "sh"):
                continue
            # Actions evaluates expressions before the selected shell parses
            # them. Substitute inert text; never execute a workflow expression.
            script = re.sub(r"\$\{\{[\s\S]*?\}\}", "workflow_expression", script)
            result = subprocess.run(
                [shell.split()[0], "-n"], input=script, text=True,
                capture_output=True, timeout=10, check=False,
            )
            if result.returncode:
                raise ValueError(
                    f"{path}: job {job_name} step {step_index} shell syntax: {result.stderr.strip()}"
                )
            checked += 1
    return checked


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", type=Path, nargs="*")
    parser.add_argument(
        "--baseline", type=Path, default=root / "config/ci_action_pin_baseline.json"
    )
    args = parser.parse_args()
    paths = args.paths or sorted((root / ".github/workflows").glob("*.y*ml"))
    if not paths:
        raise SystemExit("No workflow files found")
    try:
        checked = sum(validate_workflow(path) for path in paths)
        unpinned = audit_action_pins(paths, args.baseline)
    except (OSError, ValueError, yaml.YAMLError, subprocess.SubprocessError) as error:
        raise SystemExit(str(error)) from error
    print(f"Validated {len(paths)} workflow YAML files and {checked} shell blocks")
    if unpinned:
        print(
            f"Action-pinning ratchet passed with {sum(unpinned.values())} "
            "baselined mutable references; actual pinning is still outstanding"
        )
    else:
        print("Action-pinning audit passed: all remote actions use immutable revisions")


if __name__ == "__main__":
    main()
