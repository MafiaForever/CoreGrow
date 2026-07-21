# cg_alpha_identity_diag.py -- CG-ALPHA-IDENTITY-AUDIT-D0 mixin (diagnostic only).
from AlgorithmImports import *
from datetime import datetime
from cg_alpha_identity_core import (
    AlphaIdentityEngine, EXPERIMENT, PHASE, ASSET_MAP, SCHEMA,
    run_alpha_identity_static_tests, RESEARCH_END,
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
        self.log_only_prefixes = lp
        if not on:
            self.log("CG_ALPHA_ID_INIT,enable=0,diagnostic_real_order_count=0")
            return
        self.log(
            f"CG_ALPHA_ID_INIT,enable=1,experiment={EXPERIMENT},phase={PHASE},"
            f"schema={SCHEMA},e_equals_b=1,cost_bps=0,lag_min=0"
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
        snap = eng.finalize()
        # Compact multi-line export (log budget)
        ctr = snap.get("counters") or {}
        self.log(
            f"CG_ALPHA_ID_CLOSEOUT,status=OK,parity={int(bool(parity_ok))},"
            f"ledger={snap.get('ledger_count')},verdict={snap.get('verdict')},"
            f"captures={ctr.get('captures')},spy_bars={ctr.get('spy_bars')},"
            f"qqq_bars={ctr.get('qqq_bars')},day_marks={ctr.get('day_marks')},"
            f"nav_marks={ctr.get('nav_marks')},err={getattr(self,'_alpha_err',0)}"
        )
        pw = snap.get("pairwise") or {}
        self.log(
            f"CG_ALPHA_ID_PAIRWISE,"
            f"residual_vs_spy={pw.get('residual_vs_spy')},"
            f"residual_vs_qqq={pw.get('residual_vs_qqq')},"
            f"residual_oos_vs_spy={pw.get('residual_oos_vs_spy')},"
            f"residual_oos_vs_qqq={pw.get('residual_oos_vs_qqq')},"
            f"residual_crisis_vs_spy={pw.get('residual_crisis_vs_spy')},"
            f"residual_crisis_vs_qqq={pw.get('residual_crisis_vs_qqq')},"
            f"selection_effect={pw.get('selection_effect')},"
            f"simple_trend_check={pw.get('simple_trend_check')},"
            f"e_equals_b={int(bool(pw.get('e_equals_b')))}"
        )
        # Period residuals
        for pname, prow in (snap.get("periods") or {}).items():
            self.log(
                f"CG_ALPHA_ID_PERIOD,period={pname},"
                f"a_wf={prow.get('A_CG_FULL_wealth')},a_dd={prow.get('A_CG_FULL_maxdd')},"
                f"b_wf={prow.get('B_wealth')},c_wf={prow.get('C_wealth')},"
                f"d_wf={prow.get('D_wealth')},g_wf={prow.get('G_wealth')},"
                f"r_spy={prow.get('residual_vs_spy')},r_qqq={prow.get('residual_vs_qqq')}"
            )
        conc = snap.get("single_year_concentration") or {}
        self.log(
            f"CG_ALPHA_ID_VERDICT,verdict={snap.get('verdict')},"
            f"top_year={conc.get('top_year')},top_share={conc.get('top_share')},"
            f"dominated={int(bool(conc.get('dominated_by_one_year')))},"
            f"reason={str(snap.get('verdict_reason') or '')[:400]}"
        )
        # Compact JSON blob parts for post-cloud publish (chunked)
        payload = {
            "experiment": snap.get("experiment"),
            "phase": snap.get("phase"),
            "schema": snap.get("schema"),
            "asset_map": ASSET_MAP,
            "counters": ctr,
            "metrics": snap.get("metrics"),
            "pairwise": pw,
            "periods": snap.get("periods"),
            "years": snap.get("years"),
            "protection": {
                "valid": (snap.get("protection") or {}).get("valid"),
                "event_count": (snap.get("protection") or {}).get("event_count"),
                "mean_delta_signed": (snap.get("protection") or {}).get("mean_delta_signed"),
            },
            "verdict": snap.get("verdict"),
            "verdict_reason": snap.get("verdict_reason"),
            "single_year_concentration": conc,
            "ledger_count": snap.get("ledger_count"),
            "cost_bps": snap.get("cost_bps"),
            "lag_minutes": snap.get("lag_minutes"),
            "proxy_execution_rule": snap.get("proxy_execution_rule"),
            "alpha_err": getattr(self, "_alpha_err", 0),
        }
        raw = json.dumps(payload, separators=(",", ":"), default=str)
        chunk = 3500
        n = max(1, (len(raw) + chunk - 1) // chunk)
        self.log(f"CG_ALPHA_ID_PAYLOAD_META,parts={n},bytes={len(raw)}")
        for i in range(n):
            self.log(f"CG_ALPHA_ID_PAYLOAD,{i + 1}/{n},{raw[i * chunk:(i + 1) * chunk]}")
        # Ledger sample (bounded)
        for row in (snap.get("rows_sample") or [])[:16]:
            self.log(
                f"CG_ALPHA_ID_LEDGER,seq={row.get('seq')},dt={row.get('decision_time')},"
                f"hash={row.get('target_hash')},signed={row.get('signed_equity_exposure')},"
                f"gross={row.get('gross_equity_exposure')},cash={row.get('cash_weight')},"
                f"w2={row.get('w2')},ids={row.get('ids')},src={row.get('source')}"
            )
        self._alpha_closeout_snap = snap
