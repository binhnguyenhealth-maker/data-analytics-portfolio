"""Tests for the Urban Mobility Gap Diagnostic synthetic project.

Structure:
- `TestPositiveFixture` generates canonical artifacts and runs the real
  reusable validator (`umgd_validate.validate_artifacts`) over them. It also
  re-derives the default-focus city and review set directly from output bytes
  to prove the stored flags come from the published rows.
- `TestNegativeFixtures` mutates ACTUAL generated rows, writes them to a temp
  dir alongside the unchanged canonical files, and runs the SAME real
  validator, asserting a specific stable rejection code for each mutation.

The negative fixtures therefore exercise the production fail-closed path
rather than asserting that a hand-coded local constant is bad.

SYNTHETIC DATA / INDEPENDENT PORTFOLIO PROJECT
"""
from __future__ import annotations

import csv
import importlib.util
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[3]
sys_ref = __import__("sys")
sys_ref.path.insert(0, str(ROOT))

_PROJ = Path(__file__).resolve().parents[1]

_SPEC = importlib.util.spec_from_file_location(
    "umgd_generate", _PROJ / "src" / "generate.py")
gen = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(gen)

_VSPEC = importlib.util.spec_from_file_location(
    "umgd_validate", _PROJ / "src" / "umgd_validate.py")
val = importlib.util.module_from_spec(_VSPEC)
_VSPEC.loader.exec_module(val)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _load_csv(path: Path) -> List[Dict[str, Any]]:
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _write_csv(path: Path, rows: List[Dict[str, Any]]) -> None:
    if not rows:
        return
    fieldnames = list(rows[0].keys())
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def _copy_artifacts(src: Path, dst: Path) -> None:
    """Copy every generated artifact from src to dst (a fresh temp dir)."""
    dst.mkdir(parents=True, exist_ok=True)
    for name in ["city_model.csv", "dashboard_presentation.csv",
                 "robustness_group.csv", "robustness_summary.csv",
                 "geographic_warnings.csv", "model_receipt.json"]:
        shutil.copy2(src / name, dst / name)


