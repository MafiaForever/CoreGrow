# cg_legacy_params.py
# CG-LEGACY-PARAMS-T1: pre-RR parameter profile switch.
# Changes attribute values only. Never changes code paths, SH timing, or emergency_dd_limit.
# Priority: explicit QC project parameter > legacy profile > production default.

# Production defaults (profile=0). Used for DIFF and for resolve when profile off.
_PROD = {
    "base_spy_weight": 0.85, "max_spy_leverage": 1.3,
    "bootstrap_spy_cap_enable": True, "bootstrap_spy_cap": 1.50,
    "bootstrap_spy_cap_days": 5, "leverage_confirm_days": 10,
    "dd_soft_start": 0.11, "dd_hard_end": 0.16,
    "dd_clamp_confirm_lookback": 2, "dd_clamp_confirm_improvement": 0.0001,
    "dd_recovery_gross_ref": 1.5,
    "overlay_dd_stress_soften_enable": True, "overlay_dd_stress_blend": 0.50,
    "max_symbol_weight": 2.5, "max_total_exposure": 1.9,
    "vol_lookback": 60, "target_vol_annual": 0.18,
    "min_realized_vol": 0.08, "max_realized_vol": 0.35,
    "min_vol_leverage": 0.6, "max_vol_leverage": 1.6,
    "vix_fg_lookback": 252, "vix_low_pct": 0.35, "vix_high_pct": 0.75,
    "regime_min_persist_days": 3,
    "trend_ma_period": 160, "spy_ema_fast_period": 9, "spy_ema_mid_period": 75,
    "spy_ema_slow_period": 120, "spy_long_sma_period": 200,
    "trend_sleeve_weight": 0.05, "trend_band": 0.01,
    "trend_enable_realized_vol": 0.18, "trend_enable_vix_pct": 0.70,
    "trend_sleeve_weight_cap": 0.30,
    "neutral_decay_days": 20, "neutral_decay_factor": 0.90,
    "min_weight_delta": 0.02, "trade_cooldown_days": 1,
    "min_trade_value": 100, "min_trade_value_perc": 0.12,
    "max_days_no_core_rebalance": 45, "max_days_no_overlay_rebalance": 7,
    "rebalance_shock_threshold": 0.02,
    "panic_trigger_pct": 0.07, "panic_window_days": 7,
    "panic_recovery_min_days": 5, "panic_recovery_max_days": 15,
    "panic_block_max": 0.30, "panic_block_from_spy_frac": 0.75,
    "panic_mom_lookback": 10, "panic_mom_threshold": 0.01,
    "stress_spy_cap": 0.05, "shock_tactical_block_frac": 0.35,
    "watch_tail_spy_dampen_enable": True, "watch_tail_score_threshold": 0.20,
    "watch_tail_spy_multiplier": 0.20,
    "post_panic_brake_enable": False, "post_panic_brake_days": 3,
    "post_panic_spy_multiplier": 0.20,
    "spy_shock_1d_threshold": 3.3, "spy_shock_3d_threshold": 3.4,
    "spy_shock_5d_threshold": 3.3,
    "spy_shock_scale_1d": 0.60, "spy_shock_scale_3d": 0.40, "spy_shock_scale_5d": 0.80,
    "short_shock_1d_threshold": 2.0, "short_shock_2d_threshold": 4.0,
    "short_shock_3d_threshold": 4.2, "short_shock_decay_days": 1,
    "ids_enable": True,
    "ids_thr_watch": 0.35, "ids_thr_stress": 0.60, "ids_thr_panic_short": 0.85,
    "ids_stress_entry_confirm": 2, "ids_min_components_entry": 2,
    "ids_watch_hold_minutes": 30, "ids_stress_hold_minutes": 120,
    "ids_release_decay_alpha": 0.20,
    "ids_watch_hedge_frac": 0.20, "ids_stress_hedge_frac": 0.40, "ids_panic_hedge_frac": 0.60,
    "ids_watch_spy_cap": 0.75, "ids_stress_spy_cap": 0.50,
    "ids_panic_spy_cap_risk_on": 0.30, "ids_panic_spy_cap_neutral": 0.35,
    "ids_panic_spy_cap_risk_off": 0.15,
    "ids_watch_gross_cap": 1.40, "ids_stress_gross_cap": 1.20, "ids_panic_gross_cap": 0.90,
    "crash_ticker": "SGOV", "crash_weight": 0.50,
    "max_cr_cash_weight": 0.40, "neutral_cr_cash_weight": 0.05,
    "min_cash_anchor_overlay": 0.10, "yc_duration_ok_min": 0.25,
    "def_tilt_enable": True, "def_tilt_budget": 0.25, "def_tilt_lookback": 10,
    "def_tilt_min_score": 0.00, "def_tilt_trend_ma_period": 60,
    "def_tilt_max_single_add": 0.50, "def_tilt_skip_cash_as_winner": False,
    "tactical_min_hold_days": 10,
    "tactical_atr_exit_enable": True, "tactical_atr_len": 18,
    "tactical_atr_trail_mult": 6.0, "tactical_atr_min_hold_days": 3,
    "tactical_atr_arm_profit": 0.015,
    "tactical_sharp_exit_enable": True, "tactical_sharp_atr_mult": 1.3,
    "tactical_sharp_weak_score_min": 3, "tactical_slow_exit_enable": True,
    "tactical_reset_enable": True, "tactical_reset_min_hold_days": 3,
    "tactical_reset_dd_worsen": 0.015, "tactical_reset_abs_loss": -0.020,
    "tactical_reset_spy_underperf": -0.005, "tactical_reset_cooldown_days": 5,
    "tactical_reset_require_active_dd": True,
    "tactical_cleanup_on_winner_change": True, "tactical_block_same_symbol_enable": True,
    "tac_hold_enable": False, "tac_hold_max_days": 30, "tac_hold_dbc_spy_min": 0.08,
    "tac_hold_min_current_weight": 0.010, "tac_hold_max_weight": 0.250,
    "tac_hold_tip_bnd_min": 0.000, "tac_hold_symbol_5d_min": -0.060,
    "tac_hold_symbol_10d_min": -0.080,
    "xle_noise_d0_enable": False, "xle_noise_veto_enable": False,
    "bear_rally_gate_enable": True, "bear_rally_corr_min": 0.25,
    "bear_rally_dbc_spy_max": 0.0, "bear_rally_min_add": 0.005,
    "bear_rally_rate_corr_min": 0.50, "bear_rally_rate_bnd20_max": -0.015,
    "bear_rally_rate_spy20_max": 0.0,
    "core_ballast_c0_enable": False, "core_ballast_c0_spy_threshold": 0.85,
    "dur_c1a_enable": False, "dur_c1b_variant": 0, "c1r_ge4_enable": False,
    "dd_cb_enable": True, "dd_cb_threshold": 0.15,
    "dd_cb_cooldown_days": 1, "dd_cb_min_days_between": 1,
    "sh_mode": "SPY_CUT_ONLY",
    "sh_profit_signal_threshold": 0.007, "sh_profit_spy_scale": 0.60,
}

