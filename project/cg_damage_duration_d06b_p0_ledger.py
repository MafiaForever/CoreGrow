# cg_damage_duration_d06b_p0_ledger.py -- D0.6B P0 event-time ledger (diagnostic only).
# Passive capture of intended equity-gross snapshots. Never mutates targets/orders.
from __future__ import annotations
from copy import deepcopy
from datetime import datetime

from cg_damage_duration_d03a_core import UNAVAILABLE, EPS, _avail, _f

EXPERIMENT = "CG-DAMAGE-DURATION-D0.6B"
PHASE = "D0.6B_P0_EVENT_TIME_INSTRUMENTATION_AND_HISTORICAL_REPLAY"
P0_SOURCE_NAME = "D06B_EVENT_TIME_EQUITY_GROSS_LEDGER"
P0_EPS = 1e-9
MAX_LEDGER_ROWS = 4096
MAX_CHECKPOINT_PER_EP = 512


def clamp01(x):
    return max(0.0, min(1.0, float(x)))


def p0_restore_fraction(current_gross, after_gross, withheld):
    """Causal P0 formula. Returns (fraction|UNAVAILABLE, status)."""
    if not _avail(withheld) or float(withheld) <= P0_EPS:
        return UNAVAILABLE, "NOT_APPLICABLE"
    if not _avail(current_gross) or not _avail(after_gross):
        return UNAVAILABLE, "MISSING_CURRENT_OR_AFTER"
    num = float(current_gross) - float(after_gross)
    return clamp01(num / float(withheld)), "OK"


