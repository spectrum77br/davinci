"""A revisão final de 20/10/2026 do AGENTE de atendimento (a rodada "ajustar" 2).

Cada achado confirmado vira teste, com as frases que a revisão usou:

- a promessa de ação da equipe nas formas que ainda passavam (a passiva, o
  "a gente", o gerúndio, a 1ª pessoa do singular, o presente de ação), também
  na frase-pergunta; a exceção da evidência só para "analisa e responde";
  "aguarde o retorno da Shopee" é orientação;
- a garantia: a fala dela com o status vencido/só software vai para pessoa;
  as paráfrases da negação; o prazo da regra não livra a promessa com status;
  a garantia legal da outra linha sai sem pessoa;
- a entrega por estado: a cobertura genérica e o estado que o cliente citou;
  o "Rio" sozinho e a negação depois do estado;
- o cartão de OUTRO vendedor não é fonte (`ver_anuncio`, escopo do pacote);
- a trava da ficha no automático pelo que o TEXTO afirma;
- o caminho para fora (marca, SAC, transportadora, "ML");
- a régua: 7, 8, 9, 11 e 13 nas formas comuns (paridade com a Caixa
  Humano), a pré-venda sem pedido, o OUTRO pedido (4 e 11), revenda e mimo;
- sem pedido no sistema, a frase sobre o pedido vai para pessoa;
- as variações de 2+ anúncios (as do `ver_anuncio` entram);
- os pares com dado pessoal em minúscula;
- o erro do provedor do agente: o tipo, a pausa própria, o contador do
  legado intocado, o 400 de formato; a rodada com o Groq em pausa;
- o "avariado" do cliente e o "AVARIADO" do cadastro;
- a reversão do automático pela nota da auditoria.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import httpx2
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AtendimentoCanal
from app.services.atendimento import agente, humano, ia, restricao_envio, validador
from app.services.atendimento.agente import (
    acao_pessoa,
    checagens,
    ferramentas,
    pacote,
    pares,
    reversao,
)
from app.services.atendimento.constantes import (
    HUMANO_ACAO_PESSOA,
    MODO_AUTO,
    MODO_COPILOTO,
    PROMPT_VERSAO_AGENTE,
    RASCUNHO_BLOQUEADO,
)
from tests import test_atendimento_agente_laco as _laco
from tests import test_atendimento_agente_ligacao as _ligacao
from tests.test_atendimento_agente_pacote import _cliente, _conv, _gat
from tests.test_atendimento_conferir_saida import _base, _chamada
from tests.test_atendimento_ia import PEDIDO_MKT, _msg, _rascunhos

# As fixtures do laço (o SDK real sobre o transporte falso) e da ligação (o
# `_gerar` com o agente falso, o teto, o modelo falso do legado).
pecas = _laco.pecas
redis_falso = _laco.redis_falso
ajustes = _laco.ajustes
_rodar = _laco._rodar
agente_ligado = _ligacao.agente_ligado
ia_ligada = _ligacao.ia_ligada
modelo = _ligacao.modelo
respondedor = _ligacao.respondedor
teto = _ligacao.teto
_sem_limites_nem_rodada = _ligacao._sem_limites_nem_rodada
_do_agente = _ligacao._do_agente
_fim = _ligacao._fim

# ─────────────── a promessa de ação da equipe (regra 16) ───────────────


def _promessa(texto: str, acao: str = "responder", assunto: str = "outro") -> list[str]:
    return [
        p.motivo
        for p in checagens.conferir_promessa_de_equipe(
            texto, acao=acao, acoes_pessoa=[], assunto=assunto
        )
    ]


PROMESSAS = [
    # As da revisão de segurança.
    "Seu pedido será reenviado.",
    "Faremos a troca assim que o produto chegar.",
    "Pode deixar que a gente cancela pra você.",
    "Fica tranquilo, a gente resolve isso para você!",
    "Estamos verificando o que aconteceu com seu pedido.",
    "Estamos analisando seu caso.",
    "Seu caso foi encaminhado para o setor responsável.",
    "Pode deixar que eu verifico isso para você e já te respondo.",
    "Deixa comigo, verifico e retorno ainda hoje.",
    "Já solicitei à transportadora uma verificação do seu pedido.",
    "Iremos atrás disso para você.",
    # As da revisão de completude.
    "Pode mandar um vídeo curto do defeito? Depois da análise, trocamos o aparelho.",
    "Se confirmado o defeito, fazemos a troca.",
    "Pode mandar um vídeo? Se for defeito, reenviamos outro.",
    "A equipe analisa e faz a troca.",
    "Estamos verificando com a transportadora e já te damos retorno.",
    "Verifico e te retorno em breve.",
    "Encaminho para a equipe responsável.",
    "Já estamos analisando o seu caso.",
    "Assim que tivermos novidade, avisaremos por aqui.",
    "Te aviso assim que chegar.",
    "Pode deixar que a gente resolve.",
    "Já já te mando o rastreio.",
]


@pytest.mark.parametrize("texto", PROMESSAS)
def test_as_formas_comuns_da_promessa_vao_para_pessoa(texto):
    (motivo,) = _promessa(texto)
    assert motivo.startswith("promete ação da equipe"), texto


@pytest.mark.parametrize("assunto", ["defeito", "item_errado_faltando", "garantia"])
@pytest.mark.parametrize(
    "texto",
    [
        "Pode enviar um vídeo curto do defeito e fotos por aqui; a equipe analisa e já "
        "providencia a troca.",
        "Pode enviar um vídeo curto do defeito e fotos por aqui. Depois a gente envia outro "
        "aparelho.",
        "Pode enviar um vídeo curto do defeito e fotos por aqui. Se confirmado, a troca é "
        "garantida.",
        "Pode mandar um vídeo do defeito por aqui? A equipe analisa e faz a troca.",
    ],
)
def test_a_excecao_da_evidencia_nao_livra_a_promessa_alem_da_analise(texto, assunto):
    assert _promessa(texto, assunto=assunto), texto


@pytest.mark.parametrize("assunto", ["defeito", "item_errado_faltando", "garantia"])
def test_a_excecao_da_evidencia_vale_para_analisa_e_responde(assunto):
    for texto in (
        "Pode mandar um vídeo curto do defeito por aqui? A equipe analisa e responde por aqui.",
        "Assim que você enviar o vídeo, a equipe analisa e responde por aqui.",
        "Sentimos muito! Envie por aqui um vídeo curto mostrando o defeito e fotos; a nossa "
        "equipe vai analisar e responder por aqui, e a troca segue depois da análise.",
    ):
        assert _promessa(texto, assunto=assunto) == [], texto


@pytest.mark.parametrize(
    "texto",
    [
        "Vamos verificar com a transportadora, pode me confirmar o número do pedido?",
        "Vamos providenciar a troca, pode mandar um vídeo do defeito?",
        "Podemos reenviar o produto para você, tudo bem?",
    ],
)
def test_na_pergunta_a_frase_pergunta_tambem_e_conferida(texto):
    assert _promessa(texto, acao="perguntar"), texto


def test_a_pergunta_so_de_pedido_ou_convite_nao_e_promessa():
    for texto in (
        "Pode me confirmar o número do pedido?",
        "Qual a cor que você prefere?",
        "Vamos verificar juntos?",
        "Vamos verificar?",
        "Posso ajudar em algo mais?",
    ):
        assert _promessa(texto, acao="perguntar") == [], texto


@pytest.mark.parametrize(
    "texto",
    [
        "Aguarde o retorno da Shopee pelo próprio pedido.",
        "A Shopee analisa e devolve o valor pela mesma forma de pagamento; aguarde o retorno "
        "dela no pedido.",
        "O reembolso será analisado pela Shopee e devolvido pela mesma forma de pagamento.",
        "A gente vende só produtos originais.",
        "Já enviamos o seu pedido, o código de rastreio está em Minhas compras.",
    ],
)
def test_a_orientacao_da_plataforma_e_o_fato_nao_sao_promessa(texto):
    assert _promessa(texto, assunto="reembolso") == [], texto


def test_aguarde_o_nosso_retorno_continua_promessa():
    assert _promessa("Aguarde o nosso retorno por aqui.")
    assert _promessa("Aguarde o retorno da equipe.")


# ─────────────── a garantia (regra 7) ───────────────

URANYX = ferramentas.REGRA_URANYX
EXPIRADA = {
    "status": "expirada",
    "regra": dict(URANYX),
    "inicio": "2025-01-10",
    "fim_hardware": "2025-04-10",
    "fim_software": "2026-01-10",
    "produto": "Celular Uranyx S5",
}
SO_SOFTWARE = {**EXPIRADA, "status": "somente_software", "fim_software": "2026-12-10"}
ATIVA = {**EXPIRADA, "status": "ativa", "fim_hardware": "2026-12-10", "fim_software": "2027-09-10"}


async def _garantia(texto: str, garantias: list[dict], **mudar: Any) -> ia.SaidaConferida:
    conversa, kw = _chamada(
        _base(
            texto=texto,
            categoria="garantia",
            garantias_conferidas=garantias,
            garantia_consultada=True,
            modo=MODO_AUTO,
            textos_cliente=["meu celular parou, tem garantia?"],
            textos_rajada=["meu celular parou, tem garantia?"],
            **mudar,
        )
    )
    return await ia.conferir_saida(
        None,
        conversa,
        SimpleNamespace(),
        **kw,
        agente=True,
        permitidos_extra=[json.dumps({"garantias": garantias})],
    )


@pytest.mark.parametrize(
    "texto",
    [
        "Pelo Painel, a garantia do seu aparelho tem as datas acima; para acionar, descreva o "
        "problema e mande um vídeo curto e fotos por aqui.",
        "A cobertura do seu celular segue as regras da loja.",
    ],
)
@pytest.mark.parametrize("garantia", [EXPIRADA, SO_SOFTWARE])
async def test_a_fala_de_garantia_com_status_sem_cobertura_vai_para_pessoa(texto, garantia):
    c = await _garantia(texto, [garantia])
    assert ia.MOTIVO_RESPOSTA_DE_GARANTIA in c.motivos and c.precisa_humano


async def test_a_fala_de_garantia_ativa_com_as_datas_dela_nao_vai_para_pessoa():
    c = await _garantia(
        "Seu aparelho está na garantia: hardware até 10/12/2026 e software até 10/09/2027.",
        [ATIVA],
    )
    assert ia.MOTIVO_RESPOSTA_DE_GARANTIA not in c.motivos


@pytest.mark.parametrize(
    "texto",
    [
        # Segurança.
        "A cobertura de hardware já se encerrou; segue só o software.",
        "O período de cobertura já passou.",
        "Infelizmente o prazo para acionar já se esgotou.",
        "Como já se passaram mais de 3 meses da entrega, o hardware não é mais coberto.",
        "Pelo painel, o status é expirada.",
        "Esse tipo de dano não é coberto.",
        "Quebra de tela não entra na garantia.",
        "Dano por queda não está coberto pela garantia.",
        "Mau uso anula a garantia.",
        "Neste caso não há cobertura.",
        # Completude.
        "Passou do prazo de garantia.",
        "A garantia de hardware não está mais valendo.",
        "Esse caso não é coberto pela garantia.",
        "Queda não é coberta pela garantia.",
        "Dano por água não tem cobertura.",
        "O prazo de 3 meses de hardware já se encerrou.",
        "A cobertura de hardware já encerrou.",
    ],
)
def test_as_parafrases_da_negacao_da_garantia(texto):
    assert validador.nega_garantia(texto), texto


@pytest.mark.parametrize(
    "texto",
    [
        "A garantia ainda não venceu.",
        "O prazo de entrega já passou; você pode abrir a reclamação pelo app.",
        "A garantia passou a valer na entrega.",
        "Isso não anula a garantia.",
        "O cupom está vencido.",
    ],
)
def test_o_que_nao_nega_a_garantia(texto):
    assert not validador.nega_garantia(texto), texto


async def test_passou_do_prazo_com_a_garantia_expirada_vai_para_pessoa():
    # Rodada 4 (21/10/2026), o contrato novo: a recusa da garantia vai para PESSOA
    # nos dois modos, com o motivo — a sugestão fica (antes, bloqueava).
    c = await _garantia("Passou do prazo de garantia.", [EXPIRADA])
    assert validador.MOTIVO_NEGA_GARANTIA in c.motivos and c.precisa_humano
    assert not c.bloqueado and c.erros == []


@pytest.mark.parametrize(
    "texto",
    [
        "Tente voltar o aparelho às configurações de fábrica.",
        "Apague os dados do aparelho e configure de novo.",
    ],
)
def test_as_parafrases_do_reset(texto):
    assert validador.sugere_reset(texto), texto


@pytest.mark.parametrize(
    ("garantia", "texto"),
    [
        (
            EXPIRADA,
            "Seu aparelho está coberto pela garantia de 3 meses para hardware. Pode mandar um "
            "vídeo curto do problema por aqui?",
        ),
        (SO_SOFTWARE, "O defeito na tela está coberto pela garantia de 3 meses para hardware."),
    ],
)
async def test_o_prazo_da_regra_nao_livra_a_promessa_com_status(garantia, texto):
    c = await _garantia(texto, [garantia])
    assert any(e.startswith("promessa de cobertura") for e in c.erros), c.erros
    assert c.precisa_humano


def test_a_regra_sem_status_continua_livre_e_o_legado_igual():
    prazos = validador.prazos_da_regra([{"regra": URANYX}])
    regra = [{"status": None, "regra": URANYX}]
    texto = "A garantia é de 3 meses para hardware e 12 meses para software."
    assert validador.conferir_garantia(texto, regra, prazos_de_garantia=prazos) == []
    # O legado (sem os prazos): como em origin.
    assert validador.conferir_garantia(
        "Seu aparelho está coberto pela garantia.", [EXPIRADA]
    ) == validador.conferir_garantia("Seu aparelho está coberto pela garantia.", [EXPIRADA])


async def test_a_garantia_legal_da_outra_linha_sai_sem_pessoa():
    legal = [{"status": None, "regra": dict(ferramentas.REGRA_LEGAL)}]
    c = await _garantia(
        "Este produto tem a garantia legal. Para acionar a garantia, descreva o problema e "
        "mande um vídeo curto e fotos por aqui; a nossa equipe analisa e responde por aqui.",
        legal,
    )
    assert ia.MOTIVO_RESPOSTA_DE_GARANTIA not in c.motivos, c.motivos
    # Ajuste r3: e sai de verdade — o teste só conferia o motivo, e a frase
    # "tem a garantia legal" ficava BLOQUEADA como promessa de cobertura.
    assert not c.bloqueado and c.validador_ok, c.erros
    assert validador.prazos_da_regra(legal) == frozenset()
    # Prazo e data continuam barrados (não há nenhum para conferir).
    com_prazo = await _garantia("A garantia é de 90 dias.", legal)
    assert com_prazo.bloqueado
    com_data = await _garantia("A garantia vai até 10/01/2027.", legal)
    assert com_data.bloqueado


# ─────────────── a entrega por estado ───────────────

RJ_DO_ANUNCIO = [
    {
        "item_id": "1",
        "restricao_envio": {
            "ufs": ["RJ"],
            "fonte": "anuncio",
            "trecho": "Não enviamos para o Rio de Janeiro",
            "ufs_conforme_pedido": [],
        },
    }
]


@pytest.mark.parametrize(
    "texto",
    [
        "Enviamos para todo o Brasil.",
        "Entregamos em todos os estados.",
        "Sim, entregamos no Rio.",
        "Sim, entregamos para o seu estado.",
        "Pode comprar que entregamos para você sem problema.",
        "Atendemos todo o território nacional.",
        # A orientação do CEP noutra oração não livra a afirmação.
        "Entregamos para todo o Brasil, confira o frete pelo CEP.",
    ],
)
def test_a_cobertura_generica_contra_o_estado_restrito_vai_para_pessoa(texto):
    # Rodada 4 (21/10/2026), o contrato novo: pessoa nos dois modos, sem
    # bloquear (antes, bloqueava).
    pend = checagens.conferir_regiao(texto, RJ_DO_ANUNCIO)
    assert any(
        not p.bloqueia and not p.so_auto and p.codigo == checagens.REGIAO_CONTRADIZ for p in pend
    ), texto


@pytest.mark.parametrize("texto", ["Sim, enviamos para Niterói.", "Chega sim!"])
def test_o_estado_restrito_que_o_cliente_citou(texto):
    pend = checagens.conferir_regiao(
        texto, RJ_DO_ANUNCIO, textos_cliente=["moro no Rio, entregam aí?"]
    )
    # Rodada 4: pessoa nos dois modos, sem bloquear (antes, bloqueava).
    assert any(not p.bloqueia and not p.so_auto for p in pend), texto


def test_a_orientacao_pelo_cep_e_a_frase_do_anuncio_passam():
    for texto in (
        "Conforme o anúncio, não enviamos para o Rio de Janeiro. Para outros estados, confira "
        "o prazo e o frete pelo CEP na página do anúncio.",
        "Confira pelo CEP se chega na sua região.",
        "Não entregamos no Rio.",
    ):
        pend = checagens.conferir_regiao(
            texto, RJ_DO_ANUNCIO, textos_cliente=["moro no Rio, entregam aí?"]
        )
        assert not any(p.bloqueia for p in pend), (texto, pend)


def test_sem_restricao_e_o_cliente_citando_o_estado_vai_para_pessoa():
    (p,) = checagens.conferir_regiao(
        "Sim, entregamos para todo o Brasil.", [], textos_cliente=["moro em SP, entregam aí?"]
    )
    assert p.codigo == checagens.REGIAO_SEM_FONTE and not p.bloqueia
    assert checagens.conferir_regiao("Enviamos para todo o Brasil.", []) == []


def test_o_detector_le_o_rio_sozinho_e_a_negacao_depois_do_estado():
    assert restricao_envio.ufs_negadas("Atenção: pedidos para o RJ não são enviados") == ["RJ"]
    assert restricao_envio.ufs_negadas("Não entregamos no Rio") == ["RJ"]
    assert restricao_envio.ufs_de_destino("Sim, entregamos no Rio.") == ["RJ"]
    assert restricao_envio.ufs_de_destino("Entregamos no Rio Grande do Sul.") == ["RS"]
    assert restricao_envio.ufs_de_destino("Enviamos para Rio Verde.") == []
    assert restricao_envio.ufs_citadas("moro no Rio, entregam aí?") == ["RJ"]


# ─────────────── o cartão de OUTRO vendedor não é fonte ───────────────


async def test_o_cartao_de_outro_vendedor_nao_entra_no_escopo(db: AsyncSession, make_user):
    from tests.test_atendimento_agente_ferramentas import _base as _base_f
    from tests.test_atendimento_agente_ferramentas import _ctx, _erro
    from tests.test_atendimento_ia import _conversa

    conversa = await _conversa(db, make_user, pedido=None)
    conversa.anuncio_id = None
    await db.commit()
    m = await _msg(db, conversa, "vocês têm este? tem NFC?")
    m.anexos = [{"tipo": "produto", "item_id": "77777777777", "titulo": "Celular X 5G NFC IP68"}]
    await db.commit()
    base = await _base_f(db, conversa, recentes=[m])
    ctx = _ctx(conversa, base)
    # O pacote não põe o cartão alheio no escopo (só o desta loja).
    await pacote.montar_pacote(db, conversa, m, base, ctx)
    assert not ctx.anuncio_citado("77777777777")
    texto = await _erro(ctx, base, conversa, "ver_anuncio", item_id="77777777777")
    assert texto == ferramentas.ERRO_FORA_DO_ESCOPO
    assert ctx.fichas_lidas == []
    # Sem fonte nenhuma, "tem NFC" vai para pessoa.
    pend = checagens.conferir_fonte_produto("Sim, tem NFC e é à prova d'água.", [], ctx)
    assert {p.codigo for p in pend} == {checagens.PRODUTO_SEM_FONTE}


# ─────────────── a ficha no automático pelo que o texto afirma ───────────────


@pytest.mark.parametrize(
    "texto",
    [
        "A tela é AMOLED.",
        "O processador é octa-core.",
        "Tem rádio FM.",
        "Tem sensor de proximidade e giroscópio.",
        "Funciona com chip da Vivo, Claro e Tim.",
        "É desbloqueado para todas as operadoras.",
        "Ele roda jogos pesados como Free Fire sem travar.",
        "A mala é de policarbonato.",
        "A mala tem rodinhas 360 graus.",
        "Cabe na cabine do avião.",
        "Temos sim a cor preta disponível, e a tela é AMOLED.",
    ],
)
def test_a_afirmacao_de_caracteristica_vai_para_pessoa_no_automatico(texto):
    (p,) = checagens.conferir_ficha_no_automatico(texto, [])
    assert p.motivo == ia.MOTIVO_FICHA_NO_AUTOMATICO and p.so_auto and not p.bloqueia


@pytest.mark.parametrize(
    "texto",
    [
        "Temos sim a cor preta disponível.",
        "Temos na versão 8GB+256GB disponível.",
        "Pode mandar um vídeo curto mostrando a tela e a bateria?",
        "Você acompanha o envio em Minhas Compras.",
        "Não temos essa informação sobre a tela.",
    ],
)
def test_a_frase_so_de_variacao_e_o_pedido_de_video_nao_sao_caracteristica(texto):
    fichas = [{"item_id": "1", "variacoes": [{"nome": "Preto 8GB+256GB"}, {"nome": "Azul"}]}]
    assert checagens.conferir_ficha_no_automatico(texto, fichas) == [], texto


def test_a_trava_da_ficha_pelo_texto_so_no_automatico():
    from app.services.atendimento.agente.contexto import ContextoAgente
    from app.services.atendimento.agente.saida import Proposta

    texto = "Temos sim a cor preta disponível, e a tela é AMOLED."
    conversa = SimpleNamespace(
        id=uuid4(), plataforma="shopee", canal="chat", pedido_marketplace=None
    )
    conferida = ia.SaidaConferida(
        texto=texto,
        categoria="estoque_variacao",
        precisa_humano=False,
        motivos=[],
        erros=[],
        status="pendente",
    )
    base = SimpleNamespace(
        textos_rajada=["tem preta?"], textos_cliente=["tem preta?"], fatos={}, recentes=[], ctx={}
    )
    p = Proposta(
        acao="responder",
        texto=texto,
        assunto="estoque_variacao",
        confianca=0.9,
        acao_pessoa=None,
        motivo_pessoa="",
        evidencias=[],
    )
    for modo, pego in ((MODO_AUTO, True), (MODO_COPILOTO, False)):
        ctx = ContextoAgente.da_conversa(conversa, None, modo=modo)
        chk = checagens.conferir(p, conferida, ctx, base, fichas=[], gatilho=None)
        codigos = {x.codigo for x in chk.pendencias}
        assert (checagens.FICHA_NO_AUTOMATICO in codigos) is pego


# ─────────────── o caminho para fora da plataforma ───────────────


@pytest.mark.parametrize(
    "texto",
    [
        "Entre em contato com a Uranyx.",
        "Procure o suporte da Uranyx para o reparo.",
        "Fale com o suporte técnico da marca.",
        "Procure o SAC da Doogee.",
        "Contate a Doogee diretamente.",
        "Ligue para a transportadora J&T.",
        "Entre em contato com os Correios para saber do seu pedido.",
        "Procure a transportadora pelo site dela.",
        "Você pode comprar no ML.",
    ],
)
def test_o_contato_com_marca_sac_ou_transportadora_bloqueia(texto):
    pend = checagens.conferir_caminho_da_plataforma(texto, "shopee")
    assert pend and all(p.bloqueia for p in pend), texto


@pytest.mark.parametrize(
    "texto",
    [
        "O frasco tem 500 ml.",
        "Se o valor não aparecer, fale com o suporte da Shopee pelo próprio pedido.",
        "Consulte o rastreio da transportadora no app.",
        "A Uranyx é a marca do aparelho.",
    ],
)
def test_o_suporte_do_app_e_o_ml_de_medida_passam(texto):
    assert checagens.conferir_caminho_da_plataforma(texto, "shopee") == [], texto


def test_a_marca_do_anuncio_tambem_conta():
    pend = checagens.conferir_caminho_da_plataforma(
        "Procure a Fulanotech para o conserto.", "shopee", marcas=["Fulanotech"]
    )
    assert pend and pend[0].bloqueia


# ─────────────── o rótulo do cadastro × a palavra do cliente ───────────────


def test_avariado_do_cliente_passa_e_o_do_cadastro_bloqueia():
    assert (
        checagens.conferir_nomes_internos(
            "Sentimos muito que o produto chegou avariado. Pode enviar uma foto?", []
        )
        == []
    )
    for texto in ("Temos a cor Azul AVULSO AVARIADO.", "Temos o Azul AVARIADO.", "Azul salvado"):
        (p,) = checagens.conferir_nomes_internos(texto, [])
        assert p.bloqueia and p.codigo == checagens.ROTULO_INTERNO, texto


# ─────────────── a régua de código (e a Caixa Humano igual) ───────────────

COM_PEDIDO = {"pedido": {"numero": "300001", "situacao": "Em aberto"}}


def _paridade(texto: str, postado: str | None, ctx: dict | None = None) -> list[str]:
    """Os códigos da régua, conferidos contra a `humano.triar(agente=True)`."""
    ctx = COM_PEDIDO if ctx is None else ctx
    recentes = [_cliente(texto, 5)]
    conversa = _conv(
        pedido_marketplace=PEDIDO_MKT if ctx.get("pedido") else None, situacao="aberta"
    )
    codigos = [g.codigo for g in _gat(recentes, conversa=conversa, ctx=ctx, postado=postado)]
    t = humano.triar(
        conversa,
        recentes,
        ctx,
        so_humano=frozenset(),
        gatilho=recentes[-1],
        agente=True,
        postado=postado,
        chaves_ativas=frozenset(),
    )
    assert (HUMANO_ACAO_PESSOA in t.motivos) is bool(codigos), (texto, t)
    assert list(t.assuntos) == codigos, (texto, t)
    return codigos


@pytest.mark.parametrize(
    ("texto", "codigo"),
    [
        ("dá pra segurar o envio?", "segurar_envio"),
        ("me arrependi, não envia por favor", "segurar_envio"),
        ("o número da minha casa está errado, é 123", "alterar_endereco"),
        ("vou viajar, pode entregar na casa da minha mãe?", "alterar_endereco"),
        ("preciso da nota no meu cnpj", "enviar_arquivo_nf"),
    ],
)
def test_7_8_e_9_nas_formas_comuns_com_o_pedido_em_aberto(texto, codigo):
    assert codigo in _paridade(texto, acao_pessoa.POSTADO_NAO)


@pytest.mark.parametrize(
    ("texto", "codigo"),
    [
        ("o carteiro disse que entregou mas não recebi", "abrir_chamado"),
        ("meu pacote sumiu", "abrir_chamado"),
        ("se eu comprar 10 fica mais barato?", "desconto_brinde"),
        ("me faz um precinho camarada", "desconto_brinde"),
    ],
)
def test_11_e_13_nas_formas_comuns_com_o_pedido_postado(texto, codigo):
    assert codigo in _paridade(texto, acao_pessoa.POSTADO_SIM)


def test_as_pistas_proprias_marcam_a_orientacao_do_turno():
    assert "cancelamento" in acao_pessoa.pistas_de_orientacao(
        [_cliente("me arrependi, não envia por favor", 5)], None
    )
    assert "endereco" in acao_pessoa.pistas_de_orientacao(
        [_cliente("pode entregar na casa da minha mãe?", 5)], None
    )


@pytest.mark.parametrize(
    "texto",
    [
        "Vocês revendem produtos originais?",
        "Obrigada pelo mimo!",
        "o número do pedido está errado?",
        "não encontrei o produto na cor azul",
    ],
)
def test_revenda_mimo_e_o_que_nao_e_acao(texto):
    assert _paridade(texto, acao_pessoa.POSTADO_SIM) == []
    assert _paridade(texto, acao_pessoa.POSTADO_NAO) == []


@pytest.mark.parametrize(
    "texto", ["Posso cancelar a compra se demorar?", "Como faço pra cancelar se eu comprar?"]
)
def test_a_pre_venda_sem_pedido_e_orientacao(texto):
    assert _paridade(texto, None, ctx={}) == []
    # Na pergunta pública, também não.
    assert _gat([_cliente(texto, 5)], conversa=_conv(canal="pergunta"), postado=None) == []


def test_o_cartao_de_pedido_conta_como_pedido():
    cartao = _cliente(None, 6, anexos=[{"tipo": "pedido", "pedido": "251020XYZ"}])
    assert acao_pessoa.ha_pedido(_conv(), {}, [cartao])
    (g,) = _gat([cartao, _cliente("quero cancelar", 5)], postado=None)
    assert g.codigo == "segurar_envio"


def test_o_outro_pedido_dispara_11_e_4():
    recentes = [_cliente("ainda não recebi o outro pedido", 5)]
    entregue = {"pedido": {"numero": "300002"}, "logistica": {"entregue_em": "2026-10-18"}}
    codigos = [
        g.codigo for g in acao_pessoa.gatilhos_de_outro_pedido(entregue, "sim", recentes, None)
    ]
    assert codigos == ["abrir_chamado"]
    # O mesmo turno com o outro pedido ainda a caminho: nada.
    a_caminho = {"pedido": {"numero": "300002"}, "logistica": {"status": "Em trânsito"}}
    assert acao_pessoa.gatilhos_de_outro_pedido(a_caminho, "sim", recentes, None) == []
    avaliacao = {"pedido": {"numero": "300003"}, "avaliacoes": [{"estrelas": 2, "pendente": True}]}
    codigos = [
        g.codigo
        for g in acao_pessoa.gatilhos_de_outro_pedido(avaliacao, "sim", [_cliente("oi", 5)], None)
    ]
    assert codigos == ["avaliacao_baixa"]


# ─────────────── sem o pedido no sistema ───────────────


@pytest.mark.parametrize(
    "texto",
    [
        "Seu pedido já está com a transportadora.",
        "Ele já está em rota de entrega.",
        "Consta como entregue no sistema.",
        "Seu pedido está em separação.",
        "O pedido está atrasado por causa da transportadora.",
        "Seu pedido está pronto para envio.",
        "Seu pedido ainda não saiu.",
        "O estorno já aparece na sua fatura.",
        "Está tudo certo com seu pedido.",
    ],
)
async def test_sem_pedido_a_frase_sobre_o_pedido_vai_para_pessoa(texto):
    assert ia.afirma_do_pedido(texto), texto
    conversa, kw = _chamada(
        _base(ctx={}, conversa={"pedido_marketplace": PEDIDO_MKT}, texto=texto, valores={})
    )
    c = await ia.conferir_saida(None, conversa, SimpleNamespace(), **kw, agente=True)
    assert "pedido não encontrado no sistema" in c.motivos and c.precisa_humano


def test_pedir_o_numero_e_orientar_o_app_continuam_saindo():
    for texto in (
        "Olá! Pode me passar o número do pedido? Ele aparece em Minhas compras.",
        "Você acompanha o envio em Minhas Compras > o pedido > Informações do envio.",
        "Sim, a mala tem quatro rodinhas.",
    ):
        assert not ia.afirma_do_pedido(texto), texto


# ─────────────── as variações de 2+ anúncios ───────────────

CEL = {
    "item_id": "A",
    "titulo": "Celular Uranyx F110 Pro 5G",
    "variacoes": [
        {"nome": "Azul", "disponivel": True, "confiavel": True},
        {"nome": "Preto", "disponivel": True, "confiavel": True},
    ],
    "variacoes_completas": True,
}
RELOGIO_ESGOTADO = {
    "item_id": "B",
    "titulo": "Relógio Smartwatch G9 Ultra",
    "variacoes": [
        {"nome": "Azul", "disponivel": False, "confiavel": True},
        {"nome": "Vermelho", "disponivel": False, "confiavel": True},
    ],
    "variacoes_completas": True,
}
RELOGIO_AZUL = {
    **RELOGIO_ESGOTADO,
    "variacoes": [
        {"nome": "Azul", "disponivel": True, "confiavel": True},
        {"nome": "Vermelho", "disponivel": False, "confiavel": True},
    ],
}


def _var(texto: str, fichas: list[dict], cliente: list[str] | None = None):
    """(código, a contradição com o dado). Rodada 4 (21/10/2026), o contrato novo:
    a contradição vai para PESSOA nos dois modos (o True, antes "bloqueia"); a
    falta do dado é a regra suave (False). Nada bloqueia."""
    pend = checagens.conferir_variacoes(texto, fichas, cliente or [])
    assert not any(p.bloqueia for p in pend), pend
    return [(p.codigo, not p.so_auto) for p in pend]


def test_a_lista_lida_pelo_ver_anuncio_e_conferida():
    # O modelo do anúncio B (lido por ferramenta) esgotado: contradiz (pessoa).
    assert (checagens.VARIACAO_CONTRADIZ, True) in _var(
        "O modelo G9 está disponível na cor azul.", [CEL, RELOGIO_ESGOTADO]
    )
    # Sem dizer de qual: pessoa, sem bloqueio.
    assert _var("A cor vermelha está esgotada.", [CEL, RELOGIO_ESGOTADO]) == [
        (checagens.VARIACAO_SEM_DADO, False)
    ]


def test_com_dois_anuncios_a_resposta_certa_do_segundo_passa():
    assert _var("Temos o relógio G9 na cor azul, disponível", [CEL, RELOGIO_AZUL]) == []
    # O cliente citou o G9: vale a lista dele.
    assert _var("Temos na cor azul disponível.", [CEL, RELOGIO_AZUL], ["tem o G9 azul?"]) == []
    assert _var("Temos na cor azul disponível.", [CEL, RELOGIO_AZUL]) == [
        (checagens.VARIACAO_SEM_DADO, False)
    ]
    # A mesma ficha repetida (o pacote e o ver_anuncio) conta uma vez.
    assert _var("Temos na cor azul disponível.", [CEL, {**CEL, "titulo": CEL["titulo"]}]) == []


# ─────────────── os pares sem dado pessoal (minúscula) ───────────────


@pytest.mark.parametrize(
    "texto",
    [
        "meu marido roberto recebeu o pacote e não tinha nada",
        "foi entregue para joana minha vizinha mas ela não está",
        "moro na rua das flores perto do mercado e não chegou",
        "deixaram com a dona Maria do lado",
        "minha filha ana clara que recebeu",
        "MEU MARIDO ROBERTO RECEBEU O PACOTE",
    ],
)
def test_o_dado_pessoal_em_minuscula_tira_o_par(texto):
    assert pares.tem_dado_pessoal(texto), texto
    assert not pares._filtro(
        texto,
        "Sentimos muito! Abra a reclamação pelo app, em Minhas compras, no pedido.",
        plataforma="shopee",
        canal="chat",
        contagem=None,
    )


@pytest.mark.parametrize(
    "texto",
    [
        "meu pedido ainda não chegou, pode verificar?",
        "recebi o produto mas veio com defeito",
        "foi entregue pelo correio hoje",
        "meu marido que comprou",
    ],
)
def test_o_que_nao_e_dado_pessoal_fica(texto):
    assert not pares.tem_dado_pessoal(texto), texto


# ─────────────── o erro do provedor do agente ───────────────


def _erro_api(status: int, mensagem: str = "x", tipo: str = "api_error") -> httpx2.Response:
    return httpx2.Response(
        status, json={"type": "error", "error": {"type": tipo, "message": mensagem}}
    )


@pytest.mark.parametrize(
    ("resposta", "tipo", "definitivo"),
    [
        (_erro_api(401, tipo="authentication_error"), agente.ERRO_CONFIGURACAO, False),
        (
            _erro_api(404, "model: claude-haiku-5-5", "not_found_error"),
            agente.ERRO_CONFIGURACAO,
            False,
        ),
        (_erro_api(529, tipo="overloaded_error"), agente.ERRO_PASSAGEIRO, False),
        (_erro_api(500), agente.ERRO_PASSAGEIRO, False),
        (
            _erro_api(
                400, "output_config.format: Extra inputs are not permitted", "invalid_request_error"
            ),
            agente.ERRO_PEDIDO_INVALIDO,
            True,
        ),
        (
            _erro_api(
                400, "messages: text content blocks must be non-empty", "invalid_request_error"
            ),
            agente.ERRO_PEDIDO_RECUSADO,
            True,
        ),
    ],
)
async def test_o_tipo_do_erro_do_provedor(pecas, resposta, tipo, definitivo):
    r, _, _ = await _rodar(pecas, [resposta])
    assert r.parada == agente.PARADA_PROVEDOR and not r.gravar
    assert (r.erro_provedor.codigo, r.erro_provedor.definitivo) == (tipo, definitivo)


async def test_o_passageiro_na_terceira_vez_do_gatilho_vai_para_pessoa(pecas):
    from tests.test_atendimento_agente_laco import _gatilho

    gatilho = _gatilho()
    for _ in range(2):
        r, _, _ = await _rodar(pecas, [_erro_api(529, tipo="overloaded_error")], gatilho=gatilho)
        assert not r.gravar
    r, _, _ = await _rodar(pecas, [_erro_api(529, tipo="overloaded_error")], gatilho=gatilho)
    assert r.gravar and r.motivo == agente.MOTIVO_PROVEDOR_FALHOU
    assert r.status == RASCUNHO_BLOQUEADO and r.texto is None


@pytest.mark.parametrize("tipo", [agente.ERRO_CONFIGURACAO, agente.ERRO_PASSAGEIRO])
async def test_401_e_529_pausam_so_o_agente_e_nao_tocam_o_contador_do_legado(
    db: AsyncSession, make_user, agente_ligado, modelo, respondedor, teto, tipo
):
    agente_conv = await _do_agente(db, make_user, externo_id="conv-agente")
    await _msg(db, agente_conv, "cadê meu pedido?", ha=timedelta(minutes=5))
    legado_conv = await _do_agente(db, make_user, ligado=False, externo_id="conv-legado")
    await _msg(db, legado_conv, "cadê meu pedido?", ha=timedelta(minutes=5))
    erro = ia.ErroProvedor("provedor devolveu 401", codigo=tipo)
    respondedor.resultado = _fim(parada=agente.PARADA_PROVEDOR, gravar=False, erro_provedor=erro)

    for _ in range(3):
        assert await ia.gerar_rascunho(db, agente_conv) is None
        assert ia._agente_no_limite() and not ia._no_limite()
        assert await ia.motivo_sem_rascunho(db, agente_conv) in (
            ia.MOTIVO_AGENTE_CONFIGURACAO,
            ia.MOTIVO_AGENTE_FALHOU,
        )
    assert await _rascunhos(db, agente_conv) == []
    # O agente contou na chave dele — nunca na do legado.
    assert set(teto.chaves) == {ia._CHAVE_TETO_AGENTE}
    # O canal sem o agente continua gerando (a pausa é só do agente).
    assert await ia._gerar_pendentes(db, limite=10, modos=("copiloto",)) == 1
    assert [r.prompt_versao for r in await _rascunhos(db, legado_conv)] == [ia.PROMPT_VERSAO]
    assert ia._CHAVE_TETO in teto.chaves


async def test_o_400_de_formato_pausa_o_agente_com_aviso(
    db: AsyncSession, make_user, agente_ligado, respondedor, teto, monkeypatch
):
    avisos: list[str] = []

    async def _aviso(texto: str) -> None:
        avisos.append(texto)

    monkeypatch.setattr(ia, "_avisar_pausa_do_agente", _aviso)
    conversa = await _do_agente(db, make_user)
    await _msg(db, conversa, "cadê meu pedido?")
    erro = ia.ErroProvedor(
        "provedor devolveu 400", definitivo=True, codigo=agente.ERRO_PEDIDO_INVALIDO
    )
    respondedor.resultado = _fim(parada=agente.PARADA_PROVEDOR, gravar=False, erro_provedor=erro)
    assert await ia.gerar_rascunho(db, conversa) is None
    assert await _rascunhos(db, conversa) == []
    assert ia._agente_no_limite() and len(avisos) == 1
    assert await ia.motivo_sem_rascunho(db, conversa) == ia.MOTIVO_AGENTE_PEDIDO_INVALIDO


async def test_o_400_recusado_em_duas_conversas_seguidas_vira_pausa(
    db: AsyncSession, make_user, agente_ligado, respondedor, teto, monkeypatch
):
    avisos: list[str] = []

    async def _aviso(texto: str) -> None:
        avisos.append(texto)

    monkeypatch.setattr(ia, "_avisar_pausa_do_agente", _aviso)
    erro = ia.ErroProvedor(
        "provedor devolveu 400", definitivo=True, codigo=agente.ERRO_PEDIDO_RECUSADO
    )
    respondedor.resultado = _fim(parada=agente.PARADA_PROVEDOR, gravar=False, erro_provedor=erro)
    primeira = await _do_agente(db, make_user, externo_id="conv-1")
    await _msg(db, primeira, "cadê meu pedido?")
    r = await ia.gerar_rascunho(db, primeira)
    # A 1ª: o bloqueado do legado (pode ser o texto DESTA conversa).
    assert r is not None and r.status == RASCUNHO_BLOQUEADO and not ia._agente_no_limite()
    segunda = await _do_agente(db, make_user, externo_id="conv-2")
    await _msg(db, segunda, "e o meu pedido?")
    # A 2ª, em OUTRA conversa seguida: é o pedido — pausa, aviso, sem rascunho.
    assert await ia.gerar_rascunho(db, segunda) is None
    assert ia._agente_no_limite() and len(avisos) == 1
    assert await _rascunhos(db, segunda) == []


async def test_com_o_groq_em_pausa_o_agente_ainda_entra_na_rodada(
    db: AsyncSession, make_user, agente_ligado, modelo, respondedor, teto
):
    for i in range(11):
        legado = await _do_agente(db, make_user, ligado=False, externo_id=f"legado-{i}")
        await _msg(db, legado, "cadê meu pedido?", ha=timedelta(minutes=10 + i))
    do_agente = await _do_agente(db, make_user, externo_id="agente-1")
    await _msg(db, do_agente, "cadê meu pedido?", ha=timedelta(minutes=5))
    ia._anotar_limite(uuid4())  # o legado no limite do Groq
    assert await ia._gerar_pendentes(db, limite=10, modos=("copiloto",)) == 1
    assert len(respondedor.bases) == 1
    assert [r.prompt_versao for r in await _rascunhos(db, do_agente)] == [PROMPT_VERSAO_AGENTE]


# ─────────────── a reversão do automático pela auditoria ───────────────

LOJA = str(uuid4())


def _envio(i: int, assunto: str, *, dias: int = 1, conversa: str | None = None) -> dict:
    return {
        "tipo": "envio_automatico",
        "id": f"e{i}",
        "integration_id": LOJA,
        "conversa_id": conversa or str(uuid4()),
        "categoria": assunto,
        "enviada_em": (datetime.now(UTC) - timedelta(days=dias)).isoformat(),
    }


def test_a_reversao_por_erro_critico_e_por_nota_baixa():
    agora = datetime.now(UTC)
    envios = [_envio(i, "cancelamento") for i in range(3)] + [
        _envio(10 + i, "rastreio") for i in range(10)
    ]
    notas = [{"id": "e0", "enviaria_sem_editar": True, "erro_critico": True}] + [
        {"id": f"e{10 + i}", "enviaria_sem_editar": i < 8, "erro_critico": False} for i in range(10)
    ]
    decididas = {r.assunto: r.motivo for r in reversao.decidir(envios, notas, agora=agora)}
    assert decididas == {
        "cancelamento": reversao.MOTIVO_ERRO_CRITICO,
        "rastreio": reversao.MOTIVO_NOTA_BAIXA,
    }
    # 9/10 = 90%: fica; menos de 10 notas: fica; fora dos 7 dias: não conta.
    boas = [{"id": f"e{10 + i}", "enviaria_sem_editar": i < 9} for i in range(10)]
    assert reversao.decidir(envios[3:], boas, agora=agora) == []
    assert reversao.decidir(envios[3:8], notas[1:6], agora=agora) == []
    velho = [_envio(0, "cancelamento", dias=9)]
    assert reversao.decidir(velho, notas[:1], agora=agora) == []


async def test_a_reversao_tira_o_assunto_do_canal(db: AsyncSession, make_user):
    conversa = await _do_agente(
        db,
        make_user,
        modo="auto",
        auto_categorias=["rastreio"],
        auto_orientacao=["cancelamento"],
        auto_agente=["rastreio"],
    )
    envios = [_envio(0, "cancelamento", conversa=str(conversa.id))]
    notas = [{"id": "e0", "erro_critico": True}]
    decididas = reversao.decidir(envios, notas, agora=datetime.now(UTC))
    feitas = await reversao.reverter(db, decididas, gravar=False)
    assert [f["assunto"] for f in feitas] == ["cancelamento"]
    canal = await db.get(AtendimentoCanal, conversa.canal_id)
    assert canal.auto_orientacao == ["cancelamento"]  # o dry-run não mexe
    await reversao.reverter(db, decididas)
    await db.commit()
    await db.refresh(canal)
    assert canal.auto_orientacao == [] and canal.auto_agente == ["rastreio"]
    assert canal.auto_categorias == ["rastreio"]  # o do legado não muda
    texto = reversao.texto_do_alerta(feitas)
    assert texto and "cancelamento" in texto and str(conversa.id) not in texto


def test_o_cache_zerado_entre_conversas_e_contado():
    agente._sem_cache_seguidas[0] = 0
    sem = SimpleNamespace(requisicoes=1, cache_leitura=0)
    for _ in range(agente.CACHE_ZERADO_SEGUIDAS):
        agente._contar_cache_entre_conversas(sem, "h")
    assert agente._sem_cache_seguidas[0] == agente.CACHE_ZERADO_SEGUIDAS
    agente._contar_cache_entre_conversas(SimpleNamespace(requisicoes=1, cache_leitura=900), "h")
    assert agente._sem_cache_seguidas[0] == 0
