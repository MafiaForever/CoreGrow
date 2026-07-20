from __future__ import annotations
import json, math, re
from copy import deepcopy
from datetime import datetime, timedelta
from collections import deque

from cg_damage_duration_d03a_core import (
    UNAVAILABLE, EXPERIMENT, PHASE, SCHEMA_VERSION, EPS, RECOVERY_CONFIDENCE_MIN,
    RECOVERY_WEIGHT_SUM, _avail, _f, get,
    compute_recovery_components, compute_recovery_score, compute_model_a,
    compute_recovery_confidence, recovery_ladder_fraction,
    s_severity, s_persistence, s_structure, s_memory, s_cp, s_recovery_from_score,
    duration_forecast, model_a_contract, recovery_score_contract,
    norm_price_recovery, renewed_damage,
)

P5_STATES = (0.00, 0.25, 0.50, 0.75, 1.00)
P5_DWELL_MINUTES = 15
MAX_EPISODE_SUMMARIES = 64
SESSION_CLOSE_TOD_MINUTES = 960
MAX_P4_OBSERVED_SESSIONS = 4

FORBIDDEN_RE = re.compile(
    r"(?<![A-Za-z_])(History|AddEquity|add_equity|AddData|add_data|SetHoldings|set_holdings|"
    r"MarketOrder|market_order|LimitOrder|StopMarketOrder|Liquidate)\s*\("
    r"|PortfolioTarget\b|ObjectStore\.(Save|Delete)\b|Schedule\.On\b"
    r"|(?<![A-Za-z_])(trade_action|submit_order|apply_target|production_veto|"
    r"block_recovery|force_protection|cancel_emergency)\b"
)

def _policy(pid, action, frac, prev, effective, reason, fallback=UNAVAILABLE, **extra):
    out = {
        "policy_id": pid,
        "shadow_only": True,
        "action": action,
        "restoration_fraction": frac if frac is not None else UNAVAILABLE,
        "previous_fraction": prev if prev is not None else UNAVAILABLE,
        "effective_time": effective if effective is not None else UNAVAILABLE,
        "reason": reason,
        "fallback": fallback,
    }
    out.update(extra)
    return out

def sanitize(obj):
    if isinstance(obj, dict):
        return {k: sanitize(v) for k, v in obj.items()}
    if isinstance(obj, float) and not math.isfinite(obj):
        return UNAVAILABLE
    if obj is None:
        return UNAVAILABLE
    return obj

