#!/usr/bin/env python3
"""Static wiring check for IDS-NORMAL-CAP-D0. No backtest."""
from __future__ import annotations
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "project" / "main.py"
DIAG = ROOT / "project" / "cg_ids_normal_cap_diag.py"
FORBIDDEN = (
    ROOT / "project" / "rrx_leader_first_diag.py",
    ROOT / "project" / "rr_xsector_diag.py",
)
ORDER_APIS = ("set_holdings", "market_order", "liquidate", "ExecuteTargets")


def fail(msg: str) -> None:
    print(f"FAIL: {msg}")
    sys.exit(1)


def main() -> None:
    main_src = MAIN.read_text(encoding="utf-8")
    diag_src = DIAG.read_text(encoding="utf-8")
    for label, src in (("main.py", main_src), ("diag", diag_src)):
        try:
            ast.parse(src)
        except SyntaxError as e:
            fail(f"{label} syntax: {e}")
    checks = [
        ("Init call", "CgIdsNormalCapInit(" in main_src),
        ("Update call", "CgIdsNormalCapUpdate(" in main_src),
        ("Emit call", "CgIdsNormalCapEmitFinal(" in main_src),
        ("runtime CG_IDS_ append", 'lp.append("CG_IDS_")' in diag_src),
        ("UPDATE_OK", "CG_IDS_CAP_UPDATE_OK" in diag_src),
        ("EMIT_START", "CG_IDS_CAP_EMIT_START" in diag_src),
        ("EMIT_DONE", "CG_IDS_CAP_EMIT_DONE" in diag_src),
        ("update error [INIT]", "[INIT] CG_IDS_CAP_ERROR,stage=update" in diag_src),
        ("final error [EOA]", "[EOA] CG_IDS_CAP_ERROR,stage=final" in main_src),
        ("activation NORMAL+WATCH/STRESS", 'ids in ("WATCH", "STRESS")' in diag_src),
        ("panic NORMAL gate", 'ps == "NORMAL"' in diag_src),
        ("never mutates combined", "combined[" not in diag_src and "combined =" not in diag_src),
        ("candidates C1-C4", '"C1"' in diag_src and '"C4"' in diag_src),
        ("select final", "CG_IDS_CAP_SELECT_FINAL" in diag_src),
    ]
    for name, ok in checks:
        if not ok:
            fail(name)
    tree = ast.parse(diag_src)
    banned = set(ORDER_APIS)
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "CgIdsNormalCapDiagMixin":
            for sub in ast.walk(node):
                if isinstance(sub, ast.Call):
                    fn = sub.func
                    name = fn.id if isinstance(fn, ast.Name) else (
                        fn.attr if isinstance(fn, ast.Attribute) else None)
                    if name in banned:
                        fail(f"diagnostic calls {name}")
            break
    else:
        fail("mixin missing")
    for p in FORBIDDEN:
        if not p.exists():
            fail(f"missing {p.name}")
    # update before ExecuteTargets in rebalance path
    i = main_src.find("CgIdsNormalCapUpdate(combined)")
    j = main_src.find("ExecuteTargets(combined)")
    if i < 0 or j < 0 or i > j:
        fail("update not before ExecuteTargets")
    eoa = main_src.find("def OnEndOfAlgorithm")
    chunk = main_src[eoa:eoa + 900]
    if chunk.find("CgIdsNormalCapEmitFinal") > chunk.find('[EOA] final snapshot saved'):
        fail("emit after snapshot log")
    print("PASS")
    sys.exit(0)


if __name__ == "__main__":
    main()
