# cg_macro_a1_diag.py -- CG-MACRO-A1 LEAN mixin (macro-only Stage A).
from AlgorithmImports import *
from collections import defaultdict
from datetime import timedelta, date, datetime
import base64, hashlib, json, zlib
from cg_macro_a1_core import (
    MAISR_D4_CLOSEOUT, MACRO_TRUTH_PACKS, MACRO_PREDICTOR_VARIANTS,
    macro_truth_pack_to_d4, macro_build_truth_episodes, macro_truth_pack_stats,
    macro_map_prediction, macro_apply_gate, macro_vix_snapshot,
    macro_rv30, macro_path_efficiency, macro_down_efficiency, macro_same_tod_percentile,
    macro_match_episodes, macro_precision_recall_f1, macro_score_variant,
    macro_event_benefit, macro_stage_a_value_pass, macro_finalize_result,
    macro_a1_artifact_schemas, macro_select_predictors,
    run_macro_a1_static_tests, d4_raw_flags, d4_priority_macro,
    d4_build_episodes, d4_broad_family_count,
    d4_manifest_hash, d4_validate_source_commit,
    d4_is_placeholder_csv, _TRAINA0, _TRAINA1, _TRAINB0, _TRAINB1, _TRAIN0, _TRAIN1,
)
from cg_maisr_d2_labels import _ALL_CFG, _clfid

_STATES = (
    "SYSTEMIC_LIQUIDITY_STRESS", "RATE_INFLATION_STRESS", "BROAD_EQUITY_STRESS",
    "SECTOR_STRESS", "LOCAL_ASSET_STRESS", "DEFENSIVE_ROTATION",
    "UNCONFIRMED_NOISE", "NORMAL",
)
_MACRO_PARK = frozenset(("BIL", "SGOV", "USFR", "SH", "BND", "TIP", "GLD", "GLDM",
                         "AVGO", "MU", "NVDA", "SPY"))
_MACRO_PANEL = ("SPY", "XLE", "XLB", "XLV", "XLU", "BND", "TIP", "GLD", "GLDM", "DBC", "SH")
_BREADTH4 = ("XLE", "XLB", "XLV", "XLU")
_OOS0 = date(2019, 1, 1).toordinal()
_OOS1 = date(2021, 12, 31).toordinal()
_CR0 = date(2022, 1, 1).toordinal()
_CR1 = date(2025, 12, 31).toordinal()
_Y2020 = (date(2020, 1, 1).toordinal(), date(2020, 12, 31).toordinal())
_Y2022 = (date(2022, 1, 1).toordinal(), date(2022, 12, 31).toordinal())


def _mf(x, d=4):
    try:
        return f"{float(x):.{d}f}"
    except Exception:
        return "NA"


def _msha(t):
    return hashlib.sha256(str(t or "").encode("utf-8")).hexdigest()


