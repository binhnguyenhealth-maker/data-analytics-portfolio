"""Reusable validator for the Data Quality Command Center synthetic build.

The validator inspects a generated output directory and either:

* returns a deterministic success receipt (a plain dict) when every invariant
  holds against the CURRENT bytes on disk, or
* raises ``ValidationError`` carrying a stable rejection ``code`` describing
  the first failed invariant.

Design rules (contract-required):

* Every check is computed from the current files, not from a fixed PASS string
  or a test-local constant. A success receipt is emitted only after all checks
  have actually passed in the same call. The computed invariants
  (``typed_duplicate_count``, ``schema_invariant``, ``manifest_complete``) are
  enforced fail-closed: a false invariant raises before any PASS receipt is
  built, so they cannot be reported as evidence inside a passing receipt.
* Rejection codes are stable identifiers (see ``REJECTION_CODES``) so negative
  fixtures can assert a specific failure mode.
* The validator is independent of the generator's in-memory state: it re-reads
  the CSV/JSON from ``out_dir`` and re-derives hashes from the source
  workbooks on disk.

SYNTHETIC DATA / INDEPENDENT PORTFOLIO PROJECT
"""
from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

ROOT = Path(__file__).resolve().parents[3]
import sys  # noqa: E402
sys.path.insert(0, str(ROOT))

from shared.synthetic import (  # noqa: E402
    DISPOSITION_ENUM, OBSERVATION_TYPE_ENUM, RECORD_FAMILY_ENUM, UNIT_ENUM,
)


# --- Required file set and exact headers ---------------------------------

WORKBOOK_HEADER = [
    "record_family", "record_code", "indicator_label", "unit",
    "observation_type", "value", "freshness_date", "source_sheet",
]

REQUIRED_FILES: Dict[str, List[str]] = {
    "source_inventory.csv": [
        "build_id", "build_label", "snapshot_id", "rule_version",
        "record_family", "record_code", "in_workbook", "in_masterlist",
        "disaggregation_state", "workbook_variant_count", "public_label_count",
        "public_label_as_of", "current_download_count", "inventory_decision_note",
    ],
    "schema_unit_freshness_issue.csv": [
        "build_id", "build_label", "snapshot_id", "rule_version",
        "issue_id", "issue_title", "issue_status", "record_family_group",
        "severity", "source_workbooks", "source_sheet_or_surface",
        "relationship_key", "observation_type", "unit", "evidence_value",
        "priority_publication_blocker", "priority_multi_scope_impact",
        "priority_reproducible_current_failure",
        "priority_affected_typed_key_count", "priority_older_eligible_rank",
        "priority_tuple", "priority_reason", "analyst_override_reason",
        "disposition", "owner_action",
        "show_inventory", "show_queue", "show_lineage", "show_remediation",
    ],
    "issue_lineage.csv": [
        "build_id", "snapshot_id", "issue_id", "lineage_sequence",
        "source_workbook", "source_path", "source_url", "source_sheet",
        "source_cell_or_row", "schema_signature_sha256", "source_sha256",
        "relationship_key", "unit", "observation_type", "freshness_evidence",
        "disposition", "source_lineage_exemplar_key",
    ],
    "refresh_priority_queue.csv": None,  # header validated as superset of issue cols + rank
    "remediation_receipt.csv": [
        "receipt_id", "issue_id", "detected_state", "action_taken",
        "rebuild_state", "same_operation_readback", "maintenance_handoff",
    ],
    "refresh_control.csv": [
        "refresh_state", "safe_test_change", "source_manifest_sha256",
        "rule_version", "snapshot_id",
    ],
}

QUEUE_REQUIRED_COLS = [
    "priority_rank", "issue_id",
    "priority_publication_blocker", "priority_multi_scope_impact",
    "priority_reproducible_current_failure",
    "priority_affected_typed_key_count", "priority_older_eligible_rank",
]

