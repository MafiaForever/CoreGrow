# cg_macro_a1_core.py -- CG-MACRO-A1 pure macro-only research helpers.
# No AlgorithmImports. No LEAN types.
#
# CG-MACRO-A1 is a NEW hypothesis, not "MAISR D5". MAISR (CG-MAISR-D0..D4.2)
# tested SUBJECT-level (per-symbol sector/local) stress routing and is
# closed at STOP_MAISR / NO_SUPPORTED_SUBJECT_PACK (see MAISR_D4_CLOSEOUT
# below and docs/CG_MAISR_D4_CLOSEOUT.md). CG-MACRO-A1 only reuses the
# MACRO-level (broad/systemic/rate/defensive) primitives from D4 that do
# not depend on subject-level exposure. It never reopens subject routing.

from __future__ import annotations
import math
from collections import defaultdict

from cg_maisr_d4_core import (
    d4_raw_flags, d4_priority_macro, d4_merge_intervals, d4_build_episodes,
    d4_broad_family_count, d4_broad_family_days, d4_manifest_hash,
    d4_validate_csv_artifact, d4_is_blank_token, d4_is_placeholder_csv,
    d4_validate_source_commit, _TRAINA0, _TRAINA1, _TRAINB0, _TRAINB1, _TRAIN0, _TRAIN1,
)

MAISR_D4_CLOSEOUT = {
    "backtest_id": "bc3126d8554fceb7807dc5dd5f76cece",
    "decision": "STOP_MAISR",
    "reason": "NO_SUPPORTED_SUBJECT_PACK",
    "subject_held_days_train_a": 6,
    "subject_held_days_train_b": 55,
    "subject_held_days_total": 61,
}

# ---------------------------------------------------------------------------
# Truth packs (exactly 4) -- macro-only, no subject/local/sector dimension.
# ---------------------------------------------------------------------------

MACRO_TRUTH_PACKS = [
    {"id": "M1_B60_BR2", "B": 0.60, "br_count": 2, "local": 0.50, "resid": 0.30},
    {"id": "M2_B60_BR3", "B": 0.60, "br_count": 3, "local": 0.50, "resid": 0.30},
    {"id": "M3_B80_BR2", "B": 0.80, "br_count": 2, "local": 0.50, "resid": 0.30},
    {"id": "M4_B80_BR3", "B": 0.80, "br_count": 3, "local": 0.50, "resid": 0.30},
]


def macro_truth_pack_to_d4(pack):
    """MACRO_TRUTH_PACKS store local/resid as positive magnitudes; d4_raw_flags
    expects negative thresholds (see cg_maisr_d4_core.d4_build_packs)."""
    return {
        "B": pack["B"], "br_count": pack["br_count"],
        "local": -abs(pack["local"]), "resid": -abs(pack["resid"]),
    }


def macro_build_truth_stream(pack, session_rows):
    """session_rows: dicts with day, ts, spy_mae, breadth_stressed_count,
    breadth_n, dur_mae, gold_mae, infl_rel, infl_abs, def_resilient_n,
    def_avail_n, med_def_abs, med_def_rel. Macro-only: no held_by_subj."""
    d4pack = macro_truth_pack_to_d4(pack)
    stream = []
    for row in session_rows or []:
        flags = d4_raw_flags(
            d4pack, row.get("spy_mae"), row.get("breadth_stressed_count", 0),
            row.get("breadth_n", 0), row.get("dur_mae"), row.get("gold_mae"),
            row.get("infl_rel"), row.get("infl_abs"),
            row.get("def_resilient_n", 0), row.get("def_avail_n", 0),
            row.get("med_def_abs"), row.get("med_def_rel"), {},
        )
        label = d4_priority_macro(flags)
        stream.append({
            "day": row["day"], "ts": row["ts"], "label": label, "subject": "MACRO",
            "mae": row.get("spy_mae"), "breadth": row.get("breadth_stressed_count"),
        })
    return stream


def macro_build_truth_episodes(pack, session_rows):
    return d4_build_episodes(macro_build_truth_stream(pack, session_rows))


def macro_truth_pack_stats(pack, episodes):
    eps = episodes or []
    return {
        "id": pack["id"],
        "episode_count": len(eps),
        "episode_days": len({e["day"] for e in eps}),
        "broad_family_episodes": d4_broad_family_count(eps),
        "broad_family_days": d4_broad_family_days(eps),
    }


# ---------------------------------------------------------------------------
# Macro state mapping
# ---------------------------------------------------------------------------

_MACRO_NOISE_SOURCE_STATES = ("SECTOR_STRESS", "LOCAL_ASSET_STRESS")
_MACRO_PASSTHROUGH_STATES = (
    "SYSTEMIC_LIQUIDITY_STRESS", "RATE_INFLATION_STRESS", "BROAD_EQUITY_STRESS",
    "DEFENSIVE_ROTATION", "UNCONFIRMED_NOISE", "NORMAL",
)


def macro_map_prediction(state):
    """LOCAL/SECTOR -> UNCONFIRMED_NOISE (macro cannot confirm subject-level
    stress). SYSTEMIC/RATE/BROAD/DEFENSIVE pass through (normalized names).
    Unknown/unrecognized state maps conservatively to UNCONFIRMED_NOISE
    (never fabricates a stress label)."""
    s = str(state or "").strip().upper()
    if s in _MACRO_NOISE_SOURCE_STATES:
        return "UNCONFIRMED_NOISE"
    if s in _MACRO_PASSTHROUGH_STATES:
        return s
    return "UNCONFIRMED_NOISE"


# ---------------------------------------------------------------------------
# Gate modes G0/G1/G2
# ---------------------------------------------------------------------------

