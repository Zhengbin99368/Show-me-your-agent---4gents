"""Command-line interface for read-only fictional eligibility evaluation."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from .domain import InputValidationError, parse_cases
from .eligibility import evaluate_case


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Evaluate fictional dental follow-up eligibility without actions."
    )
    parser.add_argument("input", type=Path, help="Path to a fictional JSON case file")
    parser.add_argument(
        "--compact", action="store_true", help="Print compact JSON instead of indented JSON"
    )
    return parser


def run(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        with args.input.open("r", encoding="utf-8") as handle:
            document = json.load(handle)
        cases = parse_cases(document)
        results = [evaluate_case(case).to_dict() for case in cases]
    except (OSError, json.JSONDecodeError, InputValidationError) as error:
        print(json.dumps({"error": str(error)}), file=sys.stderr)
        return 2

    indent = None if args.compact else 2
    print(json.dumps({"results": results}, indent=indent, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(run())

