# ruff: noqa: S105, S106  (chave de um cliente falso, nada real)
"""Carrefour na caixa de atendimento: as mensagens da Mirakl (30/10/2026).

A Mirakl é falsa (respx, sem rede): o `CarrefourClient` DE VERDADE fala com um
servidor falso que segue o OpenAPI oficial do Seller API (M11 `GET
/api/inbox/threads`, M12 `POST /api/inbox/threads/{id}/message`, lido em
30/10). Nenhuma chamada real, nenhuma mensagem enviada de verdade.

O que estes testes garantem:

- a leitura grava uma conversa por thread, com o pedido, o comprador, quem
  falou (comprador, loja, Carrefour) e a fila pela VEZ que a Mirakl diz
  (`shop_reply_needed_since`), sem chamar o A01 (limite de 1 por dia);
- rodar de novo não duplica nada, a thread parada não é regravada, a
  paginação vai por `page_token` e o cursor só anda com a lista inteira;
- a resposta dada no portal tira da fila; a NOSSA voltando é reconhecida
  (pelo `message_id` do 201, ou pelo texto quando o envio ficou ambíguo);
- o envio (M12): multipart com `message_input`, `shop_id`, para quem falou por
  último (comprador ou Carrefour); 5xx/timeout = ambíguo e não repete; 401 =
  a chave recusada; o curto e o longo demais não saem; e nada além do M12 é
  escrita (o estoque continua só leitura);
- 401 na leitura = `sem_escopo` com o que fazer; 429 segura o canal;
- pela rota: a pessoa responde a conversa da loja DELA, e cada vendedor vê só
  o Carrefour dele (israel → VR, marrocos → Poofy).
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
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
    UserRole,
)
from app.security.cipher import encrypt_json
from app.services.atendimento import carrefour as carrefour_atd
from app.services.atendimento import enviar, escopo, gravar, ia, lojas, pendente, sync
from app.services.atendimento.constantes import (
    CANAIS_POR_PLATAFORMA,
    CHAVE_VEZ_DA_LOJA,
    PLATAFORMAS,
    limite_caracteres,
)
from app.services.marketplaces.base import SyncStatus
from app.services.marketplaces.carrefour import CarrefourClient
from tests.lojas_da_pessoa import dar_lojas

T0 = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)
CREDS = {"api_key": "chave-falsa-0001", "shop_id": "2001"}
HOST = "marketplace.carrefour.com.br"
URL = "/api/atendimento"
OUTRO = "quem-mexe@davinci-test.com"


# ─────────────── formatos da doc (OpenAPI do Seller API) ───────────────


def _z(quando: datetime) -> str:
    return quando.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _msg(
    mid: str,
    texto: str,
    quando: datetime,
    *,
    de: str = "CUSTOMER_USER",
    para: tuple[str, ...] = ("SHOP",),
    anexos: list | None = None,
) -> dict:
    nomes = {"CUSTOMER_USER": "Ana Souza", "SHOP_USER": "Poofy", "OPERATOR_USER": "Carrefour"}
    org = {"CUSTOMER_USER": "CUSTOMER", "SHOP_USER": "SHOP", "OPERATOR_USER": "OPERATOR"}[de]
    return {
        "id": mid,
        "body": texto,
        "date_created": _z(quando),
        "from": {
            "type": de,
            "display_name": nomes[de],
            "organization_details": {"type": org, "display_name": nomes[de]},
        },
        "to": [{"type": t, "display_name": t.title()} for t in para],
        "attachments": anexos or [],
    }


def _thread(
    tid: str,
    mensagens: list[dict],
    *,
    atualizada: datetime = T0,
    vez: datetime | None = T0,
    pedido: str | None = "P-1001-A",
    oferta: tuple[str, str] | None = None,
    com_comprador: bool = True,
) -> dict:
    autorizados = [{"type": "SHOP", "id": "2001", "display_name": "Poofy"}]
    if com_comprador:
        autorizados.insert(0, {"type": "CUSTOMER", "id": "cust-9", "display_name": "Ana Souza"})
    autorizados.append({"type": "OPERATOR", "display_name": "Carrefour"})
    entidades = []
    if pedido:
        entidades.append({"id": pedido, "label": pedido, "type": "MMP_ORDER"})
    if oferta:
        entidades.append({"id": oferta[0], "label": oferta[1], "type": "MMP_OFFER"})
    if not entidades:
        entidades.append({"id": "2001", "label": "Poofy", "type": "SELLER_OPERATOR"})
    ultima = mensagens[-1] if mensagens else None
    return {
        "id": tid,
        "topic": {"type": "REASON_CODE", "value": "Atraso na entrega"},
        "entities": entidades,
        "authorized_participants": autorizados,
        "current_participants": autorizados[:2],
        "date_created": _z(T0 - timedelta(hours=1)),
        "date_updated": _z(atualizada),
        "metadata": {
            "last_message_date": ultima["date_created"] if ultima else _z(atualizada),
            "last_sender": dict(ultima["from"]) if ultima else {"type": "SHOP_USER"},
            "shop_reply_needed_since": _z(vez) if vez else None,
            "total_count": len(mensagens),
        },
        "messages": mensagens,
    }


# ─────────────── a Mirakl falsa ───────────────


def _message_input(request: httpx.Request) -> dict:
    """A parte `message_input` (application/json) do multipart do M12."""
    corpo = request.content.decode()
    achado = re.search(
        r'name="message_input"\r\nContent-Type: application/json\r\n\r\n(.*?)\r\n--',
        corpo,
        re.S,
    )
    assert achado, "o M12 é multipart com a parte message_input em JSON"
    return json.loads(achado.group(1))


class MiraklFalsa:
    """O Seller API do Carrefour, nos formatos do OpenAPI."""

    def __init__(self) -> None:
        self.threads: list[dict] = []
        self.forcar: list[tuple[str, str, Callable[[], httpx.Response]]] = []
        self.chamadas: list[httpx.Request] = []
        self._paginas: dict[str, list[dict]] = {}
        self._enviadas = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.chamadas.append(request)
        metodo, caminho, params = request.method, request.url.path, request.url.params
        assert request.headers.get("authorization") == CREDS["api_key"]
        assert params.get("shop_id") == CREDS["shop_id"], "toda chamada diz a loja"
        for m, padrao, fabrica in self.forcar:
            if m == metodo and re.fullmatch(padrao, caminho):
                return fabrica()
        if metodo == "GET" and caminho == "/api/inbox/threads":
            limite = int(params["limit"])
            if params.get("page_token"):
                # Com page_token a Mirakl ignora os outros filtros.
                restantes = self._paginas.pop(params["page_token"])
            else:
                desde = params.get("updated_since")
                restantes = [
                    t
                    for t in self.threads
                    if not desde or t["date_updated"] >= desde.replace("+00:00", "Z")
                ]
                if params.get("with_messages") != "true":
                    restantes = [{k: v for k, v in t.items() if k != "messages"} for t in restantes]
            lote, resto = restantes[:limite], restantes[limite:]
            corpo: dict[str, Any] = {"data": lote}
            if resto:
                token = f"tok{len(self._paginas) + len(self.chamadas)}"
                self._paginas[token] = resto
                corpo["next_page_token"] = token
            return httpx.Response(200, json=corpo)
        if metodo == "POST" and (m := re.fullmatch(r"/api/inbox/threads/([^/]+)/message", caminho)):
            _message_input(request)
            self._enviadas += 1
            return httpx.Response(
                201, json={"message_id": f"msg-nova-{self._enviadas}", "thread_id": m[1]}
            )
        return httpx.Response(404, json={"message": "rota desconhecida", "status": 404})

    def feitas(self, metodo: str, padrao: str) -> list[httpx.Request]:
        return [r for r in self.chamadas if r.method == metodo and re.fullmatch(padrao, r.url.path)]


@pytest.fixture
def mirakl():
    falsa = MiraklFalsa()
    with respx.mock(assert_all_called=False) as router:
        router.route(host=HOST).mock(side_effect=falsa)
        yield falsa
    # A01 tem limite de 1 por dia e não é preciso para as mensagens.
    assert not falsa.feitas("GET", "/api/account")
    # Nada de escrita além da resposta na thread (M12).
    escritas = [r for r in falsa.chamadas if r.method != "GET"]
    assert all(re.fullmatch(r"/api/inbox/threads/[^/]+/message", r.url.path) for r in escritas)


@pytest.fixture
def relogio(monkeypatch):
    atual = {"agora": T0 + timedelta(minutes=30)}
    monkeypatch.setattr(carrefour_atd, "_agora", lambda: atual["agora"])
    return atual


async def _loja(
    db: AsyncSession, make_user, nome: str = "Carrefour Poofy", *, modo: str = "humano"
) -> tuple[AtendimentoCanal, Integration]:
    user = await make_user()
    integ = Integration(
        user_id=user.id,
        platform=IntegrationPlatform.CARREFOUR,
        name=nome,
        credentials=encrypt_json(CREDS),
    )
    db.add(integ)
    await db.commit()
    await db.refresh(integ)
    canal = AtendimentoCanal(
        integration_id=integ.id, plataforma="carrefour", canal="chat", modo=modo, status="ok"
    )
    db.add(canal)
    await db.commit()
    return canal, integ


async def _rodar(db: AsyncSession, canal: AtendimentoCanal, integ: Integration):
    resultado = await carrefour_atd.sincronizar(db, canal, integ, CarrefourClient(dict(CREDS)))
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
    return list(
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


async def _total_mensagens(db: AsyncSession) -> int:
    return int(await db.scalar(select(func.count()).select_from(AtendimentoMensagem)))


async def _nossa(
    db: AsyncSession, conversa: AtendimentoConversa, texto: str, resultado, *, em: datetime
) -> AtendimentoMensagem:
    """Grava a resposta como o `enviar` grava depois do envio (`revisar` se ambíguo)."""
    m = AtendimentoMensagem(
        conversa_id=conversa.id,
        externo_id=resultado.externo_id,
        autor="loja",
        origem="davinci_humano",
        tipo="texto",
        texto=texto,
        enviada_em=em,
        status="enviada" if resultado.ok else "revisar",
        payload={"envio": dict(resultado.payload)},
    )
    db.add(m)
    gravar.recalcular(conversa, [m])
    await db.commit()
    return m


# ─────────────── vocabulário e ligações ───────────────


def test_carrefour_e_lido_por_api_com_uma_caixa():
    assert "carrefour" in PLATAFORMAS
    assert CANAIS_POR_PLATAFORMA["carrefour"] == ("chat",)
    assert limite_caracteres("carrefour", "chat") == 2000
    assert enviar.adaptador("carrefour") is carrefour_atd
    assert ia._PLATAFORMA_NOME["carrefour"] == "Carrefour"
    assert ("carrefour", "chat") in ia._CANAL_DESCRICAO
    assert lojas.sem_prefixo("Carrefour VR", "carrefour") == "VR"


async def test_garantir_canais_cria_o_chat_do_carrefour_em_observar(db: AsyncSession, make_user):
    user = await make_user()
    integ = Integration(
        user_id=user.id,
        platform=IntegrationPlatform.CARREFOUR,
        name="Carrefour VR",
        credentials=encrypt_json(CREDS),
    )
    db.add(integ)
    await db.commit()
    await sync.garantir_canais(db)
    await db.commit()
    canais = (
        await db.execute(
            select(
                AtendimentoCanal.plataforma, AtendimentoCanal.canal, AtendimentoCanal.modo
            ).where(AtendimentoCanal.integration_id == integ.id)
        )
    ).all()
    assert canais == [("carrefour", "chat", "observar")]


async def test_estoque_continua_so_leitura(mirakl):
    r = await CarrefourClient(dict(CREDS)).update_stock(None, 5, force=True)  # type: ignore[arg-type]
    assert (r.status, r.error_code) == (SyncStatus.REQUIRES_REVIEW, "carrefour_write_not_enabled")
    assert mirakl.chamadas == []


async def test_resposta_com_thread_invalida_nao_vai_a_mirakl(mirakl):
    cliente = CarrefourClient(dict(CREDS))
    for tid in ("../account", "a/b", "", "x" * 65):
        with pytest.raises(ValueError):
            await cliente.reply_thread(tid, "Olá, tudo bem?", [{"type": "CUSTOMER"}])
    with pytest.raises(ValueError):
        await cliente.reply_thread("t-1", "Olá", [{"type": "SHOP"}])
    assert mirakl.chamadas == []


# ─────────────── leitura ───────────────


async def test_le_thread_mensagens_pedido_e_a_vez_da_mirakl(
    db: AsyncSession, make_user, mirakl, relogio
):
    canal, integ = await _loja(db, make_user)
    mirakl.threads = [
        _thread(
            "t-1",
            [
                _msg("m1", "Meu pedido não chegou", T0 - timedelta(minutes=10)),
                _msg("m2", "Alguém?", T0),
            ],
            vez=T0 - timedelta(minutes=10),
        )
    ]
    r = await _rodar(db, canal, integ)

    assert (r.status, r.conversas_novas, r.mensagens_novas) == ("ok", 1, 2)
    lista = mirakl.feitas("GET", "/api/inbox/threads")[0]
    assert lista.url.params["with_messages"] == "true"
    assert lista.url.params["limit"] == "100"
    # Primeira leitura: 30 dias para trás (com a folga do cursor).
    assert lista.url.params["updated_since"] == _z(
        relogio["agora"] - timedelta(days=30) - timedelta(minutes=15)
    )
    c = await _conversa_db(db, "t-1")
    assert (c.plataforma, c.canal, c.pedido_marketplace) == ("carrefour", "chat", "P-1001-A")
    assert (c.comprador_id, c.comprador_nome, c.conta) == ("cust-9", "Ana Souza", "Carrefour Poofy")
    assert c.dados[CHAVE_VEZ_DA_LOJA] == (T0 - timedelta(minutes=10)).isoformat()
    assert c.dados["autorizados"] == ["CUSTOMER", "OPERATOR", "SHOP"]
    assert c.dados["assunto"] == "Atraso na entrega"
    assert c.aguardando_resposta is True
    assert pendente.falta_responder(c, relogio["agora"]) is True
    msgs = await _mensagens(db, c)
    assert [(x.externo_id, x.autor, x.origem) for x in msgs] == [
        ("m1", "cliente", "cliente"),
        ("m2", "cliente", "cliente"),
    ]
    assert canal.cursor["desde"] == relogio["agora"].isoformat(timespec="seconds")


async def test_rodar_de_novo_nao_duplica_e_a_thread_parada_nao_e_regravada(
    db: AsyncSession, make_user, mirakl, relogio
):
    canal, integ = await _loja(db, make_user)
    mirakl.threads = [_thread("t-1", [_msg("m1", "Oi", T0)], atualizada=T0 + timedelta(minutes=20))]
    await _rodar(db, canal, integ)
    c = await _conversa_db(db, "t-1")
    relida = c.dados["relida_em"]

    relogio["agora"] += timedelta(minutes=2)
    r = await _rodar(db, canal, integ)
    # A folga do cursor traz a thread de novo, mas nada mexeu nela.
    segunda = mirakl.feitas("GET", "/api/inbox/threads")[-1]
    assert segunda.url.params["updated_since"] == _z(T0 + timedelta(minutes=15))
    assert (r.conversas_novas, r.conversas_atualizadas, r.mensagens_novas) == (0, 0, 0)
    assert await _total_mensagens(db) == 1
    assert (await _conversa_db(db, "t-1")).dados["relida_em"] == relida

    # Mensagem nova na thread: só ela entra.
    relogio["agora"] += timedelta(minutes=2)
    mirakl.threads = [
        _thread(
            "t-1",
            [_msg("m1", "Oi", T0), _msg("m2", "Ainda aí?", T0 + timedelta(minutes=33))],
            atualizada=T0 + timedelta(minutes=33),
        )
    ]
    r = await _rodar(db, canal, integ)
    assert (r.conversas_novas, r.conversas_atualizadas, r.mensagens_novas) == (0, 1, 1)
    assert await _total_mensagens(db) == 2


async def test_pagina_pelo_page_token_e_o_cursor_so_anda_com_a_lista_inteira(
    db: AsyncSession, make_user, mirakl, relogio, monkeypatch
):
    monkeypatch.setattr(carrefour_atd, "PAGINA", 2)
    canal, integ = await _loja(db, make_user)
    mirakl.threads = [
        _thread(
            f"t-{i}",
            [_msg(f"m{i}", "Oi", T0 + timedelta(minutes=i))],
            atualizada=T0 + timedelta(minutes=i),
        )
        for i in range(5)
    ]
    r = await _rodar(db, canal, integ)
    pedidos = mirakl.feitas("GET", "/api/inbox/threads")
    assert len(pedidos) == 3 and r.conversas_novas == 5
    assert "page_token" not in pedidos[0].url.params
    for p in pedidos[1:]:
        assert p.url.params["page_token"].startswith("tok")
        assert "updated_since" not in p.url.params  # a Mirakl ignora os filtros com o token
    assert canal.cursor["desde"] == relogio["agora"].isoformat(timespec="seconds")

    # Teto de páginas: a lista cortada grava o que veio e o cursor não anda.
    monkeypatch.setattr(carrefour_atd, "MAX_PAGINAS", 1)
    desde = canal.cursor["desde"]
    relogio["agora"] += timedelta(minutes=2)
    mirakl.threads = [
        _thread(f"n-{i}", [_msg(f"n{i}", "Olá", relogio["agora"])], atualizada=relogio["agora"])
        for i in range(3)
    ]
    r = await _rodar(db, canal, integ)
    assert r.status == "ok" and "o resto na próxima" in r.erro
    assert r.conversas_novas == 2
    assert canal.cursor["desde"] == desde


async def test_resposta_no_portal_tira_da_fila(db: AsyncSession, make_user, mirakl, relogio):
    canal, integ = await _loja(db, make_user)
    mirakl.threads = [
        _thread(
            "t-1",
            [
                _msg("m1", "Tem garantia?", T0),
                _msg(
                    "m2",
                    "Tem sim, 90 dias.",
                    T0 + timedelta(minutes=5),
                    de="SHOP_USER",
                    para=("CUSTOMER",),
                ),
            ],
            vez=None,
            atualizada=T0 + timedelta(minutes=5),
        )
    ]
    await _rodar(db, canal, integ)
    c = await _conversa_db(db, "t-1")
    assert c.aguardando_resposta is False and c.situacao == "respondida"
    assert pendente.falta_responder(c, relogio["agora"]) is False
    loja = [x for x in await _mensagens(db, c) if x.autor == "loja"]
    assert [(x.origem, x.status, x.payload) for x in loja] == [
        ("externo", "enviada", {"para": ["CUSTOMER"]})
    ]


async def test_dispensada_no_portal_sai_da_fila_sem_mensagem_nova(
    db: AsyncSession, make_user, mirakl, relogio
):
    canal, integ = await _loja(db, make_user)
    mirakl.threads = [_thread("t-1", [_msg("m1", "Obrigada!", T0)], atualizada=T0)]
    await _rodar(db, canal, integ)
    assert (await _conversa_db(db, "t-1")).aguardando_resposta is True

    # "Não precisa de resposta" no portal: a Mirakl zera a vez, sem mensagem nova.
    relogio["agora"] += timedelta(minutes=2)
    mirakl.threads = [
        _thread("t-1", [_msg("m1", "Obrigada!", T0)], atualizada=relogio["agora"], vez=None)
    ]
    await _rodar(db, canal, integ)
    c = await _conversa_db(db, "t-1")
    assert c.aguardando_resposta is False
    assert await _total_mensagens(db) == 1


async def test_carrefour_cobrando_a_loja_entra_na_fila_e_a_resposta_vai_para_ele(
    db: AsyncSession, make_user, mirakl, relogio
):
    canal, integ = await _loja(db, make_user)
    mirakl.threads = [
        _thread(
            "t-1",
            [
                _msg("m1", "Cadê meu pedido?", T0 - timedelta(hours=3)),
                _msg(
                    "m2",
                    "Está a caminho.",
                    T0 - timedelta(hours=2),
                    de="SHOP_USER",
                    para=("CUSTOMER",),
                ),
                _msg(
                    "m3",
                    "Loja, envie o rastreio ao cliente.",
                    T0,
                    de="OPERATOR_USER",
                    para=("SHOP",),
                ),
            ],
            vez=T0,
        )
    ]
    await _rodar(db, canal, integ)
    c = await _conversa_db(db, "t-1")
    autores = [(x.autor, x.payload) for x in await _mensagens(db, c)]
    assert autores[-1] == ("mediador", {"para": ["SHOP"], "remetente": "carrefour"})
    assert c.aguardando_resposta is True  # a Mirakl diz que a vez é da loja

    r = await carrefour_atd.enviar_texto(
        db, c, integ, CarrefourClient(dict(CREDS)), "O rastreio já foi enviado ao cliente."
    )
    assert r.ok and r.payload["destino"] == "OPERATOR"
    corpo = _message_input(mirakl.feitas("POST", r"/api/inbox/threads/t-1/message")[-1])
    assert corpo["to"] == [{"type": "OPERATOR"}]


async def test_thread_so_com_o_carrefour_responde_ao_carrefour(
    db: AsyncSession, make_user, mirakl, relogio
):
    canal, integ = await _loja(db, make_user)
    mirakl.threads = [
        _thread(
            "t-op",
            [_msg("m1", "Precisamos da NF do pedido.", T0, de="OPERATOR_USER")],
            pedido=None,
            com_comprador=False,
        )
    ]
    await _rodar(db, canal, integ)
    c = await _conversa_db(db, "t-op")
    assert (c.pedido_marketplace, c.comprador_id) == (None, None)
    assert c.dados["entidades"] == [{"tipo": "SELLER_OPERATOR", "id": "2001", "rotulo": "Poofy"}]
    r = await carrefour_atd.enviar_texto(
        db, c, integ, CarrefourClient(dict(CREDS)), "Segue a nota em anexo pelo portal."
    )
    assert r.ok and r.payload["destino"] == "OPERATOR"


async def test_pergunta_da_oferta_liga_o_anuncio(db: AsyncSession, make_user, mirakl, relogio):
    canal, integ = await _loja(db, make_user)
    anexo = {"id": "a1", "name": "foto.JPG", "size": 1234}
    mirakl.threads = [
        _thread(
            "t-of",
            [_msg("m1", "", T0, anexos=[anexo])],
            pedido=None,
            oferta=("of-77", "Capinha Poofy Azul"),
        )
    ]
    await _rodar(db, canal, integ)
    c = await _conversa_db(db, "t-of")
    assert (c.anuncio_id, c.anuncio_titulo, c.pedido_marketplace) == (
        "of-77",
        "Capinha Poofy Azul",
        None,
    )
    (m,) = await _mensagens(db, c)
    assert (m.tipo, m.anexos) == (
        "imagem",
        [{"nome": "foto.JPG", "tipo": "image", "id": "a1", "tamanho": 1234}],
    )


# ─────────────── envio ───────────────


async def test_envio_vai_ao_comprador_e_a_nossa_volta_sem_duplicar(
    db: AsyncSession, make_user, mirakl, relogio
):
    canal, integ = await _loja(db, make_user)
    mirakl.threads = [_thread("t-1", [_msg("m1", "Tem garantia?", T0)])]
    await _rodar(db, canal, integ)
    c = await _conversa_db(db, "t-1")

    texto = "Tem sim: 90 dias pela loja."
    r = await carrefour_atd.enviar_texto(db, c, integ, CarrefourClient(dict(CREDS)), texto)
    assert (r.ok, r.externo_id) == (True, "msg-nova-1")
    assert r.payload == {"http": 201, "destino": "CUSTOMER", "message_id": "msg-nova-1"}
    (pedido,) = mirakl.feitas("POST", r"/api/inbox/threads/t-1/message")
    assert pedido.url.host == HOST
    assert pedido.headers["content-type"].startswith("multipart/form-data")
    assert _message_input(pedido) == {"body": texto, "to": [{"type": "CUSTOMER", "id": "cust-9"}]}
    nossa = await _nossa(db, c, texto, r, em=relogio["agora"])
    await db.refresh(c)
    assert c.aguardando_resposta is False

    # A leitura traz a nossa (o id do 201) e a vez zerada: a mesma linha, sem duplicar.
    relogio["agora"] += timedelta(minutes=2)
    mirakl.threads = [
        _thread(
            "t-1",
            [
                _msg("m1", "Tem garantia?", T0),
                _msg("msg-nova-1", texto, relogio["agora"], de="SHOP_USER", para=("CUSTOMER",)),
            ],
            atualizada=relogio["agora"],
            vez=None,
        )
    ]
    r2 = await _rodar(db, canal, integ)
    assert r2.mensagens_novas == 0
    loja = [x for x in await _mensagens(db, c) if x.autor == "loja"]
    assert [(x.id, x.origem, x.status) for x in loja] == [(nossa.id, "davinci_humano", "enviada")]


async def test_envio_ambiguo_e_reconhecido_pelo_texto_depois(
    db: AsyncSession, make_user, mirakl, relogio
):
    canal, integ = await _loja(db, make_user)
    mirakl.threads = [_thread("t-1", [_msg("m1", "Oi", T0)])]
    await _rodar(db, canal, integ)
    c = await _conversa_db(db, "t-1")
    mirakl.forcar.append(
        ("POST", r"/api/inbox/threads/t-1/message", lambda: httpx.Response(503, text="x"))
    )
    texto = "Olá! Já verificamos o seu pedido."
    r = await carrefour_atd.enviar_texto(db, c, integ, CarrefourClient(dict(CREDS)), texto)
    assert (r.ok, r.ambiguo, r.erro) == (False, True, "HTTP 503")
    # Mensagem para comprador não se repete: um POST só.
    assert len(mirakl.feitas("POST", r"/api/inbox/threads/t-1/message")) == 1
    nossa = await _nossa(db, c, texto, r, em=relogio["agora"])

    # Ela saiu, e volta 1 h depois (fora dos 15 min do `gravar`): é a nossa.
    relogio["agora"] += timedelta(hours=1)
    mirakl.threads = [
        _thread(
            "t-1",
            [_msg("m1", "Oi", T0), _msg("srv-9", texto, relogio["agora"], de="SHOP_USER")],
            atualizada=relogio["agora"],
            vez=None,
        )
    ]
    await _rodar(db, canal, integ)
    loja = [x for x in await _mensagens(db, c) if x.autor == "loja"]
    assert [(x.id, x.externo_id, x.status) for x in loja] == [(nossa.id, "srv-9", "enviada")]


@pytest.mark.parametrize(
    ("resposta", "ambiguo", "trecho"),
    [
        (lambda: httpx.Response(401, json={"message": "x", "status": 401}), False, "sem_escopo"),
        (lambda: httpx.Response(400, json={"message": "x", "status": 400}), False, "HTTP 400"),
        (lambda: httpx.Response(429, headers={"Retry-After": "30"}), False, "HTTP 429"),
    ],
)
async def test_envio_recusado_nao_saiu(
    db: AsyncSession, make_user, mirakl, relogio, resposta, ambiguo, trecho
):
    canal, integ = await _loja(db, make_user)
    mirakl.threads = [_thread("t-1", [_msg("m1", "Oi", T0)])]
    await _rodar(db, canal, integ)
    c = await _conversa_db(db, "t-1")
    mirakl.forcar.append(("POST", r"/api/inbox/threads/t-1/message", resposta))
    r = await carrefour_atd.enviar_texto(db, c, integ, CarrefourClient(dict(CREDS)), "Olá!!")
    assert (r.ok, r.ambiguo) == (False, ambiguo)
    assert trecho in r.erro
    assert len(mirakl.feitas("POST", r"/api/inbox/threads/t-1/message")) == 1


async def test_envio_sem_conexao_e_timeout(db: AsyncSession, make_user, mirakl, relogio):
    canal, integ = await _loja(db, make_user)
    mirakl.threads = [_thread("t-1", [_msg("m1", "Oi", T0)])]
    await _rodar(db, canal, integ)
    c = await _conversa_db(db, "t-1")

    def _recusa():
        raise httpx.ConnectError("recusada")

    def _demora():
        raise httpx.ReadTimeout("demorou")

    mirakl.forcar.append(("POST", r"/api/inbox/threads/t-1/message", _recusa))
    r = await carrefour_atd.enviar_texto(db, c, integ, CarrefourClient(dict(CREDS)), "Olá!!")
    assert (r.ok, r.ambiguo, r.erro) == (False, False, "sem_conexao:ConnectError")
    mirakl.forcar[-1] = ("POST", r"/api/inbox/threads/t-1/message", _demora)
    r = await carrefour_atd.enviar_texto(db, c, integ, CarrefourClient(dict(CREDS)), "Olá!!")
    assert (r.ok, r.ambiguo, r.erro) == (False, True, "sem_resposta:ReadTimeout")


async def test_curto_e_longo_demais_nao_saem(db: AsyncSession, make_user, mirakl, relogio):
    canal, integ = await _loja(db, make_user)
    mirakl.threads = [_thread("t-1", [_msg("m1", "Oi", T0)])]
    await _rodar(db, canal, integ)
    c = await _conversa_db(db, "t-1")
    cliente = CarrefourClient(dict(CREDS))
    r = await carrefour_atd.enviar_texto(db, c, integ, cliente, "ok")
    assert r.ok is False and "abaixo_do_minimo_3" in r.erro
    r = await carrefour_atd.enviar_texto(db, c, integ, cliente, "a" * 2001)
    assert r.ok is False and "acima_do_limite_2000" in r.erro
    assert not mirakl.feitas("POST", r"/api/inbox/threads/.+/message")


# ─────────────── erros da leitura ───────────────


async def test_chave_recusada_vira_sem_escopo_pelo_cron(
    db: AsyncSession, make_user, mirakl, relogio
):
    """O caso do Carrefour VR em 30/10: a chave dá 401 em tudo (trocada no portal)."""
    canal, _integ = await _loja(db, make_user, "Carrefour VR")
    mirakl.forcar.append(
        ("GET", r"/api/inbox/threads", lambda: httpx.Response(401, json={"status": 401}))
    )
    r = await sync.sincronizar_canal(canal.id)
    assert r.status == "sem_escopo"
    await db.refresh(canal)
    assert canal.status == "sem_escopo"
    assert "gere a chave de API no portal do Carrefour" in canal.ultimo_erro
    assert "HTTP 401" in canal.ultimo_erro
    assert await _total_mensagens(db) == 0


async def test_429_segura_o_canal_ate_o_retry_after(db: AsyncSession, make_user, mirakl, relogio):
    canal, integ = await _loja(db, make_user)
    mirakl.forcar.append(
        (
            "GET",
            r"/api/inbox/threads",
            lambda: httpx.Response(429, headers={"Retry-After": "120"}),
        )
    )
    r = await _rodar(db, canal, integ)
    assert r.status == "erro" and "HTTP 429" in r.erro
    assert canal.cursor["espera_ate"] == (relogio["agora"] + timedelta(seconds=120)).isoformat(
        timespec="seconds"
    )
    chamadas = len(mirakl.chamadas)
    r = await _rodar(db, canal, integ)
    assert r.status == "erro" and len(mirakl.chamadas) == chamadas  # nem tentou

    mirakl.forcar.clear()
    relogio["agora"] += timedelta(minutes=3)
    r = await _rodar(db, canal, integ)
    assert r.status == "ok" and "espera_ate" not in canal.cursor


# ─────────────── pela rota: responder e as lojas por pessoa ───────────────


@pytest.fixture
def envio_ligado(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "atendimento_usuarios", OUTRO)
    monkeypatch.setattr(s, "atendimento_envio_ativo", True)
    monkeypatch.setattr(s, "atendimento_simulador", False)
    return s


async def _ids(client) -> set[str]:
    r = await client.get(f"{URL}/conversas", params={"filtro": "todas"})
    assert r.status_code == 200, r.text
    return {i["id"] for i in r.json()["itens"]}


@pytest.mark.lojas_por_pessoa
async def test_cada_vendedor_ve_e_responde_so_o_carrefour_dele(
    db: AsyncSession, make_user, mirakl, relogio, client, auth_as, envio_ligado
):
    canal_vr, vr = await _loja(db, make_user, "Carrefour VR")
    canal_poofy, poofy = await _loja(db, make_user, "Carrefour Poofy")
    mirakl.threads = [_thread("t-vr", [_msg("m1", "Quando chega?", T0)])]
    await _rodar(db, canal_vr, vr)
    mirakl.threads = [_thread("t-poofy", [_msg("n1", "Tem azul?", T0)], pedido=None)]
    await _rodar(db, canal_poofy, poofy)
    c_vr, c_poofy = await _conversa_db(db, "t-vr"), await _conversa_db(db, "t-poofy")

    israel = await make_user(role=UserRole.USER)
    marrocos = await make_user(role=UserRole.USER)
    await dar_lojas(db, israel, escopo.chave(escopo.INTEGRACAO, vr.id))
    await dar_lojas(db, marrocos, escopo.chave(escopo.INTEGRACAO, poofy.id))

    auth_as(israel)
    assert await _ids(client) == {str(c_vr.id)}
    r = (await client.get(f"{URL}/resumo")).json()
    assert {lj["integration_id"] for lj in r["lojas"]} == {str(vr.id)}
    carrefour = next(p for p in r["plataformas"] if p["plataforma"] == "carrefour")
    assert carrefour["aguardando"] == 1
    assert (await client.get(f"{URL}/conversas/{c_poofy.id}")).status_code == 404
    r = await client.post(f"{URL}/conversas/{c_poofy.id}/responder", json={"texto": "Olá!"})
    assert r.status_code == 404 and r.json()["detail"]["code"] == "conversa_nao_encontrada"
    assert not mirakl.feitas("POST", r"/api/inbox/threads/.+/message")

    # A dele sai pela Mirakl (envio ligado, loja em `humano`).
    texto = "Olá, Ana! O seu pedido já foi despachado e está a caminho."
    r = await client.post(f"{URL}/conversas/{c_vr.id}/responder", json={"texto": texto})
    assert r.status_code == 200, r.text
    (pedido,) = mirakl.feitas("POST", r"/api/inbox/threads/t-vr/message")
    assert _message_input(pedido)["body"] == texto
    linha = (
        await db.execute(
            select(AtendimentoMensagem).where(
                AtendimentoMensagem.conversa_id == c_vr.id,
                AtendimentoMensagem.autor == "loja",
            )
        )
    ).scalar_one()
    assert (linha.status, linha.externo_id, linha.origem) == (
        "enviada",
        "msg-nova-1",
        "davinci_humano",
    )

    auth_as(marrocos)
    assert await _ids(client) == {str(c_poofy.id)}
    assert (await client.get(f"{URL}/conversas/{c_vr.id}")).status_code == 404


@pytest.mark.lojas_por_pessoa
async def test_loja_em_observar_nao_envia(
    db: AsyncSession, make_user, mirakl, relogio, client, auth_as, envio_ligado
):
    canal, integ = await _loja(db, make_user, "Carrefour Poofy", modo="observar")
    mirakl.threads = [_thread("t-1", [_msg("m1", "Oi", T0)])]
    await _rodar(db, canal, integ)
    c = await _conversa_db(db, "t-1")
    marrocos = await make_user(role=UserRole.USER)
    await dar_lojas(db, marrocos, integ)
    auth_as(marrocos)
    r = await client.post(f"{URL}/conversas/{c.id}/responder", json={"texto": "Olá, tudo bem?"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "canal_em_observacao"
    assert not mirakl.feitas("POST", r"/api/inbox/threads/.+/message")
