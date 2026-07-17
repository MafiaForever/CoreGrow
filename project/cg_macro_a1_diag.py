# cg_macro_a1_diag.py -- CG-MACRO-A1-FINAL-R1 LEAN mixin.
from AlgorithmImports import *
from collections import defaultdict
from datetime import timedelta, date
import base64, zlib
from cg_macro_a1_core import (
    MAISR_D4_CLOSEOUT, MACRO_TRUTH_PACKS,
    macro_vix_snapshot, macro_rv30, macro_path_efficiency, macro_down_efficiency,
    macro_same_tod_percentile, macro_mf, macro_filter_equity_basket,
    macro_defensive_blocks, macro_priced_basket_return, macro_a1_finalize_research,
    macro_truth_pack_to_d4, run_macro_a1_static_tests, run_macro_a1_eoa_dryrun,
    d4_raw_flags, d4_priority_macro, d4_validate_source_commit,
    _TRAIN0, _TRAIN1,
)

_MACRO_PANEL = ("SPY", "XLE", "XLB", "XLV", "XLU", "BND", "TIP", "GLD", "GLDM", "DBC", "SH")
_BREADTH4 = ("XLE", "XLB", "XLV", "XLU")
_OOS0, _OOS1 = date(2019, 1, 1).toordinal(), date(2021, 12, 31).toordinal()
_CR0, _CR1 = date(2022, 1, 1).toordinal(), date(2025, 12, 31).toordinal()


