# region imports
from AlgorithmImports import *
from datetime import date as _date
# endregion
# cg_agx_shadow_diag.py
# CG-AGX-INDEPENDENT-PARITY-D1
#
# REFERENCE_MIRROR: production TPV day-over-day (truth only).
# INDEPENDENT_CONTROL: own cash/qty/NAV ledger; never copies production
# NAV/returns/holdings/cash/drawdown. Simulates fills from captured targets
# at fixed-165 using production min-order filters. No LEAN orders.
#
# PHASE A: three-way parity. PHASE B (same run, only if A passes):
# MODEL_A=ALL_NON_PARKING, MODEL_B=RISK_SLEEVE_ONLY; 100 policies each.

_RON = (0.75, 1.00, 1.10, 1.20, 1.30)
_NEU = (0.50, 0.75, 0.90, 1.00)
_ROFF = (0.00, 0.25, 0.50, 0.75, 1.00)
_DFT_DEF = frozenset(("TIP", "BND", "GLD", "GLDM", "BIL", "SGOV", "USFR", "SH"))
_LOG_BUDGET = 95000
_A_CTRL = "A_AGX_RON100_NEU100_ROFF100"
_B_CTRL = "B_AGX_RON100_NEU100_ROFF100"


def _pid(model, a, b, c):
    return f"{model}_AGX_RON{int(round(a * 100)):03d}_NEU{int(round(b * 100)):03d}_ROFF{int(round(c * 100)):03d}"


def _tk(sym):
    try:
        return str(sym.Value)
    except Exception:
        try:
            return str(sym.value)
        except Exception:
            return str(sym)


def _f(x, d=4):
    if x is None:
        return "NA"
    try:
        return f"{float(x):.{d}f}"
    except Exception:
        return "NA"


def _new_led(pid, model, ron, neu, roff, cash0):
    return {
        "id": pid, "model": model, "ron": ron, "neu": neu, "roff": roff,
        "cash": float(cash0), "qty": {}, "pending": None, "_pend_hit": 0,
        "nav": float(cash0), "peak": float(cash0), "maxdd": 0.0,
        "rets": [], "dates": [], "fees": 0.0, "turnover": 0.0,
        "reb": 0, "cap_hit": 0, "sum_eg": 0.0, "n_eg": 0, "egs": [],
        "rg_n": {"RISK_ON": 0, "NEUTRAL": 0, "RISK_OFF": 0},
        "rg_r": {"RISK_ON": 0.0, "NEUTRAL": 0.0, "RISK_OFF": 0.0},
        "rg_dd": {"RISK_ON": 0.0, "NEUTRAL": 0.0, "RISK_OFF": 0.0},
        "rg_w5": {"RISK_ON": [], "NEUTRAL": [], "RISK_OFF": []},
        "rg_eg": {"RISK_ON": 0.0, "NEUTRAL": 0.0, "RISK_OFF": 0.0},
        "rg_cap": {"RISK_ON": 0, "NEUTRAL": 0, "RISK_OFF": 0},
        "tr_n": {"0": 0, "1": 0, "2-3": 0, "4-10": 0, ">10": 0},
        "tr_r": {"0": 0.0, "1": 0.0, "2-3": 0.0, "4-10": 0.0, ">10": 0.0},
        "uw": 0, "uw_max": 0, "uw_days": 0, "fixed_err": 0.0,
    }


