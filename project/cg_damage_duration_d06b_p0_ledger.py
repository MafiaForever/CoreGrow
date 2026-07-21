# cg_damage_duration_d06b_p0_ledger.py -- D0.6B-R1 P0 causal event-time ledger.
# Diagnostic only. Latch eq_b/eq_a under immutable token before/during W2 apply;
# attach to D0 episode_id on EV_PROTECTION open. No post-hoc date/time matching.
from __future__ import annotations
from datetime import datetime

from cg_damage_duration_d03a_core import UNAVAILABLE, _avail, _f

EXPERIMENT = "CG-DAMAGE-DURATION-D0.6B-R1"
PHASE = "D0.6B_CAUSAL_CAPTURE_BIND_REPAIR_AND_SINGLE_REPLAY"
P0_SOURCE_NAME = "D06B_R1_CAUSAL_TOKEN_LATCH"
P0_EPS = 1e-9
MAX_LEDGER_ROWS = 4096
MAX_CHECKPOINT_PER_EP = 512
MAX_EXCLUSION_ROWS = 8192

# Explicit outcome categories (never silent drop).
CAT_BOUND = "BOUND"
CAT_NA = "NOT_APPLICABLE_ZERO_WITHHELD"
CAT_UNBOUND = "UNBOUND"
CAT_MISSING_PRE = "MISSING_PRE"
CAT_MISSING_POST = "MISSING_POST"
CAT_NO_LATER_CK = "NO_LATER_CHECKPOINT"
CAT_INERT = "INERT_NO_PROTECTION_SCALE"


def clamp01(x):
    return max(0.0, min(1.0, float(x)))


def p0_restore_fraction(current_gross, after_gross, withheld):
    if not _avail(withheld) or float(withheld) <= P0_EPS:
        return UNAVAILABLE, CAT_NA
    if not _avail(current_gross) or not _avail(after_gross):
        return UNAVAILABLE, "MISSING_CURRENT_OR_AFTER"
    num = float(current_gross) - float(after_gross)
    return clamp01(num / float(withheld)), "OK"


def _to_dt(t):
    if isinstance(t, datetime):
        return t
    if t is None:
        return None
    try:
        return datetime(
            int(t.year), int(t.month), int(t.day),
            int(getattr(t, "hour", 0) or 0),
            int(getattr(t, "minute", 0) or 0),
            int(getattr(t, "second", 0) or 0),
        )
    except Exception:
        return None


