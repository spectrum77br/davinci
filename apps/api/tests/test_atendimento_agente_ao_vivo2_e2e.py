# ruff: noqa: F811 — fixtures importadas do e2e do agente
"""Os itens 9, 10 e 11 da tarde AO VIVO (Barbosa, 21/10/2026), de ponta a ponta.

Tudo de verdade como no `test_atendimento_agente_e2e` (o despacho do `_gerar`,
o SDK sobre o transporte falso, o pacote, a régua, as checagens, a
`conferir_saida(agente=True)`, o verificador roteirizado e o envio espião); a
API da Anthropic e a loja são falsas. Os textos são os dos rascunhos reais
(`test_atendimento_agente_ao_vivo2`).

(9)  cd4ff3d8: TikTok, Bling 15 "Em andamento" (com e sem a linha da logística)
     → o "já foi postado" e o caminho não seguram mais ("afirma o andamento do
     pedido sem o dado"), e o texto sai com "nos pedidos do app do TikTok Shop";
(10) ef4cb373: Shopee, a opção 6 do menu → o "Como podemos ajudar?" com a
     confiança da resposta certa sai sozinho; com a de antes (0,7), o limiar de
     sempre segura. O pedido vai com a régua nova da confiança;
(11) 31a72a93: TikTok, "tá descarregando muito rápido" → o acolhimento que repete
     o relato não é "característica que os dados não trazem ('bateria')" nem
     "fala do defeito fora da orientação aprovada".

A revisão (21/10/2026), com o verificador dando "ok" e o canal em AUTO:
(9)  "o andamento" com verbo de estado ("segue o andamento normalmente", "já
     mostra a coleta") no TikTok "Em digitação" (Bling 21, não postado) continua
     "afirma o andamento do pedido sem o dado" — não sai;
(10) a régua nova da confiança só no "como ajudar" do pede_atendente e da saudação;
(11) com o defeito no `auto_orientacao` (como nos canais AUTO de produção): o
     rascunho real sai sozinho, e a característica que o cliente só perguntou ou
     contestou ("carregamento rápido", "carregador turbo"), repetida no
     acolhimento, não sai.

Nenhuma chamada a LLM, à loja ou à produção.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import BlingOrder, IntegrationPlatform, Logistica
from app.services.atendimento import ia
from app.services.atendimento.agente import checagens
from app.services.atendimento.constantes import MODO_AUTO, RASCUNHO_PENDENTE
from tests.test_atendimento_agente_ao_vivo2 import (
    CAMINHO_CORRIDO,
    MENU_DA_LOJA,
    PERGUNTA_CHEGA,
    RASCUNHO_ATENDENTE,
    RASCUNHO_BATERIA,
    RASCUNHO_POSTADO,
    RELATO_BATERIA,
    STATUS_REAL,
)
from tests.test_atendimento_agente_ao_vivo_e2e import AUTO_KIA, _no_canal
from tests.test_atendimento_agente_e2e import (  # noqa: F401 — fixtures
    _decisao,
    _do_agente,
    _final,
    agente_ligado,
    api,
    envios,
    ia_ligada,
    leitura,
    lojas,
    redis_mem,
    sem_busca_pendurada,
    telegram,
)
from tests.test_atendimento_ia import _conversa, _importar_manual_base, _msg

PEDIDO_TK = "577009876543210"


async def _tiktok(db: AsyncSession, make_user, *, situacao: str, logistica: bool, item: str):
    db.add(
        BlingOrder(
            numero="92001",
            numeroloja=PEDIDO_TK,
            data=datetime(2026, 10, 19, 15, 0, tzinfo=UTC),
            situacao=situacao,
            item_index=0,
            item_descricao=item,
            item_codigo="dg089.ci",
            item_quantidade=1,
            nome_destinatario="Bia",
        )
    )
    if logistica:
        db.add(
            Logistica(
                pedido_bling="92001",
                pedido_marketplace=PEDIDO_TK,
                rastreio="999882500000001",
                postagem_data=date(2026, 10, 20),
                localizacao=STATUS_REAL,
                cliente_nome="Bia",
            )
        )
    await db.commit()
    await _importar_manual_base(db)
    return await _conversa(
        db,
        make_user,
        plataforma="tiktok",
        platform=IntegrationPlatform.TIKTOK,
        pedido=PEDIDO_TK,
        comprador_nome="Bia",
    )


# ─────────────── (9) cd4ff3d8: o "já foi postado" e o caminho do TikTok ───────────────


@pytest.mark.parametrize("logistica", [True, False], ids=["com_logistica", "so_bling"])
async def test_9_o_rascunho_real_do_tiktok_postado_sai_sem_o_alarme(
    db: AsyncSession, make_user, agente_ligado, api, envios, logistica
):
    conversa = await _tiktok(
        db,
        make_user,
        situacao="15",
        logistica=logistica,
        item="Uranyx A18 Pro Max - S5 Edition 16.128 - Prata",
    )
    await _no_canal(db, conversa, MODO_AUTO, orientacao=["cancelamento"])
    await _msg(db, conversa, PERGUNTA_CHEGA, ha=timedelta(minutes=6))
    api.roteiro = [
        _final(texto=RASCUNHO_POSTADO, assunto="rastreio", confianca=0.8, evidencias=["pedido"])
    ]

    r = await ia.gerar_rascunho(db, conversa)

    # O modelo viu o pedido postado (a situação do Bling, com ou sem a logística).
    assert '"postado":"sim"' in api.pacote()
    agente_ = _do_agente(r)
    assert checagens.MOTIVO_PEDIDO_SEM_LASTRO not in agente_["sem_auto"], agente_["sem_auto"]
    assert not any("andamento" in m or "postagem" in m for m in agente_["sem_auto"]), agente_
    # O texto que fica (e iria ao cliente) lê "nos pedidos do app do TikTok Shop".
    assert CAMINHO_CORRIDO in r.texto and "em os pedidos" not in r.texto, r.texto
    assert r.status == RASCUNHO_PENDENTE and not r.precisa_humano, r.motivo


async def test_9_o_tiktok_sem_postar_continua_preso(
    db: AsyncSession, make_user, agente_ligado, api, envios
):
    """Em digitação (21: a etiqueta pronta, não saiu): o "já foi postado" contradiz."""
    conversa = await _tiktok(
        db, make_user, situacao="21", logistica=False, item="Uranyx A18 Pro Max 16.128"
    )
    await _no_canal(db, conversa, MODO_AUTO, orientacao=["cancelamento"])
    await _msg(db, conversa, PERGUNTA_CHEGA, ha=timedelta(minutes=6))
    api.roteiro = [_final(texto=RASCUNHO_POSTADO, assunto="rastreio", evidencias=["pedido"])]

    r = await ia.gerar_rascunho(db, conversa)

    assert r.precisa_humano and envios == []
    assert any("ainda não foi postado" in m for m in _do_agente(r)["sem_auto"]), _do_agente(r)


# A revisão do item 9: "o andamento" afirmado (com verbo de estado) e o caminho certo.
_ANDAMENTO_AFIRMADO = [
    "Olá! Seu pedido segue o andamento normalmente, e você acompanha nos pedidos do app do "
    "TikTok Shop > o pedido > detalhes do envio.",
    "Olá! O andamento do envio já mostra a coleta, e você acompanha nos pedidos do app do "
    "TikTok Shop > o pedido > detalhes do envio.",
]


@pytest.mark.parametrize("texto", _ANDAMENTO_AFIRMADO, ids=["segue_o_andamento", "ja_mostra"])
async def test_9_o_andamento_afirmado_sem_postar_nao_sai(
    db: AsyncSession, make_user, agente_ligado, api, envios, texto
):
    """TikTok em AUTO, Bling 21 (Em digitação: não postado), o verificador com "ok":
    a isenção de "o andamento" não vale com verbo de estado na oração."""
    conversa = await _tiktok(
        db, make_user, situacao="21", logistica=False, item="Uranyx A18 Pro Max 16.128"
    )
    await _no_canal(db, conversa, MODO_AUTO)
    await _msg(db, conversa, PERGUNTA_CHEGA, ha=timedelta(minutes=6))
    api.roteiro = [_final(texto=texto, assunto="rastreio", confianca=0.95, evidencias=["pedido"])]

    r = await ia.gerar_rascunho(db, conversa)

    assert '"postado":"nao"' in api.pacote()
    agente_ = _do_agente(r)
    assert checagens.MOTIVO_PEDIDO_SEM_LASTRO in agente_["sem_auto"], agente_["sem_auto"]
    assert envios == [] and _decisao(r) != "enviado", (_decisao(r), r.motivo)


# ─────────────── (10) ef4cb373: o pede_atendente com a confiança certa ───────────────


@pytest.mark.parametrize(("confianca", "decisao"), [(0.9, "enviado"), (0.7, "confianca_baixa")])
async def test_10_o_como_podemos_ajudar_sai_com_a_confianca_da_resposta_certa(
    db: AsyncSession, make_user, agente_ligado, api, envios, confianca, decisao
):
    await _importar_manual_base(db)
    conversa = await _conversa(db, make_user, pedido=None, comprador_nome="Talita")
    await _no_canal(db, conversa, MODO_AUTO)
    await _msg(db, conversa, "Olá \nBoa tarde", ha=timedelta(minutes=5))
    await _msg(db, conversa, MENU_DA_LOJA, autor="loja", origem="externo", ha=timedelta(minutes=4))
    await _msg(db, conversa, "6", ha=timedelta(minutes=4))
    api.roteiro = [
        _final(
            acao="perguntar",
            texto=RASCUNHO_ATENDENTE,
            assunto="pede_atendente",
            confianca=confianca,
        )
    ]

    r = await ia.gerar_rascunho(db, conversa)

    assert "pede_atendente" in AUTO_KIA
    assert r.categoria == "pede_atendente" and r.texto == RASCUNHO_ATENDENTE, (r.categoria, r.texto)
    assert not r.precisa_humano and _do_agente(r)["sem_auto"] == [], r.motivo
    assert _decisao(r) == decisao
    if decisao == "enviado":
        assert [e["texto"] for e in envios] == [RASCUNHO_ATENDENTE]
    else:
        assert envios == []
    # A régua da confiança vai no pedido: no sistema e na descrição do campo — só para
    # o "como ajudar" do pede_atendente e da saudação (revisão do item 10).
    pedido = api.pedidos[0]
    assert "(perguntar como ajudar é resposta completa)" in pedido["system"][0]["text"]
    descricao = pedido["output_config"]["format"]["schema"]["properties"]["confianca"][
        "description"
    ]
    assert "pede_atendente" in descricao and "como ajudar" in descricao, descricao
    assert "pergunta certa" not in descricao, descricao


# ─────────────── (11) 31a72a93: o sintoma do cliente no acolhimento ───────────────


async def test_11_o_acolhimento_com_o_sintoma_do_cliente_nao_e_caracteristica(
    db: AsyncSession, make_user, agente_ligado, api, envios
):
    conversa = await _tiktok(
        db, make_user, situacao="83953", logistica=False, item="Uranyx A17 Pro Max 12.128 - Branco"
    )
    await _no_canal(db, conversa, MODO_AUTO)
    await _msg(db, conversa, RELATO_BATERIA, ha=timedelta(minutes=6))
    api.roteiro = [
        _final(texto=RASCUNHO_BATERIA, assunto="defeito", confianca=0.85, evidencias=["pedido"])
    ]

    r = await ia.gerar_rascunho(db, conversa)

    agente_ = _do_agente(r)
    assert "caracteristica_fora_do_anuncio" not in agente_["checagens"], agente_
    assert "defeito_fora_da_orientacao" not in agente_["checagens"], agente_
    assert not any("bateria" in m for m in agente_["sem_auto"]), agente_["sem_auto"]
    assert r.texto == RASCUNHO_BATERIA and not r.precisa_humano, r.motivo


async def test_11_a_causa_que_o_cliente_nao_disse_continua_presa(
    db: AsyncSession, make_user, agente_ligado, api, envios
):
    conversa = await _tiktok(
        db, make_user, situacao="83953", logistica=False, item="Uranyx A17 Pro Max 12.128 - Branco"
    )
    await _no_canal(db, conversa, MODO_AUTO)
    await _msg(db, conversa, RELATO_BATERIA, ha=timedelta(minutes=6))
    texto = RASCUNHO_BATERIA.replace("rápido.", "rápido por causa do uso intenso.", 1)
    api.roteiro = [_final(texto=texto, assunto="defeito", evidencias=["pedido"])]

    r = await ia.gerar_rascunho(db, conversa)

    assert r.precisa_humano and envios == []
    assert "defeito_fora_da_orientacao" in _do_agente(r)["checagens"], _do_agente(r)


# A revisão do item 11: o pedido de vídeo do rascunho real, depois do acolhimento.
_PEDIDO_DE_VIDEO = RASCUNHO_BATERIA.split(". ", 1)[1]


@pytest.mark.parametrize(
    ("relato", "acolhimento", "sai"),
    [
        (RELATO_BATERIA, "Lamentamos o transtorno com a bateria descarregando rápido.", True),
        (
            "era pra ser carregamento rápido",
            "Lamentamos o transtorno com o carregamento rápido.",
            False,
        ),
        (
            "o carregador é turbo? tá carregando devagar",
            "Lamentamos o transtorno com o carregador turbo carregando devagar.",
            False,
        ),
    ],
    ids=["relato_real", "carregamento_rapido", "carregador_turbo"],
)
async def test_11_com_o_defeito_no_auto_so_o_relato_sai_sozinho(
    db: AsyncSession, make_user, agente_ligado, api, envios, relato, acolhimento, sai
):
    """TikTok em AUTO com o defeito no `auto_orientacao` (como nos canais AUTO de
    produção), o verificador com "ok" e o pedido de vídeo: o rascunho real sai; a
    característica que o cliente perguntou ou contestou, repetida no acolhimento, não."""
    conversa = await _tiktok(
        db, make_user, situacao="83953", logistica=False, item="Uranyx A17 Pro Max 12.128 - Branco"
    )
    await _no_canal(db, conversa, MODO_AUTO, orientacao=["defeito"])
    await _msg(db, conversa, relato, ha=timedelta(minutes=6))
    texto = f"{acolhimento} {_PEDIDO_DE_VIDEO}"
    api.roteiro = [_final(texto=texto, assunto="defeito", confianca=0.9, evidencias=["pedido"])]

    r = await ia.gerar_rascunho(db, conversa)

    agente_ = _do_agente(r)
    if sai:
        assert agente_["checagens"] == [] and agente_["sem_auto"] == [], agente_
        assert _decisao(r) == "enviado" and [e["texto"] for e in envios] == [texto], r.motivo
    else:
        # A característica sem fonte (pela ficha ou pelo vocabulário) e o defeito fora
        # da orientação, como antes do eco.
        caracteristica = {"caracteristica_fora_do_anuncio", checagens.FICHA_NO_AUTOMATICO}
        assert caracteristica & set(agente_["checagens"]), agente_
        assert "defeito_fora_da_orientacao" in agente_["checagens"], agente_
        assert r.precisa_humano and envios == [] and _decisao(r) != "enviado", r.motivo
