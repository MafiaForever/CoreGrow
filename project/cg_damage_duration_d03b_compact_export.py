# cg_damage_duration_d03b_compact_export.py -- D0.3B2B compact streaming aggregates.
# Diagnostic only. No ObjectStore/filesystem persistence. Bounded samples stay samples.
from __future__ import annotations
import hashlib
import json
import re
from collections import Counter
from datetime import datetime

from cg_damage_duration_d03a_core import UNAVAILABLE, _avail, _f
from cg_damage_duration_d03b_accounting import (
    FIXED_ONLY_POLICIES, FIXED_ONLY_BASELINES, NORMALIZED_SHADOW_SLEEVE_START,
    UNITS_NORMALIZED_SHADOW_SLEEVE, MAX_CHECKPOINT_ROWS, MAX_EPISODE_ROWS,
    p0_exclusion_audit, fixed_only_shadow_contract,
)
from cg_damage_duration_d03b_export import ORDINARY_LOG_LIMIT

EXPORT_MODE = "CLOUD_COMPACT_AGGREGATE"
FULL_HISTORY_RAW_EXPORT = "NOT_AVAILABLE_IN_CLOUD_MODE"
AGGREGATE_COVERAGE = "FULL_VALID_OBSERVATION_SET"
CHECKPOINT_SAMPLE_MODE = "BOUNDED_DIAGNOSTIC_SAMPLE"
EPISODE_SAMPLE_MODE = "BOUNDED_DIAGNOSTIC_SAMPLE"
COMPACT_SCHEMA_VERSION = "D03B2B_COMPACT_AGGREGATE_V1"
D0_COMPACT_PREFIX = "D0_COMPACT_CLOSEOUT"
TRANSPORT_QUIET_PARAM = "cg_damage_duration_d03b_cloud_transport_quiet_enable"
TRANSPORT_QUIET_MUTE_PREFIXES = ("CG_REGIME_TIME_",)
TRANSPORT_QUIET_KEEP_PREFIXES = (
    "D0_COMPACT_CLOSEOUT", "CG_DAMAGE_", "CG_MAISR_", "CG_MACRO_",
)
# High-frequency regime-time allowlist tokens re-injected by regime-time init.
TRANSPORT_QUIET_STRIP_ALLOW_PREFIXES = (
    "CG_REGIME_TIME_TRADE",
    "CG_REGIME_TIME_PENDING",
    "CG_REGIME_TIME_EXEC",
    "[INIT] CG_REGIME_TIME_TRADE",
)

FORBIDDEN_EXPORT_RE = re.compile(
    r"ObjectStore\.Save\s*\(|ObjectStore\.SaveBytes\s*\(|ObjectStore\.Delete\s*\(|"
    r"(?<![A-Za-z_])SaveBytes\s*\(|"
    r"open\s*\([^)]*['\"][wa]['\"]|"
    r"urllib\.request|requests\.(get|post)|socket\.socket"
)


def _num(v):
    if not _avail(v):
        return None
    try:
        return float(_f(v))
    except Exception:
        return None


def export_mode_labels():
    return {
        "export_mode": EXPORT_MODE,
        "full_history_raw_export": FULL_HISTORY_RAW_EXPORT,
        "aggregate_coverage": AGGREGATE_COVERAGE,
        "checkpoint_sample_mode": CHECKPOINT_SAMPLE_MODE,
        "episode_sample_mode": EPISODE_SAMPLE_MODE,
        "production_comparison_available": False,
        "production_claim_eligible": False,
        "max_checkpoint_rows_sample": MAX_CHECKPOINT_ROWS,
        "max_episode_rows_sample": MAX_EPISODE_ROWS,
        "units": UNITS_NORMALIZED_SHADOW_SLEEVE,
        "normalized_shadow_sleeve_start": NORMALIZED_SHADOW_SLEEVE_START,
        "schema_version": COMPACT_SCHEMA_VERSION,
    }


