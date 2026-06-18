# rr_xsector_shadow.py -- Shadow NAV / Risk Control layer for RRX
from AlgorithmImports import *

RRX_IDLE           = "RRX_IDLE"
RRX_ACTIVE         = "RRX_ACTIVE"
RRX_STRONG         = "RRX_STRONG"
RRX_OVERHEATED     = "RRX_OVERHEATED"
RRX_DAMAGED        = "RRX_DAMAGED"
RRX_DEFENSIVE_ONLY = "RRX_DEFENSIVE_ONLY"

def _RRXShadowInit(self) -> None:
    _ov = getattr(self, "_rrx_param_overrides", {})
    def _gb(k, d):
        v = self.get_parameter(k)
        if v is None: v = _ov.get(k)
        return bool(int(v)) if v is not None else bool(d)
    def _gf(k, d):
        v = self.get_parameter(k)
        if v is None: v = _ov.get(k)
        return float(v) if v is not None else float(d)
    self.rrx_stop_enable        = _gb("rrx_stop_enable",        0)
    self.rrx_stop_entry_dd      = float(_gf("rrx_stop_entry_dd", -0.07))
    self.rrx_stop_quar_days     = int(_gf("rrx_stop_quar_days", 20))
    self.rrx_lead_enable        = _gb("rrx_lead_enable",        0)
    self._rrx_stop_nav=1.0;self._rrx_stop_sym=None
    self._rrx_stop_px=self._rrx_stop_entry=0.0
    self._rrx_stop_snav_peak=1.0;self._rrx_stop_maxdd=0.0
    self._rrx_stop_worst_sret=0.0;self._rrx_stop_mstart=1.0
    self._rrx_stop_count=0;self._rrx_stop_qsym=None;self._rrx_stop_qleft=0;self._rrx_stop_qskip=0
    self.rrx_stop_reentry_delay   = int(_gf("rrx_stop_reentry_delay",   2))
    self.rrx_stop_budget_window   = int(_gf("rrx_stop_budget_window",  60))
    self.rrx_stop_budget_max      = int(_gf("rrx_stop_budget_max",      2))
    self.rrx_stop_budget_lock_days= int(_gf("rrx_stop_budget_lock_days",40))
    self.rrx_stop_budget_enable   = _gb("rrx_stop_budget_enable",    1)
    self._rrx_stop_budget_dates   = []
    self._rrx_stop_budget_lock    = 0
    self.rrx_stop_dd_guard_enable = _gb("rrx_stop_dd_guard_enable",  0)
    self.rrx_stop_dd_guard_thr    = float(_gf("rrx_stop_dd_guard_thr",   -0.25))
    self.rrx_stop_dd_recover_thr  = float(_gf("rrx_stop_dd_recover_thr", -0.18))
    self._rrx_stop_dd_blocked     = False
    self.rrx_stop_strong_only     = _gb("rrx_stop_strong_only",     0)
    self.rrx_stop_active_dd_thr   = float(_gf("rrx_stop_active_dd_thr", -0.08))
    self.rrx_stop_sma_gate        = _gb("rrx_stop_sma_gate",        0)
    self.rrx_stop_freshness_days  = int(_gf("rrx_stop_freshness_days",  2))
    self._rrx_stop_gate_days      = {}
    self.rrx_chandelier_enable    = _gb("rrx_chandelier_enable",    0)
    self.rrx_chandelier_mult      = float(_gf("rrx_chandelier_mult",  3.5))
    self._rrx_chan_high           = 0.0
    self.rrx_rotation_hysteresis_enable = _gb("rrx_rotation_hysteresis_enable", 0)
    self.rrx_rotation_confidence  = float(_gf("rrx_rotation_confidence", 0.10))
    self._rrx_stop_prev_r20       = 0.0
    self.rrx_reentry_edge_enable  = _gb("rrx_reentry_edge_enable",   0)
    self._rrx_stop_prev_theme     = None
    self.rrx_vol_size_enable      = _gb("rrx_vol_size_enable",      0)
    self.rrx_vol_target           = float(_gf("rrx_vol_target",     0.35))
    self.rrx_vol_min_size         = float(_gf("rrx_vol_min_size",   0.35))
    self.rrx_vol_max_size         = float(_gf("rrx_vol_max_size",   1.00))
    self.rrx_vol_log_enable       = _gb("rrx_vol_log_enable",    0)
    self.rrx_vol_dd_enable        = _gb("rrx_vol_dd_enable",     0)
    self.rrx_vol_target_tight     = float(_gf("rrx_vol_target_tight", 0.35))
    self.rrx_vol_dd_thr           = float(_gf("rrx_vol_dd_thr",      -0.07))
    self.rrx_d5x_enable           = _gb("rrx_d5x_enable",       0)
    self.rrx_d5y_enable           = _gb("rrx_d5y_enable",       0)
    self.rrx_d5z_enable           = _gb("rrx_d5z_enable",       0)
    self.rrx_d5z_profile          = str(self.get_parameter("rrx_d5z_profile") or _ov.get("rrx_d5z_profile","custom"))
    self.rrx_d5z_snav_dd_thr      = float(_gf("rrx_d5z_snav_dd_thr",    -0.10))
    self.rrx_d5z_stop_guard_days  = int(_gf("rrx_d5z_stop_guard_days",  30))
    self._rrx_d5z_nav=1.0; self._rrx_d5z_pk=1.0; self._rrx_d5z_dd=0.0
    self._rrx_d5z_n35=0; self._rrx_d5z_n40=0; self._rrx_d5z_last_stop_date=None
    self._rrx_d5z_tot35=0; self._rrx_d5z_tot40=0; self._rrx_d5z_ms35=1.0
    self._rrx_dz2_nav=1.0; self._rrx_dz2_pk=1.0; self._rrx_dz2_dd=0.0; self._rrx_dz2_ms=1.0
    self._rrx_dz3_nav=1.0; self._rrx_dz3_pk=1.0; self._rrx_dz3_dd=0.0; self._rrx_dz3_ms=1.0
    self._rrx_d5z_wr=0.0; self._rrx_dz2_wr=0.0; self._rrx_dz3_wr=0.0
    self._rrx_dz2_tot35=0; self._rrx_dz2_tot40=0; self._rrx_dz3_tot35=0; self._rrx_dz3_tot40=0
    for _t in ("35","40","45"):
        setattr(self,f"_rrx_d5x_nav{_t}",1.0); setattr(self,f"_rrx_d5x_pk{_t}",1.0)
        setattr(self,f"_rrx_d5x_dd{_t}",0.0); setattr(self,f"_rrx_d5x_ms{_t}",1.0)
        setattr(self,f"_rrx_d5x_wr{_t}",0.0); setattr(self,f"_rrx_d5x_mpk{_t}",1.0)
        setattr(self,f"_rrx_d5x_mdd{_t}",0.0)
    for _r in ("R1","R2","R3"):
        setattr(self,f"_rrx_d5y_nav{_r}",1.0); setattr(self,f"_rrx_d5y_pk{_r}",1.0)
        setattr(self,f"_rrx_d5y_dd{_r}",0.0); setattr(self,f"_rrx_d5y_ms{_r}",1.0)
        setattr(self,f"_rrx_d5y_wr{_r}",0.0)
        # D5Y counters for target usage (0.35 vs 0.40)
        setattr(self, f"_rrx_d5y_n35_{_r}", 0)
        setattr(self, f"_rrx_d5y_n40_{_r}", 0)
    self._rrx_stop_reentry_wait=0
    self._rrx_stop_re_sym=None;self._rrx_stop_re_type=""
    self._rrx_stop_reI=0;self._rrx_stop_reL=0
    self._rrx_stop_reH=0;self._rrx_stop_reR=0;self._rrx_stop_cluster=0
    self._rrx_sub_stock=None
    self.rrx_attr_enable        = _gb("rrx_attr_enable",        0)
    self._rrx_attr_sym=self._rrx_attr_entry_date=None
    self._rrx_attr_entry_px=self._rrx_attr_mae=self._rrx_attr_mfe=0.0
    self._rrx_attr_theme=""
    self._rrx_attr_hold=0
    self._rrx_attr_stats={}
    self.rrx_meta_epn  = int(_gf("rrx_meta_entry_pn_max",   1))
    self.rrx_meta_eids = int(_gf("rrx_meta_entry_ids_max",  3))
    self._rrx_d1_meta_nav = 1.0
    self._rrx_d1_meta_sym = None
    self._rrx_d1_meta_px  = 0.0
    self._rrx_meta_entry_days = 0
    self._rrx_meta_carry_days = 0
    self._rrx_meta_hard_days  = 0
    self._rrx_meta_flat_days  = 0
    self._rrx_meta_pos_chg    = 0
    self._rrx_meta_mnav_peak   = 1.0
    self._rrx_meta_mnav_maxdd  = 0.0
    self._rrx_meta_mnav_mstart = 1.0
    self._rrx_meta_worst_mret  = 0.0


def _RRXSmaGateLayers(self, sym):
    try:
        th=self._RRXThemeOfStock(sym) or self._rrx_top_theme or ""
        etf=self._rrx_etf_sym.get(th)
        g=lambda i:float((getattr(getattr(i,"Current",None),"Value",0)or 0))if i else 0.0
        px=float(self.securities[sym].price)
        s10=g(self._rrx_stk_sma10.get(sym)); s20=g(self._rrx_stk_sma20.get(sym)); s50=g(self._rrx_stk_sma50.get(sym))
        fast=s10>0 and px>s10
        r20=g(self._rrx_stk_roc20.get(sym)); spy20=g(self._rrx_spy_roc20); etf20=g(self._rrx_etf_roc20.get(etf))
        trend=s20>0 and px>s20 and r20>0 and r20>spy20 and r20>etf20
        e50=g(self._rrx_etf_sma50.get(etf))
        try: epx=float(self.securities[etf].price) if etf else 0.0
        except Exception: epx=0.0
        struct=s50>0 and px>s50 and s20>s50 and e50>0 and epx>e50
        return fast, trend, struct
    except Exception: return False, False, False

