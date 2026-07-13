# dyn_alloc_diag.py
# DYN_ALLOC_D0: diagnostic-only CG/RR meta-allocation shadow layer.
# Zero trading impact. No changes to target weights.
# LEAN v17719 compatible.

import numpy as np
from datetime import datetime
from typing import TYPE_CHECKING
from AlgorithmImports import Resolution, MovingAverageType, Field
from rr_sleeve import RideRocketSleeve, RR_TRADE_ENABLE as _RR_TRADE_ENABLE  # [RR_SLEEVE]


class DynamicAllocationDiagMixin:
    if TYPE_CHECKING:
        time: datetime
        live_mode: bool
        portfolio: object
        securities: object
        sym_spy: object
        sym_cash: object
        sym_crash: object
        panic_tactical_universe: list
        current_regime: str
        dd_soft_start: float
        emergency_stop_triggered: bool
        short_shock_flag: bool
        _panic_state: str
        _panic_score: float
        _ids_active: bool
        _ids_state: str
        _active_tactical_symbol: object
        last_panic_winner: object
        def add_equity(self, ticker, resolution): ...
        def sma(self, symbol, period, resolution, selector=None): ...
        def rsi(self, symbol, period, moving_average_type, resolution): ...
        def atr(self, symbol, period, moving_average_type, resolution): ...
        def history(self, symbol, periods, resolution): ...
        def get_parameter(self, name: str): ...
        def log(self, msg: str): ...
        def _LogAllowedAt(self, dt=None) -> bool: ...
        def CurrentDrawdown(self) -> float: ...

    def DynAllocInitialize(self) -> None:
        """Initialize RR diagnostic symbols and params. Diagnostic only."""
        self.dyn_alloc_d0_enable = str(self.get_parameter("dyn_alloc_d0_enable") or "1").strip().lower() in ("1", "true", "yes", "on")
        self.dyn_alloc_d0_log_all = str(self.get_parameter("dyn_alloc_d0_log_all") or "0").strip().lower() in ("1", "true", "yes", "on")
        self.dyn_alloc_base_rr = float(self.get_parameter("dyn_alloc_base_rr") or 0.34)
        self.dyn_alloc_base_cg = 1 - self.dyn_alloc_base_rr
        _dd_soft = self.dd_soft_start if hasattr(self, "dd_soft_start") else 0.11
        self.dyn_alloc_dd_freeze = float(self.get_parameter("dyn_alloc_dd_freeze") or _dd_soft)
        self.dyn_alloc_min_rr_score = float(self.get_parameter("dyn_alloc_min_rr_score") or 0.10)
        self.dyn_alloc_strong_rr_score = float(self.get_parameter("dyn_alloc_strong_rr_score") or 0.35)
        # Normalized relative-strength divisors (mirrors original RR rocket_score style).
        # Smaller divisor = more sensitive (less outperformance needed to reach rs=1.0).
        # Default 0.30 matches original RideRocket rs_qqq / rs_smh normalization.
        self.dyn_alloc_rs_norm_spy = float(self.get_parameter("dyn_alloc_rs_norm_spy") or 0.30)
        self.dyn_alloc_rs_norm_smh = float(self.get_parameter("dyn_alloc_rs_norm_smh") or 0.30)
        self.dyn_alloc_rs_norm_qqq = float(self.get_parameter("dyn_alloc_rs_norm_qqq") or 0.30)
        self.dyn_alloc_rs_norm_tac = float(self.get_parameter("dyn_alloc_rs_norm_tac") or 0.30)
        # Gate threshold for beats_core_refs / beats_tactical (0.0 = must be strictly positive)
        self.dyn_alloc_rs_min_score = float(self.get_parameter("dyn_alloc_rs_min_score") or 0.0)
        self.dyn_alloc_overheat_rsi = float(self.get_parameter("dyn_alloc_overheat_rsi") or 87.0)
        self.dyn_alloc_profit_rsi = float(self.get_parameter("dyn_alloc_profit_rsi") or 76.0)
        self.dyn_alloc_profit_ret20 = float(self.get_parameter("dyn_alloc_profit_ret20") or 0.25)
        self.dyn_alloc_idle_cash_min = float(self.get_parameter("dyn_alloc_idle_cash_min") or 0.50)
        self.dyn_alloc_rr_cash_ticker = str(self.get_parameter("rr_cash_ticker") or "USFR").upper()

        self.rr_candidates = []
        for ticker in ["MU", "NVDA", "AVGO"]:
            self.rr_candidates.append(self._CgRegisterEquity(ticker, tradable=True).Symbol)
        self.rr_smh = self._CgRegisterEquity("SMH").Symbol
        self.rr_qqq = self._CgRegisterEquity("QQQ").Symbol
        self.rr_cash = self._CgRegisterEquity(self.dyn_alloc_rr_cash_ticker, tradable=True).Symbol

        self.rr_sma50 = {s: self.sma(s, 50, Resolution.DAILY) for s in self.rr_candidates}
        self.rr_sma200 = {s: self.sma(s, 200, Resolution.DAILY) for s in self.rr_candidates}
        self.rr_rsi14 = {s: self.rsi(s, 14, MovingAverageType.WILDERS, Resolution.DAILY) for s in self.rr_candidates}
        self.rr_atr20 = {s: self.atr(s, 20, MovingAverageType.WILDERS, Resolution.DAILY) for s in self.rr_candidates}
        self.rr_vol20 = {s: self.sma(s, 20, Resolution.DAILY, Field.VOLUME) for s in self.rr_candidates}
        self.rr_smh_sma50 = self.sma(self.rr_smh, 50, Resolution.DAILY)
        self.rr_smh_sma100 = self.sma(self.rr_smh, 100, Resolution.DAILY)
        self.rr_qqq_sma100 = self.sma(self.rr_qqq, 100, Resolution.DAILY)
        self.rr_active_symbols = list(self.rr_candidates) + [self.rr_smh, self.rr_qqq, self.rr_cash]
        self._dyn_alloc_last = {}
        # [DYN_ALLOC_D1] Shadow NAV params
        self.dyn_alloc_d1_enable = str(self.get_parameter("dyn_alloc_d1_enable") or "1").strip().lower() in ("1", "true", "yes", "on")
        self.dyn_alloc_c2_rr_mult = float(self.get_parameter("dyn_alloc_c2_rr_mult") or 1.50)
        # [DYN_ALLOC_D1B] quality-filtered C2 shadow NAV + prev-day allocation
        self.dyn_alloc_d1b_enable = str(self.get_parameter("dyn_alloc_d1b_enable") or "1").strip().lower() in ("1", "true", "yes", "on")
        self.dyn_alloc_c2q_min_abs_ret20 = float(self.get_parameter("dyn_alloc_c2q_min_abs_ret20") or 0.08)
        self.dyn_alloc_c2q_min_smh20     = float(self.get_parameter("dyn_alloc_c2q_min_smh20")     or 0.00)
        self.dyn_alloc_c2q_min_qqq20     = float(self.get_parameter("dyn_alloc_c2q_min_qqq20")     or 0.00)
        self.dyn_alloc_c2q_min_rel_edge  = float(self.get_parameter("dyn_alloc_c2q_min_rel_edge")  or 0.02)
        self.dyn_alloc_c2q_max_rsi       = float(self.get_parameter("dyn_alloc_c2q_max_rsi")       or 74.0)
        self.dyn_alloc_c1_require_cg_ok  = str(self.get_parameter("dyn_alloc_c1_require_cg_ok") or "1").strip().lower() in ("1", "true", "yes", "on")
        # [DYN_ALLOC_D1] Shadow NAV state (compounding from first valid day)
        self._d1_nav_base  = 1.0
        self._d1_nav_c1    = 1.0
        self._d1_nav_c2    = 1.0
        self._d1_nav_c2q   = 1.0   # [D1B] quality-filtered C2
        self._d1_port_prev = None
        self._d1_alloc_prev = None
        self._d1_prev_weights = None  # [D1B] previous-day allocation weights
        # [DYN_ALLOC_D2] RR-native shadow state machine params (frozen RR baseline)
        self.dyn_alloc_d2_enable      = str(self.get_parameter("dyn_alloc_d2_enable")      or "1").strip().lower() in ("1", "true", "yes", "on")
        self.dyn_rr_base_exp          = float(self.get_parameter("dyn_rr_base_exp")         or 0.40)
        self.dyn_rr_add_exp           = float(self.get_parameter("dyn_rr_add_exp")          or 0.20)
        self.dyn_rr_max_exp           = float(self.get_parameter("dyn_rr_max_exp")          or 0.60)
        self.dyn_rr_rsi_entry_max     = float(self.get_parameter("dyn_rr_rsi_entry_max")    or 70.0)
        self.dyn_rr_rsi_add_max       = float(self.get_parameter("dyn_rr_rsi_add_max")      or 80.0)
        self.dyn_rr_rsi_trim          = float(self.get_parameter("dyn_rr_rsi_trim")         or 87.0)
        self.dyn_rr_chandelier        = float(self.get_parameter("dyn_rr_chandelier")       or 2.9)
        self.dyn_rr_pl_thresh         = float(self.get_parameter("dyn_rr_pl_thresh")        or 0.45)
        self.dyn_rr_pl_rsi_min        = float(self.get_parameter("dyn_rr_pl_rsi_min")       or 76.0)
        self.dyn_rr_rot_thresh        = float(self.get_parameter("dyn_rr_rot_thresh")       or 0.09)
        # [D2] Shadow RR state (simulated, diagnostic-only)
        self._dyn_rr_held         = None   # symbol currently "held" in shadow
        self._dyn_rr_avg_entry    = None   # average entry price
        self._dyn_rr_trail_high   = None   # trailing high for chandelier
        self._dyn_rr_sim_w        = 0.0    # simulated risk weight (0..max_exp)
        self._dyn_rr_added        = False  # add-on used this hold
        self._dyn_rr_in_reentry   = False  # reentry wait active
        self._dyn_rr_last_action  = "INIT"
        self._d1_nav_c2n          = 1.0
        # [D2-TRADE] Trading + native return tracking
        self.dyn_alloc_c2n_trade_enable = str(self.get_parameter("dyn_alloc_c2n_trade_enable") or "0").strip().lower() in ("1", "true", "yes", "on")  # [RR_SLEEVE] off
        # [RR_BUDGET_OWNER] LEGACY=sleeve, C2N_NATIVE=native, RRX80_BRIDGE=rrx bridge
        self._rr_budget_owner = str(self.get_parameter("rr_budget_owner") or "LEGACY").strip().upper()
        self._dyn_rr_prev_held        = None   # rotation cleanup
        self._dyn_rr_prev_sim_w       = 0.0    # yesterday sim_w (for C2N return)
        self._dyn_rr_prev_native_held = None   # yesterday held (for C2N return)
        self._dyn_rr_prev_was_active  = False  # USFR sleeve was active last day
        # [DYN_ALLOC_D1C] daily shadow NAV — compact logging
        self.dyn_alloc_d1_summary_enable = str(self.get_parameter("dyn_alloc_d1_summary_enable") or "1").strip().lower() in ("1", "true", "yes", "on")
        self.dyn_alloc_d1_log_daily      = str(self.get_parameter("dyn_alloc_d1_log_daily")      or "0").strip().lower() in ("1", "true", "yes", "on")
        self._d1_days            = 0
        self._d1_c1_wins         = 0;  self._d1_c1_losses  = 0
        self._d1_c2_wins         = 0;  self._d1_c2_losses  = 0
        self._d1_c2q_wins        = 0;  self._d1_c2q_losses = 0
        self._d1_c2n_wins        = 0;  self._d1_c2n_losses = 0
        self._d1_c2q_ok_days     = 0;  self._d1_c2q_block_days = 0
        self._d1_last_summary_month = None
        # [DYN_ALLOC_D1D] final window summary on log_end_date
        self.dyn_alloc_d1_final_enable = str(self.get_parameter("dyn_alloc_d1_final_enable") or "1").strip().lower() in ("1", "true", "yes", "on")
        self._d1_final_logged = False
        if not hasattr(self, "log_end_date"): self.log_end_date = None
        # [DYN_ALLOC_D0] cross-mixin attribute guards (avoids getattr warnings)
        if not hasattr(self, "_active_tactical_symbol"):  self._active_tactical_symbol = None
        if not hasattr(self, "last_panic_winner"):        self.last_panic_winner = None
        if not hasattr(self, "_panic_state"):             self._panic_state = "NORMAL"
        if not hasattr(self, "_ids_active"):              self._ids_active = False
        if not hasattr(self, "_ids_state"):               self._ids_state = "NORMAL"
        if not hasattr(self, "short_shock_flag"):         self.short_shock_flag = False
        if not hasattr(self, "emergency_stop_triggered"): self.emergency_stop_triggered = False
        if not hasattr(self, "panic_tactical_universe"):  self.panic_tactical_universe = []

        # ── RR Sleeve (actual trading engine) [RR_SLEEVE] ────────────────────
        _rr_alloc = float(self.get_parameter("rr_sleeve_alloc") or self.dyn_alloc_base_rr)
        self._rr = RideRocketSleeve(self, _rr_alloc)
        self._rr.rr_init()
        self._rr_prev_held       = None   # sleeve rotation cleanup tracker
        self._rr_prev_was_active = False  # sleeve idle/active transition tracker

    def RrOnOrderFill(self, order_event) -> None:  # [RR_SLEEVE]
        """Route live fills to the RR sleeve for bootstrap confirmation."""
        sleeve = getattr(self, "_rr", None)
        if sleeve is not None:
            sleeve._rr_on_fill(order_event)

    def _DynTicker(self, sym) -> str:
        try:
            return sym.Value
        except Exception:
            try:
                return sym.value
            except Exception:
                return str(sym)

    def _DynReady(self) -> bool:
        try:
            if not (self.rr_smh_sma50.IsReady and self.rr_smh_sma100.IsReady and self.rr_qqq_sma100.IsReady):
                return False
            for sym in self.rr_candidates:
                if self._DynSymbolReady(sym):
                    return True
        except Exception:
            return False
        return False

    def _DynSymbolReady(self, sym) -> bool:
        try:
            return (self.rr_sma50[sym].IsReady and self.rr_sma200[sym].IsReady and self.rr_rsi14[sym].IsReady and self.rr_atr20[sym].IsReady and self.rr_vol20[sym].IsReady)
        except Exception:
            return False

    def _DynReturn(self, sym, days: int) -> float:
        try:
            hist = self.history(sym, days + 1, Resolution.DAILY)
            if hist is None or hist.empty or "close" not in hist.columns or len(hist) < days + 1:
                return 0.0
            c0 = float(hist["close"].iloc[0])
            c1 = float(hist["close"].iloc[-1])
            if c0 <= 0 or not np.isfinite(c0) or not np.isfinite(c1):
                return 0.0
            return float(c1 / c0 - 1.0)
        except Exception:
            return 0.0

    def _DynWeightFromTargets(self, targets: dict, sym) -> float:
        try:
            return float(targets.get(sym, 0.0) or 0.0)
        except Exception:
            return 0.0

    def _DynRocketScore(self, sym) -> float:
        if not self._DynSymbolReady(sym):
            return -999.0
        try:
            price = float(self.securities[sym].Price)
            sma50 = float(self.rr_sma50[sym].Current.Value)
            sma200 = float(self.rr_sma200[sym].Current.Value)
            if price > sma50 and sma50 > sma200:
                trend_score = 1.0
            elif price > sma50:
                trend_score = 0.5
            else:
                trend_score = 0.0
            sym_ret20 = self._DynReturn(sym, 20)
            qqq_ret20 = self._DynReturn(self.rr_qqq, 20)
            smh_ret20 = self._DynReturn(self.rr_smh, 20)
            rs_qqq = max(-1.0, min(1.0, (sym_ret20 - qqq_ret20) / 0.30))
            rs_smh = max(-1.0, min(1.0, (sym_ret20 - smh_ret20) / 0.30))
            adv20 = float(self.rr_vol20[sym].Current.Value)
            vol_score = min(float(self.securities[sym].Volume) / adv20, 2.0) / 2.0 if adv20 > 0 else 0.0
            rsi = float(self.rr_rsi14[sym].Current.Value)
            rsi_score = max(0.0, 1.0 - abs(rsi - 65.0) / 35.0)
            return float(0.30 * rs_qqq + 0.25 * rs_smh + 0.20 * trend_score + 0.15 * vol_score + 0.10 * rsi_score)
        except Exception:
            return -999.0

    def _DynTopRocketLeader(self):
        best_sym = None
        best_score = -999.0
        for sym in self.rr_candidates:
            score = self._DynRocketScore(sym)
            if score > best_score:
                best_sym = sym
                best_score = score
        return best_sym, float(best_score)

    def _DynRocketHypeOn(self, sym) -> bool:
        try:
            price = float(self.securities[sym].Price)
            smh_price = float(self.securities[self.rr_smh].Price)
            qqq_price = float(self.securities[self.rr_qqq].Price)
            volume = float(self.securities[sym].Volume)
            ret20 = self._DynReturn(sym, 20)
            smh_ret20 = self._DynReturn(self.rr_smh, 20)
            qqq_ret20 = self._DynReturn(self.rr_qqq, 20)
            adv20 = float(self.rr_vol20[sym].Current.Value)
            return bool(
                price > float(self.rr_sma50[sym].Current.Value)
                and price > float(self.rr_sma200[sym].Current.Value)
                and float(self.rr_sma50[sym].Current.Value) > float(self.rr_sma200[sym].Current.Value)
                and smh_price > float(self.rr_smh_sma50.Current.Value)
                and qqq_price > float(self.rr_qqq_sma100.Current.Value)
                and ret20 > smh_ret20
                and ret20 > qqq_ret20
                and adv20 > 0
                and volume > 1.4 * adv20
                and float(self.rr_rsi14[sym].Current.Value) < 70.0
            )
        except Exception:
            return False

    def _DynCgTacticalWinner(self):
        sym = self._active_tactical_symbol
        if sym is None:
            sym = self.last_panic_winner
        return sym

    def _DynClassifyRocketState(self) -> dict:
        out = {
            "ready": 0, "state": "RR_IDLE", "leader": "NONE", "score": -999.0,
            "ret20": 0.0, "spy20": 0.0, "smh20": 0.0, "qqq20": 0.0, "tac20": 0.0,
            "vs_spy": 0.0, "vs_smh": 0.0, "vs_qqq": 0.0, "vs_tac": 0.0,
            "rs_spy": 0.0, "rs_smh": 0.0, "rs_qqq": 0.0, "rs_tac": 0.0,
            "rsi": 0.0, "risk_w": 0.0, "cash_w": 0.95, "why": "not_ready"
        }
        if not self._DynReady():
            return out
        leader, score = self._DynTopRocketLeader()
        if leader is None or score <= -100.0:
            out.update({"ready": 1, "why": "no_leader"})
            return out
        ret20 = self._DynReturn(leader, 20)
        spy20 = self._DynReturn(self.sym_spy, 20)
        smh20 = self._DynReturn(self.rr_smh, 20)
        qqq20 = self._DynReturn(self.rr_qqq, 20)
        tac = self._DynCgTacticalWinner()
        tac20 = self._DynReturn(tac, 20) if tac is not None else 0.0
        rsi = 0.0
        try:
            rsi = float(self.rr_rsi14[leader].Current.Value)
        except Exception:
            pass
        def _rs(delta: float, norm: float) -> float:
            return max(-1.0, min(1.0, delta / max(norm, 0.001)))

        vs_spy = ret20 - spy20
        vs_smh = ret20 - smh20
        vs_qqq = ret20 - qqq20
        vs_tac = ret20 - tac20

        # Normalized relative-strength scores (clamped [-1, +1])
        # Divisor controls sensitivity: smaller = more sensitive to small outperformance.
        rs_spy = _rs(vs_spy, self.dyn_alloc_rs_norm_spy)
        rs_smh = _rs(vs_smh, self.dyn_alloc_rs_norm_smh)
        rs_qqq = _rs(vs_qqq, self.dyn_alloc_rs_norm_qqq)
        rs_tac = _rs(vs_tac, self.dyn_alloc_rs_norm_tac) if tac is not None else 0.0

        min_rs = self.dyn_alloc_rs_min_score
        overheat = bool(rsi >= self.dyn_alloc_overheat_rsi or (rsi >= self.dyn_alloc_profit_rsi and ret20 >= self.dyn_alloc_profit_ret20))
        beats_core_refs = bool(rs_spy > min_rs and rs_smh > min_rs and rs_qqq > min_rs)
        beats_tactical  = bool(tac is None or rs_tac > min_rs)
        hype = self._DynRocketHypeOn(leader)
        trend_ok = False
        try:
            px = float(self.securities[leader].Price)
            trend_ok = (px > float(self.rr_sma50[leader].Current.Value)
                        and px > float(self.rr_sma200[leader].Current.Value))
        except Exception:
            trend_ok = False

        state = "RR_IDLE"
        why = "weak_score"
        risk_w = 0.0
        cash_w = 0.95
        if not trend_ok or score < self.dyn_alloc_min_rr_score:
            state = "RR_DAMAGED" if ret20 < spy20 else "RR_IDLE"
            why = "trend_or_score_fail"
        elif overheat:
            state = "RR_OVERHEATED"
            why = "rsi_or_profit_stretch"
            risk_w = 0.40
            cash_w = 0.55
        elif beats_core_refs and beats_tactical and score >= self.dyn_alloc_strong_rr_score:
            # [D1-FIX] hype not required for regime classification — only for entry gate
            state = "RR_STRONG"
            why = "beats_spy_smh_qqq_tactical"
            risk_w = 0.60
            cash_w = 0.35
        elif beats_core_refs and score >= self.dyn_alloc_min_rr_score:
            state = "RR_ACTIVE"
            why = "valid_leader"
            risk_w = 0.40
            cash_w = 0.55
        else:
            state = "RR_DAMAGED"
            why = "leader_underperforms_refs"
            risk_w = 0.0
            cash_w = 0.95

        out.update({
            "ready": 1, "state": state, "leader": self._DynTicker(leader), "score": score,
            "ret20": ret20, "spy20": spy20, "smh20": smh20, "qqq20": qqq20, "tac20": tac20,
            "vs_spy": vs_spy, "vs_smh": vs_smh, "vs_qqq": vs_qqq, "vs_tac": vs_tac,
            "rs_spy": rs_spy, "rs_smh": rs_smh, "rs_qqq": rs_qqq, "rs_tac": rs_tac,
            "rsi": rsi, "risk_w": risk_w, "cash_w": cash_w, "hype": int(hype), "why": why
        })
        return out

    def _DynAllocFreezeReason(self, rr: dict) -> str:
        try:
            dd = float(self.CurrentDrawdown())
        except Exception:
            dd = 0.0
        ps = str(self._panic_state)
        ids = bool(self._ids_active)
        if bool(self.emergency_stop_triggered):
            return "EMERGENCY"
        if dd >= float(self.dyn_alloc_dd_freeze):
            return "DD_FREEZE"
        if bool(self.short_shock_flag):
            return "SHORT_SHOCK"
        if ps in ("WATCH", "STRESS", "PANIC"):
            return "PANIC_SCORE"
        if ids:
            return "IDS_" + str(self._ids_state)
        if not rr.get("ready", 0):
            return "RR_NOT_READY"
        return "NONE"

    def _DynAllocShadow(self, rr: dict) -> dict:
        cg_base = float(self.dyn_alloc_base_cg)
        rr_base = float(self.dyn_alloc_base_rr)
        s = cg_base + rr_base
        if s <= 0:
            cg_base, rr_base = 0.66, 0.34
        else:
            cg_base, rr_base = cg_base / s, rr_base / s
        freeze = self._DynAllocFreezeReason(rr)
        cg_shadow = cg_base
        rr_shadow = rr_base
        if freeze == "NONE":
            rr_state = str(rr.get("state", "RR_IDLE"))
            rr_cash = float(rr.get("cash_w", 0.95))
            if rr_state in ("RR_IDLE", "RR_DAMAGED") and rr_cash >= float(self.dyn_alloc_idle_cash_min):
                rr_shadow = 0.0
                cg_shadow = 1.0
        return {"cg_base": cg_base, "rr_base": rr_base, "cg_shadow": cg_shadow, "rr_shadow": rr_shadow, "freeze": freeze}

    def _DynSimRrReturn(self, rr: dict) -> float:
        """Simulate RR 1-day return: risk_w * leader_ret1d + cash_w * cash_ret1d."""
        try:
            leader_ticker = str(rr.get("leader", "NONE"))
            leader_sym = None
            for s in self.rr_candidates:
                if self._DynTicker(s) == leader_ticker:
                    leader_sym = s
                    break
            risk_w = float(rr.get("risk_w", 0.0))
            cash_w = float(rr.get("cash_w", 0.95))
            leader_ret = 0.0
            if leader_sym is not None and risk_w > 0:
                h = self.history(leader_sym, 2, Resolution.DAILY)
                if h is not None and not h.empty and "close" in h.columns and len(h) >= 2:
                    p0, p1 = float(h["close"].iloc[0]), float(h["close"].iloc[-1])
                    if p0 > 0:
                        leader_ret = p1 / p0 - 1.0
            cash_ret = 0.0
            h2 = self.history(self.rr_cash, 2, Resolution.DAILY)
            if h2 is not None and not h2.empty and "close" in h2.columns and len(h2) >= 2:
                p0, p1 = float(h2["close"].iloc[0]), float(h2["close"].iloc[-1])
                if p0 > 0:
                    cash_ret = p1 / p0 - 1.0
            return float(risk_w * leader_ret + cash_w * cash_ret)
        except Exception:
            return 0.0

    def _DynCgOkForIdleCapital(self) -> bool:
        """C1 safety: idle RR capital may move to CG only if CG is not stressed."""
        if not self.dyn_alloc_c1_require_cg_ok:
            return True
        # Emergency stop always blocks capital transfer to CG
        if bool(self.emergency_stop_triggered):
            return False
        try:
            if float(self.CurrentDrawdown()) >= float(self.dd_soft_start):
                return False
        except Exception:
            pass
        if self.short_shock_flag:
            return False
        if self._panic_state in ("WATCH", "STRESS", "PANIC"):
            return False
        if self._ids_active:
            return False
        if str(self.current_regime or "NA") == "RISK_OFF":
            return False
        return True

    def _DynRrStrongQuality(self, rr: dict) -> tuple:
        """Quality gate for C2Q: no 'better falling' allowed as Rocket."""
        try:
            if str(rr.get("state", "RR_IDLE")) != "RR_STRONG":
                return False, "not_strong"
            ret20  = float(rr.get("ret20",  0.0))
            smh20  = float(rr.get("smh20",  0.0))
            qqq20  = float(rr.get("qqq20",  0.0))
            rs_spy = float(rr.get("rs_spy", 0.0))
            rs_smh = float(rr.get("rs_smh", 0.0))
            rs_qqq = float(rr.get("rs_qqq", 0.0))
            rs_tac = float(rr.get("rs_tac", 0.0))
            rsi    = float(rr.get("rsi",    0.0))
            if ret20  < self.dyn_alloc_c2q_min_abs_ret20:  return False, "abs_ret_fail"
            if smh20  < self.dyn_alloc_c2q_min_smh20:      return False, "sector_fail"
            if qqq20  < self.dyn_alloc_c2q_min_qqq20:      return False, "sector_fail"
            min_rs = self.dyn_alloc_rs_min_score
            if rs_spy <= min_rs: return False, "rs_spy_fail"
            if rs_smh <= min_rs: return False, "rs_smh_fail"
            if rs_qqq <= min_rs: return False, "rs_qqq_fail"
            if rs_tac <= 0.0:    return False, "rs_tac_fail"
            if rsi    >= self.dyn_alloc_c2q_max_rsi: return False, "rsi_quality_fail"
            return True, "quality_ok"
        except Exception as ex:
            return False, f"quality_err:{type(ex).__name__}"

    def _DynDecideC1Weights(self, rr: dict, alloc: dict) -> tuple:
        cg_base = float(alloc.get("cg_base", 0.66))
        rr_base = float(alloc.get("rr_base", 0.34))
        if (
            str(alloc.get("freeze", "NONE")) == "NONE"
            and str(rr.get("state", "RR_IDLE")) in ("RR_IDLE", "RR_DAMAGED")
            and float(rr.get("cash_w", 0.95)) >= self.dyn_alloc_idle_cash_min
            and self._DynCgOkForIdleCapital()
        ):
            return 1.0, 0.0
        return cg_base, rr_base

    def _DynDecideC2Weights(self, rr: dict, alloc: dict) -> tuple:
        if str(alloc.get("freeze", "NONE")) == "NONE" and str(rr.get("state", "RR_IDLE")) == "RR_STRONG":
            rr_base = float(alloc.get("rr_base", 0.34))
            rr_c2 = min(rr_base * self.dyn_alloc_c2_rr_mult, 0.50)
            return 1.0 - rr_c2, rr_c2
        return self._DynDecideC1Weights(rr, alloc)

    def _DynDecideC2QWeights(self, rr: dict, alloc: dict) -> tuple:
        ok, why = self._DynRrStrongQuality(rr)
        if str(alloc.get("freeze", "NONE")) == "NONE" and ok:
            rr_base = float(alloc.get("rr_base", 0.34))
            rr_c2 = min(rr_base * self.dyn_alloc_c2_rr_mult, 0.50)
            return 1.0 - rr_c2, rr_c2, True, why
        c1_cg, c1_rr = self._DynDecideC1Weights(rr, alloc)
        return c1_cg, c1_rr, False, why

    # ── RR NATIVE SHADOW STATE MACHINE [D2] ──────────────────────
    def _DynRrNativeSymReady(self, sym) -> bool:
        try:
            return (self.rr_sma50[sym].IsReady and self.rr_sma200[sym].IsReady
                    and self.rr_rsi14[sym].IsReady and self.rr_atr20[sym].IsReady
                    and self.rr_vol20[sym].IsReady)
        except Exception:
            return False

    def _DynRrNativeHypeOn(self, sym) -> bool:
        """Mirror of RR hype_on(). RSI gate uses rsi_entry_max=70."""
        try:
            px   = float(self.securities[sym].Price)
            smhx = float(self.securities[self.rr_smh].Price)
            qqqx = float(self.securities[self.rr_qqq].Price)
            vol  = float(self.securities[sym].Volume)
            r20  = self._DynReturn(sym,      20)
            rs20 = self._DynReturn(self.rr_smh, 20)
            rq20 = self._DynReturn(self.rr_qqq, 20)
            adv  = float(self.rr_vol20[sym].Current.Value)
            return bool(
                px   > float(self.rr_sma50[sym].Current.Value)
                and px   > float(self.rr_sma200[sym].Current.Value)
                and float(self.rr_sma50[sym].Current.Value) > float(self.rr_sma200[sym].Current.Value)
                and smhx > float(self.rr_smh_sma50.Current.Value)
                and qqqx > float(self.rr_qqq_sma100.Current.Value)
                and r20  > rs20 and r20 > rq20
                and adv  > 0 and vol > 1.4 * adv
                and float(self.rr_rsi14[sym].Current.Value) < self.dyn_rr_rsi_entry_max
            )
        except Exception:
            return False

    def _DynRrNativeReentry(self, sym) -> bool:
        """Mirror of RR reentry_signal(). Requires in_reentry_wait."""
        if not self._dyn_rr_in_reentry:
            return False
        try:
            px   = float(self.securities[sym].Price)
            smhx = float(self.securities[self.rr_smh].Price)
            vol  = float(self.securities[sym].Volume)
            r5   = self._DynReturn(sym, 5)
            rs5  = self._DynReturn(self.rr_smh, 5)
            adv  = float(self.rr_vol20[sym].Current.Value)
            return bool(
                px   > float(self.rr_sma50[sym].Current.Value)
                and smhx > float(self.rr_smh_sma50.Current.Value)
                and smhx > float(self.rr_smh_sma100.Current.Value)
                and r5   > rs5 + 0.03
                and adv  > 0 and vol > 1.0 * adv
                and float(self.rr_rsi14[sym].Current.Value) < 88.0
            )
        except Exception:
            return False

    def _DynRrNativeCanAdd(self, sym) -> bool:
        """Mirror of RR can_add(). Uses shadow avg_entry and trail_high."""
        try:
            if self._dyn_rr_avg_entry is None or self._dyn_rr_trail_high is None:
                return False
            px  = float(self.securities[sym].Price)
            atr = float(self.rr_atr20[sym].Current.Value)
            ch_stop = self._dyn_rr_trail_high - self.dyn_rr_chandelier * atr
            return bool(
                px  > self._dyn_rr_avg_entry * 1.10
                and px  > ch_stop
                and float(self.securities[self.rr_smh].Price) > float(self.rr_smh_sma50.Current.Value)
                and float(self.rr_rsi14[sym].Current.Value) < self.dyn_rr_rsi_add_max
            )
        except Exception:
            return False

    def _DynRrNativeShouldLiq(self, sym) -> bool:
        """Mirror of RR should_liquidate()."""
        try:
            if self._dyn_rr_trail_high is None:
                return False
            px   = float(self.securities[sym].Price)
            smhx = float(self.securities[self.rr_smh].Price)
            qqqx = float(self.securities[self.rr_qqq].Price)
            atr  = float(self.rr_atr20[sym].Current.Value)
            ch_stop = self._dyn_rr_trail_high - self.dyn_rr_chandelier * atr
            r5 = self._DynReturn(sym, 5)
            return bool(
                px   < ch_stop
                or smhx < float(self.rr_smh_sma100.Current.Value)
                or qqqx < float(self.rr_qqq_sma100.Current.Value)
                or r5   < -0.12
                or (px < float(self.rr_sma50[sym].Current.Value)
                    and smhx < float(self.rr_smh_sma50.Current.Value))
            )
        except Exception:
            return False

    def _DynRrNativeTopLeader(self):
        best, best_score = None, -999.0
        for sym in self.rr_candidates:
            if not self._DynRrNativeSymReady(sym):
                continue
            score = self._DynRocketScore(sym)
            if score > best_score:
                best, best_score = sym, score
        return best, best_score

    def _DynRrNativeStep(self) -> dict:
        """Run one shadow RR step. Returns state dict for logging + allocation. [D2]"""
        null = {"state": "NATIVE_NOT_READY", "action": "SKIP", "held": "NONE",
                "sim_w": 0.0, "rsi": 0.0, "why": "not_ready"}
        if not self._DynReady():
            return null
        try:
            state  = "NATIVE_IDLE"
            action = "NONE"
            why    = ""
            sym    = self._dyn_rr_held
            px     = float(self.securities[sym].Price) if sym is not None else 0.0
            rsi    = float(self.rr_rsi14[sym].Current.Value) if sym is not None else 0.0

            if sym is not None and self._DynRrNativeSymReady(sym):
                # Update trailing high
                if self._dyn_rr_trail_high is None:
                    self._dyn_rr_trail_high = px
                self._dyn_rr_trail_high = max(self._dyn_rr_trail_high, px)
                smhx = float(self.securities[self.rr_smh].Price)

                if self._DynRrNativeShouldLiq(sym):
                    action = "EXIT"; state = "NATIVE_EXIT"; why = "liquidate"
                    self._dyn_rr_held = None; self._dyn_rr_avg_entry = None
                    self._dyn_rr_trail_high = None; self._dyn_rr_added = False
                    self._dyn_rr_sim_w = 0.0; self._dyn_rr_in_reentry = True

                elif smhx < float(self.rr_smh_sma50.Current.Value) and self._dyn_rr_sim_w > self.dyn_rr_base_exp:
                    action = "TRIM_SECTOR"; state = "NATIVE_TRIM"; why = "smh_below_sma50"
                    self._dyn_rr_sim_w = self.dyn_rr_base_exp

                elif (self._dyn_rr_avg_entry and self._dyn_rr_avg_entry > 0
                      and self._dyn_rr_sim_w > self.dyn_rr_base_exp
                      and px / self._dyn_rr_avg_entry - 1 > self.dyn_rr_pl_thresh
                      and rsi > self.dyn_rr_pl_rsi_min):
                    action = "TRIM_PL"; state = "NATIVE_TRIM"; why = "profit_lock"
                    self._dyn_rr_sim_w = self.dyn_rr_base_exp

                elif rsi > self.dyn_rr_rsi_trim and self._dyn_rr_sim_w > self.dyn_rr_base_exp:
                    action = "TRIM_RSI"; state = "NATIVE_TRIM"; why = "rsi_euphoria"
                    self._dyn_rr_sim_w = max(self.dyn_rr_base_exp, self._dyn_rr_sim_w - self.dyn_rr_add_exp)

                elif not self._dyn_rr_added and self._DynRrNativeCanAdd(sym):
                    action = "ADD_ON"; state = "NATIVE_ADD_OK"; why = "can_add"
                    self._dyn_rr_sim_w = min(self.dyn_rr_max_exp, self._dyn_rr_sim_w + self.dyn_rr_add_exp)
                    self._dyn_rr_added = True

                else:
                    leader, l_score = self._DynRrNativeTopLeader()
                    if (leader is not None and leader != sym
                            and self._DynRrNativeSymReady(leader)
                            and l_score - self._DynRocketScore(sym) > self.dyn_rr_rot_thresh):
                        action = "ROTATE"; state = "NATIVE_HOLD"; why = f"rotate→{self._DynTicker(leader)}"
                        self._dyn_rr_held = leader
                        self._dyn_rr_avg_entry = float(self.securities[leader].Price)
                        self._dyn_rr_trail_high = self._dyn_rr_avg_entry
                        self._dyn_rr_added = False
                        self._dyn_rr_sim_w = self.dyn_rr_base_exp
                    else:
                        action = "HOLD"
                        state  = "NATIVE_ADD_OK" if self._dyn_rr_added else "NATIVE_HOLD"
                        why    = "added" if self._dyn_rr_added else "holding"
            else:
                # No position — look for entry
                leader, _ = self._DynRrNativeTopLeader()
                if leader is not None and self._DynRrNativeSymReady(leader):
                    hype    = self._DynRrNativeHypeOn(leader)
                    reentry = self._DynRrNativeReentry(leader)
                    if hype or reentry:
                        action = "ENTRY_HYPE" if hype else "ENTRY_REENTRY"
                        state  = "NATIVE_ENTRY_OK"
                        why    = action
                        self._dyn_rr_held       = leader
                        self._dyn_rr_avg_entry  = float(self.securities[leader].Price)
                        self._dyn_rr_trail_high = self._dyn_rr_avg_entry
                        self._dyn_rr_added      = False
                        self._dyn_rr_in_reentry = False
                        self._dyn_rr_sim_w      = self.dyn_rr_base_exp
                        rsi = float(self.rr_rsi14[leader].Current.Value)
                    else:
                        state  = "NATIVE_REENTRY_WAIT" if self._dyn_rr_in_reentry else "NATIVE_IDLE"
                        action = "WAIT"
                        why    = "no_signal"

            self._dyn_rr_last_action = action
            return {
                "state": state, "action": action, "why": why,
                "held": self._DynTicker(self._dyn_rr_held) if self._dyn_rr_held else "NONE",
                "sim_w": float(self._dyn_rr_sim_w),
                "rsi": float(rsi), "px": float(px),
                "avg_entry": float(self._dyn_rr_avg_entry or 0),
            }
        except Exception as ex:
            return {"state": "NATIVE_ERR", "action": "ERR", "held": "NONE",
                    "sim_w": 0.0, "rsi": 0.0, "why": str(type(ex).__name__)}

    def _DynDecideC2NativeWeights(self, rr_native: dict, rr: dict, alloc: dict) -> tuple:
        """C2_NATIVE shadow weights — must mirror actual _ApplyC2NTrade / _DynNativeMetaBudget. [D2]
        C1 path: native idle → CG if CG ok.
        C2 path: native ADD_OK + RR_STRONG quality → overweight RR.
        Base path: standard 66/34.
        """
        cg_base = float(alloc.get("cg_base", 0.66))
        rr_base = float(alloc.get("rr_base", 0.34))
        freeze  = str(alloc.get("freeze", "NONE"))
        n_state = str(rr_native.get("state", "NATIVE_IDLE"))
        sim_w   = float(rr_native.get("sim_w", 0.0))
        # No leader → always full CG (mirrors _DynNativeMetaBudget)
        idle_states = ("NATIVE_IDLE", "NATIVE_REENTRY_WAIT", "NATIVE_EXIT",
                       "NATIVE_NOT_READY", "NATIVE_ERR", "")
        if n_state in idle_states or sim_w < 0.005:
            return 1.0, 0.0
        # C2: overweight only if native ADD_OK AND independent RR quality
        if (freeze == "NONE"
                and n_state == "NATIVE_ADD_OK"
                and sim_w >= self.dyn_rr_max_exp):
            q_ok, _ = self._DynRrStrongQuality(rr)
            if q_ok:
                rr_c2n = min(rr_base * (self.dyn_rr_max_exp /
                             max(self.dyn_rr_base_exp, 0.01)), 0.50)
                return 1.0 - rr_c2n, rr_c2n
        # Base: standard split
        return cg_base, rr_base

    def _DynUpdateShadowNav(self, rr: dict, alloc: dict,
                            rr_native: dict = None,
                            native_rr_ret: float = None) -> dict:
        """Update BASE/C1/C2/C2Q/C2N shadow NAV. [D1B/D2]
        rr_native:     pre-computed from EmitDynAllocD0 (decoupled from D1).
        native_rr_ret: 1-day return of native-held position (not classifier leader).
        """
        rr_native = rr_native if rr_native is not None else {}
        try:
            cg_base = float(alloc.get("cg_base", 0.66))
            rr_base = float(alloc.get("rr_base", 0.34))
            port_now = float(self.portfolio.TotalPortfolioValue)
            if self._d1_port_prev is None or self._d1_port_prev <= 0:
                self._d1_port_prev = port_now
                c1_cg,  c1_rr                = self._DynDecideC1Weights(rr, alloc)
                c2_cg,  c2_rr                = self._DynDecideC2Weights(rr, alloc)
                c2q_cg, c2q_rr, q_ok, q_why = self._DynDecideC2QWeights(rr, alloc)
                c2n_cg, c2n_rr               = self._DynDecideC2NativeWeights(rr_native, rr, alloc)
                self._d1_prev_weights = {
                    "base": (cg_base, rr_base), "c1":  (c1_cg,  c1_rr),
                    "c2":   (c2_cg,   c2_rr),   "c2q": (c2q_cg, c2q_rr),
                    "c2n":  (c2n_cg,  c2n_rr),  "c2q_ok": q_ok, "c2q_why": q_why,
                }
                return {
                    "cg_ret": 0.0, "rr_ret": 0.0, "rr_native": rr_native,
                    "base_cg_w": cg_base, "base_rr_w": rr_base,
                    "c1_cg_w": c1_cg,   "c1_rr_w": c1_rr,
                    "c2_cg_w": c2_cg,   "c2_rr_w": c2_rr,
                    "c2q_cg_w": c2q_cg, "c2q_rr_w": c2q_rr,
                    "c2n_cg_w": c2n_cg, "c2n_rr_w": c2n_rr,
                    "base_d": 0.0, "c1_d": 0.0, "c2_d": 0.0, "c2q_d": 0.0, "c2n_d": 0.0,
                    "base_nav": self._d1_nav_base, "c1_nav": self._d1_nav_c1,
                    "c2_nav":   self._d1_nav_c2,   "c2q_nav": self._d1_nav_c2q,
                    "c2n_nav":  self._d1_nav_c2n,
                    "c2q_ok": q_ok, "c2q_why": q_why,
                }
            cg_ret = port_now / self._d1_port_prev - 1.0
            self._d1_port_prev = port_now
            rr_ret = self._DynSimRrReturn(rr)
            # C2N uses native return — tracks actual held position, not classifier
            c2n_rr_ret = native_rr_ret if native_rr_ret is not None else rr_ret
            prev = self._d1_prev_weights or {
                "base": (cg_base, rr_base), "c1": (cg_base, rr_base),
                "c2": (cg_base, rr_base),   "c2q": (cg_base, rr_base),
                "c2n": (cg_base, rr_base),  "c2q_ok": False, "c2q_why": "init_missing",
            }
            base_cg_w, base_rr_w = prev["base"]
            c1_cg_w,   c1_rr_w   = prev["c1"]
            c2_cg_w,   c2_rr_w   = prev["c2"]
            c2q_cg_w,  c2q_rr_w  = prev["c2q"]
            c2n_cg_w,  c2n_rr_w  = prev.get("c2n", (cg_base, rr_base))
            base_d = base_cg_w * cg_ret + base_rr_w * rr_ret
            c1_d   = c1_cg_w   * cg_ret + c1_rr_w   * rr_ret
            c2_d   = c2_cg_w   * cg_ret + c2_rr_w   * rr_ret
            c2q_d  = c2q_cg_w  * cg_ret + c2q_rr_w  * rr_ret
            c2n_d  = c2n_cg_w  * cg_ret + c2n_rr_w  * c2n_rr_ret
            self._d1_nav_base *= (1.0 + base_d)
            self._d1_nav_c1   *= (1.0 + c1_d)
            self._d1_nav_c2   *= (1.0 + c2_d)
            self._d1_nav_c2q  *= (1.0 + c2q_d)
            self._d1_nav_c2n  *= (1.0 + c2n_d)
            nc1_cg,  nc1_rr                  = self._DynDecideC1Weights(rr, alloc)
            nc2_cg,  nc2_rr                  = self._DynDecideC2Weights(rr, alloc)
            nc2q_cg, nc2q_rr, nq_ok, nq_why = self._DynDecideC2QWeights(rr, alloc)
            nc2n_cg, nc2n_rr                 = self._DynDecideC2NativeWeights(rr_native, rr, alloc)
            self._d1_prev_weights = {
                "base": (cg_base, rr_base), "c1":  (nc1_cg,  nc1_rr),
                "c2":   (nc2_cg,  nc2_rr),  "c2q": (nc2q_cg, nc2q_rr),
                "c2n":  (nc2n_cg, nc2n_rr), "c2q_ok": nq_ok, "c2q_why": nq_why,
            }
            return {
                "cg_ret": cg_ret, "rr_ret": rr_ret, "rr_native": rr_native,
                "base_cg_w": base_cg_w, "base_rr_w": base_rr_w,
                "c1_cg_w": c1_cg_w,   "c1_rr_w": c1_rr_w,
                "c2_cg_w": c2_cg_w,   "c2_rr_w": c2_rr_w,
                "c2q_cg_w": c2q_cg_w, "c2q_rr_w": c2q_rr_w,
                "c2n_cg_w": c2n_cg_w, "c2n_rr_w": c2n_rr_w,
                "base_d": base_d, "c1_d": c1_d, "c2_d": c2_d,
                "c2q_d": c2q_d, "c2n_d": c2n_d,
                "base_nav": self._d1_nav_base, "c1_nav": self._d1_nav_c1,
                "c2_nav":   self._d1_nav_c2,   "c2q_nav": self._d1_nav_c2q,
                "c2n_nav":  self._d1_nav_c2n,
                "c2q_ok": bool(prev.get("c2q_ok", False)),
                "c2q_why": str(prev.get("c2q_why", "NA")),
            }
        except Exception:
            return {}
    def _DynUpdateD1Stats(self, d1: dict) -> None:
        """Accumulate daily win/loss counters vs BASE. [D1C]"""
        try:
            self._d1_days += 1
            base_d = float(d1.get("base_d", 0.0))
            c1_d  = float(d1.get("c1_d",  0.0))
            c2_d  = float(d1.get("c2_d",  0.0))
            c2q_d = float(d1.get("c2q_d", 0.0))
            if c1_d > base_d:    self._d1_c1_wins  += 1
            elif c1_d < base_d:  self._d1_c1_losses += 1
            if c2_d > base_d:    self._d1_c2_wins  += 1
            elif c2_d < base_d:  self._d1_c2_losses += 1
            if c2q_d > base_d:   self._d1_c2q_wins  += 1
            elif c2q_d < base_d: self._d1_c2q_losses += 1
            c2n_d = float(d1.get("c2n_d", 0.0))
            if c2n_d > base_d:   self._d1_c2n_wins  += 1
            elif c2n_d < base_d: self._d1_c2n_losses += 1
            if bool(d1.get("c2q_ok", False)):
                self._d1_c2q_ok_days    += 1
            else:
                self._d1_c2q_block_days += 1
        except Exception:
            pass

    def _DynLogD1SummaryLine(self, tag: str, d1: dict) -> None:
        """Emit one summary line with given tag. [D1D]"""
        try:
            self.log(
                f"{tag},{self.time.date()},"
                f"days={self._d1_days},"
                f"base_nav={float(d1.get('base_nav',1.0)):.4f},"
                f"c1_nav={float(d1.get('c1_nav',1.0)):.4f},"
                f"c2_nav={float(d1.get('c2_nav',1.0)):.4f},"
                f"c2q_nav={float(d1.get('c2q_nav',1.0)):.4f},"
                f"c2n_nav={float(d1.get('c2n_nav',1.0)):.4f},"
                f"c1_wl={self._d1_c1_wins}/{self._d1_c1_losses},"
                f"c2_wl={self._d1_c2_wins}/{self._d1_c2_losses},"
                f"c2q_wl={self._d1_c2q_wins}/{self._d1_c2q_losses},"
                f"c2n_wl={self._d1_c2n_wins}/{self._d1_c2n_losses},"
                f"c2q_ok_block={self._d1_c2q_ok_days}/{self._d1_c2q_block_days}"
            )
        except Exception:
            pass

    def _DynDateOnly(self, x):
        """Normalize date/datetime/string to date. [D1D-FIX]"""
        try:
            if x is None:
                return None
            if hasattr(x, "date") and callable(x.date):
                return x.date()
            if hasattr(x, "year") and hasattr(x, "month") and hasattr(x, "day"):
                from datetime import date as _date
                return _date(int(x.year), int(x.month), int(x.day))
            if isinstance(x, str):
                s = x.strip()[:10]
                if len(s) == 10:
                    return datetime.strptime(s, "%Y-%m-%d").date()
            return x
        except Exception:
            return None

    def _DynMaybeLogD1Summary(self, d1: dict) -> None:
        """Monthly checkpoint + final window summary on log_end_date. [D1C/D1D-FIX]"""
        if not d1:
            return
        # [D1D-FIX] Final summary: normalize both sides before comparing
        try:
            end_date = self._DynDateOnly(self.log_end_date)
            cur_date = self._DynDateOnly(self.time)
            # ARM debug: log type info on last days of each quarter if FINAL not yet fired
            if (
                self.dyn_alloc_d1_final_enable
                and not self._d1_final_logged
                and cur_date is not None
                and cur_date.day >= 25
                and cur_date.month in (3, 6, 9, 12)
            ):
                self.log(
                    f"DYN_ALLOC_D1_FINAL_ARM,{cur_date},"
                    f"raw_end={self.log_end_date!r},"
                    f"end={end_date},cur={cur_date},"
                    f"days={self._d1_days},"
                    f"base_nav={float(d1.get('base_nav',1.0)):.4f}"
                )
            if (
                self.dyn_alloc_d1_final_enable
                and not self._d1_final_logged
                and end_date is not None
                and cur_date is not None
                and cur_date >= end_date
            ):
                self._d1_final_logged = True
                self._DynLogD1SummaryLine("DYN_ALLOC_D1_FINAL", d1)
        except Exception:
            pass
        # [D1C] Monthly checkpoint
        if not self.dyn_alloc_d1_summary_enable:
            return
        try:
            mk = int(self.time.year) * 100 + int(self.time.month)
            if self._d1_last_summary_month == mk:
                return
            self._d1_last_summary_month = mk
            self._DynLogD1SummaryLine("DYN_ALLOC_D1_SUMMARY", d1)
        except Exception:
            pass

    def _DynNativeMetaBudget(self) -> tuple:
        """Return (cg_budget, rr_budget) for combined targets. [D2-TRADE]
        Rule: targets change ONLY when RR has an active leader.
          held=None → cg=1.00, rr=0.00 always (no USFR parking when idle)
          held!=None → cg=0.66, rr=0.34 (CG health gate applied per-trade in _ApplyC2NTrade)
        """
        held  = self._dyn_rr_held
        sim_w = float(self._dyn_rr_sim_w)
        if held is None or sim_w < 0.005:
            return 1.0, 0.0   # No leader → full CG, zero RR, zero USFR
        return float(self.dyn_alloc_base_cg), float(self.dyn_alloc_base_rr)

    def _DynNativeReturn(self, prev_held, prev_sim_w: float) -> float:
        """1-day return of native-held position from PREVIOUS day. [D2-NAV]
        Used for C2N shadow NAV so it tracks the real native position, not classifier.
        """
        try:
            rr_base = float(self.dyn_alloc_base_rr)
            if prev_held is None or prev_sim_w < 0.005:
                # Idle RR: USFR ≈ flat daily
                cs = self.rr_cash
                if cs is not None:
                    return self._DynReturn(cs, 1)
                return 0.0
            # Active: leader return (1-day)
            leader_ret = self._DynReturn(prev_held, 1)
            # Within RR sleeve: sim_w in leader, remainder in USFR
            usfr_ret = 0.0
            cs = self.rr_cash
            if cs is not None:
                usfr_ret = self._DynReturn(cs, 1)
            return prev_sim_w * leader_ret + (1.0 - prev_sim_w) * usfr_ret
        except Exception:
            return 0.0

    def _ApplyC2NTrade(self, combined: dict) -> None:
        """[D2-TRADE] Two-level meta-allocation applied to combined targets.
        Level 1 (meta): _DynNativeMetaBudget decides cg_budget / rr_budget.
          - C1: RR idle + CG ok  -> cg=1.00, rr=0.00 (idle RR capital to CG)
          - RR idle + CG stressed -> cg=0.66, rr=0.34 (park in USFR)
          - RR active             -> cg=0.66, rr=0.34
        Level 2 (within RR): native sim_w splits rr_budget -> leader + USFR.
        QC execution handles actual buys/sells; no manual cash tracking needed.
        """
        try:
            if not self.dyn_alloc_c2n_trade_enable:
                return
            rr_cash_sym = self.rr_cash
            held        = self._dyn_rr_held
            sim_w       = float(self._dyn_rr_sim_w)
            cg_budget, rr_budget = self._DynNativeMetaBudget()
            leader_target = sim_w * rr_budget if held is not None else 0.0
            usfr_target   = rr_budget - leader_target

            # Rotation: zero old leader
            prev = self._dyn_rr_prev_held
            if prev is not None and prev != held:
                combined[prev] = 0.0
            self._dyn_rr_prev_held = held

            # Scale all CG positions to cg_budget fraction
            rr_syms = {held, prev, rr_cash_sym} - {None}
            for s in list(combined.keys()):
                if s not in rr_syms:
                    combined[s] = float(combined.get(s, 0.0)) * cg_budget

            # Set RR sleeve — only when RR is actually active (rr_budget > 0)
            # When idle: skip USFR entirely. Only close out USFR if we were previously active.
            prev_active = self._dyn_rr_prev_was_active
            rr_active   = rr_budget > 0.005
            if rr_active:
                if rr_cash_sym is not None:
                    combined[rr_cash_sym] = usfr_target
                if held is not None and leader_target > 0.005:
                    combined[held] = leader_target
                elif held is not None:
                    combined.pop(held, None)
            elif prev_active:
                # Just transitioned to idle — close out USFR position if any
                if rr_cash_sym is not None:
                    combined[rr_cash_sym] = 0.0
            # else: idle and was already idle — don't touch USFR, no warmup data needed
            self._dyn_rr_prev_was_active = rr_active

            # Store for D0 logging
            self._dyn_c2n_last_targets = {
                "cg_budget": cg_budget, "rr_budget": rr_budget,
                "leader": self._DynTicker(held) if held else "NONE",
                "leader_target": leader_target, "usfr_target": usfr_target,
            }
        except Exception:
            pass

    def _ApplyRrSleeveTrade(self, combined: dict) -> None:  # [RR_SLEEVE]
        """Apply RR sleeve targets to combined. Mirrors _ApplyC2NTrade logic but
        reads exclusively from self._rr (rr_sleeve.py). Gated by RR_TRADE_ENABLE."""
        try:
            if not _RR_TRADE_ENABLE:
                return
            sleeve = getattr(self, "_rr", None)
            if sleeve is None:
                return
            rr_cash = sleeve.usfr

            # Source of truth: held_symbol (post-bootstrap) or pre-staged rr_targets (bootstrap)
            held = sleeve.held_symbol
            if held is None:  # bootstrap: rr_targets may have pre-staged position
                held = next((s for s, w in sleeve.rr_targets.items()
                             if s != rr_cash and float(w) > 0.005), None)
            sim_w = float(sleeve.rr_targets.get(held, 0.0)) if held else 0.0

            # Meta-budget: idle/no position → CG=100%; active → CG=base_cg, RR=base_rr
            if held is None or sim_w < 0.005:
                cg_budget, rr_budget = 1.0, 0.0
            else:
                cg_budget = float(self.dyn_alloc_base_cg)
                rr_budget = float(self.dyn_alloc_base_rr)

            leader_target = sim_w * rr_budget
            usfr_target   = rr_budget - leader_target

            # Rotation cleanup: zero previous leader when it changes
            prev = self._rr_prev_held
            if prev is not None and prev != held:
                combined[prev] = 0.0
            self._rr_prev_held = held

            # Scale all CG positions to cg_budget fraction
            rr_syms = {held, prev, rr_cash} - {None}
            for s in list(combined.keys()):
                if s not in rr_syms:
                    combined[s] = float(combined.get(s, 0.0)) * cg_budget

            # Apply RR sleeve positions
            prev_active = self._rr_prev_was_active
            rr_active   = rr_budget > 0.005
            if rr_active:
                combined[rr_cash] = usfr_target
                if held is not None and leader_target > 0.005:
                    combined[held] = leader_target
                elif held is not None:
                    combined.pop(held, None)
            elif prev_active:
                combined[rr_cash] = 0.0   # close USFR on idle→idle transition
            self._rr_prev_was_active = rr_active

            # Update D0 log tracker
            self._dyn_c2n_last_targets = {
                "cg_budget": cg_budget, "rr_budget": rr_budget,
                "leader":  sleeve._ticker(held) if held else "NONE",
                "leader_target": leader_target, "usfr_target": usfr_target,
            }
        except Exception as e:
            self.log(f"[RR_SLEEVE_TRADE_ERR] {e}")

    def EmitDynAllocD0(self, combined_targets: dict) -> None:
        """Emit DYN_ALLOC_D0 diagnostics and apply C2N trading when enabled."""
        if not self.dyn_alloc_d0_enable:
            return
        try:
            rr    = self._DynClassifyRocketState()
            alloc = self._DynAllocShadow(rr)

            # [D2] Native step runs FIRST, independently of D1 diagnostic
            # Save previous state BEFORE step (for native return computation)
            prev_native_held = self._dyn_rr_held
            prev_native_sim_w = float(self._dyn_rr_sim_w)
            rr_native = self._DynRrNativeStep() if self.dyn_alloc_d2_enable else {}
            # Compute native return based on YESTERDAY's position (before today's step)
            native_rr_ret = self._DynNativeReturn(prev_native_held, prev_native_sim_w)
            # Update prev trackers for tomorrow
            self._dyn_rr_prev_native_held = prev_native_held
            self._dyn_rr_prev_sim_w       = prev_native_sim_w

            # [D1C] Shadow NAV daily update — receives pre-computed rr_native + native return
            d1 = {}
            if self.dyn_alloc_d1_enable:
                d1 = self._DynUpdateShadowNav(rr, alloc,
                                              rr_native=rr_native,
                                              native_rr_ret=native_rr_ret)
                if d1:
                    self._DynUpdateD1Stats(d1)
            self._DynMaybeLogD1Summary(d1)

            # [RR_BUDGET_OWNER] owner-based execution dispatch
            _owner = getattr(self, "_rr_budget_owner", "LEGACY")
            if _owner == "RRX80_BRIDGE":
                self._ApplyRRXTradeBridge(combined_targets)
            elif _owner == "C2N_NATIVE":
                self._ApplyC2NTrade(combined_targets)
            else:
                sleeve = getattr(self, "_rr", None)
                if sleeve is not None:
                    sleeve._rr_trade_logic()
                self._ApplyRrSleeveTrade(combined_targets)

            # [D2-DIAG] RR gate diagnostic — logs every day native step has a signal or state change
            if self.dyn_alloc_d2_enable and self._LogAllowedAt():
                n_state  = str(rr_native.get("state",  "NATIVE_NOT_READY"))
                n_action = str(rr_native.get("action", "?"))
                n_why    = str(rr_native.get("why",    "?"))
                n_held   = str(rr_native.get("held",   "NONE"))
                n_simw   = float(rr_native.get("sim_w", 0.0))
                mt       = getattr(self, "_dyn_c2n_last_targets", {})
                if n_state not in ("NATIVE_IDLE", "NATIVE_NOT_READY") or n_simw > 0.005:
                    self.log(
                        f"RR_GATE,{self.time.date()},"
                        f"d2_en={int(self.dyn_alloc_d2_enable)},"
                        f"ready={int(self._DynReady())},"
                        f"trade_en={int(self.dyn_alloc_c2n_trade_enable)},"
                        f"native_state={n_state},action={n_action},why={n_why},"
                        f"held={n_held},sim_w={n_simw:.2f},"
                        f"inst_held={self._DynTicker(self._dyn_rr_held) if self._dyn_rr_held else 'NONE'},"
                        f"inst_simw={float(self._dyn_rr_sim_w):.2f},"
                        f"meta_cg={float(mt.get('cg_budget',0.0)):.2f},"
                        f"meta_rr={float(mt.get('rr_budget',0.0)):.2f},"
                        f"leader_tgt={float(mt.get('leader_target',0.0)):.3f}"
                    )

            # ── Logging (gated: trading above is NOT gated) ────────────────
            if not self._LogAllowedAt():
                return
            cg_spy_w  = self._DynWeightFromTargets(combined_targets, self.sym_spy)
            cg_cash_w = (self._DynWeightFromTargets(combined_targets, self.sym_cash) +
                         self._DynWeightFromTargets(combined_targets, self.sym_crash))
            cg_tac_w = 0.0
            for sym in self.panic_tactical_universe:
                cg_tac_w += self._DynWeightFromTargets(combined_targets, sym)
            changed     = abs(float(alloc["rr_shadow"]) - float(alloc["rr_base"])) >= 0.005
            interesting = changed or str(rr.get("state", "RR_IDLE")) != "RR_IDLE"
            should_log_d0 = bool(self.dyn_alloc_d0_log_all or interesting)
            should_log_d1 = bool(self.dyn_alloc_d1_log_daily or interesting)
            if not should_log_d0 and not should_log_d1:
                return
            if should_log_d0:
                self._dyn_alloc_last = {"rr": dict(rr), "alloc": dict(alloc)}
                mt = getattr(self, "_dyn_c2n_last_targets", {})
                self.log(
                    f"DYN_ALLOC_D0,{self.time.date()},"
                    f"cg_reg={self.current_regime or 'NA'},"
                    f"cg_spy={cg_spy_w:.3f},cg_tac={cg_tac_w:.3f},cg_cash={cg_cash_w:.3f},"
                    f"rr_state={rr.get('state','NA')},rr_leader={rr.get('leader','NONE')},"
                    f"rr_score={float(rr.get('score',0.0)):.3f},rr_ret20={float(rr.get('ret20',0.0)):.3f},"
                    f"spy20={float(rr.get('spy20',0.0)):.3f},smh20={float(rr.get('smh20',0.0)):.3f},qqq20={float(rr.get('qqq20',0.0)):.3f},"
                    f"vs_spy={float(rr.get('vs_spy',0.0)):.3f},vs_smh={float(rr.get('vs_smh',0.0)):.3f},"
                    f"vs_qqq={float(rr.get('vs_qqq',0.0)):.3f},vs_tac={float(rr.get('vs_tac',0.0)):.3f},"
                    f"rs_spy={float(rr.get('rs_spy',0.0)):.2f},rs_smh={float(rr.get('rs_smh',0.0)):.2f},"
                    f"rs_qqq={float(rr.get('rs_qqq',0.0)):.2f},rs_tac={float(rr.get('rs_tac',0.0)):.2f},"
                    f"rsi={float(rr.get('rsi',0.0)):.1f},hype={rr.get('hype',0)},rr_cash={float(rr.get('cash_w',0.0)):.2f},"
                    f"cg_base={float(alloc['cg_base']):.2f},rr_base={float(alloc['rr_base']):.2f},"
                    f"cg_shadow={float(alloc['cg_shadow']):.2f},rr_shadow={float(alloc['rr_shadow']):.2f},"
                    f"freeze={alloc.get('freeze','NA')},why={rr.get('why','NA')},"
                    f"meta_cg={float(mt.get('cg_budget',0.0)):.2f},"
                    f"meta_rr={float(mt.get('rr_budget',0.0)):.2f},"
                    f"rr_held={mt.get('leader','NONE')},"
                    f"rr_leader_tgt={float(mt.get('leader_target',0.0)):.3f},"
                    f"rr_usfr_tgt={float(mt.get('usfr_target',0.0)):.3f}"
                )
            # [DYN_ALLOC_D1] detail log on interesting days
            if self.dyn_alloc_d1_enable and d1 and should_log_d1:
                rn = d1.get("rr_native", {})
                self.log(
                    f"DYN_ALLOC_D1,{self.time.date()},"
                    f"state={rr.get('state','NA')},leader={rr.get('leader','NONE')},freeze={alloc.get('freeze','NA')},"
                    f"cg_ret={d1.get('cg_ret',0.0):.4f},rr_ret={d1.get('rr_ret',0.0):.4f},"
                    f"b_w={d1.get('base_cg_w',0.66):.2f}/{d1.get('base_rr_w',0.34):.2f},"
                    f"c1_w={d1.get('c1_cg_w',0.66):.2f}/{d1.get('c1_rr_w',0.34):.2f},"
                    f"c2_w={d1.get('c2_cg_w',0.66):.2f}/{d1.get('c2_rr_w',0.34):.2f},"
                    f"c2q_w={d1.get('c2q_cg_w',0.66):.2f}/{d1.get('c2q_rr_w',0.34):.2f},"
                    f"c2n_w={d1.get('c2n_cg_w',0.66):.2f}/{d1.get('c2n_rr_w',0.34):.2f},"
                    f"base_d={d1.get('base_d',0.0):.4f},c1_d={d1.get('c1_d',0.0):.4f},"
                    f"c2_d={d1.get('c2_d',0.0):.4f},c2q_d={d1.get('c2q_d',0.0):.4f},c2n_d={d1.get('c2n_d',0.0):.4f},"
                    f"base_nav={d1.get('base_nav',1.0):.4f},c1_nav={d1.get('c1_nav',1.0):.4f},"
                    f"c2_nav={d1.get('c2_nav',1.0):.4f},c2q_nav={d1.get('c2q_nav',1.0):.4f},"
                    f"c2n_nav={d1.get('c2n_nav',1.0):.4f},"
                    f"c2q_ok={int(bool(d1.get('c2q_ok',False)))},c2q_why={d1.get('c2q_why','NA')}"
                )
                # [D2] RR Native state log
                if rn and str(rn.get("state","")) not in ("NATIVE_NOT_READY", "NATIVE_IDLE", ""):
                    self.log(
                        f"DYN_RR_NATIVE,{self.time.date()},"
                        f"state={rn.get('state','NA')},action={rn.get('action','NA')},"
                        f"held={rn.get('held','NONE')},sim_w={float(rn.get('sim_w',0.0)):.2f},"
                        f"rsi={float(rn.get('rsi',0.0)):.1f},avg_e={float(rn.get('avg_entry',0.0)):.2f},"
                        f"why={rn.get('why','NA')}"
                    )
        except Exception as e:
            if self.live_mode:
                self.log(f"DYN_ALLOC_D0_ERR,{self.time.date()},{type(e).__name__},{e}")