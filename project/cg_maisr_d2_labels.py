from datetime import date as _date, datetime as _datetime, timedelta as _timedelta
from collections import deque, defaultdict
# cg_maisr_d2_labels.py -- CG-MAISR-LABEL-COVERAGE-D2 evaluation-level labels.
#
# ROOT CAUSE: P1 _MsBuildLabels (cg_maisr_diag.py:672-709) keyed labels by
# date-ordinal using 5-day EOD _MsFwd/_ms_eod; _MsScoreConfigs (:711-724)
# scored only train_days with idx[do]["pre"] (09:44) -> n~1760.
# D2: one 60m-forward observation per eligible evaluation timestamp.

_D2_ROOT_CAUSE = (
    "P1_day_keyed_5d_EOD_labels+_MsScoreConfigs_PRE_only:"
    "cg_maisr_diag.py:_MsBuildLabels:672-709+_MsScoreConfigs:711-724"
)

_STATES = ("SYSTEMIC_LIQUIDITY_STRESS", "RATE_INFLATION_STRESS", "BROAD_EQUITY_STRESS",
           "SECTOR_STRESS", "LOCAL_ASSET_STRESS", "DEFENSIVE_ROTATION",
           "UNCONFIRMED_NOISE", "NORMAL")
_SIX = {s: i for i, s in enumerate(_STATES)}
_AMIN = (2, 3)
_BRTH = (0.50, 0.65, 0.75)
_HMODE = ("H0", "H1", "H2")


def _clfid(s, a, b, h):
    return f"{s}_C{a}_B{int(round(b * 100)):02d}_{h}"


_ALL_CFG = [(s, a, b, h) for s in ("S1", "S2", "S3") for a in _AMIN
            for b in _BRTH for h in _HMODE]

# Exact task thresholds (ATR fractions / breadth fractions).
LP_MILD = {
    "broad_spy": -0.15, "broad_br": 0.50,
    "local_mae": -0.20, "local_vs_spy": -0.15,
    "sector_held": -0.15, "sector_proxy": -0.15,
    "sys_dur": -0.10, "sys_gold": -0.10,
    "rate_spy": -0.10, "rate_br": 0.50, "rate_dur": -0.15, "rate_infl_rel": 0.10,
    "def_spy": -0.15, "def_rel": 0.15,
}
LP_BALANCED = {
    "broad_spy": -0.20, "broad_br": 0.60,
    "local_mae": -0.25, "local_vs_spy": -0.20,
    "sector_held": -0.20, "sector_proxy": -0.20,
    "sys_dur": -0.15, "sys_gold": -0.15,
    "rate_spy": -0.15, "rate_br": 0.60, "rate_dur": -0.20, "rate_infl_rel": 0.15,
    "def_spy": -0.20, "def_rel": 0.20,
}
LP_STRICT = {
    "broad_spy": -0.25, "broad_br": 0.70,
    "local_mae": -0.35, "local_vs_spy": -0.25,
    "sector_held": -0.25, "sector_proxy": -0.25,
    "sys_dur": -0.20, "sys_gold": -0.20,
    "rate_spy": -0.20, "rate_br": 0.70, "rate_dur": -0.25, "rate_infl_rel": 0.20,
    "def_spy": -0.25, "def_rel": 0.25,
}
_D2_PACKS = {"LP_MILD": LP_MILD, "LP_BALANCED": LP_BALANCED, "LP_STRICT": LP_STRICT}
_D2_PACK_ORDER = ("LP_BALANCED", "LP_MILD", "LP_STRICT")

_D2_EXCLUDE_HELD = frozenset(("SPY", "BIL", "SGOV", "USFR", "BND", "TIP", "GLD", "GLDM", "SH"))
_D2_PARK = frozenset(("BIL", "SGOV", "USFR"))
_D2_DUR = ("BND", "TIP")
_D2_GOLD = ("GLD", "GLDM")
_D2_BREADTH = ("XLE", "XLB", "XLV", "XLU")
_D2_INFL = ("DBC", "XLE")
_D2_DEF = ("BND", "TIP", "GLD", "GLDM", "SH", "BIL", "SGOV", "USFR")
# SPYG has no minute feed -> no SECTOR proxy for growth names.
_D2_PROXY = {"XLE": None, "XLB": None, "XLV": None, "XLU": None, "DBC": None,
             "MU": None, "NVDA": None, "AVGO": None}
