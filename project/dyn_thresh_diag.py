# dyn_thresh_diag.py - DYN-THRESH-D0: dynamic z-score shadow for bond-broken signal
# Diagnostic only. No target weights change.
import numpy as np
from datetime import datetime
from typing import TYPE_CHECKING
from AlgorithmImports import Resolution


class DynamicThresholdDiagMixin:
    """Shadow z-score layer alongside absolute DUR-D0 thresholds.
    Answers: is bonds_broken statistically meaningful vs recent vol?
    """
    if TYPE_CHECKING:
        time: datetime
        live_mode: bool
        current_regime: str
        sym_spy: object
        sym_bnd: object
        portfolio: object
        sym_tip: object
        sym_dbc: object
        sym_gld: object
        log_enable: bool
        def history(self, *a, **kw): ...
        def log(self, msg: str) -> None: ...

    def _EmitDynamicThresholdDiag(self):
        if not getattr(self, "dyn_thresh_d0_enable", False):
            return
        if not getattr(self, "log_enable", True):
            return
        try:
            syms = [self.sym_spy, self.sym_bnd, self.sym_tip, self.sym_dbc, self.sym_gld]
            h = self.history(syms, 300, Resolution.DAILY)
            if h is None or h.empty:
                return

            def _c(sym):
                try:
                    return h.loc[sym]["close"].to_numpy(dtype=float)
                except Exception:
                    return None

            def _ret(px, n):
                """n-day return series from price array"""
                if px is None or len(px) <= n:
                    return None
                return np.array([float(px[i] / px[i - n] - 1) for i in range(n, len(px))])

            def _zs(ser, w=60):
                """(value, mean, std, z) for last element vs w-period window"""
                if ser is None or len(ser) < w:
                    return 0., 0., 0., 0.
                v = float(ser[-1])
                s = ser[-w:]
                m = float(np.mean(s))
                sd = float(np.std(s))
                return v, m, sd, (v - m) / sd if sd > 1e-8 else 0.

            spy_c = _c(self.sym_spy)
            bnd_c = _c(self.sym_bnd)
            tip_c = _c(self.sym_tip)
            dbc_c = _c(self.sym_dbc)
            gld_c = _c(self.sym_gld)

            # Return series
            bnd20_s = _ret(bnd_c, 20)
            bnd10_s = _ret(bnd_c, 10)
            bnd5_s  = _ret(bnd_c, 5)
            spy20_s = _ret(spy_c, 20)
            tip20_s = _ret(tip_c, 20)
            dbc20_s = _ret(dbc_c, 20)
            gld20_s = _ret(gld_c, 20)

            # Relative return series (aligned to shorter)
            def _rel(a, b):
                if a is None or b is None:
                    return None
                n = min(len(a), len(b))
                return a[-n:] - b[-n:]

            dbc_spy_s = _rel(dbc20_s, spy20_s)
            tip_bnd_s = _rel(tip20_s, bnd20_s)
            gld_bnd_s = _rel(gld20_s, bnd20_s)

            b20, b20m, b20s, b20z = _zs(bnd20_s)
            b10, b10m, b10s, b10z = _zs(bnd10_s)
            b5,  b5m,  b5s,  b5z  = _zs(bnd5_s)
            ds,  dsm,  dss,  dsz  = _zs(dbc_spy_s)
            tb,  tbm,  tbs,  tbz  = _zs(tip_bnd_s)
            gb,  gbm,  gbs,  gbz  = _zs(gld_bnd_s)
            gd,  gdm,  gds,  gdz  = _zs(gld20_s)

            # [DYN-THRESH-D0B] Annual (252d) z-scores — diagnostic only
            _, _, _, b20z252 = _zs(bnd20_s, 252)
            _, _, _, b10z252 = _zs(bnd10_s, 252)
            _, _, _, b5z252  = _zs(bnd5_s,  252)
            _, _, _, dsz252  = _zs(dbc_spy_s, 252)
            _, _, _, tbz252  = _zs(tip_bnd_s, 252)
            # blind60: 60d eye adapted, but 252d still sees stress
            b20_blind60  = int(b20z > -1.25 and b20z252 <= -1.25)
            b5_blind60   = int(b5z  > -1.25 and b5z252  <= -1.25)
            dyn_smoothed = int(b20_blind60 == 1 or b5_blind60 == 1)
            # [DUR-B10-D0] 10d duration shock flags -- diagnostic only
            b10_dyn     = int(b10z <= -1.25)
            b10_deep    = int(b10z <= -1.75)
            b10_extreme = int(b10z <= -2.00)
            b10_blind60 = int(b10z > -1.25 and b10z252 <= -1.25)

            spy20 = float(spy20_s[-1]) if spy20_s is not None and len(spy20_s) else 0.
            tip20 = float(tip20_s[-1]) if tip20_s is not None and len(tip20_s) else 0.

            # [VOL-C1] Store volatility-adjusted thresholds (pstruct_vol_mult>0 = enabled)
            _vm = float(getattr(self, "pstruct_vol_mult", 0.))
            if _vm > 0.:
                # Bond Down: Ищем реальный обвал (Z <= -1.25)
                self._vc1_bnd_down = -(b20s * 1.0 * _vm) if b20s else -0.012            # 1.0 else -0.01 # self.testFloat
                # Bond Recovery: Достаточно небольшого возврата к среднему (Z >= 0.5)
                self._vc1_bnd_pos  = (b20s * 0.5 * _vm) if b20s else 0.005              # 0.5 else  0.005
                # Inflation: Чувствительный вход (Z >= 0.5)
                self._vc1_infl     = (tbs * 0.5 * _vm) if tbs else 0.004                # 0.75 else  0.005 #
                # Strong Inflation: Подтвержденный тренд (Z >= 2.0)
                self._vc1_infl_s   = (tbs * 2.0 * _vm) if tbs else 0.015                # 2.0 else  0.015
                # Commodity Lead: Рабочий тренд (Z >= 1.0)
                self._vc1_comm     = (dss * 1.0 * _vm) if dss else 0.03                 # 1.0 else  0.03
                # Commodity Spike: Экстремальный режим (Z >= 2.5)
                self._vc1_comm_s   = (dss * 2.0 * _vm) if dss else 0.08                 # 2.0 else  0.08    #
                # Gold: Фильтр ложных атак (Z >= 1.5)
                self._vc1_gld_up   = (gds * 0.5 * _vm) if gds else 0.04                 # 1.0 else  0.04    #
                self._vc1_gld_bnd  = (gbs * 0.5 * _vm) if gbs else 0.05                 # 1.0 else  0.05
            """
            if _vm > 0.:
                self._vc1_bnd_down = -(b20s * _vm)       if b20s else -0.01
                self._vc1_bnd_pos  =  (b20s * 0.5 * _vm) if b20s else  0.005
                self._vc1_infl     =  (tbs  * 0.75* _vm) if tbs  else  0.005   
                self._vc1_infl_s   =  (tbs  * 2.0 * _vm) if tbs  else  0.015
                self._vc1_comm     =  (dss  * _vm)       if dss  else  0.03
                self._vc1_comm_s   =  (dss  * 2.0 * _vm) if dss  else  0.08
                self._vc1_gld_up   =  (gds  * 0.9 * _vm) if gds  else  0.04
                self._vc1_gld_bnd  =  (gbs  * _vm)       if gbs  else  0.05
            """

            # SPY/BND 20d rolling correlation from price series
            try:
                if (spy_c is not None and bnd_c is not None
                        and len(spy_c) >= 21 and len(bnd_c) >= 21):
                    sr = np.diff(np.log(spy_c[-21:]))
                    br = np.diff(np.log(bnd_c[-21:]))
                    corr20 = float(np.corrcoef(sr, br)[0, 1]) if np.isfinite(sr).all() and np.isfinite(br).all() else 0.
                else:
                    corr20 = 0.
            except Exception:
                corr20 = 0.

            # Absolute hits (current fixed thresholds)
            b20_abs = int(b20 <= -0.015)
            b5_abs  = int(b5  <= -0.010)
            ds_abs  = int(ds  >=  0.030)
            tbup_abs = int(tb >=  0.005)
            tbdn_abs = int(tip20 < 0.0)
            corr_abs = int(corr20 > 0.0)

            # Dynamic hits (z-score based)
            b20_dyn  = int(b20z <= -1.25)
            b5_dyn   = int(b5z  <= -1.25)
            ds_dyn   = int(dsz  >=  1.25)
            tbup_dyn = int(tbz  >=  1.00)
            tbdn_dyn = int(tip20 < 0.0)   # tip20 direction same as absolute

            # Correlation persistence streak
            prev_streak = int(getattr(self, "_dyn_corr_streak", 0))
            streak = (prev_streak + 1) if corr20 > 0.1 else 0
            self._dyn_corr_streak = streak
            corr_ok = int(streak >= 3)

            # Absolute bonds_broken state (from DUR-D0)
            abs_bb    = int(bool(getattr(self, "_dur_bonds_broken", False)))
            abs_score = int(getattr(self, "_dur_score", 0))

            # Dynamic bonds_broken shadow
            dyn_score = (int(b20z <= -1.25) + int(b5z <= -1.25) + int(tip20 < 0.)
                         + int(tbz < -0.75) + corr_ok + int(spy20 < 0.))
            dyn_bb = int(dyn_score >= 4 and b20z <= -1.25 and spy20 < 0.)

            agree       = int(abs_bb == dyn_bb)
            conflict    = int(abs_bb != dyn_bb)
            false_alarm = int(abs_bb == 1 and dyn_bb == 0)
            blind_spot  = int(abs_bb == 0 and dyn_bb == 1)

            # [ACCEL] Two-point acceleration profile: fast (5d/10d) and medium (10d/20d).
            # accel_fast  >= 0.60: acute 5d shock vs 10d baseline (whipsaw-resistant)
            # accel_medium >= 0.65: 10d momentum vs 20d structure (regime acceleration)
            accel_fast   = float(b5  / b10) if (b10 < -1e-6 and b5  < 0.) else 0.
            accel_medium = float(b10 / b20) if (b20 < -1e-6 and b10 < 0.) else 0.
            bnd_accel_fast   = int(accel_fast   >= 0.60)
            bnd_accel_medium = int(accel_medium >= 0.65)

            # [DUR-C1B] Expose dynamic fields for C1B variant logic
            self._dur_dyn_bonds_broken = bool(dyn_bb)
            self._dur_dyn_false_alarm  = bool(false_alarm)
            self._dur_dyn_blind_spot   = bool(blind_spot)
            self._dur_dyn_score        = int(dyn_score)

            # [DUR-D0H] Expose raw hit flags for repair/hysteresis diagnostic
            self._dur_b20_abs      = bool(b20_abs)
            self._dur_b20_dyn      = bool(b20_dyn)
            self._dur_b5_abs       = bool(b5_abs)
            self._dur_b5_dyn       = bool(b5_dyn)
            self._dur_b20_blind60  = bool(b20_blind60)
            self._dur_b5_blind60   = bool(b5_blind60)
            self._dur_dyn_smoothed = bool(dyn_smoothed)
            self._dur_b10_dyn      = bool(b10_dyn)      # [DUR-B10-D0]
            self._dur_b10_blind60  = bool(b10_blind60)  # [DUR-B10-D0]
            self._dur_b10z         = float(b10z)        # [DUR-B10-D0]
            self._dur_b20z         = float(b20z)        # [DUR-AUDIT]
            self._dur_b5z          = float(b5z)         # [DUR-AUDIT]
            self._dur_b5_raw       = float(b5)          # [SHADOW-NAV] BND 5d return

            if getattr(self, "dyn_thresh_d0_log_enable", True):  # [LOG-BUDGET]
                self.log(
                    f"DYN_THRESH_D0,{self.time.date()},"
                    f"{b20:.4f},{b20m:.4f},{b20s:.4f},{b20z:.3f},{b20_abs},{b20_dyn},"
                f"{b10:.4f},{b10m:.4f},{b10s:.4f},{b10z:.3f},"
                f"{b5:.4f},{b5m:.4f},{b5s:.4f},{b5z:.3f},{b5_abs},{b5_dyn},"
                f"{accel_fast:.3f},{bnd_accel_fast},{accel_medium:.3f},{bnd_accel_medium},"
                f"{ds:.4f},{dsm:.4f},{dss:.4f},{dsz:.3f},{ds_abs},{ds_dyn},"
                f"{tb:.4f},{tbm:.4f},{tbs:.4f},{tbz:.3f},{tbup_abs},{tbdn_abs},{tbup_dyn},{tbdn_dyn},"
                f"{corr20:.4f},{corr_abs},{corr_ok},{streak},"
                f"{abs_score},{abs_bb},{dyn_score},{dyn_bb},"
                f"{agree},{conflict},{false_alarm},{blind_spot},"
                f"{b20z252:.3f},{b10z252:.3f},{b5z252:.3f},{dsz252:.3f},{tbz252:.3f},"
                    f"{b20_blind60},{b5_blind60},{dyn_smoothed}"
                )
            # [DUR-B10-D0] 10d duration shock diagnostic log
            if getattr(self, "dur_b10_d0_enable", False):
                _xr  = str(getattr(self, "_xregime_cache", {}).get("xregime", "NA"))
                _ptp = str(getattr(self, "_prev_raw_ptype", "NA"))
                _rg  = str(self.current_regime or "UNK")
                _ps  = str(getattr(self, "_panic_state", "NORMAL"))
                _ids = str(getattr(self, "_ids_state", "NORMAL"))
                self.log(
                    f"DUR_B10_D0,{self.time.date()},"
                    f"b20z={b20z:.3f},b10z={b10z:.3f},b5z={b5z:.3f},"
                    f"b20_dyn={b20_dyn},b10_dyn={b10_dyn},b5_dyn={b5_dyn},"
                    f"b10_deep={b10_deep},b10_extreme={b10_extreme},"
                    f"b20_blind60={b20_blind60},b10_blind60={b10_blind60},b5_blind60={b5_blind60},"
                    f"abs_score={abs_score},abs_bb={abs_bb},dyn_score={dyn_score},dyn_bb={dyn_bb}"
                )
                _b10_hold = int(
                    b10_dyn == 1 and abs_score >= 3
                    and _xr in ("INFLATION_RATE_SHOCK", "STAGFLATION",
                                "FISCAL_GOLD_STRESS", "COMMODITY_LEAD")
                )
                self.log(
                    f"DUR_B10_PICK_D0,{self.time.date()},"
                    f"raw={abs_bb},b10_hold={_b10_hold},"
                    f"score={abs_score},b10z={b10z:.3f},"
                    f"x={_xr},ptype={_ptp},regime={_rg},ps={_ps},ids={_ids}"
                )
            # [DUR-D0H-fix2] Call repair diag here, not from DAILYCycle.
            # DYN_THRESH_D0 is present on OFF-days; repair diag co-locates with it.
            if getattr(self, "dur_repair_d0_enable", False):
                self._EmitDurRepairDiag()
            if getattr(self, "shadow_nav_en", False):  # [SHADOW-NAV]
                self._EmitShadowNavDiffDiag()
        except Exception as e:
            if self.live_mode or getattr(self, "debug_regime", False):
                self.log(f"[DYN_THRESH_D0] error: {e}")

    # [DUR-D0H] Repair / hysteresis diagnostic -- zero trading impact.
    # Shadows 3 repair candidates alongside fast (current) repair.
    # Must be called AFTER _EmitRateShockGateDiag + _EmitDynamicThresholdDiag.
    _DUR_GUARD_REGIMES = frozenset({
        "INFLATION_RATE_SHOCK", "STAGFLATION",
        "FISCAL_GOLD_STRESS", "COMMODITY_LEAD"
    })

    def _EmitDurRepairDiag(self):
        if not getattr(self, "dur_repair_d0_enable", False):
            return
        # No log_enable gate -- no history() call, cheap on all days.
        # Must emit on OFF-days to capture the repair transition.
        try:
            # --- Inputs from upstream diag ---
            raw_on      = bool(getattr(self, "_dur_bonds_broken", False))
            prev_raw_on = bool(getattr(self, "_dur_repair_prev_raw", False))  # [DUR-AUDIT]
            abs_score = int(getattr(self, "_dur_score", 0))
            dyn_score = int(getattr(self, "_dur_dyn_score", 0))
            dyn_bb    = int(getattr(self, "_dur_dyn_bonds_broken", False))
            dyn_sm    = int(getattr(self, "_dur_dyn_smoothed", False))
            b20_abs   = int(getattr(self, "_dur_b20_abs", False))
            b20_dyn   = int(getattr(self, "_dur_b20_dyn", False))
            b5_abs    = int(getattr(self, "_dur_b5_abs",  False))
            b5_dyn    = int(getattr(self, "_dur_b5_dyn",  False))
            b20_bl60  = int(getattr(self, "_dur_b20_blind60", False))
            b5_bl60   = int(getattr(self, "_dur_b5_blind60",  False))

            xregime   = str(getattr(self, "_xregime_cache", {}).get("xregime", "NA"))
            ps_type   = str(getattr(self, "_prev_raw_ptype", "NA"))
            ps_state  = str(getattr(self, "_panic_state", "NORMAL"))
            ids_state = str(getattr(self, "_ids_state", "NORMAL"))
            cur_mode  = str(getattr(self, "_dur_shadow_mode", "NORMAL"))
            regime    = str(self.current_regime or "UNK")

            # --- candidate_repair_fast: mirrors raw (current behaviour) ---
            abs_bb = int(raw_on)

            # off_reason: why fast turned OFF this bar (if applicable)
            prev_fast = bool(getattr(self, "_dur_rfast_prev", False))
            if prev_fast and not raw_on:
                off_reason = f"score{abs_score}" if abs_score < 4 else "cond_fail"
            elif raw_on:
                off_reason = "on"
            else:
                off_reason = "none"
            self._dur_rfast_prev = raw_on

            # --- candidate_repair_h2: OFF only after 2 consecutive low-score days ---
            h2_state = bool(getattr(self, "_dur_h2_state", False))
            h2_low   = int(getattr(self,  "_dur_h2_low_count", 0))
            if raw_on:
                h2_state = True
                h2_low   = 0
            elif h2_state:
                h2_low += 1
                if h2_low >= 2:
                    h2_state = False
                    h2_low   = 0
            self._dur_h2_state      = h2_state
            self._dur_h2_low_count  = h2_low
            c_h2 = int(h2_state)

            # --- candidate_repair_regime_guard: stay ON while stress xregime active ---
            guard_regimes = getattr(self, "_DUR_GUARD_REGIMES", frozenset({  # [DUR-D0H-fix3]
                "INFLATION_RATE_SHOCK", "STAGFLATION",
                "FISCAL_GOLD_STRESS", "COMMODITY_LEAD"
            }))
            rg_state = bool(getattr(self, "_dur_rg_state", False))
            if raw_on:
                rg_state = True
            elif rg_state and xregime not in guard_regimes:
                rg_state = False
            self._dur_rg_state = rg_state
            c_rg = int(rg_state)

            # --- candidate_repair_blind60: stay ON while dyn_smoothed active ---
            b60_state = bool(getattr(self, "_dur_b60_state", False))
            if raw_on:
                b60_state = True
            elif b60_state and not dyn_sm:
                b60_state = False
            self._dur_b60_state = b60_state
            c_b60 = int(b60_state)

            # --- hold_days: consecutive days fast repair has been ON ---
            hold = int(getattr(self, "_dur_rfast_hold", 0))
            hold = (hold + 1) if raw_on else 0
            self._dur_rfast_hold = hold

            if getattr(self, "dur_repair_verbose_enable", False):  # [LOG-BUDGET]
                self.log(
                    f"DUR_REPAIR_D0,{self.time.date()},"
                    f"{abs_score},{abs_bb},{dyn_score},{dyn_bb},{dyn_sm},"
                    f"{b20_abs},{b20_dyn},{b5_abs},{b5_dyn},{b20_bl60},{b5_bl60},"
                    f"{xregime},{ps_type},{regime},{ps_state},{ids_state},{cur_mode},"
                    f"{abs_bb},{c_h2},{c_rg},{c_b60},"
                    f"{off_reason},{hold}"
                )
            # [LOG-BUDGET] Gate secondary DUR daily logs to active-signal days only
            _any_active = (raw_on or prev_raw_on
                           or bool(getattr(self, "_dur_c1r_shadow_state", False)))
            # [DUR-D0H] Candidate comparison -- active-signal days only
            if _any_active:
                self.log(
                    f"DUR_REPAIR_PICK_D0,{self.time.date()},"
                    f"raw={abs_bb},h2={c_h2},rg={c_rg},b60={c_b60},"
                    f"score={abs_score},dyn_score={dyn_score},"
                    f"x={xregime},ptype={ps_type},regime={regime},"
                    f"ps={ps_state},ids={ids_state},mode={cur_mode},"
                    f"off={off_reason}"
                )
            # [DUR-B10-D0] Release-confirmation: b10 says recovery clean enough
            _b10_dyn    = int(getattr(self, "_dur_b10_dyn",    False))
            _b10_bl60   = int(getattr(self, "_dur_b10_blind60", False))
            _b10z       = float(getattr(self, "_dur_b10z", 0.0))
            b10_rel_ok  = int(                          # [DUR-B10-D0-fix1]
                abs_bb == 0 and c_h2 == 1 and c_b60 == 0
                and _b10_dyn == 0 and _b10_bl60 == 0
                and ps_state in ("RECOVERY", "NORMAL")
                and regime == "RISK_ON"
            )
            if _any_active:
                self.log(
                    f"DUR_B10_RELEASE_D0,{self.time.date()},"
                    f"release_ok={b10_rel_ok},"
                    f"raw={abs_bb},h2={c_h2},rg={c_rg},b60={c_b60},"
                    f"b10_dyn={_b10_dyn},b10_blind60={_b10_bl60},"
                    f"b10z={_b10z:.3f},score={abs_score},"
                    f"x={xregime},ptype={ps_type},regime={regime},"
                    f"ps={ps_state},ids={ids_state}"
                )
            # [DUR-B10-D0] H2+b10 combined shadow candidate
            h2_b10_hold    = int(c_h2 == 1 and b10_rel_ok == 0)
            h2_b10_release = int(c_h2 == 1 and b10_rel_ok == 1)
            if _any_active:
                self.log(
                    f"DUR_H2_B10_SHADOW_D0,{self.time.date()},"
                    f"shadow_hold={h2_b10_hold},"
                    f"shadow_release={h2_b10_release},"
                    f"release_ok={b10_rel_ok},"
                    f"raw={abs_bb},h2={c_h2},rg={c_rg},b60={c_b60},"
                    f"b10_dyn={_b10_dyn},b10_blind60={_b10_bl60},"
                    f"b10z={_b10z:.3f},score={abs_score},dyn_score={dyn_score},"
                    f"x={xregime},ptype={ps_type},regime={regime},"
                    f"ps={ps_state},ids={ids_state}"
                )
            # [DUR-AUDIT] Save prev raw for next day rebreak detection
            self._dur_repair_prev_raw = raw_on
            # [DUR-AUDIT] DUR_RELEASE_D0: log each confirmed release event
            if b10_rel_ok:
                _b20z_r = float(getattr(self, "_dur_b20z", 0.0))
                _b5z_r  = float(getattr(self, "_dur_b5z",  0.0))
                self._dur_last_release_date = self.time.date()
                self.log(
                    f"DUR_RELEASE_D0,{self.time.date()},"
                    f"score={abs_score},raw={abs_bb},h2={c_h2},rg={c_rg},b60={c_b60},"
                    f"dyn_score={dyn_score},dyn_bb={dyn_bb},"
                    f"b10_dyn={_b10_dyn},b10_bl60={_b10_bl60},"
                    f"b5_dyn={b5_dyn},b5_bl60={b5_bl60},"
                    f"b20z={_b20z_r:.3f},b10z={_b10z:.3f},b5z={_b5z_r:.3f},"
                    f"x={xregime},ptype={ps_type},regime={regime},"
                    f"ps={ps_state},ids={ids_state},mode={cur_mode}"
                )
            # [DUR-AUDIT] DUR_REBREAK_EVENT_D0: new ON after recent release
            if raw_on and not prev_raw_on:
                _lr = getattr(self, "_dur_last_release_date", None)
                if _lr is not None:
                    _dg = (self.time.date() - _lr).days
                    if _dg <= 14:
                        _b20z_rb = float(getattr(self, "_dur_b20z", 0.0))
                        _b5z_rb  = float(getattr(self, "_dur_b5z",  0.0))
                        self.log(
                            f"DUR_REBREAK_EVENT_D0,{self.time.date()},"
                            f"last_rel={_lr},days={_dg},"
                            f"rb3={int(_dg<=4)},rb5={int(_dg<=7)},rb10={int(_dg<=14)},"
                            f"score={abs_score},mode={cur_mode},"
                            f"raw={abs_bb},h2={c_h2},rg={c_rg},b60={c_b60},"
                            f"dyn_score={dyn_score},dyn_bb={dyn_bb},"
                            f"b20z={_b20z_rb:.3f},b10z={_b10z:.3f},b5z={_b5z_rb:.3f},"
                            f"x={xregime},ptype={ps_type},regime={regime},"
                            f"ps={ps_state},ids={ids_state}"
                        )
            # [DUR-C1R] Shadow: block OFF when score >= 4 (min hysteresis rule)
            c1r_shadow = bool(getattr(self, "_dur_c1r_shadow_state", False))
            candidate_off = prev_raw_on and not raw_on
            # [DUR-C1R-EXT] score==3 hold conditions
            _s3_hold = bool(c_h2==1 or c_rg==1 or c_b60==1 or _b10z < 0)
            shadow_off_block = bool(
                candidate_off and (
                    abs_score >= 4
                    or (abs_score == 3 and _s3_hold)
                )
            )
            if raw_on:
                c1r_shadow = True
            elif shadow_off_block:
                c1r_shadow = True   # hold: would-be OFF blocked
            else:
                c1r_shadow = raw_on  # False
            self._dur_c1r_shadow_state = c1r_shadow
            # [C1R-AUDIT] Track root-cause reason for shadow NAV breakdown
            if raw_on:
                self._c1r_block_reason = "on"
            elif shadow_off_block:
                # New block starts: record reason (ge4 or score3_dur)
                self._c1r_block_reason = (
                    "ge4" if (candidate_off and abs_score >= 4)
                    else "s3dur"
                )
            elif not c1r_shadow:
                self._c1r_block_reason = "none"
            # else: "hold" state -- keep existing reason
            # [D0-C1R-S3OFF] Shadow variant: ge4-only, no s3dur blocking
            _s3off_block = bool(candidate_off and abs_score >= 4)
            if raw_on:
                _s3off = True
            elif _s3off_block:
                _s3off = True
            else:
                _s3off = raw_on  # False -- s3dur no longer holds
            self._c1r_s3off_shadow = _s3off
            # Log only when signal is/was active (budget-safe)
            if raw_on or prev_raw_on or c1r_shadow:
                reason = ("block_off_score_ge4" if (candidate_off and abs_score >= 4)
                          else "block_off_score3_dur" if shadow_off_block
                          else "normal_off" if candidate_off
                          else "on" if raw_on else "hold")
                _b20z_c = float(getattr(self, "_dur_b20z", 0.0))
                _b5z_c  = float(getattr(self, "_dur_b5z",  0.0))
                self.log(
                    f"DUR_C1R_SHADOW,{self.time.date()},"
                    f"candidate_off={int(candidate_off)},"
                    f"shadow_off_blocked={int(shadow_off_block)},"
                    f"s3_hold={int(_s3_hold)},"
                    f"score={abs_score},raw={abs_bb},"
                    f"shadow={int(c1r_shadow)},"
                    f"h2={c_h2},rg={c_rg},b60={c_b60},"
                    f"dyn_score={dyn_score},"
                    f"b20z={_b20z_c:.3f},b10z={_b10z:.3f},b5z={_b5z_c:.3f},"
                    f"x={xregime},ptype={ps_type},regime={regime},"
                    f"ps={ps_state},ids={ids_state},"
                    f"mode={cur_mode},reason={reason}"
                )
        except Exception as e:
            self.log(f"DUR_REPAIR_D0_ERROR,{self.time.date()},{type(e).__name__},{e}")  # [DUR-D0H-fix3]
    # [SHADOW-NAV] Daily shadow NAV diff -- diagnostic only, zero trading impact.
    # C1R branch: impact of replacing BND with BIL during c1r_shadow days.
    # BRG branch: impact of NOT blocking SPY on invalid corr_inv days.
    def _EmitShadowNavDiffDiag(self):
        try:
            tv = float(self.portfolio.total_portfolio_value)
            if tv <= 0:
                return

            # BND daily return proxy: 5-day return / 5 (linear approx)
            bnd_daily = float(getattr(self, "_dur_b5_raw", 0.0)) / 5.0

            # SPY daily return from 2-bar history
            spy_daily = 0.0
            try:
                sym_spy = getattr(self, "sym_spy", None)
                if sym_spy:
                    h = self.history(sym_spy, 2, Resolution.DAILY)
                    if h is not None and len(h) >= 2:
                        p0 = float(h["close"].iloc[-2])
                        p1 = float(h["close"].iloc[-1])
                        if p0 > 0:
                            spy_daily = (p1 / p0) - 1.0
            except Exception:
                pass

            # BIL daily return proxy = 0 (directional diagnostic, precision not needed)

            # --- C1R branch ---
            c1r_shadow = bool(getattr(self, "_dur_c1r_shadow_state", False))
            raw_on     = bool(getattr(self, "_dur_bonds_broken", False))
            c1r_active = c1r_shadow and not raw_on

            # [SHADOW-NAV-FIX] C1R shadow measures SPY exposure forgone by staying
            # in broken mode. When c1r_active: actual is buying back SPY (recovery),
            # shadow would hold BIL. Diff = actual_spy_weight x (-spy_daily).
            # Negative c1r_cum = shadow missed SPY rally; positive = shadow protected.
            spy_w = 0.0
            if c1r_active:
                try:
                    _spy_obj = getattr(self, "sym_spy", None)
                    if _spy_obj:
                        _spy_hv = float(self.portfolio[_spy_obj].HoldingsValue or 0)
                        spy_w = max(0.0, _spy_hv / tv)
                except Exception:
                    spy_w = 0.0
            c1r_diff = -spy_w * spy_daily if c1r_active else 0.0

            # [D0-C1R-S3OFF] ge4-only variant: s3dur removed
            s3off_shadow = bool(getattr(self, "_c1r_s3off_shadow", False))
            s3off_active = s3off_shadow and not raw_on
            s3off_spy_w = 0.0
            if s3off_active and not c1r_active:
                # Need SPY weight (c1r_active may not have run)
                try:
                    _sobj = getattr(self, "sym_spy", None)
                    if _sobj:
                        _shv = float(self.portfolio[_sobj].HoldingsValue or 0)
                        s3off_spy_w = max(0.0, _shv / tv)
                except Exception:
                    s3off_spy_w = 0.0
            elif s3off_active:
                s3off_spy_w = spy_w  # already computed above
            s3off_diff = -s3off_spy_w * spy_daily if s3off_active else 0.0

            # --- BRG valid-only branch ---
            brg_corr_inv = bool(getattr(self, "_brg_corr_inv", False))
            brg_freed    = float(getattr(self, "_brg_freed", 0.0))
            brg_diff     = brg_freed * spy_daily if brg_corr_inv else 0.0

            # Accumulate cumulative diffs
            c1r_cum = float(getattr(self, "_shadow_c1r_cum", 0.0)) + c1r_diff
            brg_cum = float(getattr(self, "_shadow_brg_cum", 0.0)) + brg_diff
            self._shadow_c1r_cum = c1r_cum
            self._shadow_brg_cum = brg_cum
            # [C1R-AUDIT] Split by block reason type
            c1r_reason = str(getattr(self, "_c1r_block_reason", "none"))
            c1r_cum_ge4  = float(getattr(self, "_shadow_c1r_cum_ge4",  0.0))
            c1r_cum_s3   = float(getattr(self, "_shadow_c1r_cum_s3",   0.0))
            if c1r_active and c1r_reason == "ge4":  c1r_cum_ge4 += c1r_diff
            elif c1r_active and c1r_reason == "s3dur": c1r_cum_s3  += c1r_diff
            self._shadow_c1r_cum_ge4 = c1r_cum_ge4
            self._shadow_c1r_cum_s3  = c1r_cum_s3
            s3off_cum  = float(getattr(self, "_shadow_s3off_cum", 0.0)) + s3off_diff
            self._shadow_s3off_cum = s3off_cum
            delta_vs_base = s3off_cum - c1r_cum

            self.log(
                f"SHADOW_NAV_DIFF_D0,{self.time.date()},"
                f"c1r_act={int(c1r_active)},spy_w={spy_w:.3f},"
                f"spy_d={spy_daily:.4f},c1r_d={c1r_diff:.4f},c1r_cum={c1r_cum:.4f},"
                f"c1r_rsn={c1r_reason},c1r_cum_ge4={c1r_cum_ge4:.4f},c1r_cum_s3={c1r_cum_s3:.4f},"
                f"s3off_act={int(s3off_active)},s3off_d={s3off_diff:.4f},"
                f"s3off_cum={s3off_cum:.4f},delta={delta_vs_base:.4f},"
                f"brg_inv={int(brg_corr_inv)},brg_freed={brg_freed:.3f},"
                f"brg_d={brg_diff:.4f},brg_cum={brg_cum:.4f}"
            )
        except Exception as e:
            if self.live_mode or getattr(self, "debug_regime", False):
                self.log(f"SHADOW_NAV_DIFF_D0_ERROR,{self.time.date()},{e}")