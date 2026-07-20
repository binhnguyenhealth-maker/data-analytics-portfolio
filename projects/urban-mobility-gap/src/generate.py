"""Project: Urban Mobility Gap Diagnostic - synthetic generator.

A frozen, cross-sectional expected-vs-actual mobility mode association
diagnostic. Per fictional city, compare a frozen benchmark ('expected')
against the observed association ('actual'), expose the gap, modal
composition, predictor profile, deterministic robustness summaries, and
geographic-context warnings. The dashboard reads from a SINGLE-ROOT
pre-joined presentation table; equality is shown via a separate y=x guide
on identical fixed axes.

Corrected design (see README.md):
- Analytical grain is one row per fictional city (24 cities, 24 scatter marks).
  The artificial central/northern/southern segment dimension was removed
  because the three segment rows were analytically identical per city.
- Each city's modal composition is generated once and retained; all derived
  actual/expected/gap/default/review values come from the retained rows.
- Default-focus city and the 90th-percentile review set recompute exactly
  from the stored city rows.
- Robustness variants are documented, deterministic sensitivity analyses that
  each alter an executed method parameter and recompute expected values, gaps,
  the review set, Jaccard against the baseline, and residual MAE/RMSE/bias
  from data. No metric is a random draw.
- No fold/holdout/cross-validation language: there is no fitted or held-out
  model. Scaling statistics are full-cohort statistics and are described
  truthfully.

SYNTHETIC DATA / INDEPENDENT PORTFOLIO PROJECT
"""
from __future__ import annotations

import math
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from shared.synthetic import (  # noqa: E402
    fictional_cities, make_rng, snapshot_id, write_csv, write_json,
)

PROJECT_ID = "urban-mobility-gap"
SNAPSHOT_ID = snapshot_id(PROJECT_ID)
MODEL_LABEL = "syn-umgd-model-001"

N_CITIES = 24
ROBUSTNESS_THRESHOLD = 0.6  # explicit Jaccard threshold
MODAL_TOLERANCE = (0.98, 1.02)  # composition gate
# A context group is "small" when its membership is at or below this disclosed
# threshold. The synthetic cohort has 24 cities across 6 countries and 5
# subregions, so several subregions have exactly 4 members.
SMALL_GROUP_MAX = 4

OUT = ROOT / "projects" / "urban-mobility-gap" / "data" / "synthetic"

FEATURE_NAMES = ["population", "density", "income_index", "fleet_size",
                 "network_length", "frequency_index", "fare_index"]
MODE_SHARE_FIELDS = ["rail_share", "bus_share", "ferry_share",
                     "informal_share", "other_share"]


# ---------------------------------------------------------------------------
# Raw + scaled features
# ---------------------------------------------------------------------------
def _scaled_features(rng) -> Dict[str, Any]:
    """Return raw + log1p-transformed + z-scored feature values per city.

    Scaling uses **full-cohort** mean/std over log1p-transformed features.
    There is no hold-out: every statistic is computed once over all 24 cities
    and disclosed in the receipt. (Earlier copy mislabeled this as
    "training-fold mean/std only"; that label was removed because no fold
    exists in this synthetic design.)
    """
    raw: Dict[str, Dict[str, float]] = {}
    cities = fictional_cities(N_CITIES, project_id=PROJECT_ID)
    # Raw values
    for c in cities:
        cid = c["city_id"]
        raw[cid] = {
            "population": float(rng.randint(200_000, 10_000_000)),
            "density": float(rng.randint(500, 15_000)),
            "income_index": round(rng.uniform(0.2, 1.0), 4),
            "fleet_size": float(rng.randint(200, 8_000)),
            "network_length": round(rng.uniform(20.0, 600.0), 2),
            "frequency_index": round(rng.uniform(0.1, 1.0), 4),
            "fare_index": round(rng.uniform(0.1, 1.0), 4),
        }
    # Scaled (log1p then z-score over the full cohort)
    means: Dict[str, float] = {}
    stds: Dict[str, float] = {}
    n = len(raw)
    for f in FEATURE_NAMES:
        vals = [math.log1p(raw[cid][f]) for cid in raw]
        m = sum(vals) / n
        var = sum((v - m) ** 2 for v in vals) / n
        means[f] = m
        stds[f] = math.sqrt(var) if var > 0 else 1.0
    scaled: Dict[str, Dict[str, float]] = {}
    for cid, feats_map in raw.items():
        scaled[cid] = {f: round((math.log1p(feats_map[f]) - means[f]) / stds[f], 6)
                       for f in FEATURE_NAMES}
    return {
        "raw": raw,
        "scaled": scaled,
        "feature_names": list(FEATURE_NAMES),
        "scaler_stats": {f: {"mean": round(means[f], 6),
                             "std": round(stds[f], 6)} for f in FEATURE_NAMES},
    }


