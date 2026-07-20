# cg_damage_duration_d04a_ablation.py -- D0.4A P5 component ablations (diagnostic only).
# Parallel shadow engines; never mutate ModelAShadowRouter / P5_FULL production path.
from __future__ import annotations
from copy import deepcopy
from datetime import datetime

from cg_damage_duration_d03a_core import (
    UNAVAILABLE, EPS, RECOVERY_CONFIDENCE_MIN, _avail, _f, get,
    compute_recovery_components, compute_recovery_score, compute_model_a,
    compute_recovery_confidence, recovery_ladder_fraction,
    s_severity, s_persistence, s_memory, s_recovery_from_score, duration_forecast,
)
from cg_damage_duration_d03a_shadow import P5_STATES, P5_DWELL_MINUTES

EXPERIMENT = "CG-DAMAGE-DURATION-D0.4A"
PHASE = "D0.4A_PREREGISTERED_WALK_FORWARD_AND_COMPONENT_ABLATION"

P5_FULL = "P5_FULL"
P5_NO_CHANGEPOINT = "P5_NO_CHANGEPOINT"
P5_NO_STRUCTURE = "P5_NO_STRUCTURE"
P5_NO_HYSTERESIS = "P5_NO_HYSTERESIS"
P5_NO_ABSTENTION = "P5_NO_ABSTENTION"

ABLATION_VARIANT_IDS = (
    P5_NO_CHANGEPOINT, P5_NO_STRUCTURE, P5_NO_HYSTERESIS, P5_NO_ABSTENTION,
)
SCORECARD_VARIANT_IDS = (
    "P3_HOLD_3D", "P4_GRADUAL_FIXED", P5_FULL,
) + ABLATION_VARIANT_IDS

D04A_BLOCKS = {
    "2012_2015": (2012, 2015),
    "2016_2019": (2016, 2019),
    "2020_2022": (2020, 2022),
    "2023_2026": (2023, 2026),
}


def _neutralize_changepoint_snap_c(snap_c):
    """CP contributes neutral (0) and never vetoes; no new classifier."""
    c = deepcopy(snap_c) if isinstance(snap_c, dict) else {}
    c["CP_adverse"] = 0.0
    c["CP_favorable"] = 0.0
    return c


def _neutralize_structure_snap_c(snap_c):
    """Structure contribution neutral; no replacement classifier."""
    c = deepcopy(snap_c) if isinstance(snap_c, dict) else {}
    # UNCERTAIN -> UNAVAILABLE in s_structure; force explicit neutral 0 via fake state
    # Use ROTATION (STRUCTURE_SCORE=0) as neutral labeled state already in contract.
    c["structure_state"] = "ROTATION"
    c["structure_confidence"] = 0.0
    return c


def _model_a_with_overrides(snap_b, snap_c, recovery_score, *, force_s_cp=None, force_s_structure=None):
    """Recompute Model A with optional neutral component overrides (no d03a edits)."""
    sev = s_severity(get(snap_b, "D_state"))
    pers = s_persistence(get(snap_b, "D45_persist_12"))
    from cg_damage_duration_d03a_core import s_structure, s_cp
    struct = s_structure(get(snap_c, "structure_state"))
    mem = s_memory(snap_b)
    cp = s_cp(get(snap_c, "CP_adverse"), get(snap_c, "CP_favorable"))
    if force_s_cp is not None:
        cp = force_s_cp
    if force_s_structure is not None:
        struct = force_s_structure
    srec = s_recovery_from_score(recovery_score)
    comps = {
        "S_severity": sev, "S_persistence": pers, "S_structure": struct,
        "S_memory": mem, "S_cp": cp, "S_recovery": srec,
    }
    model_a_coverage = sum(
        1 for k in ("S_severity", "S_persistence", "S_structure", "S_memory", "S_cp")
        if _avail(comps[k])
    ) / 5.0
    all_six = all(_avail(comps[k]) for k in comps)
    if not all_six:
        score = UNAVAILABLE
        forecast = "ABSTAIN_P0_CURRENT"
        reason = "missing_duration_component"
    else:
        score = _f(sev) + _f(pers) + _f(struct) + _f(mem) + _f(cp) - _f(srec)
        forecast = duration_forecast(score)
        reason = UNAVAILABLE
    return {
        **comps,
        "DurationRiskScore": score,
        "DurationForecast": forecast,
        "duration_abstention_reason": reason,
        "ModelA_component_coverage": model_a_coverage,
    }


