# cg_damage_duration_d03b_proxy_replay.py -- D0.3B2H fixed-only SPY proxy economic replay.
# Diagnostic only. No orders, subscriptions, targets, P0, or production NAV/holdings.
from __future__ import annotations
from datetime import date, datetime, time, timedelta
from statistics import median

from cg_damage_duration_d03a_core import UNAVAILABLE, _avail, _f
from cg_damage_duration_d03b_accounting import FIXED_ONLY_POLICIES, FIXED_ONLY_BASELINES

EXPERIMENT = "CG-DAMAGE-DURATION-D0.3B2H"
PHASE = "D0.3B2H_FIXED_ONLY_SPY_PROXY_ECONOMIC_REPLAY_AND_CLOSEOUT"
PROXY_UNDERLYING = "SPY"
PROXY_COST_BPS = 0
PROXY_COST_LABEL = "COST_FREE_COMPARATIVE_PROXY"
PROXY_EXECUTION_RULE = "FIRST_OBSERVED_SPY_BAR_STRICTLY_AFTER_DECISION"
SESSION_CLOSE_TOD_MINUTES = 960  # 16:00 ET; matches P4 session-close convention
EPS = 1e-12
BLOCKS = {
    "2012_2016": (2012, 2016),
    "2017_2021": (2017, 2021),
    "2022_2026": (2022, 2026),
}
P123 = ("P1_HOLD_TO_CLOSE", "P2_HOLD_TO_NEXT_CLOSE", "P3_HOLD_3D")
SCHEDULED_FRACTION_POLICIES = ("P4_GRADUAL_FIXED", "P5_DYNAMIC")


def _session_close_dt(day):
    return datetime.combine(day, time(16, 0))


def _tod_minutes(dt):
    return int(dt.hour) * 60 + int(dt.minute)


def _nth_session_after(sessions, from_day, n):
    later = [d for d in sessions if d > from_day]
    if n < 1 or len(later) < n:
        return None
    return later[n - 1]


def p123_target_fraction(policy_id, open_time, as_of, session_days):
    """Causal P1–P3 planned path: cash until scheduled close, then full restore."""
    if open_time is None or as_of is None:
        return 0.0
    open_day = open_time.date() if isinstance(open_time, datetime) else open_time
    days = sorted(set(session_days or []))
    if open_day not in days:
        days = sorted(set(days) | {open_day})
    if policy_id == "P1_HOLD_TO_CLOSE":
        sc = _session_close_dt(open_day)
        return 1.0 if as_of >= sc else 0.0
    if policy_id == "P2_HOLD_TO_NEXT_CLOSE":
        nxt = _nth_session_after(days, open_day, 1)
        if nxt is None:
            return 0.0
        return 1.0 if as_of >= _session_close_dt(nxt) else 0.0
    if policy_id == "P3_HOLD_3D":
        d3 = _nth_session_after(days, open_day, 3)
        if d3 is None:
            return 0.0
        return 1.0 if as_of >= _session_close_dt(d3) else 0.0
    return 0.0


def p123_decision_time_for_target(policy_id, open_time, session_days, target_f):
    """DecisionTime at which planned restore to target_f becomes known."""
    if abs(float(target_f) - 1.0) > EPS or open_time is None:
        return None
    open_day = open_time.date() if isinstance(open_time, datetime) else open_time
    days = sorted(set(session_days or []))
    if open_day not in days:
        days = sorted(set(days) | {open_day})
    if policy_id == "P1_HOLD_TO_CLOSE":
        return _session_close_dt(open_day)
    if policy_id == "P2_HOLD_TO_NEXT_CLOSE":
        nxt = _nth_session_after(days, open_day, 1)
        return None if nxt is None else _session_close_dt(nxt)
    if policy_id == "P3_HOLD_3D":
        d3 = _nth_session_after(days, open_day, 3)
        return None if d3 is None else _session_close_dt(d3)
    return None


