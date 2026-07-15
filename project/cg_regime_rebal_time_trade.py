# region imports
from AlgorithmImports import *
# endregion
# cg_regime_rebal_time_trade.py
# CG-REGIME-TIME-T1: defer ExecuteTargets to regime-dependent slots.
# Signal/targets remain calculated at 09:45. Diagnostics do not place orders here;
# this module reuses production ExecuteTargets. Emergency/reduce-only stay immediate.

_ALLOWED_SLOTS = frozenset((15, 45, 75, 105, 135, 165, 195, 225, 255, 285, 315, 345, 375))
_SIGNAL_SLOT = 15  # 09:45 ET


def _rtt_tk(sym):
    try:
        return str(sym.Value)
    except Exception:
        try:
            return str(sym.value)
        except Exception:
            return str(sym)


def _rtt_gross(w):
    g = 0.0
    for v in (w or {}).values():
        try:
            g += abs(float(v or 0.0))
        except Exception:
            continue
    return g


class CgRegimeRebalTimeTradeMixin:
    """Real deferred rebalance execution. No signal recalculation at later slots."""

    def CgRegimeRebalTimeTradeInitialize(self):
        ov = getattr(self, "_rrx_param_overrides", {}) or {}

        def _p(k, d=""):
            v = self.get_parameter(k)
            if v is None or str(v).strip() == "":
                v = ov.get(k, d)
            return v

        def _bool(k, d="0"):
            return str(_p(k, d) or d).strip().lower() in ("1", "true", "yes", "on")

        def _int(k, d):
            raw = _p(k, str(d))
            try:
                return int(str(raw).strip())
            except Exception:
                raise Exception(f"CG_REGIME_TIME_TRADE_T1 invalid int param {k}={raw}")

        self.cg_regime_rebal_time_trade_enable = _bool(
            "cg_rt_trade",
            "0",
        )
        fixed = _int(
            "cg_rt_fixed",
            -1,
        )
        ron = _int(
            "cg_rt_ron",
            15,
        )
        neu = _int(
            "cg_rt_neu",
            15,
        )
        roff = _int(
            "cg_rt_roff",
            15,
        )
        if fixed != -1:
            if fixed not in _ALLOWED_SLOTS:
                raise Exception(
                    f"CG_REGIME_TIME_TRADE_T1 invalid cg_rt_fixed={fixed}"
                )
            ron = neu = roff = fixed
        for name, val in (
            ("cg_rt_ron", ron),
            ("cg_rt_neu", neu),
            ("cg_rt_roff", roff),
        ):
            if val not in _ALLOWED_SLOTS:
                raise Exception(f"CG_REGIME_TIME_TRADE_T1 invalid {name}={val}")
        self.cg_rebal_time_risk_on_minutes = ron
        self.cg_rebal_time_neutral_minutes = neu
        self.cg_rebal_time_risk_off_minutes = roff
        self.cg_rebal_time_fixed_minutes = fixed
        self._cg_rt_pending = None
        self._cg_rt_pending_date = None
        self._cg_rt_pending_regime = None
        self._cg_rt_pending_slot = None
        self._cg_rt_pending_executed = False
        self._cg_rt_pending_reduce = False
        self._cg_rt_pending_ts = None
        self._cg_rt_n_cap = 0
        self._cg_rt_n_imm = 0
        self._cg_rt_n_def = 0
        self._cg_rt_n_exe = 0
        self._cg_rt_n_miss = 0
        self._cg_rt_n_dup = 0
        self._cg_rt_n_unk = 0
        self._cg_rt_last_regime_log = None
        self._cg_rt_trade_emitted = False
        lp = list(getattr(self, "log_only_prefixes", None) or [])
        for pref in ("CG_REGIME_TIME_TRADE", "CG_REGIME_TIME_PENDING", "CG_REGIME_TIME_EXEC", "[INIT] CG_REGIME_TIME_TRADE"):
            if pref not in lp:
                lp.append(pref)
        self.log_only_prefixes = lp
        self.log(
            f"[INIT] CG_REGIME_TIME_TRADE_T1,"
            f"enable={int(self.cg_regime_rebal_time_trade_enable)},"
            f"ron={ron},neutral={neu},roff={roff},"
            f"fixed={fixed},signal_time=09:45,targets_once=1,trade=1"
        )
        if not self.cg_regime_rebal_time_trade_enable:
            return
        # Later slots only; slot 15 is handled inside DAILYCycle.
        for minutes in sorted(_ALLOWED_SLOTS):
            if minutes == _SIGNAL_SLOT:
                continue
            self.schedule.on(
                self.date_rules.every_day(self.sym_spy),
                self.time_rules.after_market_open(self.sym_spy, minutes),
                lambda m=minutes: self.CgRegimeRebalTimeTradeExecuteSlot(m),
            )
        self.schedule.on(
            self.date_rules.every_day(self.sym_spy),
            self.time_rules.before_market_close(self.sym_spy, 2),
            self.CgRegimeRebalTimeTradeEndOfDay,
        )

    def _RtTradeSlotForRegime(self, regime):
        rg = str(regime or "").strip().upper()
        if rg == "RISK_ON":
            return self.cg_rebal_time_risk_on_minutes, rg
        if rg == "RISK_OFF":
            return self.cg_rebal_time_risk_off_minutes, rg
        if rg != "NEUTRAL":
            self._cg_rt_n_unk += 1
            rg = "NEUTRAL"
        return self.cg_rebal_time_neutral_minutes, rg

    def CgRegimeRebalTimeTradeCapture(self, combined, regime, reduce_only=False, force_immediate=False) -> bool:
        """Return True => execute now; False => deferred (do not ExecuteTargets)."""
        if not getattr(self, "cg_regime_rebal_time_trade_enable", False):
            return True
        if getattr(self, "IsWarmingUp", False) or getattr(self, "is_warming_up", False):
            return True
        if not isinstance(combined, dict):
            return True
        if force_immediate or reduce_only:
            self._cg_rt_n_cap += 1
            self._cg_rt_n_imm += 1
            return True
        slot, rg = self._RtTradeSlotForRegime(regime)
        self._cg_rt_n_cap += 1
        if slot == _SIGNAL_SLOT:
            self._cg_rt_n_imm += 1
            return True
        # Defer
        self._cg_rt_pending = dict(combined)
        self._cg_rt_pending_date = self.time.date()
        self._cg_rt_pending_regime = rg
        self._cg_rt_pending_slot = int(slot)
        self._cg_rt_pending_executed = False
        self._cg_rt_pending_reduce = False
        self._cg_rt_pending_ts = self.time
        self._cg_rt_n_def += 1
        prev_rg = getattr(self, "_cg_rt_last_regime_log", None)
        if slot != _SIGNAL_SLOT or rg != prev_rg:
            self.log(
                f"CG_REGIME_TIME_PENDING,date={self._cg_rt_pending_date},"
                f"regime={rg},slot={slot},"
                f"target_count={len(self._cg_rt_pending)},"
                f"gross={_rtt_gross(self._cg_rt_pending):.4f}"
            )
            self._cg_rt_last_regime_log = rg
        return False

    def CgRegimeRebalTimeTradeMaybeRun(self, combined, reduce_only=False, force_immediate=False) -> None:
        """Capture + ExecuteTargets when immediate. Production execution pathway only."""
        do_now = self.CgRegimeRebalTimeTradeCapture(
            combined, getattr(self, "current_regime", None),
            reduce_only=reduce_only, force_immediate=force_immediate,
        )
        if not do_now:
            return
        if reduce_only:
            self.ExecuteTargets(combined, reduce_only=True)
        else:
            self.ExecuteTargets(combined)

    def CgRegimeRebalTimeTradeExecuteSlot(self, slot_minutes: int) -> None:
        if not getattr(self, "cg_regime_rebal_time_trade_enable", False):
            return
        try:
            if getattr(self, "IsWarmingUp", False) or getattr(self, "is_warming_up", False):
                return
            if self._cg_rt_pending is None:
                return
            d = self.time.date()
            if self._cg_rt_pending_date != d:
                return
            if int(self._cg_rt_pending_slot) != int(slot_minutes):
                return
            if self._cg_rt_pending_executed:
                self._cg_rt_n_dup += 1
                return
            # Market must be open
            try:
                sec = self.securities[self.sym_spy]
                if not sec.exchange.hours.is_open(self.time, False):
                    return
            except Exception:
                pass
            targets = self._cg_rt_pending
            if not isinstance(targets, dict) or not targets:
                self.log(
                    f"CG_REGIME_TIME_TRADE_MISSED,date={d},"
                    f"regime={self._cg_rt_pending_regime},slot={slot_minutes},"
                    f"reason=invalid_pending_targets"
                )
                self._cg_rt_n_miss += 1
                self._RtTradeClearPending()
                return
            captured = self._cg_rt_pending_ts
            delay = 0
            try:
                if captured is not None:
                    delay = int((self.time - captured).total_seconds() // 60)
            except Exception:
                delay = int(slot_minutes) - _SIGNAL_SLOT
            reduce_only = bool(self._cg_rt_pending_reduce)
            if reduce_only:
                self.ExecuteTargets(targets, reduce_only=True)
            else:
                self.ExecuteTargets(targets)
            self._cg_rt_pending_executed = True
            self._cg_rt_n_exe += 1
            self.log(
                f"CG_REGIME_TIME_EXEC,date={d},"
                f"regime={self._cg_rt_pending_regime},slot={slot_minutes},"
                f"captured={captured},executed={self.time},"
                f"delay_minutes={delay},target_count={len(targets)}"
            )
            self._RtTradeClearPending()
        except Exception as exc:
            try:
                self.log(
                    f"CG_REGIME_TIME_TRADE_MISSED,date={self.time.date()},"
                    f"regime={getattr(self,'_cg_rt_pending_regime',None)},"
                    f"slot={slot_minutes},reason=exec_error_{type(exc).__name__}"
                )
            except Exception:
                pass
            self._cg_rt_n_miss += 1
            # Do not mark executed; EndOfDay will clear stale.

    def _RtTradeClearPending(self):
        self._cg_rt_pending = None
        self._cg_rt_pending_date = None
        self._cg_rt_pending_regime = None
        self._cg_rt_pending_slot = None
        self._cg_rt_pending_executed = False
        self._cg_rt_pending_reduce = False
        self._cg_rt_pending_ts = None

    def CgRegimeRebalTimeTradeEndOfDay(self) -> None:
        if not getattr(self, "cg_regime_rebal_time_trade_enable", False):
            return
        try:
            if self._cg_rt_pending is None:
                return
            d = self._cg_rt_pending_date
            if d is not None and d != self.time.date():
                # Already stale from prior day — clear without double miss if already logged.
                self._RtTradeClearPending()
                return
            if self._cg_rt_pending_executed:
                self._RtTradeClearPending()
                return
            self.log(
                f"CG_REGIME_TIME_TRADE_MISSED,date={d},"
                f"regime={self._cg_rt_pending_regime},"
                f"slot={self._cg_rt_pending_slot},"
                f"reason=slot_not_executed"
            )
            self._cg_rt_n_miss += 1
            self._RtTradeClearPending()
        except Exception:
            try:
                self._RtTradeClearPending()
            except Exception:
                pass

    def CgRegimeRebalTimeTradeEmitFinal(self) -> None:
        if getattr(self, "_cg_rt_trade_emitted", False):
            return
        self._cg_rt_trade_emitted = True
        try:
            if not getattr(self, "cg_regime_rebal_time_trade_enable", False):
                try:
                    self.log(
                        "CG_REGIME_TIME_TRADE_FINAL,ron=15,neutral=15,roff=15,"
                        "captured=0,immediate=0,deferred=0,executed=0,missed=0,"
                        "duplicate_blocked=0,unknown_regime=0,trade=0,enable=0"
                    )
                except Exception:
                    pass
            else:
                if self._cg_rt_pending is not None and not self._cg_rt_pending_executed:
                    self._cg_rt_n_miss += 1
                    self._RtTradeClearPending()
                self.log(
                    f"CG_REGIME_TIME_TRADE_FINAL,"
                    f"ron={self.cg_rebal_time_risk_on_minutes},"
                    f"neutral={self.cg_rebal_time_neutral_minutes},"
                    f"roff={self.cg_rebal_time_risk_off_minutes},"
                    f"captured={self._cg_rt_n_cap},"
                    f"immediate={self._cg_rt_n_imm},"
                    f"deferred={self._cg_rt_n_def},"
                    f"executed={self._cg_rt_n_exe},"
                    f"missed={self._cg_rt_n_miss},"
                    f"duplicate_blocked={self._cg_rt_n_dup},"
                    f"unknown_regime={self._cg_rt_n_unk},trade=1"
                )
        except Exception:
            pass
        try:
            self.CgRegimeTimeShadowS1EmitFinal()
        except Exception:
            pass
