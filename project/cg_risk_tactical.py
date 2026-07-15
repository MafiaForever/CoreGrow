import numpy as np
from datetime import timedelta, date, datetime
from typing import TYPE_CHECKING
from AlgorithmImports import *


class CoreGrowthRiskTacticalMixin:
    if TYPE_CHECKING:
        time: datetime
        live_mode: bool
        portfolio: object
        securities: object
        yc: object
        vix: object
        sym_spy: object
        sym_gld: object
        sym_bnd: object
        sym_tip: object
        sym_dbc: object
        sym_sh: object
        def _LogAllowedAt(self, dt=None) -> bool: ...
        sym_cash: object
        sym_crash: object
        panic_tactical_universe: list
        spy_ema_75: object
        spy_sma_200: object
        spy_ema_9: object
        spy_ema_120: object
        cash_gate_ma: object
        trend_ma: object
        current_regime: str
        prev_regime: str
        regime_start_date: date
        panic_mode_active: bool
        panic_end_date: datetime
        last_panic_end_date: date
        last_panic_winner: object
        overlay_shock_flag: bool
        short_shock_flag: bool
        _short_shock_set_date: object
        short_shock_decay_days: int
        portfolio_peak: float
        _dd_history: list
        _diag: dict
        vix_fg_lookback: int
        vix_low_pct: float
        vix_high_pct: float
        regime_min_persist_days: int
        debug_regime: bool
        _last_regime_diag_date: date
        dd_soft_start: float
        dd_hard_end: float
        dd_clamp_confirm_lookback: int
        dd_clamp_confirm_improvement: float
        spy_shock_1d_threshold: float
        spy_shock_3d_threshold: float
        spy_shock_5d_threshold: float
        spy_shock_scale_1d: float
        spy_shock_scale_3d: float
        spy_shock_scale_5d: float
        rebalance_shock_threshold: float
        short_shock_2d_threshold: float
        short_shock_1d_threshold: float
        short_shock_3d_threshold: float
        emergency_stop_triggered: bool
        panic_trigger_pct: float
        panic_window_days: int
        panic_recovery_min_days: int
        panic_recovery_max_days: int
        panic_block_max: float
        panic_block_from_spy_frac: float
        panic_mom_lookback: int
        panic_mom_threshold: float
        def_tilt_enable: bool
        def_tilt_budget: float
        def_tilt_lookback: int
        def_tilt_min_score: float
        def_tilt_trend_ma_period: int
        def_tilt_max_single_add: float
        def_tilt_skip_cash_as_winner: bool
        def_ma: dict
        stress_spy_cap: float
        shock_tactical_block_frac: float
        tactical_min_hold_days: int
        tactical_atr_exit_enable: bool
        tactical_atr_len: int
        tactical_atr_trail_mult: float
        tactical_atr_min_hold_days: int
        tactical_sharp_exit_enable: bool
        tactical_slow_exit_enable: bool
        tactical_sharp_atr_mult: float
        tactical_sharp_weak_score_min: int
        tactical_reset_enable: bool
        tactical_reset_min_hold_days: int
        tactical_reset_dd_worsen: float
        tactical_reset_abs_loss: float
        tactical_reset_spy_underperf: float
        tactical_reset_cooldown_days: int
        tactical_reset_require_active_dd: bool
        tactical_cleanup_on_winner_change: bool
        shadow_diag_enable: bool
        _tactical_winner_set_date: object
        _active_tactical_symbol: object
        _tactical_entry_date: date
        _tactical_entry_dd: float
        _tactical_entry_price: float
        _tactical_entry_spy_price: float
        _tactical_peak_close_since_entry: float
        _tactical_exit_lock_active: bool
        _tactical_exit_lock_date: date
        _tactical_reset_hold_until: date
        _tactical_reset_count: int
        _tactical_last_reset_symbol: object
        _tactical_last_reset_date: date
        _stale_tactical_to_zero: set
        # [LST-D0]
        latent_structure_type: str
        latent_structure_mismatch_days: int
        _shadow: dict
        _panic_score: float
        _panic_state: str
        def history(self, symbol, periods, resolution): ...
        def log(self, message: str) -> None: ...
        def debug(self, message: str) -> None: ...
        def set_holdings(self, targets) -> None: ...
        def liquidate(self, symbol=None) -> None: ...
        def GetPanicMult(self) -> float: ...
        def _TrackTacticalEntry(self, winner): ...
        def TacticalAtrExitTriggered(self, sym) -> bool: ...
        def ApplyTacticalAtrExit(self, w: dict) -> dict: ...
        def _FinalizeTacticalExit(self, targets: dict, sym, cooldown: bool = True, reason: str = "UNKNOWN", freed_weight_override=None, redirect_freed: bool = True) -> dict: ...
        def _SetTacticalReentryBlock(self, sym, reason: str, days: int = None) -> None: ...
        def _IsTacticalSymbolBlocked(self, sym) -> bool: ...
        def CurrentDrawdown(self) -> float: ...
        def GetDdControlState(self, dd: float) -> dict: ...
        def _CalcGrossMult(self, dd: float, in_recovery: bool, dd_improving: bool) -> float: ...
        def GetCurrentGrossExposure(self) -> float: ...


    def _GetSpyAtrNorm(self, lookback: int = 14) -> dict:
        _FALLBACK = {"atr_now": 0.01, "atr_prev": 0.01, "atr_base": 0.01}
        _CLAMP = lambda x: float(max(0.003, min(0.05, x)))
        try:
            hist = self.history(self.sym_spy, lookback + 5, Resolution.DAILY)
            if hist.empty or len(hist) < lookback + 3:
                return _FALLBACK
            needed = {"high", "low", "close"}
            if not needed.issubset(set(hist.columns)):
                return _FALLBACK
            highs = hist["high"].to_numpy(dtype=float)
            lows = hist["low"].to_numpy(dtype=float)
            closes = hist["close"].to_numpy(dtype=float)
            n = len(highs)
            if n < lookback + 3:
                return _FALLBACK
            tr = np.empty(n - 1)
            for i in range(1, n):
                tr[i - 1] = max(highs[i] - lows[i],
                                abs(highs[i] - closes[i - 1]),
                                abs(lows[i] - closes[i - 1]))
            cur_close = float(closes[-1])
            if cur_close <= 0:
                return _FALLBACK
            atr_now = float(np.mean(tr[-lookback:])) / cur_close
            prev_close = float(closes[-2])
            if prev_close <= 0:
                prev_close = cur_close
            atr_prev = float(np.mean(tr[-(lookback + 1):-1])) / prev_close
            tr_norm_all = tr / closes[1:] # normalize each TR by its own bar's close
            if len(tr_norm_all) >= lookback + 2:
                atr_base = float(np.median(tr_norm_all[-(lookback + 2):-2]))
            elif len(tr_norm_all) > 2:
                atr_base = float(np.median(tr_norm_all[:-2]))
            else:
                atr_base = atr_prev
            return {
                "atr_now":  _CLAMP(atr_now),
                "atr_prev": _CLAMP(atr_prev),
                "atr_base": _CLAMP(atr_base),}
        except Exception:
            return _FALLBACK
    def _bool_diag(self, value) -> int:
        return 1 if value else 0
    def _ResetDiag(self):
        self._diag = dict(
            date=None, regime=None, dd=0.0, dd_improving=0,
            recent_panic_recovery=0, recent_riskoff_recovery=0, in_recovery=0, shock_active=0,
            panic_mode=0, in_panic_recovery_window=0, equity_shock=0, trend_sleeve_enabled=0,
            trend_state_in_spy=0, crash_allowed=0, defensive_state=0, greed_proxy_active=0,
            dd_clamp_active=0, gross_mult_pre_shock=1.0, gross_mult_final=1.0, boost_applied=0.0,
            shock_x070=0, gross_clamp_triggered=0, symbol_cap_triggered=0, capital_cap_active=0,
            cooldown_blocked=0, margin_blocked_count=0, emergency_stop=0, core_rebalanced=0,
            overlay_rebalanced=0, reduce_orders=0, increase_orders=0, panic_mult=1.0,
            overlay_dd_raw=1.0, overlay_dd_eff=1.0, overlay_dd_softened=0,
            cooldown_fast_exit_ok=0, cooldown_reduce_blocked=0, cooldown_increase_blocked=0)
    def _EmitDiagLog(self):
        d = self._diag
        self.log(
            f"DIAG,{d['date']},{d['regime']},{d['dd']:.4f},{d['dd_improving']},{d['recent_panic_recovery']},{d['recent_riskoff_recovery']},{d['in_recovery']},{d['shock_active']},{d['panic_mode']},{d['in_panic_recovery_window']},{d['equity_shock']},{d['trend_sleeve_enabled']},{d['trend_state_in_spy']},{d['crash_allowed']},{d['defensive_state']},{d['greed_proxy_active']},{d['dd_clamp_active']},{d['gross_mult_pre_shock']:.4f},{d['gross_mult_final']:.4f},{d['boost_applied']:.4f},{d['shock_x070']},{d['gross_clamp_triggered']},{d['symbol_cap_triggered']},{d['capital_cap_active']},{d['cooldown_blocked']},{d['margin_blocked_count']},{d['emergency_stop']},{d['core_rebalanced']},{d['overlay_rebalanced']},{d['reduce_orders']},{d['increase_orders']}")
    def _ResetShadow(self):
        self._shadow = {}
    def _ComputeShadow(self):
        if not getattr(self, "shadow_diag_enable", True):
            return
        try:
            dd = self.CurrentDrawdown()
            ps_score = float(getattr(self, "_panic_score", 0.0))
            ps_state = str(getattr(self, "_panic_state", "NORMAL"))
            eq = self.RecentEquityShock()
            sh = self.DetectShortShockState()
            eq_active = 1 if eq.get("active") else 0
            eq_mode = eq.get("mode") or "NONE"
            sh_active = 1 if sh.get("active") else 0
            sh_mode = sh.get("mode") or "NONE"
            mss = float(max(ps_score,
                ({"S1D":0.45,"S3D":0.75,"B5D":1.00}.get(eq_mode,0.0) if eq_active else 0.0),
                ({"S1D":0.40,"S2D":0.65,"B3D":0.90}.get(sh_mode,0.0) if sh_active else 0.0)))
            ds = float(max(0.0, min(1.0,
                (dd-float(self.dd_soft_start))
                /max(1e-6,float(self.dd_hard_end)-float(self.dd_soft_start)))))
            spy_base = 0.85 if self.current_regime=="RISK_ON" else (0.70 if self.current_regime=="NEUTRAL" else 0.20)
            spy_s = float(max(0.0, spy_base*(1.0-0.70*mss)*(1.0-0.55*ds)))
            if ps_state == "PANIC": spy_s = min(spy_s, float(self.stress_spy_cap))
            spy_s = float(max(0.0, min(1.35, spy_s)))
            gross_s = float(max(0.90, min(1.70, 0.90+0.80*(1.0-max(mss,ds)))))
            pm = float(self.GetPanicMult())
            dds = self.GetDdControlState(dd)
            dsc = float(dds.get("dd_scale", 1.0))
            ddimp = dds.get("recovery_improving", False) and not self.short_shock_flag
            in_rec = (self.InPanicRecoveryWindow() or (
                self.prev_regime=="RISK_OFF"
                and self.current_regime in ("NEUTRAL","RISK_ON")
                and self.regime_start_date is not None
                and (self.time.date()-self.regime_start_date).days<=10)
            ) and not self.short_shock_flag
            gm = float(self._CalcGrossMult(dd, in_rec, ddimp))
            eq_sc = float(eq.get("scale",1.0)) if eq.get("active") else 1.0
            gap_spy = float(pm*dsc*eq_sc - spy_s/max(1e-6,spy_base))
            gap_gr = float(gm - gross_s)
            _n = ps_state=="NORMAL" and not eq_active and not sh_active
            calm_mode = _n and ds<0.12 and ps_score<0.08
            post_stress = _n and not calm_mode
            note = "OK"
            if calm_mode:
                if gap_gr<=-0.30 or gap_spy<=-0.22: note="CALM_BDIFF"
            elif post_stress:
                if (abs(gap_spy)>=0.30 or abs(gap_gr)>=0.35) and not (ps_score<0.10 and abs(gap_gr)<0.22 and abs(gap_spy)<0.25):
                    note="TAIL_BDIFF"
            else:
                if ps_state=="PANIC" and pm<=0.05 and gm>=1.75: note="PANIC_SPY0_GH"
                elif ps_state in ("PANIC","STRESS","RECOVERY") and ds>=0.05 and pm<0.85 and dsc<0.97: note="PANIC_DD_OVL"
                elif ps_state=="RECOVERY" and ds>=0.05 and gap_gr>0.20: note="REC_GROSS_HIGH"
                elif ps_state=="WATCH":
                    note=("GROSS_MISMATCH" if gap_gr<=-0.30 else ("MORE_DEF" if gap_spy<=-0.22 else ("LESS_DEF" if gap_spy>=0.22 else "OK")))
                elif gap_gr<=-0.30: note="GROSS_MISMATCH"
            self._shadow = dict(
                date=self.time.date(), regime=self.current_regime or "UNKNOWN",
                dd=dd, ps=ps_state, psc=ps_score,
                eqa=eq_active, eqm=eq_mode, sha=sh_active, shm=sh_mode,
                mss=mss, ds=ds, spys=spy_s, grs=gross_s,
                pm=pm, dsc=dsc, gm=gm, gspy=gap_spy, ggr=gap_gr, note=note)
        except Exception as e:
            self._shadow = {"_err": str(e)}
    def _EmitShadowLog(self):
        if not getattr(self, "shadow_diag_enable", True):
            return
        if not self.live_mode:  # [LOG_GATE] SHADOW not needed in backtest -- saves ~140 KB
            return
        s = self._shadow
        if not s:
            return
        if "_err" in s:
            self.log(f"[SHADOW_ERR] {s['_err']}")
            return
        self.log(f"SHADOW,{s['date']},{s['regime']},{s['dd']:.4f},{s['ps']},{s['psc']:.3f},{s['eqa']},{s['eqm']},{s['sha']},{s['shm']},{s['mss']:.3f},{s['ds']:.3f},{s['spys']:.4f},{s['grs']:.4f},{s['pm']:.3f},{s['dsc']:.4f},{s['gm']:.4f},{s['gspy']:.4f},{s['ggr']:.4f},{s['note']}")
    def _GetDefensiveScore(self, sym):
        try:
            hist=self.history(sym, self.def_tilt_lookback+1, Resolution.DAILY)
            if hist.empty or "close" not in hist.columns: return None
            cl=hist["close"].to_numpy(dtype=float)
            if len(cl)<self.def_tilt_lookback+1: return None
            p0=float(cl[0]); p1=float(cl[-1])
            if p0<=0 or not np.isfinite(p0) or not np.isfinite(p1): return None
            rets=np.diff(np.log(cl)); vol=float(np.std(rets)) if len(rets)>=10 else 0.0
            if not np.isfinite(vol) or vol<=1e-8: return None
            ma_obj=self.def_ma.get(sym)
            if ma_obj is None or not ma_obj.IsReady: return None
            px=float(self.securities[sym].Price); ma=float(ma_obj.Current.Value)
            if not np.isfinite(px) or not np.isfinite(ma) or ma<=0 or px<ma: return None
            return float((p1/p0-1.0)/vol)
        except Exception: return None
    def SelectDefensiveWinner(self, candidates):
        best_sym=None; best_score=self.def_tilt_min_score
        for sym in candidates:
            if sym==self.sym_cash and self.def_tilt_skip_cash_as_winner: continue
            score=self._GetDefensiveScore(sym)
            if score is not None and score>best_score: best_score=score; best_sym=sym
        return best_sym
    def ApplyDefensiveWinnerTilt(self, base_weights: dict, dd_state: dict) -> dict:
        if not self.def_tilt_enable or not dd_state["clamp_active"]: return base_weights
        w={sym:float(val) for sym,val in base_weights.items()}
        winner=self.SelectDefensiveWinner(list(w.keys()))
        if winner is None: return w
        tilt_budget=min(float(self.def_tilt_budget),float(self.def_tilt_max_single_add))
        if tilt_budget<=0: return w
        donors=[sym for sym in w if sym!=winner and w[sym]>0]
        if not donors: return w
        dt=sum(float(w[s]) for s in donors)
        if dt<=0: return w
        for sym in donors: w[sym]=max(0.0,float(w[sym])-tilt_budget*(float(w[sym])/dt))
        w[winner]=float(w.get(winner,0.0)+tilt_budget)
        s=sum(max(0.0,float(v)) for v in w.values())
        if s<=0: return base_weights
        for sym in list(w.keys()): w[sym]=float(max(0.0,w[sym])/s)
        if self.debug_regime: self.log(f"[DEF_TILT] winner={winner.Value} tilt={tilt_budget:.3f}")
        return w
    def RecentEquityShock(self) -> dict:
        _F = {"active": False, "mode": None, "scale": 1.0}
        try:
            hist = self.history(self.sym_spy, 7, Resolution.DAILY)
            if hist.empty or "close" not in hist.columns or len(hist) < 6:
                return {**_F, "diag": "not_enough_data"}
            cl = hist["close"].to_numpy(dtype=float)
            c1,c2,c3,c4,c6 = float(cl[-1]),float(cl[-2]),float(cl[-3]),float(cl[-4]),float(cl[-6])
            if min(c6,c4,c3,c2,c1) <= 0: return {**_F, "diag": "bad_prices"}
            drop_1d=(c2-c1)/c2; drop_3d=(c4-c1)/c4; drop_5d=(c6-c1)/c6
            accel = ((c3-c2)/c3 > 0) and (drop_1d > (c3-c2)/c3)
            weak_score = self._LastDailyWeaknessScore(hist)
            weak_candle = weak_score >= 2
            atr = self._GetSpyAtrNorm()
            score_1d=drop_1d/atr["atr_now"]; score_3d=drop_3d/atr["atr_prev"]; score_5d=drop_5d/atr["atr_base"]
            _T = {"active": True}
            if score_1d >= self.spy_shock_1d_threshold and weak_candle:
                return {**_T, "mode":"S1D","scale":self.spy_shock_scale_1d,
                        "diag":f"1d={drop_1d:.2%} sc={score_1d:.1f} atr={atr['atr_now']:.4f} w={weak_score}"}
            if score_3d >= self.spy_shock_3d_threshold and (accel or weak_candle):
                return {**_T, "mode":"S3D","scale":self.spy_shock_scale_3d,
                        "diag":f"3d={drop_3d:.2%} sc={score_3d:.1f} atr={atr['atr_prev']:.4f} w={weak_score}"}
            red_count = sum(1 for i in range(-4,-1) if cl[i] > cl[i+1])
            if score_5d >= self.spy_shock_5d_threshold and red_count >= 3 and weak_candle:
                return {**_T, "mode":"B5D","scale":self.spy_shock_scale_5d,
                        "diag":f"5d={drop_5d:.2%} sc={score_5d:.1f} atr={atr['atr_base']:.4f} r={red_count} w={weak_score}"}
            return {**_F, "diag":f"s1={score_1d:.1f} s3={score_3d:.1f} s5={score_5d:.1f} w={weak_score}"}
        except Exception:
            return {**_F, "diag":"error"}
    def _LastDailyWeaknessScore(self, hist) -> int:
        if hist is None or hist.empty or len(hist) < 2: return 0
        if not {"open","high","low","close"}.issubset(set(hist.columns)): return 0
        o1=float(hist["open"].iloc[-1]); h1=float(hist["high"].iloc[-1])
        l1=float(hist["low"].iloc[-1]);  c1=float(hist["close"].iloc[-1])
        l2=float(hist["low"].iloc[-2])
        if min(o1,h1,l1,c1,l2)<=0 or h1<=l1: return 0
        rng=h1-l1
        return int((o1-c1)/o1>=0.003) + int((c1-l1)/rng<=0.3) + int(l1<l2)
    def DetectShortShockState(self) -> dict:
        _F = {"active": False, "mode": None}
        try:
            hist = self.history(self.sym_spy, 5, Resolution.DAILY)
            if hist.empty or "close" not in hist.columns or len(hist) < 4:
                return {**_F, "diag": "not_enough_data"}
            cl = hist["close"].to_numpy(dtype=float)
            c1,c2,c3,c4 = float(cl[-1]),float(cl[-2]),float(cl[-3]),float(cl[-4])
            if min(c4,c3,c2,c1) <= 0: return {**_F, "diag": "bad_prices"}
            drop_1d=(c2-c1)/c2; drop_2d=(c3-c1)/c3; drop_3d=(c4-c1)/c4
            d1=(c3-c2)/c3; d2=(c2-c1)/c2
            red_days_2=int(d1>0)+int(d2>0); accel_2d=(d1>0 and d2>0 and d2>d1)
            weak_score=self._LastDailyWeaknessScore(hist); weak_candle=weak_score>=3
            atr=self._GetSpyAtrNorm()
            s1=drop_1d/atr["atr_now"]; s2=drop_2d/atr["atr_prev"]; s3=drop_3d/atr["atr_base"]
            _T = {"active": True}
            if s1 >= float(getattr(self,"short_shock_1d_threshold",2.0)) and weak_candle:
                return {**_T,"mode":"S1D","diag":f"1d={drop_1d:.2%} sc={s1:.1f} atr={atr['atr_now']:.4f} w={weak_score}"}
            if s2 >= float(getattr(self,"short_shock_2d_threshold",4.0)) and accel_2d and weak_candle:
                return {**_T,"mode":"S2D","diag":f"2d={drop_2d:.2%} sc={s2:.1f} atr={atr['atr_prev']:.4f} w={weak_score}"}
            if s3 >= float(getattr(self,"short_shock_3d_threshold",4.2)) and red_days_2>=2 and accel_2d and weak_candle:
                return {**_T,"mode":"B3D","diag":f"3d={drop_3d:.2%} sc={s3:.1f} atr={atr['atr_base']:.4f} w={weak_score}"}
            return {**_F,"diag":f"s1={s1:.1f} s2={s2:.1f} s3={s3:.1f} w={weak_score}"}
        except Exception:
            return {**_F, "diag": "error"}
    def UpdatePanicMode(self):
        if self.panic_mode_active and self.time >= self.panic_end_date:
            self.panic_mode_active = False
            self.last_panic_end_date = self.time.date()
            self.log(f"[PANIC] OFF on {self.time.date()}")
            return
        hist = self.history(self.sym_spy, 10, Resolution.DAILY)
        if hist.empty or "close" not in hist.columns or len(hist) < 2:
            return
        p0 = float(hist["close"].iloc[0])
        p1 = float(hist["close"].iloc[-1])
        if (not self.panic_mode_active) and p0 > 0 and (p0 - p1) / p0 > self.panic_trigger_pct:
            self.panic_mode_active = True
            self.panic_end_date = self.time + timedelta(days=self.panic_window_days)
            self._tactical_exit_lock_active = False
            self.log(f"[PANIC] ON (10d drop > {self.panic_trigger_pct*100:.1f}%) on {self.time.date()}")
        last_close = float(hist["close"].iloc[-1])
        prev_close = float(hist["close"].iloc[-2])
        if prev_close > 0:
            daily_drop = (prev_close - last_close) / prev_close
            if daily_drop > self.rebalance_shock_threshold:
                self.overlay_shock_flag = True
                if self.live_mode:
                    self.log(f"[SHOCK] DAILY SPY drop {daily_drop*100:.2f}% on {self.time.date()}")
        if not self.short_shock_flag:
            shock = self.DetectShortShockState()
            if shock["active"]:
                self.short_shock_flag = True
                self._short_shock_set_date = self.time.date()
                self._tactical_exit_lock_active = False
                if self.live_mode:
                    self.log(
                        f"[SHORT_SHOCK] mode={shock['mode']} | {shock['diag']}")
    def InPanicRecoveryWindow(self):
        if self.last_panic_end_date is None: return False
        d = (self.time.date()-self.last_panic_end_date).days
        if d <= self.panic_recovery_min_days: return True
        if d <= self.panic_recovery_max_days:
            if self.short_shock_flag or self.RecentEquityShock()["active"] or self.current_regime=="RISK_OFF":
                return True
        return False

    # ---------------------------------------------------------------------
    # [PSTRUCT_D0] Panic structure diagnostic
    # ---------------------------------------------------------------------

    def _PStructRet(self, sym, lookback: int):
        try:
            hist = self.history(sym, lookback + 1, Resolution.DAILY)
            if hist.empty or "close" not in hist.columns or len(hist) < lookback + 1:
                return None
            c = hist["close"].to_numpy(dtype=float)
            if len(c) < lookback + 1 or np.any(c <= 0) or np.any(~np.isfinite(c)):
                return None
            return float(c[-1] / c[-(lookback + 1)] - 1.0)
        except Exception:
            return None

    def _PStructRatioMomentum(self, num_sym, den_sym, lookback: int):
        try:
            hn = self.history(num_sym, lookback + 1, Resolution.DAILY)
            hd = self.history(den_sym, lookback + 1, Resolution.DAILY)
            if hn.empty or hd.empty:
                return None
            if "close" not in hn.columns or "close" not in hd.columns:
                return None
            if len(hn) < lookback + 1 or len(hd) < lookback + 1:
                return None
            n = hn["close"].to_numpy(dtype=float)
            d = hd["close"].to_numpy(dtype=float)
            if np.any(n <= 0) or np.any(d <= 0):
                return None
            if np.any(~np.isfinite(n)) or np.any(~np.isfinite(d)):
                return None
            r_now = float(n[-1] / d[-1])
            r_old = float(n[-(lookback + 1)] / d[-(lookback + 1)])
            if r_old <= 0:
                return None
            return float(r_now / r_old - 1.0)
        except Exception:
            return None

    def _PStructRollingCorr(self, a_sym, b_sym, window: int):
        try:
            ha = self.history(a_sym, window + 1, Resolution.DAILY)
            hb = self.history(b_sym, window + 1, Resolution.DAILY)
            if ha.empty or hb.empty:
                return None
            if "close" not in ha.columns or "close" not in hb.columns:
                return None
            if len(ha) < window + 1 or len(hb) < window + 1:
                return None
            a = ha["close"].to_numpy(dtype=float)
            b = hb["close"].to_numpy(dtype=float)
            if np.any(a <= 0) or np.any(b <= 0):
                return None
            if np.any(~np.isfinite(a)) or np.any(~np.isfinite(b)):
                return None
            ar = np.diff(np.log(a))
            br = np.diff(np.log(b))
            if len(ar) < window or len(br) < window:
                return None
            corr = np.corrcoef(ar[-window:], br[-window:])[0, 1]
            if not np.isfinite(corr):
                return None
            return float(corr)
        except Exception:
            return None

    def GetPanicStructureDiag(self) -> dict:
        """
        Diagnostic-only structural panic classifier.
        No trading side effects.
        """
        lb = int(getattr(self, "panic_struct_lookback", 20))
        cw = int(getattr(self, "panic_struct_corr_window", 20))

        _sym_dbc = getattr(self, "sym_dbc", None)  # [PSTRUCT_D0] defensive: sym_dbc added in main.py
        spy_ret      = self._PStructRet(self.sym_spy, lb)
        gld_ret      = self._PStructRet(self.sym_gld, lb)
        bnd_ret      = self._PStructRet(self.sym_bnd, lb)
        tip_ret      = self._PStructRet(self.sym_tip, lb)
        dbc_ret      = self._PStructRet(_sym_dbc, lb) if _sym_dbc is not None else None
        gld_bnd_mom  = self._PStructRatioMomentum(self.sym_gld, self.sym_bnd, lb)
        tip_bnd_mom  = self._PStructRatioMomentum(self.sym_tip, self.sym_bnd, lb)
        dbc_spy_mom  = self._PStructRatioMomentum(_sym_dbc, self.sym_spy, lb) if _sym_dbc is not None else None
        spy_bnd_corr = self._PStructRollingCorr(self.sym_spy, self.sym_bnd, cw)

        _base = {
            "spy20": spy_ret, "gld20": gld_ret, "bnd20": bnd_ret,
            "tip20": tip_ret, "dbc20": dbc_ret,
            "gld_bnd20": gld_bnd_mom, "tip_bnd20": tip_bnd_mom,
            "dbc_spy20": dbc_spy_mom, "spy_bnd_corr20": spy_bnd_corr,
        }

        ready = all(x is not None for x in [
            spy_ret, gld_ret, bnd_ret, tip_ret, dbc_ret,
            gld_bnd_mom, tip_bnd_mom, dbc_spy_mom
        ])
        if not ready:
            return {"ready": False, "ptype": "NA", "reason": "not_ready", **_base}

        spy_stress       = spy_ret     <= float(getattr(self, "pstruct_spy_stress_20d", -0.05))
        gld_up           = gld_ret     >= getattr(self, "_vc1_gld_up",   0.04)
        bnd_weak         = bnd_ret     <= getattr(self, "_vc1_bnd_down", -0.01)
        gld_bnd_up       = gld_bnd_mom >= getattr(self, "_vc1_gld_bnd",  0.05)
        inflation        = tip_bnd_mom >= getattr(self, "_vc1_infl",     0.005)
        commodity        = dbc_spy_mom >= getattr(self, "_vc1_comm",     0.03)
        commodity_strong = dbc_spy_mom >= getattr(self, "_vc1_comm_s",   0.08)
        inflation_strong = tip_bnd_mom >= getattr(self, "_vc1_infl_s",   0.015)
        corr_positive = (
            spy_bnd_corr is not None
            and spy_bnd_corr >= float(getattr(self, "pstruct_corr_positive_min", 0.10))
        )

        ptype  = "UNKNOWN"
        reason = "no_clear_structure"

        # [D0.3] bond_hedged: SPY stress + BND positive + no infl/comm
        bnd_positive = bnd_ret is not None and bnd_ret >= getattr(self, "_vc1_bnd_pos", 0.005)

        # [D0.2] struct_context_active replaces bare spy_stress
        ps_state  = str(getattr(self, "_panic_state", "NORMAL"))
        ids_active = bool(getattr(self, "_ids_active", False))
        struct_context_active = (
            spy_stress
            or bool(getattr(self, "panic_mode_active", False))
            or bool(getattr(self, "short_shock_flag", False))
            or ps_state in ("WATCH", "STRESS", "PANIC", "RECOVERY")
            or ids_active
        )

        if struct_context_active and inflation and commodity:
            ptype  = "COMMODITY_INFL"
            reason = "tip_bnd_up_dbc_spy_up"

        elif struct_context_active and commodity_strong:
            ptype  = "COMMODITY_LEAD"
            reason = "dbc_spy_strong_early_commodity"

        # 3. BOND_HEDGED_RISK_OFF: BND positive hedge, no infl/comm
        elif spy_stress and bnd_positive and not inflation and not commodity_strong:
            ptype  = "BOND_HEDGED_RISK_OFF"
            reason = "spy_stress_bnd_positive_no_infl_no_strong_commodity"

        elif struct_context_active and gld_up and bnd_weak and gld_bnd_up and not commodity_strong:
            ptype  = "FISCAL_USD"
            reason = "spy_down_gld_up_bnd_weak_no_commodity"

        # 5. STAGFLATION_SAFE: inflation + GLD up, DBC not beating SPY
        elif struct_context_active and inflation and gld_up and not commodity:
            ptype  = "STAGFLATION_SAFE"
            reason = "tip_bnd_up_gld_up_dbc_spy_not_up"

        # 6. DEFL_RECESSION: GLD refuge, no inflation, no commodity
        elif struct_context_active and gld_up and not inflation and not commodity:
            ptype  = "DEFL_RECESSION"
            reason = "gld_up_no_inflation_no_commodity"

        # 7. RATE_SHOCK_UNKNOWN: BND weak, no GLD refuge
        elif struct_context_active and bnd_weak and not gld_up:
            ptype  = "RATE_SHOCK_UNKNOWN"
            reason = "spy_bnd_weak_no_gld_refuge"

        return {
            "ready": True, "ptype": ptype, "reason": reason,
            "spy_stress": int(spy_stress),
            "struct_ctx": int(struct_context_active),  # [D0.2]
            "bond_hedged": int(spy_stress and bnd_positive and not inflation and not commodity_strong),  # [D0.3]
            "bnd_pos": int(bnd_positive),  # [D0.3]
            "gld_up": int(gld_up), "bnd_weak": int(bnd_weak), "gld_bnd_up": int(gld_bnd_up),
            "inflation": int(inflation), "commodity": int(commodity),
            "commodity_strong": int(commodity_strong),
            "inflation_strong": int(inflation_strong),
            "corr_positive": int(corr_positive),
            **_base,
        }

    def EmitPanicStructureDiag(self, context: str = "") -> None:
        if not bool(getattr(self, "panic_struct_diag_enable", False)):
            return
        if not self.live_mode and not self._LogAllowedAt():  # [LOG_GATE] date window
            return

        d = self.GetPanicStructureDiag()

        def _fmt(x):
            if x is None: return "NA"
            try:    return f"{float(x):.4f}"
            except: return "NA"

        active = (
            bool(getattr(self, "panic_mode_active", False))
            or bool(getattr(self, "short_shock_flag", False))
            or bool(getattr(self, "_ids_active", False))
            or str(getattr(self, "_panic_state", "NORMAL")) in ("WATCH", "STRESS", "PANIC", "RECOVERY")
        )
        # In backtest: log daily. In live: only when stress is active.
        if self.live_mode and not active:
            return

        winner     = getattr(self, "last_panic_winner", None)
        winner_str = winner.Value if winner is not None else "None"

        self.log(
            f"PANIC_STRUCT,{self.time.date()}"
            f",context={context}"
            f",ready={int(bool(d.get('ready', False)))}"
            f",type={d.get('ptype', 'NA')}"
            f",reason={d.get('reason', 'NA')}"
            f",spy20={_fmt(d.get('spy20'))}"
            f",gld20={_fmt(d.get('gld20'))}"
            f",bnd20={_fmt(d.get('bnd20'))}"
            f",tip20={_fmt(d.get('tip20'))}"
            f",dbc20={_fmt(d.get('dbc20'))}"
            f",gld_bnd20={_fmt(d.get('gld_bnd20'))}"
            f",tip_bnd20={_fmt(d.get('tip_bnd20'))}"
            f",dbc_spy20={_fmt(d.get('dbc_spy20'))}"
            f",spy_bnd_corr20={_fmt(d.get('spy_bnd_corr20'))}"
            f",ctx={d.get('struct_ctx', 'NA')}"
            f",bnd_pos={d.get('bnd_pos', 'NA')},bond_hdg={d.get('bond_hedged', 'NA')}"
            f",comm={d.get('commodity', 'NA')},infl={d.get('inflation', 'NA')}"
            f",comm_str={d.get('commodity_strong', 'NA')},infl_str={d.get('inflation_strong', 'NA')}"
            f",panic={int(bool(getattr(self, 'panic_mode_active', False)))}"
            f",short_shock={int(bool(getattr(self, 'short_shock_flag', False)))}"
            f",ps={str(getattr(self, '_panic_state', 'NORMAL'))}"
            f",ids={str(getattr(self, '_ids_state', 'NORMAL'))}"
            f",winner={winner_str}"
        )

    def UpdateLatentStructureType(self) -> None:
        """[LST-D0] Compute and persist latent_structure_type with hysteresis.
        Diagnostic only -- zero trading impact in D0.
        Must be called after UpdatePanicScore() + DetectRegime() in DAILYCycle.
        """
        # ptype -> latent type label
        _PTYPE_TO_LATENT = {
            "COMMODITY_INFL":       "COMMODITY_INFL",
            "COMMODITY_LEAD":       "COMMODITY_LEAD",
            "BOND_HEDGED_RISK_OFF": "BOND_HEDGED",
            "FISCAL_USD":           "GOLD_USD_STRESS",
            "STAGFLATION_SAFE":     "STAGFLATION_SAFE",
            "DEFL_RECESSION":       "DEFL_RECESSION",
            "RATE_SHOCK_UNKNOWN":   "RATE_SHOCK",
            "UNKNOWN":              "UNKNOWN",
            "NA":                   "UNKNOWN",
        }
        _CARRY_ALLOW = {
            "COMMODITY_INFL":   {"XLE", "XLB"},
            "COMMODITY_LEAD":   {"XLE", "XLB"},
            "BOND_HEDGED":      {"XLV", "XLU", "GLDM"},
            "GOLD_USD_STRESS":  {"GLDM", "XLV", "XLU"},
            "STAGFLATION_SAFE": {"GLDM", "XLB", "XLU", "XLV"},
            "DEFL_RECESSION":   {"GLDM", "XLV", "XLU"},
            "RATE_SHOCK":       {"XLV", "XLU"},
            "UNKNOWN":          set(),
        }

        d = self.GetPanicStructureDiag()
        raw_ptype = d.get("ptype", "NA") if d.get("ready") else "NA"
        self._prev_raw_ptype = raw_ptype  # [H6]
        candidate = _PTYPE_TO_LATENT.get(raw_ptype, "UNKNOWN")

        today   = self.time.date()
        current = self.latent_structure_type

        # Hysteresis: UNKNOWN needs 3 confirmations; all others need 2
        confirm_needed = 3 if candidate == "UNKNOWN" else 2

        if candidate == current:
            # Stable -- reset pending counter
            self.latent_structure_pending_type  = None
            self.latent_structure_pending_count = 0
        elif candidate == self.latent_structure_pending_type:
            self.latent_structure_pending_count += 1
            if self.latent_structure_pending_count >= confirm_needed:
                self.latent_structure_prev_type     = current
                self.latent_structure_type          = candidate
                self.latent_structure_since         = today
                self.latent_structure_pending_type  = None
                self.latent_structure_pending_count = 0
        else:
            # New candidate -- start fresh count
            self.latent_structure_pending_type  = candidate
            self.latent_structure_pending_count = 1

        # Track entry_type binding for carry-management (future LST-C1)
        if self.panic_mode_active and self.latent_structure_entry_type is None:
            self.latent_structure_entry_type = self.latent_structure_type
        elif not self.panic_mode_active:
            self.latent_structure_entry_type = None

        active_sym = getattr(self, "_active_tactical_symbol", None)
        has_exposure = False
        if active_sym is not None:
            try:
                holding = self.portfolio[active_sym]
                if holding is not None and holding.Invested:
                    pv = float(self.portfolio.total_portfolio_value)
                    cur_w = abs(float(holding.HoldingsValue) / pv) if pv > 0 else 0.0
                    has_exposure = cur_w > 0.005  # 0.5% minimum meaningful position
            except Exception:
                has_exposure = False

        if active_sym is not None and has_exposure:
            ticker  = active_sym.value
            allowed = _CARRY_ALLOW.get(self.latent_structure_type, set())
            if allowed and ticker not in allowed:
                self.latent_structure_mismatch_days += 1
            else:
                self.latent_structure_mismatch_days = 0
        else:
            self.latent_structure_mismatch_days = 0

        # Emit log -- live only to stay within QC 100KB backtest ceiling
        if not self.live_mode:
            return
        days_in = (today - self.latent_structure_since).days if self.latent_structure_since else 0
        winner_str = active_sym.value if active_sym else "none"
        self.log(
            f"LATENT_STRUCT,{today}"
            f",ptype={raw_ptype}"
            f",latent={self.latent_structure_type}"
            f",prev={self.latent_structure_prev_type}"
            f",days_in={days_in}"
            f",pending={self.latent_structure_pending_type or 'none'}"
            f",pending_n={self.latent_structure_pending_count}"
            f",winner={winner_str}"
            f",entry_type={self.latent_structure_entry_type or 'none'}"
            f",mismatch_days={self.latent_structure_mismatch_days}"
        )

    def ApplyLatentCarryManagement(self, w: dict) -> dict:
        """[LST-C1] Soft carry reduction on structural mismatch.
        Layer 1: target-map carry -- active_sym in w, mismatch_days >= 2.
        [LST-F1] raw-structure allow-veto in both layers.
        Does NOT change entry, universe, gross_mult, or SPY risk engine.
        """
        lst_type      = getattr(self, "latent_structure_type", "UNKNOWN")
        mismatch_days = int(getattr(self, "latent_structure_mismatch_days", 0))
        active_sym    = getattr(self, "_active_tactical_symbol", None)
        _log_ok       = self.live_mode or self._LogAllowedAt()

        _CARRY_MATRIX = {
            "COMMODITY_INFL":   {"XLE": "allow",  "XLB": "allow",  "XLV": "reduce", "XLU": "reduce", "GLDM": "reduce"},
            "COMMODITY_LEAD":   {"XLE": "allow",  "XLB": "allow",  "XLV": "reduce", "XLU": "reduce", "GLDM": "reduce"},
            "BOND_HEDGED":      {"XLE": "block",  "XLB": "reduce", "XLV": "allow",  "XLU": "allow",  "GLDM": "allow"},
            "GOLD_USD_STRESS":  {"XLE": "block",  "XLB": "reduce", "XLV": "allow",  "XLU": "allow",  "GLDM": "allow"},
            "STAGFLATION_SAFE": {"XLE": "reduce", "XLB": "allow",  "XLV": "allow",  "XLU": "allow",  "GLDM": "allow"},
            "DEFL_RECESSION":   {"XLE": "block",  "XLB": "reduce", "XLV": "allow",  "XLU": "allow",  "GLDM": "allow"},
            "RATE_SHOCK":       {"XLE": "block",  "XLB": "reduce", "XLV": "allow",  "XLU": "allow",  "GLDM": "reduce"},
            "UNKNOWN":          {},  # no action
        }
        _CARRY_MULT = {"allow": 1.00, "reduce": 0.70, "block": 0.50}

        # -- Layer 1: target-map carry reduction ------------------------------
        if active_sym is not None and mismatch_days >= 2:
            ticker = active_sym.value
            action = _CARRY_MATRIX.get(lst_type, {}).get(ticker)
            if action is not None and action != "allow":
                # [LST-F1] raw-structure allow-veto: if current raw ptype already
                # classifies this sym as "allow", stale latent must not cut it.
                _vetoed = False
                try:
                    _rd = self.GetPanicStructureDiag()
                    _rp = _rd.get("ptype","NA") if _rd.get("ready") else "NA"
                    _rl = {"COMMODITY_INFL":"COMMODITY_INFL","COMMODITY_LEAD":"COMMODITY_LEAD",
                           "BOND_HEDGED_RISK_OFF":"BOND_HEDGED","FISCAL_USD":"GOLD_USD_STRESS",
                           "STAGFLATION_SAFE":"STAGFLATION_SAFE","DEFL_RECESSION":"DEFL_RECESSION",
                           "RATE_SHOCK_UNKNOWN":"RATE_SHOCK"}.get(_rp,"UNKNOWN")
                    _raw_act = _CARRY_MATRIX.get(_rl,{}).get(ticker)
                    # [LST-F2] raw-confirmation gate: raw must also confirm reduce/block
                    if _raw_act not in ("reduce","block"):
                        _vetoed = True
                        if _log_ok: self.log(f"[LST_F2_SKIP] {self.time.date()} sym={ticker} stale={lst_type}:{action} raw={_rl}:{_raw_act or 'none'}")
                    elif action == "block" and _raw_act == "reduce":
                        action = "reduce"  # soften to current raw signal
                except Exception: pass
                if not _vetoed:
                    w = dict(w)
                    old_w = float(w.get(active_sym, 0.0))
                    if old_w > 0.0:
                        if _log_ok:
                            self.log(
                                f"LST_TARGET_AUDIT,{self.time.date()}"
                                f",sym={ticker},lst={lst_type}"
                                f",mismatch={mismatch_days},old_w={old_w:.4f},action={action}"
                            )
                        new_w = old_w * _CARRY_MULT.get(action, 1.00)
                        freed = old_w - new_w
                        w[active_sym]    = new_w
                        w[self.sym_cash] = float(w.get(self.sym_cash, 0.0)) + freed
                        if _log_ok:
                            self.log(
                                f"[LST_CARRY] {self.time.date()}"
                                f" sym={ticker} lst={lst_type} action={action}"
                                f" mult={_CARRY_MULT.get(action,1.):.2f}"
                                f" old_w={old_w:.3f}->new_w={new_w:.3f}"
                                f" freed={freed:.3f} mismatch={mismatch_days}d")

        return w

    def ApplyLatentPortfolioCarryManagement(self, w: dict) -> dict:
        """[LST-C1b] Soft-reduce mismatched tactical holdings absent from target-map."""
        lst = getattr(self, "latent_structure_type", "UNKNOWN")
        mm  = int(getattr(self, "latent_structure_mismatch_days", 0))
        if mm < 2 or lst == "UNKNOWN": return w
        _CI = {"XLE":"allow","XLB":"allow","XLV":"reduce","XLU":"reduce","GLDM":"reduce"}
        _BH = {"XLE":"block","XLB":"reduce","XLV":"allow","XLU":"allow","GLDM":"allow"}
        _CM = {"COMMODITY_INFL":_CI,"COMMODITY_LEAD":_CI,
               "BOND_HEDGED":_BH,"GOLD_USD_STRESS":_BH,"DEFL_RECESSION":_BH,
               "STAGFLATION_SAFE":{"XLE":"reduce","XLB":"allow","XLV":"allow","XLU":"allow","GLDM":"allow"},
               "RATE_SHOCK":{"XLE":"block","XLB":"reduce","XLV":"allow","XLU":"allow","GLDM":"reduce"},
               "COMMODITY_FADE":{"XLE":"reduce","XLB":"reduce","XLV":"reduce","XLU":"reduce","GLDM":"block"},
               **dict.fromkeys(["RATE_SHOCK_DECAY","BOND_HEDGED_DECAY","GOLD_USD_DECAY"],{"XLE":"block","XLB":"reduce","XLV":"allow","XLU":"allow","GLDM":"reduce"})}
        _MU = {"reduce":0.70,"block":0.50}
        _log = self.live_mode or self._LogAllowedAt()
        _raw_lst = "UNKNOWN"
        _rd2 = {}
        try:
            _rd2 = self.GetPanicStructureDiag()
            _rp2 = _rd2.get("ptype","NA") if _rd2.get("ready") else "NA"
            _raw_lst = {"COMMODITY_INFL":"COMMODITY_INFL","COMMODITY_LEAD":"COMMODITY_LEAD",
                        "BOND_HEDGED_RISK_OFF":"BOND_HEDGED","FISCAL_USD":"GOLD_USD_STRESS",
                        "STAGFLATION_SAFE":"STAGFLATION_SAFE","DEFL_RECESSION":"DEFL_RECESSION",
                        "RATE_SHOCK_UNKNOWN":"RATE_SHOCK"}.get(_rp2,"UNKNOWN")
        except Exception: pass
        _c_strong = bool(int(_rd2.get("commodity_strong",0) or 0))
        _c_weak   = bool(int(_rd2.get("commodity",0) or 0))
        _dbc_spy  = float(_rd2.get("dbc_spy20",0.0) or 0.0)
        try:
            pv = float(self.portfolio.total_portfolio_value)
            if pv <= 0: return w
            w = dict(w)
            for _s in getattr(self, "panic_tactical_universe", []):
                try:
                    _h = self.portfolio[_s]
                    if not (_h and _h.Invested): continue
                    _hw = abs(float(_h.HoldingsValue) / pv)
                    if _hw < 0.005 or float(w.get(_s, 0.0)) >= 0.005: continue
                    _a = _CM.get(lst, {}).get(_s.value)
                    if _a not in _MU: continue
                    # [LST-F2] raw-confirmation gate: raw structure must also confirm reduce/block.
                    # UNKNOWN / allow / missing = stale latent has no authority to cut.
                    _TM = {("COMMODITY_INFL","UNKNOWN"):"COMMODITY_FADE",("COMMODITY_LEAD","UNKNOWN"):"COMMODITY_FADE",("RATE_SHOCK","UNKNOWN"):"RATE_SHOCK_DECAY",("BOND_HEDGED","UNKNOWN"):"BOND_HEDGED_DECAY",("GOLD_USD_STRESS","UNKNOWN"):"GOLD_USD_DECAY"}  # [H6]
                    _pr = getattr(self, "_prev_raw_ptype", "UNKNOWN")
                    _eff_raw = _TM.get((_pr, _raw_lst), _raw_lst)  # [H6]
                    _raw_act = _CM.get(_eff_raw, {}).get(_s.value)
                    if _raw_act not in _MU:
                        if _log: self.log(f"[LST_F2_SKIP] {self.time.date()} sym={_s.value} stale={lst}:{_a} raw={_raw_lst}:{_raw_act or 'none'}")
                        continue
                    # Soften: if stale says block but raw only says reduce, use reduce
                    _a_eff = "reduce" if (_a == "block" and _raw_act == "reduce") else _a
                    # [LST-F3] RATE_SHOCK commodity residual guard for XLE/XLB.
                    # When raw=RATE_SHOCK but commodity momentum still positive,
                    # hard-block is too aggressive -- downgrade or skip.
                    if _raw_lst == "RATE_SHOCK" and _s.value in ("XLE","XLB"):
                        if _c_strong:
                            if _log: self.log(f"[LST_F3_SKIP] {self.time.date()} sym={_s.value} comm_strong dbc={_dbc_spy:.3f}")
                            continue
                        if _c_weak:
                            if _log and _a_eff == "block": self.log(f"[LST_F3_DNG] {self.time.date()} sym={_s.value} dbc={_dbc_spy:.3f} block->reduce")
                            _a_eff = "reduce"
                    _m = _MU[_a_eff]; _nw = _hw * _m; _fr = _hw - _nw
                    w[_s] = _nw
                    w[self.sym_cash] = float(w.get(self.sym_cash, 0.0)) + _fr
                    if _log: self.log(
                        f"[LST_HOLDING_CARRY] {self.time.date()}"
                        f" sym={_s.value} lst={lst} act={_a_eff} x{_m:.2f}"
                        f" raw={_raw_lst}:{_raw_act}"
                        f" hold={_hw:.3f}->new={_nw:.3f} fr={_fr:.3f} mm={mm}d")
                except Exception: pass
        except Exception: pass
        return w

    def SelectPanicWinner(self):
        best_sym, best_score = None, -1e9
        stab_on  = bool(getattr(self, "tactical_stability_enable", True))
        stab_lb  = int(getattr(self, "tactical_stability_lookback", 20))
        max_1d   = float(getattr(self, "tactical_stability_max_1d_drop", -0.035))
        max_3d   = float(getattr(self, "tactical_stability_max_3d_drop", -0.060))
        min_5d   = float(getattr(self, "tactical_stability_last_5d_min", -0.020))
        mom_lb   = int(self.panic_mom_lookback)
        hist_len = max(mom_lb + 1, stab_lb + 1)

        # [TPERM] Permission matrix by panic structure type
        _ps    = self.GetPanicStructureDiag()
        _ptype = _ps.get("ptype", "NA") if _ps.get("ready") else "NA"
        _perm_matrix  = getattr(self, "tactical_permission_matrix", {})
        _allowed_set  = (set(_perm_matrix[_ptype]) if _ptype in _perm_matrix else None)
        if bool(getattr(self,"tactical_permission_enable",True)) and _allowed_set is not None:self.log(f"[TAC_PERM] {self.time.date()} ptype={_ptype} allowed={sorted(_allowed_set)}")
        # [BRG-C1]
        if _allowed_set is not None and getattr(self,"IsRateShockEquityBlocked",lambda:False)():
            _allowed_set = set()
            if self._LogAllowedAt(): self.log(f"RATE_SHOCK_EQ_BLOCK,{self.time.date()}")

        for sym in getattr(self, "panic_tactical_universe", []):
            if (_allowed_set is not None
                    and bool(getattr(self, "tactical_permission_enable", True))
                    and sym.Value not in _allowed_set):
                continue
            if self._IsTacticalSymbolBlocked(sym):
                self.log(f"[TAC_BLOCK_SKIP] {self.time.date()} {sym.Value} until={getattr(self,'_tactical_block_until',None)} reason={getattr(self,'_tactical_block_reason','NA')}")
                continue
            try:
                hist = self.history(sym, hist_len, Resolution.DAILY)
            except Exception:
                continue
            if hist.empty or "close" not in hist.columns:
                continue
            c = hist["close"].to_numpy(dtype=float)
            if len(c) < hist_len or np.any(c <= 0) or np.any(~np.isfinite(c)):
                continue
            if stab_on:
                sc = c[-(stab_lb + 1):]
                r1 = (sc[1:] / sc[:-1]) - 1.0
                if len(r1) < 5:
                    continue
                worst_1d = float(np.min(r1))
                worst_3d = min((float(sc[i]) / float(sc[i - 3])) - 1.0 for i in range(3, len(sc)))
                last_5d  = (float(sc[-1]) / float(sc[-6])) - 1.0
                if worst_1d < max_1d or worst_3d < max_3d or last_5d < min_5d:
                    if self.debug_regime:
                        self.log(
                            f"[TAC_REJ] {sym.Value} "
                            f"w1={worst_1d:.2%} w3={worst_3d:.2%} l5={last_5d:.2%}")
                    continue
            mc  = c[-(mom_lb + 1):]
            p0  = float(mc[0])
            p1  = float(mc[-1])
            mom = (p1 / p0) - 1.0
            if (not np.isfinite(mom)) or mom <= float(self.panic_mom_threshold):
                continue
            rets = np.diff(np.log(mc))
            if len(rets) < 2:
                continue
            vol = float(np.std(rets))
            if (not np.isfinite(vol)) or vol <= 1e-8:
                continue
            score = mom / vol
            if score > best_score:
                best_score = score
                best_sym   = sym
        return best_sym

    def ApplyPanicTacticalBlock(self, w: dict) -> dict:
        in_recovery = self.InPanicRecoveryWindow()
        # [TAC_BLOCK] expiry cleanup only
        if getattr(self, "_tactical_exit_lock_active", False):
            until = getattr(self, "_tactical_reset_hold_until", None)
            if until is None or self.time.date() > until:
                self._tactical_exit_lock_active = False
                self._tactical_reset_hold_until = None
        # Determine block_cap by phase BEFORE winner selection
        block_cap = 0.0
        if self.panic_mode_active:
            block_cap = float(self.panic_block_max)
        elif in_recovery:
            if self.panic_recovery_max_days <= 0 or self.last_panic_end_date is None:
                return w
            _sc = max(0.0, 1.0 - float((self.time.date()-self.last_panic_end_date).days)
                      / float(self.panic_recovery_max_days))
            block_cap = float(self.panic_block_max) * _sc * 0.4
            if block_cap <= 0: return w
        elif self.short_shock_flag:
            block_cap = float(self.panic_block_max) * float(getattr(self, "shock_tactical_block_frac", 0.50))
        else:
            return w
        spy_w  = float(w.get(self.sym_spy,  0.0))
        cash_w = float(w.get(self.sym_cash, 0.0))
        if spy_w + cash_w <= 0:
            return w
        block     = min(block_cap, spy_w + cash_w)
        from_spy  = min(spy_w,  block * self.panic_block_from_spy_frac)
        from_cash = min(cash_w, block - from_spy)
        if getattr(self,"dur_c1a_enable",False):
            _c6=float(getattr(self,"_xregime_cache",{}).get("spy60")or 0.)
            _b=getattr(self,"_dur_bonds_broken",False)
            _m=getattr(self,"_dur_shadow_mode","?");_p=getattr(self,"_dur_struct_ptype","?")
            _wv=int(_b and _m=="BROKEN_CASH" and _p=="RATE_SHOCK_UNKNOWN"
                    and not in_recovery and not(_c6>0 and self.current_regime!="RISK_OFF"))
            self.log(f"DUR_C1A_GATE,{self.time.date()},bb={int(_b)},md={_m},pt={_p},s60={_c6:.3f},reg={self.current_regime},ir={int(in_recovery)},pa={int(self.panic_mode_active)},sh={int(self.short_shock_flag)},bl={block:.3f},fs={from_spy:.3f},wv={_wv}")
            if _wv:
                if from_spy>0:w[self.sym_spy]=spy_w-from_spy
                w[self.sym_cash]=float(w.get(self.sym_cash,0.))+from_spy
                self.last_panic_winner=None;self._tactical_winner_set_date=None
                return w
        # Winner selection -- only if veto did not fire
        if in_recovery:
            winner = getattr(self, "last_panic_winner", None)
            if winner is None: return w
        else:
            winner = self.SelectPanicWinner()
            if winner is None:
                self.last_panic_winner = None
                return w
            self.last_panic_winner = winner
            self._tactical_winner_set_date = self.time.date()
            self._RegisterTacticalWinner(winner)  # [H-TC1]
            self._TrackTacticalEntry(winner)
        if from_spy  > 0: w[self.sym_spy]  = spy_w  - from_spy
        if from_cash > 0: w[self.sym_cash] = cash_w - from_cash
        w[winner] = float(w.get(winner, 0.0) + from_spy + from_cash)
        if self.debug_regime:
            phase = "PANIC" if self.panic_mode_active else ("SHOCK" if self.short_shock_flag else "RECOVERY")
            self.log(
                f"[PANIC_BLOCK] phase={phase} winner={winner.Value} block={block:.3f} "
                f"from_spy={from_spy:.3f} from_cash={from_cash:.3f}")
        return w
    def TacticalExitDropConfirmed(self, sym, lookback_days: int = 10, min_drop: float = 0.04, use_sharp: bool = True,) -> bool:
        # [H16] slow (use_sharp=False) / sharp collapse (use_sharp=True)
        try:
            atr_len = int(getattr(self, "tactical_atr_len", 18))
            hist_len = max(lookback_days + 1, atr_len + 3)
            hist = self.history(sym, hist_len, Resolution.DAILY)
            needed = {"open", "high", "low", "close"}
            if hist.empty or len(hist) < 3 or not needed.issubset(set(hist.columns)):
                return False
            o = hist["open"].to_numpy(dtype=float)
            h = hist["high"].to_numpy(dtype=float)
            l = hist["low"].to_numpy(dtype=float)
            c = hist["close"].to_numpy(dtype=float)
            if np.any(~np.isfinite(c)) or np.any(c <= 0):
                return False
            # 1) slow invalidation
            slow_ok = False
            if len(c) >= lookback_days + 1:
                p0 = float(c[-(lookback_days + 1)])
                p1 = float(c[-1])
                if p0 > 0 and np.isfinite(p0) and np.isfinite(p1):
                    ret_lb = (p1 / p0) - 1.0
                    slow_ok = np.isfinite(ret_lb) and ret_lb <= -float(min_drop)
            if not use_sharp:
                return slow_ok
            # 2) sharp collapse: 1-day drop >= N*ATR + weak candle
            if len(c) < atr_len + 2:
                return False
            trs = []
            for i in range(1, len(c)):
                tr = max(h[i] - l[i], abs(h[i] - c[i-1]), abs(l[i] - c[i-1]))
                trs.append(float(tr))
            if len(trs) < atr_len:
                return False
            atr = float(np.mean(trs[-atr_len:]))
            prev_close = float(c[-2])
            last_close = float(c[-1])
            if prev_close <= 0 or not np.isfinite(prev_close) or not np.isfinite(last_close):
                return False
            atr_pct = atr / prev_close
            if not np.isfinite(atr_pct) or atr_pct <= 0:
                return False
            drop_1d = (prev_close - last_close) / prev_close
            o1 = float(o[-1]); h1 = float(h[-1])
            l1 = float(l[-1]); c1 = float(c[-1]); l2 = float(l[-2])
            weak_score = 0
            if o1 > 0 and (o1 - c1) / o1 >= 0.003: weak_score += 1
            if h1 > l1 and (c1 - l1) / (h1 - l1) <= 0.30: weak_score += 1
            if l1 < l2: weak_score += 1
            sharp_mult = float(getattr(self, "tactical_sharp_atr_mult", 1.30))
            weak_min = int(getattr(self, "tactical_sharp_weak_score_min", 3))
            return (  # [H16] sharp_ok only
                np.isfinite(drop_1d)
                and drop_1d >= sharp_mult * atr_pct
                and weak_score >= weak_min)
        except Exception:
            return False

    def TacticalLookbackExitConfirmed(self, sym, lookback_days: int = 20, min_return: float = 0.0) -> bool:
        """[TLB_EXIT] lb-day return <= min_return. Uses price history incl. pre-entry bars."""
        try:
            lb = int(max(2, lookback_days))
            hist = self.history(sym, lb + 1, Resolution.DAILY)
            if hist.empty or "close" not in hist.columns or len(hist) < lb + 1:
                return False
            c = hist["close"].to_numpy(dtype=float)
            if len(c) < lb + 1 or np.any(~np.isfinite(c)) or np.any(c <= 0):
                return False
            p0 = float(c[-(lb + 1)])
            p1 = float(c[-1])
            if p0 <= 0 or not np.isfinite(p0) or not np.isfinite(p1):
                return False
            ret_lb = (p1 / p0) - 1.0
            triggered = np.isfinite(ret_lb) and ret_lb <= float(min_return)
            if triggered:
                self.log(f"[TLB_EXIT] {self.time.date()} {sym.Value} lb={lb} ret={ret_lb:.2%} lim={float(min_return):.2%}")
            return bool(triggered)
        except Exception:
            return False

    def TacticalResetTriggered(self) -> dict:
        if not getattr(self, "tactical_reset_enable", False):
            return {"triggered": False, "reason": "disabled"}

        sym       = getattr(self, "_active_tactical_symbol", None)
        entry_date= getattr(self, "_tactical_entry_date", None)
        entry_dd  = getattr(self, "_tactical_entry_dd", None)
        entry_px  = getattr(self, "_tactical_entry_price", None)
        entry_spy = getattr(self, "_tactical_entry_spy_price", None)
        if sym is None or entry_date is None or entry_dd is None:
            return {"triggered": False, "reason": "no_entry"}

        held = (self.time.date() - entry_date).days
        if held < int(getattr(self, "tactical_reset_min_hold_days", 3)):
            return {"triggered": False, "reason": "min_hold", "held": held}

        cur_dd    = float(self.CurrentDrawdown())
        dd_delta  = cur_dd - float(entry_dd)
        dd_limit  = float(getattr(self, "tactical_reset_dd_worsen", 0.015))

        # Only fire during active drawdown stress (not on small pullbacks)
        if bool(getattr(self, "tactical_reset_require_active_dd", True)):
            soft_start = float(getattr(self, "dd_soft_start", 0.055))
            if cur_dd <= soft_start:
                return {"triggered": False, "reason": "dd_not_active",
                        "held": held, "dd": cur_dd, "dd_delta": dd_delta}

        if dd_delta < dd_limit:
            return {"triggered": False, "reason": "dd_delta_small",
                    "held": held, "dd": cur_dd, "dd_delta": dd_delta}

        # Need valid current prices
        try:
            cur_px  = float(self.securities[sym].price)
            cur_spy = float(self.securities[self.sym_spy].price)
        except Exception:
            return {"triggered": False, "reason": "bad_price", "held": held}

        if (cur_px <= 0 or cur_spy <= 0
                or entry_px is None or entry_spy is None
                or float(entry_px) <= 0 or float(entry_spy) <= 0):
            return {"triggered": False, "reason": "bad_entry_price", "held": held}

        tac_ret = cur_px  / float(entry_px)  - 1.0
        spy_ret = cur_spy / float(entry_spy) - 1.0
        rel_ret = tac_ret - spy_ret

        abs_limit = float(getattr(self, "tactical_reset_abs_loss",      -0.020))
        rel_limit = float(getattr(self, "tactical_reset_spy_underperf", -0.005))

        fail_abs = tac_ret <= abs_limit
        fail_rel = rel_ret <= rel_limit

        if not (fail_abs and fail_rel):
            return {"triggered": False, "reason": "asset_ok", "held": held,
                    "dd": cur_dd, "dd_delta": dd_delta,
                    "tac_ret": tac_ret, "spy_ret": spy_ret, "rel_ret": rel_ret}

        return {
            "triggered": True,
            "reason":    "ABS_LOSS_AND_SPY_UNDERPERF",
            "symbol":    sym,
            "held":      held,
            "dd":        cur_dd,
            "entry_dd":  float(entry_dd),
            "dd_delta":  dd_delta,
            "tac_ret":   tac_ret,
            "spy_ret":   spy_ret,
            "rel_ret":   rel_ret,}

    def ApplyTacticalReset(self, w: dict) -> dict:
        r = self.TacticalResetTriggered()
        if not r.get("triggered", False):
            return w
        sym = r.get("symbol")
        if sym is None:
            return w
        self._tactical_last_reset_symbol = sym
        self._tactical_last_reset_date   = self.time.date()
        self._tactical_reset_count       = int(getattr(self, "_tactical_reset_count", 0)) + 1
        cd = int(getattr(self, "tactical_reset_cooldown_days", 5))
        self.log(
            f"[TACTICAL_RESET] date={self.time.date()} sym={sym.Value} "
            f"reason={r.get('reason')} old_w={float(w.get(sym, 0.0)):.3f} held={r.get('held')} "
            f"dd={r.get('dd', 0.0):.4f} entry_dd={r.get('entry_dd', 0.0):.4f} "
            f"dd_delta={r.get('dd_delta', 0.0):.4f} "
            f"tac_ret={r.get('tac_ret', 0.0):.2%} spy_ret={r.get('spy_ret', 0.0):.2%} "
            f"rel={r.get('rel_ret', 0.0):.2%} cooldown_until={self.time.date() + timedelta(days=cd)}")
        return self._FinalizeTacticalExit(w, sym, cooldown=True, reason=r.get("reason", "RESET"))

    # [H-TC1]
    def _RegisterTacticalWinner(self, winner):
        if winner is None or not bool(getattr(self, "tactical_cleanup_on_winner_change", True)): return
        old = getattr(self, "_active_tactical_symbol", None)
        if old is not None and old != winner:
            if not hasattr(self, "_stale_tactical_to_zero"): self._stale_tactical_to_zero = set()
            self._stale_tactical_to_zero.add(old)
            if self.debug_regime: self.log(f"[TACTICAL_ROTATE] old={old.Value} new={winner.Value}")
    def ApplyTacticalWinnerCleanup(self, w: dict) -> dict:
        if not bool(getattr(self, "tactical_cleanup_on_winner_change", True)): return w
        stale = getattr(self, "_stale_tactical_to_zero", None)
        if not stale: return w
        out = dict(w)
        for sym in list(stale):
            old_w=float(out.get(sym,0.0)); out[sym]=0.0
            if self.debug_regime: self.log(f"[TACTICAL_CLEANUP] stale={sym.Value} was={old_w:.3f}")
        self._stale_tactical_to_zero = set()
        return out

    def ApplyTacticalOrphanCleanup(self, w: dict) -> dict:
        out    = dict(w)
        active = getattr(self, "_active_tactical_symbol", None)
        last   = getattr(self, "last_panic_winner", None)
        for sym in getattr(self, "panic_tactical_universe", []):
            try:
                holding  = self.portfolio[sym]
                invested = holding is not None and holding.Invested
            except Exception:
                invested = False
            if not invested:
                continue
            if sym == active or sym == last:
                continue
            if float(out.get(sym, 0.0)) > 0:
                continue
            out[sym] = 0.0
            self.log(
                f"[TACTICAL_ORPHAN] date={self.time.date()} sym={sym.Value} "
                f"active={active.Value if active else 'None'} "
                f"last={last.Value if last else 'None'}")
        return out