# ruff: noqa: S105  (tokens de teste de um cliente falso, nada real)
"""O cartão "Cliente" (P5): quem é a pessoa que escreve, sem inventar nada.

Sem rede: o ML é um cliente FALSO no formato medido em produção em 28/09/2026
(`scratchpad/formato_cliente.txt`, `formatos_pedido.txt`), e o Redis é um
dicionário. O que se mede aqui é o que faria o cartão mentir ou vazar:

- Shopee pelo índice: compras sem as canceladas, gasto, última compra,
  avaliação ruim (pelo pedido e pelo apelido na loja), devolução;
- "primeira compra" SÓ com o histórico completo (a marca da importação);
- ML ao vivo: compra = carrinho (pack), avaliação do COMPRADOR (não a da
  loja), cache de 2 h, falha em cache e tempo estourado sem segurar a tela;
- nada de nome/apelido do comprador do ML sai no cartão;
- perguntas pré-venda e reclamação aberta das outras conversas;
- TikTok/Amazon só com o que existe; sem dado nenhum, `{}`; nunca levanta.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import (
    AtendimentoCanal,
    AtendimentoConversa,
    AtendimentoMensagem,
    Devolution,
    Integration,
    IntegrationPlatform,
    User,
)
from app.security.cipher import encrypt_json
from app.services.atendimento import cliente, clientes, indice

AGORA = datetime.now(UTC).replace(microsecond=0)
SELLER = "999000111"
COMPRADOR_ML = "123456789"


class RedisFalso:
    def __init__(self) -> None:
        self.dados: dict[str, Any] = {}
        self.validade: dict[str, int | None] = {}

    async def get(self, chave):
        return self.dados.get(chave)

    async def set(self, chave, valor, *, nx=False, ex=None):
        if nx and chave in self.dados:
            return None
        self.dados[chave] = valor
        self.validade[chave] = ex
        return True

    async def delete(self, *chaves):
        for c in chaves:
            self.dados.pop(c, None)
        return 1

    async def incr(self, chave):
        self.dados[chave] = int(self.dados.get(chave) or 0) + 1
        return self.dados[chave]

    async def expire(self, chave, segundos):
        self.validade[chave] = segundos
        return 1

    async def ttl(self, chave):
        return self.validade.get(chave) or -1


@pytest.fixture
def redis_falso(monkeypatch) -> RedisFalso:
    r = RedisFalso()
    monkeypatch.setattr(cliente, "redis", r)
    monkeypatch.setattr(indice, "redis", r)
    return r


# ─────────────── cenário ───────────────


async def _integracao(db: AsyncSession, user: User, plataforma: IntegrationPlatform) -> Integration:
    integ = Integration(
        user_id=user.id,
        platform=plataforma,
        name=f"Loja {plataforma.value}",
        credentials=encrypt_json({"access_token": "t", "refresh_token": "r", "user_id": SELLER}),
    )
    db.add(integ)
    await db.commit()
    await db.refresh(integ)
    return integ


async def _conversa(
    db: AsyncSession,
    integ: Integration,
    *,
    canal: str,
    externo_id: str,
    comprador_id: str | None,
    comprador_nome: str | None = None,
    quando: datetime | None = None,
    pedido: str | None = None,
    anuncio_titulo: str | None = None,
    dados: dict | None = None,
    mensagem_em: datetime | None = None,
) -> AtendimentoConversa:
    conversa = AtendimentoConversa(
        integration_id=integ.id,
        plataforma=integ.platform.value,
        canal=canal,
        conta=integ.name,
        externo_id=externo_id,
        comprador_id=comprador_id,
        comprador_nome=comprador_nome,
        pedido_marketplace=pedido,
        anuncio_titulo=anuncio_titulo,
        ultima_mensagem_em=quando or AGORA,
        dados=dados or {},
    )
    db.add(conversa)
    await db.flush()
    if mensagem_em is not None:
        db.add(
            AtendimentoMensagem(
                conversa_id=conversa.id,
                externo_id=f"m-{externo_id}",
                autor="cliente",
                origem="cliente",
                texto="oi",
                enviada_em=mensagem_em,
            )
        )
    await db.commit()
    await db.refresh(conversa)
    return conversa


async def _pedido(db, integ, comprador, pedido, dias_atras, total, status, itens="Mala de bordo"):
    await indice.registrar_pedido(
        db,
        integration_id=integ.id,
        plataforma="shopee",
        comprador_id=comprador,
        pedido=pedido,
        criado_em=AGORA - timedelta(days=dias_atras),
        total=total,
        status=status,
        itens_resumo=itens,
    )


async def _avaliacao(db, integ, comentario, *, pedido, nome, estrelas, dias_atras, resposta=None):
    await indice.registrar_avaliacao(
        db,
        integration_id=integ.id,
        plataforma="shopee",
        comentario_id=comentario,
        pedido=pedido,
        comprador_nome_loja=nome,
        item_id="777",
        estrelas=estrelas,
        texto="chegou quebrado" if estrelas <= 2 else "gostei",
        resposta_loja=resposta,
        criado_em=AGORA - timedelta(days=dias_atras),
    )


def _iso(quando: datetime) -> str:
    return quando.astimezone(UTC).isoformat(timespec="seconds")


# ─────────────── Shopee (índice) ───────────────


async def test_shopee_recorrente_com_cancelada_e_avaliacao_ruim(
    db: AsyncSession, make_user, redis_falso
):
    user = await make_user()
    integ = await _integracao(db, user, IntegrationPlatform.SHOPEE)
    conversa = await _conversa(
        db, integ, canal="chat", externo_id="c1", comprador_id="555", comprador_nome="cliente_x"
    )
    await _pedido(db, integ, "555", "A1", 60, 100.0, "COMPLETED")
    await _pedido(db, integ, "555", "A2", 10, 50.5, "SHIPPED", itens="2× Cadeado")
    await _pedido(db, integ, "555", "A3", 5, 30.0, "CANCELLED")
    await _pedido(db, integ, "999", "B1", 3, 900.0, "COMPLETED")  # outro comprador
    await _avaliacao(db, integ, "k1", pedido="A1", nome="cliente_x", estrelas=1, dias_atras=50)
    await _avaliacao(db, integ, "k9", pedido="B1", nome="outro", estrelas=5, dias_atras=2)
    await db.commit()

    cartao = await cliente.cartao_cliente(db, conversa)

    assert cartao["compras"] == 2
    assert cartao["cancelamentos"] == 1
    assert cartao["total_gasto"] == 150.5
    assert cartao["ultima_compra"] == _iso(AGORA - timedelta(days=10))
    assert cartao["desde"] == _iso(AGORA - timedelta(days=60))
    assert cartao["devolucoes"] == 0
    assert cartao["sinais"] == ["recorrente", "avaliou_mal"]
    assert cartao["avaliacoes"] == [
        {
            "estrelas": 1,
            "texto": "chegou quebrado",
            "pedido": "A1",
            "criado_em": _iso(AGORA - timedelta(days=50)),
            "respondida": False,
        }
    ]
    # Sem a marca da importação, o índice não se diz completo.
    assert cartao["historico_completo"] is False
    tempo = cartao["linha_do_tempo"]
    assert [e["tipo"] for e in tempo] == ["compra", "avaliacao", "compra", "compra"]
    assert [e["ref"] for e in tempo] == ["A1", "A1", "A2", "A3"]
    assert tempo[1]["texto"].startswith("★☆☆☆☆")
    assert tempo[2]["texto"] == "R$ 50,50 · 2× Cadeado"
    assert tempo[3]["texto"].startswith("Cancelada")
    assert [e["em"] for e in tempo] == sorted(e["em"] for e in tempo)
    assert "B1" not in json.dumps(cartao)


async def test_shopee_primeira_compra_so_com_historico_completo(
    db: AsyncSession, make_user, redis_falso
):
    user = await make_user()
    integ = await _integracao(db, user, IntegrationPlatform.SHOPEE)
    conversa = await _conversa(db, integ, canal="chat", externo_id="c1", comprador_id="555")
    await _pedido(db, integ, "555", "A1", 3, 80.0, "READY_TO_SHIP")
    await db.commit()

    sem_marca = await cliente.cartao_cliente(db, conversa)
    assert sem_marca["compras"] == 1
    assert "primeira_compra" not in sem_marca["sinais"]

    await indice.marcar_cobertura(integ.id, AGORA - timedelta(days=90))
    # Uma importação mais curta depois não encolhe a cobertura.
    await indice.marcar_cobertura(integ.id, AGORA - timedelta(days=30))
    com_marca = await cliente.cartao_cliente(db, conversa)
    assert com_marca["sinais"] == ["primeira_compra"]
    assert com_marca["historico_completo"] is True
    assert com_marca["historico_desde"] == _iso(AGORA - timedelta(days=90))


async def test_shopee_avaliacao_pelo_apelido_e_devolucao(db: AsyncSession, make_user, redis_falso):
    user = await make_user()
    integ = await _integracao(db, user, IntegrationPlatform.SHOPEE)
    conversa = await _conversa(
        db, integ, canal="chat", externo_id="c1", comprador_id="555", comprador_nome="cliente_x"
    )
    await _pedido(db, integ, "555", "A1", 20, 100.0, "COMPLETED")
    await _pedido(db, integ, "555", "A2", 15, 40.0, "TO_RETURN")
    # Avaliação de um pedido que o índice não tem — casa pelo apelido na loja.
    await _avaliacao(
        db,
        integ,
        "k1",
        pedido="ZZ",
        nome="cliente_x",
        estrelas=4,
        dias_atras=100,
        resposta="Obrigado!",
    )
    await _avaliacao(db, integ, "k2", pedido="YY", nome="outra_pessoa", estrelas=1, dias_atras=1)
    db.add(Devolution(pedido_marketplace="A1", conta="kfa"))
    await db.commit()

    cartao = await cliente.cartao_cliente(db, conversa)

    assert cartao["devolucoes"] == 2  # A1 registrada no DaVinci, A2 em devolução na Shopee
    assert "ja_pediu_devolucao" in cartao["sinais"]
    assert "avaliou_mal" not in cartao["sinais"]
    assert [(a["pedido"], a["estrelas"], a["respondida"]) for a in cartao["avaliacoes"]] == [
        ("ZZ", 4, True)
    ]


# ─────────────── Mercado Livre (ao vivo) ───────────────


def _pedido_ml(oid: int, pack: int | None, dias: int, total: float, status: str = "paid") -> dict:
    """Pedido no formato do `/orders/search` (com o comprador que NÃO pode vazar)."""
    return {
        "id": oid,
        "date_created": (AGORA - timedelta(days=dias))
        .astimezone(UTC)
        .strftime("%Y-%m-%dT%H:%M:%S.000-00:00"),
        "pack_id": pack,
        "status": status,
        "total_amount": total,
        "paid_amount": total,
        "mediations": [],
        "order_items": [{"item": {"id": "MLB1", "title": "Mala Grande"}, "quantity": 1}],
        "buyer": {
            "id": int(COMPRADOR_ML),
            "nickname": "APELIDO_SECRETO",
            "first_name": "Fulana",
            "last_name": "De Tal",
        },
        "seller": {"id": int(SELLER)},
    }


class MLFalso:
    def __init__(self) -> None:
        self.creds = {"user_id": SELLER}
        self.pedidos: list[dict] = []
        self.feedbacks: dict[str, dict] = {}
        self.chamadas: list[tuple[str, Any]] = []
        self.erro: Exception | None = None
        self.demora = 0.0

    async def pedidos_do_comprador(self, buyer_id, *, limit=50, offset=0):
        self.chamadas.append(("pedidos_do_comprador", str(buyer_id)))
        if self.demora:
            await asyncio.sleep(self.demora)
        if self.erro is not None:
            raise self.erro
        return {"results": self.pedidos, "paging": {"total": len(self.pedidos), "limit": limit}}

    async def feedback_do_pedido(self, order_id):
        # Morto desde 02/10/2026 (0 de 60 com avaliação): o cartão não chama mais.
        self.chamadas.append(("feedback_do_pedido", str(order_id)))
        return self.feedbacks.get(str(order_id))  # None = 404 (sem avaliação)


@pytest.fixture
def ml_falso(monkeypatch) -> MLFalso:
    falso = MLFalso()

    async def _fabrica(integration):
        return falso

    monkeypatch.setattr(clientes, "cliente_da_integracao", _fabrica)
    # O ML ao vivo só com a leitura ligada (ver o teste da leitura desligada).
    monkeypatch.setattr(get_settings(), "atendimento_leitura_ativa", True)
    return falso


async def test_ml_com_a_leitura_desligada_nao_fala_com_a_loja(
    db: AsyncSession, make_user, redis_falso, ml_falso, monkeypatch
):
    """Leitura desligada = o DaVinci não fala com loja nenhuma, nem para o cartão."""
    monkeypatch.setattr(get_settings(), "atendimento_leitura_ativa", False)
    user = await make_user()
    integ = await _integracao(db, user, IntegrationPlatform.ML)
    await _conversa(db, integ, canal="pergunta", externo_id="q:1", comprador_id=COMPRADOR_ML)
    conversa = await _conversa(
        db, integ, canal="pos_venda", externo_id="3000", comprador_id=COMPRADOR_ML
    )
    ml_falso.pedidos = [_pedido_ml(21, None, 3, 199.9)]

    cartao = await cliente.cartao_cliente(db, conversa)

    assert ml_falso.chamadas == []
    assert cartao["perguntas_pre_venda"] == 1  # o que o banco sabe continua
    assert cartao["historico_completo"] is False
    assert "primeira_compra" not in cartao["sinais"]


async def test_ml_ao_vivo_compra_e_carrinho_avaliacao_do_comprador_e_cache(
    db: AsyncSession, make_user, redis_falso, ml_falso
):
    user = await make_user()
    integ = await _integracao(db, user, IntegrationPlatform.ML)
    conversa = await _conversa(
        db,
        integ,
        canal="pos_venda",
        externo_id="2000001",
        comprador_id=COMPRADOR_ML,
        dados={"claim_ids": ["55"]},
    )
    # Um carrinho com dois pedidos (uma compra) + um pedido solto + um cancelado.
    ml_falso.pedidos = [
        _pedido_ml(11, 2000001, 2, 100.0),
        _pedido_ml(12, 2000001, 2, 50.0),
        _pedido_ml(13, None, 40, 80.0),
        _pedido_ml(14, None, 60, 70.0, status="cancelled"),
    ]
    # As avaliações do ML vêm do ÍNDICE (a opinião do produto, que o cron das
    # avaliações lê — 02/10/2026): pelo pedido (11) e pelo comprador (13). A
    # de outro comprador, não. A avaliação da venda (`/feedback`) está morta.
    for comentario_id, pedido, comprador, estrelas in (
        ("r11", "11", None, 1),
        ("r13", "13", COMPRADOR_ML, 5),
        ("r99", "99", "outro", 2),
    ):
        await indice.registrar_avaliacao(
            db,
            integration_id=integ.id,
            plataforma="ml",
            comentario_id=comentario_id,
            pedido=pedido,
            comprador_nome_loja=None,
            comprador_id=comprador,
            item_id="MLB1",
            estrelas=estrelas,
            texto="demorou" if estrelas <= 2 else "top",
            resposta_loja=None,
            criado_em=AGORA - timedelta(days=1),
        )
    await db.commit()

    cartao = await cliente.cartao_cliente(db, conversa)

    assert cartao["compras"] == 2  # o carrinho conta uma vez
    assert cartao["cancelamentos"] == 1
    assert cartao["total_gasto"] == 230.0
    assert cartao["historico_completo"] is True
    assert set(cartao["sinais"]) == {"recorrente", "avaliou_mal", "reclamacao_aberta"}
    estrelas = sorted((a["pedido"], a["estrelas"], a["respondida"]) for a in cartao["avaliacoes"])
    assert estrelas == [("11", 1, False), ("13", 5, False)]
    # Nada de nome/apelido do comprador — nem no cartão, nem no cache.
    for proibido in ("APELIDO_SECRETO", "Fulana", "De Tal"):
        assert proibido not in json.dumps(cartao, ensure_ascii=False)
        assert all(proibido not in str(v) for v in redis_falso.dados.values())
    chave = f"atendimento:cliente:ml:{integ.id}:{COMPRADOR_ML}"
    assert redis_falso.validade[chave] == cliente.CACHE_ML_S
    compras_tempo = [e for e in cartao["linha_do_tempo"] if e["tipo"] == "compra"]
    assert len(compras_tempo) == 3  # uma bolinha por compra (o carrinho é uma)
    assert "reclamacao" in [e["tipo"] for e in cartao["linha_do_tempo"]]

    # A tela relê a conversa a cada 15 s: a segunda vez vem do cache.
    feitas = len(ml_falso.chamadas)
    de_novo = await cliente.cartao_cliente(db, conversa)
    assert len(ml_falso.chamadas) == feitas
    assert de_novo["compras"] == 2
    # A avaliação da venda (`/orders/{id}/feedback`) não é mais chamada.
    assert sum(1 for n, _ in ml_falso.chamadas if n == "feedback_do_pedido") == 0


async def test_ml_perguntas_pre_venda_das_outras_conversas(
    db: AsyncSession, make_user, redis_falso, ml_falso
):
    user = await make_user()
    integ = await _integracao(db, user, IntegrationPlatform.ML)
    await _conversa(
        db,
        integ,
        canal="pergunta",
        externo_id="q:1",
        comprador_id=COMPRADOR_ML,
        anuncio_titulo="Mala de Bordo ABS Rígida",
        quando=AGORA - timedelta(days=4),
        mensagem_em=AGORA - timedelta(days=4),
    )
    await _conversa(db, integ, canal="pergunta", externo_id="q:2", comprador_id="outra_pessoa")
    conversa = await _conversa(
        db, integ, canal="pos_venda", externo_id="3000", comprador_id=COMPRADOR_ML
    )
    ml_falso.pedidos = [_pedido_ml(21, None, 3, 199.9)]

    cartao = await cliente.cartao_cliente(db, conversa)

    assert cartao["perguntas_pre_venda"] == 1
    assert cartao["sinais"] == ["primeira_compra"]  # o ML devolve TODOS os pedidos dele
    perguntas = [e for e in cartao["linha_do_tempo"] if e["tipo"] == "pergunta"]
    assert perguntas[0]["texto"] == "Mala de Bordo ABS Rígida"
    assert cartao["desde"] == _iso(AGORA - timedelta(days=4))


async def test_ml_falha_nao_derruba_e_fica_em_cache(
    db: AsyncSession, make_user, redis_falso, ml_falso
):
    user = await make_user()
    integ = await _integracao(db, user, IntegrationPlatform.ML)
    await _conversa(db, integ, canal="pergunta", externo_id="q:1", comprador_id=COMPRADOR_ML)
    conversa = await _conversa(
        db, integ, canal="pos_venda", externo_id="3000", comprador_id=COMPRADOR_ML
    )
    ml_falso.erro = RuntimeError("HTTP 500")

    cartao = await cliente.cartao_cliente(db, conversa)
    assert cartao["perguntas_pre_venda"] == 1
    assert cartao["historico_completo"] is False
    chamadas = len(ml_falso.chamadas)

    await cliente.cartao_cliente(db, conversa)
    assert len(ml_falso.chamadas) == chamadas  # a falha ficou 10 min em cache
    chave = f"atendimento:cliente:ml:{integ.id}:{COMPRADOR_ML}"
    assert redis_falso.validade[chave] == cliente.CACHE_FALHA_S


async def test_ml_demorado_nao_segura_a_tela_e_termina_em_segundo_plano(
    db: AsyncSession, make_user, redis_falso, ml_falso, monkeypatch
):
    monkeypatch.setattr(cliente, "TEMPO_MAX_API_S", 0.05)
    user = await make_user()
    integ = await _integracao(db, user, IntegrationPlatform.ML)
    await _conversa(db, integ, canal="pergunta", externo_id="q:1", comprador_id=COMPRADOR_ML)
    conversa = await _conversa(
        db, integ, canal="pos_venda", externo_id="3000", comprador_id=COMPRADOR_ML
    )
    ml_falso.pedidos = [_pedido_ml(21, None, 3, 10.0)]
    ml_falso.demora = 0.3

    cartao = await cliente.cartao_cliente(db, conversa)
    assert cartao["compras"] == 0  # a tela não esperou

    await asyncio.gather(*list(cliente._EM_SEGUNDO_PLANO))
    depois = await cliente.cartao_cliente(db, conversa)
    assert depois["compras"] == 1  # a busca terminou e deixou o cache pronto


# ─────────────── TikTok/Amazon, vazio, nunca levanta ───────────────


async def test_tiktok_usa_so_o_que_existe(db: AsyncSession, make_user, redis_falso):
    user = await make_user()
    integ = await _integracao(db, user, IntegrationPlatform.TIKTOK)
    await _conversa(
        db,
        integ,
        canal="chat",
        externo_id="t1",
        comprador_id="tt-1",
        pedido="PED-1",
        dados={
            "pedido_mkt": {
                "pedido": "PED-1",
                "criado_em": (AGORA - timedelta(days=9)).isoformat(),
                "total": 59.9,
                "status": "COMPLETED",
                "enviado_em": (AGORA - timedelta(days=8)).isoformat(),
                "itens": [{"titulo": "Mochila", "quantidade": 1}],
            }
        },
        quando=AGORA - timedelta(days=8),
    )
    conversa = await _conversa(db, integ, canal="chat", externo_id="t2", comprador_id="tt-1")

    cartao = await cliente.cartao_cliente(db, conversa)

    assert cartao["compras"] == 1
    assert cartao["sinais"] == []  # sem histórico completo, "primeira compra" não se afirma
    tipos = [e["tipo"] for e in cartao["linha_do_tempo"]]
    assert tipos == ["compra", "envio", "mensagem"]


async def test_sem_dado_devolve_vazio(db: AsyncSession, make_user, redis_falso):
    user = await make_user()
    integ = await _integracao(db, user, IntegrationPlatform.SHOPEE)
    sem_comprador = await _conversa(db, integ, canal="chat", externo_id="c0", comprador_id=None)
    so_ela = await _conversa(
        db, integ, canal="chat", externo_id="c1", comprador_id="555", mensagem_em=AGORA
    )
    assert await cliente.cartao_cliente(db, sem_comprador) == {}
    assert await cliente.cartao_cliente(db, so_ela) == {}


async def test_nunca_levanta_e_a_sessao_continua_usavel(
    db: AsyncSession, make_user, redis_falso, monkeypatch
):
    user = await make_user()
    integ = await _integracao(db, user, IntegrationPlatform.SHOPEE)
    conversa = await _conversa(db, integ, canal="chat", externo_id="c1", comprador_id="555")

    async def _explode(*a, **k):
        from sqlalchemy import text

        await db.execute(text("SELECT * FROM tabela_que_nao_existe"))

    monkeypatch.setattr(cliente, "_compras_do_indice", _explode)
    assert await cliente.cartao_cliente(db, conversa) == {}
    # A consulta que falhou morreu no SAVEPOINT: a sessão de quem chamou segue.
    total = await db.scalar(select(func.count()).select_from(Integration))
    assert total == 1


def test_sinais_de_cuidado_para_a_ia():
    assert cliente.sinais_de_cuidado(
        {"sinais": ["recorrente", "avaliou_mal", "reclamacao_aberta"]}
    ) == ["avaliou_mal", "reclamacao_aberta"]
    assert cliente.sinais_de_cuidado({}) == []


# ─────────────── revisão de 28/09 ───────────────


async def test_ml_ao_vivo_respeita_o_canal_desligado(
    db: AsyncSession, make_user, redis_falso, ml_falso
):
    """SEG-05: `desligado` é o interruptor do 429 — o cartão não vai à loja."""
    user = await make_user()
    integ = await _integracao(db, user, IntegrationPlatform.ML)
    canal = AtendimentoCanal(
        integration_id=integ.id, plataforma="ml", canal="pos_venda", status="desligado"
    )
    db.add(canal)
    await db.commit()
    await _conversa(db, integ, canal="pergunta", externo_id="q:1", comprador_id=COMPRADOR_ML)
    conversa = await _conversa(
        db, integ, canal="pos_venda", externo_id="3000", comprador_id=COMPRADOR_ML
    )
    conversa.canal_id = canal.id
    await db.commit()
    ml_falso.pedidos = [_pedido_ml(21, None, 3, 199.9)]

    cartao = await cliente.cartao_cliente(db, conversa)

    assert ml_falso.chamadas == []
    assert cartao["compras"] == 0 and cartao["perguntas_pre_venda"] == 1


async def test_ml_ao_vivo_uma_busca_por_comprador_de_cada_vez(
    db: AsyncSession, make_user, redis_falso, ml_falso, monkeypatch
):
    """SEG-05: enquanto o cache não é gravado, cada leitura da tela (e da IA)
    abria uma busca NOVA — no 429, a rajada piorava o 429."""
    monkeypatch.setattr(cliente, "TEMPO_MAX_API_S", 0.05)
    user = await make_user()
    integ = await _integracao(db, user, IntegrationPlatform.ML)
    await _conversa(db, integ, canal="pergunta", externo_id="q:1", comprador_id=COMPRADOR_ML)
    conversa = await _conversa(
        db, integ, canal="pos_venda", externo_id="3000", comprador_id=COMPRADOR_ML
    )
    ml_falso.pedidos = [_pedido_ml(21, None, 3, 10.0)]
    ml_falso.demora = 0.3

    for _ in range(4):  # a tela relendo enquanto o ML demora
        await cliente.cartao_cliente(db, conversa)

    buscas = [n for n, _ in ml_falso.chamadas if n == "pedidos_do_comprador"]
    assert buscas == ["pedidos_do_comprador"]
    await asyncio.gather(*list(cliente._EM_SEGUNDO_PLANO))
    andamento = cliente._CHAVE_ML_EM_ANDAMENTO.format(integ.id, COMPRADOR_ML)
    assert andamento not in redis_falso.dados  # terminou: solta a vez
    assert (await cliente.cartao_cliente(db, conversa))["compras"] == 1


async def test_ml_ao_vivo_tem_teto_por_loja_por_minuto(
    db: AsyncSession, make_user, redis_falso, ml_falso
):
    user = await make_user()
    integ = await _integracao(db, user, IntegrationPlatform.ML)
    ml_falso.pedidos = [_pedido_ml(21, None, 3, 10.0)]
    for i in range(cliente.LIMITE_ML_POR_LOJA + 2):
        comprador = f"77{i}"
        await _conversa(db, integ, canal="pergunta", externo_id=f"q:{i}", comprador_id=comprador)
        c = await _conversa(
            db, integ, canal="pos_venda", externo_id=f"p{i}", comprador_id=comprador
        )
        await cliente.cartao_cliente(db, c)

    buscas = [n for n, _ in ml_falso.chamadas if n == "pedidos_do_comprador"]
    assert len(buscas) == cliente.LIMITE_ML_POR_LOJA


async def test_pergunta_aberta_nao_conta_como_pre_venda(
    db: AsyncSession, make_user, redis_falso, ml_falso
):
    """LOGICA-05: quem comprou 3 vezes e pergunta HOJE não "perguntou antes de
    comprar" — a pergunta da tela não conta, nem a feita depois das compras."""
    user = await make_user()
    integ = await _integracao(db, user, IntegrationPlatform.ML)
    await _conversa(
        db, integ, canal="pergunta", externo_id="q:depois", comprador_id=COMPRADOR_ML,
        quando=AGORA - timedelta(days=1),
    )
    aberta = await _conversa(
        db, integ, canal="pergunta", externo_id="q:hoje", comprador_id=COMPRADOR_ML
    )
    ml_falso.pedidos = [
        _pedido_ml(31, None, 10, 50.0),
        _pedido_ml(32, None, 20, 50.0),
        _pedido_ml(33, None, 30, 50.0),
    ]

    cartao = await cliente.cartao_cliente(db, aberta)

    assert cartao["compras"] == 3
    assert cartao["perguntas_pre_venda"] == 0

    # Uma pergunta ANTES de uma das compras: essa é pré-venda.
    await _conversa(
        db, integ, canal="pergunta", externo_id="q:antes", comprador_id=COMPRADOR_ML,
        quando=AGORA - timedelta(days=15),
    )
    assert (await cliente.cartao_cliente(db, aberta))["perguntas_pre_venda"] == 1