_D4_SECTOR_ASSETS = frozenset(("XLE", "XLB", "XLV", "XLU", "DBC"))

_D2_TRAIN0 = _date(2012, 1, 1).toordinal()
_D2_TRAIN1 = _date(2018, 12, 31).toordinal()
_D2_FWD = 60
_D2_TOD_PRE = 584
_D2_TOD_LO, _D2_TOD_HI = 590, 900


def _d2_min(a, b):
    try:
        return (b - a).total_seconds() / 60.0
    except Exception:
        return 1e9


def _D2PeakTroughMaxDD(dates, rets):
    """Peak that begins the MaxDD episode (not a later global NAV peak)."""
    if not dates or not rets or len(dates) != len(rets):
        return ("NA", "NA", "NA")
    nav = peak = 1.0
    peak_d = dates[0]
    best_dd, best_peak, best_trough, best_peak_nav = 0.0, peak_d, "NA", peak
    for d, r in zip(dates, rets):
        nav = max(1e-12, nav * (1.0 + r))
        if nav > peak:
            peak, peak_d = nav, d
        dd = 1.0 - nav / peak
        if dd > best_dd:
            best_dd, best_peak, best_trough, best_peak_nav = dd, peak_d, d, peak
    recovery, nav2, seen = "NA", 1.0, False
    for d, r in zip(dates, rets):
        nav2 = max(1e-12, nav2 * (1.0 + r))
        if not seen:
            if d == best_trough:
                seen = True
            continue
        if nav2 >= best_peak_nav - 1e-12:
            recovery = d
            break
    return (best_peak, best_trough, recovery)


def _d2_feed_ep(store, key, day, ts, label, closed):
    pos = bool(label) and label not in ("NORMAL", None, "UNAVAILABLE")
    ep = store.get(key)
    if pos:
        if (ep and ep["label"] == label and ep["day"] == day
                and _d2_min(ep["last"], ts) <= 10):
            ep["end"] = ts
            ep["n"] += 1
            ep["last"] = ts
            ep["neg"] = 0
        else:
            if ep:
                closed.append(ep)
            store[key] = {"key": key, "label": label, "day": day, "start": ts,
                          "end": ts, "n": 1, "last": ts, "neg": 0,
                          "symbol": key if key not in (None, "MACRO") else "MACRO"}
        return
    if not ep:
        return
    ep["neg"] += 1
    if ep["neg"] >= 2 or _d2_min(ep["last"], ts) > 30 or day != ep["day"]:
        closed.append(ep)
        store.pop(key, None)


