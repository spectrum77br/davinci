# ruff: noqa: E501, F401, F811 — as frases da revisão ficam inteiras; fixtures importadas
"""A revisão do r5i (21/10/2026): os 11 achados sobre a integração do eval real.

1. O parêntese das variações (`variacoes._polaridade`): o parêntese logo depois
   da citação é dela ("a Preto (disponível)"); só o que cita OUTRA variação é
   outra oração (o a6ff). A contradição de estoque volta a pegar.
2. O caminho para fora: "a loja … na Shopee" só passa com o CANAL do app logo
   depois de "loja"; a recusa da retirada com exceção ("hoje não, amanhã sim")
   continua caminho para fora.
3. Os números: as formas soltas só dos textos de PRODUTO; nunca do dinheiro
   (R$ 349,90 → 90), da hora do cabeçalho nem do estado da conversa.
4. A ficha no automático: o termo que o dado só traz NEGADO não é do dado.
5. "Direto com a loja pelo app": o motivo "negociação fora da plataforma"
   volta no agente (o envio revalidava e recusava); o prompt manda escrever
   "só pelo app da plataforma".
6. O prompt diz de onde vieram os padrões do `_RESPONDA` (o resumo das 80).
7. A garantia Uranyx (decisões 6 a 8 do dono): 1 ano, 90 dias com a loja e 9
   meses direto com a Uranyx; o caminho "pelo app Uranyx ou pelo site
   uranyx.com.br/sac" só depois dos 90 dias (ou a regra antes da compra), no
   validador, na checagem, no verificador e no envio — só no agente; nunca
   "homologado".
8. A condição só pelo TÍTULO: as frases de exemplo passam no evidências e no
   validador.
9. O cancelamento sem a negativa: "não é possível" e "não permite" avisam como
   o "não dá para"; a parte do pedido ("um item") fica de fora.
10. O "analisa e responde por aqui" do pedido de vídeo no `VALE_SEMPRE`, no
    `_ESTILO` e no `_GARANTIA`.
11. A troca: não postado, postado e por defeito; a "compra nova" para quem
    relata defeito vira aviso.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AtendimentoFato, AtendimentoRascunho, Logistica
from app.services.atendimento import enviar, evidencias, ia, validador
from app.services.atendimento.agente import checagens, ferramentas, pacote, prompt, verificador
from app.services.atendimento.agente import contexto as agente_ctx
from app.services.atendimento.agente.contexto import ContextoAgente
from app.services.atendimento.agente.saida import Proposta
from app.services.atendimento.constantes import (
    MODO_AUTO,
    MODO_COPILOTO,
    RASCUNHO_BLOQUEADO,
    RASCUNHO_DESCARTADO,
    RASCUNHO_PENDENTE,
)
from tests.test_atendimento_agente_ferramentas import _base as _base_f
from tests.test_atendimento_agente_ferramentas import _bling, _chamar, _ctx
from tests.test_atendimento_agente_furos import _completo, _sai
from tests.test_atendimento_conferir_saida import _base as _base_conferir
from tests.test_atendimento_conferir_saida import _chamada
from tests.test_atendimento_enviar import (
    _cenario,
    _mensagens_da_loja,
    _recusa,
    ligado,
    plataforma,
)
from tests.test_atendimento_ia import _conversa, _msg

# ═══════════════ 1. o parêntese das variações ═══════════════

PRETO_ESGOTADO_PRATA_DISPONIVEL = {
    "item_id": "1",
    "titulo": "Relógio Inteligente",
    "variacoes": [
        {"nome": "Preto", "disponivel": False, "confiavel": True, "fonte": "loja"},
        {"nome": "Prata", "disponivel": True, "confiavel": True, "fonte": "loja"},
    ],
    "variacoes_completas": True,
}


@pytest.mark.parametrize(
    "texto",
    [
        # "tem" o que está esgotado
        "A Preto (disponível) e a Prata (disponível) podem ser compradas.",
        "Sim, a Preto (em estoque) pode ser selecionada no anúncio.",
        # "não tem" o que está disponível
        "A Prata (esgotada) e a Preta estão no anúncio.",
        "Temos a Prata (no momento está esgotada).",
    ],
)
def test_1_o_parentese_logo_depois_da_citacao_e_dela(texto):
    """Na HEAD 008e90fa as quatro eram contradição; o r5i as soltava (o "(" cortava).
    A contradição vai para PESSOA nos dois modos (rodada 4: sem bloquear)."""
    achadas = checagens.conferir_variacoes(
        texto, [PRETO_ESGOTADO_PRATA_DISPONIVEL], ["tem a preta?"]
    )
    assert [p.codigo for p in achadas] == [checagens.VARIACAO_CONTRADIZ], achadas
    assert not achadas[0].bloqueia and not achadas[0].so_auto


@pytest.mark.parametrize(
    "texto",
    [
        "A Prata (disponível) e a Preto (esgotada) estão no anúncio.",
        "Temos a Prata e a Preto (esgotada).",
        "Temos a Prata (a Preto está esgotada).",
        "A Preto (não está disponível) e a Prata sim.",
        "A Prata (não está esgotada) pode ser comprada.",
        "A Prata (cor clássica) está disponível.",
    ],
)
def test_1_o_parentese_que_bate_com_o_dado_passa(texto):
    assert (
        checagens.conferir_variacoes(texto, [PRETO_ESGOTADO_PRATA_DISPONIVEL], ["tem a preta?"])
        == []
    )


def test_1_o_parentese_que_cita_outra_variacao_continua_outra_oracao():
    """O dev a6ff (o r5i) continua: o "esgotados" de dentro não é da Prata."""
    from tests.test_atendimento_agente_dados_reais import RELOGIO_DO_DEV, RESPOSTA_A6FF

    assert checagens.conferir_variacoes(RESPOSTA_A6FF, [RELOGIO_DO_DEV], ["42 ou 46mm?"]) == []


# ═══════════════ 2. o caminho para fora ═══════════════


@pytest.mark.parametrize(
    "texto",
    [
        # a OUTRA loja na plataforma, com uma palavra no meio que não é o canal
        "Você encontra o modelo azul na loja pelo nome Uranyx na Shopee.",
        "Procure a loja com nome Uranyx na Shopee que lá tem o azul.",
        "Temos a loja em promoção Uranyx na Shopee com esse modelo.",
        # a recusa da retirada com exceção convida a vir
        "Não é possível vir buscar hoje, mas amanhã sim, das 9h às 18h.",
        "A retirada na loja não está disponível hoje, só amanhã.",
        "No momento não fazemos retirada na loja.",
    ],
)
def test_2_o_caminho_para_fora_que_o_r5i_soltava_volta_a_bloquear(texto):
    (p,) = checagens.conferir_caminho_da_plataforma(texto, "shopee")
    assert p.codigo == checagens.CAMINHO_FORA_DA_PLATAFORMA and p.bloqueia
    # O validador do legado não pega nenhuma (a checagem é a única trava).
    assert validador.validar(texto, plataforma="shopee", canal="chat", origem="davinci_ia") in (
        [],
        ["prazo em números só pode vir do sistema"],
    )


@pytest.mark.parametrize(
    "texto",
    [
        "O atendimento é feito pela loja pelo próprio app da Shopee.",
        "A troca é acompanhada pela loja dentro do app da Shopee.",
        "A compra deve ser feita na loja através do app da Shopee.",
        "Pode falar com a loja pelo chat da Shopee.",
        "Não fazemos retirada na loja, a compra e a entrega são só pelo app da Shopee.",
    ],
)
def test_2_o_canal_do_app_depois_de_loja_continua_passando(texto):
    assert checagens.conferir_caminho_da_plataforma(texto, "shopee") == []


# ═══════════════ 3. os números ═══════════════


def _ctx_puro(modo: str = MODO_AUTO) -> ContextoAgente:
    conversa = SimpleNamespace(
        id=None,
        plataforma="shopee",
        canal="chat",
        pedido_marketplace=None,
        anuncio_id=None,
        dados={},
    )
    return ContextoAgente.da_conversa(conversa, None, modo=modo)


def test_3_o_preco_nao_libera_os_centavos():
    ctx = _ctx_puro()
    ctx.permitir(textos=['{"preco": "R$ 349,90"}'], produto=True)
    ctx.permitir(textos=['{"valor": "349,90"}'])
    assert ia.numeros_inventados(
        "Ainda restam 90 unidades.", "\n".join(ctx.permitidos_extra())
    ) == ["90"]
    # O preço inteiro continua visto (a regra do valor é outra).
    assert ia.numeros_inventados("Custa 349,90.", "\n".join(ctx.permitidos_extra())) == []
    assert agente_ctx.numeros_soltos(['{"preco": "R$ 349,90", "x": "1.299,90"}']) == ""


def test_3_as_formas_soltas_so_do_produto():
    ctx = _ctx_puro()
    # O pedido (datas, valores) entra inteiro, sem as formas soltas.
    ctx.permitir(textos=['{"data_da_compra": "05/09/2026", "nf": "16.128"}'])
    assert ia.numeros_inventados("Tem 16 GB de RAM.", "\n".join(ctx.permitidos_extra())) == ["16"]
    ctx.permitir(textos=['{"titulo": "Uranyx A18 16.128 Gb"}'], produto=True)
    assert (
        ia.numeros_inventados("Tem 16 GB de RAM e 128 GB.", "\n".join(ctx.permitidos_extra())) == []
    )


async def test_3_o_cabecalho_e_o_estado_nao_liberam_numero(db: AsyncSession, make_user):
    """O minuto 35 do cabeçalho liberava "Temos 35 unidades" (na HEAD, inventado)."""
    conversa = await _conversa(db, make_user, pedido=None)
    conversa.anuncio_id = None
    await db.commit()
    m = await _msg(db, conversa, "tem o azul?")
    base = await _base_f(db, conversa, recentes=[m])
    ctx = _ctx(conversa, base)
    # O prazo de resposta (derivado da mensagem) no minuto 35: 14:35 em São Paulo.
    conversa.prazo_resposta_em = datetime(2026, 10, 21, 17, 35, tzinfo=UTC)
    pac = await pacote.montar_pacote(db, conversa, m, base, ctx)
    assert "14:35" in pac.texto
    assert not any("14:35" in t for t in ctx.permitidos_extra())
    assert ia.numeros_inventados(
        "Temos 35 unidades em estoque.", "\n".join(ctx.permitidos_extra())
    ) == ["35"]


# ═══════════════ 4. a ficha no automático: o termo negado no dado ═══════════════

CELULAR_SEM_DIGITAL = [
    {
        "item_id": "1",
        "titulo": "Celular X",
        "descricao": "Não tem leitor de digital. Câmera simples, sem estabilização.",
        "variacoes": [
            {"nome": "Azul", "disponivel": True, "confiavel": True},
            {"nome": "Preta", "disponivel": True, "confiavel": True},
        ],
        "variacoes_completas": True,
    }
]


@pytest.mark.parametrize(
    "texto",
    [
        "Temos a azul disponível, e ela tem leitor de digital.",
        "Temos a preta, e a câmera tem estabilização.",
    ],
)
def test_4_o_que_o_dado_nega_nao_e_do_dado(texto):
    (p,) = checagens.conferir_ficha_no_automatico(
        texto, CELULAR_SEM_DIGITAL, textos_cliente=["tem a azul?"], assunto="estoque_variacao"
    )
    assert p.codigo == checagens.CARACTERISTICA_FORA_DO_ANUNCIO and p.suave


@pytest.mark.parametrize(
    "texto",
    [
        "Temos a azul disponível.",
        "Temos a azul, e a câmera não tem estabilização.",
        "Temos a azul; ela não tem leitor de digital.",
        "Temos a azul, e a câmera é simples.",
    ],
)
def test_4_o_que_bate_com_o_dado_passa(texto):
    assert (
        checagens.conferir_ficha_no_automatico(
            texto, CELULAR_SEM_DIGITAL, textos_cliente=["tem a azul?"], assunto="estoque_variacao"
        )
        == []
    )


def test_4_o_termo_curto_negado_no_dado():
    """O termo do vocabulário (NFC, 5G) que o dado só traz negado: o "termo in
    dado" de antes dava como trazido."""
    ficha = [
        {
            "item_id": "1",
            "titulo": "Celular X",
            "descricao": "Não tem NFC. Não é 5G.",
            "variacoes": [{"nome": "Azul", "disponivel": True, "confiavel": True}],
            "variacoes_completas": True,
        }
    ]
    for texto, termo in (
        ("Temos a azul, e ela tem NFC.", "nfc"),
        ("Temos a azul, e ela é 5G.", "5g"),
    ):
        assert termo in checagens.caracteristicas_fora_do_dado(texto, ficha), texto
    assert checagens.caracteristicas_fora_do_dado("Temos a azul; ela não tem NFC.", ficha) == []


