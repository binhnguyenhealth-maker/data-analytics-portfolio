# Tableau Workbook Review Guide

This repository contains three independent Tableau portfolio projects built
with deterministic synthetic data. They are prototypes—not client-commissioned
or production deployments.

## Five-minute review path

1. Start on the repository landing page and scan the three clean presentation
   previews.
2. Open the project whose decision is most relevant to you.
3. Download its `.twbx` package from the project README.
4. Open it in Tableau Desktop 2026.2 or later. No account, server, or cloud
   publication is required for local review.
5. Use the controls described below, then return to the documented landing
   state.

## Project 1 — Data Quality Refresh Command Center

Decision: which source-quality issue must be resolved first, and what evidence
supports that priority?

- Inspect the 27-row inventory and six-item governed queue.
- Follow an issue into its lineage evidence.
- Check the same-operation remediation readback.
- Change the display-detail control; row counts should remain unchanged.

[Project notes](../projects/data-quality-command-center/) ·
[Download workbook](../projects/data-quality-command-center/tableau/Data_Quality_Refresh_Command_Center_SYNTHETIC_PORTFOLIO.twbx)

## Project 2 — Urban Mobility Gap Diagnostic

Decision: which fictional cities merit contextual follow-up after their
observed association differs from a frozen benchmark?

- Inspect the 24-mark observed-versus-expected scatter.
- Compare it with the equality guide on the same fixed axes.
- Filter the stored results; the benchmark must not refit or rerank.
- Review city context, modal composition, predictors, and warnings.

[Project notes](../projects/urban-mobility-gap/) ·
[Download workbook](../projects/urban-mobility-gap/tableau/Urban_Mobility_Gap_Diagnostic_SYNTHETIC_PORTFOLIO.twbx)

## Project 3 — Explainable Peer Scenario Explorer

Decision: which five structural peers fit a focal fictional city, why were they
selected, and how sensitive is that set to reasonable specifications?

- Change the focal-city and scenario controls.
- Inspect the signed feature-level explanations.
- Compare the closest and country-diversified scenarios.
- Confirm that context appears only after matching and never affects selection.
- Reset to the documented baseline state.

[Project notes](../projects/peer-scenario-explorer/) ·
[Download workbook](../projects/peer-scenario-explorer/tableau/Peer_Scenario_Stability_Explorer_SYNTHETIC_PORTFOLIO.twbx)

## Reproducibility

```sh
python3 -m pip install -r requirements.txt
python3 shared/validation/regenerate_all.py
python3 shared/validation/build_previews.py
python3 shared/validation/run_all_tests.py
```

The public editions intentionally use smaller fictional cohorts so reviewers
can reproduce the complete payload quickly. The same decision logic, data
contracts, interaction patterns, and negative-case checks are preserved.

## Evidence boundary

The workbooks demonstrate local Tableau authoring, data modeling, calculated
fields, parameters, filters, interaction design, refresh logic, and analytical
QA. They do not demonstrate client acceptance, hosted administration, row-level
security, scheduled production refresh, training delivery, or measured client
impact.
