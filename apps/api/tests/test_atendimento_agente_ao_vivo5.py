"""A 5ª rodada da auditoria ao vivo (21/10/2026, noite): os itens 16, 17 e 21. Só o
caminho do AGENTE muda; o legado é o de sempre (o `test_atendimento_conferir_saida` e
os `test_k_*` do e2e).

16. Kia 22:29 (5839ff11): "Então a capinha não serve ne", a confirmação da resposta
    sobre a capinha, saiu como cobertura_regiao (o certo: acessorios_avulsos). Não
    abriu porta nenhuma (os dois assuntos estão liberados em todo canal do agente): o
    custo é o rótulo gravado. Só o cabeçalho dos ASSUNTOS do bloco 2 muda (quem só
    confirma ou repete a resposta da loja fica no assunto dela), sem regra nova de
    "ok/obrigado" e sem trava determinística. Sem replay de N amostras do mesmo turno
    (antes e depois), não dá para dizer que conserta.
17. Kia 22:41 (7f0c1b4e, troca_devolucao, conf 0.9): "comprei o aparelho mas ainda não
    chegou, assim que chegar posso devolver…?" — a regra suave da região lia o
    "chegou/chegar" como pergunta de entrega e o "Sim," da devolução como "sim,
    entrega" (com a restrição da LOJA, RJ). DESFEITO na 3ª contestação (22/10/2026): a
    leitura que tirava a chegada do pedido da fala só tinha o texto livre dos dois lados
    (o 7f0c1b4e não tinha pedido na conversa), e cada contestação achou rascunhos ruins
    que ela deixava sair sozinhos. A `conferir_regiao` é a do d64ce17d: o 7f0c1b4e volta
    ao aviso suave do ao vivo (um clique de pessoa), e os casos das três contestações
    ficam como guarda.
21. Barbosa ML pergunta 23:23 (552f20ba, troca_devolucao, conf 0.85): "Se trava no
    primeiro uso pode troca?" → "Não fazemos troca, nem pagando a diferença…". O
    agente aplicou a recusa padrão aprovada da troca (a da troca SEM defeito) a quem
    pergunta de defeito; só o verificador pegava. Agora o `consultar_manual` devolve a
    recusa com o recorte do que ela não cobre e a 1ª camada tem a regra suave
    `conferir_troca_negada_no_defeito`.

A calibração do verificador ganhou os casos do 17 (o 7f0c1b4e e o pós-venda bons; os
vizinhos ruins da região, regiao-14/15/16) e o do 21 (garantia-14, o 552f20ba, ruim) —
só medem; o `SISTEMA` não muda.

Contestação da 5ª rodada (22/10/2026): a 1ª versão do 17 tirava a chegada de falas de
ANTES da compra ("vou fechar meu pedido", "caso eu compre mais um" com um pedido antigo
na conversa) e com um lugar dito de outro jeito ("vive em", "é de", "tá em", "chegar o
aparelho em X"), e cortava "quando VAI chegar?" — tudo isso volta ao aviso (que é o de
antes da rodada). A regra do 21 ganhou o defeito com outras palavras (avaria, item
errado, "se esquentar"), as recusas com o "diferença" ditas de outro jeito, a isenção
"sem defeito" que nunca valia, e deixou de ler o sintoma só perguntado, a troca do
endereço/pagamento e o dano negado. Mais três linhas da calibração (regiao-17/18,
garantia-15).

2ª contestação (22/10/2026): o 17 ainda tirava a chegada da compra que não é a de agora
(o "não chegou" do cupom ou do pedido do irmão, "meu pedido foi cancelado", "comprei um
igual pro meu pai") e com o lugar dito de um jeito que a guarda não lia ("São Fidélis",
"é lá de", "minha mãe de X", "na cidade de", "pro Méier", o CEP), e a pergunta de estoque
de quem já comprou. Agora a compra incerta e o lugar largo seguram a chegada, e a leitura
da chegada só vale para o "Sim" do começo (a frase que diz a entrega com o verbo lê a
fala inteira). Mais uma linha da calibração (regiao-19, o lugar sem preposição).

3ª contestação (22/10/2026): o 17 ainda deixava sair sozinhos o "Sim" que promete a
entrega sem o verbo ("vai direitinho", "você recebe em casa"), a compra de um acessório
com o aparelho por comprar e o lugar abreviado ("S. Gonçalo", "de SG"); a correção
proposta (mais três listas) deixava passar os vizinhos seguintes ("comprei a mala…",
"em S Gonçalo"). O item 17 foi desfeito (a região do d64ce17d); os itens 16 e 21 ficam.
As linhas da calibração do 17 ficam: só medem o v7.
"""

from __future__ import annotations

import inspect
import io
import json
import re
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.atendimento import constantes, ia, validador
from app.services.atendimento import manual as manual_svc
from app.services.atendimento.agente import checagens, ferramentas, pacote, prompt, saida
from app.services.atendimento.agente import contexto as agente_ctx
from app.services.atendimento.agente import verificador as v
from app.services.atendimento.agente.saida import Proposta
from scripts.agente_eval import calibrar_verificador as calibrar
from tests import test_atendimento_agente_verificador as _ver
from tests.test_atendimento_agente_ao_vivo4 import VIZINHOS, _veredito_para
from tests.test_atendimento_agente_laco import _categorias_do_manual
from tests.test_atendimento_agente_prompt import RETRATO_BLOCO_GERAL
from tests.test_atendimento_ficha_loja import (  # noqa: F401 — o Redis falso (fixture)
    RedisMem,
    redis_mem,
)

teto_limpo = _ver.teto_limpo

# ═══════════════ os dados dos casos reais (sem dado pessoal) ═══════════════

# 7f0c1b4e (Kia Shopee, 21/10 22:41): as falas do turno e o rascunho real.
FALAS_7F0C = [
    "Entendi",
    "É pq eu comprei o aparelho mais ainda não chegou, assim que chegar eu posso devolver "
    "nós correios aí o dinheiro volta no mesmo dia o reembolso?",
    "Você sabe me dizer pfv",
]
RASCUNHO_7F0C = (
    "Sim, depois que o aparelho chegar, você pode pedir a devolução ou o reembolso em Minhas "
    "Compras > o pedido. A Shopee analisa a solicitação e devolve o valor pela mesma forma de "
    "pagamento, e o prazo para o dinheiro voltar é definido pela plataforma, que você "
    "acompanha no próprio pedido."
)
ITEM_A18 = "58265493964"
# A restrição da LOJA (store_info da Kia: RJ), como o `_com_fontes` a põe na ficha.
FICHA_A18 = {
    "de_onde": "cartão de produto que o cliente mandou",
    "item_id": ITEM_A18,
    "titulo": "Uranyx A18 Pro Max NFC 16.128 Gb Memoria S5 Premium",
    "restricao_envio": {
        "fonte": "loja",
        "fontes": ["loja"],
        "trecho": None,
        "ufs": ["RJ"],
        "ufs_conforme_pedido": [],
    },
}
LOJA_RJ = [{"restricao_envio": {"fonte": "loja", "ufs": ["RJ"], "ufs_conforme_pedido": []}}]
LOJA_RJ_CONFORME = [
    {"restricao_envio": {"fonte": "loja", "ufs": [], "ufs_conforme_pedido": ["RJ"]}}
]
APPLE_RJ = [
    {"restricao_envio": {"fonte": "regra_apple_shopee", "ufs": ["RJ"], "ufs_conforme_pedido": []}}
]
MOTIVO_7F0C = "afirma a entrega sem citar a restrição do anúncio (RJ)"

# 552f20ba (Barbosa ML pergunta, 21/10 23:23): a pergunta, o rascunho real e o fato.
PERGUNTA_552F = "Se trava no primeiro uso pode troca?"
RASCUNHO_552F = (
    "Olá! Não fazemos troca, nem pagando a diferença. A única solução é a devolução, "
    "solicitada pelo próprio app do Mercado Livre em Minhas compras, depois de receber o "
    "produto. Se você já comprou e o aparelho travar, fale com a loja pelas mensagens da "
    "compra."
)
FATO_17 = (
    "Não fazemos troca, nem pagando a diferença. A única solução é a devolução do pedido, "
    "solicitada pelo próprio app."
)
# A resposta que o item 21 quer: a orientação do defeito (sem a palavra "garantia", que
# pede o `ver_garantia` antes — a régua de sempre da `conferir_saida`).
CERTA_552F = (
    "Olá! Se o aparelho travar ou apresentar defeito, depois da compra fale com a loja pelas "
    "mensagens da compra e mande um vídeo curto e fotos do problema; a nossa equipe analisa. "
    "A devolução também pode ser pedida em Minhas compras."
)


def _codigos(pendencias: list) -> list[tuple[str, bool, bool]]:
    return [(p.codigo, p.so_auto, p.bloqueia) for p in pendencias]


SUAVE = (checagens.REGIAO_SEM_FONTE, True, False)


def _regiao(texto: str, fichas: list, cliente: str | list[str]) -> list:
    textos = [cliente] if isinstance(cliente, str) else cliente
    return checagens.conferir_regiao(texto, fichas, None, textos_cliente=textos)


# ═══════════════ o caminho do agente (sem modelo) ═══════════════


def _conversa(plataforma: str = "shopee", canal: str = "chat") -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid4(), plataforma=plataforma, canal=canal, dados={}, pedido_marketplace=None
    )


