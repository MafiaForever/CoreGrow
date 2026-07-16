# region imports
from AlgorithmImports import *
# endregion
# cg_maisr_p1_diag.py -- CG-MAISR-CANDIDATE-IDENTITY-P1 helpers.
# Extracted from cg_maisr_diag.py to keep both files under the QC 64000-char
# compile limit. Mixed into CgMaisrDiagMixin (see cg_maisr_diag.py). Relies
# on state populated by CgMaisrInit / CgMaisrOnData / _MsEval and on the
# fill-tracking identity ledgers registered by
# CgShadowReplayMixin.CgShadowRegisterIdentityLedgers.

_P1_IDENTITY_IDS = (
    "MAISR_REPLAY_IDENTITY",
    "MAISR_PIPELINE_OFF_IDENTITY",
    "MAISR_SENSOR_NO_ACTION_IDENTITY",
)
_P1_CANARY_ID = "MAISR_CANARY"
_P1_CANARY_CFG = ("S2", 2, 0.50, "H2")
_P1_CANARY_STATES = ("LOCAL_ASSET_STRESS", "SECTOR_STRESS", "BROAD_EQUITY_STRESS")


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
    """Identity/canary finals + artifact export for CG-MAISR-CANDIDATE-IDENTITY-P1."""

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
        """Hard-gate artifact export: try object_store.save then save_bytes."""
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
                # PIPELINE_OFF target audit must be exact
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
        self._MsLog(
            f"CG_MAISR_P1_CANARY_FINAL,classifier={_p1_clfid(*_P1_CANARY_CFG)},"
            f"armed={'YES' if armed else 'NO'},fired={'YES' if fired else 'NO'},"
            f"signal_state={sig_state or 'NA'},signal_time={sig_time or 'NA'},"
            f"fill_symbol={tk or 'NA'},fill_price={_p1_f(px,4)},fill_time={ftime or 'NA'},"
            f"reduce_pct=25,disabled_after_fire={'YES' if fired else 'NO'}"
        )
        return {"armed": armed, "fired": fired, "fill_symbol": tk, "fill_price": px}

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

        headers_c = ["classifier", "armed", "fired", "signal_state", "signal_time",
                     "fill_symbol", "fill_price", "fill_time", "reduce_pct"]
        vals_c = [
            _p1_clfid(*_P1_CANARY_CFG), "YES" if canary_result.get("armed") else "NO",
            "YES" if canary_result.get("fired") else "NO",
            getattr(self, "_ms_canary_signal_state", None) or "NA",
            getattr(self, "_ms_canary_signal_time", None) or "NA",
            canary_result.get("fill_symbol") or "NA", _p1_f(canary_result.get("fill_price"), 4),
            getattr(self, "_ms_canary_fill_time", None) or "NA", 25,
        ]
        lines3 = [",".join(headers_c), ",".join(str(v) for v in vals_c)]
        self._MsSaveCsv(f"cg_maisr_p1_canary_{bid}.csv", "\n".join(lines3))