class P0EventLedger:
    """Causal P0 latch → D0 attach. Entry snapshots never overwritten."""

    def __init__(self):
        self.enabled = False
        self.pending = None  # open latch awaiting D0 attach (at most one)
        self.rows = {}  # episode_id -> bound row
        self.latches = {}  # p0_token -> latch record (all outcomes)
        self.token_seq = 0
        self.seq = 0
        self.last_intended_targets = None
        self.last_intended_eq_gross = UNAVAILABLE
        self.last_intended_ts = None
        self._source_current = None
        self.open_missing_pre = {}  # episode_id -> reason (opened without latch)
        self.counters = {
            "protection_observes": 0,
            "inert_skips": 0,
            "bound_entry_snapshots": 0,
            "not_applicable_zero_withheld": 0,
            "unbound_count": 0,
            "missing_pre_count": 0,
            "missing_post_count": 0,
            "no_later_checkpoint_count": 0,
            "repeated_protection_ignored": 0,
            "pending_superseded": 0,
            "attach_without_pending": 0,
            "checkpoints": 0,
            "missing_current_source": 0,
            "diagnostic_real_orders": 0,
            "subscription_changes": 0,
            "target_mutations": 0,
            "order_mutations": 0,
            "production_gross_mutations": 0,
        }

    def set_enabled(self, on):
        self.enabled = bool(on)

    def begin_latch(self, decision_time):
        """Allocate immutable P0 token BEFORE intended exposure mutates."""
        if not self.enabled:
            return None
        self.token_seq += 1
        return "P0T%d" % self.token_seq

    def complete_latch(self, token, decision_time, gross_pre, gross_after,
                       w2_active, source="CgDefensiveTradeApply"):
        """Attach eq_b/eq_a to token in the same causal W2 call (after eq_a known).
        Does not mutate targets. Supersedes any prior pending latch as UNBOUND.
        """
        if not self.enabled:
            return None
        dt = _to_dt(decision_time)
        try:
            pre = float(gross_pre)
            after = float(gross_after)
        except Exception:
            self.counters["missing_post_count"] += 1
            return None
        withheld = max(0.0, pre - after)
        # Inert W2 path (no scale): not a protection_observe.
        if (not bool(w2_active)) and withheld <= P0_EPS:
            self.counters["inert_skips"] += 1
            return None

        self.counters["protection_observes"] += 1
        self.seq += 1
        tok = str(token or "") or ("P0T%d" % self.token_seq)

        # Supersede prior pending (repeated protection before D0 open).
        if self.pending is not None:
            self._close_latch_unbound(self.pending, "SUPERSEDED_BEFORE_D0_OPEN")
            self.counters["pending_superseded"] += 1
            self.counters["repeated_protection_ignored"] += 1

        status = "PENDING_ATTACH"
        latch = {
            "p0_token": tok,
            "decision_time": dt,
            "gross_pre_protection": pre,
            "gross_after_initial_protection": after,
            "withheld_gross": withheld,
            "w2_active": bool(w2_active),
            "source_pre": source + ":eq_b",
            "source_after": source + ":eq_a",
            "capture_sequence": self.seq,
            "bind_mode": "CAUSAL_TOKEN_LATCH",
            "outcome": status,
            "episode_id": None,
            "checkpoint_count": 0,
        }
        self.latches[tok] = latch
        self.pending = latch
        return tok

    def attach_d0_episode(self, episode_id, decision_time, protection_source=None):
        """Deterministically attach pending latch to D0 episode_id.
        No date/time/bar matching — consumes the single pending latch only.
        """
        if not self.enabled:
            return False
        eid = str(episode_id or "")
        if eid in ("", "None", "UNAVAILABLE"):
            return False
        if eid in self.rows:
            self.counters["repeated_protection_ignored"] += 1
            return False

        pending = self.pending
        if pending is None:
            self.counters["attach_without_pending"] += 1
            self.counters["missing_pre_count"] += 1
            if len(self.open_missing_pre) < MAX_EXCLUSION_ROWS:
                self.open_missing_pre[eid] = CAT_MISSING_PRE
            return False

        # Immutable entry already on latch; bind D0 id (no overwrite of eq_b/eq_a).
        withheld = float(pending["withheld_gross"])
        entry_status = CAT_NA if withheld <= P0_EPS else "ELIGIBLE_PENDING_CK"
        row = {
            "episode_id": eid,
            "p0_token": pending["p0_token"],
            "entry_decision_time": pending["decision_time"],
            "bind_decision_time": _to_dt(decision_time),
            "protection_source": protection_source,
            "gross_pre_protection": pending["gross_pre_protection"],
            "gross_after_initial_protection": pending["gross_after_initial_protection"],
            "withheld_gross": withheld,
            "source_pre": pending["source_pre"],
            "source_after": pending["source_after"],
            "capture_sequence": pending["capture_sequence"],
            "bind_mode": "CAUSAL_TOKEN_ATTACH_ON_EV_PROTECTION",
            "entry_status": entry_status,
            "last_current_gross": UNAVAILABLE,
            "last_fraction": UNAVAILABLE,
            "last_fraction_status": entry_status,
            "last_checkpoint_time": None,
            "checkpoint_count": 0,
            "checkpoints": [],
        }
        pending["episode_id"] = eid
        pending["outcome"] = CAT_BOUND if withheld > P0_EPS else CAT_NA
        self.rows[eid] = row
        self.pending = None
        self.counters["bound_entry_snapshots"] += 1
        if withheld <= P0_EPS:
            self.counters["not_applicable_zero_withheld"] += 1
        if len(self.rows) > MAX_LEDGER_ROWS:
            keys = sorted(
                self.rows.keys(),
                key=lambda k: self.rows[k].get("entry_decision_time") or datetime.min,
            )
            for k in keys[: len(self.rows) - MAX_LEDGER_ROWS]:
                self.rows.pop(k, None)
        return True

    def _close_latch_unbound(self, latch, reason):
        if latch is None:
            return
        if latch.get("outcome") in (CAT_BOUND, CAT_NA) and latch.get("episode_id"):
            return
        latch["outcome"] = CAT_UNBOUND
        latch["unbound_reason"] = reason
        self.counters["unbound_count"] += 1

    def observe_intended_targets(self, decision_time, targets, eq_gross,
                                 source="CgRegimeRebalTimeTradeCapture"):
        if not self.enabled:
            return
        if isinstance(targets, dict):
            snap = {}
            for k, v in list(targets.items())[:256]:
                try:
                    snap[str(k)] = float(v)
                except Exception:
                    continue
            self.last_intended_targets = snap
        if _avail(eq_gross):
            self.last_intended_eq_gross = float(eq_gross)
            self.last_intended_ts = _to_dt(decision_time)
            self._source_current = source

    def observe_checkpoint(self, episode_id, decision_time, current_eq_gross=None):
        if not self.enabled:
            return UNAVAILABLE
        eid = str(episode_id or "")
        row = self.rows.get(eid)
        if row is None:
            return UNAVAILABLE
        self.counters["checkpoints"] += 1
        cur = current_eq_gross
        if not _avail(cur):
            cur = self.last_intended_eq_gross
        if not _avail(cur):
            self.counters["missing_current_source"] += 1
            return row.get("last_fraction", UNAVAILABLE)

        frac, status = p0_restore_fraction(
            cur, row["gross_after_initial_protection"], row["withheld_gross"])
        row["last_current_gross"] = float(cur)
        row["last_fraction"] = frac
        row["last_fraction_status"] = status
        row["last_checkpoint_time"] = _to_dt(decision_time)
        row["checkpoint_count"] = int(row.get("checkpoint_count") or 0) + 1
        if row.get("entry_status") == "ELIGIBLE_PENDING_CK" and row["checkpoint_count"] >= 1:
            if float(row["withheld_gross"]) > P0_EPS:
                row["entry_status"] = "ELIGIBLE"
        if len(row["checkpoints"]) < MAX_CHECKPOINT_PER_EP:
            row["checkpoints"].append({
                "decision_time": row["last_checkpoint_time"],
                "current_gross": float(cur),
                "fraction": frac,
                "status": status,
            })
        return frac

    def finalize_eoa(self):
        """Close pending latch as UNBOUND; mark eligible-without-checkpoint."""
        if self.pending is not None:
            self._close_latch_unbound(self.pending, "EOA_STILL_PENDING")
            self.pending = None
        for eid, row in self.rows.items():
            if row.get("entry_status") == "ELIGIBLE_PENDING_CK":
                row["entry_status"] = CAT_NO_LATER_CK
                self.counters["no_later_checkpoint_count"] += 1

    def fraction_for(self, episode_id):
        row = self.rows.get(str(episode_id or ""))
        if row is None:
            return UNAVAILABLE
        if row.get("entry_status") in (CAT_NA, "ELIGIBLE_PENDING_CK", CAT_NO_LATER_CK):
            if row.get("entry_status") == CAT_NA:
                return UNAVAILABLE
            if row.get("entry_status") != "ELIGIBLE":
                return row.get("last_fraction", UNAVAILABLE)
        return row.get("last_fraction", UNAVAILABLE)

    def is_not_applicable(self, episode_id):
        row = self.rows.get(str(episode_id or ""))
        return bool(row and row.get("entry_status") == CAT_NA)

    def is_eligible(self, episode_id):
        row = self.rows.get(str(episode_id or ""))
        return bool(row and row.get("entry_status") == "ELIGIBLE")

    def eligible_episode_ids(self):
        return [eid for eid, r in self.rows.items() if r.get("entry_status") == "ELIGIBLE"]

    def counter_reconciliation(self):
        """protection_observes must equal categorized latch outcomes."""
        c = self.counters
        # Each protection_observe becomes exactly one of: currently pending,
        # bound (incl NA-bound), or unbound (superseded/EOA).
        pending_n = 1 if self.pending is not None else 0
        accounted = (
            int(c["bound_entry_snapshots"])
            + int(c["unbound_count"])
            + pending_n
        )
        # NA zero-withheld are a subset of bound (or pending); track separately.
        observes = int(c["protection_observes"])
        ok = (accounted == observes)
        return {
            "protection_observes": observes,
            "bound_entry_snapshots": int(c["bound_entry_snapshots"]),
            "unbound_count": int(c["unbound_count"]),
            "pending_open": pending_n,
            "not_applicable_zero_withheld": int(c["not_applicable_zero_withheld"]),
            "missing_pre_count": int(c["missing_pre_count"]),
            "missing_post_count": int(c["missing_post_count"]),
            "no_later_checkpoint_count": int(c["no_later_checkpoint_count"]),
            "inert_skips": int(c["inert_skips"]),
            "accounted": accounted,
            "gate": "PASS" if ok else "FAIL",
            "post_hoc_matching_used": "NO",
            "bind_mode": "CAUSAL_TOKEN_LATCH",
        }

    def snapshot(self):
        self.finalize_eoa()
        eligible = sum(1 for r in self.rows.values() if r.get("entry_status") == "ELIGIBLE")
        na = sum(1 for r in self.rows.values() if r.get("entry_status") == CAT_NA)
        bound = int(self.counters["bound_entry_snapshots"])
        # eligible_total = episodes that cleared entry+withheld and need ck;
        # coverage = eligible / (eligible + no_later_ck) among withheld>0 bound.
        pend_ck = sum(
            1 for r in self.rows.values()
            if r.get("entry_status") in ("ELIGIBLE_PENDING_CK", CAT_NO_LATER_CK)
        )
        eligible_total = eligible + pend_ck
        if eligible_total <= 0:
            coverage = UNAVAILABLE
        else:
            coverage = float(eligible) / float(eligible_total)
        recon = self.counter_reconciliation()
        return {
            "experiment": EXPERIMENT,
            "phase": PHASE,
            "source_name": P0_SOURCE_NAME,
            "enabled": self.enabled,
            "bound_episodes": bound,
            "eligible_episodes": eligible,
            "not_applicable_episodes": na,
            "eligible_total": eligible_total,
            "capture_coverage": coverage,
            "counters": dict(self.counters),
            "reconciliation": recon,
            "missing_pre_sample": dict(list(self.open_missing_pre.items())[:32]),
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
                "p0_token": r.get("p0_token"),
                "entry_decision_time": r.get("entry_decision_time"),
                "bind_decision_time": r.get("bind_decision_time"),
                "protection_source": r.get("protection_source"),
                "gross_pre_protection": r.get("gross_pre_protection"),
                "gross_after_initial_protection": r.get("gross_after_initial_protection"),
                "withheld_gross": r.get("withheld_gross"),
                "entry_status": r.get("entry_status"),
                "bind_mode": r.get("bind_mode"),
                "last_current_gross": r.get("last_current_gross"),
                "last_fraction": r.get("last_fraction"),
                "last_fraction_status": r.get("last_fraction_status"),
                "checkpoint_count": r.get("checkpoint_count"),
                "source_pre": r.get("source_pre"),
                "source_after": r.get("source_after"),
                "capture_sequence": r.get("capture_sequence"),
            })
        return out


