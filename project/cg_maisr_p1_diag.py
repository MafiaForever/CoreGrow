# region imports
from AlgorithmImports import *
# endregion
# cg_maisr_p1_diag.py -- CG-MAISR-CANDIDATE-IDENTITY-P1 helpers.
# Extracted from cg_maisr_diag.py to keep both files under the QC 64000-char
# compile limit. Mixed into CgMaisrDiagMixin (see cg_maisr_diag.py). Relies
# on state populated by CgMaisrInit / CgMaisrOnData / _MsEval and on the
# fill-tracking identity ledgers registered by
# CgShadowReplayMixin.CgShadowRegisterIdentityLedgers.

import base64
import zlib

_P1_IDENTITY_IDS = (
    "MAISR_REPLAY_IDENTITY",
    "MAISR_PIPELINE_OFF_IDENTITY",
    "MAISR_SENSOR_NO_ACTION_IDENTITY",
)
_P1_CANARY_ID = "MAISR_CANARY"
_P1_CANARY_CFG = ("S2", 2, 0.50, "H2")
_P1_CANARY_STATES = ("LOCAL_ASSET_STRESS", "SECTOR_STRESS", "BROAD_EQUITY_STRESS")
_P1_PRIMARY = (
    "LOCAL_ASSET_STRESS", "SECTOR_STRESS", "BROAD_EQUITY_STRESS",
    "SYSTEMIC_LIQUIDITY_STRESS", "RATE_INFLATION_STRESS", "DEFENSIVE_ROTATION",
)


def _p1_clfid(s, a, b, h):
    return f"{s}_C{a}_B{int(round(b * 100)):02d}_{h}"


def _p1_f(x, d=4):
    if x is None:
        return "NA"
    try:
        return f"{float(x):.{d}f}"
    except Exception:
        return "NA"


def _p1_tk(sym):
    try:
        return str(sym.Value)
    except Exception:
        try:
            return str(sym.value)
        except Exception:
            return str(sym)