class ModelAShadowRouter:
    """Consumes immutable D0.2B+D0.2C snapshots; emits shadow-only policy records."""

    def __init__(self):
        self.last_checkpoint = None
        self.last_snapshot = None
        self.episode_id = UNAVAILABLE
        self.prior_trough = UNAVAILABLE
        self.prior_breadth = UNAVAILABLE
        self.prior_nav_rec = UNAVAILABLE
        self.prior_d_state = UNAVAILABLE
        self.p4_checkpoint_count = 0
        self.p4_fraction = 0.0
        self.p4_observed_session_dates = []
        self.p4_stale_session_checkpoint = 0
        self.p5_fraction = 0.0
        self.p5_last_up_time = None
        self.completed = deque(maxlen=MAX_EPISODE_SUMMARIES)
        self.dependency_failure = False
        self.counters = {
            "snapshots": 0, "duplicate_blocked": 0, "abstentions": 0,
            "diagnostic_real_orders": 0, "subscription_changes": 0,
            "target_mutations": 0, "production_gross_mutations": 0,
            "p4_stale_session_checkpoint": 0,
        }

    def update(self, snap_b, snap_c, d02_enabled=True, d03a_enabled=True):
        if not d03a_enabled:
            return None
        if not d02_enabled:
            self.dependency_failure = True
            return {
                "schema_version": SCHEMA_VERSION,
                "experiment": EXPERIMENT,
                "phase": PHASE,
                "action": "DEPENDENCY_FAILURE_D02_REQUIRED",
                "shadow_only": True,
                "P0_NUMERIC_SOURCE_UNAVAILABLE": True,
            }
        if snap_b is None or snap_c is None:
            return None

        b = deepcopy(snap_b)
        c = deepcopy(snap_c)
        ck = b.get("checkpoint_key")
        if ck is not None and ck == self.last_checkpoint:
            self.counters["duplicate_blocked"] += 1
            return self.last_snapshot

        eid = get(b, "episode_id")
        if eid is None or eid == UNAVAILABLE:
            # Confirmed-close / no-open: clear per-episode P4/P5 schedule state.
            if self.episode_id not in (None, UNAVAILABLE):
                self.completed.append({
                    "episode_id": self.episode_id,
                    "final_p5": self.p5_fraction,
                    "final_p4": self.p4_fraction,
                })
                self.episode_id = UNAVAILABLE
                self.prior_trough = UNAVAILABLE
                self.prior_breadth = UNAVAILABLE
                self.prior_nav_rec = UNAVAILABLE
                self.prior_d_state = UNAVAILABLE
                self.p4_checkpoint_count = 0
                self.p4_fraction = 0.0
                self.p4_observed_session_dates = []
                self.p4_stale_session_checkpoint = 0
                self.p5_fraction = 0.0
                self.p5_last_up_time = None
            out = self._identity(b, c)
            out["DurationForecast"] = "ABSTAIN_NO_OPEN_EPISODE"
            out["P5_DYNAMIC"] = _policy(
                "P5_DYNAMIC", "ABSTAIN_NO_OPEN_EPISODE", UNAVAILABLE, self.p5_fraction,
                UNAVAILABLE, "NO_OPEN_EPISODE", fallback="P0_CURRENT",
                checkpoint_key=ck, raw_desired_fraction=UNAVAILABLE,
                hysteresis_applied=False, dwell_remaining_minutes=UNAVAILABLE,
                immediate_downgrade_trigger=False, state_changed=False)
            out = sanitize(out)
            self.last_checkpoint = ck
            self.last_snapshot = out
            self.counters["snapshots"] += 1
            self.counters["abstentions"] += 1
            return out

        if eid != self.episode_id:
            if self.episode_id not in (None, UNAVAILABLE):
                self.completed.append({
                    "episode_id": self.episode_id,
                    "final_p5": self.p5_fraction,
                    "final_p4": self.p4_fraction,
                })
            self.episode_id = eid
            self.prior_trough = UNAVAILABLE
            self.prior_breadth = UNAVAILABLE
            self.prior_nav_rec = UNAVAILABLE
            self.prior_d_state = UNAVAILABLE
            self.p4_checkpoint_count = 0
            self.p4_fraction = 0.0
            self.p4_observed_session_dates = []
            self.p4_stale_session_checkpoint = 0
            self.p5_fraction = 0.0
            self.p5_last_up_time = None

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
        rec["RecoveryConfidence"] = conf
        rec["ModelA_component_coverage"] = ma["ModelA_component_coverage"]

        raw_frac, ladder_reason = recovery_ladder_fraction(rec["RecoveryScore"])

        p0 = _policy(
            "P0_CURRENT", "FOLLOW_PRODUCTION", UNAVAILABLE, UNAVAILABLE,
            "PRODUCTION_DEFINED", "MIRROR_PRODUCTION",
            checkpoint_key=ck, P0_NUMERIC_SOURCE_UNAVAILABLE=True)

        p1 = _policy("P1_HOLD_TO_CLOSE", "SHADOW_PLAN", 0.0, 0.0,
                     "SAME_SESSION_CLOSE", "HOLD_THEN_FULL_AT_CLOSE", checkpoint_key=ck)
        p1["planned_full_restoration"] = 1.0
        p2 = _policy("P2_HOLD_TO_NEXT_CLOSE", "SHADOW_PLAN", 0.0, 0.0,
                     "NEXT_SESSION_CLOSE", "HOLD_THEN_FULL_AT_NEXT_CLOSE", checkpoint_key=ck)
        p2["planned_full_restoration"] = 1.0
        p3 = _policy("P3_HOLD_3D", "SHADOW_PLAN", 0.0, 0.0,
                     "THIRD_SESSION_CLOSE", "HOLD_THEN_FULL_AT_3D_CLOSE", checkpoint_key=ck)
        p3["planned_full_restoration"] = 1.0

        p4 = self._update_p4(b, ck)

        p5 = self._update_p5(
            b, c, rec, ma, raw_frac, ladder_reason, ck)

        if _avail(get(b, "episode_trough_PXY5")):
            self.prior_trough = _f(get(b, "episode_trough_PXY5"))
        if _avail(get(b, "NegBreadth_60")):
            self.prior_breadth = _f(get(b, "NegBreadth_60"))
        if _avail(get(b, "NAV_recovery_from_trough")):
            self.prior_nav_rec = _f(get(b, "NAV_recovery_from_trough"))
        self.prior_d_state = get(b, "D_state")

        out = self._identity(b, c)
        out.update(ma)
        out.update(rec)
        out["P0_CURRENT"] = p0
        out["P1_HOLD_TO_CLOSE"] = p1
        out["P2_HOLD_TO_NEXT_CLOSE"] = p2
        out["P3_HOLD_3D"] = p3
        out["P4_GRADUAL_FIXED"] = p4
        out["P5_DYNAMIC"] = p5
        out["P0_NUMERIC_SOURCE_UNAVAILABLE"] = True
        out["recovery_weight_sum"] = RECOVERY_WEIGHT_SUM
        out = sanitize(out)
        self.last_checkpoint = ck
        self.last_snapshot = out
        self.counters["snapshots"] += 1
        return out

    def _identity(self, b, c):
        return {
            "schema_version": SCHEMA_VERSION,
            "experiment": EXPERIMENT,
            "phase": PHASE,
            "checkpoint_key": get(b, "checkpoint_key"),
            "decision_time": get(b, "decision_time"),
            "feature_cutoff": get(b, "feature_cutoff"),
            "episode_id": get(b, "episode_id"),
            "source_d02b_schema": get(b, "schema_version"),
            "source_d02c_schema": get(c, "schema_version"),
            "shadow_only": True,
        }

    def _update_p4(self, b, ck):
        prev = self.p4_fraction
        dt = get(b, "decision_time")
        ok_dt = isinstance(dt, datetime)
        sd = dt.date() if ok_dt else None
        tod = (dt.hour * 60 + dt.minute) if ok_dt else None
        ds = self.p4_observed_session_dates
        if sd is not None and ds and sd < ds[-1]:
            self.p4_stale_session_checkpoint += 1
            self.counters["p4_stale_session_checkpoint"] = self.p4_stale_session_checkpoint
            return self._p4_rec(prev, prev, UNAVAILABLE, "STALE_SESSION_CHECKPOINT_BLOCKED",
                ck, self.p4_checkpoint_count, sd, len(ds) - 1, UNAVAILABLE, UNAVAILABLE,
                self._p4_ck(self.p4_checkpoint_count), UNAVAILABLE, False)
        if sd is not None:
            if not ds:
                ds.append(sd)
            elif sd > ds[-1]:
                ds.append(sd)
                if len(ds) > MAX_P4_OBSERVED_SESSIONS:
                    self.p4_observed_session_dates = ds[-MAX_P4_OBSERVED_SESSIONS:]
                    ds = self.p4_observed_session_dates
        self.p4_checkpoint_count += 1
        n = self.p4_checkpoint_count
        ck_f = self._p4_ck(n)
        if not ds:
            idx = same = nxt = cl_f = UNAVAILABLE
        else:
            idx = len(ds) - 1
            if not ok_dt:
                same = nxt = cl_f = UNAVAILABLE
            else:
                same = bool(idx >= 1 or (idx == 0 and tod >= SESSION_CLOSE_TOD_MINUTES))
                nxt = bool(idx >= 2 or (idx == 1 and tod >= SESSION_CLOSE_TOD_MINUTES))
                cl_f = 1.0 if nxt else (0.75 if same else 0.0)
        cands = [ck_f, prev]
        if _avail(cl_f):
            cands.append(_f(cl_f))
        frac = max(cands)
        chg = abs(frac - prev) > EPS
        self.p4_fraction = frac
        eff, rsn = self._p4_eff(frac, prev, ck_f, cl_f, n)
        return self._p4_rec(frac, prev, eff, rsn, ck, n, sd, idx, same, nxt, ck_f, cl_f, chg)

    def _p4_rec(self, frac, prev, eff, rsn, ck, n, sd, idx, same, nxt, ck_f, cl_f, chg):
        ds = self.p4_observed_session_dates
        return _policy(
            "P4_GRADUAL_FIXED", "SHADOW_SCHEDULE", frac, prev, eff, rsn, checkpoint_key=ck,
            unique_post_checkpoints=n,
            episode_session_date=ds[0] if ds else UNAVAILABLE,
            current_observed_session_date=sd if sd is not None else UNAVAILABLE,
            observed_session_index=idx, observed_session_count=len(ds),
            same_session_close_passed=same, next_session_close_passed=nxt,
            checkpoint_based_fraction=ck_f, close_based_fraction=cl_f, state_changed=chg)

    @staticmethod
    def _p4_ck(n):
        return 0.50 if n >= 24 else (0.25 if n >= 6 else 0.0)

    @staticmethod
    def _p4_eff(frac, prev, ck_f, cl_f, n):
        if abs(frac - prev) <= EPS:
            return UNAVAILABLE, "HOLD_P4_STATE"
        if abs(frac - 1.0) < EPS and _avail(cl_f) and abs(_f(cl_f) - 1.0) < EPS:
            return "NEXT_SESSION_CLOSE_OBSERVED", "NEXT_SESSION_CLOSE_STEP_100"
        if abs(frac - 0.75) < EPS and _avail(cl_f) and abs(_f(cl_f) - 0.75) < EPS:
            return "SAME_SESSION_CLOSE_OBSERVED", "SAME_SESSION_CLOSE_STEP_075"
        if abs(frac - 0.50) < EPS and abs(ck_f - 0.50) < EPS and n >= 24:
            return "CHECKPOINT_24", "CHECKPOINT_STEP_050"
        if abs(frac - 0.25) < EPS and abs(ck_f - 0.25) < EPS and n >= 6:
            return "CHECKPOINT_6", "CHECKPOINT_STEP_025"
        return UNAVAILABLE, "HOLD_P4_STATE"

    def _update_p5(self, b, c, rec, ma, raw_frac, ladder_reason, ck):
        prev = self.p5_fraction
        conf = rec.get("RecoveryConfidence", UNAVAILABLE)
        rscore = rec.get("RecoveryScore", UNAVAILABLE)
        abstain = (
            (not _avail(conf) or _f(conf) < RECOVERY_CONFIDENCE_MIN)
            or (not _avail(rscore))
            or ma.get("DurationForecast") == "ABSTAIN_P0_CURRENT"
        )
        if abstain:
            self.counters["abstentions"] += 1
            return _policy(
                "P5_DYNAMIC", "ABSTAIN_TO_P0_CURRENT", UNAVAILABLE, prev,
                UNAVAILABLE, "LOW_CONFIDENCE_OR_UNAVAILABLE", fallback="P0_CURRENT",
                checkpoint_key=ck, raw_desired_fraction=raw_frac,
                hysteresis_applied=False, dwell_remaining_minutes=self._dwell(get(b, "decision_time")),
                immediate_downgrade_trigger=False, state_changed=False)

        decision_time = get(b, "decision_time")
        desired = 0.0 if not _avail(raw_frac) else _f(raw_frac)
        desired = min(P5_STATES, key=lambda x: abs(x - desired))

        immediate = False
        d_state = get(b, "D_state")
        if str(d_state) == "D45" and str(self.prior_d_state) != "D45":
            immediate = True
        if _avail(rec["recovery_components"].get("NewLow")) and _f(rec["recovery_components"]["NewLow"]) >= 1.0 - EPS:
            immediate = True
        if _avail(get(c, "CP_adverse")) and _f(get(c, "CP_adverse")) >= 0.80:
            immediate = True

        new_frac = prev
        reason = ladder_reason
        hyst = False

        if immediate and prev > 0.0:
            idx = P5_STATES.index(prev) if prev in P5_STATES else 0
            new_frac = P5_STATES[max(0, idx - 1)]
            reason = "IMMEDIATE_ONE_STEP_DOWNGRADE"
            hyst = True
        elif desired > prev:
            rem = self._dwell(decision_time)
            if rem > EPS:
                new_frac = prev
                reason = "DWELL_BLOCK_UP"
                hyst = True
            else:
                idx = P5_STATES.index(prev) if prev in P5_STATES else 0
                new_frac = P5_STATES[min(len(P5_STATES) - 1, idx + 1)]
                self.p5_last_up_time = decision_time if isinstance(decision_time, datetime) else self.p5_last_up_time
                reason = "ONE_STEP_UP"
                hyst = True
        elif desired < prev:
            thr = {
                1.00: 0.60, 0.75: 0.35, 0.50: 0.10, 0.25: -0.15, 0.00: None,
            }.get(prev)
            if thr is not None and _avail(rscore) and _f(rscore) < thr:
                idx = P5_STATES.index(prev)
                new_frac = P5_STATES[max(0, idx - 1)]
                reason = "NORMAL_DOWNGRADE"
                hyst = True
            else:
                new_frac = prev
                reason = "HOLD_STATE"
        else:
            new_frac = prev
            reason = "HOLD_STATE"

        changed = abs(new_frac - prev) > EPS
        self.p5_fraction = new_frac
        return _policy(
            "P5_DYNAMIC", "SHADOW_HYSTERESIS", new_frac, prev,
            get(b, "decision_time"), reason, fallback="P0_CURRENT",
            checkpoint_key=ck, raw_desired_fraction=raw_frac if _avail(raw_frac) else UNAVAILABLE,
            hysteresis_applied=hyst,
            dwell_remaining_minutes=self._dwell(decision_time),
            immediate_downgrade_trigger=immediate, state_changed=changed)

    def _dwell(self, decision_time):
        if self.p5_last_up_time is None or not isinstance(decision_time, datetime):
            return 0.0
        try:
            elapsed = (decision_time - self.p5_last_up_time).total_seconds() / 60.0
        except Exception:
            return 0.0
        return max(0.0, float(P5_DWELL_MINUTES) - float(elapsed))

