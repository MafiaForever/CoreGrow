# panic_score.py — Continuous Panic Score Module
# CoreGrowth v3.73
#
# Replaces binary panic_mode_active with a continuous panic_score ∈ [0,1]
# and a 5-state machine: NORMAL → WATCH → STRESS → PANIC → RECOVERY.
#
# First test: applies only to overlay SPY de-risking via panic_mult.
# Does NOT change: tactical block, core sleeve, SH, emergency stop.

import numpy as np
from datetime import date, datetime
from typing import TYPE_CHECKING
from AlgorithmImports import *

_PS_NORMAL   = "NORMAL"
_PS_WATCH    = "WATCH"
_PS_STRESS   = "STRESS"
_PS_PANIC    = "PANIC"
_PS_RECOVERY = "RECOVERY"


class PanicScoreLogic:

    if TYPE_CHECKING:
        time:                datetime
        live_mode:           bool
        portfolio:           object
        securities:          object
        sym_spy:             object
        spy_ema_75:          object
        spy_ema_9:           object
        spy_ema_120:         object
        trend_ma:            object
        panic_mode_active:   bool
        panic_trigger_pct:   float
        stress_spy_cap:      float
        short_shock_flag:    bool
        debug_regime:        bool

        def history(self, symbol, periods, resolution): ...
        def log(self, msg: str): ...
        def GetVixPercentile(self): ...
        def RecentEquityShock(self) -> dict: ...
        def DetectShortShockState(self) -> dict: ...
        def _GetSpyAtrNorm(self, lookback: int = 14) -> dict: ...

    # ─────────────────────────────────────────────
    # Initialization
    # ─────────────────────────────────────────────
        # QC Optimizer
        testFloat:               float
        testInt:                 int

    def PanicScoreInitialize(self):
        """Call from main.py Initialize() after indicators are set up."""
        self._panic_score       = 0.0
        self._panic_state       = _PS_NORMAL
        self._panic_prev_state  = _PS_NORMAL
        self._panic_last_stress_date = None  # for RECOVERY detection

        # Smoothing
        self._ps_smooth_alpha   = 0.35  # weight of new raw vs previous score     #self.testFloat #

        # Component weights
        self._ps_w_shock   = 0.35
        self._ps_w_drop    = 0.25
        self._ps_w_trend   = 0.20
        self._ps_w_vol     = 0.10
        self._ps_w_persist = 0.10

        # State thresholds (up)
        self._ps_thr_watch  = 0.25
        self._ps_thr_stress = 0.45
        self._ps_thr_panic  = 0.70

        # Hysteresis thresholds (down)
        self._ps_hyst_from_watch  = 0.22  #
        self._ps_hyst_from_stress = 0.35
        self._ps_hyst_from_panic  = 0.60

        # Recovery
        self._ps_recovery_max_days = 10

        # Response multipliers (SPY de-risking only)
        self._ps_mult_normal   = 1.00
        self._ps_mult_watch    = 0.85  
        self._ps_mult_stress   = 0.65
        self._ps_mult_recovery = 0.75
        
        # PANIC uses stress_spy_cap directly
        self.log("[PS_INIT] PanicScore module initialized")

    # ─────────────────────────────────────────────
    # Main update — call from DAILYCycle
    # ─────────────────────────────────────────────

    def UpdatePanicScore(self):
        """Compute panic_raw, smooth into panic_score, update panic_state."""
        raw = self._CalcPanicRaw()
        prev = self._panic_score

        # Smooth
        score = (1.0 - self._ps_smooth_alpha) * prev + self._ps_smooth_alpha * raw

        # Floor for extreme events
        eq_shock = self.RecentEquityShock()
        sh_shock = self.DetectShortShockState()
        eq_mode = eq_shock.get("mode")
        sh_mode = sh_shock.get("mode")

        try:
            hist = self.history(self.sym_spy, 12, Resolution.DAILY)
            if not hist.empty and "close" in hist.columns and len(hist) >= 10:
                closes = hist["close"].to_numpy(dtype=float)
                p0_10 = float(closes[-10]) if len(closes) >= 10 else float(closes[0])
                p1 = float(closes[-1])
                drop_10d = (p0_10 - p1) / p0_10 if p0_10 > 0 else 0.0
            else:
                drop_10d = 0.0
        except Exception:
            drop_10d = 0.0

        extreme = (
            eq_mode == "B5D"           # [BUGFIX] was "BLEED_5D"
            or sh_mode == "B3D"        # [BUGFIX] was "BLEED_3D"
            or drop_10d >= 1.2 * self.panic_trigger_pct)
        if extreme:
            score = max(score, 0.85)

        score = float(max(0.0, min(1.0, score)))
        self._panic_score = score

        # State transition with hysteresis
        self._UpdatePanicState(score)

        # Diag log
        if self.live_mode or self.debug_regime:
            self.log(
                f"PANIC_DIAG,{self.time.date()},{raw:.3f},{score:.3f},"
                f"{self._panic_state}")

    # ─────────────────────────────────────────────
    # Raw score calculation
    # ─────────────────────────────────────────────

    def _CalcPanicRaw(self) -> float:
        s_shock   = self._PS_Shock()
        s_drop    = self._PS_Drop()
        s_trend   = self._PS_Trend()
        s_vol     = self._PS_Vol()
        s_persist = self._PS_Persist()

        raw = (self._ps_w_shock   * s_shock
             + self._ps_w_drop    * s_drop
             + self._ps_w_trend   * s_trend
             + self._ps_w_vol     * s_vol
             + self._ps_w_persist * s_persist)

        return float(max(0.0, min(1.0, raw)))

    def _PS_Shock(self) -> float:
        eq = self.RecentEquityShock()
        sh = self.DetectShortShockState()

        _eq_map = {"S1D": 0.45, "S3D": 0.75, "B5D": 1.00}   # [BUGFIX] keys match RecentEquityShock() mode strings
        _sh_map = {"S1D": 0.40, "S2D": 0.65, "B3D": 0.90}   # [BUGFIX] keys match DetectShortShockState() mode strings

        s_eq = _eq_map.get(eq.get("mode"), 0.0) if eq.get("active") else 0.0
        s_sh = _sh_map.get(sh.get("mode"), 0.0) if sh.get("active") else 0.0
        return max(s_eq, s_sh)

    def _PS_Drop(self) -> float:
        try:
            hist = self.history(self.sym_spy, 12, Resolution.DAILY)
            if hist.empty or "close" not in hist.columns or len(hist) < 10:
                return 0.0
            closes = hist["close"].to_numpy(dtype=float)
            c1 = float(closes[-1])
            c4 = float(closes[-4]) if len(closes) >= 4 else c1
            c6 = float(closes[-6]) if len(closes) >= 6 else c1
            c10 = float(closes[-10]) if len(closes) >= 10 else float(closes[0])
            if min(c1, c4, c6, c10) <= 0:
                return 0.0

            drop_3d  = max(0.0, (c4 - c1) / c4)
            drop_5d  = max(0.0, (c6 - c1) / c6)
            drop_10d = max(0.0, (c10 - c1) / c10)

            atr = self._GetSpyAtrNorm()
            atr_prev = atr["atr_prev"]
            atr_base = atr["atr_base"]

            D3  = max(0.0, min(1.0, (drop_3d / atr_prev - 1.5) / 1.5))
            D5  = max(0.0, min(1.0, (drop_5d / atr_base - 1.5) / 2.0))
            D10 = max(0.0, min(1.0, (drop_10d / self.panic_trigger_pct - 0.6) / 0.8))

            return 0.40 * D3 + 0.35 * D5 + 0.25 * D10
        except Exception:
            return 0.0

    def _PS_Trend(self) -> float:
        try:
            price = float(self.securities[self.sym_spy].Price)
            s = 0.0
            if self.spy_ema_75.IsReady and price < float(self.spy_ema_75.Current.Value):
                s += 0.50
            if (self.spy_ema_9.IsReady and self.spy_ema_120.IsReady
                    and float(self.spy_ema_9.Current.Value) < float(self.spy_ema_120.Current.Value)):
                s += 0.30
            if self.trend_ma.IsReady and price < float(self.trend_ma.Current.Value):
                s += 0.20
            return min(1.0, s)
        except Exception:
            return 0.0

    def _PS_Vol(self) -> float:
        try:
            vix_pct = self.GetVixPercentile()
            if vix_pct is None or not np.isfinite(vix_pct):
                return 0.0
            return float(max(0.0, min(1.0, (vix_pct - 0.60) / 0.35)))
        except Exception:
            return 0.0

    def _PS_Persist(self) -> float:
        try:
            hist = self.history(self.sym_spy, 6, Resolution.DAILY)
            if hist.empty or "close" not in hist.columns or len(hist) < 5:
                return 0.0
            closes = hist["close"].to_numpy(dtype=float)
            # Red days in last 4 transitions
            red = sum(1 for i in range(-4, -1) if closes[i] > closes[i + 1])
            # Also check i=-4 to i=-3
            if len(closes) >= 5 and closes[-5] > closes[-4]:
                red += 1
            red_frac = min(red, 4) / 4.0

            # Low break: last close below min of previous 4 closes
            prev_min = float(np.min(closes[-5:-1]))
            low_break = 1.0 if float(closes[-1]) < prev_min else 0.0

            return min(1.0, 0.70 * red_frac + 0.30 * low_break)
        except Exception:
            return 0.0

    # ─────────────────────────────────────────────
    # State machine with hysteresis
    # ─────────────────────────────────────────────

    def _UpdatePanicState(self, score: float):
        prev = self._panic_state
        today = self.time.date()

        # Upward transitions
        if score >= self._ps_thr_panic:
            new = _PS_PANIC
        elif score >= self._ps_thr_stress:
            new = _PS_STRESS
        elif score >= self._ps_thr_watch:
            new = _PS_WATCH
        else:
            new = _PS_NORMAL

        # Hysteresis: resist downward transitions
        if prev == _PS_PANIC and score >= self._ps_hyst_from_panic:
            new = _PS_PANIC
        elif prev == _PS_STRESS and score >= self._ps_hyst_from_stress:
            new = max(new, _PS_STRESS, key=lambda s: [_PS_NORMAL, _PS_WATCH, _PS_STRESS, _PS_PANIC].index(s))
        elif prev == _PS_WATCH and score >= self._ps_hyst_from_watch:
            new = max(new, _PS_WATCH, key=lambda s: [_PS_NORMAL, _PS_WATCH, _PS_STRESS, _PS_PANIC].index(s))

        # Track last stress/panic date for recovery
        if new in (_PS_STRESS, _PS_PANIC):
            self._panic_last_stress_date = today

        # Recovery detection
        if (prev in (_PS_STRESS, _PS_PANIC, _PS_RECOVERY)
                and new in (_PS_NORMAL, _PS_WATCH)
                and self._panic_last_stress_date is not None
                and (today - self._panic_last_stress_date).days <= self._ps_recovery_max_days):
            new = _PS_RECOVERY

        # Exit recovery
        if prev == _PS_RECOVERY:
            if new in (_PS_STRESS, _PS_PANIC):
                pass  # escalate out of recovery
            elif (self._panic_last_stress_date is not None
                  and (today - self._panic_last_stress_date).days > self._ps_recovery_max_days):
                new = _PS_NORMAL  # recovery expired
            elif score < self._ps_hyst_from_watch:
                new = _PS_NORMAL  # fully normalized
            else:
                new = _PS_RECOVERY  # stay in recovery

        self._panic_prev_state = prev
        if new != prev:
            self.log(f"[PS_STATE] {prev} → {new} | score={score:.3f}")
        self._panic_state = new

    # ─────────────────────────────────────────────
    # Response layer — SPY multiplier
    # ─────────────────────────────────────────────

    def GetPanicMult(self) -> float:
        """
        Returns SPY de-risking multiplier based on panic_state.
        Called from BuildOverlayTargets after computing spy.
        """
        st = getattr(self, "_panic_state", _PS_NORMAL)
        if st == _PS_WATCH:
            return self._ps_mult_watch
        elif st == _PS_STRESS:
            return self._ps_mult_stress
        elif st == _PS_PANIC:
            return 0.0  # caller should use min(spy, stress_spy_cap)
        elif st == _PS_RECOVERY:
            return self._ps_mult_recovery
        return self._ps_mult_normal

    # ─────────────────────────────────────────────
    # Persistence helpers
    # ─────────────────────────────────────────────

    def PanicScoreSaveFields(self) -> dict:
        """Returns dict of fields to add to main state snapshot."""
        def _d(dt):
            return dt.isoformat() if dt is not None else None
        return {
            "panic_score":             self._panic_score,
            "panic_state":             self._panic_state,
            "panic_prev_state":        self._panic_prev_state,
            "panic_last_stress_date":  _d(self._panic_last_stress_date),}

    def PanicScoreLoadFields(self, state: dict):
        """Restore fields from main state snapshot."""
        from datetime import date as _date
        self._panic_score      = float(state.get("panic_score", 0.0))
        self._panic_state      = state.get("panic_state", _PS_NORMAL)
        self._panic_prev_state = state.get("panic_prev_state", _PS_NORMAL)
        _d = state.get("panic_last_stress_date")
        try:
            self._panic_last_stress_date = _date.fromisoformat(_d[:10]) if _d else None
        except Exception:
            self._panic_last_stress_date = None