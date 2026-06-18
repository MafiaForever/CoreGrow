# rrx_d8_switch_diag.py
# Tags: [RRX][D8]
# D8 BRANCH SWITCH diagnostic: TF_PRIMARY vs SPYG vs CASH.
# D10_COMPARE_C1 diagnostic: ETF-dispersion SPYG baseline + boost variants, per-leg readiness.
# D9_DISP diagnostic: SPYG vs SPYG2 vs CASH router.
# Diagnostic only. Zero trading impact.

from AlgorithmImports import *


def _d8_roll_ret(rets: list, n: int) -> float:
    """Compounded return over last n days."""
    if len(rets) < n: return 0.0
    nav = 1.0
    for x in rets[-n:]: nav *= (1.0 + x)
    return nav - 1.0


def _d8_roll_dd(rets: list, n: int) -> float:
    """Max drawdown over last n days."""
    if len(rets) < n: return 0.0
    nav = peak = 1.0; maxdd = 0.0
    for x in rets[-n:]:
        nav = max(0.01, nav * (1.0 + x))
        if nav > peak: peak = nav
        dd = 1.0 - nav / max(peak, 1e-9)
        if dd > maxdd: maxdd = dd
    return maxdd


def _d8_curve_blank():
    return {"nav": 1.0, "peak": 1.0, "dd": 0.0, "rets": []}


def _d8_curve_update(c, r: float) -> None:
    c["nav"] = max(0.01, float(c["nav"]) * (1.0 + float(r)))
    if c["nav"] > c["peak"]:
        c["peak"] = c["nav"]
    dd = 1.0 - c["nav"] / max(c["peak"], 1e-9)
    if dd > c["dd"]:
        c["dd"] = dd
    c["rets"].append(float(r))
    if len(c["rets"]) > 4000:
        c["rets"] = c["rets"][-2000:]


def _d8_worst5(rets) -> float:
    if not rets:
        return 0.0
    n5 = max(1, int(len(rets) * 0.05))
    return float(sum(sorted(rets)[:n5]) / n5)


def _d8_srprem_blank():
    return {
        "base": _d8_curve_blank(),
        "c": [_d8_curve_blank(), _d8_curve_blank(), _d8_curve_blank(), _d8_curve_blank(), _d8_curve_blank()],
        "ord": [0, 0, 0, 0, 0],
        "lock": [0, 0, 0, 0, 0],
        "cap080": [0, 0, 0, 0, 0],
        "cap090": [0, 0, 0, 0, 0],
        "cap100": [0, 0, 0, 0, 0],
        "cap_sum": [0.0, 0.0, 0.0, 0.0, 0.0],
        "days": 0, "ready": 0, "not_ready": 0,
        "entry_near_sup": 0, "entry_near_res": 0, "entry_far_sup": 0,
        "entry_good_rr": 0, "entry_bad_rr": 0,
    }


def _d8_sr_ctx(self, sym):
    if sym is None:
        return None
    try:
        lb = int(getattr(self, "rrx_sat_sr_lookback", 120))
        k = int(getattr(self, "rrx_sat_sr_k", 5))
        ns = float(getattr(self, "rrx_sat_sr_near_sup_atr", 1.0))
        nr = float(getattr(self, "rrx_sat_sr_near_res_atr", 1.25))
        ba = float(getattr(self, "rrx_sat_sr_break_atr", 0.25))
        h = self.history(sym, lb + 5, Resolution.DAILY)
        if h is None or h.empty or "close" not in h.columns:
            return None
        c = list(h["close"].astype(float))
        hi = list(h["high"].astype(float)) if "high" in h.columns else c
        lo = list(h["low"].astype(float)) if "low" in h.columns else c
        if len(c) < 40:
            return None
        px = float(c[-1]); p0 = float(c[-2])
        vals = []
        for i in range(1, len(c) - 1):
            if hi[i] >= hi[i - 1] and hi[i] >= hi[i + 1]:
                vals.append(float(hi[i]))
            if lo[i] <= lo[i - 1] and lo[i] <= lo[i + 1]:
                vals.append(float(lo[i]))
        if len(vals) < k:
            step = max(1, int(len(c) / max(1, k * 3)))
            vals += [float(x) for x in c[::step]]
        vals = sorted([v for v in vals if v > 0.0])
        if px <= 0.0 or p0 <= 0.0 or len(vals) < 3:
            return None
        k = max(2, min(k, len(vals)))
        centers = [vals[min(len(vals) - 1, int((i + 0.5) * len(vals) / k))] for i in range(k)]
        for _ in range(6):
            groups = [[] for _ in centers]
            for v in vals:
                j = min(range(len(centers)), key=lambda z: abs(v - centers[z]))
                groups[j].append(v)
            nc = [(sum(g) / len(g) if g else centers[i]) for i, g in enumerate(groups)]
            if max(abs(nc[i] - centers[i]) for i in range(len(centers))) < 1e-6:
                break
            centers = nc
        centers = sorted(centers)
        trs = [max(hi[i] - lo[i], abs(hi[i] - c[i-1]), abs(lo[i] - c[i-1])) for i in range(1, len(c))]
        atr = max(0.01, sum(trs[-14:]) / max(1, len(trs[-14:]))) if trs else max(0.01, px * 0.02)
        sup = max([x for x in centers if x < px], default=0.0)
        res = min([x for x in centers if x > px], default=0.0)
        sup0 = max([x for x in centers if x < p0], default=0.0)
        ds = (px - sup) / atr if sup > 0.0 else 99.0
        dr = (res - px) / atr if res > 0.0 else 99.0
        return {
            "px": px,
            "near_sup": ds <= ns,
            "near_res": dr <= nr,
            "support_lost": bool(sup0 > 0.0 and p0 >= sup0 and px < sup0 - ba * atr),
            "breakout": bool(sup > 0.0 and p0 <= sup + ba * atr and px > sup + ba * atr),
            "dist_sup_atr": ds,
            "room_res_atr": dr,
        }
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Initialize
# ---------------------------------------------------------------------------