def test_4_o_atributo_negado_e_a_linha_da_descricao():
    ficha = [
        {
            "item_id": "1",
            "titulo": "Celular X",
            "atributos": ["Carregador: Não"],
            "descricao": "Características:\nNão acompanha fone\nNFC\nCâmera 50MP",
        }
    ]
    # A linha "NFC" sozinha afirma (a quebra de linha separa da negação de cima).
    assert checagens.caracteristicas_fora_do_dado("Temos a azul, e ela tem NFC.", ficha) == []
    assert "carregador" in checagens.caracteristicas_fora_do_dado(
        "Temos a azul, e vem com carregador.", ficha
    )


# ═══════════════ 5. "direto com a loja" continua negociação por fora ═══════════════


async def _as_duas(texto: str) -> tuple[ia.SaidaConferida, ia.SaidaConferida]:
    conversa, kw = _chamada(
        _base_conferir(texto=texto, categoria="outro", textos_cliente=["posso retirar aí?"])
    )
    legado = await ia.conferir_saida(None, conversa, SimpleNamespace(), **kw)
    agente = await ia.conferir_saida(None, conversa, SimpleNamespace(), **kw, agente=True)
    return legado, agente


@pytest.mark.parametrize(
    "texto",
    [
        "A compra é feita direto com a loja pelo próprio app da Shopee.",
        "Feche direto com a loja no chat que fazemos um preço especial.",
        "Pode comprar direto conosco pelo chat da Shopee, aí te passo a chave para pagar.",
        "Não fazemos venda por fora, mas fechando direto com a loja no chat sai por um valor menor.",
    ],
)
async def test_5_o_agente_mantem_a_negociacao_por_fora_como_o_envio(texto):
    legado, agente = await _as_duas(texto)
    assert validador.MOTIVO_POR_FORA in legado.erros
    assert validador.MOTIVO_POR_FORA in agente.erros
    # O envio (qualquer origem) recusaria do mesmo jeito: nada passa só no agente.
    for origem in ("davinci_ia", "davinci_humano", "davinci_auto"):
        assert validador.MOTIVO_POR_FORA in validador.validar(
            texto, plataforma="shopee", canal="chat", origem=origem
        )