def _RRXSmaGateOk(self, sym, act="") -> bool:
    try:
        fast,trend,struct=self._RRXSmaGateLayers(sym)
        ok=trend and struct  # D2C2: FAST not required
        if not ok and act:
            try: sv=str(sym.Value)
            except Exception: sv=str(sym)
            self.log(f"RRX_SMA_GATE,{self.time.date()},sym={sv},act={act},"
                     f"fast={int(fast)},trend={int(trend)},struct={int(struct)},"
                     f"snav={self._rrx_stop_nav:.4f}")
        return ok
    except Exception as e:
        if getattr(self,"rrx_meta_debug_log",False):
            self.log(f"RRX_SMA_GATE_ERR,{self.time.date()},{e}")
        return False

def _RRXChooseReentry(self, md, local_alt, inter_alt, no_local=False):
    ql=int(getattr(self,"_rrx_stop_qleft",0))
    qs=getattr(self,"_rrx_stop_qsym",None) if ql>0 else None
    def ok(s): return s is not None and (qs is None or s!=qs)
    def hyster(s):
        if not getattr(self,"rrx_rotation_hysteresis_enable",False) or ql<=0: return True
        try:
            _g=lambda i:float((getattr(getattr(i,"Current",None),"Value",0)or 0))if i else 0.0
            nr=_g(self._rrx_stk_roc20.get(s)); pr=self._rrx_stop_prev_r20
            return nr>max(pr*(1+self.rrx_rotation_confidence),0.0)
        except Exception: return True
    def same_th(s):
        if not getattr(self,"rrx_reentry_edge_enable",False) or ql<=0: return False
        pt=getattr(self,"_rrx_stop_prev_theme",None)
        if pt is None: return False
        return self._RRXThemeOfStock(s)==pt
    def _log_hyst(s,act,why):
        try: sv=str(s.Value)
        except Exception: sv=str(s)
        self.log(f"RRX_HYST,{self.time.date()},sym={sv},act={act},why={why},ql={ql}")
    if ok(md) and hyster(md) and not same_th(md): return md,"META"
    if ok(md) and (not hyster(md) or same_th(md)): _log_hyst(md,"BLOCK_MD","hyst" if not hyster(md) else "same_th")
    if ok(inter_alt) and self._RRXLeadPass(inter_alt) and hyster(inter_alt) and not same_th(inter_alt): return inter_alt,"INTER"
    if ok(inter_alt) and self._RRXLeadPass(inter_alt) and (not hyster(inter_alt) or same_th(inter_alt)): _log_hyst(inter_alt,"BLOCK_INTER","hyst" if not hyster(inter_alt) else "same_th")
    if not no_local and ok(local_alt) and self._RRXLeadPass(local_alt) and hyster(local_alt) and not same_th(local_alt): return local_alt,"LOCAL"
    return None,"CASH"


def _RRXShadowDailyRet(self, sym, sym_attr: str, px_attr: str) -> float:
    try:
        if sym is None:
            setattr(self, sym_attr, None)
            setattr(self, px_attr, 0.0)
            return 0.0
        px      = float(self.securities[sym].price)
        prev_sym = getattr(self, sym_attr, None)
        prev_px  = float(getattr(self, px_attr, 0.0))
        if prev_sym != sym or prev_px <= 0:
            setattr(self, sym_attr, sym)
            setattr(self, px_attr, px)
            return 0.0              # first day in position: no return yet
        setattr(self, px_attr, px)
        return float(px / prev_px - 1.0)
    except Exception:
        return 0.0


def _RRXShadowExecRet(self, desired_sym, sym_attr: str, px_attr: str) -> float:
    try:
        prev_sym = getattr(self, sym_attr, None)
        prev_px  = float(getattr(self, px_attr, 0.0))
        ret = 0.0
        if prev_sym is not None and prev_px > 0:
            ret = float(self.securities[prev_sym].price) / prev_px - 1.0
        if desired_sym is None:
            setattr(self, sym_attr, None)
            setattr(self, px_attr, 0.0)
            return float(ret)
        setattr(self, sym_attr, desired_sym)
        setattr(self, px_attr, float(self.securities[desired_sym].price))
        return float(ret)
    except Exception:
        return 0.0


def _RRXMetaUpdateRollingStress(self) -> None:
    try:
        tb       = str(getattr(self, "_rrx_tblock",    "") or "").lower()
        ps       = str(getattr(self, "_panic_state", "NORMAL") or "NORMAL").upper()
        ids_st   = str(getattr(self, "_ids_state",   "NORMAL") or "NORMAL").upper()
        lb       = int(getattr(self, "rrx_meta_turn_lb", 20))
        rp       = getattr(self, "_rrx_meta_roll_panic", [])
        ri       = getattr(self, "_rrx_meta_roll_ids",   [])
        pn  = 1 if (ps  in ("STRESS","PANIC") or "panic" in tb) else 0
        ids = 1 if (ids_st in ("WATCH","STRESS","PANIC","PANIC_SHORT") or "ids" in tb) else 0
        rp.append(pn); ri.append(ids)
        if len(rp) > lb: rp.pop(0)
        if len(ri) > lb: ri.pop(0)
        self._rrx_meta_roll_panic   = rp
        self._rrx_meta_roll_ids     = ri
        self._rrx_meta_rpn          = int(sum(rp))
        self._rrx_meta_rids         = int(sum(ri))
        self._rrx_meta_roll_pn_cnt  = self._rrx_meta_rpn
        self._rrx_meta_roll_ids_cnt = self._rrx_meta_rids
        tage = getattr(self, "_rrx_meta_theme_age", 0)
        atm  = getattr(self, "_rrx_meta_age_theme", None)
        if self._rrx_top_theme == atm:
            tage += 1
        else:
            atm  = self._rrx_top_theme
            tage = 1 if self._rrx_top_theme else 0
        self._rrx_meta_theme_age = tage
        self._rrx_meta_age_theme = atm
    except Exception:
        self._rrx_meta_rpn = self._rrx_meta_rids = 0
        self._rrx_meta_roll_pn_cnt = self._rrx_meta_roll_ids_cnt = 0


def _RRXAttrUpdate(self, pv, cv, ch, rpn, rids, rth, rld, rg, reg) -> None:
    if not getattr(self, "rrx_attr_enable", False): return
    def _s(s):
        try: return str(s.Value) if s else "NONE"
        except Exception: return str(s or "NONE")
    if pv is not None and cv != pv:
        try: ep=self._rrx_attr_entry_px; xp=float(self.securities[pv].price); tr=xp/ep-1.0 if ep>0 else 0.0
        except Exception: tr=0.0
        if tr<self._rrx_attr_mae: self._rrx_attr_mae=tr
        if tr>self._rrx_attr_mfe: self._rrx_attr_mfe=tr
        xr="hard" if ch else ("rot" if cv else "ns")
        ss=_s(pv); st=self._rrx_attr_stats.get(ss)
        if st is None: st=[0,0,0.0,tr,0.0]; self._rrx_attr_stats[ss]=st
        st[0]+=1; st[1]+=int(tr>0); st[2]+=tr
        if tr<st[3]: st[3]=tr
        if self._rrx_attr_mae<st[4]: st[4]=self._rrx_attr_mae

    if cv is not None and cv!=pv:
        self._rrx_attr_sym=cv; self._rrx_attr_entry_date=self.time.date()
        try: self._rrx_attr_entry_px=float(self.securities[cv].price)
        except Exception: self._rrx_attr_entry_px=0.0
        self._rrx_attr_theme=self._rrx_top_theme or ""; self._rrx_attr_rg=rg
        self._rrx_attr_rrxst=str(self._rrx_state); self._rrx_attr_reg=reg
        self._rrx_attr_rpn=rpn; self._rrx_attr_rids=rids; self._rrx_attr_rth=rth; self._rrx_attr_rld=rld
        self._rrx_attr_mae=self._rrx_attr_mfe=0.0; self._rrx_attr_hold=0
    elif cv is None: self._rrx_attr_sym=None
    if self._rrx_attr_sym is not None:
        try:
            cp=float(self.securities[self._rrx_attr_sym].price); ep=self._rrx_attr_entry_px
            if ep>0:
                r=cp/ep-1.0
                if r<self._rrx_attr_mae: self._rrx_attr_mae=r
                if r>self._rrx_attr_mfe: self._rrx_attr_mfe=r
        except Exception: pass
        self._rrx_attr_hold+=1


def _RRXThemeOfStock(self, sym):
    try:
        for th, arr in self._rrx_stk_sym.items():
            if sym in arr: return th
    except Exception: pass
    return None

def _RRXLeadPass(self, sym, theme=None) -> bool:
    try:
        th = theme or self._RRXThemeOfStock(sym) or self._rrx_top_theme
        e = self._rrx_etf_sym.get(th or "")
        g = lambda i: float((getattr(getattr(i,"Current",None),"Value",0)or 0))if i else 0.0
        s = g(self._rrx_stk_sma50.get(sym)); p = float(self.securities[sym].price)
        return bool((g(self._rrx_stk_roc20.get(sym))-g(self._rrx_etf_roc20.get(e)))>0
                    and s>0 and p>s and(g(self._rrx_etf_rsi14.get(e))or 50)<=75)
    except Exception: return False