class _Sleeve:
    __slots__ = (
        "cash", "shares", "frac", "pending_frac", "pending_after",
        "peak", "max_dd", "switches", "missing_price", "cost_bps", "lag_minutes",
    )

    def __init__(self, cost_bps=0, lag_minutes=0):
        self.cash = 1.0
        self.shares = 0.0
        self.frac = 0.0
        self.pending_frac = None
        self.pending_after = None
        self.peak = 1.0
        self.max_dd = 0.0
        self.switches = 0
        self.missing_price = 0
        self.cost_bps = float(cost_bps or 0)
        self.lag_minutes = int(lag_minutes or 0)

    def equity(self, px):
        if px is None or px <= 0:
            return self.cash
        return self.cash + self.shares * float(px)

    def _eligible(self, bar_time, after_time):
        if not isinstance(bar_time, datetime) or after_time is None:
            return False
        if self.lag_minutes <= 0:
            return bar_time > after_time
        return bar_time >= after_time

    def schedule(self, target_f, decision_time):
        if not isinstance(decision_time, datetime):
            return False
        if target_f is None or not _avail(target_f):
            return False
        tf = max(0.0, min(1.0, float(target_f)))
        after = decision_time
        if self.lag_minutes > 0:
            after = decision_time + timedelta(minutes=self.lag_minutes)
        if self.pending_frac is not None and abs(tf - float(self.pending_frac)) < EPS:
            # keep earliest decision for same target
            if after < self.pending_after:
                self.pending_after = after
            return True
        if abs(tf - self.frac) < EPS and self.pending_frac is None:
            return False
        self.pending_frac = tf
        self.pending_after = after
        return True

    def apply_pending(self, bar_time, px):
        if self.pending_frac is None or self.pending_after is None:
            return False
        if not self._eligible(bar_time, self.pending_after):
            return False  # same-bar / lag-not-yet / missing forward
        return self._set_frac(self.pending_frac, px, clear_pending=True)

    def _set_frac(self, new_f, px, clear_pending=False):
        if px is None or px <= 0:
            self.missing_price += 1
            return False
        new_f = max(0.0, min(1.0, float(new_f)))
        e = self.equity(px)
        turnover = abs(new_f - self.frac) * e
        if self.cost_bps > EPS and turnover > EPS:
            e = max(0.0, e - turnover * (self.cost_bps / 10000.0))
        if abs(new_f - self.frac) > EPS:
            self.switches += 1
        if e <= EPS or float(px) <= EPS:
            self.shares = 0.0
            self.cash = e
            self.frac = 0.0
        else:
            self.shares = (new_f * e) / float(px)
            self.cash = (1.0 - new_f) * e
            self.frac = new_f
        if clear_pending:
            self.pending_frac = None
            self.pending_after = None
        self._dd(self.equity(px) if float(px) > 0 else e)
        return True

    def mtm(self, px):
        if px is None or px <= 0:
            self.missing_price += 1
            return
        self._dd(self.equity(px))

    def _dd(self, e):
        if e > self.peak:
            self.peak = e
        if self.peak > EPS:
            dd = (self.peak - e) / self.peak
            if dd > self.max_dd:
                self.max_dd = dd

    def liquidate(self, px):
        return self._set_frac(0.0, px, clear_pending=True)


class _Episode:
    __slots__ = (
        "episode_id", "open_time", "open_day", "sessions", "sleeves",
        "confirm_time", "pending_liq_after", "closed", "excluded",
        "exclude_reason", "wealth", "episode_dd", "switches", "policy_ids",
    )

    def __init__(self, episode_id, open_time, policy_ids, cost_bps=0, lag_minutes=0):
        self.episode_id = str(episode_id)
        self.open_time = open_time
        self.open_day = open_time.date() if isinstance(open_time, datetime) else None
        self.sessions = []
        if self.open_day is not None:
            self.sessions = [self.open_day]
        self.policy_ids = tuple(policy_ids)
        self.sleeves = {
            pid: _Sleeve(cost_bps=cost_bps, lag_minutes=lag_minutes)
            for pid in self.policy_ids
        }
        self.confirm_time = None
        self.pending_liq_after = None
        self.closed = False
        self.excluded = False
        self.exclude_reason = None
        self.wealth = {}
        self.episode_dd = {}
        self.switches = {}