def test_5_o_prompt_manda_a_frase_que_o_validador_aceita():
    secao = prompt.bloco_geral()
    assert 'nunca "direto com a loja/conosco" nem "por fora"' in secao
    certo = (
        "Não fazemos retirada na loja: a compra, o pagamento e a entrega são só pelo app da Shopee."
    )
    for origem in ("davinci_ia", "davinci_humano"):
        assert validador.validar(certo, plataforma="shopee", canal="chat", origem=origem) == []
    assert checagens.conferir_caminho_da_plataforma(certo, "shopee") == []
    assert not hasattr(checagens, "negociacao_fora_so_aparente")


# ═══════════════ 6. de onde vieram os padrões do prompt ═══════════════


def test_6_o_prompt_diz_que_os_padroes_vieram_do_resumo_das_80():
    doc = prompt.__doc__ or ""
    assert "resumo das 80" in doc and "split TESTE" in doc
    assert "amostra nova" in doc


# ═══════════════ 7. a garantia Uranyx (decisões 6 a 8 do dono) ═══════════════

DIA = date(2026, 10, 21)
CAMINHO = "pelo app Uranyx ou pelo site uranyx.com.br/sac"


def test_7_os_periodos_da_garantia_uranyx():
    assert ferramentas.periodos_uranyx("2026-05-20", "2027-05-20", DIA) == {
        "fim_loja": "2026-08-18",
        "fim_uranyx": "2027-05-20",
        "periodo": "uranyx",
    }
    assert ferramentas.periodos_uranyx("2026-09-01", "2027-09-01", DIA)["periodo"] == "loja"
    # o último dia dos 90 ainda é da loja
    assert ferramentas.periodos_uranyx(date(2026, 7, 23), None, DIA)["periodo"] == "loja"
    assert ferramentas.periodos_uranyx(date(2026, 7, 22), None, DIA)["periodo"] == "uranyx"
    assert ferramentas.periodos_uranyx("2025-09-01", "2026-09-01", DIA)["periodo"] == "expirada"
    assert ferramentas.periodos_uranyx(None, None, DIA) == {
        "fim_loja": None,
        "fim_uranyx": None,
        "periodo": None,
    }
    regra = ferramentas.REGRA_URANYX
    assert (regra["garantia_anos"], regra["loja_dias"], regra["uranyx_meses"]) == (1, 90, 9)
    assert regra["caminho_depois_da_loja"] == CAMINHO