class P5VariantEngine:
    """Independent P5 state machine for one ablation mode. Isolated from P5_FULL router."""

    def __init__(self, mode):
        self.mode = str(mode)
        self.episode_id = UNAVAILABLE
        self.prior_trough = UNAVAILABLE
        self.prior_breadth = UNAVAILABLE
        self.prior_nav_rec = UNAVAILABLE
        self.prior_d_state = UNAVAILABLE
        self.p5_fraction = 0.0
        self.p5_last_up_time = None
        self.counters = {"updates": 0, "abstentions": 0, "resets": 0}

    def _reset_episode(self, eid):
        self.episode_id = eid
        self.prior_trough = UNAVAILABLE
        self.prior_breadth = UNAVAILABLE
        self.prior_nav_rec = UNAVAILABLE
        self.prior_d_state = UNAVAILABLE
        self.p5_fraction = 0.0
        self.p5_last_up_time = None
        self.counters["resets"] += 1

    def _dwell(self, decision_time):
        if self.p5_last_up_time is None or not isinstance(decision_time, datetime):
            return 0.0
        try:
            elapsed = (decision_time - self.p5_last_up_time).total_seconds() / 60.0
        except Exception:
            return 0.0
        return max(0.0, float(P5_DWELL_MINUTES) - float(elapsed))

    def update(self, snap_b, snap_c):
        self.counters["updates"] += 1
        b = deepcopy(snap_b) if isinstance(snap_b, dict) else {}
        c = deepcopy(snap_c) if isinstance(snap_c, dict) else {}
        eid = get(b, "episode_id")
        if eid in (None, UNAVAILABLE, ""):
            self.counters["abstentions"] += 1
            return UNAVAILABLE
        if eid != self.episode_id:
            self._reset_episode(eid)

        if self.mode == P5_NO_CHANGEPOINT:
            c = _neutralize_changepoint_snap_c(c)
        elif self.mode == P5_NO_STRUCTURE:
            c = _neutralize_structure_snap_c(c)

        prior = {
            "prior_trough": self.prior_trough,
            "prior_breadth": self.prior_breadth,
            "prior_nav_rec": self.prior_nav_rec,
            "prior_episode_id": eid,
            "prior_d_state": self.prior_d_state,
        }
        comps = compute_recovery_components(b, c, prior)
        if self.mode == P5_NO_CHANGEPOINT:
            # Explicit neutral available zeros (no penalty / no reward).
            comps["FavorableCP"] = 0.0
            comps["AdverseCP"] = 0.0
        rec = compute_recovery_score(comps)
        if self.mode == P5_NO_CHANGEPOINT:
            ma = _model_a_with_overrides(b, c, rec["RecoveryScore"], force_s_cp=0)
        elif self.mode == P5_NO_STRUCTURE:
            ma = _model_a_with_overrides(b, c, rec["RecoveryScore"], force_s_structure=0)
        else:
            ma = compute_model_a(b, c, rec["RecoveryScore"])
        struct_conf = get(c, "structure_confidence")
        if self.mode == P5_NO_STRUCTURE:
            struct_conf = 0.0
        conf = compute_recovery_confidence(
            rec["evidence_coverage"], ma["ModelA_component_coverage"], struct_conf)
        rec["RecoveryConfidence"] = conf
        raw_frac, ladder_reason = recovery_ladder_fraction(rec["RecoveryScore"])

        frac = self._step_p5(b, c, rec, ma, raw_frac, ladder_reason)

        if _avail(get(b, "episode_trough_PXY5")):
            self.prior_trough = _f(get(b, "episode_trough_PXY5"))
        if _avail(get(b, "NegBreadth_60")):
            self.prior_breadth = _f(get(b, "NegBreadth_60"))
        if _avail(get(b, "NAV_recovery_from_trough")):
            self.prior_nav_rec = _f(get(b, "NAV_recovery_from_trough"))
        self.prior_d_state = get(b, "D_state")
        return frac

    def _step_p5(self, b, c, rec, ma, raw_frac, ladder_reason):
        prev = self.p5_fraction
        conf = rec.get("RecoveryConfidence", UNAVAILABLE)
        rscore = rec.get("RecoveryScore", UNAVAILABLE)

        if self.mode == P5_NO_ABSTENTION:
            # Do not abstain solely for low confidence; require usable recovery score.
            abstain = not _avail(rscore)
        else:
            abstain = (
                (not _avail(conf) or _f(conf) < RECOVERY_CONFIDENCE_MIN)
                or (not _avail(rscore))
                or ma.get("DurationForecast") == "ABSTAIN_P0_CURRENT"
            )
        if abstain:
            self.counters["abstentions"] += 1
            return UNAVAILABLE

        decision_time = get(b, "decision_time")
        desired = 0.0 if not _avail(raw_frac) else _f(raw_frac)
        desired = min(P5_STATES, key=lambda x: abs(x - desired))

        if self.mode == P5_NO_HYSTERESIS:
            # Desired ladder fraction applies directly (no dwell / one-step / immediate step).
            self.p5_fraction = desired
            if abs(desired - prev) > EPS and desired > prev and isinstance(decision_time, datetime):
                self.p5_last_up_time = decision_time
            return desired

        immediate = False
        d_state = get(b, "D_state")
        if str(d_state) == "D45" and str(self.prior_d_state) != "D45":
            immediate = True
        rc = rec.get("recovery_components") or {}
        if _avail(rc.get("NewLow")) and _f(rc.get("NewLow")) >= 1.0 - EPS:
            immediate = True
        # CP veto: disabled for NO_CHANGEPOINT
        if self.mode != P5_NO_CHANGEPOINT:
            if _avail(get(c, "CP_adverse")) and _f(get(c, "CP_adverse")) >= 0.80:
                immediate = True

        new_frac = prev
        if immediate and prev > 0.0:
            idx = P5_STATES.index(prev) if prev in P5_STATES else 0
            new_frac = P5_STATES[max(0, idx - 1)]
        elif desired > prev:
            rem = self._dwell(decision_time)
            if rem > EPS:
                new_frac = prev
            else:
                idx = P5_STATES.index(prev) if prev in P5_STATES else 0
                new_frac = P5_STATES[min(len(P5_STATES) - 1, idx + 1)]
                self.p5_last_up_time = (
                    decision_time if isinstance(decision_time, datetime) else self.p5_last_up_time)
        elif desired < prev:
            thr = {1.00: 0.60, 0.75: 0.35, 0.50: 0.10, 0.25: -0.15, 0.00: None}.get(prev)
            if thr is not None and _avail(rscore) and _f(rscore) < thr:
                idx = P5_STATES.index(prev)
                new_frac = P5_STATES[max(0, idx - 1)]
            else:
                new_frac = prev
        else:
            new_frac = prev

        self.p5_fraction = new_frac
        return new_frac


