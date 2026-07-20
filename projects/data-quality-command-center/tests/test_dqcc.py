"""Tests for the Data Quality Command Center synthetic project.

Positive control: a freshly generated output set is passed through the real
``validate.py`` validator and must PASS every invariant.

Negative fixtures: each test mutates a real generated artifact, then passes
the mutated directory through the SAME ``validate.py`` validator and asserts a
specific stable rejection ``code``. No negative test asserts a fixed local
constant is bad; every rejection comes from the production validator running on
current bytes.

Cross-process determinism: two genuinely separate Python processes with
explicit ``PYTHONHASHSEED=1`` and ``PYTHONHASHSEED=2`` each generate into a
fresh temp directory, and every relative path and SHA-256 must match.

SYNTHETIC DATA / INDEPENDENT PORTFOLIO PROJECT
"""
from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

# Load the project generator and validator by file path (the project dir has
# hyphens, so it is not a normal importable package name).
_GEN_PATH = Path(__file__).resolve().parents[1] / "src" / "generate.py"
_VAL_PATH = Path(__file__).resolve().parents[1] / "src" / "validate.py"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


gen = _load(_GEN_PATH, "dqcc_generate")
val = _load(_VAL_PATH, "dqcc_validate")

# Shared enums (for positive-control enum assertions)
shared_syn = _load(ROOT / "shared" / "synthetic" / "__init__.py", "_shared_syn")


