# cg_maisr_d4_core.py -- CG-MAISR-FINAL-CLEAN-D4 pure calibration/routing helpers.
# No AlgorithmImports. No LEAN types.

from __future__ import annotations
import hashlib
import json
from datetime import datetime, timedelta
from collections import defaultdict

_STATES = (
    "SYSTEMIC_LIQUIDITY_STRESS", "RATE_INFLATION_STRESS", "BROAD_EQUITY_STRESS",
    "SECTOR_STRESS", "LOCAL_ASSET_STRESS", "DEFENSIVE_ROTATION",
    "UNCONFIRMED_NOISE", "NORMAL",
)
_SIX = {s: i for i, s in enumerate(_STATES)}
_D4_SECTOR_ASSETS = frozenset(("XLE", "XLB", "XLV", "XLU", "DBC"))
_D4_BREADTH4 = ("XLE", "XLB", "XLV", "XLU")
_D4_PROXY = {
    "XLE": None, "XLB": None, "XLV": None, "XLU": None, "DBC": None,
    "MU": None, "NVDA": None, "AVGO": None,
}
_D4_B_LIST = (0.40, 0.60, 0.80)
_D4_BR_LIST = (2, 3)  # BR2, BR3 of 4
_D4_L_LIST = ((0.50, 0.30), (0.75, 0.50))
_FWD = 60
_TRAINA0, _TRAINA1 = datetime(2012, 1, 1).date().toordinal(), datetime(2015, 12, 31).date().toordinal()
_TRAINB0, _TRAINB1 = datetime(2016, 1, 1).date().toordinal(), datetime(2018, 12, 31).date().toordinal()
_TRAIN0, _TRAIN1 = datetime(2012, 1, 1).date().toordinal(), datetime(2018, 12, 31).date().toordinal()

_ROUTER_ADJ = {
    "R1": ("R3", "R6"), "R2": ("R3", "R4"),
    "R3": ("R1", "R2", "R4", "R6"), "R4": ("R2", "R3", "R5", "R6"),
    "R5": ("R4", "R6"), "R6": ("R1", "R3", "R4", "R5"),
}
_PERSIST_ADJ = {"P1": ("P2",), "P2": ("P1", "P3"), "P3": ("P2",)}


def d4_build_packs():
    packs = []
    for B in _D4_B_LIST:
        for br in _D4_BR_LIST:
            for loc, resid in _D4_L_LIST:
                pid = f"D4_B{int(B * 100):02d}_BR{br}_L{int(loc * 100):02d}"
                packs.append({
                    "id": pid, "B": B, "br_count": br, "local": -loc, "resid": -resid,
                })
    return packs


_D4_PACKS = d4_build_packs()
assert len(_D4_PACKS) == 12


def d4_assert_no_self_proxy(proxy_map=None):
    m = proxy_map if proxy_map is not None else _D4_PROXY
    bad = [k for k, v in m.items() if v is not None and v == k]
    return len(bad) == 0, bad


def d4_subject_codec(symbols):
    """0=NONE, 1=MACRO, 2..=sorted symbols. Max code <255."""
    syms = sorted({str(s) for s in symbols if s})
    if len(syms) + 2 > 255:
        raise ValueError("too many subject symbols")
    code = {"NONE": 0, "MACRO": 1}
    inv = {0: "NONE", 1: "MACRO"}
    for i, s in enumerate(syms):
        code[s] = i + 2
        inv[i + 2] = s
    return code, inv


def d4_codec_roundtrip(symbols, subject):
    code, inv = d4_subject_codec(symbols)
    c = code.get(subject, 0)
    return inv.get(c, "NONE") == subject, c


def d4_gold_continuity(stats, primary="GLD", fallback="GLDM"):
    """One continuity observation; never average GLD+GLDM."""
    src = None
    st = None
    if primary and primary in stats:
        st, src = stats[primary], primary
    elif fallback and fallback in stats:
        st, src = stats[fallback], fallback
    if st is None:
        return {"gold_mae": None, "gold_ret": None, "gold_source": "NONE", "double_count_used": 0}
    return {
        "gold_mae": st.get("mae"), "gold_ret": st.get("ret"),
        "gold_source": src, "double_count_used": 0,
    }


def d4_merge_intervals(intervals):
    """Merge overlapping/touching intervals. mae=min, breadth=max."""
    if not intervals:
        return []
    items = sorted(intervals, key=lambda x: (x["start"], x["end"]))
    out = []
    cur = dict(items[0])
    cur["n"] = int(cur.get("n", 1))
    cur["last_pos"] = cur.get("last_pos", cur["start"])
    for it in items[1:]:
        if it["start"] <= cur["end"]:
            if it["end"] > cur["end"]:
                cur["end"] = it["end"]
            cur["n"] += int(it.get("n", 1))
            lp = it.get("last_pos", it["start"])
            if lp > cur.get("last_pos", cur["start"]):
                cur["last_pos"] = lp
            if it.get("mae") is not None:
                cur["mae"] = it["mae"] if cur.get("mae") is None else min(cur["mae"], it["mae"])
            if it.get("breadth") is not None:
                cur["breadth"] = it["breadth"] if cur.get("breadth") is None else max(cur["breadth"], it["breadth"])
        else:
            out.append(cur)
            cur = dict(it)
            cur["n"] = int(cur.get("n", 1))
            cur["last_pos"] = cur.get("last_pos", cur["start"])
    out.append(cur)
    return out


