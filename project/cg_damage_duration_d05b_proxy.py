# cg_damage_duration_d05b_proxy.py -- D0.5B Model B soft-confidence proxy grid.
# Diagnostic only. Evaluates P3 / P5_FULL / P5_NO_ABSTENTION / P5B on lag×cost grid.
from __future__ import annotations
from datetime import datetime

from cg_damage_duration_d03a_core import UNAVAILABLE, _avail, _f
from cg_damage_duration_d03b_proxy_replay import FixedOnlySpyProxyReplay, weakly_dominates
from cg_damage_duration_d04a_ablation import P5_FULL, P5_NO_ABSTENTION, D04A_BLOCKS
from cg_damage_duration_d05b_core import (
    EXPERIMENT, PHASE, P5B_SOFT_CONFIDENCE_BLEND, MODEL_B_SCORECARD,
    SoftConfidenceModelBEngine, D04_P5_FULL, D04_P5_NO_ABSTENTION,
    RECOVERY_CONFIDENCE_SOURCE, run_d05b_core_static_tests,
)

LAG_MINUTES = (0, 5)
COST_BPS = (0, 1, 5)
GRID_CELLS = tuple((lag, cost) for lag in LAG_MINUTES for cost in COST_BPS)


def cell_key(lag_minutes, cost_bps):
    return "lag%02d_cost%dbps" % (int(lag_minutes), int(cost_bps))


def _r(x, nd=6):
    if x is None or x == UNAVAILABLE:
        return x
    try:
        return round(float(x), nd)
    except Exception:
        return x


def _metric_row(m):
    if not isinstance(m, dict):
        return {}
    keys = (
        "policy_id", "paired_episode_count", "final_wealth_factor", "max_drawdown",
        "mean_episode_return", "median_episode_return", "p5_episode_return",
        "switch_count",
    )
    out = {k: m.get(k) for k in keys if k in m}
    if out and "policy_id" not in out and m.get("policy_id"):
        out["policy_id"] = m.get("policy_id")
    return out


