# CG-MAISR-D4 CLOSEOUT

Status: **STOP_MAISR**
Date closed: 2026-07-17

## 1. Accepted result

```text
backtest_id            = bc3126d8554fceb7807dc5dd5f76cece
calibration_result      = STOP_MAISR
calibration_reason      = NO_SUPPORTED_SUBJECT_PACK
research_conclusion     = STOP_MAISR
next                     = KEEP_CURRENT_SH
```

## 2. Source-commit chain

Chain of D4/D4.1/D4.2 fix commits validated by the accepted backtest, ending
at HEAD:

```text
990a41e DIAG CG-MAISR-D0: direct multi-asset intraday stress classifier and router
e12229d FIX CG-MAISR-D0: accept multi-minute panel feeds and preserve EOA log budget
f9a758a FIX CG-MAISR-P1: strict candidate identity and minute-event dedup
be0d2dd FIX CG-MAISR-P1: classifier support gates and complete artifact export
e652403 FIX CG-MAISR-D2: evaluation-level forward labels and held-asset coverage
c8f6b6c DIAG CG-MAISR-D2: episode gates, valid classifier selection and conditional grid
eb076f5 FIX CG-MAISR-D2: ATR-normalized relative returns for defensive/rate labels
a6ff1f1 FIX CG-MAISR-D3: overlapping-window episodes and semantic stress labels
984c114 DIAG CG-MAISR-D3: final pack selection, classifier gate and conditional economics
09b6bcd FIX CG-MAISR-D3: restore known-window constants after edit
bc60c96 FIX CG-MAISR-D3: use imported _D2_PARK in symbol roles
6ff50f7 FIX CG-MAISR-D4: subject-aware classifier and clean calibration semantics
67df90d DIAG CG-MAISR-D4: real next-bar post-only overlay and final economics
a1a0bd8 FIX CG-MAISR-D4: restore minute bar finalize path for D4 raw harvest
c93f74f FIX CG-MAISR-D4.1: correct exposure denominators and complete calibration audit
fd7bb8d FIX CG-MAISR-D4.1: resolve arts NameError and complete known-window audit
610c4d4 FIX CG-MAISR-D4.1: correct pack_stats CSV column serialization
402e5e9 FIX CG-MAISR-D4.2: enforce artifact-valid final calibration gate
8b10961 FIX CG-MAISR-D4.2: stringify artifact validation self-row
70e9195 FIX CG-MAISR-D4.2: allow intentional NONE sentinel in artifact validation
```

Chain start: `990a41e` (CG-MAISR-D0 initial diagnostic).
Chain end / accepted HEAD: `70e9195` (final D4.2 fix), immediately preceded by
`8b10961` (D4.2 self-row stringify fix). The accepted backtest
`bc3126d8554fceb7807dc5dd5f76cece` was run against `70e9195`.

## 3. Gate results

```text
production_identity_gate = PASS
fixed165_gate            = PASS
artifact_validation_gate = PASS
```

Production identity (candidate-vs-production NAV/MaxDD/correlation
replay) and the fixed-165 execution contract (signal 09:45, capture
09:45, deferred execution 12:15, no duplicate/missed execution) both
passed on the accepted run. All required D4 calibration artifacts
(identity, symbol_roles, gold_continuity, distributions, subject_exposure,
pack_stats, monotonicity, stability, episode_summary, selected_episodes,
known_windows, classifiers, manifest) parsed, validated, and hashed
correctly (`d4_validate_csv_artifact`, `d4_validate_manifest_json`).

## 4. Subject exposure (why calibration stopped)

```text
subject                 = XLU (only symbol with usable subject-level exposure)
held_days_train_a       = 6
held_days_train_b       = 55
held_days_total         = 61
```

Per `d4_stability_subject`, subject-level (LOCAL/SECTOR) stability
requires `held_days_a >= 20` and `held_days_b >= 20` before a
subject-specific pack can be scored as supported. XLU is the *only*
symbol in the D4 sector/local universe with any measurable held-day
exposure across TRAIN_A (2012-2015) and TRAIN_B (2016-2018), and its
exposure (6 / 55 / 61 days) fails the TRAIN_A floor (6 < 20). No other
sector proxy symbol (XLE, XLB, XLV, DBC) or local-residual symbol
accumulated enough held days across both sub-periods to be evaluated at
all. Because no pack has a stable, sufficiently-exposed subject signal,
`d4_finalize_calibration_result(gates_ok=True, chosen_pack=None, ...)`
returns `STOP_MAISR / NO_SUPPORTED_SUBJECT_PACK` — this is a data-support
failure, not a code or artifact failure.

## 5. Final decision

```text
decision = STOP_MAISR
action   = KEEP_CURRENT_SH
```

CG-MAISR (subject/sector/local intraday stress classifier and routing
overlay) is **not supported** by the available data at any of the 12
raw packs (`D4_B{40,60,80}_BR{2,3}_L{50,75}`). The current SH
(defensive hedge) sleeve remains the production defensive mechanism.
No MAISR subject-routing overlay is enabled in production. This closes
the CG-MAISR-D0 through D4.2 research line.

## 6. Reusable components (kept for future work)

The following pure-Python building blocks proved correct and general
enough to reuse in follow-on research (e.g. CG-MACRO-A1) even though
the subject-level MAISR hypothesis itself is closed:

- **Minute-event dedup / candidate identity** (`cg_maisr_diag.py`,
  hardened in `f9a758a` FIX CG-MAISR-P1) — strict per-timestamp
  candidate identity to prevent duplicate minute-bar events.
- **D2 pending-label / evaluation-level forward labeling** — one
  60-minute-forward observation per eligible evaluation timestamp
  (`e652403` FIX CG-MAISR-D2), replacing the old day-keyed 5-day EOD
  label bug.
- **Gold continuity helper** (`d4_gold_continuity`) — single
  GLD-primary / GLDM-fallback observation, never double-counts
  GLD+GLDM in the same series.
- **Artifact validator** (`d4_validate_csv_artifact`,
  `d4_validate_distributions_csv`, `d4_validate_manifest_json`) — CSV
  header/row-count/required-nonblank/unique-key/None-NaN/placeholder
  gate via the `csv` module (never manual `split(',')`), plus
  deterministic manifest SHA-256 hashing (`d4_manifest_hash`).
- **54 classifier configs** (`cg_maisr_d2_labels._ALL_CFG` /
  `_clfid`) — `S1..S3 x AMIN{2,3} x BRTH{0.50,0.65,0.75} x H{0,1,2}`
  grid, with the H0/H1/H2 confirmation semantics
  (`d4_hmode_classify`) that keep unconfirmed H2 mapped to
  `UNCONFIRMED_NOISE` rather than a stress state.
- **Interval merge / episode builder** (`d4_merge_intervals`,
  `d4_build_episodes`) — overlap/touch merge with MAE=min,
  breadth=max aggregation.
- **Subject codec** (`d4_subject_codec` / `d4_codec_roundtrip`) —
  compact, collision-free symbol encoding.
- **Exposure-normalized stability tests** (`d4_stability_broad`,
  `d4_stability_subject`, `d4_stability_defensive`) — density ratios
  normalized by actual sub-period length/held-days rather than raw
  episode counts.
- **Dictionary-based cut-ceiling ledger** (`d4_cut_ceiling_apply`,
  `d4_cap_buy_qty`) — fixes the earlier `getattr`-on-dict bug for
  same-day reduce-only overlays.
- **Source-commit validator** (`d4_validate_source_commit`) — rejects
  empty/`local`/non-hex/non-40-char commit strings.
- **Router/persistence adjacency tables** (`_ROUTER_ADJ`,
  `_PERSIST_ADJ`) and their symmetry check
  (`d4_router_adj_symmetric`).

These are imported directly (not re-implemented) by
`cg_macro_a1_core.py` where semantics are identical.

## 7. Forbidden revival list

The following are **explicitly forbidden** to revive as part of any
future MAISR-adjacent or macro research (including CG-MACRO-A1) unless
the user explicitly requests a new, separately-scoped experiment:

- Do **not** loosen any D4 pack threshold (`B`, `br_count`, `local`,
  `resid`) to manufacture subject-pack support. The `NO_SUPPORTED_SUBJECT_PACK`
  conclusion must stand on the data as calibrated; do not curve-fit
  thresholds to reach `CALIBRATION_PASS`.
- Do **not** enable `spyg_sat_trade_enable` (SPYG satellite overlay) as
  a workaround for the closed MAISR subject-routing hypothesis.
- Do **not** add AVGO, MU, or NVDA as tradable holdings/subjects. They
  remain non-proxy placeholders only (`_D4_PROXY`); D4 already asserts
  no-self-proxy (`d4_assert_no_self_proxy`) and these three were never
  supported as subjects.
- Do **not** add any new data subscriptions to reach subject-pack
  support. The exposure ceiling (XLU-only, 61 held days total) is a
  structural data-availability limit, not a subscription gap to patch
  around.
- Do **not** restore rejected regime-timing diagnostics or the T3
  variant router (`cg_rt_t3_variant`, `_T3_VARIANTS`,
  `CG_RT_T3_VARIANT`, `CG_RT_SHADOW_`) under this closeout.
- Do **not** re-enable `rrx_trade_bridge_enable` or
  `dyn_alloc_c2n_trade_enable` as part of this closeout.
- Do **not** change W2/E2, SH, PanicScore, IDS, or any other frozen CG
  component while working on macro-only follow-on research.

## 8. Relationship to CG-MACRO-A1

**CG-MACRO-A1 is a new, separate hypothesis — it is not "MAISR D5".**

MAISR (CG-MAISR-D0 through D4.2) tested *subject-level* (per-symbol
sector/local) intraday stress classification and routing. It is closed
at `STOP_MAISR / NO_SUPPORTED_SUBJECT_PACK` because no individual
subject symbol (other than the insufficiently-exposed XLU) has enough
held-day history to support a subject-specific pack.

CG-MACRO-A1 only reuses the *macro-level* (broad/systemic/rate/
defensive) primitives that do not depend on subject-level exposure —
`d4_raw_flags`, `d4_priority_macro`, the interval/episode merge, the
manifest/artifact validators, the gold-continuity helper, and the 54
classifier configs applied at the macro (not subject) level. It does
not reopen, extend, or attempt to rescue the subject-routing hypothesis.
Any macro-only pass/fail result from CG-MACRO-A1 has no bearing on the
`STOP_MAISR` conclusion above, and does not re-enable subject-level
MAISR routing.
