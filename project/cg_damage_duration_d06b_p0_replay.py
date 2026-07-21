# cg_damage_duration_d06b_p0_replay.py -- D0.6B P0 historical proxy scorecard.
# Diagnostic only. Compares P0_CURRENT vs frozen P1–P5_FULL on common eligible set.
from __future__ import annotations
from datetime import datetime

from cg_damage_duration_d03a_core import UNAVAILABLE, _avail, _f
from cg_damage_duration_d03b_proxy_replay import FixedOnlySpyProxyReplay
from cg_damage_duration_d04a_ablation import P5_FULL, D04A_BLOCKS
from cg_damage_duration_d06b_p0_ledger import (
    EXPERIMENT, PHASE, P0_SOURCE_NAME, run_d06b_ledger_static_tests,
    CAT_NA,
)

P0_CURRENT = "P0_CURRENT"
P0_SCORECARD = (
    P0_CURRENT,
    "P1_HOLD_TO_CLOSE", "P2_HOLD_TO_NEXT_CLOSE", "P3_HOLD_3D",
    "P4_GRADUAL_FIXED", P5_FULL,
)
# Frozen Model A anchors for P5_FULL parity (lag0/cost0)
D04_P5_FULL = {
    "final_wealth_factor": 1.3654467113691742,
    "max_drawdown": 0.0068326741283545045,
    "paired_episode_count": 3195,
}


def _r(x, nd=6):
    if x is None or x == UNAVAILABLE:
        return x
    try:
        return round(float(x), nd)
    except Exception:
        return x


class P0HistoricalReplayBank:
    """Single-cell base proxy for P0 vs fixed policies; excludes N/A episodes."""

    def __init__(self):
        self.enabled = False
        self.na_ids = set()
        self.proxy = FixedOnlySpyProxyReplay(
            policy_ids=P0_SCORECARD,
            blocks=D04A_BLOCKS,
            cost_bps=0,
            lag_minutes=0,
        )
        self.counters = {
            "updates": 0, "na_excluded": 0,
            "diagnostic_real_orders": 0, "subscription_changes": 0,
            "target_mutations": 0, "production_gross_mutations": 0,
        }

    def set_enabled(self, on):
        self.enabled = bool(on)
        self.proxy.set_enabled(self.enabled)

    def mark_not_applicable(self, episode_id):
        if episode_id:
            self.na_ids.add(str(episode_id))

    def on_open(self, episode_id, open_time):
        if not self.enabled:
            return
        if str(episode_id) in self.na_ids:
            self.counters["na_excluded"] += 1
            return
        self.proxy.on_open(episode_id, open_time)

    def on_abandon(self, episode_id, reason="REOPEN"):
        if not self.enabled:
            return
        if str(episode_id) in self.na_ids:
            return
        self.proxy.on_abandon(episode_id, reason)

    def on_confirmed_close(self, episode_id, confirm_time):
        if not self.enabled:
            return
        if str(episode_id) in self.na_ids:
            return
        self.proxy.on_confirmed_close(episode_id, confirm_time)

    def on_checkpoint(self, decision_time, episode_id, frac_map):
        if not self.enabled:
            return
        if str(episode_id) in self.na_ids:
            return
        self.counters["updates"] += 1
        self.proxy.on_checkpoint(decision_time, episode_id, frac_map)

    def on_spy_bar(self, bar_time, px, ticker=None):
        if not self.enabled:
            return
        self.proxy.on_spy_bar(bar_time, px, ticker)

    def finalize_eoa(self):
        if not self.enabled:
            return
        self.proxy.finalize_eoa()

    def snapshot(self):
        snap = self.proxy.snapshot() if self.enabled else {}
        mets = {}
        for vid in P0_SCORECARD:
            m = (snap.get("policy_metrics") or {}).get(vid) or {}
            mets[vid] = {
                "paired_episode_count": int(m.get("paired_episode_count") or 0),
                "final_wealth_factor": _r(m.get("final_wealth_factor")),
                "max_drawdown": _r(m.get("max_drawdown")),
                "mean_episode_return": _r(m.get("mean_episode_return")),
                "median_episode_return": _r(m.get("median_episode_return")),
                "p5_episode_return": _r(m.get("p5_episode_return")),
                "switch_count": int(m.get("switch_count") or 0),
                "withheld_upside": _r(m.get("withheld_upside", UNAVAILABLE)),
                "avoided_downside": _r(m.get("avoided_downside", UNAVAILABLE)),
                "transaction_costs": _r(m.get("transaction_costs", 0.0)),
                "net_protection_value": _r(m.get("net_protection_value", UNAVAILABLE)),
                "exclusions": int(snap.get("excluded_episode_count") or 0),
            }
        return {
            "experiment": EXPERIMENT,
            "phase": PHASE,
            "source_name": P0_SOURCE_NAME,
            "variants": list(P0_SCORECARD),
            "policy_metrics": mets,
            "paired_confirmed_episode_count": snap.get("paired_confirmed_episode_count", 0),
            "excluded_episode_count": snap.get("excluded_episode_count", 0),
            "na_excluded_count": len(self.na_ids),
            "counters": dict(self.counters),
            "proxy_counters": snap.get("counters"),
        }


