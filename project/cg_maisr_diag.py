# region imports
from AlgorithmImports import *
from datetime import date as _date
from collections import deque
from cg_maisr_p1_diag import CgMaisrP1Mixin, _P1_CANARY_CFG, _P1_CANARY_STATES
from cg_maisr_d2_diag import CgMaisrD2DiagMixin
from cg_maisr_final_d3 import CgMaisrFinalD3Mixin
from cg_maisr_d4_overlay import CgMaisrD4OverlayMixin
from cg_macro_resid_b1_diag import CgMacroResidB1DiagMixin
from cg_macro_a1_diag import CgMacroA1DiagMixin
from cg_damage_duration_d01_diag import CgDamageDurationD01DiagMixin as _DD01
from cg_maisr_ms_classify import ms_classify
# endregion
# cg_maisr_diag.py -- CG-MAISR-D0/P1/D2 multi-asset stress router (diagnostic only).

_STATES = ("SYSTEMIC_LIQUIDITY_STRESS", "RATE_INFLATION_STRESS", "BROAD_EQUITY_STRESS",
           "SECTOR_STRESS", "LOCAL_ASSET_STRESS", "DEFENSIVE_ROTATION",
           "UNCONFIRMED_NOISE", "NORMAL")
_SIX = {s: i for i, s in enumerate(_STATES)}
_STRESS6 = _STATES[:6]

_ROLES = {
    "BROAD": ("SPY",), "SH": ("SH",), "GROWTH": ("SPYG",),
    "BREADTH": ("XLE", "XLB", "XLV", "XLU", "SPYG"),
    "DUR": ("BND", "TIP"), "INFL": ("DBC", "XLE"),
    "PARK": ("BIL", "SGOV", "USFR"),
}
_PROXY = {"XLE": None, "XLB": None, "XLV": None, "XLU": None, "DBC": None,
          "MU": None, "NVDA": None, "AVGO": None}
_RISK_UNIV = ("SPY", "MU", "NVDA", "AVGO")
_IDS_CODE = {"NORMAL": 0, "WATCH": 1, "STRESS": 2, "PANIC_SHORT": 3}
_IDS_ELEV = ("WATCH", "STRESS", "PANIC_SHORT")

_SENS = {"S1": (0.35, 0.50), "S2": (0.50, 0.65), "S3": (0.75, 0.80)}
_AMIN = (2, 3)
_BRTH = (0.50, 0.65, 0.75)
_HMODE = ("H0", "H1", "H2")
_W5 = (0.20, 0.25, 0.20, 0.25, 0.10)


def _clfid(s, a, b, h):
    return f"{s}_C{a}_B{int(round(b * 100)):02d}_{h}"


_ALL_CFG = [(s, a, b, h) for s in ("S1", "S2", "S3") for a in _AMIN
            for b in _BRTH for h in _HMODE]

# Router fields (index): local, sector, broad_equity, systemic_equity,
# rate_equity, rate_duration, defensive_rotation_equity.
_RM_LOCAL, _RM_SECTOR, _RM_BROAD, _RM_SYS, _RM_RATE_EQ, _RM_RATE_DUR, _RM_DEF = range(7)
_ROUTERS = {
    "R1": (0.50, 0.75, 1.00, 1.00, 1.00, 1.00, 1.00),
    "R2": (1.00, 1.00, 0.60, 0.25, 0.60, 0.50, 0.60),
    "R3": (0.75, 0.75, 0.80, 0.50, 0.80, 0.75, 0.80),
    "R4": (0.50, 0.50, 0.60, 0.25, 0.60, 0.50, 0.60),
    "R5": (0.25, 0.50, 0.40, 0.00, 0.40, 0.25, 0.40),
    "R6": (0.25, 0.50, 0.75, 0.25, 0.60, 0.50, 0.75),
}
_PERSIST = {"P1": 1, "P2": 2, "P3": 3}
_TIMING = ("T1", "T2", "T3")
_RORD = {"R1": 0, "R2": 1, "R3": 2, "R4": 3, "R5": 4, "R6": 5}
_PORD = {"P1": 0, "P2": 1, "P3": 2}


def _tk(sym):
    try:
        return str(sym.Value)
    except Exception:
        try:
            return str(sym.value)
        except Exception:
            return str(sym)


def _f(x, d=4):
    if x is None:
        return "NA"
    try:
        return f"{float(x):.{d}f}"
    except Exception:
        return "NA"


