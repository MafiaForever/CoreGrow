# region imports
from AlgorithmImports import *
# endregion
# rrx_trade_bridge.py
# RRX-80 leader -> direct trade bridge [RRX_C1_TRADE_BRIDGE]
# Diagnostic-safe: gated by rrx_trade_bridge_enable (default 0).
# Zero trading impact when disabled.

def _GetRrxTradeLeader(self):
    """Return (symbol, reason) for current RRX tradable leader, or (None, reason)."""
    try:
        if not bool(getattr(self, "rr_xsector_enable", False)):
            return None, "rrx_disabled"
        if str(getattr(self, "_rrx_state", "")) != "RRX_STRONG":
            return None, "not_strong"
        sym = getattr(self, "_rrx_top_stock", None)
        if sym is None:
            return None, "no_leader"
        if int(getattr(self, "_rrx_tradable", 0)) != 1:
            return None, str(getattr(self, "_rrx_tblock", "not_tradable"))
        return sym, "rrx_ok"
    except Exception as e:
        return None, f"err_{type(e).__name__}"

def _ApplyRRXTradeBridge(self, combined: dict) -> None:
    """[RRX_C1_TRADE_BRIDGE] Route RRX-80 leader into combined targets.
    Gated by rrx_trade_bridge_enable. Respects D5Z conservative gate.
    Scales all CG positions to cg_budget; sets leader to min(rr_budget, cap).
    """
    try:
        if not bool(getattr(self, "rrx_trade_bridge_enable", False)):
            return

        # D5Z quality gate (reuse existing gate logic)
        if getattr(self, "dyn_alloc_c2n_d5z_gate_enable", False):
            try:
                d5z_ok = float(self._RRXD5ZTarget()) > 0.39
            except Exception:
                d5z_ok = False
            if not d5z_ok:
                self._rrx_bridge_last = {"leader": "NONE", "why": "d5z_gate", "tgt": 0.0}
                return

        leader, why = self._GetRrxTradeLeader()

        cg = float(getattr(self, "dyn_alloc_base_cg", 0.66))
        rr = float(getattr(self, "dyn_alloc_base_rr", 0.34))
        cap = float(getattr(self, "dyn_c2n_leader_cap", 0.20))

        if leader is None:
            self._rrx_bridge_last = {"leader": "NONE", "why": why, "tgt": 0.0}
            return

        tgt = min(rr, cap)

        # Rotation cleanup: zero old leader
        prev = getattr(self, "_rrx_bridge_prev_leader", None)
        if prev is not None and prev != leader:
            combined[prev] = 0.0
        self._rrx_bridge_prev_leader = leader

        # Scale CG targets
        rr_syms = {leader, prev} - {None}
        for s in list(combined.keys()):
            if s not in rr_syms:
                combined[s] = float(combined.get(s, 0.0)) * cg

        # Set RRX leader
        combined[leader] = tgt

        try:
            ldr_v = str(leader.Value)
        except Exception:
            ldr_v = str(leader)

        self._rrx_bridge_last = {"leader": ldr_v, "why": why, "tgt": tgt,
                                  "cg": cg, "rr": rr, "cap": cap}
        self.log(f"[RRX_BRIDGE] ldr={ldr_v},tgt={tgt:.3f},cg={cg:.2f},why={why}")

    except Exception as e:
        self.log(f"[RRX_BRIDGE_ERR] {type(e).__name__}:{e}")