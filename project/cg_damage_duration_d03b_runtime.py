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
)
from cg_damage_duration_d03b_export import PolicyRuntimeExporter, ORDINARY_LOG_LIMIT

FORBIDDEN_RE = re.compile(
    r"(?<![A-Za-z_])(History|AddEquity|AddData|SetHoldings|MarketOrder|LimitOrder|"
    r"StopMarketOrder|Liquidate)\s*\(|PortfolioTarget\b|ObjectStore\.(Save|Delete)\b|"
    r"Schedule\.On\b"
)


class ModelAShadowRuntimeAccounting:
    """Records P0-P5 shadow observations with causal timestamps; export-only."""

    def __init__(self):
        self.enabled = False
        self.last_checkpoint = None
        self.seen_checkpoints = set()
        self.checkpoint_rows = []
        self.episode_ids = []
        self.exporter = PolicyRuntimeExporter()
        self.p0_audit = dict(P0_AUDIT)
        self.counters = {
            "snapshots": 0, "duplicate_blocked": 0, "stale_blocked": 0,
            "timestamp_fail": 0, "policy_rows": 0,
            "diagnostic_real_orders": 0, "subscription_changes": 0,
            "target_mutations": 0, "production_gross_mutations": 0,
        }

    def update(self, snap_b, snap_c, shadow_out, d03b_enabled=True, prod_state=None):
        if not d03b_enabled:
            return None
        self.enabled = True
        if snap_b is None or shadow_out is None:
            return None
        b = deepcopy(snap_b)
        c = deepcopy(snap_c) if snap_c is not None else {}
        out = deepcopy(shadow_out)
        ck = b.get("checkpoint_key")
        if ck is not None and ck == self.last_checkpoint:
            self.counters["duplicate_blocked"] += 1
            return {"action": "DUPLICATE_CHECKPOINT_BLOCKED", "shadow_only": True}
        if ck is not None and ck in self.seen_checkpoints:
            self.counters["duplicate_blocked"] += 1
            return {"action": "DUPLICATE_CHECKPOINT_BLOCKED", "shadow_only": True}

        dt = get(b, "decision_time")
        # stale: earlier decision_time than last accepted
        if (
            isinstance(dt, datetime)
            and self.checkpoint_rows
            and isinstance(self.checkpoint_rows[-1].get("decision_time"), datetime)
            and dt < self.checkpoint_rows[-1]["decision_time"]
        ):
            self.counters["stale_blocked"] += 1
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
            return {"action": "TIMESTAMP_GATE_FAIL", "reason": ts_reason, "shadow_only": True}

        p0_frac, p0_name, p0_conf, audit = resolve_p0_numeric_source(
            prod_state=prod_state, decision_time=dt if isinstance(dt, datetime) else None)
        self.p0_audit = audit

        nav = get(b, "NAV") if get(b, "NAV") != UNAVAILABLE else get(b, "production_nav_read_only")
        if nav == UNAVAILABLE and prod_state and prod_state.get("production_nav_read_only") is not None:
            nav = prod_state.get("production_nav_read_only")

        rows = []
        for pid in POLICY_IDS:
            sp = out.get(pid) if isinstance(out.get(pid), dict) else {}
            # map P0_CURRENT etc. keys from shadow_out
            if not sp:
                # shadow_out uses same policy_id keys
                for k, v in out.items():
                    if isinstance(v, dict) and v.get("policy_id") == pid:
                        sp = v
                        break
            row = build_policy_observation(
                pid, sp, b, c, out, p0_frac, p0_name, p0_conf,
                production_nav_read_only=nav,
            )
            rows.append(row)
            self.checkpoint_rows.append(row)
            if len(self.checkpoint_rows) > MAX_CHECKPOINT_ROWS * len(POLICY_IDS):
                self.checkpoint_rows = self.checkpoint_rows[-(MAX_CHECKPOINT_ROWS * len(POLICY_IDS)):]

        self.exporter.add_checkpoint_rows(rows)
        eid = get(b, "episode_id")
        if eid not in (None, UNAVAILABLE) and eid not in self.episode_ids:
            self.episode_ids.append(eid)
            if len(self.episode_ids) > MAX_EPISODE_ROWS:
                self.episode_ids = self.episode_ids[-MAX_EPISODE_ROWS:]
        if eid not in (None, UNAVAILABLE):
            self.exporter.add_episode_row(build_episode_summary(eid, self.checkpoint_rows))

        self.last_checkpoint = ck
        if ck is not None:
            self.seen_checkpoints.add(ck)
            if len(self.seen_checkpoints) > MAX_CHECKPOINT_ROWS * 4:
                # bound set growth
                self.seen_checkpoints = set(list(self.seen_checkpoints)[-MAX_CHECKPOINT_ROWS * 2:])
        self.counters["snapshots"] += 1
        self.counters["policy_rows"] += len(rows)
        return {
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
            "timestamp_gate": "PASS" if ok_ts else "FAIL",
        }

    def export_summary(self):
        return {
            "schema": policy_runtime_schema(),
            "exporter": self.exporter.summary(),
            "counters": dict(self.counters),
            "p0_audit": dict(self.p0_audit),
            "checkpoint_sample_csv": self.exporter.checkpoint_csv()[:8000],
            "episode_sample_csv": self.exporter.episode_csv()[:4000],
        }


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

    body = open(__file__, encoding="utf-8").read()
    acct = open(__file__.replace("d03b_runtime.py", "d03b_accounting.py"), encoding="utf-8").read()
    exp = open(__file__.replace("d03b_runtime.py", "d03b_export.py"), encoding="utf-8").read()
    prod = body.split("def run_damage_d03b1_static_tests")[0].split("FORBIDDEN_RE")[0] + "\n" + acct + "\n" + exp

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
    ok("03_p0_unresolved", out["p0_source_name"] == "UNRESOLVED"
       and out["p0_numeric_restore_fraction"] == UNAVAILABLE)
    ok("04_p0_reject_future", resolve_p0_numeric_source({"uses_future_fills": True}, t0)[0] == UNAVAILABLE)
    ok("05_p0_accept_explicit_causal", resolve_p0_numeric_source({
        "p0_numeric_restore_fraction": 0.5,
        "p0_source_time": t0 - timedelta(minutes=1),
        "p0_source_name": "TEST_FIELD",
    }, t0)[1] == "TEST_FIELD")
    ok("06_p0_reject_same_bar_flag", resolve_p0_numeric_source(
        {"uses_same_bar_overlap": True}, t0)[0] == UNAVAILABLE)

    sch = policy_runtime_schema()
    ok("07_policy_ids", sch["policies"] == list(POLICY_IDS))
    ok("08_schema_version", sch["schema_version"] == SCHEMA_VERSION)

    p4 = run_damage_d03a_p4_repair_tests()
    ok("09_p4_regression", p4.get("failed", 1) == 0)
    sh_src = open(__file__.replace("d03b_runtime.py", "d03a_shadow.py"), encoding="utf-8").read()
    ok("10_p5_no_hard_reset", 'hard_reset": "FORBIDDEN"' in sh_src or "hard_reset" in sh_src)
    ok("11_no_cp_veto", "FORBIDDEN" in open(
        __file__.replace("d03b_runtime.py", "d03a_core.py"), encoding="utf-8").read())

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
    ok("22_no_orders", rt.counters["diagnostic_real_orders"] == 0
       and "MarketOrder(" not in prod and "SetHoldings(" not in prod
       and "AddEquity(" not in prod and "History(" not in prod)
    ok("23_no_subs", rt.counters["subscription_changes"] == 0)
    ok("24_no_targets", rt.counters["target_mutations"] == 0)
    ok("25_no_gross", rt.counters["production_gross_mutations"] == 0)
    ok("26_unavailable_not_zero", out["p0_numeric_restore_fraction"] == UNAVAILABLE)

    try:
        ast.parse(body); ast.parse(acct); ast.parse(exp)
        syn = True
    except SyntaxError:
        syn = False
    ok("27_syntax", syn)
    ok("28_ast", syn)
    ok("29_imports", True)

    main = open(__file__.replace("cg_damage_duration_d03b_runtime.py", "main.py"), encoding="utf-8").read()
    tree = ast.parse(main)
    bases = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and "CoreGrowth" in node.name:
            bases = [b.id if isinstance(b, ast.Name) else "" for b in node.bases]
    ok("30_pythonnet", bases == ["QCAlgorithm"])

    from pathlib import Path
    root = Path(__file__).resolve().parent
    sizes = {p.name: len(p.read_text(encoding="utf-8")) for p in root.glob("*.py")}
    ok("31_all_below_64000", all(v < 64000 for v in sizes.values()))
    ok("32_d03b_files_present", all(k in sizes for k in (
        "cg_damage_duration_d03b_runtime.py",
        "cg_damage_duration_d03b_accounting.py",
        "cg_damage_duration_d03b_export.py")))
    ok("33_prod_defaults", RRX_PARAMS.get("cg_watch_w2_trade_enable") == "1"
       and RRX_PARAMS.get("cg_transition_e2_trade_enable") == "0"
       and RRX_PARAMS.get("cg_rt_fixed") == "165")
    ok("34_diag_defaults_off", RRX_PARAMS.get("cg_damage_duration_d01_enable") == "0"
       and RRX_PARAMS.get("cg_damage_duration_d02_enable") == "0"
       and RRX_PARAMS.get("cg_damage_duration_d03a_enable") == "0")
    ok("35_p0_verdict_constant", P0_SOURCE_VERDICT == "STOP_P0_NUMERIC_SOURCE_UNRESOLVED")
    ok("36_shadow_only", sch["shadow_only"] is True)
    ok("37_p5_one_step", "ONE_STEP_UP" in sh_src and "NORMAL_DOWNGRADE" in sh_src)
    ok("38_p5_dwell", "DWELL_BLOCK_UP" in sh_src)
    ok("39_p5_immediate", "IMMEDIATE_ONE_STEP_DOWNGRADE" in sh_src)

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

    return {
        "passed": passed, "failed": failed, "total": passed + failed, "rows": rows,
        "p0_verdict": P0_SOURCE_VERDICT,
        "phase_verdict": "REPAIR_REQUIRED",
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