async def test_pergunta_na_linha_do_tempo_fica_na_hora_do_cliente(
    db: AsyncSession, make_user, redis_falso, ml_falso
):
    """Perguntou antes da compra, a loja respondeu depois: a bolinha da pergunta
    fica na hora do CLIENTE — a mesma data que `perguntas_pre_venda` usa (a
    tela decide "perguntou antes de comprar" pela linha do tempo)."""
    user = await make_user()
    integ = await _integracao(db, user, IntegrationPlatform.ML)
    pergunta = await _conversa(
        db, integ, canal="pergunta", externo_id="q:antes", comprador_id=COMPRADOR_ML,
        quando=AGORA - timedelta(days=5),  # a resposta da loja, depois da compra
    )
    pergunta.ultima_do_cliente_em = AGORA - timedelta(days=12)
    await db.commit()
    aberta = await _conversa(
        db, integ, canal="pos_venda", externo_id="3001", comprador_id=COMPRADOR_ML
    )
    ml_falso.pedidos = [_pedido_ml(41, None, 10, 80.0)]

    cartao = await cliente.cartao_cliente(db, aberta)

    assert cartao["perguntas_pre_venda"] == 1
    eventos = {e["tipo"]: e for e in cartao["linha_do_tempo"]}
    assert eventos["pergunta"]["em"] == _iso(AGORA - timedelta(days=12))
    assert eventos["pergunta"]["em"] < eventos["compra"]["em"]


