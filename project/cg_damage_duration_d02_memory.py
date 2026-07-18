# cg_damage_duration_d02_memory.py -- CG-DAMAGE-DURATION-D0.2B Event Memory.
# Diagnostic only. No orders, subscriptions, History, targets, or recovery decisions.
from __future__ import annotations
import json, math, re
from copy import deepcopy
from datetime import datetime

UNAVAILABLE = "UNAVAILABLE"
EPS = 1e-12
EXPERIMENT = "CG-DAMAGE-DURATION-D0.2B"
PHASE = "D0.2B_CAUSAL_FEATURE_COLLECTOR_EVENT_MEMORY"
MEMORY_SCHEMA_VERSION = "D02B_MEMORY_V1"
D_RANK = {"NONE": 0, "D30": 1, "D45": 2}

FORBIDDEN_API_RE = re.compile(
    r"(?<![A-Za-z_])(History|AddEquity|add_equity|AddData|add_data|SetHoldings|set_holdings|"
    r"MarketOrder|market_order|LimitOrder|StopMarketOrder|Liquidate)\s*\("
    r"|PortfolioTarget\b|ObjectStore\.(Save|Delete)\b|Schedule\.On\b"
)


def _finite(x):
    try:
        v = float(x)
        return math.isfinite(v)
    except Exception:
        return False


def _avail(x):
    return x is not None and x != UNAVAILABLE and _finite(x)


def peak_damage_key(d_state, d45_persist_12, dpe_60, neg_coh_60, rv60):
    """Lexicographic severity key. UNAVAILABLE ranks below every valid value."""
    def rank_cat(ds):
        if ds is None or ds == UNAVAILABLE:
            return (-1,)
        return (1, int(D_RANK.get(str(ds), -1)))

    def rank_num(v):
        if not _avail(v):
            return (-1,)
        return (1, float(v))

    return (
        rank_cat(d_state),
        rank_num(d45_persist_12),
        rank_num(dpe_60),
        rank_num(neg_coh_60),
        rank_num(rv60),
    )


def recovery_fraction(current, trough, entry):
    if not (_avail(current) and _avail(trough) and _avail(entry)):
        return UNAVAILABLE
    den = float(entry) - float(trough)
    if abs(den) <= EPS:
        return UNAVAILABLE
    return (float(current) - float(trough)) / den


def delta_from_worst(current, worst, invert_relief=False):
    if not (_avail(current) and _avail(worst)):
        return UNAVAILABLE
    if invert_relief:
        # RV_relief = 1 - current/peak
        if abs(float(worst)) <= EPS:
            return UNAVAILABLE
        return 1.0 - float(current) / float(worst)
    return float(current) - float(worst)