def _RRXVolSizeForTarget(self,sym,target:float)->float:
    try:
        atr_i=self._rrx_stk_atr20.get(sym)
        if not atr_i or not atr_i.IsReady: return 1.0
        px=float(self.securities[sym].price)
        if px<=0 or float(atr_i.Current.Value)<=0: return 1.0
        v=float(atr_i.Current.Value)/px*(252**0.5)
        return min(max(float(target)/v,self.rrx_vol_min_size),self.rrx_vol_max_size)
    except Exception: return 1.0

def _RRXSizedReturnForTarget(self,sym,raw_ret:float,cash_ret:float,target:float)->float:
    sz=self._RRXVolSizeForTarget(sym,target)
    return sz*raw_ret+(1.0-sz)*cash_ret if sz<1.0 else raw_ret

def _RRXD5XUpdateRisk(self,tk:str,nav:float)->None:
    pk=getattr(self,f"_rrx_d5x_pk{tk}",1.0)
    if nav>pk: setattr(self,f"_rrx_d5x_pk{tk}",nav)
    elif pk>0:
        dd=nav/pk-1.0
        if dd<getattr(self,f"_rrx_d5x_dd{tk}",0.0): setattr(self,f"_rrx_d5x_dd{tk}",dd)
    mpk=getattr(self,f"_rrx_d5x_mpk{tk}",1.0)
    if nav>mpk: setattr(self,f"_rrx_d5x_mpk{tk}",nav)
    elif mpk>0:
        mdd=nav/mpk-1.0
        if mdd<getattr(self,f"_rrx_d5x_mdd{tk}",0.0): setattr(self,f"_rrx_d5x_mdd{tk}",mdd)

def _RRXD5XApplySymbolReturn(self,sym,raw_ret:float,cash_ret:float)->None:
    if not getattr(self,"rrx_d5x_enable",False): return
    for tgt,tk in ((0.35,"35"),(0.40,"40"),(0.45,"45")):
        nav=getattr(self,f"_rrx_d5x_nav{tk}",1.0)*(1.0+self._RRXSizedReturnForTarget(sym,raw_ret,cash_ret,tgt))
        setattr(self,f"_rrx_d5x_nav{tk}",nav); self._RRXD5XUpdateRisk(tk,nav)

def _RRXD5XApplyCashReturn(self,cash_ret:float)->None:
    if not getattr(self,"rrx_d5x_enable",False): return
    for tk in ("35","40","45"):
        nav=getattr(self,f"_rrx_d5x_nav{tk}",1.0)*(1.0+cash_ret)
        setattr(self,f"_rrx_d5x_nav{tk}",nav); self._RRXD5XUpdateRisk(tk,nav)

def _RRXD5YTarget(self,rule:int)->float: return 0.35
def _RRXD5YApplyReturn(self,sym,raw_ret:float,cash_ret:float)->None: pass
def _RRXD5YApplyCash(self,cash_ret:float)->None: pass

def _RRXD5ZTarget(self) -> float:
    try:
        if int(getattr(self,"_rrx_stop_qleft",0))>0: return 0.35
        strong=str(getattr(self,"_rrx_state",""))=="RRX_STRONG"
        cluster=int(getattr(self,"_rrx_stop_cluster",0))
        pk=float(getattr(self,"_rrx_stop_snav_peak",1.0))
        sn=float(getattr(self,"_rrx_stop_nav",1.0))
        snav_dd=sn/pk-1.0 if pk>0 else 0.0
        ls=getattr(self,"_rrx_d5z_last_stop_date",None)
        gd=int(getattr(self,"rrx_d5z_stop_guard_days",30))
        if ls is not None and (self.time.date()-ls).days<gd: return 0.35
        thr=float(getattr(self,"rrx_d5z_snav_dd_thr",-0.10))
        return 0.40 if (strong and cluster==0 and snav_dd>thr) else 0.35
    except Exception: return 0.35

def _RRXD5ZApplyReturn(self,sym,raw_ret:float,cash_ret:float)->None:
    if not getattr(self,"rrx_d5z_enable",False): return
    tgt=self._RRXD5ZTarget()
    if abs(tgt-0.40)<1e-9: self._rrx_d5z_n40=getattr(self,"_rrx_d5z_n40",0)+1; self._rrx_d5z_tot40=getattr(self,"_rrx_d5z_tot40",0)+1
    else: self._rrx_d5z_n35=getattr(self,"_rrx_d5z_n35",0)+1; self._rrx_d5z_tot35=getattr(self,"_rrx_d5z_tot35",0)+1
    try: _pk0=float(self._rrx_stop_snav_peak); _sn0=float(self._rrx_stop_nav); _sdd=_sn0/_pk0-1.0 if _pk0>0 else 0.0
    except Exception: _sdd=-1.0
    t2=0.40 if tgt==0.40 and _sdd>-0.07 else 0.35
    t3=0.40 if tgt==0.40 and _sdd>-0.05 else 0.35
    if t2==0.40: self._rrx_dz2_tot40=getattr(self,"_rrx_dz2_tot40",0)+1
    else: self._rrx_dz2_tot35=getattr(self,"_rrx_dz2_tot35",0)+1
    if t3==0.40: self._rrx_dz3_tot40=getattr(self,"_rrx_dz3_tot40",0)+1
    else: self._rrx_dz3_tot35=getattr(self,"_rrx_dz3_tot35",0)+1
    for _tgt,_na,_pa,_da in ((tgt,"_rrx_d5z_nav","_rrx_d5z_pk","_rrx_d5z_dd"),(t2,"_rrx_dz2_nav","_rrx_dz2_pk","_rrx_dz2_dd"),(t3,"_rrx_dz3_nav","_rrx_dz3_pk","_rrx_dz3_dd")):
        _n=getattr(self,_na,1.0)*(1.0+self._RRXSizedReturnForTarget(sym,raw_ret,cash_ret,_tgt))
        setattr(self,_na,_n); _pk=getattr(self,_pa,1.0)
        if _n>_pk: setattr(self,_pa,_n)
        elif _pk>0:
            _dd=_n/_pk-1.0
            if _dd<getattr(self,_da,0.0): setattr(self,_da,_dd)

def _RRXD5ZApplyCash(self,cash_ret:float)->None:
    if not getattr(self,"rrx_d5z_enable",False): return
    for _na,_pa,_da in (("_rrx_d5z_nav","_rrx_d5z_pk","_rrx_d5z_dd"),("_rrx_dz2_nav","_rrx_dz2_pk","_rrx_dz2_dd"),("_rrx_dz3_nav","_rrx_dz3_pk","_rrx_dz3_dd")):
        _n=getattr(self,_na,1.0)*(1.0+cash_ret); setattr(self,_na,_n)
        _pk=getattr(self,_pa,1.0)
        if _n>_pk: setattr(self,_pa,_n)
        elif _pk>0:
            _dd=_n/_pk-1.0
            if _dd<getattr(self,_da,0.0): setattr(self,_da,_dd)

def _RRXVolSize(self,sym)->float:
    try:
        target=self.rrx_vol_target
        if getattr(self,"rrx_vol_dd_enable",False):
            pk=self._rrx_stop_snav_peak
            if pk>0 and self._rrx_stop_nav/pk-1.0<=self.rrx_vol_dd_thr:
                target=self.rrx_vol_target_tight
        return self._RRXVolSizeForTarget(sym,target)
    except Exception: return 1.0

