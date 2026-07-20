"""Reusable fail-closed validator for the Urban Mobility Gap Diagnostic.

This module is the production validation layer for the project's generated
artifacts. It loads actual generated rows and asserts every material
analytical and presentation invariant. Tests in
`projects/urban-mobility-gap/tests/test_umgd.py` import this module and run
canonical outputs through `validate_artifacts`, and negative fixtures mutate
real outputs and assert the stable rejection code returned here.

Design:
- A validation failure raises `ValidationError` carrying a stable `code`.
- `validate_artifacts(out_dir)` runs every check and returns a result dict on
  success, so callers can use it both as a positive control and as a negative
  fixture target by catching the exception and inspecting `err.code`.
- All recomputations use only the data in the generated files (plus the
  documented rules), so a mutation to any output is detectable.

SYNTHETIC DATA / INDEPENDENT PORTFOLIO PROJECT
"""
from __future__ import annotations

import csv
import json
import math
from pathlib import Path
from typing import Any, Dict, List, Tuple

# ---------------------------------------------------------------------------
# Required schemas (canonical column contracts)
# ---------------------------------------------------------------------------
CITY_MODEL_REQUIRED = [
    "city_key", "city_id", "city_name", "country_code", "country_name",
    "subregion", "income_band", "cohort_label", "cohort_n",
    "actual_association", "expected_association", "gap_signed", "gap_absolute",
    "gap_direction", "review_flag", "is_default_focus",
    "rail_share", "bus_share", "ferry_share", "informal_share", "other_share",
    "modal_total",
    "raw_population", "raw_density", "raw_income_index", "raw_fleet_size",
    "raw_network_length", "raw_frequency_index", "raw_fare_index",
    "scaled_population", "scaled_density", "scaled_income_index",
    "scaled_fleet_size", "scaled_network_length", "scaled_frequency_index",
    "scaled_fare_index", "geographic_caveat_flag", "safe_copy",
]
PRESENTATION_REQUIRED = [
    "presentation_row_key", "panel", "model_label", "snapshot_id",
    "city_id", "city_name", "country_code", "country_name",
    "actual_association", "expected_association", "gap_signed", "gap_absolute",
    "gap_direction", "review_flag", "is_default_focus",
    "measure_name", "measure_value", "equality_reference_value",
    "equality_axis_min", "equality_axis_max", "empty_state_copy",
    "scaled_value", "transform_label",
]
ROBUSTNESS_GROUP_REQUIRED = [
    "group_id", "variant_label", "method", "group_name", "n_cities",
    "feature_drop", "coef_scale", "intercept_shift",
    "review_set_size", "baseline_review_set_size",
    "stability_flag", "jaccard_threshold", "jaccard_value",
    "mae", "rmse", "bias",
]
ROBUSTNESS_SUMMARY_REQUIRED = ["bucket_label", "count", "pct_of_total", "min_jaccard"]
GEO_WARN_REQUIRED = ["city_id", "city_name", "subregion", "income_band",
                     "warning", "context_group_size"]

# A fixed predictor inventory. The presentation predictor panel must contain
# exactly these seven raw features per city (each row also carries its scaled
# value). Adding a target/context field here would be a leakage regression.
PREDICTOR_INVENTORY = ["population", "density", "income_index", "fleet_size",
                       "network_length", "frequency_index", "fare_index"]
TARGET_DENYLIST = {"gap_signed", "gap_absolute", "actual_association",
                   "expected_association", "modal_total"}

# Ranking / league-table language must not appear in visible copy.
BANNED_VISIBLE_LANGUAGE = ["best city", "worst city", "leaderboard", "predicts",
                           "generalizes", "outperforms"]

EXPECTED_N_CITIES = 24
EXPECTED_PANELS = {"score", "diagnostic", "modal", "predictor"}
# Per-city presentation row inventory: 1 score + 4 diagnostic + 5 modal +
# 7 predictor = 17 rows.
PER_CITY_PANEL_ROWS = {
    "score": 1,
    "diagnostic": 4,
    "modal": 5,
    "predictor": 7,
}


class ValidationError(Exception):
    """Stable-code validation failure."""

    def __init__(self, code: str, message: str):
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message


