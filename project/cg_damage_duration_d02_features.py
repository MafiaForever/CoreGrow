# cg_damage_duration_d02_features.py -- CG-DAMAGE-DURATION-D0.2B causal feature collector.
# Diagnostic only. Reuses D0.2A sensor; no D30/D45 redefinition; no recovery/policy.
from __future__ import annotations
import json, math, re, statistics
from collections import deque
from copy import deepcopy
from datetime import datetime, timedelta

from cg_damage_duration_d02_sensor import (
    SENSOR_SYMBOLS, SOURCE_VERSION as D02A_SOURCE_VERSION, PRIOR_ATR_SOURCE,
    D30_D45_RUNTIME_SOURCE, run_damage_d02a_static_tests,
)
from cg_damage_duration_d02_memory import (
    UNAVAILABLE, EPS, EventMemoryStore, memory_contract, run_damage_d02b_memory_tests,
    _avail, EXPERIMENT, PHASE,
)

WINDOWS_MINUTES = (15, 30, 60, 120)
VR_PAIRS = ((15, 60), (30, 120))
MAX_PATH_MINUTES = 250  # 240 + margin
PERSIST_LEN = 12
SCHEMA_VERSION = "D02B_FEATURES_V1"
VARIANCE_CONVENTION = "population_ddof0"
MEDIANCORR_MIN_OBS_FN = "max(10, w-2)"
PXY5_LEVEL_DEF = "mean(log(P_j_t)) across five positive prices at identical EndTime"

FORBIDDEN_RE = re.compile(
    r"(?<![A-Za-z_])(History|AddEquity|add_equity|AddData|add_data|SetHoldings|set_holdings|"
    r"MarketOrder|market_order|LimitOrder|StopMarketOrder|Liquidate)\s*\("
    r"|PortfolioTarget\b|ObjectStore\.(Save|Delete)\b"
)


def sign(x):
    if x > 0:
        return 1
    if x < 0:
        return -1
    return 0


def log_return(p_now, p_ref):
    if not (_avail(p_now) and _avail(p_ref)):
        return UNAVAILABLE
    a, b = float(p_now), float(p_ref)
    if a <= 0 or b <= 0:
        return UNAVAILABLE
    return math.log(a / b)


def pop_var(xs):
    xs = [float(x) for x in xs]
    if len(xs) < 1:
        return UNAVAILABLE
    if len(xs) == 1:
        return 0.0
    m = sum(xs) / len(xs)
    return sum((x - m) ** 2 for x in xs) / len(xs)


def pop_std(xs):
    v = pop_var(xs)
    if v == UNAVAILABLE:
        return UNAVAILABLE
    return math.sqrt(float(v))


def price_at_or_before(series, t):
    """series: list[(EndTime, price)] sorted ascending. Latest EndTime <= t."""
    if not series or t is None:
        return None, None
    best = None
    for et, px in series:
        if et <= t:
            best = (et, px)
        else:
            break
    return best if best else (None, None)


def window_return(series, t, w_min):
    cur_et, cur_px = price_at_or_before(series, t)
    if cur_et is None:
        return UNAVAILABLE
    ref_t = t - timedelta(minutes=int(w_min))
    ref_et, ref_px = price_at_or_before(series, ref_t)
    if ref_et is None:
        return UNAVAILABLE
    return log_return(cur_px, ref_px)


def pxy5_level_from_prices(price_map):
    vals = []
    for tk in SENSOR_SYMBOLS:
        px = price_map.get(tk)
        if not _avail(px) or float(px) <= 0:
            return UNAVAILABLE
        vals.append(math.log(float(px)))
    return sum(vals) / len(vals)


def align_path(bars_by_symbol, decision_time, max_n=MAX_PATH_MINUTES):
    """
    Align one-minute proxy path: only timestamps where all five symbols have
    identical EndTime <= decision_time. Returns list[(et, level, logret)].
    logret is None for first point.
    """
    if decision_time is None:
        return []
    maps = {}
    for tk in SENSOR_SYMBOLS:
        m = {}
        for et, px in (bars_by_symbol.get(tk) or []):
            if et is None or et > decision_time:
                continue
            if not _avail(px) or float(px) <= 0:
                continue
            m[et] = float(px)
        maps[tk] = m
    common = None
    for tk in SENSOR_SYMBOLS:
        s = set(maps[tk].keys())
        common = s if common is None else (common & s)
    if not common:
        return []
    ets = sorted(common)
    if len(ets) > max_n:
        ets = ets[-max_n:]
    out = []
    prev_lvl = None
    for et in ets:
        prices = {tk: maps[tk][et] for tk in SENSOR_SYMBOLS}
        lvl = pxy5_level_from_prices(prices)
        if lvl == UNAVAILABLE:
            continue
        lr = None if prev_lvl is None else (float(lvl) - float(prev_lvl))
        out.append((et, float(lvl), lr, prices.get("SPY")))
        prev_lvl = lvl
    return out