def _RRXStopUpdate(self, md, cr, alt=None, inter=None) -> None:
    try:
        ss=self._rrx_stop_sym; sp=self._rrx_stop_px; se=self._rrx_stop_entry; stp=False
        if ss and sp>0 and getattr(self,"rrx_stop_sma_gate",False):
            try:
                sv_ps=str(ss.Value)
            except Exception: sv_ps=str(ss)
            fp,tp,sp2=self._RRXSmaGateLayers(ss)
            self.log(f"RRX_SMA_PRESTOP,{self.time.date()},ss={sv_ps},"
                     f"fast={int(fp)},trend={int(tp)},struct={int(sp2)},"
                     f"snav={self._rrx_stop_nav:.4f}")
        if ss and sp>0:
            cp=float(self.securities[ss].price); dr=cp/sp-1.0; re=cp/se-1.0 if se>0 else 0.0
            sz=self._RRXVolSize(ss) if getattr(self,"rrx_vol_size_enable",False) else 1.0
            dr_eff=sz*dr+(1.0-sz)*cr if sz<1.0 else dr
            self._rrx_stop_nav*=(1.0+dr_eff)
            self._RRXD5XApplySymbolReturn(ss,dr,cr)
            self._RRXD5YApplyReturn(ss,dr,cr); self._RRXD5ZApplyReturn(ss,dr,cr)
            if getattr(self,"rrx_vol_log_enable",False) and sz<0.999:
                try: sv_v=str(ss.Value)
                except Exception: sv_v=str(ss)
                self.log(f"RRX_VOL,{self.time.date()},sym={sv_v},sz={sz:.2f},dr={dr:+.4f},eff={dr_eff:+.4f},snav={self._rrx_stop_nav:.4f}")
            # D3A: Chandelier trailing exit
            if not stp and getattr(self,"rrx_chandelier_enable",False):
                if cp>(self._rrx_chan_high or 0): self._rrx_chan_high=cp
                atr_i=self._rrx_stk_atr20.get(ss)
                atr_v=float(atr_i.Current.Value) if(atr_i and atr_i.IsReady)else 0.0
                if atr_v>0 and self._rrx_chan_high>0 and cp<(self._rrx_chan_high-atr_v*self.rrx_chandelier_mult):
                    stp=True; self._rrx_stop_count+=1; self._rrx_stop_qsym=ss
                    self._rrx_stop_qleft=self.rrx_stop_quar_days
                    self._rrx_stop_cluster=getattr(self,"_rrx_stop_cluster",0)+1
                    self._rrx_d5z_last_stop_date=self.time.date()
                    self._rrx_stop_reentry_wait=getattr(self,"rrx_stop_reentry_delay",2)
                    self._rrx_stop_budget_dates.append(self.time.date())
                    try:
                        _ri=self._rrx_stk_roc20.get(ss)
                        self._rrx_stop_prev_r20=float(_ri.Current.Value) if(_ri and _ri.IsReady)else 0.0
                    except Exception: self._rrx_stop_prev_r20=0.0
                    self._rrx_stop_prev_theme=self._RRXThemeOfStock(ss)
                    try: sv=str(ss.Value)
                    except Exception: sv=str(ss)
                    self.log(f"RRX_CHAND_EXIT,{self.time.date()},sym={sv},"
                             f"px={cp:.2f},high={self._rrx_chan_high:.2f},"
                             f"atr={atr_v:.2f},mult={self.rrx_chandelier_mult},"
                             f"stop={self._rrx_chan_high-atr_v*self.rrx_chandelier_mult:.2f},"
                             f"ret={re:+.4f},snav={self._rrx_stop_nav:.4f}")
                    self._rrx_stop_sym=None; self._rrx_stop_px=self._rrx_stop_entry=0.0
                    self._rrx_stop_re_sym=None; self._rrx_stop_re_type=""; self._rrx_chan_high=0.0
            if not stp and re<=self.rrx_stop_entry_dd:
                stp=True; self._rrx_stop_count+=1; self._rrx_stop_qsym=ss
                self._rrx_stop_qleft=self.rrx_stop_quar_days
                try: sv=str(ss.Value)
                except Exception: sv=str(ss)
                self.log(f"RRX_STOP,{self.time.date()},sym={sv},ret={re:+.4f},rsn=entry,snav={self._rrx_stop_nav:.4f}")
                self._rrx_stop_sym=None; self._rrx_stop_px=self._rrx_stop_entry=0.0
                self._rrx_stop_re_sym=None; self._rrx_stop_re_type=""
                self._rrx_stop_cluster=getattr(self,"_rrx_stop_cluster",0)+1
                self._rrx_d5z_last_stop_date=self.time.date()
                self._rrx_stop_reentry_wait=getattr(self,"rrx_stop_reentry_delay",2)
                self._rrx_stop_budget_dates.append(self.time.date())
                try:
                    _ri=self._rrx_stk_roc20.get(ss)
                    self._rrx_stop_prev_r20=float(_ri.Current.Value) if(_ri and _ri.IsReady)else 0.0
                except Exception: self._rrx_stop_prev_r20=0.0
                self._rrx_stop_prev_theme=self._RRXThemeOfStock(ss)
            elif not stp:
                self._rrx_stop_px=cp
        if not stp:

            q=self._rrx_stop_qsym; ql=int(getattr(self,"_rrx_stop_qleft",0))
            # D2B: limit shadow exposure by RRX state quality
            if getattr(self,"rrx_stop_strong_only",False):
                rrx_st=getattr(self,"_rrx_state",RRX_IDLE)
                if rrx_st!=RRX_STRONG: md=None  # force cash when not STRONG
            elif getattr(self,"rrx_stop_active_dd_thr",0.0)<0:
                rrx_st=getattr(self,"_rrx_state",RRX_IDLE)
                pk=self._rrx_stop_snav_peak
                cur_dd=self._rrx_stop_nav/pk-1.0 if pk>0 else 0.0
                if rrx_st==RRX_ACTIVE and cur_dd<=self.rrx_stop_active_dd_thr: md=None
            if getattr(self,"rrx_stop_sma_gate",False):
                gd=self._rrx_stop_gate_days; fresh=getattr(self,"rrx_stop_freshness_days",2)
                # Daily held-symbol audit log
                if ss is not None:
                    try: sv_h=str(ss.Value)
                    except Exception: sv_h=str(ss)
                    f_h,t_h,s_h=self._RRXSmaGateLayers(ss)
                    act_h="CASH" if not(t_h and s_h) else "HOLD"
                    self.log(f"RRX_SMA_HELD,{self.time.date()},ss={sv_h},"
                             f"fast={int(f_h)},trend={int(t_h)},struct={int(s_h)},"
                             f"act={act_h},snav={self._rrx_stop_nav:.4f}")
                # Carry gate: SMA only, no freshness
                if ss is not None and not self._RRXSmaGateOk(ss,act="CARRY_CASH"):
                    self._rrx_stop_sym=None; self._rrx_stop_px=self._rrx_stop_entry=0.0
                    self._rrx_stop_re_sym=None; self._rrx_stop_re_type=""; ss=None
                # Update gate_days + entry gate: SMA AND freshness
                seen={}
                def _eg(s,a=""):
                    if s is None: return True
                    if s in seen:
                        ok,prev,f,t,sl=seen[s]
                    else:
                        f,t,sl=self._RRXSmaGateLayers(s); ok=t and sl
                        prev=gd.get(s,0); gd[s]=prev+1 if ok else 0
                        seen[s]=(ok,prev,f,t,sl)
                    if not ok or prev<fresh:
                        try: sv=str(s.Value)
                        except Exception: sv=str(s)
                        self.log(f"RRX_SMA_GATE,{self.time.date()},sym={sv},act={a},"
                                 f"fast={int(f)},trend={int(t)},struct={int(sl)},"
                                 f"days={prev},fresh={fresh},snav={self._rrx_stop_nav:.4f}")
                        return False
                    return True
                if not _eg(md,"BLOCK_MD"): md=None
                if not _eg(inter,"BLOCK_INTER"): inter=None
                if not _eg(alt,"BLOCK_ALT"): alt=None
            blocked=(md is not None and q is not None and ql>0 and md==q)
            ps=str(getattr(self,"_panic_state","NORMAL")).upper()
            ids_st=str(getattr(self,"_ids_state","NORMAL")).upper()
            hard=ps in("STRESS","PANIC") or ids_st in("STRESS","PANIC","PANIC_SHORT")
            re_sym=getattr(self,"_rrx_stop_re_sym",None)
            cluster=getattr(self,"_rrx_stop_cluster",0)
            wait=getattr(self,"_rrx_stop_reentry_wait",0)
            if wait>0:
                self._rrx_stop_reentry_wait=wait-1
                use=ss; rtyp="WAIT"  # hold current (or cash if ss None)
                if ss is None: self._rrx_stop_nav*=(1.0+cr); self._RRXD5XApplyCashReturn(cr); self._RRXD5YApplyCash(cr); self._RRXD5ZApplyCash(cr)
                if self._rrx_stop_qleft>0:
                    self._rrx_stop_qleft-=1
                    if self._rrx_stop_qleft==0:
                        self._rrx_stop_qsym=None; self._rrx_stop_re_sym=None
                        self._rrx_stop_re_type=""; self._rrx_stop_cluster=0
                        self._rrx_stop_reentry_wait=0
            else:
                # D1G: rolling stop budget
                if getattr(self,"rrx_stop_budget_enable",True):
                  today=self.time.date(); cal_window=self.rrx_stop_budget_window*2
                  self._rrx_stop_budget_dates=[d for d in self._rrx_stop_budget_dates if(today-d).days<=cal_window]
                  if len(self._rrx_stop_budget_dates)>=self.rrx_stop_budget_max and self._rrx_stop_budget_lock<=0:
                    self._rrx_stop_budget_lock=self.rrx_stop_budget_lock_days
                    self._rrx_stop_re_sym=None; self._rrx_stop_re_type=""
                    self.log(f"RRX_BUDGET_LOCK,{self.time.date()},stops60={len(self._rrx_stop_budget_dates)},bl={self._rrx_stop_budget_lock}")
                if self._rrx_stop_budget_lock>0:
                    self._rrx_stop_budget_lock-=1
                    use=None; rtyp="BUDGET_CASH"
                    if ss is None: self._rrx_stop_nav*=(1.0+cr); self._RRXD5XApplyCashReturn(cr); self._RRXD5YApplyCash(cr); self._RRXD5ZApplyCash(cr)
                    self.log(f"RRX_BUDGET_CASH,{self.time.date()},bl={self._rrx_stop_budget_lock+1},snav={self._rrx_stop_nav:.4f}")
                    if self._rrx_stop_qleft>0:
                        self._rrx_stop_qleft-=1
                        if self._rrx_stop_qleft==0:
                            self._rrx_stop_qsym=None; self._rrx_stop_re_sym=None
                            self._rrx_stop_re_type=""; self._rrx_stop_cluster=0; self._rrx_stop_reentry_wait=0; self._rrx_stop_prev_r20=0.0; self._rrx_stop_prev_theme=None
                else:
                    # D2: equity curve guard
                    if getattr(self,"rrx_stop_dd_guard_enable",False):
                        pk=self._rrx_stop_snav_peak; sn_now=self._rrx_stop_nav
                        cur_dd=sn_now/pk-1.0 if pk>0 else 0.0
                        if cur_dd<=self.rrx_stop_dd_guard_thr: self._rrx_stop_dd_blocked=True
                        if self._rrx_stop_dd_blocked and cur_dd>self.rrx_stop_dd_recover_thr: self._rrx_stop_dd_blocked=False
                    if getattr(self,"_rrx_stop_dd_blocked",False):
                        use=None; rtyp="DD_GUARD"
                        if ss is None: self._rrx_stop_nav*=(1.0+cr); self._RRXD5XApplyCashReturn(cr); self._RRXD5YApplyCash(cr); self._RRXD5ZApplyCash(cr)
                        if self._rrx_stop_qleft>0:
                            self._rrx_stop_qleft-=1
                            if self._rrx_stop_qleft==0:
                                self._rrx_stop_qsym=None; self._rrx_stop_re_sym=None
                                self._rrx_stop_re_type=""; self._rrx_stop_cluster=0; self._rrx_stop_reentry_wait=0; self._rrx_stop_prev_r20=0.0; self._rrx_stop_prev_theme=None
                    elif hard:
                        use=None; rtyp="HARD_CASH"
                        self._rrx_stop_re_sym=None; self._rrx_stop_re_type=""
                    elif ql>0 and ss is not None and re_sym is not None and ss==re_sym:
                        use=ss; rtyp="LOCK"; self._rrx_stop_reH+=1
                    elif ql>0 and cluster>=2:
                        use=None; rtyp="CASH"
                    elif blocked:
                        self._rrx_stop_qskip+=1
                        use,rtyp=self._RRXChooseReentry(None,None,inter,no_local=True)
                    else:
                        use,rtyp=self._RRXChooseReentry(md,None,inter,no_local=(cluster>0))
                    def _enter(sym):
                        try: ep=float(self.securities[sym].price)
                        except Exception: ep=0.0
                        self._rrx_stop_sym=sym; self._rrx_stop_px=ep; self._rrx_stop_entry=ep
                        self._rrx_chan_high=ep  # D3A: reset chandelier trailing high
                        if ql>0:
                            if getattr(self,"_rrx_stop_re_sym",None) is None:
                                self._rrx_stop_re_sym=sym; self._rrx_stop_re_type=rtyp
                                if rtyp=="INTER": self._rrx_stop_reI+=1
                                elif rtyp=="LOCAL": self._rrx_stop_reL+=1
                            try: sv=str(sym.Value)
                            except Exception: sv=str(sym)
                            try: pv=str(ss.Value) if ss else "NONE"
                            except Exception: pv=str(ss or "NONE")
                            self.log(f"RRX_STOP_RE,{self.time.date()},sym={sv},type={rtyp},ql={ql},prev={pv},hard={int(hard)},lock={int(rtyp=='LOCK')},snav={self._rrx_stop_nav:.4f}")
                    if ss is None:
                        if use is None: self._rrx_stop_nav*=(1.0+cr); self._RRXD5XApplyCashReturn(cr); self._RRXD5YApplyCash(cr); self._RRXD5ZApplyCash(cr)
                        else: _enter(use)
                    elif use is None:
                        self._rrx_stop_sym=None; self._rrx_stop_px=self._rrx_stop_entry=0.0
                    elif use!=ss:
                        _enter(use)
                    if self._rrx_stop_qleft>0:
                        self._rrx_stop_qleft-=1
                        if self._rrx_stop_qleft==0:
                            self._rrx_stop_qsym=None
                            self._rrx_stop_re_sym=None; self._rrx_stop_re_type=""
                            self._rrx_stop_cluster=0; self._rrx_stop_reentry_wait=0; self._rrx_stop_prev_r20=0.0; self._rrx_stop_prev_theme=None
    except Exception as e:
        if getattr(self,"rrx_meta_debug_log",False):
            self.log(f"RRX_STOP_ERR,{self.time.date()},{e}")
    sn=self._rrx_stop_nav
    if sn>self._rrx_stop_snav_peak: self._rrx_stop_snav_peak=sn
    if self._rrx_stop_snav_peak>0:
        dd=sn/self._rrx_stop_snav_peak-1.0
        if dd<self._rrx_stop_maxdd: self._rrx_stop_maxdd=dd


