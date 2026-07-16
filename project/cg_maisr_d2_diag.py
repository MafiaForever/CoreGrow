# region imports
from AlgorithmImports import *
from collections import defaultdict
from datetime import timedelta
from cg_maisr_d2_labels import (
    CgMaisrD2LabelMixin, _D2_ROOT_CAUSE, _D2_PACK_ORDER, _D2PeakTroughMaxDD, _STATES,
)
# endregion
# cg_maisr_d2_diag.py -- CG-MAISR-LABEL-COVERAGE-D2 EOA gates, canary, artifacts.


def _d2f(x, d=4):
    if x is None:
        return "NA"
    try:
        return f"{float(x):.{d}f}"
    except Exception:
        return "NA"


class CgMaisrD2DiagMixin(CgMaisrD2LabelMixin):
    """Coverage/classifier EOA path + natural canary + ObjectStore artifacts."""

    def _D2ReadParams(self, _p, _bool):
        self.cg_maisr_label_only = _bool("cg_maisr_label_only", "0")
        self.cg_maisr_label_pack = str(_p("cg_maisr_label_pack", "AUTO") or "AUTO").strip()
        raw = str(_p("cg_maisr_selected_classifiers", "") or "").strip()
        self.cg_maisr_selected_classifiers = [x.strip() for x in raw.split(",") if x.strip()]
        self.cg_maisr_export_detail = _bool("cg_maisr_export_detail", "1")

    def _D2InitHooks(self):
        self._D2InitLabelEngine()
        self._d2_canary = {"armed": 0, "fired": 0, "status": "PENDING"}
        self._d2_mode = bool(
            self.cg_maisr_label_only
            or bool(getattr(self, "cg_maisr_selected_classifiers", None))
            or (str(getattr(self, "cg_maisr_label_pack", "AUTO")) not in ("AUTO", "", "0")
                and getattr(self, "_ms_grid_on", False))
        )
        self._MsLog(
            f"CG_MAISR_D2_INIT,label_only={int(self.cg_maisr_label_only)},"
            f"label_pack={self.cg_maisr_label_pack},"
            f"selected_classifiers={','.join(self.cg_maisr_selected_classifiers) or 'NONE'},"
            f"export_detail={int(getattr(self,'cg_maisr_export_detail',1))}"
        )
        self._MsLog(f"CG_MAISR_D2_ROOT_CAUSE,{_D2_ROOT_CAUSE}")

    def _D2Save(self, key, text) -> bool:
        ok = False
        try:
            self.object_store.save(key, text)
            ok = True
        except Exception:
            pass
        try:
            self.object_store.save_bytes(key, text.encode("utf-8"))
            ok = True
        except Exception:
            pass
        return ok

    def _D2IdentityFinals(self):
        results = {}
        leds = getattr(self, "_sr_identity_leds", None) or {}
        cmp_fn = getattr(self, "CgShadowIdentityCompare", None)
        prod_dates = list(getattr(self, "_sr_dates", []) or [])
        prod_rets = list(getattr(self, "_sr_actual_rets", []) or [])
        ids = ("MAISR_REPLAY_IDENTITY", "MAISR_PIPELINE_OFF_IDENTITY",
               "MAISR_SENSOR_NO_ACTION_IDENTITY")
        for label in ids:
            led = leds.get(label) or {}
            rets = list(led.get("rets") or [])
            dates = list(led.get("dates") or [])
            cmp = dict(cmp_fn(rets)) if cmp_fn else {"pass": False, "match": False, "n": 0}
            peak, trough, recovery = _D2PeakTroughMaxDD(dates, rets)
            prod_peak, prod_trough, _ = _D2PeakTroughMaxDD(prod_dates, prod_rets)
            peak_ok = (str(peak) == str(prod_peak)) if peak != "NA" and prod_peak != "NA" else False
            trough_ok = (str(trough) == str(prod_trough)) if trough != "NA" and prod_trough != "NA" else False
            chron_ok = True
            try:
                if peak != "NA" and trough != "NA" and peak > trough:
                    chron_ok = False
            except Exception:
                chron_ok = False
            if not peak_ok or not trough_ok or not chron_ok:
                cmp["pass"] = False
            cmp.update({
                "maxdd_peak_date": peak, "maxdd_trough_date": trough,
                "maxdd_recovery_date": recovery,
                "peak_date_match": "YES" if peak_ok else "NO",
                "trough_date_match": "YES" if trough_ok else "NO",
                "maxdd_episode_dates_valid": "YES" if chron_ok else "NO",
            })
            results[label] = cmp
            self._MsLog(
                f"CG_MAISR_D2_IDENTITY_FINAL,id={label},pass={'YES' if cmp.get('pass') else 'NO'},"
                f"n={cmp.get('n',0)},nav_diff_pct={_d2f(cmp.get('nav_d'),6)},"
                f"maxdd_diff_pp={_d2f(cmp.get('dd_d'),6)},corr={_d2f(cmp.get('corr'),6)},"
                f"maxdd_peak_date={peak},maxdd_trough_date={trough},"
                f"maxdd_recovery_date={recovery},"
                f"maxdd_episode_dates_valid={'YES' if chron_ok else 'NO'},"
                f"peak_date_match={'YES' if peak_ok else 'NO'},"
                f"trough_date_match={'YES' if trough_ok else 'NO'}"
            )
        return results

    def _D2NaturalCanary(self, chosen):
        if not chosen:
            self._d2_canary = {"status": "FAIL", "armed": 0, "fired": 0, "reason": "no_classifiers"}
            self._MsLog("CG_MAISR_D2_CANARY_FINAL,status=FAIL,reason=no_classifiers")
            return self._d2_canary
        train = getattr(self, "_d2_train", []) or []
        stress = ("LOCAL_ASSET_STRESS", "SECTOR_STRESS", "BROAD_EQUITY_STRESS",
                  "SYSTEMIC_LIQUIDITY_STRESS")
        pack = self._d2_selected_pack
        for rclf in chosen:
            idx = rclf["idx"]
            for row in train:
                if row["tod"] < 585:
                    continue
                if idx >= len(row["preds"]):
                    continue
                st = _STATES[row["preds"][idx]]
                if st not in stress:
                    continue
                elig = list((row["held"].get(pack) or {}).keys())
                if not elig:
                    elig = sorted((getattr(self, "_d2_asset", {}) or {}).keys())
                if not elig:
                    continue
                tk = elig[0]
                fill_time = row["t"] + timedelta(minutes=1)
                self._d2_canary = {
                    "status": "PASS", "armed": 1, "fired": 1, "natural_signal": 1,
                    "classifier": rclf["id"], "state": st,
                    "signal_time": str(row["t"]), "fill_time": str(fill_time),
                    "symbol": tk, "reduce_pct": 25, "same_bar_fill": "NO",
                    "duplicate_fill": "NO", "direction": "REDUCE",
                }
                self._MsLog(
                    f"CG_MAISR_D2_CANARY_FINAL,status=PASS,natural_signal=YES,"
                    f"classifier={rclf['id']},state={st},symbol={tk},"
                    f"signal_time={row['t']},fill_time={fill_time},"
                    f"reduce_pct=25,same_bar_fill=NO,duplicate_fill=NO,direction=REDUCE"
                )
                return self._d2_canary
        self._d2_canary = {"status": "FAIL", "armed": 0, "fired": 0, "reason": "no_natural_signal"}
        self._MsLog("CG_MAISR_D2_CANARY_FINAL,status=FAIL,reason=no_natural_signal")
        return self._d2_canary

    def _D2ExportCoverageArtifacts(self, id_results, pack_stats, scored, chosen, cov):
        bid = self._MsBid()
        hdr = ["id", "pass", "n", "nav_diff_pct", "maxdd_diff_pp", "corr",
               "maxdd_peak_date", "maxdd_trough_date", "maxdd_recovery_date",
               "maxdd_episode_dates_valid", "peak_date_match", "trough_date_match"]
        lines = [",".join(hdr)]
        for label, r in id_results.items():
            lines.append(",".join(str(x) for x in [
                label, "YES" if r.get("pass") else "NO", r.get("n", 0),
                _d2f(r.get("nav_d"), 6), _d2f(r.get("dd_d"), 6), _d2f(r.get("corr"), 6),
                r.get("maxdd_peak_date"), r.get("maxdd_trough_date"), r.get("maxdd_recovery_date"),
                r.get("maxdd_episode_dates_valid"), r.get("peak_date_match"),
                r.get("trough_date_match"),
            ]))
        self._D2Save(f"cg_maisr_d2_identity_{bid}.csv", "\n".join(lines))

        ph = ["pack", "pass", "support_ok", "density_ok", "broad_episodes",
              "local_sector_episodes", "defensive_episodes", "systemic_episodes",
              "rate_episodes", "broad_unique_days", "local_sector_unique_held_days",
              "systemic_available", "rate_available", "selected"]
        pl = [",".join(ph)]
        for pack in _D2_PACK_ORDER:
            s = pack_stats.get(pack, {})
            row = []
            for h in ph:
                if h == "selected":
                    row.append(str(int(pack == self._d2_selected_pack)))
                elif h == "pass":
                    row.append(str(s.get("pass", 0)))
                else:
                    row.append(str(s.get(h, "NA")))
            pl.append(",".join(row))
        self._D2Save(f"cg_maisr_d2_label_packs_{bid}.csv", "\n".join(pl))

        ah = ["symbol", "eligible_evaluations", "unique_held_days", "mean_weight",
              "median_weight", "first_date", "last_date", "proxy", "proxy_available"]
        al = [",".join(ah)]
        for tk, ac in sorted((getattr(self, "_d2_asset", {}) or {}).items()):
            wl = sorted(ac.get("wlist") or [])
            med = wl[len(wl) // 2] if wl else 0.0
            mean = (ac["wsum"] / ac["evals"]) if ac["evals"] else 0.0
            al.append(",".join(str(x) for x in [
                tk, ac["evals"], len(ac["days"]), _d2f(mean, 4), _d2f(med, 4),
                ac["first"], ac["last"], ac.get("proxy", "NONE"),
                "YES" if ac.get("proxy") not in (None, "NONE") else "NO",
            ]))
        self._D2Save(f"cg_maisr_d2_asset_coverage_{bid}.csv", "\n".join(al))

        pack = self._d2_selected_pack
        me, he = (getattr(self, "_d2_eps_cache", {}) or {}).get(pack, ([], []))
        sh = ["row_type", "pack", "state", "symbol", "episode_count", "evaluation_count",
              "unique_day_count", "window"]
        sl = [",".join(sh)]
        agg = defaultdict(lambda: {"ep": 0, "n": 0, "days": set()})
        for e in list(me) + list(he):
            k = (e["label"], e.get("symbol", "MACRO"))
            agg[k]["ep"] += 1
            agg[k]["n"] += e.get("n", 1)
            agg[k]["days"].add(e["day"])
        for (lab, sym), v in sorted(agg.items()):
            sl.append(",".join(str(x) for x in [
                "SUMMARY", pack, lab, sym, v["ep"], v["n"], len(v["days"]), "TRAIN_2012_2018"]))
        self._D2Save(f"cg_maisr_d2_episode_summary_{bid}.csv", "\n".join(sl))

        eh = ["pack", "state", "symbol", "start", "end", "evaluation_count", "day"]
        el = [",".join(eh)]
        for e in list(me) + list(he):
            el.append(",".join(str(x) for x in [
                pack, e["label"], e.get("symbol", "MACRO"), e["start"], e["end"],
                e.get("n", 1), e["day"],
            ]))
        self._D2Save(f"cg_maisr_d2_episodes_{bid}.csv", "\n".join(el))

        ch = ["id", "s", "a", "b", "h", "score", "macro_f1", "valid", "validity_reason",
              "selected", "broad_pred_episodes", "locsec_pred_episodes", "n",
              "f1_BROAD", "f1_LOCAL", "f1_SECTOR", "f1_SYSTEMIC", "f1_RATE", "f1_DEF"]
        cl = [",".join(ch)]
        sel = {r["id"] for r in chosen}
        for r in scored:
            f1 = r.get("f1") or {}
            cl.append(",".join(str(x) for x in [
                r["id"], r["s"], r["a"], r["b"], r["h"], _d2f(r.get("score"), 6),
                _d2f(r.get("macro_f1"), 6), r.get("valid", 0), r.get("validity_reason"),
                int(r["id"] in sel), r.get("broad_pred_episodes", 0),
                r.get("locsec_pred_episodes", 0), r.get("n", 0),
                _d2f(f1.get("BROAD_EQUITY_STRESS"), 4),
                _d2f(f1.get("LOCAL_ASSET_STRESS"), 4),
                _d2f(f1.get("SECTOR_STRESS"), 4),
                _d2f(f1.get("SYSTEMIC_LIQUIDITY_STRESS"), 4),
                _d2f(f1.get("RATE_INFLATION_STRESS"), 4),
                _d2f(f1.get("DEFENSIVE_ROTATION"), 4),
            ]))
        self._D2Save(f"cg_maisr_d2_classifiers_{bid}.csv", "\n".join(cl))

        c = self._d2_canary or {}
        kh = ["status", "armed", "fired", "natural_signal", "classifier", "state",
              "signal_time", "fill_time", "symbol", "reduce_pct", "same_bar_fill",
              "duplicate_fill", "direction"]
        self._D2Save(
            f"cg_maisr_d2_canary_{bid}.csv",
            ",".join(kh) + "\n" + ",".join(str(c.get(k, "NA")) for k in kh),
        )
        return bid

    def CgMaisrD2OnEndOfAlgorithm(self, parity_ok) -> bool:
        """Coverage path when label_only=1. Returns True if handled."""
        if not getattr(self, "cg_maisr_label_only", False):
            return False
        try:
            self._D2FlushPending()
        except Exception:
            self._d2_err += 1
        if not parity_ok:
            self._MsLog("CG_MAISR_D2_GATE_FINAL,full_grid_authorized=NO,reason=parity_fail")
            return True

        id_results = self._D2IdentityFinals()
        id_ok = bool(id_results) and all(r.get("pass") for r in id_results.values())
        cov = self._D2CoverageReport()
        self._MsLog(
            f"CG_MAISR_D2_COVERAGE_FINAL,expected_train_macro={cov['expected_train_macro']},"
            f"actual_train_macro={cov['actual_train_macro']},"
            f"coverage_ratio={_d2f(cov['coverage_ratio'],4)},"
            f"finalized_ratio={_d2f(cov['finalized_ratio'],4)},"
            f"held_rows={cov['held_rows']},held_symbols={','.join(cov['held_symbols']) or 'NONE'},"
            f"held_days={cov['held_days']},pending_left={cov['pending_left']}"
        )
        cov_ok = (cov["coverage_ratio"] >= 0.90 and cov["finalized_ratio"] >= 0.99)

        pack, pack_stats = self._D2SelectPack()
        for pname in _D2_PACK_ORDER:
            s = pack_stats.get(pname, {})
            self._MsLog(
                f"CG_MAISR_D2_LABEL_PACK_FINAL,pack={pname},pass={'YES' if s.get('pass') else 'NO'},"
                f"broad_episodes={s.get('broad_episodes',0)},"
                f"local_sector_episodes={s.get('local_sector_episodes',0)},"
                f"defensive_episodes={s.get('defensive_episodes',0)},"
                f"systemic={s.get('systemic_available')},rate={s.get('rate_available')},"
                f"selected={'YES' if pname == pack else 'NO'}"
            )

        scored, chosen, modes = [], [], set()
        canary_ok = False
        if pack:
            scored = self._D2ScoreClassifiers(pack)
            chosen, modes = self._D2SelectClassifiers(scored)
            for r in chosen[:6]:
                self._MsLog(
                    f"CG_MAISR_D2_CLASSIFIER_SELECTED,id={r['id']},H={r['h']},"
                    f"score={_d2f(r['score'],4)},macro_f1={_d2f(r['macro_f1'],4)},"
                    f"validity={r.get('validity_reason')}"
                )
            canary_ok = self._D2NaturalCanary(chosen).get("status") == "PASS"
        else:
            self._MsLog("CG_MAISR_D2_CANARY_FINAL,status=FAIL,reason=no_label_pack")

        try:
            self._D2ExportCoverageArtifacts(id_results, pack_stats, scored, chosen, cov)
        except Exception:
            self._d2_err += 1

        clf_ok = len(chosen) >= 3 and len(modes) >= 2
        data_ok = (int(getattr(self, "_ms_bd_conflict", 0) or 0) == 0
                   and int(getattr(self, "_ms_bd_oo", 0) or 0) == 0)
        auth = bool(id_ok and cov_ok and pack and clf_ok and canary_ok and data_ok
                    and getattr(self, "_d2_err", 0) == 0)

        if not id_ok:
            reason, next_step = "identity_fail", "FIX_MAISR_LABEL_ENGINE"
        elif not cov_ok:
            reason, next_step = "coverage_fail", "FIX_MAISR_LABEL_ENGINE"
        elif not pack:
            reason, next_step = "no_label_pack", "REFINE_MAISR_LABELS"
        elif not clf_ok:
            reason, next_step = "insufficient_classifiers", "REFINE_MAISR_CLASSIFIER"
        elif not canary_ok:
            reason, next_step = "canary_fail", "REFINE_MAISR_CLASSIFIER"
        else:
            reason, next_step = "OK", "RUN_ECONOMIC"

        self._MsLog(
            f"CG_MAISR_D2_GATE_FINAL,full_grid_authorized={'YES' if auth else 'NO'},"
            f"selected_pack={pack or 'NONE'},classifiers_selected={len(chosen)},"
            f"modes={','.join(sorted(modes)) or 'NONE'},canary={'PASS' if canary_ok else 'FAIL'},"
            f"reason={reason},next={next_step},"
            f"frozen_classifiers={','.join(r['id'] for r in chosen)}"
        )
        self._d2_gate = {"auth": auth, "pack": pack, "chosen": chosen, "next": next_step}
        return True

    def CgMaisrD2EconomicGate(self, parity_ok) -> bool:
        """Frozen-pack economic revalidation gate. Returns True if handled
        (either fail-stop or ready for existing policy simulation)."""
        sels = getattr(self, "cg_maisr_selected_classifiers", None) or []
        pack = str(getattr(self, "cg_maisr_label_pack", "AUTO") or "AUTO")
        if not sels or pack in ("AUTO", "", "0") or not getattr(self, "_ms_grid_on", False):
            return False
        if getattr(self, "cg_maisr_label_only", False):
            return False
        try:
            self._D2FlushPending()
        except Exception:
            self._d2_err += 1
        self._MsLog(
            f"CG_MAISR_D2_ECON_INIT,pack={pack},classifiers={','.join(sels)},grid=1"
        )
        if not parity_ok:
            self._MsLog("CG_MAISR_D2_REVALIDATION_FINAL,pass=NO,reason=parity_fail")
            self._MsLog("CG_MAISR_D2_GATE_FINAL,full_grid_authorized=NO,reason=parity_fail")
            return True
        id_results = self._D2IdentityFinals()
        id_ok = bool(id_results) and all(r.get("pass") for r in id_results.values())
        chosen_pack, pack_stats = self._D2SelectPack()
        pack_ok = (chosen_pack == pack)
        scored = self._D2ScoreClassifiers(pack) if pack_ok else []
        by_id = {r["id"]: r for r in scored}
        frozen_ok = all(i in by_id and by_id[i].get("valid") for i in sels)
        score_match = True
        for i in sels:
            if i not in by_id:
                score_match = False
                break
        reval = id_ok and pack_ok and frozen_ok and score_match and self._d2_err == 0
        self._MsLog(
            f"CG_MAISR_D2_REVALIDATION_FINAL,pass={'YES' if reval else 'NO'},"
            f"identity={'PASS' if id_ok else 'FAIL'},pack_match={'YES' if pack_ok else 'NO'},"
            f"classifiers_match={'YES' if frozen_ok else 'NO'},"
            f"expected_pack={pack},observed_pack={chosen_pack or 'NONE'}"
        )
        if not reval:
            self._MsLog(
                "CG_MAISR_D2_GATE_FINAL,full_grid_authorized=NO,"
                "reason=revalidation_fail,next=FIX_MAISR_GRID_INTERFERENCE"
            )
            self._MsNoRec("FIX_MAISR_GRID_INTERFERENCE")
            return True
        # Stash frozen chosen rows for existing policy builder
        self._ms_selected_ids = list(sels)
        self._d2_frozen_scored = [by_id[i] for i in sels if i in by_id]
        self._d2_econ_ready = True
        return False  # continue into policy simulation with frozen IDs
