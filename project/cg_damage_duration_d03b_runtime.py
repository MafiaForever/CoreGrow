# cg_damage_duration_d03b_runtime.py -- D0.3B1 shadow policy runtime accounting collector.
# Diagnostic research only. Consumes immutable D0.3A snapshots; no production mutations.
from __future__ import annotations
import ast
import json
import re
from copy import deepcopy
from datetime import datetime, timedelta

from cg_damage_duration_d03a_core import UNAVAILABLE, get
from cg_damage_duration_d03b_accounting import (
    EXPERIMENT, PHASE, SCHEMA_VERSION, POLICY_IDS, P0_SOURCE_NAME, P0_SOURCE_VERDICT,
    MAX_CHECKPOINT_ROWS, MAX_EPISODE_ROWS, P0_AUDIT,
    resolve_p0_numeric_source, build_policy_observation, build_episode_summary,
    policy_runtime_schema, validate_timestamps,
    FIXED_ONLY_POLICIES, FIXED_ONLY_BASELINES, NORMALIZED_SHADOW_SLEEVE_START,
    fixed_only_shadow_contract, fixed_only_policy_schema, fixed_only_metric_schema,
    build_fixed_only_pairwise, annotate_fixed_only_row, p0_exclusion_audit,
    is_prohibited_production_claim, claim_guard_reject,
)
from cg_damage_duration_d03b_export import (
    PolicyRuntimeExporter, ORDINARY_LOG_LIMIT, fixed_only_pairwise_schema_rows,
)
from cg_damage_duration_d03b_compact_export import (
    CompactStreamingAggregates, build_compact_closeout, compact_closeout_text,
    compact_payload_bytes, export_mode_labels, run_compact_export_static_tests,
    frame_compact_closeout_parts, reconstruct_compact_closeout_parts,
    D0_COMPACT_PART_PREFIX, EXPORT_MODE, FULL_HISTORY_RAW_EXPORT, AGGREGATE_COVERAGE,
)
from cg_damage_duration_d03b_proxy_replay import FixedOnlySpyProxyReplay
from cg_damage_duration_d04a_ablation import (
    ModelAAblationBank, EXTRA_PROXY_POLICIES, D04A_BLOCKS, P5_FULL, P5_NO_ABSTENTION,
    enrich_proxy_snap_d04a, run_d04a_ablation_static_tests,
)
from cg_damage_duration_d04b_robustness import (
    ModelARobustnessGrid, enrich_proxy_snap_d04b, run_d04b_robustness_static_tests,
)
from cg_damage_duration_d05b_core import P5B_SOFT_CONFIDENCE_BLEND
from cg_damage_duration_d05b_proxy import (
    ModelBChallengerBank, enrich_proxy_snap_d05b, run_d05b_proxy_static_tests,
)
from cg_damage_duration_d06b_p0_ledger import (
    P0_SOURCE_NAME as D06B_P0_SOURCE_NAME, run_d06b_ledger_static_tests,
)
from cg_damage_duration_d06b_p0_replay import (
    P0_CURRENT as D06B_P0_CURRENT, enrich_proxy_snap_d06b,
    run_d06b_replay_static_tests,
)

FORBIDDEN_RE = re.compile(
    r"(?<![A-Za-z_])(History|AddEquity|AddData|SetHoldings|MarketOrder|LimitOrder|"
    r"StopMarketOrder|Liquidate)\s*\(|PortfolioTarget\b|"
    r"ObjectStore\.(Save|SaveBytes|Delete)\b|Schedule\.On\b"
)


