#!/usr/bin/env python3
"""Parse workflow YAML and syntax-check embedded Bash without executing it.

This catches malformed multiline shell/YAML blocks before GitHub rejects an
entire workflow. It is not an Actions schema, expression or authorization audit.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import re
import subprocess

import yaml


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


def validate_workflow(path: Path) -> int:
    workflow = yaml.load(path.read_text(), Loader=UniqueKeyLoader)
    if not isinstance(workflow, dict) or not isinstance(workflow.get("jobs"), dict):
        raise ValueError(f"{path}: workflow must define a jobs mapping")
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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", type=Path, nargs="*")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    paths = args.paths or sorted((root / ".github/workflows").glob("*.y*ml"))
    if not paths:
        raise SystemExit("No workflow files found")
    try:
        checked = sum(validate_workflow(path) for path in paths)
    except (OSError, ValueError, yaml.YAMLError, subprocess.SubprocessError) as error:
        raise SystemExit(str(error)) from error
    print(f"Validated {len(paths)} workflow YAML files and {checked} shell blocks")


if __name__ == "__main__":
    main()