def _base(textos: list[str], modo: str) -> agente_ctx.BaseDoGerar:
    return agente_ctx.BaseDoGerar(
        recentes=[],
        rajada=[],
        foco=[],
        textos_cliente=textos,
        textos_rajada=textos,
        ctx={"pedido": None, "logistica": None, "chamados": [], "devolucoes": []},
        valores={},
        fatos={"anuncio": None},
        fatos_gravados={},
        categorias=manual_svc.categorias_padrao(),
        regras=[],
        sinais=[],
        motivos=[],
        opcoes_sensiveis=[],
        sinais_do_cliente=[],
        nomes=(),
        modo=modo,
        forcar=False,
        nf_politica_aprovada=False,
        insistiu=False,
    )


async def _caminho(
    p: Proposta,
    textos: list[str],
    *,
    modo: str = constantes.MODO_AUTO,
    fichas: list | None = None,
    conversa: SimpleNamespace | None = None,
    pedido: dict | None = None,
    permitir: tuple[list[str], list[str]] = ([], []),
) -> tuple[ia.SaidaConferida, Any, agente_ctx.ContextoAgente, agente_ctx.BaseDoGerar, Any]:
    """`ia.conferir_saida(agente=True)` + `checagens.conferir` + `checagens.aplicar`, como o
    `agente.responder` faz (o "Sugerir" só na `conferir_saida`, que sem sessão não lê se a
    loja respondeu; a base é a do automático)."""
    conversa = conversa or _conversa()
    fichas = list(fichas or [])
    ctx = agente_ctx.ContextoAgente.da_conversa(conversa, None, modo=modo)
    ids, textos_permitidos = permitir
    ctx.permitir(ids=ids, textos=textos_permitidos, produto=bool(fichas))
    base = _base(textos, modo)
    conferida = await ia.conferir_saida(
        None,
        conversa,
        SimpleNamespace(),
        **saida.para_conferir(p, base.ids_categorias),
        motivos=[],
        ctx=base.ctx,
        fatos={"anuncio": None, "ficha_do_anuncio": fichas},
        valores={},
        categorias=base.categorias,
        regras=[],
        textos_cliente=textos,
        textos_rajada=textos,
        fichas=fichas,
        garantias_conferidas=[],
        permitidos_extra=ctx.permitidos_extra(),
        modo=modo,
        forcar=True,
        agente=True,
    )
    chk = checagens.conferir(p, conferida, ctx, base, fichas=fichas, gatilho=None, pedido=pedido)
    final = checagens.aplicar(conferida, chk, acoes_pessoa=[], modo=modo)
    return final, chk, ctx, base, conversa


def _proposta(texto: str, assunto: str, evidencias: list[str], confianca: float = 0.9) -> Proposta:
    return Proposta(
        acao="responder",
        texto=texto,
        assunto=assunto,
        confianca=confianca,
        acao_pessoa=None,
        motivo_pessoa="",
        evidencias=evidencias,
    )


# Os assuntos liberados da Kia (auto_orientacao com os de orientação; o rastreio em
# auto_categorias/auto_agente): o 7f0c1b4e tem as pistas reembolso, troca_devolucao e
# rastreio.
KIA = SimpleNamespace(
    auto_categorias=["rastreio", "acessorios_avulsos", "cobertura_regiao"],
    auto_agente=["rastreio", "acessorios_avulsos", "cobertura_regiao"],
    auto_orientacao=list(constantes.ASSUNTOS_ORIENTACAO),
)


def _rascunho(final: ia.SaidaConferida, ctx: agente_ctx.ContextoAgente, **agente: Any) -> Any:
    return SimpleNamespace(
        prompt_versao="ag1",
        categoria=final.categoria,
        fatos={
            "agente": {
                **ctx.para_fatos(),
                "sem_auto": list(final.sem_auto),
                "avisos_auto": list(final.avisos_auto),
                **agente,
            }
        },
    )


# ═══════════════ 17. desfeito: a região é a do d64ce17d ═══════════════
#
# 3ª contestação da 5ª rodada (22/10/2026): a leitura que tirava a chegada do pedido
# da fala do cliente ("comprei… assim que chegar posso devolver?") só tinha o texto
# livre dos dois lados para decidir — o 7f0c1b4e não tinha pedido na conversa. Três
# contestações seguidas acharam rascunhos ruins que ela deixava sair sozinhos, e cada
# furo fechado pedia mais uma lista de palavras (o protótipo da 3ª fechava os achados
# e deixava passar os vizinhos seguintes: "comprei a mala…", "em S Gonçalo"). O item 17
# foi desfeito: a `conferir_regiao` é a do d64ce17d e o 7f0c1b4e volta ao aviso suave
# do ao vivo (um clique de pessoa). Os casos das três contestações ficam como guarda.


@pytest.mark.parametrize("fichas", [LOJA_RJ, LOJA_RJ_CONFORME], ids=["ufs", "conforme_pedido"])
def test_17_o_7f0c1b4e_volta_ao_aviso_do_ao_vivo(fichas):
    """O rascunho real com as 3 falas reais: o aviso suave do ao vivo, o custo aceito."""
    r = _regiao(RASCUNHO_7F0C, fichas, FALAS_7F0C)
    assert _codigos(r) == [SUAVE]
    if fichas is LOJA_RJ:
        assert [p.motivo for p in r] == [MOTIVO_7F0C]


def test_17_a_regiao_e_a_do_d64ce17d():
    """Sem o `com_pedido` e sem a leitura da chegada (nem o lugar na 3ª pessoa)."""
    assert "com_pedido" not in inspect.signature(checagens.conferir_regiao).parameters
    for nome in (
        "_falas_sem_a_chegada",
        "_sem_a_chegada_do_pedido",
        "_nada_da_chegada_sai",
        "_fala_um_lugar",
        "_lugar_sem_uf_nas_falas",
    ):
        assert not hasattr(checagens, nome), nome
    assert not checagens._cliente_fala_de_lugar_sem_uf(["minha mãe mora em Itaperuna"])
    assert checagens._cliente_fala_de_lugar_sem_uf(["moro em Itaperuna"])


@pytest.mark.parametrize(
    "cliente",
    [
        "meu pedido ainda não chegou, quando receber posso trocar a cor?",
        "comprei e já chegou, posso devolver?",
        "paguei ontem, depois que chegar dá pra devolver?",
        "comprei, quando chegar dá pra trocar de cor?",
        "minha encomenda não veio ainda; logo que chegar eu devolvo?",
    ],
)
def test_17_a_chegada_do_pedido_feito_continua_suave(cliente):
    texto = "Sim, quando receber, você pode abrir a devolução em Minhas Compras > o pedido."
    assert _codigos(_regiao(texto, LOJA_RJ, cliente)) == [SUAVE]


@pytest.mark.parametrize(
    ("cliente", "texto"),
    [
        ("chega aqui?", "Sim, chega!"),
        ("quando chega aqui?", "Sim, chega sim!"),
        ("se eu comprar hoje, quando chegar aqui?", "Sim, chega sim!"),
        (
            "se eu comprar esse outro, quando chegar posso trocar?",
            "Sim, quando chegar você pode trocar.",
        ),
        ("vou comprar, quando chegar posso devolver?", "Sim, quando chegar você pode devolver."),
        ("ainda não comprei, chega aqui?", "Sim, chega sim!"),
        ("ainda não fiz a compra, chega aqui?", "Sim, chega sim!"),
        (
            "ainda não fiz meu pedido, quando chegar posso trocar?",
            "Sim, quando chegar você pode trocar em Minhas Compras > o pedido.",
        ),
        (
            "não fiz um pedido ainda. Se chegar e eu não gostar, devolvo?",
            "Sim, depois que chegar você pode devolver em Minhas Compras.",
        ),
        (
            "nunca comprei aqui, quando chegar posso devolver?",
            "Sim, depois que chegar você pode devolver em Minhas Compras.",
        ),
        ("moro em Itaperuna, entregam?", "Sim, entregamos!"),
        ("quando chegar posso trocar?", "Sim, quando chegar, você pode abrir a troca."),
    ],
)
def test_17_antes_da_compra_a_pergunta_de_entrega_continua_suave(cliente, texto):
    assert _codigos(_regiao(texto, LOJA_RJ, cliente)) == [SUAVE]


@pytest.mark.parametrize(
    ("cliente", "texto"),
    [
        ("comprei, vocês entregam aqui?", "Sim, entregamos!"),
        ("comprei, chega aí?", "Sim, chega sim!"),
        ("comprei e ainda não chegou, vai chegar?", "Sim, vai chegar normalmente."),
        ("comprei, moro em Itaperuna, chega?", "Sim, chega sim!"),
        ("comprei ontem, quando vai chegar aqui?", "Sim, chega sim!"),
        ("comprei ontem, quando vai chegar?", "Sim, vai chegar normalmente!"),
        ("comprei, quando chegar aqui posso devolver?", "Sim, quando chegar você pode devolver."),
        (
            "comprei, quando chegar em Itaperuna posso devolver?",
            "Sim, quando chegar você pode devolver em Minhas Compras.",
        ),
        (
            "comprei pra minha mãe que mora em Itaperuna, quando chegar posso devolver?",
            "Sim, depois que chegar você pode devolver em Minhas Compras.",
        ),
        (
            "minha mãe mora em Itaperuna, comprei pra ela. Quando chegar posso devolver?",
            "Sim, depois que chegar você pode devolver em Minhas Compras.",
        ),
        (
            "meu pedido ainda não chegou no correio, quando receber posso devolver?",
            "Sim, quando receber você pode devolver em Minhas Compras > o pedido.",
        ),
    ],
)
def test_17_depois_da_compra_a_pergunta_de_entrega_continua_suave(cliente, texto):
    assert _codigos(_regiao(texto, LOJA_RJ, cliente)) == [SUAVE]


