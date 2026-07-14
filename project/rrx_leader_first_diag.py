# rrx_leader_first_diag.py
# Tags: [RRX][D6][D6R][D6R-MAP]
# D6     = leader-first raw shadow NAV (vs theme-first TF)
# D6R    = leader-first + panic/IDS/DD guard
# D6R-MAP= leader-first + bucket-regime confirmation (HARD and SOFT curves)
# Diagnostic only. Zero trading impact.

from AlgorithmImports import *
from datetime import timedelta

try:
    from rrx_d8_switch_diag import (
        D8SwitchDiagInitialize as _D8Init,
        D8SwitchDiagUpdate     as _D8Update,
        D8SwitchDiagEmitFinal  as _D8EmitFinal,
    )
    _D8_AVAIL = True
except ImportError:
    _D8_AVAIL = False

# ---------------------------------------------------------------------------
# Bucket mapping (derived from risk_group in RRX_THEMES)
# ---------------------------------------------------------------------------
#   SPY_GROWTH  : GROWTH_CYCLICAL, CYCLICAL  -- need RISK_ON/NEUTRAL
#   DEFENSIVE   : DEFENSIVE, DEFENSIVE_HEALTH -- can survive RISK_OFF
#   SAFE_HAVEN  : SAFE_HAVEN                 -- NEUTRAL/RISK_OFF natural habitat
#   COMMODITY   : INFLATION_CYCLICAL         -- need commodity context
#   HIGH_VOL    : THEMATIC, HEALTH_VOLATILE, GEOPOLITICAL -- strict gates

_BUCKET_BY_GROUP = {
    "GROWTH_CYCLICAL":  "SPY_GROWTH",
    "CYCLICAL":         "SPY_GROWTH",
    "DEFENSIVE":        "DEFENSIVE",
    "DEFENSIVE_HEALTH": "DEFENSIVE",
    "SAFE_HAVEN":       "SAFE_HAVEN",
    "INFLATION_CYCLICAL": "COMMODITY",
    "THEMATIC":         "HIGH_VOL",
    "HEALTH_VOLATILE":  "HIGH_VOL",
    "GEOPOLITICAL":     "HIGH_VOL",
}


def _RRXD6ThemeBucket(theme: str) -> str:
    """[D6R-MAP] Return risk bucket for a theme name."""
    from rr_xsector_diag import RRX_THEMES
    rg = RRX_THEMES.get(theme, {}).get("risk_group", "")
    return _BUCKET_BY_GROUP.get(rg, "HIGH_VOL")


def _RRXD6BucketDecision(self, bucket: str, spy20: float) -> str:
    """[D6R-MAP] ALLOW / SOFT / BLOCK based on bucket + regime + panic."""
    reg = str(getattr(self, "current_regime", "RISK_ON"))
    ps  = str(getattr(self, "_panic_state",   "NORMAL"))
    ids = str(getattr(self, "_ids_state",      "NORMAL"))
    # Hard panic/IDS always blocks
    if ps in ("PANIC",) or ids in ("PANIC", "PANIC_SHORT"):
        return "BLOCK"
    is_panic_env = ps in ("STRESS", "PANIC") or ids in ("STRESS", "PANIC", "PANIC_SHORT")
    is_risk_on   = reg == "RISK_ON"
    is_neutral   = reg == "NEUTRAL"
    is_risk_off  = reg in ("RISK_OFF", "RISKOFF_REC", "POST_PANIC")
    is_crisis    = reg == "PANIC"

    if bucket == "SPY_GROWTH":
        if is_panic_env or is_risk_off or is_crisis: return "BLOCK"
        if is_risk_on:                               return "ALLOW"
        if is_neutral and spy20 > 0:                 return "SOFT"
        return "BLOCK"

    if bucket == "DEFENSIVE":
        if is_crisis:                                return "BLOCK"
        if ps == "PANIC" or ids in ("PANIC", "PANIC_SHORT"): return "BLOCK"
        if is_risk_on or is_neutral:                 return "ALLOW"
        return "SOFT"   # ETF health already confirmed by D6 score gate

    if bucket == "SAFE_HAVEN":
        if is_crisis:                                return "SOFT"
        if is_risk_on:                               return "SOFT"
        return "ALLOW"  # NEUTRAL and RISK_OFF are natural habitat for safe-haven

    if bucket == "COMMODITY":
        if is_panic_env or is_crisis:                return "BLOCK"
        if is_risk_on:                               return "ALLOW"
        if is_neutral or is_risk_off:                return "SOFT"
        return "BLOCK"

    if bucket == "HIGH_VOL":
        if is_panic_env or is_risk_off or is_crisis: return "BLOCK"
        if is_risk_on:                               return "SOFT"
        if is_neutral:                               return "SOFT"
        return "BLOCK"

    return "BLOCK"


# ---------------------------------------------------------------------------
# Initialize
# ---------------------------------------------------------------------------

