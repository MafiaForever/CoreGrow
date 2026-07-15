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
    self.rrx_eqwin_enable = _gb("rrx_eqwin_enable", 1)
    if getattr(self, "cg_fast_baseline_mode", False):  # [E0.5.1] diagnostic-only
        _fd = getattr(self, "_cg_fast_disabled", None)
        if self.rrx_d8_log_monthly:
            self.rrx_d8_log_monthly = False
            if _fd is not None: _fd.append("rrx_d8_log_monthly")
        if self.rrx_eqwin_enable:
            self.rrx_eqwin_enable = False
            if _fd is not None: _fd.append("rrx_eqwin_enable")
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
    if self.rrx_eqwin_enable:  # [E0.5.1] skip window collectors when disabled
        for _chunk in _wdef.split("|"):
            _p = [x.strip() for x in _chunk.split(":")]
            if len(_p) == 3 and _p[0] and _p[1] and _p[2]:
                self._d8_diag_windows.append((_p[0], _p[1], _p[2]))
                self._d8_alloc_win[_p[0]] = {"base": _d8_curve_blank(), "sat20": _d8_curve_blank()}
    # SAT_SR_SUPPORT_PREMIUM_COMPARE_D0: support permits full baseline cap.
    self.rrx_sat_sr_support_premium_enable = _gb("rrx_sat_sr_support_premium_enable", 0)
    if getattr(self, "cg_fast_baseline_mode", False) and self.rrx_sat_sr_support_premium_enable:  # [E0.5.1]
        self.rrx_sat_sr_support_premium_enable = False
        _fd = getattr(self, "_cg_fast_disabled", None)
        if _fd is not None: _fd.append("rrx_sat_sr_support_premium_enable")
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

# ---------------------------------------------------------------------------
# CG-DEF-GROSS-D1: selective duration veto + toxic-IDS / transition equity caps.
# Diagnostic-only. Zero trading impact. Never mutates real targets.
# Shadow convention: weights at T applied to T close → T+1 close (no look-ahead).
# BASE is target-shadow accounting, not QC portfolio NAV.
# ---------------------------------------------------------------------------
from datetime import date as _dg_date
from collections import deque as _dg_deque

_DG_V = ("BASE", "D1", "E1", "E2")
_DG_NV = ("D1", "E1", "E2")
_DG_W = (
    ("TRAIN", _dg_date(2012,1,1), _dg_date(2018,12,31)),
    ("OOS", _dg_date(2019,1,1), _dg_date(2021,12,31)),
    ("CRISIS", _dg_date(2022,1,1), _dg_date(2025,12,31)),
    ("Y2012", _dg_date(2012,1,1), _dg_date(2012,12,31)),
    ("Y2015", _dg_date(2015,1,1), _dg_date(2015,12,31)),
    ("Y2020", _dg_date(2020,1,1), _dg_date(2020,12,31)),
    ("Y2022", _dg_date(2022,1,1), _dg_date(2022,12,31)),
    ("Y2023", _dg_date(2023,1,1), _dg_date(2023,12,31)),
    ("Y2024", _dg_date(2024,1,1), _dg_date(2024,12,31)),
    ("Y2025", _dg_date(2025,1,1), _dg_date(2025,12,31)),
    ("LIVE_RECENT", _dg_date(2026,1,1), None),
)
_DG_CASH = frozenset(("BIL", "SGOV", "USFR"))
_DG_DUR = frozenset(("BND", "TIP"))
_DG_GOLD = frozenset(("GLD", "GLDM"))
_DG_HEDGE = frozenset(("SH",))
_DG_BUDGET = 90000


def _dg_blank():
    return {"n": 0, "sum_r": 0.0, "sum_r2": 0.0, "nav": 1.0, "peak": 1.0, "maxdd": 0.0,
            "sum_tg": 0.0, "sum_eg": 0.0, "sum_dg": 0.0, "sum_gg": 0.0, "sum_c": 0.0,
            "act": 0, "rets": _dg_deque(maxlen=4096)}


