#!/usr/bin/env python3
import ast, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "project"))
from cg_legacy_params import _LEGACY, _PROD, _NEVER

def fail(m):
    print("FAIL:", m); sys.exit(1)

files = {
    "main.py": ROOT / "project" / "main.py",
    "cg_legacy_params.py": ROOT / "project" / "cg_legacy_params.py",
    "cg_logic.py": ROOT / "project" / "cg_logic.py",
}
sizes = {}
for name, p in files.items():
    s = p.read_text(encoding="utf-8")
    ast.parse(s)
    n = len(s)
    sizes[name] = n
    if n >= 64000:
        fail(f"{name} too large: {n}")

main = files["main.py"].read_text(encoding="utf-8")
diag = files["cg_legacy_params.py"].read_text(encoding="utf-8")
logic = files["cg_logic.py"].read_text(encoding="utf-8")

if 'or "0"' not in main and "or '0'" not in main:
    fail("legacy profile default not OFF")
if "CgLegacyParamProfileApply(" not in main:
    fail("Apply missing")
if "CgLegacyParamProfileAudit(" not in main:
    fail("Audit missing")
if "CgLegacyParamProfileEmitDiff(" not in main:
    fail("EmitDiff missing")
if "emergency_dd_limit" not in _NEVER:
    fail("emergency_dd not in NEVER")
if "emergency_dd_limit" in _LEGACY:
    fail("emergency_dd in LEGACY dict")
if _LEGACY.get("min_trade_value_perc") != 0.11:
    fail("legacy min_trade_value_perc")
if _LEGACY.get("bear_rally_gate_enable") is not False:
    fail("legacy bear_rally")
if _PROD.get("min_trade_value_perc") != 0.12:
    fail("prod min_trade")
if _PROD.get("bear_rally_gate_enable") is not True:
    fail("prod bear_rally")
if "getattr(self, \"max_symbol_weight\"" not in logic and "getattr(self, 'max_symbol_weight'" not in logic:
    fail("max_symbol_weight not attributed")
if "getattr(self, \"max_total_exposure\"" not in logic and "getattr(self, 'max_total_exposure'" not in logic:
    fail("max_total_exposure not attributed")
# no old SH import
if "yesterday" in diag.lower() and "sh" in diag.lower():
    pass
if "import sh_hedge_old" in main or "LegacySH" in main:
    fail("old SH restored")
# period wiring
if "self.spy_long_sma_period" not in main:
    fail("spy_long_sma_period missing")
if "int(self.spy_long_sma_period)" not in main:
    fail("long SMA not wired")
# Apply before SHInitialize
i = main.find("CgLegacyParamProfileApply")
j = main.find("self.SHInitialize()")
if i < 0 or j < 0 or i > j:
    fail("Apply not before SHInitialize")
# Audit after emergency_dd assignment
k = main.find("emergency_dd_limit")
a = main.find("CgLegacyParamProfileAudit")
if a < k:
    fail("Audit before emergency_dd set")
print("PASS")
print("sizes", sizes)
print("param_count", len(_LEGACY))
print("real_diffs", sorted(k for k in _LEGACY if _LEGACY[k] != _PROD[k]))