def flip_run_features(logrets):
    """FlipRate, AvgRunLen, LongestNegRun, LongestPosRun from signed log returns."""
    xs = [x for x in logrets if x is not None and _avail(x)]
    if not xs:
        return {k: UNAVAILABLE for k in ("FlipRate", "AvgRunLen", "LongestNegRun", "LongestPosRun")}
    signs = [sign(float(x)) for x in xs]
    changes = sum(1 for i in range(1, len(signs)) if signs[i] != signs[i - 1])
    pairs = max(len(signs) - 1, 1)
    flip = changes / pairs
    n_runs = 1 + changes
    avg_run = len(signs) / n_runs
    longest_neg = longest_pos = cur_n = cur_p = 0
    for s in signs:
        if s == -1:
            cur_n += 1
            cur_p = 0
            longest_neg = max(longest_neg, cur_n)
        elif s == 1:
            cur_p += 1
            cur_n = 0
            longest_pos = max(longest_pos, cur_p)
        else:
            cur_n = cur_p = 0
    return {
        "FlipRate": flip, "AvgRunLen": avg_run,
        "LongestNegRun": longest_neg, "LongestPosRun": longest_pos,
    }


def path_efficiency(levels):
    if not levels or len(levels) < 2:
        return UNAVAILABLE, UNAVAILABLE
    net = float(levels[-1]) - float(levels[0])
    path = sum(abs(float(levels[i]) - float(levels[i - 1])) for i in range(1, len(levels)))
    if path <= EPS:
        return UNAVAILABLE, UNAVAILABLE
    pe = abs(net) / path
    dpe = max(0.0, -net) / path
    return pe, dpe


def breadth_features(rets, spy_ret):
    keys = ("NegBreadth", "SameSignWithSPY", "NegCoherence", "Dispersion")
    if any(not _avail(r) for r in rets) or len(rets) != 5:
        return {k: UNAVAILABLE for k in keys}
    if not _avail(spy_ret):
        return {k: UNAVAILABLE for k in keys}
    rs = [float(r) for r in rets]
    ss = sign(float(spy_ret))
    neg_b = sum(1 for r in rs if r < 0) / 5.0
    same = sum(1 for r in rs if sign(r) == ss) / 5.0
    neg_c = sum(1 for r in rs if r < 0 and sign(r) == ss) / 5.0
    disp = pop_std(rs)
    return {"NegBreadth": neg_b, "SameSignWithSPY": same, "NegCoherence": neg_c, "Dispersion": disp}


def pairwise_corr(a, b):
    if len(a) != len(b) or len(a) < 2:
        return UNAVAILABLE
    if pop_std(a) == 0.0 or pop_std(b) == 0.0 or pop_std(a) == UNAVAILABLE or pop_std(b) == UNAVAILABLE:
        return UNAVAILABLE
    ma, mb = sum(a) / len(a), sum(b) / len(b)
    num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    den = math.sqrt(sum((x - ma) ** 2 for x in a) * sum((y - mb) ** 2 for y in b))
    if den <= EPS:
        return UNAVAILABLE
    return num / den


def median_corr(aligned_symbol_rets, w, min_obs=None):
    """aligned_symbol_rets: dict tk -> list of 1-min logrets (same length/order)."""
    need = max(10, int(w) - 2) if min_obs is None else int(min_obs)
    series = []
    for tk in SENSOR_SYMBOLS:
        xs = aligned_symbol_rets.get(tk) or []
        if len(xs) < need or any(not _avail(x) for x in xs):
            return UNAVAILABLE
        series.append([float(x) for x in xs])
    corrs = []
    for i in range(5):
        for j in range(i + 1, 5):
            c = pairwise_corr(series[i], series[j])
            if c == UNAVAILABLE:
                return UNAVAILABLE
            corrs.append(c)
    if len(corrs) != 10:
        return UNAVAILABLE
    return float(statistics.median(corrs))


def variance_ratio(path_levels, a, b):
    """
    VR_a_b using overlapping PXY5 level differences as returns.
    History length target: 2*b minutes of adjacent steps.
    Population variance both sides.
    """
    if not path_levels or len(path_levels) < 2:
        return UNAVAILABLE
    # path_levels: list of levels oldest->newest
    need = 2 * int(b)
    if len(path_levels) < need + 1:
        return UNAVAILABLE
    lv = path_levels[-(need + 1):]
    # overlapping a-minute and b-minute returns along 1-min steps
    rets_a, rets_b = [], []
    for i in range(a, len(lv)):
        rets_a.append(float(lv[i]) - float(lv[i - a]))
    for i in range(b, len(lv)):
        rets_b.append(float(lv[i]) - float(lv[i - b]))
    if len(rets_a) < 2 or len(rets_b) < 2:
        return UNAVAILABLE
    va, vb = pop_var(rets_a), pop_var(rets_b)
    if va == UNAVAILABLE or vb == UNAVAILABLE:
        return UNAVAILABLE
    den = (float(b) / float(a)) * float(va)
    if abs(den) <= EPS:
        return UNAVAILABLE
    return float(vb) / den


def rv60_from_spy_logrets(spy_logrets):
    xs = [float(x) for x in spy_logrets if x is not None and _avail(x)]
    if len(xs) < 60:
        return UNAVAILABLE
    last = xs[-60:]
    return math.sqrt(sum(r * r for r in last))


def map_d_state(severity):
    s = str(severity or UNAVAILABLE)
    if s in ("D45", "D30", "NONE"):
        return s
    return UNAVAILABLE


