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
# CG-CRISIS-CORE-D0: crisis-type-aware defensive core shadow (diag-only).
# Uses existing GetPanicStructureDiag ptype. Never mutates targets/orders.
# Shadow: T close → T+1 close. BASE = target-shadow, not QC NAV.
# ---------------------------------------------------------------------------
from datetime import date as _cc_date
from collections import deque as _cc_deque

_CC_V = ("BASE", "TYPE_GROUPED", "TYPE_VETO", "TYPE_VETO_PERSIST")
_CC_NV = ("TYPE_GROUPED", "TYPE_VETO", "TYPE_VETO_PERSIST")
_CC_W = (
    ("TRAIN", _cc_date(2012,1,1), _cc_date(2018,12,31)),
    ("OOS", _cc_date(2019,1,1), _cc_date(2021,12,31)),
    ("CRISIS", _cc_date(2022,1,1), _cc_date(2025,12,31)),
    ("Y2012", _cc_date(2012,1,1), _cc_date(2012,12,31)),
    ("Y2015", _cc_date(2015,1,1), _cc_date(2015,12,31)),
    ("Y2020", _cc_date(2020,1,1), _cc_date(2020,12,31)),
    ("Y2022", _cc_date(2022,1,1), _cc_date(2022,12,31)),
    ("Y2023", _cc_date(2023,1,1), _cc_date(2023,12,31)),
    ("Y2024", _cc_date(2024,1,1), _cc_date(2024,12,31)),
    ("Y2025", _cc_date(2025,1,1), _cc_date(2025,12,31)),
    ("LIVE_RECENT", _cc_date(2026,1,1), None),
)
_CC_GMAP = {
    "COMMODITY_INFL": "INFLATION", "COMMODITY_LEAD": "INFLATION", "STAGFLATION_SAFE": "INFLATION",
    "BOND_HEDGED_RISK_OFF": "DEFLATION", "DEFL_RECESSION": "DEFLATION",
    "RATE_SHOCK_UNKNOWN": "RATE_SHOCK",
    "FISCAL_USD": "UNCERTAIN", "UNKNOWN": "UNCERTAIN", "NA": "UNCERTAIN",
}
_CC_GRP = ("INFLATION", "DEFLATION", "RATE_SHOCK", "UNCERTAIN")
_CC_REC = {
    "INFLATION": {"TIP": 0.35, "GLD": 0.30, "BND": 0.05, "CASH": 0.30},
    "DEFLATION": {"TIP": 0.15, "GLD": 0.15, "BND": 0.40, "CASH": 0.30},
    "RATE_SHOCK": {"TIP": 0.05, "GLD": 0.20, "BND": 0.05, "CASH": 0.70},
    "UNCERTAIN": {"TIP": 0.20, "GLD": 0.25, "BND": 0.15, "CASH": 0.40},
}
_CC_CORE = frozenset(("TIP", "GLD", "BND", "BIL", "SGOV", "USFR"))
_CC_CASH = frozenset(("BIL", "SGOV", "USFR"))
_CC_H = (1, 3, 5, 10, 20)
_CC_ASSETS = ("BND", "TIP", "GLD", "CASH", "STATIC_CORE", "SPY")
_CC_BUDGET = 90000


def _cc_blank():
    return {"n": 0, "sum_r": 0.0, "sum_r2": 0.0, "nav": 1.0, "peak": 1.0, "maxdd": 0.0,
            "sum_bnd": 0.0, "sum_tip": 0.0, "sum_gld": 0.0, "sum_cash": 0.0,
            "sw": 0, "turn": 0.0, "rets": _cc_deque(maxlen=4096)}


def _cc_upd(st, r, bnd=0.0, tip=0.0, gld=0.0, cash=0.0, sw=0, turn=0.0):
    st["n"] += 1
    st["sum_r"] += r; st["sum_r2"] += r * r
    st["nav"] = max(0.01, st["nav"] * (1.0 + r))
    if st["nav"] > st["peak"]: st["peak"] = st["nav"]
    dd = 1.0 - st["nav"] / max(st["peak"], 1e-9)
    if dd > st["maxdd"]: st["maxdd"] = dd
    st["sum_bnd"] += bnd; st["sum_tip"] += tip; st["sum_gld"] += gld; st["sum_cash"] += cash
    st["sw"] += sw; st["turn"] += turn
    st["rets"].append(r)


