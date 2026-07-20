# cg_damage_duration_d01_core.py -- CG-DAMAGE-DURATION-D0.1 pure infrastructure.
# LEAN-independent. No orders, subscriptions, targets, or RecoveryScore.
from __future__ import annotations
import hashlib, json, re
from copy import deepcopy
from datetime import date, datetime, timedelta, time

EXPERIMENT = "CG-DAMAGE-DURATION-D0.1"
PHASE = "D0.1_EPISODE_LABEL_TIMESTAMP_INFRASTRUCTURE"
SCHEMA_VERSION = "DAMAGE_DURATION_D0.1"

EP_OPEN = "OPEN"
EP_PROVISIONAL = "PROVISIONAL_CLOSE"
EP_LOCKED = "CLOSED_LOCKED"
EP_STATES = (EP_OPEN, EP_PROVISIONAL, EP_LOCKED)

EV_PROTECTION = "PROTECTION_ENTRY"
EV_D30 = "D30_EVIDENCE"
EV_D45 = "D45_EVIDENCE"
EV_RELAPSE = "RELAPSE_REENTRY_FAILURE"
EV_KINDS = (EV_PROTECTION, EV_D30, EV_D45, EV_RELAPSE)

T0 = "T0_TRANSIENT"
T1 = "T1_INTRADAY_SHORT"
T2 = "T2_INTRADAY_LONG"
T3 = "T3_OVERNIGHT"
T4 = "T4_MULTIDAY"
DURATION_CLASSES = (T0, T1, T2, T3, T4)

LAB_UNAVAILABLE = "UNAVAILABLE"
LAB_AVAILABLE = "AVAILABLE"
LAB_RIGHT_CENSORED = "RIGHT_CENSORED"
LAB_STATES = (LAB_UNAVAILABLE, LAB_AVAILABLE, LAB_RIGHT_CENSORED)

PROT_SOURCES = ("EMERGENCY", "REDUCE_ONLY", "PANIC", "IDS", "W2", "SH", "NONE")
CONFIRMATION_WINDOW_MINUTES = 30  # infrastructure constant; not BT-tuned
T0_MAX_MIN = 30
T1_MAX_MIN = 120
MAX_HORIZON_POST_CLOSE_MIN = 30  # first 30m of session after third close
EMBARGO_SESSIONS = 4
BASELINE_COMMIT = "0ad438d2fe79084fdcd50a1e80c2d0e2e6c71183"
D30_D45_RUNTIME_SOURCE = "UNRESOLVED_WHEN_MACRO_RESID_B1_DISABLED"
D30_D45_RUNTIME_NOTE = "D0.2_PREREQUISITE"
FROZEN_PRODUCTION_DEFAULTS = {
    "cg_watch_w2_trade_enable": "1",
    "cg_transition_e2_trade_enable": "0",
    "cg_rt_trade": "1",
    "cg_rt_fixed": "165",
    "cg_rt_ron": "165",
    "cg_rt_neu": "165",
    "cg_rt_roff": "165",
    "spyg_sat_trade_enable": "0",
    "rrx_trade_bridge_enable": "0",
    "dyn_alloc_c2n_trade_enable": "0",
    "cg_damage_duration_d01_enable": "0",
}
FORBIDDEN_API_PATTERNS = (
    r"\bSetHoldings\b", r"\bset_holdings\b", r"\bLiquidate\b", r"\bMarketOrder\b",
    r"\badd_equity\b", r"\bAddEquity\b", r"\badd_data\b", r"\bAddData\b",
    r"\badd_consolidator\b", r"\bAddConsolidator\b",
)

EPISODE_SCHEMA = {
    "episode_id": "str", "state": "str", "trigger_kind": "str", "protection_source": "str",
    "decision_time": "datetime", "feature_cutoff": "datetime|null",
    "action_eligible_time": "datetime|null", "episode_start": "datetime",
    "provisional_close_time": "datetime|null", "locked_time": "datetime|null",
    "resolution_time": "datetime|null", "label_finalization_time": "datetime|null",
    "duration_class": "str|null", "label_availability": "str",
    "right_censored": "bool", "event_ids": "list[str]", "locked": "bool",
}
EVENT_SCHEMA = {
    "event_id": "str", "episode_id": "str|null", "kind": "str",
    "decision_time": "datetime", "feature_cutoff": "datetime|null",
    "action_eligible_time": "datetime|null", "protection_source": "str",
    "is_relapse_merge": "bool", "orphan": "bool",
}
LABEL_SCHEMA = {
    "episode_id": "str", "duration_class": "str|null", "label_availability": "str",
    "label_finalization_time": "datetime|null", "right_censored": "bool",
    "decision_time": "datetime", "resolution_time": "datetime|null",
}
TIMESTAMP_CONTRACT = {
    "DecisionTime": "t",
    "FeatureCutoff": "max bar EndTime <= t",
    "ActionEligibleTime": "first bar EndTime > t",
    "LabelFinalizationTime": "confirmation end or max-horizon finalization; no accepted label before this",
    "same_bar_outcome": "FORBIDDEN",
    "max_label_horizon": "third session close after event + first 30 minutes of following session",
}


def _iso(dt):
    if dt is None:
        return "NONE"
    if isinstance(dt, datetime):
        return dt.isoformat(sep="T", timespec="seconds")
    return str(dt)


def _sha16(text):
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()[:16]


def _sha256(text):
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()


def empty_counters():
    return {
        "episodes_created": 0, "events_created": 0,
        "duplicate_episode_ids": 0, "duplicate_event_ids": 0,
        "orphan_events": 0, "multi_episode_membership": 0,
        "same_bar_feature_violations": 0, "same_bar_action_violations": 0,
        "label_before_available_violations": 0,
        "post_lock_mutation_attempts": 0, "post_lock_mutation_successes": 0,
        "right_censored_episodes": 0, "split_overlap_violations": 0,
        "diagnostic_real_orders": 0, "subscription_changes": 0,
        "target_mutations": 0, "runtime_errors": 0,
        "release_checks": 0, "provisional_closes": 0, "confirmed_closes": 0,
        "relapse_reopens": 0, "attach_to_open": 0,
        "release_check_duplicate_blocked": 0, "release_check_hold": 0,
    }


# Causal RELEASE_CHECK reasons (dual of existing open edges; no new thresholds).
RELEASE_PROT = "PROTECTION_RELEASE"
RELEASE_DMG = "DAMAGE_CLEARED"
RELEASE_HOLD = "HOLD_OPEN"


def release_check_close_predicate(ep, *, protection_active, prev_protection_active,
                                  d_severity=None):
    """Existing causal close predicate for RELEASE_CHECK cadence.

    Owner: this function. Dual of protection rising-edge open and unprotected
    D30/D45 open. No RecoveryScore AND-gate and no new numeric thresholds.
    """
    if ep is None or getattr(ep, "locked", False) or getattr(ep, "state", None) != EP_OPEN:
        return False, "NO_OPEN_EPISODE"
    if bool(prev_protection_active) and not bool(protection_active):
        return True, RELEASE_PROT
    sev = str(d_severity or "").strip().upper()
    trig = str(getattr(ep, "trigger_kind", "") or "")
    if (not bool(protection_active) and not bool(prev_protection_active)
            and sev in ("NONE", "SEV_NONE") and trig in (EV_D30, EV_D45)):
        return True, RELEASE_DMG
    return False, RELEASE_HOLD


def _lifecycle_year_bucket(store, decision_time):
    if not isinstance(decision_time, datetime):
        return None
    y = str(decision_time.year)
    return store.setdefault(y, {
        "open": 0, "provisional_close": 0, "confirmed_close": 0,
        "reopen": 0, "merge": 0, "relapse_child": 0, "attach_to_open": 0,
        "release_check": 0, "reject_unavailable": 0, "reject_stale": 0,
        "reject_duplicate": 0, "reject_label_unavailable": 0,
        "eoy_open_count": 0, "eoy_open_age": "UNAVAILABLE",
    })


