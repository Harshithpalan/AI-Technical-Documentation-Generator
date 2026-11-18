(() => {
  "use strict";
  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => document.querySelectorAll(sel);

  const state = {
    files: [],
    docType: "readme",
    markdown: "",
    config: null,
    settings: JSON.parse(localStorage.getItem("docgen.settings") || "null"),
    history: JSON.parse(localStorage.getItem("docgen.history") || "[]"),
    controller: null,
  };

  const LANG_BY_EXT = {
    py: "python", js: "javascript", jsx: "javascript", ts: "typescript", tsx: "typescript", java: "java",
    go: "go", rs: "rust", rb: "ruby", php: "php", cs: "csharp", c: "c", cpp: "cpp", h: "c", kt: "kotlin",
    swift: "swift", sh: "bash", sql: "sql", html: "html", css: "css", json: "json", yml: "yaml", yaml: "yaml",
    md: "markdown", toml: "toml",
  };

  // ------------------------------------------------------------------ utils
  function toast(msg, isError = false) {
    const t = $("#toast");
    t.textContent = msg;
    t.className = "toast show" + (isError ? " error" : "");
    clearTimeout(t._timer);
    t._timer = setTimeout(() => (t.className = "toast"), 3200);
  }
  const fmtSize = (n) => (n < 1024 ? `${n} B` : `${(n / 1024).toFixed(1)} KB`);
  const escapeHtml = (s) => s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  function setStatus(text, cls = "") {
    const s = $("#status");
    s.textContent = text;
    s.className = "status " + cls;
  }

  // ------------------------------------------------------------------ theme
  const savedTheme = localStorage.getItem("docgen.theme");
  if (savedTheme) document.documentElement.dataset.theme = savedTheme;
  $("#themeBtn").onclick = () => {
    const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    localStorage.setItem("docgen.theme", next);
    if (state.markdown) renderPreview(state.markdown, true);
  };

  // ------------------------------------------------------------------ tabs
  $$(".input-panel .tab").forEach((btn) => {
    btn.onclick = () => {
      $$(".input-panel .tab").forEach((b) => b.classList.toggle("active", b === btn));
      $$(".input-panel .tab-body").forEach((b) => b.classList.toggle("hidden", b.id !== `tab-${btn.dataset.tab}`));
    };
  });
  $$(".output-toolbar .tab").forEach((btn) => {
    btn.onclick = () => {
      $$(".output-toolbar .tab").forEach((b) => b.classList.toggle("active", b === btn));
      showView(btn.dataset.view);
    };
  });
  function showView(view) {
    const has = !!state.markdown;
    $("#emptyState").classList.toggle("hidden", has);
    $("#preview").classList.toggle("hidden", !has || view !== "preview");
    $("#markdown").classList.toggle("hidden", !has || view !== "markdown");
  }
  const currentView = () => $(".output-toolbar .tab.active").dataset.view;

  // ------------------------------------------------------------------ files
  function addFiles(newFiles) {
    for (const f of newFiles) {
      const idx = state.files.findIndex((x) => x.path === f.path);
      if (idx >= 0) state.files[idx] = f;
      else state.files.push(f);
    }
    renderFiles();
  }
  function renderFiles() {
    const list = $("#fileList");
    list.innerHTML = "";
    state.files.forEach((f, i) => {
      const li = document.createElement("li");
      li.innerHTML = `<span class="path" title="${escapeHtml(f.path)}">${escapeHtml(f.path)}</span>
        <span class="meta"><span>${escapeHtml(f.language || "")}</span><span>${fmtSize(f.content.length)}</span>
        <button class="remove" title="Remove" data-i="${i}">×</button></span>`;
      list.appendChild(li);
    });
    list.querySelectorAll(".remove").forEach((b) => (b.onclick = () => { state.files.splice(+b.dataset.i, 1); renderFiles(); }));
    const total = state.files.reduce((a, f) => a + f.content.length, 0);
    $("#fileCount").textContent = state.files.length ? `${state.files.length} file(s) · ${fmtSize(total)}` : "No files yet";
  }
  $("#clearFilesBtn").onclick = () => { state.files = []; renderFiles(); };

  $("#addPasteBtn").onclick = () => {
    const code = $("#pasteCode").value;
    const name = $("#pasteName").value.trim() || "snippet.txt";
    if (!code.trim()) return toast("Paste some code first", true);
    const ext = name.split(".").pop().toLowerCase();
    addFiles([{ path: name, content: code, language: LANG_BY_EXT[ext] || "text" }]);
    $("#pasteCode").value = "";
    toast(`Added ${name}`);
  };

  async function uploadToServer(formData, label) {
    setStatus(`Importing ${label}…`);
    try {
      const res = await fetch("/api/ingest", { method: "POST", body: formData });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Import failed");
      addFiles(data.files);
      if (data.project_name && !$("#projectName").value) $("#projectName").value = data.project_name;
      setStatus("");
      toast(`Imported ${data.files.length} file(s)` + (data.skipped ? `, skipped ${data.skipped}` : ""));
    } catch (e) {
      setStatus("");
      toast(e.message, true);
    }
  }
  function handleFileList(fileList) {
    if (!fileList.length) return;
    const fd = new FormData();
    for (const f of fileList) fd.append("files", f, f.webkitRelativePath || f.name);
    uploadToServer(fd, `${fileList.length} file(s)`);
  }
  const dz = $("#dropzone");
  $("#fileInput").onchange = (e) => { handleFileList(e.target.files); e.target.value = ""; };
  dz.ondragover = (e) => { e.preventDefault(); dz.classList.add("drag"); };
  dz.ondragleave = () => dz.classList.remove("drag");
  dz.ondrop = (e) => { e.preventDefault(); dz.classList.remove("drag"); handleFileList(e.dataTransfer.files); };

  $("#githubBtn").onclick = async () => {
    const url = $("#githubUrl").value.trim();
    if (!url) return toast("Enter a GitHub URL", true);
    const btn = $("#githubBtn");
    btn.classList.add("loading"); btn.disabled = true;
    const fd = new FormData();
    fd.append("github_url", url);
    await uploadToServer(fd, url);
    btn.classList.remove("loading"); btn.disabled = false;
  };

  $("#sampleBtn").onclick = () => {
    addFiles(SAMPLE);
    $("#projectName").value = $("#projectName").value || "Todo API";
    toast("Sample project loaded");
  };

  // ------------------------------------------------------------------ doc types
  function renderDocTypes() {
    const box = $("#docTypes");
    box.innerHTML = "";
    for (const [key, t] of Object.entries(state.config.doc_types)) {
      const b = document.createElement("button");
      b.className = "doc-type" + (key === state.docType ? " active" : "");
      b.innerHTML = `<b>${escapeHtml(t.label)}</b><small>${escapeHtml(t.description)}</small>`;
      b.onclick = () => { state.docType = key; renderDocTypes(); };
      box.appendChild(b);
    }
  }

  // ------------------------------------------------------------------ settings
  function defaultSettings() {
    const p = state.config.default_provider;
    const spec = state.config.providers[p];
    return { provider: p, model: spec.default_model, apiKey: "", baseUrl: spec.default_base_url, temperature: 0.3, keys: {} };
  }
  function updateBadge() {
    const s = state.settings;
    const badge = $("#providerBadge");
    badge.textContent = s.provider === "offline" ? "Offline mode" : `${s.provider} · ${s.model}`;
    badge.classList.toggle("live", s.provider !== "offline");
  }
  function fillSettingsForm(provider) {
    const s = state.settings;
    const spec = state.config.providers[provider];
    const same = provider === s.provider;
    $("#provider").value = provider;
    $("#model").value = same ? s.model : spec.default_model;
    $("#apiKey").value = (s.keys || {})[provider] || "";
    $("#baseUrl").value = same ? s.baseUrl : spec.default_base_url;
    $("#temperature").value = s.temperature;
    $("#tempVal").textContent = s.temperature;
    $("#serverKeyNote").textContent = spec.server_key ? "(server key configured — optional)" : "";
    $("#llmFields").classList.toggle("hidden", provider === "offline");
  }
  $("#settingsBtn").onclick = () => {
    const sel = $("#provider");
    sel.innerHTML = Object.entries(state.config.providers)
      .map(([k, v]) => `<option value="${k}">${escapeHtml(v.label)}</option>`).join("");
    fillSettingsForm(state.settings.provider);
    $("#settingsDialog").showModal();
  };
  $("#provider").onchange = (e) => fillSettingsForm(e.target.value);
  $("#temperature").oninput = (e) => ($("#tempVal").textContent = e.target.value);
  $("#settingsForm").onsubmit = (e) => {
    if (e.submitter && e.submitter.value !== "save") return;
    const provider = $("#provider").value;
    const keys = { ...(state.settings.keys || {}), [provider]: $("#apiKey").value.trim() };
    state.settings = {
      provider, keys,
      model: $("#model").value.trim(),
      baseUrl: $("#baseUrl").value.trim(),
      temperature: parseFloat($("#temperature").value),
    };
    localStorage.setItem("docgen.settings", JSON.stringify(state.settings));
    updateBadge();
    toast("Settings saved");
  };

  // ------------------------------------------------------------------ rendering
  let mermaidReady = false;
  function initMermaid() {
    if (!window.mermaid) return;
    mermaid.initialize({ startOnLoad: false, theme: "default", securityLevel: "strict" });
    mermaidReady = true;
  }
  let renderTimer = null;
  function renderPreview(md, final = false) {
    const el = $("#preview");
    if (!window.marked) { el.textContent = md; return; }
    const html = marked.parse(md, { gfm: true, breaks: false });
    el.innerHTML = window.DOMPurify ? DOMPurify.sanitize(html) : html;
    el.querySelectorAll("pre code").forEach((block) => {
      const isMermaid = block.classList.contains("language-mermaid");
      if (isMermaid && final && mermaidReady) {
        const div = document.createElement("div");
        div.className = "mermaid";
        div.textContent = block.textContent;
        block.parentElement.replaceWith(div);
      } else if (!isMermaid && window.hljs) {
        try { hljs.highlightElement(block); } catch (_) { /* unknown language */ }
      }
    });
    if (final && mermaidReady) {
      mermaid.run({ nodes: el.querySelectorAll(".mermaid") }).catch(() => {});
    }
  }
  function scheduleRender() {
    if (renderTimer) return;
    renderTimer = setTimeout(() => {
      renderTimer = null;
      renderPreview(state.markdown);
      $("#markdown").value = state.markdown;
      const p = $("#preview");
      if (p.scrollHeight - p.scrollTop - p.clientHeight < 200) p.scrollTop = p.scrollHeight;
    }, 120);
  }
  function setOutput(md) {
    state.markdown = md;
    $("#markdown").value = md;
    renderPreview(md, true);
    showView(currentView());
    ["#copyBtn", "#downloadBtn", "#htmlBtn"].forEach((s) => ($(s).disabled = !md));
  }
  $("#markdown").oninput = (e) => {
    state.markdown = e.target.value;
    renderPreview(state.markdown, true);
  };

  // ------------------------------------------------------------------ generate
  async function generate() {
    if (state.controller) { state.controller.abort(); return; }
    if (!state.files.length) {
      const code = $("#pasteCode").value;
      if (code.trim()) $("#addPasteBtn").click();
      else return toast("Add some code first", true);
    }
    const s = state.settings;
    const body = {
      files: state.files.map(({ path, content, language }) => ({ path, content, language })),
      doc_type: state.docType,
      project_name: $("#projectName").value.trim(),
      audience: $("#audience").value,
      detail: $("#detail").value,
      output_language: $("#outputLanguage").value.trim() || "English",
      custom_instructions: $("#customInstructions").value,
      provider: s.provider,
      model: s.model,
      api_key: (s.keys || {})[s.provider] || "",
      base_url: s.baseUrl,
      temperature: s.temperature,
    };
    if (body.doc_type === "custom" && !body.custom_instructions.trim() && s.provider !== "offline") {
      return toast("Add instructions for a custom document", true);
    }
    const btn = $("#generateBtn");
    state.controller = new AbortController();
    btn.textContent = "Stop generating";
    state.markdown = "";
    showView(currentView());
    $("#emptyState").classList.add("hidden");
    $("#preview").classList.add("streaming");
    $("#preview").innerHTML = "";
    setStatus("Generating…");
    const started = performance.now();
    let errored = false;
    try {
      const res = await fetch("/api/generate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
        signal: state.controller.signal,
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail || res.statusText));
      }
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let idx;
        while ((idx = buffer.indexOf("\n\n")) >= 0) {
          const raw = buffer.slice(0, idx);
          buffer = buffer.slice(idx + 2);
          if (!raw.startsWith("data:")) continue;
          const ev = JSON.parse(raw.slice(5));
          if (ev.type === "chunk") { state.markdown += ev.text; scheduleRender(); }
          else if (ev.type === "meta") {
            setStatus(`Generating with ${ev.model}…`);
            if (ev.truncated && ev.truncated.length) toast(`Input too large: ${ev.truncated.length} file(s) truncated`);
          } else if (ev.type === "error") throw new Error(ev.message);
        }
      }
    } catch (e) {
      if (e.name === "AbortError") setStatus("Stopped", "");
      else { errored = true; setStatus("Error", "error"); toast(e.message, true); }
    } finally {
      state.controller = null;
      btn.textContent = "Generate documentation";
      $("#preview").classList.remove("streaming");
      clearTimeout(renderTimer); renderTimer = null;
      setOutput(state.markdown);
      if (state.markdown && !errored) {
        const secs = ((performance.now() - started) / 1000).toFixed(1);
        setStatus(`Done in ${secs}s · ${state.markdown.split(/\s+/).length} words`, "ok");
        saveHistory();
      }
    }
  }
  $("#generateBtn").onclick = generate;
  document.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key === "Enter") generate();
  });

  // ------------------------------------------------------------------ export
  const fileBase = () => `${($("#projectName").value.trim() || "documentation").replace(/\W+/g, "-").toLowerCase()}-${state.docType}`;
  function download(name, content, type) {
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([content], { type }));
    a.download = name;
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  }
  $("#copyBtn").onclick = async () => {
    try { await navigator.clipboard.writeText(state.markdown); toast("Copied to clipboard"); }
    catch { $("#markdown").select(); document.execCommand("copy"); toast("Copied"); }
  };
  $("#downloadBtn").onclick = () => download(`${fileBase()}.md`, state.markdown, "text/markdown");
  $("#htmlBtn").onclick = () => {
    const html = `<!doctype html><html><head><meta charset="utf-8"><title>${escapeHtml(fileBase())}</title>
<style>body{font:15px/1.65 system-ui,sans-serif;max-width:900px;margin:40px auto;padding:0 20px;color:#1b2437}
pre{background:#f6f8fc;padding:14px;border-radius:8px;overflow:auto}code{font-family:ui-monospace,monospace}
table{border-collapse:collapse}td,th{border:1px solid #dde3f0;padding:6px 10px}blockquote{border-left:4px solid #6d8cff;margin:0;padding:6px 14px;color:#66718a}</style>
</head><body>${$("#preview").innerHTML}</body></html>`;
    download(`${fileBase()}.html`, html, "text/html");
  };

  // ------------------------------------------------------------------ history
  function saveHistory() {
    const label = state.config.doc_types[state.docType].label;
    state.history.unshift({
      id: Date.now(), docType: state.docType, label,
      project: $("#projectName").value.trim() || state.files[0]?.path || "Untitled",
      provider: state.settings.provider, markdown: state.markdown,
    });
    state.history = state.history.slice(0, 20);
    try { localStorage.setItem("docgen.history", JSON.stringify(state.history)); }
    catch { state.history.pop(); }
    renderHistory();
  }
  function renderHistory() {
    const list = $("#historyList");
    list.innerHTML = state.history.length ? "" : `<li class="empty-h">No documents generated yet.</li>`;
    for (const h of state.history) {
      const li = document.createElement("li");
      li.innerHTML = `<b>${escapeHtml(h.label)}</b> — ${escapeHtml(h.project)}
        <small>${new Date(h.id).toLocaleString()} · ${escapeHtml(h.provider)}</small>`;
      li.onclick = () => { state.docType = h.docType; renderDocTypes(); setOutput(h.markdown); $("#historyDrawer").classList.remove("open"); };
      list.appendChild(li);
    }
  }
  $("#historyBtn").onclick = () => $("#historyDrawer").classList.toggle("open");
  $("#closeHistoryBtn").onclick = () => $("#historyDrawer").classList.remove("open");
  $("#clearHistoryBtn").onclick = () => { state.history = []; localStorage.removeItem("docgen.history"); renderHistory(); };

  // ------------------------------------------------------------------ sample
  const SAMPLE = [
    { path: "requirements.txt", language: "text", content: "fastapi>=0.110\nuvicorn>=0.29\nsqlalchemy>=2.0\n" },
    { path: "app/main.py", language: "python", content: `"""Todo API: a small FastAPI service for managing tasks."""
import os
from fastapi import FastAPI, HTTPException
from .models import Todo, TodoCreate
from .store import TodoStore

app = FastAPI(title="Todo API")
store = TodoStore(os.getenv("DATABASE_URL", "sqlite:///todos.db"))


@app.get("/todos")
def list_todos(done: bool | None = None) -> list[Todo]:
    """Return all todos, optionally filtered by completion status."""
    return store.list(done=done)


@app.post("/todos", status_code=201)
def create_todo(payload: TodoCreate) -> Todo:
    return store.create(payload.title, payload.priority)


@app.patch("/todos/{todo_id}/complete")
def complete_todo(todo_id: int) -> Todo:
    todo = store.complete(todo_id)
    if todo is None:
        raise HTTPException(status_code=404, detail="Todo not found")
    return todo


@app.delete("/todos/{todo_id}", status_code=204)
def delete_todo(todo_id: int) -> None:
    if not store.delete(todo_id):
        raise HTTPException(status_code=404, detail="Todo not found")
` },
    { path: "app/models.py", language: "python", content: `"""Pydantic models for the Todo API."""
from pydantic import BaseModel, Field


class TodoCreate(BaseModel):
    """Payload for creating a todo."""
    title: str = Field(min_length=1, max_length=200)
    priority: int = Field(default=1, ge=1, le=5)


class Todo(TodoCreate):
    id: int
    done: bool = False
` },
    { path: "app/store.py", language: "python", content: `"""In-memory persistence layer for todos."""
from .models import Todo


class TodoStore:
    """Stores todos and assigns incremental ids."""

    def __init__(self, url: str):
        self.url = url
        self._items: dict[int, Todo] = {}
        self._next_id = 1

    def list(self, done: bool | None = None) -> list[Todo]:
        items = list(self._items.values())
        return [t for t in items if done is None or t.done == done]

    def create(self, title: str, priority: int = 1) -> Todo:
        todo = Todo(id=self._next_id, title=title, priority=priority)
        self._items[todo.id] = todo
        self._next_id += 1
        return todo

    def complete(self, todo_id: int) -> Todo | None:
        todo = self._items.get(todo_id)
        if todo:
            todo.done = True
        return todo

    def delete(self, todo_id: int) -> bool:
        return self._items.pop(todo_id, None) is not None
` },
  ];

  // ------------------------------------------------------------------ init
  async function init() {
    initMermaid();
    try {
      const res = await fetch("/api/config");
      state.config = await res.json();
    } catch {
      toast("Could not reach the server", true);
      return;
    }
    if (!state.settings || !state.config.providers[state.settings.provider]) state.settings = defaultSettings();
    renderDocTypes();
    updateBadge();
    renderFiles();
    renderHistory();
  }
  init();
})();
