# region imports
from AlgorithmImports import *
from datetime import date as _date
# endregion
# Generic production-event shadow replay / independent accounting core.
# No AGX grids. No MAISR classifier logic.

_DFT_DEF = frozenset(("TIP", "BND", "GLD", "GLDM", "BIL", "SGOV", "USFR", "SH"))
_LOG_BUDGET = 28000


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


def _new_led(pid, cash0, meta=None):
    d = {
        "id": pid, "cash": float(cash0), "qty": {}, "nav": float(cash0), "peak": float(cash0),
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
        "same_day_cut": False, "cut_syms": set(),
        "pre_cuts": 0, "post_cuts": 0,
        "local_cuts": 0, "sector_cuts": 0, "broad_cuts": 0, "systemic_cuts": 0,
        "rate_cuts": 0, "def_rot_cuts": 0,
        "missed_fill": 0, "signal_n": 0, "fill_n": 0,
        "sum_delay": 0.0, "sum_slip": 0.0,
        "false_broad": 0, "missed_sys": 0, "loc_to_broad": 0, "sys_to_loc": 0,
        "sum_risk_red": 0.0, "max_risk_red": 0.0, "affected_days": 0,
    }
    if meta:
        d.update(meta)
    return d


class CgShadowReplayMixin:
    """Production event-replay control + candidate ledger accounting hooks."""

    def CgShadowReplayInit(self) -> None:
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

        # Enabled by MAISR diagnostic (or explicit shadow enable if present).
        self.cg_maisr_diag_enable = _bool("cg_maisr_diag_enable", "0")
        self.cg_maisr_grid_enable = _bool("cg_maisr_grid_enable", "0")
        self.cg_maisr_emit_events = _bool("cg_maisr_emit_events", "0")
        self._sr_cost_bps = _float("cg_maisr_cost_bps", 0.0)
        prod_cap = float(getattr(self, "max_total_exposure", 1.90) or 1.90)
        self._sr_prod_gross_cap = prod_cap
        self._sr_max_gross = float(prod_cap)
        self._sr_on = bool(self.cg_maisr_diag_enable)
        self._sr_grid_on = bool(self._sr_on and self.cg_maisr_grid_enable)

        cash_tk = getattr(self, "sym_cash", None)
        self._sr_cash_tk = _tk(cash_tk) if cash_tk is not None else "BIL"
        cash0 = 10000.0
        try:
            c = float(self.portfolio.cash)
            if c > 0:
                cash0 = c
        except Exception:
            pass
        self._sr_cash0 = cash0
        try:
            sd = getattr(self, "start_date", None) or getattr(self, "StartDate", None)
            self._sr_start_date = sd.date() if hasattr(sd, "date") else sd
        except Exception:
            self._sr_start_date = _date(2012, 1, 1)

        self._sr_log_used = 0
        self._sr_err = 0
        self._sr_emitted = False
        self._sr_started = False
        self._sr_last_mark = None
        self._sr_prev_tpv = None
        self._sr_dates = []
        self._sr_actual_rets = []
        self._sr_replay_rets = []
        self._sr_n_cap = 0
        self._sr_n_imm = 0
        self._sr_n_def = 0
        self._sr_n_exe = 0
        self._sr_target_mismatch_count = 0
        self._sr_max_abs_target_weight_diff = 0.0
        self._sr_last_base = {}
        self._sr_base_gross_obs = []
        self._sr_last_base_gross = None
        self._sr_regime_prev = None
        self._sr_regime_age = 10 ** 9
        self._sr_max_cash_diff = 0.0
        self._sr_max_hold_diff = 0.0
        self._sr_corp_mm = 0
        self._sr_div_n = 0
        self._sr_div_applied = 0
        self._sr_div_ign_adj = 0
        self._sr_div_ign_wu = 0
        self._sr_div_dup = 0
        self._sr_div_seen_keys = set()
        self._sr_split_n = 0
        self._sr_map_n = 0
        self._sr_delist_n = 0
        self._sr_cf_n = 0
        self._sr_reg_n = 0
        self._sr_unclass_n = 0
        self._sr_recon_n = 0
        self._sr_orphan_n = 0
        self._sr_dup_n = 0
        self._sr_fill_n = 0
        self._sr_ov_n = 0
        self._sr_wu_seen = 0
        self._sr_wu_ign = 0
        self._sr_first_div = None
        self._sr_cash_cat = {
            "FILL_NOTIONAL": 0.0, "ORDER_FEE": 0.0, "DIVIDEND_CASH": 0.0,
            "SPLIT_CASH_IN_LIEU": 0.0, "EXTERNAL_DEPOSIT": 0.0, "EXTERNAL_WITHDRAWAL": 0.0,
            "INTEREST_OR_BORROW": 0.0, "FX_CONVERSION": 0.0, "DELISTING_CASH": 0.0,
            "OTHER_IDENTIFIED": 0.0, "UNKNOWN": 0.0,
        }
        self._sr_seen_fill = set()
        self._sr_order_meta = {}
        self._sr_norm_cache = {}
        self._sr_ctx = {
            "class": "ACCOUNTING_ONLY", "source": "bootstrap", "fn": "init",
            "reduce_only": False, "emergency": False, "targets": None,
            "shadow_eligible": False,
        }
        self._sr_cls = {
            "SCALABLE_RISK": set(["SPY"]),
            "FIXED_DEFENSIVE": set(t for t in _DFT_DEF if t != self._sr_cash_tk),
            "PARKING_ETF": set([self._sr_cash_tk]),
            "OTHER_FIXED": set(),
            "KEEP_FIXED_UNCERTAIN": set(),
            "src": {"SPY": "W2/equity_risk_sleeve", self._sr_cash_tk: "sym_cash"},
        }
        for t in self._sr_cls["FIXED_DEFENSIVE"]:
            self._sr_cls["src"][t] = "cg_defensive_trade._DFT_DEF"
        self._sr_path_stats = {}
        self._sr_ctrl = _new_led("REPLAY_CONTROL", cash0)
        self._sr_pols = []
        self._sr_by_id = {}
        self._sr_identity_ids = set()

        lp = list(getattr(self, "log_only_prefixes", None) or [])
        for pref in ("CG_MAISR_D0_", "CG_SHADOW_REPLAY_", "[INIT] CG_MAISR"):
            if pref not in lp:
                lp.append(pref)
        self.log_only_prefixes = lp

        if self._sr_on:
            self.log(
                f"CG_SHADOW_REPLAY_INIT,enable=1,control_mode=PRODUCTION_EVENT_REPLAY,"
                f"grid_enable={int(self._sr_grid_on)},parking_etf={self._sr_cash_tk},"
                f"max_gross={_f(self._sr_max_gross)},cost_bps={_f(self._sr_cost_bps,2)},"
                f"start={self._sr_start_date},manual_div=normalization_safe"
            )
            self._SrInstallHooks()
            try:
                spy = getattr(self, "sym_spy", None)
                if spy is not None:
                    self.schedule.on(
                        self.date_rules.every_day(spy),
                        self.time_rules.after_market_open(spy, 14),
                        self.CgShadowReplayMark,
                    )
            except Exception as exc:
                self._sr_err += 1
                self.log(f"CG_SHADOW_REPLAY_INIT,schedule_error={type(exc).__name__}")

    def CgShadowRegisterPolicies(self, policies):
        """Attach candidate ledgers (called by MAISR after TRAIN selection)."""
        if not getattr(self, "_sr_on", False):
            return
        cash0 = float(self._sr_cash0)
        self._sr_pols = []
        self._sr_by_id = {}
        self._sr_identity_ids = set()
        for meta in policies or []:
            pid = str(meta.get("id") or "")
            p = _new_led(pid, cash0, meta)
            # Seed from control qty/cash if already running
            p["cash"] = float(self._sr_ctrl.get("cash", cash0))
            p["qty"] = dict(self._sr_ctrl.get("qty") or {})
            p["nav"] = float(self._sr_ctrl.get("nav", cash0))
            p["peak"] = float(self._sr_ctrl.get("peak", cash0))
            p["_prev_nav"] = self._sr_ctrl.get("_prev_nav")
            self._sr_pols.append(p)
            self._sr_by_id[pid] = p
            if meta.get("identity"):
                self._sr_identity_ids.add(pid)
        self._sr_grid_on = bool(self._sr_pols)

    def _SrLedgerActive(self):
        if getattr(self, "IsWarmingUp", False) or getattr(self, "is_warming_up", False):
            return False
        sd = getattr(self, "_sr_start_date", None)
        if sd is not None:
            try:
                if self.time.date() < sd:
                    return False
            except Exception:
                pass
        return True

    def _SrManualDiv(self, sym):
        t = _tk(sym) if not isinstance(sym, str) else sym
        if t in self._sr_norm_cache:
            return self._sr_norm_cache[t]
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
        self._sr_norm_cache[t] = need
        return need

    def _SrNoteCashDiverg(self, event, symbol, prod_delta, replay_delta, qty=None):
        if self._sr_first_div is not None:
            return
        try:
            pc = float(self.portfolio.cash)
            rc = float(self._sr_ctrl.get("cash", 0.0))
            if abs(rc - pc) <= 0.01:
                return
            self._sr_first_div = {
                "time": str(self.time), "symbol": symbol, "event": event,
                "production_delta": prod_delta, "replay_delta": replay_delta,
                "difference": rc - pc, "quantity": qty,
            }
        except Exception:
            pass

    def _SrInstallHooks(self):
        if getattr(self, "_sr_hooks", False):
            return
        self._sr_hooks = True
        _osh = self.set_holdings

        def _wsh(targets, *a, **kw):
            try:
                self._SrNoteSetHoldings(targets)
            except Exception:
                pass
            return _osh(targets, *a, **kw)

        self.set_holdings = _wsh
        _oliq = self.liquidate

        def _wliq(symbol=None, *a, **kw):
            try:
                self._SrNoteLiquidate(symbol)
            except Exception:
                pass
            if symbol is None:
                return _oliq()
            return _oliq(symbol, *a, **kw)

        self.liquidate = _wliq

    def _SrLog(self, msg):
        try:
            n = len(msg) + 1
            if self._sr_log_used + n > _LOG_BUDGET:
                return
            self.log(msg)
            self._sr_log_used += n
        except Exception:
            pass

    def _SrPath(self, name, impact=0.0):
        st = self._sr_path_stats.get(name)
        if st is None:
            st = {"n": 0, "first": str(self.time.date()), "impact": 0.0}
            self._sr_path_stats[name] = st
        st["n"] += 1
        st["impact"] = max(float(st["impact"]), float(impact))

    def CgShadowReplayPushCtx(self, source, fn, event_class, reduce_only=False,
                              emergency=False, targets=None, shadow_eligible=False,
                              agx_eligible=None):
        if not getattr(self, "_sr_on", False):
            return
        if agx_eligible is not None and not shadow_eligible:
            shadow_eligible = bool(agx_eligible)
        self._sr_ctx = {
            "class": event_class,
            "source": source,
            "fn": fn,
            "reduce_only": bool(reduce_only),
            "emergency": bool(emergency),
            "targets": targets,
            "shadow_eligible": bool(shadow_eligible),
        }

    def CgShadowReplayPopCtx(self):
        if not getattr(self, "_sr_on", False):
            return
        self._sr_ctx = {
            "class": "ACCOUNTING_ONLY", "source": "idle", "fn": "idle",
            "reduce_only": False, "emergency": False, "targets": None,
            "shadow_eligible": False,
        }

    def _SrNoteSetHoldings(self, targets):
        ctx = self._sr_ctx or {}
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
            ctx["class"] = "MANDATORY_OVERRIDE"
            ctx["shadow_eligible"] = False
        self._sr_ctx = ctx
        self._SrPath(f"{ctx.get('fn')}:set_holdings")

    def _SrNoteLiquidate(self, symbol):
        ctx = dict(self._sr_ctx or {})
        if not ctx.get("source") or ctx.get("source") in ("idle", "bootstrap"):
            ctx["source"] = "liquidate"
            ctx["fn"] = "liquidate"
        ctx["class"] = "MANDATORY_OVERRIDE"
        ctx["shadow_eligible"] = False
        ctx["targets"] = {"__LIQUIDATE__": _tk(symbol) if symbol is not None else "*"}
        self._sr_ctx = ctx
        self._SrPath(f"{ctx.get('fn')}:liquidate")

    def _SrClassifyTicker(self, t):
        if t in self._sr_cls["PARKING_ETF"]:
            return "PARKING_ETF"
        if t in self._sr_cls["FIXED_DEFENSIVE"] or t in _DFT_DEF:
            self._sr_cls["FIXED_DEFENSIVE"].add(t)
            return "FIXED_DEFENSIVE"
        if t in self._sr_cls["SCALABLE_RISK"]:
            return "SCALABLE_RISK"
        if t.isalpha() and len(t) <= 5 and t not in _DFT_DEF:
            self._sr_cls["SCALABLE_RISK"].add(t)
            self._sr_cls["src"][t] = "non_defensive_target_as_risk_sleeve"
            return "SCALABLE_RISK"
        self._sr_cls["KEEP_FIXED_UNCERTAIN"].add(t)
        self._sr_cls["OTHER_FIXED"].add(t)
        self._sr_cls["src"][t] = "KEEP_FIXED_UNCERTAIN"
        return "KEEP_FIXED_UNCERTAIN"

    def _SrPx(self, tickers=None):
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

    def _SrNav(self, led, px):
        hv = 0.0
        for t, q in (led.get("qty") or {}).items():
            p = (px or {}).get(t)
            if p and p > 0:
                hv += float(q) * p
        return float(led.get("cash", 0.0)) + hv, hv

    def _SrApplyFill(self, led, t, signed_q, px, fee):
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

    def _SrLiquidateLed(self, led, px):
        for t, q in list((led.get("qty") or {}).items()):
            p = (px or {}).get(t)
            if not p or p <= 0:
                continue
            self._SrApplyFill(led, t, -float(q), p, 0.0)
        led["ov"] = int(led.get("ov", 0)) + 1

    def _SrApplyWeights(self, led, weights, px, cost_bps=None):
        nav, _ = self._SrNav(led, px)
        if nav <= 0:
            return
        bps = float(self._sr_cost_bps if cost_bps is None else cost_bps)
        for t in list((led.get("qty") or {}).keys()):
            if t not in (weights or {}):
                p = (px or {}).get(t)
                if p and p > 0:
                    q = float(led["qty"].get(t, 0.0))
                    self._SrApplyFill(led, t, -q, p, 0.0)
        for t, w in (weights or {}).items():
            p = (px or {}).get(t)
            if not p or p <= 0:
                continue
            desire = float(w) * nav / p
            cur = float((led.get("qty") or {}).get(t, 0.0))
            dq = desire - cur
            if abs(dq) * p < 1.0:
                continue
            fee = abs(dq) * p * bps / 10000.0
            self._SrApplyFill(led, t, dq, p, fee)
        led["reb"] = int(led.get("reb", 0)) + 1

    def _SrReduceOnlyWeights(self, led, scale_map, px):
        """Reduce-only: scale selected positive risk holdings; cash absorbs release."""
        nav, _ = self._SrNav(led, px)
        if nav <= 0:
            return False, 0.0
        cur_w = {}
        for t, q in (led.get("qty") or {}).items():
            p = (px or {}).get(t)
            if p and p > 0 and abs(q) > 0:
                cur_w[t] = float(q) * p / nav
        new_w = dict(cur_w)
        reduced = 0.0
        changed = False
        for t, mult in (scale_map or {}).items():
            if t not in new_w:
                continue
            w0 = float(new_w[t])
            if w0 <= 0:
                continue
            m = max(0.0, min(1.0, float(mult)))
            if m >= 1.0 - 1e-15:
                continue
            # No same-day re-risk / no increase
            if led.get("same_day_cut") and m > float(getattr(led, "_last_mult_" + t, 1.0) or 1.0):
                continue
            w1 = w0 * m
            reduced += (w0 - w1)
            new_w[t] = w1
            changed = True
            led.setdefault("cut_syms", set()).add(t)
        if not changed:
            return False, 0.0
        # Released weight stays in cash ledger (do not park into BIL)
        self._SrApplyWeights(led, new_w, px)
        led["same_day_cut"] = True
        led["sum_risk_red"] = float(led.get("sum_risk_red", 0.0)) + reduced
        led["max_risk_red"] = max(float(led.get("max_risk_red", 0.0)), reduced)
        return True, reduced

    def CgShadowReplayOnOrderEvent(self, order_event) -> None:
        if not getattr(self, "_sr_on", False) or order_event is None:
            return
        try:
            if not self._SrLedgerActive():
                self._sr_wu_seen += 1
                self._sr_wu_ign += 1
                return
            oid = getattr(order_event, "order_id", None)
            if oid is None:
                oid = getattr(order_event, "OrderId", None)
            status = getattr(order_event, "status", None)
            st = str(status)
            t = _tk(getattr(order_event, "symbol", None))
            meta = self._sr_order_meta.get(oid)
            if meta is None:
                ctx = self._sr_ctx or {}
                ecl = ctx.get("class") or "ACCOUNTING_ONLY"
                if ecl not in ("NORMAL_SHADOW_ELIGIBLE", "MANDATORY_OVERRIDE",
                               "ACCOUNTING_ONLY", "NOT_APPLICABLE"):
                    ecl = "ACCOUNTING_ONLY"
                    self._sr_unclass_n += 1
                meta = {
                    "oid": oid, "symbol": t, "class": ecl,
                    "source": ctx.get("source"), "fn": ctx.get("fn"),
                    "shadow_eligible": bool(ctx.get("shadow_eligible")),
                    "reduce_only": bool(ctx.get("reduce_only")),
                    "emergency": bool(ctx.get("emergency")),
                    "targets": dict(ctx.get("targets") or {}),
                }
                self._sr_order_meta[oid] = meta
                self._sr_reg_n += 1
                if ctx.get("source") in (None, "idle", "bootstrap") and ecl == "ACCOUNTING_ONLY":
                    self._sr_recon_n += 1
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
            if key in self._sr_seen_fill:
                self._sr_dup_n += 1
                return
            self._sr_seen_fill.add(key)
            self._sr_fill_n += 1
            impact = abs(signed) * px
            self._SrPath(f"fill:{meta.get('fn')}:{meta.get('class')}", impact)
            notional_delta = -float(signed) * float(px)
            fee_delta = -float(fee)
            self._sr_cash_cat["FILL_NOTIONAL"] += notional_delta
            self._sr_cash_cat["ORDER_FEE"] += fee_delta
            self._SrApplyFill(self._sr_ctrl, t, signed, px, fee)
            self._SrNoteCashDiverg("FILL", t, None, notional_delta + fee_delta, signed)

            if not self._sr_grid_on:
                return
            ecl = meta.get("class")
            for p in self._sr_pols:
                is_identity = p["id"] in self._sr_identity_ids or p.get("identity")
                if is_identity:
                    self._SrApplyFill(p, t, signed, px, fee)
                    continue
                if ecl == "NORMAL_SHADOW_ELIGIBLE":
                    continue
                if ecl == "MANDATORY_OVERRIDE":
                    tgt = meta.get("targets") or {}
                    pxmap = self._SrPx()
                    if "__LIQUIDATE__" in tgt:
                        self._SrLiquidateLed(p, pxmap)
                        self._sr_ov_n += 1
                    elif tgt:
                        self._SrApplyWeights(p, tgt, pxmap)
                        self._sr_ov_n += 1
                    else:
                        self._SrApplyFill(p, t, signed, px, fee)
                        self._sr_ov_n += 1
                else:
                    self._SrApplyFill(p, t, signed, px, fee)
        except Exception:
            self._sr_err += 1

    def CgShadowReplayOnData(self, data) -> None:
        if not getattr(self, "_sr_on", False) or data is None:
            return
        try:
            active = self._SrLedgerActive()
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
                        self._sr_div_n += 1
                        dkey = f"{t}|{self.time.date()}|{dist:.8f}"
                        if dkey in self._sr_div_seen_keys:
                            self._sr_div_dup += 1
                            continue
                        self._sr_div_seen_keys.add(dkey)
                        if not active:
                            self._sr_wu_seen += 1
                            self._sr_wu_ign += 1
                            self._sr_div_ign_wu += 1
                            continue
                        if not self._SrManualDiv(sym):
                            self._sr_div_ign_adj += 1
                            continue
                        q = float((self._sr_ctrl.get("qty") or {}).get(t, 0.0))
                        if abs(q) < 1e-12:
                            continue
                        credit = q * dist
                        self._sr_div_applied += 1
                        self._sr_cash_cat["DIVIDEND_CASH"] += credit
                        for led in [self._sr_ctrl] + (self._sr_pols if self._sr_grid_on else []):
                            lq = float((led.get("qty") or {}).get(t, 0.0))
                            if abs(lq) > 0:
                                led["cash"] = float(led.get("cash", 0.0)) + lq * dist
                        self._SrPath("dividend", abs(dist))
                        self._SrNoteCashDiverg("DIVIDEND", t, None, credit, q)
                    except Exception:
                        self._sr_corp_mm += 1
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
                            self._sr_wu_seen += 1
                            self._sr_wu_ign += 1
                            continue
                        self._sr_split_n += 1
                        for led in [self._sr_ctrl] + (self._sr_pols if self._sr_grid_on else []):
                            q = float((led.get("qty") or {}).get(t, 0.0))
                            if abs(q) > 0:
                                led["qty"][t] = q / factor
                        self._SrPath("split", abs(factor))
                    except Exception:
                        self._sr_corp_mm += 1
        except Exception:
            self._sr_err += 1

    def _SrBaseW(self, combined):
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
            self._SrClassifyTicker(t)
        return w

    def _SrGross(self, w):
        g = 0.0
        cash = self._sr_cash_tk
        for t, v in (w or {}).items():
            if t == cash:
                continue
            g += abs(float(v or 0.0))
        return g

    def CgShadowReplayCapture(self, combined, regime, slot, reduce_only=False, emergency=False) -> None:
        if not getattr(self, "_sr_on", False):
            return
        try:
            if getattr(self, "IsWarmingUp", False) or getattr(self, "is_warming_up", False):
                return
            base = self._SrBaseW(combined)
            bg = self._SrGross(base)
            self._sr_base_gross_obs.append(bg)
            self._sr_last_base_gross = bg
            self._sr_last_base = dict(base)
            self._sr_n_cap += 1
            rg = str(regime or getattr(self, "current_regime", None) or "NEUTRAL").upper()
            if rg not in ("RISK_ON", "NEUTRAL", "RISK_OFF"):
                rg = "NEUTRAL"
            imm = bool(reduce_only or emergency or int(slot or 0) == 15)
            if imm:
                self._sr_n_imm += 1
            else:
                self._sr_n_def += 1
            self._sr_target_mismatch_count += 0
            self._sr_last_capture = {
                "base": dict(base), "regime": rg, "slot": int(slot or 0),
                "imm": imm, "reduce_only": bool(reduce_only), "emergency": bool(emergency),
                "date": self.time.date(),
            }
            # MAISR may queue pending candidate targets for deferred slot
            try:
                if hasattr(self, "CgMaisrOnCapture"):
                    self.CgMaisrOnCapture(base, rg, int(slot or 0), imm, reduce_only, emergency)
            except Exception:
                self._sr_err += 1
        except Exception:
            self._sr_err += 1

    def CgShadowReplayExecutePending(self) -> None:
        if not getattr(self, "_sr_on", False):
            return
        try:
            try:
                if hasattr(self, "CgMaisrOnExecutePending"):
                    self.CgMaisrOnExecutePending()
            except Exception:
                self._sr_err += 1
            if self._sr_grid_on:
                px = self._SrPx()
                for p in self._sr_pols:
                    pend = p.get("pending")
                    if pend is None:
                        continue
                    if p["id"] in self._sr_identity_ids or p.get("identity"):
                        p["pending"] = None
                        continue
                    self._SrApplyWeights(p, pend, px)
                    p["pending"] = None
            self._sr_n_exe += 1
        except Exception:
            self._sr_err += 1

    def _SrApplyDaily(self, led, prev_nav, nav, rg, age, eg):
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
        # Reset same-day cut latch at mark (new trading day next)
        led["same_day_cut"] = False

    def CgShadowReplayMark(self) -> None:
        if not getattr(self, "_sr_on", False):
            return
        try:
            if getattr(self, "IsWarmingUp", False) or getattr(self, "is_warming_up", False):
                return
            today = self.time.date()
            if self._sr_last_mark == today:
                return
            rg = str(getattr(self, "current_regime", None) or "NEUTRAL").upper()
            if rg not in ("RISK_ON", "NEUTRAL", "RISK_OFF"):
                rg = "NEUTRAL"
            if self._sr_regime_prev is None:
                self._sr_regime_prev = rg
                self._sr_regime_age = 10 ** 9
            elif rg != self._sr_regime_prev:
                self._sr_regime_prev = rg
                self._sr_regime_age = 0
            else:
                self._sr_regime_age += 1
            age = self._sr_regime_age
            try:
                tpv = float(self.portfolio.total_portfolio_value)
            except Exception:
                tpv = None
            try:
                prod_cash = float(self.portfolio.cash)
            except Exception:
                prod_cash = None
            prod_hold = (tpv - prod_cash) if (tpv is not None and prod_cash is not None) else None
            tickers = set(self._sr_ctrl.get("qty") or {}) | set(self._sr_last_base or {})
            tickers.add(self._sr_cash_tk)
            if self._sr_grid_on:
                for p in self._sr_pols:
                    tickers.update((p.get("qty") or {}).keys())
            px = self._SrPx(tickers)
            ind_nav, ind_hold = self._SrNav(self._sr_ctrl, px)
            ind_cash = float(self._sr_ctrl.get("cash", 0.0))
            if not self._sr_started:
                self._sr_started = True
                self._sr_prev_tpv = tpv if tpv else self._sr_cash0
                self._sr_ctrl["_prev_nav"] = ind_nav if ind_nav > 0 else self._sr_cash0
                if self._sr_grid_on:
                    for p in self._sr_pols:
                        n, _ = self._SrNav(p, px)
                        p["_prev_nav"] = n if n > 0 else self._sr_cash0
                self._sr_last_mark = today
                return
            if self._sr_prev_tpv and self._sr_prev_tpv > 0 and tpv is not None:
                ar = tpv / self._sr_prev_tpv - 1.0
            else:
                ar = 0.0
            self._sr_actual_rets.append(ar)
            self._sr_dates.append(today)
            self._sr_prev_tpv = tpv if tpv is not None else self._sr_prev_tpv
            prev = self._sr_ctrl.get("_prev_nav")
            self._SrApplyDaily(self._sr_ctrl, prev, ind_nav, rg, age, None)
            self._sr_ctrl["_prev_nav"] = ind_nav
            if prev and prev > 0:
                self._sr_replay_rets.append(ind_nav / prev - 1.0)
            else:
                self._sr_replay_rets.append(0.0)
            if prod_cash is not None:
                self._sr_max_cash_diff = max(self._sr_max_cash_diff, abs(prod_cash - ind_cash))
                self._SrNoteCashDiverg("MARK", self._sr_cash_tk, None, None)
            if prod_hold is not None:
                self._sr_max_hold_diff = max(self._sr_max_hold_diff, abs(prod_hold - ind_hold))
            if self._sr_grid_on:
                for p in self._sr_pols:
                    n2, _ = self._SrNav(p, px)
                    self._SrApplyDaily(p, p.get("_prev_nav"), n2, rg, age, None)
                    p["_prev_nav"] = n2
            self._sr_last_mark = today
            try:
                if hasattr(self, "CgMaisrOnMark"):
                    self.CgMaisrOnMark(today, px)
            except Exception:
                pass
        except Exception:
            self._sr_err += 1

    def _SrCorr(self, a, b):
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

    def _SrMetrics(self, rets):
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

    def _SrWin(self, dates, rets, s, e):
        xs = [r for d, r in zip(dates, rets) if (s is None or d >= s) and (e is None or d <= e)]
        return self._SrMetrics(xs)

    def _SrGate(self):
        n_a, n_r = len(self._sr_actual_rets), len(self._sr_replay_rets)
        match = bool(n_a == n_r and n_a > 0)
        am = self._SrMetrics(self._sr_actual_rets) or {}
        rm = self._SrMetrics(self._sr_replay_rets) or {}
        a_nav, r_nav = am.get("end_nav"), rm.get("end_nav")
        a_dd, r_dd = am.get("MaxDD"), rm.get("MaxDD")
        nav_d = ((r_nav / a_nav - 1.0) * 100.0) if (a_nav and r_nav is not None) else None
        dd_d = ((r_dd - a_dd) * 100.0) if (a_dd is not None and r_dd is not None) else None
        if match:
            diffs = [abs(x - y) for x, y in zip(self._sr_actual_rets, self._sr_replay_rets)]
            max_d = max(diffs) if diffs else 0.0
            mean_d = sum(diffs) / len(diffs) if diffs else 0.0
            corr = self._SrCorr(self._sr_actual_rets, self._sr_replay_rets)
        else:
            max_d = mean_d = corr = None
        try:
            prod_end = float(self.portfolio.cash)
        except Exception:
            prod_end = None
        known = float(self._sr_cash0) + sum(float(v) for k, v in self._sr_cash_cat.items() if k != "UNKNOWN")
        unknown = (prod_end - known) if prod_end is not None else None
        if unknown is not None:
            self._sr_cash_cat["UNKNOWN"] = float(unknown)
        hold_ok = self._sr_max_hold_diff <= 0.01
        cash_ok = self._sr_max_cash_diff <= 0.10
        wu_ok = self._sr_wu_seen == self._sr_wu_ign
        ok = (
            self._sr_n_cap > 0
            and self._sr_target_mismatch_count == 0
            and self._sr_unclass_n == 0
            and self._sr_orphan_n == 0
            and self._sr_dup_n == 0
            and match
            and hold_ok
            and cash_ok
            and wu_ok
            and nav_d is not None and abs(nav_d) <= 0.10
            and dd_d is not None and abs(dd_d) <= 0.10
            and corr is not None and corr >= 0.9999
            and max_d is not None and max_d <= 0.0005
            and mean_d is not None and mean_d <= 0.00005
            and self._sr_corp_mm == 0
            and self._sr_err == 0
        )
        return {
            "pass": bool(ok), "nav_d": nav_d, "dd_d": dd_d, "corr": corr,
            "max_d": max_d, "mean_d": mean_d, "match": match,
            "unknown": unknown, "hold_ok": hold_ok, "cash_ok": cash_ok,
            "_am": am, "_rm": rm,
        }

    def CgShadowReplayEmitFinal(self) -> None:
        if getattr(self, "_sr_emitted", False):
            return
        self._sr_emitted = True
        if not getattr(self, "_sr_on", False):
            return
        try:
            gate = self._SrGate()
            ok = bool(gate["pass"])
            am, rm = gate.get("_am") or {}, gate.get("_rm") or {}
            self._sr_parity_ok = ok
            self._SrLog(
                f"CG_MAISR_D0_PARITY_FINAL,holdings_parity={'PASS' if gate.get('hold_ok') else 'FAIL'},"
                f"cash_parity={'PASS' if ok else 'FAIL'},"
                f"control_mode=PRODUCTION_EVENT_REPLAY,"
                f"target_mismatch_count={self._sr_target_mismatch_count},"
                f"registered_order_count={self._sr_reg_n},fill_event_count={self._sr_fill_n},"
                f"unclassified_order_count={self._sr_unclass_n},"
                f"orphan_order_event_count={self._sr_orphan_n},"
                f"duplicate_fill_replay_count={self._sr_dup_n},"
                f"max_abs_holdings_value_difference={_f(self._sr_max_hold_diff,2)},"
                f"max_abs_cash_difference={_f(self._sr_max_cash_diff,2)},"
                f"nav_difference_pct={_f(gate['nav_d'],6)},"
                f"maxdd_difference_pp={_f(gate['dd_d'],6)},"
                f"daily_return_correlation={_f(gate['corr'],6)},"
                f"max_abs_daily_return_difference={_f(gate['max_d'],6)},"
                f"mean_abs_daily_return_difference={_f(gate['mean_d'],6)},"
                f"daily_return_count_match={'YES' if gate['match'] else 'NO'},"
                f"runtime_errors={self._sr_err}"
            )
            try:
                if hasattr(self, "CgMaisrOnEndOfAlgorithm"):
                    self.CgMaisrOnEndOfAlgorithm(ok)
            except Exception as exc:
                self._SrLog(f"CG_MAISR_D0_VALIDATION_FINAL,emit_error={type(exc).__name__}")
        except Exception as exc:
            try:
                self.log(f"CG_MAISR_D0_PARITY_FINAL,emit_error={type(exc).__name__}")
            except Exception:
                pass