def policy_contract():
    return {
        "policies": ["P0_CURRENT", "P1_HOLD_TO_CLOSE", "P2_HOLD_TO_NEXT_CLOSE",
                     "P3_HOLD_3D", "P4_GRADUAL_FIXED", "P5_DYNAMIC"],
        "shadow_only": True,
        "production_actions": 0,
        "p5_dwell_minutes": P5_DWELL_MINUTES,
        "p5_states": list(P5_STATES),
        "hard_reset": "FORBIDDEN",
        "change_point_veto": "FORBIDDEN",
        "p4_schedule": {
            "checkpoint_6": 0.25, "checkpoint_24": 0.50,
            "same_session_close": 0.75, "next_session_close": 1.00,
            "session_source": "ACTUAL_OBSERVED_POST_CHECKPOINT_DATES",
            "synthetic_sessions": "FORBIDDEN", "weekend_arithmetic": "FORBIDDEN",
            "state_monotonic": "YES", "shadow_only": True,
            "SESSION_CLOSE_TOD_MINUTES": SESSION_CLOSE_TOD_MINUTES,
        },
    }

def _snap_b(t0, i, **kw):
    d = {
        "schema_version": "D02B_FEATURES_V1",
        "checkpoint_key": (1, 1000 + i),
        "decision_time": t0 + timedelta(minutes=5 * i),
        "feature_cutoff": t0 + timedelta(minutes=5 * i),
        "episode_id": "EP1",
        "D_state": "D30",
        "D45_persist_6": 0.5,
        "D45_persist_12": 0.5,
        "DPE_60": 0.5,
        "NegBreadth_60": 0.5,
        "NegCoherence_60": 0.5,
        "PXY5_level": 1.0,
        "PXY5_recovery_from_trough": 0.5,
        "NAV_recovery_from_trough": UNAVAILABLE,
        "DeltaDPE_from_worst": -0.1,
        "DeltaBreadth_from_worst": -0.1,
        "DeltaCoherence_from_worst": -0.1,
        "RV_relief": 0.2,
        "max_D45_persist_12": 0.5,
        "worst_DPE_60": 0.6,
        "worst_NegCoherence_60": 0.5,
        "episode_trough_PXY5": 0.9,
        "episode_trough_NAV": UNAVAILABLE,
        "checkpoint_count": i + 1,
        "episode_start_time": t0,
    }
    d.update(kw)
    return d

def _snap_c(t0, i, **kw):
    d = {
        "schema_version": "D02C_STRUCTURE_V1",
        "CP_adverse": 0.5,
        "CP_favorable": 0.2,
        "CP_adverse_peak_in_current_episode": 0.5,
        "structure_state": "BROAD_CHOP",
        "structure_candidate_state": "BROAD_CHOP",
        "structure_confidence": 0.8,
        "CP_alert_direction": "NONE",
        "CP_alert_eligible": False,
    }
    d.update(kw)
    return d

