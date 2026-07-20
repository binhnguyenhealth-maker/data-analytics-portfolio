"""Reusable validator for the Peer Scenario Explorer generated outputs.

This module is the single fail-closed control for the project. It loads the
generated CSV/JSON outputs from a directory and returns a structured report
listing every check that failed with a STABLE rejection code. Negative tests
mutate real generated outputs and assert that this validator emits the
expected code.

The validator never imports the generator's logic to recompute peer sets; it
recomputes invariants from the OUTPUT BYTES alone (peer sets, distances,
Jaccard values). The only generator constants it references are the public
schema constants (K_PEERS, COUNTRY_CAP, N_VARIANTS, etc.) that are also
recorded in `method_receipt.json`.

Coverage philosophy (fail-closed): every roster focal city MUST appear as a
focal in every focal-keyed output table, MUST have exactly the required
scenario inventory and variant inventory, and every receipt row count MUST
reconcile to the bytes. Removing one focal across coordinated tables while
leaving the roster/receipt contract intact is rejected with a stable code
(FOCAL_COVERAGE_MISSING), closing the coordinated-omission fail-open.

Rejection codes (stable strings; do not renumber):
  - SCHEMA_MISSING_COLUMN
  - METADATA_SCHEMA_FAIL
  - ROSTER_COUNT_MISMATCH
  - ROSTER_KEY_DUPLICATE
  - FOCAL_UNKNOWN
  - FOCAL_COVERAGE_MISSING
  - SCENARIO_INVENTORY_WRONG
  - PEER_COUNT_NOT_K
  - PEER_RANKS_NOT_ONE_TO_K
  - PEER_SELF_SELECTED
  - PEER_DUPLICATE
  - PEER_RESULT_COMPOSITE_KEY_DUPLICATE
  - EXPLANATION_ORPHAN
  - EXPLANATION_RECONCILIATION_FAIL
  - EXPLANATION_COMPOSITE_KEY_DUPLICATE
  - EXPLANATION_COVERAGE_INCOMPLETE
  - CONTEXT_USED_IN_FITTING
  - CONTEXT_ROW_MISSING
  - VARIANT_COUNT_NOT_N
  - VARIANT_DUPLICATE_LABEL
  - VARIANT_DUPLICATE_SIGNATURE
  - VARIANT_METADATA_MISMATCH
  - NAMED_METHOD_MISLABELED
  - NAMED_METHOD_NO_CHANGE_VS_BASELINE
  - JACCARD_RECOMPUTE_FAIL
  - JACCARD_OUT_OF_RANGE
  - DIVERSIFIED_COUNTRY_CAP_EXCEEDED
  - NON_MAP_COLUMN_PRESENT
  - SURFACE_SET_WRONG
  - LANDING_WRONG_MARK_COUNT
  - LANDING_MULTIPLE
  - RANKING_LANGUAGE
  - RECEIPT_ROW_COUNT_DRIFT

`n_checks` in the report is a TRUE count of atomic predicates evaluated
against the data (incremented per item inspected), so it is non-zero on a
successful validation. `n_failures` is the failure count. The earlier
`n_checks=len(failures)` semantics is removed; a success report no longer
claims `n_checks=0`.

SYNTHETIC DATA / INDEPENDENT PORTFOLIO PROJECT
"""
from __future__ import annotations

import csv
import json
import math
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Tables whose grain is keyed by focal_city_id. Every roster city MUST appear
# as a focal in each of these (coordinated coverage gate).
FOCAL_KEYED_TABLES = [
    "peer_result.csv",
    "peer_explanation.csv",
    "stability.csv",
    "stability_summary.csv",
    "context_comparison.csv",
    "peer_scenario_surface.csv",
]

