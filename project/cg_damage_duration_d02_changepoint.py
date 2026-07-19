# cg_damage_duration_d02_changepoint.py -- CG-DAMAGE-DURATION-D0.2C change-point features.
# Diagnostic evidence only. No veto, recovery, orders, subscriptions, or History.
from __future__ import annotations
import json, math, re
from copy import deepcopy
from datetime import datetime, timedelta

UNAVAILABLE = "UNAVAILABLE"
EXPERIMENT = "CG-DAMAGE-DURATION-D0.2C"
PHASE = "D0.2C_CHANGE_POINT_STRUCTURE_FEATURES"
SCHEMA_VERSION = "D02C_CP_V1"

CP_WARMUP_VALID_CHECKPOINTS = 24
CP_ALPHA = 0.10
CP_SCALE_ALPHA = 0.10
CP_K = 0.50
CP_H = 5.00
CP_SCORE_TRIGGER = 0.70
CP_COOLDOWN_MINUTES = 15
CP_EPS = 1e-12
CP_CUSUM_CAP = 10.0 * CP_H

CHANNELS = ("mean", "vol", "corr")
CHANNEL_INPUTS = {
    "mean": "PXY5_ret_15",
    "vol": "RV60",
    "corr": "MedianCorr_60",
}

FORBIDDEN_RE = re.compile(
    r"(?<![A-Za-z_])(History|AddEquity|add_equity|AddData|add_data|SetHoldings|set_holdings|"
    r"MarketOrder|market_order|LimitOrder|StopMarketOrder|Liquidate)\s*\("
    r"|PortfolioTarget\b|ObjectStore\.(Save|Delete)\b|Schedule\.On\b"
)


def _avail(x):
    if x is None or x is UNAVAILABLE or x == UNAVAILABLE:
        return False
    if isinstance(x, str) and x.upper() == "UNAVAILABLE":
        return False
    try:
        return math.isfinite(float(x))
    except Exception:
        return False


def _f(x):
    return float(x)


def clip01(x):
    return max(0.0, min(1.0, float(x)))


def map_cusum_score(s):
    if not _avail(s):
        return UNAVAILABLE
    return 1.0 - math.exp(-float(s) / CP_H)


def bound_cusum(s):
    return max(0.0, min(float(s), CP_CUSUM_CAP))


def channel_raw_value(channel, snap):
    """Extract channel observation; vol uses log(max(RV60, EPS))."""
    key = CHANNEL_INPUTS[channel]
    v = (snap or {}).get(key, UNAVAILABLE)
    if not _avail(v):
        return UNAVAILABLE
    if channel == "vol":
        return math.log(max(_f(v), CP_EPS))
    return _f(v)


class ChannelState:
    __slots__ = (
        "mu", "var", "valid_count", "unavailable_count",
        "cusum_down", "cusum_up",
    )

    def __init__(self):
        self.mu = None
        self.var = None  # None => unavailable
        self.valid_count = 0
        self.unavailable_count = 0
        self.cusum_down = 0.0
        self.cusum_up = 0.0

    def copy_prior(self):
        return {
            "mu": self.mu, "var": self.var, "valid_count": self.valid_count,
            "cusum_down": self.cusum_down, "cusum_up": self.cusum_up,
        }