def _load_csv(path: Path) -> List[Dict[str, Any]]:
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _write_csv(path: Path, header: List[str], rows: List[Dict[str, Any]]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        # extrasaction="ignore" lets us rewrite a file with a reduced header
        # (for header-mismatch negative fixtures) without raising on the extra
        # keys present in the original rows.
        w = csv.DictWriter(f, fieldnames=header, lineterminator="\n",
                           extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


# =========================================================================
# Positive fixture: the canonical generation must PASS the real validator.
# =========================================================================

class TestPositiveFixture(unittest.TestCase):
    """Verify the well-formed canonical output passes the real validator."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="dqcc_test_")
        cls.out = Path(cls.tmp)
        cls.receipt = gen.generate(cls.out)
        cls.validation = val.validate(cls.out)

    def _csv(self, name):
        return _load_csv(self.out / name)

    def test_validator_passes_canonical_output(self):
        # The real validator must return a PASS receipt on the canonical build.
        self.assertEqual(self.validation["status"], "PASS")
        self.assertTrue(self.validation["checks_passed"],
                        "validator must record the checks it actually ran")

    def test_all_boolean_invariants_hold(self):
        ev = self.validation["evidence"]
        for name in ("inventory_key_unique", "issue_key_unique",
                     "lineage_composite_key_unique", "lineage_no_orphans",
                     "lineage_sequences_contiguous", "queue_total_ordered",
                     "schema_invariant", "manifest_complete",
                     "manifest_hash_matches_bytes"):
            self.assertTrue(ev[name], f"invariant {name} must be True")

    def test_typed_duplicate_count_is_zero_and_computed(self):
        # The typed-duplicate count is recomputed from current rows by the
        # validator; for the canonical synthetic set it must be 0.
        self.assertEqual(self.validation["evidence"]["typed_duplicate_count"], 0)

    def test_manifest_hash_matches_recomputed_bytes(self):
        self.assertEqual(
            self.validation["stored_manifest_sha256"],
            self.validation["recomputed_manifest_sha256"],
            "stored manifest hash must equal the recomputed-from-bytes hash",
        )

    def test_row_counts_match_receipt(self):
        rc = self.receipt["row_counts"]
        self.assertEqual(len(self._csv("source_inventory.csv")), rc["source_inventory"])
        self.assertEqual(len(self._csv("schema_unit_freshness_issue.csv")), rc["schema_unit_freshness_issue"])
        self.assertEqual(len(self._csv("issue_lineage.csv")), rc["issue_lineage"])
        self.assertEqual(len(self._csv("refresh_priority_queue.csv")), rc["refresh_priority_queue"])

    def test_lineage_covers_every_issue_exactly(self):
        issues = self._csv("schema_unit_freshness_issue.csv")
        lineage = self._csv("issue_lineage.csv")
        issue_ids = {r["issue_id"] for r in issues}
        lineage_issue_ids = {r["issue_id"] for r in lineage}
        self.assertEqual(issue_ids, lineage_issue_ids,
                         "lineage issue_id set must equal issue issue_id set")
        per_issue = {}
        for r in lineage:
            per_issue.setdefault(r["issue_id"], set()).add(int(r["lineage_sequence"]))
        for iid in issue_ids:
            seqs = per_issue.get(iid, set())
            self.assertEqual(seqs, set(range(1, len(seqs) + 1)),
                             f"lineage sequences for {iid} must be contiguous 1..N")

    def test_priority_queue_is_total_order_desc(self):
        q = self._csv("refresh_priority_queue.csv")
        ranks = [int(r["priority_rank"]) for r in q]
        self.assertEqual(ranks, list(range(1, len(q) + 1)),
                         "priority ranks must be contiguous 1..N")
        keys = []
        for r in q:
            keys.append((
                -int(r["priority_publication_blocker"]),
                -int(r["priority_multi_scope_impact"]),
                -int(r["priority_reproducible_current_failure"]),
                -int(r["priority_affected_typed_key_count"]),
                -int(r["priority_older_eligible_rank"]),
                r["issue_id"],
            ))
        self.assertEqual(keys, sorted(keys),
                         "priority queue must be total-sorted by tuple")

    def test_no_score_column_anywhere(self):
        for csvname in ["source_inventory.csv", "schema_unit_freshness_issue.csv",
                        "issue_lineage.csv", "refresh_priority_queue.csv"]:
            with open(self.out / csvname, encoding="utf-8") as f:
                header = next(csv.reader(f))
            for col in header:
                self.assertNotIn("score", col.lower(),
                                 f"{csvname}: blended 'score' column is prohibited: {col}")

    def test_remediation_readback_starts_with_pass(self):
        rem = self._csv("remediation_receipt.csv")
        self.assertEqual(len(rem), 1)
        self.assertTrue(rem[0]["same_operation_readback"].startswith("PASS"),
                        f"readback must start with PASS, got: {rem[0]['same_operation_readback']}")

    def test_evidence_and_owner_action_nonempty(self):
        for r in self._csv("schema_unit_freshness_issue.csv"):
            self.assertTrue(r["evidence_value"].strip(), f"{r['issue_id']} evidence empty")
            self.assertTrue(r["priority_reason"].strip(), f"{r['issue_id']} reason empty")
            self.assertTrue(r["owner_action"].strip(), f"{r['issue_id']} owner_action empty")

    def test_hashes_match_64_hex(self):
        for r in self._csv("issue_lineage.csv"):
            self.assertRegex(r["schema_signature_sha256"], r"^[0-9a-f]{64}$")
            self.assertRegex(r["source_sha256"], r"^[0-9a-f]{64}$")

    def test_unit_and_family_enums(self):
        for r in self._csv("source_inventory.csv"):
            self.assertIn(r["record_family"], shared_syn.RECORD_FAMILY_ENUM)

    def test_json_receipt_parses(self):
        with open(self.out / "build_receipt.json", encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["project_id"], gen.PROJECT_ID)
        self.assertIn("snapshot_id", data)

    def test_display_text_is_text_not_color_only(self):
        for r in self._csv("refresh_priority_queue.csv"):
            self.assertIn("-", r["display_status"])
            self.assertTrue(r["display_status"].strip())

    def test_guard_issues_labeled_synthetic_control(self):
        # Verified-guard issue rows must be labeled as synthetic validation
        # controls, not historical production events.
        for r in self._csv("schema_unit_freshness_issue.csv"):
            if r["severity"] == "Verified guard":
                self.assertIn("synthetic validation control",
                              r["issue_title"].lower(),
                              f"{r['issue_id']} guard must be labeled as a "
                              "synthetic validation control")


# =========================================================================
# Negative fixtures: mutate a real artifact, run the real validator, assert
# a specific stable rejection code.
# =========================================================================

class TestNegativeFixtures(unittest.TestCase):
    """Each test mutates a generated artifact and asserts the real validator
    rejects it with a specific code."""

    def setUp(self):
        # Fresh canonical build for each negative test so mutations are isolated.
        self.tmp = tempfile.mkdtemp(prefix="dqcc_neg_")
        self.out = Path(self.tmp)
        gen.generate(self.out)
        self.source_dir = self.out / "_source_workbooks"
        # Sanity: the unmutated build must validate before we mutate.
        self.assertEqual(val.validate(self.out)["status"], "PASS")

    def _expect_reject(self, code: str) -> None:
        """Run the real validator and assert it raises with the expected code."""
        with self.assertRaises(val.ValidationError) as cm:
            val.validate(self.out)
        self.assertEqual(
            cm.exception.code, code,
            f"expected rejection code {code!r}, got {cm.exception.code!r} "
            f"(detail: {cm.exception.detail})",
        )

    # --- Workbook-level mutations ---------------------------------------

    def test_neg_missing_required_source_workbook(self):
        (self.source_dir / gen.WORKBOOK_NAMES[0]).unlink()
        self._expect_reject("MISSING_REQUIRED_FILE")

    def test_neg_changed_schema_header(self):
        # Rewrite a workbook with a wrong header. The schema-signature check
        # (computed from the live header) and the source_sha256-vs-bytes check
        # must reject. Either SCHEMA_SIGNATURE_MISMATCH or MANIFEST_HASH_MISMATCH
        # is acceptable because both are derived from the mutated header bytes.
        wb = self.source_dir / gen.WORKBOOK_NAMES[0]
        with open(wb, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f, lineterminator="\n")
            w.writerow(["WRONG_HEADER"])
            w.writerow(["x"])
        with self.assertRaises(val.ValidationError) as cm:
            val.validate(self.out)
        self.assertIn(cm.exception.code,
                      {"SCHEMA_SIGNATURE_MISMATCH", "MANIFEST_HASH_MISMATCH",
                       "HEADER_MISMATCH"})

    # --- Coordinated fail-closed invariant fixtures ---------------------
    # These three fixtures reproduce the manager-review fail-open class: an
    # attacker mutates the underlying invariant AND simultaneously rewrites
    # every byte-derived supporting hash (refresh_control manifest hash,
    # per-lineage source_sha256, build_receipt) so the byte-hash checks would
    # otherwise stay green. The validator MUST still reject because it
    # re-derives the invariant itself from current bytes and enforces it
    # fail-closed, never trusting a stored hash or stored evidence string.

    def test_neg_coordinated_typed_key_duplicate(self):
        """Manager fail-open #1: duplicate a typed row, then rewrite every
        supporting hash so only the recomputed typed-grain invariant can catch
        it. Must reject with the exact stable code TYPED_KEY_DUPLICATE."""
        wb = self.source_dir / gen.WORKBOOK_NAMES[0]
        # 1) Introduce an EXACT typed-row duplicate (full grain collision).
        with open(wb, newline="", encoding="utf-8") as f:
            rd = list(csv.reader(f))
        header, rows = rd[0], rd[1:]
        rows.append(list(rows[0]))  # exact duplicate -> typed grain collision
        with open(wb, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f, lineterminator="\n")
            w.writerow(header)
            w.writerows(rows)
        # 2) Coordinate: rewrite refresh_control.source_manifest_sha256 to the
        #    mutated-bytes manifest so the manifest-hash check stays green.
        self._rewrite_manifest_hash_to_current_bytes()
        # 3) Coordinate: rewrite every lineage.source_sha256 pointing at the
        #    mutated workbook so the per-lineage source_sha256 check stays green.
        self._rewrite_lineage_source_hashes_to_current_bytes(gen.WORKBOOK_NAMES[0])
        # 4) The byte-derived checks no longer fire; only the fail-closed
        #    recomputed typed-grain invariant can catch this. Require its exact
        #    stable rejection code.
        self._expect_reject("TYPED_KEY_DUPLICATE")

    def test_neg_coordinated_schema_not_invariant(self):
        """Manager fail-open #2: mutate a workbook header to a different (but
        still 8-column, still byte-hash-coordinated) layout, then rewrite the
        per-lineage source_sha256 AND the schema_signature_sha256 to match the
        mutated bytes. The header no longer equals the canonical header, so the
        fail-closed schema-invariance check must reject with SCHEMA_NOT_INVARIANT
        even though every stored hash agrees with the mutated bytes."""
        wb = self.source_dir / gen.WORKBOOK_NAMES[0]
        with open(wb, newline="", encoding="utf-8") as f:
            rd = list(csv.reader(f))
        header, rows = rd[0], rd[1:]
        # Swap two column names so the column COUNT is unchanged (keeps row
        # arity valid) but the header no longer matches WORKBOOK_HEADER. This
        # is the exact coordinated case: bytes are internally consistent, only
        # the canonical-header comparison catches it.
        mutated_header = list(header)
        mutated_header[0], mutated_header[1] = mutated_header[1], mutated_header[0]
        with open(wb, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f, lineterminator="\n")
            w.writerow(mutated_header)
            w.writerows(rows)
        # Coordinate: rewrite the per-lineage source_sha256 and the
        # schema_signature_sha256 for the mutated workbook to match mutated
        # bytes/header, so the byte-derived hash checks stay green.
        self._rewrite_lineage_source_hashes_to_current_bytes(gen.WORKBOOK_NAMES[0])
        self._rewrite_lineage_schema_signatures_to_current_header(
            gen.WORKBOOK_NAMES[0], mutated_header)
        self._rewrite_manifest_hash_to_current_bytes()
        # Only the fail-closed canonical-header comparison can catch this.
        self._expect_reject("SCHEMA_NOT_INVARIANT")

    def test_neg_coordinated_manifest_incomplete(self):
        """Manager fail-open #3: add an EXTRA workbook file to the source
        directory so the present set != expected set, then rewrite the manifest
        hash so the byte-derived manifest-hash check stays green. The fail-closed
        expected-vs-present set comparison must reject with MANIFEST_INCOMPLETE
        even though the stored hash agrees with the mutated bytes."""
        extra = self.source_dir / "source_pack_99.csv"
        # Copy an existing workbook so the extra file is a well-formed CSV with
        # the canonical header (avoids tripping a header check on the extra
        # file; the point is set-completeness, not header validity of the extra).
        src = self.source_dir / gen.WORKBOOK_NAMES[1]
        extra.write_bytes(src.read_bytes())
        # Coordinate: rewrite refresh_control.source_manifest_sha256 to the
        # mutated-bytes manifest (which now includes the extra workbook) so the
        # manifest-hash-vs-bytes check stays green.
        self._rewrite_manifest_hash_to_current_bytes()
        # Only the fail-closed expected-vs-present set comparison catches this.
        self._expect_reject("MANIFEST_INCOMPLETE")

    # --- Coordinated-mutation helpers ----------------------------------

    def _rewrite_manifest_hash_to_current_bytes(self) -> None:
        """Overwrite refresh_control.source_manifest_sha256 with the manifest
        hash recomputed from the CURRENT workbook bytes, so the byte-derived
        manifest-hash check stays green after a workbook mutation."""
        new_manifest = val._expected_manifest_hash(self.source_dir, gen.WORKBOOK_NAMES)
        path = self.out / "refresh_control.csv"
        rows = _load_csv(path)
        rows[0]["source_manifest_sha256"] = new_manifest
        header = list(rows[0].keys())
        _write_csv(path, header, rows)

    def _rewrite_lineage_source_hashes_to_current_bytes(self, wb_name: str) -> None:
        """Overwrite every lineage.source_sha256 whose source_workbook ==
        wb_name with the CURRENT file hash of that workbook, so the per-lineage
        source_sha256-vs-bytes check stays green after a workbook mutation."""
        cur = hashlib.sha256((self.source_dir / wb_name).read_bytes()).hexdigest()
        path = self.out / "issue_lineage.csv"
        rows = _load_csv(path)
        for r in rows:
            if r["source_workbook"] == wb_name:
                r["source_sha256"] = cur
        header = list(rows[0].keys())
        _write_csv(path, header, rows)

    def _rewrite_lineage_schema_signatures_to_current_header(
            self, wb_name: str, header: List[str]) -> None:
        """Overwrite every lineage.schema_signature_sha256 whose
        source_workbook == wb_name with the signature recomputed from the
        CURRENT mutated header, so the byte-derived schema-signature check
        stays green after a coordinated header mutation."""
        import hashlib as _hl
        sig = _hl.sha256(
            f"schema|data-quality-command-center|{wb_name}|".encode()
            + "|".join(header).encode()
        ).hexdigest()
        path = self.out / "issue_lineage.csv"
        rows = _load_csv(path)
        for r in rows:
            if r["source_workbook"] == wb_name:
                r["schema_signature_sha256"] = sig
        hdr = list(rows[0].keys())
        _write_csv(path, hdr, rows)

    def test_neg_tampered_workbook_bytes(self):
        # Append a tamper line to a workbook. The source_sha256 stored in
        # lineage was computed from the pre-tamper bytes, so the validator's
        # "source_sha256 matches current bytes" check must reject.
        with open(self.source_dir / gen.WORKBOOK_NAMES[0], "a", encoding="utf-8") as f:
            f.write("tampered\n")
        self._expect_reject("MANIFEST_HASH_MISMATCH")

    def test_neg_manifest_hash_drift(self):
        # Tamper a workbook AND verify the stored refresh_control hash no longer
        # equals the recomputed manifest hash (the dedicated manifest check).
        with open(self.source_dir / gen.WORKBOOK_NAMES[0], "a", encoding="utf-8") as f:
            f.write("tampered\n")
        # The stored source_sha256 check fires first; verify the manifest hash
        # has actually drifted by recomputation as well.
        expected = val._expected_manifest_hash(self.source_dir, gen.WORKBOOK_NAMES)
        rc = _load_csv(self.out / "refresh_control.csv")[0]
        self.assertNotEqual(rc["source_manifest_sha256"], expected,
                            "tampered workbook must change the recomputed manifest hash")

    # --- Output-table mutations -----------------------------------------

    def test_neg_missing_required_output_file(self):
        (self.out / "source_inventory.csv").unlink()
        self._expect_reject("MISSING_REQUIRED_FILE")

    def test_neg_inventory_header_mismatch(self):
        path = self.out / "source_inventory.csv"
        rows = _load_csv(path)
        _write_csv(path, ["record_family", "record_code"], rows)
        self._expect_reject("HEADER_MISMATCH")

    def test_neg_inventory_enum_violation(self):
        path = self.out / "source_inventory.csv"
        rows = _load_csv(path)
        rows[0]["record_family"] = "classified"  # not in RECORD_FAMILY_ENUM
        header = list(rows[0].keys())
        _write_csv(path, header, rows)
        self._expect_reject("ENUM_VIOLATION")

    def test_neg_inventory_bad_reconciliation_state(self):
        path = self.out / "source_inventory.csv"
        rows = _load_csv(path)
        rows[0]["disaggregation_state"] = "PARTIAL"  # not accepted
        header = list(rows[0].keys())
        _write_csv(path, header, rows)
        self._expect_reject("BAD_RECONCILIATION_STATE")

    def test_neg_inventory_empty_required_field(self):
        path = self.out / "source_inventory.csv"
        rows = _load_csv(path)
        rows[0]["record_code"] = ""
        header = list(rows[0].keys())
        _write_csv(path, header, rows)
        self._expect_reject("EMPTY_REQUIRED_FIELD")

    def test_neg_inventory_key_duplicate(self):
        path = self.out / "source_inventory.csv"
        rows = _load_csv(path)
        # Duplicate the first row's (family, code) by appending a copy.
        dup = dict(rows[0])
        dup["inventory_decision_note"] = "DUPLICATE ROW FOR TEST"
        rows.append(dup)
        header = list(rows[0].keys())
        _write_csv(path, header, rows)
        self._expect_reject("INVENTORY_KEY_DUPLICATE")

    def test_neg_issue_key_duplicate(self):
        path = self.out / "schema_unit_freshness_issue.csv"
        rows = _load_csv(path)
        dup = dict(rows[0])
        rows.append(dup)
        header = list(rows[0].keys())
        _write_csv(path, header, rows)
        self._expect_reject("ISSUE_KEY_DUPLICATE")

    def test_neg_issue_enum_violation(self):
        path = self.out / "schema_unit_freshness_issue.csv"
        rows = _load_csv(path)
        rows[0]["observation_type"] = "guessed"  # not in OBSERVATION_TYPE_ENUM
        header = list(rows[0].keys())
        _write_csv(path, header, rows)
        self._expect_reject("ENUM_VIOLATION")

    def test_neg_lineage_composite_key_duplicate(self):
        path = self.out / "issue_lineage.csv"
        rows = _load_csv(path)
        dup = dict(rows[0])
        rows.append(dup)
        header = list(rows[0].keys())
        _write_csv(path, header, rows)
        self._expect_reject("LINEAGE_COMPOSITE_KEY_DUPLICATE")

    def test_neg_lineage_orphan_issue(self):
        path = self.out / "issue_lineage.csv"
        rows = _load_csv(path)
        rows[0]["issue_id"] = "ISS-NONEXISTENT"
        header = list(rows[0].keys())
        _write_csv(path, header, rows)
        self._expect_reject("LINEAGE_ORPHAN_ISSUE")

    def test_neg_lineage_sequence_gap(self):
        path = self.out / "issue_lineage.csv"
        rows = _load_csv(path)
        # Force a gap on the first issue by renumbering only its first lineage
        # row to 99 (leaving the remaining rows at 2,3,...). The composite keys
        # stay unique, but the sequences for that issue are no longer 1..N.
        first_issue = rows[0]["issue_id"]
        rows[0]["lineage_sequence"] = "99"
        header = list(rows[0].keys())
        _write_csv(path, header, rows)
        self._expect_reject("LINEAGE_SEQUENCE_GAP")

    def test_neg_lineage_missing_issue(self):
        # Delete all lineage rows for one issue so it has no lineage.
        path = self.out / "issue_lineage.csv"
        rows = _load_csv(path)
        first_issue = rows[0]["issue_id"]
        rows = [r for r in rows if r["issue_id"] != first_issue]
        if not rows:
            self.skipTest("cannot construct missing-issue case on single-issue build")
        header_path = self.out / "issue_lineage.csv"
        with open(header_path, encoding="utf-8") as f:
            header = next(csv.reader(f))
        _write_csv(header_path, header, rows)
        self._expect_reject("LINEAGE_MISSING_ISSUE")

    def test_neg_lineage_stored_hash_not_hex(self):
        path = self.out / "issue_lineage.csv"
        rows = _load_csv(path)
        rows[0]["source_sha256"] = "not-a-hex-string"
        header = list(rows[0].keys())
        _write_csv(path, header, rows)
        self._expect_reject("STORED_HASH_NOT_64_HEX")

    def test_neg_queue_rank_not_contiguous(self):
        path = self.out / "refresh_priority_queue.csv"
        rows = _load_csv(path)
        rows[0]["priority_rank"] = "99"
        header = list(rows[0].keys())
        _write_csv(path, header, rows)
        self._expect_reject("QUEUE_RANK_NOT_CONTIGUOUS")

    def test_neg_queue_not_total_ordered(self):
        path = self.out / "refresh_priority_queue.csv"
        rows = _load_csv(path)
        # Inflate a non-rank-1 row's publication_blocker so its tuple would now
        # sort ahead of the stored rank-1 row, breaking the stored rank order
        # without re-ranking. (Mutating the rank-1 row alone can preserve the
        # total order by coincidence.)
        rows[1]["priority_publication_blocker"] = "9"
        header = list(rows[0].keys())
        _write_csv(path, header, rows)
        self._expect_reject("QUEUE_NOT_TOTAL_ORDERED")

    def test_neg_remediation_target_missing(self):
        path = self.out / "remediation_receipt.csv"
        rows = _load_csv(path)
        rows[0]["issue_id"] = "ISS-NONEXISTENT"
        header = list(rows[0].keys())
        _write_csv(path, header, rows)
        self._expect_reject("REMEDIATION_TARGET_MISSING")

    def test_neg_remediation_readback_not_pass(self):
        path = self.out / "remediation_receipt.csv"
        rows = _load_csv(path)
        rows[0]["same_operation_readback"] = "FAIL rebuild read back 1 duplicate"
        header = list(rows[0].keys())
        _write_csv(path, header, rows)
        self._expect_reject("REMEDIATION_READBACK_NOT_PASS")

    def test_neg_refresh_control_manifest_hash_drift(self):
        # Recompute-then-overwrite the stored manifest hash with a wrong value
        # while keeping source bytes unchanged, so the manifest-hash-vs-bytes
        # check specifically fires (source_sha256 in lineage still matches).
        path = self.out / "refresh_control.csv"
        rows = _load_csv(path)
        rows[0]["source_manifest_sha256"] = "0" * 64
        header = list(rows[0].keys())
        _write_csv(path, header, rows)
        self._expect_reject("MANIFEST_HASH_MISMATCH")


# =========================================================================
# Cross-process determinism: two genuinely separate Python processes with
# explicit PYTHONHASHSEED values must produce byte-identical file sets.
# Same-process module reload is insufficient (it cannot detect the built-in
# hash() drift that motivated this correction).
# =========================================================================

class TestCrossProcessDeterminism(unittest.TestCase):
    """Generate in two separate processes (PYTHONHASHSEED=1 and =2) and
    compare every relative path and SHA-256."""

    def _generate_in_subprocess(self, seed: int, out_dir: Path) -> None:
        env = dict(os.environ)
        env["PYTHONHASHSEED"] = str(seed)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        # A self-contained snippet that loads the generator by file path and
        # generates into out_dir. This defeats any same-process caching and
        # exercises the real per-process hash-seed environment.
        script = (
            "import importlib.util, pathlib, sys\n"
            f"out = pathlib.Path({str(out_dir)!r})\n"
            f"spec = importlib.util.spec_from_file_location('g', {str(_GEN_PATH)!r})\n"
            "m = importlib.util.module_from_spec(spec)\n"
            "spec.loader.exec_module(m)\n"
            "m.generate(out)\n"
        )
        result = subprocess.run(
            [sys.executable, "-B", "-c", script],
            env=env, capture_output=True, text=True,
        )
        if result.returncode != 0:
            self.fail(
                f"subprocess generation failed (seed={seed}, rc={result.returncode})\n"
                f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
            )

    def _hash_tree(self, root: Path) -> Dict[str, str]:
        out: Dict[str, str] = {}
        for p in sorted(root.rglob("*")):
            if p.is_file() and not p.is_symlink():
                rel = p.relative_to(root).as_posix()
                h = hashlib.sha256()
                with open(p, "rb") as f:
                    h.update(f.read())
                out[rel] = h.hexdigest()
        return out

    def test_pythonhashseed_1_vs_2_byte_identical(self):
        t1 = Path(tempfile.mkdtemp(prefix="dqcc_proc1_"))
        t2 = Path(tempfile.mkdtemp(prefix="dqcc_proc2_"))
        try:
            self._generate_in_subprocess(1, t1)
            self._generate_in_subprocess(2, t2)
            h1 = self._hash_tree(t1)
            h2 = self._hash_tree(t2)
            self.assertEqual(set(h1), set(h2),
                             "file set must be identical across processes")
            diffs = {k for k in h1 if h1[k] != h2[k]}
            self.assertEqual(diffs, set(),
                             f"cross-process hash differences (must be zero): "
                             f"{sorted(diffs)}")
        finally:
            import shutil
            shutil.rmtree(t1, ignore_errors=True)
            shutil.rmtree(t2, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
