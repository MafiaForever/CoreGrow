# region imports
from AlgorithmImports import *
# endregion
# cashflow_live.py
# LFC-2: live external cash-flow handler for deposits/withdrawals.
# No alpha logic. It only protects DD/return state, capital-cap state,
# false emergency state after external cash-flow, and forces a rebalance.

from datetime import date


class LiveCashFlowMixin:

    def _lfc_bool(self, name, default):
        v = self.get_parameter(name)
        if v is None or str(v).strip() == "":
            return bool(default)
        return str(v).strip().lower() in ("1", "true", "yes", "on")

    def _lfc_float(self, name, default):
        try:
            v = self.get_parameter(name)
            if v is None or str(v).strip() == "":
                return float(default)
            return float(v)
        except Exception:
            return float(default)

    def _lfc_date(self, x):
        if x is None:
            return None
        if isinstance(x, date):
            return x
        try:
            return date.fromisoformat(str(x)[:10])
        except Exception:
            return None

    def LiveCashFlowInitialize(self):
        self.lfc_enable = bool(self.live_mode and self._lfc_bool("lfc_enable", 1))
        self.lfc_min_abs = self._lfc_float("lfc_min_abs", 500.0)
        self.lfc_min_pct = self._lfc_float("lfc_min_pct", 0.005)
        self.lfc_cash_confirm_min = self._lfc_float("lfc_cash_confirm_min", 0.50)
        self.lfc_force_rebalance_on_deposit = self._lfc_bool("lfc_force_rebalance_on_deposit", 1)
        self.lfc_reduce_only_on_withdrawal = self._lfc_bool("lfc_reduce_only_on_withdrawal", 1)

        # UNLIMITED = capital_cap remains 0; the full account NAV can be used.
        # FIXED     = capital_cap stays fixed; deposits outside strategy remain unmanaged.
        # AUTO_ADD  = deposits increase capital_cap; withdrawals reduce it.
        # PCT_NAV   = capital_cap = account NAV * lfc_managed_pct.
        # FULL      = strategy uses almost full account NAV, but still leaves a 2% buffer.
        self.lfc_cap_mode = str(self.get_parameter("lfc_cap_mode") or "UNLIMITED").strip().upper()
        self.lfc_managed_pct = self._lfc_float("lfc_managed_pct", 0.10)

        # One-time live state rebase after cash-flow / no-cap cutover.
        # This is not alpha logic. It realigns accounting/DD/emergency state.
        self.lfc_rebase_state_on_start = self._lfc_bool("lfc_rebase_state_on_start", 0)
        self.lfc_rebase_state_date = self._lfc_date(self.get_parameter("lfc_rebase_state_date"))

        self._lfc_last_equity = None
        self._lfc_last_cash = None
        self._lfc_prev_holdings = {}
        self._lfc_total_flow = 0.0
        self._lfc_last_flow_date = None
        self._lfc_last_flow_amount = 0.0
        self._lfc_last_flow_kind = "NONE"
        self._lfc_skip_return_date = None
        self._lfc_force_rebalance = False
        self._lfc_force_reduce = False
        self._lfc_rebase_done_date = None

    def LiveCashFlowSaveFields(self) -> dict:
        if not getattr(self, "lfc_enable", False):
            return {}
        return {
            "last_equity": self._lfc_last_equity,
            "last_cash": self._lfc_last_cash,
            "prev_holdings": dict(getattr(self, "_lfc_prev_holdings", {})),
            "total_flow": float(getattr(self, "_lfc_total_flow", 0.0)),
            "last_flow_date": self._lfc_last_flow_date.isoformat() if getattr(self, "_lfc_last_flow_date", None) else None,
            "last_flow_amount": float(getattr(self, "_lfc_last_flow_amount", 0.0)),
            "last_flow_kind": str(getattr(self, "_lfc_last_flow_kind", "NONE")),
            "skip_return_date": self._lfc_skip_return_date.isoformat() if getattr(self, "_lfc_skip_return_date", None) else None,
            "force_rebalance": bool(getattr(self, "_lfc_force_rebalance", False)),
            "force_reduce": bool(getattr(self, "_lfc_force_reduce", False)),
            "rebase_done_date": self._lfc_rebase_done_date.isoformat() if getattr(self, "_lfc_rebase_done_date", None) else None,
        }

    def LiveCashFlowLoadFields(self, state: dict) -> None:
        d = state.get("lfc", {}) if isinstance(state, dict) else {}
        if not isinstance(d, dict):
            d = {}

        self._lfc_last_equity = d.get("last_equity")
        self._lfc_last_cash = d.get("last_cash")
        try:
            self._lfc_last_equity = float(self._lfc_last_equity) if self._lfc_last_equity is not None else None
            self._lfc_last_cash = float(self._lfc_last_cash) if self._lfc_last_cash is not None else None
        except Exception:
            self._lfc_last_equity = None
            self._lfc_last_cash = None

        self._lfc_prev_holdings = d.get("prev_holdings", {}) or {}
        self._lfc_total_flow = float(d.get("total_flow", 0.0) or 0.0)
        self._lfc_last_flow_date = self._lfc_date(d.get("last_flow_date"))
        self._lfc_last_flow_amount = float(d.get("last_flow_amount", 0.0) or 0.0)
        self._lfc_last_flow_kind = str(d.get("last_flow_kind", "NONE") or "NONE")
        self._lfc_skip_return_date = self._lfc_date(d.get("skip_return_date"))
        self._lfc_force_rebalance = bool(d.get("force_rebalance", False))
        self._lfc_force_reduce = bool(d.get("force_reduce", False))
        self._lfc_rebase_done_date = self._lfc_date(d.get("rebase_done_date"))

    def _LFC_ReconcileHoldings(self) -> None:
        """Log diff between ObjectStore holdings and actual broker holdings on restart."""
        if not getattr(self, "live_mode", False):
            return
        prev = getattr(self, "_lfc_prev_holdings", {}) or {}
        if not prev:
            self.log("[CASHFLOW_RECON] no prior holdings in state -- cold start")
            return
        actual = self._LFC_SnapshotHoldings()
        prev_tickers  = set(prev.keys())
        actual_tickers= set(actual.keys())
        added   = actual_tickers - prev_tickers
        removed = prev_tickers  - actual_tickers
        changed = []
        for t in prev_tickers & actual_tickers:
            q_prev = float(prev[t].get("q", 0.0))
            q_now  = float(actual[t].get("q", 0.0))
            if abs(q_prev - q_now) > 0.5:
                changed.append(f"{t}:{q_prev:.0f}->{q_now:.0f}")
        if added or removed or changed:
            self.log(
                f"[CASHFLOW_RECON] mismatch: "
                f"added={list(added) or 'none'} "
                f"removed={list(removed) or 'none'} "
                f"qty_changed={changed or 'none'}"
            )
        else:
            self.log("[CASHFLOW_RECON] holdings match state -- OK")

    def _LFC_CashValue(self) -> float:
        for attr in ("cash", "Cash"):
            try:
                return float(getattr(self.portfolio, attr))
            except Exception:
                pass
        return 0.0

    def _LFC_Ticker(self, sym) -> str:
        try:
            return str(sym.Value)
        except Exception:
            try:
                return str(sym.value)
            except Exception:
                return str(sym)

    def _LFC_SnapshotHoldings(self) -> dict:
        out = {}
        try:
            for sym, h in self.portfolio.items():
                qty = float(getattr(h, "Quantity", getattr(h, "quantity", 0.0)) or 0.0)
                if abs(qty) <= 1e-8:
                    continue
                sec = self.securities[sym]
                px = float(getattr(sec, "Price", getattr(sec, "price", 0.0)) or 0.0)
                if px <= 0:
                    continue
                out[self._LFC_Ticker(sym)] = {"q": qty, "p": px}
        except Exception as e:
            if getattr(self, "live_mode", False):
                self.log(f"[CASHFLOW] snapshot error: {e}")
        return out

    def _LFC_EstimatedPnL(self) -> float:
        pnl = 0.0
        prev = getattr(self, "_lfc_prev_holdings", {}) or {}
        if not prev:
            return 0.0

        try:
            sym_by_ticker = {}
            for sym in self.securities.keys():
                sym_by_ticker[self._LFC_Ticker(sym)] = sym

            for ticker, x in prev.items():
                sym = sym_by_ticker.get(ticker)
                if sym is None:
                    continue
                q = float(x.get("q", 0.0) or 0.0)
                p0 = float(x.get("p", 0.0) or 0.0)
                p1 = float(getattr(self.securities[sym], "Price", getattr(self.securities[sym], "price", 0.0)) or 0.0)
                if p0 > 0 and p1 > 0:
                    pnl += q * (p1 - p0)
        except Exception as e:
            if getattr(self, "live_mode", False):
                self.log(f"[CASHFLOW] pnl estimate error: {e}")

        return float(pnl)

    def _LFC_AdjustPeaksForFlow(self, flow: float, cur_eq: float, prev_eq: float) -> None:
        old_peak = float(getattr(self, "portfolio_peak", 0.0) or 0.0)

        if old_peak <= 0:
            self.portfolio_peak = max(cur_eq, prev_eq + flow)
            self._peak_initialized = True
            return

        adjusted_peak = old_peak + flow
        adjusted_peak = max(cur_eq, adjusted_peak)

        self.portfolio_peak = max(1.0, float(adjusted_peak))
        self._peak_initialized = True

        local = getattr(self, "_dd_cb_local_peak", None)
        if local is not None:
            try:
                self._dd_cb_local_peak = max(cur_eq, float(local) + flow)
            except Exception:
                pass

    def _LFC_AdjustCapitalCap(self, flow: float, cur_eq: float) -> None:
        if not self.live_mode:
            return

        mode = str(getattr(self, "lfc_cap_mode", "FIXED") or "FIXED").upper()
        old = float(getattr(self, "capital_cap", 0.0) or 0.0)
        new = old

        if mode in ("UNLIMITED", "NO_CAP", "NONE"):
            new = 0.0

        elif mode == "FIXED":
            if flow < 0 and old > 0:
                new = min(old, max(0.0, cur_eq * 0.98))

        elif mode == "AUTO_ADD":
            new = max(0.0, old + flow)

        elif mode == "PCT_NAV":
            new = max(0.0, cur_eq * float(getattr(self, "lfc_managed_pct", 0.10)))

        elif mode in ("FULL", "FULL_ACCOUNT"):
            new = max(0.0, cur_eq * 0.98)

        if abs(new - old) > 1.0:
            self.log(f"[CASHFLOW_CAP] mode={mode} cap {old:.2f}->{new:.2f}")
            self.capital_cap = float(new)

    def _LFC_ShouldRebaseState(self, today) -> bool:
        if not getattr(self, "lfc_rebase_state_on_start", False):
            return False

        target = getattr(self, "lfc_rebase_state_date", None)
        done = getattr(self, "_lfc_rebase_done_date", None)

        if target is not None:
            if today != target:
                return False
            return done != today

        # If no explicit date is provided, allow only one rebase ever for this state.
        return done is None

    def _LFC_RebaseLiveState(self, cur_eq: float, cur_cash: float, context: str) -> None:
        today = self.time.date()
        old_peak = float(getattr(self, "portfolio_peak", 0.0) or 0.0)
        old_prev = getattr(self, "previous_equity", None)
        old_em = bool(getattr(self, "emergency_stop_triggered", False))
        old_liq = bool(getattr(self, "emergency_liquidation_executed", False))

        self.portfolio_peak = max(1.0, float(cur_eq))
        self._peak_initialized = True
        self.previous_equity = float(cur_eq)
        self._last_good_equity = float(cur_eq)
        self._snap_anomaly_active = False

        # Clear false emergency created by stale peak/state mismatch.
        self.emergency_stop_triggered = False
        self.emergency_liquidation_executed = False

        # Reset DD circuit-breaker state to the new live base.
        self._dd_cb_active = False
        self._dd_cb_trigger_date = None
        self._dd_cb_resume_date = None
        self._dd_cb_local_peak = None
        self._dd_cb_dd_at_trigger = None

        # Do not carry old DD history across a live base redefinition.
        self._dd_history = []

        # Sync cash-flow snapshot to the new base.
        self._lfc_last_equity = float(cur_eq)
        self._lfc_last_cash = float(cur_cash)
        self._lfc_prev_holdings = self._LFC_SnapshotHoldings()
        self._lfc_skip_return_date = today
        self._lfc_force_rebalance = True
        self._lfc_force_reduce = False
        self._lfc_rebase_done_date = today

        self.log(
            f"[CASHFLOW] REBASE ctx={context} date={today} "
            f"eq={cur_eq:.2f} cash={cur_cash:.2f} "
            f"peak {old_peak:.2f}->{self.portfolio_peak:.2f} "
            f"prev={old_prev} emergency={int(old_em)} liq={int(old_liq)}"
        )

    def LiveCashFlowCheck(self, context="DAILY") -> bool:
        if not getattr(self, "lfc_enable", False):
            return False
        if not self.live_mode or self.is_warming_up:
            return False

        today = self.time.date()
        cur_eq = float(self.portfolio.total_portfolio_value)
        cur_cash = self._LFC_CashValue()

        if self._LFC_ShouldRebaseState(today):
            self._LFC_RebaseLiveState(cur_eq, cur_cash, context)
            self._LFC_AdjustCapitalCap(0.0, cur_eq)
            return True

        # For PCT_NAV/FULL/UNLIMITED, keep cap synchronized even without new cash-flow.
        if str(getattr(self, "lfc_cap_mode", "UNLIMITED")).upper() in ("UNLIMITED", "NO_CAP", "NONE", "PCT_NAV", "FULL", "FULL_ACCOUNT"):
            self._LFC_AdjustCapitalCap(0.0, cur_eq)

        prev_eq = self._lfc_last_equity
        if prev_eq is None or prev_eq <= 0:
            prev_eq = self.previous_equity if getattr(self, "previous_equity", None) else None

        if prev_eq is None or prev_eq <= 0:
            self._lfc_last_equity = cur_eq
            self._lfc_last_cash = cur_cash
            self._lfc_prev_holdings = self._LFC_SnapshotHoldings()
            return False

        est_pnl = self._LFC_EstimatedPnL()
        raw_delta = cur_eq - float(prev_eq)
        flow = raw_delta - est_pnl
        pct = flow / max(1.0, float(prev_eq))

        cash_delta = cur_cash - float(self._lfc_last_cash if self._lfc_last_cash is not None else cur_cash)
        cash_confirm = abs(cash_delta) >= abs(flow) * float(getattr(self, "lfc_cash_confirm_min", 0.50))

        big = (
            abs(flow) >= float(getattr(self, "lfc_min_abs", 500.0))
            and abs(pct) >= float(getattr(self, "lfc_min_pct", 0.08))
        )

        if not big:
            self._lfc_last_equity = cur_eq
            self._lfc_last_cash = cur_cash
            self._lfc_prev_holdings = self._LFC_SnapshotHoldings()
            return False

        kind = "DEPOSIT" if flow > 0 else "WITHDRAWAL"

        self._lfc_total_flow += flow
        self._lfc_last_flow_date = today
        self._lfc_last_flow_amount = flow
        self._lfc_last_flow_kind = kind
        self._lfc_skip_return_date = today

        # Prevent positive/negative cash-flow from being treated as SNAP anomaly.
        self._snap_anomaly_active = False
        self.previous_equity = cur_eq
        self._last_good_equity = cur_eq

        # Prevent cash-flow from becoming artificial DD / artificial recovery.
        self._LFC_AdjustPeaksForFlow(flow, cur_eq, float(prev_eq))
        self._LFC_AdjustCapitalCap(flow, cur_eq)

        if kind == "DEPOSIT" and getattr(self, "lfc_force_rebalance_on_deposit", True):
            self._lfc_force_rebalance = True
            if not cash_confirm:
                self.log(f"[CASHFLOW] DEPOSIT cash_ok=0 -- rebalance set but verify manually")

        if kind == "WITHDRAWAL" and getattr(self, "lfc_reduce_only_on_withdrawal", True):
            if cash_confirm:
                # [LFC-GATE] require cash movement to confirm withdrawal; blocks false reduce-only
                self._lfc_force_rebalance = True
                self._lfc_force_reduce = True
            else:
                self.log(
                    f"[CASHFLOW] WITHDRAWAL cash_ok=0 -- reduce-only BLOCKED "
                    f"flow={flow:.2f} cash_delta={cash_delta:.2f} -- verify manually"
                )

        self._lfc_last_equity = cur_eq
        self._lfc_last_cash = cur_cash
        self._lfc_prev_holdings = self._LFC_SnapshotHoldings()

        self.log(
            f"[CASHFLOW] {kind} ctx={context} "
            f"flow={flow:.2f} pct={pct:.2%} "
            f"eq {prev_eq:.2f}->{cur_eq:.2f} "
            f"pnl_est={est_pnl:.2f} cash_delta={cash_delta:.2f} "
            f"cash_ok={int(cash_confirm)} "
            f"cap={float(getattr(self,'capital_cap',0.0) or 0.0):.2f}"
        )
        return True

    def LiveCashFlowSkipReturnToday(self) -> bool:
        return bool(getattr(self, "_lfc_skip_return_date", None) == self.time.date())

    def LiveCashFlowConsumeForceFlags(self) -> tuple:
        force_rebalance = bool(getattr(self, "_lfc_force_rebalance", False))
        reduce_only = bool(getattr(self, "_lfc_force_reduce", False))
        self._lfc_force_rebalance = False
        self._lfc_force_reduce = False
        return force_rebalance, reduce_only