# ---------------------------------------------------------------------------
# Positive control
# ---------------------------------------------------------------------------
class TestPositiveFixture(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="umgd_test_")
        cls.out = Path(cls.tmp)
        cls.receipt = gen.generate(cls.out)
        # Run the real validator once; it must pass on canonical output.
        cls.validation = val.validate_artifacts(cls.out)

    def _csv(self, name):
        return _load_csv(self.out / name)

    # --- validator as the single source of pass/fail ---
    def test_validator_passes_canonical_artifacts(self):
        self.assertEqual(self.validation["verdict"], "VALID")

    def test_validator_reports_24_cities(self):
        self.assertEqual(self.validation["n_cities"], 24)

    # --- grain and counts ---
    def test_city_model_is_city_level_grain(self):
        rows = self._csv("city_model.csv")
        self.assertEqual(len(rows), 24)
        self.assertEqual(len({r["city_id"] for r in rows}), 24)
        self.assertTrue(all(r["city_key"] == r["city_id"] for r in rows))

    def test_score_panel_has_24_distinct_marks(self):
        rows = self._csv("dashboard_presentation.csv")
        score = [r for r in rows if r["panel"] == "score"]
        self.assertEqual(len(score), 24)
        coords = {(r["actual_association"], r["expected_association"]) for r in score}
        self.assertEqual(len(coords), 24)

    def test_presentation_panel_counts(self):
        rows = self._csv("dashboard_presentation.csv")
        from collections import Counter
        counts = Counter(r["panel"] for r in rows)
        # 1 score + 4 diagnostic + 5 modal + 7 predictor per city, 24 cities
        self.assertEqual(counts["score"], 24)
        self.assertEqual(counts["diagnostic"], 24 * 4)
        self.assertEqual(counts["modal"], 24 * 5)
        self.assertEqual(counts["predictor"], 24 * 7)
        self.assertEqual(len(rows), 24 * (1 + 4 + 5 + 7))

    # --- default-focus recomputed from bytes ---
    def test_default_focus_recomputes_from_stored_rows(self):
        rows = self._csv("city_model.csv")
        abs_gaps = {r["city_id"]: float(r["gap_absolute"]) for r in rows}
        expected_default = val.derive_default_focus(abs_gaps)
        stored_defaults = [r["city_id"] for r in rows
                           if str(r["is_default_focus"]).lower() in ("true", "1")]
        self.assertEqual(len(stored_defaults), 1)
        self.assertEqual(stored_defaults[0], expected_default)
        self.assertEqual(stored_defaults[0], self.receipt["default_focus_city"])

    # --- review set recomputed from bytes and nonempty ---
    def test_review_set_recomputes_from_stored_rows_and_nonempty(self):
        rows = self._csv("city_model.csv")
        abs_gaps = [float(r["gap_absolute"]) for r in rows]
        threshold = val.derive_review_threshold(abs_gaps)
        expected_review = {r["city_id"] for r in rows
                           if float(r["gap_absolute"]) >= threshold - 1e-12}
        stored_review = {r["city_id"] for r in rows if r["review_flag"] == "review case"}
        self.assertEqual(stored_review, expected_review)
        self.assertGreater(len(stored_review), 0, "review set must be nonempty")
        self.assertEqual(self.receipt["review_case_count"], len(stored_review))

    # --- robustness: real deterministic variants, not random ---
    def test_robustness_has_eight_distinct_variants(self):
        rows = self._csv("robustness_group.csv")
        labels = [r["variant_label"] for r in rows]
        self.assertEqual(len(labels), 8)
        self.assertEqual(len(set(labels)), 8)
        self.assertEqual(labels[0], "baseline")

    def test_robustness_variants_actually_differ(self):
        """At least one variant must change the executed method (not all 1.0)."""
        rows = self._csv("robustness_group.csv")
        jaccards = {r["variant_label"]: float(r["jaccard_value"]) for r in rows}
        # If every variant were a no-op, all jaccards would be 1.0. Requiring
        # at least one non-baseline value below 1.0 proves real perturbation.
        non_baseline = [j for lbl, j in jaccards.items() if lbl != "baseline"]
        self.assertTrue(any(abs(j - 1.0) > 1e-9 for j in non_baseline),
                        f"robustness variants are no-op; jaccards={jaccards}")

    def test_baseline_variant_is_identity(self):
        rows = self._csv("robustness_group.csv")
        baseline = next(r for r in rows if r["variant_label"] == "baseline")
        self.assertAlmostEqual(float(baseline["jaccard_value"]), 1.0, places=6)
        # baseline residual metrics must equal the cohort's own residual stats
        cm = self._csv("city_model.csv")
        import math
        resid = [float(r["gap_signed"]) for r in cm]
        n = len(resid)
        exp_mae = round(sum(abs(v) for v in resid) / n, 6)
        exp_rmse = round(math.sqrt(sum(v * v for v in resid) / n), 6)
        exp_bias = round(sum(resid) / n, 6)
        self.assertAlmostEqual(float(baseline["mae"]), exp_mae, places=5)
        self.assertAlmostEqual(float(baseline["rmse"]), exp_rmse, places=5)
        self.assertAlmostEqual(float(baseline["bias"]), exp_bias, places=5)

    def test_robustness_metrics_recompute_from_data(self):
        """Validator already recomputes every metric; this asserts it ran."""
        # validate_artifacts (run in setUpClass) re-derives Jaccard/MAE/RMSE/
        # bias for all 8 variants and would have raised if any were random.
        self.assertEqual(self.validation["n_robustness_variants"], 8)

    # --- predictor integrity ---
    def test_feature_set_excludes_target_and_context_fields(self):
        with open(self.out / "model_receipt.json", encoding="utf-8") as f:
            receipt = json.load(f)
        feats = set(receipt["feature_set"])
        self.assertFalse(feats & val.TARGET_DENYLIST)
        self.assertEqual(feats, set(val.PREDICTOR_INVENTORY))

    def test_scaler_method_does_not_claim_fold_or_holdout(self):
        with open(self.out / "model_receipt.json", encoding="utf-8") as f:
            receipt = json.load(f)
        sm = receipt["scaler_method"].lower()
        for bad in ["fold", "holdout", "cross-validation", "training-fold"]:
            self.assertNotIn(bad, sm)

    # --- determinism: same-process regeneration is byte-identical ---
    def test_regeneration_is_byte_identical_same_process(self):
        tmp2 = tempfile.mkdtemp(prefix="umgd_det_")
        out2 = Path(tmp2)
        gen.generate(out2)
        from shared.synthetic import sha256_file
        for name in ["city_model.csv", "dashboard_presentation.csv",
                     "robustness_group.csv", "robustness_summary.csv",
                     "geographic_warnings.csv", "model_receipt.json"]:
            self.assertEqual(sha256_file(self.out / name),
                             sha256_file(out2 / name),
                             f"{name} not byte-identical across regenerations")

    def test_cross_module_hashseed_independence(self):
        """Generator must not use built-in hash() of strings (PYTHONHASHSEED-
        dependent). Scan the source for the offending pattern."""
        src = (_PROJ / "src" / "generate.py").read_text(encoding="utf-8")
        # Permit hashlib usage; forbid raw hash(...) calls that feed RNG or
        # set construction. We assert the literal `hash(` pattern is absent
        # outside of comments.
        for line in src.splitlines():
            code = line.split("#", 1)[0]
            self.assertNotIn("hash(", code,
                             f"built-in hash() is not seed-stable: {line.strip()}")