def RRXD6LeaderFirstInitialize(self) -> None:
    """[D6] Initialize D6 + D6R + D6R-MAP shadow diagnostics."""
    def _gb(k, d):
        ov = getattr(self, "_rrx_param_overrides", {}) or {}
        v  = self.get_parameter(k)
        if v is None:
            v = ov.get(k)
        return bool(int(v)) if v is not None else bool(d)
    self.rrx_d6_log_monthly      = _gb("rrx_d6_log_monthly",      0)
    self.rrx_d6_log_daily        = _gb("rrx_d6_log_daily",        0)
    if getattr(self, "cg_fast_baseline_mode", False):  # [E0.5.1] logs only
        _fd = getattr(self, "_cg_fast_disabled", None)
        if self.rrx_d6_log_monthly:
            self.rrx_d6_log_monthly = False
            if _fd is not None: _fd.append("rrx_d6_log_monthly")
        if self.rrx_d6_log_daily:
            self.rrx_d6_log_daily = False
            if _fd is not None: _fd.append("rrx_d6_log_daily")
    self.rrx_d6_risk_enable      = _gb("rrx_d6_risk_enable",      0)
    self.rrx_d6_bucket_regime_en = _gb("rrx_d6_bucket_regime_enable", 0)
    # --- D6 shadow NAVs ---
    self._d6_lf_nav = 1.0; self._d6_tf_nav = 1.0
    self._d6_lf_peak = 1.0; self._d6_tf_peak = 1.0
    self._d6_lf_maxdd = 0.0; self._d6_tf_maxdd = 0.0
    self._d6_lf_held = None; self._d6_tf_held = None
    self._d6_lf_held_px = 0.0; self._d6_tf_held_px = 0.0
    self._d6_lf_rets: list = []; self._d6_tf_rets: list = []
    self._d6_lf_days = 0; self._d6_tf_days = 0
    self._d6_same_days = 0; self._d6_lf_better = 0; self._d6_tf_better = 0
    self._d6_lf_turn = 0; self._d6_tf_turn = 0
    self._d6_start_date = None
    self._d6_nav_is_lf = 1.0; self._d6_nav_is_tf = 1.0
    self._d6_nav_oos_lf = 1.0; self._d6_nav_oos_tf = 1.0
    self._d6_nav_cris_lf = 1.0; self._d6_nav_cris_tf = 1.0
    self._d6_nav_y20_lf = 1.0; self._d6_nav_y20_tf = 1.0
    self._d6_nav_y22_lf = 1.0; self._d6_nav_y22_tf = 1.0
    self._d6_nav_y23_lf = 1.0; self._d6_nav_y23_tf = 1.0
    self._d6_nav_y24_lf = 1.0; self._d6_nav_y24_tf = 1.0
    self._d6_month_key = None
    self._d6_mnav_lf_start = 1.0; self._d6_mnav_tf_start = 1.0
    # --- D6R: simple risk gate (panic/IDS/DD) ---
    self._d6r_nav = 1.0; self._d6r_peak = 1.0; self._d6r_maxdd = 0.0
    self._d6r_held = None; self._d6r_held_px = 0.0
    self._d6r_rets: list = []
    self._d6r_days = 0; self._d6r_turn = 0
    self._d6r_blk_panic = 0; self._d6r_blk_ids = 0; self._d6r_blk_dd = 0
    self._d6r_nav_is = 1.0; self._d6r_nav_oos = 1.0; self._d6r_nav_cris = 1.0
    self._d6r_nav_y20 = 1.0; self._d6r_nav_y22 = 1.0
    self._d6r_nav_y23 = 1.0; self._d6r_nav_y24 = 1.0
    # --- D6R-MAP: bucket-regime HARD (ALLOW only) and SOFT (ALLOW+SOFT) ---
    self._d6rm_hard_nav = 1.0; self._d6rm_hard_peak = 1.0; self._d6rm_hard_maxdd = 0.0
    self._d6rm_soft_nav = 1.0; self._d6rm_soft_peak = 1.0; self._d6rm_soft_maxdd = 0.0
    self._d6rm_hard_held = None; self._d6rm_hard_held_px = 0.0
    self._d6rm_soft_held = None; self._d6rm_soft_held_px = 0.0
    self._d6rm_hard_rets: list = []; self._d6rm_soft_rets: list = []
    self._d6rm_hard_days = 0; self._d6rm_soft_days = 0
    self._d6rm_hard_turn = 0; self._d6rm_soft_turn = 0
    # Block counters per bucket
    self._d6rm_blk_spy = 0; self._d6rm_blk_def = 0
    self._d6rm_blk_gold = 0; self._d6rm_blk_comm = 0; self._d6rm_blk_hv = 0
    self._d6rm_soft_spy = 0; self._d6rm_soft_def = 0
    self._d6rm_soft_gold = 0; self._d6rm_soft_comm = 0; self._d6rm_soft_hv = 0
    # D6R-MAP period snapshots
    self._d6rm_hard_nav_is = 1.0; self._d6rm_hard_nav_oos = 1.0
    self._d6rm_hard_nav_cris = 1.0
    self._d6rm_hard_nav_y20 = 1.0; self._d6rm_hard_nav_y22 = 1.0
    self._d6rm_hard_nav_y23 = 1.0; self._d6rm_hard_nav_y24 = 1.0
    self._d6rm_soft_nav_is = 1.0; self._d6rm_soft_nav_oos = 1.0
    self._d6rm_soft_nav_cris = 1.0
    self._d6rm_soft_nav_y20 = 1.0; self._d6rm_soft_nav_y22 = 1.0
    self._d6rm_soft_nav_y23 = 1.0; self._d6rm_soft_nav_y24 = 1.0
    # D6R-MAP SOFT_TAIL: SOFT + tail gate (panic WATCH/STRESS + RSI > 75)
    self._d6rm_tail_nav  = 1.0; self._d6rm_tail_peak = 1.0; self._d6rm_tail_maxdd = 0.0
    self._d6rm_tail_held = None; self._d6rm_tail_held_px = 0.0
    self._d6rm_tail_held_theme    = None   # theme at selection time
    self._d6rm_tail_held_bucket   = "NONE" # bucket at selection time
    self._d6rm_tail_held_decision = "NONE" # decision at selection time
    self._d6rm_audit: list = []            # audit entries for worst-25 analysis
    # HIGH_VOL ride-cap lifecycle state
    self._d6rm_hv_sym_cooldown: dict = {}  # per-symbol cooldown {key: date}
    self._d6rm_tail_entry_date   = None    # entry date of current tail position
    self._d6rm_tail_entry_px     = 0.0     # entry price of current tail position
    self._d6rm_tail_rets: list = []
    self._d6rm_tail_days = 0; self._d6rm_tail_turn = 0
    self._d6rm_tail_blk_rsi = 0; self._d6rm_tail_blk_env = 0
    self._d6rm_tail_nav_is   = 1.0; self._d6rm_tail_nav_oos  = 1.0
    self._d6rm_tail_nav_cris = 1.0
    self._d6rm_tail_nav_y20  = 1.0; self._d6rm_tail_nav_y22  = 1.0
    self._d6rm_tail_nav_y23  = 1.0; self._d6rm_tail_nav_y24  = 1.0
    # D6R-MAP TAIL50: ALLOW full, SOFT half-size, BLOCK cash
    self._d6rm_tail50_nav   = 1.0; self._d6rm_tail50_peak  = 1.0
    self._d6rm_tail50_maxdd = 0.0
    self._d6rm_tail50_held  = None; self._d6rm_tail50_held_px = 0.0
    self._d6rm_tail50_weight = 0.0
    self._d6rm_tail50_rets: list = []
    self._d6rm_tail50_days  = 0;   self._d6rm_tail50_turn  = 0
    self._d6rm_tail50_nav_is   = 1.0; self._d6rm_tail50_nav_oos  = 1.0
    self._d6rm_tail50_nav_cris = 1.0
    self._d6rm_tail50_nav_y20  = 1.0; self._d6rm_tail50_nav_y22  = 1.0
    self._d6rm_tail50_nav_y23  = 1.0; self._d6rm_tail50_nav_y24  = 1.0
    # D6R-MAP RISKON_ONLY: SPY_GROWTH / ALLOW / clean RISK_ON only
    self._d6rm_riskon_nav   = 1.0; self._d6rm_riskon_peak  = 1.0
    self._d6rm_riskon_maxdd = 0.0
    self._d6rm_riskon_held  = None; self._d6rm_riskon_held_px = 0.0
    self._d6rm_riskon_rets: list = []
    self._d6rm_riskon_days  = 0; self._d6rm_riskon_turn  = 0
    self._d6rm_riskon_block = 0
    self._d6rm_riskon_nav_is   = 1.0; self._d6rm_riskon_nav_oos  = 1.0
    self._d6rm_riskon_nav_cris = 1.0
    self._d6rm_riskon_nav_y20  = 1.0; self._d6rm_riskon_nav_y22  = 1.0
    self._d6rm_riskon_nav_y23  = 1.0; self._d6rm_riskon_nav_y24  = 1.0
    # D6R-MAP SPYG_SOFT: SPY_GROWTH / ALLOW+RISK_ON or SOFT+NEUTRAL
    self._d6rm_spyg_nav   = 1.0; self._d6rm_spyg_peak  = 1.0
    self._d6rm_spyg_maxdd = 0.0
    self._d6rm_spyg_held  = None; self._d6rm_spyg_held_px = 0.0
    self._d6rm_spyg_weight = 0.0
    self._d6rm_spyg_rets: list = []
    self._d6rm_spyg_days  = 0; self._d6rm_spyg_turn  = 0
    self._d6rm_spyg_block = 0
    self._d6rm_spyg_nav_is   = 1.0; self._d6rm_spyg_nav_oos  = 1.0
    self._d6rm_spyg_nav_cris = 1.0
    self._d6rm_spyg_nav_y20  = 1.0; self._d6rm_spyg_nav_y22  = 1.0
    self._d6rm_spyg_nav_y23  = 1.0; self._d6rm_spyg_nav_y24  = 1.0
    # D6R-MAP SPYG_LEADER: best leader inside SPY_GROWTH bucket
    self._d6rm_spyg2_nav   = 1.0; self._d6rm_spyg2_peak  = 1.0
    self._d6rm_spyg2_maxdd = 0.0
    self._d6rm_spyg2_held  = None; self._d6rm_spyg2_held_px = 0.0
    self._d6rm_spyg2_weight = 0.0
    self._d6rm_spyg2_rets: list = []
    self._d6rm_spyg2_days  = 0; self._d6rm_spyg2_turn  = 0
    self._d6rm_spyg2_block = 0
    self._d6rm_spyg2_nav_is   = 1.0; self._d6rm_spyg2_nav_oos  = 1.0
    self._d6rm_spyg2_nav_cris = 1.0
    self._d6rm_spyg2_nav_y20  = 1.0; self._d6rm_spyg2_nav_y22  = 1.0
    self._d6rm_spyg2_nav_y23  = 1.0; self._d6rm_spyg2_nav_y24  = 1.0
    # D7: TF/D6R router
    self.rrx_d7_router_enable = _gb("rrx_d7_router_enable", 0)
    self._d7_tfp_nav = 1.0;  self._d7_tfp_peak = 1.0;  self._d7_tfp_maxdd = 0.0
    self._d7_tfp_held = None; self._d7_tfp_held_px = 0.0
    self._d7_tfp_rets: list = []; self._d7_tfp_days = 0; self._d7_tfp_turn = 0
    self._d7_d6rp_nav = 1.0; self._d7_d6rp_peak = 1.0; self._d7_d6rp_maxdd = 0.0
    self._d7_d6rp_held = None; self._d7_d6rp_held_px = 0.0
    self._d7_d6rp_rets: list = []; self._d7_d6rp_days = 0; self._d7_d6rp_turn = 0
    self._d7_con_nav = 1.0;  self._d7_con_peak = 1.0;  self._d7_con_maxdd = 0.0
    self._d7_con_held = None; self._d7_con_held_px = 0.0; self._d7_con_w = 0.0
    self._d7_con_rets: list = []; self._d7_con_days = 0; self._d7_con_turn = 0
    self._d7_same_sym = 0; self._d7_same_bkt = 0; self._d7_diff = 0
    self._d7_tf_win = 0; self._d7_d6r_win = 0
    self._d7_tfp_oos = 1.0;  self._d7_tfp_cris = 1.0
    self._d7_tfp_y22 = 1.0;  self._d7_tfp_y23 = 1.0;  self._d7_tfp_y24 = 1.0
    self._d7_d6rp_oos = 1.0; self._d7_d6rp_cris = 1.0
    self._d7_d6rp_y22 = 1.0; self._d7_d6rp_y23 = 1.0; self._d7_d6rp_y24 = 1.0
    self._d7_con_oos = 1.0;  self._d7_con_cris = 1.0
    self._d7_con_y22 = 1.0;  self._d7_con_y23 = 1.0;  self._d7_con_y24 = 1.0
    self.log("RRX_D6_INIT,leader_first_diag,diag_only=1,no_trading=1"
             f",d6r={int(self.rrx_d6_risk_enable)}"
             f",d6rmap={int(self.rrx_d6_bucket_regime_en)}"
             f",d7r={int(self.rrx_d7_router_enable)}")
    if _D8_AVAIL: _D8Init(self)


# ---------------------------------------------------------------------------
# D6R simple context gate
# ---------------------------------------------------------------------------

def _RRXD6RContextOk(self) -> tuple:
    """[D6R] panic/IDS/D5Z-guard gate. Returns (ok, reason)."""
    ps  = str(getattr(self, "_panic_state", "NORMAL"))
    ids = str(getattr(self, "_ids_state",   "NORMAL"))
    if ps in ("STRESS", "PANIC"):      return False, "panic"
    if ids in ("STRESS", "PANIC", "PANIC_SHORT"): return False, "ids"
    ls = getattr(self, "_rrx_d5z_last_stop_date", None)
    gd = int(getattr(self, "rrx_d5z_stop_guard_days", 30))
    if ls is not None:
        if (self.time.date() - ls).days <= gd: return False, "dd_guard"
    return True, "ok"


def _D6RMHighVolRideOk(self, sym) -> bool:
    """[D6R-MAP] HIGH_VOL ride-cap gate: per-symbol cooldown only."""
    today = self.time.date()
    try:    sk = sym.Value
    except: sk = str(sym)
    sym_cd = getattr(self, "_d6rm_hv_sym_cooldown", {}) or {}
    if sk in sym_cd and today <= sym_cd[sk]:
        return False
    return True


def _RRXD6HVQualityOk(self, sym, theme: str, spy20: float) -> bool:
    """[D6R-MAP] HIGH_VOL first-entry quality gate.
    Requires strong momentum edge vs SPY (+8%) and vs own ETF (+5%),
    and RSI in 55-75 zone (momentum without overheat).
    """
    try:
        roc20 = self._rrx_stk_roc20.get(sym)
        rsi14 = self._rrx_stk_rsi14.get(sym)
        if not (roc20 and roc20.IsReady): return False
        if not (rsi14 and rsi14.IsReady): return False
        r20 = float(roc20.Current.Value)
        rsi = float(rsi14.Current.Value)
        if rsi < 55.0 or rsi > 75.0:     return False   # RSI zone gate
        if r20 - spy20 < 0.08:            return False   # edge vs SPY >= 8%
        etf  = self._rrx_etf_sym.get(theme)
        if etf is None:                   return False
        eroc = self._rrx_etf_roc20.get(etf)
        if not (eroc and eroc.IsReady):   return False
        if r20 - float(eroc.Current.Value) < 0.05: return False  # edge vs ETF >= 5%
        return True
    except Exception:
        return False


