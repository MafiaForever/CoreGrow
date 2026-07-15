# region imports
from AlgorithmImports import *
# endregion
# cg_regime_rebal_time_diag.py
# CG-REGIME-TIME-D0-FIX2: event-time shadow timing matrix.
# Diagnostic-only. Zero trading impact. No SetHoldings/Liquidate/MarketOrder.
from datetime import date as _date
import heapq
import math

_TIMES = (
    (9, 45), (10, 15), (10, 45), (11, 15), (11, 45), (12, 15), (12, 45),
    (13, 15), (13, 45), (14, 15), (14, 45), (15, 15), (15, 45),
)
_N = 13
_NCOMBO = 2197
_NT = 169
_REGS = ("RISK_ON", "NEUTRAL", "RISK_OFF")
_RI = {"RISK_ON": 0, "NEUTRAL": 1, "RISK_OFF": 2}
_CASH = frozenset(("BIL", "SGOV", "USFR", "TFLO"))
_STALE_MIN = 5.0
_MISS_W = 0.02
_HEAP = 200
_LOG_BUDGET = 100000
_PROD_IDX = 0
_ACT_CAGR = 0.16994
_ACT_MAXDD = 0.23800
_ACT_SHARPE = 0.812
_WINDOWS = (
    ("TRAIN", _date(2012, 1, 1), _date(2018, 12, 31)),
    ("OOS", _date(2019, 1, 1), _date(2021, 12, 31)),
    ("CRISIS", _date(2022, 1, 1), _date(2025, 12, 31)),
    ("Y2012", _date(2012, 1, 1), _date(2012, 12, 31)),
    ("Y2015", _date(2015, 1, 1), _date(2015, 12, 31)),
    ("Y2018", _date(2018, 1, 1), _date(2018, 12, 31)),
    ("Y2020", _date(2020, 1, 1), _date(2020, 12, 31)),
    ("Y2022", _date(2022, 1, 1), _date(2022, 12, 31)),
    ("Y2023", _date(2023, 1, 1), _date(2023, 12, 31)),
    ("Y2024", _date(2024, 1, 1), _date(2024, 12, 31)),
    ("Y2025", _date(2025, 1, 1), _date(2025, 12, 31)),
    ("LIVE_RECENT", _date(2026, 1, 1), None),
)


def _hhmm(i):
    h, m = _TIMES[i]
    return f"{h:02d}:{m:02d}"


def _amo(i):
    h, m = _TIMES[i]
    return (h - 9) * 60 + (m - 30)


def _tk(sym):
    try:
        return str(sym.Value)
    except Exception:
        try:
            return str(sym.value)
        except Exception:
            return str(sym)


def _blank():
    return {
        "nav": 1.0, "peak": 1.0, "maxdd": 0.0,
        "wsum": 0.0, "wsum_sq": 0.0, "wdays": 0.0,
        "first_ts": None, "last_ts": None,
        "days": 0, "valid": 0, "invalid": 0, "heap": [],
    }


def _blank_cell():
    st = _blank()
    st["rets"] = []
    return st


def _upd(st, r, td, ts, ts0=None):
    st["days"] += 1
    st["valid"] += 1
    nav = st["nav"] * max(1e-12, 1.0 + r)
    st["nav"] = nav
    if nav > st["peak"]:
        st["peak"] = nav
    dd = 1.0 - nav / max(st["peak"], 1e-12)
    if dd > st["maxdd"]:
        st["maxdd"] = dd
    td = max(1, int(td))
    lr = math.log(max(1e-12, 1.0 + r))
    daily_lr = lr / float(td)
    w = float(td)
    st["wsum"] += daily_lr * w
    st["wsum_sq"] += daily_lr * daily_lr * w
    st["wdays"] += w
    if st["first_ts"] is None:
        st["first_ts"] = ts0 if ts0 is not None else ts
    st["last_ts"] = ts
    h = st["heap"]
    heapq.heappush(h, -r)
    if len(h) > _HEAP:
        heapq.heappop(h)


def _inv(st):
    st["days"] += 1
    st["invalid"] += 1


def _elapsed(st):
    a, b = st["first_ts"], st["last_ts"]
    if a is None or b is None:
        return None
    return (b - a).total_seconds() / (365.2425 * 86400.0)


def _w5(st):
    n = st["valid"]
    if n <= 0:
        return None
    arr = sorted(-x for x in st["heap"])
    k = max(1, int(0.05 * n + 0.999))
    k = min(k, len(arr))
    return sum(arr[:k]) / k


def _w5_list(rets):
    if not rets:
        return None
    arr = sorted(rets)
    k = max(1, int(0.05 * len(arr) + 0.999))
    return sum(arr[:k]) / k


def _f(x, d=4):
    if x is None:
        return "NA"
    try:
        v = float(x)
        if math.isnan(v) or math.isinf(v):
            return "NA"
        return f"{v:.{d}f}"
    except Exception:
        return "NA"


