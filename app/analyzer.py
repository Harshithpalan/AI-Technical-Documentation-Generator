"""Offline documentation generator based on static analysis (no LLM required)."""
from __future__ import annotations

import ast
import json
import os
import re
from collections import Counter
from dataclasses import dataclass, field

from .ingest import SourceFile, file_tree

HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options", "route", "api_route", "websocket"}


@dataclass
class Symbol:
    kind: str  # function | class | method
    name: str
    signature: str
    doc: str = ""
    line: int = 0
    params: list[dict] = field(default_factory=list)
    returns: str = ""
    children: list["Symbol"] = field(default_factory=list)
    bases: list[str] = field(default_factory=list)


@dataclass
class Route:
    method: str
    path: str
    handler: str
    file: str


@dataclass
class ModuleInfo:
    path: str
    language: str
    doc: str = ""
    symbols: list[Symbol] = field(default_factory=list)
    imports: list[str] = field(default_factory=list)
    routes: list[Route] = field(default_factory=list)
    lines: int = 0


# ---------------------------------------------------------------- Python

def _unparse(node: ast.AST | None) -> str:
    return ast.unparse(node) if node is not None else ""


def _py_params(args: ast.arguments) -> list[dict]:
    params: list[dict] = []
    positional = args.posonlyargs + args.args
    defaults = [None] * (len(positional) - len(args.defaults)) + list(args.defaults)
    for a, d in zip(positional, defaults):
        params.append({"name": a.arg, "type": _unparse(a.annotation), "default": _unparse(d)})
    if args.vararg:
        params.append({"name": "*" + args.vararg.arg, "type": _unparse(args.vararg.annotation), "default": ""})
    for a, d in zip(args.kwonlyargs, args.kw_defaults):
        params.append({"name": a.arg, "type": _unparse(a.annotation), "default": _unparse(d)})
    if args.kwarg:
        params.append({"name": "**" + args.kwarg.arg, "type": _unparse(args.kwarg.annotation), "default": ""})
    return [p for p in params if p["name"] not in ("self", "cls")]


def _py_function(node: ast.FunctionDef | ast.AsyncFunctionDef, kind: str) -> Symbol:
    prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
    returns = _unparse(node.returns)
    sig = f"{prefix} {node.name}({_unparse(node.args)})" + (f" -> {returns}" if returns else "")
    return Symbol(kind, node.name, sig, ast.get_docstring(node) or "", node.lineno, _py_params(node.args), returns)


def analyze_python(f: SourceFile) -> ModuleInfo:
    info = ModuleInfo(f.path, f.language, lines=f.content.count("\n") + 1)
    try:
        tree = ast.parse(f.content)
    except SyntaxError:
        return info
    info.doc = ast.get_docstring(tree) or ""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            info.imports += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            info.imports.append("." * node.level + node.module)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for dec in node.decorator_list:
                if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute):
                    method = dec.func.attr.lower()
                    if method in HTTP_METHODS and dec.args and isinstance(dec.args[0], ast.Constant):
                        verbs = method.upper()
                        for kw in dec.keywords:
                            if kw.arg == "methods":
                                verbs = ", ".join(str(getattr(e, "value", "")) for e in getattr(kw.value, "elts", []))
                        if method == "route" and verbs == "ROUTE":
                            verbs = "GET"
                        info.routes.append(Route(verbs, str(dec.args[0].value), node.name, f.path))
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            info.symbols.append(_py_function(node, "function"))
        elif isinstance(node, ast.ClassDef):
            cls = Symbol("class", node.name, f"class {node.name}" + (
                f"({', '.join(_unparse(b) for b in node.bases)})" if node.bases else ""),
                ast.get_docstring(node) or "", node.lineno, bases=[_unparse(b) for b in node.bases])
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    cls.children.append(_py_function(item, "method"))
            info.symbols.append(cls)
    return info


# ---------------------------------------------------------------- JS / TS and generic