# ---------------------------------------------------------------------------
# Frozen benchmark model
# ---------------------------------------------------------------------------
def _frozen_coefs(rng) -> Dict[str, Any]:
    """A fixed, disclosed coefficient set (no tuning)."""
    return {
        "intercept": round(rng.uniform(0.3, 0.5), 6),
        "coef": {f: round(rng.uniform(-0.05, 0.05), 6) for f in FEATURE_NAMES},
        "feature_set": list(FEATURE_NAMES),
        "scaler_method": "log1p then z-score (full-cohort mean/std)",
    }


def _expected_association(scaled_row: Dict[str, float], coef: Dict[str, float],
                          intercept: float, feature_set: List[str]) -> float:
    """Frozen linear benchmark over scaled features; clipped to [0, 1]."""
    val = intercept + sum(coef[f] * scaled_row[f] for f in feature_set)
    return round(max(0.0, min(1.0, val)), 6)


def _modal_composition(rng, cid: str) -> Dict[str, float]:
    """Return 5 component shares summing to ~1.0 within tolerance."""
    weights = [rng.uniform(0.5, 5.0) for _ in range(5)]
    s = sum(weights)
    shares = [w / s for w in weights]
    out = {field: round(shares[i], 6) for i, field in enumerate(MODE_SHARE_FIELDS)}
    out["modal_total"] = round(sum(out[k] for k in MODE_SHARE_FIELDS), 6)
    return out


def _association(modes: Dict[str, float]) -> float:
    """Actual sustainable-mode association = rail + bus + ferry."""
    return round(modes["rail_share"] + modes["bus_share"] + modes["ferry_share"], 6)


def _direction(gap_signed: float) -> str:
    if gap_signed > 1e-9:
        return "Above expected"
    if gap_signed < -1e-9:
        return "Below expected"
    return "At expected"


# ---------------------------------------------------------------------------
# Default-focus and review threshold rules (derive from stored gaps)
# ---------------------------------------------------------------------------
def _percentile_90(values: List[float]) -> float:
    """90th-percentile using the documented nearest-rank interpolation.

    Index = round(q * (n - 1)) on the sorted series. This is the rule the
    README and validator describe, so the stored threshold recomputes exactly.
    """
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
    """Closest city to the median absolute gap; ties broken by city_id (ascending)."""
    med = _median(list(abs_gaps_by_cid.values()))
    return min(abs_gaps_by_cid,
               key=lambda cid: (round(abs(abs_gaps_by_cid[cid] - med), 9), cid))


def derive_review_set(abs_gaps_by_cid: Dict[str, float]) -> Tuple[str, set]:
    """Return (threshold, {city_id}) for cities at or above the 90th percentile."""
    threshold = _percentile_90(list(abs_gaps_by_cid.values()))
    review = {cid for cid, g in abs_gaps_by_cid.items() if g >= threshold - 1e-12}
    return threshold, review


