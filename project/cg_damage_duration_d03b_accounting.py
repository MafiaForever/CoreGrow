# cg_damage_duration_d03b_accounting.py -- D0.3B1 P0 audit + causal observation builder.
# Diagnostic research only. No orders/targets/History/subscriptions.
from __future__ import annotations
from datetime import datetime

from cg_damage_duration_d03a_core import UNAVAILABLE, _avail, _f, get

EXPERIMENT = "CG-DAMAGE-DURATION-D0.3B1"
PHASE = "D0.3B1_MODEL_A_SHADOW_RUNTIME_ACCOUNTING_EXPORT"
SCHEMA_VERSION = "D03B1_POLICY_RUNTIME_V1"
POLICY_IDS = (
    "P0_CURRENT", "P1_HOLD_TO_CLOSE", "P2_HOLD_TO_NEXT_CLOSE",
    "P3_HOLD_3D", "P4_GRADUAL_FIXED", "P5_DYNAMIC",
)
MAX_CHECKPOINT_ROWS = 256
MAX_EPISODE_ROWS = 64

# Explicit non-proxy: production has no persisted withheld-restoration fraction.
P0_SOURCE_NAME = "UNRESOLVED"
P0_SOURCE_VERDICT = "STOP_P0_NUMERIC_SOURCE_UNRESOLVED"
P0_AUDIT = {
    "candidates_examined": [
        "_cg_w2_last_active", "_cg_w2_last_eq", "_cg_rt_pending",
        "GetCurrentWeights/equity_gross", "portfolio.total_portfolio_value",
        "IDS gross_cap", "panic/emergency/reduce_only flags",
    ],
    "accepted_source": None,
    "reason": (
        "No existing causal read-only scalar encodes production restoration "
        "fraction of withheld gross (0..1). Absolute gross, W2 scale, and "
        "protection flags are wrong semantics; inventing a proxy is forbidden."
    ),
    "verdict": P0_SOURCE_VERDICT,
}


def resolve_p0_numeric_source(prod_state=None, decision_time=None):
    """Attempt causal P0 resolution. Never invents a proxy. Always audit-backed."""
    audit = dict(P0_AUDIT)
    audit["decision_time"] = decision_time if isinstance(decision_time, datetime) else UNAVAILABLE
    audit["prod_state_keys"] = sorted(list((prod_state or {}).keys()))[:32]
    # Future/same-bar dependent inputs are rejected if supplied under reserved keys.
    if prod_state:
        if prod_state.get("uses_future_fills") or prod_state.get("uses_same_bar_overlap"):
            audit["rejected"] = "FUTURE_OR_SAME_BAR_DEPENDENT"
            return UNAVAILABLE, P0_SOURCE_NAME, 0.0, audit
        if prod_state.get("p0_numeric_restore_fraction") is not None:
            # Only accept an explicitly pre-instrumented causal field if present
            # with causal stamp; current production does not provide this.
            v = prod_state.get("p0_numeric_restore_fraction")
            stamp = prod_state.get("p0_source_time")
            if (
                _avail(v)
                and isinstance(stamp, datetime)
                and isinstance(decision_time, datetime)
                and stamp <= decision_time
                and not prod_state.get("reconstructed_targets")
            ):
                audit["accepted_source"] = prod_state.get("p0_source_name", "EXPLICIT_CAUSAL_FIELD")
                audit["verdict"] = "CAUSAL_RESOLVED"
                return _f(v), audit["accepted_source"], 1.0, audit
    return UNAVAILABLE, P0_SOURCE_NAME, 0.0, audit


def _session_date(decision_time):
    if isinstance(decision_time, datetime):
        return decision_time.date()
    return UNAVAILABLE


def _step_direction(curr, prev):
    if not _avail(curr) or not _avail(prev):
        return UNAVAILABLE
    c, p = _f(curr), _f(prev)
    if abs(c - p) < 1e-12:
        return "HOLD"
    return "UP" if c > p else "DOWN"


def validate_timestamps(decision_time, feature_cutoff, action_eligible):
    """Closed-bar causality: FeatureCutoff<=DecisionTime<ActionEligible (when known)."""
    if not isinstance(decision_time, datetime):
        return False, "DECISION_TIME_UNAVAILABLE"
    if isinstance(feature_cutoff, datetime) and feature_cutoff > decision_time:
        return False, "FEATURE_CUTOFF_AFTER_DECISION"
    if isinstance(action_eligible, datetime) and action_eligible <= decision_time:
        return False, "ACTION_ELIGIBLE_NOT_AFTER_DECISION"
    if (
        isinstance(feature_cutoff, datetime)
        and isinstance(action_eligible, datetime)
        and feature_cutoff == action_eligible
    ):
        return False, "SAME_BAR_OVERLAP"
    return True, "OK"