# Expected schema columns per file. Must match the generator's *_COLS exactly.
EXPECTED_SCHEMA: Dict[str, List[str]] = {
    "city_roster.csv": [
        "city_id", "city_name", "country_code", "country_name",
        "subregion", "income_tier", "membership_state", "exclusion_reason",
        "public_transport_access_share",
    ],
    "peer_result.csv": [
        "focal_city_id", "scenario_label", "peer_rank", "peer_city_id",
        "structural_distance", "subregion_penalty", "income_penalty",
        "final_distance", "peer_country_code", "same_country_peer_count",
    ],
    "peer_explanation.csv": [
        "focal_city_id", "scenario_label", "peer_rank", "peer_city_id",
        "structural_distance", "subregion_penalty", "income_penalty",
        "final_distance", "peer_country_code", "same_country_peer_count",
        "peer_key", "scaled_feature_name", "scaled_feature_value_focal",
        "scaled_feature_value_peer", "peer_minus_focal_scaled",
        "distance_component", "signed_component",
    ],
    "stability.csv": [
        "focal_city_id", "variant_label",
        "method_signature", "metric", "scaler", "feature_set",
        "weights_policy", "scenario_policy",
        "subregion_penalty_fraction", "income_penalty_fraction",
        "jaccard_value", "below_threshold", "threshold", "peer_set",
    ],
    "stability_summary.csv": [
        "focal_city_id", "min_jaccard", "median_jaccard", "mean_jaccard",
        "stability_badge", "threshold", "n_variants",
    ],
    "variant_metadata.csv": [
        "variant_label", "method_signature", "metric", "scaler",
        "feature_set", "weights_policy", "scenario_policy",
        "subregion_penalty_fraction", "income_penalty_fraction",
    ],
    "coverage_exposure.csv": [
        "dimension", "group_value", "n_cities", "inbound_peer_slots",
        "slot_share", "representation_ratio",
    ],
    "context_comparison.csv": [
        "focal_city_id", "scenario_label", "member_rank", "member_city_id",
        "context_measure_name", "context_measure_value",
        "context_only_label", "context_missing",
    ],
    "peer_scenario_surface.csv": [
        "surface", "snapshot_id", "method_version",
        "focal_city_id", "focal_city_name", "focal_country_code",
        "focal_country_name", "focal_subregion", "focal_income_tier",
        "scenario_label", "is_landing", "is_closest_set", "is_diversified_set",
        "member_role", "peer_rank", "peer_city_id", "peer_city_name",
        "peer_country_name",
        "structural_distance", "final_distance", "same_country_peer_count",
        "scaled_feature_name", "scaled_feature_value_focal",
        "scaled_feature_value_peer", "peer_minus_focal_scaled",
        "distance_component", "signed_component",
        "context_measure_name", "context_measure_value", "context_only_label",
        "closest_set", "diversified_set", "alternative_peer_codes_no_geo",
        "stability_badge", "jaccard_stability_flag",
        "prototype_status", "safe_copy",
    ],
}

# Method constants read from the receipt (never hard-coded assumptions here;
# the receipt is the contract). These defaults are only used when the receipt
# is absent, which itself produces a schema failure.
NON_MAP_FORBIDDEN_COLS = ["latitude", "longitude", "lat", "lon", "place", "geom"]
RANKING_TERMS = ["best", "worst", "objective", "true peer", "performance ranking"]
EXPECTED_SURFACES = [
    "peer_map_and_table",
    "why_this_peer",
    "closest_vs_diversified",
    "context_after_matching",
]
EXPECTED_SCENARIOS = ["baseline", "diversified", "core"]
# Named variants whose executed method must (a) carry the labeled method and
# (b) change at least one focal peer set vs the baseline under this cohort.
NAMED_METHOD_CHECKS = {
    "no_geography_penalty": {
        "metric": "euclidean",
        "subregion_penalty_fraction": 0.0,
        "income_penalty_fraction": 0.0,
    },
    "strong_geography_penalty": {
        "metric": "euclidean",
    },
    "manhattan_soft_penalty": {
        "metric": "manhattan",
    },
}