def _dg_upd(st, r, tg, eg, dg, gg, c, act=0):
    st["n"] += 1
    st["sum_r"] += r; st["sum_r2"] += r * r
    st["nav"] = max(0.01, st["nav"] * (1.0 + r))
    if st["nav"] > st["peak"]: st["peak"] = st["nav"]
    dd = 1.0 - st["nav"] / max(st["peak"], 1e-9)
    if dd > st["maxdd"]: st["maxdd"] = dd
    st["sum_tg"] += tg; st["sum_eg"] += eg; st["sum_dg"] += dg
    st["sum_gg"] += gg; st["sum_c"] += c
    st["act"] += act
    st["rets"].append(r)


def _dg_w5(rets):
    if not rets: return None
    a = sorted(rets); k = max(1, int(0.05 * len(a) + 0.999))
    return sum(a[:k]) / k


def _dg_ann(s, n):
    return None if n < 20 else (1.0 + s / n) ** 252 - 1.0


def _dg_vol(s, s2, n):
    if n < 5: return None
    m = s / n; v = max(0.0, s2 / n - m * m)
    return (v ** 0.5) * (252 ** 0.5)


def _dg_sh(s, s2, n):
    v = _dg_vol(s, s2, n); a = _dg_ann(s, n)
    if v is None or a is None or v < 1e-12: return None
    return a / v


def _dg_f(x, d=4):
    if x is None: return "NA"
    try: return f"{float(x):.{d}f}"
    except Exception: return "NA"


def _dg_tk(s):
    try: return str(s.Value)
    except Exception:
        try: return str(s.value)
        except Exception: return str(s)


def _dg_act_blank():
    return {"n": 0, "sum_br": 0.0, "sum_vr": 0.0, "pos_b": 0, "neg_b": 0,
            "nav_b": 1.0, "nav_v": 1.0,
            "sum_eb": 0.0, "sum_ea": 0.0, "sum_db": 0.0, "sum_da": 0.0,
            "sum_cb": 0.0, "sum_ca": 0.0}