class P0EventLedger:
    """Bounded per-episode P0 snapshots. Entry snapshots never overwritten."""

    def __init__(self):
        self.enabled = False
        self.unbound = None  # pending protection apply awaiting episode bind
        self.rows = {}  # episode_id -> row
        self.seq = 0
        self.last_intended_targets = None
        self.last_intended_eq_gross = UNAVAILABLE
        self.last_intended_ts = None
        self.counters = {
            "protection_observes": 0, "binds": 0, "checkpoints": 0,
            "not_applicable": 0, "missing_current_source": 0,
            "repeated_protection_ignored": 0, "unbound_dropped": 0,
            "diagnostic_real_orders": 0, "subscription_changes": 0,
            "target_mutations": 0, "order_mutations": 0,
            "production_gross_mutations": 0,
        }

    def set_enabled(self, on):
        self.enabled = bool(on)

    def observe_protection_apply(self, decision_time, gross_pre, gross_after,
                                 w2_active, source="CgDefensiveTradeApply"):
        """Call from W2 path AFTER eq_b known and AFTER scale (eq_a known).
        Does not mutate targets. Stores unbound entry candidate for later bind.
        """
        if not self.enabled:
            return
        self.counters["protection_observes"] += 1
        if not isinstance(decision_time, datetime):
            return
        # First unbound snapshot wins until bind; never overwrite entry candidate.
        if self.unbound is not None:
            self.counters["repeated_protection_ignored"] += 1
            return
        try:
            pre = float(gross_pre)
            after = float(gross_after)
        except Exception:
            return
        withheld = max(0.0, pre - after)
        # Skip inert inactive observes; wait for real protection scale or N/A bind.
        if (not bool(w2_active)) and withheld <= P0_EPS:
            return
        self.seq += 1
        self.unbound = {
            "decision_time": decision_time,
            "gross_pre_protection": pre,
            "gross_after_initial_protection": after,
            "withheld_gross": withheld,
            "w2_active": bool(w2_active),
            "source_pre": source + ":eq_b",
            "source_after": source + ":eq_a",
            "capture_sequence": self.seq,
            "status": "NOT_APPLICABLE" if withheld <= P0_EPS else "ELIGIBLE",
        }

    def observe_intended_targets(self, decision_time, targets, eq_gross,
                                 source="CgRegimeRebalTimeTradeCapture"):
        """Passive copy of intended target equity gross (not holdings)."""
        if not self.enabled:
            return
        if isinstance(targets, dict):
            # store shallow float copy only (bounded)
            snap = {}
            for k, v in list(targets.items())[:256]:
                try:
                    snap[str(k)] = float(v)
                except Exception:
                    continue
            self.last_intended_targets = snap
        if _avail(eq_gross):
            self.last_intended_eq_gross = float(eq_gross)
            self.last_intended_ts = decision_time if isinstance(decision_time, datetime) else None
            self._source_current = source

    def bind_episode(self, episode_id, decision_time, protection_source=None):
        """Bind unbound same-day protection snapshot to immutable episode_id.
        Repeated bind for same episode does not overwrite entry snapshots.
        """
        if not self.enabled:
            return False
        eid = str(episode_id or "")
        if eid in ("", "None", "UNAVAILABLE"):
            return False
        if eid in self.rows:
            # already bound — ignore repeated protection overwrite
            self.counters["repeated_protection_ignored"] += 1
            return False
        ub = self.unbound
        if ub is None:
            return False
        # same calendar day causal link
        if isinstance(decision_time, datetime) and isinstance(ub.get("decision_time"), datetime):
            if decision_time.date() != ub["decision_time"].date():
                self.counters["unbound_dropped"] += 1
                self.unbound = None
                return False
        # Prefer bind when W2 was the protection; still allow if withheld eligible
        row = {
            "episode_id": eid,
            "entry_decision_time": ub["decision_time"],
            "bind_decision_time": decision_time,
            "protection_source": protection_source,
            "gross_pre_protection": ub["gross_pre_protection"],
            "gross_after_initial_protection": ub["gross_after_initial_protection"],
            "withheld_gross": ub["withheld_gross"],
            "source_pre": ub["source_pre"],
            "source_after": ub["source_after"],
            "capture_sequence": ub["capture_sequence"],
            "entry_status": ub["status"],
            "last_current_gross": UNAVAILABLE,
            "last_fraction": UNAVAILABLE,
            "last_fraction_status": ub["status"],
            "last_checkpoint_time": None,
            "checkpoint_count": 0,
            "checkpoints": [],
        }
        if ub["status"] == "NOT_APPLICABLE":
            self.counters["not_applicable"] += 1
        self.rows[eid] = row
        self.unbound = None
        self.counters["binds"] += 1
        # bound size
        if len(self.rows) > MAX_LEDGER_ROWS:
            # drop oldest by entry time
            keys = sorted(
                self.rows.keys(),
                key=lambda k: self.rows[k].get("entry_decision_time") or datetime.min,
            )
            for k in keys[: len(self.rows) - MAX_LEDGER_ROWS]:
                self.rows.pop(k, None)
        return True

    def observe_checkpoint(self, episode_id, decision_time, current_eq_gross=None):
        """Update P0 fraction from intended equity gross at DecisionTime."""
        if not self.enabled:
            return UNAVAILABLE
        eid = str(episode_id or "")
        row = self.rows.get(eid)
        if row is None:
            return UNAVAILABLE
        self.counters["checkpoints"] += 1
        # Resolve current intended gross: explicit arg, else last intended, else missing
        cur = current_eq_gross
        if not _avail(cur):
            cur = self.last_intended_eq_gross
        if not _avail(cur):
            self.counters["missing_current_source"] += 1
            # preserve last valid causal P0 state
            return row.get("last_fraction", UNAVAILABLE)

        frac, status = p0_restore_fraction(
            cur, row["gross_after_initial_protection"], row["withheld_gross"])
        row["last_current_gross"] = float(cur)
        row["last_fraction"] = frac
        row["last_fraction_status"] = status
        row["last_checkpoint_time"] = decision_time
        row["checkpoint_count"] = int(row.get("checkpoint_count") or 0) + 1
        if len(row["checkpoints"]) < MAX_CHECKPOINT_PER_EP:
            row["checkpoints"].append({
                "decision_time": decision_time,
                "current_gross": float(cur),
                "fraction": frac,
                "status": status,
            })
        return frac

    def fraction_for(self, episode_id):
        row = self.rows.get(str(episode_id or ""))
        if row is None:
            return UNAVAILABLE
        if row.get("entry_status") == "NOT_APPLICABLE":
            return UNAVAILABLE
        return row.get("last_fraction", UNAVAILABLE)

    def is_not_applicable(self, episode_id):
        row = self.rows.get(str(episode_id or ""))
        return bool(row and row.get("entry_status") == "NOT_APPLICABLE")

    def is_eligible(self, episode_id):
        row = self.rows.get(str(episode_id or ""))
        return bool(row and row.get("entry_status") == "ELIGIBLE")

    def eligible_episode_ids(self):
        return [eid for eid, r in self.rows.items() if r.get("entry_status") == "ELIGIBLE"]

    def snapshot(self):
        eligible = sum(1 for r in self.rows.values() if r.get("entry_status") == "ELIGIBLE")
        na = sum(1 for r in self.rows.values() if r.get("entry_status") == "NOT_APPLICABLE")
        with_entry = len(self.rows)
        return {
            "experiment": EXPERIMENT,
            "phase": PHASE,
            "source_name": P0_SOURCE_NAME,
            "enabled": self.enabled,
            "bound_episodes": with_entry,
            "eligible_episodes": eligible,
            "not_applicable_episodes": na,
            "capture_coverage": (
                1.0 if with_entry == 0 else float(eligible + na) / float(with_entry)
            ),
            "counters": dict(self.counters),
            "rows_sample": [
                {k: v for k, v in r.items() if k != "checkpoints"}
                for r in list(self.rows.values())[:32]
            ],
        }

    def export_event_rows(self):
        out = []
        for eid, r in self.rows.items():
            out.append({
                "episode_id": eid,
                "entry_decision_time": r.get("entry_decision_time"),
                "bind_decision_time": r.get("bind_decision_time"),
                "protection_source": r.get("protection_source"),
                "gross_pre_protection": r.get("gross_pre_protection"),
                "gross_after_initial_protection": r.get("gross_after_initial_protection"),
                "withheld_gross": r.get("withheld_gross"),
                "entry_status": r.get("entry_status"),
                "last_current_gross": r.get("last_current_gross"),
                "last_fraction": r.get("last_fraction"),
                "last_fraction_status": r.get("last_fraction_status"),
                "checkpoint_count": r.get("checkpoint_count"),
                "source_pre": r.get("source_pre"),
                "source_after": r.get("source_after"),
                "capture_sequence": r.get("capture_sequence"),
            })
        return out