class CgMaisrDiagMixin(_DD01, CgMacroResidB1DiagMixin, CgMacroA1DiagMixin, CgMaisrD4OverlayMixin, CgMaisrFinalD3Mixin, CgMaisrD2DiagMixin, CgMaisrP1Mixin):
    """CG-MAISR-D0: multi-asset stress classifier grid + 324-policy router sim."""

    def CgMaisrInit(self) -> None:
        ov = getattr(self, "_rrx_param_overrides", {}) or {}

        def _p(k, d=""):
            v = self.get_parameter(k)
            if v is None or str(v).strip() == "":
                v = ov.get(k, d)
            return v

        def _bool(k, d="0"):
            return str(_p(k, d) or d).strip().lower() in ("1", "true", "yes", "on")

        def _float(k, d):
            try:
                return float(str(_p(k, str(d)) or d).strip())
            except Exception:
                return float(d)

        self.cg_maisr_diag_enable = _bool("cg_maisr_diag_enable", "0")
        self.cg_maisr_grid_enable = _bool("cg_maisr_grid_enable", "0")
        self.cg_maisr_emit_events = _bool("cg_maisr_emit_events", "0")
        self.cg_maisr_identity_only = _bool("cg_maisr_identity_only", "0")
        self.cg_maisr_identity_debug = _bool("cg_maisr_identity_debug", "0")
        self._D2ReadParams(_p, _bool)
        self._D3ReadParams(_p, _bool)
        self._D4ReadParams(_p, _bool)
        self._MacroA1ReadParams(_p, _bool)
        self._MacroResidB1ReadParams(_p, _bool)
        self._DamageD01ReadParams(_p, _bool)
        self._ms_cost_bps = _float("cg_maisr_cost_bps", 0.0)
        self._ms_on = bool(self.cg_maisr_diag_enable)
        if getattr(self, "cg_maisr_d4_enable", False):
            self._ms_on = True
            self.cg_maisr_diag_enable = True
        if getattr(self, "cg_macro_a1_enable", False):
            self._ms_on = True
            self.cg_maisr_diag_enable = True
        if getattr(self, "cg_macro_resid_b1_enable", False):
            self._ms_on = True
            self.cg_maisr_diag_enable = True
        self._DamageD01MaybeEnableMs()
        self._ms_grid_on = bool(self._ms_on and self.cg_maisr_grid_enable)
        self._ms_emit = bool(self._ms_on and self.cg_maisr_emit_events)
        self._ms_log_used = 0
        self._ms_err = 0
        self._ms_emitted = False

        lp = list(getattr(self, "log_only_prefixes", None) or [])
        for pref in (
            "CG_MAISR_D0_", "CG_MAISR_P1_", "CG_MAISR_D2_", "CG_MAISR_D3_", "CG_MAISR_D4_",
            "CG_MAISR_CLOSEOUT", "CG_MACRO_A1_", "CG_MACRO_RESID_B1_", "CG_MACRO_RESID_B11_", "CG_MACRO_A1_CLOSEOUT",
        ):
            if pref not in lp:
                lp.append(pref)
        self.log_only_prefixes = lp

        if not self._ms_on:
            self.log("CG_MAISR_D0_INIT,enable=0,diagnostic_real_order_count=0")
            return

        self._MsAuditSubscriptions()
        self._ms_day = {}
        self._ms_prev_close = {}
        self._ms_atr = {}
        self._ms_tr_ring = {tk: deque(maxlen=20) for tk in self._ms_all}
        self._ms_ring = {tk: deque(maxlen=90) for tk in self._ms_all}
        self._ms_events = []
        self._ms_caps = []
        self._ms_eod = []
        self._ms_n_pre = 0
        self._ms_n_post = 0
        self._ms_n_cap = 0
        self._ms_n_exe = 0
        self._ms_current_risk = set()
        self._ms_last_day = None

        # Minute-bar dedup counters (Slice.Bars TradeBars only).
        self._ms_bd_seen = 0
        self._ms_bd_accept = 0
        self._ms_bd_dup = 0
        self._ms_bd_conflict = 0
        self._ms_bd_oo = 0
        self._ms_bar_last_et = {}
        # Evaluation dedup counters.
        self._ms_eval_seen = 0
        self._ms_eval_uniq = 0
        self._ms_eval_dup = 0
        self._ms_eval_keys = set()
        # P1 canary state (fixed classifier S2_C2_B50_H2, single-shot).
        self._ms_canary_idx = None
        try:
            self._ms_canary_idx = _ALL_CFG.index(_P1_CANARY_CFG)
        except Exception:
            self._ms_canary_idx = None
        self._ms_canary_armed = False
        self._ms_canary_fired = False
        self._ms_canary_signal_time = None
        self._ms_canary_signal_state = None
        self._ms_canary_fill_tk = None
        self._ms_canary_fill_px = None
        self._ms_canary_fill_time = None

        try:
            spy = getattr(self, "sym_spy", None)
            if spy is not None:
                self.schedule.on(self.date_rules.every_day(spy),
                                  self.time_rules.after_market_open(spy, 0),
                                  self.CgMaisrSessionPrep)
                self.schedule.on(self.date_rules.every_day(spy),
                                  self.time_rules.after_market_open(spy, 14),
                                  self.CgMaisrPreCapture)
        except Exception as exc:
            self._ms_err += 1
            self.log(f"CG_MAISR_D0_INIT,schedule_error={type(exc).__name__}")

        self.log(
            f"CG_MAISR_D0_INIT,enable=1,grid_enable={int(self._ms_grid_on)},"
            f"emit_events={int(self._ms_emit)},cost_bps={_f(self._ms_cost_bps,2)},"
            f"panel={','.join(sorted(self._ms_all))},gold={self._ms_gold},"
            f"configs=54,policies=324,diagnostic_real_order_count=0"
        )
        self.log(
            f"CG_MAISR_D0_SUBSCRIPTION_FINAL,panel_n={len(self._ms_all)},"
            f"excluded={','.join(sorted(self._ms_excluded)) or 'NONE'},"
            f"mixed_resolution={','.join(getattr(self,'_ms_mixed',[]) or []) or 'NONE'}"
        )
        if not getattr(self, "cg_maisr_label_only", False):
            self.log(
                f"CG_MAISR_P1_INIT,identity_only={int(self.cg_maisr_identity_only)},"
                f"identity_debug={int(self.cg_maisr_identity_debug)},"
                f"canary_classifier={_clfid(*_P1_CANARY_CFG)},canary_idx={self._ms_canary_idx}"
            )
            dup_cfg = getattr(self, "_ms_dup_cfg", []) or []
            self.log(
                f"CG_MAISR_P1_SUBSCRIPTION_FINAL,panel_n={len(self._ms_all)},"
                f"excluded={','.join(sorted(self._ms_excluded)) or 'NONE'},"
                f"mixed_resolution={','.join(getattr(self,'_ms_mixed',[]) or []) or 'NONE'},"
                f"duplicate_tradebar_config={','.join(dup_cfg) or 'NONE'},"
                f"config_rows={len(getattr(self,'_ms_sub_rows',[]) or [])}"
            )
        try:
            self._D2InitHooks()
        except Exception:
            self._ms_err += 1
        try:
            self._D3InitHooks()
        except Exception:
            self._ms_err += 1
        try:
            self._D4InitHooks()
        except Exception:
            self._ms_err += 1
        try:
            self._MacroA1InitHooks()
        except Exception:
            self._ms_err += 1
        try:
            self._MacroResidB1InitHooks()
        except Exception:
            self._ms_err += 1
        self._DamageD01InitHooksSafe()

    def _MsAuditSubscriptions(self) -> None:
        want = set()
        for grp in _ROLES.values():
            want.update(grp)
        want.update(("GLD", "GLDM"))
        per_tk = {}
        rows = []
        try:
            all_cfgs = list(self.subscription_manager.subscriptions)
        except Exception:
            all_cfgs = []
        for cfg in all_cfgs:
            try:
                tk = str(cfg.symbol.value)
                if tk not in want:
                    continue
                res = getattr(cfg, "resolution", None)
                try:
                    is_minute = (res == Resolution.MINUTE)
                    is_daily = (res == Resolution.DAILY)
                except Exception:
                    is_minute = is_daily = False
                res_name = str(res) if res is not None else "NA"
                dtype = getattr(cfg, "type", None)
                dtype_name = "NA"
                if dtype is not None:
                    dtype_name = getattr(dtype, "Name", None) or getattr(dtype, "__name__", None) \
                        or str(dtype)
                ttype = getattr(cfg, "tick_type", None)
                ttype_name = str(ttype) if ttype is not None else "NA"
                dn = str(dtype_name).lower()
                tn = str(ttype_name).lower()
                is_tb = ("tradebar" in dn) or (tn in ("trade", "0") and "quote" not in dn)
                is_qb = ("quotebar" in dn) or ("quote" in tn)
                is_oi = ("openinterest" in dn) or ("open_interest" in dn) or ("openinterest" in tn)
                rec = per_tk.setdefault(tk, {
                    "minute": 0, "daily": 0, "other": 0,
                    "tradebar": 0, "quotebar": 0, "oi": 0,
                })
                if is_minute:
                    rec["minute"] += 1
                elif is_daily:
                    rec["daily"] += 1
                else:
                    rec["other"] += 1
                if is_tb:
                    rec["tradebar"] += 1
                elif is_qb:
                    rec["quotebar"] += 1
                elif is_oi:
                    rec["oi"] += 1
                rows.append({
                    "ticker": tk, "resolution": res_name, "data_type": dtype_name,
                    "tick_type": ttype_name, "is_tradebar": int(is_tb),
                    "is_quotebar": int(is_qb), "is_open_interest": int(is_oi),
                })
            except Exception:
                continue
        avail = {tk for tk, rec in per_tk.items()
                 if (rec["minute"] + rec["daily"] + rec["other"]) >= 1}
        usable_tb = {tk for tk, rec in per_tk.items()
                     if rec.get("minute", 0) >= 1 and rec.get("tradebar", 0) >= 1}
        classes = {}
        for tk, rec in per_tk.items():
            if rec["minute"] >= 1 and rec["daily"] >= 1:
                classes[tk] = "MIXED_RESOLUTION"
            elif rec["tradebar"] > 1:
                classes[tk] = "DUPLICATE_TRADEBAR_CONFIG"
            else:
                classes[tk] = "NORMAL_MULTI_CONFIG"
        for row in rows:
            row["classification"] = classes.get(row["ticker"], "NORMAL_MULTI_CONFIG")
        self._ms_sub_rows = rows
        self._ms_sub_classes = classes
        self._ms_mixed = sorted(tk for tk, c in classes.items() if c == "MIXED_RESOLUTION")
        self._ms_dup_cfg = sorted(tk for tk, c in classes.items() if c == "DUPLICATE_TRADEBAR_CONFIG")
        self._ms_gold_primary = "GLD" if "GLD" in usable_tb else None
        self._ms_gold_fallback = "GLDM" if "GLDM" in usable_tb else None
        self._ms_gold = self._ms_gold_primary or self._ms_gold_fallback or "GLD"
        roles = {}
        for name, grp in _ROLES.items():
            roles[name] = tuple(t for t in grp if t in avail)
        roles["GOLD"] = (self._ms_gold,) if self._ms_gold in avail else ()
        self._ms_roles = roles
        allsyms = set()
        for grp in roles.values():
            allsyms.update(grp)
        self._ms_excluded = want - allsyms
        self._ms_all = allsyms

    def CgMaisrSessionPrep(self) -> None:
        if not getattr(self, "_ms_on", False):
            return
        self._ms_last_day = self.time.date()

    def CgMaisrPreCapture(self) -> None:
        if not getattr(self, "_ms_on", False):
            return
        try:
            if getattr(self, "IsWarmingUp", False) or getattr(self, "is_warming_up", False):
                return
            self._MsEval("PRE")
        except Exception:
            self._ms_err += 1

    def CgMaisrOnData(self, data) -> None:
        if not getattr(self, "_ms_on", False) or data is None:
            return
        try:
            bars = getattr(data, "bars", None) or getattr(data, "Bars", None)
            if bars:
                seen_local = {}
                for kvp in bars:
                    try:
                        sym = kvp.Key if hasattr(kvp, "Key") else kvp
                        bar = kvp.Value if hasattr(kvp, "Value") else bars[kvp]
                    except Exception:
                        continue
                    self._ms_bd_seen += 1
                    try:
                        tk = _tk(sym)
                        et = getattr(bar, "end_time", None) or getattr(bar, "EndTime", None)
                        period = getattr(bar, "period", None) or getattr(bar, "Period", None)
                        o, h, l, c = float(bar.open), float(bar.high), float(bar.low), float(bar.close)
                        v = float(bar.volume or 0)
                    except Exception:
                        continue
                    if tk not in self._ms_all:
                        continue
                    dkey = (tk, et, period)
                    prev_ohlc = seen_local.get(dkey)
                    if prev_ohlc is not None:
                        if prev_ohlc == (o, h, l, c):
                            self._ms_bd_dup += 1
                        else:
                            self._ms_bd_conflict += 1
                        continue
                    last_et = self._ms_bar_last_et.get(tk)
                    if last_et is not None and et is not None and et < last_et:
                        self._ms_bd_oo += 1
                        continue
                    seen_local[dkey] = (o, h, l, c)
                    if et is not None:
                        self._ms_bar_last_et[tk] = et
                    self._ms_bd_accept += 1
                    self._MsUpdateBar(tk, self.time, o, h, l, c, v)
                    try:
                        if getattr(self, "cg_maisr_label_only", False) or getattr(self, "_d2_mode", False):
                            self._D2OnBar(tk, et, o, h, l, c)
                        if getattr(self, "cg_macro_a1_enable", False) and hasattr(self, "_MacroA1OnAcceptedBar"):
                            self._MacroA1OnAcceptedBar(tk, et, o, h, l, c)
                        if getattr(self, "cg_macro_resid_b1_enable", False) and hasattr(self, "_MacroResidB1OnAcceptedBar"):
                            self._MacroResidB1OnAcceptedBar(tk, et, o, h, l, c)
                        self._DamageD01OnAcceptedBarSafe(tk, et, o, h, l, c)
                    except Exception:
                        self._ms_err += 1
                if self.cg_maisr_identity_only:
                    try:
                        self._MsCanaryTryFire(bars)
                    except Exception:
                        self._ms_err += 1
            if getattr(self, "IsWarmingUp", False) or getattr(self, "is_warming_up", False):
                return
            t = self.time
            tod = t.hour * 60 + t.minute
            if 590 <= tod <= 945 and tod % 5 == 0:
                self._MsEval("POST")
        except Exception:
            self._ms_err += 1

    def _MsUpdateBar(self, tk, ts, o, h, l, c, v) -> None:
        d = ts.date()
        day = self._ms_day.get(tk)
        if day is None or day["d"] != d:
            if day is not None and day.get("o") is not None:
                self._MsFinalizeDay(tk, day)
            self._ms_day[tk] = {"d": d, "o": o, "h": h, "l": l, "c": c, "v": v}
        else:
            day["h"] = max(day["h"], h)
            day["l"] = min(day["l"], l)
            day["c"] = c
            day["v"] += v
        self._ms_ring[tk].append((ts, o, h, l, c, v))

    def _MsFinalizeDay(self, tk, day) -> None:
        pc = self._ms_prev_close.get(tk)
        h, l, c = day["h"], day["l"], day["c"]
        tr = h - l
        if pc is not None and pc > 0:
            tr = max(tr, abs(h - pc), abs(l - pc))
        ring = self._ms_tr_ring[tk]
        ring.append(tr)
        self._ms_atr[tk] = sum(ring) / len(ring)
        self._ms_prev_close[tk] = c

    def _MsFeat(self, tk):
        day = self._ms_day.get(tk)
        pc = self._ms_prev_close.get(tk)
        ring = self._ms_ring.get(tk)
        if not day or pc is None or pc <= 0 or not ring:
            return None
        atr = self._ms_atr.get(tk) or max(pc * 0.01, 0.01)
        o, h, l, c = day["o"], day["h"], day["l"], day["c"]
        gap = -(o - pc) / atr
        openstr = -(c - o) / atr
        roc15 = 0.0
        if len(ring) >= 16:
            c15 = ring[-16][4]
            roc15 = -(c - c15) / atr
        dd = (h - c) / atr
        rng = (h - l) / atr - 1.0
        mv = (c - pc) / atr
        return {"raw": (gap, openstr, roc15, dd, rng), "mv": mv}

    def _MsPx(self, tickers):
        out = []
        cache = getattr(self, "_ms_px_cache", None)
        if cache is None:
            cache = {}
            try:
                for k in self.securities.keys():
                    cache[_tk(k)] = k
            except Exception:
                pass
            self._ms_px_cache = cache
        for t in tickers:
            p = 0.0
            try:
                k = cache.get(t)
                if k is not None:
                    p = float(self.securities[k].price)
            except Exception:
                p = 0.0
            out.append(p if p and p > 0 else 0.0)
        return tuple(out)

    def _MsClassify(self, feat, cl, thr, amin, bthr, hmode, s):
        return ms_classify(
            feat, cl, thr, amin, bthr, hmode, s,
            self._ms_roles, getattr(self, "_ms_current_risk", set()) or set(),
            getattr(self, "_ids_state", "NORMAL"), self._ms_gold,
        )

    def _MsEval(self, kind) -> None:
        self._ms_eval_seen += 1
        ekey = (self.time.date(), self.time.hour, self.time.minute, kind)
        if ekey in self._ms_eval_keys:
            self._ms_eval_dup += 1
            return
        self._ms_eval_keys.add(ekey)
        self._ms_eval_uniq += 1
        feat = {}
        for tk in self._ms_all:
            f = self._MsFeat(tk)
            if f is not None:
                feat[tk] = f
        if not feat:
            return
        states = bytearray(54)
        subjects = bytearray(54)
        clip_cache = {}
        for sname in ("S1", "S2", "S3"):
            scale, thr = _SENS[sname]
            cl = {}
            for tk, f in feat.items():
                cl[tk] = [max(0.0, min(1.0, v / scale)) for v in f["raw"]]
            clip_cache[sname] = (cl, thr)
        d4 = bool(getattr(self, "cg_maisr_d4_enable", False))
        for i, (s, a, b, h) in enumerate(_ALL_CFG):
            cl, thr = clip_cache[s]
            if d4 and hasattr(self, "_D4ClassifyPair"):
                st, subj = self._D4ClassifyPair(feat, cl, thr, a, b, h, s)
                states[i] = _SIX.get(st, _SIX["NORMAL"])
                subjects[i] = int(subj) & 0xFF
            else:
                states[i] = _SIX[self._MsClassify(feat, cl, thr, a, b, h, s)]
                subjects[i] = 0
        d = self.time.date()
        tod = self.time.hour * 60 + self.time.minute
        ids_code = _IDS_CODE.get(getattr(self, "_ids_state", None), 0)
        rg = str(getattr(self, "current_regime", None) or "NEUTRAL").upper()
        rg_code = {"RISK_ON": 0, "NEUTRAL": 1, "RISK_OFF": 2}.get(rg, 1)
        w2 = 1 if getattr(self, "_cg_w2_last_active", False) else 0
        kindbit = 1 if kind == "PRE" else 0
        meta = (kindbit << 7) | (ids_code << 4) | (rg_code << 2) | w2
        riskbyte = 0
        for bit, rt in enumerate(_RISK_UNIV):
            if rt in self._ms_current_risk:
                riskbyte |= (1 << bit)
        px = self._MsPx(("SPY", "SH", self._ms_gold, "BND"))
        self._ms_events.append((d.toordinal(), tod, bytes(states), meta, riskbyte, px))
        if d4:
            self._ms_subjects_events = getattr(self, "_ms_subjects_events", [])
            self._ms_subjects_events.append(bytes(subjects))
        try:
            macro = bool(getattr(self, "cg_macro_a1_enable", False))
            resid = bool(getattr(self, "cg_macro_resid_b1_enable", False))
            dmg = self._DamageD01WantEval()
            if (getattr(self, "cg_maisr_label_only", False) or getattr(self, "_d2_mode", False)
                    or d4 or macro or resid or dmg):
                if d4:
                    self._d4_last_subjects = bytes(subjects)
                self._D2OnEval(kind, tod, bytes(states), feat)
                if d4 and hasattr(self, "_D4RuntimeOnEval"):
                    self._D4RuntimeOnEval(kind, tod, bytes(states), bytes(subjects), feat)
                if macro and hasattr(self, "_MacroA1OnEval"):
                    self._MacroA1OnEval(kind, tod, bytes(states), feat)
                if resid and hasattr(self, "_MacroResidB1OnEval"):
                    self._MacroResidB1OnEval(kind, tod, bytes(states), feat)
                self._DamageD01OnEvalSafe(kind, tod, bytes(states), feat)
        except Exception:
            self._ms_err += 1
        if kind == "PRE":
            self._ms_n_pre += 1
        else:
            self._ms_n_post += 1
        if (kind == "POST" and self.cg_maisr_identity_only and self._ms_canary_idx is not None
                and not self._ms_canary_fired and not self._ms_canary_armed and self._ms_caps
                and self._ms_caps[-1][0] == d.toordinal()):
            st = _STATES[states[self._ms_canary_idx]]
            if st in _P1_CANARY_STATES:
                self._ms_canary_armed = True
                self._ms_canary_signal_time = self.time
                self._ms_canary_signal_state = st

    def CgMaisrOnCapture(self, base, rg, slot, imm, reduce_only, emergency) -> None:
        if not getattr(self, "_ms_on", False):
            return
        try:
            bw = {}
            for k, v in (base or {}).items():
                try:
                    w = float(v or 0.0)
                except Exception:
                    continue
                if abs(w) > 1e-12:
                    bw[k if isinstance(k, str) else _tk(k)] = w
            park = set(self._ms_roles.get("PARK", ()))
            dur = set(self._ms_roles.get("DUR", ()))
            gold = {self._ms_gold}
            cash_tk = _tk(getattr(self, "sym_cash", None)) if getattr(self, "sym_cash", None) is not None else "BIL"
            self._ms_current_risk = {
                t for t, w in bw.items()
                if w > 1e-9 and t not in park and t not in dur and t not in gold and t != cash_tk
            }
            d = self.time.date()
            self._ms_caps.append((d.toordinal(), dict(bw), str(rg or "NEUTRAL").upper(),
                                   int(slot or 0), bool(imm), bool(reduce_only), bool(emergency)))
            self._ms_n_cap += 1
        except Exception:
            self._ms_err += 1

    def CgMaisrOnExecutePending(self) -> None:
        if not getattr(self, "_ms_on", False):
            return
        self._ms_n_exe += 1

    def CgMaisrOnMark(self, today, px) -> None:
        if not getattr(self, "_ms_on", False):
            return
        try:
            keep = {}
            for t, p in (px or {}).items():
                try:
                    pf = float(p)
                    if pf > 0:
                        keep[t] = pf
                except Exception:
                    continue
            self._ms_eod.append((today.toordinal(), keep))
        except Exception:
            self._ms_err += 1

    # ---------------- EOA reconstruction ----------------

    def _MsBuildIndex(self) -> None:
        idx = {}
        for (do, tod, states, meta, riskbyte, px) in self._ms_events:
            rec = idx.setdefault(do, {"pre": None, "post": []})
            if (meta >> 7) & 1:
                if rec["pre"] is None:
                    rec["pre"] = states
            else:
                rec["post"].append((tod, states))
        for (do, bw, rg, slot, imm, rq, em) in self._ms_caps:
            rec = idx.setdefault(do, {"pre": None, "post": []})
            rec["cap"] = (bw, rg, slot, imm, rq, em)
        for (do, px) in self._ms_eod:
            rec = idx.setdefault(do, {"pre": None, "post": []})
            rec["px"] = px
        for r in idx.values():
            r["post"].sort(key=lambda x: x[0])
        self._ms_idx = idx
        self._ms_days_sorted = sorted(idx.keys())

    def _MsSeries(self, tk):
        c = getattr(self, "_ms_series_cache", None)
        if c is None:
            c = {}
            self._ms_series_cache = c
        if tk not in c:
            out = []
            for do in self._ms_days_sorted:
                px = self._ms_idx[do].get("px")
                if px and tk in px:
                    out.append((do, px[tk]))
            c[tk] = out
        return c[tk]

    def _MsCompositeSeries(self, tks):
        out = []
        for do in self._ms_days_sorted:
            px = self._ms_idx[do].get("px") or {}
            vals = [px[t] for t in tks if t in px and px[t] > 0]
            if vals:
                out.append((do, sum(vals) / len(vals)))
        return out

    def _MsFwd(self, series, n=5):
        m = {}
        L = len(series)
        for i in range(L - n):
            d0, p0 = series[i]
            _, p1 = series[i + n]
            if p0 > 0:
                m[d0] = p1 / p0 - 1.0
        return m

    def _MsFwdFor(self, tk, n=5):
        c = getattr(self, "_ms_fwd_cache", None)
        if c is None:
            c = {}
            self._ms_fwd_cache = c
        key = (tk, n)
        if key not in c:
            c[key] = self._MsFwd(self._MsSeries(tk), n)
        return c[key]

    def _MsBuildLabels(self) -> None:
        park = set(self._ms_roles.get("PARK", ()))
        dur = set(self._ms_roles.get("DUR", ()))
        gold = {self._ms_gold}
        spy_f = self._MsFwdFor("SPY")
        gold_f = self._MsFwdFor(self._ms_gold)
        dur_f = self._MsFwd(self._MsCompositeSeries(self._ms_roles.get("DUR", ())))
        infl_f = self._MsFwd(self._MsCompositeSeries(self._ms_roles.get("INFL", ())))
        br_f = self._MsFwd(self._MsCompositeSeries(self._ms_roles.get("BREADTH", ())))
        spyg_f = self._MsFwdFor("SPYG")
        labels = {}
        for do in self._ms_days_sorted:
            sf, df = spy_f.get(do), dur_f.get(do)
            infl, brf, gf = infl_f.get(do), br_f.get(do), gold_f.get(do)
            lab = None
            if sf is not None and df is not None and sf <= -0.03 and df <= -0.01:
                lab = "SYSTEMIC_LIQUIDITY_STRESS"
            elif infl is not None and df is not None and infl >= 0.03 and df <= -0.015:
                lab = "RATE_INFLATION_STRESS"
            elif sf is not None and sf <= -0.03:
                lab = "BROAD_EQUITY_STRESS"
            elif brf is not None and sf is not None and brf <= -0.03 and sf > -0.02:
                lab = "SECTOR_STRESS"
            else:
                cap = self._ms_idx.get(do, {}).get("cap")
                spf = spyg_f.get(do)
                if cap:
                    bw = cap[0]
                    risk_syms = {t for t, w in bw.items()
                                 if w > 1e-9 and t not in park and t not in dur and t not in gold}
                    if (any(_PROXY.get(t) == "SPYG" for t in risk_syms) and spf is not None
                            and spf <= -0.05 and (brf is None or brf > -0.02) and (sf is None or sf > -0.02)):
                        lab = "LOCAL_ASSET_STRESS"
                if lab is None and gf is not None and df is not None and sf is not None \
                        and gf >= 0.02 and df >= 0.01 and sf <= -0.01:
                    lab = "DEFENSIVE_ROTATION"
            labels[do] = lab
        self._ms_labels = labels

    def _MsScoreConfigs(self):
        t0, t1 = _date(2012, 1, 1).toordinal(), _date(2018, 12, 31).toordinal()
        train_days = [do for do in self._ms_days_sorted
                      if t0 <= do <= t1 and self._ms_idx[do].get("pre")]
        scored = []
        for i, (s, a, b, h) in enumerate(_ALL_CFG):
            tp = {k: 0 for k in _STRESS6}
            fp = {k: 0 for k in _STRESS6}
            fn = {k: 0 for k in _STRESS6}
            loc_to_broad = sys_to_loc = n = 0
            for do in train_days:
                true_lab = self._ms_labels.get(do)
                pred = _STATES[self._ms_idx[do]["pre"][i]]
                n += 1
                for k in _STRESS6:
                    pt, pp = (true_lab == k), (pred == k)
                    if pt and pp:
                        tp[k] += 1
                    elif pp and not pt:
                        fp[k] += 1
                    elif pt and not pp:
                        fn[k] += 1
                if true_lab == "LOCAL_ASSET_STRESS" and pred == "BROAD_EQUITY_STRESS":
                    loc_to_broad += 1
                if true_lab == "SYSTEMIC_LIQUIDITY_STRESS" and pred == "LOCAL_ASSET_STRESS":
                    sys_to_loc += 1
            f1 = {}
            f1s = []
            for k in _STRESS6:
                p = tp[k] / (tp[k] + fp[k]) if (tp[k] + fp[k]) else 0.0
                r = tp[k] / (tp[k] + fn[k]) if (tp[k] + fn[k]) else 0.0
                fv = (2 * p * r / (p + r)) if (p + r) else 0.0
                f1[k] = fv
                f1s.append(fv)
            macro_f1 = sum(f1s) / len(f1s) if f1s else 0.0
            n_sys = tp["SYSTEMIC_LIQUIDITY_STRESS"] + fn["SYSTEMIC_LIQUIDITY_STRESS"]
            n_loc = tp["LOCAL_ASSET_STRESS"] + fn["LOCAL_ASSET_STRESS"]
            sys_fn = (fn["SYSTEMIC_LIQUIDITY_STRESS"] / n_sys) if n_sys else 0.0
            broad_fp = (fp["BROAD_EQUITY_STRESS"] / n) if n else 0.0
            loc_to_broad_r = (loc_to_broad / n_loc) if n_loc else 0.0
            sys_to_loc_r = (sys_to_loc / n_sys) if n_sys else 0.0
            score = macro_f1 - 2.0 * sys_fn - 1.5 * broad_fp - 1.5 * loc_to_broad_r - 1.0 * sys_to_loc_r
            scored.append({"idx": i, "id": _clfid(s, a, b, h), "s": s, "a": a, "b": b, "h": h,
                           "score": score, "macro_f1": macro_f1, "sys_fn": sys_fn,
                           "broad_fp": broad_fp, "loc_to_broad": loc_to_broad_r,
                           "sys_to_loc": sys_to_loc_r, "n": n,
                           "tp": tp, "fp": fp, "fn": fn, "f1": f1,
                           "loc_to_broad_n": loc_to_broad, "sys_to_loc_n": sys_to_loc})
        return self._MsEnrichScored(scored)

    def _MsSelectClassifiers(self, scored):
        chosen, modes = self._MsSelectClassifiersValid(scored)
        self._ms_selected_ids = [r["id"] for r in chosen]
        self._ms_selected_modes = modes
        return chosen

    def _MsBuildPolicies(self, chosen):
        metas = []
        for r in chosen:
            for rk in _ROUTERS:
                for pk in _PERSIST:
                    for tk in _TIMING:
                        metas.append({
                            "id": f"MAISR_{r['id']}_{rk}_{pk}_{tk}",
                            "clf_idx": r["idx"], "clf_id": r["id"], "h": r["h"],
                            "router": rk, "persist": pk, "timing": tk,
                        })
        return metas

    def _MsScaleMap(self, state, router, tickers, dur_stressed, gold_stressed):
        """Per-state, per-role scale map. Parking is always preserved."""
        if state not in _STRESS6:
            return {}
        roles = self._ms_roles
        park = roles.get("PARK", ())
        dur_set = set(roles.get("DUR", ()))
        gold = self._ms_gold
        out = {}
        for t in tickers:
            if t in park:
                continue
            is_dur = t in dur_set
            is_gold = (t == gold)
            if state in ("LOCAL_ASSET_STRESS", "SECTOR_STRESS"):
                if not is_dur and not is_gold and t != "SPY":
                    out[t] = router[_RM_LOCAL if state == "LOCAL_ASSET_STRESS" else _RM_SECTOR]
            elif state == "BROAD_EQUITY_STRESS":
                if not is_dur and not is_gold:
                    out[t] = router[_RM_BROAD]
            elif state == "SYSTEMIC_LIQUIDITY_STRESS":
                if not is_dur and not is_gold:
                    out[t] = router[_RM_SYS]
                elif is_dur and dur_stressed:
                    out[t] = router[_RM_RATE_DUR]
                elif is_gold and gold_stressed:
                    out[t] = router[_RM_SYS]
            elif state == "RATE_INFLATION_STRESS":
                if is_dur:
                    out[t] = router[_RM_RATE_DUR]
                elif is_gold:
                    if gold_stressed:
                        out[t] = router[_RM_RATE_EQ]
                else:
                    out[t] = router[_RM_RATE_EQ]
            elif state == "DEFENSIVE_ROTATION":
                if not is_dur and not is_gold:
                    out[t] = router[_RM_DEF]
        return out

    def _MsNav(self, led, px):
        hv = 0.0
        for t, q in led["qty"].items():
            p = px.get(t)
            if p and p > 0:
                hv += q * p
        return led["cash"] + hv

    def _MsApplyWeights(self, led, weights, px, bps):
        nav = self._MsNav(led, px)
        if nav <= 0:
            return 0.0
        turn = 0.0
        for t in list(led["qty"].keys()):
            if t not in (weights or {}):
                p = px.get(t)
                if p and p > 0:
                    q = led["qty"].pop(t)
                    turn += abs(q) * p
                    led["cash"] += q * p - abs(q) * p * bps / 10000.0
        for t, w in (weights or {}).items():
            p = px.get(t)
            if not p or p <= 0:
                continue
            desire = w * nav / p
            cur = led["qty"].get(t, 0.0)
            dq = desire - cur
            if abs(dq) * p < 1.0:
                continue
            fee = abs(dq) * p * bps / 10000.0
            led["cash"] -= dq * p + fee
            turn += abs(dq) * p
            q2 = cur + dq
            if abs(q2) < 1e-9:
                led["qty"].pop(t, None)
            else:
                led["qty"][t] = q2
        led["turnover"] += turn
        led["_day_turn"] = led.get("_day_turn", 0.0) + (turn / nav if nav > 0 else 0.0)
        return turn

    def _MsReduceOnly(self, led, scale_map, px):
        nav = self._MsNav(led, px)
        if nav <= 0:
            return 0.0
        turn = 0.0
        for t, mult in scale_map.items():
            q = led["qty"].get(t)
            if not q:
                continue
            p = px.get(t)
            if not p or p <= 0:
                continue
            newq = q * max(0.0, min(1.0, mult))
            dq = newq - q
            led["cash"] -= dq * p
            turn += abs(dq) * p
            if abs(newq) < 1e-9:
                led["qty"].pop(t, None)
            else:
                led["qty"][t] = newq
        led["turnover"] += turn
        led["_day_turn"] = led.get("_day_turn", 0.0) + (turn / nav if nav > 0 else 0.0)
        return turn

    def _MsMark(self, led, do, px):
        nav = self._MsNav(led, px)
        prev = led.get("_prev")
        if prev is not None and prev > 0:
            led["rets"].append(nav / prev - 1.0)
            led["dates"].append(_date.fromordinal(do))
            led.setdefault("turns", []).append(led.get("_day_turn", 0.0))
        led["_day_turn"] = 0.0
        led["_prev"] = nav
        if nav > led["peak"]:
            led["peak"] = nav
        led["maxdd"] = max(led["maxdd"], 1.0 - nav / max(led["peak"], 1e-9))

    def _MsPrecomputeConfirm(self, clf_idx, persist_n):
        out = {}
        for do in self._ms_days_sorted:
            rec = self._ms_idx[do]
            pre = rec.get("pre")
            pre_state = _STATES[pre[clf_idx]] if pre else "NORMAL"
            confirmed, run, cur = None, 0, None
            for _, states in (rec.get("post") or []):
                st = _STATES[states[clf_idx]]
                is_str = st not in ("NORMAL", "UNCONFIRMED_NOISE")
                if is_str and st == cur:
                    run += 1
                elif is_str:
                    cur, run = st, 1
                else:
                    cur, run = None, 0
                if is_str and run >= persist_n:
                    confirmed = st
            out[do] = (pre_state, confirmed)
        return out

    def _MsSimulate(self, clf_idx, router, persist_n, timing):
        cache = getattr(self, "_ms_confirm_cache", None)
        if cache is None:
            cache = {}
            self._ms_confirm_cache = cache
        key = (clf_idx, persist_n)
        pc = cache.get(key)
        if pc is None:
            pc = self._MsPrecomputeConfirm(clf_idx, persist_n)
            cache[key] = pc
        cash0 = float(getattr(self, "_sr_cash0", 10000.0) or 10000.0)
        led = {"cash": cash0, "qty": {}, "peak": cash0, "maxdd": 0.0,
               "rets": [], "dates": [], "turnover": 0.0, "false_broad": 0, "missed_sys": 0}
        bps = float(getattr(self, "_ms_cost_bps", 0.0) or 0.0)
        labels = getattr(self, "_ms_labels", {}) or {}
        prev_px = None
        dur_syms = self._ms_roles.get("DUR", ())
        gold = self._ms_gold
        for do in self._ms_days_sorted:
            rec = self._ms_idx[do]
            px = rec.get("px")
            if not px:
                continue
            dur_stressed = gold_stressed = False
            if prev_px:
                drs = [px[t] / prev_px[t] - 1.0 for t in dur_syms
                       if t in px and t in prev_px and prev_px[t] > 0]
                if drs and (sum(drs) / len(drs)) <= -0.003:
                    dur_stressed = True
                gp, gpp = px.get(gold), prev_px.get(gold)
                if gp and gpp and gpp > 0 and (gp / gpp - 1.0) <= -0.005:
                    gold_stressed = True
            pre_state, confirmed = pc[do]
            true_lab = labels.get(do)
            broad_hit = (pre_state == "BROAD_EQUITY_STRESS" and timing in ("T1", "T3")) or \
                        (confirmed == "BROAD_EQUITY_STRESS" and timing in ("T2", "T3"))
            if broad_hit and true_lab != "BROAD_EQUITY_STRESS" and router[_RM_BROAD] < 0.999:
                led["false_broad"] += 1
            sys_hit = (pre_state == "SYSTEMIC_LIQUIDITY_STRESS" and timing in ("T1", "T3")) or \
                      (confirmed == "SYSTEMIC_LIQUIDITY_STRESS" and timing in ("T2", "T3"))
            if true_lab == "SYSTEMIC_LIQUIDITY_STRESS" and not sys_hit:
                led["missed_sys"] += 1
            cap = rec.get("cap")
            target, cut = None, set()
            if cap is not None:
                bw = dict(cap[0])
                if timing in ("T1", "T3"):
                    sm = self._MsScaleMap(pre_state, router, list(bw.keys()), dur_stressed, gold_stressed)
                    for t, mult in sm.items():
                        bw[t] = bw[t] * mult
                        cut.add(t)
                target = bw
            if target is not None:
                self._MsApplyWeights(led, target, px, bps)
            if timing in ("T2", "T3") and confirmed is not None:
                sm = self._MsScaleMap(confirmed, router, list(led["qty"].keys()), dur_stressed, gold_stressed)
                sm = {t: m for t, m in sm.items() if t not in cut}
                if sm:
                    self._MsReduceOnly(led, sm, px)
            self._MsMark(led, do, px)
            prev_px = px
        return led

    def _MsMetrics(self, rets):
        n = len(rets)
        if n <= 0:
            return None
        nav = peak = 1.0
        maxdd = 0.0
        uw = uw_max = uw_days = 0
        sr = sr2 = 0.0
        for r in rets:
            sr += r
            sr2 += r * r
            nav = max(1e-8, nav * (1.0 + r))
            if nav > peak:
                peak, uw = nav, 0
            else:
                uw += 1
                uw_days += 1
                uw_max = max(uw_max, uw)
            maxdd = max(maxdd, 1.0 - nav / max(peak, 1e-9))
        mean = sr / n
        vol = (max(0.0, sr2 / n - mean * mean) ** 0.5) * (252 ** 0.5)
        years = n / 252.0
        cagr = (nav ** (1.0 / years) - 1.0) if years > 0.01 else None
        sharpe = (cagr / vol) if (cagr is not None and vol > 1e-12) else None
        arr = sorted(rets)
        k = max(1, int(0.05 * n + 0.999))
        return {"n": n, "end_nav": nav, "CAGR": cagr, "MaxDD": maxdd, "annual_stddev": vol,
                "Sharpe": sharpe, "worst_5pct_day_mean": sum(arr[:k]) / k,
                "recovery_days_max": uw_max,
                "time_under_water_pct": (uw_days / n) if n else None}

    def _MsWindow(self, dates, rets, s, e):
        xs = [r for d, r in zip(dates, rets) if (s is None or d >= s) and (e is None or d <= e)]
        return self._MsMetrics(xs)

    def _MsCostAdj(self, led, extra_bps):
        rets = led.get("rets") or []
        turns = led.get("turns") or [0.0] * len(rets)
        if len(turns) != len(rets):
            turns = (list(turns) + [0.0] * len(rets))[:len(rets)]
        adj = [r - (extra_bps / 10000.0) * t for r, t in zip(rets, turns)]
        return self._MsMetrics(adj) or {}

    def _MsLog(self, msg):
        try:
            n = len(msg) + 1
            # Macro A1 owns a separate EOA/artifact budget; do not starve it with P1 lines.
            if (str(msg).startswith("CG_MACRO_A1_") or str(msg).startswith("CG_MACRO_RESID_B1_")
                    or str(msg).startswith("CG_MACRO_RESID_B11_")
                    or str(msg).startswith("CG_MACRO_A1_CLOSEOUT") or str(msg).startswith("CG_MAISR_CLOSEOUT")
                    or str(msg).startswith("D0_COMPACT_CLOSEOUT") or str(msg).startswith("CG_DAMAGE_")):
                self.log(msg)
                return
            # P1 console budget target <45 KB; leave headroom for FINAL lines.
            if self._ms_log_used + n > 44000:
                return
            self.log(msg)
            self._ms_log_used += n
        except Exception:
            pass

    def _MsBid(self):
        return str(getattr(self, "algorithm_id", None) or getattr(self, "AlgorithmId", None) or "NA")

    def _MsNoRec(self, reason):
        self._MsLog(
            "CG_MAISR_P1_RECOMMENDATION,apply=NO,policy=KEEP_CURRENT_SH,classifier=NA,router=NA,"
            "persistence=NA,timing=NA,SH_mode=NA,CAGR=NA,MaxDD=NA,StdDev=NA,OOS_Sharpe=NA,"
            "CRISIS_MaxDD=NA,Y2020_MaxDD=NA,Y2022_MaxDD=NA,worst5=NA,recovery=NA,turnover=NA,"
            f"false_broad_exits=NA,missed_systemic=NA,CAGR_2bps=NA,MaxDD_2bps=NA,neighbor_stable=NO,"
            f"reason={reason}"
        )

    def CgMaisrOnEndOfAlgorithm(self, parity_ok) -> None:
        if getattr(self, "_ms_emitted", False):
            return
        self._ms_emitted = True
        if not getattr(self, "_ms_on", False):
            return
        try:
            self._MsLog(
                f"CG_MAISR_P1_BAR_DEDUP_FINAL,tradebars_seen={self._ms_bd_seen},"
                f"economic_tradebars_accepted={self._ms_bd_accept},"
                f"duplicate_tradebars_blocked={self._ms_bd_dup},"
                f"same_timestamp_conflict_count={self._ms_bd_conflict},"
                f"out_of_order_bar_count={self._ms_bd_oo},"
                f"evaluation_callbacks_seen={self._ms_eval_seen},"
                f"unique_evaluations_executed={self._ms_eval_uniq},"
                f"duplicate_evaluations_blocked={self._ms_eval_dup}"
            )
            if not parity_ok:
                self._MsLog("CG_MAISR_D0_VALIDATION_FINAL,parity=FAIL,policies_evaluated=0,"
                            "next=FIX_MAISR_PARITY")
                self._MsNoRec("shadow_replay_parity_failed")
                self._MsLog("CG_MAISR_P1_GATE_FINAL,full_grid_authorized=NO,policies_evaluated=0,"
                            "reason=shadow_replay_parity_failed")
                return

            if self.CgDamageD01TryEOA(parity_ok):
                return
            if getattr(self, "cg_macro_resid_b1_enable", False):
                try:
                    if self.CgMacroResidB1OnEndOfAlgorithm(parity_ok):
                        return
                except Exception as e:
                    self._ms_err += 1
                    try:
                        self._MsLog(f"CG_MACRO_RESID_B1_RECOMMENDATION,result=FAILED,reason=EOA_EXCEPTION:{type(e).__name__}:{e},research_conclusion=NOT_REACHED,next=FIX_MACRO_RESID_B1_IMPLEMENTATION")
                    except Exception:
                        pass
                return
            if getattr(self, "cg_macro_a1_enable", False):
                try:
                    if self.CgMacroA1OnEndOfAlgorithm(parity_ok):
                        return
                except Exception as e:
                    self._ms_err += 1
                    try:
                        self._MsLog(f"CG_MACRO_A1_RECOMMENDATION,result=FAILED,reason=EOA_EXCEPTION:{type(e).__name__}:{e},research_conclusion=NOT_REACHED,next=FIX_MACRO_A1_IMPLEMENTATION")
                    except Exception:
                        pass
                return
            if getattr(self, "cg_maisr_d4_enable", False):
                try:
                    self.CgMaisrD4OnEndOfAlgorithm(parity_ok)
                except Exception:
                    self._ms_err += 1
                return
            try:
                if self.CgMaisrD3OnEndOfAlgorithm(parity_ok):
                    return
            except Exception:
                self._ms_err += 1
            try:
                if self.CgMaisrD3EconomicGate(parity_ok):
                    return
            except Exception:
                self._ms_err += 1
            if not getattr(self, "cg_maisr_final_d3_enable", False):
                try:
                    if self.CgMaisrD2OnEndOfAlgorithm(parity_ok):
                        return
                except Exception:
                    self._ms_err += 1
                try:
                    if self.CgMaisrD2EconomicGate(parity_ok):
                        return
                except Exception:
                    self._ms_err += 1
            elif getattr(self, "_d3_econ_ready", False) or getattr(self, "_d2_econ_ready", False):
                pass
            else:
                return

            id_results = self._MsIdentityFinals()
            all_id_pass = bool(id_results) and all(r.get("pass") for r in id_results.values())
            data_ok = (
                int(getattr(self, "_ms_bd_conflict", 0) or 0) == 0
                and int(getattr(self, "_ms_bd_oo", 0) or 0) == 0
            )
            all_id_pass = bool(all_id_pass and data_ok)
            canary_result = self._MsCanaryFinal()
            if not getattr(self, "cg_maisr_final_d3_enable", False):
                try:
                    self._MsExportP1Artifacts(id_results, canary_result)
                except Exception:
                    self._ms_err += 1

            if getattr(self, "cg_maisr_identity_only", False):
                self._MsLog(
                    f"CG_MAISR_P1_GATE_FINAL,full_grid_authorized={'YES' if all_id_pass else 'NO'},"
                    f"policies_evaluated=0,identity_only=1,"
                    f"same_timestamp_conflict_count={int(getattr(self,'_ms_bd_conflict',0) or 0)},"
                    f"out_of_order_bar_count={int(getattr(self,'_ms_bd_oo',0) or 0)},"
                    f"data_integrity={'PASS' if data_ok else 'FAIL'}"
                )
                return

            if not all_id_pass:
                self._MsLog(
                    "CG_MAISR_P1_GATE_FINAL,full_grid_authorized=NO,policies_evaluated=0,"
                    "identity_only=0,reason=identity_check_failed"
                )
                self._MsNoRec("identity_check_failed")
                return

            self._MsLog(
                f"CG_MAISR_P1_IDENTITY_RECHECK,pass={'YES' if all_id_pass else 'NO'},"
                f"replay={'YES' if (id_results.get('MAISR_REPLAY_IDENTITY') or {}).get('pass') else 'NO'},"
                f"pipeline_off={'YES' if (id_results.get('MAISR_PIPELINE_OFF_IDENTITY') or {}).get('pass') else 'NO'},"
                f"sensor_no_action={'YES' if (id_results.get('MAISR_SENSOR_NO_ACTION_IDENTITY') or {}).get('pass') else 'NO'}"
            )
            self._MsLog("CG_MAISR_P1_FULL_INIT,identity_pass=YES,grid_enable=1")
            self._MsBuildIndex()
            if not self._ms_days_sorted:
                self._MsLog("CG_MAISR_P1_VALIDATION_FINAL,parity=PASS,policies_evaluated=0,"
                            "next=NO_DATA")
                return
            self._MsBuildLabels()
            scored = self._MsScoreConfigs()
            if getattr(self, "_d2_econ_ready", False) and getattr(self, "_d2_frozen_scored", None):
                chosen = list(self._d2_frozen_scored)
                self._ms_selected_ids = [r["id"] for r in chosen]
                self._ms_selected_modes = {r["h"] for r in chosen}
            else:
                chosen = self._MsSelectClassifiers(scored)
            if not chosen or not self._ms_grid_on:
                self._MsNoRec("grid_disabled_or_no_selection")
                return

            metas = self._MsBuildPolicies(chosen)
            ctrl_dates = list(getattr(self, "_sr_dates", []) or [])
            ctrl_rets = list(getattr(self, "_sr_actual_rets", []) or [])
            ctrl_m = self._MsMetrics(ctrl_rets) or {}
            ctrl_turnover = 0.0
            try:
                ctrl_turnover = float((getattr(self, "_sr_ctrl", None) or {}).get("turnover") or 0.0)
            except Exception:
                ctrl_turnover = 0.0

            # Identity from fill-replay ledgers (not _MsSimulate reconstruction).
            replay_cmp = id_results.get("MAISR_REPLAY_IDENTITY", {}) or {}
            nav_d = replay_cmp.get("nav_d")
            dd_d = replay_cmp.get("dd_d")
            identity_ok = bool(replay_cmp.get("pass")) and all_id_pass

            today = self.time.date()
            live_s = ctrl_dates[max(0, len(ctrl_dates) - 252)] if ctrl_dates else None
            windows = [
                ("RUN", _date(2012, 1, 1), today),
                ("TRAIN_2012_2018", _date(2012, 1, 1), _date(2018, 12, 31)),
                ("OOS_2019_2021", _date(2019, 1, 1), _date(2021, 12, 31)),
                ("CRISIS_2022_2025", _date(2022, 1, 1), _date(2025, 12, 31)),
                ("Y2020", _date(2020, 1, 1), _date(2020, 12, 31)),
                ("Y2022", _date(2022, 1, 1), _date(2022, 12, 31)),
                ("LIVE_RECENT", live_s, today),
            ]
            c_oos = self._MsWindow(ctrl_dates, ctrl_rets, _date(2019, 1, 1), _date(2021, 12, 31)) or {}
            c_cri = self._MsWindow(ctrl_dates, ctrl_rets, _date(2022, 1, 1), _date(2025, 12, 31)) or {}
            c_y20 = self._MsWindow(ctrl_dates, ctrl_rets, _date(2020, 1, 1), _date(2020, 12, 31)) or {}
            c_y22 = self._MsWindow(ctrl_dates, ctrl_rets, _date(2022, 1, 1), _date(2022, 12, 31)) or {}

            rows = []
            for meta in metas:
                router = _ROUTERS[meta["router"]]
                pn = _PERSIST[meta["persist"]]
                led = self._MsSimulate(meta["clf_idx"], router, pn, meta["timing"])
                m = self._MsMetrics(led["rets"]) or {}
                wins = {}
                invalid = 0
                for name, s, e in windows:
                    if s is None:
                        invalid = 1
                        continue
                    wm = self._MsWindow(led["dates"], led["rets"], s, e)
                    wins[name] = wm
                    if name in ("RUN", "TRAIN_2012_2018", "OOS_2019_2021", "CRISIS_2022_2025") \
                            and (not wm or wm.get("n", 0) <= 0):
                        invalid = 1
                oos = wins.get("OOS_2019_2021") or {}
                cri = wins.get("CRISIS_2022_2025") or {}
                y20 = wins.get("Y2020") or {}
                y22 = wins.get("Y2022") or {}
                cm2 = self._MsCostAdj(led, 2.0)
                cm5 = self._MsCostAdj(led, 5.0)
                row = dict(m)
                row.update(meta)
                row["oos_sharpe"] = oos.get("Sharpe")
                row["crisis_maxdd"] = cri.get("MaxDD")
                row["y2020_maxdd"] = y20.get("MaxDD")
                row["y2022_maxdd"] = y22.get("MaxDD")
                row["w5_abs"] = -float(m.get("worst_5pct_day_mean") or 0)
                row["risk_efficiency"] = ((m.get("CAGR") or 0) / m["MaxDD"]) if m.get("MaxDD") else None
                row["turnover"] = led.get("turnover", 0.0)
                row["false_broad"] = led.get("false_broad", 0)
                row["missed_sys"] = led.get("missed_sys", 0)
                row["CAGR_cost2"] = cm2.get("CAGR")
                row["MaxDD_cost2"] = cm2.get("MaxDD")
                row["CAGR_cost5"] = cm5.get("CAGR")
                row["MaxDD_cost5"] = cm5.get("MaxDD")
                row["invalid"] = invalid
                row["_led"] = led
                rows.append(row)

            by_key = {(r["clf_id"], r["router"], r["persist"], r["timing"]): r for r in rows}
            for r in rows:
                stable = True
                for dim, order, pos in (("router", _RORD, 1), ("persist", _PORD, 2)):
                    cur = order[r[dim]]
                    for delta in (-1, 1):
                        nb = cur + delta
                        nb_name = next((kk for kk, vv in order.items() if vv == nb), None)
                        if nb_name is None:
                            continue
                        key = [r["clf_id"], r["router"], r["persist"], r["timing"]]
                        key[pos] = nb_name
                        o = by_key.get(tuple(key))
                        if o and not o["invalid"]:
                            if (o.get("MaxDD") or 0) > (r.get("MaxDD") or 0) + 0.02:
                                stable = False
                            oc, rc = o.get("CAGR") or 0, r.get("CAGR") or 0
                            if rc > 0 and oc < 0.8 * rc:
                                stable = False
                r["neighbor_stable"] = int(stable)

            for r in rows:
                strict = (
                    not r["invalid"]
                    and (r.get("CAGR") or -9) > (ctrl_m.get("CAGR") or 0)
                    and (r.get("MaxDD") or 9) <= (ctrl_m.get("MaxDD") or 9)
                    and (r.get("worst_5pct_day_mean") or -9) >= (ctrl_m.get("worst_5pct_day_mean") or -9)
                    and (r.get("recovery_days_max") if r.get("recovery_days_max") is not None else 1e9)
                    <= (ctrl_m.get("recovery_days_max") if ctrl_m.get("recovery_days_max") is not None else 1e9)
                    and (r.get("oos_sharpe") or -9) >= 0.95 * (c_oos.get("Sharpe") or 0)
                    and (r.get("crisis_maxdd") or 9) <= (c_cri.get("MaxDD") or 0) + 0.01
                    and (r.get("y2020_maxdd") or 9) <= (c_y20.get("MaxDD") or 0) + 0.01
                    and (r.get("y2022_maxdd") or 9) <= (c_y22.get("MaxDD") or 0) + 0.01
                    and (r.get("annual_stddev") or 9) <= 0.18
                    and (ctrl_turnover <= 0 or (r.get("turnover") or 0.0) <= 1.15 * ctrl_turnover)
                    and bool(r.get("neighbor_stable"))
                    and r.get("CAGR_cost2") is not None and r["CAGR_cost2"] > (ctrl_m.get("CAGR") or 0)
                    and r.get("MaxDD_cost2") is not None and r["MaxDD_cost2"] <= (ctrl_m.get("MaxDD") or 9)
                )
                r["STRICT_PASS"] = int(bool(strict))

            valid_rows = [r for r in rows if not r["invalid"]]
            ranked = sorted(valid_rows, key=lambda r: (
                r.get("MaxDD") or 9, r.get("w5_abs") or 9, -(r.get("oos_sharpe") or -9),
                r.get("crisis_maxdd") or 9, -(r.get("CAGR") or -9)))
            strict_rows = [r for r in ranked if r["STRICT_PASS"]]
            top15 = ranked[:15]

            gate_ok = identity_ok and (self._ms_err == 0)
            best = strict_rows[0] if (gate_ok and strict_rows) else None
            if not gate_ok:
                reason = "identity_check_failed_or_errors"
            elif best is not None:
                reason = "strict_pass_found_stable"
            else:
                reason = "no_strict_pass"

            for i, r in enumerate(top15):
                if getattr(self, "cg_maisr_final_d3_enable", False):
                    break
                self._MsLog(
                    f"CG_MAISR_P1_TOP,rank={i+1},id={r['id']},clf={r['clf_id']},"
                    f"router={r['router']},persist={r['persist']},timing={r['timing']},"
                    f"CAGR={_f(r.get('CAGR'))},MaxDD={_f(r.get('MaxDD'))},"
                    f"std={_f(r.get('annual_stddev'))},Sharpe={_f(r.get('Sharpe'))},"
                    f"w5={_f(r.get('worst_5pct_day_mean'))},oos_sh={_f(r.get('oos_sharpe'))},"
                    f"crisis_dd={_f(r.get('crisis_maxdd'))},risk_eff={_f(r.get('risk_efficiency'))},"
                    f"STRICT_PASS={r['STRICT_PASS']},stable={r.get('neighbor_stable',0)},"
                    f"cagr_cost2={_f(r.get('CAGR_cost2'))},cagr_cost5={_f(r.get('CAGR_cost5'))}"
                )

            clf_key = self._MsWriteP1ClassifiersCsv(scored)
            attrib_key = self._MsWriteP1AttributionCsv(scored, chosen, rows)
            csv_key = self._MsWriteP1PoliciesCsv(rows, ctrl_m)

            local_err = sum(int(r.get("loc_to_broad_n") or 0) for r in chosen)
            local_den = sum(int(r.get("true_local") or 0) for r in chosen)
            broad_den = sum(int(r.get("true_broad") or 0) for r in chosen)
            local_broad_sep = None
            if local_den > 0 and broad_den > 0:
                local_broad_sep = 1.0 - (local_err / max(local_den, 1))
            h_scores = {}
            for r in chosen:
                h_scores.setdefault(r["h"], []).append(r["score"])
            sh_incr = None
            if h_scores.get("H2") and h_scores.get("H0"):
                sh_incr = (sum(h_scores["H2"]) / len(h_scores["H2"])) - \
                          (sum(h_scores["H0"]) / len(h_scores["H0"]))

            if getattr(self, "cg_maisr_final_d3_enable", False):
                try:
                    self._D3EmitEconFinals(
                        top15, best, rows, ctrl_m, c_oos, c_cri, identity_ok,
                        reason, strict_rows, gate_ok)
                except Exception:
                    self._ms_err += 1
            else:
                self._MsLog(
                    f"CG_MAISR_P1_VALIDATION_FINAL,parity=PASS,identity_recheck={'PASS' if identity_ok else 'FAIL'},"
                    f"configs_scored=54,classifiers_selected={len(chosen)},policies_defined={len(metas)},"
                    f"policies_evaluated={len(rows)},strict_pass_count={len(strict_rows)},"
                    f"local_vs_broad_separation={_f(local_broad_sep,4)},"
                    f"local_vs_broad_true_local_count={local_den},"
                    f"local_vs_broad_true_broad_count={broad_den},"
                    f"local_vs_broad_error_count={local_err},"
                    f"SH_incremental_value={_f(sh_incr,4)},"
                    f"policies_csv={csv_key},attribution_csv={attrib_key},classifiers_csv={clf_key},"
                    f"runtime_errors={self._ms_err}"
                )
                self._MsLog(
                    f"CG_MAISR_P1_GATE_FINAL,full_grid_authorized={'YES' if gate_ok else 'NO'},"
                    f"policies_evaluated={len(rows)},identity_only=0,"
                    f"identity_recheck={'PASS' if identity_ok else 'FAIL'}"
                )
                if best is not None:
                    self._MsLog(
                        f"CG_MAISR_P1_RECOMMENDATION,apply=YES,policy={best['id']},"
                        f"classifier={best['clf_id']},router={best['router']},persistence={best['persist']},"
                        f"timing={best['timing']},SH_mode={best.get('h','NA')},"
                        f"CAGR={_f(best.get('CAGR'))},MaxDD={_f(best.get('MaxDD'))},"
                        f"StdDev={_f(best.get('annual_stddev'))},OOS_Sharpe={_f(best.get('oos_sharpe'))},"
                        f"CRISIS_MaxDD={_f(best.get('crisis_maxdd'))},Y2020_MaxDD={_f(best.get('y2020_maxdd'))},"
                        f"Y2022_MaxDD={_f(best.get('y2022_maxdd'))},worst5={_f(best.get('worst_5pct_day_mean'))},"
                        f"recovery={best.get('recovery_days_max','NA')},turnover={_f(best.get('turnover'))},"
                        f"false_broad_exits={best.get('false_broad',0)},missed_systemic={best.get('missed_sys',0)},"
                        f"CAGR_2bps={_f(best.get('CAGR_cost2'))},MaxDD_2bps={_f(best.get('MaxDD_cost2'))},"
                        f"neighbor_stable=YES,reason={reason}"
                    )
                else:
                    self._MsNoRec(reason)
        except Exception as exc:
            self._ms_err += 1
            try:
                self.log(f"CG_MAISR_D0_VALIDATION_FINAL,emit_error={type(exc).__name__}:{exc}")
            except Exception:
                pass
