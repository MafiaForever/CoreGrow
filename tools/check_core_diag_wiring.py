#!/usr/bin/env python3
"""Static wiring check for CORE-D0.4 Core Recovery diagnostics. No backtest."""
from __future__ import annotations
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "project" / "main.py"
DIAG = ROOT / "project" / "cg_core_recovery_diag.py"
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
    for label, src in (("main.py", main_src), ("cg_core_recovery_diag.py", diag_src)):
        try:
            ast.parse(src)
        except SyntaxError as e:
            fail(f"{label} syntax: {e}")

    checks = [
        ("CgCoreRecoveryInit() call", "CgCoreRecoveryInit(" in main_src),
        ("CgCoreRecoveryUpdate() call", "CgCoreRecoveryUpdate(" in main_src),
        ("CgCoreRecoveryEmitFinal() call", "CgCoreRecoveryEmitFinal(" in main_src),
        ("runtime CG_CORE_ append", 'lp.append("CG_CORE_")' in diag_src
         or "lp.append('CG_CORE_')" in diag_src),
        ("QC override comment", "QC project parameters override RRX_PARAMS" in diag_src),
        ("update error [INIT]", '[INIT] CG_CORE_RECOVERY_ERROR,stage=update' in diag_src),
        ("final error [EOA]", '[EOA] CG_CORE_RECOVERY_ERROR,stage=final' in main_src),
        ("first-update checkpoint", "CG_CORE_RECOVERY_UPDATE_OK" in diag_src),
        ("emit start checkpoint", "CG_CORE_RECOVERY_EMIT_START" in diag_src),
        ("emit done checkpoint", "CG_CORE_RECOVERY_EMIT_DONE" in diag_src),
        ("prefix_allowed in init log", "prefix_allowed=" in diag_src),
        ("duplicate-date guard", "_crd_last_update_date" in diag_src),
        ("smoke mode", "cg_core_diag_smoke_mode" in diag_src),
        ("checkpoint days", "cg_core_diag_checkpoint_days" in diag_src),
        ("timing enable", "cg_core_diag_timing_enable" in diag_src),
    ]
    for name, ok in checks:
        if not ok:
            fail(name)

    # no order APIs inside diagnostic mixin methods (AST Call names only)
    tree = ast.parse(diag_src)
    banned = set(ORDER_APIS)
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "CgCoreRecoveryDiagMixin":
            for sub in ast.walk(node):
                if isinstance(sub, ast.Call):
                    fn = sub.func
                    name = None
                    if isinstance(fn, ast.Name):
                        name = fn.id
                    elif isinstance(fn, ast.Attribute):
                        name = fn.attr
                    if name in banned:
                        fail(f"diagnostic calls {name}")
            break
    else:
        fail("CgCoreRecoveryDiagMixin missing")

    # forbidden files unchanged in this patch intent: just ensure we didn't edit them
    # by requiring they are not in argv and exist; structural only
    for p in FORBIDDEN:
        if not p.exists():
            fail(f"missing forbidden-path file {p.name}")

    # ensure emit before final snapshot
    eoa_i = main_src.find("def OnEndOfAlgorithm")
    if eoa_i < 0:
        fail("OnEndOfAlgorithm missing")
    chunk = main_src[eoa_i:eoa_i + 800]
    if chunk.find("CgCoreRecoveryEmitFinal") < 0:
        fail("EmitFinal not in OnEndOfAlgorithm")
    if chunk.find("CgCoreRecoveryEmitFinal") > chunk.find('[EOA] final snapshot saved'):
        fail("EmitFinal after final snapshot log")

    print("PASS")
    sys.exit(0)


if __name__ == "__main__":
    main()
