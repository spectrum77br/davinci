# ruff: noqa: S105, S106  (tokens de um cliente falso, nada real)
"""Magalu na caixa de atendimento: perguntas, chat com o cliente e SAC (30/09/2026).

A Magalu é falsa (respx, sem rede): o `MagaluClient` DE VERDADE fala com um
servidor falso que segue os formatos da documentação oficial (developers.
magalu.com, lida em 30/09 — OpenAPI de cada rota). Nenhuma chamada real.

O que estes testes garantem:

- as três caixas leem e gravam (uma conversa por pergunta; chat e SAC pela
  lista com cursor), com o pedido ligado quando vem (tag do chat, `order`
  do protocolo) e o prazo do protocolo (`due_date`) no lugar do SLA fixo;
- rodar duas vezes não duplica nada, e o chat/SAC só relê as mensagens de
  quem mexeu;
- a resposta dada no PORTAL da Magalu entra como `loja`/`externo` e tira da
  fila; a NOSSA voltando é reconhecida (pelo `ref`, pelo id do 201 ou pelo
  texto), sem virar linha duplicada;
- envio dos três canais (corpo, limite, destino do SAC), 202 = "enviada, em
  moderação", e a moderação que recusa depois vira `falhou` e devolve à fila
  — também a 2ª recusa da pergunta e a recusa no chat sem interação nova;
- SAC: nunca `_offset` acima de 100 (a fila em 2 páginas, os recentes pela
  data, as mensagens da mais nova); a fila é o STATUS do protocolo (a
  mediação cobrando a loja entra, o cliente falando com a Magalu não, a
  nossa recusada em silêncio volta), com o `due_date` mesmo vencido; o
  protocolo que sempre falha não segura o cursor;
- a releitura pelo id escolhe no SQL (30 respondidas não tomam a vez);
- 429 para a rodada e segura o canal; 401/403 da API = `sem_escopo` com
  "reautorize a Magalu"; 403 em HTML (Azion) não é escopo;
- NENHUMA chamada ao `read_by` — nem existe caminho para ela.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
import respx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import (
    AtendimentoCanal,
    AtendimentoConversa,
    AtendimentoMensagem,
    Integration,
    IntegrationPlatform,
)
from app.security.cipher import encrypt_json
from app.services.atendimento import clientes, enviar, gravar, ia, lojas, sync, validador
from app.services.atendimento import magalu as magalu_atd
from app.services.atendimento.constantes import (
    CANAIS_POR_PLATAFORMA,
    PLATAFORMAS,
    limite_caracteres,
    sla_horas,
)
from app.services.marketplaces import magalu as magalu_cliente
from app.services.marketplaces.magalu import MagaluClient

T0 = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)
CREDS = {
    "access_token": "tok",
    "refresh_token": "ref",
    "expires_at": int(time.time()) + 3600,
}


# ─────────────── formatos da doc ───────────────


def _z(quando: datetime) -> str:
    """Perguntas e chat: ISO com Z."""
    return quando.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ingenua(quando: datetime) -> str:
    """SAC: a doc mostra data SEM fuso (`2024-08-09T13:42:46`)."""
    return quando.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S")


def _pergunta(
    qid: str,
    *,
    quando: datetime = T0,
    texto: str = "Serve no iPhone 15?",
    status: str = "WAITING_RESPONSE",
    resposta: str | None = None,
    resposta_id: str | None = None,
    resposta_em: datetime | None = None,
    moderacao: str = "APPROVED",
) -> dict:
    q: dict[str, Any] = {
        "id": qid,
        "status": status,
        "channel": {"id": "ch-1", "alias": "magazineluiza"},
        "subject": {
            "id": "oferta-1",
            "type": "offer",
            "extra": {
                "name": "Capinha Poofy",
                "description": "Azul",
                "url_img": "https://a-static.mlcdn.com.br/capinha.jpg",
                "url": "https://www.magazineluiza.com.br/capinha/p/1/",
            },
        },
        "identifiers": [{"name": "sku", "value": "b001-20", "reference": "x"}],
        "question": {
            "message": texto,
            "external_id": f"ext-{qid}",
            "when_at": _z(quando),
            "owner": {"name": "João Silva", "ref_key": "joaosilva", "customer_id": "cli-1"},
            "product": {"sku": "b001-20", "description": "Capinha"},
        },
        "moderation": {"status": moderacao, "when_at": _z(quando), "block_rules": []},
    }
    if resposta is not None:
        q["answer"] = {
            "message": resposta,
            "external_id": resposta_id or f"portal-{qid}",
            "when_at": _z(resposta_em or quando + timedelta(minutes=10)),
            "owner": {"name": "Maria", "external_id": "maria"},
        }
    return q


def _conversa(
    cid: str,
    *,
    quando: datetime = T0,
    nao_lidas: int = 1,
    status: str = "OPENED",
    tags: list | None = None,
) -> dict:
    return {
        "id": cid,
        "display_name": "Capinha Poofy",
        "from_user": {"external_id": "cli-9", "full_name": "Ana Souza", "type": "CUSTOMER"},
        "to_user": {"external_id": "poofy", "full_name": "Poofy", "type": "SELLER"},
        "unread_to_count": nao_lidas,
        "unread_from_count": 0,
        "status": status,
        "last_interaction_at": _z(quando),
        "tags": [{"name": "order_code", "value": "LU-123"}] if tags is None else tags,
        "created_at": _z(T0 - timedelta(hours=1)),
        "updated_at": _z(quando),
    }


def _msg_chat(
    mid: str,
    *,
    texto: str,
    quando: datetime,
    de: str = "CUSTOMER",
    moderacao: str = "APPROVED",
    ref: str | None = None,
    integracao: bool = False,
) -> dict:
    return {
        "id": mid,
        "external_id": ref,
        "from_user": {"type": de, "full_name": "Ana Souza" if de == "CUSTOMER" else "Poofy"},
        "to_user": {"type": "SELLER" if de == "CUSTOMER" else "CUSTOMER"},
        "content": texto,
        "attachments": [],
        "read_by": [],
        "moderation": {"status": moderacao, "when_at": _z(quando)},
        "when_at": _z(quando),
        "integration": integracao,
    }


def _ticket(
    tid: str,
    *,
    status: str = "waiting_seller",
    quando: datetime = T0,
    prazo: datetime | None = T0 + timedelta(days=2),
    fechado: bool = False,
) -> dict:
    return {
        "id": tid,
        "channel": {"id": "c-1"},
        "closed": fechado,
        "code": None,
        "created_at": _ingenua(T0 - timedelta(hours=2)),
        "due_date": _ingenua(prazo) if prazo else None,
        "order": {
            "id": "ord-uuid-1",
            "code": "LU-555",
            "delivery": {
                "id": "d-1",
                "items": [
                    {
                        "id": "i-1",
                        "sku": "b001-20",
                        "external_sku": "b001.20",
                        "name": "Capinha Poofy",
                        "image": "https://a-static.mlcdn.com.br/capinha.jpg",
                        "quantity": 1,
                    }
                ],
                "seller": {"id": "s-1", "name": "Poofy"},
            },
        },
        "origin": "customer",
        "protocol": "202609300001",
        "reason": "delivery_delay",
        "status": status,
        "type": "shipping",
        "updated_at": _ingenua(quando),
    }


def _msg_ticket(
    mid: str,
    *,
    texto: str,
    quando: datetime,
    de: str = "customer",
    destino: str = "seller",
    moderacao: str = "approved",
    code: str | None = None,
) -> dict:
    nomes = {"customer": "Ana Souza", "seller": "Poofy", "channel": "Magalu"}
    return {
        "id": mid,
        "message": texto,
        "created_at": _ingenua(quando),
        "updated_at": _ingenua(quando),
        "destination": destino,
        "sender": {"id": f"{de}-1", "name": nomes[de], "type": de},
        "moderation": {"status": moderacao, "block_rules": []},
        "attachments": [],
        "code": code,
        "ticket": {"id": "t"},
    }


# ─────────────── a Magalu falsa ───────────────


def _pagina(itens: list, params: httpx.QueryParams, *, total: bool = True) -> dict:
    offset = int(params.get("_offset") or 0)
    limite = int(params.get("_limit") or 10)
    pagina: dict[str, Any] = {"offset": offset, "limit": limite, "count": 0}
    if total:
        pagina["total"] = len(itens)
    lote = itens[offset : offset + limite]
    pagina["count"] = len(lote)
    return {"meta": {"page": pagina, "links": {}}, "results": lote}


def _data_falsa(bruto: Any) -> datetime:
    """Data da Magalu falsa (com Z ou sem fuso = UTC); sem data vai para o fim."""
    if not bruto:
        return datetime.max.replace(tzinfo=UTC)
    quando = datetime.fromisoformat(str(bruto).replace("Z", "+00:00"))
    return quando.replace(tzinfo=UTC) if quando.tzinfo is None else quando


def _fora_do_schema_do_sac(params: httpx.QueryParams) -> httpx.Response | None:
    """OpenAPI do SAC (get_tickets / get_ticket_messages): `_offset` e `_limit` com
    maximum 100 — o FastAPI da Magalu responde 422 acima disso."""
    for nome in ("_offset", "_limit"):
        if int(params.get(nome) or 0) > 100:
            return httpx.Response(
                422,
                json={
                    "detail": [
                        {
                            "loc": ["query", nome],
                            "msg": "Input should be less than or equal to 100",
                            "type": "less_than_equal",
                        }
                    ]
                },
            )
    return None


def _ordenar(itens: list[dict], params: httpx.QueryParams) -> list[dict]:
    """`_sort=campo:asc|desc`, como o SAC aceita."""
    ordem = params.get("_sort")
    if not ordem:
        return itens
    campo, _, sentido = ordem.partition(":")
    return sorted(itens, key=lambda x: _data_falsa(x.get(campo)), reverse=sentido == "desc")


class MagaluFalsa:
    """O servidor da Magalu (services + api), nos formatos da documentação."""

    def __init__(self) -> None:
        self.perguntas: dict[str, list[dict]] = {
            "WAITING_RESPONSE": [],
            "REJECTED_RESPONSE": [],
            "APPROVED": [],
        }
        self.pergunta_por_id: dict[str, dict] = {}
        self.conversas: list[dict] = []
        self.conversa_por_id: dict[str, dict] = {}
        self.msgs_chat: dict[str, list[dict]] = {}
        self.fila: list[dict] = []
        self.recentes: list[dict] = []
        self.ticket_por_id: dict[str, dict] = {}
        self.msgs_ticket: dict[str, list[dict]] = {}
        # (método, regex do caminho, fábrica da resposta): passa na frente de tudo.
        self.forcar: list[tuple[str, str, Callable[[], httpx.Response]]] = []
        self.chamadas: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.chamadas.append(request)
        metodo, caminho, params = request.method, request.url.path, request.url.params
        for m, padrao, fabrica in self.forcar:
            if m == metodo and re.fullmatch(padrao, caminho):
                return fabrica()
        corpo = json.loads(request.content) if request.content else None
        if request.url.host == "services.magalu.com":
            return self._services(metodo, caminho, params, corpo)
        if request.url.host == "api.magalu.com":
            return self._api(metodo, caminho, params, corpo)
        return httpx.Response(599, json={"slug": "host_desconhecido"})

    def _services(self, metodo, caminho, params, corpo) -> httpx.Response:
        if metodo == "GET" and caminho == "/v0/questions":
            return httpx.Response(
                200, json=_pagina(self.perguntas.get(params["status"], []), params)
            )
        if metodo == "GET" and (m := re.fullmatch(r"/v0/questions/([^/]+)", caminho)):
            q = self.pergunta_por_id.get(m[1])
            if q is None:
                return httpx.Response(404, json={"slug": "not_found"})
            return httpx.Response(200, json=q)
        if metodo == "POST" and (m := re.fullmatch(r"/v0/questions/([^/]+)/answer", caminho)):
            return httpx.Response(
                202,
                json={
                    "message": corpo["message"],
                    "external_id": corpo.get("external_id"),
                    "owner": corpo["owner"],
                    "moderation": {"status": "WAITING_MODERATION", "block_rules": []},
                },
            )
        if metodo == "GET" and caminho == "/v0/conversations":
            return httpx.Response(200, json=_pagina(self.conversas, params))
        if metodo == "GET" and (m := re.fullmatch(r"/v0/conversations/([^/]+)", caminho)):
            c = self.conversa_por_id.get(m[1])
            if c is None:
                return httpx.Response(404, json={"slug": "not_found"})
            return httpx.Response(200, json=c)
        if metodo == "GET" and (m := re.fullmatch(r"/v0/conversations/([^/]+)/messages", caminho)):
            return httpx.Response(200, json=_pagina(self.msgs_chat.get(m[1], []), params))
        if metodo == "POST" and (m := re.fullmatch(r"/v0/conversations/([^/]+)/messages", caminho)):
            return httpx.Response(
                201,
                json={
                    "id": f"m-{m[1]}-nova",
                    "content": corpo["content"],
                    "external_id": corpo.get("external_id"),
                    "from_user": {"type": "SELLER", "owner": corpo["owner"]},
                },
            )
        return httpx.Response(404, json={"slug": "rota_desconhecida"})

    def _api(self, metodo, caminho, params, corpo) -> httpx.Response:
        if metodo == "GET" and caminho.startswith("/seller/v0/tickets"):
            if (recusa := _fora_do_schema_do_sac(params)) is not None:
                return recusa
        if metodo == "GET" and caminho == "/seller/v0/tickets":
            fonte = self.fila if params.get("status") == "waiting_seller" else self.recentes
            if params.get("updated_at_gte"):
                corte = _data_falsa(params["updated_at_gte"])
                fonte = [t for t in fonte if _data_falsa(t.get("updated_at")) >= corte]
            return httpx.Response(200, json=_pagina(_ordenar(fonte, params), params, total=False))
        if metodo == "GET" and (m := re.fullmatch(r"/seller/v0/tickets/([^/]+)", caminho)):
            t = self.ticket_por_id.get(m[1])
            if t is None:
                return httpx.Response(404, json={"slug": "not_found"})
            return httpx.Response(200, json=t)
        if metodo == "GET" and (m := re.fullmatch(r"/seller/v0/tickets/([^/]+)/messages", caminho)):
            msgs = _ordenar(self.msgs_ticket.get(m[1], []), params)
            return httpx.Response(200, json=_pagina(msgs, params, total=False))
        if metodo == "POST" and re.fullmatch(r"/seller/v0/tickets/([^/]+)/messages", caminho):
            return httpx.Response(
                202,
                json={
                    "transaction_id": "0b7c9c1e-0000-4000-8000-000000000001",
                    "links": [{"path": "/seller/v0/transactions/0b7c9c1e"}],
                    "data": None,
                },
            )
        return httpx.Response(404, json={"slug": "rota_desconhecida"})

    # — consultas dos testes —
    def feitas(self, metodo: str, padrao: str) -> list[httpx.Request]:
        return [r for r in self.chamadas if r.method == metodo and re.fullmatch(padrao, r.url.path)]

    def corpo(self, metodo: str, padrao: str) -> dict:
        return json.loads(self.feitas(metodo, padrao)[-1].content)


async def _nada(*_a, **_k) -> None:
    return None


@pytest.fixture
def magalu(monkeypatch):
    """A Magalu falsa atrás do `MagaluClient` de verdade; o `read_by` tem rota só para provar
    que ninguém a chama. A espera do 429 do cliente vira zero (o teste não dorme)."""
    monkeypatch.setattr(get_settings(), "magalu_proxy_url", "")
    monkeypatch.setattr(magalu_cliente, "asyncio", SimpleNamespace(sleep=_nada))
    falsa = MagaluFalsa()
    with respx.mock(assert_all_called=False) as router:
        falsa.read_by = router.route(path__regex=r".*read_by.*").mock(
            return_value=httpx.Response(204)
        )
        router.route().mock(side_effect=falsa)
        yield falsa
    assert not falsa.read_by.called, "o DaVinci nunca marca a conversa da Magalu como lida"
    assert not [r for r in falsa.chamadas if "read_by" in str(r.url)]


@pytest.fixture
def relogio(monkeypatch):
    atual = {"agora": T0 + timedelta(minutes=30)}
    monkeypatch.setattr(magalu_atd, "_agora", lambda: atual["agora"])
    return atual


async def _loja(
    db: AsyncSession, make_user, canal_nome: str, *, modo: str = "humano"
) -> tuple[AtendimentoCanal, Integration]:
    user = await make_user()
    integ = Integration(
        user_id=user.id,
        platform=IntegrationPlatform.MAGALU,
        name="poofy",
        credentials=encrypt_json(CREDS),
    )
    db.add(integ)
    await db.commit()
    await db.refresh(integ)
    canal = AtendimentoCanal(
        integration_id=integ.id, plataforma="magalu", canal=canal_nome, modo=modo, status="ok"
    )
    db.add(canal)
    await db.commit()
    return canal, integ


async def _rodar(db: AsyncSession, canal: AtendimentoCanal, integ: Integration):
    resultado = await magalu_atd.sincronizar(db, canal, integ, MagaluClient(dict(CREDS)))
    await db.commit()
    return resultado


async def _conversa_db(db: AsyncSession, externo_id: str) -> AtendimentoConversa:
    c = (
        await db.execute(
            select(AtendimentoConversa).where(AtendimentoConversa.externo_id == externo_id)
        )
    ).scalar_one()
    await db.refresh(c)
    return c


async def _mensagens(db: AsyncSession, conversa: AtendimentoConversa) -> list[AtendimentoMensagem]:
    linhas = (
        (
            await db.execute(
                select(AtendimentoMensagem)
                .where(AtendimentoMensagem.conversa_id == conversa.id)
                .order_by(AtendimentoMensagem.enviada_em, AtendimentoMensagem.created_at)
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .all()
    )
    return list(linhas)


async def _total_mensagens(db: AsyncSession) -> int:
    return int(await db.scalar(select(func.count()).select_from(AtendimentoMensagem)))


async def _nossa(
    db: AsyncSession,
    conversa: AtendimentoConversa,
    texto: str,
    resultado,
    *,
    em: datetime | None = None,
) -> AtendimentoMensagem:
    """Grava a resposta como o `enviar` grava depois de um envio que saiu.

    `em` = a hora do envio no relógio falso do teste (sem ele, a hora real).
    """
    m = AtendimentoMensagem(
        conversa_id=conversa.id,
        externo_id=resultado.externo_id,
        autor="loja",
        origem="davinci_humano",
        tipo="texto",
        texto=texto,
        enviada_em=em or datetime.now(UTC),
        status="enviada",
        payload={"envio": dict(resultado.payload)},
    )
    db.add(m)
    gravar.recalcular(conversa, [m])
    await db.commit()
    return m


# ─────────────── vocabulário e ligações ───────────────


def test_magalu_e_lida_por_api_com_tres_caixas():
    assert "magalu" in PLATAFORMAS
    assert CANAIS_POR_PLATAFORMA["magalu"] == ("pergunta", "chat", "sac")
    # Limites da doc; a pergunta não tem limite documentado: o padrão da caixa.
    assert limite_caracteres("magalu", "chat") == 2200
    assert limite_caracteres("magalu", "sac") == 3000
    assert limite_caracteres("magalu", "pergunta") == 1000
    # Sem SLA oficial publicado para pergunta e chat: o padrão (24 h).
    assert sla_horas("magalu", "pergunta") == 24
    assert sla_horas("magalu", "chat") == 24
    assert enviar.adaptador("magalu") is magalu_atd


def test_escopos_e_servidor_das_caixas():
    escopos = set(magalu_cliente.MAGALU_SCOPES.split())
    assert {
        "services:questions-seller:read",
        "services:questions-seller:write",
        "services:conversations-seller:read",
        "services:conversations-seller:write",
        "open:tickets-seller:read",
        "open:ticket-messages-seller:read",
        "open:ticket-messages-seller:write",
    } <= escopos
    assert magalu_cliente.MAGALU_SERVICES_BASE == "https://services.magalu.com"


def test_nome_da_loja_sem_o_prefixo_da_magalu():
    assert lojas.sem_prefixo("Magalu Poofy", "magalu") == "Poofy"
    assert lojas.sem_prefixo("Magazine Luiza - Poofy", "magalu") == "Poofy"


def test_ia_conhece_a_magalu_e_as_tres_caixas():
    assert ia._PLATAFORMA_NOME["magalu"] == "Magalu"
    for canal in ("pergunta", "chat", "sac"):
        assert ("magalu", canal) in ia._CANAL_DESCRICAO
    assert "moderação" in ia._REGRAS_PLATAFORMA["magalu"]


def test_validador_barra_o_que_a_moderacao_da_magalu_barra():
    def v(texto: str) -> list[str]:
        return validador.validar(texto, plataforma="magalu", canal="chat", origem="davinci_humano")

    assert any("CPF" in m for m in v("Confirme o CPF 123.456.789-09 no cadastro"))
    assert any("CPF" in m for m in v("cnpj: 12345678000190"))
    assert any("PIX" in m for m in v("Faço desconto no pix"))
    assert any("e-mail" in m for m in v("Mande para loja@poofy.com.br"))
    assert any("telefone" in m for m in v("Me liga no (11) 99876-5432"))
    # Número do pedido (11 dígitos soltos) e texto comum passam.
    assert v("Seu pedido 12345678901 já foi despachado.") == []
    # Fora da Magalu, o CPF formatado de pessoa não é barrado por esta regra.
    assert not validador.validar(
        "CPF 123.456.789-09", plataforma="shopee", canal="chat", origem="davinci_humano"
    )


async def test_garantir_canais_cria_as_tres_caixas_da_magalu(db: AsyncSession, make_user):
    user = await make_user()
    integ = Integration(
        user_id=user.id,
        platform=IntegrationPlatform.MAGALU,
        name="poofy",
        credentials=encrypt_json(CREDS),
    )
    db.add(integ)
    await db.commit()
    await sync.garantir_canais(db)
    await db.commit()
    canais = (
        await db.execute(
            select(AtendimentoCanal.canal, AtendimentoCanal.modo).where(
                AtendimentoCanal.integration_id == integ.id
            )
        )
    ).all()
    assert sorted(canais) == [("chat", "observar"), ("pergunta", "observar"), ("sac", "observar")]


async def test_cliente_da_magalu_usa_a_renovacao_do_proprio_cliente(db: AsyncSession, make_user):
    """Um caminho de refresh só: o do `MagaluClient` (trava da linha da integração)."""
    user = await make_user()
    integ = Integration(
        user_id=user.id,
        platform=IntegrationPlatform.MAGALU,
        name="poofy",
        credentials=encrypt_json(CREDS),
    )
    db.add(integ)
    await db.commit()
    cliente = await clientes.cliente_da_integracao(integ)
    assert isinstance(cliente, MagaluClient)
    assert cliente._integration_id == integ.id
    assert cliente.refresh.__func__ is MagaluClient.refresh


# ─────────────── perguntas ───────────────


async def test_perguntas_entram_na_fila_uma_conversa_por_pergunta(
    db: AsyncSession, make_user, magalu, relogio
):
    canal, integ = await _loja(db, make_user, "pergunta")
    magalu.perguntas["WAITING_RESPONSE"] = [
        _pergunta("q1"),
        _pergunta("q2", quando=T0 + timedelta(minutes=5), texto="Tem na cor rosa?"),
    ]
    r = await _rodar(db, canal, integ)

    assert (r.status, r.conversas_novas, r.mensagens_novas, r.nao_lidas) == ("ok", 2, 2, 2)
    pedido = magalu.feitas("GET", "/v0/questions")[0]
    assert pedido.url.host == "services.magalu.com"
    assert pedido.url.params["status"] == "WAITING_RESPONSE"
    assert pedido.headers["authorization"] == "Bearer tok"

    c = await _conversa_db(db, "q:q1")
    assert (c.plataforma, c.canal, c.situacao) == ("magalu", "pergunta", "aberta")
    assert c.aguardando_resposta is True
    assert c.prazo_resposta_em == T0 + timedelta(hours=24)  # padrão da caixa
    assert (c.comprador_id, c.comprador_nome) == ("cli-1", "João Silva")
    assert (c.anuncio_id, c.anuncio_titulo, c.conta) == ("b001-20", "Capinha Poofy", "poofy")
    assert c.dados["produto"]["imagem"] == "https://a-static.mlcdn.com.br/capinha.jpg"
    [m] = await _mensagens(db, c)
    assert (m.externo_id, m.autor, m.origem, m.texto) == (
        "q:q1",
        "cliente",
        "cliente",
        "Serve no iPhone 15?",
    )


async def test_perguntas_rodar_duas_vezes_nao_duplica(db: AsyncSession, make_user, magalu, relogio):
    canal, integ = await _loja(db, make_user, "pergunta")
    magalu.perguntas["WAITING_RESPONSE"] = [_pergunta("q1")]
    await _rodar(db, canal, integ)
    r = await _rodar(db, canal, integ)
    assert (r.conversas_novas, r.conversas_atualizadas, r.mensagens_novas) == (0, 0, 0)
    assert await _total_mensagens(db) == 1


async def test_pergunta_respondida_no_portal_sai_da_fila(
    db: AsyncSession, make_user, magalu, relogio
):
    canal, integ = await _loja(db, make_user, "pergunta")
    magalu.perguntas["WAITING_RESPONSE"] = [_pergunta("q1")]
    await _rodar(db, canal, integ)

    # Respondida no portal: some da fila e o DaVinci pergunta pelo id.
    magalu.perguntas["WAITING_RESPONSE"] = []
    magalu.pergunta_por_id["q1"] = _pergunta(
        "q1", status="APPROVED", resposta="Serve sim!", resposta_id="portal-77"
    )
    r = await _rodar(db, canal, integ)

    assert magalu.feitas("GET", "/v0/questions/q1")
    c = await _conversa_db(db, "q:q1")
    assert c.aguardando_resposta is False
    assert c.situacao == "respondida"
    loja = [m for m in await _mensagens(db, c) if m.autor == "loja"]
    assert [(m.externo_id, m.origem, m.status) for m in loja] == [
        ("a:q1:portal-77", "externo", "enviada")
    ]
    assert r.mensagens_novas == 1


async def test_pergunta_apagada_404_fecha_a_conversa(db: AsyncSession, make_user, magalu, relogio):
    canal, integ = await _loja(db, make_user, "pergunta")
    magalu.perguntas["WAITING_RESPONSE"] = [_pergunta("q1")]
    await _rodar(db, canal, integ)
    magalu.perguntas["WAITING_RESPONSE"] = []
    await _rodar(db, canal, integ)  # GET /v0/questions/q1 → 404

    c = await _conversa_db(db, "q:q1")
    assert (c.situacao, c.bloqueio_motivo) == ("fechada", "magalu_DELETED")
    assert c.aguardando_resposta is False


async def test_envio_da_pergunta_fica_em_moderacao_e_a_recusa_volta_para_a_fila(
    db: AsyncSession, make_user, magalu, relogio, monkeypatch
):
    """Ponta a ponta pelo `enviar`: 202 = enviada, em moderação; recusada depois = falhou."""
    s = get_settings()
    monkeypatch.setattr(s, "atendimento_envio_ativo", True)
    monkeypatch.setattr(s, "atendimento_simulador", False)
    canal, integ = await _loja(db, make_user, "pergunta")
    user = await make_user()
    magalu.perguntas["WAITING_RESPONSE"] = [_pergunta("q1")]
    await _rodar(db, canal, integ)
    c = await _conversa_db(db, "q:q1")

    m = await enviar.enviar_resposta(db, c, "Serve sim, é compatível com o iPhone 15.", user=user)

    corpo = magalu.corpo("POST", "/v0/questions/q1/answer")
    assert corpo["message"] == "Serve sim, é compatível com o iPhone 15."
    assert corpo["owner"] == {"name": "poofy", "external_id": "davinci"}
    ref = corpo["external_id"]
    assert ref.startswith("davinci-")
    assert (m.status, m.externo_id) == ("enviada", f"a:q1:{ref}")
    assert m.payload["envio"]["moderacao"] == "em_moderacao"
    await db.refresh(c)
    assert c.aguardando_resposta is False

    # A leitura traz a NOSSA resposta (mesmo external_id), ainda em moderação:
    # nada de linha duplicada.
    magalu.perguntas["WAITING_RESPONSE"] = [
        _pergunta(
            "q1",
            resposta="Serve sim, é compatível com o iPhone 15.",
            resposta_id=ref,
            moderacao="WAITING_MODERATION",
        )
    ]
    await _rodar(db, canal, integ)
    loja = [x for x in await _mensagens(db, c) if x.autor == "loja"]
    assert [(x.id, x.status, x.origem) for x in loja] == [(m.id, "enviada", "davinci_humano")]
    assert loja[0].payload["moderacao"] == "waiting_moderation"

    # A moderação recusou: a resposta vira `falhou` e a pergunta volta à fila.
    magalu.perguntas["WAITING_RESPONSE"] = []
    magalu.perguntas["REJECTED_RESPONSE"] = [
        _pergunta(
            "q1",
            status="REJECTED_RESPONSE",
            resposta="Serve sim, é compatível com o iPhone 15.",
            resposta_id=ref,
            moderacao="REJECTED",
        )
    ]
    await _rodar(db, canal, integ)
    [recusada] = [x for x in await _mensagens(db, c) if x.autor == "loja"]
    assert (recusada.status, recusada.erro) == ("falhou", "moderacao_magalu")
    await db.refresh(c)
    assert c.aguardando_resposta is True
    assert c.nao_lidas == 1
    assert await magalu_atd._pergunta_pendente(db, c) is not None


async def test_envio_de_pergunta_ja_respondida_nao_vai_a_magalu(
    db: AsyncSession, make_user, magalu, relogio
):
    canal, integ = await _loja(db, make_user, "pergunta")
    magalu.perguntas["WAITING_RESPONSE"] = [_pergunta("q1", resposta="Já respondi pelo portal")]
    await _rodar(db, canal, integ)
    c = await _conversa_db(db, "q:q1")
    r = await magalu_atd.enviar_texto(db, c, integ, MagaluClient(dict(CREDS)), "Oi")
    assert (r.ok, r.erro) == (False, "sem_pergunta_pendente")
    assert not magalu.feitas("POST", r"/v0/questions/.*/answer")


async def test_pergunta_segunda_recusa_da_moderacao_volta_para_a_fila(
    db: AsyncSession, make_user, magalu, relogio, monkeypatch
):
    """REJECTED_RESPONSE sem `answer`, duas vezes: a 2ª recusa (outro
    `moderation.when_at`) é vista — e a lista atrasada, com a recusa ANTIGA,
    não derruba a resposta nova que saiu depois dela."""
    s = get_settings()
    monkeypatch.setattr(s, "atendimento_envio_ativo", True)
    monkeypatch.setattr(s, "atendimento_simulador", False)
    canal, integ = await _loja(db, make_user, "pergunta")
    user = await make_user()
    magalu.perguntas["WAITING_RESPONSE"] = [_pergunta("q1")]
    await _rodar(db, canal, integ)
    c = await _conversa_db(db, "q:q1")

    def _recusa(quando: datetime) -> dict:
        q = _pergunta("q1", status="REJECTED_RESPONSE", moderacao="REJECTED")
        q["moderation"]["when_at"] = _z(quando)
        return q

    m1 = await enviar.enviar_resposta(db, c, "Serve sim no modelo 15.", user=user)
    m1.enviada_em = T0 + timedelta(minutes=31)
    await db.commit()
    # 1ª recusa.
    relogio["agora"] = T0 + timedelta(minutes=33)
    magalu.perguntas["WAITING_RESPONSE"] = []
    magalu.perguntas["REJECTED_RESPONSE"] = [_recusa(T0 + timedelta(minutes=32))]
    await _rodar(db, canal, integ)
    await db.refresh(m1)
    assert (m1.status, m1.erro) == ("falhou", "moderacao_magalu")

    m2 = await enviar.enviar_resposta(db, c, "Serve sim, é compatível.", user=user)
    m2.enviada_em = T0 + timedelta(minutes=40)
    await db.commit()
    # A lista ainda mostra a recusa ANTIGA: a resposta nova não é dela.
    relogio["agora"] = T0 + timedelta(minutes=42)
    await _rodar(db, canal, integ)
    await db.refresh(m2)
    assert m2.status == "enviada"

    # 2ª recusa, depois do envio da m2.
    relogio["agora"] = T0 + timedelta(minutes=47)
    magalu.perguntas["REJECTED_RESPONSE"] = [_recusa(T0 + timedelta(minutes=45))]
    await _rodar(db, canal, integ)
    await db.refresh(m2)
    await db.refresh(c)
    assert (m2.status, m2.erro) == ("falhou", "moderacao_magalu")
    assert c.aguardando_resposta is True
    assert c.prazo_resposta_em is not None
    assert await magalu_atd._pergunta_pendente(db, c) is not None


async def test_pergunta_respondida_no_portal_sai_da_fila_mesmo_com_30_respondidas(
    db: AsyncSession, make_user, magalu, relogio
):
    """A releitura pelo id escolhe no SQL quem ainda espera: 30 respondidas
    recentes não tomam o lugar da que espera."""
    canal, integ = await _loja(db, make_user, "pergunta")
    respondidas = [
        _pergunta(
            f"q{i}",
            quando=T0 - timedelta(hours=5),
            resposta="Temos sim.",
            resposta_id=f"portal-{i}",
        )
        for i in range(30)
    ]
    espera = _pergunta("qX", quando=T0 - timedelta(hours=4), texto="Tem azul?")
    magalu.perguntas["WAITING_RESPONSE"] = [*respondidas, espera]
    await _rodar(db, canal, integ)
    assert (await _conversa_db(db, "q:qX")).aguardando_resposta is True

    # qX foi respondida no portal e saiu da lista (as outras 30 também).
    magalu.perguntas["WAITING_RESPONSE"] = []
    magalu.pergunta_por_id["qX"] = _pergunta(
        "qX",
        quando=T0 - timedelta(hours=4),
        status="APPROVED",
        texto="Tem azul?",
        resposta="Tem sim!",
        resposta_id="portal-X",
    )
    relogio["agora"] = T0 + timedelta(minutes=50)
    await _rodar(db, canal, integ)
    assert magalu.feitas("GET", r"/v0/questions/qX")
    cx = await _conversa_db(db, "q:qX")
    assert cx.aguardando_resposta is False
    assert cx.prazo_resposta_em is None


# ─────────────── chat ───────────────


async def test_chat_le_conversa_mensagens_e_pedido_da_tag(
    db: AsyncSession, make_user, magalu, relogio
):
    canal, integ = await _loja(db, make_user, "chat")
    magalu.conversas = [_conversa("c1", nao_lidas=2)]
    magalu.msgs_chat["c1"] = [
        _msg_chat("m2", texto="Alguém?", quando=T0),
        _msg_chat("m1", texto="Meu pedido chega quando?", quando=T0 - timedelta(minutes=3)),
        _msg_chat("m0", texto="[pedido]", quando=T0 - timedelta(minutes=4), integracao=True),
    ]
    r = await _rodar(db, canal, integ)

    assert (r.status, r.conversas_novas, r.mensagens_novas, r.nao_lidas) == ("ok", 1, 3, 2)
    lista = magalu.feitas("GET", "/v0/conversations")[0]
    assert lista.url.params["status"] == "OPENED"
    assert "last_interaction_at_start" in lista.url.params
    c = await _conversa_db(db, "c1")
    assert (c.canal, c.pedido_marketplace, c.nao_lidas) == ("chat", "LU-123", 2)
    assert (c.comprador_id, c.comprador_nome) == ("cli-9", "Ana Souza")
    assert c.aguardando_resposta is True
    assert c.prazo_resposta_em == T0 + timedelta(hours=24)
    msgs = await _mensagens(db, c)
    assert [(x.externo_id, x.autor) for x in msgs] == [
        ("m0", "sistema"),
        ("m1", "cliente"),
        ("m2", "cliente"),
    ]
    assert canal.cursor["desde"] == (T0 + timedelta(minutes=30)).isoformat(timespec="seconds")


async def test_chat_so_rele_as_mensagens_de_quem_mexeu(
    db: AsyncSession, make_user, magalu, relogio
):
    canal, integ = await _loja(db, make_user, "chat")
    magalu.conversas = [_conversa("c1"), _conversa("c2", quando=T0 + timedelta(minutes=1))]
    magalu.msgs_chat["c1"] = [_msg_chat("m1", texto="Oi", quando=T0)]
    magalu.msgs_chat["c2"] = [_msg_chat("n1", texto="Olá", quando=T0 + timedelta(minutes=1))]
    await _rodar(db, canal, integ)
    assert len(magalu.feitas("GET", r"/v0/conversations/.+/messages")) == 2

    # c2 mexeu (mensagem nova); c1 não.
    relogio["agora"] += timedelta(minutes=2)
    magalu.conversas[1] = _conversa("c2", quando=T0 + timedelta(minutes=31))
    magalu.msgs_chat["c2"].append(
        _msg_chat("n2", texto="Ainda aí?", quando=T0 + timedelta(minutes=31))
    )
    r = await _rodar(db, canal, integ)
    relidas = [x.url.path for x in magalu.feitas("GET", r"/v0/conversations/.+/messages")]
    assert relidas[2:] == ["/v0/conversations/c2/messages"]
    assert (r.conversas_novas, r.mensagens_novas) == (0, 1)
    assert await _total_mensagens(db) == 3


async def test_chat_resposta_do_portal_entra_como_externo_e_tira_da_fila(
    db: AsyncSession, make_user, magalu, relogio
):
    canal, integ = await _loja(db, make_user, "chat")
    magalu.conversas = [_conversa("c1", nao_lidas=0)]
    magalu.msgs_chat["c1"] = [
        _msg_chat("m1", texto="Tem garantia?", quando=T0),
        _msg_chat("m2", texto="Tem sim, 90 dias.", quando=T0 + timedelta(minutes=5), de="SELLER"),
    ]
    await _rodar(db, canal, integ)
    c = await _conversa_db(db, "c1")
    assert c.aguardando_resposta is False
    assert c.situacao == "respondida"
    loja = [x for x in await _mensagens(db, c) if x.autor == "loja"]
    assert [(x.origem, x.status) for x in loja] == [("externo", "enviada")]


async def test_envio_do_chat_e_a_nossa_volta_sem_duplicar(
    db: AsyncSession, make_user, magalu, relogio
):
    canal, integ = await _loja(db, make_user, "chat")
    magalu.conversas = [_conversa("c1")]
    magalu.msgs_chat["c1"] = [_msg_chat("m1", texto="Tem garantia?", quando=T0)]
    await _rodar(db, canal, integ)
    c = await _conversa_db(db, "c1")

    texto = "Tem sim: 90 dias pela loja."
    r = await magalu_atd.enviar_texto(db, c, integ, MagaluClient(dict(CREDS)), texto)
    assert (r.ok, r.externo_id) == (True, "m-c1-nova")
    assert r.payload["moderacao"] == "em_moderacao"
    pedido = magalu.feitas("POST", "/v0/conversations/c1/messages")[-1]
    assert pedido.url.host == "services.magalu.com"
    corpo = json.loads(pedido.content)
    assert corpo["content"] == texto
    assert corpo["owner"] == {"name": "poofy", "external_id": "davinci"}
    assert corpo["external_id"] == r.payload["ref"]
    nossa = await _nossa(db, c, texto, r)

    # A leitura traz a nossa aprovada, e depois uma recusa da moderação.
    relogio["agora"] += timedelta(minutes=20)
    magalu.conversas = [_conversa("c1", quando=T0 + timedelta(minutes=45), nao_lidas=0)]
    magalu.msgs_chat["c1"].append(
        _msg_chat(
            "m-c1-nova",
            texto=texto,
            quando=T0 + timedelta(minutes=45),
            de="SELLER",
            moderacao="REJECTED",
            ref=r.payload["ref"],
        )
    )
    await _rodar(db, canal, integ)
    loja = [x for x in await _mensagens(db, c) if x.autor == "loja"]
    assert [(x.id, x.status, x.erro) for x in loja] == [(nossa.id, "falhou", "moderacao_magalu")]
    await db.refresh(c)
    assert c.aguardando_resposta is True  # o comprador não recebeu


async def test_chat_acima_do_limite_nao_sai(db: AsyncSession, make_user, magalu, relogio):
    canal, integ = await _loja(db, make_user, "chat")
    magalu.conversas = [_conversa("c1")]
    magalu.msgs_chat["c1"] = [_msg_chat("m1", texto="Oi", quando=T0)]
    await _rodar(db, canal, integ)
    c = await _conversa_db(db, "c1")
    r = await magalu_atd.enviar_texto(db, c, integ, MagaluClient(dict(CREDS)), "a" * 2201)
    assert r.ok is False and "acima_do_limite" in r.erro
    assert not magalu.feitas("POST", r"/v0/conversations/.+/messages")


async def test_envio_com_5xx_e_ambiguo_e_nao_repete(db: AsyncSession, make_user, magalu, relogio):
    canal, integ = await _loja(db, make_user, "chat")
    magalu.conversas = [_conversa("c1")]
    magalu.msgs_chat["c1"] = [_msg_chat("m1", texto="Oi", quando=T0)]
    await _rodar(db, canal, integ)
    c = await _conversa_db(db, "c1")
    magalu.forcar.append(
        ("POST", r"/v0/conversations/c1/messages", lambda: httpx.Response(503, text="x"))
    )
    r = await magalu_atd.enviar_texto(db, c, integ, MagaluClient(dict(CREDS)), "Olá!")
    assert (r.ok, r.ambiguo) == (False, True)
    # Mensagem para comprador não se repete: um POST só.
    assert len(magalu.feitas("POST", "/v0/conversations/c1/messages")) == 1


async def test_envio_sem_escopo_pede_reautorizacao(db: AsyncSession, make_user, magalu, relogio):
    canal, integ = await _loja(db, make_user, "chat")
    magalu.conversas = [_conversa("c1")]
    magalu.msgs_chat["c1"] = [_msg_chat("m1", texto="Oi", quando=T0)]
    await _rodar(db, canal, integ)
    c = await _conversa_db(db, "c1")
    magalu.forcar.append(
        (
            "POST",
            r"/v0/conversations/c1/messages",
            lambda: httpx.Response(403, json={"slug": "insufficient_scope"}),
        )
    )
    r = await magalu_atd.enviar_texto(db, c, integ, MagaluClient(dict(CREDS)), "Olá!")
    assert r.ok is False and r.ambiguo is False
    assert "sem_escopo" in r.erro and "reautorize a Magalu" in r.erro


async def test_chat_recusa_da_moderacao_sem_nova_interacao_e_vista(
    db: AsyncSession, make_user, magalu, relogio
):
    """A recusa da moderação não muda o `last_interaction_at` (a última
    mensagem é a última APROVADA): a conversa que continua na lista com a
    nossa em moderação tem as mensagens relidas — no máximo a cada 15 min."""
    canal, integ = await _loja(db, make_user, "chat")
    magalu.conversas = [_conversa("c1")]
    magalu.msgs_chat["c1"] = [_msg_chat("m1", texto="Tem garantia?", quando=T0)]
    await _rodar(db, canal, integ)
    c = await _conversa_db(db, "c1")
    texto = "Tem sim: 90 dias pela loja."
    r = await magalu_atd.enviar_texto(db, c, integ, MagaluClient(dict(CREDS)), texto)
    envio = T0 + timedelta(minutes=31)
    nossa = await _nossa(db, c, texto, r, em=envio)

    # A leitura seguinte traz a nossa, ainda na moderação.
    relogio["agora"] = T0 + timedelta(minutes=33)
    magalu.conversas = [_conversa("c1", quando=envio, nao_lidas=0)]
    magalu.msgs_chat["c1"].append(
        _msg_chat(
            "m-c1-nova",
            texto=texto,
            quando=envio,
            de="SELLER",
            moderacao="WAITING_MODERATION",
            ref=r.payload["ref"],
        )
    )
    await _rodar(db, canal, integ)
    await db.refresh(nossa)
    assert nossa.payload["moderacao"] == "waiting_moderation"

    # A moderação recusa; a conversa segue na lista com a mesma última interação.
    magalu.msgs_chat["c1"][-1]["moderation"] = {
        "status": "REJECTED",
        "when_at": _z(T0 + timedelta(minutes=34)),
    }
    lidas = len(magalu.feitas("GET", r"/v0/conversations/c1/messages"))
    relogio["agora"] = T0 + timedelta(minutes=36)
    await _rodar(db, canal, integ)
    # Freio: acabou de ler as mensagens, não relê a cada rodada.
    assert len(magalu.feitas("GET", r"/v0/conversations/c1/messages")) == lidas

    relogio["agora"] = T0 + timedelta(minutes=50)
    await _rodar(db, canal, integ)
    assert len(magalu.feitas("GET", r"/v0/conversations/c1/messages")) == lidas + 1
    await db.refresh(nossa)
    assert (nossa.status, nossa.erro) == ("falhou", "moderacao_magalu")
    c = await _conversa_db(db, "c1")
    assert c.aguardando_resposta is True


async def test_chat_releitura_da_moderacao_com_30_conversas_ativas(
    db: AsyncSession, make_user, magalu, relogio
):
    """Fora da lista, a conversa com a NOSSA em moderação é relida pelo id
    mesmo com 30 conversas respondidas ativas na frente dela."""
    canal, integ = await _loja(db, make_user, "chat")
    base = T0 - timedelta(hours=10)
    magalu.conversas = [_conversa(f"c{i}", quando=base, nao_lidas=0) for i in range(30)]
    for i in range(30):
        magalu.msgs_chat[f"c{i}"] = [
            _msg_chat(f"m{i}a", texto="Oi", quando=base - timedelta(minutes=5)),
            _msg_chat(f"m{i}b", texto="Olá!", quando=base, de="SELLER"),
        ]
    magalu.conversas.append(_conversa("cN", quando=T0))
    magalu.msgs_chat["cN"] = [_msg_chat("n1", texto="Tem garantia?", quando=T0)]
    relogio["agora"] = T0 + timedelta(minutes=1)
    await _rodar(db, canal, integ)
    c = await _conversa_db(db, "cN")
    texto = "Tem sim: 90 dias."
    r = await magalu_atd.enviar_texto(db, c, integ, MagaluClient(dict(CREDS)), texto)
    nossa = await _nossa(db, c, texto, r, em=T0 + timedelta(minutes=2))

    # Nada mexeu na lista; a moderação RECUSOU a nossa.
    magalu.conversas = []
    magalu.conversa_por_id["cN"] = _conversa("cN", quando=T0, nao_lidas=0)
    magalu.msgs_chat["cN"].append(
        _msg_chat(
            "m-cN-nova",
            texto=texto,
            quando=T0 + timedelta(minutes=2),
            de="SELLER",
            moderacao="REJECTED",
            ref=r.payload["ref"],
        )
    )
    relogio["agora"] = T0 + timedelta(minutes=30)
    await _rodar(db, canal, integ)
    assert magalu.feitas("GET", r"/v0/conversations/cN")
    await db.refresh(nossa)
    assert (nossa.status, nossa.erro) == ("falhou", "moderacao_magalu")
    assert (await _conversa_db(db, "cN")).aguardando_resposta is True


# ─────────────── SAC ───────────────


async def test_sac_fila_com_prazo_do_protocolo_e_pedido(
    db: AsyncSession, make_user, magalu, relogio
):
    canal, integ = await _loja(db, make_user, "sac")
    magalu.fila = [_ticket("t1")]
    magalu.msgs_ticket["t1"] = [
        _msg_ticket("s1", texto="Meu pedido não chegou", quando=T0 - timedelta(hours=1)),
        _msg_ticket(
            "s2",
            texto="Vendedor, verifique a entrega.",
            quando=T0 - timedelta(minutes=50),
            de="channel",
        ),
    ]
    r = await _rodar(db, canal, integ)

    assert (r.status, r.conversas_novas, r.mensagens_novas, r.nao_lidas) == ("ok", 1, 2, 1)
    fila = magalu.feitas("GET", "/seller/v0/tickets")[0]
    assert fila.url.host == "api.magalu.com"
    assert fila.url.params["status"] == "waiting_seller"
    c = await _conversa_db(db, "t1")
    assert (c.canal, c.pedido_marketplace, c.anuncio_titulo) == ("sac", "LU-555", "Capinha Poofy")
    assert (c.comprador_id, c.comprador_nome) == ("customer-1", "Ana Souza")
    assert c.dados["protocolo"] == "202609300001"
    assert c.dados["pedido_mkt"]["pedido"] == "LU-555"
    # O prazo é o `due_date` do protocolo (data sem fuso = UTC), não o SLA fixo.
    assert c.aguardando_resposta is True
    assert c.prazo_resposta_em == T0 + timedelta(days=2)
    msgs = await _mensagens(db, c)
    assert [(x.externo_id, x.autor) for x in msgs] == [("s1", "cliente"), ("s2", "sistema")]
    assert msgs[1].payload["remetente"] == "magalu"


async def test_sac_prazo_da_magalu_vale_mesmo_vencido(db: AsyncSession, make_user, magalu, relogio):
    """O `due_date` e as mensagens vêm da MESMA leitura: protocolo que a Magalu
    já dá por vencido fica vencido — não ganha 24 h novas de SLA."""
    canal, integ = await _loja(db, make_user, "sac")
    magalu.fila = [_ticket("t1", prazo=T0 - timedelta(hours=5))]
    magalu.msgs_ticket["t1"] = [_msg_ticket("s1", texto="Oi?", quando=T0)]
    await _rodar(db, canal, integ)
    c = await _conversa_db(db, "t1")
    assert c.aguardando_resposta is True
    assert c.prazo_resposta_em == T0 - timedelta(hours=5)


def test_prazo_da_plataforma_gravado_antes_da_mensagem_nova_nao_vale():
    """A proteção do "prazo velho" fica só para o prazo LIDO antes da última
    mensagem do cliente (sem data de leitura, como antes: só se for posterior)."""
    base = {"plataforma": "magalu", "canal": "sac", "situacao": "aberta"}
    lido_antes = AtendimentoConversa(
        **base,
        ultima_do_cliente_em=T0,
        dados={
            "prazo_plataforma": (T0 - timedelta(hours=5)).isoformat(),
            "prazo_plataforma_lido_em": (T0 - timedelta(hours=6)).isoformat(),
        },
    )
    gravar.recalcular(lido_antes)
    assert lido_antes.prazo_resposta_em == T0 + timedelta(hours=24)

    sem_leitura = AtendimentoConversa(
        **base,
        ultima_do_cliente_em=T0,
        dados={"prazo_plataforma": (T0 - timedelta(hours=5)).isoformat()},
    )
    gravar.recalcular(sem_leitura)
    assert sem_leitura.prazo_resposta_em == T0 + timedelta(hours=24)

    lido_depois = AtendimentoConversa(
        **base,
        ultima_do_cliente_em=T0,
        dados={
            "prazo_plataforma": (T0 - timedelta(hours=5)).isoformat(),
            "prazo_plataforma_lido_em": (T0 + timedelta(minutes=1)).isoformat(),
        },
    )
    gravar.recalcular(lido_depois)
    assert lido_depois.prazo_resposta_em == T0 - timedelta(hours=5)


async def test_sac_resposta_no_portal_e_protocolo_encerrado(
    db: AsyncSession, make_user, magalu, relogio
):
    canal, integ = await _loja(db, make_user, "sac")
    magalu.fila = [_ticket("t1")]
    magalu.msgs_ticket["t1"] = [_msg_ticket("s1", texto="Cadê?", quando=T0)]
    await _rodar(db, canal, integ)

    # Respondido no portal: sai da fila da Magalu e aparece nos recentes.
    relogio["agora"] += timedelta(minutes=5)
    magalu.fila = []
    magalu.recentes = [_ticket("t1", status="waiting_customer", quando=T0 + timedelta(minutes=33))]
    magalu.msgs_ticket["t1"].append(
        _msg_ticket(
            "s2",
            texto="Já foi postado.",
            quando=T0 + timedelta(minutes=33),
            de="seller",
            destino="customer",
        )
    )
    await _rodar(db, canal, integ)
    c = await _conversa_db(db, "t1")
    assert c.aguardando_resposta is False
    assert c.dados["prazo_plataforma"] is None
    loja = [x for x in await _mensagens(db, c) if x.autor == "loja"]
    assert [(x.externo_id, x.origem) for x in loja] == [("s2", "externo")]

    # Encerrado pela Magalu: a conversa fecha.
    relogio["agora"] += timedelta(minutes=5)
    magalu.recentes = [
        _ticket("t1", status="closed", fechado=True, quando=T0 + timedelta(minutes=40))
    ]
    await _rodar(db, canal, integ)
    c = await _conversa_db(db, "t1")
    assert (c.situacao, c.bloqueio_motivo) == ("fechada", "magalu_closed")


async def test_envio_do_sac_vai_ao_cliente_e_a_nossa_e_reconhecida_pelo_code(
    db: AsyncSession, make_user, magalu, relogio
):
    canal, integ = await _loja(db, make_user, "sac")
    magalu.fila = [_ticket("t1")]
    magalu.msgs_ticket["t1"] = [_msg_ticket("s1", texto="Cadê?", quando=T0)]
    await _rodar(db, canal, integ)
    c = await _conversa_db(db, "t1")

    texto = "Olá, Ana! O pedido já foi postado e está a caminho."
    r = await magalu_atd.enviar_texto(db, c, integ, MagaluClient(dict(CREDS)), texto)
    assert (r.ok, r.externo_id) == (True, None)  # 202 sem id
    assert r.payload["transaction_id"] and r.payload["moderacao"] == "em_moderacao"
    pedido = magalu.feitas("POST", "/seller/v0/tickets/t1/messages")[-1]
    assert pedido.url.host == "api.magalu.com"
    corpo = json.loads(pedido.content)
    assert corpo["destination"] == "customer"
    assert corpo["message"] == texto
    assert corpo["owner"] == {"code": "davinci", "name": "poofy"}
    assert corpo["code"] == r.payload["ref"]
    nossa = await _nossa(db, c, texto, r)

    # Uma hora depois (fora da janela de 15 min da adoção pelo texto), a
    # leitura traz a nossa com o `code`: é reconhecida, sem linha nova.
    relogio["agora"] += timedelta(hours=1)
    magalu.fila = []
    magalu.recentes = [_ticket("t1", status="waiting_customer", quando=T0 + timedelta(hours=1))]
    magalu.msgs_ticket["t1"].append(
        _msg_ticket(
            "s9",
            texto=texto,
            quando=T0 + timedelta(hours=1),
            de="seller",
            destino="customer",
            code=r.payload["ref"],
        )
    )
    await _rodar(db, canal, integ)
    loja = [x for x in await _mensagens(db, c) if x.autor == "loja"]
    assert [(x.id, x.externo_id, x.origem) for x in loja] == [(nossa.id, "s9", "davinci_humano")]


async def test_sac_acima_do_limite_nao_sai(db: AsyncSession, make_user, magalu, relogio):
    canal, integ = await _loja(db, make_user, "sac")
    magalu.fila = [_ticket("t1")]
    magalu.msgs_ticket["t1"] = [_msg_ticket("s1", texto="Cadê?", quando=T0)]
    await _rodar(db, canal, integ)
    c = await _conversa_db(db, "t1")
    r = await magalu_atd.enviar_texto(db, c, integ, MagaluClient(dict(CREDS)), "a" * 3001)
    assert r.ok is False
    assert not magalu.feitas("POST", r"/seller/v0/tickets/.+/messages")


def _offsets_do_sac(magalu: MagaluFalsa) -> list[int]:
    return [
        int(x.url.params.get("_offset") or 0)
        for x in magalu.chamadas
        if x.url.path.startswith("/seller/v0/tickets") and x.method == "GET"
    ]


async def _total_conversas(db: AsyncSession) -> int:
    return int(await db.scalar(select(func.count()).select_from(AtendimentoConversa)))


async def test_sac_fila_grande_nao_passa_do_offset_100(
    db: AsyncSession, make_user, magalu, relogio
):
    """O SAC recusa `_offset` > 100 (422): a fila lê 2 páginas de 100, os de
    prazo mais curto primeiro — e o canal não cai."""
    canal, integ = await _loja(db, make_user, "sac")
    magalu.fila = [
        _ticket(f"t{i:03d}", quando=T0 - timedelta(minutes=i), prazo=T0 + timedelta(hours=1 + i))
        for i in range(160)
    ]
    for i in range(160):
        magalu.msgs_ticket[f"t{i:03d}"] = [
            _msg_ticket(f"s{i:03d}", texto="Cadê?", quando=T0 - timedelta(minutes=i))
        ]
    r = await _rodar(db, canal, integ)

    assert r.status == "ok", r.erro
    assert max(_offsets_do_sac(magalu)) <= 100
    fila = [
        x
        for x in magalu.feitas("GET", "/seller/v0/tickets")
        if x.url.params.get("status") == "waiting_seller"
    ]
    assert [(x.url.params["_offset"], x.url.params["_limit"]) for x in fila] == [
        ("0", "100"),
        ("100", "100"),
    ]
    assert {x.url.params["_sort"] for x in fila} == {"due_date:asc"}
    assert r.nao_lidas == 160
    assert await _total_conversas(db) > 0


async def test_sac_recentes_paginam_pela_data_e_o_cursor_anda(
    db: AsyncSession, make_user, magalu, relogio, monkeypatch
):
    """160 protocolos mexidos na 1ª janela (7 dias): a lista anda pela DATA
    (`updated_at_gte` do último da página cheia), nunca pelo offset; o cursor
    anda a cada rodada e, em poucas rodadas, tudo foi lido."""
    monkeypatch.setattr(get_settings(), "atendimento_sync_max_conversas", 500)
    canal, integ = await _loja(db, make_user, "sac")
    magalu.fila = [_ticket("t-fila")]
    magalu.msgs_ticket["t-fila"] = [_msg_ticket("f1", texto="Cadê?", quando=T0)]
    inicio = T0 - timedelta(days=6)
    magalu.recentes = [
        _ticket(f"r{i:03d}", status="waiting_customer", quando=inicio + timedelta(minutes=10 * i))
        for i in range(160)
    ]
    for i in range(160):
        magalu.msgs_ticket[f"r{i:03d}"] = [
            _msg_ticket(f"m{i:03d}", texto="Oi", quando=inicio + timedelta(minutes=10 * i))
        ]
    r = await _rodar(db, canal, integ)

    assert r.status == "ok", r.erro
    assert max(_offsets_do_sac(magalu)) <= 100
    recentes = [
        x for x in magalu.feitas("GET", "/seller/v0/tickets") if "status" not in x.url.params
    ]
    assert [x.url.params["_offset"] for x in recentes] == ["0", "0"]
    assert {x.url.params["_sort"] for x in recentes} == {"updated_at:asc"}
    assert recentes[1].url.params["updated_at_gte"] > recentes[0].url.params["updated_at_gte"]
    # O teto de leituras parou a rodada no meio: o cursor andou até onde leu.
    await db.refresh(canal)
    primeiro = canal.cursor["desde"]
    assert primeiro > inicio.isoformat(timespec="seconds")

    for _ in range(4):
        relogio["agora"] += timedelta(minutes=2)
        await _rodar(db, canal, integ)
    assert await _total_conversas(db) == 161
    await db.refresh(canal)
    assert canal.cursor["desde"] == relogio["agora"].isoformat(timespec="seconds")
    assert max(_offsets_do_sac(magalu)) <= 100


async def test_sac_recentes_falhando_nao_derrubam_a_fila(
    db: AsyncSession, make_user, magalu, relogio
):
    """A fila (a vez da loja, com prazo) é lida e gravada mesmo com a lista dos
    recentes falhando — e o cursor não anda."""
    canal, integ = await _loja(db, make_user, "sac")
    magalu.fila = [_ticket("t1")]
    magalu.msgs_ticket["t1"] = [_msg_ticket("s1", texto="Cadê?", quando=T0)]
    original = magalu._api

    def _api(metodo, caminho, params, corpo):
        if metodo == "GET" and caminho == "/seller/v0/tickets" and not params.get("status"):
            return httpx.Response(500, json={"slug": "internal_error"})
        return original(metodo, caminho, params, corpo)

    magalu._api = _api
    r = await _rodar(db, canal, integ)

    assert r.status == "ok"
    assert "HTTP 500" in (r.erro or "")
    c = await _conversa_db(db, "t1")
    assert c.aguardando_resposta is True
    assert "desde" not in (canal.cursor or {})


async def test_sac_protocolo_com_muitas_mensagens_le_as_mais_novas(
    db: AsyncSession, make_user, magalu, relogio
):
    """Mais de 200 mensagens: lê as 200 mais novas (offsets 0 e 100,
    `created_at:desc`) em vez de pedir offset 200 e levar 422."""
    canal, integ = await _loja(db, make_user, "sac")
    magalu.fila = [_ticket("t1")]
    magalu.msgs_ticket["t1"] = [
        _msg_ticket(f"s{i:03d}", texto=f"msg {i}", quando=T0 - timedelta(minutes=250 - i))
        for i in range(250)
    ]
    r = await _rodar(db, canal, integ)

    assert r.status == "ok", r.erro
    pedidos = magalu.feitas("GET", "/seller/v0/tickets/t1/messages")
    assert [x.url.params["_offset"] for x in pedidos] == ["0", "100"]
    assert {x.url.params["_sort"] for x in pedidos} == {"created_at:desc"}
    c = await _conversa_db(db, "t1")
    msgs = await _mensagens(db, c)
    assert len(msgs) == 200
    assert (msgs[0].externo_id, msgs[-1].externo_id) == ("s050", "s249")


async def test_sac_protocolo_que_sempre_falha_nao_segura_o_cursor(
    db: AsyncSession, make_user, magalu, relogio
):
    """Um protocolo cujas mensagens falham toda vez segura o cursor por 3
    rodadas; depois o cursor passa, e ele é relido pelo id até voltar."""
    canal, integ = await _loja(db, make_user, "sac")
    magalu.recentes = [_ticket("ok0", status="waiting_customer", quando=T0)]
    magalu.msgs_ticket["ok0"] = [_msg_ticket("m0", texto="oi", quando=T0)]
    await _rodar(db, canal, integ)

    ruim = _ticket("ruim", status="waiting_customer", quando=T0 + timedelta(minutes=40))
    magalu.recentes.append(ruim)
    magalu.ticket_por_id["ruim"] = ruim
    magalu.msgs_ticket["ruim"] = [_msg_ticket("mr", texto="oi", quando=T0 + timedelta(minutes=40))]
    magalu.forcar.append(
        (
            "GET",
            r"/seller/v0/tickets/ruim/messages",
            lambda: httpx.Response(500, json={"slug": "internal_error"}),
        )
    )
    cursores = []
    for k in range(1, 4):
        relogio["agora"] = T0 + timedelta(minutes=40 + 20 * k)
        tid = f"n{k}"
        magalu.recentes.append(
            _ticket(tid, status="waiting_customer", quando=relogio["agora"] - timedelta(minutes=5))
        )
        magalu.msgs_ticket[tid] = [
            _msg_ticket(f"m{tid}", texto="oi", quando=relogio["agora"] - timedelta(minutes=5))
        ]
        r = await _rodar(db, canal, integ)
        assert r.status == "ok"
        await db.refresh(canal)
        cursores.append(canal.cursor["desde"])
    # Duas rodadas seguradas no último que leu antes dele; na 3ª, passa.
    assert cursores[0] == cursores[1] == (T0 + timedelta(minutes=30)).isoformat(timespec="seconds")
    assert cursores[2] == relogio["agora"].isoformat(timespec="seconds")
    assert "ruim" in canal.cursor["pulados"]
    assert "ruim" in (r.erro or "")

    # Fora da janela da lista, ele é relido pelo id (e continua falhando).
    relogio["agora"] = T0 + timedelta(hours=6)
    await _rodar(db, canal, integ)
    assert not magalu.feitas("GET", "/seller/v0/tickets/ruim")  # ainda veio na lista
    relogio["agora"] += timedelta(minutes=16)
    await _rodar(db, canal, integ)
    assert len(magalu.feitas("GET", "/seller/v0/tickets/ruim")) == 1
    await db.refresh(canal)
    assert "ruim" in canal.cursor["pulados"]
    # Freio da releitura: não volta a cada rodada.
    relogio["agora"] += timedelta(minutes=2)
    await _rodar(db, canal, integ)
    assert len(magalu.feitas("GET", "/seller/v0/tickets/ruim")) == 1

    # Voltou: a releitura grava e ele sai da lista dos pulados.
    magalu.forcar.clear()
    relogio["agora"] += timedelta(minutes=16)
    await _rodar(db, canal, integ)
    c = await _conversa_db(db, "ruim")
    assert [m.externo_id for m in await _mensagens(db, c)] == ["mr"]
    await db.refresh(canal)
    assert "ruim" not in canal.cursor.get("pulados", {})
    assert canal.cursor["desde"] == relogio["agora"].isoformat(timespec="seconds")


async def test_sac_pulado_que_volta_na_lista_sai_das_falhas_e_dos_pulados(
    db: AsyncSession, make_user, magalu, relogio
):
    canal, integ = await _loja(db, make_user, "sac")
    ruim = _ticket("ruim", status="waiting_customer", quando=T0)
    magalu.recentes = [ruim]
    magalu.msgs_ticket["ruim"] = [_msg_ticket("mr", texto="oi", quando=T0)]
    magalu.forcar.append(
        (
            "GET",
            r"/seller/v0/tickets/ruim/messages",
            lambda: httpx.Response(500, json={"slug": "internal_error"}),
        )
    )
    for k in range(4):
        relogio["agora"] = T0 + timedelta(minutes=30 + 2 * k)
        await _rodar(db, canal, integ)
    await db.refresh(canal)
    assert canal.cursor["falhas"] == {"ruim": 4}
    assert "ruim" in canal.cursor["pulados"]

    # Voltou ainda dentro da janela da lista: sai das duas contas.
    magalu.forcar.clear()
    relogio["agora"] += timedelta(minutes=2)
    await _rodar(db, canal, integ)
    await db.refresh(canal)
    assert "falhas" not in canal.cursor
    assert "pulados" not in canal.cursor
    assert (await _conversa_db(db, "ruim")).externo_id == "ruim"


async def test_sac_mediacao_cobrando_a_loja_entra_na_fila_com_o_prazo(
    db: AsyncSession, make_user, magalu, relogio
):
    """A vez no SAC é o STATUS do protocolo: a mediação da Magalu (channel →
    seller) devolve o protocolo à loja (waiting_seller + due_date) depois da
    resposta dada no portal — entra na fila com o prazo da Magalu. E a
    resposta a essa cobrança vai para a Magalu, não ao comprador."""
    canal, integ = await _loja(db, make_user, "sac")
    magalu.fila = [_ticket("t1")]
    magalu.msgs_ticket["t1"] = [_msg_ticket("s1", texto="Cadê meu pedido?", quando=T0)]
    await _rodar(db, canal, integ)

    # Respondido no portal: a vez passa ao cliente.
    relogio["agora"] += timedelta(minutes=10)
    magalu.fila = []
    magalu.recentes = [_ticket("t1", status="waiting_customer", quando=T0 + timedelta(minutes=35))]
    magalu.msgs_ticket["t1"].append(
        _msg_ticket(
            "s2",
            texto="Já foi postado.",
            quando=T0 + timedelta(minutes=35),
            de="seller",
            destino="customer",
        )
    )
    await _rodar(db, canal, integ)
    c = await _conversa_db(db, "t1")
    assert (c.aguardando_resposta, c.situacao) == (False, "respondida")

    # A mediação cobra a loja e o protocolo VOLTA para ela, com prazo.
    relogio["agora"] = T0 + timedelta(hours=2, minutes=5)
    prazo = T0 + timedelta(hours=26)
    t = _ticket("t1", status="waiting_seller", quando=T0 + timedelta(hours=2), prazo=prazo)
    magalu.fila = [t]
    magalu.recentes = [t]
    magalu.msgs_ticket["t1"].append(
        _msg_ticket(
            "s3",
            texto="Vendedor, envie o comprovante de entrega.",
            quando=T0 + timedelta(hours=2),
            de="channel",
            destino="seller",
        )
    )
    await _rodar(db, canal, integ)
    c = await _conversa_db(db, "t1")
    assert c.aguardando_resposta is True
    assert c.prazo_resposta_em == prazo
    assert c.situacao == "aberta"

    r = await magalu_atd.enviar_texto(
        db, c, integ, MagaluClient(dict(CREDS)), "Segue o comprovante de entrega."
    )
    assert r.ok
    assert magalu.corpo("POST", "/seller/v0/tickets/t1/messages")["destination"] == "channel"
    assert r.payload["destino"] == "channel"


async def test_sac_cliente_falando_com_a_magalu_nao_e_fila_da_loja(
    db: AsyncSession, make_user, magalu, relogio
):
    """waiting_marketplace: o cliente escreveu para a Magalu (destination
    channel) — não é a vez da loja, nem entra na fila."""
    canal, integ = await _loja(db, make_user, "sac")
    magalu.recentes = [_ticket("t1", status="waiting_marketplace", prazo=None)]
    magalu.msgs_ticket["t1"] = [
        _msg_ticket("s1", texto="Magalu, quero cancelar", quando=T0, destino="channel")
    ]
    await _rodar(db, canal, integ)
    c = await _conversa_db(db, "t1")
    assert c.aguardando_resposta is False
    assert c.prazo_resposta_em is None


async def test_sac_loja_falando_com_a_magalu_nao_responde_o_comprador(
    db: AsyncSession, make_user, magalu, relogio
):
    """O cliente pediu à loja; a loja escreveu só para a Magalu (destination
    channel). Com o protocolo na vez da loja, continua na fila."""
    canal, integ = await _loja(db, make_user, "sac")
    magalu.fila = [_ticket("t1", quando=T0 + timedelta(minutes=20))]
    magalu.msgs_ticket["t1"] = [
        _msg_ticket("s1", texto="Cadê meu pedido?", quando=T0),
        _msg_ticket(
            "s2",
            texto="Magalu, podem verificar a transportadora?",
            quando=T0 + timedelta(minutes=20),
            de="seller",
            destino="channel",
        ),
    ]
    await _rodar(db, canal, integ)
    c = await _conversa_db(db, "t1")
    assert c.aguardando_resposta is True
    assert c.prazo_resposta_em == T0 + timedelta(days=2)


async def test_sac_nossa_recusada_em_silencio_volta_para_a_fila(
    db: AsyncSession, make_user, magalu, relogio
):
    """A nossa resposta sai (202) e some: a Magalu não mostra a recusada. Com o
    protocolo ainda na vez da loja depois da folga, ele volta à fila com o
    prazo da Magalu — em vez de ficar "respondida" até estourar."""
    canal, integ = await _loja(db, make_user, "sac")
    prazo = T0 + timedelta(days=2)
    magalu.fila = [_ticket("t1", prazo=prazo)]
    magalu.msgs_ticket["t1"] = [_msg_ticket("s1", texto="Cadê meu pedido?", quando=T0)]
    await _rodar(db, canal, integ)
    c = await _conversa_db(db, "t1")
    texto = "Olá! Já verificamos e o pedido está a caminho."
    r = await magalu_atd.enviar_texto(db, c, integ, MagaluClient(dict(CREDS)), texto)
    assert r.payload["destino"] == "customer"
    envio = T0 + timedelta(minutes=31)
    await _nossa(db, c, texto, r, em=envio)
    await db.refresh(c)
    assert c.aguardando_resposta is False  # saiu da fila na hora do envio

    # Logo depois: a Magalu ainda não virou o status — dentro da folga.
    relogio["agora"] = T0 + timedelta(hours=1)
    magalu.fila = [_ticket("t1", quando=envio, prazo=prazo)]
    await _rodar(db, canal, integ)
    c = await _conversa_db(db, "t1")
    assert c.aguardando_resposta is False

    # Horas depois, ainda com a loja: volta à fila, com o prazo do protocolo.
    relogio["agora"] = T0 + timedelta(hours=6)
    await _rodar(db, canal, integ)
    c = await _conversa_db(db, "t1")
    assert c.aguardando_resposta is True
    assert c.prazo_resposta_em == prazo
    assert c.situacao == "aberta"


# ─────────────── limite e permissão ───────────────


async def test_429_para_a_rodada_e_segura_o_canal(db: AsyncSession, make_user, magalu, relogio):
    canal, integ = await _loja(db, make_user, "pergunta")
    magalu.forcar.append(
        ("GET", r"/v0/questions", lambda: httpx.Response(429, json={"slug": "too_many_requests"}))
    )
    r = await _rodar(db, canal, integ)
    assert r.status == "erro" and "429" in r.erro
    # O cliente esperou e tentou 3 vezes; a rodada parou ali.
    assert len(magalu.feitas("GET", "/v0/questions")) == 3
    assert canal.cursor["espera_ate"]

    # Dentro da espera, a rodada nem vai à Magalu.
    relogio["agora"] += timedelta(seconds=30)
    r = await _rodar(db, canal, integ)
    assert r.status == "erro"
    assert len(magalu.feitas("GET", "/v0/questions")) == 3

    # Passada a espera, lê de novo.
    relogio["agora"] += timedelta(minutes=2)
    magalu.forcar.clear()
    magalu.perguntas["WAITING_RESPONSE"] = [_pergunta("q1")]
    r = await _rodar(db, canal, integ)
    assert (r.status, r.conversas_novas) == ("ok", 1)
    assert "espera_ate" not in canal.cursor


async def test_429_no_meio_guarda_o_que_leu_e_o_cursor_nao_passa(
    db: AsyncSession, make_user, magalu, relogio
):
    canal, integ = await _loja(db, make_user, "chat")
    magalu.conversas = [_conversa("c1"), _conversa("c2", quando=T0 + timedelta(minutes=1))]
    magalu.msgs_chat["c1"] = [_msg_chat("m1", texto="Oi", quando=T0)]
    magalu.forcar.append(
        ("GET", r"/v0/conversations/c2/messages", lambda: httpx.Response(429, json={}))
    )
    r = await _rodar(db, canal, integ)
    assert r.status == "ok"
    assert "429" in r.erro
    assert (await _conversa_db(db, "c1")).aguardando_resposta is True
    assert await db.scalar(select(func.count()).where(AtendimentoConversa.externo_id == "c2")) == 0
    # O cursor parou na c1: a c2 vem de novo na próxima rodada.
    assert canal.cursor["desde"] == T0.isoformat(timespec="seconds")
    assert canal.cursor["espera_ate"]


async def test_sem_escopo_vira_sem_permissao_com_reautorizacao(
    db: AsyncSession, make_user, magalu, relogio
):
    canal, integ = await _loja(db, make_user, "chat")
    magalu.forcar.append(
        (
            "GET",
            r"/v0/conversations",
            lambda: httpx.Response(403, json={"slug": "insufficient_scope"}),
        )
    )
    r = await _rodar(db, canal, integ)
    assert r.status == "sem_escopo"
    assert "reautorize a Magalu (Integrações › Autorizar no Magalu)" in r.erro
    assert await db.scalar(select(func.count()).select_from(AtendimentoConversa)) == 0


async def test_403_em_html_da_borda_nao_e_falta_de_escopo(
    db: AsyncSession, make_user, magalu, relogio
):
    canal, integ = await _loja(db, make_user, "pergunta")
    magalu.forcar.append(
        (
            "GET",
            r"/v0/questions",
            lambda: httpx.Response(
                403, text="<html>Azion</html>", headers={"content-type": "text/html"}
            ),
        )
    )
    r = await _rodar(db, canal, integ)
    assert r.status == "erro"


async def test_sac_sem_escopo_das_mensagens_e_sem_permissao(
    db: AsyncSession, make_user, magalu, relogio
):
    """O SAC tem um escopo para os protocolos e outro para as mensagens."""
    canal, integ = await _loja(db, make_user, "sac")
    magalu.fila = [_ticket("t1")]
    magalu.forcar.append(
        ("GET", r"/seller/v0/tickets/t1/messages", lambda: httpx.Response(403, json={}))
    )
    r = await _rodar(db, canal, integ)
    assert r.status == "sem_escopo"
    assert "reautorize a Magalu" in r.erro


async def test_sync_marca_o_canal_sem_escopo(
    db: AsyncSession, make_user, magalu, relogio, monkeypatch
):
    """Pelo cron: o canal fica `sem_escopo` com o motivo, como o TikTok."""
    canal, _integ = await _loja(db, make_user, "pergunta")
    magalu.forcar.append(
        ("GET", r"/v0/questions", lambda: httpx.Response(401, json={"slug": "unauthorized"}))
    )
    # 401 → o cliente tenta renovar o token uma vez (sob a trava da linha).
    magalu.forcar.append(
        ("POST", r"/oauth/token", lambda: httpx.Response(200, json={"access_token": "novo"}))
    )
    monkeypatch.setattr(get_settings(), "magalu_client_id", "cid")
    monkeypatch.setattr(get_settings(), "magalu_client_secret", "csec")
    r = await sync.sincronizar_canal(canal.id)
    assert r.status == "sem_escopo"
    await db.refresh(canal)
    assert canal.status == "sem_escopo"
    assert "reautorize a Magalu" in canal.ultimo_erro


# ─────────────── o read_by ───────────────


async def test_nenhuma_chamada_ao_read_by(db: AsyncSession, make_user, magalu, relogio):
    """Lê e responde as três caixas; a fixture `magalu` confere, no fim, que o
    `read_by` nunca foi chamado — e o cliente nem tem como chamá-lo."""
    for nome, preparar in (
        ("pergunta", lambda: magalu.perguntas.__setitem__("WAITING_RESPONSE", [_pergunta("q1")])),
        ("chat", lambda: magalu.conversas.append(_conversa("c1"))),
        ("sac", lambda: magalu.fila.append(_ticket("t1"))),
    ):
        preparar()
        magalu.msgs_chat["c1"] = [_msg_chat("m1", texto="Oi", quando=T0)]
        magalu.msgs_ticket["t1"] = [_msg_ticket("s1", texto="Oi", quando=T0)]
        canal, integ = await _loja(db, make_user, nome)
        await _rodar(db, canal, integ)
        externo = {"pergunta": "q:q1", "chat": "c1", "sac": "t1"}[nome]
        c = (
            await db.execute(
                select(AtendimentoConversa).where(
                    AtendimentoConversa.externo_id == externo,
                    AtendimentoConversa.integration_id == integ.id,
                )
            )
        ).scalar_one()
        r = await magalu_atd.enviar_texto(db, c, integ, MagaluClient(dict(CREDS)), "Olá!")
        assert r.ok, (nome, r.erro)
    assert magalu.feitas("POST", r"/v0/questions/q1/answer")
    assert magalu.feitas("POST", r"/v0/conversations/c1/messages")
    assert magalu.feitas("POST", r"/seller/v0/tickets/t1/messages")

    # E não há caminho para ele: nenhum método do cliente fala em leitura, e o
    # `_request` recusa antes de sair.
    assert not [n for n in dir(MagaluClient) if "read" in n.lower()]
    total = len(magalu.chamadas)
    with pytest.raises(RuntimeError, match="read_by"):
        await MagaluClient(dict(CREDS))._request(
            "PATCH",
            "/v0/conversations/c1/read_by",
            base=magalu_cliente.MAGALU_SERVICES_BASE,
        )
    assert len(magalu.chamadas) == total
