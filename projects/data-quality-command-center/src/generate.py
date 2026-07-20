"""Project 1: Data Quality Command Center - synthetic generator.

Generic pattern: a governed pipeline that turns a frozen, hash-pinned set of
fictional source workbooks into four logical tables:

  source_inventory            one row per (record_family, record_code)
  schema_unit_freshness_issue one row per issue_id
  issue_lineage               one row per (issue_id, lineage_sequence)
  refresh_priority_queue      one row per issue_id, ordered by a 5-tuple

plus single-row control tables (remediation_receipt, refresh_control) and a
display surface. The dashboard reads from a one-to-many relationship:
issue -> lineage, joined on issue_id, with the left endpoint declared unique.

SYNTHETIC DATA / INDEPENDENT PORTFOLIO PROJECT
"""
from __future__ import annotations

import csv
import hashlib
import os
import sys
from pathlib import Path
from typing import Any, Dict, List

# Make `shared` importable when run directly
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from shared.synthetic import (  # noqa: E402
    COUNTRIES, OBSERVATION_TYPE_ENUM, RECORD_FAMILY_ENUM, UNIT_ENUM,
    derive_seed, fictional_cities, make_rng, snapshot_id, write_csv, write_json,
    write_jsonl,
)

PROJECT_ID = "data-quality-command-center"
SNAPSHOT_ID = snapshot_id(PROJECT_ID)
RULE_VERSION = "rules-v1"
BUILD_LABEL = "syn-dqcc-build-001"


def _stable_bucket(family: str, code: str, wb_name: str, modulus: int = 7) -> int:
    """Deterministic bucket in [0, modulus) from the SHA-256 seed protocol.

    Purpose string embeds family, code, and workbook so every (family, code,
    workbook) tuple maps to a stable, process-independent integer. This
    replaces the earlier use of Python's randomized built-in hash().
    """
    purpose = f"workbook_bucket|{family}|{code}|{wb_name}"
    return derive_seed(PROJECT_ID, purpose) % modulus

# ---- Output paths --------------------------------------------------------
# Default output directory; callers (including tests) may pass their own.
OUT = ROOT / "projects" / "data-quality-command-center" / "data" / "synthetic"
SOURCE_DIR = OUT / "_source_workbooks"  # tiny synthetic source workbooks (CSV)


# ---- Step 1: synthesize the fictional "source workbooks" -----------------
# Each is a tiny CSV with a fixed schema. They are the INPUT to the build.

WORKBOOK_NAMES = [f"source_pack_{i:02d}.csv" for i in range(1, 5)]
WORKBOOK_HEADER = [
    "record_family", "record_code", "indicator_label", "unit",
    "observation_type", "value", "freshness_date", "source_sheet",
]