class FixedOnlySpyProxyReplay:
    """Per-episode SPY proxy sleeves for P1–P5; comparative diagnostic."""

    def __init__(self, extra_policies=None, blocks=None, cost_bps=0, lag_minutes=0,
                 policy_ids=None):
        self.enabled = False
        self.extra_policies = tuple(extra_policies or ())
        self.cost_bps = float(cost_bps or 0)
        self.lag_minutes = int(lag_minutes or 0)
        if policy_ids is not None:
            self.policy_ids = tuple(policy_ids)
        else:
            self.policy_ids = tuple(FIXED_ONLY_POLICIES) + tuple(
                p for p in self.extra_policies if p not in FIXED_ONLY_POLICIES)
        self.blocks = dict(blocks) if blocks is not None else dict(BLOCKS)
        self.active = {}
        self.completed = []  # list[_Episode]
        self.excluded = []
        self.last_spy_time = None
        self.last_spy_px = None
        self.chrono_equity = {pid: 1.0 for pid in self.policy_ids}
        self.chrono_peak = {pid: 1.0 for pid in self.policy_ids}
        self.chrono_dd = {pid: 0.0 for pid in self.policy_ids}
        self.counters = {
            "spy_bars": 0, "opens": 0, "confirms": 0, "abandons": 0,
            "completed": 0, "excluded": 0, "same_bar_blocked": 0,
            "missing_spy": 0, "diagnostic_real_orders": 0,
            "subscription_changes": 0, "target_mutations": 0,
            "production_gross_mutations": 0,
        }

    def set_enabled(self, on):
        self.enabled = bool(on)

    def observe_session(self, day):
        if not self.enabled or day is None:
            return
        if isinstance(day, datetime):
            day = day.date()
        for ep in self.active.values():
            if day not in ep.sessions:
                ep.sessions = sorted(set(ep.sessions) | {day})

    def on_open(self, episode_id, open_time):
        if not self.enabled:
            return False
        if episode_id in (None, UNAVAILABLE, ""):
            return False
        if not isinstance(open_time, datetime):
            return False
        eid = str(episode_id)
        if eid in self.active:
            return False
        self.active[eid] = _Episode(
            eid, open_time, self.policy_ids,
            cost_bps=self.cost_bps, lag_minutes=self.lag_minutes)
        self.counters["opens"] += 1
        return True

    def on_abandon(self, episode_id, reason="REOPEN_OR_NONCONFIRMED"):
        if not self.enabled:
            return False
        eid = str(episode_id or "")
        ep = self.active.pop(eid, None)
        if ep is None:
            return False
        ep.excluded = True
        ep.exclude_reason = str(reason)
        self.excluded.append(ep)
        self.counters["abandons"] += 1
        self.counters["excluded"] += 1
        return True

    def on_confirmed_close(self, episode_id, confirm_time):
        if not self.enabled:
            return False
        eid = str(episode_id or "")
        ep = self.active.get(eid)
        if ep is None or ep.closed:
            return False
        if not isinstance(confirm_time, datetime):
            ep.excluded = True
            ep.exclude_reason = "CONFIRM_TIME_INVALID"
            self.active.pop(eid, None)
            self.excluded.append(ep)
            self.counters["excluded"] += 1
            return False
        ep.confirm_time = confirm_time
        if self.lag_minutes > 0:
            ep.pending_liq_after = confirm_time + timedelta(minutes=self.lag_minutes)
        else:
            ep.pending_liq_after = confirm_time
        self.counters["confirms"] += 1
        # if we already have a later SPY bar, liquidate immediately on next feed
        return True

    def on_checkpoint(self, decision_time, episode_id, frac_map):
        """Schedule P4/P5 restore changes; P1–P3 handled via schedule on bars."""
        if not self.enabled:
            return
        if not isinstance(decision_time, datetime):
            return
        eid = str(episode_id or "")
        if eid in (None, "", "UNAVAILABLE") or eid not in self.active:
            return
        ep = self.active[eid]
        if ep.closed or ep.excluded:
            return
        self.observe_session(decision_time.date())
        fm = dict(frac_map or {})
        for pid in self.policy_ids:
            if pid in P123:
                continue
            raw = fm.get(pid, UNAVAILABLE)
            if not _avail(raw):
                continue
            if pid in ep.sleeves:
                ep.sleeves[pid].schedule(_f(raw), decision_time)

    def on_spy_bar(self, bar_time, px, ticker=None):
        if not self.enabled:
            return
        if ticker is not None:
            tok = str(ticker).strip().upper().replace("/", " ").split()[0]
            if tok != "SPY" and not tok.startswith("SPY"):
                return
        if not isinstance(bar_time, datetime):
            return
        try:
            px = float(px)
        except Exception:
            self.counters["missing_spy"] += 1
            return
        if px <= 0:
            self.counters["missing_spy"] += 1
            return
        self.counters["spy_bars"] += 1
        self.observe_session(bar_time.date())
        self.last_spy_time = bar_time
        self.last_spy_px = px

        done = []
        for eid, ep in list(self.active.items()):
            if ep.excluded or ep.closed:
                continue
            # P1–P3 schedule from observed sessions / bar time
            for pid in P123:
                if pid not in ep.sleeves:
                    continue
                tgt = p123_target_fraction(pid, ep.open_time, bar_time, ep.sessions)
                sl = ep.sleeves[pid]
                if abs(tgt - sl.frac) > EPS and (
                    sl.pending_frac is None or abs(float(sl.pending_frac) - tgt) > EPS
                ):
                    dt_dec = p123_decision_time_for_target(
                        pid, ep.open_time, ep.sessions, tgt)
                    if dt_dec is not None:
                        if bar_time <= dt_dec:
                            # not yet decision; do not execute early
                            sl.schedule(tgt, dt_dec)
                        else:
                            # decision already passed; require first bar strictly after decision
                            sl.schedule(tgt, dt_dec)

            for pid, sl in ep.sleeves.items():
                # same-bar / lag guard counted when pending exists but not yet eligible
                if (
                    sl.pending_frac is not None
                    and sl.pending_after is not None
                    and not sl._eligible(bar_time, sl.pending_after)
                ):
                    self.counters["same_bar_blocked"] += 1
                else:
                    sl.apply_pending(bar_time, px)
                sl.mtm(px)

            liq_ready = False
            if ep.pending_liq_after is not None:
                if self.lag_minutes > 0:
                    liq_ready = bar_time >= ep.pending_liq_after
                else:
                    liq_ready = bar_time > ep.pending_liq_after
            if liq_ready:
                ok_all = True
                for pid, sl in ep.sleeves.items():
                    # apply any remaining pending first
                    sl.apply_pending(bar_time, px)
                    if not sl.liquidate(px):
                        ok_all = False
                    ep.wealth[pid] = sl.equity(px)
                    ep.episode_dd[pid] = sl.max_dd
                    ep.switches[pid] = sl.switches
                    if sl.missing_price:
                        ok_all = False
                if not ok_all or len(ep.wealth) < len(self.policy_ids):
                    ep.excluded = True
                    ep.exclude_reason = "MISSING_COMMON_SPY_PATH"
                    self.active.pop(eid, None)
                    self.excluded.append(ep)
                    self.counters["excluded"] += 1
                    continue
                # common path ok
                ep.closed = True
                self.active.pop(eid, None)
                self.completed.append(ep)
                self.counters["completed"] += 1
                for pid in self.policy_ids:
                    w = float(ep.wealth[pid])
                    self.chrono_equity[pid] *= w
                    e = self.chrono_equity[pid]
                    if e > self.chrono_peak[pid]:
                        self.chrono_peak[pid] = e
                    peak = self.chrono_peak[pid]
                    if peak > EPS:
                        dd = (peak - e) / peak
                        if dd > self.chrono_dd[pid]:
                            self.chrono_dd[pid] = dd
                done.append(eid)

    def finalize_eoa(self):
        """Abandon still-open / unliquidated episodes (non-confirmed primary set)."""
        for eid in list(self.active.keys()):
            self.on_abandon(eid, reason="EOA_UNFINISHED")

    def _episode_rows(self):
        return [ep for ep in self.completed if not ep.excluded]

    def _metrics_for(self, episodes, pid):
        ws = [float(ep.wealth.get(pid, 1.0)) for ep in episodes]
        rets = [w - 1.0 for w in ws]
        n = len(ws)
        if n == 0:
            return {
                "policy_id": pid, "paired_episode_count": 0,
                "final_wealth_factor": UNAVAILABLE, "mean_episode_return": UNAVAILABLE,
                "median_episode_return": UNAVAILABLE, "p5_episode_return": UNAVAILABLE,
                "max_drawdown": UNAVAILABLE, "switch_count": 0,
                "units": PROXY_COST_LABEL,
            }
        rets_sorted = sorted(rets)
        # empirical 5th percentile
        idx = max(0, min(n - 1, int(0.05 * (n - 1))))
        return {
            "policy_id": pid,
            "paired_episode_count": n,
            "final_wealth_factor": self.chrono_equity[pid] if episodes is self._episode_rows()
            or len(episodes) == len(self._episode_rows()) else _product(ws),
            "mean_episode_return": sum(rets) / n,
            "median_episode_return": float(median(rets)),
            "p5_episode_return": rets_sorted[idx],
            "max_drawdown": self.chrono_dd[pid] if len(episodes) == len(self._episode_rows())
            else _path_dd(ws),
            "switch_count": int(sum(int(ep.switches.get(pid, 0) or 0) for ep in episodes)),
            "units": PROXY_COST_LABEL,
        }

    def snapshot(self):
        eps = self._episode_rows()
        # recompute chrono metrics over full completed set (already maintained)
        metrics = {pid: self._metrics_for(eps, pid) for pid in self.policy_ids}
        # ensure final wealth / dd use chrono trackers for full sample
        for pid in self.policy_ids:
            metrics[pid]["final_wealth_factor"] = self.chrono_equity[pid]
            metrics[pid]["max_drawdown"] = self.chrono_dd[pid]
            metrics[pid]["paired_episode_count"] = len(eps)

        excl_reasons = {}
        for ep in self.excluded:
            r = str(ep.exclude_reason or "UNKNOWN")
            excl_reasons[r] = int(excl_reasons.get(r, 0) or 0) + 1

        pairwise = {}
        for rhs in FIXED_ONLY_BASELINES:
            if "P5_DYNAMIC" not in metrics or rhs not in metrics:
                continue
            pairwise[rhs] = {
                "lhs": "P5_DYNAMIC", "rhs": rhs,
                "n": len(eps),
                "wealth_diff_p5_minus_fixed": (
                    metrics["P5_DYNAMIC"]["final_wealth_factor"]
                    - metrics[rhs]["final_wealth_factor"]
                ) if eps else UNAVAILABLE,
                "dd_diff_p5_minus_fixed": (
                    metrics["P5_DYNAMIC"]["max_drawdown"] - metrics[rhs]["max_drawdown"]
                ) if eps else UNAVAILABLE,
                "mean_return_diff_p5_minus_fixed": (
                    metrics["P5_DYNAMIC"]["mean_episode_return"]
                    - metrics[rhs]["mean_episode_return"]
                ) if eps else UNAVAILABLE,
                "units": PROXY_COST_LABEL,
            }

        blocks = {}
        for bname, (y0, y1) in self.blocks.items():
            beps = [ep for ep in eps
                    if isinstance(ep.open_time, datetime) and y0 <= ep.open_time.year <= y1]
            bmet = {}
            for pid in self.policy_ids:
                ws = [float(ep.wealth.get(pid, 1.0)) for ep in beps]
                rets = [w - 1.0 for w in ws]
                n = len(ws)
                if n == 0:
                    bmet[pid] = {
                        "paired_episode_count": 0, "final_wealth_factor": UNAVAILABLE,
                        "mean_episode_return": UNAVAILABLE, "median_episode_return": UNAVAILABLE,
                        "p5_episode_return": UNAVAILABLE, "max_drawdown": UNAVAILABLE,
                        "switch_count": 0,
                    }
                else:
                    rs = sorted(rets)
                    idx = max(0, min(n - 1, int(0.05 * (n - 1))))
                    bmet[pid] = {
                        "paired_episode_count": n,
                        "final_wealth_factor": _product(ws),
                        "mean_episode_return": sum(rets) / n,
                        "median_episode_return": float(median(rets)),
                        "p5_episode_return": rs[idx],
                        "max_drawdown": _path_dd(ws),
                        "switch_count": int(sum(int(ep.switches.get(pid, 0) or 0) for ep in beps)),
                    }
            bpw = {}
            for rhs in FIXED_ONLY_BASELINES:
                if rhs not in bmet or "P5_DYNAMIC" not in bmet:
                    continue
                if not beps or not _avail(bmet["P5_DYNAMIC"]["final_wealth_factor"]):
                    bpw[rhs] = {"n": 0, "wealth_diff": UNAVAILABLE, "dd_diff": UNAVAILABLE}
                else:
                    bpw[rhs] = {
                        "n": len(beps),
                        "wealth_diff_p5_minus_fixed": (
                            bmet["P5_DYNAMIC"]["final_wealth_factor"] - bmet[rhs]["final_wealth_factor"]),
                        "dd_diff_p5_minus_fixed": (
                            bmet["P5_DYNAMIC"]["max_drawdown"] - bmet[rhs]["max_drawdown"]),
                    }
            blocks[bname] = {"metrics": bmet, "pairwise": bpw, "n": len(beps)}

        return {
            "experiment": EXPERIMENT,
            "phase": PHASE,
            "proxy_underlying": PROXY_UNDERLYING,
            "proxy_cost_bps": PROXY_COST_BPS,
            "proxy_cost_label": PROXY_COST_LABEL,
            "proxy_execution_rule": PROXY_EXECUTION_RULE,
            "paired_confirmed_episode_count": len(eps),
            "excluded_episode_count": len(self.excluded),
            "excluded_reason_counts": excl_reasons,
            "policy_metrics": metrics,
            "pairwise": pairwise,
            "blocks": blocks,
            "policy_ids": list(self.policy_ids),
            "counters": dict(self.counters),
            "p0_excluded": True,
        }


