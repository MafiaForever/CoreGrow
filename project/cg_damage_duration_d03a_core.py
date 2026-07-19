# cg_damage_duration_d03a_core.py -- CG-DAMAGE-DURATION-D0.3A Model A + RecoveryScore.
# Diagnostic/shadow only. No orders, targets, History, or production mutations.
from __future__ import annotations
import math

UNAVAILABLE = "UNAVAILABLE"
EXPERIMENT = "CG-DAMAGE-DURATION-D0.3A"
PHASE = "D0.3A_MODEL_A_SHADOW_ROUTER_STATIC"
SCHEMA_VERSION = "D03A_MODEL_A_V1"
EPS = 1e-12
RECOVERY_CONFIDENCE_MIN = 0.55

# Positive weights
W_POS = {
    "PriceRecovery": 0.18,
    "BreadthRecovery": 0.18,
    "VolRelief": 0.12,
    "CoherenceImprovement": 0.12,
    "PersistenceDecay": 0.12,
    "FavorableCP": 0.08,
}
# Negative weights (enter as -weight * value)
W_NEG = {
    "RenewedDamage": 0.10,
    "NewLow": 0.04,
    "BreadthRelapse": 0.03,
    "AdverseCP": 0.02,
    "NAVRelapse": 0.01,
}
RECOVERY_WEIGHT_SUM = sum(W_POS.values()) + sum(W_NEG.values())

STRUCTURE_SCORE = {
    "SHOCK_REVERSAL": -1,
    "FAST_CHOP": 0,
    "ROTATION": 0,
    "BROAD_CHOP": 1,
    "TREND_DAMAGE": 2,
    "UNCERTAIN": UNAVAILABLE,
}


def _avail(x):
    if x is None or x is UNAVAILABLE or x == UNAVAILABLE:
        return False
    if isinstance(x, str) and str(x).upper() == "UNAVAILABLE":
        return False
    try:
        return math.isfinite(float(x))
    except Exception:
        return False


def _f(x):
    return float(x)


def clip(x, lo, hi):
    return max(lo, min(hi, float(x)))


def get(snap, key, default=UNAVAILABLE):
    if not isinstance(snap, dict):
        return default
    return snap.get(key, default)


def s_severity(d_state):
    s = str(d_state or UNAVAILABLE)
    if s == "NONE":
        return 0
    if s == "D30":
        return 1
    if s == "D45":
        return 2
    return UNAVAILABLE


def s_persistence(d45_persist_12):
    if not _avail(d45_persist_12):
        return UNAVAILABLE
    x = _f(d45_persist_12)
    if x < 0.17:
        return 0
    if x < 0.50:
        return 1
    return 2


def s_structure(structure_state):
    return STRUCTURE_SCORE.get(str(structure_state or ""), UNAVAILABLE)


def worst_dpe(dpe_60, delta_dpe):
    if _avail(dpe_60) and _avail(delta_dpe):
        return _f(dpe_60) - _f(delta_dpe)
    if _avail(dpe_60) and not _avail(delta_dpe):
        # cannot reconstruct worst; use current only as weak evidence via buckets below
        return UNAVAILABLE
    return UNAVAILABLE


def s_memory(snap_b):
    """Uses max available evidence; missing != zero."""
    dpe = get(snap_b, "DPE_60")
    ddpe = get(snap_b, "DeltaDPE_from_worst")
    wdpe = worst_dpe(dpe, ddpe)
    # also accept explicit worst_DPE_60 if present
    if _avail(get(snap_b, "worst_DPE_60")):
        wdpe = _f(get(snap_b, "worst_DPE_60"))
    max_d45 = get(snap_b, "max_D45_persist_12")
    wnc = get(snap_b, "worst_NegCoherence_60")

    any_avail = _avail(wdpe) or _avail(max_d45) or _avail(wnc)
    if not any_avail:
        return UNAVAILABLE

    if (_avail(wdpe) and _f(wdpe) >= 0.65) or (_avail(max_d45) and _f(max_d45) >= 0.50):
        return 2
    if (
        (_avail(wdpe) and _f(wdpe) >= 0.35)
        or (_avail(max_d45) and _f(max_d45) >= 0.17)
        or (_avail(wnc) and _f(wnc) >= 0.80)
    ):
        return 1
    return 0


def s_cp(cp_adverse, cp_favorable):
    adv_ok = _avail(cp_adverse)
    fav_ok = _avail(cp_favorable)
    if not adv_ok and not fav_ok:
        return UNAVAILABLE
    if adv_ok and _f(cp_adverse) >= 0.80:
        return 2
    if adv_ok and 0.60 <= _f(cp_adverse) < 0.80:
        return 1
    if fav_ok and _f(cp_favorable) >= 0.70 and (not adv_ok or _f(cp_adverse) < 0.60):
        return -1
    return 0


