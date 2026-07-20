"""Cross-process determinism check.

Regenerate every synthetic dataset in genuinely separate Python interpreter
processes under distinct `PYTHONHASHSEED` values and compare complete
output-byte manifests (relative path -> SHA-256) file-by-file.

This is the enforcement layer for the contract requirement that regeneration
is deterministic **across fresh processes**, independent of per-process hash
randomization. A same-process module-reload test cannot detect the built-in
`hash()` drift that originally motivated this check, because Python fixes the
hash seed once per process; only subprocess isolation exercises the real
per-process environment. The DQCC, UMG, and PSE generators each have a
project-local subprocess determinism test as well; this shared test is the
portfolio-wide cross-project gate.

The ZCP requires at least three distinct `PYTHONHASHSEED` values; this module
uses [1, 2, 7]. All three must produce byte-identical output sets for every
project.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

GENERATORS = [
    ("dqcc", ROOT / "projects" / "data-quality-command-center" / "src" / "generate.py"),
    ("umgd", ROOT / "projects" / "urban-mobility-gap" / "src" / "generate.py"),
    ("pse", ROOT / "projects" / "peer-scenario-explorer" / "src" / "generate.py"),
]

# At least three distinct PYTHONHASHSEED values, per the Wave 2 ZCP.
HASH_SEEDS = [1, 2, 7]


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        h.update(f.read())
    return h.hexdigest()


def _hash_tree(root: Path) -> dict:
    """Return {relative_path: sha256} for every regular file under root."""
    out: dict = {}
    for p in sorted(root.rglob("*")):
        if p.is_file() and not p.is_symlink():
            out[p.relative_to(root).as_posix()] = _sha256(p)
    return out


def _generate_in_subprocess(gen_path: Path, seed: int, out_dir: Path) -> None:
    """Run a generator in a fresh interpreter process with a fixed
    PYTHONHASHSEED and write its outputs into out_dir.

    Raises AssertionError if the subprocess fails. The snippet loads the
    generator by file path (project dirs contain hyphens, invalid as package
    names) and calls generate(out_dir). This defeats any same-process cache.
    """
    env = dict(os.environ)
    env["PYTHONHASHSEED"] = str(seed)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    script = (
        "import importlib.util, pathlib, json, sys\n"
        f"out = pathlib.Path({str(out_dir)!r})\n"
        f"spec = importlib.util.spec_from_file_location('_g', {str(gen_path)!r})\n"
        "m = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(m)\n"
        "r = m.generate(out)\n"
        # Emit the receipt to stdout as JSON so the caller can assert on it.
        "sys.stdout.write(json.dumps(r, sort_keys=True, default=str))\n"
    )
    proc = subprocess.run(
        [sys.executable, "-B", "-c", script],
        env=env, capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise AssertionError(
            f"subprocess generation failed (seed={seed}, gen={gen_path.name}, "
            f"rc={proc.returncode})\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
        )
    return proc.stdout.strip()


class TestCrossProcessDeterminism(unittest.TestCase):
    """Every generator must produce byte-identical output sets across at
    least three distinct PYTHONHASHSEED values, each in a fresh process."""

    def _generate_three_seeds(self, gen_path: Path, project: str):
        """Return {seed: hash_tree} for all three seeds plus the three
        receipts. Creates and cleans up three temp dirs."""
        trees = {}
        receipts = {}
        tmpdirs = []
        try:
            for seed in HASH_SEEDS:
                tmp = Path(tempfile.mkdtemp(prefix=f"det_{project}_s{seed}_"))
                tmpdirs.append(tmp)
                receipt_json = _generate_in_subprocess(gen_path, seed, tmp)
                trees[seed] = _hash_tree(tmp)
                try:
                    receipts[seed] = json.loads(receipt_json) if receipt_json else {}
                except json.JSONDecodeError:
                    receipts[seed] = {"_raw": receipt_json}
            return trees, receipts
        finally:
            import shutil
            for tmp in tmpdirs:
                shutil.rmtree(tmp, ignore_errors=True)

    def test_each_generator_byte_identical_across_three_seeds(self):
        for project, gen_path in GENERATORS:
            with self.subTest(project=project):
                trees, receipts = self._generate_three_seeds(gen_path, project)
                # 1. File set identical across all three seeds
                base_seed = HASH_SEEDS[0]
                base_set = set(trees[base_seed])
                for seed in HASH_SEEDS[1:]:
                    self.assertEqual(
                        set(trees[seed]), base_set,
                        f"{project}: file set differs between seed={base_seed} "
                        f"and seed={seed}: only in {base_seed}="
                        f"{sorted(base_set - set(trees[seed]))}, only in "
                        f"{seed}={sorted(set(trees[seed]) - base_set)}",
                    )
                # 2. Every file byte-identical across all three seeds
                diffs = {}
                for rel in sorted(base_set):
                    hashes = {seed: trees[seed][rel] for seed in HASH_SEEDS}
                    unique = set(hashes.values())
                    if len(unique) != 1:
                        diffs[rel] = hashes
                self.assertEqual(
                    diffs, {},
                    f"{project}: {len(diffs)} file(s) differ across "
                    f"PYTHONHASHSEED values {HASH_SEEDS}: "
                    f"{dict(list(diffs.items())[:5])}",
                )
                # 3. snapshot_id stable across seeds (every receipt must agree)
                snap_ids = set()
                for seed in HASH_SEEDS:
                    sid = receipts[seed].get("snapshot_id")
                    if sid is not None:
                        snap_ids.add(sid)
                self.assertLessEqual(
                    len(snap_ids), 1,
                    f"{project}: snapshot_id changed across seeds: {snap_ids}",
                )

    def test_three_seed_manifest_matches_canonical_output_dir(self):
        """The subprocess output under each seed must also match the canonical
        on-disk generated output directory, so the canonical artifacts are
        provably equal to a fresh regeneration (not stale)."""
        canonical_dirs = {
            "dqcc": ROOT / "projects" / "data-quality-command-center" / "data" / "synthetic",
            "umgd": ROOT / "projects" / "urban-mobility-gap" / "data" / "synthetic",
            "pse": ROOT / "projects" / "peer-scenario-explorer" / "data" / "synthetic",
        }
        for project, gen_path in GENERATORS:
            with self.subTest(project=project):
                trees, _ = self._generate_three_seeds(gen_path, project)
                canon = _hash_tree(canonical_dirs[project])
                seed1 = HASH_SEEDS[0]
                sub = trees[seed1]
                self.assertEqual(
                    set(canon), set(sub),
                    f"{project}: canonical output file set != subprocess "
                    f"(seed={seed1}) file set. Only canonical: "
                    f"{sorted(set(canon) - set(sub))[:5]}. Only subprocess: "
                    f"{sorted(set(sub) - set(canon))[:5]}.",
                )
                diffs = {rel: (canon[rel], sub[rel]) for rel in canon
                         if canon[rel] != sub.get(rel)}
                self.assertEqual(
                    diffs, {},
                    f"{project}: canonical on-disk output differs from a fresh "
                    f"subprocess regeneration (seed={seed1}): {dict(list(diffs.items())[:5])}. "
                    f"The canonical artifacts may be stale; regenerate with "
                    f"shared/validation/regenerate_all.py.",
                )

    def test_no_builtin_hash_dependency_in_shared_or_generators(self):
        """A negative control: no generator or shared helper may call Python's
        built-in hash() on a string/tuple, because that hash is randomized
        per-process. This static check is the early-warning layer; the
        subprocess test above is the runtime proof."""
        import ast
        targets = [
            ROOT / "shared" / "synthetic" / "__init__.py",
            ROOT / "projects" / "data-quality-command-center" / "src" / "generate.py",
            ROOT / "projects" / "urban-mobility-gap" / "src" / "generate.py",
            ROOT / "projects" / "peer-scenario-explorer" / "src" / "generate.py",
        ]
        for path in targets:
            with self.subTest(path=path.relative_to(ROOT).as_posix()):
                src = path.read_text(encoding="utf-8")
                tree = ast.parse(src, filename=str(path))
                bad = []
                for node in ast.walk(tree):
                    # bare `hash(...)` call -- not hashlib.sha256 / .hash()
                    if (isinstance(node, ast.Call)
                            and isinstance(node.func, ast.Name)
                            and node.func.id == "hash"):
                        bad.append(node.lineno)
                self.assertEqual(
                    bad, [],
                    f"{path}: built-in hash() call at line(s) {bad} is not "
                    f"seed-stable across processes; use shared.synthetic."
                    f"derive_seed / hashlib instead.",
                )


if __name__ == "__main__":
    unittest.main(verbosity=2)