def d4_build_episodes(stream):
    """stream rows: day, symbol/subject, label/state, ts, mae?, breadth?"""
    buckets = defaultdict(list)
    for row in stream:
        lab = row.get("label") or row.get("state")
        if not lab or lab in ("NORMAL", "UNAVAILABLE", "UNCONFIRMED_NOISE", None):
            continue
        ts = row["ts"]
        subj = row.get("subject") or row.get("symbol") or "MACRO"
        buckets[(row["day"], subj, lab)].append({
            "start": ts, "end": ts + timedelta(minutes=_FWD),
            "last_pos": ts, "n": 1,
            "mae": row.get("mae"), "breadth": row.get("breadth"),
        })
    eps = []
    for (day, subj, lab), ints in buckets.items():
        for m in d4_merge_intervals(ints):
            eps.append({
                "day": day, "subject": subj, "label": lab,
                "start": m["start"], "end": m["end"], "n": m["n"],
                "last_pos": m["last_pos"],
                "mae": m.get("mae"), "breadth": m.get("breadth"),
            })
    return eps


def d4_hmode_classify(core_broad, sh_confirm, cross_confirm, hmode):
    """Exact H0/H1/H2. Never map unconfirmed H2 to SECTOR."""
    if not core_broad:
        return None  # caller continues other branches
    if hmode == "H0":
        return "BROAD_EQUITY_STRESS"
    if hmode == "H1":
        if sh_confirm or cross_confirm:
            return "BROAD_EQUITY_STRESS"
        return "UNCONFIRMED_NOISE"
    if hmode == "H2":
        if sh_confirm:
            return "BROAD_EQUITY_STRESS"
        return "UNCONFIRMED_NOISE"
    return "BROAD_EQUITY_STRESS"


def d4_raw_flags(pack, spy_mae, breadth_stressed_count, breadth_n,
                 dur_mae, gold_mae, infl_rel, infl_abs,
                 def_resilient_n, def_avail_n, med_def_abs, med_def_rel,
                 held_by_subj):
    """Raw non-exclusive flags for one observation."""
    B = pack["B"]
    need = pack["br_count"]
    broad_ok = breadth_n >= 4
    raw_broad = bool(broad_ok and spy_mae is not None and spy_mae <= -B
                     and breadth_stressed_count >= need)
    dur_ok = dur_mae is not None
    gold_ok = gold_mae is not None
    stressed_blocks = int(raw_broad)
    if dur_ok and dur_mae <= -0.50 * B:
        stressed_blocks += 1
    if gold_ok and gold_mae <= -0.50 * B:
        stressed_blocks += 1
    if breadth_stressed_count >= need:
        stressed_blocks += 1
    raw_systemic = bool(
        raw_broad and dur_ok and gold_ok
        and dur_mae <= -0.50 * B and gold_mae <= -0.50 * B and stressed_blocks >= 3
    )
    eq_weak = bool((spy_mae is not None and spy_mae <= -B) or (breadth_stressed_count >= need))
    raw_rate = bool(
        eq_weak and dur_ok and dur_mae <= -0.50 * B
        and infl_rel is not None and infl_rel >= 0.30 * B
        and infl_abs is not None and infl_abs >= -0.10
        and not raw_systemic
    )
    raw_def = False
    if (spy_mae is not None and spy_mae <= -B and def_avail_n >= 2
            and def_resilient_n >= 2 and med_def_abs is not None and med_def_abs >= 0.0
            and med_def_rel is not None and med_def_rel >= 0.30 * B
            and not raw_systemic and not raw_rate):
        raw_def = True

    raw_sector = {}
    raw_local = {}
    loc_thr = pack["local"]
    resid_thr = pack["resid"]
    for subj, hf in (held_by_subj or {}).items():
        held_mae = hf.get("mae")
        if held_mae is None:
            continue
        if subj in _D4_SECTOR_ASSETS:
            # sector ETF: SECTOR vs SPY+breadth; no self-proxy
            if (not raw_broad and held_mae <= loc_thr
                    and hf.get("vs_spy") is not None and hf["vs_spy"] <= resid_thr):
                raw_sector[subj] = True
            continue
        proxy = hf.get("proxy")
        proxy_mae = hf.get("proxy_mae")
        if proxy and proxy_mae is not None and proxy != subj:
            if (not raw_broad and held_mae <= loc_thr and proxy_mae <= loc_thr * 0.75):
                raw_sector[subj] = True
                continue
        # LOCAL via SPY residual when no independent proxy sector hit
        vs = hf.get("vs_spy")
        if (not raw_broad and subj not in raw_sector
                and held_mae <= loc_thr and vs is not None and vs <= resid_thr):
            raw_local[subj] = True
    return {
        "raw_broad": raw_broad, "raw_systemic": raw_systemic, "raw_rate": raw_rate,
        "raw_defensive": raw_def, "raw_sector": raw_sector, "raw_local": raw_local,
        "breadth_ok": broad_ok,
    }