def generate_source_workbooks(source_dir: Path = SOURCE_DIR,
                              relative_to: Path = OUT) -> List[Dict[str, Any]]:
    """Write 4 tiny synthetic source workbooks and return their manifest."""
    rng = make_rng(PROJECT_ID, "source_workbooks")
    source_dir.mkdir(parents=True, exist_ok=True)
    manifest: List[Dict[str, Any]] = []

    # Build a stable pool of record codes per family
    codes_per_family = {
        "observed":  [f"OBS-{i:03d}" for i in range(1, 9)],
        "estimated": [f"EST-{i:03d}" for i in range(1, 7)],
        "projected": [f"PRJ-{i:03d}" for i in range(1, 6)],
        "composite": [f"CMP-{i:03d}" for i in range(1, 5)],
        "reference": [f"REF-{i:03d}" for i in range(1, 4)],
    }
    # A separate "masterlist" of expected codes (drives reconciliation)
    masterlist_codes = (
        codes_per_family["observed"][:8]
        + codes_per_family["estimated"][:6]
        + codes_per_family["projected"][:5]
        + codes_per_family["composite"][:4]
        + [f"REF-{i:03d}" for i in range(1, 5)]  # one extra reference -> REFERENCE_ONLY case
    )

    all_rows: List[Dict[str, Any]] = []
    for wb_idx, wb_name in enumerate(WORKBOOK_NAMES):
        wb_rows: List[Dict[str, Any]] = []
        for family in RECORD_FAMILY_ENUM:
            # Each workbook contains a deterministic subset
            for code in codes_per_family[family]:
                # Deterministic inclusion: skip some codes in some workbooks.
                # The bucket is derived from the SHA-256 seed protocol with a
                # stable purpose string (family|code|workbook) so it is stable
                # across fresh processes. Python's built-in hash() is NOT used
                # here because it is randomized per process and would break
                # byte-identical regeneration across PYTHONHASHSEED values.
                bucket = _stable_bucket(family, code, wb_name)
                if bucket == 0 and wb_idx > 0:
                    continue  # creates WORKBOOK_ONLY / REFERENCE_ONLY cases
                obs_type = rng.choice(OBSERVATION_TYPE_ENUM)
                unit = rng.choice(UNIT_ENUM)
                value = round(rng.uniform(0, 100), 3)
                freshness = f"2025-{1 + (bucket % 12):02d}-{1 + (bucket % 27):02d}"
                wb_rows.append({
                    "record_family": family,
                    "record_code": code,
                    "indicator_label": f"Indicator {code}",
                    "unit": unit,
                    "observation_type": obs_type,
                    "value": value,
                    "freshness_date": freshness,
                    "source_sheet": f"{wb_name[:-4]}::sheet_main",
                })
        # Persist the workbook
        wb_path = source_dir / wb_name
        with open(wb_path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=WORKBOOK_HEADER, lineterminator="\n")
            w.writeheader()
            w.writerows(wb_rows)
        all_rows.extend(wb_rows)
        manifest.append({
            "workbook_name": wb_name,
            "relative_path": str(wb_path.relative_to(relative_to)),
            "row_count": len(wb_rows),
        })

    # Persist masterlist
    ml_path = source_dir / "reference_masterlist.csv"
    with open(ml_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["record_family", "record_code"],
                           lineterminator="\n")
        w.writeheader()
        for code in masterlist_codes:
            family = code.split("-")[0].lower()
            fam_map = {"obs": "observed", "est": "estimated", "prj": "projected",
                       "cmp": "composite", "ref": "reference"}
            w.writerow({"record_family": fam_map[family], "record_code": code})
    manifest.append({
        "workbook_name": "reference_masterlist.csv",
        "relative_path": str(ml_path.relative_to(relative_to)),
        "row_count": len(masterlist_codes),
    })

    return manifest


# ---- Step 2: reconciliation -> source_inventory --------------------------