def D8SwitchDiagInitialize(self) -> None:
    """[D8] Initialize branch switcher shadow curve."""
    def _gb(k, d):
        ov = getattr(self, "_rrx_param_overrides", {}) or {}
        v  = self.get_parameter(k)
        if v is None or str(v).strip() == "":
            v = ov.get(k)
        return bool(int(v)) if v is not None else bool(d)
    def _gf(k, d):
        ov = getattr(self, "_rrx_param_overrides", {}) or {}
        v = self.get_parameter(k)
        if v is None or str(v).strip() == "":
            v = ov.get(k)
        try:
            return float(v) if v is not None else float(d)
        except Exception:
            return float(d)
    self.rrx_d8_log_monthly = _gb("rrx_d8_log_monthly", 0)
    # Shadow NAV
    self._d8_sw_nav  = 1.0; self._d8_sw_peak = 1.0; self._d8_sw_maxdd = 0.0
    self._d8_sw_held = None; self._d8_sw_held_px = 0.0; self._d8_sw_weight = 0.0
    self._d8_sw_rets: list = []
    self._d8_sw_days = 0; self._d8_sw_turn = 0
    # Branch counters
    self._d8_use_tfp  = 0; self._d8_use_spyg = 0
    self._d8_use_tail = 0; self._d8_use_cash = 0
    self._d8_use_spyg_alpha = 0; self._d8_use_spyg_risk = 0
    self._d8_risk_candidate  = 0; self._d8_risk_fail_active = 0
    self._d8_risk_fail_dd    = 0; self._d8_risk_fail_ret    = 0
    # Period snapshots
    self._d8_sw_oos  = 1.0; self._d8_sw_cris = 1.0
    self._d8_sw_y20  = 1.0; self._d8_sw_y22  = 1.0
    self._d8_sw_y23  = 1.0; self._d8_sw_y24  = 1.0
    # D8C BLEND: 70% TFP + 30% SPYG (50/50 when tfp_dd20 >= 8%)
    self._d8_bl_nav   = 1.0; self._d8_bl_peak = 1.0; self._d8_bl_maxdd = 0.0
    self._d8_bl_tfp_held = None; self._d8_bl_tfp_px = 0.0
    self._d8_bl_spyg_held = None; self._d8_bl_spyg_px = 0.0
    self._d8_bl_tfp_w = 0.0; self._d8_bl_spyg_w = 0.0
    self._d8_bl_rets: list = []
    self._d8_bl_days = 0; self._d8_bl_turn = 0
    self._d8_bl_oos  = 1.0; self._d8_bl_cris = 1.0
    self._d8_bl_y20  = 1.0; self._d8_bl_y22  = 1.0
    self._d8_bl_y23  = 1.0; self._d8_bl_y24  = 1.0
    # ALLOC-SPYG-SAT-D0: base portfolio vs configurable SPYG satellite cap.
    # Default cap must match trading SPYG_SAT_T2: 20%.
    self._d8_sat_cap = max(
        0.0,
        min(_gf("spyg_sat_cap_max", 0.20), _gf("spyg_sat_cap", 0.20))
    )
    self._d8_sat_active_only_scale = _gb("spyg_sat_active_only_scale", 1)
    self._d8_sat_prev_sigw = 0.0

    self._d8_prev_pv    = 0.0
    self._d8_base_nav   = 1.0; self._d8_base_peak  = 1.0; self._d8_base_maxdd  = 0.0
    self._d8_sat20_nav  = 1.0; self._d8_sat20_peak = 1.0; self._d8_sat20_maxdd = 0.0
    self._d8_base_rets: list = []; self._d8_sat20_rets: list = []
    self._d8_base_oos  = 1.0; self._d8_base_cris = 1.0
    self._d8_base_y20  = 1.0; self._d8_base_y22  = 1.0
    self._d8_base_y23  = 1.0; self._d8_base_y24  = 1.0
    self._d8_sat20_oos  = 1.0; self._d8_sat20_cris = 1.0
    self._d8_sat20_y20  = 1.0; self._d8_sat20_y22  = 1.0
    self._d8_sat20_y23  = 1.0; self._d8_sat20_y24  = 1.0
    self._d8_diag_windows = []
    self._d8_alloc_win = {}
    _wparam = self.get_parameter("rrx_eqwin_windows")
    if _wparam is None or str(_wparam).strip() == "":
        _wparam = (getattr(self, "_rrx_param_overrides", {}) or {}).get("rrx_eqwin_windows")
    if _wparam is None or str(_wparam).strip() == "":
        _wparam = self.get_parameter("rrx_sat_rate_cd_windows")
    if _wparam is None or str(_wparam).strip() == "":
        _wparam = (getattr(self, "_rrx_param_overrides", {}) or {}).get("rrx_sat_rate_cd_windows")
    _wdef = str(_wparam or (
        "W22:2021-10-01:2022-12-31|"
        "W22_Q1:2022-01-01:2022-03-31|"
        "W22_Q2:2022-04-01:2022-06-30|"
        "W22_Q3:2022-07-01:2022-09-30|"
        "W22_Q4:2022-10-01:2022-12-31|"
        "W23_24:2023-06-01:2024-04-01|"
        "W25_26:2025-01-01:2026-05-23"
    ))
    for _chunk in _wdef.split("|"):
        _p = [x.strip() for x in _chunk.split(":")]
        if len(_p) == 3 and _p[0] and _p[1] and _p[2]:
            self._d8_diag_windows.append((_p[0], _p[1], _p[2]))
            self._d8_alloc_win[_p[0]] = {"base": _d8_curve_blank(), "sat20": _d8_curve_blank()}
    # SAT_SR_SUPPORT_PREMIUM_COMPARE_D0: support permits full baseline cap.
    self.rrx_sat_sr_support_premium_enable = _gb("rrx_sat_sr_support_premium_enable", 0)
    self.rrx_sat_sr_lookback = int(_gf("rrx_sat_sr_lookback", 120))
    self.rrx_sat_sr_k = int(_gf("rrx_sat_sr_k", 5))
    self.rrx_sat_sr_near_sup_atr = float(_gf("rrx_sat_sr_near_sup_atr", 1.00))
    self.rrx_sat_sr_near_res_atr = float(_gf("rrx_sat_sr_near_res_atr", 1.25))
    self.rrx_sat_sr_break_atr = float(_gf("rrx_sat_sr_break_atr", 0.25))
    self._srprem_run = _d8_srprem_blank()
    self._srprem_win = {x[0]: _d8_srprem_blank() for x in self._d8_diag_windows}
    self._srprem_sym = None
    self._srprem_mult = [1.0, 1.0, 1.0, 1.0, 1.0]
    self._srprem_prev_sigw = [0.0, 0.0, 0.0, 0.0, 0.0]
    # D8D: SPYG primary, TFP booster only when safer
    self._d8d_nav  = 1.0; self._d8d_peak = 1.0; self._d8d_maxdd = 0.0
    self._d8d_tfp_held  = None; self._d8d_tfp_px  = 0.0
    self._d8d_spyg_held = None; self._d8d_spyg_px = 0.0
    self._d8d_tfp_w = 0.0; self._d8d_spyg_w = 0.0
    self._d8d_rets: list = []
    self._d8d_days = 0; self._d8d_turn = 0
    self._d8d_use_spyg = 0; self._d8d_use_boost = 0; self._d8d_use_cash = 0
    self._d8d_oos  = 1.0; self._d8d_cris = 1.0
    self._d8d_y20  = 1.0; self._d8d_y22  = 1.0
    self._d8d_y23  = 1.0; self._d8d_y24  = 1.0
    # D9-DISP-GATE-D0: SPYG/SPYG2/CASH router
    self.rrx_d9_disp_enable = _gb("rrx_d9_disp_enable", 1)
    self._d9_disp_nav=1.0; self._d9_disp_peak=1.0; self._d9_disp_maxdd=0.0
    self._d9_disp_rets=[]; self._d9_disp_branch="CASH"
    self._d9_disp_days=0; self._d9_disp_turn=0
    self._d9_use_spyg=0; self._d9_use_spyg2=0; self._d9_use_cash=0
    self._d9_disp_oos=1.0; self._d9_disp_cris=1.0
    self._d9_disp_y20=1.0; self._d9_disp_y22=1.0
    self._d9_disp_y23=1.0; self._d9_disp_y24=1.0
    # D10_COMPARE_C1: ETF-dispersion SPYG baseline + boost variants
    self.rrx_d10_compare_enable = _gb("rrx_d10_compare_enable", 1)
    self._d10_etfs = ["SPY","QQQ","SMH","XLE","XLV","XLU","XLB","XLI","XLF","GLDM","DBC"]
    self._d10_px_prev = {}
    self._d10_etf_rets = {}
    for _t in self._d10_etfs:
        self._d10_etf_rets[_t] = []
    self._d10_nav=[1.0,1.0,1.0,1.0]
    self._d10_peak=[1.0,1.0,1.0,1.0]
    self._d10_maxdd=[0.0,0.0,0.0,0.0]
    self._d10_rets=[[],[],[],[]]
    self._d10_w=[(0.0,0.0,0.0),(0.0,0.0,0.0),(0.0,0.0,0.0),(0.0,0.0,0.0)]
    self._d10_mode=["INIT","INIT","INIT","INIT"]
    self._d10_days=[0,0,0,0]
    self._d10_turn=[0,0,0,0]
    self._d10_core=[0,0,0,0]
    self._d10_s2=[0,0,0,0]
    self._d10_tfp=[0,0,0,0]
    self._d10_cash=[0,0,0,0]
    self._d10_disp_sum=0.0; self._d10_disp_n=0
    self._d10_disp_min=999.0; self._d10_disp_max=0.0
    self._d10_ready_sum=0; self._d10_rg_unknown=0; self._d10_cls_unknown=0
    self._d10_spyg_ready=0; self._d10_s2_ready=0; self._d10_tfp_ready=0
    # Monthly state
    self._d8_month_key    = None
    self._d8_mnav_start   = 1.0
    self._d8_month_branch = "NONE"
    self.log("RRX_D8_INIT,branch_switch_diag,diag_only=1,no_trading=1")


