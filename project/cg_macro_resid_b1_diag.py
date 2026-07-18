# cg_macro_resid_b1_diag.py -- CG-MACRO-RESID-B1 LEAN mixin.
from AlgorithmImports import *
from collections import defaultdict
from datetime import timedelta, date, datetime, time
import base64, bisect, zlib
from cg_macro_resid_b1_core import (
    MACRO_A1_CLOSEOUT, RESID_PXY5, RESID_BREADTH, RESID_VARIANTS, RESID_HORIZONS, RESID_TRUTH_PACK,
    resid_protection_snapshot, resid_stratum, resid_eval_variants, resid_session_peak_dd_atr,
    resid_15m_return, resid_vix_stress, resid_decluster_events, resid_proxy_benefit,
    resid_pass_gate, resid_rank_passers, resid_neighbor_variant,
    resid_window_for_day, resid_bucket, resid_baseline_keys, resid_prod_nav_return, resid_select_baselines,
    run_resid_b1_static_tests, run_resid_b1_eoa_dryrun, resid_material_symbols, resid_windows,
)
from cg_macro_resid_b11_export import (
    resid_update_close_proxy, resid_exit_threshold, resid_price_pxy5_detail, resid_apply_price_counters,
    resid_empty_counters, resid_b11_finalize, TIER1_BUDGET,
)
from cg_macro_a1_core import (
    macro_vix_snapshot, macro_rv30, macro_path_efficiency, macro_down_efficiency,
    macro_same_tod_percentile, macro_mf, macro_truth_pack_to_d4, MACRO_TRUTH_PACKS,
)
from cg_maisr_d4_core import d4_validate_source_commit, d4_raw_flags, d4_priority_macro, _TRAIN0, _TRAIN1
from cg_maisr_d2_labels import _D2_BREADTH

_OOS0, _OOS1 = date(2019, 1, 1).toordinal(), date(2021, 12, 31).toordinal()
_CR0, _CR1 = date(2022, 1, 1).toordinal(), date(2025, 12, 31).toordinal()
_TRUTH_FAMILY = frozenset(("BROAD_EQUITY_STRESS", "SYSTEMIC_LIQUIDITY_STRESS"))


