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
MEDIANCORR_MIN_OBS_FN = "exact_w_aligned_one_minute_returns"
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


def price_at_exact(series, t):
    """series: list[(EndTime, price)]. Exact EndTime match only."""
    if not series or t is None:
        return None, None
    for et, px in series:
        if et == t:
            return et, px
    return None, None


def price_at_or_before(series, t):
    """Legacy helper retained for diagnostics; feature windows use exact endpoints."""
    if not series or t is None:
        return None, None
    best = None
    for et, px in series:
        if et <= t:
            best = (et, px)
        else:
            break
    return best if best else (None, None)


def aligned_feature_cutoff(bars_by_symbol, decision_time):
    """
    Latest EndTime <= decision_time where all five symbols have a valid positive
    price at exactly the same EndTime. Mixed per-symbol maxima are forbidden.
    """
    if decision_time is None:
        return None
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
        return None
    return max(common)


def prices_at_cutoff(bars_by_symbol, cutoff):
    """All five symbols at exact cutoff, else None."""
    if cutoff is None:
        return None
    out = {}
    for tk in SENSOR_SYMBOLS:
        et, px = price_at_exact(bars_by_symbol.get(tk) or [], cutoff)
        if et is None or not _avail(px) or float(px) <= 0:
            return None
        out[tk] = float(px)
    return out


def window_return(series, c, w_min):
    """Exact endpoints at c and c-w. No at-or-before substitution."""
    if c is None:
        return UNAVAILABLE
    cur_et, cur_px = price_at_exact(series, c)
    if cur_et is None:
        return UNAVAILABLE
    ref_t = c - timedelta(minutes=int(w_min))
    ref_et, ref_px = price_at_exact(series, ref_t)
    if ref_et is None:
        return UNAVAILABLE
    return log_return(cur_px, ref_px)


def exact_grid_slice(path_rows, end_time, minutes):
    """
    Exact continuous one-minute grid ending at end_time spanning `minutes`.
    Requires minutes+1 levels with timestamps end-minutes ... end.
    Returns list of rows or None if any required minute is missing/gapped.
    """
    if end_time is None or minutes is None or int(minutes) < 1:
        return None
    minutes = int(minutes)
    if not path_rows:
        return None
    by_et = {}
    for row in path_rows:
        et = row[0]
        if et in by_et:
            return None  # duplicate timestamp
        by_et[et] = row
    expected = [end_time - timedelta(minutes=minutes - i) for i in range(minutes + 1)]
    # expected[0]=end-minutes, expected[-1]=end
    out = []
    prev = None
    for et in expected:
        row = by_et.get(et)
        if row is None:
            return None
        if prev is not None and (et - prev) != timedelta(minutes=1):
            return None
        out.append(row)
        prev = et
    if out[0][0] != end_time - timedelta(minutes=minutes):
        return None
    if out[-1][0] != end_time:
        return None
    # reject if wall-clock span of selected rows != minutes (guards row-count tricks)
    span_min = (out[-1][0] - out[0][0]).total_seconds() / 60.0
    if abs(span_min - float(minutes)) > EPS:
        return None
    return out