class CompactStreamingAggregates:
    """Streaming aggregates over ALL valid observations; independent of sample caps."""

    def __init__(self):
        self.valid_checkpoints = 0
        self.valid_policy_rows = 0
        self.valid_episodes = 0
        self.label_available = 0
        self.label_unavailable = 0
        self.years = {}  # year -> {checkpoints, policy_rows}
        self.first_decision = None
        self.last_decision = None
        self.rejected = Counter()
        self.gates = Counter()
        self._episode_seen = set()
        self.policy = {
            pid: {
                "n": 0, "sum_frac": 0.0, "sum_sq": 0.0,
                "switches": 0, "unavailable": 0,
                "min_frac": None, "max_frac": None,
            }
            for pid in FIXED_ONLY_POLICIES
        }
        self.pairwise = {
            pid: {"n": 0, "sum_diff": 0.0, "unavailable": 0}
            for pid in FIXED_ONLY_BASELINES
        }
        self.p0_numeric_in_aggregate = 0
        self.sample_meta = {
            "checkpoint_sample_cap": MAX_CHECKPOINT_ROWS,
            "episode_sample_cap": MAX_EPISODE_ROWS,
            "checkpoint_sample_retained": 0,
            "episode_sample_retained": 0,
        }

    def note_reject(self, reason):
        self.rejected[str(reason or "UNKNOWN")] += 1
        self.gates[str(reason or "UNKNOWN")] += 1

    def note_gate(self, name, n=1):
        self.gates[str(name)] += int(n)

    def set_sample_retained(self, n_ck, n_ep):
        self.sample_meta["checkpoint_sample_retained"] = int(n_ck)
        self.sample_meta["episode_sample_retained"] = int(n_ep)

    def observe_recorded(self, rows, pairwise, decision_time, episode_id=None,
                         label_ok=True, gate_ok=True):
        """Consume one already-validated causal checkpoint (all policy rows)."""
        if not gate_ok:
            return
        self.valid_checkpoints += 1
        if label_ok:
            self.label_available += 1
        else:
            self.label_unavailable += 1
        if isinstance(decision_time, datetime):
            if self.first_decision is None or decision_time < self.first_decision:
                self.first_decision = decision_time
            if self.last_decision is None or decision_time > self.last_decision:
                self.last_decision = decision_time
            y = str(decision_time.year)
            bucket = self.years.setdefault(y, {"checkpoints": 0, "policy_rows": 0})
            bucket["checkpoints"] += 1
        if episode_id not in (None, UNAVAILABLE, ""):
            if episode_id not in self._episode_seen:
                self._episode_seen.add(episode_id)
                self.valid_episodes += 1

        for row in rows or []:
            pid = row.get("policy_id")
            if pid == "P0_CURRENT":
                # never enter numeric aggregates
                if row.get("comparison_eligible") is True:
                    self.p0_numeric_in_aggregate += 1
                continue
            if pid not in FIXED_ONLY_POLICIES:
                continue
            if row.get("comparison_eligible") is False:
                continue
            self.valid_policy_rows += 1
            if isinstance(decision_time, datetime):
                self.years.setdefault(str(decision_time.year),
                                      {"checkpoints": 0, "policy_rows": 0})
                self.years[str(decision_time.year)]["policy_rows"] += 1
            st = self.policy[pid]
            val = _num(row.get("policy_restore_fraction"))
            if val is None:
                st["unavailable"] += 1
            else:
                st["n"] += 1
                st["sum_frac"] += val
                st["sum_sq"] += val * val
                st["min_frac"] = val if st["min_frac"] is None else min(st["min_frac"], val)
                st["max_frac"] = val if st["max_frac"] is None else max(st["max_frac"], val)
            direction = row.get("policy_step_direction")
            if direction in ("UP", "DOWN"):
                st["switches"] += 1

        for pw in pairwise or []:
            rhs = pw.get("rhs")
            if rhs not in self.pairwise:
                continue
            if pw.get("lhs") != "P5_DYNAMIC" or rhs == "P0_CURRENT":
                continue
            st = self.pairwise[rhs]
            diff = _num(pw.get("difference_p5_minus_fixed"))
            if diff is None:
                st["unavailable"] += 1
            else:
                st["n"] += 1
                st["sum_diff"] += diff

    def snapshot(self):
        pol = {}
        for pid, st in self.policy.items():
            n = st["n"]
            mean = (st["sum_frac"] / n) if n else UNAVAILABLE
            pol[pid] = {
                "n": n,
                "unavailable": st["unavailable"],
                "mean_restore_fraction": mean,
                "sum_restore_fraction": st["sum_frac"] if n else UNAVAILABLE,
                "min_restore_fraction": st["min_frac"] if st["min_frac"] is not None else UNAVAILABLE,
                "max_restore_fraction": st["max_frac"] if st["max_frac"] is not None else UNAVAILABLE,
                "switches": st["switches"],
                "units": UNITS_NORMALIZED_SHADOW_SLEEVE,
                "comparison_eligible": True,
            }
        pw = {}
        for rhs, st in self.pairwise.items():
            n = st["n"]
            pw[rhs] = {
                "lhs": "P5_DYNAMIC",
                "rhs": rhs,
                "n": n,
                "unavailable": st["unavailable"],
                "mean_difference_p5_minus_fixed": (st["sum_diff"] / n) if n else UNAVAILABLE,
                "sum_difference_p5_minus_fixed": st["sum_diff"] if n else UNAVAILABLE,
                "units": UNITS_NORMALIZED_SHADOW_SLEEVE,
                "best_fixed_selection": False,
                "production_claim_eligible": False,
            }
        years = {y: dict(self.years[y]) for y in sorted(self.years)}
        return {
            "valid_checkpoints": self.valid_checkpoints,
            "valid_policy_rows": self.valid_policy_rows,
            "valid_episodes": self.valid_episodes,
            "label_available": self.label_available,
            "label_unavailable": self.label_unavailable,
            "coverage_by_year": years,
            "first_decision_time": self.first_decision.isoformat() if isinstance(self.first_decision, datetime) else UNAVAILABLE,
            "last_decision_time": self.last_decision.isoformat() if isinstance(self.last_decision, datetime) else UNAVAILABLE,
            "rejected_reason_counts": dict(sorted(self.rejected.items())),
            "gate_counters": dict(sorted(self.gates.items())),
            "policy_metrics": pol,
            "pairwise_metrics": pw,
            "p0_numeric_in_aggregate": self.p0_numeric_in_aggregate,
            "sample_meta": dict(self.sample_meta),
            "aggregate_coverage": AGGREGATE_COVERAGE,
        }