_MACRO_GATES = ("G0_BASE", "G1_VOL", "G2_VOL_PATH")
_MACRO_STRESS_STATES = (
    "SYSTEMIC_LIQUIDITY_STRESS", "RATE_INFLATION_STRESS",
    "BROAD_EQUITY_STRESS", "DEFENSIVE_ROTATION",
)


def macro_apply_gate(mapped_state, gate, vix_stress, rv_stress, down_eff_ok,
                      vix_avail, rv_avail, path_avail):
    """G0_BASE: no vol/path confirmation required.
    G1_VOL: requires VIX-or-RV stress confirmation for a stress state, else
    downgrade to UNCONFIRMED_NOISE; UNAVAILABLE if both VIX and RV missing.
    G2_VOL_PATH: G1_VOL plus down-efficiency (path) confirmation; UNAVAILABLE
    if both VIX/RV missing OR if the path source itself is unavailable.
    NORMAL / UNCONFIRMED_NOISE pass through every gate unchanged."""
    if gate not in _MACRO_GATES:
        raise ValueError(f"unknown_gate:{gate}")
    if mapped_state not in _MACRO_STRESS_STATES:
        return mapped_state
    if gate == "G0_BASE":
        return mapped_state
    if not vix_avail and not rv_avail:
        return "UNAVAILABLE"
    vol_confirm = bool((vix_avail and vix_stress) or (rv_avail and rv_stress))
    if gate == "G1_VOL":
        return mapped_state if vol_confirm else "UNCONFIRMED_NOISE"
    # G2_VOL_PATH
    if not path_avail:
        return "UNAVAILABLE"
    if vol_confirm and bool(down_eff_ok):
        return mapped_state
    return "UNCONFIRMED_NOISE"


# ---------------------------------------------------------------------------
# VIX helper (pure)
# ---------------------------------------------------------------------------

def _macro_percentile_rank(values, x):
    if not values:
        return None
    n = len(values)
    le = sum(1 for v in values if v <= x)
    return 100.0 * le / n


def macro_vix_snapshot(history_rows, session_date, lookback=252):
    """history_rows: list of (date, value), already-completed days only.
    Same-session rows (date >= session_date) are rejected internally.
    'valid' means a strictly-prior observation exists; age_sessions is
    measured in series position (1 = the immediately preceding available
    observation in the filtered series), not raw calendar days."""
    rows = [(d, v) for (d, v) in (history_rows or []) if v is not None and d < session_date]
    if not rows:
        return {
            "value": None, "source_date": None, "age_sessions": None,
            "valid": False, "pct_change_1d": None, "percentile_252": None,
        }
    rows.sort(key=lambda r: r[0])
    latest_date, latest_val = rows[-1]
    prev_val = rows[-2][1] if len(rows) >= 2 else None
    pct_change_1d = None
    if prev_val is not None and prev_val != 0:
        pct_change_1d = (latest_val - prev_val) / prev_val
    window = rows[-lookback:] if lookback and lookback > 0 else rows
    window_vals = [v for _, v in window]
    percentile_252 = _macro_percentile_rank(window_vals, latest_val) if len(window_vals) >= 2 else None
    return {
        "value": latest_val, "source_date": latest_date, "age_sessions": 1,
        "valid": True, "pct_change_1d": pct_change_1d, "percentile_252": percentile_252,
    }


# ---------------------------------------------------------------------------
# RV / path
# ---------------------------------------------------------------------------

def macro_rv30(closes):
    """30 closes -> annualized realized vol, or None if insufficient/invalid."""
    xs = list(closes or [])
    if len(xs) < 30:
        return None
    xs = xs[-30:]
    rets = []
    for i in range(1, len(xs)):
        p0, p1 = xs[i - 1], xs[i]
        if p0 is None or p1 is None or p0 <= 0 or p1 <= 0:
            return None
        rets.append(math.log(p1 / p0))
    if len(rets) < 2:
        return None
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    return math.sqrt(var) * math.sqrt(252.0)


def macro_path_efficiency(closes):
    """0..1 net-displacement / total-absolute-movement, or None."""
    xs = [c for c in (closes or []) if c is not None]
    if len(xs) < 2:
        return None
    net = abs(xs[-1] - xs[0])
    total = sum(abs(xs[i] - xs[i - 1]) for i in range(1, len(xs)))
    if total <= 0:
        return None
    return max(0.0, min(1.0, net / total))


def macro_down_efficiency(closes):
    """Signed variant: only rewards efficient DOWN moves; 0.0 if net move is
    up or flat; None if total movement is zero (undefined)."""
    xs = [c for c in (closes or []) if c is not None]
    if len(xs) < 2:
        return None
    net = xs[-1] - xs[0]
    total = sum(abs(xs[i] - xs[i - 1]) for i in range(1, len(xs)))
    if total <= 0:
        return None
    if net >= 0:
        return 0.0
    return max(0.0, min(1.0, abs(net) / total))


def macro_same_tod_percentile(current, history_same_tod):
    """Percentile rank of current within history_same_tod (>=40 required)."""
    hist = [h for h in (history_same_tod or []) if h is not None]
    if current is None or len(hist) < 40:
        return None
    return _macro_percentile_rank(hist, current)


# ---------------------------------------------------------------------------
# Predictor variants: 54 configs x 3 gate modes = 162
# ---------------------------------------------------------------------------

_AMIN = (2, 3)
_BRTH = (0.50, 0.65, 0.75)
_HMODE = ("H0", "H1", "H2")
_ALL_CFG = [(s, a, b, h) for s in ("S1", "S2", "S3") for a in _AMIN
            for b in _BRTH for h in _HMODE]


def _clfid(s, a, b, h):
    return f"{s}_C{a}_B{int(round(b * 100)):02d}_{h}"


