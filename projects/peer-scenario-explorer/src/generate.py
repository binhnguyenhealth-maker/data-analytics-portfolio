"""Project 3: Explainable Urban Peer Scenario Stability Explorer - synthetic generator.

Generic pattern: for a focal fictional city and a scenario, find the 5 most
structurally similar peer cities, explain the per-feature contribution to the
distance, compare the closest set vs the diversified (two-per-country) set,
and show a context measure AFTER peer membership is frozen. Includes 17
stability variants and an explicit non-map fallback (no coordinate fields).

SYNTHETIC DATA / INDEPENDENT PORTFOLIO PROJECT
"""
from __future__ import annotations

import math
import statistics
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from shared.synthetic import (  # noqa: E402
    SCENARIO_ENUM, SURFACE_ENUM, derive_seed, fictional_cities, make_rng,
    snapshot_id, write_csv, write_json,
)

PROJECT_ID = "peer-scenario-explorer"
SNAPSHOT_ID = snapshot_id(PROJECT_ID)
METHOD_VERSION = "syn-pse-method-002"  # bumped: variant methods now execute their labels

N_CITIES = 30
K_PEERS = 5
COUNTRY_CAP = 2          # diversified scenario
STABILITY_THRESHOLD = 0.5  # min Jaccard to be 'stable'
N_VARIANTS = 17
SUBREGION_PENALTY_FRAC = 0.5
INCOME_PENALTY_FRAC = 0.25
# Strong-geography variant uses larger soft-penalty fractions than the baseline
# regime. Fractions are documented here so the method signature is auditable.
STRONG_SUBREGION_PENALTY_FRAC = 1.0
STRONG_INCOME_PENALTY_FRAC = 0.75

SUPPORTED_METRICS = ("euclidean", "manhattan")

STRUCTURAL_FEATURES = ["population", "population_density", "block_density", "built_up_area"]
CONTEXT_FEATURE = "public_transport_access_share"

OUT = ROOT / "projects" / "peer-scenario-explorer" / "data" / "synthetic"


# ---- City roster with structural + context features ---------------------

def build_roster() -> Tuple[List[Dict[str, Any]], Dict[str, Dict[str, float]]]:
    """Build the fictional city roster with raw structural features and a
    post-hoc context measure. NO coordinate columns (non-map fallback)."""
    rng = make_rng(PROJECT_ID, "roster_features")
    cities = fictional_cities(N_CITIES, project_id=PROJECT_ID)
    roster: List[Dict[str, Any]] = []
    raw: Dict[str, Dict[str, float]] = {}
    membership = ["core_eligible"] * 20 + ["extended_eligible"] * 10
    for i, c in enumerate(cities):
        cid = c["city_id"]
        feats = {
            "population": float(rng.randint(200_000, 8_000_000)),
            "population_density": float(rng.randint(500, 12_000)),
            "block_density": round(rng.uniform(5.0, 80.0), 4),
            "built_up_area": round(rng.uniform(20.0, 600.0), 4),
        }
        raw[cid] = feats
        roster.append({
            "city_id": cid,
            "city_name": c["city_name"],
            "country_code": c["country_code"],
            "country_name": c["country_name"],
            "subregion": c["subregion"],
            "income_tier": c["income_tier"],
            "membership_state": membership[i % len(membership)],
            "exclusion_reason": "",
            # Non-map fallback: no latitude/longitude columns exist.
            "public_transport_access_share": round(rng.uniform(0.1, 0.9), 6),
        })
    return roster, raw


def _transformed(raw: Dict[str, Dict[str, float]]) -> Dict[str, Dict[str, float]]:
    """log1p transform of raw structural features."""
    return {cid: {f: math.log1p(v) for f, v in feats.items()}
            for cid, feats in raw.items()}