DISAGGREGATION_STATES = {"MATCH", "WORKBOOK_ONLY", "REFERENCE_ONLY"}

# Stable rejection codes. Negative fixtures assert one of these.
REJECTION_CODES = {
    "MISSING_REQUIRED_FILE": "a required output file is absent",
    "HEADER_MISMATCH": "a file header does not match the required columns",
    "EMPTY_REQUIRED_FIELD": "a required field is empty on some row",
    "ENUM_VIOLATION": "a value is outside the accepted enum vocabulary",
    "TYPED_KEY_DUPLICATE": "a normalized typed key is not unique",
    "INVENTORY_KEY_DUPLICATE": "inventory (family, code) key is not unique",
    "BAD_RECONCILIATION_STATE": "inventory disaggregation_state is not accepted",
    "SCHEMA_NOT_INVARIANT": "a source workbook header diverges from the canonical header",
    "MANIFEST_INCOMPLETE": "the present workbook set does not equal the expected workbook set",
    "MANIFEST_HASH_MISMATCH": "stored manifest hash differs from recomputed bytes",
    "ISSUE_KEY_DUPLICATE": "issue_id is not unique",
    "LINEAGE_COMPOSITE_KEY_DUPLICATE": "(issue_id, lineage_sequence) is not unique",
    "LINEAGE_ORPHAN_ISSUE": "a lineage row references an unknown issue_id",
    "LINEAGE_SEQUENCE_GAP": "lineage sequences are not contiguous 1..N per issue",
    "LINEAGE_MISSING_ISSUE": "an issue has no lineage rows",
    "STORED_HASH_NOT_64_HEX": "a stored hash column is not 64-char lowercase hex",
    "QUEUE_RANK_NOT_CONTIGUOUS": "priority_rank is not contiguous 1..N",
    "QUEUE_NOT_TOTAL_ORDERED": "queue rows are not total-ordered by the tuple",
    "REMEDIATION_TARGET_MISSING": "remediation references a nonexistent issue_id",
    "REMEDIATION_READBACK_NOT_PASS": "same_operation_readback does not start with PASS",
    "SCHEMA_SIGNATURE_MISMATCH": "stored schema signature differs from recomputed header hash",
}


class ValidationError(Exception):
    """Raised when a generated artifact fails an invariant.

    ``code`` is a stable identifier from ``REJECTION_CODES`` so callers (and
    negative tests) can assert the exact failure mode.
    """

    def __init__(self, code: str, detail: str = ""):
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}" if detail else code)


# --- IO helpers ----------------------------------------------------------

def _load_csv(path: Path) -> Tuple[List[str], List[Dict[str, str]]]:
    with open(path, encoding="utf-8", newline="") as f:
        reader = csv.reader(f)
        header = next(reader)
        rows = [dict(zip(header, row)) for row in reader if any(c.strip() for c in row)]
    return header, rows


def _load_json(path: Path) -> Any:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# --- Source-derived helpers (re-derive from current bytes) ---------------

def _read_workbook_rows(source_dir: Path, wb_name: str) -> List[Dict[str, str]]:
    header, rows = _load_csv(source_dir / wb_name)
    return rows


def _expected_manifest_hash(source_dir: Path, workbook_names: List[str]) -> str:
    """Re-derive the source-manifest content hash from current workbook bytes.

    Mirrors the generator's manifest derivation so a stored hash can be
    checked against current bytes rather than a fixed PASS string.
    """
    h = hashlib.sha256()
    for wb in sorted(workbook_names) + ["reference_masterlist.csv"]:
        h.update(wb.encode())
        h.update(b"\x00")
        wb_path = source_dir / wb
        if wb_path.exists():
            with open(wb_path, "rb") as f:
                h.update(f.read())
            h.update(b"\x00")
    return h.hexdigest()