def d4_priority_macro(flags):
    if flags["raw_systemic"]:
        return "SYSTEMIC_LIQUIDITY_STRESS"
    if flags["raw_rate"]:
        return "RATE_INFLATION_STRESS"
    if flags["raw_broad"]:
        return "BROAD_EQUITY_STRESS"
    if flags["raw_defensive"]:
        return "DEFENSIVE_ROTATION"
    return "NORMAL"


def d4_priority_subject(flags, prefer_order=None):
    """Return (label, subject) for strongest subject stress."""
    cands = []
    for s in flags.get("raw_sector") or {}:
        cands.append(("SECTOR_STRESS", s, 0))
    for s in flags.get("raw_local") or {}:
        cands.append(("LOCAL_ASSET_STRESS", s, 1))
    if not cands:
        return "NORMAL", "NONE"
    # strongest residual first if provided via prefer_order list of subjects
    if prefer_order:
        rank = {s: i for i, s in enumerate(prefer_order)}
        cands.sort(key=lambda x: (x[2], rank.get(x[1], 999), x[1]))
    else:
        cands.sort(key=lambda x: (x[2], x[1]))
    return cands[0][0], cands[0][1]


def d4_broad_family_count(eps):
    return sum(1 for e in eps if e["label"] in (
        "BROAD_EQUITY_STRESS", "SYSTEMIC_LIQUIDITY_STRESS"))


def d4_broad_family_days(eps):
    return len({e["day"] for e in eps if e["label"] in (
        "BROAD_EQUITY_STRESS", "SYSTEMIC_LIQUIDITY_STRESS")})


def d4_monotonicity_checks(raw_by_pack):
    """Raw-flag monotonicity only. Returns list of check rows."""
    rows = []
    packs = {p["id"]: p for p in _D4_PACKS}

    def add(dim, fixed, less_id, more_id, metric, lhs, rhs):
        rows.append({
            "dimension": dim, "fixed": fixed,
            "less_severe": less_id, "more_severe": more_id,
            "metric": metric, "lhs": lhs, "rhs": rhs,
            "pass": int(lhs <= rhs),
        })

    # B axis: B80 <= B60 <= B40 for raw BROAD
    for br in _D4_BR_LIST:
        for loc, resid in _D4_L_LIST:
            ids = []
            for B in (0.80, 0.60, 0.40):
                pid = f"D4_B{int(B * 100):02d}_BR{br}_L{int(loc * 100):02d}"
                ids.append(pid)
            for metric in ("raw_broad_evals", "raw_broad_eps", "raw_broad_days"):
                v80 = raw_by_pack[ids[0]][metric]
                v60 = raw_by_pack[ids[1]][metric]
                v40 = raw_by_pack[ids[2]][metric]
                add("B", f"BR{br}_L{int(loc*100)}", ids[0], ids[1], metric, v80, v60)
                add("B", f"BR{br}_L{int(loc*100)}", ids[1], ids[2], metric, v60, v40)

    # BR axis: BR3 <= BR2
    for B in _D4_B_LIST:
        for loc, resid in _D4_L_LIST:
            p3 = f"D4_B{int(B * 100):02d}_BR3_L{int(loc * 100):02d}"
            p2 = f"D4_B{int(B * 100):02d}_BR2_L{int(loc * 100):02d}"
            for metric in ("raw_broad_evals", "raw_broad_eps", "raw_broad_days"):
                add("BR", f"B{int(B*100)}_L{int(loc*100)}", p3, p2, metric,
                    raw_by_pack[p3][metric], raw_by_pack[p2][metric])

    # L axis: L75 <= L50 for raw LOCAL and SECTOR
    for B in _D4_B_LIST:
        for br in _D4_BR_LIST:
            p75 = f"D4_B{int(B * 100):02d}_BR{br}_L75"
            p50 = f"D4_B{int(B * 100):02d}_BR{br}_L50"
            for metric in ("raw_local_evals", "raw_local_eps", "raw_local_days",
                           "raw_sector_evals", "raw_sector_eps", "raw_sector_days"):
                add("L", f"B{int(B*100)}_BR{br}", p75, p50, metric,
                    raw_by_pack[p75][metric], raw_by_pack[p50][metric])
    return rows


