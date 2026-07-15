# region imports
from AlgorithmImports import *
# endregion
# cg_regime_time_shadow_s1.py
# CG-REGIME-TIME-SHADOW-S1/T2: 13-slot fixed + regime-specific (3x13=39) shadow screen.
# Diagnostic-only. Shadow portfolios only. Zero trading impact.
from datetime import date as _date
import math

_SH_SLOTS = (15, 45, 75, 105, 135, 165, 195, 225, 255, 285, 315, 345, 375)
_SH_N = 13
_SH_REGS = ("RISK_ON", "NEUTRAL", "RISK_OFF")
_SH_CASH = frozenset(("BIL", "SGOV", "USFR", "TFLO", "__CASH__"))
_SH_PARK = {"SGOV": "BIL", "USFR": "BIL", "TFLO": "BIL", "GLDM": "GLD"}
_SH_STALE = 5.0
_SH_WMIN = 1e-6
_SH_INIT = 1.0
_SH_BUDGET = 100000
_SH_HEAP = 200
_SH_WINDOWS = (
    ("TRAIN", _date(2012, 1, 1), _date(2018, 12, 31)),
    ("OOS", _date(2019, 1, 1), _date(2021, 12, 31)),
    ("CRISIS", _date(2022, 1, 1), _date(2025, 12, 31)),
    ("Y2015", _date(2015, 1, 1), _date(2015, 12, 31)),
    ("Y2020", _date(2020, 1, 1), _date(2020, 12, 31)),
    ("Y2022", _date(2022, 1, 1), _date(2022, 12, 31)),
    ("Y2023", _date(2023, 1, 1), _date(2023, 12, 31)),
    ("Y2024", _date(2024, 1, 1), _date(2024, 12, 31)),
    ("Y2025", _date(2025, 1, 1), _date(2025, 12, 31)),
    ("Y2026_YTD", _date(2026, 1, 1), None),
)
_SH_TRANS = (
    ("RISK_ON", "RISK_ON"), ("RISK_ON", "NEUTRAL"), ("RISK_ON", "RISK_OFF"),
    ("NEUTRAL", "NEUTRAL"), ("NEUTRAL", "RISK_OFF"),
    ("RISK_OFF", "NEUTRAL"), ("RISK_OFF", "RISK_OFF"),
)


def _sh_hhmm(m):
    tot = 9 * 60 + 30 + int(m)
    return f"{tot // 60:02d}:{tot % 60:02d}"


def _sh_tk(sym):
    try:
        return str(sym.Value)
    except Exception:
        try:
            return str(sym.value)
        except Exception:
            return str(sym)


def _sh_f(x, d=4):
    if x is None:
        return "NA"
    try:
        v = float(x)
        if math.isnan(v) or math.isinf(v):
            return "NA"
        return f"{v:.{d}f}"
    except Exception:
        return "NA"


def _sh_blank_port():
    return {
        "cash": _SH_INIT, "qty": {}, "last_px": {},
        "nav": _SH_INIT, "peak": _SH_INIT, "maxdd": 0.0,
        "trade_n": 0, "turn": 0.0,
        "invalid_exec": 0, "valid_exec": 0,
        "invalid_day": 0, "valid_day": 0,
        "daily": [],  # (date, nav)
        "irets": [],  # interval returns for transitions heap not needed full
        "prev_nav_exec": None,
        "trans": {},  # (from,to) -> list of interval rets
    }


