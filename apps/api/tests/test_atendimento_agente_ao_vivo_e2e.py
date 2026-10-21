# ruff: noqa: F811 — fixtures importadas do e2e do agente
"""Os ajustes do 1º dia AO VIVO (Kia) e do eval cego do TikTok, de ponta a ponta (21/10/2026).

Tudo de verdade como no `test_atendimento_agente_e2e` (o despacho do `_gerar`,
o SDK sobre o transporte falso, o pacote, as ferramentas, a régua, as
checagens, a `conferir_saida(agente=True)`, o verificador roteirizado e o
envio espião); a API da Anthropic e a loja são falsas. Os textos são os dos
rascunhos reais (`test_atendimento_agente_ao_vivo`).

(1) paulovitor85: "Esse relógio é recondicionado?" com o anúncio "Condição:
    Novo" (o atributo da Shopee) → "É novo, não recondicionado" é sugestão limpa
    (antes: "condição de novo sem confirmação no título do anúncio");
(3) 15da122e: a equipe disse "vamos abrir um chamado", a cliente só "Ok
    Aguardando" / "Obrigada" → o agente propõe repetir a orientação e pedir
    `abrir_chamado` de novo → DESCARTADO, nada enviado, nenhuma ação; a linha
    curta de cortesia do modelo sai; pergunta nova é respondida;
(6) o pacote escreve os ids que prova (a linha EVIDÊNCIAS);
(7) TikTok: o `consultar_manual` devolve o caminho do TikTok Shop; o
    "Minhas compras" no rascunho não sai sozinho;
(8) TikTok: o pedido entregue pela situação do Bling (sem a linha da
    logística) + "não chegou" → `abrir_chamado` (antes saía sozinho).

Nenhuma chamada a LLM, à loja ou à produção.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import BlingOrder, IntegrationPlatform
from app.services.atendimento import agente, ficha, ia
from app.services.atendimento.agente import pacote
from app.services.atendimento.constantes import (
    MODO_AUTO,
    MODO_COPILOTO,
    RASCUNHO_DESCARTADO,
    RASCUNHO_PENDENTE,
)
from tests.test_atendimento_agente_ao_vivo import (
    CITACAO_DA_LOJA,
    NO_TIKTOK,
    PEDIU_AJUDA,
    PERGUNTA_RELOGIO,
    RESPOSTA_RELOGIO,
    TITULO_RELOGIO,
    VAMOS_ABRIR,
)
from tests.test_atendimento_agente_e2e import (  # noqa: F401 — fixtures
    _do_agente,
    _final,
    _ligar,
    _usa,
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
from tests.test_atendimento_ia import (
    PEDIDO_MKT,
    _conversa,
    _importar_manual_base,
    _msg,
    _pedido_completo,
)

# Os assuntos que o dono liberou na Kia Shopee (o `auto_agente`, 21/10/2026).
AUTO_KIA = [
    "prazo_envio", "rastreio", "entrega_instrucoes", "nota_fiscal", "pedido_nao_localizado",
    "confirmar_variacao", "uso_configuracao", "preco_pagamento", "estoque_variacao",
    "cobertura_regiao", "prazo_antes_compra", "acessorios_avulsos", "pedido_link_midia",
    "opiniao_recomendacao", "parceria_fora_escopo", "canal_externo_dados", "pede_atendente",
    "saudacao_ou_vaga", "agradecimento", "entrega_nao_recebida",
]  # fmt: skip


async def _no_canal(db, conversa, modo: str = MODO_AUTO, orientacao: list | None = None) -> None:
    await _ligar(
        db,
        conversa,
        modo=modo,
        auto_categorias=AUTO_KIA,
        auto_orientacao=orientacao or [],
        auto_agente=AUTO_KIA,
    )


# ─────────────── (1) a condição pelo anúncio: paulovitor85 ───────────────

ITEM_RELOGIO = "22111222333"
RELOGIO_HOJE = {
    "item_id": int(ITEM_RELOGIO),
    "item_name": TITULO_RELOGIO,
    "description": "Smartwatch com tela de 2,2 polegadas e chamadas por Bluetooth.",
    "has_model": False,
    "item_status": "NORMAL",
    "condition": "NEW",
    "attribute_list": [
        {
            "original_attribute_name": "Condição",
            "attribute_value_list": [{"original_value_name": "Novo"}],
        },
        {
            "original_attribute_name": "Tamanho da tela",
            "attribute_value_list": [{"original_value_name": "2.2"}],
        },
    ],
}


async def _relogio(db: AsyncSession, make_user, redis_mem, lojas, item: dict = RELOGIO_HOJE):
    await _importar_manual_base(db)
    conversa = await _conversa(db, make_user, pedido=None, comprador_nome="Paulo")
    conversa.anuncio_id = ITEM_RELOGIO
    conversa.anuncio_titulo = None
    await db.commit()
    lojas.shopee[ITEM_RELOGIO] = item
    bruta = ficha.ficha_bruta_shopee(item)
    redis_mem.dados[ficha.chave_ficha("shopee", conversa.integration_id, ITEM_RELOGIO)] = (
        json.dumps({**bruta, "buscada_em": datetime.now(UTC).isoformat()}, ensure_ascii=False)
    )
    return conversa


async def test_1_paulovitor85_o_novo_pelo_atributo_de_condicao(
    db: AsyncSession, make_user, agente_ligado, api, envios, redis_mem, lojas
):
    conversa = await _relogio(db, make_user, redis_mem, lojas)
    await _no_canal(db, conversa, MODO_COPILOTO)
    await _msg(db, conversa, PERGUNTA_RELOGIO, ha=timedelta(minutes=6))
    api.roteiro = [
        _final(
            texto=RESPOSTA_RELOGIO,
            assunto="originalidade_condicao",
            evidencias=[f"ficha:{ITEM_RELOGIO}"],
        )
    ]

    r = await ia.gerar_rascunho(db, conversa)

    # O pacote levou a condição que o anúncio declara (o atributo "Condição: Novo").
    assert (
        '"condicao":{"conflito_no_anuncio":false,"novo":{"fontes":["atributo"],"valor":true}}'
        in _texto_do_pacote(api)
    )
    assert r.texto == RESPOSTA_RELOGIO and r.status == RASCUNHO_PENDENTE
    assert not r.precisa_humano, r.motivo
    assert "sem confirmação no título" not in (r.motivo or "")
    assert envios == []


async def test_1_o_anuncio_que_se_contradiz_vai_para_pessoa(
    db: AsyncSession, make_user, agente_ligado, api, envios, redis_mem, lojas
):
    item = {**RELOGIO_HOJE, "item_name": "Smartwatch W69 Ultra Seminovo Revisado"}
    conversa = await _relogio(db, make_user, redis_mem, lojas, item)
    await _no_canal(db, conversa, MODO_COPILOTO)
    await _msg(db, conversa, PERGUNTA_RELOGIO, ha=timedelta(minutes=6))
    api.roteiro = [
        _final(
            texto=RESPOSTA_RELOGIO,
            assunto="originalidade_condicao",
            evidencias=[f"ficha:{ITEM_RELOGIO}"],
        )
    ]

    r = await ia.gerar_rascunho(db, conversa)

    assert r.precisa_humano and "corrigir anúncio" in (r.motivo or ""), r.motivo
    assert envios == []


# ─────────────── (3) o "ok" depois de a equipe tratar: 15da122e ───────────────

# O rascunho real: a orientação repetida e o `abrir_chamado` que a equipe já prometeu.
RASCUNHO_REPETIDO = (
    "Entendemos sua preocupação, já que o presente é para o aniversário do seu filho. O pedido "
    "já foi postado, e o andamento do envio e a previsão aparecem em Minhas Compras > o pedido > "
    "Informações do envio."
)
MOTIVO_REPETIDO = (
    "A loja disse nesta conversa que abriria um chamado; a cliente aguarda e pede ajuda com um "
    "prazo urgente (aniversário amanhã), sem previsão de entrega no pedido. Abrir o chamado e "
    "acompanhar o caso."
)


async def _caso_do_chamado(db: AsyncSession, make_user, *cliente: str):
    await _importar_manual_base(db)
    await _pedido_completo(db)
    conversa = await _conversa(db, make_user, pedido=PEDIDO_MKT, comprador_nome="Ana")
    for texto, autor, minutos in (
        (PEDIU_AJUDA, "cliente", 64),
        ("Poderia por favor Ver o que pode fazer para me ajudar", "cliente", 63),
        ("Boa tarde", "cliente", 13),
        (CITACAO_DA_LOJA, "loja", 2),
        (VAMOS_ABRIR, "loja", 2),
    ):
        await _msg(
            db,
            conversa,
            texto,
            autor=autor,
            origem="externo" if autor == "loja" else None,
            ha=timedelta(minutes=minutos),
        )
    for i, texto in enumerate(cliente):
        await _msg(db, conversa, texto, ha=timedelta(minutes=1, seconds=-i))
    return conversa


@pytest.mark.parametrize("modo", [MODO_AUTO, MODO_COPILOTO])
async def test_3_ok_aguardando_depois_do_chamado_prometido_nao_pede_rascunho(
    db: AsyncSession, make_user, agente_ligado, api, envios, modo
):
    conversa = await _caso_do_chamado(db, make_user, "Ok Aguardando", "Obrigada")
    await _no_canal(db, conversa, modo)
    api.roteiro = [
        _final(
            acao="pessoa",
            texto=RASCUNHO_REPETIDO,
            assunto="rastreio",
            acao_pessoa="abrir_chamado",
            motivo_pessoa=MOTIVO_REPETIDO,
            evidencias=["pedido"],
        )
    ]

    r = await ia.gerar_rascunho(db, conversa)

    assert r.status == RASCUNHO_DESCARTADO, (r.status, r.motivo)
    assert r.motivo == agente.MOTIVO_JA_TRATADO and not r.precisa_humano
    assert _do_agente(r)["acoes_pessoa"] == []
    assert envios == []


async def test_3_a_linha_curta_de_cortesia_do_modelo_fica(
    db: AsyncSession, make_user, agente_ligado, api, envios
):
    """A linha curta de cortesia ("no máximo uma linha, se o estilo da loja pede")
    continua a sugestão de sempre (no automático, a pausa da equipe que acabou de
    responder segura o envio, como antes)."""
    conversa = await _caso_do_chamado(db, make_user, "Ok Aguardando", "Obrigada")
    await _no_canal(db, conversa, MODO_COPILOTO)
    cortesia = "Por nada! Qualquer coisa, estamos à disposição."
    api.roteiro = [_final(texto=cortesia, assunto="agradecimento")]

    r = await ia.gerar_rascunho(db, conversa)

    assert r.status == RASCUNHO_PENDENTE and r.texto == cortesia, (r.status, r.motivo)
    assert not r.precisa_humano and _do_agente(r)["acoes_pessoa"] == []
    assert envios == []


async def test_3_com_pergunta_nova_o_agente_responde(
    db: AsyncSession, make_user, agente_ligado, api, envios
):
    conversa = await _caso_do_chamado(db, make_user, "Ok", "e consegue chegar até amanhã?")
    await _no_canal(db, conversa, MODO_COPILOTO)
    texto = (
        "Não conseguimos garantir a entrega até amanhã; a previsão aparece em Minhas compras > o "
        "pedido > Informações do envio."
    )
    api.roteiro = [_final(texto=texto, assunto="rastreio", evidencias=["pedido"])]

    r = await ia.gerar_rascunho(db, conversa)

    assert r.status != RASCUNHO_DESCARTADO and r.texto == texto, (r.status, r.motivo)


# ─────────────── (6) o pacote escreve os ids que prova ───────────────


async def test_6_o_pacote_tem_a_linha_das_evidencias(
    db: AsyncSession, make_user, agente_ligado, api, envios, redis_mem, lojas
):
    conversa = await _relogio(db, make_user, redis_mem, lojas)
    await _no_canal(db, conversa, MODO_COPILOTO)
    await _msg(db, conversa, PERGUNTA_RELOGIO, ha=timedelta(minutes=6))
    api.roteiro = [_final(texto=RESPOSTA_RELOGIO, assunto="originalidade_condicao")]

    await ia.gerar_rascunho(db, conversa)

    (linha,) = [
        linha
        for linha in _texto_do_pacote(api).splitlines()
        if linha.startswith(pacote.EVIDENCIAS_DOS_DADOS)
    ]
    ids = json.loads(linha.split(": ", 1)[1])
    assert f"ficha:{ITEM_RELOGIO}" in ids and not any(i.startswith("regra:") for i in ids), ids


def _texto_do_pacote(api) -> str:
    conteudo = api.pedidos[0]["messages"][0]["content"]
    if isinstance(conteudo, str):
        return conteudo
    return "".join(c.get("text", "") for c in conteudo)


# ─────────────── (7) e (8) o TikTok ───────────────

PEDIDO_TK = "577001234567890"


async def _tiktok(db: AsyncSession, make_user, *, pedido: str | None = None):
    await _importar_manual_base(db)
    return await _conversa(
        db,
        make_user,
        plataforma="tiktok",
        platform=IntegrationPlatform.TIKTOK,
        pedido=pedido,
        comprador_nome="Bia",
    )


async def test_7_o_manual_no_tiktok_vem_com_o_caminho_do_tiktok(
    db: AsyncSession, make_user, agente_ligado, api, envios
):
    conversa = await _tiktok(db, make_user)
    await _no_canal(db, conversa, MODO_AUTO, orientacao=["cancelamento"])
    await _msg(db, conversa, "como faço pra cancelar a compra?", ha=timedelta(minutes=6))
    api.roteiro = [
        _usa("consultar_manual", assunto="cancelamento", pergunta=None),
        _final(texto=NO_TIKTOK, assunto="rastreio", evidencias=["regra:cancelamento"]),
    ]

    r = await ia.gerar_rascunho(db, conversa)

    manual = api.resultado(1)
    assert "Minhas compras" not in manual and "Minhas Compras" not in manual, manual
    assert "nos pedidos do app do TikTok Shop" in manual, manual
    # O sistema do TikTok avisa que lá não existe "Minhas compras".
    assert 'No TikTok Shop não existe \\"Minhas compras\\"' in json.dumps(
        api.pedidos[0]["system"], ensure_ascii=False
    )
    # O "Minhas compras" no rascunho do TikTok não sai sozinho (regra suave).
    agente_ = _do_agente(r)
    assert any("Minhas compras" in m for m in agente_["sem_auto"]), agente_["sem_auto"]
    assert envios == []


async def test_8_tiktok_consta_entregue_pela_situacao_do_bling_e_nao_chegou(
    db: AsyncSession, make_user, agente_ligado, api, envios
):
    db.add(
        BlingOrder(
            numero="91001",
            numeroloja=PEDIDO_TK,
            data=datetime(2026, 10, 10, 15, 0, tzinfo=UTC),
            situacao="83953",
            item_index=0,
            item_descricao="Fone Bluetooth X1",
            item_codigo="FONE-X1",
            item_quantidade=1,
            nome_destinatario="Bia",
        )
    )
    await db.commit()
    conversa = await _tiktok(db, make_user, pedido=PEDIDO_TK)
    await _no_canal(db, conversa, MODO_AUTO)
    await _msg(db, conversa, "meu pedido não chegou", ha=timedelta(minutes=6))
    # O que o eval viu sair sozinho: "consta que foi entregue".
    texto = (
        "Olá! Consta no sistema que o pedido {numero_pedido} foi entregue. Confira com vizinhos "
        "ou portaria."
    )
    api.roteiro = [_final(texto=texto, assunto="entrega_nao_recebida", evidencias=["pedido"])]

    r = await ia.gerar_rascunho(db, conversa)

    # O modelo viu `entregue: true` (a situação do Bling) — e a régua do chamado também.
    assert '"entregue":true' in _texto_do_pacote(api)
    assert "abrir_chamado" in _do_agente(r)["acoes_pessoa"], _do_agente(r)
    assert r.precisa_humano and envios == []