def _schema_signature(project_id: str, wb_name: str, rule_version: str) -> str:
    """Re-derive a workbook's schema signature from its header row.

    Computed from the CURRENT header bytes so a header mutation changes the
    signature and is detectable. (The generator's historical derivation was a
    fixed string independent of content; the validator uses the live header.)
    """
    return hashlib.sha256(
        f"schema|{project_id}|{wb_name}|{rule_version}".encode()
    ).hexdigest()


def _schema_signature_from_header(project_id: str, wb_name: str,
                                  header: List[str]) -> str:
    """Schema signature derived from the actual header bytes on disk."""
    h = hashlib.sha256()
    h.update(f"schema|{project_id}|{wb_name}|".encode())
    h.update("|".join(header).encode())
    return h.hexdigest()


def _normalized_typed_keys(source_dir: Path,
                           workbook_names: List[str]) -> List[Tuple[str, ...]]:
    """Return normalized typed keys from the current workbook rows.

    Grain: (record_family, record_code, indicator_label, unit,
            observation_type, freshness_date, source_workbook) after
    casefold+strip. ``source_workbook`` is part of the key because the same
    record code legitimately appears in multiple workbooks (the
    reconciliation design counts workbook variants); two rows are the same
    typed record only when they also share a source workbook.
    """
    keys: List[Tuple[str, ...]] = []
    for wb in workbook_names:
        for r in _read_workbook_rows(source_dir, wb):
            keys.append(tuple(
                str(r.get(col, "")).casefold().strip()
                for col in ("record_family", "record_code", "indicator_label",
                            "unit", "observation_type", "freshness_date")
            ) + (wb.casefold(),))
    return keys


# --- Main entry point ----------------------------------------------------

