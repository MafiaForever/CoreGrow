# region imports
from AlgorithmImports import *
from datetime import date as _date
# endregion
# PRODUCTION_EVENT_REPLAY + cash-parity P2 (warmup/div-norm + 400 grid)

_RON = (0.75, 0.85, 0.95, 1.00, 1.05, 1.10, 1.20, 1.30)
_NEU = (0.25, 0.50, 0.75, 0.90, 1.00)
_ROFF = (0.00, 0.25, 0.50, 0.75, 1.00)
_DFT_DEF = frozenset(("TIP", "BND", "GLD", "GLDM", "BIL", "SGOV", "USFR", "SH"))
_LOG_BUDGET = 28000
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
        self.cg_agx_cash_parity_enable = _bool("cg_agx_cash_parity_enable", "0")
        self.cg_agx_shadow_emit_events = _bool("cg_agx_shadow_emit_events", "0")
        req_cap = _float("cg_agx_shadow_max_gross", 2.00)
        prod_cap = float(getattr(self, "max_total_exposure", 1.90) or 1.90)
        self._agx_prod_gross_cap = prod_cap
        self._agx_max_gross = min(float(req_cap), float(prod_cap))
        self._agx_cost_bps = _float("cg_agx_shadow_cost_bps", 0.0)
        self._agx_rp_on = bool(
            self.cg_agx_shadow_diag_enable and self.cg_agx_exec_replay_enable
        )
        self._agx_cash_p2 = bool(self._agx_rp_on and self.cg_agx_cash_parity_enable)
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
        try:
            sd = getattr(self, "start_date", None) or getattr(self, "StartDate", None)
            self._agx_start_date = sd.date() if hasattr(sd, "date") else sd
        except Exception:
            self._agx_start_date = _date(2012, 1, 1)

        self._agx_log_used = 0
        self._agx_err = 0
        self._agx_emitted = False
        self._agx_rp_emitted = False
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
        self._agx_max_cash_diff = 0.0
        self._agx_max_hold_diff = 0.0
        self._agx_corp_mm = 0
        self._agx_div_n = 0
        self._agx_div_applied = 0
        self._agx_div_ign_adj = 0
        self._agx_div_ign_wu = 0
        self._agx_div_dup = 0
        self._agx_div_seen_keys = set()
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
        self._agx_fill_notional_mm = 0
        self._agx_fee_mm = 0
        self._agx_wu_seen = 0
        self._agx_wu_ign = 0
        self._agx_first_div = None
        self._agx_cash_cat = {
            "FILL_NOTIONAL": 0.0, "ORDER_FEE": 0.0, "DIVIDEND_CASH": 0.0,
            "SPLIT_CASH_IN_LIEU": 0.0, "EXTERNAL_DEPOSIT": 0.0, "EXTERNAL_WITHDRAWAL": 0.0,
            "INTEREST_OR_BORROW": 0.0, "FX_CONVERSION": 0.0, "DELISTING_CASH": 0.0,
            "OTHER_IDENTIFIED": 0.0, "UNKNOWN": 0.0,
        }
        self._agx_seen_fill = set()
        self._agx_order_meta = {}
        self._agx_norm_cache = {}
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
        for pref in ("CG_AGX_REPLAY_", "CG_AGX_P1_", "CG_AGX_CASH_", "CG_AGX_P2_", "[INIT] CG_AGX"):
            if pref not in lp:
                lp.append(pref)
        self.log_only_prefixes = lp

        if self._agx_cash_p2:
            self.log(
                f"CG_AGX_CASH_P2_INIT,enable=1,control_mode=PRODUCTION_EVENT_REPLAY,"
                f"grid_enable={int(self._agx_grid_on)},policies={len(self._agx_pols)},"
                f"parking_etf={self._agx_cash_tk},max_gross={_f(self._agx_max_gross)},"
                f"start={self._agx_start_date},manual_div=normalization_safe,"
                f"candidate_quarantined=1"
            )
        else:
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

    def _AgxRpLedgerActive(self):
        if getattr(self, "IsWarmingUp", False) or getattr(self, "is_warming_up", False):
            return False
        sd = getattr(self, "_agx_start_date", None)
        if sd is not None:
            try:
                if self.time.date() < sd:
                    return False
            except Exception:
                pass
        return True

    def _AgxRpManualDiv(self, sym):
        t = _tk(sym) if not isinstance(sym, str) else sym
        if t in self._agx_norm_cache:
            return self._agx_norm_cache[t]
        need = False
        try:
            sec = None
            try:
                sec = self.securities[sym]
            except Exception:
                for k in self.securities.keys():
                    if _tk(k) == t:
                        sec = self.securities[k]
                        break
            mode = getattr(sec, "data_normalization_mode", None) if sec else None
            if mode is None and sec is not None:
                mode = getattr(sec, "DataNormalizationMode", None)
            if mode is not None:
                try:
                    need = mode in (DataNormalizationMode.Raw, DataNormalizationMode.SplitAdjusted)
                except Exception:
                    need = ("Raw" in str(mode)) or ("SplitAdjusted" in str(mode))
        except Exception:
            need = False
        self._agx_norm_cache[t] = need
        return need

    def _AgxRpNoteCashDiverg(self, event, symbol, prod_delta, replay_delta, qty=None):
        if self._agx_first_div is not None or not self._agx_cash_p2:
            return
        try:
            pc = float(self.portfolio.cash)
            rc = float(self._agx_ctrl.get("cash", 0.0))
            if abs(rc - pc) <= 0.01:
                return
            self._agx_first_div = {
                "time": str(self.time), "symbol": symbol, "event": event,
                "production_delta": prod_delta, "replay_delta": replay_delta,
                "difference": rc - pc, "quantity": qty,
            }
            mode = "manual_div" if self._AgxRpManualDiv(symbol) else "adjusted_embedded"
            self._AgxRpLog(
                f"CG_AGX_CASH_FIRST_DIVERGENCE,time={self.time},symbol={symbol},"
                f"event={event},production_delta={_f(prod_delta,6)},"
                f"replay_delta={_f(replay_delta,6)},difference={_f(rc-pc,6)},"
                f"normalization_mode={mode},"
                f"is_warming_up={int(bool(getattr(self,'is_warming_up',False)))},"
                f"quantity={_f(qty,4)}"
            )
        except Exception:
            pass

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
            if not self._AgxRpLedgerActive():
                self._agx_wu_seen += 1
                self._agx_wu_ign += 1
                return
            oid = getattr(order_event, "order_id", None)
            if oid is None:
                oid = getattr(order_event, "OrderId", None)
            status = getattr(order_event, "status", None)
            st = str(status)
            t = _tk(getattr(order_event, "symbol", None))
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
                    self._agx_recon_n += 1
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
            notional_delta = -float(signed) * float(px)
            fee_delta = -float(fee)
            self._agx_cash_cat["FILL_NOTIONAL"] += notional_delta
            self._agx_cash_cat["ORDER_FEE"] += fee_delta
            self._AgxRpApplyFill(self._agx_ctrl, t, signed, px, fee)
            self._AgxRpNoteCashDiverg("FILL", t, None, notional_delta + fee_delta, signed)

            if not self._agx_grid_on:
                return
            ecl = meta.get("class")
            for p in self._agx_pols:
                is_identity = (
                    abs(p["ron"] - 1.0) < 1e-15
                    and abs(p["neu"] - 1.0) < 1e-15
                    and abs(p["roff"] - 1.0) < 1e-15
                )
                if is_identity:
                    self._AgxRpApplyFill(p, t, signed, px, fee)
                    continue
                if ecl == "NORMAL_AGX_ELIGIBLE":
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
                        self._AgxRpApplyFill(p, t, signed, px, fee)
                        self._agx_ov_n += 1
                else:
                    self._AgxRpApplyFill(p, t, signed, px, fee)
        except Exception as exc:
            self._agx_err += 1
            if self._agx_err <= 3 and not getattr(self, "_agx_cash_p2", False):
                self._AgxRpLog(f"CG_AGX_REPLAY_FILL,error={type(exc).__name__}")

    def CgAgxReplayOnData(self, data) -> None:
        if not getattr(self, "_agx_rp_on", False) or data is None:
            return
        try:
            active = self._AgxRpLedgerActive()
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
                        dkey = f"{t}|{self.time.date()}|{dist:.8f}"
                        if dkey in self._agx_div_seen_keys:
                            self._agx_div_dup += 1
                            continue
                        self._agx_div_seen_keys.add(dkey)
                        if not active:
                            self._agx_wu_seen += 1
                            self._agx_wu_ign += 1
                            self._agx_div_ign_wu += 1
                            continue
                        if not self._AgxRpManualDiv(sym):
                            self._agx_div_ign_adj += 1
                            continue
                        q = float((self._agx_ctrl.get("qty") or {}).get(t, 0.0))
                        if abs(q) < 1e-12:
                            continue
                        credit = q * dist
                        self._agx_div_applied += 1
                        self._agx_cash_cat["DIVIDEND_CASH"] += credit
                        for led in [self._agx_ctrl] + (self._agx_pols if self._agx_grid_on else []):
                            lq = float((led.get("qty") or {}).get(t, 0.0))
                            if abs(lq) > 0:
                                led["cash"] = float(led.get("cash", 0.0)) + lq * dist
                        self._AgxRpPath("dividend", abs(dist))
                        self._AgxRpNoteCashDiverg("DIVIDEND", t, None, credit, q)
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
                        if not active:
                            self._agx_wu_seen += 1
                            self._agx_wu_ign += 1
                            continue
                        self._agx_split_n += 1
                        for led in [self._agx_ctrl] + (self._agx_pols if self._agx_grid_on else []):
                            q = float((led.get("qty") or {}).get(t, 0.0))
                            if abs(q) > 0:
                                led["qty"][t] = q / factor
                        self._AgxRpPath("split", abs(factor))
                    except Exception:
                        self._agx_corp_mm += 1
        except Exception as exc:
            self._agx_err += 1
            if self._agx_err <= 3 and not getattr(self, "_agx_cash_p2", False):
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
                self._AgxRpNoteCashDiverg("MARK", self._agx_cash_tk, None, None)
            if prod_hold is not None:
                self._agx_max_hold_diff = max(self._agx_max_hold_diff, abs(prod_hold - ind_hold))
            if self._agx_grid_on:
                for p in self._agx_pols:
                    n2, _ = self._AgxRpNav(p, px)
                    self._AgxRpApplyDaily(p, p.get("_prev_nav"), n2, rg, age, None)
                    p["_prev_nav"] = n2
            self._agx_last_mark = today
        except Exception as exc:
            self._agx_err += 1
            if self._agx_err <= 3 and not getattr(self, "_agx_cash_p2", False):
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
        try:
            prod_end = float(self.portfolio.cash)
        except Exception:
            prod_end = None
        known = float(self._agx_cash0) + sum(float(v) for k, v in self._agx_cash_cat.items() if k != "UNKNOWN")
        unknown = (prod_end - known) if prod_end is not None else None
        if unknown is not None:
            self._agx_cash_cat["UNKNOWN"] = float(unknown)
        hold_ok = self._agx_max_hold_diff <= 0.01
        cash_ok = self._agx_max_cash_diff <= 0.10
        unk_ok = unknown is not None and abs(unknown) <= 0.01
        wu_ok = self._agx_wu_seen == self._agx_wu_ign
        fill_ok = self._agx_fill_n == 1214
        ok = (
            self._agx_n_cap > 0
            and self._agx_target_mismatch_count == 0
            and self._agx_max_abs_target_weight_diff <= 1e-12
            and self._agx_unclass_n == 0
            and self._agx_orphan_n == 0
            and self._agx_dup_n == 0
            and fill_ok
            and match
            and hold_ok
            and cash_ok
            and unk_ok
            and wu_ok
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
            "unknown": unknown, "hold_ok": hold_ok, "cash_ok": cash_ok,
            "fill_ok": fill_ok, "_am": am, "_rm": rm,
        }

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

    def _AgxRpNeigh(self, r, by_id):
        out = []
        for dim, vals in (("ron", _RON), ("neu", _NEU), ("roff", _ROFF)):
            cur = float(r[dim])
            ix = list(vals).index(cur) if cur in vals else -1
            if ix < 0:
                continue
            for j in (ix - 1, ix + 1):
                if 0 <= j < len(vals):
                    kw = {"ron": r["ron"], "neu": r["neu"], "roff": r["roff"], dim: vals[j]}
                    nid = _pid(r["model"], kw["ron"], kw["neu"], kw["roff"])
                    if nid in by_id:
                        out.append(by_id[nid])
        return out

    def _AgxRpStable(self, r, by_id):
        for n in self._AgxRpNeigh(r, by_id):
            if (n.get("MaxDD") or 9) > (r.get("MaxDD") or 0) + 0.015:
                return False
            rc, nc = float(r.get("CAGR") or 0), float(n.get("CAGR") or 0)
            if rc > 0 and nc < rc * 0.85:
                return False
            if (n.get("oos_sharpe") or -9) < 0.90 * (r.get("oos_sharpe") or 0):
                return False
            if (n.get("crisis_maxdd") or 9) > (r.get("crisis_maxdd") or 0) + 0.015:
                return False
        return True

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
            cat = self._agx_cash_cat
            hold_pass = "PASS" if gate.get("hold_ok") else "FAIL"
            if not gate.get("hold_ok"):
                nxt = "FIX_HOLDINGS_REGRESSION"
            elif not ok:
                nxt = "FIX_CASH_LEDGER_AGAIN"
            else:
                nxt = "PROCEED"
            if getattr(self, "_agx_cash_p2", False):
                self._AgxRpLog(
                    f"CG_AGX_CASH_RECON_FINAL,cash_start={_f(self._agx_cash0,2)},"
                    f"fill_notional={_f(cat['FILL_NOTIONAL'],2)},fee={_f(cat['ORDER_FEE'],2)},"
                    f"div={_f(cat['DIVIDEND_CASH'],2)},split={_f(cat['SPLIT_CASH_IN_LIEU'],2)},"
                    f"external={_f(cat['EXTERNAL_DEPOSIT']+cat['EXTERNAL_WITHDRAWAL'],2)},"
                    f"interest={_f(cat['INTEREST_OR_BORROW'],2)},fx={_f(cat['FX_CONVERSION'],2)},"
                    f"delist={_f(cat['DELISTING_CASH'],2)},other={_f(cat['OTHER_IDENTIFIED'],2)},"
                    f"unknown={_f(gate.get('unknown'),6)},"
                    f"wu_seen={self._agx_wu_seen},wu_ign={self._agx_wu_ign},"
                    f"div_seen={self._agx_div_n},div_applied={self._agx_div_applied},"
                    f"div_ign_adj={self._agx_div_ign_adj},div_ign_wu={self._agx_div_ign_wu},"
                    f"div_dup={self._agx_div_dup}"
                )
                self._AgxRpLog(
                    f"CG_AGX_CASH_PARITY_FINAL,holdings_parity={hold_pass},"
                    f"cash_parity={'PASS' if ok else 'FAIL'},"
                    f"control_mode=PRODUCTION_EVENT_REPLAY,"
                    f"target_mismatch_count={self._agx_target_mismatch_count},"
                    f"registered_order_count={self._agx_reg_n},fill_event_count={self._agx_fill_n},"
                    f"unclassified_order_count={self._agx_unclass_n},"
                    f"orphan_order_event_count={self._agx_orphan_n},"
                    f"duplicate_fill_replay_count={self._agx_dup_n},"
                    f"max_abs_holdings_value_difference={_f(self._agx_max_hold_diff,2)},"
                    f"max_abs_cash_difference={_f(self._agx_max_cash_diff,2)},"
                    f"unknown_cash_total={_f(gate.get('unknown'),6)},"
                    f"actual_final_nav={_f(am.get('end_nav'))},replay_final_nav={_f(rm.get('end_nav'))},"
                    f"nav_difference_pct={_f(gate['nav_d'],6)},"
                    f"actual_maxdd={_f(am.get('MaxDD'))},replay_maxdd={_f(rm.get('MaxDD'))},"
                    f"maxdd_difference_pp={_f(gate['dd_d'],6)},"
                    f"daily_return_correlation={_f(gate['corr'],6)},"
                    f"max_abs_daily_return_difference={_f(gate['max_d'],6)},"
                    f"mean_abs_daily_return_difference={_f(gate['mean_d'],6)},"
                    f"daily_return_count_match={'YES' if gate['match'] else 'NO'},"
                    f"runtime_errors={self._agx_err},continue_to_grid={'YES' if ok and self._agx_grid_on else 'NO'}"
                )
            else:
                self._AgxRpLog(
                    f"CG_AGX_REPLAY_FINAL,replay_parity_gate={'PASS' if ok else 'FAIL'},"
                    f"fill_event_count={self._agx_fill_n},nav_difference_pct={_f(gate['nav_d'],6)},"
                    f"max_abs_cash_difference={_f(self._agx_max_cash_diff,2)},"
                    f"max_abs_holdings_value_difference={_f(self._agx_max_hold_diff,2)}"
                )
            if not ok or not self._agx_grid_on:
                if getattr(self, "_agx_cash_p2", False):
                    self._AgxRpLog(
                        f"CG_AGX_P2_GRID_FINAL,policies_evaluated=0,selection_allowed=0,next={nxt}"
                    )
                    self._AgxRpLog(
                        "CG_AGX_P2_RECOMMENDATION,apply=NO,model=CONTROL,ron=1.00,neu=1.00,"
                        "roff=1.00,class=CONTROL,neighbor_stable=NO,reason=parity_fail"
                    )
                return

            ctrl_m = self._AgxRpPolM(self._agx_ctrl)
            n_r = len(self._agx_replay_rets)

            def ctrl_ok(p):
                if not p:
                    return False
                m = self._AgxRpPolM(p)
                nav_d = dd_d = corr = max_d = None
                if ctrl_m.get("end_nav") and m.get("end_nav"):
                    nav_d = abs(m["end_nav"] / ctrl_m["end_nav"] - 1.0) * 100.0
                if ctrl_m.get("MaxDD") is not None and m.get("MaxDD") is not None:
                    dd_d = abs(m["MaxDD"] - ctrl_m["MaxDD"]) * 100.0
                if len(p["rets"]) == n_r and n_r > 1:
                    corr = self._AgxRpCorr(self._agx_replay_rets, p["rets"])
                    diffs = [abs(x - y) for x, y in zip(self._agx_replay_rets, p["rets"])]
                    max_d = max(diffs) if diffs else 0.0
                return (
                    nav_d is not None and nav_d <= 0.10
                    and dd_d is not None and dd_d <= 0.10
                    and corr is not None and corr >= 0.9999
                    and max_d is not None and max_d <= 0.0005
                )

            a_ok, b_ok = ctrl_ok(self._agx_a_ctrl), ctrl_ok(self._agx_b_ctrl)
            if not a_ok and not b_ok:
                self._AgxRpLog(
                    "CG_AGX_P2_GRID_FINAL,policies_evaluated=0,model_a_control_valid=NO,"
                    "model_b_control_valid=NO,next=FIX_CANDIDATE_CONTROL"
                )
                self._AgxRpLog(
                    "CG_AGX_P2_RECOMMENDATION,apply=NO,model=CONTROL,ron=1.00,neu=1.00,"
                    "roff=1.00,class=CONTROL,neighbor_stable=NO,reason=control_fail"
                )
                return

            today = self.time.date()
            live_s = self._agx_dates[max(0, len(self._agx_dates) - 252)] if self._agx_dates else None
            winspec = [
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
            c_oos = self._AgxRpWin(self._agx_ctrl["dates"], self._agx_ctrl["rets"], _date(2019, 1, 1), _date(2021, 12, 31)) or {}
            c_cri = self._AgxRpWin(self._agx_ctrl["dates"], self._agx_ctrl["rets"], _date(2022, 1, 1), _date(2025, 12, 31)) or {}
            c_y20 = self._AgxRpWin(self._agx_ctrl["dates"], self._agx_ctrl["rets"], _date(2020, 1, 1), _date(2020, 12, 31)) or {}
            c_y22 = self._AgxRpWin(self._agx_ctrl["dates"], self._agx_ctrl["rets"], _date(2022, 1, 1), _date(2022, 12, 31)) or {}

            def _ge(a, b):
                return a is not None and b is not None and a >= b

            def _le(a, b):
                return a is not None and b is not None and a <= b

            all_rows, by_id = [], {}
            for p in self._agx_pols:
                if (p["model"] == "A" and not a_ok) or (p["model"] == "B" and not b_ok):
                    continue
                m = self._AgxRpPolM(p)
                wins, missing = {}, 0
                for name, s, e in winspec:
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
                oos, cri = wins.get("OOS_2019_2021") or {}, wins.get("CRISIS_2022_2025") or {}
                y20, y22 = wins.get("Y2020") or {}, wins.get("Y2022") or {}
                std = m.get("annual_stddev")
                invalid = int(bool(
                    missing or len(p["rets"]) != n_r
                    or (std is not None and std > 0.20)
                    or float(m.get("fixed_err") or 0) > 1e-12
                ))
                row = dict(m)
                row.update({
                    "oos_sharpe": oos.get("Sharpe"), "crisis_maxdd": cri.get("MaxDD"),
                    "y2020_maxdd": y20.get("MaxDD"), "y2022_maxdd": y22.get("MaxDD"),
                    "w5_abs": -float(m.get("worst_5pct_day_mean") or 0),
                    "worst5": m.get("worst_5pct_day_mean"), "invalid": invalid,
                    "cap_hit_rate": (m.get("cap_hit_count") or 0) / max(1, m.get("rebalance_count") or 1),
                })
                sp = rp = 0
                if not invalid:
                    sp = int(
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
                    rp = int(
                        (std is not None and std <= 0.18)
                        and _ge(oos.get("Sharpe"), 0.90 * (c_oos.get("Sharpe") or 0))
                        and _le(cri.get("MaxDD"), (c_cri.get("MaxDD") or 0) + 0.01)
                        and _le(y20.get("MaxDD"), (c_y20.get("MaxDD") or 0) + 0.01)
                        and _le(y22.get("MaxDD"), (c_y22.get("MaxDD") or 0) + 0.01)
                    )
                row["STRICT_PASS"], row["ROBUST_OK"] = sp, rp
                all_rows.append(row)
                by_id[row["id"]] = row

            valid_rows = [r for r in all_rows if not r["invalid"]]
            front = self._AgxRpPareto([r for r in valid_rows if r["ROBUST_OK"]])
            front_ids = set(r["id"] for r in front)
            for r in all_rows:
                r["ROBUST_PARETO"] = int(r["id"] in front_ids and r["ROBUST_OK"] and not r["invalid"])
                r["neighbor_stable"] = "YES" if (not r["invalid"] and self._AgxRpStable(r, by_id)) else "NO"

            def tb(r):
                return (
                    -float(r.get("CAGR") or -9), float(r.get("MaxDD") or 9),
                    -float(r.get("worst5") or -9), -float(r.get("oos_sharpe") or -9),
                    float(r.get("turnover") or 9e9),
                    abs(r["ron"] - 1) + abs(r["neu"] - 1) + abs(r["roff"] - 1),
                )

            strict = sorted([r for r in valid_rows if r["STRICT_PASS"] and r["neighbor_stable"] == "YES"], key=tb)
            robust = sorted(
                [r for r in valid_rows if r["ROBUST_PARETO"] and r["neighbor_stable"] == "YES"
                 and _le(r.get("MaxDD"), ctrl_m.get("MaxDD"))
                 and _le(r.get("recovery_days_max"), ctrl_m.get("recovery_days_max"))],
                key=tb,
            )
            pick, cls = None, "CONTROL"
            if strict:
                pick, cls = strict[0], "STRICT_PASS"
            elif robust:
                pick, cls = robust[0], "ROBUST_PARETO"
            apply = "YES" if pick else "NO"
            if pick:
                nxt = "PREPARE_AGX_SHADOW_D3"
            elif len(valid_rows) >= 400 or (a_ok and b_ok and len(all_rows) >= 200):
                nxt = "KEEP_CONTROL"
            else:
                nxt = "KEEP_CONTROL"
            top = sorted(valid_rows, key=tb)[:10]
            for i, r in enumerate(top):
                self._AgxRpLog(
                    f"CG_AGX_P2_TOP,rank={i+1},id={r['id']},CAGR={_f(r.get('CAGR'))},"
                    f"MaxDD={_f(r.get('MaxDD'))},oos={_f(r.get('oos_sharpe'))},"
                    f"crisis={_f(r.get('crisis_maxdd'))},strict={r['STRICT_PASS']},"
                    f"pareto={r['ROBUST_PARETO']},stable={r['neighbor_stable']}"
                )
            self._AgxRpLog(
                f"CG_AGX_P2_GRID_FINAL,policies_evaluated={len(all_rows)},"
                f"model_a_control_valid={'YES' if a_ok else 'NO'},"
                f"model_b_control_valid={'YES' if b_ok else 'NO'},"
                f"strict_pass_count={sum(1 for r in valid_rows if r['STRICT_PASS'])},"
                f"robust_pareto_count={sum(1 for r in valid_rows if r['ROBUST_PARETO'])},"
                f"candidate_cash_model_valid=YES,next={nxt}"
            )
            if pick:
                self._AgxRpLog(
                    f"CG_AGX_P2_RECOMMENDATION,apply={apply},model=MODEL_{pick['model']},"
                    f"ron={pick['ron']:.2f},neu={pick['neu']:.2f},roff={pick['roff']:.2f},"
                    f"class={cls},CAGR={_f(pick.get('CAGR'))},MaxDD={_f(pick.get('MaxDD'))},"
                    f"StdDev={_f(pick.get('annual_stddev'))},OOS_Sharpe={_f(pick.get('oos_sharpe'))},"
                    f"CRISIS_MaxDD={_f(pick.get('crisis_maxdd'))},"
                    f"Y2020_MaxDD={_f(pick.get('y2020_maxdd'))},Y2022_MaxDD={_f(pick.get('y2022_maxdd'))},"
                    f"worst5={_f(pick.get('worst5'))},recovery={_f(pick.get('recovery_days_max'),1)},"
                    f"turnover={_f(pick.get('turnover'),2)},neighbor_stable={pick['neighbor_stable']},"
                    f"reason={cls.lower()}_stable_best_cagr"
                )
            else:
                self._AgxRpLog(
                    "CG_AGX_P2_RECOMMENDATION,apply=NO,model=CONTROL,ron=1.00,neu=1.00,"
                    "roff=1.00,class=CONTROL,CAGR=NA,MaxDD=NA,StdDev=NA,OOS_Sharpe=NA,"
                    "CRISIS_MaxDD=NA,Y2020_MaxDD=NA,Y2022_MaxDD=NA,worst5=NA,recovery=NA,"
                    "turnover=NA,neighbor_stable=NO,reason=no_stable_eligible"
                )
            hdr = (
                "id,model,ron,neu,roff,CAGR,MaxDD,annual_stddev,Sharpe,Sortino,"
                "worst_5pct_day_mean,recovery_days_max,turnover,fees,cap_hit_rate,"
                "oos_sharpe,crisis_maxdd,y2020_maxdd,y2022_maxdd,STRICT_PASS,"
                "ROBUST_PARETO,neighbor_stable,invalid"
            )
            lines = [hdr]
            for r in all_rows:
                lines.append(",".join(str(r.get(h, "NA")) for h in hdr.split(",")))
            bid = "unknown"
            try:
                bid = str(getattr(self, "algorithm_id", None) or getattr(self, "AlgorithmId", None) or "unknown")
            except Exception:
                pass
            csv_key = f"cg_agx_cash_p2_{bid}.csv"
            try:
                self.object_store.save(csv_key, "\n".join(lines))
            except Exception:
                csv_key = f"NONE"
            self._AgxRpLog(f"CG_AGX_P2_GRID_FINAL,artifact={csv_key}")
        except Exception as exc:
            self._agx_err += 1
            try:
                self.log(f"CG_AGX_P2_GRID_FINAL,emit_error={type(exc).__name__}:{exc}")
            except Exception:
                pass