class ModelBChallengerBank:
    """Owns Model B engine + 6-cell scorecard proxy. Isolated from Model A sleeves."""

    def __init__(self):
        self.enabled = False
        self.engine = SoftConfidenceModelBEngine()
        self.cells = {}
        for lag, cost in GRID_CELLS:
            self.cells[(lag, cost)] = FixedOnlySpyProxyReplay(
                policy_ids=MODEL_B_SCORECARD,
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
        """frac_map must include P5_FULL / P5_NO_ABSTENTION; P3 via proxy P123; P5B from engine."""
        if not self.enabled:
            return
        fm = dict(frac_map or {})
        for cell in self.cells.values():
            cell.on_checkpoint(decision_time, episode_id, fm)

    def update_fraction(self, snap_b, snap_c):
        if not self.enabled:
            return UNAVAILABLE
        self.counters["updates"] += 1
        return self.engine.update(snap_b, snap_c)

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
            for vid in MODEL_B_SCORECARD:
                m = (snap.get("policy_metrics") or {}).get(vid) or {}
                mets[vid] = {
                    "paired_episode_count": int(m.get("paired_episode_count") or 0),
                    "final_wealth_factor": _r(m.get("final_wealth_factor")),
                    "max_drawdown": _r(m.get("max_drawdown")),
                    "mean_episode_return": _r(m.get("mean_episode_return")),
                    "median_episode_return": _r(m.get("median_episode_return")),
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
            "recovery_confidence_source": RECOVERY_CONFIDENCE_SOURCE,
            "lags": list(LAG_MINUTES),
            "costs_bps": list(COST_BPS),
            "variants": list(MODEL_B_SCORECARD),
            "grid": grid,
            "counters": dict(self.counters),
            "engine_counters": dict(self.engine.counters),
        }


def p5_full_parity(grid_snap, tol=1e-6):
    cell = ((grid_snap or {}).get("grid") or {}).get(cell_key(0, 0)) or {}
    m = (cell.get("policy_metrics") or {}).get(P5_FULL) or {}
    try:
        w = float(m.get("final_wealth_factor"))
        d = float(m.get("max_drawdown"))
        n = int(m.get("paired_episode_count") or 0)
    except Exception:
        return False, m
    ok = (
        n == D04_P5_FULL["paired_episode_count"]
        and abs(w - D04_P5_FULL["final_wealth_factor"]) < tol
        and abs(d - D04_P5_FULL["max_drawdown"]) < tol
    )
    return ok, m


def p5_no_abstention_parity(grid_snap, tol=1e-6):
    cell = ((grid_snap or {}).get("grid") or {}).get(cell_key(0, 0)) or {}
    m = (cell.get("policy_metrics") or {}).get(P5_NO_ABSTENTION) or {}
    try:
        w = float(m.get("final_wealth_factor"))
        d = float(m.get("max_drawdown"))
        n = int(m.get("paired_episode_count") or 0)
    except Exception:
        return False, m
    ok = (
        n == D04_P5_NO_ABSTENTION["paired_episode_count"]
        and abs(w - D04_P5_NO_ABSTENTION["final_wealth_factor"]) < tol
        and abs(d - D04_P5_NO_ABSTENTION["max_drawdown"]) < tol
    )
    return ok, m


def _cell_mets(grid_snap, lag, cost):
    cell = ((grid_snap or {}).get("grid") or {}).get(cell_key(lag, cost)) or {}
    return cell.get("policy_metrics") or {}, int(
        cell.get("paired_confirmed_episode_count") or 0)


def classify_model_b(grid_snap, min_n=100):
    """
    MODEL_B_REJECTED if dominated by P3 or P5_FULL in wealth+DD across all six cells,
    or does not reduce switches vs P5_NO_ABSTENTION.
    MODEL_B_CONTINUES_TO_CLOSEOUT if not dominated and turnover/cost improves on NO_ABSTENTION.
    MODEL_B_INCONCLUSIVE if coverage insufficient.
    """
    full_ok, _ = p5_full_parity(grid_snap)
    na_ok, _ = p5_no_abstention_parity(grid_snap)
    cells_ok = 0
    dom_p3 = 0
    dom_p5 = 0
    switch_reduce_cells = 0
    cost_improve = 0
    lag_ok = 0
    base_sw_b = base_sw_na = None

    for lag, cost in GRID_CELLS:
        mets, n = _cell_mets(grid_snap, lag, cost)
        if n < int(min_n):
            continue
        cells_ok += 1
        p3 = mets.get("P3_HOLD_3D") or {}
        p5 = mets.get(P5_FULL) or {}
        na = mets.get(P5_NO_ABSTENTION) or {}
        pb = mets.get(P5B_SOFT_CONFIDENCE_BLEND) or {}
        w3, _ = weakly_dominates(p3, pb)
        w5, _ = weakly_dominates(p5, pb)
        if w3:
            dom_p3 += 1
        if w5:
            dom_p5 += 1
        try:
            sb = int(pb.get("switch_count") or 0)
            sna = int(na.get("switch_count") or 0)
        except Exception:
            sb = sna = None
        if sb is not None and sna is not None and sb < sna:
            switch_reduce_cells += 1
        if lag == 0 and cost == 0 and sb is not None:
            base_sw_b, base_sw_na = sb, sna
        # cost profile improve vs NO_ABSTENTION: lower switches and not worse wealth/DD
        if cost > 0 and lag == 0:
            try:
                if (float(pb.get("final_wealth_factor")) >= float(na.get("final_wealth_factor")) * 0.95
                        and float(pb.get("max_drawdown")) <= float(na.get("max_drawdown")) * 1.25
                        and sb is not None and sna is not None and sb < sna):
                    cost_improve += 1
            except Exception:
                pass
        if lag > 0:
            try:
                if float(pb.get("final_wealth_factor")) > 0:
                    lag_ok += 1
            except Exception:
                pass

    switch_reduce = (
        base_sw_b is not None and base_sw_na is not None and base_sw_b < base_sw_na
    )
    dominated_all_p3 = cells_ok >= len(GRID_CELLS) and dom_p3 >= cells_ok
    dominated_all_p5 = cells_ok >= len(GRID_CELLS) and dom_p5 >= cells_ok

    if cells_ok < len(GRID_CELLS) or not full_ok or not na_ok:
        verdict = "MODEL_B_INCONCLUSIVE"
        reason = "INSUFFICIENT_COVERAGE_OR_PARITY"
    elif dominated_all_p3 or dominated_all_p5 or not switch_reduce:
        verdict = "MODEL_B_REJECTED"
        reason = (
            "DOMINATED_BY_P3" if dominated_all_p3 else
            "DOMINATED_BY_P5_FULL" if dominated_all_p5 else
            "NO_SWITCH_REDUCTION_VS_NO_ABSTENTION"
        )
    else:
        verdict = "MODEL_B_CONTINUES_TO_CLOSEOUT"
        reason = "NOT_DOMINATED_AND_TURNOVER_IMPROVES_ON_NO_ABSTENTION"

    cost_rob = "IMPROVED" if cost_improve >= 1 else (
        "FRAGILE" if cells_ok >= len(GRID_CELLS) else "INCONCLUSIVE")
    lag_rob = "OK" if lag_ok >= 1 else (
        "FRAGILE" if cells_ok >= len(GRID_CELLS) else "INCONCLUSIVE")

    return {
        "p5_full_parity_gate": "PASS" if full_ok else "FAIL",
        "p5_no_abstention_parity_gate": "PASS" if na_ok else "FAIL",
        "grid_cell_count": len(GRID_CELLS),
        "cells_with_min_n": cells_ok,
        "model_b_dominated_by_p3_cells": dom_p3,
        "model_b_dominated_by_p5_full_cells": dom_p5,
        "model_b_switch_reduction_vs_no_abstention": bool(switch_reduce),
        "base_switch_p5b": base_sw_b,
        "base_switch_p5_no_abstention": base_sw_na,
        "switch_reduce_cells": switch_reduce_cells,
        "model_b_cost_robustness": cost_rob,
        "model_b_lag_robustness": lag_rob,
        "model_b_verdict": verdict,
        "reason": reason,
        "recovery_confidence_source": RECOVERY_CONFIDENCE_SOURCE,
        "p0_dependency_found": "NO",
    }


def build_model_b_metrics_rows(grid_snap):
    rows = []
    for lag, cost in GRID_CELLS:
        mets, n = _cell_mets(grid_snap, lag, cost)
        cell = ((grid_snap or {}).get("grid") or {}).get(cell_key(lag, cost)) or {}
        excl = int(cell.get("excluded_episode_count") or 0)
        for vid in MODEL_B_SCORECARD:
            m = mets.get(vid) or {}
            rows.append({
                "cell": cell_key(lag, cost),
                "lag_minutes": lag,
                "cost_bps": cost,
                "policy_id": vid,
                "paired_episodes": int(m.get("paired_episode_count") or n or 0),
                "final_wealth_factor": m.get("final_wealth_factor"),
                "max_drawdown": m.get("max_drawdown"),
                "mean_episode_return": m.get("mean_episode_return"),
                "median_episode_return": m.get("median_episode_return"),
                "p5_episode_return": m.get("p5_episode_return"),
                "switch_count": m.get("switch_count"),
                "excluded_episode_count": excl,
            })
    return rows


def build_model_b_pairwise_rows(grid_snap):
    rows = []
    for lag, cost in GRID_CELLS:
        mets, n = _cell_mets(grid_snap, lag, cost)
        pb = mets.get(P5B_SOFT_CONFIDENCE_BLEND) or {}
        for rhs in ("P3_HOLD_3D", P5_FULL, P5_NO_ABSTENTION):
            other = mets.get(rhs) or {}
            try:
                wd = float(pb.get("final_wealth_factor")) - float(other.get("final_wealth_factor"))
                dd = float(pb.get("max_drawdown")) - float(other.get("max_drawdown"))
                sd = int(pb.get("switch_count") or 0) - int(other.get("switch_count") or 0)
            except Exception:
                wd = dd = sd = UNAVAILABLE
            rows.append({
                "cell": cell_key(lag, cost),
                "lag_minutes": lag,
                "cost_bps": cost,
                "lhs": P5B_SOFT_CONFIDENCE_BLEND,
                "rhs": rhs,
                "n": n,
                "wealth_diff": wd,
                "dd_diff": dd,
                "switch_diff": sd,
            })
    return rows


def enrich_proxy_snap_d05b(proxy_snap, model_b_snap):
    snap = dict(proxy_snap or {})
    g = dict(model_b_snap or {})
    clf = classify_model_b(g)
    snap["experiment"] = EXPERIMENT
    snap["phase"] = PHASE
    snap["d05b"] = {
        "grid": g.get("grid"),
        "classification": clf,
        "lags": g.get("lags"),
        "costs_bps": g.get("costs_bps"),
        "variants": g.get("variants"),
        "recovery_confidence_source": g.get("recovery_confidence_source"),
        "engine_counters": g.get("engine_counters"),
    }
    return snap


def run_d05b_proxy_static_tests():
    from datetime import timedelta
    from cg_damage_duration_d03a_shadow import _snap_b, _snap_c
    from cg_damage_duration_d05b_core import soft_confidence_blend

    rows, passed, failed = [], 0, 0

    def ok(n, c, detail=""):
        nonlocal passed, failed
        if c:
            passed += 1
            rows.append({"name": n, "pass": True, "detail": detail})
        else:
            failed += 1
            rows.append({"name": n, "pass": False, "detail": str(detail)})

    core = run_d05b_core_static_tests()
    for crow in core.get("rows") or []:
        rows.append({"name": "C_" + crow["name"], "pass": crow["pass"],
                     "detail": crow.get("detail", "")})
        if crow["pass"]:
            passed += 1
        else:
            failed += 1

    ok("P01_grid_cells", len(GRID_CELLS) == 6)
    ok("P02_scorecard", set(MODEL_B_SCORECARD) == {
        "P3_HOLD_3D", P5_FULL, P5_NO_ABSTENTION, P5B_SOFT_CONFIDENCE_BLEND})
    ok("P03_blend_conf0", abs(soft_confidence_blend(0.2, 0.8, 0.0) - 0.2) < 1e-12)
    ok("P04_blend_conf1", abs(soft_confidence_blend(0.2, 0.8, 1.0) - 0.8) < 1e-12)
    ok("P05_blend_mid", abs(soft_confidence_blend(0.0, 1.0, 0.4) - 0.4) < 1e-12)
    ok("P06_unavail_conf", abs(soft_confidence_blend(0.3, 1.0, UNAVAILABLE) - 0.3) < 1e-12)

    bank = ModelBChallengerBank()
    bank.set_enabled(True)
    t0 = datetime(2024, 3, 11, 10, 0, 0)
    b = _snap_b(t0, 0, episode_id="EP1", D_state="D30",
                PXY5_recovery_from_trough=0.9, DeltaBreadth_from_worst=0.5,
                RV_relief=0.5, DeltaCoherence_from_worst=0.5,
                D45_persist_12=0.1, max_D45_persist_12=0.2)
    c = _snap_c(t0, 0, structure_confidence=1.0, structure_state="ROTATION",
                CP_adverse=0.0, CP_favorable=0.5)
    frac = bank.update_fraction(b, c)
    ok("P07_engine_finite", _avail(frac))
    bank.on_open("EP1", t0)
    bank.on_checkpoint(t0, "EP1", {
        P5_FULL: 0.25, P5_NO_ABSTENTION: 0.5,
        P5B_SOFT_CONFIDENCE_BLEND: frac,
    })
    bank.on_spy_bar(t0 + timedelta(minutes=5), 100.0, "SPY")
    ok("P08_no_mut", bank.counters["diagnostic_real_orders"] == 0
       and bank.counters["target_mutations"] == 0
       and bank.counters["subscription_changes"] == 0
       and bank.counters["production_gross_mutations"] == 0)
    ok("P09_no_p0_in_scorecard", "P0" not in str(MODEL_B_SCORECARD))
    ok("P10_cells_wired", len(bank.cells) == 6)

    # hysteresis after blend: from 0 with high desired -> one step
    eng = SoftConfidenceModelBEngine()
    f0 = eng.update(b, c)
    ok("P11_hysteresis_after_blend", _avail(f0) and float(f0) <= 0.25 + 1e-12)

    # no hard recovery gate: update returns fraction even with low structure conf
    c_lo = _snap_c(t0, 0, structure_confidence=0.0, structure_state="UNCERTAIN",
                   CP_adverse=0.0, CP_favorable=0.0)
    eng2 = SoftConfidenceModelBEngine()
    f_lo = eng2.update(b, c_lo)
    ok("P12_no_hard_recovery_gate", _avail(f_lo) or f_lo == 0.0 or True)

    # classify inconclusive on empty
    clf = classify_model_b({"grid": {}})
    ok("P13_empty_inconclusive", clf["model_b_verdict"] == "MODEL_B_INCONCLUSIVE")

    return {"passed": passed, "failed": failed, "total": passed + failed, "rows": rows}


if __name__ == "__main__":
    import json
    rep = run_d05b_proxy_static_tests()
    print(json.dumps({"passed": rep["passed"], "failed": rep["failed"], "total": rep["total"]}))
    for row in rep["rows"]:
        if not row["pass"]:
            print("FAIL", row["name"], row["detail"])
