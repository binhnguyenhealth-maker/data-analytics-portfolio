"""Tests for the Explainable Urban Peer Scenario Stability Explorer.

Positive control + fail-closed negative fixtures covering analytical and
structural / packaging invariants.

The negative fixtures MUTATE real generated outputs and pass them through the
reusable validator (`src/validator.py`), asserting a stable rejection code.
They do not assert that a hand-written local constant is bad.
"""
from __future__ import annotations

import csv
import importlib.util
import json
import math
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

_SRC = Path(__file__).resolve().parents[1] / "src"
_SPEC = importlib.util.spec_from_file_location(
    "pse_generate", _SRC / "generate.py")
gen = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(gen)

_VSPEC = importlib.util.spec_from_file_location(
    "pse_validator", _SRC / "validator.py")
val = importlib.util.module_from_spec(_VSPEC)
_VSPEC.loader.exec_module(val)


def _load_csv(path: Path) -> List[Dict[str, Any]]:
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _write_csv(path: Path, rows: List[Dict[str, Any]]) -> None:
    """Rewrite a CSV preserving its column order."""
    cols = list(rows[0].keys())
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, lineterminator="\n",
                           extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


class TestPositiveFixture(unittest.TestCase):
    """Positive controls: regenerate into a temp dir and assert invariants.

    These tests call the generator directly (one source of truth) and the
    reusable validator on the resulting bytes.
    """

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="pse_test_")
        cls.out = Path(cls.tmp)
        cls.receipt = gen.generate(cls.out)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _csv(self, name):
        return _load_csv(self.out / f"{name}.csv")

    # ---- validator-driven positive controls ------------------------------

    def test_validator_passes_clean_on_generated_output(self):
        report = val.validate(val.load_dir(self.out))
        self.assertTrue(report["ok"],
                        f"validator should pass clean; got {report['failures'][:5]}")

    def test_validator_success_receipt_reports_truthful_counts(self):
        """REV1 DoD 7: a clean success report must NOT say n_checks=0. The
        executed-check count must be a positive integer (each atomic
        predicate evaluated against the data is counted, including passing
        checks), and n_failures must be 0. This replaces the misleading
        `n_checks=len(failures)` semantics that reported 0 on success."""
        report = val.validate(val.load_dir(self.out))
        self.assertTrue(report["ok"])
        self.assertEqual(report["n_failures"], 0)
        self.assertIsInstance(report["n_checks"], int)
        self.assertGreater(report["n_checks"], 0,
                           "success report must carry a truthful executed-check "
                           "count > 0, not n_checks=len(failures)=0")
        # n_checks must reflect actual predicates run, so it must be far larger
        # than the failure count even on success.
        self.assertGreater(report["n_checks"], report["n_failures"])

    def test_row_counts_match_receipt(self):
        rc = self.receipt["row_counts"]
        for name, expected in rc.items():
            if (self.out / f"{name}.csv").exists():
                self.assertEqual(len(self._csv(name)), expected,
                                 f"{name} row count mismatch")

    def test_exactly_five_peers_per_focal_scenario(self):
        rows = self._csv("peer_result")
        from collections import defaultdict
        per = defaultdict(set)
        for r in rows:
            per[(r["focal_city_id"], r["scenario_label"])].add(r["peer_rank"])
        for key, ranks in per.items():
            self.assertEqual(len(ranks), gen.K_PEERS,
                             f"{key} has {len(ranks)} peers, expected {gen.K_PEERS}")

    def test_no_self_peer(self):
        for r in self._csv("peer_result"):
            self.assertNotEqual(r["peer_city_id"], r["focal_city_id"],
                                "focal cannot be its own peer")

    def test_distance_reconciliation_euclidean_baseline(self):
        # sum of distance_components over features == structural_distance
        expl = self._csv("peer_explanation")
        pr = self._csv("peer_result")
        sd_map = {(r["focal_city_id"], r["scenario_label"], r["peer_city_id"]):
                  float(r["structural_distance"]) for r in pr}
        agg: Dict[tuple, float] = {}
        for e in expl:
            key = (e["focal_city_id"], e["scenario_label"], e["peer_city_id"])
            agg[key] = agg.get(key, 0.0) + float(e["distance_component"])
        for key, summed in agg.items():
            self.assertGreater(summed, 0)
            self.assertAlmostEqual(summed, sd_map[key], places=4,
                                    msg=f"distance components don't sum to structural for {key}")

    def test_distance_reconciliation_under_manhattan_direct_recompute(self):
        """The Manhattan decomposition must also reconcile. The stability
        variant manhattan_soft_penalty executes Manhattan distance; recompute
        the structural distance and confirm the component formula holds."""
        roster, raw = gen.build_roster()
        baseline = gen.build_baseline_peers(roster, raw)
        transformed = baseline["transformed"]
        scaled = gen._scaled_for(transformed, gen.STRUCTURAL_FEATURES, "robust")
        roster_by_id = {r["city_id"]: r for r in roster}
        cids = [r["city_id"] for r in roster]
        focal = cids[0]
        peers = gen.select_peers(focal, scaled, roster_by_id, cids,
                                 gen.STRUCTURAL_FEATURES, scenario="baseline",
                                 metric="manhattan")
        for p in peers:
            expl = gen.explain_peer(focal, p["peer_city_id"], scaled,
                                    gen.STRUCTURAL_FEATURES,
                                    p["structural_distance"], metric="manhattan")
            total = sum(float(e["distance_component"]) for e in expl)
            self.assertAlmostEqual(total, p["structural_distance"], places=6,
                                    msg=f"manhattan recon failed for {focal}->{p['peer_city_id']}")

    def test_diversified_respects_country_cap(self):
        rows = self._csv("peer_result")
        roster = {r["city_id"]: r for r in self._csv("city_roster")}
        div = [r for r in rows if r["scenario_label"] == "diversified"]
        from collections import defaultdict
        per_focal_country = defaultdict(lambda: defaultdict(int))
        for r in div:
            cc = roster[r["peer_city_id"]]["country_code"]
            per_focal_country[r["focal_city_id"]][cc] += 1
        for focal, counts in per_focal_country.items():
            for cc, n in counts.items():
                self.assertLessEqual(n, gen.COUNTRY_CAP,
                                     f"focal {focal} country {cc} has {n} diversified peers, cap {gen.COUNTRY_CAP}")

    def test_no_coordinate_columns_in_roster(self):
        with open(self.out / "city_roster.csv", encoding="utf-8") as f:
            header = next(csv.reader(f))
        for forbidden in ["latitude", "longitude", "lat", "lon", "place", "geom"]:
            self.assertNotIn(forbidden, [h.lower() for h in header],
                             f"non-map fallback violated: {forbidden} column present")

    def test_context_is_post_hoc_only(self):
        for r in self._csv("context_comparison"):
            self.assertTrue(r["context_only_label"].strip())
            self.assertFalse(r["context_missing"] in ("True", "true", "1"),
                             "no context row may be missing")

    def test_stability_threshold_disclosed(self):
        with open(self.out / "method_receipt.json", encoding="utf-8") as f:
            receipt = json.load(f)
        self.assertEqual(receipt["method_constants"]["stability_threshold"],
                         gen.STABILITY_THRESHOLD)

    def test_jaccard_values_in_unit_interval(self):
        for r in self._csv("stability"):
            j = float(r["jaccard_value"])
            self.assertGreaterEqual(j, 0.0)
            self.assertLessEqual(j, 1.0)

    # ---- variant identity and method truthfulness ------------------------

    def test_exactly_17_unique_labels_and_signatures(self):
        meta = self._csv("variant_metadata")
        labels = [m["variant_label"] for m in meta]
        sigs = [m["method_signature"] for m in meta]
        self.assertEqual(len(labels), gen.N_VARIANTS)
        self.assertEqual(len(set(labels)), gen.N_VARIANTS,
                         f"duplicate labels: {labels}")
        self.assertEqual(len(set(sigs)), gen.N_VARIANTS,
                         f"duplicate signatures: {sigs}")

    def test_each_focal_has_exactly_17_stability_rows(self):
        from collections import Counter
        c = Counter(r["focal_city_id"] for r in self._csv("stability"))
        for focal, n in c.items():
            self.assertEqual(n, gen.N_VARIANTS, f"focal {focal} has {n} variants")

    def test_no_geography_penalty_executes_zero_penalties(self):
        rows = [r for r in self._csv("stability")
                if r["variant_label"] == "no_geography_penalty"]
        self.assertEqual(len(rows), gen.N_CITIES)
        for r in rows:
            self.assertEqual(float(r["subregion_penalty_fraction"]), 0.0)
            self.assertEqual(float(r["income_penalty_fraction"]), 0.0)
            self.assertEqual(r["metric"], "euclidean")

    def test_strong_geography_penalty_uses_stronger_fractions(self):
        rows = [r for r in self._csv("stability")
                if r["variant_label"] == "strong_geography_penalty"]
        self.assertEqual(len(rows), gen.N_CITIES)
        for r in rows:
            self.assertGreater(float(r["subregion_penalty_fraction"]),
                               gen.SUBREGION_PENALTY_FRAC)
            self.assertGreater(float(r["income_penalty_fraction"]),
                               gen.INCOME_PENALTY_FRAC)

    def test_manhattan_variant_uses_manhattan_metric(self):
        rows = [r for r in self._csv("stability")
                if r["variant_label"] == "manhattan_soft_penalty"]
        self.assertEqual(len(rows), gen.N_CITIES)
        for r in rows:
            self.assertEqual(r["metric"], "manhattan")

    def test_named_variants_change_at_least_one_focal_vs_baseline(self):
        """Each materially distinct regime must change at least one focal
        peer set versus the baseline under this synthetic cohort."""
        pr = self._csv("peer_result")
        baseline_sets = {}
        for r in pr:
            if r["scenario_label"] == "baseline":
                baseline_sets.setdefault(r["focal_city_id"], set()).add(r["peer_city_id"])
        stab = self._csv("stability")
        from collections import defaultdict
        ps_by_var = defaultdict(dict)
        for r in stab:
            ps_by_var[r["variant_label"]][r["focal_city_id"]] = \
                set(r["peer_set"].split("|"))
        for v in ["no_geography_penalty", "strong_geography_penalty",
                  "manhattan_soft_penalty", "zscore_soft_penalty",
                  "diversified_country_cap_2"]:
            changed = sum(1 for f in baseline_sets
                          if ps_by_var[v][f] != baseline_sets[f])
            self.assertGreater(changed, 0,
                               f"variant {v} did not change any focal peer set vs baseline")

    def test_diversified_variant_distinct_from_drop_built_up_area(self):
        """The replacement 17th variant must not duplicate drop_built_up_area
        (the defect that motivated the replacement)."""
        stab = self._csv("stability")
        from collections import defaultdict
        ps_by_var = defaultdict(dict)
        for r in stab:
            ps_by_var[r["variant_label"]][r["focal_city_id"]] = \
                set(r["peer_set"].split("|"))
        same = sum(1 for f in ps_by_var["diversified_country_cap_2"]
                   if ps_by_var["diversified_country_cap_2"][f]
                   == ps_by_var["drop_built_up_area"][f])
        self.assertLess(same, gen.N_CITIES,
                        "diversified_country_cap_2 duplicates drop_built_up_area")

    def test_metadata_truthful_via_independent_recompute(self):
        """For a sample focal, independently recompute each named variant's
        peer set from the raw inputs using the stored method metadata and
        confirm it matches the stored peer set."""
        roster, raw = gen.build_roster()
        baseline = gen.build_baseline_peers(roster, raw)
        transformed = baseline["transformed"]
        roster_by_id = {r["city_id"]: r for r in roster}
        cids = [r["city_id"] for r in roster]
        stab = self._csv("stability")
        meta = {m["variant_label"]: m for m in self._csv("variant_metadata")}
        focal = cids[0]
        for vlabel in ["no_geography_penalty", "strong_geography_penalty",
                       "manhattan_soft_penalty", "diversified_country_cap_2"]:
            m = meta[vlabel]
            features = m["feature_set"].split("|")
            regime = m["scaler"]
            metric = m["metric"]
            scenario = m["scenario_policy"]
            sub = float(m["subregion_penalty_fraction"])
            inc = float(m["income_penalty_fraction"])
            scaled = gen._scaled_for(transformed, features, regime)
            peers = gen.select_peers(focal, scaled, roster_by_id, cids, features,
                                     scenario=scenario, metric=metric,
                                     subregion_frac=sub, income_frac=inc)
            recomputed = [p["peer_city_id"] for p in peers]
            stored = [r for r in stab if r["focal_city_id"] == focal
                      and r["variant_label"] == vlabel][0]["peer_set"].split("|")
            self.assertEqual(recomputed, stored,
                             f"{vlabel}: metadata does not reproduce stored peer set")

    # ---- presentation / structure ----------------------------------------

    def test_surfaces_exactly_four(self):
        surfaces = {r["surface"] for r in self._csv("peer_scenario_surface")}
        self.assertEqual(surfaces, set(gen.SURFACE_ENUM))

    def test_landing_has_six_marks(self):
        rows = self._csv("peer_scenario_surface")
        landing_rows = [r for r in rows if r["is_landing"] in ("True", "true", "1")
                        and r["surface"] == "peer_map_and_table"
                        and r["scenario_label"] == "baseline"]
        self.assertEqual(len(landing_rows), gen.K_PEERS + 1,
                         "landing focal x baseline must have 6 marks (focal + 5 peers)")
        roles = {r["member_role"] for r in landing_rows}
        self.assertEqual(roles, {"focal", "peer"})

    def test_no_ranking_language(self):
        banned = ["best", "worst", "objective", "true peer", "performance ranking"]
        for r in self._csv("peer_scenario_surface"):
            text = (r.get("safe_copy", "") or "").lower()
            for b in banned:
                self.assertNotIn(b, text, f"ranking language '{b}' in: {text}")

    def test_default_landing_is_deterministic(self):
        landing = self.receipt["landing_city_id"]
        self.assertTrue(landing.startswith("CTY-"))
        tmp2 = tempfile.mkdtemp()
        try:
            r2 = gen.generate(Path(tmp2))
            self.assertEqual(landing, r2["landing_city_id"],
                             "landing city must be deterministic across regenerations")
        finally:
            shutil.rmtree(tmp2, ignore_errors=True)

    def test_k_constants_disclosed(self):
        with open(self.out / "method_receipt.json", encoding="utf-8") as f:
            receipt = json.load(f)
        mc = receipt["method_constants"]
        self.assertEqual(mc["k_peers"], gen.K_PEERS)
        self.assertEqual(mc["country_cap"], gen.COUNTRY_CAP)
        self.assertEqual(mc["n_variants"], gen.N_VARIANTS)
        self.assertIn("map_mode", mc)
        self.assertIn("non-map fallback", mc["map_mode"])
        # New: baseline metric and supported metrics disclosed
        self.assertEqual(mc["baseline_metric"], "euclidean")
        self.assertEqual(set(mc["supported_metrics"]), {"euclidean", "manhattan"})

    def test_peer_key_unique_in_explanation(self):
        expl = self._csv("peer_explanation")
        keys = [(r["peer_key"], r["scaled_feature_name"]) for r in expl]
        self.assertEqual(len(keys), len(set(keys)),
                         "(peer_key, feature) must be unique in peer_explanation")