def _D6RMRiskOnOnlyOk(self, bucket: str, decision: str, sym) -> bool:
    """[D6R-MAP] Stage-1 candidate gate: SPY_GROWTH / ALLOW / clean RISK_ON."""
    if sym is None or bucket != "SPY_GROWTH" or decision != "ALLOW":
        return False
    reg = str(getattr(self, "current_regime", "NA"))
    ps  = str(getattr(self, "_panic_state",   "NORMAL"))
    ids = str(getattr(self, "_ids_state",     "NORMAL"))
    return (reg == "RISK_ON" and ps == "NORMAL" and ids == "NORMAL")


def _D6RMSpygSoftWeight(self, bucket: str, decision: str, sym, spy20: float) -> float:
    """[D6R-MAP] SPYG_SOFT weight: SPY_GROWTH with IDS/panic as sizing gate.
    ALLOW + RISK_ON + clean       -> 1.0
    SOFT + NEUTRAL + clean        -> 1.0
    SOFT + NEUTRAL + ids=WATCH    -> 0.5
    RISK_OFF / STRESS/PANIC       -> 0.0
    """
    if sym is None or bucket != "SPY_GROWTH":
        return 0.0
    reg = str(getattr(self, "current_regime", "NA"))
    ps  = str(getattr(self, "_panic_state",   "NORMAL"))
    ids = str(getattr(self, "_ids_state",     "NORMAL"))
    if ps  in ("STRESS", "PANIC"):               return 0.0
    if ids in ("STRESS", "PANIC", "PANIC_SHORT"): return 0.0
    if reg in ("RISK_OFF", "RISKOFF_REC", "POST_PANIC", "PANIC"): return 0.0
    if decision == "ALLOW" and reg == "RISK_ON" and ps == "NORMAL" and ids == "NORMAL":
        return 1.0
    if decision == "SOFT" and reg == "NEUTRAL" and ps == "NORMAL" and spy20 > 0:
        if ids == "NORMAL": return 1.0
        if ids == "WATCH":  return 0.5
    return 0.0


# ---------------------------------------------------------------------------
# Stock scoring
# ---------------------------------------------------------------------------

def _RRXD6StockScore(self, sym, theme: str, spy20: float, qqq20: float) -> dict:
    """[D6] Score stock globally. Theme ETF = confirmation gate only."""
    if sym is None:
        return {"score": -999.0, "ok": False, "reason": "none"}
    try:
        roc20 = self._rrx_stk_roc20.get(sym)
        sma20 = self._rrx_stk_sma20.get(sym)
        sma50 = self._rrx_stk_sma50.get(sym)
        rsi14 = self._rrx_stk_rsi14.get(sym)
        if not (roc20 and roc20.IsReady): return {"score": -999.0, "ok": False, "reason": "no_roc20"}
        if not (sma20 and sma20.IsReady): return {"score": -999.0, "ok": False, "reason": "no_sma20"}
        if not (sma50 and sma50.IsReady): return {"score": -999.0, "ok": False, "reason": "no_sma50"}
        if not (rsi14 and rsi14.IsReady): return {"score": -999.0, "ok": False, "reason": "no_rsi"}
        r20 = float(roc20.Current.Value)
        s20 = float(sma20.Current.Value)
        s50 = float(sma50.Current.Value)
        rsi = float(rsi14.Current.Value)
        try:
            px = float(self.securities[sym].price)
        except Exception:
            return {"score": -999.0, "ok": False, "reason": "no_px"}
        if px <= 0:                    return {"score": -999.0, "ok": False, "reason": "px_zero"}
        if s50 <= 0 or px <= s50:     return {"score": -999.0, "ok": False, "reason": "below_s50"}
        if s20 <= 0 or px <= s20:     return {"score": -999.0, "ok": False, "reason": "below_s20"}
        if r20 <= spy20:               return {"score": -999.0, "ok": False, "reason": "under_spy"}
        ovheat_rsi = float(getattr(self, "rrx_overheat_rsi", 85.0))
        if rsi >= ovheat_rsi:          return {"score": -999.0, "ok": False, "reason": "overheat"}
        etf = self._rrx_etf_sym.get(theme)
        if etf is None:                return {"score": -999.0, "ok": False, "reason": "no_etf"}
        eroc = self._rrx_etf_roc20.get(etf); esma = self._rrx_etf_sma50.get(etf)
        if not (eroc and eroc.IsReady): return {"score": -999.0, "ok": False, "reason": "etf_nrdy"}
        if not (esma and esma.IsReady): return {"score": -999.0, "ok": False, "reason": "etf_sma"}
        etf_r20 = float(eroc.Current.Value)
        try:
            etf_px  = float(self.securities[etf].price)
            etf_s50 = float(esma.Current.Value)
        except Exception:
            return {"score": -999.0, "ok": False, "reason": "etf_px"}
        if etf_s50 <= 0 or etf_px <= etf_s50: return {"score": -999.0, "ok": False, "reason": "etf_broken"}
        if r20 <= etf_r20:             return {"score": -999.0, "ok": False, "reason": "under_etf"}
        edge_spy = r20 - spy20; edge_qqq = r20 - qqq20; edge_etf = r20 - etf_r20
        norm_spy = max(-1.0, min(1.0, edge_spy / 0.20))
        norm_qqq = max(-1.0, min(1.0, edge_qqq / 0.20))
        norm_etf = max(-1.0, min(1.0, edge_etf / 0.15))
        norm_m20 = max(-1.0, min(1.0, r20 / 0.15))
        ovheat_p = max(0.0, (rsi - 75.0) / 10.0) if rsi > 75.0 else 0.0
        score = (0.30 * norm_spy + 0.20 * norm_qqq + 0.20 * norm_etf
                 + 0.20 * 1.0 + 0.10 * norm_m20 - 0.15 * ovheat_p)
        return {"score": score, "ok": True, "r20": r20, "rsi": rsi, "theme": theme}
    except Exception:
        return {"score": -999.0, "ok": False, "reason": "err"}


def _RRXD6FindLeader(self, spy20: float, qqq20: float):
    """[D6] Global best stock across all themes."""
    from rr_xsector_diag import RRX_THEMES
    best_sym, best_sc, best_theme = None, -999.0, None
    for theme in RRX_THEMES:
        for sym in self._rrx_stk_sym.get(theme, []):
            d  = _RRXD6StockScore(self, sym, theme, spy20, qqq20)
            sc = float(d.get("score", -999.0))
            if sc > best_sc:
                best_sc, best_sym, best_theme = sc, sym, theme
    if best_sc <= -900.0:
        return None, None, -999.0
    return best_sym, best_theme, best_sc


# ---------------------------------------------------------------------------
# Price helper
# ---------------------------------------------------------------------------

def _RRXD6FindLeaderInBucket(self, target_bucket: str, spy20: float, qqq20: float):
    """[D6R-MAP] Best stock leader inside one risk bucket only."""
    from rr_xsector_diag import RRX_THEMES
    best_sym, best_theme, best_sc = None, None, -999.0
    for theme in RRX_THEMES:
        if _RRXD6ThemeBucket(theme) != target_bucket:
            continue
        for sym in self._rrx_stk_sym.get(theme, []):
            d  = _RRXD6StockScore(self, sym, theme, spy20, qqq20)
            sc = float(d.get("score", -999.0))
            if sc > best_sc:
                best_sc, best_sym, best_theme = sc, sym, theme
    if best_sc <= -900.0:
        return None, None, -999.0
    return best_sym, best_theme, best_sc


def _RRXD6PxOf(self, sym) -> float:
    try:
        if sym is None: return 0.0
        return float(self.securities[sym].price)
    except Exception:
        return 0.0


# ---------------------------------------------------------------------------
# NAV helper: apply return to one shadow curve
# ---------------------------------------------------------------------------

def _d6_apply_nav(nav, peak, maxdd, held, held_px, new_sym, pxof_fn):
    """Update one shadow NAV. Returns (nav, peak, maxdd, ret)."""
    ret = 0.0
    if held is not None and held_px > 0:
        cur = pxof_fn(held)
        if cur > 0:
            ret = cur / held_px - 1.0
    nav = max(0.01, nav * (1.0 + ret))
    if nav > peak: peak = nav
    dd = 1.0 - nav / max(peak, 1e-9)
    if dd > maxdd: maxdd = dd
    return nav, peak, maxdd, ret


# ---------------------------------------------------------------------------
# Weighted NAV helper (for TAIL50: SOFT = 0.5 weight)
# ---------------------------------------------------------------------------

def _d6_apply_nav_weighted(nav, peak, maxdd, held, held_px, weight, pxof_fn):
    """Update weighted shadow NAV. weight=1.0 full, 0.5 half, 0.0 cash."""
    ret = 0.0
    if held is not None and held_px > 0 and weight > 0:
        cur = pxof_fn(held)
        if cur > 0:
            ret = float(weight) * (cur / held_px - 1.0)
    nav = max(0.01, nav * (1.0 + ret))
    if nav > peak: peak = nav
    dd = 1.0 - nav / max(peak, 1e-9)
    if dd > maxdd: maxdd = dd
    return nav, peak, maxdd, ret