def _metrics(st):
    n = st["valid"]
    ey = _elapsed(st)
    cagr = None
    if ey is not None and ey > 0.0 and st["nav"] > 0.0:
        cagr = st["nav"] ** (1.0 / ey) - 1.0
    vol = sharpe = mean_d = None
    wd = st["wdays"]
    if wd > 0:
        mean_d = st["wsum"] / wd
        var_d = max(0.0, st["wsum_sq"] / wd - mean_d * mean_d)
        vol = math.sqrt(var_d) * math.sqrt(252.0)
        if vol > 1e-12:
            sharpe = mean_d * 252.0 / vol
    return {
        "days": st["days"], "valid": n, "invalid": st["invalid"],
        "nav": st["nav"], "maxdd": st["maxdd"], "cagr": cagr,
        "vol": vol, "sharpe": sharpe, "worst5": _w5(st),
        "mean": mean_d, "elapsed": ey, "wdays": wd,
        "first": st["first_ts"], "last": st["last_ts"],
    }


def _in_win(d, a, b):
    if d < a:
        return False
    if b is not None and d > b:
        return False
    return True


def _score(cagr, sharpe, maxdd):
    c = 0.0 if cagr is None else cagr
    s = 0.0 if sharpe is None else sharpe
    m = 0.0 if maxdd is None else maxdd
    return c + 0.50 * s - 1.50 * m


def _RtWeightedRet(pw, pp, cp):
    miss = 0.0
    rsum = 0.0
    for tk, wt in pw.items():
        try:
            wf = float(wt or 0.0)
        except Exception:
            continue
        if abs(wf) < 1e-12:
            continue
        if tk in _CASH or tk == "__CASH__":
            continue
        p0 = None if pp is None else pp.get(tk)
        p1 = None if cp is None else cp.get(tk)
        if p0 is None or p1 is None or p0 <= 0 or p1 <= 0:
            miss += abs(wf)
            continue
        rsum += wf * (p1 / p0 - 1.0)
    if miss > _MISS_W:
        return None
    return rsum