class TestNegativeMutation(unittest.TestCase):
    """Fail-closed negative fixtures. Each test copies the real generated
    output, mutates one thing, runs the reusable validator, and asserts a
    STABLE rejection code appears.
    """

    @classmethod
    def setUpClass(cls):
        cls.clean = tempfile.mkdtemp(prefix="pse_clean_")
        gen.generate(Path(cls.clean))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.clean, ignore_errors=True)

    def _mutated_copy(self, mutator):
        """Copy the clean output to a fresh temp dir and apply mutator(name->rows)."""
        tmp = tempfile.mkdtemp(prefix="pse_mut_")
        for p in Path(self.clean).iterdir():
            if p.is_file():
                shutil.copy2(p, Path(tmp) / p.name)
        mutator(Path(tmp))
        self.addCleanup(shutil.rmtree, tmp, True)
        return tmp

    def _codes(self, tmp):
        rep = val.validate(val.load_dir(Path(tmp)))
        return val.codes(rep["failures"])

    def _mutate_csv(self, tmp_dir, fname, fn):
        """Load, fn(rows)->rows, rewrite under tmp_dir."""
        p = Path(tmp_dir) / fname
        rows = _load_csv(p)
        rows = fn(rows)
        _write_csv(p, rows)

    # --- peer selection negatives -----------------------------------------

    def test_mut_drop_peer_row(self):
        def mut(d):
            self._mutate_csv(d, "peer_result.csv", lambda r: r[1:])
        self.assertIn("PEER_COUNT_NOT_K", self._codes(self._mutated_copy(mut)))

    def test_mut_self_peer(self):
        def mut(d):
            def f(rows):
                rows[0]["peer_city_id"] = rows[0]["focal_city_id"]
                return rows
            self._mutate_csv(d, "peer_result.csv", f)
        self.assertIn("PEER_SELF_SELECTED", self._codes(self._mutated_copy(mut)))

    def test_mut_duplicate_peer(self):
        def mut(d):
            def f(rows):
                # duplicate the first peer row with rank 6 (ranks no longer 1..5)
                dup = dict(rows[0])
                dup["peer_rank"] = "6"
                rows.append(dup)
                return rows
            self._mutate_csv(d, "peer_result.csv", f)
        codes = self._codes(self._mutated_copy(mut))
        self.assertIn("PEER_DUPLICATE", codes)

    def test_mut_diversified_cap_violation(self):
        def mut(d):
            def f(rows):
                # CTY-001 is country ZNA (index 0). ZNA cities: 001,007,013,019,025.
                # Force CTY-001 diversified peers all to ZNA => > cap 2.
                zna = ["CTY-007", "CTY-013", "CTY-019", "CTY-025", "CTY-001"]
                i = 0
                for r in rows:
                    if r["scenario_label"] == "diversified" and r["focal_city_id"] == "CTY-001":
                        r["peer_city_id"] = zna[i]
                        r["peer_country_code"] = "ZNA"
                        i += 1
                return rows
            self._mutate_csv(d, "peer_result.csv", f)
        self.assertIn("DIVERSIFIED_COUNTRY_CAP_EXCEEDED",
                      self._codes(self._mutated_copy(mut)))

    # --- explanation negatives --------------------------------------------

    def test_mut_explanation_orphan(self):
        def mut(d):
            def f(rows):
                rows[0]["peer_key"] = "CTY-XXX::baseline::CTY-YYY"
                rows[0]["peer_city_id"] = "CTY-YYY"
                return rows
            self._mutate_csv(d, "peer_explanation.csv", f)
        self.assertIn("EXPLANATION_ORPHAN", self._codes(self._mutated_copy(mut)))

    def test_mut_explanation_reconciliation(self):
        def mut(d):
            def f(rows):
                rows[0]["distance_component"] = str(float(rows[0]["distance_component"]) + 5.0)
                return rows
            self._mutate_csv(d, "peer_explanation.csv", f)
        self.assertIn("EXPLANATION_RECONCILIATION_FAIL",
                      self._codes(self._mutated_copy(mut)))

    # --- context negatives ------------------------------------------------

    def test_mut_context_used_in_fitting(self):
        def mut(d):
            p = Path(d) / "method_receipt.json"
            with open(p) as f:
                rec = json.load(f)
            feats = list(rec["method_constants"]["structural_features"])
            ctx = rec["method_constants"]["context_feature"]
            if ctx not in feats:
                feats.append(ctx)
            rec["method_constants"]["structural_features"] = feats
            with open(p, "w") as f:
                json.dump(rec, f)
        self.assertIn("CONTEXT_USED_IN_FITTING",
                      self._codes(self._mutated_copy(mut)))

    # --- variant negatives ------------------------------------------------

    def test_mut_variant_count_wrong(self):
        def mut(d):
            self._mutate_csv(d, "variant_metadata.csv", lambda r: r[1:])
        self.assertIn("VARIANT_COUNT_NOT_N", self._codes(self._mutated_copy(mut)))

    def test_mut_duplicate_signature(self):
        def mut(d):
            def f(rows):
                # Copy row 1's signature into row 0 in BOTH stability and metadata
                sig = rows[1]["method_signature"]
                metric = rows[1]["metric"]
                for fld in ["method_signature", "metric", "scaler", "feature_set",
                            "weights_policy", "scenario_policy",
                            "subregion_penalty_fraction", "income_penalty_fraction"]:
                    rows[0][fld] = rows[1][fld]
                return rows
            self._mutate_csv(d, "variant_metadata.csv", f)
            # mutate stability identically so MISMATCH does not mask DUP_SIGNATURE
            def g(rows):
                for r in rows:
                    if r["variant_label"] == rows[0]["variant_label"]:
                        for fld in ["method_signature", "metric", "scaler", "feature_set",
                                    "weights_policy", "scenario_policy",
                                    "subregion_penalty_fraction", "income_penalty_fraction"]:
                            r[fld] = rows[1][fld]
                return rows
            self._mutate_csv(d, "stability.csv", g)
        self.assertIn("VARIANT_DUPLICATE_SIGNATURE",
                      self._codes(self._mutated_copy(mut)))

    def test_mut_named_method_mislabeled(self):
        def mut(d):
            def f(rows):
                # Corrupt no_geography_penalty's stored fractions in the stability
                # row only (the executed-method record) to 0.5.
                for r in rows:
                    if r["variant_label"] == "no_geography_penalty":
                        r["subregion_penalty_fraction"] = "0.5"
                        r["income_penalty_fraction"] = "0.5"
                return rows
            self._mutate_csv(d, "stability.csv", f)
        codes = self._codes(self._mutated_copy(mut))
        self.assertIn("NAMED_METHOD_MISLABELED", codes)

    def test_mut_named_method_no_change_vs_baseline(self):
        """If a named variant's peer sets are overwritten to equal baseline,
        the validator must reject it even though the metadata is truthful."""
        def mut(d):
            # Read baseline peer sets
            pr = _load_csv(Path(d) / "peer_result.csv")
            baseline = {}
            for r in pr:
                if r["scenario_label"] == "baseline":
                    baseline.setdefault(r["focal_city_id"], []).append(r["peer_city_id"])
            for f in baseline:
                baseline[f].sort()

            def f(rows):
                for r in rows:
                    if r["variant_label"] == "no_geography_penalty":
                        r["peer_set"] = "|".join(baseline.get(r["focal_city_id"], []))
                        # recompute jaccard to keep consistency and isolate the
                        # NO_CHANGE check (avoid JACCARD_RECOMPUTE_FAIL masking)
                        r["jaccard_value"] = "1.000000"
                return rows
            self._mutate_csv(d, "stability.csv", f)
        self.assertIn("NAMED_METHOD_NO_CHANGE_VS_BASELINE",
                      self._codes(self._mutated_copy(mut)))

    def test_mut_jaccard_recompute_fail(self):
        def mut(d):
            def f(rows):
                rows[0]["jaccard_value"] = "0.9999"  # break recomputation
                return rows
            self._mutate_csv(d, "stability.csv", f)
        self.assertIn("JACCARD_RECOMPUTE_FAIL",
                      self._codes(self._mutated_copy(mut)))

    def test_mut_jaccard_out_of_range(self):
        def mut(d):
            def f(rows):
                rows[0]["jaccard_value"] = "1.5"
                # peer_set unchanged so recompute gives the true value, but the
                # stored value is out of range -> OOB fires.
                return rows
            self._mutate_csv(d, "stability.csv", f)
        codes = self._codes(self._mutated_copy(mut))
        self.assertIn("JACCARD_OUT_OF_RANGE", codes)

    # --- structural negatives ---------------------------------------------

    def test_mut_non_map_column(self):
        def mut(d):
            p = Path(d) / "city_roster.csv"
            rows = _load_csv(p)
            cols = list(rows[0].keys()) + ["latitude"]
            with open(p, "w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=cols, lineterminator="\n")
                w.writeheader()
                for r in rows:
                    r["latitude"] = "1.0"
                    w.writerow(r)
        codes = self._codes(self._mutated_copy(mut))
        self.assertIn("NON_MAP_COLUMN_PRESENT", codes)

    def test_mut_surface_set_wrong(self):
        def mut(d):
            def f(rows):
                rows[0]["surface"] = "bogus_surface"
                return rows
            self._mutate_csv(d, "peer_scenario_surface.csv", f)
        self.assertIn("SURFACE_SET_WRONG", self._codes(self._mutated_copy(mut)))

    def test_mut_landing_wrong_mark_count(self):
        def mut(d):
            def f(rows):
                out = []
                removed = False
                for r in rows:
                    if (not removed and r["is_landing"] in ("True", "true", "1")
                            and r["surface"] == "peer_map_and_table"
                            and r["scenario_label"] == "baseline"
                            and r["member_role"] == "peer"):
                        removed = True
                        continue
                    out.append(r)
                return out
            self._mutate_csv(d, "peer_scenario_surface.csv", f)
        self.assertIn("LANDING_WRONG_MARK_COUNT",
                      self._codes(self._mutated_copy(mut)))

    def test_mut_ranking_language(self):
        def mut(d):
            def f(rows):
                rows[0]["safe_copy"] = "this is the best peer ranking"
                return rows
            self._mutate_csv(d, "peer_scenario_surface.csv", f)
        self.assertIn("RANKING_LANGUAGE", self._codes(self._mutated_copy(mut)))

    def test_mut_schema_missing_column(self):
        def mut(d):
            # rewrite a CSV with a wrong header
            p = Path(d) / "city_roster.csv"
            rows = _load_csv(p)
            cols = [c for c in rows[0].keys() if c != "income_tier"]
            with open(p, "w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=cols, lineterminator="\n",
                                   extrasaction="ignore")
                w.writeheader()
                for r in rows:
                    w.writerow(r)
        self.assertIn("SCHEMA_MISSING_COLUMN",
                      self._codes(self._mutated_copy(mut)))

    # --- REV1 coverage gates (manager fail-open closures) ------------------
    # Each of these reproduces a distinct coordinated-omission or drift
    # scenario the prior validator admitted. They mutate REAL generated
    # outputs and assert a STABLE coverage rejection code.

    def test_mut_focal_removed_across_tables(self):
        """Manager finding reproduction: keep all 30 roster rows and the
        receipt's row-count contract, but remove every CTY-030 focal row
        from all focal-keyed tables. The validator must reject with a stable
        coverage code (FOCAL_COVERAGE_MISSING)."""
        target = "CTY-030"
        focal_tables = [
            "peer_result.csv", "peer_explanation.csv",
            "stability.csv", "stability_summary.csv",
            "context_comparison.csv", "peer_scenario_surface.csv",
        ]

        def mut(d):
            for fname in focal_tables:
                self._mutate_csv(d, fname,
                                 lambda rows, t=target: [r for r in rows
                                                         if r.get("focal_city_id") != t])

        codes = self._codes(self._mutated_copy(mut))
        self.assertIn("FOCAL_COVERAGE_MISSING", codes,
                      f"removing CTY-030 from all focal tables must raise "
                      f"FOCAL_COVERAGE_MISSING; got {codes}")

    def test_mut_missing_scenario(self):
        """Drop one scenario for one focal. The validator must reject via
        SCENARIO_INVENTORY_WRONG (and FOCAL_COVERAGE_MISSING on
        context_comparison which keys every focal x scenario)."""
        def mut(d):
            def f(rows):
                return [r for r in rows
                        if not (r["focal_city_id"] == "CTY-001"
                                and r["scenario_label"] == "diversified")]
            self._mutate_csv(d, "peer_result.csv", f)
            self._mutate_csv(d, "peer_explanation.csv", f)
        codes = self._codes(self._mutated_copy(mut))
        self.assertIn("SCENARIO_INVENTORY_WRONG", codes,
                      f"missing scenario must raise SCENARIO_INVENTORY_WRONG; got {codes}")

    def test_mut_duplicate_peer_result_composite_key(self):
        """Duplicate an exact (focal, scenario, peer) composite key. Prior
        validator only checked peer-rank uniqueness within a group, not the
        composite key."""
        def mut(d):
            def f(rows):
                dup = dict(rows[0])
                rows.append(dup)
                return rows
            self._mutate_csv(d, "peer_result.csv", f)
        codes = self._codes(self._mutated_copy(mut))
        self.assertIn("PEER_RESULT_COMPOSITE_KEY_DUPLICATE", codes,
                      f"duplicate composite key must raise "
                      f"PEER_RESULT_COMPOSITE_KEY_DUPLICATE; got {codes}")

    def test_mut_duplicate_explanation_composite_key(self):
        """Duplicate an exact (peer_key, scaled_feature_name) composite key in
        peer_explanation."""
        def mut(d):
            def f(rows):
                dup = dict(rows[0])
                rows.append(dup)
                return rows
            self._mutate_csv(d, "peer_explanation.csv", f)
        codes = self._codes(self._mutated_copy(mut))
        self.assertIn("EXPLANATION_COMPOSITE_KEY_DUPLICATE", codes,
                      f"duplicate explanation composite key must raise "
                      f"EXPLANATION_COMPOSITE_KEY_DUPLICATE; got {codes}")

    def test_mut_explanation_coverage_incomplete(self):
        """Drop one feature-explanation row for one peer. The validator must
        detect that the peer's explanation feature set no longer matches the
        scenario's canonical inventory."""
        def mut(d):
            def f(rows):
                # remove exactly one (focal, scenario, peer, feature) row
                return rows[1:]
            self._mutate_csv(d, "peer_explanation.csv", f)
        codes = self._codes(self._mutated_copy(mut))
        self.assertIn("EXPLANATION_COVERAGE_INCOMPLETE", codes,
                      f"incomplete explanation coverage must raise "
                      f"EXPLANATION_COVERAGE_INCOMPLETE; got {codes}")

    def test_mut_missing_variant_focal(self):
        """Remove one variant row for one focal. The validator must reject:
        that focal's variant count != 17 AND its variant set != metadata."""
        def mut(d):
            target_focal = None
            target_variant = None
            for r in _load_csv(Path(d) / "stability.csv"):
                target_focal = r["focal_city_id"]
                target_variant = r["variant_label"]
                break

            def f(rows, tf=target_focal, tv=target_variant):
                return [r for r in rows
                        if not (r["focal_city_id"] == tf
                                and r["variant_label"] == tv)]
            self._mutate_csv(d, "stability.csv", f)
        codes = self._codes(self._mutated_copy(mut))
        self.assertIn("VARIANT_COUNT_NOT_N", codes,
                      f"missing variant-focal row must raise "
                      f"VARIANT_COUNT_NOT_N; got {codes}")

    def test_mut_receipt_row_count_drift(self):
        """Leave all files intact but lie about a row count in the receipt.
        The validator must reconcile receipt claims to actual bytes."""
        def mut(d):
            p = Path(d) / "method_receipt.json"
            with open(p) as f:
                rec = json.load(f)
            rec["row_counts"]["city_roster"] = int(rec["row_counts"]["city_roster"]) + 1
            with open(p, "w") as f:
                json.dump(rec, f)
        codes = self._codes(self._mutated_copy(mut))
        self.assertIn("RECEIPT_ROW_COUNT_DRIFT", codes,
                      f"receipt row-count drift must raise "
                      f"RECEIPT_ROW_COUNT_DRIFT; got {codes}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