def s_recovery_from_score(recovery_score):
    if not _avail(recovery_score):
        return UNAVAILABLE
    x = _f(recovery_score)
    if x < 0.00:
        return 0
    if x < 0.25:
        return 1
    if x < 0.50:
        return 2
    return 3


def duration_forecast(score):
    if not _avail(score):
        return "ABSTAIN_P0_CURRENT"
    s = int(_f(score)) if abs(_f(score) - round(_f(score))) < 1e-9 else _f(score)
    # use numeric thresholds
    x = _f(score)
    if x <= 0:
        return "T0_TRANSIENT"
    if x <= 2:
        return "T1_INTRADAY_SHORT"
    if x <= 4:
        return "T2_INTRADAY_LONG"
    if x <= 6:
        return "T3_OVERNIGHT"
    return "T4_MULTIDAY"


def norm_price_recovery(x):
    if not _avail(x):
        return UNAVAILABLE
    return clip(2.0 * _f(x) - 1.0, -1.0, 1.0)


def norm_breadth_recovery(delta_breadth):
    if not _avail(delta_breadth):
        return UNAVAILABLE
    return clip(-_f(delta_breadth) / 0.60, -1.0, 1.0)


def norm_vol_relief(rv_relief):
    if not _avail(rv_relief):
        return UNAVAILABLE
    return clip(_f(rv_relief), -1.0, 1.0)


def norm_coherence_improvement(delta_coh):
    if not _avail(delta_coh):
        return UNAVAILABLE
    return clip(-_f(delta_coh) / 0.60, -1.0, 1.0)


def norm_persistence_decay(d45_p12, max_d45):
    if not _avail(d45_p12) or not _avail(max_d45):
        return UNAVAILABLE
    mx = _f(max_d45)
    if mx <= 0:
        return 0.0
    return clip(1.0 - _f(d45_p12) / mx, 0.0, 1.0)


def norm_favorable_cp(cp_fav):
    if not _avail(cp_fav):
        return UNAVAILABLE
    return clip(_f(cp_fav), 0.0, 1.0)


def renewed_damage(d_state):
    s = str(d_state or UNAVAILABLE)
    if s == "D45":
        return 1.0
    if s == "D30":
        return 0.5
    if s == "NONE":
        return 0.0
    return UNAVAILABLE


def new_low_flag(episode_id, current_trough, prior_trough, prior_episode_id):
    if episode_id is None or episode_id == UNAVAILABLE:
        return UNAVAILABLE
    if prior_episode_id != episode_id:
        return UNAVAILABLE
    if not _avail(current_trough) or not _avail(prior_trough):
        return UNAVAILABLE
    if _f(current_trough) < _f(prior_trough) - EPS:
        return 1.0
    return 0.0


def breadth_relapse(current_nb, prior_nb, episode_id, prior_episode_id):
    if episode_id is None or episode_id == UNAVAILABLE or prior_episode_id != episode_id:
        return UNAVAILABLE
    if not _avail(current_nb) or not _avail(prior_nb):
        return UNAVAILABLE
    return clip((_f(current_nb) - _f(prior_nb)) / 0.40, 0.0, 1.0)


def nav_relapse(current_nav_rec, prior_nav_rec, episode_id, prior_episode_id):
    if episode_id is None or episode_id == UNAVAILABLE or prior_episode_id != episode_id:
        return UNAVAILABLE
    if not _avail(current_nav_rec) or not _avail(prior_nav_rec):
        return UNAVAILABLE
    return clip((_f(prior_nav_rec) - _f(current_nav_rec)) / 0.50, 0.0, 1.0)


def adverse_cp_norm(cp_adverse):
    if not _avail(cp_adverse):
        return UNAVAILABLE
    return clip(_f(cp_adverse), 0.0, 1.0)


def compute_recovery_components(snap_b, snap_c, prior):
    """prior: dict with prior_trough, prior_breadth, prior_nav_rec, prior_episode_id."""
    prior = prior or {}
    eid = get(snap_b, "episode_id")
    peid = prior.get("prior_episode_id", UNAVAILABLE)
    comps = {
        "PriceRecovery": norm_price_recovery(get(snap_b, "PXY5_recovery_from_trough")),
        "BreadthRecovery": norm_breadth_recovery(get(snap_b, "DeltaBreadth_from_worst")),
        "VolRelief": norm_vol_relief(get(snap_b, "RV_relief")),
        "CoherenceImprovement": norm_coherence_improvement(get(snap_b, "DeltaCoherence_from_worst")),
        "PersistenceDecay": norm_persistence_decay(
            get(snap_b, "D45_persist_12"), get(snap_b, "max_D45_persist_12")),
        "FavorableCP": norm_favorable_cp(get(snap_c, "CP_favorable")),
        "RenewedDamage": renewed_damage(get(snap_b, "D_state")),
        "NewLow": new_low_flag(
            eid, get(snap_b, "episode_trough_PXY5"),
            prior.get("prior_trough", UNAVAILABLE), peid),
        "BreadthRelapse": breadth_relapse(
            get(snap_b, "NegBreadth_60"), prior.get("prior_breadth", UNAVAILABLE), eid, peid),
        "AdverseCP": adverse_cp_norm(get(snap_c, "CP_adverse")),
        "NAVRelapse": nav_relapse(
            get(snap_b, "NAV_recovery_from_trough"),
            prior.get("prior_nav_rec", UNAVAILABLE), eid, peid),
    }
    return comps