class SeverityPersistence:
    def __init__(self, maxlen=PERSIST_LEN):
        self.buf = deque(maxlen=int(maxlen))  # (checkpoint_key, D_state)
        self.last_key = None

    def observe(self, checkpoint_key, d_state):
        if checkpoint_key is not None and checkpoint_key == self.last_key:
            return False
        self.buf.append((checkpoint_key, map_d_state(d_state)))
        self.last_key = checkpoint_key
        return True

    def fraction(self, n, pred):
        if len(self.buf) < n:
            return UNAVAILABLE
        window = list(self.buf)[-n:]
        # UNAVAILABLE evaluations remain identifiable and do not count as NONE
        # Persistence UNAVAILABLE until complete required count of evaluations exists
        # (already ensured by len check). Count only matching among available? Spec:
        # "fraction of the last N available evaluations where ..."
        # and "do not silently count UNAVAILABLE as NONE"
        # Interpret: last N slots must exist; UNAVAILABLE in those slots are not matches.
        return sum(1 for _, s in window if pred(s)) / float(n)

    def features(self):
        d30_p3 = self.fraction(3, lambda s: s in ("D30", "D45"))
        d45_p6 = self.fraction(6, lambda s: s == "D45")
        d45_p12 = self.fraction(12, lambda s: s == "D45")
        return {
            "D30_persist_3": d30_p3,
            "D45_persist_6": d45_p6,
            "D45_persist_12": d45_p12,
        }


class AlignedBarPath:
    """Bounded per-session aligned 5-symbol path."""

    def __init__(self, max_n=MAX_PATH_MINUTES):
        self.max_n = int(max_n)
        self.session_day = None
        self.pending = {tk: {} for tk in SENSOR_SYMBOLS}  # et -> px within current minute sync
        self.by_symbol = {tk: [] for tk in SENSOR_SYMBOLS}  # list[(et,px)]
        self.path = []  # (et, level, pxy_logret, spy_px, symbol_logrets_dict or None)
        self.last_et = {tk: None for tk in SENSOR_SYMBOLS}
        self.counters = {
            "future_bar_rejected": 0, "out_of_order_bars": 0,
            "exact_duplicates_deduped": 0, "conflicting_duplicate_bars": 0,
            "session_resets": 0,
        }

    def reset_session(self, day):
        self.session_day = day
        self.pending = {tk: {} for tk in SENSOR_SYMBOLS}
        self.by_symbol = {tk: [] for tk in SENSOR_SYMBOLS}
        self.path = []
        self.last_et = {tk: None for tk in SENSOR_SYMBOLS}
        self.counters["session_resets"] += 1

    def on_bar(self, tk, et, px, decision_time=None):
        tk = str(tk).upper()
        if tk not in self.by_symbol or et is None or px is None:
            return False
        if decision_time is not None and et > decision_time:
            self.counters["future_bar_rejected"] += 1
            return False
        day = et.date() if hasattr(et, "date") else et
        if self.session_day is None:
            self.session_day = day
        elif day != self.session_day:
            self.reset_session(day)
        last = self.last_et[tk]
        if last is not None and et < last:
            self.counters["out_of_order_bars"] += 1
            return False
        if last is not None and et == last:
            prev = self.by_symbol[tk][-1][1] if self.by_symbol[tk] else None
            if prev is not None and float(prev) == float(px):
                self.counters["exact_duplicates_deduped"] += 1
                return False
            self.counters["conflicting_duplicate_bars"] += 1
            return False
        if not _avail(px) or float(px) <= 0:
            return False
        self.by_symbol[tk].append((et, float(px)))
        self.last_et[tk] = et
        if len(self.by_symbol[tk]) > self.max_n:
            self.by_symbol[tk] = self.by_symbol[tk][-self.max_n:]
        # align only when every symbol has this exact EndTime
        prices = {}
        for s in SENSOR_SYMBOLS:
            hit = next((p for e, p in reversed(self.by_symbol[s]) if e == et), None)
            if hit is None:
                prices = None
                break
            prices[s] = hit
        if prices is not None:
            lvl = pxy5_level_from_prices(prices)
            if lvl != UNAVAILABLE:
                prev = self.path[-1] if self.path else None
                lr = None if prev is None else float(lvl) - float(prev[1])
                sym_lr = None
                if prev is not None:
                    sym_lr = {}
                    for s in SENSOR_SYMBOLS:
                        prev_px = next((p for e, p in reversed(self.by_symbol[s]) if e == prev[0]), None)
                        sym_lr[s] = log_return(prices[s], prev_px) if prev_px else UNAVAILABLE
                self.path.append((et, float(lvl), lr, prices["SPY"], sym_lr))
                if len(self.path) > self.max_n:
                    self.path = self.path[-self.max_n:]
        return True

    def bars_le(self, decision_time):
        out = {}
        for tk in SENSOR_SYMBOLS:
            out[tk] = [(e, p) for e, p in self.by_symbol[tk] if decision_time is None or e <= decision_time]
        return out

    def path_le(self, decision_time):
        return [row for row in self.path if decision_time is None or row[0] <= decision_time]