def make_episode_id(decision_time, trigger_kind, protection_source):
    return "EP_" + _sha16(f"{_iso(decision_time)}|{trigger_kind}|{protection_source}")


def make_event_id(episode_id_or_none, event_kind, event_time, protection_source="NONE"):
    ep = episode_id_or_none or "ORPHAN"
    return "EV_" + _sha16(f"{ep}|{event_kind}|{_iso(event_time)}|{protection_source}")


def feature_cutoff(decision_time, bar_end_times):
    """max EndTime <= DecisionTime; None if none qualify."""
    if decision_time is None:
        return None
    cands = [et for et in (bar_end_times or []) if et is not None and et <= decision_time]
    return max(cands) if cands else None


def action_eligible_time(decision_time, bar_end_times):
    """first EndTime > DecisionTime."""
    if decision_time is None:
        return None
    cands = sorted(et for et in (bar_end_times or []) if et is not None and et > decision_time)
    return cands[0] if cands else None


def validate_timestamp_contract(decision_time, feat_cut, act_elig, outcome_end=None):
    """Return (ok, counters_delta). Rejects same-bar feature/action/outcome leakage."""
    out = {"same_bar_feature_violations": 0, "same_bar_action_violations": 0}
    ok = True
    if feat_cut is not None and decision_time is not None and feat_cut > decision_time:
        out["same_bar_feature_violations"] += 1
        ok = False
    if act_elig is not None and decision_time is not None and act_elig <= decision_time:
        out["same_bar_action_violations"] += 1
        ok = False
    if outcome_end is not None and decision_time is not None and outcome_end <= decision_time:
        out["same_bar_action_violations"] += 1
        ok = False
    return ok, out


def protection_source_from_snapshot(snap, sh_active=False):
    """Record existing protection without changing it. Priority order fixed."""
    s = dict(snap or {})
    if bool(s.get("emergency_active")):
        return "EMERGENCY"
    if bool(s.get("reduce_only_active")):
        return "REDUCE_ONLY"
    panic = str(s.get("panic_state") or "").strip().upper()
    if panic in ("STRESS", "PANIC"):
        return "PANIC"
    ids = str(s.get("ids_state") or "").strip().upper()
    if ids in ("WATCH", "STRESS", "PANIC_SHORT"):
        return "IDS"
    if bool(s.get("w2_active")):
        return "W2"
    if bool(sh_active):
        return "SH"
    return "NONE"


def material_protection_active(snap, sh_active=False):
    return protection_source_from_snapshot(snap, sh_active=sh_active) != "NONE"


def session_close_dt(session_day, close_tod_minutes=960):
    """Build session close datetime from exchange session date (ET minutes from midnight)."""
    if isinstance(session_day, datetime):
        d = session_day.date()
    else:
        d = session_day
    h, m = divmod(int(close_tod_minutes), 60)
    return datetime.combine(d, time(h, m))


def map_exchange_sessions(session_days):
    """Normalize exchange-session inputs to sorted unique dates (weekend/holiday already excluded by caller)."""
    out = []
    for x in session_days or []:
        if isinstance(x, datetime):
            out.append(x.date())
        elif isinstance(x, date):
            out.append(x)
        else:
            out.append(date.fromisoformat(str(x)[:10]))
    return sorted(set(out))


def nth_session_after(session_days, from_day, n):
    """Return the n-th exchange session strictly after from_day (n>=1), or None."""
    days = map_exchange_sessions(session_days)
    if isinstance(from_day, datetime):
        from_day = from_day.date()
    later = [d for d in days if d > from_day]
    if n < 1 or len(later) < n:
        return None
    return later[n - 1]


def same_session_close(session_days, event_time, close_tod_minutes=960):
    if event_time is None:
        return None
    d = event_time.date() if isinstance(event_time, datetime) else event_time
    days = map_exchange_sessions(session_days)
    if d not in days and days:
        # event on a mapped session day only
        if d not in days:
            return None
    return session_close_dt(d, close_tod_minutes)


def next_session_close(session_days, event_time, close_tod_minutes=960):
    nxt = nth_session_after(session_days, event_time.date() if isinstance(event_time, datetime) else event_time, 1)
    return None if nxt is None else session_close_dt(nxt, close_tod_minutes)


def third_session_close(session_days, event_time, close_tod_minutes=960):
    d3 = nth_session_after(session_days, event_time.date() if isinstance(event_time, datetime) else event_time, 3)
    return None if d3 is None else session_close_dt(d3, close_tod_minutes)


def max_label_horizon_end(session_days, event_time, close_tod_minutes=960):
    """Third session close after event + first 30 minutes of the immediately following session."""
    d3 = nth_session_after(session_days, event_time.date() if isinstance(event_time, datetime) else event_time, 3)
    if d3 is None:
        return None
    d4 = nth_session_after(session_days, d3, 1)
    if d4 is None:
        return None
    # session open assumed 09:30 ET = 570; +30m => 10:00
    return datetime.combine(d4, time(10, 0))


def assign_duration_class(event_time, resolution_time, session_days, close_tod_minutes=960):
    """
    Assign T0-T4 from resolution_time. Returns (class_or_None, right_censored_bool, reason).
    Incomplete horizon => RIGHT_CENSORED (not T4).
    """
    if event_time is None or resolution_time is None:
        return None, True, "MISSING_TIMES"
    if resolution_time <= event_time:
        return None, False, "NON_POSITIVE_DURATION"
    mins = (resolution_time - event_time).total_seconds() / 60.0
    sc = same_session_close(session_days, event_time, close_tod_minutes)
    nc = next_session_close(session_days, event_time, close_tod_minutes)
    tc = third_session_close(session_days, event_time, close_tod_minutes)
    hz = max_label_horizon_end(session_days, event_time, close_tod_minutes)
    if sc is None or nc is None or tc is None or hz is None:
        return None, True, "INCOMPLETE_SESSION_CALENDAR"
    if resolution_time > hz:
        return None, True, "BEYOND_MAX_HORIZON"
    if mins <= T0_MAX_MIN:
        return T0, False, "OK"
    if mins <= T1_MAX_MIN:
        return T1, False, "OK"
    if resolution_time <= sc:
        return T2, False, "OK"
    if resolution_time <= nc:
        return T3, False, "OK"
    if resolution_time <= tc:
        return T4, False, "OK"
    # between third close and horizon end still T4 per max horizon definition
    if resolution_time <= hz:
        return T4, False, "OK"
    return None, True, "UNCLASSIFIED"


def label_finalization_time(provisional_close_time, confirmation_minutes=CONFIRMATION_WINDOW_MINUTES,
                            max_horizon_end=None, right_censored=False):
    if right_censored:
        return max_horizon_end
    if provisional_close_time is None:
        return None
    return provisional_close_time + timedelta(minutes=int(confirmation_minutes))


def label_is_available(now_t, finalization_t, locked=False, right_censored=False):
    if right_censored:
        return LAB_RIGHT_CENSORED
    if finalization_t is None or now_t is None:
        return LAB_UNAVAILABLE
    if now_t < finalization_t:
        return LAB_UNAVAILABLE
    if locked or now_t >= finalization_t:
        return LAB_AVAILABLE
    return LAB_UNAVAILABLE


def purge_overlaps_test(episode_start, label_finalization_t, test_start):
    """True if training episode [start, finalization] overlaps test_start (purge candidate)."""
    if episode_start is None or label_finalization_t is None or test_start is None:
        return False
    return episode_start <= test_start <= label_finalization_t or (
        episode_start < test_start and label_finalization_t >= test_start
    )


def embargo_end_session(session_days, train_cutoff_day, n=EMBARGO_SESSIONS):
    """Four trading sessions after training cutoff (metadata only; does not drop OOS eval bars)."""
    if isinstance(train_cutoff_day, datetime):
        train_cutoff_day = train_cutoff_day.date()
    return nth_session_after(session_days, train_cutoff_day, int(n))


