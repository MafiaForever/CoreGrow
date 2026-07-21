# cg_defensive_trade.py
# CG-DEF-TRADE-T1: W2 WATCH equity scale + E2 transition equity cap.
# Trading: W2 ON, E2 OFF. LEAN v17921 compatible.

from AlgorithmImports import *
from cg_regime_rebal_time_trade import CgRegimeRebalTimeTradeMixin


_DFT_DEF = frozenset(("TIP", "BND", "GLD", "GLDM", "BIL", "SGOV", "USFR", "SH"))


def _dft_tk(s):
    try:
        return str(s.Value)
    except Exception:
        try:
            return str(s.value)
        except Exception:
            return str(s)


class CgDefensiveTradeMixin(CgRegimeRebalTimeTradeMixin):
    """W2 / E2 production overlays. No order calls."""

    def CgDefensiveTradeInit(self) -> None:
        ov = getattr(self, "_rrx_param_overrides", {}) or {}

        def _p(k, d=""):
            v = self.get_parameter(k)
            if v is None or str(v).strip() == "":
                v = ov.get(k, d)
            return v

        def _bool(k, d="0"):
            return str(_p(k, d) or d).strip().lower() in ("1", "true", "yes", "on")

        self.cg_watch_w2_trade_enable = _bool("cg_watch_w2_trade_enable", "1")
        self.cg_transition_e2_trade_enable = _bool("cg_transition_e2_trade_enable", "0")
        self._cg_e2_active = False
        self._cg_e2_start_date = None
        self._cg_e2_days = 0
        self._cg_e2_last_day = None
        self._cg_w2_last_active = None
        self._cg_w2_last_eq = None
        self._cg_dft_last_day = None
        self._cg_spy20_buf = []
        lp = list(getattr(self, "log_only_prefixes", None) or [])
        for pref in ("CG_W2_TRADE", "CG_E2_STATE", "[INIT] CG_DEF_TRADE"):
            if pref not in lp:
                lp.append(pref)
        self.log_only_prefixes = lp
        self.log(
            f"[INIT] CG_DEF_TRADE_T1,"
            f"w2={int(self.cg_watch_w2_trade_enable)},"
            f"e2={int(self.cg_transition_e2_trade_enable)},"
            f"w2_scale=0.80,e2_equity_cap=1.00,e2_max_days=20,trade=1"
        )
        try:
            self.CgRegimeRebalTimeTradeInitialize()
        except Exception as exc:
            raise Exception(f"CG_REGIME_TIME_TRADE_T1 init failed: {exc}")
        try:
            self.CgShadowReplayInit()
        except Exception:
            pass
        try:
            self.CgMaisrInit()
        except Exception:
            pass

    def _DftCashSym(self):
        return getattr(self, "sym_cash", None)

    def _DftIsEquity(self, sym, eq_set):
        tk = _dft_tk(sym)
        if tk in _DFT_DEF:
            return False
        if tk == "SPY" or tk in eq_set:
            return True
        if sym in eq_set:
            return True
        try:
            if getattr(self, "sym_spy", None) is not None and sym == self.sym_spy:
                return True
        except Exception:
            pass
        for s in getattr(self, "panic_tactical_universe", []) or []:
            if sym == s or tk == _dft_tk(s):
                return True
        return False

    def _DftEqSet(self):
        eq = {"SPY"}
        for s in getattr(self, "panic_tactical_universe", []) or []:
            eq.add(_dft_tk(s))
            eq.add(s)
        return eq

    def _DftSpyPx(self):
        try:
            return float(self.securities[self.sym_spy].price)
        except Exception:
            return None

    def _DftInd(self, attr):
        try:
            ind = getattr(self, attr, None)
            if ind is None or not ind.IsReady:
                return None
            return float(ind.Current.Value)
        except Exception:
            return None

    def _DftSpy20(self, today):
        # one append per trading date; return T close vs T-20 close
        if self._cg_dft_last_day != today:
            px = self._DftSpyPx()
            if px is not None and px > 0:
                self._cg_spy20_buf.append(px)
                if len(self._cg_spy20_buf) > 25:
                    self._cg_spy20_buf = self._cg_spy20_buf[-25:]
            self._cg_dft_last_day = today
        buf = self._cg_spy20_buf
        if len(buf) <= 20 or buf[-21] <= 0:
            return None
        return buf[-1] / buf[-21] - 1.0

    def _DftPark(self, out, freed):
        if freed <= 1e-12:
            return out
        cs = self._DftCashSym()
        if cs is None:
            return out
        out[cs] = float(out.get(cs, 0.0) or 0.0) + freed
        return out

    def _DftEqGross(self, w, eq_set):
        g = 0.0
        for s, wt in w.items():
            try:
                wf = float(wt or 0.0)
            except Exception:
                continue
            if wf > 0 and self._DftIsEquity(s, eq_set):
                g += wf
        return g

    def _DftScaleEquity(self, w, eq_set, scale):
        out = dict(w)
        freed = 0.0
        for s in list(out.keys()):
            try:
                wf = float(out[s] or 0.0)
            except Exception:
                continue
            if wf > 0 and self._DftIsEquity(s, eq_set):
                nw = wf * scale
                freed += wf - nw
                out[s] = nw
        return self._DftPark(out, freed), freed

    def _DftW2Active(self, spy_px, ema75, spy20):
        if not self.cg_watch_w2_trade_enable:
            return False
        ps = str(getattr(self, "_panic_state", "NORMAL") or "NORMAL")
        ids = str(getattr(self, "_ids_state", "NORMAL") or "NORMAL")
        if ps != "NORMAL":
            return False
        if ids != "WATCH":
            return False
        if spy_px is None or ema75 is None or spy20 is None:
            return False
        if not (spy_px < ema75 and spy20 < 0):
            return False
        return True

    def _DftE2Update(self, today, spy_px, ema75, ema9, ema120, dd):
        if not self.cg_transition_e2_trade_enable:
            if self._cg_e2_active:
                self._cg_e2_active = False
                self._cg_e2_start_date = None
                self._cg_e2_days = 0
                self.log(f"CG_E2_STATE,date={today},event=EXIT,reason=DISABLED,days=0")
            return False
        if getattr(self, "IsWarmingUp", False) or getattr(self, "is_warming_up", False):
            return False
        if any(x is None for x in (spy_px, ema75, ema9, ema120)):
            return False

        # exit checks while active (once per date)
        if self._cg_e2_active:
            if self._cg_e2_last_day != today:
                self._cg_e2_days += 1
                self._cg_e2_last_day = today
            reason = None
            if ema9 >= ema120:
                reason = "TREND_RECOVERED"
            elif dd < 0.03:
                reason = "DD_RECOVERED"
            elif self._cg_e2_days >= 20:
                reason = "MAX_DAYS"
            if reason:
                days = int(self._cg_e2_days)
                self._cg_e2_active = False
                self._cg_e2_start_date = None
                self._cg_e2_days = 0
                self._cg_e2_last_day = today
                self.log(f"CG_E2_STATE,date={today},event=EXIT,reason={reason},days={days}")
                return False
            return True

        # start
        regime = str(getattr(self, "current_regime", None) or "")
        prev = str(getattr(self, "prev_regime", None) or "")
        start = (
            regime in ("NEUTRAL", "RISK_OFF")
            and prev == "RISK_ON"
            and spy_px < ema75
            and ema9 < ema120
            and dd >= 0.05
        )
        if start:
            self._cg_e2_active = True
            self._cg_e2_start_date = today
            self._cg_e2_days = 1
            self._cg_e2_last_day = today
            return True
        return False

    def CgDefensiveTradeApply(self, combined) -> dict:
        """Return adjusted copy. Never mutates input. No orders."""
        if not isinstance(combined, dict):
            return combined
        w2_on = bool(getattr(self, "cg_watch_w2_trade_enable", False))
        e2_on = bool(getattr(self, "cg_transition_e2_trade_enable", False))
        if not w2_on and not e2_on:
            return combined

        today = self.time.date()
        out = dict(combined)
        eq_set = self._DftEqSet()
        spy_px = self._DftSpyPx()
        ema75 = self._DftInd("spy_ema_75")
        ema9 = self._DftInd("spy_ema_9")
        ema120 = self._DftInd("spy_ema_120")
        spy20 = self._DftSpy20(today)
        try:
            dd = float(self.CurrentDrawdown())
        except Exception:
            dd = 0.0

        # W2 first
        if w2_on:
            active = self._DftW2Active(spy_px, ema75, spy20)
            eq_b = self._DftEqGross(out, eq_set)
            cash_add = 0.0
            if active:
                out, cash_add = self._DftScaleEquity(out, eq_set, 0.80)
            eq_a = self._DftEqGross(out, eq_set)
            changed = (
                active != getattr(self, "_cg_w2_last_active", None)
                or (active and abs(eq_a - float(getattr(self, "_cg_w2_last_eq", eq_a) or eq_a)) > 1e-6)
            )
            if changed:
                ps = str(getattr(self, "_panic_state", "NORMAL") or "NORMAL")
                ids = str(getattr(self, "_ids_state", "NORMAL") or "NORMAL")
                self.log(
                    f"CG_W2_TRADE,date={today},active={int(active)},"
                    f"equity_before={eq_b:.4f},equity_after={eq_a:.4f},"
                    f"cash_add={cash_add:.4f},ids={ids},panic={ps}"
                )
            self._cg_w2_last_active = active
            self._cg_w2_last_eq = eq_a
            # D0.6B P0: passive equity-gross observe only (no target mutation).
            try:
                if bool(getattr(self, "cg_damage_duration_d06b_p0_enable", False)):
                    obs = getattr(self, "_D06bP0ObserveProtection", None)
                    if callable(obs):
                        obs(getattr(self, "time", None), eq_b, eq_a, active)
            except Exception:
                pass

        # E2 second
        if e2_on:
            was = bool(self._cg_e2_active)
            active = self._DftE2Update(today, spy_px, ema75, ema9, ema120, dd)
            if active:
                eq_b = self._DftEqGross(out, eq_set)
                if eq_b > 1.00 + 1e-12:
                    out, _ = self._DftScaleEquity(out, eq_set, 1.00 / eq_b)
                eq_a = self._DftEqGross(out, eq_set)
                if not was:
                    regime = str(getattr(self, "current_regime", None) or "")
                    self.log(
                        f"CG_E2_STATE,date={today},event=ENTER,regime={regime},"
                        f"dd={dd:.4f},equity_before={eq_b:.4f},equity_after={eq_a:.4f}"
                    )

        return out

    def CgDefensiveTradePersist(self, state: dict) -> None:
        state["cg_e2_active"] = bool(getattr(self, "_cg_e2_active", False))
        sd = getattr(self, "_cg_e2_start_date", None)
        state["cg_e2_start_date"] = sd.isoformat() if sd is not None else None
        state["cg_e2_days"] = int(getattr(self, "_cg_e2_days", 0) or 0)
        try:
            self.CgRegimeRebalTimeTradePersist(state)
        except Exception:
            pass

    def CgDefensiveTradeRestore(self, state: dict) -> None:
        if not state:
            return
        self._cg_e2_active = bool(state.get("cg_e2_active", False))
        raw = state.get("cg_e2_start_date")
        if raw:
            try:
                from datetime import date as _d
                self._cg_e2_start_date = _d.fromisoformat(str(raw)[:10])
            except Exception:
                self._cg_e2_start_date = None
        else:
            self._cg_e2_start_date = None
        self._cg_e2_days = int(state.get("cg_e2_days") or 0)
        try:
            self.CgRegimeRebalTimeTradeRestore(state)
        except Exception:
            pass
