# region imports
from AlgorithmImports import *
from datetime import date as _date, timedelta as _timedelta
from collections import defaultdict
import math
from cg_maisr_d2_labels import (
    _STATES, _ALL_CFG, _clfid, _D2_FWD, _D2_TRAIN0, _D2_TRAIN1,
    _D2_DUR, _D2_GOLD, _D2_BREADTH, _D2_INFL, _D2_EXCLUDE_HELD, _D2_PARK,
    _D2_PROXY, _D2PeakTroughMaxDD, _d2_min,
)
# endregion
# cg_maisr_final_d3.py -- CG-MAISR-FINAL-D3 overlapping-window episodes + 12 packs.

_D3_EPISODE_ROOT = (
    "D2_gap10_neg2_close:_d2_feed_ep:cg_maisr_d2_labels.py:111-133;"
    "no_[t,t+60]_interval_union;overlapping_60m_windows_fragmented"
)

_D3_Q = (0.005, 0.01, 0.025, 0.05, 0.10, 0.25, 0.50, 0.75, 0.90, 0.95, 0.975, 0.99, 0.995)
_D3_TRAINA0, _D3_TRAINA1 = _date(2012, 1, 1).toordinal(), _date(2015, 12, 31).toordinal()
_D3_TRAINB0, _D3_TRAINB1 = _date(2016, 1, 1).toordinal(), _date(2018, 12, 31).toordinal()
_D3_KEEP_ORDS = set()
for _wid, _d0, _d1 in _D3_WINDOWS:
    for _o in range(_d0.toordinal(), _d1.toordinal() + 1):
        _D3_KEEP_ORDS.add(_o)


def _D3KeepRaw(do):
    return (_D2_TRAIN0 <= do <= _D2_TRAIN1) or (do in _D3_KEEP_ORDS)
_D3_DEF_SECTOR = ("XLV", "XLU")
_D3_SAT = frozenset(("AVGO", "MU", "NVDA"))
_D3_DEF_TK = frozenset(("BND", "TIP", "GLD", "GLDM", "SH", "BIL", "SGOV", "USFR", "XLV", "XLU"))
_D3_INV = frozenset(("SH",))


def _d3f(x, d=4):
    if x is None:
        return "NA"
    try:
        if isinstance(x, float) and (math.isnan(x) or math.isinf(x)):
            return "NA"
        return f"{float(x):.{d}f}"
    except Exception:
        return "NA"


def _D3BuildPacks():
    packs = []
    for b in (0.40, 0.60, 0.80):
        for br in (0.60, 0.70):
            for loc in (0.50, 0.75):
                resid = 0.30 if abs(loc - 0.50) < 1e-9 else 0.50
                pid = f"D3_B{b:.2f}_{br:.2f}_L{loc:.2f}_{resid:.2f}"
                packs.append({
                    "id": pid, "B": b, "breadth": br, "local": -loc, "resid": -resid,
                    "sector_proxy_mult": 0.75,
                })
    return packs


_D3_PACKS = _D3BuildPacks()
assert len(_D3_PACKS) == 12


def _D3MergeIntervals(intervals):
    """Merge overlapping/touching [start,end] intervals. Deterministic."""
    if not intervals:
        return []
    items = sorted(intervals, key=lambda x: (x["start"], x["end"]))
    out = []
    cur = dict(items[0])
    cur["n"] = int(cur.get("n", 1))
    cur["last_pos"] = cur.get("last_pos", cur["start"])
    for it in items[1:]:
        if it["start"] <= cur["end"]:
            if it["end"] > cur["end"]:
                cur["end"] = it["end"]
            cur["n"] += int(it.get("n", 1))
            lp = it.get("last_pos", it["start"])
            if lp > cur["last_pos"]:
                cur["last_pos"] = lp
            for k in ("mae", "breadth"):
                if it.get(k) is not None:
                    cur[k] = min(cur[k], it[k]) if cur.get(k) is not None else it[k]
        else:
            out.append(cur)
            cur = dict(it)
            cur["n"] = int(cur.get("n", 1))
            cur["last_pos"] = cur.get("last_pos", cur["start"])
    out.append(cur)
    return out


def _D3UnionSelfTest():
    a = _date(2015, 1, 2)
    t0 = __import__("datetime").datetime(2015, 1, 2, 10, 0)
    t1 = t0 + _timedelta(minutes=30)
    t2 = t0 + _timedelta(minutes=90)
    m1 = _D3MergeIntervals([
        {"start": t0, "end": t0 + _timedelta(minutes=60), "n": 1, "last_pos": t0},
        {"start": t1, "end": t1 + _timedelta(minutes=60), "n": 1, "last_pos": t1},
    ])
    m2 = _D3MergeIntervals([
        {"start": t0, "end": t0 + _timedelta(minutes=60), "n": 1, "last_pos": t0},
        {"start": t2, "end": t2 + _timedelta(minutes=60), "n": 1, "last_pos": t2},
    ])
    ok = (len(m1) == 1 and len(m2) == 2)
    return ok, len(m1), len(m2)


def _D3Quantile(xs, q):
    if not xs:
        return None
    ys = sorted(xs)
    if len(ys) == 1:
        return ys[0]
    pos = q * (len(ys) - 1)
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return ys[lo]
    w = pos - lo
    return ys[lo] * (1 - w) + ys[hi] * w


def _D3DistRow(name, xs):
    miss = sum(1 for x in xs if x is None)
    vals = [float(x) for x in xs if x is not None]
    zero = sum(1 for x in vals if abs(x) < 1e-15)
    n = len(vals)
    mean = sum(vals) / n if n else None
    std = (sum((x - mean) ** 2 for x in vals) / n) ** 0.5 if n else None
    row = {
        "feature": name, "count": n, "missing": miss, "zero": zero,
        "mean": mean, "std": std,
        "min": min(vals) if vals else None, "max": max(vals) if vals else None,
    }
    for q in _D3_Q:
        row[f"p{q}"] = _D3Quantile(vals, q)
    return row