class ChangePointEngine:
    """Online prior-only EWMA/CUSUM change-point scores over D0.2B snapshots."""

    def __init__(self):
        self.channels = {c: ChannelState() for c in CHANNELS}
        self.last_checkpoint = None
        self.last_alert_time = None
        self.last_alert_direction = "NONE"
        self.repeat_suppressed = 0
        self.episode_id = UNAVAILABLE
        self.cp_adverse_peak = UNAVAILABLE
        self.counters = {
            "checkpoints": 0, "duplicate_blocked": 0, "alerts_eligible": 0,
            "alerts_suppressed": 0,
        }
        self.last_cp = None

    def process(self, snap):
        ck = (snap or {}).get("checkpoint_key")
        if ck is not None and ck == self.last_checkpoint:
            self.counters["duplicate_blocked"] += 1
            out = dict(self.last_cp) if self.last_cp else self._empty_out(snap)
            out["CP_repeat_suppressed"] = True
            out["_duplicate_blocked"] = True
            return out

        decision_time = (snap or {}).get("decision_time")
        episode_id = (snap or {}).get("episode_id", UNAVAILABLE)
        if episode_id != self.episode_id:
            self.episode_id = episode_id
            self.cp_adverse_peak = UNAVAILABLE

        scores = {}
        avail_map = {}
        warmup = {}
        unavail = {}

        for ch in CHANNELS:
            st = self.channels[ch]
            warmup[ch] = int(st.valid_count)
            unavail[ch] = int(st.unavailable_count)
            x = channel_raw_value(ch, snap)
            if not _avail(x):
                st.unavailable_count += 1
                unavail[ch] = int(st.unavailable_count)
                scores[ch] = {"down": UNAVAILABLE, "up": UNAVAILABLE}
                avail_map[ch] = False
                continue

            prior = st.copy_prior()
            score_ready = (
                prior["valid_count"] >= CP_WARMUP_VALID_CHECKPOINTS
                and prior["mu"] is not None
                and prior["var"] is not None
            )
            if not score_ready:
                scores[ch] = {"down": UNAVAILABLE, "up": UNAVAILABLE}
                avail_map[ch] = False
            else:
                scale = math.sqrt(max(float(prior["var"]), CP_EPS))
                z = (_f(x) - float(prior["mu"])) / scale
                if ch == "mean":
                    st.cusum_down = bound_cusum(prior["cusum_down"] + (-z) - CP_K)
                    st.cusum_up = bound_cusum(prior["cusum_up"] + z - CP_K)
                    scores[ch] = {
                        "down": map_cusum_score(st.cusum_down),
                        "up": map_cusum_score(st.cusum_up),
                    }
                elif ch == "vol":
                    st.cusum_up = bound_cusum(prior["cusum_up"] + z - CP_K)
                    st.cusum_down = 0.0
                    scores[ch] = {
                        "down": UNAVAILABLE,
                        "up": map_cusum_score(st.cusum_up),
                    }
                else:  # corr
                    st.cusum_down = bound_cusum(prior["cusum_down"] + (-z) - CP_K)
                    st.cusum_up = bound_cusum(prior["cusum_up"] + z - CP_K)
                    scores[ch] = {
                        "down": map_cusum_score(st.cusum_down),
                        "up": map_cusum_score(st.cusum_up),
                    }
                avail_map[ch] = True

            # update baseline AFTER scoring
            if st.valid_count == 0:
                st.mu = _f(x)
                st.var = None
                st.valid_count = 1
            else:
                mu_prior = float(st.mu)
                residual = _f(x) - mu_prior
                st.mu = (1.0 - CP_ALPHA) * mu_prior + CP_ALPHA * _f(x)
                if st.var is None:
                    st.var = residual * residual
                else:
                    st.var = (
                        (1.0 - CP_SCALE_ALPHA) * float(st.var)
                        + CP_SCALE_ALPHA * (residual * residual)
                    )
                st.valid_count += 1
            warmup[ch] = int(st.valid_count)

        cp_mean_down = scores["mean"]["down"]
        cp_mean_up = scores["mean"]["up"]
        cp_vol = scores["vol"]["up"]
        cp_corr_down = scores["corr"]["down"]
        cp_corr_up = scores["corr"]["up"]

        adverse_parts = [v for v in (cp_mean_down, cp_vol, cp_corr_down) if _avail(v)]
        fav_parts = [v for v in (cp_mean_up, cp_corr_up) if _avail(v)]
        cp_adverse = max(adverse_parts) if adverse_parts else UNAVAILABLE
        cp_favorable = max(fav_parts) if fav_parts else UNAVAILABLE

        if _avail(cp_adverse):
            if not _avail(self.cp_adverse_peak) or float(cp_adverse) > float(self.cp_adverse_peak):
                self.cp_adverse_peak = float(cp_adverse)

        alert = self._alert(cp_adverse, cp_favorable, decision_time)

        out = {
            "CP_mean_down": cp_mean_down,
            "CP_mean_up": cp_mean_up,
            "CP_vol": cp_vol,
            "CP_corr_down": cp_corr_down,
            "CP_corr_up": cp_corr_up,
            "CP_adverse": cp_adverse,
            "CP_favorable": cp_favorable,
            "CP_component_availability": {
                "mean_down": _avail(cp_mean_down),
                "mean_up": _avail(cp_mean_up),
                "vol": _avail(cp_vol),
                "corr_down": _avail(cp_corr_down),
                "corr_up": _avail(cp_corr_up),
            },
            "CP_alert_direction": alert["direction"],
            "CP_alert_eligible": alert["eligible"],
            "CP_cooldown_remaining_minutes": alert["cooldown_remaining"],
            "CP_repeat_suppressed": alert["repeat_suppressed"],
            "CP_last_alert_time": self.last_alert_time if self.last_alert_time is not None else UNAVAILABLE,
            "CP_warmup_counts": dict(warmup),
            "CP_unavailable_counts": dict(unavail),
            "CP_adverse_peak_in_current_episode": self.cp_adverse_peak,
            "_duplicate_blocked": False,
        }
        self.last_checkpoint = ck
        self.last_cp = out
        self.counters["checkpoints"] += 1
        return out

    def _alert(self, cp_adverse, cp_favorable, decision_time):
        adv_ok = _avail(cp_adverse) and float(cp_adverse) >= CP_SCORE_TRIGGER
        fav_ok = _avail(cp_favorable) and float(cp_favorable) >= CP_SCORE_TRIGGER
        if not adv_ok and not fav_ok:
            rem = self._cooldown_remaining(decision_time)
            return {
                "direction": "NONE" if (_avail(cp_adverse) or _avail(cp_favorable)) else "UNAVAILABLE",
                "eligible": False,
                "cooldown_remaining": rem,
                "repeat_suppressed": False,
            }
        if adv_ok and fav_ok:
            if abs(float(cp_adverse) - float(cp_favorable)) <= CP_EPS:
                direction = "MIXED"
            elif float(cp_adverse) > float(cp_favorable):
                direction = "ADVERSE"
            else:
                direction = "FAVORABLE"
        elif adv_ok:
            direction = "ADVERSE"
        else:
            direction = "FAVORABLE"

        rem = self._cooldown_remaining(decision_time)
        eligible = rem <= CP_EPS
        if eligible:
            self.last_alert_time = decision_time
            self.last_alert_direction = direction
            self.counters["alerts_eligible"] += 1
            return {
                "direction": direction, "eligible": True,
                "cooldown_remaining": 0.0, "repeat_suppressed": False,
            }
        self.repeat_suppressed += 1
        self.counters["alerts_suppressed"] += 1
        return {
            "direction": direction, "eligible": False,
            "cooldown_remaining": rem, "repeat_suppressed": True,
        }

    def _cooldown_remaining(self, decision_time):
        if self.last_alert_time is None or decision_time is None:
            return 0.0
        try:
            elapsed = (decision_time - self.last_alert_time).total_seconds() / 60.0
        except Exception:
            return 0.0
        return max(0.0, float(CP_COOLDOWN_MINUTES) - float(elapsed))

    def _empty_out(self, snap):
        return {
            "CP_mean_down": UNAVAILABLE, "CP_mean_up": UNAVAILABLE, "CP_vol": UNAVAILABLE,
            "CP_corr_down": UNAVAILABLE, "CP_corr_up": UNAVAILABLE,
            "CP_adverse": UNAVAILABLE, "CP_favorable": UNAVAILABLE,
            "CP_component_availability": {},
            "CP_alert_direction": "UNAVAILABLE", "CP_alert_eligible": False,
            "CP_cooldown_remaining_minutes": 0.0, "CP_repeat_suppressed": False,
            "CP_last_alert_time": UNAVAILABLE,
            "CP_warmup_counts": {c: self.channels[c].valid_count for c in CHANNELS},
            "CP_unavailable_counts": {c: self.channels[c].unavailable_count for c in CHANNELS},
            "CP_adverse_peak_in_current_episode": self.cp_adverse_peak,
        }


def changepoint_contract():
    return {
        "schema_version": SCHEMA_VERSION,
        "experiment": EXPERIMENT,
        "phase": PHASE,
        "CP_WARMUP_VALID_CHECKPOINTS": CP_WARMUP_VALID_CHECKPOINTS,
        "CP_ALPHA": CP_ALPHA,
        "CP_SCALE_ALPHA": CP_SCALE_ALPHA,
        "CP_K": CP_K,
        "CP_H": CP_H,
        "CP_SCORE_TRIGGER": CP_SCORE_TRIGGER,
        "CP_COOLDOWN_MINUTES": CP_COOLDOWN_MINUTES,
        "channels": list(CHANNELS),
        "channel_inputs": dict(CHANNEL_INPUTS),
        "production_veto": "FORBIDDEN",
        "scoring_order": ["read_prior", "score", "update_cusum", "update_baseline"],
    }


def sanitize(obj):
    if isinstance(obj, dict):
        return {k: sanitize(v) for k, v in obj.items()}
    if isinstance(obj, float) and not math.isfinite(obj):
        return UNAVAILABLE
    return obj