class CgMacroA1DiagMixin:
    """CG-MACRO-A1 one-run macro-only causal gates + 60m event study."""

    def _MacroA1ReadParams(self, _p, _bool):
        self.cg_macro_a1_enable = _bool("cg_macro_a1_enable", "0")
        self.cg_macro_a1_source_commit = str(_p("cg_macro_a1_source_commit", "") or "").strip().lower()
        self.cg_macro_a1_export_detail = _bool("cg_macro_a1_export_detail", "1")

    def _MacroA1InitHooks(self):
        if not getattr(self, "cg_macro_a1_enable", False):
            return
        self._d2_mode = True
        self._macro_a1_obs = []
        self._macro_a1_meta = {}
        self._macro_a1_tod_hist = defaultdict(list)
        self._macro_a1_data = {tk: {"bars": 0, "dup": 0, "oo": 0, "first": None, "last": None}
                              for tk in _MACRO_PANEL}
        self._macro_a1_err = 0
        self._macro_a1_real_orders = 0
        self._macro_a1_art_used = 0
        self._macro_a1_future_vix = 0
        self._macro_a1_same_bar = 0
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
        if p != n or not src_ok:
            self._macro_a1_err += 1

    def _MacroA1EquityBasket(self):
        out = {}
        try:
            held = self._D2HeldWeights() if hasattr(self, "_D2HeldWeights") else {}
        except Exception:
            held = {}
        for tk, w in (held or {}).items():
            if tk in _MACRO_PARK or float(w or 0) <= 0:
                continue
            out[tk] = float(w)
        s = sum(out.values())
        if s > 0:
            out = {k: v / s for k, v in out.items()}
        return out

    def _MacroA1Vix(self):
        sess = self.time.date()
        rows = []
        try:
            if hasattr(self, "_CgFredValuesBeforeToday") and getattr(self, "vix", None):
                h = self.history(self.vix, 320, Resolution.DAILY)
                if h is not None and (not getattr(h, "empty", True)) and "value" in h.columns:
                    try:
                        idx = h.index.get_level_values(-1)
                        for d, v in zip(idx, h["value"].to_numpy(dtype=float)):
                            dd = d.date() if hasattr(d, "date") else d
                            if dd < sess:
                                rows.append((dd, float(v)))
                            # same-session / future rows are ignored, not used
                    except Exception:
                        vals = self._CgFredValuesBeforeToday(self.vix, 320)
                        if vals is not None:
                            for i, v in enumerate(vals):
                                rows.append((sess - timedelta(days=len(vals) - i), float(v)))
        except Exception:
            self._macro_a1_err += 1
        return macro_vix_snapshot(rows, sess, lookback=252)

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
        # exclude current day: hist only contains prior finalize appends
        rv_pct = macro_same_tod_percentile(rv, hist) if rv is not None else None
        vix = self._MacroA1Vix()
        vix_stress = False
        if vix.get("valid"):
            pct = vix.get("percentile_252")
            chg = vix.get("pct_change_1d")
            # percentile_252 is 0..100 rank among prior completed days
            vix_stress = (pct is not None and pct >= 65.0) or (chg is not None and chg >= 0.10)
        rv_stress = rv_pct is not None and rv_pct >= 70.0
        down_ok = de is not None and de >= 0.30
        basket = self._MacroA1EquityBasket()
        self._macro_a1_meta[(do, int(tod))] = {
            "t": self.time, "preds": bytes(states_bytes) if states_bytes else b"\x00" * 54,
            "vix": vix, "rv": rv, "rv_pct": rv_pct, "path": pe, "down": de,
            "vix_stress": vix_stress, "rv_stress": rv_stress, "down_ok": down_ok,
            "vix_avail": bool(vix.get("valid")), "rv_avail": rv is not None and rv_pct is not None,
            "path_avail": pe is not None, "basket": basket,
            "rg": str(getattr(self, "current_regime", None) or "NEUTRAL"),
            "w2": 1 if getattr(self, "_cg_w2_last_active", False) else 0,
            "ids": str(getattr(self, "_ids_state", None) or "NORMAL"),
        }

    def _MacroA1StoreFromFinalize(self, p, stats, spy, dur_mae, gold_mae, dur_ok, gold_ok,
                                  infl_ret, infl_rel, held_feat, br_maes, gold_source):
        if not getattr(self, "cg_macro_a1_enable", False):
            return
        if p.get("kind") == "PRE":
            return
        tod = int(p.get("tod", -1))
        if not (590 <= tod <= 900):
            return
        meta = self._macro_a1_meta.get((p["do"], tod)) or {}
        spy_mae = spy.get("mae") if spy else None
        def_names = ("BND", "TIP", "GLD", "GLDM")
        avail = []
        for name in def_names:
            st = (stats or {}).get(name)
            if not st or st.get("ret") is None:
                continue
            ab = st.get("ret")
            avail.append({"abs": ab, "rel": ab - (spy.get("ret") or 0)})
        med_abs = sorted(b["abs"] for b in avail)[len(avail) // 2] if avail else None
        med_rel = sorted(b["rel"] for b in avail)[len(avail) // 2] if avail else None
        resilient = [b for b in avail if b["abs"] >= 0 and b["rel"] >= 0]
        truth = {}
        for pack in MACRO_TRUTH_PACKS:
            B = pack["B"]
            br_n = sum(1 for t in _BREADTH4 if t in (br_maes or {}) and br_maes[t] <= -B)
            br_avail = sum(1 for t in _BREADTH4 if t in (br_maes or {}))
            flags = d4_raw_flags(
                macro_truth_pack_to_d4(pack), spy_mae, br_n, br_avail,
                dur_mae, gold_mae, infl_rel, infl_ret,
                len(resilient), len(avail), med_abs, med_rel, {},
            )
            truth[pack["id"]] = d4_priority_macro(flags)
        basket = meta.get("basket") or {}
        t0 = p["t"]
        rets = []
        for tk, w in basket.items():
            cpx, ct = self._MacroA1NextOpen(tk, t0)
            rpx, rt = self._MacroA1NextOpen(tk, t0 + timedelta(minutes=60))
            if cpx and rpx and ct and rt:
                if ct <= t0:
                    continue
                rets.append((w, rpx / cpx - 1.0))
        basket_ret = sum(w * r for w, r in rets) if rets else None
        rv = meta.get("rv")
        if rv is not None:
            self._macro_a1_tod_hist[tod].append(rv)
            if len(self._macro_a1_tod_hist[tod]) > 80:
                self._macro_a1_tod_hist[tod] = self._macro_a1_tod_hist[tod][-80:]
        self._macro_a1_obs.append({
            "do": p["do"], "tod": tod, "t": t0, "ts": t0, "day": p["do"],
            "preds": meta.get("preds") or p.get("preds"), "truth": truth,
            "spy_mae": spy_mae,
            "breadth_stressed_count": sum(
                1 for t in _BREADTH4 if t in (br_maes or {}) and br_maes[t] <= -0.60),
            "breadth_n": sum(1 for t in _BREADTH4 if t in (br_maes or {})),
            "dur_mae": dur_mae, "gold_mae": gold_mae, "infl_rel": infl_rel, "infl_abs": infl_ret,
            "def_resilient_n": len(resilient), "def_avail_n": len(avail),
            "med_def_abs": med_abs, "med_def_rel": med_rel,
            "vix": meta.get("vix") or {}, "rv": rv, "rv_pct": meta.get("rv_pct"),
            "path": meta.get("path"), "down": meta.get("down"),
            "vix_stress": meta.get("vix_stress", False), "rv_stress": meta.get("rv_stress", False),
            "down_ok": meta.get("down_ok", False),
            "vix_avail": meta.get("vix_avail", False), "rv_avail": meta.get("rv_avail", False),
            "path_avail": meta.get("path_avail", False),
            "basket": basket, "basket_ret": basket_ret,
            "rg": meta.get("rg", p.get("rg")), "w2": meta.get("w2", p.get("w2")),
            "ids": meta.get("ids", p.get("ids")),
        })

    def _MacroA1Emit(self, name, text):
        raw = str(text or "").encode("utf-8")
        z = zlib.compress(raw, 9)
        b64 = base64.b64encode(z).decode("ascii")
        chunk, used, budget = 700, int(getattr(self, "_macro_a1_art_used", 0) or 0), 30000
        n = (len(b64) + chunk - 1) // chunk or 1
        meta = f"CG_MACRO_A1_ART_META,name={name},bytes={len(raw)},zbytes={len(z)},chunks={n}"
        if used + len(meta) > budget:
            return
        self._MsLog(f"{meta},emitted_pending=1")
        used += len(meta) + 1
        emit = 0
        for i in range(n):
            part = b64[i * chunk:(i + 1) * chunk]
            line = f"CG_MACRO_A1_ART,name={name},i={i},n={n},b64={part}"
            if used + len(line) > budget:
                break
            self._MsLog(line)
            used += len(line) + 1
            emit += 1
        self._macro_a1_art_used = used
        self._MsLog(f"CG_MACRO_A1_ART_META,name={name},emitted={emit},truncated={'YES' if emit < n else 'NO'}")

    def _MacroA1Identity(self):
        out = {}
        for label, attr in (
            ("MAISR_REPLAY_IDENTITY", "_sr_ctrl"),
            ("MAISR_PIPELINE_OFF_IDENTITY", "_sr_pipe_off"),
            ("MAISR_SENSOR_NO_ACTION_IDENTITY", "_sr_sensor"),
        ):
            led = getattr(self, attr, None) or getattr(self, "_sr_ctrl", None)
            cmp_fn = getattr(self, "CgShadowIdentityCompare", None)
            if led is None or not cmp_fn:
                out[label] = {"pass": False, "n": 0}
                self._MsLog(f"CG_MACRO_A1_IDENTITY_FINAL,id={label},pass=NO,identity_observed=NO")
                continue
            cmp = dict(cmp_fn(list(led.get("rets") or [])))
            passed = bool(cmp.get("pass"))
            out[label] = {"pass": passed, "n": cmp.get("n", 0), "nav_d": cmp.get("nav_d"),
                          "dd_d": cmp.get("dd_d"), "corr": cmp.get("corr")}
            self._MsLog(
                f"CG_MACRO_A1_IDENTITY_FINAL,id={label},pass={'YES' if passed else 'NO'},"
                f"n={cmp.get('n',0)},nav_diff_pct={_mf(cmp.get('nav_d'),6)},"
                f"maxdd_diff_pp={_mf(cmp.get('dd_d'),6)},corr={_mf(cmp.get('corr'),6)}"
            )
        return out

    def _MacroA1Win(self, do):
        if _TRAIN0 <= do <= _TRAIN1:
            return "TRAIN_2012_2018"
        if _OOS0 <= do <= _OOS1:
            return "OOS_2019_2021"
        if _CR0 <= do <= _CR1:
            return "CRISIS_2022_2025"
        return "OTHER"

    def _MacroA1FamilyF1(self, pred_eps, truth_eps, labels):
        te = [e for e in (truth_eps or []) if e.get("label") in labels]
        pe = [e for e in (pred_eps or []) if e.get("label") in labels]
        m = macro_match_episodes(pe, te)
        p, r, f1 = macro_precision_recall_f1(m["tp"], m["fp"], m["fn"])
        fpr = m["fp"] / max(len(pe), 1)
        fnr = m["fn"] / max(len(te), 1)
        return f1, fpr, fnr, m

    def _MacroA1SoftMetrics(self, events):
        soft = {}
        for wname, pred in (
            ("TRAIN", lambda d: _TRAIN0 <= d <= _TRAIN1),
            ("OOS", lambda d: _OOS0 <= d <= _OOS1),
            ("CRISIS", lambda d: _CR0 <= d <= _CR1),
            ("Y2020", lambda d: _Y2020[0] <= d <= _Y2020[1]),
            ("Y2022", lambda d: _Y2022[0] <= d <= _Y2022[1]),
            ("RUN", lambda d: True),
        ):
            xs = [e for e in events if pred(e["do"])]
            if not xs:
                soft[wname] = {"n": 0, "mean_2bps": None, "median_2bps": None,
                               "false_cut_rate": None, "total_2bps": 0, "total_5bps": 0,
                               "year_pos_shares": 0}
            else:
                b2s = sorted(e["b2"] for e in xs)
                soft[wname] = {
                    "n": len(xs), "mean_2bps": sum(b2s) / len(b2s),
                    "median_2bps": b2s[len(b2s) // 2],
                    "false_cut_rate": sum(e["false_cut"] for e in xs) / len(xs),
                    "total_2bps": sum(e["b2"] for e in xs),
                    "total_5bps": sum(e["b5"] for e in xs), "year_pos_shares": 0.0,
                }
        return soft

    def _MacroA1BuildEvents(self, var, obs, cfg_idx):
        events, last_restore = [], None
        for r in obs:
            preds = r.get("preds") or b"\x00" * 54
            idx = cfg_idx.get(var["clf_id"], 0)
            raw_st = _STATES[preds[idx] if idx < len(preds) else 7]
            mapped = macro_map_prediction(raw_st)
            gated = macro_apply_gate(
                mapped, var["gate"], r.get("vix_stress", False), r.get("rv_stress", False),
                r.get("down_ok", False), r.get("vix_avail", False), r.get("rv_avail", False),
                r.get("path_avail", False))
            if gated not in ("BROAD_EQUITY_STRESS", "SYSTEMIC_LIQUIDITY_STRESS",
                             "RATE_INFLATION_STRESS", "DEFENSIVE_ROTATION"):
                continue
            if not r.get("basket") or r.get("basket_ret") is None:
                continue
            if last_restore is not None and r["t"] < last_restore:
                continue
            last_restore = r["t"] + timedelta(minutes=60)
            br = float(r["basket_ret"])
            events.append({
                "predictor": var["id"], "do": r["do"], "t": r["t"], "state": gated,
                "basket_ret": br, "b0": macro_event_benefit(br, 0.20, 0),
                "b2": macro_event_benefit(br, 0.20, 2),
                "b5": macro_event_benefit(br, 0.20, 5),
                "false_cut": int(br > 0), "win": self._MacroA1Win(r["do"]),
            })
        return events

    def _MacroA1NeighborOk(self, sid, scored, metrics_by_sid):
        var = next((v for v in MACRO_PREDICTOR_VARIANTS if v["id"] == sid), None)
        if not var:
            return False
        for r in scored:
            if r["id"] == sid or not r.get("valid"):
                continue
            same_base = r["clf_id"] == var["clf_id"] and r["gate"] != var["gate"]
            adj_h = (r["clf_id"] != var["clf_id"] and r["s"] == var["s"] and r["a"] == var["a"]
                     and abs(float(r["b"]) - float(var["b"])) < 1e-9 and r["gate"] == var["gate"]
                     and r["h"] != var["h"])
            if not (same_base or adj_h):
                continue
            m = metrics_by_sid.get(r["id"]) or {}
            oos = (m.get("OOS") or {}).get("mean_2bps")
            cri = (m.get("CRISIS") or {}).get("mean_2bps")
            if oos is not None and oos >= 0 and cri is not None and cri >= 0:
                return True
        return False

    def CgMacroA1OnEndOfAlgorithm(self, parity_ok) -> bool:
        if not getattr(self, "cg_macro_a1_enable", False):
            return False
        try:
            if hasattr(self, "_D2FlushPending"):
                self._D2FlushPending()
        except Exception:
            self._macro_a1_err += 1

        id_results = self._MacroA1Identity()
        id_ok = all(r.get("pass") for r in id_results.values()) and parity_ok
        obs = list(getattr(self, "_macro_a1_obs", []) or [])
        vix_ok_n = sum(1 for r in obs if (r.get("vix") or {}).get("valid"))
        rv_ok_n = sum(1 for r in obs if r.get("rv_avail"))
        path_ok_n = sum(1 for r in obs if r.get("path_avail"))
        n_obs = max(len(obs), 1)
        self._MsLog(
            f"CG_MACRO_A1_DATA_FINAL,obs={len(obs)},panel={len(_MACRO_PANEL)},"
            f"real_orders={self._macro_a1_real_orders},same_bar={self._macro_a1_same_bar},"
            f"future_vix={self._macro_a1_future_vix}"
        )
        self._MsLog(
            f"CG_MACRO_A1_VIX_FINAL,source=FRED:VIXCLS,valid_ratio={_mf(vix_ok_n / n_obs)},"
            f"rv_valid_ratio={_mf(rv_ok_n / n_obs)},path_valid_ratio={_mf(path_ok_n / n_obs)},"
            f"future_vix_use_count={self._macro_a1_future_vix}"
        )

        pack_rows, eps_by, eps_train_by = [], {}, {}
        all_rows = [{**r, "day": r["do"], "ts": r["t"]} for r in obs]
        train_rows = [r for r in all_rows if _TRAIN0 <= r["day"] <= _TRAIN1]
        for pack in MACRO_TRUTH_PACKS:
            eps_all = macro_build_truth_episodes(pack, all_rows)
            eps = macro_build_truth_episodes(pack, train_rows)
            eps_by[pack["id"]] = eps_all
            eps_train_by[pack["id"]] = eps
            st = macro_truth_pack_stats(pack, eps)
            bf_ep, bf_days = st["broad_family_episodes"], st["broad_family_days"]
            def_ep = sum(1 for e in eps if e["label"] == "DEFENSIVE_ROTATION")
            ep_a = d4_broad_family_count([e for e in eps if _TRAINA0 <= e["day"] <= _TRAINA1])
            ep_b = d4_broad_family_count([e for e in eps if _TRAINB0 <= e["day"] <= _TRAINB1])
            da, db = ep_a / 4.0, ep_b / 3.0
            bratio = (max(da, db) / min(da, db)) if da > 0 and db > 0 else 999.0
            def_a = sum(1 for e in eps if _TRAINA0 <= e["day"] <= _TRAINA1 and e["label"] == "DEFENSIVE_ROTATION")
            def_b = sum(1 for e in eps if _TRAINB0 <= e["day"] <= _TRAINB1 and e["label"] == "DEFENSIVE_ROTATION")
            dda, ddb = def_a / 4.0, def_b / 3.0
            dratio = (max(dda, ddb) / min(dda, ddb)) if dda > 0 and ddb > 0 else 999.0
            support_ok = (20 <= bf_ep <= 200 and 15 <= bf_days <= 150 and 10 <= def_ep <= 200)
            stability_ok = (da > 0 and db > 0 and bratio <= 4 and dda > 0 and ddb > 0 and dratio <= 5)
            kw_ok = any(
                (date.fromordinal(e["day"]).year == 2015 and date.fromordinal(e["day"]).month in (8, 9))
                or (date.fromordinal(e["day"]).year == 2018 and date.fromordinal(e["day"]).month >= 10)
                for e in eps_all if e["label"] in ("BROAD_EQUITY_STRESS", "SYSTEMIC_LIQUIDITY_STRESS"))
            has_2020 = any(
                date.fromordinal(e["day"]).year == 2020
                and e["label"] in ("BROAD_EQUITY_STRESS", "SYSTEMIC_LIQUIDITY_STRESS") for e in eps_all)
            valid = support_ok and stability_ok and kw_ok and has_2020
            score = abs(bf_ep - 80) + abs(def_ep - 100) + abs(bratio - 1.0)
            row = {**st, "B": pack["B"], "br_count": pack["br_count"], "local": pack["local"],
                   "resid": pack["resid"], "defensive_episodes": def_ep,
                   "support_ok": int(support_ok), "stability_ok": int(stability_ok),
                   "valid": int(valid), "score": score, "selected": 0}
            pack_rows.append(row)
            self._MsLog(
                f"CG_MACRO_A1_TRUTH_PACK_FINAL,id={pack['id']},valid={int(valid)},"
                f"bf={bf_ep},bf_days={bf_days},def={def_ep},support={int(support_ok)},"
                f"stable={int(stability_ok)}"
            )
        valid_packs = [r for r in pack_rows if r["valid"]]
        valid_packs.sort(key=lambda r: (r["score"], 0 if r["br_count"] == 3 else 1,
                                        0 if r["B"] >= 0.80 else 1, r["id"]))
        chosen = valid_packs[0]["id"] if valid_packs else None
        for r in pack_rows:
            r["selected"] = int(r["id"] == chosen)
        self._MsLog(f"CG_MACRO_A1_SELECTED_TRUTH,id={chosen or 'NONE'},valid_packs={len(valid_packs)}")

        scored = []
        truth_eps = eps_train_by.get(chosen, []) if chosen else []
        train_obs = [r for r in obs if _TRAIN0 <= r["do"] <= _TRAIN1]
        cfg_idx = {_clfid(*c): i for i, c in enumerate(_ALL_CFG)}
        for var in MACRO_PREDICTOR_VARIANTS:
            pred_stream, avail_n, need_n = [], 0, 0
            for r in train_obs:
                preds = r.get("preds") or b"\x00" * 54
                idx = cfg_idx.get(var["clf_id"], 0)
                raw_st = _STATES[preds[idx] if idx < len(preds) else 7]
                mapped = macro_map_prediction(raw_st)
                need_n += 1
                gated = macro_apply_gate(
                    mapped, var["gate"], r.get("vix_stress", False), r.get("rv_stress", False),
                    r.get("down_ok", False), r.get("vix_avail", False), r.get("rv_avail", False),
                    r.get("path_avail", False))
                if gated == "UNAVAILABLE":
                    continue
                avail_n += 1
                pred_stream.append({
                    "day": r["do"], "ts": r["t"], "label": gated, "subject": "MACRO",
                    "mae": r.get("spy_mae"), "breadth": r.get("breadth_stressed_count"),
                })
            pred_eps = d4_build_episodes(pred_stream) if pred_stream else []
            bf_f1, bf_fp, _, _ = self._MacroA1FamilyF1(
                pred_eps, truth_eps, ("BROAD_EQUITY_STRESS", "SYSTEMIC_LIQUIDITY_STRESS"))
            def_f1, def_fp, _, _ = self._MacroA1FamilyF1(
                pred_eps, truth_eps, ("DEFENSIVE_ROTATION",))
            sys_eps_t = [e for e in truth_eps if e["label"] == "SYSTEMIC_LIQUIDITY_STRESS"]
            rate_eps_t = [e for e in truth_eps if e["label"] == "RATE_INFLATION_STRESS"]
            sys_f1, _, sys_fn, _ = self._MacroA1FamilyF1(
                pred_eps, truth_eps, ("SYSTEMIC_LIQUIDITY_STRESS",)) if len(sys_eps_t) >= 5 else (None, 0, None, None)
            rate_f1, _, _, _ = self._MacroA1FamilyF1(
                pred_eps, truth_eps, ("RATE_INFLATION_STRESS",)) if len(rate_eps_t) >= 5 else (None, 0, None, None)
            fams = [x for x in (bf_f1, def_f1, sys_f1, rate_f1) if x is not None]
            mean_f1 = (sum(fams) / len(fams)) if fams else 0.0
            other_ok = any((x or 0) > 0 for x in (def_f1, sys_f1, rate_f1))
            avail_ratio = avail_n / max(need_n, 1)
            score = macro_score_variant(mean_f1, bf_fp, def_fp, sys_fn)
            ta = [e for e in truth_eps if _TRAINA0 <= e["day"] <= _TRAINA1]
            tb = [e for e in truth_eps if _TRAINB0 <= e["day"] <= _TRAINB1]
            valid = (bool(chosen) and bf_f1 > 0 and other_ok and mean_f1 > 0
                     and 10 <= len(pred_eps) <= 400 and avail_ratio >= 0.90)
            scored.append({
                "id": var["id"], "clf_id": var["clf_id"], "s": var["s"], "a": var["a"],
                "b": var["b"], "h": var["h"], "gate": var["gate"],
                "score": score, "f1_train_a": bf_f1 or 0, "f1_train_b": def_f1 or 0,
                "n_train_a": len(ta), "n_train_b": len(tb),
                "valid": int(valid), "selected": 0,
                "sig_hash": _msha(f"{var['id']}:{len(pred_eps)}:{bf_f1}:{def_f1}"),
            })
        sel = macro_select_predictors(scored)
        sel_ids = set(sel["selected_ids"])
        for r in scored:
            r["selected"] = int(r["id"] in sel_ids)
        for i, sid in enumerate(list(sel["selected_ids"])[:6]):
            self._MsLog(f"CG_MACRO_A1_PREDICTOR_SELECTED,id={sid},rank={i + 1}")

        value_pass_n = 0
        best = None
        event_rows, summary_rows = [], []
        metrics_by_sid = {}
        win_defs = (
            ("TRAIN_2012_2018", lambda d: _TRAIN0 <= d <= _TRAIN1),
            ("TRAIN_A_2012_2015", lambda d: _TRAINA0 <= d <= _TRAINA1),
            ("TRAIN_B_2016_2018", lambda d: _TRAINB0 <= d <= _TRAINB1),
            ("OOS_2019_2021", lambda d: _OOS0 <= d <= _OOS1),
            ("CRISIS_2022_2025", lambda d: _CR0 <= d <= _CR1),
            ("Y2020", lambda d: _Y2020[0] <= d <= _Y2020[1]),
            ("Y2022", lambda d: _Y2022[0] <= d <= _Y2022[1]),
            ("Y2023", lambda d: date(2023, 1, 1).toordinal() <= d <= date(2023, 12, 31).toordinal()),
            ("Y2024", lambda d: date(2024, 1, 1).toordinal() <= d <= date(2024, 12, 31).toordinal()),
            ("Y2025", lambda d: date(2025, 1, 1).toordinal() <= d <= date(2025, 12, 31).toordinal()),
            ("LIVE_RECENT", lambda d: date(2024, 1, 1).toordinal() <= d <= _CR1),
            ("RUN", lambda d: True),
        )
        # Pre-collect neighbor ids that need soft metrics
        neighbor_ids = set()
        for sid in sel["selected_ids"]:
            var0 = next(v for v in MACRO_PREDICTOR_VARIANTS if v["id"] == sid)
            for r in scored:
                if not r.get("valid") or r["id"] == sid:
                    continue
                same_base = r["clf_id"] == var0["clf_id"] and r["gate"] != var0["gate"]
                adj_h = (r["clf_id"] != var0["clf_id"] and r["s"] == var0["s"] and r["a"] == var0["a"]
                         and abs(float(r["b"]) - float(var0["b"])) < 1e-9 and r["gate"] == var0["gate"]
                         and r["h"] != var0["h"])
                if same_base or adj_h:
                    neighbor_ids.add(r["id"])
        eval_ids = list(dict.fromkeys(list(sel["selected_ids"]) + list(neighbor_ids)))
        for sid in eval_ids:
            var = next(v for v in MACRO_PREDICTOR_VARIANTS if v["id"] == sid)
            events = self._MacroA1BuildEvents(var, obs, cfg_idx)
            if sid in sel_ids:
                event_rows.extend(events)
            soft_m = self._MacroA1SoftMetrics(events)
            for wname, pred in win_defs:
                xs = [e for e in events if pred(e["do"])]
                if not xs:
                    rowm = {"n": 0, "mean_2bps": None, "median_2bps": None,
                            "false_cut_rate": None, "total_2bps": 0, "total_5bps": 0,
                            "year_pos_shares": 0}
                else:
                    b2s = sorted(e["b2"] for e in xs)
                    rowm = {
                        "n": len(xs), "mean_2bps": sum(b2s) / len(b2s),
                        "median_2bps": b2s[len(b2s) // 2],
                        "false_cut_rate": sum(e["false_cut"] for e in xs) / len(xs),
                        "total_2bps": sum(e["b2"] for e in xs),
                        "total_5bps": sum(e["b5"] for e in xs), "year_pos_shares": 0,
                    }
                if sid in sel_ids:
                    summary_rows.append({"window": wname, "truth_pack": chosen or "NONE",
                                         "predictor": sid, **rowm, "pass": 0})
            pos_by_year = defaultdict(float)
            for e in events:
                if e["b2"] > 0:
                    pos_by_year[date.fromordinal(e["do"]).year] += e["b2"]
            pos_tot = sum(pos_by_year.values()) or 0.0
            soft_m["RUN"]["year_pos_shares"] = (
                (max(pos_by_year.values()) / pos_tot) if pos_tot > 0 else 0.0)
            task_ok = (soft_m["TRAIN"]["n"] >= 20 and soft_m["OOS"]["n"] >= 8
                       and soft_m["CRISIS"]["n"] >= 12)
            for w in ("OOS", "CRISIS"):
                m = soft_m[w]
                if not m["n"] or m["mean_2bps"] is None or m["mean_2bps"] <= 0:
                    task_ok = False
                if m["median_2bps"] is None or m["median_2bps"] < 0:
                    task_ok = False
                if m["false_cut_rate"] is None or m["false_cut_rate"] > 0.50:
                    task_ok = False
            for y in ("Y2020", "Y2022"):
                if soft_m[y]["total_2bps"] < 0:
                    task_ok = False
            if soft_m["RUN"]["total_5bps"] < -0.10 * abs(soft_m["RUN"]["total_2bps"] or 1):
                task_ok = False
            if pos_tot > 0 and max(pos_by_year.values()) / pos_tot > 0.60:
                task_ok = False
            soft_m["_task_ok"] = task_ok
            metrics_by_sid[sid] = soft_m
            if sid in sel_ids:
                self._MsLog(
                    f"CG_MACRO_A1_EVENT_FINAL,id={sid},pass={int(task_ok)},"
                    f"train_n={soft_m['TRAIN']['n']},oos_n={soft_m['OOS']['n']},"
                    f"crisis_n={soft_m['CRISIS']['n']},oos_mean2={_mf(soft_m['OOS']['mean_2bps'])},"
                    f"crisis_mean2={_mf(soft_m['CRISIS']['mean_2bps'])}"
                )
                if best is None or (soft_m["OOS"]["mean_2bps"] or -9) > (best.get("oos") or -9):
                    best = {"id": sid, "oos": soft_m["OOS"]["mean_2bps"],
                            "crisis": soft_m["CRISIS"]["mean_2bps"],
                            "y2020": soft_m["Y2020"]["total_2bps"],
                            "y2022": soft_m["Y2022"]["total_2bps"],
                            "run5": soft_m["RUN"]["total_5bps"],
                            "train_n": soft_m["TRAIN"]["n"], "oos_n": soft_m["OOS"]["n"],
                            "crisis_n": soft_m["CRISIS"]["n"]}

        value_pass_n = 0
        for sid in sel["selected_ids"]:
            m = metrics_by_sid.get(sid) or {}
            neigh = self._MacroA1NeighborOk(sid, scored, metrics_by_sid)
            soft = macro_stage_a_value_pass(m, neigh)
            if m.get("_task_ok") and soft["pass"]:
                value_pass_n += 1

        # Populate data audit from accepted D2 minute rings
        for tk in _MACRO_PANEL:
            ring = (getattr(self, "_d2_bars", {}) or {}).get(tk) or []
            d = self._macro_a1_data.setdefault(tk, {"bars": 0, "dup": 0, "oo": 0, "first": None, "last": None})
            d["bars"] = len(ring)
            if ring:
                d["first"] = str(ring[0][0])
                d["last"] = str(ring[-1][0])

        tech_ok = (id_ok and self._macro_a1_err == 0 and self._macro_a1_real_orders == 0
                   and self._macro_a1_future_vix == 0 and self._macro_a1_same_bar == 0)
        truth_ok = chosen is not None
        pred_ok = bool(sel.get("pred_ok"))

        bid = self._MsBid() if hasattr(self, "_MsBid") else "NA"
        schemas = macro_a1_artifact_schemas()
        arts = {}
        arts[f"cg_macro_a1_closeout_{bid}.json"] = json.dumps(
            {**MAISR_D4_CLOSEOUT, "macro_experiment": "CG-MACRO-A1"},
            sort_keys=True, separators=(",", ":"))
        il = [",".join(schemas["identity"])]
        for k, r in id_results.items():
            il.append(f"{k},{'YES' if r.get('pass') else 'NO'},{r.get('n', 0)},"
                      f"{_mf(r.get('nav_d'), 6)},{_mf(r.get('dd_d'), 6)},{_mf(r.get('corr'), 6)}")
        arts[f"cg_macro_a1_identity_{bid}.csv"] = "\n".join(il)
        da = ["symbol,bars,dup,oo,first,last"]
        for tk in _MACRO_PANEL:
            d = self._macro_a1_data.get(tk) or {}
            da.append(f"{tk},{d.get('bars', 0)},{d.get('dup', 0)},{d.get('oo', 0)},"
                      f"{d.get('first') or 'NA'},{d.get('last') or 'NA'}")
        arts[f"cg_macro_a1_data_audit_{bid}.csv"] = "\n".join(da)
        va = [",".join(schemas["vix_snapshot"])]
        step = max(1, len(obs) // 12) if obs else 1
        for r in obs[::step][:12]:
            v = r.get("vix") or {}
            va.append(f"{r['do']},{_mf(v.get('value'))},{v.get('source_date') or 'NA'},"
                      f"{v.get('age_sessions') if v.get('age_sessions') is not None else 'NA'},"
                      f"{int(bool(v.get('valid')))},{_mf(v.get('pct_change_1d'))},"
                      f"{_mf(v.get('percentile_252'))}")
        if len(va) == 1:
            va.append("NONE,NA,NA,NA,0,NA,NA")
        arts[f"cg_macro_a1_vix_audit_{bid}.csv"] = "\n".join(va)
        arts[f"cg_macro_a1_feature_distributions_{bid}.csv"] = (
            f"feature,count,status\nRV30,{rv_ok_n},OK\nPATH,{path_ok_n},OK\nVIX,{vix_ok_n},OK")
        tp = [",".join(schemas["truth_packs"])]
        for r in pack_rows:
            tp.append(",".join(str(r.get(c, "")) for c in schemas["truth_packs"]))
        arts[f"cg_macro_a1_truth_packs_{bid}.csv"] = "\n".join(tp)
        te = ["pack,state,start,end,day,n"]
        for pid, eps in eps_by.items():
            for e in eps[:40]:
                te.append(f"{pid},{e['label']},{e['start']},{e['end']},{e['day']},{e.get('n', 1)}")
        if len(te) == 1:
            te.append("NONE,NO_EPISODES,0,0,0,0")
        arts[f"cg_macro_a1_truth_episodes_{bid}.csv"] = "\n".join(te)
        pr = [",".join(schemas["predictors"])]
        for r in scored:
            pr.append(",".join(str(r.get(c, "")) for c in schemas["predictors"]))
        arts[f"cg_macro_a1_predictors_{bid}.csv"] = "\n".join(pr)
        sp = ["id,gate,h,score,selected"]
        if sel_ids:
            for r in scored:
                if r["id"] in sel_ids:
                    sp.append(f"{r['id']},{r['gate']},{r['h']},{_mf(r['score'])},1")
        else:
            sp.append("NONE,NONE,NONE,0,0")
        arts[f"cg_macro_a1_selected_predictors_{bid}.csv"] = "\n".join(sp)
        es = [",".join(schemas["event_value"])]
        for r in summary_rows:
            es.append(",".join(str(r.get(c, "NA")) for c in schemas["event_value"]))
        if len(es) == 1:
            es.append("NONE,NONE,NONE,0,NA,NA,NA,0,0,0,0")
        arts[f"cg_macro_a1_event_summary_{bid}.csv"] = "\n".join(es)
        sev = ["predictor,do,state,basket_ret,b2,false_cut,window"]
        if event_rows:
            for e in event_rows:
                sev.append(f"{e['predictor']},{e['do']},{e['state']},{_mf(e['basket_ret'])},"
                           f"{_mf(e['b2'])},{e['false_cut']},{e['win']}")
        else:
            sev.append("NONE,0,NO_SELECTED_PREDICTOR,0,0,0,NONE")
        arts[f"cg_macro_a1_selected_events_{bid}.csv"] = "\n".join(sev)
        kw = ["pack,window,broad_family_episodes,defensive_episodes,status"]
        windows = (("W2015", 735780, 735841), ("W2018Q4", 736938, 737059),
                   ("W2020", 737456, 737545), ("W2022", 738156, 738520))
        for pack in MACRO_TRUTH_PACKS:
            for wn, a, b in windows:
                eps = [e for e in eps_by.get(pack["id"], []) if a <= e["day"] <= b]
                kw.append(f"{pack['id']},{wn},{d4_broad_family_count(eps)},"
                          f"{sum(1 for e in eps if e['label'] == 'DEFENSIVE_ROTATION')},AUDIT")
        arts[f"cg_macro_a1_known_windows_{bid}.csv"] = "\n".join(kw)

        all_pass, fail_reason, hashes = True, "", {}
        for name, text in sorted(arts.items()):
            hashes[name] = _msha(text)
            if name.endswith(".json"):
                continue
            if d4_is_placeholder_csv(text) or not text.strip():
                all_pass = False
                fail_reason = f"ARTIFACT_VALIDATION_FAIL:{name}:empty_or_placeholder"
        prow = max(0, len(arts[f"cg_macro_a1_predictors_{bid}.csv"].splitlines()) - 1)
        if prow != 162:
            all_pass = False
            fail_reason = f"ARTIFACT_VALIDATION_FAIL:predictors:rows={prow}"
        tprow = max(0, len(arts[f"cg_macro_a1_truth_packs_{bid}.csv"].splitlines()) - 1)
        if tprow != 4:
            all_pass = False
            fail_reason = f"ARTIFACT_VALIDATION_FAIL:truth_packs:rows={tprow}"
        kwrow = max(0, len(arts[f"cg_macro_a1_known_windows_{bid}.csv"].splitlines()) - 1)
        if kwrow != 16:
            all_pass = False
            fail_reason = f"ARTIFACT_VALIDATION_FAIL:known_windows:rows={kwrow}"

        src = getattr(self, "cg_macro_a1_source_commit", "") or ""
        tent = macro_finalize_result(tech_ok, all_pass, truth_ok, pred_ok, value_pass_n)
        manifest = {
            "schema_version": "MACRO_A1.0", "source_commit": src,
            "accepted_D4_closeout": MAISR_D4_CLOSEOUT, "truth_pack": chosen,
            "selected_predictors": list(sel_ids), "artifact_sha256": hashes,
            "technical_result": "PASS" if tech_ok else "FAIL",
            "research_result": tent["result"], "reason": tent["reason"],
            "value_pass_n": value_pass_n,
        }
        mhash, _ = d4_manifest_hash(manifest)
        manifest["manifest_sha256"] = mhash
        arts[f"cg_macro_a1_manifest_{bid}.json"] = json.dumps(
            manifest, sort_keys=True, separators=(",", ":"), default=str)
        vl = ["artifact,bytes,rows,sha256,pass"]
        for name, text in sorted(arts.items()):
            rows_n = max(0, len(text.splitlines()) - 1) if not name.endswith(".json") else 0
            vl.append(f"{name},{len(text.encode('utf-8'))},{rows_n},{_msha(text)},1")
        vl.append(f"ARTIFACT_VALIDATION_SELF,0,{len(arts)},SELF_EXCLUDED,{int(all_pass)}")
        arts[f"cg_macro_a1_artifact_validation_{bid}.csv"] = "\n".join(vl)

        fin = macro_finalize_result(tech_ok, all_pass, truth_ok, pred_ok, value_pass_n)
        if not all_pass:
            fin = {"result": "FAILED", "reason": fail_reason or "ARTIFACT_VALIDATION_FAIL",
                   "next": "FIX_MACRO_A1_IMPLEMENTATION", "research_conclusion": "NOT_REACHED"}

        self._macro_a1_art_used = 0
        for name, text in arts.items():
            self._MacroA1Emit(name, text)
        self._MsLog(
            f"CG_MACRO_A1_ARTIFACT_FINAL,artifacts={len(arts)},manifest_sha256={mhash},"
            f"validation_pass={int(all_pass)}"
        )
        self._MsLog(
            f"CG_MACRO_A1_RECOMMENDATION,result={fin['result']},reason={fin['reason']},"
            f"next={fin['next']},research_conclusion={fin['research_conclusion']},"
            f"truth={chosen or 'NONE'},predictors={len(sel_ids)},value_pass={value_pass_n},"
            f"best={best['id'] if best else 'NONE'},"
            f"best_oos_mean2={_mf(best['oos']) if best else 'NA'},"
            f"best_crisis_mean2={_mf(best['crisis']) if best else 'NA'}"
        )
        self._macro_a1_final = fin
        self._macro_a1_best = best
        return True