JS_PATTERNS = [
    ("class", re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:abstract\s+)?class\s+(\w+)(?:\s+extends\s+([\w.]+))?", re.M)),
    ("function", re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s*\*?\s*(\w+)\s*(\([^)]*\))", re.M)),
    ("function", re.compile(r"^\s*(?:export\s+)?(?:const|let|var)\s+(\w+)\s*(?::[^=]+)?=\s*(?:async\s+)?(\([^)]*\)|\w+)\s*(?::[^=]+)?=>", re.M)),
]
JS_IMPORT = re.compile(r"""(?:import\s[^'"]*?from\s*|import\s*|require\s*\(\s*)['"]([^'"]+)['"]""")
JS_ROUTE = re.compile(r"""\b(?:app|router|server|api)\.(get|post|put|patch|delete|all)\s*\(\s*['"`]([^'"`]+)['"`]""", re.I)

GENERIC_PATTERNS = {
    "go": [("function", re.compile(r"^func\s+(?:\([^)]*\)\s*)?(\w+)\s*(\([^)]*\))", re.M)),
           ("class", re.compile(r"^type\s+(\w+)\s+(struct|interface)", re.M))],
    "java": [("class", re.compile(r"^\s*(?:public\s+|abstract\s+|final\s+)*(?:class|interface|enum|record)\s+(\w+)", re.M)),
             ("function", re.compile(r"^\s*(?:public|protected)\s+(?:static\s+)?[\w<>\[\],\s]+?\s+(\w+)\s*(\([^)]*\))\s*(?:throws [\w, ]+)?\{", re.M))],
    "rust": [("function", re.compile(r"^\s*(?:pub\s+)?(?:async\s+)?fn\s+(\w+)\s*(?:<[^>]*>)?(\([^)]*\))", re.M)),
             ("class", re.compile(r"^\s*(?:pub\s+)?(?:struct|enum|trait)\s+(\w+)", re.M))],
    "ruby": [("class", re.compile(r"^\s*(?:class|module)\s+([\w:]+)", re.M)),
             ("function", re.compile(r"^\s*def\s+([\w.?!]+)\s*(\([^)]*\))?", re.M))],
    "php": [("class", re.compile(r"^\s*(?:abstract\s+|final\s+)?class\s+(\w+)", re.M)),
            ("function", re.compile(r"^\s*(?:public\s+|private\s+|protected\s+|static\s+)*function\s+(\w+)\s*(\([^)]*\))", re.M))],
    "csharp": [("class", re.compile(r"^\s*(?:public\s+|internal\s+|static\s+|abstract\s+|sealed\s+|partial\s+)*(?:class|interface|record|struct)\s+(\w+)", re.M)),
               ("function", re.compile(r"^\s*(?:public|protected|internal)\s+(?:static\s+|async\s+|virtual\s+|override\s+)*[\w<>\[\],?]+\s+(\w+)\s*(\([^)]*\))", re.M))],
}
GENERIC_PATTERNS["kotlin"] = [("class", re.compile(r"^\s*(?:data\s+|open\s+|abstract\s+)?class\s+(\w+)", re.M)),
                              ("function", re.compile(r"^\s*(?:suspend\s+)?fun\s+(\w+)\s*(\([^)]*\))", re.M))]
GENERIC_PATTERNS["c"] = GENERIC_PATTERNS["cpp"] = [
    ("function", re.compile(r"^[\w\*\s:<>]+?\s+\**(\w+)\s*(\([^;{)]*\))\s*\{", re.M))]

COMMENT_BEFORE = re.compile(r"(?:/\*\*(.*?)\*/|((?:^\s*(?://|#)[^\n]*\n)+))\s*$", re.S | re.M)


def _leading_comment(text: str, pos: int) -> str:
    m = COMMENT_BEFORE.search(text[max(0, pos - 1500):pos])
    if not m:
        return ""
    raw = m.group(1) or m.group(2) or ""
    lines = [re.sub(r"^\s*(?:\*|//|#)\s?", "", ln).rstrip() for ln in raw.splitlines()]
    return "\n".join(ln for ln in lines if ln).strip()


