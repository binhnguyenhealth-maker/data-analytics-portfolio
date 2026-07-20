# VALIDATION.md

**SYNTHETIC DATA / INDEPENDENT PORTFOLIO PROJECT**

Validation record for the `data-analytics-portfolio` staging repository. This
record is produced by the Wave 2 shared-release reconciliation and is a
required release-control artifact: the structure validator
(`shared/validation/test_structure_scan.py`) fails closed if this file is
absent, empty, or a stub. It records the exact commands run, their results,
source/output boundaries, the native-Tableau status, unresolved
license/publication gates, and the hashes of every generated and tracked
file at the time of this validation.

- Audit author: `builtin:zai-coding-plan/GLM-5.2`
- Reasoning level: `UNKNOWN - NOT EXPOSED`
- Execution surface: Desktop LLM (ZCode)
- Validation date: 2026-07-17 (America/New_York)
- Repository state: local staging; **NOT** a Git repository; no network.

## 1. Environment

- Working directory: the staging tree root (this repository). The absolute
  local path is intentionally omitted from this record so the disclosure
  scan stays clean; it is the staging directory named in the Wave 2 ZCP.
- Python: CPython 3.13.7 (Frameworks)
- Runtime dependency: `numpy 2.4.2` (see `requirements.txt`)
- All commands use `PYTHONDONTWRITEBYTECODE=1 python3 -B` and only the Python
  standard library plus `numpy`. No network, no package install, no Git.

## 2. Commands and results

### 2.1 Canonical regeneration

```
PYTHONDONTWRITEBYTECODE=1 python3 -B shared/validation/regenerate_all.py
```

Result: exit 0. Stable per-project `snapshot_id` values (unchanged across
runs and across `PYTHONHASHSEED` values):

| Project | snapshot_id | Row counts |
|---|---|---|
| data-quality-command-center | `syn-data-quality-command-center-fe8779ad4e6c` | source_inventory=27, schema_unit_freshness_issue=6, issue_lineage=25, refresh_priority_queue=6, remediation_receipt=1, refresh_control=1 |
| urban-mobility-gap | `syn-urban-mobility-gap-fe8779ad4e6c` | city_model=24, dashboard_presentation=408, robustness_group=8, robustness_summary=2, geographic_warnings=16 |
| peer-scenario-explorer | `syn-peer-scenario-explorer-fe8779ad4e6c` | city_roster=30, peer_result=450, peer_explanation=1650, stability=510, stability_summary=30, variant_metadata=17, coverage_exposure=15, context_comparison=540, peer_scenario_surface=2760 |

### 2.2 Canonical validators

| Project | Command | Result |
|---|---|---|
| DQCC | `python3 -B projects/data-quality-command-center/src/validate.py` (via module load) | `status=PASS`, `typed_duplicate_count=0`, `schema_invariant=true`, `manifest_complete=true` |
| UMG | `python3 -B projects/urban-mobility-gap/src/umgd_validate.py` | `VALID`, n_cities=24, default_focus=CTY-022, review_set={CTY-010, CTY-017, CTY-020}, review_threshold=0.450272 |
| PSE | `python3 -B projects/peer-scenario-explorer/src/validator.py projects/peer-scenario-explorer/data/synthetic` | `{"ok": true, "n_failures": 0, "n_checks": 13183, "codes": [], "failures": []}` |

### 2.3 Project test suites

| Project | Command | Result |
|---|---|---|
| DQCC | `python3 -B -m unittest projects.data-quality-command-center.tests.test_dqcc` | **Ran 41 tests, OK** (15 positive + 22 prior negative + 3 coordinated negative + 1 cross-process determinism) |
| UMG | `python3 -B -m unittest projects.urban-mobility-gap.tests.test_umgd` | **Ran 36 tests, OK** (15 positive + 21 negative, incl. `ROBUSTNESS_METHOD_LABEL_CONTRADICTION`) |
| PSE | `python3 -B -m unittest projects.peer-scenario-explorer.tests.test_pse` | **Ran 51 tests, OK** (26 positive + 25 mutation, incl. `FOCAL_COVERAGE_MISSING`) |