def _D6RMAdaptiveSoftWeight(self, bucket: str, decision: str, sym) -> float:
    """[D6R-MAP] Adaptive shadow weight for TAIL_ADAPT.
    ALLOW = 1.0 always.
    SOFT = bucket/regime-dependent.
    BLOCK or None = 0.0.
    """
    if sym is None or decision == "BLOCK":
        return 0.0
    if decision == "ALLOW":
        return 1.0
    if decision != "SOFT":
        return 0.0
    ps_now  = str(getattr(self, "_panic_state",  "NORMAL"))
    ids_now = str(getattr(self, "_ids_state",    "NORMAL"))
    reg_now = str(getattr(self, "current_regime", "NA"))
    calm         = (ps_now == "NORMAL" and ids_now == "NORMAL")
    risk_on_like = reg_now in ("RISK_ON", "NEUTRAL")
    def_like     = reg_now in ("RISK_OFF", "RISKOFF_REC", "POST_PANIC", "NEUTRAL")
    # SPY-like: full in calm RISK_ON/NEUTRAL, half otherwise
    if bucket == "SPY_GROWTH":
        return 1.0 if (calm and risk_on_like) else 0.5
    # HIGH_VOL: always half in SOFT
    if bucket == "HIGH_VOL":
        return 0.5
    # Commodity: transitional, half
    if bucket == "COMMODITY":
        return 0.5
    # Defensive/safe-haven: full in protective regime, half otherwise
    if bucket in ("DEFENSIVE", "SAFE_HAVEN"):
        if (def_like and ps_now != "PANIC"
                and ids_now not in ("PANIC", "PANIC_SHORT")):
            return 1.0
        return 0.5
    return 0.5


# ---------------------------------------------------------------------------
# Daily update
# ---------------------------------------------------------------------------