def analyze_regex(f: SourceFile) -> ModuleInfo:
    info = ModuleInfo(f.path, f.language, lines=f.content.count("\n") + 1)
    patterns = JS_PATTERNS if f.language in ("javascript", "typescript", "vue", "svelte") else GENERIC_PATTERNS.get(f.language, [])
    found: list[Symbol] = []
    seen: set[tuple[str, int]] = set()
    for kind, rx in patterns:
        for m in rx.finditer(f.content):
            name = m.group(1)
            if name in ("if", "for", "while", "switch", "return", "catch", "main") and kind == "function" and f.language != "go":
                continue
            line = f.content.count("\n", 0, m.start()) + 1
            if (name, line) in seen:
                continue
            seen.add((name, line))
            extra = m.group(2) if m.lastindex and m.lastindex >= 2 and m.group(2) else ""
            sig = m.group(0).strip().rstrip("{").strip()
            bases = [extra] if kind == "class" and extra and not extra.startswith("(") else []
            params = []
            if kind == "function" and extra.startswith("("):
                for p in [p.strip() for p in extra[1:-1].split(",") if p.strip()]:
                    name_part, _, default = p.partition("=")
                    pname, _, ptype = name_part.partition(":")
                    params.append({"name": pname.strip(), "type": ptype.strip(), "default": default.strip()})
            found.append(Symbol(kind, name, sig, _leading_comment(f.content, m.start()), line, params, bases=bases))
    info.symbols = sorted(found, key=lambda s: s.line)
    if f.language in ("javascript", "typescript", "vue", "svelte"):
        info.imports = JS_IMPORT.findall(f.content)
        info.routes = [Route(m.group(1).upper(), m.group(2), "", f.path) for m in JS_ROUTE.finditer(f.content)]
    return info


CODE_LANGS = {"python", "javascript", "typescript", "vue", "svelte", *GENERIC_PATTERNS.keys()}


def analyze(files: list[SourceFile]) -> list[ModuleInfo]:
    return [analyze_python(f) if f.language == "python" else analyze_regex(f) for f in files if f.language in CODE_LANGS]


# ---------------------------------------------------------------- project metadata

def detect_project(files: list[SourceFile]) -> dict:
    by_name = {os.path.basename(f.path).lower(): f for f in files if f.path.count("/") == 0} or {}
    meta: dict = {"dependencies": [], "scripts": {}, "install": [], "run": [], "test": [], "description": "", "name": ""}
    pkg = by_name.get("package.json")
    if pkg:
        try:
            data = json.loads(pkg.content)
            meta["name"] = data.get("name", "")
            meta["description"] = data.get("description", "")
            meta["dependencies"] += list((data.get("dependencies") or {}).keys())
            meta["scripts"] = data.get("scripts") or {}
            meta["install"].append("npm install")
            for key in ("dev", "start"):
                if key in meta["scripts"]:
                    meta["run"].append(f"npm run {key}" if key != "start" else "npm start")
            if "test" in meta["scripts"]:
                meta["test"].append("npm test")
        except ValueError:
            pass
    req = by_name.get("requirements.txt")
    if req:
        meta["dependencies"] += [re.split(r"[<>=\[;~! ]", ln.strip())[0] for ln in req.content.splitlines()
                                 if ln.strip() and not ln.strip().startswith(("#", "-"))]
        meta["install"].append("pip install -r requirements.txt")
    pyproject = by_name.get("pyproject.toml")
    if pyproject:
        m = re.search(r'^name\s*=\s*"([^"]+)"', pyproject.content, re.M)
        if m and not meta["name"]:
            meta["name"] = m.group(1)
        d = re.search(r'^description\s*=\s*"([^"]+)"', pyproject.content, re.M)
        if d and not meta["description"]:
            meta["description"] = d.group(1)
        if not req:
            meta["install"].append("pip install -e .")
    if "go.mod" in by_name:
        meta["install"].append("go mod download")
        meta["run"].append("go run .")
        meta["test"].append("go test ./...")
    if "cargo.toml" in by_name:
        meta["run"].append("cargo run")
        meta["test"].append("cargo test")
    if "pom.xml" in by_name:
        meta["install"].append("mvn install")
        meta["test"].append("mvn test")
    if "dockerfile" in by_name:
        meta["docker"] = True
    paths = [f.path for f in files]
    if any(p.endswith(".py") for p in paths):
        for cand in ("main.py", "app.py", "manage.py", "server.py", "cli.py", "app/main.py"):
            if cand in paths:
                if any("fastapi" in d.lower() for d in meta["dependencies"]):
                    mod = cand[:-3].replace("/", ".")
                    meta["run"].append(f"uvicorn {mod}:app --reload")
                else:
                    meta["run"].append(f"python {cand}")
                break
        if any(re.search(r"(^|/)tests?/|test_\w+\.py$", p) for p in paths):
            meta["test"].append("pytest")
    env_vars: set[str] = set()
    for f in files:
        env_vars.update(re.findall(r"""(?:os\.getenv|os\.environ\.get|os\.environ\[)\(?\s*['"]([A-Z][A-Z0-9_]+)['"]""", f.content))
        env_vars.update(re.findall(r"process\.env\.([A-Z][A-Z0-9_]+)", f.content))
        env_vars.update(re.findall(r"""os\.Getenv\(\s*"([A-Z][A-Z0-9_]+)"\)""", f.content))
        if os.path.basename(f.path).startswith(".env"):
            env_vars.update(re.findall(r"^([A-Z][A-Z0-9_]+)=", f.content, re.M))
    meta["env_vars"] = sorted(env_vars)
    meta["dependencies"] = sorted(set(d for d in meta["dependencies"] if d))
    return meta