def macro_build_predictor_variants():
    variants = []
    for (s, a, b, h) in _ALL_CFG:
        base_id = _clfid(s, a, b, h)
        for gate in _MACRO_GATES:
            variants.append({
                "id": f"{base_id}_{gate}", "clf_id": base_id,
                "s": s, "a": a, "b": b, "h": h, "gate": gate,
            })
    return variants


MACRO_PREDICTOR_VARIANTS = macro_build_predictor_variants()


# ---------------------------------------------------------------------------
# Episode scoring / matching
# ---------------------------------------------------------------------------

def macro_match_episode(pred_ep, truth_ep):
    """Match if same label AND (overlap OR predicted starts up to 10 minutes
    before truth starts)."""
    if pred_ep.get("label") != truth_ep.get("label"):
        return False
    ps, pe_ = pred_ep["start"], pred_ep["end"]
    ts, te_ = truth_ep["start"], truth_ep["end"]
    if ps <= te_ and pe_ >= ts:
        return True
    try:
        gap_minutes = (ts - ps).total_seconds() / 60.0
    except Exception:
        return False
    return 0 <= gap_minutes <= 10


def macro_match_episodes(pred_eps, truth_eps):
    """Greedy one-to-one matching; returns tp/fp/fn and matched pairs."""
    truths = list(truth_eps or [])
    preds = list(pred_eps or [])
    used_truth = [False] * len(truths)
    tp = 0
    matched = []
    for p in preds:
        best_j = None
        for j, t in enumerate(truths):
            if used_truth[j]:
                continue
            if macro_match_episode(p, t):
                best_j = j
                break
        if best_j is not None:
            used_truth[best_j] = True
            tp += 1
            matched.append((p, truths[best_j]))
    fp = len(preds) - tp
    fn = len(truths) - tp
    return {"tp": tp, "fp": fp, "fn": fn, "matched": matched}


def macro_precision_recall_f1(tp, fp, fn):
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) > 0 else 0.0
    return precision, recall, f1


def macro_score_variant(f1_train_a, f1_train_b, n_train_a, n_train_b, min_n=5):
    """TRAIN-selection score: exposure-gated, stability-penalized average F1
    (mirrors D4's exposure/stability-normalized selection philosophy)."""
    if n_train_a < min_n or n_train_b < min_n:
        return 0.0, "INSUFFICIENT_N"
    if f1_train_a <= 0 or f1_train_b <= 0:
        return 0.0, "ZERO_F1"
    lo, hi = min(f1_train_a, f1_train_b), max(f1_train_a, f1_train_b)
    ratio = hi / lo if lo > 0 else None
    penalty = 0.5 if (ratio is not None and ratio > 3.0) else 0.0
    score = (0.5 * f1_train_a + 0.5 * f1_train_b) * (1.0 - penalty)
    return score, "OK"


# ---------------------------------------------------------------------------
# Event study math
# ---------------------------------------------------------------------------

def macro_event_benefit(basket_ret, action=0.20, cost_bps_per_side=0):
    """gross = -action * basket_ret; cost = 2 * (cost_bps/10000) * action
    (both sides on traded notional); return gross - cost."""
    gross = -float(action) * float(basket_ret)
    cost = 2.0 * (float(cost_bps_per_side) / 10000.0) * float(action)
    return gross - cost


# ---------------------------------------------------------------------------
# Stage A value gate
# ---------------------------------------------------------------------------

_MACRO_VALUE_WINDOWS_REQUIRED = ("TRAIN", "OOS", "CRISIS")
_MACRO_VALUE_ALL_WINDOWS = ("TRAIN", "OOS", "CRISIS", "Y2020", "Y2022", "RUN")
_MACRO_FALSE_CUT_MAX = 0.40
_MACRO_VALUE_MIN_N = 5


def macro_stage_a_value_pass(metrics_by_window, neighbor_ok):
    """metrics_by_window keys: TRAIN, OOS, CRISIS, Y2020, Y2022, RUN; each a
    dict with n, mean_2bps, median_2bps, false_cut_rate, total_2bps,
    total_5bps, year_pos_shares. Required windows (TRAIN/OOS/CRISIS) must
    each show n>=min, positive mean/median 2bps benefit, and an acceptable
    false-cut rate; neighbor_ok (parameter-neighborhood stability) required."""
    reasons = []
    windows_checked = {}
    for w in _MACRO_VALUE_WINDOWS_REQUIRED:
        m = (metrics_by_window or {}).get(w)
        if not m:
            reasons.append(f"{w}_MISSING")
            windows_checked[w] = False
            continue
        ok = True
        if m.get("n", 0) < _MACRO_VALUE_MIN_N:
            ok = False
            reasons.append(f"{w}_N_LOW")
        if m.get("mean_2bps") is None or m["mean_2bps"] <= 0:
            ok = False
            reasons.append(f"{w}_MEAN_NONPOS")
        if m.get("median_2bps") is None or m["median_2bps"] <= 0:
            ok = False
            reasons.append(f"{w}_MEDIAN_NONPOS")
        if m.get("false_cut_rate") is None or m["false_cut_rate"] > _MACRO_FALSE_CUT_MAX:
            ok = False
            reasons.append(f"{w}_FALSE_CUT_HIGH")
        windows_checked[w] = ok
    all_required_ok = all(windows_checked.get(w) for w in _MACRO_VALUE_WINDOWS_REQUIRED)
    if not neighbor_ok:
        reasons.append("NEIGHBOR_UNSTABLE")
    passed = all_required_ok and bool(neighbor_ok)
    return {
        "pass": passed, "reasons": reasons, "windows_checked": windows_checked,
        "windows_required": _MACRO_VALUE_WINDOWS_REQUIRED,
    }


# ---------------------------------------------------------------------------
# Finalize research result
# ---------------------------------------------------------------------------