# ---------------------------------------------------------------------------
# IO helpers
# ---------------------------------------------------------------------------
def _load_csv(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        raise ValidationError("FILE_MISSING", f"missing file: {path.name}")
    with open(path, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    return rows


def _load_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise ValidationError("FILE_MISSING", f"missing file: {path.name}")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _f(val: Any) -> float:
    if val is None or val == "":
        raise ValidationError("EMPTY_NUMERIC", "expected numeric value, got empty cell")
    return float(val)


def _b(val: Any) -> bool:
    return str(val).strip().lower() in ("true", "1", "yes")


# ---------------------------------------------------------------------------
# Documented derivation rules (mirror generate.py; kept here so the validator
# does not import the generator)
# ---------------------------------------------------------------------------
def _percentile_90(values: List[float]) -> float:
    s = sorted(values)
    k = int(round(0.90 * (len(s) - 1)))
    return s[k]


def _median(values: List[float]) -> float:
    s = sorted(values)
    n = len(s)
    mid = n // 2
    if n % 2 == 1:
        return s[mid]
    return (s[mid - 1] + s[mid]) / 2.0


def derive_default_focus(abs_gaps_by_cid: Dict[str, float]) -> str:
    med = _median(list(abs_gaps_by_cid.values()))
    return min(abs_gaps_by_cid,
               key=lambda cid: (round(abs(abs_gaps_by_cid[cid] - med), 9), cid))


def derive_review_threshold(abs_gaps: List[float]) -> float:
    return _percentile_90(abs_gaps)


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------
def _check_schema(rows: List[Dict[str, Any]], required: List[str], fname: str) -> None:
    if not rows:
        raise ValidationError("EMPTY_FILE", f"{fname} has no rows")
    header = list(rows[0].keys())
    missing = [c for c in required if c not in header]
    if missing:
        raise ValidationError("SCHEMA_MISSING_COLUMNS",
                              f"{fname} missing columns: {missing}")


def _check_city_keys_and_count(city_model: List[Dict[str, Any]]) -> None:
    if len(city_model) != EXPECTED_N_CITIES:
        raise ValidationError("CITY_COUNT_MISMATCH",
                              f"expected {EXPECTED_N_CITIES} city rows, got {len(city_model)}")
    city_ids = [r["city_id"] for r in city_model]
    if len(set(city_ids)) != EXPECTED_N_CITIES:
        raise ValidationError("CITY_KEY_NOT_UNIQUE",
                              "city_id values are not unique across rows")
    if any(r["city_key"] != r["city_id"] for r in city_model):
        raise ValidationError("CITY_KEY_NOT_CITY_ID",
                              "city_key must equal city_id at the city-level grain")


def _check_modal_totals(city_model: List[Dict[str, Any]]) -> None:
    lo, hi = 0.98, 1.02
    for r in city_model:
        total = (float(r["rail_share"]) + float(r["bus_share"])
                 + float(r["ferry_share"]) + float(r["informal_share"])
                 + float(r["other_share"]))
        if total < lo or total > hi:
            raise ValidationError("MODAL_TOTAL_OUT_OF_BAND",
                                  f"{r['city_id']} modal total {total} outside [{lo}, {hi}]")
        stored_total = float(r["modal_total"])
        if abs(stored_total - total) > 1e-6:
            raise ValidationError("MODAL_TOTAL_MISMATCH",
                                  f"{r['city_id']} stored modal_total {stored_total} != recomputed {total}")


def _check_association_and_gap(city_model: List[Dict[str, Any]]) -> None:
    for r in city_model:
        actual = float(r["actual_association"])
        expected = float(r["expected_association"])
        for label, v in [("actual", actual), ("expected", expected)]:
            if v < 0.0 or v > 1.0:
                raise ValidationError("ASSOCIATION_OUT_OF_AXIS",
                                      f"{r['city_id']} {label}={v} outside [0,1]")
        recomputed_actual = (float(r["rail_share"]) + float(r["bus_share"])
                             + float(r["ferry_share"]))
        if abs(actual - round(recomputed_actual, 6)) > 1e-5:
            raise ValidationError("ACTUAL_NOT_RAIL_BUS_FERRY",
                                  f"{r['city_id']} actual != rail+bus+ferry")
        gap_signed = float(r["gap_signed"])
        if abs(gap_signed - (actual - expected)) > 1e-5:
            raise ValidationError("GAP_SIGNED_ARITHMETIC",
                                  f"{r['city_id']} gap_signed != actual - expected")
        gap_abs = float(r["gap_absolute"])
        if abs(gap_abs - abs(gap_signed)) > 1e-5:
            raise ValidationError("GAP_ABSOLUTE_ARITHMETIC",
                                  f"{r['city_id']} gap_absolute != |gap_signed|")
        # direction consistency
        if gap_signed > 1e-9:
            exp_dir = "Above expected"
        elif gap_signed < -1e-9:
            exp_dir = "Below expected"
        else:
            exp_dir = "At expected"
        if r["gap_direction"] != exp_dir:
            raise ValidationError("GAP_DIRECTION_MISMATCH",
                                  f"{r['city_id']} direction {r['gap_direction']} != {exp_dir}")


def _check_default_focus(city_model: List[Dict[str, Any]]) -> str:
    defaults = [r for r in city_model if _b(r["is_default_focus"])]
    if len(defaults) != 1:
        raise ValidationError("DEFAULT_FOCUS_COUNT",
                              f"expected exactly 1 default focus, got {len(defaults)}")
    abs_gaps = {r["city_id"]: float(r["gap_absolute"]) for r in city_model}
    expected_default = derive_default_focus(abs_gaps)
    stored_default = defaults[0]["city_id"]
    if stored_default != expected_default:
        raise ValidationError("DEFAULT_FOCUS_NOT_MEDIAN",
                              f"stored default {stored_default} != expected {expected_default}")
    return stored_default


def _check_review_set(city_model: List[Dict[str, Any]]) -> Tuple[float, set]:
    abs_gaps = [float(r["gap_absolute"]) for r in city_model]
    threshold = derive_review_threshold(abs_gaps)
    expected_review = {r["city_id"] for r in city_model
                       if float(r["gap_absolute"]) >= threshold - 1e-12}
    stored_review = {r["city_id"] for r in city_model
                     if r["review_flag"] == "review case"}
    if stored_review != expected_review:
        raise ValidationError("REVIEW_SET_MISMATCH",
                              f"stored review {sorted(stored_review)} != expected {sorted(expected_review)}")
    if not stored_review:
        raise ValidationError("REVIEW_SET_EMPTY",
                              "review set must be nonempty so the diagnostic surfaces cases")
    # context cities must be the complement
    stored_context = {r["city_id"] for r in city_model if r["review_flag"] == "context"}
    if stored_context != ({r["city_id"] for r in city_model} - stored_review):
        raise ValidationError("REVIEW_FLAG_INVALID_VALUE",
                              "review_flag must be exactly 'review case' or 'context'")
    return threshold, stored_review


def _check_predictor_denylist(receipt: Dict[str, Any]) -> None:
    feats = set(receipt.get("feature_set", []))
    overlap = feats & TARGET_DENYLIST
    if overlap:
        raise ValidationError("PREDICTOR_DENYLIST_VIOLATION",
                              f"forbidden target/context fields in feature_set: {overlap}")
    if set(feats) != set(PREDICTOR_INVENTORY):
        raise ValidationError("PREDICTOR_INVENTORY_MISMATCH",
                              f"feature_set {sorted(feats)} != inventory {sorted(PREDICTOR_INVENTORY)}")


def _check_presentation(presentation: List[Dict[str, Any]],
                        city_model: List[Dict[str, Any]],
                        receipt: Dict[str, Any]) -> None:
    # Unique single-root key
    keys = [r["presentation_row_key"] for r in presentation]
    if len(set(keys)) != len(keys):
        raise ValidationError("PRESENTATION_KEY_DUPLICATE",
                              "presentation_row_key must be unique")
    # Panels contract
    panels = {r["panel"] for r in presentation}
    if panels != EXPECTED_PANELS:
        raise ValidationError("PRESENTATION_PANEL_SET",
                              f"panel set {sorted(panels)} != {sorted(EXPECTED_PANELS)}")
    # Per-city panel row counts
    city_ids = {r["city_id"] for r in city_model}
    n_cities = len(city_ids)
    for panel, per_city in PER_CITY_PANEL_ROWS.items():
        panel_rows = [r for r in presentation if r["panel"] == panel]
        if len(panel_rows) != n_cities * per_city:
            raise ValidationError("PRESENTATION_PANEL_COUNT",
                                  f"panel '{panel}' has {len(panel_rows)} rows, "
                                  f"expected {n_cities * per_city}")
    # Score panel must have exactly one row per city and 24 unique coords
    score_rows = [r for r in presentation if r["panel"] == "score"]
    score_cities = [r["city_id"] for r in score_rows]
    if len(score_cities) != len(set(score_cities)):
        raise ValidationError("SCORE_PANEL_DUPLICATE_CITY",
                              "score panel has duplicate city_id")
    coords = {(r["actual_association"], r["expected_association"]) for r in score_rows}
    if len(coords) != n_cities:
        raise ValidationError("SCORE_PANEL_COORDINATE_COLLISION",
                              f"score panel has {len(coords)} unique coords, expected {n_cities}")
    # Fixed equality axis
    for r in presentation:
        if float(r["equality_axis_min"]) != 0.0 or float(r["equality_axis_max"]) != 1.0:
            raise ValidationError("EQUALITY_AXIS_NOT_FIXED",
                                  f"{r['presentation_row_key']} axis not fixed at [0,1]")
        if r["measure_name"] == "actual_association" and r["panel"] == "score":
            if r["equality_reference_value"] in ("", None):
                raise ValidationError("MISSING_EQUALITY_REFERENCE",
                                      "score row missing equality_reference_value")
    # Predictor panel uses exactly the canonical predictor inventory
    pred_measures = {r["measure_name"] for r in presentation if r["panel"] == "predictor"}
    if pred_measures != set(PREDICTOR_INVENTORY):
        raise ValidationError("PREDICTOR_PANEL_INVENTORY",
                              f"predictor measures {sorted(pred_measures)} != inventory")
    # No target/context field appears as a predictor measure
    leaked = pred_measures & TARGET_DENYLIST
    if leaked:
        raise ValidationError("PREDICTOR_PANEL_LEAK",
                              f"target/context fields in predictor panel: {leaked}")
    # Diagnostic panel measures
    diag_measures = {r["measure_name"] for r in presentation if r["panel"] == "diagnostic"}
    if diag_measures != {"actual_association", "expected_association",
                         "gap_absolute", "gap_signed"}:
        raise ValidationError("DIAGNOSTIC_PANEL_MEASURES",
                              f"diagnostic measures {sorted(diag_measures)} unexpected")
    # Modal panel measures
    modal_measures = {r["measure_name"] for r in presentation if r["panel"] == "modal"}
    if modal_measures != {"rail_share", "bus_share", "ferry_share",
                          "informal_share", "other_share"}:
        raise ValidationError("MODAL_PANEL_MEASURES",
                              f"modal measures {sorted(modal_measures)} unexpected")


def _check_ranking_language(city_model: List[Dict[str, Any]],
                            presentation: List[Dict[str, Any]]) -> None:
    for r in city_model:
        text = (r.get("safe_copy") or "").lower()
        for b in BANNED_VISIBLE_LANGUAGE:
            if b in text:
                raise ValidationError("BANNED_RANKING_LANGUAGE",
                                      f"{r['city_id']} safe_copy contains '{b}'")
    # empty_state_copy is also visible; must not contain ranking language
    for r in presentation:
        text = (r.get("empty_state_copy") or "").lower()
        for b in BANNED_VISIBLE_LANGUAGE:
            if b in text:
                raise ValidationError("BANNED_RANKING_LANGUAGE",
                                      f"{r['presentation_row_key']} empty_state_copy contains '{b}'")


def _check_robustness(robustness_group: List[Dict[str, Any]],
                      robustness_summary: List[Dict[str, Any]],
                      city_model: List[Dict[str, Any]],
                      receipt: Dict[str, Any]) -> None:
    if len(robustness_group) != 8:
        raise ValidationError("ROBUSTNESS_VARIANT_COUNT",
                              f"expected 8 robustness variants, got {len(robustness_group)}")
    labels = [r["variant_label"] for r in robustness_group]
    if len(set(labels)) != 8:
        raise ValidationError("ROBUSTNESS_VARIANT_DUPLICATE",
                              f"duplicate variant labels: {labels}")
    if labels[0] != "baseline":
        raise ValidationError("ROBUSTNESS_BASELINE_FIRST",
                              f"first variant must be 'baseline', got {labels[0]}")

    # Recompute baseline review set from stored city rows.
    baseline_review = {r["city_id"] for r in city_model
                       if r["review_flag"] == "review case"}
    threshold = derive_review_threshold([float(r["gap_absolute"]) for r in city_model])
    actual_by_cid = {r["city_id"]: float(r["actual_association"]) for r in city_model}
    scaled_by_cid = {r["city_id"]: {f: float(r[f"scaled_{f}"]) for f in PREDICTOR_INVENTORY}
                     for r in city_model}
    base_coef = receipt["coef"]
    base_intercept = receipt["intercept"]
    base_features = receipt["feature_set"]
    jac_threshold = float(receipt["robustness_jaccard_threshold"])

    stable = 0
    unstable = 0
    jaccards: List[float] = []
    for row in robustness_group:
        # Visible method label must agree with the structured metadata. The
        # structured fields (feature_drop, coef_scale, intercept_shift) are the
        # authoritative record of the executed perturbation; the human-readable
        # `method` string must not contradict them or claim a fit/refit that
        # never occurs in this fixed-coefficient design.
        _check_method_label_consistency(row)
        spec = _reconstruct_variant_spec(row, base_coef, base_intercept, base_features)
        # Recompute expected, review set, MAE/RMSE/bias under this variant
        expected = {}
        for cid in actual_by_cid:
            val = spec["intercept"] + sum(spec["coef"][f] * scaled_by_cid[cid][f]
                                          for f in spec["feature_set"])
            # Round to 6 decimals to match the generator's _expected_association
            # exactly, so residual metrics recompute bit-for-bit from stored rows.
            expected[cid] = round(max(0.0, min(1.0, val)), 6)
        variant_review = {cid for cid in actual_by_cid
                          if abs(actual_by_cid[cid] - expected[cid]) >= threshold - 1e-12}
        cids = sorted(actual_by_cid)
        resid = [actual_by_cid[c] - expected[c] for c in cids]
        n = len(resid)
        mae = round(sum(abs(v) for v in resid) / n, 6)
        rmse = round(math.sqrt(sum(v * v for v in resid) / n), 6)
        bias = round(sum(resid) / n, 6)
        inter = len(baseline_review & variant_review)
        union = len(baseline_review | variant_review)
        jac = inter / union if union else (1.0 if not baseline_review and not variant_review else 0.0)
        # Jaccard value must match
        if abs(round(jac, 6) - float(row["jaccard_value"])) > 1e-6:
            raise ValidationError("ROBUSTNESS_JACCARD_MISMATCH",
                                  f"{row['variant_label']}: stored jaccard {row['jaccard_value']} "
                                  f"!= recomputed {round(jac, 6)}")
        if abs(round(mae, 6) - float(row["mae"])) > 1e-6:
            raise ValidationError("ROBUSTNESS_MAE_MISMATCH",
                                  f"{row['variant_label']}: stored mae {row['mae']} != {round(mae, 6)}")
        if abs(round(rmse, 6) - float(row["rmse"])) > 1e-6:
            raise ValidationError("ROBUSTNESS_RMSE_MISMATCH",
                                  f"{row['variant_label']}: stored rmse {row['rmse']} != {round(rmse, 6)}")
        if abs(round(bias, 6) - float(row["bias"])) > 1e-6:
            raise ValidationError("ROBUSTNESS_BIAS_MISMATCH",
                                  f"{row['variant_label']}: stored bias {row['bias']} != {round(bias, 6)}")
        # review set sizes
        if int(row["review_set_size"]) != len(variant_review):
            raise ValidationError("ROBUSTNESS_REVIEW_SIZE_MISMATCH",
                                  f"{row['variant_label']}: review_set_size {row['review_set_size']} "
                                  f"!= {len(variant_review)}")
        if int(row["baseline_review_set_size"]) != len(baseline_review):
            raise ValidationError("ROBUSTNESS_BASELINE_SIZE_MISMATCH",
                                  f"baseline_review_set_size {row['baseline_review_set_size']} "
                                  f"!= {len(baseline_review)}")
        # stability flag from threshold
        expected_flag = "stable" if jac >= jac_threshold else "unstable"
        if row["stability_flag"] != expected_flag:
            raise ValidationError("ROBUSTNESS_STABILITY_FLAG",
                                  f"{row['variant_label']}: stability_flag {row['stability_flag']} "
                                  f"!= {expected_flag}")
        # jaccard in unit interval
        jv = float(row["jaccard_value"])
        if jv < 0.0 or jv > 1.0:
            raise ValidationError("ROBUSTNESS_JACCARD_OUT_OF_RANGE",
                                  f"{row['variant_label']}: jaccard {jv} outside [0,1]")
        jaccards.append(jac)
        if expected_flag == "stable":
            stable += 1
        else:
            unstable += 1

    # Summary checks
    summary_by_bucket = {r["bucket_label"]: r for r in robustness_summary}
    if set(summary_by_bucket) != {"stable", "unstable"}:
        raise ValidationError("ROBUSTNESS_SUMMARY_BUCKETS",
                              f"summary buckets {sorted(summary_by_bucket)} != ['stable','unstable']")
    if int(summary_by_bucket["stable"]["count"]) != stable:
        raise ValidationError("ROBUSTNESS_SUMMARY_STABLE_COUNT", "stable count mismatch")
    if int(summary_by_bucket["unstable"]["count"]) != unstable:
        raise ValidationError("ROBUSTNESS_SUMMARY_UNSTABLE_COUNT", "unstable count mismatch")
    total = stable + unstable
    if abs(float(summary_by_bucket["stable"]["pct_of_total"]) - stable / total) > 1e-6:
        raise ValidationError("ROBUSTNESS_SUMMARY_STABLE_PCT", "stable pct mismatch")
    stable_jacs = [j for j in jaccards if j >= jac_threshold]
    exp_min_stable = round(min(stable_jacs), 6) if stable_jacs else 0.0
    if abs(float(summary_by_bucket["stable"]["min_jaccard"]) - exp_min_stable) > 1e-6:
        raise ValidationError("ROBUSTNESS_SUMMARY_MIN_JACCARD",
                              f"stable min_jaccard {summary_by_bucket['stable']['min_jaccard']} != {exp_min_stable}")


def _check_method_label_consistency(row: Dict[str, Any]) -> None:
    """Fail closed if the visible `method` label contradicts the structured
    metadata (`feature_drop`, `coef_scale`, `intercept_shift`).

    The structured fields are the authoritative record of the executed
    perturbation in this fixed-coefficient design. The human-readable `method`
    string is visible copy and must not claim a fit/refit that never occurs,
    and must not mislabel which parameter changed. This guards the regression
    where a feature-drop row was once labeled 'refit intercept to zero
    coefficient on it' even though no intercept fitting occurs.

    Rules:
      - A label may never claim fit/refit/fitting/tuning/retrain/optimiz; there
        is no fitted model in this synthetic design.
      - feature_drop set (non-empty): the label must name that feature and must
        state the coefficient is set to 0.0 (zeroed); it must not claim an
        intercept change.
      - coef_scale != 1.0: the label must reference scaling coefficients by the
        stored factor and must not claim a feature drop or intercept change.
      - intercept_shift != 0.0: the label must reference an intercept change of
        the stored magnitude and must not claim a feature drop.
      - baseline (all identity): the label must claim no perturbation.
    """
    label = row["variant_label"]
    method = str(row.get("method", ""))
    mlow = method.lower()
    coef_scale = float(row["coef_scale"])
    intercept_shift = float(row["intercept_shift"])
    feature_drop = row["feature_drop"].strip() if row["feature_drop"] else ""

    # No fitted-model language is ever truthful here.
    for bad in ("refit", "refitting", "fitted", "fit ", "fitting",
                "tuning", "tuned", "retrain", "retrain", "optimiz",
                "estimate", "regress"):
        if bad in mlow:
            raise ValidationError(
                "ROBUSTNESS_METHOD_LABEL_CONTRADICTION",
                f"{label}: method claims '{bad.strip()}' but the benchmark "
                "uses fixed coefficients with no fitting")

    if label == "baseline":
        # baseline must report no perturbation
        if any(w in mlow for w in ("drop", "set ", "scale", "coefficient",
                                   "intercept", "zero", "0.0")):
            raise ValidationError(
                "ROBUSTNESS_METHOD_LABEL_CONTRADICTION",
                f"{label}: method '{method}' claims a perturbation but the "
                "structured metadata is identity (baseline)")
        return

    if feature_drop:
        if feature_drop not in mlow:
            raise ValidationError(
                "ROBUSTNESS_METHOD_LABEL_CONTRADICTION",
                f"{label}: feature_drop={feature_drop} but method does not "
                f"name it: '{method}'")
        if "0.0" not in method and "zero" not in mlow:
            raise ValidationError(
                "ROBUSTNESS_METHOD_LABEL_CONTRADICTION",
                f"{label}: feature_drop set but method does not state the "
                f"coefficient is set to 0.0: '{method}'")
        if "intercept" in mlow and "unchanged" not in mlow:
            raise ValidationError(
                "ROBUSTNESS_METHOD_LABEL_CONTRADICTION",
                f"{label}: feature_drop variant mentions 'intercept' without "
                f"stating it is unchanged: '{method}'")
        return

    if coef_scale != 1.0:
        scale_text = f"{coef_scale:g}"
        if "scale" not in mlow or "coef" not in mlow:
            raise ValidationError(
                "ROBUSTNESS_METHOD_LABEL_CONTRADICTION",
                f"{label}: coef_scale={coef_scale} but method does not say it "
                f"scales coefficients: '{method}'")
        if scale_text not in method:
            raise ValidationError(
                "ROBUSTNESS_METHOD_LABEL_CONTRADICTION",
                f"{label}: coef_scale={coef_scale} but method does not name "
                f"that factor: '{method}'")
        return

    if intercept_shift != 0.0:
        shift_text = f"{intercept_shift:g}"
        if "intercept" not in mlow:
            raise ValidationError(
                "ROBUSTNESS_METHOD_LABEL_CONTRADICTION",
                f"{label}: intercept_shift={intercept_shift} but method does "
                f"not mention intercept: '{method}'")
        if shift_text not in method:
            raise ValidationError(
                "ROBUSTNESS_METHOD_LABEL_CONTRADICTION",
                f"{label}: intercept_shift={intercept_shift} but method does "
                f"not name that magnitude: '{method}'")
        return

    # A non-baseline row with all-identity metadata but a perturbing label.
    if any(w in mlow for w in ("drop", "set", "scale", "intercept", "zero")):
        raise ValidationError(
            "ROBUSTNESS_METHOD_LABEL_CONTRADICTION",
            f"{label}: method '{method}' describes a perturbation but the "
            "structured metadata is identity")


def _reconstruct_variant_spec(row: Dict[str, Any], base_coef: Dict[str, float],
                              base_intercept: float,
                              base_features: List[str]) -> Dict[str, Any]:
    """Reconstruct the executed variant model from stored metadata."""
    label = row["variant_label"]
    coef_scale = float(row["coef_scale"])
    intercept_shift = float(row["intercept_shift"])
    feature_drop = row["feature_drop"].strip() if row["feature_drop"] else None
    feature_set = [f for f in base_features if f != feature_drop]
    if label == "baseline":
        return {"intercept": base_intercept, "coef": dict(base_coef),
                "feature_set": list(base_features)}
    if feature_drop:
        coef = {f: (0.0 if f == feature_drop else v) for f, v in base_coef.items()}
        return {"intercept": base_intercept, "coef": coef, "feature_set": list(base_features)}
    if coef_scale != 1.0:
        coef = {f: round(v * coef_scale, 6) for f, v in base_coef.items()}
        return {"intercept": base_intercept, "coef": coef, "feature_set": list(base_features)}
    if intercept_shift != 0.0:
        return {"intercept": round(base_intercept + intercept_shift, 6),
                "coef": dict(base_coef), "feature_set": list(base_features)}
    raise ValidationError("ROBUSTNESS_VARIANT_SPEC",
                          f"cannot reconstruct spec for {label}")


def _check_receipt(receipt: Dict[str, Any], city_model: List[Dict[str, Any]]) -> None:
    # fitted_on_count
    if int(receipt["fitted_on_count"]) != EXPECTED_N_CITIES:
        raise ValidationError("RECEIPT_FITTED_COUNT",
                              "fitted_on_count != city count")
    # publication_state honest
    if "LOCAL PROTOTYPE" not in str(receipt.get("publication_state", "")):
        raise ValidationError("RECEIPT_PUBLICATION_STATE",
                              "publication_state must be LOCAL PROTOTYPE")
    # scaler method must not claim fold/holdout
    sm = str(receipt.get("scaler_method", "")).lower()
    for bad in ["fold", "holdout", "cross-validation", "train/", "training-fold"]:
        if bad in sm:
            raise ValidationError("RECEIPT_SCALER_FOLD_CLAIM",
                                  f"scaler_method claims '{bad}' but no fold exists")
    # review threshold and default match stored rows
    abs_gaps = [float(r["gap_absolute"]) for r in city_model]
    expected_threshold = round(derive_review_threshold(abs_gaps), 6)
    if abs(float(receipt["review_threshold_absolute_gap"]) - expected_threshold) > 1e-6:
        raise ValidationError("RECEIPT_REVIEW_THRESHOLD",
                              "receipt review_threshold does not recompute from rows")
    abs_by_cid = {r["city_id"]: float(r["gap_absolute"]) for r in city_model}
    expected_default = derive_default_focus(abs_by_cid)
    if receipt["default_focus_city"] != expected_default:
        raise ValidationError("RECEIPT_DEFAULT_FOCUS",
                              "receipt default_focus_city does not recompute from rows")
    stored_review = {r["city_id"] for r in city_model if r["review_flag"] == "review case"}
    if int(receipt["review_case_count"]) != len(stored_review):
        raise ValidationError("RECEIPT_REVIEW_COUNT",
                              "receipt review_case_count does not match stored rows")


# ---------------------------------------------------------------------------
# Top-level entry point
# ---------------------------------------------------------------------------
def validate_artifacts(out_dir: Path) -> Dict[str, Any]:
    """Run every check against generated artifacts in `out_dir`.

    Returns a summary dict on success. Raises ValidationError(code, message)
    on the first failure. Negative fixtures catch the exception and assert
    `err.code` equals the expected stable rejection code.
    """
    out_dir = Path(out_dir)
    city_model = _load_csv(out_dir / "city_model.csv")
    presentation = _load_csv(out_dir / "dashboard_presentation.csv")
    robustness_group = _load_csv(out_dir / "robustness_group.csv")
    robustness_summary = _load_csv(out_dir / "robustness_summary.csv")
    geo_warns = _load_csv(out_dir / "geographic_warnings.csv")
    receipt = _load_json(out_dir / "model_receipt.json")

    # Schemas
    _check_schema(city_model, CITY_MODEL_REQUIRED, "city_model.csv")
    _check_schema(presentation, PRESENTATION_REQUIRED, "dashboard_presentation.csv")
    _check_schema(robustness_group, ROBUSTNESS_GROUP_REQUIRED, "robustness_group.csv")
    _check_schema(robustness_summary, ROBUSTNESS_SUMMARY_REQUIRED, "robustness_summary.csv")
    _check_schema(geo_warns, GEO_WARN_REQUIRED, "geographic_warnings.csv")

    # City model checks
    _check_city_keys_and_count(city_model)
    _check_modal_totals(city_model)
    _check_association_and_gap(city_model)
    default_focus = _check_default_focus(city_model)
    threshold, review_set = _check_review_set(city_model)

    # Receipt checks
    _check_receipt(receipt, city_model)
    _check_predictor_denylist(receipt)

    # Presentation checks
    _check_presentation(presentation, city_model, receipt)

    # Robustness checks
    _check_robustness(robustness_group, robustness_summary, city_model, receipt)

    # Ranking language
    _check_ranking_language(city_model, presentation)

    return {
        "n_cities": len(city_model),
        "n_presentation_rows": len(presentation),
        "default_focus_city": default_focus,
        "review_threshold": round(threshold, 6),
        "review_set": sorted(review_set),
        "n_robustness_variants": len(robustness_group),
        "verdict": "VALID",
    }


# Rejection-code vocabulary (documentation; negative fixtures use these codes).
REJECTION_CODES = [
    "FILE_MISSING", "EMPTY_FILE", "SCHEMA_MISSING_COLUMNS",
    "CITY_COUNT_MISMATCH", "CITY_KEY_NOT_UNIQUE", "CITY_KEY_NOT_CITY_ID",
    "MODAL_TOTAL_OUT_OF_BAND", "MODAL_TOTAL_MISMATCH",
    "ASSOCIATION_OUT_OF_AXIS", "ACTUAL_NOT_RAIL_BUS_FERRY",
    "GAP_SIGNED_ARITHMETIC", "GAP_ABSOLUTE_ARITHMETIC", "GAP_DIRECTION_MISMATCH",
    "DEFAULT_FOCUS_COUNT", "DEFAULT_FOCUS_NOT_MEDIAN",
    "REVIEW_SET_MISMATCH", "REVIEW_SET_EMPTY", "REVIEW_FLAG_INVALID_VALUE",
    "PREDICTOR_DENYLIST_VIOLATION", "PREDICTOR_INVENTORY_MISMATCH",
    "PRESENTATION_KEY_DUPLICATE", "PRESENTATION_PANEL_SET",
    "PRESENTATION_PANEL_COUNT", "SCORE_PANEL_DUPLICATE_CITY",
    "SCORE_PANEL_COORDINATE_COLLISION", "EQUALITY_AXIS_NOT_FIXED",
    "MISSING_EQUALITY_REFERENCE", "PREDICTOR_PANEL_INVENTORY",
    "PREDICTOR_PANEL_LEAK", "DIAGNOSTIC_PANEL_MEASURES", "MODAL_PANEL_MEASURES",
    "BANNED_RANKING_LANGUAGE",
    "ROBUSTNESS_VARIANT_COUNT", "ROBUSTNESS_VARIANT_DUPLICATE",
    "ROBUSTNESS_BASELINE_FIRST", "ROBUSTNESS_JACCARD_MISMATCH",
    "ROBUSTNESS_MAE_MISMATCH", "ROBUSTNESS_RMSE_MISMATCH",
    "ROBUSTNESS_BIAS_MISMATCH", "ROBUSTNESS_REVIEW_SIZE_MISMATCH",
    "ROBUSTNESS_BASELINE_SIZE_MISMATCH", "ROBUSTNESS_STABILITY_FLAG",
    "ROBUSTNESS_JACCARD_OUT_OF_RANGE", "ROBUSTNESS_VARIANT_SPEC",
    "ROBUSTNESS_METHOD_LABEL_CONTRADICTION",
    "ROBUSTNESS_SUMMARY_BUCKETS", "ROBUSTNESS_SUMMARY_STABLE_COUNT",
    "ROBUSTNESS_SUMMARY_UNSTABLE_COUNT", "ROBUSTNESS_SUMMARY_STABLE_PCT",
    "ROBUSTNESS_SUMMARY_MIN_JACCARD",
    "RECEIPT_FITTED_COUNT", "RECEIPT_PUBLICATION_STATE",
    "RECEIPT_SCALER_FOLD_CLAIM", "RECEIPT_REVIEW_THRESHOLD",
    "RECEIPT_DEFAULT_FOCUS", "RECEIPT_REVIEW_COUNT",
]
