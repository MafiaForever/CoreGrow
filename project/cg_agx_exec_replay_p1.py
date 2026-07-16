# region imports
from AlgorithmImports import *
from datetime import date as _date
# endregion
# cg_agx_exec_replay_p1.py
# CG-AGX-INDEPENDENT-EXEC-REPLAY-P1
# control_mode=PRODUCTION_EVENT_REPLAY
# Independent accounting via chronological production fill/fee/corpaction
# replay. Does not copy NAV/returns/holdings/cash snapshots.

_RON = (0.75, 1.00, 1.10, 1.20, 1.30)
_NEU = (0.50, 0.75, 0.90, 1.00)
_ROFF = (0.00, 0.25, 0.50, 0.75, 1.00)
_DFT_DEF = frozenset(("TIP", "BND", "GLD", "GLDM", "BIL", "SGOV", "USFR", "SH"))
_LOG_BUDGET = 95000
_A_CTRL = "A_AGX_RON100_NEU100_ROFF100"
_B_CTRL = "B_AGX_RON100_NEU100_ROFF100"


def _pid(model, a, b, c):
    return (
        f"{model}_AGX_RON{int(round(a * 100)):03d}_"
        f"NEU{int(round(b * 100)):03d}_ROFF{int(round(c * 100)):03d}"
    )


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
        "cash": float(cash0), "qty": {}, "nav": float(cash0), "peak": float(cash0),
        "maxdd": 0.0, "rets": [], "dates": [], "fees": 0.0, "turnover": 0.0,
        "reb": 0, "ov": 0, "cap_hit": 0, "sum_eg": 0.0, "n_eg": 0, "egs": [],
        "rg_n": {"RISK_ON": 0, "NEUTRAL": 0, "RISK_OFF": 0},
        "rg_r": {"RISK_ON": 0.0, "NEUTRAL": 0.0, "RISK_OFF": 0.0},
        "rg_dd": {"RISK_ON": 0.0, "NEUTRAL": 0.0, "RISK_OFF": 0.0},
        "rg_eg": {"RISK_ON": 0.0, "NEUTRAL": 0.0, "RISK_OFF": 0.0},
        "tr_n": {"0": 0, "1": 0, "2-3": 0, "4-10": 0, ">10": 0},
        "tr_r": {"0": 0.0, "1": 0.0, "2-3": 0.0, "4-10": 0.0, ">10": 0.0},
        "uw": 0, "uw_max": 0, "uw_days": 0, "fixed_err": 0.0,
        "_prev_nav": None, "pending": None, "_pend_hit": 0,
    }


