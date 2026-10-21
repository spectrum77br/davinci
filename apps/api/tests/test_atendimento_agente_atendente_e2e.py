# ruff: noqa: F811 — fixtures importadas do e2e do agente
"""O AGENTE É O ATENDENTE, de ponta a ponta pelo `ia.gerar_rascunho` (21/10/2026).

Decisão do dono (Eduardo, 21/10/2026, no chat): "sobre esse aí ele conseguiria
responder automaticamente, como se ele fosse o atendente; se ele puder e
conseguir, ele mesmo responde". O caso real (Kia Shopee, 20–21/10): o
comprador perguntou se o pote de 2,5 L esgotado ia voltar, escolheu a opção 6
do menu ("Falar com Atendente"), recebeu o "Em que podemos ajudar?", repetiu
a pergunta, reclamou que não teve resposta e escolheu o 6 de novo — a régua
mandou para pessoa ("cliente insistiu em falar com uma pessoa") e o agente
sabia responder (esgotado no anúncio, sem previsão de reposição).

Tudo de verdade como no `test_atendimento_agente_e2e` (o despacho do `_gerar`,
o SDK sobre o transporte falso, o pacote, as ferramentas, a régua, as
checagens, a `conferir_saida(agente=True)`, o verificador roteirizado e o
envio espião); a API da Anthropic e a loja são falsas.

(a) o caso real (o pote esgotado + o "6" duas vezes) → o agente responde como
    o atendente, o assunto é `estoque_variacao` e, no automático com o assunto
    liberado, sai sozinho; no copiloto, a sugestão limpa;
(b) o 1º pedido de atendente → respondido ("em que posso ajudar?");
(c) o pedido repetido depois da resposta, sem nada novo → pessoa
    (`pediu_pessoa`), mesmo com o modelo respondendo; com pergunta nova, o
    agente responde — e a resposta genérica (`pede_atendente`) não resolve a
    pergunta que veio junto do pedido;
(d) o pedido repetido com irritação → pessoa;
(e) "é robô?" → a resposta honesta sai; e o "quero a pessoa" depois dela →
    pessoa;
(f) o texto do agente que se diz pessoa → bloqueado, nos dois modos;
(g) o canal SEM o agente (o legado) → o pedido de atendente vai para pessoa
    como sempre.

Revisão de 21/10/2026: o laço depois da resposta DE VERDADE (o "não respondeu"
+ "6", a mesma pergunta repetida com o pedido, o teto do 3º pedido) → pessoa;
o "tem atendente?" do 1º pedido sai; "uma pessoa por favor"/"humano" aceitam a
oferta; "Não, aqui é a equipe da loja" ao "é robô?" → bloqueado, e o texto que
foge da pergunta não sai sozinho.

Nenhuma chamada a LLM, à loja ou à produção.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Integration, IntegrationPlatform, Product, ProductLink
from app.models.enums import LinkSyncStatus
from app.services.atendimento import ficha, ia
from app.services.atendimento.agente import acao_pessoa, checagens
from app.services.atendimento.constantes import (
    MODO_AUTO,
    MODO_COPILOTO,
    ORIGEM_IA,
    PROMPT_VERSAO_AGENTE,
    RASCUNHO_BLOQUEADO,
    RASCUNHO_PENDENTE,
)
from tests.test_atendimento_agente_e2e import (  # noqa: F401 — fixtures
    _decisao,
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
from tests.test_atendimento_ia import _conversa, _importar_manual_base, _msg

# Os assuntos que o dono liberou na Kia Shopee (produção, 21/10/2026: o
# `auto_agente` do canal) e a orientação.
AUTO_AGENTE_KIA = [
    "prazo_envio", "rastreio", "entrega_instrucoes", "nota_fiscal", "pedido_nao_localizado",
    "confirmar_variacao", "uso_configuracao", "preco_pagamento", "estoque_variacao",
    "cobertura_regiao", "prazo_antes_compra", "acessorios_avulsos", "pedido_link_midia",
    "opiniao_recomendacao", "parceria_fora_escopo", "canal_externo_dados", "pede_atendente",
    "saudacao_ou_vaga", "agradecimento",
]  # fmt: skip

# O menu do Duoke da Kia e a resposta automática ao "6" (os textos reais).
MENU = (
    "Olá, por favor selecione sua dúvida e logo um dos nossos consultores irá atendê-lo! "
    "1 - Previsão de entrega / envio 2 - Nota fiscal 3 - Encerramento da compra 4 - Troca de "
    "endereço 5 - Garantia 6 - Falar com Atendente"
)
DESCREVA = "Descreva sua dúvida que assim que um atendente estiver disponível ele irá te responder!"
EM_QUE_AJUDAR = "Olá! Em que podemos ajudar sobre esse produto ou pedido?"

# O anúncio do pote (o do caso real, sem dado da loja de verdade).
ITEM_POTE = "40123456789"
TITULO_POTE = "Pote Hermético de Vidro com Tampa 1,5 L e 2,5 L"
DESCRICAO_POTE = "Pote hermético de vidro com tampa de travas.\nVai ao micro-ondas sem a tampa.\n"
POTE_15, POTE_25 = "300000000001", "300000000002"
ITEM_POTE_HOJE = {
    "item_id": int(ITEM_POTE),
    "item_name": TITULO_POTE,
    "description": DESCRICAO_POTE,
    "has_model": True,
    "item_status": "NORMAL",
    "attribute_list": [
        {
            "original_attribute_name": "Material",
            "attribute_value_list": [{"original_value_name": "Vidro"}],
        }
    ],
    "brand": {"original_brand_name": "Kia Casa"},
}
MODELOS_POTE = {
    "tier_variation": [
        {"name": "Capacidade", "option_list": [{"option": "1,5 L"}, {"option": "2,5 L"}]}
    ],
    "model": [
        {
            "model_id": int(POTE_15),
            "tier_index": [0],
            "model_sku": "pote15",
            "model_status": "MODEL_NORMAL",
            "stock_info_v2": {"summary_info": {"total_available_stock": 12}},
            "price_info": [{"current_price": 49.9, "original_price": 59.9}],
        },
        {
            "model_id": int(POTE_25),
            "tier_index": [1],
            "model_sku": "pote25",
            "model_status": "MODEL_NORMAL",
            "stock_info_v2": {"summary_info": {"total_available_stock": 0}},
            "price_info": [{"current_price": 69.9, "original_price": 79.9}],
        },
    ],
}
VAR_POTE = f"variacoes:{ITEM_POTE}"
RESPOSTA_DO_POTE = (
    "Olá! O pote de 2,5 L está esgotado no momento neste anúncio, e não temos previsão de "
    "reposição."
)
HONESTA = (
    "Olá! Sou o atendimento virtual da loja. Se preferir, uma pessoa da nossa equipe pode "
    "assumir a conversa."
)


async def _pote(db: AsyncSession, make_user, redis_mem, lojas):
    """A conversa da Kia no anúncio do pote (sem pedido): o 2,5 L esgotado (a loja,
    o vínculo e o produto em 0 — "esgotado" confiável), o 1,5 L disponível."""
    await _importar_manual_base(db)
    conversa = await _conversa(db, make_user, pedido=None, comprador_nome="Maria")
    conversa.anuncio_id = ITEM_POTE
    conversa.anuncio_titulo = None  # como nas conversas da Shopee em produção
    await db.commit()
    integ = await db.get(Integration, conversa.integration_id)
    for vid, sku, opcao, estoque in (
        (POTE_15, "pote15", "1,5 L", 12),
        (POTE_25, "pote25", "2,5 L", 0),
    ):
        produto = Product(
            user_id=integ.user_id, sku=sku, name=f"Pote Hermético Vidro {opcao}", stock=estoque
        )
        db.add(produto)
        await db.flush()
        db.add(
            ProductLink(
                user_id=integ.user_id,
                product_id=produto.id,
                integration_id=integ.id,
                platform=IntegrationPlatform.SHOPEE,
                external_id=ITEM_POTE,
                variation_id=vid,
                external_sku=sku,
                listing_title=f"{TITULO_POTE} - {opcao}",
                stock=estoque,
                last_sync_status=LinkSyncStatus.OK,
            )
        )
    await db.commit()
    lojas.shopee[ITEM_POTE] = ITEM_POTE_HOJE
    lojas.modelos[ITEM_POTE] = MODELOS_POTE
    bruta = ficha.ficha_bruta_shopee(ITEM_POTE_HOJE)
    redis_mem.dados[ficha.chave_ficha("shopee", integ.id, ITEM_POTE)] = json.dumps(
        {**bruta, "buscada_em": datetime.now(UTC).isoformat()}, ensure_ascii=False
    )
    return conversa


async def _caso_real(db: AsyncSession, conversa) -> None:
    """A linha do tempo do caso real (20–21/10/2026), com as horas relativas."""
    linha = [
        (
            "cliente",
            None,
            "Olá, bom dia Haverá renovação do estoque ? Estou querendo muito um",
            1600,
        ),
        ("loja", "externo", MENU, 1599),
        ("cliente", None, "6", 1598),
        ("loja", ORIGEM_IA, EM_QUE_AJUDAR, 1597),
        (
            "cliente",
            None,
            "O produto está como esgotado, eu queria saber se você vao ter mais em estoque, "
            "estou querendo comprar o pote de 2,5",
            1596,
        ),
        ("cliente", None, "Você não respondeu a minha pergunta", 1480),
        ("loja", "externo", MENU, 840),
        ("cliente", None, "6", 62),
        ("loja", "externo", DESCREVA, 61),
    ]
    for autor, origem, texto, minutos in linha:
        await _msg(db, conversa, texto, autor=autor, origem=origem, ha=timedelta(minutes=minutos))


async def _no_canal(db, conversa, modo: str) -> None:
    await _ligar(
        db,
        conversa,
        modo=modo,
        auto_categorias=AUTO_AGENTE_KIA,
        auto_orientacao=[],
        auto_agente=AUTO_AGENTE_KIA,
    )


# ─────────────── (a) o caso real ───────────────


@pytest.mark.parametrize("modo", [MODO_AUTO, MODO_COPILOTO])
async def test_a_o_caso_real_o_agente_responde_como_o_atendente(
    db: AsyncSession, make_user, agente_ligado, api, envios, redis_mem, lojas, modo
):
    conversa = await _pote(db, make_user, redis_mem, lojas)
    await _no_canal(db, conversa, modo)
    await _caso_real(db, conversa)
    api.roteiro = [
        _usa("ver_anuncio", item_id=ITEM_POTE),
        _final(texto=RESPOSTA_DO_POTE, assunto="estoque_variacao", evidencias=[VAR_POTE]),
    ]

    r = await ia.gerar_rascunho(db, conversa)

    agente = _do_agente(r)
    # O agente RODOU (antes: pessoa pela régua, "cliente insistiu…") e respondeu.
    assert len(api.pedidos) == 2 and agente["acoes_pessoa"] == [], agente["acoes_pessoa"]
    assert "insistiu" not in (r.motivo or ""), r.motivo
    assert r.categoria == "estoque_variacao" and r.texto == RESPOSTA_DO_POTE
    assert r.status == RASCUNHO_PENDENTE and not r.precisa_humano, (r.motivo, r.validador_erros)
    assert agente["sem_auto"] == [], agente["sem_auto"]
    # A ferramenta mostrou o 2,5 L esgotado (o que o texto afirma).
    assert "2,5 L" in api.resultado(1)
    if modo == MODO_AUTO:
        assert [e["texto"] for e in envios] == [RESPOSTA_DO_POTE] and _decisao(r) == "enviado"
    else:
        assert envios == []


# ─────────────── (b) o 1º pedido ───────────────


@pytest.mark.parametrize(
    "pedido",
    [
        "quero falar com um atendente",
        # revisão de 21/10/2026: o "tem…?" do pedido não é "pergunta concreta pendente"
        "tem atendente?",
        "tem algum atendente disponível?",
        "tem alguém pra me ajudar?",
    ],
)
async def test_b_o_1o_pedido_de_atendente_e_respondido(
    db: AsyncSession, make_user, agente_ligado, api, envios, redis_mem, lojas, pedido
):
    conversa = await _pote(db, make_user, redis_mem, lojas)
    await _no_canal(db, conversa, MODO_AUTO)
    await _msg(db, conversa, pedido, ha=timedelta(minutes=6))
    texto = "Olá! Sou do atendimento da loja. Em que posso ajudar?"
    api.roteiro = [_final(texto=texto, assunto="pede_atendente")]

    r = await ia.gerar_rascunho(db, conversa)

    assert _do_agente(r)["acoes_pessoa"] == [] and not r.precisa_humano, r.motivo
    assert r.categoria == "pede_atendente" and r.texto == texto
    assert [e["texto"] for e in envios] == [texto] and envios[0]["origem"] == ORIGEM_IA


# ─────────────── (c) e (d) o pedido repetido ───────────────


@pytest.mark.parametrize(
    ("de_novo", "motivo"),
    [
        ("quero falar com um atendente", ia.MOTIVO_INSISTIU_EM_PESSOA),
        ("6", ia.MOTIVO_INSISTIU_EM_PESSOA),
        (
            "QUE ABSURDO, já pedi 3 vezes, quero falar com um atendente",
            acao_pessoa.MOTIVO_PEDIU_IRRITADO,
        ),
    ],
)
async def test_c_d_pedir_de_novo_sem_nada_novo_ou_irritado_vai_para_pessoa(
    db: AsyncSession, make_user, agente_ligado, api, envios, redis_mem, lojas, de_novo, motivo
):
    conversa = await _pote(db, make_user, redis_mem, lojas)
    await _no_canal(db, conversa, MODO_AUTO)
    await _msg(db, conversa, "quero falar com um atendente", ha=timedelta(minutes=30))
    await _msg(
        db,
        conversa,
        "Olá! Sou do atendimento da loja. Em que posso ajudar?",
        autor="loja",
        origem=ORIGEM_IA,
        ha=timedelta(minutes=29),
    )
    if de_novo == "6":
        await _msg(db, conversa, MENU, autor="loja", origem="externo", ha=timedelta(minutes=10))
    await _msg(db, conversa, de_novo, ha=timedelta(minutes=6))
    # O modelo "responde" — a régua do CÓDIGO vence.
    api.roteiro = [_final(texto="Olá! Em que posso ajudar?", assunto="pede_atendente")]

    r = await ia.gerar_rascunho(db, conversa)

    agente = _do_agente(r)
    assert "pediu_pessoa" in agente["acoes_pessoa"] and r.precisa_humano, agente
    assert motivo in (r.motivo or ""), r.motivo
    assert envios == []


@pytest.mark.parametrize(
    ("linha", "motivo"),
    [
        # (a) a resposta de verdade, e "Você não respondeu a minha pergunta" + "6"
        (
            [
                ("cliente", None, "o pote de 2,5 L vai voltar ao estoque?", 30),
                ("loja", ORIGEM_IA, RESPOSTA_DO_POTE, 29),
                ("loja", "externo", MENU, 10),
                ("cliente", None, "Você não respondeu a minha pergunta", 7),
                ("cliente", None, "6", 6),
            ],
            acao_pessoa.MOTIVO_NAO_RESOLVEU,
        ),
        # (b) a mesma pergunta, já respondida de verdade, com o pedido de novo
        (
            [
                ("cliente", None, "o pote vai voltar? quero falar com atendente", 30),
                ("loja", ORIGEM_IA, RESPOSTA_DO_POTE, 29),
                ("cliente", None, "o pote vai voltar? quero falar com atendente", 6),
            ],
            ia.MOTIVO_INSISTIU_EM_PESSOA,
        ),
        # (b) e o teto: o 3º pedido seguido
        (
            [
                ("cliente", None, "o pote vai voltar? quero falar com atendente", 40),
                ("loja", ORIGEM_IA, RESPOSTA_DO_POTE, 39),
                ("cliente", None, "o pote vai voltar? quero falar com atendente", 30),
                ("loja", ORIGEM_IA, RESPOSTA_DO_POTE, 29),
                ("cliente", None, "o pote vai voltar? quero falar com atendente", 6),
            ],
            acao_pessoa.MOTIVO_PEDIU_3_VEZES,
        ),
    ],
    ids=["nao_respondeu_depois_da_resposta", "mesma_pergunta_2x", "teto_3x"],
)
async def test_c_o_laco_depois_da_resposta_de_verdade_vai_para_pessoa(
    db: AsyncSession, make_user, agente_ligado, api, envios, redis_mem, lojas, linha, motivo
):
    """Revisão de 21/10/2026: a loja respondeu DE VERDADE e o cliente segue pedindo a
    pessoa — no automático, a mesma resposta sairia sem parar. A régua do CÓDIGO vence."""
    conversa = await _pote(db, make_user, redis_mem, lojas)
    await _no_canal(db, conversa, MODO_AUTO)
    for autor, origem, texto, minutos in linha:
        await _msg(db, conversa, texto, autor=autor, origem=origem, ha=timedelta(minutes=minutos))
    api.roteiro = [
        _usa("ver_anuncio", item_id=ITEM_POTE),
        _final(texto=RESPOSTA_DO_POTE, assunto="estoque_variacao", evidencias=[VAR_POTE]),
    ]

    r = await ia.gerar_rascunho(db, conversa)

    agente = _do_agente(r)
    assert "pediu_pessoa" in agente["acoes_pessoa"] and r.precisa_humano, agente
    assert motivo in (r.motivo or ""), r.motivo
    assert envios == []


async def test_c_pedir_de_novo_com_pergunta_nova_o_agente_responde(
    db: AsyncSession, make_user, agente_ligado, api, envios, redis_mem, lojas
):
    conversa = await _pote(db, make_user, redis_mem, lojas)
    await _no_canal(db, conversa, MODO_AUTO)
    await _msg(db, conversa, "quero falar com um atendente", ha=timedelta(minutes=30))
    await _msg(
        db, conversa, EM_QUE_AJUDAR, autor="loja", origem=ORIGEM_IA, ha=timedelta(minutes=29)
    )
    await _msg(db, conversa, "atendente, o pote de 2,5 L vai voltar?", ha=timedelta(minutes=6))
    api.roteiro = [
        _usa("ver_anuncio", item_id=ITEM_POTE),
        _final(texto=RESPOSTA_DO_POTE, assunto="estoque_variacao", evidencias=[VAR_POTE]),
    ]

    r = await ia.gerar_rascunho(db, conversa)

    assert _do_agente(r)["acoes_pessoa"] == [] and not r.precisa_humano, r.motivo
    assert [e["texto"] for e in envios] == [RESPOSTA_DO_POTE]


async def test_c_a_resposta_generica_nao_resolve_a_pergunta_que_veio_junto(
    db: AsyncSession, make_user, agente_ligado, api, envios, redis_mem, lojas
):
    """O assunto é o da pergunta (decisão do dono): "atendente, o pote vai voltar?"
    respondido com "em que posso ajudar?" declarado `pede_atendente` não sai."""
    conversa = await _pote(db, make_user, redis_mem, lojas)
    await _no_canal(db, conversa, MODO_AUTO)
    await _msg(db, conversa, "atendente, o pote de 2,5 L vai voltar?", ha=timedelta(minutes=6))
    api.roteiro = [_final(texto="Olá! Em que posso ajudar?", assunto="pede_atendente")]

    r = await ia.gerar_rascunho(db, conversa)

    assert _do_agente(r)["acoes_pessoa"] == []
    assert r.precisa_humano and "há pergunta concreta pendente" in (r.motivo or ""), r.motivo
    assert envios == []


# ─────────────── (e) "é robô?" ───────────────


@pytest.mark.parametrize(
    "aceita",
    # revisão de 21/10/2026: as palavras mais naturais também aceitam a oferta
    ["quero falar com a pessoa", "uma pessoa por favor", "humano"],
)
async def test_e_e_robo_a_resposta_honesta_sai_e_o_pedido_da_pessoa_depois_vai_para_pessoa(
    db: AsyncSession, make_user, agente_ligado, api, envios, redis_mem, lojas, aceita
):
    conversa = await _pote(db, make_user, redis_mem, lojas)
    await _no_canal(db, conversa, MODO_AUTO)
    await _msg(db, conversa, "é robô?", ha=timedelta(minutes=6))
    api.roteiro = [_final(texto=HONESTA, assunto="pede_atendente")]

    r = await ia.gerar_rascunho(db, conversa)

    assert _do_agente(r)["acoes_pessoa"] == [] and not r.precisa_humano, r.motivo
    assert r.status == RASCUNHO_PENDENTE and r.validador_ok, r.validador_erros
    assert [e["texto"] for e in envios] == [HONESTA]

    # A resposta chegou ao cliente (o envio aqui é o espião: a mensagem da loja
    # entra como a plataforma a devolveria) e ele pede a pessoa oferecida →
    # pessoa pela régua do CÓDIGO.
    await _msg(db, conversa, HONESTA, autor="loja", origem=ORIGEM_IA, ha=timedelta(minutes=2))
    await _msg(db, conversa, aceita, ha=timedelta(minutes=1))
    api.roteiro = [
        _final(
            texto="",
            acao="pessoa",
            acao_pessoa="pediu_pessoa",
            motivo_pessoa="o cliente pediu a pessoa oferecida",
            assunto="pede_atendente",
        )
    ]
    r2 = await ia.gerar_rascunho(db, conversa)

    assert "pediu_pessoa" in _do_agente(r2)["acoes_pessoa"] and r2.precisa_humano, r2.motivo
    assert acao_pessoa.MOTIVO_ACEITOU_A_PESSOA in (r2.motivo or ""), r2.motivo
    assert len(envios) == 1


# ─────────────── (f) o texto que se diz pessoa ───────────────


@pytest.mark.parametrize("modo", [MODO_AUTO, MODO_COPILOTO])
@pytest.mark.parametrize(
    ("pergunta", "texto", "erro"),
    [
        (
            "vc é humano?",
            "Olá! Não sou robô, sou da equipe da loja. Em que posso ajudar?",
            "diz que é uma pessoa",
        ),
        # revisão de 21/10/2026: a negação mais provável não diz "sou humano"
        (
            "é robô?",
            "Olá! Não, aqui é a equipe da loja. Em que posso ajudar?",
            "nega que é o atendimento virtual",
        ),
        ("é robô?", "Sou da equipe da loja, não um robô.", "diz que é uma pessoa"),
    ],
    ids=["nao_sou_robo", "nao_aqui_e_a_equipe", "nao_um_robo"],
)
async def test_f_o_texto_que_se_diz_pessoa_e_bloqueado(
    db: AsyncSession,
    make_user,
    agente_ligado,
    api,
    envios,
    redis_mem,
    lojas,
    modo,
    pergunta,
    texto,
    erro,
):
    conversa = await _pote(db, make_user, redis_mem, lojas)
    await _no_canal(db, conversa, modo)
    await _msg(db, conversa, pergunta, ha=timedelta(minutes=6))
    api.roteiro = [_final(texto=texto, assunto="pede_atendente")]

    r = await ia.gerar_rascunho(db, conversa)

    agente = _do_agente(r)
    assert checagens.SE_DIZ_HUMANO in agente["checagens"], agente["checagens"]
    assert r.status == RASCUNHO_BLOQUEADO and r.precisa_humano, r.motivo
    assert any(erro in e for e in r.validador_erros), r.validador_erros
    assert envios == []


async def test_f_perguntado_se_e_robo_o_texto_que_foge_nao_sai_sozinho(
    db: AsyncSession, make_user, agente_ligado, api, envios, redis_mem, lojas
):
    """Fugir da pergunta "é robô?" sem dizer que é o atendimento virtual é SUAVE: no
    automático não sai sozinho (com o aviso); não bloqueia."""
    conversa = await _pote(db, make_user, redis_mem, lojas)
    await _no_canal(db, conversa, MODO_AUTO)
    await _msg(db, conversa, "é robô?", ha=timedelta(minutes=6))
    texto = "Olá! Sou do atendimento da loja. Em que posso ajudar?"
    api.roteiro = [_final(texto=texto, assunto="pede_atendente")]

    r = await ia.gerar_rascunho(db, conversa)

    agente = _do_agente(r)
    assert checagens.NAO_DISSE_QUE_E_VIRTUAL in agente["checagens"], agente["checagens"]
    assert r.status != RASCUNHO_BLOQUEADO and r.texto == texto, (r.status, r.motivo)
    assert envios == []


# ─────────────── (g) o legado ───────────────


async def test_g_sem_o_agente_o_pedido_de_atendente_continua_pessoa(
    db: AsyncSession, make_user, agente_ligado, api, envios, redis_mem, lojas
):
    """O canal sem o botão do agente: o legado (Groq) e a régua de sempre — no
    copiloto, "cliente pediu atendente" manda para pessoa."""
    conversa = await _pote(db, make_user, redis_mem, lojas)
    await _ligar(db, conversa, modo=MODO_COPILOTO, agente_no_canal=False)
    await _msg(db, conversa, "quero falar com um atendente", ha=timedelta(minutes=6))
    saidas = iter(
        [
            '{"categoria": "pede_atendente"}',
            json.dumps(
                {
                    "categoria": "pede_atendente",
                    "precisa_humano": False,
                    "confianca": 0.9,
                    "resposta": "Olá! Em que posso ajudar?",
                    "motivo": "ok",
                }
            ),
        ]
    )
    with respx.mock(assert_all_called=True) as router:
        router.post("https://llm.invalid/v1/chat/completions").mock(
            side_effect=lambda request: httpx.Response(
                200,
                json={
                    "model": "modelo-configurado",
                    "choices": [{"message": {"content": next(saidas)}}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 3},
                },
            )
        )
        r = await ia.gerar_rascunho(db, conversa)

    assert api.pedidos == [] and r.prompt_versao != PROMPT_VERSAO_AGENTE
    assert r.precisa_humano and "cliente pediu atendente" in (r.motivo or ""), r.motivo
    assert envios == []