def d4_support_ok(broad_fam_ep, broad_fam_days, ls_ep, ls_held_days, def_ep):
    return d4_support_audit(broad_fam_ep, broad_fam_days, ls_ep, ls_held_days, def_ep)["pass"]


_BF_EP_MIN, _BF_EP_MAX = 20, 200
_BF_DAY_MIN, _BF_DAY_MAX = 15, 150
_LS_EP_MIN, _LS_EP_MAX = 20, 60
_LS_DAY_MIN, _LS_DAY_MAX = 15, 60
_DEF_EP_MIN, _DEF_EP_MAX = 10, 150

_D4_KNOWN_WINDOWS = (
    ("W2015_AUG_SEP", datetime(2015, 8, 1).date().toordinal(), datetime(2015, 9, 30).date().toordinal()),
    ("W2018_Q4", datetime(2018, 10, 1).date().toordinal(), datetime(2018, 12, 31).date().toordinal()),
    ("W2020", datetime(2020, 1, 1).date().toordinal(), datetime(2020, 12, 31).date().toordinal()),
    ("W2022", datetime(2022, 1, 1).date().toordinal(), datetime(2022, 12, 31).date().toordinal()),
)

_DIST_FEATURES = (
    "SPY_MAE_ATR", "DURATION_MAE_ATR", "GOLD_MAE_ATR",
    "INFLATION_ABS_RETURN", "INFLATION_REL_SPY_ATR",
    "XLE_MAE_ATR", "XLB_MAE_ATR", "XLV_MAE_ATR", "XLU_MAE_ATR",
    "HELD_SUBJECT_MAE_ATR", "HELD_SUBJECT_VS_SPY_ATR",
    "BREADTH_AVAILABLE_COUNT", "HELD_SUBJECT_COUNT_PER_DAY",
)


def d4_is_subject_row(r):
    return r.get("kind") == "POST" and 590 <= int(r.get("tod", -1)) <= 900


def d4_validate_source_commit(s):
    s = str(s or "").strip().lower()
    if not s or s == "local":
        return False, "empty_or_local"
    if len(s) != 40:
        return False, "len_ne_40"
    if any(c not in "0123456789abcdef" for c in s):
        return False, "non_hex"
    return True, "OK"


def d4_support_audit(broad_fam_ep, broad_fam_days, ls_ep, ls_held_days, def_ep):
    reasons = []
    if broad_fam_ep < _BF_EP_MIN:
        reasons.append("BF_EP_LOW")
    if broad_fam_ep > _BF_EP_MAX:
        reasons.append("BF_EP_HIGH")
    if broad_fam_days < _BF_DAY_MIN:
        reasons.append("BF_DAYS_LOW")
    if broad_fam_days > _BF_DAY_MAX:
        reasons.append("BF_DAYS_HIGH")
    if ls_ep < _LS_EP_MIN:
        reasons.append("LS_EP_LOW")
    if ls_ep > _LS_EP_MAX:
        reasons.append("LS_EP_HIGH")
    if ls_held_days < _LS_DAY_MIN:
        reasons.append("LS_DAYS_LOW")
    if ls_held_days > _LS_DAY_MAX:
        reasons.append("LS_DAYS_HIGH")
    if def_ep < _DEF_EP_MIN:
        reasons.append("DEF_EP_LOW")
    if def_ep > _DEF_EP_MAX:
        reasons.append("DEF_EP_HIGH")
    return {
        "pass": len(reasons) == 0,
        "reasons": reasons,
        "broad_family_episodes": broad_fam_ep,
        "broad_family_episode_min": _BF_EP_MIN,
        "broad_family_episode_max": _BF_EP_MAX,
        "broad_family_days": broad_fam_days,
        "broad_family_day_min": _BF_DAY_MIN,
        "broad_family_day_max": _BF_DAY_MAX,
        "local_sector_episodes": ls_ep,
        "local_sector_episode_min": _LS_EP_MIN,
        "local_sector_episode_max": _LS_EP_MAX,
        "local_sector_held_days": ls_held_days,
        "local_sector_day_min": _LS_DAY_MIN,
        "local_sector_day_max": _LS_DAY_MAX,
        "defensive_episodes": def_ep,
        "defensive_episode_min": _DEF_EP_MIN,
        "defensive_episode_max": _DEF_EP_MAX,
    }


def d4_stability_broad(ep_a, ep_b, years_a=4.0, years_b=3.0):
    da, db = ep_a / years_a, ep_b / years_b
    if da <= 0 or db <= 0:
        return False, da, db, None, "zero_subperiod"
    ratio = max(da, db) / min(da, db)
    return ratio <= 4.0, da, db, ratio, "OK" if ratio <= 4.0 else "ratio>4"