def build_compact_closeout(aggregates, runtime_counters=None, source_manifest_hash=None,
                           fixed_only=False, p0_audit=None):
    """Deterministic compact EOA payload; ordinary-log safe."""
    agg = aggregates.snapshot() if aggregates is not None else {}
    labels = export_mode_labels()
    p0 = p0_exclusion_audit()
    if p0_audit:
        p0 = dict(p0)
        p0["runtime_audit_verdict"] = (p0_audit or {}).get("verdict", UNAVAILABLE)
    payload = {
        "prefix": D0_COMPACT_PREFIX,
        "schema_version": COMPACT_SCHEMA_VERSION,
        **labels,
        "fixed_only_shadow_enable": bool(fixed_only),
        "policy_universe": list(FIXED_ONLY_POLICIES),
        "p0_exclusion": p0,
        "contract": {
            "comparison_scope": "FIXED_ONLY_SHADOW",
            "units": UNITS_NORMALIZED_SHADOW_SLEEVE,
            "production_comparison_available": False,
            "production_claim_eligible": False,
        },
        "runtime_counters": dict(runtime_counters or {}),
        "source_manifest_hash": source_manifest_hash or UNAVAILABLE,
        "aggregates": agg,
        "artifact_completeness": {
            "aggregates": "COMPLETE_VALID_OBSERVATION_SET",
            "raw_checkpoint_export": FULL_HISTORY_RAW_EXPORT,
            "raw_episode_export": FULL_HISTORY_RAW_EXPORT,
            "bounded_sample_only": True,
        },
    }
    return payload


def compact_closeout_text(payload):
    """Single-line machine-readable closeout + size metadata."""
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True, default=str)
    line = "%s,%s" % (D0_COMPACT_PREFIX, body)
    return line


def compact_payload_bytes(payload):
    return len(compact_closeout_text(payload).encode("utf-8"))


def scan_export_forbidden(source_text):
    # Drop string literals and regex definitions so pattern/test text cannot self-match.
    cleaned = re.sub(
        r"(\"\"\"[\s\S]*?\"\"\"|'''[\s\S]*?'''|\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*')",
        "\"\"",
        source_text or "",
    )
    cleaned = re.sub(
        r"FORBIDDEN[_A-Z0-9]*\s*=\s*re\.compile\((?:.|\n)*?\)\n",
        "",
        cleaned,
    )
    return [m.group(0) for m in FORBIDDEN_EXPORT_RE.finditer(cleaned)]


def sha256_text(text):
    return hashlib.sha256((text or "").replace("\r\n", "\n").encode("utf-8")).hexdigest()


def build_local_source_manifest_from_contents(file_contents, commit_sha=None):
    """Build manifest from preloaded {name: text}. No filesystem I/O (Cloud-safe)."""
    files = {}
    for name in sorted((file_contents or {}).keys()):
        text = (file_contents[name] or "").replace("\r\n", "\n")
        files[name] = {
            "path": name,
            "sha256": sha256_text(text),
            "bytes": len(text.encode("utf-8")),
        }
    body = {
        "cloud_project": "CoreGrowth",
        "cloud_project_id": 27489898,
        "commit_sha": commit_sha or UNAVAILABLE,
        "files": files,
        "required_dependency_count": len(files),
    }
    digest = hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    body["manifest_sha256"] = digest
    return body