def _garantia(periodo: str | None, **mais: Any) -> dict:
    inicio = {"uranyx": "2026-05-20", "loja": "2026-09-01", None: None}.get(periodo, "2025-09-01")
    g = {
        "status": {"uranyx": "somente_software", "loja": "ativa", None: None}.get(
            periodo, "expirada"
        ),
        "inicio": inicio,
        "fim_hardware": None,
        "fim_software": None,
        "regra": dict(ferramentas.REGRA_URANYX),
        "produto": "Celular Uranyx A18",
    }
    if inicio is not None:
        g.update(ferramentas.periodos_uranyx(inicio, None, DIA))
    g.update(mais)
    return g


async def _completo_com_garantia(
    texto: str, *, garantias: list[dict], modo: str, cliente: str, categoria: str = "garantia"
) -> ia.SaidaConferida:
    """O `_completo` do furos com o `ctx.garantias_conferidas` que o `ver_garantia`
    deixaria (a checagem do caminho lê de lá)."""
    conversa, kw = _chamada(
        _base_conferir(
            texto=texto,
            categoria=categoria,
            modo=modo,
            textos_cliente=[cliente],
            textos_rajada=[cliente],
            garantias_conferidas=garantias,
            garantia_consultada=True,
        )
    )
    conferida = await ia.conferir_saida(
        None,
        conversa,
        SimpleNamespace(),
        **kw,
        agente=True,
        permitidos_extra=[json.dumps({"garantias": garantias})],
    )
    ctx = ContextoAgente.da_conversa(conversa, None, modo=modo)
    ctx.garantias_conferidas = list(garantias)
    base = SimpleNamespace(
        textos_rajada=[cliente],
        textos_cliente=[cliente],
        fatos=kw["fatos"],
        recentes=[],
        ctx=kw["ctx"],
    )
    proposta = Proposta(
        acao="responder",
        texto=texto,
        assunto=categoria,
        confianca=0.9,
        acao_pessoa=None,
        motivo_pessoa="",
        evidencias=[],
    )
    chk = checagens.conferir(proposta, conferida, ctx, base, fichas=[], gatilho=None)
    return checagens.aplicar(conferida, chk, acoes_pessoa=[], modo=modo)