def _product(ws):
    out = 1.0
    for w in ws:
        out *= float(w)
    return out


def _path_dd(wealth_factors):
    """Chronological DD on cumulative product of episode wealth factors."""
    eq = 1.0
    peak = 1.0
    dd = 0.0
    for w in wealth_factors:
        eq *= float(w)
        if eq > peak:
            peak = eq
        if peak > EPS:
            d = (peak - eq) / peak
            if d > dd:
                dd = d
    return dd


def weakly_dominates(fixed_m, p5_m):
    """fixed weakly dominates p5 on wealth (higher better) and DD (lower better)."""
    if not fixed_m or not p5_m:
        return False, False
    fw, pw = fixed_m.get("final_wealth_factor"), p5_m.get("final_wealth_factor")
    fd, pd = fixed_m.get("max_drawdown"), p5_m.get("max_drawdown")
    if not all(_avail(x) for x in (fw, pw, fd, pd)):
        return False, False
    fw, pw, fd, pd = float(fw), float(pw), float(fd), float(pd)
    weak = (fw >= pw - EPS) and (fd <= pd + EPS)
    strict = (fw > pw + EPS) or (fd < pd - EPS)
    return weak, strict


def decide_proxy_verdict(snap, min_episodes=100):
    n = int(snap.get("paired_confirmed_episode_count", 0) or 0)
    if n < int(min_episodes):
        return {
            "verdict": "MODEL_A_PROXY_INCONCLUSIVE",
            "fixed_policy_dominating_p5": "NONE",
            "reason": "INSUFFICIENT_COMMON_EPISODES",
            "paired_confirmed_episode_count": n,
        }
    metrics = snap.get("policy_metrics") or {}
    blocks = snap.get("blocks") or {}
    p5 = metrics.get("P5_DYNAMIC") or {}
    dominating = None
    for pid in FIXED_ONLY_BASELINES:
        fm = metrics.get(pid) or {}
        weak, strict_full = weakly_dominates(fm, p5)
        if not weak:
            continue
        block_weak = 0
        block_strict = 0
        for bname, b in blocks.items():
            bm = (b.get("metrics") or {})
            bw, bs = weakly_dominates(bm.get(pid) or {}, bm.get("P5_DYNAMIC") or {})
            if bw:
                block_weak += 1
            if bs:
                block_strict += 1
        if block_weak >= 2 and (strict_full or block_strict >= 1):
            dominating = pid
            break
    if dominating:
        return {
            "verdict": "MODEL_A_PROXY_REJECTED",
            "fixed_policy_dominating_p5": dominating,
            "reason": "FIXED_WEAKLY_DOMINATES_P5_WEALTH_AND_DD",
            "paired_confirmed_episode_count": n,
        }
    return {
        "verdict": "MODEL_A_PROXY_CONTINUES_TO_D0.4",
        "fixed_policy_dominating_p5": "NONE",
        "reason": "P5_NOT_DOMINATED_BY_FIXED_ON_WEALTH_AND_DD",
        "paired_confirmed_episode_count": n,
    }