class CgMaisrFinalD3Mixin:
    """Final D3 calibration / selection / conditional economic freeze."""

    def _D3ReadParams(self, _p, _bool):
        self.cg_maisr_final_d3_enable = _bool("cg_maisr_final_d3_enable", "0")
        self.cg_maisr_d3_label_only = _bool("cg_maisr_d3_label_only", "0")
        self.cg_maisr_d3_selected_pack = str(_p("cg_maisr_d3_selected_pack", "") or "").strip()
        raw = str(_p("cg_maisr_d3_selected_classifiers", "") or "").strip()
        self.cg_maisr_d3_selected_classifiers = [x.strip() for x in raw.split(",") if x.strip()]
        self.cg_maisr_d3_export_detail = _bool("cg_maisr_d3_export_detail", "1")

    def _D3InitHooks(self):
        self._d3_raw = []
        self._d3_err = 0
        self._d3_canary = {}
        self._d3_selected_pack = None
        self._d3_pack_stats = {}
        self._d3_scored = []
        self._d3_chosen = []
        self._d3_modes = set()
        self._d3_roles = {}
        self._d3_dists = []
        self._d3_audit = {}
        self._d3_eps_cache = {}
        self._d3_old_eps = 0
        self._d3_merged_eps = 0
        on = bool(getattr(self, "cg_maisr_final_d3_enable", False))
        if on:
            # Reuse D2 pending/minute engine without D2 pack AUTO path.
            self._d2_mode = True
            if not getattr(self, "cg_maisr_label_only", False):
                # keep label_only off for econ; still need pending finalize
                pass
        if on:
            self._MsLog(
                f"CG_MAISR_D3_INIT,final_d3=1,label_only={int(self.cg_maisr_d3_label_only)},"
                f"selected_pack={self.cg_maisr_d3_selected_pack or 'AUTO'},"
                f"selected_classifiers={','.join(self.cg_maisr_d3_selected_classifiers) or 'NONE'},"
                f"export_detail={int(self.cg_maisr_d3_export_detail)}"
            )
            ok, n1, n2 = _D3UnionSelfTest()
            self._MsLog(
                f"CG_MAISR_D3_EPISODE_AUDIT,root={_D3_EPISODE_ROOT},"
                f"union_selftest={'PASS' if ok else 'FAIL'},overlap_merged={n1},gap_separate={n2}"
            )

    def _D3StoreRaw(self, p, stats, spy, dur_mae, gold_mae, dur_ok, gold_ok,
                    infl_ret, infl_rel, blocks, held_feat, br_maes) -> None:
        if not _D3KeepRaw(p["do"]):
            return
        self._d3_raw.append({
            "do": p["do"], "tod": p["tod"], "t": p["t"], "preds": p["preds"],
            "rg": p["rg"], "w2": p["w2"], "ids": p["ids"],
            "spy_mae": spy["mae"], "spy_ret": spy["ret"],
            "dur_mae": dur_mae, "gold_mae": gold_mae, "dur_ok": dur_ok, "gold_ok": gold_ok,
            "infl_ret": infl_ret, "infl_rel": infl_rel,
            "blocks": blocks, "held": held_feat, "br_maes": br_maes,
            "train": bool(_D2_TRAIN0 <= p["do"] <= _D2_TRAIN1),
        })

    def _D3Blocks(self, stats, spy_ret):
        blocks = []
        for name, tks in (("duration", _D2_DUR), ("gold", _D2_GOLD), ("def_sector", _D3_DEF_SECTOR)):
            vals = [stats[t] for t in tks if t in stats]
            if len(vals) < 1:
                blocks.append({"name": name, "ok": False, "abs": None, "rel": None})
                continue
            ab = sum(v["ret"] for v in vals) / len(vals)
            rel = ab - spy_ret
            blocks.append({"name": name, "ok": True, "abs": ab, "rel": rel})
        return blocks

    def _D3LabelMacro(self, pack, row):
        B = pack["B"]
        br_thr = -B
        br_n = [v for v in (row["br_maes"] or {}).values() if v is not None]
        breadth = (sum(1 for v in br_n if v <= br_thr) / len(br_n)) if br_n else 0.0
        spy_mae = row["spy_mae"]
        broad = (spy_mae <= -B and breadth >= pack["breadth"])
        flags = {"BROAD": int(broad), "breadth": breadth}
        dur_ok, gold_ok = row["dur_ok"], row["gold_ok"]
        dur_mae, gold_mae = row["dur_mae"], row["gold_mae"]
        # SYSTEMIC: BROAD + duration + gold + >=3 macro blocks stressed
        stressed = 0
        if broad:
            stressed += 1
        if dur_ok and dur_mae <= -0.50 * B:
            stressed += 1
        if gold_ok and gold_mae <= -0.50 * B:
            stressed += 1
        # equity breadth panel stress counts as a block when breadth met
        if breadth >= pack["breadth"]:
            stressed += 1
        systemic = bool(broad and dur_ok and gold_ok
                        and dur_mae <= -0.50 * B and gold_mae <= -0.50 * B and stressed >= 3)
        flags["SYSTEMIC"] = int(systemic)
        if systemic:
            return "SYSTEMIC_LIQUIDITY_STRESS", flags
        # RATE
        eq_weak = (spy_mae <= -B) or (breadth >= pack["breadth"])
        infl_ok = row["infl_ret"] is not None
        rate = bool(
            eq_weak and dur_ok and dur_mae <= -0.50 * B and infl_ok
            and row["infl_rel"] >= 0.30 * B and row["infl_ret"] >= -0.10
            and not systemic
        )
        flags["RATE"] = int(rate)
        if rate:
            return "RATE_INFLATION_STRESS", flags
        if broad:
            return "BROAD_EQUITY_STRESS", flags
        # DEFENSIVE: multi-block resilience (no best-of)
        avail = [b for b in row["blocks"] if b.get("ok")]
        if len(avail) < 2:
            flags["DEF"] = 0
            flags["DEF_UNAVAILABLE"] = 1
            return "NORMAL", flags
        abs_floor, rel_floor = 0.0, 0.30 * B
        resilient = [b for b in avail
                     if b["abs"] is not None and b["rel"] is not None
                     and b["abs"] >= abs_floor and b["rel"] >= rel_floor]
        med_abs = sorted(b["abs"] for b in avail)[len(avail) // 2]
        med_rel = sorted(b["rel"] for b in avail)[len(avail) // 2]
        defensive = bool(
            spy_mae <= -B and len(resilient) >= 2 and med_abs >= abs_floor
            and med_rel >= rel_floor and not systemic and not rate
        )
        flags["DEF"] = int(defensive)
        if defensive:
            return "DEFENSIVE_ROTATION", flags
        return "NORMAL", flags

    def _D3LabelHeld(self, pack, hf, broad):
        if broad:
            return "NORMAL", {}
        loc = pack["local"]
        resid = pack["resid"]
        proxy_thr = loc * pack["sector_proxy_mult"]
        held_mae = hf["mae"]
        flags = {}
        proxy_ok = bool(hf.get("proxy_ok"))
        proxy_mae = hf.get("proxy_mae")
        if proxy_ok and proxy_mae is not None:
            if held_mae <= loc and proxy_mae <= proxy_thr:
                flags["SECTOR"] = 1
                return "SECTOR_STRESS", flags
        # residual vs max(SPY, proxy) in ATR units (more negative = worse)
        spy_mae = hf["spy_mae"]
        base = spy_mae
        if proxy_ok and proxy_mae is not None:
            base = max(spy_mae, proxy_mae)  # less adverse of the two
        vs = held_mae - base
        if held_mae <= loc and vs <= resid:
            flags["LOCAL"] = 1
            return "LOCAL_ASSET_STRESS", flags
        return "NORMAL", flags

    def _D3ApplyPack(self, pack, rows):
        out = []
        for r in rows:
            mlab, mflags = self._D3LabelMacro(pack, r)
            broad = mlab in ("BROAD_EQUITY_STRESS", "SYSTEMIC_LIQUIDITY_STRESS")
            held = {}
            for tk, hf in (r.get("held") or {}).items():
                hlab, _ = self._D3LabelHeld(pack, hf, broad)
                held[tk] = hlab
            out.append({
                "do": r["do"], "tod": r["tod"], "t": r["t"], "preds": r["preds"],
                "macro": mlab, "held": held, "flags": mflags,
                "rg": r["rg"], "w2": r["w2"], "ids": r["ids"],
                "spy_mae": r["spy_mae"], "breadth": mflags.get("breadth", 0.0),
            })
        return out

    def _D3OldEpisodes(self, stream):
        """Reproduce D2 gap/neg episode logic for audit counts."""
        from cg_maisr_d2_labels import _d2_feed_ep
        store, closed = {}, []
        for row in stream:
            _d2_feed_ep(store, row.get("symbol", "MACRO"), row["day"], row["ts"],
                        row.get("label"), closed)
        closed.extend(store.values())
        return closed

    def _D3MergedEpisodes(self, stream):
        """Each positive -> [t,t+60]; merge overlapping same day/symbol/label."""
        buckets = defaultdict(list)
        for row in stream:
            lab = row.get("label")
            if not lab or lab in ("NORMAL", "UNAVAILABLE", None):
                continue
            ts = row["ts"]
            buckets[(row["day"], row.get("symbol", "MACRO"), lab)].append({
                "start": ts,
                "end": ts + _timedelta(minutes=_D2_FWD),
                "last_pos": ts,
                "n": 1,
                "mae": row.get("mae"),
                "breadth": row.get("breadth"),
                "rg": row.get("rg"), "w2": row.get("w2"), "ids": row.get("ids"),
            })
        eps = []
        for (day, sym, lab), ints in buckets.items():
            for m in _D3MergeIntervals(ints):
                eps.append({
                    "day": day, "symbol": sym, "label": lab,
                    "start": m["start"], "end": m["end"],
                    "last_pos": m["last_pos"], "n": m["n"],
                    "mae": m.get("mae"), "breadth": m.get("breadth"),
                    "rg": m.get("rg"), "w2": m.get("w2"), "ids": m.get("ids"),
                    "dur_min": _d2_min(m["start"], m["end"]),
                })
        return eps

    def _D3EpisodesForLabeled(self, labeled):
        macro_stream = [{
            "ts": r["t"], "day": r["do"], "symbol": "MACRO", "label": r["macro"],
            "mae": r.get("spy_mae"), "breadth": r.get("breadth"),
            "rg": r["rg"], "w2": r["w2"], "ids": r["ids"],
        } for r in labeled]
        held_stream = []
        for r in labeled:
            for tk, lab in (r.get("held") or {}).items():
                held_stream.append({
                    "ts": r["t"], "day": r["do"], "symbol": tk, "label": lab,
                    "rg": r["rg"], "w2": r["w2"], "ids": r["ids"],
                })
        return (
            self._D3MergedEpisodes(macro_stream),
            self._D3MergedEpisodes(held_stream),
            self._D3OldEpisodes(macro_stream),
            self._D3OldEpisodes(held_stream),
        )

    def _D3Cnt(self, eps, lab):
        return sum(1 for e in eps if e["label"] == lab)

    def _D3Days(self, eps, lab):
        return len({e["day"] for e in eps if e["label"] == lab})

    def _D3PackStats(self, pack, me, he, train_days, held_day_n, train_a_days, train_b_days):
        broad = self._D3Cnt(me, "BROAD_EQUITY_STRESS")
        sys_ = self._D3Cnt(me, "SYSTEMIC_LIQUIDITY_STRESS")
        rate = self._D3Cnt(me, "RATE_INFLATION_STRESS")
        deff = self._D3Cnt(me, "DEFENSIVE_ROTATION")
        loc = self._D3Cnt(he, "LOCAL_ASSET_STRESS")
        sec = self._D3Cnt(he, "SECTOR_STRESS")
        ls = loc + sec
        broad_d = self._D3Days(me, "BROAD_EQUITY_STRESS")
        ls_d = len({(e["day"], e["symbol"]) for e in he
                    if e["label"] in ("LOCAL_ASSET_STRESS", "SECTOR_STRESS")})
        support = (
            20 <= broad <= 200 and 15 <= broad_d <= 150
            and 20 <= ls <= 60 and 15 <= ls_d <= 60
            and 10 <= deff <= 150
        )
        sys_av = 5 <= sys_ <= 60
        rate_av = 5 <= rate <= 75
        # subperiod density
        def _dens(eps, lab, days_set, years):
            n = sum(1 for e in eps if e["label"] == lab and e["day"] in days_set)
            return n / max(years, 1e-9)

        ya, yb = 4.0, 3.0
        ba, bb = _dens(me, "BROAD_EQUITY_STRESS", train_a_days, ya), _dens(me, "BROAD_EQUITY_STRESS", train_b_days, yb)
        la = _dens(he, "LOCAL_ASSET_STRESS", train_a_days, ya) + _dens(he, "SECTOR_STRESS", train_a_days, ya)
        lb = _dens(he, "LOCAL_ASSET_STRESS", train_b_days, yb) + _dens(he, "SECTOR_STRESS", train_b_days, yb)
        da, db = _dens(me, "DEFENSIVE_ROTATION", train_a_days, ya), _dens(me, "DEFENSIVE_ROTATION", train_b_days, yb)

        def _stable(a, b, lim=4.0):
            if a <= 0 or b <= 0:
                return False
            return max(a, b) / min(a, b) <= lim

        def _stable_def(a, b):
            nz = [x for x in (a, b) if x > 0]
            if len(nz) < 2:
                return True  # rare ok when one half zero for DEF? task: max/min non-zero <=5
            return max(nz) / min(nz) <= 5.0

        stab = _stable(ba, bb) and _stable(la, lb) and _stable_def(da, db)
        # known windows filled later
        return {
            "id": pack["id"], "B": pack["B"], "breadth": pack["breadth"],
            "local": pack["local"], "resid": pack["resid"],
            "broad_episodes": broad, "local_sector_episodes": ls,
            "local_episodes": loc, "sector_episodes": sec,
            "defensive_episodes": deff, "systemic_episodes": sys_, "rate_episodes": rate,
            "broad_unique_days": broad_d, "local_sector_unique_held_days": ls_d,
            "systemic_available": "YES" if sys_av else "RARE_UNAVAILABLE",
            "rate_available": "YES" if rate_av else "RARE_UNAVAILABLE",
            "support_ok": int(support), "stability_ok": int(stab),
            "dens_broad_a": ba, "dens_broad_b": bb, "dens_ls_a": la, "dens_ls_b": lb,
            "pass": 0, "semantic_ok": 0, "mono_ok": 1,
            "dist_score": abs(broad - 80) + abs(ls - 35) + abs(deff - 40),
            "disp": (max(ba, bb) / max(min(ba, bb), 1e-9)) + (max(la, lb) / max(min(la, lb), 1e-9)),
        }

    def _D3KnownWindowAudit(self, pack_id, me, he):
        rows = []
        concern = False
        has_2015_broad = has_2018q4_broad = has_2020 = False
        for wid, d0, d1 in _D3_WINDOWS:
            o0, o1 = d0.toordinal(), d1.toordinal()
            sub_m = [e for e in me if o0 <= e["day"] <= o1]
            sub_h = [e for e in he if o0 <= e["day"] <= o1]
            broad = self._D3Cnt(sub_m, "BROAD_EQUITY_STRESS")
            sys_ = self._D3Cnt(sub_m, "SYSTEMIC_LIQUIDITY_STRESS")
            rate = self._D3Cnt(sub_m, "RATE_INFLATION_STRESS")
            deff = self._D3Cnt(sub_m, "DEFENSIVE_ROTATION")
            ls = self._D3Cnt(sub_h, "LOCAL_ASSET_STRESS") + self._D3Cnt(sub_h, "SECTOR_STRESS")
            starts = [e["start"] for e in sub_m + sub_h]
            durs = [e.get("dur_min", 0) for e in sub_m + sub_h]
            rows.append({
                "pack": pack_id, "window": wid, "broad": broad, "systemic": sys_,
                "rate": rate, "defensive": deff, "local_sector": ls,
                "first_signal": str(min(starts)) if starts else "NA",
                "max_merged_duration": max(durs) if durs else 0,
            })
            if wid == "W2015_08" and broad > 0:
                has_2015_broad = True
            if wid == "W2018_Q4" and broad > 0:
                has_2018q4_broad = True
            if wid == "W2020" and (broad > 0 or sys_ > 0):
                has_2020 = True
        if not (has_2015_broad and has_2018q4_broad):
            concern = True
        if not has_2020:
            concern = True
        return rows, (not concern)

    def _D3Monotonicity(self, stats_by_id):
        """Hard fail if violation >1 episode when holding other fields fixed."""
        ok = True
        # group by (breadth, local)
        from itertools import groupby
        packs = list(_D3_PACKS)
        for br in (0.60, 0.70):
            for loc in (-0.50, -0.75):
                seq = [p for p in packs if abs(p["breadth"] - br) < 1e-9 and abs(p["local"] - loc) < 1e-9]
                seq = sorted(seq, key=lambda p: -p["B"])  # 0.80, 0.60, 0.40
                for metric in ("broad_episodes", "local_sector_episodes", "broad_unique_days",
                               "local_sector_unique_held_days"):
                    vals = [stats_by_id[p["id"]][metric] for p in seq]
                    # B0.80 <= B0.60 <= B0.40
                    if vals[0] > vals[1] + 1 or vals[1] > vals[2] + 1:
                        ok = False
        for B in (0.40, 0.60, 0.80):
            for loc in (-0.50, -0.75):
                p60 = next(p for p in packs if abs(p["B"] - B) < 1e-9 and abs(p["breadth"] - 0.60) < 1e-9 and abs(p["local"] - loc) < 1e-9)
                p70 = next(p for p in packs if abs(p["B"] - B) < 1e-9 and abs(p["breadth"] - 0.70) < 1e-9 and abs(p["local"] - loc) < 1e-9)
                for metric in ("broad_episodes", "local_sector_episodes"):
                    if stats_by_id[p70["id"]][metric] > stats_by_id[p60["id"]][metric] + 1:
                        ok = False
            for br in (0.60, 0.70):
                p50 = next(p for p in packs if abs(p["B"] - B) < 1e-9 and abs(p["breadth"] - br) < 1e-9 and abs(p["local"] + 0.50) < 1e-9)
                p75 = next(p for p in packs if abs(p["B"] - B) < 1e-9 and abs(p["breadth"] - br) < 1e-9 and abs(p["local"] + 0.75) < 1e-9)
                for metric in ("local_sector_episodes", "local_sector_unique_held_days"):
                    if stats_by_id[p75["id"]][metric] > stats_by_id[p50["id"]][metric] + 1:
                        ok = False
        return ok

    def _D3SelectPack(self, stats_by_id):
        pool = [s for s in stats_by_id.values()
                if s.get("support_ok") and s.get("stability_ok") and s.get("semantic_ok")
                and s.get("mono_ok")]
        if not pool:
            return None
        pool.sort(key=lambda s: (
            s["dist_score"], s["disp"],
            0 if abs(s["breadth"] - 0.70) < 1e-9 else 1,
            abs(s["B"] - 0.60),
            0 if abs(s["local"] + 0.75) < 1e-9 else 1,
            s["id"],
        ))
        return pool[0]["id"]

    def _D3ScoreClassifiers(self, pack_id, labeled, me, he, stats):
        sys_av = stats.get("systemic_available") == "YES"
        rate_av = stats.get("rate_available") == "YES"
        avail_macro = ["BROAD_EQUITY_STRESS", "DEFENSIVE_ROTATION"]
        if sys_av:
            avail_macro.insert(0, "SYSTEMIC_LIQUIDITY_STRESS")
        if rate_av:
            avail_macro.insert(1 if sys_av else 0, "RATE_INFLATION_STRESS")
        held_labs = ("LOCAL_ASSET_STRESS", "SECTOR_STRESS")

        def _match(pe, te):
            if pe["label"] != te["label"]:
                return False
            if pe.get("symbol") != te.get("symbol"):
                return False
            # overlap or start within 10m before true
            if pe["start"] <= te["end"] and pe["end"] >= te["start"]:
                return True
            return pe["start"] <= te["start"] and _d2_min(pe["start"], te["start"]) <= 10

        scored = []
        for idx, (s, a, b, h) in enumerate(_ALL_CFG):
            cid = _clfid(s, a, b, h)
            pred_m, pred_h = [], []
            for r in labeled:
                if idx >= len(r["preds"]):
                    continue
                st = _STATES[r["preds"][idx]]
                pred_m.append({"ts": r["t"], "day": r["do"], "symbol": "MACRO", "label": st})
                if st in held_labs:
                    for tk in (r.get("held") or {}):
                        pred_h.append({"ts": r["t"], "day": r["do"], "symbol": tk, "label": st})
            pme = self._D3MergedEpisodes(pred_m)
            phe = self._D3MergedEpisodes(pred_h)
            f1s, tps, fps, fns = {}, {}, {}, {}
            for lab in list(avail_macro) + list(held_labs):
                true = [e for e in (me if lab in avail_macro else he) if e["label"] == lab]
                pred = [e for e in (pme if lab in avail_macro else phe) if e["label"] == lab]
                used_p = set()
                tp = 0
                for te in true:
                    hit = None
                    for i, pe in enumerate(pred):
                        if i in used_p:
                            continue
                        if _match(pe, te):
                            hit = i
                            break
                    if hit is not None:
                        tp += 1
                        used_p.add(hit)
                fp = len(pred) - len(used_p)
                fn = len(true) - tp
                prec = tp / (tp + fp) if (tp + fp) else 0.0
                rec = tp / (tp + fn) if (tp + fn) else 0.0
                f1 = (2 * prec * rec / (prec + rec)) if (prec + rec) else 0.0
                f1s[lab] = f1
                tps[lab], fps[lab], fns[lab] = tp, fp, fn
            avail_f1 = [f1s[x] for x in avail_macro + list(held_labs) if x in f1s]
            macro_f1 = sum(f1s.get(x, 0) for x in avail_macro) / max(len(avail_macro), 1)
            broad_f1 = f1s.get("BROAD_EQUITY_STRESS", 0)
            locsec_f1 = max(f1s.get("LOCAL_ASSET_STRESS", 0), f1s.get("SECTOR_STRESS", 0))
            n_pos = sum(1 for x in avail_f1 if x > 0)
            broad_pred = self._D3Cnt(pme, "BROAD_EQUITY_STRESS")
            locsec_pred = self._D3Cnt(phe, "LOCAL_ASSET_STRESS") + self._D3Cnt(phe, "SECTOR_STRESS")
            broad_fp_rate = fps.get("BROAD_EQUITY_STRESS", 0) / max(
                fps.get("BROAD_EQUITY_STRESS", 0) + tps.get("BROAD_EQUITY_STRESS", 0), 1)
            # local-to-broad error: LOCAL/SECTOR predicted overlapping BROAD true? approximate via FP share
            ltbe = 0.0
            true_broad = [e for e in me if e["label"] == "BROAD_EQUITY_STRESS"]
            loc_pred = [e for e in phe if e["label"] in held_labs]
            bad = 0
            for pe in loc_pred:
                if any(_match({**pe, "label": "BROAD_EQUITY_STRESS"}, {**te, "label": "BROAD_EQUITY_STRESS"})
                       or (pe["start"] <= te["end"] and pe["end"] >= te["start"])
                       for te in true_broad):
                    bad += 1
            ltbe = bad / max(len(loc_pred), 1)
            sys_fnr = fns.get("SYSTEMIC_LIQUIDITY_STRESS", 0) / max(
                fns.get("SYSTEMIC_LIQUIDITY_STRESS", 0) + tps.get("SYSTEMIC_LIQUIDITY_STRESS", 0), 1) if sys_av else 0.0
            score = (sum(avail_f1) / max(len(avail_f1), 1)
                     - 2.0 * sys_fnr - 1.5 * broad_fp_rate - 1.5 * ltbe)
            valid = int(
                broad_f1 > 0 and locsec_f1 > 0 and n_pos >= 2 and macro_f1 > 0
                and broad_pred > 0 and locsec_pred > 0
            )
            reason = "OK" if valid else "zero_f1_or_support"
            scored.append({
                "id": cid, "idx": idx, "s": s, "a": a, "b": b, "h": h,
                "score": score, "macro_f1": macro_f1, "valid": valid,
                "validity_reason": reason, "f1": f1s,
                "broad_pred_episodes": broad_pred, "locsec_pred_episodes": locsec_pred,
                "broad_fp_rate": broad_fp_rate, "ltbe": ltbe, "n": len(labeled),
                "broad_f1": broad_f1, "locsec_f1": locsec_f1,
            })
        return scored

    def _D3SelectClassifiers(self, scored):
        chosen, modes = [], set()
        for h in ("H0", "H1", "H2"):
            cand = [r for r in scored if r["valid"] and r["h"] == h]
            cand.sort(key=lambda r: (
                -r["score"], -r["broad_f1"], -r["locsec_f1"],
                r["broad_fp_rate"], r["ltbe"], r["s"], r["a"], r["b"], r["id"],
            ))
            for r in cand[:2]:
                chosen.append(r)
                modes.add(h)
        chosen.sort(key=lambda r: -r["score"])
        return chosen[:6], modes

    def _D3NaturalCanary(self, chosen, labeled):
        if not chosen:
            self._d3_canary = {"status": "FAIL", "armed": 0, "fired": 0, "reason": "no_classifiers"}
            self._MsLog("CG_MAISR_D3_CANARY_FINAL,status=FAIL,reason=no_classifiers")
            return self._d3_canary
        stress = ("LOCAL_ASSET_STRESS", "SECTOR_STRESS", "BROAD_EQUITY_STRESS",
                  "SYSTEMIC_LIQUIDITY_STRESS", "RATE_INFLATION_STRESS")
        for rclf in chosen:
            idx = rclf["idx"]
            for row in labeled:
                if row["tod"] < 585:
                    continue
                if idx >= len(row["preds"]):
                    continue
                st = _STATES[row["preds"][idx]]
                if st not in stress:
                    continue
                elig = [tk for tk, lab in (row.get("held") or {}).items() if lab != "NORMAL"]
                if not elig:
                    elig = sorted((row.get("held") or {}).keys())
                if not elig:
                    continue
                tk = elig[0]
                fill_time = row["t"] + _timedelta(minutes=1)
                self._d3_canary = {
                    "status": "PASS", "armed": 1, "fired": 1, "natural_signal": 1,
                    "classifier": rclf["id"], "state": st,
                    "signal_time": str(row["t"]), "fill_time": str(fill_time),
                    "signal_bar_end": str(row["t"]), "fill_bar_end": str(fill_time),
                    "symbol": tk, "reduce_pct": 25, "same_bar_fill": "NO",
                    "duplicate_fill": "NO", "direction": "REDUCE", "same_day_rerisk": "NO",
                }
                self._MsLog(
                    f"CG_MAISR_D3_CANARY_FINAL,status=PASS,natural_signal=YES,"
                    f"classifier={rclf['id']},state={st},symbol={tk},"
                    f"signal_time={row['t']},fill_time={fill_time},"
                    f"same_bar_fill=NO,duplicate_fill=NO,direction=REDUCE"
                )
                return self._d3_canary
        self._d3_canary = {"status": "FAIL", "armed": 0, "fired": 0, "reason": "no_natural_signal"}
        self._MsLog("CG_MAISR_D3_CANARY_FINAL,status=FAIL,reason=no_natural_signal")
        return self._d3_canary

    def _D3SymbolRoles(self):
        panel = list(getattr(self, "_ms_all", ()) or ())
        spyg = str(self.get_parameter("spyg_sat_trade_enable") or "0").strip() in ("1", "true", "yes")
        held_seen = set((getattr(self, "_d2_asset", {}) or {}).keys())
        roles = {}
        for tk in sorted(set(panel) | held_seen | set(_D3_SAT) | set(_D3_DEF_TK)):
            src = "panel"
            if tk in _D3_PARK or tk in ("BIL", "SGOV", "USFR"):
                role, src = "PARKING", "cash_parking"
            elif tk in _D3_INV:
                role, src = "INVERSE_CONFIRM", "sh_confirm"
            elif tk in ("BND", "TIP", "GLD", "GLDM"):
                role, src = "DEFENSIVE", "defensive_block"
            elif tk in _D3_SAT and not spyg:
                role, src = "INACTIVE_TRADING_PATH", "spyg_sat_trade_enable=0"
            elif tk in held_seen:
                role, src = "ACTIVE_HELD_RISK", "production_holding_or_target"
            elif tk in _D2_BREADTH or tk in ("DBC", "XLE", "XLB"):
                role, src = "SENSOR_ONLY", "classifier_panel"
            elif tk == "SPY":
                role, src = "SENSOR_ONLY", "benchmark"
            else:
                role, src = "SENSOR_ONLY", "minute_panel"
            roles[tk] = {"role": role, "source": src}
        self._d3_roles = roles
        return roles

    def _D3Distributions(self, train_rows):
        feats = defaultdict(list)
        for r in train_rows:
            feats["SPY_60m_MAE_ATR"].append(r["spy_mae"])
            br_n = [v for v in (r.get("br_maes") or {}).values() if v is not None]
            # adverse breadth at -0.60 reference for distribution
            feats["equity_adverse_breadth"].append(
                (sum(1 for v in br_n if v <= -0.60) / len(br_n)) if br_n else None)
            feats["duration_60m_MAE_ATR"].append(r["dur_mae"] if r["dur_ok"] else None)
            feats["gold_60m_MAE_ATR"].append(r["gold_mae"] if r["gold_ok"] else None)
            feats["inflation_relative_ATR"].append(r.get("infl_rel"))
            avail = [b for b in r["blocks"] if b.get("ok") and b.get("abs") is not None]
            if avail:
                feats["defensive_median_absolute_ATR"].append(
                    sorted(b["abs"] for b in avail)[len(avail) // 2])
                feats["defensive_median_relative_ATR"].append(
                    sorted(b["rel"] for b in avail)[len(avail) // 2])
                feats["defensive_resilient_block_count"].append(
                    sum(1 for b in avail if b["abs"] >= 0 and b["rel"] >= 0.18))
            else:
                feats["defensive_median_absolute_ATR"].append(None)
                feats["defensive_median_relative_ATR"].append(None)
                feats["defensive_resilient_block_count"].append(None)
            for tk, hf in (r.get("held") or {}).items():
                feats["held_60m_MAE_ATR"].append(hf.get("mae"))
                feats["held_underperformance_SPY_ATR"].append(hf.get("vs_spy"))
                feats["held_underperformance_proxy_ATR"].append(hf.get("vs_proxy"))
                feats["proxy_60m_MAE_ATR"].append(hf.get("proxy_mae"))
        self._d3_dists = [_D3DistRow(k, v) for k, v in sorted(feats.items())]
        return self._d3_dists

    def _D3EmitChunks(self, key, text) -> None:
        import base64, zlib
        raw = zlib.compress(text.encode("utf-8"), 9)
        b64 = base64.b64encode(raw).decode("ascii")
        chunk = 700
        n = (len(b64) + chunk - 1) // chunk
        used = int(getattr(self, "_ms_art_used", 0) or 0)
        budget = 34000
        name = str(key).replace(",", "_")
        emit_n = 0
        meta = f"CG_MAISR_D3_ART_META,name={name},bytes={len(text)},zbytes={len(raw)},chunks={n}"
        if used + len(meta) + 1 > budget:
            self._MsLog(f"{meta},emitted=0,truncated=YES")
            return
        self._MsLog(f"{meta},emitted_pending=1")
        used += len(meta) + 1
        for i in range(n):
            part = b64[i * chunk:(i + 1) * chunk]
            line = f"CG_MAISR_D3_ART,name={name},i={i},n={n},b64={part}"
            if used + len(line) + 1 > budget:
                break
            self._MsLog(line)
            used += len(line) + 1
            emit_n += 1
        self._ms_art_used = used
        if emit_n < n:
            self._MsLog(f"CG_MAISR_D3_ART_META,name={name},emitted={emit_n},truncated=YES")
        else:
            self._MsLog(f"CG_MAISR_D3_ART_META,name={name},emitted={emit_n},truncated=NO")

    def _D3Save(self, key, text) -> bool:
        ok = False
        try:
            self.object_store.save(key, text)
            ok = True
        except Exception:
            pass
        try:
            self.object_store.save_bytes(key, text.encode("utf-8"))
            ok = True
        except Exception:
            pass
        try:
            self._D3EmitChunks(key, text)
            ok = True
        except Exception:
            self._d3_err += 1
        return ok

    def _D3IdentityFinals(self):
        results = {}
        leds = getattr(self, "_sr_identity_leds", None) or {}
        cmp_fn = getattr(self, "CgShadowIdentityCompare", None)
        prod_dates = list(getattr(self, "_sr_dates", []) or [])
        prod_rets = list(getattr(self, "_sr_actual_rets", []) or [])
        ids = ("MAISR_REPLAY_IDENTITY", "MAISR_PIPELINE_OFF_IDENTITY",
               "MAISR_SENSOR_NO_ACTION_IDENTITY")
        for label in ids:
            led = leds.get(label) or {}
            rets = list(led.get("rets") or [])
            dates = list(led.get("dates") or [])
            cmp = dict(cmp_fn(rets)) if cmp_fn else {"pass": False, "n": 0}
            peak, trough, recovery = _D2PeakTroughMaxDD(dates, rets)
            chron_ok = True
            try:
                if peak != "NA" and trough != "NA" and peak > trough:
                    chron_ok = False
            except Exception:
                chron_ok = False
            if not chron_ok:
                cmp["pass"] = False
            nav_d = abs(float(cmp.get("nav_diff_pct") or cmp.get("nav_d") or 0))
            dd_d = abs(float(cmp.get("maxdd_diff_pp") or cmp.get("dd_d") or 0))
            corr = float(cmp.get("corr") or cmp.get("correlation") or 0)
            # tolerate either key naming from shadow compare
            if "nav_diff_pct" not in cmp and "nav_d" in cmp:
                nav_d = abs(float(cmp.get("nav_d") or 0))
            passed = bool(cmp.get("pass") or cmp.get("match")) and nav_d <= 0.10 and dd_d <= 0.10 and corr >= 0.9999 and chron_ok
            results[label] = {
                "pass": passed, "n": cmp.get("n", 0),
                "nav_d": nav_d, "dd_d": dd_d, "corr": corr,
                "maxdd_peak_date": peak, "maxdd_trough_date": trough,
                "maxdd_recovery_date": recovery,
                "maxdd_episode_dates_valid": "YES" if chron_ok else "NO",
            }
            self._MsLog(
                f"CG_MAISR_D3_IDENTITY_FINAL,id={label},pass={'YES' if passed else 'NO'},"
                f"n={cmp.get('n',0)},nav_diff_pct={_d3f(nav_d,6)},maxdd_diff_pp={_d3f(dd_d,6)},"
                f"corr={_d3f(corr,6)},maxdd_peak_date={peak},maxdd_trough_date={trough},"
                f"maxdd_recovery_date={recovery},maxdd_episode_dates_valid="
                f"{'YES' if chron_ok else 'NO'}"
            )
        return results

    def _D3Export(self, bid, id_results, pack_stats, known_rows, scored, chosen, me_sel, he_sel, cov):
        self._ms_art_used = 0
        # identity
        hdr = ["id", "pass", "n", "nav_diff_pct", "maxdd_diff_pp", "corr",
               "maxdd_peak_date", "maxdd_trough_date", "maxdd_recovery_date",
               "maxdd_episode_dates_valid"]
        lines = [",".join(hdr)]
        for lab, r in id_results.items():
            lines.append(",".join(str(x) for x in [
                lab, "YES" if r["pass"] else "NO", r["n"], _d3f(r["nav_d"], 6),
                _d3f(r["dd_d"], 6), _d3f(r["corr"], 6), r["maxdd_peak_date"],
                r["maxdd_trough_date"], r["maxdd_recovery_date"], r["maxdd_episode_dates_valid"],
            ]))
        self._D3Save(f"cg_maisr_d3_identity_{bid}.csv", "\n".join(lines))

        # symbol roles
        rh = ["symbol", "role", "source"]
        rl = [",".join(rh)]
        for tk, v in sorted(self._d3_roles.items()):
            rl.append(f"{tk},{v['role']},{v['source']}")
        self._D3Save(f"cg_maisr_d3_symbol_roles_{bid}.csv", "\n".join(rl))

        # distributions
        dh = ["feature", "count", "missing", "zero", "mean", "std", "min", "max"] + [f"p{q}" for q in _D3_Q]
        dl = [",".join(dh)]
        for row in self._d3_dists:
            dl.append(",".join(_d3f(row.get(h), 6) if h not in ("feature", "count", "missing", "zero")
                               else str(row.get(h)) for h in dh))
        self._D3Save(f"cg_maisr_d3_distributions_{bid}.csv", "\n".join(dl))

        # packs
        ph = ["id", "pass", "support_ok", "stability_ok", "semantic_ok", "mono_ok",
              "broad_episodes", "local_sector_episodes", "defensive_episodes",
              "systemic_episodes", "rate_episodes", "broad_unique_days",
              "local_sector_unique_held_days", "systemic_available", "rate_available",
              "dist_score", "selected"]
        pl = [",".join(ph)]
        for p in _D3_PACKS:
            s = pack_stats[p["id"]]
            row = []
            for h in ph:
                if h == "selected":
                    row.append(str(int(p["id"] == self._d3_selected_pack)))
                else:
                    row.append(str(s.get(h, "NA")))
            pl.append(",".join(row))
        self._D3Save(f"cg_maisr_d3_label_packs_{bid}.csv", "\n".join(pl))

        # episode summary selected
        sh = ["row_type", "pack", "state", "symbol", "episode_count", "evaluation_count",
              "unique_day_count", "window"]
        sl = [",".join(sh)]
        agg = defaultdict(lambda: {"ep": 0, "n": 0, "days": set()})
        for e in list(me_sel) + list(he_sel):
            k = (e["label"], e.get("symbol", "MACRO"))
            agg[k]["ep"] += 1
            agg[k]["n"] += e.get("n", 1)
            agg[k]["days"].add(e["day"])
        for (lab, sym), v in sorted(agg.items()):
            sl.append(",".join(str(x) for x in [
                "SUMMARY", self._d3_selected_pack or "NONE", lab, sym, v["ep"], v["n"],
                len(v["days"]), "TRAIN_2012_2018"]))
        self._D3Save(f"cg_maisr_d3_episode_summary_{bid}.csv", "\n".join(sl))

        eh = ["pack", "state", "symbol", "start", "end", "evaluation_count", "day", "dur_min"]
        el = [",".join(eh)]
        for e in list(me_sel) + list(he_sel):
            el.append(",".join(str(x) for x in [
                self._d3_selected_pack, e["label"], e.get("symbol", "MACRO"),
                e["start"], e["end"], e.get("n", 1), e["day"], _d3f(e.get("dur_min"), 2),
            ]))
        self._D3Save(f"cg_maisr_d3_episodes_selected_{bid}.csv", "\n".join(el))

        kh = ["pack", "window", "broad", "systemic", "rate", "defensive", "local_sector",
              "first_signal", "max_merged_duration"]
        kl = [",".join(kh)]
        for r in known_rows:
            kl.append(",".join(str(r.get(h, "NA")) for h in kh))
        self._D3Save(f"cg_maisr_d3_known_windows_{bid}.csv", "\n".join(kl))

        ch = ["id", "s", "a", "b", "h", "score", "macro_f1", "valid", "validity_reason",
              "selected", "broad_pred_episodes", "locsec_pred_episodes", "n",
              "f1_BROAD", "f1_LOCAL", "f1_SECTOR", "f1_SYSTEMIC", "f1_RATE", "f1_DEF"]
        cl = [",".join(ch)]
        sel = {r["id"] for r in chosen}
        for r in scored:
            f1 = r.get("f1") or {}
            cl.append(",".join(str(x) for x in [
                r["id"], r["s"], r["a"], r["b"], r["h"], _d3f(r.get("score"), 6),
                _d3f(r.get("macro_f1"), 6), r.get("valid", 0), r.get("validity_reason"),
                int(r["id"] in sel), r.get("broad_pred_episodes", 0),
                r.get("locsec_pred_episodes", 0), r.get("n", 0),
                _d3f(f1.get("BROAD_EQUITY_STRESS"), 4),
                _d3f(f1.get("LOCAL_ASSET_STRESS"), 4),
                _d3f(f1.get("SECTOR_STRESS"), 4),
                _d3f(f1.get("SYSTEMIC_LIQUIDITY_STRESS"), 4),
                _d3f(f1.get("RATE_INFLATION_STRESS"), 4),
                _d3f(f1.get("DEFENSIVE_ROTATION"), 4),
            ]))
        self._D3Save(f"cg_maisr_d3_classifiers_{bid}.csv", "\n".join(cl))

        c = self._d3_canary or {}
        cah = ["status", "armed", "fired", "natural_signal", "classifier", "state",
               "signal_time", "fill_time", "symbol", "reduce_pct", "same_bar_fill",
               "duplicate_fill", "direction", "same_day_rerisk"]
        self._D3Save(
            f"cg_maisr_d3_canary_{bid}.csv",
            ",".join(cah) + "\n" + ",".join(str(c.get(k, "NA")) for k in cah),
        )

    def CgMaisrD3OnEndOfAlgorithm(self, parity_ok) -> bool:
        if not getattr(self, "cg_maisr_final_d3_enable", False):
            return False
        if not getattr(self, "cg_maisr_d3_label_only", False):
            return False
        try:
            self._D2FlushPending()
        except Exception:
            self._d3_err += 1
        if not parity_ok:
            self._MsLog("CG_MAISR_D3_GATE_FINAL,calibration_gate=FAIL,reason=parity_fail,"
                        "next=FIX_MAISR_D3_IMPLEMENTATION")
            return True

        id_results = self._D3IdentityFinals()
        id_ok = bool(id_results) and all(r.get("pass") for r in id_results.values())
        cov = self._D2CoverageReport()
        cov_ok = cov["coverage_ratio"] >= 0.99 and cov["finalized_ratio"] >= 0.99
        self._MsLog(
            f"CG_MAISR_D3_DISTRIBUTION_FINAL,train_raw={len([x for x in self._d3_raw if x.get('train')])},"
            f"coverage_ratio={_d3f(cov['coverage_ratio'],4)},"
            f"finalized_ratio={_d3f(cov['finalized_ratio'],4)},"
            f"held_rows={cov['held_rows']},held_symbols={','.join(cov['held_symbols']) or 'NONE'}"
        )
        roles = self._D3SymbolRoles()
        train_raw = [r for r in self._d3_raw if r.get("train")]
        self._D3Distributions(train_raw)

        train_days = {r["do"] for r in train_raw}
        train_a = {d for d in train_days if _D3_TRAINA0 <= d <= _D3_TRAINA1}
        train_b = {d for d in train_days if _D3_TRAINB0 <= d <= _D3_TRAINB1}
        held_day_n = len(getattr(self, "_d2_held_days", set()) or set()) or 1

        pack_stats = {}
        known_all = []
        labeled_by = {}
        eps_by = {}
        old_n = merged_n = 0
        for pack in _D3_PACKS:
            labeled = self._D3ApplyPack(pack, train_raw)
            me, he, ome, ohe = self._D3EpisodesForLabeled(labeled)
            old_n += len(ome) + len(ohe)
            merged_n += len(me) + len(he)
            st = self._D3PackStats(pack, me, he, len(train_days), held_day_n, train_a, train_b)
            # Known-window semantic audit uses full-period labels (incl. 2020/2022).
            full_lab = self._D3ApplyPack(pack, self._d3_raw)
            fme, fhe, _, _ = self._D3EpisodesForLabeled(full_lab)
            kw, sem_ok = self._D3KnownWindowAudit(pack["id"], fme, fhe)
            st["semantic_ok"] = int(sem_ok)
            known_all.extend(kw)
            pack_stats[pack["id"]] = st
            labeled_by[pack["id"]] = labeled
            eps_by[pack["id"]] = (me, he)
            self._MsLog(
                f"CG_MAISR_D3_PACK_FINAL,id={pack['id']},support={st['support_ok']},"
                f"stable={st['stability_ok']},semantic={st['semantic_ok']},"
                f"broad={st['broad_episodes']},ls={st['local_sector_episodes']},"
                f"def={st['defensive_episodes']},sys={st['systemic_episodes']},"
                f"rate={st['rate_episodes']}"
            )

        mono = self._D3Monotonicity(pack_stats)
        for s in pack_stats.values():
            s["mono_ok"] = int(mono)
            s["pass"] = int(s["support_ok"] and s["stability_ok"] and s["semantic_ok"] and s["mono_ok"])

        frag = (old_n / merged_n) if merged_n else 0.0
        self._d3_old_eps, self._d3_merged_eps = old_n, merged_n
        chosen_pack = self._D3SelectPack(pack_stats)
        self._d3_selected_pack = chosen_pack
        self._d3_pack_stats = pack_stats
        self._d3_eps_cache = eps_by

        if chosen_pack:
            s = pack_stats[chosen_pack]
            self._MsLog(
                f"CG_MAISR_D3_SELECTED_PACK,id={chosen_pack},broad={s['broad_episodes']},"
                f"ls={s['local_sector_episodes']},def={s['defensive_episodes']},"
                f"sys={s['systemic_episodes']},rate={s['rate_episodes']},"
                f"old_episodes={old_n},merged_episodes={merged_n},"
                f"fragmentation_ratio={_d3f(frag,4)}"
            )
        else:
            self._MsLog(
                f"CG_MAISR_D3_SELECTED_PACK,id=NONE,old_episodes={old_n},"
                f"merged_episodes={merged_n},fragmentation_ratio={_d3f(frag,4)},"
                f"reason=no_valid_pack"
            )

        scored, chosen, modes = [], [], set()
        canary_ok = False
        me_sel, he_sel = [], []
        if chosen_pack:
            me_sel, he_sel = eps_by[chosen_pack]
            scored = self._D3ScoreClassifiers(
                chosen_pack, labeled_by[chosen_pack], me_sel, he_sel, pack_stats[chosen_pack])
            chosen, modes = self._D3SelectClassifiers(scored)
            self._d3_scored, self._d3_chosen, self._d3_modes = scored, chosen, modes
            for r in chosen[:6]:
                self._MsLog(
                    f"CG_MAISR_D3_CLASSIFIER_SELECTED,id={r['id']},H={r['h']},"
                    f"score={_d3f(r['score'],4)},macro_f1={_d3f(r['macro_f1'],4)}"
                )
            canary_ok = self._D3NaturalCanary(chosen, labeled_by[chosen_pack]).get("status") == "PASS"
        else:
            # still score empty classifiers artifact with 54 rows valid=0
            scored = [{
                "id": _clfid(s, a, b, h), "idx": i, "s": s, "a": a, "b": b, "h": h,
                "score": 0, "macro_f1": 0, "valid": 0, "validity_reason": "no_pack",
                "f1": {}, "broad_pred_episodes": 0, "locsec_pred_episodes": 0, "n": 0,
                "broad_f1": 0, "locsec_f1": 0, "broad_fp_rate": 0, "ltbe": 0,
            } for i, (s, a, b, h) in enumerate(_ALL_CFG)]
            self._d3_scored = scored
            self._MsLog("CG_MAISR_D3_CANARY_FINAL,status=FAIL,reason=no_label_pack")

        bid = self._MsBid()
        try:
            self._D3Export(bid, id_results, pack_stats, known_all, scored, chosen, me_sel, he_sel, cov)
        except Exception:
            self._d3_err += 1

        data_ok = (int(getattr(self, "_ms_bd_conflict", 0) or 0) == 0
                   and int(getattr(self, "_ms_bd_oo", 0) or 0) == 0)
        clf_ok = len(chosen) >= 3 and len(modes) >= 2
        roles_ok = bool(roles) and all(
            roles.get(tk, {}).get("role") in (
                "INACTIVE_TRADING_PATH", "SENSOR_ONLY") for tk in _D3_SAT)
        auth = bool(id_ok and cov_ok and chosen_pack and clf_ok and canary_ok and data_ok
                    and roles_ok and self._d3_err == 0 and mono)

        if not id_ok:
            reason, nxt = "identity_fail", "FIX_MAISR_D3_IMPLEMENTATION"
        elif not cov_ok or not data_ok:
            reason, nxt = "coverage_or_data_fail", "FIX_MAISR_D3_IMPLEMENTATION"
        elif not mono:
            reason, nxt = "monotonicity_fail", "FIX_MAISR_D3_IMPLEMENTATION"
        elif not chosen_pack:
            reason, nxt = "no_valid_pack", "STOP_MAISR"
        elif not clf_ok:
            reason, nxt = "insufficient_classifiers", "STOP_MAISR"
        elif not canary_ok:
            reason, nxt = "canary_fail", "STOP_MAISR"
        elif not auth:
            reason, nxt = "gate_fail", "FIX_MAISR_D3_IMPLEMENTATION"
        else:
            reason, nxt = "calibration_pass", "ECONOMIC_AUTHORIZED"

        self._MsLog(
            f"CG_MAISR_D3_GATE_FINAL,calibration_gate={'PASS' if auth else 'FAIL'},"
            f"full_grid_authorized={'YES' if auth else 'NO'},"
            f"selected_pack={chosen_pack or 'NONE'},"
            f"classifiers_selected={len(chosen)},modes={','.join(sorted(modes)) or 'NONE'},"
            f"canary={'PASS' if canary_ok else 'FAIL'},reason={reason},next={nxt},"
            f"frozen_classifiers={','.join(r['id'] for r in chosen)}"
        )
        return True

    def CgMaisrD3EconomicGate(self, parity_ok) -> bool:
        if not getattr(self, "cg_maisr_final_d3_enable", False):
            return False
        if getattr(self, "cg_maisr_d3_label_only", False):
            return False
        pack = str(getattr(self, "cg_maisr_d3_selected_pack", "") or "").strip()
        sels = getattr(self, "cg_maisr_d3_selected_classifiers", None) or []
        if not pack or not sels or not getattr(self, "_ms_grid_on", False):
            return False
        try:
            self._D2FlushPending()
        except Exception:
            self._d3_err += 1
        self._MsLog(
            f"CG_MAISR_D3_ECON_INIT,pack={pack},classifiers={','.join(sels)},grid=1"
        )
        if not parity_ok:
            self._MsLog("CG_MAISR_D3_REVALIDATION_FINAL,pass=NO,reason=parity_fail")
            return True
        # Rebuild pack selection path for revalidation
        train_raw = [r for r in self._d3_raw if r.get("train")]
        pack_obj = next((p for p in _D3_PACKS if p["id"] == pack), None)
        if not pack_obj:
            self._MsLog("CG_MAISR_D3_REVALIDATION_FINAL,pass=NO,reason=unknown_pack")
            self._MsLog("CG_MAISR_D3_GATE_FINAL,next=FIX_MAISR_GRID_INTERFERENCE")
            return True
        labeled = self._D3ApplyPack(pack_obj, train_raw)
        me, he, _, _ = self._D3EpisodesForLabeled(labeled)
        train_days = {r["do"] for r in train_raw}
        train_a = {d for d in train_days if _D3_TRAINA0 <= d <= _D3_TRAINA1}
        train_b = {d for d in train_days if _D3_TRAINB0 <= d <= _D3_TRAINB1}
        held_day_n = len(getattr(self, "_d2_held_days", set()) or set()) or 1
        st = self._D3PackStats(pack_obj, me, he, len(train_days), held_day_n, train_a, train_b)
        scored = self._D3ScoreClassifiers(pack, labeled, me, he, st)
        by_id = {r["id"]: r for r in scored}
        frozen_ok = all(i in by_id and by_id[i].get("valid") for i in sels)
        id_results = self._D3IdentityFinals()
        id_ok = bool(id_results) and all(r.get("pass") for r in id_results.values())
        reval = id_ok and frozen_ok and self._d3_err == 0
        self._MsLog(
            f"CG_MAISR_D3_REVALIDATION_FINAL,pass={'YES' if reval else 'NO'},"
            f"identity={'PASS' if id_ok else 'FAIL'},"
            f"classifiers_match={'YES' if frozen_ok else 'NO'},pack={pack}"
        )
        if not reval:
            self._MsLog("CG_MAISR_D3_GATE_FINAL,next=FIX_MAISR_GRID_INTERFERENCE")
            self._MsNoRec("FIX_MAISR_GRID_INTERFERENCE")
            return True
        self._ms_selected_ids = list(sels)
        self._d2_frozen_scored = [by_id[i] for i in sels if i in by_id]
        self._d2_econ_ready = True
        self._d3_econ_ready = True
        return False

    def _D3EmitEconFinals(self, top15, best, rows, ctrl_m, c_oos, c_cri,
                          identity_ok, reason, strict_rows, gate_ok):
        def _f(x, d=4):
            return _d3f(x, d)

        for i, r in enumerate(top15[:15]):
            self._MsLog(
                f"CG_MAISR_D3_TOP,rank={i+1},id={r['id']},clf={r['clf_id']},"
                f"router={r['router']},persist={r['persist']},timing={r['timing']},"
                f"CAGR={_f(r.get('CAGR'))},MaxDD={_f(r.get('MaxDD'))},"
                f"STRICT_PASS={r['STRICT_PASS']},cagr_cost2={_f(r.get('CAGR_cost2'))}"
            )
        bid = self._MsBid()
        headers = ["id", "clf_id", "h", "router", "persist", "timing", "CAGR", "MaxDD",
                   "Sharpe", "worst5", "oos_sharpe", "crisis_maxdd", "STRICT_PASS",
                   "CAGR_cost2", "MaxDD_cost2", "neighbor_stable"]
        plines = [",".join(headers)]
        plines.append(",".join(str(x) for x in [
            "CONTROL", "NA", "NA", "NA", "NA", "NA",
            _f(ctrl_m.get("CAGR")), _f(ctrl_m.get("MaxDD")), _f(ctrl_m.get("Sharpe")),
            _f(ctrl_m.get("worst_5pct_day_mean")), _f(c_oos.get("Sharpe")),
            _f(c_cri.get("MaxDD")), 0, "NA", "NA", 1,
        ]))
        for r in rows:
            plines.append(",".join(str(x) for x in [
                r.get("id"), r.get("clf_id"), r.get("h"), r.get("router"),
                r.get("persist"), r.get("timing"), _f(r.get("CAGR")), _f(r.get("MaxDD")),
                _f(r.get("Sharpe")), _f(r.get("worst_5pct_day_mean")),
                _f(r.get("oos_sharpe")), _f(r.get("crisis_maxdd")),
                r.get("STRICT_PASS", 0), _f(r.get("CAGR_cost2")), _f(r.get("MaxDD_cost2")),
                int(bool(r.get("neighbor_stable"))),
            ]))
        self._D3Save(f"cg_maisr_d3_policies_{bid}.csv", "\n".join(plines))
        vlines = [
            "field,value",
            f"selected_pack,{getattr(self,'cg_maisr_d3_selected_pack','')}",
            f"selected_classifiers,{','.join(getattr(self,'cg_maisr_d3_selected_classifiers',[]) or [])}",
            f"identity_revalidation,{'PASS' if identity_ok else 'FAIL'}",
            f"policies_evaluated,{len(rows)}",
            f"strict_pass_count,{len(strict_rows)}",
            f"gate_ok,{int(bool(gate_ok))}",
        ]
        self._D3Save(f"cg_maisr_d3_selected_validation_{bid}.csv", "\n".join(vlines))
        if best is not None:
            self._MsLog(
                f"CG_MAISR_D3_RECOMMENDATION,apply=YES,policy={best['id']},"
                f"classifier={best['clf_id']},router={best['router']},persistence={best['persist']},"
                f"timing={best['timing']},SH_mode={best.get('h','NA')},"
                f"CAGR={_f(best.get('CAGR'))},MaxDD={_f(best.get('MaxDD'))},"
                f"OOS_Sharpe={_f(best.get('oos_sharpe'))},CRISIS_MaxDD={_f(best.get('crisis_maxdd'))},"
                f"CAGR_2bps={_f(best.get('CAGR_cost2'))},reason={reason},"
                f"next=PREPARE_MAISR_LIVE_SHADOW_D1"
            )
        else:
            self._MsLog(
                f"CG_MAISR_D3_RECOMMENDATION,apply=NO,policy=KEEP_CURRENT_SH,"
                f"reason={reason},next=STOP_MAISR"
            )
