# region imports
from AlgorithmImports import *
from datetime import date as _date, datetime as _dt, timedelta as _td
# endregion
# cg_agx_shadow_diag.py
# CG-AGX-SHADOW-D0: regime-dependent gross multiplier shadow grid (448 policies).
# Diagnostic-only. No orders. No production weight mutation.

_RON = (0.00, 0.50, 0.75, 1.00, 1.10, 1.20, 1.30, 1.50)
_NEU = (0.00, 0.25, 0.50, 0.75, 0.90, 1.00, 1.10, 1.25)
_ROFF = (0.00, 0.25, 0.40, 0.55, 0.70, 0.85, 1.00)
_CTRL = "AGX_RON100_NEU100_ROFF100"
_LOG_BUDGET = 90000
_CASH_FALLBACK = frozenset(("BIL", "SGOV", "USFR"))


def _pid(a, b, c):
    return f"AGX_RON{int(round(a * 100)):03d}_NEU{int(round(b * 100)):03d}_ROFF{int(round(c * 100)):03d}"


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


def _safe_div(a, b):
    try:
        if b is None or abs(float(b)) < 1e-15:
            return None
        return float(a) / float(b)
    except Exception:
        return None


def _build_grid():
    out = []
    for a in _RON:
        for b in _NEU:
            for c in _ROFF:
                out.append((_pid(a, b, c), float(a), float(b), float(c)))
    return out


def _new_pol(pid, ron, neu, roff):
    return {
        "id": pid, "ron": ron, "neu": neu, "roff": roff,
        "w": {}, "pending": None, "nav": 1.0, "peak": 1.0, "maxdd": 0.0,
        "rets": [], "dates": [],
        "sum_r": 0.0, "sum_r2": 0.0, "n": 0, "pos": 0,
        "turnover": 0.0, "cost": 0.0, "reb": 0, "cap_hit": 0,
        "sum_eg": 0.0, "n_eg": 0, "egs": [],
        "rg_n": {"RISK_ON": 0, "NEUTRAL": 0, "RISK_OFF": 0},
        "rg_r": {"RISK_ON": 0.0, "NEUTRAL": 0.0, "RISK_OFF": 0.0},
        "rg_dd": {"RISK_ON": 0.0, "NEUTRAL": 0.0, "RISK_OFF": 0.0},
        "rg_w5": {"RISK_ON": [], "NEUTRAL": [], "RISK_OFF": []},
        "rg_bg": {"RISK_ON": 0.0, "NEUTRAL": 0.0, "RISK_OFF": 0.0},
        "rg_eg": {"RISK_ON": 0.0, "NEUTRAL": 0.0, "RISK_OFF": 0.0},
        "rg_cap": {"RISK_ON": 0, "NEUTRAL": 0, "RISK_OFF": 0},
        "tr_n": {"0": 0, "1": 0, "2-3": 0, "4-10": 0, ">10": 0},
        "tr_r": {"0": 0.0, "1": 0.0, "2-3": 0.0, "4-10": 0.0, ">10": 0.0},
        "uw": 0, "uw_max": 0, "uw_days": 0,
        "emerg": 0, "ro": 0,
    }