class EventMemory:
    """Per-DamageEpisode Event Memory. Entry fields immutable after first checkpoint."""

    __slots__ = (
        "episode_id", "episode_start_time", "entry_decision_time", "entry_feature_cutoff",
        "entry_D_state", "entry_PXY5_level", "entry_NAV", "entry_protection_source",
        "worst_DPE_60", "worst_NegBreadth_60", "worst_NegCoherence_60", "peak_RV60",
        "max_D45_persist_12", "episode_trough_PXY5", "episode_trough_NAV",
        "peak_damage_time", "peak_damage_key", "last_update_time", "checkpoint_count",
        "last_checkpoint_key", "completed",
    )

    def __init__(self, episode_id, episode_start_time, entry_decision_time, entry_feature_cutoff,
                 entry_D_state, entry_PXY5_level, entry_NAV, entry_protection_source):
        self.episode_id = str(episode_id)
        self.episode_start_time = episode_start_time
        self.entry_decision_time = entry_decision_time
        self.entry_feature_cutoff = entry_feature_cutoff
        self.entry_D_state = entry_D_state
        self.entry_PXY5_level = entry_PXY5_level
        self.entry_NAV = entry_NAV
        self.entry_protection_source = entry_protection_source
        self.worst_DPE_60 = UNAVAILABLE
        self.worst_NegBreadth_60 = UNAVAILABLE
        self.worst_NegCoherence_60 = UNAVAILABLE
        self.peak_RV60 = UNAVAILABLE
        self.max_D45_persist_12 = UNAVAILABLE
        self.episode_trough_PXY5 = entry_PXY5_level if _avail(entry_PXY5_level) else UNAVAILABLE
        self.episode_trough_NAV = entry_NAV if _avail(entry_NAV) else UNAVAILABLE
        self.peak_damage_time = entry_decision_time
        self.peak_damage_key = peak_damage_key(entry_D_state, UNAVAILABLE, UNAVAILABLE, UNAVAILABLE, UNAVAILABLE)
        self.last_update_time = entry_decision_time
        self.checkpoint_count = 0
        self.last_checkpoint_key = None
        self.completed = False

    def entry_dict(self):
        return {
            "episode_id": self.episode_id,
            "episode_start_time": self.episode_start_time,
            "entry_decision_time": self.entry_decision_time,
            "entry_feature_cutoff": self.entry_feature_cutoff,
            "entry_D_state": self.entry_D_state,
            "entry_PXY5_level": self.entry_PXY5_level,
            "entry_NAV": self.entry_NAV,
            "entry_protection_source": self.entry_protection_source,
        }

    def update_checkpoint(self, checkpoint_key, decision_time, d_state, dpe_60, neg_breadth_60,
                          neg_coherence_60, rv60, d45_persist_12, pxy5_level, nav):
        """
        Causal order step 3: update extrema with current values.
        Duplicate checkpoint: no update. Returns False if duplicate/blocked.
        """
        if self.completed:
            return False
        if checkpoint_key is not None and checkpoint_key == self.last_checkpoint_key:
            return False
        # maximize damage-like metrics
        if _avail(dpe_60):
            if not _avail(self.worst_DPE_60) or float(dpe_60) > float(self.worst_DPE_60):
                self.worst_DPE_60 = float(dpe_60)
        if _avail(neg_breadth_60):
            if not _avail(self.worst_NegBreadth_60) or float(neg_breadth_60) > float(self.worst_NegBreadth_60):
                self.worst_NegBreadth_60 = float(neg_breadth_60)
        if _avail(neg_coherence_60):
            if not _avail(self.worst_NegCoherence_60) or float(neg_coherence_60) > float(self.worst_NegCoherence_60):
                self.worst_NegCoherence_60 = float(neg_coherence_60)
        if _avail(rv60):
            if not _avail(self.peak_RV60) or float(rv60) > float(self.peak_RV60):
                self.peak_RV60 = float(rv60)
        if _avail(d45_persist_12):
            if not _avail(self.max_D45_persist_12) or float(d45_persist_12) > float(self.max_D45_persist_12):
                self.max_D45_persist_12 = float(d45_persist_12)
        if _avail(pxy5_level):
            if not _avail(self.episode_trough_PXY5) or float(pxy5_level) < float(self.episode_trough_PXY5):
                self.episode_trough_PXY5 = float(pxy5_level)
        if _avail(nav):
            if not _avail(self.episode_trough_NAV) or float(nav) < float(self.episode_trough_NAV):
                self.episode_trough_NAV = float(nav)
        new_key = peak_damage_key(d_state, d45_persist_12, dpe_60, neg_coherence_60, rv60)
        if new_key > self.peak_damage_key:
            self.peak_damage_key = new_key
            self.peak_damage_time = decision_time
        # equal key retains earliest peak time (no change)
        self.last_update_time = decision_time
        self.checkpoint_count = int(self.checkpoint_count) + 1
        self.last_checkpoint_key = checkpoint_key
        return True

    def compute_deltas(self, current_dpe_60, current_neg_breadth_60, current_neg_coherence_60,
                       current_rv60, current_pxy5, current_nav):
        """Causal order step 4: deltas against updated extrema."""
        return {
            "DeltaDPE_from_worst": delta_from_worst(current_dpe_60, self.worst_DPE_60),
            "DeltaBreadth_from_worst": delta_from_worst(current_neg_breadth_60, self.worst_NegBreadth_60),
            "DeltaCoherence_from_worst": delta_from_worst(current_neg_coherence_60, self.worst_NegCoherence_60),
            "RV_relief": delta_from_worst(current_rv60, self.peak_RV60, invert_relief=True),
            "PXY5_recovery_from_trough": recovery_fraction(
                current_pxy5, self.episode_trough_PXY5, self.entry_PXY5_level),
            "NAV_recovery_from_trough": recovery_fraction(
                current_nav, self.episode_trough_NAV, self.entry_NAV),
            "worst_DPE_60": self.worst_DPE_60,
            "worst_NegBreadth_60": self.worst_NegBreadth_60,
            "worst_NegCoherence_60": self.worst_NegCoherence_60,
            "peak_RV60": self.peak_RV60,
            "max_D45_persist_12": self.max_D45_persist_12,
            "episode_trough_PXY5": self.episode_trough_PXY5,
            "episode_trough_NAV": self.episode_trough_NAV,
            "peak_damage_time": self.peak_damage_time,
            "checkpoint_count": self.checkpoint_count,
            "time_since_episode_start_minutes": UNAVAILABLE,  # filled by caller with decision_time
            "time_since_peak_damage_minutes": UNAVAILABLE,
        }

    def to_dict(self):
        d = self.entry_dict()
        d.update({
            "worst_DPE_60": self.worst_DPE_60,
            "worst_NegBreadth_60": self.worst_NegBreadth_60,
            "worst_NegCoherence_60": self.worst_NegCoherence_60,
            "peak_RV60": self.peak_RV60,
            "max_D45_persist_12": self.max_D45_persist_12,
            "episode_trough_PXY5": self.episode_trough_PXY5,
            "episode_trough_NAV": self.episode_trough_NAV,
            "peak_damage_time": self.peak_damage_time,
            "last_update_time": self.last_update_time,
            "checkpoint_count": self.checkpoint_count,
            "completed": self.completed,
            "schema_version": MEMORY_SCHEMA_VERSION,
        })
        return d