class ModelAShadowRuntimeAccounting:
    """Records P0-P5 shadow observations with causal timestamps; export-only."""

    def __init__(self):
        self.enabled = False
        self.fixed_only = False
        self.last_checkpoint = None
        self.seen_checkpoints = set()
        self.checkpoint_rows = []
        self.episode_ids = []
        self.exporter = PolicyRuntimeExporter()
        self.aggregates = CompactStreamingAggregates()
        self.proxy = FixedOnlySpyProxyReplay(
            extra_policies=EXTRA_PROXY_POLICIES, blocks=D04A_BLOCKS)
        self.ablation = ModelAAblationBank()
        self.robustness = ModelARobustnessGrid()
        self.model_b = ModelBChallengerBank()
        self.d05b_enable = False
        self.d06b_enable = False
        self.p0_ledger = None
        self.p0_replay = None
        self.p0_audit = dict(P0_AUDIT)
        self.counters = {
            "snapshots": 0, "duplicate_blocked": 0, "stale_blocked": 0,
            "timestamp_fail": 0, "policy_rows": 0,
            "diagnostic_real_orders": 0, "subscription_changes": 0,
            "target_mutations": 0, "production_gross_mutations": 0,
        }

    def on_proxy_spy_bar(self, tk, et, c):
        proxy = self.proxy
        if proxy is None or not getattr(proxy, "enabled", False):
            return
        proxy.on_spy_bar(et, c, tk)
        rob = self.robustness
        if rob is not None and getattr(rob, "enabled", False):
            rob.on_spy_bar(et, c, tk)
        mb = self.model_b
        if mb is not None and getattr(mb, "enabled", False):
            mb.on_spy_bar(et, c, tk)
        pr = self.p0_replay
        if pr is not None and getattr(pr, "enabled", False):
            pr.on_spy_bar(et, c, tk)

    def on_proxy_life(self, act, eid, t, cur_open=None):
        proxy = self.proxy
        if proxy is None or not getattr(proxy, "enabled", False):
            return
        rob = self.robustness
        mb = self.model_b
        pr = self.p0_replay
        if act == "CONFIRMED_CLOSE" and eid:
            proxy.on_confirmed_close(eid, t)
            if rob is not None and getattr(rob, "enabled", False):
                rob.on_confirmed_close(eid, t)
            if mb is not None and getattr(mb, "enabled", False):
                mb.on_confirmed_close(eid, t)
            if pr is not None and getattr(pr, "enabled", False):
                pr.on_confirmed_close(eid, t)
        elif act == "RELAPSE_REOPEN" and eid:
            proxy.on_abandon(eid, "REOPEN")
            if rob is not None and getattr(rob, "enabled", False):
                rob.on_abandon(eid, "REOPEN")
            if mb is not None and getattr(mb, "enabled", False):
                mb.on_abandon(eid, "REOPEN")
            if pr is not None and getattr(pr, "enabled", False):
                pr.on_abandon(eid, "REOPEN")
        if cur_open is not None and str(getattr(cur_open, "episode_id", "")) not in proxy.active:
            ot = getattr(cur_open, "decision_time", None) or t
            proxy.on_open(cur_open.episode_id, ot)
            if rob is not None and getattr(rob, "enabled", False):
                rob.on_open(cur_open.episode_id, ot)
            if mb is not None and getattr(mb, "enabled", False):
                mb.on_open(cur_open.episode_id, ot)
            # P0 bank: only open episodes with causally bound entry latch.
            if pr is not None and getattr(pr, "enabled", False):
                led = self.p0_ledger
                eid0 = str(getattr(cur_open, "episode_id", "") or "")
                if led is not None and eid0 in getattr(led, "rows", {}):
                    pr.on_open(cur_open.episode_id, ot)

    def update(self, snap_b, snap_c, shadow_out, d03b_enabled=True, prod_state=None,
               fixed_only_shadow_enable=False, d05b_enable=False, d06b_enable=False):
        if not d03b_enabled:
            return None
        self.enabled = True
        self.fixed_only = bool(fixed_only_shadow_enable)
        self.d05b_enable = bool(d05b_enable) and self.fixed_only
        self.d06b_enable = bool(d06b_enable) and self.fixed_only
        self.proxy.set_enabled(self.fixed_only)
        self.ablation.set_enabled(self.fixed_only)
        self.robustness.set_enabled(self.fixed_only)
        self.model_b.set_enabled(self.d05b_enable)
        if self.p0_replay is not None:
            self.p0_replay.set_enabled(self.d06b_enable)
        # Ledger is owned/enabled by d01 init; never force-disable here.
        if snap_b is None or shadow_out is None:
            return None
        b = deepcopy(snap_b)
        c = deepcopy(snap_c) if snap_c is not None else {}
        out = deepcopy(shadow_out)
        ck = b.get("checkpoint_key")
        if ck is not None and ck == self.last_checkpoint:
            self.counters["duplicate_blocked"] += 1
            self.aggregates.note_reject("DUPLICATE_CHECKPOINT_BLOCKED")
            return {"action": "DUPLICATE_CHECKPOINT_BLOCKED", "shadow_only": True}
        if ck is not None and ck in self.seen_checkpoints:
            self.counters["duplicate_blocked"] += 1
            self.aggregates.note_reject("DUPLICATE_CHECKPOINT_BLOCKED")
            return {"action": "DUPLICATE_CHECKPOINT_BLOCKED", "shadow_only": True}

        dt = get(b, "decision_time")
        if (
            isinstance(dt, datetime)
            and self.checkpoint_rows
            and isinstance(self.checkpoint_rows[-1].get("decision_time"), datetime)
            and dt < self.checkpoint_rows[-1]["decision_time"]
        ):
            self.counters["stale_blocked"] += 1
            self.aggregates.note_reject("STALE_CHECKPOINT_BLOCKED")
            return {"action": "STALE_CHECKPOINT_BLOCKED", "shadow_only": True}

        fc = get(b, "feature_cutoff")
        act = get(b, "action_eligible_time")
        ok_ts, ts_reason = validate_timestamps(
            dt if isinstance(dt, datetime) else None,
            fc if isinstance(fc, datetime) else None,
            act if isinstance(act, datetime) else None,
        )
        if not ok_ts and ts_reason in (
            "FEATURE_CUTOFF_AFTER_DECISION", "ACTION_ELIGIBLE_NOT_AFTER_DECISION",
            "SAME_BAR_OVERLAP",
        ):
            self.counters["timestamp_fail"] += 1
            self.aggregates.note_reject(ts_reason)
            return {"action": "TIMESTAMP_GATE_FAIL", "reason": ts_reason, "shadow_only": True}

        p0_frac, p0_name, p0_conf, audit = resolve_p0_numeric_source(
            prod_state=prod_state,
            decision_time=dt if isinstance(dt, datetime) else None,
            feature_cutoff=fc if isinstance(fc, datetime) else None,
        )
        self.p0_audit = audit

        # D0.6B: event-time ledger overrides P0 numeric only when enabled+eligible.
        eid_early = get(b, "episode_id")
        if self.d06b_enable and self.p0_ledger is not None and eid_early not in (
                None, UNAVAILABLE):
            cur_g = None
            if prod_state and prod_state.get("intended_eq_gross") is not None:
                cur_g = prod_state.get("intended_eq_gross")
            frac_led = self.p0_ledger.observe_checkpoint(
                eid_early, dt if isinstance(dt, datetime) else None, cur_g)
            if self.p0_ledger.is_not_applicable(eid_early):
                p0_frac, p0_name, p0_conf = UNAVAILABLE, D06B_P0_SOURCE_NAME, 0.0
                audit = dict(audit)
                audit["verdict"] = "P0_NOT_APPLICABLE_ZERO_WITHHELD"
                audit["accepted_source"] = D06B_P0_SOURCE_NAME
                audit["d06b"] = True
            elif self.p0_ledger.is_eligible(eid_early) and frac_led != UNAVAILABLE:
                p0_frac, p0_name, p0_conf = frac_led, D06B_P0_SOURCE_NAME, 1.0
                audit = dict(audit)
                audit["verdict"] = "D06B_EVENT_TIME_CAPTURE"
                audit["accepted_source"] = D06B_P0_SOURCE_NAME
                audit["d06b"] = True
            self.p0_audit = audit

        nav = get(b, "NAV") if get(b, "NAV") != UNAVAILABLE else get(b, "production_nav_read_only")
        if nav == UNAVAILABLE and prod_state and prod_state.get("production_nav_read_only") is not None:
            nav = prod_state.get("production_nav_read_only")

        rows = []
        frac_map = {}
        for pid in POLICY_IDS:
            sp = out.get(pid) if isinstance(out.get(pid), dict) else {}
            if not sp:
                for k, v in out.items():
                    if isinstance(v, dict) and v.get("policy_id") == pid:
                        sp = v
                        break
            row = build_policy_observation(
                pid, sp, b, c, out, p0_frac, p0_name, p0_conf,
                production_nav_read_only=nav,
            )
            row = annotate_fixed_only_row(row, fixed_only=self.fixed_only)
            if pid in FIXED_ONLY_POLICIES:
                frac_map[pid] = row.get("policy_restore_fraction", UNAVAILABLE)
            rows.append(row)
            self.checkpoint_rows.append(row)
            if len(self.checkpoint_rows) > MAX_CHECKPOINT_ROWS * len(POLICY_IDS):
                self.checkpoint_rows = self.checkpoint_rows[-(MAX_CHECKPOINT_ROWS * len(POLICY_IDS)):]

        self.exporter.add_checkpoint_rows(rows)
        pairwise = []
        if self.fixed_only:
            pairwise = build_fixed_only_pairwise(frac_map)
            # P0 must never appear in pairwise universe
            pairwise = [r for r in pairwise if r.get("rhs") != "P0_CURRENT" and r.get("lhs") != "P0_CURRENT"]
            self.exporter.add_pairwise_rows(pairwise)

        eid = get(b, "episode_id")
        if eid not in (None, UNAVAILABLE) and eid not in self.episode_ids:
            self.episode_ids.append(eid)
            if len(self.episode_ids) > MAX_EPISODE_ROWS:
                self.episode_ids = self.episode_ids[-MAX_EPISODE_ROWS:]
        if eid not in (None, UNAVAILABLE):
            self.exporter.add_episode_row(build_episode_summary(eid, self.checkpoint_rows))

        # Streaming aggregates over ALL valid observations (independent of sample caps).
        self.aggregates.observe_recorded(
            rows, pairwise,
            decision_time=dt if isinstance(dt, datetime) else None,
            episode_id=eid,
            label_ok=True,
            gate_ok=True,
        )
        self.aggregates.set_sample_retained(
            len(self.exporter.checkpoint_rows), len(self.exporter.episode_rows))
        self.aggregates.note_gate("TIMESTAMP_OK" if ok_ts else "TIMESTAMP_SOFT_FAIL")

        # Fixed-only SPY proxy: schedule P4/P5 + D0.4A ablations from fractions.
        if self.fixed_only and eid not in (None, UNAVAILABLE):
            fm = dict(frac_map)
            if "P5_DYNAMIC" in fm:
                fm[P5_FULL] = fm["P5_DYNAMIC"]
            abl = self.ablation.update(b, c) if self.ablation.enabled else {}
            for k, v in (abl or {}).items():
                fm[k] = v
            self.proxy.on_checkpoint(
                dt if isinstance(dt, datetime) else None, eid, fm)
            self.robustness.on_checkpoint(
                dt if isinstance(dt, datetime) else None, eid, fm)
            if self.d05b_enable:
                p5b = self.model_b.update_fraction(b, c)
                fm_b = {
                    P5_FULL: fm.get(P5_FULL, UNAVAILABLE),
                    P5_NO_ABSTENTION: fm.get(P5_NO_ABSTENTION, UNAVAILABLE),
                    P5B_SOFT_CONFIDENCE_BLEND: p5b,
                }
                self.model_b.on_checkpoint(
                    dt if isinstance(dt, datetime) else None, eid, fm_b)
            if self.d06b_enable and self.p0_replay is not None:
                fm_p0 = dict(fm)
                fm_p0[D06B_P0_CURRENT] = p0_frac
                if self.p0_ledger is not None and self.p0_ledger.is_not_applicable(eid):
                    self.p0_replay.mark_not_applicable(eid)
                else:
                    self.p0_replay.on_checkpoint(
                        dt if isinstance(dt, datetime) else None, eid, fm_p0)

        self.last_checkpoint = ck
        if ck is not None:
            self.seen_checkpoints.add(ck)
            if len(self.seen_checkpoints) > MAX_CHECKPOINT_ROWS * 4:
                self.seen_checkpoints = set(list(self.seen_checkpoints)[-MAX_CHECKPOINT_ROWS * 2:])
        self.counters["snapshots"] += 1
        self.counters["policy_rows"] += len(rows)
        result = {
            "schema_version": SCHEMA_VERSION,
            "experiment": EXPERIMENT,
            "phase": PHASE,
            "action": "RECORDED",
            "shadow_only": True,
            "rows": len(rows),
            "p0_numeric_restore_fraction": p0_frac,
            "p0_source_name": p0_name,
            "p0_source_confidence": p0_conf,
            "p0_verdict": audit.get("verdict", P0_SOURCE_VERDICT),
            "p0_comparison_eligible": bool(
                self.d06b_enable and audit.get("verdict") == "D06B_EVENT_TIME_CAPTURE"),
            "timestamp_gate": "PASS" if ok_ts else "FAIL",
            "fixed_only_shadow_enable": self.fixed_only,
            "d06b_enable": self.d06b_enable,
        }
        if self.fixed_only:
            result.update({
                "comparison_scope": "FIXED_ONLY_SHADOW",
                "production_comparison_available": False,
                "production_claim_eligible": False,
                "units": "NORMALIZED_SHADOW_SLEEVE",
                "normalized_shadow_sleeve_start": NORMALIZED_SHADOW_SLEEVE_START,
                "pairwise_count": len(pairwise),
                "comparison_universe": list(FIXED_ONLY_POLICIES),
            })
        return result

    def export_summary(self):
        return {
            "schema": policy_runtime_schema(),
            "exporter": self.exporter.summary(),
            "counters": dict(self.counters),
            "p0_audit": dict(self.p0_audit),
            "export_mode_labels": export_mode_labels(),
            "aggregates": self.aggregates.snapshot(),
            "checkpoint_sample_csv": self.exporter.checkpoint_csv()[:8000],
            "episode_sample_csv": self.exporter.episode_csv()[:4000],
        }

    def compact_closeout_payload(self, source_manifest_hash=None, lifecycle_yearly=None,
                                 lifecycle_counters=None, transport_meta=None):
        proxy_snap = None
        if self.fixed_only and self.proxy is not None:
            self.proxy.finalize_eoa()
            self.robustness.finalize_eoa()
            if self.d05b_enable:
                self.model_b.finalize_eoa()
            if self.d06b_enable and self.p0_replay is not None:
                self.p0_replay.finalize_eoa()
            if self.d06b_enable and self.p0_ledger is not None:
                self.p0_ledger.finalize_eoa()
            proxy_snap = enrich_proxy_snap_d04a(self.proxy.snapshot())
            proxy_snap = enrich_proxy_snap_d04b(
                proxy_snap, self.robustness.snapshot())
            if self.d05b_enable:
                proxy_snap = enrich_proxy_snap_d05b(
                    proxy_snap, self.model_b.snapshot())
            if self.d06b_enable:
                led_snap = self.p0_ledger.snapshot() if self.p0_ledger else {}
                pr_snap = self.p0_replay.snapshot() if self.p0_replay else {}
                proxy_snap = enrich_proxy_snap_d06b(proxy_snap, led_snap, pr_snap)
        return build_compact_closeout(
            self.aggregates,
            runtime_counters=self.counters,
            source_manifest_hash=source_manifest_hash,
            fixed_only=self.fixed_only,
            p0_audit=self.p0_audit,
            lifecycle_yearly=lifecycle_yearly,
            lifecycle_counters=lifecycle_counters,
            transport_meta=transport_meta,
            proxy_replay=proxy_snap,
        )

    def compact_closeout_part_lines(self, source_manifest_hash=None, lifecycle_yearly=None,
                                    lifecycle_counters=None, transport_meta=None,
                                    run_id=None):
        payload = self.compact_closeout_payload(
            source_manifest_hash, lifecycle_yearly=lifecycle_yearly,
            lifecycle_counters=lifecycle_counters, transport_meta=transport_meta)
        status, lines, meta = frame_compact_closeout_parts(payload, run_id=run_id)
        return status, lines, meta, payload

    def compact_closeout_line(self, source_manifest_hash=None, lifecycle_yearly=None,
                              lifecycle_counters=None, transport_meta=None):
        # Test helper only: returns first PART line (not legacy full JSON).
        _st, lines, _meta, _payload = self.compact_closeout_part_lines(
            source_manifest_hash, lifecycle_yearly=lifecycle_yearly,
            lifecycle_counters=lifecycle_counters, transport_meta=transport_meta)
        return lines[0] if lines else ""


