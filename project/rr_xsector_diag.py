# rr_xsector_diag.py
# Tags: [RRX]

from AlgorithmImports import *
from rr_xsector_shadow import (
    _RRXShadowInit,
    _RRXShadowDailyRet, _RRXShadowExecRet,
    _RRXMetaUpdateRollingStress, _RRXAttrUpdate,
    _RRXThemeOfStock, _RRXLeadPass, _RRXChooseReentry, _RRXSmaGateLayers, _RRXSmaGateOk,
    _RRXVolSizeForTarget, _RRXSizedReturnForTarget, _RRXD5XUpdateRisk,
    _RRXD5XApplySymbolReturn, _RRXD5XApplyCashReturn,
    _RRXD5YTarget, _RRXD5YApplyReturn, _RRXD5YApplyCash,
    _RRXD5ZTarget, _RRXD5ZApplyReturn, _RRXD5ZApplyCash,
    _RRXVolSize, _RRXStopUpdate,
    _RRXShadowUpdate, _RRXUpdateSumCounters,
    _RRXEmitMonthlySummary, RRXEmitFinalSummary,
)

from typing import TYPE_CHECKING
from datetime import datetime, date as _date

# [D6] leader-first diagnostic (rrx_leader_first_diag.py)
from rrx_leader_first_diag import (
    RRXD6LeaderFirstInitialize as _D6Init,
    RRXD6LeaderFirstUpdate     as _D6Update,
    _RRXD6StockScore           as _D6StockScore,
    _RRXD6FindLeader           as _D6FindLeader,
    _RRXD6PxOf                 as _D6PxOf,
    _RRXD6Worst5Pct            as _D6Worst5,
    RRXD6EmitMonthly           as _D6EmitMonthly,
    RRXD6EmitFinal             as _D6EmitFinal,
)

RRX_THEMES = {
    "SEMIS":         {"etf": "SMH",  "stocks": ["MU",   "NVDA", "AVGO"], "risk_group": "GROWTH_CYCLICAL"},
    "TECH":          {"etf": "XLK",  "stocks": ["MSFT", "AAPL", "GOOGL"],"risk_group": "GROWTH_CYCLICAL"},
    "DEFENSE":       {"etf": "ITA",  "stocks": ["RTX",  "LMT",  "NOC"],  "risk_group": "GEOPOLITICAL"},
    "BIOTECH":       {"etf": "IBB",  "stocks": ["AMGN", "GILD", "REGN"], "risk_group": "HEALTH_VOLATILE"},
    "HEALTH_CORE":   {"etf": "XLV",  "stocks": ["UNH",  "JNJ",  "ABT"],  "risk_group": "DEFENSIVE_HEALTH"},
    "PHARMA":        {"etf": "XPH",  "stocks": ["LLY",  "PFE",  "MRK"],  "risk_group": "DEFENSIVE_HEALTH"},
    "MEDICAL_DEV":   {"etf": "IHI",  "stocks": ["SYK",  "MDT",  "BSX"],  "risk_group": "DEFENSIVE_HEALTH"},
    "ENERGY":        {"etf": "XLE",  "stocks": ["XOM",  "CVX",  "COP"],  "risk_group": "INFLATION_CYCLICAL"},
    "OIL_SERVICES":  {"etf": "OIH",  "stocks": ["HAL",  "SLB",  "NOV"],  "risk_group": "INFLATION_CYCLICAL"},
    "GOLD_MINERS":   {"etf": "GDX",  "stocks": ["NEM",  "AEM",  "GFI"],  "risk_group": "SAFE_HAVEN"},
    "FINANCIALS":    {"etf": "XLF",  "stocks": ["JPM",  "BAC",  "GS"],   "risk_group": "CYCLICAL"},
    "INDUSTRIALS":   {"etf": "XLI",  "stocks": ["CAT",  "HON",  "DE"],   "risk_group": "CYCLICAL"},
    "MATERIALS":     {"etf": "XLB",  "stocks": ["APD",  "NUE",  "FCX"],  "risk_group": "CYCLICAL"},
    "DISCRETIONARY": {"etf": "XLY",  "stocks": ["AMZN", "HD",   "NKE"],  "risk_group": "GROWTH_CYCLICAL"},
    "HOME_BUILDERS": {"etf": "ITB",  "stocks": ["DHI",  "LEN",  "PHM"],  "risk_group": "CYCLICAL"},
    "TRANSPORT":     {"etf": "XTN",  "stocks": ["UPS",  "FDX",  "DAL"],  "risk_group": "CYCLICAL"},
    "UTILITIES":     {"etf": "XLU",  "stocks": ["NEE",  "DUK",  "SO"],   "risk_group": "DEFENSIVE"},
    "STAPLES":       {"etf": "XLP",  "stocks": ["PG",   "KO",   "WMT"],  "risk_group": "DEFENSIVE"},
    "URANIUM":       {"etf": "URA",  "stocks": ["CCJ",  "NXE",  "UEC"],  "risk_group": "THEMATIC"},
    "REAL_ESTATE":   {"etf": "VNQ",  "stocks": ["AMT",  "PLD",  "SPG"],  "risk_group": "CYCLICAL"},
}

RRX_IDLE           = "RRX_IDLE"
RRX_ACTIVE         = "RRX_ACTIVE"
RRX_STRONG         = "RRX_STRONG"
RRX_OVERHEATED     = "RRX_OVERHEATED"
RRX_DAMAGED        = "RRX_DAMAGED"
RRX_DEFENSIVE_ONLY = "RRX_DEFENSIVE_ONLY"

_TC_ROCKET    = "ROCKET"
_TC_POSITIVE  = "POSITIVE"
_TC_DEFENSIVE = "DEFENSIVE"
_TC_WEAK      = "WEAK"
_TC_DAMAGED   = "DAMAGED"