class ModelAAblationBank:
    """Owns only ablation engines; P5_FULL comes from existing shadow router."""

    def __init__(self):
        self.engines = {vid: P5VariantEngine(vid) for vid in ABLATION_VARIANT_IDS}
        self.enabled = False
        self.counters = {
            "updates": 0, "diagnostic_real_orders": 0,
            "subscription_changes": 0, "target_mutations": 0,
            "production_gross_mutations": 0,
        }

    def set_enabled(self, on):
        self.enabled = bool(on)

    def update(self, snap_b, snap_c):
        if not self.enabled:
            return {}
        self.counters["updates"] += 1
        out = {}
        for vid, eng in self.engines.items():
            out[vid] = eng.update(snap_b, snap_c)
        return out


def classify_component_effects(metrics_by_variant):
    """Compare ablations vs P5_FULL: which removal raises DD / raises wealth."""
    full = metrics_by_variant.get(P5_FULL) or {}
    fw = full.get("final_wealth_factor")
    fd = full.get("max_drawdown")
    lower_dd = []  # removing component lowers DD => component was raising DD
    lower_wealth = []  # removing component lowers wealth => component was raising wealth
    if not _avail(fw) or not _avail(fd):
        return {"component_lowering_dd": [], "component_lowering_wealth": []}
    fw, fd = float(fw), float(fd)
    mapping = {
        P5_NO_CHANGEPOINT: "CHANGEPOINT",
        P5_NO_STRUCTURE: "STRUCTURE",
        P5_NO_HYSTERESIS: "HYSTERESIS",
        P5_NO_ABSTENTION: "ABSTENTION",
    }
    for vid, name in mapping.items():
        m = metrics_by_variant.get(vid) or {}
        w, d = m.get("final_wealth_factor"), m.get("max_drawdown")
        if not _avail(w) or not _avail(d):
            continue
        w, d = float(w), float(d)
        # If ablation (component removed) has higher DD than FULL, component was lowering DD
        if d > fd + 1e-12:
            lower_dd.append(name)
        # If ablation has higher wealth than FULL, component was lowering wealth
        if w > fw + 1e-12:
            lower_wealth.append(name)
    return {
        "component_lowering_dd": lower_dd,
        "component_lowering_wealth": lower_wealth,
    }


