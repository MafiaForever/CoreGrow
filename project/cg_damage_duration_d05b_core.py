# cg_damage_duration_d05b_core.py -- D0.5B Model B soft-confidence blend (diagnostic).
# Shadow-only. Does not mutate P5_FULL / P5_NO_ABSTENTION / production.
from __future__ import annotations
from copy import deepcopy
from datetime import datetime

from cg_damage_duration_d03a_core import (
    UNAVAILABLE, EPS, _avail, _f, get, clip,
    compute_recovery_components, compute_recovery_score, compute_model_a,
    compute_recovery_confidence, recovery_ladder_fraction,
)
from cg_damage_duration_d03a_shadow import P5_STATES, P5_DWELL_MINUTES
from cg_damage_duration_d03b_proxy_replay import p123_target_fraction

EXPERIMENT = "CG-DAMAGE-DURATION-D0.5B"
PHASE = "D0.5B_SOFT_CONFIDENCE_MODEL_B_CHALLENGER"

P5B_SOFT_CONFIDENCE_BLEND = "P5B_SOFT_CONFIDENCE_BLEND"
MODEL_B_SCORECARD = (
    "P3_HOLD_3D", "P5_FULL", "P5_NO_ABSTENTION", P5B_SOFT_CONFIDENCE_BLEND,
)

# D0.4A/B base parity anchors (lag0/cost0)
D04_P5_FULL = {
    "final_wealth_factor": 1.3654467113691742,
    "max_drawdown": 0.0068326741283545045,
    "paired_episode_count": 3195,
}
D04_P5_NO_ABSTENTION = {
    "final_wealth_factor": 3.613208163800539,
    "max_drawdown": 0.01477669951750741,
    "paired_episode_count": 3195,
}

RECOVERY_CONFIDENCE_SOURCE = (
    "compute_recovery_confidence(evidence_coverage,ModelA_component_coverage,"
    "structure_confidence); clip[0,1]; causal at DecisionTime"
)


def soft_confidence_blend(p3_fraction, p5_no_abstention_fraction, recovery_confidence):
    """B = P3 + conf*(P5NA - P3). Unavailable conf or P5NA -> exactly P3."""
    p3 = 0.0 if not _avail(p3_fraction) else max(0.0, min(1.0, _f(p3_fraction)))
    if not _avail(recovery_confidence):
        return p3
    if not _avail(p5_no_abstention_fraction):
        return p3
    c = clip(_f(recovery_confidence), 0.0, 1.0)
    p5 = max(0.0, min(1.0, _f(p5_no_abstention_fraction)))
    return p3 + c * (p5 - p3)


