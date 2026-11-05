#!/usr/bin/env python3
"""Pull the structured answer out of a headless Claude result file.

Usage: extract.py RESULT_JSON OUTPUT_JSON. Exits non-zero and writes nothing if the
run failed or returned no structured answer, so a bad run can never reach the gate.
"""

import json
import sys
from pathlib import Path


def main(result_path: str, output_path: str) -> int:
    """Write the run's structured output; return a process exit code."""
    try:
        result = json.loads(Path(result_path).read_text())
    except (OSError, ValueError) as exc:
        print(f"unreadable Claude result: {exc}", file=sys.stderr)
        return 1
    answer = result.get("structured_output") if isinstance(result, dict) else None
    if not isinstance(answer, dict) or result.get("is_error"):
        print("Claude run returned no structured answer", file=sys.stderr)
        return 1
    Path(output_path).write_text(json.dumps(answer, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
