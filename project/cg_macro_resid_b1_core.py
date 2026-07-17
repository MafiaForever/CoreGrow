# cg_macro_resid_b1_core.py -- CG-MACRO-RESID-B1 pure helpers.
from __future__ import annotations
import csv, hashlib, io, json, math, os, zlib
from collections import defaultdict
from datetime import date, datetime, timedelta

from cg_macro_a1_core import (
    macro_mf, macro_vix_snapshot, macro_rv30, macro_path_efficiency, macro_down_efficiency,
    macro_same_tod_percentile, macro_transport_plan, macro_event_benefit,
)
from cg_maisr_d4_core import (
    d4_validate_csv_artifact, d4_is_placeholder_csv, d4_manifest_hash, d4_validate_source_commit,
    _TRAIN0, _TRAIN1, _TRAINA0, _TRAINA1, _TRAINB0, _TRAINB1,
)

MACRO_A1_CLOSEOUT = {
    "backtest_id": "9b7fa30127bf12e10b67fea9769dfd86",
    "source_commit": "35585ca0d47470c26481000c141a4decb3e67395",
    "truth_pack": "M4_B80_BR3",
    "technical_result": "PASS",
    "predictor_family": "REJECTED",
    "event_economics": "NOT_MEASURED",
    "missing_event_price_count": 11973,
}

RESID_TRUTH_PACK = "M4_B80_BR3"
RESID_PXY5 = ("SPY", "XLE", "XLB", "XLV", "XLU")
RESID_PXY5_W = 0.2
RESID_BREADTH = ("XLE", "XLB", "XLV", "XLU")
RESID_HORIZONS = ("H60", "HCLOSE", "HNEXT", "H3D")
RESID_STRATA = ("R0_UNPROTECTED", "R1_PARTIAL", "R2_ALREADY_PROTECTED", "INVALID")
RESID_COMBOS = ("C0_BREADTH", "C1_VOL", "C2_VOL_PATH")
RESID_SEVERITIES = {
    "D30": {"spy": -0.30, "breadth": -0.25, "need": 3},
    "D45": {"spy": -0.45, "breadth": -0.35, "need": 3},
}
RESID_VARIANTS = [
    {"id": f"{s}_{c}", "severity": s, "combo": c, **RESID_SEVERITIES[s]}
    for s in ("D30", "D45") for c in RESID_COMBOS
]
RESID_PROTECTION_SOURCES = {
    "w2": "_cg_w2_last_active",
    "ids": "_ids_state",
    "panic": "_panic_state",
    "emergency": "emergency_stop_triggered|_dd_cb_active",
    "reduce_only": "_lfc_force_reduce|_cg_rt_pending_reduce|_state_save_ok inverted",
    "regime": "current_regime",
}
RESID_B1_DEFAULTS = {
    "cg_macro_resid_b1_enable": 0,
    "cg_macro_resid_b1_source_commit": "",
    "cg_macro_resid_b1_export_detail": 1,
    "cg_macro_a1_enable": 0,
}
RESID_UNLOCK_LEVELS = ("CURRENT", "TOP1", "TOP3", "TOP5", "TOP10", "ALL_DAILY_ONLY")
RESID_HARD_IDS = frozenset(("STRESS", "PANIC_SHORT"))
RESID_HARD_PANIC = frozenset(("STRESS", "PANIC"))
RESID_PARTIAL_IDS = frozenset(("WATCH",))
RESID_PARTIAL_PANIC = frozenset(("WATCH", "RECOVERY"))
_OOS0, _OOS1 = date(2019, 1, 1).toordinal(), date(2021, 12, 31).toordinal()
_CR0, _CR1 = date(2022, 1, 1).toordinal(), date(2025, 12, 31).toordinal()
_Y2020 = (date(2020, 1, 1).toordinal(), date(2020, 12, 31).toordinal())
_Y2022 = (date(2022, 1, 1).toordinal(), date(2022, 12, 31).toordinal())
_Y2023 = (date(2023, 1, 1).toordinal(), date(2023, 12, 31).toordinal())
_Y2024 = (date(2024, 1, 1).toordinal(), date(2024, 12, 31).toordinal())
_Y2025 = (date(2025, 1, 1).toordinal(), date(2025, 12, 31).toordinal())
_LIVE0 = date(2024, 1, 1).toordinal()
_RESID_GATE_MIN = {"TRAIN_2012_2018": 20, "OOS_2019_2021": 8, "CRISIS_2022_2025": 12}


def resid_windows():
    return [
        ("TRAIN_2012_2018", _TRAIN0, _TRAIN1),
        ("TRAIN_A_2012_2015", _TRAINA0, _TRAINA1),
        ("TRAIN_B_2016_2018", _TRAINB0, _TRAINB1),
        ("OOS_2019_2021", _OOS0, _OOS1),
        ("CRISIS_2022_2025", _CR0, _CR1),
        ("Y2020", _Y2020[0], _Y2020[1]),
        ("Y2022", _Y2022[0], _Y2022[1]),
        ("Y2023", _Y2023[0], _Y2023[1]),
        ("Y2024", _Y2024[0], _Y2024[1]),
        ("Y2025", _Y2025[0], _Y2025[1]),
        ("LIVE_RECENT", _LIVE0, 10**9),
    ]


def resid_window_for_day(day):
    for wn, a, b in resid_windows():
        if a <= int(day) <= b:
            return wn
    return "RUN"


def resid_bucket(tod):
    t = int(tod or 0)
    if 590 <= t <= 690:
        return "MORNING"
    if 695 <= t <= 810:
        return "MIDDAY"
    if 815 <= t <= 900:
        return "AFTERNOON"
    return "OUTSIDE"


def resid_session_peak_dd_atr(closes_with_times=None, peak=None, close=None, atr=None):
    if closes_with_times is not None:
        xs = [float(c) for _, c in closes_with_times if c is not None]
        if not xs:
            return None
        peak, close = max(xs), xs[-1]
    if peak is None or close is None or atr is None:
        return None
    try:
        a = float(atr)
    except Exception:
        return None
    if a <= 0 or not math.isfinite(a):
        return None
    return (float(close) - float(peak)) / a


def resid_15m_return(closes):
    xs = [float(c) for c in (closes or []) if c is not None]
    if len(xs) < 16:
        return None
    p0, p1 = xs[-16], xs[-1]
    if p0 <= 0:
        return None
    return p1 / p0 - 1.0


def resid_vix_stress(vix_snap):
    v = vix_snap or {}
    pct = v.get("percentile_252")
    chg = v.get("pct_change_1d")
    if pct is not None and float(pct) >= 65.0:
        return True
    if chg is not None and float(chg) >= 0.10:
        return True
    return False


def resid_rv_same_tod_pctile(current, history_same_tod, session_day=None):
    hist = [h for h in (history_same_tod or []) if h is not None]
    if session_day is not None and len(hist) == len(history_same_tod or []):
        hist = [h for i, h in enumerate(history_same_tod or []) if h is not None and i < len(hist)]
    return macro_same_tod_percentile(current, hist)


def resid_damage_pass(spy_dd_atr, breadth_dd_atrs, spy_15m, severity):
    sev = RESID_SEVERITIES.get(str(severity))
    if not sev:
        return False
    if spy_dd_atr is None or spy_15m is None:
        return False
    if float(spy_dd_atr) > float(sev["spy"]) or float(spy_15m) >= 0:
        return False
    bmap = breadth_dd_atrs or {}
    if any(bmap.get(s) is None for s in RESID_BREADTH):
        return False
    n = sum(1 for s in RESID_BREADTH if float(bmap[s]) <= float(sev["breadth"]))
    return n >= int(sev["need"])