class CgRegimeRebalTimeDiagMixin:
    """Shadow timing matrix. No SetHoldings/Liquidate/orders."""

    def CgRegimeRebalTimeDiagInitialize(self):
        try:
            ov = getattr(self, "_rrx_param_overrides", {}) or {}

            def _p(k, d=""):
                v = self.get_parameter(k)
                if v is None or str(v).strip() == "":
                    v = ov.get(k, d)
                return v

            en = str(_p("cg_regime_rebal_time_diag_enable", "1") or "1").strip().lower()
            self.cg_regime_rebal_time_diag_enable = en in ("1", "true", "yes", "on")
            d1 = str(_p("cg_regime_rebal_time_d1_enable", "0") or "0").strip().lower()
            self.cg_regime_rebal_time_d1_enable = d1 in ("1", "true", "yes", "on")
            self.cg_regime_rebal_time_d1_enable = False
            if self.cg_regime_rebal_time_diag_enable:
                lp = list(getattr(self, "log_only_prefixes", None) or [])
                if "CG_REGIME_TIME_" not in lp:
                    lp.append("CG_REGIME_TIME_")
                self.log_only_prefixes = lp
                mp = list(getattr(self, "log_mute_prefixes", None) or [])
                if "CG_REGIME_TIME_" in mp:
                    self.log_mute_prefixes = [x for x in mp if x != "CG_REGIME_TIME_"]
            self.log(
                "[INIT] CG_REGIME_TIME_D0_FIX2,times=13,combinations=2197,"
                "common_sample=1,event_time=1,trade=0,fees=0,slippage=0,d1=0,baseline=W2"
            )
            if not self.cg_regime_rebal_time_diag_enable:
                return
            self._rt_prod_idx = _PROD_IDX
            self._rt_prod_hhmm = _hhmm(_PROD_IDX)
            self._rt_a = None
            self._rt_b = None
            self._rt_pend = None
            self._rt_hold_a = None
            self._rt_hold_pend = None
            self._rt_combo = [_blank() for _ in range(_NCOMBO)]
            self._rt_fixed = [_blank() for _ in range(_N)]
            self._rt_prod = _blank()
            self._rt_prod_only = _blank()
            self._rt_hold = _blank()
            self._rt_cell = [[_blank_cell() for _ in range(_N)] for _ in range(3)]
            self._rt_cache = []
            self._rt_sym_map = {}
            self._rt_trade_date_index = {}
            self._rt_log_bytes = 0
            self._rt_cand_snaps = 0
            self._rt_miss_snaps = 0
            self._rt_stale_snaps = 0
            self._rt_missing_px = 0
            self._rt_stale_px = 0
            self._rt_inv_w = 0
            self._rt_max_miss_w = 0.0
            self._rt_inv_reg = 0
            self._rt_all_tr = 0
            self._rt_prod_tr = 0
            self._rt_common_tr = 0
            self._rt_tgt_inv = 0
            self._rt_stale_inv = 0
            self._rt_miss_inv = 0
            self._rt_td_min = None
            self._rt_td_max = None
            self._rt_td_sum = 0
            self._rt_td_n = 0
            self._rt_td_one = 0
            self._rt_td_multi = 0
            self._rt_td_sample = []
            self._rt_emitted = False
            for i in range(_N):
                mins = _amo(i)
                self.schedule.on(
                    self.date_rules.every_day(self.sym_spy),
                    self.time_rules.after_market_open(self.sym_spy, mins),
                    lambda ii=i: self._RtSnap(ii),
                )
            self.schedule.on(
                self.date_rules.every_day(self.sym_spy),
                self.time_rules.after_market_open(self.sym_spy, 16),
                self._RtHoldingsSnap,
            )
        except Exception:
            self.cg_regime_rebal_time_diag_enable = False

    def CgRegimeRebalTimeDiagOnMinute(self):
        return None

    def _RtNoteTradeDate(self):
        d = self.time.date()
        m = self._rt_trade_date_index
        if d not in m:
            m[d] = len(m)
        return m[d]

    def _RtMapTk(self, tk):
        m = self._rt_sym_map
        if tk in m:
            return m[tk]
        try:
            for kv in self.securities:
                try:
                    sym = kv.Key if hasattr(kv, "Key") else kv
                    if _tk(sym) == tk:
                        m[tk] = sym
                        return sym
                except Exception:
                    continue
        except Exception:
            pass
        m[tk] = None
        return None

    def _RtPx(self, sym):
        try:
            sec = self.securities[sym]
            px = float(sec.price)
            if px <= 0:
                self._rt_missing_px += 1
                return None
            last = None
            try:
                last = sec.get_last_data()
            except Exception:
                try:
                    last = sec.GetLastData()
                except Exception:
                    last = getattr(getattr(sec, "cache", None), "last_data", None)
            if last is None:
                self._rt_missing_px += 1
                return None
            et = getattr(last, "end_time", None) or getattr(last, "EndTime", None)
            if et is None:
                self._rt_missing_px += 1
                return None
            age = (self.time - et).total_seconds() / 60.0
            if age < 0 or age > _STALE_MIN:
                self._rt_stale_px += 1
                return None
            return px
        except Exception:
            self._rt_missing_px += 1
            return None

    def _RtNewShell(self, weights, regime, d):
        return {
            "date": d, "regime": regime, "w": dict(weights),
            "px": [None] * _N, "valid": [False] * _N,
            "miss_w": [1.0] * _N, "snapped": [False] * _N,
            "ts": [None] * _N, "trade_idx": self._RtNoteTradeDate(),
        }

    def _RtSnapShell(self, shell, ti):
        if shell is None or shell["snapped"][ti]:
            return
        self._RtNoteTradeDate()
        w = shell["w"]
        miss = 0.0
        miss_n = 0
        stale_hit = 0
        pxmap = {}
        for tk, wt in w.items():
            try:
                wf = float(wt or 0.0)
            except Exception:
                continue
            if abs(wf) < 1e-12:
                continue
            if tk in _CASH:
                pxmap[tk] = 0.0
                continue
            before_m = self._rt_missing_px
            before_s = self._rt_stale_px
            sym = self._RtMapTk(tk)
            px = self._RtPx(sym) if sym is not None else None
            if px is None:
                miss += abs(wf)
                miss_n += 1
                if self._rt_stale_px > before_s:
                    stale_hit += 1
                elif self._rt_missing_px == before_m and sym is None:
                    self._rt_missing_px += 1
            else:
                pxmap[tk] = px
        shell["px"][ti] = pxmap
        shell["miss_w"][ti] = miss
        shell["snapped"][ti] = True
        shell["ts"][ti] = self.time
        self._rt_cand_snaps += 1
        if miss > self._rt_max_miss_w:
            self._rt_max_miss_w = miss
        if miss > _MISS_W:
            shell["valid"][ti] = False
            self._rt_inv_w += 1
            self._rt_miss_snaps += 1
            if stale_hit > 0:
                self._rt_stale_snaps += 1
                self._rt_stale_inv += 1
            else:
                self._rt_miss_inv += 1
        else:
            shell["valid"][ti] = True
            if miss_n > 0:
                self._rt_miss_snaps += 1

    def _RtSnap(self, ti):
        if not getattr(self, "cg_regime_rebal_time_diag_enable", False):
            return
        try:
            if getattr(self, "IsWarmingUp", False) or getattr(self, "is_warming_up", False):
                return
            pend = self._rt_pend
            if pend is None or pend["date"] != self.time.date():
                return
            self._RtSnapShell(pend, ti)
        except Exception:
            pass

    def _RtHoldingsSnap(self):
        if not getattr(self, "cg_regime_rebal_time_diag_enable", False):
            return
        try:
            if getattr(self, "IsWarmingUp", False) or getattr(self, "is_warming_up", False):
                return
            d = self.time.date()
            pend = self._rt_pend
            if pend is None or pend["date"] != d:
                return
            self._RtNoteTradeDate()
            tpv = float(self.portfolio.total_portfolio_value)
            if tpv <= 0:
                return
            w = {}
            try:
                for sym, h in self.portfolio.items():
                    try:
                        if h is None:
                            continue
                        hv = float(getattr(h, "HoldingsValue", None) or getattr(h, "holdings_value", 0) or 0)
                        if abs(hv) < 1e-12:
                            continue
                        tk = _tk(sym)
                        w[tk] = hv / tpv
                        if tk not in self._rt_sym_map:
                            self._rt_sym_map[tk] = sym
                    except Exception:
                        continue
            except Exception:
                pass
            try:
                cash = float(self.portfolio.cash)
                if abs(cash) > 1e-12:
                    w["__CASH__"] = cash / tpv
            except Exception:
                pass
            miss = 0.0
            pxmap = {}
            for tk, wt in w.items():
                if tk == "__CASH__" or tk in _CASH:
                    pxmap[tk] = 0.0
                    continue
                sym = self._RtMapTk(tk)
                px = self._RtPx(sym) if sym is not None else None
                if px is None:
                    miss += abs(wt)
                else:
                    pxmap[tk] = px
            ok = miss <= _MISS_W
            shell = {
                "date": d, "w": w, "px": pxmap, "valid": ok,
                "miss_w": miss, "ts": self.time,
                "trade_idx": self._rt_trade_date_index.get(d, 0),
            }
            if self._rt_hold_pend is not None and self._rt_hold_pend["date"] != d:
                done = self._rt_hold_pend
                self._rt_hold_pend = None
                if self._rt_hold_a is None:
                    self._rt_hold_a = done
                else:
                    self._RtFinalizeHold(self._rt_hold_a, done)
                    self._rt_hold_a = done
            self._rt_hold_pend = shell
        except Exception:
            pass

    def CgRegimeRebalTimeDiagCaptureTargets(self, combined, regime):
        if not getattr(self, "cg_regime_rebal_time_diag_enable", False):
            return
        try:
            if getattr(self, "IsWarmingUp", False) or getattr(self, "is_warming_up", False):
                return
            if not isinstance(combined, dict):
                return
            d = self.time.date()
            self._RtNoteTradeDate()
            if self._rt_pend is not None and self._rt_pend["date"] != d:
                done = self._rt_pend
                self._rt_pend = None
                if self._rt_a is None:
                    self._rt_a = done
                else:
                    self._rt_b = done
                    self._RtFinalizePair(self._rt_a, self._rt_b)
                    self._rt_a = self._rt_b
                    self._rt_b = None
            rg = str(regime or "").strip().upper()
            if rg not in _RI:
                rg = "NEUTRAL"
                self._rt_inv_reg += 1
            w = {}
            for k, v in combined.items():
                try:
                    wf = float(v or 0.0)
                except Exception:
                    continue
                if abs(wf) < 1e-12:
                    continue
                tk = _tk(k)
                w[tk] = wf
                if tk not in self._rt_sym_map:
                    self._rt_sym_map[tk] = k
            shell = self._RtNewShell(w, rg, d)
            self._rt_pend = shell
            self._RtSnapShell(shell, _PROD_IDX)
        except Exception:
            pass

    def _RtNoteTd(self, td):
        self._rt_td_n += 1
        self._rt_td_sum += td
        if self._rt_td_min is None or td < self._rt_td_min:
            self._rt_td_min = td
        if self._rt_td_max is None or td > self._rt_td_max:
            self._rt_td_max = td
        if td <= 1:
            self._rt_td_one += 1
        else:
            self._rt_td_multi += 1
        s = self._rt_td_sample
        if len(s) < 500:
            s.append(td)

    def _RtFinalizeHold(self, prev, cur):
        td = max(1, int(cur["trade_idx"]) - int(prev["trade_idx"]))
        ts0, ts1 = prev["ts"], cur["ts"]
        ok = bool(prev["valid"] and cur["valid"])
        r = _RtWeightedRet(prev["w"], prev["px"], cur["px"]) if ok else None
        if r is None:
            _inv(self._rt_hold)
        else:
            _upd(self._rt_hold, r, td, ts1, ts0)

    def _RtFinalizePair(self, prev, cur):
        pri = _RI.get(prev["regime"], 1)
        cri = _RI.get(cur["regime"], 1)
        d = cur["date"]
        td = max(1, int(cur["trade_idx"]) - int(prev["trade_idx"]))
        ts0 = prev["ts"][_PROD_IDX] or prev["ts"][0]
        ts1 = cur["ts"][_PROD_IDX] or cur["ts"][0]
        self._RtNoteTd(td)
        self._rt_all_tr += 1
        prod_ok = bool(prev["valid"][_PROD_IDX] and cur["valid"][_PROD_IDX])
        if prod_ok:
            self._rt_prod_tr += 1
        common = all(prev["valid"]) and all(cur["valid"])
        if common:
            self._rt_common_tr += 1
        else:
            self._rt_tgt_inv += 1
        flat = None
        prod_r = None
        if prod_ok:
            prod_r = _RtWeightedRet(
                prev["w"], prev["px"][_PROD_IDX], cur["px"][_PROD_IDX]
            )
            if prod_r is None:
                _inv(self._rt_prod_only)
            else:
                _upd(self._rt_prod_only, prod_r, td, ts1, ts0)
        if common:
            flat = [None] * _NT
            for pi in range(_N):
                pp = prev["px"][pi] or {}
                for ci in range(_N):
                    cp = cur["px"][ci] or {}
                    flat[pi * _N + ci] = _RtWeightedRet(prev["w"], pp, cp)
            if prod_r is None:
                prod_r = flat[_PROD_IDX * _N + _PROD_IDX]
            if prod_r is None:
                _inv(self._rt_prod)
            else:
                _upd(self._rt_prod, prod_r, td, ts1, ts0)
            for ron in range(_N):
                for neu in range(_N):
                    for roff in range(_N):
                        cid = ron * _NT + neu * _N + roff
                        pt = (ron, neu, roff)[pri]
                        ct = (ron, neu, roff)[cri]
                        r = flat[pt * _N + ct]
                        if r is None:
                            _inv(self._rt_combo[cid])
                        else:
                            _upd(self._rt_combo[cid], r, td, ts1, ts0)
            for t in range(_N):
                r = flat[t * _N + t]
                if r is None:
                    _inv(self._rt_fixed[t])
                else:
                    _upd(self._rt_fixed[t], r, td, ts1, ts0)
                cell = self._rt_cell[pri][t]
                if r is None:
                    _inv(cell)
                else:
                    _upd(cell, r, td, ts1, ts0)
                    cell["rets"].append(r)
        self._rt_cache.append(
            (pri, cri, d, td, ts0, ts1, flat, common, prod_ok, prod_r)
        )

    def _RtLog(self, msg):
        if self._rt_log_bytes >= _LOG_BUDGET:
            return False
        self.log(msg)
        self._rt_log_bytes += len(msg) + 1
        return True

    def _RtReplay(self, pick_times, windows=True, common_only=True):
        st = _blank()
        win = {w[0]: _blank() for w in _WINDOWS} if windows else None
        trans = {}
        for pri, cri, d, td, ts0, ts1, flat, common, prod_ok, prod_r in self._rt_cache:
            if common_only and not common:
                continue
            if pick_times == "PROD":
                if common_only:
                    if not common:
                        continue
                    r = prod_r
                    if flat is not None:
                        r = flat[_PROD_IDX * _N + _PROD_IDX]
                else:
                    if not prod_ok:
                        continue
                    r = prod_r
            elif isinstance(pick_times, int):
                if flat is None:
                    continue
                r = flat[pick_times * _N + pick_times]
            else:
                if flat is None:
                    continue
                ron, neu, roff = pick_times
                pt = (ron, neu, roff)[pri]
                ct = (ron, neu, roff)[cri]
                r = flat[pt * _N + ct]
            if r is None:
                _inv(st)
                if windows:
                    for wn, a, b in _WINDOWS:
                        if _in_win(d, a, b):
                            _inv(win[wn])
                continue
            _upd(st, r, td, ts1, ts0)
            if windows:
                for wn, a, b in _WINDOWS:
                    if _in_win(d, a, b):
                        _upd(win[wn], r, td, ts1, ts0)
            if isinstance(pick_times, tuple):
                key = (_REGS[pri], _REGS[cri])
                tr = trans.get(key)
                if tr is None:
                    tr = {"n": 0, "sum": 0.0, "nav": 1.0, "rets": []}
                    trans[key] = tr
                tr["n"] += 1
                tr["sum"] += r
                tr["nav"] = max(1e-12, tr["nav"] * (1.0 + r))
                tr["rets"].append(r)
        return st, win, trans

    def _RtCloseOpen(self):
        if self._rt_a is not None and self._rt_b is not None:
            self._RtFinalizePair(self._rt_a, self._rt_b)
            self._rt_a = self._rt_b
            self._rt_b = None
        if self._rt_pend is not None and self._rt_a is not None:
            if any(self._rt_pend["snapped"]) and any(self._rt_a["snapped"]):
                self._RtFinalizePair(self._rt_a, self._rt_pend)
                self._rt_a = None
                self._rt_pend = None
        if self._rt_hold_a is not None and self._rt_hold_pend is not None:
            if self._rt_hold_pend["date"] != self._rt_hold_a["date"]:
                self._RtFinalizeHold(self._rt_hold_a, self._rt_hold_pend)

    def _RtSanity(self, win_map):
        fail = 0
        for wn, st in win_map.items():
            m = _metrics(st)
            ey = m["elapsed"]
            cagr, nav, wd = m["cagr"], m["nav"], m["wdays"]
            if ey is not None and ey < 0:
                fail += 1
            if wd == 0 and m["valid"] > 0:
                fail += 1
            for v in (cagr, m["vol"], m["sharpe"], nav, m["maxdd"]):
                if v is not None and (math.isnan(v) or math.isinf(v)):
                    fail += 1
                    break
            if ey is not None and 0.90 <= ey <= 1.10 and cagr is not None:
                if abs(cagr - (nav - 1.0)) > 0.10:
                    fail += 1
                if cagr > 3.0 and nav < 2.0:
                    fail += 1
        return fail

    def CgRegimeRebalTimeDiagEmitFinal(self):
        if not getattr(self, "cg_regime_rebal_time_diag_enable", False):
            return
        if getattr(self, "_rt_emitted", False):
            return
        self._rt_emitted = True
        try:
            self._RtCloseOpen()
        except Exception:
            pass
        try:
            n = self._rt_td_n
            avg_td = (self._rt_td_sum / n) if n else None
            med_td = None
            if self._rt_td_sample:
                arr = sorted(self._rt_td_sample)
                mid = len(arr) // 2
                med_td = arr[mid] if len(arr) % 2 else 0.5 * (arr[mid - 1] + arr[mid])
            self._RtLog(
                f"CG_REGIME_TIME_INTERVAL_FINAL,events={n},"
                f"min_trading_days={self._rt_td_min if self._rt_td_min is not None else 'NA'},"
                f"median_trading_days={_f(med_td,2)},avg_trading_days={_f(avg_td,2)},"
                f"max_trading_days={self._rt_td_max if self._rt_td_max is not None else 'NA'},"
                f"one_day_intervals={self._rt_td_one},multi_day_intervals={self._rt_td_multi}"
            )
            pv = self._rt_prod_tr
            cv = self._rt_common_tr
            crate = (cv / pv) if pv > 0 else 0.0
            self._RtLog(
                f"CG_REGIME_TIME_SAMPLE_FINAL,all={self._rt_all_tr},"
                f"prod_valid={pv},common_valid={cv},common_rate={_f(crate)},"
                f"target_invalid={self._rt_tgt_inv},stale_invalid={self._rt_stale_inv},"
                f"missing_invalid={self._rt_miss_inv}"
            )
            po = _metrics(self._rt_prod_only)
            dc = None if po["cagr"] is None else po["cagr"] - _ACT_CAGR
            dd = None if po["maxdd"] is None else po["maxdd"] - _ACT_MAXDD
            tgt_pass = (
                dc is not None and dd is not None
                and abs(dc) <= 0.04 and abs(dd) <= 0.10
            )
            self._RtLog(
                f"CG_REGIME_TIME_RECON_TARGET_FINAL,events={po['days']},"
                f"valid_events={po['valid']},"
                f"first={po['first']},last={po['last']},"
                f"elapsed_years={_f(po['elapsed'])},nav={_f(po['nav'])},"
                f"cagr={_f(po['cagr'])},maxdd={_f(po['maxdd'])},"
                f"vol={_f(po['vol'])},sharpe={_f(po['sharpe'])},"
                f"actual_cagr={_ACT_CAGR},actual_maxdd={_ACT_MAXDD},"
                f"actual_sharpe={_ACT_SHARPE},"
                f"delta_cagr={_f(dc)},delta_maxdd={_f(dd)},"
                f"recon={'PASS' if tgt_pass else 'FAIL'}"
            )
            hm = _metrics(self._rt_hold)
            hdc = None if hm["cagr"] is None else hm["cagr"] - _ACT_CAGR
            hdd = None if hm["maxdd"] is None else hm["maxdd"] - _ACT_MAXDD
            hold_pass = (
                hdc is not None and hdd is not None
                and abs(hdc) <= 0.03 and abs(hdd) <= 0.06
            )
            self._RtLog(
                f"CG_REGIME_TIME_RECON_HOLDINGS_FINAL,events={hm['days']},"
                f"valid_events={hm['valid']},"
                f"first={hm['first']},last={hm['last']},"
                f"elapsed_years={_f(hm['elapsed'])},nav={_f(hm['nav'])},"
                f"cagr={_f(hm['cagr'])},maxdd={_f(hm['maxdd'])},"
                f"vol={_f(hm['vol'])},sharpe={_f(hm['sharpe'])},"
                f"actual_cagr={_ACT_CAGR},actual_maxdd={_ACT_MAXDD},"
                f"actual_sharpe={_ACT_SHARPE},"
                f"delta_cagr={_f(hdc)},delta_maxdd={_f(hdd)},"
                f"recon={'PASS' if hold_pass else 'FAIL'}"
            )
            tgt_broken = (
                (dc is not None and abs(dc) > 0.15)
                or (dd is not None and abs(dd) > 0.25)
                or po["nav"] < 1.5 or po["nav"] > 80.0
            )
            overall_recon = hold_pass and not tgt_broken
            prod_m = _metrics(self._rt_prod)
            self._RtLog(
                f"CG_REGIME_TIME_PROD_FINAL,time={self._rt_prod_hhmm},"
                f"days={prod_m['days']},valid_days={prod_m['valid']},"
                f"invalid_days={prod_m['invalid']},nav={_f(prod_m['nav'])},"
                f"cagr={_f(prod_m['cagr'])},maxdd={_f(prod_m['maxdd'])},"
                f"worst5={_f(prod_m['worst5'],6)},vol={_f(prod_m['vol'])},"
                f"sharpe={_f(prod_m['sharpe'])},elapsed_years={_f(prod_m['elapsed'])}"
            )
            best_fixed_i = 0
            best_fixed_cagr = None
            fixed_ms = []
            for t in range(_N):
                m = _metrics(self._rt_fixed[t])
                fixed_ms.append(m)
                dn = (m["nav"] - prod_m["nav"]) if m["nav"] is not None else None
                ddv = (m["maxdd"] - prod_m["maxdd"]) if m["maxdd"] is not None else None
                self._RtLog(
                    f"CG_REGIME_TIME_FIXED_FINAL,time={_hhmm(t)},"
                    f"days={m['days']},valid_days={m['valid']},"
                    f"nav={_f(m['nav'])},cagr={_f(m['cagr'])},maxdd={_f(m['maxdd'])},"
                    f"worst5={_f(m['worst5'],6)},vol={_f(m['vol'])},sharpe={_f(m['sharpe'])},"
                    f"delta_nav_vs_prod={_f(dn)},delta_dd_vs_prod={_f(ddv)}"
                )
                if m["cagr"] is not None and (best_fixed_cagr is None or m["cagr"] > best_fixed_cagr):
                    best_fixed_cagr = m["cagr"]
                    best_fixed_i = t
            bf = fixed_ms[best_fixed_i]
            for ri, rg in enumerate(_REGS):
                for t in range(_N):
                    st = self._rt_cell[ri][t]
                    m = _metrics(st)
                    rets = st.get("rets") or []
                    med = pos = None
                    if rets:
                        sr = sorted(rets)
                        mid = len(sr) // 2
                        med = sr[mid] if len(sr) % 2 else 0.5 * (sr[mid - 1] + sr[mid])
                        pos = sum(1 for x in rets if x > 0) / len(rets)
                    self._RtLog(
                        f"CG_REGIME_TIME_CELL_FINAL,regime={rg},time={_hhmm(t)},"
                        f"days={m['days']},valid_days={m['valid']},"
                        f"nav={_f(m['nav'])},mean={_f(m['mean'],6)},median={_f(med,6)},"
                        f"positive_rate={_f(pos)},maxdd={_f(m['maxdd'])},"
                        f"worst5={_f(_w5_list(rets),6)},vol={_f(m['vol'])},sharpe={_f(m['sharpe'])}"
                    )
            pvv = max(1, prod_m["valid"])
            pdd = prod_m["maxdd"] or 0.0
            pvol = prod_m["vol"] if prod_m["vol"] is not None else 1e9
            ranked = []
            for ron in range(_N):
                for neu in range(_N):
                    for roff in range(_N):
                        cid = ron * _NT + neu * _N + roff
                        m = _metrics(self._rt_combo[cid])
                        if m["valid"] < 0.95 * pvv:
                            continue
                        if m["maxdd"] > pdd + 0.005:
                            continue
                        if m["vol"] is not None and m["vol"] > pvol + 0.01:
                            continue
                        sc = _score(m["cagr"], m["sharpe"], m["maxdd"])
                        ranked.append((sc, cid, ron, neu, roff, m))
            ranked.sort(key=lambda x: x[0], reverse=True)
            top = ranked[:30]
            for rank, row in enumerate(top, 1):
                sc, cid, ron, neu, roff, m = row
                if not self._RtLog(
                    f"CG_REGIME_TIME_COMBO_FINAL,rank={rank},"
                    f"ron={_hhmm(ron)},neutral={_hhmm(neu)},roff={_hhmm(roff)},"
                    f"days={m['days']},valid_days={m['valid']},"
                    f"nav={_f(m['nav'])},cagr={_f(m['cagr'])},maxdd={_f(m['maxdd'])},"
                    f"worst5={_f(m['worst5'],6)},vol={_f(m['vol'])},sharpe={_f(m['sharpe'])},"
                    f"score={_f(sc)},delta_nav_vs_prod={_f(m['nav']-prod_m['nav'])},"
                    f"delta_dd_vs_prod={_f(m['maxdd']-prod_m['maxdd'])}"
                ):
                    break
            win_n = 5 if self._rt_log_bytes > _LOG_BUDGET * 0.75 else 10
            detailed = []
            for rank, row in enumerate(top[:win_n], 1):
                sc, cid, ron, neu, roff, m0 = row
                st, win, trans = self._RtReplay((ron, neu, roff), windows=True)
                detailed.append((rank, ron, neu, roff, st, win, trans, m0, sc))
                for wn, a, b in _WINDOWS:
                    wm = _metrics(win[wn])
                    if not self._RtLog(
                        f"CG_REGIME_TIME_WINDOW_FINAL,rank={rank},"
                        f"ron={_hhmm(ron)},neutral={_hhmm(neu)},roff={_hhmm(roff)},"
                        f"window={wn},nav={_f(wm['nav'])},cagr={_f(wm['cagr'])},"
                        f"maxdd={_f(wm['maxdd'])},worst5={_f(wm['worst5'],6)},"
                        f"vol={_f(wm['vol'])},sharpe={_f(wm['sharpe'])}"
                    ):
                        break
            for rank, ron, neu, roff, st, win, trans, m0, sc in detailed:
                for (fr, to), tr in sorted(trans.items()):
                    mean = tr["sum"] / tr["n"] if tr["n"] else None
                    if not self._RtLog(
                        f"CG_REGIME_TIME_TRANS_FINAL,rank={rank},"
                        f"from_regime={fr},to_regime={to},days={tr['n']},"
                        f"mean={_f(mean,6)},nav={_f(tr['nav'])},"
                        f"worst5={_f(_w5_list(tr['rets']),6)}"
                    ):
                        break
            _, prod_win, _ = self._RtReplay("PROD", windows=True)
            sanity = self._RtSanity(prod_win)
            for _, _, _, _, _, win, _, _, _ in detailed:
                sanity += self._RtSanity(win)
            why = None
            if not overall_recon:
                why = "production_shadow_not_reconciled"
            elif crate < 0.85:
                why = "insufficient_common_sample"
            elif sanity > 0:
                why = "metric_sanity_failure"
            pick = None
            eligible = []
            if why is None:
                prod_wm = {k: _metrics(v) for k, v in prod_win.items()}

                def _accept(m, win):
                    if m["cagr"] is None or prod_m["cagr"] is None:
                        return False
                    if m["cagr"] < prod_m["cagr"]:
                        return False
                    if m["maxdd"] > (prod_m["maxdd"] or 0):
                        return False
                    if m["sharpe"] is None or prod_m["sharpe"] is None or m["sharpe"] < prod_m["sharpe"]:
                        return False
                    if m["worst5"] is None or prod_m["worst5"] is None or m["worst5"] < prod_m["worst5"]:
                        return False
                    if m["vol"] is not None and prod_m["vol"] is not None and m["vol"] > prod_m["vol"] + 0.005:
                        return False
                    oos = _metrics(win["OOS"])
                    poo = prod_wm["OOS"]
                    if oos["sharpe"] is None or poo["sharpe"] is None or oos["sharpe"] < 0.97 * poo["sharpe"]:
                        return False
                    for yk in ("Y2015", "Y2020", "Y2022"):
                        ym = _metrics(win[yk])
                        if ym["maxdd"] > (prod_wm[yk]["maxdd"] or 0):
                            return False
                    cr = _metrics(win["CRISIS"])
                    pc = prod_wm["CRISIS"]
                    if cr["cagr"] is None or pc["cagr"] is None:
                        return False
                    if cr["cagr"] < pc["cagr"] - 0.0020:
                        return False
                    if m["valid"] < 0.95 * pvv:
                        return False
                    if bf["cagr"] is None or m["cagr"] < bf["cagr"]:
                        return False
                    if bf["sharpe"] is None or m["sharpe"] is None or m["sharpe"] < bf["sharpe"]:
                        return False
                    dd_ok = m["maxdd"] <= (bf["maxdd"] or 0) - 0.002
                    cagr_ok = m["cagr"] >= bf["cagr"] + 0.0020
                    return dd_ok or cagr_ok

                for rank, row in enumerate(top, 1):
                    sc, cid, ron, neu, roff, m0 = row
                    found = None
                    for drow in detailed:
                        if drow[1] == ron and drow[2] == neu and drow[3] == roff:
                            found = drow
                            break
                    if found is None:
                        st, win, trans = self._RtReplay((ron, neu, roff), windows=True)
                        m = _metrics(st)
                    else:
                        st, win, trans = found[4], found[5], found[6]
                        m = _metrics(st)
                    if _accept(m, win):
                        ndist = len({ron, neu, roff})
                        eligible.append((m["cagr"], m["sharpe"], -m["maxdd"], -ndist, -roff, ron, neu, roff, m, win))
                eligible.sort(reverse=True)
                why = "no_eligible"
                if eligible:
                    e = eligible[0]
                    ron, neu, roff, m, win = e[5], e[6], e[7], e[8], e[9]
                    n_pass = 0
                    for reg_i, base in enumerate((ron, neu, roff)):
                        for delta in (-1, 1):
                            nt = base + delta
                            if nt < 0 or nt >= _N:
                                continue
                            cand = [ron, neu, roff]
                            cand[reg_i] = nt
                            stn, _, _ = self._RtReplay(tuple(cand), windows=False)
                            mn = _metrics(stn)
                            ok = (
                                mn["cagr"] is not None and m["cagr"] is not None
                                and mn["cagr"] >= m["cagr"] - 0.0030
                                and mn["maxdd"] <= m["maxdd"] + 0.0030
                                and mn["sharpe"] is not None and m["sharpe"] is not None
                                and mn["sharpe"] >= m["sharpe"] - 0.03
                            )
                            n_pass += int(ok)
                            self._RtLog(
                                f"CG_REGIME_TIME_NEIGHBOR_FINAL,"
                                f"base_pick={_hhmm(ron)}/{_hhmm(neu)}/{_hhmm(roff)},"
                                f"changed_regime={_REGS[reg_i]},candidate_time={_hhmm(nt)},"
                                f"nav={_f(mn['nav'])},cagr={_f(mn['cagr'])},"
                                f"maxdd={_f(mn['maxdd'])},sharpe={_f(mn['sharpe'])},"
                                f"pass={int(ok)}"
                            )
                    if n_pass >= 4:
                        pick = (ron, neu, roff)
                        why = "accepted"
                    else:
                        why = "unstable_time_surface"
            if pick is None:
                self._RtLog(
                    f"CG_REGIME_TIME_SELECT_FINAL,pick=NONE,ron=NA,neutral=NA,roff=NA,"
                    f"best_fixed={_hhmm(best_fixed_i)},eligible={len(eligible)},"
                    f"why={why},overall_recon={int(overall_recon)},"
                    f"common_rate={_f(crate)},metric_sanity_fail={sanity},trade=0"
                )
            else:
                ron, neu, roff = pick
                self._RtLog(
                    f"CG_REGIME_TIME_SELECT_FINAL,pick={_hhmm(ron)}/{_hhmm(neu)}/{_hhmm(roff)},"
                    f"ron={_hhmm(ron)},neutral={_hhmm(neu)},roff={_hhmm(roff)},"
                    f"best_fixed={_hhmm(best_fixed_i)},eligible={len(eligible)},"
                    f"why={why},overall_recon={int(overall_recon)},"
                    f"common_rate={_f(crate)},metric_sanity_fail={sanity},trade=0"
                )
            self._RtLog(
                f"CG_REGIME_TIME_DATA_FINAL,candidate_snapshots={self._rt_cand_snaps},"
                f"missing_snapshots={self._rt_miss_snaps},"
                f"stale_snapshots={self._rt_stale_snaps},"
                f"invalid_weight_events={self._rt_inv_w},"
                f"max_missing_weight={_f(self._rt_max_miss_w)},"
                f"invalid_regime_days={self._rt_inv_reg},"
                f"metric_sanity_fail={sanity},"
                f"missing_price_count={self._rt_missing_px},"
                f"stale_price_count={self._rt_stale_px},"
                f"prod_time={self._rt_prod_hhmm},"
                f"target_snapshot_time=09:45,holdings_snapshot_time=09:46,"
                f"dd_sampling=EVENT_TIMESTAMPS,fees=0,slippage=0,"
                f"overall_recon={'PASS' if overall_recon else 'FAIL'}"
            )
        except Exception as exc:
            try:
                self.log(
                    f"CG_REGIME_TIME_DATA_FINAL,error={type(exc).__name__},fees=0,slippage=0"
                )
            except Exception:
                pass
