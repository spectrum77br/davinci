# ruff: noqa: S105, S106  (segredos de teste, nada real)
"""Apoio dos testes do Zap (WhatsApp Cloud API, RF10, Onda 1).

Nada aqui fala com a Meta: o app, a WABA e o número são INVENTADOS, o Redis é
um falso em memória (o Redis de verdade é compartilhado entre os agentes que
rodam testes ao mesmo tempo) e a fila do arq é um falso que anota o job.
Os corpos vêm das fixtures `tests/fixtures/whatsapp/*.json` (o formato real da
Meta), pelo mesmo `montar_corpo` do script local.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import app.db as _db
from app.config import get_settings
from app.main import app as app_fastapi
from app.models import (
    AtendimentoZapApp,
    AtendimentoZapConta,
    AtendimentoZapEvento,
    AtendimentoZapNumero,
)
from app.routers import webhooks, whatsapp_webhook
from app.services.atendimento import canais_externos, zap_contas, zap_entrada
from scripts.atendimento_zap_local import montar_corpo

API_URL = "https://api.davinci.test"
APP_ID = "910000000000001"
SEGREDO = "segredo-de-teste-do-app-zap-0123456789"
VERIFY = "verify-token-de-teste-abcdef"
WABA = "920000000000001"
PNID = "930000000000001"
DISPLAY = "+55 11 90000-0000"
WA_ID = "5511987654321"
NOME = "Ana Fictícia"
URL = f"/api/webhooks/meta/whatsapp/{APP_ID}"
T0 = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)


class RedisFalso:
    """O que o webhook e a rodada usam do Redis (get/set NX/incr/expire/delete/eval)."""

    def __init__(self) -> None:
        self.dados: dict[str, Any] = {}

    async def get(self, chave):
        return self.dados.get(chave)

    async def set(self, chave, valor, *, nx=False, ex=None):
        if nx and chave in self.dados:
            return None
        self.dados[chave] = valor
        return True

    async def incr(self, chave):
        self.dados[chave] = int(self.dados.get(chave) or 0) + 1
        return self.dados[chave]

    async def expire(self, chave, segundos):
        return 1

    async def delete(self, *chaves):
        n = 0
        for c in chaves:
            n += 1 if self.dados.pop(c, None) is not None else 0
        return n

    async def exists(self, chave):
        return 1 if chave in self.dados else 0

    async def eval(self, script, n, chave, token):
        if self.dados.get(chave) == token:
            self.dados.pop(chave, None)
            return 1
        return 0


class PoolFalso:
    def __init__(self) -> None:
        self.jobs: list[str] = []
        self.falhar = False

    async def enqueue_job(self, nome, *args, **kwargs):
        if self.falhar:
            raise ConnectionError("redis do arq fora")
        self.jobs.append(nome)
        return SimpleNamespace(job_id=f"j{len(self.jobs)}")


@pytest.fixture
def zap(monkeypatch):
    """Ambiente do Zap: host principal de teste, leitura LIGADA, Redis e fila falsos."""
    s = get_settings()
    monkeypatch.setattr(s, "api_url", API_URL)
    monkeypatch.setattr(s, "atendimento_leitura_ativa", True)
    monkeypatch.setattr(s, "atendimento_zap_leitura_ativa", True)
    r = RedisFalso()
    monkeypatch.setattr(whatsapp_webhook, "redis", r)
    monkeypatch.setattr(webhooks, "redis", r)
    import app.redis_client as _redis_client

    monkeypatch.setattr(_redis_client, "redis", r)
    pool = PoolFalso()

    async def _pool():
        return pool

    monkeypatch.setattr(whatsapp_webhook, "get_arq_pool", _pool)
    zap_contas.limpar_cache()
    whatsapp_webhook._ultimo_evento.clear()
    yield SimpleNamespace(redis=r, pool=pool, settings=s)
    zap_contas.limpar_cache()
    whatsapp_webhook._ultimo_evento.clear()


@pytest_asyncio.fixture
async def cliente_zap():
    """O cliente no host PRINCIPAL (o do `api_url` de teste)."""
    async with AsyncClient(transport=ASGITransport(app=app_fastapi), base_url=API_URL) as ac:
        yield ac


async def cadastrar(
    db: AsyncSession,
    *,
    app_id: str = APP_ID,
    segredo: str = SEGREDO,
    verify: str = VERIFY,
    waba: str = WABA,
    pnid: str = PNID,
    display: str = DISPLAY,
    nome_na_barra: str | None = "Charlots · Zap",
    com_canal: bool = True,
) -> tuple[AtendimentoZapApp, AtendimentoZapConta, AtendimentoZapNumero]:
    """App + WABA + número de mentira (como o `cadastrar --ficticio`). Commita."""
    app = (
        await db.execute(select(AtendimentoZapApp).where(AtendimentoZapApp.app_id == app_id))
    ).scalar_one_or_none()
    if app is None:
        app = AtendimentoZapApp(
            app_id=app_id,
            nome="DaVinci Zap Teste",
            segredo_enc=zap_contas.cifrar_segredo_app(segredo),
            verify_token_enc=zap_contas.cifrar_verify_token(verify),
            teste=True,
        )
        db.add(app)
        await db.flush()
    conta = (
        await db.execute(select(AtendimentoZapConta).where(AtendimentoZapConta.waba_id == waba))
    ).scalar_one_or_none()
    if conta is None:
        conta = AtendimentoZapConta(
            app_ref=app.id,
            waba_id=waba,
            token_enc=zap_contas.cifrar_token_conta("token-de-mentira"),
        )
        db.add(conta)
        await db.flush()
    numero = AtendimentoZapNumero(
        conta_ref=conta.id,
        phone_number_id=pnid,
        display_phone_number=display,
        verified_name="Charlots",
        nome_na_barra=nome_na_barra,
        teste=True,
        etapa="teste",
    )
    db.add(numero)
    await db.flush()
    if com_canal:
        canal = await canais_externos.garantir_canal(
            db,
            externo_ref=canais_externos.ref_do_zap(pnid),
            plataforma="whatsapp",
            canal="zap",
            nome=nome_na_barra,
        )
        numero.canal_id = canal.id
    await db.commit()
    zap_contas.limpar_cache()
    return app, conta, numero


def corpo(tipo: str, *, waba: str = WABA, pnid: str = PNID, **kw) -> dict:
    kw.setdefault("display", "5511900000000")
    kw.setdefault("wa_id", WA_ID)
    kw.setdefault("nome", NOME)
    kw.setdefault("ts", T0)
    return montar_corpo(tipo, waba_id=waba, pnid=pnid, **kw)


def bruto(c: dict) -> bytes:
    return json.dumps(c, ensure_ascii=False).encode()


def assinar(dados: bytes, segredo: str = SEGREDO) -> str:
    return "sha256=" + hmac.new(segredo.encode(), dados, hashlib.sha256).hexdigest()


async def postar(cliente: AsyncClient, c: dict | bytes, *, segredo: str = SEGREDO, **headers):
    dados = c if isinstance(c, bytes) else bruto(c)
    h = {"Content-Type": "application/json", "X-Hub-Signature-256": assinar(dados, segredo)}
    h.update(headers)
    return await cliente.post(URL, content=dados, headers=h)


async def gravar_corpo(c: dict, *, app: AtendimentoZapApp) -> int:
    """O corpo direto na caixa de entrada (a mesma porta do webhook, sem HTTP)."""
    async with _db.SessionLocal() as s:
        n = await zap_entrada.gravar_itens(s, zap_entrada.itens_do_corpo(c), app_ref=app.id)
        await s.commit()
    return n


async def processar(**kw) -> dict:
    """O processador numa sessão PRÓPRIA (como o worker)."""
    async with _db.SessionLocal() as s:
        return await zap_entrada.processar_eventos(s, **kw)


async def eventos(db: AsyncSession) -> list[AtendimentoZapEvento]:
    return list(
        (
            await db.execute(
                select(AtendimentoZapEvento)
                .order_by(AtendimentoZapEvento.id)
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .all()
    )
