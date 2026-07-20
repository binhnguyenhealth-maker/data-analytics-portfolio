# FUTURE_TABLEAU_REBUILD_PLAN.md

**SYNTHETIC DATA / INDEPENDENT PORTFOLIO PROJECT**

This document records the native Tableau build contract used for each of the
three synthetic case studies. The contract has been implemented: packaged
workbooks and native previews now live under each project's `tableau/` and
`images/` directories. The synthetic CSV/JSON outputs in
`projects/<p>/data/synthetic/` remain the canonical inputs.

Irreducible authoring and final reopen checks were completed in Tableau
Desktop 2026.2 Free Edition. The release validator independently checks the
package inventory, workbook XML, embedded CSV bytes, dashboards, worksheets,
fixed viewport, disclosures, and previews.

## Common rebuild requirements (all projects)

- **Source**: Tableau Desktop 2026.2 or later. Free Edition is acceptable;
  record the exact edition and any disabled native controls truthfully.
- **Data source**: connect to the project's `data/synthetic/` CSVs. Do not
  re-host or copy the data out of the staging tree for the rebuild.
- **Disclosure**: every dashboard carries the visible label
  `SYNTHETIC DATA / INDEPENDENT PORTFOLIO PROJECT`.
- **Accessibility**: status conveyed as text, not color alone. Fixed viewport.
- **Performance**: run native Performance Recording; record event counts,
  p95, and any event > 0.5s. Disclose the Optimizer state truthfully
  (enabled or disabled by edition); do not claim an Optimizer run that did
  not occur.
- **Screenshots required**: ordered PNG captures of every worksheet and the
  assembled dashboard, plus a final reopen. Hashes recorded.
- **Native acceptance tests**: enumerated per project below. Each must be
  run and recorded with the exact pass/fail evidence.

---

## Project 1: Data Quality Command Center

### Inputs
- `projects/data-quality-command-center/data/synthetic/source_inventory.csv`
- `projects/data-quality-command-center/data/synthetic/schema_unit_freshness_issue.csv`
- `projects/data-quality-command-center/data/synthetic/issue_lineage.csv`
- `projects/data-quality-command-center/data/synthetic/refresh_priority_queue.csv`
- `projects/data-quality-command-center/data/synthetic/remediation_receipt.csv`
- `projects/data-quality-command-center/data/synthetic/refresh_control.csv`

### Relationship (logical model)
- One physical relationship: `schema_unit_freshness_issue.issue_id` (left,
  `unique-key="true"`) one-to-many `issue_lineage.issue_id` (right).
- The dashboard surface reads from `refresh_priority_queue` joined to
  `issue_lineage` on `issue_id`.

### Worksheets (exact titles)
1. `01 Inventory & Reconciliation`
2. `02 Refresh Priority Queue`
3. `03 Lineage Evidence Detail`
4. `04 Remediation Receipt`

### Dashboard
- One dashboard, fixed size `1366 x 768`.
- Exactly four worksheet filters: `show_inventory`, `show_queue`,
  `show_lineage`, `show_remediation`. Each governs the visibility of its
  matching worksheet.
- No cross-sheet dashboard actions in this MVP cut.

### Parameter (display-only)
- Caption: `Display Detail Level`. Members: `Summary`, `Full`. Default
  `Summary`. Two required calculations: `Draft Status Label` and
  `Display Detail Selector`. The parameter changes display text only.

### Native acceptance tests
1. Reopen the workbook without a save/recovery prompt.
2. Read back the four worksheet counts and reconcile them to the current
   `build_receipt.json` `row_counts`:
   - `01 Inventory & Reconciliation`: `source_inventory.csv` = **27** rows
     (and the governed `schema_unit_freshness_issue.csv` = **6** issue rows).
   - `02 Refresh Priority Queue`: `refresh_priority_queue.csv` = **6** rows.
   - `03 Lineage Evidence Detail`: `issue_lineage.csv` = **25** lineage rows.
   - `04 Remediation Receipt`: `remediation_receipt.csv` = **1** row.
   These counts are pinned from the current canonical build receipt
   (`snapshot_id` `syn-data-quality-command-center-fe8779ad4e6c`,
   `rule_version` `rules-v1`); if a regeneration changes them, re-derive the
   expected readback from the new `build_receipt.json` `row_counts` rather
   than from the numbers printed here.
3. Assert zero unmatched left endpoints on the `issue_id` relationship.
4. Assert the `Display Detail Level` parameter changes display text only;
   underlying row counts and hashes are unchanged.
5. Performance Recording: record event count, p95, and any event > 0.5s.
6. Record Optimizer state truthfully (enabled or disabled by edition).

---

## Project 2: Urban Mobility Gap Diagnostic

