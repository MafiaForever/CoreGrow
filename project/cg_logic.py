import numpy as np
from datetime import timedelta, date, datetime
from AlgorithmImports import *
from cg_risk_tactical import CoreGrowthRiskTacticalMixin

class CoreGrowthLogic(CoreGrowthRiskTacticalMixin):
    def DetectRegime(self, return_diag=False):
        today = self.time.date()
        if self.panic_mode_active:
            return ("RISK_OFF", "panic") if return_diag else "RISK_OFF"
        yc_val = None
        vix_pct = None
        macro_ok = False
        try:
            hist_yc = self.history(self.yc, 30, Resolution.DAILY)
            if (not hist_yc.empty) and ("value" in hist_yc.columns):
                yc_val = float(hist_yc["value"].iloc[-1])
        except Exception:
            yc_val = None
        try:
            vix_pct = self.GetVixPercentile()
        except Exception:
            vix_pct = None
        macro_ok = (yc_val is not None) and (vix_pct is not None) and np.isfinite(vix_pct)
        if macro_ok:
            score = 0
            if vix_pct >= self.vix_high_pct: score -= 1
            elif vix_pct <= self.vix_low_pct: score += 1
            if yc_val <= -0.10: score -= 1
            elif yc_val >= 0.30: score += 1
            if score <= -1: candidate = "RISK_OFF"
            elif score >= 1: candidate = "RISK_ON"
            else: candidate = "NEUTRAL"
            diag = f"macro vix_pct={vix_pct:.3f} yc={yc_val:.3f} score={score}"
            # [H-REGIME-RE-RISK-VETO-1] Macro RISK_ON requires trend confirmation.
            # If trend_cand != RISK_ON, cap candidate to NEUTRAL.
            if (candidate == "RISK_ON"
                    and self.spy_ema_75.IsReady and self.spy_sma_200.IsReady):
                _px   = float(self.securities[self.sym_spy].price)
                _s200 = float(self.spy_sma_200.Current.Value)
                _s18  = float(self.spy_ema_9.Current.Value)
                _s100 = float(self.spy_ema_120.Current.Value)
                _s50  = float(self.spy_ema_75.Current.Value)
                if (_s18 > _s200) and (_s100 > _s200):
                    _trend = "RISK_ON"
                elif (_px < _s50) and (_s18 < _s100):
                    _trend = "RISK_OFF"
                else:
                    _trend = "NEUTRAL"
                if _trend != "RISK_ON":
                    candidate = "NEUTRAL"
                    diag += f" | rrveto1 trend={_trend}->NEUTRAL"
        else:
            if not (self.spy_ema_75.IsReady and self.spy_sma_200.IsReady):
                candidate = self.current_regime or "NEUTRAL"
                diag = "fallback_not_ready"
            else:
                price = float(self.securities[self.sym_spy].price)
                sma50 = float(self.spy_ema_75.Current.Value)
                sma200 = float(self.spy_sma_200.Current.Value)
                sma18 = float(self.spy_ema_9.Current.Value)
                sma100 = float(self.spy_ema_120.Current.Value)
                if (sma18 > sma200) and (sma100 > sma200):
                    candidate = "RISK_ON"
                elif (price < sma50) and (sma18 < sma100):
                    candidate = "RISK_OFF"
                else:
                    candidate = "NEUTRAL"
                diag = f"fallback spy={price:.2f} sma50={sma50:.2f} sma200={sma200:.2f}"
        if self.current_regime is not None and self.regime_start_date is not None:
            days_in_regime = (today - self.regime_start_date).days
            if candidate != self.current_regime and days_in_regime < self.regime_min_persist_days:
                candidate = self.current_regime
                diag = diag + f" | persist_hold days={days_in_regime}"
        if self.debug_regime:
            if self._last_regime_diag_date is None or (today - self._last_regime_diag_date).days >= 7:
                self._last_regime_diag_date = today
                self.log(f"[REGIME_DIAG] {today} cur={self.current_regime} cand={candidate} | {diag}")
        if self.live_mode and (self._last_regime_diag_date is None or today != self._last_regime_diag_date):
            self._last_regime_diag_date = today
            _fb = not macro_ok and self.spy_ema_75.IsReady and self.spy_sma_200.IsReady
            _p = f"{float(self.securities[self.sym_spy].price):.4f}" if _fb else "NA"
            _s50 = f"{float(self.spy_ema_75.Current.Value):.4f}" if _fb else "NA"
            _s200 = f"{float(self.spy_sma_200.Current.Value):.4f}" if _fb else "NA"
            _s18 = f"{float(self.spy_ema_9.Current.Value):.4f}" if _fb else "NA"
            _s100 = f"{float(self.spy_ema_120.Current.Value):.4f}" if _fb else "NA"
            self.log(f"REGIME_FULL,{today},{self.current_regime},{candidate},"
                     f"{'NA' if yc_val is None else f'{yc_val:.4f}'},"
                     f"{'NA' if vix_pct is None else f'{vix_pct:.4f}'},"
                     f"{'1' if macro_ok else '0'},{_p},{_s50},{_s200},{_s18},{_s100},"
                     f"{'1' if 'persist_hold' in diag else '0'}")
        return (candidate, diag) if return_diag else candidate

    def _CgFredValuesBeforeToday(self, sym, n):
        try:
            h = self.history(sym, n, Resolution.DAILY)
            if h.empty or "value" not in h.columns:
                return None
            try:
                d0 = self.time.date()
                h = h.loc[[d < d0 for d in h.index.get_level_values(-1).date]]
            except Exception:
                pass
            return None if h.empty else h["value"].to_numpy(dtype=float)
        except Exception:
            return None

    def GetVixPercentile(self):
        try:
            v = self._CgFredValuesBeforeToday(self.vix, self.vix_fg_lookback + 5)
            if v is None or len(v) < max(60, int(self.vix_fg_lookback * 0.7)):
                return None
            cur = float(v[-1])
            if not np.isfinite(cur):
                return None
            return float(np.sum(v < cur)) / float(len(v))
        except Exception:
            return None
    def UpdateDrawdownPeak(self):
        value = float(self.portfolio.total_portfolio_value)
        self.portfolio_peak = max(self.portfolio_peak, value)
    def _RecordDdHistory(self):
        if not hasattr(self, "_dd_history"):
            self._dd_history = []
        self._dd_history.append(self.CurrentDrawdown())
    def CurrentDrawdown(self):
        if self.portfolio_peak <= 0:
            return 0.0
        return (float(self.portfolio_peak) - float(self.portfolio.total_portfolio_value)) / float(self.portfolio_peak)
    def GetCurrentGrossExposure(self) -> float:
        try:
            cur = self.GetCurrentWeights()
            if not cur:
                return 0.0
            return float(sum(abs(w) for w in cur.values()))
        except Exception:
            return 0.0
    def _IsDdImproving(self, lookback_days: int = 5, min_improvement: float = 0.002) -> bool:
        try:
            if not hasattr(self, "_dd_history") or not self._dd_history:
                return False
            max_len = lookback_days + 2
            hist = self._dd_history[-max_len:] # read-only slice, do not mutate self._dd_history
            if len(hist) < lookback_days + 1:
                return False
            dd_series = hist[-(lookback_days + 1):]
            if dd_series[0] - dd_series[-1] < min_improvement:
                return False
            if dd_series[-1] > dd_series[-2]:
                return False
            improving_steps = sum(1 for i in range(1, len(dd_series)) if dd_series[i] < dd_series[i - 1])
            return improving_steps >= int(np.ceil(lookback_days * 0.6))
        except Exception:
            return False
    def GetDdControlState(self, dd: float) -> dict:
        ci = self._IsDdImproving(lookback_days=self.dd_clamp_confirm_lookback, min_improvement=self.dd_clamp_confirm_improvement)
        gn = min(1.25, max(0.75, self.GetCurrentGrossExposure()/max(1e-6,float(getattr(self,"dd_recovery_gross_ref",1.50)))))
        ri = self._IsDdImproving(lookback_days=5, min_improvement=0.004*gn)
        ca = (dd > self.dd_soft_start) and not ci
        sc = max(0.4, 1.0-(dd-self.dd_soft_start)/max(1e-6,self.dd_hard_end-self.dd_soft_start)) if ca else 1.0
        return {"clamp_improving":ci,"recovery_improving":ri,"clamp_active":ca,"dd_scale":float(sc)}
    def ApplyCapitalCap(self, targets):
        if not self.live_mode: return targets
        if not hasattr(self,"capital_cap") or not self.capital_cap or self.capital_cap<=0: return targets
        total=float(self.portfolio.total_portfolio_value)
        if total<=0: return targets
        scale=min(1.0,float(self.capital_cap)/total)
        if scale>=0.999: return targets
        self._diag['capital_cap_active']=1
        return {sym:float(w)*scale for sym,w in targets.items()}
    def GetDdCbDrawdown(self) -> float:
        """DD for CB purposes: from local_peak if active, else from global peak."""
        cur   = float(self.portfolio.total_portfolio_value)
        local = getattr(self, "_dd_cb_local_peak", None)
        if local is not None and local > 0:
            return float(max(0.0, (local - cur) / local))
        return self.CurrentDrawdown()

    def CheckDdCircuitBreaker(self) -> bool:
        """
        Returns True  -> skip rebalance (CB active or holding).
        Returns False -> proceed normally.
        Must be called after UpdateDrawdownPeak / UpdatePanicMode / DetectRegime.
        """
        if not getattr(self, "dd_cb_enable", False):
            return False
        today   = self.time.date()
        resume  = getattr(self, "_dd_cb_resume_date", None)

        if resume is not None and today < resume:
            self.log(
                f"[DD_CB_HOLD] date={today} "
                f"dd_local={self.GetDdCbDrawdown():.4f} "
                f"dd_global={self.CurrentDrawdown():.4f} "
                f"cooldown_until={resume} count={getattr(self,'_dd_cb_count',0)}")
            return True

        if resume is not None and today >= resume:
            cur = float(self.portfolio.total_portfolio_value)
            self._dd_cb_local_peak  = cur
            self._dd_cb_resume_date = None
            self._dd_cb_active      = False
            self.log(
                f"[DD_CB_RESUME] date={today} "
                f"local_peak_set={cur:.2f} "
                f"global_peak={self.portfolio_peak:.2f} "
                f"count={getattr(self,'_dd_cb_count',0)}")

        local = getattr(self, "_dd_cb_local_peak", None)
        if local is not None:
            cur = float(self.portfolio.total_portfolio_value)
            if cur > local:
                self._dd_cb_local_peak = cur
            if cur >= self.portfolio_peak * 0.999:
                self._dd_cb_local_peak = None
                self.log(f"[DD_CB_RECOVERED] date={today} global_peak_reached={cur:.2f}")

        cb_dd     = self.GetDdCbDrawdown()
        threshold = float(getattr(self, "dd_cb_threshold", 0.10))
        if cb_dd < threshold:
            return False

        last_trig = getattr(self, "_dd_cb_trigger_date", None)
        min_days  = int(getattr(self, "dd_cb_min_days_between", 10))
        if last_trig is not None and (today - last_trig).days < min_days:
            self.log(
                f"[DD_CB_SKIP] date={today} dd={cb_dd:.4f} "
                f"last_trigger={last_trig} "
                f"min_days_not_met={(today-last_trig).days}<{min_days}")
            return False

        cd = int(getattr(self, "dd_cb_cooldown_days", 5))
        self._dd_cb_trigger_date  = today
        self._dd_cb_resume_date   = today + timedelta(days=cd)
        self._dd_cb_active        = True
        self._dd_cb_count         = int(getattr(self, "_dd_cb_count", 0)) + 1
        self._dd_cb_dd_at_trigger = cb_dd
        local_pk                  = getattr(self, "_dd_cb_local_peak", None)

        self.log(
            f"[DD_CB_FIRE] date={today} "
            f"dd={cb_dd:.4f} dd_global={self.CurrentDrawdown():.4f} "
            f"count={self._dd_cb_count} "
            f"local_peak={local_pk} global_peak={self.portfolio_peak:.2f} "
            f"cooldown_until={self._dd_cb_resume_date} "
            f"regime={self.current_regime} panic={self.panic_mode_active}")

        self.liquidate()   # full portfolio including SH
        return True   # skip rebalance this bar

    def CheckEmergencyStop(self):
        if self.emergency_stop_triggered:
            if not self.emergency_liquidation_executed:
                self.TriggerEmergencyLiquidation()
            return True
        dd = self.CurrentDrawdown()
        if dd > self.emergency_dd_limit:
            self.emergency_stop_triggered = True
            self.log(
                f"[EMERGENCY] DD {dd:.2%} > limit {self.emergency_dd_limit:.2%}; "
                f"triggering full defensive stop")
            self.TriggerEmergencyLiquidation()
            return True
        return False
    def TriggerEmergencyLiquidation(self):
        if self.emergency_liquidation_executed:
            return
        invested_symbols = []
        for symbol, holding in self.portfolio.items():
            if holding is None or not holding.Invested:
                continue
            invested_symbols.append(symbol)
        if not invested_symbols:
            self.emergency_liquidation_executed = True
            self.log("[EMERGENCY] No invested positions found; portfolio already flat")
            return
        park_symbol = self.sym_cash
        park_security = self.securities[park_symbol] if park_symbol in self.securities else None
        can_park = (
            park_security is not None and
            park_security.HasData and
            park_security.Price is not None and
            park_security.Price > 0)
        if can_park:
            emergency_targets = []
            for symbol in invested_symbols:
                if symbol != park_symbol:
                    emergency_targets.append(PortfolioTarget(symbol, 0.0))
            emergency_targets.append(PortfolioTarget(park_symbol, 1.0))
            self.set_holdings(emergency_targets)
            self.log(f"[EMERGENCY] Submitted full defensive rebalance into {park_symbol.Value}")
        else:
            for symbol in invested_symbols:
                self.liquidate(symbol)
            self.log("[EMERGENCY] Defensive asset unavailable; liquidated all positions to cash")
        self.emergency_liquidation_executed = True
        self.last_trade_date = self.time.date()
    def GetAllocations(self):
        if self.debug_regime:
            self.log(f"ALLOC DEBUG regime={self.current_regime}")
        # [CB-C0B] Eliminate core sleeve entirely when SPY already dominates portfolio
        try:
            if self.CoreBallastC0Gate():
                return 0.0, 1.0
        except Exception:
            pass
        if self.current_regime == "RISK_ON":
            return 0.00, 1.0
        elif self.current_regime == "RISK_OFF":
            return 0.25, 0.75
        else: # self.current_regime == "NEUTRAL:
            if self.prev_regime == "RISK_OFF":
                return 0.25, 0.75
            if self.prev_regime == "RISK_ON":
                return 0.95, 0.05
            return 0.35, 0.65
    def _CalcGrossMult(self, dd: float, in_recovery: bool, dd_improving: bool) -> float:
        """
        Unified gross multiplier calculation.
        Order of operations:
          1. Base level by regime + dd
          2. Override if dd_improving
          3. short_shock_flag penalty
          4. Recovery boost (only if not shocked)
        """
        short_shock = self.short_shock_flag
        if self.current_regime == "RISK_ON":
            gross_mult = 1.9
        elif self.current_regime == "NEUTRAL":
            gross_mult = 1.6
        elif self.current_regime == "RISK_OFF":
            _ps = getattr(self, "_panic_state", "NORMAL")
            if self.panic_mode_active:
                gross_mult = 1.0
            elif _ps in ("STRESS", "PANIC"): # [H10]
                gross_mult = 1.7
            else:
                gross_mult = 1.7
        else:
            gross_mult = 1.0
        if dd_improving:
            if self.current_regime == "RISK_ON": gross_mult *= 1.01
            elif self.current_regime == "NEUTRAL": gross_mult *= 1.01
            elif self.current_regime == "RISK_OFF": gross_mult *= 1.01
        if short_shock:
            if self.current_regime == "RISK_ON": gross_mult *= 0.40
            elif self.current_regime == "NEUTRAL": gross_mult *= 0.80
            else: gross_mult *= 0.05
        if in_recovery and self.current_regime in ("RISK_ON", "NEUTRAL"):
            if dd < 0.20:
                boost = 0.1 if dd < 0.12 else 0.05
                gross_mult = min(2.0, gross_mult + boost)
        return gross_mult
    def MergeSleeves(self, core_targets: dict, overlay_targets: dict) -> dict:
        core_alloc, overlay_alloc = self.GetAllocations()
        if self.debug_regime:
            self.log(f"ALLOC USED core={core_alloc} ovl={overlay_alloc}")
        merged: dict = {}
        for sym, w in core_targets.items():
            merged[sym] = merged.get(sym, 0.0) + float(w) * core_alloc
        for sym, w in overlay_targets.items():
            merged[sym] = merged.get(sym, 0.0) + float(w) * overlay_alloc
        dd = self.CurrentDrawdown()
        today = self.time.date()
        recent_panic_recovery = self.InPanicRecoveryWindow()
        recent_riskoff_recovery = (
            self.prev_regime == "RISK_OFF"
            and self.current_regime in ("NEUTRAL", "RISK_ON")
            and self.regime_start_date is not None
            and (today - self.regime_start_date).days <= 10)
        in_recovery = (recent_panic_recovery or recent_riskoff_recovery) and not self.short_shock_flag
        dd_state = self.GetDdControlState(dd)
        dd_improving_flag = dd_state["recovery_improving"] and not self.short_shock_flag
        self._diag['dd_improving'] = self._bool_diag(dd_improving_flag)
        self._diag['recent_panic_recovery'] = self._bool_diag(recent_panic_recovery)
        self._diag['recent_riskoff_recovery'] = self._bool_diag(recent_riskoff_recovery)
        self._diag['in_recovery'] = self._bool_diag(in_recovery)
        self._diag['dd_clamp_active'] = self._bool_diag(dd_state["clamp_active"])
        gross_mult = self._CalcGrossMult(dd, in_recovery, dd_improving_flag)
        gross_mult = getattr(self, "_ApplyIntradayStressGrossCap", lambda x: x)(gross_mult) # [IDS_V2]
        for sym in merged:
            merged[sym] *= gross_mult
        max_symbol_weight = 2.5
        symbol_cap_hit = False
        for sym in merged:
            original = merged[sym]
            merged[sym] = max(-max_symbol_weight, min(merged[sym], max_symbol_weight))
            if merged[sym] != original:
                symbol_cap_hit = True
        self._diag['symbol_cap_triggered'] = self._bool_diag(symbol_cap_hit)
        max_total_exposure = 1.9
        gross = sum(abs(w) for w in merged.values())
        if gross > max_total_exposure:
            scale = max_total_exposure / gross
            for sym in merged:
                merged[sym] *= scale
            self._diag['gross_clamp_triggered'] = 1
            if self.debug_regime:
                self.log(f"[GROSS_CLAMP] gross={gross:.3f} > {max_total_exposure:.2f}, scale={scale:.3f}")
        if self.debug_regime:
            self.log(f"[MERGE_FINAL] {[(str(k.Value), round(v, 3)) for k, v in merged.items()]}")
        return merged
    def BuildCoreTargets(self):
        if self.current_regime == "RISK_OFF":
            base = {
                self.sym_tip: 0.40,
                self.sym_gld: 0.30,
                self.sym_cash: 0.30,}
        else:
            base = {
                self.sym_tip: 0.30,
                self.sym_gld: 0.25,
                self.sym_bnd: 0.20,
                self.sym_cash: 0.20,}
        _v=int(getattr(self,"dur_c1b_variant",0)or 0)
        if _v:
            _c6=float(getattr(self,"_xregime_cache",{}).get("spy60")or 0.)
            _bb=getattr(self,"_dur_bonds_broken",False)
            _sc=int(getattr(self,"_dur_score",0))
            _dbb=getattr(self,"_dur_dyn_bonds_broken",False)
            _dfa=getattr(self,"_dur_dyn_false_alarm",False)
            _rec=_c6>0. and self.current_regime!="RISK_OFF"
            if _v==2: _vt=_bb and _sc>=4 and _dbb and not _rec
            elif _v==3: _vt=_bb and _sc>=4 and not(_dfa and _rec)
            else: _vt=_bb and _sc>=4 and not _rec
            if _vt:
                _fr=0.
                for _s in(self.sym_tip,self.sym_bnd):
                    _w=float(base.get(_s,0.))
                    if _w>0.: base[_s]=0.;_fr+=_w
                if _fr>0.:
                    base[self.sym_cash]=float(base.get(self.sym_cash,0.))+_fr
                    self.log(f"DUR_C1B_CORE,{self.time.date()},v={_v},bb={int(_bb)},dbb={int(_dbb)},fa={int(_dfa)},sc={_sc},s60={_c6:.3f},rec={int(_rec)},freed={_fr:.4f}")
        dd_state = self.GetDdControlState(self.CurrentDrawdown())
        base = self.ApplyDefensiveWinnerTilt(base, dd_state)
        # [CB-C0B] Update gate state for flip detection in ShouldRebalanceCore
        try:
            _cb_gate=self.CoreBallastC0Gate()
        except Exception:
            _cb_gate=False
        self._core_ballast_c0_last_gate=bool(_cb_gate)
        if _cb_gate:
            self.log(f"CB_C0B,{self.time.date()},alloc=0_0 core_bypassed=1")
        return base
    def GetSpyVolMultiplier(self):
        try:
            hist = self.history(self.sym_spy, self.vol_lookback+1, Resolution.DAILY)
            if hist.empty or "close" not in hist.columns: return 1.0
            cl = hist["close"].values
            if len(cl) < 2: return 1.0
            rets = np.diff(np.log(cl))
            if len(rets) < 20: return 1.0
            rv = float(np.std(rets)*np.sqrt(252.0))
            if not np.isfinite(rv) or rv<=0: return 1.0
            rc = min(max(rv, self.min_realized_vol), self.max_realized_vol)
            return float(max(self.min_vol_leverage, min(self.max_vol_leverage, self.target_vol_annual/rc)))
        except Exception: return 1.0
    def GetApproxRealizedVol(self):
        vm = float(self.GetSpyVolMultiplier())
        return float(self.target_vol_annual/max(1e-6,vm)) if vm>0 else None
    def GetLatestYC(self):
        try:
            v = self._CgFredValuesBeforeToday(self.yc, 5)
            return float(v[-1]) if v is not None and len(v) else None
        except Exception:
            return None
    def crashSlotGateOk(self):
        if self.cash_gate_ma is None or not self.cash_gate_ma.IsReady: return False
        px=float(self.securities[self.sym_cash].price); ma=float(self.cash_gate_ma.Current.Value)
        return (ma>0) and (px>=ma)
    def TrendSleeveEnabled(self):
        try:
            vix_pct = self.GetVixPercentile()
        except Exception:
            vix_pct = None
        if vix_pct is not None and vix_pct >= self.trend_enable_vix_pct:
            return True
        realized = self.GetApproxRealizedVol()
        if realized is not None and realized >= self.trend_enable_realized_vol:
            return True
        return False
    def GetTrendSleeveWeights(self):
        _DEF = {self.sym_spy:1.0, self.sym_cash:0.0}
        if self.trend_ma is None or not self.trend_ma.IsReady: return _DEF
        px=float(self.securities[self.sym_spy].price); ma=float(self.trend_ma.Current.Value)
        if ma<=0: return _DEF
        prev=self.trend_state_in_spy
        if px>=ma*(1.0+self.trend_band): self.trend_state_in_spy=True
        elif px<=ma*(1.0-self.trend_band): self.trend_state_in_spy=False
        if self.trend_state_in_spy!=prev:
            self.log(f"[TREND] {'SPY' if self.trend_state_in_spy else 'CASH'} on {self.time.date()}")
        _so=self.trend_state_in_spy
        if _so and getattr(self,"IsBearRallyBlocked",lambda:False)():
            _so=False
            if self._LogAllowedAt():self.log(f"BEAR_RALLY_GATE,{self.time.date()},block=SPY")
        return{self.sym_spy:float(_so),self.sym_cash:float(not _so)}
    def CombineWithTrendSleeve(self, overlay_weights):
        if not self.TrendSleeveEnabled(): return overlay_weights
        sleeve=self.GetTrendSleeveWeights()
        tw=max(0.0,min(self.trend_sleeve_weight_cap,float(self.trend_sleeve_weight)))
        out={sym:float(w)*(1.0-tw) for sym,w in overlay_weights.items()}
        out[self.sym_spy]=float(out.get(self.sym_spy,0.0)+tw*float(sleeve.get(self.sym_spy,0.0)))
        out[self.sym_cash]=float(out.get(self.sym_cash,0.0)+tw*float(sleeve.get(self.sym_cash,0.0)))
        return out or {self.sym_cash:1.0}
    def BuildOverlayTargets(self):
        dd = self.CurrentDrawdown()
        spy = 0.0
        crash = 0.0
        if self.current_regime == "RISK_ON" and not self.panic_mode_active:
            days = (self.time.date() - self.regime_start_date).days if self.regime_start_date else 0
            leverage_mult = self.max_spy_leverage if days >= self.leverage_confirm_days else 1.0
            if self.trend_ma.IsReady:
                price = float(self.securities[self.sym_spy].Price)
                ma = float(self.trend_ma.Current.Value)
                if price <= ma:
                    leverage_mult = 1.0
            vol_mult = self.GetSpyVolMultiplier()
            spy = self.base_spy_weight * leverage_mult * vol_mult
        elif self.current_regime == "NEUTRAL":
            spy = 0.70
        else:
            spy = 0.20
        if self.current_regime == "NEUTRAL" and self.regime_start_date:
            days = (self.time.date() - self.regime_start_date).days
            if days > self.neutral_decay_days:
                spy *= self.neutral_decay_factor
        dd_state = self.GetDdControlState(dd)
        self._diag['dd_clamp_active'] = self._bool_diag(dd_state["clamp_active"])
        _ps = str(getattr(self, "_panic_state", "NORMAL"))
        _sov = _ps in ("PANIC", "STRESS", "RECOVERY")
        _ddr = float(dd_state["dd_scale"]); _dde = _ddr
        if dd_state["clamp_active"]:
            if getattr(self, "overlay_dd_stress_soften_enable", False) and _sov:
                _b = float(max(0.0, min(1.0, getattr(self, "overlay_dd_stress_blend", 0.50))))
                _dde = _ddr + _b * (1.0 - _ddr)
            spy *= _dde
        self._diag['overlay_dd_raw'] = _ddr; self._diag['overlay_dd_eff'] = _dde
        self._diag['overlay_dd_softened']= self._bool_diag(_sov and _dde > _ddr)
        _spy_after_dd = spy                                                  # [SPY_CHAIN]
        _panic_mult = self.GetPanicMult()
        self._diag['panic_mult'] = float(_panic_mult)
        if _panic_mult <= 0: spy = min(spy, self.stress_spy_cap)
        else: spy *= _panic_mult
        if self.current_regime == "RISK_ON":
            spy = max(0.0, min(spy, 1.40))
        else:
            spy = max(0.0, min(spy, 0.90))
        spy = min(spy, 1.35)
        _spy_after_ps = spy                                                  # [SPY_CHAIN]
        _psc = float(getattr(self, "_panic_score", 0.0))
        if getattr(self, "watch_tail_spy_dampen_enable", False) and _ps == "WATCH" and _psc >= float(getattr(self, "watch_tail_score_threshold", 0.30)):
            spy *= max(0.0, min(1.0, float(getattr(self, "watch_tail_spy_multiplier", 0.92))))
            self.log(f"WATCH_TAIL,{self.time.date()},{self.current_regime or 'UNKNOWN'},{_ps},{_psc:.3f},{spy:.4f}")
        # [H-PPB] Post-panic brake:  re-risk  N-   
        #  ,  continuous score     (<threshold).
        # :       risk-on   bounce.
        if (getattr(self, "post_panic_brake_enable", True)
                and not self.panic_mode_active
                and self.last_panic_end_date is not None):
            _ppb_days = int(getattr(self, "post_panic_brake_days", 3))
            _ppb_thr  = float(getattr(self, "post_panic_brake_score_threshold", 0.10))
            _ppb_mult = float(getattr(self, "post_panic_spy_multiplier", 0.75))
            _ppb_d    = (self.time.date() - self.last_panic_end_date).days
            _ppb_score = float(getattr(self, "_panic_score", 0.0))
            if _ppb_d <= _ppb_days and _ppb_score < _ppb_thr:
                spy *= _ppb_mult
                if self.live_mode or getattr(self, "debug_regime", False):
                    self.log(
                        f"[PPB] {self.time.date()} "
                        f"d={_ppb_d}/{_ppb_days} score={_ppb_score:.3f} "
                        f"mult={_ppb_mult:.2f} spy->{spy:.3f}")
        eq_shock = self.RecentEquityShock()
        if eq_shock["active"]:
            spy *= eq_shock["scale"]
            if self.debug_regime:
                self.log(f"[EQ_SHOCK] {eq_shock['mode']} scale={eq_shock['scale']:.2f} | {eq_shock['diag']}")
        spy = getattr(self, "_ApplyIntradayStressSpyCap", lambda x: x)(spy) # [IDS_V2]
        _spy_final = spy                                                      # [SPY_CHAIN]
        # [SPY_CHAIN] Emit priority resolution log -- backtest only when debug_regime, always in live
        _ids_state = str(getattr(self, "_ids_state", "NORMAL"))
        _ids_active = bool(getattr(self, "_ids_active", False))
        if self.live_mode or getattr(self, "debug_regime", False):
            self.log(
                f"[SPY_CHAIN] {self.time.date()} "
                f"binary={'ON' if self.panic_mode_active else 'OFF'} "
                f"ps={_ps}({_psc:.2f}) pm={_panic_mult:.2f} "
                f"ids={'ON' if _ids_active else 'OFF'}({_ids_state}) "
                f"spy: base={_spy_after_dd:.3f}->ps={_spy_after_ps:.3f}->ids={_spy_final:.3f}")
        # [PANIC_CONFLICT] Warn when binary panic is OFF but continuous layers still stressed
        _ps_stressed   = _ps in ("PANIC", "STRESS")
        _ids_stressed  = _ids_active and _ids_state in ("STRESS", "PANIC_SHORT")
        if (not self.panic_mode_active) and (_ps_stressed or _ids_stressed) and (self.live_mode or self._LogAllowedAt()):  # [LOG_GATE]
            self.log(
                f"[PANIC_CONFLICT] {self.time.date()} binary=OFF but "
                f"ps={_ps}({_psc:.2f}) ids={_ids_state}(active={int(_ids_active)}) "
                f"-> spy capped at {_spy_final:.3f} by lower layers")
        yc_val = self.GetLatestYC()
        duration_ok = (yc_val is not None and yc_val >= self.yc_duration_ok_min)
        crash_allowed = duration_ok and self.crashSlotGateOk()
        self._diag['crash_allowed'] = self._bool_diag(crash_allowed)
        defensive_state = (self.panic_mode_active or self.current_regime == "RISK_OFF" or dd > self.dd_soft_start)
        self._diag['defensive_state'] = self._bool_diag(defensive_state)
        remainder = max(0.0, 1.0 - spy)
        cash_anchor = min(max(self.min_cash_anchor_overlay, 0.0), remainder)
        cash = 0.0
        crash = 0.0
        if defensive_state and crash_allowed:
            cash = min(self.max_cr_cash_weight, max(0.0, remainder - cash_anchor))
        else:
            cash = 0.0
            if (not defensive_state) and self.current_regime == "NEUTRAL" and crash_allowed:
                cash = min(self.neutral_cr_cash_weight, remainder)
        crash = cash * self.crash_weight
        cash = cash - crash
        w = {
            self.sym_spy: float(spy),
            self.sym_crash: float(crash),
            self.sym_cash: float(cash)}
        # [PSTRUCT_D0] Diagnostic snapshot at tactical decision point -- no trading impact.
        if self.panic_mode_active or self.short_shock_flag:
            self.EmitPanicStructureDiag(context="pre_tactical")

        if self.panic_mode_active or self.short_shock_flag:
            w = self.ApplyPanicTacticalBlock(w)
        w = self.ApplyLatentCarryManagement(w)
        w = self.ApplyLatentPortfolioCarryManagement(w)
        w = self.ApplyTacticalWinnerCleanup(w)
        w = self.ApplyCommodityTacticalHold(w)
        w = self.ApplyXleNoiseExitVeto(w)
        w = self.ApplyTacticalReset(w)
        w = self.ApplyTacticalAtrExit(w)
        w = self.ApplyTacticalOrphanCleanup(w)
        s = sum(w.values())
        if s <= 0:
            w = {self.sym_cash: 1.0}
        else:
            for k in list(w.keys()):
                w[k] = float(max(0.0, w[k]) / s)
        w = self.CombineWithTrendSleeve(w)
        return w
    def ExecuteTargets(self, targets, reduce_only=False):  # [LSS2]
        if getattr(self, "emergency_stop_triggered", False):
            self._diag['emergency_stop'] = 1
            return
        targets = self._CgFinalTradeGate(targets)  # [E0.4] final diagnostic-trade safety gate
        targets = self.ApplyCapitalCap(targets)
        _cd = self.InCooldown()
        _fe = self.AllowFastExitOnCooldown()
        self._diag['cooldown_blocked'] = self._bool_diag(_cd)
        self._diag['cooldown_fast_exit_ok']= self._bool_diag(_fe)
        cur = self.GetCurrentWeights()
        if getattr(self,"bear_rally_gate_enable",False):targets=self.ApplyBearRallyGate(dict(targets))

        # [H-DUST1] Tactical dust force-close: close only truly tiny residual holdings.
        tactical_dust_close_orders = []
        tactical_dust_syms = set()
        _dust_cur_w = float(getattr(self, "tactical_dust_current_weight", 0.005))  # 0.5% of portfolio
        _dust_tgt_w = float(getattr(self, "tactical_dust_target_weight",  0.005))  # target also tiny
        _act = getattr(self, "_active_tactical_symbol", None)
        _lst = getattr(self, "last_panic_winner", None)
        for sym in getattr(self, "panic_tactical_universe", []):
            try:
                holding  = self.portfolio[sym]
                invested = holding is not None and holding.Invested
            except Exception:
                invested = False
            if not invested:
                continue
            cur_w = float(cur.get(sym, 0.0))
            if cur_w <= 0.0:
                continue
            if cur_w >= _dust_cur_w:                 # meaningful position -- not dust, leave alone
                continue
            tgt_w = float(targets.get(sym, 0.0))
            if tgt_w >= _dust_tgt_w:                 # target still wants a managed sleeve -- leave alone
                continue
            sec = self.securities[sym]
            price = getattr(sec, "Price", None)
            if not sec.HasData or price is None or price <= 0:
                continue
            targets = dict(targets)
            targets[sym] = 0.0
            tactical_dust_syms.add(sym)
            tactical_dust_close_orders.append(PortfolioTarget(sym, 0.0))
            self.log(
                f"[TACTICAL_DUST] date={self.time.date()} sym={sym.Value} "
                f"cur_w={cur_w:.4f} tgt_w={tgt_w:.4f} "
                f"dust_cur={_dust_cur_w:.4f} dust_tgt={_dust_tgt_w:.4f} "
                f"active={_act.Value if _act else 'None'} "
                f"last={_lst.Value if _lst else 'None'}")
            if _act == sym:
                self._active_tactical_symbol             = None
                self._tactical_entry_date                = None
                self._tactical_entry_dd                  = None
                self._tactical_entry_price               = None
                self._tactical_entry_spy_price           = None
                self._tactical_peak_close_since_entry    = None
            if _lst == sym:
                self.last_panic_winner = None
            if hasattr(self, "_stale_tactical_to_zero"):
                self._stale_tactical_to_zero.discard(sym)

        _tac_set = getattr(self, "_tactical_winner_set_date", None)
        _tac_min_hold = int(getattr(self, "tactical_min_hold_days", 15))
        _late_hold_ok = (
            _tac_set is None
            or (self.time.date() - _tac_set).days >= _tac_min_hold)
        _sharp_min_hold = int(getattr(self, "tactical_atr_min_hold_days", 3))
        _sharp_hold_ok = (
            _tac_set is not None
            and (self.time.date() - _tac_set).days >= _sharp_min_hold)
        _lookback_min_hold = int(getattr(self, "tactical_lookback_exit_min_hold_days", 3))  # [TLB_EXIT]
        _lookback_hold_ok = (                                                                # [TLB_EXIT]
            _tac_set is not None
            and (self.time.date() - _tac_set).days >= _lookback_min_hold)
        # [H2] force exit active tactical if carry mismatch too long
        _h2_sym = getattr(self, "_active_tactical_symbol", None)
        _h2_mm  = int(getattr(self, "latent_structure_mismatch_days", 0))
        _h2_max = int(getattr(self, "tactical_max_carry_mm_days", 10))
        if (_h2_sym is not None and _h2_mm >= _h2_max
                and not self.panic_mode_active
                and not self.InPanicRecoveryWindow()
                and not self.short_shock_flag):
            _h2_cd = int(getattr(self, "tactical_reset_cooldown_days", 5))
            targets = self._FinalizeTacticalExit(targets, _h2_sym, cooldown=False, reason="carry_expired")
            self._SetTacticalReentryBlock(_h2_sym, "carry_expired", _h2_cd)
            self.log(f"[H2_EXIT] {self.time.date()} sym={_h2_sym.Value} mm={_h2_mm}d cd={_h2_cd}d")
        tactical_to_zero = []
        tactical_exit_reasons = {}
        for sym in getattr(self, "panic_tactical_universe", []):
            cur_w = float(cur.get(sym, 0.0))
            if abs(cur_w) <= self.min_weight_delta:
                continue
            tgt_w = float(targets.get(sym, 0.0))
            slow_exit = (                                                    # [PERF_EXIT] regime-independent
                getattr(self, "tactical_slow_exit_enable", True)
                and not self.panic_mode_active                               # [PERF_EXIT] never block re-entry during active panic
                and _late_hold_ok
                and self.TacticalExitDropConfirmed(sym, lookback_days=11, min_drop=0.04, use_sharp=False))
            lookback_exit = (                                                # [TLB_EXIT] long-window performance invalidation
                getattr(self, "tactical_lookback_exit_enable", False)
                and not self.panic_mode_active                               # [TLB_EXIT] same guard as slow_exit
                and _lookback_hold_ok
                and self.TacticalLookbackExitConfirmed(
                    sym,
                    lookback_days=int(getattr(self, "tactical_lookback_exit_days", 50)),
                    min_return=float(getattr(self, "tactical_lookback_exit_min_return", -0.01))))
            if slow_exit or lookback_exit:
                tactical_to_zero.append(sym)
                if lookback_exit:
                    tactical_exit_reasons[sym] = "lookback_weak"
                else:
                    tactical_exit_reasons[sym] = "slow"
        perf_exit_force_close = []                                             # [PERF_EXIT] bypasses cooldown
        perf_exit_syms = set()                                                 # [PERF_EXIT] guard against orphan double-close
        if tactical_to_zero:
            for sym in tactical_to_zero:
                reason = tactical_exit_reasons[sym]
                # [PERF_EXIT] freed = target weight (LST may have already trimmed cur_w)
                freed_target_w = max(0.0, float(targets.get(sym, 0.0)))
                cd = int(getattr(self, "tactical_reset_cooldown_days", 5))
                targets = self._FinalizeTacticalExit(
                    targets, sym, cooldown=False, reason=reason,
                    freed_weight_override=freed_target_w, redirect_freed=True)
                self._SetTacticalReentryBlock(sym, reason, cd)
                self.log(
                    f"[PERF_EXIT] {self.time.date()} {sym.Value} "
                    f"reason={reason} cooldown={cd}d freed={freed_target_w:.4f}")
                try:
                    if self.portfolio[sym].Invested:
                        perf_exit_force_close.append(PortfolioTarget(sym, 0.0))
                        perf_exit_syms.add(sym)                                # [PERF_EXIT]
                except Exception:
                    pass
        # [H-OC2] Force-close orphan tactical symbols: invested + not active/last + target=0
        # Bypasses ALL gates: min_weight_delta, min_trade_value_perc, cooldown, margin.
        _tac_active = getattr(self, "_active_tactical_symbol", None)
        _tac_last   = getattr(self, "last_panic_winner", None)
        orphan_close_orders = list(tactical_dust_close_orders) + perf_exit_force_close  # [PERF_EXIT] force-close bypasses cooldown
        _orphan_skipped = set()
        for sym in getattr(self, "panic_tactical_universe", []):
            if sym in tactical_dust_syms:                        # [H-DUST1] already handled -- skip
                continue
            if sym in perf_exit_syms:                            # [PERF_EXIT] already force-closed -- skip
                continue
            if sym == _tac_active or sym == _tac_last:
                continue
            try:
                invested = self.portfolio[sym].Invested
            except Exception:
                invested = False
            if not invested:
                continue
            cur_w = float(cur.get(sym, 0.0))
            if cur_w <= 0.0:
                continue
            tgt_w = float(targets.get(sym, 0.0))
            if tgt_w > 0.0:
                continue  # something intentionally holds it -- don't touch
            sec = self.securities[sym]
            price = getattr(sec, "Price", None)
            if not sec.HasData or price is None or price <= 0:
                _orphan_skipped.add(sym)
                continue
            orphan_close_orders.append(PortfolioTarget(sym, 0.0))
            self.log(
                f"[ORPHAN_FORCE_CLOSE] date={self.time.date()} sym={sym.Value} "
                f"cur_w={cur_w:.4f} active={_tac_active.Value if _tac_active else 'None'} "
                f"last={_tac_last.Value if _tac_last else 'None'}"
            )

        reduce_orders = []
        increase_orders = []
        margin_blocked = 0
        total_value = float(self.portfolio.total_portfolio_value)
        margin_remaining = float(self.portfolio.margin_remaining)
        _spb = bool(getattr(self, "_spyg_sat_exec_bypass", False))
        _ov = getattr(self, "_rrx_param_overrides", {}) or {}
        self.min_trade_sat_value_perc = max(0.0, min(1.0, float(getattr(self, "min_trade_sat_value_perc", self.get_parameter("min_trade_sat_value_perc") or _ov.get("min_trade_sat_value_perc") or 0.05))))
        _mtvp = self.min_trade_sat_value_perc if _spb else self.min_trade_value_perc
        _orphan_syms = {t.symbol for t in orphan_close_orders}
        for sym, tgt in targets.items():
            if sym in _orphan_syms:
                continue  # already handled as force-close
            cur_w = float(cur.get(sym, 0.0))
            tgt_w = float(tgt)
            sec = self.securities[sym]
            price = sec.Price
            if (sec is None) or (not sec.HasData) or (price is None) or (price <= 0):
                continue
            # [CB-C0B] bypass min-trade filters when closing to zero (leftover cleanup)
            _zero_close = (tgt_w == 0.0 and cur_w > 0.0)
            if not _zero_close and not _spb and abs(tgt_w - cur_w) < self.min_weight_delta:
                continue
            trade_value = abs(tgt_w - cur_w) * total_value
            min_trade_value = max(self.min_trade_value, total_value * _mtvp)
            if not _zero_close and trade_value < min_trade_value:
                continue
            if tgt_w < cur_w:
                reduce_orders.append(PortfolioTarget(sym, tgt_w))
            else:
                approx_margin_needed = trade_value * 0.5
                if margin_remaining >= approx_margin_needed:
                    increase_orders.append(PortfolioTarget(sym, tgt_w))
                else:
                    margin_blocked += 1
        _br = _bi = 0
        if _cd:
            _bi = len(increase_orders); increase_orders = []
            if not _fe: _br = len(reduce_orders); reduce_orders = []
        self._diag['margin_blocked_count'] = margin_blocked
        if reduce_only:  # [LSS2] reduce-only gate
            increase_orders = []
        self._diag['reduce_orders'] = len(reduce_orders) + len(orphan_close_orders)
        self._diag['increase_orders'] = len(increase_orders)
        self._diag['cooldown_reduce_blocked'] = _br
        self._diag['cooldown_increase_blocked']= _bi

        if not reduce_orders and not increase_orders and not orphan_close_orders:
            self._spyg_sat_exec_bypass = False
            return
        # Orphan closes go first, unconditionally (bypass all gates)
        if orphan_close_orders:
            self.set_holdings(orphan_close_orders)
        if reduce_orders:
            self.set_holdings(reduce_orders)
        if increase_orders:
            avail_margin = float(self.portfolio.margin_remaining)
            order_meta = []
            total_needed = 0.0
            for target in increase_orders:
                sym = target.symbol
                cur_w = float(cur.get(sym, 0.0))
                tgt_w = float(target.quantity)
                trade_val = abs(tgt_w - cur_w) * total_value
                needed = trade_val * 0.50
                order_meta.append((target, cur_w, tgt_w, needed))
                total_needed += needed
            scale = min(1.0, avail_margin / total_needed) if total_needed > 0 else 0.0
            if scale < 1.0:
                self.log(f"[MARGIN_SCALE] avail=${avail_margin:.0f} needed=${total_needed:.0f} scale={scale:.3f}")
            scaled_targets = []
            for target, cur_w, tgt_w, needed in order_meta:
                if scale <= 0:
                    self._diag['margin_blocked_count'] += 1
                    continue
                new_tgt_w = cur_w + (tgt_w - cur_w) * scale
                if abs(new_tgt_w - cur_w) < self.min_weight_delta and not _spb:
                    continue
                if abs(new_tgt_w - cur_w) * total_value < max(self.min_trade_value, total_value * _mtvp):
                    continue
                scaled_targets.append(PortfolioTarget(target.symbol, new_tgt_w))
            self._diag['increase_orders'] = len(scaled_targets)
            if scaled_targets:
                self.set_holdings(scaled_targets)
        self.last_trade_date = self.time.date()
        self._bootstrap_trade_count = getattr(self, "_bootstrap_trade_count", 0) + 1  # [BSC]
        self.log(f"[TRADE] reduce={len(reduce_orders)} increase={len(increase_orders)}")
        self._spyg_sat_exec_bypass = False
    def AllowFastExitOnCooldown(self) -> bool:
        if not getattr(self,"cooldown_fast_exit_enable",False): return False
        ps=str(getattr(self,"_panic_state","NORMAL"))
        return bool(self.RecentEquityShock().get("active")) or bool(getattr(self,"short_shock_flag",False)) or ps in ("WATCH","PANIC","STRESS")
    def GetCurrentWeights(self):
        total=float(self.portfolio.total_portfolio_value)
        if total<=0: return {}
        return {s:float(h.HoldingsValue)/total for s,h in self.portfolio.items() if h and h.Invested}
    def InCooldown(self):
        return bool(self.last_trade_date and (self.time.date()-self.last_trade_date).days<self.trade_cooldown_days)
    def CoreBallastC0Gate(self) -> bool:
        # [CB-C0B] Gate: actual portfolio SPY weight >= threshold
        if not getattr(self,"core_ballast_c0_enable",False): return False
        try:
            spy_w=max(0.,float(self.GetCurrentWeights().get(self.sym_spy,0.)))
        except Exception:
            return False
        return spy_w>=float(getattr(self,"core_ballast_c0_spy_threshold",0.85))
    def ShouldRebalanceCore(self, today):
        # [CB-C0A] force core rebalance when gate flips ON/OFF
        try:
            gate=self.CoreBallastC0Gate()
            prev=bool(getattr(self,"_core_ballast_c0_last_gate",False))
            if gate!=prev: return True
        except Exception: pass
        if today==getattr(self,"force_rebalance_date",None): return True  # [FORCE_REBAL]
        return (not self.last_core_rebalance) or (today-self.last_core_rebalance).days>=self.max_days_no_core_rebalance
    def ShouldRebalanceOverlay(self, today):
        if self.overlay_shock_flag: self.overlay_shock_flag=False; return True
        if today==getattr(self,"force_rebalance_date",None): return True  # [FORCE_REBAL]
        return (not self.last_overlay_rebalance) or (today-self.last_overlay_rebalance).days>=self.max_days_no_overlay_rebalance

    def _EmitRegimeSplitDiag(self):
        if getattr(self,"log_quiet_mode",False): return  # [LOG-BUDGET]
        try:
            yc_val = None
            try:
                h = self.history(self.yc, 30, Resolution.DAILY)
                if (not h.empty) and "value" in h.columns:
                    yc_val = float(h["value"].iloc[-1])
                    yc_20d = (float(h["value"].iloc[-1]) - float(h["value"].iloc[-21])
                              if len(h) >= 21 else None)
                else:
                    yc_20d = None
            except Exception:
                yc_val = None; yc_20d = None

            vix_pct = None
            try:
                vix_pct = self.GetVixPercentile()
            except Exception:
                pass

            macro_ok = (yc_val is not None and vix_pct is not None
                        and np.isfinite(vix_pct if vix_pct is not None else float("nan")))

            if macro_ok:
                sc = 0
                if vix_pct >= self.vix_high_pct: sc -= 1
                elif vix_pct <= self.vix_low_pct: sc += 1
                if yc_val <= -0.10: sc -= 1
                elif yc_val >= 0.30: sc += 1
                macro_cand = "RISK_OFF" if sc <= -1 else ("RISK_ON" if sc >= 1 else "NEUTRAL")
            else:
                macro_cand = "NA"

            if self.spy_ema_75.IsReady and self.spy_sma_200.IsReady:
                px    = float(self.securities[self.sym_spy].price)
                s50   = float(self.spy_ema_75.Current.Value)
                s200  = float(self.spy_sma_200.Current.Value)
                s18   = float(self.spy_ema_9.Current.Value)
                s100  = float(self.spy_ema_120.Current.Value)
                if (s18 > s200) and (s100 > s200): trend_cand = "RISK_ON"
                elif (px < s50) and (s18 < s100):  trend_cand = "RISK_OFF"
                else:                               trend_cand = "NEUTRAL"
            else:
                trend_cand = "NA"

            dd = self.CurrentDrawdown()
            ps = float(getattr(self, "_panic_score", 0.0))
            _yc  = f"{yc_val:.4f}"  if yc_val  is not None else "NA"
            _yc2 = f"{yc_20d:.4f}"  if yc_20d  is not None else "NA"
            _vix = f"{vix_pct:.3f}" if vix_pct is not None else "NA"
            self.log(
                f"[REGIME_SPLIT] {self.time.date()} "
                f"macro_ok={int(macro_ok)} yc={_yc} yc20d={_yc2} "
                f"vix_pct={_vix} "
                f"macro_cand={macro_cand} trend_cand={trend_cand} "
                f"final={self.current_regime or 'UNK'} "
                f"pure_rs={int(getattr(self,'_rsg_pure_rs',0))} "
                f"ci_shock={int(getattr(self,'_rsg_ci_shock',0))} "
                f"panic={int(bool(self.panic_mode_active))} "
                f"dd={dd:.4f} ps={ps:.2f}")
        except Exception as e:
            if self.live_mode or getattr(self, "debug_regime", False):
                self.log(f"[REGIME_SPLIT] error: {e}")

    def _EmitReRiskVetoDiag(self):
        if getattr(self,"log_quiet_mode",False): return  # [LOG-BUDGET]
        try:
            # -- Macro candidate (VIX + T10Y3M) --
            yc_val = None; yc_20d = None
            try:
                h = self.history(self.yc, 25, Resolution.DAILY)
                if (not h.empty) and "value" in h.columns:
                    yc_val = float(h["value"].iloc[-1])
                    yc_20d = (float(h["value"].iloc[-1]) - float(h["value"].iloc[-21])
                              if len(h) >= 21 else None)
            except Exception:
                pass
            vix_pct = None
            try: vix_pct = self.GetVixPercentile()
            except Exception: pass
            macro_ok = (yc_val is not None and vix_pct is not None
                        and np.isfinite(vix_pct))
            if macro_ok:
                sc = 0
                if vix_pct >= self.vix_high_pct: sc -= 1
                elif vix_pct <= self.vix_low_pct: sc += 1
                if yc_val <= -0.10: sc -= 1
                elif yc_val >= 0.30: sc += 1
                macro_cand = "RISK_OFF" if sc<=-1 else ("RISK_ON" if sc>=1 else "NEUTRAL")
            else:
                macro_cand = "NA"

            # -- Trend candidate (SPY SMAs) --
            if self.spy_ema_75.IsReady and self.spy_sma_200.IsReady:
                px   = float(self.securities[self.sym_spy].price)
                s50  = float(self.spy_ema_75.Current.Value)
                s200 = float(self.spy_sma_200.Current.Value)
                s18  = float(self.spy_ema_9.Current.Value)
                s100 = float(self.spy_ema_120.Current.Value)
                if (s18 > s200) and (s100 > s200): trend_cand = "RISK_ON"
                elif (px < s50) and (s18 < s100):  trend_cand = "RISK_OFF"
                else:                               trend_cand = "NEUTRAL"
            else:
                trend_cand = "NA"

            # -- State flags --
            pure_rs  = int(getattr(self, "_rsg_pure_rs",  0))
            ci_shock = int(getattr(self, "_rsg_ci_shock", 0))
            ps_state = str(getattr(self, "_panic_state",  "NORMAL"))
            ids_state = str(getattr(self, "_ids_state",   "NORMAL"))
            ids_hot   = ids_state in ("WATCH", "STRESS", "PANIC_SHORT")
            ps_hot    = ps_state  in ("WATCH", "STRESS", "PANIC")
            yc20_str  = f"{yc_20d:.3f}" if yc_20d is not None else "NA"
            vix_str   = f"{vix_pct:.3f}" if vix_pct is not None else "NA"
            yc_str    = f"{yc_val:.3f}"  if yc_val  is not None else "NA"

            # -- Veto rule: macro RISK_ON blocked if trend not confirming + stress present --
            stress_present = (pure_rs or ci_shock or ps_hot or ids_hot
                              or (yc_20d is not None and yc_20d < -0.50))
            final = self.current_regime or "UNK"
            # [D0B] stress veto: macro RISK_ON blocked by active stress
            blk_stress = (macro_cand == "RISK_ON"
                          and trend_cand != "RISK_ON"
                          and stress_present)
            # [D0B] recovery veto: macro RISK_ON blocked -- trend not confirming
            blk_recov  = (macro_cand == "RISK_ON"
                          and trend_cand != "RISK_ON")
            wf_stress = "NEUTRAL" if blk_stress and final == "RISK_ON" else final
            wf_recov  = "NEUTRAL" if blk_recov  and final == "RISK_ON" else final

            self.log(
                f"[RRVETO_D0B] {self.time.date()} "
                f"mc={macro_cand} tc={trend_cand} final={final} "
                f"pure_rs={pure_rs} ci={ci_shock} "
                f"ps={ps_state} ids={ids_state} "
                f"yc={yc_str} yc20d={yc20_str} vix={vix_str} "
                f"blk_s={int(blk_stress)} wf_s={wf_stress} "
                f"blk_r={int(blk_recov)} wf_r={wf_recov} "
                f"panic={int(bool(self.panic_mode_active))}")
        except Exception as e:
            if self.live_mode or getattr(self, "debug_regime", False):
                self.log(f"[RRVETO_D0] error: {e}")

    def _EmitRateShockGateDiag(self):
        self._rsg_pure_rs  = 0
        self._rsg_ci_shock = 0
        self._dur_bonds_broken      = False
        self._dur_score             = 0
        self._dur_shadow_mode       = "NORMAL"
        self._dur_router_shadow_mode= "NORMAL"
        self._dur_struct_ptype      = "NA"
        try:
            d         = self.GetPanicStructureDiag()
            spy20     = float(d.get("spy20")          or 0.0)
            bnd20     = float(d.get("bnd20")          or 0.0)
            tip20     = float(d.get("tip20")          or 0.0)
            dbc20     = float(d.get("dbc20")          or 0.0)
            gld20     = float(d.get("gld20")          or 0.0)
            tip_bnd20 = float(d.get("tip_bnd20")      or 0.0)
            dbc_spy20 = float(d.get("dbc_spy20")      or 0.0)
            gld_bnd20 = float(d.get("gld_bnd20")      or 0.0)
            corr20    = float(d.get("spy_bnd_corr20") or 0.0)

            dur_score = (
                int(bnd20     <= -0.015) +
                int(tip20     <   0.0  ) +
                int(tip_bnd20 <   0.0  ) +
                int(corr20    >   0.0  ) +
                int(spy20     <   0.0  )
            )
            bonds_broken = (dur_score >= 4 and bnd20 <= -0.015 and spy20 < 0.0)

            commodity_score = (
                int(dbc20     >  0.0  ) +
                int(dbc_spy20 >  0.03 ) +
                int(tip_bnd20 >  0.005) +
                int(gld20     >  0.0  )
            )

            ids_state  = str(getattr(self, "_ids_state", "NORMAL"))
            ids_stress = ids_state in ("WATCH", "STRESS", "PANIC_SHORT")
            ptype      = str(getattr(self, "_prev_raw_ptype", "NA"))
            rps = (int(bnd20 <= -0.010) + int(tip20 < 0.0) + int(dbc_spy20 >= 0.035)
                   + int(spy20 <= -0.005) + int(ids_stress)
                   + (2 if ptype == "RATE_SHOCK_UNKNOWN" else 0))
            self._rsg_pure_rs  = int(rps >= 3)                                   # [H-2022-RATE-RESTORE-1]
            self._rsg_ci_shock = int(dbc_spy20 > 0.05 and tip_bnd20 > 0.01       # [H-REGIME-SPLIT-D0]
                                     and spy20 < -0.02)

            if bonds_broken and commodity_score >= 3:
                shadow_mode = "BROKEN_COMMODITY"
                s_bil, s_dbc, s_xle = 0.30, 0.40, 0.30
            elif bonds_broken:
                shadow_mode = "BROKEN_CASH"
                s_bil, s_dbc, s_xle = 1.0, 0.0, 0.0
            elif spy20 <= -0.05 and bnd20 >= 0.005:
                shadow_mode = "BOND_HEDGE_OK"
                s_bil, s_dbc, s_xle = 0.0, 0.0, 0.0
            else:
                shadow_mode = "NORMAL"
                s_bil, s_dbc, s_xle = 0.0, 0.0, 0.0

            self._dur_bonds_broken       = bonds_broken
            self._dur_score              = int(dur_score)
            self._dur_shadow_mode        = shadow_mode
            self._dur_router_shadow_mode = shadow_mode
            self._dur_struct_ptype = d.get("ptype","NA") if d.get("ready") else "NA"

            if not getattr(self, "log_enable", True):
                return

            prev_broken = getattr(self, "_dur_bonds_broken_prev", False)
            if bonds_broken and not prev_broken:
                self.log(f"DUR_EVENT,BONDS_BROKEN_ON,{self.time.date()},"
                         f"score={dur_score},mode={shadow_mode},rps={rps}")
            elif not bonds_broken and prev_broken:
                self.log(f"DUR_EVENT,BONDS_BROKEN_OFF,{self.time.date()},score={dur_score}")
            self._dur_bonds_broken_prev = bonds_broken

            if bonds_broken and not getattr(self, "_dur_broken_start", None):
                self._dur_broken_start = self.time.date()
            elif not bonds_broken:
                self._dur_broken_start = None
            lead = (self.time.date() - self._dur_broken_start).days \
                   if getattr(self, "_dur_broken_start", None) else 0

            if not getattr(self,"log_quiet_mode",False):  # [LOG-BUDGET]
                dd = self.CurrentDrawdown()
                self.log(
                    f"DUR_D0,{self.time.date()},"
                    f"{spy20:.4f},{bnd20:.4f},{tip20:.4f},{dbc20:.4f},{gld20:.4f},"
                    f"{tip_bnd20:.4f},{dbc_spy20:.4f},{gld_bnd20:.4f},{corr20:.4f},"
                    f"{dur_score},{int(bonds_broken)},{commodity_score},"
                    f"{shadow_mode},"
                    f"{ptype},{self.current_regime or 'UNK'},"
                    f"{ids_state},{int(bool(self.panic_mode_active))},{dd:.4f},"
                    f"{s_bil:.2f},{s_dbc:.2f},{s_xle:.2f},{lead}"
                )
        except Exception as e:
            if self.live_mode or getattr(self, "debug_regime", False):
                self.log(f"[DUR_D0] error: {e}")
