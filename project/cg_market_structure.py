import numpy as np
from AlgorithmImports import *
from typing import TYPE_CHECKING
from cg_risk_tactical import CoreGrowthRiskTacticalMixin


class CoreGrowthMarketStructureMixin:
    if TYPE_CHECKING:
        time: object
        live_mode: bool
        debug_regime: bool
        securities: object
        sym_spy: object
        sym_bnd: object
        sym_dbc: object
        sym_tip: object
        sym_gld: object
        spy_ema_9: object
        spy_ema_120: object
        spy_sma_200: object
        _prev_raw_ptype: str
        _panic_state: str
        _ids_state: str
        short_shock_flag: bool
        _xregime_cache_key: object
        _xregime_cache_date: object
        _xregime_cache: dict
        _market_structure_type: str
        _market_structure_raw_type: str
        _market_structure_confidence: int
        portfolio: object
        sym_cash: object
        _active_tactical_symbol: object
        last_panic_winner: object
        _tactical_winner_set_date: object
        def get_parameter(self, name): ...
        def history(self, symbols, periods: int, resolution): ...
        def GetVixPercentile(self): ...
        def _IsDdImproving(self, lookback_days: int = 5, min_improvement: float = 0.002) -> bool: ...
        def _LogAllowedAt(self, dt=None) -> bool: ...
        def log(self, message) -> None: ...

    _MS_D0 = {
        "PURE_RATE_SHOCK", "INFLATION_RATE_SHOCK",
        "FISCAL_GOLD_STRESS", "EARLY_RISK_OFF",
    }
    _MS_PROFILES = {
        "D0_RATE_SPLIT": _MS_D0,
        "D1_SAFE_REFINE": _MS_D0,
        "D2_COMMODITY": _MS_D0 | {"COMMODITY_LEAD", "STAGFLATION"},
        "D3_RECOVERY": _MS_D0 | {"COMMODITY_LEAD", "STAGFLATION",
                                  "RECOVERY_RE_RISK", "VOL_CRUSH_REBOUND"},
        "D4_FULL": {
            "PURE_RATE_SHOCK", "INFLATION_RATE_SHOCK", "FISCAL_GOLD_STRESS",
            "EARLY_RISK_OFF", "EQUITY_CRASH_SHORT_SHOCK",
            "BOND_HEDGED_RISK_OFF", "DEFLATION_RECESSION",
            "COMMODITY_LEAD", "STAGFLATION", "RECOVERY_RE_RISK",
            "VOL_CRUSH_REBOUND", "RISK_ON_REFLATION", "RISK_ON_TREND",
            "NORMAL_CHOP",
        },
    }

    def _MSParam(self, name, default):
        try:  # QC param has priority over MsInitialize defaults
            p = self.get_parameter(name)
            if p is not None and str(p).strip() != "":
                return p
        except Exception:
            pass
        v = getattr(self, name, None)
        if v is not None:
            return v
        return default

    def _MSBool(self, name, default):
        v = self._MSParam(name, default)
        if isinstance(v, bool):
            return v
        return str(v).strip().lower() not in ("0", "false", "no", "off")

    def _MSFloat(self, name, default):
        try:
            return float(self._MSParam(name, default))
        except Exception:
            return float(default)

    def _MSEnabledRegimes(self, profile):
        custom = self._MSParam("ms_enabled_xregimes", None)
        if custom:
            if isinstance(custom, (set, list, tuple)):
                return set(str(x).strip().upper() for x in custom if str(x).strip())
            s = str(custom).strip()
            if s.upper() == "ALL":
                return set(CoreGrowthMarketStructureMixin._MS_PROFILES["D4_FULL"])
            return set(x.strip().upper() for x in s.replace(";", ",").split(",") if x.strip())
        return set(CoreGrowthMarketStructureMixin._MS_PROFILES.get(
            str(profile).upper(), CoreGrowthMarketStructureMixin._MS_D0))

    def _MSRet(self, hist, sym, days):
        try:
            px = hist.loc[sym]["close"]
            if len(px) < days + 1:
                return None
            a = float(px.iloc[-1]); b = float(px.iloc[-(days + 1)])
            if a <= 0 or b <= 0 or not np.isfinite(a) or not np.isfinite(b):
                return None
            return float(a / b - 1.0)
        except Exception:
            return None

    def _MSF(self, x, default=0.0):
        try:
            return float(x) if x is not None and np.isfinite(float(x)) else default
        except Exception:
            return default

    def MsInitialize(self) -> None:
        """[MS] Default params — zero trading impact. Override via get_parameter()."""
        self.ms_enable          = True
        self.ms_diag_only       = True   # True=no trading impact (D0 canonical)
        self.ms_refine_enable   = False  # override via QC param for D1+
        self.ms_refine_min_conf = 4
        self.ms_profile         = "D0_RATE_SPLIT"
        self.ms_enabled_xregimes = None  # None=use profile defaults
        self._xregime_cache_key  = None
        self._xregime_cache_date = None
        self._xregime_cache      = {}
        self._market_structure_type       = "NA"
        self._market_structure_raw_type   = "NA"
        self._market_structure_confidence = 0
        # [XRD] distribution tracking
        self._ms_raw_counts  = {}  # {year: {regime: days}}
        self._ms_act_counts  = {}  # {year: {regime: days}}
        self._ms_last_xr_year = None
        self._d2a_narrow_tac = False  # [D2A]
        self.d2a_tac_enable  = self._MSBool("d2a_tac_enable", False)  # [D2A] reads QC param

    def GetExpandedRegimeDiag(self, base=None) -> dict:
        profile = str(self._MSParam("ms_profile", "D0_RATE_SPLIT")).upper()
        diag_only = self._MSBool("ms_diag_only", True)
        refine_en = self._MSBool("ms_refine_enable", False) and not diag_only
        min_conf = int(self._MSFloat("ms_refine_min_conf", 4))
        base_ptype = str(base.get("ptype", "NA")) if isinstance(base, dict) else str(getattr(self, "_prev_raw_ptype", "NA"))
        today = self.time.date()
        if base is None and getattr(self, "_xregime_cache_date", None) == today:
            return dict(getattr(self, "_xregime_cache", {}))
        key = (today, profile, base_ptype, diag_only, refine_en, min_conf,
               tuple(sorted(self._MSEnabledRegimes(profile))))
        if getattr(self, "_xregime_cache_key", None) == key:
            return dict(getattr(self, "_xregime_cache", {}))

        d = {
            "ready": False, "raw_xregime": "NA", "xregime": "NA",
            "xregime_enabled": 0, "xconf": 0, "xreason": "not_ready",
            "ptype_refine": None, "profile": profile, "base_ptype": base_ptype,
            "diag_only": int(diag_only), "refine_enabled": int(refine_en),
        }
        if not self._MSBool("ms_enable", True):
            d.update({"xreason": "ms_disabled", "xregime": "NORMAL_CHOP"})
            return d

        try:
            hist = self.history([self.sym_spy, self.sym_bnd, self.sym_dbc,
                                 self.sym_tip, self.sym_gld], 66, Resolution.DAILY)
            if hist is None or hist.empty:
                return d

            spy5 = self._MSRet(hist, self.sym_spy, 5)
            spy10 = self._MSRet(hist, self.sym_spy, 10)
            spy20 = self._MSRet(hist, self.sym_spy, 20)
            spy60 = self._MSRet(hist, self.sym_spy, 60)
            bnd20 = self._MSRet(hist, self.sym_bnd, 20)
            tip20 = self._MSRet(hist, self.sym_tip, 20)
            dbc20 = self._MSRet(hist, self.sym_dbc, 20)
            gld20 = self._MSRet(hist, self.sym_gld, 20)
            if any(x is None for x in (spy5, spy10, spy20, bnd20, tip20, dbc20, gld20)):
                return d

            dbc_spy20 = dbc20 - spy20
            tip_bnd20 = tip20 - bnd20
            gld_bnd20 = gld20 - bnd20
            ps_state = str(getattr(self, "_panic_state", "NORMAL"))
            ids_state = str(getattr(self, "_ids_state", "NORMAL"))
            ps_hot = ps_state in ("WATCH", "STRESS", "PANIC", "RECOVERY")
            ids_hot = ids_state in ("WATCH", "STRESS", "PANIC_SHORT")
            short_hot = bool(getattr(self, "short_shock_flag", False))

            vix_pct = None
            try:
                vix_pct = self.GetVixPercentile()
            except Exception:
                pass
            vix_hi = vix_pct is not None and vix_pct >= float(getattr(self, "vix_high_pct", 0.75))
            vix_lo = vix_pct is not None and vix_pct <= float(getattr(self, "vix_low_pct", 0.35))

            trend_on = trend_off = False
            try:
                if self.spy_ema_9.IsReady and self.spy_ema_120.IsReady and self.spy_sma_200.IsReady:
                    s9 = float(self.spy_ema_9.Current.Value)
                    s120 = float(self.spy_ema_120.Current.Value)
                    s200 = float(self.spy_sma_200.Current.Value)
                    px = float(self.securities[self.sym_spy].price)
                    trend_on = (s9 > s200) and (s120 > s200)
                    trend_off = (px < s200) and (s9 < s120)
            except Exception:
                pass

            dd_imp = False
            try:
                dd_imp = bool(self._IsDdImproving(5, 0.002))
            except Exception:
                pass

            equity = int(spy5 <= -0.02) + int(spy10 <= -0.035) + int(spy20 <= -0.05)
            equity += int(vix_hi) + int(ps_hot) + int(ids_hot) + int(short_hot)
            rate = int(bnd20 <= -0.010) + int(tip20 < 0.0) + int(gld20 < 0.02)
            rate += int(spy20 <= -0.005) + int(ids_hot)
            infl = int(tip_bnd20 >= 0.005) + int(tip_bnd20 >= 0.015)
            infl += int(dbc_spy20 >= 0.030) + int(dbc_spy20 >= 0.080) + int(dbc20 > 0.0)
            comm = int(dbc_spy20 >= 0.030) + int(dbc_spy20 >= 0.080) + int(dbc20 >= 0.020)
            comm += int(tip_bnd20 >= 0.005)
            deff = int(dbc_spy20 <= -0.020) + int(dbc20 < 0.0) + int(bnd20 >= 0.005)
            deff += int(gld20 >= 0.020) + int(spy20 <= -0.050)
            goldf = int(gld20 >= 0.040) + int(gld_bnd20 >= 0.050) + int(bnd20 <= -0.010)
            goldf += int(dbc_spy20 < 0.080) + int(tip_bnd20 < 0.015)
            rec = int(spy5 >= 0.020) + int(spy10 >= 0.035) + int(dd_imp)
            rec += int(vix_lo) + int(ps_state == "RECOVERY")
            trend = int(trend_on) + int(spy20 > 0.0) + int(self._MSF(spy60) > 0.0)

            raw = "NORMAL_CHOP"; why = "low_scores"
            if short_hot and (spy5 <= -0.025 or equity >= 4):
                raw, why = "EQUITY_CRASH_SHORT_SHOCK", "short_shock_spy_drop"
            elif rate >= 3 and infl >= 3 and spy20 < -0.02:
                raw, why = "INFLATION_RATE_SHOCK", "rate_plus_inflation"
            elif comm >= 4 and infl >= 2 and spy20 < 0.0:
                raw, why = "STAGFLATION", "commodity_inflation_equity_weak"
            elif deff >= 4 and equity >= 2:
                raw, why = "DEFLATION_RECESSION", "growth_down_bonds_or_gold_bid"
            elif goldf >= 4 and comm < 4:
                raw, why = "FISCAL_GOLD_STRESS", "gold_up_bonds_weak"
            elif rate >= 3 and comm <= 2 and gld20 < 0.04:
                raw, why = "PURE_RATE_SHOCK", "bonds_tip_down_no_gold_refuge"
            elif bnd20 >= 0.005 and equity >= 2 and infl <= 1 and comm <= 1:
                raw, why = "BOND_HEDGED_RISK_OFF", "bonds_hedging_equity_stress"
            elif comm >= 3 and spy20 > -0.03:
                raw, why = "COMMODITY_LEAD", "commodities_lead_without_crash"
            elif rec >= 4 and vix_lo and spy5 >= 0.02:
                raw, why = "VOL_CRUSH_REBOUND", "fast_rebound_low_vix"
            elif rec >= 3 and equity <= 2:
                raw, why = "RECOVERY_RE_RISK", "spy_rebound_dd_improving"
            elif trend >= 2 and infl >= 2:
                raw, why = "RISK_ON_REFLATION", "trend_with_inflation_beta"
            elif trend >= 2 and not trend_off:
                raw, why = "RISK_ON_TREND", "trend_confirmed"
            elif equity >= 2:
                raw, why = "EARLY_RISK_OFF", "stress_before_structure"

            enabled_set = self._MSEnabledRegimes(profile)
            in_profile = raw in enabled_set
            in_scope = base_ptype == "RATE_SHOCK_UNKNOWN" if profile == "D0_RATE_SPLIT" else True
            enabled = bool(in_profile and in_scope)
            active = raw if enabled else "NORMAL_CHOP"
            if not enabled:
                why = f"{why}|disabled"
                if not in_scope:
                    why += f":base={base_ptype}"

            pmap = {
                "PURE_RATE_SHOCK": "RATE_SHOCK_UNKNOWN",
                "INFLATION_RATE_SHOCK": "COMMODITY_INFL",
                "FISCAL_GOLD_STRESS": "FISCAL_USD",
                "EARLY_RISK_OFF": "RATE_SHOCK_UNKNOWN",
                "STAGFLATION": "COMMODITY_INFL",
                "COMMODITY_LEAD": "COMMODITY_LEAD",
                "DEFLATION_RECESSION": "DEFL_RECESSION",
                "BOND_HEDGED_RISK_OFF": "BOND_HEDGED_RISK_OFF",
                "EQUITY_CRASH_SHORT_SHOCK": "RATE_SHOCK_UNKNOWN",
            }
            score_max = max(equity, rate, infl, comm, deff, goldf, rec, trend)
            conf = int(min(5, score_max))
            pt_ref = None
            if refine_en and enabled and conf >= min_conf:
                pt_ref = pmap.get(active)

            d = {
                "ready": True, "profile": profile, "base_ptype": base_ptype,
                "diag_only": int(diag_only), "refine_enabled": int(refine_en),
                "raw_xregime": raw, "xregime": active,
                "xregime_enabled": int(enabled), "xconf": conf,
                "xreason": why, "ptype_refine": pt_ref,
                "raw_ptype_refine": pmap.get(raw),
                "eqs": equity, "rates": rate, "infs": infl, "comms": comm,
                "defs": deff, "goldfs": goldf, "recs": rec, "trends": trend,
                "spy5": spy5, "spy10": spy10, "spy20": spy20, "spy60": spy60,
                "bnd20": bnd20, "tip20": tip20, "dbc20": dbc20, "gld20": gld20,
                "dbc_spy20": dbc_spy20, "tip_bnd20": tip_bnd20, "gld_bnd20": gld_bnd20,
            }
            self._xregime_cache_key = key
            self._xregime_cache_date = today
            self._xregime_cache = dict(d)
            self._market_structure_type = active
            self._market_structure_raw_type = raw
            self._market_structure_confidence = conf
            self._d2a_narrow_tac = (base_ptype in ("COMMODITY_INFL","COMMODITY_LEAD") and raw in ("INFLATION_RATE_SHOCK","EQUITY_CRASH_SHORT_SHOCK") and conf >= 4)  # [D2A]
            # [XRD] accumulate daily counts (once per fresh computation)
            yr = today.year
            rc = self._ms_raw_counts.setdefault(yr, {})
            rc[raw]    = rc.get(raw, 0) + 1
            ac = self._ms_act_counts.setdefault(yr, {})
            ac[active] = ac.get(active, 0) + 1
            # year-change: emit previous year summary in backtest
            prev = getattr(self, "_ms_last_xr_year", None)
            if prev and prev != yr and not self.live_mode:
                self._EmitXRDYear(prev)
            self._ms_last_xr_year = yr
            return d
        except Exception as e:
            d["xreason"] = f"error:{e}"
            return d

    def GetPanicStructureDiag(self) -> dict:
        d = CoreGrowthRiskTacticalMixin.GetPanicStructureDiag(self)
        try:
            x = self.GetExpandedRegimeDiag(d)
            d.update({
                "raw_xregime": x.get("raw_xregime", "NA"),
                "xregime": x.get("xregime", "NA"),
                "xregime_enabled": x.get("xregime_enabled", 0),
                "xconf": x.get("xconf", 0),
                "xreason": x.get("xreason", "NA"),
            })
            rp = x.get("ptype_refine")
            old = d.get("ptype", "NA")
            can_refine = old in ("UNKNOWN", "RATE_SHOCK_UNKNOWN", "NA")
            if d.get("ready") and rp and can_refine and x.get("refine_enabled", 0):  # [MS] gate
                d["base_ptype"] = old
                d["ptype"] = rp
                d["reason"] = f"{d.get('reason','NA')}|x={x.get('xregime','NA')}"
        except Exception:
            pass
        return d

    def EmitPanicStructureDiag(self, context: str = "") -> None:
        try:
            CoreGrowthRiskTacticalMixin.EmitPanicStructureDiag(self, context)
        except Exception as e:
            if self.live_mode or getattr(self, "debug_regime", False):
                self.log(f"PANIC_STRUCT error: {e}")
        try:
            self.EmitExpandedRegimeDiag(context)
        except Exception as e:
            if self.live_mode or getattr(self, "debug_regime", False):
                self.log(f"XREGIME error: {e}")

    def EmitExpandedRegimeDiag(self, context: str = "") -> None:
        if getattr(self,"log_quiet_mode",False): return  # [LOG-BUDGET]
        if not self.live_mode and not self._LogAllowedAt():
            return
        x = self.GetExpandedRegimeDiag()
        if not x.get("ready"): return
        self.log(
            f"XREGIME,{self.time.date()},ctx={context}"
            f",prof={x.get('profile','NA')},diag={x.get('diag_only',1)}"
            f",ref_en={x.get('refine_enabled',0)},base={x.get('base_ptype','NA')}"
            f",raw={x.get('raw_xregime','NA')},x={x.get('xregime','NA')}"
            f",en={x.get('xregime_enabled',0)},conf={x.get('xconf',0)}"
            f",why={x.get('xreason','NA')},ref={x.get('ptype_refine') or 'NONE'}"
            f",eq={x.get('eqs',0)},rate={x.get('rates',0)},infl={x.get('infs',0)}"
            f",comm={x.get('comms',0)},defl={x.get('defs',0)}"
            f",goldf={x.get('goldfs',0)},rec={x.get('recs',0)},trend={x.get('trends',0)}"
        )

    def _EmitXRDYear(self, year: int) -> None:
        """Emit compact XRD distribution line for one year. [XRD]"""
        raw_c = self._ms_raw_counts.get(year, {})
        act_c = self._ms_act_counts.get(year, {})
        if not raw_c: return
        total = sum(raw_c.values())
        raw_s = "|".join(f"{k}:{v}" for k, v in sorted(raw_c.items(), key=lambda x: -x[1]))
        act_s = "|".join(f"{k}:{v}" for k, v in sorted(act_c.items(), key=lambda x: -x[1]))
        self.log(f"XRD,{year},{total},raw={raw_s},act={act_s}")  # [XRD]

    def EmitXRegimeFinalDist(self) -> None:
        """Emit XRD summary for all tracked years. Call from OnEndOfAlgorithm. [XRD]"""
        for yr in sorted(self._ms_raw_counts.keys()):
            self._EmitXRDYear(yr)

    def IsBearRallyBlocked(self) -> bool:
        # [BRG-C1] True when NEUTRAL regime with positive SPY/BND corr and negative DBC/SPY.
        if not bool(getattr(self, "bear_rally_gate_enable", False)):
            return False
        try:
            if str(getattr(self, "current_regime", "")) != "NEUTRAL":
                return False
            ps = self.GetPanicStructureDiag()
            if not ps.get("ready", False):
                return False
            corr    = float(ps.get("spy_bnd_corr20", 0.0) or 0.0)
            dbc_spy = float(ps.get("dbc_spy20",    0.0) or 0.0)
            return (corr    >  float(getattr(self, "bear_rally_corr_min",    0.25))
                    and dbc_spy < float(getattr(self, "bear_rally_dbc_spy_max", 0.0)))
        except Exception:
            return False

    def IsRateShockEquityBlocked(self) -> bool:
        # [BRG-C1B] True when rate-shock with high corr, negative bonds, and weak SPY.
        if not bool(getattr(self, "bear_rally_gate_enable", False)):
            return False
        try:
            ps = self.GetPanicStructureDiag()
            if not ps.get("ready", False):
                return False
            ptype  = str(ps.get("ptype", "NA"))
            corr   = float(ps.get("spy_bnd_corr20", 0.0) or 0.0)
            bnd20  = float(ps.get("bnd20",          0.0) or 0.0)
            spy20  = float(ps.get("spy20",           0.0) or 0.0)
            regime = str(getattr(self, "current_regime", ""))
            return (regime in ("NEUTRAL", "RISK_OFF")
                    and ptype == "RATE_SHOCK_UNKNOWN"
                    and corr  >  float(getattr(self, "bear_rally_rate_corr_min",  0.50))
                    and bnd20 <  float(getattr(self, "bear_rally_rate_bnd20_max", -0.015))
                    and spy20 <  float(getattr(self, "bear_rally_rate_spy20_max",  0.0)))
        except Exception:
            return False

    def _IsB2SpyBlocked(self, ps: dict) -> bool:
        # [BRG-B2] NEUTRAL + rate-stress signals + active IDS/panic confirmation.
        if str(getattr(self, "current_regime", "")) != "NEUTRAL":
            return False
        corr  = float(ps.get("spy_bnd_corr20", 0.0) or 0.0)
        bnd20 = float(ps.get("bnd20",          0.0) or 0.0)
        spy20 = float(ps.get("spy20",          0.0) or 0.0)
        if (corr  <= float(getattr(self, "bear_rally_b2_corr_min",  0.40))
                or bnd20 >= float(getattr(self, "bear_rally_b2_bnd20_max", -0.015))
                or spy20 >= 0.0):
            return False
        ids_st = str(getattr(self, "_ids_state", "NORMAL"))
        return bool(getattr(self, "panic_mode_active", False)) or ids_st in ("WATCH", "STRESS", "PANIC_SHORT")


    # -----------------------------------------------------------------------
    # [C1R-S3OFF] Duration C1R veto -- ge4-only, s3dur removed.
    # Independent of bear_rally_gate_enable.
    # -----------------------------------------------------------------------

    def _UpdateC1RGe4State(self) -> None:
        """Update _c1r_ge4_active: block duration OFF only when score >= 4."""
        raw_on    = bool(getattr(self, "_dur_bonds_broken", False))
        abs_score = int(getattr(self, "_dur_score", 0))
        prev_raw  = bool(getattr(self, "_c1r_raw_prev", False))
        candidate_off = prev_raw and not raw_on
        ge4_block = candidate_off and abs_score >= 4
        if raw_on:
            self._c1r_ge4_active = True
        elif ge4_block:
            self._c1r_ge4_active = True
        else:
            self._c1r_ge4_active = raw_on  # False
        self._c1r_raw_prev = raw_on

    def _ApplyDurC1RVeto(self, w: dict) -> dict:
        """Block duration re-entry while c1r_ge4_active; redirect to cash."""
        if not getattr(self, "_c1r_ge4_active", False):
            return w
        if bool(getattr(self, "_dur_bonds_broken", False)):
            return w  # raw broken mode handles itself
        try:
            tv = float(self.portfolio.total_portfolio_value)
            if tv <= 0:
                return w
            dur_attrs = ["sym_bnd", "sym_tip"]
            freed = 0.0
            details = []
            for attr in dur_attrs:
                sym = getattr(self, attr, None)
                if sym is None or sym not in w:
                    continue
                proposed = float(w.get(sym, 0.0) or 0.0)
                try:
                    current = float(self.portfolio[sym].HoldingsValue or 0) / tv
                except Exception:
                    current = 0.0
                add = proposed - current
                if add > 0.005:
                    w[sym] = max(0.0, current)
                    freed += add
                    details.append(f"{sym.Value}:{proposed:.3f}->{current:.3f}")
            if freed > 0:
                sym_c = getattr(self, "sym_cash", None)
                if sym_c:
                    w[sym_c] = float(w.get(sym_c, 0.0) or 0.0) + freed
                score = int(getattr(self, "_dur_score", 0))
                h2    = int(getattr(self, "_dur_h2_state",  False))
                rg    = int(getattr(self, "_dur_rg_state",  False))
                b60   = int(getattr(self, "_dur_b60_state", False))
                dsco  = int(getattr(self, "_dur_dyn_score", 0))
                self.log(
                    f"C1R_C1_S3OFF,{self.time.date()},"
                    f"act=1,reason=ge4_hold,"
                    f"score={score},raw=0,h2={h2},rg={rg},b60={b60},"
                    f"dyn_score={dsco},"
                    f"freed={freed:.3f},dest=CASH,"
                    f"blocked={'|'.join(details)}"
                )
        except Exception as e:
            if self.live_mode or getattr(self, "debug_regime", False):
                self.log(f"[C1R_C1_S3OFF] error: {e}")
        return w

    def ApplyBearRallyGate(self, w: dict) -> dict:
        # [BRG-C1/C1B/B2] Block SPY/XLV/XLU increases in bear-rally or rate-shock conditions.
        # [C1R-S3OFF] Duration veto runs independently before BRG gate
        self._UpdateC1RGe4State()
        if bool(getattr(self, "c1r_ge4_enable", False)) or (self.get_parameter("c1r_ge4_enable") or "0") == "1":
            w = self._ApplyDurC1RVeto(w)
        if not bool(getattr(self, "bear_rally_gate_enable", False)):
            return w
        try:
            ps = self.GetPanicStructureDiag()
            if not ps.get("ready", False):
                return w
            corr    = float(ps.get("spy_bnd_corr20", 0.0) or 0.0)
            dbc_spy = float(ps.get("dbc_spy20",      0.0) or 0.0)
            bnd20   = float(ps.get("bnd20",           0.0) or 0.0)
            spy20   = float(ps.get("spy20",           0.0) or 0.0)
            regime  = str(getattr(self, "current_regime", ""))

            # C1: NEUTRAL + positive corr + negative DBC/SPY
            brg = (regime == "NEUTRAL"
                   and corr    >  float(getattr(self, "bear_rally_corr_min",    0.25))
                   and dbc_spy <  float(getattr(self, "bear_rally_dbc_spy_max", 0.0)))
            # C1B: RATE_SHOCK_UNKNOWN + high corr + negative bonds + weak SPY
            rsb = getattr(self, "IsRateShockEquityBlocked", lambda: False)()
            # B2: NEUTRAL + rate-stress signals + active IDS/panic
            b2 = self._IsB2SpyBlocked(ps)

            # [BRG-D0] Correlation validity audit -- diagnostic only
            if (self.get_parameter("brg_d0_enable") or "0") == "1":
                bnd20_pos = int(bnd20 > 0)
                corr_inv  = int(
                    bnd20_pos and
                    corr > float(getattr(self, "bear_rally_corr_min", 0.25))
                )
                any_gate = int(brg or rsb or b2)
                if any_gate or bnd20_pos:  # only log interesting days
                    self.log(
                        f"BRG_D0,{self.time.date()},"
                        f"reg={regime},corr={corr:.3f},"
                        f"bnd20={bnd20:.3f},dbc_spy={dbc_spy:.3f},spy20={spy20:.3f},"
                        f"brg_c1={int(brg)},brg_rsb={int(rsb)},brg_b2={int(b2)},"
                        f"any_gate={any_gate},"
                        f"bnd20_pos={bnd20_pos},corr_inv={corr_inv}"
                    )

            if not brg and not rsb and not b2:
                self._brg_corr_inv = False; self._brg_freed = 0.0  # [SHADOW-NAV]
                return w

            tv = float(self.portfolio.total_portfolio_value)
            if tv <= 0:
                return w

            # brg/rsb gate SPY+XLV+XLU; b2 gates SPY only
            if brg or rsb:
                _gated_vals = {"XLV", "XLU"}
                gated = [self.sym_spy] + [
                    s for s in getattr(self, "panic_tactical_universe", [])
                    if s.Value in _gated_vals]
            else:
                gated = [self.sym_spy]
            freed = 0.0
            details = []
            min_add = float(getattr(self, "bear_rally_min_add", 0.005))
            for sym in gated:
                proposed = float(w.get(sym, 0.0) or 0.0)
                try:
                    current = float(self.portfolio[sym].HoldingsValue) / tv
                except Exception:
                    current = 0.0
                add = proposed - current
                if add > min_add:
                    w[sym] = max(0.0, current)
                    freed += add
                    details.append(f"{sym.Value}:{proposed:.3f}->{current:.3f}")
            if freed > 0.0:
                w[self.sym_cash] = float(w.get(self.sym_cash, 0.0) or 0.0) + freed
                if self.live_mode or self._LogAllowedAt():
                    tag = "BRG_C1B" if rsb else ("BRG_B2" if b2 else "BEAR_RALLY_GATE")
                    self.log(
                        f"{tag},{self.time.date()},"
                        f"reg={regime},corr={corr:.3f},"
                        f"dbc_spy={dbc_spy:.3f},bnd20={bnd20:.3f},spy20={spy20:.3f},"
                        f"freed={freed:.3f},blocked={'|'.join(details)}"
                    )
                # [SHADOW-NAV] Store BRG invalid signal for shadow NAV
                _c_min = float(getattr(self,"bear_rally_corr_min",0.25))
                self._brg_corr_inv = bool(bnd20 > 0 and corr > _c_min)
                self._brg_freed    = float(freed)
            else:
                self._brg_corr_inv = False; self._brg_freed = 0.0  # [SHADOW-NAV]
        except Exception as e:
            if self.live_mode or getattr(self, "debug_regime", False):
                self.log(f"[BEAR_RALLY_GATE_ERR] {self.time.date()} {e}")
        return w

    def ApplyXleNoiseExitVeto(self, w: dict) -> dict:
        # [TAC-HOLD-C1C] Log XLE exit noise / apply reduce-veto if confirmed.
        _d0 = getattr(self, "xle_noise_d0_enable", False)
        _ve = getattr(self, "xle_noise_veto_enable", False)
        if not _d0 and not _ve:
            return w
        try:
            sym_xle = next(
                (s for s in getattr(self, "panic_tactical_universe", []) if s.Value == "XLE"),
                None)
            if sym_xle is None:
                return w

            # Portfolio state
            try:
                hld = self.portfolio[sym_xle]
                holding_xle = bool(hld.Invested)
                tv = float(self.portfolio.total_portfolio_value)
                xle_port_w = float(hld.HoldingsValue) / tv if tv > 0 and holding_xle else 0.0
            except Exception:
                holding_xle = False
                xle_port_w = 0.0

            # Regime/state fields (no-fail)
            ptype   = str(getattr(self, "_prev_raw_ptype", "NA"))
            regime  = str(getattr(self, "current_regime", "NA"))
            panic   = int(bool(getattr(self, "panic_mode_active", False)))
            winner  = getattr(self, "_active_tactical_symbol", None)
            w_val   = winner.Value if winner else "NONE"

            # Price history: XLE + SPY + DBC
            try:
                _syms = [sym_xle, self.sym_spy, self.sym_dbc]
                _h = self.history(_syms, 25, Resolution.DAILY)
                def _px(s):
                    try: return _h.loc[s]["close"].to_numpy(dtype=float)
                    except Exception: return None
                xc = _px(sym_xle); sc = _px(self.sym_spy); dc = _px(self.sym_dbc)
                def _r(arr, n):
                    return float(arr[-1]/arr[-(n+1)]-1) if arr is not None and len(arr)>n else 0.
                xle5=_r(xc,5); xle10=_r(xc,10); xle20=_r(xc,20)
                spy5=_r(sc,5); spy10=_r(sc,10); spy20=_r(sc,20)
                dbc5=_r(dc,5); dbc10=_r(dc,10); dbc20=_r(dc,20)
                xle_spy5=xle5-spy5; xle_spy10=xle10-spy10; xle_spy20=xle20-spy20
                xle_dbc5=xle5-dbc5; xle_dbc10=xle10-dbc10; xle_dbc20=xle20-dbc20
                dbc_spy10=dbc10-spy10; dbc_spy20=dbc20-spy20
                # EMA
                def _ema(arr, n):
                    if arr is None or len(arr) < n: return 0.
                    k=2/(n+1); e=float(arr[0])
                    for p in arr[1:]: e=float(p)*k+e*(1-k)
                    return e
                ema10=_ema(xc,10); ema20=_ema(xc,20)
                last_xle = float(xc[-1]) if xc is not None and len(xc) else 0.
                below10=int(last_xle<ema10 and ema10>0)
                below20=int(last_xle<ema20 and ema20>0)
            except Exception:
                xle5=xle10=xle20=spy5=spy10=spy20=dbc5=dbc10=dbc20=0.
                xle_spy5=xle_spy10=xle_spy20=0.
                xle_dbc5=xle_dbc10=xle_dbc20=0.
                dbc_spy10=dbc_spy20=0.
                below10=below20=0

            # GetPanicStructureDiag for tip_bnd20
            ps = self.GetPanicStructureDiag()
            tip_bnd20 = float(ps.get("tip_bnd20", 0) or 0)

            # Signals
            _ht = float(getattr(self, "xle_noise_hard_xle_spy10", -0.03))
            soft_exit = int(xle_spy10 < 0 or xle_dbc10 < 0 or below10)
            hard_exit = int(
                (xle_spy10 < _ht and xle_dbc10 < _ht and bool(below20))
                or (dbc_spy20 < 0 and xle_spy20 < 0)
                or xle10 < float(getattr(self, "xle_noise_hard_xle10", -0.08))
            )

            # Persistence streak
            prev_s = int(getattr(self, "_xle_noise_streak", 0))
            streak = (prev_s + 1) if hard_exit else 0
            self._xle_noise_streak = streak
            cd = int(getattr(self, "xle_noise_confirm_days", 3))
            bd_confirmed = int(streak >= cd)

            # D0 log
            if _d0 and (self.live_mode or self._LogAllowedAt()):
                self.log(
                    f"XLE_NOISE_D0,{self.time.date()}"
                    f",held={int(holding_xle)},w_val={w_val},pt={ptype},reg={regime}"
                    f",panic={panic}"
                    f",xle5={xle5:.3f},xle10={xle10:.3f},xle20={xle20:.3f}"
                    f",xspy5={xle_spy5:.3f},xspy10={xle_spy10:.3f},xspy20={xle_spy20:.3f}"
                    f",xdbc5={xle_dbc5:.3f},xdbc10={xle_dbc10:.3f},xdbc20={xle_dbc20:.3f}"
                    f",dbc20={dbc20:.3f},dsp10={dbc_spy10:.3f},dsp20={dbc_spy20:.3f}"
                    f",tb20={tip_bnd20:.3f}"
                    f",ema10={below10},ema20={below20}"
                    f",soft={soft_exit},hard={hard_exit}"
                    f",streak={streak},bd={bd_confirmed}"
                    f",old_tgt={float(w.get(sym_xle,0)):.3f},port={xle_port_w:.3f}"
                )

            # Veto: only if veto enabled, holding XLE, target being reduced
            if not _ve:
                return w
            if not holding_xle:
                return w
            if bd_confirmed:
                return w  # breakdown confirmed — allow exit
            old_t = float(w.get(sym_xle, 0.0))
            if old_t >= xle_port_w:
                return w  # not being reduced
            age = 0
            ed = getattr(self, "_tactical_winner_set_date", None)
            if ed:
                age = (self.time.date() - ed).days
            if age > int(getattr(self, "xle_noise_max_hold_days", 75)):
                return w
            # Cash-only restore
            delta = min(xle_port_w, float(getattr(self, "xle_noise_max_weight", 0.25))) - old_t
            cash_w = float(w.get(self.sym_cash, 0.0))
            if delta <= 0 or cash_w < delta:
                return w
            w[sym_xle] = old_t + delta
            w[self.sym_cash] = cash_w - delta
            if self.live_mode or self._LogAllowedAt():
                self.log(f"XLE_NOISE_VETO,{self.time.date()},hard={hard_exit},streak={streak},delta={delta:.3f}")
        except Exception as e:
            if self.live_mode or getattr(self, "debug_regime", False):
                self.log(f"[XLE_NOISE_ERR] {self.time.date()} {e}")
        return w

    def ApplyCommodityTacticalHold(self, w: dict) -> dict:
        # [TAC-HOLD-C1B] Reduce-veto: preserve existing XLE/XLB. No new entry.
        if not getattr(self, "tac_hold_enable", False):
            return w
        try:
            act = getattr(self, "_active_tactical_symbol", None) or getattr(self, "last_panic_winner", None)
            if not act:
                return w
            if act.Value not in getattr(self, "tac_hold_assets", {"XLE", "XLB"}):
                return w
            h = self.portfolio[act]
            if not h.Invested:
                return w
            tv = float(self.portfolio.total_portfolio_value)
            if tv <= 0:
                return w
            cw = float(h.HoldingsValue) / tv
            if cw < getattr(self, "tac_hold_min_current_weight", 0.010):
                return w
            ed = getattr(self, "_tactical_winner_set_date", None)
            if ed and (self.time.date() - ed).days > int(getattr(self, "tac_hold_max_days", 30)):
                return w
            ps = self.GetPanicStructureDiag()
            if not ps.get("ready"):
                return w
            pt = str(ps.get("ptype", "")).upper()
            if pt not in getattr(self, "tac_hold_ptypes", {"COMMODITY_INFL", "COMMODITY_LEAD", "RATE_SHOCK_UNKNOWN"}):
                if self.live_mode or self._LogAllowedAt():
                    self.log(f"TAC_HOLD_C1B_SKIP,{self.time.date()},{act.Value},ptype={pt}")
                return w
            if float(ps.get("dbc_spy20", 0) or 0) < float(getattr(self, "tac_hold_dbc_spy_min", 0.08)):
                return w
            if float(ps.get("tip_bnd20", 0) or 0) < float(getattr(self, "tac_hold_tip_bnd_min", 0.0)):
                return w
            try:
                _hst = self.history(act, 12, Resolution.DAILY)
                try:
                    _c = _hst.loc[act]["close"].to_numpy(dtype=float)
                except Exception:
                    _c = _hst["close"].to_numpy(dtype=float)
                if len(_c) < 11 or not np.all(np.isfinite(_c)) or np.any(_c <= 0):
                    return w
                r5 = float(_c[-1] / _c[-6] - 1)
                r10 = float(_c[-1] / _c[-11] - 1)
            except Exception:
                return w
            if r5 < float(getattr(self, "tac_hold_symbol_5d_min", -0.060)):
                return w
            if r10 < float(getattr(self, "tac_hold_symbol_10d_min", -0.080)):
                return w
            hd = min(cw, float(getattr(self, "tac_hold_max_weight", 0.250)))
            old = float(w.get(act, 0.0))
            if old >= hd:
                return w
            # [TAC-HOLD-C1B] Explicit cash financing: undo carry reduction, keep sum=1.
            delta = hd - old
            cash_w = float(w.get(self.sym_cash, 0.0))
            if cash_w < delta:
                return w  # insufficient cash — skip veto
            w[act] = hd
            w[self.sym_cash] = cash_w - delta
            if self.live_mode or self._LogAllowedAt():
                age = (self.time.date() - ed).days if ed else -1
                self.log(
                    f"TAC_HOLD_C1B,{self.time.date()},{act.Value}"
                    f",pt={pt},age={age},cw={cw:.3f},old={old:.3f},new={hd:.3f}"
                    f",delta={delta:.3f},cash={cash_w:.3f}"
                    f",dbc={ps.get('dbc_spy20',0):.3f},tip={ps.get('tip_bnd20',0):.3f}"
                    f",r5={r5:.3f},r10={r10:.3f}"
                )
        except Exception as e:
            if self.live_mode or getattr(self, "debug_regime", False):
                self.log(f"[TAC_HOLD_C1B_ERR] {self.time.date()} {e}")
        return w