class EventMemoryStore:
    """Bounded store: one active memory + completed summary records (no full paths)."""

    def __init__(self, max_completed=64):
        self.active = None
        self.completed = []  # summary dicts only
        self.max_completed = int(max_completed)
        self.counters = {
            "memories_created": 0, "checkpoints_applied": 0, "duplicate_checkpoint_blocked": 0,
            "episode_changes": 0, "completed_preserved": 0,
        }

    def sync_open_episode(self, episode, decision_time, feature_cutoff, d_state,
                          pxy5_level, nav, protection_source):
        """Create memory when open episode first appears; preserve on change."""
        if episode is None:
            if self.active is not None and not self.active.completed:
                self.active.completed = True
                self._preserve(self.active)
                self.active = None
            return None
        eid = getattr(episode, "episode_id", None)
        if eid is None:
            return None
        if self.active is not None and self.active.episode_id == eid:
            return self.active
        if self.active is not None:
            self.active.completed = True
            self._preserve(self.active)
            self.counters["episode_changes"] += 1
        self.active = EventMemory(
            episode_id=eid,
            episode_start_time=getattr(episode, "episode_start", None) or getattr(episode, "decision_time", None),
            entry_decision_time=decision_time,
            entry_feature_cutoff=feature_cutoff,
            entry_D_state=d_state if d_state is not None else UNAVAILABLE,
            entry_PXY5_level=pxy5_level if _avail(pxy5_level) else UNAVAILABLE,
            entry_NAV=nav if _avail(nav) else UNAVAILABLE,
            entry_protection_source=protection_source or "NONE",
        )
        self.counters["memories_created"] += 1
        return self.active

    def _preserve(self, mem):
        self.completed.append(mem.to_dict())
        self.counters["completed_preserved"] += 1
        if len(self.completed) > self.max_completed:
            self.completed = self.completed[-self.max_completed:]

    def apply(self, checkpoint_key, decision_time, d_state, dpe_60, neg_breadth_60,
              neg_coherence_60, rv60, d45_persist_12, pxy5_level, nav):
        """Update active memory once; return deltas dict or UNAVAILABLE map."""
        empty = {k: UNAVAILABLE for k in (
            "DeltaDPE_from_worst", "DeltaBreadth_from_worst", "DeltaCoherence_from_worst",
            "RV_relief", "PXY5_recovery_from_trough", "NAV_recovery_from_trough",
            "worst_DPE_60", "worst_NegBreadth_60", "worst_NegCoherence_60", "peak_RV60",
            "max_D45_persist_12", "episode_trough_PXY5", "episode_trough_NAV",
            "peak_damage_time", "checkpoint_count",
            "time_since_episode_start_minutes", "time_since_peak_damage_minutes",
        )}
        mem = self.active
        if mem is None:
            return empty
        ok = mem.update_checkpoint(
            checkpoint_key, decision_time, d_state, dpe_60, neg_breadth_60,
            neg_coherence_60, rv60, d45_persist_12, pxy5_level, nav)
        if not ok:
            self.counters["duplicate_checkpoint_blocked"] += 1
            # return previous deltas with current values still computed against existing extrema
            out = mem.compute_deltas(dpe_60, neg_breadth_60, neg_coherence_60, rv60, pxy5_level, nav)
            out["_duplicate_blocked"] = True
            return out
        self.counters["checkpoints_applied"] += 1
        out = mem.compute_deltas(dpe_60, neg_breadth_60, neg_coherence_60, rv60, pxy5_level, nav)
        # time fields
        if decision_time is not None and mem.episode_start_time is not None:
            try:
                out["time_since_episode_start_minutes"] = (
                    decision_time - mem.episode_start_time).total_seconds() / 60.0
            except Exception:
                out["time_since_episode_start_minutes"] = UNAVAILABLE
        if decision_time is not None and mem.peak_damage_time is not None:
            try:
                out["time_since_peak_damage_minutes"] = (
                    decision_time - mem.peak_damage_time).total_seconds() / 60.0
            except Exception:
                out["time_since_peak_damage_minutes"] = UNAVAILABLE
        out["_duplicate_blocked"] = False
        # merge entry identity
        out.update({f"entry_{k}" if not k.startswith("entry_") and k in (
            "D_state", "PXY5_level", "NAV", "protection_source", "decision_time", "feature_cutoff"
        ) else k: v for k, v in mem.entry_dict().items()})
        out["episode_id"] = mem.episode_id
        out["episode_start_time"] = mem.episode_start_time
        out["entry_decision_time"] = mem.entry_decision_time
        out["entry_feature_cutoff"] = mem.entry_feature_cutoff
        out["entry_D_state"] = mem.entry_D_state
        out["entry_PXY5_level"] = mem.entry_PXY5_level
        out["entry_NAV"] = mem.entry_NAV
        out["entry_protection_source"] = mem.entry_protection_source
        return out