**Project total: 128/128 PASS.**

### 2.4 Shared disclosure scan

```
PYTHONDONTWRITEBYTECODE=1 python3 -B shared/validation/scan.py
```

Result: **CLEAN**. The Wave 1 known false positive (a 10-digit decimal run
embedded inside the 64-char hex SHA-256 digest stored in DQCC
`refresh_control.source_manifest_sha256`) is resolved by a precise,
evidence-backed suppression: a phone match that is a substring of a longer
all-hex span of 40+ characters is treated as a hash fragment, not a phone.
The exact digit run is not reproduced here so this record self-scans clean;
it is documented in the Wave 1 DQCC REV1 result and reproduced from
fragments in the disclosure negative fixtures. A standalone real phone
number is still caught (asserted by negative fixtures). All forbidden-marker
categories (org, private path, access material) are now matched
**case-insensitively**; mixed-case variants are caught (asserted by negative
fixtures).

### 2.5 Shared structure scan

```
PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest shared.validation.test_structure_scan
```

Result: **11 tests, OK.** Enforces: no forbidden extensions, no file >10 MiB,
no symlinks, no unexpected hidden files, required top-level files present,
required per-project README+generator present, required docs present,
**`validation/VALIDATION.md` exists and is non-empty (fail closed)**,
**`validation/INCLUDED_FILES_SHA256.md` exists, is well-formed, every listed
hash verifies against current bytes (staleness fail-closed), and its file set
matches the current tracked-file set (self-consistency fail-closed)**.

### 2.6 Shared cross-process determinism

```
PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest shared.validation.test_determinism
```

Result: **3 tests, OK.** Each generator runs in fresh interpreter subprocesses
under `PYTHONHASHSEED` = **1, 2, 7** (three distinct seeds, exceeding the ZCP
minimum). Complete output-byte manifests (relative path -> SHA-256) are
byte-identical across all three seeds for every project, and equal the
canonical on-disk output directory (staleness guard). A static check forbids
the built-in `hash()` anywhere in a generator or shared helper.

Combined output-byte manifest per seed (SHA-256 over `(relpath || sha256)`
of every generated file, all three projects):

| PYTHONHASHSEED | combined manifest |
|---|---|
| 1 | `0289d52ab4782a371169a4ec57ece43f199f81a1d4277a58ef00dd20d269747d` |
| 2 | `0289d52ab4782a371169a4ec57ece43f199f81a1d4277a58ef00dd20d269747d` |
| 7 | `0289d52ab4782a371169a4ec57ece43f199f81a1d4277a58ef00dd20d269747d` |

Per-project output-byte manifests (identical across all three seeds):

| Project | files | manifest SHA-256 |
|---|---:|---|
| data-quality-command-center | 12 | `ac58db55059bc61b335d2d7a84bdc6a266f1d2494626806adc4a1f282bef11b8` |
| urban-mobility-gap | 6 | `3543afd76b85f0082d9a9be1e9a976d5bcdbf26b2fa1cf6434dd9da42400ebfa` |
| peer-scenario-explorer | 10 | `e05cec8473ed7b49fff3e1e748bdf811a3bc5770a48b501cce13f28b5ee54be6` |

### 2.7 Aggregate suite

```
PYTHONDONTWRITEBYTECODE=1 python3 -B shared/validation/run_all_tests.py
```

Result (after this record and the rebuilt manifest exist): **all tests OK.**
The pre-Wave-2 baseline was 144 tests, 142 PASS, 2 FAIL (the isolated
SHA-hex phone false positive). After Wave 2 the suite is 164 tests
(+20 new boundary/determinism/structure negative fixtures), all green.

## 3. Source / output boundaries