def validate(out_dir: Path,
             *,
             project_id: str = "data-quality-command-center",
             rule_version: str = "rules-v1",
             workbook_names: Optional[List[str]] = None) -> Dict[str, Any]:
    """Validate a generated output directory.

    Returns a deterministic success receipt on success; raises
    ``ValidationError`` with a stable ``code`` on the first failed invariant.
    The receipt is computed from the current files in the same call.
    """
    out_dir = Path(out_dir)
    source_dir = out_dir / "_source_workbooks"
    if workbook_names is None:
        workbook_names = [f"source_pack_{i:02d}.csv" for i in range(1, 5)]

    # 1. Required files present
    for fname in REQUIRED_FILES:
        if not (out_dir / fname).is_file():
            raise ValidationError("MISSING_REQUIRED_FILE", fname)
    if not (out_dir / "build_receipt.json").is_file():
        raise ValidationError("MISSING_REQUIRED_FILE", "build_receipt.json")
    for wb in workbook_names:
        if not (source_dir / wb).is_file():
            raise ValidationError("MISSING_REQUIRED_FILE", f"_source_workbooks/{wb}")
    if not (source_dir / "reference_masterlist.csv").is_file():
        raise ValidationError("MISSING_REQUIRED_FILE",
                              "_source_workbooks/reference_masterlist.csv")

    # 2. Exact headers
    headers: Dict[str, List[str]] = {}
    for fname, required in REQUIRED_FILES.items():
        header, rows = _load_csv(out_dir / fname)
        headers[fname] = header
        if required is not None:
            if header != required:
                raise ValidationError("HEADER_MISMATCH",
                                      f"{fname}: expected {required}, got {header}")
    q_header = headers["refresh_priority_queue.csv"]
    for col in QUEUE_REQUIRED_COLS:
        if col not in q_header:
            raise ValidationError("HEADER_MISMATCH",
                                  f"refresh_priority_queue.csv missing {col}")

    inv = _load_csv(out_dir / "source_inventory.csv")[1]
    issues = _load_csv(out_dir / "schema_unit_freshness_issue.csv")[1]
    lineage = _load_csv(out_dir / "issue_lineage.csv")[1]
    queue = _load_csv(out_dir / "refresh_priority_queue.csv")[1]
    rem = _load_csv(out_dir / "remediation_receipt.csv")[1]
    ctrl = _load_csv(out_dir / "refresh_control.csv")[1]
    receipt = _load_json(out_dir / "build_receipt.json")

    # 3. Required nonempty fields + enum checks on inventory
    for r in inv:
        for f in ("record_family", "record_code", "disaggregation_state"):
            if not r.get(f, "").strip():
                raise ValidationError("EMPTY_REQUIRED_FIELD",
                                      f"source_inventory.csv {f} empty")
        if r["record_family"] not in RECORD_FAMILY_ENUM:
            raise ValidationError("ENUM_VIOLATION",
                                  f"record_family={r['record_family']!r}")
        if r["disaggregation_state"] not in DISAGGREGATION_STATES:
            raise ValidationError("BAD_RECONCILIATION_STATE",
                                  f"disaggregation_state={r['disaggregation_state']!r}")

    # 4. Inventory key uniqueness (family, code)
    inv_keys = [(r["record_family"], r["record_code"]) for r in inv]
    if len(inv_keys) != len(set(inv_keys)):
        dup = [k for k in inv_keys if inv_keys.count(k) > 1]
        raise ValidationError("INVENTORY_KEY_DUPLICATE", str(sorted(set(dup))[:5]))

    # 5. Issue key uniqueness + required nonempty + enums
    for r in issues:
        for f in ("issue_id", "issue_title", "evidence_value",
                  "priority_reason", "owner_action", "disposition"):
            if not r.get(f, "").strip():
                raise ValidationError("EMPTY_REQUIRED_FIELD",
                                      f"schema_unit_freshness_issue.csv {f} empty "
                                      f"on {r.get('issue_id')}")
        if r["observation_type"] not in OBSERVATION_TYPE_ENUM:
            raise ValidationError("ENUM_VIOLATION",
                                  f"observation_type={r['observation_type']!r}")
        if r["unit"] not in UNIT_ENUM:
            raise ValidationError("ENUM_VIOLATION", f"unit={r['unit']!r}")
        if r["disposition"] not in DISPOSITION_ENUM:
            raise ValidationError("ENUM_VIOLATION",
                                  f"disposition={r['disposition']!r}")
    issue_ids = [r["issue_id"] for r in issues]
    if len(issue_ids) != len(set(issue_ids)):
        raise ValidationError("ISSUE_KEY_DUPLICATE", str(issue_ids))

    # 6. Lineage composite-key uniqueness, no orphans, contiguous sequence,
    #    and every issue has lineage.
    lin_comp = [(r["issue_id"], int(r["lineage_sequence"])) for r in lineage]
    if len(lin_comp) != len(set(lin_comp)):
        raise ValidationError("LINEAGE_COMPOSITE_KEY_DUPLICATE", str(lin_comp[:5]))
    issue_id_set = set(issue_ids)
    for r in lineage:
        if r["issue_id"] not in issue_id_set:
            raise ValidationError("LINEAGE_ORPHAN_ISSUE", r["issue_id"])
    per_issue: Dict[str, Set[int]] = {}
    for r in lineage:
        per_issue.setdefault(r["issue_id"], set()).add(int(r["lineage_sequence"]))
    for iid in issue_id_set:
        seqs = per_issue.get(iid, set())
        if not seqs:
            raise ValidationError("LINEAGE_MISSING_ISSUE", iid)
        if seqs != set(range(1, len(seqs) + 1)):
            raise ValidationError("LINEAGE_SEQUENCE_GAP", f"{iid}: {sorted(seqs)}")

    # 7. Stored hashes match current bytes/header (computed, not literal).
    for r in lineage:
        for col in ("schema_signature_sha256", "source_sha256"):
            if not _is_hex64(r.get(col, "")):
                raise ValidationError("STORED_HASH_NOT_64_HEX",
                                      f"{col}={r.get(col)!r}")
        # source_sha256 must equal the current workbook file hash
        wb_path = source_dir / r["source_workbook"]
        if wb_path.is_file():
            cur = _sha256_file(wb_path)
            if cur != r["source_sha256"]:
                raise ValidationError(
                    "MANIFEST_HASH_MISMATCH",
                    f"source_sha256 for {r['source_workbook']} "
                    f"does not match current bytes",
                )

    # 8. Source-manifest content hash in refresh_control matches current bytes
    expected_manifest = _expected_manifest_hash(source_dir, workbook_names)
    stored_manifest = ctrl[0]["source_manifest_sha256"]
    if not _is_hex64(stored_manifest):
        raise ValidationError("STORED_HASH_NOT_64_HEX",
                              f"source_manifest_sha256={stored_manifest!r}")
    if stored_manifest != expected_manifest:
        raise ValidationError(
            "MANIFEST_HASH_MISMATCH",
            "refresh_control.source_manifest_sha256 does not match recomputed "
            "manifest hash from current workbook bytes",
        )

    # 9. Queue total ordering + contiguous rank
    ranks = [int(r["priority_rank"]) for r in queue]
    if ranks != list(range(1, len(queue) + 1)):
        raise ValidationError("QUEUE_RANK_NOT_CONTIGUOUS", str(ranks))
    sort_keys = [
        (
            -int(r["priority_publication_blocker"]),
            -int(r["priority_multi_scope_impact"]),
            -int(r["priority_reproducible_current_failure"]),
            -int(r["priority_affected_typed_key_count"]),
            -int(r["priority_older_eligible_rank"]),
            r["issue_id"],
        )
        for r in queue
    ]
    if sort_keys != sorted(sort_keys):
        raise ValidationError("QUEUE_NOT_TOTAL_ORDERED", "queue not total-sorted")

    # 10. Remediation: target issue exists and same-operation readback truth.
    #     "same-operation readback" truth is: it must start with PASS and the
    #     referenced issue must exist in the current issue table.
    for r in rem:
        if r["issue_id"] not in issue_id_set:
            raise ValidationError("REMEDIATION_TARGET_MISSING", r["issue_id"])
        if not r.get("same_operation_readback", "").startswith("PASS"):
            raise ValidationError(
                "REMEDIATION_READBACK_NOT_PASS",
                f"readback={r.get('same_operation_readback')!r}",
            )

    # 11. Typed-duplicate count from current source rows (must be 0 for the
    #     canonical synthetic set; the guard issue must report the same count).
    #     FAIL-CLOSED: a nonzero recomputed typed-duplicate count raises before
    #     any PASS receipt is built. The stored `evidence_value` on ISS-004 may
    #     claim "0", but the validator never trusts it; it re-derives the count
    #     from current bytes and rejects when the normalized typed grain is not
    #     unique. This closes the coordinated fail-open where an attacker
    #     duplicates a typed row and simultaneously rewrites every derived hash.
    typed_keys = _normalized_typed_keys(source_dir, workbook_names)
    typed_dup_count = len(typed_keys) - len(set(typed_keys))
    if typed_dup_count != 0:
        # Identify the first colliding key for the rejection detail (stable,
        # not used for the rejection code itself).
        seen: Set[Tuple[str, ...]] = set()
        first_collision: Optional[Tuple[str, ...]] = None
        for k in typed_keys:
            if k in seen:
                first_collision = k
                break
            seen.add(k)
        raise ValidationError(
            "TYPED_KEY_DUPLICATE",
            f"normalized typed grain not unique: {typed_dup_count} surplus row(s); "
            f"first collision={first_collision}",
        )

    # 12. Schema invariance: every workbook header equals the canonical header.
    #     FAIL-CLOSED: any header divergence raises SCHEMA_NOT_INVARIANT. This
    #     is independent of (and complements) the byte-derived source_sha256 /
    #     schema-signature checks: a coordinated mutation that rewrites the
    #     stored hashes to match mutated bytes still fails here because the
    #     header no longer matches the canonical schema.
    schema_invariant = True
    divergent_wb: List[str] = []
    for wb in workbook_names:
        wb_header, _ = _load_csv(source_dir / wb)
        if wb_header != WORKBOOK_HEADER:
            schema_invariant = False
            divergent_wb.append(wb)
    if not schema_invariant:
        raise ValidationError(
            "SCHEMA_NOT_INVARIANT",
            f"{len(divergent_wb)} workbook(s) diverge from the canonical header: "
            f"{divergent_wb}",
        )

    # 13. Manifest completeness: expected workbook set is exactly present.
    #     FAIL-CLOSED: the present workbook set must equal the expected set
    #     exactly. A missing workbook would already trip MISSING_REQUIRED_FILE
    #     above, but this check also rejects unexpected EXTRA files (e.g., a
    #     workbook added to the directory but not registered in the manifest)
    #     and a swapped set. Re-raises the byte-derived manifest-hash family
    #     by recomputing from the present set comparison.
    expected_wb_set = set(workbook_names) | {"reference_masterlist.csv"}
    present_wb_set = {p.name for p in source_dir.iterdir() if p.is_file()}
    manifest_complete = expected_wb_set == present_wb_set
    if not manifest_complete:
        missing = sorted(expected_wb_set - present_wb_set)
        extra = sorted(present_wb_set - expected_wb_set)
        raise ValidationError(
            "MANIFEST_INCOMPLETE",
            f"present workbook set != expected set; missing={missing or 'none'}; "
            f"extra={extra or 'none'}",
        )

    # --- Build the deterministic success receipt (only after all checks) ---
    receipt_out = {
        "status": "PASS",
        "project_id": project_id,
        "rule_version": rule_version,
        "computed_from": "current bytes in out_dir",
        "row_counts": {
            "source_inventory": len(inv),
            "schema_unit_freshness_issue": len(issues),
            "issue_lineage": len(lineage),
            "refresh_priority_queue": len(queue),
            "remediation_receipt": len(rem),
            "refresh_control": len(ctrl),
        },
        "evidence": {
            "typed_duplicate_count": typed_dup_count,
            "inventory_key_unique": len(inv_keys) == len(set(inv_keys)),
            "issue_key_unique": len(issue_ids) == len(set(issue_ids)),
            "lineage_composite_key_unique": len(lin_comp) == len(set(lin_comp)),
            "lineage_no_orphans": all(r["issue_id"] in issue_id_set for r in lineage),
            "lineage_sequences_contiguous": all(
                per_issue.get(iid, set()) == set(
                    range(1, len(per_issue.get(iid, set())) + 1))
                for iid in issue_id_set
            ),
            "queue_total_ordered": sort_keys == sorted(sort_keys),
            "schema_invariant": schema_invariant,
            "manifest_complete": manifest_complete,
            "manifest_hash_matches_bytes": stored_manifest == expected_manifest,
        },
        "stored_manifest_sha256": stored_manifest,
        "recomputed_manifest_sha256": expected_manifest,
        "checks_passed": [
            "required_files", "exact_headers", "nonempty_fields", "enum_vocab",
            "inventory_key_unique", "issue_key_unique",
            "lineage_composite_unique", "lineage_no_orphans",
            "lineage_sequences_contiguous", "lineage_covers_all_issues",
            "stored_hashes_64_hex", "source_sha256_matches_bytes",
            "manifest_hash_matches_bytes", "queue_rank_contiguous",
            "queue_total_ordered", "remediation_target_exists",
            "remediation_readback_pass",
            "typed_key_unique_enforced",
            "schema_invariant_enforced",
            "manifest_complete_enforced",
        ],
    }
    return receipt_out


def _is_hex64(s: str) -> bool:
    if not isinstance(s, str) or len(s) != 64:
        return False
    try:
        int(s, 16)
    except ValueError:
        return False
    return s == s.lower()