def _RRXShadowUpdate(self) -> dict:
    try:
        rr          = getattr(self, "_rr", None)
        native_sym  = getattr(rr, "held_symbol", None) if rr else None
        usfr_sym    = getattr(rr, "usfr",        None) if rr else None
        raw_sym     = (self._rrx_top_stock
                       if self._rrx_state == RRX_STRONG and self._rrx_top_stock
                       else None)
        tradable_sym = (self._rrx_top_stock
                        if getattr(self, "_rrx_tradable", 0) == 1 and self._rrx_top_stock
                        else None)

        native_ret   = self._RRXShadowDailyRet(native_sym,   "_rrx_d1_native_sym",   "_rrx_d1_native_px")
        tradable_ret = self._RRXShadowDailyRet(tradable_sym, "_rrx_d1_tradable_sym", "_rrx_d1_tradable_px")
        raw_ret      = self._RRXShadowDailyRet(raw_sym,      "_rrx_d1_raw_sym",      "_rrx_d1_raw_px")
        cash_ret     = self._RRXShadowDailyRet(usfr_sym,     "_rrx_d1_cash_sym",     "_rrx_d1_cash_px")

        self._rrx_d1_native_nav   *= (1.0 + native_ret)
        self._rrx_d1_tradable_nav *= (1.0 + tradable_ret)
        self._rrx_d1_raw_nav      *= (1.0 + raw_ret)
        self._rrx_d1_cash_nav     *= (1.0 + cash_ret)
        talloc_ret = tradable_ret if tradable_sym is not None else cash_ret
        self._rrx_d1_talloc_nav   *= (1.0 + talloc_ret)

        lag_tradable_sym = getattr(self, "_rrx_d1_next_tradable_sym", None)
        lag_raw_sym      = getattr(self, "_rrx_d1_next_raw_sym",      None)
        lag_tret = self._RRXShadowDailyRet(
            lag_tradable_sym, "_rrx_d1_lag_tradable_sym", "_rrx_d1_lag_tradable_px")
        lag_rret = self._RRXShadowDailyRet(
            lag_raw_sym, "_rrx_d1_lag_raw_sym", "_rrx_d1_lag_raw_px")
        lag_talloc_ret = lag_tret if lag_tradable_sym is not None else cash_ret
        self._rrx_d1_lag_tradable_nav *= (1.0 + lag_talloc_ret)
        self._rrx_d1_lag_raw_nav      *= (1.0 + lag_rret)
        # Store today's signal for tomorrow's lagged position
        self._rrx_d1_next_tradable_sym = tradable_sym
        self._rrx_d1_next_raw_sym      = raw_sym
        prev_exec_sym = getattr(self, "_rrx_d1_exec_sym", None)
        exec_ret = self._RRXShadowExecRet(
            tradable_sym, "_rrx_d1_exec_sym", "_rrx_d1_exec_px")
        self._rrx_d1_exec_nav *= (1.0 + exec_ret)
        flat = (prev_exec_sym is None and tradable_sym is None)
        exec_alloc_ret = cash_ret if flat else exec_ret
        self._rrx_d1_exec_alloc_nav *= (1.0 + exec_alloc_ret)

        # Meta NAV: entry/carry/hard filter applied daily
        if getattr(self, "rrx_meta_enable", False):
            self._RRXMetaUpdateRollingStress()
            rpn  = int(getattr(self, "_rrx_meta_rpn",  0))
            rids = int(getattr(self, "_rrx_meta_rids", 0))
            _ids = str(getattr(self, "_ids_state",   "NORMAL")).upper()
            _ps  = str(getattr(self, "_panic_state", "NORMAL")).upper()
            _reg = str(getattr(self, "current_regime", "NA"))
            hard = (_ps in ("STRESS","PANIC") or _ids in ("STRESS","PANIC","PANIC_SHORT"))
            prev_meta = getattr(self, "_rrx_d1_meta_sym", None)
            if hard:
                meta_desired = None
            elif tradable_sym is not None:
                meta_desired = tradable_sym
            elif prev_meta is not None and _reg != "RISK_OFF":
                meta_desired = prev_meta
            else:
                meta_desired = None
            if getattr(self, "rrx_meta_turnover_enable", False):
                lb=self.rrx_meta_turn_lb
                rt=getattr(self,"_rrx_meta_roll_themes",[]);rl=getattr(self,"_rrx_meta_roll_leaders",[])
                try: cur_ldr=str(self._rrx_top_stock.Value) if self._rrx_top_stock else ""
                except Exception: cur_ldr=str(self._rrx_top_stock or "")
                rt.append(self._rrx_top_theme or "");rl.append(cur_ldr)
                if len(rt)>lb:rt.pop(0)
                if len(rl)>lb:rl.pop(0)
                self._rrx_meta_roll_themes=rt;self._rrx_meta_roll_leaders=rl
                rtc=sum(1 for i in range(1,len(rt)) if rt[i]!=rt[i-1])
                rlc=sum(1 for i in range(1,len(rl)) if rl[i]!=rl[i-1])
                self._rrx_meta_roll_th_chg=rtc;self._rrx_meta_roll_ld_chg=rlc
                if not getattr(self,"rrx_meta_stress_enable",False):
                    if rtc>=self.rrx_meta_hth or rlc>=self.rrx_meta_hld:
                        hard=True;meta_desired=None
                    elif meta_desired is not None:
                        is_e=tradable_sym is not None
                        if is_e and(rtc>=self.rrx_meta_eth or rlc>=self.rrx_meta_eld):meta_desired=None
                        elif not is_e and(rtc>=self.rrx_meta_cth or rlc>=self.rrx_meta_cld):meta_desired=None
            if getattr(self, "rrx_meta_stress_enable", False):
                cur_sev=(_ps in("STRESS","PANIC") or _ids in("STRESS","PANIC","PANIC_SHORT"))
                rth_now=getattr(self,"_rrx_meta_roll_th_chg",0)
                rlc_now=getattr(self,"_rrx_meta_roll_ld_chg",0)
                c_hard=cur_sev or self._rrx_state==RRX_DAMAGED
                rg=getattr(self,"_rrx_risk_group","")
                ca_ok=(rg in("THEMATIC","SAFE_HAVEN","INFLATION_CYCLICAL","GROWTH_CYCLICAL"))
                th_ov=(self._rrx_state==RRX_STRONG
                       and (rth_now<=1 or getattr(self,"_rrx_meta_theme_age",0)>=4)
                       and ca_ok)
                carry_ok=(prev_meta is not None and not c_hard
                          and rth_now<self.rrx_meta_hth
                          and (_reg!="RISK_OFF" or ca_ok))
                ldr_blk=((not th_ov) and rlc_now>=self.rrx_meta_eld)
                entry_ok=(tradable_sym is not None and not c_hard
                          and not(rpn>=self.rrx_meta_epn or rids>=self.rrx_meta_eids
                                  or rth_now>=self.rrx_meta_eth or ldr_blk))
                if(not entry_ok and th_ov and tradable_sym is not None
                   and not c_hard and rpn==0 and rids<=2):entry_ok=True
                if c_hard:meta_desired=None
                elif carry_ok:meta_desired=prev_meta
                elif entry_ok:meta_desired=tradable_sym
                else:meta_desired=None
                if c_hard:hard=True
                self._RRXAttrUpdate(prev_meta,meta_desired,c_hard,rpn,rids,rth_now,rlc_now,rg,_reg)
            meta_ret = self._RRXShadowExecRet(
                meta_desired, "_rrx_d1_meta_sym", "_rrx_d1_meta_px")
            meta_alloc = cash_ret if (meta_desired is None and prev_meta is None) else meta_ret
            self._rrx_d1_meta_nav *= (1.0 + meta_alloc)
            if getattr(self,"rrx_stop_enable",False):
                self._RRXStopUpdate(meta_desired, cash_ret, tradable_sym, getattr(self,"_rrx_sub_stock",None))
            if getattr(self, "rrx_meta_enable", False):
                _mn = self._rrx_d1_meta_nav
                if _mn > self._rrx_meta_mnav_peak:
                    self._rrx_meta_mnav_peak = _mn
                if self._rrx_meta_mnav_peak > 0:
                    _dd = _mn / self._rrx_meta_mnav_peak - 1.0
                    if _dd < self._rrx_meta_mnav_maxdd:
                        self._rrx_meta_mnav_maxdd = _dd
            if hard:                                              self._rrx_meta_hard_days  += 1
            elif meta_desired is not None and tradable_sym:       self._rrx_meta_entry_days += 1
            elif meta_desired is not None:                        self._rrx_meta_carry_days += 1
            else:                                                 self._rrx_meta_flat_days  += 1
            if meta_desired != prev_meta:                         self._rrx_meta_pos_chg    += 1
        if native_sym is None:
            native_state = "IDLE_CASH"
        else:
            native_state = "ACTIVE"
            try:
                if float(rr.roc5_cand[native_sym].Current.Value) < -0.03:
                    native_state = "DAMAGED"
            except Exception:
                pass

        # Allocation reason: what RRX would do vs native
        tr = getattr(self, "_rrx_tradable", 0)
        if tr == 1:
            if native_state == "IDLE_CASH":   alloc_reason = "replace_idle_rr"
            elif native_state == "DAMAGED":   alloc_reason = "replace_damaged_rr"
            else:                              alloc_reason = "supplement_active_rr"
        else:
            alloc_reason = "no_replace"

        def _tk(s):
            if s is None: return "NONE"
            try: return str(s.Value)
            except Exception: return str(s)

        return {
            "tradable_symbol":     _tk(tradable_sym),
            "lag_tradable_symbol": _tk(lag_tradable_sym),
            "tradable_ret":        tradable_ret,
            "lag_tradable_ret":    lag_tret,
            "cash_ret":            cash_ret,
            "talloc_nav":          self._rrx_d1_talloc_nav,
            "raw_nav":             self._rrx_d1_raw_nav,
            "cash_nav":            self._rrx_d1_cash_nav,
            "lag_tradable_nav":    self._rrx_d1_lag_tradable_nav,
            "lag_raw_nav":         self._rrx_d1_lag_raw_nav,
            "delta_talloc":        self._rrx_d1_talloc_nav  - self._rrx_d1_cash_nav,
            "delta_raw":           self._rrx_d1_raw_nav      - self._rrx_d1_cash_nav,
            "delta_ltalloc":       self._rrx_d1_lag_tradable_nav - self._rrx_d1_cash_nav,
            "delta_lraw":          self._rrx_d1_lag_raw_nav      - self._rrx_d1_cash_nav,
            "exec_symbol":         _tk(getattr(self, "_rrx_d1_exec_sym", None)),
            "exec_ret":            exec_ret,
            "exec_nav":            self._rrx_d1_exec_nav,
            "exec_alloc_nav":      self._rrx_d1_exec_alloc_nav,
            "meta_nav":            self._rrx_d1_meta_nav,
            "delta_meta":          self._rrx_d1_meta_nav - self._rrx_d1_cash_nav,
        }
    except Exception:
        return {}