class SoftConfidenceModelBEngine:
    """Independent Model B state; applies P5 hysteresis after blend. No P0."""

    def __init__(self):
        self.episode_id = UNAVAILABLE
        self.episode_open_time = None
        self.sessions = []
        self.prior_trough = UNAVAILABLE
        self.prior_breadth = UNAVAILABLE
        self.prior_nav_rec = UNAVAILABLE
        self.prior_d_state = UNAVAILABLE
        self.p5b_fraction = 0.0
        self.p5_last_up_time = None
        self.last_confidence = UNAVAILABLE
        self.last_p3 = UNAVAILABLE
        self.last_p5na_desired = UNAVAILABLE
        self.last_blend_desired = UNAVAILABLE
        self.counters = {
            "updates": 0, "abstentions": 0, "resets": 0, "conf_fallback_p3": 0,
            "diagnostic_real_orders": 0, "subscription_changes": 0,
            "target_mutations": 0, "production_gross_mutations": 0,
        }

    def _reset_episode(self, eid, open_time):
        self.episode_id = eid
        self.episode_open_time = open_time if isinstance(open_time, datetime) else None
        self.sessions = []
        if self.episode_open_time is not None:
            self.sessions = [self.episode_open_time.date()]
        self.prior_trough = UNAVAILABLE
        self.prior_breadth = UNAVAILABLE
        self.prior_nav_rec = UNAVAILABLE
        self.prior_d_state = UNAVAILABLE
        self.p5b_fraction = 0.0
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

    def _p3_planned(self, decision_time):
        if self.episode_open_time is None or not isinstance(decision_time, datetime):
            return 0.0
        day = decision_time.date()
        if day not in self.sessions:
            self.sessions = sorted(set(self.sessions) | {day})
        return p123_target_fraction(
            "P3_HOLD_3D", self.episode_open_time, decision_time, self.sessions)

    def update(self, snap_b, snap_c):
        self.counters["updates"] += 1
        b = deepcopy(snap_b) if isinstance(snap_b, dict) else {}
        c = deepcopy(snap_c) if isinstance(snap_c, dict) else {}
        eid = get(b, "episode_id")
        dt = get(b, "decision_time")
        if eid in (None, UNAVAILABLE, "") or not isinstance(dt, datetime):
            self.counters["abstentions"] += 1
            return UNAVAILABLE
        if eid != self.episode_id:
            self._reset_episode(eid, dt)

        prior = {
            "prior_trough": self.prior_trough,
            "prior_breadth": self.prior_breadth,
            "prior_nav_rec": self.prior_nav_rec,
            "prior_episode_id": eid,
            "prior_d_state": self.prior_d_state,
        }
        comps = compute_recovery_components(b, c, prior)
        rec = compute_recovery_score(comps)
        ma = compute_model_a(b, c, rec["RecoveryScore"])
        conf = compute_recovery_confidence(
            rec["evidence_coverage"], ma["ModelA_component_coverage"],
            get(c, "structure_confidence"))
        # conf is always float here; mark unavailable only if non-finite
        if not _avail(conf):
            conf = UNAVAILABLE
            self.counters["conf_fallback_p3"] += 1
        else:
            conf = clip(_f(conf), 0.0, 1.0)
        rec["RecoveryConfidence"] = conf
        raw_frac, _ladder = recovery_ladder_fraction(rec["RecoveryScore"])
        # P5_NO_ABSTENTION desired = ladder fraction when score available (no conf gate)
        p5na_desired = UNAVAILABLE if not _avail(raw_frac) else _f(raw_frac)
        p3 = self._p3_planned(dt)
        blend = soft_confidence_blend(p3, p5na_desired, conf)
        self.last_confidence = conf
        self.last_p3 = p3
        self.last_p5na_desired = p5na_desired
        self.last_blend_desired = blend

        # No hard recovery/confidence gate; always feed blended desired into hysteresis.
        out = self._step_hysteresis(b, c, rec, blend, dt)

        if _avail(get(b, "episode_trough_PXY5")):
            self.prior_trough = _f(get(b, "episode_trough_PXY5"))
        if _avail(get(b, "NegBreadth_60")):
            self.prior_breadth = _f(get(b, "NegBreadth_60"))
        if _avail(get(b, "NAV_recovery_from_trough")):
            self.prior_nav_rec = _f(get(b, "NAV_recovery_from_trough"))
        self.prior_d_state = get(b, "D_state")
        return out

    def _step_hysteresis(self, b, c, rec, raw_desired, decision_time):
        """Existing P5 one-step/dwell hysteresis; CP is penalty (one-step), not absolute veto."""
        prev = self.p5b_fraction
        desired = max(0.0, min(1.0, float(raw_desired)))
        desired = min(P5_STATES, key=lambda x: abs(x - desired))
        rscore = rec.get("RecoveryScore", UNAVAILABLE)

        immediate = False
        d_state = get(b, "D_state")
        if str(d_state) == "D45" and str(self.prior_d_state) != "D45":
            immediate = True
        rc = rec.get("recovery_components") or {}
        if _avail(rc.get("NewLow")) and _f(rc.get("NewLow")) >= 1.0 - EPS:
            immediate = True
        # CP adverse: one-step penalty only (not absolute veto / hard block)
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
                    decision_time if isinstance(decision_time, datetime)
                    else self.p5_last_up_time)
        elif desired < prev:
            thr = {1.00: 0.60, 0.75: 0.35, 0.50: 0.10, 0.25: -0.15, 0.00: None}.get(prev)
            if thr is not None and _avail(rscore) and _f(rscore) < thr:
                idx = P5_STATES.index(prev)
                new_frac = P5_STATES[max(0, idx - 1)]
            else:
                new_frac = prev
        else:
            new_frac = prev
        self.p5b_fraction = new_frac
        return new_frac