def _first_line(text: str) -> str:
    return text.strip().split("\n\n")[0].replace("\n", " ") if text else ""


def _lang_stats(files: list[SourceFile]) -> list[tuple[str, int]]:
    c: Counter = Counter()
    for f in files:
        if f.language not in ("markdown", "text", "json", "yaml", "toml", "ini"):
            c[f.language] += f.content.count("\n") + 1
    return c.most_common()


# ---------------------------------------------------------------- renderers

NOTE = ("> Generated offline by static analysis. Connect an LLM provider in **Settings** for richer, "
        "narrative documentation.\n")


def _routes(modules: list[ModuleInfo]) -> list[Route]:
    return [r for m in modules for r in m.routes]


def _route_table(routes: list[Route]) -> str:
    rows = ["| Method | Path | Handler | File |", "|---|---|---|---|"]
    rows += [f"| `{r.method}` | `{r.path}` | {('`' + r.handler + '`') if r.handler else '—'} | `{r.file}` |" for r in routes]
    return "\n".join(rows)


def render_readme(files: list[SourceFile], modules: list[ModuleInfo], project: str) -> str:
    meta = detect_project(files)
    name = project or meta["name"] or "Project"
    existing = next((f for f in files if os.path.basename(f.path).lower().startswith("readme")), None)
    summary = meta["description"] or next((_first_line(m.doc) for m in modules if m.doc), "") or (
        _first_line(re.sub(r"^#.*\n", "", existing.content)) if existing else "")
    out = [f"# {name}", "", NOTE, summary or "_Add a short description of the project here._", ""]
    stats = _lang_stats(files)
    out += ["## Tech Stack", ""]
    out += [f"- **{lang.capitalize()}** ({lines:,} lines)" for lang, lines in stats[:6]]
    if meta["dependencies"]:
        out.append(f"- Key dependencies: {', '.join('`' + d + '`' for d in meta['dependencies'][:15])}")
    out.append("")
    features = []
    routes = _routes(modules)
    if routes:
        features.append(f"HTTP API with {len(routes)} endpoint(s)")
    for m in modules[:12]:
        if m.doc:
            features.append(f"`{m.path}` — {_first_line(m.doc)}")
    if features:
        out += ["## Features", ""] + [f"- {x}" for x in features] + [""]
    out += ["## Getting Started", "", "### Installation", "", "```bash"]
    out += meta["install"] or ["# Add installation steps here"]
    out += ["```", ""]
    if meta["env_vars"]:
        out += ["### Configuration", "", "| Variable | Description |", "|---|---|"]
        out += [f"| `{v}` | _describe_ |" for v in meta["env_vars"]] + [""]
    out += ["### Usage", "", "```bash"] + (meta["run"] or ["# Add run command here"]) + ["```", ""]
    if meta.get("docker"):
        out += ["### Docker", "", "```bash", f"docker build -t {name.lower().replace(' ', '-')} .",
                f"docker run -p 8000:8000 {name.lower().replace(' ', '-')}", "```", ""]
    if meta["test"]:
        out += ["### Running Tests", "", "```bash"] + meta["test"] + ["```", ""]
    if routes:
        out += ["## API Endpoints", "", _route_table(routes), ""]
    out += ["## Project Structure", "", "```", file_tree([f.path for f in files]), "```", ""]
    out += ["## Contributing", "", "Contributions are welcome. Please open an issue or pull request.", "",
            "## License", "", "_Specify the license here._", ""]
    return "\n".join(out)