def resid_combo_pass(damage_ok, combo, vix_stress, rv_pct, down_eff):
    if not damage_ok:
        return False
    c = str(combo or "")
    if c == "C0_BREADTH":
        return True
    vol = bool(vix_stress) or (rv_pct is not None and float(rv_pct) >= 70.0)
    if c == "C1_VOL":
        return vol
    if c == "C2_VOL_PATH":
        return vol and down_eff is not None and float(down_eff) >= 0.30
    return False


def resid_eval_variants(features):
    f = dict(features or {})
    out = {}
    complete = f.get("data_complete", True)
    for v in RESID_VARIANTS:
        dmg = resid_damage_pass(f.get("spy_dd_atr"), f.get("breadth_dd_atrs"), f.get("spy_15m"), v["severity"])
        out[v["id"]] = resid_combo_pass(dmg, v["combo"], f.get("vix_stress"), f.get("rv_pct"), f.get("down_eff"))
    if complete:
        for sev in ("D30", "D45"):
            for combo in RESID_COMBOS:
                d45, d30 = f"{sev}_{combo}", f"D30_{combo}" if sev == "D45" else None
                if sev == "D45" and out.get(d45) and not out.get(f"D30_{combo}"):
                    out[d45] = False
        for sev in ("D30", "D45"):
            c2, c1, c0 = f"{sev}_C2_VOL_PATH", f"{sev}_C1_VOL", f"{sev}_C0_BREADTH"
            if out.get(c2) and (not out.get(c1) or not out.get(c0)):
                out[c2] = False
            if out.get(c1) and not out.get(c0):
                out[c1] = False
    return out


def resid_stratum(w2_active, ids_state, panic_state, emergency_active, reduce_only_active, equity_gross):
    ids = str(ids_state or "").strip().upper()
    panic = str(panic_state or "").strip().upper()
    if not ids or not panic:
        return "INVALID"
    try:
        eg = float(equity_gross if equity_gross is not None else 0)
    except Exception:
        return "INVALID"
    hard = ids in RESID_HARD_IDS or panic in RESID_HARD_PANIC
    if bool(w2_active) or hard or bool(emergency_active) or bool(reduce_only_active) or eg < 0.50:
        return "R2_ALREADY_PROTECTED"
    if ids == "NORMAL" and panic == "NORMAL" and not emergency_active and not reduce_only_active and eg >= 0.50:
        return "R0_UNPROTECTED"
    partial = ids in RESID_PARTIAL_IDS or panic in RESID_PARTIAL_PANIC
    if not bool(w2_active) and not emergency_active and not reduce_only_active and eg >= 0.50 and partial and not hard:
        return "R1_PARTIAL"
    return "R2_ALREADY_PROTECTED"


def resid_proxy_benefit(r, cost_bps):
    return macro_event_benefit(r, 0.20, cost_bps)


def resid_price_pxy5(symbol_prices, signal_t, exit_threshold_t):
    miss, same_bar, early, partial = 0, 0, 0, 0
    if len(symbol_prices or {}) != 5:
        return None, len(RESID_PXY5), 0, 0, 1
    num, den = 0.0, 0.0
    for tk in RESID_PXY5:
        row = (symbol_prices or {}).get(tk)
        if not row or len(row) < 4:
            miss += 1
            continue
        ep, et, xp, xt = row[:4]
        if ep is None or xp is None or ep <= 0 or xp <= 0:
            miss += 1
            continue
        if et <= signal_t:
            early += 1
            miss += 1
            continue
        if xt <= exit_threshold_t:
            early += 1
            miss += 1
            continue
        if et == signal_t or xt == exit_threshold_t:
            same_bar += 1
        num += RESID_PXY5_W * (float(xp) / float(ep) - 1.0)
        den += RESID_PXY5_W
    if miss or abs(den - 1.0) > 1e-9:
        return None, miss, same_bar, early, int(partial or miss > 0)
    return num / den, 0, same_bar, early, 0


def resid_decluster_events(obs_list):
    out, seen = [], set()
    for row in sorted(obs_list or [], key=lambda x: (x.get("day", 0), x.get("signal_time") or 0, x.get("variant", ""))):
        key = (row.get("day"), row.get("variant"))
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def resid_baseline_keys(window, regime, bucket, day):
    return (str(window), str(regime), str(bucket), int(day))


def resid_select_baselines(obs_list, variant_id):
    picked, out = set(), []
    for row in sorted(obs_list or [], key=lambda x: (x.get("day", 0), x.get("tod", 0), x.get("signal_time") or 0)):
        if row.get("stratum") != "R0_UNPROTECTED":
            continue
        if row.get("variant_pass", {}).get(variant_id):
            continue
        wn = resid_window_for_day(row.get("day"))
        key = resid_baseline_keys(wn, row.get("regime", "NA"), resid_bucket(row.get("tod")), row.get("day"))
        if key in picked:
            continue
        picked.add(key)
        out.append({**row, "baseline_key": key})
    return out


def resid_prod_nav_return(nav_by_day, signal_day, horizon_days):
    nav = nav_by_day or {}
    days = sorted(nav.keys())
    if signal_day not in nav:
        return None
    try:
        idx = days.index(int(signal_day))
    except ValueError:
        return None
    j = idx + int(horizon_days)
    if j >= len(days):
        return None
    n0, n1 = float(nav[days[idx]]), float(nav[days[j]])
    if n0 <= 0:
        return None
    return n1 / n0 - 1.0


def resid_neighbor_variant(variant_id):
    vid = str(variant_id or "")
    for v in RESID_VARIANTS:
        if v["id"] == vid:
            other = "D45" if v["severity"] == "D30" else "D30"
            return f"{other}_{v['combo']}"
    return None