- **Canonical editable sources**: `shared/synthetic/__init__.py`,
  `shared/validation/*.py`, each `projects/<p>/src/*.py` generator and
  validator, each `projects/<p>/tests/test_*.py`, root `README.md`,
  `SOURCE_AND_USAGE_POLICY.md`, `LICENSE_PENDING.md`, `.gitignore`,
  `docs/*.md`. These are the files tracked by the inclusion manifest.
- **Generated outputs** (gitignored, reproducible): every file under
  `projects/<p>/data/synthetic/`. These are NOT in the inclusion manifest;
  they are tracked by per-project receipts and the determinism manifests
  above. Their SHA-256 inventory is in section 5.
- **Cooperative locks**: `STAGING_LOCK.md` (if present) is gitignored and
  excluded from the manifest; no lock is currently held on this tree.
- No file under this tree is a symlink, exceeds 10 MiB, or has a forbidden
  extension (`.twbx/.twb/.hyper/.pdf/.png/.xlsx/.docx/.zip/...`).

## 4. Native Tableau status

`HELD`. No native Tableau authoring, workbook, extract, screenshot,
performance recording, or Optimizer evidence exists in this tree, and none
is claimed. `docs/FUTURE_TABLEAU_REBUILD_PLAN.md` is a **contract** for a
later, dated rebuild step, not a Tableau artifact. The DQCC plan's worksheet
readback counts are pinned to the current canonical build receipt
(`source_inventory=27, schema_unit_freshness_issue=6,
refresh_priority_queue=6, issue_lineage=25, remediation_receipt=1`) and
instruct the rebuild author to re-derive from `build_receipt.json` if a
regeneration changes them. Native Tableau work remains blocked until an
independent review accepts this Wave 2 result.

## 5. Generated output SHA-256 inventory

DQCC (12 files):

```
9ebf4b89ee4a193d0b09e391684e2318b8567689857ef2682c9aeb2780fbd239  _source_workbooks/reference_masterlist.csv
11f86ba1e1b3e47ba9905b34c8eb269b28231975334076bd9b15f7894ad9b62c  _source_workbooks/source_pack_01.csv
73d30d3d9dda4badd795cbb56dd2a64fa73eb78d756c94213d4090b8866a68ff  _source_workbooks/source_pack_02.csv
839776c57c03783c3b465bef6aa29bea60d84213e894774d9b2da50089854dc8  _source_workbooks/source_pack_03.csv
14483fbbe5ef63be5e5b35e6e2cd46347c25c49eb513241f4822d8d63bc54936  _source_workbooks/source_pack_04.csv
51feed4e01a667eccf3205318a4cc0938e416675234ccbdce4c1922a8960ba3e  build_receipt.json
28d7492002357629d0cc9a778d50569132bd248c53c9c8244aaf98d92a63e105  issue_lineage.csv
ecf4d8c4c3d45727e7490a7a314e9c1dccd0360234f8ca473649af37d47e8078  refresh_control.csv
a2bb30de73bc99125a147c506c0c5cfd5bce185982ef4802396422f67d60871d  refresh_priority_queue.csv
da60b68be6541453bd39b22e520cac9cdf6304e05aaedbfc44400173e8c81d28  remediation_receipt.csv
2ac98ffefa2ea3dbc12830f8e4c56dd61dea13a59f65ac454c1d235240d95d3f  schema_unit_freshness_issue.csv
c60806402b598817f7e87a316fdc458ab9d77dd746995df6bab17c9a861927f3  source_inventory.csv
```

UMG (6 files):

```
d790a68032cfa51e307ba0eb411bb67f1858f5653622965134d95312aca29f33  city_model.csv
a11456a9c5cb66bf86f3a304ed319d4f7436f8737ad9b673de6ecc70fcb79597  dashboard_presentation.csv
1e0e4d8bd5156d6ff8ef282fc2fa5456c248430363d4dd34780edee346377fc1  geographic_warnings.csv
89c03d943a14c5e02c4bc42439386cc0eca7644de3bfcdc1f07bda69159232c3  model_receipt.json
703b908aced8a38e0df40a1f0a7b09963b95f46aa6ea875e7734049a36f12cfa  robustness_group.csv
fe8fc48eb7b31f340f98f5b3c0909abb3987d351d04a5c5b17ba9da8f9d652dd  robustness_summary.csv
```