def test_7_o_caminho_da_uranyx_so_depois_dos_90_dias_ou_antes_da_compra():
    assert checagens.caminho_uranyx_liberado([_garantia("uranyx")])
    assert checagens.caminho_uranyx_liberado([{"status": None, "regra": ferramentas.REGRA_URANYX}])
    assert not checagens.caminho_uranyx_liberado([_garantia("loja")])
    assert not checagens.caminho_uranyx_liberado([_garantia("expirada")])
    # Sem data de entrega depois da compra: a regra sem o caminho.
    assert not checagens.caminho_uranyx_liberado(
        [{"status": "entregue_sem_data", "regra": ferramentas.REGRA_URANYX}]
    )
    assert not checagens.caminho_uranyx_liberado(
        [{"status": None, "regra": ferramentas.REGRA_URANYX, "antes_da_compra": False}]
    )
    # A Charlots e a garantia legal: nunca.
    assert not checagens.caminho_uranyx_liberado([{"status": None, "regra": {"dias": 90}}])
    assert not checagens.caminho_uranyx_liberado([{"status": None, "regra": {"legal": True}}])
    assert not checagens.caminho_uranyx_liberado(None)


DEPOIS_DOS_90 = (
    "Olá! Como já passaram os 90 dias com a loja, a garantia do seu aparelho segue direto com a "
    "Uranyx, pelo app Uranyx ou pelo site uranyx.com.br/sac."
)
A_REGRA = (
    "Olá! Os aparelhos Uranyx têm 1 ano de garantia a partir da entrega: os 90 primeiros dias "
    "com a loja e os 9 meses seguintes direto com a Uranyx."
)


@pytest.mark.parametrize("texto", [DEPOIS_DOS_90, A_REGRA])
async def test_7_a_garantia_uranyx_depois_dos_90_dias_passa_so_no_agente(texto):
    for modo in (MODO_COPILOTO, MODO_AUTO):
        final = await _completo_com_garantia(
            texto,
            modo=modo,
            cliente="meu celular parou de carregar, tem garantia?",
            garantias=[_garantia("uranyx")],
        )
        assert final.status == RASCUNHO_PENDENTE and not final.erros, (final.motivos, final.erros)
        assert not any("fora da plataforma" in m for m in final.motivos), final.motivos
        # O caminho da Uranyx é a orientação do período, mesmo com o relato do
        # defeito: sem aviso, e sai sozinho no automático (o 1º teste da
        # orientação do defeito marcava "fora da orientação").
        assert not final.avisos and not final.sem_auto, (final.avisos, final.sem_auto)
        if modo == MODO_AUTO:
            assert _sai(final)
    # O legado (sem as garantias do agente) barra o link e o prazo.
    conversa, kw = _chamada(_base_conferir(texto=texto, categoria="garantia"))
    legado = await ia.conferir_saida(None, conversa, SimpleNamespace(), **kw)
    assert legado.erros


async def test_7_dentro_dos_90_dias_o_caminho_da_uranyx_continua_barrado():
    final = await _completo_com_garantia(
        DEPOIS_DOS_90,
        modo=MODO_COPILOTO,
        cliente="meu celular parou de carregar",
        garantias=[_garantia("loja")],
    )
    assert final.status == RASCUNHO_BLOQUEADO
    assert "link" in final.erros or any("fora da plataforma" in e for e in final.erros)


@pytest.mark.parametrize(
    "texto",
    [
        # outro canal da Uranyx
        "Olá! A garantia segue direto com a Uranyx: chame no WhatsApp da Uranyx ou acesse uranyx.com.br.",
        "Olá! Acione a garantia pelo site uranyx.com.br/suporte.",
        # o caminho certo + outro link
        "Olá! A garantia segue pelo app Uranyx ou pelo site uranyx.com.br/sac, ou em www.exemplo.com.",
    ],
)
async def test_7_so_a_frase_exata_passa(texto):
    final = await _completo_com_garantia(
        texto, modo=MODO_COPILOTO, cliente="meu celular parou", garantias=[_garantia("uranyx")]
    )
    assert final.erros, final.motivos