@pytest.mark.parametrize(
    ("fichas", "cliente", "texto"),
    [
        (
            APPLE_RJ,
            "vocês enviam para o RJ?",
            "Olá! Sim, enviamos para o RJ normalmente pela Shopee.",
        ),
        (
            LOJA_RJ,
            "comprei e ainda não chegou, vocês enviam pro RJ?",
            "Sim, enviamos para o RJ normalmente.",
        ),
        (LOJA_RJ, "comprei, mas ainda não chegou", "Sim, entregamos para todo o Brasil."),
        (
            LOJA_RJ,
            "moro em Niterói. se eu comprar e não gostar, posso devolver?",
            "Olá! Sim, depois que o produto chegar, você pode pedir a devolução em Minhas "
            "Compras > o pedido.",
        ),
        (
            LOJA_RJ,
            "comprei, moro em Niterói. quando chegar posso devolver?",
            "Sim, depois que chegar você pode devolver em Minhas Compras.",
        ),
    ],
)
def test_17_a_entrega_no_estado_restrito_continua_pessoa(fichas, cliente, texto):
    """O estado (ou a cidade) do cliente e a afirmação: pessoa nos dois modos, como antes."""
    r = _regiao(texto, fichas, cliente)
    assert (checagens.REGIAO_CONTRADIZ, False, False) in _codigos(r)


# O pedido da conversa no caminho do agente (o do ao vivo não tinha; o bloco do
# `pacote`, sem dado pessoal).
_BLOCO = {"numero": "{numero_pedido}", "postado": "sim", "enviado": True}
BLOCO_A_CAMINHO = {**_BLOCO, "entregue": False}
BLOCO_ENTREGUE = {**_BLOCO, "entregue": True}


@pytest.mark.parametrize("pedido", [None, BLOCO_A_CAMINHO], ids=["sem_pedido", "com_pedido"])
async def test_17_o_rascunho_real_no_caminho_do_agente_e_o_do_ao_vivo(pedido):
    """O 7f0c1b4e pelo caminho inteiro, no automático, com a ficha e a restrição da loja:
    exatamente o desfecho do ao vivo — o aviso da região, pessoa, `sem_auto`; o pedido na
    conversa não muda nada."""
    p = _proposta(RASCUNHO_7F0C, "troca_devolucao", [f"ficha:{ITEM_A18}"])
    permitir = ([f"ficha:{ITEM_A18}"], [json.dumps([FICHA_A18], ensure_ascii=False)])
    pistas = {
        "pistas_turno": ["reembolso", "troca_devolucao", "rastreio"],
        "pistas_orientacao": ["troca_devolucao", "reembolso"],
    }
    final, chk, ctx, base, conversa = await _caminho(
        p, FALAS_7F0C, fichas=[FICHA_A18], permitir=permitir, pedido=pedido
    )
    assert [x.codigo for x in chk.pendencias] == [checagens.REGIAO_SEM_FONTE]
    assert final.motivos == [MOTIVO_7F0C] and final.sem_auto == [MOTIVO_7F0C]
    assert v.por_que_nao_verifica(final, p, ctx, base, conversa) == v.NAO_RODA_PESSOA
    assert ia.trava_do_agente(_rascunho(final, ctx, **pistas), KIA) == "sem_auto"


async def test_17_o_pedido_na_conversa_nao_muda_a_regiao():
    """`conferir` não leva o pedido à região: "já chegou, posso trocar?" fica com o aviso
    com e sem o pedido na conversa (como no d64ce17d)."""
    texto = "Sim, você pode abrir a troca em Minhas Compras > o pedido."
    p = _proposta(texto, "troca_devolucao", [])
    com, *_ = await _caminho(p, ["já chegou, posso trocar?"], fichas=LOJA_RJ, pedido=BLOCO_ENTREGUE)
    sem, *_ = await _caminho(p, ["já chegou, posso trocar?"], fichas=LOJA_RJ)
    motivo = "afirma a entrega sem citar a restrição do anúncio (RJ)"
    assert motivo in com.motivos and motivo in com.sem_auto
    assert motivo in sem.motivos and motivo in sem.sem_auto


async def _o_aviso_no_caminho(casos: list[tuple[str, str]], pedido: dict | None) -> None:
    """Cada (texto, fala) pelo caminho inteiro, no automático, com a ficha e a restrição
    da loja da Kia: o aviso da região, pessoa, o verificador não roda e a trava fecha."""
    permitir = ([f"ficha:{ITEM_A18}"], [json.dumps([FICHA_A18], ensure_ascii=False)])
    pistas = {
        "pistas_turno": ["troca_devolucao", "reembolso"],
        "pistas_orientacao": ["troca_devolucao", "reembolso"],
    }
    for texto, cliente in casos:
        p = _proposta(texto, "troca_devolucao", [f"ficha:{ITEM_A18}"])
        final, chk, ctx, base, conversa = await _caminho(
            p, [cliente], fichas=[FICHA_A18], permitir=permitir, pedido=pedido
        )
        assert checagens.REGIAO_SEM_FONTE in [x.codigo for x in chk.pendencias], (texto, cliente)
        assert final.precisa_humano and final.sem_auto, (texto, cliente)
        assert v.por_que_nao_verifica(final, p, ctx, base, conversa) == v.NAO_RODA_PESSOA
        assert ia.trava_do_agente(_rascunho(final, ctx, **pistas), KIA) == "sem_auto"


# ── 17. 1ª contestação da 5ª rodada (22/10/2026): os furos da 1ª versão ──
#
# Cada caso abaixo ficava SUAVE no d64ce17d e saía sem pendência nenhuma com a 1ª
# versão do item 17 (a compra por fazer, "meu pedido" sem o estado dele, a compra não
# finalizada, qualquer lugar na fala, o lugar depois do sujeito, a compra antiga).

# O rascunho que dá a chegada como certa antes da compra (o `_REGIAO` do prompt:
# com a restrição da LOJA, antes da compra, é pessoa).
CHEGA_CERTO = (
    "Pode comprar tranquilo: chega certinho e, quando chegar, você pode pedir a devolução em "
    "Minhas Compras > o pedido."
)
DEVOLVE_DEPOIS = "Sim, depois que chegar você pode devolver em Minhas Compras > o pedido."
ANTES_DA_COMPRA = [
    # "meu pedido"/"minha compra" sem o estado dele e a compra por fazer.
    "Vou fechar meu pedido agora. Quando chegar dá pra devolver se eu não gostar?",
    "Se eu fizer meu pedido hoje, quando chegar posso devolver?",
    "Quero fazer meu pedido hoje, quando chegar posso devolver se não servir?",
    "Pretendo fazer meu pedido amanhã, quando chegar posso devolver?",
    "Gostaria de fazer minha encomenda, quando chegar posso devolver?",
    "Antes de fechar minha compra: quando chegar posso devolver?",
    "vou fazer minha compra amanhã, quando chegar dá pra devolver?",
    "quero fechar meu pedido hoje. assim que chegar posso trocar a cor?",
    "meu pedido vai ser de 2 unidades, quando chegar posso devolver uma?",
    # A compra não finalizada.
    "Ainda não finalizei minha compra, quando chegar posso devolver?",
    "Ainda não fechei meu pedido, quando chegar posso devolver?",
    # Fora da lista curta (com o pedido na conversa, antes passava tudo).
    "Caso eu compre mais um, quando chegar posso devolver?",
    "Gostaria de levar mais um pro meu irmão. Quando chegar ele pode devolver se não gostar?",
    "Tenho interesse em comprar, se chegar com defeito posso devolver?",
    "To pensando em comprar, quando chegar posso devolver?",
    "Gostaria de comprar. Se chegar com defeito posso devolver?",
    "Se eu efetuar o pedido hoje, quando chegar posso devolver?",
    "Caso eu adquira, quando chegar posso devolver?",
]
LUGAR_NA_FALA = [
    # O lugar na 3ª pessoa fora de "mora/reside" e com advérbio no meio.
    "Comprei pra minha filha que vive em Itaperuna, quando chegar posso devolver?",
    "Comprei pra minha mãe que é de Itaperuna, quando chegar posso devolver?",
    "Comprei pra minha mãe que mora lá em Itaperuna, quando chegar posso devolver?",
    "Comprei pra minha avó, ela tá em Itaperuna, quando chegar posso devolver?",
    "Comprei pra minha avó, ela tá na Penha, quando chegar posso devolver?",
    "Comprei, assim que chegar certinho em Itaperuna posso devolver?",
    # O lugar depois do sujeito ("chegar o aparelho em X").
    "comprei pra minha mãe, quando chegar o aparelho em Itaperuna posso devolver se ela não "
    "gostar?",
    "comprei, quando chegar o aparelho aqui posso devolver?",
    # O lugar com "no/na/até" depois de uma palavra (o `_LUGAR_DO_CLIENTE` lia).
    "comprei, quando chegar logo no Méier posso devolver?",
]


@pytest.mark.parametrize("cliente", ANTES_DA_COMPRA)
def test_17_contestacao_antes_da_compra_continua_suave(cliente):
    assert _codigos(_regiao(CHEGA_CERTO, LOJA_RJ, cliente)) == [SUAVE]


@pytest.mark.parametrize("cliente", LUGAR_NA_FALA)
def test_17_contestacao_o_lugar_na_fala_continua_suave(cliente):
    """O mesmo texto do regiao-16 (ruim na calibração), com o lugar dito de outro jeito."""
    assert _codigos(_regiao(DEVOLVE_DEPOIS, LOJA_RJ, cliente)) == [SUAVE]


@pytest.mark.parametrize(
    "falas",
    [
        ["minha filha vive em Itaperuna", "comprei pra ela, quando chegar posso devolver?"],
        ["ela tá na Penha", "comprei, quando chegar posso devolver?"],
        ["quero comprar outro", "comprei o primeiro e adorei, quando chegar posso devolver?"],
        ["Já comprei com vocês antes, quando chegar posso devolver?"],
        ["comprei aqui outras vezes. Quando chegar posso devolver?"],
    ],
)
def test_17_contestacao_a_outra_fala_e_a_compra_antiga_continuam_suaves(falas):
    assert _codigos(_regiao(DEVOLVE_DEPOIS, LOJA_RJ, falas)) == [SUAVE]