def _render_symbol(s: Symbol, level: int) -> list[str]:
    hashes = "#" * level
    label = {"class": "class", "method": "method", "function": "function"}[s.kind]
    out = [f"{hashes} `{s.name}` <sub>{label}</sub>", "", f"```\n{s.signature}\n```", ""]
    out.append(s.doc.strip() if s.doc else "_No documentation provided._")
    out.append("")
    if s.params:
        out += ["| Parameter | Type | Default |", "|---|---|---|"]
        out += [f"| `{p['name']}` | {('`' + p['type'] + '`') if p['type'] else '—'} | "
                f"{('`' + p['default'] + '`') if p['default'] else '—'} |" for p in s.params]
        out.append("")
    if s.returns:
        out += [f"**Returns:** `{s.returns}`", ""]
    for c in s.children:
        if not c.name.startswith("_") or c.name == "__init__":
            out += _render_symbol(c, min(level + 1, 6))
    return out


def render_api(files: list[SourceFile], modules: list[ModuleInfo], project: str) -> str:
    out = [f"# {project or 'Project'} — API Reference", "", NOTE]
    routes = _routes(modules)
    if routes:
        out += ["## HTTP Endpoints", "", _route_table(routes), ""]
    documented = [m for m in modules if any(not s.name.startswith("_") for s in m.symbols)]
    if documented:
        out += ["## Contents", ""] + [f"- [`{m.path}`](#{re.sub(r'[^a-z0-9-]', '', m.path.lower().replace('/', '').replace('.', ''))})" for m in documented] + [""]
    for m in documented:
        out += [f"## {m.path}", ""]
        if m.doc:
            out += [m.doc.strip(), ""]
        for s in m.symbols:
            if not s.name.startswith("_"):
                out += _render_symbol(s, 3)
    if not documented and not routes:
        out.append("_No public symbols were detected in the provided source._")
    return "\n".join(out)


def _local_edges(modules: list[ModuleInfo]) -> list[tuple[str, str]]:
    index: dict[str, str] = {}
    for m in modules:
        base = os.path.splitext(m.path)[0]
        index[base.replace("/", ".")] = m.path
        index[os.path.basename(base)] = m.path
        if base.endswith("/__init__"):
            index[base[:-9].replace("/", ".")] = m.path
    edges: set[tuple[str, str]] = set()
    for m in modules:
        for imp in m.imports:
            key = imp.lstrip("./").replace("/", ".")
            key = re.sub(r"\.(js|ts|jsx|tsx)$", "", key)
            target = index.get(key) or index.get(key.split(".")[-1])
            if target and target != m.path:
                edges.add((m.path, target))
    return sorted(edges)


def _node_id(path: str) -> str:
    return re.sub(r"\W", "_", path)


