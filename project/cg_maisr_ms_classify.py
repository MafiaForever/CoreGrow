# cg_maisr_ms_classify.py -- shared MAISR state classifier (pure helper).
# Extracted from cg_maisr_diag to keep main mixin under QC char limits.

_PROXY = {"XLE": None, "XLB": None, "XLV": None, "XLU": None, "DBC": None,
          "MU": None, "NVDA": None, "AVGO": None}
_IDS_ELEV = ("WATCH", "STRESS", "PANIC_SHORT")
_W5 = (0.20, 0.25, 0.20, 0.25, 0.10)
_RESID_THR = {"S1": 0.5, "S2": 0.75, "S3": 1.0}


def ms_classify(feat, cl, thr, amin, bthr, hmode, s, roles, current_risk, ids_state, gold_tk):
    park = roles.get("PARK", ())

    def stressed(tk):
        if tk in park:
            return False
        c = cl.get(tk)
        if c is None:
            return False
        score = sum(w * v for w, v in zip(_W5, c))
        active = sum(1 for v in c if v >= thr)
        return bool(score >= thr and active >= amin)

    def mv(tk):
        f = feat.get(tk)
        return f["mv"] if f else 0.0

    def dd_raw(tk):
        f = feat.get(tk)
        return f["raw"][3] if f else 0.0

    broad = roles.get("BROAD", ())
    spy_str = any(stressed(t) for t in broad)
    breadth = roles.get("BREADTH", ())
    n_b = sum(1 for t in breadth if stressed(t))
    breadth_frac = (n_b / len(breadth)) if breadth else 0.0
    dur = roles.get("DUR", ())
    bond_str = any(stressed(t) for t in dur)
    dur_mv = (sum(mv(t) for t in dur) / len(dur)) if dur else 0.0
    infl = roles.get("INFL", ())
    infl_mv = (sum(mv(t) for t in infl) / len(infl)) if infl else 0.0
    sh_role = roles.get("SH", ())
    gold_str = stressed(gold_tk)
    gold_mv = mv(gold_tk)

    blocks = int(spy_str) + int(breadth_frac >= bthr) + int(bond_str) + int(gold_str)
    if spy_str and blocks >= 3:
        return "SYSTEMIC_LIQUIDITY_STRESS"

    if (spy_str or breadth_frac >= bthr) and bond_str and infl_mv > 0:
        return "RATE_INFLATION_STRESS"

    if spy_str and breadth_frac >= bthr:
        if hmode == "H2":
            ids_now = str(ids_state or "NORMAL")
            sh_ok = bool(sh_role and stressed(sh_role[0]))
            if not (sh_ok or ids_now in _IDS_ELEV):
                return "UNCONFIRMED_NOISE"
        elif hmode == "H1":
            ids_now = str(ids_state or "NORMAL")
            sh_ok = bool(sh_role and stressed(sh_role[0]))
            cross = bool(bond_str or gold_str)
            if not (sh_ok or ids_now in _IDS_ELEV or cross):
                return "UNCONFIRMED_NOISE"
        return "BROAD_EQUITY_STRESS"

    held = set(current_risk or ()) - set(broad)
    if held and breadth_frac < bthr:
        proxies = {_PROXY.get(t, t) for t in held}
        if any(stressed(p) for p in proxies if p):
            return "SECTOR_STRESS"
        res_thr = _RESID_THR.get(s, 0.75)
        resid = 0.0
        for p in proxies:
            if p and p in feat:
                resid = max(resid, dd_raw(p) - dd_raw("SPY"))
        if resid >= res_thr:
            return "LOCAL_ASSET_STRESS"

    if breadth_frac >= bthr:
        return "SECTOR_STRESS"

    xlv_mv, xlu_mv = mv("XLV"), mv("XLU")
    if spy_str and gold_mv > 0 and dur_mv > 0 and (xlv_mv > 0 or xlu_mv > 0):
        return "DEFENSIVE_ROTATION"
    if spy_str:
        return "UNCONFIRMED_NOISE"
    return "NORMAL"
