"""Structure scan: enforce file-extension, size, symlink, and validation-record
constraints.

Required by the release contract:
- exactly one approved .twbx and one approved .jpeg per project
- zero unapproved native analytics, archive, or office-document files
- zero files > 10 MiB
- zero symlinks
- no hidden files except .gitignore
- `validation/INCLUDED_FILES_SHA256.md` exists, is well-formed, and every
  listed hash verifies against the current on-disk bytes (fail closed on
  missing, malformed, stale, or self-inconsistent manifest)
- `validation/VALIDATION.md` exists and is non-empty (fail closed on absence)

The previous version checked the manifest only if it already existed and
never asserted either required file existed. That could pass on an empty
validation directory. This version fails closed on absence or staleness.
"""
from __future__ import annotations

import hashlib
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

MAX_BYTES = 10 * 1024 * 1024  # 10 MiB

CONTROLLED_EXTENSIONS = {
    ".twbx", ".twb", ".hyper", ".pdf", ".png", ".jpeg", ".jpg",
    ".tab", ".xlsx", ".docx", ".zip", ".tar", ".gz", ".tgz", ".7z", ".rar",
}

ALLOWED_HIDDEN = {".gitignore", ".gitattributes"}

ALLOWED_CONTROLLED_FILES = {
    "projects/data-quality-command-center/tableau/Data_Quality_Refresh_Command_Center_SYNTHETIC_PORTFOLIO.twbx",
    "projects/data-quality-command-center/images/dashboard-preview.jpeg",
    "projects/urban-mobility-gap/tableau/Urban_Mobility_Gap_Diagnostic_SYNTHETIC_PORTFOLIO.twbx",
    "projects/urban-mobility-gap/images/dashboard-preview.jpeg",
    "projects/peer-scenario-explorer/tableau/Peer_Scenario_Stability_Explorer_SYNTHETIC_PORTFOLIO.twbx",
    "projects/peer-scenario-explorer/images/dashboard-preview.jpeg",
}
# The cooperative staging lock uses a non-hidden filename and is gitignored,
# so it does not appear in the hidden-files check.

MANIFEST_PATH = ROOT / "validation" / "INCLUDED_FILES_SHA256.md"
VALIDATION_RECORD_PATH = ROOT / "validation" / "VALIDATION.md"

# Files that build_manifest.py excludes from the inclusion manifest. These
# must match the builder's exclusion rules exactly so the structure test can
# detect a manifest that is stale relative to the current tracked-file set.
MANIFEST_EXCLUDE_DIRS = {".git", "__pycache__", ".pytest_cache", ".mypy_cache",
                         ".locks"}
MANIFEST_EXCLUDE_FILENAMES = {".DS_Store", "STAGING_LOCK.md",
                              "LICENSE_PENDING.md"}
MANIFEST_SELF_REL = "validation/INCLUDED_FILES_SHA256.md"

# Lines that match a manifest entry: <64-hex> | path | size | hash.
# MULTILINE so ^/$ match each table row, not just the whole document.
_MANIFEST_ROW_RE = re.compile(
    r"^\|\s*`([^`]+)`\s*\|\s*(\d+)\s*\|\s*`([0-9a-f]{64})`\s*\|\s*$",
    re.MULTILINE)


def _iter_all_files():
    for path in sorted(ROOT.rglob("*")):
        if path.is_file() and ".git" not in path.relative_to(ROOT).parts:
            yield path


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _manifest_should_include(path: Path) -> bool:
    """Mirror build_manifest.py's _should_skip, returning True if the path
    SHOULD appear in the inclusion manifest."""
    parts = set(path.relative_to(ROOT).parts)
    if parts & MANIFEST_EXCLUDE_DIRS:
        return False
    if path.name in MANIFEST_EXCLUDE_FILENAMES:
        return False
    if path.suffix.lower() == ".log":
        return False
    rel = path.relative_to(ROOT)
    # Skip generated synthetic data (reproducible from the generators).
    if (len(rel.parts) >= 3 and rel.parts[0] == "projects"
            and rel.parts[2] == "data"):
        return False
    if rel.as_posix() == MANIFEST_SELF_REL:
        return False
    return True


def _expected_manifest_paths() -> set:
    """The set of relative paths that must appear in the manifest, derived
    from the current on-disk tracked-file set."""
    out = set()
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        if _manifest_should_include(path):
            out.add(path.relative_to(ROOT).as_posix())
    return out


