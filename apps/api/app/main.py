from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.config import get_settings
from app.db import engine
from app.redis_client import redis
from app.routers import auth as auth_router
from app.routers import cadastros as cadastros_router
from app.routers import companies as companies_router
from app.routers import integrations as integrations_router
from app.routers import jobs as jobs_router
from app.routers import oauth as oauth_router
from app.routers import products as products_router
from app.routers import stores as stores_router
from app.routers import sync as sync_router
from app.routers import users as users_router
from app.services.bootstrap import promote_owner_if_needed
from app.worker_pool import close_arq_pool

logger = structlog.get_logger()
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("startup", env=settings.env)
    try:
        await promote_owner_if_needed()
    except Exception as e:
        logger.warning("bootstrap_owner_failed", err=str(e))
    yield
    await close_arq_pool()
    await engine.dispose()
    await redis.aclose()
    logger.info("shutdown")


app = FastAPI(
    title="DaVinci API",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)

# In prod, web and api share the same origin (path-routing on app.hadken.com),
# so CORS is largely a no-op. Still set allow_origins explicitly for any
# cross-origin SSR fetches and to satisfy strict browsers.
_DEV_ORIGINS = [
    "http://localhost:3000", "http://127.0.0.1:3000",
    "http://localhost:3001", "http://127.0.0.1:3001",
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.app_url] if settings.is_prod else _DEV_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router.router)
app.include_router(users_router.router)
app.include_router(companies_router.router)
app.include_router(stores_router.router)
app.include_router(cadastros_router.router)
app.include_router(integrations_router.router)
app.include_router(oauth_router.router)
app.include_router(products_router.router)
app.include_router(jobs_router.router)
app.include_router(sync_router.router)


@app.get("/api/health")
async def health() -> dict:
    pg_ok = False
    redis_ok = False
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        pg_ok = True
    except Exception as e:
        logger.warning("postgres_health_failed", err=str(e))
    try:
        await redis.ping()
        redis_ok = True
    except Exception as e:
        logger.warning("redis_health_failed", err=str(e))
    status = "ok" if pg_ok and redis_ok else "degraded"
    return {"status": status, "postgres": "ok" if pg_ok else "fail", "redis": "ok" if redis_ok else "fail"}