def render_architecture(files: list[SourceFile], modules: list[ModuleInfo], project: str) -> str:
    meta = detect_project(files)
    out = [f"# {project or meta['name'] or 'Project'} — Architecture Overview", "", NOTE]
    out += ["## Overview", "", meta["description"] or next((_first_line(m.doc) for m in modules if m.doc), "") or
            f"The codebase contains {len(files)} files across {len({os.path.dirname(f.path) for f in files})} directories.", ""]
    dirs: dict[str, list[ModuleInfo]] = {}
    for m in modules:
        dirs.setdefault(os.path.dirname(m.path) or "(root)", []).append(m)
    out += ["## Components", "", "| Component | Files | Responsibility |", "|---|---|---|"]
    for d, mods in sorted(dirs.items()):
        resp = next((_first_line(m.doc) for m in mods if m.doc), "") or ", ".join(
            s.name for m in mods for s in m.symbols[:3])[:120]
        out.append(f"| `{d}` | {len(mods)} | {resp or '—'} |")
    out.append("")
    edges = _local_edges(modules)
    if edges:
        out += ["## Module Dependency Graph", "", "```mermaid", "flowchart LR"]
        nodes = sorted({p for e in edges for p in e})
        out += [f'    {_node_id(n)}["{n}"]' for n in nodes]
        out += [f"    {_node_id(a)} --> {_node_id(b)}" for a, b in edges[:80]]
        out += ["```", ""]
    routes = _routes(modules)
    if routes:
        out += ["## Request Flow", "", "```mermaid", "sequenceDiagram", "    participant C as Client", "    participant S as Server"]
        for r in routes[:8]:
            out += [f"    C->>S: {r.method} {r.path}", f"    S-->>C: response ({r.handler or 'handler'})"]
        out += ["```", "", _route_table(routes), ""]
    if meta["dependencies"]:
        out += ["## External Dependencies", ""] + [f"- `{d}`" for d in meta["dependencies"]] + [""]
    if meta["env_vars"]:
        out += ["## Configuration", ""] + [f"- `{v}`" for v in meta["env_vars"]] + [""]
    biggest = sorted(modules, key=lambda m: m.lines, reverse=True)[:5]
    undocumented = [s.name for m in modules for s in m.symbols if not s.doc and not s.name.startswith("_")]
    out += ["## Observations", ""]
    out += [f"- Largest modules: {', '.join(f'`{m.path}` ({m.lines} lines)' for m in biggest)}"] if biggest else []
    total = sum(1 for m in modules for s in m.symbols if not s.name.startswith("_"))
    if total:
        out.append(f"- Documentation coverage: {total - len(undocumented)}/{total} public top-level symbols have docstrings/comments.")
    out.append("")
    return "\n".join(out)


def _py_docstring_for(sym: Symbol) -> str:
    words = re.sub(r"(?<!^)(?=[A-Z])|_", " ", sym.name).strip().lower()
    summary = f"{words.capitalize()}." if sym.kind != "class" else f"{sym.name} class."
    lines = [summary]
    if sym.params:
        lines += ["", "Args:"] + [f"    {p['name'].lstrip('*')}{' (' + p['type'] + ')' if p['type'] else ''}: Description."
                                  for p in sym.params]
    if sym.returns and sym.returns != "None":
        lines += ["", "Returns:", f"    {sym.returns}: Description."]
    return "\n".join(lines)


def add_python_docstrings(f: SourceFile) -> str:
    try:
        tree = ast.parse(f.content)
    except SyntaxError:
        return f.content
    src = f.content.splitlines(keepends=True)
    inserts: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and not ast.get_docstring(node):
            if not node.body:
                continue
            first = node.body[0]
            if first.lineno == node.lineno:  # one-liner body
                continue
            indent = re.match(r"\s*", src[first.lineno - 1]).group(0)
            sym = (_py_function(node, "function") if not isinstance(node, ast.ClassDef)
                   else Symbol("class", node.name, "", ""))
            doc = _py_docstring_for(sym).replace("\n", "\n" + indent).replace(indent + "\n", "\n")
            doc = "\n".join(ln if ln.strip() else "" for ln in doc.split("\n"))
            inserts.append((first.lineno - 1, f'{indent}"""{doc}\n{indent}"""\n' if "\n" in doc else f'{indent}"""{doc}"""\n'))
    for idx, text in sorted(inserts, reverse=True):
        src.insert(idx, text)
    return "".join(src)