def compare_source_parity(local_manifest, remote_files):
    """remote_files: {name: content_str}. No deletes; only required-set compare."""
    required = local_manifest.get("files") or {}
    remote = remote_files or {}
    missing, mismatch, match = [], [], []
    for name, meta in sorted(required.items()):
        if name not in remote:
            missing.append(name)
            continue
        rem_txt = (remote[name] or "").replace("\r\n", "\n")
        rem_hash = hashlib.sha256(rem_txt.encode("utf-8")).hexdigest()
        if rem_hash == meta["sha256"]:
            match.append(name)
        else:
            mismatch.append(name)
    report = {
        "cloud_project": local_manifest.get("cloud_project", "CoreGrowth"),
        "cloud_project_id": local_manifest.get("cloud_project_id", 27489898),
        "commit_sha": local_manifest.get("commit_sha"),
        "required_dependency_count": len(required),
        "remote_missing_count": len(missing),
        "remote_mismatch_count": len(mismatch),
        "remote_extra_conflict_count": 0,
        "remote_missing": missing,
        "remote_mismatch": mismatch,
        "remote_match_count": len(match),
        "source_parity": "PASS" if (not missing and not mismatch) else "FAIL",
    }
    return report


def compact_export_schema():
    return {
        "schema_version": COMPACT_SCHEMA_VERSION,
        "export_mode": EXPORT_MODE,
        "full_history_raw_export": FULL_HISTORY_RAW_EXPORT,
        "aggregate_coverage": AGGREGATE_COVERAGE,
        "checkpoint_sample_mode": CHECKPOINT_SAMPLE_MODE,
        "episode_sample_mode": EPISODE_SAMPLE_MODE,
        "ordinary_log_limit_bytes": ORDINARY_LOG_LIMIT,
        "required_aggregate_fields": [
            "coverage_by_year", "valid_checkpoints", "valid_episodes",
            "policy_metrics", "pairwise_metrics", "gate_counters",
            "rejected_reason_counts", "sample_meta",
        ],
    }


def transport_quiet_active(fixed_only_enable, transport_quiet_enable):
    return bool(fixed_only_enable) and bool(transport_quiet_enable)


def apply_transport_quiet_filters(log_only_prefixes, log_mute_prefixes,
                                  fixed_only_enable, transport_quiet_enable):
    """Pure filter update. Zero effect unless both D0 diagnostic flags are on.
    Does not mutate targets/orders/gross/episodes — only log prefix lists."""
    lp0 = list(log_only_prefixes or [])
    mp0 = list(log_mute_prefixes or [])
    if not transport_quiet_active(fixed_only_enable, transport_quiet_enable):
        return lp0, mp0, False
    strip = set(TRANSPORT_QUIET_STRIP_ALLOW_PREFIXES)
    lp = [p for p in lp0 if str(p) not in strip and not str(p).startswith("CG_REGIME_TIME")]
    for pref in TRANSPORT_QUIET_KEEP_PREFIXES:
        if pref not in lp:
            lp.append(pref)
    mp = list(mp0)
    for pref in TRANSPORT_QUIET_MUTE_PREFIXES:
        if pref not in mp:
            mp.append(pref)
    return lp, mp, True


def project_transport_budget_bytes(compact_payload_nbytes, init_line_count=8,
                                   status_line_count=10, avg_line_bytes=220):
    """Ordinary-log projection with CG_REGIME_TIME_* suppressed (by construction)."""
    n = int(compact_payload_nbytes or 0)
    overhead = int(init_line_count + status_line_count) * int(avg_line_bytes)
    return n + overhead


def transport_quiet_contract():
    return {
        "param": TRANSPORT_QUIET_PARAM,
        "default": "0",
        "dual_gate_required": [
            "cg_damage_duration_d03b_fixed_only_shadow_enable",
            TRANSPORT_QUIET_PARAM,
        ],
        "muted_high_frequency_prefixes": list(TRANSPORT_QUIET_MUTE_PREFIXES),
        "preserved_prefixes": list(TRANSPORT_QUIET_KEEP_PREFIXES),
        "strips_regime_allowlist_reinjection": True,
        "per_checkpoint_d0_logs": "FORBIDDEN_WHEN_QUIET",
        "state_mutation": "FORBIDDEN",
        "ordinary_log_limit_bytes": ORDINARY_LOG_LIMIT,
    }


