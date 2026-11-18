import io
import json
import zipfile

from fastapi.testclient import TestClient

from app.analyzer import add_python_docstrings, analyze, generate_offline
from app.ingest import Collector, SourceFile, build_bundle, parse_github_url
from app.main import app
from app.prompts import build_user_prompt

client = TestClient(app)

PY = SourceFile("app/main.py", '''"""Demo service."""
from fastapi import FastAPI
app = FastAPI()


@app.get("/items/{item_id}")
def read_item(item_id: int, q: str | None = None) -> dict:
    return {"id": item_id}


class Store:
    def add(self, x: int) -> None:
        pass
''', "python")
JS = SourceFile("src/server.js", '''const express = require("express");
const app = express();
/** Say hello. */
function hello(name, greeting = "hi") { return greeting + name; }
app.post("/users", (req, res) => res.json({}));
export const add = (a, b) => a + b;
''', "javascript")


def _events(resp):
    return [json.loads(line[5:]) for line in resp.text.split("\n\n") if line.startswith("data:")]


def test_python_analysis_detects_symbols_and_routes():
    mod = analyze([PY])[0]
    assert mod.doc == "Demo service."
    assert [s.name for s in mod.symbols] == ["read_item", "Store"]
    assert mod.routes[0].method == "GET" and mod.routes[0].path == "/items/{item_id}"
    assert mod.symbols[0].params[1] == {"name": "q", "type": "str | None", "default": "None"}


def test_js_analysis():
    mod = analyze([JS])[0]
    names = {s.name for s in mod.symbols}
    assert {"hello", "add"} <= names
    assert next(s for s in mod.symbols if s.name == "hello").doc == "Say hello."
    assert mod.routes[0].path == "/users"


def test_offline_docs_for_every_type():
    for doc_type in ("readme", "api", "architecture", "docstrings", "guide", "custom"):
        md = generate_offline(doc_type, [PY, JS], "Demo")
        assert md.startswith("#") and "Demo" in md


def test_docstrings_inserted_and_valid_python():
    import ast
    out = add_python_docstrings(PY)
    tree = ast.parse(out)
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "read_item")
    assert "Args:" in ast.get_docstring(fn)


def test_bundle_and_prompt():
    bundle, truncated = build_bundle([PY, JS], max_chars=100_000)
    assert "### app/main.py" in bundle and not truncated
    prompt = build_user_prompt("api", bundle, "Demo", "beginners", "concise")
    assert "API reference" in prompt and "beginner" in prompt


def test_collector_filters_and_zip():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("repo-main/src/a.py", "x = 1\n")
        z.writestr("repo-main/node_modules/b.js", "x")
        z.writestr("repo-main/img.png", b"\x89PNG\x00\x00")
    c = Collector()
    c.add_zip(buf.getvalue(), strip_root=True)
    assert [f.path for f in c.files] == ["src/a.py"]


def test_parse_github_url():
    assert parse_github_url("https://github.com/o/r") == ("o", "r", "HEAD", "")
    assert parse_github_url("github.com/o/r.git") == ("o", "r", "HEAD", "")
    assert parse_github_url("https://github.com/o/r/tree/dev/src/lib") == ("o", "r", "dev", "src/lib")


def test_config_endpoint():
    data = client.get("/api/config").json()
    assert "readme" in data["doc_types"] and "offline" in data["providers"]


def test_ingest_upload():
    r = client.post("/api/ingest", files=[("files", ("main.py", b"def f():\n    pass\n", "text/plain"))])
    assert r.status_code == 200 and r.json()["files"][0]["language"] == "python"


def test_generate_offline_stream():
    r = client.post("/api/generate", json={"files": [{"path": PY.path, "content": PY.content}], "doc_type": "api"})
    events = _events(r)
    assert events[0]["type"] == "meta" and events[-1]["type"] == "done"
    assert "read_item" in "".join(e.get("text", "") for e in events)


def test_generate_without_key_returns_error_event(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    r = client.post("/api/generate", json={"files": [{"path": "a.py", "content": "x=1"}], "provider": "openai"})
    assert _events(r)[-1]["type"] == "error"


def test_generate_validation():
    assert client.post("/api/generate", json={"files": []}).status_code == 422
    assert client.post("/api/generate", json={"files": [{"path": "a", "content": "b"}], "doc_type": "x"}).status_code == 400
