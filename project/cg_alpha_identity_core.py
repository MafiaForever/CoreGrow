# cg_alpha_identity_core.py -- CG-ALPHA-IDENTITY-AUDIT-D0 pure diagnostics.
# No orders, subscriptions, target mutations, or holdings-derived targets.
from __future__ import annotations
import hashlib
import json
from datetime import date, datetime, time, timedelta

EXPERIMENT = "CG-ALPHA-IDENTITY-AUDIT-D0"
PHASE = "A0_CAUSAL_PRODUCTION_PATH_CAPTURE_AND_COUNTERFACTUAL_AUDIT"
SCHEMA = "ALPHA_IDENTITY_A0_V1"
EPS = 1e-12
MAX_LEDGER = 8192
MAX_NAV = 8192
COST_BPS = 0
LAG_MINUTES = 0
PROXY_EXEC = "FIRST_OBSERVED_BAR_STRICTLY_AFTER_DECISION"

RESEARCH_START = date(2012, 1, 1)
RESEARCH_END = date(2026, 5, 10)
PERIODS = {
    "TRAIN": (date(2012, 1, 1), date(2018, 12, 31)),
    "OOS": (date(2019, 1, 1), date(2021, 12, 31)),
    "CRISIS": (date(2022, 1, 1), date(2025, 12, 31)),
    "UNTOUCHED_RECENT": (date(2026, 1, 1), date(2026, 5, 10)),
    "FULL": (date(2012, 1, 1), date(2026, 5, 10)),
}

# Explicit static map (primary). Unlisted positive-weight assets => UNCERTAIN.
DEFENSIVE_TK = frozenset({
    "TIP", "BND", "GLD", "GLDM", "BIL", "SGOV", "USFR", "SH", "TFLO",
})
CASH_TK = frozenset({"BIL", "SGOV", "USFR", "TFLO"})
# Equity: SPY + known tactical/growth/sector equities used by CG sleeves.
EQUITY_TK = frozenset({
    "SPY", "SPYG", "QQQ", "SMH", "XLE", "XLB", "XLV", "XLU", "DBC",
    "MU", "NVDA", "AVGO",
})

PATHS = (
    "A_CG_FULL",
    "B_GROSS_MATCHED_SPY",
    "C_GROSS_MATCHED_QQQ",
    "D_FIXED_BUDGET_SELECTION",
    "E_TIMING_ONLY_SPY",  # mathematically identical to B
    "G_SIMPLE_TREND_SPY",
)

ASSET_MAP = {
    "equity_tickers": sorted(EQUITY_TK),
    "defensive_tickers": sorted(DEFENSIVE_TK),
    "cash_tickers": sorted(CASH_TK),
    "signed_equity_formula": "sum(w for w>0 and class==EQUITY) - sum(abs(w) for w<0 and class==EQUITY)",
    "gross_equity_formula": "sum(abs(w) for class==EQUITY)",
    "cash_weight_formula": "sum(w for class==CASH) + parked cash ETF weight",
    "defensive_weight_formula": "sum(w for class==DEFENSIVE)",
    "uncertain_policy": "flag separately; excluded from primary equity gross",
    "e_equals_b": True,
    "e_note": "E_TIMING_ONLY_SPY uses the same signed equity exposure on SPY as B; identical by definition.",
}


def _tk(sym):
    try:
        return str(sym.Value).upper()
    except Exception:
        try:
            return str(sym.value).upper()
        except Exception:
            s = str(sym or "").upper()
            return s.split(" ")[0] if s else ""


def _to_dt(t):
    if isinstance(t, datetime):
        return t
    if t is None:
        return None
    try:
        return datetime(
            int(t.year), int(t.month), int(t.day),
            int(getattr(t, "hour", 0) or 0),
            int(getattr(t, "minute", 0) or 0),
            int(getattr(t, "second", 0) or 0),
        )
    except Exception:
        return None


def classify_ticker(tk, cash_sym_tk=None):
    t = str(tk or "").upper()
    if not t:
        return "EMPTY"
    if cash_sym_tk and t == str(cash_sym_tk).upper():
        return "CASH"
    if t in CASH_TK:
        return "CASH"
    if t in DEFENSIVE_TK:
        return "DEFENSIVE"
    if t in EQUITY_TK:
        return "EQUITY"
    return "UNCERTAIN"


def weights_to_ticker_map(targets):
    out = {}
    for k, v in (targets or {}).items():
        try:
            w = float(v or 0.0)
        except Exception:
            continue
        if abs(w) < EPS:
            continue
        t = _tk(k)
        out[t] = out.get(t, 0.0) + w
    return out