def macro_finalize_result(tech_ok, art_ok, truth_ok, pred_ok, value_pass_n):
    if not tech_ok:
        return {"result": "FAILED", "reason": "TECHNICAL_GATE_FAIL",
                "next": "FIX_MACRO_A1_TECHNICAL", "research_conclusion": "NOT_REACHED"}
    if not art_ok:
        return {"result": "FAILED", "reason": "ARTIFACT_VALIDATION_FAIL",
                "next": "FIX_MACRO_A1_ARTIFACTS", "research_conclusion": "NOT_REACHED"}
    if not truth_ok:
        return {"result": "STOP_MACRO_A1", "reason": "NO_VALID_MACRO_TRUTH_PACK",
                "next": "STOP_MACRO_A1", "research_conclusion": "STOP_MACRO_A1"}
    if not pred_ok:
        return {"result": "STOP_MACRO_A1", "reason": "INSUFFICIENT_MACRO_PREDICTOR_DIVERSITY",
                "next": "STOP_MACRO_A1", "research_conclusion": "STOP_MACRO_A1"}
    if int(value_pass_n or 0) == 0:
        return {"result": "STOP_MACRO_A1", "reason": "NO_STABLE_MACRO_EVENT_VALUE",
                "next": "STOP_MACRO_A1", "research_conclusion": "STOP_MACRO_A1"}
    return {"result": "MACRO_A1_PASS", "reason": "OK",
            "next": "BUILD_MACRO_A2_EXECUTION_SHADOW", "research_conclusion": "NOT_REACHED"}


# ---------------------------------------------------------------------------
# Artifact schemas
# ---------------------------------------------------------------------------

def macro_a1_artifact_schemas():
    return {
        "identity": ["id", "pass", "n", "nav_diff_pct", "maxdd_diff_pp", "corr"],
        "truth_packs": ["id", "B", "br_count", "local", "resid", "episode_count",
                         "episode_days", "selected"],
        "predictors": ["id", "clf_id", "s", "a", "b", "h", "gate", "score",
                        "f1_train_a", "f1_train_b", "n_train_a", "n_train_b",
                        "valid", "selected"],
        "event_value": ["window", "truth_pack", "predictor", "n", "mean_2bps",
                         "median_2bps", "false_cut_rate", "total_2bps",
                         "total_5bps", "year_pos_shares", "pass"],
        "vix_snapshot": ["session_date", "value", "source_date", "age_sessions",
                          "valid", "pct_change_1d", "percentile_252"],
    }


# ---------------------------------------------------------------------------
# Selection helpers
# ---------------------------------------------------------------------------

def macro_select_truth_pack(pack_stats):
    """Highest-score supported+stable pack, or None if none qualify."""
    supported = [p for p in (pack_stats or []) if p.get("support_ok") and p.get("stability_ok")]
    if not supported:
        return None
    supported.sort(key=lambda p: (-float(p.get("score", 0.0) or 0.0), p["id"]))
    return supported[0]["id"]


_MACRO_SELECT_MAX = 6
_MACRO_SELECT_MAX_PER_GATE = 2
_MACRO_SELECT_MIN = 3
_MACRO_SELECT_MIN_GATES = 2
_MACRO_SELECT_MIN_H = 2
_MACRO_SELECT_MIN_HASHES = 2


def macro_select_predictors(scored_variants):
    """Select up to 6 valid variants, max 2 per gate, ranked by score.
    pred_ok requires >=3 selected, >=2 distinct gates, >=2 distinct H modes,
    and >=2 distinct signature hashes (genuine diversity, not near-dupes)."""
    cands = [v for v in (scored_variants or []) if v.get("valid")]
    cands.sort(key=lambda v: (-float(v.get("score", 0.0) or 0.0), v.get("id", "")))
    selected = []
    per_gate = defaultdict(int)
    for v in cands:
        if len(selected) >= _MACRO_SELECT_MAX:
            break
        gate = v.get("gate")
        if per_gate[gate] >= _MACRO_SELECT_MAX_PER_GATE:
            continue
        selected.append(v)
        per_gate[gate] += 1
    gates = {v.get("gate") for v in selected}
    hmodes = {v.get("h") for v in selected}
    hashes = {v.get("sig_hash") for v in selected if v.get("sig_hash") is not None}
    pred_ok = (
        len(selected) >= _MACRO_SELECT_MIN
        and len(gates) >= _MACRO_SELECT_MIN_GATES
        and len(hmodes) >= _MACRO_SELECT_MIN_H
        and len(hashes) >= _MACRO_SELECT_MIN_HASHES
    )
    return {
        "selected_ids": [v["id"] for v in selected],
        "n_selected": len(selected),
        "distinct_gates": len(gates),
        "distinct_h": len(hmodes),
        "distinct_hashes": len(hashes),
        "pred_ok": pred_ok,
    }


def macro_validate_source_commit_pair(commit_a, commit_b):
    ok_a, why_a = d4_validate_source_commit(commit_a)
    ok_b, why_b = d4_validate_source_commit(commit_b)
    return (ok_a and ok_b), {"a": why_a, "b": why_b}


# ---------------------------------------------------------------------------
# Static tests 01..44
# ---------------------------------------------------------------------------

