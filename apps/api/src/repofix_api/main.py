from __future__ import annotations

from fastapi import FastAPI

app = FastAPI(title="RepoFix API", version="0.1.0")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "repofix-api"}