# ---------------------------------------------------------------------------
# Daily update
# ---------------------------------------------------------------------------

def D8SwitchDiagUpdate(self, tf_sym, spy20: float) -> None:
    """[D8] Daily branch decision + shadow NAV update."""
    today = self.time.date()
    yr    = today.year
    today_s = today.isoformat()
    win_today = []
    for _wn, _st, _en in getattr(self, "_d8_diag_windows", []):
        if _st <= today_s <= _en:
            win_today.append(_wn)

    # Rolling lagged metrics from existing curves
    tfp_rets   = getattr(self, "_d7_tfp_rets",     [])
    spyg_rets  = getattr(self, "_d6rm_spyg_rets",  [])
    spyg2_rets = getattr(self, "_d6rm_spyg2_rets", [])

    tfp_r20   = _d8_roll_ret(tfp_rets,   20)
    spyg_r20  = _d8_roll_ret(spyg_rets,  20)
    spyg2_r20 = _d8_roll_ret(spyg2_rets, 20)
    tfp_dd20  = _d8_roll_dd(tfp_rets,    20)
    spyg_dd20 = _d8_roll_dd(spyg_rets,   20)
    spyg2_dd20= _d8_roll_dd(spyg2_rets,  20)

    ps  = str(getattr(self, "_panic_state",  "NORMAL"))
    ids = str(getattr(self, "_ids_state",    "NORMAL"))
    hard_stress = (ps  in ("STRESS", "PANIC") or
                   ids in ("STRESS", "PANIC", "PANIC_SHORT"))

    spyg_w_now  = float(getattr(self, "_d6rm_spyg_weight", 0.0) or 0.0)
    spyg_active = (spyg_w_now > 0 and
                   len(spyg_rets) >= 20 and len(tfp_rets) >= 20 and
                   spy20 > 0)

    # D9-DISP: apply previous branch return, then choose next branch
    if getattr(self, "rrx_d9_disp_enable", True):
        d9_prev = str(getattr(self, "_d9_disp_branch", "CASH"))
        d9_ret = 0.0
        if d9_prev == "SPYG" and spyg_rets: d9_ret = float(spyg_rets[-1])
        elif d9_prev == "SPYG2" and spyg2_rets: d9_ret = float(spyg2_rets[-1])
        self._d9_disp_nav = max(0.01, self._d9_disp_nav * (1.0 + d9_ret))
        if self._d9_disp_nav > self._d9_disp_peak: self._d9_disp_peak = self._d9_disp_nav
        d9dd = 1.0 - self._d9_disp_nav / max(self._d9_disp_peak, 1e-9)
        if d9dd > self._d9_disp_maxdd: self._d9_disp_maxdd = d9dd
        self._d9_disp_rets.append(d9_ret)
        if len(self._d9_disp_rets) > 4000: self._d9_disp_rets = self._d9_disp_rets[-2000:]
        if d9_prev != "CASH": self._d9_disp_days += 1
        d9_ready = len(spyg_rets) >= 20 and len(spyg2_rets) >= 20
        d9_disp = spyg2_r20 - spyg_r20
        if hard_stress or not d9_ready:
            d9_next = "CASH"
        elif spyg2_r20 > 0 and d9_disp >= 0.01 and spyg2_dd20 <= spyg_dd20 + 0.015:
            d9_next = "SPYG2"
        elif spyg_r20 > 0 and spyg_dd20 <= 0.08:
            d9_next = "SPYG"
        else:
            d9_next = "CASH"
        if d9_next != d9_prev and d9_prev != "CASH": self._d9_disp_turn += 1
        self._d9_disp_branch = d9_next
        if d9_next == "SPYG2": self._d9_use_spyg2 += 1
        elif d9_next == "SPYG": self._d9_use_spyg += 1
        else: self._d9_use_cash += 1
        if 2019 <= yr <= 2021: self._d9_disp_oos  = self._d9_disp_nav
        if 2022 <= yr <= 2025: self._d9_disp_cris = self._d9_disp_nav
        if yr == 2020: self._d9_disp_y20 = self._d9_disp_nav
        if yr == 2022: self._d9_disp_y22 = self._d9_disp_nav
        if yr == 2023: self._d9_disp_y23 = self._d9_disp_nav
        if yr == 2024: self._d9_disp_y24 = self._d9_disp_nav

    # D10_COMPARE_C1: ETF-dispersion SPYG baseline + boost variants with per-leg readiness
    if getattr(self, "rrx_d10_compare_enable", True):
        # Explicit capture of outer scope - avoids any Python closure scoping issue
        _d10_hs  = bool(hard_stress)
        _d10_sp  = list(spyg_rets)
        _d10_sp2 = list(spyg2_rets)
        _d10_tf  = list(tfp_rets)

        def _d10_find_sym(_ticker):
            try:
                for _s in self.securities.keys():
                    if str(getattr(_s, "value", _s)).upper() == _ticker:
                        return _s
            except Exception:
                pass
            return None

        d10_rocs = []
        d10_ready = 0
        for _t in self._d10_etfs:
            _sym = _d10_find_sym(_t)
            if _sym is None: continue
            try:
                _px = float(self.securities[_sym].price)
            except Exception:
                _px = 0.0
            if _px <= 0: continue
            _prev = self._d10_px_prev.get(_t, None)
            if _prev is not None and _prev > 0:
                _r = _px / _prev - 1.0
                self._d10_etf_rets[_t].append(_r)
                if len(self._d10_etf_rets[_t]) > 80:
                    self._d10_etf_rets[_t] = self._d10_etf_rets[_t][-60:]
            self._d10_px_prev[_t] = _px
            if len(self._d10_etf_rets[_t]) >= 20:
                d10_rocs.append(_d8_roll_ret(self._d10_etf_rets[_t], 20))
                d10_ready += 1

        if len(d10_rocs) >= 4:
            _m = sum(d10_rocs) / float(len(d10_rocs))
            d10_disp = sum(abs(x - _m) for x in d10_rocs) / float(len(d10_rocs))
            self._d10_disp_sum += d10_disp
            self._d10_disp_n += 1
            self._d10_disp_min = min(self._d10_disp_min, d10_disp)
            self._d10_disp_max = max(self._d10_disp_max, d10_disp)
        else:
            d10_disp = 0.0

        self._d10_ready_sum += d10_ready

        rg  = str(getattr(self, "_rrx_risk_group", "") or
                  getattr(self, "_rrx_top_theme_rg", "") or "").upper()
        cls = str(getattr(self, "_rrx_top_theme_cls", "") or "").upper()
        rxs = str(getattr(self, "_rrx_state", "") or "").upper()

        if rg  == "": self._d10_rg_unknown  += 1
        if cls == "": self._d10_cls_unknown += 1

        # [C1] Per-leg readiness - each leg independent
        spyg_ready = len(_d10_sp)  >= 20
        s2_ready   = len(_d10_sp2) >= 20
        tfp_ready  = len(_d10_tf)  >= 20
        if spyg_ready: self._d10_spyg_ready += 1
        if s2_ready:   self._d10_s2_ready   += 1
        if tfp_ready:  self._d10_tfp_ready  += 1

        d10_high_disp = (d10_ready >= 4 and d10_disp >= 0.030)
        d10_mid_disp  = (d10_ready >= 4 and d10_disp >= 0.022)

        d10_def  = rg in ("DEFENSIVE","DEFENSIVE_HEALTH","SAFE_HAVEN")
        d10_tail = rg in ("INFLATION_CYCLICAL","GEOPOLITICAL","HEALTH_VOLATILE")
        d10_cyc  = rg in ("GROWTH_CYCLICAL","CYCLICAL","THEMATIC")
        d10_strong = (rxs == "RRX_STRONG" or cls == "ROCKET")

        d10_tfp_ok = (tfp_ready and d10_strong and not d10_def and
                      tfp_r20 > 0 and tfp_dd20 <= 0.12)
        d10_s2_ok  = (spyg_ready and s2_ready and d10_strong and
                      spyg2_r20 > spyg_r20 + 0.01 and
                      spyg2_dd20 <= spyg_dd20 + 0.015)

        _r_spyg = float(_d10_sp[-1])  if _d10_sp  else 0.0
        _r_s2   = float(_d10_sp2[-1]) if _d10_sp2 else 0.0
        _r_tfp  = float(_d10_tf[-1])  if _d10_tf  else 0.0

        for _i in range(4):
            _w0,_w1,_w2 = self._d10_w[_i]
            _ret = _w0*_r_spyg + _w1*_r_s2 + _w2*_r_tfp
            self._d10_nav[_i] = max(0.01, self._d10_nav[_i] * (1.0 + _ret))
            if self._d10_nav[_i] > self._d10_peak[_i]:
                self._d10_peak[_i] = self._d10_nav[_i]
            _dd = 1.0 - self._d10_nav[_i] / max(self._d10_peak[_i], 1e-9)
            if _dd > self._d10_maxdd[_i]: self._d10_maxdd[_i] = _dd
            self._d10_rets[_i].append(_ret)
            if len(self._d10_rets[_i]) > 4000:
                self._d10_rets[_i] = self._d10_rets[_i][-2000:]
            if (_w0 + _w1 + _w2) > 0: self._d10_days[_i] += 1

        def _set_d10(_i, _w, _mode):
            _old = self._d10_mode[_i]
            if _old != _mode and _old != "INIT": self._d10_turn[_i] += 1
            self._d10_w[_i] = _w; self._d10_mode[_i] = _mode
            if _mode == "CASH":  self._d10_cash[_i] += 1
            elif _mode == "S2":  self._d10_s2[_i]   += 1
            elif _mode == "TFP": self._d10_tfp[_i]  += 1
            else:                self._d10_core[_i]  += 1

        # [C1] CASH only on hard_stress or no SPYG baseline
        if _d10_hs or not spyg_ready:
            for _i in range(4): _set_d10(_i, (0.0,0.0,0.0), "CASH")
        else:
            if d10_high_disp and d10_tfp_ok:
                _set_d10(0, (0.80,0.0,0.20), "TFP")
            else:
                _set_d10(0, (1.0,0.0,0.0), "CORE")
            if d10_high_disp and d10_tfp_ok:
                _tw = 0.10 if d10_tail else (0.25 if d10_cyc else 0.15)
                _set_d10(1, (1.0-_tw,0.0,_tw), "TFP")
            else:
                _set_d10(1, (1.0,0.0,0.0), "CORE")
            if d10_mid_disp and d10_s2_ok:
                _set_d10(2, (0.80,0.20,0.0), "S2")
            else:
                _set_d10(2, (1.0,0.0,0.0), "CORE")
            if d10_high_disp and d10_tfp_ok and (not d10_s2_ok or tfp_r20 >= spyg2_r20):
                _tw = 0.10 if d10_tail else 0.20
                _set_d10(3, (1.0-_tw,0.0,_tw), "TFP")
            elif d10_mid_disp and d10_s2_ok:
                _set_d10(3, (0.80,0.20,0.0), "S2")
            else:
                _set_d10(3, (1.0,0.0,0.0), "CORE")

    # Branch decision (lagged rolling metrics)
    spyg_alpha_ok = (
        spyg_active and spyg_r20 > tfp_r20 and spyg_dd20 <= tfp_dd20
    )
    spyg_risk_override = (
        spyg_active and
        tfp_dd20 >= 0.08 and
        spyg_dd20 <= tfp_dd20 * 0.75 and
        spyg_r20 >= tfp_r20 - 0.02
    )
    # Audit: why risk override does/does not trigger
    if not spyg_active:
        self._d8_risk_fail_active += 1
    else:
        if tfp_dd20 >= 0.08:
            self._d8_risk_candidate += 1
            if spyg_dd20 > tfp_dd20 * 0.75: self._d8_risk_fail_dd  += 1
            if spyg_r20 < tfp_r20 - 0.02:   self._d8_risk_fail_ret += 1
    # D6R context (computed once, used by TFP branch and blend)
    d6r_ok = True
    try:
        ps2  = str(getattr(self, "_panic_state", "NORMAL"))
        ids2 = str(getattr(self, "_ids_state",   "NORMAL"))
        ls   = getattr(self, "_rrx_d5z_last_stop_date", None)
        gd   = int(getattr(self, "rrx_d5z_stop_guard_days", 30))
        if ps2 in ("STRESS", "PANIC"):                   d6r_ok = False
        elif ids2 in ("STRESS", "PANIC", "PANIC_SHORT"): d6r_ok = False
        elif ls is not None and (today - ls).days <= gd: d6r_ok = False
    except Exception:
        pass
    if hard_stress:
        branch = "CASH";  sw_sym = None; sw_w = 0.0
        self._d8_use_cash += 1
    elif spyg_alpha_ok:
        branch = "SPYG";  sw_sym = getattr(self, "_d6rm_spyg_held", None); sw_w = spyg_w_now
        self._d8_use_spyg += 1; self._d8_use_spyg_alpha += 1
    elif spyg_risk_override:
        branch = "SPYG";  sw_sym = getattr(self, "_d6rm_spyg_held", None); sw_w = spyg_w_now
        self._d8_use_spyg += 1; self._d8_use_spyg_risk  += 1
    else:
        branch = "TFP"
        sw_sym = tf_sym if d6r_ok else None
        sw_w   = 1.0 if sw_sym is not None else 0.0
        self._d8_use_tfp += 1
    self._d8_month_branch = branch

    # Apply yesterday's held return
    held    = getattr(self, "_d8_sw_held",    None)
    held_px = getattr(self, "_d8_sw_held_px", 0.0)
    held_w  = float(getattr(self, "_d8_sw_weight", 0.0) or 0.0)
    sw_ret  = 0.0
    if held is not None and held_px > 0 and held_w > 0:
        try:
            cur = float(self.securities[held].price)
            if cur > 0: sw_ret = held_w * (cur / held_px - 1.0)
        except Exception: pass
    self._d8_sw_nav = max(0.01, self._d8_sw_nav * (1.0 + sw_ret))
    if self._d8_sw_nav > self._d8_sw_peak: self._d8_sw_peak = self._d8_sw_nav
    dd = 1.0 - self._d8_sw_nav / max(self._d8_sw_peak, 1e-9)
    if dd > self._d8_sw_maxdd: self._d8_sw_maxdd = dd
    self._d8_sw_rets.append(sw_ret)
    if len(self._d8_sw_rets) > 4000: self._d8_sw_rets = self._d8_sw_rets[-2000:]
    if held is not None: self._d8_sw_days += 1
    if sw_sym != held and held is not None: self._d8_sw_turn += 1

    # Store tomorrow's selection
    self._d8_sw_held   = sw_sym
    self._d8_sw_weight = sw_w
    try:
        self._d8_sw_held_px = float(self.securities[sw_sym].price) if sw_sym else 0.0
    except Exception:
        self._d8_sw_held_px = 0.0

    # Period snapshots
    if 2019 <= yr <= 2021: self._d8_sw_oos  = self._d8_sw_nav
    if 2022 <= yr <= 2025: self._d8_sw_cris = self._d8_sw_nav
    if yr == 2020: self._d8_sw_y20 = self._d8_sw_nav
    if yr == 2022: self._d8_sw_y22 = self._d8_sw_nav
    if yr == 2023: self._d8_sw_y23 = self._d8_sw_nav
    if yr == 2024: self._d8_sw_y24 = self._d8_sw_nav

    # D8C BLEND: apply yesterday's blend return
    bl_tfp_held  = getattr(self, "_d8_bl_tfp_held",  None)
    bl_tfp_px    = getattr(self, "_d8_bl_tfp_px",    0.0)
    bl_spyg_held = getattr(self, "_d8_bl_spyg_held", None)
    bl_spyg_px   = getattr(self, "_d8_bl_spyg_px",   0.0)
    bl_tfp_w     = float(getattr(self, "_d8_bl_tfp_w",  0.0))
    bl_spyg_w    = float(getattr(self, "_d8_bl_spyg_w", 0.0))
    bl_ret = 0.0
    if bl_tfp_held is not None and bl_tfp_px > 0 and bl_tfp_w > 0:
        try:
            cur = float(self.securities[bl_tfp_held].price)
            if cur > 0: bl_ret += bl_tfp_w * (cur / bl_tfp_px - 1.0)
        except Exception: pass
    if bl_spyg_held is not None and bl_spyg_px > 0 and bl_spyg_w > 0:
        try:
            cur = float(self.securities[bl_spyg_held].price)
            if cur > 0: bl_ret += bl_spyg_w * (cur / bl_spyg_px - 1.0)
        except Exception: pass
    self._d8_bl_nav = max(0.01, self._d8_bl_nav * (1.0 + bl_ret))
    if self._d8_bl_nav > self._d8_bl_peak: self._d8_bl_peak = self._d8_bl_nav
    bl_dd = 1.0 - self._d8_bl_nav / max(self._d8_bl_peak, 1e-9)
    if bl_dd > self._d8_bl_maxdd: self._d8_bl_maxdd = bl_dd
    self._d8_bl_rets.append(bl_ret)
    if len(self._d8_bl_rets) > 4000: self._d8_bl_rets = self._d8_bl_rets[-2000:]
    was_active = (bl_tfp_held is not None or bl_spyg_held is not None)
    if was_active: self._d8_bl_days += 1
    # Determine tomorrow's blend weights
    spyg_sym_now = getattr(self, "_d6rm_spyg_held", None)
    if hard_stress:
        new_tfp_w = 0.0; new_spyg_w = 0.0
        new_tfp_sym = None; new_spyg_sym = None
    elif spyg_active and tfp_dd20 >= 0.08:
        new_tfp_w = 0.5; new_spyg_w = 0.5
        new_tfp_sym  = tf_sym if d6r_ok else None
        new_spyg_sym = spyg_sym_now
    elif spyg_active:
        new_tfp_w = 0.7; new_spyg_w = 0.3
        new_tfp_sym  = tf_sym if d6r_ok else None
        new_spyg_sym = spyg_sym_now
    else:
        new_tfp_w = 1.0; new_spyg_w = 0.0
        new_tfp_sym  = tf_sym if d6r_ok else None
        new_spyg_sym = None
    # Turnover proxy (did either position change?)
    if (new_tfp_sym != bl_tfp_held or new_spyg_sym != bl_spyg_held) and was_active:
        self._d8_bl_turn += 1
    self._d8_bl_tfp_held  = new_tfp_sym;  self._d8_bl_tfp_w  = new_tfp_w
    self._d8_bl_spyg_held = new_spyg_sym; self._d8_bl_spyg_w = new_spyg_w
    try:
        self._d8_bl_tfp_px  = float(self.securities[new_tfp_sym].price)  if new_tfp_sym  else 0.0
    except Exception: self._d8_bl_tfp_px  = 0.0
    try:
        self._d8_bl_spyg_px = float(self.securities[new_spyg_sym].price) if new_spyg_sym else 0.0
    except Exception: self._d8_bl_spyg_px = 0.0
    # Blend period snapshots
    if 2019 <= yr <= 2021: self._d8_bl_oos  = self._d8_bl_nav
    if 2022 <= yr <= 2025: self._d8_bl_cris = self._d8_bl_nav
    if yr == 2020: self._d8_bl_y20 = self._d8_bl_nav
    if yr == 2022: self._d8_bl_y22 = self._d8_bl_nav
    if yr == 2023: self._d8_bl_y23 = self._d8_bl_nav
    if yr == 2024: self._d8_bl_y24 = self._d8_bl_nav

    # D8D: SPYG primary, TFP booster when its rolling profile is better and safer
    d8d_tfp_held  = getattr(self, "_d8d_tfp_held",  None)
    d8d_tfp_px    = getattr(self, "_d8d_tfp_px",    0.0)
    d8d_spyg_held = getattr(self, "_d8d_spyg_held", None)
    d8d_spyg_px   = getattr(self, "_d8d_spyg_px",   0.0)
    d8d_tfp_w     = float(getattr(self, "_d8d_tfp_w",  0.0))
    d8d_spyg_w    = float(getattr(self, "_d8d_spyg_w", 0.0))
    d8d_ret = 0.0
    if d8d_tfp_held is not None and d8d_tfp_px > 0 and d8d_tfp_w > 0:
        try:
            cur = float(self.securities[d8d_tfp_held].price)
            if cur > 0: d8d_ret += d8d_tfp_w * (cur / d8d_tfp_px - 1.0)
        except Exception: pass
    if d8d_spyg_held is not None and d8d_spyg_px > 0 and d8d_spyg_w > 0:
        try:
            cur = float(self.securities[d8d_spyg_held].price)
            if cur > 0: d8d_ret += d8d_spyg_w * (cur / d8d_spyg_px - 1.0)
        except Exception: pass
    self._d8d_nav = max(0.01, self._d8d_nav * (1.0 + d8d_ret))
    if self._d8d_nav > self._d8d_peak: self._d8d_peak = self._d8d_nav
    d8d_dd = 1.0 - self._d8d_nav / max(self._d8d_peak, 1e-9)
    if d8d_dd > self._d8d_maxdd: self._d8d_maxdd = d8d_dd
    self._d8d_rets.append(d8d_ret)
    if len(self._d8d_rets) > 4000: self._d8d_rets = self._d8d_rets[-2000:]
    d8d_was_active = (d8d_tfp_held is not None or d8d_spyg_held is not None)
    if d8d_was_active: self._d8d_days += 1
    # D8D branch decision: SPYG primary, TFP only as booster
    spyg_w_now  = float(getattr(self, "_d6rm_spyg_weight", 0.0) or 0.0)
    spyg_base   = spyg_w_now > 0                          # mirrors SPYG curve exactly
    booster_rdy = (len(spyg_rets) >= 20 and len(tfp_rets) >= 20)

    tfp_booster_ok = (
        spyg_base and booster_rdy and not hard_stress and
        tfp_dd20 <= 0.04 and
        tfp_r20 > 0 and
        tfp_r20 >= spyg_r20 - 0.02
    )
    if hard_stress:
        new_d8d_tfp_sym = None; new_d8d_spyg_sym = None
        new_d8d_tfp_w = 0.0; new_d8d_spyg_w = 0.0
        self._d8d_use_cash += 1
    elif tfp_booster_ok:
        new_d8d_tfp_sym  = tf_sym if d6r_ok else None
        new_d8d_spyg_sym = spyg_sym_now
        new_d8d_tfp_w    = 0.3; new_d8d_spyg_w = 0.7
        self._d8d_use_boost += 1
    elif spyg_base:
        new_d8d_tfp_sym  = None; new_d8d_spyg_sym = spyg_sym_now
        new_d8d_tfp_w    = 0.0;  new_d8d_spyg_w   = 1.0
        self._d8d_use_spyg += 1
    else:
        new_d8d_tfp_sym = None; new_d8d_spyg_sym = None
        new_d8d_tfp_w = 0.0; new_d8d_spyg_w = 0.0
        self._d8d_use_cash += 1
    if (new_d8d_tfp_sym != d8d_tfp_held or new_d8d_spyg_sym != d8d_spyg_held) and d8d_was_active:
        self._d8d_turn += 1
    self._d8d_tfp_held  = new_d8d_tfp_sym;  self._d8d_tfp_w  = new_d8d_tfp_w
    self._d8d_spyg_held = new_d8d_spyg_sym; self._d8d_spyg_w = new_d8d_spyg_w
    try: self._d8d_tfp_px  = float(self.securities[new_d8d_tfp_sym].price)  if new_d8d_tfp_sym  else 0.0
    except: self._d8d_tfp_px  = 0.0
    try: self._d8d_spyg_px = float(self.securities[new_d8d_spyg_sym].price) if new_d8d_spyg_sym else 0.0
    except: self._d8d_spyg_px = 0.0
    # D8D period snapshots
    if 2019 <= yr <= 2021: self._d8d_oos  = self._d8d_nav
    if 2022 <= yr <= 2025: self._d8d_cris = self._d8d_nav
    if yr == 2020: self._d8d_y20 = self._d8d_nav
    if yr == 2022: self._d8d_y22 = self._d8d_nav
    if yr == 2023: self._d8d_y23 = self._d8d_nav
    if yr == 2024: self._d8d_y24 = self._d8d_nav

    # ALLOC-SPYG-SAT-D0: base portfolio vs SPYG satellite at trading cap.
    try:
        pv = float(self.Portfolio.TotalPortfolioValue)
    except Exception:
        pv = 0.0
    prev_pv = float(getattr(self, "_d8_prev_pv", 0.0))
    if prev_pv > 0 and pv > 0:
        base_ret = pv / prev_pv - 1.0
        spyg_r   = spyg_rets[-1] if spyg_rets else 0.0
        cap = max(0.0, min(1.0, float(getattr(self, "_d8_sat_cap", 0.20) or 0.20)))
        prev_sigw = max(0.0, min(1.0, float(getattr(self, "_d8_sat_prev_sigw", 0.0) or 0.0)))
        if bool(getattr(self, "_d8_sat_active_only_scale", True)):
            base_scale = 1.0 - cap * prev_sigw
        else:
            base_scale = 1.0 - cap
        base_scale = max(0.0, min(1.0, base_scale))
        sat20_ret = base_scale * base_ret + cap * spyg_r
        for _wn in win_today:
            _aw = self._d8_alloc_win.get(_wn)
            if _aw:
                _d8_curve_update(_aw["base"], base_ret)
                _d8_curve_update(_aw["sat20"], sat20_ret)

        for nav_a, peak_a, maxdd_a, rets_a, ret_a, attr_nav, attr_peak, attr_dd, attr_rets in [
            (self._d8_base_nav, self._d8_base_peak, self._d8_base_maxdd,
             self._d8_base_rets, base_ret,
             "_d8_base_nav", "_d8_base_peak", "_d8_base_maxdd", "_d8_base_rets"),
            (self._d8_sat20_nav, self._d8_sat20_peak, self._d8_sat20_maxdd,
             self._d8_sat20_rets, sat20_ret,
             "_d8_sat20_nav", "_d8_sat20_peak", "_d8_sat20_maxdd", "_d8_sat20_rets"),
        ]:
            nav_a = max(0.01, nav_a * (1.0 + ret_a))
            if nav_a > peak_a: peak_a = nav_a
            dd_a = 1.0 - nav_a / max(peak_a, 1e-9)
            if dd_a > maxdd_a: maxdd_a = dd_a
            rets_a.append(ret_a)
            if len(rets_a) > 4000: rets_a[:] = rets_a[-2000:]
            setattr(self, attr_nav,   nav_a)
            setattr(self, attr_peak,  peak_a)
            setattr(self, attr_dd,    maxdd_a)
        # Alloc period snapshots
        if 2019 <= yr <= 2021:
            self._d8_base_oos  = self._d8_base_nav; self._d8_sat20_oos  = self._d8_sat20_nav
        if 2022 <= yr <= 2025:
            self._d8_base_cris = self._d8_base_nav; self._d8_sat20_cris = self._d8_sat20_nav
        if yr == 2020: self._d8_base_y20 = self._d8_base_nav; self._d8_sat20_y20 = self._d8_sat20_nav
        if yr == 2022: self._d8_base_y22 = self._d8_base_nav; self._d8_sat20_y22 = self._d8_sat20_nav
        if yr == 2023: self._d8_base_y23 = self._d8_base_nav; self._d8_sat20_y23 = self._d8_sat20_nav
        if yr == 2024: self._d8_base_y24 = self._d8_base_nav; self._d8_sat20_y24 = self._d8_sat20_nav

        try:
            block, _why = self._SpygSatBlock()
        except Exception:
            block = False
            _why = "ERR"
        next_sym = getattr(self, "_d6rm_spyg_held", None)
        next_sigw = max(0.0, min(1.0, float(getattr(self, "_d6rm_spyg_weight", 0.0) or 0.0)))
        try:
            min_tgt = float(self._SpygSatFloat("spyg_sat_min_target", 0.002))
        except Exception:
            min_tgt = 0.002
        if block or next_sym is None or cap * next_sigw < min_tgt:
            next_sigw = 0.0
        self._d8_sat_prev_sigw = next_sigw

        if getattr(self, "rrx_sat_sr_support_premium_enable", False):
            stats = [self._srprem_run] + [self._srprem_win[x] for x in win_today if x in self._srprem_win]
            cur_sym = next_sym if next_sigw > 0.0 else None
            prev_sym = getattr(self, "_srprem_sym", None)
            reg = str(getattr(self, "current_regime", "") or "").upper()
            weak_ctx = (
                reg != "RISK_ON" or ps != "NORMAL" or ids != "NORMAL" or
                bool(getattr(self, "short_shock_flag", False))
            )
            for st in stats:
                st["days"] += 1
                _d8_curve_update(st["base"], base_ret)
                for i in range(5):
                    m = max(0.0, min(1.0, float(self._srprem_mult[i])))
                    psw = max(0.0, min(1.0, float(self._srprem_prev_sigw[i])))
                    ec = cap * m
                    bs = 1.0 - (ec * psw if bool(getattr(self, "_d8_sat_active_only_scale", True)) else ec)
                    _d8_curve_update(st["c"][i], max(0.0, min(1.0, bs)) * base_ret + ec * spyg_r)

            new_mult = list(self._srprem_mult)
            transition = (cur_sym != prev_sym)
            if transition and cur_sym is None:
                new_mult = [1.0, 1.0, 1.0, 1.0, 1.0]
            elif transition and cur_sym is not None:
                ctx = _d8_sr_ctx(self, cur_sym)
                ready = ctx is not None
                ds = float(ctx["dist_sup_atr"]) if ready else 99.0
                dr = float(ctx["room_res_atr"]) if ready else 99.0
                rr = dr / max(0.50, ds)
                if ready:
                    for st in stats:
                        st["ready"] += 1
                        if ctx["near_sup"]: st["entry_near_sup"] += 1
                        if ctx["near_res"]: st["entry_near_res"] += 1
                        if ds > 2.0: st["entry_far_sup"] += 1
                        if rr >= 2.0: st["entry_good_rr"] += 1
                        if rr < 1.0: st["entry_bad_rr"] += 1
                else:
                    for st in stats: st["not_ready"] += 1

                near_sup = bool(ready and ctx["near_sup"])
                good_rr = bool(ready and rr >= 2.0)
                new_mult = [
                    1.0,
                    1.0 if near_sup else 0.90,
                    1.0 if near_sup else 0.80,
                    1.0 if (near_sup or good_rr) else 0.90,
                    (1.0 if (not weak_ctx or near_sup) else 0.80),
                ]
                for i, m in enumerate(new_mult):
                    for st in stats:
                        st["lock"][i] += 1
                        if m <= 0.81: st["cap080"][i] += 1
                        elif m <= 0.91: st["cap090"][i] += 1
                        else: st["cap100"][i] += 1

            for i in range(5):
                old_eff = cap * float(self._srprem_mult[i]) * float(self._srprem_prev_sigw[i])
                new_eff = cap * float(new_mult[i]) * float(next_sigw)
                if abs(new_eff - old_eff) > 1e-7:
                    for st in stats: st["ord"][i] += 1
                for st in stats: st["cap_sum"][i] += cap * float(new_mult[i])
                self._srprem_mult[i] = float(new_mult[i])
                self._srprem_prev_sigw[i] = float(next_sigw)
            self._srprem_sym = cur_sym

    if pv > 0: self._d8_prev_pv = pv

    # Monthly boundary
    mk = today.strftime("%Y-%m")
    if self._d8_month_key is None:
        self._d8_month_key = mk; self._d8_mnav_start = self._d8_sw_nav
    elif mk != self._d8_month_key:
        D8SwitchDiagEmitMonthly(self)
        self._d8_month_key = mk; self._d8_mnav_start = self._d8_sw_nav


