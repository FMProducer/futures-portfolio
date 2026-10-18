"""Run only the offline ledger scenarios and save reproducibility evidence."""
import difflib
import hashlib
import io
import json
import platform
import subprocess
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path


class RecordedResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.records = []

    def addSuccess(self, test):
        super().addSuccess(test)
        self.records.append(dict(scenario=test.id(), status="PASS"))

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self.records.append(dict(scenario=test.id(), status="FAIL"))

    def addError(self, test, err):
        super().addError(test, err)
        self.records.append(dict(scenario=test.id(), status="ERROR"))


def main():
    root = Path(__file__).resolve().parents[2]
    output = root / "docs" / "coordination" / "accounting_stage1_evidence"
    output.mkdir(exist_ok=True)
    sources = sorted((root / "accounting").rglob("*.py"))
    sources.append(root / "docs" / "coordination" / "ACCOUNTING_SPEC_V3.md")
    hashes = {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}
    candidate = hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()
    log = io.StringIO()
    suite = unittest.defaultTestLoader.loadTestsFromName("accounting.scenarios.test_ledger")
    result = unittest.TextTestRunner(stream=log, verbosity=2, resultclass=RecordedResult).run(suite)
    (output / "scenarios.log").write_text(log.getvalue(), encoding="utf-8")
    manifest = dict(base_commit=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip(),
                    candidate_sha256=candidate, source_sha256=hashes, python=platform.python_version(),
                    executable=sys.executable, sqlite_version=__import__("sqlite3").sqlite_version,
                    run_utc=datetime.now(timezone.utc).isoformat(), tests_run=result.testsRun,
                    successful=result.wasSuccessful(), scenarios=result.records)
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    patch = "".join("".join(difflib.unified_diff([], p.read_text(encoding="utf-8").splitlines(True),
                    fromfile="/dev/null", tofile="b/" + p.relative_to(root).as_posix())) for p in sources)
    (output / "candidate.patch").write_text(patch, encoding="utf-8")
    print(json.dumps({k: manifest[k] for k in ("base_commit", "candidate_sha256", "tests_run", "successful")}))
    if not result.wasSuccessful():
        print(log.getvalue())
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