class CgMaisrP1Mixin:
    """Identity/canary finals + classifier gates + artifact export for P1."""

    def _MsWriteCsv(self, rows):
        key = f"cg_maisr_d0_policies_{self._MsBid()}.csv"
        try:
            headers = ["id", "clf_id", "h", "router", "persist", "timing", "CAGR", "MaxDD",
                       "annual_stddev", "Sharpe", "worst_5pct_day_mean", "recovery_days_max",
                       "oos_sharpe", "crisis_maxdd", "y2020_maxdd", "y2022_maxdd",
                       "risk_efficiency", "turnover", "false_broad", "missed_sys",
                       "CAGR_cost2", "MaxDD_cost2", "neighbor_stable", "STRICT_PASS", "invalid"]
            lines = [",".join(headers)]
            for r in rows:
                lines.append(",".join(str(r.get(h, "NA")) for h in headers))
            self.object_store.save(key, "\n".join(lines))
            return key
        except Exception as exc:
            return f"NONE:{type(exc).__name__}"

    def _MsWriteAttributionCsv(self, chosen, rows):
        key = f"cg_maisr_d0_attribution_{self._MsBid()}.csv"
        try:
            headers = ["clf_id", "h", "false_broad_total", "missed_sys_total",
                       "policies_n", "strict_pass_n"]
            lines = [",".join(headers)]
            for r in chosen:
                sub = [x for x in rows if x.get("clf_id") == r["id"]]
                fb = sum(int(x.get("false_broad", 0) or 0) for x in sub)
                ms = sum(int(x.get("missed_sys", 0) or 0) for x in sub)
                sp = sum(1 for x in sub if x.get("STRICT_PASS"))
                lines.append(",".join(str(v) for v in (r["id"], r["h"], fb, ms, len(sub), sp)))
            self.object_store.save(key, "\n".join(lines))
            return key
        except Exception as exc:
            return f"NONE:{type(exc).__name__}"

    def _MsWriteClassifiersCsv(self, scored):
        key = f"cg_maisr_d0_classifiers_{self._MsBid()}.csv"
        try:
            headers = ["id", "s", "a", "b", "h", "score", "macro_f1", "sys_fn",
                       "broad_fp", "loc_to_broad", "sys_to_loc", "n"]
            lines = [",".join(headers)]
            for r in scored:
                lines.append(",".join(str(r.get(h, "NA")) for h in headers))
            self.object_store.save(key, "\n".join(lines))
            return key
        except Exception as exc:
            return f"NONE:{type(exc).__name__}"

    def _MsCanaryTryFire(self, bars) -> None:
        """Single-shot: fixed classifier S2_C2_B50_H2 armed a LOCAL/SECTOR/BROAD
        signal; fire a 25% reduce of the first eligible held risk symbol at the
        next bar's Open whose EndTime is strictly after the signal, then disable."""
        if not getattr(self, "_ms_canary_armed", False) or getattr(self, "_ms_canary_fired", False):
            return
        led = (getattr(self, "_sr_identity_leds", None) or {}).get(_P1_CANARY_ID)
        if led is None:
            return
        sig_t = self._ms_canary_signal_time
        eligible = sorted(t for t in self._ms_current_risk
                           if float((led.get("qty") or {}).get(t, 0.0) or 0.0) > 0)
        if not eligible:
            return
        for kvp in bars:
            try:
                sym = kvp.Key if hasattr(kvp, "Key") else kvp
                bar = kvp.Value if hasattr(kvp, "Value") else bars[kvp]
                tk = _p1_tk(sym)
                if tk not in eligible:
                    continue
                et = getattr(bar, "end_time", None) or getattr(bar, "EndTime", None)
                if et is None or sig_t is None or et <= sig_t:
                    continue
                open_px = float(bar.open)
                if open_px <= 0:
                    continue
                pxmap = {tk: open_px}
                try:
                    if hasattr(self, "_SrPx"):
                        pxmap = self._SrPx()
                        pxmap[tk] = open_px
                except Exception:
                    pxmap = {tk: open_px}
                self._MsReduceOnly(led, {tk: 0.75}, pxmap)
                self._ms_canary_fired = True
                self._ms_canary_armed = False
                self._ms_canary_fill_tk = tk
                self._ms_canary_fill_px = open_px
                self._ms_canary_fill_time = self.time
                self._ms_canary_same_bar = 0
                break
            except Exception:
                continue

    def _MsPeakTrough(self, dates, rets):
        if not dates or not rets or len(dates) != len(rets):
            return "NA", "NA"
        nav = peak = 1.0
        peak_d = dates[0]
        trough_d = "NA"
        maxdd = 0.0
        for d, r in zip(dates, rets):
            nav = max(1e-8, nav * (1.0 + r))
            if nav > peak:
                peak = nav
                peak_d = d
            dd = 1.0 - nav / max(peak, 1e-9)
            if dd > maxdd:
                maxdd = dd
                trough_d = d
        return peak_d, trough_d

    def _MsSaveCsv(self, key, text) -> bool:
        """ObjectStore save + compressed log chunks (ObjectStore download is
        blocked on non-Institutional accounts; chunks enable local recovery)."""
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
        try:
            self._MsEmitArtifactChunks(key, text)
        except Exception:
            self._ms_err += 1
        return ok

    def _MsEmitArtifactChunks(self, key, text) -> None:
        raw = zlib.compress(text.encode("utf-8"), 9)
        b64 = base64.b64encode(raw).decode("ascii")
        chunk = 700
        n = (len(b64) + chunk - 1) // chunk
        used = int(getattr(self, "_ms_art_used", 0) or 0)
        # Shared console budget for artifact recovery (~22 KB).
        budget = 22000
        name = str(key).replace(",", "_")
        emit_n = 0
        meta = f"CG_MAISR_P1_ART_META,name={name},bytes={len(text)},zbytes={len(raw)},chunks={n}"
        if used + len(meta) + 1 > budget:
            self._MsLog(f"{meta},emitted=0,truncated=YES")
            return
        self._MsLog(f"{meta},emitted_pending=1")
        used += len(meta) + 1
        for i in range(n):
            part = b64[i * chunk:(i + 1) * chunk]
            line = f"CG_MAISR_P1_ART,name={name},i={i},n={n},b64={part}"
            if used + len(line) + 1 > budget:
                break
            self._MsLog(line)
            used += len(line) + 1
            emit_n += 1
        self._ms_art_used = used
        if emit_n < n:
            self._MsLog(f"CG_MAISR_P1_ART_META,name={name},emitted={emit_n},truncated=YES")
        else:
            self._MsLog(f"CG_MAISR_P1_ART_META,name={name},emitted={emit_n},truncated=NO")

    def _MsIdentityFinals(self):
        """Strict fill-replay identity comparisons. These ledgers only ever
        receive real production fills, so they prove the accounting itself
        is correct instead of a synthetic weight-reconstruction replay."""
        results = {}
        leds = getattr(self, "_sr_identity_leds", None) or {}
        cmp_fn = getattr(self, "CgShadowIdentityCompare", None)
        prod_dates = list(getattr(self, "_sr_dates", []) or [])
        prod_rets = list(getattr(self, "_sr_actual_rets", []) or [])
        prod_peak, prod_trough = self._MsPeakTrough(prod_dates, prod_rets)
        for label in _P1_IDENTITY_IDS:
            led = leds.get(label) or {}
            rets = list(led.get("rets") or [])
            dates = list(led.get("dates") or [])
            cmp = dict(cmp_fn(rets)) if cmp_fn is not None else {"pass": False, "match": False, "n": 0}
            peak_d, trough_d = self._MsPeakTrough(dates, rets)
            mismatch_events = int(led.get("mismatch_events", 0) or 0)
            mismatch_keys = int(led.get("mismatch_keys", 0) or 0)
            peak_match = (str(peak_d) == str(prod_peak)) if peak_d != "NA" and prod_peak != "NA" else False
            trough_match = (str(trough_d) == str(prod_trough)) if trough_d != "NA" and prod_trough != "NA" else False
            if not peak_match or not trough_match:
                cmp["pass"] = False
            cmp["peak_date"] = peak_d
            cmp["trough_date"] = trough_d
            cmp["peak_date_match"] = "YES" if peak_match else "NO"
            cmp["trough_date_match"] = "YES" if trough_match else "NO"
            cmp["mismatch_events"] = mismatch_events
            cmp["mismatch_keys"] = mismatch_keys
            if mismatch_keys > 0 or mismatch_events > 0:
                if label == "MAISR_PIPELINE_OFF_IDENTITY":
                    cmp["pass"] = False
            results[label] = cmp
            self._MsLog(
                f"CG_MAISR_P1_IDENTITY_FINAL,id={label},pass={'YES' if cmp.get('pass') else 'NO'},"
                f"n={cmp.get('n',0)},nav_diff_pct={_p1_f(cmp.get('nav_d'),6)},"
                f"maxdd_diff_pp={_p1_f(cmp.get('dd_d'),6)},corr={_p1_f(cmp.get('corr'),6)},"
                f"max_abs_daily_diff={_p1_f(cmp.get('max_d'),6)},"
                f"mean_abs_daily_diff={_p1_f(cmp.get('mean_d'),6)},"
                f"count_match={'YES' if cmp.get('match') else 'NO'},peak_date={peak_d},"
                f"trough_date={trough_d},peak_date_match={'YES' if peak_match else 'NO'},"
                f"trough_date_match={'YES' if trough_match else 'NO'},"
                f"mismatch_events={mismatch_events},mismatch_keys={mismatch_keys}"
            )
            if not cmp.get("pass") and cmp.get("first_div_idx") is not None and dates:
                idx = cmp["first_div_idx"]
                dv = dates[idx] if idx < len(dates) else "NA"
                self._MsLog(
                    f"CG_MAISR_P1_FIRST_DIVERGENCE,identity={label},time={dv},"
                    f"stage=daily_return,symbol=NA,production=NA,candidate=NA,"
                    f"difference={_p1_f(cmp.get('max_d'),6)},source_event=mark,"
                    f"is_warming_up=0"
                )
        return results

    def _MsCanaryFinal(self):
        fired = bool(getattr(self, "_ms_canary_fired", False))
        armed = bool(getattr(self, "_ms_canary_armed", False)) or fired
        tk = getattr(self, "_ms_canary_fill_tk", None)
        px = getattr(self, "_ms_canary_fill_px", None)
        ftime = getattr(self, "_ms_canary_fill_time", None)
        sig_state = getattr(self, "_ms_canary_signal_state", None)
        sig_time = getattr(self, "_ms_canary_signal_time", None)
        status = "PASS" if fired else ("NO_SIGNAL" if not armed else "ARMED_NO_FILL")
        self._MsLog(
            f"CG_MAISR_P1_CANARY_FINAL,classifier={_p1_clfid(*_P1_CANARY_CFG)},"
            f"status={status},armed={'YES' if armed else 'NO'},fired={'YES' if fired else 'NO'},"
            f"signal_state={sig_state or 'NA'},signal_time={sig_time or 'NA'},"
            f"fill_symbol={tk or 'NA'},fill_price={_p1_f(px,4)},fill_time={ftime or 'NA'},"
            f"reduce_pct=25,same_bar_fill=NO,duplicate_fill=NO,direction=REDUCE,"
            f"disabled_after_fire={'YES' if fired else 'NO'}"
        )
        return {"armed": armed, "fired": fired, "fill_symbol": tk, "fill_price": px, "status": status}

    def _MsExportP1Artifacts(self, id_results, canary_result) -> None:
        bid = self._MsBid()
        headers_id = ["id", "pass", "n", "nav_diff_pct", "maxdd_diff_pp", "corr",
                      "max_abs_daily_diff", "mean_abs_daily_diff", "count_match",
                      "peak_date", "trough_date", "peak_date_match", "trough_date_match",
                      "mismatch_events", "mismatch_keys", "first_div_idx"]
        lines = [",".join(headers_id)]
        for label in _P1_IDENTITY_IDS:
            r = id_results.get(label, {}) or {}
            vals = [
                label, "YES" if r.get("pass") else "NO", r.get("n", 0),
                _p1_f(r.get("nav_d"), 6), _p1_f(r.get("dd_d"), 6), _p1_f(r.get("corr"), 6),
                _p1_f(r.get("max_d"), 6), _p1_f(r.get("mean_d"), 6),
                "YES" if r.get("match") else "NO", r.get("peak_date", "NA"),
                r.get("trough_date", "NA"),
                r.get("peak_date_match", "NO"), r.get("trough_date_match", "NO"),
                r.get("mismatch_events", 0), r.get("mismatch_keys", 0),
                r.get("first_div_idx", "NA"),
            ]
            lines.append(",".join(str(v) for v in vals))
        self._MsSaveCsv(f"cg_maisr_p1_identity_{bid}.csv", "\n".join(lines))

        sub_rows = getattr(self, "_ms_sub_rows", []) or []
        headers_sub = [
            "ticker", "resolution", "data_type", "tick_type",
            "is_tradebar", "is_quotebar", "is_open_interest", "classification",
        ]
        lines2 = [",".join(headers_sub)]
        for row in sub_rows:
            lines2.append(",".join(str(row.get(h, "NA")) for h in headers_sub))
        self._MsSaveCsv(f"cg_maisr_p1_subscriptions_{bid}.csv", "\n".join(lines2))

        headers_c = ["classifier", "status", "armed", "fired", "signal_state", "signal_time",
                     "fill_symbol", "fill_price", "fill_time", "reduce_pct",
                     "same_bar_fill", "duplicate_fill", "direction"]
        vals_c = [
            _p1_clfid(*_P1_CANARY_CFG), canary_result.get("status", "NA"),
            "YES" if canary_result.get("armed") else "NO",
            "YES" if canary_result.get("fired") else "NO",
            getattr(self, "_ms_canary_signal_state", None) or "NA",
            getattr(self, "_ms_canary_signal_time", None) or "NA",
            canary_result.get("fill_symbol") or "NA", _p1_f(canary_result.get("fill_price"), 4),
            getattr(self, "_ms_canary_fill_time", None) or "NA", 25, "NO", "NO", "REDUCE",
        ]
        lines3 = [",".join(headers_c), ",".join(str(v) for v in vals_c)]
        self._MsSaveCsv(f"cg_maisr_p1_canary_{bid}.csv", "\n".join(lines3))

    def _MsClfCoreValid(self, r) -> bool:
        if not r.get("core_ok"):
            return False
        if float(r.get("macro_f1") or 0.0) <= 0.0:
            return False
        if int(r.get("primary_f1_gt0") or 0) < 2:
            return False
        return True

    def _MsEnrichScored(self, scored):
        """Add support / validity fields required by P1 classifier gates."""
        out = []
        for r in scored:
            tp = r.get("tp") or {}
            fn = r.get("fn") or {}
            fp = r.get("fp") or {}
            f1 = r.get("f1") or {}
            true_broad = int(tp.get("BROAD_EQUITY_STRESS", 0) or 0) + int(fn.get("BROAD_EQUITY_STRESS", 0) or 0)
            true_local = int(tp.get("LOCAL_ASSET_STRESS", 0) or 0) + int(fn.get("LOCAL_ASSET_STRESS", 0) or 0)
            true_sector = int(tp.get("SECTOR_STRESS", 0) or 0) + int(fn.get("SECTOR_STRESS", 0) or 0)
            true_sys = int(tp.get("SYSTEMIC_LIQUIDITY_STRESS", 0) or 0) + int(fn.get("SYSTEMIC_LIQUIDITY_STRESS", 0) or 0)
            true_rate = int(tp.get("RATE_INFLATION_STRESS", 0) or 0) + int(fn.get("RATE_INFLATION_STRESS", 0) or 0)
            true_def = int(tp.get("DEFENSIVE_ROTATION", 0) or 0) + int(fn.get("DEFENSIVE_ROTATION", 0) or 0)
            primary_gt0 = sum(1 for k in _P1_PRIMARY if float(f1.get(k) or 0.0) > 0.0)
            core_ok = (true_broad >= 20 and (true_local + true_sector) >= 20)
            reason = "OK"
            if true_broad < 20 or (true_local + true_sector) < 20:
                reason = "INSUFFICIENT_CORE_SUPPORT"
            elif float(r.get("macro_f1") or 0.0) <= 0.0:
                reason = "ZERO_MACRO_F1"
            elif primary_gt0 < 2:
                reason = "FEWER_THAN_TWO_PRIMARY_F1"
            nr = dict(r)
            nr.update({
                "true_broad": true_broad, "true_local": true_local, "true_sector": true_sector,
                "true_sys": true_sys, "true_rate": true_rate, "true_def": true_def,
                "primary_f1_gt0": primary_gt0, "core_ok": int(core_ok),
                "valid": int(core_ok and float(r.get("macro_f1") or 0) > 0 and primary_gt0 >= 2),
                "validity_reason": reason,
                "loc_to_broad_n": int(r.get("loc_to_broad_n", 0) or 0),
                "sys_to_loc_n": int(r.get("sys_to_loc_n", 0) or 0),
            })
            out.append(nr)
        return out

    def _MsSelectClassifiersValid(self, scored):
        """Up to 2 valid configs per SH mode; never fill with F1=0 rows."""
        by_h = {"H0": [], "H1": [], "H2": []}
        for r in scored:
            if self._MsClfCoreValid(r):
                by_h[r["h"]].append(r)
        chosen, seen = [], set()
        for h in ("H0", "H1", "H2"):
            for r in sorted(by_h[h], key=lambda x: (-float(x.get("score") or -9), -float(x.get("macro_f1") or 0)))[:2]:
                if r["id"] not in seen:
                    chosen.append(r)
                    seen.add(r["id"])
        modes = {r["h"] for r in chosen}
        return chosen[:6], modes

    def _MsWriteP1ClassifiersCsv(self, scored):
        bid = self._MsBid()
        key = f"cg_maisr_p1_classifiers_{bid}.csv"
        headers = [
            "id", "s", "a", "b", "h", "score", "macro_f1", "n",
            "true_broad", "true_local", "true_sector", "true_sys", "true_rate", "true_def",
            "f1_LOCAL_ASSET_STRESS", "f1_SECTOR_STRESS", "f1_BROAD_EQUITY_STRESS",
            "f1_SYSTEMIC_LIQUIDITY_STRESS", "f1_RATE_INFLATION_STRESS", "f1_DEFENSIVE_ROTATION",
            "tp_LOCAL", "fp_LOCAL", "fn_LOCAL", "tp_BROAD", "fp_BROAD", "fn_BROAD",
            "loc_to_broad_n", "sys_to_loc_n", "primary_f1_gt0", "core_ok", "valid",
            "validity_reason", "selected",
        ]
        selected_ids = set(getattr(self, "_ms_selected_ids", []) or [])
        lines = [",".join(headers)]
        for r in scored:
            f1 = r.get("f1") or {}
            tp = r.get("tp") or {}
            fp = r.get("fp") or {}
            fn = r.get("fn") or {}
            vals = [
                r.get("id"), r.get("s"), r.get("a"), r.get("b"), r.get("h"),
                _p1_f(r.get("score"), 6), _p1_f(r.get("macro_f1"), 6), r.get("n"),
                r.get("true_broad"), r.get("true_local"), r.get("true_sector"),
                r.get("true_sys"), r.get("true_rate"), r.get("true_def"),
                _p1_f(f1.get("LOCAL_ASSET_STRESS"), 4), _p1_f(f1.get("SECTOR_STRESS"), 4),
                _p1_f(f1.get("BROAD_EQUITY_STRESS"), 4),
                _p1_f(f1.get("SYSTEMIC_LIQUIDITY_STRESS"), 4),
                _p1_f(f1.get("RATE_INFLATION_STRESS"), 4),
                _p1_f(f1.get("DEFENSIVE_ROTATION"), 4),
                tp.get("LOCAL_ASSET_STRESS", 0), fp.get("LOCAL_ASSET_STRESS", 0),
                fn.get("LOCAL_ASSET_STRESS", 0),
                tp.get("BROAD_EQUITY_STRESS", 0), fp.get("BROAD_EQUITY_STRESS", 0),
                fn.get("BROAD_EQUITY_STRESS", 0),
                r.get("loc_to_broad_n", 0), r.get("sys_to_loc_n", 0),
                r.get("primary_f1_gt0", 0), r.get("core_ok", 0), r.get("valid", 0),
                r.get("validity_reason", "NA"),
                1 if r.get("id") in selected_ids else 0,
            ]
            lines.append(",".join(str(v) for v in vals))
        self._MsSaveCsv(key, "\n".join(lines))
        return key

    def _MsWriteP1PoliciesCsv(self, rows, ctrl_m):
        bid = self._MsBid()
        key = f"cg_maisr_p1_policies_{bid}.csv"
        headers = [
            "id", "clf_id", "h", "router", "persist", "timing", "CAGR", "MaxDD",
            "annual_stddev", "Sharpe", "worst_5pct_day_mean", "recovery_days_max",
            "oos_sharpe", "crisis_maxdd", "y2020_maxdd", "y2022_maxdd",
            "risk_efficiency", "turnover", "false_broad", "missed_sys",
            "CAGR_cost2", "MaxDD_cost2", "neighbor_stable", "STRICT_PASS", "invalid",
            "validity_reason", "is_control",
        ]
        lines = [",".join(headers)]
        # control row
        ctrl = {
            "id": "CONTROL_PRODUCTION", "clf_id": "CONTROL", "h": "NA", "router": "NA",
            "persist": "NA", "timing": "NA", "CAGR": (ctrl_m or {}).get("CAGR"),
            "MaxDD": (ctrl_m or {}).get("MaxDD"), "annual_stddev": (ctrl_m or {}).get("annual_stddev"),
            "Sharpe": (ctrl_m or {}).get("Sharpe"),
            "worst_5pct_day_mean": (ctrl_m or {}).get("worst_5pct_day_mean"),
            "recovery_days_max": (ctrl_m or {}).get("recovery_days_max"),
            "oos_sharpe": "NA", "crisis_maxdd": "NA", "y2020_maxdd": "NA", "y2022_maxdd": "NA",
            "risk_efficiency": "NA", "turnover": "NA", "false_broad": 0, "missed_sys": 0,
            "CAGR_cost2": "NA", "MaxDD_cost2": "NA", "neighbor_stable": 1, "STRICT_PASS": 0,
            "invalid": 0, "validity_reason": "CONTROL", "is_control": 1,
        }
        lines.append(",".join(str(ctrl.get(h, "NA")) for h in headers))
        for r in rows:
            rr = dict(r)
            rr["validity_reason"] = "OK" if not r.get("invalid") else "INVALID_WINDOW"
            rr["is_control"] = 0
            lines.append(",".join(str(rr.get(h, "NA")) for h in headers))
        self._MsSaveCsv(key, "\n".join(lines))
        return key

    def _MsWriteP1AttributionCsv(self, scored, chosen, rows):
        bid = self._MsBid()
        key = f"cg_maisr_p1_attribution_{bid}.csv"
        headers = [
            "row_type", "clf_id", "h", "state", "true_count", "pred_count",
            "tp", "fp", "fn", "precision", "recall", "f1", "window",
            "false_broad_total", "missed_sys_total", "policies_n", "strict_pass_n",
        ]
        lines = [",".join(headers)]
        for r in scored:
            tp = r.get("tp") or {}
            fp = r.get("fp") or {}
            fn = r.get("fn") or {}
            f1 = r.get("f1") or {}
            for st in _P1_PRIMARY:
                tpc = int(tp.get(st, 0) or 0)
                fpc = int(fp.get(st, 0) or 0)
                fnc = int(fn.get(st, 0) or 0)
                true_c = tpc + fnc
                pred_c = tpc + fpc
                prec = (tpc / (tpc + fpc)) if (tpc + fpc) else 0.0
                rec = (tpc / (tpc + fnc)) if (tpc + fnc) else 0.0
                lines.append(",".join(str(v) for v in (
                    "STATE", r.get("id"), r.get("h"), st, true_c, pred_c, tpc, fpc, fnc,
                    _p1_f(prec, 4), _p1_f(rec, 4), _p1_f(f1.get(st), 4), "TRAIN_2012_2018",
                    "", "", "", "",
                )))
            lines.append(",".join(str(v) for v in (
                "CLF_SUMMARY", r.get("id"), r.get("h"), "ALL",
                r.get("true_broad"), "", "", "", "", "", "", _p1_f(r.get("macro_f1"), 4),
                "TRAIN_2012_2018", r.get("loc_to_broad_n", 0), r.get("sys_to_loc_n", 0), "", "",
            )))
        for r in chosen:
            sub = [x for x in rows if x.get("clf_id") == r["id"]]
            fb = sum(int(x.get("false_broad", 0) or 0) for x in sub)
            ms = sum(int(x.get("missed_sys", 0) or 0) for x in sub)
            sp = sum(1 for x in sub if x.get("STRICT_PASS"))
            lines.append(",".join(str(v) for v in (
                "POLICY_CLF", r["id"], r["h"], "ALL", "", "", "", "", "", "", "", "",
                "RUN", fb, ms, len(sub), sp,
            )))
        self._MsSaveCsv(key, "\n".join(lines))
        return key