def _scaled_for(transformed, feature_subset, regime="robust") -> Dict[str, Dict[str, float]]:
    """Scale features. regime: 'robust' (median/IQR) or 'zscore' (mean/std).

    Scaling is computed over the same set of cities that will be used in
    fitting; no held-out fold is leaked because this is a synthetic demo.
    """
    cids = sorted(transformed)
    scaled = {cid: {} for cid in cids}
    for f in feature_subset:
        vals = [transformed[c][f] for c in cids]
        if regime == "robust":
            med = statistics.median(vals)
            sv = sorted(vals)
            q1 = sv[len(sv) // 4]
            q3 = sv[3 * len(sv) // 4]
            denom = (q3 - q1) or 1.0
            for c in cids:
                scaled[c][f] = round((transformed[c][f] - med) / denom, 6)
        else:  # zscore
            m = sum(vals) / len(vals)
            var = sum((v - m) ** 2 for v in vals) / len(vals)
            sd = math.sqrt(var) or 1.0
            for c in cids:
                scaled[c][f] = round((transformed[c][f] - m) / sd, 6)
    return scaled


# ---- Distance computation and peer selection -----------------------------

def _resolve_weights(weights, features) -> Dict[str, float]:
    """Normalize a weights spec into a complete {feature: weight} map.

    Missing features default to 1.0. Non-positive weights are rejected so the
    weighted distance decomposition always reconciles.
    """
    w = {f: 1.0 for f in features}
    if weights:
        for f, val in weights.items():
            if f not in features:
                raise ValueError(f"weight for unknown feature {f!r}")
            w[f] = float(val)
    for f in features:
        if w[f] <= 0:
            raise ValueError(f"non-positive weight for feature {f!r}: {w[f]}")
    return w


def _pairwise_distance(scaled_a, scaled_b, features, weights=None,
                       metric: str = "euclidean") -> float:
    """Weighted structural distance under the named metric.

    - euclidean: sqrt(sum(w_f * delta_f^2))
    - manhattan: sum(w_f * |delta_f|)

    The self-distance for identical scaled vectors is exactly 0.0 (callers
    additionally guard the focal diagonal with +inf ordering so a city can
    never select itself).
    """
    w = _resolve_weights(weights, features)
    if metric == "euclidean":
        return math.sqrt(sum(w[f] * (scaled_a[f] - scaled_b[f]) ** 2
                             for f in features))
    if metric == "manhattan":
        return sum(w[f] * abs(scaled_a[f] - scaled_b[f]) for f in features)
    raise ValueError(f"unsupported metric: {metric!r}")


def _typical_kth_distance(scaled, cids, features, weights=None,
                          metric: str = "euclidean") -> float:
    """Median across cities of each city's K-th-nearest structural distance.

    The kth anchor is computed with the SAME metric/weights as the selector so
    the soft-penalty fraction is interpretable as 'fraction of typical kth
    distance under the variant's own distance regime'.
    """
    kth_list = []
    for a in cids:
        ds = sorted(_pairwise_distance(scaled[a], scaled[b], features, weights, metric)
                    for b in cids if b != a)
        if len(ds) >= K_PEERS:
            kth_list.append(ds[K_PEERS - 1])
    return statistics.median(kth_list) if kth_list else 1.0


def _final_distance(structural_d, focal_city, peer_city, roster_by_id, kth,
                    subregion_frac: float, income_frac: float):
    """Structural distance plus named soft penalties.

    The penalty fractions are explicit parameters so each variant executes the
    fractions claimed by its label (no-geography => 0.0, strong-geography =>
    larger fractions).
    """
    sub_a = roster_by_id[focal_city]["subregion"]
    sub_b = roster_by_id[peer_city]["subregion"]
    inc_a = roster_by_id[focal_city]["income_tier"]
    inc_b = roster_by_id[peer_city]["income_tier"]
    sub_pen = subregion_frac * kth if sub_a != sub_b else 0.0
    inc_pen = income_frac * kth if inc_a != inc_b else 0.0
    return structural_d + sub_pen + inc_pen, sub_pen, inc_pen


def select_peers(focal, scaled, roster_by_id, cids, features,
                 scenario: str = "baseline", weights=None,
                 metric: str = "euclidean",
                 subregion_frac: float = SUBREGION_PENALTY_FRAC,
                 income_frac: float = INCOME_PENALTY_FRAC) -> List[Dict[str, Any]]:
    """Return exactly K_PEERS peers for the focal under the scenario policy.

    All method knobs (metric, weights, soft-penalty fractions, scenario
    policy) are explicit so the executed method matches the variant label.
    """
    kth = _typical_kth_distance(scaled, cids, features, weights, metric)
    candidates: List[Dict[str, Any]] = []
    for b in cids:
        if b == focal:
            continue
        struct_d = _pairwise_distance(scaled[focal], scaled[b], features,
                                      weights, metric)
        final_d, sub_pen, inc_pen = _final_distance(
            struct_d, focal, b, roster_by_id, kth, subregion_frac, income_frac)
        candidates.append({
            "peer_city_id": b,
            "structural_distance": struct_d,
            "subregion_penalty": sub_pen,
            "income_penalty": inc_pen,
            "final_distance": final_d,
            "peer_country_code": roster_by_id[b]["country_code"],
            "peer_subregion": roster_by_id[b]["subregion"],
            "peer_income_tier": roster_by_id[b]["income_tier"],
        })

    # Sort by policy
    candidates.sort(key=lambda x: (x["final_distance"], x["structural_distance"], x["peer_city_id"]))

    if scenario == "diversified":
        chosen: List[Dict[str, Any]] = []
        per_country: Dict[str, int] = {}
        for cand in candidates:
            cc = cand["peer_country_code"]
            if per_country.get(cc, 0) >= COUNTRY_CAP:
                continue
            chosen.append(cand)
            per_country[cc] = per_country.get(cc, 0) + 1
            if len(chosen) == K_PEERS:
                break
        # If diversified cannot reach K (small country count), top up from closest
        if len(chosen) < K_PEERS:
            chosen_ids = {c["peer_city_id"] for c in chosen}
            for cand in candidates:
                if cand["peer_city_id"] in chosen_ids:
                    continue
                chosen.append(cand)
                if len(chosen) == K_PEERS:
                    break
        candidates = chosen
    else:
        candidates = candidates[:K_PEERS]

    # Ensure exactly K (synthetic cohort is large enough)
    if len(candidates) != K_PEERS:
        raise RuntimeError(f"peer selection returned {len(candidates)} != {K_PEERS}")

    for rank, c in enumerate(candidates, start=1):
        c["peer_rank"] = rank
    return candidates


def explain_peer(focal, peer, scaled, features, structural_d,
                 weights=None, metric: str = "euclidean") -> List[Dict[str, Any]]:
    """Per-feature signed contribution to structural distance.

    The decomposition reconciles to the structural distance under both metrics
    because the component formula is the metric-specific addend that sums to
    the distance:

    - euclidean: component_f = w_f * delta_f^2 / structural_d
      (sum of components == structural_d, since d = sqrt(sum(w*delta^2))).
    - manhattan: component_f = w_f * |delta_f|
      (sum of components == structural_d, since d = sum(w*|delta|)).

    The signed_component keeps the sign of delta so the reader can see which
    features pull the peer above vs below the focal.

    Reconciliation is exact when structural_d > 0; for the degenerate 0 case
    (impossible here because the focal diagonal is excluded) components are 0.
    """
    w = _resolve_weights(weights, features)
    rows = []
    for f in features:
        delta = scaled[peer][f] - scaled[focal][f]
        if metric == "euclidean":
            comp = (w[f] * delta ** 2) / structural_d if structural_d > 0 else 0.0
        elif metric == "manhattan":
            comp = w[f] * abs(delta)
        else:
            raise ValueError(f"unsupported metric: {metric!r}")
        signed = math.copysign(comp, delta) if delta != 0 else comp
        rows.append({
            "scaled_feature_name": f,
            "scaled_feature_value_focal": scaled[focal][f],
            "scaled_feature_value_peer": scaled[peer][f],
            "peer_minus_focal_scaled": round(delta, 6),
            "distance_component": round(comp, 6),
            "signed_component": round(signed, 6),
        })
    return rows


def build_baseline_peers(roster, raw) -> Dict[str, Any]:
    """Compute the baseline (closest-K) peer sets for every focal city."""
    transformed = _transformed(raw)
    baseline_scaled = _scaled_for(transformed, STRUCTURAL_FEATURES, "robust")
    roster_by_id = {r["city_id"]: r for r in roster}
    cids = [r["city_id"] for r in roster]
    peer_result: List[Dict[str, Any]] = []
    peer_explanation: List[Dict[str, Any]] = []
    peer_sets: Dict[str, List[str]] = {}
    for focal in cids:
        peers = select_peers(focal, baseline_scaled, roster_by_id, cids,
                             STRUCTURAL_FEATURES, scenario="baseline")
        peer_sets[focal] = [p["peer_city_id"] for p in peers]
        for p in peers:
            row = {
                "focal_city_id": focal,
                "scenario_label": "baseline",
                "peer_rank": p["peer_rank"],
                "peer_city_id": p["peer_city_id"],
                "structural_distance": round(p["structural_distance"], 6),
                "subregion_penalty": round(p["subregion_penalty"], 6),
                "income_penalty": round(p["income_penalty"], 6),
                "final_distance": round(p["final_distance"], 6),
                "peer_country_code": p["peer_country_code"],
                "same_country_peer_count": sum(
                    1 for q in peers
                    if roster_by_id[q["peer_city_id"]]["country_code"] == p["peer_country_code"]),
            }
            peer_result.append(row)
            for expl in explain_peer(focal, p["peer_city_id"], baseline_scaled,
                                      STRUCTURAL_FEATURES, p["structural_distance"]):
                peer_explanation.append({**row, **expl,
                                          "peer_key": f"{focal}::baseline::{p['peer_city_id']}"})
    # Also compute 'core' scenario (3-feature core universe) and 'diversified'
    core_features = STRUCTURAL_FEATURES[:3]
    core_scaled = _scaled_for(transformed, core_features, "robust")
    for focal in cids:
        peers = select_peers(focal, core_scaled, roster_by_id, cids,
                             core_features, scenario="core")
        for p in peers:
            row = {
                "focal_city_id": focal,
                "scenario_label": "core",
                "peer_rank": p["peer_rank"],
                "peer_city_id": p["peer_city_id"],
                "structural_distance": round(p["structural_distance"], 6),
                "subregion_penalty": round(p["subregion_penalty"], 6),
                "income_penalty": round(p["income_penalty"], 6),
                "final_distance": round(p["final_distance"], 6),
                "peer_country_code": p["peer_country_code"],
                "same_country_peer_count": 0,
            }
            peer_result.append(row)
            for expl in explain_peer(focal, p["peer_city_id"], core_scaled,
                                      core_features, p["structural_distance"]):
                peer_explanation.append({**row, **expl,
                                          "peer_key": f"{focal}::core::{p['peer_city_id']}"})
    # Diversified
    for focal in cids:
        peers = select_peers(focal, baseline_scaled, roster_by_id, cids,
                             STRUCTURAL_FEATURES, scenario="diversified")
        for p in peers:
            row = {
                "focal_city_id": focal,
                "scenario_label": "diversified",
                "peer_rank": p["peer_rank"],
                "peer_city_id": p["peer_city_id"],
                "structural_distance": round(p["structural_distance"], 6),
                "subregion_penalty": round(p["subregion_penalty"], 6),
                "income_penalty": round(p["income_penalty"], 6),
                "final_distance": round(p["final_distance"], 6),
                "peer_country_code": p["peer_country_code"],
                "same_country_peer_count": sum(
                    1 for q in peers
                    if roster_by_id[q["peer_city_id"]]["country_code"] == p["peer_country_code"]),
            }
            peer_result.append(row)
            for expl in explain_peer(focal, p["peer_city_id"], baseline_scaled,
                                      STRUCTURAL_FEATURES, p["structural_distance"]):
                peer_explanation.append({**row, **expl,
                                          "peer_key": f"{focal}::diversified::{p['peer_city_id']}"})
    return {
        "peer_result": peer_result,
        "peer_explanation": peer_explanation,
        "baseline_peer_sets": peer_sets,
        "baseline_scaled": baseline_scaled,
        "transformed": transformed,
    }


def _method_signature(vdef: Dict[str, Any]) -> str:
    """Build a stable, human-auditable signature for a variant's executed method.

    The signature is the canonical read-back of the selector configuration so
    a reviewer can confirm the label matches the executed method WITHOUT having
    to read the generator source. Every field that changes the peer set under
    this synthetic cohort is included.
    """
    features = list(vdef["features"])
    regime = vdef["regime"]
    metric = vdef.get("metric", "euclidean")
    weights = vdef.get("weights")
    scenario = vdef.get("scenario", "baseline")
    sub_frac = vdef.get("subregion_penalty_fraction", SUBREGION_PENALTY_FRAC)
    inc_frac = vdef.get("income_penalty_fraction", INCOME_PENALTY_FRAC)
    if weights:
        # Canonical ordering by STRUCTURAL_FEATURES so the signature is stable
        w_repr = ",".join(f"{f}={float(weights.get(f, 1.0))}"
                          for f in STRUCTURAL_FEATURES if f in features)
    else:
        w_repr = "uniform"
    return (
        f"features=[{','.join(features)}]"
        f"|scaler={regime}"
        f"|metric={metric}"
        f"|weights={w_repr}"
        f"|scenario={scenario}"
        f"|subregion_frac={float(sub_frac)}"
        f"|income_frac={float(inc_frac)}"
    )


def build_stability(roster, raw, baseline_info) -> Dict[str, Any]:
    """17 sensitivity variants. For each focal x variant, execute the variant's
    declared method, then compute Jaccard similarity vs the baseline peer set.

    Every variant's metric, weights, scaler, scenario policy, and soft-penalty
    fractions are threaded through the actual selector so the peer set is a
    function of the declared method, not of the label.
    """
    transformed = baseline_info["transformed"]
    baseline_sets = baseline_info["baseline_peer_sets"]
    roster_by_id = {r["city_id"]: r for r in roster}
    cids = [r["city_id"] for r in roster]

    variant_defs = _variant_definitions()
    assert len(variant_defs) == N_VARIANTS, len(variant_defs)
    # Each variant must carry a unique executed-method signature.
    sigs = [_method_signature(v) for v in variant_defs]
    assert len(set(sigs)) == len(sigs), "duplicate method signatures across variants"

    # Per-variant scaled tables are memoized; the (features, regime) pair fully
    # determines the scaled table and is shared by several variants.
    scaled_cache: Dict[Tuple[Tuple[str, ...], str], Dict[str, Dict[str, float]]] = {}

    def _scaled_for_variant(features, regime):
        key = (tuple(features), regime)
        if key not in scaled_cache:
            scaled_cache[key] = _scaled_for(transformed, list(features), regime)
        return scaled_cache[key]

    stability: List[Dict[str, Any]] = []
    stability_summary: List[Dict[str, Any]] = []
    alt_for_default: Dict[str, List[str]] = {}
    variant_meta: List[Dict[str, Any]] = []
    for focal in cids:
        alt_for_default[focal] = []

    for vdef in variant_defs:
        v_label = vdef["label"]
        features = list(vdef["features"])
        regime = vdef["regime"]
        metric = vdef.get("metric", "euclidean")
        weights = vdef.get("weights")
        scenario = vdef.get("scenario", "baseline")
        sub_frac = vdef.get("subregion_penalty_fraction", SUBREGION_PENALTY_FRAC)
        inc_frac = vdef.get("income_penalty_fraction", INCOME_PENALTY_FRAC)
        sig = _method_signature(vdef)
        scaled = _scaled_for_variant(features, regime)
        # Record one metadata row per variant (focal-independent) once.
        if len(variant_meta) < len(variant_defs):
            variant_meta.append({
                "variant_label": v_label,
                "method_signature": sig,
                "scaler": regime,
                "metric": metric,
                "feature_set": "|".join(features),
                "weights_policy": (
                    "|".join(f"{f}={float(weights.get(f, 1.0))}" for f in features)
                    if weights else "uniform"
                ),
                "scenario_policy": scenario,
                "subregion_penalty_fraction": float(sub_frac),
                "income_penalty_fraction": float(inc_frac),
            })

    for focal in cids:
        js: List[float] = []
        for vdef in variant_defs:
            v_label = vdef["label"]
            features = list(vdef["features"])
            regime = vdef["regime"]
            metric = vdef.get("metric", "euclidean")
            weights = vdef.get("weights")
            scenario = vdef.get("scenario", "baseline")
            sub_frac = vdef.get("subregion_penalty_fraction", SUBREGION_PENALTY_FRAC)
            inc_frac = vdef.get("income_penalty_fraction", INCOME_PENALTY_FRAC)
            sig = _method_signature(vdef)
            scaled = _scaled_for_variant(features, regime)
            peers = select_peers(focal, scaled, roster_by_id, cids, features,
                                 scenario=scenario, weights=weights, metric=metric,
                                 subregion_frac=sub_frac, income_frac=inc_frac)
            vset = [p["peer_city_id"] for p in peers]
            j = _jaccard(set(baseline_sets[focal]), set(vset))
            js.append(j)
            below = j < STABILITY_THRESHOLD
            stability.append({
                "focal_city_id": focal,
                "variant_label": v_label,
                "method_signature": sig,
                "metric": metric,
                "scaler": regime,
                "feature_set": "|".join(features),
                "weights_policy": (
                    "|".join(f"{f}={float(weights.get(f, 1.0))}" for f in features)
                    if weights else "uniform"
                ),
                "scenario_policy": scenario,
                "subregion_penalty_fraction": float(sub_frac),
                "income_penalty_fraction": float(inc_frac),
                "jaccard_value": round(j, 6),
                "below_threshold": below,
                "threshold": STABILITY_THRESHOLD,
                "peer_set": "|".join(vset),
            })
            if v_label == "no_geography_penalty":
                alt_for_default[focal] = vset
        min_j = min(js)
        med_j = statistics.median(js)
        mean_j = sum(js) / len(js)
        badge = "stable under audited variants" if min_j >= STABILITY_THRESHOLD \
            else "unstable scenario - inspect alternatives"
        stability_summary.append({
            "focal_city_id": focal,
            "min_jaccard": round(min_j, 6),
            "median_jaccard": round(med_j, 6),
            "mean_jaccard": round(mean_j, 6),
            "stability_badge": badge,
            "threshold": STABILITY_THRESHOLD,
            "n_variants": N_VARIANTS,
        })
    return {
        "stability": stability,
        "stability_summary": stability_summary,
        "alt_peer_sets_no_geo": alt_for_default,
        "variant_metadata": variant_meta,
    }


def _variant_definitions() -> List[Dict[str, Any]]:
    """17 named variants. Each entry fully specifies the executed method:
    features, scaling regime, distance metric, feature weights, scenario
    policy, and the two soft-penalty fractions. No two variants share both a
    label and an executed-method signature.

    Variant roster:
      1. no_geography_penalty        - subregion & income penalties set to 0
      2. strong_geography_penalty    - stronger penalty fractions than baseline
      3. zscore_soft_penalty         - z-score scaling
      4. manhattan_soft_penalty      - weighted Manhattan distance (not Euclidean)
      5..12. double_<f>/half_<f>     - feature weight perturbations
      13..16. drop_<f>               - one-feature-dropped feature sets
      17. diversified_country_cap_2  - greedy two-per-country scenario policy
    """
    out: List[Dict[str, Any]] = []
    out.append({
        "label": "no_geography_penalty",
        "features": STRUCTURAL_FEATURES, "regime": "robust",
        "metric": "euclidean", "scenario": "baseline",
        "subregion_penalty_fraction": 0.0,
        "income_penalty_fraction": 0.0,
    })
    out.append({
        "label": "strong_geography_penalty",
        "features": STRUCTURAL_FEATURES, "regime": "robust",
        "metric": "euclidean", "scenario": "baseline",
        "subregion_penalty_fraction": STRONG_SUBREGION_PENALTY_FRAC,
        "income_penalty_fraction": STRONG_INCOME_PENALTY_FRAC,
    })
    out.append({
        "label": "zscore_soft_penalty",
        "features": STRUCTURAL_FEATURES, "regime": "zscore",
        "metric": "euclidean", "scenario": "baseline",
    })
    out.append({
        "label": "manhattan_soft_penalty",
        "features": STRUCTURAL_FEATURES, "regime": "robust",
        "metric": "manhattan", "scenario": "baseline",
    })
    for f in STRUCTURAL_FEATURES:
        out.append({"label": f"double_{f}", "features": STRUCTURAL_FEATURES,
                    "regime": "robust", "metric": "euclidean", "scenario": "baseline",
                    "weights": {x: (2.0 if x == f else 1.0) for x in STRUCTURAL_FEATURES}})
        out.append({"label": f"half_{f}", "features": STRUCTURAL_FEATURES,
                    "regime": "robust", "metric": "euclidean", "scenario": "baseline",
                    "weights": {x: (0.5 if x == f else 1.0) for x in STRUCTURAL_FEATURES}})
    for f in STRUCTURAL_FEATURES:
        rest = [x for x in STRUCTURAL_FEATURES if x != f]
        out.append({"label": f"drop_{f}", "features": rest, "regime": "robust",
                    "metric": "euclidean", "scenario": "baseline"})
    # 17th variant: distinct method. Replaces the former
    # core_universe_without_built_up_area, which executed the same selector as
    # drop_built_up_area. diversified_country_cap_2 uses the two-per-country
    # greedy scenario policy over the full 4-feature robust-scaled universe,
    # which is a demonstrably different method (selection policy change, not a
    # distance/scaler/weight change).
    out.append({
        "label": "diversified_country_cap_2",
        "features": STRUCTURAL_FEATURES, "regime": "robust",
        "metric": "euclidean", "scenario": "diversified",
    })
    return out


def _jaccard(a, b):
    if not a and not b:
        return 1.0
    return len(a & b) / len(a | b) if (a | b) else 0.0


def build_coverage_exposure(roster, baseline_info) -> List[Dict[str, Any]]:
    """Coverage/exposure by country, subregion, income_tier, plus an ALL row."""
    roster_by_id = {r["city_id"]: r for r in roster}
    baseline_sets = baseline_info["baseline_peer_sets"]
    rows: List[Dict[str, Any]] = []
    for dim in ["country_code", "subregion", "income_tier"]:
        groups: Dict[str, List[str]] = {}
        for r in roster:
            groups.setdefault(str(r[dim]), []).append(r["city_id"])
        for g, members in groups.items():
            slots = sum(1 for f, peers in baseline_sets.items()
                        for p in peers if roster_by_id[p][dim] == g)
            n = len(members)
            rows.append({
                "dimension": dim,
                "group_value": g,
                "n_cities": n,
                "inbound_peer_slots": slots,
                "slot_share": round(slots / max(1, sum(
                    1 for f, peers in baseline_sets.items() for p in peers)), 6),
                "representation_ratio": round(slots / max(1, n), 6),
            })
    total_slots = sum(len(p) for p in baseline_sets.values())
    rows.append({
        "dimension": "__ALL__",
        "group_value": "__ALL__",
        "n_cities": len(roster),
        "inbound_peer_slots": total_slots,
        "slot_share": 1.0,
        "representation_ratio": round(total_slots / max(1, len(roster)), 6),
    })
    return rows


def build_context_comparison(roster, baseline_info) -> List[Dict[str, Any]]:
    """One context row per (focal x scenario x member 0..K). Post-hoc only."""
    roster_by_id = {r["city_id"]: r for r in roster}
    rows: List[Dict[str, Any]] = []
    # gather peer sets per scenario from peer_result
    by_focal_sc: Dict[Tuple[str, str], List[str]] = {}
    for pr in baseline_info["peer_result"]:
        key = (pr["focal_city_id"], pr["scenario_label"])
        by_focal_sc.setdefault(key, []).append(pr["peer_city_id"])
    for (focal, sc), peers in by_focal_sc.items():
        # focal row (rank 0) + 5 peers
        for rank, cid in enumerate([focal] + sorted(peers), start=0):
            rows.append({
                "focal_city_id": focal,
                "scenario_label": sc,
                "member_rank": rank,
                "member_city_id": cid,
                "context_measure_name": CONTEXT_FEATURE,
                "context_measure_value": roster_by_id[cid]["public_transport_access_share"],
                "context_only_label": "post-hoc measure; never used in fitting",
                "context_missing": False,
            })
    return rows


def default_landing(roster, raw) -> str:
    """Deterministic landing focal: nearest city to multivariate median
    of transformed features under robust scaling. Ties by city_id."""
    transformed = _transformed(raw)
    scaled = _scaled_for(transformed, STRUCTURAL_FEATURES, "robust")
    # Multivariate median: per-feature medians
    feats = STRUCTURAL_FEATURES
    cids = [r["city_id"] for r in roster]
    medians = {f: statistics.median([scaled[c][f] for c in cids]) for f in feats}
    best = None
    best_d = float("inf")
    for c in cids:
        d = math.sqrt(sum((scaled[c][f] - medians[f]) ** 2 for f in feats))
        if d < best_d or (d == best_d and c < best):
            best_d = d
            best = c
    return best


def build_presentation(roster, baseline_info, stability_info, landing_id) -> List[Dict[str, Any]]:
    """Single-root presentation table with 4 surfaces."""
    roster_by_id = {r["city_id"]: r for r in roster}
    rows: List[Dict[str, Any]] = []
    alt_sets = stability_info["alt_peer_sets_no_geo"]
    baseline_sets = baseline_info["baseline_peer_sets"]
    stability_summary = {s["focal_city_id"]: s for s in stability_info["stability_summary"]}

    # Group peer_result by focal x scenario
    by_focal_sc: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for pr in baseline_info["peer_result"]:
        by_focal_sc.setdefault((pr["focal_city_id"], pr["scenario_label"]), []).append(pr)
    # explanations keyed by peer_key
    expl_by_key = {}
    for ex in baseline_info["peer_explanation"]:
        expl_by_key.setdefault(ex["peer_key"], []).append(ex)

    for focal, roster_focal in roster_by_id.items():
        summ = stability_summary[focal]
        for scenario in SCENARIO_ENUM:
            key = (focal, scenario)
            peer_rows = sorted(by_focal_sc.get(key, []), key=lambda p: p["peer_rank"])
            base = {
                "snapshot_id": SNAPSHOT_ID,
                "method_version": METHOD_VERSION,
                "focal_city_id": focal,
                "focal_city_name": roster_focal["city_name"],
                "focal_country_code": roster_focal["country_code"],
                "focal_country_name": roster_focal["country_name"],
                "focal_subregion": roster_focal["subregion"],
                "focal_income_tier": roster_focal["income_tier"],
                "scenario_label": scenario,
                "is_landing": focal == landing_id,
                "is_closest_set": scenario == "baseline",
                "is_diversified_set": scenario == "diversified",
                "stability_badge": summ["stability_badge"],
                "jaccard_stability_flag": summ["min_jaccard"] >= STABILITY_THRESHOLD,
                "alternative_peer_codes_no_geo": "|".join(alt_sets.get(focal, [])),
                "prototype_status": "LOCAL PROTOTYPE",
            }
            # Surface 1: peer_map_and_table - focal self-row + 5 peers
            rows.append({**base, "surface": SURFACE_ENUM[0],
                         "member_role": "focal", "peer_rank": 0,
                         "peer_city_id": focal,
                         "peer_city_name": roster_focal["city_name"],
                         "peer_country_name": roster_focal["country_name"],
                         "structural_distance": 0.0,
                         "final_distance": 0.0,
                         "safe_copy": f"{focal} focal city under {scenario}"})
            for p in peer_rows:
                pid = p["peer_city_id"]
                rows.append({**base, "surface": SURFACE_ENUM[0],
                             "member_role": "peer", "peer_rank": p["peer_rank"],
                             "peer_city_id": pid,
                             "peer_city_name": roster_by_id[pid]["city_name"],
                             "peer_country_name": roster_by_id[pid]["country_name"],
                             "structural_distance": p["structural_distance"],
                             "final_distance": p["final_distance"],
                             "same_country_peer_count": p["same_country_peer_count"],
                             "safe_copy": f"peer {p['peer_rank']} for {focal} ({scenario})"})
            # Surface 2: why_this_peer - per-feature decomposition
            for p in peer_rows:
                pkey = f"{focal}::{scenario}::{p['peer_city_id']}"
                for expl in expl_by_key.get(pkey, []):
                    rows.append({**base, "surface": SURFACE_ENUM[1],
                                 "member_role": "peer",
                                 "peer_rank": p["peer_rank"],
                                 "peer_city_id": p["peer_city_id"],
                                 "peer_city_name": roster_by_id[p["peer_city_id"]]["city_name"],
                                 "scaled_feature_name": expl["scaled_feature_name"],
                                 "scaled_feature_value_focal": expl["scaled_feature_value_focal"],
                                 "scaled_feature_value_peer": expl["scaled_feature_value_peer"],
                                 "peer_minus_focal_scaled": expl["peer_minus_focal_scaled"],
                                 "distance_component": expl["distance_component"],
                                 "signed_component": expl["signed_component"],
                                 "safe_copy": f"feature {expl['scaled_feature_name']} explains part of {focal}->{p['peer_city_id']}"})
            # Surface 3: closest_vs_diversified
            if scenario == "baseline":
                closest = baseline_sets.get(focal, [])
                div = [p["peer_city_id"] for p in by_focal_sc.get((focal, "diversified"), [])]
                rows.append({**base, "surface": SURFACE_ENUM[2],
                             "peer_rank": 0,
                             "peer_city_id": focal,
                             "peer_city_name": roster_focal["city_name"],
                             "alternative_peer_codes_no_geo": "|".join(alt_sets.get(focal, [])),
                             "closest_set": "|".join(closest),
                             "diversified_set": "|".join(div),
                             "safe_copy": "closest (pure nearest) vs diversified (two-per-country) peer sets"})
            # Surface 4: context_after_matching - per-member post-hoc measure
            for rank, cid in enumerate([focal] + [p["peer_city_id"] for p in peer_rows]):
                rows.append({**base, "surface": SURFACE_ENUM[3],
                             "member_role": "focal" if cid == focal else "peer",
                             "peer_rank": rank,
                             "peer_city_id": cid,
                             "peer_city_name": roster_by_id[cid]["city_name"],
                             "context_measure_name": CONTEXT_FEATURE,
                             "context_measure_value": roster_by_id[cid]["public_transport_access_share"],
                             "context_only_label": "post-hoc measure; never used in fitting",
                             "safe_copy": f"{CONTEXT_FEATURE} shown after peer membership frozen"})
    return rows


ROSTER_COLS = [
    "city_id", "city_name", "country_code", "country_name",
    "subregion", "income_tier", "membership_state", "exclusion_reason",
    "public_transport_access_share",
]
PEER_RESULT_COLS = [
    "focal_city_id", "scenario_label", "peer_rank", "peer_city_id",
    "structural_distance", "subregion_penalty", "income_penalty",
    "final_distance", "peer_country_code", "same_country_peer_count",
]
PEER_EXPL_COLS = PEER_RESULT_COLS + [
    "peer_key", "scaled_feature_name", "scaled_feature_value_focal",
    "scaled_feature_value_peer", "peer_minus_focal_scaled",
    "distance_component", "signed_component",
]
STABILITY_COLS = [
    "focal_city_id", "variant_label",
    "method_signature", "metric", "scaler", "feature_set",
    "weights_policy", "scenario_policy",
    "subregion_penalty_fraction", "income_penalty_fraction",
    "jaccard_value", "below_threshold", "threshold", "peer_set",
]
STABILITY_SUMMARY_COLS = [
    "focal_city_id", "min_jaccard", "median_jaccard", "mean_jaccard",
    "stability_badge", "threshold", "n_variants",
]
VARIANT_META_COLS = [
    "variant_label", "method_signature", "metric", "scaler",
    "feature_set", "weights_policy", "scenario_policy",
    "subregion_penalty_fraction", "income_penalty_fraction",
]
COVERAGE_COLS = [
    "dimension", "group_value", "n_cities", "inbound_peer_slots",
    "slot_share", "representation_ratio",
]
CONTEXT_COLS = [
    "focal_city_id", "scenario_label", "member_rank", "member_city_id",
    "context_measure_name", "context_measure_value",
    "context_only_label", "context_missing",
]
PRESENTATION_COLS = [
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
]


def generate(out_dir: Path = OUT) -> Dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    roster, raw = build_roster()
    baseline = build_baseline_peers(roster, raw)
    stability = build_stability(roster, raw, baseline)
    coverage = build_coverage_exposure(roster, baseline)
    context = build_context_comparison(roster, baseline)
    landing = default_landing(roster, raw)
    presentation = build_presentation(roster, baseline, stability, landing)

    write_csv(out_dir / "city_roster.csv", roster, ROSTER_COLS)
    write_csv(out_dir / "peer_result.csv", baseline["peer_result"], PEER_RESULT_COLS)
    write_csv(out_dir / "peer_explanation.csv", baseline["peer_explanation"], PEER_EXPL_COLS)
    write_csv(out_dir / "stability.csv", stability["stability"], STABILITY_COLS)
    write_csv(out_dir / "stability_summary.csv", stability["stability_summary"], STABILITY_SUMMARY_COLS)
    write_csv(out_dir / "variant_metadata.csv", stability["variant_metadata"], VARIANT_META_COLS)
    write_csv(out_dir / "coverage_exposure.csv", coverage, COVERAGE_COLS)
    write_csv(out_dir / "context_comparison.csv", context, CONTEXT_COLS)
    write_csv(out_dir / "peer_scenario_surface.csv", presentation, PRESENTATION_COLS)

    receipt = {
        "project_id": PROJECT_ID,
        "snapshot_id": SNAPSHOT_ID,
        "method_version": METHOD_VERSION,
        "method_constants": {
            "n_cities": N_CITIES,
            "k_peers": K_PEERS,
            "country_cap": COUNTRY_CAP,
            "stability_threshold": STABILITY_THRESHOLD,
            "n_variants": N_VARIANTS,
            "subregion_penalty_fraction": SUBREGION_PENALTY_FRAC,
            "income_penalty_fraction": INCOME_PENALTY_FRAC,
            "strong_subregion_penalty_fraction": STRONG_SUBREGION_PENALTY_FRAC,
            "strong_income_penalty_fraction": STRONG_INCOME_PENALTY_FRAC,
            "baseline_metric": "euclidean",
            "supported_metrics": list(SUPPORTED_METRICS),
            "structural_features": STRUCTURAL_FEATURES,
            "context_feature": CONTEXT_FEATURE,
            "map_mode": "authorized non-map fallback; complete peer roster retained; no coordinate source added",
        },
        "landing_city_id": landing,
        "row_counts": {
            "city_roster": len(roster),
            "peer_result": len(baseline["peer_result"]),
            "peer_explanation": len(baseline["peer_explanation"]),
            "stability": len(stability["stability"]),
            "stability_summary": len(stability["stability_summary"]),
            "variant_metadata": len(stability["variant_metadata"]),
            "coverage_exposure": len(coverage),
            "context_comparison": len(context),
            "peer_scenario_surface": len(presentation),
        },
        "variant_labels": [v["variant_label"] for v in stability["variant_metadata"]],
        "variant_method_signatures": [v["method_signature"] for v in stability["variant_metadata"]],
    }
    write_json(out_dir / "method_receipt.json", receipt)
    return receipt


if __name__ == "__main__":
    r = generate()
    print(f"[{PROJECT_ID}] snapshot={r['snapshot_id']} method={r['method_version']}")
    print(f"  landing_city={r['landing_city_id']}")
    for k, v in r["row_counts"].items():
        print(f"  {k}: {v} rows")