class CgDefGrossDiagMixin:
    """DEF-GROSS-D1 shadow matrix. Diagnostic-only."""

    def CgDefGrossInit(self) -> None:
        try:
            ov = getattr(self, "_rrx_param_overrides", {}) or {}
            def _p(k, d=""):
                v = self.get_parameter(k)
                if v is None or str(v).strip() == "": v = ov.get(k, d)
                return v
            en = str(_p("cg_def_gross_diag_enable", "1") or "1").strip().lower()
            self.cg_def_gross_diag_enable = en in ("1", "true", "yes", "on")
            lp = list(getattr(self, "log_only_prefixes", None) or [])
            for pref in ("CG_DEF_D1_", "CG_DEF_GROSS_"):
                if pref not in lp: lp.append(pref)
            self.log_only_prefixes = lp
            self.log("[INIT] CG_DEF_D1_DIAG enable="
                     f"{int(self.cg_def_gross_diag_enable)} variants=BASE,D1,E1,E2 trade=0 "
                     f"conv=T_close_to_T1_close shadow=target_not_qc_nav")
            if not self.cg_def_gross_diag_enable: return
            self._dg_run = {v: _dg_blank() for v in _DG_V}
            self._dg_win = {(v, w[0]): _dg_blank() for v in _DG_V for w in _DG_W}
            self._dg_act = {v: _dg_act_blank() for v in _DG_NV}
            self._dg_ids = {k: _dg_blank() for k in ("WATCH_BASE", "WATCH_E1", "STRESS_BASE", "STRESS_E1")}
            self._dg_prev_w = {v: None for v in _DG_V}
            self._dg_prev_px = None
            self._dg_prev_act = {}
            self._dg_prev_ids = None
            self._dg_prev_ps = None
            self._dg_last = None
            self._dg_n = 0
            self._dg_bytes = 0
            self._dg_pxbuf = {}
            self._dg_e2_on = False
            self._dg_e2_left = 0
            self._dg_err = False
        except Exception as e:
            try: self.log(f"[INIT] CG_DEF_D1_ERROR,stage=init,type={type(e).__name__}")
            except Exception: pass

    def _DgEqSet(self):
        eq = {"SPY"}
        for s in getattr(self, "panic_tactical_universe", []) or []:
            eq.add(_dg_tk(s))
        return eq

    def _DgGrp(self, tk, eq):
        if tk in _DG_DUR: return "D"
        if tk in _DG_GOLD: return "G"
        if tk in _DG_CASH: return "C"
        if tk in _DG_HEDGE: return "H"
        if tk in eq: return "E"
        return "O"

    def _DgGross(self, w, eq, which=None):
        g = 0.0
        for t, wt in (w or {}).items():
            try: wf = abs(float(wt or 0.0))
            except Exception: continue
            gr = self._DgGrp(t, eq)
            if which is None:
                if gr != "C": g += wf
            elif gr == which:
                g += wf
        return g

    def _DgCash(self, w):
        c = 0.0
        for t, wt in (w or {}).items():
            if t in _DG_CASH:
                try: c += abs(float(wt or 0.0))
                except Exception: pass
        return c

    def _DgCashTk(self):
        s = getattr(self, "sym_cash", None)
        return _dg_tk(s) if s is not None else "BIL"

    def _DgPark(self, before, after):
        def nc(w):
            return sum(abs(float(x or 0.0)) for t, x in (w or {}).items() if t not in _DG_CASH)
        freed = nc(before) - nc(after)
        if freed <= 1e-12: return after
        out = dict(after); ct = self._DgCashTk()
        out[ct] = float(out.get(ct, 0.0) or 0.0) + freed
        return out

    def _DgScaleGroup(self, w, eq, grp, scale):
        out = dict(w)
        for t in list(out.keys()):
            if self._DgGrp(t, eq) == grp:
                try: out[t] = float(out[t] or 0.0) * scale
                except Exception: pass
        return self._DgPark(w, out)

    def _DgZeroGroup(self, w, eq, grp):
        out = dict(w)
        for t in list(out.keys()):
            if self._DgGrp(t, eq) == grp: out[t] = 0.0
        return self._DgPark(w, out)

    def _DgBaseW(self, combined):
        w = {}
        for s, wt in (combined or {}).items():
            try: w[_dg_tk(s)] = float(wt or 0.0)
            except Exception: continue
        return w

    def _DgPx(self, combined):
        px = {}
        syms = list(combined or {})
        for s in getattr(self, "panic_tactical_universe", []) or []:
            if s not in syms: syms.append(s)
        for attr in ("sym_spy","sym_bnd","sym_tip","sym_gld","sym_cash","sym_crash","sym_sh"):
            s = getattr(self, attr, None)
            if s is not None and s not in syms: syms.append(s)
        for s in syms:
            t = _dg_tk(s)
            try:
                p = float(self.securities[s].price)
                if p > 0: px[t] = p
            except Exception: pass
        return px

    def _DgRet(self, w, p0, p1):
        r = 0.0
        for t, wt in (w or {}).items():
            if t in _DG_CASH: continue
            a = p0.get(t) if p0 else None; b = p1.get(t) if p1 else None
            if not a or not b or a <= 0: continue
            try: r += float(wt or 0.0) * (b / a - 1.0)
            except Exception: pass
        return r

    def _DgBufRet(self, tk, n):
        buf = self._dg_pxbuf.get(tk)
        if not buf or len(buf) <= n: return 0.0
        p0 = buf[-1 - n]; p1 = buf[-1]
        if p0 <= 0: return 0.0
        return p1 / p0 - 1.0

    def _DgInd(self, attr):
        try:
            ind = getattr(self, attr, None)
            if ind is None or not ind.IsReady: return None
            return float(ind.Current.Value)
        except Exception: return None

    def _DgBuildVariants(self, base, eq, spy_px, ema75, ema9, ema120, spy20, bnd20, tip20,
                         regime, prev_reg, ps, ids, dd):
        out = {"BASE": dict(base)}
        # D1 selective rate-shock duration veto
        a_d1 = (str(regime) in ("NEUTRAL", "RISK_OFF")
                and spy_px is not None and ema75 is not None and spy_px < ema75
                and ema9 is not None and ema120 is not None and ema9 < ema120
                and spy20 < 0 and bnd20 < 0 and tip20 < 0)
        w = dict(base)
        if a_d1: w = self._DgZeroGroup(w, eq, "D")
        out["D1"] = w
        # E1 toxic IDS equity cap
        a_e1 = (str(ps) == "NORMAL"
                and str(ids) in ("WATCH", "STRESS")
                and spy_px is not None and ema75 is not None and spy_px < ema75
                and spy20 < 0)
        w = dict(base)
        if a_e1:
            eg = self._DgGross(w, eq, "E")
            if eg > 0.90 and eg > 1e-12:
                w = self._DgScaleGroup(w, eq, "E", 0.90 / eg)
        out["E1"] = w
        # E2 damaged transition equity cap (diag-only hold state; no prod mutation)
        start_e2 = (str(regime) in ("NEUTRAL", "RISK_OFF")
                    and str(prev_reg) == "RISK_ON"
                    and spy_px is not None and ema75 is not None and spy_px < ema75
                    and ema9 is not None and ema120 is not None and ema9 < ema120
                    and dd >= 0.05)
        if self._dg_e2_on:
            stop = ((ema9 is not None and ema120 is not None and ema9 >= ema120)
                    or dd < 0.03 or self._dg_e2_left <= 0)
            if stop:
                self._dg_e2_on = False
                self._dg_e2_left = 0
        if (not self._dg_e2_on) and start_e2:
            self._dg_e2_on = True
            self._dg_e2_left = 20
        a_e2 = bool(self._dg_e2_on)
        if a_e2:
            self._dg_e2_left -= 1
        w = dict(base)
        if a_e2:
            eg = self._DgGross(w, eq, "E")
            if eg > 1.00 and eg > 1e-12:
                w = self._DgScaleGroup(w, eq, "E", 1.00 / eg)
        out["E2"] = w
        return out, {"D1": a_d1, "E1": a_e1, "E2": a_e2}

    def CgDefGrossUpdate(self, combined) -> None:
        if not getattr(self, "cg_def_gross_diag_enable", False): return
        try:
            today = self.time.date()
            if self._dg_last == today: return
            base = self._DgBaseW(combined)
            px = self._DgPx(combined)
            eq = self._DgEqSet()
            for t, p in px.items():
                if t not in self._dg_pxbuf:
                    self._dg_pxbuf[t] = _dg_deque(maxlen=25)
                self._dg_pxbuf[t].append(p)
            spy20 = self._DgBufRet("SPY", 20)
            bnd20 = self._DgBufRet("BND", 20); tip20 = self._DgBufRet("TIP", 20)
            spy_px = px.get("SPY")
            ema75 = self._DgInd("spy_ema_75")
            ema9 = self._DgInd("spy_ema_9")
            ema120 = self._DgInd("spy_ema_120")
            regime = str(getattr(self, "current_regime", None) or "UNKNOWN")
            prev_reg = str(getattr(self, "prev_regime", None) or "")
            ps = str(getattr(self, "_panic_state", "NORMAL") or "NORMAL")
            ids = str(getattr(self, "_ids_state", "NORMAL") or "NORMAL")
            dd = float(self.CurrentDrawdown())
            variants, active = self._DgBuildVariants(
                base, eq, spy_px, ema75, ema9, ema120, spy20, bnd20, tip20,
                regime, prev_reg, ps, ids, dd)
            if self._dg_prev_px is not None:
                for v in _DG_V:
                    pw = self._dg_prev_w.get(v)
                    if pw is None: continue
                    r = self._DgRet(pw, self._dg_prev_px, px)
                    tg = self._DgGross(pw, eq); eg = self._DgGross(pw, eq, "E")
                    dg = self._DgGross(pw, eq, "D"); gg = self._DgGross(pw, eq, "G")
                    c = self._DgCash(pw)
                    act = int(bool(getattr(self, "_dg_prev_act", {}).get(v)))
                    _dg_upd(self._dg_run[v], r, tg, eg, dg, gg, c, act)
                    pd = self._dg_last
                    for name, s, e in _DG_W:
                        ee = e if e is not None else today
                        if pd is not None and s <= pd <= ee:
                            _dg_upd(self._dg_win[(v, name)], r, tg, eg, dg, gg, c, act)
                    if v != "BASE" and act:
                        a = self._dg_act[v]
                        a["n"] += 1
                        br = self._DgRet(self._dg_prev_w.get("BASE") or {}, self._dg_prev_px, px)
                        a["sum_br"] += br; a["sum_vr"] += r
                        if br > 0: a["pos_b"] += 1
                        if br < 0: a["neg_b"] += 1
                        a["nav_b"] *= (1.0 + br); a["nav_v"] *= (1.0 + r)
                        bw = self._dg_prev_w.get("BASE") or {}
                        a["sum_eb"] += self._DgGross(bw, eq, "E")
                        a["sum_ea"] += eg
                        a["sum_db"] += self._DgGross(bw, eq, "D")
                        a["sum_da"] += dg
                        a["sum_cb"] += self._DgCash(bw)
                        a["sum_ca"] += c
                # E1 IDS attribution: prior-day NORMAL + WATCH/STRESS only
                pps = getattr(self, "_dg_prev_ps", None)
                pids = getattr(self, "_dg_prev_ids", None)
                if pps == "NORMAL" and pids in ("WATCH", "STRESS"):
                    for tag, vv in (("BASE", "BASE"), ("E1", "E1")):
                        pw = self._dg_prev_w.get(vv) or {}
                        r = self._DgRet(pw, self._dg_prev_px, px)
                        key = f"{pids}_{tag}"
                        _dg_upd(self._dg_ids[key], r, 0, 0, 0, 0, 0, 0)
                self._dg_n += 1
            for v in _DG_V:
                self._dg_prev_w[v] = variants[v]
            self._dg_prev_px = px
            self._dg_prev_act = active
            self._dg_prev_ps = ps
            self._dg_prev_ids = ids
            self._dg_last = today
        except Exception as e:
            if not self._dg_err:
                self._dg_err = True
                try: self.log(f"[INIT] CG_DEF_D1_ERROR,stage=update,type={type(e).__name__}")
                except Exception: pass

    def _DgEmit(self, lines, line):
        b = len(line.encode("utf-8"))
        if b > 1800:
            line = line[:1780] + "...TRUNC"; b = len(line.encode("utf-8"))
        if self._dg_bytes + b > _DG_BUDGET: return False
        lines.append(line); self._dg_bytes += b
        return True

    def _DgFmt(self, prefix, name, st):
        n = st["n"]
        return (f"{prefix},{name},days={n},nav={_dg_f(st['nav'])},"
                f"cagr={_dg_f(_dg_ann(st['sum_r'], n))},maxdd={_dg_f(st['maxdd'])},"
                f"worst5={_dg_f(_dg_w5(list(st['rets'])),6)},"
                f"vol={_dg_f(_dg_vol(st['sum_r'], st['sum_r2'], n))},"
                f"sharpe={_dg_f(_dg_sh(st['sum_r'], st['sum_r2'], n))},"
                f"avg_total_gross={_dg_f(st['sum_tg']/n if n else None)},"
                f"avg_equity_gross={_dg_f(st['sum_eg']/n if n else None)},"
                f"avg_duration_gross={_dg_f(st['sum_dg']/n if n else None)},"
                f"avg_gold_gross={_dg_f(st['sum_gg']/n if n else None)},"
                f"avg_cash={_dg_f(st['sum_c']/n if n else None)},"
                f"active_days={st['act']}")

    def CgDefGrossEmitFinal(self) -> None:
        if not getattr(self, "cg_def_gross_diag_enable", False): return
        self.log(f"[EOA] CG_DEF_D1_EMIT_START,n={getattr(self,'_dg_n',0)}")
        lines = []; self._dg_bytes = 0
        for v in _DG_V:
            st = self._dg_run[v]
            if st["n"] <= 0:
                self._DgEmit(lines, f"CG_DEF_D1_FINAL,variant={v},status=NO_DATA")
            else:
                self._DgEmit(lines, self._DgFmt("CG_DEF_D1_FINAL", f"variant={v}", st))
        for v in _DG_V:
            for name, _, _ in _DG_W:
                st = self._dg_win[(v, name)]
                if st["n"] <= 0: continue
                if not self._DgEmit(lines, self._DgFmt(
                        "CG_DEF_D1_WINDOW_FINAL", f"variant={v},window={name}", st)):
                    break
        for v in _DG_NV:
            a = self._dg_act[v]; n = a["n"]
            if n <= 0:
                self._DgEmit(lines, f"CG_DEF_D1_ACTIVE_FINAL,variant={v},status=NO_DATA")
                continue
            neg_r = a["neg_b"] / n
            self._DgEmit(lines, (
                f"CG_DEF_D1_ACTIVE_FINAL,variant={v},active_days={n},"
                f"negative_base_days={a['neg_b']},positive_base_days={a['pos_b']},"
                f"negative_rate={_dg_f(neg_r,4)},"
                f"avg_base_return={_dg_f(a['sum_br']/n,6)},"
                f"avg_variant_return={_dg_f(a['sum_vr']/n,6)},"
                f"base_nav_active={_dg_f(a['nav_b'])},variant_nav_active={_dg_f(a['nav_v'])},"
                f"avg_equity_before={_dg_f(a['sum_eb']/n)},avg_equity_after={_dg_f(a['sum_ea']/n)},"
                f"avg_duration_before={_dg_f(a['sum_db']/n)},avg_duration_after={_dg_f(a['sum_da']/n)},"
                f"avg_cash_before={_dg_f(a['sum_cb']/n)},avg_cash_after={_dg_f(a['sum_ca']/n)}"))
        for ids in ("WATCH", "STRESS"):
            b = self._dg_ids[f"{ids}_BASE"]; e = self._dg_ids[f"{ids}_E1"]
            if b["n"] <= 0 and e["n"] <= 0: continue
            nb = max(1, b["n"]); ne = max(1, e["n"])
            self._DgEmit(lines, (
                f"CG_DEF_D1_IDS_FINAL,ids={ids},days={b['n']},"
                f"nav_base={_dg_f(b['nav'])},nav_e1={_dg_f(e['nav'])},"
                f"mean_base={_dg_f(b['sum_r']/nb,6)},mean_e1={_dg_f(e['sum_r']/ne,6)},"
                f"maxdd_base={_dg_f(b['maxdd'])},maxdd_e1={_dg_f(e['maxdd'])},"
                f"worst5_base={_dg_f(_dg_w5(list(b['rets'])),6)},"
                f"worst5_e1={_dg_f(_dg_w5(list(e['rets'])),6)}"))
        # selection
        base = self._dg_run["BASE"]
        b_dd = base["maxdd"]; b_w5 = _dg_w5(list(base["rets"])); b_nav = base["nav"]
        b_oos = self._dg_win[("BASE", "OOS")]
        b_oos_sh = _dg_sh(b_oos["sum_r"], b_oos["sum_r2"], b_oos["n"])
        b_y20 = self._dg_win[("BASE", "Y2020")]["maxdd"]
        b_y22 = self._dg_win[("BASE", "Y2022")]
        b_y15 = self._dg_win[("BASE", "Y2015")]["maxdd"]
        eligible = []; reasons = {}
        for v in _DG_NV:
            st = self._dg_run[v]
            if st["n"] <= 0 or base["n"] <= 0: continue
            c_w5 = _dg_w5(list(st["rets"]))
            c_oos = self._dg_win[(v, "OOS")]
            c_oos_sh = _dg_sh(c_oos["sum_r"], c_oos["sum_r2"], c_oos["n"])
            c_y20 = self._dg_win[(v, "Y2020")]["maxdd"]
            c_y22 = self._dg_win[(v, "Y2022")]
            c_y15 = self._dg_win[(v, "Y2015")]["maxdd"]
            ok = True; why_fail = None
            if st["maxdd"] >= b_dd - 1e-12: ok = False; why_fail = "maxdd"
            elif b_w5 is not None and c_w5 is not None and c_w5 < b_w5 - 1e-12: ok = False; why_fail = "worst5"
            elif st["nav"] < 0.97 * b_nav - 1e-12: ok = False; why_fail = "nav"
            elif b_oos_sh is not None and c_oos_sh is not None and c_oos_sh < b_oos_sh * 0.95: ok = False; why_fail = "oos_sharpe"
            elif c_y20 > b_y20 + 1e-12: ok = False; why_fail = "y2020_dd"
            elif c_y22["maxdd"] > b_y22["maxdd"] + 1e-12: ok = False; why_fail = "y2022_dd"
            elif c_y15 > b_y15 + 1e-12: ok = False; why_fail = "y2015_dd"
            elif self._dg_act[v]["n"] < 20: ok = False; why_fail = "active_days"
            else:
                # variant-specific materiality
                if v == "D1":
                    nav_ok = c_y22["nav"] > b_y22["nav"] + 1e-12
                    dd_ok = c_y22["maxdd"] < b_y22["maxdd"] - 0.005
                    if not (nav_ok or dd_ok): ok = False; why_fail = "d1_2022"
                elif v == "E1":
                    improved = False
                    for ids in ("WATCH", "STRESS"):
                        bb = self._dg_ids[f"{ids}_BASE"]; ee = self._dg_ids[f"{ids}_E1"]
                        if bb["n"] <= 0: continue
                        if (ee["maxdd"] < bb["maxdd"] - 1e-12
                                or ee["nav"] > bb["nav"] + 1e-12
                                or (_dg_w5(list(ee["rets"])) or -9) > (_dg_w5(list(bb["rets"])) or -9)):
                            improved = True
                    if not improved: ok = False; why_fail = "e1_ids"
                elif v == "E2":
                    if c_y15 >= b_y15 - 1e-12: ok = False; why_fail = "e2_2015"
            if ok:
                eligible.append((v, -st["nav"], st)); reasons[v] = "ok"
            else:
                reasons[v] = why_fail or "fail"
        pick = "NONE"; why = "none_eligible"
        if eligible:
            eligible.sort()
            pick = eligible[0][0]; why = f"max_nav|{reasons.get(pick,'ok')}"
        self._DgEmit(lines, (
            f"CG_DEF_D1_SELECT_FINAL,pick={pick},"
            f"eligible={','.join(e[0] for e in eligible) or 'NONE'},"
            f"why={why},trade=0"))
        for ln in lines: self.log(ln)
        self.log(f"[EOA] CG_DEF_D1_EMIT_DONE,lines={len(lines)},bytes={self._dg_bytes}")