# ---------------------------------------------------------------------------
# Negative fixtures: mutate REAL outputs, run the SAME validator, assert code
# ---------------------------------------------------------------------------
class TestNegativeFixtures(unittest.TestCase):
    """Each test mutates a real generated artifact, runs the production
    validator, and asserts a stable rejection code."""

    @classmethod
    def setUpClass(cls):
        cls.canonical = Path(tempfile.mkdtemp(prefix="umgd_neg_src_"))
        gen.generate(cls.canonical)

    def _fresh_copy(self) -> Path:
        dst = Path(tempfile.mkdtemp(prefix="umgd_neg_mut_"))
        _copy_artifacts(self.canonical, dst)
        return dst

    def _run(self, out: Path) -> str:
        try:
            val.validate_artifacts(out)
        except val.ValidationError as e:
            return e.code
        self.fail("validator passed on a mutated artifact; expected a rejection code")

    # --- city model mutations ---
    def test_neg_city_count_too_few(self):
        out = self._fresh_copy()
        rows = _load_csv(out / "city_model.csv")
        _write_csv(out / "city_model.csv", rows[:-1])
        self.assertEqual(self._run(out), "CITY_COUNT_MISMATCH")

    def test_neg_duplicate_city_key(self):
        out = self._fresh_copy()
        rows = _load_csv(out / "city_model.csv")
        rows[1] = {**rows[1], "city_id": rows[0]["city_id"],
                   "city_key": rows[0]["city_key"]}
        _write_csv(out / "city_model.csv", rows)
        self.assertEqual(self._run(out), "CITY_KEY_NOT_UNIQUE")

    def test_neg_modal_total_out_of_band(self):
        out = self._fresh_copy()
        rows = _load_csv(out / "city_model.csv")
        rows[0]["rail_share"] = "5.0"  # blows the total past 1.02
        _write_csv(out / "city_model.csv", rows)
        self.assertEqual(self._run(out), "MODAL_TOTAL_OUT_OF_BAND")

    def test_neg_actual_not_rail_bus_ferry(self):
        out = self._fresh_copy()
        rows = _load_csv(out / "city_model.csv")
        rows[0]["actual_association"] = "0.999"  # inconsistent with modal shares
        _write_csv(out / "city_model.csv", rows)
        self.assertEqual(self._run(out), "ACTUAL_NOT_RAIL_BUS_FERRY")

    def test_neg_gap_signed_arithmetic(self):
        out = self._fresh_copy()
        rows = _load_csv(out / "city_model.csv")
        rows[0]["gap_signed"] = "0.5"  # != actual - expected
        _write_csv(out / "city_model.csv", rows)
        self.assertEqual(self._run(out), "GAP_SIGNED_ARITHMETIC")

    def test_neg_default_focus_not_median(self):
        """Flip the is_default_focus flag onto the wrong city."""
        out = self._fresh_copy()
        rows = _load_csv(out / "city_model.csv")
        for r in rows:
            r["is_default_focus"] = "False"
        rows[0]["is_default_focus"] = "True"  # arbitrary wrong city
        _write_csv(out / "city_model.csv", rows)
        self.assertEqual(self._run(out), "DEFAULT_FOCUS_NOT_MEDIAN")

    def test_neg_two_default_focus(self):
        out = self._fresh_copy()
        rows = _load_csv(out / "city_model.csv")
        rows[1]["is_default_focus"] = "True"
        _write_csv(out / "city_model.csv", rows)
        self.assertEqual(self._run(out), "DEFAULT_FOCUS_COUNT")

    def test_neg_review_set_mismatch(self):
        """Flip a review-case label to 'context' while leaving all gap values
        intact. The stored flag then disagrees with the recomputed review
        membership; arithmetic, default-focus median, and threshold are all
        unchanged, so the validator reaches the review-set check."""
        out = self._fresh_copy()
        rows = _load_csv(out / "city_model.csv")
        target = next(r for r in rows if r["review_flag"] == "review case")
        target["review_flag"] = "context"  # gap_absolute unchanged
        _write_csv(out / "city_model.csv", rows)
        self.assertEqual(self._run(out), "REVIEW_SET_MISMATCH")

    def test_neg_review_set_emptied(self):
        out = self._fresh_copy()
        rows = _load_csv(out / "city_model.csv")
        for r in rows:
            r["review_flag"] = "context"
        _write_csv(out / "city_model.csv", rows)
        self.assertEqual(self._run(out), "REVIEW_SET_MISMATCH")

    # --- presentation mutations ---
    def test_neg_presentation_key_duplicate(self):
        out = self._fresh_copy()
        rows = _load_csv(out / "dashboard_presentation.csv")
        rows[1] = {**rows[1], "presentation_row_key": rows[0]["presentation_row_key"]}
        _write_csv(out / "dashboard_presentation.csv", rows)
        self.assertEqual(self._run(out), "PRESENTATION_KEY_DUPLICATE")

    def test_neg_score_panel_coordinate_collision(self):
        """Force two score rows onto the same coordinate."""
        out = self._fresh_copy()
        rows = _load_csv(out / "dashboard_presentation.csv")
        score_rows = [r for r in rows if r["panel"] == "score"]
        score_rows[1]["actual_association"] = score_rows[0]["actual_association"]
        score_rows[1]["expected_association"] = score_rows[0]["expected_association"]
        _write_csv(out / "dashboard_presentation.csv", rows)
        self.assertEqual(self._run(out), "SCORE_PANEL_COORDINATE_COLLISION")

    def test_neg_equality_axis_not_fixed(self):
        out = self._fresh_copy()
        rows = _load_csv(out / "dashboard_presentation.csv")
        rows[0]["equality_axis_max"] = "1.5"
        _write_csv(out / "dashboard_presentation.csv", rows)
        self.assertEqual(self._run(out), "EQUALITY_AXIS_NOT_FIXED")

    # --- receipt mutations ---
    def test_neg_predictor_denylist_violation(self):
        out = self._fresh_copy()
        with open(out / "model_receipt.json", encoding="utf-8") as f:
            receipt = json.load(f)
        receipt["feature_set"].append("actual_association")
        with open(out / "model_receipt.json", "w", encoding="utf-8") as f:
            json.dump(receipt, f, indent=2)
        self.assertEqual(self._run(out), "PREDICTOR_DENYLIST_VIOLATION")

    def test_neg_receipt_scaler_claims_fold(self):
        out = self._fresh_copy()
        with open(out / "model_receipt.json", encoding="utf-8") as f:
            receipt = json.load(f)
        receipt["scaler_method"] = "log1p then z-score (training-fold mean/std only)"
        with open(out / "model_receipt.json", "w", encoding="utf-8") as f:
            json.dump(receipt, f, indent=2)
        self.assertEqual(self._run(out), "RECEIPT_SCALER_FOLD_CLAIM")

    def test_neg_receipt_default_focus_drift(self):
        out = self._fresh_copy()
        with open(out / "model_receipt.json", encoding="utf-8") as f:
            receipt = json.load(f)
        receipt["default_focus_city"] = "CTY-999"
        with open(out / "model_receipt.json", "w", encoding="utf-8") as f:
            json.dump(receipt, f, indent=2)
        self.assertEqual(self._run(out), "RECEIPT_DEFAULT_FOCUS")

    # --- robustness mutations ---
    def test_neg_robustness_jaccard_mismatch(self):
        out = self._fresh_copy()
        rows = _load_csv(out / "robustness_group.csv")
        rows[1]["jaccard_value"] = "0.111111"  # does not recompute
        _write_csv(out / "robustness_group.csv", rows)
        self.assertEqual(self._run(out), "ROBUSTNESS_JACCARD_MISMATCH")

    def test_neg_robustness_mae_mismatch(self):
        out = self._fresh_copy()
        rows = _load_csv(out / "robustness_group.csv")
        rows[2]["mae"] = "0.777777"  # does not recompute from data
        _write_csv(out / "robustness_group.csv", rows)
        self.assertEqual(self._run(out), "ROBUSTNESS_MAE_MISMATCH")

    def test_neg_robustness_baseline_not_first(self):
        out = self._fresh_copy()
        rows = _load_csv(out / "robustness_group.csv")
        # Swap baseline to the end
        baseline = rows.pop(0)
        rows.append(baseline)
        _write_csv(out / "robustness_group.csv", rows)
        self.assertEqual(self._run(out), "ROBUSTNESS_BASELINE_FIRST")

    def test_neg_robustness_variant_count(self):
        out = self._fresh_copy()
        rows = _load_csv(out / "robustness_group.csv")
        _write_csv(out / "robustness_group.csv", rows[:-1])
        self.assertEqual(self._run(out), "ROBUSTNESS_VARIANT_COUNT")

    def test_neg_robustness_method_label_refit_claim(self):
        """A feature-drop row's visible `method` label must not claim a refit.

        The structured metadata says the named coefficient is set to 0.0 and
        the intercept is unchanged; no fitting occurs. Mutating the label to
        a refit claim must fail the fail-closed consistency check with a
        stable code, guarding the regression fixed in REV1."""
        out = self._fresh_copy()
        rows = _load_csv(out / "robustness_group.csv")
        target = next(r for r in rows if r["variant_label"] == "drop_density")
        # Structured metadata left intact; only the visible label is poisoned.
        target["method"] = ("drop feature density and refit intercept to "
                            "zero coefficient on it")
        _write_csv(out / "robustness_group.csv", rows)
        self.assertEqual(self._run(out),
                         "ROBUSTNESS_METHOD_LABEL_CONTRADICTION")

    # --- ranking language mutation ---
    def test_neg_banned_ranking_language(self):
        out = self._fresh_copy()
        rows = _load_csv(out / "city_model.csv")
        rows[0]["safe_copy"] = "this dashboard predicts outcomes and is the best city"
        _write_csv(out / "city_model.csv", rows)
        self.assertEqual(self._run(out), "BANNED_RANKING_LANGUAGE")


if __name__ == "__main__":
    unittest.main(verbosity=2)
