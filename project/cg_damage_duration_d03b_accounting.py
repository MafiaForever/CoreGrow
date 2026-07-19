# cg_damage_duration_d03b_accounting.py -- D0.3B1 P0 audit + causal observation builder.
# Diagnostic research only. No orders/targets/History/subscriptions.
from __future__ import annotations
from datetime import datetime

from cg_damage_duration_d03a_core import UNAVAILABLE, _avail, _f, get

EXPERIMENT = "CG-DAMAGE-DURATION-D0.3B1-P0-REPAIR"
PHASE = "D0.3B1_P0_NUMERIC_SOURCE_REPAIR"
SCHEMA_VERSION = "D03B1_POLICY_RUNTIME_V1"
POLICY_IDS = (
    "P0_CURRENT", "P1_HOLD_TO_CLOSE", "P2_HOLD_TO_NEXT_CLOSE",
    "P3_HOLD_3D", "P4_GRADUAL_FIXED", "P5_DYNAMIC",
)
MAX_CHECKPOINT_ROWS = 256
MAX_EPISODE_ROWS = 64

P0_SOURCE_NAME = "UNAVAILABLE"
P0_SOURCE_VERDICT = "STOP_D0_P0_BASELINE_UNOBSERVABLE"

# Complete lineage audit: every candidate examined for P0 restoration fraction.
P0_CANDIDATES = [
    {
        "candidate_id": "C01_W2_ACTIVE",
        "owner_file": "cg_defensive_trade.py",
        "owner_function": "CgDefensiveTradeApplyOverlays",
        "field_name": "_cg_w2_last_active",
        "numeric_semantics": "boolean W2 overlay active; not a [0,1] restoration fraction",
        "availability_time": "set at DAILYCycle ~09:45 when overlays applied",
        "timestamp_source": "algorithm.time at overlay apply",
        "episode_reset_behavior": "persists across days until W2 recomputed; not episode-scoped",
        "causal_verdict": "REJECTED",
        "rejection_reason_or_acceptance_proof": "WRONG_SEMANTICS_BOOLEAN_NOT_RESTORATION_FRACTION",
    },
    {
        "candidate_id": "C02_W2_EQ_GROSS",
        "owner_file": "cg_defensive_trade.py",
        "owner_function": "CgDefensiveTradeApplyOverlays",
        "field_name": "_cg_w2_last_eq",
        "numeric_semantics": "absolute post-W2 equity gross on constructed targets; scale 0.80 when active",
        "availability_time": "set at DAILYCycle ~09:45",
        "timestamp_source": "algorithm.time at overlay apply",
        "episode_reset_behavior": "not episode-scoped; no withheld baseline stored",
        "causal_verdict": "REJECTED",
        "rejection_reason_or_acceptance_proof": "ABSOLUTE_GROSS_NOT_WITHHELD_RESTORATION_FRACTION; inventing 0.80/1.0 proxy FORBIDDEN",
    },
    {
        "candidate_id": "C03_RT_PENDING_TARGETS",
        "owner_file": "cg_regime_rebal_time_trade.py",
        "owner_function": "CgRegimeRebalTimeTradeCapture",
        "field_name": "_cg_rt_pending",
        "numeric_semantics": "deferred target weight dict; gross via _rtt_gross is absolute",
        "availability_time": "captured at 09:45; executed at fixed slot 165 (12:15)",
        "timestamp_source": "_cg_rt_pending_ts",
        "episode_reset_behavior": "cleared after successful execute; not a recovery fraction",
        "causal_verdict": "REJECTED",
        "rejection_reason_or_acceptance_proof": "ABSOLUTE_TARGET_GROSS_NOT_RESTORATION_FRACTION",
    },
    {
        "candidate_id": "C04_RT_FIXED_SLOT",
        "owner_file": "rrx_params.py",
        "owner_function": "RRX_PARAMS",
        "field_name": "cg_rt_fixed",
        "numeric_semantics": "execution slot minute (165); timing only",
        "availability_time": "parameter constant",
        "timestamp_source": "NONE",
        "episode_reset_behavior": "N/A",
        "causal_verdict": "REJECTED",
        "rejection_reason_or_acceptance_proof": "TIMING_PARAMETER_NOT_RESTORATION_FRACTION",
    },
    {
        "candidate_id": "C05_CURRENT_HOLDINGS_WEIGHTS",
        "owner_file": "cg_logic.py",
        "owner_function": "GetCurrentWeights",
        "field_name": "HoldingsValue/TotalPortfolioValue",
        "numeric_semantics": "realized holdings weights; fill-dependent",
        "availability_time": "any time; under fixed-165 lags until 12:15 execution",
        "timestamp_source": "portfolio mark time",
        "episode_reset_behavior": "continuous; not episode-scoped",
        "causal_verdict": "REJECTED",
        "rejection_reason_or_acceptance_proof": "FILL_HOLDINGS_PATH_NOT_TARGET_RESTORATION; lagged under fixed-165",
    },
    {
        "candidate_id": "C06_RESID_EQUITY_GROSS",
        "owner_file": "cg_macro_resid_b1_diag.py",
        "owner_function": "_ResidB1ProtectionSnap",
        "field_name": "equity_gross",
        "numeric_semantics": "absolute equity gross from GetCurrentWeights",
        "availability_time": "when resid snap built",
        "timestamp_source": "algorithm.time",
        "episode_reset_behavior": "not damage-episode scoped",
        "causal_verdict": "REJECTED",
        "rejection_reason_or_acceptance_proof": "ABSOLUTE_GROSS_FROM_HOLDINGS_NOT_RESTORATION_FRACTION",
    },
    {
        "candidate_id": "C07_PORTFOLIO_NAV",
        "owner_file": "cg_damage_duration_d01_diag.py",
        "owner_function": "_DamageD02OnEval",
        "field_name": "portfolio.total_portfolio_value",
        "numeric_semantics": "NAV dollars; used for D0.2B recovery features",
        "availability_time": "decision_time read-only",
        "timestamp_source": "algorithm.time",
        "episode_reset_behavior": "continuous",
        "causal_verdict": "REJECTED",
        "rejection_reason_or_acceptance_proof": "NAV_NOT_RESTORATION_FRACTION",
    },
    {
        "candidate_id": "C08_IDS_GROSS_CAP",
        "owner_file": "sh_hedge.py",
        "owner_function": "_IDSGetOverlayCaps",
        "field_name": "gross_cap",
        "numeric_semantics": "absolute gross cap by IDS state (e.g. 1.40/1.20)",
        "availability_time": "when IDS state active",
        "timestamp_source": "IDS latch update time",
        "episode_reset_behavior": "IDS latch independent of damage episode",
        "causal_verdict": "REJECTED",
        "rejection_reason_or_acceptance_proof": "ABSOLUTE_CAP_NOT_WITHHELD_RESTORATION_FRACTION",
    },
    {
        "candidate_id": "C09_IDS_HEDGE_FRAC",
        "owner_file": "sh_hedge.py",
        "owner_function": "_IDSGetDesiredHedgeFraction",
        "field_name": "ids_*_hedge_frac",
        "numeric_semantics": "hedge size as fraction of SPY weight; not equity restore path",
        "availability_time": "IDS state driven",
        "timestamp_source": "IDS latch",
        "episode_reset_behavior": "IDS latch",
        "causal_verdict": "REJECTED",
        "rejection_reason_or_acceptance_proof": "HEDGE_FRACTION_NOT_PRODUCTION_EQUITY_RESTORATION",
    },
    {
        "candidate_id": "C10_PROTECTION_FLAGS",
        "owner_file": "cg_damage_duration_d01_diag.py",
        "owner_function": "_DamageD01ProtectionSnap",
        "field_name": "w2_active|ids_state|panic_state|emergency|reduce_only",
        "numeric_semantics": "categorical protection labels",
        "availability_time": "getattr at eval",
        "timestamp_source": "algorithm.time",
        "episode_reset_behavior": "flags independent",
        "causal_verdict": "REJECTED",
        "rejection_reason_or_acceptance_proof": "CATEGORICAL_FLAGS_NOT_NUMERIC_RESTORATION_FRACTION",
    },
    {
        "candidate_id": "C11_RISK_GROSS_MULT",
        "owner_file": "cg_risk_tactical.py",
        "owner_function": "_CalcGrossMult / diag gross_mult_final",
        "field_name": "gross_mult_final",
        "numeric_semantics": "regime/DD gross multiplier for tactical sizing",
        "availability_time": "daily risk path",
        "timestamp_source": "risk calc time",
        "episode_reset_behavior": "not damage-episode scoped; not wired into D0",
        "causal_verdict": "REJECTED",
        "rejection_reason_or_acceptance_proof": "TACTICAL_GROSS_MULT_NOT_WITHHELD_RESTORATION; not D0-wired",
    },
    {
        "candidate_id": "C12_P1_P5_SHADOW",
        "owner_file": "cg_damage_duration_d03a_shadow.py",
        "owner_function": "ModelAShadowRouter.update",
        "field_name": "P1..P5 restoration_fraction",
        "numeric_semantics": "counterfactual shadow schedules/hysteresis",
        "availability_time": "same decision_time as D0.3A",
        "timestamp_source": "decision_time",
        "episode_reset_behavior": "resets with episode",
        "causal_verdict": "REJECTED",
        "rejection_reason_or_acceptance_proof": "P0_MUST_NOT_CONSUME_P1_P5_OUTPUTS",
    },
    {
        "candidate_id": "C13_D02_PRICE_RECOVERY",
        "owner_file": "cg_damage_duration_d02_memory.py",
        "owner_function": "recovery_fraction",
        "field_name": "PXY5_recovery_from_trough",
        "numeric_semantics": "price recovery from trough; feature not production restore intent",
        "availability_time": "feature_cutoff",
        "timestamp_source": "feature_cutoff",
        "episode_reset_behavior": "episode trough scoped",
        "causal_verdict": "REJECTED",
        "rejection_reason_or_acceptance_proof": "FEATURE_RECOVERY_NOT_PRODUCTION_RESTORE_PATH",
    },
    {
        "candidate_id": "C14_MACRO_RESTORE_FILL",
        "owner_file": "cg_macro_a1_diag.py",
        "owner_function": "macro restore timing",
        "field_name": "restore_fill_time",
        "numeric_semantics": "diagnostic restore fill timestamp for macro A1",
        "availability_time": "post-fill / later bar",
        "timestamp_source": "fill time",
        "episode_reset_behavior": "macro event scoped",
        "causal_verdict": "REJECTED",
        "rejection_reason_or_acceptance_proof": "FUTURE_OR_POST_FILL_DEPENDENT; macro diagnostic not production D0 path",
    },
]