@pytest.mark.parametrize("pedido", [None, BLOCO_ENTREGUE], ids=["sem_pedido", "com_pedido"])
async def test_17_contestacao_no_caminho_do_agente(pedido):
    await _o_aviso_no_caminho(
        [
            (
                CHEGA_CERTO,
                "Vou fechar meu pedido agora. Quando chegar dá pra devolver se eu não gostar?",
            ),
            (CHEGA_CERTO, "Caso eu compre mais um, quando chegar posso devolver?"),
            (
                DEVOLVE_DEPOIS,
                "comprei pra minha mãe, quando chegar o aparelho em Itaperuna posso "
                "devolver se ela não gostar?",
            ),
            (
                DEVOLVE_DEPOIS,
                "Comprei pra minha filha que vive em Itaperuna, quando chegar posso devolver?",
            ),
        ],
        pedido,
    )


# ── 17. 2ª contestação da 5ª rodada (22/10/2026): os furos da 2ª versão ──
#
# Cada caso abaixo ficava SUAVE no d64ce17d e saía sem pendência nenhuma depois da 1ª
# contestação: a compra que não é a de agora lida como feita, o lugar dito de um jeito
# que a guarda não lia e a pergunta de estoque de quem já comprou.

# O rascunho que diz a entrega com o verbo e o que só começa com "Sim" (o do 7f0c1b4e).
CHEGA_SIM = "Chega sim! Pode comprar tranquilo."
CHEGARAM_SIM = "Chegaram sim! Pode comprar tranquilo que chega certinho."
VAI_CHEGAR = (
    "Sim, vai chegar normalmente! Depois que chegar, a devolução é em Minhas Compras > o pedido."
)
ANTES_DA_COMPRA_2 = [
    # A compra que falhou, foi cancelada ou não terminou.
    "Minha compra não foi aprovada no cartão. Se passar no pix, quando chegar posso devolver "
    "se não servir?",
    "minha compra ta dando erro no pagamento, quando chegar posso trocar se nao gostar?",
    "minha compra foi recusada, vou tentar de novo. Quando chegar da pra devolver?",
    "meu pedido foi cancelado pelo sistema, quando chegar o novo posso devolver?",
    "Meu pedido nao foi aprovado ainda, ta em analise. quando chegar posso devolver se nao servir?",
    "Fiz um pedido mes passado e cancelaram. Se refizer, quando chegar posso devolver?",
    "minha encomenda foi barrada no cartão, quando chegar posso devolver se nao servir?",
    "oi, meu pedido foi cancelado do nada, vou fazer de novo. quando chegar posso devolver?",
    "opa, minha compra foi negada pelo banco. se chegar com defeito vcs trocam?",
    "paguei e cancelaram, ai fiquei com medo. quando chegar posso devolver se der problema?",
    "minha compra ta aguardando pagamento, quando chegar da pra devolver?",
    "Meu pedido não foi finalizado, deu erro. quando chegar posso devolver se nao servir?",
    "minha compra ta pendente, quando chegar posso devolver?",
    "meu pedido esta pronto pra fechar, quando chegar posso devolver?",
    "paguei o boleto mas venceu, quando chegar posso devolver?",
    "comprei mas nao finalizei o pagamento, quando chegar posso devolver?",
    "comprei? nao sei se foi, quando chegar posso devolver?",
    "comprei nada ainda, quando chegar posso devolver?",
    # O "não chegou/veio/recebi" de outra coisa.
    "o cupom nao veio, quando chegar posso devolver se nao gostar?",
    "nao recebi o codigo de desconto ainda. quando chegar posso devolver se nao servir?",
    "nao veio resposta no outro chat. Quando chegar posso devolver se nao gostar??",
    "a nota fiscal do outro nao veio, quando chegar esse posso devolver?",
    # A compra de outra coisa, de outra pessoa ou em outra loja, e a vontade de comprar.
    "Comprei um igual pro meu pai e chegou certinho. Quero um pra mim, quando chegar posso "
    "devolver se nao gostar?",
    "comprei a capinha semana passada e ja chegou, agora quero o celular. quando chegar posso "
    "devolver se nao gostar?",
    "paguei 1500 num celular que travava, por isso to com receio. quando chegar posso devolver "
    "se nao gostar?",
    "fiz o pedido errado e cancelei, quero o preto agora. quando chegar posso trocar se nao "
    "servir?",
    "Ja comprei 2 aqui e chegaram certinho, quero o terceiro. quando chegar posso devolver se "
    "nao gostar?",
    "comprei em outra loja e nunca chegou. quando chegar o de vcs posso devolver?",
    "comprei um parecido no ano passado, gostei. esse aqui quando chegar posso devolver se nao "
    "servir?",
    "Ainda nao comprei pq o do meu irmao nao chegou ainda. Quando chegar o meu posso devolver?",
    "ainda nao comprei, minha vizinha comprou e nao chegou. quando chegar o meu posso devolver?",
    "to em duvida, nao comprei ainda. meu primo disse que o dele nao veio. quando chegar posso "
    "devolver?",
    "comprei 1 no black friday e nao veio ate hoje, ai fiquei com receio. quando chegar esse "
    "posso devolver?",
    "comprei o fone ontem ja, agora to de olho no relogio. quando chegar posso devolver se nao "
    "gostar?",
    "comprei o carregador semana passada, agora to querendo o celular. quando chegar posso "
    "devolver?",
    "comprei ano passado e foi otimo, quando chegar esse novo posso devolver?",
    "comprei um ontem e chegou hoje, top! vou querer mais um, quando chegar posso devolver?",
    "minha irma comprou aqui e nao chegou. quando chegar o meu posso devolver?",
    "comprei 1 e chegou, queria outro pra minha mae, quando chegar posso devolver?",
    "comprei na black friday e chegou, quero mais 2, quando chegar posso devolver?",
    "Comprei um igual pro meu pai mes passado e chegou certinho. Quero um pra mim, se chegar "
    "antes do natal eu fecho",
    "minha compra nao foi aprovada no cartao. se chegar antes do dia 20 eu passo no pix",
]
LUGAR_NA_FALA_2 = [
    # O nome de 2 palavras com a 1ª curta ou na lista de não-lugar.
    "Comprei pra minha filha que vive em São Fidélis, quando chegar posso devolver?",
    "Comprei pra minha mãe que é de São Fidélis, quando chegar posso devolver?",
    "comprei pra minha avó, ela tá em Três Rios, quando chegar posso devolver?",
    "comprei pro meu tio que vive em Duas Barras, quando chegar posso devolver?",
    "comprei pra minha irma que trabalha em São Fidélis, quando chegar posso devolver?",
    "comprei, assim que chegar certinho em São Fidélis posso devolver?",
    "comprei pro meu filho que estuda em São Fidélis, quando chegar posso devolver?",
    "comprei pra minha mae, ela é de Bom Jesus do Itabapoana. quando chegar posso devolver?",
    "Comprei pro meu pai, ele é de Tres Rios. quando chegar ele pode devolver?",
    "comprei pra minha tia que é lá de Rio Bonito, quando chegar ela pode devolver?",
    "comprei pra minha prima la de Tres Rios, quando chegar posso devolver?",
    "comprei, vou receber la na casa da minha mae em Rio Claro. quando chegar posso devolver?",
    # "é lá de", "natural de", "daqui de", "minha mãe de X".
    "Comprei pra minha mãe, ela é lá de Itaperuna, quando chegar posso devolver?",
    "comprei pra minha sogra, ela é natural de Itaperuna, quando chegar posso devolver?",
    "comprei pra minha mãe que é natural de Natividade, quando chegar posso devolver?",
    "Comprei. Minha mãe é daqui de Itaperuna mesmo, quando chegar posso devolver?",
    "comprei pra minha mae de Itaperuna, quando chegar posso devolver?",
    "comprei pra minha tia la de Cachoeiras de Macacu, quando chegar posso devolver?",
    # A cidade, o bairro, a região, o endereço, o CEP, "aqui é", o gentílico.
    "comprei, moro na cidade de Itaperuna, quando chegar posso devolver?",
    "comprei pra minha irmã que mora na região de Itaperuna, quando chegar posso devolver?",
    "comprei, moro na regiao serrana, quando chegar posso devolver?",
    "comprei, moro no bairro do Méier, quando chegar posso devolver?",
    "comprei, sou do bairro do Meier. quando chegar posso devolver?",
    "comprei, minha casa e no Meier, quando chegar posso devolver?",
    "comprei, minha cidade e Itaperuna, quando chegar posso devolver?",
    "comprei, bairro Penha, quando chegar posso devolver?",
    "comprei, moro perto de Campos, quando chegar posso devolver?",
    "comprei, meu cep é 28300-000, quando chegar posso devolver?",
    "comprei, cep 28300000. quando chegar posso devolver?",
    "comprei, 28300-000, quando chegar posso devolver?",
    "comprei pra minha mae, endereço dela é Itaperuna, quando chegar posso devolver?",
    "comprei pra minha mae, endereco dela: Rua Dez, 50, Itaperuna. quando chegar posso devolver?",
    "Comprei, aqui é Itaperuna, quando chegar posso devolver?",
    "comprei, sou itaperunense, quando chegar posso devolver?",
    # "pro Méier" (não é infinitivo) e o "no/na X".
    "comprei, quando chegar direitinho pro Méier posso devolver?",
    "comprei pra minha mae no Meier, quando chegar posso devolver?",
    "comprei, aqui na Penha, quando chegar posso devolver?",
]
LUGAR_EM_OUTRA_FALA_2 = [
    ["minha mae vive em sao fidelis", "comprei pra ela, quando chegar posso devolver?"],
    ["ela é lá de Itaperuna", "comprei pra ela, quando chegar posso devolver?"],
    ["moro no bairro do meier", "comprei ontem, assim que chegar posso trocar?"],
]
# A pergunta de estoque de quem já comprou (o pedido antigo na conversa).
ESTOQUE_COM_PEDIDO = [
    "ja chegaram as capinhas do A18? vou querer duas",
    "o outro chegou rapidinho, quero mais dois pra presente",
    "O azul já chegou? quero um pra minha esposa",
    "chegou o modelo novo? quero o de 256",
]
# Depois da compra, sem lugar e sem outra compra: o ganho da 2ª versão do item 17 (o
# 7f0c1b4e e os vizinhos dele) — com o item desfeito, o aviso de antes (um clique).
DEPOIS_DA_COMPRA_SEM_LUGAR = [
    "comprei e ainda nao chegou, quando chegar posso devolver?",
    "meu pedido ainda nao chegou, quando chegar posso trocar?",
    "paguei ontem, depois que chegar da pra devolver?",
    "comprei o celular semana passada e ainda nao chegou, quando chegar posso devolver?",
    "comprei ontem a noite, assim que chegar posso devolver se nao gostar?",
    "fiz o pedido dia 10 e nao chegou, quando chegar posso pedir reembolso?",
    "comprei mas o rastreio ta parado, quando chegar posso devolver?",
    "minha encomenda ainda nao veio, quando chegar posso devolver?",
    "comprei pra minha mae, quando chegar ela pode devolver se nao gostar?",
    "comprei 2, quando chegar posso devolver 1?",
    "comprei pelo pix, quando chegar posso devolver?",
    "comprei com cupom, quando chegar posso devolver?",
    "meu pedido ta atrasado, quando chegar posso devolver?",
    "comprei, quando chegar quero devolver, como faco?",
    "comprei, quando chegar queria saber se posso devolver",
    "comprei, tenho uma duvida: quando chegar posso devolver?",
    "paguei e nada ainda, quando chegar posso devolver?",
    checagens._plano(FALAS_7F0C[1]),
]


