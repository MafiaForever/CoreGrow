# cg_core_recovery_diag.py
# CORE-D0.2: compact CoreGrowth recovery diagnostics. Diagnostic-only. Zero trading impact.
from datetime import date as _date
from collections import deque

_CORE_WINDOWS = (
    ("TRAIN",       _date(2012, 1, 1), _date(2018, 12, 31)),
    ("OOS",         _date(2019, 1, 1), _date(2021, 12, 31)),
    ("CRISIS",      _date(2022, 1, 1), _date(2025, 12, 31)),
    ("Y2020",       _date(2020, 1, 1), _date(2020, 12, 31)),
    ("Y2022",       _date(2022, 1, 1), _date(2022, 12, 31)),
    ("Y2023",       _date(2023, 1, 1), _date(2023, 12, 31)),
    ("Y2024",       _date(2024, 1, 1), _date(2024, 12, 31)),
    ("Y2025",       _date(2025, 1, 1), _date(2025, 12, 31)),
    ("LIVE_RECENT", _date(2026, 1, 1), None),
)
_REGIMES = ("RISK_ON", "NEUTRAL", "RISK_OFF", "RISKOFF_REC", "POST_PANIC", "PANIC", "UNKNOWN")
_TIMING_TYPES = ("RISK_INCREASE", "RISK_DECREASE", "FULL_EXIT", "REENTRY",
                 "DEFENSIVE_ENTRY", "DEFENSIVE_EXIT")
_EXP_BUCKETS = ("CASHY", "DEFENSIVE", "NORMAL", "LEVERED")
_LOG_BUDGET = 90000
_LINE_MAX = 1800
_DD_MAX = 8
_STATE_MAX = 10


def _blank_stats():
    return {"n": 0, "sum_r": 0.0, "sum_r2": 0.0, "nav_m": 1.0, "peak": 1.0, "maxdd": 0.0,
            "worst": 0.0, "pos": 0, "sum_g": 0.0, "sum_c": 0.0, "rets": deque(maxlen=64),
            "entries": 0, "exits": 0}


def _upd_stats(st, r, gross, cash):
    st["n"] += 1
    st["sum_r"] += r
    st["sum_r2"] += r * r
    st["nav_m"] = max(0.01, st["nav_m"] * (1.0 + r))
    if st["nav_m"] > st["peak"]:
        st["peak"] = st["nav_m"]
    dd = 1.0 - st["nav_m"] / max(st["peak"], 1e-9)
    if dd > st["maxdd"]:
        st["maxdd"] = dd
    if r < st["worst"]:
        st["worst"] = r
    if r > 0:
        st["pos"] += 1
    st["sum_g"] += gross
    st["sum_c"] += cash
    st["rets"].append(r)


def _w5_mean(rets):
    if not rets:
        return None
    arr = sorted(rets)
    k = max(1, int(0.05 * len(arr) + 0.999))
    return sum(arr[:k]) / k


def _ann(sum_r, n):
    if n < 20:
        return None
    return (1.0 + sum_r / n) ** 252 - 1.0


def _vol(sum_r, sum_r2, n):
    if n < 5:
        return None
    m = sum_r / n
    v = max(0.0, sum_r2 / n - m * m)
    return (v ** 0.5) * (252 ** 0.5)


def _sharpe(sum_r, sum_r2, n):
    v = _vol(sum_r, sum_r2, n)
    a = _ann(sum_r, n)
    if v is None or a is None or v < 1e-12:
        return None
    return a / v


def _f(x, d=4):
    if x is None:
        return "NA"
    try:
        return f"{float(x):.{d}f}"
    except Exception:
        return "NA"


def _ticker(sym):
    try:
        return str(sym.Value)
    except Exception:
        try:
            return str(sym.value)
        except Exception:
            return str(sym)