P0_AUDIT = {
    "candidates": P0_CANDIDATES,
    "candidate_count": len(P0_CANDIDATES),
    "candidates_rejected": len(P0_CANDIDATES),
    "accepted_source": None,
    "p0_fallback_found": False,
    "reason": (
        "No existing causal read-only production scalar encodes the current "
        "production restoration fraction of withheld gross on [0,1]. All "
        "examined candidates are wrong semantics, absolute gross/caps, "
        "timing params, fill/holdings-lagged, categorical flags, or "
        "forbidden P1-P5/shadow outputs. Inventing a proxy is forbidden."
    ),
    "verdict": P0_SOURCE_VERDICT,
}


def resolve_p0_numeric_source(prod_state=None, decision_time=None, feature_cutoff=None):
    """P0 resolution. Production baseline is unobservable; never invents a proxy."""
    audit = {
        "candidates": P0_CANDIDATES,
        "candidate_count": len(P0_CANDIDATES),
        "candidates_rejected": len(P0_CANDIDATES),
        "accepted_source": None,
        "p0_fallback_found": False,
        "verdict": P0_SOURCE_VERDICT,
        "reason": P0_AUDIT["reason"],
        "decision_time": decision_time if isinstance(decision_time, datetime) else UNAVAILABLE,
        "feature_cutoff": feature_cutoff if isinstance(feature_cutoff, datetime) else UNAVAILABLE,
        "prod_state_keys": sorted(list((prod_state or {}).keys()))[:32],
    }
    ps = prod_state or {}
    # Explicit rejections (strengthen UNAVAILABLE; never fallback to 0/1)
    if ps.get("uses_future_fills") or ps.get("uses_same_bar_overlap"):
        audit["rejected"] = "FUTURE_OR_SAME_BAR_DEPENDENT"
        return UNAVAILABLE, P0_SOURCE_NAME, 0.0, audit
    if ps.get("from_p1_p5") or ps.get("policy_id") in POLICY_IDS[1:]:
        audit["rejected"] = "P0_MUST_NOT_CONSUME_P1_P5"
        return UNAVAILABLE, P0_SOURCE_NAME, 0.0, audit
    if ps.get("reconstructed_targets") or ps.get("from_later_holdings"):
        audit["rejected"] = "RECONSTRUCTION_OR_LATER_HOLDINGS"
        return UNAVAILABLE, P0_SOURCE_NAME, 0.0, audit
    if "p0_numeric_restore_fraction" in ps:
        # Production does not emit this field. Reject injected/synthetic values.
        stamp = ps.get("p0_source_time")
        if isinstance(feature_cutoff, datetime) and isinstance(stamp, datetime) and stamp > feature_cutoff:
            audit["rejected"] = "SOURCE_AFTER_FEATURE_CUTOFF"
        elif isinstance(decision_time, datetime) and isinstance(stamp, datetime) and stamp > decision_time:
            audit["rejected"] = "SOURCE_AFTER_DECISION"
        else:
            audit["rejected"] = "SYNTHETIC_OR_NONPRODUCTION_INJECTION"
        return UNAVAILABLE, P0_SOURCE_NAME, 0.0, audit
    if ps.get("default_fraction") in (0, 0.0, 1, 1.0):
        audit["rejected"] = "IMPLICIT_DEFAULT_FALLBACK_FORBIDDEN"
        return UNAVAILABLE, P0_SOURCE_NAME, 0.0, audit
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