@pytest.mark.parametrize("cliente", ANTES_DA_COMPRA_2)
@pytest.mark.parametrize("texto", [CHEGA_CERTO, DEVOLVE_DEPOIS])
def test_17_contestacao2_a_compra_incerta_continua_suave(cliente, texto):
    assert _codigos(_regiao(texto, LOJA_RJ, cliente)) == [SUAVE]


@pytest.mark.parametrize("cliente", [*LUGAR_NA_FALA_2, *LUGAR_EM_OUTRA_FALA_2])
@pytest.mark.parametrize("texto", [DEVOLVE_DEPOIS, VAI_CHEGAR])
def test_17_contestacao2_o_lugar_dito_de_outro_jeito_continua_suave(cliente, texto):
    """O regiao-16/18 (ruim na calibração) com o lugar dito de outro jeito."""
    assert _codigos(_regiao(texto, LOJA_RJ, cliente)) == [SUAVE]


@pytest.mark.parametrize("cliente", ESTOQUE_COM_PEDIDO)
@pytest.mark.parametrize("texto", [CHEGARAM_SIM, CHEGA_SIM, "Sim!"])
def test_17_contestacao2_a_pergunta_de_estoque_continua_suave(cliente, texto):
    assert _codigos(_regiao(texto, LOJA_RJ, cliente)) == [SUAVE]


@pytest.mark.parametrize("cliente", DEPOIS_DA_COMPRA_SEM_LUGAR)
@pytest.mark.parametrize(
    "texto",
    [
        DEVOLVE_DEPOIS,
        RASCUNHO_7F0C,
        CHEGA_CERTO,
        CHEGA_SIM,
        VAI_CHEGAR,
        "Sim, chega certinho! Depois que chegar você pode devolver em Minhas Compras > o pedido.",
        "Sim, entregamos normalmente. Quando chegar, a devolução é em Minhas Compras.",
    ],
)
def test_17_contestacao2_depois_da_compra_o_sim_continua_suave(cliente, texto):
    """Sem o item 17, o "Sim" da devolução depois da compra dita é o aviso de antes —
    como a frase que diz a entrega com o verbo."""
    assert _codigos(_regiao(texto, LOJA_RJ, cliente)) == [SUAVE]


@pytest.mark.parametrize("pedido", [None, BLOCO_ENTREGUE], ids=["sem_pedido", "com_pedido"])
async def test_17_contestacao2_no_caminho_do_agente(pedido):
    await _o_aviso_no_caminho(
        [
            (
                CHEGA_CERTO,
                "Ainda nao comprei pq o do meu irmao nao chegou ainda. Quando chegar o meu "
                "posso devolver?",
            ),
            (
                DEVOLVE_DEPOIS,
                "Comprei um igual pro meu pai e chegou certinho. Quero um pra mim, quando "
                "chegar posso devolver se nao gostar?",
            ),
            (
                DEVOLVE_DEPOIS,
                "meu pedido foi cancelado pelo sistema, quando chegar o novo posso devolver?",
            ),
            (
                DEVOLVE_DEPOIS,
                "Comprei pra minha filha que vive em São Fidélis, quando chegar posso devolver?",
            ),
            (DEVOLVE_DEPOIS, "comprei, quando chegar direitinho pro Méier posso devolver?"),
            (VAI_CHEGAR, "comprei, cep 28300000. quando chegar posso devolver?"),
            (CHEGARAM_SIM, "ja chegaram as capinhas do A18? vou querer duas"),
        ],
        pedido,
    )


# ── 17. 3ª contestação da 5ª rodada (22/10/2026): os furos da 3ª versão ──
#
# Cada caso abaixo ficava SUAVE no d64ce17d e saía sem pendência nenhuma no 3ccaf10f
# (a 3ª versão do item 17), com a trava da Kia aberta — só o verificador:
#   - o "Sim/Claro" que promete a entrega sem um verbo do `_AFIRMA_ENTREGA` ("vai
#     direitinho", "você recebe em casa", "está a caminho", "a gente envia"), também
#     na outra frase do texto;
#   - a compra de um ACESSÓRIO (ou de outra coisa) com o aparelho ainda por comprar;
#   - o lugar abreviado ou com sigla ("S. Gonçalo", "Sto. Antônio de Pádua", "de SG").
# O protótipo da revisão (mais três listas: a ação do "Sim", os acessórios, as
# abreviações com ponto) fechava estes e deixava passar os vizinhos marcados abaixo.
DEVOLVE_DEPOIS_DO_APARELHO = (
    "Sim, depois que o aparelho chegar, você pode pedir a devolução ou o reembolso em Minhas "
    "Compras > o pedido."
)
VOCE_RECEBE = (
    "Claro! Você recebe em casa e, depois que chegar, pode pedir a devolução em Minhas Compras."
)
POS_VENDA_3 = [
    "comprei ontem, quando chegar posso devolver se não gostar?",
    "meu pedido ainda não chegou, quando chegar posso devolver?",
    "Comprei o aparelho mas ainda não chegou, assim que chegar posso devolver?",
]
PROMETE_SEM_O_VERBO = [
    "Sim, ele vai direitinho.",
    VOCE_RECEBE,
    "Sim, depois que o aparelho chegar, você pode pedir a devolução em Minhas Compras > o "
    "pedido. Fica tranquila que ele vai direitinho.",
    "Sim, o produto vai até sua casa.",
    "Claro! Vai sim!",
    "Sim, está a caminho.",
    "Sim, você vai receber.",
    "Sim, a gente envia.",
    "Sim! Ele vai direitinho e, quando chegar, você pode pedir a devolução.",
]
COMPRA_DE_OUTRA_COISA = [
    "comprei a capinha ontem. o celular quando chegar dá pra devolver se não gostar?",
    "paguei o boleto do fone. o celular quando chegar posso devolver?",
    "Já paguei a capinha. E o aparelho, quando chegar posso devolver?",
    "comprei 2 capinhas ontem, quando chegar o A18 posso devolver se não servir?",
    "Paguei a película. Agora o celular: quando chegar posso devolver se não gostar?",
    # Os vizinhos que a lista dos acessórios do protótipo deixava passar.
    "comprei a mala ontem. o celular quando chegar dá pra devolver se não gostar?",
    "comprei uma airfryer de vocês. o celular quando chegar posso devolver?",
]
LUGAR_ABREVIADO = [
    "comprei pra minha mãe em S. Gonçalo, quando chegar posso devolver se ela não gostar?",
    "comprei, sou de S. Gonçalo, quando chegar posso devolver?",
    "comprei pra minha avó de Sto. Antônio de Pádua, quando chegar posso devolver?",
    "comprei, fica em S. Fidélis, quando chegar posso devolver?",
    "comprei pra minha mae, ela é de SG, quando chegar posso devolver?",
    "comprei pro meu marido q trabalha em S. Gonçalo, quando chegar posso devolver?",
    # Os vizinhos que as abreviações com ponto do protótipo deixavam passar.
    "comprei pra minha mãe em S Gonçalo, quando chegar posso devolver se ela não gostar?",
    "comprei pra minha mãe lá em S.G., quando chegar posso devolver?",
]


@pytest.mark.parametrize("cliente", POS_VENDA_3)
@pytest.mark.parametrize("texto", PROMETE_SEM_O_VERBO)
def test_17_contestacao3_o_sim_que_promete_a_entrega_sem_o_verbo_e_suave(cliente, texto):
    assert _codigos(_regiao(texto, LOJA_RJ, cliente)) == [SUAVE]


