from AlgorithmImports import *

# [E0.1] Centralized execution-subscription integrity (execution-only).
_CG_MINUTE_ETFS = frozenset({
    "SPY", "GLD", "BND", "TIP",
    "BIL", "TFLO", "SGOV",
    "SH", "SPYG",
    "XLE", "XLB", "XLV", "XLU", "GLDM", "DBC",
})
# Tradable universe: any equity that can ever receive an order => MINUTE.
_CG_TRADABLE = _CG_MINUTE_ETFS | frozenset({"MU", "NVDA", "AVGO", "USFR"})


class CoreGrowthSubscriptionMixin:

    def _CgFlag(self, name: str) -> bool:
        try:
            ov = getattr(self, "_rrx_param_overrides", {}) or {}
            v = self.get_parameter(name)
            if v is None or str(v).strip() == "":
                v = ov.get(name)
            return str(v or "0").strip().lower() in ("1", "true", "yes", "on")
        except Exception:
            return False

    def _CgBuildTradableExtra(self) -> None:
        """[E0.4.1] RRX symbols count as tradable for SUBSCRIPTION purposes only
        for the specific symbols each enabled real trading path can route orders
        to. Never alters which symbol is selected or sized -- that logic is
        untouched. Two independent sources, both execution-only:
          * rrx_trade_bridge_enable -> full RRX_THEMES universe (bridge leader).
          * spyg_sat_trade_enable   -> SPY_GROWTH-bucket stocks only (the only
            pool SPYGSatTrade's leader-first signal can ever select from)."""
        extra = set()
        self._cg_rrx_bridge_flag = self._CgFlag("rrx_trade_bridge_enable")
        self._cg_spyg_sat_flag   = self._CgFlag("spyg_sat_trade_enable")
        if self._cg_rrx_bridge_flag:
            try:
                from rr_xsector_diag import RRX_THEMES
                for c in RRX_THEMES.values():
                    extra.add(str(c.get("etf", "")).upper())
                    for s in c.get("stocks", []):
                        extra.add(str(s).upper())
            except Exception:
                pass
        if self._cg_spyg_sat_flag:
            try:
                from rr_xsector_diag import RRX_THEMES
                from rrx_leader_first_diag import _BUCKET_BY_GROUP
                for c in RRX_THEMES.values():
                    rg = str(c.get("risk_group", ""))
                    if _BUCKET_BY_GROUP.get(rg, "") == "SPY_GROWTH":
                        for s in c.get("stocks", []):
                            extra.add(str(s).upper())
            except Exception:
                pass
        self._cg_tradable_extra = extra

    def _CgRegisterEquity(self, ticker, tradable: bool = False):
        """[E0.1] Single deduplicated registration path. Stores Security, effective
        resolution and tradable flag. Re-registration returns the cached Security
        and never calls add_equity() again."""
        tkr = str(ticker or "").strip().upper()
        if not hasattr(self, "_cg_sub_registry"):
            self._cg_sub_registry = {}
        reg = self._cg_sub_registry
        is_tradable = (bool(tradable) or tkr in _CG_TRADABLE
                       or tkr in getattr(self, "_cg_tradable_extra", frozenset()))
        rec = reg.get(tkr)
        if rec is not None:
            if is_tradable and not bool(rec.get("tradable", False)):
                raise Exception(f"CG_SUB_LATE_UPGRADE:{tkr}:DAILY_TO_MINUTE")
            return rec["security"]
        res = Resolution.MINUTE if is_tradable else Resolution.DAILY
        sec = self.add_equity(tkr, res)
        reg[tkr] = {"security": sec, "resolution": res, "tradable": is_tradable}
        return sec

    def _CgAddEquity(self, ticker):
        return self._CgRegisterEquity(ticker, tradable=False)

    def _CgTicker(self, sym) -> str:
        try:
            return str(sym.Value).upper()
        except Exception:
            try:
                return str(sym.value).upper()
            except Exception:
                return str(sym).upper()

    def _CgSymbolBlockedForTrade(self, sym) -> bool:
        """[E0.4] True only for signal-only diagnostic equities that must never
        receive a real order. Classification uses the centralized registry and the
        explicit tradable sets, never RRX_THEMES membership. Explicitly tradable
        symbols (CoreGrowth, active RR MU/NVDA/AVGO/USFR, SPYG, bridge-enabled RRX)
        return False."""
        tkr = self._CgTicker(sym)
        if tkr in _CG_TRADABLE or tkr in getattr(self, "_cg_tradable_extra", frozenset()):
            return False
        rec = getattr(self, "_cg_sub_registry", {}).get(tkr)
        return bool(rec is not None and not rec.get("tradable", False))

    def _CgFinalTradeGate(self, targets):
        """[E0.4] Final safety gate immediately before order submission.
        Non-zero real target for a signal-only diagnostic symbol:
        backtest -> raise; live -> drop that target and log."""
        if not targets:
            return targets
        out = None
        for sym in list(targets.keys()):
            try:
                w = float(targets.get(sym, 0.0) or 0.0)
            except Exception:
                w = 0.0
            if w != 0.0 and self._CgSymbolBlockedForTrade(sym):
                tkr = self._CgTicker(sym)
                if not self.live_mode:
                    raise Exception(f"CG_DIAG_TRADE_BLOCK:{tkr}")
                if out is None:
                    out = dict(targets)
                out[sym] = 0.0
                self.log(f"[INIT] CG_DIAG_TRADE_BLOCK:{tkr}")
        return out if out is not None else targets

    def _CgDiagGuardStartupLog(self) -> None:
        """[E0.4] One compact startup line."""
        bridge = 1 if getattr(self, "_cg_rrx_bridge_flag", False) else 0
        spyg   = 1 if getattr(self, "_cg_spyg_sat_flag", False) else 0
        c2n = 1 if getattr(self, "dyn_alloc_c2n_trade_enable", False) else 0
        self.log(f"[INIT] CG_DIAG_TRADE_GUARD diag_trade_guard=ON "
                 f"rrx_bridge={bridge} spyg_sat_trade={spyg} dyn_c2n_trade={c2n}")

    def _CgSubscriptionAudit(self) -> None:
        """[E0.1] Per-equity minute/daily/duplicate audit.
        Tradable valid: minute>=1 and daily==0. Signal valid: daily>=1 and minute==0.
        Violations: NO_MINUTE, MIXED_DAILY_MINUTE, DUPLICATE_MINUTE.
        Backtest: raise. Live: log only."""
        eq = {}
        cust = []
        try:
            subs = list(self.subscription_manager.subscriptions)
        except Exception:
            subs = []
        for cfg in subs:
            try:
                tkr = str(cfg.symbol.value)
                st  = cfg.symbol.security_type
                res = cfg.resolution
            except Exception:
                continue
            if st != SecurityType.EQUITY:
                if tkr not in cust:
                    cust.append(tkr)
                continue
            c = eq.setdefault(tkr, [0, 0])
            if res == Resolution.MINUTE:
                c[0] += 1
            elif res == Resolution.DAILY:
                c[1] += 1
        reg = getattr(self, "_cg_sub_registry", {})
        trad = _CG_TRADABLE | set(getattr(self, "_cg_tradable_extra", ()))
        tm, sd, vio = [], [], []
        for tkr in sorted(eq.keys()):
            nmin, nday = eq[tkr]
            rec = reg.get(tkr) or {}
            is_tr = bool(rec.get("tradable", False)) or (tkr in trad)
            rep = f"{tkr}(m={nmin},d={nday})"
            if is_tr:
                tm.append(rep)
                if nmin == 0:
                    vio.append(f"{tkr}:NO_MINUTE")
                if nday > 0:
                    vio.append(f"{tkr}:TRADABLE_HAS_DAILY")
                if nmin > 0 and nday > 0:
                    vio.append(f"{tkr}:MIXED_DAILY_MINUTE")
            else:
                sd.append(rep)
                if nmin > 0 and nday > 0:
                    vio.append(f"{tkr}:MIXED_DAILY_MINUTE")
                if nmin > 0:
                    vio.append(f"{tkr}:SIGNAL_HAS_MINUTE")
                if nday == 0:
                    vio.append(f"{tkr}:NO_DAILY")
        v = "NONE" if not vio else "; ".join(vio)
        self.log("[INIT] CG_SUBSCRIPTION_AUDIT | "
                 f"tradable minute: {', '.join(tm)} | signal daily: {', '.join(sd)} | "
                 f"custom daily: {', '.join(cust)} | violations: {v}")
        if vio and not self.live_mode:
            raise Exception(f"CG_SUBSCRIPTION_AUDIT violations: {v}")
