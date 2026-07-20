# Urban Mobility Gap Diagnostic

**SYNTHETIC DATA / INDEPENDENT PORTFOLIO PROJECT**

![Source-backed presentation preview](images/dashboard-preview.jpeg)

[Download the packaged Tableau workbook](tableau/Urban_Mobility_Gap_Diagnostic_SYNTHETIC_PORTFOLIO.twbx)

## Decision supported

Every review cycle, a mobility analyst uses this diagnostic to decide which
fictional cities merit contextual follow-up after their observed association
deviates from a frozen benchmark.

## Analytical boundary

This is a cross-sectional diagnostic, not a forecast, causal model, performance
ranking, or service-quality score. The benchmark is frozen before filtering;
dashboard controls subset the stored results and never refit, rescale, or
rerank them.

## Technical highlights

- 24 fictional cities represented by a single-root presentation table;
- actual-versus-expected scatter with a separately rendered equality guide;
- fixed 0–100% axes for direct visual comparison;
- modal-composition totals checked within 98–102% per city;
- standardized predictor profiles with transform labels;
- geographic and small-group warnings shown as context; and
- perturbation tests that preserve a disclosed review-set threshold.

## Tableau surfaces

- actual versus expected association
- equality reference on the same fixed axes
- selected-city summary
- modal composition
- predictor profile
- default and empty-state guidance
- robustness summary
- geographic warnings

## Rebuild and validate

```sh
python3 src/generate.py
python3 src/umgd_validate.py
python3 -m unittest tests/test_umgd.py
```

## Evidence boundary

All names, places, values, and model outputs are synthetic. The prototype
demonstrates analytical controls and Tableau interaction design; it does not
claim a real-world mobility finding, production deployment, or client result.