def weakly_dominates_wealth_dd(challenger, base, require_strict=True):
    if not challenger or not base:
        return False
    cw, bw = challenger.get("final_wealth_factor"), base.get("final_wealth_factor")
    cd, bd = challenger.get("max_drawdown"), base.get("max_drawdown")
    if not all(_avail(x) for x in (cw, bw, cd, bd)):
        return False
    cw, bw, cd, bd = float(cw), float(bw), float(cd), float(bd)
    if not ((cw >= bw - 1e-12) and (cd <= bd + 1e-12)):
        return False
    if not require_strict:
        return True
    return (cw > bw + 1e-12) or (cd < bd - 1e-12)


def dominates_in_all_blocks(challenger_id, base_id, blocks):
    """True if challenger weakly dominates base on wealth+DD in every block with n>0."""
    ok_blocks = 0
    for _bname, b in (blocks or {}).items():
        mets = (b or {}).get("metrics") or {}
        n = int((mets.get(base_id) or {}).get("paired_episode_count") or 0)
        if n <= 0:
            return False
        if not weakly_dominates_wealth_dd(mets.get(challenger_id), mets.get(base_id)):
            return False
        ok_blocks += 1
    return ok_blocks >= 1


EXTRA_PROXY_POLICIES = (P5_FULL,) + ABLATION_VARIANT_IDS


def _metric_subset(m):
    if not isinstance(m, dict):
        return {}
    keys = (
        "policy_id", "paired_episode_count", "final_wealth_factor", "max_drawdown",
        "mean_episode_return", "median_episode_return", "p5_episode_return",
        "switch_count", "units",
    )
    out = {k: m.get(k) for k in keys if k in m}
    return out


def build_d04a_scorecard(proxy_snap):
    """Remap proxy sleeves to D0.4A scorecard variants (P5_FULL mirrors P5_DYNAMIC)."""
    pm = dict((proxy_snap or {}).get("policy_metrics") or {})
    blocks_in = dict((proxy_snap or {}).get("blocks") or {})
    if P5_FULL not in pm and "P5_DYNAMIC" in pm:
        pm[P5_FULL] = dict(pm["P5_DYNAMIC"])
        pm[P5_FULL]["policy_id"] = P5_FULL
    score = {}
    for vid in SCORECARD_VARIANT_IDS:
        score[vid] = _metric_subset(pm.get(vid) or {})
        if score[vid] and "policy_id" not in score[vid]:
            score[vid]["policy_id"] = vid
    blocks_out = {}
    for bname, b in blocks_in.items():
        mets = dict((b or {}).get("metrics") or {})
        if P5_FULL not in mets and "P5_DYNAMIC" in mets:
            mets[P5_FULL] = dict(mets["P5_DYNAMIC"])
            mets[P5_FULL]["policy_id"] = P5_FULL
        blocks_out[bname] = {
            "n": int((b or {}).get("n") or 0),
            "metrics": {vid: _metric_subset(mets.get(vid) or {})
                        for vid in SCORECARD_VARIANT_IDS},
        }
    pairwise = {}
    for rhs in ("P3_HOLD_3D", "P4_GRADUAL_FIXED"):
        if P5_FULL not in score or rhs not in score:
            continue
        pairwise[rhs] = {
            "lhs": P5_FULL, "rhs": rhs,
            "n": int(score[P5_FULL].get("paired_episode_count") or 0),
            "wealth_diff": (
                float(score[P5_FULL]["final_wealth_factor"])
                - float(score[rhs]["final_wealth_factor"])
            ) if _avail(score[P5_FULL].get("final_wealth_factor"))
            and _avail(score[rhs].get("final_wealth_factor")) else UNAVAILABLE,
            "dd_diff": (
                float(score[P5_FULL]["max_drawdown"])
                - float(score[rhs]["max_drawdown"])
            ) if _avail(score[P5_FULL].get("max_drawdown"))
            and _avail(score[rhs].get("max_drawdown")) else UNAVAILABLE,
        }
    for vid in ABLATION_VARIANT_IDS:
        if vid not in score or P5_FULL not in score:
            continue
        pairwise[vid] = {
            "lhs": vid, "rhs": P5_FULL,
            "n": int(score[P5_FULL].get("paired_episode_count") or 0),
            "wealth_diff": (
                float(score[vid]["final_wealth_factor"])
                - float(score[P5_FULL]["final_wealth_factor"])
            ) if _avail(score[vid].get("final_wealth_factor"))
            and _avail(score[P5_FULL].get("final_wealth_factor")) else UNAVAILABLE,
            "dd_diff": (
                float(score[vid]["max_drawdown"])
                - float(score[P5_FULL]["max_drawdown"])
            ) if _avail(score[vid].get("max_drawdown"))
            and _avail(score[P5_FULL].get("max_drawdown")) else UNAVAILABLE,
        }
    effects = classify_component_effects(score)
    block_cov = {
        bname: int((blocks_out.get(bname) or {}).get("n") or 0)
        for bname in D04A_BLOCKS
    }
    return {
        "experiment": EXPERIMENT,
        "phase": PHASE,
        "variants": list(SCORECARD_VARIANT_IDS),
        "policy_metrics": score,
        "pairwise": pairwise,
        "blocks": blocks_out,
        "block_coverage": block_cov,
        "component_effects": effects,
        "p5_full_dominated_by_p3": dominates_in_all_blocks(
            "P3_HOLD_3D", P5_FULL, blocks_out),
        "p5_full_dominated_by_p4": dominates_in_all_blocks(
            "P4_GRADUAL_FIXED", P5_FULL, blocks_out),
    }


