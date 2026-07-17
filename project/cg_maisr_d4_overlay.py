# region imports
from AlgorithmImports import *
from datetime import timedelta
from collections import defaultdict
import base64
import hashlib
import zlib
import json
from cg_maisr_d4_core import (
    _D4_PACKS, _STATES, _SIX, _D4_BREADTH4, _D4_SECTOR_ASSETS, _D4_PROXY,
    _TRAIN0, _TRAIN1, _TRAINA0, _TRAINA1, _TRAINB0, _TRAINB1,
    _D4_KNOWN_WINDOWS, _DIST_FEATURES,
    d4_build_packs, d4_subject_codec, d4_gold_continuity, d4_raw_flags,
    d4_priority_macro, d4_priority_subject, d4_build_episodes,
    d4_broad_family_count, d4_broad_family_days, d4_monotonicity_checks,
    d4_support_audit, d4_stability_broad, d4_stability_subject, d4_stability_defensive,
    d4_match_episode, d4_hmode_classify, d4_manifest_hash, d4_select_subject,
    d4_assert_no_self_proxy, d4_apply_cut_fill, d4_cut_ceiling_apply,
    d4_is_subject_row, d4_validate_source_commit, d4_held_pairs, d4_dist_stats,
    d4_is_placeholder_csv, run_d4_static_tests, _ROUTER_ADJ,
    d4_validate_csv_artifact, d4_validate_distributions_csv, d4_validate_manifest_json,
    d4_calibration_artifact_schemas, d4_calibration_artifact_expected_rows,
    d4_finalize_calibration_result, d4_artifact_validation_self_contract,
)
from cg_maisr_d2_labels import _ALL_CFG, _clfid, _D2PeakTroughMaxDD, _D2_FWD, _D2_DUR, _D2_INFL

_W5 = (0.20, 0.25, 0.20, 0.25, 0.10)
# endregion
# cg_maisr_d4_overlay.py -- CG-MAISR-FINAL-CLEAN-D4 LEAN mixin.


def _d4f(x, d=4):
    if x is None:
        return "NA"
    try:
        return f"{float(x):.{d}f}"
    except Exception:
        return "NA"


def _d4f0(x, d=4):
    if x is None:
        return "0"
    try:
        return f"{float(x):.{d}f}"
    except Exception:
        return "0"


def _d4_sha(text):
    return hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()


def _d4_macro_eligible(r):
    t = int(r.get("tod", -1))
    k = r.get("kind", "")
    return (k == "PRE" and t <= 584) or (k == "POST" and 590 <= t <= 900)


def _d4_row_feature(r, feat):
    br, held = r.get("br_maes") or {}, r.get("held") or {}
    if feat == "SPY_MAE_ATR":
        return r.get("spy_mae")
    if feat == "DURATION_MAE_ATR":
        return r.get("dur_mae")
    if feat == "GOLD_MAE_ATR":
        return r.get("gold_mae")
    if feat == "INFLATION_ABS_RETURN":
        return r.get("infl_ret")
    if feat == "INFLATION_REL_SPY_ATR":
        return r.get("infl_rel")
    if feat in ("XLE_MAE_ATR", "XLB_MAE_ATR", "XLV_MAE_ATR", "XLU_MAE_ATR"):
        return br.get(feat[:3])
    if feat == "BREADTH_AVAILABLE_COUNT":
        return float(sum(1 for t in _D4_BREADTH4 if t in br)) if br else None
    if feat == "HELD_SUBJECT_COUNT_PER_DAY":
        return float(len(held)) if held else None
    if feat == "HELD_SUBJECT_MAE_ATR":
        maes = [v.get("mae") for v in held.values() if v.get("mae") is not None]
        return min(maes) if maes else None
    if feat == "HELD_SUBJECT_VS_SPY_ATR":
        vs = [v.get("vs_spy") for v in held.values() if v.get("vs_spy") is not None]
        return min(vs) if vs else None
    return None


