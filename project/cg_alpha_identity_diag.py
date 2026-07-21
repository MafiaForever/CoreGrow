# cg_alpha_identity_diag.py -- CG-ALPHA-IDENTITY-AUDIT-D0 mixin (diagnostic only).
from AlgorithmImports import *
from datetime import datetime
from cg_alpha_identity_core import (
    AlphaIdentityEngine, EXPERIMENT, PHASE, ASSET_MAP, SCHEMA,
    run_alpha_identity_static_tests, RESEARCH_END, MUTE_PREFIXES_WHEN_ALPHA,
    DIAG_LOG_BUDGET_BYTES, estimate_diag_log_bytes, decode_alpha_transport_parts,
)
import json


class CgAlphaIdentityDiagMixin:
    """Passive intended-target ledger + counterfactual shadows. No orders."""

    def _AlphaIdentityMaybeEnableMs(self):
        # Flag may be bootstrapped before MaisrInit; else read here for MS enable.
        if not hasattr(self, "cg_alpha_identity_enable"):
            try:
                self._AlphaIdentityBootstrapParams()
            except Exception:
                self.cg_alpha_identity_enable = False
        if getattr(self, "cg_alpha_identity_enable", False):
            self._ms_on = True
            self.cg_maisr_diag_enable = True

    def _AlphaIdentityBootstrapParams(self):
        ov = getattr(self, "_rrx_param_overrides", {}) or {}

        def _p(k, d=""):
            v = self.get_parameter(k)
            if v is None or str(v).strip() == "":
                v = ov.get(k, d)
            return v

        def _bool(k, d="0"):
            return str(_p(k, d) or d).strip().lower() in ("1", "true", "yes", "on")

        self.cg_alpha_identity_enable = _bool("cg_alpha_identity_enable", "0")

    def _AlphaIdentityReadParams(self, _p, _bool):
        self.cg_alpha_identity_enable = _bool("cg_alpha_identity_enable", "0")

    def AlphaIdentityBootstrap(self):
        """Call before CgMaisrInit so enable flag + engine exist."""
        try:
            self._AlphaIdentityBootstrapParams()
            self._AlphaIdentityInitHooks()
        except Exception:
            self.cg_alpha_identity_enable = False
            self._alpha_on = False

    def _AlphaIdentityInitHooksSafe(self):
        try:
            self._AlphaIdentityInitHooks()
        except Exception:
            self._ms_err = int(getattr(self, "_ms_err", 0) or 0) + 1

    def _AlphaIdentityInitHooks(self):
        on = bool(getattr(self, "cg_alpha_identity_enable", False))
        self._alpha_on = on
        self._alpha_eng = AlphaIdentityEngine()
        self._alpha_eng.set_enabled(on)
        self._alpha_err = 0
        self._alpha_eoa_emitted = False
        self._alpha_nav_day = None
        lp = list(getattr(self, "log_only_prefixes", None) or [])
        for pref in ("CG_ALPHA_ID_", "CG_ALPHA_IDENTITY_"):
            if pref not in lp:
                lp.append(pref)
        # Strip regime-time allowlist reinjection so mute can suppress floods.
        lp = [p for p in lp if not str(p).startswith("CG_REGIME_TIME")]
        self.log_only_prefixes = lp
        if on:
            mp = list(getattr(self, "log_mute_prefixes", None) or [])
            for pref in MUTE_PREFIXES_WHEN_ALPHA:
                if pref not in mp:
                    mp.append(pref)
            # Mute verbose Maisr P1 noise while alpha owns the diagnostic budget.
            for pref in ("CG_MAISR_P1_", "CG_MAISR_D2_", "CG_W2_TRADE"):
                if pref not in mp:
                    mp.append(pref)
            self.log_mute_prefixes = mp
        if not on:
            self.log("CG_ALPHA_ID_INIT,enable=0,diagnostic_real_order_count=0")
            return
        self.log(
            f"CG_ALPHA_ID_INIT,enable=1,experiment={EXPERIMENT},phase={PHASE},"
            f"schema={SCHEMA},e_equals_b=1,cost_bps=0,lag_min=0,budget={DIAG_LOG_BUDGET_BYTES}"
        )

    def _AlphaIdentityCashTk(self):
        try:
            cs = getattr(self, "sym_cash", None)
            if cs is None:
                return "BIL"
            return str(cs.Value).upper() if hasattr(cs, "Value") else str(cs).upper()
        except Exception:
            return "BIL"

    def _AlphaIdentityFlags(self):
        return {
            "w2": bool(getattr(self, "_cg_w2_last_active", False)),
            "ids": getattr(self, "_ids_state", None),
            "panic": getattr(self, "_panic_state", None),
            "sh": bool(getattr(self, "_sh_active", False))
            or bool(getattr(self, "sh_hedge_active", False)),
        }

    def _AlphaIdentityFeatureCutoff(self, decision_time):
        # Completed-bar features: decision uses indicators ready at DecisionTime;
        # FeatureCutoff == DecisionTime (no future bars).
        return decision_time

    def AlphaIdentityObserveProtectionPair(self, pre_targets, post_targets):
        if not getattr(self, "_alpha_on", False):
            return
        eng = getattr(self, "_alpha_eng", None)
        if eng is None:
            return
        try:
            eng.observe_protection_pair(
                getattr(self, "time", None), pre_targets, post_targets,
                cash_tk=self._AlphaIdentityCashTk(),
            )
        except Exception:
            self._alpha_err = int(getattr(self, "_alpha_err", 0) or 0) + 1

    def AlphaIdentityObserveCapture(self, targets, slot_minutes=165):
        if not getattr(self, "_alpha_on", False):
            return
        eng = getattr(self, "_alpha_eng", None)
        if eng is None or not isinstance(targets, dict):
            return
        try:
            dt = getattr(self, "time", None)
            eid = None
            try:
                led = getattr(self, "_dmg_ledger", None)
                cur = led.current_open() if led is not None else None
                if cur is not None:
                    eid = getattr(cur, "episode_id", None) or (cur.get("episode_id") if isinstance(cur, dict) else None)
            except Exception:
                eid = None
            eng.observe_capture(
                dt, targets, slot_minutes=int(slot_minutes or 165),
                feature_cutoff=self._AlphaIdentityFeatureCutoff(dt),
                flags=self._AlphaIdentityFlags(),
                episode_id=eid,
                cash_tk=self._AlphaIdentityCashTk(),
                source="CgRegimeRebalTimeTradeCapture",
            )
        except Exception:
            self._alpha_err = int(getattr(self, "_alpha_err", 0) or 0) + 1

    def _AlphaIdentityOnAcceptedBar(self, tk, et, o, h, l, c):
        if not getattr(self, "_alpha_on", False):
            return
        eng = getattr(self, "_alpha_eng", None)
        if eng is None:
            return
        try:
            eng.on_bar(tk, et, c)
        except Exception:
            self._alpha_err = int(getattr(self, "_alpha_err", 0) or 0) + 1

    def AlphaIdentityOnSliceBars(self, bars):
        """Feed SPY/QQQ/BIL/selected equities including tickers outside Maisr panel."""
        if not getattr(self, "_alpha_on", False) or bars is None:
            return
        eng = getattr(self, "_alpha_eng", None)
        if eng is None:
            return
        want = frozenset({
            "SPY", "QQQ", "BIL", "SPYG", "SMH", "XLE", "XLB", "XLV", "XLU",
            "DBC", "MU", "NVDA", "AVGO", "GLD", "GLDM", "BND", "TIP", "SH",
            "SGOV", "USFR", "TFLO",
        })
        try:
            for kv in bars:
                try:
                    bar = kv.Value if hasattr(kv, "Value") else bars[kv]
                    sym = kv.Key if hasattr(kv, "Key") else kv
                    tk = str(sym.Value).upper() if hasattr(sym, "Value") else str(sym).upper()
                    if tk not in want:
                        continue
                    et = getattr(bar, "EndTime", None) or getattr(bar, "end_time", None)
                    c = float(bar.Close if hasattr(bar, "Close") else bar.close)
                    o = float(bar.Open if hasattr(bar, "Open") else bar.open)
                    h = float(bar.High if hasattr(bar, "High") else bar.high)
                    l = float(bar.Low if hasattr(bar, "Low") else bar.low)
                    eng.on_bar(tk, et, c)
                except Exception:
                    continue
        except Exception:
            self._alpha_err = int(getattr(self, "_alpha_err", 0) or 0) + 1

    def AlphaIdentityMaybeMarkNav(self):
        if not getattr(self, "_alpha_on", False):
            return
        eng = getattr(self, "_alpha_eng", None)
        if eng is None:
            return
        try:
            t = getattr(self, "time", None)
            if t is None:
                return
            d = t.date() if hasattr(t, "date") else t
            # Poll QQQ/BIL last prices for shadow paths (no new subscriptions).
            for tk, attr in (("QQQ", "rr_qqq"), ("BIL", "sym_cash")):
                try:
                    sym = getattr(self, attr, None)
                    if sym is None:
                        continue
                    px = float(self.securities[sym].price)
                    if px > 0:
                        eng.on_bar(tk if tk != "BIL" else "BIL", t, px)
                except Exception:
                    continue
            if self._alpha_nav_day == d:
                return
            tod = t.hour * 60 + t.minute if isinstance(t, datetime) else 0
            if tod < 955:
                return
            if d > RESEARCH_END:
                return
            nav = float(self.portfolio.total_portfolio_value)
            eng.observe_nav(t, nav)
            self._alpha_nav_day = d
        except Exception:
            self._alpha_err = int(getattr(self, "_alpha_err", 0) or 0) + 1

    def CgAlphaIdentityTryEOA(self, parity_ok=True):
        if not getattr(self, "cg_alpha_identity_enable", False):
            return False
        if getattr(self, "_alpha_eoa_emitted", False):
            return True
        try:
            self.CgAlphaIdentityOnEndOfAlgorithm(parity_ok)
        except Exception as e:
            try:
                self.log(f"CG_ALPHA_ID_EOA_FAIL,err={type(e).__name__}")
            except Exception:
                pass
        self._alpha_eoa_emitted = True
        return True

    def CgAlphaIdentityOnEndOfAlgorithm(self, parity_ok=True):
        eng = getattr(self, "_alpha_eng", None)
        if eng is None:
            self.log("CG_ALPHA_ID_CLOSEOUT,status=NO_ENGINE")
            return
        try:
            tr = eng.build_transport_pack()
        except Exception as e:
            self.log(f"CG_ALPHA_ID_CLOSEOUT,status=PACK_FAIL,err={type(e).__name__}")
            return
        snap = tr.get("snap") or {}
        ctr = snap.get("counters") or {}
        est = estimate_diag_log_bytes(tr["b64_bytes"], tr["part_count"])
        # One pre-closeout technical counter line (required).
        self.log(
            f"CG_ALPHA_ID_COUNTERS,captures={ctr.get('captures')},ledger={tr.get('ledger_count')},"
            f"vectors={tr.get('vector_count')},spy_bars={ctr.get('spy_bars')},"
            f"qqq_bars={ctr.get('qqq_bars')},day_marks={ctr.get('day_marks')},"
            f"nav_marks={ctr.get('nav_marks')},ooo={ctr.get('out_of_order')},"
            f"same_bar={ctr.get('same_bar_blocked')},err={getattr(self,'_alpha_err',0)},"
            f"b64={tr.get('b64_bytes')},parts={tr.get('part_count')},est={est}"
        )
        if est >= DIAG_LOG_BUDGET_BYTES:
            self.log(
                f"CG_ALPHA_ID_CLOSEOUT,status=BUDGET_EXCEEDED,est={est},"
                f"budget={DIAG_LOG_BUDGET_BYTES},ledger={tr.get('ledger_count')},"
                f"digest={tr.get('digest')}"
            )
            self._alpha_closeout_snap = snap
            return
        # Controlled PART protocol (same pattern as D0_COMPACT_CLOSEOUT_PART).
        pc = int(tr["part_count"])
        digest = tr["digest"]
        for i, part in enumerate(tr["parts"], 1):
            self.log(
                f"CG_ALPHA_ID_CLOSEOUT_PART,run={digest},i={i},n={pc},"
                f"b64={part}"
            )
        pw = snap.get("pairwise") or {}
        conc = snap.get("single_year_concentration") or {}
        self.log(
            f"CG_ALPHA_ID_CLOSEOUT,status=OK,parity={int(bool(parity_ok))},"
            f"ledger={tr.get('ledger_count')},vectors={tr.get('vector_count')},"
            f"verdict={snap.get('verdict')},digest={digest},parts={pc},"
            f"b64={tr.get('b64_bytes')},est={est},"
            f"r_oos_spy={pw.get('residual_oos_vs_spy')},"
            f"r_oos_qqq={pw.get('residual_oos_vs_qqq')},"
            f"r_cr_spy={pw.get('residual_crisis_vs_spy')},"
            f"r_cr_qqq={pw.get('residual_crisis_vs_qqq')},"
            f"sel={pw.get('selection_effect')},trend={pw.get('simple_trend_check')},"
            f"top_year={conc.get('top_year')},dominated={int(bool(conc.get('dominated_by_one_year')))}"
        )
        self._alpha_closeout_snap = snap
        self._alpha_transport = tr
