"""Regenerate every synthetic dataset deterministically.

Usage:
    python3 shared/validation/regenerate_all.py

Local-only; reads no network resource; writes only inside the staging tree.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


GENERATORS = [
    ("dqcc",
     ROOT / "projects" / "data-quality-command-center" / "src" / "generate.py"),
    ("umgd",
     ROOT / "projects" / "urban-mobility-gap" / "src" / "generate.py"),
    ("pse",
     ROOT / "projects" / "peer-scenario-explorer" / "src" / "generate.py"),
]


def regenerate_all() -> dict:
    """Run every project generator against its canonical data/synthetic dir."""
    receipts = {}
    for name, path in GENERATORS:
        mod = _load(f"_gen_{name}", path)
        receipt = mod.generate()  # uses the module's default OUT dir
        receipts[name] = receipt
        print(f"[{name}] snapshot={receipt['snapshot_id']}")
        for k, v in receipt["row_counts"].items():
            print(f"  {k}: {v} rows")
    return receipts


if __name__ == "__main__":
    regenerate_all()