def run_macro_a1_static_tests():
    results = []

    def ok(n, name, passed, detail=""):
        results.append({"n": n, "name": name, "pass": bool(passed), "detail": detail})

    from datetime import date, datetime, timedelta

    # 01 MAISR D4 closeout backtest id and decision
    ok(1, "maisr_d4_closeout_backtest_id_and_decision",
       MAISR_D4_CLOSEOUT["backtest_id"] == "bc3126d8554fceb7807dc5dd5f76cece"
       and MAISR_D4_CLOSEOUT["decision"] == "STOP_MAISR"
       and MAISR_D4_CLOSEOUT["reason"] == "NO_SUPPORTED_SUBJECT_PACK")

    # 02 subject exposure
    ok(2, "maisr_d4_closeout_subject_exposure",
       MAISR_D4_CLOSEOUT["subject_held_days_train_a"] == 6
       and MAISR_D4_CLOSEOUT["subject_held_days_train_b"] == 55
       and MAISR_D4_CLOSEOUT["subject_held_days_total"] == 61
       and MAISR_D4_CLOSEOUT["subject_held_days_train_a"] + MAISR_D4_CLOSEOUT["subject_held_days_train_b"]
       == MAISR_D4_CLOSEOUT["subject_held_days_total"])

    # 03 truth packs count and unique
    ids3 = [p["id"] for p in MACRO_TRUTH_PACKS]
    tuples3 = [(p["B"], p["br_count"], p["local"], p["resid"]) for p in MACRO_TRUTH_PACKS]
    ok(3, "macro_truth_packs_count_and_unique",
       len(MACRO_TRUTH_PACKS) == 4 and len(set(ids3)) == 4 and len(set(tuples3)) == 4)

    # 04 truth pack fields
    ok(4, "macro_truth_packs_fields",
       all(p["B"] in (0.60, 0.80) for p in MACRO_TRUTH_PACKS)
       and all(p["br_count"] in (2, 3) for p in MACRO_TRUTH_PACKS)
       and all(p["local"] == 0.50 and p["resid"] == 0.30 for p in MACRO_TRUTH_PACKS))

    # 05 truth pack -> d4 sign conversion
    d4p = macro_truth_pack_to_d4(MACRO_TRUTH_PACKS[0])
    ok(5, "macro_truth_pack_to_d4_sign_conversion",
       d4p["local"] == -0.50 and d4p["resid"] == -0.30
       and d4p["B"] == MACRO_TRUTH_PACKS[0]["B"] and d4p["br_count"] == MACRO_TRUTH_PACKS[0]["br_count"])

    # 06 macro_build_truth_episodes reuses d4_raw_flags/d4_priority_macro/d4_build_episodes
    d0 = date(2015, 8, 24)
    d1 = date(2015, 8, 25)
    rows6 = [
        {"day": d0.toordinal(), "ts": datetime(2015, 8, 24, 9, 45),
         "spy_mae": -0.90, "breadth_stressed_count": 3, "breadth_n": 4},
        {"day": d1.toordinal(), "ts": datetime(2015, 8, 25, 9, 45),
         "spy_mae": -0.95, "breadth_stressed_count": 4, "breadth_n": 4},
    ]
    pack6 = MACRO_TRUTH_PACKS[2]  # M3_B80_BR2
    eps6 = macro_build_truth_episodes(pack6, rows6)
    stats6 = macro_truth_pack_stats(pack6, eps6)
    ok(6, "macro_build_truth_episodes_reuses_d4",
       len(eps6) == 2 and all(e["label"] == "BROAD_EQUITY_STRESS" for e in eps6)
       and stats6["broad_family_episodes"] == 2 and stats6["broad_family_days"] == 2
       and stats6["episode_count"] == 2)

    # 07 map prediction LOCAL/SECTOR -> noise
    ok(7, "macro_map_prediction_local_sector_noise",
       macro_map_prediction("LOCAL_ASSET_STRESS") == "UNCONFIRMED_NOISE"
       and macro_map_prediction("SECTOR_STRESS") == "UNCONFIRMED_NOISE")

    # 08 map prediction stress passthrough
    ok(8, "macro_map_prediction_stress_passthrough",
       macro_map_prediction("SYSTEMIC_LIQUIDITY_STRESS") == "SYSTEMIC_LIQUIDITY_STRESS"
       and macro_map_prediction("RATE_INFLATION_STRESS") == "RATE_INFLATION_STRESS"
       and macro_map_prediction("BROAD_EQUITY_STRESS") == "BROAD_EQUITY_STRESS"
       and macro_map_prediction("DEFENSIVE_ROTATION") == "DEFENSIVE_ROTATION")

    # 09 map prediction normal/noise passthrough
    ok(9, "macro_map_prediction_normal_noise_passthrough",
       macro_map_prediction("NORMAL") == "NORMAL"
       and macro_map_prediction("UNCONFIRMED_NOISE") == "UNCONFIRMED_NOISE")

    # 10 map prediction unknown + case normalize
    ok(10, "macro_map_prediction_unknown_and_case_normalize",
       macro_map_prediction("bogus_state") == "UNCONFIRMED_NOISE"
       and macro_map_prediction("  broad_equity_stress  ") == "BROAD_EQUITY_STRESS"
       and macro_map_prediction(None) == "UNCONFIRMED_NOISE")

    # 11 gate G0 stress passthrough
    ok(11, "macro_apply_gate_g0_stress_passthrough",
       macro_apply_gate("BROAD_EQUITY_STRESS", "G0_BASE", False, False, False, False, False, False)
       == "BROAD_EQUITY_STRESS")

    # 12 gate G0 normal/noise unchanged regardless of gate mode
    ok(12, "macro_apply_gate_g0_normal_noise_unchanged",
       macro_apply_gate("NORMAL", "G2_VOL_PATH", False, False, False, False, False, False) == "NORMAL"
       and macro_apply_gate("UNCONFIRMED_NOISE", "G1_VOL", False, False, False, False, False, False)
       == "UNCONFIRMED_NOISE")

    # 13 gate G1 confirmed by VIX
    ok(13, "macro_apply_gate_g1_confirmed_by_vix",
       macro_apply_gate("BROAD_EQUITY_STRESS", "G1_VOL", True, False, False, True, False, False)
       == "BROAD_EQUITY_STRESS")

    # 14 gate G1 confirmed by RV
    ok(14, "macro_apply_gate_g1_confirmed_by_rv",
       macro_apply_gate("BROAD_EQUITY_STRESS", "G1_VOL", False, True, False, False, True, False)
       == "BROAD_EQUITY_STRESS")

    # 15 gate G1 unconfirmed -> noise
    ok(15, "macro_apply_gate_g1_unconfirmed_noise",
       macro_apply_gate("BROAD_EQUITY_STRESS", "G1_VOL", False, False, False, True, True, False)
       == "UNCONFIRMED_NOISE")

    # 16 gate G1 both vol sources unavailable -> UNAVAILABLE
    ok(16, "macro_apply_gate_g1_both_unavailable",
       macro_apply_gate("BROAD_EQUITY_STRESS", "G1_VOL", False, False, False, False, False, True)
       == "UNAVAILABLE")

    # 17 gate G2 confirmed (vol + path) -> pass
    ok(17, "macro_apply_gate_g2_confirmed_pass",
       macro_apply_gate("BROAD_EQUITY_STRESS", "G2_VOL_PATH", True, False, True, True, False, True)
       == "BROAD_EQUITY_STRESS")

    # 18 gate G2 path fail -> noise
    ok(18, "macro_apply_gate_g2_path_fail_noise",
       macro_apply_gate("BROAD_EQUITY_STRESS", "G2_VOL_PATH", True, False, False, True, False, True)
       == "UNCONFIRMED_NOISE")

    # 19 gate G2 path source unavailable -> UNAVAILABLE
    ok(19, "macro_apply_gate_g2_path_unavailable",
       macro_apply_gate("BROAD_EQUITY_STRESS", "G2_VOL_PATH", True, False, True, True, False, False)
       == "UNAVAILABLE")

    # 20 gate G2 vol both unavailable -> UNAVAILABLE (checked before path)
    ok(20, "macro_apply_gate_g2_vol_unavailable",
       macro_apply_gate("BROAD_EQUITY_STRESS", "G2_VOL_PATH", False, False, True, False, False, True)
       == "UNAVAILABLE")

    # 21 gate invalid mode raises
    raised21 = False
    try:
        macro_apply_gate("BROAD_EQUITY_STRESS", "G9_BOGUS", False, False, False, False, False, False)
    except ValueError:
        raised21 = True
    ok(21, "macro_apply_gate_invalid_raises", raised21)

    # 22 vix snapshot rejects same-session rows
    sd22 = date(2020, 3, 16)
    hist22 = [(date(2020, 3, 15), 60.0), (sd22, 999.0), (date(2020, 3, 17), 111.0)]
    snap22 = macro_vix_snapshot(hist22, sd22)
    ok(22, "macro_vix_snapshot_rejects_same_session",
       snap22["valid"] and snap22["value"] == 60.0 and snap22["source_date"] == date(2020, 3, 15))

    # 23 vix snapshot valid pct_change and age
    hist23 = [(date(2020, 3, 12), 50.0), (date(2020, 3, 13), 60.0)]
    snap23 = macro_vix_snapshot(hist23, date(2020, 3, 16))
    ok(23, "macro_vix_snapshot_valid_pct_change_age",
       snap23["valid"] and abs(snap23["pct_change_1d"] - 0.2) < 1e-9 and snap23["age_sessions"] == 1)

    # 24 vix snapshot percentile rank
    hist24 = [(date(2020, 1, 1) + timedelta(days=i), float(i + 1)) for i in range(40)]  # 1..40
    snap24 = macro_vix_snapshot(hist24, date(2020, 3, 1), lookback=252)
    ok(24, "macro_vix_snapshot_percentile_rank", abs(snap24["percentile_252"] - 100.0) < 1e-9)

    # 25 vix snapshot empty -> invalid
    snap25 = macro_vix_snapshot([], date(2020, 3, 16))
    ok(25, "macro_vix_snapshot_empty_invalid",
       not snap25["valid"] and snap25["value"] is None and snap25["percentile_252"] is None)

    # 26 rv30 insufficient and constant series
    rv_none = macro_rv30([100.0] * 10)
    rv_zero = macro_rv30([100.0] * 30)
    ok(26, "macro_rv30_insufficient_and_constant", rv_none is None and rv_zero == 0.0)

    # 27 path efficiency straight line and insufficient
    pe_full = macro_path_efficiency([float(i) for i in range(1, 11)])
    pe_none = macro_path_efficiency([5.0])
    ok(27, "macro_path_efficiency_straight_and_insufficient",
       pe_full is not None and abs(pe_full - 1.0) < 1e-9 and pe_none is None)

    # 28 down efficiency decline/incline/flat
    de_decline = macro_down_efficiency([float(i) for i in range(10, 0, -1)])
    de_incline = macro_down_efficiency([float(i) for i in range(1, 11)])
    de_flat = macro_down_efficiency([5.0, 5.0, 5.0])
    ok(28, "macro_down_efficiency_decline_incline_flat",
       abs(de_decline - 1.0) < 1e-9 and de_incline == 0.0 and de_flat is None)

    # 29 same-tod percentile insufficient and valid
    hist29 = [float(i) for i in range(1, 40)]  # 39 points -> insufficient
    hist29b = [float(i) for i in range(1, 41)]  # 40 points
    stp_none = macro_same_tod_percentile(20.0, hist29)
    stp_val = macro_same_tod_percentile(20.5, hist29b)
    ok(29, "macro_same_tod_percentile_insufficient_and_valid",
       stp_none is None and stp_val is not None and abs(stp_val - 50.0) < 1e-9)

    # 30 predictor configs: 54 unique
    ok(30, "predictor_variants_54_unique_cfg_ids",
       len(_ALL_CFG) == 54 and len(set(_ALL_CFG)) == 54
       and len({_clfid(*c) for c in _ALL_CFG}) == 54)

    # 31 predictor variants 162 total with gate suffix
    ok(31, "predictor_variants_162_total_gate_suffix",
       len(MACRO_PREDICTOR_VARIANTS) == 162
       and len({v["id"] for v in MACRO_PREDICTOR_VARIANTS}) == 162
       and all(v["id"].endswith(v["gate"]) for v in MACRO_PREDICTOR_VARIANTS)
       and {v["gate"] for v in MACRO_PREDICTOR_VARIANTS} == set(_MACRO_GATES))

    # 32 match episode overlap and lead-within-10
    t0 = datetime(2020, 3, 16, 10, 0)
    truth32 = {"label": "BROAD_EQUITY_STRESS", "start": t0 + timedelta(minutes=30),
               "end": t0 + timedelta(minutes=90)}
    pred_overlap = {"label": "BROAD_EQUITY_STRESS", "start": t0, "end": t0 + timedelta(minutes=60)}
    pred_lead = {"label": "BROAD_EQUITY_STRESS", "start": t0 + timedelta(minutes=22),
                 "end": t0 + timedelta(minutes=27)}
    ok(32, "macro_match_episode_overlap_and_lead",
       macro_match_episode(pred_overlap, truth32) and macro_match_episode(pred_lead, truth32))

    # 33 match episode label mismatch and gap>10 fail
    pred_wrong_label = {**pred_overlap, "label": "RATE_INFLATION_STRESS"}
    pred_gap = {"label": "BROAD_EQUITY_STRESS", "start": t0, "end": t0 + timedelta(minutes=5)}
    truth33 = {"label": "BROAD_EQUITY_STRESS", "start": t0 + timedelta(minutes=20),
               "end": t0 + timedelta(minutes=40)}
    ok(33, "macro_match_episode_label_mismatch_and_gap_fail",
       not macro_match_episode(pred_wrong_label, truth32) and not macro_match_episode(pred_gap, truth33))

    # 34 match episodes perfect prf1
    truths34 = [
        {"label": "BROAD_EQUITY_STRESS", "start": t0, "end": t0 + timedelta(minutes=30)},
        {"label": "BROAD_EQUITY_STRESS", "start": t0 + timedelta(hours=2), "end": t0 + timedelta(hours=2, minutes=30)},
    ]
    preds34 = [dict(e) for e in truths34]
    m34 = macro_match_episodes(preds34, truths34)
    p34, r34, f134 = macro_precision_recall_f1(m34["tp"], m34["fp"], m34["fn"])
    ok(34, "macro_match_episodes_perfect_prf1",
       m34["tp"] == 2 and m34["fp"] == 0 and m34["fn"] == 0
       and p34 == 1.0 and r34 == 1.0 and f134 == 1.0)

    # 35 match episodes partial prf1
    truths35 = [
        {"label": "BROAD_EQUITY_STRESS", "start": t0, "end": t0 + timedelta(minutes=30)},
        {"label": "BROAD_EQUITY_STRESS", "start": t0 + timedelta(hours=2), "end": t0 + timedelta(hours=2, minutes=30)},
        {"label": "BROAD_EQUITY_STRESS", "start": t0 + timedelta(hours=4), "end": t0 + timedelta(hours=4, minutes=30)},
    ]
    preds35 = [
        dict(truths35[0]), dict(truths35[1]),
        {"label": "BROAD_EQUITY_STRESS", "start": t0 + timedelta(hours=6), "end": t0 + timedelta(hours=6, minutes=30)},
        {"label": "BROAD_EQUITY_STRESS", "start": t0 + timedelta(hours=8), "end": t0 + timedelta(hours=8, minutes=30)},
    ]
    m35 = macro_match_episodes(preds35, truths35)
    p35, r35, f135 = macro_precision_recall_f1(m35["tp"], m35["fp"], m35["fn"])
    exp_p, exp_r, exp_f1 = macro_precision_recall_f1(2, 2, 1)
    ok(35, "macro_match_episodes_partial_prf1",
       m35["tp"] == 2 and m35["fp"] == 2 and m35["fn"] == 1
       and abs(p35 - exp_p) < 1e-9 and abs(r35 - exp_r) < 1e-9 and abs(f135 - exp_f1) < 1e-9)

    # 36 event benefit zero cost
    b36 = macro_event_benefit(0.05, action=0.20, cost_bps_per_side=0)
    ok(36, "macro_event_benefit_zero_cost", abs(b36 - (-0.01)) < 1e-12)

    # 37 event benefit with cost
    b37 = macro_event_benefit(0.05, action=0.20, cost_bps_per_side=5)
    ok(37, "macro_event_benefit_with_cost", abs(b37 - (-0.0102)) < 1e-12)

    # 38 stage A value pass -- passing scenario
    good_win = {"n": 10, "mean_2bps": 0.001, "median_2bps": 0.0008, "false_cut_rate": 0.2,
                "total_2bps": 0.01, "total_5bps": 0.02, "year_pos_shares": 0.6}
    metrics38 = {"TRAIN": good_win, "OOS": good_win, "CRISIS": good_win}
    v38 = macro_stage_a_value_pass(metrics38, True)
    ok(38, "macro_stage_a_value_pass_passes", v38["pass"] and v38["reasons"] == [])

    # 39 stage A value pass -- missing window and neighbor-unstable fail
    metrics39 = {"TRAIN": good_win, "CRISIS": good_win}  # OOS missing
    v39a = macro_stage_a_value_pass(metrics39, True)
    v39b = macro_stage_a_value_pass(metrics38, False)
    ok(39, "macro_stage_a_value_pass_fails_missing_and_neighbor",
       not v39a["pass"] and "OOS_MISSING" in v39a["reasons"]
       and not v39b["pass"] and "NEIGHBOR_UNSTABLE" in v39b["reasons"])

    # 40 finalize result branches
    f_tech = macro_finalize_result(False, True, True, True, 1)
    f_art = macro_finalize_result(True, False, True, True, 1)
    f_truth = macro_finalize_result(True, True, False, True, 1)
    f_pred = macro_finalize_result(True, True, True, False, 1)
    f_val0 = macro_finalize_result(True, True, True, True, 0)
    f_pass = macro_finalize_result(True, True, True, True, 3)
    ok(40, "macro_finalize_result_branches",
       f_tech["result"] == "FAILED" and f_tech["reason"] == "TECHNICAL_GATE_FAIL"
       and f_art["result"] == "FAILED" and f_art["reason"] == "ARTIFACT_VALIDATION_FAIL"
       and f_truth == {"result": "STOP_MACRO_A1", "reason": "NO_VALID_MACRO_TRUTH_PACK",
                        "next": "STOP_MACRO_A1", "research_conclusion": "STOP_MACRO_A1"}
       and f_pred["reason"] == "INSUFFICIENT_MACRO_PREDICTOR_DIVERSITY"
       and f_val0["reason"] == "NO_STABLE_MACRO_EVENT_VALUE"
       and f_pass["result"] == "MACRO_A1_PASS" and f_pass["next"] == "BUILD_MACRO_A2_EXECUTION_SHADOW")

    # 41 artifact schemas shape
    schemas41 = macro_a1_artifact_schemas()
    ok(41, "macro_a1_artifact_schemas_shape",
       set(schemas41) == {"identity", "truth_packs", "predictors", "event_value", "vix_snapshot"}
       and all(isinstance(v, list) and len(v) >= 3 for v in schemas41.values()))

    # 42 artifact CSV validate reuse (d4_validate_csv_artifact / blank / placeholder)
    tp_schema = schemas41["truth_packs"]
    good_lines42 = [",".join(tp_schema)]
    for p in MACRO_TRUTH_PACKS:
        good_lines42.append(",".join(str(x) for x in [
            p["id"], p["B"], p["br_count"], p["local"], p["resid"], 10, 8, 0]))
    v42_good = d4_validate_csv_artifact(
        "truth_packs", "\n".join(good_lines42), tp_schema, 4, ["id", "B"], unique_key="id")
    bad_lines42 = [",".join(tp_schema), ",".join(str(x) for x in [
        MACRO_TRUTH_PACKS[0]["id"], "", 2, 0.5, 0.3, 10, 8, 0])]
    v42_bad = d4_validate_csv_artifact(
        "truth_packs", "\n".join(bad_lines42), tp_schema, 1, ["id", "B"], unique_key="id")
    placeholder_ok42 = d4_is_placeholder_csv("id,B\nALL,SEE_STABILITY") and d4_is_blank_token("")
    ok(42, "macro_artifact_csv_validate_reuse",
       v42_good["pass"] and not v42_bad["pass"] and placeholder_ok42)

    # 43 validate source commit pair reuse
    ok_pair43, why43 = macro_validate_source_commit_pair("a" * 40, "b" * 40)
    bad_pair43, _ = macro_validate_source_commit_pair("local", "b" * 40)
    ok(43, "macro_validate_source_commit_pair_reuse",
       ok_pair43 and why43["a"] == "OK" and why43["b"] == "OK" and not bad_pair43)

    # 44 select truth pack and select predictors
    pack_stats44 = [
        {"id": "M1_B60_BR2", "support_ok": True, "stability_ok": True, "score": 0.5},
        {"id": "M2_B60_BR3", "support_ok": True, "stability_ok": True, "score": 0.8},
        {"id": "M3_B80_BR2", "support_ok": False, "stability_ok": True, "score": 0.9},
    ]
    sel44 = macro_select_truth_pack(pack_stats44)
    sel44_none = macro_select_truth_pack([{"id": "X", "support_ok": False, "stability_ok": True, "score": 1.0}])
    variants_pass = [
        {"id": "V1", "gate": "G0_BASE", "h": "H0", "score": 0.9, "valid": True, "sig_hash": "hA"},
        {"id": "V2", "gate": "G0_BASE", "h": "H1", "score": 0.8, "valid": True, "sig_hash": "hB"},
        {"id": "V3", "gate": "G1_VOL", "h": "H0", "score": 0.7, "valid": True, "sig_hash": "hC"},
    ]
    variants_fail = [
        {"id": "V1", "gate": "G0_BASE", "h": "H0", "score": 0.9, "valid": True, "sig_hash": "hA"},
        {"id": "V2", "gate": "G0_BASE", "h": "H0", "score": 0.8, "valid": True, "sig_hash": "hA"},
    ]
    sp_pass = macro_select_predictors(variants_pass)
    sp_fail = macro_select_predictors(variants_fail)
    ok(44, "macro_select_truth_pack_and_predictors",
       sel44 == "M2_B60_BR3" and sel44_none is None
       and sp_pass["pred_ok"] and sp_fail["pred_ok"] is False
       and sp_pass["n_selected"] == 3 and sp_pass["distinct_gates"] == 2)

    by_n = {}
    for r in results:
        by_n[r["n"]] = r
    uniq = [by_n[i] for i in range(1, 45) if i in by_n]
    passed = sum(1 for r in uniq if r["pass"])
    return uniq, passed, len(uniq)


if __name__ == "__main__":
    rows, p, n = run_macro_a1_static_tests()
    for r in rows:
        print(f"{r['n']:02d} {r['name']}: {'PASS' if r['pass'] else 'FAIL'} {r.get('detail', '')}")
    print(f"TOTAL {p}/{n}")