@pytest.mark.parametrize("cliente", COMPRA_DE_OUTRA_COISA)
@pytest.mark.parametrize("texto", [DEVOLVE_DEPOIS_DO_APARELHO, VOCE_RECEBE])
def test_17_contestacao3_a_compra_de_outra_coisa_e_suave(cliente, texto):
    assert _codigos(_regiao(texto, LOJA_RJ, cliente)) == [SUAVE]


@pytest.mark.parametrize("cliente", LUGAR_ABREVIADO)
def test_17_contestacao3_o_lugar_abreviado_e_suave(cliente):
    assert _codigos(_regiao(DEVOLVE_DEPOIS_DO_APARELHO, LOJA_RJ, cliente)) == [SUAVE]


@pytest.mark.parametrize("pedido", [None, BLOCO_A_CAMINHO], ids=["sem_pedido", "com_pedido"])
async def test_17_contestacao3_no_caminho_do_agente(pedido):
    await _o_aviso_no_caminho(
        [
            *((t, c) for c in POS_VENDA_3 for t in PROMETE_SEM_O_VERBO),
            *(
                (t, c)
                for c in COMPRA_DE_OUTRA_COISA
                for t in (DEVOLVE_DEPOIS_DO_APARELHO, VOCE_RECEBE)
            ),
            *((DEVOLVE_DEPOIS_DO_APARELHO, c) for c in LUGAR_ABREVIADO),
        ],
        pedido,
    )


# ═══════════════ 21. a recusa padrão da troca não é para defeito ═══════════════


def test_21_o_552f20ba_ganha_a_regra_suave():
    r = checagens.conferir_troca_negada_no_defeito(RASCUNHO_552F, textos_cliente=[PERGUNTA_552F])
    assert _codigos(r) == [(checagens.TROCA_NEGADA_NO_DEFEITO, True, False)]
    assert r[0].motivo == checagens.MOTIVO_TROCA_NEGADA_NO_DEFEITO


def test_21_so_o_verificador_pegava():
    """A 1ª camada de antes deixava passar: o relato do defeito é só PERGUNTADO (sem o
    sintoma afirmado) e o "diferença" livra a frase das réguas de recusa do validador
    (o legado não muda)."""
    assert not checagens._relata_defeito([PERGUNTA_552F], com_pedido=False)
    assert not validador.recusa_cobertura(RASCUNHO_552F)
    assert not validador.recusa_por_estrutura(RASCUNHO_552F)
    for regra in (
        checagens.conferir_orientacao_do_defeito(
            RASCUNHO_552F,
            acao="responder",
            assunto="troca_devolucao",
            textos_cliente=[PERGUNTA_552F],
            com_pedido=False,
        ),
        checagens.conferir_troca_no_lugar_da_devolucao(
            RASCUNHO_552F, acao="responder", textos_cliente=[PERGUNTA_552F]
        ),
        checagens.conferir_compra_nova_no_defeito(RASCUNHO_552F, textos_cliente=[PERGUNTA_552F]),
    ):
        assert regra == []


@pytest.mark.parametrize(
    "cliente",
    [
        PERGUNTA_552F,
        "e se vier com defeito, vocês trocam?",
        "e se parar de funcionar, troca?",
        "caso dê problema, posso trocar?",
        "se quebrar no primeiro dia tem troca?",
        "travou, quero trocar",
        "o celular não liga, quero trocar",
        "chegou quebrado, troca?",
        "parou de carregar, vocês trocam?",
        "veio com defeito, quero outro",
    ],
)
@pytest.mark.parametrize(
    "texto",
    [
        FATO_17,
        RASCUNHO_552F,
        "Olá! Não trocamos produto; a única opção é a devolução pelo app.",
        "Infelizmente não é possível trocar. A devolução é pelo app.",
        "Troca a gente não faz; a devolução é pelo app.",
        "O único caminho é a devolução pelo próprio app.",
    ],
)
def test_21_a_recusa_da_troca_a_quem_fala_de_defeito_e_suave(cliente, texto):
    r = checagens.conferir_troca_negada_no_defeito(texto, textos_cliente=[cliente])
    assert _codigos(r) == [(checagens.TROCA_NEGADA_NO_DEFEITO, True, False)], (cliente, texto)


@pytest.mark.parametrize(
    "cliente",
    [
        "quero trocar pelo azul",
        "quero trocar de cor",
        "não gostei, posso trocar?",
        "posso trocar se não servir?",
        "comprei o modelo errado, posso trocar pelo pro max?",
        "dá pra trocar pela versão de 256?",
        "pago a diferença e vocês trocam pelo maior?",
        "o reembolso caiu? quero trocar a cor",
        "deu problema no pagamento, posso trocar a cor?",
        "se for para o Rio chega quando? posso trocar de cor?",
        # Contestação da 5ª rodada: o sintoma só PERGUNTADO antes da compra é
        # característica (a régua da rodada 4), o problema do cartão não é defeito e o
        # dano negado não é dano.
        "ele é lento? se eu não gostar posso trocar pelo pro pagando a diferença?",
        "a mala tem trava TSA? se não gostar troca pagando a diferença?",
        "a tela desliga sozinha quando guarda? posso trocar pagando a diferença?",
        "deu problema no cartão, posso trocar a cor?",
        "não veio danificado, só não gostei, posso trocar pelo azul?",
    ],
)
def test_21_sem_defeito_a_recusa_padrao_fica(cliente):
    """A recusa padrão no lugar dela (arrependimento, outra cor ou modelo, a diferença)."""
    for texto in (FATO_17, RASCUNHO_552F):
        assert checagens.conferir_troca_negada_no_defeito(texto, textos_cliente=[cliente]) == []


@pytest.mark.parametrize(
    "texto",
    [
        CERTA_552F,
        # A orientação aprovada do defeito (prompt.ORIENTACAO["defeito"]).
        "Olá! Sentimos muito. Envie um vídeo curto do problema e fotos por aqui; a nossa equipe "
        "analisa e responde por aqui. Troca, devolução ou reembolso seguem depois da análise.",
        # A recusa só da troca SEM defeito na frase, e o defeito com a orientação.
        "Troca de cor ou de modelo não fazemos. Se o aparelho travar, envie um vídeo curto e "
        "fotos pelas mensagens da compra; a nossa equipe analisa.",
        "",
    ],
)
def test_21_a_orientacao_do_defeito_passa(texto):
    assert checagens.conferir_troca_negada_no_defeito(texto, textos_cliente=[PERGUNTA_552F]) == []


def test_21_a_recusa_com_o_defeito_na_mesma_frase_nao_se_livra():
    texto = "Troca de modelo não fazemos, nem com defeito."
    r = checagens.conferir_troca_negada_no_defeito(texto, textos_cliente=[PERGUNTA_552F])
    assert _codigos(r) == [(checagens.TROCA_NEGADA_NO_DEFEITO, True, False)]


async def test_21_o_rascunho_real_no_caminho_do_agente():
    """O 552f20ba pelo caminho inteiro: no automático, pessoa (o verificador nem roda); no
    copiloto, o aviso. A resposta certa passa a 1ª camada e vai ao verificador."""
    conversa = _conversa("ml", "pergunta")
    permitir = (["fato:17", "regra:troca_devolucao"], [json.dumps({"fatos": [{"texto": FATO_17}]})])
    p = _proposta(RASCUNHO_552F, "troca_devolucao", ["fato:17", "regra:troca_devolucao"], 0.85)
    auto, chk, ctx, base, _ = await _caminho(
        p, [PERGUNTA_552F], conversa=conversa, permitir=permitir
    )
    motivo = checagens.MOTIVO_TROCA_NEGADA_NO_DEFEITO
    assert [x.codigo for x in chk.pendencias] == [checagens.TROCA_NEGADA_NO_DEFEITO]
    assert motivo in auto.motivos and motivo in auto.sem_auto and motivo in auto.avisos
    assert auto.precisa_humano and auto.status == constantes.RASCUNHO_PENDENTE
    assert auto.texto == RASCUNHO_552F
    assert v.por_que_nao_verifica(auto, p, ctx, base, conversa) == v.NAO_RODA_PESSOA

    copiloto, *_ = await _caminho(
        p, [PERGUNTA_552F], conversa=conversa, modo=constantes.MODO_COPILOTO, permitir=permitir
    )
    assert motivo not in copiloto.motivos and motivo in copiloto.avisos_auto
    assert not copiloto.precisa_humano

    certa = _proposta(CERTA_552F, "troca_devolucao", ["regra:troca_devolucao"], 0.9)
    final, chk, ctx, base, conversa = await _caminho(
        certa, [PERGUNTA_552F], conversa=conversa, permitir=permitir
    )
    assert chk.pendencias == [] and not final.precisa_humano and final.sem_auto == []
    assert v.por_que_nao_verifica(final, certa, ctx, base, conversa) is None


def test_21_o_consultar_manual_devolve_o_recorte_da_recusa_da_troca():
    recusa = {
        "id": "fato:17",
        "texto": FATO_17,
        "escopo": "global",
        "assunto": "troca_devolucao",
        "fonte": constantes.FATO_FONTE_RECUSA_PADRAO,
    }
    dado = ferramentas.fato_do_manual(recusa)
    assert dado == {
        "id": "fato:17",
        "texto": FATO_17,
        "escopo": "global",
        "nao_vale_para": ferramentas.NAO_VALE_PARA_A_RECUSA_DE_TROCA,
    }
    nota = ferramentas.NAO_VALE_PARA_A_RECUSA_DE_TROCA
    for trecho in ("defeito", "se travar", "nunca se diz 'não fazemos troca'", "vídeo curto"):
        assert trecho in nota, trecho
    # Sem número (o texto vira "permitido" para o validador).
    assert not any(c.isdigit() for c in nota)
    # Só restringe: as outras recusas padrão e os fatos do dono, como antes.
    for outro in (
        {**recusa, "assunto": "desconto", "texto": "Os cupons ativos aparecem no anúncio."},
        {**recusa, "fonte": "dono"},
        {**recusa, "assunto": None},
    ):
        assert set(ferramentas.fato_do_manual(outro)) == {"id", "texto", "escopo"}