class CgMaisrD4OverlayMixin:
    """D4 calibration / execution-proof / post-only economic overlay."""

    def _D4ReadParams(self, _p, _bool):
        self.cg_maisr_d4_enable = _bool("cg_maisr_d4_enable", "0")
        self.cg_maisr_d4_phase = str(_p("cg_maisr_d4_phase", "") or "").strip().upper()
        self.cg_maisr_d4_selected_pack = str(_p("cg_maisr_d4_selected_pack", "") or "").strip()
        raw = str(_p("cg_maisr_d4_selected_classifiers", "") or "").strip()
        self.cg_maisr_d4_selected_classifiers = [x.strip() for x in raw.split(",") if x.strip()]
        self.cg_maisr_d4_manifest_sha256 = str(_p("cg_maisr_d4_manifest_sha256", "") or "").strip()
        self.cg_maisr_d4_export_detail = _bool("cg_maisr_d4_export_detail", "1")
        self.cg_maisr_d4_source_commit = str(_p("cg_maisr_d4_source_commit", "") or "").strip().lower()

    def _D4InitHooks(self):
        if not getattr(self, "cg_maisr_d4_enable", False):
            return
        self._d2_mode = True  # reuse minute pending engine
        self._d4_raw = []
        self._d4_err = 0
        self._d4_gold_avail = 0
        self._d4_gold_n = 0
        self._d4_art_used = 0
        self._d4_pending_cuts = []
        self._d4_canary = {"status": "SEARCHING", "natural_signal": "NO", "fired": 0,
                           "reason": "NO_NATURAL_ACTIONABLE_SIGNAL"}
        self._d4_canary_led = None
        self._d4_overlay_led = None
        self._d4_reanchor_led = None
        self._d4_frozen_idx = []
        risk = sorted(set(getattr(self, "_ms_all", set()) or set()) - {"SPY", "SH", "BIL", "SGOV", "USFR", "BND", "TIP", "GLD", "GLDM"})
        self._d4_subj_code, self._d4_subj_inv = d4_subject_codec(risk + list(_D4_BREADTH4) + ["DBC"])
        proxy_ok, bad = d4_assert_no_self_proxy(_D4_PROXY)
        static_rows, p, n = run_d4_static_tests()
        self._MsLog(
            f"CG_MAISR_D42_CAL_INIT,phase={self.cg_maisr_d4_phase or 'NA'},"
            f"pack={self.cg_maisr_d4_selected_pack or 'AUTO'},"
            f"source_commit={getattr(self,'cg_maisr_d4_source_commit','') or 'NONE'},"
            f"classifiers={','.join(self.cg_maisr_d4_selected_classifiers) or 'NONE'}"
        )
        self._MsLog(
            f"CG_MAISR_D42_STATIC_FINAL,tests={p}/{n},self_proxy_ok={int(proxy_ok)},"
            f"self_proxy_bad={','.join(bad) or 'NONE'},"
            f"gold_primary={getattr(self,'_ms_gold_primary',None)},"
            f"gold_fallback={getattr(self,'_ms_gold_fallback',None)}"
        )
        if p != n or not proxy_ok:
            self._d4_err += 1
        # Resolve frozen classifier indices for exec/econ
        ids = getattr(self, "cg_maisr_d4_selected_classifiers", None) or []
        if ids:
            from cg_maisr_d2_labels import _ALL_CFG, _clfid
            id_to_i = {_clfid(*c): i for i, c in enumerate(_ALL_CFG)}
            self._d4_frozen_idx = [id_to_i[x] for x in ids if x in id_to_i]

    def _D4ClassifyPair(self, feat, cl, thr, amin, bthr, hmode, s):
        """Return (state, subject_code). Subject-aware LOCAL/SECTOR."""
        roles = self._ms_roles
        park = roles.get("PARK", ())

        def stressed(tk):
            if tk in park:
                return False
            c = cl.get(tk)
            if c is None:
                return False
            score = sum(w * v for w, v in zip(_W5, c))
            active = sum(1 for v in c if v >= thr)
            return bool(score >= thr and active >= amin)

        def dd_raw(tk):
            f = feat.get(tk)
            return f["raw"][3] if f else 0.0

        code = self._d4_subj_code
        broad = roles.get("BROAD", ())
        spy_str = any(stressed(t) for t in broad)
        breadth = tuple(t for t in _D4_BREADTH4 if t in (getattr(self, "_ms_all", set()) or set()))
        n_b = sum(1 for t in breadth if stressed(t))
        breadth_frac = (n_b / len(breadth)) if breadth else 0.0
        # Map legacy bthr fractions to count-ish; D4 packs use BR2/BR3 separately in labels.
        dur = roles.get("DUR", ())
        bond_str = any(stressed(t) for t in dur)
        sh_role = roles.get("SH", ())
        gold_str = stressed(getattr(self, "_ms_gold", "GLD"))
        ids_now = str(getattr(self, "_ids_state", "NORMAL") or "NORMAL")
        sh_confirm = bool((sh_role and stressed(sh_role[0])) or ids_now in ("WATCH", "STRESS", "PANIC_SHORT"))
        cross_confirm = bool(bond_str or gold_str)

        blocks = int(spy_str) + int(breadth_frac >= bthr) + int(bond_str) + int(gold_str)
        if spy_str and blocks >= 3:
            return "SYSTEMIC_LIQUIDITY_STRESS", code.get("MACRO", 1)

        infl = roles.get("INFL", ())
        infl_mv = 0.0
        if infl:
            infl_mv = sum((feat.get(t) or {}).get("mv", 0.0) for t in infl) / len(infl)
        if (spy_str or breadth_frac >= bthr) and bond_str and infl_mv > 0:
            return "RATE_INFLATION_STRESS", code.get("MACRO", 1)

        core_broad = bool(spy_str and breadth_frac >= bthr)
        if core_broad:
            hb = d4_hmode_classify(True, sh_confirm, cross_confirm, hmode)
            return hb, code.get("MACRO", 1)

        # Subject selection among held risk (strongest positive residual vs SPY)
        held = set(getattr(self, "_ms_current_risk", set()) or set()) - set(broad) - set(park)
        held = {t for t in held if t in (getattr(self, "_ms_all", set()) or set())}
        residuals = {t: dd_raw(t) - dd_raw("SPY") for t in held}
        subj = None
        best = None
        for t in sorted(residuals.keys()):
            v = residuals[t]
            if best is None or v > best + 1e-15:
                best, subj = v, t
        thr_s = 0.5 if s == "S1" else 0.75
        if subj and best is not None and best >= thr_s:
            if subj in _D4_SECTOR_ASSETS:
                return "SECTOR_STRESS", code.get(subj, 0)
            return "LOCAL_ASSET_STRESS", code.get(subj, 0)

        xlv_mv = (feat.get("XLV") or {}).get("mv", 0.0)
        xlu_mv = (feat.get("XLU") or {}).get("mv", 0.0)
        gold_mv = (feat.get(getattr(self, "_ms_gold", "GLD")) or {}).get("mv", 0.0)
        dur_mv = 0.0
        if dur:
            dur_mv = sum((feat.get(t) or {}).get("mv", 0.0) for t in dur) / len(dur)
        if spy_str and gold_mv > 0 and dur_mv > 0 and (xlv_mv > 0 or xlu_mv > 0):
            return "DEFENSIVE_ROTATION", code.get("MACRO", 1)

        if spy_str:
            return "UNCONFIRMED_NOISE", 0
        return "NORMAL", 0

    def _D4OnEval(self, kind, tod, states, subjects, feat):
        pass

    def _D4RuntimeOnEval(self, kind, tod, states, subjects, feat):
        """POST-only canary / overlay signal arming during EXECUTION_PROOF/ECONOMIC."""
        phase = getattr(self, "cg_maisr_d4_phase", "") or ""
        if phase not in ("EXECUTION_PROOF", "ECONOMIC"):
            return
        if kind != "POST" or tod < 590 or tod > 900:
            return
        if not getattr(self, "_ms_caps", None):
            return
        idxs = getattr(self, "_d4_frozen_idx", None) or []
        if not idxs:
            raw = getattr(self, "cg_maisr_d4_selected_classifiers", None) or []
            # resolve later at EOA if unknown
            return
        idx = idxs[0]
        if idx >= len(states):
            return
        st = _STATES[states[idx]] if states[idx] < len(_STATES) else "NORMAL"
        subj = self._d4_subj_inv.get(subjects[idx] if idx < len(subjects) else 0, "NONE")
        if st in ("NORMAL", "UNCONFIRMED_NOISE"):
            return
        # Canary: first actionable only
        can = getattr(self, "_d4_canary", None)
        if can is None:
            self._d4_canary = {"status": "SEARCHING", "natural_signal": "NO", "fired": 0}
            can = self._d4_canary
        if phase == "EXECUTION_PROOF" and can.get("fired"):
            return
        affected = self._D4AffectedSymbols(st, subj)
        if not affected:
            return
        # require positive integer 25% cut on at least one symbol
        ctrl = getattr(self, "_sr_ctrl", None) or {}
        qty_map = dict(ctrl.get("qty") or {})
        cuts = {}
        for tk in affected:
            q = int(float(qty_map.get(tk, 0) or 0))
            if q <= 0:
                continue
            sell = int(q * 0.25)
            if sell <= 0:
                continue
            cuts[tk] = sell
        if not cuts:
            return
        # min-order: $1 notional approx
        px = self._MsPx(tuple(cuts.keys())) if hasattr(self, "_MsPx") else []
        ok_any = False
        for i, tk in enumerate(cuts.keys()):
            p = px[i] if i < len(px) else 0
            if p and cuts[tk] * p >= 1.0:
                ok_any = True
                break
        if not ok_any:
            return
        sig_t = self.time
        pending = {
            "event_id": f"{sig_t}|{st}|{subj}",
            "signal_time": sig_t,
            "state": st, "subject": subj,
            "cuts": cuts, "filled": {},
        }
        self._d4_pending_cuts = getattr(self, "_d4_pending_cuts", [])
        self._d4_pending_cuts.append(pending)
        if phase == "EXECUTION_PROOF" and can.get("natural_signal") != "YES":
            can.update({
                "natural_signal": "YES", "status": "ARMED",
                "state": st, "subject": subj, "signal_time": str(sig_t),
                "affected": ",".join(cuts.keys()), "requested": str(cuts),
            })

    def _D4AffectedSymbols(self, st, subj):
        park = set((getattr(self, "_ms_roles", {}) or {}).get("PARK", ()) or ())
        dur = set((getattr(self, "_ms_roles", {}) or {}).get("DUR", ()) or ())
        gold = {getattr(self, "_ms_gold", "GLD")}
        risk = set(getattr(self, "_ms_current_risk", set()) or set()) - park
        if st in ("LOCAL_ASSET_STRESS", "SECTOR_STRESS"):
            return [subj] if subj in risk else []
        if st in ("BROAD_EQUITY_STRESS", "DEFENSIVE_ROTATION"):
            return [t for t in risk if t not in dur and t not in gold and t != "SH"]
        if st == "SYSTEMIC_LIQUIDITY_STRESS":
            out = [t for t in risk if t not in park and t != "SH"]
            return out
        if st == "RATE_INFLATION_STRESS":
            return [t for t in risk if t not in gold and t != "SH"]
        return []

    def _D4TryFillPending(self, tk, et, o):
        pending = getattr(self, "_d4_pending_cuts", None) or []
        if not pending:
            return
        phase = getattr(self, "cg_maisr_d4_phase", "") or ""
        for ev in list(pending):
            if tk not in ev.get("cuts", {}):
                continue
            if tk in ev.get("filled", {}):
                continue
            sig = ev["signal_time"]
            if et is None or et <= sig:
                continue
            # next-bar Open fill
            sell = int(ev["cuts"][tk])
            if sell <= 0:
                continue
            led = getattr(self, "_d4_canary_led", None)
            if led is None:
                continue
            q0 = float((led.get("qty") or {}).get(tk, 0) or 0)
            if q0 <= 0:
                continue
            sell = min(sell, int(q0))
            if sell <= 0:
                continue
            fee = abs(sell) * float(o) * 0.0
            d4_apply_cut_fill(led, tk, -sell, float(o), fee)
            d4_cut_ceiling_apply(led, tk, float((led.get("qty") or {}).get(tk, 0) or 0), 0.75)
            ev.setdefault("filled", {})[tk] = {"qty": sell, "px": float(o), "et": str(et)}
            if phase == "EXECUTION_PROOF":
                can = self._d4_canary
                can.update({
                    "status": "PASS", "fired": 1, "fill_et": str(et), "fill_open": float(o),
                    "actual_sell": sell, "symbol": tk, "same_bar_fill": "NO",
                    "direction": "REDUCE", "reason": "natural_next_bar",
                })
            if len(ev["filled"]) >= len(ev["cuts"]):
                pending.remove(ev)

    def _D4CloneLed(self, src):
        import copy
        return copy.deepcopy(src) if src else {"qty": {}, "cash": 0.0, "rets": [], "dates": [],
                                               "cut_ceiling_qty": {}, "last_cut_mult": {}}

    def _D4StoreFromFinalize(self, p, stats, spy, dur_mae, gold_mae, dur_ok, gold_ok,
                             infl_ret, infl_rel, held_feat, br_maes, gold_source):
        do = p["do"]
        if not (_TRAIN0 <= do <= _TRAIN1 or True):
            pass
        # keep TRAIN + known windows via D2 path already filtering? store all finalized for D4
        self._d4_gold_n += 1
        if gold_ok:
            self._d4_gold_avail += 1
        blocks = []
        for name, tks in (("duration", _D2_DUR), ("gold", (gold_source,) if gold_source not in (None, "NONE") else ()),
                          ("def_sector", ("XLV", "XLU"))):
            vals = [stats[t] for t in tks if t in stats]
            if not vals:
                blocks.append({"name": name, "ok": False, "abs": None, "rel": None})
            else:
                ab = sum(v["ret"] for v in vals) / len(vals)
                blocks.append({"name": name, "ok": True, "abs": ab, "rel": ab - spy["ret"]})
        self._d4_raw.append({
            "do": do, "tod": p["tod"], "t": p["t"], "preds": p["preds"],
            "subjects": p.get("subjects") or b"\x00" * 54,
            "kind": p.get("kind", "POST"),
            "rg": p["rg"], "w2": p["w2"], "ids": p["ids"],
            "spy_mae": spy["mae"], "spy_ret": spy["ret"],
            "dur_mae": dur_mae if dur_ok else None,
            "gold_mae": gold_mae if gold_ok else None,
            "gold_source": gold_source,
            "infl_ret": infl_ret, "infl_rel": infl_rel,
            "blocks": blocks, "held": held_feat, "br_maes": br_maes,
            "train": bool(_TRAIN0 <= do <= _TRAIN1),
        })

    def _D4Emit(self, key, text):
        try:
            self.object_store.save(key, text)
        except Exception:
            pass
        try:
            self.object_store.save_bytes(key, text.encode("utf-8"))
        except Exception:
            pass
        raw = zlib.compress(text.encode("utf-8"), 9)
        b64 = base64.b64encode(raw).decode("ascii")
        chunk, used, budget = 700, int(getattr(self, "_d4_art_used", 0) or 0), 34000
        n = (len(b64) + chunk - 1) // chunk
        name = str(key).replace(",", "_")
        meta = f"CG_MAISR_D4_ART_META,name={name},bytes={len(text)},zbytes={len(raw)},chunks={n}"
        if used + len(meta) > budget:
            self._MsLog(f"{meta},emitted=0,truncated=YES")
            return
        self._MsLog(f"{meta},emitted_pending=1")
        used += len(meta) + 1
        emit = 0
        for i in range(n):
            line = f"CG_MAISR_D4_ART,name={name},i={i},n={n},b64={b64[i*chunk:(i+1)*chunk]}"
            if used + len(line) > budget:
                break
            self._MsLog(line)
            used += len(line) + 1
            emit += 1
        self._d4_art_used = used
        self._MsLog(f"CG_MAISR_D4_ART_META,name={name},emitted={emit},truncated={'YES' if emit < n else 'NO'}")

    def _D4Identity(self):
        results = {}
        leds = getattr(self, "_sr_identity_leds", None) or {}
        cmp_fn = getattr(self, "CgShadowIdentityCompare", None)
        for label in ("MAISR_REPLAY_IDENTITY", "MAISR_PIPELINE_OFF_IDENTITY",
                      "MAISR_SENSOR_NO_ACTION_IDENTITY"):
            led = leds.get(label) or {}
            cmp = dict(cmp_fn(list(led.get("rets") or []))) if cmp_fn else {"pass": False, "n": 0}
            peak, trough, recovery = _D2PeakTroughMaxDD(list(led.get("dates") or []), list(led.get("rets") or []))
            chron = not (peak != "NA" and trough != "NA" and peak > trough)
            passed = bool(cmp.get("pass")) and chron
            results[label] = {
                "pass": passed, "n": cmp.get("n", 0),
                "nav_d": cmp.get("nav_d"), "dd_d": cmp.get("dd_d"), "corr": cmp.get("corr"),
                "peak": peak, "trough": trough, "recovery": recovery,
            }
            self._MsLog(
                f"CG_MAISR_D42_IDENTITY_FINAL,id={label},pass={'YES' if passed else 'NO'},"
                f"n={cmp.get('n',0)},nav_diff_pct={_d4f(cmp.get('nav_d'),6)},"
                f"maxdd_diff_pp={_d4f(cmp.get('dd_d'),6)},corr={_d4f(cmp.get('corr'),6)}"
            )
        return results

    def CgMaisrD4OnEndOfAlgorithm(self, parity_ok) -> bool:
        if not getattr(self, "cg_maisr_d4_enable", False):
            return False
        phase = getattr(self, "cg_maisr_d4_phase", "") or ""
        if phase == "CALIBRATION":
            return self._D4CalibrationEOA(parity_ok)
        if phase == "EXECUTION_PROOF":
            return self._D4ExecutionProofEOA(parity_ok)
        if phase == "ECONOMIC":
            return self._D4EconomicEOA(parity_ok)
        return False

    def _D4CalibrationEOA(self, parity_ok) -> bool:
        src = getattr(self, "cg_maisr_d4_source_commit", "") or ""
        src_ok, src_rsn = d4_validate_source_commit(src)
        try:
            self._D2FlushPending()
        except Exception:
            self._d4_err += 1
        self._D4HarvestFromD2Finalize()
        if not src_ok:
            self._MsLog(
                f"CG_MAISR_D42_CALIBRATION_FINAL,result=FAILED,reason=invalid_source_commit,"
                f"detail={src_rsn},research_conclusion=NOT_REACHED"
            )
            return True
        if not parity_ok or self._d4_err:
            self._MsLog(
                "CG_MAISR_D42_CALIBRATION_FINAL,result=FAILED,reason=parity_or_static,"
                "research_conclusion=NOT_REACHED,next=FIX_D4_1_CALIBRATION"
            )
            return True
        if not self._d4_raw:
            self._MsLog(
                "CG_MAISR_D42_CALIBRATION_FINAL,result=FAILED,reason=empty_d4_raw,"
                "research_conclusion=NOT_REACHED,next=FIX_D4_1_CALIBRATION"
            )
            return True

        id_results = self._D4Identity()
        id_ok = all(r.get("pass") for r in id_results.values())
        cov = self._D2CoverageReport() if hasattr(self, "_D2CoverageReport") else {}
        gold_cov = (self._d4_gold_avail / max(self._d4_gold_n, 1)) if self._d4_gold_n else 0.0
        self._MsLog(
            f"CG_MAISR_D42_GOLD_FINAL,train_coverage={_d4f(gold_cov,4)},"
            f"double_count_used=0,primary={getattr(self,'_ms_gold_primary',None)},"
            f"fallback={getattr(self,'_ms_gold_fallback',None)}"
        )
        gold_ok = gold_cov >= 0.95
        train = [r for r in self._d4_raw if r.get("train")]
        macro_n = sum(1 for r in train if _d4_macro_eligible(r))
        subject_n = sum(1 for r in train if d4_is_subject_row(r))
        self._MsLog(
            f"CG_MAISR_D42_SCOPE_FINAL,macro_rows={macro_n},subject_rows={subject_n},"
            f"pre_subject_rows_used=0,post_subject_rows_used={subject_n}"
        )
        ta_sub = [r for r in train if _TRAINA0 <= r["do"] <= _TRAINA1 and d4_is_subject_row(r)]
        tb_sub = [r for r in train if _TRAINB0 <= r["do"] <= _TRAINB1 and d4_is_subject_row(r)]
        hp_a, hp_b = d4_held_pairs(ta_sub), d4_held_pairs(tb_sub)
        hp_all = hp_a | hp_b
        self._MsLog(
            f"CG_MAISR_D42_EXPOSURE_FINAL,held_symbol_days_a={len(hp_a)},"
            f"held_symbol_days_b={len(hp_b)},held_symbol_days_total={len(hp_all)},"
            f"symbols_with_exposure={len({tk for _, tk in hp_all})}"
        )

        pack_stats, raw_by, eps_by, labeled_by, stab_rows = {}, {}, {}, {}, []
        for pack in _D4_PACKS:
            labeled, raw_acc, me, he = self._D4LabelPack(pack, train)
            labeled_by[pack["id"]] = labeled
            eps_by[pack["id"]] = (me, he)
            bf_ep, bf_days = d4_broad_family_count(me), d4_broad_family_days(me)
            loc = sum(1 for e in he if e["label"] == "LOCAL_ASSET_STRESS")
            sec = sum(1 for e in he if e["label"] == "SECTOR_STRESS")
            ls, ls_days = loc + sec, len({(e["day"], e["subject"]) for e in he
                                          if e["label"] in ("LOCAL_ASSET_STRESS", "SECTOR_STRESS")})
            deff = sum(1 for e in me if e["label"] == "DEFENSIVE_ROTATION")
            sys_ = sum(1 for e in me if e["label"] == "SYSTEMIC_LIQUIDITY_STRESS")
            rate = sum(1 for e in me if e["label"] == "RATE_INFLATION_STRESS")
            aud = d4_support_audit(bf_ep, bf_days, ls, ls_days, deff)
            support_ok = aud["pass"]
            support_reason = ";".join(aud["reasons"]) if aud["reasons"] else "OK"
            ep_a = d4_broad_family_count([e for e in me if _TRAINA0 <= e["day"] <= _TRAINA1])
            ep_b = d4_broad_family_count([e for e in me if _TRAINB0 <= e["day"] <= _TRAINB1])
            broad_ok, da, db, bratio, brsn = d4_stability_broad(ep_a, ep_b)
            ls_a = sum(1 for e in he if _TRAINA0 <= e["day"] <= _TRAINA1
                       and e["label"] in ("LOCAL_ASSET_STRESS", "SECTOR_STRESS"))
            ls_b = sum(1 for e in he if _TRAINB0 <= e["day"] <= _TRAINB1
                       and e["label"] in ("LOCAL_ASSET_STRESS", "SECTOR_STRESS"))
            sub_ok, sda, sdb, sratio, srsn = d4_stability_subject(ls_a, ls_b, len(hp_a), len(hp_b))
            def_a = sum(1 for e in me if _TRAINA0 <= e["day"] <= _TRAINA1 and e["label"] == "DEFENSIVE_ROTATION")
            def_b = sum(1 for e in me if _TRAINB0 <= e["day"] <= _TRAINB1 and e["label"] == "DEFENSIVE_ROTATION")
            def_ok, dda, ddb, dratio, drsn = d4_stability_defensive(def_a, def_b)
            stab_ok = broad_ok and sub_ok and def_ok
            stab_rows.append({
                "pack": pack["id"],
                "broad_ep_a": ep_a, "broad_ep_b": ep_b,
                "broad_years_a": 4.0, "broad_years_b": 3.0,
                "broad_density_a": _d4f0(da), "broad_density_b": _d4f0(db),
                "broad_ratio": _d4f0(bratio), "broad_reason": brsn,
                "subject_ep_a": ls_a, "subject_ep_b": ls_b,
                "eligible_held_symbol_days_a": len(hp_a),
                "eligible_held_symbol_days_b": len(hp_b),
                "subject_density_a": _d4f0(sda), "subject_density_b": _d4f0(sdb),
                "subject_ratio": _d4f0(sratio), "subject_reason": srsn,
                "defensive_ep_a": def_a, "defensive_ep_b": def_b,
                "defensive_density_a": _d4f0(dda), "defensive_density_b": _d4f0(ddb),
                "defensive_ratio": _d4f0(dratio), "defensive_reason": drsn,
                "stability_ok": int(stab_ok),
                "broad_ok": int(broad_ok), "subject_ok": int(sub_ok), "defensive_ok": int(def_ok),
            })
            raw_by[pack["id"]] = raw_acc
            pack_stats[pack["id"]] = {
                **{k: aud[k] for k in aud if k not in ("pass", "reasons")},
                "id": pack["id"], "support_ok": int(support_ok), "support_reason": support_reason,
                "stability_ok": int(stab_ok), "systemic_episodes": sys_, "rate_episodes": rate,
                "dist_score": abs(bf_ep - 80) + abs(ls - 35) + abs(deff - 40),
                "pass": 0, "mono_ok": 0,
            }
            self._MsLog(
                f"CG_MAISR_D42_PACK_FINAL,id={pack['id']},support={int(support_ok)},"
                f"support_reason={support_reason},stable={int(stab_ok)},"
                f"bf={bf_ep},ls={ls},def={deff},sys={sys_},rate={rate}"
            )

        mono_rows = d4_monotonicity_checks(raw_by)
        mono_ok = all(r["pass"] for r in mono_rows)
        for s in pack_stats.values():
            s["mono_ok"] = int(mono_ok)
            s["pass"] = int(s["support_ok"] and s["stability_ok"] and mono_ok)

        pool = [s for s in pack_stats.values() if s["pass"]]
        pool.sort(key=lambda s: (s["dist_score"], 0 if "BR3" in s["id"] else 1,
                                 0 if "B60" in s["id"] else 1, 0 if "L75" in s["id"] else 1, s["id"]))
        chosen_pack = pool[0]["id"] if pool else None
        self._MsLog(
            f"CG_MAISR_D42_SELECTED_PACK,id={chosen_pack or 'NONE'},"
            f"mono={'PASS' if mono_ok else 'FAIL'},gold_ok={int(gold_ok)}"
        )

        scored, chosen, sigs = [], [], {}
        if chosen_pack:
            scored, chosen, _, sigs = self._D4ScoreSelect(
                chosen_pack, labeled_by[chosen_pack], eps_by[chosen_pack], pack_stats[chosen_pack])
            for r in chosen[:6]:
                self._MsLog(
                    f"CG_MAISR_D42_CLASSIFIER_SELECTED,id={r['id']},H={r['h']},"
                    f"score={_d4f(r['score'],4)},sig={r.get('sig_hash','')[:12]}"
                )
        else:
            scored = [{"id": _clfid(s, a, b, h), "s": s, "a": a, "b": b, "h": h,
                       "valid": 0, "score": 0, "validity_reason": "no_pack",
                       "macro_f1": 0, "f1": {}, "sig_hash": "NONE", "n": 0,
                       "macro_sig_hash": "NONE", "subject_sig_hash": "NONE",
                       "combined_sig_hash": "NONE"}
                      for s, a, b, h in _ALL_CFG]

        clf_ok = len(chosen) >= 3 and len(set(sigs.values())) >= 2
        data_ok = int(getattr(self, "_ms_bd_conflict", 0) or 0) == 0
        cov_ok = cov.get("coverage_ratio", 0) >= 0.99
        gates_ok = id_ok and gold_ok and mono_ok and data_ok and cov_ok

        if gates_ok and chosen_pack and clf_ok:
            tent_result, tent_reason = "CALIBRATION_PASS", "OK"
        elif gates_ok and not chosen_pack:
            tent_result, tent_reason = "STOP_MAISR", "NO_SUPPORTED_SUBJECT_PACK"
        elif gates_ok and chosen_pack and not clf_ok:
            tent_result, tent_reason = "STOP_MAISR", "INSUFFICIENT_CLASSIFIER_DIVERSITY"
        else:
            tent_result, tent_reason = "FAILED", "calibration_gate_fail"

        bid = self._MsBid() if hasattr(self, "_MsBid") else "NA"
        self._d4_selected_pack = chosen_pack
        mhash, arts, all_pass, fail_reason = self._D4ExportCalib(
            bid, src, id_results, pack_stats, mono_rows, stab_rows, scored, chosen,
            eps_by.get(chosen_pack, ([], [])), cov, gold_cov, tent_result, tent_reason)
        fin = d4_finalize_calibration_result(gates_ok, chosen_pack, clf_ok, all_pass)
        result, reason, nxt = fin["result"], fin["reason"], fin["next"]
        rc = fin["research_conclusion"]
        if not all_pass:
            result, reason, nxt, rc = "FAILED", fail_reason, "FIX_D4_ARTIFACTS", "NOT_REACHED"
        ph_n = sum(1 for t in arts.values() if d4_is_placeholder_csv(t))
        self._MsLog(
            f"CG_MAISR_D42_ARTIFACT_FINAL,artifacts={len(arts)},"
            f"manifest_sha256={mhash},placeholder_count={ph_n},validation_pass={int(all_pass)}"
        )
        self._MsLog(
            f"CG_MAISR_D42_CALIBRATION_FINAL,result={result},reason={reason},next={nxt},"
            f"research_conclusion={rc},selected_pack={chosen_pack or 'NONE'},"
            f"classifiers={len(chosen)},h_sigs={len(set(sigs.values()))},manifest_sha256={mhash},"
            f"frozen_classifiers={','.join(r['id'] for r in chosen)}"
        )
        self._d4_manifest = {"manifest_sha256": mhash, "selected_pack": chosen_pack}
        self._d4_chosen = chosen
        return True

    def _D4HarvestFromD2Finalize(self):
        """If D4Store not wired, reconstruct minimal raw from pending train is empty — rely on patch."""
        # Patch finalize to call _D4StoreFromFinalize — applied below via method override
        pass

    def _D4LabelPack(self, pack, rows):
        labeled, macro_stream, held_stream = [], [], []
        raw_evals_b = raw_loc_e = raw_sec_e = 0
        day_b, day_l, day_s = set(), set(), set()
        macro_n = subj_n = pre_subj_excl = post_subj_used = 0
        for r in rows:
            is_macro = _d4_macro_eligible(r)
            is_subj = d4_is_subject_row(r)
            if is_macro:
                macro_n += 1
            if is_subj:
                subj_n += 1
                post_subj_used += 1
            elif r.get("kind") == "PRE" and (r.get("held") or {}):
                pre_subj_excl += 1
            br_n = sum(1 for t in _D4_BREADTH4 if t in (r.get("br_maes") or {}) and r["br_maes"][t] <= -pack["B"])
            br_avail = sum(1 for t in _D4_BREADTH4 if t in (r.get("br_maes") or {}))
            avail = [b for b in r.get("blocks") or [] if b.get("ok")]
            resilient = [b for b in avail if b.get("abs") is not None and b.get("rel") is not None
                         and b["abs"] >= 0 and b["rel"] >= 0.30 * pack["B"]]
            med_abs = sorted(b["abs"] for b in avail)[len(avail) // 2] if avail else None
            med_rel = sorted(b["rel"] for b in avail)[len(avail) // 2] if avail else None
            flags = d4_raw_flags(
                pack, r["spy_mae"], br_n, br_avail,
                r.get("dur_mae"), r.get("gold_mae"), r.get("infl_rel"), r.get("infl_ret"),
                len(resilient), len(avail), med_abs, med_rel, r.get("held") or {},
            )
            if is_subj:
                if flags["raw_broad"]:
                    raw_evals_b += 1
                    day_b.add(r["do"])
                for _s in flags["raw_local"]:
                    raw_loc_e += 1
                    day_l.add(r["do"])
                for _s in flags["raw_sector"]:
                    raw_sec_e += 1
                    day_s.add(r["do"])
            mlab = "NORMAL"
            slab, ssubj = "NORMAL", "NONE"
            if is_macro:
                mlab = d4_priority_macro(flags) if flags.get("breadth_ok") else "NORMAL"
                macro_stream.append({"ts": r["t"], "day": r["do"], "subject": "MACRO", "label": mlab,
                                     "mae": r["spy_mae"], "breadth": br_n / 4.0 if br_avail else 0.0})
            if is_subj:
                order = sorted((r.get("held") or {}).keys(),
                               key=lambda tk: (r["held"][tk].get("vs_spy") if r["held"][tk].get("vs_spy") is not None else 0, tk))
                slab, ssubj = d4_priority_subject(flags, order)
                if slab != "NORMAL" and ssubj != "NONE":
                    held_stream.append({"ts": r["t"], "day": r["do"], "subject": ssubj, "label": slab})
            labeled.append({**r, "macro": mlab, "subj_label": slab, "subj": ssubj, "flags": flags,
                            "breadth": br_n / 4.0 if br_avail else 0.0})
        me, he = d4_build_episodes(macro_stream), d4_build_episodes(held_stream)
        raw_b_stream = [{"ts": r["t"], "day": r["do"], "subject": "MACRO", "label": "BROAD_EQUITY_STRESS"}
                        for r, lab in zip(rows, labeled) if d4_is_subject_row(r) and lab["flags"]["raw_broad"]]
        raw_l_stream = [{"ts": r["t"], "day": r["do"], "subject": s, "label": "LOCAL_ASSET_STRESS"}
                        for r, lab in zip(rows, labeled) if d4_is_subject_row(r) for s in lab["flags"]["raw_local"]]
        raw_s_stream = [{"ts": r["t"], "day": r["do"], "subject": s, "label": "SECTOR_STRESS"}
                        for r, lab in zip(rows, labeled) if d4_is_subject_row(r) for s in lab["flags"]["raw_sector"]]
        raw_acc = {
            "raw_broad_evals": raw_evals_b, "raw_broad_eps": len(d4_build_episodes(raw_b_stream)),
            "raw_broad_days": len(day_b), "raw_local_evals": raw_loc_e,
            "raw_local_eps": len(d4_build_episodes(raw_l_stream)), "raw_local_days": len(day_l),
            "raw_sector_evals": raw_sec_e, "raw_sector_eps": len(d4_build_episodes(raw_s_stream)),
            "raw_sector_days": len(day_s), "macro_n": macro_n, "subject_n": subj_n,
            "pre_subj_excl": pre_subj_excl, "post_subj_used": post_subj_used,
        }
        return labeled, raw_acc, me, he

    def _D4ScoreSelect(self, pack_id, labeled, eps, stats):
        me, he = eps
        scored, sigs = [], {}
        for idx, (s, a, b, h) in enumerate(_ALL_CFG):
            cid = _clfid(s, a, b, h)
            pred_m, pred_h, mp, sp, cp = [], [], [], [], []
            for r in labeled:
                if idx >= len(r["preds"]):
                    continue
                st = _STATES[r["preds"][idx]]
                subj_code = r["subjects"][idx] if idx < len(r["subjects"]) else 0
                subj = self._d4_subj_inv.get(subj_code, "NONE")
                if _d4_macro_eligible(r):
                    mp.append(f"{st}:MACRO")
                    if st in ("BROAD_EQUITY_STRESS", "SYSTEMIC_LIQUIDITY_STRESS",
                              "RATE_INFLATION_STRESS", "DEFENSIVE_ROTATION"):
                        pred_m.append({"ts": r["t"], "day": r["do"], "subject": "MACRO", "label": st})
                if d4_is_subject_row(r) and subj not in ("NONE", "MACRO"):
                    sp.append(f"{st}:{subj}")
                    if st in ("LOCAL_ASSET_STRESS", "SECTOR_STRESS"):
                        pred_h.append({"ts": r["t"], "day": r["do"], "subject": subj, "label": st})
                cp.append(f"{st}:{subj}")
            macro_sig = hashlib.sha256("|".join(mp[:5000]).encode()).hexdigest()[:16]
            subj_sig = hashlib.sha256("|".join(sp[:5000]).encode()).hexdigest()[:16]
            comb_sig = hashlib.sha256("|".join(cp[:5000]).encode()).hexdigest()[:16]
            sigs[cid] = comb_sig
            pme, phe = d4_build_episodes(pred_m), d4_build_episodes(pred_h)
            f1s = {}
            for lab, true_eps, pred_eps in (
                ("BROAD_EQUITY_STRESS", [e for e in me if e["label"] in ("BROAD_EQUITY_STRESS", "SYSTEMIC_LIQUIDITY_STRESS")],
                 [e for e in pme if e["label"] in ("BROAD_EQUITY_STRESS", "SYSTEMIC_LIQUIDITY_STRESS")]),
                ("LOCAL_ASSET_STRESS", [e for e in he if e["label"] == "LOCAL_ASSET_STRESS"],
                 [e for e in phe if e["label"] == "LOCAL_ASSET_STRESS"]),
                ("SECTOR_STRESS", [e for e in he if e["label"] == "SECTOR_STRESS"],
                 [e for e in phe if e["label"] == "SECTOR_STRESS"]),
                ("DEFENSIVE_ROTATION", [e for e in me if e["label"] == "DEFENSIVE_ROTATION"],
                 [e for e in pme if e["label"] == "DEFENSIVE_ROTATION"]),
            ):
                used, tp = set(), 0
                for te in true_eps:
                    for i, pe in enumerate(pred_eps):
                        if i in used:
                            continue
                        if d4_match_episode(pe, te):
                            tp += 1
                            used.add(i)
                            break
                fp, fn = len(pred_eps) - len(used), len(true_eps) - tp
                prec = tp / (tp + fp) if (tp + fp) else 0.0
                rec = tp / (tp + fn) if (tp + fn) else 0.0
                f1s[lab] = (2 * prec * rec / (prec + rec)) if (prec + rec) else 0.0
            bf1 = f1s.get("BROAD_EQUITY_STRESS", 0)
            ls1 = max(f1s.get("LOCAL_ASSET_STRESS", 0), f1s.get("SECTOR_STRESS", 0))
            macro_f1 = (bf1 + f1s.get("DEFENSIVE_ROTATION", 0)) / 2.0
            valid = int(bf1 > 0 and ls1 > 0 and macro_f1 > 0)
            score = (bf1 + ls1 + f1s.get("DEFENSIVE_ROTATION", 0)) / 3.0
            scored.append({
                "id": cid, "idx": idx, "s": s, "a": a, "b": b, "h": h,
                "valid": valid, "score": score, "macro_f1": macro_f1, "f1": f1s,
                "validity_reason": "OK" if valid else "zero_f1", "sig_hash": comb_sig,
                "macro_sig_hash": macro_sig, "subject_sig_hash": subj_sig, "combined_sig_hash": comb_sig,
                "n": len(labeled), "broad_f1": bf1, "locsec_f1": ls1,
            })
        chosen, modes = [], set()
        for hv in ("H0", "H1", "H2"):
            cand = [r for r in scored if r["valid"] and r["h"] == hv]
            cand.sort(key=lambda r: (-r["score"], -r["broad_f1"], -r["locsec_f1"], r["id"]))
            for r in cand[:2]:
                chosen.append(r)
                modes.add(hv)
        return scored, chosen[:6], modes, sigs

    def _D4ExportCalib(self, bid, src, id_results, pack_stats, mono_rows, stab_rows, scored, chosen,
                       eps, cov, gold_cov, cal_result, cal_reason):
        arts = {}
        schemas = d4_calibration_artifact_schemas()
        chosen_id = getattr(self, "_d4_selected_pack", None)

        il = [",".join(schemas["identity"])]
        for k, r in id_results.items():
            il.append(f"{k},{'YES' if r['pass'] else 'NO'},{r['n']},"
                      f"{_d4f(r.get('nav_d'),6)},{_d4f(r.get('dd_d'),6)},{_d4f(r.get('corr'),6)},"
                      f"{r.get('peak','NA')},{r.get('trough','NA')},{r.get('recovery','NA')}")
        arts[f"cg_maisr_d4_identity_{bid}.csv"] = "\n".join(il)

        rl = [",".join(schemas["symbol_roles"])]
        for tk in sorted(getattr(self, "_ms_all", set()) or []):
            if tk in ("AVGO", "MU", "NVDA"):
                role, src_r = "INACTIVE_TRADING_PATH", "spyg_sat_trade_enable=0"
            elif tk in ("BIL", "SGOV", "USFR"):
                role, src_r = "PARKING", "cash"
            elif tk in ("GLD", "GLDM", "BND", "TIP"):
                role, src_r = "DEFENSIVE", "block"
            elif tk == "SH":
                role, src_r = "INVERSE_CONFIRM", "sh"
            elif tk == "SPY":
                role, src_r = "SENSOR_ONLY", "benchmark"
            else:
                role, src_r = "ACTIVE_HELD_RISK", "panel"
            rl.append(f"{tk},{role},{src_r}")
        arts[f"cg_maisr_d4_symbol_roles_{bid}.csv"] = "\n".join(rl)

        arts[f"cg_maisr_d4_gold_continuity_{bid}.csv"] = (
            "metric,value\ntrain_coverage," + _d4f(gold_cov, 4) +
            "\ndouble_count_used,0\nprimary," + str(getattr(self, "_ms_gold_primary", "NA")) +
            "\nfallback," + str(getattr(self, "_ms_gold_fallback", "NA")))

        train = [r for r in self._d4_raw if r.get("train")]
        scopes = [("TRAIN_ALL", train),
                  ("TRAIN_A", [r for r in train if _TRAINA0 <= r["do"] <= _TRAINA1]),
                  ("TRAIN_B", [r for r in train if _TRAINB0 <= r["do"] <= _TRAINB1])]
        dl = [",".join(schemas["distributions"])]
        for sc, srows in scopes:
            tc = len(srows)
            for feat in _DIST_FEATURES:
                vals = [float(_d4_row_feature(r, feat)) for r in srows if _d4_row_feature(r, feat) is not None]
                st = d4_dist_stats(vals, tc, sc, feat)
                dl.append(",".join(str(st[k]) if st[k] is not None else "NA" for k in schemas["distributions"]))
        arts[f"cg_maisr_d4_distributions_{bid}.csv"] = "\n".join(dl)

        ta_sub = [r for r in train if _TRAINA0 <= r["do"] <= _TRAINA1 and d4_is_subject_row(r)]
        tb_sub = [r for r in train if _TRAINB0 <= r["do"] <= _TRAINB1 and d4_is_subject_row(r)]
        hp_a, hp_b = d4_held_pairs(ta_sub), d4_held_pairs(tb_sub)
        sym_exp = defaultdict(lambda: {"a": 0, "b": 0})
        for d, tk in hp_a:
            sym_exp[tk]["a"] += 1
        for d, tk in hp_b:
            sym_exp[tk]["b"] += 1
        first_last = {}
        for d, tk in (hp_a | hp_b):
            fl = first_last.setdefault(tk, [d, d])
            if d < fl[0]:
                fl[0] = d
            if d > fl[1]:
                fl[1] = d
        a_n, b_n = len(hp_a), len(hp_b)
        do_a = [d for d, _ in hp_a]
        do_b = [d for d, _ in hp_b]
        min_do_a = min(do_a) if do_a else 0
        max_do_a = max(do_a) if do_a else 0
        min_do_b = min(do_b) if do_b else 0
        max_do_b = max(do_b) if do_b else 0
        min_do_all = min(do_a + do_b) if (do_a or do_b) else 0
        max_do_all = max(do_a + do_b) if (do_a or do_b) else 0
        sel = [",".join(schemas["subject_exposure"])]
        for tk in sorted(sym_exp.keys()):
            a, b = sym_exp[tk]["a"], sym_exp[tk]["b"]
            f0, f1 = first_last.get(tk, [0, 0])
            sel.append(f"{tk},{a},{b},{a + b},{f0},{f1}")
        sel.append(f"TRAIN_A_TOTAL,{a_n},0,{a_n},{min_do_a},{max_do_a}")
        sel.append(f"TRAIN_B_TOTAL,0,{b_n},{b_n},{min_do_b},{max_do_b}")
        sel.append(f"TRAIN_TOTAL,{a_n},{b_n},{a_n + b_n},{min_do_all},{max_do_all}")
        arts[f"cg_maisr_d4_subject_exposure_{bid}.csv"] = "\n".join(sel)

        ph = schemas["pack_stats"]
        ps_req = [c for c in ph if c != "support_reason"]
        pl = [",".join(ph)]
        for p in _D4_PACKS:
            s = pack_stats[p["id"]]
            pl.append(",".join(
                str(int(p["id"] == chosen_id) if h == "selected" else s.get(h, ""))
                for h in ph
            ))
        arts[f"cg_maisr_d4_pack_stats_{bid}.csv"] = "\n".join(pl)

        ml = [",".join(schemas["monotonicity"])]
        for r in mono_rows:
            ml.append(",".join(str(r[k]) for k in schemas["monotonicity"]))
        arts[f"cg_maisr_d4_monotonicity_{bid}.csv"] = "\n".join(ml)

        sk_cols = schemas["stability"]
        sl = [",".join(sk_cols)]
        for r in stab_rows:
            sl.append(",".join(str(r.get(k, "0")) for k in sk_cols))
        arts[f"cg_maisr_d4_stability_{bid}.csv"] = "\n".join(sl)

        es = [",".join(schemas["episode_summary"])]
        for pid in [p["id"] for p in _D4_PACKS]:
            s = pack_stats[pid]
            es.append(f"{pid},BROAD_FAMILY,MACRO,{s['broad_family_episodes']},TRAIN")
            es.append(f"{pid},LOCAL_SECTOR,HELD,{s['local_sector_episodes']},TRAIN")
            es.append(f"{pid},DEFENSIVE,MACRO,{s['defensive_episodes']},TRAIN")
        arts[f"cg_maisr_d4_episode_summary_{bid}.csv"] = "\n".join(es)

        me, he = eps
        el = [",".join(schemas["selected_episodes"])]
        for e in list(me) + list(he):
            el.append(f"{chosen_id or 'NONE'},{e['label']},{e['subject']},{e['start']},{e['end']},{e['n']},{e['day']}")
        if len(el) == 1:
            el.append("NONE,NO_SELECTED_PACK,NONE,0,0,0,0")
        arts[f"cg_maisr_d4_selected_episodes_{bid}.csv"] = "\n".join(el)

        kw = [",".join(schemas["known_windows"])]
        for pack in _D4_PACKS:
            for wname, w0, w1 in _D4_KNOWN_WINDOWS:
                wrows = [r for r in self._d4_raw if w0 <= r["do"] <= w1]
                _, _, me_w, he_w = self._D4LabelPack(pack, wrows)
                bf = d4_broad_family_count(me_w)
                sys_w = sum(1 for e in me_w if e["label"] == "SYSTEMIC_LIQUIDITY_STRESS")
                rate_w = sum(1 for e in me_w if e["label"] == "RATE_INFLATION_STRESS")
                def_w = sum(1 for e in me_w if e["label"] == "DEFENSIVE_ROTATION")
                loc_w = sum(1 for e in he_w if e["label"] == "LOCAL_ASSET_STRESS")
                sec_w = sum(1 for e in he_w if e["label"] == "SECTOR_STRESS")
                held_w = len(d4_held_pairs([r for r in wrows if d4_is_subject_row(r)]))
                days = [e["day"] for e in list(me_w) + list(he_w)]
                first_s = min(days) if days else 0
                last_s = max(days) if days else 0
                kw.append(
                    f"{pack['id']},{wname},{bf},{sys_w},{rate_w},{def_w},{loc_w},{sec_w},"
                    f"{held_w},{first_s},{last_s},AUDIT"
                )
        arts[f"cg_maisr_d4_known_windows_{bid}.csv"] = "\n".join(kw)

        cl = schemas["classifiers"]
        sel_ids = {r["id"] for r in chosen}
        clines = [",".join(cl)]
        for r in scored:
            f1 = r.get("f1") or {}
            clines.append(",".join(str(x) for x in [
                r["id"], r.get("s"), r.get("a"), r.get("b"), r.get("h"),
                _d4f(r.get("score"), 6), _d4f(r.get("macro_f1"), 6), r.get("valid", 0),
                r.get("validity_reason"), int(r["id"] in sel_ids), r.get("sig_hash"),
                r.get("macro_sig_hash"), r.get("subject_sig_hash"), r.get("combined_sig_hash"),
                r.get("n", 0), _d4f(f1.get("BROAD_EQUITY_STRESS"), 4), _d4f(f1.get("LOCAL_ASSET_STRESS"), 4),
                _d4f(f1.get("SECTOR_STRESS"), 4), _d4f(f1.get("DEFENSIVE_ROTATION"), 4),
            ]))
        arts[f"cg_maisr_d4_classifiers_{bid}.csv"] = "\n".join(clines)

        kind_map = {
            f"cg_maisr_d4_identity_{bid}.csv": "identity",
            f"cg_maisr_d4_symbol_roles_{bid}.csv": "symbol_roles",
            f"cg_maisr_d4_gold_continuity_{bid}.csv": "gold_continuity",
            f"cg_maisr_d4_distributions_{bid}.csv": "distributions",
            f"cg_maisr_d4_subject_exposure_{bid}.csv": "subject_exposure",
            f"cg_maisr_d4_pack_stats_{bid}.csv": "pack_stats",
            f"cg_maisr_d4_monotonicity_{bid}.csv": "monotonicity",
            f"cg_maisr_d4_stability_{bid}.csv": "stability",
            f"cg_maisr_d4_episode_summary_{bid}.csv": "episode_summary",
            f"cg_maisr_d4_selected_episodes_{bid}.csv": "selected_episodes",
            f"cg_maisr_d4_known_windows_{bid}.csv": "known_windows",
            f"cg_maisr_d4_classifiers_{bid}.csv": "classifiers",
        }
        req_nb = {
            "identity": ["id", "pass", "n", "nav_diff_pct", "maxdd_diff_pp", "corr"],
            "symbol_roles": ["symbol", "role", "source"],
            "gold_continuity": ["metric", "value"],
            "subject_exposure": ["symbol", "held_days_a", "held_days_b", "held_days_total",
                                 "first_eligible_date", "last_eligible_date"],
            "pack_stats": list(schemas["pack_stats"]),
            "monotonicity": ["dimension", "fixed", "less_severe", "more_severe", "metric", "lhs", "rhs", "pass"],
            "stability": list(schemas["stability"]),
            "episode_summary": ["pack", "state", "subject", "episode_count", "window"],
            "selected_episodes": ["pack", "state", "subject", "n", "day"],
            "known_windows": ["pack", "window", "broad_family_episodes", "systemic_episodes",
                              "rate_episodes", "defensive_episodes", "local_episodes",
                              "sector_episodes", "eligible_held_symbol_days", "status"],
            "classifiers": ["id", "s", "a", "b", "h", "score", "macro_f1", "valid",
                            "validity_reason", "selected", "sig_hash", "macro_sig_hash",
                            "subject_sig_hash", "combined_sig_hash", "n"],
        }
        opt_blank = {
            "identity": {"peak", "trough", "recovery"},
            "distributions": {"min", "p01", "p05", "p10", "p25", "p50", "p75", "p90", "p95", "p99", "max"},
            "selected_episodes": {"start", "end"},
            "known_windows": {"first_signal", "last_signal"},
            "classifiers": {"f1_BROAD", "f1_LOCAL", "f1_SECTOR", "f1_DEF"},
            "stability": {"subject_density_a", "subject_density_b", "subject_ratio",
                          "broad_density_a", "broad_density_b", "broad_ratio",
                          "defensive_density_a", "defensive_density_b", "defensive_ratio"},
        }
        ukeys = {
            "pack_stats": "id",
            "known_windows": ("pack", "window"),
            "classifiers": "id",
            "symbol_roles": "symbol",
        }
        val_rows = []
        all_pass = True
        fail_reason = ""
        art_hashes = {}
        for name in sorted(arts.keys()):
            text = arts[name]
            kind = kind_map[name]
            schema = schemas[kind]
            exp = d4_calibration_artifact_expected_rows(kind)
            if exp is None:
                exp = max(0, len(text.splitlines()) - 1)
            if kind == "distributions":
                vr = d4_validate_distributions_csv(text)
            else:
                vr = d4_validate_csv_artifact(
                    name, text, schema, exp, req_nb.get(kind, schema[:1]),
                    unique_key=ukeys.get(kind),
                    optional_blank=opt_blank.get(kind))
            passed = int(vr["pass"])
            if not vr["pass"] and all_pass:
                all_pass = False
                fail_reason = f"ARTIFACT_VALIDATION_FAIL:{name}:{vr['reason']}"
            sha = _d4_sha(text)
            art_hashes[name] = sha
            val_rows.append({
                "artifact": name,
                "bytes": len(text.encode("utf-8")),
                "rows": vr["row_count"],
                "expected_rows": exp,
                "sha256": sha,
                "exists": 1,
                "schema_ok": int(vr["header_exact"]),
                "row_count_ok": int(vr["row_count_ok"]),
                "placeholder_only": int(vr["placeholder_only"]),
                "pass": passed,
            })

        manifest = {
            "schema_version": "D4.2A", "source_commit": src,
            "selected_pack": chosen_id, "selected_classifiers": [r["id"] for r in chosen],
            "calibration_result": cal_result, "calibration_reason": cal_reason,
            "artifact_sha256": dict(art_hashes), "pack_support": pack_stats, "pack_stability": stab_rows,
            "gold_train_coverage": gold_cov, "coverage_ratio": cov.get("coverage_ratio"),
            "mono_ok": int(all(r["pass"] for r in mono_rows)),
        }
        mhash, _ = d4_manifest_hash(manifest)
        manifest["manifest_sha256"] = mhash
        mjson = json.dumps({**manifest, "manifest_sha256": mhash}, sort_keys=True, separators=(",", ":"))
        mname = f"cg_maisr_d4_manifest_{bid}.json"
        arts[mname] = mjson
        art_hashes[mname] = _d4_sha(mjson)
        mvr = d4_validate_manifest_json(mjson, expected_sha=mhash)
        m_pass = int(mvr["pass"])
        if not mvr["pass"] and all_pass:
            all_pass = False
            fail_reason = f"ARTIFACT_VALIDATION_FAIL:{mname}:{mvr['reason']}"
        val_rows.append({
            "artifact": mname,
            "bytes": len(mjson.encode("utf-8")),
            "rows": 0,
            "expected_rows": 0,
            "sha256": art_hashes[mname],
            "exists": 1,
            "schema_ok": int(mvr["parse_ok"] and mvr["keys_ok"]),
            "row_count_ok": 1,
            "placeholder_only": 0,
            "pass": m_pass,
        })

        self_c = d4_artifact_validation_self_contract()
        vl = ["artifact,bytes,rows,expected_rows,sha256,exists,schema_ok,row_count_ok,placeholder_only,pass"]
        for vr in val_rows:
            vl.append(",".join(str(vr[k]) for k in (
                "artifact", "bytes", "rows", "expected_rows", "sha256", "exists",
                "schema_ok", "row_count_ok", "placeholder_only", "pass")))
        vl.append(",".join([
            self_c["artifact"], 0, len(val_rows), len(val_rows), self_c["sha256"],
            1, 1, 1, 0, int(all_pass),
        ]))
        vname = f"cg_maisr_d4_artifact_validation_{bid}.csv"
        arts[vname] = "\n".join(vl)

        self._d4_art_used = 0
        for name, text in arts.items():
            self._D4Emit(name, text)
        return mhash, arts, all_pass, fail_reason

    def _D4ExecutionProofEOA(self, parity_ok) -> bool:
        self._MsLog(
            "CG_MAISR_D4_EXECUTION_PROOF_FINAL,result=FAILED,reason=D4_2_EXECUTION_ENGINE_NOT_IMPLEMENTED,"
            "research_conclusion=NOT_REACHED"
        )
        return True

    def _D4CompareLedIdentity(self, led, label):
        if led is None:
            self._MsLog(
                f"CG_MAISR_D42_IDENTITY_FINAL,id={label},pass=NO,identity_observed=NO"
            )
            return False
        cmp_fn = getattr(self, "CgShadowIdentityCompare", None)
        if not cmp_fn:
            return int(led.get("synth_fills", 0) or 0) == 0
        cmp = dict(cmp_fn(list(led.get("rets") or [])))
        synth0 = int(led.get("synth_fills", 0) or 0) == 0
        passed = bool(cmp.get("pass")) and synth0
        self._MsLog(
            f"CG_MAISR_D42_IDENTITY_FINAL,id={label},pass={'YES' if passed else 'NO'},"
            f"identity_observed=YES,n={cmp.get('n',0)},nav_diff_pct={_d4f(cmp.get('nav_d'),6)},"
            f"synth_fills={led.get('synth_fills',0)}"
        )
        return passed

    def _D4EconomicEOA(self, parity_ok) -> bool:
        self._MsLog(
            "CG_MAISR_D4_ECONOMIC_FINAL,result=FAILED,reason=D4_2_EVENT_ENGINE_NOT_IMPLEMENTED,"
            "research_conclusion=NOT_REACHED,policies_evaluated=0"
        )
        return True

    def CgMaisrD4OnProductionFill(self, symbol, signed_qty, price, fee, meta):
        if not getattr(self, "cg_maisr_d4_enable", False):
            return
        phase = getattr(self, "cg_maisr_d4_phase", "") or ""
        if phase not in ("EXECUTION_PROOF", "ECONOMIC"):
            return
        # Lazy-clone identity ledgers from control on first fill
        ctrl = getattr(self, "_sr_ctrl", None)
        if self._d4_canary_led is None and ctrl is not None:
            self._d4_canary_led = self._D4CloneLed(ctrl)
            self._d4_overlay_led = self._D4CloneLed(ctrl)
            self._d4_reanchor_led = self._D4CloneLed(ctrl)
        for led in (self._d4_canary_led, self._d4_overlay_led, self._d4_reanchor_led):
            if led is None:
                continue
            # Cap buys against same-day cut ceiling
            if signed_qty > 0:
                from cg_maisr_d4_core import d4_cap_buy_qty
                q0 = float((led.get("qty") or {}).get(symbol, 0) or 0)
                desired = q0 + float(signed_qty)
                capped, blocked = d4_cap_buy_qty(led, symbol, desired)
                apply_q = capped - q0
                if blocked > 0:
                    led["blocked_rerisk_qty"] = float(led.get("blocked_rerisk_qty", 0) or 0) + blocked
                    led["same_day_rerisk_count"] = int(led.get("same_day_rerisk_count", 0) or 0) + 1
                if abs(apply_q) < 1e-12:
                    continue
                signed_qty = apply_q
            apply = getattr(self, "_SrApplyFill", None)
            if apply:
                apply(led, symbol, signed_qty, price, fee)
            else:
                d4_apply_cut_fill(led, symbol, signed_qty, price, fee)

    def CgMaisrD4OnReplayMark(self, date, prices):
        if not getattr(self, "cg_maisr_d4_enable", False):
            return
        # Next-session reset of cut ceilings
        for led in (self._d4_canary_led, self._d4_overlay_led, self._d4_reanchor_led):
            if not led:
                continue
            if led.get("cut_day") is not None and led.get("cut_day") != date:
                led["cut_ceiling_qty"] = {}
                led["last_cut_mult"] = {}
                led["cut_day"] = None
                led["same_day_cut"] = False
            # mark NAV for identity rets
            apply = getattr(self, "_SrApplyDaily", None)
            nav_fn = getattr(self, "_SrNav", None)
            if apply and nav_fn and prices:
                n, _ = nav_fn(led, prices)
                apply(led, led.get("_prev_nav"), n, None, None, None)
                led["_prev_nav"] = n