class CgCoreRecoveryDiagMixin:

    def CgCoreRecoveryInit(self) -> None:
        ov = getattr(self, "_rrx_param_overrides", {}) or {}
        def _p(k, d=""):
            v = self.get_parameter(k)
            if v is None or str(v).strip() == "":
                v = ov.get(k, d)
            return v
        def _gb(k, d):
            return str(_p(k, d) or d).strip().lower() in ("1", "true", "yes", "on")
        def _gf(k, d):
            try:
                return float(_p(k, d) if _p(k, d) not in (None, "") else d)
            except Exception:
                return float(d)
        self.cg_core_recovery_diag_enable = _gb("cg_core_recovery_diag_enable", "1")
        self.cg_core_diag_event_weight_threshold = _gf("cg_core_diag_event_weight_threshold", 0.05)
        self.cg_core_diag_cash_threshold = _gf("cg_core_diag_cash_threshold", 0.20)
        self.cg_core_diag_bad_cash_spy5 = _gf("cg_core_diag_bad_cash_spy5", 0.02)
        self.cg_core_diag_exp_cashy = _gf("cg_core_diag_exp_cashy", 0.35)
        self.cg_core_diag_exp_def = _gf("cg_core_diag_exp_def", 0.70)
        self.cg_core_diag_exp_lev = _gf("cg_core_diag_exp_lev", 1.10)
        sat = 1 if getattr(self, "_cg_spyg_sat_flag", False) else 0
        fast = 1 if getattr(self, "cg_fast_baseline_mode", False) else 0
        self.log(f"[INIT] CG_CORE_RECOVERY_DIAG enable={int(self.cg_core_recovery_diag_enable)} "
                 f"sat_trade={sat} fast_mode={fast}")
        if not self.cg_core_recovery_diag_enable:
            return
        self._crd_start = None
        self._crd_end = None
        self._crd_prev_nav = None
        self._crd_prev_spy_px = None
        self._crd_prev_gross = None
        self._crd_prev_spy_w = None
        self._crd_prev_def_w = None
        self._crd_prev_cash_w = None
        self._crd_peak_nav = None
        self._crd_peak_date = None
        self._crd_trough_nav = None
        self._crd_trough_date = None
        self._crd_in_dd = False
        self._crd_dd_list = []
        self._crd_open_dd = None
        self._crd_max_rec_days = 0
        self._crd_reg = {r: _blank_stats() for r in _REGIMES}
        self._crd_states = {}
        self._crd_win = {w[0]: _blank_stats() for w in _CORE_WINDOWS}
        self._crd_exp = {b: _blank_stats() for b in _EXP_BUCKETS}
        self._crd_timing = {t: {"n": 0, "fwd": {h: [] for h in (1, 3, 5, 10, 20)}}
                            for t in _TIMING_TYPES}
        self._crd_pending = []
        self._crd_cash = {r: {"n": 0, "sum_c": 0.0, "sum_pr": 0.0, "sum_sr": 0.0,
                              "good": 0, "bad": 0, "opp": 0.0} for r in _REGIMES}
        self._crd_cash_pend = []
        self._crd_spy_rets = deque(maxlen=30)
        self._crd_port_rets = deque(maxlen=30)
        self._crd_dates = deque(maxlen=30)
        self._crd_moc = 0
        self._crd_diag_block = 0
        self._crd_n_days = 0
        self._crd_sum_r = 0.0
        self._crd_sum_r2 = 0.0
        self._crd_nav_m = 1.0
        self._crd_peak_m = 1.0
        self._crd_maxdd = 0.0
        self._crd_orders = 0
        self._crd_log_bytes = 0

    def _CrdW(self, combined, sym):
        if sym is None or not combined:
            return 0.0
        try:
            return float(combined.get(sym, 0.0) or 0.0)
        except Exception:
            return 0.0

    def _CrdWeights(self, combined):
        spy = self._CrdW(combined, getattr(self, "sym_spy", None))
        sh = self._CrdW(combined, getattr(self, "sym_sh", None))
        cash = 0.0
        for s in (getattr(self, "sym_cash", None), getattr(self, "sym_crash", None)):
            cash += max(0.0, self._CrdW(combined, s))
        defensive = 0.0
        for attr in ("sym_gld", "sym_bnd", "sym_tip", "sym_dbc"):
            defensive += max(0.0, self._CrdW(combined, getattr(self, attr, None)))
        tactical = 0.0
        act = getattr(self, "_active_tactical_symbol", None)
        for s in getattr(self, "panic_tactical_universe", []) or []:
            tactical += max(0.0, self._CrdW(combined, s))
        invested = 0.0
        nz = 0
        cash_syms = {getattr(self, "sym_cash", None), getattr(self, "sym_crash", None)}
        for s, w in (combined or {}).items():
            try:
                wf = float(w or 0.0)
            except Exception:
                continue
            if abs(wf) > 1e-8:
                nz += 1
            if s not in cash_syms:
                invested += abs(wf)
        return spy, sh, cash, defensive, tactical, invested, nz, act

    def _CrdExpBucket(self, g):
        if g < self.cg_core_diag_exp_cashy:
            return "CASHY"
        if g < self.cg_core_diag_exp_def:
            return "DEFENSIVE"
        if g <= self.cg_core_diag_exp_lev:
            return "NORMAL"
        return "LEVERED"

    def _CrdStateKey(self):
        return (f"panic={getattr(self,'_panic_state','NORMAL')}|"
                f"ids={getattr(self,'_ids_state','NORMAL')}|"
                f"shock={int(bool(getattr(self,'short_shock_flag',False)))}|"
                f"estop={int(bool(getattr(self,'emergency_stop_triggered',False)))}")

    def CgCoreRecoveryOnOrder(self, order_event) -> None:
        if not getattr(self, "cg_core_recovery_diag_enable", False):
            return
        try:
            self._crd_orders = getattr(self, "_crd_orders", 0) + 1
            oid = getattr(order_event, "order_id", None)
            if oid is None:
                return
            o = self.transactions.get_order_by_id(oid)
            if o is None:
                return
            ot = getattr(o, "type", None)
            if ot == OrderType.MARKET_ON_CLOSE or str(ot).upper().endswith("MARKET_ON_CLOSE"):
                self._crd_moc = getattr(self, "_crd_moc", 0) + 1
        except Exception:
            pass

    def CgCoreRecoveryUpdate(self, combined) -> None:
        """Daily update after final targets, before ExecuteTargets. Diagnostic-only."""
        if not getattr(self, "cg_core_recovery_diag_enable", False):
            return
        try:
            today = self.time.date()
            nav = float(self.portfolio.total_portfolio_value)
            if nav <= 0:
                return
            if self._crd_start is None:
                self._crd_start = today
            self._crd_end = today
            prev = self._crd_prev_nav
            r = 0.0 if prev is None or prev <= 0 else (nav / prev - 1.0)
            spy_sym = getattr(self, "sym_spy", None)
            try:
                spy_px = float(self.securities[spy_sym].price) if spy_sym else 0.0
            except Exception:
                spy_px = 0.0
            spy_r = 0.0
            if self._crd_prev_spy_px and self._crd_prev_spy_px > 0 and spy_px > 0:
                spy_r = spy_px / self._crd_prev_spy_px - 1.0
            spy_w, sh_w, cash_w, def_w, tac_w, gross, nz, act = self._CrdWeights(combined)
            # count diagnostic trade leaks without modifying targets
            for s, w in (combined or {}).items():
                try:
                    wf = float(w or 0.0)
                except Exception:
                    continue
                if abs(wf) > 1e-8 and self._CgSymbolBlockedForTrade(s):
                    self._crd_diag_block += 1
            regime = str(getattr(self, "current_regime", None) or "UNKNOWN")
            if regime not in self._crd_reg:
                regime = "UNKNOWN"
            panic = str(getattr(self, "_panic_state", "NORMAL") or "NORMAL")
            ids = str(getattr(self, "_ids_state", "NORMAL") or "NORMAL")
            dd = 0.0
            if self._crd_peak_nav is None or nav >= self._crd_peak_nav:
                if self._crd_in_dd and self._crd_open_dd is not None:
                    ep = self._crd_open_dd
                    ep["rec_date"] = today
                    ep["rec_days"] = (today - ep["trough_date"]).days
                    if ep["rec_days"] > self._crd_max_rec_days:
                        self._crd_max_rec_days = ep["rec_days"]
                    self._crd_dd_list.append(ep)
                    self._crd_dd_list = sorted(self._crd_dd_list, key=lambda x: -x["depth"])[:_DD_MAX]
                    self._crd_open_dd = None
                    self._crd_in_dd = False
                self._crd_peak_nav = nav
                self._crd_peak_date = today
                self._crd_trough_nav = nav
                self._crd_trough_date = today
            else:
                depth = 1.0 - nav / max(self._crd_peak_nav, 1e-9)
                if depth > 0.02:
                    if not self._crd_in_dd:
                        self._crd_in_dd = True
                        self._crd_open_dd = {
                            "peak_date": self._crd_peak_date, "peak_nav": self._crd_peak_nav,
                            "trough_date": today, "trough_nav": nav, "depth": depth,
                            "reg_peak": regime, "reg_trough": regime,
                            "panic_t": panic, "ids_t": ids,
                            "sum_g": gross, "sum_c": cash_w, "n": 1,
                            "worst": r, "def_before": int(def_w >= 0.15),
                            "def_after": 0, "ron_before": 0, "ron_after": 0,
                            "rec_date": None, "rec_days": None,
                        }
                    else:
                        ep = self._crd_open_dd
                        if nav < ep["trough_nav"]:
                            ep["trough_nav"] = nav
                            ep["trough_date"] = today
                            ep["depth"] = 1.0 - nav / max(ep["peak_nav"], 1e-9)
                            ep["reg_trough"] = regime
                            ep["panic_t"] = panic
                            ep["ids_t"] = ids
                        ep["sum_g"] += gross
                        ep["sum_c"] += cash_w
                        ep["n"] += 1
                        if r < ep["worst"]:
                            ep["worst"] = r
                        if def_w >= 0.15 and today > ep["trough_date"]:
                            ep["def_after"] = 1
                        if regime == "RISK_ON" and today > ep["trough_date"]:
                            ep["ron_before"] = 1
            # global stats
            self._crd_n_days += 1
            self._crd_sum_r += r
            self._crd_sum_r2 += r * r
            self._crd_nav_m = max(0.01, self._crd_nav_m * (1.0 + r))
            if self._crd_nav_m > self._crd_peak_m:
                self._crd_peak_m = self._crd_nav_m
            gdd = 1.0 - self._crd_nav_m / max(self._crd_peak_m, 1e-9)
            if gdd > self._crd_maxdd:
                self._crd_maxdd = gdd
            _upd_stats(self._crd_reg[regime], r, gross, cash_w)
            sk = self._CrdStateKey()
            if sk not in self._crd_states:
                if len(self._crd_states) < _STATE_MAX:
                    self._crd_states[sk] = _blank_stats()
            if sk in self._crd_states:
                _upd_stats(self._crd_states[sk], r, gross, cash_w)
            for name, s, e in _CORE_WINDOWS:
                ee = e if e is not None else today
                if s <= today <= ee:
                    _upd_stats(self._crd_win[name], r, gross, cash_w)
            _upd_stats(self._crd_exp[self._CrdExpBucket(gross)], r, gross, cash_w)
            # timing events vs previous day
            thr = self.cg_core_diag_event_weight_threshold
            if self._crd_prev_gross is not None:
                dg = gross - self._crd_prev_gross
                ds = spy_w - (self._crd_prev_spy_w or 0.0)
                dd_w = def_w - (self._crd_prev_def_w or 0.0)
                etypes = []
                if abs(dg) >= thr or abs(ds) >= thr:
                    if dg >= thr or ds >= thr:
                        etypes.append("RISK_INCREASE")
                    if dg <= -thr or ds <= -thr:
                        etypes.append("RISK_DECREASE")
                    if (self._crd_prev_gross or 0) >= thr and gross < 0.10:
                        etypes.append("FULL_EXIT")
                    if (self._crd_prev_gross or 0) < 0.10 and gross >= thr:
                        etypes.append("REENTRY")
                if dd_w >= thr:
                    etypes.append("DEFENSIVE_ENTRY")
                if dd_w <= -thr:
                    etypes.append("DEFENSIVE_EXIT")
                for et in etypes:
                    self._crd_timing[et]["n"] += 1
                    self._crd_pending.append({"type": et, "i": len(self._crd_port_rets),
                                              "left": {1, 3, 5, 10, 20}})
                    if et == "RISK_INCREASE":
                        self._crd_reg[regime]["entries"] += 1
                    if et == "RISK_DECREASE":
                        self._crd_reg[regime]["exits"] += 1
            # resolve pending forward returns
            self._crd_port_rets.append(r)
            self._crd_spy_rets.append(spy_r)
            self._crd_dates.append(today)
            nrets = len(self._crd_port_rets)
            still = []
            for p in self._crd_pending:
                done = set()
                for h in list(p["left"]):
                    if nrets - 1 >= p["i"] + h:
                        fwd = 1.0
                        for j in range(p["i"] + 1, p["i"] + h + 1):
                            if 0 <= j < nrets:
                                fwd *= (1.0 + self._crd_port_rets[j])
                        self._crd_timing[p["type"]]["fwd"][h].append(fwd - 1.0)
                        done.add(h)
                p["left"] -= done
                if p["left"]:
                    still.append(p)
            self._crd_pending = still[-200:]
            # cash drag
            if cash_w >= self.cg_core_diag_cash_threshold:
                cs = self._crd_cash[regime]
                cs["n"] += 1
                cs["sum_c"] += cash_w
                cs["sum_pr"] += r
                cs["sum_sr"] += spy_r
                self._crd_cash_pend.append({"i": nrets - 1, "reg": regime, "left": {5, 20}})
            cstill = []
            for p in self._crd_cash_pend:
                if 5 in p["left"] and nrets - 1 >= p["i"] + 5:
                    fwd = 1.0
                    for j in range(p["i"] + 1, p["i"] + 6):
                        if 0 <= j < len(self._crd_spy_rets):
                            fwd *= (1.0 + self._crd_spy_rets[j])
                    f5 = fwd - 1.0
                    cs = self._crd_cash[p["reg"]]
                    if f5 < 0:
                        cs["good"] += 1
                    if f5 > self.cg_core_diag_bad_cash_spy5:
                        cs["bad"] += 1
                        cs["opp"] += f5
                    p["left"].discard(5)
                if 20 in p["left"] and nrets - 1 >= p["i"] + 20:
                    p["left"].discard(20)
                if p["left"]:
                    cstill.append(p)
            self._crd_cash_pend = cstill[-200:]
            self._crd_prev_nav = nav
            self._crd_prev_spy_px = spy_px
            self._crd_prev_gross = gross
            self._crd_prev_spy_w = spy_w
            self._crd_prev_def_w = def_w
            self._crd_prev_cash_w = cash_w
        except Exception as e:
            try:
                self.log(f"CG_CORE_DIAG_ERR,{type(e).__name__}")
            except Exception:
                pass

    def _CrdEmit(self, lines, line, reserved=0):
        b = len(line.encode("utf-8"))
        if b > _LINE_MAX:
            line = line[:_LINE_MAX - 20] + "...TRUNC"
            b = len(line.encode("utf-8"))
        if self._crd_log_bytes + b + reserved > _LOG_BUDGET:
            return False
        lines.append(line)
        self._crd_log_bytes += b
        return True

    def _CrdFmtStats(self, prefix, name, st):
        n = st["n"]
        mean = st["sum_r"] / n if n else None
        return (f"{prefix},{name},days={n},nav={_f(st['nav_m'])},"
                f"ann={_f(_ann(st['sum_r'], n))},mean={_f(mean, 6)},"
                f"vol={_f(_vol(st['sum_r'], st['sum_r2'], n))},"
                f"maxdd={_f(st['maxdd'])},worst={_f(st['worst'], 6)},"
                f"w5={_f(_w5_mean(list(st['rets'])), 6)},"
                f"pos={_f(st['pos'] / n if n else None, 3)},"
                f"avg_g={_f(st['sum_g'] / n if n else None)},"
                f"avg_c={_f(st['sum_c'] / n if n else None)},"
                f"ent={st.get('entries', 0)},ex={st.get('exits', 0)}")

    def CgCoreRecoveryEmitFinal(self) -> None:
        if not getattr(self, "cg_core_recovery_diag_enable", False):
            return
        lines = []
        self._crd_log_bytes = 0
        # close open DD
        if self._crd_open_dd is not None:
            ep = self._crd_open_dd
            ep["rec_date"] = None
            ep["rec_days"] = None
            self._crd_dd_list.append(ep)
            self._crd_dd_list = sorted(self._crd_dd_list, key=lambda x: -x["depth"])[:_DD_MAX]
        n = self._crd_n_days
        cagr = _ann(self._crd_sum_r, n)
        vol = _vol(self._crd_sum_r, self._crd_sum_r2, n)
        sh = _sharpe(self._crd_sum_r, self._crd_sum_r2, n)
        # windows first (optional priority but needed for readiness)
        win_lines = []
        for name, _, _ in _CORE_WINDOWS:
            st = self._crd_win[name]
            if st["n"] <= 0:
                continue
            win_lines.append(self._CrdFmtStats("CG_CORE_WINDOW_FINAL", name, st))
        if not win_lines:
            win_lines.append("CG_CORE_WINDOW_FINAL,status=NO_DATA")
        # readiness depends on windows
        train = self._crd_win["TRAIN"]
        oos = self._crd_win["OOS"]
        crisis = self._crd_win["CRISIS"]
        t_sh = _sharpe(train["sum_r"], train["sum_r2"], train["n"])
        o_sh = _sharpe(oos["sum_r"], oos["sum_r2"], oos["n"])
        oos_ratio = None
        if t_sh is not None and o_sh is not None and abs(t_sh) > 1e-9:
            oos_ratio = o_sh / t_sh
        open_dd = 0.0
        open_days = 0
        if self._crd_peak_nav and self._crd_prev_nav:
            open_dd = max(0.0, 1.0 - self._crd_prev_nav / max(self._crd_peak_nav, 1e-9))
            if open_dd > 0.01 and self._crd_peak_date and self._crd_end:
                open_days = (self._crd_end - self._crd_peak_date).days
        reasons = []
        ready = 1
        if self._crd_diag_block > 0:
            ready = 0; reasons.append("diag_trade")
        if self._crd_moc > 0:
            ready = 0; reasons.append("moc")
        if oos_ratio is not None and oos_ratio < 0.70:
            ready = 0; reasons.append("oos_weak")
        if vol is not None and vol > 0.18:
            ready = 0; reasons.append("std_high")
        if open_dd > 0.15:
            ready = 0; reasons.append("open_dd")
        if not reasons:
            reasons.append("ok")
        rsn = "|".join(reasons)[:200]
        recovery = (f"CG_CORE_RECOVERY_FINAL,ver=D0,"
                    f"start={self._crd_start},end={self._crd_end},"
                    f"days={n},nav={_f(self._crd_nav_m)},cagr={_f(cagr)},"
                    f"maxdd={_f(self._crd_maxdd)},sharpe={_f(sh)},std={_f(vol)},"
                    f"w5={_f(_w5_mean(list(self._crd_port_rets)), 6)},"
                    f"orders={self._crd_orders},moc={self._crd_moc},"
                    f"diag_block={self._crd_diag_block}")
        live = (f"CG_CORE_LIVE_READY_FINAL,ver=D0,"
                f"period_start={self._crd_start},period_end={self._crd_end},"
                f"nav={_f(self._crd_nav_m)},cagr={_f(cagr)},maxdd={_f(self._crd_maxdd)},"
                f"sharpe={_f(sh)},std={_f(vol)},"
                f"w5={_f(_w5_mean(list(self._crd_port_rets)), 6)},"
                f"orders={self._crd_orders},turnover=NA,"
                f"oos_sharpe_ratio={_f(oos_ratio)},"
                f"crisis_maxdd={_f(crisis['maxdd'])},"
                f"y2020_maxdd={_f(self._crd_win['Y2020']['maxdd'])},"
                f"y2022_maxdd={_f(self._crd_win['Y2022']['maxdd'])},"
                f"open_drawdown={_f(open_dd)},open_drawdown_days={open_days},"
                f"max_recovery_days={self._crd_max_rec_days},"
                f"diag_trade_violations={self._crd_diag_block},"
                f"moc_orders_detected={self._crd_moc},"
                f"ready={ready},reasons={rsn}")
        # optional categories, precomputed so every category yields >=1 line
        reg_lines = []
        for r in _REGIMES:
            st = self._crd_reg[r]
            if st["n"] > 0:
                reg_lines.append(self._CrdFmtStats("CG_CORE_REGIME_FINAL", r, st))
        if not reg_lines:
            reg_lines.append("CG_CORE_REGIME_FINAL,status=NO_DATA")
        dd_lines = []
        for i, ep in enumerate(self._crd_dd_list[:_DD_MAX], 1):
            nn = max(1, ep.get("n", 1))
            dd_lines.append(
                f"CG_CORE_DD_FINAL,rank={i},"
                f"peak={ep['peak_date']},trough={ep['trough_date']},"
                f"rec={ep['rec_date'] or 'OPEN'},depth={_f(ep['depth'])},"
                f"pt_days={(ep['trough_date']-ep['peak_date']).days},"
                f"tr_days={ep['rec_days'] if ep['rec_days'] is not None else 'OPEN'},"
                f"reg_p={ep['reg_peak']},reg_t={ep['reg_trough']},"
                f"panic_t={ep['panic_t']},ids_t={ep['ids_t']},"
                f"avg_g={_f(ep['sum_g']/nn)},avg_c={_f(ep['sum_c']/nn)},"
                f"worst={_f(ep['worst'],6)},"
                f"def_b={ep['def_before']},def_a={ep['def_after']},"
                f"ron_b={ep['ron_before']},ron_a={ep['ron_after']}")
        if not dd_lines:
            dd_lines.append("CG_CORE_DD_FINAL,status=NO_DATA")
        timing_lines = []
        for t in _TIMING_TYPES:
            tm = self._crd_timing[t]
            if tm["n"] <= 0:
                continue
            parts = [f"CG_CORE_TIMING_FINAL,{t},n={tm['n']}"]
            for h in (1, 3, 5, 10, 20):
                arr = tm["fwd"][h]
                if not arr:
                    parts.append(f"d{h}=NA")
                    continue
                mean = sum(arr) / len(arr)
                sarr = sorted(arr)
                med = sarr[len(sarr) // 2]
                pos = sum(1 for x in arr if x > 0) / len(arr)
                worst = sarr[0]
                parts.append(f"d{h}={_f(mean,4)}/{_f(med,4)}/{_f(pos,2)}/{_f(worst,4)}")
            timing_lines.append(",".join(parts))
        if not timing_lines:
            timing_lines.append("CG_CORE_TIMING_FINAL,status=NO_DATA")
        state_lines = []
        for sk, st in list(self._crd_states.items())[:_STATE_MAX]:
            if st["n"] > 0:
                state_lines.append(self._CrdFmtStats("CG_CORE_STATE_FINAL", sk, st))
        if not state_lines:
            state_lines.append("CG_CORE_STATE_FINAL,status=NO_DATA")
        cash_lines = []
        for r in _REGIMES:
            cs = self._crd_cash[r]
            if cs["n"] <= 0:
                continue
            nn = cs["n"]
            cash_lines.append(
                f"CG_CORE_CASH_FINAL,{r},days={nn},"
                f"avg_c={_f(cs['sum_c']/nn)},"
                f"port={_f(cs['sum_pr']/nn,6)},"
                f"spy={_f(cs['sum_sr']/nn,6)},"
                f"diff={_f((cs['sum_pr']-cs['sum_sr'])/nn,6)},"
                f"good={cs['good']},bad={cs['bad']},"
                f"opp={_f(cs['opp'])}")
        if not cash_lines:
            cash_lines.append("CG_CORE_CASH_FINAL,status=NO_DATA")
        exp_lines = []
        for b in _EXP_BUCKETS:
            st = self._crd_exp[b]
            if st["n"] > 0:
                exp_lines.append(self._CrdFmtStats("CG_CORE_EXPOSURE_FINAL", b, st))
        if not exp_lines:
            exp_lines.append("CG_CORE_EXPOSURE_FINAL,status=NO_DATA")
        # emit mandatory first, then optional in priority order
        self._CrdEmit(lines, recovery)
        self._CrdEmit(lines, live)
        omitted = []
        for cat_name, cat_lines in (
            ("windows", win_lines), ("regimes", reg_lines), ("drawdowns", dd_lines),
            ("timing", timing_lines), ("states", state_lines), ("cash", cash_lines),
            ("exposure", exp_lines),
        ):
            stop = False
            for ln in cat_lines:
                if not self._CrdEmit(lines, ln):
                    omitted.append(cat_name)
                    stop = True
                    break
            if stop:
                break
        if omitted:
            self._CrdEmit(lines, f"CG_CORE_LOG_TRUNCATED,bytes={self._crd_log_bytes},"
                                 f"omitted={'|'.join(omitted)}")
        for ln in lines:
            self.log(ln)
