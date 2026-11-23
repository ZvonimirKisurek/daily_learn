from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from app.api.deps import build_pool
from app.api.mastery_routes import router as mastery_router
from app.api.routes import router
from app.config import Settings, get_settings


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Pool is created once per process at startup and closed at shutdown (not per request).
        pool = build_pool(settings)
        await pool.open(wait=True, timeout=15)
        app.state.pool = pool
        yield
        await pool.close()
        from app.db import close_pool  # the sync pool used by the grader's usage ledger (opened lazily)

        close_pool()

    app = FastAPI(title="MLE-Sprint API", version="0.1.0", lifespan=lifespan,
                  docs_url="/docs" if settings.enable_docs else None, redoc_url="/redoc" if settings.enable_docs else None,
                  openapi_url="/openapi.json" if settings.enable_docs else None)

    # CORS: browsers block a page on origin A from reading responses from origin B unless B opts in via
    # headers. Our default path avoids the problem (Next.js server components/actions call this API
    # server-to-server, and browsers only ever talk to Next.js). This allow-list exists for the cases where the
    # browser calls the API directly (local experiments, a client-side fetch). Explicit origins, no wildcard,
    # no credentials (we use a bearer header, not cookies), and only the verbs/headers we actually use (PUT = notebook).
    # In production set FRONTEND_URL (the Vercel origin); it is added to CORS_ORIGINS. The deployed app still works with
    # CORS misconfigured, because the browser never calls this API directly - Vercel's server does, and CORS is browser-only.
    app.add_middleware(
        CORSMiddleware, allow_origins=settings.cors_origin_list, allow_methods=["GET", "POST", "PUT"],
        allow_headers=["Authorization", "Content-Type"], allow_credentials=False, max_age=600,
    )
    app.include_router(router)
    app.include_router(mastery_router)

    @app.get("/health", tags=["ops"])
    async def health():
        try:
            async with app.state.pool.connection() as conn:
                await conn.execute("select 1")
        except Exception as exc:
            raise HTTPException(503, "database unavailable") from exc
        return {"status": "ok"}

    return app


app = create_app()
