from AlgorithmImports import *
import numpy as np
import json
import inspect
from datetime import date, datetime, timedelta
from cg_logic import CoreGrowthLogic
from sh_hedge import SHHedgeLogic
from panic_score import PanicScoreLogic
from stress_scenarios import StressScenarioMixin
from cg_market_structure import CoreGrowthMarketStructureMixin
from dyn_thresh_diag import DynamicThresholdDiagMixin
from dyn_alloc_diag import DynamicAllocationDiagMixin
from rr_xsector_diag import RRXSectorDiagMixin          # [RRX]
from cashflow_live import LiveCashFlowMixin
from cg_subscriptions import CoreGrowthSubscriptionMixin  # [E0.1]


class CoreGrowthPlusConditionalTrendSleeve(QCAlgorithm):

    def _ParseDateParam(self, value):
        if not value:
            return None
        try:
            return datetime.strptime(str(value).strip(), "%Y-%m-%d").date()
        except Exception:
            return None

    def _LogAllowedAt(self, dt=None) -> bool:
        if not getattr(self, "log_enable", True):
            return False
        try:
            cur = dt.date() if (dt and hasattr(dt, "date")) else self.time.date()
        except Exception:
            return True
        start = getattr(self, "log_start_date", None)
        end   = getattr(self, "log_end_date",   None)
        if start is not None and cur < start:
            return False
        if end   is not None and cur > end:
            return False
        return True

    def log(self, message) -> None:  # type: ignore[override]
        if not self._LogAllowedAt(): return
        s = str(message)
        if "Runtime Error" not in s and "Traceback" not in s and not s.startswith(("[INIT]","[EOA]")):
            o = getattr(self, "log_only_prefixes", ())
            if o and not any(s.startswith(p) for p in o): return
            m = getattr(self, "log_mute_prefixes", ())
            if m and any(s.startswith(p) for p in m): return
        super().log(message)

    def debug(self, message) -> None:  # type: ignore[override]
        if not self._LogAllowedAt(): return
        s = str(message)
        o = getattr(self, "log_only_prefixes", ())
        if o and not any(s.startswith(p) for p in o): return
        m = getattr(self, "log_mute_prefixes", ())
        if m and any(s.startswith(p) for p in m): return
        super().debug(message)

    def Initialize(self):
        if not self.live_mode:
            self.set_start_date(2012, 1, 1)
            self.set_end_date  (2026, 7, 1)
            self.set_cash(10000)

        try:
            from rrx_params import RRX_PARAMS
            self._rrx_param_overrides = RRX_PARAMS
        except Exception:
            self._rrx_param_overrides = getattr(self, "_rrx_param_overrides", {}) or {}
        def _param(k):
            v = self.get_parameter(k)
            if v is None or str(v).strip() == "":
                v = self._rrx_param_overrides.get(k)
            return v

        self.force_rebalance_date = self._ParseDateParam(_param("force_rebalance_date")) or date(2026,5,29)

        # [E0.5.1] Parse fast-baseline flag before any diagnostic init that reads it.
        self.cg_fast_baseline_mode = str(_param("cg_fast_baseline_mode") or "1").strip().lower() in ("1","true","yes","on")
        self._cg_fast_disabled = []

        self._CgBuildTradableExtra()  # [E0.1] before any equity subscription

        self.set_brokerage_model(BrokerageName.INTERACTIVE_BROKERS_BROKERAGE)

        self.log_enable     = str(_param("log_enable") or "1").strip().lower() not in ("0","false","no","off")
        self.log_start_date = self._ParseDateParam(_param("log_start_date"))
        self.log_end_date   = self._ParseDateParam(_param("log_end_date"))
        if self.log_start_date and self.log_end_date and self.log_start_date > self.log_end_date:
            self.log_start_date, self.log_end_date = self.log_end_date, self.log_start_date
        _lp = lambda k: [x.strip() for x in str(_param(k) or "").replace("|",",").split(",") if x.strip()]
        self.log_only_prefixes = _lp("log_only_prefixes")
        self.log_mute_prefixes = _lp("log_mute_prefixes")
        self.plot_enable = str(
            self.get_parameter("plot_enable") or "1"
        ).strip().lower() in ("1","true","yes","on")
        if self.cg_fast_baseline_mode and self.plot_enable:  # [E0.5.1]
            self.plot_enable = False
            self._cg_fast_disabled.append("plot_enable")

        self.testFloat = float(self.get_parameter("testFloat") or 1.3)

        self.base_spy_weight = 0.85
        self.max_spy_leverage = 1.3
        self.bootstrap_spy_cap_enable    = True
        self.bootstrap_spy_cap           = 1.50
        self.bootstrap_spy_cap_days      = 5
        self.leverage_confirm_days = 10
        self.dd_soft_start = 0.11
        self.dd_hard_end = 0.16
        self.dd_clamp_confirm_lookback = 2
        self.dd_clamp_confirm_improvement = 0.0001
        self.dd_recovery_gross_ref = 1.5
        self.stress_spy_cap = 0.05

        self.overlay_dd_stress_soften_enable = True
        self.overlay_dd_stress_blend         = 0.50 # accepted: 0.65 > 0.60 > 0.50 on all metrics

        self.cooldown_fast_exit_enable = True

        self.watch_tail_spy_dampen_enable  = True
        self.watch_tail_score_threshold    = 0.20
        self.watch_tail_spy_multiplier     = 0.20

        self.post_panic_brake_enable          = False
        self.post_panic_brake_days            = 3
        self.post_panic_spy_multiplier        = 0.20

        self.rebalance_shock_threshold = 0.02

        self.spy_shock_1d_threshold  = 3.3
        self.spy_shock_3d_threshold  = float(self.get_parameter("spy_shock_3d_threshold") or 3.4)
        self.spy_shock_5d_threshold  = float(self.get_parameter("spy_shock_5d_threshold") or 3.3)
        self.spy_shock_scale_1d      = float(self.get_parameter("spy_shock_scale_1d") or 0.60)
        self.spy_shock_scale_3d      = float(self.get_parameter("spy_shock_scale_3d") or 0.40)
        self.spy_shock_scale_5d      = float(self.get_parameter("spy_shock_scale_5d") or 0.80)

        self.short_shock_1d_threshold = float(self.get_parameter("short_shock_1d_threshold") or 2.0)
        self.short_shock_2d_threshold = float(self.get_parameter("short_shock_2d_threshold") or 4.0)
        self.short_shock_3d_threshold = float(self.get_parameter("short_shock_3d_threshold") or 4.2)

        self.neutral_decay_days = 20
        self.neutral_decay_factor = 0.90
        self.min_weight_delta = 0.02
        self.trade_cooldown_days = 1
        self.min_trade_value = 100
        self.min_trade_value_perc = 0.12
        self.panic_trigger_pct = 0.07
        self.panic_window_days = 7
        self.max_days_no_core_rebalance = 45
        self.max_days_no_overlay_rebalance = 7
        self.vix_fg_lookback = 252
        self.vix_low_pct = 0.35
        self.vix_high_pct = 0.75
        self.crash_ticker = str(self.get_parameter("crash_ticker") or "SGOV").upper()
        self.crash_weight = 0.50
        self.max_cr_cash_weight = 0.40
        self.neutral_cr_cash_weight = 0.05
        self.min_cash_anchor_overlay = 0.10
        self.yc_duration_ok_min = 0.25
        self.panic_block_max = 0.30
        self.panic_block_from_spy_frac = 0.75
        self.panic_mom_lookback = 10
        self.panic_mom_threshold = 0.01

        self.tactical_permission_matrix = {
            "COMMODITY_INFL":       ["XLE", "XLB", "XLV", "XLU"],
            "COMMODITY_LEAD":       ["XLE", "XLB", "XLV", "XLU"],
            "BOND_HEDGED_RISK_OFF": ["XLV", "XLU", "GLDM"],
            "FISCAL_USD":           ["XLV", "XLU", "GLDM"],
            "STAGFLATION_SAFE":     ["XLV", "XLU", "GLDM", "XLB"],
            "DEFL_RECESSION":       ["XLV", "XLU", "GLDM"],
            "RATE_SHOCK_UNKNOWN":   ["XLV", "XLU"],
            "UNKNOWN":              ["XLE", "XLB", "XLV", "XLU"],
        }
        self.tactical_permission_enable = True

        self.panic_struct_diag_enable = str(
            self.get_parameter("panic_struct_diag_enable") or "0"
        ).strip().lower() in ("1","true","yes","on")
        self.panic_struct_lookback          = 20
        self.panic_struct_corr_window       = 20
        self.pstruct_spy_stress_20d         = -0.05
        self.pstruct_gld_up_20d             =  0.04
        self.pstruct_bnd_down_20d           = -0.01
        self.pstruct_gld_bnd_ratio_up_20d   =  0.05
        self.pstruct_tip_bnd_inflation_min  =  0.005
        self.pstruct_dbc_spy_commodity_min  =  0.03
        self.pstruct_corr_positive_min      =  0.10
        self.pstruct_bnd_positive_min       =  0.005
        self.pstruct_commodity_strong_min   =  0.08
        self.pstruct_inflation_strong_min   =  0.015
        self.pstruct_vol_mult = float(self.get_parameter("pstruct_vol_mult") or 0.9)
        self.panic_recovery_min_days = 5
        self.panic_recovery_max_days = 15
        self.shock_tactical_block_frac = 0.35
        self.tactical_min_hold_days    = 10
        self.tactical_atr_exit_enable   = True
        self.tactical_atr_len           = 18
        self.tactical_atr_trail_mult    = float(self.get_parameter("tactical_atr_trail_mult") or 6.0)
        self.tactical_atr_min_hold_days = 3
        self.tactical_atr_arm_profit = float(self.get_parameter("tactical_atr_arm_profit") or 0.015)
        self.tactical_sharp_exit_enable    = True
        self.tactical_sharp_atr_mult       = float(self.get_parameter("tactical_sharp_atr_mult") or 1.3)
        self.tactical_sharp_weak_score_min = 3
        self.tactical_slow_exit_enable     = True
        self.dur_c1a_enable = str(self.get_parameter("dur_c1a_enable") or "0").strip().lower() in ("1","true","yes","on")
        self.dur_c1b_variant = max(0,min(3,int(self.get_parameter("dur_c1b_variant") or 0)))
        self.core_ballast_c0_enable=str(self.get_parameter("core_ballast_c0_enable")or"0").strip().lower()in("1","true","yes","on")
        self.core_ballast_c0_spy_threshold=float(self.get_parameter("core_ballast_c0_spy_threshold")or 0.85)
        _heavy_diag = str(
            self.get_parameter("heavy_diag_enable") or "0"
        ).strip().lower() in ("1","true","yes","on")
        self.dyn_thresh_d0_enable = _heavy_diag
        self.dur_repair_d0_enable = _heavy_diag
        self.shadow_nav_en        = _heavy_diag
        self.dyn_thresh_d0_log_enable=self.dur_b10_d0_enable=False;self.log_quiet_mode=True
        self.tactical_block_same_symbol_enable = True
        self._tactical_blocked_symbol = None
        self._tactical_block_until    = None
        self._tactical_block_reason   = None

        self.tactical_reset_enable            = True
        self.tactical_reset_min_hold_days     = 3
        self.tactical_reset_dd_worsen         = 0.015
        self.tactical_reset_abs_loss          = -0.020
        self.tactical_reset_spy_underperf     = -0.005
        self.tactical_reset_cooldown_days     = 5
        self.tactical_reset_require_active_dd = True

        self.tactical_cleanup_on_winner_change = True
        self.tac_hold_enable = str(self.get_parameter("tac_hold_enable") or "0").strip().lower() in ("1","true","yes","on")
        self.tac_hold_assets         = {"XLE", "XLB"}
        self.tac_hold_ptypes         = {"COMMODITY_INFL", "COMMODITY_LEAD", "RATE_SHOCK_UNKNOWN"}
        self.tac_hold_max_days       = int(float(self.get_parameter("tac_hold_max_days") or 30))
        self.tac_hold_dbc_spy_min    = float(self.get_parameter("tac_hold_dbc_spy_min") or 0.08)
        self.tac_hold_min_current_weight = 0.010
        self.tac_hold_max_weight         = 0.250
        self.tac_hold_tip_bnd_min        = 0.000
        self.tac_hold_symbol_5d_min      = -0.060
        self.tac_hold_symbol_10d_min     = -0.080
        self.xle_noise_d0_enable=bool(int(self.get_parameter("xle_noise_d0_enable")or 0))
        self.xle_noise_veto_enable=bool(int(self.get_parameter("xle_noise_veto_enable")or 0))
        self.bear_rally_gate_enable=str(self.get_parameter("bear_rally_gate_enable")or"1").strip().lower()in("1","true","yes","on")
        self.bear_rally_corr_min=float(self.get_parameter("bear_rally_corr_min")or 0.25)
        self.bear_rally_dbc_spy_max=float(self.get_parameter("bear_rally_dbc_spy_max")or 0.0)
        self.bear_rally_min_add=float(self.get_parameter("bear_rally_min_add")or 0.005)
        self.bear_rally_rate_corr_min=float(self.get_parameter("bear_rally_rate_corr_min")or 0.50)
        self.bear_rally_rate_bnd20_max=float(self.get_parameter("bear_rally_rate_bnd20_max")or-0.015)
        self.bear_rally_rate_spy20_max=float(self.get_parameter("bear_rally_rate_spy20_max")or 0.0)

        self.tactical_stability_enable        = True
        self.tactical_stability_lookback      = 20
        self.tactical_stability_max_1d_drop   = -0.035
        self.tactical_stability_max_3d_drop   = -0.060
        self.tactical_stability_last_5d_min   = -0.020

        self.dd_cb_enable           = True
        self.dd_cb_threshold        = 0.15
        self.dd_cb_cooldown_days    = 1
        self.dd_cb_min_days_between = 1

        self._dd_cb_active          = False
        self._dd_cb_trigger_date    = None   # date of last CB fire
        self._dd_cb_resume_date     = None   # date cooldown expires
        self._dd_cb_count           = 0
        self._dd_cb_local_peak      = None
        self._dd_cb_dd_at_trigger   = None

        self.def_tilt_enable               = True
        self.def_tilt_budget               = 0.25
        self.def_tilt_lookback             = 10
        self.def_tilt_min_score            = 0.00
        self.def_tilt_trend_ma_period      = 60
        self.def_tilt_max_single_add       = 0.50
        self.def_tilt_skip_cash_as_winner  = False

        self.panic_tactical_universe = []
        for t in ["XLE", "XLV", "XLU", "XLB", "GLDM"]:  # [TPERM] full universe; matrix gates per regime
            try:
                sym = self._CgAddEquity(t).Symbol
                self.panic_tactical_universe.append(sym)
            except Exception:
                continue

        self.vol_lookback = 60
        self.target_vol_annual = 0.18
        self.min_realized_vol = 0.08
        self.max_realized_vol = 0.35
        self.min_vol_leverage = 0.6
        self.max_vol_leverage = 1.6
        self.trend_sleeve_weight = 0.05
        self.trend_band = 0.01
        self.trend_state_in_spy = True
        self.trend_enable_realized_vol = 0.18
        self.trend_enable_vix_pct = 0.70
        self.trend_sleeve_weight_cap = 0.30
        self.regime_min_persist_days = 3

        self.sym_spy   = self._CgAddEquity("SPY").Symbol
        self.sym_gld   = self._CgAddEquity("GLD").Symbol
        self.sym_bnd   = self._CgAddEquity("BND").Symbol
        self.sym_tip   = self._CgAddEquity("TIP").Symbol
        self.sym_dbc   = self._CgAddEquity("DBC").Symbol
        self.sym_cash  = self._CgAddEquity("BIL").Symbol
        self.sym_crash = self._CgAddEquity(self.crash_ticker).Symbol
        self.sym_sh    = self._CgAddEquity("SH").Symbol

        self.ids_enable              = True
        self.ids_thr_watch           = 0.35
        self.ids_thr_stress          = 0.60
        self.ids_thr_panic_short     = 0.85
        self.ids_stress_entry_confirm = 2
        self.ids_min_components_entry = 2
        self.ids_watch_hold_minutes  = 30
        self.ids_stress_hold_minutes = 120
        self.ids_release_decay_alpha = 0.20
        self.ids_watch_hedge_frac    = 0.20
        self.ids_stress_hedge_frac   = 0.40
        self.ids_panic_hedge_frac    = 0.60
        self.ids_watch_spy_cap       = 0.75
        self.ids_stress_spy_cap      = 0.50
        self.ids_panic_spy_cap_risk_on  = 0.30
        self.ids_panic_spy_cap_neutral  = 0.35
        self.ids_panic_spy_cap_risk_off = 0.15
        self.ids_watch_gross_cap     = 1.40
        self.ids_stress_gross_cap    = 1.20
        self.ids_panic_gross_cap     = 0.90
        self._ids_active        = False
        self._ids_state         = "NORMAL"
        self._ids_score         = 0.0
        self._ids_peak_score    = 0.0
        self._ids_reason        = None
        self._ids_set_time      = None
        self._ids_last_update   = None
        self._ids_release_after = None
        self._ids_diag_date     = None

        self.sh_mode = str(self.get_parameter("sh_mode") or "SPY_CUT_ONLY").upper()  # FULL | SPY_CUT_ONLY
        self.SHInitialize()

        self.DynAllocInitialize()  # [DYN_ALLOC_D0]
        self.RRXInitialize()       # [RRX]

        self.active_symbols = set([
            self.sym_spy, self.sym_gld, self.sym_bnd, self.sym_tip,
            self.sym_dbc,
            self.sym_cash, self.sym_crash, self.sym_sh,
            ] + self.panic_tactical_universe
              + getattr(self, "rr_active_symbols",  [])
              + getattr(self, "rrx_active_symbols", []))  # [RRX]

        self.cash_gate_ma    = self.sma(self.sym_cash, 80, Resolution.DAILY)
        self.trend_ma    = self.sma(self.sym_spy, 160, Resolution.DAILY)
        self.spy_ema_75  = self.ema(self.sym_spy, 75,  Resolution.DAILY)
        self.spy_sma_200 = self.sma(self.sym_spy, 200, Resolution.DAILY)
        self.spy_ema_9  = self.ema(self.sym_spy, 9,  Resolution.DAILY)
        self.spy_ema_120 = self.ema(self.sym_spy, 120, Resolution.DAILY)

        self.def_ma = {
            self.sym_tip:  self.sma(self.sym_tip,  self.def_tilt_trend_ma_period, Resolution.DAILY),
            self.sym_gld:  self.sma(self.sym_gld,  self.def_tilt_trend_ma_period, Resolution.DAILY),
            self.sym_bnd:  self.sma(self.sym_bnd,  self.def_tilt_trend_ma_period, Resolution.DAILY),
            self.sym_cash: self.sma(self.sym_cash, self.def_tilt_trend_ma_period, Resolution.DAILY),}

        self.daily_snap           = self.live_mode
        self.debug_regime         = False
        self._last_regime_diag_date = None

        self.vix = self.add_data(Fred, "VIXCLS", Resolution.DAILY).Symbol
        self.yc  = self.add_data(Fred, "T10Y3M", Resolution.DAILY).Symbol

        self._CgSubscriptionAudit()  # [E0] subscription integrity check
        self._CgDiagGuardStartupLog()  # [E0.4] diagnostic trade guard status

        # [E0.5.1] Fast-baseline overrides were applied at each flag's own source
        # (before its expensive diagnostic init); report what was actually forced off.
        if self.cg_fast_baseline_mode:
            self.log(f"[INIT] CG_FAST_BASELINE mode=1 disabled={','.join(self._cg_fast_disabled)}")

        self.current_regime    = None
        self.regime_start_date = None
        self.prev_regime       = None

        self.panic_mode_active   = False
        self.panic_end_date      = None
        self.last_panic_end_date = None
        self.last_panic_winner   = None
        self._tactical_winner_set_date = None
        self._tactical_exit_lock_active       = False
        self._tactical_exit_lock_date         = None
        self._active_tactical_symbol          = None
        self._tactical_entry_date             = None
        self._tactical_peak_close_since_entry = None
        self._tactical_entry_dd               = None
        self._tactical_entry_price            = None
        self._tactical_entry_spy_price        = None
        self._tactical_reset_hold_until       = None
        self._tactical_reset_count            = 0
        self._tactical_last_reset_symbol      = None
        self._tactical_last_reset_date        = None
        self._stale_tactical_to_zero          = set()
        self.latent_structure_type          = "UNKNOWN"
        self.latent_structure_prev_type     = "UNKNOWN"
        self.latent_structure_since         = None
        self.latent_structure_pending_type  = None
        self.latent_structure_pending_count = 0
        self.latent_structure_entry_type    = None
        self.latent_structure_mismatch_days = 0

        self.portfolio_peak    = 0.0
        self._peak_initialized = False

        self.last_trade_date        = None
        self._bootstrap_trade_count = 0          # [BSC]
        self.last_core_rebalance    = None
        self.last_overlay_rebalance = None

        self.overlay_shock_flag = False

        self.short_shock_flag       = False
        self._short_shock_set_date  = None
        self.short_shock_decay_days = 1

        self.sh_profit_signal_threshold = 0.007    # [SH_PROFIT_SIGNAL] min sh_move  profitable exit
        self.sh_profit_spy_scale        = 0.60     # [SH_PROFIT_SIGNAL] scale   restore_spy   

        self._sh_profit_exit_signal     = False    # [SH_PROFIT_SIGNAL]

        self._last_core_targets    = {}
        self._last_overlay_targets = {}

        try:
            self.capital_cap = float(self.get_parameter("capital_cap") or 0)
        except Exception:
            self.capital_cap = 0.0

        if self.live_mode:
            self.emergency_dd_limit              = 0.15
        else:
            self.emergency_dd_limit              = 0.25

        self.emergency_stop_triggered        = False
        self.emergency_liquidation_executed  = False

        self.previous_equity = None

        self._dd_history = []

        self._diag = {}  # [DIAG]

        self.shadow_diag_enable  = False  # [SHADOW] enable only for ownership analysis runs
        self.shadow_diag_version = "v1"   # [SHADOW]
        self._shadow             = {}     # [SHADOW]

        self._daily_returns = []  # [(date, return)] for worst-5% summary
        self._live_state_loaded = False; self._last_good_equity = None  # [LSS1]
        self._state_save_ok = True  # [LSS2]
        self._snap_anomaly_active = False  # [LSS1]

        self.PanicScoreInitialize()

        self.StressInitialize()
        self.MsInitialize()  # [MS]

        self._STATE_KEY = "cg_v38_live_state"
        self.LiveCashFlowInitialize()

        self._LoadState()
        if self.live_mode:
            self._LFC_ReconcileHoldings()  # [LFC-RECON] log holdings diff on restart

        self.set_warm_up(200)

        portfolio_chart = Chart("portfolio")
        portfolio_chart.add_series(Series("Equity",   SeriesType.LINE, 0))
        portfolio_chart.add_series(Series("Drawdown", SeriesType.LINE, 1))
        self.add_chart(portfolio_chart)

        regime_chart = Chart("Regime")
        regime_chart.add_series(Series("RegimeCode", SeriesType.LINE, 0))
        regime_chart.add_series(Series("TrendState", SeriesType.LINE, 0))
        self.add_chart(regime_chart)

        vol_chart = Chart("Volatility")
        vol_chart.add_series(Series("RealizedVolApprox", SeriesType.LINE, 0))
        vol_chart.add_series(Series("VixPercentile",     SeriesType.LINE, 0))
        self.add_chart(vol_chart)

        self.schedule.on(
            self.date_rules.every_day(self.sym_spy),
            self.time_rules.after_market_open(self.sym_spy, 15),
            self.DAILYCycle)

        self.debug(
            f"[INIT] CoreGrowth_v3.8 | LiveMode={self.live_mode} | "
            f"cr_cash={self.crash_ticker} | capital_cap={self.capital_cap}")


    def _TrackTacticalEntry(self, winner):
        """Record new tactical winner for ATR tracking."""
        if getattr(self, "_active_tactical_symbol", None) != winner:
            self._active_tactical_symbol          = winner
            self._tactical_entry_date             = self.time.date()
            self._tactical_peak_close_since_entry = float(self.securities[winner].price)
            try:
                spy_px = float(self.securities[self.sym_spy].price)
                self._tactical_entry_price     = self._tactical_peak_close_since_entry
                self._tactical_entry_spy_price = spy_px if spy_px > 0 else None
            except Exception:
                self._tactical_entry_price     = None
                self._tactical_entry_spy_price = None
            self._tactical_entry_dd = float(self.CurrentDrawdown())
            if self.live_mode:
                self.log(
                    f"[TACTICAL_ENTRY] sym={winner.Value} date={self.time.date()} "
                    f"px={self._tactical_peak_close_since_entry:.2f} "
                    f"dd={self._tactical_entry_dd:.4f}")

    def TacticalAtrExitTriggered(self, sym) -> bool:
        """Return True if ATR trailing stop is breached for active tactical winner."""
        entry_date = getattr(self, "_tactical_entry_date", None)
        if entry_date is None:
            return False
        held = (self.time.date() - entry_date).days
        if held < int(getattr(self, "tactical_atr_min_hold_days", 3)):
            return False
        entry_px = getattr(self, "_tactical_entry_price", None)
        atr_len = int(getattr(self, "tactical_atr_len", 14))
        try:
            hist = self.history(sym, atr_len + 5, Resolution.DAILY)
            if hist.empty or len(hist) < atr_len + 1:
                return False
            hi = hist["high"].values.astype(float)
            lo = hist["low"].values.astype(float)
            cl = hist["close"].values.astype(float)
            trs = [max(hi[i]-lo[i], abs(hi[i]-cl[i-1]), abs(lo[i]-cl[i-1]))
                   for i in range(1, len(cl))]
            atr   = float(np.mean(trs[-atr_len:]))
            latest = cl[-1]
            peak  = getattr(self, "_tactical_peak_close_since_entry", None)
            if peak is None or latest > peak:
                self._tactical_peak_close_since_entry = latest
                peak = latest
            arm_profit = float(getattr(self, "tactical_atr_arm_profit", 0.015))
            if entry_px is not None and float(entry_px) > 0:
                peak_profit = float(peak) / float(entry_px) - 1.0
                if peak_profit < arm_profit:
                    if self.debug_regime:
                        self.log(
                            f"[TACTICAL_ATR_WAIT] sym={sym.Value} held={held}d "
                            f"entry={entry_px:.2f} peak={peak:.2f} latest={latest:.2f} "
                            f"peak_profit={peak_profit:.2%} arm={arm_profit:.2%}")
                    return False
            stop = peak - float(getattr(self, "tactical_atr_trail_mult", 2.0)) * atr
            if latest < stop:
                self._last_tactical_exit_reason = "ATR_TRAIL"                   # [H15]
                self.log(
                    f"[TACTICAL_ATR_EXIT] sym={sym.Value} cl={latest:.2f} "
                    f"peak={peak:.2f} atr={atr:.2f} stop={stop:.2f} held={held}d")
                return True
            if getattr(self, "tactical_sharp_exit_enable", True):
                if self.TacticalExitDropConfirmed(sym, lookback_days=20, min_drop=0.00, use_sharp=True):
                    self._last_tactical_exit_reason = "SHARP"                    # [H16]
                    self.log(f"[TACTICAL_SHARP_EXIT] sym={sym.Value} held={held}d")
                    return True
        except Exception:
            return False
        return False

    def _SetTacticalReentryBlock(self, sym, reason: str, days: int = None) -> None:
        """[TAC_BLOCK] Block only the exited symbol from re-entry for cooldown period."""
        if sym is None or not getattr(self, "tactical_block_same_symbol_enable", True):
            return
        cd = int(days if days is not None else getattr(self, "tactical_reset_cooldown_days", 5))
        self._tactical_blocked_symbol = sym
        self._tactical_block_until    = self.time.date() + timedelta(days=cd)
        self._tactical_block_reason   = str(reason or "UNKNOWN")
        self._tactical_reset_hold_until = self._tactical_block_until
        self._tactical_exit_lock_active = True
        self._tactical_exit_lock_date   = self.time.date()
        self.log(f"[TAC_BLOCK_SET] {self.time.date()} {sym.Value} reason={self._tactical_block_reason} until={self._tactical_block_until}")

    def _IsTacticalSymbolBlocked(self, sym) -> bool:
        """[TAC_BLOCK] True only if sym is the blocked symbol within its window."""
        if sym is None or not getattr(self, "tactical_block_same_symbol_enable", True):
            return False
        blocked = getattr(self, "_tactical_blocked_symbol", None)
        until   = getattr(self, "_tactical_block_until", None)
        if blocked is None or until is None:
            return False
        if self.time.date() > until:
            self._tactical_blocked_symbol = None
            self._tactical_block_until    = None
            self._tactical_block_reason   = None
            self._tactical_exit_lock_active    = False
            self._tactical_reset_hold_until    = None
            return False
        return sym == blocked

    def _TacticalExitFreedDestination(self, sym, reason: str):
        """[FREED_DEST] Regime-aware freed tactical weight destination."""
        try:
            regime = str(getattr(self, "current_regime", "UNKNOWN") or "UNKNOWN")
            ps     = str(getattr(self, "_panic_state", "NORMAL") or "NORMAL")
            stress = (
                float(self.CurrentDrawdown()) > float(getattr(self, "dd_soft_start", 0.05))
                or bool(getattr(self, "panic_mode_active", False))
                or bool(getattr(self, "short_shock_flag", False))
                or ps in ("WATCH", "STRESS", "PANIC")
                or bool(getattr(self, "_ids_active", False)))
            if stress or regime == "RISK_OFF":
                return self.sym_cash
            entry_px  = getattr(self, "_tactical_entry_price", None)
            entry_spy = getattr(self, "_tactical_entry_spy_price", None)
            cur_px    = float(self.securities[sym].price)
            cur_spy   = float(self.securities[self.sym_spy].price)
            if (entry_px is None or entry_spy is None
                    or float(entry_px) <= 0 or float(entry_spy) <= 0
                    or cur_px <= 0 or cur_spy <= 0):
                return self.sym_cash
            tac_ret = cur_px  / float(entry_px)  - 1.0
            spy_ret = cur_spy / float(entry_spy) - 1.0
            rel_ret = tac_ret - spy_ret
            spy_ok = (
                regime == "RISK_ON"
                or (regime == "NEUTRAL" and bool(getattr(self, "trend_state_in_spy", False))))
            if not spy_ok or spy_ret <= 0:
                return self.sym_cash
            reason_u = str(reason or "").upper()
            if reason_u in ("SLOW", "LOOKBACK_WEAK", "ABS_LOSS_AND_SPY_UNDERPERF", "RESET"):
                return self.sym_spy if rel_ret < 0 else self.sym_cash
            if reason_u in ("ATR_TRAIL", "ATR"):
                return self.sym_spy if regime == "RISK_ON" else self.sym_cash
        except Exception:
            pass
        return self.sym_cash

    def _FinalizeTacticalExit(self, targets: dict, sym, cooldown: bool = True,
                               reason: str = "UNKNOWN", freed_weight_override=None,
                               redirect_freed: bool = True) -> dict:
        """[PERF_EXIT] Unified state cleanup for all hard tactical exits. Caller logs."""
        targets = dict(targets)
        old_w = float(freed_weight_override if freed_weight_override is not None else targets.get(sym, 0.0))
        targets[sym] = 0.0
        if redirect_freed and old_w > 0:
            dest = self._TacticalExitFreedDestination(sym, reason)
            targets[dest] = float(targets.get(dest, 0.0)) + old_w
            self.log(
                f"[FREED_DEST] {self.time.date()} {sym.Value} "
                f"reason={reason} freed={old_w:.4f} dest={dest.Value} "
                f"regime={getattr(self,'current_regime','?')} ps={getattr(self,'_panic_state','?')}")
        _is_active = sym == getattr(self, "_active_tactical_symbol", None)
        _is_last   = sym == getattr(self, "last_panic_winner", None)
        if _is_active:
            self._active_tactical_symbol          = None
            self._tactical_entry_date             = None
            self._tactical_entry_dd               = None
            self._tactical_entry_price            = None
            self._tactical_entry_spy_price        = None
            self._tactical_peak_close_since_entry = None
        if _is_last:
            self.last_panic_winner = None
        if _is_active or _is_last:
            self._tactical_winner_set_date = None
        if hasattr(self, "_stale_tactical_to_zero"):
            self._stale_tactical_to_zero.discard(sym)
        if cooldown:
            cd = int(getattr(self, "tactical_reset_cooldown_days", 5))
            self._SetTacticalReentryBlock(sym, reason, cd)
        return targets

    def ApplyTacticalAtrExit(self, w: dict) -> dict:
        if not getattr(self, "tactical_atr_exit_enable", True):
            return w
        sym = getattr(self, "_active_tactical_symbol", None)
        if sym is None:
            return w
        if not self.TacticalAtrExitTriggered(sym):
            return w
        reason = getattr(self, "_last_tactical_exit_reason", "ATR_TRAIL")  # [H15/H16]
        self._last_tactical_exit_reason = None
        old_target = float(w.get(sym, 0.0))
        self.log(f"[TACTICAL_ATR_FORCE_EXIT] {self.time.date()} {sym.Value} reason={reason} w={old_target:.3f}")
        return self._FinalizeTacticalExit(w, sym, cooldown=True, reason=reason)


    def _SaveState(self):
        if not self.live_mode:
            return
        try:
            def _d(dt):
                if dt is None: return None
                return dt.isoformat()

            winner_ticker = None
            if self.last_panic_winner is not None:
                try: winner_ticker = self.last_panic_winner.value
                except Exception: winner_ticker = None

            def _targets_to_dict(targets):
                out = {}
                for sym, w in targets.items():
                    try: out[sym.value] = float(w)
                    except Exception: pass
                return out

            panic_end_str = self.panic_end_date.isoformat() if self.panic_end_date is not None else None

            state = {
                "portfolio_peak":               self.portfolio_peak,
                "emergency_stop_triggered":     self.emergency_stop_triggered,
                "emergency_liquidation_executed": self.emergency_liquidation_executed,
                "last_trade_date":              _d(self.last_trade_date),
                "bootstrap_trade_count":        getattr(self, "_bootstrap_trade_count", 0),  # [BSC]
                "last_core_rebalance":          _d(self.last_core_rebalance),
                "last_overlay_rebalance":       _d(self.last_overlay_rebalance),
                "current_regime":               self.current_regime,
                "prev_regime":                  self.prev_regime,
                "regime_start_date":            _d(self.regime_start_date),
                "panic_mode_active":            self.panic_mode_active,
                "panic_end_date":               panic_end_str,
                "last_panic_end_date":          _d(self.last_panic_end_date),
                "last_panic_winner":            winner_ticker,
                "trend_state_in_spy":           self.trend_state_in_spy,
                "previous_equity":              self.previous_equity,
                "dd_history":                   list(self._dd_history) if hasattr(self, "_dd_history") else [],
                "last_core_targets":            _targets_to_dict(self._last_core_targets),
                "last_overlay_targets":         _targets_to_dict(self._last_overlay_targets),
                "overlay_shock_flag":           self.overlay_shock_flag,
                "short_shock_flag":             self.short_shock_flag,
                "short_shock_set_date":         _d(self._short_shock_set_date),
                "sh_profit_exit_signal":        getattr(self, "_sh_profit_exit_signal", False),
                "dd_cb_active":          getattr(self, "_dd_cb_active", False),
                "dd_cb_trigger_date":    _d(getattr(self, "_dd_cb_trigger_date", None)),
                "dd_cb_resume_date":     _d(getattr(self, "_dd_cb_resume_date", None)),
                "dd_cb_count":           int(getattr(self, "_dd_cb_count", 0)),
                "dd_cb_local_peak":      getattr(self, "_dd_cb_local_peak", None),
                "dd_cb_dd_at_trigger":   getattr(self, "_dd_cb_dd_at_trigger", None),
                "tactical_active_sym":        getattr(self, "_active_tactical_symbol", None) and self._active_tactical_symbol.value,
                "tactical_entry_date":        _d(getattr(self, "_tactical_entry_date", None)),
                "tactical_entry_dd":          getattr(self, "_tactical_entry_dd", None),
                "tactical_entry_price":       getattr(self, "_tactical_entry_price", None),
                "tactical_entry_spy_price":   getattr(self, "_tactical_entry_spy_price", None),
                "tactical_peak_close":        getattr(self, "_tactical_peak_close_since_entry", None),
                "tactical_exit_lock_active":  getattr(self, "_tactical_exit_lock_active", False),
                "tactical_exit_lock_date":    _d(getattr(self, "_tactical_exit_lock_date", None)),
                "tactical_reset_hold_until":  _d(getattr(self, "_tactical_reset_hold_until", None)),
                "tactical_last_reset_sym":    getattr(self, "_tactical_last_reset_symbol", None) and self._tactical_last_reset_symbol.value,
                "tactical_last_reset_date":   _d(getattr(self, "_tactical_last_reset_date", None)),
                "tactical_reset_count":       int(getattr(self, "_tactical_reset_count", 0)),
                "tactical_winner_set_date":   _d(getattr(self, "_tactical_winner_set_date", None)),
                "tactical_stale_to_zero":     [s.value for s in getattr(self, "_stale_tactical_to_zero", set())],
                "tactical_blocked_sym":       getattr(self, "_tactical_blocked_symbol", None) and self._tactical_blocked_symbol.value,
                "tactical_block_until":       _d(getattr(self, "_tactical_block_until", None)),
                "tactical_block_reason":      getattr(self, "_tactical_block_reason", None),
                "lst_type":           self.latent_structure_type,
                "lst_prev_type":      self.latent_structure_prev_type,
                "lst_since":          _d(self.latent_structure_since),
                "lst_pending_type":   self.latent_structure_pending_type,
                "lst_pending_count":  int(self.latent_structure_pending_count),
                "lst_entry_type":     self.latent_structure_entry_type,
                "lst_mismatch_days":  int(self.latent_structure_mismatch_days),
                "state_schema":1,"strategy_version":"v3.8","crash_ticker":self.crash_ticker,
                "sh_mode":self.sh_mode,"capital_cap":self.capital_cap,
                "snap_anomaly_active":getattr(self,"_snap_anomaly_active",False),
}  # [LSS1]

            state.update(self.PanicScoreSaveFields())
            state.update(self._IDSSaveFields())   # [IDS_V2]
            state["lfc"] = self.LiveCashFlowSaveFields()
            self.object_store.save(self._STATE_KEY, json.dumps(state))
            self.SHSaveState()
            self._state_save_ok = True    # [LSS2]
            self._live_state_loaded = True  # [LFC-FIX] cold-start protection after first save
        except Exception as e:
            self._state_save_ok = False  # [LSS2]
            self.log(f"[STATE_SAVE] failed: {e}")

    def _LoadState(self):
        if not self.live_mode:
            return
        try:
            if not self.object_store.contains_key(self._STATE_KEY):
                self._live_state_loaded=True; self.log("[STATE_LOAD] No snapshot found -- cold start.")
                return                
            raw   = self.object_store.read(self._STATE_KEY)
            state = json.loads(raw)
            if state.get("state_schema",0) != 1:  # [LSS1]
                self.log(f"[LSS1] schema mismatch -- cold start"); return
            for _fld,_cur in [("crash_ticker",self.crash_ticker),("sh_mode",self.sh_mode)]:
                _sv=state.get(_fld)
                if _sv and _sv!=_cur:
                    self.log(f"[LSS2] {_fld} mismatch: {_sv}!={_cur} -- cold start"); return

            def _parse_date(s):
                if not s: return None
                try: return date.fromisoformat(s[:10])
                except Exception: return None
            def _parse_dt(s):
                if not s: return None
                try: return datetime.fromisoformat(s)
                except Exception: return None

            ticker_to_sym = {sym.value: sym for sym in self.active_symbols}

            self.portfolio_peak                  = float(state.get("portfolio_peak", 0.0))
            self._peak_initialized               = self.portfolio_peak > 0
            self.emergency_stop_triggered        = bool(state.get("emergency_stop_triggered", False))
            self.emergency_liquidation_executed  = bool(state.get("emergency_liquidation_executed", False))
            self.last_trade_date                 = _parse_date(state.get("last_trade_date"))
            self._bootstrap_trade_count          = int(state.get("bootstrap_trade_count", 999))  # [BSC] default 999 = already past cap
            self.last_core_rebalance             = _parse_date(state.get("last_core_rebalance"))
            self.last_overlay_rebalance          = _parse_date(state.get("last_overlay_rebalance"))
            self.current_regime                  = state.get("current_regime")
            self.prev_regime                     = state.get("prev_regime")
            self.regime_start_date               = _parse_date(state.get("regime_start_date"))
            self.panic_mode_active               = bool(state.get("panic_mode_active", False))
            self.panic_end_date                  = _parse_dt(state.get("panic_end_date"))
            self.last_panic_end_date             = _parse_date(state.get("last_panic_end_date"))
            self.trend_state_in_spy              = bool(state.get("trend_state_in_spy", True))
            prev_eq                              = state.get("previous_equity")
            self.previous_equity                 = float(prev_eq) if prev_eq is not None else None
            self.overlay_shock_flag              = bool(state.get("overlay_shock_flag", False))
            self.short_shock_flag                = bool(state.get("short_shock_flag", False))
            self._short_shock_set_date           = _parse_date(state.get("short_shock_set_date"))
            self._sh_profit_exit_signal          = bool(state.get("sh_profit_exit_signal", False))

            self._dd_cb_active        = bool(state.get("dd_cb_active", False))
            self._dd_cb_trigger_date  = _parse_date(state.get("dd_cb_trigger_date"))
            self._dd_cb_resume_date   = _parse_date(state.get("dd_cb_resume_date"))
            self._dd_cb_count         = int(state.get("dd_cb_count") or 0)
            self._dd_cb_local_peak    = state.get("dd_cb_local_peak")
            if self._dd_cb_local_peak is not None:
                self._dd_cb_local_peak = float(self._dd_cb_local_peak)
            self._dd_cb_dd_at_trigger = state.get("dd_cb_dd_at_trigger")
            if self._dd_cb_dd_at_trigger is not None:
                self._dd_cb_dd_at_trigger = float(self._dd_cb_dd_at_trigger)

            raw_hist = state.get("dd_history", [])
            if raw_hist:
                self._dd_history = [float(x) for x in raw_hist]

            winner_ticker = state.get("last_panic_winner")
            if winner_ticker and winner_ticker in ticker_to_sym:
                self.last_panic_winner = ticker_to_sym[winner_ticker]

            def _restore_targets(raw_dict):
                out = {}
                for ticker, w in (raw_dict or {}).items():
                    sym = ticker_to_sym.get(ticker)
                    if sym is not None: out[sym] = float(w)
                return out

            restored_core    = _restore_targets(state.get("last_core_targets"))
            restored_overlay = _restore_targets(state.get("last_overlay_targets"))
            if restored_core:    self._last_core_targets = restored_core
            if restored_overlay: self._last_overlay_targets = restored_overlay

            self.PanicScoreLoadFields(state)
            self._IDSLoadFields(state)            # [IDS_V2]
            self.LiveCashFlowLoadFields(state)

            tact_sym_ticker = state.get("tactical_active_sym")
            self._active_tactical_symbol = ticker_to_sym.get(tact_sym_ticker) if tact_sym_ticker else None
            self._tactical_entry_date    = _parse_date(state.get("tactical_entry_date"))
            v = state.get("tactical_entry_dd");       self._tactical_entry_dd               = float(v) if v is not None else None
            v = state.get("tactical_entry_price");    self._tactical_entry_price            = float(v) if v is not None else None
            v = state.get("tactical_entry_spy_price");self._tactical_entry_spy_price        = float(v) if v is not None else None
            v = state.get("tactical_peak_close");     self._tactical_peak_close_since_entry = float(v) if v is not None else None
            self._tactical_exit_lock_active  = bool(state.get("tactical_exit_lock_active", False))
            self._tactical_exit_lock_date    = _parse_date(state.get("tactical_exit_lock_date"))
            self._tactical_reset_hold_until  = _parse_date(state.get("tactical_reset_hold_until"))
            last_reset_ticker = state.get("tactical_last_reset_sym")
            self._tactical_last_reset_symbol = ticker_to_sym.get(last_reset_ticker) if last_reset_ticker else None
            self._tactical_last_reset_date   = _parse_date(state.get("tactical_last_reset_date"))
            self._tactical_reset_count       = int(state.get("tactical_reset_count") or 0)
            self._tactical_winner_set_date   = _parse_date(state.get("tactical_winner_set_date"))
            stale_tickers = state.get("tactical_stale_to_zero", [])
            self._stale_tactical_to_zero     = {ticker_to_sym[t] for t in stale_tickers if t in ticker_to_sym}
            blocked_ticker = state.get("tactical_blocked_sym")
            self._tactical_blocked_symbol = ticker_to_sym.get(blocked_ticker) if blocked_ticker else None
            self._tactical_block_until    = _parse_date(state.get("tactical_block_until"))
            self._tactical_block_reason   = state.get("tactical_block_reason")

            self.latent_structure_type          = state.get("lst_type", "UNKNOWN")
            self.latent_structure_prev_type     = state.get("lst_prev_type", "UNKNOWN")
            self.latent_structure_since         = _parse_date(state.get("lst_since"))
            self.latent_structure_pending_type  = state.get("lst_pending_type")
            self.latent_structure_pending_count = int(state.get("lst_pending_count") or 0)
            self.latent_structure_entry_type    = state.get("lst_entry_type")
            self.latent_structure_mismatch_days = int(state.get("lst_mismatch_days") or 0)

            self._last_good_equity=state.get("last_good_equity")  # [LSS1]
            self._snap_anomaly_active=bool(state.get("snap_anomaly_active",False))  # [LSS1]
            v=state.get("rs_daily_spy_cap"); self._rs_daily_spy_cap=float(v) if v is not None else None  # [LSS1]
            self._rs_daily_spy_cap_until=_parse_date(state.get("rs_daily_spy_cap_until"))  # [LSS1]
            self._live_state_loaded=True  # [LSS1]
            self.log(
                f"[STATE_LOAD] OK: regime={self.current_regime} "
                f"peak={self.portfolio_peak:.2f} "
                f"emergency={self.emergency_stop_triggered} "
                f"liq_executed={self.emergency_liquidation_executed} "
                f"panic={self.panic_mode_active} "
                f"trend_in_spy={self.trend_state_in_spy}")
            self.SHLoadState()
        except Exception as e:
            self.log(f"[STATE_LOAD] Failed -- cold start: {e}")


    def DAILYCycle(self):
        if self.is_warming_up:
            return

        self.LiveCashFlowCheck("DAILY")

        if not self._peak_initialized:
            self.portfolio_peak    = float(self.portfolio.total_portfolio_value)
            self._peak_initialized = True

        if self.CheckEmergencyStop():
            if not self._diag: self._ResetDiag()
            self._diag['emergency_stop'] = 1
            self._diag['date']   = self.time.date()
            self._diag['regime'] = self.current_regime or 'UNKNOWN'
            self._EmitDiagLog()
            return

        self._ResetDiag()
        self._ResetShadow()  # [SHADOW]
        if self.short_shock_flag and self._short_shock_set_date is not None:
            if (self.time.date() - self._short_shock_set_date).days >= self.short_shock_decay_days:
                self.short_shock_flag      = False
                self._short_shock_set_date = None

        if getattr(self,"_snap_anomaly_active",False):  # [LSS2]
            self.log("[LSS2] SNAP active -- skipping peak/DD update")
        else:
            self.UpdateDrawdownPeak()
            self._RecordDdHistory()
        self.UpdatePanicMode()
        self.UpdatePanicScore()

        prev_regime = self.current_regime
        regime, diag = self.DetectRegime(return_diag=True)

        if regime != self.current_regime:
            self.prev_regime       = self.current_regime
            self.current_regime    = regime
            self.regime_start_date = self.time.date()
            if self.live_mode or self.debug_regime:
                if self.debug_regime:
                    self.log(f"[REGIME] {prev_regime} -> {self.current_regime} on {self.time.date()} | {diag}")
                else:
                    self.log(f"[REGIME] {prev_regime} -> {self.current_regime} on {self.time.date()}")

        self.EmitPanicStructureDiag(context="daily")
        self.UpdateLatentStructureType()  # [LST-D0] after regime + panic, before target build
        self._EmitRateShockGateDiag()
        self._EmitDynamicThresholdDiag()
        self._EmitRegimeSplitDiag()
        self._EmitReRiskVetoDiag()
        self.RRXDailyCycle()                              # [RRX]

        today = self.time.date()

        if self.CheckDdCircuitBreaker():
            self._EmitDiagLog()
            self._SaveState()
            return

        _eq_shock = self.RecentEquityShock()
        self._diag['date']                     = today
        self._diag['regime']                   = self.current_regime or 'UNKNOWN'
        self._diag['dd']                       = self.CurrentDrawdown()
        self._diag['panic_mode']               = self._bool_diag(self.panic_mode_active)
        self._diag['in_panic_recovery_window'] = self._bool_diag(self.InPanicRecoveryWindow())
        self._diag['trend_state_in_spy']       = self._bool_diag(self.trend_state_in_spy)
        self._diag['equity_shock']             = self._bool_diag(_eq_shock["active"])
        self._diag['trend_sleeve_enabled']     = self._bool_diag(self.TrendSleeveEnabled())

        _ug_ok = not self.live_mode or getattr(self,"_live_state_loaded",True)  # [LSS2]
        for symbol, holding in self.portfolio.items():
            if holding.invested and symbol not in self.active_symbols:
                if _ug_ok:
                    self.liquidate(symbol)
                    self.log(f"[UNIVERSE_GUARD] liquidated stale: {symbol.value} {today}")
                else:
                    self.log(f"[UNIVERSE_GUARD] no-state skip: {symbol.value} {today}")  # [LSS2]

        if getattr(self, "_sh_state", _SH_IDLE) not in (_SH_HEDGED, _SH_ENTRY_PENDING, _SH_EXIT_PENDING):
            _sh_holding = self.portfolio.get(self.sym_sh)
            if _sh_holding is not None and _sh_holding.invested:
                self.liquidate(self.sym_sh)
                self.log(f"[SH_HEDGE] Liquidated stale SH position on {today}")

        need_core    = self.ShouldRebalanceCore(today)
        need_overlay = self.ShouldRebalanceOverlay(today)
        if self.SPYGSatNeed(today): need_overlay = True
        # [LFC-FIX] Read one-shot flags without consuming them here.
        # They are cleared only after the execution path is reached.
        _lfc_force_raw  = bool(getattr(self, "_lfc_force_rebalance", False))
        _lfc_reduce_raw = bool(getattr(self, "_lfc_force_reduce", False))
        _lfc_reduce_allowed = bool(getattr(self, "lfc_reduce_only_on_withdrawal", False))
        _lfc_reduce = bool(_lfc_reduce_raw and _lfc_reduce_allowed)
        _lfc_force  = bool(_lfc_force_raw or _lfc_reduce)
        if _lfc_force:
            need_core = True
            need_overlay = True

        self._ComputeShadow()  # [SHADOW] pre-trade snapshot -- no side effects

        if need_core or need_overlay:
            if need_core:
                self._last_core_targets  = self.BuildCoreTargets()
                self.last_core_rebalance = today
                self._diag['core_rebalanced'] = 1
            if need_overlay:
                self._last_overlay_targets  = self.BuildOverlayTargets()
                self.last_overlay_rebalance = today
                self._diag['overlay_rebalanced'] = 1

            combined = self.MergeSleeves(self._last_core_targets, self._last_overlay_targets)
            if getattr(self, "bootstrap_spy_cap_enable", True):
                _is_cold = (self.last_trade_date is None)
                if not _is_cold and self.last_trade_date is not None:
                    _trade_count = getattr(self, "_bootstrap_trade_count", 0)
                    _is_cold = _trade_count < int(getattr(self, "bootstrap_spy_cap_days", 5))
                if _is_cold:
                    _cap = float(getattr(self, "bootstrap_spy_cap", 1.10))
                    _spy_w = float(combined.get(self.sym_spy, 0.0))
                    if _spy_w > _cap:
                        combined = dict(combined)
                        combined[self.sym_spy] = _cap
                        self.log(
                            f"[BSC] cold-start cap: spy {_spy_w:.3f}->{_cap:.3f} "
                            f"trade_count={getattr(self,'_bootstrap_trade_count',0)}"
                        )
            # [SPYG_SAT_SPY_SYNC_D0] Capture base SPY weight before satellite modifies targets.
            try:
                _spy_w = 0.0
                _spy_sym = getattr(self, "sym_spy", None)
                for _s, _w in list(combined.items()):
                    try:
                        _sv = _s.Value
                    except Exception:
                        _sv = str(_s)
                    if (_spy_sym is not None and _s == _spy_sym) or _sv == "SPY":
                        try:
                            _spy_w += float(_w or 0.0)
                        except Exception:
                            pass
                self._spyg_sat_base_spy_w = max(0.0, float(_spy_w))
                self._spyg_sat_base_spy_seen = True
            except Exception:
                self._spyg_sat_base_spy_w = 0.0
                self._spyg_sat_base_spy_seen = False

            self.EmitDynAllocD0(combined)  # [DYN_ALLOC_D0]
            self.SPYGSatTrade(combined)    # [SPYG_SAT]

            _no_state = self.live_mode and not getattr(self,"_live_state_loaded",True)
            _save_err = self.live_mode and not getattr(self,"_state_save_ok",True)  # [LSS2]
            _lfc_clear_ok = False                                                   # [LFC-FIX]
            if _no_state:
                self.log("[LSS1] no state -- skipping ExecuteTargets")
            elif _save_err:
                self.log("[LSS2] save failed -- reduce-only")
                self.ExecuteTargets(combined, reduce_only=True)
                _lfc_clear_ok = True
            elif _lfc_reduce:
                self.log("[CASHFLOW] withdrawal reduce-only")
                self.ExecuteTargets(combined, reduce_only=True)
                _lfc_clear_ok = True
            else:
                self.ExecuteTargets(combined)
                _lfc_clear_ok = True
            if _lfc_clear_ok:
                self._lfc_force_rebalance = False
                self._lfc_force_reduce = False

        self._EmitDiagLog()
        self._EmitShadowLog()  # [SHADOW]
        self.UpdateMonitoring()
        self._SaveState()

    def OnWarmupFinished(self):
        try: r, info = self.DetectRegime(return_diag=True)
        except Exception: r, info = (self.current_regime or "NEUTRAL"), "init_fail"
        if r != self.current_regime:
            self.current_regime    = r
            self.regime_start_date = self.time.date()
        else:
            self.current_regime = r
            if self.regime_start_date is None:
                self.regime_start_date = self.time.date()
        if self.debug_regime:
            self.log(f"[REGIME_INIT] {self.current_regime} start={self.regime_start_date} on {self.time.date()} | {info}")

    def UpdateMonitoring(self):
        if not getattr(self, "plot_enable", False):
            return
        equity = float(self.portfolio.total_portfolio_value)
        dd     = self.CurrentDrawdown()

        self.plot("portfolio", "Equity",   equity)
        self.plot("portfolio", "Drawdown", dd * 100.0)

        if self.current_regime == "RISK_OFF":
            regime_code = 0
        elif self.current_regime == "RISK_ON":
            regime_code = 2
        else:
            regime_code = 1

        self.plot("Regime", "RegimeCode", regime_code)
        self.plot("Regime", "TrendState", 1 if self.trend_state_in_spy else 0)

        realized_vol = self.GetApproxRealizedVol()
        try:
            vix_pct = self.GetVixPercentile()
        except Exception:
            vix_pct = None

        if realized_vol is not None and np.isfinite(realized_vol):
            self.plot("Volatility", "RealizedVolApprox", realized_vol * 100.0)
        if vix_pct is not None and np.isfinite(vix_pct):
            self.plot("Volatility", "VixPercentile", vix_pct * 100.0)

    def OnOrderEvent(self, order_event):
        if order_event is None: return
        try:
            _t=getattr(order_event.symbol,"Value","")
            _rs={getattr(s,"Value","") for s in getattr(self,"_rrx_symbols",[])}
            if _t in _rs: self._rrxg_rrx_orders=getattr(self,"_rrxg_rrx_orders",0)+1
        except Exception: pass
        if not self.live_mode: return
        if order_event.status in (OrderStatus.FILLED, OrderStatus.PARTIALLY_FILLED):
            self.log(
                f"[FILL] {order_event.symbol.value} "
                f"qty={order_event.fill_quantity} "
                f"price={order_event.fill_price} "
                f"status={order_event.status}")
            if order_event.status == OrderStatus.FILLED:
                if order_event.symbol in (self.sym_sh, self.sym_spy):
                    self.SHOnOrderFill(order_event.symbol)
                self.RrOnOrderFill(order_event)  # [RR_SLEEVE]

    def OnEndOfDay(self, symbol):
        if self.is_warming_up: return
        if not hasattr(self, "_snap_last_date"): self._snap_last_date = None
        today = self.time.date()
        if self._snap_last_date == today: return
        self._snap_last_date = today
        self.LiveCashFlowCheck("EOD")
        equity = float(self.portfolio.total_portfolio_value)
        try: getattr(self,"GetExpandedRegimeDiag",lambda:None)()  # [XRD]
        except Exception: pass
        daily_return = (equity / self.previous_equity) - 1.0 if (self.previous_equity and self.previous_equity > 0) else 0.0
        _prev_ok = bool(self.previous_equity and self.previous_equity > 0)
        _snap = self.live_mode and _prev_ok and abs(daily_return) > 0.10  # [LSS2]
        if _snap:
            self._snap_anomaly_active = True
            self.log(f"[SNAP_ANOMALY] {daily_return:.1%} eq={equity:.0f} -- state frozen")  # [LSS2]
        else:
            if self.live_mode: self._snap_anomaly_active = False; self._last_good_equity = equity
            self.previous_equity = equity  # [LSS2]
            if _prev_ok: self._daily_returns.append((today, daily_return))  # [LSS2]

        if self.live_mode and today.month == 12 and today.day >= 28:  # [XRD]
            if getattr(self,"_w5_last_emitted_year",None) != today.year:
                self._w5_last_emitted_year = today.year
                self._EmitWorstDays(label=f"ANNUAL_{today.year}", top_n=25)
        regime = self.current_regime or "UNKNOWN"
        panic  = int(self.panic_mode_active)
        try: core_w, overlay_w = self.GetAllocations()
        except Exception: core_w, overlay_w = 0.0, 0.0
        try: trend_w = float(self.trend_sleeve_weight) if self.TrendSleeveEnabled() else 0.0
        except Exception: trend_w = 0.0
        if self.daily_snap:
            self.debug(
                f"SNAP,{today},{equity:.4f},{daily_return:.6f},"
                f"{regime},{panic},"
                f"{core_w:.4f},{overlay_w:.4f},{trend_w:.4f}")
        self._SaveState()

    def _EmitWorstDays(self, label="FINAL", top_n=None):
        if not getattr(self, "_daily_returns", None):
            return
        sr = sorted(self._daily_returns, key=lambda x: x[1])
        n5 = max(1, int(len(sr) * 0.05))
        rows = sr[:n5]
        if top_n is not None:
            rows = rows[:top_n]
        sep = "=" * 48
        super().log(f"{sep}")
        super().log(f"WORST_5PCT,{label},{n5}_of_{len(sr)}_days")
        for i, (day, ret) in enumerate(rows, 1):
            super().log(f"W5,{i},{day},{ret*100:+.2f}%")
        super().log(f"{sep}")

    def OnEndOfAlgorithm(self):
        self._SaveState()
        self.RRXEmitFinalSummary()                        # [RRX]
        self.log("[EOA] final snapshot saved")
        if self.live_mode: self._EmitWorstDays(label="FINAL")
        getattr(self, "EmitXRegimeFinalDist", lambda: None)()  # [XRD]

    def OnData(self, data):
        try: self.SHOnData(data)
        except Exception: pass


from sh_hedge import _SH_IDLE, _SH_HEDGED, _SH_ENTRY_PENDING, _SH_EXIT_PENDING  # noqa: F401

for _cls in (CoreGrowthSubscriptionMixin, CoreGrowthLogic, SHHedgeLogic, PanicScoreLogic, StressScenarioMixin, CoreGrowthMarketStructureMixin, DynamicThresholdDiagMixin, DynamicAllocationDiagMixin, RRXSectorDiagMixin, LiveCashFlowMixin):
    for _name, _fn in inspect.getmembers(_cls, predicate=inspect.isfunction):
        setattr(CoreGrowthPlusConditionalTrendSleeve, _name, _fn)