class CgRegimeTimeShadowS1Mixin:
    """13 fixed-slot + optional 3x13 regime-specific shadow screen. trade=0."""

    def CgRegimeTimeShadowS1Initialize(self):
        ov = getattr(self, "_rrx_param_overrides", {}) or {}

        def _p(k, d=""):
            v = self.get_parameter(k)
            if v is None or str(v).strip() == "":
                v = ov.get(k, d)
            return v

        def _bool(k, d="0"):
            return str(_p(k, d) or d).strip().lower() in ("1", "true", "yes", "on")

        self.cg_rt_shadow = _bool("cg_rt_shadow", "0")
        self.cg_rt_shadow_log = _bool("cg_rt_sh_log", "0")
        self.cg_rt_sh_reg = _bool("cg_rt_sh_reg", "0")
        self.log(
            f"[INIT] CG_RT_SHADOW_S1,enable={int(self.cg_rt_shadow)},"
            f"reg={int(self.cg_rt_sh_reg)},slots=13,regimes=3,candidates=39,"
            f"signal_time=09:45,daily_mtm=1,partial_returns=0,trade=0"
        )
        if not self.cg_rt_shadow:
            return
        lp = list(getattr(self, "log_only_prefixes", None) or [])
        for pref in ("CG_RT_SHADOW_", "[INIT] CG_RT_SHADOW"):
            if pref not in lp:
                lp.append(pref)
        self.log_only_prefixes = lp
        self._sh_ports = [_sh_blank_port() for _ in range(_SH_N)]
        self._sh_reg_ports = None
        if self.cg_rt_sh_reg:
            self._sh_reg_ports = [
                [_sh_blank_port() for _ in range(_SH_N)] for _ in range(len(_SH_REGS))
            ]
        self._sh_pend = None
        self._sh_prev_regime = None
        self._sh_sym_map = {}
        self._sh_log_bytes = 0
        self._sh_emitted = False
        self._sh_subs = {}
        for m in _SH_SLOTS:
            if m == 15:
                continue
            self.schedule.on(
                self.date_rules.every_day(self.sym_spy),
                self.time_rules.after_market_open(self.sym_spy, m),
                lambda mm=m: self.CgRegimeTimeShadowS1Slot(mm),
            )
        self.schedule.on(
            self.date_rules.every_day(self.sym_spy),
            self.time_rules.before_market_close(self.sym_spy, 1),
            self.CgRegimeTimeShadowS1DailyMark,
        )

    def _ShLog(self, msg):
        if self._sh_log_bytes >= _SH_BUDGET:
            return False
        self.log(msg)
        self._sh_log_bytes += len(msg) + 1
        return True

    def _ShMap(self, tk):
        m = self._sh_sym_map
        if tk in m:
            return m[tk]
        try:
            for kv in self.securities:
                try:
                    sym = kv.Key if hasattr(kv, "Key") else kv
                    if _sh_tk(sym) == tk:
                        m[tk] = sym
                        return sym
                except Exception:
                    continue
        except Exception:
            pass
        m[tk] = None
        return None

    def _ShAvail(self, tk):
        sym = self._ShMap(tk)
        if sym is None:
            return False
        try:
            sec = self.securities[sym]
            hd = getattr(sec, "HasData", None)
            if hd is None:
                hd = getattr(sec, "has_data", False)
            px = float(sec.price)
            return bool(hd) and px > 0
        except Exception:
            return False

    def _ShPark(self, w):
        out = dict(w)
        for src, dst in _SH_PARK.items():
            if src not in out:
                continue
            if self._ShAvail(src):
                continue
            if not self._ShAvail(dst):
                continue
            wf = float(out.pop(src) or 0.0)
            out[dst] = float(out.get(dst, 0.0) or 0.0) + wf
            rec = self._sh_subs.get((src, dst))
            if rec is None:
                rec = {"n": 0, "wsum": 0.0}
                self._sh_subs[(src, dst)] = rec
            rec["n"] += 1
            rec["wsum"] += abs(wf)
            if dst not in self._sh_sym_map:
                for attr in ("sym_cash", "sym_gld", "sym_crash"):
                    s = getattr(self, attr, None)
                    if s is not None and _sh_tk(s) == dst:
                        self._sh_sym_map[dst] = s
                        break
        return out

    def _ShPx(self, tk):
        if tk in _SH_CASH:
            return 1.0  # cash/parking unit price for synthetic cash leg
        sym = self._ShMap(tk)
        if sym is None:
            return None
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
                    last = None
            et = None
            if last is not None:
                et = (
                    getattr(last, "end_time", None) or getattr(last, "EndTime", None)
                    or getattr(last, "time", None) or getattr(last, "Time", None)
                )
            if et is not None:
                age = (self.time - et).total_seconds() / 60.0
                if age < 0 or age > _SH_STALE:
                    return None
                return px
            hd = getattr(sec, "HasData", None)
            if hd is None:
                hd = getattr(sec, "has_data", False)
            if not hd:
                return None
            try:
                if not sec.exchange.hours.is_open(self.time, False):
                    return None
            except Exception:
                pass
            # minute subscription check
            try:
                reg = getattr(self, "_cg_sub_registry", {}) or {}
                rec = reg.get(tk)
                if rec is not None and not rec.get("tradable", False):
                    return None
            except Exception:
                pass
            return px
        except Exception:
            return None

    def _ShMark(self, port):
        nav = float(port["cash"])
        ok = True
        for tk, q in list(port["qty"].items()):
            if abs(q) < 1e-15:
                continue
            px = self._ShPx(tk)
            if px is None:
                px = port["last_px"].get(tk)
                if px is None or px <= 0:
                    ok = False
                    continue
            else:
                port["last_px"][tk] = px
            nav += float(q) * float(px)
        port["nav"] = max(1e-12, nav)
        if port["nav"] > port["peak"]:
            port["peak"] = port["nav"]
        dd = 1.0 - port["nav"] / max(port["peak"], 1e-12)
        if dd > port["maxdd"]:
            port["maxdd"] = dd
        return ok

    def _ShRebalance(self, port, targets):
        # mark first
        if not self._ShMark(port):
            port["invalid_exec"] += 1
            return False
        # validate all meaningful targets have px
        pxmap = {}
        for tk, wt in targets.items():
            try:
                wf = float(wt or 0.0)
            except Exception:
                continue
            if abs(wf) <= _SH_WMIN:
                continue
            if tk in _SH_CASH:
                pxmap[tk] = 1.0
                continue
            px = self._ShPx(tk)
            if px is None or px <= 0:
                port["invalid_exec"] += 1
                return False
            pxmap[tk] = px
            port["last_px"][tk] = px
        nav = port["nav"]
        # compute turnover vs old holdings
        old_val = {}
        for tk, q in port["qty"].items():
            px = pxmap.get(tk) or port["last_px"].get(tk) or self._ShPx(tk) or 0.0
            old_val[tk] = float(q) * float(px)
        new_qty = {}
        cash = nav
        traded = 0.0
        for tk, wt in targets.items():
            try:
                wf = float(wt or 0.0)
            except Exception:
                continue
            if abs(wf) <= _SH_WMIN:
                continue
            if tk in _SH_CASH:
                # leave in cash
                continue
            px = pxmap[tk]
            tgt_val = nav * wf
            q = tgt_val / px
            new_qty[tk] = q
            cash -= tgt_val
            traded += abs(tgt_val - float(old_val.get(tk, 0.0)))
        # close old notional not in new
        for tk, ov in old_val.items():
            if tk not in new_qty:
                traded += abs(ov)
        port["qty"] = new_qty
        port["cash"] = cash
        port["turn"] += traded / max(nav, 1e-12)
        port["trade_n"] += 1
        port["valid_exec"] += 1
        # interval return from previous exec mark
        prev = port["prev_nav_exec"]
        self._ShMark(port)
        if prev is not None and prev > 0:
            iret = port["nav"] / prev - 1.0
            port["irets"].append(iret)
            pend = self._sh_pend
            if pend is not None:
                fr = pend.get("prev_regime") or pend.get("regime")
                to = pend.get("regime")
                key = (str(fr), str(to))
                if key in _SH_TRANS or True:
                    lst = port["trans"].get(key)
                    if lst is None:
                        lst = []
                        port["trans"][key] = lst
                    lst.append(iret)
        port["prev_nav_exec"] = port["nav"]
        return True

    def _ShRegExecSlot(self, family, day_rg, cand_slot):
        # Vary only the matched 09:45 regime; other regimes stay at slot 15.
        return int(cand_slot) if str(day_rg) == str(family) else 15

    def _ShMarkPortDay(self, port, d):
        ok = self._ShMark(port)
        if ok:
            port["valid_day"] += 1
            port["daily"].append((d, port["nav"]))
        else:
            port["invalid_day"] += 1
            if port["daily"]:
                port["daily"].append((d, port["nav"]))

    def CgRegimeTimeShadowS1Capture(self, combined, regime):
        if not getattr(self, "cg_rt_shadow", False):
            return
        try:
            if getattr(self, "IsWarmingUp", False) or getattr(self, "is_warming_up", False):
                return
            if not isinstance(combined, dict):
                return
            d = self.time.date()
            rg = str(regime or "").strip().upper()
            if rg not in ("RISK_ON", "NEUTRAL", "RISK_OFF"):
                rg = "NEUTRAL"
            w = {}
            for k, v in combined.items():
                try:
                    wf = float(v or 0.0)
                except Exception:
                    continue
                if abs(wf) < 1e-15:
                    continue
                tk = _sh_tk(k)
                w[tk] = wf
                if tk not in self._sh_sym_map:
                    self._sh_sym_map[tk] = k
            w = self._ShPark(w)
            gross = sum(abs(float(x)) for x in w.values())
            prev_rg = self._sh_prev_regime
            self._sh_pend = {
                "date": d, "ts": self.time, "regime": rg,
                "prev_regime": prev_rg if prev_rg is not None else rg,
                "w": dict(w), "gross": gross, "count": len(w),
            }
            self._sh_prev_regime = rg
            # slot 15 executes with capture (same 09:45 bar)
            self._ShRebalance(self._sh_ports[0], w)
            # Regime-specific: execute now if effective slot is 15
            rports = getattr(self, "_sh_reg_ports", None)
            if rports is not None:
                for ri, family in enumerate(_SH_REGS):
                    for si, slot in enumerate(_SH_SLOTS):
                        if self._ShRegExecSlot(family, rg, slot) == 15:
                            self._ShRebalance(rports[ri][si], w)
        except Exception:
            pass

    def CgRegimeTimeShadowS1Slot(self, minutes):
        if not getattr(self, "cg_rt_shadow", False):
            return
        try:
            if getattr(self, "IsWarmingUp", False) or getattr(self, "is_warming_up", False):
                return
            pend = self._sh_pend
            if pend is None or pend["date"] != self.time.date():
                return
            try:
                si = _SH_SLOTS.index(int(minutes))
            except ValueError:
                return
            if si == 0:
                return  # handled at capture
            self._ShRebalance(self._sh_ports[si], pend["w"])
            rports = getattr(self, "_sh_reg_ports", None)
            if rports is not None:
                m = int(minutes)
                rg = pend["regime"]
                w = pend["w"]
                for ri, family in enumerate(_SH_REGS):
                    for sj, slot in enumerate(_SH_SLOTS):
                        if self._ShRegExecSlot(family, rg, slot) == m:
                            self._ShRebalance(rports[ri][sj], w)
        except Exception:
            pass

    def CgRegimeTimeShadowS1DailyMark(self):
        if not getattr(self, "cg_rt_shadow", False):
            return
        try:
            if getattr(self, "IsWarmingUp", False) or getattr(self, "is_warming_up", False):
                return
            d = self.time.date()
            for port in self._sh_ports:
                self._ShMarkPortDay(port, d)
            rports = getattr(self, "_sh_reg_ports", None)
            if rports is not None:
                for row in rports:
                    for port in row:
                        self._ShMarkPortDay(port, d)
        except Exception:
            pass

    def _ShMetricsFromDaily(self, daily):
        if not daily or len(daily) < 2:
            return None
        first_d, first_n = daily[0]
        last_d, last_n = daily[-1]
        try:
            ey = (last_d - first_d).total_seconds() / (365.2425 * 86400.0)
        except Exception:
            ey = max(1, (last_d - first_d).days) / 365.2425
        nav = float(last_n) / max(float(first_n), 1e-12)
        cagr = None
        if ey and ey > 0 and nav > 0:
            cagr = nav ** (1.0 / ey) - 1.0
        rets = []
        peak = float(daily[0][1])
        maxdd = 0.0
        for i in range(1, len(daily)):
            n0 = float(daily[i - 1][1])
            n1 = float(daily[i][1])
            if n0 > 0:
                rets.append(n1 / n0 - 1.0)
            if n1 > peak:
                peak = n1
            dd = 1.0 - n1 / max(peak, 1e-12)
            if dd > maxdd:
                maxdd = dd
        vol = sharpe = mean = None
        worst = worst5 = pos = None
        if rets:
            mean = sum(rets) / len(rets)
            var = max(0.0, sum((r - mean) ** 2 for r in rets) / len(rets))
            vol = math.sqrt(var) * math.sqrt(252.0)
            if vol > 1e-12:
                sharpe = mean * 252.0 / vol
            worst = min(rets)
            arr = sorted(rets)
            k = max(1, int(0.05 * len(arr) + 0.999))
            worst5 = sum(arr[:k]) / k
            pos = sum(1 for r in rets if r > 0) / len(rets)
        return {
            "nav": nav, "cagr": cagr, "maxdd": maxdd, "vol": vol, "sharpe": sharpe,
            "worst": worst, "worst5": worst5, "positive": pos, "elapsed": ey,
            "days": len(daily),
        }

    def _ShWindowMetrics(self, daily, a, b):
        sub = []
        for d, n in daily:
            if d < a:
                continue
            if b is not None and d > b:
                continue
            sub.append((d, n))
        return self._ShMetricsFromDaily(sub)

    def _ShScore(self, m):
        c = 0.0 if m["cagr"] is None else m["cagr"]
        s = 0.0 if m["sharpe"] is None else m["sharpe"]
        dd = 0.0 if m["maxdd"] is None else m["maxdd"]
        w5 = 0.0 if m["worst5"] is None else m["worst5"]
        return 2.0 * c + 0.20 * s - 1.50 * dd + 0.25 * w5

    def _ShPortMetrics(self, port):
        m = self._ShMetricsFromDaily(port["daily"]) or {
            "nav": port["nav"], "cagr": None, "maxdd": port["maxdd"],
            "vol": None, "sharpe": None, "worst": None, "worst5": None,
            "positive": None, "elapsed": None, "days": 0,
        }
        vd = port["valid_day"]
        idd = port["invalid_day"]
        ve = port["valid_exec"]
        ie = port["invalid_exec"]
        tot_e = max(1, ve + ie)
        cov = ve / tot_e
        m["coverage"] = cov
        m["valid_exec"] = ve
        m["invalid_exec"] = ie
        m["valid_day"] = vd
        m["invalid_day"] = idd
        m["turnover"] = port["turn"]
        m["rebalances"] = port["trade_n"]
        m["score"] = self._ShScore(m)
        return m

    def _ShEligible(self, m, ctrl):
        if m["coverage"] is None or m["coverage"] < 0.95:
            return False
        idr = m["invalid_day"] / max(1, m["valid_day"] + m["invalid_day"])
        if idr > 0.02:
            return False
        if m["cagr"] is None or ctrl["cagr"] is None:
            return False
        if m["cagr"] < ctrl["cagr"] - 0.0050:
            return False
        if m["maxdd"] is None or ctrl["maxdd"] is None:
            return False
        if m["maxdd"] > ctrl["maxdd"] + 0.0100:
            return False
        if m["sharpe"] is None or ctrl["sharpe"] is None:
            return False
        if m["sharpe"] < ctrl["sharpe"] - 0.05:
            return False
        return True

    def _ShBuildShortlist(self, metrics, n_max=5):
        short = []
        seen = set()

        def _add(si, reason):
            if si in seen:
                return
            short.append((si, reason))
            seen.add(si)

        _add(0, "CONTROL")
        elig = [i for i in range(len(metrics)) if self._ShEligible(metrics[i], metrics[0])]
        if not elig:
            elig = list(range(len(metrics)))
        _add(max(elig, key=lambda i: metrics[i]["cagr"] if metrics[i]["cagr"] is not None else -1e9), "CAGR")
        _add(max(elig, key=lambda i: metrics[i]["sharpe"] if metrics[i]["sharpe"] is not None else -1e9), "SHARPE")
        _add(min(elig, key=lambda i: metrics[i]["maxdd"] if metrics[i]["maxdd"] is not None else 1e9), "MAXDD")
        _add(max(elig, key=lambda i: metrics[i]["score"]), "BALANCED")
        return short[:n_max]

    def _ShEmitRegFinal(self):
        rports = getattr(self, "_sh_reg_ports", None)
        if rports is None:
            return
        picks = {}
        for ri, family in enumerate(_SH_REGS):
            ports = rports[ri]
            metrics = [self._ShPortMetrics(p) for p in ports]
            for si, m in enumerate(metrics):
                self._ShLog(
                    f"CG_RT_SHADOW_REG_FINAL,regime={family},"
                    f"slot={_SH_SLOTS[si]},time={_sh_hhmm(_SH_SLOTS[si])},"
                    f"nav={_sh_f(m['nav'])},cagr={_sh_f(m['cagr'])},maxdd={_sh_f(m['maxdd'])},"
                    f"vol={_sh_f(m['vol'])},sharpe={_sh_f(m['sharpe'])},"
                    f"worst={_sh_f(m['worst'],6)},worst5={_sh_f(m['worst5'],6)},"
                    f"positive={_sh_f(m['positive'])},turnover={_sh_f(m['turnover'])},"
                    f"rebalances={m['rebalances']},valid_exec={m['valid_exec']},"
                    f"invalid_exec={m['invalid_exec']},valid_days={m['valid_day']},"
                    f"invalid_days={m['invalid_day']},coverage={_sh_f(m['coverage'])}"
                )
            # Keep at most 2 unique non-control finalists + control (matches T2 Stage1 cap).
            short = self._ShBuildShortlist(metrics, n_max=5)
            finals = []
            for rank, (si, reason) in enumerate(short, 1):
                m = metrics[si]
                self._ShLog(
                    f"CG_RT_SHADOW_REG_SHORTLIST_FINAL,regime={family},rank={rank},"
                    f"slot={_SH_SLOTS[si]},time={_sh_hhmm(_SH_SLOTS[si])},"
                    f"reason={reason},score={_sh_f(m['score'])},requires_real_backtest=1"
                )
                if reason != "CONTROL" and len(finals) < 2 and si not in finals:
                    finals.append(si)
            # If only control survived, still allow best CAGR as second look
            if len(finals) < 2:
                for si, reason in short:
                    if si == 0 or si in finals:
                        continue
                    finals.append(si)
                    if len(finals) >= 2:
                        break
            picks[family] = [_SH_SLOTS[si] for si in finals]
            for si in finals:
                port = ports[si]
                for wn, a, b in _SH_WINDOWS:
                    wm = self._ShWindowMetrics(port["daily"], a, b)
                    if wm is None:
                        continue
                    self._ShLog(
                        f"CG_RT_SHADOW_REG_WINDOW_FINAL,regime={family},"
                        f"slot={_SH_SLOTS[si]},window={wn},"
                        f"nav={_sh_f(wm['nav'])},cagr={_sh_f(wm['cagr'])},"
                        f"maxdd={_sh_f(wm['maxdd'])},vol={_sh_f(wm['vol'])},"
                        f"sharpe={_sh_f(wm['sharpe'])},worst5={_sh_f(wm['worst5'],6)}"
                    )
        ron = ",".join(str(x) for x in picks.get("RISK_ON", [])) or "NONE"
        neu = ",".join(str(x) for x in picks.get("NEUTRAL", [])) or "NONE"
        roff = ",".join(str(x) for x in picks.get("RISK_OFF", [])) or "NONE"
        self._ShLog(
            f"CG_RT_SHADOW_REG_SELECT_FINAL,risk_on={ron},neutral={neu},risk_off={roff},"
            f"candidates=39,trade=0,next=REAL_BACKTEST"
        )

    def CgRegimeTimeShadowS1EmitFinal(self):
        if not getattr(self, "cg_rt_shadow", False):
            return
        if getattr(self, "_sh_emitted", False):
            return
        self._sh_emitted = True
        try:
            # final mark
            try:
                self.CgRegimeTimeShadowS1DailyMark()
            except Exception:
                pass
            metrics = []
            for si, port in enumerate(self._sh_ports):
                m = self._ShPortMetrics(port)
                metrics.append(m)
                self._ShLog(
                    f"CG_RT_SHADOW_FIXED_FINAL,slot={_SH_SLOTS[si]},time={_sh_hhmm(_SH_SLOTS[si])},"
                    f"nav={_sh_f(m['nav'])},cagr={_sh_f(m['cagr'])},maxdd={_sh_f(m['maxdd'])},"
                    f"vol={_sh_f(m['vol'])},sharpe={_sh_f(m['sharpe'])},"
                    f"worst={_sh_f(m['worst'],6)},worst5={_sh_f(m['worst5'],6)},"
                    f"positive={_sh_f(m['positive'])},turnover={_sh_f(m['turnover'])},"
                    f"rebalances={m['rebalances']},valid_exec={m['valid_exec']},"
                    f"invalid_exec={m['invalid_exec']},valid_days={m['valid_day']},"
                    f"invalid_days={m['invalid_day']},coverage={_sh_f(m['coverage'])}"
                )
            short = self._ShBuildShortlist(metrics, n_max=5)
            for rank, (si, reason) in enumerate(short, 1):
                m = metrics[si]
                self._ShLog(
                    f"CG_RT_SHADOW_SHORTLIST_FINAL,rank={rank},"
                    f"slot={_SH_SLOTS[si]},time={_sh_hhmm(_SH_SLOTS[si])},"
                    f"reason={reason},score={_sh_f(m['score'])},requires_real_backtest=1"
                )
            for si, reason in short:
                port = self._sh_ports[si]
                for wn, a, b in _SH_WINDOWS:
                    wm = self._ShWindowMetrics(port["daily"], a, b)
                    if wm is None:
                        continue
                    self._ShLog(
                        f"CG_RT_SHADOW_WINDOW_FINAL,slot={_SH_SLOTS[si]},window={wn},"
                        f"nav={_sh_f(wm['nav'])},cagr={_sh_f(wm['cagr'])},"
                        f"maxdd={_sh_f(wm['maxdd'])},vol={_sh_f(wm['vol'])},"
                        f"sharpe={_sh_f(wm['sharpe'])},worst5={_sh_f(wm['worst5'],6)}"
                    )
                if getattr(self, "cg_rt_shadow_log", False):
                    for (fr, to) in _SH_TRANS:
                        rets = port["trans"].get((fr, to)) or []
                        if len(rets) < 5:
                            continue
                        sr = sorted(rets)
                        mid = len(sr) // 2
                        med = sr[mid] if len(sr) % 2 else 0.5 * (sr[mid - 1] + sr[mid])
                        mean = sum(rets) / len(rets)
                        pos = sum(1 for x in rets if x > 0) / len(rets)
                        self._ShLog(
                            f"CG_RT_SHADOW_TRANS_FINAL,slot={_SH_SLOTS[si]},"
                            f"from={fr},to={to},events={len(rets)},"
                            f"mean={_sh_f(mean,6)},median={_sh_f(med,6)},"
                            f"positive={_sh_f(pos)},worst={_sh_f(min(rets),6)}"
                        )
            slots = ",".join(str(_SH_SLOTS[si]) for si, _ in short)
            self._ShLog(
                f"CG_RT_SHADOW_SELECT_FINAL,control=15,shortlist={slots},"
                f"candidates=13,trade=0,next=REAL_BACKTEST"
            )
            if getattr(self, "cg_rt_sh_reg", False):
                self._ShEmitRegFinal()
        except Exception as exc:
            try:
                self.log(f"CG_RT_SHADOW_SELECT_FINAL,error={type(exc).__name__},trade=0")
            except Exception:
                pass