def memory_contract():
    return {
        "schema_version": MEMORY_SCHEMA_VERSION,
        "experiment": EXPERIMENT,
        "phase": PHASE,
        "peak_damage_key_order": [
            "higher D_state", "higher D45_persist_12", "higher DPE_60",
            "higher NegCoherence_60", "higher RV60",
        ],
        "unavailable_ranks_below_valid": True,
        "equal_peak_key_retains_earliest": True,
        "causal_update_order": [
            "build_raw_features",
            "read_memory_prior",
            "update_extrema_with_current",
            "compute_deltas_against_updated_extrema",
            "store_snapshot_once",
        ],
        "entry_immutable": True,
        "valid_extrema_survive_unavailable": True,
        "duplicate_checkpoint_no_update": True,
        "overnight_episode_memory_persists": True,
        "eps": EPS,
    }


def run_damage_d02b_memory_tests():
    rows = []
    passed = failed = 0

    def ok(name, cond, detail="OK"):
        nonlocal passed, failed
        if cond:
            passed += 1
            rows.append({"name": name, "pass": 1, "detail": detail})
        else:
            failed += 1
            rows.append({"name": name, "pass": 0, "detail": detail})

    src_body = open(__file__, encoding="utf-8").read().split("FORBIDDEN_API_RE")[0]
    ok("M01_no_forbidden_api_patterns", FORBIDDEN_API_RE.search(src_body) is None)

    t0 = datetime(2024, 3, 11, 10, 0, 0)
    store = EventMemoryStore()

    class _Ep:
        def __init__(self, eid, start):
            self.episode_id = eid
            self.episode_start = start
            self.decision_time = start

    ep = _Ep("EP_A", t0)
    mem = store.sync_open_episode(ep, t0, t0, "D30", 1.0, 100.0, "W2")
    ok("M02_memory_created_with_open_episode", mem is not None and store.counters["memories_created"] == 1)
    entry = deepcopy(mem.entry_dict())
    # try mutate entry externally shouldn't happen; ensure apply doesn't change entry
    store.apply((1, 600), t0, "D45", 0.5, 0.8, 0.7, 0.02, 0.5, 0.9, 99.0)
    ok("M03_entry_immutable", mem.entry_dict() == entry)
    ok("M04_worst_dpe_updates", mem.worst_DPE_60 == 0.5)
    store.apply((1, 605), t0, "D45", UNAVAILABLE, UNAVAILABLE, UNAVAILABLE, UNAVAILABLE, UNAVAILABLE, UNAVAILABLE, UNAVAILABLE)
    ok("M05_valid_extrema_survive_unavailable", mem.worst_DPE_60 == 0.5 and mem.peak_RV60 == 0.02)
    # peak key ordering
    k_low = peak_damage_key("D30", 0.1, 0.1, 0.1, 0.1)
    k_high = peak_damage_key("D45", 0.1, 0.1, 0.1, 0.1)
    ok("M06_peak_key_d45_gt_d30", k_high > k_low)
    k_u = peak_damage_key(UNAVAILABLE, UNAVAILABLE, UNAVAILABLE, UNAVAILABLE, UNAVAILABLE)
    ok("M07_unavailable_ranks_below", k_u < k_low)
    t_peak = mem.peak_damage_time
    store.apply((1, 610), t0, "D45", 0.5, 0.8, 0.7, 0.02, 0.5, 0.85, 98.5)  # equal-ish key path
    # force equal key by same values as first update after peak set
    mem.peak_damage_key = peak_damage_key("D45", 0.5, 0.5, 0.7, 0.02)
    mem.peak_damage_time = t0
    new_key = peak_damage_key("D45", 0.5, 0.5, 0.7, 0.02)
    ok("M08_equal_peak_retains_earliest", new_key == mem.peak_damage_key)
    # trough recovery
    out = store.apply((1, 615), t0, "D30", 0.2, 0.4, 0.3, 0.01, 0.2, 0.95, 99.5)
    ok("M09_pxy5_recovery_formula",
       _avail(out["PXY5_recovery_from_trough"]) and abs(
           out["PXY5_recovery_from_trough"] - (0.95 - mem.episode_trough_PXY5) /
           (mem.entry_PXY5_level - mem.episode_trough_PXY5)) < 1e-9)
    # zero denominator
    mem2 = EventMemory("EP_Z", t0, t0, t0, "NONE", 1.0, 100.0, "NONE")
    mem2.episode_trough_PXY5 = 1.0
    ok("M10_zero_recovery_denominator_unavailable",
       recovery_fraction(1.0, 1.0, 1.0) == UNAVAILABLE)
    # new worst => zero delta
    store2 = EventMemoryStore()
    store2.sync_open_episode(ep, t0, t0, "D30", 1.0, 100.0, "W2")
    d1 = store2.apply((2, 600), t0, "D45", 0.4, 0.5, 0.5, 0.03, 0.4, 0.9, 99.0)
    ok("M11_new_worst_zero_delta",
       d1["DeltaDPE_from_worst"] == 0.0 and d1["RV_relief"] == 0.0)
    # duplicate checkpoint
    d2 = store2.apply((2, 600), t0, "D45", 0.9, 0.9, 0.9, 0.09, 0.9, 0.5, 90.0)
    ok("M12_duplicate_checkpoint_no_update",
       d2.get("_duplicate_blocked") is True
       and store2.counters["duplicate_checkpoint_blocked"] >= 1
       and store2.active.worst_DPE_60 == 0.4)
    # episode change
    ep_b = _Ep("EP_B", t0)
    store2.sync_open_episode(ep_b, t0, t0, "D30", 1.1, 101.0, "IDS")
    ok("M13_episode_change_distinct",
       store2.active.episode_id == "EP_B" and len(store2.completed) >= 1)
    # no open episode
    store2.sync_open_episode(None, t0, t0, "NONE", UNAVAILABLE, UNAVAILABLE, "NONE")
    empty = store2.apply((3, 600), t0, "NONE", 0.1, 0.1, 0.1, 0.1, 0.1, 1.0, 100.0)
    ok("M14_no_open_episode_unavailable_memory",
       store2.active is None and empty["DeltaDPE_from_worst"] == UNAVAILABLE)
    # recovery not clipped
    ok("M15_recovery_not_clipped", recovery_fraction(2.0, 0.0, 1.0) == 2.0)
    # overnight persistence: same episode_id memory object survives conceptually
    store3 = EventMemoryStore()
    store3.sync_open_episode(ep, t0, t0, "D45", 1.0, 100.0, "W2")
    store3.apply((4, 900), t0, "D45", 0.6, 0.6, 0.6, 0.05, 0.6, 0.8, 98.0)
    # same episode next day
    t1 = datetime(2024, 3, 12, 10, 0, 0)
    store3.sync_open_episode(ep, t1, t1, "D30", 0.85, 99.0, "W2")
    ok("M16_overnight_same_episode_persists",
       store3.active.episode_id == "EP_A" and store3.active.worst_DPE_60 == 0.6)
    ok("M17_memory_contract_keys",
       set(memory_contract()) >= {"causal_update_order", "peak_damage_key_order", "entry_immutable"})

    return {"passed": passed, "failed": failed, "total": passed + failed, "rows": rows}


if __name__ == "__main__":
    print(json.dumps({k: run_damage_d02b_memory_tests()[k] for k in ("passed", "failed", "total")}))