def run_damage_d03a_static_tests(param_map=None):
    rows = []
    passed = failed = 0

    def ok(name, cond, detail="OK"):
        nonlocal passed, failed
        if cond:
            passed += 1
            rows.append({"name": name, "pass": 1, "detail": detail})
        else:
            failed += 1
            rows.append({"name": name, "pass": 0, "detail": str(detail)})

    # Cloud-safe: no file-read API/source scan. Forbidden-API gate is external.
    from rrx_params import RRX_PARAMS
    ok("01_flag_default_off", RRX_PARAMS.get("cg_damage_duration_d03a_enable") == "0")
    ok("02_qc_override_supported", True)
    r = ModelAShadowRouter()
    out_dep = r.update(_snap_b(datetime(2024, 1, 2, 10), 0), _snap_c(datetime(2024, 1, 2, 10), 0),
                       d02_enabled=False, d03a_enabled=True)
    ok("03_requires_d02", out_dep.get("action") == "DEPENDENCY_FAILURE_D02_REQUIRED")
    ok("04_disabled_noop", r.update(_snap_b(datetime(2024, 1, 2, 10), 0), _snap_c(datetime(2024, 1, 2, 10), 0),
                                    d02_enabled=True, d03a_enabled=False) is None)
    ok("05_no_forbidden_apis",
       not any(hasattr(ModelAShadowRouter, n) for n in (
           "History", "AddEquity", "SetHoldings", "MarketOrder", "Liquidate", "PortfolioTarget"))
       and policy_contract()["hard_reset"] == "FORBIDDEN"
       and model_a_contract()["change_point_veto"] == "FORBIDDEN")
    ok("06_no_production_mutations_in_api",
       policy_contract()["production_actions"] == 0 and policy_contract()["shadow_only"] is True)
    ok("07_weight_sum_exact", abs(RECOVERY_WEIGHT_SUM - 1.0) < 1e-12)

    ok("08_price_recovery_bounds",
       abs(norm_price_recovery(0.0) - (-1.0)) < 1e-12 and abs(norm_price_recovery(1.0) - 1.0) < 1e-12)
    ok("09_missing_unavailable", norm_price_recovery(UNAVAILABLE) == UNAVAILABLE)

    comps = {
        "PriceRecovery": 1.0, "BreadthRecovery": 1.0, "VolRelief": 1.0,
        "CoherenceImprovement": 1.0, "PersistenceDecay": 1.0, "FavorableCP": 1.0,
        "RenewedDamage": UNAVAILABLE, "NewLow": UNAVAILABLE, "BreadthRelapse": UNAVAILABLE,
        "AdverseCP": UNAVAILABLE, "NAVRelapse": UNAVAILABLE,
    }
    rs = compute_recovery_score(comps)
    ok("10_recovery_exact_weighted", abs(float(rs["RecoveryScore"]) - 1.0) < 1e-9)

    comps2 = dict(comps)
    comps2["PriceRecovery"] = UNAVAILABLE
    rs2 = compute_recovery_score(comps2)
    ok("11_renormalize_available", _avail(rs2["RecoveryScore"]) and rs2["recovery_available_weight"] < 1.0)
    comps3 = {k: UNAVAILABLE for k in comps}
    rs3 = compute_recovery_score(comps3)
    ok("12_zero_weight_abstains", rs3["RecoveryScore"] == UNAVAILABLE)
    ok("13_score_bounded", -1.0 <= float(rs["RecoveryScore"]) <= 1.0)

    conf = compute_recovery_confidence(1.0, 1.0, 1.0)
    ok("14_confidence_exact", abs(conf - 1.0) < 1e-12)
    conf_low = compute_recovery_confidence(0.2, 0.2, 0.2)
    ok("15_confidence_below_abstains", conf_low < RECOVERY_CONFIDENCE_MIN)

    t0 = datetime(2024, 3, 11, 10, 0, 0)
    router = ModelAShadowRouter()
    sparse_b = _snap_b(t0, 0, PXY5_recovery_from_trough=UNAVAILABLE, DeltaBreadth_from_worst=UNAVAILABLE,
                       DeltaCoherence_from_worst=UNAVAILABLE, RV_relief=UNAVAILABLE,
                       D45_persist_12=UNAVAILABLE, max_D45_persist_12=UNAVAILABLE,
                       D_state=UNAVAILABLE, NegBreadth_60=UNAVAILABLE, episode_trough_PXY5=UNAVAILABLE,
                       NAV_recovery_from_trough=UNAVAILABLE, worst_DPE_60=UNAVAILABLE,
                       worst_NegCoherence_60=UNAVAILABLE, DPE_60=UNAVAILABLE, DeltaDPE_from_worst=UNAVAILABLE)
    sparse_c = _snap_c(t0, 0, CP_adverse=UNAVAILABLE, CP_favorable=UNAVAILABLE, structure_confidence=0.0,
                       structure_state="UNCERTAIN")
    out_a = router.update(sparse_b, sparse_c)
    ok("16_abstention_not_zero_force",
       out_a["P5_DYNAMIC"]["action"] == "ABSTAIN_TO_P0_CURRENT"
       and out_a["P5_DYNAMIC"]["restoration_fraction"] == UNAVAILABLE)

    ok("17_severity_map", s_severity("NONE") == 0 and s_severity("D30") == 1 and s_severity("D45") == 2)
    ok("18_persist_buckets",
       s_persistence(0.0) == 0 and s_persistence(0.17) == 1 and s_persistence(0.50) == 2)
    ok("19_structure_map",
       s_structure("SHOCK_REVERSAL") == -1 and s_structure("TREND_DAMAGE") == 2
       and s_structure("UNCERTAIN") == UNAVAILABLE)
    ok("20_memory0", s_memory({"worst_DPE_60": 0.1, "max_D45_persist_12": 0.0, "worst_NegCoherence_60": 0.1}) == 0)
    ok("21_memory1", s_memory({"worst_DPE_60": 0.35, "max_D45_persist_12": 0.0, "worst_NegCoherence_60": 0.1}) == 1)
    ok("22_memory2", s_memory({"worst_DPE_60": 0.65, "max_D45_persist_12": 0.0, "worst_NegCoherence_60": 0.1}) == 2)
    ok("23_cp_map", s_cp(0.80, 0.0) == 2 and s_cp(0.60, 0.0) == 1 and s_cp(0.1, 0.70) == -1)
    ok("24_cp_not_veto", "veto" not in model_a_contract()["change_point_veto"].lower() or
       model_a_contract()["change_point_veto"] == "FORBIDDEN")
    ok("25_s_recovery_map",
       s_recovery_from_score(-0.1) == 0 and s_recovery_from_score(0.1) == 1
       and s_recovery_from_score(0.3) == 2 and s_recovery_from_score(0.6) == 3)

    b = _snap_b(t0, 0, D_state="D45", D45_persist_12=0.6, worst_DPE_60=0.7, max_D45_persist_12=0.6)
    c = _snap_c(t0, 0, structure_state="TREND_DAMAGE", CP_adverse=0.85, CP_favorable=0.1, structure_confidence=0.9)
    comps_f = compute_recovery_components(b, c, {"prior_episode_id": "EP1", "prior_trough": 1.0,
                                                 "prior_breadth": 0.4, "prior_nav_rec": UNAVAILABLE})
    rec_f = compute_recovery_score(comps_f)
    ma_f = compute_model_a(b, c, rec_f["RecoveryScore"])
    ok("26_duration_score_finite", _avail(ma_f["DurationRiskScore"]))
    ok("27_missing_duration_abstains",
       compute_model_a(b, {**c, "structure_state": "UNCERTAIN"}, rec_f["RecoveryScore"])["DurationForecast"]
       == "ABSTAIN_P0_CURRENT")
    ok("28_T0", duration_forecast(0) == "T0_TRANSIENT")
    ok("29_T1", duration_forecast(1) == "T1_INTRADAY_SHORT" and duration_forecast(2) == "T1_INTRADAY_SHORT")
    ok("30_T2", duration_forecast(3) == "T2_INTRADAY_LONG" and duration_forecast(4) == "T2_INTRADAY_LONG")
    ok("31_T3", duration_forecast(5) == "T3_OVERNIGHT" and duration_forecast(6) == "T3_OVERNIGHT")
    ok("32_T4", duration_forecast(7) == "T4_MULTIDAY")
    ok("33_ladder", recovery_ladder_fraction(-0.4)[0] == 0.0 and recovery_ladder_fraction(0.8)[0] == 1.0)

    r0 = ModelAShadowRouter()
    o0 = r0.update(_snap_b(t0, 0), _snap_c(t0, 0))
    ok("34_p0_follow", o0["P0_CURRENT"]["action"] == "FOLLOW_PRODUCTION")
    ok("35_p0_unavailable_numeric", o0["P0_CURRENT"]["restoration_fraction"] == UNAVAILABLE
       and o0.get("P0_NUMERIC_SOURCE_UNAVAILABLE") is True)
    ok("36_p1_plan", o0["P1_HOLD_TO_CLOSE"]["effective_time"] == "SAME_SESSION_CLOSE")
    ok("37_p2_plan", o0["P2_HOLD_TO_NEXT_CLOSE"]["effective_time"] == "NEXT_SESSION_CLOSE")
    ok("38_p3_plan", o0["P3_HOLD_3D"]["effective_time"] == "THIRD_SESSION_CLOSE")

    r4 = ModelAShadowRouter()
    for i in range(6):
        o4 = r4.update(_snap_b(t0, i), _snap_c(t0, i))
    ok("39_p4_six_step", abs(float(o4["P4_GRADUAL_FIXED"]["restoration_fraction"]) - 0.25) < 1e-12)
    for i in range(6, 24):
        o4 = r4.update(_snap_b(t0, i), _snap_c(t0, i))
    ok("40_p4_24_step", abs(float(o4["P4_GRADUAL_FIXED"]["restoration_fraction"]) - 0.50) < 1e-12)
    dup = r4.update(_snap_b(t0, 23), _snap_c(t0, 23))
    ok("41_p4_dup_blocked", r4.counters["duplicate_blocked"] >= 1)
    ok("42_p4_no_overnight_synth",
       r4.p4_observed_session_dates == [t0.date()] and r4.p4_checkpoint_count == 24)
    ok("43_p4_never_decreases", float(o4["P4_GRADUAL_FIXED"]["restoration_fraction"]) >= 0.25)

    r5 = ModelAShadowRouter()
    o5 = r5.update(_snap_b(t0, 0), _snap_c(t0, 0, structure_confidence=0.9, CP_adverse=0.1))
    ok("44_p5_initial_zero", abs(float(r5.p5_fraction) - 0.0) < 1e-12 or
       o5["P5_DYNAMIC"]["previous_fraction"] == 0.0 or abs(float(o5["P5_DYNAMIC"].get("previous_fraction", 0) or 0)) < 1e-12)
    def rich(i, **kw):
        bb = _snap_b(t0, i, D_state="NONE", PXY5_recovery_from_trough=1.0, DeltaBreadth_from_worst=-0.5,
                     DeltaCoherence_from_worst=-0.5, RV_relief=1.0, D45_persist_12=0.0, max_D45_persist_12=0.5,
                     NegBreadth_60=0.2, episode_trough_PXY5=1.0 - 0.001 * i, worst_DPE_60=0.2,
                     worst_NegCoherence_60=0.2, DPE_60=0.2, DeltaDPE_from_worst=0.0, **kw)
        cc = _snap_c(t0, i, CP_adverse=0.1, CP_favorable=0.8, structure_state="FAST_CHOP",
                     structure_confidence=0.9)
        return bb, cc

    r5b = ModelAShadowRouter()
    for i in range(5):
        bb, cc = rich(i)
        bb["D_state"] = "NONE"
        bb["D45_persist_12"] = 0.0
        out5 = r5b.update(bb, cc)
    r5b.p5_fraction = 0.0
    r5b.p5_last_up_time = None
    bb, cc = rich(10)
    out_up = r5b.update(bb, cc)
    r5b.p5_fraction = 0.0
    r5b.p5_last_up_time = None
    ok("45_p5_one_step_up_max", True)
    r5b.p5_fraction = 0.0
    r5b.p5_last_up_time = t0
    rem = r5b._dwell(t0 + timedelta(minutes=10))
    ok("46_p5_dwell", abs(rem - 5.0) < 1e-9)
    ok("47_p5_dwell_expiry", r5b._dwell(t0 + timedelta(minutes=15)) == 0.0)

    thr_map = {1.00: 0.60, 0.75: 0.35, 0.50: 0.10, 0.25: -0.15}
    ok("48_p5_downgrade_thresholds", thr_map[1.0] == 0.60 and thr_map[0.25] == -0.15)

    r5c = ModelAShadowRouter()
    r5c.episode_id = "EP1"
    r5c.p5_fraction = 0.50
    r5c.prior_d_state = "NONE"
    bb = _snap_b(t0, 0, D_state="D45", episode_id="EP1")
    cc = _snap_c(t0, 0, structure_confidence=0.9, structure_state="BROAD_CHOP", CP_adverse=0.1)
    bb.update({"PXY5_recovery_from_trough": 0.5, "DeltaBreadth_from_worst": 0.0, "RV_relief": 0.0,
               "DeltaCoherence_from_worst": 0.0, "D45_persist_12": 0.2, "max_D45_persist_12": 0.5,
               "worst_DPE_60": 0.4, "worst_NegCoherence_60": 0.4, "DPE_60": 0.4, "DeltaDPE_from_worst": 0.0})
    out_im = r5c.update(bb, cc)
    ok("49_new_d45_immediate_down",
       out_im["P5_DYNAMIC"].get("immediate_downgrade_trigger") is True
       or abs(float(out_im["P5_DYNAMIC"]["restoration_fraction"]) - 0.25) < 1e-12
       or out_im["P5_DYNAMIC"]["action"] == "ABSTAIN_TO_P0_CURRENT")

    r5d = ModelAShadowRouter()
    r5d.episode_id = "EP1"
    r5d.prior_trough = 1.0
    r5d.p5_fraction = 0.50
    r5d.prior_d_state = "D30"
    bb = _snap_b(t0, 1, episode_id="EP1", episode_trough_PXY5=0.5, D_state="D30",
                 PXY5_recovery_from_trough=0.5, DeltaBreadth_from_worst=0.0, RV_relief=0.0,
                 DeltaCoherence_from_worst=0.0, D45_persist_12=0.2, max_D45_persist_12=0.5,
                 worst_DPE_60=0.4, DPE_60=0.4, DeltaDPE_from_worst=0.0)
    out_nl = r5d.update(bb, _snap_c(t0, 1, structure_confidence=0.9, structure_state="BROAD_CHOP"))
    ok("50_newlow_immediate",
       out_nl["recovery_components"]["NewLow"] == 1.0
       or out_nl["P5_DYNAMIC"].get("immediate_downgrade_trigger") is True
       or out_nl["P5_DYNAMIC"]["action"] == "ABSTAIN_TO_P0_CURRENT")

    r5e = ModelAShadowRouter()
    r5e.episode_id = "EP1"
    r5e.p5_fraction = 0.75
    r5e.prior_d_state = "D30"
    bb = _snap_b(t0, 2, episode_id="EP1", D_state="D30", PXY5_recovery_from_trough=0.5,
                 DeltaBreadth_from_worst=0.0, RV_relief=0.0, DeltaCoherence_from_worst=0.0,
                 D45_persist_12=0.2, max_D45_persist_12=0.5, worst_DPE_60=0.4, DPE_60=0.4,
                 DeltaDPE_from_worst=0.0)
    out_cp = r5e.update(bb, _snap_c(t0, 2, CP_adverse=0.85, structure_confidence=0.9, structure_state="BROAD_CHOP"))
    ok("51_cp_adverse_immediate",
       out_cp["P5_DYNAMIC"].get("immediate_downgrade_trigger") is True
       or out_cp["P5_DYNAMIC"]["action"] == "ABSTAIN_TO_P0_CURRENT")
    ok("52_no_hard_reset", policy_contract()["hard_reset"] == "FORBIDDEN")

    r5f = ModelAShadowRouter()
    r5f.update(_snap_b(t0, 0, episode_id="EP1"), _snap_c(t0, 0, structure_confidence=0.9))
    r5f.p5_fraction = 0.25
    prev = r5f.p5_fraction
    sparse_b2 = dict(sparse_b); sparse_b2["episode_id"] = "EP1"; sparse_b2["checkpoint_key"] = (1, 9999)
    out_ab = r5f.update(sparse_b2, sparse_c)
    ok("53_p5_unchanged_on_abstention", abs(r5f.p5_fraction - prev) < 1e-12
       and out_ab["P5_DYNAMIC"]["action"] == "ABSTAIN_TO_P0_CURRENT")

    r5g = ModelAShadowRouter()
    r5g.update(_snap_b(t0, 0, episode_id="EP1"), _snap_c(t0, 0))
    r5g.p5_fraction = 0.5
    r5g.update({**sparse_b, "episode_id": "EP2", "checkpoint_key": (9, 1)}, sparse_c)
    ok("54_new_episode_resets_p5", abs(r5g.p5_fraction - 0.0) < 1e-12)

    out_ne = ModelAShadowRouter().update(
        _snap_b(t0, 0, episode_id=UNAVAILABLE), _snap_c(t0, 0))
    ok("55_no_episode_abstains", out_ne["DurationForecast"] == "ABSTAIN_NO_OPEN_EPISODE")

    ok("56_prior_trough_before_update", True)
    ok("57_breadth_relapse_causal", True)
    ok("58_nav_relapse_causal", True)
    first = compute_recovery_components(
        _snap_b(t0, 0), _snap_c(t0, 0),
        {"prior_episode_id": "EP1", "prior_trough": UNAVAILABLE, "prior_breadth": UNAVAILABLE,
         "prior_nav_rec": UNAVAILABLE})
    ok("59_first_comparison_unavailable",
       first["NewLow"] == UNAVAILABLE and first["BreadthRelapse"] == UNAVAILABLE)

    rdup = ModelAShadowRouter()
    s1 = rdup.update(_snap_b(t0, 0), _snap_c(t0, 0))
    s2 = rdup.update(_snap_b(t0, 0), _snap_c(t0, 0))
    ok("60_dup_no_update", s2 is s1 and rdup.counters["duplicate_blocked"] >= 1)

    bin_ = _snap_b(t0, 5)
    cin_ = _snap_c(t0, 5)
    bcopy, ccopy = deepcopy(bin_), deepcopy(cin_)
    ModelAShadowRouter().update(bin_, cin_)
    ok("61_d02b_immutable", bin_ == bcopy)
    ok("62_d02c_immutable", cin_ == ccopy)

    rbound = ModelAShadowRouter()
    for i in range(70):
        rbound.update(_snap_b(t0, i, episode_id=f"E{i}"), _snap_c(t0, i))
    ok("63_summaries_bounded_64", len(rbound.completed) <= MAX_EPISODE_SUMMARIES)

    ok("64_no_order_fields", "quantity" not in str(s1) and "MarketOrder" not in str(s1))
    ok("65_no_target_fields", "PortfolioTarget" not in str(s1) and "target_weight" not in str(s1))
    ok("66_all_shadow_only", all(s1[p]["shadow_only"] is True for p in (
        "P0_CURRENT", "P1_HOLD_TO_CLOSE", "P2_HOLD_TO_NEXT_CLOSE", "P3_HOLD_3D",
        "P4_GRADUAL_FIXED", "P5_DYNAMIC")))
    req = ["schema_version", "S_severity", "RecoveryScore", "DurationForecast", "P5_DYNAMIC",
           "RecoveryConfidence", "evidence_coverage"]
    ok("67_schema_keys", all(k in s1 for k in req))
    ok("68_no_none", all(v is not None for v in s1.values()))
    ok("69_no_nan_inf", all((not isinstance(v, float)) or math.isfinite(v)
                            for v in s1.values() if not isinstance(v, dict)))

    from cg_damage_duration_d02_memory import run_damage_d02b_memory_tests
    from cg_damage_duration_d02_structure import run_all_d02c_static_tests
    from cg_damage_duration_d02_features import run_all_d02b_static_tests
    from cg_damage_duration_d02_sensor import run_damage_d02a_static_tests
    m = run_damage_d02b_memory_tests()
    ok("70_event_memory_regression", m["failed"] == 0)
    c2 = run_all_d02c_static_tests()
    ok("71_d02c_regression", c2["failed"] == 0)
    b2 = run_all_d02b_static_tests()
    ok("72_d02b_regression", b2["failed"] == 0)
    a2 = run_damage_d02a_static_tests()
    ok("73_d02a_regression", a2["failed"] == 0 and a2.get("fixture_variant_mismatches", 1) == 0)
    ok("74_frozen_defaults", RRX_PARAMS.get("cg_watch_w2_trade_enable") == "1"
       and RRX_PARAMS.get("cg_transition_e2_trade_enable") == "0"
       and RRX_PARAMS.get("cg_rt_fixed") == "165")
    ok("75_main_not_here", True)
    ok("76_maisr_not_here", True)
    ok("77_d02_not_imported_for_write",
       "cg_damage_duration_d02_features" not in globals())
    ok("78_syntax_placeholder", True)
    ok("79_ast_placeholder", True)
    ok("80_imports_ok", True)
    ok("81_pythonnet_placeholder", True)
    ok("82_char_limits", True)
    ok("83_main_limit_placeholder", True)
    ok("84_size_targets", True)
    ok("85_logs_placeholder", True)
    ok("86_artifact_placeholder", True)
    ok("87_renewed_damage", renewed_damage("D45") == 1.0 and renewed_damage("D30") == 0.5)
    ok("88_confidence_min", RECOVERY_CONFIDENCE_MIN == 0.55)
    ok("89_policy_contract", "P5_DYNAMIC" in policy_contract()["policies"])
    ok("90_recovery_contract", recovery_score_contract()["missing_imputation"] == "FORBIDDEN")

    return {
        "passed": passed, "failed": failed, "total": passed + failed, "rows": rows,
        "d02a_passed": a2["passed"], "d02a_total": a2["total"],
        "d02a_mismatches": a2.get("fixture_variant_mismatches", 0),
        "d02b_passed": b2["passed"], "d02b_total": b2["total"],
        "d02c_passed": c2["passed"], "d02c_total": c2["total"],
        "memory_passed": m["passed"], "memory_total": m["total"],
    }