def _RRXUpdateSumCounters(self) -> None:
    today = self.time.date()
    if self._rrx_d1_sum_start is None:
        self._rrx_d1_sum_start = today
    if getattr(self, "_rrx_tradable", 0) == 1:
        self._rrx_d1_tradable_days += 1
    if self._rrx_state == RRX_STRONG and self._rrx_top_stock:
        self._rrx_d1_raw_days += 1
    tb = str(getattr(self, "_rrx_tblock", "none"))
    if "risk_off"  in tb: self._rrx_d1_blk_risk_off  += 1
    if "ids"       in tb: self._rrx_d1_blk_ids        += 1
    if "panic"     in tb: self._rrx_d1_blk_panic      += 1
    if "stretch"   in tb: self._rrx_d1_blk_stretch    += 1
    if "defensive" in tb: self._rrx_d1_blk_defensive  += 1
    # Meta: count theme and leader changes
    if self._rrx_top_theme != self._rrx_meta_prev_theme:
        if self._rrx_meta_prev_theme is not None:
            self._rrx_meta_theme_chg += 1
        self._rrx_meta_prev_theme = self._rrx_top_theme
    if self._rrx_top_stock != self._rrx_meta_prev_leader:
        if self._rrx_meta_prev_leader is not None:
            self._rrx_meta_leader_chg += 1
        self._rrx_meta_prev_leader = self._rrx_top_stock


