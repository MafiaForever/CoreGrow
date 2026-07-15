# rrx_d8_switch_diag.py
# Tags: [RRX][D8]
# ALLOC-SPYG-SAT-D0 diagnostic only. Zero trading impact.
# Completed shadow candidates moved to trading/retired; keep alloc collector only.

from AlgorithmImports import *


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


# ---------------------------------------------------------------------------
# Initialize — ALLOC-SPYG-SAT-D0 only
# ---------------------------------------------------------------------------

def D8SwitchDiagInitialize(self) -> None:
    """[D8] Initialize SPYG satellite allocation shadow collector."""
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
    self.rrx_eqwin_enable = _gb("rrx_eqwin_enable", 1)
    if getattr(self, "cg_fast_baseline_mode", False):
        _fd = getattr(self, "_cg_fast_disabled", None)
        if self.rrx_eqwin_enable:
            self.rrx_eqwin_enable = False
            if _fd is not None: _fd.append("rrx_eqwin_enable")
    # ALLOC-SPYG-SAT-D0: base portfolio vs configurable SPYG satellite cap.
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
    if self.rrx_eqwin_enable:
        for _chunk in _wdef.split("|"):
            _p = [x.strip() for x in _chunk.split(":")]
            if len(_p) == 3 and _p[0] and _p[1] and _p[2]:
                self._d8_diag_windows.append((_p[0], _p[1], _p[2]))
                self._d8_alloc_win[_p[0]] = {"base": _d8_curve_blank(), "sat20": _d8_curve_blank()}
    self.log("RRX_D8_INIT,alloc_spyg_sat_diag,diag_only=1,no_trading=1")


# ---------------------------------------------------------------------------
# Daily update — ALLOC-SPYG-SAT-D0 only
# ---------------------------------------------------------------------------

def D8SwitchDiagUpdate(self, tf_sym=None, spy20: float = 0.0) -> None:
    """[D8] Daily SPYG satellite allocation shadow update."""
    today = self.time.date()
    yr    = today.year
    today_s = today.isoformat()
    win_today = []
    for _wn, _st, _en in getattr(self, "_d8_diag_windows", []):
        if _st <= today_s <= _en:
            win_today.append(_wn)

    spyg_rets = getattr(self, "_d6rm_spyg_rets", []) or []

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
        next_sym = getattr(self, "_d6rm_spyg_held", None)
        next_sigw = max(0.0, min(1.0, float(getattr(self, "_d6rm_spyg_weight", 0.0) or 0.0)))
        try:
            min_tgt = float(self._SpygSatFloat("spyg_sat_min_target", 0.002))
        except Exception:
            min_tgt = 0.002
        if block or next_sym is None or cap * next_sigw < min_tgt:
            next_sigw = 0.0
        self._d8_sat_prev_sigw = next_sigw

    if pv > 0: self._d8_prev_pv = pv


# ---------------------------------------------------------------------------
# Emit — ALLOC-SPYG-SAT-D0 only
# ---------------------------------------------------------------------------

def D8SwitchDiagEmitFinal(self, start, today) -> None:
    """[D8] Final SPYG satellite allocation summary."""
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