# ---------------------------------------------------------------------------
# City model (one retained row per city)
# ---------------------------------------------------------------------------
def build_city_model(coef_pack: Dict[str, Any],
                     feat_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """One retained row per city: actual, expected, gap, modal, predictors.

    The modal composition is generated once and retained. All derived fields
    (actual, expected, gap, default-focus, review flag) are computed from the
    retained row, so the published rows and the analytical flags share the
    same data.
    """
    rng = make_rng(PROJECT_ID, "city_model")
    cities = fictional_cities(N_CITIES, project_id=PROJECT_ID)
    scaled = feat_data["scaled"]
    raw = feat_data["raw"]
    coef = coef_pack["coef"]
    intercept = coef_pack["intercept"]
    feature_set = coef_pack["feature_set"]

    retained: List[Dict[str, Any]] = []
    for c in cities:
        cid = c["city_id"]
        modes = _modal_composition(rng, cid)  # generated once, retained
        actual = _association(modes)
        expected = _expected_association(scaled[cid], coef, intercept, feature_set)
        gap_signed = round(actual - expected, 6)
        gap_abs = round(abs(gap_signed), 6)
        retained.append({
            "city": c,
            "modes": modes,
            "actual": actual,
            "expected": expected,
            "gap_signed": gap_signed,
            "gap_abs": gap_abs,
        })

    # Derive default + review from the retained gaps.
    abs_gaps = {r["city"]["city_id"]: r["gap_abs"] for r in retained}
    default_focus = derive_default_focus(abs_gaps)
    threshold, review_set = derive_review_set(abs_gaps)

    rows: List[Dict[str, Any]] = []
    for r in retained:
        c = r["city"]
        cid = c["city_id"]
        gap_signed = r["gap_signed"]
        gap_abs = r["gap_abs"]
        review_flag = "review case" if cid in review_set else "context"
        rows.append({
            "city_key": cid,
            "city_id": cid,
            "city_name": c["city_name"],
            "country_code": c["country_code"],
            "country_name": c["country_name"],
            "subregion": c["subregion"],
            "income_band": c["income_tier"],
            "cohort_label": "primary",
            "cohort_n": N_CITIES,
            "actual_association": r["actual"],
            "expected_association": r["expected"],
            "gap_signed": gap_signed,
            "gap_absolute": gap_abs,
            "gap_direction": _direction(gap_signed),
            "review_flag": review_flag,
            "is_default_focus": cid == default_focus,
            **r["modes"],
            **{f"raw_{k}": v for k, v in raw[cid].items()},
            **{f"scaled_{k}": v for k, v in scaled[cid].items()},
            "geographic_caveat_flag": _small_group(c, cities),
            "safe_copy": "SYNTHETIC DATA / INDEPENDENT PORTFOLIO PROJECT",
        })
    return rows


def _small_group(city, cities) -> bool:
    sub_n = sum(1 for c in cities if c["subregion"] == city["subregion"])
    return sub_n <= SMALL_GROUP_MAX


# ---------------------------------------------------------------------------
# Receipt
# ---------------------------------------------------------------------------
def build_model_receipt(coef_pack, feat_data, default_focus_cid,
                        review_threshold, n_review) -> Dict[str, Any]:
    return {
        "model_label": MODEL_LABEL,
        "fitted_on_count": N_CITIES,
        "scaler_method": coef_pack["scaler_method"],
        "feature_set": coef_pack["feature_set"],
        "penalty_policy": "fixed coefficients, no fitting or tuning",
        "generated_at_label": SNAPSHOT_ID,
        "default_focus_rule": "closest city to median absolute gap; ties by city_id ascending",
        "review_rule": "review case if gap_absolute >= 90th percentile of cohort absolute gaps",
        "review_percentile": 0.90,
        "default_focus_city": default_focus_cid,
        "review_threshold_absolute_gap": round(review_threshold, 6),
        "review_case_count": n_review,
        "composition_gate": f"modal_total in [{MODAL_TOLERANCE[0]}, {MODAL_TOLERANCE[1]}]",
        "target_field_denylist": "gap_signed, gap_absolute, actual_association, expected_association, modal_total, raw_*, scaled_*",
        "publication_state": "LOCAL PROTOTYPE / NOT PUBLISHED",
        "scaler_stats": feat_data["scaler_stats"],
        "intercept": coef_pack["intercept"],
        "coef": coef_pack["coef"],
        "robustness_jaccard_threshold": ROBUSTNESS_THRESHOLD,
        "robustness_method": (
            "fixed-coefficient sensitivity analysis: each variant alters an "
            "executed method parameter (feature drop, coefficient scaling, or "
            "intercept shift), recomputes expected/gap/review from the stored "
            "cohort, and reports Jaccard, MAE, RMSE, and bias from data; no "
            "random draws, no hold-out, no cross-validation")
    }


# ---------------------------------------------------------------------------
# Robustness: deterministic sensitivity variants
# ---------------------------------------------------------------------------
def _predict_all(feat_data: Dict[str, Any], coef: Dict[str, float],
                 intercept: float, feature_set: List[str],
                 ) -> Dict[str, float]:
    """Recompute expected association for every city under a given model spec."""
    scaled = feat_data["scaled"]
    return {cid: _expected_association(scaled[cid], coef, intercept, feature_set)
            for cid in scaled}


def _residual_metrics(actual_by_cid: Dict[str, float],
                      expected_by_cid: Dict[str, float]) -> Tuple[float, float, float]:
    """Return (MAE, RMSE, bias) of (actual - expected) residuals from data."""
    cids = sorted(actual_by_cid)
    resid = [actual_by_cid[c] - expected_by_cid[c] for c in cids]
    n = len(resid)
    mae = sum(abs(r) for r in resid) / n
    rmse = math.sqrt(sum(r * r for r in resid) / n)
    bias = sum(resid) / n
    return round(mae, 6), round(rmse, 6), round(bias, 6)


def _review_set_from_expected(actual_by_cid: Dict[str, float],
                              expected_by_cid: Dict[str, float],
                              baseline_threshold: float
                              ) -> set:
    """Review set under a variant: cities whose |gap| is at/above the same
    absolute-gap threshold used by the baseline. Reusing the baseline
    threshold keeps the comparison an apples-to-apples review-set stability
    check rather than a moving-target one.
    """
    return {cid for cid in actual_by_cid
            if abs(actual_by_cid[cid] - expected_by_cid[cid]) >= baseline_threshold - 1e-12}


def _variant_specs(coef_pack: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Eight deterministic sensitivity specs; each alters an executed parameter.

    Variant set:
      1 baseline
      2 drop_income       feature_drop = income_index
      3 drop_density      feature_drop = density
      4 drop_fleet        feature_drop = fleet_size
      5 drop_frequency    feature_drop = frequency_index
      6 half_coefs        coef_scale = 0.5
      7 double_coefs      coef_scale = 2.0
      8 intercept_up      intercept_shift = +0.05

    Each spec records enough metadata to reproduce the row.
    """
    base = {
        "intercept": coef_pack["intercept"],
        "coef": dict(coef_pack["coef"]),
        "feature_set": list(coef_pack["feature_set"]),
    }
    specs: List[Dict[str, Any]] = [{
        "variant_label": "baseline",
        "method": "frozen benchmark (no perturbation)",
        **base,
        "feature_drop": None,
        "coef_scale": 1.0,
        "intercept_shift": 0.0,
    }]
    for drop in ["income_index", "density", "fleet_size", "frequency_index"]:
        specs.append({
            "variant_label": f"drop_{drop}",
            "method": f"set {drop} coefficient to 0.0; intercept unchanged",
            "intercept": base["intercept"],
            "coef": {k: (0.0 if k == drop else v) for k, v in base["coef"].items()},
            "feature_set": list(base["feature_set"]),
            "feature_drop": drop,
            "coef_scale": 1.0,
            "intercept_shift": 0.0,
        })
    for label, scale in [("half_coefs", 0.5), ("double_coefs", 2.0)]:
        specs.append({
            "variant_label": label,
            "method": f"scale all coefficients by {scale}",
            "intercept": base["intercept"],
            "coef": {k: round(v * scale, 6) for k, v in base["coef"].items()},
            "feature_set": list(base["feature_set"]),
            "feature_drop": None,
            "coef_scale": scale,
            "intercept_shift": 0.0,
        })
    specs.append({
        "variant_label": "intercept_up",
        "method": "raise intercept by +0.05",
        "intercept": round(base["intercept"] + 0.05, 6),
        "coef": dict(base["coef"]),
        "feature_set": list(base["feature_set"]),
        "feature_drop": None,
        "coef_scale": 1.0,
        "intercept_shift": 0.05,
    })
    return specs


def build_robustness(city_model: List[Dict[str, Any]],
                     coef_pack: Dict[str, Any],
                     feat_data: Dict[str, Any],
                     baseline_threshold: float
                     ) -> Dict[str, Any]:
    """Deterministic sensitivity analysis over the stored cohort.

    Each variant recomputes expected values and the review set, then derives
    Jaccard vs the baseline review set plus residual MAE/RMSE/bias from data.
    No metric is a random draw; no fold or hold-out is simulated.
    """
    actual_by_cid = {r["city_id"]: float(r["actual_association"]) for r in city_model}
    baseline_review = {r["city_id"] for r in city_model
                       if r["review_flag"] == "review case"}

    specs = _variant_specs(coef_pack)
    groups_rows: List[Dict[str, Any]] = []
    jaccards: List[float] = []
    stable = 0
    unstable = 0
    for spec in specs:
        expected_by_cid = _predict_all(
            feat_data, spec["coef"], spec["intercept"], spec["feature_set"])
        variant_review = _review_set_from_expected(
            actual_by_cid, expected_by_cid, baseline_threshold)
        mae, rmse, bias = _residual_metrics(actual_by_cid, expected_by_cid)
        j = _jaccard(baseline_review, variant_review)
        jaccards.append(j)
        stable_flag = "stable" if j >= ROBUSTNESS_THRESHOLD else "unstable"
        if stable_flag == "stable":
            stable += 1
        else:
            unstable += 1
        groups_rows.append({
            "group_id": f"{spec['variant_label']}::all",
            "variant_label": spec["variant_label"],
            "method": spec["method"],
            "group_name": "all_cities",
            "n_cities": N_CITIES,
            "feature_drop": spec["feature_drop"] if spec["feature_drop"] is not None else "",
            "coef_scale": spec["coef_scale"],
            "intercept_shift": spec["intercept_shift"],
            "review_set_size": len(variant_review),
            "baseline_review_set_size": len(baseline_review),
            "stability_flag": stable_flag,
            "jaccard_threshold": ROBUSTNESS_THRESHOLD,
            "jaccard_value": round(j, 6),
            "mae": mae,
            "rmse": rmse,
            "bias": bias,
        })

    stable_jacs = [j for j in jaccards if j >= ROBUSTNESS_THRESHOLD]
    unstable_jacs = [j for j in jaccards if j < ROBUSTNESS_THRESHOLD]
    n_variants = len(specs)
    summary_rows = [{
        "bucket_label": "stable",
        "count": stable,
        "pct_of_total": round(stable / n_variants, 6),
        "min_jaccard": round(min(stable_jacs), 6) if stable_jacs else 0.0,
    }, {
        "bucket_label": "unstable",
        "count": unstable,
        "pct_of_total": round(unstable / n_variants, 6),
        "min_jaccard": round(min(unstable_jacs), 6) if unstable_jacs else 0.0,
    }]
    return {
        "robustness_group": groups_rows,
        "robustness_summary": summary_rows,
        "variant_specs": specs,  # retained for full reproducibility
    }


def _jaccard(a: set, b: set) -> float:
    if not a and not b:
        return 1.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


# ---------------------------------------------------------------------------
# Geographic warnings (retain small-n context caveat)
# ---------------------------------------------------------------------------
def build_geographic_warnings(city_model: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows = []
    for r in city_model:
        if r["geographic_caveat_flag"]:
            rows.append({
                "city_id": r["city_id"],
                "city_name": r["city_name"],
                "subregion": r["subregion"],
                "income_band": r["income_band"],
                "warning": "small-n context group; interpret with caution",
                "context_group_size": sum(
                    1 for c in fictional_cities(N_CITIES, project_id=PROJECT_ID)
                    if c["subregion"] == r["subregion"]),
            })
    return rows


# ---------------------------------------------------------------------------
# Single-root presentation table
# ---------------------------------------------------------------------------
# Exactly one score row, four diagnostic rows, five modal rows, and seven
# predictor rows per city => (1 + 4 + 5 + 7) * 24 = 408 rows.
PRESENTATION_DIAG_MEASURES = ["actual_association", "expected_association",
                              "gap_absolute", "gap_signed"]


def build_presentation(city_model: List[Dict[str, Any]],
                       receipt: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Single-root pre-joined presentation table for the dashboard."""
    rows: List[Dict[str, Any]] = []
    for r in city_model:
        cid = r["city_id"]
        base = {
            "city_id": cid,
            "city_name": r["city_name"],
            "country_code": r["country_code"],
            "country_name": r["country_name"],
            "model_label": receipt["model_label"],
            "snapshot_id": SNAPSHOT_ID,
            "actual_association": r["actual_association"],
            "expected_association": r["expected_association"],
            "gap_signed": r["gap_signed"],
            "gap_absolute": r["gap_absolute"],
            "gap_direction": r["gap_direction"],
            "review_flag": r["review_flag"],
            "is_default_focus": r["is_default_focus"],
            "measure_value": "",
            "equality_reference_value": r["expected_association"],
            "equality_axis_min": 0.0,
            "equality_axis_max": 1.0,
            "empty_state_copy": "",
            "scaled_value": "",
            "transform_label": "",
        }
        # 1 score row (one per city = 24 distinct scatter marks)
        rows.append({
            **base,
            "presentation_row_key": f"{cid}::score",
            "panel": "score",
            "measure_name": "actual_association",
            "measure_value": r["actual_association"],
        })
        # 4 diagnostic rows
        for meas in PRESENTATION_DIAG_MEASURES:
            rows.append({
                **base,
                "presentation_row_key": f"{cid}::diag::{meas}",
                "panel": "diagnostic",
                "measure_name": meas,
                "measure_value": r[meas],
            })
        # 5 modal rows
        for meas in MODE_SHARE_FIELDS:
            rows.append({
                **base,
                "presentation_row_key": f"{cid}::modal::{meas}",
                "panel": "modal",
                "measure_name": meas,
                "measure_value": r[meas],
            })
        # 7 predictor rows
        for f in receipt["feature_set"]:
            rows.append({
                **base,
                "presentation_row_key": f"{cid}::pred::{f}",
                "panel": "predictor",
                "measure_name": f,
                "measure_value": r[f"raw_{f}"],
                "scaled_value": r[f"scaled_{f}"],
                "transform_label": "log1p then z-score",
            })
    return rows


CITY_MODEL_COLS = [
    "city_key", "city_id", "city_name", "country_code", "country_name",
    "subregion", "income_band", "cohort_label", "cohort_n",
    "actual_association", "expected_association", "gap_signed", "gap_absolute",
    "gap_direction", "review_flag", "is_default_focus",
    "rail_share", "bus_share", "ferry_share", "informal_share", "other_share",
    "modal_total",
    "raw_population", "raw_density", "raw_income_index", "raw_fleet_size",
    "raw_network_length", "raw_frequency_index", "raw_fare_index",
    "scaled_population", "scaled_density", "scaled_income_index", "scaled_fleet_size",
    "scaled_network_length", "scaled_frequency_index", "scaled_fare_index",
    "geographic_caveat_flag", "safe_copy",
]
PRESENTATION_COLS = [
    "presentation_row_key", "panel", "model_label", "snapshot_id",
    "city_id", "city_name", "country_code", "country_name",
    "actual_association", "expected_association", "gap_signed", "gap_absolute",
    "gap_direction", "review_flag", "is_default_focus",
    "measure_name", "measure_value", "equality_reference_value",
    "equality_axis_min", "equality_axis_max", "empty_state_copy",
    "scaled_value", "transform_label",
]
ROBUSTNESS_GROUP_COLS = [
    "group_id", "variant_label", "method", "group_name", "n_cities",
    "feature_drop", "coef_scale", "intercept_shift",
    "review_set_size", "baseline_review_set_size",
    "stability_flag", "jaccard_threshold", "jaccard_value",
    "mae", "rmse", "bias",
]
ROBUSTNESS_SUMMARY_COLS = [
    "bucket_label", "count", "pct_of_total", "min_jaccard",
]
GEO_WARN_COLS = [
    "city_id", "city_name", "subregion", "income_band", "warning",
    "context_group_size",
]


def generate(out_dir: Path = OUT) -> Dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    # Frozen coefficients from a single dedicated RNG stream (stable across runs).
    coef_rng = make_rng(PROJECT_ID, "frozen_coef")
    coef_pack = _frozen_coefs(coef_rng)
    # Features from a separate stream so changing the model does not perturb
    # the feature table.
    feat_rng = make_rng(PROJECT_ID, "features")
    feat_data = _scaled_features(feat_rng)
    city_model = build_city_model(coef_pack, feat_data)

    # Derive and publish default + review from the stored rows.
    abs_gaps = {r["city_id"]: float(r["gap_absolute"]) for r in city_model}
    default_focus = derive_default_focus(abs_gaps)
    threshold, review_set = derive_review_set(abs_gaps)
    receipt = build_model_receipt(coef_pack, feat_data, default_focus,
                                  threshold, len(review_set))
    robustness = build_robustness(city_model, coef_pack, feat_data, threshold)
    geo_warns = build_geographic_warnings(city_model)
    presentation = build_presentation(city_model, receipt)

    write_csv(out_dir / "city_model.csv", city_model, CITY_MODEL_COLS)
    write_csv(out_dir / "dashboard_presentation.csv", presentation, PRESENTATION_COLS)
    write_csv(out_dir / "robustness_group.csv", robustness["robustness_group"],
              ROBUSTNESS_GROUP_COLS)
    write_csv(out_dir / "robustness_summary.csv", robustness["robustness_summary"],
              ROBUSTNESS_SUMMARY_COLS)
    write_csv(out_dir / "geographic_warnings.csv", geo_warns, GEO_WARN_COLS)
    write_json(out_dir / "model_receipt.json", receipt)

    return {
        "project_id": PROJECT_ID,
        "snapshot_id": SNAPSHOT_ID,
        "model_label": MODEL_LABEL,
        "row_counts": {
            "city_model": len(city_model),
            "dashboard_presentation": len(presentation),
            "robustness_group": len(robustness["robustness_group"]),
            "robustness_summary": len(robustness["robustness_summary"]),
            "geographic_warnings": len(geo_warns),
        },
        "default_focus_city": default_focus,
        "review_threshold_absolute_gap": round(threshold, 6),
        "review_case_count": len(review_set),
        "jaccard_threshold": ROBUSTNESS_THRESHOLD,
        "composition_tolerance": list(MODAL_TOLERANCE),
    }


if __name__ == "__main__":
    r = generate()
    print(f"[{PROJECT_ID}] snapshot={r['snapshot_id']} model={r['model_label']}")
    for k, v in r["row_counts"].items():
        print(f"  {k}: {v} rows")
    print(f"  default_focus_city: {r['default_focus_city']}")
    print(f"  review_threshold_absolute_gap: {r['review_threshold_absolute_gap']}")
    print(f"  review_case_count: {r['review_case_count']}")