def _RRXEmitMonthlySummary(self, today, top) -> None:
    if not self._LogAllowedAt():
        return
    d1 = getattr(self, "_rrx_d1_last", {})
    cnav   = float(d1.get("cash_nav",         1.0))
    tanav  = float(d1.get("talloc_nav",       1.0))
    rnav   = float(d1.get("raw_nav",          1.0))
    ltanav = float(d1.get("lag_tradable_nav", 1.0))
    lrnav  = float(d1.get("lag_raw_nav",      1.0))
    exnav  = float(d1.get("exec_nav",         1.0))
    exanav = float(d1.get("exec_alloc_nav",   1.0))
    self.log(
        f"RRX_D1_SUMMARY,"
        f"start={self._rrx_d1_sum_start},end={today},"
        f"cnav={cnav:.4f},tanav={tanav:.4f},rnav={rnav:.4f},"
        f"dta={tanav - cnav:+.4f},dr={rnav - cnav:+.4f},"
        f"ltanav={ltanav:.4f},lrnav={lrnav:.4f},"
        f"ldta={ltanav - cnav:+.4f},ldr={lrnav - cnav:+.4f},"
        f"exnav={exnav:.4f},dex={exnav - cnav:+.4f},"
        f"exanav={exanav:.4f},dexa={exanav - cnav:+.4f},"
        f"td={self._rrx_d1_tradable_days},bro={self._rrx_d1_blk_risk_off},"
        f"bid={self._rrx_d1_blk_ids},bpn={self._rrx_d1_blk_panic}"
        f"bst={self._rrx_d1_blk_stretch},"            f"bdf={self._rrx_d1_blk_defensive}"
    )
    if top is not None:
        self.log(
            f"RRX_MONTH,{today},"
            f"state={self._rrx_state},"
            f"theme={top['theme']},cls={top['cls']},"
            f"sc={top['score']:.3f},"
            f"tr={getattr(self,'_rrx_tradable',0)},"
            f"rg={getattr(self,'_rrx_risk_group','')}"
        )
    # RRX_META_SUMMARY: regime context for meta-filter research
    if getattr(self, "rrx_meta_enable", False):
        td=self._rrx_d1_tradable_days; pn=self._rrx_d1_blk_panic; ids=self._rrx_d1_blk_ids
        ro=self._rrx_d1_blk_risk_off; tc=self._rrx_meta_theme_chg; lc=self._rrx_meta_leader_chg
        entry=int(td>0 and pn==0 and ids==0 and ro==0 and tc<=2)
        carry=int(pn==0 and (td>0 or (ro>0 and lc<=3)))
        hard=int(pn>5 or ids>10)
        d1 = getattr(self, "_rrx_d1_last", {})
        mnav = float(d1.get("meta_nav", 1.0))
        mret = mnav / self._rrx_meta_mnav_mstart - 1.0 if self._rrx_meta_mnav_mstart > 0 else 0.0
        mdd  = mnav / self._rrx_meta_mnav_peak  - 1.0 if self._rrx_meta_mnav_peak  > 0 else 0.0
        if mret < self._rrx_meta_worst_mret: self._rrx_meta_worst_mret = mret
        sn=self._rrx_stop_nav; sm=self._rrx_stop_mstart
        sret=sn/sm-1.0 if sm>0 else 0.0
        sdd=sn/self._rrx_stop_snav_peak-1.0 if self._rrx_stop_snav_peak>0 else 0.0
        if sret<self._rrx_stop_worst_sret: self._rrx_stop_worst_sret=sret
        self.log(
            f"RRX_META_SUMMARY,"
            f"start={self._rrx_d1_sum_start},end={today},"
            f"en={entry},ca={carry},ha={hard},"
            f"med={self._rrx_meta_entry_days},"
            f"mcd={self._rrx_meta_carry_days},"
            f"mhd={self._rrx_meta_hard_days},"
            f"mfd={self._rrx_meta_flat_days},"
            f"mpc={self._rrx_meta_pos_chg},"
            f"regime={str(getattr(self,'current_regime','NA'))},"
            f"rrx={self._rrx_state},"
            f"theme={self._rrx_top_theme or 'NONE'},"
            f"rg={getattr(self,'_rrx_risk_group','')},"
            f"spy20={getattr(self,'_rrx_last_spy20',0.0):.3f},"
            f"qqq20={getattr(self,'_rrx_last_qqq20',0.0):.3f},"
            f"cnav={cnav:.4f},exanav={exanav:.4f},"
            f"dexa={exanav - cnav:+.4f},"
            f"mnav={mnav:.4f},dm={mnav - cnav:+.4f},"
            f"mret={mret:+.4f},mdd={mdd:+.4f},mmaxdd={self._rrx_meta_mnav_maxdd:+.4f},"
            f"snav={sn:.4f},ds={sn-cnav:+.4f},"
            f"sret={sret:+.4f},sdd={sdd:+.4f},smaxdd={self._rrx_stop_maxdd:+.4f},"
            f"sc={self._rrx_stop_count},ql={getattr(self,'_rrx_stop_qleft',0)},qskip={getattr(self,'_rrx_stop_qskip',0)},"
            f"td={td},bro={ro},bid={ids},bpn={pn},"
            f"th={self._rrx_meta_theme_chg},ld={self._rrx_meta_leader_chg},"
            f"rth={getattr(self,'_rrx_meta_roll_th_chg',0)},rld={getattr(self,'_rrx_meta_roll_ld_chg',0)},"
            f"rpn={getattr(self,'_rrx_meta_roll_pn_cnt',0)},rids={getattr(self,'_rrx_meta_roll_ids_cnt',0)}"
        )
        self._rrx_meta_entry_days = 0
        self._rrx_meta_carry_days = 0
        self._rrx_meta_hard_days  = 0
        self._rrx_meta_flat_days  = 0
        self._rrx_meta_pos_chg    = 0
        self._rrx_meta_mnav_mstart = mnav
        self._rrx_stop_mstart = sn
        if getattr(self,"rrx_d5x_enable",False):
            d5={}
            for tk in ("35","40","45"):
                n=getattr(self,f"_rrx_d5x_nav{tk}",1.0); ms=getattr(self,f"_rrx_d5x_ms{tk}",1.0)
                r=n/ms-1 if ms>0 else 0.0; d=getattr(self,f"_rrx_d5x_dd{tk}",0.0)
                md=getattr(self,f"_rrx_d5x_mdd{tk}",0.0)
                if r<getattr(self,f"_rrx_d5x_wr{tk}",0.0): setattr(self,f"_rrx_d5x_wr{tk}",r)
                setattr(self,f"_rrx_d5x_ms{tk}",n); d5[tk]=(n,r,d,md)
            n35,r35,d35,md35=d5["35"]
            wn=max(("35","40","45"),key=lambda t:d5[t][0])
            wr="35"; wrk="35"
            for tk in ("40","45"):
                nt,rt,dt,mdt=d5[tk]
                if rt>r35: wr=tk
                if rt>r35 and mdt>=md35-0.005: wrk=tk
            self.log(f"RRX_D5X,{today},"
                     f"state={getattr(self,'_rrx_state','?')},"
                     f"regime={str(getattr(self,'current_regime','NA'))},"
                     f"theme={getattr(self,'_rrx_top_theme',None) or 'NONE'},"
                     f"rg={getattr(self,'_rrx_risk_group','')},"
                     f"ql={getattr(self,'_rrx_stop_qleft',0)},"
                     f"sc={self._rrx_stop_count},"
                     f"spy20={getattr(self,'_rrx_last_spy20',0.0):.3f},"
                     f"qqq20={getattr(self,'_rrx_last_qqq20',0.0):.3f},"
                     f"nav35={n35:.4f},nav40={d5['40'][0]:.4f},nav45={d5['45'][0]:.4f},"
                     f"r35={r35:+.4f},r40={d5['40'][1]:+.4f},r45={d5['45'][1]:+.4f},"
                     f"dd35={d35:+.4f},dd40={d5['40'][2]:+.4f},dd45={d5['45'][2]:+.4f},"
                     f"mdd35={md35:+.4f},mdd40={d5['40'][3]:+.4f},mdd45={d5['45'][3]:+.4f},"
                     f"wn={wn},wr={wr},wrk={wrk}")
            # Reset monthly local DD
            for tk in ("35","40","45"):
                n=getattr(self,f"_rrx_d5x_nav{tk}",1.0)
                setattr(self,f"_rrx_d5x_mpk{tk}",n); setattr(self,f"_rrx_d5x_mdd{tk}",0.0)
        if getattr(self, "rrx_d5y_enable", False): pass  # [D5Y] rejected
        if getattr(self, "rrx_d5z_enable", False):
            nz=getattr(self,"_rrx_d5z_nav",1.0)
            msz=getattr(self,"_rrx_d5z_ms",1.0) if hasattr(self,"_rrx_d5z_ms") else 1.0
            rz=nz/msz-1 if msz>0 else 0.0; self._rrx_d5z_ms=nz
            dz=getattr(self,"_rrx_d5z_dd",0.0); ls=getattr(self,"_rrx_d5z_last_stop_date",None)
            pk=float(getattr(self,"_rrx_stop_snav_peak",1.0)); sn=float(getattr(self,"_rrx_stop_nav",1.0))
            sdd=sn/pk-1.0 if pk>0 else 0.0
            n35b=getattr(self,"_rrx_d5x_nav35",1.0); ms35=getattr(self,"_rrx_d5z_ms35",1.0)
            r35_m=n35b/ms35-1 if ms35>0 else 0.0; self._rrx_d5z_ms35=n35b
            dd35=getattr(self,"_rrx_d5x_dd35",0.0)
            nz2=getattr(self,"_rrx_dz2_nav",1.0); ms2=getattr(self,"_rrx_dz2_ms",1.0)
            rz2=nz2/ms2-1 if ms2>0 else 0.0; self._rrx_dz2_ms=nz2
            nz3=getattr(self,"_rrx_dz3_nav",1.0); ms3=getattr(self,"_rrx_dz3_ms",1.0)
            rz3=nz3/ms3-1 if ms3>0 else 0.0; self._rrx_dz3_ms=nz3
            if rz<getattr(self,"_rrx_d5z_wr",0.0): self._rrx_d5z_wr=rz
            if rz2<getattr(self,"_rrx_dz2_wr",0.0): self._rrx_dz2_wr=rz2
            if rz3<getattr(self,"_rrx_dz3_wr",0.0): self._rrx_dz3_wr=rz3
            tzr=self._RRXD5ZTarget()
            wh="OK"if tzr>.39 else"ql"if getattr(self,"_rrx_stop_qleft",0)else"cl"if getattr(self,"_rrx_stop_cluster",0)else"sdd"
            eps=0.002
            if rz<r35_m-eps:
                self.log(f"RRX_D5Z_FAIL_RET,{today},th={getattr(self,'_rrx_top_theme',None) or 'N'},rg={getattr(self,'_rrx_risk_group','')},tgt_eom={tzr:.2f},wh={wh},rz={rz:+.4f},r35={r35_m:+.4f},rz2={rz2:+.4f},rz3={rz3:+.4f},sdd={sdd:+.4f},ql={getattr(self,'_rrx_stop_qleft',0)},cl={getattr(self,'_rrx_stop_cluster',0)},spy={getattr(self,'_rrx_last_spy20',0.0):.3f},qqq={getattr(self,'_rrx_last_qqq20',0.0):.3f}")
            _prev_dz=getattr(self,"_rrx_d5z_prev_dd",0.0)
            if dz<dd35-eps and dz<_prev_dz-eps:
                self.log(f"RRX_D5Z_FAIL_DD,{today},dz={dz:+.4f},dd35={dd35:+.4f},prev={_prev_dz:+.4f},wh={wh},sdd={sdd:+.4f},th={getattr(self,'_rrx_top_theme',None) or 'N'}")
            self._rrx_d5z_prev_dd=dz
            self.log(f"RRX_D5Z,{today},state={getattr(self,'_rrx_state','?')},ql={getattr(self,'_rrx_stop_qleft',0)},sc={self._rrx_stop_count},cl={getattr(self,'_rrx_stop_cluster',0)},sdd={sdd:+.4f},thr={self.rrx_d5z_snav_dd_thr:.2f},ls={ls},nav={nz:.4f},r={rz:+.4f},dd={dz:+.4f},n35={getattr(self,'_rrx_d5z_n35',0)},n40={getattr(self,'_rrx_d5z_n40',0)},tgt_eom={tzr:.2f},wh={wh},navZ2={nz2:.4f},rZ2={rz2:+.4f},navZ3={nz3:.4f},rZ3={rz3:+.4f}")
            self._rrx_d5z_n35=0; self._rrx_d5z_n40=0
    self._rrx_d1_sum_start     = today
    self._rrx_d1_tradable_days = 0
    self._rrx_d1_raw_days      = 0
    self._rrx_d1_blk_risk_off  = 0
    self._rrx_d1_blk_ids       = 0
    self._rrx_d1_blk_panic     = 0
    self._rrx_d1_blk_stretch   = 0
    self._rrx_d1_blk_defensive = 0
    self._rrx_meta_theme_chg   = 0
    self._rrx_meta_leader_chg  = 0