def run_d06b_ledger_static_tests():
    from datetime import timedelta

    rows, passed, failed = [], 0, 0

    def ok(n, c, detail=""):
        nonlocal passed, failed
        if c:
            passed += 1
            rows.append({"name": n, "pass": True, "detail": detail})
        else:
            failed += 1
            rows.append({"name": n, "pass": False, "detail": str(detail)})

    # Formula
    f, st = p0_restore_fraction(0.8, 0.6, 0.4)
    ok("L01_formula_mid", st == "OK" and abs(f - 0.5) < 1e-12)
    f2, st2 = p0_restore_fraction(1.0, 0.6, 0.4)
    ok("L02_clamp_high", st2 == "OK" and abs(f2 - 1.0) < 1e-12)
    f3, st3 = p0_restore_fraction(0.5, 0.6, 0.4)
    ok("L03_clamp_low", st3 == "OK" and abs(f3 - 0.0) < 1e-12)
    f4, st4 = p0_restore_fraction(0.8, 0.6, 0.0)
    ok("L04_zero_withheld", st4 == "NOT_APPLICABLE" and f4 == UNAVAILABLE)

    led = P0EventLedger()
    led.set_enabled(True)
    t0 = datetime(2024, 3, 11, 9, 45, 0)
    # Entry: pre=1.0 after=0.8 withheld=0.2
    led.observe_protection_apply(t0, 1.0, 0.8, True)
    ok("L05_unbound_set", led.unbound is not None and abs(led.unbound["withheld_gross"] - 0.2) < 1e-12)
    t1 = t0 + timedelta(minutes=5)
    ok("L06_bind", led.bind_episode("EP1", t1, "W2"))
    ok("L07_no_overwrite", not led.bind_episode("EP1", t1, "W2"))
    ok("L08_repeated_counter", led.counters["repeated_protection_ignored"] >= 1)

    # Intended current at checkpoint
    led.observe_intended_targets(t1, {"SPY": 0.9, "BIL": 0.1}, 0.9)
    frac = led.observe_checkpoint("EP1", t1, 0.9)
    # (0.9-0.8)/0.2 = 0.5
    ok("L09_checkpoint_frac", _avail(frac) and abs(float(frac) - 0.5) < 1e-12)

    # Missing current preserves last
    frac2 = led.observe_checkpoint("EP1", t1 + timedelta(minutes=5), None)
    # last_intended still 0.9 so still works; clear it
    led.last_intended_eq_gross = UNAVAILABLE
    frac3 = led.observe_checkpoint("EP1", t1 + timedelta(minutes=10), None)
    ok("L10_missing_preserves", abs(float(frac3) - 0.5) < 1e-12)
    ok("L11_missing_counter", led.counters["missing_current_source"] >= 1)

    # N/A episode (active but zero withheld)
    led2 = P0EventLedger()
    led2.set_enabled(True)
    led2.observe_protection_apply(t0, 0.8, 0.8, True)
    led2.bind_episode("EP2", t1, "IDS")
    ok("L12_na", led2.is_not_applicable("EP2"))
    ok("L13_zero_mut", led.counters["target_mutations"] == 0
       and led.counters["order_mutations"] == 0
       and led.counters["production_gross_mutations"] == 0)

    # Disabled no-op
    led3 = P0EventLedger()
    led3.set_enabled(False)
    led3.observe_protection_apply(t0, 1.0, 0.8, True)
    ok("L14_disabled_noop", led3.unbound is None and led3.counters["protection_observes"] == 0)

    # Production no-op: observe helpers never mutate target dict
    targets = {"SPY": 0.7, "QQQ": 0.3}
    before = dict(targets)
    led4 = P0EventLedger()
    led4.set_enabled(True)
    led4.observe_protection_apply(t0, 1.0, 0.8, True)
    led4.observe_intended_targets(t0, targets, 1.0)
    ok("L15_targets_unchanged", targets == before)
    ok("L16_entry_before_fill", led4.unbound["source_pre"].endswith(":eq_b")
       and led4.unbound["source_after"].endswith(":eq_a"))

    return {"passed": passed, "failed": failed, "total": passed + failed, "rows": rows}


if __name__ == "__main__":
    import json
    r = run_d06b_ledger_static_tests()
    print(json.dumps({k: r[k] for k in ("passed", "failed", "total")}))
    for row in r["rows"]:
        if not row["pass"]:
            print("FAIL", row["name"], row["detail"])