def analyze_targets(targets, cash_sym_tk=None):
    wm = weights_to_ticker_map(targets)
    signed_eq = 0.0
    gross_eq = 0.0
    cash_w = 0.0
    def_w = 0.0
    uncertain = {}
    selected = {}
    for t, w in wm.items():
        cls = classify_ticker(t, cash_sym_tk)
        if cls == "EQUITY":
            if w >= 0:
                signed_eq += w
            else:
                signed_eq += w  # short equity reduces signed
            gross_eq += abs(w)
            selected[t] = w
        elif cls == "CASH":
            cash_w += w
        elif cls == "DEFENSIVE":
            def_w += w
        else:
            uncertain[t] = w
    # Fixed-budget selection: normalize selected equities to unit gross if any
    sel_norm = {}
    if gross_eq > EPS:
        for t, w in selected.items():
            sel_norm[t] = w / gross_eq
    h = hashlib.sha256(
        json.dumps({"w": {k: round(v, 10) for k, v in sorted(wm.items())}},
                   sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:16]
    return {
        "weights": {k: round(v, 10) for k, v in sorted(wm.items())},
        "selected_equity": {k: round(v, 10) for k, v in sorted(selected.items())},
        "selected_norm_unit_gross": {k: round(v, 10) for k, v in sorted(sel_norm.items())},
        "signed_equity_exposure": round(signed_eq, 10),
        "gross_equity_exposure": round(gross_eq, 10),
        "cash_weight": round(cash_w, 10),
        "defensive_weight": round(def_w, 10),
        "uncertain": {k: round(v, 10) for k, v in sorted(uncertain.items())},
        "target_hash": h,
    }


def _slot_dt(day, slot_minutes):
    return datetime.combine(day, time(int(slot_minutes) // 60, int(slot_minutes) % 60))


class _Sleeve:
    __slots__ = ("cash", "shares", "frac", "pending_frac", "pending_after",
                 "peak", "max_dd", "switches", "cost_bps")

    def __init__(self, cost_bps=COST_BPS):
        self.cash = 1.0
        self.shares = 0.0
        self.frac = 0.0
        self.pending_frac = None
        self.pending_after = None
        self.peak = 1.0
        self.max_dd = 0.0
        self.switches = 0
        self.cost_bps = float(cost_bps)

    def equity(self, px):
        if px is None or px <= 0:
            return self.cash
        return self.cash + self.shares * float(px)

    def schedule(self, frac, decision_time):
        if not isinstance(decision_time, datetime):
            return
        tf = max(-1.0, min(2.0, float(frac)))  # allow signed exposure
        # long-only sleeve uses max(0, signed) for SPY/QQQ matched path
        tf = max(0.0, min(1.5, tf))
        self.pending_frac = tf
        self.pending_after = decision_time

    def apply(self, bar_time, px):
        if self.pending_frac is None or self.pending_after is None:
            return False
        if not isinstance(bar_time, datetime) or not (bar_time > self.pending_after):
            return False
        return self._set(self.pending_frac, px, True)

    def _set(self, new_f, px, clear=False):
        if px is None or px <= 0:
            return False
        new_f = float(new_f)
        e = self.equity(px)
        turn = abs(new_f - self.frac) * e
        if self.cost_bps > EPS and turn > EPS:
            e = max(0.0, e - turn * (self.cost_bps / 10000.0))
        if abs(new_f - self.frac) > EPS:
            self.switches += 1
        if e <= EPS or float(px) <= EPS:
            self.shares = 0.0
            self.cash = e
            self.frac = 0.0
        else:
            self.shares = (new_f * e) / float(px)
            self.cash = (1.0 - new_f) * e
            self.frac = new_f
        if clear:
            self.pending_frac = None
            self.pending_after = None
        ee = self.equity(px)
        if ee > self.peak:
            self.peak = ee
        if self.peak > EPS:
            self.max_dd = max(self.max_dd, (self.peak - ee) / self.peak)
        return True

    def mtm(self, px):
        if px is None or px <= 0:
            return
        ee = self.equity(px)
        if ee > self.peak:
            self.peak = ee
        if self.peak > EPS:
            self.max_dd = max(self.max_dd, (self.peak - ee) / self.peak)


class AlphaIdentityEngine:
    """Bounded target ledger + counterfactual sleeves. Diagnostic only."""

    def __init__(self):
        self.enabled = False
        self.seq = 0
        self.rows = []
        self.nav_path = []  # (datetime, nav) for A_CG_FULL
        self.last_hash = None
        self.last_seq_time = None
        self.sleeves = {p: _Sleeve() for p in PATHS if p != "A_CG_FULL"}
        # E mirrors B
        self.spy_px = None
        self.qqq_px = None
        self.bil_px = None
        self.spy_closes = []  # (date, close) for SMA200
        self.trend_state = 0.0
        self.pre_prot = None  # optional {hash, signed, weights, time}
        self.prot_events = []
        self.path_marks = {p: [] for p in PATHS}  # daily (date, equity)
        self._last_mark_day = None
        self._d_holdings = {}  # ticker -> shares for selection sleeve
        self._d_cash = 1.0
        self._d_pending = None  # (after_dt, weight_map unit gross * budget)
        self._d_peak = 1.0
        self._d_max_dd = 0.0
        self._d_switches = 0
        self._px = {}  # last px by ticker
        self.counters = {
            "captures": 0, "duplicate_hash_blocked": 0, "out_of_order": 0,
            "spy_bars": 0, "qqq_bars": 0, "bil_bars": 0, "other_bars": 0,
            "same_bar_blocked": 0, "uncertain_weight_events": 0,
            "protection_pairs": 0, "nav_marks": 0, "day_marks": 0,
            "diagnostic_real_orders": 0, "subscription_changes": 0,
            "target_mutations": 0, "order_mutations": 0,
            "production_gross_mutations": 0,
        }

    def set_enabled(self, on):
        self.enabled = bool(on)

    def observe_protection_pair(self, decision_time, pre_targets, post_targets, cash_tk=None):
        if not self.enabled:
            return
        pre = analyze_targets(pre_targets, cash_tk)
        post = analyze_targets(post_targets, cash_tk)
        self.pre_prot = {
            "decision_time": _to_dt(decision_time),
            "pre": pre, "post": post,
        }
        self.counters["protection_pairs"] += 1

    def observe_capture(self, decision_time, targets, slot_minutes=165,
                        feature_cutoff=None, flags=None, episode_id=None,
                        cash_tk=None, source="CgRegimeRebalTimeTradeCapture"):
        if not self.enabled:
            return None
        dt = _to_dt(decision_time)
        if dt is None or not isinstance(targets, dict):
            return None
        # Causal ordering
        if self.last_seq_time is not None and dt < self.last_seq_time:
            self.counters["out_of_order"] += 1
            return None
        an = analyze_targets(targets, cash_tk)
        if an["uncertain"]:
            self.counters["uncertain_weight_events"] += 1
        if an["target_hash"] == self.last_hash and self.rows and self.rows[-1].get("decision_date") == dt.date().isoformat():
            self.counters["duplicate_hash_blocked"] += 1
            # still allow if exposures changed flags-only? skip duplicate same-day identical hash
            return None
        self.seq += 1
        fc = _to_dt(feature_cutoff) if feature_cutoff is not None else dt
        if fc is not None and fc > dt:
            fc = dt  # never after decision
        exe = _slot_dt(dt.date(), int(slot_minutes or 165))
        if exe <= dt:
            exe = dt + timedelta(minutes=1)
        fl = flags or {}
        row = {
            "seq": self.seq,
            "decision_time": dt,
            "decision_date": dt.date().isoformat(),
            "feature_cutoff": fc,
            "next_execution_eligible_time": exe,
            "target_hash": an["target_hash"],
            "signed_equity_exposure": an["signed_equity_exposure"],
            "gross_equity_exposure": an["gross_equity_exposure"],
            "cash_weight": an["cash_weight"],
            "defensive_weight": an["defensive_weight"],
            "selected_equity": an["selected_equity"],
            "selected_norm_unit_gross": an["selected_norm_unit_gross"],
            "uncertain": an["uncertain"],
            "weights": an["weights"],
            "w2": int(bool(fl.get("w2"))),
            "ids": str(fl.get("ids") or ""),
            "panic": str(fl.get("panic") or ""),
            "sh": int(bool(fl.get("sh"))),
            "episode_id": episode_id,
            "source": source,
            "causality": "INTENDED_TARGET_BEFORE_DISPATCH",
        }
        self.rows.append(row)
        if len(self.rows) > MAX_LEDGER:
            self.rows = self.rows[-MAX_LEDGER:]
        self.last_hash = an["target_hash"]
        self.last_seq_time = dt
        self.counters["captures"] += 1

        signed = an["signed_equity_exposure"]
        self.sleeves["B_GROSS_MATCHED_SPY"].schedule(signed, dt)
        self.sleeves["E_TIMING_ONLY_SPY"].schedule(signed, dt)
        self.sleeves["C_GROSS_MATCHED_QQQ"].schedule(signed, dt)
        # D: selected equity mix normalized to unit gross, then scaled to FIXED_BUDGET=1.0
        # Missing prices remain in cash until observed (no look-ahead).
        self._d_pending = (dt, dict(an["selected_norm_unit_gross"]))

        if self.pre_prot and self.pre_prot.get("decision_time") and self.pre_prot["decision_time"].date() == dt.date():
            pre = self.pre_prot["pre"]
            post = self.pre_prot["post"]
            if pre["target_hash"] != post["target_hash"]:
                self.prot_events.append({
                    "decision_time": dt.isoformat(sep=" "),
                    "pre_signed": pre["signed_equity_exposure"],
                    "post_signed": post["signed_equity_exposure"],
                    "delta_signed": round(
                        post["signed_equity_exposure"] - pre["signed_equity_exposure"], 10),
                    "pre_hash": pre["target_hash"],
                    "post_hash": post["target_hash"],
                })
                if len(self.prot_events) > 2048:
                    self.prot_events = self.prot_events[-2048:]
        return row

    def observe_nav(self, t, nav):
        if not self.enabled:
            return
        dt = _to_dt(t)
        try:
            n = float(nav)
        except Exception:
            return
        if dt is None or n <= 0:
            return
        self.nav_path.append((dt, n))
        if len(self.nav_path) > MAX_NAV:
            self.nav_path = self.nav_path[-MAX_NAV:]
        self.counters["nav_marks"] += 1

    def on_bar(self, ticker, bar_time, px):
        if not self.enabled:
            return
        dt = _to_dt(bar_time)
        try:
            p = float(px)
        except Exception:
            return
        if dt is None or p <= 0:
            return
        if dt.date() > RESEARCH_END:
            return
        tk = str(ticker or "").upper()
        self._px[tk] = p
        if tk == "SPY":
            self.counters["spy_bars"] += 1
            self.spy_px = p
            d = dt.date()
            if not self.spy_closes or self.spy_closes[-1][0] != d:
                self.spy_closes.append((d, p))
            else:
                self.spy_closes[-1] = (d, p)
            if len(self.spy_closes) > 400:
                self.spy_closes = self.spy_closes[-400:]
            for name in ("B_GROSS_MATCHED_SPY", "E_TIMING_ONLY_SPY", "G_SIMPLE_TREND_SPY"):
                sl = self.sleeves[name]
                if sl.pending_frac is not None and sl.pending_after is not None and not (dt > sl.pending_after):
                    self.counters["same_bar_blocked"] += 1
                sl.apply(dt, p)
                sl.mtm(p)
            self._maybe_schedule_trend(dt, p)
            self._apply_d(dt)
            self._maybe_day_mark(dt)
        elif tk == "QQQ":
            self.counters["qqq_bars"] += 1
            self.qqq_px = p
            sl = self.sleeves["C_GROSS_MATCHED_QQQ"]
            if sl.pending_frac is not None and sl.pending_after is not None and not (dt > sl.pending_after):
                self.counters["same_bar_blocked"] += 1
            sl.apply(dt, p)
            sl.mtm(p)
            self._apply_d(dt)
        elif tk == "BIL":
            self.counters["bil_bars"] += 1
            self.bil_px = p
            self._apply_d(dt)
        else:
            self.counters["other_bars"] += 1
            self._apply_d(dt)

    def _d_equity(self):
        e = self._d_cash
        for t, sh in self._d_holdings.items():
            px = self._px.get(t)
            if px is not None and px > 0:
                e += sh * px
        return e

    def _apply_d(self, dt):
        if self._d_pending is None:
            return
        after, wmap = self._d_pending
        if not isinstance(dt, datetime) or not (dt > after):
            self.counters["same_bar_blocked"] += 1
            return
        missing = [t for t in wmap if t not in self._px or self._px[t] <= 0]
        if missing:
            return
        e = self._d_equity()
        self._d_cash = e
        self._d_holdings = {}
        if e > EPS and wmap:
            for t, w in wmap.items():
                self._d_holdings[t] = (float(w) * e) / self._px[t]
            self._d_cash = 0.0
            self._d_switches += 1
        self._d_pending = None
        ee = self._d_equity()
        if ee > self._d_peak:
            self._d_peak = ee
        if self._d_peak > EPS:
            self._d_max_dd = max(self._d_max_dd, (self._d_peak - ee) / self._d_peak)

    def _maybe_day_mark(self, dt):
        if dt.hour * 60 + dt.minute < 955:
            return
        d = dt.date()
        if self._last_mark_day == d:
            return
        if d < RESEARCH_START or d > RESEARCH_END:
            return
        spy = self.spy_px
        qqq = self.qqq_px or spy
        # A: normalize NAV to wealth factor vs first nav in research window
        a_e = None
        if self.nav_path:
            base = None
            last = None
            for t, n in self.nav_path:
                if t.date() < RESEARCH_START:
                    continue
                if t.date() > d:
                    break
                if base is None:
                    base = n
                last = n
            if base is not None and last is not None and base > EPS:
                a_e = last / base
        marks = {
            "B_GROSS_MATCHED_SPY": self.sleeves["B_GROSS_MATCHED_SPY"].equity(spy),
            "E_TIMING_ONLY_SPY": self.sleeves["E_TIMING_ONLY_SPY"].equity(spy),
            "C_GROSS_MATCHED_QQQ": self.sleeves["C_GROSS_MATCHED_QQQ"].equity(qqq),
            "D_FIXED_BUDGET_SELECTION": self._d_equity(),
            "G_SIMPLE_TREND_SPY": self.sleeves["G_SIMPLE_TREND_SPY"].equity(spy),
        }
        if a_e is not None:
            marks["A_CG_FULL"] = a_e
        for name, val in marks.items():
            self.path_marks[name].append((d, float(val)))
            if len(self.path_marks[name]) > MAX_NAV:
                self.path_marks[name] = self.path_marks[name][-MAX_NAV:]
        self._last_mark_day = d
        self.counters["day_marks"] += 1

    def _maybe_schedule_trend(self, dt, spy_px):
        closes = self.spy_closes
        if len(closes) < 201:
            return
        if dt.hour * 60 + dt.minute < 960:
            return
        hist = [c for d, c in closes if d < dt.date()]
        if len(hist) < 200:
            return
        prev = hist[-1]
        sma = sum(hist[-200:]) / 200.0
        tgt = 1.0 if prev > sma else 0.0
        if abs(tgt - self.trend_state) < EPS:
            return
        self.trend_state = tgt
        dec = datetime.combine(dt.date(), time(16, 0))
        self.sleeves["G_SIMPLE_TREND_SPY"].schedule(tgt, dec)

    def finalize(self):
        return self.snapshot()

    def snapshot(self):
        spy = self.spy_px or 1.0
        qqq = self.qqq_px or spy
        metrics = {}
        if len(self.nav_path) >= 2:
            n0 = self.nav_path[0][1]
            n1 = self.nav_path[-1][1]
            a_wf = n1 / n0 if n0 > EPS else 1.0
            peak = self.nav_path[0][1]
            a_dd = 0.0
            for _, n in self.nav_path:
                if n > peak:
                    peak = n
                if peak > EPS:
                    a_dd = max(a_dd, (peak - n) / peak)
        else:
            a_wf, a_dd = 1.0, 0.0
        metrics["A_CG_FULL"] = {
            "final_wealth_factor": a_wf, "max_drawdown": a_dd, "switch_count": None,
            "nav_marks": len(self.nav_path),
        }
        for name, px in (
            ("B_GROSS_MATCHED_SPY", spy),
            ("E_TIMING_ONLY_SPY", spy),
            ("G_SIMPLE_TREND_SPY", spy),
            ("C_GROSS_MATCHED_QQQ", qqq),
        ):
            sl = self.sleeves[name]
            metrics[name] = {
                "final_wealth_factor": sl.equity(px),
                "max_drawdown": sl.max_dd,
                "switch_count": sl.switches,
            }
        metrics["D_FIXED_BUDGET_SELECTION"] = {
            "final_wealth_factor": self._d_equity(),
            "max_drawdown": self._d_max_dd,
            "switch_count": self._d_switches,
        }
        period_rows = self._period_scorecard()
        year_rows = self._year_contribution(period_rows)
        oos = period_rows.get("OOS") or {}
        crisis = period_rows.get("CRISIS") or {}
        pairwise = {
            "residual_vs_spy": metrics["A_CG_FULL"]["final_wealth_factor"] - metrics["B_GROSS_MATCHED_SPY"]["final_wealth_factor"],
            "residual_vs_qqq": metrics["A_CG_FULL"]["final_wealth_factor"] - metrics["C_GROSS_MATCHED_QQQ"]["final_wealth_factor"],
            "residual_oos_vs_spy": oos.get("residual_vs_spy"),
            "residual_oos_vs_qqq": oos.get("residual_vs_qqq"),
            "residual_crisis_vs_spy": crisis.get("residual_vs_spy"),
            "residual_crisis_vs_qqq": crisis.get("residual_vs_qqq"),
            "selection_effect": metrics["D_FIXED_BUDGET_SELECTION"]["final_wealth_factor"] - metrics["B_GROSS_MATCHED_SPY"]["final_wealth_factor"],
            "simple_trend_check": metrics["A_CG_FULL"]["final_wealth_factor"] - metrics["G_SIMPLE_TREND_SPY"]["final_wealth_factor"],
            "e_equals_b": abs(
                metrics["E_TIMING_ONLY_SPY"]["final_wealth_factor"]
                - metrics["B_GROSS_MATCHED_SPY"]["final_wealth_factor"]) < 1e-9,
            "e_note": "E_TIMING_ONLY_SPY identical to B_GROSS_MATCHED_SPY by construction",
        }
        prot = {
            "valid": len(self.prot_events) > 0,
            "event_count": len(self.prot_events),
            "mean_delta_signed": (
                sum(e["delta_signed"] for e in self.prot_events) / len(self.prot_events)
                if self.prot_events else None),
            "note": "Paired pre/post intended signed equity at W2 apply; not a no-protection path.",
            "events_sample": self.prot_events[:32],
        }
        verdict, reason, conc = self._verdict(period_rows, pairwise, year_rows)
        return {
            "experiment": EXPERIMENT,
            "phase": PHASE,
            "schema": SCHEMA,
            "asset_map": ASSET_MAP,
            "cost_bps": COST_BPS,
            "lag_minutes": LAG_MINUTES,
            "proxy_execution_rule": PROXY_EXEC,
            "ledger_count": len(self.rows),
            "counters": dict(self.counters),
            "metrics": metrics,
            "pairwise": pairwise,
            "periods": period_rows,
            "years": year_rows,
            "protection": prot,
            "verdict": verdict,
            "verdict_reason": reason,
            "single_year_concentration": conc,
            "rows_sample": [
                {k: (v.isoformat(sep=" ") if isinstance(v, datetime) else v)
                 for k, v in r.items() if k not in ("weights", "selected_norm_unit_gross")}
                for r in self.rows[:32]
            ],
        }

    def _mark_slice(self, name, d0, d1):
        return [(d, v) for d, v in self.path_marks.get(name, []) if d0 <= d <= d1]

    def _nav_slice(self, d0, d1):
        return [(t, n) for t, n in self.nav_path if d0 <= t.date() <= d1]

    def _wf_dd_dated(self, pts):
        if len(pts) < 2:
            return None, None
        n0, n1 = pts[0][1], pts[-1][1]
        wf = n1 / n0 if n0 > EPS else 1.0
        peak = pts[0][1]
        dd = 0.0
        for _, n in pts:
            if n > peak:
                peak = n
            if peak > EPS:
                dd = max(dd, (peak - n) / peak)
        return wf, dd

    def _cagr(self, wf, d0, d1):
        if wf is None or wf <= 0:
            return None
        days = max(1, (d1 - d0).days)
        years = days / 365.25
        if years < 1.0 / 12.0:
            return None
        return wf ** (1.0 / years) - 1.0

    def _period_scorecard(self):
        rows = {}
        for name, (d0, d1) in PERIODS.items():
            a_pts = self._nav_slice(d0, d1)
            a_wf, a_dd = self._wf_dd_dated(a_pts)
            b_wf, b_dd = self._wf_dd_dated(self._mark_slice("B_GROSS_MATCHED_SPY", d0, d1))
            c_wf, c_dd = self._wf_dd_dated(self._mark_slice("C_GROSS_MATCHED_QQQ", d0, d1))
            d_wf, d_dd = self._wf_dd_dated(self._mark_slice("D_FIXED_BUDGET_SELECTION", d0, d1))
            g_wf, g_dd = self._wf_dd_dated(self._mark_slice("G_SIMPLE_TREND_SPY", d0, d1))
            r_spy = (a_wf - b_wf) if (a_wf is not None and b_wf is not None) else None
            r_qqq = (a_wf - c_wf) if (a_wf is not None and c_wf is not None) else None
            rows[name] = {
                "A_CG_FULL_wealth": a_wf,
                "A_CG_FULL_maxdd": a_dd,
                "A_CG_FULL_cagr": self._cagr(a_wf, d0, d1) if a_wf is not None else None,
                "B_wealth": b_wf, "B_maxdd": b_dd,
                "C_wealth": c_wf, "C_maxdd": c_dd,
                "D_wealth": d_wf, "D_maxdd": d_dd,
                "G_wealth": g_wf, "G_maxdd": g_dd,
                "residual_vs_spy": r_spy,
                "residual_vs_qqq": r_qqq,
                "selection_effect": (d_wf - b_wf) if (d_wf is not None and b_wf is not None) else None,
                "simple_trend_check": (a_wf - g_wf) if (a_wf is not None and g_wf is not None) else None,
                "n_nav": len(a_pts),
                "n_marks_b": len(self._mark_slice("B_GROSS_MATCHED_SPY", d0, d1)),
            }
        return rows

    def _year_contribution(self, period_rows):
        by_a, by_b = {}, {}
        for t, n in self.nav_path:
            by_a.setdefault(t.year, []).append((t.date(), n))
        for d, v in self.path_marks.get("B_GROSS_MATCHED_SPY", []):
            by_b.setdefault(d.year, []).append((d, v))
        years = sorted(set(by_a) | set(by_b))
        tmp = []
        total_abs = 0.0
        for y in years:
            a_pts = by_a.get(y) or []
            b_pts = by_b.get(y) or []
            # convert nav to dated tuples for wf
            a_wf, a_dd = self._wf_dd_dated([(d, n) for d, n in a_pts]) if len(a_pts) >= 2 else (None, None)
            b_wf, _ = self._wf_dd_dated(b_pts) if len(b_pts) >= 2 else (None, None)
            excess = (a_wf - b_wf) if (a_wf is not None and b_wf is not None) else None
            tmp.append((y, a_wf, a_dd, b_wf, excess))
            if excess is not None:
                total_abs += abs(excess)
        rows = []
        for y, a_wf, a_dd, b_wf, excess in tmp:
            share = (abs(excess) / total_abs) if (excess is not None and total_abs > EPS) else None
            rows.append({
                "year": y,
                "cg_wealth_factor": a_wf,
                "cg_maxdd": a_dd,
                "b_wealth_factor": b_wf,
                "excess_vs_spy": excess,
                "abs_excess_share": share,
            })
        return rows

    def _verdict(self, periods, pairwise, years):
        oos = periods.get("OOS") or {}
        crisis = periods.get("CRISIS") or {}
        r_oos_spy = oos.get("residual_vs_spy")
        r_oos_qqq = oos.get("residual_vs_qqq")
        r_cr_spy = crisis.get("residual_vs_spy")
        r_cr_qqq = crisis.get("residual_vs_qqq")
        if years:
            ranked = [r for r in years if r.get("abs_excess_share") is not None]
            if ranked:
                top = max(ranked, key=lambda r: r["abs_excess_share"])
                # leave-one-out: if removing top year flips OOS+CRISIS agreement
                conc = {
                    "top_year": top["year"],
                    "top_share": top["abs_excess_share"],
                    "dominated_by_one_year": bool(top["abs_excess_share"] >= 0.50),
                }
            else:
                conc = {"top_year": None, "top_share": None, "dominated_by_one_year": False}
        else:
            conc = {"top_year": None, "top_share": None, "dominated_by_one_year": False}

        def _pos(x):
            return x is not None and x > 0

        oos_both = _pos(r_oos_spy) and _pos(r_oos_qqq)
        crisis_both = _pos(r_cr_spy) and _pos(r_cr_qqq)
        oos_crisis_agree = (
            r_oos_spy is not None and r_cr_spy is not None
            and ((r_oos_spy > 0) == (r_cr_spy > 0))
        )
        trend_explains = (pairwise.get("simple_trend_check") is not None
                          and pairwise["simple_trend_check"] <= 0)
        sel = pairwise.get("selection_effect")
        r_spy = pairwise.get("residual_vs_spy")
        r_qqq = pairwise.get("residual_vs_qqq")

        if oos_both and crisis_both and not conc["dominated_by_one_year"] and not trend_explains:
            v = "CG_CORE_ALPHA_CONFIRMED"
            reason = (
                f"OOS residual spy/qqq={r_oos_spy}/{r_oos_qqq}; "
                f"CRISIS residual spy/qqq={r_cr_spy}/{r_cr_qqq}; "
                f"not single-year dominated; simple trend does not explain."
            )
        elif (not oos_both or not crisis_both) and sel is not None and sel > 0 and (r_spy is None or r_spy <= 0):
            v = "CG_TIMING_SLEEVE_CONFIRMED_SELECTION_ALPHA_WEAK"
            reason = "Selection effect positive but residual vs SPY not robust across OOS/CRISIS."
        elif (r_spy is not None and r_spy <= 0 and r_qqq is not None and r_qqq <= 0
              and self.prot_events):
            v = "CG_PROTECTION_VALUE_ONLY"
            reason = "Full residual vs SPY/QQQ non-positive; only protection-pair events available."
        else:
            v = "CG_RESIDUAL_ALPHA_NOT_CONFIRMED"
            reason = (
                f"oos_spy={r_oos_spy}, oos_qqq={r_oos_qqq}, "
                f"crisis_spy={r_cr_spy}, crisis_qqq={r_cr_qqq}, "
                f"agree={oos_crisis_agree}, trend_explains={trend_explains}, "
                f"conc={conc}."
            )
        return v, reason, conc


def run_alpha_identity_static_tests():
    rows, passed, failed = [], 0, 0

    def ok(n, c, detail=""):
        nonlocal passed, failed
        if c:
            passed += 1
            rows.append({"name": n, "pass": True, "detail": detail})
        else:
            failed += 1
            rows.append({"name": n, "pass": False, "detail": str(detail)})

    ok("A01_asset_map", "SPY" in EQUITY_TK and "BIL" in DEFENSIVE_TK)
    an = analyze_targets({"SPY": 0.8, "BIL": 0.2})
    ok("A02_signed", abs(an["signed_equity_exposure"] - 0.8) < 1e-12)
    ok("A03_hash", len(an["target_hash"]) == 16)
    unc = analyze_targets({"SPY": 0.5, "ZZZUNK": 0.1})
    ok("A04_uncertain", "ZZZUNK" in unc["uncertain"])

    eng = AlphaIdentityEngine()
    eng.set_enabled(True)
    t0 = datetime(2020, 3, 16, 9, 45)
    eng.observe_capture(t0, {"SPY": 0.6, "BIL": 0.4}, slot_minutes=165,
                        feature_cutoff=t0, flags={"w2": 1, "ids": "WATCH"})
    ok("A05_ledger", eng.counters["captures"] == 1 and eng.rows[0]["causality"] == "INTENDED_TARGET_BEFORE_DISPATCH")
    ok("A06_fc", eng.rows[0]["feature_cutoff"] <= eng.rows[0]["decision_time"])
    # same-bar block
    eng.on_bar("SPY", t0, 100.0)
    ok("A07_same_bar", eng.sleeves["B_GROSS_MATCHED_SPY"].frac == 0.0)
    eng.on_bar("SPY", t0 + timedelta(minutes=5), 100.0)
    ok("A08_after", abs(eng.sleeves["B_GROSS_MATCHED_SPY"].frac - 0.6) < 1e-9)
    # E == B
    eng.on_bar("SPY", t0 + timedelta(minutes=10), 101.0)
    ok("A09_e_eq_b", abs(eng.sleeves["E_TIMING_ONLY_SPY"].equity(101) - eng.sleeves["B_GROSS_MATCHED_SPY"].equity(101)) < 1e-9)
    eng.observe_nav(t0, 10000)
    eng.observe_nav(t0 + timedelta(days=1), 10100)
    snap = eng.snapshot()
    ok("A10_snap", snap["pairwise"]["e_equals_b"] is True)
    ok("A11_noop", eng.counters["target_mutations"] == 0)
    eng2 = AlphaIdentityEngine()
    eng2.set_enabled(False)
    eng2.observe_capture(t0, {"SPY": 1.0})
    ok("A12_disabled", eng2.counters["captures"] == 0)
    # ooo
    eng.observe_capture(t0 - timedelta(days=1), {"SPY": 0.5})
    ok("A13_ooo", eng.counters["out_of_order"] >= 1)
    ok("A14_verdict_key", "verdict" in snap)
    # D multi-asset
    eng3 = AlphaIdentityEngine()
    eng3.set_enabled(True)
    eng3.observe_capture(t0, {"SPY": 0.5, "QQQ": 0.5}, slot_minutes=165)
    eng3.on_bar("SPY", t0 + timedelta(minutes=1), 100.0)
    eng3.on_bar("QQQ", t0 + timedelta(minutes=1), 200.0)
    ok("A15_d_applied", abs(eng3._d_equity() - 1.0) < 1e-9)
    ok("A16_asset_map_e", ASSET_MAP.get("e_equals_b") is True)

    return {"passed": passed, "failed": failed, "total": passed + failed, "rows": rows}


if __name__ == "__main__":
    r = run_alpha_identity_static_tests()
    print(json.dumps({k: r[k] for k in ("passed", "failed", "total")}))
    for row in r["rows"]:
        if not row["pass"]:
            print("FAIL", row["name"], row["detail"])
