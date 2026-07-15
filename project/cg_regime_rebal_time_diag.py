# region imports
from AlgorithmImports import *
# endregion
# cg_regime_rebal_time_diag.py
# CG-REGIME-REBAL-TIME-D0: coarse regime-dependent intraday rebalance timing.
# Diagnostic-only. Zero trading impact. Never mutates production targets/orders.
from datetime import date as _date
import heapq

_TIMES = (
    (9, 45), (10, 15), (10, 45), (11, 15), (11, 45), (12, 15), (12, 45),
    (13, 15), (13, 45), (14, 15), (14, 45), (15, 15), (15, 45),
)
_N = 13
_NCOMBO = 13 * 13 * 13  # 2197
_NT = 13 * 13  # 169
_REGS = ("RISK_ON", "NEUTRAL", "RISK_OFF")
_RI = {"RISK_ON": 0, "NEUTRAL": 1, "RISK_OFF": 2}
_CASH = frozenset(("BIL", "SGOV", "USFR", "TFLO"))
_STALE_MIN = 5.0
_MISS_W = 0.02
_HEAP = 200
_LOG_BUDGET = 90000
_PROD_IDX = 0  # 09:45 = after_market_open+15
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
        "sum_r": 0.0, "sum_r2": 0.0,
        "days": 0, "valid": 0, "invalid": 0,
        "heap": [],
    }


def _blank_cell():
    st = _blank()
    st["rets"] = []
    return st


def _upd(st, r, ok):
    st["days"] += 1
    if not ok:
        st["invalid"] += 1
        return
    st["valid"] += 1
    st["sum_r"] += r
    st["sum_r2"] += r * r
    st["nav"] = max(0.01, st["nav"] * (1.0 + r))
    if st["nav"] > st["peak"]:
        st["peak"] = st["nav"]
    dd = 1.0 - st["nav"] / max(st["peak"], 1e-9)
    if dd > st["maxdd"]:
        st["maxdd"] = dd
    h = st["heap"]
    heapq.heappush(h, -r)
    if len(h) > _HEAP:
        heapq.heappop(h)


def _ann(sum_r, n):
    if n < 20:
        return None
    return (1.0 + sum_r / n) ** 252 - 1.0


def _vol(sum_r, sum_r2, n):
    if n < 5:
        return None
    m = sum_r / n
    v = max(0.0, sum_r2 / n - m * m)
    return (v ** 0.5) * (252 ** 0.5)


def _sharpe(sum_r, sum_r2, n):
    v = _vol(sum_r, sum_r2, n)
    a = _ann(sum_r, n)
    if v is None or a is None or v < 1e-12:
        return None
    return a / v


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
        return f"{float(x):.{d}f}"
    except Exception:
        return "NA"