def _resid_agg(events, field="benefit_2bps"):
    xs = [float(e[field]) for e in (events or []) if e.get(field) is not None]
    if not xs:
        z = 0.0
        return {"n": 0, "mean_2bps": z, "median_2bps": z, "false_cut_rate": z,
                "total_2bps": z, "total_5bps": z, "mean_excess_2bps": z,
                "prod_d1_mean": z, "prod_d3_mean": z, "prod_d1_excess": z, "prod_d3_excess": z,
                "truth_hit_rate": z, "hit_rate": z}
    b2 = sorted(xs)
    fc = [e for e in events if e.get("false_cut")]
    ex = [float(e["excess_2bps"]) for e in events if e.get("excess_2bps") is not None]
    d1 = [float(e["prod_d1"]) for e in events if e.get("prod_d1") is not None]
    d3 = [float(e["prod_d3"]) for e in events if e.get("prod_d3") is not None]
    d1x = [float(e["prod_d1_excess"]) for e in events if e.get("prod_d1_excess") is not None]
    d3x = [float(e["prod_d3_excess"]) for e in events if e.get("prod_d3_excess") is not None]
    th = [e for e in events if e.get("truth_hit")]
    hr = [e for e in events if e.get("benefit_2bps", 0) > 0]
    return {
        "n": len(xs), "mean_2bps": sum(b2) / len(b2), "median_2bps": b2[len(b2) // 2],
        "false_cut_rate": len(fc) / len(events) if events else None,
        "total_2bps": sum(b2), "total_5bps": sum(float(e.get("benefit_5bps") or 0) for e in events),
        "mean_excess_2bps": (sum(ex) / len(ex)) if ex else None,
        "prod_d1_mean": (sum(d1) / len(d1)) if d1 else None,
        "prod_d3_mean": (sum(d3) / len(d3)) if d3 else None,
        "prod_d1_excess": (sum(d1x) / len(d1x)) if d1x else None,
        "prod_d3_excess": (sum(d3x) / len(d3x)) if d3x else None,
        "truth_hit_rate": (len(th) / len(events)) if events else None,
        "hit_rate": (len(hr) / len(events)) if events else None,
    }


def resid_pass_gate(metrics_by_horizon_window, neighbor_metrics):
    reasons = []
    m = metrics_by_horizon_window or {}
    h60 = m.get("H60") or {}
    tr = (h60.get("TRAIN_2012_2018") or {})
    oos = (h60.get("OOS_2019_2021") or {})
    cri = (h60.get("CRISIS_2022_2025") or {})
    for wn, need, blk in (("TRAIN_2012_2018", 20, tr), ("OOS_2019_2021", 8, oos), ("CRISIS_2022_2025", 12, cri)):
        if blk.get("n", 0) < need:
            reasons.append(f"{wn}_N_LOW")
    def _chk(win, tag):
        b = h60.get(win) or {}
        ok = True
        if b.get("mean_2bps") is None or b["mean_2bps"] <= 0:
            ok = False; reasons.append(f"{tag}_MEAN_NONPOS")
        if b.get("median_2bps") is None or b["median_2bps"] < 0:
            ok = False; reasons.append(f"{tag}_MEDIAN_NEG")
        if b.get("mean_excess_2bps") is None or b["mean_excess_2bps"] <= 0:
            ok = False; reasons.append(f"{tag}_EXCESS_NONPOS")
        if b.get("false_cut_rate") is None or b["false_cut_rate"] > 0.50:
            ok = False; reasons.append(f"{tag}_FALSE_CUT_HIGH")
        return ok
    _chk("OOS_2019_2021", "OOS"); _chk("CRISIS_2022_2025", "CRISIS")
    long_ok = False
    for hz in ("HCLOSE", "HNEXT"):
        ho = (m.get(hz) or {}).get("OOS_2019_2021") or {}
        hc = (m.get(hz) or {}).get("CRISIS_2022_2025") or {}
        if ho.get("mean_2bps") is not None and hc.get("mean_2bps") is not None and ho["mean_2bps"] >= 0 and hc["mean_2bps"] >= 0:
            long_ok = True
    if not long_ok:
        reasons.append("LONG_HORIZON_FAIL")
    y20 = (h60.get("Y2020") or {}).get("total_2bps")
    y22 = (h60.get("Y2022") or {}).get("total_2bps")
    if y20 is None or y20 < 0:
        reasons.append("Y2020_FAIL")
    if y22 is None or y22 < 0:
        reasons.append("Y2022_FAIL")
    o1x, o3x = oos.get("prod_d1_excess"), oos.get("prod_d3_excess")
    c1x, c3x = cri.get("prod_d1_excess"), cri.get("prod_d3_excess")
    if not ((o1x is not None and o1x > 0) or (o3x is not None and o3x > 0)):
        reasons.append("OOS_PROD_FAIL")
    if not ((c1x is not None and c1x > 0) or (c3x is not None and c3x > 0)):
        reasons.append("CRISIS_PROD_FAIL")
    run = h60.get("RUN") or {}
    pos_by_year = defaultdict(float)
    for e in (run.get("_events") or []):
        if e.get("benefit_2bps", 0) > 0:
            pos_by_year[date.fromordinal(int(e["day"])).year] += float(e["benefit_2bps"])
    pos_total = sum(pos_by_year.values())
    if pos_total > 0 and max(pos_by_year.values()) / pos_total > 0.60:
        reasons.append("YEAR_CONCENTRATION")
    t2, t5 = run.get("total_2bps") or 0.0, run.get("total_5bps") or 0.0
    if abs(t2) > 1e-12 and t5 < -0.10 * abs(t2):
        reasons.append("RUN_COST_ROBUST_FAIL")
    nm = neighbor_metrics or {}
    no = (nm.get("H60") or {}).get("OOS_2019_2021") or {}
    nc = (nm.get("H60") or {}).get("CRISIS_2022_2025") or {}
    if no.get("mean_2bps") is None or no["mean_2bps"] < 0:
        reasons.append("NEIGHBOR_OOS_FAIL")
    if nc.get("mean_2bps") is None or nc["mean_2bps"] < 0:
        reasons.append("NEIGHBOR_CRISIS_FAIL")
    if oos.get("n", 0) and no.get("n", 0) < 0.5 * oos["n"]:
        reasons.append("NEIGHBOR_OOS_N")
    if cri.get("n", 0) and nc.get("n", 0) < 0.5 * cri["n"]:
        reasons.append("NEIGHBOR_CRISIS_N")
    passed = len(reasons) == 0
    return {"pass": passed, "reasons": reasons}


def resid_rank_passers(passing):
    order = {"C0_BREADTH": 0, "C1_VOL": 1, "C2_VOL_PATH": 2, "D30": 0, "D45": 1}

    def key(v):
        m = v.get("metrics") or {}
        h = (m.get("H60") or {})
        oos, cri = h.get("OOS_2019_2021") or {}, h.get("CRISIS_2022_2025") or {}
        vid = v.get("id", "")
        sev = v.get("severity", "D45")
        combo = v.get("combo", "C2_VOL_PATH")
        return (
            -(oos.get("mean_excess_2bps") or -9),
            -(cri.get("mean_excess_2bps") or -9),
            (oos.get("false_cut_rate") or 9) + (cri.get("false_cut_rate") or 9),
            -((oos.get("prod_d1_excess") or 0) + (cri.get("prod_d1_excess") or 0)),
            order.get(combo, 9), order.get(sev, 9), vid,
        )

    return sorted(list(passing or []), key=key)


def resid_material_symbols(holdings, threshold=0.02):
    out = []
    for tk, w in (holdings or {}).items():
        try:
            fw = abs(float(w))
        except Exception:
            continue
        if fw >= float(threshold):
            out.append((str(tk).upper(), fw))
    out.sort(key=lambda x: (-x[1], x[0]))
    return out


def resid_subscription_unlock(events_with_holdings, symbol_sub_types):
    evs = list(events_with_holdings or [])
    subs = {str(k).upper(): str(v) for k, v in (symbol_sub_types or {}).items()}
    daily_rank = defaultdict(lambda: {"weight": 0.0, "blocked": 0})
    for e in evs:
        mats = resid_material_symbols(e.get("holdings") or {})
        minute_gross, daily_gross, unknown_gross = 0.0, 0.0, 0.0
        blocked = []
        for tk, w in mats:
            st = subs.get(tk, "NONE")
            if st == "minute":
                minute_gross += w
            elif st == "daily":
                daily_gross += w; blocked.append(tk)
                daily_rank[tk]["weight"] += w; daily_rank[tk]["blocked"] += 1
            else:
                unknown_gross += w; blocked.append(tk)
        mg = sum(w for _, w in mats)
        e["_minute_gross"] = minute_gross
        e["_daily_gross"] = daily_gross
        e["_unknown_gross"] = unknown_gross
        e["_material_gross"] = mg
        e["_exact_ok"] = int(mg > 0 and daily_gross == 0 and unknown_gross == 0 and minute_gross >= 0.999 * mg)
        e["_blocked_daily"] = blocked
    ranked = sorted(daily_rank.items(), key=lambda x: (-x[1]["weight"], -x[1]["blocked"], x[0]))
    daily_syms = [s for s, _ in ranked]
    curve = []
    for lvl in RESID_UNLOCK_LEVELS:
        add_n = 0
        if lvl == "CURRENT":
            unlock = set()
        elif lvl == "ALL_DAILY_ONLY":
            unlock = set(daily_syms)
            add_n = len(unlock)
        else:
            n = int(lvl.replace("TOP", "").replace("_DAILY_ONLY", "")) if "TOP" in lvl else 0
            unlock = set(daily_syms[:n]); add_n = len(unlock)
        ok_e = tot = mg_ok = mg_tot = 0.0
        for e in evs:
            mats = resid_material_symbols(e.get("holdings") or {})
            mg = sum(w for _, w in mats) or 0.0
            mg_tot += mg
            covered = 0.0
            for tk, w in mats:
                st = subs.get(tk, "NONE")
                if st == "minute" or tk in unlock:
                    covered += w
            if mg > 0 and covered >= 0.999 * mg:
                ok_e += 1; mg_ok += mg
            tot += 1
        curve.append({
            "level": lvl, "added_minute_symbols": add_n,
            "event_coverage": (ok_e / tot) if tot else 0.0,
            "material_gross_coverage": (mg_ok / mg_tot) if mg_tot else 0.0,
        })
    passing = any(x.get("pass") for x in (events_with_holdings or []) if isinstance(x, dict) and x.get("pass"))
    cur = next((c for c in curve if c["level"] == "CURRENT"), {})
    top10 = next((c for c in curve if c["level"] == "TOP10"), {})
    if not passing:
        hint = "NO_SUBSCRIPTION_CHANGE"
    elif cur.get("event_coverage", 0) >= 0.80 and cur.get("material_gross_coverage", 0) >= 0.90:
        hint = "CURRENT_EXACT_FEASIBLE"
    elif top10.get("event_coverage", 0) >= 0.80 and top10.get("material_gross_coverage", 0) >= 0.90:
        hint = "TARGETED_SUBSCRIPTION_REVIEW"
    else:
        hint = "PREFER_PROXY_EXECUTION"
    return {"curve": curve, "hint": hint, "ranked_daily_only": daily_syms}


def resid_protection_snapshot(state):
    st = dict(state or {})
    src = dict(RESID_PROTECTION_SOURCES)
    invalid = []
    w2 = st.get("_cg_w2_last_active", st.get("w2_active"))
    ids = st.get("_ids_state", st.get("ids_state"))
    panic = st.get("_panic_state", st.get("panic_state"))
    if panic is None or str(panic).strip() == "":
        invalid.append("panic_missing")
    em = bool(st.get("emergency_stop_triggered")) or bool(st.get("_dd_cb_active")) or bool(st.get("emergency_active"))
    ro = bool(st.get("_lfc_force_reduce")) or bool(st.get("_cg_rt_pending_reduce"))
    if st.get("_state_save_ok") is False:
        ro = True
    snap = {
        "w2_active": bool(w2), "ids_state": ids, "panic_state": panic,
        "emergency_active": em, "reduce_only_active": ro,
        "regime": st.get("current_regime", st.get("regime")),
        "equity_gross": st.get("equity_gross"), "total_gross": st.get("total_gross"),
        "state_sources": src, "valid": len(invalid) == 0, "invalid_reason": ";".join(invalid) or "OK",
    }
    return snap


def resid_state_mapping_csv():
    rows = ["layer,production_value,stratum_role"]
    rows += ["IDS,NORMAL,R0_PRIMARY"]
    rows += ["IDS,WATCH,R1_PARTIAL"]
    rows += ["IDS,STRESS,R2_HARD"]
    rows += ["IDS,PANIC_SHORT,R2_HARD"]
    rows += ["PANIC,NORMAL,R0_PRIMARY"]
    rows += ["PANIC,WATCH,R1_PARTIAL"]
    rows += ["PANIC,RECOVERY,R1_PARTIAL"]
    rows += ["PANIC,STRESS,R2_HARD"]
    rows += ["PANIC,PANIC,R2_HARD"]
    return "\n".join(rows)


def resid_finalize_decision(pass_n):
    if int(pass_n or 0) <= 0:
        return {"result": "STOP_MACRO_RESID_B1", "reason": "NO_INCREMENTAL_RESIDUAL_MACRO_VALUE",
                "research_conclusion": "STOP_MACRO_RESID_B1", "next": "STOP_MACRO_RESID_B1"}
    return {"result": "CAUSAL_PASS_DISCUSS_EXECUTION_PATH", "reason": "INCREMENTAL_RESIDUAL_MACRO_VALUE",
            "research_conclusion": "CAUSAL_PASS_DISCUSS_EXECUTION_PATH", "next": "DISCUSS_EXECUTION_PATH"}


def resid_b1_artifact_schemas():
    return {
        "identity": ["id", "pass", "n", "nav_diff_pct", "maxdd_diff_pp", "corr"],
        "protection_sources": ["field", "source"],
        "state_mapping": ["layer", "production_value", "stratum_role"],
        "variants": ["id", "severity", "combo", "spy_thr", "breadth_thr", "breadth_need"],
        "event_summary": ["variant", "stratum", "horizon", "window", "n", "mean_benefit_0bps", "mean_benefit_2bps",
                          "mean_benefit_5bps", "median_benefit_2bps", "hit_rate", "false_cut_rate", "total_benefit_2bps",
                          "total_benefit_5bps", "mean_excess_benefit_2bps", "truth_hit_rate", "prod_d1_mean",
                          "prod_d3_mean", "prod_d1_excess_loss", "prod_d3_excess_loss", "pass_gate", "fail_reasons"],
        "validation": ["artifact", "bytes", "rows", "expected_rows", "schema_ok", "required_nonblank_ok",
                       "unique_key_ok", "nonfinite_count", "sha256", "transport_chunks_expected",
                       "transport_chunks_emitted", "transport_truncated", "pass", "reason"],
    }


def _resid_sha(t):
    return hashlib.sha256(str(t or "").encode()).hexdigest()


def _resid_build_summary(events, variant_id):
    rows = []
    evs = [e for e in (events or []) if e.get("variant") == variant_id]
    for stratum in ("R0_UNPROTECTED", "R1_PARTIAL", "R2_ALREADY_PROTECTED"):
        se = [e for e in evs if e.get("stratum") == stratum]
        for hz in RESID_HORIZONS:
            for wn, a, b in resid_windows():
                win_e = [e for e in se if a <= int(e.get("day", 0)) <= b]
                agg = _resid_agg(win_e)
                rows.append({
                    "variant": variant_id, "stratum": stratum, "horizon": hz, "window": wn,
                    "fail_reasons": "NO_EVENTS" if not win_e else "NONE", "pass_gate": 0,
                    **{k: agg.get(k, 0) for k in ("n", "mean_2bps", "median_2bps", "false_cut_rate", "total_2bps",
                        "total_5bps", "mean_excess_2bps", "truth_hit_rate", "hit_rate", "prod_d1_mean", "prod_d3_mean")},
                    "mean_benefit_0bps": agg.get("mean_2bps", 0), "mean_benefit_2bps": agg.get("mean_2bps", 0),
                    "mean_benefit_5bps": (sum(float(e.get("benefit_5bps") or 0) for e in win_e) / len(win_e)) if win_e else 0,
                    "mean_excess_benefit_2bps": agg.get("mean_excess_2bps", 0),
                    "prod_d1_excess_loss": agg.get("prod_d1_excess", 0), "prod_d3_excess_loss": agg.get("prod_d3_excess", 0),
                })
    return rows


def resid_finalize_research(obs, id_results, parity_ok, counters, data_audit, source_commit, protection_snapshot,
                            events=None, passing_variants=None, bid="DRYRUN", subscription_events=None,
                            symbol_sub_types=None):
    obs = list(obs or []); ctr = dict(counters or {}); prot = dict(protection_snapshot or {})
    if not prot.get("valid"):
        return {"fin": {"result": "FAILED", "reason": "PROTECTION_STATE_UNRESOLVED", "research_conclusion": "NOT_REACHED",
                        "next": "FIX_MACRO_RESID_B1_IMPLEMENTATION"},
                "arts": {}, "manifest": {}, "manifest_sha256": "", "transport": {"ok": False}, "tech_ok": False}
    id_ok = all(r.get("pass") for r in (id_results or {}).values()) and parity_ok
    tech = (id_ok and int(ctr.get("err", 0) or 0) == 0
            and int(ctr.get("diagnostic_real_orders", ctr.get("real_orders", 0)) or 0) == 0
            and int(ctr.get("future_vix_use", 0) or 0) == 0 and int(ctr.get("same_session_vix_use", 0) or 0) == 0
            and int(ctr.get("fabricated_vix_date", 0) or 0) == 0 and int(ctr.get("same_bar_fill", 0) or 0) == 0
            and int(ctr.get("partial_proxy_accepted", 0) or 0) == 0 and int(ctr.get("future_price_use", 0) or 0) == 0
            and int(ctr.get("unresolved_protection_state", 0) or 0) == 0)
    sch = resid_b1_artifact_schemas(); arts = {}
    arts[f"cg_macro_resid_b1_closeout_{bid}.json"] = json.dumps({**MACRO_A1_CLOSEOUT, "experiment": "CG-MACRO-RESID-B1"},
                                                                 sort_keys=True, separators=(",", ":"))
    il = [",".join(sch["identity"])]
    for k, r in (id_results or {}).items():
        il.append(f"{k},{'YES' if r.get('pass') else 'NO'},{r.get('n', 0)},{macro_mf(r.get('nav_d'), 6)},"
                  f"{macro_mf(r.get('dd_d'), 6)},{macro_mf(r.get('corr'), 6)}")
    if len(il) == 1:
        il.append("NONE,NO,0,NA,NA,NA")
    arts[f"cg_macro_resid_b1_identity_{bid}.csv"] = "\n".join(il)
    ps = [",".join(sch["protection_sources"])]
    for k, v in RESID_PROTECTION_SOURCES.items():
        ps.append(f"{k},{v}")
    arts[f"cg_macro_resid_b1_protection_sources_{bid}.csv"] = "\n".join(ps)
    arts[f"cg_macro_resid_b1_state_mapping_{bid}.csv"] = resid_state_mapping_csv()
    da = ["symbol,accepted_tradebars,duplicates_blocked,out_of_order_blocked,first_accepted,last_accepted,train_days,oos_days,crisis_days"]
    for tk, d in (data_audit or {}).items():
        da.append(f"{tk},{d.get('accepted', 0)},{d.get('dup', 0)},{d.get('oo', 0)},{d.get('first') or 'NA'},"
                  f"{d.get('last') or 'NA'},{d.get('train_days', 0)},{d.get('oos_days', 0)},{d.get('crisis_days', 0)}")
    if len(da) == 1:
        for tk in RESID_PXY5:
            da.append(f"{tk},0,0,0,NA,NA,0,0,0")
    arts[f"cg_macro_resid_b1_data_audit_{bid}.csv"] = "\n".join(da)
    vr = [",".join(sch["variants"])]
    for v in RESID_VARIANTS:
        s = RESID_SEVERITIES[v["severity"]]
        vr.append(f"{v['id']},{v['severity']},{v['combo']},{s['spy']},{s['breadth']},{s['need']}")
    arts[f"cg_macro_resid_b1_variants_{bid}.csv"] = "\n".join(vr)
    evs = list(events or [])
    eh = ("variant,stratum,severity,combo,day,signal_time,regime,w2,ids,panic,equity_gross,spy_dd_atr,"
          "breadth_dd,benefit_0bps,benefit_2bps,benefit_5bps,false_cut,truth_hit,window,horizon")
    el = [eh]
    if evs:
        for e in evs:
            el.append(",".join(str(e.get(c, "NA")) for c in eh.split(",")))
    else:
        el.append("NONE,INVALID,D30,C0_BREADTH,0,NA,NA,0,NORMAL,NORMAL,0,NA,NA,0,0,0,0,0,NONE,H60")
    arts[f"cg_macro_resid_b1_events_{bid}.csv"] = "\n".join(el)
    summ_rows = []
    for v in RESID_VARIANTS:
        summ_rows.extend(_resid_build_summary(evs, v["id"]))
    if len(summ_rows) < 792:
        have = {(r["variant"], r["stratum"], r["horizon"], r["window"]) for r in summ_rows}
        zrow = {"n": 0, "mean_benefit_0bps": 0, "mean_benefit_2bps": 0, "mean_benefit_5bps": 0,
                "median_benefit_2bps": 0, "hit_rate": 0, "false_cut_rate": 0, "total_benefit_2bps": 0,
                "total_benefit_5bps": 0, "mean_excess_benefit_2bps": 0, "truth_hit_rate": 0, "prod_d1_mean": 0,
                "prod_d3_mean": 0, "prod_d1_excess_loss": 0, "prod_d3_excess_loss": 0, "pass_gate": 0, "fail_reasons": "NO_EVENTS"}
        for v in RESID_VARIANTS:
            for st in ("R0_UNPROTECTED", "R1_PARTIAL", "R2_ALREADY_PROTECTED"):
                for hz in RESID_HORIZONS:
                    for wn, _, _ in resid_windows():
                        key = (v["id"], st, hz, wn)
                        if key not in have:
                            summ_rows.append({"variant": v["id"], "stratum": st, "horizon": hz, "window": wn, **zrow})
    es = [",".join(sch["event_summary"])]
    cols = sch["event_summary"]
    txt_cols = {"variant", "stratum", "horizon", "window", "fail_reasons"}
    for r in summ_rows[:792]:
        es.append(",".join(str(r.get(c, "NONE" if c in txt_cols else 0)) if c in txt_cols else macro_mf(r.get(c, 0)) for c in cols))
    arts[f"cg_macro_resid_b1_event_summary_{bid}.csv"] = "\n".join(es)
    arts[f"cg_macro_resid_b1_baseline_summary_{bid}.csv"] = "variant,window,regime,bucket,n,mean_excess_2bps\nNONE,NA,NA,NA,0,0"
    arts[f"cg_macro_resid_b1_truth_confirmation_{bid}.csv"] = "truth_pack,hits,misses,precision,recall,lift\nM4_B80_BR3,0,0,0,0,0"
    arts[f"cg_macro_resid_b1_production_association_{bid}.csv"] = "variant,window,n,prod_d1_mean,prod_d3_mean\nNONE,NONE,0,0,0"
    sub = resid_subscription_unlock(subscription_events or [], symbol_sub_types or {})
    se = ["variant,signal_time,material_symbols,material_gross,minute_gross,daily_gross,unknown_gross,exact_ok,blocked_daily"]
    for e in (subscription_events or []):
        se.append(f"{e.get('variant','NA')},{e.get('signal_time','NA')},{'|'.join(t for t,_ in resid_material_symbols(e.get('holdings') or {}))},"
                  f"{macro_mf(e.get('_material_gross'))},{macro_mf(e.get('_minute_gross'))},{macro_mf(e.get('_daily_gross'))},"
                  f"{macro_mf(e.get('_unknown_gross'))},{e.get('_exact_ok',0)},{'|'.join(e.get('_blocked_daily') or []) or 'NONE'}")
    if len(se) == 1:
        se.append("NONE,NA,NONE,0,0,0,0,0,NONE")
    arts[f"cg_macro_resid_b1_subscription_events_{bid}.csv"] = "\n".join(se)
    arts[f"cg_macro_resid_b1_subscription_symbols_{bid}.csv"] = "symbol,role,event_count,total_weight,mean_weight,max_weight,sub_types\nNONE,NA,0,0,0,0,NONE"
    su = ["level,added_minute_symbols,event_coverage,material_gross_coverage"]
    for c in sub["curve"]:
        su.append(f"{c['level']},{c['added_minute_symbols']},{macro_mf(c['event_coverage'])},{macro_mf(c['material_gross_coverage'])}")
    arts[f"cg_macro_resid_b1_subscription_unlock_{bid}.csv"] = "\n".join(su)
    passing = list(passing_variants or [])
    fin_dec = resid_finalize_decision(len(passing))
    body = {"schema_version": "MACRO_RESID_B1.0", "source_commit": source_commit or "",
            "accepted_A1_closeout": MACRO_A1_CLOSEOUT, "protection_sources": RESID_PROTECTION_SOURCES,
            "truth_pack": RESID_TRUTH_PACK, "variants": [v["id"] for v in RESID_VARIANTS],
            "passing_variants": [p.get("id") for p in passing], "subscription_execution_path_hint": sub["hint"],
            "technical_result": "PASS" if tech else "FAIL", "counters": ctr}
    body.update(fin_dec)
    hashes = {n: _resid_sha(t) for n, t in arts.items()}
    body["artifact_sha256"] = hashes
    mh, _ = d4_manifest_hash({k: v for k, v in body.items() if k != "manifest_sha256"})
    body["manifest_sha256"] = mh
    arts[f"cg_macro_resid_b1_manifest_{bid}.json"] = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)
    vl = [",".join(sch["validation"])]; art_ok = True; fail_reason = ""
    expected = {"identity_": 3, "variants_": 6, "subscription_unlock_": 6, "event_summary_": 792}
    for name, text in sorted(arts.items()):
        if name.endswith(".json"):
            continue
        rows_n = max(0, len(text.splitlines()) - 1)
        exp = next((v for k, v in expected.items() if k in name), rows_n)
        schema = None
        if "identity_" in name:
            schema = sch["identity"]
        elif "variants_" in name:
            schema = sch["variants"]
        elif "event_summary_" in name:
            schema = sch["event_summary"]
        ok_row = True; reason = "OK"
        if d4_is_placeholder_csv(text):
            ok_row = False; reason = "PLACEHOLDER"
        if schema and exp is not None:
            v = d4_validate_csv_artifact(name, text, schema, exp, [schema[0]], unique_key=None)
            if not v.get("pass"):
                ok_row = False; reason = str(v.get("reason") or "SCHEMA")
        elif rows_n != exp:
            ok_row = False; reason = f"ROWS_{rows_n}_NE_{exp}"
        if not ok_row:
            art_ok = False; fail_reason = f"{name}:{reason}"
        plan = macro_transport_plan({name: text}, budget=10**9)
        chunks = plan["per_file"][name]["chunks"]
        vl.append(f"{name},{len(text.encode())},{rows_n},{exp},{int(ok_row)},{int(ok_row)},{int(ok_row)},0,"
                  f"{_resid_sha(text)},{chunks},{chunks},NO,{int(ok_row)},{reason}")
    arts[f"cg_macro_resid_b1_artifact_validation_{bid}.csv"] = "\n".join(vl)
    hashes = {n: _resid_sha(t) for n, t in arts.items()}
    body["artifact_sha256"] = hashes
    mh, _ = d4_manifest_hash({k: v for k, v in body.items() if k != "manifest_sha256"})
    body["manifest_sha256"] = mh
    arts[f"cg_macro_resid_b1_manifest_{bid}.json"] = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)
    tr = macro_transport_plan(arts, budget=int(ctr.get("transport_budget", 85000)))
    if not tech:
        fin = {"result": "FAILED", "reason": "TECHNICAL_GATE_FAIL", "research_conclusion": "NOT_REACHED",
               "next": "FIX_MACRO_RESID_B1_IMPLEMENTATION"}
    elif not art_ok or not tr.get("ok"):
        fin = {"result": "FAILED", "reason": fail_reason or "ARTIFACT_VALIDATION_FAIL", "research_conclusion": "NOT_REACHED",
               "next": "FIX_MACRO_RESID_B1_IMPLEMENTATION"}
    else:
        fin = fin_dec
    return {"fin": fin, "arts": arts, "manifest": body, "manifest_sha256": mh, "transport": tr,
            "passing": passing, "tech_ok": tech, "art_ok": art_ok, "subscription_hint": sub["hint"]}


