import multiprocessing
multiprocessing.freeze_support()

from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import logging

from app.core.config import settings
from app.core.dependencies import db_manager
from app.api.router import api_router
from app.api.routes.ws import router as ws_router

# ── Logging ──────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


# ── Lifespan (replaces deprecated @app.on_event) ─────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    # --- startup ---
    logger.info("Starting up application...")
    db_manager.init_db()
    try:
        from app.services.llm import ollama_client
        await ollama_client.warmup()
    except Exception as e:
        logger.warning(f"Ollama warmup skipped: {e}")

    yield

    # --- shutdown ---
    logger.info("Shutting down application...")
    try:
        from app.services.llm import ollama_client
        await ollama_client.close()
    except Exception:
        pass
    db_manager.close()


# ── Application ───────────────────────────────────────────────────
app = FastAPI(
    title="Offline Sales Analytics API",
    description="API for local offline sales analytics system with voice interface",
    version="0.1.0",
    lifespan=lifespan,
)

# Security headers middleware (registered first → runs last = outermost wrapper)
@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response

# CORS (registered after security middleware)
allowed_origins = [o.strip() for o in settings.ALLOWED_ORIGINS.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

# ── Routers ───────────────────────────────────────────────────────
app.include_router(api_router, prefix="/api/v1")
app.include_router(ws_router)


# ── Health endpoint ───────────────────────────────────────────────
@app.get("/health", tags=["system"])
async def health_check():
    try:
        db_manager.get_connection().execute("SELECT 1")
        return {"status": "ok", "db": "connected"}
    except Exception as e:
        return JSONResponse(
            status_code=503,
            content={"status": "degraded", "db": "disconnected", "error": str(e)}
        )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)