# Back-compat aliases used by older call sites during transition.
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

    f, st = p0_restore_fraction(0.8, 0.6, 0.4)
    ok("L01_formula_mid", st == "OK" and abs(f - 0.5) < 1e-12)
    f2, st2 = p0_restore_fraction(1.0, 0.6, 0.4)
    ok("L02_clamp_high", st2 == "OK" and abs(f2 - 1.0) < 1e-12)
    f3, st3 = p0_restore_fraction(0.5, 0.6, 0.4)
    ok("L03_clamp_low", st3 == "OK" and abs(f3 - 0.0) < 1e-12)
    f4, st4 = p0_restore_fraction(0.8, 0.6, 0.0)
    ok("L04_zero_withheld", st4 == CAT_NA and f4 == UNAVAILABLE)

    led = P0EventLedger()
    led.set_enabled(True)
    t0 = datetime(2024, 3, 11, 9, 45, 0)
    tok = led.begin_latch(t0)
    ok("L05_token_before_mutate", tok is not None and tok.startswith("P0T"))
    led.complete_latch(tok, t0, 1.0, 0.8, True)
    ok("L06_pending", led.pending is not None and led.pending["p0_token"] == tok)
    t1 = t0 + timedelta(minutes=5)
    ok("L07_attach", led.attach_d0_episode("EP1", t1, "W2"))
    ok("L08_immutable_id", led.rows["EP1"]["p0_token"] == tok)
    ok("L09_no_overwrite", not led.attach_d0_episode("EP1", t1, "W2"))
    ok("L10_bound_counter", led.counters["bound_entry_snapshots"] == 1)

    # Repeated latch before attach → prior UNBOUND, no post-hoc date match
    led2 = P0EventLedger()
    led2.set_enabled(True)
    t_a = led2.begin_latch(t0)
    led2.complete_latch(t_a, t0, 1.0, 0.8, True)
    t_b = led2.begin_latch(t0 + timedelta(days=1))
    led2.complete_latch(t_b, t0 + timedelta(days=1), 1.0, 0.7, True)
    ok("L11_supersede_unbound", led2.counters["unbound_count"] >= 1)
    ok("L12_attach_latest", led2.attach_d0_episode("EP2", t0 + timedelta(days=1, minutes=5), "W2"))
    ok("L13_no_date_match_field", "same_day" not in str(led2.rows["EP2"].get("bind_mode", "")).lower())

    led.observe_intended_targets(t1, {"SPY": 0.9, "BIL": 0.1}, 0.9)
    frac = led.observe_checkpoint("EP1", t1, 0.9)
    ok("L14_checkpoint", _avail(frac) and abs(float(frac) - 0.5) < 1e-12)
    ok("L15_eligible", led.is_eligible("EP1"))

    # Missing pre on open
    led3 = P0EventLedger()
    led3.set_enabled(True)
    ok("L16_missing_pre", not led3.attach_d0_episode("EPX", t1, "IDS"))
    ok("L17_missing_pre_ctr", led3.counters["missing_pre_count"] == 1)

    # Inert skip not counted as protection_observe
    led4 = P0EventLedger()
    led4.set_enabled(True)
    led4.complete_latch(led4.begin_latch(t0), t0, 0.8, 0.8, False)
    ok("L18_inert", led4.counters["protection_observes"] == 0 and led4.counters["inert_skips"] == 1)

    # NA zero withheld still binds
    led5 = P0EventLedger()
    led5.set_enabled(True)
    tok5 = led5.begin_latch(t0)
    led5.complete_latch(tok5, t0, 0.8, 0.8, True)
    led5.attach_d0_episode("EPNA", t1, "W2")
    ok("L19_na", led5.is_not_applicable("EPNA"))

    # Coverage never 1.0 when eligible_total=0
    led6 = P0EventLedger()
    led6.set_enabled(True)
    snap6 = led6.snapshot()
    ok("L20_coverage_empty", snap6["capture_coverage"] == UNAVAILABLE)

    recon = led.counter_reconciliation()
    ok("L21_recon", recon["gate"] == "PASS", detail=str(recon))

    # Entry ID present before persistence
    ok("L22_id_before_persist", "p0_token" in led.rows["EP1"] and led.rows["EP1"]["episode_id"] == "EP1")

    # Disabled no-op
    led7 = P0EventLedger()
    led7.set_enabled(False)
    ok("L23_disabled", led7.begin_latch(t0) is None)

    # Targets unchanged
    targets = {"SPY": 0.7, "QQQ": 0.3}
    before = dict(targets)
    led.observe_intended_targets(t1, targets, 1.0)
    ok("L24_noop_targets", targets == before)
    ok("L25_zero_mut", led.counters["target_mutations"] == 0
       and led.counters["order_mutations"] == 0)

    # .NET-like time object conversion
    class _FakeT:
        year, month, day, hour, minute, second = 2024, 3, 11, 9, 45, 0
    led8 = P0EventLedger()
    led8.set_enabled(True)
    tok8 = led8.begin_latch(_FakeT())
    led8.complete_latch(tok8, _FakeT(), 1.0, 0.8, True)
    ok("L26_fake_dt", led8.pending is not None and isinstance(led8.pending["decision_time"], datetime))

    return {"passed": passed, "failed": failed, "total": passed + failed, "rows": rows}


# Keep old method names as thin wrappers for residual callers.
def _observe_protection_apply(self, decision_time, gross_pre, gross_after,
                              w2_active, source="CgDefensiveTradeApply"):
    return self.complete_latch(
        self.begin_latch(decision_time), decision_time, gross_pre, gross_after,
        w2_active, source)


P0EventLedger.observe_protection_apply = _observe_protection_apply
P0EventLedger.bind_episode = P0EventLedger.attach_d0_episode


if __name__ == "__main__":
    import json
    r = run_d06b_ledger_static_tests()
    print(json.dumps({k: r[k] for k in ("passed", "failed", "total")}))
    for row in r["rows"]:
        if not row["pass"]:
            print("FAIL", row["name"], row["detail"])