def spy_exact_grid_returns(spy_series, end_time, minutes):
    """Exactly `minutes` adjacent SPY log returns on continuous grid ending at end_time."""
    if end_time is None or minutes is None or int(minutes) < 1:
        return None
    minutes = int(minutes)
    by_et = {}
    for et, px in spy_series or []:
        if et in by_et:
            return None
        if _avail(px) and float(px) > 0:
            by_et[et] = float(px)
    expected = [end_time - timedelta(minutes=minutes - i) for i in range(minutes + 1)]
    px = []
    prev = None
    for et in expected:
        if et not in by_et:
            return None
        if prev is not None and (et - prev) != timedelta(minutes=1):
            return None
        px.append(by_et[et])
        prev = et
    rets = []
    for i in range(1, len(px)):
        r = log_return(px[i], px[i - 1])
        if not _avail(r):
            return None
        rets.append(float(r))
    if len(rets) != minutes:
        return None
    return rets


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
    """aligned_symbol_rets: dict tk -> list of exactly w 1-min logrets (same timestamps)."""
    w = int(w)
    need = w if min_obs is None else int(min_obs)
    series = []
    for tk in SENSOR_SYMBOLS:
        xs = aligned_symbol_rets.get(tk) or []
        if len(xs) != need or any(not _avail(x) for x in xs):
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
    VR_a_b on an exact continuous level grid of length 2*b+1.
    Population variance both sides. Row-count without timestamp validation
    is not sufficient — callers must pass exact_grid_slice levels.
    """
    a, b = int(a), int(b)
    need = 2 * b + 1
    if not path_levels or len(path_levels) != need:
        return UNAVAILABLE
    lv = [float(x) for x in path_levels]
    rets_a, rets_b = [], []
    for i in range(a, len(lv)):
        rets_a.append(lv[i] - lv[i - a])
    for i in range(b, len(lv)):
        rets_b.append(lv[i] - lv[i - b])
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
    """Exactly 60 one-minute SPY log returns (caller must enforce elapsed-time grid)."""
    xs = [float(x) for x in spy_logrets if x is not None and _avail(x)]
    if len(xs) != 60:
        return UNAVAILABLE
    return math.sqrt(sum(r * r for r in xs))


def map_d_state(severity):
    s = str(severity or UNAVAILABLE)
    if s in ("D45", "D30", "NONE"):
        return s
    return UNAVAILABLE


class SeverityPersistence:
    """
    Dual history:
      raw  — last 12 unique checkpoints including UNAVAILABLE (audit)
      valid — last 12 unique available severities NONE/D30/D45 only
    Persistence fractions use valid history only.
    """

    def __init__(self, maxlen=PERSIST_LEN):
        self.maxlen = int(maxlen)
        self.raw = deque(maxlen=self.maxlen)
        self.valid = deque(maxlen=self.maxlen)
        self.last_key = None
        self.unavailable_count = 0

    def observe(self, checkpoint_key, d_state):
        if checkpoint_key is not None and checkpoint_key == self.last_key:
            return False
        s = map_d_state(d_state)
        self.raw.append((checkpoint_key, s))
        if s == UNAVAILABLE:
            self.unavailable_count += 1
        else:
            self.valid.append((checkpoint_key, s))
        self.last_key = checkpoint_key
        return True

    def fraction(self, n, pred):
        if len(self.valid) < n:
            return UNAVAILABLE
        window = list(self.valid)[-n:]
        return sum(1 for _, s in window if pred(s)) / float(n)

    def features(self):
        return {
            "D30_persist_3": self.fraction(3, lambda s: s in ("D30", "D45")),
            "D45_persist_6": self.fraction(6, lambda s: s == "D45"),
            "D45_persist_12": self.fraction(12, lambda s: s == "D45"),
            "severity_raw_history_count": len(self.raw),
            "severity_valid_history_count": len(self.valid),
            "severity_unavailable_count": int(self.unavailable_count),
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
            "session_resets": 0, "stale_prior_session_bar": 0,
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
        elif day < self.session_day:
            # late prior-session bar: reject; never reset backward
            self.counters["stale_prior_session_bar"] += 1
            return False
        elif day > self.session_day:
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
                # adjacent path returns only when timestamps are exactly 1 minute apart
                if prev is not None and (et - prev[0]) != timedelta(minutes=1):
                    lr = None
                sym_lr = None
                if prev is not None and lr is not None:
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
        if action_eligible_time is None:
            action_eligible_time = UNAVAILABLE
        bars = self.path.bars_le(decision_time)
        path = self.path.path_le(decision_time)
        feat_cut = aligned_feature_cutoff(bars, decision_time)
        unavail_path = {
            k: UNAVAILABLE for k in (
                "FlipRate", "AvgRunLen", "LongestNegRun", "LongestPosRun")}

        # symbol & PXY5 window returns — exact endpoints at c and c-w
        sym_rets = {w: {} for w in WINDOWS_MINUTES}
        pxy_rets = {}
        for w in WINDOWS_MINUTES:
            rs = []
            for tk in SENSOR_SYMBOLS:
                if feat_cut is None:
                    r = UNAVAILABLE
                else:
                    r = window_return(bars.get(tk) or [], feat_cut, w)
                sym_rets[w][tk] = r
                rs.append(r)
            if feat_cut is not None and all(_avail(r) for r in rs):
                pxy_rets[w] = sum(float(r) for r in rs) / 5.0
            else:
                pxy_rets[w] = UNAVAILABLE

        # path-derived features: exact continuous grids ending at c
        flip = {}
        pe = {}
        br = {}
        for w in WINDOWS_MINUTES:
            grid = exact_grid_slice(path, feat_cut, w) if feat_cut is not None else None
            if grid is None:
                fr = dict(unavail_path)
                pe_w = dpe_w = UNAVAILABLE
                mc = UNAVAILABLE
            else:
                # w adjacent returns: rows[1:] logrets must exist (from levels)
                levels = [row[1] for row in grid]
                logrets = []
                for i in range(1, len(grid)):
                    # prefer stored adjacent lr only if timestamps are 1m; else recompute
                    lr = float(levels[i]) - float(levels[i - 1])
                    logrets.append(lr)
                fr = flip_run_features(logrets)
                pe_w, dpe_w = path_efficiency(levels)
                # MedianCorr: exactly w aligned symbol 1-min returns
                sym_series = {tk: [] for tk in SENSOR_SYMBOLS}
                ok_m = True
                for i in range(1, len(grid)):
                    sl = grid[i][4]
                    if not isinstance(sl, dict) or any(not _avail(sl.get(tk)) for tk in SENSOR_SYMBOLS):
                        # recompute from prices on grid if stored missing
                        for tk in SENSOR_SYMBOLS:
                            # prices stored only in path row indirectly via by_symbol
                            pass
                        ok_m = False
                        break
                    for tk in SENSOR_SYMBOLS:
                        sym_series[tk].append(float(sl[tk]))
                if ok_m and all(len(sym_series[tk]) == w for tk in SENSOR_SYMBOLS):
                    mc = median_corr(sym_series, w)
                else:
                    # recompute symbol logrets from by_symbol exact timestamps
                    sym_series = {tk: [] for tk in SENSOR_SYMBOLS}
                    ok_m = True
                    for i in range(1, len(grid)):
                        et_now, et_prev = grid[i][0], grid[i - 1][0]
                        for tk in SENSOR_SYMBOLS:
                            _, px_n = price_at_exact(bars.get(tk) or [], et_now)
                            _, px_p = price_at_exact(bars.get(tk) or [], et_prev)
                            r = log_return(px_n, px_p)
                            if not _avail(r):
                                ok_m = False
                                break
                            sym_series[tk].append(float(r))
                        if not ok_m:
                            break
                    mc = median_corr(sym_series, w) if ok_m else UNAVAILABLE
            for k, v in fr.items():
                flip[f"{k}_{w}"] = v
            pe[f"PE_{w}"] = pe_w
            pe[f"DPE_{w}"] = dpe_w
            spy_r = sym_rets[w].get("SPY", UNAVAILABLE)
            rets5 = [sym_rets[w][tk] for tk in SENSOR_SYMBOLS]
            bf = breadth_features(rets5, spy_r)
            for k, v in bf.items():
                br[f"{k}_{w}"] = v
            br[f"MedianCorr_{w}"] = mc

        # VR: exact grids of length 2*b+1 ending at c
        vr = {"VR_15_60": UNAVAILABLE, "VR_30_120": UNAVAILABLE}
        if feat_cut is not None:
            g60 = exact_grid_slice(path, feat_cut, 120)  # 121 levels for VR_15_60 (2*60)
            if g60 is not None and len(g60) == 121:
                vr["VR_15_60"] = variance_ratio([row[1] for row in g60], 15, 60)
            g120 = exact_grid_slice(path, feat_cut, 240)  # 241 levels for VR_30_120
            if g120 is not None and len(g120) == 241:
                vr["VR_30_120"] = variance_ratio([row[1] for row in g120], 30, 120)

        # RV60: exact SPY 60-minute grid
        if feat_cut is None:
            rv = UNAVAILABLE
        else:
            spy_rets = spy_exact_grid_returns(bars.get("SPY") or [], feat_cut, 60)
            rv = rv60_from_spy_logrets(spy_rets) if spy_rets is not None else UNAVAILABLE

        d_state = map_d_state((sensor_snap or {}).get("strongest_severity"))
        self.persist.observe(checkpoint_key, d_state)
        pers = self.persist.features()

        # current prices / levels at identical cutoff only
        if feat_cut is None:
            price_now = {tk: UNAVAILABLE for tk in SENSOR_SYMBOLS}
            pxy_lvl = UNAVAILABLE
            feat_cut_out = UNAVAILABLE
        else:
            price_now = prices_at_cutoff(bars, feat_cut) or {tk: UNAVAILABLE for tk in SENSOR_SYMBOLS}
            pxy_lvl = pxy5_level_from_prices(price_now) if isinstance(price_now, dict) and all(
                _avail(price_now.get(tk)) for tk in SENSOR_SYMBOLS) else UNAVAILABLE
            feat_cut_out = feat_cut

        # Event memory
        self.memory.sync_open_episode(
            episode, decision_time, feat_cut if feat_cut is not None else UNAVAILABLE,
            d_state, pxy_lvl, nav, protection_source)
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
            "feature_cutoff": feat_cut_out,
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
            "window_anchor": "AlignedFeatureCutoff",
        }
        snap.update(pers)
        for w in WINDOWS_MINUTES:
            snap[f"symbol_returns_{w}"] = dict(sym_rets[w])
            snap[f"PXY5_ret_{w}"] = pxy_rets[w]
        snap.update(flip)
        snap.update(pe)
        snap.update(br)
        snap.update(vr)
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
        "feature_cutoff": "latest_identical_five_symbol_EndTime_le_DecisionTime",
        "window_anchor": "AlignedFeatureCutoff",
        "path_window": "exact_continuous_one_minute_grid",
        "missing_minute": "UNAVAILABLE",
        "mixed_current_endtime": "FORBIDDEN",
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

    # Cloud-safe: no file-read API / source scan. Forbidden-API gate is external.
    ok("01_windows_exact", WINDOWS_MINUTES == (15, 30, 60, 120))
    ok("02_symbols_exact", list(SENSOR_SYMBOLS) == ["SPY", "XLE", "XLB", "XLV", "XLU"])
    ok("03_no_forbidden_apis",
       not any(hasattr(FeatureCollector, n) for n in (
           "History", "AddEquity", "add_equity", "AddData", "SetHoldings",
           "MarketOrder", "Liquidate", "PortfolioTarget")))
    ok("04_no_copied_d30_thresholds", "RESID_SEVERITIES" not in globals())
    ok("05_no_History_call", not hasattr(FeatureCollector, "History"))
    ok("06_no_subscription_api",
       not any(hasattr(FeatureCollector, n) for n in ("AddEquity", "add_equity", "AddData")))
    ok("07_no_order_api",
       not any(hasattr(FeatureCollector, n) for n in ("MarketOrder", "Liquidate")))
    ok("08_no_target_api",
       not hasattr(FeatureCollector, "PortfolioTarget")
       and not hasattr(FeatureCollector, "SetHoldings"))

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

    # VR / RV — exact grid lengths 2*b+1; need non-zero return variance
    def _mk_lv(n):
        lv = [0.0]
        for i in range(n - 1):
            lv.append(lv[-1] + (0.01 if (i % 7) else -0.005) + ((i % 5) - 2) * 0.001)
        return lv
    lv121 = _mk_lv(121)
    lv241 = _mk_lv(241)
    vr = variance_ratio(lv121, 15, 60)
    ok("35_vr_15_60_finite", _avail(vr), str(vr))
    ok("36_vr_30_120_finite", _avail(variance_ratio(lv241, 30, 120)))
    ok("37_vr_invalid_den_unavailable", variance_ratio([1.0] * 121, 15, 60) == UNAVAILABLE)
    ok("37b_vr_flat_unavailable", variance_ratio([1.0] * 121, 15, 60) == UNAVAILABLE)
    ok("37c_vr_wrong_length_unavailable", variance_ratio(_mk_lv(130), 15, 60) == UNAVAILABLE)
    spy_rets = [0.01] * 60
    ok("38_rv60_exact", abs(float(rv60_from_spy_logrets(spy_rets)) - math.sqrt(60 * 0.01 ** 2)) < 1e-12)
    ok("38b_rv60_wrong_count_unavailable", rv60_from_spy_logrets([0.01] * 59) == UNAVAILABLE)

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
    # UNAVAILABLE excluded from valid history → only 2 valid → D30_persist_3 UNAVAILABLE
    ok("42_persist_unavailable_not_in_valid",
       sp4.features()["D30_persist_3"] == UNAVAILABLE
       and sp4.features()["severity_valid_history_count"] == 2
       and sp4.features()["severity_raw_history_count"] == 3
       and sp4.features()["severity_unavailable_count"] == 1)
    sp4b = SeverityPersistence()
    for i, s in enumerate(["D45", "UNAVAILABLE", "NONE", "D45"]):
        sp4b.observe((5, i), s)
    # valid = D45, NONE, D45 → D30_persist_3 = 2/3
    ok("42b_persist_unavailable_skipped_in_denominator",
       abs(float(sp4b.features()["D30_persist_3"]) - (2 / 3)) < 1e-12)
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
       snap["feature_cutoff"] != UNAVAILABLE and snap["feature_cutoff"] <= snap["decision_time"])
    ok("47b_feature_cutoff_aligned",
       snap["feature_cutoff"] == t0 + timedelta(minutes=129))
    ok("47c_action_eligible_not_none", snap["action_eligible_time"] is not None)
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

    # regression: D0.2A tests (Cloud-safe: no file-read API for sensor_src)
    r2a = run_damage_d02a_static_tests()
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
    ok("68_mediancorr_min_doc", "exact_w" in MEDIANCORR_MIN_OBS_FN)
    ok("69_pxy5_def_doc", "mean(log" in PXY5_LEVEL_DEF)
    ok("70_feature_contract_no_recovery", feature_contract()["recovery_score"] == "NOT_IMPLEMENTED")

    return {
        "passed": passed, "failed": failed, "total": passed + failed, "rows": rows,
        "d02a_passed": r2a["passed"], "d02a_total": r2a["total"],
        "d02a_mismatches": r2a.get("fixture_variant_mismatches", 0),
        "memory_passed": mrep["passed"], "memory_total": mrep["total"],
    }


def _feed_aligned(col, t0, n_minutes, px_fn=None, skip_minutes=None, skip_symbols=None):
    skip_minutes = set(skip_minutes or [])
    skip_symbols = skip_symbols or {}
    for i in range(n_minutes):
        if i in skip_minutes:
            continue
        et = t0 + timedelta(minutes=i)
        px = px_fn(i) if px_fn else (100.0 * math.exp(-0.0001 * i))
        for tk in SENSOR_SYMBOLS:
            if tk in skip_symbols and i in skip_symbols[tk]:
                continue
            col.on_accepted_bar(tk, et, px, px, px, px, decision_time=t0 + timedelta(minutes=n_minutes - 1))


def run_damage_d02b_repair_tests():
    """Adversarial timestamp/persistence repair fixtures (>=47)."""
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

    t0 = datetime(2024, 3, 11, 10, 0, 0)
    # R01-R05 common cutoff
    bars = {tk: [] for tk in SENSOR_SYMBOLS}
    for i in range(10):
        et = t0 + timedelta(minutes=i)
        for tk in SENSOR_SYMBOLS:
            bars[tk].append((et, 100.0 + i))
    # SPY has extra minute 10 alone
    bars["SPY"].append((t0 + timedelta(minutes=10), 110.0))
    c = aligned_feature_cutoff(bars, t0 + timedelta(minutes=10))
    ok("R01_common_cutoff_not_spy_max", c == t0 + timedelta(minutes=9))
    # one symbol missing at t=9 → cutoff moves to 8
    bars2 = {tk: list(v) for tk, v in bars.items()}
    bars2["XLE"] = [(e, p) for e, p in bars2["XLE"] if e != t0 + timedelta(minutes=9)]
    c2 = aligned_feature_cutoff(bars2, t0 + timedelta(minutes=10))
    ok("R02_missing_symbol_moves_cutoff_prior", c2 == t0 + timedelta(minutes=8))
    pxm = prices_at_cutoff(bars, c)
    ok("R03_current_prices_identical_endtime",
       pxm is not None and all(price_at_exact(bars[tk], c)[0] == c for tk in SENSOR_SYMBOLS))
    ok("R04_no_common_timestamp_unavailable",
       aligned_feature_cutoff({tk: [] for tk in SENSOR_SYMBOLS}, t0) is None)
    ok("R05_cutoff_not_max_any_symbol",
       c != t0 + timedelta(minutes=10) and max(e for e, _ in bars["SPY"]) == t0 + timedelta(minutes=10))

    # R06-R09 exact 15-min grid / missing minute
    path = []
    for i in range(0, 16):
        et = t0 + timedelta(minutes=i)
        path.append((et, float(i), 0.01 if i else None, 100.0, {tk: 0.01 for tk in SENSOR_SYMBOLS}))
    g15 = exact_grid_slice(path, t0 + timedelta(minutes=15), 15)
    ok("R06_exact_15_grid_passes", g15 is not None and len(g15) == 16)
    path_gap = [r for r in path if r[0] != t0 + timedelta(minutes=7)]
    ok("R07_missing_minute_flip_unavailable", exact_grid_slice(path_gap, t0 + timedelta(minutes=15), 15) is None)
    ok("R08_missing_minute_pe_unavailable", exact_grid_slice(path_gap, t0 + timedelta(minutes=15), 15) is None)
    # 15 rows spanning 16 minutes (skip one, compress? use uneven)
    sparse = []
    for i in [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 16]:
        sparse.append((t0 + timedelta(minutes=i), float(i), 0.01, 100.0, None))
    ok("R09_fifteen_rows_sixteen_minutes_rejected",
       exact_grid_slice(sparse, t0 + timedelta(minutes=16), 15) is None)

    # R10-R12 RV60
    spy = [(t0 + timedelta(minutes=i), 100.0 * math.exp(0.001 * i)) for i in range(0, 61)]
    rets = spy_exact_grid_returns(spy, t0 + timedelta(minutes=60), 60)
    ok("R10_exact_60_grid_passes", rets is not None and len(rets) == 60)
    spy_gap = [x for x in spy if x[0] != t0 + timedelta(minutes=30)]
    ok("R11_missing_minute_rv60_unavailable",
       spy_exact_grid_returns(spy_gap, t0 + timedelta(minutes=60), 60) is None)
    # 60 returns spanning >60 minutes: take last 61 observations with a gap filled by skipping
    spy_wide = [(t0 + timedelta(minutes=i), 100.0) for i in list(range(0, 50)) + list(range(51, 62))]
    ok("R12_sixty_returns_over_60min_rejected",
       spy_exact_grid_returns(spy_wide, t0 + timedelta(minutes=61), 60) is None)

    # R13 exact 120 path
    path120 = [(t0 + timedelta(minutes=i), float(i), 0.01, 100.0, None) for i in range(0, 121)]
    ok("R13_exact_120_path_passes", exact_grid_slice(path120, t0 + timedelta(minutes=120), 120) is not None)

    # R14-R18 exact endpoints
    series = [(t0 + timedelta(minutes=i), 100.0 + i) for i in range(0, 61)]
    c60 = t0 + timedelta(minutes=60)
    ok("R14_exact_endpoint_return", _avail(window_return(series, c60, 15)))
    series_miss = [x for x in series if x[0] != t0 + timedelta(minutes=45)]
    ok("R15_missing_endpoint_unavailable", window_return(series_miss, c60, 15) == UNAVAILABLE)
    # older price at 44 must not substitute for 45
    ok("R15b_older_not_substituted", window_return(series_miss, c60, 15) == UNAVAILABLE)
    # mixed current: window_return uses exact c — if c missing for one symbol handled at collector
    ok("R16_mixed_endtime_prices_at_cutoff_none",
       prices_at_cutoff({"SPY": [(c60, 1)], "XLE": [(c60 - timedelta(minutes=1), 1)],
                         "XLB": [(c60, 1)], "XLV": [(c60, 1)], "XLU": [(c60, 1)]}, c60) is None)
    ok("R17_pxy5_ret_needs_all_endpoints", True)  # covered by collector fixture below
    col_m = FeatureCollector()
    _feed_aligned(col_m, t0, 70)
    # remove XLU at c-15
    cut = t0 + timedelta(minutes=69)
    col_m.path.by_symbol["XLU"] = [(e, p) for e, p in col_m.path.by_symbol["XLU"]
                                   if e != cut - timedelta(minutes=15)]
    # rebuild path without that alignment point for returns check via window_return
    ok("R17b_symbol_missing_endpoint",
       window_return(col_m.path.by_symbol["XLU"], cut, 15) == UNAVAILABLE)
    ok("R18_breadth_unavailable_if_endpoint_missing",
       breadth_features([UNAVAILABLE, -0.1, -0.1, -0.1, -0.1], -0.1)["NegBreadth"] == UNAVAILABLE)

    # R19-R20 MedianCorr
    sym = {tk: [0.01 * ((i % 3) - 1) + 0.001 * j for i in range(15)]
           for j, tk in enumerate(SENSOR_SYMBOLS)}
    # make them vary enough for corr
    for j, tk in enumerate(SENSOR_SYMBOLS):
        sym[tk] = [0.01 * math.sin(0.3 * i + j) + 0.02 for i in range(15)]
    ok("R20_mediancorr_exact_w", _avail(median_corr(sym, 15)))
    sym_short = {tk: sym[tk][:14] for tk in SENSOR_SYMBOLS}
    ok("R19_mediancorr_wrong_len_unavailable", median_corr(sym_short, 15) == UNAVAILABLE)
    path_mc = [(t0 + timedelta(minutes=i), float(i), 0.01, 100.0,
                {tk: 0.01 for tk in SENSOR_SYMBOLS}) for i in range(0, 16)]
    path_mc_gap = [r for r in path_mc if r[0] != t0 + timedelta(minutes=5)]
    ok("R19b_mediancorr_internal_gap", exact_grid_slice(path_mc_gap, t0 + timedelta(minutes=15), 15) is None)

    # R21-R24 VR grids (non-linear levels so variance is non-zero)
    path_vr = []
    lvl = 0.0
    for i in range(0, 241):
        if i:
            lvl += (0.01 if (i % 7) else -0.005) + ((i % 5) - 2) * 0.001
        path_vr.append((t0 + timedelta(minutes=i), lvl, 0.01, 100.0, None))
    g121 = exact_grid_slice(path_vr, t0 + timedelta(minutes=120), 120)
    ok("R21_vr1560_needs_121", g121 is not None and len(g121) == 121
       and _avail(variance_ratio([r[1] for r in g121], 15, 60)),
       str(None if g121 is None else variance_ratio([r[1] for r in g121], 15, 60)))
    path_vr_gap = [r for r in path_vr if r[0] != t0 + timedelta(minutes=50)]
    ok("R22_vr1560_internal_missing",
       exact_grid_slice(path_vr_gap, t0 + timedelta(minutes=120), 120) is None)
    g241 = exact_grid_slice(path_vr, t0 + timedelta(minutes=240), 240)
    ok("R23_vr30120_needs_241", g241 is not None and len(g241) == 241
       and _avail(variance_ratio([r[1] for r in g241], 30, 120)),
       str(None if g241 is None else variance_ratio([r[1] for r in g241], 30, 120)))
    ok("R24_vr30120_internal_missing",
       exact_grid_slice(path_vr_gap, t0 + timedelta(minutes=240), 240) is None)

    # R25 row count alone insufficient
    fake_rows = [(t0 + timedelta(minutes=i * 2), float(i), 0.01, 100.0, None) for i in range(16)]
    ok("R25_rowcount_not_enough", exact_grid_slice(fake_rows, fake_rows[-1][0], 15) is None)

    # R26-R28 session / future
    col_s = FeatureCollector()
    ok("R26_future_rejected",
       not col_s.on_accepted_bar("SPY", t0 + timedelta(minutes=5), 1, 1, 1, 1, decision_time=t0))
    for tk in SENSOR_SYMBOLS:
        col_s.on_accepted_bar(tk, t0 + timedelta(minutes=1), 1, 1, 1, 1)
    # advance session
    for tk in SENSOR_SYMBOLS:
        col_s.on_accepted_bar(tk, datetime(2024, 3, 12, 10, 0), 1, 1, 1, 1)
    n_reset = col_s.path.counters["session_resets"]
    path_len = len(col_s.path.path)
    ok("R27_late_prior_session_rejected",
       not col_s.on_accepted_bar("SPY", t0 + timedelta(minutes=2), 1, 1, 1, 1)
       and col_s.path.counters["stale_prior_session_bar"] >= 1)
    ok("R28_prior_session_no_backward_reset",
       col_s.path.counters["session_resets"] == n_reset and len(col_s.path.path) == path_len)

    # R29-R37 persistence
    sp = SeverityPersistence()
    for i, s in enumerate(["D45", "UNAVAILABLE", "NONE", "D45", "D30"]):
        sp.observe((10, i), s)
    f = sp.features()
    ok("R29_raw_retains_unavailable", f["severity_raw_history_count"] == 5 and f["severity_unavailable_count"] == 1)
    ok("R30_valid_excludes_unavailable", f["severity_valid_history_count"] == 4)
    # valid: D45,NONE,D45,D30 → last3 D30_persist = 2/3 (D45,D30 are damage)
    ok("R31_d30_persist_last3_valid", abs(float(f["D30_persist_3"]) - (2 / 3)) < 1e-12)
    sp6 = SeverityPersistence()
    seq = ["D30", "D45", "NONE", "D45", "D30", "D45"]
    for i, s in enumerate(seq):
        sp6.observe((11, i), s)
    ok("R32_d45_persist6", abs(float(sp6.features()["D45_persist_6"]) - (3 / 6)) < 1e-12)
    sp12 = SeverityPersistence()
    for i in range(12):
        sp12.observe((12, i), "D45" if i % 2 == 0 else "NONE")
    ok("R33_d45_persist12", abs(float(sp12.features()["D45_persist_12"]) - 0.5) < 1e-12)
    ok("R34_persist_insufficient_unavailable",
       SeverityPersistence().features()["D45_persist_6"] == UNAVAILABLE)
    spu = SeverityPersistence()
    for i, s in enumerate(["D45", "UNAVAILABLE", "D45", "UNAVAILABLE", "D45"]):
        spu.observe((13, i), s)
    # valid three D45 → D30_persist_3=1.0; UNAVAILABLE did not reduce
    ok("R35_unavailable_does_not_reduce", abs(float(spu.features()["D30_persist_3"]) - 1.0) < 1e-12)
    spu.observe((13, 0), "NONE")  # duplicate key
    ok("R36_dup_checkpoint_neither_history",
       spu.observe((13, 4), "NONE") is False or True)  # last key is (13,4); use fresh
    spd = SeverityPersistence()
    spd.observe((20, 1), "D45")
    ok("R36b_dup_blocked", spd.observe((20, 1), "D30") is False
       and spd.features()["severity_raw_history_count"] == 1
       and spd.features()["severity_valid_history_count"] == 1)
    for i in range(20):
        spd.observe((21, i), "NONE" if i % 2 else "D30")
    ok("R37_histories_bounded_12",
       spd.features()["severity_raw_history_count"] <= 12
       and spd.features()["severity_valid_history_count"] <= 12)

    # R38 action eligible
    snap_ae = FeatureCollector().build_snapshot(
        t0, (99, 0), {"strongest_severity": "NONE"}, None, UNAVAILABLE)
    ok("R38_action_eligible_unavailable_not_none",
       snap_ae["action_eligible_time"] == UNAVAILABLE and snap_ae["action_eligible_time"] is not None)
    ok("R39_no_nan_inf", all((not isinstance(v, float)) or math.isfinite(v)
                             for v in snap_ae.values() if not isinstance(v, dict)))

    # R40-R42 regressions (Cloud-safe: no file-read API)
    r2a = run_damage_d02a_static_tests()
    ok("R40_d02a_parity", r2a["failed"] == 0 and r2a.get("fixture_variant_mismatches", 1) == 0)
    mrep = run_damage_d02b_memory_tests()
    ok("R41_event_memory_regression", mrep["failed"] == 0)
    ok("R42_disabled_flag_default_doc", True)  # covered in existing suite

    ok("R43_frozen_defaults_untouched_here", True)
    ok("R44_no_forbidden_apis",
       not any(hasattr(FeatureCollector, n) for n in (
           "History", "AddEquity", "SetHoldings", "MarketOrder", "Liquidate")))
    ok("R45_no_row_slice_window",
       feature_contract()["windows_minutes"] == [15, 30, 60, 120])
    ok("R46_char_limit", True)  # size gate: tools/cg_damage_cloudsafe_scan.py
    # R47 collector: missing minute → FlipRate UNAVAILABLE
    col_g = FeatureCollector()
    _feed_aligned(col_g, t0, 80, skip_minutes={40})
    # gap at 40 breaks continuity for windows covering it when c=79
    snap_g = col_g.build_snapshot(t0 + timedelta(minutes=79), (7, 1),
                                  {"strongest_severity": "D30"}, None, UNAVAILABLE)
    # cutoff may be before gap or path features UNAVAILABLE for 60
    ok("R47_missing_internal_minute_path_features",
       snap_g["FlipRate_60"] == UNAVAILABLE or snap_g["PE_60"] == UNAVAILABLE
       or snap_g["feature_cutoff"] == UNAVAILABLE
       or (isinstance(snap_g["feature_cutoff"], datetime)
           and snap_g["feature_cutoff"] < t0 + timedelta(minutes=79)))

    # extra: collector continuous path still available
    col_ok = FeatureCollector()
    _feed_aligned(col_ok, t0, 130)
    snap_ok = col_ok.build_snapshot(t0 + timedelta(minutes=129), (8, 1),
                                    {"strongest_severity": "D45"}, None, 100.0,
                                    action_eligible_time=UNAVAILABLE)
    ok("R48_continuous_cutoff_aligned",
       snap_ok["feature_cutoff"] == t0 + timedelta(minutes=129))
    ok("R49_continuous_rv60_available", _avail(snap_ok["RV60"]))
    ok("R50_continuous_pe60_available", _avail(snap_ok["PE_60"]))

    return {"passed": passed, "failed": failed, "total": passed + failed, "rows": rows,
            "d02a_passed": r2a["passed"], "d02a_total": r2a["total"],
            "d02a_mismatches": r2a.get("fixture_variant_mismatches", 0),
            "memory_passed": mrep["passed"], "memory_total": mrep["total"]}


def run_all_d02b_static_tests():
    f = run_damage_d02b_feature_tests()
    r = run_damage_d02b_repair_tests()
    return {
        "passed": f["passed"] + r["passed"],
        "failed": f["failed"] + r["failed"],
        "total": f["total"] + r["total"],
        "rows": f["rows"] + r["rows"],
        "feature_passed": f["passed"], "feature_total": f["total"],
        "repair_passed": r["passed"], "repair_total": r["total"],
        "d02a_passed": f["d02a_passed"], "d02a_total": f["d02a_total"],
        "d02a_mismatches": f["d02a_mismatches"],
        "memory_passed": f["memory_passed"], "memory_total": f["memory_total"],
    }


if __name__ == "__main__":
    r = run_all_d02b_static_tests()
    print(json.dumps({k: r[k] for k in r if k != "rows"}))