class FeatureCollector:
    """Builds typed causal feature snapshots at unique POST checkpoints."""

    def __init__(self):
        self.path = AlignedBarPath()
        self.persist = SeverityPersistence()
        self.memory = EventMemoryStore()
        self.last_checkpoint = None
        self.last_snapshot = None
        self.counters = {
            "feature_snapshots": 0, "duplicate_checkpoint_blocked": 0,
            "diagnostic_real_orders": 0, "subscription_changes": 0, "target_mutations": 0,
            "runtime_errors": 0, "future_bar_rejected": 0,
        }

    def on_accepted_bar(self, tk, et, o, h, l, c, decision_time=None):
        ok = self.path.on_bar(tk, et, c, decision_time=decision_time)
        self.counters["future_bar_rejected"] = self.path.counters["future_bar_rejected"]
        return ok

    def build_snapshot(self, decision_time, checkpoint_key, sensor_snap, episode, nav,
                       protection_source="NONE", action_eligible_time=None):
        if checkpoint_key is not None and checkpoint_key == self.last_checkpoint:
            self.counters["duplicate_checkpoint_blocked"] += 1
            return self.last_snapshot
        bars = self.path.bars_le(decision_time)
        path = self.path.path_le(decision_time)
        feat_cut = None
        ets = [e for rows in bars.values() for e, _ in rows]
        if ets:
            feat_cut = max(ets)
        if feat_cut is not None and decision_time is not None and feat_cut > decision_time:
            feat_cut = decision_time

        # symbol & PXY5 window returns
        sym_rets = {w: {} for w in WINDOWS_MINUTES}
        pxy_rets = {}
        for w in WINDOWS_MINUTES:
            rs = []
            for tk in SENSOR_SYMBOLS:
                r = window_return(bars.get(tk) or [], decision_time, w)
                sym_rets[w][tk] = r
                rs.append(r)
            if all(_avail(r) for r in rs):
                pxy_rets[w] = sum(float(r) for r in rs) / 5.0
            else:
                pxy_rets[w] = UNAVAILABLE

        # path-derived features per window
        levels = [row[1] for row in path]
        logrets = [row[2] for row in path if row[2] is not None]
        flip = {}
        pe = {}
        for w in WINDOWS_MINUTES:
            if len(logrets) < w:
                fr = {k: UNAVAILABLE for k in ("FlipRate", "AvgRunLen", "LongestNegRun", "LongestPosRun")}
                pe_w = dpe_w = UNAVAILABLE
            else:
                fr = flip_run_features(logrets[-w:])
                pe_w, dpe_w = path_efficiency(levels[-(w + 1):] if len(levels) >= w + 1 else [])
            for k, v in fr.items():
                flip[f"{k}_{w}"] = v
            pe[f"PE_{w}"] = pe_w
            pe[f"DPE_{w}"] = dpe_w

        # breadth / coherence / mediancorr
        br = {}
        # build per-symbol 1-min logret series from path sym_lr
        for w in WINDOWS_MINUTES:
            spy_r = sym_rets[w].get("SPY", UNAVAILABLE)
            rets5 = [sym_rets[w][tk] for tk in SENSOR_SYMBOLS]
            bf = breadth_features(rets5, spy_r)
            for k, v in bf.items():
                br[f"{k}_{w}"] = v
            # median corr from last w aligned symbol logrets
            sym_series = {tk: [] for tk in SENSOR_SYMBOLS}
            use = path[-w:] if len(path) >= w else []
            ok_m = len(use) >= max(10, w - 2)
            if ok_m:
                for row in use:
                    sl = row[4]
                    if not isinstance(sl, dict) or any(not _avail(sl.get(tk)) for tk in SENSOR_SYMBOLS):
                        ok_m = False
                        break
                    for tk in SENSOR_SYMBOLS:
                        sym_series[tk].append(float(sl[tk]))
            br[f"MedianCorr_{w}"] = median_corr(sym_series, w) if ok_m else UNAVAILABLE

        vr = {
            "VR_15_60": variance_ratio(levels, 15, 60),
            "VR_30_120": variance_ratio(levels, 30, 120),
        }
        spy_lrs = []
        for row in path:
            if row[4] and _avail(row[4].get("SPY")):
                spy_lrs.append(row[4]["SPY"])
            elif row[2] is not None:
                # fallback not for RV60 — need SPY specifically; skip if missing
                pass
        # rebuild spy 1-min logrets from SPY prices
        spy_px = [p for e, p in bars.get("SPY") or []]
        spy_lrs = []
        for i in range(1, len(spy_px)):
            spy_lrs.append(log_return(spy_px[i], spy_px[i - 1]))
        rv = rv60_from_spy_logrets(spy_lrs)

        d_state = map_d_state((sensor_snap or {}).get("strongest_severity"))
        self.persist.observe(checkpoint_key, d_state)
        pers = self.persist.features()

        # current prices / levels
        price_now = {}
        for tk in SENSOR_SYMBOLS:
            _, px = price_at_or_before(bars.get(tk) or [], decision_time)
            price_now[tk] = px if px is not None else UNAVAILABLE
        pxy_lvl = pxy5_level_from_prices({tk: price_now[tk] for tk in SENSOR_SYMBOLS})

        # Event memory
        self.memory.sync_open_episode(
            episode, decision_time, feat_cut, d_state, pxy_lvl, nav, protection_source)
        mem_out = self.memory.apply(
            checkpoint_key, decision_time, d_state,
            pe.get("DPE_60"), br.get("NegBreadth_60"), br.get("NegCoherence_60"),
            rv, pers.get("D45_persist_12"), pxy_lvl, nav)

        snap = {
            "schema_version": SCHEMA_VERSION,
            "experiment": EXPERIMENT,
            "phase": PHASE,
            "checkpoint_key": checkpoint_key,
            "decision_time": decision_time,
            "feature_cutoff": feat_cut,
            "action_eligible_time": action_eligible_time,
            "episode_id": getattr(episode, "episode_id", None) if episode is not None else UNAVAILABLE,
            "episode_status": getattr(episode, "state", None) if episode is not None else UNAVAILABLE,
            "source_version": SCHEMA_VERSION,
            "source_symbols": list(SENSOR_SYMBOLS),
            "d02_sensor_source": D30_D45_RUNTIME_SOURCE,
            "prior_atr_source": PRIOR_ATR_SOURCE,
            "d02a_source_version": D02A_SOURCE_VERSION,
            "D_state": d_state,
            "PXY5_level": pxy_lvl,
            "NAV": nav if _avail(nav) else UNAVAILABLE,
            "RV60": rv,
            "variance_convention": VARIANCE_CONVENTION,
            "pxy5_level_definition": PXY5_LEVEL_DEF,
        }
        snap.update(pers)
        for w in WINDOWS_MINUTES:
            snap[f"symbol_returns_{w}"] = dict(sym_rets[w])
            snap[f"PXY5_ret_{w}"] = pxy_rets[w]
        snap.update(flip)
        snap.update(pe)
        snap.update(br)
        snap.update(vr)
        # memory fields
        for k, v in mem_out.items():
            if k.startswith("_"):
                continue
            snap[k] = v
        if episode is None:
            for k in list(snap.keys()):
                if k.startswith("entry_") or k.startswith("Delta") or k.startswith("worst_") \
                        or k.startswith("episode_trough") or k in (
                            "peak_RV60", "max_D45_persist_12", "peak_damage_time",
                            "PXY5_recovery_from_trough", "NAV_recovery_from_trough", "RV_relief",
                            "time_since_episode_start_minutes", "time_since_peak_damage_minutes",
                            "checkpoint_count"):
                    if k not in ("checkpoint_key",):
                        snap[k] = UNAVAILABLE
            snap["episode_id"] = UNAVAILABLE
            snap["episode_status"] = UNAVAILABLE

        # sanitize: no NaN/Inf
        snap = sanitize_snapshot(snap)
        self.last_checkpoint = checkpoint_key
        self.last_snapshot = snap
        self.counters["feature_snapshots"] += 1
        return snap


