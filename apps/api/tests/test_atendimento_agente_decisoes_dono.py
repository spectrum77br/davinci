# ruff: noqa: F811, E501 — fixtures importadas; as tabelas de casos reais
"""Decisões D1–D3 do dono (23/10/2026 ~12:40–12:50): "evoluir pra responder pelo menos uns
40% automaticamente; veja as mensagens que as pessoas estão respondendo, mas adapte
corretamente; faça esse agente funcionar por ele mesmo".

D1. "Responde e marca": com a devolução/reclamação ABERTA na plataforma (não o chamado, não
    o atraso) e o turno só informativo (o reembolso, o "já pedi", como postar, o que vem
    agora), a resposta do agente pode sair sozinha (checagens + verificador) E a conversa
    continua na Caixa Humano com a ação (aprovar/recusar na central do vendedor):
    `acao_pessoa.responde_e_marca_do_turno`, `ContextoAgente.acoes_pos_envio`,
    `humano.CHAVE_POS_ENVIO`. A resposta nunca aprova/recusa, nunca dá data/prazo/valor,
    nunca fala de Pix, nunca manda abrir outra solicitação; da equipe, só "a equipe
    analisa a devolução pela Shopee".
D2. A garantia Apple antes da compra: a condição pelo anúncio (L2) e a garantia SÓ como o
    fato aprovado diz ("garantia Apple de 1 ano a partir da ativação", o fato 25 da Kia,
    citado) ou a resposta da equipe da MESMA loja; com variação aberta/ativada no anúncio,
    nunca "novo e lacrado" para o anúncio inteiro.
D3. A entrega como a equipe responde: sem a condição do chamado (o cliente pede para a loja
    verificar/agir, pede o dinheiro/cancelar pelo atraso, o rastreio parado há
    `DIAS_SEM_EVENTO` dias, a falha/volta da entrega), o "cadê / sem atualização / hoje
    (ontem) era o dia" é respondido com o que o pedido mostra e a política da loja ("depois
    do envio a entrega é da Shopee; não temos acesso ao transporte"), citando a resposta da
    equipe; o chamado continua 100% pessoa. O "6" do menu não é insistência.

Os casos têm a forma dos turnos reais de 22–23/10/2026 (Kia, Vita e Barbosa Shopee), sem
dado de cliente (nenhum nome, telefone, CPF, endereço, rastreio ou número de pedido real).
Nenhuma chamada a LLM, à loja ou à produção.
"""

from __future__ import annotations

import itertools
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import select

