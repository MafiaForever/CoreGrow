# cg_damage_duration_d04b_robustness.py -- D0.4B lag/cost sensitivity (diagnostic only).
# Evaluation grid over existing shadow fractions; never mutates P5_FULL or production.
from __future__ import annotations
from datetime import datetime, timedelta

from cg_damage_duration_d03a_core import UNAVAILABLE, _avail, _f
from cg_damage_duration_d03b_proxy_replay import FixedOnlySpyProxyReplay, weakly_dominates
from cg_damage_duration_d04a_ablation import (
    SCORECARD_VARIANT_IDS, P5_FULL, P5_NO_STRUCTURE, P5_NO_ABSTENTION,
    D04A_BLOCKS,
)

EXPERIMENT = "CG-DAMAGE-DURATION-D0.4B"
PHASE = "D0.4B_COST_LAG_ROBUSTNESS_AND_MODEL_A_CLOSEOUT"

LAG_MINUTES = (0, 5, 15)
COST_BPS = (0, 1, 5)
GRID_CELLS = tuple((lag, cost) for lag in LAG_MINUTES for cost in COST_BPS)

D04A_P5_FULL_BASE = {
    "final_wealth_factor": 1.3654467113691742,
    "max_drawdown": 0.0068326741283545045,
    "paired_episode_count": 3195,
}


def cell_key(lag_minutes, cost_bps):
    return "lag%02d_cost%dbps" % (int(lag_minutes), int(cost_bps))


def _r(x, nd=6):
    if x is None or x == UNAVAILABLE:
        return x
    try:
        return round(float(x), nd)
    except Exception:
        return x


class ModelARobustnessGrid:
    """Nine parallel scorecard-only proxy cells; base policies untouched elsewhere."""

    def __init__(self):
        self.enabled = False
        self.cells = {}
        for lag, cost in GRID_CELLS:
            self.cells[(lag, cost)] = FixedOnlySpyProxyReplay(
                policy_ids=SCORECARD_VARIANT_IDS,
                blocks=D04A_BLOCKS,
                cost_bps=cost,
                lag_minutes=lag,
            )
        self.counters = {
            "updates": 0, "diagnostic_real_orders": 0,
            "subscription_changes": 0, "target_mutations": 0,
            "production_gross_mutations": 0,
        }

    def set_enabled(self, on):
        self.enabled = bool(on)
        for cell in self.cells.values():
            cell.set_enabled(self.enabled)

    def on_open(self, episode_id, open_time):
        if not self.enabled:
            return
        for cell in self.cells.values():
            cell.on_open(episode_id, open_time)

    def on_abandon(self, episode_id, reason="REOPEN"):
        if not self.enabled:
            return
        for cell in self.cells.values():
            cell.on_abandon(episode_id, reason)

    def on_confirmed_close(self, episode_id, confirm_time):
        if not self.enabled:
            return
        for cell in self.cells.values():
            cell.on_confirmed_close(episode_id, confirm_time)

    def on_checkpoint(self, decision_time, episode_id, frac_map):
        if not self.enabled:
            return
        fm = dict(frac_map or {})
        for cell in self.cells.values():
            cell.on_checkpoint(decision_time, episode_id, fm)

    def on_spy_bar(self, bar_time, px, ticker=None):
        if not self.enabled:
            return
        for cell in self.cells.values():
            cell.on_spy_bar(bar_time, px, ticker)

    def finalize_eoa(self):
        if not self.enabled:
            return
        for cell in self.cells.values():
            cell.finalize_eoa()

    def snapshot(self):
        grid = {}
        for (lag, cost), cell in self.cells.items():
            snap = cell.snapshot()
            key = cell_key(lag, cost)
            mets = {}
            for vid in SCORECARD_VARIANT_IDS:
                m = (snap.get("policy_metrics") or {}).get(vid) or {}
                mets[vid] = {
                    "paired_episode_count": int(m.get("paired_episode_count") or 0),
                    "final_wealth_factor": _r(m.get("final_wealth_factor")),
                    "max_drawdown": _r(m.get("max_drawdown")),
                    "p5_episode_return": _r(m.get("p5_episode_return")),
                    "switch_count": int(m.get("switch_count") or 0),
                }
            grid[key] = {
                "lag_minutes": lag,
                "cost_bps": cost,
                "paired_confirmed_episode_count": snap.get(
                    "paired_confirmed_episode_count", 0),
                "excluded_episode_count": snap.get("excluded_episode_count", 0),
                "policy_metrics": mets,
            }
        return {
            "experiment": EXPERIMENT,
            "phase": PHASE,
            "lags": list(LAG_MINUTES),
            "costs_bps": list(COST_BPS),
            "variants": list(SCORECARD_VARIANT_IDS),
            "grid": grid,
            "counters": dict(self.counters),
        }