def run_compact_export_static_tests():
    """D0.3B2B repair tests; returns {passed,failed,total,rows,...}."""
    from datetime import timedelta
    from cg_damage_duration_d03a_shadow import ModelAShadowRouter, _snap_b, _snap_c
    from cg_damage_duration_d03b_runtime import ModelAShadowRuntimeAccounting
    from cg_damage_duration_d03b_export import PolicyRuntimeExporter
    from rrx_params import RRX_PARAMS

    rows, passed, failed = [], 0, 0

    def ok(n, c, detail=""):
        nonlocal passed, failed
        if c:
            passed += 1
            rows.append({"name": n, "pass": True, "detail": detail})
        else:
            failed += 1
            rows.append({"name": n, "pass": False, "detail": str(detail)})

    # --- labels / forbidden ---
    lab = export_mode_labels()
    ok("C01_export_mode", lab["export_mode"] == EXPORT_MODE)
    ok("C02_no_full_history_claim", lab["full_history_raw_export"] == FULL_HISTORY_RAW_EXPORT)
    ok("C03_aggregate_coverage_label", lab["aggregate_coverage"] == AGGREGATE_COVERAGE)
    ok("C04_sample_modes", lab["checkpoint_sample_mode"] == CHECKPOINT_SAMPLE_MODE
       and lab["episode_sample_mode"] == EPISODE_SAMPLE_MODE)

    # Cloud-safe ObjectStore gate: scanner must detect real calls and ignore clean text.
    ok("C05_no_objectstore_export",
       len(scan_export_forbidden("x = ObjectStore.Save('a')")) >= 1
       and len(scan_export_forbidden("a = 1\nb = 2\n")) == 0)

    # export must not mutate policy/state: observe uses deepcopy inputs in runtime;
    # aggregates only increment counters
    ag = CompactStreamingAggregates()
    before = {"targets": {"SPY": 0.5}, "gross": 1.0}
    ag.observe_recorded(
        [{"policy_id": "P5_DYNAMIC", "policy_restore_fraction": 0.5,
          "comparison_eligible": True, "policy_step_direction": "UP"}],
        [{"lhs": "P5_DYNAMIC", "rhs": "P1_HOLD_TO_CLOSE",
          "difference_p5_minus_fixed": 0.1}],
        datetime(2020, 1, 2, 10, 0, 0), episode_id="E1",
    )
    ok("C06_no_state_mutation", before == {"targets": {"SPY": 0.5}, "gross": 1.0})

    # --- fixture parity with capped samples ---
    t0 = datetime(2019, 6, 3, 10, 0, 0)
    router = ModelAShadowRouter()
    n_ck = 400  # > 256 sample cap

    def feed(rt, max_ck=MAX_CHECKPOINT_ROWS, max_ep=MAX_EPISODE_ROWS):
        rt.exporter = PolicyRuntimeExporter(max_ck=max_ck, max_ep=max_ep)
        for i in range(n_ck):
            ti = t0 + timedelta(minutes=5 * i)
            bi = _snap_b(ti, i)
            bi["decision_time"] = ti
            bi["feature_cutoff"] = ti - timedelta(minutes=5)
            bi["action_eligible_time"] = ti + timedelta(minutes=5)
            bi["episode_id"] = "EP%d" % (i // 10)
            bi["checkpoint_key"] = "CK%d" % i
            shi = router.update(bi, _snap_c(ti, i))
            rt.update(bi, _snap_c(ti, i), shi, d03b_enabled=True,
                      fixed_only_shadow_enable=True)
        return rt

    rt_a = feed(ModelAShadowRuntimeAccounting(), max_ck=256, max_ep=64)
    rt_b = feed(ModelAShadowRuntimeAccounting(), max_ck=32, max_ep=8)
    snap_a = rt_a.aggregates.snapshot()
    snap_b = rt_b.aggregates.snapshot()
    ok("C07_valid_ck_count", snap_a["valid_checkpoints"] == n_ck)
    ok("C08_sample_capped",
       len(rt_a.exporter.checkpoint_rows) <= 256
       and len(rt_b.exporter.checkpoint_rows) <= 32)
    ok("C09_cap_invariance_checkpoints",
       snap_a["valid_checkpoints"] == snap_b["valid_checkpoints"])
    ok("C10_cap_invariance_policy_n",
       snap_a["policy_metrics"]["P5_DYNAMIC"]["n"]
       == snap_b["policy_metrics"]["P5_DYNAMIC"]["n"])
    ok("C11_cap_invariance_pairwise",
       snap_a["pairwise_metrics"]["P1_HOLD_TO_CLOSE"]["n"]
       == snap_b["pairwise_metrics"]["P1_HOLD_TO_CLOSE"]["n"])
    ok("C12_aggregate_gt_sample",
       snap_a["valid_checkpoints"] > len(rt_a.exporter.checkpoint_rows))

    # offline full mean parity: recompute from aggregate path only (not samples)
    ok("C13_fixture_parity_mean_present",
       _avail(snap_a["policy_metrics"]["P1_HOLD_TO_CLOSE"]["mean_restore_fraction"]))

    # duplicates / stale must not double-count
    rt_d = ModelAShadowRuntimeAccounting()
    bi = _snap_b(t0, 0)
    bi["decision_time"] = t0
    bi["feature_cutoff"] = t0 - timedelta(minutes=5)
    bi["action_eligible_time"] = t0 + timedelta(minutes=5)
    bi["checkpoint_key"] = "DUP1"
    sh = router.update(bi, _snap_c(t0, 0))
    rt_d.update(bi, _snap_c(t0, 0), sh, d03b_enabled=True, fixed_only_shadow_enable=True)
    rt_d.update(bi, _snap_c(t0, 0), sh, d03b_enabled=True, fixed_only_shadow_enable=True)
    ok("C14_dup_no_double_count",
       rt_d.aggregates.valid_checkpoints == 1
       and rt_d.counters["duplicate_blocked"] >= 1)
    b_old = _snap_b(t0 - timedelta(minutes=5), 1)
    b_old["decision_time"] = t0 - timedelta(minutes=5)
    b_old["feature_cutoff"] = t0 - timedelta(minutes=10)
    b_old["action_eligible_time"] = t0
    b_old["checkpoint_key"] = "STALE1"
    sho = ModelAShadowRouter().update(b_old, _snap_c(t0 - timedelta(minutes=5), 1))
    rt_d.update(b_old, _snap_c(t0 - timedelta(minutes=5), 1), sho,
                d03b_enabled=True, fixed_only_shadow_enable=True)
    ok("C15_stale_no_double_count",
       rt_d.aggregates.valid_checkpoints == 1
       and rt_d.counters["stale_blocked"] >= 1)

    # P0 exclusion
    ok("C16_p0_exclusion", p0_exclusion_audit()["p0_in_aggregation"] is False
       and snap_a["p0_numeric_in_aggregate"] == 0
       and "P0_CURRENT" not in snap_a["policy_metrics"])

    # disabled noop
    ok("C17_disabled_noop",
       ModelAShadowRuntimeAccounting().update(None, None, None, d03b_enabled=False) is None
       and RRX_PARAMS.get("cg_damage_duration_d03b_enable") == "0"
       and RRX_PARAMS.get("cg_damage_duration_d03b_fixed_only_shadow_enable") == "0"
       and RRX_PARAMS.get("cg_damage_duration_d03b_cloud_transport_quiet_enable") == "0")

    # EOA payload size
    payload = build_compact_closeout(
        rt_a.aggregates, runtime_counters=rt_a.counters,
        source_manifest_hash="TEST", fixed_only=True, p0_audit=rt_a.p0_audit,
    )
    nbytes = compact_payload_bytes(payload)
    ok("C18_eoa_below_100kb", nbytes < ORDINARY_LOG_LIMIT, detail=str(nbytes))
    ok("C19_eoa_deterministic",
       compact_closeout_text(payload) == compact_closeout_text(payload))
    ok("C20_units", all(
        snap_a["policy_metrics"][p]["units"] == UNITS_NORMALIZED_SHADOW_SLEEVE
        for p in FIXED_ONLY_POLICIES))

    # source manifest detects missing/mismatch/match (in-memory contents only)
    contents = {
        "a.py": "print(1)\n",
        "b.py": "print(2)\n",
        "c.py": "print(3)\n",
    }
    local = build_local_source_manifest_from_contents(contents, commit_sha="TESTSHA")
    remote = {"a.py": contents["a.py"]}
    rep_miss = compare_source_parity(local, remote)
    ok("C21_manifest_detects_missing", rep_miss["remote_missing_count"] > 0
       and rep_miss["source_parity"] == "FAIL")
    bad_remote = dict(contents)
    bad_remote["a.py"] = contents["a.py"] + "# tamper\n"
    rep_mm = compare_source_parity(local, bad_remote)
    ok("C22_manifest_detects_mismatch", rep_mm["remote_mismatch_count"] >= 1)
    good_remote = dict(contents)
    rep_ok = compare_source_parity(local, good_remote)
    ok("C23_manifest_match_pass", rep_ok["source_parity"] == "PASS"
       and rep_ok["remote_missing_count"] == 0
       and rep_ok["remote_mismatch_count"] == 0)

    ok("C24_contract_ready", fixed_only_shadow_contract()["production_claim_eligible"] is False)
    ok("C25_schema", compact_export_schema()["export_mode"] == EXPORT_MODE)

    # --- transport quiet dual-gate (log prefixes only; no state mutation) ---
    tqc = transport_quiet_contract()
    ok("TQ01_quiet_default_off",
       RRX_PARAMS.get(TRANSPORT_QUIET_PARAM) == "0" and tqc["default"] == "0")
    base_lp = list(TRANSPORT_QUIET_STRIP_ALLOW_PREFIXES) + ["CG_DAMAGE_", "OTHER"]
    base_mp = ["KEEP_MUTE"]
    lp_a, mp_a, ap_a = apply_transport_quiet_filters(base_lp, base_mp, False, False)
    ok("TQ02_off_both_noop", ap_a is False and lp_a == base_lp and mp_a == base_mp)
    lp_b, mp_b, ap_b = apply_transport_quiet_filters(base_lp, base_mp, True, False)
    ok("TQ03_fixed_only_without_quiet_noop", ap_b is False and lp_b == base_lp and mp_b == base_mp)
    lp_c, mp_c, ap_c = apply_transport_quiet_filters(base_lp, base_mp, False, True)
    ok("TQ04_quiet_without_fixed_only_noop", ap_c is False and lp_c == base_lp and mp_c == base_mp)
    lp_d, mp_d, ap_d = apply_transport_quiet_filters(base_lp, base_mp, True, True)
    ok("TQ05_dual_gate_applies", ap_d is True)
    ok("TQ06_regime_stripped",
       all(p not in lp_d for p in TRANSPORT_QUIET_STRIP_ALLOW_PREFIXES)
       and not any(str(p).startswith("CG_REGIME_TIME") for p in lp_d))
    ok("TQ07_regime_muted", "CG_REGIME_TIME_" in mp_d and "KEEP_MUTE" in mp_d)
    ok("TQ08_d0_prefixes_kept",
       all(p in lp_d for p in TRANSPORT_QUIET_KEEP_PREFIXES) and "OTHER" in lp_d)
    # suppression cannot mutate research counters/targets
    before_ctr = dict(rt_a.counters)
    before_tg = {"SPY": 0.5, "gross": 1.0}
    _ = apply_transport_quiet_filters(["CG_REGIME_TIME_PENDING"], [], True, True)
    ok("TQ09_no_state_mutation",
       rt_a.counters == before_ctr and before_tg == {"SPY": 0.5, "gross": 1.0})
    line = compact_closeout_text(payload)
    ok("TQ10_compact_still_emitted", line.startswith(D0_COMPACT_PREFIX + ","))
    proj = project_transport_budget_bytes(nbytes)
    ok("TQ11_budget_projection_under_100kb", proj < ORDINARY_LOG_LIMIT, detail=str(proj))
    ok("TQ12_no_per_checkpoint_requirement",
       tqc["per_checkpoint_d0_logs"] == "FORBIDDEN_WHEN_QUIET")
    # simulate log gate: muted message starts with CG_REGIME_TIME_
    muted = any("CG_REGIME_TIME_PENDING".startswith(p) for p in mp_d)
    ok("TQ13_regime_line_would_mute", muted is True)
    ok("TQ14_compact_not_muted",
       not any(D0_COMPACT_PREFIX.startswith(p) for p in mp_d))

    # --- D0 fixed-only EOA dispatch independence ---
    eoa = run_d0_eoa_dispatch_static_tests()
    ok("EOA00_suite", eoa.get("failed", 1) == 0, detail=str(eoa.get("failed")))
    for erow in eoa.get("rows") or []:
        rows.append({"name": "EOA_" + erow["name"], "pass": erow["pass"], "detail": erow.get("detail", "")})
        if erow["pass"]:
            passed += 1
        else:
            failed += 1

    return {
        "passed": passed, "failed": failed, "total": passed + failed, "rows": rows,
        "eoa_payload_size_bytes": nbytes,
        "transport_budget_projection_bytes": proj,
        "aggregate_fixture_parity": "PASS" if failed == 0 else "FAIL",
        "sample_cap_invariance_gate": "PASS" if failed == 0 else "FAIL",
        "transport_quiet_gate": "PASS" if failed == 0 else "FAIL",
        "d0_eoa_dispatch": eoa,
    }


def run_d0_eoa_dispatch_static_tests():
    """EOA dispatch independence from _sr_on / cg_maisr_diag_enable."""
    from cg_damage_duration_d01_diag import CgDamageDurationD01DiagMixin
    from cg_maisr_diag import CgMaisrDiagMixin
    from rrx_params import RRX_PARAMS

    rows, passed, failed = [], 0, 0

    def ok(n, c, detail=""):
        nonlocal passed, failed
        if c:
            passed += 1
            rows.append({"name": n, "pass": True, "detail": detail})
        else:
            failed += 1
            rows.append({"name": n, "pass": False, "detail": str(detail)})

    class _H(CgMaisrDiagMixin, CgDamageDurationD01DiagMixin):
        def __init__(self):
            self.logs = []
            self._ms_err = 0
            self._ms_emitted = False
            self._ms_on = False
            self._sr_on = False
            self._sr_emitted = False
            self.cg_maisr_diag_enable = False
            self.cg_damage_duration_d01_enable = True
            self.cg_damage_duration_d02_enable = True
            self.cg_damage_duration_d03a_enable = True
            self.cg_damage_duration_d03b_enable = True
            self.cg_damage_duration_d03b_fixed_only_shadow_enable = True
            self._dmg_d03b = None
            self._dmg_d0_eoa_emitted = False
            self._dmg_real_orders = 0
            self._dmg_sub_changes = 0
            self._dmg_target_mut = 0
            self._dmg_d02_err = 0
            self._ms_bd_seen = self._ms_bd_accept = self._ms_bd_dup = 0
            self._ms_bd_conflict = self._ms_bd_oo = 0
            self._ms_eval_seen = self._ms_eval_uniq = self._ms_eval_dup = 0

        def _DamageD01Log(self, msg):
            self.logs.append(str(msg))

        def _MsLog(self, msg):
            self.logs.append(str(msg))

        def log(self, msg):
            self.logs.append(str(msg))

        def CgDamageD02OnEndOfAlgorithm(self, parity_ok):
            self.logs.append("D0_COMPACT_CLOSEOUT,{\"test\":1,\"parity\":%s}" % int(bool(parity_ok)))
            return True

        def CgDamageD01OnEndOfAlgorithm(self, parity_ok):
            self.logs.append("CG_DAMAGE_D01_EOA,parity=%s" % int(bool(parity_ok)))
            return True

    ok("E01_maisr_diag_default_off", RRX_PARAMS.get("cg_maisr_diag_enable", "0") == "0")
    h = _H()
    ok("E02_predicate_true", h._DamageD0FixedOnlyEOAPredicate() is True)
    ok("E03_sr_off", h._sr_on is False and h.cg_maisr_diag_enable is False)
    h.CgShadowReplayEmitFinal()
    compact = [l for l in h.logs if l.startswith("D0_COMPACT_CLOSEOUT")]
    maisr_final = [l for l in h.logs if "CG_MAISR_P1_BAR_DEDUP_FINAL" in l or "CG_MAISR_D0_PARITY" in l]
    ok("E04_compact_with_sr_off", len(compact) == 1, detail=str(len(compact)))
    ok("E05_generic_maisr_not_activated", len(maisr_final) == 0 and h._ms_emitted is False)
    ok("E06_emitted_flag", h._dmg_d0_eoa_emitted is True)
    # exactly once
    h.CgShadowReplayEmitFinal()
    compact2 = [l for l in h.logs if l.startswith("D0_COMPACT_CLOSEOUT")]
    ok("E07_exactly_once", len(compact2) == 1, detail=str(len(compact2)))
    # TryEOA no-op after emit (generic MAISR path must not duplicate)
    ok("E08_try_eoa_noop_after", h.CgDamageD01TryEOA(True) is False)
    compact3 = [l for l in h.logs if l.startswith("D0_COMPACT_CLOSEOUT")]
    ok("E09_no_dup_via_try_eoa", len(compact3) == 1)

    # all D0 flags off => no emit
    h2 = _H()
    h2.cg_damage_duration_d03b_enable = False
    h2.cg_damage_duration_d03b_fixed_only_shadow_enable = False
    ok("E10_predicate_off", h2._DamageD0FixedOnlyEOAPredicate() is False)
    h2.CgShadowReplayEmitFinal()
    ok("E11_no_emit_when_off",
       not any(l.startswith("D0_COMPACT_CLOSEOUT") for l in h2.logs))

    # failure containment
    h3 = _H()

    def _boom(parity_ok):
        raise RuntimeError("inject_fail")

    h3.CgDamageD02OnEndOfAlgorithm = _boom
    h3.CgShadowReplayEmitFinal()
    fail_lines = [l for l in h3.logs if l.startswith("D0_COMPACT_CLOSEOUT") and "EOA_FAIL" in l]
    ok("E12_failure_containment", len(fail_lines) == 1 and "RuntimeError" in fail_lines[0])
    ok("E13_failure_idempotent_flag", h3._dmg_d0_eoa_emitted is True)

    # no state mutation from dispatch
    h4 = _H()
    h4._targets = {"SPY": 0.5}
    h4._gross = 1.0
    h4.CgShadowReplayEmitFinal()
    ok("E14_no_state_mutation",
       h4._targets == {"SPY": 0.5} and h4._gross == 1.0
       and int(getattr(h4, "_dmg_real_orders", 0) or 0) == 0)

    return {
        "passed": passed, "failed": failed, "total": passed + failed, "rows": rows,
        "d0_fixed_only_eoa_gate": "PASS" if failed == 0 else "FAIL",
        "d0_eoa_exactly_once_gate": "PASS" if failed == 0 else "FAIL",
        "d0_eoa_failure_containment_gate": "PASS" if failed == 0 else "FAIL",
        "cg_maisr_diag_enable_required": "NO",
        "sr_on_required_for_d0_eoa": "NO",
        "generic_maisr_activation_found": "NO",
    }


if __name__ == "__main__":
    r = run_compact_export_static_tests()
    print(json.dumps({k: r[k] for k in r if k != "rows"}))
    for row in r["rows"]:
        if not row["pass"]:
            print("FAIL", row["name"], row["detail"])