async def test_21_o_consultar_manual_leva_o_recorte_ao_agente(
    db: AsyncSession,
    make_user,
    redis_mem: RedisMem,  # noqa: F811
):
    """O `consultar_manual` real (com o banco): a recusa padrão da troca vem com o recorte;
    o outro fato do assunto, como sempre."""
    import dataclasses

    from tests.test_atendimento_agente_ferramentas import _base as _base_f
    from tests.test_atendimento_agente_ferramentas import _chamar, _ctx, _fato
    from tests.test_atendimento_ia import _conversa as _conversa_db

    conversa = await _conversa_db(db, make_user, pedido=None)
    recusa = _fato(
        chave=f"recusa_troca_teste_{uuid4().hex[:8]}",
        assunto="troca_devolucao",
        fonte=constantes.FATO_FONTE_RECUSA_PADRAO,
        texto=FATO_17,
    )
    outro = _fato(assunto="troca_devolucao", fonte="dono", texto="A devolução é pelo app.")
    db.add_all([recusa, outro])
    await db.commit()
    base = await _base_f(db, conversa)
    base = dataclasses.replace(
        base, categorias=[*base.categorias, {"id": "troca_devolucao", "nome": "x", "descricao": ""}]
    )
    ctx = _ctx(conversa, base)
    dados = await _chamar(
        ctx,
        base,
        conversa,
        "consultar_manual",
        assunto="troca_devolucao",
        pergunta="trava no primeiro uso, pode trocar",
    )
    por_id = {f["id"]: f for f in dados["fatos"]}
    assert (
        por_id[f"fato:{recusa.id}"]["nao_vale_para"] == ferramentas.NAO_VALE_PARA_A_RECUSA_DE_TROCA
    )
    assert "nao_vale_para" not in por_id[f"fato:{outro.id}"]
    # O recorte chega ao registro do verificador (o JSON sem os ids), como o resto.
    assert any("nunca se diz 'não fazemos troca'" in t for t in ctx.registro.textos)


# ── 21. contestação da 5ª rodada (22/10/2026) ──

# O defeito, a avaria e o item errado ditos com outras palavras.
DEFEITO_OUTRAS_PALAVRAS = [
    "Se chegar danificado troca?",
    "E se vier com algum dano?",
    "Se apresentar falha, vocês trocam?",
    "E se esquentar muito, troca?",
    "Se a bateria viciar, troca?",
    "Se vier com mau funcionamento, troca?",
    "Se vier diferente do anúncio, troca?",
    "E se vier a cor errada, troca?",
    "E se bugar, troca?",
    "Se der ruim, troca?",
    "Se o touch falhar, troca?",
    "Se vier com vício, troca?",
    "Se der superaquecimento, troca?",
    "se o aparelho morrer, troca?",
    "Se não prestar, troca?",
    "Se vier usado, troca?",
    "e se o lacre vier violado?",
    "o vidro já veio danificado, troca?",
]


@pytest.mark.parametrize("cliente", DEFEITO_OUTRAS_PALAVRAS)
def test_21_contestacao_o_defeito_com_outras_palavras(cliente):
    for texto in (FATO_17, RASCUNHO_552F):
        r = checagens.conferir_troca_negada_no_defeito(texto, textos_cliente=[cliente])
        assert _codigos(r) == [(checagens.TROCA_NEGADA_NO_DEFEITO, True, False)], (cliente, texto)


# A recusa com o "diferença" (que livra a frase do validador) dita de outro jeito, e as
# recusas que só a regra antiga pegava.
RECUSAS_COM_A_DIFERENCA = [
    "Olá! Não fazemos esse tipo de troca, nem mesmo pagando a diferença, só aceitamos a "
    "devolução pelo app do Mercado Livre em Minhas compras.",
    "Olá! Não há possibilidade de troca, nem pagando a diferença, só a devolução pelo app do "
    "Mercado Livre em Minhas compras.",
    "Olá! Mesmo pagando a diferença, a troca não está disponível, só aceitamos devolução pelo "
    "app do Mercado Livre em Minhas compras.",
]
RECUSAS_OUTRAS = [
    "Não efetuamos trocas.",
    "A troca não é realizada.",
    "Não temos política de troca.",
    "Não substituímos o aparelho.",
    "Só fazemos devolução.",
    "A única saída é pedir reembolso.",
]


@pytest.mark.parametrize("texto", [*RECUSAS_COM_A_DIFERENCA, *RECUSAS_OUTRAS])
def test_21_contestacao_as_outras_recusas(texto):
    r = checagens.conferir_troca_negada_no_defeito(texto, textos_cliente=[PERGUNTA_552F])
    assert _codigos(r) == [(checagens.TROCA_NEGADA_NO_DEFEITO, True, False)], texto


@pytest.mark.parametrize("texto", RECUSAS_COM_A_DIFERENCA)
async def test_21_contestacao_a_recusa_com_a_diferenca_no_caminho_do_agente(texto):
    """Pelo caminho inteiro no automático: a regra nova é a única a pegar (o "diferença"
    livra a frase da regra antiga da recusa) — pessoa, o verificador nem roda."""
    conversa = _conversa("ml", "pergunta")
    permitir = (["fato:17", "regra:troca_devolucao"], [json.dumps({"fatos": [{"texto": FATO_17}]})])
    p = _proposta(texto, "troca_devolucao", ["fato:17", "regra:troca_devolucao"], 0.85)
    final, chk, ctx, base, _ = await _caminho(
        p, [PERGUNTA_552F], conversa=conversa, permitir=permitir
    )
    assert [x.codigo for x in chk.pendencias] == [checagens.TROCA_NEGADA_NO_DEFEITO]
    assert final.precisa_humano
    assert v.por_que_nao_verifica(final, p, ctx, base, conversa) == v.NAO_RODA_PESSOA


# A resposta completa a quem pergunta das duas coisas: o caminho do defeito e a recusa
# padrão dita só para a troca SEM defeito.
CLIENTE_DEFEITO_E_COR = (
    "Se trava no primeiro uso pode troca? E se eu não gostar, posso trocar pelo azul pagando a "
    "diferença?"
)
COMPLETA_552F = (
    "Olá! Se o aparelho travar ou apresentar defeito, depois da compra fale com a loja pelas "
    "mensagens da compra e mande um vídeo curto e fotos do problema; a nossa equipe analisa. "
    "Sem defeito, não fazemos troca, nem pagando a diferença: a devolução pode ser pedida em "
    "Minhas compras."
)


@pytest.mark.parametrize(
    ("texto", "fica"),
    [
        (COMPLETA_552F, False),
        (
            "Olá! Se travar, envie um vídeo curto e fotos pelas mensagens da compra; a nossa "
            "equipe analisa. Por outro motivo, não fazemos troca, nem pagando a diferença.",
            False,
        ),
        # A troca só depois da análise (a orientação aprovada do defeito).
        (
            "Não fazemos a troca direto: primeiro envie um vídeo curto e fotos do problema pelas "
            "mensagens da compra; a nossa equipe analisa.",
            False,
        ),
        (
            "A troca não é feita na hora: envie um vídeo curto e fotos; a nossa equipe analisa.",
            False,
        ),
        (
            "Se travar, envie um vídeo e fotos; a equipe analisa, e não tem custo de troca.",
            False,
        ),
        # Continuam: o "não direto" sem o caminho do defeito no texto, o defeito na
        # frase da recusa, e a recusa que sobra depois do "direto".
        ("Não fazemos a troca direto. A devolução é pelo app.", True),
        ("Sem defeito não fazemos troca, e com defeito também não.", True),
        ("Troca de modelo não fazemos, nem com defeito.", True),
        (
            "Não fazemos a troca direto, nem pagando a diferença: a única solução é a devolução "
            "pelo app.",
            True,
        ),
        (FATO_17, True),
    ],
)
def test_21_contestacao_a_isencao_sem_defeito_vale(texto, fica):
    r = checagens.conferir_troca_negada_no_defeito(texto, textos_cliente=[CLIENTE_DEFEITO_E_COR])
    assert bool(r) is fica, texto


async def test_21_contestacao_a_resposta_completa_vai_ao_verificador():
    """Antes, a isenção "sem defeito" nunca valia (o "defeito" de "sem defeito"): a melhor
    resposta ficava presa. Agora passa a 1ª camada e vai ao verificador."""
    conversa = _conversa("ml", "pergunta")
    permitir = (["fato:17", "regra:troca_devolucao"], [json.dumps({"fatos": [{"texto": FATO_17}]})])
    p = _proposta(COMPLETA_552F, "troca_devolucao", ["fato:17", "regra:troca_devolucao"], 0.9)
    final, chk, ctx, base, _ = await _caminho(
        p, [CLIENTE_DEFEITO_E_COR], conversa=conversa, permitir=permitir
    )
    assert chk.pendencias == [], _codigos(chk.pendencias)
    assert not final.precisa_humano and final.sem_auto == []
    assert v.por_que_nao_verifica(final, p, ctx, base, conversa) is None


