"""Run every project test plus the shared disclosure/structure validators.

Usage:
    python3 shared/validation/run_all_tests.py

Exits nonzero if any test or validator fails. Local-only.
"""
from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# Project test modules are loaded by file path because project directories
# contain hyphens (invalid as Python package names).
TEST_FILES = [
    ("dqcc_tests",
     ROOT / "projects" / "data-quality-command-center" / "tests" / "test_dqcc.py"),
    ("umgd_tests",
     ROOT / "projects" / "urban-mobility-gap" / "tests" / "test_umgd.py"),
    ("pse_tests",
     ROOT / "projects" / "peer-scenario-explorer" / "tests" / "test_pse.py"),
    ("disclosure_scan",
     ROOT / "shared" / "validation" / "test_disclosure_scan.py"),
    ("structure_scan",
     ROOT / "shared" / "validation" / "test_structure_scan.py"),
    ("determinism",
     ROOT / "shared" / "validation" / "test_determinism.py"),
    ("tableau_release",
     ROOT / "shared" / "validation" / "test_tableau_release.py"),
]


def main() -> int:
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for name, path in TEST_FILES:
        if not path.exists():
            print(f"WARN: test file missing: {path}", file=sys.stderr)
            continue
        mod = _load(name, path)
        suite.addTests(loader.loadTestsFromModule(mod))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