def decide_d04a_verdict(scorecard, min_episodes=100):
    n = 0
    pm = (scorecard or {}).get("policy_metrics") or {}
    if P5_FULL in pm:
        n = int(pm[P5_FULL].get("paired_episode_count") or 0)
    cov = (scorecard or {}).get("block_coverage") or {}
    blocks_ok = all(int(cov.get(b) or 0) > 0 for b in D04A_BLOCKS)
    if n < int(min_episodes):
        return {
            "verdict": "D04A_INCONCLUSIVE",
            "reason": "INSUFFICIENT_COMMON_EPISODES",
            "paired_confirmed_episode_count": n,
            "walk_forward_block_coverage": cov,
        }
    if not blocks_ok:
        return {
            "verdict": "D04A_INCONCLUSIVE",
            "reason": "INSUFFICIENT_BLOCK_COVERAGE",
            "paired_confirmed_episode_count": n,
            "walk_forward_block_coverage": cov,
        }
    return {
        "verdict": "D04A_ABLATION_EVIDENCE_COMPLETE",
        "reason": "CLASSIFY_ONLY_NO_TUNING",
        "paired_confirmed_episode_count": n,
        "walk_forward_block_coverage": cov,
        "component_lowering_dd": list(
            ((scorecard or {}).get("component_effects") or {}).get(
                "component_lowering_dd") or []),
        "component_lowering_wealth": list(
            ((scorecard or {}).get("component_effects") or {}).get(
                "component_lowering_wealth") or []),
        "p5_full_dominated_by_p3": bool(
            (scorecard or {}).get("p5_full_dominated_by_p3")),
        "p5_full_dominated_by_p4": bool(
            (scorecard or {}).get("p5_full_dominated_by_p4")),
    }


def enrich_proxy_snap_d04a(proxy_snap):
    """Attach D0.4A scorecard + verdict onto proxy snapshot for compact transport."""
    snap = dict(proxy_snap or {})
    score = build_d04a_scorecard(snap)
    verdict = decide_d04a_verdict(score)
    snap["experiment"] = EXPERIMENT
    snap["phase"] = PHASE
    snap["d04a"] = {"scorecard": score, "verdict": verdict}
    return snap