def _metrics(st):
    n = st["valid"]
    return {
        "days": st["days"], "valid": n, "invalid": st["invalid"],
        "nav": st["nav"], "maxdd": st["maxdd"],
        "cagr": _ann(st["sum_r"], n),
        "vol": _vol(st["sum_r"], st["sum_r2"], n),
        "sharpe": _sharpe(st["sum_r"], st["sum_r2"], n),
        "worst5": _w5(st),
        "mean": (st["sum_r"] / n) if n else None,
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
            if self.cg_regime_rebal_time_diag_enable:
                lp = list(getattr(self, "log_only_prefixes", None) or [])
                if "CG_REGIME_TIME_" not in lp:
                    lp.append("CG_REGIME_TIME_")
                self.log_only_prefixes = lp
                mp = list(getattr(self, "log_mute_prefixes", None) or [])
                if "CG_REGIME_TIME_" in mp:
                    self.log_mute_prefixes = [x for x in mp if x != "CG_REGIME_TIME_"]
            self.log(
                "[INIT] CG_REGIME_TIME_D0,times=13,combinations=2197,"
                "fixed_controls=13,trade=0,slippage=0,fees=0,baseline=W2"
            )
            if not self.cg_regime_rebal_time_diag_enable:
                return
            # Mirror production schedule: after_market_open(SPY, 15) → 09:45 ET.
            if not hasattr(self, "_rt_prod_amo_mins") or self._rt_prod_amo_mins is None:
                self._rt_prod_amo_mins = 15
            prod_mins = int(self._rt_prod_amo_mins)
            matched = None
            for i in range(_N):
                if _amo(i) == prod_mins:
                    matched = i
                    break
            self._rt_prod_idx = matched if matched is not None else _PROD_IDX
            self._rt_prod_hhmm = _hhmm(self._rt_prod_idx)
            self._rt_prod_offgrid = matched is None
            if self._rt_prod_offgrid:
                # Keep exact HH:MM label from offset (09:30 + mins).
                th = 9 + (30 + prod_mins) // 60
                tm = (30 + prod_mins) % 60
                self._rt_prod_hhmm = f"{th:02d}:{tm:02d}"
            self._rt_a = None
            self._rt_b = None
            self._rt_pend = None
            self._rt_combo = [_blank() for _ in range(_NCOMBO)]
            self._rt_fixed = [_blank() for _ in range(_N)]
            self._rt_prod = _blank()
            self._rt_cell = [[_blank_cell() for _ in range(_N)] for _ in range(3)]
            self._rt_cache = []
            self._rt_sym_map = {}
            self._rt_log_bytes = 0
            self._rt_cand_snaps = 0
            self._rt_miss_snaps = 0
            self._rt_inv_w = 0
            self._rt_max_miss_w = 0.0
            self._rt_inv_reg = 0
            self._rt_emitted = False
            for i in range(_N):
                mins = _amo(i)
                # bind index via default-arg
                self.schedule.on(
                    self.date_rules.every_day(self.sym_spy),
                    self.time_rules.after_market_open(self.sym_spy, mins),
                    lambda ii=i: self._RtSnap(ii),
                )
        except Exception:
            self.cg_regime_rebal_time_diag_enable = False

    def CgRegimeRebalTimeDiagOnMinute(self):
        return None

    def _RtMapTk(self, tk):
        m = self._rt_sym_map
        if tk in m:
            return m[tk]
        # attrs sym_*
        for attr, val in list(self.__dict__.items()):
            if not attr.startswith("sym_"):
                continue
            try:
                if val is not None and _tk(val) == tk:
                    m[tk] = val
                    return val
            except Exception:
                continue
        for s in getattr(self, "panic_tactical_universe", []) or []:
            try:
                if _tk(s) == tk:
                    m[tk] = s
                    return s
            except Exception:
                continue
        for s in getattr(self, "active_symbols", []) or []:
            try:
                if _tk(s) == tk:
                    m[tk] = s
                    return s
            except Exception:
                continue
        # securities scan
        try:
            secs = self.securities
            for kv in secs:
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
                return None
            last = None
            try:
                last = sec.get_last_data()
            except Exception:
                try:
                    last = sec.GetLastData()
                except Exception:
                    last = getattr(getattr(sec, "cache", None), "last_data", None)
            if last is not None:
                et = getattr(last, "end_time", None) or getattr(last, "EndTime", None)
                if et is not None:
                    age = (self.time - et).total_seconds() / 60.0
                    if age > _STALE_MIN:
                        return None
            return px
        except Exception:
            return None

    def _RtNewShell(self, weights, regime, d):
        return {
            "date": d,
            "regime": regime,
            "w": dict(weights),
            "px": [None] * _N,
            "valid": [False] * _N,
            "miss_w": [1.0] * _N,
            "snapped": [False] * _N,
        }

    def _RtSnapShell(self, shell, ti):
        if shell is None or shell["snapped"][ti]:
            return
        w = shell["w"]
        miss = 0.0
        ok_n = 0
        miss_n = 0
        pxmap = {}
        for tk, wt in w.items():
            try:
                wf = float(wt or 0.0)
            except Exception:
                continue
            if abs(wf) < 1e-12:
                continue
            if tk in _CASH:
                pxmap[tk] = 0.0  # cash: skip price; ret treated 0
                ok_n += 1
                continue
            sym = self._RtMapTk(tk)
            px = self._RtPx(sym) if sym is not None else None
            if px is None:
                miss += abs(wf)
                miss_n += 1
            else:
                pxmap[tk] = px
                ok_n += 1
        shell["px"][ti] = pxmap
        shell["miss_w"][ti] = miss
        shell["snapped"][ti] = True
        self._rt_cand_snaps += 1
        if miss > self._rt_max_miss_w:
            self._rt_max_miss_w = miss
        if miss > _MISS_W:
            shell["valid"][ti] = False
            self._rt_inv_w += 1
            self._rt_miss_snaps += 1
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
            if pend is None:
                return
            if pend["date"] != self.time.date():
                return
            self._RtSnapShell(pend, ti)
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
            # On capture morning of D+2: promote completed D+1, finalize D→D+1
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
                w[_tk(k)] = wf
            shell = self._RtNewShell(w, rg, d)
            self._rt_pend = shell
            # snap T00 at capture (prod time)
            self._RtSnapShell(shell, _PROD_IDX)
        except Exception:
            pass

    def _RtTransMatrix(self, prev, cur):
        """13x13 flat returns using PREV day weights; None if invalid."""
        flat = [None] * _NT
        pw = prev["w"]
        for pi in range(_N):
            if not prev["valid"][pi]:
                continue
            pp = prev["px"][pi] or {}
            for ci in range(_N):
                if not cur["valid"][ci]:
                    continue
                cp = cur["px"][ci] or {}
                rsum = 0.0
                ok = True
                for tk, wt in pw.items():
                    try:
                        wf = float(wt or 0.0)
                    except Exception:
                        continue
                    if abs(wf) < 1e-12:
                        continue
                    if tk in _CASH:
                        continue  # ret 0
                    p0 = pp.get(tk)
                    p1 = cp.get(tk)
                    if p0 is None or p1 is None or p0 <= 0 or p1 <= 0:
                        # missing priced weight already checked via miss_w; skip symbol
                        continue
                    rsum += wf * (p1 / p0 - 1.0)
                flat[pi * _N + ci] = rsum
        return flat

    def _RtFinalizePair(self, prev, cur):
        flat = self._RtTransMatrix(prev, cur)
        pri = _RI.get(prev["regime"], 1)
        cri = _RI.get(cur["regime"], 1)
        d = cur["date"]
        self._rt_cache.append((pri, cri, d, flat, list(prev["valid"]), list(cur["valid"])))
        # combos
        for ron in range(_N):
            for neu in range(_N):
                for roff in range(_N):
                    cid = ron * _NT + neu * _N + roff
                    pt = (ron, neu, roff)[pri]
                    ct = (ron, neu, roff)[cri]
                    ok = prev["valid"][pt] and cur["valid"][ct]
                    r = flat[pt * _N + ct] if ok else None
                    if r is None:
                        ok = False
                        r = 0.0
                    _upd(self._rt_combo[cid], r, ok)
        # fixed + prod
        for t in range(_N):
            ok = prev["valid"][t] and cur["valid"][t]
            r = flat[t * _N + t] if ok else None
            if r is None:
                ok = False
                r = 0.0
            _upd(self._rt_fixed[t], r, ok)
            if t == self._rt_prod_idx:
                _upd(self._rt_prod, r, ok)
        # cells by PREV regime: same-time
        for t in range(_N):
            ok = prev["valid"][t] and cur["valid"][t]
            r = flat[t * _N + t] if ok else None
            if r is None:
                ok = False
                r = 0.0
            cell = self._rt_cell[pri][t]
            _upd(cell, r, ok)
            if ok:
                cell["rets"].append(r)

    def _RtLog(self, msg):
        if self._rt_log_bytes >= _LOG_BUDGET:
            return False
        self.log(msg)
        self._rt_log_bytes += len(msg) + 1
        return True

    def _RtReplay(self, pick_times, windows=True):
        """pick_times: (ron,neu,roff) or int for fixed, or 'PROD'."""
        st = _blank()
        win = {w[0]: _blank() for w in _WINDOWS} if windows else None
        trans = {}
        for pri, cri, d, flat, pv, cv in self._rt_cache:
            if pick_times == "PROD":
                pt = ct = self._rt_prod_idx
            elif isinstance(pick_times, int):
                pt = ct = pick_times
            else:
                ron, neu, roff = pick_times
                pt = (ron, neu, roff)[pri]
                ct = (ron, neu, roff)[cri]
            ok = bool(pv[pt] and cv[ct])
            r = flat[pt * _N + ct] if ok else None
            if r is None:
                ok = False
                r = 0.0
            _upd(st, r, ok)
            if windows:
                for wn, a, b in _WINDOWS:
                    if _in_win(d, a, b):
                        _upd(win[wn], r, ok)
            if ok and isinstance(pick_times, tuple):
                key = (_REGS[pri], _REGS[cri])
                tr = trans.get(key)
                if tr is None:
                    tr = {"n": 0, "sum": 0.0, "nav": 1.0, "rets": []}
                    trans[key] = tr
                tr["n"] += 1
                tr["sum"] += r
                tr["nav"] = max(0.01, tr["nav"] * (1.0 + r))
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
            prod_m = _metrics(self._rt_prod)
            self._RtLog(
                f"CG_REGIME_TIME_PROD_FINAL,time={self._rt_prod_hhmm},"
                f"days={prod_m['days']},valid_days={prod_m['valid']},"
                f"invalid_days={prod_m['invalid']},nav={_f(prod_m['nav'])},"
                f"cagr={_f(prod_m['cagr'])},maxdd={_f(prod_m['maxdd'])},"
                f"worst5={_f(prod_m['worst5'],6)},vol={_f(prod_m['vol'])},"
                f"sharpe={_f(prod_m['sharpe'])}"
            )
            # fixed
            best_fixed_i = 0
            best_fixed_cagr = None
            fixed_ms = []
            for t in range(_N):
                m = _metrics(self._rt_fixed[t])
                fixed_ms.append(m)
                dn = (m["nav"] - prod_m["nav"]) if m["nav"] is not None else None
                dd = (m["maxdd"] - prod_m["maxdd"]) if m["maxdd"] is not None else None
                self._RtLog(
                    f"CG_REGIME_TIME_FIXED_FINAL,time={_hhmm(t)},"
                    f"days={m['days']},valid_days={m['valid']},"
                    f"nav={_f(m['nav'])},cagr={_f(m['cagr'])},maxdd={_f(m['maxdd'])},"
                    f"worst5={_f(m['worst5'],6)},vol={_f(m['vol'])},sharpe={_f(m['sharpe'])},"
                    f"delta_nav_vs_prod={_f(dn)},delta_dd_vs_prod={_f(dd)}"
                )
                if m["cagr"] is not None and (best_fixed_cagr is None or m["cagr"] > best_fixed_cagr):
                    best_fixed_cagr = m["cagr"]
                    best_fixed_i = t
            bf = fixed_ms[best_fixed_i]
            # cells
            for ri, rg in enumerate(_REGS):
                for t in range(_N):
                    st = self._rt_cell[ri][t]
                    m = _metrics(st)
                    rets = st.get("rets") or []
                    med = None
                    pos = None
                    if rets:
                        sr = sorted(rets)
                        mid = len(sr) // 2
                        med = sr[mid] if len(sr) % 2 else 0.5 * (sr[mid - 1] + sr[mid])
                        pos = sum(1 for x in rets if x > 0) / len(rets)
                    w5 = _w5_list(rets)
                    self._RtLog(
                        f"CG_REGIME_TIME_CELL_FINAL,regime={rg},time={_hhmm(t)},"
                        f"days={m['days']},valid_days={m['valid']},"
                        f"nav={_f(m['nav'])},mean={_f(m['mean'],6)},median={_f(med,6)},"
                        f"positive_rate={_f(pos)},maxdd={_f(m['maxdd'])},"
                        f"worst5={_f(w5,6)},vol={_f(m['vol'])},sharpe={_f(m['sharpe'])}"
                    )
            # screen combos
            pv = max(1, prod_m["valid"])
            pdd = prod_m["maxdd"] or 0.0
            pvol = prod_m["vol"] if prod_m["vol"] is not None else 1e9
            ranked = []
            for ron in range(_N):
                for neu in range(_N):
                    for roff in range(_N):
                        cid = ron * _NT + neu * _N + roff
                        m = _metrics(self._rt_combo[cid])
                        if m["valid"] < 0.95 * pv:
                            continue
                        if m["maxdd"] > pdd + 0.005:
                            continue
                        if m["vol"] is not None and m["vol"] > pvol + 0.01:
                            continue
                        sc = _score(m["cagr"], m["sharpe"], m["maxdd"])
                        ranked.append((sc, cid, ron, neu, roff, m))
            ranked.sort(key=lambda x: x[0], reverse=True)
            top_n = 30
            top = ranked[:top_n]
            # reduce if log pressure later
            for rank, row in enumerate(top, 1):
                sc, cid, ron, neu, roff, m = row
                dn = m["nav"] - prod_m["nav"]
                dd = m["maxdd"] - prod_m["maxdd"]
                if not self._RtLog(
                    f"CG_REGIME_TIME_COMBO_FINAL,rank={rank},"
                    f"ron={_hhmm(ron)},neutral={_hhmm(neu)},roff={_hhmm(roff)},"
                    f"days={m['days']},valid_days={m['valid']},"
                    f"nav={_f(m['nav'])},cagr={_f(m['cagr'])},maxdd={_f(m['maxdd'])},"
                    f"worst5={_f(m['worst5'],6)},vol={_f(m['vol'])},sharpe={_f(m['sharpe'])},"
                    f"score={_f(sc)},delta_nav_vs_prod={_f(dn)},delta_dd_vs_prod={_f(dd)}"
                ):
                    break
            # detailed replay top 10
            win_n = 10
            if self._rt_log_bytes > _LOG_BUDGET * 0.75:
                win_n = 5
                top_n = min(top_n, 20)
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
                    w5 = _w5_list(tr["rets"])
                    if not self._RtLog(
                        f"CG_REGIME_TIME_TRANS_FINAL,rank={rank},"
                        f"from_regime={fr},to_regime={to},days={tr['n']},"
                        f"mean={_f(mean,6)},nav={_f(tr['nav'])},worst5={_f(w5,6)}"
                    ):
                        break
            # prod window metrics for acceptance
            _, prod_win, _ = self._RtReplay("PROD", windows=True)
            prod_wm = {k: _metrics(v) for k, v in prod_win.items()}

            def _accept(ron, neu, roff, m, win):
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
                po = prod_wm["OOS"]
                if oos["sharpe"] is None or po["sharpe"] is None or oos["sharpe"] < 0.97 * po["sharpe"]:
                    return False
                for yk in ("Y2015", "Y2020", "Y2022"):
                    ym = _metrics(win[yk])
                    py = prod_wm[yk]
                    if ym["maxdd"] > (py["maxdd"] or 0):
                        return False
                cr = _metrics(win["CRISIS"])
                pc = prod_wm["CRISIS"]
                if cr["cagr"] is None or pc["cagr"] is None:
                    return False
                if cr["cagr"] < pc["cagr"] - 0.0020:  # 0.20 pp
                    return False
                if m["valid"] < 0.95 * pv:
                    return False
                # vs best fixed
                if bf["cagr"] is None or m["cagr"] < bf["cagr"]:
                    return False
                if bf["sharpe"] is None or m["sharpe"] is None or m["sharpe"] < bf["sharpe"]:
                    return False
                dd_ok = m["maxdd"] <= (bf["maxdd"] or 0) - 0.002
                cagr_ok = m["cagr"] >= bf["cagr"] + 0.0020
                if not (dd_ok or cagr_ok):
                    return False
                return True

            eligible = []
            # need windows for all top 30 for acceptance
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
                if _accept(ron, neu, roff, m, win):
                    ndist = len({ron, neu, roff})
                    eligible.append((m["cagr"], m["sharpe"], -m["maxdd"], -ndist, -roff, ron, neu, roff, m, win))
            eligible.sort(reverse=True)
            pick = None
            why = "no_eligible"
            if eligible:
                e = eligible[0]
                ron, neu, roff, m, win = e[5], e[6], e[7], e[8], e[9]
                # robustness neighbours ±30m, one regime at a time
                neighbors = []
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
                        neighbors.append((_REGS[reg_i], nt, mn, int(ok)))
                        self._RtLog(
                            f"CG_REGIME_TIME_NEIGHBOR_FINAL,"
                            f"base_pick={_hhmm(ron)}/{_hhmm(neu)}/{_hhmm(roff)},"
                            f"changed_regime={_REGS[reg_i]},candidate_time={_hhmm(nt)},"
                            f"nav={_f(mn['nav'])},cagr={_f(mn['cagr'])},"
                            f"maxdd={_f(mn['maxdd'])},sharpe={_f(mn['sharpe'])},"
                            f"pass={int(ok)}"
                        )
                n_pass = sum(1 for x in neighbors if x[3])
                if n_pass >= 4:
                    pick = (ron, neu, roff)
                    why = "accepted"
                else:
                    pick = None
                    why = "unstable_time_surface"
            if pick is None:
                self._RtLog(
                    f"CG_REGIME_TIME_SELECT_FINAL,pick=NONE,ron=NA,neutral=NA,roff=NA,"
                    f"best_fixed={_hhmm(best_fixed_i)},eligible={len(eligible)},"
                    f"why={why},trade=0"
                )
            else:
                ron, neu, roff = pick
                self._RtLog(
                    f"CG_REGIME_TIME_SELECT_FINAL,pick={_hhmm(ron)}/{_hhmm(neu)}/{_hhmm(roff)},"
                    f"ron={_hhmm(ron)},neutral={_hhmm(neu)},roff={_hhmm(roff)},"
                    f"best_fixed={_hhmm(best_fixed_i)},eligible={len(eligible)},"
                    f"why={why},trade=0"
                )
            self._RtLog(
                f"CG_REGIME_TIME_DATA_FINAL,candidate_snapshots={self._rt_cand_snaps},"
                f"missing_snapshots={self._rt_miss_snaps},"
                f"invalid_weight_days={self._rt_inv_w},"
                f"max_missing_weight={_f(self._rt_max_miss_w)},"
                f"invalid_regime_days={self._rt_inv_reg},"
                f"prod_time={self._rt_prod_hhmm},fees=0,slippage=0"
            )
        except Exception as exc:
            try:
                self.log(f"CG_REGIME_TIME_DATA_FINAL,error={type(exc).__name__},fees=0,slippage=0")
            except Exception:
                pass
