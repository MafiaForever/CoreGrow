# cg_damage_duration_d01_diag.py -- CG-DAMAGE-DURATION-D0.1/D0.2A diagnostic mixin only.
# Zero order/subscription/target APIs. Consumes existing state; no production ObjectStore.
try:
    from AlgorithmImports import *  # noqa: F401,F403
except Exception:
    pass
from datetime import datetime
from cg_damage_duration_d01_core import (
    EXPERIMENT, PHASE, FROZEN_PRODUCTION_DEFAULTS, CONFIRMATION_WINDOW_MINUTES,
    EV_PROTECTION, EV_D30, EV_D45,
    DamageEpisodeLedger, empty_counters, protection_source_from_snapshot,
    material_protection_active, scan_forbidden_apis,
    verify_frozen_defaults, build_technical_counters_csv,
)
from cg_damage_duration_d02_sensor import (
    DamageD02Sensor,
    PRIOR_ATR_SOURCE, D30_D45_RUNTIME_SOURCE, EXPERIMENT as D02_EXPERIMENT,
    PHASE as D02_PHASE, empty_sensor_counters,
)
from cg_damage_duration_d02_features import (
    FeatureCollector, SCHEMA_VERSION as D02B_SCHEMA,
    EXPERIMENT as D02B_EXPERIMENT, PHASE as D02B_PHASE,
)
from cg_damage_duration_d02_structure import (
    D02CCollector, EXPERIMENT as D02C_EXPERIMENT,
    PHASE as D02C_PHASE,
)
from cg_damage_duration_d03a_shadow import (
    ModelAShadowRouter, EXPERIMENT as D03A_EXPERIMENT,
    PHASE as D03A_PHASE,
)
from cg_damage_duration_d03b_runtime import (
    ModelAShadowRuntimeAccounting,
    EXPERIMENT as D03B_EXPERIMENT, PHASE as D03B_PHASE,
)
from cg_damage_duration_d03b_compact_export import (
    apply_transport_quiet_filters, transport_quiet_active,
)
from cg_damage_duration_d03b_accounting import P0_SOURCE_VERDICT

_SH_ACTIVE = frozenset(("HEDGED", "ENTRY_PENDING", "EXIT_PENDING"))