class CgMacroResidB1DiagMixin:
    """CG-MACRO-RESID-B1 online collection + EOA finalize."""

    def _MacroResidB1ReadParams(self, _p, _bool):
        self.cg_macro_resid_b1_enable = _bool("cg_macro_resid_b1_enable", "0")
        self.cg_macro_resid_b1_source_commit = str(_p("cg_macro_resid_b1_source_commit", "") or "").strip().lower()
        self.cg_macro_resid_b1_export_detail = _bool("cg_macro_resid_b1_export_detail", "1")

    def _MacroResidB1InitHooks(self):
        if not getattr(self, "cg_macro_resid_b1_enable", False):
            return
        self._d2_mode = True
        self._resid_obs = []
        self._resid_meta = {}
        self._resid_truth = []
        self._resid_truth_by_key = {}
        # Full-history open series for EOA pricing (bisect); session closes for causal features.
        self._resid_open_et = {tk: [] for tk in RESID_PXY5}
        self._resid_open_px = {tk: [] for tk in RESID_PXY5}
        self._resid_sess_day = None
        self._resid_sess_closes = {tk: [] for tk in RESID_PXY5}
        self._resid_daily_1555 = {tk: {} for tk in RESID_PXY5}
        self._resid_spy_days = []
        self._resid_price_cache = {}
        self._resid_tod_hist = defaultdict(list)
        self._resid_data = {
            tk: {"accepted": 0, "dup": 0, "oo": 0, "first": None, "last": None,
                 "train_days": set(), "oos_days": set(), "crisis_days": set(), "last_et": None}
            for tk in RESID_PXY5
        }
        z = 0
        self._resid_err = self._resid_real_orders = self._resid_art_used = z
        self._resid_future_vix = self._resid_same_session_vix = self._resid_fabricated_vix = z
        self._resid_vix_ts_unavail = z
        self._resid_ctr = resid_empty_counters()
        self._resid_coverage_meta = []
        self._resid_detail_signals = []
        self._resid_unresolved_prot = self._resid_r0 = self._resid_r1 = self._resid_r2 = z
        self._resid_vix_cache = self._resid_vix_cache_day = None
        co = MACRO_A1_CLOSEOUT
        self._MacroResidB1Log(
            f"CG_MACRO_A1_CLOSEOUT_FINAL,backtest_id={co['backtest_id']},"
            f"source_commit={co['source_commit']},truth_pack={co['truth_pack']},"
            f"technical_result={co['technical_result']},predictor_family={co['predictor_family']}"
        )
        src = getattr(self, "cg_macro_resid_b1_source_commit", "") or ""
        src_ok, src_rsn = d4_validate_source_commit(src)
        self._MacroResidB1Log(
            f"CG_MACRO_RESID_B11_INIT,enable=1,source_commit={src or 'NONE'},"
            f"source_ok={int(src_ok)},detail={src_rsn},export={int(self.cg_macro_resid_b1_export_detail)}"
        )
        _rows, p, n = run_resid_b1_static_tests()
        self._MacroResidB1Log(f"CG_MACRO_RESID_B11_STATIC_FINAL,tests={p}/{n}")
        dry = run_resid_b1_eoa_dryrun()
        self._MacroResidB1Log(str(dry))
        if p != n or not src_ok or "pass=8,fail=0" not in str(dry):
            self._resid_err += 1
            self._resid_ctr["err"] = int(self._resid_ctr.get("err", 0)) + 1
        lp = list(getattr(self, "log_only_prefixes", None) or [])
        for pref in ("CG_MACRO_RESID_B11_", "CG_MACRO_RESID_B1_", "CG_MACRO_A1_CLOSEOUT"):
            if pref not in lp:
                lp.append(pref)
        self.log_only_prefixes = lp

    def _MacroResidB1Log(self, msg):
        try:
            if hasattr(self, "_MsLog"):
                self._MsLog(msg)
            else:
                self.log(msg)
        except Exception:
            pass

    def _MacroResidProtectionSnapshot(self):
        state = {
            "_cg_w2_last_active": getattr(self, "_cg_w2_last_active", False),
            "_ids_state": getattr(self, "_ids_state", None),
            "_panic_state": getattr(self, "_panic_state", None),
            "emergency_stop_triggered": getattr(self, "emergency_stop_triggered", False),
            "_dd_cb_active": getattr(self, "_dd_cb_active", False),
            "_lfc_force_reduce": getattr(self, "_lfc_force_reduce", False),
            "_cg_rt_pending_reduce": getattr(self, "_cg_rt_pending_reduce", False),
            "_state_save_ok": getattr(self, "_state_save_ok", True),
            "current_regime": getattr(self, "current_regime", None),
        }
        eq_gross, total_gross = 0.0, 0.0
        try:
            w = self.GetCurrentWeights() if hasattr(self, "GetCurrentWeights") else {}
            if hasattr(self, "_DftEqGross") and hasattr(self, "_DftEqSet"):
                eq_gross = float(self._DftEqGross(w, self._DftEqSet()) or 0.0)
            else:
                eq_gross = sum(float(w.get(tk, 0) or 0) for tk in RESID_PXY5 if float(w.get(tk, 0) or 0) > 0)
            total_gross = sum(abs(float(v or 0)) for v in w.values())
        except Exception:
            self._resid_err += 1
        state["equity_gross"] = eq_gross
        state["total_gross"] = total_gross
        snap = resid_protection_snapshot(state)
        if not snap.get("valid"):
            self._resid_unresolved_prot += 1
            self._resid_ctr["unresolved_protection_state"] = int(self._resid_ctr.get("unresolved_protection_state", 0)) + 1
        return snap

    def _MacroResidB1OnAcceptedBar(self, tk, et, o, h, l, c):
        if not getattr(self, "cg_macro_resid_b1_enable", False) or tk not in RESID_PXY5:
            return
        d = self._resid_data[tk]
        last = d.get("last_et")
        if last is not None and et is not None and et < last:
            d["oo"] += 1
            return
        if last is not None and et is not None and et == last:
            d["dup"] += 1
            return
        d["accepted"] += 1
        d["last_et"] = et
        if d["first"] is None:
            d["first"] = str(et)
        d["last"] = str(et)
        try:
            do = et.date().toordinal() if hasattr(et, "date") else self.time.date().toordinal()
        except Exception:
            do = self.time.date().toordinal()
        if _TRAIN0 <= do <= _TRAIN1:
            d["train_days"].add(do)
        elif _OOS0 <= do <= _OOS1:
            d["oos_days"].add(do)
        elif _CR0 <= do <= _CR1:
            d["crisis_days"].add(do)
        if et is not None and o is not None and float(o) > 0:
            self._resid_open_et[tk].append(et)
            self._resid_open_px[tk].append(float(o))
        try:
            day_ord = et.date().toordinal() if hasattr(et, "date") else do
            if self._resid_sess_day != day_ord:
                self._resid_sess_day = day_ord
                for s in RESID_PXY5:
                    self._resid_sess_closes[s] = []
            if c is not None:
                self._resid_sess_closes[tk].append(float(c))
            # Designated close-proxy: first EndTime strictly after 15:55 ET.
            cell, _acc = resid_update_close_proxy(self._resid_daily_1555[tk].get(day_ord), o, et)
            if cell is not None:
                self._resid_daily_1555[tk][day_ord] = cell
        except Exception:
            self._resid_err += 1
            self._resid_ctr["err"] = int(self._resid_ctr.get("err", 0)) + 1

    def _MacroResidB1NextOpen(self, tk, after_t):
        ets = self._resid_open_et.get(tk) or []
        if not ets or after_t is None:
            return None, None
        i = bisect.bisect_right(ets, after_t)
        px = self._resid_open_px.get(tk) or []
        while i < len(ets):
            o = px[i] if i < len(px) else None
            if o is not None and float(o) > 0:
                return float(o), ets[i]
            i += 1
        return None, None

    def _MacroResidB1SessionCloses(self, tk):
        return list(self._resid_sess_closes.get(tk) or [])

    def _MacroResidB1Vix(self):
        sess = self.time.date()
        if self._resid_vix_cache_day == sess and self._resid_vix_cache is not None:
            return self._resid_vix_cache
        rows = []
        try:
            if getattr(self, "vix", None) is None:
                self._resid_vix_ts_unavail += 1
                snap = macro_vix_snapshot([], sess, lookback=252)
                self._resid_vix_cache, self._resid_vix_cache_day = snap, sess
                return snap
            h = self.history(self.vix, 320, Resolution.DAILY)
            if h is None or getattr(h, "empty", True) or "value" not in getattr(h, "columns", []):
                self._resid_vix_ts_unavail += 1
                snap = macro_vix_snapshot([], sess, lookback=252)
                self._resid_vix_cache, self._resid_vix_cache_day = snap, sess
                return snap
            idx = h.index.get_level_values(-1)
            for d, v in zip(idx, h["value"].to_numpy(dtype=float)):
                dd = d.date() if hasattr(d, "date") else d
                if dd >= sess:
                    continue
                rows.append((dd, float(v)))
        except Exception:
            self._resid_vix_ts_unavail += 1
            self._resid_err += 1
            rows = []
        snap = macro_vix_snapshot(rows, sess, lookback=252)
        self._resid_vix_cache, self._resid_vix_cache_day = snap, sess
        return snap

    def _MacroResidB1OnEval(self, kind, tod, states_bytes, feat):
        if not getattr(self, "cg_macro_resid_b1_enable", False):
            return
        if kind != "POST" or not (590 <= int(tod) <= 900 and int(tod) % 5 == 0):
            return
        prot = self._MacroResidProtectionSnapshot()
        stratum = resid_stratum(
            prot.get("w2_active"), prot.get("ids_state"), prot.get("panic_state"),
            prot.get("emergency_active"), prot.get("reduce_only_active"), prot.get("equity_gross"),
        )
        if stratum == "R0_UNPROTECTED":
            self._resid_r0 += 1
        elif stratum == "R1_PARTIAL":
            self._resid_r1 += 1
        else:
            self._resid_r2 += 1
        breadth_dd = {}
        data_complete = True
        spy_dd = None
        spy_closes = self._MacroResidB1SessionCloses("SPY")
        for tk in ("SPY",) + RESID_BREADTH:
            atr = (getattr(self, "_ms_atr", {}) or {}).get(tk)
            closes = spy_closes if tk == "SPY" else self._MacroResidB1SessionCloses(tk)
            if not closes or atr is None:
                data_complete = False
                breadth_dd[tk] = None
                continue
            peak = max(closes)
            dd = resid_session_peak_dd_atr(peak=peak, close=closes[-1], atr=atr)
            if tk == "SPY":
                spy_dd = dd
            else:
                breadth_dd[tk] = dd
        spy_15m = resid_15m_return(spy_closes)
        if any(breadth_dd.get(s) is None for s in RESID_BREADTH):
            data_complete = False
        vix = self._MacroResidB1Vix()
        rv = macro_rv30(spy_closes[-30:]) if len(spy_closes) >= 30 else None
        pe = macro_path_efficiency(spy_closes[-30:]) if len(spy_closes) >= 30 else None
        de = macro_down_efficiency(spy_closes[-30:]) if len(spy_closes) >= 30 else None
        hist = list(self._resid_tod_hist.get(int(tod), []))
        rv_pct = macro_same_tod_percentile(rv, hist) if rv is not None else None
        feats = {
            "spy_dd_atr": spy_dd,
            "breadth_dd_atrs": {s: breadth_dd.get(s) for s in RESID_BREADTH},
            "spy_15m": spy_15m,
            "vix_stress": resid_vix_stress(vix),
            "rv_pct": rv_pct,
            "down_eff": de,
            "data_complete": data_complete,
        }
        variants = resid_eval_variants(feats) if data_complete else {v["id"]: False for v in RESID_VARIANTS}
        held = {}
        try:
            if hasattr(self, "GetCurrentWeights"):
                held = {str(k).upper(): float(v) for k, v in self.GetCurrentWeights().items()}
        except Exception:
            held = {}
        do = self.time.date().toordinal()
        row = {
            "do": do, "day": do, "tod": int(tod), "t": self.time, "ts": self.time,
            "stratum": stratum, "prot": prot, "variant_pass": variants, "features": feats,
            "held": held, "vix": vix, "rv": rv, "rv_pct": rv_pct, "path": pe, "down": de,
            "spy_dd_atr": spy_dd, "breadth_dd_atrs": breadth_dd, "regime": str(getattr(self, "current_regime", None) or "NEUTRAL"),
            "w2": 1 if prot.get("w2_active") else 0,
            "ids": str(prot.get("ids_state") or "NORMAL"),
            "panic": str(prot.get("panic_state") or "NORMAL"),
            "equity_gross": prot.get("equity_gross"),
            "signal_time": self.time,
        }
        self._resid_obs.append(row)
        self._resid_meta[(do, int(tod))] = row
        if rv is not None:
            self._resid_tod_hist[int(tod)].append(rv)
            if len(self._resid_tod_hist[int(tod)]) > 80:
                self._resid_tod_hist[int(tod)] = self._resid_tod_hist[int(tod)][-80:]

    def _MacroResidB1StoreFromFinalize(self, p, stats, spy, dur_mae, gold_mae, dur_ok, gold_ok,
                                      infl_ret, infl_rel, held_feat, br_maes, gold_source):
        if not getattr(self, "cg_macro_resid_b1_enable", False) or p.get("kind") == "PRE":
            return
        tod = int(p.get("tod", -1))
        if not (590 <= tod <= 900):
            return
        spy_mae = spy.get("mae") if spy else None
        pack = next((x for x in MACRO_TRUTH_PACKS if x["id"] == RESID_TRUTH_PACK), None)
        if not pack:
            return
        br_n = sum(1 for t in _D2_BREADTH if t in (br_maes or {}) and br_maes[t] <= -pack["B"])
        br_avail = sum(1 for t in _D2_BREADTH if t in (br_maes or {}))
        flags = d4_raw_flags(
            macro_truth_pack_to_d4(pack), spy_mae, br_n, br_avail, dur_mae, gold_mae,
            infl_rel, infl_ret, 0, 0, 0.0, 0.0, {},
        )
        label = d4_priority_macro(flags)
        key = (p["do"], tod)
        self._resid_truth_by_key[key] = label
        meta = self._resid_meta.get(key)
        if meta is not None:
            meta["truth_label"] = label
        if label in _TRUTH_FAMILY:
            self._resid_truth.append({"day": p["do"], "ts": p["t"], "label": label, "tod": tod})

    def _MacroResidB1ExitThreshold(self, sig_t, horizon):
        thr, rsn = resid_exit_threshold(sig_t, horizon, self._resid_spy_days)
        return thr, rsn

    def _MacroResidB1ResolveExitPrice(self, tk, sig_t, horizon):
        """Return entry/exit opens and EndTimes for one symbol/horizon."""
        out = {"entry_open": None, "entry_bar_end_time": None, "exit_open": None,
               "exit_bar_end_time": None, "exit_threshold_time": None, "valid": False, "reason": "INIT"}
        thr, rsn = self._MacroResidB1ExitThreshold(sig_t, horizon)
        out["exit_threshold_time"] = thr
        if thr is None:
            out["reason"] = rsn or "NO_THRESHOLD"
            return out
        ep, et = self._MacroResidB1NextOpen(tk, sig_t)
        out["entry_open"], out["entry_bar_end_time"] = ep, et
        if ep is None or et is None:
            out["reason"] = "MISSING_ENTRY"
            return out
        if horizon in ("H60", "HCLOSE"):
            xp, xt = self._MacroResidB1NextOpen(tk, thr)
        else:
            exit_day = thr.date().toordinal()
            cell = (self._resid_daily_1555.get(tk) or {}).get(exit_day)
            if isinstance(cell, dict):
                xp, xt = cell.get("open_price"), cell.get("bar_end_time")
            elif isinstance(cell, (tuple, list)) and len(cell) >= 2:
                xp, xt = cell[0], cell[1]
            else:
                xp, xt = None, None
        out["exit_open"], out["exit_bar_end_time"] = xp, xt
        if xp is None or xt is None:
            out["reason"] = "MISSING_EXIT"
            return out
        if et <= sig_t or xt <= thr:
            out["reason"] = "EARLY_OR_EQUAL"
            return out
        out["valid"] = True
        out["reason"] = "OK"
        return out

    def _MacroResidB1PriceEvent(self, sig_t, horizon):
        cache_key = (sig_t, horizon)
        if cache_key in self._resid_price_cache:
            return self._resid_price_cache[cache_key]
        thr, rsn = self._MacroResidB1ExitThreshold(sig_t, horizon)
        if thr is None:
            if rsn == "RIGHT_CENSORED":
                self._resid_ctr = resid_apply_price_counters(self._resid_ctr, {}, None, right_censored=True)
            self._resid_ctr.setdefault("rejected_missing_price_by_horizon", {})
            self._resid_ctr["rejected_missing_price_by_horizon"][horizon] = int(
                self._resid_ctr["rejected_missing_price_by_horizon"].get(horizon, 0)) + 1
            self._resid_price_cache[cache_key] = (None, {"censored": rsn == "RIGHT_CENSORED", "miss_exit": 1})
            return self._resid_price_cache[cache_key]
        symbol_prices = {}
        for tk in RESID_PXY5:
            r = self._MacroResidB1ResolveExitPrice(tk, sig_t, horizon)
            if r.get("valid"):
                symbol_prices[tk] = (r["entry_open"], r["entry_bar_end_time"], r["exit_open"], r["exit_bar_end_time"])
        br, info = resid_price_pxy5_detail(symbol_prices, sig_t, thr)
        self._resid_ctr = resid_apply_price_counters(self._resid_ctr, info, br)
        if br is None:
            self._resid_ctr.setdefault("rejected_missing_price_by_horizon", {})
            self._resid_ctr["rejected_missing_price_by_horizon"][horizon] = int(
                self._resid_ctr["rejected_missing_price_by_horizon"].get(horizon, 0)) + 1
        meta = {"censored": False, "miss_entry": int(info.get("miss_entry", 0) > 0),
                "miss_exit": int(info.get("miss_exit", 0) > 0), "priceable": br is not None}
        self._resid_price_cache[cache_key] = (br, meta)
        return br, meta

    def _MacroResidB1TruthHit(self, sig_t):
        end = sig_t + timedelta(minutes=60)
        idx = getattr(self, "_resid_truth_idx", None)
        if idx is None:
            rows = [ep for ep in (self._resid_truth or []) if ep.get("label") in _TRUTH_FAMILY and ep.get("ts") is not None]
            rows.sort(key=lambda x: x["ts"])
            self._resid_truth_idx = rows
            idx = rows
        if not idx:
            return 0
        ts_list = [ep["ts"] for ep in idx]
        lo = bisect.bisect_left(ts_list, sig_t)
        hi = bisect.bisect_right(ts_list, end)
        return 1 if lo < hi else 0

    def _MacroResidB1NavByDay(self):
        dates = list(getattr(self, "_sr_dates", []) or [])
        rets = list(getattr(self, "_sr_actual_rets", []) or [])
        nav, n = {}, 1.0
        for d, r in zip(dates, rets):
            try:
                do = d.toordinal() if hasattr(d, "toordinal") else int(d)
                n *= (1.0 + float(r))
                nav[do] = n
            except Exception:
                continue
        return nav

    def _MacroResidB1AggEvents(self, evs):
        xs = [float(e["benefit_2bps"]) for e in (evs or []) if e.get("benefit_2bps") is not None]
        z = 0.0
        if not xs:
            return {"n": 0, "mean_2bps": z, "median_2bps": z, "false_cut_rate": z,
                    "total_2bps": z, "total_5bps": z, "mean_excess_2bps": z,
                    "prod_d1_mean": z, "prod_d3_mean": z, "prod_d1_excess": z, "prod_d3_excess": z,
                    "truth_hit_rate": z, "hit_rate": z, "_events": list(evs or [])}
        b2 = sorted(xs)
        ex = [float(e["excess_2bps"]) for e in evs if e.get("excess_2bps") is not None]
        d1 = [float(e["prod_d1"]) for e in evs if e.get("prod_d1") is not None]
        d3 = [float(e["prod_d3"]) for e in evs if e.get("prod_d3") is not None]
        d1x = [float(e["prod_d1_excess"]) for e in evs if e.get("prod_d1_excess") is not None]
        d3x = [float(e["prod_d3_excess"]) for e in evs if e.get("prod_d3_excess") is not None]
        fc = [e for e in evs if e.get("false_cut")]
        th = [e for e in evs if e.get("truth_hit")]
        hr = [e for e in evs if float(e.get("benefit_2bps") or 0) > 0]
        return {
            "n": len(xs), "mean_2bps": sum(b2) / len(b2), "median_2bps": b2[len(b2) // 2],
            "false_cut_rate": len(fc) / len(evs) if evs else z,
            "total_2bps": sum(b2), "total_5bps": sum(float(e.get("benefit_5bps") or 0) for e in evs),
            "mean_excess_2bps": (sum(ex) / len(ex)) if ex else None,
            "prod_d1_mean": (sum(d1) / len(d1)) if d1 else None,
            "prod_d3_mean": (sum(d3) / len(d3)) if d3 else None,
            "prod_d1_excess": (sum(d1x) / len(d1x)) if d1x else None,
            "prod_d3_excess": (sum(d3x) / len(d3x)) if d3x else None,
            "truth_hit_rate": (len(th) / len(evs)) if evs else None,
            "hit_rate": (len(hr) / len(evs)) if evs else None,
            "_events": list(evs or []),
        }

    def _MacroResidB1BuildPriceIndex(self):
        self._resid_price_cache = {}
        self._resid_spy_days = sorted(self._resid_daily_1555.get("SPY") or {})
        self._resid_coverage_meta = []
        self._resid_detail_signals = []

    def _MacroResidB1AttachExcess(self, events):
        bl_by_var = {v["id"]: resid_select_baselines(self._resid_obs, v["id"]) for v in RESID_VARIANTS}
        bl_by_key = {vid: {b.get("baseline_key"): b for b in rows} for vid, rows in bl_by_var.items()}
        bl_cache = {}
        for vid, rows in bl_by_var.items():
            for b in rows:
                k = b.get("baseline_key")
                for hz in RESID_HORIZONS:
                    br, _meta = self._MacroResidB1PriceEvent(b["t"], hz)
                    if br is not None:
                        bl_cache[(vid, k, hz)] = resid_proxy_benefit(br, 2)
        nav = self._MacroResidB1NavByDay()
        for e in events:
            vid = e.get("variant")
            hz = e.get("horizon")
            wn = resid_window_for_day(e.get("day"))
            key = resid_baseline_keys(wn, e.get("regime", "NA"), resid_bucket(e.get("tod")), e.get("day"))
            b2 = bl_cache.get((vid, key, hz))
            if b2 is not None and e.get("benefit_2bps") is not None:
                e["excess_2bps"] = float(e["benefit_2bps"]) - float(b2)
            sig_day = int(e.get("day", 0))
            e["prod_d1"] = resid_prod_nav_return(nav, sig_day, 1)
            e["prod_d3"] = resid_prod_nav_return(nav, sig_day, 3)
            bl_row = (bl_by_key.get(vid) or {}).get(key)
            if bl_row is not None:
                bl_day = int(bl_row.get("day", sig_day))
                bl_d1 = resid_prod_nav_return(nav, bl_day, 1)
                bl_d3 = resid_prod_nav_return(nav, bl_day, 3)
                if e.get("prod_d1") is not None and bl_d1 is not None:
                    e["prod_d1_excess"] = float(bl_d1) - float(e["prod_d1"])
                if e.get("prod_d3") is not None and bl_d3 is not None:
                    e["prod_d3_excess"] = float(bl_d3) - float(e["prod_d3"])

    def _MacroResidB1BuildEvents(self):
        self._MacroResidB1BuildPriceIndex()
        candidates = []
        vmap = {v["id"]: v for v in RESID_VARIANTS}
        for obs in self._resid_obs:
            vp = obs.get("variant_pass") or {}
            for vid, fired in vp.items():
                if not fired:
                    continue
                vdef = vmap.get(vid, {})
                candidates.append({
                    "day": obs["do"], "tod": obs["tod"], "t": obs["t"], "variant": vid,
                    "signal_time": obs["t"], "stratum": obs.get("stratum"),
                    "severity": vdef.get("severity", "D30"), "combo": vdef.get("combo", "C0_BREADTH"),
                    "regime": obs.get("regime"), "w2": obs.get("w2"), "ids": obs.get("ids"),
                    "panic": obs.get("panic"), "equity_gross": obs.get("equity_gross"),
                    "spy_dd_atr": obs.get("spy_dd_atr"),
                    "breadth_dd": min((obs.get("breadth_dd_atrs") or {}).get(s, 0) or 0 for s in RESID_BREADTH),
                    "held": obs.get("held") or {},
                })
        decl = resid_decluster_events(candidates)
        events = []
        for cand in decl:
            sig_t = cand["signal_time"]
            wn = resid_window_for_day(cand["day"])
            wide = {
                "variant": cand["variant"], "stratum": cand.get("stratum"), "day": cand["day"],
                "signal_time": str(sig_t), "regime": cand.get("regime"), "w2": cand.get("w2"),
                "ids": cand.get("ids"), "panic": cand.get("panic"), "equity_gross": cand.get("equity_gross"),
                "h60_b2": "NA", "hclose_b2": "NA", "hnext_b2": "NA", "h3d_b2": "NA",
                "h60_ok": 0, "hclose_ok": 0, "hnext_ok": 0, "h3d_ok": 0,
            }
            for hz in RESID_HORIZONS:
                br, meta = self._MacroResidB1PriceEvent(sig_t, hz)
                priceable = br is not None
                self._resid_coverage_meta.append({
                    "variant": cand["variant"], "stratum": cand.get("stratum"), "horizon": hz,
                    "window": wn, "priceable": priceable,
                    "miss_entry": int(meta.get("miss_entry", 0)), "miss_exit": int(meta.get("miss_exit", 0)),
                    "censored": bool(meta.get("censored")),
                })
                col = hz.lower()
                if priceable:
                    b0 = resid_proxy_benefit(br, 0)
                    b2 = resid_proxy_benefit(br, 2)
                    b5 = resid_proxy_benefit(br, 5)
                    wide[f"{col}_b2"] = b2
                    wide[f"{col}_ok"] = 1
                    events.append({
                        **cand, "horizon": hz, "window": wn,
                        "benefit_0bps": b0, "benefit_2bps": b2, "benefit_5bps": b5,
                        "false_cut": int(br > 0), "truth_hit": self._MacroResidB1TruthHit(sig_t),
                    })
            self._resid_detail_signals.append(wide)
        self._MacroResidB1AttachExcess(events)
        return events

    def _MacroResidB1ComputePasses(self, events):
        passing = []
        for v in RESID_VARIANTS:
            vid = v["id"]
            r0 = [e for e in events if e.get("variant") == vid and e.get("stratum") == "R0_UNPROTECTED"]
            metrics = {}
            for hz in RESID_HORIZONS:
                metrics[hz] = {}
                hz_evs = [e for e in r0 if e.get("horizon") == hz]
                for wn, a, b in resid_windows():
                    win_evs = [e for e in hz_evs if a <= int(e.get("day", 0)) <= b]
                    metrics[hz][wn] = self._MacroResidB1AggEvents(win_evs)
            nbr = resid_neighbor_variant(vid)
            nbr_metrics = {}
            if nbr:
                nbr_r0 = [e for e in events if e.get("variant") == nbr and e.get("stratum") == "R0_UNPROTECTED"]
                nbr_metrics["H60"] = {}
                for wn, a, b in resid_windows():
                    win_evs = [e for e in nbr_r0 if e.get("horizon") == "H60" and a <= int(e.get("day", 0)) <= b]
                    nbr_metrics["H60"][wn] = self._MacroResidB1AggEvents(win_evs)
            gate = resid_pass_gate(metrics, nbr_metrics)
            if gate.get("pass"):
                passing.append({"id": vid, "severity": v["severity"], "combo": v["combo"],
                                "metrics": metrics, "pass": True, "reasons": gate.get("reasons") or []})
        return resid_rank_passers(passing), passing

    def _MacroResidB1EmitTier1(self, tier1, meta_map):
        """Emit only Tier-1 within 85KB budget; META includes sha256 and chunk counts."""
        chunk = 700
        emitted = 0
        for name, text in sorted((tier1 or {}).items()):
            mm = (meta_map or {}).get(name) or {}
            raw = str(text or "").encode("utf-8")
            z = zlib.compress(raw, 9)
            b64 = base64.b64encode(z).decode("ascii")
            n = max(1, (len(b64) + chunk - 1) // chunk)
            sha = mm.get("sha256") or ""
            self._MacroResidB1Log(
                f"CG_MACRO_RESID_B11_ART_META,name={name},bytes={len(raw)},zbytes={len(z)},"
                f"expected_chunks={n},emitted_chunks={n},truncated=NO,sha256={sha}"
            )
            for i in range(n):
                self._MacroResidB1Log(
                    f"CG_MACRO_RESID_B11_ART,name={name},i={i},n={n},b64={b64[i * chunk:(i + 1) * chunk]}"
                )
            emitted += 1
        return emitted

    def _MacroResidB1SaveTier2(self, tier2):
        saved, readback = 0, 0
        detail = {}
        for name, text in sorted((tier2 or {}).items()):
            ok_save = False
            try:
                if hasattr(self, "object_store") and self.object_store is not None:
                    try:
                        self.object_store.save(name, str(text))
                        ok_save = True
                    except Exception:
                        try:
                            self.object_store.save_bytes(name, str(text).encode("utf-8"))
                            ok_save = True
                        except Exception:
                            ok_save = False
            except Exception:
                ok_save = False
            rb = False
            if ok_save:
                saved += 1
                try:
                    got = self.object_store.read(name)
                    rb = (got is not None) and (str(got) == str(text) or (
                        hasattr(got, "decode") and got.decode("utf-8") == str(text)))
                except Exception:
                    rb = False
                if rb:
                    readback += 1
            detail[name] = {"objectstore_save_attempted": 1, "objectstore_save_ok": int(ok_save),
                            "objectstore_readback_ok": int(rb)}
        return saved, readback, detail

    def _MacroResidB1Identity(self):
        out = {}
        leds = getattr(self, "_sr_identity_leds", None) or {}
        cmp_fn = getattr(self, "CgShadowIdentityCompare", None)
        for label in ("MAISR_REPLAY_IDENTITY", "MAISR_PIPELINE_OFF_IDENTITY", "MAISR_SENSOR_NO_ACTION_IDENTITY"):
            led = leds.get(label) or {}
            if not cmp_fn:
                out[label] = {"pass": False, "n": 0}
                self._MacroResidB1Log(f"CG_MACRO_RESID_B11_IDENTITY_FINAL,id={label},pass=NO,identity_observed=NO")
                continue
            cmp = dict(cmp_fn(list(led.get("rets") or [])))
            passed = bool(cmp.get("pass"))
            out[label] = {"pass": passed, "n": cmp.get("n", 0), "nav_d": cmp.get("nav_d"),
                          "dd_d": cmp.get("dd_d"), "corr": cmp.get("corr")}
            self._MacroResidB1Log(
                f"CG_MACRO_RESID_B11_IDENTITY_FINAL,id={label},pass={'YES' if passed else 'NO'},"
                f"n={cmp.get('n',0)},nav_diff_pct={macro_mf(cmp.get('nav_d'),6)},"
                f"maxdd_diff_pp={macro_mf(cmp.get('dd_d'),6)},corr={macro_mf(cmp.get('corr'),6)}"
            )
        return out

    def _MacroResidB1SubType(self, tk):
        tk = str(tk).upper()
        try:
            for cfg in list(self.subscription_manager.subscriptions):
                if str(cfg.symbol.value).upper() != tk:
                    continue
                res = getattr(cfg, "resolution", None)
                if res == Resolution.MINUTE:
                    return "minute"
                if res == Resolution.DAILY:
                    return "daily"
        except Exception:
            pass
        return "none"

    def CgMacroResidB1OnEndOfAlgorithm(self, parity_ok) -> bool:
        if not getattr(self, "cg_macro_resid_b1_enable", False):
            return False
        try:
            if hasattr(self, "_D2FlushPending"):
                self._D2FlushPending()
        except Exception:
            self._resid_err += 1
            self._resid_ctr["err"] = int(self._resid_ctr.get("err", 0)) + 1
        id_results = self._MacroResidB1Identity()
        events = self._MacroResidB1BuildEvents()
        ranked, passing = self._MacroResidB1ComputePasses(events)
        subscription_events = [
            {"variant": e.get("variant"), "signal_time": e.get("signal_time"), "holdings": e.get("held") or {},
             "pass": any(p.get("id") == e.get("variant") for p in passing)}
            for e in events if e.get("stratum") == "R0_UNPROTECTED" and e.get("horizon") == "H60"
        ]
        symbol_sub_types = {}
        try:
            syms = set()
            for k in list(self.securities.keys()):
                syms.add(str(k.value if hasattr(k, "value") else k).upper())
            for tk in syms:
                symbol_sub_types[tk] = self._MacroResidB1SubType(tk)
        except Exception:
            self._resid_err += 1
        self._resid_ctr["diagnostic_real_orders"] = int(self._resid_real_orders or 0)
        self._resid_ctr["err"] = int(self._resid_err or 0)
        prot = self._MacroResidProtectionSnapshot()
        self._MacroResidB1Log(
            f"CG_MACRO_RESID_B11_PROTECTION_FINAL,valid={int(prot.get('valid',0))},"
            f"unresolved={self._resid_unresolved_prot},r0={self._resid_r0},r1={self._resid_r1},r2={self._resid_r2}"
        )
        # price coverage summary by horizon (RUN R0)
        cov_by_hz = {h: {"sig": 0, "ok": 0} for h in RESID_HORIZONS}
        for row in self._resid_coverage_meta:
            if row.get("stratum") != "R0_UNPROTECTED" or row.get("window") not in (
                    "TRAIN_2012_2018", "OOS_2019_2021", "CRISIS_2022_2025", "Y2020", "Y2022",
                    "Y2023", "Y2024", "Y2025", "LIVE_RECENT", "TRAIN_A_2012_2015", "TRAIN_B_2016_2018"):
                # still count all R0 for RUN-like aggregate across all windows via signals
                pass
            hz = row.get("horizon")
            if hz in cov_by_hz and row.get("stratum") == "R0_UNPROTECTED":
                cov_by_hz[hz]["sig"] += 1
                cov_by_hz[hz]["ok"] += int(bool(row.get("priceable")))
        parts = []
        for hz in RESID_HORIZONS:
            s, o = cov_by_hz[hz]["sig"], cov_by_hz[hz]["ok"]
            parts.append(f"{hz}={macro_mf((o / s) if s else 0, 4)}")
        self._MacroResidB1Log(f"CG_MACRO_RESID_B11_PRICE_COVERAGE_FINAL,{','.join(parts)}")
        for v in RESID_VARIANTS:
            n_fire = sum(1 for o in self._resid_obs if (o.get("variant_pass") or {}).get(v["id"]))
            self._MacroResidB1Log(f"CG_MACRO_RESID_B11_VARIANT_FINAL,id={v['id']},fires={n_fire}")
        self._MacroResidB1Log(
            f"CG_MACRO_RESID_B11_PASS_FINAL,passing={len(passing)},ranked={len(ranked)},"
            f"top={ranked[0]['id'] if ranked else 'NONE'}"
        )
        self._MacroResidB1Log(
            f"CG_MACRO_RESID_B11_SUBSCRIPTION_FINAL,events={len(subscription_events)},symbols={len(symbol_sub_types)}"
        )
        bid = self._MsBid() if hasattr(self, "_MsBid") else "NA"
        src = getattr(self, "cg_macro_resid_b1_source_commit", "") or ""
        out = resid_b11_finalize(
            self._resid_obs, id_results, parity_ok, self._resid_ctr, src, prot,
            events=events, coverage_meta=self._resid_coverage_meta, passing_variants=passing,
            bid=bid, subscription_events=subscription_events, symbol_sub_types=symbol_sub_types,
            detail_signals=self._resid_detail_signals,
        )
        t2_saved, t2_rb, t2_detail = self._MacroResidB1SaveTier2(out.get("tier2") or {})
        if out.get("transport", {}).get("ok") and out.get("art_ok") and out.get("tech_ok"):
            emitted = self._MacroResidB1EmitTier1(out.get("tier1") or {}, out.get("meta_map") or {})
        else:
            emitted = 0
            # still emit META-only failure markers for expected tier1 names
            for name in sorted((out.get("tier1") or {}).keys()):
                self._MacroResidB1Log(
                    f"CG_MACRO_RESID_B11_ART_META,name={name},bytes=0,zbytes=0,"
                    f"expected_chunks=0,emitted_chunks=0,truncated=YES,sha256=NONE"
                )
        fin = out["fin"]
        if t2_saved < 3 and out.get("art_ok") and out.get("tech_ok"):
            # Tier-2 ObjectStore failure does not invalidate when Tier-1 complete
            if isinstance(out.get("manifest"), dict):
                out["manifest"]["detail_status"] = "DETAIL_NOT_EXTERNALLY_RETRIEVED"
        self._MacroResidB1Log(
            f"CG_MACRO_RESID_B11_ARTIFACT_FINAL,tier1={len(out.get('tier1') or {})},"
            f"tier1_emitted={emitted},transport_ok={int(out['transport'].get('ok',0))},"
            f"tier2_saved={t2_saved},tier2_readback={t2_rb},"
            f"manifest_sha256={out.get('manifest_sha256')}"
        )
        self._MacroResidB1Log(
            f"CG_MACRO_RESID_B11_RECOMMENDATION,result={fin['result']},reason={fin['reason']},"
            f"next={fin['next']},research_conclusion={fin['research_conclusion']},"
            f"passing={len(passing)},subscription_hint={out.get('subscription_hint') or 'NONE'}"
        )
        self._resid_final = fin
        return True
