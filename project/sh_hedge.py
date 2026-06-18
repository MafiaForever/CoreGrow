# sh_hedge.py — SH v2.0 Intraday Micro-Hedge + IDS Engine
# CoreGrowth v3.73
#
# v2.0: IDS (Intraday Stress Engine) replaces single session-gain trigger.
#   4-component sensor on SH minute bars → intraday stress latch (shared risk state).
#   Entry driven by latch state, not sess_gain threshold.
#   Hedge size is state-based (WATCH/STRESS/PANIC_SHORT).
#   Exit no longer blindly restores SPY snapshot; respects active latch cap.
#   cg_logic.py reads latch for overlay SPY cap and gross cap.
#
# Retained from v1.6:
#   SL/TP/TIME_STOP/EOD exits, state machine, persistence, SHOnOrderFill.

import numpy as np
import json
from datetime import date, datetime, timedelta
from collections import deque
from AlgorithmImports import *
from typing import TYPE_CHECKING

_SH_IDLE           = "IDLE"
_SH_ARMED          = "ARMED"
_SH_ENTRY_PENDING  = "ENTRY_PENDING"
_SH_HEDGED         = "HEDGED"
_SH_EXIT_PENDING   = "EXIT_PENDING"
_SH_DONE           = "DONE_FOR_DAY"

_SH_BUF_SIZE = 130