PSE (10 files):

```
d22964c7d7b38076a3b8c7f99c83160191054393751ece4209a973551c4249b1  city_roster.csv
4f0c052ece316cb6cf216a3ebff7cc9133154a79fc72e0e91727ef5f57883b85  context_comparison.csv
c3635eeb260455f98bd52a2546fa87ab21a5268216c0dec23ea39aa56ad7aa6e  coverage_exposure.csv
3042147e17acf6f939b49a8a02dfc33f71de1dca8232e903e382743f1bd28934  method_receipt.json
9f99d31548ecd4ebdcfbfb93290c865f32738773dfe749ef36c41861d2e9f193  peer_explanation.csv
3db3f91cf39f060dfd277a3d79a42388ab31fd6a6e1eede91168311d132518f3  peer_result.csv
99f8fe151905a97816e7a8303d7ad975ef45f8d161920cdf8ab87b780a02d55e  peer_scenario_surface.csv
88ff83e2737f73c9f7067cfda01439f1c7cea0127a8af070f8c9c99c5c658f57  stability.csv
31685c2b63af199ab460f1b3accd29deddfda3c21591616cea5b2ebc7b8e8440  stability_summary.csv
baf8bc72339902305b8856071d3a31c3af73ff280102e86da0d798d50247769a  variant_metadata.csv
```

## 6. Inclusion manifest

`validation/INCLUDED_FILES_SHA256.md` is regenerated as the **last** step
after every other byte stabilized, and self-verifies (every listed hash
matches the current on-disk bytes, and the file set exactly matches the
current tracked-file set). See that file for the per-entry SHA-256 table.

## 7. Disclosure and protected boundary

- The disclosure scan is case-insensitive across all forbidden-marker
  categories and reports CLEAN on the full tree (section 2.4).
- The scanner source self-scans CLEAN (asserted by a dedicated test), as
  does this validation record (asserted by the same scan).
- The three protected manifests under the separate, out-of-tree candidate
  build area named in the Wave 2 ZCP were reverified **without
  modification**: Candidate 1 `66/66` entries verify, Candidate 3 `111/111`
  entries verify, and Candidate 2's accepted recovery boundary manifest
  (`..._SUCCESS_ARTIFACT_MANIFEST_SHA256.md`) `15/15` entries verify. The
  older 222-entry Candidate 2 historical manifest is preserved unchanged;
  its 3 hash differences are the expected pre-recovery -> recovery delta on
  the workbook, postprocessor, and package validator, all of which match
  the recovery boundary manifest.
- No file outside this staging tree was modified. The out-of-tree protected
  manifests were read once, read-only, for hash verification only.

## 8. Unresolved gates (fail-closed)

- **Native Tableau rebuild**: HELD. No native evidence exists or is claimed.
- **License**: PENDING. `LICENSE_PENDING.md` remains in force; no open-source
  license has been chosen or added.
- **Git / publication / submission**: not performed. This tree is not a Git
  repository; no remote, commit, push, upload, or submission has occurred.
- **Independent review**: this Wave 2 result requires an independent Codex
  review before native Tableau work may begin.

## 9. Regeneration instructions

```
# 1. Regenerate every synthetic dataset (deterministic; safe to re-run)
python3 shared/validation/regenerate_all.py
# 2. Run every project test and the shared disclosure/structure/determinism validators
python3 shared/validation/run_all_tests.py
# 3. Recompute the inclusion manifest (SHA-256 of every tracked file)
python3 shared/validation/build_manifest.py
```

The validation record (`validation/VALIDATION.md`) and the inclusion manifest
(`validation/INCLUDED_FILES_SHA256.md`) must both exist and verify, or the
structure scan fails closed.