import app.db as _db
from app.models import AtendimentoConversa, AtendimentoReclamacao
from app.services.atendimento import constantes, gravar, humano, ia
from app.services.atendimento.agente import acao_pessoa, checagens, pares, prompt, saida
from app.services.atendimento.agente import contexto as agente_ctx
from app.services.atendimento.agente import pacote as pacote_svc
from app.services.atendimento.agente import verificador as v
from app.services.atendimento.agente.saida import Proposta
from scripts.agente_eval import calibrar_verificador as calibrar
from tests.test_atendimento_agente_e2e import (  # noqa: F401 — fixtures
    _decisao,
    _do_agente,
    _final,
    _ligar,
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
from tests.test_atendimento_agente_laco import _categorias_do_manual
from tests.test_atendimento_agente_redondo import SHOPEE_REAL, VAI_AO_VERIFICADOR, VAI_COM_AVISO
from tests.test_atendimento_humano import _loja_db
from tests.test_atendimento_ia import PEDIDO_MKT, _importar_manual_base, _pedido_completo
from tests.test_atendimento_ia import _conversa as _conversa_ia
from tests.test_atendimento_ia import _msg as _msg_ia

VAI = VAI_AO_VERIFICADOR
AGORA = datetime(2026, 10, 22, 18, 0, tzinfo=UTC)
_seq = itertools.count()
MENU = (
    "Olá, por favor selecione sua dúvida e logo um dos nossos consultores irá atendê-lo!\n\n"
    "1 - Previsão de entrega / envio\n2 - Nota fiscal\n3 - Encerramento da compra\n"
    "4 - Troca de endereço\n5 - Garantia\n6 - Falar com Atendente"
)
PEDIDO = "261019AAAA0001"  # não é um pedido real
VALORES = {"numero_pedido": PEDIDO, "data_envio": "19/10/2026"}


def _m(autor: str, texto: str | None, *, origem: str | None = None, tipo: str = "texto"):
    quando = AGORA - timedelta(hours=2) + timedelta(seconds=next(_seq))
    return SimpleNamespace(
        id=uuid4(), autor=autor, texto=texto, tipo=tipo, payload={}, origem=origem or autor,
        status="recebida" if autor == "cliente" else "enviada", enviada_em=quando,
        created_at=quando, anexos=[], autor_user_id=None, erro=None,
    )  # fmt: skip


def _cli(*falas: str) -> list[SimpleNamespace]:
    return [_m("cliente", f) for f in falas]


def _menu() -> SimpleNamespace:
    return _m("loja", MENU, origem="externo")


def _loja(texto: str) -> SimpleNamespace:
    return _m("loja", texto, origem="externo")


def _conv(**campos: Any) -> SimpleNamespace:
    base = {
        "id": uuid4(), "plataforma": "shopee", "canal": "chat", "dados": {},
        "pedido_marketplace": PEDIDO, "etiqueta": "pos_venda", "etiquetas_secundarias": [],
        "etiquetas": [], "canal_id": None, "integration_id": None, "comprador_id": None,
        "situacao": "aberta", "aguardando_resposta": True,
    }  # fmt: skip
    base.update(campos)
    return SimpleNamespace(**base)


# ── O pedido postado (o real de 22–23/10: postado em 19/10, o retrato fresco) ──
STATUS = "O pedido está em separação no centro logístico, novas atualizações em até 1 dia."
CTX_POSTADO = {
    "pedido": {
        "numero": "90001", "numeroloja": PEDIDO, "situacao": "Em andamento",
        "enviado_em": "2026-10-19", "itens": [{"descricao": "Uranyx A17 Pro Max 12.64 - Laranja", "quantidade": 1}],
    },
    "logistica": {"status": "Pedido postado", "data_envio": "2026-10-19"},
    "chamados": [], "devolucoes": [],
}  # fmt: skip


def _bloco(status: str | None = STATUS, **kw: Any) -> dict:
    return {
        "numero": "{numero_pedido}", "situacao": "Em andamento", "postado": acao_pessoa.POSTADO_SIM,
        "enviado": True, "data_envio": "{data_envio}", "entregue": False, "ultimo_status": status,
        "chamados": [], "devolucoes": [], "reclamacoes_abertas": 0, **kw,
    }  # fmt: skip


def _retrato(dias_do_evento: float, descricao: str = STATUS) -> dict:
    """O retrato fresco do MESMO pedido (lido há 1 h) com o último evento da Shopee."""
    return {
        "pedido_mkt": {
            "pedido": PEDIDO, "fonte": "shopee", "status": "SHIPPED",
            "atualizado_em": (AGORA - timedelta(hours=1)).isoformat(),
            "logistica": {
                "descricao": descricao, "status": "LOGISTICS_PICKUP_DONE",
                "atualizado_em": (AGORA - timedelta(days=dias_do_evento)).isoformat(),
            },
        }
    }  # fmt: skip


# As respostas da equipe da MESMA loja (os pares reais 8440 e 4489, sem dado de cliente).
PAR_PRAZO = {
    "id": "par:8440", "pergunta": "Porque tanta demora do meu pedido",
    "resposta": "Pode seguir o prazo informado na hora da compra pela plataforma, não temos acesso ao transporte!",
    "mesma_loja": True, "mesmo_anuncio": False, "mesmo_produto": False, "fonte_de_produto": False,
    "com_anuncio": False, "idade_dias": 2,
}  # fmt: skip
PAR_LOGISTICA = {
    **PAR_PRAZO, "id": "par:4489",
    "pergunta": "ainda não recebi meu produto a última data de entrega foi ontem",
    "resposta": "Após o envio dos produtos não temos acesso ao processo logístico, ele fica inteiramente por conta da plataforma.",
}  # fmt: skip
PAR_OUTRA_LOJA = {**PAR_PRAZO, "id": "par:9001", "mesma_loja": False}
PAR_REEMBOLSO = {
    **PAR_PRAZO,
    "id": "par:2003",
    "pergunta": "quando cai o reembolso",
    "resposta": "o reembolso é feito pela plataforma!",
}
PAR_APPLE = {
    **PAR_PRAZO, "id": "par:1500",
    "pergunta": "A garantia é direto com a Apple após ativação do produto com duração de 1 ano?",
    "resposta": "exatamente, a garantia é com a apple após ativação",
}  # fmt: skip

TEMPLATE = (
    "O seu pedido foi postado em {data_envio} e o último status é: em separação no centro "
    "logístico. Depois do envio, a entrega fica por conta da Shopee e não temos acesso ao "
    "transporte; o prazo que vale é o que aparece em Minhas Compras > o pedido > Informações "
    "do envio."
)


def _p(texto: str, assunto: str, evidencias: list[str], *, confianca: float = 0.9) -> Proposta:
    return Proposta(
        acao="responder", texto=texto, assunto=assunto, confianca=confianca, acao_pessoa=None,
        motivo_pessoa="", evidencias=evidencias,
    )  # fmt: skip


async def _auto(
    p: Proposta,
    msgs: list,
    *,
    ctx_pedido: dict | None = None,
    bloco: dict | None = None,
    postado: str | None = None,
    fichas: list[dict] | None = None,
    valores: dict | None = None,
    fatos_lidos: dict[str, str] | None = None,
    pares_do_pacote: list[dict] | None = None,
    conversa: Any = None,
    agora: datetime = AGORA,
) -> SimpleNamespace:
    """O caminho AUTO do `agente.responder` sem banco e sem modelo (o `_auto` das rodadas de
    antes) com o que D1–D3 leem: os pares do pacote, o retrato da conversa, o relógio, as
    marcas do código (`rotina_do_turno`, `responde_e_marca_do_turno`) e as do agente antes
    da `ia.conferir_saida` (`liberacao_da_proposta`, `garantia_apple_da_proposta`)."""
    fichas = list(fichas or [])
    conversa = conversa or _conv()
    gatilho = next(x for x in reversed(msgs) if x.autor == "cliente")
    rajada = ia.rajada_do_cliente(msgs, gatilho)
    foco = ia.perguntas_pendentes(msgs, gatilho)
    textos_cliente = [x.texto or "" for x in (foco or rajada)]
    textos_rajada = [x.texto or "" for x in rajada]
    ctxd = ctx_pedido or {"pedido": None, "logistica": None, "chamados": [], "devolucoes": []}
    base = agente_ctx.BaseDoGerar(
        recentes=msgs, rajada=rajada, foco=foco, textos_cliente=textos_cliente,
        textos_rajada=textos_rajada, ctx=ctxd, valores=dict(valores or {}), fatos={"anuncio": None},
        fatos_gravados={}, categorias=_categorias_do_manual(), regras=[], sinais=[], motivos=[],
        opcoes_sensiveis=[], sinais_do_cliente=[], nomes=(), modo=constantes.MODO_AUTO,
        forcar=False, nf_politica_aprovada=False, insistiu=False,
    )  # fmt: skip
    lista = acao_pessoa.gatilhos(
        conversa, msgs, ctxd, gatilho=gatilho, postado=postado, chaves_ativas=frozenset(), agora=agora
    )  # fmt: skip
    ctx = agente_ctx.ContextoAgente.da_conversa(conversa, gatilho, modo=constantes.MODO_AUTO)
    ctx.acoes_codigo = agente_ctx.ordenar_acoes([g.codigo for g in lista])
    ctx.devolucao_de_rotina = acao_pessoa.rotina_do_turno(conversa, ctxd, msgs, gatilho)
    ctx.responde_e_marca = acao_pessoa.responde_e_marca_do_turno(
        conversa, ctxd, msgs, gatilho, acoes=ctx.acoes_codigo, agora=agora
    )
    ctx.pistas_orientacao = acao_pessoa.pistas_de_orientacao(msgs, gatilho)
    ctx.pistas_turno = acao_pessoa.pistas_do_turno(
        msgs, gatilho, com_pedido=bool(ctxd.get("pedido"))
    )
    provados = [agente_ctx.ID_PEDIDO] if bloco else []
    for f in fichas:
        provados.append(agente_ctx.id_ficha(f["item_id"]))
        if f.get("variacoes"):
            provados.append(agente_ctx.id_variacoes(f["item_id"]))
    ctx.permitir(ids=provados, textos=[json.dumps(bloco, ensure_ascii=False) if bloco else None])
    ctx.permitir(textos=[json.dumps(fichas, ensure_ascii=False) if fichas else None], produto=True)
    for i, texto in (fatos_lidos or {}).items():
        ctx.permitir(ids=[i], textos=[texto])
        ctx.registrar_fatos([{"id": i, "texto": texto}])
    ctx.registrar_pares(pares_do_pacote or [])
    ctx.pistas_turno = list(
        dict.fromkeys([*ctx.pistas_turno, *acao_pessoa.pistas_do_rascunho(p.texto)])
    )
    ctx.acao = p.acao
    ctx.acoes_modelo = saida.acoes_do_modelo(p)
    ctx.evidencias = saida.evidencias(p)
    fatos: dict[str, Any] = {"anuncio": None, "ficha_do_anuncio": fichas}
    if bloco:
        fatos["agente_pedido"] = bloco
    entrada = saida.para_conferir(p, base.ids_categorias)
    entrada["texto"] = prompt.caminho_na_frase(entrada["texto"])
    conferida = await ia.conferir_saida(
        None, conversa, gatilho, **entrada, motivos=[], ctx=ctxd, fatos=fatos,
        valores=dict(valores or {}), categorias=base.categorias, regras=[],
        textos_cliente=textos_cliente, textos_rajada=textos_rajada, foco=foco, fichas=fichas,
        garantias_conferidas=ctx.garantias_conferidas, garantia_consultada=ctx.garantia_consultada,
        permitidos_extra=ctx.permitidos_extra(), modo=constantes.MODO_AUTO, forcar=True, agente=True,
        devolucao_de_rotina=ctx.devolucao_de_rotina or ctx.responde_e_marca,
        produto_pela_equipe=bool(ctx.pares_citados(ctx.evidencias, de_produto=True)),
        fatos_citados=ctx.fatos_citados(ctx.evidencias),
        produto_pelo_anuncio=checagens.liberacao_da_proposta(p, entrada["texto"], entrada["categoria"], ctx, base, fichas=fichas),
        garantia_apple_pelo_fato=checagens.garantia_apple_da_proposta(p, entrada["texto"], ctx, base, fichas=fichas),
    )  # fmt: skip
    conferida = checagens.sem_lacuna_vazia(conferida)
    ctx.pedido_da_conversa = bloco
    chk = checagens.conferir(p, conferida, ctx, base, fichas=fichas, gatilho=gatilho)
    # O portão do dono (27/10/2026), como no `agente.conferir_proposta`: os avisos do portão
    # e o que a `conferir_saida` diz e não é duro viram AVISO ao verificador.
    chk = checagens.Checagens(
        pendencias=[
            *chk.pendencias,
            *checagens.avisos_do_portao(
                confianca=saida.confianca(p),
                assunto=ia.assunto_do_agente(conferida.categoria, p.acao, base.ids_categorias),
                produto_pelo_anuncio=ctx.produto_pelo_anuncio,
                produto_pela_equipe=bool(ctx.pares_citados(ctx.evidencias, de_produto=True)),
            ),
        ]
    )
    conferida = checagens.separar_avisos(
        conferida, modo=constantes.MODO_AUTO, pessoa_do_modelo=bool(entrada["precisa_humano"])
    )
    final = checagens.aplicar(
        conferida, chk, acoes_pessoa=ctx.acoes_pessoa, modo=constantes.MODO_AUTO
    )
    nao = v.por_que_nao_verifica(final, p, ctx, base, conversa)
    rascunho = SimpleNamespace(
        prompt_versao="ag1", categoria=final.categoria,
        fatos={
            "agente": {**ctx.para_fatos(), "sem_auto": list(final.sem_auto), "avisos_auto": list(final.avisos_auto)},
            "devolucoes": len(ctxd.get("devolucoes") or []),
            "chamados_abertos": len(ctxd.get("chamados") or []),
        },
    )  # fmt: skip
    trava = ia.trava_do_agente(rascunho, SHOPEE_REAL)
    return SimpleNamespace(
        decisao=(
            (VAI if not final.avisos else VAI_COM_AVISO)
            if nao is None and trava is None
            else (nao or trava)
        ),
        avisos=list(final.avisos),
        codigos=[x.codigo for x in chk.pendencias], motivos=list(final.motivos), ctx=ctx,
        rascunho=rascunho, base=base,
    )  # fmt: skip


def _gat(
    msgs: list,
    *,
    ctx: dict | None = None,
    conversa: Any = None,
    postado=acao_pessoa.POSTADO_SIM,
    agora=AGORA,
):
    gatilho = next(m for m in reversed(msgs) if m.autor == "cliente")
    return [
        g.codigo
        for g in acao_pessoa.gatilhos(
            conversa or _conv(), msgs, ctx if ctx is not None else CTX_POSTADO, gatilho=gatilho,
            postado=postado, chaves_ativas=frozenset(), agora=agora,
        )
    ]  # fmt: skip


# ═══════════════ D3. a entrega respondida como a equipe ═══════════════

# Os turnos reais (22–23/10/2026, anonimizados): o retrato fresco com movimento recente.
TURNOS_DA_EQUIPE = [
    # 22/10 14:15 e 16:10 (Barbosa): "sem atualização", a equipe responde o padrão.
    ["Meu pedido está sem atualização", "?"],
    # 22/10 20:31 e 20:33 (Kia): "hoje era o dia" + a opção 6 do menu.
    ["Hoje era o dia da entrega do relogio e ainda não recebi", "6"],
    # 23/10 10:34 e 11:19 (Kia): a equipe mandou "Pode seguir o prazo informado…".
    ["Ontem era o dia de receber e nada"],
    # 23/10 03:16 (Barbosa).
    ["Meu pedido está sem atualização", "Não sei onde esta"],
    ["cadê meu pedido?"],
    ["Meu pedido não chegou e a data limite foi ontem"],
]


@pytest.mark.parametrize("falas", TURNOS_DA_EQUIPE)
def test_d3_sem_a_condicao_do_chamado_nao_vira_chamado(falas):
    assert constantes.ACAO_ABRIR_CHAMADO not in _gat(
        [_menu(), *_cli(*falas)], conversa=_conv(dados=_retrato(1))
    )


@pytest.mark.parametrize(
    "falas",
    [
        # O real de 23/10 08:23 (Kia): o pedido parado e o pedido de ver com a Shopee.
        [
            "Meu pedido até agora nada, já tem mais de 8 dias e nem saiu de SP, vocês podem tá vendo junto com a Shopee o que aconteceu?"
        ],
        ["Meu pedido não chegou, vocês podem abrir um chamado?"],
        ["Hoje era o dia da entrega e não chegou, me ajuda"],
        ["Cadê meu pedido? Pôr favor né fazem alguma coisa"],
        ["Não chegou e a previsão já passou, quero o reembolso"],
        ["Meu pedido está atrasado, verifica com a transportadora por favor"],
        # O próprio cliente diz que está parado há dias (o par 1398: a equipe abriu chamado).
        ["Meu pedido está sem atualização desde quarta"],
        ["o rastreio está parado desde o dia 18"],
    ],
)
def test_d3_com_o_pedido_de_acao_vira_chamado(falas):
    assert constantes.ACAO_ABRIR_CHAMADO in _gat(
        [_menu(), *_cli(*falas)], conversa=_conv(dados=_retrato(1))
    )


def test_d3_o_pedido_parado_ha_dias_e_a_falha_viram_chamado():
    """O retrato fresco com o último evento de 3+ dias (parado), a falha ou a volta da
    entrega; sem o retrato (movimento desconhecido), os 10 dias desde a postagem."""
    hoje_era = [_menu(), *_cli("Hoje era o dia da entrega e ainda não recebi")]
    sem_atualizacao = [_menu(), *_cli("Meu pedido está sem atualização")]
    assert constantes.ACAO_ABRIR_CHAMADO in _gat(hoje_era, conversa=_conv(dados=_retrato(4)))
    # O "parado/sem atualização" com o evento velho: o motivo próprio (item 31).
    assert _gat(sem_atualizacao, conversa=_conv(dados=_retrato(4))) == [
        constantes.ACAO_ABRIR_CHAMADO
    ]
    falha = "Houve um imprevisto durante a entrega do seu pedido e, por isso, ela não pôde ser concluída hoje."
    assert constantes.ACAO_ABRIR_CHAMADO in _gat(
        hoje_era, conversa=_conv(dados=_retrato(0.2, falha))
    )
    voltando = {
        **CTX_POSTADO,
        "logistica": {**CTX_POSTADO["logistica"], "status": "Pedido será devolvido ao remetente"},
    }
    assert constantes.ACAO_ABRIR_CHAMADO in _gat(
        hoje_era, ctx=voltando, conversa=_conv(dados=_retrato(0.5))
    )
    # Sem o retrato fresco: 3 dias de postado não basta; 11 dias, sim.
    assert constantes.ACAO_ABRIR_CHAMADO not in _gat(hoje_era)
    velho = {**CTX_POSTADO, "logistica": {**CTX_POSTADO["logistica"], "data_envio": "2026-10-11"}}
    assert constantes.ACAO_ABRIR_CHAMADO in _gat(hoje_era, ctx=velho)
    # Com o retrato e movimento recente, nem os 11 dias viram chamado (a equipe: o padrão).
    assert constantes.ACAO_ABRIR_CHAMADO not in _gat(
        hoje_era, ctx=velho, conversa=_conv(dados=_retrato(0.5))
    )


def test_d3_quem_desiste_em_dia_e_quem_so_pergunta_nao_vira_chamado():
    for falas in (
        ["Quero cancelar, ainda não chegou"],
        ["consegue ver onde está meu pedido?"],
        ["O que aconteceu com meu pedido?"],
        ["me ajuda a configurar o celular"],
    ):
        assert constantes.ACAO_ABRIR_CHAMADO not in _gat(
            [_menu(), *_cli(*falas)], conversa=_conv(dados=_retrato(1))
        ), falas


def test_d3_fora_da_shopee_a_regra_de_antes():
    """ML/TikTok/Amazon não mudam: a data que passou continua virando chamado."""
    ml = _conv(plataforma="ml")
    assert constantes.ACAO_ABRIR_CHAMADO in _gat(
        [*_cli("Meu pedido não chegou e a data limite foi ontem")], conversa=ml
    )


@pytest.mark.parametrize(
    "falas",
    [
        ["Meu pedido está sem atualização", "?"],
        ["Hoje era o dia da entrega do relogio e ainda não recebi", "6"],
        ["Ontem era o dia de receber e nada"],
    ],
)
async def test_d3_a_resposta_padrao_com_o_par_vai_ao_verificador(falas):
    r = await _auto(
        _p(TEMPLATE, "rastreio", ["pedido", "par:8440"]), [_menu(), *_cli(*falas)],
        ctx_pedido=CTX_POSTADO, bloco=_bloco(), postado=acao_pessoa.POSTADO_SIM, valores=VALORES,
        pares_do_pacote=[PAR_PRAZO, PAR_LOGISTICA], conversa=_conv(dados=_retrato(1)),
    )  # fmt: skip
    assert r.decisao == VAI, (r.codigos, r.motivos)


@pytest.mark.parametrize(
    ("texto", "evidencias", "pares_", "codigo"),
    [
        # Sem a resposta da equipe citada (ou só a de OUTRA loja): a política sem lastro.
        (TEMPLATE, ["pedido"], [PAR_PRAZO], checagens.POLITICA_DA_ENTREGA_SEM_PAR),
        (TEMPLATE, ["pedido", "par:9001"], [PAR_OUTRA_LOJA], checagens.POLITICA_DA_ENTREGA_SEM_PAR),
        # A data prometida, a cidade, a ação da equipe.
        (
            "O seu pedido foi postado em {data_envio} e chega amanhã. Depois do envio, a entrega fica por conta da Shopee.",
            ["pedido", "par:8440"],
            [PAR_PRAZO],
            checagens.PEDIDO_SEM_LASTRO,
        ),
        (
            "O seu pedido está em Betim, em separação no centro logístico. Depois do envio, a entrega fica por conta da Shopee e não temos acesso ao transporte.",
            ["pedido", "par:8440"],
            [PAR_PRAZO],
            checagens.PEDIDO_SEM_LASTRO,
        ),
        (
            "O último status é: em separação no centro logístico. Já pedimos à transportadora para verificar.",
            ["pedido", "par:8440"],
            [PAR_PRAZO],
            checagens.PROMESSA_DE_EQUIPE,
        ),
        (
            "O último status é: em separação no centro logístico. Vamos verificar com a Shopee e te avisamos.",
            ["pedido", "par:8440"],
            [PAR_PRAZO],
            checagens.PROMESSA_DE_EQUIPE,
        ),
    ],
)
async def test_d3_o_que_nao_e_a_resposta_padrao_nao_sai(texto, evidencias, pares_, codigo):
    r = await _auto(
        _p(texto, "rastreio", evidencias), [_menu(), *_cli("Meu pedido está sem atualização")],
        ctx_pedido=CTX_POSTADO, bloco=_bloco(), postado=acao_pessoa.POSTADO_SIM, valores=VALORES,
        pares_do_pacote=pares_, conversa=_conv(dados=_retrato(1)),
    )  # fmt: skip
    assert codigo in r.codigos, (r.codigos, r.motivos)
    assert r.decisao != VAI


async def test_d3_com_o_chamado_a_resposta_padrao_nao_sai():
    """O chamado é 100% pessoa (decisão do dono): mesmo a resposta-padrão com o par."""
    r = await _auto(
        _p(TEMPLATE, "entrega_nao_recebida", ["pedido", "par:8440"]),
        [_menu(), *_cli("já tem 8 dias e nem saiu de SP, vocês podem ver junto com a Shopee?")],
        ctx_pedido=CTX_POSTADO, bloco=_bloco(), postado=acao_pessoa.POSTADO_SIM, valores=VALORES,
        pares_do_pacote=[PAR_PRAZO], conversa=_conv(dados=_retrato(1)),
    )  # fmt: skip
    assert constantes.ACAO_ABRIR_CHAMADO in r.ctx.acoes_pessoa and r.decisao != VAI
    assert not r.ctx.responde_e_marca and r.ctx.acoes_pos_envio == []


def test_d3_o_par_da_resposta_padrao_vai_ao_pacote():
    """Os pares 8440/4489/3450: antes fora do pacote ("não temos acesso ao transporte"),
    agora são a resposta-padrão (D3) — e o resto da régua do pacote não muda."""
    for resposta in (PAR_PRAZO["resposta"], PAR_LOGISTICA["resposta"]):
        assert pares.serve_ao_pacote(resposta), resposta
        assert pares.politica_da_entrega(resposta), resposta
    assert not pares.politica_da_entrega("o reembolso é feito pela plataforma!")
    assert not pares.serve_ao_pacote("seu pedido já foi enviado, acompanhe em minhas compras")


def test_d3_o_turno_da_entrega_no_pacote():
    base = SimpleNamespace(recentes=[_menu(), *_cli("Meu pedido está sem atualização")])
    gatilho = base.recentes[-1]
    assert pacote_svc._turno_da_entrega(base, _bloco(), gatilho)
    assert not pacote_svc._turno_da_entrega(base, {**_bloco(), "entregue": True}, gatilho)
    assert not pacote_svc._turno_da_entrega(
        base, {**_bloco(), "postado": acao_pessoa.POSTADO_NAO}, gatilho
    )
    outro = SimpleNamespace(recentes=[_menu(), *_cli("esse celular tem NFC?")])
    assert not pacote_svc._turno_da_entrega(outro, _bloco(), outro.recentes[-1])


# ── o "6" do menu não é insistência ──


def test_d3_o_6_com_a_pergunta_pendente_e_o_agente_que_responde():
    """22/10 20:31–20:33 e 23/10 02:18: o "6" depois do menu, com a pergunta antes — o
    atendente é o agente. Só "quero falar com uma pessoa" depois de uma resposta, sem nada
    novo, vai para pessoa."""
    respondida = [
        *_cli("Meu pedido está sem atualização"),
        _loja("O seu pedido está em separação no centro logístico."),
        _menu(),
    ]
    assert acao_pessoa.pedido_de_pessoa([*respondida, *_cli("6")], None) is None
    assert (
        acao_pessoa.pedido_de_pessoa([*respondida, *_cli("Ok"), _menu(), *_cli("6")], None) is None
    )
    insiste = acao_pessoa.pedido_de_pessoa([*respondida, *_cli("quero falar com uma pessoa")], None)
    assert insiste is None or insiste  # a 1ª vez depende da régua de sempre
    duas = [
        *_cli("quero falar com uma pessoa"), _loja("Somos o atendimento virtual da loja. Como podemos ajudar?"),
        *_cli("quero falar com uma pessoa"), _loja("O seu pedido está em separação no centro logístico."),
        *_cli("quero falar com uma pessoa"),
    ]  # fmt: skip
    assert acao_pessoa.pedido_de_pessoa(duas, None) is not None
    # O 8e50fabf (23/10 02:18): um "6" antes, a resposta da loja, e agora o relato com o
    # "6" de novo — o agente responde o relato (antes: "insistiu em falar com uma pessoa").
    antes = [
        *_cli("Oi", "6"),
        _loja("Olá! Somos o atendimento da loja. Como podemos ajudar?"),
        _menu(),
    ]
    assert (
        acao_pessoa.pedido_de_pessoa(
            [*antes, *_cli("Já solicitei meu reembolso", "Pois o pedido não chegou", "6")], None
        )
        is None
    )
    # O "6" sozinho de novo, sem nada novo, e o "você não respondeu" + "6": a régua de sempre.
    assert acao_pessoa.pedido_de_pessoa([*antes, *_cli("6")], None) == ia.MOTIVO_INSISTIU_EM_PESSOA
    respondeu = [
        *_cli("o pote de 2,5 vai voltar ao estoque?"),
        _loja("O pote de 2,5 L está esgotado e não temos previsão de reposição."),
        _menu(),
    ]
    assert (
        acao_pessoa.pedido_de_pessoa(
            [*respondeu, *_cli("Você não respondeu a minha pergunta", "6")], None
        )
        is not None
    )
    # O "6" três vezes seguidas continua sendo o 3º pedido (a régua de sempre).
    seis = [
        *_cli("6"),
        _loja("Como podemos ajudar?"),
        *_cli("6"),
        _loja("Em que podemos ajudar?"),
        *_cli("6"),
    ]
    assert acao_pessoa.pedido_de_pessoa(seis, None) == acao_pessoa.MOTIVO_PEDIU_3_VEZES


# ═══════════════ D1. o "responde e marca" ═══════════════


def _reclamacao(
    status: str = "Pedido de devolução aberto",
    acao: str | None = "Responder à solicitação de devolução na Shopee",
    **kw: Any,
) -> dict:
    return {
        "id": "r1", "plataforma": "Shopee", "numero": "x", "tipo": constantes.RECLAMACAO_TIPO_DEVOLUCAO,
        "tipo_rotulo": "Devolução", "status": status, "aberta": True, "prazo_em": None,
        "acao_pendente": acao, "encerrada_em": None, **kw,
    }  # fmt: skip


def _ctx_dev(**kw: Any) -> dict:
    return {
        "pedido": {"numero": "90002", "numeroloja": "261006BBBB0002", "situacao": "Aguardando Devolução",
                   "itens": [{"descricao": "Uranyx C50 5G 4+128 Azul", "quantidade": 1}]},
        "logistica": {"status": "Pedido postado"}, "chamados": [], "devolucoes": [{"status": "em andamento"}],
        "reclamacoes": [_reclamacao()], **kw,
    }  # fmt: skip


BLOCO_DEV = _bloco(
    situacao="Aguardando Devolução", reclamacoes_abertas=1, devolucoes=[{"estado": "em andamento"}],
    reclamacoes=[{"tipo": "Devolução", "estado": "Pedido de devolução aberto", "acao_pendente": "Responder à solicitação de devolução na Shopee"}],
)  # fmt: skip
D1_TEXTO = (
    "A sua solicitação de reembolso está aberta na Shopee, e a equipe analisa a devolução pela "
    "Shopee. O reembolso é feito pela própria Shopee, pela mesma forma de pagamento, e o "
    "andamento aparece em Minhas Compras > o pedido."
)
# Os turnos reais (22–23/10, anonimizados): c91b3ec2/5f54187d, 51bcee3b, db0bb8a9, 8e50fabf.
TURNOS_D1 = [
    ["Eu tô esperando o dinheiro do celular", "Porque só dia 30"],
    ["Eu tô esperando o dinheiro do reembolso do celular"],
    [
        "Eu pedi o reembolso,só que a shopee fecha dia 25, aí eu vou ter que pagar a primeira prestação do celular?"
    ],
    ["Já solicitei meu reembolso", "Pois o pedido não chegou"],
    ["Já solicitei meu reembolso", "Pois o pedido não chegou", "6"],
    ["Como faço para postar o produto de volta?"],
    # 28/10/2026 (1e2dd5ac, anonimizado): o "quero o dinheiro de volta" com a devolução
    # já aberta é a própria solicitação, e "quase duas semanas" não chega a 15 dias.
    [
        "Porque já fiz quase duas semanas",
        "Pôr favor manda o dinheiro do reembolso",
        "Eu quero esse dinheiro urgente",
        "Eu só quero o dinheiro de volta",
    ],
    ["Quero meu reembolso de volta"],
]


@pytest.mark.parametrize("falas", TURNOS_D1)
async def test_d1_o_turno_informativo_responde_e_marca(falas):
    msgs = [_menu(), *_cli(*falas)]
    r = await _auto(
        _p(D1_TEXTO, "reembolso", ["pedido", "par:2003"]), msgs, ctx_pedido=_ctx_dev(), bloco=BLOCO_DEV,
        postado=acao_pessoa.POSTADO_SIM, valores=VALORES, pares_do_pacote=[PAR_REEMBOLSO],
    )  # fmt: skip
    assert r.ctx.responde_e_marca, falas
    assert r.decisao == VAI, (r.codigos, r.motivos)
    agente = r.rascunho.fatos["agente"]
    # A ação continua marcada — só não trava o envio.
    assert agente["responde_e_marca"] is True
    assert agente["acoes_pos_envio"] == [constantes.ACAO_RECLAMACAO_ABERTA]
    assert constantes.ACAO_RECLAMACAO_ABERTA in agente["acoes_codigo"]
    assert constantes.ACAO_RECLAMACAO_ABERTA not in agente["acoes_pessoa"]
    assert ia.responde_e_marca_no_rascunho(
        SimpleNamespace(prompt_versao="ag1", fatos=r.rascunho.fatos)
    )


@pytest.mark.parametrize(
    ("falas", "ctx_kw", "conv_kw"),
    [
        # O pedido à loja e a decisão.
        (["Vocês podem aceitar a minha devolução?"], {}, {}),
        (["Quero cancelar a devolução"], {}, {}),
        (["A Shopee recusou, vocês podem aprovar?"], {}, {}),
        (["Posso ficar com o produto e receber metade?"], {}, {}),
        # Pix, o problema, a ameaça, o atraso longo, a pessoa.
        (["Eu tô esperando o dinheiro", "meu pix é a chave do celular"], {}, {}),
        (["O reembolso não caiu e o produto veio quebrado"], {}, {}),
        (["Já faz 40 dias que pedi o reembolso"], {}, {}),
        (["Faz semanas que espero o reembolso"], {}, {}),
        (["Já são três semanas sem o reembolso"], {}, {}),
        (["Quero o reembolso parcial"], {}, {}),
        (["Quero meu dinheiro de volta, o produto veio quebrado"], {}, {}),
        # A revisão de 28/10/2026: o "quero o reembolso" só é informação SOZINHO, e o
        # "duas semanas" só não é atraso longo quando é isso mesmo.
        (["Quero o reembolso sem devolver o produto"], {}, {}),
        (["Quero meu dinheiro de volta, não vou devolver"], {}, {}),
        (["Quero o reembolso do frete"], {}, {}),
        (["Quero o reembolso de um dos produtos"], {}, {}),
        (["Quero meu dinheiro de volta ou que mandem outro"], {}, {}),
        (["Quero o reembolso na minha conta do nubank"], {}, {}),
        (["Quero o reembolso, o produto veio usado"], {}, {}),
        (["Eu tô esperando o reembolso", "o produto é falsificado"], {}, {}),
        (["Já faz mais de duas semanas que devolvi e nada do reembolso"], {}, {}),
        (["Mais de 2 semanas esperando o reembolso"], {}, {}),
        (["Faz duas semanas e meia que espero o reembolso"], {}, {}),
        (["Vou no Procon se não devolverem meu dinheiro"], {}, {}),
        (["Eu tô esperando o reembolso", "quero falar com uma pessoa"], {}, {}),
        (["Cadê meu reembolso? me manda a nota fiscal"], {}, {}),
        # O chamado no pedido, a reclamação do ML e o canal de reclamação.
        (["Eu tô esperando o dinheiro do celular"], {"chamados": [{"status": "aberto"}]}, {}),
        # 28/10/2026 (25c0a87f): a reclamação em DISPUTA (a loja contestou) é da pessoa.
        (
            ["Quero meu reembolso de volta"],
            {"reclamacoes": [_reclamacao(status="Loja contestou")]},
            {},
        ),
        (
            ["Eu tô esperando o dinheiro do celular"],
            {"reclamacoes": [_reclamacao(status="Em análise pela Shopee (disputa)")]},
            {},
        ),
        (["Eu tô esperando o dinheiro do celular"], {}, {"plataforma": "ml"}),
        (["Eu tô esperando o dinheiro do celular"], {}, {"canal": constantes.CANAL_RECLAMACAO}),
    ],
)
async def test_d1_o_que_nao_e_informativo_continua_com_pessoa(falas, ctx_kw, conv_kw):
    msgs = [_menu(), *_cli(*falas)]
    r = await _auto(
        _p(D1_TEXTO, "reembolso", ["pedido"]), msgs, ctx_pedido=_ctx_dev(**ctx_kw), bloco=BLOCO_DEV,
        postado=acao_pessoa.POSTADO_SIM, valores=VALORES, conversa=_conv(**conv_kw),
    )  # fmt: skip
    assert not r.ctx.responde_e_marca, falas
    assert r.decisao != VAI, (falas, r.codigos)
    assert r.ctx.acoes_pos_envio == []


def _cartao_rr(situacao: str, rr_status: str = "request_submitted", devolucao: int = 7) -> Any:
    conteudo = {
        "rr_detail": {"return_id": devolucao, "status": 1, "translated_detail": []},
        "tracking_info": {"rr_status": rr_status},
        "card_title_for_sender": {
            "translated_with_lang": [
                {
                    "text": f"Status do reembolso feito pelo comprador: {situacao}",
                    "language": "pt-BR",
                }
            ]
        },
    }
    m = _m("loja", "[Mensagem]", origem="externo", tipo="outro")
    m.payload = {"message_type": "track_rr_status_card", "source": "server", "content": conteudo}
    return m


def test_d1_a_disputa_no_cartao_do_chat_fica_com_pessoa():
    """A revisão de 28/10/2026: até a reclamação sincronizar (~1 h), a disputa só está
    no cartão do chat — o D1 não pode responder por cima dela."""
    disputa = _cartao_rr("Disputa com a Shopee solicitada")
    assert acao_pessoa.disputa_nos_cartoes([disputa])
    assert not acao_pessoa.disputa_nos_cartoes([_cartao_rr("Em análise pela Shopee")])
    # O último cartão da MESMA solicitação é o que vale: encerrada, não é mais disputa.
    assert not acao_pessoa.disputa_nos_cartoes(
        [disputa, _cartao_rr("Reembolso concluído", rr_status="refund_completed")]
    )
    msgs = [_menu(), disputa, *_cli("Quero meu reembolso de volta")]
    assert not acao_pessoa.responde_e_marca_do_turno(_conv(), _ctx_dev(), msgs, msgs[-1])
    sem = [_menu(), _cartao_rr("Em análise pela Shopee"), *_cli("Quero meu reembolso de volta")]
    assert acao_pessoa.responde_e_marca_do_turno(_conv(), _ctx_dev(), sem, sem[-1])


def test_d1_sem_devolucao_aberta_nao_ha_o_que_marcar():
    msgs = [_menu(), *_cli("Eu tô esperando o dinheiro do celular")]
    sem = _ctx_dev(reclamacoes=[], devolucoes=[])
    assert not acao_pessoa.responde_e_marca_do_turno(_conv(), sem, msgs, msgs[-1])
    fechada = _ctx_dev(
        reclamacoes=[_reclamacao(aberta=False, encerrada_em="2026-10-22T10:00:00+00:00")]
    )
    assert not acao_pessoa.responde_e_marca_do_turno(_conv(), fechada, msgs, msgs[-1])


def test_d1_o_anexo_e_a_chave_desligada():
    msgs = [
        _menu(),
        *_cli("Eu tô esperando o dinheiro do celular"),
        _m("cliente", None, tipo="imagem"),
    ]
    assert not acao_pessoa.responde_e_marca_do_turno(_conv(), _ctx_dev(), msgs, msgs[-1])
    ok = [_menu(), *_cli("Eu tô esperando o dinheiro do celular")]
    assert acao_pessoa.responde_e_marca_do_turno(_conv(), _ctx_dev(), ok, ok[-1])
    # Outra ação de pessoa do código junto: não marca (a outra trava de qualquer jeito).
    assert not acao_pessoa.responde_e_marca_do_turno(
        _conv(),
        _ctx_dev(),
        ok,
        ok[-1],
        acoes=[constantes.ACAO_RECLAMACAO_ABERTA, constantes.ACAO_PEDIU_PESSOA],
    )
    # A reclamação aberta em OUTRA conversa do comprador (o cartão Cliente): não.
    assert not acao_pessoa.responde_e_marca_do_turno(
        _conv(), _ctx_dev(), ok, ok[-1], ["reclamacao_aberta"]
    )


def test_d1_a_chave_desliga(monkeypatch):
    from app.config import get_settings

    ok = [_menu(), *_cli("Eu tô esperando o dinheiro do celular")]
    monkeypatch.setattr(get_settings(), "atendimento_agente_responde_e_marca", False)
    assert not acao_pessoa.responde_e_marca_do_turno(_conv(), _ctx_dev(), ok, ok[-1])


@pytest.mark.parametrize(
    ("texto", "codigo"),
    [
        (
            "O seu reembolso já foi aprovado pela Shopee e volta pela mesma forma de pagamento.",
            checagens.RESPONDE_E_MARCA,
        ),
        (
            "A sua solicitação está aberta na Shopee e o reembolso cai até o dia 30.",
            checagens.RESPONDE_E_MARCA,
        ),
        (
            "A sua solicitação está aberta na Shopee. Se preferir, nos passe a sua chave Pix.",
            checagens.RESPONDE_E_MARCA,
        ),
        (
            "A sua solicitação está aberta na Shopee; se não cair, abra uma nova solicitação de reembolso.",
            checagens.RESPONDE_E_MARCA,
        ),
        (
            "A sua solicitação está aberta na Shopee e vamos aprovar a devolução.",
            checagens.RESPONDE_E_MARCA,
        ),
        (
            "A sua solicitação está aberta na Shopee e a nossa equipe vai resolver.",
            checagens.RESPONDE_E_MARCA,
        ),
        (
            "A sua solicitação está aberta na Shopee; pode ficar com o produto.",
            checagens.RESPONDE_E_MARCA,
        ),
        (
            "A solicitação está aberta; você vai receber o reembolso em 2 dias úteis.",
            checagens.RESPONDE_E_MARCA,
        ),
        (
            "A solicitação está aberta na Shopee e o reembolso cai no fim do mês.",
            checagens.RESPONDE_E_MARCA,
        ),
        (
            "A solicitação está aberta na Shopee, e o seu reembolso está garantido.",
            checagens.RESPONDE_E_MARCA,
        ),
        (
            "Para pedir a devolução, acesse Minhas Compras > o pedido e escolha a opção de devolução.",
            checagens.RESPONDE_E_MARCA,
        ),
    ],
)
async def test_d1_a_resposta_fora_da_forma_nao_sai(texto, codigo):
    msgs = [_menu(), *_cli("Eu tô esperando o dinheiro do celular", "Porque só dia 30")]
    r = await _auto(
        _p(texto, "reembolso", ["pedido"]), msgs, ctx_pedido=_ctx_dev(), bloco=BLOCO_DEV,
        postado=acao_pessoa.POSTADO_SIM, valores=VALORES,
    )  # fmt: skip
    assert r.ctx.responde_e_marca
    assert codigo in r.codigos, (r.codigos, r.motivos)
    assert r.decisao != VAI


def test_d1_a_forma_permitida_da_equipe():
    ok = checagens.conferir_responde_e_marca(D1_TEXTO, acao="responder", marca=True)
    assert ok == []
    assert (
        checagens.conferir_responde_e_marca("A equipe vai aprovar.", acao="responder", marca=False)
        == []
    )


def test_d1_a_trava_do_envio_e_o_andamento():
    agente = {
        "acoes_pessoa": [], "acoes_codigo": [constantes.ACAO_RECLAMACAO_ABERTA], "responde_e_marca": True,
        "acoes_pos_envio": [constantes.ACAO_RECLAMACAO_ABERTA], "modo": constantes.MODO_AUTO,
        "pistas_orientacao": ["reembolso"], "pistas_turno": [], "sem_auto": [], "avisos_auto": [],
    }  # fmt: skip
    marcado = SimpleNamespace(
        prompt_versao="ag1", categoria="reembolso", fatos={"agente": agente, "devolucoes": 1}
    )
    assert not ia.andamento_no_rascunho(marcado)
    assert ia.trava_do_agente(marcado, SHOPEE_REAL) is None
    # Sem a marca: a devolução para (como antes); com o chamado, sempre.
    sem = SimpleNamespace(
        prompt_versao="ag1",
        categoria="reembolso",
        fatos={"agente": {**agente, "responde_e_marca": False}, "devolucoes": 1},
    )
    assert ia.andamento_no_rascunho(sem)
    chamado = SimpleNamespace(
        prompt_versao="ag1", categoria="reembolso", fatos={"agente": agente, "chamados_abertos": 1}
    )
    assert ia.andamento_no_rascunho(chamado)
    assert not ia.responde_e_marca_no_rascunho(
        SimpleNamespace(prompt_versao="ag1", fatos={"agente": {**agente, "acoes_pos_envio": []}})
    )


def test_d1_a_caixa_humano_cobre_so_a_reclamacao_e_a_acao_dela():
    """A triagem de AGORA (a do envio) diz o que a marca cobre: a reclamação, a devolução e
    a ação dela — e nada com outro motivo."""
    msgs = [_menu(), *_cli("Eu tô esperando o dinheiro do celular")]
    t = humano.triar(
        _conv(),
        msgs,
        _ctx_dev(),
        so_humano=(),
        gatilho=msgs[-1],
        agente=True,
        postado=acao_pessoa.POSTADO_SIM,
        agora=AGORA,
    )
    assert constantes.HUMANO_RECLAMACAO in t.motivos and constantes.HUMANO_ACAO_PESSOA in t.motivos
    assert set(t.marca) == set(t.motivos) & {
        constantes.HUMANO_RECLAMACAO,
        constantes.HUMANO_DEVOLUCAO,
        constantes.HUMANO_ACAO_PESSOA,
    }
    assert set(t.motivos) - set(t.marca) == set()
    pede = [_menu(), *_cli("Vocês podem aceitar a minha devolução?")]
    t2 = humano.triar(
        _conv(),
        pede,
        _ctx_dev(),
        so_humano=(),
        gatilho=pede[-1],
        agente=True,
        postado=acao_pessoa.POSTADO_SIM,
        agora=AGORA,
    )
    assert t2.marca == ()
    chamado = humano.triar(
        _conv(),
        msgs,
        _ctx_dev(chamados=[{"status": "aberto"}]),
        so_humano=(),
        gatilho=msgs[-1],
        agente=True,
        postado=acao_pessoa.POSTADO_SIM,
        agora=AGORA,
    )
    assert chamado.marca == () and constantes.HUMANO_CHAMADO in chamado.motivos


def _epoch(d: datetime) -> float:
    return d.timestamp()


def test_d1_depois_que_a_pessoa_age_na_plataforma_sai_da_caixa_humano():
    """A conversa respondida com a ação pendente fica na Caixa Humano enquanto a loja tem o
    que fazer (o pedido de devolução aberto, a ação pendente); com a devolução só em espera
    (a pessoa aceitou: "Em andamento", sem ação pendente), sai sozinha."""
    turno = AGORA - timedelta(minutes=20)
    marca = {
        "turno": _epoch(turno),
        "ate": _epoch(AGORA + timedelta(minutes=2)),
        "acoes": ["reclamacao_aberta"],
    }
    conv = _conv(aguardando_resposta=False, ultima_do_cliente_em=turno, ultima_da_loja_em=AGORA,
                 sem_resposta_necessaria=False, dados={humano.CHAVE_POS_ENVIO: marca})  # fmt: skip
    msgs = [_menu(), *_cli("Eu tô esperando o dinheiro do celular")]

    def triar(ctx):
        return humano.triar(
            conv,
            msgs,
            ctx,
            so_humano=(),
            gatilho=msgs[-1],
            agente=True,
            postado=acao_pessoa.POSTADO_SIM,
            agora=AGORA,
        )

    pendente = triar(_ctx_dev())
    assert pendente.pos_envio and constantes.HUMANO_RECLAMACAO in pendente.motivos
    assert constantes.ACAO_RECLAMACAO_ABERTA in pendente.assuntos
    espera = _ctx_dev(reclamacoes=[_reclamacao(status="Em andamento", acao=None)])
    feito = triar(espera)
    assert feito.motivos == () and not feito.pos_envio
    # Com o chamado no pedido, continua (o chamado é sempre pessoa).
    assert (
        constantes.HUMANO_CHAMADO in triar({**espera, "chamados": [{"status": "aberto"}]}).motivos
    )
    # Sem a marca (a conversa esperando resposta), a régua de sempre: a reclamação aberta.
    esperando = _conv(aguardando_resposta=True, ultima_do_cliente_em=turno)
    t = humano.triar(
        esperando,
        msgs,
        espera,
        so_humano=(),
        gatilho=msgs[-1],
        agente=True,
        postado=acao_pessoa.POSTADO_SIM,
        agora=AGORA,
    )
    assert constantes.HUMANO_RECLAMACAO in t.motivos


def test_d1_a_marca_depois_do_envio_pura():
    turno = AGORA - timedelta(minutes=20)
    marca = {
        "turno": _epoch(turno),
        "ate": _epoch(AGORA + timedelta(minutes=2)),
        "acoes": ["reclamacao_aberta"],
    }
    conv = _conv(aguardando_resposta=False, ultima_do_cliente_em=turno, ultima_da_loja_em=AGORA,
                 sem_resposta_necessaria=False, dados={humano.CHAVE_POS_ENVIO: marca})  # fmt: skip
    assert humano.pos_envio_vale(conv) and humano.no_escopo(conv)
    # A espera da IA e a IA pausada não valem para ela (só entra pelo motivo).
    assert not humano.ia_sem_resposta(conv, AGORA + timedelta(hours=1))
    # Uma pessoa respondeu depois: sai. O cliente escreveu de novo: sai da marca (volta pela fila).
    assert not humano.pos_envio_vale(
        _conv(**{**vars(conv), "ultima_da_loja_em": AGORA + timedelta(minutes=30)})
    )
    assert not humano.pos_envio_vale(
        _conv(**{**vars(conv), "ultima_do_cliente_em": AGORA + timedelta(minutes=5)})
    )
    assert not humano.pos_envio_vale(_conv(**{**vars(conv), "sem_resposta_necessaria": True}))
    assert not humano.no_escopo(_conv(**{**vars(conv), "situacao": "fechada"}))
    # Sem a marca, como antes: quem não espera resposta não entra.
    assert not humano.no_escopo(_conv(**{**vars(conv), "dados": {}}))
    # A tela: a frase de "respondida pela IA" só com o cálculo do pos_envio.
    assert (
        humano.para_tela({"motivos": ["reclamacao"], "pos_envio": True}, ia_pausada=False)[
            "pos_envio"
        ]
        is True
    )
    assert "pos_envio" not in humano.para_tela({"motivos": ["reclamacao"]}, ia_pausada=False)


async def test_d1_a_marca_no_banco_e_as_duas_reguas(db, make_user):
    """`marcar_pos_envio` grava; `escopo_sql`/`pos_envio_sql` e `no_escopo`/`pos_envio_vale`
    dizem o mesmo antes e depois da marca, depois da resposta de pessoa e depois de o
    cliente escrever de novo."""
    dono = await make_user()
    integ, canal = await _loja_db(db, dono)
    agora = datetime.now(UTC)
    conversa, _ = await gravar.upsert_conversa(
        db, canal=canal, integration=integ, plataforma="shopee", canal_nome="chat",
        externo_id="d1-1", pedido_marketplace="261006BBBB0002", comprador_nome="Comprador",
    )  # fmt: skip
    await gravar.gravar_mensagem(
        db,
        conversa,
        externo_id="d1-c",
        autor="cliente",
        texto="Eu tô esperando o dinheiro do celular",
        enviada_em=agora - timedelta(minutes=10),
    )
    await db.commit()

    async def reguas() -> tuple[bool, bool, bool]:
        async with _db.SessionLocal() as s:
            c, escopo, marca = (
                await s.execute(
                    select(AtendimentoConversa, humano.escopo_sql(), humano.pos_envio_sql()).where(
                        AtendimentoConversa.id == conversa.id
                    )
                )
            ).one()
            assert humano.no_escopo(c) is escopo and humano.pos_envio_vale(c) is marca
            return c.aguardando_resposta, escopo, marca

    assert await reguas() == (True, True, False)
    # A resposta do agente (sai da fila "esperando resposta").
    await gravar.gravar_mensagem(
        db,
        conversa,
        externo_id="d1-ia",
        autor="loja",
        texto=D1_TEXTO,
        enviada_em=agora - timedelta(minutes=1),
        origem=constantes.ORIGEM_IA,
    )
    await db.commit()
    await db.refresh(conversa)
    assert await reguas() == (False, False, False)
    # A marca do "responde e marca".
    await humano.marcar_pos_envio(
        db, conversa, SimpleNamespace(id=uuid4()), [constantes.ACAO_RECLAMACAO_ABERTA]
    )
    await db.commit()
    assert await reguas() == (False, True, True)
    dados = await db.scalar(
        select(AtendimentoConversa.dados).where(AtendimentoConversa.id == conversa.id)
    )
    assert dados[humano.CHAVE_POS_ENVIO]["acoes"] == [constantes.ACAO_RECLAMACAO_ABERTA]
    # Uma pessoa respondeu (pela Central do Vendedor, depois): sai.
    await gravar.gravar_mensagem(
        db,
        conversa,
        externo_id="d1-p",
        autor="loja",
        texto="Sua devolução foi analisada.",
        enviada_em=datetime.now(UTC) + timedelta(minutes=10),
    )
    await db.commit()
    assert await reguas() == (False, False, False)


# ═══════════════ D2. a garantia Apple antes da compra ═══════════════

ITEM_WATCH = "22299473628"
WATCH = {
    "de_onde": "anúncio ligado à conversa", "item_id": ITEM_WATCH,
    "titulo": "Apple Watch Series 11 - 42mm ou 46mm - 1 Ano de Garantia Apple",
    "atributos": ["Marca: Apple", "Smart Wear Model: Serie 11", "Item Type: Smartwatch"],
    "descricao": "Apple Watch S11 (NOVO LACRADO)\n**1 Ano de garantia Apple após ativação do produto**\nO Apple Watch Series 11 traz notificações de frequência cardíaca.",
    "condicao": {"novo": {"valor": True, "fontes": ["descricao"]}, "lacrado": {"valor": True, "fontes": ["descricao"]}, "conflito_no_anuncio": False},
}  # fmt: skip
WATCH_ABERTO = {
    **WATCH,
    "variacoes": [
        {"nome": "42mm Preto", "disponivel": True},
        {"nome": "42mm Aberto com garantia ativada", "disponivel": True},
    ],
}
# Antes da compra: a conversa sem pedido.
PRE_VENDA = _conv(pedido_marketplace=None)
FATO_25 = {
    "fato:25": "Os produtos Apple da Kia são novos e lacrados, com garantia Apple de 1 ano a partir da ativação."
}
D2_TEXTO = "Conforme o anúncio, o Apple Watch Series 11 é novo e lacrado. A garantia é da Apple, de 1 ano a partir da ativação."
# Os turnos reais (Kia, 22/10 17:10, 17:34 e 20:40), sem dado do cliente.
TURNOS_D2 = [
    (
        "Boa tarde esse Apple Watch tá com preço bom é original lacrado posso comprar com garantia",
        "originalidade_condicao",
    ),
    (
        "Boa tarde esse Apple Watch tá com preço bom é original lacrado posso comprar com garantia",
        "garantia",
    ),
    ("É original lacrado garantia da Apple", "garantia"),
]


@pytest.mark.parametrize(("fala", "assunto"), TURNOS_D2)
async def test_d2_a_garantia_apple_do_fato_vai_ao_verificador(fala, assunto):
    r = await _auto(
        _p(D2_TEXTO, assunto, [f"ficha:{ITEM_WATCH}", "fato:25"]),
        [*_cli(fala)],
        fichas=[WATCH],
        fatos_lidos=FATO_25,
        conversa=PRE_VENDA,
    )
    # v16 (27/10/2026): o «é original?» sem "original" no rascunho vai com AVISO ao verificador.
    assert r.decisao in (VAI, VAI_COM_AVISO), (r.codigos, r.motivos)
    assert set(r.codigos) <= {checagens.CONDICAO_SEM_RESPOSTA}, r.codigos


async def test_d2_a_resposta_da_equipe_da_mesma_loja_tambem_e_lastro():
    r = await _auto(
        _p(D2_TEXTO, "originalidade_condicao", [f"ficha:{ITEM_WATCH}", "par:1500"]),
        [*_cli(TURNOS_D2[0][0])],
        fichas=[WATCH],
        pares_do_pacote=[PAR_APPLE],
        conversa=PRE_VENDA,
    )
    # v16 (27/10/2026): o «é original?» sem "original" no rascunho vai com AVISO ao verificador.
    assert r.decisao in (VAI, VAI_COM_AVISO), (r.codigos, r.motivos)
    assert set(r.codigos) <= {checagens.CONDICAO_SEM_RESPOSTA}, r.codigos
    outra = await _auto(
        _p(D2_TEXTO, "originalidade_condicao", [f"ficha:{ITEM_WATCH}", "par:1500"]),
        [*_cli(TURNOS_D2[0][0])],
        fichas=[WATCH],
        pares_do_pacote=[{**PAR_APPLE, "mesma_loja": False}],
        conversa=PRE_VENDA,
    )
    assert outra.decisao != VAI


@pytest.mark.parametrize(
    ("texto", "kw"),
    [
        # A garantia da loja, a legal, a cobertura, outro prazo, o fato sem a condição do anúncio.
        ("Conforme o anúncio, é novo e lacrado. A garantia é de 1 ano com a loja.", {}),
        (
            "Conforme o anúncio, é novo e lacrado. A garantia é da Apple, de 1 ano a partir da ativação, além da garantia legal do Código de Defesa do Consumidor.",
            {},
        ),
        (
            "Conforme o anúncio, é novo e lacrado, e a compra é protegida pela plataforma. A garantia é da Apple, de 1 ano a partir da ativação.",
            {},
        ),
        (
            "Conforme o anúncio, é novo e lacrado. A garantia é da Apple, de 2 anos a partir da ativação.",
            {},
        ),
        (
            "Os produtos Apple da loja são novos e lacrados, com garantia Apple de 1 ano a partir da ativação.",
            {},
        ),
        (
            "Conforme o anúncio, é novo e lacrado. A garantia é da Apple, de 1 ano a partir da ativação, e cobre qualquer defeito.",
            {},
        ),
        # Sem o fato citado; com o pedido (é pós-venda: `ver_garantia`); o anúncio não Apple.
        (D2_TEXTO, {"sem_fato": True}),
        (D2_TEXTO, {"com_pedido": True}),
        (D2_TEXTO, {"nao_apple": True}),
        # A cautela das variações (pares de 07/10): "novo e lacrado" para o anúncio inteiro.
        (D2_TEXTO, {"aberta": True}),
    ],
)
async def test_d2_fora_da_forma_continua_com_pessoa(texto, kw):
    fichas = [WATCH_ABERTO if kw.get("aberta") else WATCH]
    if kw.get("nao_apple"):
        fichas = [
            {
                **WATCH,
                "titulo": "Relógio Smartwatch D20 1 Ano de Garantia",
                "atributos": ["Marca: Genérica"],
            }
        ]
    evid = [f"ficha:{ITEM_WATCH}"] + ([] if kw.get("sem_fato") else ["fato:25"])
    r = await _auto(
        _p(texto, "originalidade_condicao", evid), [*_cli(TURNOS_D2[0][0])], fichas=fichas, fatos_lidos=FATO_25,
        ctx_pedido=CTX_POSTADO if kw.get("com_pedido") else None, bloco=_bloco() if kw.get("com_pedido") else None,
        postado=acao_pessoa.POSTADO_SIM if kw.get("com_pedido") else None, valores=VALORES if kw.get("com_pedido") else None,
        conversa=_conv() if kw.get("com_pedido") else PRE_VENDA,
    )  # fmt: skip
    assert r.decisao != VAI, (texto, kw, r.codigos, r.motivos)
    if kw.get("aberta"):
        assert checagens.CONDICAO_POR_VARIACAO in r.codigos


def test_d2_a_variacao_aberta_e_a_frase_do_fato():
    assert checagens.variacoes_abertas([WATCH_ABERTO]) == ["42mm Aberto com garantia ativada"]
    assert checagens.variacoes_abertas([WATCH]) == []
    # A resposta que separa a variação passa nesta checagem.
    assert (
        checagens.conferir_condicao_por_variacao(
            "A variação 42mm Preto é nova e lacrada; a 42mm Aberto com garantia ativada é aberta.",
            [WATCH_ABERTO],
        )
        == []
    )
    assert checagens.frase_da_garantia_apple("a garantia e da apple, de 1 ano a partir da ativacao")
    assert checagens.frase_da_garantia_apple(
        "o apple watch series 11 tem garantia apple de 1 ano apos a ativacao"
    )
    assert not checagens.frase_da_garantia_apple(
        "a garantia e de 90 dias com a loja e 1 ano com a apple apos a ativacao"
    )
    assert not checagens.frase_da_garantia_apple(
        "garantia apple de 1 ano a partir da ativacao, coberta pela loja"
    )


# ═══════════════ o verificador v12 e a calibração ═══════════════


def test_v12_a_entrada_so_traz_a_acao_da_pessoa_no_responde_e_marca():
    resumo = v.ResumoDoPacote(
        plataforma="Shopee",
        agora="22/10/2026 18:00",
        perguntas=["Eu tô esperando o dinheiro"],
        conversa=[],
        evidencias=[json.dumps(BLOCO_DEV, ensure_ascii=False)],
        plataforma_codigo="shopee",
    )
    sem = v.montar_entrada(
        acao="responder", acao_pessoa=None, texto=D1_TEXTO, resumo=resumo, consultas=[]
    )
    assert v.CABECALHO_POS_ENVIO not in sem
    com = v.montar_entrada(
        acao="responder",
        acao_pessoa=None,
        texto=D1_TEXTO,
        resumo=v.ResumoDoPacote(
            **{**vars(resumo), "pos_envio": (constantes.ACAO_RECLAMACAO_ABERTA,)}
        ),
        consultas=[],
    )
    assert v.CABECALHO_POS_ENVIO in com and "central do vendedor" in com
    assert v.VERSAO == "v16" and v.MODELO == "claude-haiku-5-5"
    assert (
        "AÇÃO QUE CONTINUA COM A PESSOA" in v.SISTEMA and "não vale para essa variação" in v.SISTEMA
    )


GRUPO = "dono_d123"
LINHAS_BOAS = {
    "dono-d1-reembolso-informa-boa", "dono-d1-ja-pedi-nao-chegou-boa", "dono-d2-apple-fato25-boa",
    "dono-d3-sem-atualizacao-par-boa", "dono-d3-hoje-era-o-dia-par-boa",
}  # fmt: skip
LINHAS_RUINS = {
    "dono-d1-aprovado-ruim", "dono-d1-equipe-decide-ruim", "dono-d2-variacao-aberta-ruim",
    "dono-d2-garantia-da-loja-ruim", "dono-d3-cidade-ruim", "dono-d3-data-prometida-ruim",
    "dono-d3-ja-pedimos-ruim",
}  # fmt: skip


def test_v12_a_calibracao_tem_o_grupo_do_dono():
    casos = {
        c["id"]: c for c in calibrar.ler_casos(calibrar.CASOS_PADRAO) if c.get("grupo") == GRUPO
    }
    assert set(casos) == LINHAS_BOAS | LINHAS_RUINS
    for i in LINHAS_BOAS:
        assert casos[i]["esperado"]["ok_para_envio_automatico"] is True, i
    for i in LINHAS_RUINS:
        assert casos[i]["esperado"]["ok_para_envio_automatico"] is False, i
    # Só as do D1 trazem a ação que continua com a pessoa (a entrada das outras não muda).
    for i, c in casos.items():
        assert bool(c.get("pos_envio")) is i.startswith("dono-d1"), i
        assert v.CABECALHO_POS_ENVIO in calibrar.entrada_do_caso(c) or not i.startswith("dono-d1")


# ═══════════════ o bloco 2 ═══════════════


def test_bloco_2_diz_as_tres_decisoes_so_onde_cabem():
    shopee = prompt.bloco_da_plataforma("shopee", _categorias_do_manual())
    assert "não tem acesso ao transporte" in shopee and "par:<id>" in shopee
    assert "responde_e_marca" in shopee and "a equipe analisa a devolução pela Shopee" in shopee
    assert "garantia Apple de 1 ano a partir da ativação" in shopee
    ml = prompt.bloco_da_plataforma("ml", _categorias_do_manual())
    assert "responde_e_marca" not in ml and "garantia Apple de 1 ano a partir da ativação" in ml
    assert len(shopee) / 3.5 <= 5_250  # garantia da equipe (o dono, 27/10/2026)


# ═══════════════ E2E: o D1 inteiro — o agente, o verificador, o envio e a Caixa Humano ═══════════════


@pytest.fixture
def _sem_os_dias(monkeypatch):
    """O pedido do `_pedido_completo` tem 28 dias de postado: a régua dos dias fica de fora."""
    monkeypatch.setattr(acao_pessoa, "DIAS_ATRASO_SHOPEE", 10_000)


async def _conversa_com_devolucao(db, make_user, fala: str):
    await _importar_manual_base(db)
    await _pedido_completo(db)
    conversa = await _conversa_ia(db, make_user, pedido=PEDIDO_MKT)
    db.add(
        AtendimentoReclamacao(
            integration_id=conversa.integration_id, conversa_id=conversa.id, plataforma="shopee",
            externo_id="RET-0001", tipo=constantes.RECLAMACAO_TIPO_DEVOLUCAO, status="REQUESTED",
            pedido_marketplace=PEDIDO_MKT, aberta_em=datetime.now(UTC) - timedelta(days=1),
            dados={"acao_pendente": "SHOPEE_RESPONDER_SOLICITACAO",
                   "acao_texto": "Responder à solicitação de devolução na Shopee"},
        )
    )  # fmt: skip
    await db.commit()
    await _ligar(
        db, conversa, modo=constantes.MODO_AUTO, auto_categorias=SHOPEE_REAL.auto_categorias,
        auto_orientacao=SHOPEE_REAL.auto_orientacao, auto_agente=SHOPEE_REAL.auto_agente,
    )  # fmt: skip
    await _msg_ia(db, conversa, fala)
    return conversa


async def test_e2e_d1_responde_marca_e_continua_na_caixa_humano(
    db, make_user, agente_ligado, api, envios, _sem_os_dias
):
    conversa = await _conversa_com_devolucao(
        db, make_user, "Eu tô esperando o dinheiro do reembolso do celular"
    )
    api.roteiro = [_final(texto=D1_TEXTO, assunto="reembolso", evidencias=["pedido"])]

    r = await ia.gerar_rascunho(db, conversa)

    agente_ = _do_agente(r)
    assert agente_.get("responde_e_marca") is True, (r.motivo, agente_.get("acoes_pessoa"))
    assert agente_["acoes_pos_envio"] == [constantes.ACAO_RECLAMACAO_ABERTA]
    assert agente_["verificador"]["rodou"] is True and agente_["verificador"]["ok"] is True
    assert [e["texto"] for e in envios] == [r.texto] and _decisao(r) == "enviado", r.motivo
    # O verificador recebeu a ação que continua com a pessoa e o estado da solicitação.
    entrada = json.dumps(api.verificador[-1], ensure_ascii=False)
    assert v.CABECALHO_POS_ENVIO in entrada and "Pedido de devolução aberto" in entrada
    # A trava do envio (o `enviar._destino` relê a triagem sob a trava): com o rascunho
    # marcado, a reclamação não segura; sem ele (a pessoa, outro rascunho), segura.
    from app.services.atendimento import enviar

    await db.refresh(conversa)
    assert await ia._motivos_humano_para_auto(db, conversa, r) == ()
    assert constantes.HUMANO_RECLAMACAO in await ia._motivos_humano_para_auto(db, conversa)
    # O espião do envio não grava a mensagem: a resposta do agente chega como a do envio real.
    await gravar.gravar_mensagem(
        db, conversa, externo_id="ia-1", autor="loja", texto=r.texto,
        enviada_em=datetime.now(UTC), origem=constantes.ORIGEM_IA,
    )  # fmt: skip
    await db.commit()
    assert enviar  # o módulo do envio real (a trava acima é a dele)
    # A conversa saiu do "esperando resposta" e continua na Caixa Humano, com a etiqueta.
    await db.refresh(conversa)
    assert not conversa.aguardando_resposta
    assert conversa.dados[humano.CHAVE_POS_ENVIO]["acoes"] == [constantes.ACAO_RECLAMACAO_ABERTA]
    assert humano.no_escopo(conversa) and humano.pos_envio_vale(conversa)
    await humano.recalcular(orcamento_s=None)
    dados = await db.scalar(
        select(AtendimentoConversa.dados).where(AtendimentoConversa.id == conversa.id)
    )
    assert dados["humano"]["pos_envio"] is True
    assert constantes.HUMANO_RECLAMACAO in dados["humano"]["motivos"]
    assert constantes.ACAO_RECLAMACAO_ABERTA in dados["humano"]["assuntos"]
    async with _db.SessionLocal() as s:
        lojas_ = await humano.lojas_da_caixa(s, humano.modos_da_caixa())
        na_caixa = await s.scalar(
            select(humano.caixa_humano_sql(lojas_)).where(AtendimentoConversa.id == conversa.id)
        )
    assert na_caixa is True


async def test_e2e_d1_o_pedido_a_loja_nao_sai_nem_marca(
    db, make_user, agente_ligado, api, envios, _sem_os_dias
):
    conversa = await _conversa_com_devolucao(
        db, make_user, "Vocês podem aceitar a minha devolução?"
    )
    api.roteiro = [_final(texto=D1_TEXTO, assunto="reembolso", evidencias=["pedido"])]

    r = await ia.gerar_rascunho(db, conversa)

    assert envios == [] and _decisao(r) != "enviado"
    assert _do_agente(r).get("responde_e_marca") is not True
    await db.refresh(conversa)
    assert conversa.aguardando_resposta and humano.CHAVE_POS_ENVIO not in (conversa.dados or {})