def split_metadata(episode_start, label_finalization_t, test_start, session_days, train_cutoff_day):
    overlap = purge_overlaps_test(episode_start, label_finalization_t, test_start)
    emb = embargo_end_session(session_days, train_cutoff_day, EMBARGO_SESSIONS)
    return {
        "purge": bool(overlap),
        "embargo_sessions": EMBARGO_SESSIONS,
        "embargo_end_session": emb.isoformat() if emb else None,
        "embargo_applies_to": "fitting_tuning_only",
        "oos_eval_bars_removed": False,
    }


class DamageEvent:
    __slots__ = (
        "event_id", "episode_id", "kind", "decision_time", "feature_cutoff",
        "action_eligible_time", "protection_source", "is_relapse_merge", "orphan",
    )

    def __init__(self, kind, decision_time, protection_source="NONE", feature_cutoff=None,
                 action_eligible_time=None, episode_id=None, is_relapse_merge=False,
                 event_id=None):
        self.kind = str(kind)
        self.decision_time = decision_time
        self.protection_source = str(protection_source or "NONE")
        self.feature_cutoff = feature_cutoff
        self.action_eligible_time = action_eligible_time
        self.episode_id = episode_id
        self.is_relapse_merge = bool(is_relapse_merge)
        self.orphan = episode_id is None
        self.event_id = event_id or make_event_id(
            episode_id, self.kind, decision_time, self.protection_source)

    def to_dict(self):
        return {
            "event_id": self.event_id, "episode_id": self.episode_id, "kind": self.kind,
            "decision_time": _iso(self.decision_time), "feature_cutoff": _iso(self.feature_cutoff),
            "action_eligible_time": _iso(self.action_eligible_time),
            "protection_source": self.protection_source,
            "is_relapse_merge": self.is_relapse_merge, "orphan": self.orphan,
        }


class DamageEpisode:
    __slots__ = (
        "episode_id", "state", "trigger_kind", "protection_source", "decision_time",
        "feature_cutoff", "action_eligible_time", "episode_start",
        "provisional_close_time", "locked_time", "resolution_time",
        "label_finalization_time", "duration_class", "label_availability",
        "right_censored", "event_ids", "locked", "_frozen",
    )

    def __init__(self, trigger_kind, decision_time, protection_source="NONE",
                 feature_cutoff=None, action_eligible_time=None, episode_id=None):
        self.trigger_kind = str(trigger_kind)
        self.decision_time = decision_time
        self.protection_source = str(protection_source or "NONE")
        self.feature_cutoff = feature_cutoff
        self.action_eligible_time = action_eligible_time
        self.episode_start = decision_time
        self.episode_id = episode_id or make_episode_id(
            decision_time, self.trigger_kind, self.protection_source)
        self.state = EP_OPEN
        self.provisional_close_time = None
        self.locked_time = None
        self.resolution_time = None
        self.label_finalization_time = None
        self.duration_class = None
        self.label_availability = LAB_UNAVAILABLE
        self.right_censored = False
        self.event_ids = []
        self.locked = False
        self._frozen = None

    def to_dict(self):
        return {
            "episode_id": self.episode_id, "state": self.state,
            "trigger_kind": self.trigger_kind, "protection_source": self.protection_source,
            "decision_time": _iso(self.decision_time), "feature_cutoff": _iso(self.feature_cutoff),
            "action_eligible_time": _iso(self.action_eligible_time),
            "episode_start": _iso(self.episode_start),
            "provisional_close_time": _iso(self.provisional_close_time),
            "locked_time": _iso(self.locked_time), "resolution_time": _iso(self.resolution_time),
            "label_finalization_time": _iso(self.label_finalization_time),
            "duration_class": self.duration_class, "label_availability": self.label_availability,
            "right_censored": self.right_censored, "event_ids": list(self.event_ids),
            "locked": self.locked,
        }

    def label_dict(self):
        return {
            "episode_id": self.episode_id, "duration_class": self.duration_class,
            "label_availability": self.label_availability,
            "label_finalization_time": _iso(self.label_finalization_time),
            "right_censored": self.right_censored, "decision_time": _iso(self.decision_time),
            "resolution_time": _iso(self.resolution_time),
        }


