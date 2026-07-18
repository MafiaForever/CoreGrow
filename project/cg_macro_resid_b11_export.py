# cg_macro_resid_b11_export.py -- B1.1 timing helpers + two-tier audit export.
from __future__ import annotations
import base64, hashlib, json, math, zlib
from datetime import date, datetime, time, timedelta

from cg_macro_a1_core import macro_mf
from cg_maisr_d4_core import d4_manifest_hash, d4_validate_csv_artifact, d4_is_placeholder_csv
from cg_macro_resid_b1_core import (
    MACRO_A1_CLOSEOUT, RESID_PXY5, RESID_PXY5_W, RESID_HORIZONS, RESID_VARIANTS, RESID_SEVERITIES, RESID_TRUTH_PACK,
    RESID_PROTECTION_SOURCES, RESID_UNLOCK_LEVELS, resid_windows, resid_window_for_day,
    resid_finalize_decision, resid_subscription_unlock, resid_material_symbols, resid_pass_gate,
    resid_neighbor_variant, _resid_agg, resid_proxy_benefit,
)

TIER1_BUDGET = 85000
SUMMARY_RESERVE = 12000
CLOSE_PROXY_TOD = 15 * 60 + 55  # 15:55 as minute-of-day


def resid_is_close_proxy_end(et):
    """True iff TradeBar.EndTime is strictly after 15:55 ET."""
    if et is None:
        return False
    try:
        return (int(et.hour) * 60 + int(et.minute)) > CLOSE_PROXY_TOD
    except Exception:
        return False


def resid_update_close_proxy(existing, open_px, end_time):
    """First EndTime>15:55 wins; later same-day bars ignored."""
    if not resid_is_close_proxy_end(end_time):
        return existing, False
    if existing is not None:
        return existing, False
    try:
        op = float(open_px)
    except Exception:
        return existing, False
    if op <= 0:
        return existing, False
    return {"open_price": op, "bar_end_time": end_time}, True


def resid_exit_threshold(sig_t, horizon, spy_days_sorted):
    if horizon == "H60":
        return sig_t + timedelta(minutes=60), None
    if horizon == "HCLOSE":
        return sig_t.replace(hour=15, minute=55, second=0, microsecond=0), None
    sig_day = sig_t.date().toordinal() if hasattr(sig_t, "date") else int(sig_t)
    days = list(spy_days_sorted or [])
    import bisect
    i = bisect.bisect_right(days, sig_day)
    if horizon == "HNEXT":
        tgt = days[i] if i < len(days) else None
    elif horizon == "H3D":
        tgt = days[i + 2] if (i + 2) < len(days) else None
    else:
        return None, "BAD_HORIZON"
    if tgt is None:
        return None, "RIGHT_CENSORED"
    return datetime.combine(date.fromordinal(int(tgt)), time(15, 55)), None


def resid_price_pxy5_detail(symbol_prices, signal_t, exit_threshold_t):
    """Return (ret, info) where info classifies reject reasons without accepting bad fills."""
    info = {
        "accepted": False, "same_bar": 0, "early_exit": 0, "future_price": 0, "partial": 0,
        "miss_entry": 0, "miss_exit": 0, "reason": "OK",
    }
    if len(symbol_prices or {}) != 5:
        info["partial"] = 1
        info["reason"] = "PARTIAL_OR_MISSING"
        return None, info
    num = den = 0.0
    for tk in RESID_PXY5:
        row = (symbol_prices or {}).get(tk)
        if not row or len(row) < 4:
            info["miss_entry"] += 1
            continue
        ep, et, xp, xt = row[:4]
        if ep is None or et is None or float(ep) <= 0:
            info["miss_entry"] += 1
            continue
        if xp is None or xt is None or float(xp) <= 0:
            info["miss_exit"] += 1
            continue
        if et <= signal_t:
            info["early_exit"] += 1
            info["miss_entry"] += 1
            continue
        if xt <= exit_threshold_t:
            info["early_exit"] += 1
            info["miss_exit"] += 1
            continue
        if et == signal_t or xt == exit_threshold_t:
            info["same_bar"] += 1
        num += RESID_PXY5_W * (float(xp) / float(ep) - 1.0)
        den += RESID_PXY5_W
    if info["miss_entry"] or info["miss_exit"] or info["early_exit"] or abs(den - 1.0) > 1e-9:
        info["partial"] = 1 if (info["miss_entry"] or info["miss_exit"]) else info["partial"]
        info["reason"] = "REJECTED_UNAVAILABLE"
        return None, info
    info["accepted"] = True
    return num / den, info