class RRXSectorDiagMixin:

    if TYPE_CHECKING:
        time: datetime
        live_mode: bool
        portfolio: object
        securities: object
        sym_spy: object
        is_warming_up: bool
        short_shock_flag: bool
        emergency_stop_triggered: bool
        dd_soft_start: float
        _panic_state: str
        _ids_state: str
        _active_tactical_symbol: object
        def add_equity(self, ticker, resolution): ...
        def roc(self, symbol, period, resolution): ...
        def sma(self, symbol, period, resolution): ...
        def rsi(self, symbol, period, mtype, resolution): ...
        def get_parameter(self, name: str): ...
        def log(self, msg: str): ...
        def _LogAllowedAt(self, dt=None) -> bool: ...
        def CurrentDrawdown(self) -> float: ...
        def RRXD6LeaderFirstInitialize(self) -> None: ...
        def RRXD6LeaderFirstUpdate(self) -> None: ...
        def RRXD6EmitFinal(self) -> None: ...


    # Shadow methods (from rr_xsector_shadow.py)
    _RRXShadowDailyRet          = _RRXShadowDailyRet
    _RRXShadowExecRet           = _RRXShadowExecRet
    _RRXMetaUpdateRollingStress = _RRXMetaUpdateRollingStress
    _RRXAttrUpdate              = _RRXAttrUpdate
    _RRXThemeOfStock            = _RRXThemeOfStock
    _RRXLeadPass                = _RRXLeadPass
    _RRXChooseReentry           = _RRXChooseReentry
    _RRXSmaGateLayers           = _RRXSmaGateLayers
    _RRXSmaGateOk               = _RRXSmaGateOk
    _RRXVolSizeForTarget        = _RRXVolSizeForTarget
    _RRXSizedReturnForTarget    = _RRXSizedReturnForTarget
    _RRXD5XUpdateRisk           = _RRXD5XUpdateRisk
    _RRXD5XApplySymbolReturn    = _RRXD5XApplySymbolReturn
    _RRXD5XApplyCashReturn      = _RRXD5XApplyCashReturn

    # [D5Y] dynamic target policy surface -- diagnostic only
    _RRXD5YTarget               = _RRXD5YTarget
    _RRXD5YApplyReturn          = _RRXD5YApplyReturn
    _RRXD5YApplyCash            = _RRXD5YApplyCash

    # [D5Z] clean-filter diagnostic
    _RRXD5ZTarget               = _RRXD5ZTarget
    _RRXD5ZApplyReturn          = _RRXD5ZApplyReturn
    _RRXD5ZApplyCash            = _RRXD5ZApplyCash

    _RRXVolSize                 = _RRXVolSize
    _RRXStopUpdate              = _RRXStopUpdate
    _RRXShadowUpdate            = _RRXShadowUpdate
    _RRXUpdateSumCounters       = _RRXUpdateSumCounters
    _RRXEmitMonthlySummary      = _RRXEmitMonthlySummary
    RRXEmitFinalSummary         = RRXEmitFinalSummary

    # [D6] leader-first diagnostic methods
    RRXD6LeaderFirstInitialize  = _D6Init
    RRXD6LeaderFirstUpdate      = _D6Update
    _RRXD6StockScore            = _D6StockScore
    _RRXD6FindLeader            = _D6FindLeader
    _RRXD6PxOf                  = _D6PxOf
    _RRXD6Worst5Pct             = _D6Worst5
    RRXD6EmitMonthly            = _D6EmitMonthly
    RRXD6EmitFinal              = _D6EmitFinal

    # [SPYG_SAT] satellite trading methods
    def _SpygSatBlock(self):
        """[SPYG_SAT_T2] Block satellite in 2022-like rate/inflation shock."""
        try:
            if not self._SpygSatBool("spyg_sat_rate_block_enable", 1):
                return False, "off"
            xr = "NA"
            try:
                _xrd = getattr(self, "GetExpandedRegimeDiag", None)
                d = _xrd() if callable(_xrd) else None
                if isinstance(d, dict):
                    xr = str(d.get("raw_xregime") or d.get("xregime") or "NA").upper()
            except Exception:
                xr = "NA"
            spy20 = 0.0
            try:
                ind = getattr(self, "_rrx_spy_roc20", None)
                if ind is not None and ind.IsReady:
                    spy20 = float(ind.Current.Value)
            except Exception:
                pass
            bad_xr = xr in ("PURE_RATE_SHOCK","INFLATION_RATE_SHOCK",
                             "RATE_SHOCK_UNKNOWN","STAGFLATION","EARLY_RISK_OFF")
            if bad_xr and spy20 <= self._SpygSatFloat("spyg_sat_rate_block_spy20_thr", 0.03):
                return True, xr
            return False, xr
        except Exception as e:
            return False, f"ERR_{type(e).__name__}"

    def _SpygSatBool(self, k, d):
        try:
            ov = getattr(self, "_rrx_param_overrides", {}) or {}
            v  = self.get_parameter(k)
            if v is None: v = ov.get(k)
            return bool(int(v)) if v is not None else bool(d)
        except Exception:
            return bool(d)

    def _SpygSatFloat(self, k, d):
        try:
            ov = getattr(self, "_rrx_param_overrides", {}) or {}
            v  = self.get_parameter(k)
            if v is None: v = ov.get(k)
            return float(v) if v is not None else float(d)
        except Exception:
            return float(d)

    def _SpygSatRateClusterQualityCap(self, cap: float, block: bool, why: str):
        try:
            if not self._SpygSatBool("spyg_sat_rate_cluster_quality_trade_enable", 0):
                return float(cap), 0, 0, 0, 0, 0, 0.0, 0.0, 0.0
            if not hasattr(self, "_spyg_sat_rq_compress_hist"):
                self._spyg_sat_rq_compress_hist = []
                self._spyg_sat_rq_rate_hist = []
            cluster_lb = max(1, int(self._SpygSatFloat("spyg_sat_rq_cluster_lookback", 20)))
            cluster_min = max(1, int(self._SpygSatFloat("spyg_sat_rq_cluster_min_count", 5)))
            rate_lb = max(1, int(self._SpygSatFloat("spyg_sat_rq_rate_lookback", 15)))
            mom_lb = max(1, int(self._SpygSatFloat("spyg_sat_rq_sat_mom_lookback", 20)))
            compress_thr = self._SpygSatFloat("spyg_sat_rq_compress_spy_w_thr", 0.25)
            sat_thr = self._SpygSatFloat("spyg_sat_rq_sat_r20_thr", 0.0)
            esc_spy_w = self._SpygSatFloat("spyg_sat_rq_escape_spy_w_thr", 1.50)
            esc_spy20 = self._SpygSatFloat("spyg_sat_rq_escape_spy20_thr", 0.0)
            guard_cap = self._SpygSatFloat("spyg_sat_rq_guard_cap", 0.05)
            today = self.time.date()
            if getattr(self, "_spyg_sat_rq_day", None) != today:
                self._spyg_sat_rq_day = today
                seen = bool(getattr(self, "_spyg_sat_base_spy_seen", False))
                spy_w = float(getattr(self, "_spyg_sat_base_spy_w", 0.0) or 0.0)
                spy_w = max(0.0, min(2.0, spy_w))
                ps = str(getattr(self, "_panic_state", "NORMAL"))
                ids = str(getattr(self, "_ids_state", "NORMAL"))
                hard = ps in ("STRESS", "PANIC") or ids in ("STRESS", "PANIC", "PANIC_SHORT")
                why_u = str(why).upper()
                rate_ctx = bool(block or hard or why_u in (
                    "PURE_RATE_SHOCK", "INFLATION_RATE_SHOCK", "RATE_SHOCK_UNKNOWN",
                    "STAGFLATION", "EARLY_RISK_OFF"
                ))
                self._spyg_sat_rq_compress_hist.append(1 if seen and spy_w <= compress_thr else 0)
                self._spyg_sat_rq_rate_hist.append(1 if rate_ctx else 0)
                self._spyg_sat_rq_compress_hist = self._spyg_sat_rq_compress_hist[-cluster_lb:]
                self._spyg_sat_rq_rate_hist = self._spyg_sat_rq_rate_hist[-rate_lb:]
            spy_w = max(0.0, min(2.0, float(getattr(self, "_spyg_sat_base_spy_w", 0.0) or 0.0)))
            rets = getattr(self, "_d6rm_spyg_rets", []) or []
            nav = 1.0
            if len(rets) >= mom_lb:
                for r in rets[-mom_lb:]:
                    nav *= 1.0 + float(r)
                spyg_r20 = nav - 1.0
            else:
                spyg_r20 = 0.0
            spy20 = 0.0
            try:
                ind = getattr(self, "_rrx_spy_roc20", None)
                if ind is not None and ind.IsReady:
                    spy20 = float(ind.Current.Value)
            except Exception:
                spy20 = 0.0
            cluster = sum(getattr(self, "_spyg_sat_rq_compress_hist", [])[-cluster_lb:]) >= cluster_min
            rate15 = sum(getattr(self, "_spyg_sat_rq_rate_hist", [])[-rate_lb:]) > 0
            sat_bad = spyg_r20 <= sat_thr
            escape = spy_w >= esc_spy_w and spy20 > esc_spy20
            guard = bool(cluster and rate15 and sat_bad and not escape)
            eff_cap = min(float(cap), guard_cap) if guard else float(cap)
            if getattr(self, "_spyg_sat_rq_stat_day", None) != today:
                self._spyg_sat_rq_stat_day = today
                self._spyg_sat_rq_days = getattr(self, "_spyg_sat_rq_days", 0) + 1
                self._spyg_sat_rq_guard = getattr(self, "_spyg_sat_rq_guard", 0) + int(guard)
                self._spyg_sat_rq_escape = getattr(self, "_spyg_sat_rq_escape", 0) + int(escape)
                self._spyg_sat_rq_cluster = getattr(self, "_spyg_sat_rq_cluster", 0) + int(cluster)
                self._spyg_sat_rq_rate15 = getattr(self, "_spyg_sat_rq_rate15", 0) + int(rate15)
                self._spyg_sat_rq_sat_bad = getattr(self, "_spyg_sat_rq_sat_bad", 0) + int(sat_bad)
                self._spyg_sat_rq_cap_sum = getattr(self, "_spyg_sat_rq_cap_sum", 0.0) + float(eff_cap)
                self._spyg_sat_rq_cap5 = getattr(self, "_spyg_sat_rq_cap5", 0) + int(eff_cap <= guard_cap + 1e-6)
                self._spyg_sat_rq_cap20 = getattr(self, "_spyg_sat_rq_cap20", 0) + int(eff_cap >= float(cap) - 1e-9)
            return eff_cap, int(guard), int(cluster), int(rate15), int(sat_bad), int(escape), spy_w, spy20, spyg_r20
        except Exception:
            return float(cap), 0, 0, 0, 0, 0, 0.0, 0.0, 0.0

    def SPYGSatNeed(self, today) -> bool:
        """[SPYG_SAT] True when SPYG satellite position needs rebalance."""
        en  = self._SpygSatBool("spyg_sat_trade_enable", 0)
        cap = self._SpygSatFloat("spyg_sat_cap", 0.05)
        prev = getattr(self, "_spyg_sat_prev_sym", None)
        if not en or cap <= 0:
            return prev is not None
        cap_min = self._SpygSatFloat("spyg_sat_cap_min", 0.0)
        cap_max = self._SpygSatFloat("spyg_sat_cap_max", 0.20)
        cap_lo = min(float(cap_min), float(cap_max))
        cap_hi = max(float(cap_min), float(cap_max))
        cap = max(cap_lo, min(cap_hi, cap))
        sym  = getattr(self, "_d6rm_spyg_held", None)
        sigw = float(getattr(self, "_d6rm_spyg_weight", 0.0) or 0.0)
        block, why = self._SpygSatBlock()
        if block: sym = None; sigw = 0.0
        try:   sv = sym.Value if sym is not None else "NONE"
        except: sv = str(sym) if sym is not None else "NONE"
        if self._SpygSatBool("spyg_sat_rate_cluster_quality_trade_enable", 0):
            eff_cap, rq_guard, _, _, _, _, _, _, _ = self._SpygSatRateClusterQualityCap(cap, block, why)
            key = f"{sv}:{sigw:.3f}:{eff_cap:.3f}:blk={int(block)}:{why}:rq={rq_guard}"
        else:
            key = f"{sv}:{sigw:.3f}:{cap:.3f}:blk={int(block)}:{why}"
        return key != getattr(self, "_spyg_sat_key", None)

    def SPYGSatTrade(self, combined: dict) -> None:
        """[SPYG_SAT] Apply SPYG satellite: 95% base + up to 5% SPYG."""
        try:
            en      = self._SpygSatBool("spyg_sat_trade_enable",  0)
            cap     = self._SpygSatFloat("spyg_sat_cap",          0.05)
            min_tgt = self._SpygSatFloat("spyg_sat_min_target", 0.002)
            log_en  = self._SpygSatBool("spyg_sat_log",            1)
            prev    = getattr(self, "_spyg_sat_prev_sym", None)
            self._spyg_sat_exec_bypass = False
            if not en or cap <= 0:
                if prev is not None:
                    self._spyg_sat_exec_bypass = True
                    combined[prev] = 0.0
                    if log_en:
                        self.log(f"SPYG_SAT,{self.time.date()},off=1,"
                                 f"prev={getattr(prev,'Value',str(prev))},tgt=0.000")
                self._spyg_sat_prev_sym = None
                self._spyg_sat_key = "OFF"
                return
            cap_max = self._SpygSatFloat("spyg_sat_cap_max", 0.20)
            cap_min = self._SpygSatFloat("spyg_sat_cap_min", 0.0)
            cap_lo = min(float(cap_min), float(cap_max))
            cap_hi = max(float(cap_min), float(cap_max))
            cap = max(cap_lo, min(cap_hi, cap))
            sym  = getattr(self, "_d6rm_spyg_held", None)
            sigw = float(getattr(self, "_d6rm_spyg_weight", 0.0) or 0.0)
            sigw = max(0.0, min(1.0, sigw))
            block, block_why = self._SpygSatBlock()
            if block: sym = None; sigw = 0.0
            rq_en = self._SpygSatBool("spyg_sat_rate_cluster_quality_trade_enable", 0)
            if rq_en:
                eff_cap, rq_guard, rq_cluster, rq_rate15, rq_sat_bad, rq_escape, rq_spy_w, rq_spy20, rq_spyg_r20 = \
                    self._SpygSatRateClusterQualityCap(cap, block, block_why)
            else:
                eff_cap, rq_guard = cap, 0
                rq_cluster = rq_rate15 = rq_sat_bad = rq_escape = 0
                rq_spy_w = rq_spy20 = rq_spyg_r20 = 0.0
            sat_tgt = eff_cap * sigw if sym is not None else 0.0
            active_only = self._SpygSatBool("spyg_sat_active_only_scale", 1)
            base_scale = (1.0 - sat_tgt) if active_only else (1.0 - eff_cap)
            base_scale = max(0.0, min(1.0, base_scale))
            sat_syms = set()
            if prev is not None: sat_syms.add(prev)
            if sym  is not None: sat_syms.add(sym)
            for s in list(combined.keys()):
                if s not in sat_syms:
                    combined[s] = float(combined.get(s, 0.0)) * base_scale
            if prev is not None and prev != sym:
                combined[prev] = 0.0
            active_sym = None
            if sym is not None and sat_tgt >= min_tgt:
                combined[sym] = sat_tgt; active_sym = sym
            elif sym is not None:
                combined[sym] = 0.0
            try:   sv = sym.Value  if sym  is not None else "NONE"
            except: sv = str(sym) if sym  is not None else "NONE"
            try:   pv = prev.Value if prev is not None else "NONE"
            except: pv = str(prev) if prev is not None else "NONE"
            key = f"{sv}:{sigw:.3f}:{eff_cap:.3f}:rq={rq_guard}" if rq_en else f"{sv}:{sigw:.3f}:{cap:.3f}"
            changed = key != getattr(self, "_spyg_sat_key", None)
            self._spyg_sat_exec_bypass = bool(changed)
            if rq_en and log_en and changed:
                self.log(
                    f"RRX_SAT_RATE_CLUSTER_QUALITY_T0,{self.time.date()},"
                    f"sym={sv},prev={pv},sigw={sigw:.3f},cap_raw={cap:.3f},"
                    f"cap_eff={eff_cap:.3f},tgt={sat_tgt:.3f},guard={rq_guard},"
                    f"cluster={rq_cluster},rate15={rq_rate15},sat_bad={rq_sat_bad},"
                    f"escape={rq_escape},spy_w={rq_spy_w:.3f},spy20={rq_spy20:+.4f},"
                    f"spyg_r20={rq_spyg_r20:+.4f},block={int(block)},why={block_why}"
                )
            if log_en and changed:
                self.log(f"SPYG_SAT,{self.time.date()},en=1,"
                         f"sym={sv},prev={pv},sigw={sigw:.3f},"
                         f"cap={eff_cap:.3f},tgt={sat_tgt:.3f},"
                         f"base_scale={base_scale:.3f},"
                         f"block={int(block)},why={block_why}")
            self._spyg_sat_prev_sym = active_sym
            self._spyg_sat_key      = key
        except Exception as e:
            self.log(f"SPYG_SAT_ERR,{self.time.date()},{type(e).__name__}:{e}")

    def RRXEmitFinalSummary(self) -> None:
        try:
            RRXEmitFinalSummary(self)
        finally:
            if self._SpygSatBool("spyg_sat_rate_cluster_quality_trade_enable", 0):
                d = int(getattr(self, "_spyg_sat_rq_days", 0) or 0)
                cap_avg = float(getattr(self, "_spyg_sat_rq_cap_sum", 0.0) or 0.0) / max(1, d)
                self.log(
                    f"RRX_SAT_RATE_CLUSTER_QUALITY_T0_FINAL,"
                    f"days={d},"
                    f"guard={int(getattr(self, '_spyg_sat_rq_guard', 0) or 0)},"
                    f"escape={int(getattr(self, '_spyg_sat_rq_escape', 0) or 0)},"
                    f"cluster={int(getattr(self, '_spyg_sat_rq_cluster', 0) or 0)},"
                    f"rate15={int(getattr(self, '_spyg_sat_rq_rate15', 0) or 0)},"
                    f"sat_bad={int(getattr(self, '_spyg_sat_rq_sat_bad', 0) or 0)},"
                    f"cap_avg={cap_avg:.4f},"
                    f"cap5={int(getattr(self, '_spyg_sat_rq_cap5', 0) or 0)},"
                    f"cap20={int(getattr(self, '_spyg_sat_rq_cap20', 0) or 0)}"
                )

    def RRXInitialize(self) -> None:
        # Load param overrides from rrx_params.py (file takes lower priority than QC UI)
        _overrides = {}
        try:
            from rrx_params import RRX_PARAMS
            _overrides = RRX_PARAMS
        except Exception:
            pass
        self._rrx_param_overrides = _overrides  # share with shadow init
        def _gv(k):
            v = self.get_parameter(k)
            if v is None: v = _overrides.get(k)
            return v
        def _gb(k, d):
            v = _gv(k)
            return bool(int(v)) if v is not None else bool(d)
        def _gf(k, d):
            v = _gv(k)
            return float(v) if v is not None else float(d)

        self.rr_xsector_enable  = _gb("rr_xsector_enable",  0)
        self.rr_xsector_log_all = _gb("rr_xsector_log_all", 0)
        self.rr_xsector_detail_log = _gb("rr_xsector_detail_log", 0)  # [RRX] stock-level detail
        self.rr_xsector_heat_log   = _gb("rr_xsector_heat_log",   0)  # [RRX] near-overheat events
        self.rrx_heat_watch_rsi    = _gf("rrx_heat_watch_rsi", 75.0)

        self.rrx_active_symbols: list = []          # [RRX] empty when disabled
        self._rrx_state         = RRX_IDLE
        self._rrx_top_theme     = None
        self._rrx_top_score     = None
        self._rrx_top_theme_cls = None
        self._rrx_top_stock     = None
        self._rrx_stk_score     = None
        self._rrx_prev_state    = None
        self._rrx_prev_theme    = None
        self._rrx_prev_stock    = None
        self._rrx_last_week     = None
        self._rrx_top_stock_diag = {}               # [RRX] last stock diagnostic
        self._rrx_top_stock_fail = ""               # [RRX] gate failure summary
        self._rrx_tradable       = 0                # [RRX] tradable flag (D0 only)
        self._rrx_tblock         = ""               # [RRX] tradable block reasons
        self._rrx_risk_group     = ""               # [RRX] top theme risk group
        # D1 shadow NAV state
        self._rrx_d1_native_nav   = 1.0
        self._rrx_d1_tradable_nav = 1.0
        self._rrx_d1_raw_nav      = 1.0
        self._rrx_d1_native_sym   = None
        self._rrx_d1_native_px    = 0.0
        self._rrx_d1_tradable_sym = None
        self._rrx_d1_tradable_px  = 0.0
        self._rrx_d1_raw_sym      = None
        self._rrx_d1_raw_px       = 0.0
        self._rrx_d1_cash_sym     = None            # [RRX] USFR baseline
        self._rrx_d1_cash_px      = 0.0
        self._rrx_d1_cash_nav     = 1.0
        self._rrx_d1_talloc_nav   = 1.0             # [RRX] tradable + cash accrual
        self._rrx_d1_last         = {}              # [RRX] cached for log
        self._rrx_d1_lag_tradable_nav = 1.0
        self._rrx_d1_lag_raw_nav      = 1.0
        self._rrx_d1_lag_tradable_sym = None        # [RRX] current lag position
        self._rrx_d1_lag_tradable_px  = 0.0
        self._rrx_d1_lag_raw_sym      = None
        self._rrx_d1_lag_raw_px       = 0.0
        self._rrx_d1_next_tradable_sym = None
        self._rrx_d1_next_raw_sym      = None
        self._rrx_d1_exec_nav = 1.0                 # [RRX] execution-style NAV
        self._rrx_d1_exec_sym = None
        self._rrx_d1_exec_px  = 0.0
        self._rrx_d1_exec_alloc_nav = 1.0           # [RRX] execution + cash when flat
        self._rrx_meta_theme_chg  = 0              # [RRX] theme changes in period
        self._rrx_meta_leader_chg = 0              # [RRX] leader changes in period
        self._rrx_meta_prev_theme  = None
        self._rrx_meta_prev_leader = None
        self._rrx_last_spy20 = 0.0                 # [RRX] stored for meta summary
        self._rrx_last_qqq20 = 0.0
        # D1 summary counters - reset monthly
        self._rrx_d1_sum_start    = None
        self._rrx_d1_tradable_days = 0
        self._rrx_d1_raw_days      = 0
        self._rrx_d1_blk_risk_off  = 0
        self._rrx_d1_blk_ids       = 0
        self._rrx_d1_blk_panic     = 0
        self._rrx_d1_blk_stretch   = 0
        self._rrx_d1_blk_defensive = 0
        self._rrx_last_month_key   = None           # [RRX] "YYYY-MM"

        if not self.rr_xsector_enable:
            return

        self.rrx_min_abs_r20      = _gf("rrx_min_abs_r20",      0.00)
        self.rrx_min_rel_spy20    = _gf("rrx_min_rel_spy20",    0.00)
        self.rrx_strong_rel_spy20 = _gf("rrx_strong_rel_spy20", 0.03)
        self.rrx_strong_rel_qqq20 = _gf("rrx_strong_rel_qqq20", 0.01)
        self.rrx_overheat_rsi     = _gf("rrx_overheat_rsi",    85.0)
        self.rrx_entry_rsi_max    = _gf("rrx_entry_rsi_max",   75.0)  # hard gate in stock scoring
        self.rrx_stock_min_score  = _gf("rrx_stock_min_score",  0.0)  # raised default (hard gates active)
        self.rrx_min_theme_score  = _gf("rrx_min_theme_score", -0.60)
        self.rrx_tradable_block_risk_off  = _gb("rrx_tradable_block_risk_off",  1)
        self.rrx_tradable_block_ids_watch = _gb("rrx_tradable_block_ids_watch", 1)
        self.rrx_heat_st50  = _gf("rrx_heat_st50",  0.18)  # [RRX] stretch heat threshold
        self.rrx_block_st50 = _gf("rrx_block_st50", 0.25)  # [RRX] stretch tradable block
        self.rrx_d1_enable  = _gb("rrx_d1_enable",        0)  # [RRX] shadow NAV tracking
        self.rrx_d1_summary_enable = _gb("rrx_d1_summary_enable", 0)  # [RRX] monthly summary
        self.rrx_d1_audit_log      = _gb("rrx_d1_audit_log",      0)  # [RRX] daily timing audit
        self.rrx_meta_enable = _gb("rrx_meta_enable", 0)
        self.rrx_meta_turnover_enable = _gb("rrx_meta_turnover_enable", 0)
        self.rrx_meta_turn_lb = int(_gf("rrx_meta_turn_lb", 20))
        self.rrx_meta_eth = int(_gf("rrx_meta_entry_th_chg_max", 2))
        self.rrx_meta_eld = int(_gf("rrx_meta_entry_ldr_chg_max", 4))
        self.rrx_meta_cth = int(_gf("rrx_meta_carry_th_chg_max", 3))
        self.rrx_meta_cld = int(_gf("rrx_meta_carry_ldr_chg_max", 6))
        self.rrx_meta_hth = int(_gf("rrx_meta_hard_th_chg", 6))
        self.rrx_meta_hld = int(_gf("rrx_meta_hard_ldr_chg", 9))
        self.rrx_meta_stress_enable = _gb("rrx_meta_stress_enable", 0)
        self.rrx_meta_debug_log     = _gb("rrx_meta_debug_log",     0)
        self.rrx_rotq_enable        = _gb("rrx_rotq_enable",        0)
        self.rrx_rotq_max_rsi       = float(_gf("rrx_rotq_max_rsi",   82.0))
        self.rrx_rotq_spy_edge      = float(_gf("rrx_rotq_spy_edge",   0.01))

        self._rrx_etf_sym:    dict = {}
        self._rrx_stk_sym:    dict = {}
        self._rrx_etf_roc20:  dict = {}
        self._rrx_etf_roc60:  dict = {}
        self._rrx_etf_sma50:  dict = {}
        self._rrx_etf_sma200: dict = {}
        self._rrx_etf_sma20:  dict = {}
        self._rrx_etf_rsi14:  dict = {}
        self._rrx_stk_roc20:  dict = {}
        self._rrx_stk_roc60:  dict = {}
        self._rrx_stk_sma10:  dict = {}
        self._rrx_stk_sma20:  dict = {}
        self._rrx_stk_sma50:  dict = {}
        self._rrx_stk_atr20:  dict = {}
        self._rrx_stk_rsi14:  dict = {}

        all_syms: list = []

        for theme, cfg in RRX_THEMES.items():
            etf_ticker = cfg["etf"]
            try:
                etf_sym = self.add_equity(etf_ticker, Resolution.DAILY).Symbol
            except Exception:
                continue
            self._rrx_etf_sym[theme] = etf_sym
            if etf_sym not in self._rrx_etf_roc20:
                self._rrx_etf_roc20[etf_sym]  = self.roc(etf_sym, 20,  Resolution.DAILY)
                self._rrx_etf_roc60[etf_sym]  = self.roc(etf_sym, 60,  Resolution.DAILY)
                self._rrx_etf_sma50[etf_sym]  = self.sma(etf_sym, 50,  Resolution.DAILY)
                self._rrx_etf_sma200[etf_sym] = self.sma(etf_sym, 200, Resolution.DAILY)
                self._rrx_etf_sma20[etf_sym]  = self.sma(etf_sym, 20,  Resolution.DAILY)
                self._rrx_etf_rsi14[etf_sym]  = self.rsi(
                    etf_sym, 14, MovingAverageType.WILDERS, Resolution.DAILY)
            all_syms.append(etf_sym)

            stk_syms: list = []
            for stk_ticker in cfg.get("stocks", []):
                try:
                    stk_sym = self.add_equity(stk_ticker, Resolution.DAILY).Symbol
                except Exception:
                    continue
                stk_syms.append(stk_sym)
                if stk_sym not in self._rrx_stk_roc20:
                    self._rrx_stk_roc20[stk_sym] = self.roc(stk_sym, 20, Resolution.DAILY)
                    self._rrx_stk_roc60[stk_sym] = self.roc(stk_sym, 60, Resolution.DAILY)
                    self._rrx_stk_sma10[stk_sym] = self.sma(stk_sym, 10, Resolution.DAILY)
                    self._rrx_stk_sma20[stk_sym] = self.sma(stk_sym, 20, Resolution.DAILY)
                    self._rrx_stk_sma50[stk_sym] = self.sma(stk_sym, 50, Resolution.DAILY)
                    self._rrx_stk_atr20[stk_sym] = self.atr(stk_sym, 20, MovingAverageType.WILDERS, Resolution.DAILY)
                    self._rrx_stk_rsi14[stk_sym] = self.rsi(
                        stk_sym, 14, MovingAverageType.WILDERS, Resolution.DAILY)
                all_syms.append(stk_sym)
            self._rrx_stk_sym[theme] = stk_syms

        spy = getattr(self, "sym_spy", None) or self.add_equity("SPY", Resolution.DAILY).Symbol
        qqq = self.add_equity("QQQ", Resolution.DAILY).Symbol
        self._rrx_spy_roc20 = self.roc(spy, 20, Resolution.DAILY)
        self._rrx_spy_roc60 = self.roc(spy, 60, Resolution.DAILY)
        self._rrx_qqq_roc20 = self.roc(qqq, 20, Resolution.DAILY)

        self.rrx_active_symbols = list({s for s in all_syms})
        _RRXShadowInit(self)
        # [D6] leader-first diagnostic
        self.rrx_d6_leader_first_enable = _gb("rrx_d6_leader_first_enable", 0)
        if self.rrx_d6_leader_first_enable:
            self.RRXD6LeaderFirstInitialize()
        self.log(
            f"RRX_INIT,en=1,themes={len(RRX_THEMES)},"
            f"symbols={len(self.rrx_active_symbols)},"
            f"detail={int(self.rr_xsector_detail_log)},"
            f"log_all={int(self.rr_xsector_log_all)}"
        )

    def _RRXEtfReady(self, sym) -> bool:
        try:
            return (self._rrx_etf_roc20[sym].IsReady
                    and self._rrx_etf_roc60[sym].IsReady
                    and self._rrx_etf_sma50[sym].IsReady
                    and self._rrx_etf_rsi14[sym].IsReady)
        except Exception:
            return False

    def _RRXStkReady(self, sym) -> bool:
        try:
            return (self._rrx_stk_roc20[sym].IsReady
                    and self._rrx_stk_roc60[sym].IsReady
                    and self._rrx_stk_sma50[sym].IsReady
                    and self._rrx_stk_rsi14[sym].IsReady)
        except Exception:
            return False

    def _RRXRefReady(self) -> bool:
        try:
            return self._rrx_spy_roc20.IsReady and self._rrx_qqq_roc20.IsReady
        except Exception:
            return False

    def _RRXThemeScore(self, theme: str) -> dict:
        etf = self._rrx_etf_sym.get(theme)
        if etf is None or not self._RRXEtfReady(etf):
            return None

        r20  = float(self._rrx_etf_roc20[etf].Current.Value)
        r60  = float(self._rrx_etf_roc60[etf].Current.Value)
        s50  = float(self._rrx_etf_sma50[etf].Current.Value)
        rsi  = float(self._rrx_etf_rsi14[etf].Current.Value)
        s200 = (float(self._rrx_etf_sma200[etf].Current.Value)
                if self._rrx_etf_sma200[etf].IsReady else s50)
        try:
            px = float(self.securities[etf].price)
        except Exception:
            px = 0.0

        spy20 = float(self._rrx_spy_roc20.Current.Value) if self._rrx_spy_roc20.IsReady else 0.0
        qqq20 = float(self._rrx_qqq_roc20.Current.Value) if self._rrx_qqq_roc20.IsReady else 0.0

        above_s50 = int(px > s50 > 0)
        golden    = int(above_s50 and s50 > s200 > 0)
        trend_sc  = 0.5 * above_s50 + 0.5 * golden

        edge_spy  = r20 - spy20
        edge_qqq  = r20 - qqq20
        norm_esp  = max(-1.0, min(1.0, edge_spy / 0.20))
        norm_eqq  = max(-1.0, min(1.0, edge_qqq / 0.20))
        norm_m20  = max(-1.0, min(1.0, r20 / 0.15))
        norm_m60  = max(-1.0, min(1.0, r60 / 0.25))
        overheat  = (max(0.0, (rsi - self.rrx_overheat_rsi) / 15.0)
                     if rsi > self.rrx_overheat_rsi else 0.0)

        score = (0.25 * trend_sc + 0.25 * norm_esp + 0.15 * norm_eqq
                 + 0.20 * norm_m20 + 0.15 * norm_m60 - 0.20 * overheat)

        min_r20 = self.rrx_min_abs_r20
        if r20 < -0.08 or not above_s50:
            cls = _TC_DAMAGED
        elif (r20 >= min_r20 and r60 >= 0
              and edge_spy >= self.rrx_strong_rel_spy20
              and edge_qqq >= self.rrx_strong_rel_qqq20
              and above_s50 and rsi < self.rrx_overheat_rsi):
            cls = _TC_ROCKET
        elif r20 >= min_r20 and edge_spy >= self.rrx_min_rel_spy20:
            cls = _TC_POSITIVE
        elif r20 < min_r20 and edge_spy >= self.rrx_min_rel_spy20:
            cls = _TC_DEFENSIVE
        else:
            cls = _TC_WEAK

        return {
            "theme": theme, "score": score, "r20": r20, "r60": r60,
            "rsi": rsi, "cls": cls, "edge_spy": edge_spy, "edge_qqq": edge_qqq,
            "above_s50": above_s50,
        }

    def _RRXStockDiag(self, sym, etf_r20: float, spy20: float, qqq20: float) -> dict:
        if sym is None:
            return {"score": -999.0, "gate": "none"}
        if not self._RRXStkReady(sym):
            return {"score": -999.0, "gate": "not_ready"}
        try:
            r20 = float(self._rrx_stk_roc20[sym].Current.Value)
            r60 = float(self._rrx_stk_roc60[sym].Current.Value)
            s50 = float(self._rrx_stk_sma50[sym].Current.Value)
            rsi = float(self._rrx_stk_rsi14[sym].Current.Value)
            px  = float(self.securities[sym].price) if sym in self.securities else 0.0
            above      = int(px > s50 > 0)
            edge_spy   = r20 - spy20
            edge_theme = r20 - etf_r20
            edge_qqq   = r20 - qqq20
            st50       = (px / s50 - 1.0) if s50 > 0 else 0.0
            gate = "OK"
            if rsi > self.rrx_entry_rsi_max:   gate = "rsi_entry"
            elif not above:                     gate = "below_sma50"
            elif r20 < 0:                       gate = "r20_neg"
            elif r20 < etf_r20:                 gate = "under_theme"
            elif r20 < spy20:                   gate = "under_spy"
            norm_esp = max(-1.0, min(1.0, edge_spy   / 0.20))
            norm_eth = max(-1.0, min(1.0, edge_theme / 0.15))
            norm_eqq = max(-1.0, min(1.0, edge_qqq   / 0.20))
            norm_m20 = max(-1.0, min(1.0, r20 / 0.15))
            norm_m60 = max(-1.0, min(1.0, r60 / 0.25))
            overheat = (max(0.0, (rsi - self.rrx_overheat_rsi) / 15.0)
                        if rsi > self.rrx_overheat_rsi else 0.0)
            score = (0.20 * 1.0 + 0.20 * norm_esp + 0.20 * norm_eth
                     + 0.10 * norm_eqq + 0.15 * norm_m20 + 0.15 * norm_m60
                     - 0.20 * overheat)
            if gate != "OK":
                score = -999.0
            return {"score": score, "gate": gate, "r20": r20, "r60": r60,
                    "rsi": rsi, "edge_spy": edge_spy, "edge_theme": edge_theme,
                    "edge_qqq": edge_qqq, "st50": st50, "above": above}
        except Exception:
            return {"score": -999.0, "gate": "err"}

    def _RRXStockScore(self, sym, etf_r20: float, spy20: float, qqq20: float) -> float:
        return float(self._RRXStockDiag(sym, etf_r20, spy20, qqq20).get("score", -999.0))

    def _RRXSelectStockLeader(self, theme: str, etf_r20: float,
                               spy20: float, qqq20: float):
        best_sym, best_sc, best_diag = None, -999.0, {}
        fail_counts: dict = {}
        for sym in self._rrx_stk_sym.get(theme, []):
            d    = self._RRXStockDiag(sym, etf_r20, spy20, qqq20)
            sc   = float(d.get("score", -999.0))
            gate = str(d.get("gate", "err"))
            if gate != "OK":
                fail_counts[gate] = fail_counts.get(gate, 0) + 1
            if sc > best_sc:
                best_sc, best_sym, best_diag = sc, sym, d
        self._rrx_top_stock_diag = best_diag if isinstance(best_diag, dict) else {}
        self._rrx_top_stock_fail = "|".join(
            f"{k}:{v}" for k, v in sorted(fail_counts.items()))
        if best_sc <= -900:
            return None, -999.0
        return best_sym, best_sc

    def _RRXAltStockLeader(self, theme: str, etf_r20: float, spy20: float, qqq20: float):
        best, bsc = None, -999.0
        for s in self._rrx_stk_sym.get(theme, []):
            d = self._RRXStockDiag(s, etf_r20, spy20, qqq20)
            sc = float(d.get("score", -999.0))
            if sc > bsc: best, bsc = s, sc
        return best if bsc > -900 else None

    def _RRXContextDiag(self) -> dict:
        ids   = str(getattr(self, "_ids_state",   "NORMAL"))
        ps    = str(getattr(self, "_panic_state", "NORMAL"))
        shock = bool(getattr(self, "short_shock_flag", False))
        emerg = bool(getattr(self, "emergency_stop_triggered", False))
        dd    = float(self.CurrentDrawdown()) if hasattr(self, "CurrentDrawdown") else 0.0
        reg   = str(getattr(self, "current_regime", "NA"))
        blocks = []
        if ps  in ("STRESS", "PANIC"):                    blocks.append("panic")
        if ids in ("STRESS", "PANIC", "PANIC_SHORT"):     blocks.append("ids")
        if shock:                                          blocks.append("short_shock")
        if emerg:                                          blocks.append("emergency")
        if dd >= float(getattr(self, "dd_soft_start", 0.11)): blocks.append("dd")
        return {"ok": len(blocks) == 0,
                "block": "|".join(blocks) if blocks else "none",
                "ps": ps, "ids": ids, "dd": dd, "regime": reg}

    def _RRXContextOk(self) -> bool:
        return bool(self._RRXContextDiag().get("ok", False))

    def _RRXTradableDiag(self, top: dict, sd: dict, cd: dict) -> dict:
        tblocks: list = []
        # Hard gate: raw strong requires state=STRONG + valid stock leader
        raw_ok = (
            self._rrx_state == RRX_STRONG
            and self._rrx_top_stock is not None
            and self._rrx_stk_score is not None
            and float(self._rrx_stk_score) > -900
        )
        if not raw_ok:
            if self._rrx_state != RRX_STRONG:  tblocks.append("not_strong")
            if self._rrx_top_stock is None:     tblocks.append("no_leader")

        base = cd.get("block", "none")
        if base != "none":
            tblocks.extend(b for b in base.split("|") if b)
        # Additional tradable-specific blocks
        reg = str(cd.get("regime", "NA"))
        ids = str(cd.get("ids", "NORMAL"))
        if bool(self.rrx_tradable_block_risk_off) and reg == "RISK_OFF":
            tblocks.append("risk_off")
        if bool(self.rrx_tradable_block_ids_watch) and ids == "WATCH":
            tblocks.append("ids_watch")
        # Risk group: defensive themes not tradable as rocket booster
        theme = top.get("theme", "") if top else ""
        rg = str(RRX_THEMES.get(theme, {}).get("risk_group", "UNKNOWN"))
        if rg in ("DEFENSIVE", "DEFENSIVE_HEALTH"):
            tblocks.append("defensive")
        # Stretch: stock over-extended above SMA50
        sst50 = float((sd or {}).get("st50", 0.0) or 0.0)
        if sst50 >= float(self.rrx_block_st50):
            tblocks.append("stretch")
        return {
            "tradable":   int(raw_ok and len(tblocks) == 0),
            "tblock":     "|".join(tblocks) if tblocks else "none",
            "risk_group": rg,
        }

    def _RRXClassifyState(self, top: dict, stk_score: float) -> str:
        if top is None:
            return RRX_IDLE

        cls = top.get("cls", _TC_WEAK)
        rsi = top.get("rsi", 50.0)
        r20 = top.get("r20", 0.0)

        if rsi >= self.rrx_overheat_rsi:
            return RRX_OVERHEATED
        if cls == _TC_DAMAGED or r20 < -0.10:
            return RRX_DAMAGED
        if cls == _TC_DEFENSIVE:
            return RRX_DEFENSIVE_ONLY
        if cls == _TC_WEAK:
            return RRX_IDLE

        stock_ok = stk_score > self.rrx_stock_min_score   # -999 < 0 always fails

        if cls == _TC_POSITIVE:
            return RRX_ACTIVE if stock_ok else RRX_IDLE
        if cls == _TC_ROCKET:
            if not stock_ok:
                return RRX_ACTIVE
            return RRX_STRONG if self._RRXContextOk() else RRX_ACTIVE

        return RRX_IDLE


    def RRXDailyCycle(self) -> None:
        if not getattr(self, "rr_xsector_enable", False):
            return
        if not self._RRXRefReady():
            return

        spy20 = float(self._rrx_spy_roc20.Current.Value)
        qqq20 = float(self._rrx_qqq_roc20.Current.Value)

        scored = []
        for theme in RRX_THEMES:
            td = self._RRXThemeScore(theme)
            if td is not None and td["score"] >= self.rrx_min_theme_score:
                scored.append(td)

        if not scored:
            self._rrx_state       = RRX_IDLE
            self._rrx_top_theme   = None
            self._rrx_top_score   = None
            self._rrx_top_theme_cls = None
            self._rrx_top_stock   = None
            self._rrx_stk_score   = None
            self._RRXMaybeLog(spy20, qqq20, None, None, None, [])
            self._rrx_prev_state  = RRX_IDLE
            self._rrx_prev_theme  = None
            self._rrx_prev_stock  = None
            return

        scored.sort(key=lambda x: x["score"], reverse=True)
        top     = scored[0]
        # D1C: capture second-theme leader for inter-theme re-entry
        self._rrx_sub_stock = None; self._rrx_sub_theme = None
        if len(scored) > 1:
            sub = scored[1]
            self._rrx_sub_theme = sub["theme"]
            self._rrx_sub_stock = self._RRXAltStockLeader(
                sub["theme"], sub["r20"], spy20, qqq20)
        top_r20 = top["r20"]

        best_stk, best_sc = self._RRXSelectStockLeader(
            top["theme"], top_r20, spy20, qqq20)

        state = self._RRXClassifyState(top, best_sc)

        self._rrx_state         = state
        self._rrx_top_theme     = top["theme"]
        self._rrx_top_score     = top["score"]
        self._rrx_top_theme_cls = top["cls"]
        self._rrx_top_stock     = best_stk
        self._rrx_stk_score     = best_sc
        # [RRX] Tradable diag - D0 only, no trading impact
        _cd  = self._RRXContextDiag()
        _sd  = getattr(self, "_rrx_top_stock_diag", {}) or {}
        _td  = self._RRXTradableDiag(top, _sd, _cd)
        self._rrx_tradable   = _td["tradable"]
        self._rrx_tblock     = _td["tblock"]
        self._rrx_risk_group = _td["risk_group"]
        # [RRX] D1 shadow NAV - D0 only, no trading impact
        if getattr(self, "rrx_d1_enable", False):
            self._rrx_d1_last = self._RRXShadowUpdate()
        else:
            self._rrx_d1_last = {}
        if getattr(self, "rrx_d1_summary_enable", False):
            self._RRXUpdateSumCounters()
        self._rrx_last_spy20 = spy20
        self._rrx_last_qqq20 = qqq20

        self._RRXMaybeLog(spy20, qqq20, top, best_stk, best_sc, scored)

        self._rrx_prev_state = state
        self._rrx_prev_theme = top["theme"]
        self._rrx_prev_stock = best_stk

        # [D6] leader-first diagnostic (diag only, no trading)
        if getattr(self, "rrx_d6_leader_first_enable", False):
            self.RRXD6LeaderFirstUpdate()


    def _RRXMaybeLog(self, spy20: float, qqq20: float,
                     top, stk, stk_sc, scored: list) -> None:
        if not self._LogAllowedAt():
            return

        today         = self.time.date()
        state_changed = (self._rrx_state    != self._rrx_prev_state)
        theme_changed = (self._rrx_top_theme != self._rrx_prev_theme)
        stock_changed = (self._rrx_top_stock != self._rrx_prev_stock)
        changed       = state_changed or theme_changed or stock_changed
        log_all       = getattr(self, "rr_xsector_log_all", False)
        iso_week      = today.isocalendar()[1]
        week_new      = (iso_week != self._rrx_last_week)

        # Monthly summary trigger
        month_key = today.strftime("%Y-%m")
        if (getattr(self, "rrx_d1_summary_enable", False)
                and self._rrx_last_month_key is not None
                and month_key != self._rrx_last_month_key):
            self._RRXEmitMonthlySummary(today, top)
        self._rrx_last_month_key = month_key

        if getattr(self, "rrx_d1_audit_log", False):
            d1 = getattr(self, "_rrx_d1_last", {}) or {}
            try:
                ref_end = str(self._rrx_spy_roc20.Current.EndTime)
            except Exception:
                ref_end = "NA"
            try:
                ldr_v = self._rrx_top_stock.Value if self._rrx_top_stock else "NONE"
            except Exception:
                ldr_v = str(self._rrx_top_stock) if self._rrx_top_stock else "NONE"
            self.log(
                f"RRX_D1_AUDIT,{self.time},re={ref_end},st={self._rrx_state},"
                f"th={getattr(self,'_rrx_top_theme','NONE')},ldr={ldr_v},"
                f"tr={getattr(self,'_rrx_tradable',0)},tb={getattr(self,'_rrx_tblock','none')},"
                f"tsym={d1.get('tradable_symbol','NONE')},lsym={d1.get('lag_tradable_symbol','NONE')},"
                f"tret={d1.get('tradable_ret',0.0):+.4f},lret={d1.get('lag_tradable_ret',0.0):+.4f},"
                f"tanav={d1.get('talloc_nav',1.0):.4f},ltanav={d1.get('lag_tradable_nav',1.0):.4f},"
                f"exsym={d1.get('exec_symbol','NONE')},exret={d1.get('exec_ret',0.0):+.4f},"
                f"exanav={d1.get('exec_alloc_nav',1.0):.4f}"
            )

        if not (changed or log_all or week_new):
            return

        if getattr(self,"rrx_lead_enable",False) and stk is not None:
            try:
                e=self._rrx_etf_sym.get(self._rrx_top_theme or"")
                _g=lambda i:float((getattr(getattr(i,"Current",None),"Value",0)or 0))if i else 0.0
                r20=_g(self._rrx_stk_roc20.get(stk));rsi=_g(self._rrx_etf_rsi14.get(e))or 50.0
                try:sv=str(stk.Value)
                except Exception:sv=str(stk)
                self.log(f"RRX_LEAD,{today},sym={sv},th={self._rrx_top_theme or''},r20={r20:+.3f},rel={r20-_g(self._rrx_etf_roc20.get(e)):+.3f},rsi={rsi:.1f},spy={_g(self._rrx_spy_roc20):+.3f},fail={int(not self._RRXLeadPass(stk))}")
            except Exception:pass

        if top is not None:
            try:
                ldr = stk.Value if stk is not None else "NONE"
            except Exception:
                ldr = str(stk) if stk is not None else "NONE"
            lsc = f"{stk_sc:.3f}" if (stk_sc is not None and stk_sc > -900) else "n/a"
            msg = (
                f"RRX_D0,{today},{self._rrx_state},"
                f"{top['theme']},{top['cls']},"
                f"sc={top['score']:.3f},"
                f"r20={top['r20']:.3f},r60={top['r60']:.3f},"
                f"esp={top['edge_spy']:.3f},eqq={top['edge_qqq']:.3f},"
                f"rsi={top['rsi']:.1f},ldr={ldr},lsc={lsc}"
            )
            if getattr(self, "rr_xsector_detail_log", False):
                sd = getattr(self, "_rrx_top_stock_diag", {}) or {}
                cd = self._RRXContextDiag()
                msg += (
                    f",sr20={float(sd.get('r20', 0.0)):.3f}"
                    f",sr60={float(sd.get('r60', 0.0)):.3f}"
                    f",setf={float(sd.get('edge_theme', 0.0)):.3f}"
                    f",sspy={float(sd.get('edge_spy', 0.0)):.3f}"
                    f",sqqq={float(sd.get('edge_qqq', 0.0)):.3f}"
                    f",srsi={float(sd.get('rsi', 0.0)):.1f}"
                    f",sst50={float(sd.get('st50', 0.0)):.3f}"
                    f",gate={str(sd.get('gate', 'none'))}"
                    f",fails={getattr(self, '_rrx_top_stock_fail', '')}"
                    f",reg={cd.get('regime')}"
                    f",ps={cd.get('ps')}"
                    f",ids={cd.get('ids')}"
                    f",dd={float(cd.get('dd', 0.0)):.3f}"
                    f",ctx={int(bool(cd.get('ok')))}"
                    f",blk={cd.get('block')}"
                    f",raw={int(self._rrx_state == RRX_STRONG)}"
                    f",tradable={getattr(self, '_rrx_tradable', 0)}"
                    f",tblock={getattr(self, '_rrx_tblock', 'none')}"
                    f",rg={getattr(self, '_rrx_risk_group', '')}"
                )
            self.log(msg)
            if getattr(self, "rr_xsector_heat_log", False):
                sd   = getattr(self, "_rrx_top_stock_diag", {}) or {}
                srsi = float(sd.get("rsi", 0.0) or 0.0)
                trsi = float(top.get("rsi", 0.0) or 0.0)
                sst50 = float(sd.get("st50", 0.0) or 0.0)
                gate = str(sd.get("gate", "none"))
                hw   = float(getattr(self, "rrx_heat_watch_rsi", 75.0))
                hs50 = float(getattr(self, "rrx_heat_st50", 0.18))
                if trsi >= hw or srsi >= hw or gate == "rsi_entry" or sst50 >= hs50:
                    self.log(
                        f"RRX_HEAT,{today},state={self._rrx_state},"
                        f"theme={top['theme']},trsi={trsi:.1f},"
                        f"ldr={ldr},srsi={srsi:.1f},sst50={sst50:.3f},"
                        f"gate={gate},lsc={lsc}"
                    )
        else:
            self.log(f"RRX_D0,{today},{self._rrx_state},NONE")

        if week_new:
            self._rrx_last_week = iso_week
            top3 = scored[:3]
            summary = ",".join(
                f"{td['theme']}:{td['cls']}:{td['score']:.3f}" for td in top3)
            self.log(
                f"RRX_WEEK,{today},"
                f"spy20={spy20:.3f},qqq20={qqq20:.3f},"
                f"top3=[{summary}],state={self._rrx_state}"
            )

        if getattr(self, "rrx_d1_enable", False):
            d1 = getattr(self, "_rrx_d1_last", {})
            if d1:
                self.log(
                    f"RRX_D1,{today},rrx={self._rrx_state},"
                    f"th={top['theme'] if top is not None else 'NONE'},"
                    f"ldr={ldr},tr={getattr(self,'_rrx_tradable',0)},"
                    f"tb={getattr(self,'_rrx_tblock','none')},"
                    f"tanav={d1.get('talloc_nav',1.0):.4f},cnav={d1.get('cash_nav',1.0):.4f},"
                    f"dta={d1.get('delta_talloc',0.0):+.4f},dr={d1.get('delta_raw',0.0):+.4f},"
                    f"ltanav={d1.get('lag_tradable_nav',1.0):.4f},"
                    f"ldta={d1.get('delta_ltalloc',0.0):+.4f},ldr={d1.get('delta_lraw',0.0):+.4f}"
                )