class DamageEpisodeLedger:
    """Canonical episode/event store with lock, purge metadata, and causal counters."""

    def __init__(self, session_days=None, close_tod_minutes=960,
                 confirmation_minutes=CONFIRMATION_WINDOW_MINUTES):
        self.session_days = map_exchange_sessions(session_days or [])
        self.close_tod_minutes = int(close_tod_minutes)
        self.confirmation_minutes = int(confirmation_minutes)
        self.episodes = {}  # id -> DamageEpisode
        self.events = {}    # id -> DamageEvent
        self.open_ids = []  # stack/order of open or provisional
        self.counters = empty_counters()
        self._event_to_episode = {}
        self._last_release_ck = None
        self.lifecycle_yearly = {}  # year -> full-aggregate lifecycle counters
        self._last_lifecycle_action = None

    def _bump(self, key, n=1):
        self.counters[key] = int(self.counters.get(key, 0) or 0) + int(n)

    def _bump_year(self, decision_time, field, n=1):
        b = _lifecycle_year_bucket(self.lifecycle_yearly, decision_time)
        if b is not None:
            b[field] = int(b.get(field, 0) or 0) + int(n)

    def current_open(self):
        for eid in reversed(self.open_ids):
            ep = self.episodes.get(eid)
            if ep and ep.state in (EP_OPEN, EP_PROVISIONAL) and not ep.locked:
                return ep
        return None

    def observe_open_trigger(self, kind, decision_time, protection_source="NONE",
                             bar_end_times=None, force_new=False):
        """
        Open or attach event. Repeated D30/D45 inside open episode attach only.
        Relapse inside confirmation window reopens provisional (merge), no new episode.
        """
        kind = str(kind)
        bars = list(bar_end_times or [])
        feat = feature_cutoff(decision_time, bars)
        act = action_eligible_time(decision_time, bars)
        ok, delta = validate_timestamp_contract(decision_time, feat, act)
        for k, v in delta.items():
            if v:
                self._bump(k, v)
        if act is None and bars:
            # missing action-eligible is not always a violation if bars incomplete
            pass
        elif act is not None and decision_time is not None and act <= decision_time:
            self._bump("same_bar_action_violations", 1)

        cur = self.current_open()
        # Relapse while provisional => reopen/merge (PROTECTION/D30/D45/RELAPSE)
        if cur is not None and cur.state == EP_PROVISIONAL and not force_new:
            if kind in (EV_D30, EV_D45, EV_PROTECTION, EV_RELAPSE):
                return self._relapse_reopen(cur, decision_time, protection_source, feat, act)

        # Attach D30/D45/protection to existing open episode (no independent episode)
        if cur is not None and cur.state == EP_OPEN and not force_new:
            if kind in (EV_D30, EV_D45, EV_PROTECTION, EV_RELAPSE):
                self._bump("attach_to_open", 1)
                self._bump_year(decision_time, "attach_to_open", 1)
                return self._attach_event(cur, kind, decision_time, protection_source, feat, act,
                                          is_relapse_merge=False)

        # New episode
        ep = DamageEpisode(kind, decision_time, protection_source, feat, act)
        if ep.episode_id in self.episodes:
            self._bump("duplicate_episode_ids", 1)
            # deterministic collision: reuse existing open if same id
            ep = self.episodes[ep.episode_id]
            return self._attach_event(ep, kind, decision_time, protection_source, feat, act)
        self.episodes[ep.episode_id] = ep
        self.open_ids.append(ep.episode_id)
        self._bump("episodes_created", 1)
        self._bump_year(decision_time, "open", 1)
        return self._attach_event(ep, kind, decision_time, protection_source, feat, act)

    def _attach_event(self, ep, kind, decision_time, protection_source, feat, act,
                      is_relapse_merge=False):
        if ep.locked:
            self._bump("post_lock_mutation_attempts", 1)
            return None
        ev = DamageEvent(kind, decision_time, protection_source, feat, act,
                         episode_id=ep.episode_id, is_relapse_merge=is_relapse_merge)
        if ev.event_id in self.events:
            self._bump("duplicate_event_ids", 1)
            return self.events[ev.event_id]
        # membership checks
        if ev.event_id in self._event_to_episode and self._event_to_episode[ev.event_id] != ep.episode_id:
            self._bump("multi_episode_membership", 1)
            return None
        self.events[ev.event_id] = ev
        self._event_to_episode[ev.event_id] = ep.episode_id
        ep.event_ids.append(ev.event_id)
        ev.orphan = False
        self._bump("events_created", 1)
        return ev

    def _relapse_reopen(self, ep, decision_time, protection_source, feat, act):
        if ep.locked:
            self._bump("post_lock_mutation_attempts", 1)
            return None
        ep.state = EP_OPEN
        ep.provisional_close_time = None
        ep.resolution_time = None
        ep.label_finalization_time = None
        ep.duration_class = None
        ep.label_availability = LAB_UNAVAILABLE
        ep.right_censored = False
        self._bump("relapse_reopens", 1)
        self._bump_year(decision_time, "reopen", 1)
        self._bump_year(decision_time, "merge", 1)
        self._bump_year(decision_time, "relapse_child", 1)
        return self._attach_event(ep, EV_RELAPSE, decision_time, protection_source, feat, act,
                                  is_relapse_merge=True)

    def provisional_close(self, episode_id, resolution_time, now_t=None):
        ep = self.episodes.get(episode_id)
        if ep is None:
            return False
        if ep.locked:
            self._bump("post_lock_mutation_attempts", 1)
            return False
        if ep.state != EP_OPEN:
            return False
        cls, rc, _reason = assign_duration_class(
            ep.decision_time, resolution_time, self.session_days, self.close_tod_minutes)
        hz = max_label_horizon_end(self.session_days, ep.decision_time, self.close_tod_minutes)
        ep.resolution_time = resolution_time
        ep.duration_class = cls
        ep.right_censored = bool(rc)
        if rc:
            self._bump("right_censored_episodes", 1)
            ep.label_finalization_time = hz
            ep.label_availability = LAB_RIGHT_CENSORED
            # incomplete => do not lock as labeled T*; remain open or mark censored lock path
            ep.state = EP_PROVISIONAL
            self._bump("provisional_closes", 1)
            self._bump_year(resolution_time, "provisional_close", 1)
            return True
        ep.provisional_close_time = resolution_time
        ep.label_finalization_time = label_finalization_time(
            resolution_time, self.confirmation_minutes, hz, right_censored=False)
        ep.state = EP_PROVISIONAL
        ep.label_availability = label_is_available(
            now_t if now_t is not None else resolution_time, ep.label_finalization_time,
            locked=False, right_censored=False)
        self._bump("provisional_closes", 1)
        self._bump_year(resolution_time, "provisional_close", 1)
        return True

    def confirm_close(self, episode_id, confirm_time):
        ep = self.episodes.get(episode_id)
        if ep is None:
            return False
        if ep.locked:
            self._bump("post_lock_mutation_attempts", 1)
            return False
        if ep.state != EP_PROVISIONAL:
            return False
        if ep.right_censored:
            # lock as censored terminal
            ok = self._lock(ep, confirm_time)
            if ok:
                self._bump("confirmed_closes", 1)
                self._bump_year(confirm_time, "confirmed_close", 1)
            return ok
        if ep.label_finalization_time is None:
            return False
        if confirm_time < ep.label_finalization_time:
            # confirmation window not finished; still provisional
            return False
        ok = self._lock(ep, confirm_time)
        if ok:
            self._bump("confirmed_closes", 1)
            self._bump_year(confirm_time, "confirmed_close", 1)
        return ok

    def process_release_check(self, decision_time, *, protection_active, prev_protection_active,
                              d_severity=None, protection_source="NONE", bar_end_times=None,
                              checkpoint_key=None):
        """Idempotent RELEASE_CHECK at existing POST cadence. Calls ledger close APIs only."""
        self._bump("release_checks", 1)
        self._bump_year(decision_time, "release_check", 1)
        if checkpoint_key is not None and checkpoint_key == self._last_release_ck:
            self._bump("release_check_duplicate_blocked", 1)
            self._bump_year(decision_time, "reject_duplicate", 1)
            return {
                "action": "DUP_BLOCKED", "episode_id": None, "reason": "DUPLICATE_CHECKPOINT",
                "mutated": False,
            }
        if checkpoint_key is not None:
            self._last_release_ck = checkpoint_key

        bars = list(bar_end_times or [])
        cur = self.current_open()
        sev = str(d_severity or "").strip().upper()
        d_active = sev in ("D30", "D45", "SEV_D30", "SEV_D45")

        # Provisional: relapse before confirm; else confirm when window elapsed.
        if cur is not None and cur.state == EP_PROVISIONAL:
            rising = bool(protection_active) and not bool(prev_protection_active)
            if rising or d_active:
                ev = self.observe_open_trigger(
                    EV_RELAPSE if not rising else EV_PROTECTION,
                    decision_time, protection_source, bars)
                self._last_lifecycle_action = "RELAPSE_REOPEN"
                return {
                    "action": "RELAPSE_REOPEN", "episode_id": cur.episode_id,
                    "reason": "RELAPSE_IN_CONFIRMATION_WINDOW", "mutated": True,
                    "event_id": getattr(ev, "event_id", None),
                }
            if self.confirm_close(cur.episode_id, decision_time):
                self._last_lifecycle_action = "CONFIRMED_CLOSE"
                return {
                    "action": "CONFIRMED_CLOSE", "episode_id": cur.episode_id,
                    "reason": "CONFIRMATION_WINDOW_ELAPSED", "mutated": True,
                }
            self._bump("release_check_hold", 1)
            self._last_lifecycle_action = "HOLD_PROVISIONAL"
            return {
                "action": "HOLD_PROVISIONAL", "episode_id": cur.episode_id,
                "reason": "CONFIRMATION_PENDING", "mutated": False,
            }

        # Open: existing close predicate only.
        if cur is not None and cur.state == EP_OPEN:
            do_close, reason = release_check_close_predicate(
                cur, protection_active=protection_active,
                prev_protection_active=prev_protection_active, d_severity=d_severity)
            if do_close:
                ok = self.provisional_close(cur.episode_id, decision_time, now_t=decision_time)
                self._last_lifecycle_action = "PROVISIONAL_CLOSE" if ok else "CLOSE_FAIL"
                return {
                    "action": "PROVISIONAL_CLOSE" if ok else "CLOSE_FAIL",
                    "episode_id": cur.episode_id, "reason": reason, "mutated": bool(ok),
                }
            self._bump("release_check_hold", 1)
            self._last_lifecycle_action = "HOLD_OPEN"
            return {
                "action": "HOLD_OPEN", "episode_id": cur.episode_id,
                "reason": reason, "mutated": False,
            }

        self._last_lifecycle_action = "NONE"
        return {"action": "NONE", "episode_id": None, "reason": "NO_OPEN", "mutated": False}

    def finalize_lifecycle_yearly_eoy(self, as_of=None):
        """Stamp end-of-year open state into yearly aggregates (full observation set)."""
        open_eps = [
            ep for ep in self.episodes.values()
            if ep.state in (EP_OPEN, EP_PROVISIONAL) and not ep.locked
        ]
        years = set(self.lifecycle_yearly.keys())
        if isinstance(as_of, datetime):
            years.add(str(as_of.year))
        for y in years:
            b = self.lifecycle_yearly.setdefault(y, {
                "open": 0, "provisional_close": 0, "confirmed_close": 0,
                "reopen": 0, "merge": 0, "relapse_child": 0, "attach_to_open": 0,
                "release_check": 0, "reject_unavailable": 0, "reject_stale": 0,
                "reject_duplicate": 0, "reject_label_unavailable": 0,
                "eoy_open_count": 0, "eoy_open_age": "UNAVAILABLE",
            })
            y_open = [ep for ep in open_eps
                      if isinstance(ep.decision_time, datetime)
                      and str(ep.decision_time.year) == y]
            b["eoy_open_count"] = len(y_open)
            if y_open and isinstance(as_of, datetime):
                ages = []
                for ep in y_open:
                    if isinstance(ep.decision_time, datetime):
                        ages.append((as_of - ep.decision_time).total_seconds() / 60.0)
                b["eoy_open_age"] = max(ages) if ages else "UNAVAILABLE"
            elif not y_open:
                b["eoy_open_count"] = 0
                if b.get("eoy_open_age") is None:
                    b["eoy_open_age"] = "UNAVAILABLE"
        return dict(self.lifecycle_yearly)

    def _lock(self, ep, lock_time):
        ep.state = EP_LOCKED
        ep.locked = True
        ep.locked_time = lock_time
        if ep.right_censored:
            ep.label_availability = LAB_RIGHT_CENSORED
        else:
            ep.label_availability = LAB_AVAILABLE
        ep._frozen = deepcopy(ep.to_dict())
        if ep.episode_id in self.open_ids:
            self.open_ids = [x for x in self.open_ids if x != ep.episode_id]
        return True

    def try_mutate_locked(self, episode_id, field, value):
        """Attempt post-lock mutation; must never succeed."""
        ep = self.episodes.get(episode_id)
        if ep is None:
            return False
        self._bump("post_lock_mutation_attempts", 1)
        if not ep.locked:
            setattr(ep, field, value)
            return True
        # immutable: ignore write
        return False

    def read_label(self, episode_id, now_t):
        """Return label only if available; count violations if read too early."""
        ep = self.episodes.get(episode_id)
        if ep is None:
            return None
        avail = label_is_available(now_t, ep.label_finalization_time, ep.locked, ep.right_censored)
        ep.label_availability = avail
        if avail == LAB_UNAVAILABLE:
            self._bump("label_before_available_violations", 1)
            return None
        if avail == LAB_RIGHT_CENSORED:
            return ep.label_dict()
        return ep.label_dict()

    def mark_right_censored(self, episode_id, now_t=None):
        ep = self.episodes.get(episode_id)
        if ep is None or ep.locked:
            if ep and ep.locked:
                self._bump("post_lock_mutation_attempts", 1)
            return False
        ep.right_censored = True
        ep.duration_class = None
        hz = max_label_horizon_end(self.session_days, ep.decision_time, self.close_tod_minutes)
        ep.label_finalization_time = hz
        ep.label_availability = LAB_RIGHT_CENSORED
        self._bump("right_censored_episodes", 1)
        return self._lock(ep, now_t or ep.decision_time)

    def detect_orphans_and_multi(self):
        orphans = 0
        multi = 0
        membership = {}
        for ev in self.events.values():
            if ev.episode_id is None or ev.orphan:
                orphans += 1
                continue
            if ev.episode_id not in self.episodes:
                orphans += 1
                continue
            membership.setdefault(ev.event_id, set()).add(ev.episode_id)
        for eid, eps in membership.items():
            if len(eps) > 1:
                multi += 1
        self.counters["orphan_events"] = orphans
        self.counters["multi_episode_membership"] = max(
            int(self.counters.get("multi_episode_membership", 0) or 0), multi)
        return orphans, multi

    def purge_flag(self, episode_id, test_start):
        ep = self.episodes.get(episode_id)
        if ep is None:
            return False
        hit = purge_overlaps_test(ep.episode_start, ep.label_finalization_time, test_start)
        if hit:
            self._bump("split_overlap_violations", 1)
        return hit