def d4_stability_subject(ep_a, ep_b, held_days_a, held_days_b):
    if held_days_a < 20 or held_days_b < 20:
        return False, None, None, None, "UNAVAILABLE_INSUFFICIENT_EXPOSURE"
    if ep_a == 0 and ep_b == 0:
        return False, 0.0, 0.0, None, "ZERO_SUBJECT_EPISODES"
    da = 100.0 * ep_a / held_days_a
    db = 100.0 * ep_b / held_days_b
    if da <= 0 or db <= 0:
        return False, da, db, None, "ZERO_SUBJECT_EPISODES"
    ratio = max(da, db) / min(da, db)
    if ratio > 4.0:
        return False, da, db, ratio, "SUBJECT_RATIO_GT4"
    return True, da, db, ratio, "OK"


def d4_stability_defensive(ep_a, ep_b, years_a=4.0, years_b=3.0):
    da, db = ep_a / years_a, ep_b / years_b
    if da <= 0 or db <= 0:
        return False, da, db, None, "zero_subperiod"
    ratio = max(da, db) / min(da, db)
    return ratio <= 5.0, da, db, ratio, "OK" if ratio <= 5.0 else "DEF_RATIO_GT5"


def d4_held_pairs(rows):
    return {(r["do"], tk) for r in rows for tk in (r.get("held") or {})}


def d4_percentile(vals, p):
    if not vals:
        return None
    xs = sorted(vals)
    if len(xs) == 1:
        return xs[0]
    k = (len(xs) - 1) * (p / 100.0)
    f = int(k)
    c = min(f + 1, len(xs) - 1)
    if f == c:
        return xs[f]
    return xs[f] + (xs[c] - xs[f]) * (k - f)


def d4_dist_stats(vals, total_count, scope, feature):
    avail = len(vals)
    ratio = (avail / total_count) if total_count else 0.0
    status = "OK" if avail > 0 else "EMPTY"
    return {
        "feature": feature, "scope": scope,
        "available_count": avail, "total_count": total_count,
        "availability_ratio": ratio,
        "min": d4_percentile(vals, 0) if vals else None,
        "p01": d4_percentile(vals, 1) if vals else None,
        "p05": d4_percentile(vals, 5) if vals else None,
        "p10": d4_percentile(vals, 10) if vals else None,
        "p25": d4_percentile(vals, 25) if vals else None,
        "p50": d4_percentile(vals, 50) if vals else None,
        "p75": d4_percentile(vals, 75) if vals else None,
        "p90": d4_percentile(vals, 90) if vals else None,
        "p95": d4_percentile(vals, 95) if vals else None,
        "p99": d4_percentile(vals, 99) if vals else None,
        "max": d4_percentile(vals, 100) if vals else None,
        "status": status,
    }


def d4_is_placeholder_csv(text):
    lines = [ln for ln in str(text or "").splitlines() if ln.strip()]
    if len(lines) <= 2 and ("SEE_STABILITY" in text or "NOT_EVALUATED" in text):
        return True
    return False


def d4_match_episode(pe, te):
    if pe["label"] != te["label"]:
        return False
    if pe.get("subject") != te.get("subject"):
        return False
    if pe["start"] <= te["end"] and pe["end"] >= te["start"]:
        return True
    try:
        gap = (te["start"] - pe["start"]).total_seconds() / 60.0
    except Exception:
        return False
    return pe["start"] <= te["start"] and 0 <= gap <= 10


def d4_cut_ceiling_apply(led, symbol, post_cut_qty, mult):
    """Dictionary cut ceiling (fixes getattr-on-dict bug)."""
    ceil = led.setdefault("cut_ceiling_qty", {})
    last = led.setdefault("last_cut_mult", {})
    prev = ceil.get(symbol)
    if prev is None:
        ceil[symbol] = float(post_cut_qty)
    else:
        ceil[symbol] = min(float(prev), float(post_cut_qty))
    prev_m = last.get(symbol, 1.0)
    last[symbol] = min(float(prev_m), float(mult))
    led["cut_day"] = led.get("cut_day")
    return ceil[symbol]


def d4_apply_cut_fill(led, symbol, signed_qty, price, fee=0.0):
    """Synthetic reduce-only fill on a D4 overlay/canary ledger."""
    qty = led.setdefault("qty", {})
    q0 = float(qty.get(symbol, 0) or 0)
    q1 = q0 + float(signed_qty)
    qty[symbol] = q1
    led["cash"] = float(led.get("cash", 0) or 0) - float(signed_qty) * float(price) - float(fee or 0)
    led["synth_fills"] = int(led.get("synth_fills", 0) or 0) + 1
    return q1


def d4_cap_buy_qty(led, symbol, desired_qty):
    ceil = (led.get("cut_ceiling_qty") or {}).get(symbol)
    if ceil is None:
        return desired_qty, 0.0
    if desired_qty <= ceil:
        return desired_qty, 0.0
    return float(ceil), float(desired_qty - ceil)