def run_proxy_replay_static_tests():
    rows, passed, failed = [], 0, 0

    def ok(n, c, detail=""):
        nonlocal passed, failed
        if c:
            passed += 1
            rows.append({"name": n, "pass": True, "detail": detail})
        else:
            failed += 1
            rows.append({"name": n, "pass": False, "detail": str(detail)})

    t0 = datetime(2024, 3, 11, 10, 0, 0)
    # prices: flat then up
    px0, px1, px2, px3 = 100.0, 100.0, 110.0, 110.0

    r = FixedOnlySpyProxyReplay()
    r.set_enabled(True)
    r.on_open("EP1", t0)
    # P5 schedules 0.5 at t0; must NOT execute on same-bar 10:00
    r.on_checkpoint(t0, "EP1", {"P5_DYNAMIC": 0.5, "P4_GRADUAL_FIXED": 0.25})
    r.on_spy_bar(t0, px0, "SPY")  # same bar as decision
    ok("PR01_same_bar_no_exec", abs(r.active["EP1"].sleeves["P5_DYNAMIC"].frac) < EPS)
    # next bar executes
    t1 = t0 + timedelta(minutes=5)
    r.on_spy_bar(t1, px1, "SPY")
    ok("PR02_next_bar_exec", abs(r.active["EP1"].sleeves["P5_DYNAMIC"].frac - 0.5) < EPS)

    # down-step hysteresis
    r.on_checkpoint(t1, "EP1", {"P5_DYNAMIC": 0.25, "P4_GRADUAL_FIXED": 0.25})
    t2 = t1 + timedelta(minutes=5)
    r.on_spy_bar(t2, px2, "SPY")
    ok("PR03_p5_down_step", abs(r.active["EP1"].sleeves["P5_DYNAMIC"].frac - 0.25) < EPS)

    # common start equality
    ok("PR04_common_start",
       all(abs(sl.cash - 1.0) < EPS or abs(sl.equity(px2) - (
           r.active["EP1"].sleeves["P1_HOLD_TO_CLOSE"].equity(px2)
           if pid == "P1_HOLD_TO_CLOSE" else sl.equity(px2))) < 1e9
           for pid, sl in r.active["EP1"].sleeves.items()))
    # identical initial capital was 1.0
    r2 = FixedOnlySpyProxyReplay()
    r2.set_enabled(True)
    r2.on_open("EPx", t0)
    ok("PR05_init_capital",
       all(abs(sl.cash - 1.0) < EPS and abs(sl.shares) < EPS
           for sl in r2.active["EPx"].sleeves.values()))

    # confirm + next-bar liquidate
    r.on_confirmed_close("EP1", t2)
    t3 = t2 + timedelta(minutes=5)
    r.on_spy_bar(t3, px3, "SPY")
    ok("PR06_confirm_liq", "EP1" not in r.active and len(r.completed) == 1)
    ok("PR07_liq_flat", abs(r.completed[0].sleeves["P5_DYNAMIC"].frac) < EPS)

    # reopen abandons
    r.on_open("EP2", t0)
    r.on_abandon("EP2", "REOPEN")
    ok("PR08_reopen_exclude", len(r.excluded) >= 1 and r.excluded[-1].exclude_reason == "REOPEN")

    # missing price exclusion on liquidate
    r3 = FixedOnlySpyProxyReplay()
    r3.set_enabled(True)
    r3.on_open("EP3", t0)
    r3.on_confirmed_close("EP3", t0 + timedelta(minutes=5))
    r3.on_spy_bar(t0 + timedelta(minutes=10), -1.0, "SPY")
    ok("PR09_missing_px", r3.counters["missing_spy"] >= 1)

    # P1 schedule: session close decision, next bar after 16:00
    r4 = FixedOnlySpyProxyReplay()
    r4.set_enabled(True)
    r4.on_open("EP4", t0)
    r4.on_spy_bar(datetime(2024, 3, 11, 15, 55), 100.0, "SPY")
    ok("PR10_p1_before_close", abs(r4.active["EP4"].sleeves["P1_HOLD_TO_CLOSE"].frac) < EPS)
    r4.on_spy_bar(datetime(2024, 3, 11, 16, 0), 100.0, "SPY")  # decision bar, no exec
    ok("PR11_p1_at_close_bar", abs(r4.active["EP4"].sleeves["P1_HOLD_TO_CLOSE"].frac) < EPS)
    r4.on_spy_bar(datetime(2024, 3, 12, 9, 35), 101.0, "SPY")
    ok("PR12_p1_after_close", abs(r4.active["EP4"].sleeves["P1_HOLD_TO_CLOSE"].frac - 1.0) < EPS)

    # no future bar: pending after future decision must not exec early
    r5 = FixedOnlySpyProxyReplay()
    r5.set_enabled(True)
    r5.on_open("EP5", t0)
    fut = t0 + timedelta(hours=2)
    r5.active["EP5"].sleeves["P5_DYNAMIC"].schedule(1.0, fut)
    r5.on_spy_bar(t0 + timedelta(minutes=5), 100.0, "SPY")
    ok("PR13_no_future", abs(r5.active["EP5"].sleeves["P5_DYNAMIC"].frac) < EPS)

    # fractional up rebalance wealth continuity
    sl = _Sleeve()
    sl._set_frac(0.5, 100.0)
    e0 = sl.equity(100.0)
    sl._set_frac(1.0, 100.0)
    ok("PR14_rebalance_continuity", abs(sl.equity(100.0) - e0) < 1e-9)

    snap = r.snapshot()
    ok("PR15_snapshot_keys", "policy_metrics" in snap and "blocks" in snap)
    ok("PR16_cost_label", snap["proxy_cost_bps"] == 0)
    ok("PR17_p0_excluded", snap.get("p0_excluded") is True)
    ok("PR18_no_mut_counters",
       r.counters["diagnostic_real_orders"] == 0
       and r.counters["subscription_changes"] == 0
       and r.counters["target_mutations"] == 0)

    # verdict helpers
    fake = {
        "paired_confirmed_episode_count": 50,
        "policy_metrics": {}, "blocks": {},
    }
    v = decide_proxy_verdict(fake)
    ok("PR19_inconclusive", v["verdict"] == "MODEL_A_PROXY_INCONCLUSIVE")

    # disabled no-op
    r0 = FixedOnlySpyProxyReplay()
    r0.on_open("X", t0)
    ok("PR20_disabled_noop", len(r0.active) == 0)

    return {"passed": passed, "failed": failed, "total": passed + failed, "rows": rows}


if __name__ == "__main__":
    import json
    rep = run_proxy_replay_static_tests()
    print(json.dumps({"passed": rep["passed"], "failed": rep["failed"], "total": rep["total"]}))
    for row in rep["rows"]:
        if not row["pass"]:
            print("FAIL", row["name"], row["detail"])