# Legacy pre-RR values. Identical keys to _PROD; only differing values affect trading.
_LEGACY = dict(_PROD)
_LEGACY.update({
    "min_trade_value_perc": 0.11,
    "bear_rally_gate_enable": False,
})
# Explicit never-override list
_NEVER = frozenset(("emergency_dd_limit",))


def _qc_explicit(algo, k):
    try:
        v = algo.get_parameter(k)
    except Exception:
        return False
    return v is not None and str(v).strip() != ""


def _cast_like(sample, raw):
    if isinstance(sample, bool):
        if isinstance(raw, bool):
            return raw
        return str(raw).strip().lower() in ("1", "true", "yes", "on")
    if isinstance(sample, int) and not isinstance(sample, bool):
        return int(float(raw))
    if isinstance(sample, float):
        return float(raw)
    return str(raw)


def _fmt(v):
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, float):
        s = f"{v:.6g}"
        return s
    return str(v)


class CgLegacyParamProfileMixin:

    def CgLegacyParamProfileApply(self) -> None:
        """Apply profile after production defaults are set; before SH/indicators.
        Never touches emergency_dd_limit. QC explicit params win.
        """
        self._cg_legacy_changed = []
        # Ensure period attrs exist before indicator wiring even if profile off
        for k, v in (
            ("trend_ma_period", 160), ("spy_ema_fast_period", 9),
            ("spy_ema_mid_period", 75), ("spy_ema_slow_period", 120),
            ("spy_long_sma_period", 200),
            ("max_symbol_weight", 2.5), ("max_total_exposure", 1.9),
            ("c1r_ge4_enable", False),
        ):
            if not hasattr(self, k):
                setattr(self, k, v)
        # Runtime allowlist so DIFF line is never filtered by stale QC log_only_prefixes
        lp = list(getattr(self, "log_only_prefixes", None) or [])
        if "CG_LEGACY_" not in lp:
            lp.append("CG_LEGACY_")
        if "CG_PARAM" not in lp and "[INIT] CG_PARAM_PROFILE" not in "".join(lp):
            # [INIT] bypasses filter; still add CG_LEGACY_ for DIFF
            pass
        self.log_only_prefixes = lp
        if not getattr(self, "cg_legacy_param_profile_enable", False):
            return
        for k, leg in _LEGACY.items():
            if k in _NEVER:
                continue
            if _qc_explicit(self, k):
                continue
            prod = _PROD.get(k, leg)
            before = getattr(self, k, prod)
            try:
                newv = _cast_like(prod, leg)
            except Exception:
                newv = leg
            setattr(self, k, newv)
            # record only real production→legacy diffs
            try:
                changed = (float(prod) != float(newv)) if isinstance(prod, (int, float)) and not isinstance(prod, bool) and isinstance(newv, (int, float)) and not isinstance(newv, bool) else (prod != newv)
            except Exception:
                changed = (prod != newv)
            if changed:
                self._cg_legacy_changed.append(f"{k}:{_fmt(prod)}->{_fmt(newv)}")

    def CgLegacyParamProfileAudit(self) -> None:
        """Emit compact startup audit after emergency_dd_limit is assigned."""
        try:
            leg = 1 if getattr(self, "cg_legacy_param_profile_enable", False) else 0
            self.log(
                f"[INIT] CG_PARAM_PROFILE,legacy={leg},"
                f"dd_soft={_fmt(getattr(self,'dd_soft_start',0))},"
                f"dd_hard={_fmt(getattr(self,'dd_hard_end',0))},"
                f"min_trade_pct={_fmt(getattr(self,'min_trade_value_perc',0))},"
                f"bear_rally={int(bool(getattr(self,'bear_rally_gate_enable',False)))},"
                f"dd_cb={int(bool(getattr(self,'dd_cb_enable',False)))},"
                f"dd_cb_thr={_fmt(getattr(self,'dd_cb_threshold',0))},"
                f"target_vol={_fmt(getattr(self,'target_vol_annual',0))},"
                f"max_gross={_fmt(getattr(self,'max_total_exposure',1.9))},"
                f"ids_watch_gross={_fmt(getattr(self,'ids_watch_gross_cap',0))},"
                f"ids_stress_gross={_fmt(getattr(self,'ids_stress_gross_cap',0))},"
                f"emergency_dd={_fmt(getattr(self,'emergency_dd_limit',0))},"
                f"sh_current_path=1")
        except Exception as e:
            try:
                self.log(f"[INIT] CG_LEGACY_PARAM_ERROR,stage=audit,type={type(e).__name__}")
            except Exception:
                pass

    def CgLegacyParamProfileEmitDiff(self) -> None:
        ch = list(getattr(self, "_cg_legacy_changed", []) or [])
        body = "|".join(ch)
        line = f"CG_LEGACY_PARAM_DIFF_FINAL,count={len(ch)},changed={body if body else 'NONE'}"
        b = line.encode("utf-8")
        if len(b) > 4000:
            line = line[:3980] + "...TRUNC"
        self.log(line)