def test_7_os_prazos_da_regra_de_1_ano():
    prazos = validador.prazos_da_regra([_garantia("uranyx")])
    assert (
        validador.validar(
            A_REGRA,
            plataforma="shopee",
            canal="chat",
            origem="davinci_ia",
            prazos_de_garantia=prazos,
        )
        == []
    )
    # O "3 meses" do aparelho Uranyx (a fala antiga) continua barrado sem o par.
    assert validador.validar(
        "A garantia do aparelho Uranyx é de 3 meses.",
        plataforma="shopee",
        canal="chat",
        origem="davinci_ia",
        prazos_de_garantia=prazos,
    )
    # Os 90 dias só com a loja; os 9 meses só com a Uranyx.
    assert validador.validar(
        "A garantia é de 9 meses com a loja.",
        plataforma="shopee",
        canal="chat",
        origem="davinci_ia",
        prazos_de_garantia=prazos,
    )
    # As datas dos períodos são do bloco.
    g = _garantia("uranyx")
    assert (
        validador.conferir_garantia(
            "A garantia com a loja foi até 18/08/2026; com a Uranyx vai até 20/05/2027.",
            [g],
            prazos_de_garantia=prazos,
        )
        == []
    )


def test_7_nunca_homologado():
    (p,) = checagens.conferir_homologado(
        "Aparelhos Uranyx homologados pela Uranyx têm 1 ano de garantia."
    )
    assert p.codigo == checagens.HOMOLOGADO and p.bloqueia
    assert checagens.conferir_homologado("O aparelho é homologado pela Anatel.") == []
    assert checagens.conferir_homologado(A_REGRA) == []
    assert 'nunca "homologado"' in prompt.bloco_geral()


def test_7_o_verificador_conhece_a_excecao():
    assert verificador.VERSAO == "v5"
    assert '"pelo app Uranyx ou pelo site uranyx.com.br/sac", exatamente assim' in (
        verificador.SISTEMA
    )
    assert 'o "periodo" "uranyx"' in verificador.SISTEMA


def test_7_o_prompt_da_garantia_uranyx():
    geral = prompt.bloco_geral()
    garantia = geral[geral.index("# GARANTIA") : geral.index("# ORIGEM")]
    assert 'Pelo "periodo": "loja" (até "fim_loja"), como acionar abaixo' in garantia
    assert (
        '"uranyx" (até "fim_uranyx"), direto com a Uranyx, pelo app Uranyx ou pelo site uranyx.com.br/sac (exatamente assim)'
        in garantia
    )
    assert "nunca os meses de hardware e software" in garantia
    assert "Exceção única: o caminho da Uranyx da GARANTIA." in geral
    # O prompt não traz prazo em número (só o que o ver_garantia devolve):
    # a orientação diz a regra pelos campos dela.
    assert (
        "garantia_anos a partir da entrega, os loja_dias primeiros com a loja"
        in prompt.ORIENTACAO["garantia"]
    )
    assert "os uranyx_meses seguintes direto com a Uranyx" in prompt.ORIENTACAO["garantia"]
    assert "a partir da entrega" in ferramentas.DESCRICOES[ferramentas.VER_GARANTIA]


async def test_7_ver_garantia_traz_os_periodos_e_os_fatos(db: AsyncSession, make_user):
    conversa = await _conversa(db, make_user, pedido="251001URANYX01")
    await _bling(db, "300001", "251001URANYX01")
    entrega = datetime.now(UTC) - timedelta(days=150)
    db.add(
        Logistica(
            pedido_bling="300001",
            pedido_marketplace="251001URANYX01",
            rastreio="AA123456789BR",
            entregue_em=entrega,
            cliente_nome="Fulano",
        )
    )
    fatos = [
        AtendimentoFato(
            chave=chave,
            escopo="marca",
            marca="uranyx",
            assunto="garantia",
            texto=texto,
            fonte="dono",
            ativo=True,
            aprovado_por="Eduardo (chat 21/10/2026)",
        )
        for chave, texto in zip(
            ferramentas.CHAVES_GARANTIA_URANYX,
            (
                "Aparelhos Uranyx homologados pela Uranyx têm 1 ano de garantia.",
                "Depois dos 90 primeiros dias, pelo app Uranyx ou pelo site uranyx.com.br/sac.",
            ),
            strict=True,
        )
    ]
    db.add_all(fatos)
    await db.commit()
    base = await _base_f(db, conversa)
    ctx = _ctx(conversa, base)
    dados = await _chamar(ctx, base, conversa, "ver_garantia", numero=None)
    assert dados["linha"] == "uranyx" and dados["periodo"] == "uranyx"
    inicio = date.fromisoformat(dados["inicio"])
    assert dados["fim_loja"] == (inicio + timedelta(days=90)).isoformat()
    assert dados["fim_uranyx"] == dados["fim_software"]
    assert dados["regra"]["caminho_depois_da_loja"] == CAMINHO
    ids = [f"fato:{f.id}" for f in fatos]
    assert dados["regra"]["fatos"] == ids and set(ids) <= set(dados["evidencias"])
    # O texto do fato ("homologados") não vai na regra.
    assert "homologad" not in json.dumps(dados, ensure_ascii=False)
    (conferida,) = ctx.garantias_conferidas
    assert conferida["periodo"] == "uranyx" and conferida["fim_loja"] == dados["fim_loja"]
    assert checagens.caminho_uranyx_liberado(ctx.garantias_conferidas)