def RRXD6LeaderFirstUpdate(self) -> None:
    """[D6/D6R/D6R-MAP] Daily cycle. Must be called after RRXDailyCycle."""
    today = self.time.date()
    if self._d6_start_date is None:
        self._d6_start_date = today

    spy20 = float(self._rrx_spy_roc20.Current.Value) if self._rrx_spy_roc20.IsReady else 0.0
    qqq20 = float(self._rrx_qqq_roc20.Current.Value) if self._rrx_qqq_roc20.IsReady else 0.0
    pxof  = lambda s: _RRXD6PxOf(self, s)

    # -- D6: apply yesterday's returns --
    held_lf = getattr(self, "_d6_lf_held", None)
    held_lf_px = getattr(self, "_d6_lf_held_px", 0.0)
    held_tf = getattr(self, "_d6_tf_held", None)
    held_tf_px = getattr(self, "_d6_tf_held_px", 0.0)
    self._d6_lf_nav, self._d6_lf_peak, self._d6_lf_maxdd, lf_ret = \
        _d6_apply_nav(self._d6_lf_nav, self._d6_lf_peak, self._d6_lf_maxdd,
                      held_lf, held_lf_px, None, pxof)
    self._d6_tf_nav, self._d6_tf_peak, self._d6_tf_maxdd, tf_ret = \
        _d6_apply_nav(self._d6_tf_nav, self._d6_tf_peak, self._d6_tf_maxdd,
                      held_tf, held_tf_px, None, pxof)
    self._d6_lf_rets.append(lf_ret); self._d6_tf_rets.append(tf_ret)
    if len(self._d6_lf_rets) > 4000:
        self._d6_lf_rets = self._d6_lf_rets[-2000:]
        self._d6_tf_rets = self._d6_tf_rets[-2000:]
    if held_lf is not None: self._d6_lf_days += 1
    if held_tf is not None: self._d6_tf_days += 1
    same = held_lf is not None and held_tf is not None and held_lf == held_tf
    if same:                          self._d6_same_days += 1
    elif lf_ret > tf_ret + 1e-6:     self._d6_lf_better += 1
    elif tf_ret > lf_ret + 1e-6:     self._d6_tf_better += 1

    # -- Find today's selections --
    lf_sym, lf_theme, _lf_sc = _RRXD6FindLeader(self, spy20, qqq20)
    tf_sym = getattr(self, "_rrx_top_stock", None)
    if lf_sym != held_lf and held_lf is not None: self._d6_lf_turn += 1
    if tf_sym != held_tf and held_tf is not None: self._d6_tf_turn += 1
    self._d6_lf_held = lf_sym; self._d6_lf_held_px = pxof(lf_sym)
    self._d6_tf_held = tf_sym; self._d6_tf_held_px = pxof(tf_sym)

    yr = today.year
    if 2012 <= yr <= 2018:
        self._d6_nav_is_lf = self._d6_lf_nav; self._d6_nav_is_tf = self._d6_tf_nav
    if 2019 <= yr <= 2021:
        self._d6_nav_oos_lf = self._d6_lf_nav; self._d6_nav_oos_tf = self._d6_tf_nav
    if 2022 <= yr <= 2025:
        self._d6_nav_cris_lf = self._d6_lf_nav; self._d6_nav_cris_tf = self._d6_tf_nav
    if yr == 2020: self._d6_nav_y20_lf = self._d6_lf_nav; self._d6_nav_y20_tf = self._d6_tf_nav
    if yr == 2022: self._d6_nav_y22_lf = self._d6_lf_nav; self._d6_nav_y22_tf = self._d6_tf_nav
    if yr == 2023: self._d6_nav_y23_lf = self._d6_lf_nav; self._d6_nav_y23_tf = self._d6_tf_nav
    if yr == 2024: self._d6_nav_y24_lf = self._d6_lf_nav; self._d6_nav_y24_tf = self._d6_tf_nav

    # -- D6R: simple risk gate --
    if getattr(self, "rrx_d6_risk_enable", False):
        d6r_ok, d6r_reason = _RRXD6RContextOk(self)
        held_d6r = getattr(self, "_d6r_held", None)
        held_d6r_px = getattr(self, "_d6r_held_px", 0.0)
        self._d6r_nav, self._d6r_peak, self._d6r_maxdd, d6r_ret = \
            _d6_apply_nav(self._d6r_nav, self._d6r_peak, self._d6r_maxdd,
                          held_d6r, held_d6r_px, None, pxof)
        self._d6r_rets.append(d6r_ret)
        if len(self._d6r_rets) > 4000: self._d6r_rets = self._d6r_rets[-2000:]
        d6r_sym = lf_sym if (d6r_ok and lf_sym is not None) else None
        if not d6r_ok:
            if d6r_reason == "panic":     self._d6r_blk_panic += 1
            elif d6r_reason == "ids":     self._d6r_blk_ids   += 1
            elif d6r_reason == "dd_guard": self._d6r_blk_dd   += 1
        if d6r_sym != held_d6r and held_d6r is not None: self._d6r_turn += 1
        if held_d6r is not None: self._d6r_days += 1
        self._d6r_held = d6r_sym; self._d6r_held_px = pxof(d6r_sym)
        if 2012 <= yr <= 2018: self._d6r_nav_is   = self._d6r_nav
        if 2019 <= yr <= 2021: self._d6r_nav_oos  = self._d6r_nav
        if 2022 <= yr <= 2025: self._d6r_nav_cris = self._d6r_nav
        if yr == 2020: self._d6r_nav_y20 = self._d6r_nav
        if yr == 2022: self._d6r_nav_y22 = self._d6r_nav
        if yr == 2023: self._d6r_nav_y23 = self._d6r_nav
        if yr == 2024: self._d6r_nav_y24 = self._d6r_nav

    # -- D6R-MAP: bucket-regime HARD and SOFT --
    if getattr(self, "rrx_d6_bucket_regime_en", False):

        # 1) Apply yesterday's HARD held return every day
        held_hard    = getattr(self, "_d6rm_hard_held",    None)
        held_hard_px = getattr(self, "_d6rm_hard_held_px", 0.0)
        self._d6rm_hard_nav, self._d6rm_hard_peak, self._d6rm_hard_maxdd, hard_ret = \
            _d6_apply_nav(self._d6rm_hard_nav, self._d6rm_hard_peak, self._d6rm_hard_maxdd,
                          held_hard, held_hard_px, None, pxof)
        self._d6rm_hard_rets.append(hard_ret)
        if len(self._d6rm_hard_rets) > 4000:
            self._d6rm_hard_rets = self._d6rm_hard_rets[-2000:]
        if held_hard is not None: self._d6rm_hard_days += 1

        # 2) Apply yesterday's SOFT held return every day
        held_soft    = getattr(self, "_d6rm_soft_held",    None)
        held_soft_px = getattr(self, "_d6rm_soft_held_px", 0.0)
        self._d6rm_soft_nav, self._d6rm_soft_peak, self._d6rm_soft_maxdd, soft_ret = \
            _d6_apply_nav(self._d6rm_soft_nav, self._d6rm_soft_peak, self._d6rm_soft_maxdd,
                          held_soft, held_soft_px, None, pxof)
        self._d6rm_soft_rets.append(soft_ret)
        if len(self._d6rm_soft_rets) > 4000:
            self._d6rm_soft_rets = self._d6rm_soft_rets[-2000:]
        if held_soft is not None: self._d6rm_soft_days += 1

        # 3) Decide today's symbol (cash if no leader)
        bucket   = "NONE"
        decision = "BLOCK"
        if lf_theme is not None and lf_sym is not None:
            bucket   = _RRXD6ThemeBucket(lf_theme)
            decision = _RRXD6BucketDecision(self, bucket, spy20)
            if decision == "BLOCK":
                if bucket == "SPY_GROWTH":  self._d6rm_blk_spy  += 1
                elif bucket == "DEFENSIVE": self._d6rm_blk_def  += 1
                elif bucket == "SAFE_HAVEN":self._d6rm_blk_gold += 1
                elif bucket == "COMMODITY": self._d6rm_blk_comm += 1
                else:                       self._d6rm_blk_hv   += 1
            elif decision == "SOFT":
                if bucket == "SPY_GROWTH":  self._d6rm_soft_spy  += 1
                elif bucket == "DEFENSIVE": self._d6rm_soft_def  += 1
                elif bucket == "SAFE_HAVEN":self._d6rm_soft_gold += 1
                elif bucket == "COMMODITY": self._d6rm_soft_comm += 1
                else:                       self._d6rm_soft_hv   += 1

        hard_sym = lf_sym if decision == "ALLOW" else None
        soft_sym = lf_sym if decision in ("ALLOW", "SOFT") else None
        if hard_sym != held_hard and held_hard is not None: self._d6rm_hard_turn += 1
        if soft_sym != held_soft and held_soft is not None: self._d6rm_soft_turn += 1
        self._d6rm_hard_held    = hard_sym; self._d6rm_hard_held_px = pxof(hard_sym)
        self._d6rm_soft_held    = soft_sym; self._d6rm_soft_held_px = pxof(soft_sym)

        # SOFT_TAIL: apply yesterday return first, then decide tomorrow
        held_tail    = getattr(self, "_d6rm_tail_held",    None)
        held_tail_px = getattr(self, "_d6rm_tail_held_px", 0.0)
        self._d6rm_tail_nav, self._d6rm_tail_peak, self._d6rm_tail_maxdd, tail_ret = \
            _d6_apply_nav(self._d6rm_tail_nav, self._d6rm_tail_peak, self._d6rm_tail_maxdd,
                          held_tail, held_tail_px, None, pxof)
        self._d6rm_tail_rets.append(tail_ret)
        if len(self._d6rm_tail_rets) > 4000: self._d6rm_tail_rets = self._d6rm_tail_rets[-2000:]
        if held_tail is not None: self._d6rm_tail_days += 1
        # Audit
        if tail_ret < -0.01 and held_tail is not None:
            try:    sym_v = held_tail.Value
            except: sym_v = str(held_tail)
            try:    rsi_v = float(self._rrx_stk_rsi14.get(held_tail).Current.Value)
            except: rsi_v = 0.0
            self._d6rm_audit.append({
                "ret":      tail_ret, "date": str(today), "sym": sym_v,
                "theme":    getattr(self, "_d6rm_tail_held_theme",    "NONE") or "NONE",
                "bucket":   getattr(self, "_d6rm_tail_held_bucket",   "NONE") or "NONE",
                "decision": getattr(self, "_d6rm_tail_held_decision", "NONE") or "NONE",
                "rsi":      round(rsi_v, 1), "spy20": round(spy20, 4),
                "ps":       str(getattr(self, "_panic_state", "NORMAL")),
                "ids":      str(getattr(self, "_ids_state",   "NORMAL")),
                "regime":   str(getattr(self, "current_regime", "NA")),
            })
        # HIGH_VOL exit: set cooldown if ride-cap triggered
        tail_force_exit = False
        if held_tail is not None and getattr(self, "_d6rm_tail_held_bucket", "NONE") == "HIGH_VOL":
            entry_px   = getattr(self, "_d6rm_tail_entry_px",   0.0)
            entry_date = getattr(self, "_d6rm_tail_entry_date", None)
            cur_px_hv  = pxof(held_tail)
            cum_pnl    = (cur_px_hv / entry_px - 1.0) if (entry_px > 0 and cur_px_hv > 0) else 0.0
            age_days   = (today - entry_date).days if entry_date else 999
            if age_days >= 10 or cum_pnl >= 0.12 or cum_pnl <= -0.04:
                try:    sk = held_tail.Value
                except: sk = str(held_tail)
                sym_cd = getattr(self, "_d6rm_hv_sym_cooldown", {}) or {}
                sym_cd[sk] = today + timedelta(days=65)
                self._d6rm_hv_sym_cooldown = sym_cd
                tail_force_exit = True
        # Determine tomorrow tail_sym
        ps_t  = str(getattr(self, "_panic_state", "NORMAL"))
        ids_t = str(getattr(self, "_ids_state",   "NORMAL"))
        tail_env_ok = (ps_t  not in ("WATCH", "STRESS", "PANIC") and
                       ids_t not in ("WATCH", "STRESS", "PANIC", "PANIC_SHORT"))
        tail_rsi_ok = True
        if lf_sym is not None:
            try:
                ri = self._rrx_stk_rsi14.get(lf_sym)
                if ri and ri.IsReady and float(ri.Current.Value) > 75.0:
                    tail_rsi_ok = False
            except Exception:
                pass
        if tail_force_exit:
            tail_sym = None
        elif decision == "ALLOW":
            tail_sym = lf_sym
        elif decision == "SOFT":
            if bucket == "HIGH_VOL":
                hv_ok  = _D6RMHighVolRideOk(self, lf_sym) if lf_sym is not None else False
                hv_qty = _RRXD6HVQualityOk(self, lf_sym, lf_theme, spy20) if (hv_ok and lf_sym is not None) else False
                if hv_ok and hv_qty and tail_env_ok and tail_rsi_ok:
                    tail_sym = lf_sym
                else:
                    tail_sym = None
                    if not tail_env_ok:   self._d6rm_tail_blk_env += 1
                    elif not tail_rsi_ok: self._d6rm_tail_blk_rsi += 1
            elif tail_env_ok and tail_rsi_ok:
                tail_sym = lf_sym
            else:
                tail_sym = None
                if not tail_env_ok:   self._d6rm_tail_blk_env += 1
                elif not tail_rsi_ok: self._d6rm_tail_blk_rsi += 1
        else:
            tail_sym = None
        # Turnover + entry tracking
        if tail_sym != held_tail and held_tail is not None: self._d6rm_tail_turn += 1
        if tail_sym is not None and tail_sym != held_tail:
            self._d6rm_tail_entry_date = today
            self._d6rm_tail_entry_px   = pxof(tail_sym)
        elif tail_sym is None:
            self._d6rm_tail_entry_date = None
            self._d6rm_tail_entry_px   = 0.0
        self._d6rm_tail_held    = tail_sym
        self._d6rm_tail_held_px = pxof(tail_sym)
        self._d6rm_tail_held_theme    = lf_theme if tail_sym is not None else None
        self._d6rm_tail_held_bucket   = bucket   if tail_sym is not None else "NONE"
        self._d6rm_tail_held_decision = decision if tail_sym is not None else "NONE"

        # TAIL50: same symbol as TAIL, SOFT gets 0.5 weight
        held50    = getattr(self, "_d6rm_tail50_held",    None)
        held50_px = getattr(self, "_d6rm_tail50_held_px", 0.0)
        held50_w  = float(getattr(self, "_d6rm_tail50_weight", 0.0) or 0.0)
        self._d6rm_tail50_nav, self._d6rm_tail50_peak, self._d6rm_tail50_maxdd, tail50_ret =             _d6_apply_nav_weighted(
                self._d6rm_tail50_nav, self._d6rm_tail50_peak, self._d6rm_tail50_maxdd,
                held50, held50_px, held50_w, pxof)
        self._d6rm_tail50_rets.append(tail50_ret)
        if len(self._d6rm_tail50_rets) > 4000:
            self._d6rm_tail50_rets = self._d6rm_tail50_rets[-2000:]
        if held50 is not None: self._d6rm_tail50_days += 1
        # TAIL_ADAPT: same gates as TAIL, adaptive weight by bucket/regime
        tail50_sym = tail_sym
        t50_w = _D6RMAdaptiveSoftWeight(self, bucket, decision, tail50_sym)
        if tail_sym != held50 and held50 is not None: self._d6rm_tail50_turn += 1
        self._d6rm_tail50_held    = tail_sym
        self._d6rm_tail50_held_px = pxof(tail_sym)
        self._d6rm_tail50_weight  = t50_w

        # RISKON_ONLY: SPY_GROWTH / ALLOW / clean RISK_ON
        held_ro    = getattr(self, "_d6rm_riskon_held",    None)
        held_ro_px = getattr(self, "_d6rm_riskon_held_px", 0.0)
        self._d6rm_riskon_nav, self._d6rm_riskon_peak, self._d6rm_riskon_maxdd, ro_ret =             _d6_apply_nav(self._d6rm_riskon_nav, self._d6rm_riskon_peak,
                          self._d6rm_riskon_maxdd, held_ro, held_ro_px, None, pxof)
        self._d6rm_riskon_rets.append(ro_ret)
        if len(self._d6rm_riskon_rets) > 4000:
            self._d6rm_riskon_rets = self._d6rm_riskon_rets[-2000:]
        if held_ro is not None: self._d6rm_riskon_days += 1
        riskon_sym = lf_sym if _D6RMRiskOnOnlyOk(self, bucket, decision, lf_sym) else None
        if riskon_sym is None and lf_sym is not None: self._d6rm_riskon_block += 1
        if riskon_sym != held_ro and held_ro is not None: self._d6rm_riskon_turn += 1
        self._d6rm_riskon_held    = riskon_sym
        self._d6rm_riskon_held_px = pxof(riskon_sym)

        # SPYG_SOFT: SPY_GROWTH with IDS/panic as sizing gate
        held_spyg    = getattr(self, "_d6rm_spyg_held",    None)
        held_spyg_px = getattr(self, "_d6rm_spyg_held_px", 0.0)
        held_spyg_w  = float(getattr(self, "_d6rm_spyg_weight", 0.0) or 0.0)
        self._d6rm_spyg_nav, self._d6rm_spyg_peak, self._d6rm_spyg_maxdd, spyg_ret =             _d6_apply_nav_weighted(
                self._d6rm_spyg_nav, self._d6rm_spyg_peak, self._d6rm_spyg_maxdd,
                held_spyg, held_spyg_px, held_spyg_w, pxof)
        self._d6rm_spyg_rets.append(spyg_ret)
        if len(self._d6rm_spyg_rets) > 4000:
            self._d6rm_spyg_rets = self._d6rm_spyg_rets[-2000:]
        if held_spyg is not None: self._d6rm_spyg_days += 1
        spyg_w = _D6RMSpygSoftWeight(self, bucket, decision, lf_sym, spy20)
        spyg_sym = lf_sym if spyg_w > 0 else None
        if spyg_sym is None and lf_sym is not None: self._d6rm_spyg_block += 1
        if spyg_sym != held_spyg and held_spyg is not None: self._d6rm_spyg_turn += 1
        self._d6rm_spyg_held    = spyg_sym
        self._d6rm_spyg_held_px = pxof(spyg_sym)
        self._d6rm_spyg_weight  = spyg_w

        # SPYG_LEADER: best leader inside SPY_GROWTH, not global LF
        held_spyg2    = getattr(self, "_d6rm_spyg2_held",    None)
        held_spyg2_px = getattr(self, "_d6rm_spyg2_held_px", 0.0)
        held_spyg2_w  = float(getattr(self, "_d6rm_spyg2_weight", 0.0) or 0.0)
        self._d6rm_spyg2_nav, self._d6rm_spyg2_peak, self._d6rm_spyg2_maxdd, spyg2_ret =             _d6_apply_nav_weighted(
                self._d6rm_spyg2_nav, self._d6rm_spyg2_peak, self._d6rm_spyg2_maxdd,
                held_spyg2, held_spyg2_px, held_spyg2_w, pxof)
        self._d6rm_spyg2_rets.append(spyg2_ret)
        if len(self._d6rm_spyg2_rets) > 4000:
            self._d6rm_spyg2_rets = self._d6rm_spyg2_rets[-2000:]
        if held_spyg2 is not None: self._d6rm_spyg2_days += 1
        spyg2_sym, spyg2_theme, _spyg2_sc = _RRXD6FindLeaderInBucket(
            self, "SPY_GROWTH", spy20, qqq20)
        spyg2_decision = "BLOCK"
        if spyg2_sym is not None:
            spyg2_decision = _RRXD6BucketDecision(self, "SPY_GROWTH", spy20)
        spyg2_w = _D6RMSpygSoftWeight(
            self, "SPY_GROWTH", spyg2_decision, spyg2_sym, spy20)
        spyg2_trade_sym = spyg2_sym if spyg2_w > 0 else None
        if spyg2_trade_sym is None and spyg2_sym is not None:
            self._d6rm_spyg2_block += 1
        if spyg2_trade_sym != held_spyg2 and held_spyg2 is not None:
            self._d6rm_spyg2_turn += 1
        self._d6rm_spyg2_held    = spyg2_trade_sym
        self._d6rm_spyg2_held_px = pxof(spyg2_trade_sym)
        self._d6rm_spyg2_weight  = spyg2_w

        # 4) Period snapshots
        if 2012 <= yr <= 2018:
            self._d6rm_hard_nav_is    = self._d6rm_hard_nav
            self._d6rm_soft_nav_is    = self._d6rm_soft_nav
            self._d6rm_tail_nav_is    = self._d6rm_tail_nav
            self._d6rm_tail50_nav_is  = self._d6rm_tail50_nav
        if 2019 <= yr <= 2021:
            self._d6rm_hard_nav_oos   = self._d6rm_hard_nav
            self._d6rm_soft_nav_oos   = self._d6rm_soft_nav
            self._d6rm_tail_nav_oos   = self._d6rm_tail_nav
            self._d6rm_tail50_nav_oos = self._d6rm_tail50_nav
        if 2022 <= yr <= 2025:
            self._d6rm_hard_nav_cris   = self._d6rm_hard_nav
            self._d6rm_soft_nav_cris   = self._d6rm_soft_nav
            self._d6rm_tail_nav_cris   = self._d6rm_tail_nav
            self._d6rm_tail50_nav_cris = self._d6rm_tail50_nav
        if yr == 2020:
            self._d6rm_hard_nav_y20   = self._d6rm_hard_nav
            self._d6rm_soft_nav_y20   = self._d6rm_soft_nav
            self._d6rm_tail_nav_y20   = self._d6rm_tail_nav
            self._d6rm_tail50_nav_y20 = self._d6rm_tail50_nav
        if yr == 2022:
            self._d6rm_hard_nav_y22   = self._d6rm_hard_nav
            self._d6rm_soft_nav_y22   = self._d6rm_soft_nav
            self._d6rm_tail_nav_y22   = self._d6rm_tail_nav
            self._d6rm_tail50_nav_y22 = self._d6rm_tail50_nav
        if yr == 2023:
            self._d6rm_hard_nav_y23   = self._d6rm_hard_nav
            self._d6rm_soft_nav_y23   = self._d6rm_soft_nav
            self._d6rm_tail_nav_y23   = self._d6rm_tail_nav
            self._d6rm_tail50_nav_y23 = self._d6rm_tail50_nav
        if yr == 2024:
            self._d6rm_hard_nav_y24   = self._d6rm_hard_nav
            self._d6rm_soft_nav_y24   = self._d6rm_soft_nav
            self._d6rm_tail_nav_y24   = self._d6rm_tail_nav
            self._d6rm_tail50_nav_y24 = self._d6rm_tail50_nav
        # RISKON_ONLY period snapshots
        if 2012 <= yr <= 2018: self._d6rm_riskon_nav_is   = self._d6rm_riskon_nav
        if 2019 <= yr <= 2021: self._d6rm_riskon_nav_oos  = self._d6rm_riskon_nav
        if 2022 <= yr <= 2025: self._d6rm_riskon_nav_cris = self._d6rm_riskon_nav
        if yr == 2020: self._d6rm_riskon_nav_y20 = self._d6rm_riskon_nav
        if yr == 2022: self._d6rm_riskon_nav_y22 = self._d6rm_riskon_nav
        if yr == 2023: self._d6rm_riskon_nav_y23 = self._d6rm_riskon_nav
        if yr == 2024: self._d6rm_riskon_nav_y24 = self._d6rm_riskon_nav
        # SPYG_SOFT period snapshots
        if 2012 <= yr <= 2018: self._d6rm_spyg_nav_is   = self._d6rm_spyg_nav
        if 2019 <= yr <= 2021: self._d6rm_spyg_nav_oos  = self._d6rm_spyg_nav
        if 2022 <= yr <= 2025: self._d6rm_spyg_nav_cris = self._d6rm_spyg_nav
        if yr == 2020: self._d6rm_spyg_nav_y20 = self._d6rm_spyg_nav
        if yr == 2022: self._d6rm_spyg_nav_y22 = self._d6rm_spyg_nav
        if yr == 2023: self._d6rm_spyg_nav_y23 = self._d6rm_spyg_nav
        if yr == 2024: self._d6rm_spyg_nav_y24 = self._d6rm_spyg_nav
        if 2012 <= yr <= 2018: self._d6rm_spyg2_nav_is   = self._d6rm_spyg2_nav
        if 2019 <= yr <= 2021: self._d6rm_spyg2_nav_oos  = self._d6rm_spyg2_nav
        if 2022 <= yr <= 2025: self._d6rm_spyg2_nav_cris = self._d6rm_spyg2_nav
        if yr == 2020: self._d6rm_spyg2_nav_y20 = self._d6rm_spyg2_nav
        if yr == 2022: self._d6rm_spyg2_nav_y22 = self._d6rm_spyg2_nav
        if yr == 2023: self._d6rm_spyg2_nav_y23 = self._d6rm_spyg2_nav
        if yr == 2024: self._d6rm_spyg2_nav_y24 = self._d6rm_spyg2_nav

        # D7: TF/D6R router (3 shadow curves)
        if getattr(self, "rrx_d7_router_enable", False):
            tf_bkt = _RRXD6ThemeBucket(str(getattr(self,"_rrx_top_theme","") or "")) if tf_sym else "NONE"
            d6r_sym = self._d6r_held
            d6r_bkt = _RRXD6ThemeBucket(lf_theme) if (lf_sym and lf_theme) else "NONE"
            d6r_ok  = _RRXD6RContextOk(self)[0]
            same_s  = (tf_sym is not None and d6r_sym is not None and tf_sym == d6r_sym)
            same_b  = (tf_sym is not None and d6r_sym is not None and tf_bkt == d6r_bkt and tf_bkt != "NONE")
            # TF_PRIMARY
            held_tfp = getattr(self,"_d7_tfp_held",None); held_tfp_px = getattr(self,"_d7_tfp_held_px",0.0)
            self._d7_tfp_nav,self._d7_tfp_peak,self._d7_tfp_maxdd,tfp_ret =                 _d6_apply_nav(self._d7_tfp_nav,self._d7_tfp_peak,self._d7_tfp_maxdd,held_tfp,held_tfp_px,None,pxof)
            self._d7_tfp_rets.append(tfp_ret)
            if len(self._d7_tfp_rets)>4000: self._d7_tfp_rets=self._d7_tfp_rets[-2000:]
            if held_tfp is not None: self._d7_tfp_days += 1
            tfp_sym = tf_sym if d6r_ok else None
            if tfp_sym != held_tfp and held_tfp is not None: self._d7_tfp_turn += 1
            self._d7_tfp_held = tfp_sym; self._d7_tfp_held_px = pxof(tfp_sym)
            # D6R_PRIMARY
            held_d6rp = getattr(self,"_d7_d6rp_held",None); held_d6rp_px = getattr(self,"_d7_d6rp_held_px",0.0)
            self._d7_d6rp_nav,self._d7_d6rp_peak,self._d7_d6rp_maxdd,d6rp_ret =                 _d6_apply_nav(self._d7_d6rp_nav,self._d7_d6rp_peak,self._d7_d6rp_maxdd,held_d6rp,held_d6rp_px,None,pxof)
            self._d7_d6rp_rets.append(d6rp_ret)
            if len(self._d7_d6rp_rets)>4000: self._d7_d6rp_rets=self._d7_d6rp_rets[-2000:]
            if held_d6rp is not None: self._d7_d6rp_days += 1
            d6rp_sym = d6r_sym if (same_s or same_b) else None
            if d6rp_sym != held_d6rp and held_d6rp is not None: self._d7_d6rp_turn += 1
            self._d7_d6rp_held = d6rp_sym; self._d7_d6rp_held_px = pxof(d6rp_sym)
            # CONSENSUS
            held_con = getattr(self,"_d7_con_held",None); held_con_px = getattr(self,"_d7_con_held_px",0.0)
            held_con_w = float(getattr(self,"_d7_con_w",0.0))
            self._d7_con_nav,self._d7_con_peak,self._d7_con_maxdd,con_ret =                 _d6_apply_nav_weighted(self._d7_con_nav,self._d7_con_peak,self._d7_con_maxdd,held_con,held_con_px,held_con_w,pxof)
            self._d7_con_rets.append(con_ret)
            if len(self._d7_con_rets)>4000: self._d7_con_rets=self._d7_con_rets[-2000:]
            if held_con is not None: self._d7_con_days += 1
            if same_s:   con_sym,con_w = tf_sym,1.0
            elif same_b: con_sym,con_w = tf_sym,0.5
            else:        con_sym,con_w = None,0.0
            if con_sym != held_con and held_con is not None: self._d7_con_turn += 1
            self._d7_con_held = con_sym; self._d7_con_held_px = pxof(con_sym); self._d7_con_w = con_w
            # Disagreement tracking
            if tf_sym is not None and d6r_sym is not None:
                if same_s:   self._d7_same_sym += 1
                elif same_b: self._d7_same_bkt += 1
                else:
                    self._d7_diff += 1
                    if tfp_ret > d6rp_ret+1e-6:  self._d7_tf_win  += 1
                    elif d6rp_ret > tfp_ret+1e-6: self._d7_d6r_win += 1
            # D7 period snapshots
            if 2019<=yr<=2021:
                self._d7_tfp_oos=self._d7_tfp_nav; self._d7_d6rp_oos=self._d7_d6rp_nav; self._d7_con_oos=self._d7_con_nav
            if 2022<=yr<=2025:
                self._d7_tfp_cris=self._d7_tfp_nav; self._d7_d6rp_cris=self._d7_d6rp_nav; self._d7_con_cris=self._d7_con_nav
            if yr==2022: self._d7_tfp_y22=self._d7_tfp_nav; self._d7_d6rp_y22=self._d7_d6rp_nav; self._d7_con_y22=self._d7_con_nav
            if yr==2023: self._d7_tfp_y23=self._d7_tfp_nav; self._d7_d6rp_y23=self._d7_d6rp_nav; self._d7_con_y23=self._d7_con_nav
            if yr==2024: self._d7_tfp_y24=self._d7_tfp_nav; self._d7_d6rp_y24=self._d7_d6rp_nav; self._d7_con_y24=self._d7_con_nav
        if _D8_AVAIL: _D8Update(self, tf_sym, spy20)

    # Monthly boundary
    mk = today.strftime("%Y-%m")
    if self._d6_month_key is None:
        self._d6_month_key = mk
        self._d6_mnav_lf_start = self._d6_lf_nav
        self._d6_mnav_tf_start = self._d6_tf_nav
    elif mk != self._d6_month_key:
        RRXD6EmitMonthly(self)
        self._d6_month_key = mk
        self._d6_mnav_lf_start = self._d6_lf_nav
        self._d6_mnav_tf_start = self._d6_tf_nav

    if getattr(self, "rrx_d6_log_daily", False):
        try:    lv = lf_sym.Value if lf_sym else "NONE"
        except: lv = str(lf_sym) if lf_sym else "NONE"
        try:    tv = tf_sym.Value if tf_sym else "NONE"
        except: tv = str(tf_sym) if tf_sym else "NONE"
        bucket_s = _RRXD6ThemeBucket(lf_theme) if lf_theme else "NONE"
        self.log(
            f"RRX_D6_DAY,{today},"
            f"lf_nav={self._d6_lf_nav:.4f},tf_nav={self._d6_tf_nav:.4f},"
            f"hard_nav={self._d6rm_hard_nav:.4f},soft_nav={self._d6rm_soft_nav:.4f},"
            f"lf_sym={lv},lf_th={lf_theme or 'NONE'},bucket={bucket_s},"
            f"tf_sym={tv},same={int(same)}"
        )