def compute_recovery_score(components):
    avail = {}
    weighted_sum = 0.0
    available_weight = 0.0
    for name, w in W_POS.items():
        v = components.get(name, UNAVAILABLE)
        ok = _avail(v)
        avail[name] = ok
        if ok:
            available_weight += w
            weighted_sum += w * _f(v)
    for name, w in W_NEG.items():
        v = components.get(name, UNAVAILABLE)
        ok = _avail(v)
        avail[name] = ok
        if ok:
            available_weight += w
            weighted_sum -= w * _f(v)
    if available_weight <= EPS:
        score = UNAVAILABLE
    else:
        score = clip(weighted_sum / available_weight, -1.0, 1.0)
    return {
        "recovery_components": dict(components),
        "recovery_component_availability": avail,
        "recovery_available_weight": available_weight if available_weight > EPS else 0.0,
        "evidence_coverage": available_weight if available_weight > EPS else 0.0,
        "RecoveryScore": score,
    }


def recovery_ladder_fraction(recovery_score):
    if not _avail(recovery_score):
        return UNAVAILABLE, "ABSTAIN"
    x = _f(recovery_score)
    if x < -0.35:
        return 0.00, "HOLD_CURRENT_PROTECTION"
    if x < 0.00:
        return 0.00, "NO_RESTORATION"
    if x < 0.25:
        return 0.25, "RESTORE_025"
    if x < 0.50:
        return 0.50, "RESTORE_050"
    if x < 0.75:
        return 0.75, "RESTORE_075"
    return 1.00, "RESTORE_100"


def compute_model_a(snap_b, snap_c, recovery_score):
    sev = s_severity(get(snap_b, "D_state"))
    pers = s_persistence(get(snap_b, "D45_persist_12"))
    struct = s_structure(get(snap_c, "structure_state"))
    mem = s_memory(snap_b)
    cp = s_cp(get(snap_c, "CP_adverse"), get(snap_c, "CP_favorable"))
    srec = s_recovery_from_score(recovery_score)
    comps = {
        "S_severity": sev,
        "S_persistence": pers,
        "S_structure": struct,
        "S_memory": mem,
        "S_cp": cp,
        "S_recovery": srec,
    }
    model_a_coverage = sum(1 for k in ("S_severity", "S_persistence", "S_structure", "S_memory", "S_cp")
                           if _avail(comps[k])) / 5.0
    all_six = all(_avail(comps[k]) for k in comps)
    if not all_six:
        score = UNAVAILABLE
        forecast = "ABSTAIN_P0_CURRENT"
        reason = "missing_duration_component"
    else:
        score = (
            _f(sev) + _f(pers) + _f(struct) + _f(mem) + _f(cp) - _f(srec)
        )
        forecast = duration_forecast(score)
        reason = UNAVAILABLE
    return {
        **comps,
        "DurationRiskScore": score,
        "DurationForecast": forecast,
        "duration_component_map": dict(comps),
        "duration_abstention_reason": reason,
        "ModelA_component_coverage": model_a_coverage,
    }


def compute_recovery_confidence(evidence_coverage, model_a_coverage, structure_confidence):
    sc = _f(structure_confidence) if _avail(structure_confidence) else 0.0
    return clip(0.50 * _f(evidence_coverage) + 0.30 * _f(model_a_coverage) + 0.20 * sc, 0.0, 1.0)


def model_a_contract():
    return {
        "schema_version": SCHEMA_VERSION,
        "experiment": EXPERIMENT,
        "phase": PHASE,
        "formula": "S_severity+S_persistence+S_structure+S_memory+S_cp-S_recovery",
        "recovery_weight_sum": RECOVERY_WEIGHT_SUM,
        "RECOVERY_CONFIDENCE_MIN": RECOVERY_CONFIDENCE_MIN,
        "change_point_veto": "FORBIDDEN",
        "production_actions": "FORBIDDEN",
    }


def recovery_score_contract():
    return {
        "positive_weights": dict(W_POS),
        "negative_weights": dict(W_NEG),
        "weight_sum": RECOVERY_WEIGHT_SUM,
        "missing_imputation": "FORBIDDEN",
        "renormalize_available_weight": True,
    }