async def test_pedido_nao_pago_nao_e_compra(db: AsyncSession, make_user, redis_falso):
    """LOGICA-06: o Pix que o comprador não pagou (UNPAID) não é compra nem soma
    no gasto — nem faz o cliente "recorrente"."""
    user = await make_user()
    integ = await _integracao(db, user, IntegrationPlatform.SHOPEE)
    conversa = await _conversa(db, integ, canal="chat", externo_id="c1", comprador_id="555")
    await _pedido(db, integ, "555", "U1", 2, 50.0, "UNPAID")
    await _pedido(db, integ, "555", "P1", 1, 50.0, "READY_TO_SHIP")
    await db.commit()

    cartao = await cliente.cartao_cliente(db, conversa)

    assert cartao["compras"] == 1
    assert cartao["total_gasto"] == 50.0
    assert cartao["cancelamentos"] == 0
    assert "recorrente" not in cartao["sinais"]
    assert cartao["ultima_compra"] == _iso(AGORA - timedelta(days=1))
    compras = {e["ref"]: e["texto"] for e in cartao["linha_do_tempo"] if e["tipo"] == "compra"}
    assert compras["U1"].startswith("Aguardando pagamento")


async def test_ml_payment_required_nao_e_compra(
    db: AsyncSession, make_user, redis_falso, ml_falso
):
    user = await make_user()
    integ = await _integracao(db, user, IntegrationPlatform.ML)
    await _conversa(db, integ, canal="pergunta", externo_id="q:1", comprador_id=COMPRADOR_ML)
    conversa = await _conversa(
        db, integ, canal="pos_venda", externo_id="3000", comprador_id=COMPRADOR_ML
    )
    ml_falso.pedidos = [
        _pedido_ml(41, None, 1, 80.0, status="payment_required"),
        _pedido_ml(42, None, 2, 70.0, status="payment_in_process"),
        _pedido_ml(43, None, 5, 60.0),
    ]

    cartao = await cliente.cartao_cliente(db, conversa)

    assert (cartao["compras"], cartao["total_gasto"]) == (1, 60.0)
    assert "recorrente" not in cartao["sinais"]