@pytest.mark.parametrize(
    ("cliente", "texto", "fica"),
    [
        (
            "o rastreio parou, posso trocar o endereço de entrega?",
            "Não conseguimos trocar o endereço depois do envio. Acompanhe em Minhas Compras > o "
            "pedido.",
            False,
        ),
        (
            "deu problema no cartão, dá pra trocar a forma de pagamento?",
            "Não é possível trocar a forma de pagamento depois da compra.",
            False,
        ),
        # A troca do produto na mesma frase continua sendo lida.
        (
            "o celular parou de funcionar, dá pra trocar o aparelho e o endereço?",
            "Não trocamos o aparelho; o endereço não dá para trocar depois do envio.",
            True,
        ),
    ],
)
def test_21_contestacao_a_troca_de_outra_coisa_nao_e_recusa(cliente, texto, fica):
    r = checagens.conferir_troca_negada_no_defeito(texto, textos_cliente=[cliente])
    assert bool(r) is fica, texto


# ═══════════════ 16. o cabeçalho dos ASSUNTOS no bloco 2 ═══════════════


@pytest.mark.parametrize("plataforma", ["shopee", "ml", "tiktok", "amazon"])
def test_16_o_bloco_da_plataforma_manda_ficar_no_assunto_da_resposta(plataforma):
    cats = _categorias_do_manual()
    texto = prompt.bloco_da_plataforma(plataforma, cats)
    assert prompt.ASSUNTOS_CABECALHO in texto
    cabecalho = prompt.ASSUNTOS_CABECALHO
    for trecho in (
        "nunca um de que a conversa não fala",
        "na dúvida, o que aparece primeiro",
        "quem só confirma ou repete a resposta da loja",
        '("então não serve, né?") fica no assunto dessa resposta',
    ):
        assert trecho in cabecalho, trecho
    # Nenhuma regra nova de "ok/obrigado" (o manual: o "ok" a uma pergunta da loja herda
    # o caso aberto).
    assert "agradecimento" not in cabecalho and "obrigad" not in cabecalho
    assert "ok" not in re.findall(r"\w+", cabecalho)
    # O teto do catálogo (o `test_bloco_da_plataforma`) e o do prefixo.
    lista = texto.split("# ASSUNTOS")[1]
    assert len(lista) <= prompt.MAX_CHARS_ASSUNTOS + 200
    assert len(texto) / 3.5 <= 4_500


def test_16_o_bloco_geral_nao_muda():
    import hashlib

    atual = hashlib.sha256(prompt.bloco_geral().encode()).hexdigest()[:16]
    assert atual == RETRATO_BLOCO_GERAL


def test_16_o_legado_nao_le_o_cabecalho():
    """O cabeçalho é só do bloco 2 do agente: o catálogo do legado
    (`ia.lista_de_assuntos`) é o mesmo."""
    cats = _categorias_do_manual()
    assert prompt.ASSUNTOS_CABECALHO not in ia.lista_de_assuntos(cats, prompt.MAX_CHARS_ASSUNTOS)


# ═══════════════ a calibração: os casos dos itens 17 e 21 ═══════════════

NOVOS = {
    # id → (grupo, bloqueia, a marca medida)
    "boa-aj17-7f0c1b4e": ("boa", False, None),
    "boa-aj17-pos-venda": ("boa", False, None),
    "regiao-14": ("regiao", True, v.REGIAO),
    "regiao-15": ("regiao", True, v.REGIAO),
    "regiao-16": ("regiao", True, v.REGIAO),
    "garantia-14": ("garantia", True, v.NEGA),
    # Contestação da 5ª rodada (22/10/2026): as formas que a 1ª versão do item 17
    # deixava passar, para medir o v7 nelas (o pré-venda com "meu pedido", o lugar com
    # "vive em", a recusa com o "diferença" a quem pergunta de avaria). Com o item 17
    # desfeito (3ª contestação), a 1ª camada segura as de região: as linhas só medem.
    "regiao-17": ("regiao", True, v.REGIAO),
    "regiao-18": ("regiao", True, v.REGIAO),
    "garantia-15": ("garantia", True, v.NEGA),
    # 2ª contestação (22/10/2026): o lugar sem preposição, que a 1ª camada não lê.
    "regiao-19": ("regiao", True, v.REGIAO),
}


def _casos() -> dict[str, dict]:
    return {c["id"]: c for c in calibrar.ler_casos(calibrar.CASOS_PADRAO)}


def test_a_calibracao_tem_os_casos_da_5a_rodada():
    casos = _casos()
    for id_, (grupo, bloqueia, marca) in NOVOS.items():
        c = casos[id_]
        assert c["grupo"] == grupo, id_
        e = calibrar.esperado_do_caso(c)
        assert e[calibrar.BLOQUEIA] is bloqueia, id_
        if marca is None:
            assert c["tolerados"] == [] and not any(e.values()), id_
        else:
            assert e[marca] and marca not in c["tolerados"], id_
        # Nenhum é vizinho (o grupo `vizinhos` é o do v7, fechado), nenhum repete uma
        # frase-exemplo do `SISTEMA` e nenhum diz "nenhuma" nas perguntas.
        assert id_ not in VIZINHOS
        assert not calibrar.contaminado(c), id_
        assert not calibrar.sem_pergunta_do_caso(c), id_
        entrada = calibrar.entrada_do_caso(c)
        assert entrada.startswith(pacote.ABRE_DADOS) and entrada.endswith(pacote.AVISO_DADOS)
    # O 7f0c1b4e: o texto, as falas e a restrição da LOJA reais.
    boa = casos["boa-aj17-7f0c1b4e"]
    assert boa["texto"] == RASCUNHO_7F0C and boa["perguntas"] == FALAS_7F0C
    assert boa["evidencias"] == [FICHA_A18]
    assert "comprei o aparelho" in calibrar.entrada_do_caso(boa)
    assert "LACUNAS DO RASCUNHO" in calibrar.entrada_do_caso(casos["boa-aj17-pos-venda"])
    # O regiao-16: o lugar na 3ª pessoa, sem a UF na fala.
    assert "mora em Itaperuna" in casos["regiao-16"]["perguntas"][0]
    # O 552f20ba: o texto real, o fato da recusa SEM o recorte novo (o pior caso: a
    # entrada do ao vivo de antes) e o assunto declarado já liberado.
    g = casos["garantia-14"]
    assert g["texto"] == RASCUNHO_552F and g["perguntas"] == [PERGUNTA_552F]
    assert g["plataforma"] == "ml" and g["declarado"] == "troca_devolucao"
    entrada = calibrar.entrada_do_caso(g)
    assert FATO_17 in entrada and "nao_vale_para" not in entrada
    assert "o código já o liberou" in entrada
    # Os da contestação: as falas que a 1ª versão do item 17 deixava sem aviso e a
    # recusa com o "diferença" fora da régua do validador.
    assert casos["regiao-17"]["perguntas"][0].startswith("Vou fechar meu pedido")
    assert "vive em Itaperuna" in casos["regiao-18"]["perguntas"][0]
    assert casos["regiao-18"]["texto"] == casos["regiao-16"]["texto"]
    g15 = casos["garantia-15"]
    assert "diferença" in g15["texto"] and not validador.recusa_cobertura(g15["texto"])
    assert g15["evidencias"] == g["evidencias"]
    # O da 2ª contestação: o regiao-16 com o lugar sem preposição, em outra fala (a 1ª
    # camada não lê esse lugar: mede o verificador nele).
    r19 = casos["regiao-19"]
    assert r19["perguntas"][1].endswith("Itaperuna") and r19["texto"] == casos["regiao-16"]["texto"]
    # O arquivo de antes ficou igual: as 342 linhas e os 10 novos depois delas (a 6ª
    # rodada, 22/10/2026, acrescenta os dela no fim).
    ids = [c["id"] for c in calibrar.ler_casos(calibrar.CASOS_PADRAO)]
    assert len(ids) >= 352 and ids[342:352] == list(NOVOS)


@pytest.mark.parametrize(
    "escapa", [None, "garantia-14", "regiao-16", "regiao-17", "garantia-15", "regiao-19"]
)
async def test_a_calibracao_mede_os_novos_sem_modelo(tmp_path, teto_limpo, escapa):
    casos = [_casos()[i] for i in NOVOS]
    falsa = _ver.Api()
    falsa.veredito = [_veredito_para(c, deixa_escapar=escapa) for c in casos]
    eco = io.StringIO()
    async with _ver._cliente(falsa) as cliente:
        codigo = await calibrar.principal(
            ["--casos", str(_ver._arquivo(tmp_path, casos))],
            agora=_ver.FORA_DA_JANELA,
            eco=eco,
            cliente=cliente,
        )
    assert codigo == calibrar.OK
    resumo = json.loads(eco.getvalue().strip().splitlines()[-1])
    if escapa is None:
        assert resumo["errados"] == []
        assert resumo["por_grupo"]["regiao"]["recall"] == 1.0
        assert resumo["por_grupo"]["garantia"]["recall"] == 1.0
        assert resumo["por_grupo"]["boa"]["casos"] == 2
    else:
        assert resumo["errados"] == [escapa]
        grupo = NOVOS[escapa][0]
        assert resumo["por_grupo"][grupo]["escapes"] == [escapa]


def test_a_calibracao_nao_mudou_o_sistema():
    """Os casos novos só medem: o verificador é o v7 de sempre (o v8 da 6ª rodada,
    22/10/2026, é outro conserto — itens 22 e 23)."""
    assert v.VERSAO in ("v7", "v8")
    assert "nao_vale_para" not in v.SISTEMA


def test_o_legado_nao_conhece_as_regras_novas():
    """`checagens` é só do agente; o validador do legado é o mesmo."""
    assert not hasattr(validador, "conferir_troca_negada_no_defeito")
    assert validador.recusa_cobertura(FATO_17) is False


def test_os_motivos_novos_nao_tem_numero():
    assert not any(c.isdigit() for c in checagens.MOTIVO_TROCA_NEGADA_NO_DEFEITO)
