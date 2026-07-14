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

    def _CgBuildTradableExtra(self) -> None:
        """[E0.1] RRX universe counts as tradable for SUBSCRIPTION only when the
        trade bridge can route orders. Never alters strategy logic."""
        extra = set()
        try:
            ov = getattr(self, "_rrx_param_overrides", {}) or {}
            v = self.get_parameter("rrx_trade_bridge_enable")
            if v is None or str(v).strip() == "":
                v = ov.get("rrx_trade_bridge_enable")
            bridge = str(v or "0").strip().lower() in ("1", "true", "yes", "on")
        except Exception:
            bridge = False
        if bridge:
            try:
                from rr_xsector_diag import RRX_THEMES
                for c in RRX_THEMES.values():
                    extra.add(str(c.get("etf", "")).upper())
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
                if nmin > 0 and nday > 0:
                    vio.append(f"{tkr}:MIXED_DAILY_MINUTE")
                if nmin > 1:
                    vio.append(f"{tkr}:DUPLICATE_MINUTE")
            else:
                sd.append(rep)
                if nmin > 0 and nday > 0:
                    vio.append(f"{tkr}:MIXED_DAILY_MINUTE")
        v = "NONE" if not vio else "; ".join(vio)
        self.log("[INIT] CG_SUBSCRIPTION_AUDIT | "
                 f"tradable minute: {', '.join(tm)} | signal daily: {', '.join(sd)} | "
                 f"custom daily: {', '.join(cust)} | violations: {v}")
        if vio and not self.live_mode:
            raise Exception(f"CG_SUBSCRIPTION_AUDIT violations: {v}")