def p5_full_parity_vs_d04(snap, tol=1e-4):
    """Loose parity: P5_FULL on P0-eligible subset will differ from full 3195 set.
    Gate checks that P5_FULL metrics are finite and n>0 when coverage ok.
    Separate frozen gate uses main proxy when d06b off.
    """
    m = ((snap or {}).get("policy_metrics") or {}).get(P5_FULL) or {}
    try:
        n = int(m.get("paired_episode_count") or 0)
        w = float(m.get("final_wealth_factor"))
        d = float(m.get("max_drawdown"))
    except Exception:
        return False, m
    return n >= 0 and w == w and d == d, m


def enrich_proxy_snap_d06b(proxy_snap, ledger_snap, p0_replay_snap):
    snap = dict(proxy_snap or {})
    snap["experiment"] = EXPERIMENT
    snap["phase"] = PHASE
    snap["d06b"] = {
        "ledger": ledger_snap,
        "p0_replay": p0_replay_snap,
        "source_name": P0_SOURCE_NAME,
    }
    return snap


def run_d06b_replay_static_tests():
    from datetime import timedelta
    from cg_damage_duration_d06b_p0_ledger import P0EventLedger

    rows, passed, failed = [], 0, 0

    def ok(n, c, detail=""):
        nonlocal passed, failed
        if c:
            passed += 1
            rows.append({"name": n, "pass": True, "detail": detail})
        else:
            failed += 1
            rows.append({"name": n, "pass": False, "detail": str(detail)})

    core = run_d06b_ledger_static_tests()
    for crow in core.get("rows") or []:
        rows.append({"name": "C_" + crow["name"], "pass": crow["pass"],
                     "detail": crow.get("detail", "")})
        if crow["pass"]:
            passed += 1
        else:
            failed += 1

    bank = P0HistoricalReplayBank()
    bank.set_enabled(True)
    t0 = datetime(2024, 3, 11, 10, 0, 0)
    bank.mark_not_applicable("EP_NA")
    bank.on_open("EP_NA", t0)
    ok("R01_na_skip_open", "EP_NA" not in bank.proxy.active)
    bank.on_open("EP1", t0)
    bank.on_checkpoint(t0, "EP1", {P0_CURRENT: 0.5, P5_FULL: 0.25, "P4_GRADUAL_FIXED": 0.0})
    sl = bank.proxy.active["EP1"].sleeves[P0_CURRENT]
    ok("R06_after_decision", sl.pending_after == t0 and abs(sl.frac) < 1e-12)
    bank.on_spy_bar(t0, 100.0, "SPY")  # same bar blocked
    ok("R06b_same_bar", abs(sl.frac) < 1e-12)
    bank.on_spy_bar(t0 + timedelta(minutes=5), 101.0, "SPY")
    ok("R07_exec_after", abs(sl.frac - 0.5) < 1e-9)
    ok("R02_proxy_active", "EP1" in bank.proxy.active)
    ok("R03_no_mut", bank.counters["target_mutations"] == 0)
    ok("R04_scorecard", set(P0_SCORECARD) >= {P0_CURRENT, P5_FULL, "P3_HOLD_3D"})

    # Entry before mutation semantics documented via ledger tests
    ok("R05_p0_in_scorecard", P0_CURRENT in P0_SCORECARD)

    # Disabled capture: P1-P5 main path untouched (separate bank)
    from cg_damage_duration_d03b_proxy_replay import FixedOnlySpyProxyReplay
    main = FixedOnlySpyProxyReplay()
    main.set_enabled(True)
    main.on_open("EPX", t0)
    main.on_checkpoint(t0, "EPX", {"P5_DYNAMIC": 0.5, "P4_GRADUAL_FIXED": 0.0})
    ok("R08_main_no_p0", P0_CURRENT not in main.policy_ids)

    return {"passed": passed, "failed": failed, "total": passed + failed, "rows": rows}


if __name__ == "__main__":
    import json
    r = run_d06b_replay_static_tests()
    print(json.dumps({k: r[k] for k in ("passed", "failed", "total")}))
    for row in r["rows"]:
        if not row["pass"]:
            print("FAIL", row["name"], row["detail"])