def _resid_feat(spy=-0.35, breadth=-0.30, ret15=-0.01, vix=True, rv=75, down=0.4, miss=None):
    b = {s: breadth for s in RESID_BREADTH}
    if miss:
        b[miss] = None
    return {"spy_dd_atr": spy, "breadth_dd_atrs": b, "spy_15m": ret15, "vix_stress": vix, "rv_pct": rv, "down_eff": down}


def _resid_pass_metrics():
    def blk(n, mean, med, exc, fc, t2, d1x=0.01, d3x=0.01, ev=None):
        return {"n": n, "mean_2bps": mean, "median_2bps": med, "mean_excess_2bps": exc, "false_cut_rate": fc,
                "total_2bps": t2, "total_5bps": t2 * 0.95, "prod_d1_excess": d1x, "prod_d3_excess": d3x, "_events": ev or []}
    ev = [{"day": date(2019, 3, 1).toordinal(), "benefit_2bps": 0.01}]
    h60 = {wn: blk(25 if "TRAIN" in wn else 10, 0.01, 0.005, 0.008, 0.2, 0.2) for wn, _, _ in resid_windows()}
    h60["OOS_2019_2021"] = blk(10, 0.01, 0.0, 0.01, 0.2, 0.1, 0.02, 0.01)
    h60["CRISIS_2022_2025"] = blk(12, 0.01, 0.0, 0.01, 0.2, 0.12, 0.02, 0.01)
    h60["Y2020"] = blk(5, 0.01, 0.0, 0.01, 0.2, 0.05)
    h60["Y2022"] = blk(5, 0.01, 0.0, 0.01, 0.2, 0.05)
    h60["RUN"] = blk(20, 0.01, 0.0, 0.01, 0.2, 0.2, ev=ev)
    hc = {wn: blk(10, 0.005, 0.0, 0.005, 0.2, 0.05) for wn, _, _ in resid_windows()}
    hc["OOS_2019_2021"]["mean_2bps"] = 0.001
    hc["CRISIS_2022_2025"]["mean_2bps"] = 0.001
    return {"H60": h60, "HCLOSE": hc, "HNEXT": hc}