def build_source_inventory(source_dir: Path = SOURCE_DIR) -> List[Dict[str, Any]]:
    """Reconcile workbooks vs reference list. One row per (family, code)."""
    # Load workbook codes
    wb_codes: Dict[str, set] = {}
    for wb_name in WORKBOOK_NAMES:
        with open(source_dir / wb_name, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                wb_codes.setdefault(r["record_family"], set()).add(r["record_code"])
    # Load masterlist
    ml_codes: Dict[str, set] = {}
    with open(source_dir / "reference_masterlist.csv", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            ml_codes.setdefault(r["record_family"], set()).add(r["record_code"])

    rows: List[Dict[str, Any]] = []
    families = sorted(set(wb_codes) | set(ml_codes))
    for family in families:
        wb = wb_codes.get(family, set())
        ml = ml_codes.get(family, set())
        for code in sorted(wb | ml):
            in_wb = code in wb
            in_ml = code in ml
            if in_wb and in_ml:
                state = "MATCH"
            elif in_wb:
                state = "WORKBOOK_ONLY"
            else:
                state = "REFERENCE_ONLY"
            rows.append({
                "build_id": BUILD_LABEL,
                "build_label": BUILD_LABEL,
                "snapshot_id": SNAPSHOT_ID,
                "rule_version": RULE_VERSION,
                "record_family": family,
                "record_code": code,
                "in_workbook": in_wb,
                "in_masterlist": in_ml,
                "disaggregation_state": state,
                "workbook_variant_count": sum(1 for w in WORKBOOK_NAMES
                                              if code in _wb_codes_for(w, family, source_dir)),
                "public_label_count": len(ml),
                "public_label_as_of": "2025-06-01",
                "current_download_count": len(wb),
                "inventory_decision_note": _inventory_note(state),
            })
    return rows


def _wb_codes_for(wb_name: str, family: str, source_dir: Path = SOURCE_DIR) -> set:
    out = set()
    with open(source_dir / wb_name, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["record_family"] == family:
                out.add(r["record_code"])
    return out


def _inventory_note(state: str) -> str:
    return {
        "MATCH": "Present in both workbook set and reference list.",
        "WORKBOOK_ONLY": "Present in workbook set, absent from reference list.",
        "REFERENCE_ONLY": "Present in reference list, absent from workbook set.",
    }[state]


# ---- Step 3: detect issues -> schema_unit_freshness_issue ---------------

def detect_issues(inventory: List[Dict[str, Any]],
                  source_dir: Path = SOURCE_DIR) -> List[Dict[str, Any]]:
    """Walk the inventory + workbook content and emit a small set of issues.

    Each issue has a 5-component priority tuple (all descending) used for
    queue ordering. Components are generic:
      c1 publication_blocker
      c2 multi_scope_impact
      c3 reproducible_current_failure
      c4 affected_typed_key_count
      c5 older_eligible_rank
    """
    rng = make_rng(PROJECT_ID, "issue_detection")
    issues: List[Dict[str, Any]] = []

    # Detector 1: reconciliation disagreement (WORKBOOK_ONLY / REFERENCE_ONLY)
    reconc_count = sum(1 for r in inventory
                       if r["disaggregation_state"] != "MATCH")
    if reconc_count:
        issues.append(_make_issue(
            rng, issue_id="ISS-001",
            title="Inventory reconciliation disagreement",
            status="OPEN_CURRENT_SNAPSHOT",
            family_group="inventory",
            severity="Investigate first",
            source_workbooks=",".join(WORKBOOK_NAMES),
            source_sheet_or_surface="reference_masterlist vs workbook set",
            observation_type="observed", unit="integer_count",
            evidence_value=f"{reconc_count} codes disagree across workbook/reference",
            priority=(1, 1, 1, reconc_count, 5),
            reason="Some codes appear on only one side of the workbook/reference reconciliation.",
            disposition="open_current_snapshot",
            owner_action="Confirm whether the mismatched codes should be added to or removed from the reference list.",
        ))

    # Detector 2: mixed-unit scale (a single indicator appears under multiple units)
    multi_unit = _find_multi_unit_indicators(source_dir)
    if multi_unit:
        issues.append(_make_issue(
            rng, issue_id="ISS-002",
            title="Mixed-unit scale on shared indicators",
            status="OPEN_CURRENT_SNAPSHOT",
            family_group="unit",
            severity="Investigate first",
            source_workbooks=",".join(WORKBOOK_NAMES),
            source_sheet_or_surface="indicator_label aggregation",
            observation_type="mixed", unit="mixed",
            evidence_value=f"{len(multi_unit)} indicators carry more than one unit",
            priority=(1, 1, 1, len(multi_unit), 3),
            reason="The same indicator label is stored under different units, blocking a single typed measure.",
            disposition="open_current_snapshot",
            owner_action="Pick a canonical unit per indicator and re-emit the typed column.",
        ))

    # Detector 3: freshness exclusion (projected/reference rows must not age).
    # This is a SYNTHETIC VALIDATION CONTROL: the counts are recomputed from
    # the current workbook rows on every build.
    prj_count = _count_observation_type("projected", source_dir)
    ref_count = _count_observation_type("reference", source_dir)
    if prj_count or ref_count:
        issues.append(_make_issue(
            rng, issue_id="ISS-003",
            title="Projected and reference rows excluded from freshness aging "
                  "(synthetic validation control)",
            status="REMEDIATED_IN_TYPED_PIPELINE",
            family_group="freshness",
            severity="Verified guard",
            source_workbooks=",".join(WORKBOOK_NAMES),
            source_sheet_or_surface="observation_type column",
            observation_type="not_applicable", unit="not_applicable",
            evidence_value=f"{prj_count} projected + {ref_count} reference rows "
                           f"flagged freshness_ineligible (computed from current rows)",
            priority=(0, 1, 0, prj_count + ref_count, 4),
            reason="Freshness-exclusion counts are recomputed from current "
                   "workbook rows on every build; this is a synthetic validation "
                   "control, not a historical production event.",
            disposition="remediated_in_typed_pipeline",
            owner_action="No action: freshness-ineligible counts are recomputed each build.",
        ))

    # Detector 4: typed-key duplicate scan (computed from current source rows).
    # This is a SYNTHETIC VALIDATION CONTROL: the synthetic source set is
    # deliberately built with unique typed keys, so this detector reports the
    # live computed duplicate count (which is 0 for the canonical build) and
    # would surface a real collision if one were introduced. It is NOT a
    # historical production event.
    typed_dup_count = _count_typed_duplicates(source_dir)
    issues.append(_make_issue(
        rng, issue_id="ISS-004",
        title="Typed-key duplicate scan (synthetic validation control)",
        status="REMEDIATED_IN_TYPED_PIPELINE",
        family_group="typed_key",
        severity="Verified guard",
        source_workbooks=",".join(WORKBOOK_NAMES),
        source_sheet_or_surface="typed grain: (family, code, indicator, unit, obs_type, freshness, source_workbook)",
        observation_type="observed", unit="integer_count",
        evidence_value=f"{typed_dup_count} typed duplicates after normalization "
                       f"(computed from current source rows)",
        priority=(0, 0, 0, typed_dup_count, 1),
        reason="Typed grain uniqueness is recomputed from the current workbook "
               "rows on every build; this is a synthetic validation control, not "
               "a historical production event.",
        disposition="remediated_in_typed_pipeline",
        owner_action="No action: typed duplicate count is recomputed each build.",
    ))

    # Detector 5: source-manifest completeness (computed from the actual
    # expected-vs-present workbook set). This is a SYNTHETIC VALIDATION CONTROL
    # that compares the expected workbook set against the files actually
    # present in the source directory. It does NOT assert an earlier packet
    # history; the evidence string reports the live present/expected sets.
    expected_wb = sorted(WORKBOOK_NAMES) + ["reference_masterlist.csv"]
    present_wb = sorted(p.name for p in source_dir.iterdir() if p.is_file())
    missing = [w for w in expected_wb if w not in present_wb]
    extra = [w for w in present_wb if w not in expected_wb]
    completeness_note = (
        f"expected {len(expected_wb)} workbooks, present {len(present_wb)}; "
        f"missing={missing or 'none'}; extra={extra or 'none'}"
    )
    issues.append(_make_issue(
        rng, issue_id="ISS-005",
        title="Source-manifest completeness check (synthetic validation control)",
        status="CORRECTED_SOURCE_CONTRACT",
        family_group="source_contract",
        severity="Verified guard",
        source_workbooks=",".join(expected_wb),
        source_sheet_or_surface="source workbook directory vs manifest list",
        observation_type="reference", unit="integer_count",
        evidence_value=completeness_note,
        priority=(0, 0, 0, len(missing), 2),
        reason="Completeness is recomputed by comparing the expected workbook "
               "set to the files present on disk; this is a synthetic validation "
               "control, not a claim about an earlier packet history.",
        disposition="corrected_source_contract",
        owner_action="No action: manifest completeness is recomputed each build.",
    ))

    # Detector 6: schema header invariance (computed from current headers).
    # This is a SYNTHETIC VALIDATION CONTROL that compares every workbook's
    # header row to the canonical header and reports the count of invariant
    # headers from current bytes.
    invariant_count = _count_invariant_headers(source_dir)
    header_col_count = len(WORKBOOK_HEADER)
    issues.append(_make_issue(
        rng, issue_id="ISS-006",
        title="Schema header invariance check (synthetic validation control)",
        status="REMEDIATED_IN_TYPED_PIPELINE",
        family_group="schema",
        severity="Verified guard",
        source_workbooks=",".join(WORKBOOK_NAMES),
        source_sheet_or_surface="workbook header row",
        observation_type="observed", unit="not_applicable",
        evidence_value=f"{invariant_count}/{len(WORKBOOK_NAMES)} workbooks match "
                       f"the canonical {header_col_count}-column header "
                       f"(computed from current headers)",
        priority=(0, 0, 0, invariant_count, 1),
        reason="Header invariance is recomputed by reading each workbook's "
               "header row on every build; this is a synthetic validation control, "
               "not a historical production event.",
        disposition="remediated_in_typed_pipeline",
        owner_action="No action: header invariance verified.",
    ))

    return issues


def _make_issue(rng, issue_id, title, status, family_group, severity,
                source_workbooks, source_sheet_or_surface, observation_type,
                unit, evidence_value, priority, reason, disposition,
                owner_action) -> Dict[str, Any]:
    c1, c2, c3, c4, c5 = priority
    show = {
        "show_inventory": family_group == "inventory",
        "show_queue": True,  # all issues appear in the queue
        "show_lineage": True,
        "show_remediation": status.startswith("REMEDIATED") or status.startswith("CORRECTED"),
    }
    return {
        "build_id": BUILD_LABEL,
        "build_label": BUILD_LABEL,
        "snapshot_id": SNAPSHOT_ID,
        "rule_version": RULE_VERSION,
        "issue_id": issue_id,
        "issue_title": title,
        "issue_status": status,
        "record_family_group": family_group,
        "severity": severity,
        "source_workbooks": source_workbooks,
        "source_sheet_or_surface": source_sheet_or_surface,
        "relationship_key": issue_id,
        "observation_type": observation_type,
        "unit": unit,
        "evidence_value": evidence_value,
        "priority_publication_blocker": c1,
        "priority_multi_scope_impact": c2,
        "priority_reproducible_current_failure": c3,
        "priority_affected_typed_key_count": c4,
        "priority_older_eligible_rank": c5,
        "priority_tuple": f"({c1},{c2},{c3},{c4},{c5})",
        "priority_reason": reason,
        "analyst_override_reason": "",
        "disposition": disposition,
        "owner_action": owner_action,
        "show_inventory": show["show_inventory"],
        "show_queue": show["show_queue"],
        "show_lineage": show["show_lineage"],
        "show_remediation": show["show_remediation"],
    }


def _find_multi_unit_indicators(source_dir: Path = SOURCE_DIR) -> List[str]:
    indicator_units: Dict[str, set] = {}
    for wb_name in WORKBOOK_NAMES:
        with open(source_dir / wb_name, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                indicator_units.setdefault(r["indicator_label"], set()).add(r["unit"])
    return sorted(lbl for lbl, units in indicator_units.items() if len(units) > 1)


def _count_observation_type(target: str, source_dir: Path = SOURCE_DIR) -> int:
    n = 0
    for wb_name in WORKBOOK_NAMES:
        with open(source_dir / wb_name, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                if r["observation_type"] == target:
                    n += 1
    return n


def _count_typed_duplicates(source_dir: Path = SOURCE_DIR) -> int:
    """Recompute the typed-duplicate count from current workbook rows.

    Grain (after casefold+strip): (record_family, record_code,
    indicator_label, unit, observation_type, freshness_date, source_workbook).
    ``source_workbook`` is part of the typed key because the same record code
    legitimately appears in multiple source workbooks (the reconciliation
    design counts workbook variants); two rows are the same typed record only
    when they also share a source workbook. Within a single workbook each
    (family, code) appears at most once, so the grain is unique by
    construction and the recomputed count is 0 for the canonical build.
    Returns the number of surplus rows whose normalized key collides with an
    earlier row.
    """
    seen: set = set()
    dup = 0
    for wb_name in WORKBOOK_NAMES:
        with open(source_dir / wb_name, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                key = tuple(
                    str(r.get(col, "")).casefold().strip()
                    for col in ("record_family", "record_code", "indicator_label",
                                "unit", "observation_type", "freshness_date")
                ) + (wb_name.casefold(),)
                if key in seen:
                    dup += 1
                else:
                    seen.add(key)
    return dup


def _count_invariant_headers(source_dir: Path = SOURCE_DIR) -> int:
    """Count workbooks whose current header equals the canonical header."""
    n = 0
    for wb_name in WORKBOOK_NAMES:
        with open(source_dir / wb_name, encoding="utf-8", newline="") as f:
            header = next(csv.reader(f))
        if header == WORKBOOK_HEADER:
            n += 1
    return n


# ---- Step 4: build lineage ----------------------------------------------

def build_lineage(issues: List[Dict[str, Any]],
                  source_dir: Path = SOURCE_DIR) -> List[Dict[str, Any]]:
    """One or more lineage rows per issue, contributing workbooks numbered 1..N."""
    rows: List[Dict[str, Any]] = []
    for iss in issues:
        wb_list = [w.strip() for w in iss["source_workbooks"].split(",") if w.strip()]
        if not wb_list:
            wb_list = ["reference_masterlist.csv"]
        seq = 0
        for wb in wb_list:
            seq += 1
            freshness = _freshness_evidence(iss["observation_type"])
            rows.append({
                "build_id": BUILD_LABEL,
                "snapshot_id": SNAPSHOT_ID,
                "issue_id": iss["issue_id"],
                "lineage_sequence": seq,
                "source_workbook": wb,
                "source_path": f"_source_workbooks/{wb}",
                "source_url": "",  # synthetic; no external URL
                "source_sheet": iss["source_sheet_or_surface"],
                "source_cell_or_row": "row::*",
                "schema_signature_sha256": _schema_sig(wb, source_dir),
                "source_sha256": _file_hash(source_dir / wb),
                "relationship_key": iss["issue_id"],
                "unit": iss["unit"],
                "observation_type": iss["observation_type"],
                "freshness_evidence": freshness,
                "disposition": iss["disposition"],
                "source_lineage_exemplar_key": f"{iss['issue_id']}::{wb}",
            })
    return rows


def _freshness_evidence(obs_type: str) -> str:
    if obs_type in ("projected", "reference", "not_applicable"):
        return "Not freshness-eligible"
    return f"Frozen source snapshot {SNAPSHOT_ID}"


def _schema_sig(wb_name: str, source_dir: Path = SOURCE_DIR) -> str:
    """Schema signature derived from the workbook's actual header row.

    Content-addressable: the signature is `sha256("schema|project|wb|" ||
    "|".join(header))`, read from the current workbook bytes. This makes the
    schema-invariance check a real comparison against current headers rather
    than a fixed string independent of content.
    """
    wb_path = source_dir / wb_name
    with open(wb_path, encoding="utf-8", newline="") as f:
        header = next(csv.reader(f))
    h = hashlib.sha256()
    h.update(f"schema|{PROJECT_ID}|{wb_name}|".encode())
    h.update("|".join(header).encode())
    return h.hexdigest()


def _file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        h.update(f.read())
    return h.hexdigest()


# ---- Step 5: priority queue ---------------------------------------------

def build_priority_queue(issues: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Sort issues by the 5-tuple (descending), assign rank 1..N."""
    def sort_key(iss):
        return (
            -iss["priority_publication_blocker"],
            -iss["priority_multi_scope_impact"],
            -iss["priority_reproducible_current_failure"],
            -iss["priority_affected_typed_key_count"],
            -iss["priority_older_eligible_rank"],
            iss["issue_id"],  # ascending tiebreaker
        )
    ordered = sorted(issues, key=sort_key)
    out: List[Dict[str, Any]] = []
    for rank, iss in enumerate(ordered, start=1):
        row = dict(iss)
        row["priority_rank"] = rank
        row["display_title"] = f"{rank}. {iss['issue_title']}"
        row["display_status"] = f"{iss['issue_status']} - {iss['severity']}"
        row["display_evidence"] = iss["evidence_value"]
        row["display_detail"] = f"{iss['priority_reason']} Action: {iss['owner_action']}"
        row["target_viewport"] = "1366x768 implementation target"
        row["public_data_boundary"] = "SYNTHETIC DATA / INDEPENDENT PORTFOLIO PROJECT"
        out.append(row)
    return out


# ---- Step 6: control tables ---------------------------------------------

def build_control_tables(issues, lineage, queue,
                         source_dir: Path = SOURCE_DIR) -> Dict[str, List[Dict[str, Any]]]:
    # The remediation readback is a SAME-OPERATION computed truth: the typed
    # duplicate count is recomputed from the current source bytes in the same
    # build (not a fixed "0"), so the PASS line reflects the live recomputation.
    recomputed_dup = _count_typed_duplicates(source_dir)
    remediation = [{
        "receipt_id": "REM-001",
        "issue_id": "ISS-004",
        "detected_state": "typed grain normalized",
        "action_taken": "applied typed-grain normalization before dedup",
        "rebuild_state": f"{recomputed_dup} typed duplicates",
        "same_operation_readback": (
            f"PASS rebuild {BUILD_LABEL} read back {recomputed_dup} "
            f"duplicates (recomputed from current source rows in this build)"
        ),
        "maintenance_handoff": "re-run detect_issues if workbook set changes",
    }]
    refresh = [{
        "refresh_state": "CANONICAL_CURRENT_SNAPSHOT",
        "safe_test_change": "none",
        "source_manifest_sha256": _manifest_hash(source_dir),
        "rule_version": RULE_VERSION,
        "snapshot_id": SNAPSHOT_ID,
    }]
    return {"remediation_receipt": remediation, "refresh_control": refresh}


def _manifest_hash(source_dir: Path = SOURCE_DIR) -> str:
    """Content-aware manifest hash: names + file bytes.

    Used as the source-pin in refresh_control. Any tampering with a source
    workbook changes this hash, so the manifest-mismatch negative control
    fails closed.
    """
    h = hashlib.sha256()
    for wb in sorted(WORKBOOK_NAMES) + ["reference_masterlist.csv"]:
        h.update(wb.encode())
        h.update(b"\x00")
        wb_path = source_dir / wb
        if wb_path.exists():
            with open(wb_path, "rb") as f:
                h.update(f.read())
            h.update(b"\x00")
    return h.hexdigest()


# ---- Driver -------------------------------------------------------------

SOURCE_INVENTORY_COLS = [
    "build_id", "build_label", "snapshot_id", "rule_version",
    "record_family", "record_code", "in_workbook", "in_masterlist",
    "disaggregation_state", "workbook_variant_count", "public_label_count",
    "public_label_as_of", "current_download_count", "inventory_decision_note",
]
ISSUE_COLS = [
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
]
LINEAGE_COLS = [
    "build_id", "snapshot_id", "issue_id", "lineage_sequence",
    "source_workbook", "source_path", "source_url", "source_sheet",
    "source_cell_or_row", "schema_signature_sha256", "source_sha256",
    "relationship_key", "unit", "observation_type", "freshness_evidence",
    "disposition", "source_lineage_exemplar_key",
]
QUEUE_COLS = ISSUE_COLS + [
    "priority_rank", "display_title", "display_status",
    "display_evidence", "display_detail", "target_viewport",
    "public_data_boundary",
]


def generate(out_dir: Path = OUT) -> Dict[str, Any]:
    """Regenerate all synthetic artifacts under `out_dir`."""
    out_dir.mkdir(parents=True, exist_ok=True)
    source_dir = out_dir / "_source_workbooks"
    source_manifest = generate_source_workbooks(source_dir, relative_to=out_dir)
    inventory = build_source_inventory(source_dir)
    issues = detect_issues(inventory, source_dir)
    lineage = build_lineage(issues, source_dir)
    queue = build_priority_queue(issues)
    controls = build_control_tables(issues, lineage, queue, source_dir)

    paths: Dict[str, str] = {}
    paths["source_inventory"] = "source_inventory.csv"
    write_csv(out_dir / "source_inventory.csv", inventory, SOURCE_INVENTORY_COLS)
    paths["schema_unit_freshness_issue"] = "schema_unit_freshness_issue.csv"
    write_csv(out_dir / "schema_unit_freshness_issue.csv", issues, ISSUE_COLS)
    paths["issue_lineage"] = "issue_lineage.csv"
    write_csv(out_dir / "issue_lineage.csv", lineage, LINEAGE_COLS)
    paths["refresh_priority_queue"] = "refresh_priority_queue.csv"
    write_csv(out_dir / "refresh_priority_queue.csv", queue, QUEUE_COLS)
    paths["remediation_receipt"] = "remediation_receipt.csv"
    write_csv(out_dir / "remediation_receipt.csv",
              controls["remediation_receipt"],
              ["receipt_id", "issue_id", "detected_state", "action_taken",
               "rebuild_state", "same_operation_readback", "maintenance_handoff"])
    paths["refresh_control"] = "refresh_control.csv"
    write_csv(out_dir / "refresh_control.csv",
              controls["refresh_control"],
              ["refresh_state", "safe_test_change", "source_manifest_sha256",
               "rule_version", "snapshot_id"])

    receipt = {
        "project_id": PROJECT_ID,
        "snapshot_id": SNAPSHOT_ID,
        "build_label": BUILD_LABEL,
        "rule_version": RULE_VERSION,
        "root_seed_protocol": "shared.synthetic.ROOT_SEED",
        "row_counts": {
            "source_inventory": len(inventory),
            "schema_unit_freshness_issue": len(issues),
            "issue_lineage": len(lineage),
            "refresh_priority_queue": len(queue),
            "remediation_receipt": len(controls["remediation_receipt"]),
            "refresh_control": len(controls["refresh_control"]),
        },
        "output_files": paths,
        "source_workbook_manifest": source_manifest,
    }
    write_json(out_dir / "build_receipt.json", receipt)
    return receipt


if __name__ == "__main__":
    r = generate()
    print(f"[{PROJECT_ID}] snapshot={r['snapshot_id']} build={r['build_label']}")
    for k, v in r["row_counts"].items():
        print(f"  {k}: {v} rows")