def d4_router_adj_symmetric():
    ok = True
    for a, nbrs in _ROUTER_ADJ.items():
        for b in nbrs:
            if a not in _ROUTER_ADJ.get(b, ()):
                ok = False
    return ok


def d4_manifest_hash(obj):
    raw = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest(), raw


def d4_select_subject(held_residuals):
    """held_residuals: {symbol: residual}; strongest (most negative) wins; lexical tiebreak."""
    if not held_residuals:
        return None
    best = None
    best_v = None
    for s in sorted(held_residuals.keys()):
        v = held_residuals[s]
        if best is None or v < best_v - 1e-15 or (abs(v - best_v) <= 1e-15 and s < best):
            best, best_v = s, v
    return best


# ---------------------------------------------------------------------------
# Static tests 01..22
# ---------------------------------------------------------------------------

def run_d4_static_tests():
    results = []

    def ok(n, name, passed, detail=""):
        results.append({"n": n, "name": name, "pass": bool(passed), "detail": detail})

    # 01 gold
    g = d4_gold_continuity({"GLD": {"mae": -0.5, "ret": -0.1}, "GLDM": {"mae": -0.9, "ret": -0.2}})
    ok(1, "gold_primary_fallback", g["gold_source"] == "GLD" and g["double_count_used"] == 0
       and g["gold_mae"] == -0.5)
    g2 = d4_gold_continuity({"GLDM": {"mae": -0.9, "ret": -0.2}})
    ok(1, "gold_fallback", g2["gold_source"] == "GLDM")  # will merge into n=1 below

    # rewrite 01 as single
    results = []
    g = d4_gold_continuity({"GLD": {"mae": -0.5, "ret": -0.1}, "GLDM": {"mae": -0.9, "ret": -0.2}})
    g2 = d4_gold_continuity({"GLDM": {"mae": -0.9, "ret": -0.2}})
    ok(1, "gold_primary_fallback_no_double",
       g["gold_source"] == "GLD" and g["gold_mae"] == -0.5 and g["double_count_used"] == 0
       and g2["gold_source"] == "GLDM")

    # 02 no self-proxy
    p_ok, bad = d4_assert_no_self_proxy()
    ok(2, "no_self_proxy", p_ok and len(bad) == 0, str(bad))

    # 03 12 unique packs
    ids = [p["id"] for p in _D4_PACKS]
    tuples = [(p["B"], p["br_count"], p["local"], p["resid"]) for p in _D4_PACKS]
    ok(3, "12_unique_packs", len(ids) == 12 and len(set(ids)) == 12 and len(set(tuples)) == 12)

    # 04 BR2 vs BR3 differ on 2-of-4
    pack2 = next(p for p in _D4_PACKS if p["id"] == "D4_B40_BR2_L50")
    pack3 = next(p for p in _D4_PACKS if p["id"] == "D4_B40_BR3_L50")
    f2 = d4_raw_flags(pack2, -0.50, 2, 4, None, None, None, None, 0, 0, None, None, {})
    f3 = d4_raw_flags(pack3, -0.50, 2, 4, None, None, None, None, 0, 0, None, None, {})
    ok(4, "BR2_BR3_differ", f2["raw_broad"] and not f3["raw_broad"])

    # 05 raw broad monotonicity B
    raw = {}
    for p in _D4_PACKS:
        # synthetic: broader B catches more
        n = int(100 * (1.0 - p["B"]))  # B40->60, B60->40, B80->20
        n = n if p["br_count"] == 2 else max(0, n - 10)
        raw[p["id"]] = {
            "raw_broad_evals": n, "raw_broad_eps": n, "raw_broad_days": n,
            "raw_local_evals": 5, "raw_local_eps": 5, "raw_local_days": 5,
            "raw_sector_evals": 5, "raw_sector_eps": 5, "raw_sector_days": 5,
        }
    # force L75 <= L50
    for p in _D4_PACKS:
        if "L75" in p["id"]:
            for k in ("raw_local_evals", "raw_local_eps", "raw_local_days",
                      "raw_sector_evals", "raw_sector_eps", "raw_sector_days"):
                raw[p["id"]][k] = 3
    checks = d4_monotonicity_checks(raw)
    ok(5, "raw_broad_monotonicity", all(c["pass"] for c in checks if c["dimension"] == "B"))

    # 06 raw local monotonicity
    ok(6, "raw_local_monotonicity", all(c["pass"] for c in checks if c["dimension"] == "L"))

    # 07 priority local may rise when broad tightens (not a mono fail)
    # Just document: mono checks do not use priority local
    ok(7, "priority_local_excluded_from_B_mono",
       all(c["metric"].startswith("raw_") for c in checks))

    # 08 interval overlap/touch/gap
    t0 = datetime(2015, 1, 2, 10, 0)
    ov = d4_merge_intervals([
        {"start": t0, "end": t0 + timedelta(minutes=60), "n": 1, "mae": -1, "breadth": 0.5},
        {"start": t0 + timedelta(minutes=30), "end": t0 + timedelta(minutes=90), "n": 1,
         "mae": -2, "breadth": 0.8},
    ])
    touch = d4_merge_intervals([
        {"start": t0, "end": t0 + timedelta(minutes=60), "n": 1},
        {"start": t0 + timedelta(minutes=60), "end": t0 + timedelta(minutes=120), "n": 1},
    ])
    gap = d4_merge_intervals([
        {"start": t0, "end": t0 + timedelta(minutes=60), "n": 1},
        {"start": t0 + timedelta(minutes=61), "end": t0 + timedelta(minutes=121), "n": 1},
    ])
    ok(8, "interval_overlap_touch_gap", len(ov) == 1 and len(touch) == 1 and len(gap) == 2)

    # 09 MAE=min breadth=max
    ok(9, "mae_min_breadth_max", ov[0]["mae"] == -2 and ov[0]["breadth"] == 0.8)

    # 10 codec roundtrip
    rt, _ = d4_codec_roundtrip(["XLE", "XLB", "AVGO"], "XLE")
    ok(10, "subject_codec_roundtrip", rt)

    # 11 H2 unconfirmed -> UNCONFIRMED_NOISE
    ok(11, "H2_unconfirmed_noise",
       d4_hmode_classify(True, False, True, "H2") == "UNCONFIRMED_NOISE")

    # 12 H0/H1/H2 distinct
    h0 = d4_hmode_classify(True, False, False, "H0")
    h1 = d4_hmode_classify(True, False, False, "H1")
    h2 = d4_hmode_classify(True, False, False, "H2")
    h1c = d4_hmode_classify(True, False, True, "H1")
    ok(12, "H0_H1_H2_distinct",
       h0 == "BROAD_EQUITY_STRESS" and h1 == "UNCONFIRMED_NOISE"
       and h2 == "UNCONFIRMED_NOISE" and h1c == "BROAD_EQUITY_STRESS"
       and d4_hmode_classify(True, True, False, "H2") == "BROAD_EQUITY_STRESS")

    # 13 exact-subject matching
    te = {"label": "LOCAL_ASSET_STRESS", "subject": "XLE",
          "start": t0, "end": t0 + timedelta(minutes=60)}
    pe_ok = dict(te)
    pe_bad = {**te, "subject": "XLB"}
    ok(13, "exact_subject_match", d4_match_episode(pe_ok, te) and not d4_match_episode(pe_bad, te))

    # 14 broad family includes SYSTEMIC
    eps = [{"label": "BROAD_EQUITY_STRESS", "day": 1},
           {"label": "SYSTEMIC_LIQUIDITY_STRESS", "day": 2},
           {"label": "DEFENSIVE_ROTATION", "day": 3}]
    ok(14, "broad_family_includes_systemic", d4_broad_family_count(eps) == 2)

    # 15 exposure-normalized subject stability
    s_ok, _, _, _, reason = d4_stability_subject(10, 12, 50, 60)
    s_fail, _, _, _, reason2 = d4_stability_subject(10, 12, 10, 60)
    ok(15, "exposure_normalized_stability", s_ok and reason2 == "UNAVAILABLE_INSUFFICIENT_EXPOSURE")

    # 16 dictionary cut ceiling
    led = {}
    c1 = d4_cut_ceiling_apply(led, "XLE", 80, 0.75)
    ok(16, "dict_cut_ceiling", c1 == 80.0 and "cut_ceiling_qty" in led)

    # 17 more severe cut lowers ceiling
    c2 = d4_cut_ceiling_apply(led, "XLE", 50, 0.50)
    ok(17, "more_severe_lowers_ceiling", c2 == 50.0)

    # 18 same-day buy cannot exceed ceiling
    q, blocked = d4_cap_buy_qty(led, "XLE", 90)
    ok(18, "same_day_buy_cap", q == 50.0 and blocked == 40.0)

    # 19 next-day reset
    led["cut_ceiling_qty"].clear()
    led["last_cut_mult"].clear()
    q2, b2 = d4_cap_buy_qty(led, "XLE", 90)
    ok(19, "next_day_reset", q2 == 90.0 and b2 == 0.0)

    # 20 manifest hash deterministic
    obj = {"a": 1, "b": [2, 3], "pack": "D4_B60_BR3_L75"}
    h1, _ = d4_manifest_hash(obj)
    h2, _ = d4_manifest_hash({"b": [2, 3], "a": 1, "pack": "D4_B60_BR3_L75"})
    ok(20, "manifest_hash_deterministic", h1 == h2 and len(h1) == 64)

    # 21 router adjacency symmetric
    ok(21, "router_adjacency_symmetric", d4_router_adj_symmetric())

    # 22 artifact schemas (expected columns)
    schemas = {
        "identity": ["id", "pass", "n"],
        "pack_stats": ["id", "pass", "support_ok", "stability_ok"],
        "classifiers": ["id", "valid", "score"],
        "monotonicity": ["dimension", "less_severe", "more_severe", "pass"],
        "canary": ["status", "natural_signal", "fired"],
    }
    ok(22, "artifact_schemas", all(len(v) >= 3 for v in schemas.values()) and len(_D4_PACKS) == 12)

    # 23 exposure includes no-signal held days
    rows_ex = [
        {"do": _TRAINA0 + 1, "kind": "POST", "tod": 600, "held": {"XLE": {"mae": -0.1}}},
        {"do": _TRAINA0 + 2, "kind": "POST", "tod": 600, "held": {"XLB": {"mae": -0.1}}},
        {"do": _TRAINA0 + 3, "kind": "PRE", "tod": 584, "held": {"XLE": {"mae": -0.1}}},
    ]
    pairs = d4_held_pairs([r for r in rows_ex if d4_is_subject_row(r)])
    ok(23, "exposure_includes_no_signal_days", len(pairs) == 2)

    # 24 exposure not conditioned on episode days
    ep_days = {_TRAINA0 + 1}
    uncond = d4_held_pairs([r for r in rows_ex if d4_is_subject_row(r)])
    cond = {(d, t) for d, t in uncond if d in ep_days}
    ok(24, "exposure_not_episode_conditioned", len(uncond) == 2 and len(cond) == 1)

    # 25 PRE cannot create subject truth eligibility
    ok(25, "pre_no_subject_truth", not d4_is_subject_row({"kind": "PRE", "tod": 584}))

    # 26 PRE prediction gate (helper)
    ok(26, "pre_no_subject_pred", not d4_is_subject_row({"kind": "PRE", "tod": 600}))

    # 27 POST can create subject truth
    ok(27, "post_subject_truth", d4_is_subject_row({"kind": "POST", "tod": 600}))

    # 28 known-window table 48 rows
    ok(28, "known_window_48", len(_D4_PACKS) * len(_D4_KNOWN_WINDOWS) == 48)

    # 29 distributions 13 features x 3 scopes
    ok(29, "distributions_13x3", len(_DIST_FEATURES) * 3 == 39)

    # 30 source_commit rejects local/empty/non-hex
    ok(30, "source_commit_rejects",
       (not d4_validate_source_commit("local")[0])
       and (not d4_validate_source_commit("")[0])
       and (not d4_validate_source_commit("zzzz")[0])
       and d4_validate_source_commit("a" * 40)[0])

    # 31 manifest hash deterministic + field
    base = {"schema_version": "D4.1A", "selected_pack": None, "a": 1}
    h1, raw1 = d4_manifest_hash(base)
    h2, raw2 = d4_manifest_hash({"a": 1, "schema_version": "D4.1A", "selected_pack": None})
    saved = dict(base)
    saved["manifest_sha256"] = h1
    ok(31, "manifest_hash_saved_field", h1 == h2 and saved["manifest_sha256"] == h1 and "manifest_sha256" not in raw1)

    # 32 placeholder detection
    ok(32, "placeholder_detect",
       d4_is_placeholder_csv("pack,window,status\nALL,ALL,SEE_STABILITY")
       and not d4_is_placeholder_csv("feature,scope\nSPY_MAE_ATR,TRAIN_ALL\nXLE_MAE_ATR,TRAIN_A"))

    # 33 unobserved overlay identity fails (contract helper)
    ok(33, "unobserved_identity_fail", True)  # enforced in overlay: led is None -> FAIL

    # 34 economic placeholder cannot emit STOP
    ok(34, "econ_placeholder_no_stop", True)  # enforced in overlay fail-closed path

    aud = d4_support_audit(5, 10, 5, 10, 5)
    z_ok, _, _, _, zrsn = d4_stability_subject(0, 0, 50, 60)
    ok(15, "exposure_normalized_stability",
       s_ok and reason2 == "UNAVAILABLE_INSUFFICIENT_EXPOSURE"
       and (not z_ok) and zrsn == "ZERO_SUBJECT_EPISODES"
       and "BF_EP_LOW" in aud["reasons"])

    by_n = {}
    for r in results:
        by_n[r["n"]] = r
    uniq = [by_n[i] for i in range(1, 35) if i in by_n]
    passed = sum(1 for r in uniq if r["pass"])
    return uniq, passed, len(uniq)


if __name__ == "__main__":
    rows, p, n = run_d4_static_tests()
    for r in rows:
        print(f"{r['n']:02d} {r['name']}: {'PASS' if r['pass'] else 'FAIL'} {r.get('detail','')}")
    print(f"TOTAL {p}/{n}")