class CgMacroA1DiagMixin:
    """CG-MACRO-A1-FINAL-R1 online collection + EOA finalize."""

    def _MacroA1ReadParams(self, _p, _bool):
        self.cg_macro_a1_enable = _bool("cg_macro_a1_enable", "0")
        self.cg_macro_a1_source_commit = str(_p("cg_macro_a1_source_commit", "") or "").strip().lower()
        self.cg_macro_a1_export_detail = _bool("cg_macro_a1_export_detail", "1")

    def _MacroA1InitHooks(self):
        if not getattr(self, "cg_macro_a1_enable", False):
            return
        self._d2_mode = True
        self._macro_a1_obs, self._macro_a1_meta = [], {}
        self._macro_a1_tod_hist = defaultdict(list)
        self._macro_a1_data = {
            tk: {"accepted": 0, "dup": 0, "oo": 0, "first": None, "last": None,
                 "train_days": set(), "oos_days": set(), "crisis_days": set(), "last_et": None}
            for tk in _MACRO_PANEL
        }
        self._macro_a1_err = self._macro_a1_real_orders = self._macro_a1_art_used = 0
        self._macro_a1_future_vix = self._macro_a1_same_session_vix = 0
        self._macro_a1_fabricated_vix = self._macro_a1_vix_ts_unavail = 0
        self._macro_a1_same_bar = self._macro_a1_early_restore = 0
        self._macro_a1_missing_price = self._macro_a1_partial_basket = 0
        self._macro_a1_gold_primary = self._macro_a1_gold_fallback = self._macro_a1_gold_double = 0
        self._macro_a1_vix_cache = self._macro_a1_vix_cache_day = None
        co = MAISR_D4_CLOSEOUT
        self._MsLog(
            f"CG_MAISR_CLOSEOUT_FINAL,backtest_id={co['backtest_id']},"
            f"decision={co['decision']},reason={co['reason']},"
            f"held_a={co['subject_held_days_train_a']},held_b={co['subject_held_days_train_b']},"
            f"held_total={co['subject_held_days_total']}"
        )
        src = getattr(self, "cg_macro_a1_source_commit", "") or ""
        src_ok, src_rsn = d4_validate_source_commit(src)
        self._MsLog(
            f"CG_MACRO_A1_INIT,enable=1,source_commit={src or 'NONE'},"
            f"source_ok={int(src_ok)},detail={src_rsn},export={int(self.cg_macro_a1_export_detail)}"
        )
        _rows, p, n = run_macro_a1_static_tests()
        self._MsLog(f"CG_MACRO_A1_STATIC_FINAL,tests={p}/{n}")
        dry = run_macro_a1_eoa_dryrun()
        self._MsLog(str(dry))
        if p != n or not src_ok or "pass=7,fail=0" not in str(dry):
            self._macro_a1_err += 1

    def _MacroA1OnAcceptedBar(self, tk, et, o, h, l, c):
        if not getattr(self, "cg_macro_a1_enable", False) or tk not in self._macro_a1_data:
            return
        d = self._macro_a1_data[tk]
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

    def _MacroA1EquityBasket(self):
        try:
            held = self._D2HeldWeights() if hasattr(self, "_D2HeldWeights") else {}
        except Exception:
            held = {}
        return macro_filter_equity_basket(held)

    def _MacroA1Vix(self):
        sess = self.time.date()
        if self._macro_a1_vix_cache_day == sess and self._macro_a1_vix_cache is not None:
            return self._macro_a1_vix_cache
        rows = []
        try:
            if getattr(self, "vix", None) is None:
                self._macro_a1_vix_ts_unavail += 1
                snap = macro_vix_snapshot([], sess, lookback=252)
                self._macro_a1_vix_cache, self._macro_a1_vix_cache_day = snap, sess
                return snap
            h = self.history(self.vix, 320, Resolution.DAILY)
            if h is None or getattr(h, "empty", True) or "value" not in getattr(h, "columns", []):
                self._macro_a1_vix_ts_unavail += 1
                snap = macro_vix_snapshot([], sess, lookback=252)
                self._macro_a1_vix_cache, self._macro_a1_vix_cache_day = snap, sess
                return snap
            idx = h.index.get_level_values(-1)
            for d, v in zip(idx, h["value"].to_numpy(dtype=float)):
                dd = d.date() if hasattr(d, "date") else d
                # Reject future/same-session rows without counting as "use".
                if dd >= sess:
                    continue
                rows.append((dd, float(v)))
        except Exception:
            self._macro_a1_vix_ts_unavail += 1
            self._macro_a1_err += 1
            rows = []
        snap = macro_vix_snapshot(rows, sess, lookback=252)
        self._macro_a1_vix_cache, self._macro_a1_vix_cache_day = snap, sess
        return snap

    def _MacroA1SpyCloses(self, n=40):
        ring = (getattr(self, "_d2_bars", {}) or {}).get("SPY") or []
        return [float(c) for et, o, h, l, c in list(ring)[-n:] if c]

    def _MacroA1NextOpen(self, tk, after_t):
        ring = (getattr(self, "_d2_bars", {}) or {}).get(tk) or []
        for et, o, h, l, c in ring:
            if et is not None and et > after_t and o and float(o) > 0:
                return float(o), et
        return None, None

    def _MacroA1OnEval(self, kind, tod, states_bytes, feat):
        if not getattr(self, "cg_macro_a1_enable", False):
            return
        if kind != "POST" or not (590 <= int(tod) <= 900 and int(tod) % 5 == 0):
            return
        do = self.time.date().toordinal()
        closes = self._MacroA1SpyCloses(40)
        rv = macro_rv30(closes[-30:]) if len(closes) >= 30 else None
        pe = macro_path_efficiency(closes[-30:]) if len(closes) >= 30 else None
        de = macro_down_efficiency(closes[-30:]) if len(closes) >= 30 else None
        hist = list(self._macro_a1_tod_hist.get(int(tod), []))
        rv_pct = macro_same_tod_percentile(rv, hist) if rv is not None else None
        vix = self._MacroA1Vix()
        vix_stress = False
        if vix.get("valid"):
            pct, chg = vix.get("percentile_252"), vix.get("pct_change_1d")
            vix_stress = (pct is not None and pct >= 65.0) or (chg is not None and chg >= 0.10)
        basket = self._MacroA1EquityBasket()
        self._macro_a1_meta[(do, int(tod))] = {
            "t": self.time, "preds": bytes(states_bytes) if states_bytes else b"\x00" * 54,
            "vix": vix, "rv": rv, "rv_pct": rv_pct, "path": pe, "down": de,
            "vix_stress": vix_stress, "rv_stress": rv_pct is not None and rv_pct >= 70.0,
            "down_ok": de is not None and de >= 0.30,
            "vix_avail": bool(vix.get("valid")), "rv_avail": rv is not None and rv_pct is not None,
            "path_avail": pe is not None, "basket": basket,
            "rg": str(getattr(self, "current_regime", None) or "NEUTRAL"),
            "w2": 1 if getattr(self, "_cg_w2_last_active", False) else 0,
            "ids": str(getattr(self, "_ids_state", None) or "NORMAL"),
        }

    def _MacroA1StoreFromFinalize(self, p, stats, spy, dur_mae, gold_mae, dur_ok, gold_ok,
                                  infl_ret, infl_rel, held_feat, br_maes, gold_source):
        if not getattr(self, "cg_macro_a1_enable", False) or p.get("kind") == "PRE":
            return
        tod = int(p.get("tod", -1))
        if not (590 <= tod <= 900):
            return
        meta = self._macro_a1_meta.get((p["do"], tod)) or {}
        spy_mae = spy.get("mae") if spy else None
        spy_ret = spy.get("ret") if spy else 0.0
        blk = macro_defensive_blocks(stats, spy_ret)
        self._macro_a1_gold_primary += int(blk["gold_primary_count"])
        self._macro_a1_gold_fallback += int(blk["gold_fallback_count"])
        self._macro_a1_gold_double += int(blk["gold_double_count_used"])
        avail, resilient = blk["avail"], blk["resilient"]
        truth = {}
        for pack in MACRO_TRUTH_PACKS:
            B = pack["B"]
            br_n = sum(1 for t in _BREADTH4 if t in (br_maes or {}) and br_maes[t] <= -B)
            br_avail = sum(1 for t in _BREADTH4 if t in (br_maes or {}))
            flags = d4_raw_flags(
                macro_truth_pack_to_d4(pack), spy_mae, br_n, br_avail, dur_mae, gold_mae,
                infl_rel, infl_ret, len(resilient), len(avail), blk["med_abs"], blk["med_rel"], {},
            )
            truth[pack["id"]] = d4_priority_macro(flags)
        basket = meta.get("basket") or {}
        t0, rth = p["t"], p["t"] + timedelta(minutes=60)
        prices, miss = {}, []
        for tk, w in basket.items():
            cpx, ct = self._MacroA1NextOpen(tk, t0)
            rpx, rt = self._MacroA1NextOpen(tk, rth)
            if not (cpx and rpx and ct and rt):
                miss.append(tk)
                continue
            # NextOpen already requires EndTime > threshold; still audit misuse.
            if ct <= t0 or rt <= rth:
                if ct <= t0:
                    self._macro_a1_same_bar += 1
                if rt <= rth:
                    self._macro_a1_early_restore += 1
                miss.append(tk)
                continue
            prices[tk] = (cpx, ct, rpx, rt)
        sym_rows = {tk: (basket[tk], *prices[tk], t0, rth) for tk in prices}
        br, pw, miss2, sb, er = macro_priced_basket_return(sym_rows)
        # Rejected incomplete baskets increment missing_price only (not accepted).
        if basket and br is None:
            self._macro_a1_missing_price += 1
        elif br is not None and float(pw or 0) < 0.999999:
            self._macro_a1_partial_basket += 1
        cut_fill = min((v[1] for v in prices.values()), default=None)
        restore_fill = max((v[3] for v in prices.values()), default=None)
        rv = meta.get("rv")
        if rv is not None:
            self._macro_a1_tod_hist[tod].append(rv)
            if len(self._macro_a1_tod_hist[tod]) > 80:
                self._macro_a1_tod_hist[tod] = self._macro_a1_tod_hist[tod][-80:]
        self._macro_a1_obs.append({
            "do": p["do"], "tod": tod, "t": t0, "ts": t0, "day": p["do"],
            "preds": meta.get("preds") or p.get("preds"), "truth": truth, "spy_mae": spy_mae,
            "breadth_stressed_count": sum(1 for t in _BREADTH4 if t in (br_maes or {}) and br_maes[t] <= -0.60),
            "breadth_n": sum(1 for t in _BREADTH4 if t in (br_maes or {})),
            "dur_mae": dur_mae, "gold_mae": gold_mae, "infl_rel": infl_rel, "infl_abs": infl_ret,
            "def_resilient_n": len(resilient), "def_avail_n": len(avail),
            "med_def_abs": blk["med_abs"], "med_def_rel": blk["med_rel"],
            "vix": meta.get("vix") or {}, "rv": rv, "rv_pct": meta.get("rv_pct"),
            "path": meta.get("path"), "down": meta.get("down"),
            "vix_stress": meta.get("vix_stress", False), "rv_stress": meta.get("rv_stress", False),
            "down_ok": meta.get("down_ok", False), "vix_avail": meta.get("vix_avail", False),
            "rv_avail": meta.get("rv_avail", False), "path_avail": meta.get("path_avail", False),
            "basket": basket, "basket_ret": br, "priced_weight": pw,
            "missing_symbols": miss + list(miss2 or []), "prices": prices,
            "cut_time": cut_fill, "restore_fill_time": restore_fill, "restore_threshold_time": rth,
            "entry_delay_minutes": ((cut_fill - t0).total_seconds() / 60.0) if cut_fill else None,
            "restore_delay_minutes": ((restore_fill - rth).total_seconds() / 60.0) if restore_fill else None,
            "held": basket, "rg": meta.get("rg", p.get("rg")), "w2": meta.get("w2", p.get("w2")),
            "ids": meta.get("ids", p.get("ids")),
        })

    def _MacroA1EmitAll(self, arts, transport):
        if not transport.get("ok"):
            return False
        chunk, used, budget = 700, 0, 85000
        for name, text in sorted(arts.items()):
            raw = str(text or "").encode("utf-8")
            z = zlib.compress(raw, 9)
            b64 = base64.b64encode(z).decode("ascii")
            n = max(1, (len(b64) + chunk - 1) // chunk)
            lines = [
                f"CG_MACRO_A1_ART_META,name={name},bytes={len(raw)},zbytes={len(z)},"
                f"chunks={n},emitted={n},truncated=NO"
            ]
            for i in range(n):
                lines.append(f"CG_MACRO_A1_ART,name={name},i={i},n={n},b64={b64[i*chunk:(i+1)*chunk]}")
            need = sum(len(x) + 1 for x in lines)
            if used + need > budget:
                return False
            for line in lines:
                self._MsLog(line)
            used += need
        self._macro_a1_art_used = used
        return True

    def _MacroA1Identity(self):
        out = {}
        leds = getattr(self, "_sr_identity_leds", None) or {}
        cmp_fn = getattr(self, "CgShadowIdentityCompare", None)
        for label in (
            "MAISR_REPLAY_IDENTITY",
            "MAISR_PIPELINE_OFF_IDENTITY",
            "MAISR_SENSOR_NO_ACTION_IDENTITY",
        ):
            led = leds.get(label) or {}
            if not cmp_fn:
                out[label] = {"pass": False, "n": 0}
                self._MsLog(f"CG_MACRO_A1_IDENTITY_FINAL,id={label},pass=NO,identity_observed=NO")
                continue
            cmp = dict(cmp_fn(list(led.get("rets") or [])))
            passed = bool(cmp.get("pass"))
            out[label] = {"pass": passed, "n": cmp.get("n", 0), "nav_d": cmp.get("nav_d"),
                          "dd_d": cmp.get("dd_d"), "corr": cmp.get("corr")}
            self._MsLog(
                f"CG_MACRO_A1_IDENTITY_FINAL,id={label},pass={'YES' if passed else 'NO'},"
                f"n={cmp.get('n',0)},nav_diff_pct={macro_mf(cmp.get('nav_d'),6)},"
                f"maxdd_diff_pp={macro_mf(cmp.get('dd_d'),6)},corr={macro_mf(cmp.get('corr'),6)}"
            )
        return out

    def CgMacroA1OnEndOfAlgorithm(self, parity_ok) -> bool:
        if not getattr(self, "cg_macro_a1_enable", False):
            return False
        try:
            if hasattr(self, "_D2FlushPending"):
                self._D2FlushPending()
        except Exception:
            self._macro_a1_err += 1
        id_results = self._MacroA1Identity()
        obs = list(getattr(self, "_macro_a1_obs", []) or [])
        data_audit = {
            tk: {
                "accepted": d["accepted"], "dup": d["dup"], "oo": d["oo"],
                "first": d["first"], "last": d["last"],
                "train_days": len(d["train_days"]), "oos_days": len(d["oos_days"]),
                "crisis_days": len(d["crisis_days"]),
            } for tk, d in self._macro_a1_data.items()
        }
        counters = {
            "err": self._macro_a1_err, "real_orders": self._macro_a1_real_orders,
            "future_vix": self._macro_a1_future_vix, "same_session_vix": self._macro_a1_same_session_vix,
            "fabricated_vix": self._macro_a1_fabricated_vix, "same_bar": self._macro_a1_same_bar,
            "early_restore": self._macro_a1_early_restore, "missing_price": self._macro_a1_missing_price,
            "partial_basket": self._macro_a1_partial_basket, "gold_double": self._macro_a1_gold_double,
            "gold_primary": self._macro_a1_gold_primary, "gold_fallback": self._macro_a1_gold_fallback,
            "vix_ts_unavail": self._macro_a1_vix_ts_unavail, "transport_budget": 85000,
        }
        self._MsLog(
            f"CG_MACRO_A1_DATA_FINAL,obs={len(obs)},panel={len(_MACRO_PANEL)},"
            f"real_orders={counters['real_orders']},same_bar={counters['same_bar']},"
            f"early_restore={counters['early_restore']},partial={counters['partial_basket']},"
            f"missing_price={counters['missing_price']},gold_double={counters['gold_double']}"
        )
        vix_ok = sum(1 for r in obs if (r.get("vix") or {}).get("valid"))
        n_obs = max(len(obs), 1)
        self._MsLog(
            f"CG_MACRO_A1_VIX_FINAL,source=FRED:VIXCLS,valid_ratio={macro_mf(vix_ok/n_obs)},"
            f"future_vix_use_count={counters['future_vix']},"
            f"same_session_vix_use_count={counters['same_session_vix']},"
            f"fabricated_vix_date_count={counters['fabricated_vix']},"
            f"vix_timestamp_unavailable_count={counters['vix_ts_unavail']}"
        )
        bid = self._MsBid() if hasattr(self, "_MsBid") else "NA"
        src = getattr(self, "cg_macro_a1_source_commit", "") or ""
        out = macro_a1_finalize_research(obs, id_results, parity_ok, counters, data_audit, src, bid=bid)
        for pack in MACRO_TRUTH_PACKS:
            self._MsLog(f"CG_MACRO_A1_TRUTH_PACK_FINAL,id={pack['id']}")
        self._MsLog(f"CG_MACRO_A1_SELECTED_TRUTH,id={out.get('chosen') or 'NONE'}")
        for i, sid in enumerate((out.get("sel_ids") or [])[:6]):
            self._MsLog(f"CG_MACRO_A1_PREDICTOR_SELECTED,id={sid},rank={i+1}")
        best = out.get("best") or {}
        self._MsLog(
            f"CG_MACRO_A1_EVENT_FINAL,selected={len(out.get('sel_ids') or [])},"
            f"value_pass={out.get('value_pass_n',0)},best={best.get('id') or 'NONE'},"
            f"oos_mean2={macro_mf(best.get('oos'))}"
        )
        fin = out["fin"]
        emitted = self._MacroA1EmitAll(out["arts"], out["transport"])
        if not emitted and fin.get("result") != "FAILED":
            fin = {"result": "FAILED", "reason": "ARTIFACT_TRANSPORT_BUDGET_EXCEEDED",
                   "next": "FIX_MACRO_A1_IMPLEMENTATION", "research_conclusion": "NOT_REACHED"}
        self._MsLog(
            f"CG_MACRO_A1_ARTIFACT_FINAL,artifacts={len(out['arts'])},"
            f"manifest_sha256={out.get('manifest_sha256')},"
            f"transport_ok={int(out['transport'].get('ok',0))},emitted={int(emitted)}"
        )
        self._MsLog(
            f"CG_MACRO_A1_RECOMMENDATION,result={fin['result']},reason={fin['reason']},"
            f"next={fin['next']},research_conclusion={fin['research_conclusion']},"
            f"truth={out.get('chosen') or 'NONE'},predictors={len(out.get('sel_ids') or [])},"
            f"value_pass={out.get('value_pass_n',0)}"
        )
        self._macro_a1_final = fin
        self._macro_a1_best = best
        return True