### Inputs
- `projects/urban-mobility-gap/data/synthetic/city_model.csv`
- `projects/urban-mobility-gap/data/synthetic/dashboard_presentation.csv`
- `projects/urban-mobility-gap/data/synthetic/robustness_group.csv`
- `projects/urban-mobility-gap/data/synthetic/robustness_summary.csv`
- `projects/urban-mobility-gap/data/synthetic/geographic_warnings.csv`
- `projects/urban-mobility-gap/data/synthetic/model_receipt.json`

### Data source model
- **Single-root**: the dashboard reads only from `dashboard_presentation.csv`
  (the pre-joined presentation table). No relationships across physical
  tables on the surface.

### Worksheets (exact titles)
1. `01 Actual vs Expected Association`
2. `01A Equality Guide (same 0-100% axes)`
3. `02A City Summary`
4. `02B Modal Composition`
5. `02C Predictor Profile`
6. `02D Default Guidance and No-match`
7. `03A Robustness Summary`
8. `03B Geographic Warnings`

### Dashboard
- The scatter (`01`) is a native single-pane scatter. The equality guide
  (`01A`) is a **separate** `y = x` line on **identical fixed `[0, 1]` axes**.
  Do not hand-build a dual-axis chart.
- Parameter control is **not exposed**; a visible direction guide replaces it.
- Empty-state copy: "No cities match the current filters."

### Native acceptance tests
1. Scatter and equality guide share the exact same fixed `[0, 1]` axes.
2. Read back the mark count of the scatter; assert it equals the number of
   `city_key` rows in the presentation `score` panel.
3. Modal composition bars sum to ~100% per city (within `[98%, 102%]`).
4. Filters subset frozen scores only; refitting, rescaling, or reranking is
   prohibited.
5. Performance Recording and Optimizer state recorded.
6. No ranking/league-table language appears in any visible copy.

---

## Project 3: Explainable Urban Peer Scenario Stability Explorer

### Inputs
- `projects/peer-scenario-explorer/data/synthetic/city_roster.csv`
- `projects/peer-scenario-explorer/data/synthetic/peer_result.csv`
- `projects/peer-scenario-explorer/data/synthetic/peer_explanation.csv`
- `projects/peer-scenario-explorer/data/synthetic/stability.csv`
- `projects/peer-scenario-explorer/data/synthetic/stability_summary.csv`
- `projects/peer-scenario-explorer/data/synthetic/coverage_exposure.csv`
- `projects/peer-scenario-explorer/data/synthetic/context_comparison.csv`
- `projects/peer-scenario-explorer/data/synthetic/peer_scenario_surface.csv`
- `projects/peer-scenario-explorer/data/synthetic/method_receipt.json`

### Data source model
- **Single-root**: the dashboard reads only from
  `peer_scenario_surface.csv`. The `surface` column discriminates the four
  analytical surfaces.

### Worksheets (exact titles)
1. `01 Focal City + Five Peers`
2. `02 Why This Peer`
3. `03 Closest vs Diversified`
4. `04 Context After Matching`

### Dashboard
- Two parameter controls: `Focal City` (single-select; default = landing
  city from `method_receipt.json`) and `Peer Scenario`
  (`baseline` / `diversified` / `core`; default `baseline`).
- One dashboard. Reset returns to `landing_city x baseline`.

### Non-map fallback (design invariant)
- **No coordinate source.** Do not add latitude, longitude, or place fields.
- Map worksheets are disallowed. Present the peer roster as a table.

### Native acceptance tests
1. Default state opens to `landing_city x baseline` with exactly 6 marks on
   `01 Focal City + Five Peers` (1 focal + 5 peers).
2. Change `Peer Scenario` to `diversified`; assert the diversified set
   respects the two-per-country cap.
3. Change `Focal City`; assert `02 Why This Peer` updates with the
   per-feature signed contributions for the new focal.
4. Assert `04 Context After Matching` shows the post-hoc measure only
   (no fitting artifact).
5. Reset returns to `landing_city x baseline`.
6. Assert no map worksheet and no coordinate field anywhere in the workbook.
7. Performance Recording and Optimizer state recorded.
8. No ranking language ("best", "worst", "objective", "true peer",
   "performance ranking") in any visible copy.

---

## Rebuild record template

For each project, the rebuild author produces a record containing:

- Tableau edition and exact build number.
- Workbook Optimizer state (enabled or disabled; if disabled, the edition
  limitation is recorded, not worked around).
- Performance Recording summary (event count, p95, max actionable event).
- Ordered screenshot list with SHA-256 hashes.
- Native acceptance test results (one pass/fail per test above).
- Disclosure label visible on every dashboard.
- Confirmation that no production, client, or third-party data was introduced.

This record is the sole evidence of a completed native rebuild. The staging
tree's synthetic outputs and this plan are the contract; the rebuild is the
implementation.