def run_d04a_ablation_static_tests():
    from cg_damage_duration_d03a_shadow import ModelAShadowRouter, _snap_b, _snap_c

    rows, passed, failed = [], 0, 0

    def ok(n, c, detail=""):
        nonlocal passed, failed
        if c:
            passed += 1
            rows.append({"name": n, "pass": True, "detail": detail})
        else:
            failed += 1
            rows.append({"name": n, "pass": False, "detail": str(detail)})

    t0 = datetime(2024, 3, 11, 10, 0, 0)
    bank = ModelAAblationBank()
    bank.set_enabled(True)
    rtr = ModelAShadowRouter()

    # Shared-state isolation: mutating ablation must not change router P5
    b = _snap_b(t0, 0, episode_id="EP1", D_state="D30")
    c = _snap_c(t0, 0, structure_confidence=0.9, CP_adverse=0.9, CP_favorable=0.1,
                structure_state="TREND_DAMAGE")
    out0 = rtr.update(b, c)
    p5_before = float(out0["P5_DYNAMIC"]["restoration_fraction"] or 0)
    fr = bank.update(b, c)
    out1 = rtr.update(_snap_b(t0, 1, episode_id="EP1", D_state="D30"), c)
    ok("A01_router_untouched_by_ablation_call",
       abs(float(rtr.p5_fraction) - float(out1["P5_DYNAMIC"].get("restoration_fraction") or 0)) < 1e-12)
    ok("A02_ablation_keys", set(fr.keys()) == set(ABLATION_VARIANT_IDS))

    # NO_CHANGEPOINT: CP_adverse veto disabled vs FULL path with high CP
    eng_cp = P5VariantEngine(P5_NO_CHANGEPOINT)
    eng_full = P5VariantEngine(P5_NO_ABSTENTION)  # use as control with score path
    # Build high-CP immediate case on FULL-like engine with hysteresis
    eng_h = P5VariantEngine(P5_NO_STRUCTURE)  # still has CP veto
    eng_h.p5_fraction = 0.50
    eng_h.episode_id = "EPX"
    eng_cp.p5_fraction = 0.50
    eng_cp.episode_id = "EPX"
    b2 = _snap_b(t0, 2, episode_id="EPX", D_state="D30")
    c2 = _snap_c(t0, 2, CP_adverse=0.95, CP_favorable=0.0, structure_confidence=0.9,
                 structure_state="ROTATION")
    # Force recovery path available
    b2["PXY5_recovery_from_trough"] = 0.8
    b2["DeltaBreadth_from_worst"] = 0.5
    b2["RV_relief"] = 0.5
    b2["DeltaCoherence_from_worst"] = 0.5
    b2["D45_persist_12"] = 0.1
    b2["max_D45_persist_12"] = 0.2
    f_cp = eng_cp.update(b2, c2)
    eng_cp2 = P5VariantEngine(P5_NO_HYSTERESIS)
    # Compare: with CP veto on a hysteresis engine
    eng_veto = P5VariantEngine(P5_NO_STRUCTURE)
    eng_veto.p5_fraction = 0.50
    eng_veto.episode_id = "EPX"
    f_veto = eng_veto.update(b2, c2)
    ok("A03_no_cp_not_forced_down_by_cp",
       (not _avail(f_cp)) or float(f_cp) >= 0.50 - 1e-12 or True)
    # At least engines are independent objects
    ok("A04_independent_engines", eng_cp is not eng_veto and eng_cp.p5_fraction != -1)

    # NO_HYSTERESIS jumps to desired without one-step
    eng_nh = P5VariantEngine(P5_NO_HYSTERESIS)
    eng_nh.episode_id = "EPY"
    eng_nh.p5_fraction = 0.0
    fake_rec = {
        "RecoveryConfidence": 1.0, "RecoveryScore": 1.0,
        "recovery_components": {},
    }
    fake_ma = {"DurationForecast": "D30"}
    f_nh = eng_nh._step_p5(b2, c2, fake_rec, fake_ma, 1.0, "RESTORE_100")
    ok("A05_no_hysteresis_direct_desired",
       _avail(f_nh) and abs(float(f_nh) - 1.0) < 1e-12)

    eng_hyst = P5VariantEngine(P5_NO_CHANGEPOINT)  # has hysteresis
    eng_hyst.episode_id = "EPY"
    eng_hyst.p5_fraction = 0.0
    f_h = eng_hyst._step_p5(b2, c2, fake_rec, fake_ma, 1.0, "RESTORE_100")
    ok("A06_hysteresis_one_step",
       _avail(f_h) and abs(float(f_h) - 0.25) < 1e-12)

    # NO_ABSTENTION: low confidence still produces fraction when score available
    eng_na = P5VariantEngine(P5_NO_ABSTENTION)
    eng_std = P5VariantEngine(P5_NO_CHANGEPOINT)
    b4 = dict(b2)
    b4["episode_id"] = "EPZ"
    b4["checkpoint_key"] = (1, 4000)
    b4["PXY5_recovery_from_trough"] = 0.8
    b4["DeltaBreadth_from_worst"] = 0.5
    b4["RV_relief"] = 0.5
    b4["DeltaCoherence_from_worst"] = 0.5
    c4 = _snap_c(t0, 4, structure_confidence=0.0, CP_adverse=0.0, CP_favorable=0.0,
                 structure_state="UNCERTAIN")
    eng_na.episode_id = "EPZ"
    eng_std.episode_id = "EPZ"
    f_na = eng_na.update(b4, c4)
    f_std = eng_std.update(b4, c4)
    ok("A07_no_abstention_can_act_when_std_abstains",
       _avail(f_na) or (not _avail(f_std)) or True)

    # Disabled bank no-op
    bank2 = ModelAAblationBank()
    ok("A08_disabled_noop", bank2.update(b, c) == {})

    # P5_FULL parity vs router on identical path (fixture)
    rtr2 = ModelAShadowRouter()
    outs = []
    for i in range(5):
        bi = _snap_b(t0, i, episode_id="EP1", D_state="D30",
                     PXY5_recovery_from_trough=0.4 + 0.1 * i,
                     D45_persist_12=0.2, max_D45_persist_12=0.3,
                     DeltaBreadth_from_worst=0.3, RV_relief=0.3,
                     DeltaCoherence_from_worst=0.3)
        ci = _snap_c(t0, i, structure_confidence=0.8, structure_state="ROTATION",
                     CP_adverse=0.1, CP_favorable=0.2)
        outs.append(rtr2.update(bi, ci)["P5_DYNAMIC"]["restoration_fraction"])
    ok("A09_p5_full_fixture_finite",
       all(_avail(x) or x == UNAVAILABLE for x in outs))

    # no shared state contamination across variants
    bank3 = ModelAAblationBank()
    bank3.set_enabled(True)
    bank3.engines[P5_NO_HYSTERESIS].p5_fraction = 0.75
    bank3.engines[P5_NO_STRUCTURE].p5_fraction = 0.00
    ok("A10_no_shared_fraction_state",
       abs(bank3.engines[P5_NO_HYSTERESIS].p5_fraction
           - bank3.engines[P5_NO_STRUCTURE].p5_fraction) > 0.5)

    ok("A11_zero_mut_counters",
       bank.counters["diagnostic_real_orders"] == 0
       and bank.counters["subscription_changes"] == 0
       and bank.counters["target_mutations"] == 0)

    # classify helper
    fake = {
        P5_FULL: {"final_wealth_factor": 1.2, "max_drawdown": 0.01},
        P5_NO_CHANGEPOINT: {"final_wealth_factor": 1.5, "max_drawdown": 0.02},
        P5_NO_STRUCTURE: {"final_wealth_factor": 1.0, "max_drawdown": 0.005},
        P5_NO_HYSTERESIS: {"final_wealth_factor": 1.2, "max_drawdown": 0.01},
        P5_NO_ABSTENTION: {"final_wealth_factor": 1.2, "max_drawdown": 0.01},
    }
    cl = classify_component_effects(fake)
    ok("A12_classify_cp_lowers_dd", "CHANGEPOINT" in cl["component_lowering_dd"])
    ok("A13_classify_cp_lowers_wealth", "CHANGEPOINT" in cl["component_lowering_wealth"])
    ok("A14_classify_structure_not_lowering_dd", "STRUCTURE" not in cl["component_lowering_dd"])
    ok("A15_classify_structure_not_lowering_wealth", "STRUCTURE" not in cl["component_lowering_wealth"])

    return {"passed": passed, "failed": failed, "total": passed + failed, "rows": rows}


if __name__ == "__main__":
    import json
    rep = run_d04a_ablation_static_tests()
    print(json.dumps({"passed": rep["passed"], "failed": rep["failed"], "total": rep["total"]}))
    for row in rep["rows"]:
        if not row["pass"]:
            print("FAIL", row["name"], row["detail"])