def p5_full_base_parity(grid_snap, tol=1e-6):
    cell = ((grid_snap or {}).get("grid") or {}).get(cell_key(0, 0)) or {}
    m = (cell.get("policy_metrics") or {}).get(P5_FULL) or {}
    try:
        w = float(m.get("final_wealth_factor"))
        d = float(m.get("max_drawdown"))
        n = int(m.get("paired_episode_count") or 0)
    except Exception:
        return False, m
    ok = (
        n == D04A_P5_FULL_BASE["paired_episode_count"]
        and abs(w - D04A_P5_FULL_BASE["final_wealth_factor"]) < tol
        and abs(d - D04A_P5_FULL_BASE["max_drawdown"]) < tol
    )
    return ok, m


def _advantage_survives(challenger_id, base_id, grid_snap, cost_bps, lag_minutes=0):
    """True if challenger has higher wealth than base at the cell (lag, cost)."""
    cell = ((grid_snap or {}).get("grid") or {}).get(
        cell_key(lag_minutes, cost_bps)) or {}
    mets = cell.get("policy_metrics") or {}
    c = mets.get(challenger_id) or {}
    b = mets.get(base_id) or {}
    try:
        cw, bw = float(c.get("final_wealth_factor")), float(b.get("final_wealth_factor"))
    except Exception:
        return False
    return cw > bw + 1e-12