class CgDamageDurationD01DiagMixin:
    """D0.1 episode/label infrastructure + D0.2A independent D30/D45 sensor hooks."""

    def _DamageD01ReadParams(self, _p, _bool):
        self.cg_damage_duration_d01_enable = _bool("cg_damage_duration_d01_enable", "0")
        self.cg_damage_duration_d02_enable = _bool("cg_damage_duration_d02_enable", "0")
        self.cg_damage_duration_d03a_enable = _bool("cg_damage_duration_d03a_enable", "0")
        self.cg_damage_duration_d03b_enable = _bool("cg_damage_duration_d03b_enable", "0")
        self.cg_damage_duration_d03b_fixed_only_shadow_enable = _bool(
            "cg_damage_duration_d03b_fixed_only_shadow_enable", "0")
        self.cg_damage_duration_d03b_cloud_transport_quiet_enable = _bool(
            "cg_damage_duration_d03b_cloud_transport_quiet_enable", "0")
        # Audit-only: record quiet override source (QC > RRX fallback > hard default).
        try:
            qv = self.get_parameter(
                "cg_damage_duration_d03b_cloud_transport_quiet_enable")
        except Exception:
            qv = None
        if qv is not None and str(qv).strip() != "":
            self._dmg_transport_quiet_source = "QC_PARAMETER"
        elif (getattr(self, "_rrx_param_overrides", {}) or {}).get(
                "cg_damage_duration_d03b_cloud_transport_quiet_enable") is not None:
            self._dmg_transport_quiet_source = "RRX_FALLBACK"
        else:
            self._dmg_transport_quiet_source = "HARD_DEFAULT"

    def _DamageD01MaybeEnableMs(self):
        if getattr(self, "cg_damage_duration_d01_enable", False) or getattr(
                self, "cg_damage_duration_d02_enable", False):
            self._ms_on = True
            self.cg_maisr_diag_enable = True

    def _DamageD01InitHooksSafe(self):
        try:
            self._DamageD01InitHooks()
        except Exception:
            self._ms_err = int(getattr(self, "_ms_err", 0) or 0) + 1
        try:
            self._DamageD02InitHooks()
        except Exception:
            self._ms_err = int(getattr(self, "_ms_err", 0) or 0) + 1
        try:
            self._DamageD03aInitHooks()
        except Exception:
            self._ms_err = int(getattr(self, "_ms_err", 0) or 0) + 1
        try:
            self._DamageD03bInitHooks()
        except Exception:
            self._ms_err = int(getattr(self, "_ms_err", 0) or 0) + 1
        try:
            self._DamageD03bApplyCloudTransportQuiet()
        except Exception:
            self._ms_err = int(getattr(self, "_ms_err", 0) or 0) + 1

    def _DamageD03bApplyCloudTransportQuiet(self):
        # Dual-gate only: fixed-only + transport-quiet. Mute CG_REGIME_TIME_*
        # even when regime-time init re-appended those prefixes to log_only.
        fo = bool(getattr(self, "cg_damage_duration_d03b_fixed_only_shadow_enable", False))
        q = bool(getattr(self, "cg_damage_duration_d03b_cloud_transport_quiet_enable", False))
        applied = transport_quiet_active(fo, q)
        self._dmg_transport_quiet_requested = q
        self._dmg_transport_quiet_effective = applied
        lp, mp, did = apply_transport_quiet_filters(
            getattr(self, "log_only_prefixes", None),
            getattr(self, "log_mute_prefixes", None),
            fo, q,
        )
        if not did:
            return
        self.log_only_prefixes = lp
        self.log_mute_prefixes = mp
        src = str(getattr(self, "_dmg_transport_quiet_source", "UNAVAILABLE") or "UNAVAILABLE")
        self._DamageD01Log(
            "CG_DAMAGE_D03B_TRANSPORT_QUIET,enable=1,muted=CG_REGIME_TIME_,"
            "fixed_only=1,budget_target_lt_100kb=1,source=%s,effective=1" % src
        )

    def _DamageD01OnAcceptedBarSafe(self, tk, et, o, h, l, c):
        if getattr(self, "cg_damage_duration_d01_enable", False):
            self._DamageD01OnAcceptedBar(tk, et, o, h, l, c)
        if getattr(self, "cg_damage_duration_d02_enable", False):
            self._DamageD02OnAcceptedBar(tk, et, o, h, l, c)
            fc = getattr(self, "_dmg_d02_features", None)
            if fc is not None:
                try:
                    t = self.time if isinstance(getattr(self, "time", None), datetime) else None
                    fc.on_accepted_bar(tk, et, o, h, l, c, decision_time=t)
                except Exception:
                    self._dmg_d02_err = int(getattr(self, "_dmg_d02_err", 0) or 0) + 1

    def _DamageD01WantEval(self):
        return bool(getattr(self, "cg_damage_duration_d01_enable", False)
                    or getattr(self, "cg_damage_duration_d02_enable", False))

    def _DamageD01OnEvalSafe(self, kind, tod, states, feat):
        if getattr(self, "cg_damage_duration_d02_enable", False):
            self._DamageD02OnEval(kind, tod, states, feat)
        elif getattr(self, "cg_damage_duration_d01_enable", False):
            self._DamageD01OnEval(kind, tod, states, feat)

    def _DamageD0FixedOnlyEOAPredicate(self):
        # Fixed-only D0 finalization must not depend on _sr_on / cg_maisr_diag_enable.
        return bool(getattr(self, "cg_damage_duration_d03b_enable", False)) and bool(
            getattr(self, "cg_damage_duration_d03b_fixed_only_shadow_enable", False))

    def _DamageD0FixedOnlyEmitEOAOnce(self, parity_ok=True):
        """Idempotent D0 compact EOA. Export-only; no state/target/order mutation."""
        if not self._DamageD0FixedOnlyEOAPredicate():
            return False
        if getattr(self, "_dmg_d0_eoa_emitted", False):
            return False
        try:
            d01 = bool(getattr(self, "cg_damage_duration_d01_enable", False))
            d02 = bool(getattr(self, "cg_damage_duration_d02_enable", False))
            if not d01 and not d02:
                self._DamageD01Log(
                    "D0_COMPACT_CLOSEOUT,status=EOA_SKIPPED,reason=D01_D02_DISABLED,"
                    "export_mode=CLOUD_COMPACT_AGGREGATE"
                )
            else:
                if d02:
                    self.CgDamageD02OnEndOfAlgorithm(parity_ok)
                if d01 and not d02:
                    self.CgDamageD01OnEndOfAlgorithm(parity_ok)
        except Exception as e:
            try:
                self._DamageD01Log(
                    f"D0_COMPACT_CLOSEOUT,status=EOA_FAIL,err={type(e).__name__},"
                    f"export_mode=CLOUD_COMPACT_AGGREGATE"
                )
            except Exception:
                pass
        finally:
            self._dmg_d0_eoa_emitted = True
        return True

    def CgDamageD01TryEOA(self, parity_ok):
        if getattr(self, "_dmg_d0_eoa_emitted", False):
            return False
        d01 = bool(getattr(self, "cg_damage_duration_d01_enable", False))
        d02 = bool(getattr(self, "cg_damage_duration_d02_enable", False))
        if not d01 and not d02:
            return False
        try:
            if d02:
                self.CgDamageD02OnEndOfAlgorithm(parity_ok)
            if d01 and not d02:
                self.CgDamageD01OnEndOfAlgorithm(parity_ok)
            elif d01 and d02:
                # D0.2A owns EOA when both on; d01 ledger already shared
                pass
            self._dmg_d0_eoa_emitted = True
        except Exception:
            self._ms_err = int(getattr(self, "_ms_err", 0) or 0) + 1
            self._dmg_d0_eoa_emitted = True
        return True

    def _DamageD01InitHooks(self):
        if not getattr(self, "cg_damage_duration_d01_enable", False):
            return
        self._dmg_on = True
        if getattr(self, "_dmg_ledger", None) is None:
            self._dmg_ledger = DamageEpisodeLedger(confirmation_minutes=CONFIRMATION_WINDOW_MINUTES)
        self._dmg_ctr = empty_counters()
        self._dmg_err = 0
        self._dmg_prev_prot = False
        self._dmg_bar_ends = []
        self._dmg_real_orders = 0
        self._dmg_sub_changes = 0
        self._dmg_target_mut = 0
        lp = list(getattr(self, "log_only_prefixes", None) or [])
        if "CG_DAMAGE_D01_" not in lp:
            lp.append("CG_DAMAGE_D01_")
        self.log_only_prefixes = lp
        try:
            import inspect
            core_src = inspect.getsource(__import__("cg_damage_duration_d01_core", fromlist=["*"]))
            diag_src = inspect.getsource(__import__("cg_damage_duration_d01_diag", fromlist=["*"]))
        except Exception:
            core_src = diag_src = ""
        hits = scan_forbidden_apis(core_src + "\n" + diag_src)
        fr_ok, _ = verify_frozen_defaults(FROZEN_PRODUCTION_DEFAULTS)
        # Static suites run only via explicit Cursor/local tooling (not Initialize).
        self._dmg_static = {"passed": 0, "failed": 0, "total": 0, "external_only": 1}
        self._DamageD01Log(
            f"CG_DAMAGE_D01_INIT,enable=1,tests=EXTERNAL_ONLY,"
            f"forbidden_api={len(hits)},frozen_ok={int(fr_ok)},"
            f"diagnostic_real_orders=0,subscription_changes=0,target_mutations=0"
        )
        if hits or not fr_ok:
            self._dmg_err += 1

    def _DamageD02InitHooks(self):
        if not getattr(self, "cg_damage_duration_d02_enable", False):
            return
        if getattr(self, "_dmg_ledger", None) is None:
            self._dmg_ledger = DamageEpisodeLedger(confirmation_minutes=CONFIRMATION_WINDOW_MINUTES)
        self._dmg_d02_sensor = DamageD02Sensor()
        self._dmg_d02_features = FeatureCollector()
        self._dmg_d02c = D02CCollector()
        self._dmg_d02_ctr = empty_sensor_counters()
        self._dmg_d02_err = 0
        self._dmg_prev_prot = getattr(self, "_dmg_prev_prot", False)
        self._dmg_bar_ends = getattr(self, "_dmg_bar_ends", None) or []
        self._dmg_real_orders = int(getattr(self, "_dmg_real_orders", 0) or 0)
        self._dmg_sub_changes = int(getattr(self, "_dmg_sub_changes", 0) or 0)
        self._dmg_target_mut = int(getattr(self, "_dmg_target_mut", 0) or 0)
        lp = list(getattr(self, "log_only_prefixes", None) or [])
        for pref in ("CG_DAMAGE_D02_", "CG_DAMAGE_D02B_", "CG_DAMAGE_D02C_"):
            if pref not in lp:
                lp.append(pref)
        self.log_only_prefixes = lp
        # Static suites: EXTERNAL_ONLY (Cursor/local). Never invoke from Initialize.
        _ext = {"passed": 0, "failed": 0, "total": 0, "external_only": 1}
        self._dmg_d02_static = dict(_ext)
        self._dmg_d02b_static = dict(_ext)
        self._dmg_d02c_static = dict(_ext)
        self._DamageD01Log(
            f"CG_DAMAGE_D02A_INIT,enable=1,tests=EXTERNAL_ONLY,"
            f"atr_source={PRIOR_ATR_SOURCE},runtime_source={D30_D45_RUNTIME_SOURCE},"
            f"macro_resid_b1_required=0,diagnostic_real_orders=0"
        )
        self._DamageD01Log(
            f"CG_DAMAGE_D02B_INIT,enable=1,tests=EXTERNAL_ONLY,"
            f"schema={D02B_SCHEMA},feature_collector=1,event_memory=1"
        )
        self._DamageD01Log(
            f"CG_DAMAGE_D02C_INIT,enable=1,tests=EXTERNAL_ONLY,"
            f"changepoint=1,structure=1,veto=FORBIDDEN"
        )

    def _DamageD03aInitHooks(self):
        # Initialize only when both D0.2 and D0.3A are enabled; else no-op.
        if not getattr(self, "cg_damage_duration_d03a_enable", False):
            return
        if not getattr(self, "cg_damage_duration_d02_enable", False):
            self._dmg_d03a_dep_fail = True
            self._DamageD01Log("CG_DAMAGE_D03A_INIT,enable=1,dependency=D02_REQUIRED,initialized=0")
            return
        self._dmg_d03a = ModelAShadowRouter()
        self._dmg_d03a_dep_fail = False
        lp = list(getattr(self, "log_only_prefixes", None) or [])
        if "CG_DAMAGE_D03A_" not in lp:
            lp.append("CG_DAMAGE_D03A_")
        self.log_only_prefixes = lp
        self._dmg_d03a_static = {"passed": 0, "failed": 0, "total": 0, "external_only": 1}
        self._DamageD01Log(
            "CG_DAMAGE_D03A_INIT,enable=1,tests=EXTERNAL_ONLY,"
            "model_a=1,shadow=1,production_actions=0"
        )

    def _DamageD03bInitHooks(self):
        # D0.3B1 accounting/export; requires D0.3A. Default OFF.
        if not getattr(self, "cg_damage_duration_d03b_enable", False):
            return
        if not getattr(self, "cg_damage_duration_d03a_enable", False):
            self._DamageD01Log("CG_DAMAGE_D03B_INIT,enable=1,dependency=D03A_REQUIRED,initialized=0")
            return
        self._dmg_d03b = ModelAShadowRuntimeAccounting()
        lp = list(getattr(self, "log_only_prefixes", None) or [])
        if "CG_DAMAGE_D03B_" not in lp:
            lp.append("CG_DAMAGE_D03B_")
        self.log_only_prefixes = lp
        # Never run static suites / failure-containment monkeypatches from Initialize.
        self._dmg_d03b_static = {
            "passed": 0, "failed": 0, "total": 0, "external_only": 1,
            "p0_verdict": P0_SOURCE_VERDICT, "phase_verdict": "RUNTIME_INIT_OK",
        }
        self._DamageD01Log(
            f"CG_DAMAGE_D03B_INIT,enable=1,tests=EXTERNAL_ONLY,"
            f"p0={P0_SOURCE_VERDICT},verdict=RUNTIME_INIT_OK"
        )

    def _DamageD01Log(self, msg):
        try:
            if hasattr(self, "_MsLog"):
                self._MsLog(msg)
            else:
                self.log(msg)
        except Exception:
            pass

    def _DamageD01ShActive(self):
        st = str(getattr(self, "_sh_state", "") or "").strip().upper()
        return st in _SH_ACTIVE

    def _DamageD01ProtectionSnap(self):
        return {
            "w2_active": bool(getattr(self, "_cg_w2_last_active", False)),
            "ids_state": getattr(self, "_ids_state", None),
            "panic_state": getattr(self, "_panic_state", None),
            "emergency_active": bool(getattr(self, "emergency_stop_triggered", False))
            or bool(getattr(self, "_dd_cb_active", False)),
            "reduce_only_active": bool(getattr(self, "_lfc_force_reduce", False))
            or bool(getattr(self, "_cg_rt_pending_reduce", False))
            or (getattr(self, "_state_save_ok", True) is False),
        }

    def _DamageD01OnAcceptedBar(self, tk, et, o, h, l, c):
        if not getattr(self, "cg_damage_duration_d01_enable", False):
            return
        if et is None:
            return
        try:
            ends = getattr(self, "_dmg_bar_ends", None)
            if ends is None:
                self._dmg_bar_ends = []
                ends = self._dmg_bar_ends
            ends.append(et)
            if len(ends) > 5000:
                del ends[:1000]
        except Exception:
            self._dmg_err = int(getattr(self, "_dmg_err", 0) or 0) + 1

    def _DamageD02OnAcceptedBar(self, tk, et, o, h, l, c):
        if not getattr(self, "cg_damage_duration_d02_enable", False):
            return
        sens = getattr(self, "_dmg_d02_sensor", None)
        if sens is None or et is None:
            return
        try:
            t = self.time if isinstance(getattr(self, "time", None), datetime) else None
            sens.on_accepted_bar(tk, et, o, h, l, c, decision_time=t)
            ends = getattr(self, "_dmg_bar_ends", None)
            if ends is None:
                self._dmg_bar_ends = []
                ends = self._dmg_bar_ends
            ends.append(et)
            if len(ends) > 5000:
                del ends[:1000]
        except Exception:
            self._dmg_d02_err = int(getattr(self, "_dmg_d02_err", 0) or 0) + 1

    def _DamageD01OnEval(self, kind, tod, states, feat):
        if not getattr(self, "cg_damage_duration_d01_enable", False):
            return
        if str(kind) != "POST":
            return
        try:
            snap = self._DamageD01ProtectionSnap()
            src = protection_source_from_snapshot(snap, sh_active=self._DamageD01ShActive())
            active = material_protection_active(snap, sh_active=self._DamageD01ShActive())
            prev = bool(getattr(self, "_dmg_prev_prot", False))
            t = self.time if isinstance(getattr(self, "time", None), datetime) else None
            if t is None:
                try:
                    t = datetime(self.time.year, self.time.month, self.time.day,
                                 self.time.hour, self.time.minute, getattr(self.time, "second", 0))
                except Exception:
                    return
            bars = list(getattr(self, "_dmg_bar_ends", []) or [])
            led = getattr(self, "_dmg_ledger", None)
            if led is None:
                return
            ck = (t.date().toordinal(), int(tod)) if tod is not None else (
                t.date().toordinal(), t.hour * 60 + t.minute)
            # RELEASE_CHECK before open/attach; skip same-checkpoint open if mutated.
            lc = led.process_release_check(
                t, protection_active=active, prev_protection_active=prev,
                d_severity=None, protection_source=src, bar_end_times=bars,
                checkpoint_key=("RC",) + tuple(ck) if isinstance(ck, tuple) else ("RC", ck))
            skip_open = bool(lc.get("mutated"))
            if active and not prev and not skip_open:
                led.observe_open_trigger(EV_PROTECTION, t, src, bars)
            # D0.1-only path: B1 residual pass-through (unresolved when B1 off; D0.2A replaces)
            if not getattr(self, "cg_damage_duration_d02_enable", False) and not skip_open:
                vp = getattr(self, "_resid_last_variant_pass", None)
                if isinstance(vp, dict):
                    if any(bool(vp.get(k)) for k in vp if str(k).startswith("D45_")):
                        led.observe_open_trigger(EV_D45, t, src, bars)
                    elif any(bool(vp.get(k)) for k in vp if str(k).startswith("D30_")):
                        led.observe_open_trigger(EV_D30, t, src, bars)
            # After confirmed close, clear event memory active pointer if present.
            if lc.get("action") == "CONFIRMED_CLOSE":
                try:
                    fc = getattr(self, "_dmg_d02_features", None)
                    store = getattr(fc, "memory", None) if fc is not None else None
                    if store is None:
                        store = getattr(self, "_dmg_event_memory", None)
                    if store is not None and hasattr(store, "sync_open_episode"):
                        store.sync_open_episode(None, t, t, "NONE", None, None, "NONE")
                except Exception:
                    pass
            self._dmg_prev_prot = active
            self._dmg_ctr = dict(led.counters)
            self._dmg_last_lifecycle = lc
        except Exception:
            self._dmg_err = int(getattr(self, "_dmg_err", 0) or 0) + 1
            ctr = getattr(self, "_dmg_ctr", None) or empty_counters()
            ctr["runtime_errors"] = int(ctr.get("runtime_errors", 0) or 0) + 1
            self._dmg_ctr = ctr

    def _DamageD02OnEval(self, kind, tod, states, feat):
        if not getattr(self, "cg_damage_duration_d02_enable", False):
            return
        if str(kind) != "POST":
            return
        sens = getattr(self, "_dmg_d02_sensor", None)
        led = getattr(self, "_dmg_ledger", None)
        if sens is None or led is None:
            return
        try:
            snap_prot = self._DamageD01ProtectionSnap()
            src = protection_source_from_snapshot(snap_prot, sh_active=self._DamageD01ShActive())
            active = material_protection_active(snap_prot, sh_active=self._DamageD01ShActive())
            prev = bool(getattr(self, "_dmg_prev_prot", False))
            t = self.time if isinstance(getattr(self, "time", None), datetime) else None
            if t is None:
                try:
                    t = datetime(self.time.year, self.time.month, self.time.day,
                                 self.time.hour, self.time.minute, getattr(self.time, "second", 0))
                except Exception:
                    return
            bars = list(getattr(self, "_dmg_bar_ends", []) or [])
            atr_map = dict(getattr(self, "_ms_atr", {}) or {})
            b1_on = bool(getattr(self, "cg_macro_resid_b1_enable", False))
            b1_vp = None
            if b1_on:
                # comparison-only; never call B1 methods — read if already present
                vp = getattr(self, "_resid_last_variant_pass", None)
                if isinstance(vp, dict):
                    b1_vp = vp
            sens_snap = sens.evaluate(
                t, atr_map, source_macro_resid_enabled=b1_on, b1_variant_pass=b1_vp)
            ck = (t.date().toordinal(), int(tod))
            d_sev = None if sens_snap is None else sens_snap.get("strongest_severity")
            # RELEASE_CHECK before open/attach; existing causal predicates only.
            lc = led.process_release_check(
                t, protection_active=active, prev_protection_active=prev,
                d_severity=d_sev, protection_source=src, bar_end_times=bars,
                checkpoint_key=("RC2",) + tuple(ck))
            self._dmg_last_lifecycle = lc
            skip_open = bool(lc.get("mutated"))
            if active and not prev and not skip_open:
                led.observe_open_trigger(EV_PROTECTION, t, src, bars)
            if sens_snap is not None and not skip_open:
                sens.attach_to_ledger(led, sens_snap, protection_source=src,
                                      bar_end_times=bars, checkpoint_key=ck)
            if lc.get("action") == "CONFIRMED_CLOSE":
                try:
                    fc0 = getattr(self, "_dmg_d02_features", None)
                    store = getattr(fc0, "memory", None) if fc0 is not None else None
                    if store is not None and hasattr(store, "sync_open_episode"):
                        store.sync_open_episode(None, t, t, "NONE", None, None, "NONE")
                except Exception:
                    pass
            # D0.2B feature collector + event memory (after sensor/ledger)
            fc = getattr(self, "_dmg_d02_features", None)
            if fc is not None:
                ep = led.current_open() if led is not None else None
                nav = "UNAVAILABLE"
                try:
                    nav = float(self.portfolio.total_portfolio_value)
                except Exception:
                    try:
                        nav = float(self.Portfolio.TotalPortfolioValue)
                    except Exception:
                        nav = "UNAVAILABLE"
                # action-eligible: first bar end > t among known ends
                act = None
                for et in sorted(bars):
                    if et is not None and et > t:
                        act = et
                        break
                snap_b = fc.build_snapshot(t, ck, sens_snap, ep, nav, src, action_eligible_time=act)
                d02c = getattr(self, "_dmg_d02c", None)
                snap_c = None
                if d02c is not None and snap_b is not None:
                    snap_c = d02c.update(snap_b)
                d03a = getattr(self, "_dmg_d03a", None)
                shadow_out = None
                if d03a is not None and snap_b is not None:
                    shadow_out = d03a.update(
                        snap_b, snap_c if snap_c is not None else getattr(d02c, "last_snapshot", None),
                        d02_enabled=bool(getattr(self, "cg_damage_duration_d02_enable", False)),
                        d03a_enabled=bool(getattr(self, "cg_damage_duration_d03a_enable", False)),
                    )
                d03b = getattr(self, "_dmg_d03b", None)
                if d03b is not None and snap_b is not None and shadow_out is not None:
                    sb_acc = dict(snap_b)
                    if sb_acc.get("action_eligible_time") in (None,):
                        sb_acc["action_eligible_time"] = act
                    d03b.update(
                        sb_acc, snap_c if snap_c is not None else getattr(d02c, "last_snapshot", None),
                        shadow_out,
                        d03b_enabled=bool(getattr(self, "cg_damage_duration_d03b_enable", False)),
                        prod_state={"production_nav_read_only": nav},
                        fixed_only_shadow_enable=bool(
                            getattr(self, "cg_damage_duration_d03b_fixed_only_shadow_enable", False)),
                    )
            self._dmg_prev_prot = active
            self._dmg_d02_ctr = dict(sens.counters)
            self._dmg_ctr = dict(led.counters)
        except Exception:
            self._dmg_d02_err = int(getattr(self, "_dmg_d02_err", 0) or 0) + 1
            ctr = getattr(self, "_dmg_d02_ctr", None) or empty_sensor_counters()
            ctr["runtime_errors"] = int(ctr.get("runtime_errors", 0) or 0) + 1
            self._dmg_d02_ctr = ctr

    def CgDamageD01OnEndOfAlgorithm(self, parity_ok) -> bool:
        if not getattr(self, "cg_damage_duration_d01_enable", False):
            return False
        try:
            led = getattr(self, "_dmg_ledger", None)
            if led is not None:
                led.detect_orphans_and_multi()
                for eid, ep in list(led.episodes.items()):
                    if ep.state in ("OPEN", "PROVISIONAL_CLOSE") and not ep.locked:
                        led.mark_right_censored(eid, now_t=getattr(self, "time", None))
                self._dmg_ctr = dict(led.counters)
            ctr = getattr(self, "_dmg_ctr", None) or empty_counters()
            ctr["diagnostic_real_orders"] = int(getattr(self, "_dmg_real_orders", 0) or 0)
            ctr["subscription_changes"] = int(getattr(self, "_dmg_sub_changes", 0) or 0)
            ctr["target_mutations"] = int(getattr(self, "_dmg_target_mut", 0) or 0)
            ctr["runtime_errors"] = int(getattr(self, "_dmg_err", 0) or 0)
            self._DamageD01Log(
                f"CG_DAMAGE_D01_EOA,parity={int(bool(parity_ok))},"
                f"episodes={ctr.get('episodes_created', 0)},events={ctr.get('events_created', 0)},"
                f"right_censored={ctr.get('right_censored_episodes', 0)},"
                f"diagnostic_real_orders={ctr.get('diagnostic_real_orders', 0)}"
            )
            rep = getattr(self, "_dmg_static", None) or {}
            self._DamageD01Log(
                f"CG_DAMAGE_D01_CLOSEOUT,experiment={EXPERIMENT},phase={PHASE},"
                f"static={rep.get('passed', 0)}/{rep.get('total', 0)}"
            )
        except Exception as e:
            self._dmg_err = int(getattr(self, "_dmg_err", 0) or 0) + 1
            self._DamageD01Log(f"CG_DAMAGE_D01_EOA_FAIL,err={type(e).__name__}")
        return True

    def CgDamageD02OnEndOfAlgorithm(self, parity_ok) -> bool:
        if not getattr(self, "cg_damage_duration_d02_enable", False):
            return False
        try:
            led = getattr(self, "_dmg_ledger", None)
            sens = getattr(self, "_dmg_d02_sensor", None)
            if led is not None:
                led.detect_orphans_and_multi()
            ctr = dict(getattr(sens, "counters", None) or empty_sensor_counters())
            ctr["diagnostic_real_orders"] = int(getattr(self, "_dmg_real_orders", 0) or 0)
            ctr["subscription_changes"] = int(getattr(self, "_dmg_sub_changes", 0) or 0)
            ctr["target_mutations"] = int(getattr(self, "_dmg_target_mut", 0) or 0)
            ctr["runtime_errors"] = int(getattr(self, "_dmg_d02_err", 0) or 0)
            self._dmg_d02_ctr = ctr
            rep = getattr(self, "_dmg_d02_static", None) or {}
            self._DamageD01Log(
                f"CG_DAMAGE_D02A_EOA,parity={int(bool(parity_ok))},"
                f"sensor_eval={ctr.get('sensor_evaluations', 0)},"
                f"d30={ctr.get('ledger_d30_events', 0)},d45={ctr.get('ledger_d45_events', 0)},"
                f"unavailable={ctr.get('sensor_unavailable', 0)},"
                f"parity_mismatch={ctr.get('b1_parity_mismatch', 0)},"
                f"diagnostic_real_orders={ctr.get('diagnostic_real_orders', 0)}"
            )
            self._DamageD01Log(
                f"CG_DAMAGE_D02A_CLOSEOUT,experiment={D02_EXPERIMENT},phase={D02_PHASE},"
                f"static={rep.get('passed', 0)}/{rep.get('total', 0)},"
                f"runtime_source={D30_D45_RUNTIME_SOURCE},"
                f"next=D0.3A_MODEL_A_DETERMINISTIC_SHADOW_ROUTER"
            )
            rep_b = getattr(self, "_dmg_d02b_static", None) or {}
            fc = getattr(self, "_dmg_d02_features", None)
            n_snap = int(getattr(fc, "counters", {}).get("feature_snapshots", 0) or 0) if fc else 0
            self._DamageD01Log(
                f"CG_DAMAGE_D02B_CLOSEOUT,experiment={D02B_EXPERIMENT},phase={D02B_PHASE},"
                f"static={rep_b.get('passed', 0)}/{rep_b.get('total', 0)},"
                f"feature_snapshots={n_snap},feature_collector=IMPLEMENTED,"
                f"event_memory=IMPLEMENTED,recovery_score=NOT_IMPLEMENTED,"
                f"next=D0.3A_MODEL_A_DETERMINISTIC_SHADOW_ROUTER"
            )
            rep_c = getattr(self, "_dmg_d02c_static", None) or {}
            d02c = getattr(self, "_dmg_d02c", None)
            n_c = int(getattr(d02c, "counters", {}).get("snapshots", 0) or 0) if d02c else 0
            self._DamageD01Log(
                f"CG_DAMAGE_D02C_CLOSEOUT,experiment={D02C_EXPERIMENT},phase={D02C_PHASE},"
                f"static={rep_c.get('passed', 0)}/{rep_c.get('total', 0)},"
                f"d02c_snapshots={n_c},changepoint=IMPLEMENTED,structure=IMPLEMENTED,"
                f"veto=FORBIDDEN,recovery_score=NOT_IMPLEMENTED,"
                f"next=D0.3B_MODEL_A_SHADOW_RUNTIME_BACKTEST"
            )
            if getattr(self, "cg_damage_duration_d03a_enable", False):
                rep_a = getattr(self, "_dmg_d03a_static", None) or {}
                d03a = getattr(self, "_dmg_d03a", None)
                n_a = int(getattr(d03a, "counters", {}).get("snapshots", 0) or 0) if d03a else 0
                self._DamageD01Log(
                    f"CG_DAMAGE_D03A_CLOSEOUT,experiment={D03A_EXPERIMENT},phase={D03A_PHASE},"
                    f"static={rep_a.get('passed', 0)}/{rep_a.get('total', 0)},"
                    f"shadow_snapshots={n_a},model_a=IMPLEMENTED,p5=IMPLEMENTED,"
                    f"production_actions=0,next=D0.3B1_ACCOUNTING_EXPORT"
                )
            if getattr(self, "cg_damage_duration_d03b_enable", False):
                rep_b3 = getattr(self, "_dmg_d03b_static", None) or {}
                d03b = getattr(self, "_dmg_d03b", None)
                n_b = int(getattr(d03b, "counters", {}).get("snapshots", 0) or 0) if d03b else 0
                self._DamageD01Log(
                    f"CG_DAMAGE_D03B_CLOSEOUT,experiment={D03B_EXPERIMENT},phase={D03B_PHASE},"
                    f"static={rep_b3.get('passed', 0)}/{rep_b3.get('total', 0)},"
                    f"runtime_rows={n_b},p0={rep_b3.get('p0_verdict', 'UNRESOLVED')},"
                    f"verdict={rep_b3.get('phase_verdict', 'REPAIR_REQUIRED')},"
                    f"next=D0.3B2B_FIXED_ONLY_SHADOW_HISTORICAL_BACKTEST_RERUN"
                )
                # Compact aggregate closeout (bounded PART frames; no legacy full JSON line).
                if d03b is not None and hasattr(d03b, "compact_closeout_part_lines"):
                    try:
                        ly = None
                        lc_ctr = None
                        if led is not None:
                            try:
                                ly = led.finalize_lifecycle_yearly_eoy(
                                    as_of=getattr(self, "time", None))
                            except Exception:
                                ly = dict(getattr(led, "lifecycle_yearly", {}) or {})
                            lc_ctr = dict(getattr(led, "counters", {}) or {})
                        fo = bool(getattr(
                            self, "cg_damage_duration_d03b_fixed_only_shadow_enable", False))
                        q_req = bool(getattr(
                            self, "cg_damage_duration_d03b_cloud_transport_quiet_enable", False))
                        q_eff = bool(getattr(
                            self, "_dmg_transport_quiet_effective",
                            transport_quiet_active(fo, q_req)))
                        tmeta = {
                            "quiet_requested": q_req,
                            "quiet_effective": q_eff,
                            "quiet_effective_source": str(getattr(
                                self, "_dmg_transport_quiet_source", "UNAVAILABLE")
                                or "UNAVAILABLE"),
                            "fixed_only_requested": fo,
                        }
                        rid = str(
                            getattr(self, "algorithm_id", None)
                            or getattr(self, "AlgorithmId", None)
                            or "D0"
                        )
                        status, lines, meta, _payload = d03b.compact_closeout_part_lines(
                            source_manifest_hash=getattr(
                                self, "_dmg_source_manifest_hash", None),
                            lifecycle_yearly=ly,
                            lifecycle_counters=lc_ctr,
                            transport_meta=tmeta,
                            run_id=rid,
                        )
                        if status == "OK" and lines:
                            for ln in lines:
                                self._DamageD01Log(ln)
                        elif lines:
                            for ln in lines:
                                self._DamageD01Log(ln)
                        else:
                            self._DamageD01Log(
                                "D0_COMPACT_CLOSEOUT,status=OVERSIZE_OR_EMPTY,"
                                "export_mode=CLOUD_COMPACT_AGGREGATE"
                            )
                    except Exception:
                        self._DamageD01Log(
                            "D0_COMPACT_CLOSEOUT,status=EOA_EMIT_FAIL,"
                            "export_mode=CLOUD_COMPACT_AGGREGATE"
                        )
                elif d03b is not None and hasattr(d03b, "compact_closeout_line"):
                    # Defensive: older accounting object without PART API.
                    try:
                        from cg_damage_duration_d03b_compact_export import (
                            frame_compact_closeout_parts as _frame_parts,
                        )
                        payload = d03b.compact_closeout_payload(
                            source_manifest_hash=getattr(
                                self, "_dmg_source_manifest_hash", None))
                        _st, lines, _meta = _frame_parts(payload)
                        for ln in lines:
                            self._DamageD01Log(ln)
                    except Exception:
                        self._DamageD01Log(
                            "D0_COMPACT_CLOSEOUT,status=EOA_EMIT_FAIL,"
                            "export_mode=CLOUD_COMPACT_AGGREGATE"
                        )
        except Exception as e:
            self._dmg_d02_err = int(getattr(self, "_dmg_d02_err", 0) or 0) + 1
            self._DamageD01Log(f"CG_DAMAGE_D02A_EOA_FAIL,err={type(e).__name__}")
        return True


