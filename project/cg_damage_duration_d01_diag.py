# cg_damage_duration_d01_diag.py -- CG-DAMAGE-DURATION-D0.1 diagnostic mixin only.
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
    material_protection_active, run_damage_d01_static_tests, scan_forbidden_apis,
    verify_frozen_defaults, build_technical_counters_csv,
)

_SH_ACTIVE = frozenset(("HEDGED", "ENTRY_PENDING", "EXIT_PENDING"))


class CgDamageDurationD01DiagMixin:
    """D0.1 episode/label/timestamp infrastructure hooks (diagnostic only)."""

    def _DamageD01ReadParams(self, _p, _bool):
        self.cg_damage_duration_d01_enable = _bool("cg_damage_duration_d01_enable", "0")

    def _DamageD01MaybeEnableMs(self):
        if getattr(self, "cg_damage_duration_d01_enable", False):
            self._ms_on = True
            self.cg_maisr_diag_enable = True

    def _DamageD01InitHooksSafe(self):
        try:
            self._DamageD01InitHooks()
        except Exception:
            self._ms_err = int(getattr(self, "_ms_err", 0) or 0) + 1

    def _DamageD01OnAcceptedBarSafe(self, tk, et, o, h, l, c):
        if getattr(self, "cg_damage_duration_d01_enable", False):
            self._DamageD01OnAcceptedBar(tk, et, o, h, l, c)

    def _DamageD01WantEval(self):
        return bool(getattr(self, "cg_damage_duration_d01_enable", False))

    def _DamageD01OnEvalSafe(self, kind, tod, states, feat):
        if getattr(self, "cg_damage_duration_d01_enable", False):
            self._DamageD01OnEval(kind, tod, states, feat)

    def CgDamageD01TryEOA(self, parity_ok):
        if not getattr(self, "cg_damage_duration_d01_enable", False):
            return False
        try:
            self.CgDamageD01OnEndOfAlgorithm(parity_ok)
        except Exception:
            self._ms_err = int(getattr(self, "_ms_err", 0) or 0) + 1
        return True

    def _DamageD01InitHooks(self):
        if not getattr(self, "cg_damage_duration_d01_enable", False):
            return
        self._dmg_on = True
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
        rep = run_damage_d01_static_tests(FROZEN_PRODUCTION_DEFAULTS, core_src, diag_src)
        self._dmg_static = rep
        self._DamageD01Log(
            f"CG_DAMAGE_D01_INIT,enable=1,tests={rep['passed']}/{rep['total']},"
            f"forbidden_api={len(hits)},frozen_ok={int(fr_ok)},"
            f"diagnostic_real_orders=0,subscription_changes=0,target_mutations=0"
        )
        if rep["failed"] or hits or not fr_ok:
            self._dmg_err += 1

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
            # Open observation: material protection entry (edge)
            if active and not prev:
                led.observe_open_trigger(EV_PROTECTION, t, src, bars)
            # D30/D45 evidence reuse when resid already evaluated on this algo
            vp = None
            if hasattr(self, "_resid_last_variant_pass"):
                vp = getattr(self, "_resid_last_variant_pass", None)
            if isinstance(vp, dict):
                if any(bool(vp.get(k)) for k in vp if str(k).startswith("D45_")):
                    led.observe_open_trigger(EV_D45, t, src, bars)
                elif any(bool(vp.get(k)) for k in vp if str(k).startswith("D30_")):
                    led.observe_open_trigger(EV_D30, t, src, bars)
            self._dmg_prev_prot = active
            self._dmg_ctr = dict(led.counters)
        except Exception:
            self._dmg_err = int(getattr(self, "_dmg_err", 0) or 0) + 1
            ctr = getattr(self, "_dmg_ctr", None) or empty_counters()
            ctr["runtime_errors"] = int(ctr.get("runtime_errors", 0) or 0) + 1
            self._dmg_ctr = ctr

    def CgDamageD01OnEndOfAlgorithm(self, parity_ok) -> bool:
        if not getattr(self, "cg_damage_duration_d01_enable", False):
            return False
        try:
            led = getattr(self, "_dmg_ledger", None)
            if led is not None:
                led.detect_orphans_and_multi()
                # right-censor still-open episodes at EOA (incomplete horizon)
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
                f"diagnostic_real_orders={ctr.get('diagnostic_real_orders', 0)},"
                f"subscription_changes={ctr.get('subscription_changes', 0)},"
                f"target_mutations={ctr.get('target_mutations', 0)}"
            )
            self._DamageD01Log("CG_DAMAGE_D01_COUNTERS_CSV_BEGIN")
            for line in build_technical_counters_csv(ctr).strip().splitlines():
                self._DamageD01Log(f"CG_DAMAGE_D01_COUNTERS,{line}")
            self._DamageD01Log("CG_DAMAGE_D01_COUNTERS_CSV_END")
            rep = getattr(self, "_dmg_static", None) or {}
            self._DamageD01Log(
                f"CG_DAMAGE_D01_CLOSEOUT,experiment={EXPERIMENT},phase={PHASE},"
                f"static={rep.get('passed', 0)}/{rep.get('total', 0)},"
                f"next=D0.2_FEATURE_EVENT_MEMORY_COLLECTOR"
            )
        except Exception as e:
            self._dmg_err = int(getattr(self, "_dmg_err", 0) or 0) + 1
            self._DamageD01Log(f"CG_DAMAGE_D01_EOA_FAIL,err={type(e).__name__}")
        return True