def RRXEmitFinalSummary(self) -> None:
    if not getattr(self, "rr_xsector_enable", False):
        return
    # [D6] independent of D1 summary gate
    if getattr(self, "rrx_d6_leader_first_enable", False):
        getattr(self, "RRXD6EmitFinal", lambda: None)()
    if not getattr(self, "rrx_d1_summary_enable", False):
        return
    try:
        d1     = getattr(self, "_rrx_d1_last", {})
        cnav   = float(d1.get("cash_nav",         1.0))
        tanav  = float(d1.get("talloc_nav",       1.0))
        rnav   = float(d1.get("raw_nav",          1.0))
        ltanav = float(d1.get("lag_tradable_nav", 1.0))
        lrnav  = float(d1.get("lag_raw_nav",      1.0))
        exnav  = float(d1.get("exec_nav",         1.0))
        exanav = float(d1.get("exec_alloc_nav",   1.0))
        super(type(self), self).log(
            f"RRX_D1_FINAL,"
            f"start={self._rrx_d1_sum_start},end={self.time.date()},"
            f"cnav={cnav:.4f},tanav={tanav:.4f},rnav={rnav:.4f},"
            f"dta={tanav - cnav:+.4f},dr={rnav - cnav:+.4f},"
            f"ltanav={ltanav:.4f},lrnav={lrnav:.4f},"
            f"ldta={ltanav - cnav:+.4f},ldr={lrnav - cnav:+.4f},"
            f"exnav={exnav:.4f},dex={exnav - cnav:+.4f},"
            f"exanav={exanav:.4f},dexa={exanav - cnav:+.4f},"
            f"td={self._rrx_d1_tradable_days},"
            f"rd={self._rrx_d1_raw_days},"
            f"bro={self._rrx_d1_blk_risk_off},"
            f"bid={self._rrx_d1_blk_ids},"
            f"bpn={self._rrx_d1_blk_panic},"
            f"bst={self._rrx_d1_blk_stretch},"                f"bdf={self._rrx_d1_blk_defensive}"
        )
    except Exception as e:
        try:
            super(type(self), self).log(f"RRX_D1_FINAL_ERROR,{e}")
        except Exception:
            pass
    if getattr(self, "rrx_meta_enable", False):
        try:
            d1m = getattr(self, "_rrx_d1_last", {})
            mnf = float(d1m.get("meta_nav", 1.0))
            cnf = float(d1m.get("cash_nav",  1.0))
            enf = float(d1m.get("exec_alloc_nav", 1.0))
            super(type(self), self).log(
                f"RRX_META_FINAL,"
                f"start={self._rrx_d1_sum_start},end={self.time.date()},"
                f"cnav={cnf:.4f},exanav={enf:.4f},mnav={mnf:.4f},"
                f"dexa={enf - cnf:+.4f},dm={mnf - cnf:+.4f},"
                f"mmaxdd={self._rrx_meta_mnav_maxdd:+.4f},"
                f"worst_mret={self._rrx_meta_worst_mret:+.4f},"
                f"snav={self._rrx_stop_nav:.4f},ds={self._rrx_stop_nav-cnf:+.4f},"
                f"smaxdd={self._rrx_stop_maxdd:+.4f},"
                f"worst_sret={self._rrx_stop_worst_sret:+.4f},"
                f"sc={self._rrx_stop_count},"
                f"reI={getattr(self,'_rrx_stop_reI',0)},reL={getattr(self,'_rrx_stop_reL',0)},reH={getattr(self,'_rrx_stop_reH',0)},reR={getattr(self,'_rrx_stop_reR',0)},clust={getattr(self,'_rrx_stop_cluster',0)},bl={getattr(self,'_rrx_stop_budget_lock',0)},"
                f"med={self._rrx_meta_entry_days},mcd={self._rrx_meta_carry_days},"
                f"mhd={self._rrx_meta_hard_days},mfd={self._rrx_meta_flat_days},mpc={self._rrx_meta_pos_chg}"
            )
        except Exception as e2:
            try:
                super(type(self), self).log(f"RRX_META_FINAL_ERROR,{e2}")
            except Exception:
                pass
    if getattr(self, "rrx_attr_enable", False):
        for sym, st in getattr(self, "_rrx_attr_stats", {}).items():
            n,w,tot,worst,max_mae = st
            super(type(self), self).log(
                f"RRX_ATTR_SUMMARY,sym={sym},"
                f"n={n},wr={w/n:.2f},avg={tot/n:+.4f},"
                f"worst={worst:+.4f},tot={tot:+.4f},"
                f"max_mae={max_mae:+.4f}"
            )
    if getattr(self, "rrx_d5x_enable", False):
        try:
            n35=getattr(self,"_rrx_d5x_nav35",1.0); sn_f=self._rrx_stop_nav
            diff=abs(n35-sn_f)/max(sn_f,1e-6)
            self.log(
                f"RRX_D5X_FINAL,"
                f"nav35={n35:.4f},"
                f"nav40={getattr(self,'_rrx_d5x_nav40',1.0):.4f},"
                f"nav45={getattr(self,'_rrx_d5x_nav45',1.0):.4f},"
                f"dd35={getattr(self,'_rrx_d5x_dd35',0.0):+.4f},"
                f"dd40={getattr(self,'_rrx_d5x_dd40',0.0):+.4f},"
                f"dd45={getattr(self,'_rrx_d5x_dd45',0.0):+.4f},"
                f"wr35={getattr(self,'_rrx_d5x_wr35',0.0):+.4f},"
                f"wr40={getattr(self,'_rrx_d5x_wr40',0.0):+.4f},"
                f"wr45={getattr(self,'_rrx_d5x_wr45',0.0):+.4f}"
            )
            self.log(f"RRX_D5X_PARITY,snav={sn_f:.4f},nav35={n35:.4f},"
                     f"diff={diff:+.4f},ok={'Y' if diff<0.001 else 'N'}")
        except Exception: pass
    if getattr(self, "rrx_d5y_enable", False):
        try:
            n35b = getattr(self, "_rrx_d5x_nav35", 1.0)
            self.log(
                f"RRX_D5Y_FINAL,"
                f"navR1={getattr(self,'_rrx_d5y_navR1',1.0):.4f},"
                f"navR2={getattr(self,'_rrx_d5y_navR2',1.0):.4f},"
                f"navR3={getattr(self,'_rrx_d5y_navR3',1.0):.4f},"
                f"ddR1={getattr(self,'_rrx_d5y_ddR1',0.0):+.4f},"
                f"ddR2={getattr(self,'_rrx_d5y_ddR2',0.0):+.4f},"
                f"ddR3={getattr(self,'_rrx_d5y_ddR3',0.0):+.4f},"
                f"wrR1={getattr(self,'_rrx_d5y_wrR1',0.0):+.4f},"
                f"wrR2={getattr(self,'_rrx_d5y_wrR2',0.0):+.4f},"
                f"wrR3={getattr(self,'_rrx_d5y_wrR3',0.0):+.4f},"
                f"vs35_nav={n35b:.4f},"
                f"vs35_dd={getattr(self,'_rrx_d5x_dd35',0.0):+.4f},"
                f"vs35_wr={getattr(self,'_rrx_d5x_wr35',0.0):+.4f}"
            )
        except Exception:
            pass
    if getattr(self, "rrx_d5z_enable", False):
        try:
            b=getattr(self,"_rrx_d5x_nav35",1.0)
            bd=getattr(self,"_rrx_d5x_dd35",0.0); bw=getattr(self,"_rrx_d5x_wr35",0.0)
            self.log(
                f"RRX_D5Z_FINAL,"
                f"z1={self._rrx_d5z_nav:.4f},d1={self._rrx_d5z_dd:+.4f},w1={self._rrx_d5z_wr:+.4f},"
                f"z2={self._rrx_dz2_nav:.4f},d2={self._rrx_dz2_dd:+.4f},w2={self._rrx_dz2_wr:+.4f},"
                f"z3={self._rrx_dz3_nav:.4f},d3={self._rrx_dz3_dd:+.4f},w3={self._rrx_dz3_wr:+.4f},"
                f"b={b:.4f},bd={bd:+.4f},bw={bw:+.4f},"
                f"t35={getattr(self,'_rrx_d5z_tot35',0)},t40={getattr(self,'_rrx_d5z_tot40',0)},"
                f"z2t40={getattr(self,'_rrx_dz2_tot40',0)},z3t40={getattr(self,'_rrx_dz3_tot40',0)},"
                f"thr={self.rrx_d5z_snav_dd_thr:.2f},"
                f"guard={self.rrx_d5z_stop_guard_days},"
                f"prof={getattr(self,'rrx_d5z_profile','custom')}"
            )
        except Exception:
            pass
    stk_label = None
    try:
        s = getattr(self, "_rrx_top_stock", None)
        stk_label = s.Value if s is not None else None
    except Exception:
        stk_label = None
    return {
        "enabled":    getattr(self, "rr_xsector_enable", False),
        "state":      getattr(self, "_rrx_state",         RRX_IDLE),
        "top_theme":  getattr(self, "_rrx_top_theme",     None),
        "top_score":  getattr(self, "_rrx_top_score",     None),
        "top_cls":    getattr(self, "_rrx_top_theme_cls", None),
        "top_stock":  stk_label,
        "stk_score":  getattr(self, "_rrx_stk_score",     None),
        "tradable":   getattr(self, "_rrx_tradable",       0),
        "tblock":     getattr(self, "_rrx_tblock",         ""),
        "risk_group": getattr(self, "_rrx_risk_group",     ""),
    }