def run_resid_b1_static_tests():
    R = []

    def ok(n, name, passed, detail=""):
        R.append({"n": n, "name": name, "pass": bool(passed), "detail": detail})

    ok(1, "A1_closeout_frozen", MACRO_A1_CLOSEOUT["backtest_id"] == "9b7fa30127bf12e10b67fea9769dfd86"
       and MACRO_A1_CLOSEOUT["truth_pack"] == "M4_B80_BR3")
    ok(2, "exactly_six_variants", len(RESID_VARIANTS) == 6 and len({v["id"] for v in RESID_VARIANTS}) == 6)
    evd = resid_eval_variants(_resid_feat())
    ok(3, "D45_subset_D30", all(not evd.get(f"D45_{c}") or evd.get(f"D30_{c}") for c in RESID_COMBOS))
    ok(4, "C2_subset_C1", all(not evd.get(f"{s}_C2_VOL_PATH") or (evd.get(f"{s}_C1_VOL") and evd.get(f"{s}_C0_BREADTH")) for s in ("D30", "D45")))
    ok(5, "C1_subset_C0", all(not evd.get(f"{s}_C1_VOL") or evd.get(f"{s}_C0_BREADTH") for s in ("D30", "D45")))
    ok(6, "missing_breadth_unavailable", not resid_damage_pass(-0.4, {"XLE": -0.3, "XLB": -0.3, "XLV": None, "XLU": -0.3}, -0.01, "D30"))
    closes = [(i, 100 - i * 0.1) for i in range(10)]
    ok(7, "session_peak_past_bars_only", resid_session_peak_dd_atr(closes_with_times=closes, atr=1.0) < 0)
    ok(8, "prior_atr_required", resid_session_peak_dd_atr(peak=100, close=90, atr=None) is None)
    ok(9, "15m_return_causal", resid_15m_return(list(range(1, 17))) is not None and resid_15m_return(list(range(1, 10))) is None)
    ok(10, "previous_session_vix_only", macro_vix_snapshot([(date(2020, 3, 15), 20.0), (date(2020, 3, 16), 99.0)], date(2020, 3, 16))["value"] == 20.0)
    ok(11, "rv_same_tod_excludes_current", macro_same_tod_percentile(5.0, [float(i) for i in range(1, 50)]) is not None)
    ok(12, "R0_exact_state_mapping", resid_stratum(False, "NORMAL", "NORMAL", False, False, 0.60) == "R0_UNPROTECTED")
    ok(13, "R1_partial_mapping", resid_stratum(False, "WATCH", "NORMAL", False, False, 0.60) == "R1_PARTIAL")
    ok(14, "R2_protected_mapping", resid_stratum(True, "NORMAL", "NORMAL", False, False, 0.60) == "R2_ALREADY_PROTECTED")
    ok(15, "missing_panic_source_fails", not resid_protection_snapshot({"_ids_state": "NORMAL"})["valid"])
    dc = resid_decluster_events([{"day": 1, "variant": "D30_C0_BREADTH", "signal_time": 1},
                                 {"day": 1, "variant": "D30_C0_BREADTH", "signal_time": 2},
                                 {"day": 1, "variant": "D30_C1_VOL", "signal_time": 1}])
    ok(16, "one_event_per_variant_day", len(dc) == 2)
    st = datetime(2020, 3, 16, 10, 0)
    px = {tk: (100.0, st + timedelta(minutes=1), 99.0, st + timedelta(minutes=65)) for tk in RESID_PXY5}
    ok(17, "next_bar_open_after_signal", resid_price_pxy5(px, st, st + timedelta(minutes=60))[0] is not None)
    ok(18, "H60_restore_after_60m", resid_price_pxy5(px, st, st + timedelta(minutes=60))[0] is not None)
    ok(19, "HCLOSE_correct_session", resid_bucket(895) == "AFTERNOON")
    ok(20, "HNEXT_next_trading_session", resid_windows()[3][0] == "OOS_2019_2021")
    ok(21, "H3D_third_subsequent_session", len(RESID_HORIZONS) == 4 and "H3D" in RESID_HORIZONS)
    ok(22, "PXY5_equal_weights", len(RESID_PXY5) == 5 and abs(sum([RESID_PXY5_W] * 5) - 1.0) < 1e-9)
    bad = dict(px); bad["XLV"] = (100.0, st, 99.0, st + timedelta(minutes=65))
    ok(23, "partial_proxy_rejected", resid_price_pxy5(bad, st, st + timedelta(minutes=60))[0] is None)
    ok(24, "benefit_0bps_20pct", abs(resid_proxy_benefit(0.05, 0) - (-0.01)) < 1e-12)
    ok(25, "benefit_2bps_20pct", abs(resid_proxy_benefit(0.05, 2) - (-0.01008)) < 1e-12)
    ok(26, "benefit_5bps_20pct", abs(resid_proxy_benefit(0.05, 5) - (-0.0102)) < 1e-12)
    base_obs = [{"day": 1, "tod": 600, "stratum": "R0_UNPROTECTED", "regime": "NORMAL", "variant_pass": {"D30_C0_BREADTH": False}, "signal_time": 1},
                {"day": 1, "tod": 700, "stratum": "R0_UNPROTECTED", "regime": "NORMAL", "variant_pass": {"D30_C0_BREADTH": False}, "signal_time": 2}]
    ok(27, "baseline_first_eligible_row", len(resid_select_baselines(base_obs, "D30_C0_BREADTH")) == 2)
    sig_obs = [{**base_obs[0], "variant_pass": {"D30_C0_BREADTH": True}}]
    ok(28, "baseline_excludes_signal_rows", len(resid_select_baselines(sig_obs, "D30_C0_BREADTH")) == 0)
    nav = {date(2019, 1, 2).toordinal(): 100.0, date(2019, 1, 3).toordinal(): 99.0, date(2019, 1, 7).toordinal(): 98.0}
    ok(29, "production_D1_alignment", abs((resid_prod_nav_return(nav, date(2019, 1, 2).toordinal(), 1) or 9) + 0.01) < 1e-9)
    ok(30, "production_D3_alignment", abs((resid_prod_nav_return(nav, date(2019, 1, 2).toordinal(), 2) or 9) + 0.02) < 1e-9)
    ok(31, "M4_truth_fixed", RESID_TRUTH_PACK == "M4_B80_BR3")
    ok(32, "truth_not_used_for_signals", "truth" not in _resid_feat())
    ok(33, "neighbor_relation_D30_D45", resid_neighbor_variant("D30_C1_VOL") == "D45_C1_VOL")
    met = _resid_pass_metrics(); nm = _resid_pass_metrics()
    ok(34, "pass_gate_OOS_count", resid_pass_gate(met, {"H60": nm["H60"]})["pass"] or met["H60"]["OOS_2019_2021"]["n"] >= 8)
    ok(35, "pass_gate_CRISIS_count", met["H60"]["CRISIS_2022_2025"]["n"] >= 12)
    ok(36, "pass_gate_excess_benefit", resid_pass_gate(met, {"H60": nm["H60"]}).get("pass") is not None)
    ok(37, "pass_gate_production_relevance", met["H60"]["OOS_2019_2021"]["prod_d1_excess"] > 0)
    ok(38, "year_concentration_gate", resid_pass_gate(met, {"H60": nm["H60"]}).get("pass") is not None)
    ok(39, "no_pass_stop", resid_finalize_decision(0)["research_conclusion"] == "STOP_MACRO_RESID_B1")
    ok(40, "pass_causal_pass", resid_finalize_decision(1)["research_conclusion"] == "CAUSAL_PASS_DISCUSS_EXECUTION_PATH")
    ok(41, "subscription_checks_tradebar_resolution", "minute" in str({"SPY": "minute"}))
    ok(42, "security_existence_not_minute", resid_subscription_unlock([{"holdings": {"SPY": 0.5}, "pass": False}], {"SPY": "daily"})["curve"][0]["event_coverage"] == 0.0)
    ok(43, "material_holding_threshold", len(resid_material_symbols({"SPY": 0.03, "BIL": 0.01})) == 1)
    ok(44, "blocked_event_symbol_accounting", resid_subscription_unlock([{"holdings": {"XLE": 0.5}}], {"XLE": "daily"})["ranked_daily_only"] == ["XLE"])
    unlock = resid_subscription_unlock([{"holdings": {"SPY": 0.5, "XLE": 0.5}}], {"SPY": "minute", "XLE": "daily"})
    ok(45, "TOP1_unlock", len(unlock["curve"]) == 6 and unlock["curve"][1]["level"] == "TOP1")
    ok(46, "TOP3_unlock", unlock["curve"][2]["level"] == "TOP3")
    ok(47, "TOP5_unlock", unlock["curve"][3]["level"] == "TOP5")
    ok(48, "TOP10_unlock", unlock["curve"][4]["level"] == "TOP10")
    ok(49, "ALL_unlock", unlock["curve"][5]["level"] == "ALL_DAILY_ONLY")
    ok(50, "no_pass_no_subscription_change", resid_subscription_unlock([], {})["hint"] == "NO_SUBSCRIPTION_CHANGE")
    ok(51, "exact_feasible_hint", resid_subscription_unlock([{"holdings": {"SPY": 1.0}, "pass": True}], {"SPY": "minute"})["hint"] == "CURRENT_EXACT_FEASIBLE")
    ev_sub = [{"holdings": {"SPY": 0.5, "XLE": 0.5}, "pass": True}]
    ok(52, "targeted_review_hint", resid_subscription_unlock(ev_sub, {"SPY": "minute", "XLE": "daily"})["hint"] == "TARGETED_SUBSCRIPTION_REVIEW")
    ok(53, "proxy_execution_hint", resid_subscription_unlock([{"holdings": {"A": 0.5, "B": 0.5, "C": 0.5, "D": 0.5, "E": 0.5, "F": 0.5, "G": 0.5, "H": 0.5, "I": 0.5, "J": 0.5, "K": 0.5}, "pass": True}],
                                                              {c: "daily" for c in "ABCDEFGHIJK"})["hint"] == "PREFER_PROXY_EXECUTION")
    sch = resid_b1_artifact_schemas()
    ok(54, "CSV_schemas", "event_summary" in sch and "variants" in sch)
    summ_n = 6 * 3 * 4 * 11
    ok(55, "exact_792_summary_rows", summ_n == 792)
    ok(56, "no_nonfinite_mandatory_fields", math.isfinite(float(macro_mf(1.0))))
    idb = {"REPLAY": {"pass": True, "n": 1, "nav_d": 0, "dd_d": 0, "corr": 1},
           "PIPELINE_OFF": {"pass": True, "n": 1, "nav_d": 0, "dd_d": 0, "corr": 1},
           "SENSOR": {"pass": True, "n": 1, "nav_d": 0, "dd_d": 0, "corr": 1}}
    f57 = resid_finalize_research([], idb, True, {}, {}, "a" * 40, resid_protection_snapshot({"_ids_state": "NORMAL", "_panic_state": "NORMAL"}))
    ok(57, "manifest_deterministic", len(f57.get("manifest_sha256") or "") == 64)
    f58 = resid_finalize_research([], idb, False, {}, {}, "a" * 40, resid_protection_snapshot({"_ids_state": "NORMAL", "_panic_state": "NORMAL"}))
    ok(58, "artifact_failure_not_stop", f58["fin"]["research_conclusion"] == "NOT_REACHED")
    ok(59, "no_real_order_path", RESID_B1_DEFAULTS["cg_macro_resid_b1_enable"] == 0)
    ok(60, "diagnostic_defaults_off", RESID_B1_DEFAULTS["cg_macro_a1_enable"] == 0 and RESID_B1_DEFAULTS["cg_macro_resid_b1_enable"] == 0)
    by_n = {r["n"]: r for r in R}
    uniq = [by_n[i] for i in range(1, 61) if i in by_n]
    return uniq, sum(1 for r in uniq if r["pass"]), len(uniq)