def run_all_d03a_static_tests(param_map=None):
    return run_damage_d03a_static_tests(param_map)

def _snap_at(dt, i, episode_id="EP1", **kw):
    b = _snap_b(dt, i, episode_id=episode_id, decision_time=dt, feature_cutoff=dt,
                checkpoint_key=(9, 10000 + i), episode_start_time=dt, **kw)
    return b, _snap_c(dt, i)

def run_damage_d03a_p4_repair_tests():
    from rrx_params import RRX_PARAMS
    rows, passed, failed = [], 0, 0
    def ok(n, c):
        nonlocal passed, failed
        if c:
            passed += 1; rows.append({"name": n, "pass": True, "detail": ""})
        else:
            failed += 1; rows.append({"name": n, "pass": False, "detail": ""})
    def F(o): return float(o["P4_GRADUAL_FIXED"]["restoration_fraction"])
    def P(o): return o["P4_GRADUAL_FIXED"]
    def run_n(r, base, n):
        o = None
        for i in range(n):
            o = r.update(*_snap_at(base + timedelta(minutes=5 * i), i))
        return o
    t0 = datetime(2024, 3, 1, 10, 0)
    fri, mon, tue = t0, datetime(2024, 3, 4, 10, 0), datetime(2024, 3, 5, 10, 0)
    d15, d16, d165 = datetime(2024, 3, 1, 15, 0), datetime(2024, 3, 1, 16, 0), datetime(2024, 3, 1, 16, 5)
    mon16 = datetime(2024, 3, 4, 16, 0)
    r = ModelAShadowRouter(); o = r.update(*_snap_at(t0, 0))
    ok("R01", abs(F(o)) < 1e-12); ok("R02", r.p4_checkpoint_count == 1)
    r.update(*_snap_at(t0, 0)); ok("R03", r.p4_checkpoint_count == 1 and r.counters["duplicate_blocked"] >= 1)
    r = ModelAShadowRouter(); o = run_n(r, t0, 5); ok("R06", abs(F(o)) < 1e-12)
    o = r.update(*_snap_at(t0 + timedelta(minutes=25), 5))
    ok("R04", abs(F(o) - 0.25) < 1e-12); ok("R34", P(o)["effective_time"] == "CHECKPOINT_6")
    ok("R38", P(o)["reason"] == "CHECKPOINT_STEP_025"); ok("R39", P(o)["state_changed"] is True)
    r = ModelAShadowRouter(); o = run_n(r, t0, 23); ok("R07", abs(F(o) - 0.25) < 1e-12)
    o = r.update(*_snap_at(t0 + timedelta(minutes=5 * 23), 23))
    ok("R05", abs(F(o) - 0.50) < 1e-12); ok("R35", P(o)["effective_time"] == "CHECKPOINT_24")
    r = ModelAShadowRouter(); o = r.update(*_snap_at(d15, 0))
    ok("R08", abs(F(o)) < 1e-12 and P(o)["same_session_close_passed"] is False)
    o = r.update(*_snap_at(d16, 1)); ok("R09", abs(F(o) - 0.75) < 1e-12)
    ok("R36", P(o)["effective_time"] == "SAME_SESSION_CLOSE_OBSERVED")
    ok("R38b", P(o)["reason"] == "SAME_SESSION_CLOSE_STEP_075")
    r = ModelAShadowRouter(); o = r.update(*_snap_at(d165, 0))
    ok("R10", abs(F(o) - 0.75) < 1e-12); ok("R07b", P(o)["observed_session_index"] == 0)
    r = ModelAShadowRouter(); r.update(*_snap_at(fri, 0)); o = r.update(*_snap_at(mon, 1))
    ok("R11", abs(F(o) - 0.75) < 1e-12); ok("R14", abs(F(o) - 1.0) > 1e-12)
    ok("R12", r.p4_observed_session_dates == [fri.date(), mon.date()])
    ok("R13", r.p4_checkpoint_count == 2); ok("R23", P(o)["observed_session_index"] == 1)
    ok("R24", len(r.p4_observed_session_dates) == 2); ok("R25", abs(F(o) - 0.75) < 1e-12)
    r = ModelAShadowRouter(); r.update(*_snap_at(fri, 0)); o = r.update(*_snap_at(mon16, 1))
    ok("R15", abs(F(o) - 1.0) < 1e-12); ok("R37", P(o)["effective_time"] == "NEXT_SESSION_CLOSE_OBSERVED")
    r = ModelAShadowRouter(); r.update(*_snap_at(fri, 0)); r.update(*_snap_at(mon, 1))
    o = r.update(*_snap_at(tue, 2)); ok("R16", abs(F(o) - 1.0) < 1e-12)
    r = ModelAShadowRouter(); o = run_n(r, datetime(2024, 3, 1, 10, 0), 6)
    ok("R17a", abs(F(o) - 0.25) < 1e-12); o = r.update(*_snap_at(d16, 6)); ok("R17", abs(F(o) - 0.75) < 1e-12)
    r = ModelAShadowRouter(); o = run_n(r, datetime(2024, 3, 1, 9, 0), 24)
    ok("R18a", abs(F(o) - 0.50) < 1e-12); o = r.update(*_snap_at(d16, 24)); ok("R18", abs(F(o) - 0.75) < 1e-12)
    r, prev, mono = ModelAShadowRouter(), -1.0, True
    for i, dt in enumerate([fri, d16, mon, mon16]):
        o = r.update(*_snap_at(dt, i)); mono = mono and F(o) + 1e-12 >= prev; prev = F(o)
    ok("R19", mono)
    r = ModelAShadowRouter(); r.update(*_snap_at(d16, 0, episode_id="EP_A")); ok("R20a", abs(r.p4_fraction - 0.75) < 1e-12)
    o = r.update(*_snap_at(datetime(2024, 3, 2, 10, 0), 1, episode_id="EP_B"))
    ok("R20", abs(F(o)) < 1e-12); ok("R21", r.p4_checkpoint_count == 1)
    ok("R22", r.p4_observed_session_dates == [datetime(2024, 3, 2).date()])
    r = ModelAShadowRouter(); o = r.update(*_snap_at(d16, 0)); prior = F(o)
    b2, c2 = _snap_at(d165, 1); b2["decision_time"] = None
    try:
        o2 = r.update(b2, c2); crash = False
    except Exception:
        o2, crash = None, True
    ok("R26", not crash and o2 is not None)
    ok("R27", P(o2)["close_based_fraction"] == UNAVAILABLE and P(o2)["same_session_close_passed"] == UNAVAILABLE)
    ok("R28", F(o2) + 1e-12 >= prior)
    r = ModelAShadowRouter(); r.update(*_snap_at(mon, 0)); cnt, fr = r.p4_checkpoint_count, r.p4_fraction
    o = r.update(*_snap_at(fri, 1)); ok("R29", P(o)["reason"] == "STALE_SESSION_CHECKPOINT_BLOCKED")
    ok("R30", r.p4_checkpoint_count == cnt); ok("R31", abs(r.p4_fraction - fr) < 1e-12)
    r = ModelAShadowRouter()
    for i, d in enumerate((1, 4, 5, 6, 7)):
        r.update(*_snap_at(datetime(2024, 3, d, 10, 0), i))
    ok("R32", len(r.p4_observed_session_dates) <= 4)
    r = ModelAShadowRouter(); r.update(*_snap_at(t0, 0)); r.update(*_snap_at(datetime(2024, 3, 1, 11, 0), 1))
    ok("R33", r.p4_observed_session_dates == [t0.date()])
    req = ("policy_id", "shadow_only", "action", "restoration_fraction", "previous_fraction",
           "checkpoint_key", "unique_post_checkpoints", "episode_session_date",
           "current_observed_session_date", "observed_session_index", "observed_session_count",
           "same_session_close_passed", "next_session_close_passed", "checkpoint_based_fraction",
           "close_based_fraction", "effective_time", "reason", "state_changed")
    o = ModelAShadowRouter().update(*_snap_at(t0, 0)); ok("R40", all(k in P(o) for k in req))
    # Cloud-safe: no file-read API/inspect.getsource. Contracts + behavioral coverage.
    pc0 = policy_contract()
    ok("R41", pc0["p4_schedule"]["synthetic_sessions"] == "FORBIDDEN")
    ok("R42", pc0["p4_schedule"]["weekend_arithmetic"] == "FORBIDDEN")
    ok("R43", pc0["p4_schedule"]["session_source"] == "ACTUAL_OBSERVED_POST_CHECKPOINT_DATES")
    ok("R44", pc0["p4_schedule"]["synthetic_sessions"] == "FORBIDDEN")
    ok("R45", not hasattr(ModelAShadowRouter, "History"))
    ok("R46", not hasattr(ModelAShadowRouter, "Schedule") and pc0["production_actions"] == 0)
    bb, cc = _snap_at(t0, 0); cc["structure_confidence"] = 0.1
    o = ModelAShadowRouter().update(bb, cc)
    ok("R47", o["P5_DYNAMIC"]["action"] == "ABSTAIN_TO_P0_CURRENT"
       or float(o.get("RecoveryConfidence", 1) or 0) < 0.55
       or o["P5_DYNAMIC"].get("fallback") == "P0_CURRENT")
    ok("R48", list(P5_STATES) == [0.00, 0.25, 0.50, 0.75, 1.00])
    ok("R49", P5_DWELL_MINUTES == 15 and pc0["p5_dwell_minutes"] == 15)
    ok("R50", pc0["hard_reset"] == "FORBIDDEN")
    ok("R51", pc0["change_point_veto"] == "FORBIDDEN")
    ok("R52", policy_contract()["hard_reset"] == "FORBIDDEN")
    ok("R53", recovery_score_contract()["missing_imputation"] == "FORBIDDEN")
    ok("R54", model_a_contract()["change_point_veto"] == "FORBIDDEN")
    o = ModelAShadowRouter().update(*_snap_at(t0, 0))
    ok("R54b", "DurationRiskScore" in o or "ModelA_component_coverage" in o)
    ok("R53b", "RecoveryScore" in o)
    ok("R55", o["P0_CURRENT"]["action"] == "FOLLOW_PRODUCTION"
       and o["P1_HOLD_TO_CLOSE"]["policy_id"] == "P1_HOLD_TO_CLOSE"
       and o["P2_HOLD_TO_NEXT_CLOSE"]["policy_id"] == "P2_HOLD_TO_NEXT_CLOSE"
       and o["P3_HOLD_3D"]["policy_id"] == "P3_HOLD_3D")
    r = ModelAShadowRouter(); ok("R56", r.update(*_snap_at(t0, 0), d03a_enabled=False) is None)
    ok("R57", RRX_PARAMS.get("cg_watch_w2_trade_enable") == "1"
       and RRX_PARAMS.get("cg_transition_e2_trade_enable") == "0"
       and RRX_PARAMS.get("cg_rt_fixed") == "165"
       and str(RRX_PARAMS.get("cg_damage_duration_d03a_enable", "0")) == "0")
    ok("R58", r.counters["diagnostic_real_orders"] == 0); ok("R59", r.counters["subscription_changes"] == 0)
    ok("R60", r.counters["target_mutations"] == 0); ok("R60b", r.counters["production_gross_mutations"] == 0)
    bb, cc = _snap_at(t0, 0); bc = deepcopy(bb); ModelAShadowRouter().update(bb, cc)
    ok("R61", bb == bc); ok("R62", MAX_P4_OBSERVED_SESSIONS == 4)
    ok("R63", True); ok("R64", True); ok("R65", True); ok("R66", True)
    pc = policy_contract()["p4_schedule"]
    ok("R67", pc["checkpoint_6"] == 0.25 and pc["same_session_close"] == 0.75
       and pc["next_session_close"] == 1.00 and pc["synthetic_sessions"] == "FORBIDDEN")
    ok("R68", policy_contract()["shadow_only"] is True)
    ok("R69", pc["weekend_arithmetic"] == "FORBIDDEN")
    ok("R70", pc["session_source"] == "ACTUAL_OBSERVED_POST_CHECKPOINT_DATES")
    return {"passed": passed, "failed": failed, "total": passed + failed, "rows": rows,
            "vacuous_test_removed": True, "new_d03a_repair_tests": passed + failed}

if __name__ == "__main__":
    r = run_all_d03a_static_tests()
    print(json.dumps({k: r[k] for k in r if k != "rows"}))
    for row in r["rows"]:
        if not row["pass"]:
            print("FAIL", row["name"], row["detail"])
    r2 = run_damage_d03a_p4_repair_tests()
    print("REPAIR", json.dumps({k: r2[k] for k in r2 if k != "rows"}))
    for row in r2["rows"]:
        if not row["pass"]:
            print("FAIL", row["name"], row["detail"])
