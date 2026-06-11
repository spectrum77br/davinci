from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.config import get_settings
from app.db import engine
from app.redis_client import redis
from app.routers import alerts as alerts_router
from app.routers import audit as audit_router
from app.routers import auth as auth_router
from app.routers import cadastros as cadastros_router
from app.routers import companies as companies_router
from app.routers import dashboard as dashboard_router
from app.routers import dev as dev_router
from app.routers import devolutions as devolutions_router
from app.routers import discrepancies as discrepancies_router
from app.routers import estoque as estoque_router
from app.routers import faturamento as faturamento_router
from app.routers import financeiro as financeiro_router
from app.routers import importacao as importacao_router
from app.routers import integrations as integrations_router
from app.routers import jobs as jobs_router
from app.routers import listings as listings_router
from app.routers import margem_audit as margem_audit_router
from app.routers import margens as margens_router
from app.routers import metrics as metrics_router
from app.routers import nf_upload as nf_upload_router
from app.routers import oauth as oauth_router
from app.routers import pricing as pricing_router
from app.routers import products as products_router
from app.routers import refunds as refunds_router
from app.routers import segments as segments_router
from app.routers import settings as settings_router
from app.routers import stores as stores_router
from app.routers import sync as sync_router
from app.routers import tarefas as tarefas_router
from app.routers import users as users_router
from app.routers import webhooks as webhooks_router
from app.services.bootstrap import promote_owner_if_needed
from app.services.sentry import init_sentry
from app.worker_pool import close_arq_pool

logger = structlog.get_logger()
settings = get_settings()
init_sentry(component="api")


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


_OPENAPI_TAGS = [
    {"name": "auth", "description": "Email-OTP login + JWT session."},
    {"name": "users", "description": "User accounts, RBAC, admin operations."},
    {"name": "companies", "description": "Tenant companies (razão social / CNPJ)."},
    {"name": "stores", "description": "Marketplaces lojas attached to a company."},
    {"name": "cadastros", "description": "Cadastros (telefone / e-mail / domínio)."},
    {"name": "integrations", "description": "Marketplace integrations + OAuth credentials."},
    {"name": "oauth", "description": "OAuth callback handlers."},
    {"name": "products", "description": "Local catalog (products + product_links)."},
    {"name": "sync", "description": "Triggered syncs and sync_logs."},
    {"name": "jobs", "description": "Background job status and enqueue."},
    {"name": "webhooks", "description": "Inbound marketplace webhooks (Bling first)."},
    {"name": "alerts", "description": "User alerts feed."},
    {"name": "listings", "description": "Cached marketplace listings."},
    {"name": "pricing", "description": "Catálogo, regras, push de preços, auditorias."},
    {"name": "segments", "description": "Taxonomia hierárquica (Celular/Mala/..., subtipos)."},
    {"name": "audit", "description": "Audit by spreadsheet — runs, items, fixes."},
    {"name": "discrepancies", "description": "Cross-platform stock divergences."},
    {"name": "dashboard", "description": "Onboarding + KPIs do dashboard."},
    {"name": "settings", "description": "User preferences (Telegram, daily sync, etc)."},
    {"name": "metrics", "description": "Observability — counts, latency, error codes."},
    {
        "name": "tarefas",
        "description": "Tarefas atribuídas a usuários (admin gerencia, usuário vê as suas).",
    },
    {"name": "refunds", "description": "Reembolsos vinculados aos pedidos da conciliação."},
    {"name": "devolutions", "description": "Devoluções por pedido — controle manual de retorno de produto."},
    {"name": "financeiro", "description": "Consórcio, suprimentos (certificações) e simulação de cotações de importação."},
    {"name": "importacao", "description": "Controle de pedidos de importação de malas — SKUs, lotes, resumo financeiro."},
    {"name": "notas_fiscais", "description": "Upload de XML de NF-e → envio automático ao Mercado Livre."},
]

app = FastAPI(
    title="DaVinci API",
    version="0.1.0",
    description=(
        "REST backend for the DaVinci stock-sync platform. Routes are grouped by "
        "domain via OpenAPI tags. Authentication is cookie-based JWT issued by "
        "the email-OTP flow under `/api/auth`."
    ),
    lifespan=lifespan,
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
    openapi_tags=_OPENAPI_TAGS,
    contact={"name": "DaVinci", "url": "https://app.hadken.com"},
)

# In prod, web and api share the same origin (Caddy path-routes /api/*
# to api:8000 on every host the page is served from), so CORS is largely
# a no-op. Allowlist is kept explicit anyway as defense-in-depth: SSR
# can occasionally emit a cross-origin fetch, and the operator portal
# at gestaoestoque.com is a second valid front-end host.
_DEV_ORIGINS = [
    "http://localhost:3000", "http://127.0.0.1:3000",
    "http://localhost:3001", "http://127.0.0.1:3001",
]
_PROD_ORIGINS = [
    settings.app_url,
    "https://gestaoestoque.com",
    "https://www.gestaoestoque.com",
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_PROD_ORIGINS if settings.is_prod else _DEV_ORIGINS,
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
app.include_router(webhooks_router.router)
app.include_router(settings_router.router)
app.include_router(alerts_router.router)
app.include_router(listings_router.router)
app.include_router(pricing_router.router)
app.include_router(segments_router.router)
app.include_router(audit_router.router)
app.include_router(discrepancies_router.router)
app.include_router(dashboard_router.router)
app.include_router(metrics_router.router)
app.include_router(margens_router.router)
app.include_router(margem_audit_router.router)
app.include_router(tarefas_router.router)
app.include_router(refunds_router.router)
app.include_router(devolutions_router.router)
app.include_router(estoque_router.router)
app.include_router(faturamento_router.router)
app.include_router(financeiro_router.router)
app.include_router(importacao_router.router)
app.include_router(nf_upload_router.router)
app.include_router(dev_router.router)

if settings.enable_marketing:
    # Optional Marketing module — see config.enable_marketing. Import lazily so
    # prod boots even when marketing_* tables don't exist on the DB.
    from app.routers import marketing as marketing_router
    app.include_router(marketing_router.router)


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
    return {
        "status": status,
        "postgres": "ok" if pg_ok else "fail",
        "redis": "ok" if redis_ok else "fail",
    }