class _Tally:
    """Accumulates failures and a TRUE executed-check count.

    `check()` evaluates one atomic predicate against the data: it always
    increments `n_checks` (the real count of predicates evaluated, including
    passing ones) and records a failure only when the predicate is false.
    `fail()` records a failure without consuming a check slot (used when a
    single predicate has already been counted but yields multiple bad items).
    """

    def __init__(self) -> None:
        self.failures: List[Tuple[str, str]] = []
        self.n_checks: int = 0

    def check(self, cond: bool, code: str, detail: str) -> bool:
        self.n_checks += 1
        if not cond:
            self.failures.append((code, detail))
        return cond

    def fail(self, code: str, detail: str) -> None:
        self.failures.append((code, detail))

    def report(self) -> Dict[str, Any]:
        return {
            "ok": len(self.failures) == 0,
            "failures": self.failures,
            "n_failures": len(self.failures),
            "n_checks": self.n_checks,
        }


def load_dir(out_dir: Path) -> Dict[str, Any]:
    """Load all generated outputs from a directory into a dict."""
    out_dir = Path(out_dir)
    data: Dict[str, Any] = {"_dir": str(out_dir), "tables": {}, "receipt": None}
    for fname, _cols in EXPECTED_SCHEMA.items():
        p = out_dir / fname
        if p.exists():
            with open(p, encoding="utf-8") as f:
                data["tables"][fname] = list(csv.DictReader(f))
        else:
            data["tables"][fname] = None
    rp = out_dir / "method_receipt.json"
    if rp.exists():
        with open(rp, encoding="utf-8") as f:
            data["receipt"] = json.load(f)
    return data


def _jaccard(a: set, b: set) -> float:
    if not a and not b:
        return 1.0
    return len(a & b) / len(a | b) if (a | b) else 0.0


def _schema_check(t: _Tally, data) -> None:
    """Every present file must have its expected header in order."""
    for fname, cols in EXPECTED_SCHEMA.items():
        rows = data["tables"].get(fname)
        p = Path(data["_dir"]) / fname
        if rows is None:
            # absent file is reported under the checks that need it, but a
            # missing schema file is itself a schema failure.
            t.check(False, "SCHEMA_MISSING_COLUMN", f"{fname}: file absent")
            continue
        with open(p, encoding="utf-8") as f:
            header = next(csv.reader(f))
        t.check(header == cols, "SCHEMA_MISSING_COLUMN",
                f"{fname}: header mismatch. got {header}")


def _file_row_count(path: Path) -> int:
    """Count data rows in a CSV (excluding header), tolerant of blank lines."""
    with open(path, encoding="utf-8") as f:
        rd = csv.reader(f)
        try:
            next(rd)  # header
        except StopIteration:
            return 0
        return sum(1 for row in rd if any(cell.strip() for cell in row))