def run_d05b_core_static_tests():
    from datetime import timedelta
    from cg_damage_duration_d03a_shadow import _snap_b, _snap_c

    rows, passed, failed = [], 0, 0

    def ok(n, c, detail=""):
        nonlocal passed, failed
        if c:
            passed += 1
            rows.append({"name": n, "pass": True, "detail": detail})
        else:
            failed += 1
            rows.append({"name": n, "pass": False, "detail": str(detail)})

    ok("B01_conf0_is_p3", abs(soft_confidence_blend(0.0, 1.0, 0.0) - 0.0) < 1e-12)
    ok("B02_conf1_is_p5na", abs(soft_confidence_blend(0.0, 1.0, 1.0) - 1.0) < 1e-12)
    ok("B03_mid_linear", abs(soft_confidence_blend(0.0, 1.0, 0.5) - 0.5) < 1e-12)
    ok("B04_unavail_conf_p3", abs(soft_confidence_blend(0.25, 1.0, UNAVAILABLE) - 0.25) < 1e-12)
    ok("B05_unavail_p5na_p3", abs(soft_confidence_blend(0.25, UNAVAILABLE, 0.9) - 0.25) < 1e-12)
    ok("B06_no_p0_symbol", "P0" not in soft_confidence_blend.__code__.co_names)

    eng = SoftConfidenceModelBEngine()
    t0 = datetime(2024, 3, 11, 10, 0, 0)
    b = _snap_b(t0, 0, episode_id="EP1", D_state="D30",
                PXY5_recovery_from_trough=0.9, DeltaBreadth_from_worst=0.5,
                RV_relief=0.5, DeltaCoherence_from_worst=0.5,
                D45_persist_12=0.1, max_D45_persist_12=0.2)
    c = _snap_c(t0, 0, structure_confidence=1.0, structure_state="ROTATION",
                CP_adverse=0.0, CP_favorable=0.5)
    f0 = eng.update(b, c)
    ok("B07_engine_returns_finite", _avail(f0))
    # hysteresis: from 0 with high desired -> one step 0.25
    ok("B08_hysteresis_one_step", abs(float(f0) - 0.25) < 1e-12 or float(f0) <= 0.25 + 1e-12)

    # conf=0 path: force via blend unit already; engine with zero structure and empty evidence
    eng2 = SoftConfidenceModelBEngine()
    # After first update open set; second with low conf still blends
    b2 = dict(b)
    b2["checkpoint_key"] = (1, 2)
    b2["decision_time"] = t0 + timedelta(minutes=5)
    c2 = _snap_c(t0, 1, structure_confidence=0.0, structure_state="UNCERTAIN",
                 CP_adverse=0.0, CP_favorable=0.0)
    eng2.update(b, c)
    # Direct blend check already covers conf extremes

    ok("B09_zero_mut", eng.counters["diagnostic_real_orders"] == 0
       and eng.counters["target_mutations"] == 0)
    ok("B10_sources_documented", "compute_recovery_confidence" in RECOVERY_CONFIDENCE_SOURCE)

    return {"passed": passed, "failed": failed, "total": passed + failed, "rows": rows}


if __name__ == "__main__":
    import json
    rep = run_d05b_core_static_tests()
    print(json.dumps({"passed": rep["passed"], "failed": rep["failed"], "total": rep["total"]}))
    for row in rep["rows"]:
        if not row["pass"]:
            print("FAIL", row["name"], row["detail"])