def _cc_w5(rets):
    if not rets: return None
    a = sorted(rets); k = max(1, int(0.05 * len(a) + 0.999))
    return sum(a[:k]) / k


def _cc_ann(s, n):
    return None if n < 20 else (1.0 + s / n) ** 252 - 1.0


def _cc_vol(s, s2, n):
    if n < 5: return None
    m = s / n; v = max(0.0, s2 / n - m * m)
    return (v ** 0.5) * (252 ** 0.5)


def _cc_sh(s, s2, n):
    v = _cc_vol(s, s2, n); a = _cc_ann(s, n)
    if v is None or a is None or v < 1e-12: return None
    return a / v


def _cc_f(x, d=4):
    if x is None: return "NA"
    try: return f"{float(x):.{d}f}"
    except Exception: return "NA"


def _cc_tk(s):
    try: return str(s.Value)
    except Exception:
        try: return str(s.value)
        except Exception: return str(s)


def _cc_med(xs):
    if not xs: return None
    a = sorted(xs); n = len(a); m = n // 2
    return a[m] if n % 2 else 0.5 * (a[m - 1] + a[m])


def _cc_ep_blank():
    return {"days": 0, "eps": [], "cur": 0, "switches": 0, "prev": None}


class CgDefGrossDiagMixin:
    """CRISIS-CORE-D0 via existing diag hooks. Diagnostic-only."""

    def CgDefGrossInit(self) -> None:
        try:
            ov = getattr(self, "_rrx_param_overrides", {}) or {}
            def _p(k, d=""):
                v = self.get_parameter(k)
                if v is None or str(v).strip() == "": v = ov.get(k, d)
                return v
            en = str(_p("cg_crisis_core_diag_enable", "1") or "1").strip().lower()
            self.cg_def_gross_diag_enable = en in ("1", "true", "yes", "on")
            lp = list(getattr(self, "log_only_prefixes", None) or [])
            for pref in ("CG_CRISIS_", "CG_DEF_D2_", "CG_DEF_D1_", "CG_DEF_GROSS_"):
                if pref not in lp: lp.append(pref)
            self.log_only_prefixes = lp
            self.log("[INIT] CG_CRISIS_CORE_DIAG enable="
                     f"{int(self.cg_def_gross_diag_enable)} variants=BASE,TYPE_GROUPED,TYPE_VETO,"
                     f"TYPE_VETO_PERSIST trade=0 conv=T_close_to_T1_close shadow=target_not_qc_nav")
            if not self.cg_def_gross_diag_enable: return
            self._cc_run = {v: _cc_blank() for v in _CC_V}
            self._cc_win = {(v, w[0]): _cc_blank() for v in _CC_V for w in _CC_W}
            self._cc_type = {(v, g): _cc_blank() for v in _CC_V for g in _CC_GRP}
            self._cc_swinfo = {v: {"sw": 0, "gaps": [], "last_sw": None, "one_day": 0,
                                   "post1": [], "post3": [], "post5": [], "turn": 0.0}
                               for v in _CC_NV}
            self._cc_raw_ep = {}
            self._cc_grp_ep = {}
            self._cc_fwd = {}
            self._cc_prev_w = {v: None for v in _CC_V}
            self._cc_prev_px = None
            self._cc_prev_grp = {v: None for v in _CC_V}
            self._cc_prev_raw = None
            self._cc_prev_g = None
            self._cc_pxbuf = {k: _cc_deque(maxlen=30) for k in ("SPY","BND","TIP","GLD","CASH")}
            self._cc_hist = []  # (raw, grp, pxdict, core_w)
            self._cc_conf = "UNCERTAIN"
            self._cc_pend = None
            self._cc_pn = 0
            self._cc_last_raw = None
            self._cc_last_grp = None
            self._cc_last = None
            self._cc_n = 0
            self._cc_bytes = 0
            self._cc_err = False
            self._cc_start = None
            self._cc_prev_core = {}
            self._cc_sw_mark = {v: [] for v in _CC_NV}  # (age_left3, age_left5, acc3, acc5)
        except Exception as e:
            try: self.log(f"[INIT] CG_CRISIS_CORE_ERROR,stage=init,type={type(e).__name__}")
            except Exception: pass

    def _CcCashTk(self):
        s = getattr(self, "sym_cash", None)
        return _cc_tk(s) if s is not None else "BIL"

    def _CcMap(self, raw):
        return _CC_GMAP.get(str(raw or "UNKNOWN"), "UNCERTAIN")

    def _CcEp(self, store, name):
        if name not in store:
            store[name] = _cc_ep_blank()
        return store[name]

    def _CcEpUp(self, store, name, last_attr):
        cur = getattr(self, last_attr, None)
        if cur != name:
            if cur is not None and cur in store:
                st = store[cur]
                if st["cur"] > 0: st["eps"].append(st["cur"]); st["switches"] += 1
                st["cur"] = 0
            st = self._CcEp(store, name)
            st["cur"] = 1; st["days"] += 1
            setattr(self, last_attr, name)
        else:
            st = self._CcEp(store, name); st["cur"] += 1; st["days"] += 1

    def _CcCloseEps(self, store):
        for st in store.values():
            if st["cur"] > 0: st["eps"].append(st["cur"]); st["cur"] = 0

    def _CcCoreW(self, targets):
        w = {"TIP": 0.0, "GLD": 0.0, "BND": 0.0, "CASH": 0.0}
        ct = self._CcCashTk()
        for s, wt in (targets or {}).items():
            try: wf = float(wt or 0.0)
            except Exception: continue
            t = _cc_tk(s)
            if t == "TIP": w["TIP"] += wf
            elif t == "GLD": w["GLD"] += wf
            elif t == "BND": w["BND"] += wf
            elif t in _CC_CASH or t == ct: w["CASH"] += wf
        return w

    def _CcGross(self, cw):
        return abs(cw["TIP"]) + abs(cw["GLD"]) + abs(cw["BND"]) + abs(cw["CASH"])

    def _CcRecipe(self, grp, gross, bnd20, tip20, gld20, veto=False):
        rec = dict(_CC_REC.get(grp, _CC_REC["UNCERTAIN"]))
        if veto:
            if bnd20 < 0: rec["CASH"] += rec["BND"]; rec["BND"] = 0.0
            if tip20 < 0: rec["CASH"] += rec["TIP"]; rec["TIP"] = 0.0
            if gld20 < 0: rec["CASH"] += rec["GLD"]; rec["GLD"] = 0.0
        s = rec["TIP"] + rec["GLD"] + rec["BND"] + rec["CASH"]
        if s <= 1e-12: rec = {"TIP": 0.0, "GLD": 0.0, "BND": 0.0, "CASH": 1.0}; s = 1.0
        g = max(0.0, float(gross))
        return {k: g * (rec[k] / s) for k in ("TIP", "GLD", "BND", "CASH")}

    def _CcMerge(self, combined, core_new, core_old):
        out = {}
        for s, wt in (combined or {}).items():
            try: out[_cc_tk(s)] = float(wt or 0.0)
            except Exception: continue
        ct = self._CcCashTk()
        for k, tk in (("TIP", "TIP"), ("GLD", "GLD"), ("BND", "BND"), ("CASH", ct)):
            out[tk] = float(out.get(tk, 0.0) or 0.0) - float(core_old.get(k, 0.0) or 0.0)
            out[tk] = float(out.get(tk, 0.0) or 0.0) + float(core_new.get(k, 0.0) or 0.0)
        return out

    def _CcPx(self):
        px = {}
        for attr, tk in (("sym_spy","SPY"),("sym_bnd","BND"),("sym_tip","TIP"),
                         ("sym_gld","GLD"),("sym_cash","CASH")):
            s = getattr(self, attr, None)
            if s is None: continue
            try:
                p = float(self.securities[s].price)
                if p > 0: px[tk] = p
            except Exception: pass
        return px

    def _CcRetMap(self, w, p0, p1):
        r = 0.0
        ct = self._CcCashTk()
        for t, wt in (w or {}).items():
            if t == "CASH" or t in _CC_CASH or t == ct: continue
            a = (p0 or {}).get(t); b = (p1 or {}).get(t)
            if not a or not b or a <= 0: continue
            try: r += float(wt or 0.0) * (b / a - 1.0)
            except Exception: pass
        return r

    def _CcPersist(self, g):
        if g == self._cc_conf:
            self._cc_pend = None; self._cc_pn = 0
            return self._cc_conf
        if g == self._cc_pend: self._cc_pn += 1
        else: self._cc_pend = g; self._cc_pn = 1
        need = 3 if self._cc_conf == "UNCERTAIN" else 2
        if self._cc_pn >= need:
            self._cc_conf = self._cc_pend
            self._cc_pend = None; self._cc_pn = 0
        return self._cc_conf

    def _CcTurn(self, a, b):
        return 0.5 * sum(abs(float((a or {}).get(k, 0.0) or 0.0) - float((b or {}).get(k, 0.0) or 0.0))
                         for k in ("TIP", "GLD", "BND", "CASH"))

    def _CcFwdAdd(self, level, typ, asset, h, ret):
        k = (level, typ, asset)
        if k not in self._cc_fwd: self._cc_fwd[k] = {hh: [] for hh in _CC_H}
        self._cc_fwd[k][h].append(ret)

    def CgDefGrossUpdate(self, combined) -> None:
        if not getattr(self, "cg_def_gross_diag_enable", False): return
        try:
            today = self.time.date()
            if self._cc_last == today: return
            if self._cc_start is None: self._cc_start = today
            # existing classifier — read only
            try:
                ps = self.GetPanicStructureDiag()
                raw = str((ps or {}).get("ptype") or "UNKNOWN")
            except Exception:
                raw = "UNKNOWN"
            if raw in ("", "None"): raw = "UNKNOWN"
            grp = self._CcMap(raw)
            core_t = getattr(self, "_last_core_targets", None) or {}
            core_old = self._CcCoreW(core_t)
            gross = self._CcGross(core_old)
            px = self._CcPx()
            for k in self._cc_pxbuf:
                if k in px: self._cc_pxbuf[k].append(px[k])
            # 20d returns known at T
            def _r20(tk):
                b = self._cc_pxbuf.get(tk)
                if not b or len(b) <= 20 or b[-21] <= 0: return 0.0
                return b[-1] / b[-21] - 1.0
            bnd20 = _r20("BND"); tip20 = _r20("TIP"); gld20 = _r20("GLD")
            # persistence shadow state
            g_persist = self._CcPersist(grp)
            # build variant cores
            cores = {
                "BASE": dict(core_old),
                "TYPE_GROUPED": self._CcRecipe(grp, gross, bnd20, tip20, gld20, False),
                "TYPE_VETO": self._CcRecipe(grp, gross, bnd20, tip20, gld20, True),
                "TYPE_VETO_PERSIST": self._CcRecipe(g_persist, gross, bnd20, tip20, gld20, True),
            }
            groups_used = {
                "BASE": grp,
                "TYPE_GROUPED": grp,
                "TYPE_VETO": grp,
                "TYPE_VETO_PERSIST": g_persist,
            }
            variants = {}
            for v in _CC_V:
                if v == "BASE":
                    # exact combined targets (ticker map)
                    w = {}
                    for s, wt in (combined or {}).items():
                        try: w[_cc_tk(s)] = float(wt or 0.0)
                        except Exception: continue
                    variants[v] = w
                else:
                    variants[v] = self._CcMerge(combined, cores[v], core_old)
            # episode tracking raw/group
            self._CcEpUp(self._cc_raw_ep, raw, "_cc_last_raw")
            self._CcEpUp(self._cc_grp_ep, grp, "_cc_last_grp")
            # hist for forward returns
            self._cc_hist.append((raw, grp, dict(px), dict(core_old)))
            if len(self._cc_hist) > 40: self._cc_hist = self._cc_hist[-40:]
            i = len(self._cc_hist) - 1
            for h in _CC_H:
                j = i - h
                if j < 0: continue
                raw0, grp0, px0, core0 = self._cc_hist[j]
                px1 = self._cc_hist[i][2]
                for level, typ in (("RAW", raw0), ("GROUP", grp0)):
                    for asset in _CC_ASSETS:
                        if asset == "STATIC_CORE":
                            rr = 0.0; den = 0.0
                            for kk, pk in (("TIP","TIP"),("GLD","GLD"),("BND","BND")):
                                a = px0.get(pk); b = px1.get(pk); w = float(core0.get(kk, 0.0) or 0.0)
                                if a and b and a > 0 and abs(w) > 0:
                                    rr += w * (b / a - 1.0); den += abs(w)
                            ret = (rr / den) if den > 1e-12 else 0.0
                        elif asset == "CASH":
                            ret = 0.0
                        else:
                            a = px0.get(asset); b = px1.get(asset)
                            if not a or not b or a <= 0: continue
                            ret = b / a - 1.0
                        self._CcFwdAdd(level, typ, asset, h, ret)
            # realize prior day shadow returns
            if self._cc_prev_px is not None:
                for v in _CC_V:
                    pw = self._cc_prev_w.get(v)
                    if pw is None: continue
                    r = self._CcRetMap(pw, self._cc_prev_px, px)
                    cw = self._cc_prev_core.get(v) or core_old
                    bnd=float(cw.get("BND",0)); tip=float(cw.get("TIP",0))
                    gld=float(cw.get("GLD",0)); cash=float(cw.get("CASH",0))
                    prev_g = self._cc_prev_grp.get(v); cur_g = groups_used[v]
                    sw = 1 if (prev_g is not None and prev_g != cur_g) else 0
                    turn = self._CcTurn(getattr(self, "_cc_prev_core2", {}).get(v), cw)
                    _cc_upd(self._cc_run[v], r, bnd, tip, gld, cash, sw, turn)
                    pd = self._cc_last
                    for name, s, e in _CC_W:
                        ee = e if e is not None else today
                        if pd is not None and s <= pd <= ee:
                            _cc_upd(self._cc_win[(v, name)], r, bnd, tip, gld, cash, sw, turn)
                    tg = getattr(self, "_cc_prev_g", grp)
                    if tg in _CC_GRP: _cc_upd(self._cc_type[(v, tg)], r, bnd, tip, gld, cash)
                    if v in _CC_NV:
                        marks = []
                        for left3, left5, acc3, acc5 in self._cc_sw_mark[v]:
                            acc3 = (1+acc3)*(1+r)-1; acc5 = (1+acc5)*(1+r)-1
                            left3 -= 1; left5 -= 1
                            si = self._cc_swinfo[v]
                            if left3 == 0: si.setdefault("post3", []).append(acc3)
                            if left5 == 0: si.setdefault("post5", []).append(acc5)
                            if left5 > 0: marks.append((left3, left5, acc3, acc5))
                        if sw:
                            si = self._cc_swinfo[v]; si["sw"] += 1
                            if si["last_sw"] is not None:
                                try: gap = (pd - si["last_sw"]).days
                                except Exception: gap = 1
                                si["gaps"].append(max(1, int(gap)))
                                if gap <= 1: si["one_day"] += 1
                            si["last_sw"] = pd; si["turn"] += turn; si["post1"].append(r)
                            marks.append((2, 4, r, r))
                        self._cc_sw_mark[v] = marks
                self._cc_n += 1
            self._cc_prev_core2 = {k: dict(v) for k, v in self._cc_prev_core.items()}
            for v in _CC_V:
                self._cc_prev_w[v] = variants[v]
                self._cc_prev_grp[v] = groups_used[v]
                self._cc_prev_core[v] = dict(cores[v] if v != "BASE" else core_old)
            self._cc_prev_px = px; self._cc_prev_raw = raw; self._cc_prev_g = grp
            self._cc_last = today
        except Exception as e:
            if not self._cc_err:
                self._cc_err = True
                try: self.log(f"[INIT] CG_CRISIS_CORE_ERROR,stage=update,type={type(e).__name__}")
                except Exception: pass

    def _CcEmit(self, lines, line):
        b = len(line.encode("utf-8"))
        if b > 1800:
            line = line[:1780] + "...TRUNC"; b = len(line.encode("utf-8"))
        if self._cc_bytes + b > _CC_BUDGET: return False
        lines.append(line); self._cc_bytes += b
        return True

    def _CcStat3(self, xs):
        if not xs: return "NA/NA/NA"
        m = sum(xs) / len(xs); med = _cc_med(xs); pr = sum(1 for x in xs if x > 0) / len(xs)
        return f"{_cc_f(m,6)}/{_cc_f(med,6)}/{_cc_f(pr,3)}"

    def _CcFmt(self, prefix, name, st, extra=""):
        n = max(1, st["n"])
        yrs = max(1e-9, st["n"] / 252.0)
        return (f"{prefix},{name},days={st['n']},nav={_cc_f(st['nav'])},"
                f"cagr={_cc_f(_cc_ann(st['sum_r'], st['n']))},maxdd={_cc_f(st['maxdd'])},"
                f"worst5={_cc_f(_cc_w5(list(st['rets'])),6)},"
                f"vol={_cc_f(_cc_vol(st['sum_r'], st['sum_r2'], st['n']))},"
                f"sharpe={_cc_f(_cc_sh(st['sum_r'], st['sum_r2'], st['n']))},"
                f"avg_bnd={_cc_f(st['sum_bnd']/n)},avg_tip={_cc_f(st['sum_tip']/n)},"
                f"avg_gld={_cc_f(st['sum_gld']/n)},avg_cash={_cc_f(st['sum_cash']/n)},"
                f"switches={st['sw']},turnover_proxy={_cc_f(st['turn']/yrs)}{extra}")

    def CgDefGrossEmitFinal(self) -> None:
        if not getattr(self, "cg_def_gross_diag_enable", False): return
        self.log(f"[EOA] CG_CRISIS_CORE_EMIT_START,n={getattr(self,'_cc_n',0)}")
        lines = []; self._cc_bytes = 0
        self._CcCloseEps(self._cc_raw_ep); self._CcCloseEps(self._cc_grp_ep)
        # persistence / classifier quality
        for level, store in (("RAW", self._cc_raw_ep), ("GROUP", self._cc_grp_ep)):
            for name, st in store.items():
                eps = st["eps"] or ([st["cur"]] if st["cur"] else [])
                if not eps and st["days"] <= 0: continue
                avg = sum(eps) / len(eps) if eps else 0.0
                med = _cc_med(eps) or 0.0
                one = sum(1 for x in eps if x == 1)
                short = sum(1 for x in eps if x <= 3)
                if not self._CcEmit(lines, (
                    f"CG_CRISIS_TYPE_PERSIST_FINAL,level={level},type={name},"
                    f"days={st['days']},episodes={len(eps)},avg_days={_cc_f(avg,2)},"
                    f"median_days={_cc_f(med,2)},max_days={max(eps) if eps else 0},"
                    f"one_day={one},short_le3={short},switches={st['switches']}")):
                    break
        # forward asset returns
        for (level, typ, asset), hs in self._cc_fwd.items():
            parts = [f"CG_CRISIS_TYPE_FWD_FINAL,level={level},type={typ},asset={asset},n={len(hs.get(1) or [])}"]
            for h, lab in ((1,"d1"),(3,"d3"),(5,"d5"),(10,"d10"),(20,"d20")):
                parts.append(f"{lab}={self._CcStat3(hs.get(h) or [])}")
            if not self._CcEmit(lines, ",".join(parts)): break
        # finals
        for v in _CC_V:
            st = self._cc_run[v]
            if st["n"] <= 0:
                self._CcEmit(lines, f"CG_CRISIS_CORE_FINAL,variant={v},status=NO_DATA")
            else:
                self._CcEmit(lines, self._CcFmt("CG_CRISIS_CORE_FINAL", f"variant={v}", st))
        for v in _CC_V:
            for name, _, _ in _CC_W:
                st = self._cc_win[(v, name)]
                if st["n"] <= 0: continue
                if not self._CcEmit(lines, self._CcFmt(
                        "CG_CRISIS_CORE_WINDOW_FINAL", f"variant={v},window={name}", st)):
                    break
        for v in _CC_V:
            for g in _CC_GRP:
                st = self._cc_type[(v, g)]
                if st["n"] <= 0: continue
                n = st["n"]
                if not self._CcEmit(lines, (
                    f"CG_CRISIS_CORE_TYPE_FINAL,variant={v},type={g},days={n},"
                    f"nav={_cc_f(st['nav'])},mean={_cc_f(st['sum_r']/n,6)},"
                    f"maxdd={_cc_f(st['maxdd'])},worst5={_cc_f(_cc_w5(list(st['rets'])),6)},"
                    f"avg_bnd={_cc_f(st['sum_bnd']/n)},avg_tip={_cc_f(st['sum_tip']/n)},"
                    f"avg_gld={_cc_f(st['sum_gld']/n)},avg_cash={_cc_f(st['sum_cash']/n)}")):
                    break
        for v in _CC_NV:
            si = self._cc_swinfo[v];             n = max(1, si["sw"])
            avg_gap = (sum(si["gaps"]) / len(si["gaps"])) if si["gaps"] else None
            yrs = max(1e-9, self._cc_run[v]["n"] / 252.0)
            p3 = si.get("post3") or []; p5 = si.get("post5") or []
            self._CcEmit(lines, (
                f"CG_CRISIS_CORE_SWITCH_FINAL,variant={v},switches={si['sw']},"
                f"avg_days_between={_cc_f(avg_gap,2)},one_day_switches={si['one_day']},"
                f"turnover_proxy={_cc_f(si['turn']/yrs)},"
                f"return_1d_after_switch={_cc_f((sum(si['post1'])/len(si['post1'])) if si['post1'] else None,6)},"
                f"return_3d_after_switch={_cc_f((sum(p3)/len(p3)) if p3 else None,6)},"
                f"return_5d_after_switch={_cc_f((sum(p5)/len(p5)) if p5 else None,6)}"))
        # selection
        base = self._cc_run["BASE"]
        b_nav, b_dd = base["nav"], base["maxdd"]
        b_w5 = _cc_w5(list(base["rets"]))
        b_oos = self._cc_win[("BASE", "OOS")]
        b_oos_sh = _cc_sh(b_oos["sum_r"], b_oos["sum_r2"], b_oos["n"])
        b_y20 = self._cc_win[("BASE", "Y2020")]["maxdd"]
        b_y22 = self._cc_win[("BASE", "Y2022")]["maxdd"]
        b_y15 = self._cc_win[("BASE", "Y2015")]["maxdd"]
        eligible = []
        for v in _CC_NV:
            st = self._cc_run[v]
            if st["n"] <= 0 or base["n"] <= 0: continue
            c_w5 = _cc_w5(list(st["rets"]))
            c_oos = self._cc_win[(v, "OOS")]
            c_oos_sh = _cc_sh(c_oos["sum_r"], c_oos["sum_r2"], c_oos["n"])
            yrs = max(1e-9, st["n"] / 252.0)
            tpy = st["turn"] / yrs
            ok = True
            if st["nav"] < 0.98 * b_nav - 1e-12: ok = False
            elif st["maxdd"] > b_dd + 1e-12: ok = False
            elif b_w5 is not None and c_w5 is not None and c_w5 < b_w5 - 1e-12: ok = False
            elif b_oos_sh is not None and c_oos_sh is not None and c_oos_sh < b_oos_sh * 0.97: ok = False
            elif self._cc_win[(v, "Y2020")]["maxdd"] > b_y20 + 1e-12: ok = False
            elif self._cc_win[(v, "Y2022")]["maxdd"] >= b_y22 - 1e-12: ok = False
            elif self._cc_win[(v, "Y2015")]["maxdd"] > b_y15 + 1e-12: ok = False
            elif st["sw"] > 250: ok = False
            elif tpy > 0.50 + 1e-12: ok = False
            else:
                if v == "TYPE_GROUPED":
                    improved = False
                    for g in _CC_GRP:
                        bb = self._cc_type[("BASE", g)]; vv = self._cc_type[(v, g)]
                        if bb["n"] <= 0 or vv["n"] <= 0: continue
                        if (vv["maxdd"] < bb["maxdd"] - 0.002) or (vv["nav"] > bb["nav"] + 0.005):
                            improved = True
                    if not improved: ok = False
                elif v == "TYPE_VETO":
                    bb = self._cc_type[("BASE", "RATE_SHOCK")]; vv = self._cc_type[(v, "RATE_SHOCK")]
                    if bb["n"] <= 0 or not ((vv["maxdd"] < bb["maxdd"] - 1e-12) or (vv["nav"] > bb["nav"] + 1e-12)):
                        ok = False
                elif v == "TYPE_VETO_PERSIST":
                    sw_v = self._cc_swinfo["TYPE_VETO"]["sw"]
                    sw_p = self._cc_swinfo["TYPE_VETO_PERSIST"]["sw"]
                    if sw_v <= 0 or sw_p > sw_v * 0.75 + 1e-12: ok = False
            if ok: eligible.append((v, -st["nav"]))
        pick = "NONE"; why = "none_eligible"
        if eligible:
            eligible.sort(); pick = eligible[0][0]; why = "max_nav_among_eligible"
        self._CcEmit(lines, (
            f"CG_CRISIS_CORE_SELECT_FINAL,pick={pick},"
            f"eligible={','.join(e[0] for e in eligible) or 'NONE'},why={why},trade=0"))
        for ln in lines: self.log(ln)
        self.log(f"[EOA] CG_CRISIS_CORE_EMIT_DONE,lines={len(lines)},bytes={self._cc_bytes}")
