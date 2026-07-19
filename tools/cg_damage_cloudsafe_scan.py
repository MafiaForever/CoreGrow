# tools/cg_damage_cloudsafe_scan.py -- Cursor-local only.
# NOT imported by project/ algorithm modules. Scans the D0 Cloud dependency closure
# for (1) AST executable forbidden file-read APIs and (2) QC raw lexical whitelist
# rejects of the bare file-open-call token (comments/strings/docstrings included).
from __future__ import annotations
import ast
import json
import re
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROJ = ROOT / "project"
_WIRING_HOSTS = ("cg_maisr_diag.py",)

# QC rejects the bare call token even inside comments/strings.
# Boundary avoids false positives on identifiers like current_open( / after_market_open(.
RAW_OPEN_LEXEME_RE = re.compile(r"(?<![A-Za-z0-9_])open\s*\(")

FORBIDDEN_KIND = {
    "OPEN": "builtin file-open-call",
    "IO_OPEN": "io file-open-call",
    "OS_OPEN": "os file-open-call",
    "PATH_OPEN": "Path file-open-call",
    "READ_TEXT": "Path.read_text-call",
    "READ_BYTES": "Path.read_bytes-call",
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


def _name_chain(node: ast.AST):
    """Return dotted Name/Attribute chain, or None if interrupted (e.g. Call)."""
    parts = []
    cur = node
    while True:
        if isinstance(cur, ast.Name):
            parts.append(cur.id)
            break
        if isinstance(cur, ast.Attribute):
            parts.append(cur.attr)
            cur = cur.value
            continue
        return None
    parts.reverse()
    return parts


def scan_file_executable_forbidden(path: Path):
    """AST-based scan: comments/strings are excluded (not Call nodes)."""
    src = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(src, filename=str(path))
    except SyntaxError as e:
        return [{"line": e.lineno or 0, "kind": "PARSE_FAIL", "snippet": str(e)}]
    hits = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        kind = None
        func = node.func
        if isinstance(func, ast.Name) and func.id == "open":
            kind = "OPEN"
        elif isinstance(func, ast.Attribute):
            attr = func.attr
            base = _name_chain(func.value)
            if attr == "open":
                if base == ["io"]:
                    kind = "IO_OPEN"
                elif base == ["os"]:
                    kind = "OS_OPEN"
                else:
                    kind = "PATH_OPEN"
            elif attr == "read_text":
                kind = "READ_TEXT"
            elif attr == "read_bytes":
                kind = "READ_BYTES"
        if kind:
            hits.append({
                "line": getattr(node, "lineno", 0),
                "kind": kind,
                "snippet": FORBIDDEN_KIND[kind],
            })
    seen = set()
    out = []
    for h in hits:
        key = (h["line"], h["kind"])
        if key in seen:
            continue
        seen.add(key)
        out.append(h)
    return out


def _classify_raw_context(line: str):
    s = line.lstrip()
    if s.startswith("#"):
        return "comment"
    if '"""' in line or "'''" in line:
        return "docstring_or_string"
    if '"' in line or "'" in line:
        return "string"
    return "code_or_other"


def scan_file_raw_lexical(path: Path):
    """Raw UTF-8 lexical scan. Includes comments, strings, and docstrings."""
    src = path.read_text(encoding="utf-8")
    hits = []
    for i, line in enumerate(src.splitlines(), 1):
        for m in RAW_OPEN_LEXEME_RE.finditer(line):
            hits.append({
                "line": i,
                "col": m.start() + 1,
                "kind": "RAW_OPEN_LEXEME",
                "context": _classify_raw_context(line),
                "snippet": line.strip()[:160],
            })
    return hits


def scan_closure(project_dir=None):
    """AST executable-call scan over the synchronized dependency closure."""
    root = Path(project_dir) if project_dir else PROJ
    closure = build_d03b_dependency_closure(root)
    files = {}
    per_file_counts = {}
    total = 0
    for name in closure:
        hits = scan_file_executable_forbidden(root / name)
        files[name] = hits
        per_file_counts[name] = len(hits)
        total += len(hits)
    return {
        "cloud_project": "CoreGrowth",
        "cloud_project_id": 27489898,
        "scanned_dependency_count": len(closure),
        "executable_forbidden_call_count": total,
        "per_file_counts": per_file_counts,
        "files": files,
        "violating_files": sorted([n for n, h in files.items() if h]),
        "gate": "PASS" if total == 0 else "FAIL",
        "comments_strings_excluded": True,
        "parser": "ast.Call",
    }


def scan_raw_lexical_closure(project_dir=None):
    """Raw lexical scan over the synchronized dependency closure (comments/strings included)."""
    root = Path(project_dir) if project_dir else PROJ
    closure = build_d03b_dependency_closure(root)
    files = {}
    per_file_counts = {}
    total = 0
    for name in closure:
        hits = scan_file_raw_lexical(root / name)
        files[name] = hits
        per_file_counts[name] = len(hits)
        total += len(hits)
    return {
        "cloud_project": "CoreGrowth",
        "cloud_project_id": 27489898,
        "scanned_dependency_count": len(closure),
        "raw_lexical_forbidden_count": total,
        "per_file_counts": per_file_counts,
        "files": files,
        "violating_files": sorted([n for n, h in files.items() if h]),
        "gate": "PASS" if total == 0 else "FAIL",
        "comments_strings_docstrings_included": True,
        "pattern": "(?<![A-Za-z0-9_])open\\\\s*\\\\(",
        "note": "Matches QC whitelist-rejected bare file-open-call token; not identifiers like current_open",
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


def _selftest_ast_excludes_comments_strings():
    """AST executable scan must ignore comments/strings."""
    tok = "open"
    sample = (
        f"# {tok}(\"/tmp/x\")\n"
        f"s = \"{tok}('/tmp/y')\"\n"
        't = """Path.read_text()"""\n'
        "def f():\n"
        "    return 1\n"
    )
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "sample.py"
        p.write_text(sample, encoding="utf-8")
        return len(scan_file_executable_forbidden(p)) == 0


def _selftest_ast_detects_executable():
    tok = "open"
    sample = (
        "from pathlib import Path\n"
        "import io, os\n"
        "def f(path):\n"
        f"    {tok}(path)\n"
        f"    io.{tok}(path)\n"
        f"    os.{tok}(path, 0)\n"
        f"    Path(path).{tok}()\n"
        "    Path(path).read_text()\n"
        "    Path(path).read_bytes()\n"
    )
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "sample.py"
        p.write_text(sample, encoding="utf-8")
        hits = scan_file_executable_forbidden(p)
    kinds = sorted(h["kind"] for h in hits)
    expected = sorted(["OPEN", "IO_OPEN", "OS_OPEN", "PATH_OPEN", "READ_TEXT", "READ_BYTES"])
    return kinds == expected


def _selftest_raw_includes_comments_strings_docstrings():
    """Raw lexical scan must count comments/strings/docstrings."""
    tok = "open"
    sample = (
        f"# safe note about {tok}()\n"
        f"MSG = \"never call {tok}(here)\"\n"
        f'DOC = """also {tok}(in docstring)"""\n'
        "def f():\n"
        "    return 1\n"
    )
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "sample.py"
        p.write_text(sample, encoding="utf-8")
        hits = scan_file_raw_lexical(p)
    contexts = sorted(h["context"] for h in hits)
    return len(hits) == 3 and contexts == sorted(
        ["comment", "string", "docstring_or_string"]
    )


def _selftest_raw_ignores_identifier_suffix():
    """Identifiers like current_open( must not count as the bare lexeme."""
    sample = (
        "def current_open(self):\n"
        "    return None\n"
        "def after_market_open(spy, n):\n"
        "    return n\n"
        "x = self.current_open()\n"
    )
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "sample.py"
        p.write_text(sample, encoding="utf-8")
        hits = scan_file_raw_lexical(p)
    return len(hits) == 0


def run_cloudsafe_gate():
    scan = scan_closure()
    raw = scan_raw_lexical_closure()
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
    ok("CS02_ast_forbidden_zero", scan["executable_forbidden_call_count"] == 0,
       detail=str(scan["violating_files"]))
    ok("CS02b_raw_lexical_zero", raw["raw_lexical_forbidden_count"] == 0,
       detail=str(raw["violating_files"]))
    ok("CS03_pythonnet", py["pythonnet_ok"], detail=str(py["pythonnet_bases"]))
    ok("CS04_sizes", py["all_below_64000"], detail=str(py["above_64000"]))
    ok("CS05_ast_excludes_comments_strings", _selftest_ast_excludes_comments_strings())
    ok("CS06_ast_detects_executable", _selftest_ast_detects_executable())
    ok("CS07_raw_includes_comments_strings_docs", _selftest_raw_includes_comments_strings_docstrings())
    ok("CS08_raw_ignores_identifier_suffix", _selftest_raw_ignores_identifier_suffix())
    for name in (
        "cg_damage_duration_d03b_runtime.py",
        "cg_damage_duration_d03b_compact_export.py",
        "cg_damage_duration_d01_diag.py",
        "cg_damage_duration_d02_features.py",
        "cg_damage_duration_d02_memory.py",
        "cg_damage_duration_d02_sensor.py",
        "cg_damage_duration_d02_structure.py",
        "cg_damage_duration_d03a_shadow.py",
    ):
        ok("CS09_ast_clean_" + name, len(scan["files"].get(name) or []) == 0,
           detail=str(scan["files"].get(name)))
        ok("CS10_raw_clean_" + name, len(raw["files"].get(name) or []) == 0,
           detail=str(raw["files"].get(name)))
    return {
        "passed": passed,
        "failed": failed,
        "total": passed + failed,
        "rows": rows,
        "scan": scan,
        "raw_lexical": raw,
        "pythonnet_sizes": py,
    }


if __name__ == "__main__":
    r = run_cloudsafe_gate()
    print(json.dumps({k: r[k] for k in r if k != "rows"}, indent=2))
    for row in r["rows"]:
        if not row["pass"]:
            print("FAIL", row["name"], row["detail"])