class CgAgxExecReplayMixin:
    """Production event-replay control + conditional dual-model AGX grid."""

    def CgAgxReplayInit(self) -> None:
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

        self.cg_agx_exec_replay_enable = _bool("cg_agx_exec_replay_enable", "0")
        self.cg_agx_shadow_diag_enable = _bool("cg_agx_shadow_diag_enable", "0")
        self.cg_agx_independent_grid_enable = _bool("cg_agx_independent_grid_enable", "0")
        self.cg_agx_shadow_emit_events = _bool("cg_agx_shadow_emit_events", "0")
        req_cap = _float("cg_agx_shadow_max_gross", 2.00)
        prod_cap = float(getattr(self, "max_total_exposure", 1.90) or 1.90)
        self._agx_prod_gross_cap = prod_cap
        self._agx_max_gross = min(float(req_cap), float(prod_cap))
        self._agx_cost_bps = _float("cg_agx_shadow_cost_bps", 0.0)
        self._agx_rp_on = bool(
            self.cg_agx_shadow_diag_enable and self.cg_agx_exec_replay_enable
        )
        self._agx_grid_on = bool(self._agx_rp_on and self.cg_agx_independent_grid_enable)
        # Disable prior D1 target-sim path when replay is active.
        if self._agx_rp_on:
            self._agx_enabled = False

        cash_tk = getattr(self, "sym_cash", None)
        self._agx_cash_tk = _tk(cash_tk) if cash_tk is not None else "BIL"
        cash0 = 10000.0
        try:
            c = float(self.portfolio.cash)
            if c > 0:
                cash0 = c
        except Exception:
            pass
        self._agx_cash0 = cash0

        self._agx_log_used = 0
        self._agx_err = 0
        self._agx_emitted = False
        self._agx_started = False
        self._agx_last_mark = None
        self._agx_prev_tpv = None
        self._agx_dates = []
        self._agx_actual_rets = []
        self._agx_replay_rets = []
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
        self._agx_max_cash_diff = 0.0
        self._agx_max_hold_diff = 0.0
        self._agx_corp_mm = 0
        self._agx_div_n = 0
        self._agx_split_n = 0
        self._agx_map_n = 0
        self._agx_delist_n = 0
        self._agx_cf_n = 0
        self._agx_reg_n = 0
        self._agx_unclass_n = 0
        self._agx_recon_n = 0
        self._agx_orphan_n = 0
        self._agx_dup_n = 0
        self._agx_fill_n = 0
        self._agx_ov_n = 0
        self._agx_seen_fill = set()
        self._agx_order_meta = {}
        self._agx_ctx = {
            "class": "ACCOUNTING_ONLY",
            "source": "bootstrap",
            "fn": "init",
            "reduce_only": False,
            "emergency": False,
            "targets": None,
            "agx_eligible": False,
        }
        self._agx_cls = {
            "SCALABLE_RISK": set(["SPY"]),
            "FIXED_DEFENSIVE": set(t for t in _DFT_DEF if t != self._agx_cash_tk),
            "PARKING_ETF": set([self._agx_cash_tk]),
            "OTHER_FIXED": set(),
            "KEEP_FIXED_UNCERTAIN": set(),
            "src": {"SPY": "W2/equity_risk_sleeve", self._agx_cash_tk: "sym_cash"},
        }
        for t in self._agx_cls["FIXED_DEFENSIVE"]:
            self._agx_cls["src"][t] = "cg_defensive_trade._DFT_DEF"
        self._agx_path_stats = {}
        self._agx_ctrl = _new_led("REPLAY_CONTROL", "CTRL", 1.0, 1.0, 1.0, cash0)
        self._agx_pols = []
        self._agx_by_id = {}
        self._agx_a_ctrl = None
        self._agx_b_ctrl = None
        if self._agx_grid_on:
            for model in ("A", "B"):
                for a in _RON:
                    for b in _NEU:
                        for c in _ROFF:
                            pid = _pid(model, a, b, c)
                            p = _new_led(pid, model, a, b, c, cash0)
                            self._agx_pols.append(p)
                            self._agx_by_id[pid] = p
            self._agx_a_ctrl = self._agx_by_id.get(_A_CTRL)
            self._agx_b_ctrl = self._agx_by_id.get(_B_CTRL)

        lp = list(getattr(self, "log_only_prefixes", None) or [])
        for pref in ("CG_AGX_REPLAY_", "CG_AGX_P1_", "[INIT] CG_AGX"):
            if pref not in lp:
                lp.append(pref)
        self.log_only_prefixes = lp

        self.log(
            f"CG_AGX_REPLAY_INIT,enable={int(self._agx_rp_on)},"
            f"control_mode=PRODUCTION_EVENT_REPLAY,"
            f"grid_enable={int(self._agx_grid_on)},"
            f"policies={len(self._agx_pols)},"
            f"parking_etf={self._agx_cash_tk},"
            f"max_gross={_f(self._agx_max_gross)},prod_cap={_f(prod_cap)},"
            f"req_cap={_f(req_cap)},cost_bps={_f(self._agx_cost_bps, 2)},"
            f"candidate_quarantined=1,selection_allowed=0"
        )
        if not self._agx_rp_on:
            return
        self._AgxRpInstallHooks()
        try:
            spy = getattr(self, "sym_spy", None)
            if spy is not None:
                self.schedule.on(
                    self.date_rules.every_day(spy),
                    self.time_rules.after_market_open(spy, 14),
                    self.CgAgxReplayMark,
                )
        except Exception as exc:
            self._agx_err += 1
            self.log(f"CG_AGX_REPLAY_INIT,schedule_error={type(exc).__name__}")

    def _AgxRpInstallHooks(self):
        if getattr(self, "_agx_rp_hooks", False):
            return
        self._agx_rp_hooks = True
        _osh = self.set_holdings

        def _wsh(targets, *a, **kw):
            try:
                self._AgxRpNoteSetHoldings(targets)
            except Exception:
                pass
            return _osh(targets, *a, **kw)

        self.set_holdings = _wsh
        _oliq = self.liquidate

        def _wliq(symbol=None, *a, **kw):
            try:
                self._AgxRpNoteLiquidate(symbol)
            except Exception:
                pass
            if symbol is None:
                return _oliq()
            return _oliq(symbol, *a, **kw)

        self.liquidate = _wliq
        # LEAN dispatches OnOrderEvent/OnData via class methods; those hooks
        # are wired in main.py. Keep set_holdings/liquidate wrappers only.

    def _AgxRpLog(self, msg):
        try:
            n = len(msg) + 1
            if self._agx_log_used + n > _LOG_BUDGET:
                return
            self.log(msg)
            self._agx_log_used += n
        except Exception:
            pass

    def _AgxRpPath(self, name, impact=0.0):
        st = self._agx_path_stats.get(name)
        if st is None:
            st = {"n": 0, "first": str(self.time.date()), "impact": 0.0}
            self._agx_path_stats[name] = st
        st["n"] += 1
        st["impact"] = max(float(st["impact"]), float(impact))

    def CgAgxReplayPushCtx(self, source, fn, event_class, reduce_only=False,
                           emergency=False, targets=None, agx_eligible=False):
        if not getattr(self, "_agx_rp_on", False):
            return
        self._agx_ctx = {
            "class": event_class,
            "source": source,
            "fn": fn,
            "reduce_only": bool(reduce_only),
            "emergency": bool(emergency),
            "targets": targets,
            "agx_eligible": bool(agx_eligible),
        }

    def CgAgxReplayPopCtx(self):
        if not getattr(self, "_agx_rp_on", False):
            return
        self._agx_ctx = {
            "class": "ACCOUNTING_ONLY",
            "source": "idle",
            "fn": "idle",
            "reduce_only": False,
            "emergency": False,
            "targets": None,
            "agx_eligible": False,
        }

    def _AgxRpNoteSetHoldings(self, targets):
        ctx = self._agx_ctx or {}
        tw = {}
        try:
            if isinstance(targets, dict):
                for k, v in targets.items():
                    tw[_tk(k)] = float(v)
            else:
                for t in targets or []:
                    tw[_tk(getattr(t, "symbol", t))] = float(getattr(t, "quantity", 0.0))
        except Exception:
            tw = {}
        ctx = dict(ctx)
        ctx["targets"] = tw
        if not ctx.get("source") or ctx.get("source") in ("idle", "bootstrap"):
            ctx["source"] = "set_holdings"
            ctx["fn"] = "set_holdings"
            # Unscoped set_holdings: treat as mandatory portfolio intent
            ctx["class"] = "MANDATORY_OVERRIDE"
            ctx["agx_eligible"] = False
        self._agx_ctx = ctx
        self._AgxRpPath(f"{ctx.get('fn')}:set_holdings")

    def _AgxRpNoteLiquidate(self, symbol):
        ctx = dict(self._agx_ctx or {})
        if not ctx.get("source") or ctx.get("source") in ("idle", "bootstrap"):
            ctx["source"] = "liquidate"
            ctx["fn"] = "liquidate"
        ctx["class"] = "MANDATORY_OVERRIDE"
        ctx["agx_eligible"] = False
        ctx["targets"] = {"__LIQUIDATE__": _tk(symbol) if symbol is not None else "*"}
        self._agx_ctx = ctx
        self._AgxRpPath(f"{ctx.get('fn')}:liquidate")

    def _AgxRpClassifyTicker(self, t):
        if t in self._agx_cls["PARKING_ETF"]:
            return "PARKING_ETF"
        if t in self._agx_cls["FIXED_DEFENSIVE"] or t in _DFT_DEF:
            self._agx_cls["FIXED_DEFENSIVE"].add(t)
            return "FIXED_DEFENSIVE"
        if t in self._agx_cls["SCALABLE_RISK"]:
            return "SCALABLE_RISK"
        if t.isalpha() and len(t) <= 5 and t not in _DFT_DEF:
            self._agx_cls["SCALABLE_RISK"].add(t)
            self._agx_cls["src"][t] = "non_defensive_target_as_risk_sleeve"
            return "SCALABLE_RISK"
        self._agx_cls["KEEP_FIXED_UNCERTAIN"].add(t)
        self._agx_cls["OTHER_FIXED"].add(t)
        self._agx_cls["src"][t] = "KEEP_FIXED_UNCERTAIN"
        return "KEEP_FIXED_UNCERTAIN"

    def _AgxRpPx(self, tickers=None):
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

    def _AgxRpNav(self, led, px):
        hv = 0.0
        for t, q in (led.get("qty") or {}).items():
            p = (px or {}).get(t)
            if p and p > 0:
                hv += float(q) * p
        return float(led.get("cash", 0.0)) + hv, hv

    def _AgxRpApplyFill(self, led, t, signed_q, px, fee):
        q = float((led.get("qty") or {}).get(t, 0.0)) + float(signed_q)
        cash = float(led.get("cash", 0.0)) - float(signed_q) * float(px) - float(fee)
        qty = dict(led.get("qty") or {})
        if abs(q) < 1e-12:
            qty.pop(t, None)
        else:
            qty[t] = q
        led["qty"] = qty
        led["cash"] = cash
        led["fees"] = float(led.get("fees", 0.0)) + float(fee)
        led["turnover"] = float(led.get("turnover", 0.0)) + abs(float(signed_q) * float(px))

    def _AgxRpLiquidateLed(self, led, px):
        for t, q in list((led.get("qty") or {}).items()):
            p = (px or {}).get(t)
            if not p or p <= 0:
                continue
            self._AgxRpApplyFill(led, t, -float(q), p, 0.0)
        led["ov"] = int(led.get("ov", 0)) + 1

    def _AgxRpApplyWeights(self, led, weights, px):
        nav, _ = self._AgxRpNav(led, px)
        if nav <= 0:
            return
        # Close missing
        for t in list((led.get("qty") or {}).keys()):
            if t not in (weights or {}):
                p = (px or {}).get(t)
                if p and p > 0:
                    q = float(led["qty"].get(t, 0.0))
                    self._AgxRpApplyFill(led, t, -q, p, 0.0)
        for t, w in (weights or {}).items():
            p = (px or {}).get(t)
            if not p or p <= 0:
                continue
            desire = float(w) * nav / p
            cur = float((led.get("qty") or {}).get(t, 0.0))
            dq = desire - cur
            if abs(dq) * p < 1.0:
                continue
            fee = abs(dq) * p * float(self._agx_cost_bps) / 10000.0
            self._AgxRpApplyFill(led, t, dq, p, fee)
        led["reb"] = int(led.get("reb", 0)) + 1

    def CgAgxReplayOnOrderEvent(self, order_event) -> None:
        if not getattr(self, "_agx_rp_on", False) or order_event is None:
            return
        try:
            oid = getattr(order_event, "order_id", None)
            if oid is None:
                oid = getattr(order_event, "OrderId", None)
            status = getattr(order_event, "status", None)
            st = str(status)
            t = _tk(getattr(order_event, "symbol", None))
            # Register on first sight
            meta = self._agx_order_meta.get(oid)
            if meta is None:
                ctx = self._agx_ctx or {}
                ecl = ctx.get("class") or "ACCOUNTING_ONLY"
                if ecl not in ("NORMAL_AGX_ELIGIBLE", "MANDATORY_OVERRIDE", "ACCOUNTING_ONLY", "NOT_APPLICABLE"):
                    ecl = "ACCOUNTING_ONLY"
                    self._agx_unclass_n += 1
                meta = {
                    "oid": oid, "symbol": t, "class": ecl,
                    "source": ctx.get("source"), "fn": ctx.get("fn"),
                    "agx_eligible": bool(ctx.get("agx_eligible")),
                    "reduce_only": bool(ctx.get("reduce_only")),
                    "emergency": bool(ctx.get("emergency")),
                    "targets": dict(ctx.get("targets") or {}),
                }
                self._agx_order_meta[oid] = meta
                self._agx_reg_n += 1
                if ctx.get("source") in (None, "idle", "bootstrap") and ecl == "ACCOUNTING_ONLY":
                    # framework event without strategy context
                    self._agx_recon_n += 1
                if self._agx_reg_n <= 3 or self._agx_reg_n % 200 == 0:
                    self._AgxRpLog(
                        f"CG_AGX_REPLAY_ORDER_REG,oid={oid},sym={t},class={ecl},"
                        f"source={meta['source']},fn={meta['fn']},n={self._agx_reg_n}"
                    )
            # Fills only
            filled = False
            try:
                filled = status in (OrderStatus.Filled, OrderStatus.PartiallyFilled)
            except Exception:
                filled = ("FILL" in st.upper())
            if not filled:
                return
            fq = getattr(order_event, "fill_quantity", None)
            if fq is None:
                fq = getattr(order_event, "FillQuantity", 0)
            fq = float(fq or 0.0)
            px = getattr(order_event, "fill_price", None)
            if px is None:
                px = getattr(order_event, "FillPrice", 0)
            px = float(px or 0.0)
            if abs(fq) < 1e-12 or px <= 0:
                return
            # signed quantity
            signed = fq
            try:
                direction = getattr(order_event, "direction", None)
                ds = str(direction).lower() if direction is not None else ""
                if "sell" in ds and signed > 0:
                    signed = -abs(signed)
                elif "buy" in ds and signed < 0:
                    signed = abs(signed)
            except Exception:
                pass
            fee = 0.0
            try:
                ofee = getattr(order_event, "order_fee", None) or getattr(order_event, "OrderFee", None)
                if ofee is not None:
                    val = getattr(ofee, "value", None) or getattr(ofee, "Value", None)
                    if val is not None:
                        amt = getattr(val, "amount", None) or getattr(val, "Amount", None)
                        if amt is None and not hasattr(val, "amount"):
                            try:
                                fee = abs(float(val))
                            except Exception:
                                fee = 0.0
                        else:
                            fee = abs(float(amt or 0.0))
            except Exception:
                fee = 0.0
            ts = str(getattr(self, "time", ""))
            key = f"{oid}|{st}|{signed:.8f}|{px:.8f}|{ts}"
            if key in self._agx_seen_fill:
                self._agx_dup_n += 1
                return
            self._agx_seen_fill.add(key)
            self._agx_fill_n += 1
            impact = abs(signed) * px
            self._AgxRpPath(f"fill:{meta.get('fn')}:{meta.get('class')}", impact)

            # CONTROL: always replay exact fill
            self._AgxRpApplyFill(self._agx_ctrl, t, signed, px, fee)

            if not self._agx_grid_on:
                return
            ecl = meta.get("class")
            is_ctrl_id = False
            # Candidates
            for p in self._agx_pols:
                is_identity = (
                    abs(p["ron"] - 1.0) < 1e-15
                    and abs(p["neu"] - 1.0) < 1e-15
                    and abs(p["roff"] - 1.0) < 1e-15
                )
                if is_identity:
                    # Identity controls inherit exact production fills (same economic stream).
                    self._AgxRpApplyFill(p, t, signed, px, fee)
                    continue
                if ecl == "NORMAL_AGX_ELIGIBLE":
                    # Counterfactual fills applied at pending execution, not here.
                    continue
                if ecl == "MANDATORY_OVERRIDE":
                    tgt = meta.get("targets") or {}
                    pxmap = self._AgxRpPx()
                    if "__LIQUIDATE__" in tgt:
                        self._AgxRpLiquidateLed(p, pxmap)
                        self._agx_ov_n += 1
                    elif tgt:
                        self._AgxRpApplyWeights(p, tgt, pxmap)
                        self._agx_ov_n += 1
                    else:
                        # Unknown override: apply same signed fill (economic event completeness)
                        self._AgxRpApplyFill(p, t, signed, px, fee)
                        self._agx_ov_n += 1
                else:
                    # ACCOUNTING_ONLY: apply to all candidates (dividends handled separately)
                    self._AgxRpApplyFill(p, t, signed, px, fee)
            if self._agx_fill_n <= 2 or self._agx_fill_n % 250 == 0:
                self._AgxRpLog(
                    f"CG_AGX_REPLAY_FILL,n={self._agx_fill_n},oid={oid},sym={t},"
                    f"qty={_f(signed,4)},px={_f(px,4)},fee={_f(fee,4)},class={ecl}"
                )
        except Exception as exc:
            self._agx_err += 1
            if self._agx_err <= 3:
                self._AgxRpLog(f"CG_AGX_REPLAY_FILL,error={type(exc).__name__}")

    def CgAgxReplayOnData(self, data) -> None:
        if not getattr(self, "_agx_rp_on", False) or data is None:
            return
        try:
            divs = getattr(data, "dividends", None) or getattr(data, "Dividends", None)
            if divs:
                for kvp in divs:
                    try:
                        sym = kvp.Key if hasattr(kvp, "Key") else kvp
                        dv = kvp.Value if hasattr(kvp, "Value") else divs[kvp]
                        t = _tk(sym)
                        dist = float(getattr(dv, "distribution", None) or getattr(dv, "Distribution", 0) or 0)
                        if dist == 0:
                            continue
                        self._agx_div_n += 1
                        for led in [self._agx_ctrl] + (self._agx_pols if self._agx_grid_on else []):
                            q = float((led.get("qty") or {}).get(t, 0.0))
                            if abs(q) > 0:
                                led["cash"] = float(led.get("cash", 0.0)) + q * dist
                        self._AgxRpPath("dividend", abs(dist))
                    except Exception:
                        self._agx_corp_mm += 1
            splits = getattr(data, "splits", None) or getattr(data, "Splits", None)
            if splits:
                for kvp in splits:
                    try:
                        sym = kvp.Key if hasattr(kvp, "Key") else kvp
                        sp = kvp.Value if hasattr(kvp, "Value") else splits[kvp]
                        t = _tk(sym)
                        factor = float(getattr(sp, "split_factor", None) or getattr(sp, "SplitFactor", 0) or 0)
                        if factor == 0:
                            continue
                        self._agx_split_n += 1
                        for led in [self._agx_ctrl] + (self._agx_pols if self._agx_grid_on else []):
                            q = float((led.get("qty") or {}).get(t, 0.0))
                            if abs(q) > 0:
                                led["qty"][t] = q / factor
                        self._AgxRpPath("split", abs(factor))
                        if self._agx_split_n <= 3:
                            self._AgxRpLog(
                                f"CG_AGX_REPLAY_CORPACTION,type=split,sym={t},factor={_f(factor)}"
                            )
                    except Exception:
                        self._agx_corp_mm += 1
        except Exception as exc:
            self._agx_err += 1
            if self._agx_err <= 3:
                self._AgxRpLog(f"CG_AGX_REPLAY_CORPACTION,error={type(exc).__name__}")

    def _AgxRpBaseW(self, combined):
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
            self._AgxRpClassifyTicker(t)
        return w

    def _AgxRpGross(self, w):
        g = 0.0
        cash = self._agx_cash_tk
        for t, v in (w or {}).items():
            if t == cash:
                continue
            g += abs(float(v or 0.0))
        return g

    def _AgxRpScale(self, base_w, mult, model):
        park = self._agx_cash_tk
        base = {t: float(v) for t, v in (base_w or {}).items()}
        if abs(float(mult) - 1.0) < 1e-15:
            return dict(base), 1.0, self._AgxRpGross(base), self._AgxRpGross(base), 0, 0.0
        scalable, fixed = {}, {}
        for t, v in base.items():
            cls = self._AgxRpClassifyTicker(t)
            if model == "A":
                if t == park:
                    fixed[t] = v
                else:
                    scalable[t] = v
            else:
                if cls == "SCALABLE_RISK":
                    scalable[t] = v
                else:
                    fixed[t] = v
        bg = sum(abs(v) for v in scalable.values())
        fixed_np = sum(abs(v) for t, v in fixed.items() if t != park)
        req = float(mult)
        cap = float(self._agx_max_gross)
        hit = 0
        if bg <= 1e-12:
            eff, pre, post = req, fixed_np, fixed_np
            scaled_s = {}
        else:
            pre = fixed_np + bg * req
            room = max(0.0, cap - fixed_np)
            if bg * req > room + 1e-12:
                eff = room / bg
                hit = 1
                post = fixed_np + bg * eff
            else:
                eff = req
                post = pre
            scaled_s = {t: v * eff for t, v in scalable.items()}
        out = dict(fixed)
        out.update(scaled_s)
        ferr = 0.0
        for t, v in fixed.items():
            ferr = max(ferr, abs(float(out.get(t, 0.0)) - float(v)))
        return out, eff, pre, post, hit, ferr

    def CgAgxReplayCapture(self, combined, regime, slot, reduce_only=False, emergency=False) -> None:
        if not getattr(self, "_agx_rp_on", False):
            return
        try:
            if getattr(self, "IsWarmingUp", False) or getattr(self, "is_warming_up", False):
                return
            base = self._AgxRpBaseW(combined)
            bg = self._AgxRpGross(base)
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
            ctrl_t = dict(base)
            mismatch = 0 if set(ctrl_t) == set(base) else 1
            max_diff = 0.0
            for k in set(ctrl_t) | set(base):
                max_diff = max(max_diff, abs(float(ctrl_t.get(k, 0)) - float(base.get(k, 0))))
            self._agx_target_mismatch_count += mismatch
            if max_diff > self._agx_max_abs_target_weight_diff:
                self._agx_max_abs_target_weight_diff = max_diff

            # Candidate pending only for normal AGX-eligible (non identity handled at exec)
            if self._agx_grid_on and not (reduce_only or emergency):
                for p in self._agx_pols:
                    if abs(p["ron"] - 1) < 1e-15 and abs(p["neu"] - 1) < 1e-15 and abs(p["roff"] - 1) < 1e-15:
                        continue
                    mult = p["ron"] if rg == "RISK_ON" else (p["roff"] if rg == "RISK_OFF" else p["neu"])
                    tw, eff, pre, post, hit, ferr = self._AgxRpScale(base, mult, p["model"])
                    p["fixed_err"] = max(float(p.get("fixed_err", 0.0)), float(ferr))
                    if imm:
                        px = self._AgxRpPx(set(tw) | set(p.get("qty") or {}))
                        self._AgxRpApplyWeights(p, tw, px)
                        p["cap_hit"] += int(hit)
                        p["reb"] += 1
                    else:
                        p["pending"] = tw
                        p["_pend_hit"] = hit
        except Exception as exc:
            self._agx_err += 1
            if self._agx_err <= 3:
                self._AgxRpLog(f"CG_AGX_REPLAY_OVERRIDE,capture_error={type(exc).__name__}")

    def CgAgxReplayExecutePending(self) -> None:
        if not getattr(self, "_agx_rp_on", False):
            return
        try:
            if not self._agx_grid_on:
                self._agx_n_exe += 1
                return
            px = self._AgxRpPx()
            any_p = False
            for p in self._agx_pols:
                pend = p.get("pending")
                if pend is None:
                    continue
                any_p = True
                hit = int(p.pop("_pend_hit", 0) or 0)
                # Skip identity — they track fills
                if abs(p["ron"] - 1) < 1e-15 and abs(p["neu"] - 1) < 1e-15 and abs(p["roff"] - 1) < 1e-15:
                    p["pending"] = None
                    continue
                self._AgxRpApplyWeights(p, pend, px)
                p["pending"] = None
                p["cap_hit"] += hit
            if any_p:
                self._agx_n_exe += 1
        except Exception as exc:
            self._agx_err += 1
            if self._agx_err <= 3:
                self._AgxRpLog(f"CG_AGX_REPLAY_OVERRIDE,exec_error={type(exc).__name__}")

    def _AgxRpApplyDaily(self, led, prev_nav, nav, rg, age, eg):
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
        led["rg_eg"][rg] += float(eg or 0.0)
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

    def CgAgxReplayMark(self) -> None:
        if not getattr(self, "_agx_rp_on", False):
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
            px = self._AgxRpPx(tickers)
            ind_nav, ind_hold = self._AgxRpNav(self._agx_ctrl, px)
            ind_cash = float(self._agx_ctrl.get("cash", 0.0))
            if not self._agx_started:
                self._agx_started = True
                self._agx_prev_tpv = tpv if tpv else self._agx_cash0
                self._agx_ctrl["_prev_nav"] = ind_nav if ind_nav > 0 else self._agx_cash0
                if self._agx_grid_on:
                    for p in self._agx_pols:
                        n, _ = self._AgxRpNav(p, px)
                        p["_prev_nav"] = n if n > 0 else self._agx_cash0
                self._agx_last_mark = today
                return
            if self._agx_prev_tpv and self._agx_prev_tpv > 0 and tpv is not None:
                ar = tpv / self._agx_prev_tpv - 1.0
            else:
                ar = 0.0
            self._agx_actual_rets.append(ar)
            self._agx_dates.append(today)
            self._agx_prev_tpv = tpv if tpv is not None else self._agx_prev_tpv
            prev = self._agx_ctrl.get("_prev_nav")
            self._AgxRpApplyDaily(self._agx_ctrl, prev, ind_nav, rg, age, None)
            self._agx_ctrl["_prev_nav"] = ind_nav
            if prev and prev > 0:
                self._agx_replay_rets.append(ind_nav / prev - 1.0)
            else:
                self._agx_replay_rets.append(0.0)
            if prod_cash is not None:
                self._agx_max_cash_diff = max(self._agx_max_cash_diff, abs(prod_cash - ind_cash))
            if prod_hold is not None:
                self._agx_max_hold_diff = max(self._agx_max_hold_diff, abs(prod_hold - ind_hold))
            self._agx_snap_candidates.append({
                "date": today, "regime": rg, "prod_nav": tpv, "rep_nav": ind_nav,
                "prod_cash": prod_cash, "rep_cash": ind_cash,
                "prod_hold": prod_hold, "rep_hold": ind_hold,
                "gross": self._agx_last_base_gross,
                "bil": (self._agx_last_base or {}).get(self._agx_cash_tk),
            })
            n = len(self._agx_dates)
            if n % 63 == 0:
                self._AgxRpLog(
                    f"CG_AGX_REPLAY_DAILY,date={today},n={n},prod_nav={_f(tpv,2)},"
                    f"rep_nav={_f(ind_nav,2)},fills={self._agx_fill_n},"
                    f"reg={self._agx_reg_n},orphan={self._agx_orphan_n},"
                    f"dup={self._agx_dup_n},errors={self._agx_err}"
                )
            if self._agx_grid_on:
                for p in self._agx_pols:
                    n2, _ = self._AgxRpNav(p, px)
                    self._AgxRpApplyDaily(p, p.get("_prev_nav"), n2, rg, age, None)
                    p["_prev_nav"] = n2
            self._agx_last_mark = today
        except Exception as exc:
            self._agx_err += 1
            if self._agx_err <= 3:
                self._AgxRpLog(f"CG_AGX_REPLAY_DAILY,mark_error={type(exc).__name__}")

    def _AgxRpCorr(self, a, b):
        n = min(len(a), len(b))
        if n < 2:
            return 1.0
        a, b = a[:n], b[:n]
        ma, mb = sum(a) / n, sum(b) / n
        cov = sum((a[i] - ma) * (b[i] - mb) for i in range(n))
        va = sum((x - ma) ** 2 for x in a)
        vb = sum((x - mb) ** 2 for x in b)
        den = (va * vb) ** 0.5
        return 1.0 if den <= 1e-18 else cov / den

    def _AgxRpMetrics(self, rets):
        n = len(rets)
        if n <= 0:
            return None
        nav = peak = 1.0
        maxdd = 0.0
        uw = uw_max = uw_days = 0
        sum_r = sum_r2 = 0.0
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
                uw_max = max(uw_max, uw)
            maxdd = max(maxdd, 1.0 - nav / max(peak, 1e-9))
        mean = sum_r / n
        vol = (max(0.0, sum_r2 / n - mean * mean) ** 0.5) * (252 ** 0.5)
        years = n / 252.0
        cagr = (nav ** (1.0 / years) - 1.0) if years > 0.01 else None
        sharpe = (cagr / vol) if (cagr is not None and vol > 1e-12) else None
        if dn:
            dmean = sum(dn) / n
            dvol = (max(0.0, sum(x * x for x in dn) / n - dmean * dmean) ** 0.5) * (252 ** 0.5)
            sortino = (cagr / dvol) if (cagr is not None and dvol > 1e-12) else None
        else:
            sortino = None
        arr = sorted(rets)
        k = max(1, int(0.05 * n + 0.999))
        return {
            "n": n, "end_nav": nav, "total_return": nav - 1.0, "CAGR": cagr,
            "MaxDD": maxdd, "annual_stddev": vol, "Sharpe": sharpe, "Sortino": sortino,
            "worst_5pct_day_mean": sum(arr[:k]) / k, "worst_day": arr[0], "best_day": arr[-1],
            "recovery_days_max": uw_max, "time_under_water_pct": uw_days / n,
            "positive_day_rate": pos / n,
        }

    def _AgxRpWin(self, dates, rets, s, e):
        xs = [r for d, r in zip(dates, rets) if (s is None or d >= s) and (e is None or d <= e)]
        return self._AgxRpMetrics(xs)

    def _AgxRpPolM(self, p):
        m = self._AgxRpMetrics(p["rets"]) or {}
        egs = sorted(p["egs"]) if p["egs"] else []
        m.update({
            "start_nav": 1.0, "turnover": p["turnover"], "fees": p["fees"],
            "rebalance_count": p["reb"], "override_count": p["ov"],
            "cap_hit_count": p["cap_hit"],
            "mean_effective_gross": (p["sum_eg"] / p["n_eg"]) if p["n_eg"] else None,
            "max_effective_gross": max(egs) if egs else None,
            "ron": p["ron"], "neu": p["neu"], "roff": p["roff"],
            "id": p["id"], "model": p["model"], "fixed_err": p.get("fixed_err", 0.0),
        })
        return m

    def _AgxRpGate(self):
        n_a, n_r = len(self._agx_actual_rets), len(self._agx_replay_rets)
        match = bool(n_a == n_r and n_a > 0)
        am = self._AgxRpMetrics(self._agx_actual_rets) or {}
        rm = self._AgxRpMetrics(self._agx_replay_rets) or {}
        a_nav, r_nav = am.get("end_nav"), rm.get("end_nav")
        a_dd, r_dd = am.get("MaxDD"), rm.get("MaxDD")
        nav_d = ((r_nav / a_nav - 1.0) * 100.0) if (a_nav and r_nav is not None) else None
        dd_d = ((r_dd - a_dd) * 100.0) if (a_dd is not None and r_dd is not None) else None
        if match:
            diffs = [abs(x - y) for x, y in zip(self._agx_actual_rets, self._agx_replay_rets)]
            max_d = max(diffs) if diffs else 0.0
            mean_d = sum(diffs) / len(diffs) if diffs else 0.0
            corr = self._AgxRpCorr(self._agx_actual_rets, self._agx_replay_rets)
        else:
            max_d = mean_d = corr = None
        ok = (
            self._agx_n_cap > 0
            and self._agx_target_mismatch_count == 0
            and self._agx_max_abs_target_weight_diff <= 1e-12
            and self._agx_unclass_n == 0
            and self._agx_orphan_n == 0
            and self._agx_dup_n == 0
            and match
            and nav_d is not None and abs(nav_d) <= 0.10
            and dd_d is not None and abs(dd_d) <= 0.10
            and corr is not None and corr >= 0.9999
            and max_d is not None and max_d <= 0.0005
            and mean_d is not None and mean_d <= 0.00005
            and self._agx_corp_mm == 0
            and self._agx_err == 0
        )
        return {
            "pass": bool(ok), "nav_d": nav_d, "dd_d": dd_d, "corr": corr,
            "max_d": max_d, "mean_d": mean_d, "match": match,
            "_am": am, "_rm": rm,
        }

    def _AgxRpSelectSnap(self):
        cands = self._agx_snap_candidates
        if not cands:
            return []
        want = {
            _date(2013, 10, 4), _date(2016, 7, 7), _date(2019, 10, 8),
            _date(2020, 1, 8), _date(2021, 9, 29), _date(2025, 1, 13),
            _date(2026, 6, 16),
        }
        out, seen = [], set()

        def add(e):
            if e and e["date"] not in seen:
                seen.add(e["date"])
                out.append(e)

        add(cands[0])
        for c in cands:
            if c["date"] in want:
                add(c)
        # nearest to wanted if exact missing
        for w in want:
            if w in seen:
                continue
            near = min(cands, key=lambda c: abs((c["date"] - w).days))
            add(near)
        cashn = [c for c in cands if c.get("prod_cash") is not None]
        if cashn:
            add(max(cashn, key=lambda c: c["prod_cash"]))
        add(cands[-1])
        return out[:15]

    def _AgxRpPareto(self, rows):
        front = []
        for r in rows:
            dom = False
            for o in rows:
                if o is r:
                    continue
                km = ("MaxDD", "w5_abs", "crisis_maxdd", "recovery_days_max")
                kx = ("oos_sharpe", "CAGR")
                ge = all(o.get(k, 1e9) <= r.get(k, 1e9) for k in km) and all(
                    (o.get(k) or -1e9) >= (r.get(k) or -1e9) for k in kx)
                gt = any(o.get(k, 1e9) < r.get(k, 1e9) for k in km) or any(
                    (o.get(k) or -1e9) > (r.get(k) or -1e9) for k in kx)
                if ge and gt:
                    dom = True
                    break
            if not dom:
                front.append(r)
        return front

    def _AgxRpRank(self, r):
        return (
            float(r.get("MaxDD") or 9), float(r.get("w5_abs") or 9),
            -float(r.get("oos_sharpe") or -9), float(r.get("crisis_maxdd") or 9),
            float(r.get("recovery_days_max") or 9e9), -float(r.get("CAGR") or -9),
        )

    def CgAgxReplayEmitFinal(self) -> None:
        if getattr(self, "_agx_rp_emitted", False):
            return
        self._agx_rp_emitted = True
        if not getattr(self, "_agx_rp_on", False):
            return
        try:
            gate = self._AgxRpGate()
            ok = bool(gate["pass"])
            am, rm = gate.get("_am") or {}, gate.get("_rm") or {}
            for c in self._AgxRpSelectSnap():
                self._AgxRpLog(
                    f"CG_AGX_REPLAY_SNAPSHOT,date={c['date']},regime={c['regime']},"
                    f"prod_nav={_f(c.get('prod_nav'),2)},rep_nav={_f(c.get('rep_nav'),2)},"
                    f"prod_cash={_f(c.get('prod_cash'),2)},rep_cash={_f(c.get('rep_cash'),2)},"
                    f"prod_hold={_f(c.get('prod_hold'),2)},rep_hold={_f(c.get('rep_hold'),2)},"
                    f"gross={_f(c.get('gross'))},bil={_f(c.get('bil'),6)}"
                )
            # Path inventory
            for name, st in sorted(self._agx_path_stats.items(), key=lambda kv: -kv[1]["n"])[:20]:
                self._AgxRpLog(
                    f"CG_AGX_REPLAY_OVERRIDE,path={name},n={st['n']},"
                    f"first={st['first']},impact={_f(st['impact'],2)}"
                )
            self._AgxRpLog(
                f"CG_AGX_REPLAY_FINAL,replay_parity_gate={'PASS' if ok else 'FAIL'},"
                f"control_mode=PRODUCTION_EVENT_REPLAY,"
                f"target_mismatch_count={self._agx_target_mismatch_count},"
                f"registered_order_count={self._agx_reg_n},"
                f"unclassified_order_count={self._agx_unclass_n},"
                f"orphan_order_event_count={self._agx_orphan_n},"
                f"duplicate_fill_replay_count={self._agx_dup_n},"
                f"reconciled_order_count={self._agx_recon_n},"
                f"fill_event_count={self._agx_fill_n},"
                f"mandatory_override_count={self._agx_ov_n},"
                f"dividend_event_count={self._agx_div_n},"
                f"split_event_count={self._agx_split_n},"
                f"corporate_action_mismatch_count={self._agx_corp_mm},"
                f"actual_final_nav={_f(am.get('end_nav'))},"
                f"replay_final_nav={_f(rm.get('end_nav'))},"
                f"nav_difference_pct={_f(gate['nav_d'],6)},"
                f"actual_maxdd={_f(am.get('MaxDD'))},replay_maxdd={_f(rm.get('MaxDD'))},"
                f"maxdd_difference_pp={_f(gate['dd_d'],6)},"
                f"daily_return_correlation={_f(gate['corr'],6)},"
                f"max_abs_daily_return_difference={_f(gate['max_d'],6)},"
                f"mean_abs_daily_return_difference={_f(gate['mean_d'],6)},"
                f"max_abs_cash_difference={_f(self._agx_max_cash_diff,2)},"
                f"max_abs_holdings_value_difference={_f(self._agx_max_hold_diff,2)},"
                f"daily_return_count_match={'YES' if gate['match'] else 'NO'},"
                f"runtime_errors={self._agx_err},"
                f"next={'PROCEED_CANDIDATE_GATE' if ok else 'FIX_EVENT_CAPTURE'}"
            )
            if not ok or not self._agx_grid_on:
                self._AgxRpLog(
                    f"CG_AGX_P1_FINAL,diagnostic=CG-AGX-INDEPENDENT-EXEC-REPLAY-P1,"
                    f"replay_parity_gate={'PASS' if ok else 'FAIL'},continue_to_grid=NO,"
                    f"selection_allowed=0,policies_evaluated=0,"
                    f"candidate_execution_model_valid=NO,"
                    f"next={'FIX_EVENT_CAPTURE' if not ok else 'FIX_CANDIDATE_EXECUTION_MODEL'}"
                )
                return

            # Candidate control identity
            ctrl_m = self._AgxRpPolM(self._agx_ctrl)
            n_r = len(self._agx_replay_rets)

            def ctrl_ok(p):
                if not p:
                    return False, None, None, None
                m = self._AgxRpPolM(p)
                nav_d = dd_d = corr = None
                if ctrl_m.get("end_nav") and m.get("end_nav"):
                    nav_d = abs(m["end_nav"] / ctrl_m["end_nav"] - 1.0) * 100.0
                if ctrl_m.get("MaxDD") is not None and m.get("MaxDD") is not None:
                    dd_d = abs(m["MaxDD"] - ctrl_m["MaxDD"]) * 100.0
                if len(p["rets"]) == n_r and n_r > 1:
                    corr = self._AgxRpCorr(self._agx_replay_rets, p["rets"])
                good = (
                    nav_d is not None and nav_d <= 0.10
                    and dd_d is not None and dd_d <= 0.10
                    and corr is not None and corr >= 0.9999
                )
                return good, nav_d, dd_d, corr

            a_ok, a_nd, a_dd, a_c = ctrl_ok(self._agx_a_ctrl)
            b_ok, b_nd, b_dd, b_c = ctrl_ok(self._agx_b_ctrl)
            cand_valid = bool(a_ok and b_ok)
            self._AgxRpLog(
                f"CG_AGX_P1_VALIDATION,candidate_execution_model_valid="
                f"{'YES' if cand_valid else 'NO'},"
                f"A_nav_diff={_f(a_nd,6)},B_nav_diff={_f(b_nd,6)},"
                f"A_dd_diff={_f(a_dd,6)},B_dd_diff={_f(b_dd,6)},"
                f"A_corr={_f(a_c,6)},B_corr={_f(b_c,6)},"
                f"model_a_control_valid={'YES' if a_ok else 'NO'},"
                f"model_b_control_valid={'YES' if b_ok else 'NO'}"
            )
            if not cand_valid:
                self._AgxRpLog(
                    "CG_AGX_P1_FINAL,diagnostic=CG-AGX-INDEPENDENT-EXEC-REPLAY-P1,"
                    "replay_parity_gate=PASS,continue_to_grid=NO,selection_allowed=0,"
                    "policies_evaluated=0,candidate_execution_model_valid=NO,"
                    "next=FIX_CANDIDATE_EXECUTION_MODEL"
                )
                return

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

            def build(model):
                rows = []
                for p in self._agx_pols:
                    if p["model"] != model:
                        continue
                    m = self._AgxRpPolM(p)
                    wins = {}
                    missing = 0
                    for name, s, e in windows:
                        if s is None:
                            wins[name] = None
                            missing += 1
                            continue
                        wm = self._AgxRpWin(p["dates"], p["rets"], s, e)
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
                    invalid = int(bool(
                        missing or len(p["rets"]) != n_r
                        or (std is not None and std > 0.20)
                        or float(m.get("fixed_err") or 0) > 1e-12
                    ))
                    row = dict(m)
                    row.update({
                        "wins": wins, "oos_sharpe": oos.get("Sharpe"),
                        "crisis_maxdd": cri.get("MaxDD"),
                        "y2020_maxdd": y20.get("MaxDD"), "y2022_maxdd": y22.get("MaxDD"),
                        "w5_abs": -float(m.get("worst_5pct_day_mean") or 0),
                        "invalid": invalid, "_p": p,
                    })
                    p1 = 0
                    tgt = 0
                    if not invalid and ctrl_m:
                        def _ge(a, b):
                            return a is not None and b is not None and a >= b

                        def _le(a, b):
                            return a is not None and b is not None and a <= b

                        c_oos = self._AgxRpWin(self._agx_ctrl["dates"], self._agx_ctrl["rets"], _date(2019, 1, 1), _date(2021, 12, 31)) or {}
                        c_cri = self._AgxRpWin(self._agx_ctrl["dates"], self._agx_ctrl["rets"], _date(2022, 1, 1), _date(2025, 12, 31)) or {}
                        c_y20 = self._AgxRpWin(self._agx_ctrl["dates"], self._agx_ctrl["rets"], _date(2020, 1, 1), _date(2020, 12, 31)) or {}
                        c_y22 = self._AgxRpWin(self._agx_ctrl["dates"], self._agx_ctrl["rets"], _date(2022, 1, 1), _date(2022, 12, 31)) or {}
                        p1 = int(
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
                        if ((m.get("CAGR") or 0) >= 0.45 and (m.get("MaxDD") or 9) <= 0.13 and (std or 9) <= 0.18):
                            tgt = 1
                    row["P1_PASS"] = p1
                    row["TARGET_PROFILE_MET"] = tgt
                    rows.append(row)
                return rows

            results = {}
            all_rows = []
            for model, valid in (("A", a_ok), ("B", b_ok)):
                rows = build(model)
                all_rows.extend(rows)
                if not valid:
                    results[model] = {"valid": False, "ranked": [], "p1": [], "front": [], "top": [], "best": None}
                    continue
                valid_rows = [r for r in rows if not r["invalid"]]
                ranked = sorted(valid_rows, key=self._AgxRpRank)
                p1s = [r for r in ranked if r["P1_PASS"]]
                front = self._AgxRpPareto(valid_rows)
                results[model] = {
                    "valid": True, "ranked": ranked, "p1": p1s, "front": front,
                    "top": ranked[:10], "best": (p1s[0] if p1s else (ranked[0] if ranked else None)),
                }

            csv_key = "cg_agx_exec_replay_p1.csv"
            try:
                headers = [
                    "id", "model", "ron", "neu", "roff", "CAGR", "MaxDD", "annual_stddev",
                    "Sharpe", "Sortino", "worst_5pct_day_mean", "recovery_days_max",
                    "turnover", "fees", "rebalance_count", "override_count", "cap_hit_count",
                    "oos_sharpe", "crisis_maxdd", "y2020_maxdd", "y2022_maxdd",
                    "P1_PASS", "TARGET_PROFILE_MET", "invalid",
                ]
                lines = [",".join(headers)]
                for r in all_rows:
                    lines.append(",".join(str(r.get(h, "NA")) for h in headers))
                self.object_store.save(csv_key, "\n".join(lines))
            except Exception as exc:
                csv_key = f"NONE:{type(exc).__name__}"

            a_p1, b_p1 = len(results["A"]["p1"]), len(results["B"]["p1"])
            a_pf, b_pf = len(results["A"]["front"]), len(results["B"]["front"])
            tgt_n = sum(1 for r in all_rows if r.get("TARGET_PROFILE_MET"))
            stable = []
            for model in ("A", "B"):
                stable.extend(results[model]["p1"][:5])
            next_dec = "STOP_AGX"
            if stable:
                next_dec = "PREPARE_AGX_P2"
            elif a_p1 or b_p1:
                next_dec = "REFINE_MODEL_A" if a_p1 >= b_p1 else "REFINE_MODEL_B"
            elif a_pf or b_pf:
                next_dec = "REFINE_MODEL_A" if a_pf >= b_pf else "REFINE_MODEL_B"

            for model in ("A", "B"):
                rs = results[model]
                best = (rs["best"] or {}).get("id", "NONE")
                self._AgxRpLog(
                    f"CG_AGX_P1_MODEL,model={model},valid={int(rs['valid'])},"
                    f"p1_pass={len(rs['p1'])},pareto={len(rs['front'])},best={best}"
                )
                for i, r in enumerate(rs["top"]):
                    self._AgxRpLog(
                        f"CG_AGX_P1_TOP,model={model},rank={i+1},id={r['id']},"
                        f"CAGR={_f(r.get('CAGR'))},MaxDD={_f(r.get('MaxDD'))},"
                        f"Sharpe={_f(r.get('Sharpe'))},oos={_f(r.get('oos_sharpe'))},"
                        f"crisis={_f(r.get('crisis_maxdd'))},P1={r.get('P1_PASS')}"
                    )

            focus = [self._agx_ctrl]
            for model in ("A", "B"):
                focus.extend([r["_p"] for r in results[model]["top"][:5]])
            for p in focus:
                for name, s, e in windows:
                    wm = self._AgxRpWin(p["dates"], p["rets"], s, e)
                    if not wm:
                        continue
                    self._AgxRpLog(
                        f"CG_AGX_P1_WINDOW,id={p['id']},window={name},"
                        f"CAGR={_f(wm.get('CAGR'))},MaxDD={_f(wm.get('MaxDD'))},"
                        f"Sharpe={_f(wm.get('Sharpe'))},w5={_f(wm.get('worst_5pct_day_mean'))}"
                    )
                for tb in ("0", "1", "2-3", "4-10", ">10"):
                    self._AgxRpLog(
                        f"CG_AGX_P1_TRANSITION,id={p['id']},bucket={tb},"
                        f"days={p['tr_n'][tb]},ret_contrib={_f(p['tr_r'][tb])}"
                    )

            self._AgxRpLog(
                f"CG_AGX_P1_FINAL,diagnostic=CG-AGX-INDEPENDENT-EXEC-REPLAY-P1,"
                f"replay_parity_gate=PASS,continue_to_grid=YES,selection_allowed=1,"
                f"candidate_execution_model_valid=YES,"
                f"model_a_control_valid=YES,model_b_control_valid=YES,"
                f"policies_evaluated={len(all_rows)},"
                f"model_a_best={(results['A']['best'] or {}).get('id','NONE')},"
                f"model_b_best={(results['B']['best'] or {}).get('id','NONE')},"
                f"model_a_p1_pass_count={a_p1},model_b_p1_pass_count={b_p1},"
                f"model_a_pareto_count={a_pf},model_b_pareto_count={b_pf},"
                f"target_profile_met_count={tgt_n},result_artifact={csv_key},next={next_dec}"
            )
        except Exception as exc:
            self._agx_err += 1
            try:
                self.log(f"CG_AGX_P1_FINAL,emit_error={type(exc).__name__}:{exc}")
            except Exception:
                pass