def add_jsdoc(f: SourceFile, module: ModuleInfo) -> str:
    lines = f.content.splitlines(keepends=True)
    for s in sorted(module.symbols, key=lambda s: s.line, reverse=True):
        if s.doc:
            continue
        indent = re.match(r"\s*", lines[s.line - 1]).group(0)
        words = re.sub(r"(?<!^)(?=[A-Z])|_", " ", s.name).strip().lower().capitalize()
        block = [f"{indent}/**", f"{indent} * {words}."]
        for p in s.params:
            n = p["name"].lstrip(".").strip("{}[] ")
            if n:
                block.append(f"{indent} * @param {{{p['type'] or '*'}}} {n} - Description.")
        block.append(f"{indent} */\n")
        lines.insert(s.line - 1, "\n".join(block))
    return "".join(lines)


def render_docstrings(files: list[SourceFile], modules: list[ModuleInfo], project: str) -> str:
    out = [f"# {project or 'Project'} — Documented Source", "", NOTE,
           "Docstring stubs were inserted for undocumented symbols. Replace the placeholder descriptions.", ""]
    mod_by_path = {m.path: m for m in modules}
    for f in files:
        if f.language == "python":
            code = add_python_docstrings(f)
        elif f.language in ("javascript", "typescript") and f.path in mod_by_path:
            code = add_jsdoc(f, mod_by_path[f.path])
        else:
            continue
        out += [f"### {f.path}", "", f"```{f.language}", code.rstrip("\n"), "```", ""]
    if len(out) == 5:
        out.append("_Offline docstring generation supports Python, JavaScript and TypeScript._")
    return "\n".join(out)


def render_guide(files: list[SourceFile], modules: list[ModuleInfo], project: str) -> str:
    meta = detect_project(files)
    name = project or meta["name"] or "the project"
    out = [f"# Developer Onboarding Guide — {name}", "", NOTE, "## 1. Local Setup", "", "```bash",
           "git clone <repository-url>", f"cd {name.lower().replace(' ', '-')}"]
    out += meta["install"] + ["```", ""]
    if meta["env_vars"]:
        out += ["Create a `.env` file (or export variables) for:", ""] + [f"- `{v}`" for v in meta["env_vars"]] + [""]
    out += ["## 2. Running", "", "```bash"] + (meta["run"] or ["# see README"]) + ["```", ""]
    out += ["## 3. Testing", "", "```bash"] + (meta["test"] or ["# no test runner detected"]) + ["```", ""]
    out += ["## 4. Codebase Tour", ""]
    for m in sorted(modules, key=lambda m: m.path):
        pub = [s.name for s in m.symbols if not s.name.startswith("_")][:6]
        desc = _first_line(m.doc) or (f"Defines {', '.join('`' + p + '`' for p in pub)}" if pub else "")
        out.append(f"- **`{m.path}`** — {desc or 'supporting code'}")
    out += ["", "## 5. Common Tasks", ""]
    if _routes(modules):
        r = _routes(modules)[0]
        out += [f"**Add an endpoint:** follow the pattern in `{r.file}` (e.g. `{r.method} {r.path}`), then add a test.", ""]
    out += ["**Add a feature:** locate the relevant module above, add code with docstrings, and cover it with tests.", "",
            "## 6. Troubleshooting", "", "- Make sure all dependencies are installed and environment variables are set.",
            "- Run the test suite before opening a pull request.", ""]
    return "\n".join(out)


RENDERERS = {
    "readme": render_readme,
    "api": render_api,
    "architecture": render_architecture,
    "docstrings": render_docstrings,
    "guide": render_guide,
    "custom": render_readme,
}


def generate_offline(doc_type: str, files: list[SourceFile], project: str = "") -> str:
    modules = analyze(files)
    return RENDERERS.get(doc_type, render_readme)(files, modules, project)