async def test_7_o_envio_aceita_o_caminho_so_no_rascunho_do_agente_marcado(
    db: AsyncSession, make_user, ligado, plataforma
):
    """O link barrava o envio de qualquer origem: só o rascunho `ag1` com a marca
    `isencoes` passa, e só com a frase exata."""
    conversa, user = await _cenario(db, make_user)
    texto = DEPOIS_DOS_90

    async def _rascunho(isencoes: list[str] | None) -> AtendimentoRascunho:
        r = AtendimentoRascunho(
            conversa_id=conversa.id,
            texto=texto,
            categoria="garantia",
            status="pendente",
            precisa_humano=True,
            validador_ok=True,
            prompt_versao="ag1",
            fatos={"agente": {"isencoes": isencoes}} if isencoes is not None else {},
        )
        db.add(r)
        await db.commit()
        await db.refresh(r)
        return r

    sem_marca = await _rascunho(None)
    e = await _recusa(
        enviar.enviar_resposta(db, conversa, texto, user=user, rascunho_id=sem_marca.id)
    )
    assert e.code == "texto_invalido" and "link" in e.detail
    sem_marca.status = RASCUNHO_DESCARTADO  # um pendente por conversa
    await db.commit()
    # Outro link junto: recusa mesmo com a marca.
    marcado = await _rascunho([checagens.ISENCAO_CAMINHO_URANYX])
    e = await _recusa(
        enviar.enviar_resposta(
            db, conversa, texto + " Veja também www.exemplo.com.", user=user, rascunho_id=marcado.id
        )
    )
    assert e.code == "texto_invalido" and "link" in e.detail
    # Sem rascunho (a pessoa escrevendo do zero): recusa.
    e = await _recusa(enviar.enviar_resposta(db, conversa, texto, user=user))
    assert e.code == "texto_invalido"
    # O rascunho marcado, com a frase exata: sai.
    m = await enviar.enviar_resposta(db, conversa, texto, user=user, rascunho_id=marcado.id)
    assert m.texto == texto and plataforma.chamadas == [texto]


# ═══════════════ 8. a condição só pelo título ═══════════════

APPLE_WATCH = {
    "ficha_do_anuncio": [
        {
            "item_id": "22299473628",
            "titulo": "Apple Watch Series 11 - 42mm ou 46mm - 1 Ano de Garantia Apple",
            "descricao": "Produto novo e original, lacrado.",
            "variacoes": [{"nome": "Prata, 46mm NOVO", "disponivel": True, "confiavel": True}],
        }
    ]
}


def test_8_a_frase_do_prompt_passa_e_as_que_ele_proibe_nao():
    produto = prompt.bloco_geral()
    # Fontes v1.3, item 10 (dono, 21/10/2026): no agente, a condição vem da
    # "condicao" da ficha (título, atributo ou descrição); a trava só do título
    # (`evidencias`, logo abaixo) é a do legado e não mudou.
    assert 'original) só pela "condicao" da ficha' in produto
    assert (
        "diga o que o título traz (marca e modelo) e que a compra é protegida pela plataforma"
        in produto
    )
    assert "O prazo de garantia do título não se repete (só o do ver_garantia)." in produto
    assert "sem isso, diga o que o anúncio informa" not in produto
    certo = (
        "Olá! O anúncio é do Apple Watch Series 11, da marca Apple, e a compra é protegida "
        "pela plataforma."
    )
    assert evidencias.motivos_sem_evidencia(certo, APPLE_WATCH) == []
    assert validador.validar(certo, plataforma="shopee", canal="chat", origem="davinci_ia") == []
    # O que o prompt de antes empurrava (cc95, a0ce): condição fora do título…
    assert evidencias.motivos_sem_evidencia(
        "O anúncio informa que o relógio é novo e original.", APPLE_WATCH
    )
    # …e o prazo de garantia do título.
    assert validador.validar(
        "O anúncio informa 1 ano de garantia Apple.",
        plataforma="shopee",
        canal="chat",
        origem="davinci_ia",
    )


# ═══════════════ 9. o cancelamento sem a negativa ═══════════════

CANCELAMENTO_PROPOSTO = (
    "Olá! Como o pedido já foi enviado, para cancelar agora o caminho é a devolução: ao receber, "
    "peça a devolução ou o reembolso em Minhas compras > o pedido; a Shopee analisa e devolve "
    "pela mesma forma de pagamento."
)