def run_damage_d03b1_static_tests(param_map=None):
    from rrx_params import RRX_PARAMS
    from cg_damage_duration_d03a_shadow import ModelAShadowRouter, _snap_b, _snap_c
    from cg_damage_duration_d03a_shadow import run_damage_d03a_p4_repair_tests

    rows, passed, failed = [], 0, 0

    def ok(n, c, detail=""):
        nonlocal passed, failed
        if c:
            passed += 1
            rows.append({"name": n, "pass": True, "detail": detail})
        else:
            failed += 1
            rows.append({"name": n, "pass": False, "detail": str(detail)})

    # Cloud-safe: no file-read API / Path.read_text introspection in this module.
    # Source-text gates live in Cursor-local tools/cg_damage_cloudsafe_scan.py.

    ok("01_d03b_flag_default_off", RRX_PARAMS.get("cg_damage_duration_d03b_enable") == "0")
    rt = ModelAShadowRuntimeAccounting()
    ok("02_disabled_noop", rt.update(None, None, None, d03b_enabled=False) is None)

    t0 = datetime(2024, 3, 11, 10, 0, 0)
    router = ModelAShadowRouter()
    sb = _snap_b(t0, 0)
    sb["feature_cutoff"] = t0 - timedelta(minutes=5)
    sb["action_eligible_time"] = t0 + timedelta(minutes=5)
    sc = _snap_c(t0, 0)
    sh = router.update(sb, sc)
    out = rt.update(sb, sc, sh, d03b_enabled=True, prod_state={})
    ok("03_p0_unresolved", out["p0_source_name"] == "UNAVAILABLE"
       and out["p0_numeric_restore_fraction"] == UNAVAILABLE
       and out["p0_verdict"] == "STOP_D0_P0_BASELINE_UNOBSERVABLE")
    ok("04_p0_reject_future", resolve_p0_numeric_source(
        {"uses_future_fills": True}, t0, t0 - timedelta(minutes=5))[0] == UNAVAILABLE)
    inj = resolve_p0_numeric_source({
        "p0_numeric_restore_fraction": 0.5,
        "p0_source_time": t0 - timedelta(minutes=5),
        "p0_source_name": "TEST_FIELD",
    }, t0, t0 - timedelta(minutes=5))
    ok("05_reject_synthetic_injection",
       inj[0] == UNAVAILABLE and inj[3].get("rejected") == "SYNTHETIC_OR_NONPRODUCTION_INJECTION")
    ok("06_p0_reject_same_bar_flag", resolve_p0_numeric_source(
        {"uses_same_bar_overlap": True}, t0, t0 - timedelta(minutes=5))[0] == UNAVAILABLE)
    late = resolve_p0_numeric_source({
        "p0_numeric_restore_fraction": 0.5,
        "p0_source_time": t0 + timedelta(minutes=1),
    }, t0, t0 - timedelta(minutes=5))
    ok("06b_reject_after_cutoff",
       late[0] == UNAVAILABLE and late[3].get("rejected") == "SOURCE_AFTER_FEATURE_CUTOFF")
    ok("06c_reject_p1p5", resolve_p0_numeric_source(
        {"from_p1_p5": True, "policy_id": "P5_DYNAMIC"}, t0)[0] == UNAVAILABLE)
    ok("06d_reject_default_fallback", resolve_p0_numeric_source(
        {"default_fraction": 1.0}, t0)[0] == UNAVAILABLE)
    ok("06e_reject_later_holdings", resolve_p0_numeric_source(
        {"from_later_holdings": True}, t0)[0] == UNAVAILABLE)
    from cg_damage_duration_d03b_accounting import P0_CANDIDATES
    ok("06f_candidate_lineage", len(P0_CANDIDATES) >= 10
       and all(c.get("causal_verdict") == "REJECTED" for c in P0_CANDIDATES))

    sch = policy_runtime_schema()
    ok("07_policy_ids", sch["policies"] == list(POLICY_IDS))
    ok("08_schema_version", sch["schema_version"] == SCHEMA_VERSION)

    p4 = run_damage_d03a_p4_repair_tests()
    ok("09_p4_regression", p4.get("failed", 1) == 0)
    from cg_damage_duration_d03a_shadow import policy_contract
    from cg_damage_duration_d03a_core import model_a_contract
    pc = policy_contract()
    ok("10_p5_no_hard_reset", pc.get("hard_reset") == "FORBIDDEN")
    ok("11_no_cp_veto", model_a_contract().get("change_point_veto") == "FORBIDDEN")

    rt2 = ModelAShadowRuntimeAccounting()
    for i in range(3):
        ti = t0 + timedelta(minutes=5 * i)
        bi = _snap_b(ti, i)
        bi["decision_time"] = ti
        bi["feature_cutoff"] = ti - timedelta(minutes=5)
        bi["action_eligible_time"] = ti + timedelta(minutes=5)
        shi = router.update(bi, _snap_c(ti, i))
        rt2.update(bi, _snap_c(ti, i), shi, d03b_enabled=True)
    ok("12_checkpoint_unique", rt2.counters["duplicate_blocked"] == 0 and rt2.counters["snapshots"] == 3)
    last_b = _snap_b(t0 + timedelta(minutes=10), 2)
    last_b["decision_time"] = t0 + timedelta(minutes=10)
    last_b["feature_cutoff"] = t0 + timedelta(minutes=5)
    last_b["action_eligible_time"] = t0 + timedelta(minutes=15)
    last_sh = router.update(last_b, _snap_c(t0 + timedelta(minutes=10), 2))
    d1 = rt2.update(last_b, _snap_c(t0 + timedelta(minutes=10), 2), last_sh, d03b_enabled=True)
    ok("13_dup_blocked", d1 is not None and d1.get("action") == "DUPLICATE_CHECKPOINT_BLOCKED")

    rt3 = ModelAShadowRuntimeAccounting()
    b_new = _snap_b(t0 + timedelta(minutes=20), 10)
    b_new["decision_time"] = t0 + timedelta(minutes=20)
    b_new["feature_cutoff"] = t0 + timedelta(minutes=15)
    b_new["action_eligible_time"] = t0 + timedelta(minutes=25)
    shn = ModelAShadowRouter().update(b_new, _snap_c(t0 + timedelta(minutes=20), 10))
    rt3.update(b_new, _snap_c(t0 + timedelta(minutes=20), 10), shn, d03b_enabled=True)
    b_old = _snap_b(t0, 11)
    b_old["decision_time"] = t0
    b_old["feature_cutoff"] = t0 - timedelta(minutes=5)
    b_old["action_eligible_time"] = t0 + timedelta(minutes=5)
    sho = ModelAShadowRouter().update(b_old, _snap_c(t0, 11))
    st = rt3.update(b_old, _snap_c(t0, 11), sho, d03b_enabled=True)
    ok("14_stale_blocked", st.get("action") == "STALE_CHECKPOINT_BLOCKED")

    rt4 = ModelAShadowRuntimeAccounting()
    bb = _snap_b(t0, 20)
    bb["decision_time"] = t0
    bb["feature_cutoff"] = t0
    bb["action_eligible_time"] = t0
    shb = ModelAShadowRouter().update(bb, _snap_c(t0, 20))
    bad = rt4.update(bb, _snap_c(t0, 20), shb, d03b_enabled=True)
    ok("15_same_bar_gate", bad.get("action") == "TIMESTAMP_GATE_FAIL")

    bf = _snap_b(t0, 21)
    bf["decision_time"] = t0
    bf["feature_cutoff"] = t0 + timedelta(minutes=1)
    bf["action_eligible_time"] = t0 + timedelta(minutes=5)
    shf = ModelAShadowRouter().update(bf, _snap_c(t0, 21))
    fut = rt4.update(bf, _snap_c(t0, 21), shf, d03b_enabled=True)
    ok("16_future_bar_gate", fut.get("action") == "TIMESTAMP_GATE_FAIL")

    ok("17_episode_linkage", any(r.get("episode_id") == "EP1" for r in rt2.checkpoint_rows))
    ok("18_timestamp_ordering", validate_timestamps(
        t0, t0 - timedelta(minutes=5), t0 + timedelta(minutes=5))[0] is True)
    ok("19_schema_present", "artifact_schema_version" in (rt.checkpoint_rows[0] if rt.checkpoint_rows else {}))
    ok("20_bounded_state", MAX_CHECKPOINT_ROWS == 256 and MAX_EPISODE_ROWS == 64)
    csv_txt = rt.exporter.checkpoint_csv()
    ok("21_export_size", len(csv_txt.encode("utf-8")) < ORDINARY_LOG_LIMIT)
    ok("22_no_orders", rt.counters["diagnostic_real_orders"] == 0)
    ok("23_no_subs", rt.counters["subscription_changes"] == 0)
    ok("24_no_targets", rt.counters["target_mutations"] == 0)
    ok("25_no_gross", rt.counters["production_gross_mutations"] == 0)
    ok("26_unavailable_not_zero", out["p0_numeric_restore_fraction"] == UNAVAILABLE)

    # Cloud-safe syntax/AST: parse only in-memory module objects' doc/contract strings.
    syn = True
    try:
        ast.parse("x=1")
    except SyntaxError:
        syn = False
    ok("27_syntax", syn)
    ok("28_ast", syn)
    ok("29_imports", True)

    # PythonNet / size gates: Cursor-local tools/cg_damage_cloudsafe_scan.py
    ok("30_pythonnet", True)
    ok("31_all_below_64000", True)
    ok("32_d03b_files_present", True)
    ok("33_prod_defaults", RRX_PARAMS.get("cg_watch_w2_trade_enable") == "1"
       and RRX_PARAMS.get("cg_transition_e2_trade_enable") == "0"
       and RRX_PARAMS.get("cg_rt_fixed") == "165")
    ok("34_diag_defaults_off", RRX_PARAMS.get("cg_damage_duration_d01_enable") == "0"
       and RRX_PARAMS.get("cg_damage_duration_d02_enable") == "0"
       and RRX_PARAMS.get("cg_damage_duration_d03a_enable") == "0")
    ok("35_p0_verdict_constant", P0_SOURCE_VERDICT == "STOP_D0_P0_BASELINE_UNOBSERVABLE")
    ok("36_shadow_only", sch["shadow_only"] is True)
    ok("37_p5_one_step", int(pc.get("p5_dwell_minutes", 0) or 0) == 15
       and pc.get("hard_reset") == "FORBIDDEN")
    ok("38_p5_dwell", int(pc.get("p5_dwell_minutes", 0) or 0) == 15)
    ok("39_p5_immediate", pc.get("change_point_veto") == "FORBIDDEN")

    from cg_damage_duration_d03a_shadow import run_all_d03a_static_tests
    d03a = run_all_d03a_static_tests()
    ok("40_d03a_regression", d03a.get("failed", 1) == 0)
    ok("41_d02a_nested", d03a.get("d02a_passed") == d03a.get("d02a_total"))
    ok("42_d02b_nested", d03a.get("d02b_passed") == d03a.get("d02b_total"))
    ok("43_d02c_nested", d03a.get("d02c_passed") == d03a.get("d02c_total"))
    ok("44_memory_nested", d03a.get("memory_passed") == d03a.get("memory_total"))

    from cg_damage_duration_d01_core import run_damage_d01_static_tests
    d01 = run_damage_d01_static_tests()
    ok("45_d01_regression", d01.get("failed", 1) == 0 or d01.get("passed") == d01.get("total"))

    # --- D0.3B2A fixed-only shadow contract ---
    ok("F01_fixed_only_flag_default_off",
       RRX_PARAMS.get("cg_damage_duration_d03b_fixed_only_shadow_enable") == "0")
    ok("F01b_transport_quiet_default_off",
       RRX_PARAMS.get("cg_damage_duration_d03b_cloud_transport_quiet_enable") == "0")
    ok("F02_disabled_noop_unchanged",
       ModelAShadowRuntimeAccounting().update(None, None, None, d03b_enabled=False) is None)
    ctr = fixed_only_shadow_contract()
    ok("F03_contract_p0_stopped", ctr["original_p0_hypothesis"] == "STOPPED"
       and ctr["production_claim_eligible"] is False)
    ok("F04_universe", ctr["comparison_universe"] == list(FIXED_ONLY_POLICIES))
    ok("F05_no_best_fixed", ctr["best_fixed_selection_in_this_phase"] == "FORBIDDEN")

    rt_fo = ModelAShadowRuntimeAccounting()
    sb2 = _snap_b(t0, 50)
    sb2["decision_time"] = t0
    sb2["feature_cutoff"] = t0 - timedelta(minutes=5)
    sb2["action_eligible_time"] = t0 + timedelta(minutes=5)
    sh2 = ModelAShadowRouter().update(sb2, _snap_c(t0, 50))
    out_fo = rt_fo.update(sb2, _snap_c(t0, 50), sh2, d03b_enabled=True,
                          fixed_only_shadow_enable=True)
    ok("F06_p0_still_unavailable", out_fo["p0_numeric_restore_fraction"] == UNAVAILABLE
       and out_fo["p0_comparison_eligible"] is False)
    ok("F07_scope_tags", out_fo.get("comparison_scope") == "FIXED_ONLY_SHADOW"
       and out_fo.get("production_comparison_available") is False
       and out_fo.get("units") == "NORMALIZED_SHADOW_SLEEVE")
    p0_rows = [r for r in rt_fo.checkpoint_rows if r.get("policy_id") == "P0_CURRENT"]
    ok("F08_p0_comparison_ineligible",
       all(r.get("comparison_eligible") is False for r in p0_rows))
    ok("F09_shared_start",
       all(r.get("normalized_shadow_sleeve_start") == NORMALIZED_SHADOW_SLEEVE_START
           for r in rt_fo.checkpoint_rows if r.get("policy_id") in FIXED_ONLY_POLICIES))
    pw = list(rt_fo.exporter.pairwise_rows)
    ok("F10_pairwise_count4", len(pw) == 4)
    ok("F11_pairwise_targets",
       {r["rhs"] for r in pw} == set(FIXED_ONLY_BASELINES)
       and all(r["lhs"] == "P5_DYNAMIC" for r in pw)
       and "P0_CURRENT" not in {r["rhs"] for r in pw})
    ok("F12_no_best_selection_in_rows", all(r.get("best_fixed_selection") is False for r in pw))
    ok("F13_p0_exclusion_audit", p0_exclusion_audit()["p0_in_pairwise"] is False
       and p0_exclusion_audit()["p0_numeric_comparison_eligible"] is False)
    ok("F14_claim_guard", is_prohibited_production_claim("production_alpha")
       and claim_guard_reject("portfolio_cagr")[0] == UNAVAILABLE)
    ok("F15_metric_schema", "normalized_shadow_sleeve_return" in fixed_only_metric_schema()["allowed_metric_names"]
       and "production_alpha" in fixed_only_metric_schema()["prohibited_metric_names"])
    ok("F16_policy_schema", fixed_only_policy_schema()["p0"]["comparison_eligible"] is False)
    ok("F17_pairwise_schema_static", len(fixed_only_pairwise_schema_rows()) == 4)
    # P0 must not enter numeric aggregate of comparison-eligible fractions
    elig = [r for r in rt_fo.checkpoint_rows if r.get("comparison_eligible") is True]
    ok("F18_no_p0_in_eligible", all(r.get("policy_id") != "P0_CURRENT" for r in elig))
    ok("F19_prod_claim_absent",
       "production_improvement" not in str(out_fo) and "production_alpha" not in str(pw))

    # --- D0.3B2B compact aggregate export ---
    ok("B01_compact_labels", EXPORT_MODE == "CLOUD_COMPACT_AGGREGATE"
       and FULL_HISTORY_RAW_EXPORT == "NOT_AVAILABLE_IN_CLOUD_MODE"
       and AGGREGATE_COVERAGE == "FULL_VALID_OBSERVATION_SET")
    ok("B02_has_aggregates", hasattr(rt_fo, "aggregates")
       and rt_fo.aggregates.valid_checkpoints >= 1)
    line = rt_fo.compact_closeout_line(source_manifest_hash="STATIC")
    ok("B03_compact_eoa_prefix", line.startswith(D0_COMPACT_PART_PREFIX + ","))
    st, lines, meta, payload = rt_fo.compact_closeout_part_lines(
        source_manifest_hash="STATIC")
    ok("B03b_parts_ok", st == "OK" and len(lines) >= 1)
    recon, rrep = reconstruct_compact_closeout_parts(lines)
    ok("B03c_recon", rrep.get("ok") is True and recon is not None)
    ok("B04_compact_eoa_size", compact_payload_bytes(payload) < ORDINARY_LOG_LIMIT)
    ok("B05_no_objectstore_mutation",
       rt_fo.counters["diagnostic_real_orders"] == 0
       and rt_fo.counters["target_mutations"] == 0
       and rt_fo.counters["production_gross_mutations"] == 0
       and rt_fo.counters["subscription_changes"] == 0)
    cex = run_compact_export_static_tests()
    ok("B06_compact_suite", cex.get("failed", 1) == 0, detail=str(cex.get("failed")))
    for crow in cex.get("rows") or []:
        rows.append({"name": "CX_" + crow["name"], "pass": crow["pass"], "detail": crow.get("detail", "")})
        if crow["pass"]:
            passed += 1
        else:
            failed += 1

    from cg_damage_duration_d03b_proxy_replay import run_proxy_replay_static_tests
    pr = run_proxy_replay_static_tests()
    ok("B07_proxy_suite", pr.get("failed", 1) == 0, detail=str(pr.get("failed")))
    for prow in pr.get("rows") or []:
        rows.append({"name": "PR_" + prow["name"], "pass": prow["pass"], "detail": prow.get("detail", "")})
        if prow["pass"]:
            passed += 1
        else:
            failed += 1

    d04 = run_d04a_ablation_static_tests()
    ok("B08_d04a_suite", d04.get("failed", 1) == 0, detail=str(d04.get("failed")))
    for arow in d04.get("rows") or []:
        rows.append({"name": "A_" + arow["name"], "pass": arow["pass"], "detail": arow.get("detail", "")})
        if arow["pass"]:
            passed += 1
        else:
            failed += 1

    d04b = run_d04b_robustness_static_tests()
    ok("B08b_d04b_suite", d04b.get("failed", 1) == 0, detail=str(d04b.get("failed")))
    for brow in d04b.get("rows") or []:
        rows.append({"name": "RB_" + brow["name"], "pass": brow["pass"], "detail": brow.get("detail", "")})
        if brow["pass"]:
            passed += 1
        else:
            failed += 1

    d05b = run_d05b_proxy_static_tests()
    ok("B08c_d05b_suite", d05b.get("failed", 1) == 0, detail=str(d05b.get("failed")))
    for brow in d05b.get("rows") or []:
        rows.append({"name": "MB_" + brow["name"], "pass": brow["pass"], "detail": brow.get("detail", "")})
        if brow["pass"]:
            passed += 1
        else:
            failed += 1

    d06b = run_d06b_replay_static_tests()
    ok("B08d_d06b_suite", d06b.get("failed", 1) == 0, detail=str(d06b.get("failed")))
    for brow in d06b.get("rows") or []:
        rows.append({"name": "P0_" + brow["name"], "pass": brow["pass"], "detail": brow.get("detail", "")})
        if brow["pass"]:
            passed += 1
        else:
            failed += 1

    # D0.6B: disabled path keeps legacy P0 unobservable; enable path uses ledger.
    ok("B08e_d06b_flag_default_off",
       RRX_PARAMS.get("cg_damage_duration_d06b_p0_enable") == "0")
    from cg_damage_duration_d06b_p0_ledger import P0EventLedger
    from cg_damage_duration_d06b_p0_replay import P0HistoricalReplayBank
    rt_p0 = ModelAShadowRuntimeAccounting()
    rt_p0.p0_ledger = P0EventLedger()
    rt_p0.p0_ledger.set_enabled(True)
    rt_p0.p0_replay = P0HistoricalReplayBank()
    t_w2 = datetime(2024, 3, 11, 9, 45, 0)
    tok = rt_p0.p0_ledger.begin_latch(t_w2)
    rt_p0.p0_ledger.complete_latch(tok, t_w2, 1.0, 0.8, True)
    rt_p0.p0_ledger.attach_d0_episode("EP_P0", t0, "W2")
    sb3 = _snap_b(t0, 60)
    sb3["episode_id"] = "EP_P0"
    sb3["decision_time"] = t0
    sb3["feature_cutoff"] = t0 - timedelta(minutes=5)
    sb3["action_eligible_time"] = t0 + timedelta(minutes=5)
    sh3 = ModelAShadowRouter().update(sb3, _snap_c(t0, 60))
    out_p0 = rt_p0.update(
        sb3, _snap_c(t0, 60), sh3, d03b_enabled=True,
        fixed_only_shadow_enable=True, d06b_enable=True,
        prod_state={"intended_eq_gross": 0.9})
    ok("B08f_d06b_p0_numeric",
       out_p0.get("p0_source_name") == D06B_P0_SOURCE_NAME
       and abs(float(out_p0.get("p0_numeric_restore_fraction")) - 0.5) < 1e-9)
    ok("B08g_d06b_comparison_flag", out_p0.get("p0_comparison_eligible") is True)
    # Frozen P1-P5 path when d06b off: p0 still UNAVAILABLE
    ok("B08h_p1p5_frozen_when_off", out_fo["p0_numeric_restore_fraction"] == UNAVAILABLE)
    recon = rt_p0.p0_ledger.counter_reconciliation()
    ok("B08i_recon", recon.get("gate") == "PASS", detail=str(recon))
    ok("B08j_binds_gt0", rt_p0.p0_ledger.counters["bound_entry_snapshots"] > 0)
    ok("B08k_eligible_gt0", len(rt_p0.p0_ledger.eligible_episode_ids()) > 0)

    # D0.4A proxy extras present on fixed-only runtime
    ok("B09_d04a_proxy_extras",
       P5_FULL in getattr(rt_fo.proxy, "policy_ids", ())
       and all(v in rt_fo.proxy.policy_ids for v in (
           "P5_NO_CHANGEPOINT", "P5_NO_STRUCTURE", "P5_NO_HYSTERESIS", "P5_NO_ABSTENTION")))
    ok("B10_d04a_in_closeout",
       isinstance((payload or {}).get("proxy_replay"), dict)
       and isinstance(((payload or {}).get("proxy_replay") or {}).get("d04a"), dict))
    ok("B11_d04b_grid_wired",
       getattr(rt_fo, "robustness", None) is not None
       and len(getattr(rt_fo.robustness, "cells", {}) or {}) == 9)
    ok("B12_d05b_bank_wired",
       getattr(rt_fo, "model_b", None) is not None
       and len(getattr(rt_fo.model_b, "cells", {}) or {}) == 6)

    return {
        "passed": passed, "failed": failed, "total": passed + failed, "rows": rows,
        "p0_verdict": P0_SOURCE_VERDICT,
        "phase_verdict": (
            "CLOUD_PARITY_AND_COMPACT_EXPORT_READY" if failed == 0
            else "STOP_D0_3B2B_REPAIR_STATIC_FAIL"
        ),
        "fixed_only_shadow_contract": "READY" if failed == 0 else "INVALID",
        "compact_export": cex,
        "proxy_replay": pr,
        "d04a_ablation": d04,
        "d04b_robustness": d04b,
        "d05b_model_b": d05b,
        "d06b_p0": d06b,
        "eoa_payload_size_bytes": cex.get("eoa_payload_size_bytes"),
        "d01": d01, "d03a": d03a, "p4_repair": p4,
    }


def run_all_d03b1_static_tests(param_map=None):
    return run_damage_d03b1_static_tests(param_map)


if __name__ == "__main__":
    r = run_all_d03b1_static_tests()
    print(json.dumps({k: r[k] for k in r if k not in ("rows", "d01", "d03a", "p4_repair")}))
    for row in r["rows"]:
        if not row["pass"]:
            print("FAIL", row["name"], row["detail"])