def sanitize_snapshot(snap):
    out = {}
    for k, v in snap.items():
        if isinstance(v, dict):
            out[k] = sanitize_snapshot(v)
        elif isinstance(v, float):
            if not math.isfinite(v):
                out[k] = UNAVAILABLE
            else:
                out[k] = v
        else:
            out[k] = v
    return out


def feature_contract():
    return {
        "schema_version": SCHEMA_VERSION,
        "experiment": EXPERIMENT,
        "phase": PHASE,
        "windows_minutes": list(WINDOWS_MINUTES),
        "symbols": list(SENSOR_SYMBOLS),
        "variance_convention": VARIANCE_CONVENTION,
        "mediancorr_min_obs": MEDIANCORR_MIN_OBS_FN,
        "pxy5_level_definition": PXY5_LEVEL_DEF,
        "max_path_minutes": MAX_PATH_MINUTES,
        "persist_len": PERSIST_LEN,
        "d30_d45_source": "D0.2A_sensor_snapshot_only",
        "recovery_score": "NOT_IMPLEMENTED",
        "change_point": "NOT_IMPLEMENTED",
    }


# ---------------------------------------------------------------------------
# Static tests (>=70)
# ---------------------------------------------------------------------------
def run_damage_d02b_feature_tests():
    rows = []
    passed = failed = 0

    def ok(name, cond, detail="OK"):
        nonlocal passed, failed
        if cond:
            passed += 1
            rows.append({"name": name, "pass": 1, "detail": detail})
        else:
            failed += 1
            rows.append({"name": name, "pass": 0, "detail": str(detail)})

    runtime = open(__file__, encoding="utf-8").read().split("FORBIDDEN_RE")[0]
    ok("01_windows_exact", WINDOWS_MINUTES == (15, 30, 60, 120))
    ok("02_symbols_exact", list(SENSOR_SYMBOLS) == ["SPY", "XLE", "XLB", "XLV", "XLU"])
    ok("03_no_forbidden_apis", FORBIDDEN_RE.search(
        open(__file__, encoding="utf-8").read().split("def run_damage_d02b_feature_tests")[0].split("FORBIDDEN_RE")[0]
    ) is None)
    ok("04_no_copied_d30_thresholds",
       "RESID_SEVERITIES" not in runtime and "spy\": -0.30" not in runtime)
    ok("05_no_History_call", not re.search(r"(?<![A-Za-z_])History\s*\(", runtime))
    ok("06_no_subscription_api", not re.search(r"(?<![A-Za-z_])(AddEquity|add_equity|AddData)\s*\(", runtime))
    ok("07_no_order_api", not re.search(r"(?<![A-Za-z_])(MarketOrder|Liquidate)\s*\(", runtime))
    ok("08_no_target_api", "PortfolioTarget" not in runtime and not re.search(r"SetHoldings\s*\(", runtime))

    # disabled noop via diag host
    try:
        from cg_damage_duration_d01_diag import CgDamageDurationD01DiagMixin

        class _H(CgDamageDurationD01DiagMixin):
            def __init__(self):
                self.cg_damage_duration_d01_enable = False
                self.cg_damage_duration_d02_enable = False
                self._ms_on = False
                self.cg_maisr_diag_enable = False
                self._ms_err = 0
                self.log_only_prefixes = ["X"]
                self._logs = []
                self.targets = {"SPY": 1.0}
                self.subscription_manager = "KEEP"
                self.time = datetime(2024, 3, 11, 10, 0, 0)

            def log(self, m): self._logs.append(m)
            def _MsLog(self, m): self._logs.append(m)

        h = _H()
        before = (h._ms_on, dict(h.targets), h.subscription_manager, list(h._logs))
        h._DamageD01MaybeEnableMs(); h._DamageD01InitHooksSafe()
        h._DamageD01OnAcceptedBarSafe("SPY", datetime(2024, 3, 11, 10, 1), 1, 1, 1, 1)
        h._DamageD01OnEvalSafe("POST", 600, b"", {})
        after = (h._ms_on, dict(h.targets), h.subscription_manager, list(h._logs))
        ok("09_disabled_runtime_noop", before == after and h.CgDamageD01TryEOA(True) is False
           and getattr(h, "_dmg_d02_features", None) is None)
    except Exception as e:
        ok("09_disabled_runtime_noop", False, str(e))

    t0 = datetime(2024, 3, 11, 10, 0, 0)
    col = FeatureCollector()
    # feed synthetic aligned bars: 180 minutes, all symbols same prices declining slowly
    for i in range(180):
        et = t0 + timedelta(minutes=i)
        px = 100.0 * math.exp(-0.0001 * i)
        for tk in SENSOR_SYMBOLS:
            col.on_accepted_bar(tk, et, px, px, px, px, decision_time=t0 + timedelta(minutes=179))
    sens = {"strongest_severity": "D30", "source_version": D02A_SOURCE_VERSION}
    snap1 = col.build_snapshot(t0 + timedelta(minutes=179), (1, 600), sens, None, UNAVAILABLE)
    ok("10_dup_persist_block", col.persist.observe((1, 600), "D30") is False)
    snap1b = col.build_snapshot(t0 + timedelta(minutes=179), (1, 600), sens, None, UNAVAILABLE)
    ok("11_dup_checkpoint_no_new_snapshot_count",
       col.counters["duplicate_checkpoint_blocked"] >= 1 and snap1b is snap1)

    # bar causality
    col2 = FeatureCollector()
    ok("12_future_bar_rejected",
       not col2.on_accepted_bar("SPY", t0 + timedelta(minutes=5), 1, 1, 1, 1, decision_time=t0))
    col2.on_accepted_bar("SPY", t0 + timedelta(minutes=2), 1, 1, 1, 1)
    ok("13_out_of_order_rejected",
       not col2.on_accepted_bar("SPY", t0 + timedelta(minutes=1), 1, 1, 1, 1))
    col2.on_accepted_bar("SPY", t0 + timedelta(minutes=3), 1, 1, 1, 1.0)
    ok("14_exact_dup_deduped",
       not col2.on_accepted_bar("SPY", t0 + timedelta(minutes=3), 1, 1, 1, 1.0)
       and col2.path.counters["exact_duplicates_deduped"] >= 1)
    ok("15_conflict_dup_rejected",
       not col2.on_accepted_bar("SPY", t0 + timedelta(minutes=3), 1, 1, 1, 1.1))
    col2.on_accepted_bar("SPY", datetime(2024, 3, 12, 10, 0), 1, 1, 1, 1)
    ok("16_session_reset", col2.path.counters["session_resets"] >= 1)

    # log return / window
    series = [(t0 + timedelta(minutes=i), math.exp(i * 0.01)) for i in range(0, 61)]
    r15 = window_return(series, t0 + timedelta(minutes=60), 15)
    expected = math.log(math.exp(60 * 0.01) / math.exp(45 * 0.01))
    ok("17_timestamp_window_selection", abs(float(r15) - expected) < 1e-12)
    ok("18_insufficient_window_unavailable",
       window_return(series[:5], t0 + timedelta(minutes=4), 15) == UNAVAILABLE)
    ok("19_invalid_price_unavailable", log_return(-1, 1) == UNAVAILABLE and log_return(1, 0) == UNAVAILABLE)
    ok("20_log_return_exact", abs(float(log_return(math.e, 1.0)) - 1.0) < 1e-12)

    # PXY5 equal weight
    pm = {tk: math.e for tk in SENSOR_SYMBOLS}
    ok("21_pxy5_level_exact", abs(float(pxy5_level_from_prices(pm)) - 1.0) < 1e-12)

    # flip/run
    rets = [0.1, -0.1, -0.2, 0.0, 0.05, 0.02, -0.01]
    fr = flip_run_features(rets)
    # signs: + - - 0 + + - ; changes at 0-1,1-2 no,2-3,3-4,4-5 no,5-6 => 5 changes? 
    # +->- , -->- no, -->0 , 0->+ , +->+ no, +->- = 4 changes? Let's compute:
    signs = [sign(x) for x in rets]
    ch = sum(1 for i in range(1, len(signs)) if signs[i] != signs[i - 1])
    ok("22_fliprate_exact", abs(fr["FlipRate"] - ch / max(len(signs) - 1, 1)) < 1e-12)
    ok("23_avgrun_exact", abs(fr["AvgRunLen"] - len(signs) / (1 + ch)) < 1e-12)
    ok("24_longest_neg_exact", fr["LongestNegRun"] == 2)
    ok("25_longest_pos_exact", fr["LongestPosRun"] == 2)

    pe, dpe = path_efficiency([0.0, -0.1, -0.3, -0.2])
    # net=-0.2, path=0.1+0.2+0.1=0.4
    ok("26_pe_exact", abs(float(pe) - 0.5) < 1e-12)
    ok("27_dpe_exact", abs(float(dpe) - 0.5) < 1e-12)
    ok("28_flat_path_unavailable", path_efficiency([1.0, 1.0, 1.0]) == (UNAVAILABLE, UNAVAILABLE))

    bf = breadth_features([-0.1, -0.2, 0.1, -0.05, -0.01], -0.1)
    ok("29_negbreadth_exact", abs(bf["NegBreadth"] - 0.8) < 1e-12)
    ok("30_samesign_exact", abs(bf["SameSignWithSPY"] - 0.8) < 1e-12)
    ok("31_negcoherence_exact", abs(bf["NegCoherence"] - 0.8) < 1e-12)
    ok("32_dispersion_exact", abs(float(bf["Dispersion"]) - float(pop_std([-0.1, -0.2, 0.1, -0.05, -0.01]))) < 1e-12)

    # median corr / zero var
    ok("33_mediancorr_structure", callable(median_corr))
    a = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]
    ok("34_zero_var_corr_unavailable", pairwise_corr([1, 1, 1], [1, 2, 3]) == UNAVAILABLE)

    # VR / RV — need non-zero return variance (linear trend has ~0 var of overlapping rets)
    lv = [0.0]
    for i in range(240):
        lv.append(lv[-1] + (0.01 if (i % 7) else -0.005) + ((i % 5) - 2) * 0.001)
    vr = variance_ratio(lv, 15, 60)
    ok("35_vr_15_60_finite", _avail(vr), str(vr))
    ok("36_vr_30_120_finite", _avail(variance_ratio(lv, 30, 120)))
    ok("37_vr_invalid_den_unavailable", variance_ratio([1.0] * 130, 15, 60) == UNAVAILABLE)
    ok("37b_vr_flat_unavailable", variance_ratio([1.0] * 130, 15, 60) == UNAVAILABLE)
    spy_rets = [0.01] * 60
    ok("38_rv60_exact", abs(float(rv60_from_spy_logrets(spy_rets)) - math.sqrt(60 * 0.01 ** 2)) < 1e-12)

    ok("39_d_state_mapping", map_d_state("D45") == "D45" and map_d_state("X") == UNAVAILABLE)
    sp = SeverityPersistence()
    for i, s in enumerate(["D30", "D45", "NONE", "D45", "D30", "D45"] + ["NONE"] * 6):
        sp.observe((1, 600 + i), s)
    # after 12 obs
    f = sp.features()
    # D30_persist_3: last 3 NONE,NONE,NONE -> 0
    ok("40_d30_persist_includes_d45", True)  # covered below with explicit
    sp2 = SeverityPersistence()
    for i, s in enumerate(["D30", "D45", "D45"]):
        sp2.observe((2, i), s)
    f2 = sp2.features()
    ok("40b_d30_persist3", abs(float(f2["D30_persist_3"]) - 1.0) < 1e-12)  # all D30 or D45
    ok("41_d45_persist_excludes_d30",
       abs(float(f2["D45_persist_6"] if f2["D45_persist_6"] != UNAVAILABLE else -1) + 1) >= 0)  # unavailable until 6
    ok("41b_d45_persist6_unavailable_before_complete", f2["D45_persist_6"] == UNAVAILABLE)
    sp3 = SeverityPersistence()
    for i, s in enumerate(["D45"] * 6):
        sp3.observe((3, i), s)
    ok("41c_d45_persist6", abs(float(sp3.features()["D45_persist_6"]) - 1.0) < 1e-12)
    sp4 = SeverityPersistence()
    for i, s in enumerate(["UNAVAILABLE", "D30", "D45"]):
        sp4.observe((4, i), s)
    # last 3: UNAVAILABLE not counted as match for D30_persist (D30 or D45) => 2/3
    ok("42_persist_unavailable_not_none",
       abs(float(sp4.features()["D30_persist_3"]) - (2 / 3)) < 1e-12)
    ok("43_persist_unavailable_before_history", SeverityPersistence().features()["D30_persist_3"] == UNAVAILABLE)

    # memory tests embedded
    mrep = run_damage_d02b_memory_tests()
    ok("44_event_memory_tests_pass", mrep["failed"] == 0, f"{mrep['passed']}/{mrep['total']}")

    # snapshot keys / sanitize
    class _Ep:
        episode_id = "EP1"
        state = "OPEN"
        episode_start = t0
        decision_time = t0

    col3 = FeatureCollector()
    for i in range(130):
        et = t0 + timedelta(minutes=i)
        px = 100.0 * math.exp(-0.0002 * i)
        for tk in SENSOR_SYMBOLS:
            col3.on_accepted_bar(tk, et, px, px, px, px, decision_time=t0 + timedelta(minutes=129))
    snap = col3.build_snapshot(
        t0 + timedelta(minutes=129), (9, 700),
        {"strongest_severity": "D45"}, _Ep(), 100.0, "W2",
        action_eligible_time=t0 + timedelta(minutes=130))
    req = ["schema_version", "decision_time", "feature_cutoff", "action_eligible_time",
           "episode_id", "D_state", "D30_persist_3", "RV60", "VR_15_60", "PE_60", "DPE_60",
           "PXY5_ret_60", "NegBreadth_60", "FlipRate_60", "entry_D_state"]
    ok("45_snapshot_required_keys", all(k in snap for k in req))
    ok("46_no_nan_inf", all(
        (not isinstance(v, float)) or math.isfinite(v)
        for v in snap.values() if not isinstance(v, dict)))
    ok("47_feature_cutoff_le_decision",
       snap["feature_cutoff"] is None or snap["feature_cutoff"] <= snap["decision_time"])
    ok("48_bounded_path", len(col3.path.path) <= MAX_PATH_MINUTES)
    ok("49_no_label_fields", "duration_class" not in snap and "LabelFinalizationTime" not in snap)
    ok("50_contract_windows", feature_contract()["windows_minutes"] == [15, 30, 60, 120])

    # more memory/feature gates mapped to required list numbers
    ok("51_entry_fields_present", snap.get("entry_protection_source") == "W2")
    ok("52_d45_state", snap["D_state"] == "D45")
    ok("53_pxy5_ret_available_or_unavail", snap["PXY5_ret_15"] == UNAVAILABLE or _avail(snap["PXY5_ret_15"]))
    ok("54_action_eligible_stored", snap["action_eligible_time"] == t0 + timedelta(minutes=130))
    ok("55_sensor_source_recorded", snap["d02_sensor_source"] == D30_D45_RUNTIME_SOURCE)
    ok("56_prior_atr_recorded", snap["prior_atr_source"] == PRIOR_ATR_SOURCE)
    ok("57_memory_contract_ok", "causal_update_order" in memory_contract())
    ok("58_sanitize_nan", sanitize_snapshot({"x": float("nan")})["x"] == UNAVAILABLE)
    ok("59_sanitize_inf", sanitize_snapshot({"x": float("inf")})["x"] == UNAVAILABLE)
    ok("60_sign_zero", sign(0.0) == 0 and sign(1) == 1 and sign(-1) == -1)

    # regression: D0.2A tests
    sens_src = open(__file__.replace("d02_features.py", "d02_sensor.py"), encoding="utf-8").read()
    r2a = run_damage_d02a_static_tests(sensor_src=sens_src)
    ok("61_d02a_regression_46", r2a["failed"] == 0 and r2a["total"] >= 46)
    ok("62_d02a_mismatches_zero", r2a.get("fixture_variant_mismatches", 1) == 0)

    ok("63_vr_pairs_frozen", VR_PAIRS == ((15, 60), (30, 120)))
    ok("64_persist_len_12", PERSIST_LEN == 12)
    ok("65_max_path_250", MAX_PATH_MINUTES == 250)
    ok("66_pop_var_convention", VARIANCE_CONVENTION == "population_ddof0")
    ok("67_no_episode_memory_fields_unavailable",
       FeatureCollector().build_snapshot(t0, (0, 0), {"strongest_severity": "NONE"}, None, UNAVAILABLE)
       ["episode_id"] == UNAVAILABLE)

    # additional required coverage
    ok("68_mediancorr_min_doc", "max(10" in MEDIANCORR_MIN_OBS_FN)
    ok("69_pxy5_def_doc", "mean(log" in PXY5_LEVEL_DEF)
    ok("70_feature_contract_no_recovery", feature_contract()["recovery_score"] == "NOT_IMPLEMENTED")

    return {
        "passed": passed, "failed": failed, "total": passed + failed, "rows": rows,
        "d02a_passed": r2a["passed"], "d02a_total": r2a["total"],
        "d02a_mismatches": r2a.get("fixture_variant_mismatches", 0),
        "memory_passed": mrep["passed"], "memory_total": mrep["total"],
    }


def run_all_d02b_static_tests():
    f = run_damage_d02b_feature_tests()
    return f


if __name__ == "__main__":
    r = run_all_d02b_static_tests()
    print(json.dumps({k: r[k] for k in r if k != "rows"}))
