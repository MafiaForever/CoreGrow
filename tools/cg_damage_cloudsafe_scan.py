# tools/cg_damage_cloudsafe_scan.py -- Cursor-local only.
# NOT imported by project/ algorithm modules. Scans the D0 Cloud dependency closure
# for executable forbidden file-read APIs (open / Path.read_text / etc.).
from __future__ import annotations
import ast
import hashlib
import io
import json
import re
import tokenize
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROJ = ROOT / "project"
_WIRING_HOSTS = ("cg_maisr_diag.py",)

FORBIDDEN_NAMES = {
    "OPEN": "builtin open(",
    "IO_OPEN": "io.open(",
    "OS_OPEN": "os.open(",
    "PATH_OPEN": "Path.open(",
    "READ_TEXT": "Path.read_text(",
    "READ_BYTES": "Path.read_bytes(",
}


def local_module_imports(path: Path):
    out = set()
    text = path.read_text(encoding="utf-8")
    for line in text.splitlines():
        s = line.strip()
        m = re.match(r"(?:from|import)\s+([a-zA-Z0-9_]+)", s)
        if not m:
            continue
        top = m.group(1) + ".py"
        if (path.parent / top).exists():
            out.add(top)
    return out


def build_d03b_dependency_closure(project_dir=None):
    root = Path(project_dir) if project_dir else PROJ
    seeds = {p.name for p in root.glob("cg_damage_duration_*.py")}
    for host in _WIRING_HOSTS:
        if (root / host).exists():
            seeds.add(host)
    for p in root.glob("*.py"):
        if p.name in seeds:
            continue
        txt = p.read_text(encoding="utf-8")
        if re.search(r"(?:from|import)\s+cg_damage_duration_", txt):
            seeds.add(p.name)
    seen = set()
    stack = list(seeds)
    while stack:
        name = stack.pop()
        if name in seen:
            continue
        seen.add(name)
        path = root / name
        if not path.exists():
            continue
        for dep in local_module_imports(path):
            if dep not in seen:
                stack.append(dep)
    return sorted(seen)


def scan_file_executable_forbidden(path: Path):
    """Return list of {line, kind, snippet} for executable forbidden APIs."""
    src = path.read_text(encoding="utf-8")
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(src).readline))
    except tokenize.TokenError as e:
        return [{"line": 0, "kind": "TOKENIZE_FAIL", "snippet": str(e)}]
    hits = []
    for i, t in enumerate(tokens):
        if t.type != tokenize.NAME:
            continue
        nxt = tokens[i + 1].string if i + 1 < len(tokens) else ""
        prev = tokens[i - 1].string if i > 0 else ""
        prev2 = tokens[i - 2].string if i > 1 else ""
        if t.string == "open" and nxt == "(":
            if prev == "." and prev2 == "io":
                kind = "IO_OPEN"
            elif prev == ".":
                kind = "PATH_OPEN"
            else:
                kind = "OPEN"
            hits.append({"line": t.start[0], "kind": kind, "snippet": FORBIDDEN_NAMES[kind]})
        if t.string == "open" and prev == "." and prev2 == "os" and nxt == "(":
            hits.append({"line": t.start[0], "kind": "OS_OPEN", "snippet": FORBIDDEN_NAMES["OS_OPEN"]})
        if t.string == "read_text" and prev == "." and nxt == "(":
            hits.append({"line": t.start[0], "kind": "READ_TEXT", "snippet": FORBIDDEN_NAMES["READ_TEXT"]})
        if t.string == "read_bytes" and prev == "." and nxt == "(":
            hits.append({"line": t.start[0], "kind": "READ_BYTES", "snippet": FORBIDDEN_NAMES["READ_BYTES"]})
    # dedupe by line+kind
    seen = set()
    out = []
    for h in hits:
        key = (h["line"], h["kind"])
        if key in seen:
            continue
        seen.add(key)
        out.append(h)
    return out


def scan_closure(project_dir=None):
    root = Path(project_dir) if project_dir else PROJ
    closure = build_d03b_dependency_closure(root)
    files = {}
    total = 0
    for name in closure:
        hits = scan_file_executable_forbidden(root / name)
        files[name] = hits
        total += len(hits)
    return {
        "cloud_project": "CoreGrowth",
        "cloud_project_id": 27489898,
        "scanned_dependency_count": len(closure),
        "executable_forbidden_call_count": total,
        "files": files,
        "violating_files": sorted([n for n, h in files.items() if h]),
        "gate": "PASS" if total == 0 else "FAIL",
        "comments_strings_excluded": True,
    }


def build_local_source_manifest(project_dir=None, commit_sha=None):
    """Disk-backed manifest builder (Cursor-local)."""
    import sys
    sys.path.insert(0, str(PROJ))
    from cg_damage_duration_d03b_compact_export import build_local_source_manifest_from_contents
    root = Path(project_dir) if project_dir else PROJ
    contents = {}
    for name in build_d03b_dependency_closure(root):
        contents[name] = (root / name).read_text(encoding="utf-8")
    return build_local_source_manifest_from_contents(contents, commit_sha=commit_sha)


def verify_pythonnet_and_sizes(project_dir=None):
    root = Path(project_dir) if project_dir else PROJ
    main = (root / "main.py").read_text(encoding="utf-8")
    tree = ast.parse(main)
    bases = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and "CoreGrowth" in node.name:
            bases = [b.id if isinstance(b, ast.Name) else "" for b in node.bases]
    sizes = {p.name: len(p.read_text(encoding="utf-8")) for p in root.glob("*.py")}
    return {
        "pythonnet_bases": bases,
        "pythonnet_ok": bases == ["QCAlgorithm"],
        "all_below_64000": all(v < 64000 for v in sizes.values()),
        "above_64000": [k for k, v in sizes.items() if v >= 64000],
        "main_chars": sizes.get("main.py"),
    }


def run_cloudsafe_gate():
    scan = scan_closure()
    py = verify_pythonnet_and_sizes()
    rows = []
    passed = failed = 0

    def ok(n, c, detail=""):
        nonlocal passed, failed
        if c:
            passed += 1
            rows.append({"name": n, "pass": True, "detail": detail})
        else:
            failed += 1
            rows.append({"name": n, "pass": False, "detail": str(detail)})

    ok("CS01_scan_ran", scan["scanned_dependency_count"] > 0)
    ok("CS02_forbidden_zero", scan["executable_forbidden_call_count"] == 0,
       detail=str(scan["violating_files"]))
    ok("CS03_pythonnet", py["pythonnet_ok"], detail=str(py["pythonnet_bases"]))
    ok("CS04_sizes", py["all_below_64000"], detail=str(py["above_64000"]))
    # allowed modules must themselves be clean
    for name in (
        "cg_damage_duration_d03b_runtime.py",
        "cg_damage_duration_d03b_compact_export.py",
        "cg_damage_duration_d01_diag.py",
    ):
        ok("CS05_clean_" + name, len(scan["files"].get(name) or []) == 0,
           detail=str(scan["files"].get(name)))
    return {
        "passed": passed,
        "failed": failed,
        "total": passed + failed,
        "rows": rows,
        "scan": scan,
        "pythonnet_sizes": py,
    }


if __name__ == "__main__":
    r = run_cloudsafe_gate()
    print(json.dumps({k: r[k] for k in r if k != "rows"}, indent=2))
    for row in r["rows"]:
        if not row["pass"]:
            print("FAIL", row["name"], row["detail"])