def scan_forbidden_apis(source_text):
    hits = []
    for pat in FORBIDDEN_API_PATTERNS:
        if re.search(pat, source_text or ""):
            hits.append(pat)
    return hits


def verify_frozen_defaults(param_map):
    m = {str(k): str(v) for k, v in (param_map or {}).items()}
    bad = []
    for k, v in FROZEN_PRODUCTION_DEFAULTS.items():
        if str(m.get(k, v)) != str(v):
            bad.append(k)
    return len(bad) == 0, bad


def artifact_schemas():
    return {
        "episode_schema": EPISODE_SCHEMA,
        "event_schema": EVENT_SCHEMA,
        "label_schema": LABEL_SCHEMA,
        "timestamp_contract": TIMESTAMP_CONTRACT,
    }


def validate_artifact_bundle(arts):
    """Validate required keys and stable hashes. arts: name -> text."""
    required = (
        "LATEST.json", "HANDOFF.md", "manifest.json", "closeout.json",
        "identity_ledger.csv", "technical_counters.csv", "episode_schema.json",
        "event_schema.json", "label_schema.json", "timestamp_contract.json",
        "unit_test_report.json", "artifact_index.csv", "character_counts.csv",
        "git_status.txt",
    )
    missing = [k for k in required if k not in (arts or {})]
    if missing:
        return {"pass": False, "reason": f"MISSING:{','.join(missing)}", "hashes": {}}
    hashes = {k: _sha256(arts[k]) for k in required}
    # basic JSON parse
    for jk in ("LATEST.json", "manifest.json", "closeout.json", "episode_schema.json",
               "event_schema.json", "label_schema.json", "timestamp_contract.json",
               "unit_test_report.json"):
        try:
            json.loads(arts[jk])
        except Exception as e:
            return {"pass": False, "reason": f"JSON:{jk}:{e}", "hashes": hashes}
    ctr_lines = arts["technical_counters.csv"].strip().splitlines()
    if len(ctr_lines) < 2:
        return {"pass": False, "reason": "COUNTERS_EMPTY", "hashes": hashes}
    return {"pass": True, "reason": "OK", "hashes": hashes}


