"""Private subprocess entry point; not a shell or arbitrary-code executor."""

import argparse
import json
import sys

from .agents import execute_tool
from .database import Database, canonical


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--kind", required=True)
    parser.add_argument("--database", required=True)
    args = parser.parse_args()
    raw = sys.stdin.buffer.read(1_048_577)
    if len(raw) > 1_048_576:
        raise ValueError("input_too_large")
    result = execute_tool(args.kind, json.loads(raw), Database(args.database))
    sys.stdout.buffer.write(canonical(result))


if __name__ == "__main__":
    main()