# ---------------------------------------------------------------------------
# Worst-5% helper
# ---------------------------------------------------------------------------

def _RRXD6Worst5Pct(self, rets: list) -> float:
    if not rets: return 0.0
    n = max(1, int(len(rets) * 0.05))
    return float(sum(sorted(rets)[:n]) / n)


# ---------------------------------------------------------------------------
# Monthly
# ---------------------------------------------------------------------------

def RRXD6EmitMonthly(self, today=None) -> None:
    if not getattr(self, "rrx_d6_log_monthly", False): return
    if today is None: today = self.time.date()
    base_lf = max(self._d6_mnav_lf_start, 1e-9)
    base_tf = max(self._d6_mnav_tf_start, 1e-9)
    try:    lv = self._d6_lf_held.Value if self._d6_lf_held else "NONE"
    except: lv = str(self._d6_lf_held) if self._d6_lf_held else "NONE"
    try:    tv = self._d6_tf_held.Value if self._d6_tf_held else "NONE"
    except: tv = str(self._d6_tf_held) if self._d6_tf_held else "NONE"
    map_part = ""
    if getattr(self, "rrx_d6_bucket_regime_en", False):
        map_part = (
            f",hard_nav={self._d6rm_hard_nav:.4f}"
            f",soft_nav={self._d6rm_soft_nav:.4f}"
            f",tail_nav={self._d6rm_tail_nav:.4f}"
            f",tail50_nav={self._d6rm_tail50_nav:.4f}"
            f",hard_dd={self._d6rm_hard_maxdd:.4f}"
            f",soft_dd={self._d6rm_soft_maxdd:.4f}"
            f",tail_dd={self._d6rm_tail_maxdd:.4f}"
            f",tail50_dd={self._d6rm_tail50_maxdd:.4f}"
            f",tail_blk_env={self._d6rm_tail_blk_env}"
            f",tail_blk_rsi={self._d6rm_tail_blk_rsi}"
        )
    self.log(
        f"RRX_D6R_MONTH,{self._d6_month_key},{today},"
        f"lf_nav={self._d6_lf_nav:.4f},tf_nav={self._d6_tf_nav:.4f},"
        f"lf_mret={self._d6_lf_nav/base_lf-1:+.4f},"
        f"tf_mret={self._d6_tf_nav/base_tf-1:+.4f},"
        f"lf_dd={self._d6_lf_maxdd:.4f},tf_dd={self._d6_tf_maxdd:.4f},"
        f"lf_sym={lv},tf_sym={tv},"
        f"same={self._d6_same_days},"
        f"lf_turn={self._d6_lf_turn},tf_turn={self._d6_tf_turn}"
        + map_part
    )