class CgMaisrD2LabelMixin:
    """Evaluation-level pending forward outcomes + pack/episode/classifier."""

    def _D2InitLabelEngine(self) -> None:
        panel = tuple(getattr(self, "_ms_all", ()) or ())
        self._d2_panel = panel
        self._d2_bars = {tk: deque(maxlen=90) for tk in panel}
        self._d2_pending = deque()
        self._d2_n_eval = self._d2_n_elig = self._d2_n_fin = self._d2_n_drop = 0
        self._d2_held_rows = 0
        self._d2_train = []  # compact TRAIN finalized rows
        self._d2_asset = {}  # tk -> coverage dict
        self._d2_err = 0
        self._d2_selected_pack = None
        self._d2_pack_stats = {}
        self._d2_expected_train = 0
        self._d2_train_days = set()
        self._d2_held_days = set()

    def _D2OnBar(self, tk, et, o, h, l, c) -> None:
        ring = self._d2_bars.get(tk)
        if ring is None:
            ring = deque(maxlen=90)
            self._d2_bars[tk] = ring
        try:
            ring.append((et, float(o), float(h), float(l), float(c)))
            self._D2TryFinalize()
            if getattr(self, "cg_maisr_d4_enable", False) and hasattr(self, "_D4TryFillPending"):
                self._D4TryFillPending(tk, et, float(o))
        except Exception:
            self._d2_err += 1

    def _D2HeldEligible(self, tk, weight) -> bool:
        try:
            w = float(weight or 0.0)
        except Exception:
            return False
        if w < 0.02:
            return False
        if tk in _D2_EXCLUDE_HELD or tk in _D2_PARK:
            return False
        if tk not in (getattr(self, "_ms_all", set()) or set()):
            return False
        atr = (getattr(self, "_ms_atr", {}) or {}).get(tk)
        return bool(atr and atr > 0)

    def _D2HeldWeights(self) -> dict:
        out = {}
        try:
            tpv = float(self.portfolio.total_portfolio_value)
        except Exception:
            tpv = 0.0
        if tpv > 0:
            try:
                for kvp in self.portfolio:
                    try:
                        h = kvp.Value if hasattr(kvp, "Value") else self.portfolio[kvp]
                        sym = kvp.Key if hasattr(kvp, "Key") else kvp
                        tk = str(getattr(sym, "Value", None) or getattr(sym, "value", None) or sym)
                        hv = float(getattr(h, "holdings_value", None)
                                   or getattr(h, "HoldingsValue", 0) or 0)
                        w = hv / tpv
                        if self._D2HeldEligible(tk, w):
                            out[tk] = max(out.get(tk, 0.0), w)
                    except Exception:
                        continue
            except Exception:
                pass
        caps = getattr(self, "_ms_caps", None) or []
        if caps:
            for tk, w in (caps[-1][1] or {}).items():
                if self._D2HeldEligible(tk, w):
                    out[tk] = max(out.get(tk, 0.0), float(w))
        return out

    def _D2LabelEligible(self, tod) -> bool:
        return tod == _D2_TOD_PRE or (_D2_TOD_LO <= tod <= _D2_TOD_HI and tod % 5 == 0)

    def _D2OnEval(self, kind, tod, states_bytes, feat_dict) -> None:
        self._d2_n_eval += 1
        if not self._D2LabelEligible(tod):
            return
        self._d2_n_elig += 1
        sig = self.time
        do = sig.date().toordinal()
        if _D2_TRAIN0 <= do <= _D2_TRAIN1:
            self._d2_train_days.add(do)
        atrs = getattr(self, "_ms_atr", {}) or {}
        origin = {}
        for tk, f in (feat_dict or {}).items():
            try:
                ring = self._d2_bars.get(tk)
                px = float(ring[-1][4]) if ring else 0.0
                atr = float(atrs.get(tk) or 0)
                if px > 0 and atr > 0:
                    origin[tk] = px
            except Exception:
                continue
        if "SPY" not in origin:
            return
        held = {tk: w for tk, w in self._D2HeldWeights().items() if tk in origin}
        entry = {
            "t": sig, "do": do, "tod": tod,
            "preds": bytes(states_bytes) if states_bytes is not None else b"\x00" * 54,
            "origin": origin,
            "atrs": {tk: float(atrs[tk]) for tk in origin if atrs.get(tk)},
            "held": held,
            "rg": str(getattr(self, "current_regime", None) or "NEUTRAL").upper(),
            "w2": 1 if getattr(self, "_cg_w2_last_active", False) else 0,
            "ids": str(getattr(self, "_ids_state", None) or "NORMAL"),
        }
        if getattr(self, "cg_maisr_d4_enable", False):
            entry["subjects"] = getattr(self, "_d4_last_subjects", None) or (b"\x00" * 54)
            entry["kind"] = kind
        if getattr(self, "cg_macro_a1_enable", False):
            entry["kind"] = kind
        self._d2_pending.append(entry)
        while len(self._d2_pending) > 128:
            self._d2_pending.popleft()
            self._d2_n_drop += 1

    def _D2TryFinalize(self) -> None:
        wall = self.time
        n = 0
        while self._d2_pending and n < 64:
            p = self._d2_pending[0]
            if wall < p["t"] + _timedelta(minutes=_D2_FWD + 1):
                break
            self._d2_pending.popleft()
            try:
                self._D2FinalizeOne(p)
            except Exception:
                self._d2_err += 1
            n += 1

    def _D2Win(self, tk, t0, px0, atr):
        ring = self._d2_bars.get(tk)
        if not ring or px0 <= 0 or atr <= 0:
            return None
        t1 = t0 + _timedelta(minutes=_D2_FWD)
        last_c, mn = None, None
        for et, o, h, l, c in ring:
            if et is None or et <= t0 or et > t1:
                continue
            last_c = c
            mn = l if mn is None else min(mn, l)
        if last_c is None:
            return None
        ret60 = (last_c - px0) / atr
        mae = ((mn - px0) / atr) if mn is not None else ret60
        return {"mae": mae, "ret": ret60, "raw_ret": (last_c / px0 - 1.0)}

    def _D2MacroLabel(self, pack, spy_mae, breadth, dur_mae, gold_mae, infl_rel, def_rel,
                      dur_ok, gold_ok):
        """Priority: SYSTEMIC > RATE > BROAD > DEFENSIVE > NORMAL."""
        broad = (spy_mae <= pack["broad_spy"] and breadth >= pack["broad_br"])
        flags = {"BROAD": broad}
        if broad and dur_ok and gold_ok and dur_mae <= pack["sys_dur"] and gold_mae <= pack["sys_gold"]:
            return "SYSTEMIC_LIQUIDITY_STRESS", flags
        rate = ((spy_mae <= pack["rate_spy"] or breadth >= pack["rate_br"])
                and dur_ok and dur_mae <= pack["rate_dur"] and infl_rel >= pack["rate_infl_rel"])
        flags["RATE"] = rate
        if rate:
            return "RATE_INFLATION_STRESS", flags
        if broad:
            return "BROAD_EQUITY_STRESS", flags
        defensive = (spy_mae <= pack["def_spy"] and def_rel >= pack["def_rel"] and not rate)
        flags["DEF"] = defensive
        if defensive:
            return "DEFENSIVE_ROTATION", flags
        return "NORMAL", flags

    def _D2HeldLabel(self, pack, held_mae, vs_spy, proxy_mae, proxy_ok, broad):
        if broad:
            return "NORMAL", {}
        flags = {}
        if proxy_ok and proxy_mae is not None:
            if held_mae <= pack["sector_held"] and proxy_mae <= pack["sector_proxy"]:
                return "SECTOR_STRESS", {"SECTOR": True}
        if held_mae <= pack["local_mae"] and vs_spy <= pack["local_vs_spy"]:
            return "LOCAL_ASSET_STRESS", {"LOCAL": True}
        return "NORMAL", flags

    def _D2FinalizeOne(self, p) -> None:
        t0, origin, atrs = p["t"], p["origin"], p["atrs"]
        stats = {}
        for tk, px in origin.items():
            st = self._D2Win(tk, t0, px, atrs.get(tk, 0))
            if st:
                stats[tk] = st
        if "SPY" not in stats:
            return
        spy = stats["SPY"]
        spy_mae = spy["mae"]
        dur_vals = [stats[t]["mae"] for t in _D2_DUR if t in stats]
        dur_ok = bool(dur_vals)
        dur_mae = sum(dur_vals) / len(dur_vals) if dur_ok else 0.0
        # Gold continuity: primary GLD then fallback GLDM; never average.
        primary = getattr(self, "_ms_gold_primary", None) or "GLD"
        fallback = getattr(self, "_ms_gold_fallback", None) or "GLDM"
        gold_stat = stats.get(primary) or stats.get(fallback)
        gold_ok = gold_stat is not None
        gold_mae = gold_stat["mae"] if gold_ok else 0.0
        gold_ret = gold_stat["ret"] if gold_ok else 0.0
        gold_source = primary if primary in stats else (fallback if fallback in stats else "NONE")
        self._d2_gold_double_count_used = 0
        self._d2_last_gold_source = gold_source
        # ATR-normalized relative returns (raw 0.15–0.25 in 60m is impossible).
        infl_vals = [stats[t]["ret"] for t in _D2_INFL if t in stats]
        infl_ret = (sum(infl_vals) / len(infl_vals)) if infl_vals else None
        infl_rel = (infl_ret - spy["ret"]) if infl_ret is not None else 0.0
        def_vals = [stats[t]["ret"] - spy["ret"] for t in _D2_DEF if t in stats]
        def_rel = max(def_vals) if def_vals else 0.0
        br_maes = {t: stats[t]["mae"] for t in _D2_BREADTH if t in stats}

        held_feat = {}
        for tk, w in p["held"].items():
            own = stats.get(tk)
            if not own:
                continue
            proxy = _D2_PROXY.get(tk, None)
            proxy_ok = proxy is not None and proxy in stats
            proxy_mae = stats[proxy]["mae"] if proxy_ok else None
            vs_spy = own["mae"] - spy_mae
            vs_proxy = (own["mae"] - proxy_mae) if proxy_ok and proxy_mae is not None else None
            held_feat[tk] = {
                "mae": own["mae"], "ret": own["ret"], "w": float(w),
                "spy_mae": spy_mae, "vs_spy": vs_spy, "vs_proxy": vs_proxy,
                "proxy_ok": proxy_ok, "proxy_mae": proxy_mae,
            }
            ac = self._d2_asset.setdefault(tk, {
                "evals": 0, "days": set(), "wsum": 0.0, "wlist": [],
                "first": None, "last": None, "proxy": proxy if proxy else "NONE",
            })
            ac["evals"] += 1
            ac["days"].add(p["do"])
            ac["wsum"] += float(w)
            ac["wlist"].append(float(w))
            ac["first"] = ac["first"] or p["do"]
            ac["last"] = p["do"]
            self._d2_held_days.add((p["do"], tk))
            self._d2_held_rows += 1

        # D3 raw feature snapshot (all eras; TRAIN filtered at EOA).
        if getattr(self, "cg_maisr_final_d3_enable", False) and hasattr(self, "_D3StoreRaw"):
            try:
                blocks = self._D3Blocks(stats, spy["ret"])
                self._D3StoreRaw(
                    p, stats, spy, dur_mae, gold_mae, dur_ok, gold_ok,
                    infl_ret if infl_ret is not None else 0.0, infl_rel, blocks,
                    held_feat, br_maes,
                )
            except Exception:
                self._d2_err += 1
        if getattr(self, "cg_maisr_d4_enable", False) and hasattr(self, "_D4StoreFromFinalize"):
            try:
                self._D4StoreFromFinalize(
                    p, stats, spy, dur_mae, gold_mae, dur_ok, gold_ok,
                    infl_ret if infl_ret is not None else 0.0, infl_rel,
                    held_feat, br_maes, gold_source,
                )
            except Exception:
                self._d2_err += 1
        if getattr(self, "cg_macro_a1_enable", False) and hasattr(self, "_MacroA1StoreFromFinalize"):
            try:
                self._MacroA1StoreFromFinalize(
                    p, stats, spy, dur_mae, gold_mae, dur_ok, gold_ok,
                    infl_ret if infl_ret is not None else 0.0, infl_rel,
                    held_feat, br_maes, gold_source,
                )
            except Exception:
                self._d2_err += 1
        if getattr(self, "cg_macro_resid_b1_enable", False) and hasattr(self, "_MacroResidB1StoreFromFinalize"):
            try:
                self._MacroResidB1StoreFromFinalize(
                    p, stats, spy, dur_mae, gold_mae, dur_ok, gold_ok,
                    infl_ret if infl_ret is not None else 0.0, infl_rel,
                    held_feat, br_maes, gold_source,
                )
            except Exception:
                self._d2_err += 1

        outcomes = {}
        for pname, pack in _D2_PACKS.items():
            br_thr = pack["broad_spy"]  # adverse = mae at least as bad as broad_spy
            br_n = [stats[t] for t in _D2_BREADTH if t in stats]
            breadth = (sum(1 for s in br_n if s["mae"] <= br_thr) / len(br_n)) if br_n else 0.0
            mlab, _ = self._D2MacroLabel(pack, spy_mae, breadth, dur_mae, gold_mae,
                                         infl_rel, def_rel, dur_ok, gold_ok)
            held_labs = {}
            for tk, hf in held_feat.items():
                hlab, _ = self._D2HeldLabel(
                    pack, hf["mae"], hf["vs_spy"], hf["proxy_mae"], hf["proxy_ok"],
                    mlab == "BROAD_EQUITY_STRESS" or mlab == "SYSTEMIC_LIQUIDITY_STRESS")
                held_labs[tk] = hlab
            outcomes[pname] = (mlab, held_labs, breadth)

        self._d2_n_fin += 1
        if _D2_TRAIN0 <= p["do"] <= _D2_TRAIN1:
            # Store outcomes for all packs as state indices + held map
            row = {
                "do": p["do"], "tod": p["tod"], "t": p["t"], "preds": p["preds"],
                "macro": {k: v[0] for k, v in outcomes.items()},
                "held": {k: v[1] for k, v in outcomes.items()},
                "breadth": {k: v[2] for k, v in outcomes.items()},
                "rg": p["rg"], "w2": p["w2"], "ids": p["ids"],
            }
            self._d2_train.append(row)

    def _D2FlushPending(self) -> None:
        while self._d2_pending:
            p = self._d2_pending.popleft()
            try:
                self._D2FinalizeOne(p)
            except Exception:
                self._d2_err += 1

    def _D2BuildEpisodes(self, stream):
        store, closed = {}, []
        for row in stream:
            _d2_feed_ep(store, row.get("symbol", "MACRO"), row["day"], row["ts"],
                        row.get("label"), closed)
        for ep in store.values():
            closed.append(ep)
        return closed

    def _D2EpisodesForPack(self, pack):
        train = self._d2_train
        macro_stream = [{"ts": r["t"], "day": r["do"], "symbol": "MACRO",
                         "label": r["macro"].get(pack)} for r in train]
        held_stream = []
        for r in train:
            for tk, lab in (r["held"].get(pack) or {}).items():
                held_stream.append({"ts": r["t"], "day": r["do"], "symbol": tk, "label": lab})
        return self._D2BuildEpisodes(macro_stream), self._D2BuildEpisodes(held_stream)

    def _D2PackSupport(self, pack, macro_eps, held_eps, train_days, held_day_n):
        def _cnt(eps, lab):
            return sum(1 for e in eps if e["label"] == lab)

        def _days(eps, lab):
            return len({e["day"] for e in eps if e["label"] == lab})

        broad_ep = _cnt(macro_eps, "BROAD_EQUITY_STRESS")
        sys_ep = _cnt(macro_eps, "SYSTEMIC_LIQUIDITY_STRESS")
        rate_ep = _cnt(macro_eps, "RATE_INFLATION_STRESS")
        def_ep = _cnt(macro_eps, "DEFENSIVE_ROTATION")
        loc_ep = _cnt(held_eps, "LOCAL_ASSET_STRESS")
        sec_ep = _cnt(held_eps, "SECTOR_STRESS")
        broad_days = _days(macro_eps, "BROAD_EQUITY_STRESS")
        locsec_days = len({(e["day"], e.get("symbol")) for e in held_eps
                           if e["label"] in ("LOCAL_ASSET_STRESS", "SECTOR_STRESS")})
        td = max(train_days, 1)
        hd = max(held_day_n, 1)
        support_ok = (
            broad_ep >= 20 and (loc_ep + sec_ep) >= 20 and def_ep >= 10
            and broad_days >= 15 and locsec_days >= 15
        )
        density_ok = (broad_ep / td <= 0.15) and ((loc_ep + sec_ep) / hd <= 0.25)
        sys_avail = sys_ep >= 5
        rate_avail = rate_ep >= 5
        return {
            "pack": pack,
            "broad_episodes": broad_ep, "local_episodes": loc_ep, "sector_episodes": sec_ep,
            "local_sector_episodes": loc_ep + sec_ep,
            "defensive_episodes": def_ep, "systemic_episodes": sys_ep, "rate_episodes": rate_ep,
            "broad_unique_days": broad_days, "local_sector_unique_held_days": locsec_days,
            "systemic_available": "YES" if sys_avail else "RARE_UNAVAILABLE",
            "rate_available": "YES" if rate_avail else "RARE_UNAVAILABLE",
            "support_ok": int(support_ok), "density_ok": int(density_ok),
            "pass": int(support_ok and density_ok),
            "train_days": train_days, "held_days": held_day_n,
        }

    def _D2SelectPack(self):
        train_days = len(self._d2_train_days) or len({r["do"] for r in self._d2_train})
        held_day_n = len(self._d2_held_days) or 1
        stats = {}
        eps_cache = {}
        for pack in _D2_PACK_ORDER:
            me, he = self._D2EpisodesForPack(pack)
            eps_cache[pack] = (me, he)
            stats[pack] = self._D2PackSupport(pack, me, he, train_days, held_day_n)
        chosen = None
        if stats["LP_BALANCED"]["pass"]:
            chosen = "LP_BALANCED"
        elif stats["LP_MILD"]["pass"]:
            chosen = "LP_MILD"
        self._d2_selected_pack = chosen
        self._d2_pack_stats = stats
        self._d2_eps_cache = eps_cache
        return chosen, stats

    def _D2ScoreClassifiers(self, pack):
        train = self._d2_train
        if not train or not pack:
            return []
        macro_eps, held_eps = self._d2_eps_cache.get(pack) or self._D2EpisodesForPack(pack)
        sys_avail = self._d2_pack_stats.get(pack, {}).get("systemic_available") == "YES"
        rate_avail = self._d2_pack_stats.get(pack, {}).get("rate_available") == "YES"
        avail_macro = ["BROAD_EQUITY_STRESS", "DEFENSIVE_ROTATION"]
        if sys_avail:
            avail_macro.insert(0, "SYSTEMIC_LIQUIDITY_STRESS")
        if rate_avail:
            avail_macro.insert(1 if sys_avail else 0, "RATE_INFLATION_STRESS")
        held_labs = ("LOCAL_ASSET_STRESS", "SECTOR_STRESS")

        def _match(pe, te):
            if pe["label"] != te["label"]:
                return False
            if pe.get("symbol") and te.get("symbol") and pe["symbol"] != te["symbol"]:
                return False
            if pe["day"] != te["day"]:
                return False
            if pe["start"] <= te["end"] and te["start"] <= pe["end"]:
                return True
            return 0 <= _d2_min(pe["start"], te["start"]) <= 10

        scored = []
        for i, (s, a, b, h) in enumerate(_ALL_CFG):
            pred_m = [{"ts": r["t"], "day": r["do"], "symbol": "MACRO",
                       "label": (_STATES[r["preds"][i]]
                                 if i < len(r["preds"]) and _STATES[r["preds"][i]] in avail_macro
                                 else None)}
                      for r in train]
            pred_h = [{"ts": r["t"], "day": r["do"], "symbol": "MACRO",
                       "label": (_STATES[r["preds"][i]]
                                 if i < len(r["preds"]) and _STATES[r["preds"][i]] in held_labs
                                 else None)}
                      for r in train]
            pme = self._D2BuildEpisodes(pred_m)
            phe = self._D2BuildEpisodes(pred_h)
            tp, fp, fn, f1 = {}, {}, {}, {}
            for lab in list(avail_macro) + list(held_labs):
                truths = [e for e in (macro_eps if lab in avail_macro else held_eps)
                          if e["label"] == lab]
                preds = [e for e in (pme if lab in avail_macro else phe) if e["label"] == lab]
                matched = set()
                tpc = fpc = 0
                for pe in preds:
                    hit = next((j for j, te in enumerate(truths)
                                if j not in matched and _match(pe, te)), None)
                    if hit is not None:
                        tpc += 1
                        matched.add(hit)
                    else:
                        fpc += 1
                fnc = len(truths) - len(matched)
                tp[lab], fp[lab], fn[lab] = tpc, fpc, fnc
                pp = tpc / (tpc + fpc) if (tpc + fpc) else 0.0
                rr = tpc / (tpc + fnc) if (tpc + fnc) else 0.0
                f1[lab] = (2 * pp * rr / (pp + rr)) if (pp + rr) else 0.0
            avail_states = [x for x in avail_macro + list(held_labs) if True]
            macro_f1 = (sum(f1[x] for x in avail_states) / len(avail_states)) if avail_states else 0.0
            n_f1_gt0 = sum(1 for x in avail_states if f1.get(x, 0) > 0)
            broad_pred = sum(1 for e in pme if e["label"] == "BROAD_EQUITY_STRESS")
            locsec_pred = sum(1 for e in phe if e["label"] in held_labs)
            # error rates
            n_broad_fp = fp.get("BROAD_EQUITY_STRESS", 0)
            n_pred_broad = n_broad_fp + tp.get("BROAD_EQUITY_STRESS", 0)
            broad_fp_rate = (n_broad_fp / n_pred_broad) if n_pred_broad else 0.0
            # local predicted as broad: approximate via unmatched
            loc_to_broad = 0.0
            sys_fn_rate = 0.0
            if sys_avail:
                ns = tp.get("SYSTEMIC_LIQUIDITY_STRESS", 0) + fn.get("SYSTEMIC_LIQUIDITY_STRESS", 0)
                sys_fn_rate = (fn.get("SYSTEMIC_LIQUIDITY_STRESS", 0) / ns) if ns else 0.0
            score = (macro_f1 - 2.0 * sys_fn_rate - 1.5 * broad_fp_rate
                     - 1.5 * loc_to_broad)
            valid = (
                macro_f1 > 0 and n_f1_gt0 >= 2 and broad_pred > 0 and locsec_pred > 0
            )
            scored.append({
                "idx": i, "id": _clfid(s, a, b, h), "s": s, "a": a, "b": b, "h": h,
                "score": score, "macro_f1": macro_f1, "f1": f1, "tp": tp, "fp": fp, "fn": fn,
                "broad_pred_episodes": broad_pred, "locsec_pred_episodes": locsec_pred,
                "broad_fp_rate": broad_fp_rate, "sys_fn_rate": sys_fn_rate,
                "primary_f1_gt0": n_f1_gt0, "valid": int(valid),
                "validity_reason": ("OK" if valid else
                                    ("ZERO_MACRO_F1" if macro_f1 <= 0 else
                                     ("FEW_STATE_F1" if n_f1_gt0 < 2 else
                                      ("NO_BROAD_PRED" if broad_pred <= 0 else "NO_LOCSEC_PRED")))),
                "n": len(train), "selected": 0,
            })
        return scored

    def _D2SelectClassifiers(self, scored):
        valid = [r for r in scored if r.get("valid")]
        by_h = {h: [] for h in _HMODE}
        for r in valid:
            by_h[r["h"]].append(r)
        chosen, seen = [], set()
        for h in _HMODE:
            for r in sorted(by_h[h], key=lambda x: (
                -x["score"], -x["f1"].get("BROAD_EQUITY_STRESS", 0),
                -(x["f1"].get("LOCAL_ASSET_STRESS", 0) + x["f1"].get("SECTOR_STRESS", 0)),
                x["broad_fp_rate"], x["s"]
            ))[:2]:
                if r["id"] not in seen:
                    chosen.append(r)
                    seen.add(r["id"])
                    r["selected"] = 1
        modes = {r["h"] for r in chosen}
        return chosen[:6], modes

    def _D2CoverageReport(self):
        td = len(self._d2_train_days) or len({r["do"] for r in self._d2_train})
        # 09:44 + 09:50..15:00 step 5 => 1 + ((900-590)/5 + 1) = 64
        expected = td * 64
        actual = len(self._d2_train)
        cov = (actual / expected) if expected else 0.0
        fin = (self._d2_n_fin / self._d2_n_elig) if self._d2_n_elig else 0.0
        return {
            "expected_train_macro": expected, "actual_train_macro": actual,
            "coverage_ratio": cov, "finalized_ratio": fin,
            "eval_eligible": self._d2_n_elig, "eval_finalized": self._d2_n_fin,
            "held_rows": self._d2_held_rows,
            "held_symbols": sorted(self._d2_asset.keys()),
            "held_days": len(self._d2_held_days),
            "train_days": td, "pending_left": len(self._d2_pending),
            "stale_dropped": self._d2_n_drop, "errors": self._d2_err,
        }
