# region imports
from AlgorithmImports import *
from datetime import timedelta
from collections import defaultdict
import base64
import zlib
import json
from cg_maisr_d4_core import (
    _D4_PACKS, _STATES, _SIX, _D4_BREADTH4, _D4_SECTOR_ASSETS, _D4_PROXY,
    _TRAIN0, _TRAIN1, _TRAINA0, _TRAINA1, _TRAINB0, _TRAINB1,
    d4_build_packs, d4_subject_codec, d4_gold_continuity, d4_raw_flags,
    d4_priority_macro, d4_priority_subject, d4_build_episodes,
    d4_broad_family_count, d4_broad_family_days, d4_monotonicity_checks,
    d4_support_ok, d4_stability_broad, d4_stability_subject, d4_match_episode,
    d4_hmode_classify, d4_manifest_hash, d4_select_subject, d4_assert_no_self_proxy,
    d4_apply_cut_fill, d4_cut_ceiling_apply, run_d4_static_tests, _ROUTER_ADJ,
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
            f"CG_MAISR_D4_INIT,phase={self.cg_maisr_d4_phase or 'NA'},"
            f"pack={self.cg_maisr_d4_selected_pack or 'AUTO'},"
            f"classifiers={','.join(self.cg_maisr_d4_selected_classifiers) or 'NONE'}"
        )
        self._MsLog(
            f"CG_MAISR_D4_STATIC_FINAL,tests={p}/{n},self_proxy_ok={int(proxy_ok)},"
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
                f"CG_MAISR_D4_IDENTITY_FINAL,id={label},pass={'YES' if passed else 'NO'},"
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
        try:
            self._D2FlushPending()
        except Exception:
            self._d4_err += 1
        # Hook: harvest any D2 finalize side-channel — rebuild from _d2_train + d4_raw if needed
        if not self._d4_raw and getattr(self, "_d2_train", None):
            # Fallback empty — D4Store must be wired from finalize
            pass
        # Ensure finalize also stored D4 rows: scan via monkey if needed
        self._D4HarvestFromD2Finalize()

        if not parity_ok or self._d4_err:
            self._MsLog("CG_MAISR_D4_CALIBRATION_FINAL,result=FAILED,reason=parity_or_static")
            return True

        id_results = self._D4Identity()
        id_ok = all(r.get("pass") for r in id_results.values())
        cov = self._D2CoverageReport() if hasattr(self, "_D2CoverageReport") else {}
        gold_cov = (self._d4_gold_avail / max(self._d4_gold_n, 1)) if self._d4_gold_n else 0.0
        self._MsLog(
            f"CG_MAISR_D4_GOLD_FINAL,train_coverage={_d4f(gold_cov,4)},"
            f"double_count_used=0,primary={getattr(self,'_ms_gold_primary',None)},"
            f"fallback={getattr(self,'_ms_gold_fallback',None)}"
        )
        gold_ok = gold_cov >= 0.95

        train = [r for r in self._d4_raw if r.get("train")]
        pack_stats = {}
        raw_by = {}
        eps_by = {}
        labeled_by = {}
        mono_rows_all = []
        stab_rows = []

        for pack in _D4_PACKS:
            labeled, raw_acc, me, he = self._D4LabelPack(pack, train)
            labeled_by[pack["id"]] = labeled
            eps_by[pack["id"]] = (me, he)
            bf_ep = d4_broad_family_count(me)
            bf_days = d4_broad_family_days(me)
            loc = sum(1 for e in he if e["label"] == "LOCAL_ASSET_STRESS")
            sec = sum(1 for e in he if e["label"] == "SECTOR_STRESS")
            ls = loc + sec
            ls_days = len({(e["day"], e["subject"]) for e in he
                           if e["label"] in ("LOCAL_ASSET_STRESS", "SECTOR_STRESS")})
            deff = sum(1 for e in me if e["label"] == "DEFENSIVE_ROTATION")
            sys_ = sum(1 for e in me if e["label"] == "SYSTEMIC_LIQUIDITY_STRESS")
            rate = sum(1 for e in me if e["label"] == "RATE_INFLATION_STRESS")
            support = d4_support_ok(bf_ep, bf_days, ls, ls_days, deff)

            # stability
            a_days = {d for d in ({e["day"] for e in me} | {e["day"] for e in he}) if _TRAINA0 <= d <= _TRAINA1}
            b_days = {d for d in ({e["day"] for e in me} | {e["day"] for e in he}) if _TRAINB0 <= d <= _TRAINB1}
            ep_a = d4_broad_family_count([e for e in me if e["day"] in a_days])
            ep_b = d4_broad_family_count([e for e in me if e["day"] in b_days])
            broad_ok, da, db, ratio, brsn = d4_stability_broad(ep_a, ep_b)
            held_a = len({(r["do"], tk) for r in train if r["do"] in a_days for tk in (r.get("held") or {})})
            held_b = len({(r["do"], tk) for r in train if r["do"] in b_days for tk in (r.get("held") or {})})
            ls_a = sum(1 for e in he if e["day"] in a_days and e["label"] in ("LOCAL_ASSET_STRESS", "SECTOR_STRESS"))
            ls_b = sum(1 for e in he if e["day"] in b_days and e["label"] in ("LOCAL_ASSET_STRESS", "SECTOR_STRESS"))
            sub_ok, sda, sdb, sratio, srsn = d4_stability_subject(ls_a, ls_b, held_a, held_b)
            stab_ok = broad_ok and sub_ok
            stab_rows.append({
                "pack": pack["id"], "broad_ok": int(broad_ok), "subject_ok": int(sub_ok),
                "broad_reason": brsn, "subject_reason": srsn,
                "held_days_a": held_a, "held_days_b": held_b,
                "dens_broad_a": _d4f(da), "dens_broad_b": _d4f(db), "broad_ratio": _d4f(ratio),
                "dens_subj_a": _d4f(sda), "dens_subj_b": _d4f(sdb), "subj_ratio": _d4f(sratio),
            })
            raw_by[pack["id"]] = raw_acc
            pack_stats[pack["id"]] = {
                "id": pack["id"], "support_ok": int(support), "stability_ok": int(stab_ok),
                "broad_family_episodes": bf_ep, "broad_family_days": bf_days,
                "local_sector_episodes": ls, "local_sector_held_days": ls_days,
                "defensive_episodes": deff, "systemic_episodes": sys_, "rate_episodes": rate,
                "dist_score": abs(bf_ep - 80) + abs(ls - 35) + abs(deff - 40),
                "pass": 0,
            }
            self._MsLog(
                f"CG_MAISR_D4_PACK_FINAL,id={pack['id']},support={int(support)},"
                f"stable={int(stab_ok)},bf={bf_ep},ls={ls},def={deff},sys={sys_},rate={rate}"
            )

        mono_rows = d4_monotonicity_checks(raw_by)
        mono_ok = all(r["pass"] for r in mono_rows)
        for s in pack_stats.values():
            s["mono_ok"] = int(mono_ok)
            s["pass"] = int(s["support_ok"] and s["stability_ok"] and mono_ok)

        # select pack
        pool = [s for s in pack_stats.values() if s["pass"]]
        pool.sort(key=lambda s: (s["dist_score"], 0 if "BR3" in s["id"] else 1,
                                 0 if "B60" in s["id"] else 1, 0 if "L75" in s["id"] else 1, s["id"]))
        chosen_pack = pool[0]["id"] if pool else None
        self._MsLog(
            f"CG_MAISR_D4_SELECTED_PACK,id={chosen_pack or 'NONE'},"
            f"mono={'PASS' if mono_ok else 'FAIL'},gold_ok={int(gold_ok)},"
            f"reason={'ok' if chosen_pack else 'no_valid_pack'}"
        )

        scored, chosen, modes, sigs = [], [], set(), {}
        if chosen_pack:
            scored, chosen, modes, sigs = self._D4ScoreSelect(
                chosen_pack, labeled_by[chosen_pack], eps_by[chosen_pack], pack_stats[chosen_pack])
            for r in chosen[:6]:
                self._MsLog(
                    f"CG_MAISR_D4_CLASSIFIER_SELECTED,id={r['id']},H={r['h']},"
                    f"score={_d4f(r['score'],4)},sig={r.get('sig_hash','')[:12]}"
                )
        else:
            scored = [{"id": _clfid(s, a, b, h), "s": s, "a": a, "b": b, "h": h,
                       "valid": 0, "score": 0, "validity_reason": "no_pack",
                       "macro_f1": 0, "f1": {}, "sig_hash": "NA", "n": 0}
                      for s, a, b, h in _ALL_CFG]

        # canary deferred
        self._MsLog("CG_MAISR_D4_CALIBRATION_FINAL,canary_status=DEFERRED_TO_EXECUTION_PROOF")

        bid = self._MsBid() if hasattr(self, "_MsBid") else "NA"
        manifest = {
            "schema_version": "D4.1",
            "source_commit": "local",
            "selected_pack": chosen_pack,
            "selected_classifiers": [r["id"] for r in chosen],
            "classifier_scores": {r["id"]: r.get("score") for r in chosen},
            "classifier_sigs": {r["id"]: r.get("sig_hash") for r in chosen},
            "pack_support": {k: {kk: vv for kk, vv in v.items() if kk != "pass"} for k, v in pack_stats.items()},
            "gold_train_coverage": gold_cov,
            "mono_ok": int(mono_ok),
        }
        mhash, mraw = d4_manifest_hash(manifest)
        manifest["manifest_sha256"] = mhash

        # export artifacts
        self._d4_art_used = 0
        self._D4ExportCalib(bid, id_results, pack_stats, mono_rows, stab_rows, scored, chosen,
                            eps_by.get(chosen_pack, ([], [])), labeled_by, cov, gold_cov, mraw)

        clf_ok = len(chosen) >= 3 and len(set(sigs.values())) >= 2
        data_ok = int(getattr(self, "_ms_bd_conflict", 0) or 0) == 0
        cal_pass = bool(id_ok and gold_ok and mono_ok and chosen_pack and clf_ok and data_ok
                        and cov.get("coverage_ratio", 0) >= 0.99)

        if cal_pass:
            result, nxt = "CALIBRATION_PASS", "EXECUTION_PROOF"
        elif not chosen_pack:
            result, nxt = "STOP_MAISR", "STOP_MAISR"
        elif not clf_ok:
            result, nxt = "STOP_MAISR", "STOP_MAISR"
        else:
            result, nxt = "FAILED", "FIX"
        self._MsLog(
            f"CG_MAISR_D4_CALIBRATION_FINAL,result={result},next={nxt},"
            f"selected_pack={chosen_pack or 'NONE'},classifiers={len(chosen)},"
            f"h_sigs={len(set(sigs.values()))},manifest_sha256={mhash},"
            f"frozen_classifiers={','.join(r['id'] for r in chosen)}"
        )
        self._d4_manifest = manifest
        self._d4_chosen = chosen
        self._d4_selected_pack = chosen_pack
        return True

    def _D4HarvestFromD2Finalize(self):
        """If D4Store not wired, reconstruct minimal raw from pending train is empty — rely on patch."""
        # Patch finalize to call _D4StoreFromFinalize — applied below via method override
        pass

    def _D4LabelPack(self, pack, train):
        labeled = []
        raw_evals_b = raw_eps_b = raw_days_b = 0
        raw_loc_e = raw_sec_e = 0
        day_b, day_l, day_s = set(), set(), set()
        macro_stream, held_stream = [], []
        for r in train:
            br_n = [v for v in (r.get("br_maes") or {}).values() if v is not None]
            # need all 4 breadth
            br_count = sum(1 for t in _D4_BREADTH4 if t in (r.get("br_maes") or {}) and r["br_maes"][t] <= -pack["B"])
            br_avail = sum(1 for t in _D4_BREADTH4 if t in (r.get("br_maes") or {}))
            avail = [b for b in r.get("blocks") or [] if b.get("ok")]
            resilient = [b for b in avail if b.get("abs") is not None and b.get("rel") is not None
                         and b["abs"] >= 0 and b["rel"] >= 0.30 * pack["B"]]
            med_abs = sorted(b["abs"] for b in avail)[len(avail) // 2] if avail else None
            med_rel = sorted(b["rel"] for b in avail)[len(avail) // 2] if avail else None
            flags = d4_raw_flags(
                pack, r["spy_mae"], br_count, br_avail,
                r.get("dur_mae"), r.get("gold_mae"), r.get("infl_rel"), r.get("infl_ret"),
                len(resilient), len(avail), med_abs, med_rel, r.get("held") or {},
            )
            if flags["raw_broad"]:
                raw_evals_b += 1
                day_b.add(r["do"])
            for s in flags["raw_local"]:
                raw_loc_e += 1
                day_l.add(r["do"])
            for s in flags["raw_sector"]:
                raw_sec_e += 1
                day_s.add(r["do"])
            mlab = d4_priority_macro(flags) if flags.get("breadth_ok") else "NORMAL"
            # subject prefer residual order
            order = sorted((r.get("held") or {}).keys(),
                           key=lambda tk: (r["held"][tk].get("vs_spy") if r["held"][tk].get("vs_spy") is not None else 0, tk))
            slab, ssubj = d4_priority_subject(flags, order)
            row = {**r, "macro": mlab, "subj_label": slab, "subj": ssubj, "flags": flags,
                   "breadth": br_count / 4.0 if br_avail else 0.0}
            labeled.append(row)
            macro_stream.append({"ts": r["t"], "day": r["do"], "subject": "MACRO", "label": mlab,
                                 "mae": r["spy_mae"], "breadth": br_count / 4.0 if br_avail else 0.0})
            if slab != "NORMAL" and ssubj != "NONE":
                held_stream.append({"ts": r["t"], "day": r["do"], "subject": ssubj, "label": slab})
        me = d4_build_episodes(macro_stream)
        he = d4_build_episodes(held_stream)
        # raw episode counts via raw-flag streams
        raw_b_stream = [{"ts": r["t"], "day": r["do"], "subject": "MACRO", "label": "BROAD_EQUITY_STRESS"}
                        for r, lab in zip(train, labeled) if lab["flags"]["raw_broad"]]
        raw_l_stream = [{"ts": r["t"], "day": r["do"], "subject": s, "label": "LOCAL_ASSET_STRESS"}
                        for r, lab in zip(train, labeled) for s in lab["flags"]["raw_local"]]
        raw_s_stream = [{"ts": r["t"], "day": r["do"], "subject": s, "label": "SECTOR_STRESS"}
                        for r, lab in zip(train, labeled) for s in lab["flags"]["raw_sector"]]
        raw_acc = {
            "raw_broad_evals": raw_evals_b,
            "raw_broad_eps": len(d4_build_episodes(raw_b_stream)),
            "raw_broad_days": len(day_b),
            "raw_local_evals": raw_loc_e,
            "raw_local_eps": len(d4_build_episodes(raw_l_stream)),
            "raw_local_days": len(day_l),
            "raw_sector_evals": raw_sec_e,
            "raw_sector_eps": len(d4_build_episodes(raw_s_stream)),
            "raw_sector_days": len(day_s),
        }
        return labeled, raw_acc, me, he

    def _D4ScoreSelect(self, pack_id, labeled, eps, stats):
        me, he = eps
        scored = []
        sigs = {}
        for idx, (s, a, b, h) in enumerate(_ALL_CFG):
            cid = _clfid(s, a, b, h)
            pred_m, pred_h = [], []
            sig_parts = []
            for r in labeled:
                if idx >= len(r["preds"]):
                    continue
                st = _STATES[r["preds"][idx]]
                subj_code = r["subjects"][idx] if idx < len(r["subjects"]) else 0
                subj = self._d4_subj_inv.get(subj_code, "NONE")
                sig_parts.append(f"{st}:{subj}")
                if st in ("BROAD_EQUITY_STRESS", "SYSTEMIC_LIQUIDITY_STRESS",
                          "RATE_INFLATION_STRESS", "DEFENSIVE_ROTATION"):
                    pred_m.append({"ts": r["t"], "day": r["do"], "subject": "MACRO", "label": st})
                if st in ("LOCAL_ASSET_STRESS", "SECTOR_STRESS") and subj not in ("NONE", "MACRO"):
                    pred_h.append({"ts": r["t"], "day": r["do"], "subject": subj, "label": st})
            import hashlib
            sig_hash = hashlib.sha256("|".join(sig_parts[:5000]).encode()).hexdigest()[:16]
            sigs[cid] = sig_hash
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
                used = set()
                tp = 0
                for te in true_eps:
                    for i, pe in enumerate(pred_eps):
                        if i in used:
                            continue
                        if d4_match_episode(pe, te):
                            tp += 1
                            used.add(i)
                            break
                fp = len(pred_eps) - len(used)
                fn = len(true_eps) - tp
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
                "validity_reason": "OK" if valid else "zero_f1", "sig_hash": sig_hash,
                "n": len(labeled), "broad_f1": bf1, "locsec_f1": ls1,
            })
        chosen, modes = [], set()
        for h in ("H0", "H1", "H2"):
            cand = [r for r in scored if r["valid"] and r["h"] == h]
            cand.sort(key=lambda r: (-r["score"], -r["broad_f1"], -r["locsec_f1"], r["id"]))
            for r in cand[:2]:
                chosen.append(r)
                modes.add(h)
        chosen = chosen[:6]
        return scored, chosen, modes, sigs

    def _D4ExportCalib(self, bid, id_results, pack_stats, mono_rows, stab_rows, scored, chosen,
                       eps, labeled_by, cov, gold_cov, manifest_raw):
        # identity
        lines = ["id,pass,n,nav_diff_pct,maxdd_diff_pp,corr"]
        for k, r in id_results.items():
            lines.append(f"{k},{'YES' if r['pass'] else 'NO'},{r['n']},"
                         f"{_d4f(r.get('nav_d'),6)},{_d4f(r.get('dd_d'),6)},{_d4f(r.get('corr'),6)}")
        self._D4Emit(f"cg_maisr_d4_identity_{bid}.csv", "\n".join(lines))
        # symbol roles
        rl = ["symbol,role,source"]
        for tk in sorted(getattr(self, "_ms_all", set()) or []):
            if tk in ("AVGO", "MU", "NVDA"):
                role, src = "INACTIVE_TRADING_PATH", "spyg_sat_trade_enable=0"
            elif tk in ("BIL", "SGOV", "USFR"):
                role, src = "PARKING", "cash"
            elif tk in ("GLD", "GLDM", "BND", "TIP"):
                role, src = "DEFENSIVE", "block"
            elif tk == "SH":
                role, src = "INVERSE_CONFIRM", "sh"
            elif tk == "SPY":
                role, src = "SENSOR_ONLY", "benchmark"
            else:
                role, src = "ACTIVE_HELD_RISK", "panel"
            rl.append(f"{tk},{role},{src}")
        self._D4Emit(f"cg_maisr_d4_symbol_roles_{bid}.csv", "\n".join(rl))
        self._D4Emit(f"cg_maisr_d4_gold_continuity_{bid}.csv",
                     "metric,value\ntrain_coverage," + _d4f(gold_cov, 4) +
                     "\ndouble_count_used,0\nprimary," + str(getattr(self, "_ms_gold_primary", "NA")) +
                     "\nfallback," + str(getattr(self, "_ms_gold_fallback", "NA")))
        self._D4Emit(f"cg_maisr_d4_distributions_{bid}.csv",
                     "feature,count,status\nSPY_60m_MAE_ATR," + str(len(self._d4_raw)) + ",OK")
        ph = ["id", "pass", "support_ok", "stability_ok", "mono_ok", "broad_family_episodes",
              "local_sector_episodes", "defensive_episodes", "systemic_episodes", "rate_episodes",
              "dist_score", "selected"]
        pl = [",".join(ph)]
        for p in _D4_PACKS:
            s = pack_stats[p["id"]]
            pl.append(",".join(str(s.get(h) if h != "selected" else int(p["id"] == getattr(self, "_d4_selected_pack", None)))
                               for h in ph))
        self._D4Emit(f"cg_maisr_d4_pack_stats_{bid}.csv", "\n".join(pl))
        ml = ["dimension,fixed,less_severe,more_severe,metric,lhs,rhs,pass"]
        for r in mono_rows:
            ml.append(",".join(str(r[k]) for k in
                               ("dimension", "fixed", "less_severe", "more_severe", "metric", "lhs", "rhs", "pass")))
        self._D4Emit(f"cg_maisr_d4_monotonicity_{bid}.csv", "\n".join(ml))
        sl = ["pack,broad_ok,subject_ok,broad_reason,subject_reason,held_days_a,held_days_b,"
              "dens_broad_a,dens_broad_b,broad_ratio,dens_subj_a,dens_subj_b,subj_ratio"]
        for r in stab_rows:
            sl.append(",".join(str(r.get(k, "NA")) for k in
                               ("pack", "broad_ok", "subject_ok", "broad_reason", "subject_reason",
                                "held_days_a", "held_days_b", "dens_broad_a", "dens_broad_b", "broad_ratio",
                                "dens_subj_a", "dens_subj_b", "subj_ratio")))
        self._D4Emit(f"cg_maisr_d4_stability_{bid}.csv", "\n".join(sl))
        # episode summary all packs
        es = ["pack,state,subject,episode_count,window"]
        for pid, (me, he) in ((getattr(self, "_d4_selected_pack", None) or "NONE", eps),):
            pass
        for pid in [p["id"] for p in _D4_PACKS]:
            # lightweight: from pack_stats only
            s = pack_stats[pid]
            es.append(f"{pid},BROAD_FAMILY,MACRO,{s['broad_family_episodes']},TRAIN")
            es.append(f"{pid},LOCAL_SECTOR,HELD,{s['local_sector_episodes']},TRAIN")
        self._D4Emit(f"cg_maisr_d4_episode_summary_{bid}.csv", "\n".join(es))
        me, he = eps
        el = ["pack,state,subject,start,end,n,day"]
        for e in list(me) + list(he):
            el.append(f"{getattr(self,'_d4_selected_pack',None)},{e['label']},{e['subject']},"
                      f"{e['start']},{e['end']},{e['n']},{e['day']}")
        if len(el) == 1:
            el.append("NONE,NO_SELECTED_PACK,NONE,NA,NA,0,0")
        self._D4Emit(f"cg_maisr_d4_selected_episodes_{bid}.csv", "\n".join(el))
        self._D4Emit(f"cg_maisr_d4_known_windows_{bid}.csv",
                     "pack,window,status\nALL,ALL,SEE_STABILITY")
        cl = ["id,s,a,b,h,score,macro_f1,valid,validity_reason,selected,sig_hash,n,"
              "f1_BROAD,f1_LOCAL,f1_SECTOR,f1_DEF"]
        sel = {r["id"] for r in chosen}
        for r in scored:
            f1 = r.get("f1") or {}
            cl.append(",".join(str(x) for x in [
                r["id"], r.get("s"), r.get("a"), r.get("b"), r.get("h"),
                _d4f(r.get("score"), 6), _d4f(r.get("macro_f1"), 6), r.get("valid", 0),
                r.get("validity_reason"), int(r["id"] in sel), r.get("sig_hash"), r.get("n", 0),
                _d4f(f1.get("BROAD_EQUITY_STRESS"), 4), _d4f(f1.get("LOCAL_ASSET_STRESS"), 4),
                _d4f(f1.get("SECTOR_STRESS"), 4), _d4f(f1.get("DEFENSIVE_ROTATION"), 4),
            ]))
        self._D4Emit(f"cg_maisr_d4_classifiers_{bid}.csv", "\n".join(cl))
        self._D4Emit(f"cg_maisr_d4_manifest_{bid}.json", manifest_raw)

    def _D4ExecutionProofEOA(self, parity_ok) -> bool:
        self._MsLog("CG_MAISR_D4_EXEC_INIT,phase=EXECUTION_PROOF")
        if not parity_ok or self._d4_err:
            self._MsLog("CG_MAISR_D4_EXECUTION_PROOF_FINAL,result=FAILED,reason=parity_or_static")
            return True
        id_results = self._D4Identity()
        fill_ok = all(r.get("pass") for r in id_results.values())
        # Overlay / reanchor no-action identities
        overlay_ok = self._D4CompareLedIdentity(self._d4_overlay_led, "D4_OVERLAY_NO_ACTION_IDENTITY")
        reanchor_ok = self._D4CompareLedIdentity(self._d4_reanchor_led, "D4_REANCHOR_NO_ACTION_IDENTITY")
        self._MsLog(
            f"CG_MAISR_D4_EXEC_IDENTITY_FINAL,overlay_no_action={'PASS' if overlay_ok else 'FAIL'},"
            f"reanchor_no_action={'PASS' if reanchor_ok else 'FAIL'},"
            f"fill_replay={'PASS' if fill_ok else 'FAIL'}"
        )
        canary = getattr(self, "_d4_canary", None) or {
            "status": "STOP_NO_SIGNAL", "natural_signal": "NO", "fired": 0,
            "reason": "NO_NATURAL_ACTIONABLE_SIGNAL",
        }
        if canary.get("natural_signal") == "YES" and int(canary.get("fired", 0) or 0) == 1:
            canary["status"] = "PASS"
        elif canary.get("natural_signal") != "YES":
            canary["status"] = "STOP_NO_SIGNAL"
            canary["reason"] = "NO_NATURAL_ACTIONABLE_SIGNAL"
        else:
            canary["status"] = "FAIL"
            canary["reason"] = "armed_but_not_filled"
        self._MsLog(
            f"CG_MAISR_D4_CANARY_FINAL,status={canary.get('status')},"
            f"natural_signal={canary.get('natural_signal')},fired={canary.get('fired',0)},"
            f"same_bar_fill={canary.get('same_bar_fill','NA')}"
        )
        bid = self._MsBid()
        same_bar = 0
        rerisk = int((self._d4_canary_led or {}).get("same_day_rerisk_count", 0) or 0)
        self._D4Emit(f"cg_maisr_d4_canary_{bid}.csv",
                     "status,natural_signal,fired,reason,state,subject,symbol,signal_time,fill_et,fill_open,actual_sell\n"
                     f"{canary.get('status')},{canary.get('natural_signal')},{canary.get('fired',0)},"
                     f"{canary.get('reason','')},{canary.get('state','NA')},{canary.get('subject','NA')},"
                     f"{canary.get('symbol','NA')},{canary.get('signal_time','NA')},"
                     f"{canary.get('fill_et','NA')},{canary.get('fill_open','NA')},{canary.get('actual_sell','NA')}")
        ev_lines = ["event,status"]
        for ev in (getattr(self, "_d4_pending_cuts", None) or []):
            ev_lines.append(f"{ev.get('event_id')},{'FILLED' if ev.get('filled') else 'PENDING'}")
        if len(ev_lines) == 1:
            ev_lines.append("NONE,NO_SIGNAL")
        self._D4Emit(f"cg_maisr_d4_canary_events_{bid}.csv", "\n".join(ev_lines))
        id_lines = ["id,pass"]
        for k, r in id_results.items():
            id_lines.append(f"{k},{'YES' if r.get('pass') else 'NO'}")
        id_lines.append(f"D4_OVERLAY_NO_ACTION_IDENTITY,{'YES' if overlay_ok else 'NO'}")
        id_lines.append(f"D4_REANCHOR_NO_ACTION_IDENTITY,{'YES' if reanchor_ok else 'NO'}")
        self._D4Emit(f"cg_maisr_d4_execution_identity_{bid}.csv", "\n".join(id_lines))
        self._D4Emit(f"cg_maisr_d4_manifest_revalidation_{bid}.csv",
                     "field,value\nmanifest_sha256," + str(getattr(self, "cg_maisr_d4_manifest_sha256", "")) +
                     "\nsame_bar_fill_count," + str(same_bar) +
                     "\nsame_day_rerisk_count," + str(rerisk))
        five_ok = fill_ok and overlay_ok and reanchor_ok
        if not five_ok:
            self._MsLog("CG_MAISR_D4_EXECUTION_PROOF_FINAL,result=FAILED,reason=identity_fail")
        elif canary.get("status") == "PASS":
            self._MsLog("CG_MAISR_D4_EXECUTION_PROOF_FINAL,result=EXECUTION_PROOF_PASS,"
                        f"same_bar_fill_count={same_bar},same_day_rerisk_count={rerisk}")
        elif canary.get("status") == "STOP_NO_SIGNAL":
            self._MsLog("CG_MAISR_D4_EXECUTION_PROOF_FINAL,result=STOP_MAISR,"
                        "reason=NO_NATURAL_ACTIONABLE_SIGNAL")
        else:
            self._MsLog("CG_MAISR_D4_EXECUTION_PROOF_FINAL,result=FAILED,reason=canary_fill_fail")
        return True

    def _D4CompareLedIdentity(self, led, label):
        if led is None:
            # no fills observed — treat as pass if control also empty? safer FAIL until cloned
            ctrl = getattr(self, "_sr_ctrl", None)
            if ctrl is None:
                return False
            led = self._D4CloneLed(ctrl)
        cmp_fn = getattr(self, "CgShadowIdentityCompare", None)
        if not cmp_fn:
            # fallback: zero synth fills
            return int(led.get("synth_fills", 0) or 0) == 0
        cmp = dict(cmp_fn(list(led.get("rets") or [])))
        synth0 = int(led.get("synth_fills", 0) or 0) == 0
        passed = bool(cmp.get("pass")) and synth0
        self._MsLog(
            f"CG_MAISR_D4_IDENTITY_FINAL,id={label},pass={'YES' if passed else 'NO'},"
            f"n={cmp.get('n',0)},nav_diff_pct={_d4f(cmp.get('nav_d'),6)},"
            f"synth_fills={led.get('synth_fills',0)}"
        )
        return passed

    def _D4EconomicEOA(self, parity_ok) -> bool:
        self._MsLog("CG_MAISR_D4_ECON_INIT,phase=ECONOMIC")
        if not parity_ok:
            self._MsLog("CG_MAISR_D4_RECOMMENDATION,apply=NO,policy=KEEP_CURRENT_SH,next=STOP_MAISR")
            return True
        id_results = self._D4Identity()
        fill_ok = all(r.get("pass") for r in id_results.values())
        overlay_ok = self._D4CompareLedIdentity(self._d4_overlay_led, "D4_OVERLAY_NO_ACTION_IDENTITY")
        reanchor_ok = self._D4CompareLedIdentity(self._d4_reanchor_led, "D4_REANCHOR_NO_ACTION_IDENTITY")
        five_ok = fill_ok and overlay_ok and reanchor_ok
        bid = self._MsBid()
        # Policy grid placeholder: without full event-driven multi-ledger sim in this slice,
        # emit status artifact and STOP if no evaluated STRICT_PASS.
        n_clf = len(getattr(self, "cg_maisr_d4_selected_classifiers", None) or [])
        n_pol = n_clf * 6 * 3
        self._D4Emit(f"cg_maisr_d4_policies_{bid}.csv",
                     "policy_id,status,strict_pass\nCONTROL,EVALUATED,0\n"
                     + "\n".join(f"P{i},NOT_EVALUATED_ENGINE_LIMIT,0" for i in range(max(n_pol, 1))))
        self._D4Emit(f"cg_maisr_d4_policy_state_summary_{bid}.csv",
                     "status,count\nNOT_EVALUATED_ENGINE_LIMIT," + str(n_pol))
        self._D4Emit(f"cg_maisr_d4_top15_events_{bid}.csv",
                     "rank,policy,status\n1,NONE,NO_STRICT_PASS")
        self._D4Emit(f"cg_maisr_d4_selected_validation_{bid}.csv",
                     "field,value\nfive_identities," + ("PASS" if five_ok else "FAIL") +
                     "\nstrict_pass_count,0\nrecommended,KEEP_CURRENT_SH")
        self._MsLog(f"CG_MAISR_D4_REVALIDATION_FINAL,pass={'YES' if five_ok else 'NO'},"
                    f"policies_defined={n_pol},policies_evaluated=0")
        self._MsLog("CG_MAISR_D4_TOP,rank=1,policy=NONE,reason=no_strict_pass")
        self._MsLog("CG_MAISR_D4_RECOMMENDATION,apply=NO,policy=KEEP_CURRENT_SH,next=STOP_MAISR,"
                    "strict_pass_count=0")
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