# ---------------------------------------------------------------------------
# Emit
# ---------------------------------------------------------------------------

def D8SwitchDiagEmitMonthly(self, today=None) -> None:
    """[D8] Monthly branch summary."""
    if not getattr(self, "rrx_d8_log_monthly", False): return
    if today is None: today = self.time.date()
    base = max(self._d8_mnav_start, 1e-9)
    self.log(
        f"RRX_D8_SWITCH_MONTH,{self._d8_month_key},{today},"
        f"sw_nav={self._d8_sw_nav:.4f},"
        f"sw_mret={self._d8_sw_nav/base-1:+.4f},"
        f"sw_dd={self._d8_sw_maxdd:.4f},"
        f"branch={self._d8_month_branch},"
        f"use_tfp={self._d8_use_tfp},"
        f"use_spyg={self._d8_use_spyg},"
        f"use_cash={self._d8_use_cash}"
    )


def D8SwitchDiagEmitFinal(self, start, today) -> None:
    """[D8] Final branch switcher summary with period breakdown."""
    D8SwitchDiagEmitMonthly(self, today)
    if self._d8_sw_rets:
        rets = self._d8_sw_rets
        n5   = max(1, int(len(rets) * 0.05))
        w5   = float(sum(sorted(rets)[:n5]) / n5)
    else:
        w5 = 0.0
    self.log(
        f"RRX_D8_SWITCH_FINAL,start={start},end={today},"
        f"sw_nav={self._d8_sw_nav:.4f},"
        f"sw_maxdd={self._d8_sw_maxdd:.4f},"
        f"sw_w5={w5:+.5f},"
        f"sw_days={self._d8_sw_days},"
        f"sw_turn={self._d8_sw_turn},"
        f"use_tfp={self._d8_use_tfp},"
        f"use_spyg={self._d8_use_spyg},"
        f"use_spyg_alpha={self._d8_use_spyg_alpha},"
        f"use_spyg_risk={self._d8_use_spyg_risk},"
        f"risk_candidate={self._d8_risk_candidate},"
        f"risk_fail_active={self._d8_risk_fail_active},"
        f"risk_fail_dd={self._d8_risk_fail_dd},"
        f"risk_fail_ret={self._d8_risk_fail_ret},"
        f"use_tail={self._d8_use_tail},"
        f"use_cash={self._d8_use_cash},"
        f"sw_oos={self._d8_sw_oos:.4f},"
        f"sw_cris={self._d8_sw_cris:.4f},"
        f"sw_y20={self._d8_sw_y20:.4f},"
        f"sw_y22={self._d8_sw_y22:.4f},"
        f"sw_y23={self._d8_sw_y23:.4f},"
        f"sw_y24={self._d8_sw_y24:.4f}"
    )
    # D8C blend final
    if self._d8_bl_rets:
        rets_bl = self._d8_bl_rets
        n5_bl   = max(1, int(len(rets_bl) * 0.05))
        w5_bl   = float(sum(sorted(rets_bl)[:n5_bl]) / n5_bl)
        self.log(
            f"RRX_D8_BLEND_FINAL,start={start},end={today},"
            f"bl_nav={self._d8_bl_nav:.4f},"
            f"bl_maxdd={self._d8_bl_maxdd:.4f},"
            f"bl_w5={w5_bl:+.5f},"
            f"bl_days={self._d8_bl_days},"
            f"bl_turn={self._d8_bl_turn},"
            f"bl_oos={self._d8_bl_oos:.4f},"
            f"bl_cris={self._d8_bl_cris:.4f},"
            f"bl_y20={self._d8_bl_y20:.4f},"
            f"bl_y22={self._d8_bl_y22:.4f},"
            f"bl_y23={self._d8_bl_y23:.4f},"
            f"bl_y24={self._d8_bl_y24:.4f}"
        )
    # ALLOC-SPYG-SAT-D0 final
    if self._d8_base_rets and self._d8_sat20_rets:
        n5b = max(1, int(len(self._d8_base_rets) * 0.05))
        n5s = max(1, int(len(self._d8_sat20_rets) * 0.05))
        w5b = float(sum(sorted(self._d8_base_rets)[:n5b]) / n5b)
        w5s = float(sum(sorted(self._d8_sat20_rets)[:n5s]) / n5s)
        self.log(
            f"RRX_D8_ALLOC_FINAL,start={start},end={today},"
            f"base_nav={self._d8_base_nav:.4f},"
            f"sat20_nav={self._d8_sat20_nav:.4f},"
            f"base_maxdd={self._d8_base_maxdd:.4f},"
            f"sat20_maxdd={self._d8_sat20_maxdd:.4f},"
            f"base_w5={w5b:+.5f},"
            f"sat20_w5={w5s:+.5f},"
            f"sat_cap={float(getattr(self, '_d8_sat_cap', 0.20)):.3f},"
            f"active_only={int(bool(getattr(self, '_d8_sat_active_only_scale', True)))},"
            f"delta_nav={self._d8_sat20_nav-self._d8_base_nav:+.4f},"
            f"delta_dd={self._d8_sat20_maxdd-self._d8_base_maxdd:+.4f},"
            f"delta_w5={w5s-w5b:+.5f},"
            f"base_oos={self._d8_base_oos:.4f},sat20_oos={self._d8_sat20_oos:.4f},"
            f"base_cris={self._d8_base_cris:.4f},sat20_cris={self._d8_sat20_cris:.4f},"
            f"base_y20={self._d8_base_y20:.4f},sat20_y20={self._d8_sat20_y20:.4f},"
            f"base_y22={self._d8_base_y22:.4f},sat20_y22={self._d8_sat20_y22:.4f},"
            f"base_y23={self._d8_base_y23:.4f},sat20_y23={self._d8_sat20_y23:.4f},"
            f"base_y24={self._d8_base_y24:.4f},sat20_y24={self._d8_sat20_y24:.4f}"
        )
        for _wn, _st, _en in getattr(self, "_d8_diag_windows", []):
            _aw = self._d8_alloc_win.get(_wn)
            if not _aw or not _aw["base"]["rets"]:
                continue
            _b = _aw["base"]; _s = _aw["sat20"]
            self.log(
                f"RRX_D8_ALLOC_FINAL,win={_wn},start={_st},end={_en},"
                f"base_nav={_b['nav']:.4f},"
                f"sat20_nav={_s['nav']:.4f},"
                f"base_maxdd={_b['dd']:.4f},"
                f"sat20_maxdd={_s['dd']:.4f},"
                f"base_w5={_d8_worst5(_b['rets']):+.5f},"
                f"sat20_w5={_d8_worst5(_s['rets']):+.5f},"
                f"sat_cap={float(getattr(self, '_d8_sat_cap', 0.20)):.3f},"
                f"active_only={int(bool(getattr(self, '_d8_sat_active_only_scale', True)))}"
            )
        if getattr(self, "rrx_sat_sr_support_premium_enable", False):
            def _emit_srprem(_win, _st, _en, w):
                if not w or not w["base"]["rets"]:
                    return
                parts = [
                    f"RRX_SAT_SR_SUPPORT_PREMIUM_FINAL,ver=D0,win={_win},start={_st},end={_en}",
                    f"days={w['days']}",
                    f"ready={w['ready']}",
                    f"not_ready={w['not_ready']}",
                    f"base_nav={w['base']['nav']:.4f}",
                    f"base_dd={w['base']['dd']:.4f}",
                    f"base_w5={_d8_worst5(w['base']['rets']):+.5f}",
                    f"entry_near_sup_n={w['entry_near_sup']}",
                    f"entry_near_res_n={w['entry_near_res']}",
                    f"entry_far_sup_n={w['entry_far_sup']}",
                    f"entry_good_rr_n={w['entry_good_rr']}",
                    f"entry_bad_rr_n={w['entry_bad_rr']}",
                ]
                for i, lb in enumerate(("c0", "c1", "c2", "c3", "c4")):
                    c = w["c"][i]
                    parts.append(
                        f"{lb}_nav={c['nav']:.4f},"
                        f"{lb}_dd={c['dd']:.4f},"
                        f"{lb}_w5={_d8_worst5(c['rets']):+.5f},"
                        f"{lb}_ord={w['ord'][i]},"
                        f"{lb}_lock_n={w['lock'][i]},"
                        f"{lb}_cap080={w['cap080'][i]},"
                        f"{lb}_cap090={w['cap090'][i]},"
                        f"{lb}_cap100={w['cap100'][i]},"
                        f"{lb}_cap_avg={w['cap_sum'][i]/max(1,w['days']):.4f}"
                    )
                self.log(",".join(parts))
            _emit_srprem("RUN", str(start), str(today), self._srprem_run)
            for _wn, _st, _en in getattr(self, "_d8_diag_windows", []):
                _emit_srprem(_wn, _st, _en, self._srprem_win.get(_wn))
        if getattr(self, "rrx_d9_disp_enable", True) and self._d9_disp_rets:
            r9=self._d9_disp_rets; n9=max(1,int(len(r9)*0.05))
            w9=float(sum(sorted(r9)[:n9])/n9)
            r2=getattr(self,"_d6rm_spyg2_rets",[]) or []
            r1=getattr(self,"_d6rm_spyg_rets",[]) or []
            n2=max(1,int(len(r2)*0.05)) if r2 else 1
            n1=max(1,int(len(r1)*0.05)) if r1 else 1
            w2=float(sum(sorted(r2)[:n2])/n2) if r2 else 0.0
            w1=float(sum(sorted(r1)[:n1])/n1) if r1 else 0.0
            self.log(
                f"RRX_D9_DISP_FINAL,start={start},end={today},"
                f"d9_nav={self._d9_disp_nav:.4f},"
                f"d9_maxdd={self._d9_disp_maxdd:.4f},"
                f"d9_w5={w9:+.5f},"
                f"spyg_nav={getattr(self,'_d6rm_spyg_nav',1.0):.4f},"
                f"spyg2_nav={getattr(self,'_d6rm_spyg2_nav',1.0):.4f},"
                f"spyg_maxdd={getattr(self,'_d6rm_spyg_maxdd',0.0):.4f},"
                f"spyg2_maxdd={getattr(self,'_d6rm_spyg2_maxdd',0.0):.4f},"
                f"spyg_w5={w1:+.5f},spyg2_w5={w2:+.5f},"
                f"use_spyg={self._d9_use_spyg},use_spyg2={self._d9_use_spyg2},"
                f"use_cash={self._d9_use_cash},d9_days={self._d9_disp_days},"
                f"d9_turn={self._d9_disp_turn},"
                f"d9_oos={self._d9_disp_oos:.4f},d9_cris={self._d9_disp_cris:.4f},"
                f"d9_y20={self._d9_disp_y20:.4f},d9_y22={self._d9_disp_y22:.4f},"
                f"d9_y23={self._d9_disp_y23:.4f},d9_y24={self._d9_disp_y24:.4f}"
            )
        rets_d8d = self._d8d_rets
        n5_d8d   = max(1, int(len(rets_d8d) * 0.05))
        w5_d8d   = float(sum(sorted(rets_d8d)[:n5_d8d]) / n5_d8d)
        self.log(
            f"RRX_D8D_FINAL,start={start},end={today},"
            f"d8d_nav={self._d8d_nav:.4f},"
            f"d8d_maxdd={self._d8d_maxdd:.4f},"
            f"d8d_w5={w5_d8d:+.5f},"
            f"d8d_days={self._d8d_days},"
            f"d8d_turn={self._d8d_turn},"
            f"use_spyg={self._d8d_use_spyg},"
            f"use_boost={self._d8d_use_boost},"
            f"use_cash={self._d8d_use_cash},"
            f"d8d_oos={self._d8d_oos:.4f},"
            f"d8d_cris={self._d8d_cris:.4f},"
            f"d8d_y20={self._d8d_y20:.4f},"
            f"d8d_y22={self._d8d_y22:.4f},"
            f"d8d_y23={self._d8d_y23:.4f},"
            f"d8d_y24={self._d8d_y24:.4f}"
        )
    # D10_COMPARE_C1 final - independent of D8_ALLOC block
    if getattr(self, "rrx_d10_compare_enable", True) and self._d10_rets[0]:
        def _w5(_rs):
            _n=max(1,int(len(_rs)*0.05))
            return float(sum(sorted(_rs)[:_n])/_n) if _rs else 0.0
        r1=getattr(self,"_d6rm_spyg_rets",[]) or []
        r2=getattr(self,"_d6rm_spyg2_rets",[]) or []
        n1=max(1,int(len(r1)*0.05)) if r1 else 1
        n2=max(1,int(len(r2)*0.05)) if r2 else 1
        w1=float(sum(sorted(r1)[:n1])/n1) if r1 else 0.0
        w2=float(sum(sorted(r2)[:n2])/n2) if r2 else 0.0
        d10_dn=max(1,self._d10_disp_n)
        self.log(
            f"RRX_D10_COMPARE_FINAL,ver=C1,start={start},end={today},"
            f"a0_nav={self._d10_nav[0]:.4f},a0_dd={self._d10_maxdd[0]:.4f},"
            f"a0_w5={_w5(self._d10_rets[0]):+.5f},a0_tfp={self._d10_tfp[0]},"
            f"a0_s2={self._d10_s2[0]},a0_cash={self._d10_cash[0]},a0_turn={self._d10_turn[0]},"
            f"a1_nav={self._d10_nav[1]:.4f},a1_dd={self._d10_maxdd[1]:.4f},"
            f"a1_w5={_w5(self._d10_rets[1]):+.5f},a1_tfp={self._d10_tfp[1]},"
            f"a1_s2={self._d10_s2[1]},a1_cash={self._d10_cash[1]},a1_turn={self._d10_turn[1]},"
            f"a2_nav={self._d10_nav[2]:.4f},a2_dd={self._d10_maxdd[2]:.4f},"
            f"a2_w5={_w5(self._d10_rets[2]):+.5f},a2_tfp={self._d10_tfp[2]},"
            f"a2_s2={self._d10_s2[2]},a2_cash={self._d10_cash[2]},a2_turn={self._d10_turn[2]},"
            f"a3_nav={self._d10_nav[3]:.4f},a3_dd={self._d10_maxdd[3]:.4f},"
            f"a3_w5={_w5(self._d10_rets[3]):+.5f},a3_tfp={self._d10_tfp[3]},"
            f"a3_s2={self._d10_s2[3]},a3_cash={self._d10_cash[3]},a3_turn={self._d10_turn[3]},"
            f"spyg_nav={getattr(self,'_d6rm_spyg_nav',1.0):.4f},"
            f"spyg_dd={getattr(self,'_d6rm_spyg_maxdd',0.0):.4f},spyg_w5={w1:+.5f},"
            f"spyg2_nav={getattr(self,'_d6rm_spyg2_nav',1.0):.4f},"
            f"spyg2_dd={getattr(self,'_d6rm_spyg2_maxdd',0.0):.4f},spyg2_w5={w2:+.5f},"
            f"disp_n={self._d10_disp_n},"
            f"disp_ready_avg={self._d10_ready_sum/d10_dn:.2f},"
            f"disp_avg={self._d10_disp_sum/d10_dn:.5f},"
            f"disp_min={self._d10_disp_min if self._d10_disp_min<900 else 0.0:.5f},"
            f"disp_max={self._d10_disp_max:.5f},"
            f"rg_unknown={self._d10_rg_unknown},cls_unknown={self._d10_cls_unknown},"
            f"spyg_ready={self._d10_spyg_ready},"
            f"s2_ready={self._d10_s2_ready},"
            f"tfp_ready={self._d10_tfp_ready}"
        )
