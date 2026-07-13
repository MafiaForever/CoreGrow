from AlgorithmImports import *
import json
from datetime import date as _date

RR_SLEEVE_KEY    = "rr_sleeve_state_v1"
RR_VERSION       = "CG_RR_v1"
RR_TRADE_ENABLE  = True   # ← Master switch: False = no orders, diagnostics only


class RideRocketSleeve:
    """Rocket sleeve: targets are sleeve-relative; get_abs_targets scales by sleeve_alloc."""

    def __init__(self, algo, sleeve_alloc: float = 0.34):
        self.algo         = algo
        self.sleeve_alloc = float(sleeve_alloc)
        self.rr_targets: dict = {}
        self.all_syms:   list = []
        self.rr_dirty     = False

    # ── LEAN API proxies ──────────────────────────────────────────────────────
    @property
    def portfolio(self):     return self.algo.portfolio
    @property
    def securities(self):    return self.algo.securities
    @property
    def time(self):          return self.algo.time
    @property
    def live_mode(self):     return self.algo.live_mode
    @property
    def is_warming_up(self): return self.algo.is_warming_up
    @property
    def object_store(self):  return self.algo.object_store

    def log(self, msg):          self.algo.log(f"[RR] {msg}")
    def history(self, *a, **kw): return self.algo.history(*a, **kw)

    def _set(self, symbol, weight: float):
        w = max(0.0, float(weight))
        old = float(self.rr_targets.get(symbol, -1.0))
        if abs(old - w) > 1e-6:
            self.rr_targets[symbol] = w
            self.rr_dirty = True

    def _assign_rr_targets(self, mapping: dict):
        self.rr_targets = {k: max(0.0, float(v)) for k, v in mapping.items()}
        self.rr_dirty = True

    def _liquidate(self, symbol=None):
        if symbol is None:
            for s in self.candidates + [self.usfr]:
                self._set(s, 0.0)
        else:
            self._set(symbol, 0.0)

    def get_abs_targets(self) -> dict:
        """Account-relative targets = sleeve_relative * sleeve_alloc."""
        return {s: float(w) * self.sleeve_alloc
                for s, w in self.rr_targets.items()}

    def _ticker(self, symbol) -> str:
        """Safe symbol -> ticker string, compatible with both LEAN API variants."""
        try:
            return str(symbol.Value)
        except Exception:
            try:
                return str(symbol.value)
            except Exception:
                return str(symbol)

    def _rr_w(self, symbol) -> float:
        """Current sleeve-relative weight of symbol (portfolio-derived)."""
        pv = float(self.portfolio.total_portfolio_value)
        holding = self.portfolio[symbol]
        try:
            hv = float(holding.HoldingsValue)
        except Exception:
            hv = float(getattr(holding, "holdings_value", 0.0))
        return (hv / max(1e-6, pv)) / max(1e-6, self.sleeve_alloc)

    # ── INITIALIZE ────────────────────────────────────────────────────────────
    def rr_init(self):
        """Called from algo.Initialize() after base CG symbols are added."""
        a = self.algo
        tickers = ["MU", "NVDA", "AVGO"]
        self.candidates = [a._CgRegisterEquity(t, tradable=True).symbol for t in tickers]
        self.smh  = a._CgRegisterEquity("SMH").symbol
        self.qqq  = a._CgRegisterEquity("QQQ").symbol
        self.usfr = a._CgRegisterEquity("USFR", tradable=True).symbol
        self.all_syms = self.candidates + [self.smh, self.qqq, self.usfr]

        # ── Parameters (all overridable via get_parameter) ────────────────────
        g  = lambda k, d: float(a.get_parameter(k) or d)
        gb = lambda k, d: bool(int(a.get_parameter(k) or int(d)))

        self.base_exposure = float(a.get_parameter("rr_base_exposure") or 1.0)  # QC Optimizer
        self.max_exposure          = g("rr_max_exposure",    1.00)
        self.add_exposure          = self.max_exposure - self.base_exposure #g("rr_add_exposure",    0.20)
        self.rsi_trim_level        = g("rr_rsi_trim",        87.0)  # matched dyn_rr_rsi_trim
        self.rsi_entry_max         = g("rr_rsi_entry_max",   70.0)  # matched dyn_rr_rsi_entry_max
        self.rsi_add_max           = g("rr_rsi_add_max",     80.0)  # matched dyn_rr_rsi_add_max
        self.chandelier_mult       = g("rr_chandelier_mult",  2.9)  # matched dyn_rr_chandelier
        self.profit_lock_threshold = g("rr_profit_lock",     0.45)
        self.profit_lock_rsi_min   = g("rr_profit_rsi_min",  76.0)  # matched dyn_rr_pl_rsi_min
        self.rotation_threshold    = g("rr_rotation_thr",    0.09)
        self.rr_equity_dd_kill     = gb("rr_equity_dd_kill_enable", 0)
        self.rr_bootstrap_enable   = gb("rr_bootstrap_enable",      0) #!!! 0 !!!
        self.rr_initial_mu_target  = g("rr_initial_mu_target",     0.98)
        # ── RR shock guard C1: trading, no diagnostics ──────────────────────
        self.rr_shock_exit_enable      = gb("rr_shock_exit_enable",      0)  # [RR_SHOCK]
        self.rr_rot_shock_guard_enable = gb("rr_rot_shock_guard_enable", 1)  # [RR_SHOCK]
        self.rr_shock_include_watch    = gb("rr_shock_include_watch",    1)  # [RR_SHOCK]
        self.rr_shock_ma_period        = min(30, max(5, int(g("rr_shock_ma_period", 10))))  # [RR_SHOCK]
        self.rr_rot_shock_spy5_edge    = g("rr_rot_shock_spy5_edge", 0.01)  # [RR_SHOCK]
        self.rr_rot_shock_smh5_edge    = g("rr_rot_shock_smh5_edge", 0.00)  # [RR_SHOCK]
        self.rr_rot_shock_max_rsi      = g("rr_rot_shock_max_rsi",   82.0)  # [RR_SHOCK]
        self.rr_rot_shock_to_cash      = gb("rr_rot_shock_to_cash",     1)  # [RR_SHOCK]
        # RSI+ROC exit confirmation (optional, default OFF)
        self.rr_shock_rsi_confirm      = gb("rr_shock_rsi_confirm",     0)  # [RR_SHOCK]
        self.rr_shock_rsi_min          = g("rr_shock_rsi_min",       55.0)  # [RR_SHOCK]
        self.rr_shock_r5_neg           = g("rr_shock_r5_neg",         0.0)  # [RR_SHOCK]
        # SPY_CUT intraday safety exit
        self.rr_spycut_exit_enable     = gb("rr_spycut_exit_enable",    1)  # [RR_SPYCUT]
        self.rr_spycut_rsi_peak_min    = g("rr_spycut_rsi_peak_min", 75.0)  # [RR_SPYCUT]

        # ── Indicators ────────────────────────────────────────────────────────
        self.sma10  = {s: a.sma(s, self.rr_shock_ma_period, Resolution.DAILY)
                       for s in self.candidates}                            # [RR_SHOCK]
        self.sma50  = {s: a.sma(s, 50,  Resolution.DAILY) for s in self.candidates}
        self.sma200 = {s: a.sma(s, 200, Resolution.DAILY) for s in self.candidates}
        self.rsi14  = {s: a.rsi(s, 14, MovingAverageType.WILDERS, Resolution.DAILY)
                       for s in self.candidates}
        self.atr20  = {s: a.atr(s, 20, MovingAverageType.WILDERS, Resolution.DAILY)
                       for s in self.candidates}
        self.vol20  = {s: a.sma(s, 20, Resolution.DAILY, Field.VOLUME)
                       for s in self.candidates}
        self.smh_sma50  = a.sma(self.smh, 50,  Resolution.DAILY)
        self.smh_sma100 = a.sma(self.smh, 100, Resolution.DAILY)
        self.qqq_sma100 = a.sma(self.qqq, 100, Resolution.DAILY)

        # ── ROC indicators — replaces all history() calls in daily logic ───────
        # Eliminates 15,000+ history() API calls over a 3-year backtest
        self.roc20_cand = {s: a.roc(s, 20, Resolution.DAILY) for s in self.candidates}
        self.roc5_cand  = {s: a.roc(s,  5, Resolution.DAILY) for s in self.candidates}
        self.roc20_smh  = a.roc(self.smh, 20, Resolution.DAILY)
        self.roc5_smh   = a.roc(self.smh,  5, Resolution.DAILY)
        self.roc20_qqq  = a.roc(self.qqq, 20, Resolution.DAILY)

        # ── SPY relative-strength gate  [SPY_GATE] ────────────────────────────
        self.spy = getattr(a, "sym_spy", None)
        if self.spy is None:
            self.spy = a._CgRegisterEquity("SPY").symbol
        self.roc20_spy = a.roc(self.spy, 20, Resolution.DAILY)
        self.roc5_spy  = a.roc(self.spy,  5, Resolution.DAILY)

        self.rr_min_spy_edge20    = g("rr_min_spy_edge20",    0.00)  # 0=off, matches old system
        self.rr_min_spy_edge5     = g("rr_min_spy_edge5",     0.00)
        self.rr_add_spy_edge20    = g("rr_add_spy_edge20",    0.00)  # 0=off
        self.rr_rotate_spy_edge20 = g("rr_rotate_spy_edge20", 0.00)  # 0=off
        self.rr_strong_spy_edge20 = g("rr_strong_spy_edge20", 0.05)  # for future RR_STRONG

        # ── Post-profit rotation tightening  [POST_PROFIT] ───────────────────
        self.last_rr_profit_date  = None
        self.last_rr_profit_pct   = None
        self.rr_post_profit_days  = int(g("rr_post_profit_days",  30))
        self.rr_post_profit_min   = g("rr_post_profit_min",  0.30)
        self.rr_post_profit_edge  = g("rr_post_profit_edge", 0.15)

        # ── Position state ────────────────────────────────────────────────────
        self.held_symbol           = None
        self.trailing_high         = None
        self.avg_entry_price       = None
        self.added                 = False
        self.rr_recent_rsi_peak    = 0.0          # [RR_SPYCUT] tracks overheat
        self.in_reentry_wait       = False
        self.peak_equity           = 0.0
        self.risk_kill_active      = False
        self.last_trade_date       = None
        self.last_liquidation_date = None
        self.last_rotation_date    = None
        self._reconciled           = False

        # ── Bootstrap state ───────────────────────────────────────────────────
        self.rr_bootstrap_pending = False
        self.rr_bootstrap_done    = False

        # ── Emergency/pause live-control params ───────────────────────────────
        self.emergency_liquidate = 0
        self.pause_entries       = 0

        # ── Default target: park in USFR ─────────────────────────────────────
        self.rr_targets = {self.usfr: 1.0}

        # ── Load live state (or set bootstrap_pending on cold start) ──────────
        self._rr_load()

        self.log(
            f"[INIT] alloc={self.sleeve_alloc:.2f} "
            f"bootstrap_pending={self.rr_bootstrap_pending} "
            f"bootstrap_done={self.rr_bootstrap_done} "
            f"held={self._ticker(self.held_symbol) if self.held_symbol else 'None'}"
        )

    # ── STATE SAVE ────────────────────────────────────────────────────────────
    def _rr_save(self):
        if not self.live_mode:
            return
        try:
            ds = lambda dt: str(dt) if dt else None
            state = {
                "version":               RR_VERSION,
                "held_symbol":           self._ticker(self.held_symbol) if self.held_symbol else None,
                "avg_entry_price":       self.avg_entry_price,
                "trailing_high":         self.trailing_high,
                "added":                 self.added,
                "in_reentry_wait":       self.in_reentry_wait,
                "peak_equity":           self.peak_equity,
                "risk_kill_active":      self.risk_kill_active,
                "last_trade_date":       ds(self.last_trade_date),
                "last_liquidation_date": ds(self.last_liquidation_date),
                "last_rotation_date":    ds(self.last_rotation_date),
                "last_rr_profit_date":   ds(self.last_rr_profit_date),   # [POST_PROFIT]
                "last_rr_profit_pct":    self.last_rr_profit_pct,        # [POST_PROFIT]
                "rr_bootstrap_done":     self.rr_bootstrap_done,
                "rr_bootstrap_pending":  self.rr_bootstrap_pending,
                "rr_targets":            {self._ticker(s): float(w) for s, w in self.rr_targets.items()},
                "save_date":             str(self.time.date()),
            }
            self.object_store.save(RR_SLEEVE_KEY, json.dumps(state))
            self.log(
                f"[STATE_SAVE] held={state['held_symbol']} "
                f"done={self.rr_bootstrap_done} pending={self.rr_bootstrap_pending}"
            )
        except Exception as e:
            self.log(f"[STATE_SAVE_ERROR] {e}")

    # ── STATE LOAD ────────────────────────────────────────────────────────────
    def _rr_load(self):
        """Load sleeve state from ObjectStore. Sets bootstrap_pending on cold start."""
        if not self.live_mode:
            # Backtest: if bootstrap enabled, pre-stage MU target immediately
            if self.rr_bootstrap_enable:
                self.rr_bootstrap_pending = True
                mu = next((s for s in self.candidates if self._ticker(s) == "MU"),
                          self.candidates[0])
                rem = max(0.0, 1.0 - self.rr_initial_mu_target)
                self.rr_targets = {mu: self.rr_initial_mu_target,
                                   self.usfr: rem}
            return
        try:
            if not self.object_store.contains_key(RR_SLEEVE_KEY):
                self.log("[STATE_LOAD] No state found -- cold start.")
                if self.rr_bootstrap_enable:
                    self.rr_bootstrap_pending = True
                return

            state = json.loads(self.object_store.read(RR_SLEEVE_KEY))

            saved_ticker = state.get("held_symbol")
            if saved_ticker:
                matched = next(
                    (s for s in self.candidates if self._ticker(s) == saved_ticker), None)
                if matched:
                    self.held_symbol = matched
                else:
                    self.log(f"[STATE_LOAD_WARN] {saved_ticker} not in candidates -- ignored")

            def _pd(s):
                if not s: return None
                try:    return _date.fromisoformat(str(s)[:10])
                except: return None

            self.avg_entry_price       = (float(state["avg_entry_price"])
                                           if state.get("avg_entry_price") is not None else None)
            self.trailing_high         = (float(state["trailing_high"])
                                           if state.get("trailing_high") is not None else None)
            self.added                 = bool(state.get("added", False))
            self.in_reentry_wait       = bool(state.get("in_reentry_wait", False))
            self.peak_equity           = float(state.get("peak_equity", 0.0))
            self.risk_kill_active      = bool(state.get("risk_kill_active", False))
            self.rr_bootstrap_done     = bool(state.get("rr_bootstrap_done", False))
            self.rr_bootstrap_pending  = bool(state.get("rr_bootstrap_pending", False))
            self.last_trade_date       = _pd(state.get("last_trade_date"))
            self.last_liquidation_date = _pd(state.get("last_liquidation_date"))
            self.last_rotation_date    = _pd(state.get("last_rotation_date"))
            self.last_rr_profit_date   = _pd(state.get("last_rr_profit_date"))   # [POST_PROFIT]
            try:                                                                   # [POST_PROFIT]
                raw_pp = state.get("last_rr_profit_pct")
                self.last_rr_profit_pct = None if raw_pp is None else float(raw_pp)
            except Exception:
                self.last_rr_profit_pct = None

            raw_tt = state.get("rr_targets")
            restored_tt = {}
            if isinstance(raw_tt, dict) and raw_tt:
                for tick, w in raw_tt.items():
                    # [FIX] use _ticker() for robust Symbol matching (live: .Value; some: .value)
                    sym = next((s for s in self.all_syms if self._ticker(s) == str(tick)), None)
                    if sym is not None:
                        restored_tt[sym] = float(w)
            if restored_tt:
                self.rr_targets = restored_tt
            elif self.held_symbol and not self.rr_bootstrap_pending:
                try:
                    inv = self.portfolio[self.held_symbol].Invested
                except Exception:
                    inv = False
                if inv:
                    actual_w = self._rr_w(self.held_symbol)
                    if self.rr_bootstrap_done:
                        held_w = max(self.base_exposure, min(1.0, actual_w))
                    else:
                        held_w = max(self.base_exposure, min(self.max_exposure, actual_w))
                else:
                    held_w = float(self.base_exposure)
                self.rr_targets = {
                    self.held_symbol: held_w,
                    self.usfr: max(0.0, 1.0 - held_w),
                }
            else:
                self.rr_targets = {self.usfr: 1.0}

            self.log(
                f"[STATE_LOAD] held={saved_ticker} "
                f"done={self.rr_bootstrap_done} pending={self.rr_bootstrap_pending} "
                f"kill={self.risk_kill_active}"
            )
        except Exception as e:
            self.log(f"[STATE_LOAD_ERROR] {e} -- cold start")
            if self.rr_bootstrap_enable:
                self.rr_bootstrap_pending = True

    # ── BOOTSTRAP FILL HANDLER ────────────────────────────────────────────────
    def _rr_on_fill(self, order_event):
        """Called from main.py OnOrderEvent. Confirms MU bootstrap fill."""
        if not self.rr_bootstrap_pending or self.rr_bootstrap_done:
            return
        status = str(order_event.status).lower()
        if "filled" not in status or "partial" in status:
            return
        mu_sym = next((s for s in self.candidates if self._ticker(s) == "MU"), None)
        if mu_sym is None or order_event.symbol != mu_sym:
            return
        mu_w = self._rr_w(mu_sym)
        if mu_w >= 0.90 * self.rr_initial_mu_target:
            fp = float(order_event.fill_price)
            self.rr_bootstrap_done    = True
            self.rr_bootstrap_pending = False
            self.held_symbol          = mu_sym
            self.avg_entry_price      = fp
            self.trailing_high        = fp
            self.added                = True   # full entry already exceeds normal max
            self.in_reentry_wait      = False
            rem = max(0.0, 1.0 - self.rr_initial_mu_target)
            m = {mu_sym: self.rr_initial_mu_target}
            if rem > 0.001:
                m[self.usfr] = rem
            self._assign_rr_targets(m)
            self._rr_save()
            self.log(
                f"[BOOTSTRAP_FILL] MU fill_px={fp:.2f} "
                f"rr_w={mu_w:.3f} bootstrap_done=True"
            )

    # ── RECONCILE ────────────────────────────────────────────────────────────
    def _reconcile(self):
        """Portfolio wins over ObjectStore state on startup."""
        actual = next(
            (s for s in self.candidates if self.portfolio[s].invested), None)
        if actual is None and self.held_symbol is not None:
            self.log(
                f"[RECONCILE] No position; state had "
                f"{self._ticker(self.held_symbol)} -- resetting")
            self._reset_state()
        elif actual is not None:
            if self.held_symbol != actual:
                self.log(
                    f"[RECONCILE] Portfolio={self._ticker(actual)}; state="
                    f"{self._ticker(self.held_symbol) if self.held_symbol else 'None'} "
                    f"-- portfolio wins")
                self.held_symbol     = actual
                self.avg_entry_price = (self.avg_entry_price
                                        or float(self.securities[actual].price))
                self.trailing_high   = (self.trailing_high
                                        or float(self.securities[actual].price))
        if self.held_symbol:
            actual_w = self._rr_w(self.held_symbol)
            if self.rr_bootstrap_done:
                held_w = max(self.base_exposure, min(1.0, actual_w))
            else:
                held_w = max(self.base_exposure, min(self.max_exposure, actual_w))
            self._assign_rr_targets({
                self.held_symbol: held_w,
                self.usfr: max(0.0, 1.0 - held_w),
            })
        else:
            self._assign_rr_targets({self.usfr: 1.0})
        self.log(
            f"[RECONCILE_DONE] "
            f"held={self._ticker(self.held_symbol) if self.held_symbol else 'None'}")

    # ── MAIN DAILY LOGIC ─────────────────────────────────────────────────────
    def _rr_trade_logic(self) -> bool:
        if self.is_warming_up or not self._ready():
            return False
        self.rr_dirty = False
        self._return_cache: dict = {}  # per-day cache: eliminates redundant history() calls
        a = self.algo
        self.emergency_liquidate = int(a.get_parameter("rr_emergency_liquidate") or 0)
        self.pause_entries       = int(a.get_parameter("rr_pause_entries") or 0)

        if self.emergency_liquidate:
            self.log("[EMERGENCY_LIQ] triggered via rr_emergency_liquidate")
            self._liquidate()
            self._set(self.usfr, 1.0)
            self._reset_state()
            self._rr_save()
            return True

        if self.rr_equity_dd_kill and self.live_mode:
            cur_eq = float(self.portfolio.total_portfolio_value)
            self.peak_equity = max(self.peak_equity, cur_eq)
            dd = (self.peak_equity - cur_eq) / max(1e-6, self.peak_equity)
            if dd > 0.15 and not self.risk_kill_active:
                self.log(f"[RISK_KILL] DD={dd:.1%} -- liquidating Rocket sleeve")
                self._liquidate()
                self._set(self.usfr, 1.0)
                self._reset_state()
                self.risk_kill_active = True
                self._rr_save()
                return True
            if self.risk_kill_active:
                return False

        if self.rr_bootstrap_pending and not self.rr_bootstrap_done:
            invested_cand = next(
                (s for s in self.candidates if self.portfolio[s].invested), None)
            if invested_cand is None:
                mu = next((s for s in self.candidates if self._ticker(s) == "MU"),
                          self.candidates[0])
                rem = max(0.0, 1.0 - self.rr_initial_mu_target)
                self._set(mu, self.rr_initial_mu_target)
                self._set(self.usfr, rem)
                self.log(
                    f"[BOOTSTRAP] MU rr_w={self.rr_initial_mu_target:.2f} USFR={rem:.4f}"
                )
                return self.rr_dirty
            self.rr_bootstrap_done    = True
            self.rr_bootstrap_pending = False
            self.held_symbol          = invested_cand
            if not self.avg_entry_price:
                self.avg_entry_price = float(self.securities[invested_cand].price)
            if not self.trailing_high:
                self.trailing_high = float(self.securities[invested_cand].price)
            self.added = True
            self._rr_save()
            self.log(
                f"[BOOTSTRAP_AUTO_CONFIRM] {self._ticker(invested_cand)} "
                f"avg={self.avg_entry_price:.2f}"
            )

        if not self._reconciled:
            self._reconcile()
            self._reconciled = True

        if not self._sanity_ok():
            return self.rr_dirty

        smh_px = float(self.securities[self.smh].price)
        qqq_px = float(self.securities[self.qqq].price)

        if self.held_symbol and not self.portfolio[self.held_symbol].invested:
            self.log(f"[RECONCILE] {self._ticker(self.held_symbol)} gone externally -- reset")
            self._reset_state()
            self._assign_rr_targets({self.usfr: 1.0})

        invested = (self.held_symbol is not None
                    and self.portfolio[self.held_symbol].invested)

        if invested:
            price     = float(self.securities[self.held_symbol].price)
            mu_weight = self._rr_w(self.held_symbol)
            self.trailing_high = max(self.trailing_high or price, price)
            if self.trailing_high is None:
                self.trailing_high = price
            try:                                   # [RR_SPYCUT] track RSI peak daily
                if self.rsi14[self.held_symbol].IsReady:
                    self.rr_recent_rsi_peak = max(
                        float(getattr(self, "rr_recent_rsi_peak", 0.0)),
                        float(self.rsi14[self.held_symbol].Current.Value))
            except Exception:
                pass
            chandelier_stop = (
                float(self.trailing_high)
                - self.chandelier_mult
                * float(self.atr20[self.held_symbol].Current.Value)
            )

            if self._should_liquidate(self.held_symbol, smh_px, qqq_px, chandelier_stop):
                reason = self._liq_reason(            # [EXIT_LOG]
                    self.held_symbol, smh_px, qqq_px, chandelier_stop) or "UNKNOWN"
                self.log(
                    f"[LIQ] {self._ticker(self.held_symbol)} {reason}")
                self._record_rr_profit_if_any(self.held_symbol, price)  # [POST_PROFIT]
                self._liquidate(self.held_symbol)
                self._set(self.usfr, 1.0)
                self.last_liquidation_date = self.time.date()
                self._reset_state()
                self.in_reentry_wait = True
                self._rr_save()
                return self.rr_dirty

            if smh_px < self.smh_sma50.Current.Value and mu_weight > self.base_exposure:
                new_w = self.base_exposure
                self.log(                             # [EXIT_LOG]
                    f"[TRIM] {self._ticker(self.held_symbol)} SECTOR_WEAK "
                    f"smh={smh_px:.2f}<sma50={float(self.smh_sma50.Current.Value):.2f} "
                    f"rr_w={mu_weight:.2f}->{new_w:.2f}")
                self._set(self.held_symbol, new_w)
                self._set(self.usfr, max(0.0, 1.0 - new_w))
                self._rr_save()
                return self.rr_dirty

            if (mu_weight > self.base_exposure
                    and self.avg_entry_price
                    and float(self.avg_entry_price) > 0):
                unr     = price / float(self.avg_entry_price) - 1.0
                rsi_now = float(self.rsi14[self.held_symbol].Current.Value)
                if unr > self.profit_lock_threshold and rsi_now > self.profit_lock_rsi_min:
                    self.log(                         # [EXIT_LOG]
                        f"[TRIM] {self._ticker(self.held_symbol)} PROFIT_LOCK "
                        f"unr={unr:.1%} RSI={rsi_now:.1f} "
                        f"rr_w={mu_weight:.2f}->{self.base_exposure:.2f}")
                    self.last_rr_profit_date = self.time.date()   # [POST_PROFIT] lock = profit
                    self.last_rr_profit_pct  = float(unr)
                    self._set(self.held_symbol, self.base_exposure)
                    self._set(self.usfr, max(0.0, 1.0 - self.base_exposure))
                    self._rr_save()
                    return self.rr_dirty

            rsi_now = float(self.rsi14[self.held_symbol].Current.Value)
            if rsi_now > self.rsi_trim_level and mu_weight > self.base_exposure:
                new_w = max(self.base_exposure, mu_weight - 0.20)
                self.log(                             # [EXIT_LOG]
                    f"[TRIM] {self._ticker(self.held_symbol)} RSI_OVERHEAT "
                    f"RSI={rsi_now:.1f}>{self.rsi_trim_level:.0f} "
                    f"rr_w={mu_weight:.2f}->{new_w:.2f}")
                self._set(self.held_symbol, new_w)
                self._set(self.usfr, max(0.0, 1.0 - new_w))
                self._rr_save()
                return self.rr_dirty

            if not self.added and self._can_add(self.held_symbol, price, chandelier_stop):
                target = min(self.max_exposure, mu_weight + self.add_exposure)
                self.log(
                    f"[ADD_ON] {self._ticker(self.held_symbol)} "
                    f"rr_w={mu_weight:.2f} -> {target:.2f}")
                self._set(self.held_symbol, target)
                self._set(self.usfr, max(0.0, 1.0 - target))
                self.added = True
                self._rr_save()
                return self.rr_dirty

            leader, leader_score = self._top_leader()
            if (leader is not None
                    and leader != self.held_symbol
                    and self._sym_ready(leader)):
                held_score  = self._rocket_score(self.held_symbol)
                leader_edge = leader_score - held_score

                edge_required = float(self.rotation_threshold)
                in_pp = self._post_profit_rotation_active()        # [POST_PROFIT]
                if in_pp:
                    edge_required = max(edge_required, float(self.rr_post_profit_edge))

                spy_ok = self._beats_spy(leader, self.rr_rotate_spy_edge20, 0.0)  # [SPY_GATE]
                spy_e  = self._spy_edge(leader)

                if leader_edge > edge_required and spy_ok:
                    # [RR-ROT-SHOCK-C1] Block rotation during confirmed market shock
                    # if new leader fails strict short-term quality gate.
                    if self._rr_rotation_blocked_by_shock(leader):
                        if bool(self.rr_rot_shock_to_cash):
                            self._record_rr_profit_if_any(self.held_symbol,
                                float(self.securities[self.held_symbol].price))
                            self._liquidate(self.held_symbol)
                            self._set(self.usfr, 1.0)
                            self.last_liquidation_date = self.time.date()
                            self._reset_state()
                            self.in_reentry_wait = True
                            self._rr_save()
                            return self.rr_dirty
                        return self.rr_dirty
                    self.log(
                        f"[ROTATE] {self._ticker(self.held_symbol)}->"
                        f"{self._ticker(leader)} "
                        f"edge={leader_edge:.3f} req={edge_required:.3f} "
                        f"spy20={float(spy_e.get('edge20', 0.0)):.3f}"
                    )
                    self._liquidate(self.held_symbol)
                    self.last_rotation_date = self.time.date()
                    self._reset_state()
                    self._enter(leader)
                    self._rr_save()
                else:
                    if leader_edge > self.rotation_threshold and not spy_ok:
                        self.log(
                            f"[ROT_BLOCK_SPY] {self._ticker(leader)} "
                            f"edge={leader_edge:.3f} "
                            f"spy20={float(spy_e.get('edge20', 0.0)):.3f}"
                            f"<need={self.rr_rotate_spy_edge20:.2f}"
                        )
                    elif leader_edge > self.rotation_threshold and in_pp:
                        self.log(
                            f"[ROT_BLOCK_POST_PROFIT] {self._ticker(leader)} "
                            f"edge={leader_edge:.3f} req={edge_required:.3f} "
                            f"profit={self.last_rr_profit_pct:.1%}"
                        )
            return self.rr_dirty

        if self.pause_entries or self.risk_kill_active:
            return self.rr_dirty
        leader, _ = self._top_leader()
        if leader is not None and self._sym_ready(leader):
            # [RR-SHOCK-C1] Block fresh entry / reentry during confirmed shock
            # unless new leader passes strict short-term quality gate.
            if (
                bool(self.rr_rot_shock_guard_enable)
                and self._rr_market_short_shock()
                and not self._rr_strict_new_leader_quality(leader)
            ):
                return self.rr_dirty
            hype    = self._hype_on(leader)
            reentry = self._reentry_signal(leader)
            if hype or reentry:
                reason = "HYPE_ON" if hype else "REENTRY"
                self.log(
                    f"[ENTRY] {self._ticker(leader)} {reason} "
                    f"px={float(self.securities[leader].price):.2f}")
                self._enter(leader)
                self.last_trade_date = self.time.date()
                self._rr_save()
        return self.rr_dirty

    # ── ENTRY HELPER ─────────────────────────────────────────────────────────
    def _enter(self, symbol):
        price = float(self.securities[symbol].price)
        target = float(self.base_exposure)
        self._set(symbol, target)
        self._set(self.usfr, max(0.0, 1.0 - target))
        self.held_symbol        = symbol
        self.avg_entry_price    = price
        self.trailing_high      = price
        self.added              = False
        self.in_reentry_wait    = False
        try:                                       # [RR_SPYCUT] seed peak at entry
            if self.rsi14[symbol].IsReady:
                self.rr_recent_rsi_peak = float(self.rsi14[symbol].Current.Value)
        except Exception:
            self.rr_recent_rsi_peak = 0.0

    def _reset_state(self):
        self.held_symbol        = None
        self.trailing_high      = None
        self.avg_entry_price    = None
        self.added              = False
        self.rr_recent_rsi_peak = 0.0             # [RR_SPYCUT]

    # ── READINESS / SANITY ───────────────────────────────────────────────────
    def _sym_ready(self, s) -> bool:
        return (
            self.sma50[s].IsReady
            and self.sma200[s].IsReady
            and self.rsi14[s].IsReady
            and self.atr20[s].IsReady
            and self.vol20[s].IsReady
            and self.roc20_cand[s].IsReady
            and self.roc5_cand[s].IsReady
        )

    def _ready(self) -> bool:
        return (
            self.smh_sma50.IsReady
            and self.smh_sma100.IsReady
            and self.qqq_sma100.IsReady
            and self.roc20_smh.IsReady
            and self.roc20_qqq.IsReady
            and self.roc5_smh.IsReady
            and self.roc20_spy.IsReady  # [SPY_GATE]
            and self.roc5_spy.IsReady   # [SPY_GATE]
            and any(self._sym_ready(s) for s in self.candidates)
        )

    def _sanity_ok(self) -> bool:
        for sym in [self.smh, self.qqq, self.spy]:  # [SPY_GATE]
            if float(self.securities[sym].price) <= 0:
                self.log(f"[SANITY_FAIL] {sym} px<=0"); return False
        for sym in self.candidates:
            if float(self.securities[sym].price) <= 0:
                self.log(f"[SANITY_FAIL] {self._ticker(sym)} px<=0"); return False
        return True

    # ── SIGNAL HELPERS ───────────────────────────────────────────────────────
    def _return_n(self, symbol, days) -> float:
        key = (id(symbol), days)
        cache = getattr(self, "_return_cache", None)
        if cache is not None and key in cache:
            return cache[key]
        hist = self.history(symbol, days + 1, Resolution.DAILY)
        if hist.empty or len(hist) < days + 1:
            result = 0.0
        else:
            closes = hist["close"]
            first  = float(closes.iloc[0])
            result = 0.0 if first == 0 else float(closes.iloc[-1]) / first - 1.0
        if cache is not None:
            cache[key] = result
        return result

    def _spy_edge(self, sym) -> dict:  # [SPY_GATE]
        """Return SPY-relative edge metrics for sym. Always returns a dict."""
        try:
            if not (self.roc20_cand[sym].IsReady and self.roc5_cand[sym].IsReady
                    and self.roc20_spy.IsReady and self.roc5_spy.IsReady):
                return {"ready": False, "edge20": 0.0, "edge5": 0.0}
            r20   = float(self.roc20_cand[sym].Current.Value)
            r5    = float(self.roc5_cand[sym].Current.Value)
            spy20 = float(self.roc20_spy.Current.Value)
            spy5  = float(self.roc5_spy.Current.Value)
            return {
                "ready":  True,
                "edge20": float(r20 - spy20),
                "edge5":  float(r5  - spy5),
                "r20":    float(r20),
                "spy20":  float(spy20),
            }
        except Exception:
            return {"ready": False, "edge20": 0.0, "edge5": 0.0}

    def _beats_spy(self, sym, edge20: float, edge5: float = 0.0) -> bool:  # [SPY_GATE]
        """True iff sym outperforms SPY by at least edge20 (20-day) and edge5 (5-day)."""
        e = self._spy_edge(sym)
        if not e["ready"]:
            return False
        return bool(e["edge20"] >= edge20 and e["edge5"] >= edge5)

    def _rr_market_short_shock(self) -> bool:                               # [RR_SHOCK]
        """Confirmed short market shock from CG/IDS. No logging."""
        try:
            a = self.algo
            if bool(getattr(a, "short_shock_flag", False)): return True
            ids = str(getattr(a, "_ids_state",   "NORMAL")).upper()
            ps  = str(getattr(a, "_panic_state", "NORMAL")).upper()
            if ids in ("STRESS", "PANIC_SHORT", "PANIC"):   return True
            if ps  in ("STRESS", "PANIC"):                  return True
            if bool(self.rr_shock_include_watch):
                if ids == "WATCH" or ps == "WATCH":         return True
            return False
        except Exception:
            return False

    def _rr_below_fast_ma(self, sym) -> bool:                              # [RR_SHOCK]
        try:
            if not self.sma10[sym].IsReady:
                return False
            price = float(self.securities[sym].price)
            ma    = float(self.sma10[sym].Current.Value)
            return bool(ma > 0 and price < ma)
        except Exception:
            return False

    def _rr_shock_confirmed(self, sym) -> bool:                            # [RR_SHOCK]
        """Optional RSI + ROC5 confirmation for shock exit.
        When disabled (rr_shock_rsi_confirm=0): always True — does not block exit.
        When enabled: RSI must be above rr_shock_rsi_min (asset was overheated)
        AND 5-day return must be below rr_shock_r5_neg (short-term decline started).
        Fail-closed: _sym_ready() already guarantees these indicators are ready
        for any held candidate, so not-ready is a bug signal, not a normal state.
        """
        if not bool(self.rr_shock_rsi_confirm):
            return True
        try:
            if not self.rsi14[sym].IsReady or not self.roc5_cand[sym].IsReady:
                return False  # unexpected: _sym_ready() should have caught this
            rsi = float(self.rsi14[sym].Current.Value)
            r5  = float(self.roc5_cand[sym].Current.Value)
            return bool(
                rsi > float(self.rr_shock_rsi_min)
                and r5  < float(self.rr_shock_r5_neg)
            )
        except Exception:
            return False  # fail-closed: don't silently bypass the filter

    def _rr_strict_new_leader_quality(self, sym) -> bool:                  # [RR_SHOCK]
        """Extra quality gate for entering into a new RR leader during shock."""
        try:
            if not self._sym_ready(sym): return False
            price = float(self.securities[sym].price)
            ma    = float(self.sma10[sym].Current.Value)
            r5    = float(self.roc5_cand[sym].Current.Value)
            spy5  = float(self.roc5_spy.Current.Value)
            smh5  = float(self.roc5_smh.Current.Value)
            rsi   = float(self.rsi14[sym].Current.Value)
            return bool(
                ma > 0
                and price > ma
                and r5 > 0.0
                and r5 >= spy5 + float(self.rr_rot_shock_spy5_edge)
                and r5 >= smh5 + float(self.rr_rot_shock_smh5_edge)
                and rsi <= float(self.rr_rot_shock_max_rsi)
            )
        except Exception:
            return False

    def _rr_rotation_blocked_by_shock(self, new_leader) -> bool:           # [RR_SHOCK]
        try:
            if not bool(self.rr_rot_shock_guard_enable): return False
            if not self._rr_market_short_shock():         return False
            return not self._rr_strict_new_leader_quality(new_leader)
        except Exception:
            return False

    def rr_spycut_safety_exit(self, ids_state: str) -> bool:              # [RR_SPYCUT]
        """Intraday safety exit triggered by SPY_CUT event in sh_hedge.py.
        Exits RR leader to USFR when IDS is in confirmed stress/panic
        AND the held leader was recently overheated (rr_recent_rsi_peak >= threshold).
        Executes orders immediately via set_holdings; state updated for next daily cycle.
        Returns True if exit was executed.
        """
        if not bool(self.rr_spycut_exit_enable):
            return False
        if self.is_warming_up:
            return False
        if self.held_symbol is None:
            return False
        if not self.portfolio[self.held_symbol].invested:
            return False
        st = str(ids_state or "NORMAL").upper()
        if st not in ("STRESS", "PANIC_SHORT", "PANIC"):
            return False
        if float(getattr(self, "rr_recent_rsi_peak", 0.0)) < float(self.rr_spycut_rsi_peak_min):
            return False
        sym   = self.held_symbol
        price = float(self.securities[sym].price)
        self.log(
            f"[RR_SPYCUT_EXIT] {self._ticker(sym)} "
            f"ids={st} rsi_peak={self.rr_recent_rsi_peak:.1f} px={price:.2f}")
        self._record_rr_profit_if_any(sym, price)
        self._liquidate(sym)
        self._set(self.usfr, 1.0)
        self.last_liquidation_date = self.time.date()
        self._reset_state()
        self.in_reentry_wait = True
        self._rr_save()
        # Intraday execution: sell leader immediately, USFR handled next daily cycle
        try:
            targets = [PortfolioTarget(sym, 0.0)]
            usfr_sec = self.securities[self.usfr]
            if usfr_sec.HasData and float(usfr_sec.price) > 0:
                targets.append(PortfolioTarget(self.usfr, float(self.sleeve_alloc)))
            self.algo.set_holdings(targets)
        except Exception as e:
            self.log(f"[RR_SPYCUT_EXIT] set_holdings failed: {e}")
        return True

    def _post_profit_rotation_active(self) -> bool:  # [POST_PROFIT]
        """True if we are within rr_post_profit_days of a large profitable exit."""
        try:
            if self.last_rr_profit_date is None or self.last_rr_profit_pct is None:
                return False
            days = (self.time.date() - self.last_rr_profit_date).days
            return (
                0 <= days <= int(self.rr_post_profit_days)
                and float(self.last_rr_profit_pct) >= float(self.rr_post_profit_min)
            )
        except Exception:
            return False

    def _record_rr_profit_if_any(self, sym, exit_price: float) -> None:  # [POST_PROFIT]
        """Record a profitable exit so rotation gate can tighten for rr_post_profit_days."""
        try:
            if self.avg_entry_price is None or float(self.avg_entry_price) <= 0:
                return
            pnl_pct = float(exit_price) / float(self.avg_entry_price) - 1.0
            if pnl_pct > 0:
                self.last_rr_profit_date = self.time.date()
                self.last_rr_profit_pct  = float(pnl_pct)
                self.log(
                    f"[RR_PROFIT_STATE] {self._ticker(sym)} "
                    f"pnl={pnl_pct:.1%} date={self.last_rr_profit_date}"
                )
        except Exception:
            pass

    def _rocket_score(self, symbol) -> float:
        if not self._sym_ready(symbol):
            return -999.0
        price = float(self.securities[symbol].price)
        s50   = float(self.sma50[symbol].Current.Value)
        s200  = float(self.sma200[symbol].Current.Value)
        trend = 1.0 if (price > s50 and s50 > s200) else (0.5 if price > s50 else 0.0)
        r20   = float(self.roc20_cand[symbol].Current.Value)
        q20   = float(self.roc20_qqq.Current.Value)
        m20   = float(self.roc20_smh.Current.Value)
        s20   = float(self.roc20_spy.Current.Value)          # [SPY_GATE]
        rs_q  = max(-1.0, min(1.0, (r20 - q20) / 0.30))
        rs_m  = max(-1.0, min(1.0, (r20 - m20) / 0.30))
        rs_s  = max(-1.0, min(1.0, (r20 - s20) / 0.30))    # [SPY_GATE]
        adv   = float(self.vol20[symbol].Current.Value)
        vol_s = (min(float(self.securities[symbol].volume) / adv, 2.0) / 2.0
                 if adv > 0 else 0.0)
        rsi_s = max(0.0, 1.0 - abs(float(self.rsi14[symbol].Current.Value) - 65.0) / 35.0)
        # [SPY_GATE] SPY rs gets 15%; QQQ/SMH each drop 5 pp vs original weights
        return 0.25*rs_q + 0.25*rs_m + 0.15*rs_s + 0.20*trend + 0.10*vol_s + 0.05*rsi_s

    def _top_leader(self):
        best, score = None, -999.0
        for s in self.candidates:
            if not self._sym_ready(s): continue
            sc = self._rocket_score(s)
            if sc > score: score, best = sc, s
        return best, score

    def _hype_on(self, sym) -> bool:
        price = float(self.securities[sym].price)
        smhp  = float(self.securities[self.smh].price)
        qqqp  = float(self.securities[self.qqq].price)
        r20   = float(self.roc20_cand[sym].Current.Value)
        smh20 = float(self.roc20_smh.Current.Value)
        qqq20 = float(self.roc20_qqq.Current.Value)
        return (
            price > float(self.sma50[sym].Current.Value)
            and price > float(self.sma200[sym].Current.Value)
            and float(self.sma50[sym].Current.Value) > float(self.sma200[sym].Current.Value)
            and smhp > float(self.smh_sma50.Current.Value)
            and qqqp > float(self.qqq_sma100.Current.Value)
            and r20  > smh20 and r20 > qqq20
            and self._beats_spy(sym, self.rr_min_spy_edge20, self.rr_min_spy_edge5)  # [SPY_GATE]
            and float(self.securities[sym].volume) > 1.4 * float(self.vol20[sym].Current.Value)
            and float(self.rsi14[sym].Current.Value) < self.rsi_entry_max
        )

    def _reentry_signal(self, sym) -> bool:
        if not self.in_reentry_wait:
            return False
        price = float(self.securities[sym].price)
        smhp  = float(self.securities[self.smh].price)
        r5    = float(self.roc5_cand[sym].Current.Value)
        smh5  = float(self.roc5_smh.Current.Value)
        return (
            price > float(self.sma50[sym].Current.Value)
            and smhp > float(self.smh_sma50.Current.Value)
            and smhp > float(self.smh_sma100.Current.Value)
            and r5   > smh5 + 0.03
            and self._beats_spy(sym, self.rr_min_spy_edge20, 0.0)  # [SPY_GATE]
            and float(self.securities[sym].volume) > 1.0 * float(self.vol20[sym].Current.Value)
            and float(self.rsi14[sym].Current.Value) < 88.0
        )

    def _can_add(self, sym, price, chandelier_stop) -> bool:
        if self.avg_entry_price is None or chandelier_stop is None:
            return False
        return (
            price > float(self.avg_entry_price) * 1.10
            and price > chandelier_stop
            and float(self.securities[self.smh].price) > float(self.smh_sma50.Current.Value)
            and self._beats_spy(sym, self.rr_add_spy_edge20, 0.0)  # [SPY_GATE]
            and float(self.rsi14[sym].Current.Value) < self.rsi_add_max
        )

    def _liq_reason(self, sym, smh_px, qqq_px, chandelier_stop) -> str | None:
        """Return a short reason string if a full exit is warranted, else None."""
        if chandelier_stop is None:
            return None
        price  = float(self.securities[sym].price)
        r5     = float(self.roc5_cand[sym].Current.Value)
        smh100 = float(self.smh_sma100.Current.Value)
        qqq100 = float(self.qqq_sma100.Current.Value)
        smh50  = float(self.smh_sma50.Current.Value)
        sma50  = float(self.sma50[sym].Current.Value)
        # [RR-SHOCK-C1] First: clean attribution before legacy exits fire.
        if (
            bool(self.rr_shock_exit_enable)
            and self._rr_market_short_shock()
            and self._rr_below_fast_ma(sym)
            and self._rr_shock_confirmed(sym)
        ):
            return "RR_MA10_SHORT_SHOCK"
        if price < chandelier_stop:
            return f"CHANDELIER px={price:.2f} stop={chandelier_stop:.2f}"
        if smh_px < smh100:
            return f"SMH_100 smh={smh_px:.2f}<sma100={smh100:.2f}"
        if qqq_px < qqq100:
            return f"QQQ_100 qqq={qqq_px:.2f}<sma100={qqq100:.2f}"
        if r5 < -0.12:
            return f"R5_CRASH r5={r5:.3f}"
        if price < sma50 and smh_px < smh50:
            return f"SECTOR_DUAL px<sma50={sma50:.2f} smh<sma50={smh50:.2f}"
        return None

    def _should_liquidate(self, sym, smh_px, qqq_px, chandelier_stop) -> bool:
        return self._liq_reason(sym, smh_px, qqq_px, chandelier_stop) is not None