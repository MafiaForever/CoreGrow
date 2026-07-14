# cg_ids_normal_cap_diag.py
# IDS-NORMAL-CAP-D0: one-run shadow matrix for IDS transition gross caps.
# Diagnostic-only. Zero trading impact. Never mutates real targets.
from datetime import date as _date
from collections import deque

_CANDS = ("BASE", "C1", "C2", "C3", "C4")
# (mode, WATCH_val, STRESS_val); mode: None=BASE, "cap", "mult"
_SPEC = {
    "BASE": None,
    "C1": ("cap", 1.10, 0.80),
    "C2": ("cap", 0.90, 0.60),
    "C3": ("cap", 0.75, 0.40),
    "C4": ("mult", 0.70, 0.45),
}
_WINDOWS = (
    ("RUN",         _date(2012, 1, 1), None),
    ("TRAIN",       _date(2012, 1, 1), _date(2018, 12, 31)),
    ("OOS",         _date(2019, 1, 1), _date(2021, 12, 31)),
    ("CRISIS",      _date(2022, 1, 1), _date(2025, 12, 31)),
    ("Y2020",       _date(2020, 1, 1), _date(2020, 12, 31)),
    ("Y2022",       _date(2022, 1, 1), _date(2022, 12, 31)),
    ("Y2023",       _date(2023, 1, 1), _date(2023, 12, 31)),
    ("Y2024",       _date(2024, 1, 1), _date(2024, 12, 31)),
    ("Y2025",       _date(2025, 1, 1), _date(2025, 12, 31)),
    ("LIVE_RECENT", _date(2026, 1, 1), None),
)
_STATES = ("NORMAL_IDS_WATCH", "NORMAL_IDS_STRESS")
_LOG_BUDGET = 30000
_LINE_MAX = 1800
_CASH_TK = frozenset(("BIL", "SGOV", "USFR"))


def _blank():
    return {"n": 0, "sum_r": 0.0, "sum_r2": 0.0, "nav": 1.0, "peak": 1.0, "maxdd": 0.0,
            "pos": 0, "sum_g": 0.0, "rets": deque(maxlen=4096)}


def _upd(st, r, gross):
    st["n"] += 1
    st["sum_r"] += r
    st["sum_r2"] += r * r
    st["nav"] = max(0.01, st["nav"] * (1.0 + r))
    if st["nav"] > st["peak"]:
        st["peak"] = st["nav"]
    dd = 1.0 - st["nav"] / max(st["peak"], 1e-9)
    if dd > st["maxdd"]:
        st["maxdd"] = dd
    if r > 0:
        st["pos"] += 1
    st["sum_g"] += gross
    st["rets"].append(r)


def _w5(rets):
    if not rets:
        return None
    arr = sorted(rets)
    k = max(1, int(0.05 * len(arr) + 0.999))
    return sum(arr[:k]) / k


def _ann(sum_r, n):
    if n < 20:
        return None
    return (1.0 + sum_r / n) ** 252 - 1.0


def _vol(sum_r, sum_r2, n):
    if n < 5:
        return None
    m = sum_r / n
    v = max(0.0, sum_r2 / n - m * m)
    return (v ** 0.5) * (252 ** 0.5)


def _sharpe(sum_r, sum_r2, n):
    v = _vol(sum_r, sum_r2, n)
    a = _ann(sum_r, n)
    if v is None or a is None or v < 1e-12:
        return None
    return a / v


def _f(x, d=4):
    if x is None:
        return "NA"
    try:
        return f"{float(x):.{d}f}"
    except Exception:
        return "NA"


def _tk(sym):
    try:
        return str(sym.Value)
    except Exception:
        try:
            return str(sym.value)
        except Exception:
            return str(sym)