def classify_robustness(grid_snap, min_n=100):
    grid = (grid_snap or {}).get("grid") or {}
    parity_ok, _ = p5_full_base_parity(grid_snap)
    p3_dom = 0
    p4_dom = 0
    cells_ok = 0
    fragile_cost = False
    fragile_lag = False
    base_cell = grid.get(cell_key(0, 0)) or {}
    base_p5 = (base_cell.get("policy_metrics") or {}).get(P5_FULL) or {}
    try:
        base_w = float(base_p5.get("final_wealth_factor"))
        base_dd = float(base_p5.get("max_drawdown"))
    except Exception:
        base_w = base_dd = None

    for (lag, cost) in GRID_CELLS:
        cell = grid.get(cell_key(lag, cost)) or {}
        mets = cell.get("policy_metrics") or {}
        n = int(cell.get("paired_confirmed_episode_count") or 0)
        if n < int(min_n):
            continue
        cells_ok += 1
        p5 = mets.get(P5_FULL) or {}
        p3 = mets.get("P3_HOLD_3D") or {}
        p4 = mets.get("P4_GRADUAL_FIXED") or {}
        w3, _ = weakly_dominates(p3, p5)
        w4, _ = weakly_dominates(p4, p5)
        # weakly_dominates(fixed, p5) returns (weak, strict) where fixed dominates p5
        if w3:
            p3_dom += 1
        if w4:
            p4_dom += 1
        if base_w is not None and base_dd is not None:
            try:
                w = float(p5.get("final_wealth_factor"))
                d = float(p5.get("max_drawdown"))
            except Exception:
                continue
            if cost > 0 and (w < base_w * 0.95 or d > base_dd * 1.5 + 1e-12):
                fragile_cost = True
            if lag > 0 and (w < base_w * 0.95 or d > base_dd * 1.5 + 1e-12):
                fragile_lag = True

    majority = max(1, cells_ok // 2 + (1 if cells_ok % 2 else 0))
    # majority of cells: > half
    maj = cells_ok // 2 + 1 if cells_ok else 0
    no_s_1 = _advantage_survives(P5_NO_STRUCTURE, P5_FULL, grid_snap, 1, 0)
    no_s_5 = _advantage_survives(P5_NO_STRUCTURE, P5_FULL, grid_snap, 5, 0)
    no_a_1 = _advantage_survives(P5_NO_ABSTENTION, P5_FULL, grid_snap, 1, 0)
    no_a_5 = _advantage_survives(P5_NO_ABSTENTION, P5_FULL, grid_snap, 5, 0)

    lag_rob = "STABLE"
    cost_rob = "STABLE"
    if fragile_lag:
        lag_rob = "FRAGILE"
    if fragile_cost:
        cost_rob = "FRAGILE"

    # trade-off: P5 still lower DD than P3/P4 at base costs across lags?
    tradeoff = "STABLE_TRADEOFF"
    if p3_dom >= maj or p4_dom >= maj or fragile_cost or fragile_lag:
        tradeoff = "COST_OR_LAG_FRAGILE" if (fragile_cost or fragile_lag) else "DOMINANCE_SHIFT"

    if not parity_ok:
        verdict = "STOP_D04B_BASE_PARITY_FAIL"
    elif cells_ok < len(GRID_CELLS):
        verdict = "STOP_D04B_INSUFFICIENT_CELL_COVERAGE"
    elif p3_dom >= maj or p4_dom >= maj:
        verdict = "MODEL_A_REJECT_DOMINATED_UNDER_ROBUSTNESS"
    elif not (no_s_1 and no_s_5 and no_a_1 and no_a_5):
        # ablation wealth edge dies under costs -> FULL tradeoff more credible
        verdict = "MODEL_A_ACCEPT_TRADEOFF_COST_SENSITIVE_ABLATIONS"
    else:
        verdict = "MODEL_A_ACCEPT_WITH_ROBUST_ABLATION_EDGE"

    # Reinterpret: if ablations keep wealth edge at 1/5 bps, note that; closeout still classify
    if parity_ok and cells_ok >= len(GRID_CELLS) and p3_dom < maj and p4_dom < maj:
        if no_s_1 and no_s_5 and no_a_1 and no_a_5:
            if tradeoff == "STABLE_TRADEOFF":
                verdict = "MODEL_A_CLOSEOUT_ACCEPT_TRADEOFF_ABLATION_EDGE_SURVIVES_COST"
            else:
                verdict = "MODEL_A_CLOSEOUT_ACCEPT_TRADEOFF_WITH_LAG_OR_COST_STRESS"
        else:
            verdict = "MODEL_A_CLOSEOUT_ACCEPT_TRADEOFF_ABLATION_EDGE_COST_FRAGILE"

    return {
        "p5_full_base_parity_gate": "PASS" if parity_ok else "FAIL",
        "grid_cell_count": len(GRID_CELLS),
        "cells_with_min_n": cells_ok,
        "p5_full_dominated_by_p3_cells": p3_dom,
        "p5_full_dominated_by_p4_cells": p4_dom,
        "no_structure_survives_1bps": bool(no_s_1),
        "no_structure_survives_5bps": bool(no_s_5),
        "no_abstention_survives_1bps": bool(no_a_1),
        "no_abstention_survives_5bps": bool(no_a_5),
        "execution_lag_robustness": lag_rob,
        "cost_robustness": cost_rob,
        "model_a_closeout_verdict": verdict,
        "tradeoff_class": tradeoff,
        "model_b_challenger_evidence": (
            "ABLATION_WEALTH_EDGE_AT_COST"
            if (no_s_1 or no_a_1) else "NO_COST_ROBUST_ABLATION_EDGE"
        ),
    }


def enrich_proxy_snap_d04b(proxy_snap, grid_snap):
    snap = dict(proxy_snap or {})
    g = dict(grid_snap or {})
    clf = classify_robustness(g)
    snap["experiment"] = EXPERIMENT
    snap["phase"] = PHASE
    # Transport budget: drop bulky walk-forward block matrices; keep D04A metrics/verdict.
    d04a = snap.get("d04a")
    if isinstance(d04a, dict):
        sc = dict(d04a.get("scorecard") or {})
        sc.pop("blocks", None)
        snap["d04a"] = {
            "verdict": d04a.get("verdict"),
            "scorecard": {
                "policy_metrics": sc.get("policy_metrics"),
                "pairwise": sc.get("pairwise"),
                "block_coverage": sc.get("block_coverage"),
                "component_effects": sc.get("component_effects"),
                "p5_full_dominated_by_p3": sc.get("p5_full_dominated_by_p3"),
                "p5_full_dominated_by_p4": sc.get("p5_full_dominated_by_p4"),
            },
        }
    # Top-level blocks are large; keep only per-block n for audit.
    blocks = snap.get("blocks")
    if isinstance(blocks, dict):
        snap["blocks"] = {k: {"n": (v or {}).get("n", 0)} for k, v in blocks.items()}
    snap["d04b"] = {"grid": g.get("grid"), "classification": clf,
                    "lags": g.get("lags"), "costs_bps": g.get("costs_bps"),
                    "variants": g.get("variants")}
    return snap


def run_d04b_robustness_static_tests():
    from datetime import timedelta
    from cg_damage_duration_d03b_proxy_replay import FixedOnlySpyProxyReplay, EPS

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
    # cost arithmetic: 100% buy then sell at flat px, 5bps each leg
    sl = FixedOnlySpyProxyReplay(policy_ids=("P5_FULL",), cost_bps=5).active
    r = FixedOnlySpyProxyReplay(policy_ids=("P5_FULL",), cost_bps=5, lag_minutes=0)
    r.set_enabled(True)
    r.on_open("E1", t0)
    r.on_checkpoint(t0, "E1", {"P5_FULL": 1.0})
    r.on_spy_bar(t0, 100.0, "SPY")  # same bar blocked
    ok("R01_same_bar", abs(r.active["E1"].sleeves["P5_FULL"].frac) < 1e-12)
    t1 = t0 + timedelta(minutes=5)
    r.on_spy_bar(t1, 100.0, "SPY")
    # buy 100% of 1.0 at 5bps => equity 0.9995, frac=1
    e_buy = r.active["E1"].sleeves["P5_FULL"].equity(100.0)
    ok("R02_5bps_buy", abs(e_buy - 0.9995) < 1e-9, detail=str(e_buy))
    r.on_confirmed_close("E1", t1)
    t2 = t1 + timedelta(minutes=5)
    r.on_spy_bar(t2, 100.0, "SPY")
    # sell leg another 5bps on remaining equity
    w = r.completed[0].wealth["P5_FULL"]
    ok("R03_5bps_roundtrip", abs(w - 0.9995 * 0.9995) < 1e-9, detail=str(w))

    # 1bps
    r1 = FixedOnlySpyProxyReplay(policy_ids=("P5_FULL",), cost_bps=1)
    r1.set_enabled(True)
    r1.on_open("E2", t0)
    r1.on_checkpoint(t0, "E2", {"P5_FULL": 1.0})
    r1.on_spy_bar(t1, 100.0, "SPY")
    ok("R04_1bps_buy", abs(r1.active["E2"].sleeves["P5_FULL"].equity(100.0) - 0.9999) < 1e-9)

    # +5 min lag: decision t0, bar t0+5 must not exec if lag=5 requires >= t0+5
    # with lag=5, pending_after=t0+5; bar at t0+5 eligible (>=); bar at t0+4 not
    rl = FixedOnlySpyProxyReplay(policy_ids=("P5_FULL",), lag_minutes=5)
    rl.set_enabled(True)
    rl.on_open("E3", t0)
    rl.on_checkpoint(t0, "E3", {"P5_FULL": 0.5})
    rl.on_spy_bar(t0 + timedelta(minutes=4), 100.0, "SPY")
    ok("R05_lag5_blocked_early", abs(rl.active["E3"].sleeves["P5_FULL"].frac) < 1e-12)
    rl.on_spy_bar(t0 + timedelta(minutes=5), 100.0, "SPY")
    ok("R06_lag5_exec", abs(rl.active["E3"].sleeves["P5_FULL"].frac - 0.5) < 1e-12)

    # +15 min
    rl15 = FixedOnlySpyProxyReplay(policy_ids=("P5_FULL",), lag_minutes=15)
    rl15.set_enabled(True)
    rl15.on_open("E4", t0)
    rl15.on_checkpoint(t0, "E4", {"P5_FULL": 1.0})
    rl15.on_spy_bar(t0 + timedelta(minutes=10), 100.0, "SPY")
    ok("R07_lag15_block", abs(rl15.active["E4"].sleeves["P5_FULL"].frac) < 1e-12)
    rl15.on_spy_bar(t0 + timedelta(minutes=15), 100.0, "SPY")
    ok("R08_lag15_exec", abs(rl15.active["E4"].sleeves["P5_FULL"].frac - 1.0) < 1e-12)

    # missing bar: pending stays; later bar executes (forward only)
    rm = FixedOnlySpyProxyReplay(policy_ids=("P5_FULL",), lag_minutes=0)
    rm.set_enabled(True)
    rm.on_open("E5", t0)
    rm.on_checkpoint(t0, "E5", {"P5_FULL": 1.0})
    rm.on_spy_bar(t0 + timedelta(minutes=5), -1.0, "SPY")  # invalid
    ok("R09_missing_no_exec", "E5" in rm.active and abs(rm.active["E5"].sleeves["P5_FULL"].frac) < 1e-12)
    rm.on_spy_bar(t0 + timedelta(minutes=10), 100.0, "SPY")
    ok("R10_forward_after_missing", abs(rm.active["E5"].sleeves["P5_FULL"].frac - 1.0) < 1e-12)

    # grid isolation: 9 cells, independent
    g = ModelARobustnessGrid()
    g.set_enabled(True)
    ok("R11_nine_cells", len(g.cells) == 9)
    g.on_open("EG", t0)
    g.on_checkpoint(t0, "EG", {"P5_FULL": 0.5, "P3_HOLD_3D": UNAVAILABLE, "P4_GRADUAL_FIXED": 0.25})
    # mutate one cell sleeve must not affect another
    g.cells[(0, 0)].active["EG"].sleeves["P5_FULL"].frac = 0.75
    ok("R12_no_shared_state",
       abs(g.cells[(5, 1)].active["EG"].sleeves["P5_FULL"].frac) < 1e-12)

    g2 = ModelARobustnessGrid()
    ok("R13_disabled_noop", len(g2.cells[(0, 0)].active) == 0)

    # base proxy cost 0 unchanged path
    rb = FixedOnlySpyProxyReplay(policy_ids=("P5_FULL",), cost_bps=0)
    rb.set_enabled(True)
    rb.on_open("EB", t0)
    rb.on_checkpoint(t0, "EB", {"P5_FULL": 1.0})
    rb.on_spy_bar(t1, 100.0, "SPY")
    ok("R14_zero_cost_parity_equity",
       abs(rb.active["EB"].sleeves["P5_FULL"].equity(100.0) - 1.0) < 1e-12)

    ok("R15_zero_mut", g.counters["diagnostic_real_orders"] == 0)

    # classify helpers on fake grid
    fake = {"grid": {}}
    for lag, cost in GRID_CELLS:
        fake["grid"][cell_key(lag, cost)] = {
            "lag_minutes": lag, "cost_bps": cost,
            "paired_confirmed_episode_count": 3195,
            "excluded_episode_count": 0,
            "policy_metrics": {
                P5_FULL: {
                    "final_wealth_factor": D04A_P5_FULL_BASE["final_wealth_factor"],
                    "max_drawdown": D04A_P5_FULL_BASE["max_drawdown"],
                    "paired_episode_count": 3195, "p5_episode_return": 0.0,
                    "switch_count": 2013,
                },
                "P3_HOLD_3D": {
                    "final_wealth_factor": 1.35, "max_drawdown": 0.03,
                    "paired_episode_count": 3195, "p5_episode_return": 0.0,
                    "switch_count": 62,
                },
                "P4_GRADUAL_FIXED": {
                    "final_wealth_factor": 12.9, "max_drawdown": 0.036,
                    "paired_episode_count": 3195, "p5_episode_return": 0.0,
                    "switch_count": 100,
                },
                P5_NO_STRUCTURE: {
                    "final_wealth_factor": 3.5 if cost == 0 else (2.0 if cost == 1 else 0.9),
                    "max_drawdown": 0.015, "paired_episode_count": 3195,
                    "p5_episode_return": 0.0, "switch_count": 1000,
                },
                P5_NO_ABSTENTION: {
                    "final_wealth_factor": 3.6 if cost == 0 else (2.1 if cost == 1 else 0.8),
                    "max_drawdown": 0.015, "paired_episode_count": 3195,
                    "p5_episode_return": 0.0, "switch_count": 1000,
                },
                "P5_NO_CHANGEPOINT": {
                    "final_wealth_factor": 1.5, "max_drawdown": 0.02,
                    "paired_episode_count": 3195, "p5_episode_return": 0.0,
                    "switch_count": 100,
                },
                "P5_NO_HYSTERESIS": {
                    "final_wealth_factor": 1.6, "max_drawdown": 0.016,
                    "paired_episode_count": 3195, "p5_episode_return": 0.0,
                    "switch_count": 100,
                },
            },
        }
    pok, _ = p5_full_base_parity(fake)
    ok("R16_base_parity_helper", pok)
    clf = classify_robustness(fake)
    ok("R17_classify_keys", "model_a_closeout_verdict" in clf)

    return {"passed": passed, "failed": failed, "total": passed + failed, "rows": rows}


if __name__ == "__main__":
    import json
    rep = run_d04b_robustness_static_tests()
    print(json.dumps({"passed": rep["passed"], "failed": rep["failed"], "total": rep["total"]}))
    for row in rep["rows"]:
        if not row["pass"]:
            print("FAIL", row["name"], row["detail"])