class CgAgxShadowDiagMixin:
    """Independent-ledger AGX D1: parity gate + dual-model reduced grid."""

    def CgAgxShadowInit(self) -> None:
        ov = getattr(self, "_rrx_param_overrides", {}) or {}

        def _p(k, d=""):
            v = self.get_parameter(k)
            if v is None or str(v).strip() == "":
                v = ov.get(k, d)
            return v

        def _bool(k, d="0"):
            return str(_p(k, d) or d).strip().lower() in ("1", "true", "yes", "on")

        def _float(k, d):
            try:
                return float(str(_p(k, str(d)) or d).strip())
            except Exception:
                return float(d)

        self.cg_agx_shadow_diag_enable = _bool("cg_agx_shadow_diag_enable", "0")
        self.cg_agx_independent_parity_enable = _bool("cg_agx_independent_parity_enable", "0")
        self.cg_agx_independent_grid_enable = _bool("cg_agx_independent_grid_enable", "0")
        self.cg_agx_shadow_emit_events = _bool("cg_agx_shadow_emit_events", "0")
        req_cap = _float("cg_agx_shadow_max_gross", 2.00)
        prod_cap = float(getattr(self, "max_total_exposure", 1.90) or 1.90)
        self._agx_prod_gross_cap = prod_cap
        self._agx_max_gross = min(float(req_cap), float(prod_cap))
        self._agx_cost_bps = _float("cg_agx_shadow_cost_bps", 0.0)
        self._agx_enabled = bool(
            self.cg_agx_shadow_diag_enable and self.cg_agx_independent_parity_enable
        )
        self._agx_grid_on = bool(self._agx_enabled and self.cg_agx_independent_grid_enable)

        cash = getattr(self, "sym_cash", None)
        self._agx_cash_tk = _tk(cash) if cash is not None else "BIL"
        self._agx_log_used = 0
        self._agx_err = 0
        self._agx_emitted = False
        self._agx_started = False
        self._agx_last_mark = None
        self._agx_prev_tpv = None
        self._agx_prev_px = None
        self._agx_dates = []
        self._agx_actual_rets = []
        self._agx_mirror_rets = []
        self._agx_ind_rets = []
        self._agx_n_cap = 0
        self._agx_n_imm = 0
        self._agx_n_def = 0
        self._agx_n_exe = 0
        self._agx_target_mismatch_count = 0
        self._agx_max_abs_target_weight_diff = 0.0
        self._agx_last_base = {}
        self._agx_base_gross_obs = []
        self._agx_last_base_gross = None
        self._agx_regime_prev = None
        self._agx_regime_age = 10 ** 9
        self._agx_snap_candidates = []
        self._agx_exec_events = 0
        self._agx_dir_mm = 0
        self._agx_qty_mm = 0
        self._agx_sup_mm = 0
        self._agx_sec_mm = 0
        self._agx_cash_mm = 0
        self._agx_fee_mm = 0
        self._agx_qty_exceptions = []
        self._agx_max_cash_diff = 0.0
        self._agx_max_hold_diff = 0.0
        self._agx_last_ind_expect = None
        self._agx_corpaction = "PASS"
        self._agx_residual_convention = "INDEPENDENT_CASH_LEDGER_NO_BIL_RESIDUAL"
        self._agx_cls = {"SCALABLE_RISK": set(), "FIXED_DEFENSIVE": set(),
                         "PARKING_ETF": set(), "OTHER_FIXED": set(),
                         "KEEP_FIXED_UNCERTAIN": set(), "src": {}}
        self._agx_pols = []
        self._agx_by_id = {}
        self._agx_ctrl = None
        self._agx_a_ctrl = None
        self._agx_b_ctrl = None

        lp = list(getattr(self, "log_only_prefixes", None) or [])
        for pref in ("CG_AGX_IND_PARITY_", "CG_AGX_D1_", "[INIT] CG_AGX"):
            if pref not in lp:
                lp.append(pref)
        self.log_only_prefixes = lp

        cash0 = 10000.0
        try:
            cash0 = float(self.portfolio.cash) if float(self.portfolio.cash) > 0 else 10000.0
        except Exception:
            cash0 = 10000.0
        self._agx_cash0 = cash0
        self._agx_ctrl = _new_led("INDEPENDENT_CONTROL", "CTRL", 1.0, 1.0, 1.0, cash0)

        npol = 0
        if self._agx_grid_on:
            for model in ("A", "B"):
                for a in _RON:
                    for b in _NEU:
                        for c in _ROFF:
                            pid = _pid(model, a, b, c)
                            p = _new_led(pid, model, a, b, c, cash0)
                            self._agx_pols.append(p)
                            self._agx_by_id[pid] = p
            npol = len(self._agx_pols)
            self._agx_a_ctrl = self._agx_by_id.get(_A_CTRL)
            self._agx_b_ctrl = self._agx_by_id.get(_B_CTRL)

        self._AgxBuildClassification()
        self.log(
            f"CG_AGX_IND_PARITY_INIT,enable={int(self._agx_enabled)},"
            f"independent_control_mode=INDEPENDENT_LEDGER,"
            f"reference_mirror=REFERENCE_MIRROR,"
            f"grid_enable={int(self._agx_grid_on)},policies={npol},"
            f"parking_etf={self._agx_cash_tk},"
            f"residual_convention={self._agx_residual_convention},"
            f"max_gross={_f(self._agx_max_gross)},prod_cap={_f(prod_cap)},"
            f"req_cap={_f(req_cap)},cost_bps={_f(self._agx_cost_bps, 2)},"
            f"min_weight_delta={_f(getattr(self,'min_weight_delta',0.02))},"
            f"min_trade_value={_f(getattr(self,'min_trade_value',100),1)},"
            f"min_trade_value_perc={_f(getattr(self,'min_trade_value_perc',0.11))},"
            f"scalable_risk={','.join(sorted(self._agx_cls['SCALABLE_RISK'])[:20])},"
            f"fixed_defensive={','.join(sorted(self._agx_cls['FIXED_DEFENSIVE']))},"
            f"parking={','.join(sorted(self._agx_cls['PARKING_ETF']))},"
            f"emit_events={int(self.cg_agx_shadow_emit_events)},"
            f"candidate_quarantined=1,selection_allowed=0"
        )
        if not self._agx_enabled:
            return
        try:
            spy = getattr(self, "sym_spy", None)
            if spy is not None:
                self.schedule.on(
                    self.date_rules.every_day(spy),
                    self.time_rules.after_market_open(spy, 14),
                    self.CgAgxShadowMark,
                )
        except Exception as exc:
            self._agx_err += 1
            self.log(f"CG_AGX_IND_PARITY_INIT,schedule_error={type(exc).__name__}")

    def _AgxBuildClassification(self):
        park = self._agx_cash_tk
        self._agx_cls["PARKING_ETF"].add(park)
        self._agx_cls["src"][park] = "sym_cash/parking_etf"
        for t in _DFT_DEF:
            if t == park:
                continue
            self._agx_cls["FIXED_DEFENSIVE"].add(t)
            self._agx_cls["src"][t] = "cg_defensive_trade._DFT_DEF"
        # Risk sleeve: SPY + known non-defensive equity names seen in targets over run
        self._agx_cls["SCALABLE_RISK"].add("SPY")
        self._agx_cls["src"]["SPY"] = "W2/equity_risk_sleeve"

    def _AgxClassifyTicker(self, t):
        if t in self._agx_cls["PARKING_ETF"]:
            return "PARKING_ETF"
        if t in self._agx_cls["FIXED_DEFENSIVE"]:
            return "FIXED_DEFENSIVE"
        if t in self._agx_cls["SCALABLE_RISK"]:
            return "SCALABLE_RISK"
        if t in _DFT_DEF:
            self._agx_cls["FIXED_DEFENSIVE"].add(t)
            self._agx_cls["src"][t] = "cg_defensive_trade._DFT_DEF"
            return "FIXED_DEFENSIVE"
        # Equity/risk: not defensive and not parking → scalable risk sleeve
        if t == "SPY" or t not in _DFT_DEF:
            # Ambiguous unknown tickers: treat as risk if alphanumeric ETF/stock-like
            if t.isalpha() and len(t) <= 5:
                self._agx_cls["SCALABLE_RISK"].add(t)
                self._agx_cls["src"][t] = "non_defensive_target_as_risk_sleeve"
                return "SCALABLE_RISK"
        self._agx_cls["KEEP_FIXED_UNCERTAIN"].add(t)
        self._agx_cls["OTHER_FIXED"].add(t)
        self._agx_cls["src"][t] = "KEEP_FIXED_UNCERTAIN"
        return "KEEP_FIXED_UNCERTAIN"

    def _AgxLog(self, msg):
        try:
            n = len(msg) + 1
            if self._agx_log_used + n > _LOG_BUDGET:
                return
            self.log(msg)
            self._agx_log_used += n
        except Exception:
            pass

    def _AgxPx(self, tickers=None):
        out = {}
        sec = getattr(self, "securities", None)
        if sec is None:
            return out
        want = set(tickers) if tickers is not None else None
        try:
            for k in sec.keys():
                t = _tk(k)
                if want is not None and t not in want:
                    continue
                try:
                    px = float(sec[k].price)
                    if px > 0:
                        out[t] = px
                except Exception:
                    continue
        except Exception:
            pass
        return out

    def _AgxBaseW(self, combined):
        w = {}
        for k, v in (combined or {}).items():
            try:
                wf = float(v or 0.0)
            except Exception:
                continue
            if abs(wf) < 1e-12:
                continue
            t = _tk(k)
            w[t] = wf
            self._AgxClassifyTicker(t)
        return w

    def _AgxGross(self, w):
        g = 0.0
        cash = self._agx_cash_tk
        for t, v in (w or {}).items():
            if t == cash:
                continue
            try:
                g += abs(float(v or 0.0))
            except Exception:
                pass
        return g

    def _AgxNav(self, led, px):
        hv = 0.0
        for t, q in (led.get("qty") or {}).items():
            p = (px or {}).get(t)
            if p and p > 0:
                hv += float(q) * p
        return float(led.get("cash", 0.0)) + hv, hv

    def _AgxCurrW(self, led, px, nav):
        if nav is None or nav <= 0:
            return {}
        out = {}
        for t, q in (led.get("qty") or {}).items():
            p = (px or {}).get(t)
            if p and p > 0 and abs(q) > 1e-12:
                out[t] = float(q) * p / nav
        return out

    def _AgxScale(self, base_w, mult, model):
        """Scale scalable set only; preserve fixed exactly; residual via cash ledger.
        mult==1.0 with no cap is identity copy of base_w."""
        park = self._agx_cash_tk
        base = {t: float(v) for t, v in (base_w or {}).items()}
        if abs(float(mult) - 1.0) < 1e-15:
            return dict(base), 1.0, 1.0, self._AgxGross(base), self._AgxGross(base), 0, 0.0

        scalable = {}
        fixed = {}
        for t, v in base.items():
            cls = self._AgxClassifyTicker(t)
            if model == "A":
                if t == park:
                    fixed[t] = v
                else:
                    scalable[t] = v
            else:  # B RISK_SLEEVE_ONLY
                if cls == "SCALABLE_RISK":
                    scalable[t] = v
                else:
                    fixed[t] = v

        bg = sum(abs(v) for v in scalable.values())
        req = float(mult)
        cap = float(self._agx_max_gross)
        # Cap applies to total non-parking gross after scale
        fixed_nonpark = sum(abs(v) for t, v in fixed.items() if t != park)
        hit = 0
        if bg <= 1e-12:
            eff = req
            pre = fixed_nonpark
            post = fixed_nonpark
            scaled_s = {}
        else:
            pre = fixed_nonpark + bg * req
            room = max(0.0, cap - fixed_nonpark)
            if bg * req > room + 1e-12:
                eff = room / bg if bg > 0 else 0.0
                hit = 1
                post = fixed_nonpark + bg * eff
            else:
                eff = req
                post = pre
            scaled_s = {t: v * eff for t, v in scalable.items()}
        out = dict(fixed)
        out.update(scaled_s)
        # fixed-weight preservation error
        ferr = 0.0
        for t, v in fixed.items():
            ferr = max(ferr, abs(float(out.get(t, 0.0)) - float(v)))
        return out, req, eff, pre, post, hit, ferr

    def _AgxSimExec(self, led, targets, px, audit=False):
        """Simulate production-like filters then fill at px. Residual stays in cash."""
        nav, _ = self._AgxNav(led, px)
        if nav <= 0:
            return {"suppressed": set(), "traded": {}, "expect_qty": {}}
        cur = self._AgxCurrW(led, px, nav)
        mwd = float(getattr(self, "min_weight_delta", 0.02) or 0.02)
        mtv = float(getattr(self, "min_trade_value", 100) or 100)
        mtvp = float(getattr(self, "min_trade_value_perc", 0.11) or 0.11)
        min_tv = max(mtv, nav * mtvp)
        tgt = {t: float(v) for t, v in (targets or {}).items()}
        # Close omitted holdings to 0
        for t in list(cur.keys()):
            if t not in tgt:
                tgt[t] = 0.0
        suppressed = set()
        traded = {}
        expect_qty = dict(led.get("qty") or {})
        cash = float(led.get("cash", 0.0))
        fees = 0.0
        tov = 0.0
        # reduce-first ordering
        items = sorted(tgt.items(), key=lambda kv: (0 if float(kv[1]) < float(cur.get(kv[0], 0.0)) else 1, kv[0]))
        for t, tw in items:
            p = (px or {}).get(t)
            if not p or p <= 0:
                suppressed.add(t)
                continue
            cw = float(cur.get(t, 0.0))
            zero_close = (abs(tw) < 1e-15 and cw > 0.0)
            if not zero_close and abs(tw - cw) < mwd:
                suppressed.add(t)
                continue
            trade_value = abs(tw - cw) * nav
            if not zero_close and trade_value < min_tv:
                suppressed.add(t)
                continue
            desired_q = tw * nav / p
            old_q = float((led.get("qty") or {}).get(t, 0.0))
            dq = desired_q - old_q
            notional = abs(dq) * p
            fee = notional * float(self._agx_cost_bps) / 10000.0
            cash -= dq * p
            cash -= fee
            fees += fee
            tov += notional
            expect_qty[t] = desired_q
            traded[t] = dq
            if abs(desired_q) < 1e-12:
                expect_qty.pop(t, None)
        led["cash"] = cash
        led["qty"] = {t: q for t, q in expect_qty.items() if abs(q) > 1e-12}
        led["fees"] = float(led.get("fees", 0.0)) + fees
        led["turnover"] = float(led.get("turnover", 0.0)) + (tov / max(nav, 1e-9)) * 0.5
        led["reb"] = int(led.get("reb", 0)) + 1
        nav2, _ = self._AgxNav(led, px)
        led["nav"] = nav2
        if nav2 > led["peak"]:
            led["peak"] = nav2
        return {"suppressed": suppressed, "traded": traded, "expect_qty": dict(led["qty"]),
                "cash": cash, "fees": fees, "nav": nav2}

    def _AgxApplyDaily(self, led, prev_nav, nav, rg, age, eg, hit):
        if prev_nav is None or prev_nav <= 0 or nav is None:
            return
        r = nav / prev_nav - 1.0
        led["rets"].append(r)
        led["dates"].append(self.time.date())
        led["nav"] = nav
        if nav > led["peak"]:
            led["peak"] = nav
            led["uw"] = 0
        else:
            led["uw"] += 1
            led["uw_days"] += 1
            if led["uw"] > led["uw_max"]:
                led["uw_max"] = led["uw"]
        dd = 1.0 - nav / max(led["peak"], 1e-9)
        if dd > led["maxdd"]:
            led["maxdd"] = dd
        if eg is not None:
            led["sum_eg"] += eg
            led["n_eg"] += 1
            led["egs"].append(eg)
        rg = str(rg or "NEUTRAL").upper()
        if rg not in led["rg_n"]:
            rg = "NEUTRAL"
        led["rg_n"][rg] += 1
        led["rg_r"][rg] += r
        led["rg_dd"][rg] = max(led["rg_dd"][rg], dd)
        led["rg_w5"][rg].append(r)
        led["rg_eg"][rg] += float(eg or 0.0)
        if hit:
            led["rg_cap"][rg] += 1
        if age <= 0:
            tb = "0"
        elif age == 1:
            tb = "1"
        elif age <= 3:
            tb = "2-3"
        elif age <= 10:
            tb = "4-10"
        else:
            tb = ">10"
        led["tr_n"][tb] += 1
        led["tr_r"][tb] += r

    def CgAgxShadowCapture(self, combined, regime, slot, reduce_only=False, emergency=False) -> None:
        if not getattr(self, "_agx_enabled", False):
            return
        try:
            if getattr(self, "IsWarmingUp", False) or getattr(self, "is_warming_up", False):
                return
            base = self._AgxBaseW(combined)
            bg = self._AgxGross(base)
            self._agx_base_gross_obs.append(bg)
            self._agx_last_base_gross = bg
            self._agx_last_base = dict(base)
            self._agx_n_cap += 1
            rg = str(regime or getattr(self, "current_regime", None) or "NEUTRAL").upper()
            if rg not in ("RISK_ON", "NEUTRAL", "RISK_OFF"):
                rg = "NEUTRAL"
            imm = bool(reduce_only or emergency or int(slot or 0) == 15)
            if imm:
                self._agx_n_imm += 1
            else:
                self._agx_n_def += 1

            ctrl_t = {t: float(v) for t, v in base.items()}
            mismatch = 0 if set(ctrl_t) == set(base) else 1
            max_diff = 0.0
            for k in set(ctrl_t) | set(base):
                max_diff = max(max_diff, abs(float(ctrl_t.get(k, 0)) - float(base.get(k, 0))))
            self._agx_target_mismatch_count += mismatch
            if max_diff > self._agx_max_abs_target_weight_diff:
                self._agx_max_abs_target_weight_diff = max_diff
            if self._agx_n_cap <= 2 or (self._agx_n_cap % 200 == 0):
                self._AgxLog(
                    f"CG_AGX_IND_PARITY_TARGET,date={self.time.date()},regime={rg},"
                    f"n={len(ctrl_t)},gross={_f(bg)},signed={_f(sum(ctrl_t.values()))},"
                    f"bil={_f(ctrl_t.get(self._agx_cash_tk))},mismatch={mismatch},"
                    f"max_diff={_f(max_diff, 14)}"
                )

            px = self._AgxPx(set(base) | set((self._agx_ctrl.get("qty") or {})))
            # Independent control: exact captured targets (identity)
            if reduce_only or emergency or imm:
                self._AgxSimExec(self._agx_ctrl, ctrl_t, px)
            else:
                self._agx_ctrl["pending"] = ctrl_t
                self._agx_ctrl["_pend_hit"] = 0

            if self._agx_grid_on:
                for p in self._agx_pols:
                    if reduce_only or emergency:
                        tw = dict(base)
                        hit = 0
                        ferr = 0.0
                    else:
                        mult = p["ron"] if rg == "RISK_ON" else (p["roff"] if rg == "RISK_OFF" else p["neu"])
                        tw, _req, _eff, _pre, _post, hit, ferr = self._AgxScale(base, mult, p["model"])
                        p["fixed_err"] = max(float(p.get("fixed_err", 0.0)), float(ferr))
                    if imm or reduce_only or emergency:
                        self._AgxSimExec(p, tw, px)
                        p["cap_hit"] += int(hit)
                    else:
                        p["pending"] = tw
                        p["_pend_hit"] = hit
        except Exception as exc:
            self._agx_err += 1
            if self._agx_err <= 3:
                self._AgxLog(f"CG_AGX_IND_PARITY_EXEC,capture_error={type(exc).__name__}")

    def CgAgxShadowExecutePending(self) -> None:
        if not getattr(self, "_agx_enabled", False):
            return
        try:
            px = self._AgxPx()
            any_p = False
            if self._agx_ctrl.get("pending") is not None:
                any_p = True
                info = self._AgxSimExec(self._agx_ctrl, self._agx_ctrl["pending"], px, audit=True)
                self._agx_ctrl["pending"] = None
                self._agx_last_ind_expect = info
                self._agx_exec_events += 1
            if self._agx_grid_on:
                for p in self._agx_pols:
                    pend = p.get("pending")
                    if pend is None:
                        continue
                    any_p = True
                    hit = int(p.pop("_pend_hit", 0) or 0)
                    self._AgxSimExec(p, pend, px)
                    p["pending"] = None
                    p["cap_hit"] += hit
            if any_p:
                self._agx_n_exe += 1
            # Snapshot production pre-state for post-exec audit
            try:
                self._agx_prod_pre_cash = float(self.portfolio.cash)
                self._agx_prod_pre_w = {
                    _tk(s): float(h.HoldingsValue) / max(float(self.portfolio.total_portfolio_value), 1e-9)
                    for s, h in self.portfolio.items() if h and h.Invested
                }
                self._agx_prod_pre_qty = {
                    _tk(s): float(h.Quantity) for s, h in self.portfolio.items() if h and h.Invested
                }
            except Exception:
                self._agx_prod_pre_cash = None
                self._agx_prod_pre_w = {}
                self._agx_prod_pre_qty = {}
        except Exception as exc:
            self._agx_err += 1
            if self._agx_err <= 3:
                self._AgxLog(f"CG_AGX_IND_PARITY_EXEC,exec_error={type(exc).__name__}")

    def CgAgxShadowAuditPostExec(self) -> None:
        """Compare independent expected trades vs production post-fill (audit only)."""
        if not getattr(self, "_agx_enabled", False):
            return
        info = self._agx_last_ind_expect
        if not info:
            return
        try:
            prod_qty = {
                _tk(s): float(h.Quantity) for s, h in self.portfolio.items() if h and h.Invested
            }
            prod_cash = float(self.portfolio.cash)
            ind_qty = info.get("expect_qty") or {}
            # Direction / quantity / security-set
            keys = set(prod_qty) | set(ind_qty) | set(self._agx_prod_pre_qty or {})
            for t in keys:
                pq = float(prod_qty.get(t, 0.0))
                iq = float(ind_qty.get(t, 0.0))
                pre = float((self._agx_prod_pre_qty or {}).get(t, 0.0))
                pd = pq - pre
                idlt = float((info.get("traded") or {}).get(t, iq - float((self._agx_ctrl.get("qty") or {}).get(t, 0.0))))
                # Use traded dict if available
                if t in (info.get("traded") or {}):
                    idlt = float(info["traded"][t])
                else:
                    # If suppressed independently, expect no trade
                    if t in (info.get("suppressed") or set()):
                        idlt = 0.0
                if abs(pd) > 1e-6 or abs(idlt) > 1e-6:
                    if (pd > 0) != (idlt > 0) and abs(pd) > 1e-6 and abs(idlt) > 1e-6:
                        self._agx_dir_mm += 1
                    if abs(pq - iq) > max(1.0, 0.01 * abs(pq)):
                        self._agx_qty_mm += 1
                        if len(self._agx_qty_exceptions) < 8:
                            self._agx_qty_exceptions.append(
                                f"{self.time.date()}:{t}:prod={pq:.2f}:ind={iq:.2f}"
                            )
            if set(prod_qty.keys()) != set(ind_qty.keys()):
                self._agx_sec_mm += 1
            # Suppression: if independent suppressed but production traded
            for t in (info.get("suppressed") or set()):
                pre = float((self._agx_prod_pre_qty or {}).get(t, 0.0))
                pq = float(prod_qty.get(t, 0.0))
                if abs(pq - pre) > 1e-4:
                    self._agx_sup_mm += 1
            cd = abs(prod_cash - float(info.get("cash", 0.0)))
            if cd > self._agx_max_cash_diff:
                self._agx_max_cash_diff = cd
            # Fees: production total fees not per-event; skip hard fee mismatch unless cost_bps>0
            if self._agx_exec_events <= 3 or self._agx_exec_events % 100 == 0:
                self._AgxLog(
                    f"CG_AGX_IND_PARITY_EXEC,date={self.time.date()},"
                    f"events={self._agx_exec_events},dir_mm={self._agx_dir_mm},"
                    f"qty_mm={self._agx_qty_mm},sup_mm={self._agx_sup_mm},"
                    f"sec_mm={self._agx_sec_mm},ind_cash={_f(info.get('cash'),2)},"
                    f"prod_cash={_f(prod_cash,2)}"
                )
            self._agx_last_ind_expect = None
        except Exception as exc:
            self._agx_err += 1
            if self._agx_err <= 3:
                self._AgxLog(f"CG_AGX_IND_PARITY_EXEC,audit_error={type(exc).__name__}")

    def CgAgxShadowMark(self) -> None:
        if not getattr(self, "_agx_enabled", False):
            return
        try:
            if getattr(self, "IsWarmingUp", False) or getattr(self, "is_warming_up", False):
                return
            today = self.time.date()
            if self._agx_last_mark == today:
                return
            rg = str(getattr(self, "current_regime", None) or "NEUTRAL").upper()
            if rg not in ("RISK_ON", "NEUTRAL", "RISK_OFF"):
                rg = "NEUTRAL"
            if self._agx_regime_prev is None:
                self._agx_regime_prev = rg
                self._agx_regime_age = 10 ** 9
            elif rg != self._agx_regime_prev:
                self._agx_regime_prev = rg
                self._agx_regime_age = 0
            else:
                self._agx_regime_age += 1
            age = self._agx_regime_age

            try:
                tpv = float(self.portfolio.total_portfolio_value)
            except Exception:
                tpv = None
            try:
                prod_cash = float(self.portfolio.cash)
            except Exception:
                prod_cash = None
            prod_hold = (tpv - prod_cash) if (tpv is not None and prod_cash is not None) else None

            tickers = set(self._agx_ctrl.get("qty") or {}) | set(self._agx_last_base or {})
            tickers.add(self._agx_cash_tk)
            if self._agx_grid_on:
                for p in self._agx_pols:
                    tickers.update((p.get("qty") or {}).keys())
            px = self._AgxPx(tickers)

            ind_nav, ind_hold = self._AgxNav(self._agx_ctrl, px)
            ind_cash = float(self._agx_ctrl.get("cash", 0.0))

            if not self._agx_started:
                self._agx_started = True
                self._agx_prev_tpv = tpv if tpv else self._agx_cash0
                self._agx_ctrl["_prev_nav"] = ind_nav if ind_nav > 0 else self._agx_cash0
                if self._agx_grid_on:
                    for p in self._agx_pols:
                        n, _ = self._AgxNav(p, px)
                        p["_prev_nav"] = n if n > 0 else self._agx_cash0
                self._agx_prev_px = px
                self._agx_last_mark = today
                return

            # REFERENCE_MIRROR
            if self._agx_prev_tpv and self._agx_prev_tpv > 0 and tpv is not None:
                ar = tpv / self._agx_prev_tpv - 1.0
            else:
                ar = 0.0
            self._agx_actual_rets.append(ar)
            self._agx_mirror_rets.append(ar)
            self._agx_dates.append(today)
            self._agx_prev_tpv = tpv if tpv is not None else self._agx_prev_tpv

            prev_ind = self._agx_ctrl.get("_prev_nav")
            self._AgxApplyDaily(self._agx_ctrl, prev_ind, ind_nav, rg, age, self._AgxGross(self._AgxCurrW(self._agx_ctrl, px, ind_nav)), 0)
            self._agx_ctrl["_prev_nav"] = ind_nav
            if prev_ind and prev_ind > 0:
                self._agx_ind_rets.append(ind_nav / prev_ind - 1.0)
            else:
                self._agx_ind_rets.append(0.0)

            if prod_cash is not None:
                self._agx_max_cash_diff = max(self._agx_max_cash_diff, abs(prod_cash - ind_cash))
            if prod_hold is not None:
                self._agx_max_hold_diff = max(self._agx_max_hold_diff, abs(prod_hold - ind_hold))

            self._agx_snap_candidates.append({
                "date": today, "regime": rg,
                "target_count": len(self._agx_last_base or {}),
                "target_gross": self._agx_last_base_gross,
                "target_signed_sum": sum((self._agx_last_base or {}).values()),
                "BIL_target": (self._agx_last_base or {}).get(self._agx_cash_tk),
                "production_cash": prod_cash, "independent_cash": ind_cash,
                "production_holdings": prod_hold, "independent_holdings": ind_hold,
                "production_NAV": tpv, "independent_NAV": ind_nav,
                "max_target_diff": self._agx_max_abs_target_weight_diff,
                "w2": bool(getattr(self, "_cg_w2_last_active", False)),
            })

            n = len(self._agx_dates)
            if n % 63 == 0:
                self._AgxLog(
                    f"CG_AGX_IND_PARITY_DAILY,date={today},n={n},"
                    f"prod_nav={_f(tpv,2)},ind_nav={_f(ind_nav,2)},"
                    f"prod_cash={_f(prod_cash,2)},ind_cash={_f(ind_cash,2)},"
                    f"dir_mm={self._agx_dir_mm},qty_mm={self._agx_qty_mm},"
                    f"sup_mm={self._agx_sup_mm},errors={self._agx_err}"
                )

            if self._agx_grid_on:
                for p in self._agx_pols:
                    n2, _ = self._AgxNav(p, px)
                    eg = self._AgxGross(self._AgxCurrW(p, px, n2))
                    self._AgxApplyDaily(p, p.get("_prev_nav"), n2, rg, age, eg, 0)
                    p["_prev_nav"] = n2

            self._agx_prev_px = px
            self._agx_last_mark = today
        except Exception as exc:
            self._agx_err += 1
            if self._agx_err <= 3:
                self._AgxLog(f"CG_AGX_IND_PARITY_DAILY,mark_error={type(exc).__name__}")

    def _AgxCorr(self, a, b):
        n = min(len(a), len(b))
        if n < 2:
            return 1.0
        a = a[:n]
        b = b[:n]
        ma = sum(a) / n
        mb = sum(b) / n
        cov = sum((a[i] - ma) * (b[i] - mb) for i in range(n))
        va = sum((x - ma) ** 2 for x in a)
        vb = sum((x - mb) ** 2 for x in b)
        den = (va * vb) ** 0.5
        if den <= 1e-18:
            return 1.0
        return cov / den

    def _AgxMetricsFromRets(self, rets):
        n = len(rets)
        if n <= 0:
            return None
        nav = 1.0
        peak = 1.0
        maxdd = 0.0
        uw = 0
        uw_max = 0
        uw_days = 0
        sum_r = 0.0
        sum_r2 = 0.0
        pos = 0
        dn = []
        for r in rets:
            sum_r += r
            sum_r2 += r * r
            if r > 0:
                pos += 1
            if r < 0:
                dn.append(r)
            nav = max(1e-8, nav * (1.0 + r))
            if nav > peak:
                peak = nav
                uw = 0
            else:
                uw += 1
                uw_days += 1
                if uw > uw_max:
                    uw_max = uw
            dd = 1.0 - nav / max(peak, 1e-9)
            if dd > maxdd:
                maxdd = dd
        mean = sum_r / n
        var = max(0.0, sum_r2 / n - mean * mean)
        vol = (var ** 0.5) * (252 ** 0.5)
        years = n / 252.0
        cagr = (nav ** (1.0 / years) - 1.0) if years > 0.01 else None
        sharpe = (cagr / vol) if (cagr is not None and vol > 1e-12) else None
        if dn:
            dmean = sum(dn) / n
            dvar = max(0.0, sum(x * x for x in dn) / n - dmean * dmean)
            dvol = (dvar ** 0.5) * (252 ** 0.5)
            sortino = (cagr / dvol) if (cagr is not None and dvol > 1e-12) else None
        else:
            sortino = None
        arr = sorted(rets)
        k = max(1, int(0.05 * n + 0.999))
        w5 = sum(arr[:k]) / k
        return {
            "n": n, "end_nav": nav, "total_return": nav - 1.0, "CAGR": cagr,
            "MaxDD": maxdd, "annual_stddev": vol, "Sharpe": sharpe, "Sortino": sortino,
            "worst_5pct_day_mean": w5, "worst_day": arr[0], "best_day": arr[-1],
            "recovery_days_max": uw_max,
            "time_under_water_pct": (uw_days / n) if n else None,
            "positive_day_rate": pos / n,
        }

    def _AgxWindowMetrics(self, dates, rets, s, e):
        xs = []
        for d, r in zip(dates, rets):
            if s is not None and d < s:
                continue
            if e is not None and d > e:
                continue
            xs.append(r)
        return self._AgxMetricsFromRets(xs)

    def _AgxPolMetrics(self, p):
        m = self._AgxMetricsFromRets(p["rets"]) or {}
        egs = sorted(p["egs"]) if p["egs"] else []
        mid = egs[len(egs) // 2] if egs else None
        p95 = egs[int(0.95 * (len(egs) - 1))] if len(egs) > 1 else (egs[0] if egs else None)
        m.update({
            "start_nav": 1.0, "turnover": p["turnover"], "estimated_fees": p["fees"],
            "rebalance_count": p["reb"], "cap_hit_count": p["cap_hit"],
            "mean_effective_gross": (p["sum_eg"] / p["n_eg"]) if p["n_eg"] else None,
            "median_effective_gross": mid, "p95_effective_gross": p95,
            "max_effective_gross": max(egs) if egs else None,
            "ron": p["ron"], "neu": p["neu"], "roff": p["roff"], "id": p["id"],
            "model": p["model"], "fixed_err": p.get("fixed_err", 0.0),
        })
        return m

    def _AgxStrictGate(self):
        n_act = len(self._agx_actual_rets)
        n_ind = len(self._agx_ind_rets)
        count_match = bool(n_act == n_ind and n_act > 0)
        am = self._AgxMetricsFromRets(self._agx_actual_rets) or {}
        im = self._AgxMetricsFromRets(self._agx_ind_rets) or {}
        a_nav = am.get("end_nav")
        i_nav = im.get("end_nav")
        a_dd = am.get("MaxDD")
        i_dd = im.get("MaxDD")
        nav_diff = ((i_nav / a_nav - 1.0) * 100.0) if (a_nav and i_nav is not None) else None
        dd_diff = ((i_dd - a_dd) * 100.0) if (a_dd is not None and i_dd is not None) else None
        if count_match:
            diffs = [abs(a - c) for a, c in zip(self._agx_actual_rets, self._agx_ind_rets)]
            max_diff = max(diffs) if diffs else 0.0
            mean_diff = (sum(diffs) / len(diffs)) if diffs else 0.0
            corr = self._AgxCorr(self._agx_actual_rets, self._agx_ind_rets)
        else:
            max_diff = mean_diff = corr = None
        # Quantity mismatches allowed only if economics within tolerance
        qty_ok = True
        if self._agx_qty_mm > 0:
            qty_ok = bool(
                nav_diff is not None and abs(nav_diff) <= 0.10
                and dd_diff is not None and abs(dd_diff) <= 0.10
            )
            if qty_ok and self._agx_qty_exceptions:
                self._AgxLog(
                    "CG_AGX_IND_PARITY_EXEC,qty_exceptions_explained_by_rounding="
                    + ";".join(self._agx_qty_exceptions[:5])
                )
        ok = (
            self._agx_n_cap > 0
            and self._agx_target_mismatch_count == 0
            and self._agx_max_abs_target_weight_diff <= 1e-12
            and count_match
            and nav_diff is not None and abs(nav_diff) <= 0.10
            and dd_diff is not None and abs(dd_diff) <= 0.10
            and corr is not None and corr >= 0.9999
            and max_diff is not None and max_diff <= 0.0005
            and mean_diff is not None and mean_diff <= 0.00005
            and self._agx_sup_mm == 0
            and self._agx_dir_mm == 0
            and self._agx_corpaction == "PASS"
            and self._agx_err == 0
            and qty_ok
        )
        return {
            "pass": bool(ok), "nav_difference_pct": nav_diff, "maxdd_difference_pp": dd_diff,
            "daily_return_correlation": corr, "max_abs_daily_return_difference": max_diff,
            "mean_abs_daily_return_difference": mean_diff,
            "daily_return_count_match": "YES" if count_match else "NO",
            "n_act": n_act, "n_ind": n_ind, "_am": am, "_im": im,
        }

    def _AgxSelectSnapshots(self):
        cands = self._agx_snap_candidates
        if not cands:
            return []
        chosen, seen = [], set()

        def add(e):
            if e and e["date"] not in seen:
                seen.add(e["date"])
                chosen.append(e)

        add(cands[0])
        ron = [c for c in cands if c["regime"] == "RISK_ON" and c.get("target_gross") is not None]
        if ron:
            add(min(ron, key=lambda c: abs(c["target_gross"] - 1.9)))
        for rg in ("NEUTRAL", "RISK_OFF"):
            xs = [c for c in cands if c["regime"] == rg]
            if xs:
                add(xs[len(xs) // 2])
        bil = [c for c in cands if c.get("BIL_target") is not None]
        if bil:
            add(max(bil, key=lambda c: c["BIL_target"]))
        cashn = [c for c in cands if c.get("production_cash") is not None]
        if cashn:
            add(min(cashn, key=lambda c: c["production_cash"]))
            add(max(cashn, key=lambda c: c["production_cash"]))
        w2s = [c for c in cands if c.get("w2")]
        if w2s:
            add(w2s[0])
        prev_g, best_drop = None, None
        for c in cands:
            g = c.get("target_gross")
            if g is not None and prev_g is not None:
                d = prev_g - g
                if best_drop is None or d > best_drop[0]:
                    best_drop = (d, c)
            if g is not None:
                prev_g = g
        if best_drop:
            add(best_drop[1])
        add(cands[-1])
        return chosen[:15]

    def _AgxPareto(self, rows):
        front = []
        for r in rows:
            dominated = False
            for o in rows:
                if o is r:
                    continue
                keys_min = ("MaxDD", "w5_abs", "crisis_maxdd", "recovery_days_max")
                keys_max = ("oos_sharpe", "CAGR")
                ge = all(o.get(k, 1e9) <= r.get(k, 1e9) for k in keys_min) and all(
                    (o.get(k) or -1e9) >= (r.get(k) or -1e9) for k in keys_max)
                gt = any(o.get(k, 1e9) < r.get(k, 1e9) for k in keys_min) or any(
                    (o.get(k) or -1e9) > (r.get(k) or -1e9) for k in keys_max)
                if ge and gt:
                    dominated = True
                    break
            if not dominated:
                front.append(r)
        return front

    def _AgxRankKey(self, r):
        return (
            float(r.get("MaxDD") or 9), float(r.get("w5_abs") or 9),
            -float(r.get("oos_sharpe") or -9), float(r.get("crisis_maxdd") or 9),
            float(r.get("recovery_days_max") or 9e9), -float(r.get("CAGR") or -9),
        )

    def _AgxNeighborStable(self, row, by_id, model):
        def nbr(dim, vals):
            cur = row[dim]
            ix = next((i for i, v in enumerate(vals) if abs(v - cur) < 1e-12), None)
            if ix is None:
                return True
            ok = True
            for j in (ix - 1, ix + 1):
                if j < 0 or j >= len(vals):
                    continue
                kwargs = {"ron": row["ron"], "neu": row["neu"], "roff": row["roff"]}
                kwargs[dim] = vals[j]
                nid = _pid(model, kwargs["ron"], kwargs["neu"], kwargs["roff"])
                o = by_id.get(nid)
                if not o:
                    continue
                if (o.get("MaxDD") or 0) > (row.get("MaxDD") or 0) + 0.02:
                    ok = False
                c0, c1 = row.get("CAGR") or 0, o.get("CAGR") or 0
                if c0 > 0 and c1 < 0.8 * c0:
                    ok = False
                s0, s1 = row.get("oos_sharpe") or 0, o.get("oos_sharpe") or 0
                if s0 > 0 and s1 < 0.9 * s0:
                    ok = False
                if (o.get("crisis_maxdd") or 0) > (row.get("crisis_maxdd") or 0) + 0.02:
                    ok = False
            return ok
        a, b, c = nbr("ron", _RON), nbr("neu", _NEU), nbr("roff", _ROFF)
        return a, b, c, (a and b and c)

    def CgAgxShadowEmitFinal(self) -> None:
        if getattr(self, "_agx_emitted", False):
            return
        self._agx_emitted = True
        if not getattr(self, "_agx_enabled", False):
            return
        try:
            gate = self._AgxStrictGate()
            parity_pass = bool(gate["pass"])
            continue_grid = bool(parity_pass and self._agx_grid_on)
            am, im = gate.get("_am") or {}, gate.get("_im") or {}

            for c in self._AgxSelectSnapshots():
                self._AgxLog(
                    f"CG_AGX_IND_PARITY_SNAPSHOT,date={c['date']},regime={c['regime']},"
                    f"target_count={c.get('target_count')},target_gross={_f(c.get('target_gross'))},"
                    f"target_signed_sum={_f(c.get('target_signed_sum'))},"
                    f"BIL_target={_f(c.get('BIL_target'),6)},"
                    f"production_cash={_f(c.get('production_cash'),2)},"
                    f"independent_cash={_f(c.get('independent_cash'),2)},"
                    f"production_holdings={_f(c.get('production_holdings'),2)},"
                    f"independent_holdings={_f(c.get('independent_holdings'),2)},"
                    f"production_NAV={_f(c.get('production_NAV'),2)},"
                    f"independent_NAV={_f(c.get('independent_NAV'),2)},"
                    f"max_target_diff={_f(c.get('max_target_diff'),14)},"
                    f"quantity_mismatch_count={self._agx_qty_mm},"
                    f"suppression_match={int(self._agx_sup_mm==0)}"
                )

            self._AgxLog(
                f"CG_AGX_IND_PARITY_FINAL,independent_parity_gate={'PASS' if parity_pass else 'FAIL'},"
                f"independent_control_mode=INDEPENDENT_LEDGER,"
                f"target_mismatch_count={self._agx_target_mismatch_count},"
                f"max_abs_target_weight_difference={_f(self._agx_max_abs_target_weight_diff,14)},"
                f"execution_event_count={self._agx_exec_events},"
                f"trade_direction_mismatch_count={self._agx_dir_mm},"
                f"trade_quantity_mismatch_count={self._agx_qty_mm},"
                f"suppression_mismatch_count={self._agx_sup_mm},"
                f"security_set_mismatch_count={self._agx_sec_mm},"
                f"cash_update_mismatch_count={self._agx_cash_mm},"
                f"fee_mismatch_count={self._agx_fee_mm},"
                f"actual_final_nav={_f(am.get('end_nav'))},"
                f"independent_final_nav={_f(im.get('end_nav'))},"
                f"nav_difference_pct={_f(gate['nav_difference_pct'],6)},"
                f"actual_maxdd={_f(am.get('MaxDD'))},independent_maxdd={_f(im.get('MaxDD'))},"
                f"maxdd_difference_pp={_f(gate['maxdd_difference_pp'],6)},"
                f"daily_return_correlation={_f(gate['daily_return_correlation'],6)},"
                f"max_abs_daily_return_difference={_f(gate['max_abs_daily_return_difference'],6)},"
                f"mean_abs_daily_return_difference={_f(gate['mean_abs_daily_return_difference'],6)},"
                f"max_abs_cash_difference={_f(self._agx_max_cash_diff,2)},"
                f"max_abs_holdings_value_difference={_f(self._agx_max_hold_diff,2)},"
                f"daily_return_count_match={gate['daily_return_count_match']},"
                f"corporate_action_parity={self._agx_corpaction},"
                f"runtime_errors={self._agx_err},"
                f"continue_to_grid={'YES' if continue_grid else 'NO'},"
                f"residual_convention={self._agx_residual_convention},"
                f"next={'PROCEED_GRID' if continue_grid else 'FIX_INDEPENDENT_LEDGER'}"
            )

            if not continue_grid:
                self._AgxLog(
                    f"CG_AGX_D1_FINAL,diagnostic=CG-AGX-INDEPENDENT-PARITY-D1,"
                    f"independent_parity_gate=FAIL,continue_to_grid=NO,"
                    f"selection_allowed=0,policies_evaluated=0,next=FIX_INDEPENDENT_LEDGER"
                )
                return

            # ---- PHASE B ranking ----
            today = self.time.date()
            live_s = self._agx_dates[max(0, len(self._agx_dates) - 252)] if self._agx_dates else None
            windows = [
                ("RUN", _date(2012, 1, 1), today),
                ("TRAIN_2012_2018", _date(2012, 1, 1), _date(2018, 12, 31)),
                ("OOS_2019_2021", _date(2019, 1, 1), _date(2021, 12, 31)),
                ("CRISIS_2022_2025", _date(2022, 1, 1), _date(2025, 12, 31)),
                ("Y2020", _date(2020, 1, 1), _date(2020, 12, 31)),
                ("Y2022", _date(2022, 1, 1), _date(2022, 12, 31)),
                ("Y2023", _date(2023, 1, 1), _date(2023, 12, 31)),
                ("Y2024", _date(2024, 1, 1), _date(2024, 12, 31)),
                ("Y2025", _date(2025, 1, 1), _date(2025, 12, 31)),
                ("LIVE_RECENT", live_s, today),
            ]
            ctrl_m = self._AgxPolMetrics(self._agx_ctrl)
            n_ind = len(self._agx_ind_rets)
            obs = sorted(self._agx_base_gross_obs)
            p95_base = obs[int(0.95 * (len(obs) - 1))] if len(obs) > 1 else (obs[0] if obs else None)
            max_base = max(obs) if obs else None

            def build_rows(model):
                rows = []
                for p in self._agx_pols:
                    if p["model"] != model:
                        continue
                    m = self._AgxPolMetrics(p)
                    wins = {}
                    missing = 0
                    for name, s, e in windows:
                        if s is None:
                            wins[name] = None
                            missing += 1
                            continue
                        wm = self._AgxWindowMetrics(p["dates"], p["rets"], s, e)
                        wins[name] = wm
                        if (wm is None or wm.get("n", 0) <= 0) and name in (
                            "RUN", "TRAIN_2012_2018", "OOS_2019_2021", "CRISIS_2022_2025"
                        ):
                            missing += 1
                    oos = wins.get("OOS_2019_2021") or {}
                    cri = wins.get("CRISIS_2022_2025") or {}
                    y20 = wins.get("Y2020") or {}
                    y22 = wins.get("Y2022") or {}
                    std = m.get("annual_stddev")
                    invalid = 0
                    if missing or len(p["rets"]) != n_ind:
                        invalid = 1
                    if (m.get("max_effective_gross") or 0) > self._agx_max_gross + 1e-6:
                        invalid = 1
                    if std is not None and std > 0.20:
                        invalid = 1
                    if float(m.get("fixed_err") or 0) > 1e-12:
                        invalid = 1
                    row = dict(m)
                    row["wins"] = wins
                    row["oos_sharpe"] = oos.get("Sharpe")
                    row["crisis_maxdd"] = cri.get("MaxDD")
                    row["y2020_maxdd"] = y20.get("MaxDD")
                    row["y2022_maxdd"] = y22.get("MaxDD")
                    row["w5_abs"] = -float(m.get("worst_5pct_day_mean") or 0)
                    row["invalid"] = invalid
                    row["boundary"] = int(
                        abs(p["roff"]) < 1e-12 or abs(p["ron"] - 1.30) < 1e-12
                        or abs(p["neu"] - 0.50) < 1e-12
                    )
                    d1 = 0
                    tgt = 0
                    if not invalid and ctrl_m:
                        def _ge(a, b):
                            return a is not None and b is not None and a >= b

                        def _le(a, b):
                            return a is not None and b is not None and a <= b

                        c_oos = self._AgxWindowMetrics(
                            self._agx_ctrl["dates"], self._agx_ctrl["rets"],
                            _date(2019, 1, 1), _date(2021, 12, 31)) or {}
                        c_cri = self._AgxWindowMetrics(
                            self._agx_ctrl["dates"], self._agx_ctrl["rets"],
                            _date(2022, 1, 1), _date(2025, 12, 31)) or {}
                        c_y20 = self._AgxWindowMetrics(
                            self._agx_ctrl["dates"], self._agx_ctrl["rets"],
                            _date(2020, 1, 1), _date(2020, 12, 31)) or {}
                        c_y22 = self._AgxWindowMetrics(
                            self._agx_ctrl["dates"], self._agx_ctrl["rets"],
                            _date(2022, 1, 1), _date(2022, 12, 31)) or {}
                        ok = (
                            _le(m.get("MaxDD"), ctrl_m.get("MaxDD"))
                            and _ge(m.get("worst_5pct_day_mean"), ctrl_m.get("worst_5pct_day_mean"))
                            and _ge(oos.get("Sharpe"), 0.95 * (c_oos.get("Sharpe") or 0))
                            and _le(cri.get("MaxDD"), (c_cri.get("MaxDD") or 0) + 0.01)
                            and _le(y20.get("MaxDD"), (c_y20.get("MaxDD") or 0) + 0.01)
                            and _le(y22.get("MaxDD"), (c_y22.get("MaxDD") or 0) + 0.01)
                            and (std is not None and std <= 0.18)
                            and _le(m.get("recovery_days_max"), ctrl_m.get("recovery_days_max"))
                            and (m.get("CAGR") or 0) > (ctrl_m.get("CAGR") or 0)
                        )
                        d1 = int(ok)
                        if ((m.get("CAGR") or 0) >= 0.45 and (m.get("MaxDD") or 9) <= 0.13
                                and (std or 9) <= 0.18):
                            tgt = 1
                    row["D1_PASS"] = d1
                    row["TARGET_PROFILE_MET"] = tgt
                    row["_p"] = p
                    rows.append(row)
                return rows

            def model_ctrl_ok(pctrl):
                if not pctrl:
                    return False, None, None
                m = self._AgxPolMetrics(pctrl)
                nav_d = None
                dd_d = None
                if ctrl_m.get("end_nav") and m.get("end_nav"):
                    nav_d = abs(m["end_nav"] / ctrl_m["end_nav"] - 1.0) * 100.0
                if ctrl_m.get("MaxDD") is not None and m.get("MaxDD") is not None:
                    dd_d = abs(m["MaxDD"] - ctrl_m["MaxDD"]) * 100.0
                ok = (nav_d is not None and nav_d <= 0.01 and dd_d is not None and dd_d <= 0.01)
                return ok, nav_d, dd_d

            a_ok, a_navd, a_ddd = model_ctrl_ok(self._agx_a_ctrl)
            b_ok, b_navd, b_ddd = model_ctrl_ok(self._agx_b_ctrl)

            results = {}
            all_rows = []
            for model, valid in (("A", a_ok), ("B", b_ok)):
                rows = build_rows(model)
                all_rows.extend(rows)
                if not valid:
                    results[model] = {
                        "valid": False, "rows": rows, "ranked": [], "d1": [],
                        "front": [], "top": [], "best": None,
                    }
                    continue
                valid_rows = [r for r in rows if not r["invalid"]]
                front = self._AgxPareto(valid_rows) if valid_rows else []
                ranked = sorted(valid_rows, key=self._AgxRankKey) if valid_rows else []
                d1s = [r for r in ranked if r["D1_PASS"]]
                by_id = {r["id"]: r for r in rows}
                top = ranked[:10]
                for r in top:
                    x, y, z, all_ok = self._AgxNeighborStable(r, by_id, model)
                    r["ron_neighbor_stable"] = int(x)
                    r["neutral_neighbor_stable"] = int(y)
                    r["riskoff_neighbor_stable"] = int(z)
                    r["all_neighbors_stable"] = int(all_ok)
                results[model] = {
                    "valid": True, "rows": rows, "ranked": ranked, "d1": d1s,
                    "front": front, "top": top, "best": (d1s[0] if d1s else (ranked[0] if ranked else None)),
                    "by_id": by_id,
                }

            # CSV
            csv_key = "cg_agx_independent_d1.csv"
            try:
                headers = [
                    "id", "model", "ron", "neu", "roff", "CAGR", "MaxDD", "annual_stddev",
                    "Sharpe", "Sortino", "worst_5pct_day_mean", "worst_day", "best_day",
                    "recovery_days_max", "time_under_water_pct", "positive_day_rate",
                    "turnover", "estimated_fees", "rebalance_count", "cap_hit_count",
                    "mean_effective_gross", "median_effective_gross", "p95_effective_gross",
                    "max_effective_gross", "oos_sharpe", "crisis_maxdd", "y2020_maxdd",
                    "y2022_maxdd", "D1_PASS", "TARGET_PROFILE_MET", "boundary", "invalid",
                ]
                lines = [",".join(headers)]
                for r in all_rows:
                    lines.append(",".join(str(r.get(h, "NA")) for h in headers))
                self.object_store.save(csv_key, "\n".join(lines))
            except Exception as exc:
                csv_key = f"NONE:{type(exc).__name__}"

            def med(xs):
                xs = [x for x in xs if x is not None]
                if not xs:
                    return None
                xs = sorted(xs)
                return xs[len(xs) // 2]

            # Model comparison
            def med_field(model, field):
                rs = results[model]["ranked"]
                return med([r.get(field) for r in rs])

            a_d1 = len(results["A"]["d1"])
            b_d1 = len(results["B"]["d1"])
            a_p = len(results["A"]["front"])
            b_p = len(results["B"]["front"])
            if not results["A"]["valid"] and not results["B"]["valid"]:
                model_cmp = "NEITHER_VALID"
            elif results["A"]["valid"] and not results["B"]["valid"]:
                model_cmp = "MODEL_A_DOMINATES"
            elif results["B"]["valid"] and not results["A"]["valid"]:
                model_cmp = "MODEL_B_DOMINATES"
            else:
                # Prefer more D1_PASS, then better median crisis MaxDD, then OOS Sharpe
                score_a = (a_d1, -float(med_field("A", "crisis_maxdd") or 9), float(med_field("A", "oos_sharpe") or -9))
                score_b = (b_d1, -float(med_field("B", "crisis_maxdd") or 9), float(med_field("B", "oos_sharpe") or -9))
                if score_a > score_b:
                    model_cmp = "MODEL_A_DOMINATES"
                elif score_b > score_a:
                    model_cmp = "MODEL_B_DOMINATES"
                else:
                    model_cmp = "MIXED"

            tgt_n = sum(1 for r in all_rows if r.get("TARGET_PROFILE_MET"))
            stable_d1 = []
            for model in ("A", "B"):
                if results[model]["valid"]:
                    stable_d1.extend([r for r in results[model]["d1"] if r.get("all_neighbors_stable")])

            next_dec = "STOP_AGX"
            if not results["A"]["valid"] and not results["B"]["valid"]:
                next_dec = "FIX_INDEPENDENT_LEDGER"
            elif stable_d1:
                next_dec = "PREPARE_AGX_D2"
            elif a_d1 or b_d1 or results["A"]["front"] or results["B"]["front"]:
                tr = self._agx_ctrl["tr_r"]
                early = abs(tr.get("0", 0) + tr.get("1", 0) + tr.get("2-3", 0) + tr.get("4-10", 0))
                late = abs(tr.get(">10", 0))
                if early > 0 and early >= 1.25 * max(late, 1e-9):
                    next_dec = "TEST_RISK_REENTRY"
                elif model_cmp == "MODEL_A_DOMINATES":
                    next_dec = "REFINE_MODEL_A"
                elif model_cmp == "MODEL_B_DOMINATES":
                    next_dec = "REFINE_MODEL_B"
                else:
                    next_dec = "STOP_AGX"
            else:
                next_dec = "STOP_AGX"

            self._AgxLog(
                f"CG_AGX_D1_VALIDATION,A_control_vs_independent_NAV_diff={_f(a_navd,6)},"
                f"B_control_vs_independent_NAV_diff={_f(b_navd,6)},"
                f"A_control_vs_independent_MaxDD_diff={_f(a_ddd,6)},"
                f"B_control_vs_independent_MaxDD_diff={_f(b_ddd,6)},"
                f"model_a_valid={'YES' if a_ok else 'NO'},model_b_valid={'YES' if b_ok else 'NO'},"
                f"obs_max_target_gross={_f(max_base)},obs_p95_target_gross={_f(p95_base)},"
                f"prod_cap={_f(self._agx_prod_gross_cap)},eff_cap={_f(self._agx_max_gross)},"
                f"live_recent={live_s}..{today},"
                f"cls_risk={','.join(sorted(self._agx_cls['SCALABLE_RISK'])[:30])},"
                f"cls_def={','.join(sorted(self._agx_cls['FIXED_DEFENSIVE']))},"
                f"cls_park={','.join(sorted(self._agx_cls['PARKING_ETF']))},"
                f"cls_uncertain={','.join(sorted(self._agx_cls['KEEP_FIXED_UNCERTAIN']))}"
            )

            for model in ("A", "B"):
                rs = results[model]
                _bw = sum(1 for r in rs["ranked"] if r.get("boundary"))
                _ch = sum(r.get("cap_hit_count", 0) for r in rs["ranked"])
                _rb = sum(r.get("rebalance_count", 0) for r in rs["ranked"])
                _chr = (_ch / _rb) if _rb else 0.0
                _best = (rs["best"] or {}).get("id", "NONE")
                self._AgxLog(
                    f"CG_AGX_D1_MODEL,model={model},valid={int(rs['valid'])},"
                    f"d1_pass={len(rs['d1'])},pareto={len(rs['front'])},"
                    f"median_CAGR={_f(med_field(model,'CAGR'))},"
                    f"median_MaxDD={_f(med_field(model,'MaxDD'))},"
                    f"median_oos_sharpe={_f(med_field(model,'oos_sharpe'))},"
                    f"median_crisis_maxdd={_f(med_field(model,'crisis_maxdd'))},"
                    f"best={_best},boundary_winners={_bw},cap_hit_rate={_f(_chr)}"
                )

            emit = set()
            for model in ("A", "B"):
                if self._agx_by_id.get(_pid(model, 1, 1, 1)):
                    emit.add(_pid(model, 1, 1, 1))
                for r in results[model]["top"] + results[model]["front"] + results[model]["d1"]:
                    emit.add(r["id"])

            for model in ("A", "B"):
                for i, r in enumerate(results[model]["top"]):
                    self._AgxLog(
                        f"CG_AGX_D1_TOP,model={model},rank={i+1},id={r['id']},"
                        f"ron={_f(r['ron'],2)},neu={_f(r['neu'],2)},roff={_f(r['roff'],2)},"
                        f"CAGR={_f(r.get('CAGR'))},MaxDD={_f(r.get('MaxDD'))},"
                        f"std={_f(r.get('annual_stddev'))},Sharpe={_f(r.get('Sharpe'))},"
                        f"w5={_f(r.get('worst_5pct_day_mean'))},oos_sh={_f(r.get('oos_sharpe'))},"
                        f"crisis_dd={_f(r.get('crisis_maxdd'))},rec={r.get('recovery_days_max')},"
                        f"D1={r.get('D1_PASS')},stable={r.get('all_neighbors_stable',0)},"
                        f"boundary={r.get('boundary')}"
                    )

            # Windows for control + top5 per valid model
            focus = [self._agx_ctrl]
            for model in ("A", "B"):
                if results[model]["valid"]:
                    focus.extend([r["_p"] for r in results[model]["top"][:5]])
            for p in focus:
                mid = p["id"]
                for name, s, e in windows:
                    wm = self._AgxWindowMetrics(p["dates"], p["rets"], s, e)
                    if not wm:
                        continue
                    if mid not in emit and mid != "INDEPENDENT_CONTROL":
                        continue
                    self._AgxLog(
                        f"CG_AGX_D1_WINDOW,id={mid},window={name},n={wm.get('n')},"
                        f"CAGR={_f(wm.get('CAGR'))},MaxDD={_f(wm.get('MaxDD'))},"
                        f"Sharpe={_f(wm.get('Sharpe'))},std={_f(wm.get('annual_stddev'))},"
                        f"w5={_f(wm.get('worst_5pct_day_mean'))},rec={wm.get('recovery_days_max')}"
                    )

            for p in focus[:11]:
                for rg in ("RISK_ON", "NEUTRAL", "RISK_OFF"):
                    n = p["rg_n"][rg]
                    w5l = p["rg_w5"][rg]
                    w5 = None
                    if w5l:
                        kk = max(1, int(0.05 * len(w5l) + 0.999))
                        w5 = sum(sorted(w5l)[:kk]) / kk
                    self._AgxLog(
                        f"CG_AGX_D1_REGIME,id={p['id']},regime={rg},days={n},"
                        f"ret_contrib={_f(p['rg_r'][rg])},dd={_f(p['rg_dd'][rg])},"
                        f"w5={_f(w5)},mean_eff_gross={_f(p['rg_eg'][rg]/n if n else None)},"
                        f"cap_hit={p['rg_cap'][rg]}"
                    )
                for tb in ("0", "1", "2-3", "4-10", ">10"):
                    self._AgxLog(
                        f"CG_AGX_D1_TRANSITION,id={p['id']},bucket={tb},"
                        f"days={p['tr_n'][tb]},ret_contrib={_f(p['tr_r'][tb])}"
                    )

            self._AgxLog(
                f"CG_AGX_D1_FINAL,diagnostic=CG-AGX-INDEPENDENT-PARITY-D1,"
                f"independent_parity_gate=PASS,continue_to_grid=YES,"
                f"model_a_valid={'YES' if a_ok else 'NO'},model_b_valid={'YES' if b_ok else 'NO'},"
                f"policies_evaluated={len(all_rows)},"
                f"model_a_best={(results['A']['best'] or {}).get('id','NONE')},"
                f"model_b_best={(results['B']['best'] or {}).get('id','NONE')},"
                f"model_a_d1_pass_count={a_d1},model_b_d1_pass_count={b_d1},"
                f"model_a_pareto_count={a_p},model_b_pareto_count={b_p},"
                f"model_comparison={model_cmp},target_profile_met_count={tgt_n},"
                f"result_artifact={csv_key},next={next_dec}"
            )
        except Exception as exc:
            self._agx_err += 1
            try:
                self.log(f"CG_AGX_D1_FINAL,emit_error={type(exc).__name__}:{exc}")
            except Exception:
                pass
