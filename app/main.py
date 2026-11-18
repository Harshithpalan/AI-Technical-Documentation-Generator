"""FastAPI application: AI Technical Documentation Generator."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .analyzer import generate_offline
from .ingest import Collector, IngestError, SourceFile, build_bundle, detect_language, fetch_github
from .llm import PROVIDERS, LLMError, LLMSettings, server_has_key, stream_completion
from .prompts import AUDIENCES, DETAIL_LEVELS, DOC_TYPES, SYSTEM_PROMPT, build_user_prompt

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

app = FastAPI(title="AI Technical Documentation Generator", version="1.0.0")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


class FileIn(BaseModel):
    path: str
    content: str
    language: str | None = None


class GenerateRequest(BaseModel):
    files: list[FileIn] = Field(min_length=1)
    doc_type: str = "readme"
    project_name: str = ""
    audience: str = "developers"
    detail: str = "standard"
    output_language: str = "English"
    custom_instructions: str = ""
    provider: str = "offline"
    model: str = ""
    api_key: str = ""
    base_url: str = ""
    temperature: float = Field(0.3, ge=0, le=2)


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
async def health() -> dict:
    return {"status": "ok"}


@app.get("/api/config")
async def config() -> dict:
    default = os.getenv("LLM_PROVIDER") or next((p for p in ("openai", "anthropic", "gemini") if server_has_key(p)), "offline")
    return {
        "doc_types": {k: {"label": v["label"], "description": v["description"]} for k, v in DOC_TYPES.items()},
        "audiences": list(AUDIENCES),
        "detail_levels": list(DETAIL_LEVELS),
        "providers": {
            k: {"label": v["label"], "default_model": v["default_model"], "default_base_url": v["default_base_url"],
                "server_key": server_has_key(k)}
            for k, v in PROVIDERS.items()
        },
        "default_provider": default if default in PROVIDERS else "offline",
    }


@app.post("/api/ingest")
async def ingest(
    files: list[UploadFile] = File(default=[]),
    github_url: str = Form(default=""),
) -> dict:
    collector = Collector()
    project = ""
    try:
        for upload in files:
            data = await upload.read()
            name = upload.filename or "file"
            if name.lower().endswith(".zip"):
                collector.add_zip(data, strip_root=True)
                project = project or Path(name).stem
            elif detect_language(name) is None:
                collector.add(name + ".txt", data)
            else:
                collector.add(name, data)
        if github_url.strip():
            project = await fetch_github(github_url, collector, os.getenv("GITHUB_TOKEN"))
    except IngestError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not collector.files:
        raise HTTPException(status_code=400, detail="No supported source files were found.")
    return {"project_name": project, "files": [f.to_dict() for f in collector.files], "skipped": collector.skipped}


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event)}\n\n"


@app.post("/api/generate")
async def generate(req: GenerateRequest) -> StreamingResponse:
    if req.doc_type not in DOC_TYPES:
        raise HTTPException(status_code=400, detail=f"Unknown doc_type '{req.doc_type}'")
    if req.provider not in PROVIDERS:
        raise HTTPException(status_code=400, detail=f"Unknown provider '{req.provider}'")
    files = [SourceFile(f.path, f.content, f.language or detect_language(f.path) or "text") for f in req.files]

    async def events():
        if req.provider == "offline":
            text = await asyncio.to_thread(generate_offline, req.doc_type, files, req.project_name)
            yield _sse({"type": "meta", "provider": "offline", "model": "static-analysis", "truncated": []})
            for i in range(0, len(text), 400):
                yield _sse({"type": "chunk", "text": text[i:i + 400]})
            yield _sse({"type": "done"})
            return
        bundle, truncated = build_bundle(files)
        prompt = build_user_prompt(req.doc_type, bundle, req.project_name, req.audience, req.detail,
                                   req.custom_instructions, req.output_language)
        settings = LLMSettings(req.provider, req.model, req.api_key, req.base_url, req.temperature)
        try:
            resolved = settings.resolved()
            yield _sse({"type": "meta", "provider": resolved.provider, "model": resolved.model, "truncated": truncated})
            async for chunk in stream_completion(settings, SYSTEM_PROMPT, prompt):
                yield _sse({"type": "chunk", "text": chunk})
            yield _sse({"type": "done"})
        except LLMError as exc:
            yield _sse({"type": "error", "message": str(exc)})
        except Exception as exc:  # network errors etc.
            yield _sse({"type": "error", "message": f"{type(exc).__name__}: {exc}"})

    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})
