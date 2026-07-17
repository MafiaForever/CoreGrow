# CG-MACRO-A1 CLOSEOUT

Status: **STOP_MACRO_A1**
Date closed: 2026-07-17

## 1. Accepted result

```text
backtest_id                 = 9b7fa30127bf12e10b67fea9769dfd86
source_commit               = 35585ca0d47470c26481000c141a4decb3e67395
technical_result            = PASS
truth_pack                  = M4_B80_BR3
predictor_family            = REJECTED
event_economics             = NOT_MEASURED
missing_event_price_count   = 11973
research_conclusion         = STOP_MACRO_A1
reason                      = NO_STABLE_MACRO_EVENT_VALUE
next                        = STOP_MACRO_A1
```

## 2. Research interpretation

```text
macro truth exists
old 54-classifier / 162-variant predictor family rejected
exact production-basket economics not measured
  (11973 exact-production-basket price failures)
```

## 3. Evidence summary

```text
production identity PASS (3/3, n=3643)
fixed165 PASS (captured=894, deferred=executed=894)
previous-session VIX semantics PASS
GLD/GLDM continuity PASS
truth packs defined=4, valid=2 (M3, M4)
selected truth pack=M4_B80_BR3
predictor variants scored=162, formally valid=107
predictors selected=6 (economically weak)
selected predictors produced zero priceable production-basket events
stage_a_value_pass_count=0
```

## 4. Six selected weak predictors

```text
S3_C2_B65_H1_G1_VOL
S3_C2_B65_H2_G0_BASE
S3_C2_B65_H1_G0_BASE
S3_C2_B65_H1_G2_VOL_PATH
S3_C2_B65_H0_G2_VOL_PATH
S3_C2_B65_H2_G1_VOL
```

## 5. Residual hypothesis (next)

Do not rerun or refine the 162-variant A1 family.

Next diagnostic: **CG-MACRO-RESID-B1** — direct causal combinations after
existing W2 / IDS / Panic, proxy outcomes, production-loss association,
and subscription bottleneck audit (no subscription changes).