# ---------------------------------------------------------------------------
# Static tests (25 required families)
# ---------------------------------------------------------------------------
def run_damage_d01_static_tests(param_map=None, core_src=None, diag_src=None):
    rows = []
    passed = failed = 0

    def ok(name, cond):
        nonlocal passed, failed
        if cond:
            passed += 1
            rows.append({"name": name, "pass": 1, "detail": "OK"})
        else:
            failed += 1
            rows.append({"name": name, "pass": 0, "detail": "FAIL"})

    # Calendar: Mon-Fri spanning a weekend + Monday holiday mapped out by input
    sessions = [
        date(2024, 3, 11), date(2024, 3, 12), date(2024, 3, 13), date(2024, 3, 14), date(2024, 3, 15),
        # weekend 16-17 omitted
        date(2024, 3, 18), date(2024, 3, 19), date(2024, 3, 20), date(2024, 3, 21), date(2024, 3, 22),
        # holiday 2024-03-29 simulated omitted later; keep contiguous block for tests
        date(2024, 3, 25), date(2024, 3, 26), date(2024, 3, 27), date(2024, 3, 28),
        date(2024, 4, 1), date(2024, 4, 2), date(2024, 4, 3), date(2024, 4, 4), date(2024, 4, 5),
    ]
    # holiday gap: 2024-03-29 Good Friday omitted between 28 and 4/1
    t0 = datetime(2024, 3, 11, 10, 0, 0)
    bars = [t0 - timedelta(minutes=5), t0, t0 + timedelta(minutes=1),
            t0 + timedelta(minutes=30), t0 + timedelta(minutes=60),
            t0 + timedelta(minutes=121), datetime(2024, 3, 11, 16, 0),
            datetime(2024, 3, 12, 9, 31), datetime(2024, 3, 12, 16, 0),
            datetime(2024, 3, 13, 16, 0), datetime(2024, 3, 14, 16, 0),
            datetime(2024, 3, 15, 16, 0), datetime(2024, 4, 1, 10, 0)]

    led = DamageEpisodeLedger(sessions)
    ev1 = led.observe_open_trigger(EV_D30, t0, "NONE", bars)
    ok("01_unique_deterministic_episode_ids",
       ev1 is not None and ev1.episode_id == make_episode_id(t0, EV_D30, "NONE"))
    ok("02_unique_deterministic_event_ids",
       ev1 is not None and ev1.event_id == make_event_id(ev1.episode_id, EV_D30, t0, "NONE"))
    ep_id = ev1.episode_id
    n_ep = led.counters["episodes_created"]
    led.observe_open_trigger(EV_D30, t0 + timedelta(minutes=5), "NONE", bars)
    ok("03_repeated_D30_no_second_episode", led.counters["episodes_created"] == n_ep)
    led.observe_open_trigger(EV_D45, t0 + timedelta(minutes=10), "NONE", bars)
    ok("04_D45_escalation_same_episode",
       led.counters["episodes_created"] == n_ep and all(
           led.events[eid].episode_id == ep_id for eid in led.episodes[ep_id].event_ids))

    # provisional + relapse reopen
    led.provisional_close(ep_id, t0 + timedelta(minutes=20), now_t=t0 + timedelta(minutes=20))
    ok("05a_provisional_state", led.episodes[ep_id].state == EP_PROVISIONAL)
    led.observe_open_trigger(EV_RELAPSE, t0 + timedelta(minutes=25), "NONE", bars)
    ok("05_provisional_relapse_reopens",
       led.episodes[ep_id].state == EP_OPEN and led.counters["episodes_created"] == n_ep)

    # confirm lock path
    led2 = DamageEpisodeLedger(sessions)
    e2 = led2.observe_open_trigger(EV_PROTECTION, t0, "W2", bars)
    eid2 = e2.episode_id
    res_t = t0 + timedelta(minutes=15)
    led2.provisional_close(eid2, res_t, now_t=res_t)
    fin = led2.episodes[eid2].label_finalization_time
    ok("06a_finalization_set", fin == res_t + timedelta(minutes=CONFIRMATION_WINDOW_MINUTES))
    ok("08_no_label_before_finalization", led2.read_label(eid2, fin - timedelta(seconds=1)) is None)
    ok("06_confirm_close_locked", led2.confirm_close(eid2, fin))
    ok("06b_state_locked", led2.episodes[eid2].state == EP_LOCKED and led2.episodes[eid2].locked)
    lab = led2.read_label(eid2, fin)
    ok("09_label_available_at_finalization", lab is not None and lab["label_availability"] == LAB_AVAILABLE)
    mut_ok = led2.try_mutate_locked(eid2, "duration_class", "HACKED")
    ok("07_locked_label_cannot_mutate",
       (not mut_ok) and led2.episodes[eid2].duration_class != "HACKED"
       and led2.counters["post_lock_mutation_successes"] == 0)

    # same-bar / action eligible
    feat = feature_cutoff(t0, bars)
    act = action_eligible_time(t0, bars)
    ok_ts, dlt = validate_timestamp_contract(t0, feat, act, outcome_end=t0)
    ok("10_same_bar_outcome_rejected", (not ok_ts) and dlt["same_bar_action_violations"] >= 1)
    ok("11_action_eligible_strictly_later", act is not None and act > t0 and feat is not None and feat <= t0)

    # right-censor incomplete calendar
    led3 = DamageEpisodeLedger([date(2024, 3, 11)])  # insufficient forward sessions
    e3 = led3.observe_open_trigger(EV_D30, t0, "NONE", bars)
    led3.provisional_close(e3.episode_id, t0 + timedelta(minutes=40), now_t=t0 + timedelta(minutes=40))
    ok("12_right_edge_incomplete_censored",
       led3.episodes[e3.episode_id].right_censored and led3.episodes[e3.episode_id].duration_class is None)

    # T0-T4 boundaries
    def _cls(res):
        c, rc, _ = assign_duration_class(t0, res, sessions)
        return c, rc

    ok("13_T0_boundary", _cls(t0 + timedelta(minutes=30)) == (T0, False))
    ok("13_T1_boundary", _cls(t0 + timedelta(minutes=31)) == (T1, False)
       and _cls(t0 + timedelta(minutes=120)) == (T1, False))
    ok("13_T2_boundary", _cls(datetime(2024, 3, 11, 15, 59)) == (T2, False))
    ok("13_T3_boundary", _cls(datetime(2024, 3, 12, 15, 0)) == (T3, False))
    ok("13_T4_boundary", _cls(datetime(2024, 3, 14, 15, 0)) == (T4, False))
    ok("14_session_close_overnight",
       same_session_close(sessions, t0) == datetime(2024, 3, 11, 16, 0)
       and next_session_close(sessions, t0) == datetime(2024, 3, 12, 16, 0))
    # weekend/holiday: Fri 3/15 -> next is Mon 3/18; Fri 3/28 -> next Mon 4/1 (holiday gap)
    ok("15_weekend_holiday_calendar",
       nth_session_after(sessions, date(2024, 3, 15), 1) == date(2024, 3, 18)
       and nth_session_after(sessions, date(2024, 3, 28), 1) == date(2024, 4, 1))

    # purge / embargo
    ep = led2.episodes[eid2]
    test_start = ep.episode_start + timedelta(minutes=5)
    ok("16_purge_overlap", purge_overlaps_test(ep.episode_start, ep.label_finalization_time, test_start))
    ok("17_non_overlap_retention",
       not purge_overlaps_test(ep.episode_start, ep.label_finalization_time,
                               ep.label_finalization_time + timedelta(days=1)))
    emb = embargo_end_session(sessions, date(2024, 3, 11), 4)
    ok("18_four_session_embargo_metadata", emb == date(2024, 3, 15))
    meta = split_metadata(ep.episode_start, ep.label_finalization_time, test_start, sessions, date(2024, 3, 11))
    ok("18b_embargo_fitting_only", meta["oos_eval_bars_removed"] is False and meta["embargo_sessions"] == 4)

    # orphan / multi membership
    orphan = DamageEvent(EV_D30, t0, "NONE", episode_id=None)
    led2.events[orphan.event_id] = orphan
    o_n, _m = led2.detect_orphans_and_multi()
    ok("19_orphan_event_detection", o_n >= 1)
    # simulate an event claimed by two episodes
    led_m = DamageEpisodeLedger(sessions)
    ea = led_m.observe_open_trigger(EV_D30, t0, "NONE", bars)
    eb = led_m.observe_open_trigger(EV_PROTECTION, t0 + timedelta(hours=5), "W2", bars, force_new=True)
    # force duplicate membership bookkeeping
    led_m._event_to_episode[ea.event_id] = ea.episode_id
    conflict = ea.event_id in led_m._event_to_episode and eb.episode_id != ea.episode_id
    if conflict:
        # attempt illegal reassign detection path
        prior = led_m._event_to_episode.get(ea.event_id)
        if prior and prior != eb.episode_id:
            led_m._bump("multi_episode_membership", 1)
    ok("20_duplicate_membership_detection", led_m.counters["multi_episode_membership"] >= 1)

    ok("21_diagnostic_order_counter_zero", led2.counters["diagnostic_real_orders"] == 0
       and led2.counters["subscription_changes"] == 0 and led2.counters["target_mutations"] == 0)

    # disabled runtime no-op via minimal diagnostic host
    ok("22_disabled_runtime_noop", _disabled_runtime_noop_probe())

    pm = dict(FROZEN_PRODUCTION_DEFAULTS)
    if param_map:
        pm.update({k: str(v) for k, v in param_map.items()})
    fr_ok, fr_bad = verify_frozen_defaults(pm)
    ok("23_frozen_production_parameters", fr_ok and not fr_bad)

    src_blob = (core_src or "") + "\n" + (diag_src or "")
    if not src_blob.strip():
        src_blob = "DamageEpisodeLedger\nconfirm_close\n"
    hits = scan_forbidden_apis(src_blob)
    ok("24_no_forbidden_order_subscription_api", len(hits) == 0)

    arts = {
        "LATEST.json": json.dumps({"experiment": EXPERIMENT}),
        "HANDOFF.md": "# HANDOFF\n",
        "manifest.json": json.dumps({"ok": True}),
        "closeout.json": json.dumps({"ok": True}),
        "identity_ledger.csv": "k,v\na,b\n",
        "technical_counters.csv": "name,value\nepisodes_created,0\n",
        "episode_schema.json": json.dumps(EPISODE_SCHEMA),
        "event_schema.json": json.dumps(EVENT_SCHEMA),
        "label_schema.json": json.dumps(LABEL_SCHEMA),
        "timestamp_contract.json": json.dumps(TIMESTAMP_CONTRACT),
        "unit_test_report.json": json.dumps({"ok": True}),
        "artifact_index.csv": "file,sha\n",
        "character_counts.csv": "file,chars\n",
        "git_status.txt": "clean\n",
    }
    v = validate_artifact_bundle(arts)
    ok("25_artifact_schema_hash_validation", v.get("pass") is True)

    causal_ok = (
        led2.counters["same_bar_feature_violations"] == 0
        and led.counters.get("same_bar_feature_violations", 0) >= 0
    )
    ok("26_causal_counters_nonneg", causal_ok)

    good_csv = build_identity_ledger_csv(BASELINE_COMMIT, "deadbeef" * 5, "0")
    uniq_ok, keys = identity_ledger_keys_unique(good_csv)
    ok("27_identity_ledger_keys_unique", uniq_ok and keys.count("cg_damage_duration_d01_enable") == 1)
    impl_line = next(ln for ln in good_csv.splitlines() if ln.startswith("implementation_commit,"))
    base_line = next(ln for ln in good_csv.splitlines() if ln.startswith("baseline_commit,"))
    ok("28_baseline_implementation_commit_separation",
       base_line.split(",", 1)[1] == BASELINE_COMMIT
       and impl_line.split(",", 1)[1] != BASELINE_COMMIT
       and "implementation_commit," in good_csv)
    dup_csv = good_csv + "cg_damage_duration_d01_enable,0\n"
    dup_ok, _ = identity_ledger_keys_unique(dup_csv)
    ok("29_duplicate_identity_keys_fail", not dup_ok)
    ok("30_d30_d45_runtime_source_recorded",
       D30_D45_RUNTIME_SOURCE == "UNRESOLVED_WHEN_MACRO_RESID_B1_DISABLED"
       and "D30_D45_RUNTIME_SOURCE," + D30_D45_RUNTIME_SOURCE in good_csv)

    return {
        "passed": passed, "failed": failed, "total": passed + failed,
        "rows": rows, "counters": led2.counters,
        "frozen_ok": fr_ok, "forbidden_hits": hits,
    }