def build_policy_observation(
    policy_id,
    shadow_policy,
    snap_b,
    snap_c,
    shadow_out,
    p0_frac,
    p0_source_name,
    p0_source_confidence,
    production_nav_read_only=UNAVAILABLE,
    proxy_basket_return_since_checkpoint=UNAVAILABLE,
):
    """Build one checkpoint-level policy row. Inputs treated as immutable."""
    sp = shadow_policy or {}
    frac = sp.get("restoration_fraction", UNAVAILABLE)
    prev = sp.get("previous_fraction", UNAVAILABLE)
    dt = get(snap_b, "decision_time")
    fc = get(snap_b, "feature_cutoff")
    act = get(snap_b, "action_eligible_time")
    ok_ts, ts_reason = validate_timestamps(
        dt if isinstance(dt, datetime) else None,
        fc if isinstance(fc, datetime) else None,
        act if isinstance(act, datetime) else None,
    )
    row = {
        "artifact_schema_version": SCHEMA_VERSION,
        "experiment": EXPERIMENT,
        "phase": PHASE,
        "episode_id": get(snap_b, "episode_id"),
        "checkpoint_id": get(snap_b, "checkpoint_key"),
        "decision_time": dt,
        "feature_cutoff_time": fc,
        "action_eligible_time": act,
        "session_date": _session_date(dt if isinstance(dt, datetime) else None),
        "policy_id": policy_id,
        "policy_restore_fraction": frac if policy_id != "P0_CURRENT" else p0_frac,
        "previous_policy_restore_fraction": prev,
        "policy_step_direction": _step_direction(
            frac if policy_id != "P0_CURRENT" else p0_frac, prev),
        "duration_risk_score": get(shadow_out, "DurationRiskScore"),
        "recovery_score": get(shadow_out, "RecoveryScore"),
        "recovery_confidence": get(shadow_out, "RecoveryConfidence"),
        "structure_state": get(snap_c, "structure_state"),
        "structure_confidence": get(snap_c, "structure_confidence"),
        "change_point_score": get(snap_c, "CP_adverse"),
        "d30_state": get(snap_b, "D_state"),
        "d45_state": get(snap_b, "D_state"),
        "damage_alive_proxy_state": get(snap_b, "D45_persist_12"),
        "p0_numeric_restore_fraction": p0_frac,
        "p0_source_name": p0_source_name,
        "p0_source_confidence": p0_source_confidence,
        "production_nav_read_only": production_nav_read_only,
        "proxy_basket_return_since_checkpoint": proxy_basket_return_since_checkpoint,
        "timestamp_gate": "PASS" if ok_ts else "FAIL",
        "timestamp_reason": ts_reason,
        "shadow_only": True,
    }
    return row


def build_episode_summary(episode_id, rows):
    """Compact episode-level rollup from checkpoint rows (same episode only)."""
    erows = [r for r in rows if r.get("episode_id") == episode_id]
    if not erows:
        return {
            "artifact_schema_version": SCHEMA_VERSION,
            "episode_id": episode_id,
            "checkpoint_count": 0,
            "first_decision_time": UNAVAILABLE,
            "last_decision_time": UNAVAILABLE,
            "p0_numeric_restore_fraction": UNAVAILABLE,
            "p0_source_name": P0_SOURCE_NAME,
        }
    dts = [r["decision_time"] for r in erows if isinstance(r.get("decision_time"), datetime)]
    p5 = [r for r in erows if r.get("policy_id") == "P5_DYNAMIC"]
    last_p5 = p5[-1] if p5 else erows[-1]
    return {
        "artifact_schema_version": SCHEMA_VERSION,
        "episode_id": episode_id,
        "checkpoint_count": len({r.get("checkpoint_id") for r in erows}),
        "first_decision_time": min(dts) if dts else UNAVAILABLE,
        "last_decision_time": max(dts) if dts else UNAVAILABLE,
        "last_p5_fraction": last_p5.get("policy_restore_fraction", UNAVAILABLE),
        "p0_numeric_restore_fraction": last_p5.get("p0_numeric_restore_fraction", UNAVAILABLE),
        "p0_source_name": last_p5.get("p0_source_name", P0_SOURCE_NAME),
        "shadow_only": True,
    }


def policy_runtime_schema():
    return {
        "schema_version": SCHEMA_VERSION,
        "experiment": EXPERIMENT,
        "phase": PHASE,
        "policies": list(POLICY_IDS),
        "required_fields": [
            "episode_id", "checkpoint_id", "decision_time", "feature_cutoff_time",
            "action_eligible_time", "session_date", "policy_id",
            "policy_restore_fraction", "previous_policy_restore_fraction",
            "policy_step_direction", "duration_risk_score", "recovery_score",
            "recovery_confidence", "structure_state", "structure_confidence",
            "change_point_score", "d30_state", "d45_state",
            "damage_alive_proxy_state", "p0_numeric_restore_fraction",
            "p0_source_name", "p0_source_confidence", "production_nav_read_only",
            "proxy_basket_return_since_checkpoint", "artifact_schema_version",
        ],
        "p0_verdict": P0_SOURCE_VERDICT,
        "shadow_only": True,
        "max_checkpoint_rows": MAX_CHECKPOINT_ROWS,
        "max_episode_rows": MAX_EPISODE_ROWS,
    }