class CgAgxShadowDiagMixin:
    """448-policy AGX gross-multiplier shadow. Diagnostic only."""

    def CgAgxShadowInit(self) -> None:
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

        self.cg_agx_shadow_diag_enable = _bool("cg_agx_shadow_diag_enable", "0")
        self.cg_agx_shadow_emit_events = _bool("cg_agx_shadow_emit_events", "0")
        req_cap = _float("cg_agx_shadow_max_gross", 2.00)
        prod_cap = float(getattr(self, "max_total_exposure", 1.90) or 1.90)
        self._agx_prod_gross_cap = prod_cap
        self._agx_max_gross = min(float(req_cap), float(prod_cap))
        self._agx_cost_bps = _float("cg_agx_shadow_cost_bps", 0.0)
        self._agx_log_used = 0
        self._agx_err = 0
        self._agx_emitted = False
        self._agx_prev_px = None
        self._agx_last_mark = None
        self._agx_started = False
        self._agx_start_nav_actual = None
        self._agx_dates = []
        self._agx_actual_rets = []
        self._agx_actual_nav = 1.0
        self._agx_actual_peak = 1.0
        self._agx_actual_maxdd = 0.0
        self._agx_prev_actual_nav = None
        self._agx_base_gross_obs = []
        self._agx_regime_prev = None
        self._agx_regime_age = 10**9
        self._agx_n_cap = 0
        self._agx_n_imm = 0
        self._agx_n_def = 0
        self._agx_n_exe = 0
        cash = getattr(self, "sym_cash", None)
        self._agx_cash_tk = _tk(cash) if cash is not None else "BIL"
        lp = list(getattr(self, "log_only_prefixes", None) or [])
        for pref in (
            "CG_AGX_SHADOW_",
            "[INIT] CG_AGX_SHADOW",
        ):
            if pref not in lp:
                lp.append(pref)
        self.log_only_prefixes = lp
        self.log(
            f"CG_AGX_SHADOW_INIT,enable={int(self.cg_agx_shadow_diag_enable)},"
            f"policies={448 if self.cg_agx_shadow_diag_enable else 0},"
            f"max_gross={_f(self._agx_max_gross)},"
            f"prod_cap={_f(prod_cap)},req_cap={_f(req_cap)},"
            f"cost_bps={_f(self._agx_cost_bps, 2)},cash={self._agx_cash_tk},"
            f"emit_events={int(self.cg_agx_shadow_emit_events)}"
        )
        self._agx_pols = []
        self._agx_by_id = {}
        self._agx_ctrl = None
        if not self.cg_agx_shadow_diag_enable:
            return
        grid = _build_grid()
        self._agx_pols = [_new_pol(pid, a, b, c) for pid, a, b, c in grid]
        for p in self._agx_pols:
            p["w"] = {self._agx_cash_tk: 1.0}
        self._agx_by_id = {p["id"]: p for p in self._agx_pols}
        self._agx_ctrl = self._agx_by_id.get(_CTRL)
        try:
            spy = getattr(self, "sym_spy", None)
            if spy is not None:
                self.schedule.on(
                    self.date_rules.every_day(spy),
                    self.time_rules.after_market_open(spy, 14),
                    self.CgAgxShadowMark,
                )
        except Exception as exc:
            self._agx_err += 1
            self.log(f"CG_AGX_SHADOW_INIT,schedule_error={type(exc).__name__}")

    def _AgxLog(self, msg):
        try:
            n = len(msg) + 1
            if self._agx_log_used + n > _LOG_BUDGET:
                return
            self.log(msg)
            self._agx_log_used += n
        except Exception:
            pass

    def _AgxPx(self, tickers=None):
        out = {}
        sec = getattr(self, "securities", None)
        if sec is None:
            return out
        want = set(tickers) if tickers is not None else None
        try:
            for k in sec.keys():
                t = _tk(k)
                if want is not None and t not in want:
                    continue
                try:
                    px = float(sec[k].price)
                    if px > 0:
                        out[t] = px
                except Exception:
                    continue
        except Exception:
            pass
        return out

    def _AgxBaseW(self, combined):
        w = {}
        for k, v in (combined or {}).items():
            try:
                wf = float(v or 0.0)
            except Exception:
                continue
            if abs(wf) < 1e-12:
                continue
            w[_tk(k)] = wf
        return w

    def _AgxGross(self, w):
        g = 0.0
        cash = self._agx_cash_tk
        for t, v in (w or {}).items():
            if t == cash or t in _CASH_FALLBACK:
                continue
            try:
                g += abs(float(v or 0.0))
            except Exception:
                pass
        return g

    def _AgxScale(self, base_w, mult):
        cash = self._agx_cash_tk
        non = {}
        for t, v in (base_w or {}).items():
            if t == cash or t in _CASH_FALLBACK:
                continue
            non[t] = float(v)
        bg = sum(abs(v) for v in non.values())
        req = float(mult)
        pre = bg * req
        cap = float(self._agx_max_gross)
        hit = 0
        if bg <= 1e-12:
            eff = 0.0
            pre = 0.0
            post = 0.0
        elif pre > cap + 1e-12:
            eff = cap / bg
            hit = 1
            post = cap
        else:
            eff = req
            post = pre
        scaled = {t: v * eff for t, v in non.items()}
        signed = sum(scaled.values())
        scaled[cash] = 1.0 - signed
        return scaled, req, eff, pre, post, hit, bg

    def _AgxRet(self, w, prev_px, curr_px):
        r = 0.0
        cash = self._agx_cash_tk
        for t, wt in (w or {}).items():
            if t == cash or t in _CASH_FALLBACK:
                continue
            p0 = (prev_px or {}).get(t)
            p1 = (curr_px or {}).get(t)
            if not p0 or not p1 or p0 <= 0:
                continue
            try:
                r += float(wt) * (p1 / p0 - 1.0)
            except Exception:
                pass
        return r

    def _AgxTrBucket(self, age):
        if age <= 0:
            return "0"
        if age == 1:
            return "1"
        if age <= 3:
            return "2-3"
        if age <= 10:
            return "4-10"
        return ">10"

    def _AgxApplyRet(self, p, r, rg, age, bg, eg, hit):
        p["n"] += 1
        p["sum_r"] += r
        p["sum_r2"] += r * r
        if r > 0:
            p["pos"] += 1
        p["nav"] = max(1e-8, p["nav"] * (1.0 + r))
        if p["nav"] > p["peak"]:
            p["peak"] = p["nav"]
            p["uw"] = 0
        else:
            p["uw"] += 1
            p["uw_days"] += 1
            if p["uw"] > p["uw_max"]:
                p["uw_max"] = p["uw"]
        dd = 1.0 - p["nav"] / max(p["peak"], 1e-9)
        if dd > p["maxdd"]:
            p["maxdd"] = dd
        p["rets"].append(r)
        p["dates"].append(self.time.date())
        if eg is not None:
            p["sum_eg"] += eg
            p["n_eg"] += 1
            p["egs"].append(eg)
        rg = str(rg or "NEUTRAL").upper()
        if rg not in p["rg_n"]:
            rg = "NEUTRAL"
        p["rg_n"][rg] += 1
        p["rg_r"][rg] += r
        p["rg_dd"][rg] = max(p["rg_dd"][rg], dd)
        p["rg_w5"][rg].append(r)
        p["rg_bg"][rg] += float(bg or 0.0)
        p["rg_eg"][rg] += float(eg or 0.0)
        if hit:
            p["rg_cap"][rg] += 1
        tb = self._AgxTrBucket(int(age))
        p["tr_n"][tb] += 1
        p["tr_r"][tb] += r

    def _AgxRebalance(self, p, new_w, hit):
        old = p["w"] or {}
        keys = set(old) | set(new_w or {})
        tov = 0.0
        for t in keys:
            tov += abs(float((new_w or {}).get(t, 0.0)) - float(old.get(t, 0.0)))
        tov *= 0.5
        p["turnover"] += tov
        c = tov * float(self._agx_cost_bps) / 10000.0
        if c > 0:
            p["nav"] = max(1e-8, p["nav"] * (1.0 - c))
            p["cost"] += c
        p["w"] = dict(new_w or {})
        p["pending"] = None
        p["reb"] += 1
        p["cap_hit"] += int(hit)

    def CgAgxShadowMark(self) -> None:
        if not getattr(self, "cg_agx_shadow_diag_enable", False):
            return
        try:
            if getattr(self, "IsWarmingUp", False) or getattr(self, "is_warming_up", False):
                return
            today = self.time.date()
            if self._agx_last_mark == today:
                return
            tickers = set()
            for p in self._agx_pols:
                tickers.update((p["w"] or {}).keys())
                if p.get("pending"):
                    tickers.update(p["pending"].keys())
            tickers.add(self._agx_cash_tk)
            curr = self._AgxPx(tickers if tickers else None)
            rg = str(getattr(self, "current_regime", None) or "NEUTRAL").upper()
            if rg not in ("RISK_ON", "NEUTRAL", "RISK_OFF"):
                rg = "NEUTRAL"
            if self._agx_regime_prev is None:
                self._agx_regime_prev = rg
                self._agx_regime_age = 10**9
            elif rg != self._agx_regime_prev:
                self._agx_regime_prev = rg
                self._agx_regime_age = 0
            else:
                self._agx_regime_age += 1
            age = self._agx_regime_age
            if not self._agx_started:
                try:
                    self._agx_start_nav_actual = float(self.portfolio.total_portfolio_value)
                except Exception:
                    self._agx_start_nav_actual = 10000.0
                self._agx_prev_actual_nav = self._agx_start_nav_actual
                self._agx_started = True
                self._agx_prev_px = curr
                self._agx_last_mark = today
                return
            # actual path
            try:
                anav = float(self.portfolio.total_portfolio_value)
            except Exception:
                anav = self._agx_prev_actual_nav or 1.0
            if self._agx_prev_actual_nav and self._agx_prev_actual_nav > 0:
                ar = anav / self._agx_prev_actual_nav - 1.0
            else:
                ar = 0.0
            self._agx_actual_rets.append(ar)
            self._agx_dates.append(today)
            self._agx_actual_nav = max(1e-8, self._agx_actual_nav * (1.0 + ar))
            if self._agx_actual_nav > self._agx_actual_peak:
                self._agx_actual_peak = self._agx_actual_nav
            add = 1.0 - self._agx_actual_nav / max(self._agx_actual_peak, 1e-9)
            if add > self._agx_actual_maxdd:
                self._agx_actual_maxdd = add
            self._agx_prev_actual_nav = anav
            if self._agx_prev_px is not None:
                for p in self._agx_pols:
                    r = self._AgxRet(p["w"], self._agx_prev_px, curr)
                    eg = self._AgxGross(p["w"])
                    self._AgxApplyRet(p, r, rg, age, eg, eg, 0)
            self._agx_prev_px = curr
            self._agx_last_mark = today
        except Exception as exc:
            self._agx_err += 1
            if self._agx_err <= 3:
                self._AgxLog(f"CG_AGX_SHADOW_VALIDATION,mark_error={type(exc).__name__}")

    def CgAgxShadowCapture(self, combined, regime, slot, reduce_only=False, emergency=False) -> None:
        if not getattr(self, "cg_agx_shadow_diag_enable", False):
            return
        try:
            if getattr(self, "IsWarmingUp", False) or getattr(self, "is_warming_up", False):
                return
            base = self._AgxBaseW(combined)
            bg = self._AgxGross(base)
            self._agx_base_gross_obs.append(bg)
            self._agx_n_cap += 1
            rg = str(regime or getattr(self, "current_regime", None) or "NEUTRAL").upper()
            if rg not in ("RISK_ON", "NEUTRAL", "RISK_OFF"):
                rg = "NEUTRAL"
            imm = bool(reduce_only or emergency or int(slot or 0) == 15)
            if imm:
                self._agx_n_imm += 1
            else:
                self._agx_n_def += 1
            for p in self._agx_pols:
                if reduce_only or emergency:
                    # No AGX experiment: apply exact base immediately.
                    self._AgxRebalance(p, base, 0)
                    if emergency:
                        p["emerg"] += 1
                    if reduce_only:
                        p["ro"] += 1
                    continue
                mult = p["ron"] if rg == "RISK_ON" else (p["roff"] if rg == "RISK_OFF" else p["neu"])
                scaled, req, eff, pre, post, hit, _bg = self._AgxScale(base, mult)
                if imm:
                    self._AgxRebalance(p, scaled, hit)
                else:
                    p["pending"] = scaled
                    if hit:
                        p["cap_hit"] += 0  # counted at exec
                    p["_pend_hit"] = hit
                    p["_pend_eg"] = post
                    p["_pend_bg"] = bg
                    p["_pend_rg"] = rg
            if self.cg_agx_shadow_emit_events:
                self._AgxLog(
                    f"CG_AGX_SHADOW_EVENT,date={self.time.date()},regime={rg},"
                    f"slot={slot},reduce={int(bool(reduce_only))},"
                    f"emerg={int(bool(emergency))},base_gross={_f(bg)},imm={int(imm)}"
                )
        except Exception as exc:
            self._agx_err += 1
            if self._agx_err <= 3:
                self._AgxLog(f"CG_AGX_SHADOW_VALIDATION,capture_error={type(exc).__name__}")

    def CgAgxShadowExecutePending(self) -> None:
        if not getattr(self, "cg_agx_shadow_diag_enable", False):
            return
        try:
            any_p = False
            for p in self._agx_pols:
                pend = p.get("pending")
                if pend is None:
                    continue
                any_p = True
                hit = int(p.pop("_pend_hit", 0) or 0)
                self._AgxRebalance(p, pend, hit)
            if any_p:
                self._agx_n_exe += 1
        except Exception as exc:
            self._agx_err += 1
            if self._agx_err <= 3:
                self._AgxLog(f"CG_AGX_SHADOW_VALIDATION,exec_error={type(exc).__name__}")

    def _AgxWindowMetrics(self, dates, rets, s, e):
        xs = []
        for d, r in zip(dates, rets):
            if s is not None and d < s:
                continue
            if e is not None and d > e:
                continue
            xs.append(r)
        return self._AgxMetricsFromRets(xs)

    def _AgxMetricsFromRets(self, rets):
        n = len(rets)
        if n <= 0:
            return None
        nav = 1.0
        peak = 1.0
        maxdd = 0.0
        uw = 0
        uw_max = 0
        uw_days = 0
        sum_r = 0.0
        sum_r2 = 0.0
        pos = 0
        dn = []
        for r in rets:
            sum_r += r
            sum_r2 += r * r
            if r > 0:
                pos += 1
            if r < 0:
                dn.append(r)
            nav = max(1e-8, nav * (1.0 + r))
            if nav > peak:
                peak = nav
                uw = 0
            else:
                uw += 1
                uw_days += 1
                if uw > uw_max:
                    uw_max = uw
            dd = 1.0 - nav / max(peak, 1e-9)
            if dd > maxdd:
                maxdd = dd
        mean = sum_r / n
        var = max(0.0, sum_r2 / n - mean * mean)
        vol = (var ** 0.5) * (252 ** 0.5)
        years = n / 252.0
        cagr = (nav ** (1.0 / years) - 1.0) if years > 0.01 else None
        sharpe = (cagr / vol) if (cagr is not None and vol > 1e-12) else None
        if dn:
            dmean = sum(dn) / n
            dvar = max(0.0, sum(x * x for x in dn) / n - dmean * dmean)
            dvol = (dvar ** 0.5) * (252 ** 0.5)
            sortino = (cagr / dvol) if (cagr is not None and dvol > 1e-12) else None
        else:
            sortino = None
        arr = sorted(rets)
        k = max(1, int(0.05 * n + 0.999))
        w5 = sum(arr[:k]) / k
        return {
            "n": n, "end_nav": nav, "total_return": nav - 1.0, "CAGR": cagr,
            "MaxDD": maxdd, "annual_stddev": vol, "Sharpe": sharpe, "Sortino": sortino,
            "worst_5pct_day_mean": w5, "worst_day": arr[0], "best_day": arr[-1],
            "recovery_days_max": uw_max,
            "time_under_water_pct": (uw_days / n) if n else None,
            "positive_day_rate": pos / n,
        }

    def _AgxPolMetrics(self, p):
        m = self._AgxMetricsFromRets(p["rets"]) or {}
        egs = sorted(p["egs"]) if p["egs"] else []
        mid = egs[len(egs) // 2] if egs else None
        p95 = egs[int(0.95 * (len(egs) - 1))] if len(egs) > 1 else (egs[0] if egs else None)
        m.update({
            "start_nav": 1.0,
            "turnover": p["turnover"],
            "estimated_cost": p["cost"],
            "rebalance_count": p["reb"],
            "cap_hit_count": p["cap_hit"],
            "mean_effective_gross": (p["sum_eg"] / p["n_eg"]) if p["n_eg"] else None,
            "median_effective_gross": mid,
            "p95_effective_gross": p95,
            "max_effective_gross": max(egs) if egs else None,
            "days_RISK_ON": p["rg_n"]["RISK_ON"],
            "days_NEUTRAL": p["rg_n"]["NEUTRAL"],
            "days_RISK_OFF": p["rg_n"]["RISK_OFF"],
            "ron": p["ron"], "neu": p["neu"], "roff": p["roff"], "id": p["id"],
        })
        return m

    def _AgxIsBoundary(self, p):
        return (
            abs(p["ron"]) < 1e-12 or abs(p["neu"]) < 1e-12 or abs(p["roff"]) < 1e-12
            or abs(p["ron"] - 1.50) < 1e-12 or abs(p["neu"] - 1.25) < 1e-12
        )

    def _AgxPareto(self, rows, ctrl):
        # Minimize MaxDD, worst5 magnitude (more negative worse), recovery;
        # maximize OOS Sharpe, minimize CRISIS MaxDD; maximize CAGR.
        # Keep non-dominated on (MaxDD, worst5, oos_sh, crisis_dd, recovery, -CAGR)
        front = []
        for r in rows:
            if not r.get("parity_ok", True):
                continue
            dominated = False
            for o in rows:
                if o is r or not o.get("parity_ok", True):
                    continue
                # o better or equal on all risk-first keys and better on at least one
                keys_min = ("MaxDD", "w5_abs", "crisis_maxdd", "recovery_days_max")
                keys_max = ("oos_sharpe", "CAGR")
                ge = all(o.get(k, 1e9) <= r.get(k, 1e9) for k in keys_min) and all(
                    (o.get(k) or -1e9) >= (r.get(k) or -1e9) for k in keys_max
                )
                gt = any(o.get(k, 1e9) < r.get(k, 1e9) for k in keys_min) or any(
                    (o.get(k) or -1e9) > (r.get(k) or -1e9) for k in keys_max
                )
                if ge and gt:
                    dominated = True
                    break
            if not dominated:
                front.append(r)
        return front

    def _AgxRankKey(self, r):
        return (
            float(r.get("MaxDD") or 9),
            float(r.get("w5_abs") or 9),
            -float(r.get("oos_sharpe") or -9),
            float(r.get("crisis_maxdd") or 9),
            float(r.get("recovery_days_max") or 9e9),
            -float(r.get("CAGR") or -9),
        )

    def _AgxNeighborStable(self, row, by_id):
        def nbr(dim, vals):
            cur = row[dim]
            ix = None
            for i, v in enumerate(vals):
                if abs(v - cur) < 1e-12:
                    ix = i
                    break
            if ix is None:
                return True
            ok = True
            for j in (ix - 1, ix + 1):
                if j < 0 or j >= len(vals):
                    continue
                kwargs = {"ron": row["ron"], "neu": row["neu"], "roff": row["roff"]}
                kwargs[dim] = vals[j]
                nid = _pid(kwargs["ron"], kwargs["neu"], kwargs["roff"])
                o = by_id.get(nid)
                if not o:
                    continue
                if (o.get("MaxDD") or 0) > (row.get("MaxDD") or 0) + 0.02:
                    ok = False
                c0 = row.get("CAGR") or 0
                c1 = o.get("CAGR") or 0
                if c0 > 0 and c1 < 0.8 * c0:
                    ok = False
                s0 = row.get("oos_sharpe") or 0
                s1 = o.get("oos_sharpe") or 0
                if s0 > 0 and s1 < 0.9 * s0:
                    ok = False
                if (o.get("crisis_maxdd") or 0) > (row.get("crisis_maxdd") or 0) + 0.02:
                    ok = False
            return ok

        ron_ok = nbr("ron", _RON)
        neu_ok = nbr("neu", _NEU)
        off_ok = nbr("roff", _ROFF)
        return ron_ok, neu_ok, off_ok, (ron_ok and neu_ok and off_ok)

    def CgAgxShadowEmitFinal(self) -> None:
        if getattr(self, "_agx_emitted", False):
            return
        self._agx_emitted = True
        if not getattr(self, "cg_agx_shadow_diag_enable", False):
            return
        try:
            today = self.time.date()
            live_s = None
            if self._agx_dates:
                live_s = self._agx_dates[max(0, len(self._agx_dates) - 252)]
            windows = [
                ("RUN", _date(2012, 1, 1), today),
                ("TRAIN_2012_2018", _date(2012, 1, 1), _date(2018, 12, 31)),
                ("OOS_2019_2021", _date(2019, 1, 1), _date(2021, 12, 31)),
                ("CRISIS_2022_2025", _date(2022, 1, 1), _date(2025, 12, 31)),
                ("Y2020", _date(2020, 1, 1), _date(2020, 12, 31)),
                ("Y2022", _date(2022, 1, 1), _date(2022, 12, 31)),
                ("Y2023", _date(2023, 1, 1), _date(2023, 12, 31)),
                ("Y2024", _date(2024, 1, 1), _date(2024, 12, 31)),
                ("Y2025", _date(2025, 1, 1), _date(2025, 12, 31)),
                ("LIVE_RECENT", live_s, today),
            ]
            obs = sorted(self._agx_base_gross_obs)
            p95_base = obs[int(0.95 * (len(obs) - 1))] if len(obs) > 1 else (obs[0] if obs else None)
            max_base = max(obs) if obs else None
            ctrl = self._agx_ctrl
            ctrl_m = self._AgxPolMetrics(ctrl) if ctrl else {}
            # parity
            try:
                actual_final = float(self.portfolio.total_portfolio_value)
            except Exception:
                actual_final = None
            start_a = float(self._agx_start_nav_actual or 0.0) or None
            shadow_final = None
            if ctrl and start_a:
                shadow_final = start_a * float(ctrl.get("nav") or 1.0)
            nav_diff = None
            if actual_final and shadow_final and start_a:
                nav_diff = (shadow_final / actual_final - 1.0) * 100.0
            maxdd_diff = None
            if ctrl_m.get("MaxDD") is not None:
                maxdd_diff = (float(ctrl_m["MaxDD"]) - float(self._agx_actual_maxdd)) * 100.0
            n_act = len(self._agx_actual_rets)
            n_sh = len(ctrl["rets"]) if ctrl else 0
            parity = (
                nav_diff is not None and abs(nav_diff) <= 0.50
                and maxdd_diff is not None and abs(maxdd_diff) <= 0.25
                and n_act == n_sh and n_act > 0
            )
            # Soft-parity fallback: relative path compare when absolute IB costs diverge
            if not parity and ctrl and n_act == n_sh and n_act > 50:
                # compare normalized MaxDD and terminal relative nav within looser band for diagnostic validity flag only
                rel_ok = (
                    abs((ctrl.get("nav") or 1) / max(self._agx_actual_nav, 1e-9) - 1.0) <= 0.05
                    and abs(float(ctrl_m.get("MaxDD") or 0) - float(self._agx_actual_maxdd)) <= 0.01
                )
                # keep strict parity_pass as specified; diagnostic_valid uses strict
                pass
            self._AgxLog(
                f"CG_AGX_SHADOW_PARITY,parity_pass={int(parity)},"
                f"actual_final_nav={_f(actual_final, 2)},"
                f"shadow_m100_final_nav={_f(shadow_final, 2)},"
                f"nav_difference_pct={_f(nav_diff)},"
                f"actual_maxdd={_f(self._agx_actual_maxdd)},"
                f"shadow_m100_maxdd={_f(ctrl_m.get('MaxDD'))},"
                f"maxdd_difference_pp={_f(maxdd_diff)},"
                f"actual_daily_return_count={n_act},"
                f"shadow_daily_return_count={n_sh},"
                f"prod_gross_cap={_f(self._agx_prod_gross_cap)},"
                f"agx_max_gross={_f(self._agx_max_gross)},"
                f"obs_max_target_gross={_f(max_base)},"
                f"obs_p95_target_gross={_f(p95_base)}"
            )
            diagnostic_valid = int(parity and self._agx_err == 0)
            selection_allowed = diagnostic_valid
            rows = []
            for p in self._agx_pols:
                m = self._AgxPolMetrics(p)
                # windows
                wins = {}
                missing = 0
                for name, s, e in windows:
                    if s is None:
                        wins[name] = None
                        missing += 1
                        continue
                    wm = self._AgxWindowMetrics(p["dates"], p["rets"], s, e)
                    wins[name] = wm
                    if wm is None or wm.get("n", 0) <= 0:
                        if name in ("RUN", "TRAIN_2012_2018", "OOS_2019_2021", "CRISIS_2022_2025"):
                            missing += 1
                oos = wins.get("OOS_2019_2021") or {}
                cri = wins.get("CRISIS_2022_2025") or {}
                y20 = wins.get("Y2020") or {}
                y22 = wins.get("Y2022") or {}
                std = m.get("annual_stddev")
                invalid = 0
                if not parity:
                    invalid = 1
                if self._agx_err > 0:
                    invalid = 1
                if missing:
                    invalid = 1
                if n_act != n_sh:
                    invalid = 1
                if (m.get("max_effective_gross") or 0) > self._agx_max_gross + 1e-6:
                    invalid = 1
                if std is not None and std > 0.20:
                    invalid = 1
                row = dict(m)
                row["wins"] = wins
                row["oos_sharpe"] = oos.get("Sharpe")
                row["crisis_maxdd"] = cri.get("MaxDD")
                row["y2020_maxdd"] = y20.get("MaxDD")
                row["y2022_maxdd"] = y22.get("MaxDD")
                row["w5_abs"] = -float(m.get("worst_5pct_day_mean") or 0)
                row["parity_ok"] = parity
                row["invalid"] = invalid
                row["boundary"] = int(self._AgxIsBoundary(p))
                # D0_PASS vs control
                d0 = 0
                tgt = 0
                if selection_allowed and not invalid and ctrl_m:
                    def _ge(a, b):
                        return a is not None and b is not None and a >= b

                    def _le(a, b):
                        return a is not None and b is not None and a <= b

                    c_oos = (self._AgxWindowMetrics(ctrl["dates"], ctrl["rets"], _date(2019, 1, 1), _date(2021, 12, 31)) or {})
                    c_cri = (self._AgxWindowMetrics(ctrl["dates"], ctrl["rets"], _date(2022, 1, 1), _date(2025, 12, 31)) or {})
                    c_y20 = (self._AgxWindowMetrics(ctrl["dates"], ctrl["rets"], _date(2020, 1, 1), _date(2020, 12, 31)) or {})
                    c_y22 = (self._AgxWindowMetrics(ctrl["dates"], ctrl["rets"], _date(2022, 1, 1), _date(2022, 12, 31)) or {})
                    ok = (
                        _le(m.get("MaxDD"), ctrl_m.get("MaxDD"))
                        and _ge(m.get("worst_5pct_day_mean"), ctrl_m.get("worst_5pct_day_mean"))
                        and _ge(oos.get("Sharpe"), 0.95 * (c_oos.get("Sharpe") or 0))
                        and _le(cri.get("MaxDD"), (c_cri.get("MaxDD") or 0) + 0.01)
                        and _le(y20.get("MaxDD"), (c_y20.get("MaxDD") or 0) + 0.01)
                        and _le(y22.get("MaxDD"), (c_y22.get("MaxDD") or 0) + 0.01)
                        and (std is not None and std <= 0.18)
                        and _le(m.get("recovery_days_max"), ctrl_m.get("recovery_days_max"))
                        and _ge(m.get("CAGR"), ctrl_m.get("CAGR"))
                        and (m.get("CAGR") or 0) > (ctrl_m.get("CAGR") or 0)
                    )
                    d0 = int(ok)
                    if (
                        (m.get("CAGR") or 0) >= 0.45
                        and (m.get("MaxDD") or 9) <= 0.13
                        and (std or 9) <= 0.18
                    ):
                        tgt = 1
                row["D0_PASS"] = d0
                row["TARGET_PROFILE_MET"] = tgt
                row["_p"] = p
                rows.append(row)

            valid_rows = [r for r in rows if not r["invalid"]]
            if not selection_allowed:
                valid_rows = []
            front = self._AgxPareto(valid_rows, ctrl_m) if valid_rows else []
            ranked = sorted(valid_rows, key=self._AgxRankKey) if valid_rows else []
            top20 = ranked[:20]
            d0s = [r for r in ranked if r["D0_PASS"]]
            tgts = [r for r in ranked if r["TARGET_PROFILE_MET"]]
            # sensitivity on top20
            by_id = {r["id"]: r for r in rows}
            for r in top20:
                a, b, c, all_ok = self._AgxNeighborStable(r, by_id)
                r["ron_neighbor_stable"] = int(a)
                r["neutral_neighbor_stable"] = int(b)
                r["riskoff_neighbor_stable"] = int(c)
                r["all_neighbors_stable"] = int(all_ok)
            # boundary audit
            b_all = [r for r in ranked if r["boundary"]]
            b_front = [r for r in front if r["boundary"]]
            b_d0 = [r for r in d0s if r["boundary"]]
            best_b = b_all[0] if b_all else None
            best_i = next((r for r in ranked if not r["boundary"]), None)
            b_adv_cagr = b_adv_dd = b_adv_oos = None
            if best_b and best_i:
                b_adv_cagr = (best_b.get("CAGR") or 0) - (best_i.get("CAGR") or 0)
                b_adv_dd = (best_b.get("MaxDD") or 0) - (best_i.get("MaxDD") or 0)
                b_adv_oos = (best_b.get("oos_sharpe") or 0) - (best_i.get("oos_sharpe") or 0)
            b_class = "NA"
            if best_b:
                if best_b.get("all_neighbors_stable", 1) and best_b in d0s:
                    b_class = "BOUNDARY_ROBUST"
                elif abs(best_b["ron"] - 1.50) < 1e-12 or abs(best_b["neu"] - 1.25) < 1e-12:
                    b_class = "BOUNDARY_NEEDS_EXTENSION"
                else:
                    b_class = "BOUNDARY_UNSTABLE"

            # CSV artifact
            csv_key = "cg_agx_shadow_d0.csv"
            try:
                headers = [
                    "id", "ron", "neu", "roff", "CAGR", "MaxDD", "annual_stddev", "Sharpe", "Sortino",
                    "worst_5pct_day_mean", "worst_day", "best_day", "recovery_days_max",
                    "time_under_water_pct", "positive_day_rate", "turnover", "estimated_cost",
                    "rebalance_count", "cap_hit_count", "mean_effective_gross", "median_effective_gross",
                    "p95_effective_gross", "max_effective_gross", "days_RISK_ON", "days_NEUTRAL",
                    "days_RISK_OFF", "oos_sharpe", "crisis_maxdd", "y2020_maxdd", "y2022_maxdd",
                    "D0_PASS", "TARGET_PROFILE_MET", "boundary", "invalid",
                ]
                lines = [",".join(headers)]
                for r in rows:
                    lines.append(",".join(str(r.get(h, "NA")) for h in headers))
                self.object_store.save(csv_key, "\n".join(lines))
            except Exception as exc:
                csv_key = f"NONE:{type(exc).__name__}"

            best = d0s[0] if d0s else (ranked[0] if ranked else None)
            # NEXT decision
            stable_d0 = [r for r in d0s if r.get("all_neighbors_stable")]
            # transition concentration for control
            next_dec = "STOP_AGX"
            if not selection_allowed or not ranked:
                next_dec = "STOP_AGX"
            elif stable_d0:
                next_dec = "PREPARE_AGX_SHADOW_D1"
            elif d0s or front:
                # check transition concentration on control
                tr = ctrl["tr_r"] if ctrl else {}
                early = abs(tr.get("0", 0) + tr.get("1", 0) + tr.get("2-3", 0) + tr.get("4-10", 0))
                late = abs(tr.get(">10", 0))
                if early > 0 and early >= 1.25 * max(late, 1e-9) and not stable_d0:
                    next_dec = "TEST_RISK_REENTRY"
                else:
                    next_dec = "REFINE_AGX"
            elif front:
                next_dec = "REFINE_AGX"

            try:
                self.set_runtime_statistic("AGX_PARITY", str(int(parity)))
                self.set_runtime_statistic("AGX_NEXT", str(next_dec))
                self.set_runtime_statistic("AGX_D0", str(len(d0s)))
                self.set_runtime_statistic("AGX_BEST", str((best or {}).get("id", "NONE")))
                if best:
                    self.set_runtime_statistic("AGX_BEST_CAGR", _f(best.get("CAGR")))
                    self.set_runtime_statistic("AGX_BEST_DD", _f(best.get("MaxDD")))
            except Exception:
                pass
            self._AgxLog(
                f"CG_AGX_SHADOW_FINAL,diagnostic_valid={diagnostic_valid},"
                f"selection_allowed={selection_allowed},policies={len(rows)},"
                f"parity_pass={int(parity)},errors={self._agx_err},"
                f"d0_pass={len(d0s)},target_profile_met={len(tgts)},"
                f"pareto={len(front)},next={next_dec},artifact={csv_key},"
                f"captured={self._agx_n_cap},immediate={self._agx_n_imm},"
                f"deferred={self._agx_n_def},executed={self._agx_n_exe}"
            )
            self._AgxLog(
                f"CG_AGX_SHADOW_VALIDATION,boundary_policy_count={len(b_all)},"
                f"boundary_pareto_count={len(b_front)},boundary_d0_pass_count={len(b_d0)},"
                f"best_boundary_policy={(best_b or {}).get('id', 'NONE')},"
                f"best_interior_policy={(best_i or {}).get('id', 'NONE')},"
                f"boundary_advantage_cagr={_f(b_adv_cagr)},"
                f"boundary_advantage_maxdd={_f(b_adv_dd)},"
                f"boundary_advantage_oos_sharpe={_f(b_adv_oos)},"
                f"boundary_class={b_class}"
            )
            # control + top windows
            emit_ids = set()
            if ctrl:
                emit_ids.add(ctrl["id"])
            for r in top20:
                emit_ids.add(r["id"])
            for r in front:
                emit_ids.add(r["id"])
            for r in d0s:
                emit_ids.add(r["id"])
            for r in rows:
                if r["id"] not in emit_ids:
                    continue
                for name, s, e in windows:
                    wm = (r.get("wins") or {}).get(name)
                    if not wm:
                        continue
                    self._AgxLog(
                        f"CG_AGX_SHADOW_WINDOW,id={r['id']},window={name},"
                        f"n={wm.get('n')},CAGR={_f(wm.get('CAGR'))},"
                        f"MaxDD={_f(wm.get('MaxDD'))},Sharpe={_f(wm.get('Sharpe'))},"
                        f"std={_f(wm.get('annual_stddev'))},"
                        f"w5={_f(wm.get('worst_5pct_day_mean'))},"
                        f"rec={wm.get('recovery_days_max')}"
                    )
            for i, r in enumerate(top20):
                self._AgxLog(
                    f"CG_AGX_SHADOW_TOP,rank={i + 1},id={r['id']},"
                    f"ron={_f(r['ron'], 2)},neu={_f(r['neu'], 2)},roff={_f(r['roff'], 2)},"
                    f"CAGR={_f(r.get('CAGR'))},MaxDD={_f(r.get('MaxDD'))},"
                    f"std={_f(r.get('annual_stddev'))},Sharpe={_f(r.get('Sharpe'))},"
                    f"w5={_f(r.get('worst_5pct_day_mean'))},oos_sh={_f(r.get('oos_sharpe'))},"
                    f"crisis_dd={_f(r.get('crisis_maxdd'))},rec={r.get('recovery_days_max')},"
                    f"D0={r.get('D0_PASS')},stable={r.get('all_neighbors_stable', 0)},"
                    f"boundary={r.get('boundary')}"
                )
            # regime / transition for control + top5
            for r in ([by_id.get(_CTRL)] if _CTRL in by_id else []) + top20[:5]:
                if not r:
                    continue
                p = r.get("_p")
                if not p:
                    continue
                for rg in ("RISK_ON", "NEUTRAL", "RISK_OFF"):
                    n = p["rg_n"][rg]
                    w5l = p["rg_w5"][rg]
                    w5 = (sum(sorted(w5l)[:max(1, int(0.05 * len(w5l) + 0.999))]) / max(1, int(0.05 * len(w5l) + 0.999))) if w5l else None
                    self._AgxLog(
                        f"CG_AGX_SHADOW_REGIME,id={p['id']},regime={rg},days={n},"
                        f"ret_contrib={_f(p['rg_r'][rg])},dd={_f(p['rg_dd'][rg])},"
                        f"w5={_f(w5)},mean_base_gross={_f(p['rg_bg'][rg] / n if n else None)},"
                        f"mean_eff_gross={_f(p['rg_eg'][rg] / n if n else None)},"
                        f"cap_hit={p['rg_cap'][rg]}"
                    )
                for tb in ("0", "1", "2-3", "4-10", ">10"):
                    self._AgxLog(
                        f"CG_AGX_SHADOW_TRANSITION,id={p['id']},bucket={tb},"
                        f"days={p['tr_n'][tb]},ret_contrib={_f(p['tr_r'][tb])}"
                    )
            self._agx_result = {
                "parity": parity, "best": best, "d0": d0s, "front": front,
                "top20": top20, "next": next_dec, "artifact": csv_key,
                "ctrl": ctrl_m, "valid": diagnostic_valid,
                "live_recent_start": str(live_s), "live_recent_end": str(today),
            }
        except Exception as exc:
            self._agx_err += 1
            try:
                self.log(f"CG_AGX_SHADOW_VALIDATION,emit_error={type(exc).__name__}:{exc}")
            except Exception:
                pass