class TestStructureScan(unittest.TestCase):

    def test_only_approved_controlled_files(self):
        bad = []
        for path in _iter_all_files():
            rel = path.relative_to(ROOT).as_posix()
            if (path.suffix.lower() in CONTROLLED_EXTENSIONS
                    and rel not in ALLOWED_CONTROLLED_FILES):
                bad.append(rel)
        self.assertEqual(bad, [], f"unapproved controlled files present: {bad}")
        missing = ALLOWED_CONTROLLED_FILES - {
            p.relative_to(ROOT).as_posix() for p in _iter_all_files()
        }
        self.assertEqual(missing, set(), f"approved release files missing: {missing}")

    def test_no_file_exceeds_10mib(self):
        bad = []
        for path in _iter_all_files():
            size = path.stat().st_size
            if size > MAX_BYTES:
                bad.append((path.relative_to(ROOT).as_posix(), size))
        self.assertEqual(bad, [], f"files exceed 10 MiB: {bad}")

    def test_no_symlinks(self):
        bad = []
        for path in ROOT.rglob("*"):
            if path.is_symlink():
                if ".git" in path.relative_to(ROOT).parts:
                    continue
                bad.append(path.relative_to(ROOT).as_posix())
        self.assertEqual(bad, [], f"symlinks present: {bad}")

    def test_no_unexpected_hidden_files(self):
        bad = []
        for path in _iter_all_files():
            name = path.name
            if name.startswith(".") and name not in ALLOWED_HIDDEN:
                bad.append(path.relative_to(ROOT).as_posix())
        self.assertEqual(bad, [], f"unexpected hidden files: {bad}")

    def test_required_top_level_files_present(self):
        required = ["README.md", ".gitignore", ".gitattributes", "LICENSE",
                    "NOTICE", "SOURCE_AND_USAGE_POLICY.md", "requirements.txt"]
        for name in required:
            self.assertTrue((ROOT / name).exists(), f"missing required file: {name}")

    def test_required_per_project_readme_and_generator_present(self):
        for proj in ["data-quality-command-center", "urban-mobility-gap",
                     "peer-scenario-explorer"]:
            self.assertTrue((ROOT / "projects" / proj / "README.md").exists(),
                            f"missing README for {proj}")
            src = list((ROOT / "projects" / proj / "src").glob("generate.py"))
            self.assertEqual(len(src), 1, f"expected one generate.py for {proj}")

    def test_required_docs_present(self):
        for doc in ["PORTFOLIO_ARCHITECTURE.md", "DISCLOSURE_MATRIX.md",
                    "FUTURE_TABLEAU_REBUILD_PLAN.md"]:
            self.assertTrue((ROOT / "docs" / doc).exists(),
                            f"missing doc: {doc}")


class TestValidationRecordAndManifest(unittest.TestCase):
    """Fail closed when validation/VALIDATION.md or the inclusion manifest is
    missing, malformed, stale, or self-inconsistent. The previous version
    checked the manifest only when it already existed and never asserted
    either required file existed."""

    def test_validation_record_exists_and_is_nonempty(self):
        # VALIDATION.md is required by the staging contract. Absence means
        # validation has not been run / recorded. Fail closed on absence or
        # an empty placeholder.
        self.assertTrue(
            VALIDATION_RECORD_PATH.exists(),
            "validation/VALIDATION.md is required and is absent. "
            "Run the Wave 2 reconciliation and write the validation record.",
        )
        text = VALIDATION_RECORD_PATH.read_text(encoding="utf-8").strip()
        self.assertGreater(
            len(text), 0,
            "validation/VALIDATION.md exists but is empty.",
        )
        # Must actually look like a validation record, not a stub.
        for marker in ["#", "SHA-256", "PYTHONHASHSEED"]:
            self.assertIn(
                marker, text,
                f"validation/VALIDATION.md is missing expected section "
                f"marker {marker!r}; it may be a stub.",
            )

    def test_inclusion_manifest_exists_and_is_well_formed(self):
        self.assertTrue(
            MANIFEST_PATH.exists(),
            "validation/INCLUDED_FILES_SHA256.md is required and is absent. "
            "Run shared/validation/build_manifest.py.",
        )
        text = MANIFEST_PATH.read_text(encoding="utf-8")
        rows = _MANIFEST_ROW_RE.findall(text)
        self.assertGreater(
            len(rows), 0,
            "manifest contains no well-formed rows (malformed).",
        )

    def test_every_manifest_entry_verifies_against_current_bytes(self):
        # Fail closed if any listed file is missing or its hash changed since
        # the manifest was built (stale manifest).
        self.assertTrue(MANIFEST_PATH.exists(), "manifest absent")
        text = MANIFEST_PATH.read_text(encoding="utf-8")
        rows = _MANIFEST_ROW_RE.findall(text)
        self.assertGreater(len(rows), 0, "manifest malformed")
        mismatches = []
        for rel, size, expected_hash in rows:
            path = ROOT / rel
            if not path.exists():
                mismatches.append((rel, "MISSING"))
                continue
            actual = _sha256(path)
            if actual != expected_hash:
                mismatches.append((rel, f"hash {expected_hash[:10]}.. != {actual[:10]}.."))
        self.assertEqual(
            mismatches, [],
            f"manifest is stale or self-inconsistent: {len(mismatches)} "
            f"entr(ies) do not verify: {mismatches[:5]}. Rebuild with "
            f"shared/validation/build_manifest.py.",
        )

    def test_manifest_file_set_matches_current_tracked_files(self):
        # Fail closed if the manifest is missing tracked files or lists
        # files that no longer exist / are now excluded (self-inconsistent).
        self.assertTrue(MANIFEST_PATH.exists(), "manifest absent")
        text = MANIFEST_PATH.read_text(encoding="utf-8")
        listed = {rel for rel, _, _ in _MANIFEST_ROW_RE.findall(text)}
        expected = _expected_manifest_paths()
        missing = expected - listed
        extra = listed - expected
        self.assertEqual(
            missing, set(),
            f"manifest is stale: {len(missing)} tracked file(s) not listed: "
            f"{sorted(missing)[:5]}. Rebuild with build_manifest.py.",
        )
        self.assertEqual(
            extra, set(),
            f"manifest is stale: {len(extra)} listed file(s) no longer "
            f"tracked: {sorted(extra)[:5]}. Rebuild with build_manifest.py.",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
