"""Tests for pulling the structured answer out of a headless Claude result file."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "feed" / "extract.py"


def run(result_text: str) -> tuple[int, str | None]:
    """Run extract.py on the given text; return (exit code, output file text)."""
    with tempfile.TemporaryDirectory() as raw:
        directory = Path(raw)
        (directory / "in.json").write_text(result_text)
        out = directory / "out.json"
        done = subprocess.run(
            [sys.executable, str(SCRIPT), str(directory / "in.json"), str(out)],
            capture_output=True,
            text=True,
            check=False,
        )
        return done.returncode, out.read_text() if out.exists() else None


class Extract(unittest.TestCase):
    def test_writes_structured_output(self) -> None:
        answer = {"items": {"5080": {"price": 1}}}
        code, text = run(json.dumps({"is_error": False, "structured_output": answer}))
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(text or ""), answer)

    def test_run_marked_as_error_is_refused(self) -> None:
        answer = {"items": {"5080": {"price": 1}}}
        code, text = run(json.dumps({"is_error": True, "structured_output": answer}))
        self.assertNotEqual(code, 0)
        self.assertIsNone(text)

    def test_missing_structured_output_is_refused(self) -> None:
        code, text = run(json.dumps({"is_error": False, "result": "here are prices"}))
        self.assertNotEqual(code, 0)
        self.assertIsNone(text)

    def test_non_json_is_refused(self) -> None:
        code, text = run("rate limited")
        self.assertNotEqual(code, 0)
        self.assertIsNone(text)


if __name__ == "__main__":
    unittest.main()