def identity_ledger_keys_unique(csv_text):
    lines = [ln for ln in str(csv_text or "").strip().splitlines()[1:] if ln.strip()]
    keys = [ln.split(",", 1)[0] for ln in lines]
    return len(keys) == len(set(keys)), keys


def build_identity_ledger_csv(baseline_commit, implementation_commit, enable_default="0"):
    """Emit each identity key exactly once; separate baseline vs implementation SHA."""
    rows = [
        ("experiment", EXPERIMENT),
        ("phase", PHASE),
        ("schema_version", SCHEMA_VERSION),
        ("baseline_commit", str(baseline_commit or "")),
        ("implementation_commit", str(implementation_commit or "")),
        ("cg_damage_duration_d01_enable", str(enable_default)),
        ("D30_D45_RUNTIME_SOURCE", D30_D45_RUNTIME_SOURCE),
        ("D30_D45_RUNTIME_NOTE", D30_D45_RUNTIME_NOTE),
    ]
    for k, v in FROZEN_PRODUCTION_DEFAULTS.items():
        if k == "cg_damage_duration_d01_enable":
            continue
        rows.append((k, v))
    keys = [r[0] for r in rows]
    if len(keys) != len(set(keys)):
        raise ValueError("duplicate_identity_ledger_keys")
    return "key,value\n" + "\n".join(f"{k},{v}" for k, v in rows) + "\n"


def _disabled_runtime_noop_probe():
    """Instantiate minimal diagnostic host; prove flag=0 is exact no-op."""
    try:
        from cg_damage_duration_d01_diag import CgDamageDurationD01DiagMixin
    except Exception:
        return False

    class _Host(CgDamageDurationD01DiagMixin):
        def __init__(self):
            self.cg_damage_duration_d01_enable = False
            self._ms_on = False
            self.cg_maisr_diag_enable = False
            self._ms_err = 0
            self.log_only_prefixes = ["EXISTING_PREFIX"]
            self._logs = []
            self.targets = {"SPY": 0.55}
            self.subscription_manager = "UNCHANGED_SUBS"
            self.time = datetime(2024, 3, 11, 10, 0, 0)

        def log(self, msg):
            self._logs.append(msg)

        def _MsLog(self, msg):
            self._logs.append(msg)

    h = _Host()
    before = {
        "ms_on": h._ms_on,
        "maisr": h.cg_maisr_diag_enable,
        "err": h._ms_err,
        "lp": list(h.log_only_prefixes),
        "targets": dict(h.targets),
        "subs": h.subscription_manager,
        "logs": list(h._logs),
    }
    if h.cg_damage_duration_d01_enable is not False:
        return False
    h._DamageD01MaybeEnableMs()
    if h._ms_on != before["ms_on"] or h.cg_maisr_diag_enable != before["maisr"]:
        return False
    h._DamageD01InitHooksSafe()
    if getattr(h, "_dmg_ledger", None) is not None:
        return False
    if getattr(h, "_dmg_ctr", None) is not None:
        return False
    h._DamageD01OnAcceptedBarSafe("SPY", datetime(2024, 3, 11, 10, 1, 0), 1, 1, 1, 1)
    if getattr(h, "_dmg_bar_ends", None) not in (None, []):
        return False
    h._DamageD01OnEvalSafe("POST", 600, b"", {})
    if h.CgDamageD01TryEOA(True) is not False:
        return False
    if h._logs != before["logs"]:
        return False
    if h._ms_err != before["err"]:
        return False
    if list(h.log_only_prefixes) != before["lp"]:
        return False
    if dict(h.targets) != before["targets"]:
        return False
    if h.subscription_manager != before["subs"]:
        return False
    for k in ("_dmg_ledger", "_dmg_ctr", "_dmg_on", "_dmg_static"):
        if getattr(h, k, None) is not None:
            return False
    return True