class SHHedgeLogic:

    if TYPE_CHECKING:
        sym_spy:                  object
        sym_sh:                   object
        current_regime:           str
        panic_mode_active:        bool
        emergency_stop_triggered: bool
        live_mode:                bool
        time:                     datetime
        is_warming_up:            bool
        portfolio:                object
        securities:               object
        min_weight_delta:         float
        sh_enabled:               bool
        sh_min_spy_exposure:      float
        sh_hedge_fraction:        float
        sh_hedge_fraction_cap:    float
        sh_session_gain_exit:     float
        sh_max_hold_minutes:      int
        sh_entry_confirm:         int
        sh_entry_cutoff_offset:   int
        _sh_state:                str
        _sh_session_date:         object
        _sh_diag_date:            object
        _sh_entry_time:           object
        _sh_exit_time:            object
        _sh_entry_reason:         object
        _sh_exit_reason:          object
        _sh_done_for_day:         bool
        _sh_hold_minutes:         int
        _sh_parent_spy_snap:      object
        _sh_hedge_size:           float
        _sh_entry_spy_price:      object
        _sh_pending_order:        bool
        _sh_entry_confirm_count:  int
        _sh_session_armed:        bool
        _sh_session_entered:      bool
        _sh_signals_blocked:      int
        _sh_bar_buf:              object
        _SH_STATE_KEY:            str
        overlay_shock_flag:       bool
        schedule:                 object
        date_rules:               object
        time_rules:               object
        object_store:             object

        # [IDS_V2] Intraday Stress Engine fields
        _ids_active:          bool
        _ids_state:           str
        _ids_score:           float
        _ids_peak_score:      float
        _ids_reason:          object
        _ids_set_time:        object
        _ids_last_update:     object
        _ids_release_after:   object
        _ids_diag_date:       object

        # [IDS_V2] Params (set in main.py Initialize)
        ids_enable:              bool
        ids_thr_watch:           float
        ids_thr_stress:          float
        ids_thr_panic_short:     float
        ids_watch_hold_minutes:  int
        ids_stress_hold_minutes: int
        ids_release_decay_alpha: float
        ids_watch_hedge_frac:    float
        ids_stress_hedge_frac:   float
        ids_panic_hedge_frac:    float
        ids_watch_spy_cap:       float
        ids_stress_spy_cap:      float
        ids_panic_spy_cap:       float
        ids_watch_gross_cap:     float
        ids_stress_gross_cap:    float
        ids_panic_gross_cap:     float

        # [SPY_BOOST]
        spy_boost_enable: bool; spy_boost_min_score: float; spy_boost_min_components: int
        spy_boost_add_watch: float; spy_boost_add_strong: float; spy_boost_max_add: float
        spy_boost_max_spy_w: float; spy_boost_margin_use_frac: float
        spy_boost_entry_cutoff_offset: int; spy_boost_stop_sh_reversal: float
        _spy_boost_active: bool; _spy_boost_pre_w: object; _spy_boost_entry_price: object
        _spy_boost_entry_sh_price: object; _spy_boost_score: float; _spy_boost_done_for_day: bool

        def CurrentDrawdown(self) -> float: ...
        def GetCurrentWeights(self) -> dict: ...
        def get_parameter(self, name: str) -> object: ...
        def log(self, msg: str): ...
        def set_holdings(self, targets): ...
        def add_equity(self, ticker, resolution): ...
        def history(self, symbol, periods, resolution): ...

    # ─────────────────────────────────────────────────────────────────────
    # Initialization
    # ─────────────────────────────────────────────────────────────────────

    def SHInitialize(self):
        """Call from main.py Initialize() after sym_spy, before active_symbols."""
        self.sh_enabled             = True
        self.sh_min_spy_exposure    = 0.75

        # SPY cut sizing (IDS latch drives fraction)
        self.sh_hedge_fraction      = 0.50
        self.sh_hedge_fraction_cap  = 0.90
        self.sh_entry_confirm       = 2
        self.sh_entry_cutoff_offset = 300

        # Exit: time-stop + gain-compress only (no SH SL/TP — we don't hold SH)
        self.sh_session_gain_exit = 0.003
        self.sh_max_hold_minutes  = 120

        self._sh_bar_buf = deque(maxlen=_SH_BUF_SIZE)

        self._sh_state               = _SH_IDLE
        self._sh_session_date        = None
        self._sh_diag_date           = None
        self._sh_entry_time          = None
        self._sh_exit_time           = None
        self._sh_entry_reason        = None
        self._sh_exit_reason         = None
        self._sh_done_for_day        = False
        self._sh_hold_minutes        = 0
        self._sh_parent_spy_snap     = None
        self._sh_hedge_size          = 0.0
        self._sh_entry_spy_price     = None
        self._sh_pending_order       = False
        self._sh_exit_restore_spy    = None
        self._sh_entry_confirm_count = 0
        self._sh_session_armed       = False
        self._sh_session_entered     = False
        self._sh_signals_blocked     = 0
        self._SH_STATE_KEY           = "cg_v38_sh_intraday_state"

        # [SPY_BOOST] RISK_OFF relief-rally capture — conservative first test
        self.spy_boost_enable              = False
        self.spy_boost_min_score           = float(self.get_parameter("spy_boost_min_score") or 0.75)
        self.spy_boost_min_components      = int(self.get_parameter("spy_boost_min_components") or 2)
        self.spy_boost_add_watch           = float(self.get_parameter("spy_boost_add_watch") or 0.03)
        self.spy_boost_add_strong          = float(self.get_parameter("spy_boost_add_strong") or 0.05)
        self.spy_boost_max_add             = float(self.get_parameter("spy_boost_max_add") or 0.06)
        self.spy_boost_max_spy_w           = float(self.get_parameter("spy_boost_max_spy_w") or 0.35)
        self.spy_boost_margin_use_frac     = float(self.get_parameter("spy_boost_margin_use_frac") or 0.25)
        self.spy_boost_entry_cutoff_offset = int(self.get_parameter("spy_boost_entry_cutoff_offset") or 240)
        self.spy_boost_stop_sh_reversal    = float(self.get_parameter("spy_boost_stop_sh_reversal") or 0.005)
        self._spy_boost_active        = False
        self._spy_boost_pre_w         = None
        self._spy_boost_entry_price   = None
        self._spy_boost_entry_sh_price = None  # SH price at boost entry — used for stop-loss
        self._spy_boost_score         = 0.0
        self._spy_boost_done_for_day  = False

        # [IDS_V2] Initialize latch state (params are in main.py Initialize)
        self._IDSInitialize()

        for _offset in range(20, 305, 5):
            self.schedule.on(
                self.date_rules.every_day(self.sym_spy),
                self.time_rules.after_market_open(self.sym_spy, _offset),
                self._SHEval)

        self.schedule.on(
            self.date_rules.every_day(self.sym_spy),
            self.time_rules.before_market_close(self.sym_spy, 15),
            self._SHEodFlatten)

        self.log(
            f"[SH_INIT] SPY_CUT_ONLY v2.0 | IDS engine | "
            f"WATCH≥{self.ids_thr_watch:.2f} STRESS≥{self.ids_thr_stress:.2f} "
            f"PANIC≥{self.ids_thr_panic_short:.2f} | "
            f"cut_frac W={self.ids_watch_hedge_frac:.0%} "
            f"S={self.ids_stress_hedge_frac:.0%} P={self.ids_panic_hedge_frac:.0%} | "
            f"exit: TIME_STOP={self.sh_max_hold_minutes}m GAIN_COMPRESS={self.sh_session_gain_exit:.3f}")

    # ─────────────────────────────────────────────────────────────────────
    # [IDS_V2] Intraday Stress Engine — Sensor
    # ─────────────────────────────────────────────────────────────────────

    def _IDSInitialize(self):
        """Reset IDS latch state. Params must be set in main.py before calling SHInitialize."""
        self._ids_active        = False
        self._ids_state         = "NORMAL"
        self._ids_score         = 0.0
        self._ids_peak_score    = 0.0
        self._ids_reason        = None
        self._ids_set_time      = None
        self._ids_last_update   = None
        self._ids_release_after = None
        self._ids_diag_date     = None

    def _SHGetPrevDailyClose(self):
        """Returns previous day's SH daily close price, or None."""
        try:
            hist = self.history(self.sym_sh, 3, Resolution.DAILY)
            if hist.empty or "close" not in hist.columns or len(hist) < 2:
                return None
            return float(hist["close"].iloc[-2])
        except Exception:
            return None

    def _SHCalcIntradayStressSignal(self) -> dict:
        """
        4-component IDS sensor on SH minute bars.
        All components are positive when SH is rising (SPY is falling).
        Returns dict: score, state, reason, diag.
        """
        arrays = self._SHGetTodayArrays()
        _fail = {"score": 0.0, "state": "NORMAL", "reason": "",
                 "active_components": [], "active_n": 0, "diag": "no_data"}
        if arrays is None:
            return _fail
        c = arrays["close"]
        n = len(c)
        if n < 6:
            return {**_fail, "diag": f"too_few_bars={n}"}

        open_px = float(c[0])
        last_px = float(c[-1])
        if open_px <= 0 or last_px <= 0:
            return _fail

        # Component 1: Gap vs previous daily close (SH up = SPY gapped down)
        prev_close = self._SHGetPrevDailyClose()
        gap = 0.0
        if prev_close and prev_close > 0:
            gap = max(0.0, (open_px / prev_close) - 1.0)

        # Component 2: Session move from today's open
        move_open = max(0.0, (last_px / open_px) - 1.0)

        # Component 3: Short-term acceleration (roc_15m or roc_5m if fewer bars)
        accel = 0.0
        if n >= 16 and float(c[-16]) > 0:
            accel = max(0.0, (last_px / float(c[-16])) - 1.0)
        elif n >= 6 and float(c[-6]) > 0:
            accel = max(0.0, (last_px / float(c[-6])) - 1.0)

        # Component 4: Intraday vol burst vs median TR
        tr = self._SHGetIntradayTRSeries()
        vol_burst = 0.0
        if tr is not None and len(tr) >= 10:
            cur_tr = float(tr[-1])
            med_tr = float(np.median(tr[-10:]))
            if med_tr > 0:
                vol_burst = max(0.0, min(1.0, (cur_tr / med_tr - 1.0) / 2.0))

        # Normalize to [0,1]; active_n filter provides the real entry gate
        s_gap   = max(0.0, min(1.0, gap       / 0.008))
        s_open  = max(0.0, min(1.0, move_open / 0.007))
        s_accel = max(0.0, min(1.0, accel     / 0.010))
        s_vol   = vol_burst

        score = 0.30*s_gap + 0.30*s_open + 0.25*s_accel + 0.15*s_vol
        score = float(max(0.0, min(1.0, score)))

        state = self._SHScoreToState(score)

        active = []
        if s_gap   >= 0.30: active.append("gap")
        if s_open  >= 0.30: active.append("open")
        if s_accel >= 0.30: active.append("accel")
        if s_vol   >= 0.30: active.append("vol")
        reason = "+".join(active)

        return {
            "score":             score,
            "state":             state,
            "reason":            reason,
            "active_components": active,
            "active_n":          len(active),
            "diag":              (f"gap={gap:.3%}|open={move_open:.3%}|"
                                  f"accel={accel:.3%}|vb={vol_burst:.2f}|n={n}")}

    def _SHScoreToState(self, score: float) -> str:
        thr_p = getattr(self, "ids_thr_panic_short", 0.75)
        thr_s = getattr(self, "ids_thr_stress",      0.50)
        thr_w = getattr(self, "ids_thr_watch",        0.25)
        if score >= thr_p: return "PANIC_SHORT"
        if score >= thr_s: return "STRESS"
        if score >= thr_w: return "WATCH"
        return "NORMAL"

    # ─────────────────────────────────────────────────────────────────────
    # [IDS_V2] Intraday Stress Engine — Latch
    # ─────────────────────────────────────────────────────────────────────

    def _IDSUpdate(self):
        """
        Called at the top of every _SHEval (every 5 min, 09:50–14:30).
        Updates the intraday stress latch based on current sensor reading.
        """
        if not getattr(self, "ids_enable", True):
            return
        try:
            sig = self._SHCalcIntradayStressSignal()
        except Exception as e:
            self.log(f"[IDS_ERR] sensor failed: {e}")
            return

        sig_state = str(sig["score"] and sig["state"] or "NORMAL")
        sig_state = sig["state"]
        sig_score = float(sig["score"])

        if sig_state == "NORMAL":
            self._IDSDecayOrRelease()
        else:
            self._IDSArmLatch(sig_state, sig_score, sig.get("reason", ""))

        # One-shot daily log on first non-NORMAL reading
        today = self.time.date()
        if (self._ids_active
                and getattr(self, "_ids_diag_date", None) != today):
            self._ids_diag_date = today
            if not getattr(self,"log_quiet_mode",False):  # [LOG-BUDGET]
                self.log(
                    f"[IDS_DIAG] {today} {self.time.strftime('%H:%M')} "
                    f"state={self._ids_state} score={self._ids_score:.3f} "
                    f"peak={self._ids_peak_score:.3f} | {sig['diag']}")

    def _IDSArmLatch(self, state: str, score: float, reason: str):
        """Arm or update the intraday stress latch."""
        was_active = self._ids_active
        self._ids_active     = True
        self._ids_score      = score
        self._ids_peak_score = max(self._ids_peak_score, score)
        self._ids_reason     = reason
        self._ids_last_update = self.time

        if not was_active:
            self._ids_set_time = self.time

        # Escalate state, never downgrade via ArmLatch (decay handles downgrade)
        _order = ["NORMAL", "WATCH", "STRESS", "PANIC_SHORT"]
        cur_rank = _order.index(self._ids_state) if self._ids_state in _order else 0
        new_rank = _order.index(state) if state in _order else 0
        if new_rank > cur_rank:
            self._ids_state = state

        # Extend hold window (never shorten)
        hold_min = (self.ids_stress_hold_minutes
                    if state in ("STRESS", "PANIC_SHORT")
                    else self.ids_watch_hold_minutes)
        new_release = self.time + timedelta(minutes=hold_min)
        if self._ids_release_after is None or new_release > self._ids_release_after:
            self._ids_release_after = new_release

    def _IDSDecayOrRelease(self):
        """Decay score when signal is NORMAL; release latch when expired and decayed."""
        if not self._ids_active:
            return
        old_state = self._ids_state                          # [BUGFIX] capture before decay overwrites
        alpha = getattr(self, "ids_release_decay_alpha", 0.20)
        self._ids_score = max(0.0, self._ids_score * (1.0 - alpha))
        # Re-evaluate state from decayed score
        self._ids_state = self._SHScoreToState(self._ids_score)
        # Release once past hold window AND score below WATCH threshold
        thr_w = getattr(self, "ids_thr_watch", 0.25)
        past_hold = (self._ids_release_after is not None
                     and self.time >= self._ids_release_after)
        if past_hold and self._ids_score < thr_w:
            self._ids_active        = False
            self._ids_state         = "NORMAL"
            self._ids_score         = 0.0
            self._ids_peak_score    = 0.0
            self._ids_reason        = None
            self._ids_set_time      = None
            self._ids_last_update   = None
            self._ids_release_after = None
            self.log(f"[IDS_RELEASE] Latch released on {self.time.date()} "
                     f"(was {old_state})")

    def _IDSActiveForRisk(self) -> bool:
        return bool(getattr(self, "_ids_active", False))

    def _IDSGetDesiredHedgeFraction(self) -> float:
        """Hedge size as fraction of current SPY weight, driven by latch state."""
        st = getattr(self, "_ids_state", "NORMAL")
        if st == "PANIC_SHORT": return getattr(self, "ids_panic_hedge_frac",  0.60)
        if st == "STRESS":      return getattr(self, "ids_stress_hedge_frac", 0.40)
        if st == "WATCH":       return getattr(self, "ids_watch_hedge_frac",  0.20)
        return 0.0

    def _IDSGetOverlayCaps(self) -> dict:
        """
        Returns SPY and gross caps for cg_logic.py to apply.

        PANIC_SHORT spy_cap is regime-dependent:
        - RISK_ON   -> tighter cap
        - NEUTRAL   -> softer cap
        - RISK_OFF  -> tight again (higher cap is mostly useless there)

        Add these params in Initialize() only if you want to override defaults:
            self.ids_panic_spy_cap_risk_on  = 0.30
            self.ids_panic_spy_cap_neutral  = 0.35
            self.ids_panic_spy_cap_risk_off = 0.15
        """
        st = getattr(self, "_ids_state", "NORMAL")

        if not getattr(self, "_ids_active", False) or st == "NORMAL":
            return {"spy_cap": 9.9, "gross_cap": 9.9}

        if st == "WATCH":
            return {
                "spy_cap":   float(getattr(self, "ids_watch_spy_cap",   0.75)),
                "gross_cap": float(getattr(self, "ids_watch_gross_cap", 1.40)),}

        if st == "STRESS":
            return {
                "spy_cap":   float(getattr(self, "ids_stress_spy_cap",   0.50)),
                "gross_cap": float(getattr(self, "ids_stress_gross_cap", 1.20)),}

        # PANIC_SHORT
        regime = str(getattr(self, "current_regime", "NEUTRAL"))
        if regime == "RISK_ON":
            panic_spy_cap = float(getattr(self, "ids_panic_spy_cap_risk_on", 0.30))
        elif regime == "NEUTRAL":
            panic_spy_cap = float(getattr(self, "ids_panic_spy_cap_neutral", 0.35))
        else:  # RISK_OFF or unknown
            panic_spy_cap = float(getattr(self, "ids_panic_spy_cap_risk_off", 0.15))

        return {
            "spy_cap":   self._IDSApplyScoreAdj(panic_spy_cap),  # [IDS_DYN]
            "gross_cap": float(getattr(self, "ids_panic_gross_cap", 1.00)),}

    def _IDSApplyScoreAdj(self, base_cap: float) -> float:
        """[IDS_DYN] Tighten PANIC_SHORT spy_cap further under compounding stress signals.
        Takes regime-based base_cap as anchor (already set by _IDSGetOverlayCaps).
        Adjustments (additive, independent):
          -0.10  if IDS score >= 0.90  (extreme intraday sensor pressure)
          -0.05  if panic_score >= 0.65 (macro tail signal from panic_score.py)
        Floor: 0.10 (never cut below sensible minimum).
        Thresholds calibrated for base caps RISK_ON=0.30, NEUTRAL=0.35, RISK_OFF=0.15.
        """
        adj = 0.0
        if getattr(self, "_ids_score", 0.0) >= 0.75:
            adj -= 0.05
        if getattr(self, "_panic_score", 0.0) >= 0.65:
            adj -= 0.05
        result = max(0.10, base_cap + adj)
        if adj != 0.0:
            self.log(
                f"[IDS_DYN] panic_spy_cap {base_cap:.2f} -> {result:.2f} "
                f"(ids_score={getattr(self, '_ids_score', 0.0):.3f} "
                f"panic_score={getattr(self, '_panic_score', 0.0):.3f})")
        return result

    def _ApplyIntradayStressSpyCap(self, spy: float) -> float:
        """Cap overlay SPY. Called from cg_logic.BuildOverlayTargets via setattr injection."""
        if not getattr(self, "_ids_active", False):
            return spy
        return min(spy, float(self._IDSGetOverlayCaps()["spy_cap"]))

    def _ApplyIntradayStressGrossCap(self, gross_mult: float) -> float:
        """Cap gross multiplier. Called from cg_logic.MergeSleeves via setattr injection."""
        if not getattr(self, "_ids_active", False):
            return gross_mult
        return min(gross_mult, float(self._IDSGetOverlayCaps()["gross_cap"]))

    def _IDSSaveFields(self) -> dict:
        """Returns IDS latch fields to merge into main ObjectStore snapshot."""
        def _dt(t): return t.isoformat() if t is not None else None
        return {
            "ids_active":        self._ids_active,
            "ids_state":         self._ids_state,
            "ids_score":         self._ids_score,
            "ids_peak_score":    self._ids_peak_score,
            "ids_reason":        self._ids_reason,
            "ids_set_time":      _dt(self._ids_set_time),
            "ids_last_update":   _dt(self._ids_last_update),
            "ids_release_after": _dt(self._ids_release_after),}

    def _IDSLoadFields(self, state: dict):
        """Restore IDS latch from main ObjectStore snapshot."""
        def _pdt(s):
            try: return datetime.fromisoformat(s) if s else None
            except: return None
        self._ids_active        = bool(state.get("ids_active", False))
        self._ids_state         = str(state.get("ids_state", "NORMAL"))
        self._ids_score         = float(state.get("ids_score", 0.0))
        self._ids_peak_score    = float(state.get("ids_peak_score", 0.0))
        self._ids_reason        = state.get("ids_reason")
        self._ids_set_time      = _pdt(state.get("ids_set_time"))
        self._ids_last_update   = _pdt(state.get("ids_last_update"))
        self._ids_release_after = _pdt(state.get("ids_release_after"))

    # ─────────────────────────────────────────────────────────────────────
    # [IDS_V2] Execution helpers
    # ─────────────────────────────────────────────────────────────────────

    def _SHEntryGateV2(self) -> bool:
        """
        V2 entry gate: driven by IDS latch, not by regime.
        Regime restriction removed — if intraday stress is real, hedge it.
        """
        if getattr(self, "emergency_stop_triggered", False):
            return False
        if not getattr(self, "_ids_active", False):
            return False
        if getattr(self, "_ids_state", "NORMAL") not in ("WATCH", "STRESS", "PANIC_SHORT"):
            return False
        if self._sh_pending_order:
            return False
        try:
            spy_w = float(self.GetCurrentWeights().get(self.sym_spy, 0.0))
        except Exception:
            return False
        return spy_w >= self.sh_min_spy_exposure

    def _SHGetAllowedSpyAfterExit(self) -> float:
        """
        [IDS_V2] SPY weight to restore after SH exit.
        If latch still active, caps restore to avoid blind re-risk.
        If latch released (stress passed), restores to pre-hedge snapshot.
        """
        pre = float(self._sh_parent_spy_snap) if self._sh_parent_spy_snap is not None else 0.0
        if not getattr(self, "_ids_active", False):
            return pre  # stress gone — restore fully
        caps = self._IDSGetOverlayCaps()
        return min(pre, float(caps["spy_cap"]))

    # ─────────────────────────────────────────────────────────────────────
    # Persistence
    # ─────────────────────────────────────────────────────────────────────

    def SHSaveState(self):
        if not self.live_mode:
            return
        try:
            def _d(dt): return dt.isoformat() if dt is not None else None
            self.object_store.save(self._SH_STATE_KEY, json.dumps({
                "sh_state":           self._sh_state,
                "sh_session_date":    _d(self._sh_session_date),
                "sh_entry_time":      _d(self._sh_entry_time),
                "sh_exit_time":       _d(self._sh_exit_time),
                "sh_entry_reason":    self._sh_entry_reason,
                "sh_exit_reason":     self._sh_exit_reason,
                "sh_done_for_day":    self._sh_done_for_day,
                "sh_hold_minutes":    self._sh_hold_minutes,
                "sh_parent_spy_snap": self._sh_parent_spy_snap,
                "sh_hedge_size":      self._sh_hedge_size,
                "sh_entry_spy_price": self._sh_entry_spy_price,
                "sh_pending_order":   self._sh_pending_order,
                "boost_active":       self._spy_boost_active,
                "boost_pre_w":        self._spy_boost_pre_w,
                "boost_entry_price":  self._spy_boost_entry_price,
                "boost_entry_sh_px":  self._spy_boost_entry_sh_price,
                "boost_score":        self._spy_boost_score,
                "boost_done":         self._spy_boost_done_for_day,
                # [LSS1] session continuity fields
                "sh_session_armed":      self._sh_session_armed,
                "sh_session_entered":    self._sh_session_entered,
                "sh_signals_blocked":    self._sh_signals_blocked,
                "sh_entry_confirm_count":self._sh_entry_confirm_count,
                "sh_exit_restore_spy":   self._sh_exit_restore_spy,
            }))
        except Exception as e:
            self.log(f"[SH_STATE] Save failed: {e}")

    def SHLoadState(self):
        if not self.live_mode:
            return
        try:
            if not self.object_store.contains_key(self._SH_STATE_KEY):
                return
            state = json.loads(self.object_store.read(self._SH_STATE_KEY))
            def _pd(s):
                try: return date.fromisoformat(s[:10]) if s else None
                except: return None
            def _pdt(s):
                try: return datetime.fromisoformat(s) if s else None
                except: return None
            if _pd(state.get("sh_session_date")) == self.time.date():
                self._sh_state           = state.get("sh_state", _SH_IDLE)
                self._sh_session_date    = _pd(state.get("sh_session_date"))
                self._sh_entry_time      = _pdt(state.get("sh_entry_time"))
                self._sh_exit_time       = _pdt(state.get("sh_exit_time"))
                self._sh_entry_reason    = state.get("sh_entry_reason")
                self._sh_exit_reason     = state.get("sh_exit_reason")
                self._sh_done_for_day    = bool(state.get("sh_done_for_day", False))
                self._sh_hold_minutes    = int(state.get("sh_hold_minutes", 0))
                self._sh_parent_spy_snap = state.get("sh_parent_spy_snap")
                self._sh_hedge_size      = float(state.get("sh_hedge_size", 0.0))
                self._sh_entry_spy_price = state.get("sh_entry_spy_price")
                self._sh_pending_order   = bool(state.get("sh_pending_order", False))
                # [SPY_BOOST] restore boost state across restarts
                self._spy_boost_active         = bool(state.get("boost_active", False))
                self._spy_boost_pre_w          = state.get("boost_pre_w")
                self._spy_boost_entry_price    = state.get("boost_entry_price")
                self._spy_boost_entry_sh_price = state.get("boost_entry_sh_px")
                self._spy_boost_score          = float(state.get("boost_score", 0.0))
                self._spy_boost_done_for_day   = bool(state.get("boost_done", False))
                # [LSS1] session continuity restore
                self._sh_session_armed       = bool(state.get("sh_session_armed", False))
                self._sh_session_entered     = bool(state.get("sh_session_entered", False))
                self._sh_signals_blocked     = int(state.get("sh_signals_blocked", 0))
                self._sh_entry_confirm_count = int(state.get("sh_entry_confirm_count", 0))
                self._sh_exit_restore_spy    = state.get("sh_exit_restore_spy")
                if self._sh_state == _SH_HEDGED:
                    self.log(
                        f"[SH_RESTORE] SPY cut active | size={self._sh_hedge_size:.3f} "
                        f"snap={self._sh_parent_spy_snap}")
                elif self._sh_state in (_SH_ENTRY_PENDING, _SH_EXIT_PENDING):
                    sh_h   = self.portfolio.get(self.sym_sh)
                    has_sh = sh_h is not None and sh_h.invested
                    try:
                        cur_weights = self.GetCurrentWeights()
                        spy_w = float(cur_weights.get(self.sym_spy, 0.0))
                        sh_w  = float(cur_weights.get(self.sym_sh,  0.0))
                    except Exception:
                        spy_w = sh_w = 0.0
                    if self._sh_state == _SH_ENTRY_PENDING:
                        if has_sh and sh_w > 0.005:
                            self._sh_state = _SH_HEDGED
                            self._sh_pending_order = False
                            self.log(f"[SH_RESTORE] ENTRY_PENDING → HEDGED (sh_w={sh_w:.3f})")
                        else:
                            self._sh_state = _SH_IDLE
                            self._sh_pending_order = False
                            self.log(f"[SH_RESTORE] ENTRY_PENDING → IDLE (spy_w={spy_w:.3f})")
                    else:
                        if not has_sh:
                            reason = self._sh_exit_reason or "RESTART"
                            self._SHFinalizeExit(reason)
                            self.log(f"[SH_RESTORE] EXIT_PENDING → DONE (SH flat)")
                        else:
                            self._sh_state = _SH_HEDGED
                            self._sh_pending_order = False
                            self.log(f"[SH_RESTORE] EXIT_PENDING → HEDGED (SH open sh_w={sh_w:.3f})")
            else:
                self._sh_state        = _SH_IDLE
                self._sh_done_for_day = False
        except Exception as e:
            self.log(f"[SH_STATE] Load failed: {e}")

    # ─────────────────────────────────────────────────────────────────────
    # OnData feed
    # ─────────────────────────────────────────────────────────────────────

    def SHOnData(self, data):
        try:
            if not data.Bars.ContainsKey(self.sym_sh):
                return
            bar = data.Bars[self.sym_sh]
            if bar is None:
                return
            self._sh_bar_buf.append((
                bar.EndTime,
                float(bar.Open),
                float(bar.High),
                float(bar.Low),
                float(bar.Close),
                float(bar.Volume) if bar.Volume > 0 else 1.0
            ))
            if not getattr(self, "_sh_bar_confirmed", False):
                self._sh_bar_confirmed = True
                self.log(f"[SH_DATA] First SH bar at {self.time} | buf={len(self._sh_bar_buf)}")
        except Exception as e:
            if not getattr(self, "_sh_data_err_logged", False):
                self._sh_data_err_logged = True
                self.log(f"[SH_ONDATA_ERR] {e}")

    # ─────────────────────────────────────────────────────────────────────
    # Main eval callback (every 5 min, 09:50–14:30)
    # ─────────────────────────────────────────────────────────────────────

    def _SHEval(self):
        if self.is_warming_up:
            return
        if not getattr(self, "sh_enabled", True):
            return

        today = self.time.date()
        if self._sh_session_date != today:
            self._SHSessionReset(today)

        # [IDS_V2] Update sensor + latch first — always, even if already HEDGED/DONE
        self._IDSUpdate()

        # [SPY_BOOST] stop-loss check runs always (boost may be active from earlier eval)
        try:
            self._SPYBoostCheckStop()
        except Exception as e:
            self.log(f"[SPY_BOOST_STOP_ERR] {e}")

        if self._sh_state == _SH_DONE:
            return

        # Waiting for fill confirmation (live mode)
        if self._sh_state in (_SH_ENTRY_PENDING, _SH_EXIT_PENDING):
            return

        mao     = self._SHMinutesAfterOpen(self.time)
        tod_c, tod_v = self._SHFetchTodayBars()

        if self._sh_state == _SH_HEDGED:
            self._SHEvalExit(tod_c)
            return

        # Past cutoff
        if mao > self.sh_entry_cutoff_offset:
            if self._sh_state in (_SH_ARMED, _SH_IDLE):
                if not self._sh_session_entered:
                    self._SHTransition(_SH_DONE, "cutoff_no_entry")
                else:
                    self._SHTransition(_SH_IDLE, "past_cutoff")
                self._sh_entry_confirm_count = 0
            return

        # [SPY_BOOST] entry eval — after DONE/PENDING/HEDGED checks so gate is consistent
        try:
            self._SPYBoostEval()
        except Exception as e:
            self.log(f"[SPY_BOOST_ERR] {e}")

        if getattr(self, "emergency_stop_triggered", False):
            self._sh_entry_confirm_count = 0
            return

        # [IDS_V2] Gate from latch
        gate_ok  = self._SHEntryGateV2()
        ids_state = getattr(self, "_ids_state", "NORMAL")

        # Manage IDLE ↔ ARMED via latch gate
        if self._sh_state == _SH_IDLE:
            if gate_ok:
                self._SHTransition(_SH_ARMED, f"ids_{ids_state}")
                self._sh_session_armed = True
        elif self._sh_state == _SH_ARMED:
            if not gate_ok:
                self._SHTransition(_SH_IDLE, "ids_gate_lost")
                self._sh_entry_confirm_count = 0
                return

        if not gate_ok:
            self._sh_entry_confirm_count = 0
            return

        if self._sh_pending_order or tod_c is None or len(tod_c) < 2:
            return

        # One-time session diag on first armed eval
        if self._sh_diag_date != today:
            self._sh_diag_date = today
            try:
                spy_w = float(self.GetCurrentWeights().get(self.sym_spy, 0.0))
            except Exception:
                spy_w = -1.0
            if not getattr(self,"log_quiet_mode",False):  # [LOG-BUDGET]
                self.log(
                    f"[SH_DIAG] {today} t={self.time.strftime('%H:%M')} "
                    f"ids_state={ids_state} ids_score={self._ids_score:.3f} "
                    f"spy_w={spy_w:.2f} bars={len(tod_c)}")

        # [IDS_V2] Re-read sensor payload for active-components gate
        sig      = self._SHCalcIntradayStressSignal()
        active_n = int(sig.get("active_n", 0))
        min_n    = int(getattr(self, "ids_min_components_entry", 2))
        stress_conf = int(getattr(self, "ids_stress_entry_confirm", 2))

        if ids_state == "WATCH":
            # WATCH arms latch/caps only — no SH entry   # [LOG_GATE]
            if self.live_mode or getattr(self, "debug_regime", False):
                self.log(f"[SH_FILTER] WATCH suppressed | score={self._ids_score:.3f}")
            if self._sh_entry_confirm_count > 0:
                self._sh_signals_blocked += 1
            self._sh_entry_confirm_count = 0
            return

        if ids_state == "STRESS":
            if self.live_mode or getattr(self, "debug_regime", False):   # [LOG_GATE]
                self.log(
                    f"[SH_FILTER] ids=STRESS score={self._ids_score:.3f} "
                    f"active_n={active_n} min_n={min_n}")
            if active_n < min_n:
                if self._sh_entry_confirm_count > 0:
                    self._sh_signals_blocked += 1
                self._sh_entry_confirm_count = 0
                return
            self._sh_entry_confirm_count += 1
            if self.live_mode or getattr(self, "debug_regime", False):   # [LOG_GATE]
                self.log(
                    f"[SH_ARM] STRESS confirm {self._sh_entry_confirm_count}/{stress_conf} "
                    f"| score={self._ids_score:.3f} active_n={active_n}")
            if self._sh_entry_confirm_count >= stress_conf:
                self._SHExecuteEntryV2(
                    f"ids_STRESS_x{self._sh_entry_confirm_count}_"
                    f"score={self._ids_score:.3f}_n={active_n}")
            return

        if ids_state == "PANIC_SHORT":
            self._sh_entry_confirm_count = 0
            if self.live_mode or getattr(self, "debug_regime", False):   # [LOG_GATE]
                self.log(
                    f"[SH_FILTER] ids=PANIC_SHORT score={self._ids_score:.3f} "
                    f"active_n={active_n} min_n={min_n}")
            if active_n >= min_n:
                self._SHExecuteEntryV2(
                    f"ids_PANIC_SHORT_score={self._ids_score:.3f}_n={active_n}")
            else:
                self._sh_signals_blocked += 1
            return

        if self._sh_entry_confirm_count > 0:
            self._sh_signals_blocked += 1
        self._sh_entry_confirm_count = 0

    def _SHSessionReset(self, new_date):
        if self._sh_state in (_SH_HEDGED, _SH_ENTRY_PENDING):
            self.log(f"[SH_STATE] Stale hedge/pending on {new_date} — exit")
            self._sh_pending_order = False
            self._SHExecuteExit("SESSION_STALE")
        elif self._sh_state == _SH_EXIT_PENDING:
            self.log(f"[SH_STATE] Stale exit-pending on {new_date} — force cleanup")
            self._SHFinalizeExit("SESSION_STALE")
        if self._sh_session_date is not None:
            self._SHEmitDailySummary()
        self._sh_done_for_day        = False
        self._sh_hold_minutes        = 0
        self._sh_entry_confirm_count = 0
        self._sh_pending_order       = False
        self._sh_session_armed       = False
        self._sh_session_entered     = False
        self._sh_signals_blocked     = 0
        self._sh_session_date        = new_date
        self._sh_state               = _SH_IDLE
        # [SPY_BOOST] daily reset
        self._spy_boost_done_for_day = False
        self._spy_boost_active       = False

    # ─────────────────────────────────────────────────────────────────────
    # Buffer
    # ─────────────────────────────────────────────────────────────────────

    def _SHFetchTodayBars(self):
        if not self._sh_bar_buf:
            return None, None
        today    = self.time.date()
        tod_bars = [b for b in self._sh_bar_buf if b[0].date() == today]
        if not tod_bars:
            return None, None
        return (np.array([b[4] for b in tod_bars], dtype=float),
                np.array([b[5] for b in tod_bars], dtype=float))

    def _SHGetTodayArrays(self):
        if not self._sh_bar_buf:
            return None
        today    = self.time.date()
        tod_bars = [b for b in self._sh_bar_buf if b[0].date() == today]
        if not tod_bars or len(tod_bars) < 2:
            return None
        return {
            "time":   [b[0] for b in tod_bars],
            "open":   np.array([b[1] for b in tod_bars], dtype=float),
            "high":   np.array([b[2] for b in tod_bars], dtype=float),
            "low":    np.array([b[3] for b in tod_bars], dtype=float),
            "close":  np.array([b[4] for b in tod_bars], dtype=float),
            "volume": np.array([b[5] for b in tod_bars], dtype=float),}

    def _SHGetIntradayTRSeries(self):
        arrays = self._SHGetTodayArrays()
        if arrays is None:
            return None
        h = arrays["high"]
        l = arrays["low"]
        c = arrays["close"]
        n = len(h)
        if n < 2:
            return None
        tr = np.empty(n - 1)
        for i in range(1, n):
            tr[i - 1] = max(h[i] - l[i],
                            abs(h[i] - c[i - 1]),
                            abs(l[i] - c[i - 1]))
        return tr

    # ─────────────────────────────────────────────────────────────────────
    # Signal (kept for diagnostics; no longer primary gate)
    # ─────────────────────────────────────────────────────────────────────

    def _SHGetSessionGain(self, tod_c):
        """SH return from session open. Positive = SPY declining. Diagnostic use only in v2."""
        if tod_c is None or len(tod_c) < 2:
            return None
        p0 = float(tod_c[0])
        return ((float(tod_c[-1]) / p0) - 1.0) if p0 > 0 else None

    # ─────────────────────────────────────────────────────────────────────
    # Exit evaluation — SL/TP checked every 5 min
    # ─────────────────────────────────────────────────────────────────────

    def _SHEvalExit(self, tod_c):
        if self._sh_entry_time is not None:
            held = (self.time - self._sh_entry_time).total_seconds() / 60.0
            self._sh_hold_minutes = int(held)
            if held >= self.sh_max_hold_minutes:
                self._SHExecuteExit("TIME_STOP")
                return

        if tod_c is not None and len(tod_c) >= 2:
            sess_gain = self._SHGetSessionGain(tod_c)
            if sess_gain is not None and sess_gain < self.sh_session_gain_exit:
                self._SHExecuteExit("GAIN_COMPRESS")
                return

        if getattr(self, "emergency_stop_triggered", False):
            self._SHExecuteExit("PARENT_EMERGENCY")
            return

        if self.live_mode:
            self.SHSaveState()

    # ─────────────────────────────────────────────────────────────────────
    # Helpers
    # ─────────────────────────────────────────────────────────────────────

    def _SHTransition(self, new_state, reason):
        old = self._sh_state
        self._sh_state = new_state
        if old != new_state:
            self.log(f"[SH_STATE] {old} → {new_state} | {reason}")
        if self.live_mode:
            self.SHSaveState()

    def _SHMinutesAfterOpen(self, t):
        return (t - t.replace(hour=9, minute=30, second=0, microsecond=0)).total_seconds() / 60.0

    # ─────────────────────────────────────────────────────────────────────
    # Execution
    # ─────────────────────────────────────────────────────────────────────

    def _SHExecuteEntryV2(self, trigger_diag):
        """[IDS_V2] Entry with state-based hedge sizing from IDS latch."""
        try:
            cur   = self.GetCurrentWeights()
            spy_w = float(cur.get(self.sym_spy, 0.0))
            if spy_w < self.sh_min_spy_exposure or self._sh_pending_order:
                return

            frac  = self._IDSGetDesiredHedgeFraction()
            if frac <= 0:
                return
            hedge = round(min(spy_w * frac, spy_w * self.sh_hedge_fraction_cap), 4)
            if hedge < getattr(self, "min_weight_delta", 0.01):
                return

            self._sh_parent_spy_snap     = spy_w
            self._sh_hedge_size          = hedge
            self._sh_entry_time          = self.time
            self._sh_entry_spy_price     = float(self.securities[self.sym_spy].Price)
            self._sh_entry_reason        = trigger_diag
            self._sh_hold_minutes        = 0
            self._sh_pending_order       = True

            self._SHTransition(_SH_ENTRY_PENDING, "entry_submitting_v2")
            self.set_holdings([
                PortfolioTarget(self.sym_spy, spy_w - hedge),
                PortfolioTarget(self.sym_sh,  0.0),
            ])
            self._sh_session_entered = True

            if not self.live_mode:
                self._sh_pending_order = False
                self._SHTransition(_SH_HEDGED, "entry_filled_sync")

            self.log(
                f"[SPY_CUT] ids={self._ids_state} frac={frac:.0%} "
                f"spy={spy_w:.3f}→{spy_w - hedge:.3f} cut={hedge:.3f} | {trigger_diag}")
            try:                                   # [RR_SPYCUT] intraday RR safety exit
                _rr = getattr(self, "_rr", None)
                if _rr is not None:
                    _rr.rr_spycut_safety_exit(str(getattr(self, "_ids_state", "NORMAL")))
            except Exception as _e:
                self.log(f"[RR_SPYCUT_EXIT_ERR] {_e}")
        except Exception as e:
            self._sh_pending_order = False
            self._SHTransition(_SH_ARMED, "entry_failed_v2")
            self.log(f"[SPY_CUT] FAILED: {e}")

    def _SHExecuteExit(self, reason):
        try:
            cur         = self.GetCurrentWeights()
            spy_w       = float(cur.get(self.sym_spy, 0.0))
            restore_spy = self._SHGetAllowedSpyAfterExit()
            self._sh_exit_restore_spy = restore_spy

            hold_min = spy_move = 0
            if self._sh_entry_time is not None:
                hold_min = int((self.time - self._sh_entry_time).total_seconds() / 60.0)
            if self._sh_entry_spy_price and self._sh_entry_spy_price > 0:
                spy_move = float(self.securities[self.sym_spy].Price) / self._sh_entry_spy_price - 1.0

            self._sh_exit_time    = self.time
            self._sh_exit_reason  = reason
            self._sh_hold_minutes = hold_min

            self._sh_pending_order = True
            self._SHTransition(_SH_EXIT_PENDING, f"exit_submitting_{reason}")
            self.set_holdings([
                PortfolioTarget(self.sym_spy, restore_spy),
                PortfolioTarget(self.sym_sh,  0.0),
            ])

            pre_snap  = float(self._sh_parent_spy_snap) if self._sh_parent_spy_snap is not None else 0.0
            ids_cap   = getattr(self, "_ids_state", "NORMAL")
            spy_delta = restore_spy - spy_w
            self.log(
                f"[SPY_RESTORE] reason={reason} hold={hold_min}m "
                f"spy_move={spy_move:.2%} snap={pre_snap:.3f} "
                f"restored={restore_spy:.3f} delta={spy_delta:+.3f} ids={ids_cap}")

            if not self.live_mode:
                self._SHFinalizeExit(reason)

        except Exception as e:
            self._sh_pending_order = False
            self._SHTransition(_SH_HEDGED, f"exit_failed_{reason}")
            self.log(f"[SPY_RESTORE] FAILED reason={reason}: {e}")

    def _SHFinalizeExit(self, reason):
        self._sh_pending_order       = False
        self._sh_parent_spy_snap     = None
        self._sh_hedge_size          = 0.0
        self._sh_entry_spy_price     = None
        self._sh_entry_confirm_count = 0
        self._sh_done_for_day        = True
        self._sh_exit_restore_spy    = None
        self._SHTransition(_SH_DONE, f"exit_{reason}")

    # ──────────────────────────────────────────────────────────────
    # [SPY_BOOST] SH-down mirror signal + intraday boost layer
    # ──────────────────────────────────────────────────────────────

    def _SHCalcIntradaySpyBoostSignal(self) -> dict:
        arrays = self._SHGetTodayArrays()
        _fail = {"score": 0.0, "state": "NORMAL", "reason": "",
                 "active_components": [], "active_n": 0, "diag": "no_data"}
        if arrays is None:
            return _fail
        c = arrays["close"]
        n = len(c)
        if n < 6:
            return {**_fail, "diag": f"too_few_bars={n}"}
        open_px = float(c[0])
        last_px = float(c[-1])
        if open_px <= 0 or last_px <= 0:
            return _fail

        prev_close = self._SHGetPrevDailyClose()
        gap_down = 0.0
        if prev_close and prev_close > 0:
            gap_down = max(0.0, 1.0 - open_px / prev_close)

        move_down = max(0.0, 1.0 - last_px / open_px)

        accel_down = 0.0
        if n >= 16 and float(c[-16]) > 0:
            accel_down = max(0.0, 1.0 - last_px / float(c[-16]))
        elif n >= 6 and float(c[-6]) > 0:
            accel_down = max(0.0, 1.0 - last_px / float(c[-6]))

        tr = self._SHGetIntradayTRSeries()
        vol_burst = 0.0
        if tr is not None and len(tr) >= 10:
            cur_tr = float(tr[-1])
            med_tr = float(np.median(tr[-10:]))
            if med_tr > 0:
                vol_burst = max(0.0, min(1.0, (cur_tr / med_tr - 1.0) / 2.0))

        s_gap   = max(0.0, min(1.0, gap_down   / 0.006))
        s_open  = max(0.0, min(1.0, move_down  / 0.006))
        s_accel = max(0.0, min(1.0, accel_down / 0.008))
        s_vol   = vol_burst

        score = float(max(0.0, min(1.0,
            0.25 * s_gap + 0.35 * s_open + 0.25 * s_accel + 0.15 * s_vol)))

        active = []
        if s_gap   >= 0.30: active.append("gap_down")
        if s_open  >= 0.30: active.append("open_down")
        if s_accel >= 0.30: active.append("accel_down")
        if s_vol   >= 0.30: active.append("vol")

        state = "BOOST_STRONG" if score >= 0.75 else ("BOOST_WATCH" if score >= 0.60 else "NORMAL")

        return {
            "score":             score,
            "state":             state,
            "reason":            "+".join(active),
            "active_components": active,
            "active_n":          len(active),
            "diag":              (f"gap_down={gap_down:.3%}|open_down={move_down:.3%}|"
                                  f"accel_down={accel_down:.3%}|vb={vol_burst:.2f}|n={n}")}

    def _SPYBoostGate(self) -> bool:
        if not getattr(self, "spy_boost_enable", False):
            return False
        if getattr(self, "emergency_stop_triggered", False):
            return False
        if getattr(self, "_spy_boost_done_for_day", False):
            return False
        if getattr(self, "_spy_boost_active", False):
            return False
        # SH layer done for day → no boost either
        if getattr(self, "_sh_state", "IDLE") in (_SH_DONE, _SH_HEDGED, _SH_ENTRY_PENDING, _SH_EXIT_PENDING):
            return False
        # IDS active = SPY already being cut — don't fight it
        if getattr(self, "_ids_active", False):
            return False
        if getattr(self, "short_shock_flag", False):
            return False
        # Block only full binary panic; allow WATCH/STRESS/RECOVERY
        if getattr(self, "panic_mode_active", False):
            return False
        if str(getattr(self, "_panic_state", "NORMAL")) == "PANIC":
            return False
        # Hypothesis A: RISK_OFF regime only
        if str(getattr(self, "current_regime", "UNKNOWN")) != "RISK_OFF":
            return False
        mao = self._SHMinutesAfterOpen(self.time)
        if mao > int(getattr(self, "spy_boost_entry_cutoff_offset", 240)):
            return False
        return True

    def _SPYBoostClose(self, reason):
        """Unified close: stop-loss or EOD. Respects IDS cap on restore."""
        pre_w = self._spy_boost_pre_w
        if pre_w is None:
            pre_w = float(self.GetCurrentWeights().get(self.sym_spy, 0.0))
        # [IDS cap] don't restore above what current latch allows
        if getattr(self, "_ids_active", False):
            ids_spy_cap = float(self._IDSGetOverlayCaps().get("spy_cap", 9.9))
            pre_w = min(float(pre_w), ids_spy_cap)
        try:
            cur_spy  = float(self.GetCurrentWeights().get(self.sym_spy, 0.0))
            entry_px = float(self._spy_boost_entry_price or 0.0)
            cur_px   = float(self.securities[self.sym_spy].Price)
            move     = (cur_px / entry_px - 1.0) if entry_px > 0 else 0.0
        except Exception:
            cur_spy = move = 0.0
        self.set_holdings([PortfolioTarget(self.sym_spy, float(pre_w))])
        self.log(
            f"[SPY_BOOST_CLOSE] {reason} {self.time.date()} "
            f"spy={cur_spy:.3f}->{float(pre_w):.3f} "
            f"move={move:.2%} score={self._spy_boost_score:.3f}")
        self._spy_boost_active        = False
        self._spy_boost_pre_w         = None
        self._spy_boost_entry_price   = None
        self._spy_boost_entry_sh_price = None
        self._spy_boost_score         = 0.0
        self._spy_boost_done_for_day  = True

    def _SPYBoostCheckStop(self):
        """Intraday stop-loss: if SH rose ≥ stop_sh_reversal from entry → close boost."""
        if not getattr(self, "_spy_boost_active", False):
            return
        entry_sh = getattr(self, "_spy_boost_entry_sh_price", None)
        if not entry_sh or entry_sh <= 0:
            return
        try:
            cur_sh   = float(self.securities[self.sym_sh].Price)
            reversal = (cur_sh / entry_sh) - 1.0
            if reversal >= float(getattr(self, "spy_boost_stop_sh_reversal", 0.005)):
                self._SPYBoostClose("SH_REVERSAL")
        except Exception:
            pass

    def _SPYBoostEval(self):
        if not self._SPYBoostGate():
            return
        sig      = self._SHCalcIntradaySpyBoostSignal()
        score    = float(sig.get("score", 0.0))
        active_n = int(sig.get("active_n", 0))
        if score    < float(getattr(self, "spy_boost_min_score",      0.75)):
            return
        if active_n < int(getattr(self,   "spy_boost_min_components", 2)):
            return

        cur     = self.GetCurrentWeights()
        cur_spy = float(cur.get(self.sym_spy, 0.0))
        max_spy = float(getattr(self, "spy_boost_max_spy_w", 0.35))
        room    = max(0.0, max_spy - cur_spy)
        if room <= 0:
            return

        desired_add = (float(getattr(self, "spy_boost_add_strong", 0.05))
                       if score >= 0.75
                       else float(getattr(self, "spy_boost_add_watch", 0.03)))
        desired_add = min(desired_add, float(getattr(self, "spy_boost_max_add", 0.06)))

        total_value = float(self.portfolio.total_portfolio_value)
        if total_value <= 0:
            return
        try:
            margin_remaining = float(self.portfolio.margin_remaining)
        except Exception:
            margin_remaining = 0.0
        bp_weight_cap  = max(0.0, 2.0 * margin_remaining / total_value)
        bp_weight_cap *= float(getattr(self, "spy_boost_margin_use_frac", 0.25))

        add_w = min(desired_add, room, bp_weight_cap)
        if add_w < float(getattr(self, "min_weight_delta", 0.02)):
            return

        target_spy = cur_spy + add_w
        self._spy_boost_active         = True
        self._spy_boost_pre_w          = cur_spy
        self._spy_boost_entry_price    = float(self.securities[self.sym_spy].Price)
        self._spy_boost_entry_sh_price = float(self.securities[self.sym_sh].Price)
        self._spy_boost_score          = score

        self.set_holdings([PortfolioTarget(self.sym_spy, target_spy)])
        self.log(
            f"[SPY_BOOST_ENTER] {self.time.date()} {self.time.strftime('%H:%M')} "
            f"score={score:.3f} n={active_n} add={add_w:.3f} "
            f"spy={cur_spy:.3f}->{target_spy:.3f} bp_cap={bp_weight_cap:.3f}")

    def _SPYBoostEodRestore(self):
        if getattr(self, "_spy_boost_active", False):
            self._SPYBoostClose("EOD")

    # ─────────────────────────────────────────────────────────────────────
    # EOD flatten + daily summary
    # ─────────────────────────────────────────────────────────────────────

    def _SHEodFlatten(self):
        if self.is_warming_up:
            return
        if self._sh_state in (_SH_HEDGED, _SH_ENTRY_PENDING):
            self.log("[SH_EOD] Forced flatten at 15:45")
            self._sh_pending_order = False
            self._SHExecuteExit("EOD")
        elif self._sh_state == _SH_EXIT_PENDING:
            self._SHFinalizeExit("EOD_PENDING_CLEANUP")
        else:
            try:
                sh_h = self.portfolio.get(self.sym_sh)
                if sh_h is not None and sh_h.invested:
                    self.set_holdings([PortfolioTarget(self.sym_sh, 0.0)])
                    self.log("[SH_EOD] Liquidated stale SH")
            except Exception:
                pass
            if not self._sh_done_for_day and self._sh_state != _SH_DONE:
                self._SHTransition(_SH_DONE, "eod_cutoff")
        self._SHEmitDailySummary()
        try:
            self._SPYBoostEodRestore()
        except Exception as e:
            self.log(f"[SPY_BOOST_EOD_ERR] {e}")
        self.SHSaveState()

    def _SHEmitDailySummary(self):
        if getattr(self,"log_quiet_mode",False): return  # [LOG-BUDGET]
        restore = getattr(self, "_sh_exit_restore_spy", None)
        snap    = getattr(self, "_sh_parent_spy_snap",  None)
        spy_delta_tag = (f"{restore - snap:+.3f}"
                         if restore is not None and snap is not None else "n/a")
        self.log(
            f"SH_DAY,{self._sh_session_date},"
            f"armed={int(self._sh_session_armed)},"
            f"entered={int(self._sh_session_entered)},"
            f"hedge={self._sh_hedge_size:.3f},"
            f"hold_min={self._sh_hold_minutes},"
            f"exit={self._sh_exit_reason or 'NONE'},"
            f"spy_delta={spy_delta_tag},"
            f"blocked={self._sh_signals_blocked},"
            f"ids_state={self._ids_state},"
            f"ids_peak={self._ids_peak_score:.3f},"
            f"ids_active={int(self._ids_active)}")

    # ─────────────────────────────────────────────────────────────────────
    # Live fill confirmation
    # ─────────────────────────────────────────────────────────────────────

    def SHOnOrderFill(self, symbol):
        if not self.live_mode:
            return
        if self._sh_state == _SH_ENTRY_PENDING:
            if symbol == self.sym_spy:
                self._sh_pending_order = False
                self._SHTransition(_SH_HEDGED, "entry_fill_confirmed")
                self.log(f"[SH_FILL] Entry confirmed | trigger={symbol}")
        elif self._sh_state == _SH_EXIT_PENDING:
            if symbol == self.sym_spy:
                reason = self._sh_exit_reason or "UNKNOWN"
                self._SHFinalizeExit(reason)
                self.log(f"[SH_FILL] Exit confirmed | trigger={symbol}")