@pytest.mark.parametrize(
    "texto",
    [
        "Como o pedido já foi enviado, a Shopee não permite cancelar um pedido já enviado.",
        "Não é possível cancelar o pedido, pois ele já foi enviado.",
        "O pedido já foi enviado, então não é mais possível cancelar.",
        "Não há como cancelar o pedido agora.",
        "O cancelamento não é mais possível.",
        "Não dá mais para cancelar o pedido.",
    ],
)
def test_9_a_negativa_formal_e_a_da_plataforma_avisam_como_o_nao_da(texto):
    (p,) = checagens.conferir_cancelar_pela_loja(texto)
    assert p.codigo == checagens.CANCELAR_PELA_LOJA and p.suave
    assert "para cancelar agora o caminho é a devolução" in p.motivo


@pytest.mark.parametrize(
    "texto",
    [
        CANCELAMENTO_PROPOSTO,
        "O pedido já foi postado com a mala e os 2 cadeados, então não é possível cancelar um item agora.",
        "Não é possível cancelar só o cadeado.",
        "Você não precisa cancelar o pedido.",
    ],
)
def test_9_a_orientacao_proposta_e_a_parte_do_pedido_passam(texto):
    assert checagens.conferir_cancelar_pela_loja(texto) == []


async def test_9_a_orientacao_proposta_sai_sozinha():
    postado = {"numero": "{numero_pedido}", "postado": "sim", "enviado": True}
    final = await _completo(
        CANCELAMENTO_PROPOSTO,
        categoria="cancelamento",
        modo=MODO_AUTO,
        cliente="quero cancelar",
        pedido=postado,
    )
    assert _sai(final), (final.motivos, final.sem_auto)
    o = prompt.ORIENTACAO["cancelamento"]
    assert "para cancelar agora o caminho é a devolução" in o
    assert "não permite cancelar um pedido já enviado" not in o


# ═══════════════ 10. a exceção do pedido de vídeo ═══════════════


def test_10_a_excecao_do_analisa_e_responde_nos_tres_lugares():
    assert "fora o 'a nossa equipe analisa e responde por aqui' de quem pede vídeo e fotos" in (
        prompt.VALE_SEMPRE
    )
    assert "se alguém precisa agir ou decidir agora (a régua)" in prompt.VALE_SEMPRE
    assert "se a próxima etapa depende de alguém" not in prompt.VALE_SEMPRE
    geral = prompt.bloco_geral()
    estilo = geral[geral.index("# ESTILO") : geral.index("# OS <dados>")]
    assert '(fora o "analisa e responde por aqui" do pedido de vídeo e fotos)' in estilo
    garantia = geral[geral.index("# GARANTIA") : geral.index("# ORIGEM")]
    assert "No rascunho de pessoa, sem a última oração." in garantia
    assert "Se a próxima etapa depende de" not in geral


# ═══════════════ 11. a troca: não postado, postado e por defeito ═══════════════


def test_11_a_troca_separada_por_postado_e_por_defeito():
    o = prompt.ORIENTACAO["troca_devolucao"]
    assert "não postado e quer outra variação ou modelo, excecao" in o
    assert "postado, a devolução deste pedido ao receber" in o
    assert "por defeito ou avaria, a orientação do defeito (vídeo e fotos), nunca compra nova" in o


@pytest.mark.parametrize(
    ("cliente", "texto"),
    [
        (
            "quero trocar, veio com defeito",
            "Olá! É só devolver pelo app e fazer uma compra nova do modelo.",
        ),
        (
            "a tela veio trincada, quero trocar",
            "Olá! Pode pedir a devolução e comprar de novo no anúncio.",
        ),
        ("o celular não liga, quero outro", "Olá! Faça um novo pedido e devolva este pelo app."),
    ],
)
def test_11_compra_nova_para_quem_relata_defeito_e_aviso(cliente, texto):
    (p,) = checagens.conferir_compra_nova_no_defeito(texto, textos_cliente=[cliente])
    assert p.codigo == checagens.COMPRA_NOVA_NO_DEFEITO and p.suave


@pytest.mark.parametrize(
    ("cliente", "texto"),
    [
        # a troca de modelo sem defeito (postado): a compra nova é a orientação
        (
            "quero trocar pelo azul",
            "Olá! A troca segue pela devolução deste pedido no app, e o azul é uma compra nova.",
        ),
        # o defeito com a orientação certa
        (
            "quero trocar, veio com defeito",
            "Sinto muito! Pode mandar por aqui um vídeo curto e fotos? A equipe analisa e responde por aqui, e troca, devolução ou reembolso seguem depois da análise.",
        ),
    ],
)
def test_11_o_que_nao_e_compra_nova_no_defeito_passa(cliente, texto):
    assert checagens.conferir_compra_nova_no_defeito(texto, textos_cliente=[cliente]) == []