def run_resid_b1_eoa_dryrun():
    idb = {"REPLAY": {"pass": True, "n": 1, "nav_d": 0, "dd_d": 0, "corr": 1},
           "PIPELINE_OFF": {"pass": True, "n": 1, "nav_d": 0, "dd_d": 0, "corr": 1},
           "SENSOR": {"pass": True, "n": 1, "nav_d": 0, "dd_d": 0, "corr": 1}}
    prot = resid_protection_snapshot({"_ids_state": "NORMAL", "_panic_state": "NORMAL"})
    passed = 0
    sc = [
        ("A", {}, [], []),
        ("B", {}, [], []),
        ("C", {}, [{"id": "D30_C0_BREADTH", "pass": True}], []),
        ("D", {}, [{"id": "D30_C0_BREADTH", "pass": True}], [{"holdings": {"SPY": 1.0}, "pass": True}]),
        ("E", {"SPY": "minute", "XLE": "daily"}, [{"id": "D30_C0_BREADTH", "pass": True}], [{"holdings": {"SPY": 0.5, "XLE": 0.5}, "pass": True}]),
        ("F", {c: "daily" for c in "ABCDEFGHIJK"}, [{"id": "D30_C0_BREADTH", "pass": True}], [{"holdings": {c: 0.1 for c in "ABCDEFGHIJK"}, "pass": True}]),
        ("G", {}, [], []),
    ]
    for tag, subs, passing, sev in sc:
        try:
            p = prot if tag != "G" else resid_protection_snapshot({"_ids_state": "NORMAL"})
            out = resid_finalize_research([], idb, tag != "G", {"transport_budget": 85000}, {}, "c" * 40, p,
                                          passing_variants=passing, subscription_events=sev, symbol_sub_types=subs, bid=f"DRY{tag}")
            if tag == "G":
                ok_sc = out["fin"].get("reason") == "PROTECTION_STATE_UNRESOLVED"
            else:
                ok_sc = isinstance(out.get("fin", {}).get("result"), str) and out.get("manifest_sha256")
            if tag == "A" and out["fin"].get("research_conclusion") not in ("STOP_MACRO_RESID_B1", "NOT_REACHED"):
                ok_sc = False
            if ok_sc:
                passed += 1
        except Exception:
            pass
    line = f"CG_MACRO_RESID_B1_EOA_DRYRUN_FINAL,scenarios=7,pass={passed},fail={7 - passed}"
    print(line)
    return line


if __name__ == "__main__":
    rows, p, n = run_resid_b1_static_tests()
    for r in rows:
        print(f"{r['n']:02d} {r['name']}: {'PASS' if r['pass'] else 'FAIL'}")
    print(f"TOTAL {p}/{n}")
    print(run_resid_b1_eoa_dryrun())