def run_damage_d01_diag_signature_static_tests():
    """External-only regression: D02 evaluate keyword binding through real OnEval path."""
    import ast
    import inspect
    import re
    import sys
    from datetime import timedelta
    from types import SimpleNamespace

    rows, passed, failed = [], 0, 0

    def ok(n, c, detail=""):
        nonlocal passed, failed
        if c:
            passed += 1
            rows.append({"name": n, "pass": True, "detail": detail})
        else:
            failed += 1
            rows.append({"name": n, "pass": False, "detail": str(detail)})

    sig = inspect.signature(DamageD02Sensor.evaluate)
    ok("S01_callee_has_source_macro", "source_macro_resid_enabled" in sig.parameters)
    ok("S02_callee_no_force_macro", "force_macro_resid_enabled" not in sig.parameters)

    src = inspect.getsource(CgDamageDurationD01DiagMixin._DamageD02OnEval)
    ok("S03_caller_uses_source_macro", "source_macro_resid_enabled=" in src)
    ok("S04_caller_absent_force_macro", "force_macro_resid_enabled" not in src)

    tree = ast.parse(inspect.getsource(CgDamageDurationD01DiagMixin))
    eval_kws = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == "evaluate":
                eval_kws = [kw.arg for kw in node.keywords if kw.arg]
    ok("S05_ast_evaluate_kw_source", "source_macro_resid_enabled" in eval_kws)
    ok("S06_ast_evaluate_kw_no_force", "force_macro_resid_enabled" not in eval_kws)

    sens = DamageD02Sensor()
    t0 = datetime(2024, 3, 11, 10, 0, 0)
    try:
        out = sens.evaluate(t0, {}, source_macro_resid_enabled=False, b1_variant_pass=None)
        ok("S07_evaluate_source_kw_ok", True, detail=str(type(out)))
    except TypeError as e:
        ok("S07_evaluate_source_kw_ok", False, detail=str(e))
    try:
        sens.evaluate(t0, {}, force_macro_resid_enabled=False)
        ok("S08_evaluate_force_kw_rejected", False, detail="unexpected_accept")
    except TypeError as e:
        ok("S08_evaluate_force_kw_rejected", "force_macro_resid_enabled" in str(e), detail=str(e))

    class _RecSensor(DamageD02Sensor):
        def __init__(self):
            super().__init__()
            self.last_kwargs = None

        def evaluate(self, decision_time, atr_map, source_macro_resid_enabled=False,
                     b1_variant_pass=None, extras=None):
            self.last_kwargs = {
                "source_macro_resid_enabled": source_macro_resid_enabled,
                "b1_variant_pass": b1_variant_pass,
            }
            return super().evaluate(
                decision_time, atr_map,
                source_macro_resid_enabled=source_macro_resid_enabled,
                b1_variant_pass=b1_variant_pass, extras=extras)

    class _Host(CgDamageDurationD01DiagMixin):
        def __init__(self):
            self.cg_damage_duration_d02_enable = True
            self.cg_damage_duration_d03a_enable = False
            self.cg_damage_duration_d03b_enable = False
            self.cg_macro_resid_b1_enable = False
            self.time = t0
            self._dmg_ledger = DamageEpisodeLedger()
            self._dmg_d02_sensor = _RecSensor()
            self._dmg_d02_features = None
            self._dmg_d02c = None
            self._dmg_d03a = None
            self._dmg_d03b = None
            self._dmg_prev_prot = False
            self._dmg_bar_ends = [t0 - timedelta(minutes=5), t0, t0 + timedelta(minutes=5)]
            self._ms_atr = {}
            self._dmg_d02_err = 0
            self.portfolio = SimpleNamespace(total_portfolio_value=100000.0)

        def _DamageD01ProtectionSnap(self):
            return {"w2_active": False, "ids_state": "NORMAL", "panic_state": "NORMAL",
                    "emergency_active": False, "reduce_only_active": False}

        def _DamageD01ShActive(self):
            return False

    host = _Host()
    host._DamageD02OnEval("POST", 600, b"", None)
    kw = host._dmg_d02_sensor.last_kwargs or {}
    ok("S09_runtime_path_invoked",
       kw.get("source_macro_resid_enabled") is False
       and "force_macro_resid_enabled" not in kw)
    ok("S10_feature_cutoff_contract",
       all((ev.feature_cutoff is None or ev.feature_cutoff <= ev.decision_time)
           for ev in host._dmg_ledger.events.values()) if host._dmg_ledger.events else True)

    init_blob = "\n".join([
        inspect.getsource(CgDamageDurationD01DiagMixin._DamageD01InitHooks),
        inspect.getsource(CgDamageDurationD01DiagMixin._DamageD02InitHooks),
        inspect.getsource(CgDamageDurationD01DiagMixin._DamageD01InitHooksSafe),
    ])
    hits = re.findall(r"run_\w*static_tests\s*\(", init_blob)
    ok("S11_init_no_static_suite", len(hits) == 0, detail=str(hits))

    runtime_src = inspect.getsource(CgDamageDurationD01DiagMixin._DamageD02OnEval)
    ok("S12_force_absent_runtime_method", "force_macro_resid_enabled" not in runtime_src)

    # Char count without filesystem file-read APIs (Cloudsafe-forbidden).
    mod = sys.modules.get("cg_damage_duration_d01_diag") or sys.modules[__name__]
    n_chars = len(inspect.getsource(mod))
    ok("S13_diag_below_64000", n_chars < 64000, detail=str(n_chars))

    return {
        "passed": passed, "failed": failed, "total": passed + failed, "rows": rows,
        "old_keyword": "force_macro_resid_enabled",
        "corrected_keyword": "source_macro_resid_enabled",
        "callee_signature": str(sig),
        "runtime_reachable_static_test_call_count": len(hits),
        "d01_diag_chars": n_chars,
    }


if __name__ == "__main__":
    import json as _json
    rep = run_damage_d01_diag_signature_static_tests()
    print(_json.dumps({
        "passed": rep["passed"], "failed": rep["failed"], "total": rep["total"],
        "d01_diag_chars": rep.get("d01_diag_chars"),
    }))
    for row in rep["rows"]:
        if not row["pass"]:
            print("FAIL", row["name"], row["detail"])
