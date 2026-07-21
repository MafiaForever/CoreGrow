# region imports
from AlgorithmImports import *
from datetime import date, datetime, timedelta
# endregion
# cg_regime_rebal_time_trade.py
# CG-REGIME-TIME-T1: defer ExecuteTargets to fixed/deferred slots.
# Signal/targets remain calculated at 09:45. This module reuses production
# ExecuteTargets. Emergency/reduce-only stay immediate. Live pending snapshots
# persist across restarts until the scheduled slot executes.

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
        for pref in (
            "CG_REGIME_TIME_TRADE",
            "CG_REGIME_TIME_PENDING",
            "CG_REGIME_TIME_EXEC",
            "[INIT] CG_REGIME_TIME_TRADE",
        ):
            if pref not in lp:
                lp.append(pref)
        # Drop obsolete shadow filter tokens if present from older deploys.
        lp = [p for p in lp if "CG_RT_SHADOW" not in str(p)]
        self.log_only_prefixes = lp
        self.log(
            f"[INIT] CG_REGIME_TIME_TRADE_T1,"
            f"enable={int(self.cg_regime_rebal_time_trade_enable)},"
            f"ron={ron},neutral={neu},roff={roff},"
            f"fixed={fixed},signal_time=09:45,targets_once=1,trade=1"
        )
        if not self.cg_regime_rebal_time_trade_enable:
            return
        # Unique late slots only; slot 15 is handled inside DAILYCycle.
        late_slots = sorted(
            {int(ron), int(neu), int(roff)} - {_SIGNAL_SLOT}
        )
        for minutes in late_slots:
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

    def _RtTradeSaveLive(self):
        try:
            if getattr(self, "live_mode", False) and hasattr(self, "_SaveState"):
                self._SaveState()
        except Exception:
            pass

    def _RtTradeSlotPassed(self, slot_minutes, now=None):
        """True if current clock is at/after after_market_open(slot_minutes)."""
        now = now or self.time
        slot_minutes = int(slot_minutes)
        try:
            hours = self.securities[self.sym_spy].exchange.hours
            day0 = datetime(now.year, now.month, now.day)
            open_dt = hours.get_next_market_open(day0 - timedelta(seconds=1), False)
            if open_dt.date() != now.date():
                open_dt = hours.get_next_market_open(day0, False)
            target = open_dt + timedelta(minutes=slot_minutes)
            return now >= target
        except Exception:
            # US equity fallback: 09:30 + slot minutes.
            mins = 9 * 60 + 30 + slot_minutes
            return (now.hour * 60 + now.minute) >= mins

    def _RtTradeResolveTargets(self, ticker_weights):
        ticker_to_sym = {}
        for sym in getattr(self, "active_symbols", set()) or []:
            try:
                ticker_to_sym[_rtt_tk(sym)] = sym
            except Exception:
                continue
        try:
            for sym in self.securities.keys():
                try:
                    ticker_to_sym.setdefault(_rtt_tk(sym), sym)
                except Exception:
                    continue
        except Exception:
            pass
        out = {}
        for tk, w in (ticker_weights or {}).items():
            sym = ticker_to_sym.get(str(tk))
            if sym is None:
                continue
            try:
                out[sym] = float(w)
            except Exception:
                continue
        return out

    def CgRegimeRebalTimeTradePersist(self, state: dict) -> None:
        if self._cg_rt_pending is None or self._cg_rt_pending_executed:
            state["cg_rt_pending"] = None
            return
        targets = {}
        for sym, w in (self._cg_rt_pending or {}).items():
            try:
                targets[_rtt_tk(sym)] = float(w)
            except Exception:
                continue
        if not targets:
            state["cg_rt_pending"] = None
            return
        pd = self._cg_rt_pending_date
        ts = self._cg_rt_pending_ts
        state["cg_rt_pending"] = {
            "targets": targets,
            "date": pd.isoformat() if pd is not None else None,
            "regime": self._cg_rt_pending_regime,
            "slot": int(self._cg_rt_pending_slot) if self._cg_rt_pending_slot is not None else None,
            "ts": ts.isoformat() if ts is not None else None,
            "reduce": bool(self._cg_rt_pending_reduce),
            "executed": bool(self._cg_rt_pending_executed),
        }

    def CgRegimeRebalTimeTradeRestore(self, state: dict) -> None:
        if not state:
            return
        if getattr(self, "IsWarmingUp", False) or getattr(self, "is_warming_up", False):
            return
        if not getattr(self, "cg_regime_rebal_time_trade_enable", False):
            return
        raw = state.get("cg_rt_pending")
        if not raw or not isinstance(raw, dict):
            return
        if bool(raw.get("executed")):
            return
        try:
            pd = date.fromisoformat(str(raw.get("date") or "")[:10])
        except Exception:
            return
        today = self.time.date()
        if pd != today:
            # Stale prior-day pending — discard silently (miss already counted or EOD).
            return
        try:
            slot = int(raw.get("slot"))
        except Exception:
            return
        if slot not in _ALLOWED_SLOTS or slot == _SIGNAL_SLOT:
            return
        fixed = int(getattr(self, "cg_rebal_time_fixed_minutes", -1))
        if fixed >= 0 and slot != fixed:
            return
        if self._RtTradeSlotPassed(slot):
            self.log(
                f"CG_REGIME_TIME_TRADE_MISSED,date={pd},"
                f"regime={raw.get('regime')},slot={slot},"
                f"reason=restart_after_slot"
            )
            self._cg_rt_n_miss += 1
            self._RtTradeClearPending()
            self._RtTradeSaveLive()
            return
        targets = self._RtTradeResolveTargets(raw.get("targets") or {})
        if not targets:
            return
        self._cg_rt_pending = targets
        self._cg_rt_pending_date = pd
        self._cg_rt_pending_regime = raw.get("regime")
        self._cg_rt_pending_slot = slot
        self._cg_rt_pending_executed = False
        self._cg_rt_pending_reduce = bool(raw.get("reduce"))
        ts_raw = raw.get("ts")
        try:
            self._cg_rt_pending_ts = (
                datetime.fromisoformat(str(ts_raw)) if ts_raw else self.time
            )
        except Exception:
            self._cg_rt_pending_ts = self.time
        self.log(
            f"CG_REGIME_TIME_PENDING,date={pd},regime={self._cg_rt_pending_regime},"
            f"slot={slot},target_count={len(targets)},"
            f"gross={_rtt_gross(targets):.4f},restored=1"
        )

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
            try:
                if getattr(self, "_sr_on", False):
                    self.CgShadowReplayCapture(
                        combined, regime, _SIGNAL_SLOT,
                        reduce_only=bool(reduce_only),
                        emergency=bool(force_immediate and not reduce_only),
                    )
            except Exception:
                pass
            return True
        slot, rg = self._RtTradeSlotForRegime(regime)
        self._cg_rt_n_cap += 1
        if slot == _SIGNAL_SLOT:
            self._cg_rt_n_imm += 1
            try:
                if getattr(self, "_sr_on", False):
                    self.CgShadowReplayCapture(combined, rg, slot, False, False)
            except Exception:
                pass
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
        # Suppress per-event pending logs when shadow/MAISR diag is active
        # to preserve QC log budget for final diagnostic lines.
        if (not getattr(self, "_sr_on", False)
                and (slot != _SIGNAL_SLOT or rg != prev_rg)):
            self.log(
                f"CG_REGIME_TIME_PENDING,date={self._cg_rt_pending_date},"
                f"regime={rg},slot={slot},"
                f"target_count={len(self._cg_rt_pending)},"
                f"gross={_rtt_gross(self._cg_rt_pending):.4f}"
            )
        self._cg_rt_last_regime_log = rg
        try:
            if getattr(self, "_sr_on", False):
                self.CgShadowReplayCapture(combined, rg, slot, False, False)
        except Exception:
            pass
        self._RtTradeSaveLive()
        # D0.6B P0: passive intended-target observe (copy gross only; no mutation).
        try:
            if bool(getattr(self, "cg_damage_duration_d06b_p0_enable", False)):
                obs = getattr(self, "_D06bP0ObserveIntended", None)
                if callable(obs):
                    obs(getattr(self, "time", None), self._cg_rt_pending)
        except Exception:
            pass
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
                self._RtTradeSaveLive()
                return
            captured = self._cg_rt_pending_ts
            delay = 0
            try:
                if captured is not None:
                    delay = int((self.time - captured).total_seconds() // 60)
            except Exception:
                delay = int(slot_minutes) - _SIGNAL_SLOT
            reduce_only = bool(self._cg_rt_pending_reduce)
            try:
                if getattr(self, "_sr_on", False):
                    self.CgShadowReplayExecutePending()
            except Exception:
                pass
            if reduce_only:
                self.ExecuteTargets(targets, reduce_only=True)
            else:
                self.ExecuteTargets(targets)
            self._cg_rt_pending_executed = True
            self._cg_rt_n_exe += 1
            if not getattr(self, "_sr_on", False):
                self.log(
                    f"CG_REGIME_TIME_EXEC,date={d},"
                    f"regime={self._cg_rt_pending_regime},slot={slot_minutes},"
                    f"captured={captured},executed={self.time},"
                    f"delay_minutes={delay},target_count={len(targets)}"
                )
            self._RtTradeClearPending()
            self._RtTradeSaveLive()
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
                self._RtTradeSaveLive()
                return
            if self._cg_rt_pending_executed:
                self._RtTradeClearPending()
                self._RtTradeSaveLive()
                return
            self.log(
                f"CG_REGIME_TIME_TRADE_MISSED,date={d},"
                f"regime={self._cg_rt_pending_regime},"
                f"slot={self._cg_rt_pending_slot},"
                f"reason=slot_not_executed"
            )
            self._cg_rt_n_miss += 1
            self._RtTradeClearPending()
            self._RtTradeSaveLive()
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
            self.CgShadowReplayEmitFinal()
        except Exception:
            pass