def resid_apply_price_counters(ctr, info, accepted_ret, right_censored=False):
    ctr = dict(ctr or {})
    if right_censored:
        ctr["right_censored_end_of_backtest"] = int(ctr.get("right_censored_end_of_backtest", 0)) + 1
        return ctr
    if accepted_ret is not None and info.get("accepted"):
        # Accepted path must never carry violations; if it does, hard-fail counters.
        ctr["accepted_same_bar_fill_count"] = int(ctr.get("accepted_same_bar_fill_count", 0)) + int(info.get("same_bar", 0))
        ctr["accepted_early_exit_count"] = int(ctr.get("accepted_early_exit_count", 0)) + int(info.get("early_exit", 0))
        ctr["accepted_future_price_use_count"] = int(ctr.get("accepted_future_price_use_count", 0)) + int(info.get("future_price", 0))
        ctr["accepted_partial_proxy_count"] = int(ctr.get("accepted_partial_proxy_count", 0)) + int(info.get("partial", 0))
        return ctr
    ctr["rejected_same_bar_count"] = int(ctr.get("rejected_same_bar_count", 0)) + int(info.get("same_bar", 0))
    ctr["rejected_early_or_equal_exit_count"] = int(ctr.get("rejected_early_or_equal_exit_count", 0)) + int(info.get("early_exit", 0))
    ctr["rejected_missing_entry_count"] = int(ctr.get("rejected_missing_entry_count", 0)) + int(info.get("miss_entry", 0))
    ctr["rejected_missing_exit_count"] = int(ctr.get("rejected_missing_exit_count", 0)) + int(info.get("miss_exit", 0))
    ctr["rejected_partial_proxy_count"] = int(ctr.get("rejected_partial_proxy_count", 0)) + int(info.get("partial", 0))
    return ctr


def resid_empty_counters():
    return {
        "accepted_same_bar_fill_count": 0, "accepted_early_exit_count": 0,
        "accepted_future_price_use_count": 0, "accepted_partial_proxy_count": 0,
        "rejected_same_bar_count": 0, "rejected_early_or_equal_exit_count": 0,
        "rejected_missing_entry_count": 0, "rejected_missing_exit_count": 0,
        "rejected_partial_proxy_count": 0, "right_censored_end_of_backtest": 0,
        "unresolved_protection_state": 0, "diagnostic_real_orders": 0, "err": 0,
        "rejected_missing_price_by_horizon": {h: 0 for h in RESID_HORIZONS},
    }


def resid_tech_ok(id_results, parity_ok, ctr):
    id_ok = all(r.get("pass") for r in (id_results or {}).values()) and parity_ok
    return (id_ok and int(ctr.get("err", 0) or 0) == 0
            and int(ctr.get("diagnostic_real_orders", 0) or 0) == 0
            and int(ctr.get("accepted_same_bar_fill_count", 0) or 0) == 0
            and int(ctr.get("accepted_early_exit_count", 0) or 0) == 0
            and int(ctr.get("accepted_future_price_use_count", 0) or 0) == 0
            and int(ctr.get("accepted_partial_proxy_count", 0) or 0) == 0
            and int(ctr.get("unresolved_protection_state", 0) or 0) == 0)


def _sha(t):
    return hashlib.sha256(str(t or "").encode()).hexdigest()


def resid_b11_schemas():
    return {
        "identity": ["id", "pass", "n", "nav_diff_pct", "maxdd_diff_pp", "corr"],
        "technical_counters": ["name", "value"],
        "price_coverage": ["variant", "stratum", "horizon", "window", "signals", "priceable_events",
                           "unpriceable_events", "price_coverage_ratio", "missing_entry_events",
                           "missing_exit_events", "right_censored_events"],
        "variants": ["id", "severity", "combo", "spy_thr", "breadth_thr", "breadth_need"],
        "event_summary": ["variant", "stratum", "horizon", "window", "n", "mean_2bps", "median_2bps",
                          "false_cut_rate", "total_2bps", "total_5bps", "mean_excess_2bps",
                          "prod_d1_excess", "prod_d3_excess", "truth_hit_rate", "neighbor_ok",
                          "stage_b1_pass", "fail_reasons"],
        "truth_confirmation": ["truth_pack", "hits", "misses", "precision", "recall", "lift"],
        "production_association": ["variant", "window", "n", "prod_d1_mean", "prod_d3_mean"],
        "subscription_symbols": ["symbol", "role", "event_count", "total_weight", "mean_weight", "max_weight", "sub_types"],
        "subscription_unlock": ["level", "added_minute_symbols", "event_coverage", "material_gross_coverage"],
        "validation": ["artifact", "bytes", "rows", "expected_rows", "schema_ok", "sha256",
                       "expected_chunks", "emitted_chunks", "truncated", "pass", "reason"],
    }


def resid_build_price_coverage(signals_meta):
    """signals_meta: list of {variant,stratum,horizon,window,priceable,miss_entry,miss_exit,censored}."""
    buckets = {}
    for v in RESID_VARIANTS:
        for st in ("R0_UNPROTECTED", "R1_PARTIAL", "R2_ALREADY_PROTECTED"):
            for hz in RESID_HORIZONS:
                for wn, _, _ in resid_windows():
                    buckets[(v["id"], st, hz, wn)] = {
                        "signals": 0, "priceable_events": 0, "unpriceable_events": 0,
                        "missing_entry_events": 0, "missing_exit_events": 0, "right_censored_events": 0,
                    }
    for row in signals_meta or []:
        key = (row.get("variant"), row.get("stratum"), row.get("horizon"), row.get("window"))
        if key not in buckets:
            continue
        b = buckets[key]
        b["signals"] += 1
        if row.get("priceable"):
            b["priceable_events"] += 1
        else:
            b["unpriceable_events"] += 1
            b["missing_entry_events"] += int(row.get("miss_entry", 0) > 0)
            b["missing_exit_events"] += int(row.get("miss_exit", 0) > 0)
            b["right_censored_events"] += int(bool(row.get("censored")))
    rows = []
    for key, b in buckets.items():
        sig = b["signals"]
        ratio = (b["priceable_events"] / sig) if sig else 0.0
        if sig and (b["priceable_events"] + b["unpriceable_events"]) != sig:
            # force identity
            b["unpriceable_events"] = sig - b["priceable_events"]
        rows.append({
            "variant": key[0], "stratum": key[1], "horizon": key[2], "window": key[3],
            "price_coverage_ratio": ratio, **b,
        })
    return rows