# ---------------------------------------------------------------------------
# Final
# ---------------------------------------------------------------------------

def RRXD6EmitFinal(self) -> None:
    """[D6/D6R/D6R-MAP] Full-run summary with period breakdown."""
    today = self.time.date()
    RRXD6EmitMonthly(self, today)
    w5_lf = _RRXD6Worst5Pct(self, self._d6_lf_rets)
    w5_tf = _RRXD6Worst5Pct(self, self._d6_tf_rets)
    start = str(self._d6_start_date or "NA")
    self.log(
        f"RRX_D6_FINAL,start={start},end={today},"
        f"lf_nav={self._d6_lf_nav:.4f},tf_nav={self._d6_tf_nav:.4f},"
        f"lf_maxdd={self._d6_lf_maxdd:.4f},tf_maxdd={self._d6_tf_maxdd:.4f},"
        f"lf_w5={w5_lf:+.5f},tf_w5={w5_tf:+.5f},"
        f"lf_days={self._d6_lf_days},tf_days={self._d6_tf_days},"
        f"same={self._d6_same_days},"
        f"lf_better={self._d6_lf_better},tf_better={self._d6_tf_better},"
        f"lf_turn={self._d6_lf_turn},tf_turn={self._d6_tf_turn},"
        f"is_lf={self._d6_nav_is_lf:.4f},is_tf={self._d6_nav_is_tf:.4f},"
        f"oos_lf={self._d6_nav_oos_lf:.4f},oos_tf={self._d6_nav_oos_tf:.4f},"
        f"cris_lf={self._d6_nav_cris_lf:.4f},cris_tf={self._d6_nav_cris_tf:.4f},"
        f"y20_lf={self._d6_nav_y20_lf:.4f},y20_tf={self._d6_nav_y20_tf:.4f},"
        f"y22_lf={self._d6_nav_y22_lf:.4f},y22_tf={self._d6_nav_y22_tf:.4f},"
        f"y23_lf={self._d6_nav_y23_lf:.4f},y23_tf={self._d6_nav_y23_tf:.4f},"
        f"y24_lf={self._d6_nav_y24_lf:.4f},y24_tf={self._d6_nav_y24_tf:.4f}"
    )
    if getattr(self, "rrx_d6_risk_enable", False):
        w5_d6r = _RRXD6Worst5Pct(self, self._d6r_rets)
        self.log(
            f"RRX_D6R_FINAL,start={start},end={today},"
            f"d6r_nav={self._d6r_nav:.4f},lf_nav={self._d6_lf_nav:.4f},tf_nav={self._d6_tf_nav:.4f},"
            f"d6r_maxdd={self._d6r_maxdd:.4f},lf_maxdd={self._d6_lf_maxdd:.4f},tf_maxdd={self._d6_tf_maxdd:.4f},"
            f"d6r_w5={w5_d6r:+.5f},lf_w5={w5_lf:+.5f},tf_w5={w5_tf:+.5f},"
            f"d6r_days={self._d6r_days},d6r_turn={self._d6r_turn},"
            f"blk_panic={self._d6r_blk_panic},blk_ids={self._d6r_blk_ids},blk_dd={self._d6r_blk_dd},"
            f"is={self._d6r_nav_is:.4f},oos={self._d6r_nav_oos:.4f},cris={self._d6r_nav_cris:.4f},"
            f"y20={self._d6r_nav_y20:.4f},y22={self._d6r_nav_y22:.4f},"
            f"y23={self._d6r_nav_y23:.4f},y24={self._d6r_nav_y24:.4f}"
        )
    if _D8_AVAIL: _D8EmitFinal(self, start, today)
    # D7 router final emit
    if getattr(self, "rrx_d7_router_enable", False):
        w5_tfp  = _RRXD6Worst5Pct(self, self._d7_tfp_rets)
        w5_d6rp = _RRXD6Worst5Pct(self, self._d7_d6rp_rets)
        w5_con  = _RRXD6Worst5Pct(self, self._d7_con_rets)
        self.log(
            f"RRX_D7_DIFF_FINAL,start={start},end={today},"
            f"same_sym={self._d7_same_sym},same_bkt={self._d7_same_bkt},"
            f"diff={self._d7_diff},tf_win={self._d7_tf_win},d6r_win={self._d7_d6r_win},"
            f"tfp_nav={self._d7_tfp_nav:.4f},tfp_maxdd={self._d7_tfp_maxdd:.4f},tfp_w5={w5_tfp:+.5f},"
            f"d6rp_nav={self._d7_d6rp_nav:.4f},d6rp_maxdd={self._d7_d6rp_maxdd:.4f},d6rp_w5={w5_d6rp:+.5f},"
            f"con_nav={self._d7_con_nav:.4f},con_maxdd={self._d7_con_maxdd:.4f},con_w5={w5_con:+.5f},"
            f"tfp_oos={self._d7_tfp_oos:.4f},tfp_cris={self._d7_tfp_cris:.4f},"
            f"tfp_y22={self._d7_tfp_y22:.4f},tfp_y23={self._d7_tfp_y23:.4f},tfp_y24={self._d7_tfp_y24:.4f},"
            f"d6rp_oos={self._d7_d6rp_oos:.4f},d6rp_cris={self._d7_d6rp_cris:.4f},"
            f"d6rp_y22={self._d7_d6rp_y22:.4f},d6rp_y23={self._d7_d6rp_y23:.4f},d6rp_y24={self._d7_d6rp_y24:.4f},"
            f"con_oos={self._d7_con_oos:.4f},con_cris={self._d7_con_cris:.4f},"
            f"con_y22={self._d7_con_y22:.4f},con_y23={self._d7_con_y23:.4f},con_y24={self._d7_con_y24:.4f}"
        )
    if getattr(self, "rrx_d6_bucket_regime_en", False):
        # Worst-25 tail days audit
        audit = getattr(self, "_d6rm_audit", [])
        if audit:
            worst25 = sorted(audit, key=lambda x: x["ret"])[:25]
            for e in worst25:
                self.log(
                    f"RRX_D6RM_WORST,"
                    f"date={e['date']},sym={e['sym']},theme={e['theme']},"
                    f"bucket={e['bucket']},decision={e['decision']},"
                    f"ret={e['ret']:+.5f},rsi={e['rsi']},"
                    f"spy20={e['spy20']:+.4f},"
                    f"ps={e['ps']},ids={e['ids']},regime={e['regime']}"
                )
        w5_spyg2  = _RRXD6Worst5Pct(self, self._d6rm_spyg2_rets)
        w5_spyg   = _RRXD6Worst5Pct(self, self._d6rm_spyg_rets)
        w5_riskon = _RRXD6Worst5Pct(self, self._d6rm_riskon_rets)
        w5_hard  = _RRXD6Worst5Pct(self, self._d6rm_hard_rets)
        w5_soft  = _RRXD6Worst5Pct(self, self._d6rm_soft_rets)
        w5_tail  = _RRXD6Worst5Pct(self, self._d6rm_tail_rets)
        w5_tail50 = _RRXD6Worst5Pct(self, self._d6rm_tail50_rets)
        w5_soft = _RRXD6Worst5Pct(self, self._d6rm_soft_rets)
        w5_tail = _RRXD6Worst5Pct(self, self._d6rm_tail_rets)
        self.log(
            f"RRX_D6RM_FINAL,start={start},end={today},"
            f"hard_nav={self._d6rm_hard_nav:.4f},soft_nav={self._d6rm_soft_nav:.4f},"
            f"tail_nav={self._d6rm_tail_nav:.4f},tail50_nav={self._d6rm_tail50_nav:.4f},"
            f"tail50_mode=ADAPT,"
            f"lf_nav={self._d6_lf_nav:.4f},tf_nav={self._d6_tf_nav:.4f},"
            f"hard_maxdd={self._d6rm_hard_maxdd:.4f},soft_maxdd={self._d6rm_soft_maxdd:.4f},"
            f"tail_maxdd={self._d6rm_tail_maxdd:.4f},tail50_maxdd={self._d6rm_tail50_maxdd:.4f},"
            f"lf_maxdd={self._d6_lf_maxdd:.4f},tf_maxdd={self._d6_tf_maxdd:.4f},"
            f"hard_w5={w5_hard:+.5f},soft_w5={w5_soft:+.5f},"
            f"tail_w5={w5_tail:+.5f},tail50_w5={w5_tail50:+.5f},"
            f"lf_w5={w5_lf:+.5f},tf_w5={w5_tf:+.5f},"
            f"hard_days={self._d6rm_hard_days},hard_turn={self._d6rm_hard_turn},"
            f"soft_days={self._d6rm_soft_days},soft_turn={self._d6rm_soft_turn},"
            f"tail_days={self._d6rm_tail_days},tail_turn={self._d6rm_tail_turn},"
            f"tail50_days={self._d6rm_tail50_days},tail50_turn={self._d6rm_tail50_turn},"
            f"tail_blk_env={self._d6rm_tail_blk_env},tail_blk_rsi={self._d6rm_tail_blk_rsi},"
            f"blk_spy={self._d6rm_blk_spy},blk_def={self._d6rm_blk_def},"
            f"blk_gold={self._d6rm_blk_gold},blk_comm={self._d6rm_blk_comm},blk_hv={self._d6rm_blk_hv},"
            f"soft_spy={self._d6rm_soft_spy},soft_def={self._d6rm_soft_def},"
            f"soft_gold={self._d6rm_soft_gold},soft_comm={self._d6rm_soft_comm},soft_hv={self._d6rm_soft_hv},"
            f"hard_is={self._d6rm_hard_nav_is:.4f},hard_oos={self._d6rm_hard_nav_oos:.4f},"
            f"hard_cris={self._d6rm_hard_nav_cris:.4f},"
            f"hard_y20={self._d6rm_hard_nav_y20:.4f},hard_y22={self._d6rm_hard_nav_y22:.4f},"
            f"hard_y23={self._d6rm_hard_nav_y23:.4f},hard_y24={self._d6rm_hard_nav_y24:.4f},"
            f"soft_is={self._d6rm_soft_nav_is:.4f},soft_oos={self._d6rm_soft_nav_oos:.4f},"
            f"soft_cris={self._d6rm_soft_nav_cris:.4f},"
            f"soft_y20={self._d6rm_soft_nav_y20:.4f},soft_y22={self._d6rm_soft_nav_y22:.4f},"
            f"soft_y23={self._d6rm_soft_nav_y23:.4f},soft_y24={self._d6rm_soft_nav_y24:.4f},"
            f"tail_is={self._d6rm_tail_nav_is:.4f},tail_oos={self._d6rm_tail_nav_oos:.4f},"
            f"tail_cris={self._d6rm_tail_nav_cris:.4f},"
            f"tail_y20={self._d6rm_tail_nav_y20:.4f},tail_y22={self._d6rm_tail_nav_y22:.4f},"
            f"tail_y23={self._d6rm_tail_nav_y23:.4f},tail_y24={self._d6rm_tail_nav_y24:.4f},"
            f"tail50_is={self._d6rm_tail50_nav_is:.4f},tail50_oos={self._d6rm_tail50_nav_oos:.4f},"
            f"tail50_cris={self._d6rm_tail50_nav_cris:.4f},"
            f"tail50_y20={self._d6rm_tail50_nav_y20:.4f},tail50_y22={self._d6rm_tail50_nav_y22:.4f},"
            f"tail50_y23={self._d6rm_tail50_nav_y23:.4f},tail50_y24={self._d6rm_tail50_nav_y24:.4f},"
            f"spyg2_nav={self._d6rm_spyg2_nav:.4f},"
            f"spyg2_maxdd={self._d6rm_spyg2_maxdd:.4f},"
            f"spyg2_w5={w5_spyg2:+.5f},"
            f"spyg2_days={self._d6rm_spyg2_days},"
            f"spyg2_turn={self._d6rm_spyg2_turn},"
            f"spyg2_block={self._d6rm_spyg2_block},"
            f"spyg2_is={self._d6rm_spyg2_nav_is:.4f},"
            f"spyg2_oos={self._d6rm_spyg2_nav_oos:.4f},"
            f"spyg2_cris={self._d6rm_spyg2_nav_cris:.4f},"
            f"spyg2_y20={self._d6rm_spyg2_nav_y20:.4f},"
            f"spyg2_y22={self._d6rm_spyg2_nav_y22:.4f},"
            f"spyg2_y23={self._d6rm_spyg2_nav_y23:.4f},"
            f"spyg2_y24={self._d6rm_spyg2_nav_y24:.4f},"
            f"spyg_nav={self._d6rm_spyg_nav:.4f},"
            f"spyg_maxdd={self._d6rm_spyg_maxdd:.4f},"
            f"spyg_w5={w5_spyg:+.5f},"
            f"spyg_days={self._d6rm_spyg_days},"
            f"spyg_turn={self._d6rm_spyg_turn},"
            f"spyg_block={self._d6rm_spyg_block},"
            f"spyg_is={self._d6rm_spyg_nav_is:.4f},"
            f"spyg_oos={self._d6rm_spyg_nav_oos:.4f},"
            f"spyg_cris={self._d6rm_spyg_nav_cris:.4f},"
            f"spyg_y20={self._d6rm_spyg_nav_y20:.4f},"
            f"spyg_y22={self._d6rm_spyg_nav_y22:.4f},"
            f"spyg_y23={self._d6rm_spyg_nav_y23:.4f},"
            f"spyg_y24={self._d6rm_spyg_nav_y24:.4f},"
            f"riskon_nav={self._d6rm_riskon_nav:.4f},"
            f"riskon_maxdd={self._d6rm_riskon_maxdd:.4f},"
            f"riskon_w5={w5_riskon:+.5f},"
            f"riskon_days={self._d6rm_riskon_days},"
            f"riskon_turn={self._d6rm_riskon_turn},"
            f"riskon_block={self._d6rm_riskon_block},"
            f"riskon_is={self._d6rm_riskon_nav_is:.4f},"
            f"riskon_oos={self._d6rm_riskon_nav_oos:.4f},"
            f"riskon_cris={self._d6rm_riskon_nav_cris:.4f},"
            f"riskon_y20={self._d6rm_riskon_nav_y20:.4f},"
            f"riskon_y22={self._d6rm_riskon_nav_y22:.4f},"
            f"riskon_y23={self._d6rm_riskon_nav_y23:.4f},"
            f"riskon_y24={self._d6rm_riskon_nav_y24:.4f}"
        )