class CgIdsNormalCapDiagMixin:

    def CgIdsNormalCapInit(self) -> None:
        try:
            ov = getattr(self, "_rrx_param_overrides", {}) or {}
            def _p(k, d=""):
                v = self.get_parameter(k)
                if v is None or str(v).strip() == "":
                    v = ov.get(k, d)
                return v
            en = str(_p("cg_ids_normal_cap_diag_enable", "1") or "1").strip().lower()
            self.cg_ids_normal_cap_diag_enable = en in ("1", "true", "yes", "on")
            # QC project parameters override RRX_PARAMS defaults; mandatory diagnostic
            # prefix must therefore be appended at runtime.
            if self.cg_ids_normal_cap_diag_enable:
                lp = list(getattr(self, "log_only_prefixes", None) or [])
                if "CG_IDS_" not in lp:
                    lp.append("CG_IDS_")
                self.log_only_prefixes = lp
                mp = list(getattr(self, "log_mute_prefixes", None) or [])
                if "CG_IDS_" in mp:
                    self.log_mute_prefixes = [x for x in mp if x != "CG_IDS_"]
            self.log("[INIT] CG_IDS_NORMAL_CAP_DIAG enable="
                     f"{int(self.cg_ids_normal_cap_diag_enable)} candidates=4 trade_impact=ZERO")
            if not self.cg_ids_normal_cap_diag_enable:
                return
            self._idn_run = {c: _blank() for c in _CANDS}
            self._idn_win = {(c, w[0]): _blank() for c in _CANDS for w in _WINDOWS}
            self._idn_state = {}
            for c in _CANDS:
                for sk in _STATES:
                    self._idn_state[(c, sk)] = {
                        "n": 0, "nav": 1.0, "peak": 1.0, "maxdd": 0.0,
                        "sum_r": 0.0, "sum_gb": 0.0, "sum_ga": 0.0,
                        "rets": deque(maxlen=2048),
                    }
            self._idn_prev_w = {c: None for c in _CANDS}
            self._idn_prev_px = None
            self._idn_last_date = None
            self._idn_n = 0
            self._idn_act_watch = 0
            self._idn_act_stress = 0
            self._idn_days_capped = 0
            self._idn_update_ok = False
            self._idn_err_logged = False
            self._idn_log_bytes = 0
            self._idn_cash = set(_CASH_TK)
            for attr in ("sym_cash", "sym_crash"):
                s = getattr(self, attr, None)
                if s is not None:
                    self._idn_cash.add(_tk(s))
        except Exception as e:
            try:
                self.log(f"[INIT] CG_IDS_CAP_ERROR,stage=init,type={type(e).__name__}")
            except Exception:
                pass

    def _IdnGross(self, w):
        g = 0.0
        for t, wt in (w or {}).items():
            if t in self._idn_cash:
                continue
            try:
                g += abs(float(wt or 0.0))
            except Exception:
                pass
        return g

    def _IdnScale(self, base_w, ids, cand):
        """Return scaled weights dict (ticker->float). Never mutates base_w."""
        if cand == "BASE" or _SPEC[cand] is None:
            return dict(base_w)
        mode, w_val, s_val = _SPEC[cand]
        val = w_val if ids == "WATCH" else s_val
        g = self._IdnGross(base_w)
        if mode == "cap":
            scale = 1.0 if g <= 1e-12 else min(1.0, val / g)
        else:
            scale = float(val)
        if scale >= 1.0 - 1e-12:
            return dict(base_w)
        out = {}
        for t, wt in base_w.items():
            try:
                wf = float(wt or 0.0)
            except Exception:
                continue
            if t in self._idn_cash:
                out[t] = wf
            else:
                out[t] = wf * scale
        return out

    def _IdnPx(self, combined):
        px = {}
        for s in (combined or {}):
            t = _tk(s)
            try:
                p = float(self.securities[s].price)
            except Exception:
                p = 0.0
            if p > 0:
                px[t] = p
        return px

    def _IdnBaseW(self, combined):
        w = {}
        for s, wt in (combined or {}).items():
            try:
                w[_tk(s)] = float(wt or 0.0)
            except Exception:
                continue
        return w

    def _IdnShadowRet(self, weights, prev_px, curr_px):
        r = 0.0
        for t, wt in (weights or {}).items():
            if t in self._idn_cash:
                continue
            p0 = prev_px.get(t) if prev_px else None
            p1 = curr_px.get(t) if curr_px else None
            if not p0 or not p1 or p0 <= 0:
                continue
            try:
                r += float(wt or 0.0) * (p1 / p0 - 1.0)
            except Exception:
                pass
        return r

    def _IdnUpdState(self, cand, sk, r, gb, ga):
        st = self._idn_state[(cand, sk)]
        st["n"] += 1
        st["sum_r"] += r
        st["nav"] = max(0.01, st["nav"] * (1.0 + r))
        if st["nav"] > st["peak"]:
            st["peak"] = st["nav"]
        dd = 1.0 - st["nav"] / max(st["peak"], 1e-9)
        if dd > st["maxdd"]:
            st["maxdd"] = dd
        st["sum_gb"] += gb
        st["sum_ga"] += ga
        st["rets"].append(r)

    def CgIdsNormalCapUpdate(self, combined) -> None:
        if not getattr(self, "cg_ids_normal_cap_diag_enable", False):
            return
        try:
            today = self.time.date()
            if self._idn_last_date == today:
                return
            # read-only snapshot; never mutate combined
            base_w = self._IdnBaseW(combined)
            curr_px = self._IdnPx(combined)
            ps = str(getattr(self, "_panic_state", "NORMAL") or "NORMAL")
            ids = str(getattr(self, "_ids_state", "NORMAL") or "NORMAL")
            active = (ps == "NORMAL" and ids in ("WATCH", "STRESS"))
            gb = self._IdnGross(base_w)
            # apply yesterday's shadow weights to today's move (no look-ahead)
            if self._idn_prev_px is not None:
                for c in _CANDS:
                    pw = self._idn_prev_w.get(c)
                    if pw is None:
                        continue
                    r = self._IdnShadowRet(pw, self._idn_prev_px, curr_px)
                    ga = self._IdnGross(pw)
                    _upd(self._idn_run[c], r, ga)
                    for name, s, e in _WINDOWS:
                        ee = e if e is not None else today
                        # attribute return to the day the weight was held (prev date)
                        pd = self._idn_last_date
                        if pd is not None and s <= pd <= ee:
                            _upd(self._idn_win[(c, name)], r, ga)
                    # state attribution uses activation state stored with prev weights
                    psk = getattr(self, "_idn_prev_sk", None)
                    if psk:
                        pgb = float(getattr(self, "_idn_prev_gb", gb) or 0.0)
                        self._IdnUpdState(c, psk, r, pgb, ga)
                self._idn_n += 1
            # build today's candidate weights for next-day return
            sk = None
            if active:
                sk = "NORMAL_IDS_WATCH" if ids == "WATCH" else "NORMAL_IDS_STRESS"
                if ids == "WATCH":
                    self._idn_act_watch += 1
                else:
                    self._idn_act_stress += 1
            any_scaled = False
            for c in _CANDS:
                if active:
                    cw = self._IdnScale(base_w, ids, c)
                else:
                    cw = dict(base_w)
                if active and c != "BASE" and abs(self._IdnGross(cw) - gb) > 1e-9:
                    any_scaled = True
                self._idn_prev_w[c] = cw
            if any_scaled:
                self._idn_days_capped += 1
            self._idn_prev_px = curr_px
            self._idn_prev_sk = sk
            self._idn_prev_gb = gb
            self._idn_last_date = today
            if not self._idn_update_ok:
                self._idn_update_ok = True
                self.log(
                    f"[INIT] CG_IDS_CAP_UPDATE_OK,date={today},"
                    f"base_gross={_f(gb)},"
                    f"watch={int(ps=='NORMAL' and ids=='WATCH')},"
                    f"stress={int(ps=='NORMAL' and ids=='STRESS')}")
        except Exception as e:
            if not getattr(self, "_idn_err_logged", False):
                self._idn_err_logged = True
                try:
                    self.log(f"[INIT] CG_IDS_CAP_ERROR,stage=update,type={type(e).__name__}")
                except Exception:
                    pass

    def _IdnEmit(self, lines, line):
        b = len(line.encode("utf-8"))
        if b > _LINE_MAX:
            line = line[:_LINE_MAX - 20] + "...TRUNC"
            b = len(line.encode("utf-8"))
        if self._idn_log_bytes + b > _LOG_BUDGET:
            return False
        lines.append(line)
        self._idn_log_bytes += b
        return True

    def _IdnFmtRun(self, c, st):
        n = st["n"]
        return (f"CG_IDS_CAP_FINAL,{c},days={n},nav={_f(st['nav'])},"
                f"cagr={_f(_ann(st['sum_r'], n))},maxdd={_f(st['maxdd'])},"
                f"worst5={_f(_w5(list(st['rets'])), 6)},"
                f"vol={_f(_vol(st['sum_r'], st['sum_r2'], n))},"
                f"sharpe={_f(_sharpe(st['sum_r'], st['sum_r2'], n))},"
                f"pos={_f(st['pos']/n if n else None, 3)},"
                f"avg_g={_f(st['sum_g']/n if n else None)}")

    def CgIdsNormalCapEmitFinal(self) -> None:
        if not getattr(self, "cg_ids_normal_cap_diag_enable", False):
            return
        self.log(f"[EOA] CG_IDS_CAP_EMIT_START,n={getattr(self,'_idn_n',0)}")
        lines = []
        self._idn_log_bytes = 0
        # RUN lines
        for c in _CANDS:
            st = self._idn_run[c]
            if st["n"] <= 0:
                self._IdnEmit(lines, f"CG_IDS_CAP_FINAL,{c},status=NO_DATA")
            else:
                self._IdnEmit(lines, self._IdnFmtRun(c, st))
        # windows (max 45): skip RUN (covered by CG_IDS_CAP_FINAL) → 5×9=45
        wcount = 0
        for c in _CANDS:
            for name, _, _ in _WINDOWS:
                if name == "RUN":
                    continue
                if wcount >= 45:
                    break
                st = self._idn_win[(c, name)]
                if st["n"] <= 0:
                    ln = f"CG_IDS_CAP_WINDOW_FINAL,{c},{name},status=NO_DATA"
                else:
                    n = st["n"]
                    ln = (f"CG_IDS_CAP_WINDOW_FINAL,{c},{name},days={n},"
                          f"nav={_f(st['nav'])},cagr={_f(_ann(st['sum_r'], n))},"
                          f"maxdd={_f(st['maxdd'])},"
                          f"worst5={_f(_w5(list(st['rets'])), 6)},"
                          f"vol={_f(_vol(st['sum_r'], st['sum_r2'], n))},"
                          f"sharpe={_f(_sharpe(st['sum_r'], st['sum_r2'], n))},"
                          f"avg_g={_f(st['sum_g']/n if n else None)}")
                if not self._IdnEmit(lines, ln):
                    break
                wcount += 1
        # state lines (max 10)
        scount = 0
        for c in _CANDS:
            for sk in _STATES:
                if scount >= 10:
                    break
                st = self._idn_state[(c, sk)]
                if st["n"] <= 0:
                    ln = f"CG_IDS_CAP_STATE_FINAL,{c},{sk},status=NO_DATA"
                else:
                    n = st["n"]
                    ln = (f"CG_IDS_CAP_STATE_FINAL,{c},{sk},days={n},"
                          f"nav={_f(st['nav'])},"
                          f"mean={_f(st['sum_r']/n, 6)},"
                          f"maxdd={_f(st['maxdd'])},"
                          f"worst5={_f(_w5(list(st['rets'])), 6)},"
                          f"avg_g_before={_f(st['sum_gb']/n)},"
                          f"avg_g_after={_f(st['sum_ga']/n)}")
                if not self._IdnEmit(lines, ln):
                    break
                scount += 1
        # selection
        base = self._idn_run["BASE"]
        b_nav = base["nav"]
        b_dd = base["maxdd"]
        b_w5 = _w5(list(base["rets"]))
        b_oos = self._idn_win[("BASE", "OOS")]
        b_oos_sh = _sharpe(b_oos["sum_r"], b_oos["sum_r2"], b_oos["n"])
        b_y20 = self._idn_win[("BASE", "Y2020")]["maxdd"]
        b_y22 = self._idn_win[("BASE", "Y2022")]["maxdd"]
        eligible = []
        for c in ("C1", "C2", "C3", "C4"):
            st = self._idn_run[c]
            if st["n"] <= 0 or base["n"] <= 0:
                continue
            c_w5 = _w5(list(st["rets"]))
            c_vol = _vol(st["sum_r"], st["sum_r2"], st["n"])
            c_oos = self._idn_win[(c, "OOS")]
            c_oos_sh = _sharpe(c_oos["sum_r"], c_oos["sum_r2"], c_oos["n"])
            c_y20 = self._idn_win[(c, "Y2020")]["maxdd"]
            c_y22 = self._idn_win[(c, "Y2022")]["maxdd"]
            ok = True
            reasons = []
            if st["nav"] < b_nav * 0.98:
                ok = False; reasons.append("nav")
            if st["maxdd"] > b_dd + 1e-12:
                ok = False; reasons.append("maxdd")
            if b_w5 is not None and c_w5 is not None and c_w5 < b_w5 - 1e-12:
                ok = False; reasons.append("worst5")
            if b_oos_sh is not None and c_oos_sh is not None and c_oos_sh < b_oos_sh * 0.90:
                ok = False; reasons.append("oos_sh")
            if c_y20 > b_y20 + 1e-12:
                ok = False; reasons.append("y2020_dd")
            if c_y22 > b_y22 + 1e-12:
                ok = False; reasons.append("y2022_dd")
            if c_vol is not None and c_vol > 0.18:
                ok = False; reasons.append("std")
            if ok:
                eligible.append((c, st["maxdd"], c_w5 if c_w5 is not None else -1e9,
                                 -st["nav"], c))
            else:
                # keep failed reasons for SELECT line context if none eligible
                pass
        pick = "NONE"
        why = "none_eligible"
        if eligible:
            # lowest MaxDD, then best worst5, then highest NAV, then simplest (C order)
            eligible.sort(key=lambda x: (x[1], -x[2], x[3], x[4]))
            pick = eligible[0][0]
            why = "maxdd|worst5|nav|simple"
        self._IdnEmit(
            lines,
            f"CG_IDS_CAP_SELECT_FINAL,pick={pick},why={why},"
            f"eligible={','.join(e[0] for e in eligible) or 'NONE'},"
            f"act_watch={self._idn_act_watch},act_stress={self._idn_act_stress},"
            f"days_capped={self._idn_days_capped},trade=0")
        for ln in lines:
            self.log(ln)
        self.log(f"[EOA] CG_IDS_CAP_EMIT_DONE,lines={len(lines)},bytes={self._idn_log_bytes}")