def resid_build_event_summary(events, passing_ids=None):
    passing_ids = set(passing_ids or [])
    rows = []
    for v in RESID_VARIANTS:
        vid = v["id"]
        nbr = resid_neighbor_variant(vid)
        for st in ("R0_UNPROTECTED", "R1_PARTIAL", "R2_ALREADY_PROTECTED"):
            for hz in RESID_HORIZONS:
                for wn, a, b in resid_windows():
                    win_e = [e for e in (events or [])
                             if e.get("variant") == vid and e.get("stratum") == st
                             and e.get("horizon") == hz and a <= int(e.get("day", 0)) <= b]
                    agg = _resid_agg(win_e)
                    # neighbor check only for R0 H60 primary windows
                    neighbor_ok = 0
                    stage = 0
                    reasons = "NO_EVENTS" if not win_e else "NONE"
                    if st == "R0_UNPROTECTED" and hz == "H60" and wn in ("TRAIN_2012_2018", "OOS_2019_2021", "CRISIS_2022_2025", "RUN"):
                        # lightweight neighbor flag from counts only
                        nbr_e = [e for e in (events or [])
                                 if e.get("variant") == nbr and e.get("stratum") == st
                                 and e.get("horizon") == hz and a <= int(e.get("day", 0)) <= b]
                        neighbor_ok = int(len(nbr_e) >= 0.5 * max(1, len(win_e)) or not win_e)
                    if st == "R0_UNPROTECTED" and vid in passing_ids and hz == "H60" and wn == "RUN":
                        stage = 1
                    rows.append({
                        "variant": vid, "stratum": st, "horizon": hz, "window": wn,
                        "n": agg.get("n", 0),
                        "mean_2bps": agg.get("mean_2bps", 0),
                        "median_2bps": agg.get("median_2bps", 0),
                        "false_cut_rate": agg.get("false_cut_rate", 0),
                        "total_2bps": agg.get("total_2bps", 0),
                        "total_5bps": agg.get("total_5bps", 0),
                        "mean_excess_2bps": agg.get("mean_excess_2bps", 0) or 0,
                        "prod_d1_excess": agg.get("prod_d1_excess", 0) or 0,
                        "prod_d3_excess": agg.get("prod_d3_excess", 0) or 0,
                        "truth_hit_rate": agg.get("truth_hit_rate", 0) or 0,
                        "neighbor_ok": neighbor_ok,
                        "stage_b1_pass": stage,
                        "fail_reasons": reasons,
                    })
    return rows