def validate(data: Dict[str, Any]) -> Dict[str, Any]:
    """Run every check. Returns a report with `ok`, `failures` (list of
    (code, detail)), `n_failures`, and `n_checks` (TRUE count of atomic
    predicates evaluated). `ok` is True iff failures is empty.
    """
    t = _Tally()
    receipt = data["receipt"]
    if receipt is None:
        t.fail("SCHEMA_MISSING_COLUMN", "method_receipt.json absent")
        # Cannot continue; constants unreadable.
        return t.report()

    mc = receipt["method_constants"]
    k_peers = mc["k_peers"]
    country_cap = mc["country_cap"]
    n_variants = mc["n_variants"]
    n_cities = mc["n_cities"]
    context_feature = mc["context_feature"]
    structural_features = mc["structural_features"]

    _schema_check(t, data)

    tables = data["tables"]
    roster = tables["city_roster.csv"]
    peer_result = tables["peer_result.csv"]
    peer_expl = tables["peer_explanation.csv"]
    stability = tables["stability.csv"]
    stability_summary = tables["stability_summary.csv"]
    variant_meta = tables["variant_metadata.csv"]
    context = tables["context_comparison.csv"]
    surface = tables["peer_scenario_surface.csv"]

    # --- receipt row-count reconciliation (all tables incl. focal-keyed) ----
    row_counts = receipt.get("row_counts", {})
    for key, expected in row_counts.items():
        p = Path(data["_dir"]) / f"{key}.csv"
        if not p.exists():
            t.check(False, "RECEIPT_ROW_COUNT_DRIFT",
                    f"{key}: file absent (receipt claims {expected})")
            continue
        actual = _file_row_count(p)
        t.check(actual == int(expected), "RECEIPT_ROW_COUNT_DRIFT",
                f"{key}: {actual} rows != receipt {expected}")

    # --- roster count + unique city keys ------------------------------------
    roster_ids = [r["city_id"] for r in roster] if roster else []
    roster_id_set = set(roster_ids)
    rc_receipt = int(row_counts.get("city_roster", len(roster)))
    t.check(bool(roster) and len(roster) == int(n_cities),
            "ROSTER_COUNT_MISMATCH",
            f"city_roster has {len(roster) if roster else 0} rows != n_cities {n_cities}")
    t.check(len(roster) == rc_receipt, "ROSTER_COUNT_MISMATCH",
            f"city_roster has {len(roster) if roster else 0} rows != receipt {rc_receipt}")
    t.check(len(roster_id_set) == len(roster_ids), "ROSTER_KEY_DUPLICATE",
            f"{len(roster_ids) - len(roster_id_set)} duplicate city_id values")

    # --- coordinated focal coverage across focal-keyed tables --------------
    # Every roster city must appear as a focal in every focal-keyed table,
    # and no focal in those tables may be absent from the roster. This is the
    # gate that closes the coordinated-omission fail-open (manager finding):
    # a roster city dropped from all focal-keyed tables while the roster and
    # receipt still claim it is rejected with FOCAL_COVERAGE_MISSING.
    for fname in FOCAL_KEYED_TABLES:
        rows = tables.get(fname)
        if not rows:
            t.check(False, "FOCAL_COVERAGE_MISSING",
                    f"{fname}: table absent or empty; cannot cover roster")
            continue
        table_focals = {r["focal_city_id"] for r in rows}
        # unknown focal: present in table but not in roster
        unknown = sorted(table_focals - roster_id_set)
        for fid in unknown:
            t.check(False, "FOCAL_UNKNOWN",
                    f"{fname}: focal {fid} not in roster")
        # missing focal: in roster but not in table (the coordinated-omission gate)
        missing = sorted(roster_id_set - table_focals)
        for fid in missing:
            t.check(False, "FOCAL_COVERAGE_MISSING",
                    f"{fname}: roster city {fid} has no rows")

    # --- scenario inventory per roster focal -------------------------------
    if peer_result:
        from collections import defaultdict
        scenarios_by_focal: Dict[str, set] = defaultdict(set)
        for r in peer_result:
            scenarios_by_focal[r["focal_city_id"]].add(r["scenario_label"])
        expected_scn = set(EXPECTED_SCENARIOS)
        for fid in sorted(roster_id_set):
            got = scenarios_by_focal.get(fid, set())
            t.check(got == expected_scn, "SCENARIO_INVENTORY_WRONG",
                    f"focal {fid}: scenarios {sorted(got)} != {sorted(expected_scn)}")

    # --- peer count, ranks, self, duplicate per (focal, scenario) ----------
    if peer_result:
        from collections import defaultdict
        per_fs_ranks: Dict[Tuple[str, str], List[str]] = defaultdict(list)
        per_fs_peers: Dict[Tuple[str, str], List[str]] = defaultdict(list)
        for r in peer_result:
            per_fs_ranks[(r["focal_city_id"], r["scenario_label"])].append(r["peer_rank"])
            per_fs_peers[(r["focal_city_id"], r["scenario_label"])].append(r["peer_city_id"])
        # composite-key uniqueness (focal, scenario, peer_city_id)
        seen_comp: set = set()
        comp_dups = 0
        for r in peer_result:
            comp = (r["focal_city_id"], r["scenario_label"], r["peer_city_id"])
            t.check(comp not in seen_comp, "PEER_RESULT_COMPOSITE_KEY_DUPLICATE",
                    f"duplicate peer_result composite key {comp}")
            if comp in seen_comp:
                comp_dups += 1
            seen_comp.add(comp)
        for key, ranks in per_fs_ranks.items():
            t.check(len(ranks) == k_peers, "PEER_COUNT_NOT_K",
                    f"{key}: {len(ranks)} peers != {k_peers}")
            t.check(sorted(int(x) for x in ranks) == list(range(1, k_peers + 1)),
                    "PEER_RANKS_NOT_ONE_TO_K",
                    f"{key}: ranks {sorted(ranks)} != 1..{k_peers}")
            # no self peer
            focal = key[0]
            for pid in per_fs_peers[key]:
                t.check(pid != focal, "PEER_SELF_SELECTED",
                        f"{key}: peer {pid} == focal")
            # no duplicate peer
            t.check(len(per_fs_peers[key]) == len(set(per_fs_peers[key])),
                    "PEER_DUPLICATE",
                    f"{key}: duplicate peer in {per_fs_peers[key]}")

    # --- explanation: orphans, composite-key uniqueness, reconciliation,
    #     and complete coverage for every peer at the scenario's feature
    #     inventory -----------------------------------------------------------
    if peer_result and peer_expl:
        pr_keys = {(r["focal_city_id"], r["scenario_label"], r["peer_city_id"])
                   for r in peer_result}
        sd_map = {(r["focal_city_id"], r["scenario_label"], r["peer_city_id"]):
                  float(r["structural_distance"]) for r in peer_result}

        # composite-key uniqueness in explanation: (peer_key, feature)
        seen_ek: set = set()
        for e in peer_expl:
            ek = (e.get("peer_key", ""), e.get("scaled_feature_name", ""))
            t.check(ek not in seen_ek, "EXPLANATION_COMPOSITE_KEY_DUPLICATE",
                    f"duplicate (peer_key, feature) {ek}")
            seen_ek.add(ek)

        # orphan check: every explanation peer_key must resolve to a peer_result row
        orphan_keys = set()
        for e in peer_expl:
            pkey = e.get("peer_key", "")
            parts = pkey.split("::")
            if len(parts) != 3:
                orphan_keys.add(pkey)
                continue
            if (parts[0], parts[1], parts[2]) not in pr_keys:
                orphan_keys.add(pkey)
        for pkey in orphan_keys:
            t.check(False, "EXPLANATION_ORPHAN",
                    f"explanation peer_key not in peer_result: {pkey}")

        # complete coverage: for every peer_result row, peer_explanation must
        # carry exactly one row per feature in that scenario's canonical
        # feature inventory. The inventory is derived from the output bytes
        # (the most common per-peer feature set within the scenario) so the
        # check is self-consistent and does not hardcode the core=3 rule.
        from collections import Counter, defaultdict
        feats_by_peer: Dict[Tuple[str, str, str], set] = defaultdict(set)
        for e in peer_expl:
            feats_by_peer[(e["focal_city_id"], e["scenario_label"],
                           e["peer_city_id"])].add(e["scaled_feature_name"])
        # canonical inventory per scenario = most common per-peer feature set
        inv_by_scn: Dict[str, frozenset] = {}
        peers_by_scn: Dict[str, List[Tuple[str, str, str]]] = defaultdict(list)
        for key in feats_by_peer:
            peers_by_scn[key[1]].append(key)
        for scn, peers in peers_by_scn.items():
            sets = [frozenset(feats_by_peer[p]) for p in peers]
            if sets:
                inv_by_scn[scn] = Counter(sets).most_common(1)[0][0]
        # also bind baseline inventory to the receipt's structural_features
        # contract so a whole-feature omission is caught.
        baseline_inv = inv_by_scn.get("baseline", frozenset())
        t.check(set(baseline_inv) == set(structural_features),
                "EXPLANATION_COVERAGE_INCOMPLETE",
                f"baseline feature inventory {sorted(baseline_inv)} "
                f"!= structural_features {structural_features}")
        for pr_row in peer_result:
            key = (pr_row["focal_city_id"], pr_row["scenario_label"],
                   pr_row["peer_city_id"])
            inv = inv_by_scn.get(key[1])
            if inv is None:
                t.check(False, "EXPLANATION_COVERAGE_INCOMPLETE",
                        f"{key}: no feature inventory for scenario {key[1]}")
                continue
            got = feats_by_peer.get(key, set())
            t.check(frozenset(got) == inv, "EXPLANATION_COVERAGE_INCOMPLETE",
                    f"{key}: explanation features {sorted(got)} "
                    f"!= scenario inventory {sorted(inv)}")

        # reconciliation: sum of distance_components == structural_distance
        agg: Dict[Tuple[str, str, str], float] = {}
        for e in peer_expl:
            key = (e["focal_city_id"], e["scenario_label"], e["peer_city_id"])
            agg[key] = agg.get(key, 0.0) + float(e["distance_component"])
        # tolerance accounts for 6-decimal rounding of both stored values
        tol = 1e-4
        for key, summed in agg.items():
            if key not in sd_map:
                continue
            sd = sd_map[key]
            if sd <= 0:
                continue
            t.check(abs(summed - sd) <= tol, "EXPLANATION_RECONCILIATION_FAIL",
                    f"{key}: sum(components)={summed} != structural={sd}")

    # --- context exclusion + coverage ---------------------------------------
    if roster and context:
        # context_feature must NOT appear as a structural feature
        t.check(context_feature not in structural_features,
                "CONTEXT_USED_IN_FITTING",
                f"{context_feature} appears in structural_features")
        for r in context:
            missing = str(r.get("context_missing", "")).strip().lower() in ("true", "1", "yes")
            t.check(not missing, "CONTEXT_ROW_MISSING",
                    f"context row flagged missing: focal {r.get('focal_city_id')}")
            t.check(bool(str(r.get("context_only_label", "")).strip()),
                    "CONTEXT_ROW_MISSING",
                    f"context row lacks disclosure label: focal {r.get('focal_city_id')}")

    # --- variant inventory + metadata truthfulness -------------------------
    if stability and variant_meta:
        labels = [r["variant_label"] for r in variant_meta]
        sigs = [r["method_signature"] for r in variant_meta]
        t.check(len(labels) == n_variants, "VARIANT_COUNT_NOT_N",
                f"variant_metadata has {len(labels)} variants != {n_variants}")
        t.check(len(set(labels)) == len(labels), "VARIANT_DUPLICATE_LABEL",
                f"duplicate labels: {set([l for l in labels if labels.count(l) > 1])}")
        t.check(len(set(sigs)) == len(sigs), "VARIANT_DUPLICATE_SIGNATURE",
                f"{len(sigs) - len(set(sigs))} duplicate method signatures")
        # each stability row's metadata must match the variant_metadata entry
        meta_by_label = {m["variant_label"]: m for m in variant_meta}
        meta_fields = ["method_signature", "metric", "scaler", "feature_set",
                       "weights_policy", "scenario_policy",
                       "subregion_penalty_fraction", "income_penalty_fraction"]
        for s in stability:
            m = meta_by_label.get(s["variant_label"])
            if m is None:
                continue
            for fld in meta_fields:
                t.check(str(s.get(fld, "")) == str(m.get(fld, "")),
                        "VARIANT_METADATA_MISMATCH",
                        f"{s['variant_label']}:{fld} stability={s.get(fld)} "
                        f"!= meta={m.get(fld)}")
        # each roster focal must have exactly n_variants stability rows AND
        # exactly the variant_metadata label set (no missing/extra variant).
        from collections import Counter, defaultdict
        per_focal_rows: Dict[str, List[str]] = defaultdict(list)
        for s in stability:
            per_focal_rows[s["focal_city_id"]].append(s["variant_label"])
        meta_label_set = set(labels)
        for fid in sorted(roster_id_set):
            vlist = per_focal_rows.get(fid, [])
            t.check(len(vlist) == n_variants, "VARIANT_COUNT_NOT_N",
                    f"focal {fid}: {len(vlist)} stability rows != {n_variants}")
            t.check(set(vlist) == meta_label_set, "VARIANT_COUNT_NOT_N",
                    f"focal {fid}: variant set {sorted(set(vlist))} "
                    f"!= metadata labels")

    # --- named method truthfulness + distinctness vs baseline ---------------
    if peer_result and stability:
        # baseline peer sets per focal
        baseline_sets: Dict[str, set] = {}
        for r in peer_result:
            if r["scenario_label"] == "baseline":
                baseline_sets.setdefault(r["focal_city_id"], set()).add(r["peer_city_id"])
        st_by_var: Dict[str, Dict[str, set]] = {}
        for s in stability:
            st_by_var.setdefault(s["variant_label"], {})[s["focal_city_id"]] = \
                set(s["peer_set"].split("|")) if s["peer_set"] else set()
        for vlabel, req in NAMED_METHOD_CHECKS.items():
            rows_v = [s for s in stability if s["variant_label"] == vlabel]
            if not rows_v:
                t.check(False, "NAMED_METHOD_MISLABELED",
                        f"{vlabel}: no stability rows")
                continue
            # metadata truthfulness: stored fields match the labeled method
            for s in rows_v:
                for fld, expected in req.items():
                    got = s.get(fld)
                    if fld in ("subregion_penalty_fraction", "income_penalty_fraction"):
                        t.check(abs(float(got) - float(expected)) <= 1e-9,
                                "NAMED_METHOD_MISLABELED",
                                f"{vlabel}:{s['focal_city_id']}:{fld} "
                                f"stored={got} != labeled={expected}")
                    else:
                        t.check(str(got) == str(expected),
                                "NAMED_METHOD_MISLABELED",
                                f"{vlabel}:{s['focal_city_id']}:{fld} "
                                f"stored={got} != labeled={expected}")
            # the variant must change at least one focal peer set vs baseline
            unchanged = all(st_by_var[vlabel].get(f, set()) == baseline_sets.get(f, set())
                            for f in baseline_sets)
            t.check(not unchanged, "NAMED_METHOD_NO_CHANGE_VS_BASELINE",
                    f"{vlabel}: peer sets identical to baseline for all focals")

    # --- Jaccard recomputation ---------------------------------------------
    if stability and peer_result:
        baseline_sets = {}
        for r in peer_result:
            if r["scenario_label"] == "baseline":
                baseline_sets.setdefault(r["focal_city_id"], set()).add(r["peer_city_id"])
        for s in stability:
            j_stored = float(s["jaccard_value"])
            t.check(0.0 <= j_stored <= 1.0, "JACCARD_OUT_OF_RANGE",
                    f"{s['focal_city_id']}:{s['variant_label']} jaccard={j_stored} out of [0,1]")
            base = baseline_sets.get(s["focal_city_id"], set())
            vset = set(s["peer_set"].split("|")) if s["peer_set"] else set()
            j_recomp = _jaccard(base, vset)
            t.check(abs(j_recomp - j_stored) <= 1e-6, "JACCARD_RECOMPUTE_FAIL",
                    f"{s['focal_city_id']}:{s['variant_label']} "
                    f"stored={j_stored} != recomputed={j_recomp}")

    # --- diversified country cap -------------------------------------------
    if peer_result and roster:
        roster_cc = {r["city_id"]: r["country_code"] for r in roster}
        from collections import defaultdict
        per_focal_cc: Dict[Tuple[str, str], Dict[str, int]] = defaultdict(lambda: defaultdict(int))
        for r in peer_result:
            if r["scenario_label"] != "diversified":
                continue
            per_focal_cc[(r["focal_city_id"], r["scenario_label"])][roster_cc[r["peer_city_id"]]] += 1
        for key, cc_counts in per_focal_cc.items():
            for cc, n in cc_counts.items():
                t.check(n <= country_cap, "DIVERSIFIED_COUNTRY_CAP_EXCEEDED",
                        f"{key}: country {cc} has {n} > cap {country_cap}")

    # --- non-map boundary ---------------------------------------------------
    if roster is not None:
        p = Path(data["_dir"]) / "city_roster.csv"
        with open(p, encoding="utf-8") as f:
            header = [h.lower() for h in next(csv.reader(f))]
        present = [c for c in NON_MAP_FORBIDDEN_COLS if c in header]
        t.check(not present, "NON_MAP_COLUMN_PRESENT",
                f"forbidden coordinate columns: {present}")

    # --- presentation surfaces, landing, ranking ---------------------------
    if surface:
        surfaces = {r["surface"] for r in surface}
        t.check(surfaces == set(EXPECTED_SURFACES), "SURFACE_SET_WRONG",
                f"surfaces {surfaces} != {set(EXPECTED_SURFACES)}")
        landing_rows = [r for r in surface
                        if str(r.get("is_landing", "")).lower() in ("true", "1")
                        and r["surface"] == "peer_map_and_table"
                        and r["scenario_label"] == "baseline"]
        landing_focals = {r["focal_city_id"] for r in landing_rows}
        t.check(len(landing_focals) == 1, "LANDING_MULTIPLE",
                f"{len(landing_focals)} landing focals: {landing_focals}")
        if len(landing_focals) == 1:
            lf = next(iter(landing_focals))
            n_marks = sum(1 for r in landing_rows if r["focal_city_id"] == lf)
            t.check(n_marks == k_peers + 1, "LANDING_WRONG_MARK_COUNT",
                    f"landing focal {lf} has {n_marks} marks != {k_peers + 1}")
        # ranking language scan over safe_copy
        for r in surface:
            text = (r.get("safe_copy", "") or "").lower()
            hit = next((b for b in RANKING_TERMS if b in text), None)
            t.check(hit is None, "RANKING_LANGUAGE",
                    f"ranking term {hit!r} in safe_copy for focal "
                    f"{r.get('focal_city_id')}")

    # variant_metadata must itself carry all the schema columns used above
    if variant_meta:
        for m in variant_meta:
            for fld in EXPECTED_SCHEMA["variant_metadata.csv"]:
                t.check(fld in m, "METADATA_SCHEMA_FAIL",
                        f"variant_metadata missing field {fld}")

    return t.report()


def codes(failures: List[Tuple[str, str]]) -> List[str]:
    """Return the sorted unique rejection codes from a failure list."""
    return sorted({c for c, _ in failures})


if __name__ == "__main__":
    import sys
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else (
        Path(__file__).resolve().parents[1] / "data" / "synthetic")
    data = load_dir(out)
    report = validate(data)
    print(json.dumps({"ok": report["ok"],
                      "n_failures": report["n_failures"],
                      "n_checks": report["n_checks"],
                      "codes": codes(report["failures"]),
                      "failures": report["failures"][:20]}, indent=2))
    sys.exit(0 if report["ok"] else 1)