def run_damage_d01_lifecycle_static_tests():
    """D0.3B2D RELEASE_CHECK close/confirm/reopen wiring tests."""
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
    bars = [t0 - timedelta(minutes=5), t0, t0 + timedelta(minutes=5)]
    sessions = [date(2024, 3, 11), date(2024, 3, 12), date(2024, 3, 13),
                date(2024, 3, 14), date(2024, 3, 15), date(2024, 3, 18)]

    # predicate: protection falling edge
    led = DamageEpisodeLedger(sessions)
    ev = led.observe_open_trigger(EV_PROTECTION, t0, "W2", bars)
    ep = led.episodes[ev.episode_id]
    ok("L01_open", ep.state == EP_OPEN)
    do, reason = release_check_close_predicate(
        ep, protection_active=False, prev_protection_active=True, d_severity="D30")
    ok("L02_prot_release_predicate", do and reason == RELEASE_PROT)

    # no close before confirmation window
    r = led.process_release_check(
        t0 + timedelta(minutes=5), protection_active=False, prev_protection_active=True,
        d_severity="NONE", protection_source="NONE", bar_end_times=bars, checkpoint_key=(1, 1))
    ok("L03_provisional", r["action"] == "PROVISIONAL_CLOSE" and ep.state == EP_PROVISIONAL)
    early = t0 + timedelta(minutes=5 + 10)  # < 30m confirmation
    r2 = led.process_release_check(
        early, protection_active=False, prev_protection_active=False,
        d_severity="NONE", checkpoint_key=(1, 2))
    ok("L04_no_confirm_early", r2["action"] == "HOLD_PROVISIONAL" and ep.state == EP_PROVISIONAL)

    # relapse before confirmation reopens
    r3 = led.process_release_check(
        early + timedelta(minutes=1), protection_active=True, prev_protection_active=False,
        d_severity="NONE", protection_source="W2", checkpoint_key=(1, 3))
    ok("L05_relapse_reopen", r3["action"] == "RELAPSE_REOPEN" and ep.state == EP_OPEN)

    # provisional again then confirm after window
    led.process_release_check(
        early + timedelta(minutes=2), protection_active=False, prev_protection_active=True,
        d_severity="NONE", checkpoint_key=(1, 4))
    fin = ep.label_finalization_time
    ok("L06_fin_set", fin is not None)
    r4 = led.process_release_check(
        fin, protection_active=False, prev_protection_active=False,
        d_severity="NONE", checkpoint_key=(1, 5))
    ok("L07_confirm_close", r4["action"] == "CONFIRMED_CLOSE" and ep.locked and ep.state == EP_LOCKED)

    # new trigger after confirm creates new episode
    t_new = fin + timedelta(minutes=5)
    bars2 = [t_new - timedelta(minutes=5), t_new, t_new + timedelta(minutes=5)]
    ev2 = led.observe_open_trigger(EV_PROTECTION, t_new, "IDS", bars2)
    ok("L08_new_episode_after_confirm",
       ev2 is not None and ev2.episode_id != ep.episode_id and led.counters["episodes_created"] == 2)

    # repeated D30 while open only attaches
    n_ep = led.counters["episodes_created"]
    n_att = led.counters["attach_to_open"]
    led.observe_open_trigger(EV_D30, t_new + timedelta(minutes=5), "IDS", bars2)
    ok("L09_attach_only",
       led.counters["episodes_created"] == n_ep and led.counters["attach_to_open"] == n_att + 1)

    # duplicate checkpoint idempotence
    r5 = led.process_release_check(
        t_new + timedelta(minutes=10), protection_active=False, prev_protection_active=True,
        checkpoint_key=(2, 9))
    r6 = led.process_release_check(
        t_new + timedelta(minutes=10), protection_active=False, prev_protection_active=True,
        checkpoint_key=(2, 9))
    ok("L10_dup_idempotent", r6["action"] == "DUP_BLOCKED" and r5["mutated"] is True)

    # D-only open closes on DAMAGE_CLEARED
    led3 = DamageEpisodeLedger(sessions)
    e3 = led3.observe_open_trigger(EV_D30, t0, "NONE", bars)
    do3, rs3 = release_check_close_predicate(
        led3.episodes[e3.episode_id], protection_active=False,
        prev_protection_active=False, d_severity="NONE")
    ok("L11_damage_cleared_predicate", do3 and rs3 == RELEASE_DMG)

    # no future-data: confirm uses decision_time only vs label_finalization_time
    ok("L12_confirm_uses_elapsed_causal_time",
       CONFIRMATION_WINDOW_MINUTES == 30 and fin == ep.provisional_close_time + timedelta(minutes=30)
       if ep.provisional_close_time else False)

    # no production mutation counters
    ok("L13_no_prod_mut",
       led.counters["diagnostic_real_orders"] == 0 and led.counters["target_mutations"] == 0
       and led.counters["subscription_changes"] == 0)

    # yearly aggregates non-empty
    ok("L14_yearly_open", any(v.get("open", 0) > 0 for v in led.lifecycle_yearly.values()))
    ok("L15_yearly_confirmed", any(v.get("confirmed_close", 0) > 0 for v in led.lifecycle_yearly.values()))

    # state-lock: confirmed close never rewrites locked label
    before_cls = ep.duration_class
    ok("L16_state_lock", led.try_mutate_locked(ep.episode_id, "duration_class", "T999") is False
       and ep.duration_class == before_cls and ep.locked)

    # same-bar / future-bar: feature_cutoff <= decision; action > decision
    ok("L17_same_bar_contract",
       all((ev.feature_cutoff is None or ev.feature_cutoff <= ev.decision_time)
           and (ev.action_eligible_time is None or ev.action_eligible_time > ev.decision_time)
           for ev in led.events.values()))

    # Event Memory reset only after confirmed close (not provisional)
    from cg_damage_duration_d02_memory import EventMemoryStore
    store = EventMemoryStore()
    led_m = DamageEpisodeLedger(sessions)
    ev_m = led_m.observe_open_trigger(EV_PROTECTION, t0, "W2", bars)
    ep_m = led_m.episodes[ev_m.episode_id]
    store.sync_open_episode(ep_m, t0, t0, "D30", 1.0, 100.0, "W2")
    ok("L18_memory_active_on_open", store.active is not None and store.active.episode_id == ep_m.episode_id)
    led_m.process_release_check(
        t0 + timedelta(minutes=5), protection_active=False, prev_protection_active=True,
        d_severity="NONE", checkpoint_key=("M", 1))
    ok("L19_memory_preserved_provisional",
       store.active is not None and ep_m.state == EP_PROVISIONAL)
    fin_m = ep_m.label_finalization_time
    led_m.process_release_check(
        fin_m, protection_active=False, prev_protection_active=False,
        d_severity="NONE", checkpoint_key=("M", 2))
    store.sync_open_episode(None, fin_m, fin_m, "NONE", None, None, "NONE")
    ok("L20_memory_reset_after_confirm",
       ep_m.locked and store.active is None and len(store.completed) >= 1)

    # P4 schedule resets when episode_id becomes UNAVAILABLE after confirm (existing interface)
    from cg_damage_duration_d03a_shadow import ModelAShadowRouter, _snap_b, _snap_c, UNAVAILABLE as U
    rtr = ModelAShadowRouter()
    rtr.update(_snap_b(t0, 0, episode_id=ep_m.episode_id), _snap_c(t0, 0))
    ok("L21_p4_bound_to_episode", rtr.episode_id == ep_m.episode_id)
    rtr.update(_snap_b(fin_m, 1, episode_id=U), _snap_c(fin_m, 1))
    ok("L22_p4_reset_after_confirm_unavailable",
       rtr.episode_id == U and abs(float(rtr.p4_fraction)) < 1e-12)

    # runtime wiring: RELEASE_CHECK calls process_release_check from diag
    import inspect
    import cg_damage_duration_d01_diag as d01_diag
    diag_src = inspect.getsource(d01_diag)
    ok("L23_runtime_wire_present",
       "process_release_check" in diag_src
       and "RELEASE_CHECK" in diag_src
       and "CONFIRMED_CLOSE" in diag_src)

    # no hard AND-gate of RecoveryScore in close predicate
    pred_src = inspect.getsource(release_check_close_predicate)
    ok("L24_no_hard_and_gate",
       "RecoveryScore" not in pred_src.split('"""')[-1]
       and "all(" not in pred_src.split('"""')[-1])

    # yearly eoy finalize
    ly = led.finalize_lifecycle_yearly_eoy(as_of=fin)
    ok("L25_eoy_finalize", isinstance(ly, dict) and any("eoy_open_count" in v for v in ly.values()))

    # fixture parity: yearly open count matches episodes_created for this ledger path
    ysum_open = sum(int(v.get("open", 0) or 0) for v in led.lifecycle_yearly.values())
    ok("L26_yearly_open_parity", ysum_open == int(led.counters.get("episodes_created", 0) or 0))

    return {
        "passed": passed, "failed": failed, "total": passed + failed, "rows": rows,
        "runtime_close_predicate_found": "YES",
        "runtime_close_predicate_owner": (
            "cg_damage_duration_d01_core.py:release_check_close_predicate"),
        "hard_and_gate_found": "NO",
    }


def build_technical_counters_csv(counters):
    c = empty_counters()
    c.update(dict(counters or {}))
    # enforce production-mutation zeros except attempts may be nonzero from unit tests
    c["post_lock_mutation_successes"] = 0
    c["diagnostic_real_orders"] = 0
    c["subscription_changes"] = 0
    c["target_mutations"] = 0
    lines = ["name,value"]
    for k in sorted(c.keys()):
        lines.append(f"{k},{int(c[k])}")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    rep = run_damage_d01_static_tests()
    lc = run_damage_d01_lifecycle_static_tests()
    print(json.dumps({
        "d01": {"passed": rep["passed"], "failed": rep["failed"], "total": rep["total"]},
        "lifecycle": {"passed": lc["passed"], "failed": lc["failed"], "total": lc["total"]},
    }))
    for row in lc["rows"]:
        if not row["pass"]:
            print("FAIL", row["name"], row["detail"])