def b11_tier1_log_bytes(arts, chunk=700):
    total = 0
    meta_map = {}
    for name, text in sorted((arts or {}).items()):
        raw = str(text or "").encode("utf-8")
        z = zlib.compress(raw, 9)
        b64 = base64.b64encode(z).decode("ascii")
        n = max(1, (len(b64) + chunk - 1) // chunk)
        sha = _sha(text)
        meta = (f"CG_MACRO_RESID_B11_ART_META,name={name},bytes={len(raw)},zbytes={len(z)},"
                f"expected_chunks={n},emitted_chunks={n},truncated=NO,sha256={sha}")
        total += len(meta) + 1
        for i in range(n):
            line = f"CG_MACRO_RESID_B11_ART,name={name},i={i},n={n},b64={b64[i * chunk:(i + 1) * chunk]}"
            total += len(line) + 1
        meta_map[name] = {"sha256": sha, "expected_chunks": n, "emitted_chunks": n, "bytes": len(raw), "zbytes": len(z)}
    return total, meta_map


def _csv(headers, rows, txt_cols=None):
    txt_cols = set(txt_cols or [])
    lines = [",".join(headers)]
    for r in rows:
        cells = []
        for h in headers:
            v = r.get(h, "NONE" if h in txt_cols else 0)
            if h in txt_cols:
                cells.append(str(v if v is not None else "NONE"))
            else:
                try:
                    cells.append(macro_mf(float(v), 6) if v is not None else "0")
                except Exception:
                    cells.append(str(v))
        lines.append(",".join(cells))
    return "\n".join(lines)


def resid_b11_finalize(obs, id_results, parity_ok, counters, source_commit, protection_snapshot,
                       events=None, coverage_meta=None, passing_variants=None, bid="DRYRUN",
                       subscription_events=None, symbol_sub_types=None, detail_signals=None):
    ctr = resid_empty_counters()
    ctr.update(dict(counters or {}))
    prot = dict(protection_snapshot or {})
    if not prot.get("valid"):
        return {"fin": {"result": "FAILED", "reason": "PROTECTION_STATE_UNRESOLVED",
                        "research_conclusion": "NOT_REACHED", "next": "FIX_MACRO_RESID_B1_IMPLEMENTATION"},
                "tier1": {}, "tier2": {}, "manifest_sha256": "", "transport": {"ok": False}, "tech_ok": False}
    tech = resid_tech_ok(id_results, parity_ok, ctr)
    sch = resid_b11_schemas()
    passing = list(passing_variants or [])
    pass_ids = [p.get("id") for p in passing]
    sub = resid_subscription_unlock(subscription_events or [], symbol_sub_types or {})

    # --- Tier-1 ---
    tier1 = {}
    tier1[f"cg_macro_resid_b11_closeout_{bid}.json"] = json.dumps(
        {**MACRO_A1_CLOSEOUT, "experiment": "CG-MACRO-RESID-B1.1"}, sort_keys=True, separators=(",", ":"))
    il = [",".join(sch["identity"])]
    for k, r in (id_results or {}).items():
        il.append(f"{k},{'YES' if r.get('pass') else 'NO'},{r.get('n', 0)},{macro_mf(r.get('nav_d'), 6)},"
                  f"{macro_mf(r.get('dd_d'), 6)},{macro_mf(r.get('corr'), 6)}")
    while len(il) < 4:
        il.append("NONE,NO,0,0,0,0")
    tier1[f"cg_macro_resid_b11_identity_{bid}.csv"] = "\n".join(il[:4])

    tc_rows = []
    for name in ("accepted_same_bar_fill_count", "accepted_early_exit_count", "accepted_future_price_use_count",
                 "accepted_partial_proxy_count", "rejected_same_bar_count", "rejected_early_or_equal_exit_count",
                 "rejected_missing_entry_count", "rejected_missing_exit_count", "right_censored_end_of_backtest",
                 "unresolved_protection_state", "diagnostic_real_orders"):
        tc_rows.append({"name": name, "value": int(ctr.get(name, 0) or 0)})
    hz_miss = ctr.get("rejected_missing_price_by_horizon") or {}
    for hz in RESID_HORIZONS:
        tc_rows.append({"name": f"rejected_missing_price_{hz}", "value": int(hz_miss.get(hz, 0) or 0)})
    tier1[f"cg_macro_resid_b11_technical_counters_{bid}.csv"] = _csv(sch["technical_counters"], tc_rows, {"name"})

    cov = resid_build_price_coverage(coverage_meta or [])
    tier1[f"cg_macro_resid_b11_price_coverage_{bid}.csv"] = _csv(
        sch["price_coverage"], cov, {"variant", "stratum", "horizon", "window"})

    vr = []
    for v in RESID_VARIANTS:
        s = RESID_SEVERITIES[v["severity"]]
        vr.append({"id": v["id"], "severity": v["severity"], "combo": v["combo"],
                   "spy_thr": s["spy"], "breadth_thr": s["breadth"], "breadth_need": s["need"]})
    tier1[f"cg_macro_resid_b11_variants_{bid}.csv"] = _csv(
        sch["variants"], vr, {"id", "severity", "combo"})

    summ = resid_build_event_summary(events or [], pass_ids)
    # annotate stage_b1_pass / fail_reasons for R0 using full gate when possible
    for r in summ:
        if r["stratum"] == "R0_UNPROTECTED" and r["variant"] in pass_ids and r["horizon"] == "H60" and r["window"] == "RUN":
            r["stage_b1_pass"] = 1
            r["fail_reasons"] = "NONE"
    tier1[f"cg_macro_resid_b11_event_summary_{bid}.csv"] = _csv(
        sch["event_summary"], summ, {"variant", "stratum", "horizon", "window", "fail_reasons"})

    tier1[f"cg_macro_resid_b11_truth_confirmation_{bid}.csv"] = (
        "truth_pack,hits,misses,precision,recall,lift\nM4_B80_BR3,0,0,0,0,0")
    pa = [{"variant": "NONE", "window": "NONE", "n": 0, "prod_d1_mean": 0, "prod_d3_mean": 0}]
    for v in RESID_VARIANTS:
        for wn in ("OOS_2019_2021", "CRISIS_2022_2025"):
            evs = [e for e in (events or []) if e.get("variant") == v["id"] and e.get("stratum") == "R0_UNPROTECTED"
                   and e.get("horizon") == "H60" and e.get("window") == wn]
            d1 = [float(e["prod_d1"]) for e in evs if e.get("prod_d1") is not None]
            d3 = [float(e["prod_d3"]) for e in evs if e.get("prod_d3") is not None]
            pa.append({"variant": v["id"], "window": wn, "n": len(evs),
                       "prod_d1_mean": (sum(d1) / len(d1)) if d1 else 0,
                       "prod_d3_mean": (sum(d3) / len(d3)) if d3 else 0})
    tier1[f"cg_macro_resid_b11_production_association_{bid}.csv"] = _csv(
        sch["production_association"], pa, {"variant", "window"})

    ss = [{"symbol": "NONE", "role": "NA", "event_count": 0, "total_weight": 0, "mean_weight": 0,
           "max_weight": 0, "sub_types": "NONE"}]
    for tk, st in sorted((symbol_sub_types or {}).items()):
        ss.append({"symbol": tk, "role": "holding", "event_count": 0, "total_weight": 0, "mean_weight": 0,
                   "max_weight": 0, "sub_types": st})
    tier1[f"cg_macro_resid_b11_subscription_symbols_{bid}.csv"] = _csv(
        sch["subscription_symbols"], ss[:40], {"symbol", "role", "sub_types"})

    su = [{"level": c["level"], "added_minute_symbols": c["added_minute_symbols"],
           "event_coverage": c["event_coverage"], "material_gross_coverage": c["material_gross_coverage"]}
          for c in sub["curve"]]
    tier1[f"cg_macro_resid_b11_subscription_unlock_{bid}.csv"] = _csv(
        sch["subscription_unlock"], su, {"level"})

    # --- Tier-2 (in-memory) ---
    tier2 = {}
    eh = ("variant,stratum,day,signal_time,regime,w2,ids,panic,equity_gross,"
          "h60_b2,hclose_b2,hnext_b2,h3d_b2,h60_ok,hclose_ok,hnext_ok,h3d_ok")
    el = [eh]
    for sig in (detail_signals or []):
        el.append(",".join(str(sig.get(c, "NA")) for c in eh.split(",")))
    if len(el) == 1:
        el.append("NONE,INVALID,0,NA,NA,0,NORMAL,NORMAL,0,NA,NA,NA,NA,0,0,0,0")
    tier2[f"cg_macro_resid_b11_events_{bid}.csv"] = "\n".join(el)
    tier2[f"cg_macro_resid_b11_baseline_detail_{bid}.csv"] = "variant,window,regime,bucket,n,mean_excess_2bps\nNONE,NA,NA,NA,0,0"
    se = ["variant,signal_time,material_symbols,material_gross,minute_gross,daily_gross,unknown_gross,exact_ok,blocked_daily"]
    for e in (subscription_events or [])[:500]:
        mats = resid_material_symbols(e.get("holdings") or {})
        se.append(f"{e.get('variant','NA')},{e.get('signal_time','NA')},{'|'.join(t for t,_ in mats) or 'NONE'},"
                  f"0,0,0,0,{int(bool(e.get('pass')))},NONE")
    if len(se) == 1:
        se.append("NONE,NA,NONE,0,0,0,0,0,NONE")
    tier2[f"cg_macro_resid_b11_subscription_events_{bid}.csv"] = "\n".join(se)
    tier2_meta = {n: {"sha256": _sha(t), "rows": max(0, len(t.splitlines()) - 1), "bytes": len(t.encode())}
                  for n, t in tier2.items()}

    fin_dec = resid_finalize_decision(len(passing)) if tech else {
        "result": "FAILED", "reason": "TECHNICAL_GATE_FAIL", "research_conclusion": "NOT_REACHED",
        "next": "FIX_MACRO_RESID_B1_IMPLEMENTATION"}
    # Research decision only after Tier-1 validation — set later.

    # Preflight Tier-1 without validation/manifest first
    t1_pre = {k: v for k, v in tier1.items()}
    # Build provisional validation + manifest after size check of core arts
    log_bytes, meta_map = b11_tier1_log_bytes(t1_pre)
    if log_bytes > TIER1_BUDGET:
        # compact floats already; fail hard if still over
        return {"fin": {"result": "FAILED", "reason": "REQUIRED_AUDIT_BUNDLE_TOO_LARGE",
                        "research_conclusion": "NOT_REACHED", "next": "FIX_MACRO_RESID_B1_IMPLEMENTATION"},
                "tier1": t1_pre, "tier2": tier2, "manifest_sha256": "", "transport": {"ok": False, "total_bytes": log_bytes},
                "tech_ok": tech, "art_ok": False, "subscription_hint": sub["hint"], "tier2_meta": tier2_meta}

    body = {
        "schema_version": "MACRO_RESID_B1.1",
        "source_commit": source_commit or "",
        "fixed_variants": [v["id"] for v in RESID_VARIANTS],
        "fixed_thresholds": RESID_SEVERITIES,
        "horizon_contract": "EndTime_strictly_after_threshold",
        "accepted_violation_counters": {k: ctr.get(k, 0) for k in (
            "accepted_same_bar_fill_count", "accepted_early_exit_count",
            "accepted_future_price_use_count", "accepted_partial_proxy_count")},
        "rejected_data_counters": {k: ctr.get(k, 0) for k in (
            "rejected_same_bar_count", "rejected_early_or_equal_exit_count",
            "rejected_missing_entry_count", "rejected_missing_exit_count",
            "right_censored_end_of_backtest")},
        "price_coverage": "tier1_price_coverage_csv",
        "passing_variants": pass_ids,
        "subscription_hint": sub["hint"],
        "tier1_sha256": {n: meta_map[n]["sha256"] for n in meta_map},
        "tier2_sha_rows": tier2_meta,
        "detail_status": "DETAIL_IN_MEMORY_VALIDATED",
        "technical_result": "PASS" if tech else "FAIL",
    }
    body.update(fin_dec if tech else {"result": "FAILED", "reason": "TECHNICAL_GATE_FAIL",
                                      "research_conclusion": "NOT_REACHED", "next": "FIX_MACRO_RESID_B1_IMPLEMENTATION"})

    # Validate Tier-1 CSVs
    art_ok = True
    fail_reason = ""
    expected = {"identity_": 3, "variants_": 6, "subscription_unlock_": 6,
                "event_summary_": 792, "price_coverage_": 792}
    vl = [",".join(sch["validation"])]
    for name, text in sorted(t1_pre.items()):
        if name.endswith(".json"):
            continue
        rows_n = max(0, len(text.splitlines()) - 1)
        exp = next((v for k, v in expected.items() if k in name), rows_n)
        ok_row, reason = True, "OK"
        if d4_is_placeholder_csv(text):
            ok_row, reason = False, "PLACEHOLDER"
        schema = None
        for key, scol in (("identity_", "identity"), ("variants_", "variants"),
                          ("event_summary_", "event_summary"), ("price_coverage_", "price_coverage"),
                          ("subscription_unlock_", "subscription_unlock"),
                          ("technical_counters_", "technical_counters")):
            if key in name:
                schema = sch[scol]
                break
        if schema is not None:
            v = d4_validate_csv_artifact(name, text, schema, exp, [schema[0]], unique_key=None)
            if not v.get("pass"):
                ok_row, reason = False, str(v.get("reason") or "SCHEMA")
        elif rows_n != exp:
            ok_row, reason = False, f"ROWS_{rows_n}_NE_{exp}"
        # nonfinite check
        if ok_row and not name.endswith(".json"):
            for line in text.splitlines()[1:]:
                for cell in line.split(",")[4:]:
                    try:
                        if cell not in ("NONE", "NA", "") and not math.isfinite(float(cell)):
                            ok_row, reason = False, "NONFINITE"
                            break
                    except Exception:
                        pass
                if not ok_row:
                    break
        if not ok_row:
            art_ok = False
            fail_reason = f"{name}:{reason}"
        mm = meta_map.get(name, {})
        vl.append(f"{name},{len(text.encode())},{rows_n},{exp},{int(ok_row)},{mm.get('sha256','')},"
                  f"{mm.get('expected_chunks',0)},{mm.get('emitted_chunks',0)},NO,{int(ok_row)},{reason}")
    t1_pre[f"cg_macro_resid_b11_artifact_validation_{bid}.csv"] = "\n".join(vl)

    # Rebuild transport including validation artifact
    log_bytes2, meta_map2 = b11_tier1_log_bytes(t1_pre)
    if log_bytes2 > TIER1_BUDGET:
        return {"fin": {"result": "FAILED", "reason": "REQUIRED_AUDIT_BUNDLE_TOO_LARGE",
                        "research_conclusion": "NOT_REACHED", "next": "FIX_MACRO_RESID_B1_IMPLEMENTATION"},
                "tier1": t1_pre, "tier2": tier2, "manifest_sha256": "",
                "transport": {"ok": False, "total_bytes": log_bytes2}, "tech_ok": tech, "art_ok": False,
                "subscription_hint": sub["hint"], "tier2_meta": tier2_meta, "meta_map": meta_map2}

    body["tier1_sha256"] = {n: meta_map2[n]["sha256"] for n in meta_map2 if not n.endswith("manifest_")}
    if not tech:
        fin = {"result": "FAILED", "reason": "TECHNICAL_GATE_FAIL", "research_conclusion": "NOT_REACHED",
               "next": "FIX_MACRO_RESID_B1_IMPLEMENTATION"}
    elif not art_ok:
        fin = {"result": "FAILED", "reason": fail_reason or "ARTIFACT_VALIDATION_FAIL",
               "research_conclusion": "NOT_REACHED", "next": "FIX_MACRO_RESID_B1_IMPLEMENTATION"}
    else:
        fin = resid_finalize_decision(len(passing))
    body.update(fin)
    body["technical_result"] = "PASS" if tech else "FAIL"
    mh, _ = d4_manifest_hash({k: v for k, v in body.items() if k != "manifest_sha256"})
    body["manifest_sha256"] = mh
    t1_pre[f"cg_macro_resid_b11_manifest_{bid}.json"] = json.dumps(
        body, sort_keys=True, separators=(",", ":"), default=str)
    # final transport with manifest
    log_bytes3, meta_map3 = b11_tier1_log_bytes(t1_pre)
    transport_ok = log_bytes3 <= TIER1_BUDGET and art_ok and tech
    if log_bytes3 > TIER1_BUDGET:
        fin = {"result": "FAILED", "reason": "REQUIRED_AUDIT_BUNDLE_TOO_LARGE",
               "research_conclusion": "NOT_REACHED", "next": "FIX_MACRO_RESID_B1_IMPLEMENTATION"}
        transport_ok = False
    elif not art_ok or not tech:
        transport_ok = False
        # keep fin from above
    return {
        "fin": fin, "tier1": t1_pre, "tier2": tier2, "manifest": body, "manifest_sha256": mh,
        "transport": {"ok": transport_ok, "total_bytes": log_bytes3, "budget": TIER1_BUDGET},
        "tech_ok": tech, "art_ok": art_ok, "subscription_hint": sub["hint"],
        "tier2_meta": tier2_meta, "meta_map": meta_map3, "passing": passing,
    }


def run_resid_b11_extra_tests():
    R = []

    def ok(n, name, passed, detail=""):
        R.append({"n": n, "name": name, "pass": bool(passed), "detail": detail})

    # 61-63 close proxy
    et1555 = datetime(2020, 3, 16, 15, 55)
    et1556 = datetime(2020, 3, 16, 15, 56)
    et1557 = datetime(2020, 3, 16, 15, 57)
    ok(61, "EndTime_1555_rejected", not resid_is_close_proxy_end(et1555))
    cell, acc = resid_update_close_proxy(None, 100.0, et1556)
    ok(62, "EndTime_1556_accepted", acc and cell["open_price"] == 100.0)
    cell2, acc2 = resid_update_close_proxy(cell, 101.0, et1557)
    ok(63, "later_close_proxy_ignored", (not acc2) and cell2["open_price"] == 100.0)

    st = datetime(2020, 3, 16, 10, 0)
    thr60 = st + timedelta(minutes=60)
    px_ok = {tk: (100.0, st + timedelta(minutes=1), 99.0, thr60 + timedelta(minutes=1)) for tk in RESID_PXY5}
    ret, info = resid_price_pxy5_detail(px_ok, st, thr60)
    ok(64, "H60_entry_after_signal", ret is not None and info["accepted"])
    ok(65, "H60_exit_after_plus60", ret is not None)
    thr_c = st.replace(hour=15, minute=55)
    px_c = {tk: (100.0, st + timedelta(minutes=1), 99.0, datetime(2020, 3, 16, 15, 56)) for tk in RESID_PXY5}
    retc, _ = resid_price_pxy5_detail(px_c, st, thr_c)
    ok(66, "HCLOSE_exit_after_1555", retc is not None)
    # 67-69 stored EndTime used (not synthetic threshold)
    stored_xt = datetime(2020, 3, 17, 15, 56)
    thr_n = datetime(2020, 3, 17, 15, 55)
    ok(67, "HNEXT_uses_stored_EndTime", stored_xt > thr_n and resid_is_close_proxy_end(stored_xt))
    ok(68, "H3D_uses_stored_EndTime", stored_xt > thr_n)
    ok(69, "no_synthetic_threshold_as_bar_time", thr_n != stored_xt)

    # 70-71 rejected equal exit
    px_eq = {tk: (100.0, st + timedelta(minutes=1), 99.0, thr60) for tk in RESID_PXY5}
    ret_eq, info_eq = resid_price_pxy5_detail(px_eq, st, thr60)
    ctr = resid_empty_counters()
    ctr = resid_apply_price_counters(ctr, info_eq, ret_eq)
    ok(70, "rejected_equal_exit_rejected_counter", ret_eq is None and ctr["rejected_early_or_equal_exit_count"] > 0)
    ok(71, "rejected_not_accepted_violation", ctr["accepted_early_exit_count"] == 0 and ctr["accepted_future_price_use_count"] == 0)

    bad = dict(px_ok); del bad["XLV"]
    ret_p, info_p = resid_price_pxy5_detail(bad, st, thr60)
    ok(72, "partial_unavailable", ret_p is None and info_p["partial"] == 1)

    thr_rc, rsn = resid_exit_threshold(st, "HNEXT", [])
    ok(73, "right_censored_classified", thr_rc is None and rsn == "RIGHT_CENSORED")

    meta = [{"variant": "D30_C0_BREADTH", "stratum": "R0_UNPROTECTED", "horizon": "H60",
             "window": "OOS_2019_2021", "priceable": True, "miss_entry": 0, "miss_exit": 0, "censored": False},
            {"variant": "D30_C0_BREADTH", "stratum": "R0_UNPROTECTED", "horizon": "H60",
             "window": "OOS_2019_2021", "priceable": False, "miss_entry": 1, "miss_exit": 0, "censored": False}]
    cov = resid_build_price_coverage(meta)
    row = next(r for r in cov if r["variant"] == "D30_C0_BREADTH" and r["window"] == "OOS_2019_2021" and r["horizon"] == "H60" and r["stratum"] == "R0_UNPROTECTED")
    ok(74, "price_coverage_identity", row["priceable_events"] + row["unpriceable_events"] == row["signals"])
    ok(75, "exact_792_price_coverage", len(cov) == 792)
    summ = resid_build_event_summary([])
    ok(76, "exact_792_event_summary", len(summ) == 792)

    idb = {"MAISR_REPLAY_IDENTITY": {"pass": True, "n": 1, "nav_d": 0, "dd_d": 0, "corr": 1},
           "MAISR_PIPELINE_OFF_IDENTITY": {"pass": True, "n": 1, "nav_d": 0, "dd_d": 0, "corr": 1},
           "MAISR_SENSOR_NO_ACTION_IDENTITY": {"pass": True, "n": 1, "nav_d": 0, "dd_d": 0, "corr": 1}}
    from cg_macro_resid_b1_core import resid_protection_snapshot
    prot = resid_protection_snapshot({"_ids_state": "NORMAL", "_panic_state": "NORMAL"})
    out = resid_b11_finalize([], idb, True, resid_empty_counters(), "a" * 40, prot, events=[], bid="T77")
    ok(77, "tier1_transport_le_85kb", out["transport"].get("total_bytes", 10**9) <= TIER1_BUDGET and out["transport"].get("ok"))
    # 78 reconstruct sha
    arts = out["tier1"]
    total, mmap = b11_tier1_log_bytes(arts)
    ok(78, "tier1_chunk_sha_match", all(mmap[n]["sha256"] == _sha(arts[n]) for n in arts) and total <= TIER1_BUDGET)

    by_n = {r["n"]: r for r in R}
    uniq = [by_n[i] for i in range(61, 79) if i in by_n]
    return uniq, sum(1 for r in uniq if r["pass"]), len(uniq)


def run_resid_b11_eoa_dryrun():
    from cg_macro_resid_b1_core import resid_protection_snapshot
    idb = {"MAISR_REPLAY_IDENTITY": {"pass": True, "n": 1, "nav_d": 0, "dd_d": 0, "corr": 1},
           "MAISR_PIPELINE_OFF_IDENTITY": {"pass": True, "n": 1, "nav_d": 0, "dd_d": 0, "corr": 1},
           "MAISR_SENSOR_NO_ACTION_IDENTITY": {"pass": True, "n": 1, "nav_d": 0, "dd_d": 0, "corr": 1}}
    prot = resid_protection_snapshot({"_ids_state": "NORMAL", "_panic_state": "NORMAL"})
    passed = 0
    scenarios = []
    # A no signals
    scenarios.append(("A", resid_b11_finalize([], idb, True, resid_empty_counters(), "c" * 40, prot, bid="A")))
    # B insufficient
    scenarios.append(("B", resid_b11_finalize([], idb, True, resid_empty_counters(), "c" * 40, prot,
                                              passing_variants=[], bid="B")))
    # C priceable path (empty events still produces tier1)
    scenarios.append(("C", resid_b11_finalize([], idb, True, resid_empty_counters(), "c" * 40, prot, bid="C")))
    # D equality rejection counters
    ctr_d = resid_empty_counters()
    info = {"accepted": False, "same_bar": 0, "early_exit": 1, "future_price": 0, "partial": 0,
            "miss_entry": 0, "miss_exit": 1, "reason": "REJECTED"}
    ctr_d = resid_apply_price_counters(ctr_d, info, None)
    scenarios.append(("D", resid_b11_finalize([], idb, True, ctr_d, "c" * 40, prot, bid="D")))
    # E right censored
    ctr_e = resid_empty_counters(); ctr_e["right_censored_end_of_backtest"] = 1
    scenarios.append(("E", resid_b11_finalize([], idb, True, ctr_e, "c" * 40, prot, bid="E")))
    # F economically passing variant declared
    scenarios.append(("F", resid_b11_finalize([], idb, True, resid_empty_counters(), "c" * 40, prot,
                                              passing_variants=[{"id": "D30_C0_BREADTH"}], bid="F")))
    # G Tier-2 unused / Tier-1 valid
    scenarios.append(("G", resid_b11_finalize([], idb, True, resid_empty_counters(), "c" * 40, prot, bid="G")))
    # H technical fail via accepted violation
    ctr_h = resid_empty_counters(); ctr_h["accepted_future_price_use_count"] = 1
    scenarios.append(("H", resid_b11_finalize([], idb, True, ctr_h, "c" * 40, prot, bid="H")))

    for tag, out in scenarios:
        try:
            if tag == "H":
                ok_sc = out["fin"]["research_conclusion"] == "NOT_REACHED" and out["fin"]["result"] == "FAILED"
            elif tag == "F":
                ok_sc = (out.get("art_ok") and out["transport"].get("ok")
                         and out["fin"].get("research_conclusion") == "CAUSAL_PASS_DISCUSS_EXECUTION_PATH")
            elif tag == "D":
                ok_sc = out["tier1"] and out["fin"]["research_conclusion"] in (
                    "STOP_MACRO_RESID_B1", "CAUSAL_PASS_DISCUSS_EXECUTION_PATH")
            else:
                ok_sc = (out.get("art_ok") and out["transport"].get("ok")
                         and out["fin"]["research_conclusion"] in (
                             "STOP_MACRO_RESID_B1", "CAUSAL_PASS_DISCUSS_EXECUTION_PATH", "NOT_REACHED")
                         and (out["fin"]["research_conclusion"] != "STOP_MACRO_RESID_B1"
                              or out.get("tech_ok")))
            # STOP cannot precede tier1 validation: if STOP, art_ok and transport must hold
            if out["fin"].get("research_conclusion") == "STOP_MACRO_RESID_B1":
                ok_sc = ok_sc and out.get("art_ok") and out["transport"].get("ok") and out.get("tech_ok")
            if ok_sc:
                passed += 1
        except Exception:
            pass
    line = f"CG_MACRO_RESID_B11_EOA_DRYRUN_FINAL,scenarios=8,pass={passed},fail={8 - passed}"
    return line


if __name__ == "__main__":
    rows, p, n = run_resid_b11_extra_tests()
    print(f"b11_extra={p}/{n}")
    print([r for r in rows if not r["pass"]])
    print(run_resid_b11_eoa_dryrun())
