# ruff: noqa: F811 — fixtures importadas do e2e do agente
"""Rodada "redondo" da auditoria ao vivo (22–23/10/2026), parte 1: os itens 1 a 4.

A medição desde o deploy de 22/10 14:08 (35 turnos nas 3 Shopee com o agente, 3 enviados
sozinhos): os erros NOSSOS. Cada caso com o rascunho real (sem dado pessoal: o número do
pedido, o rastreio e o lugar do cliente trocados), no caminho AUTO inteiro sem modelo
(`acao_pessoa.gatilhos` → `ia.conferir_saida(agente=True)` → `checagens.conferir` →
`aplicar` → `verificador.por_que_nao_verifica` → `ia.trava_do_agente`, com a configuração
REAL dos canais Shopee de produção) e, no fim, de ponta a ponta pelo `ia._gerar` com o
banco de teste e a API falsa.

1. Rastreio "sem atualização" (25d21d7f 14:15, f06b1b32 16:10, b683b08f 16:40, 300e041a
   18:31, b4d96294 03:16 — o mesmo pedido da Barbosa): o bloco do pedido traz o último
   evento da Shopee do retrato fresco ("O pedido está em separação no centro logístico,
   novas atualizações em até 1 dia.") e o agente o repete; o `pedido_sem_lastro` e o
   validador ("prazo em números só pode vir do sistema") não o aceitavam. Agora a oração
   que só diz palavras do status tem lastro, e o prazo que o próprio status traz também.
   O 14:15 continua para pessoa pelo "regra:rastreio" citado sem o consultar_manual; o
   16:40 e o 18:31, pela confiança do próprio modelo (0,7).
2. Falsos alarmes da 1ª camada: d4f5a308 (Kia 14:42, "Vocês enviam nota fiscal?" lido
   como pergunta de entrega), cdc3507d (Barbosa 21:50, a saudação com "a nossa equipe
   responde por aqui"), 3ee1f81e (Barbosa 22:09, a garantia Uranyx de 1 ano do fato
   aprovado: o "SÃO" entre o número e o par e o "nesses primeiros 90 dias") e 3fe6b65e
   (Barbosa 23:20, "Lamentamos que o vidro tenha chegado arranhado").
3. O verificador barrando resposta boa: a entrada passa a trazer o dado que o rascunho
   usou — as variações da ficha longa (05df6dc5), "os outros tamanhos também esgotados"
   com todas esgotadas (073983a2), a instrução de recebimento (31ab491d). O 4f4bb4a9 (a
   garantia Apple) e o resto do 31ab491d ("assim que o pedido for enviado"): o
   verificador acertou.
4. A queixa do dono: a orientação de outro assunto que o cliente não pediu (cd4ff3d8:
   "Quando o meu pedido chegará?" → "…a forma de cancelar agora é a devolução…") —
   o bloco 2 (`prompt.SO_O_QUE_PERGUNTOU`) e a trava suave
   `checagens.conferir_assunto_nao_pedido`, com os negativos (o cliente pediu).

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

from app.services.atendimento import constantes, ia, validador
from app.services.atendimento.agente import (
    acao_pessoa,
    checagens,
    ferramentas,
    pacote,
    prompt,
    saida,
)
from app.services.atendimento.agente import contexto as agente_ctx
from app.services.atendimento.agente import verificador as v
from app.services.atendimento.agente.saida import Proposta
from scripts.agente_eval import calibrar_verificador as calibrar
from tests.test_atendimento_agente_e2e import (  # noqa: F401 — fixtures
    _decisao,
    _do_agente,
    _final,
    _ligar,
    _sem_a_regua_dos_dias,
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
from tests.test_atendimento_ia import _msg

# ═══════════════ a configuração REAL dos canais Shopee (prod, 23/10/2026) ═══════════════

AUTO_CATEGORIAS = [
    "posvenda_pergunta_publica", "prazo_envio", "rastreio", "entrega_instrucoes", "nota_fiscal",
    "pedido_nao_localizado", "confirmar_variacao", "uso_configuracao", "preco_pagamento",
    "o_que_e_o_anuncio", "originalidade_condicao", "resistencia_agua", "espec_celular",
    "espec_mala", "espec_eletro", "espec_outros", "conteudo_da_caixa", "estoque_variacao",
    "cobertura_regiao", "prazo_antes_compra", "acessorios_avulsos", "pedido_link_midia",
    "opiniao_recomendacao", "parceria_fora_escopo", "canal_externo_dados", "pede_atendente",
    "saudacao_ou_vaga", "agradecimento",
]  # fmt: skip
AUTO_AGENTE = [
    "prazo_envio", "rastreio", "entrega_instrucoes", "nota_fiscal", "pedido_nao_localizado",
    "confirmar_variacao", "uso_configuracao", "preco_pagamento", "estoque_variacao",
    "cobertura_regiao", "prazo_antes_compra", "acessorios_avulsos", "pedido_link_midia",
    "opiniao_recomendacao", "parceria_fora_escopo", "canal_externo_dados", "pede_atendente",
    "saudacao_ou_vaga", "agradecimento",
]  # fmt: skip
AUTO_ORIENTACAO = [
    "cancelamento", "troca_devolucao", "reembolso", "garantia", "defeito",
    "item_errado_faltando", "entrega_nao_recebida", "endereco", "desconto",
    "divergencia_anuncio", "substituicao_estoque", "outro",
]  # fmt: skip
SHOPEE_REAL = SimpleNamespace(
    auto_categorias=AUTO_CATEGORIAS, auto_agente=AUTO_AGENTE, auto_orientacao=AUTO_ORIENTACAO
)
VAI_AO_VERIFICADOR = "verificador"

AGORA = datetime(2026, 10, 22, 18, 0, tzinfo=UTC)
_seq = itertools.count()

MENU_SHOPEE = (
    "Olá, por favor selecione sua dúvida e logo um dos nossos consultores irá atendê-lo!\n\n"
    "1 - Previsão de entrega / envio\n2 - Nota fiscal\n3 - Encerramento da compra\n"
    "4 - Troca de endereço\n5 - Garantia\n6 - Falar com Atendente"
)


def _m(autor: str, texto: str | None, *, origem: str | None = None) -> SimpleNamespace:
    quando = AGORA - timedelta(hours=2) + timedelta(seconds=next(_seq))
    return SimpleNamespace(
        id=uuid4(),
        autor=autor,
        texto=texto,
        tipo="texto",
        payload={},
        origem=origem or autor,
        status="recebida" if autor == "cliente" else "enviada",
        enviada_em=quando,
        created_at=quando,
        anexos=[],
        autor_user_id=None,
        erro=None,
    )


def _cli(*falas: str) -> list[SimpleNamespace]:
    return [_m("cliente", f) for f in falas]


def _menu() -> SimpleNamespace:
    """O menu do Duoke (automático da loja: não fecha o turno)."""
    return _m("loja", MENU_SHOPEE, origem="externo")


def _proposta(texto: str, assunto: str, evidencias: list[str], confianca: float) -> Proposta:
    return Proposta(
        acao="responder",
        texto=texto,
        assunto=assunto,
        confianca=confianca,
        acao_pessoa=None,
        motivo_pessoa="",
        evidencias=evidencias,
    )


async def _auto(
    p: Proposta,
    msgs: list[SimpleNamespace],
    *,
    ctx_pedido: dict | None = None,
    bloco: dict | None = None,
    postado: str | None = None,
    fichas: list[dict] | None = None,
    valores: dict | None = None,
    fatos_lidos: dict[str, str] | None = None,
    ids: list[str] | None = None,
    garantias: list[dict] | None = None,
    plataforma: str = "shopee",
    buscas: list[dict] | None = None,
    fichas_lidas: list[dict] | None = None,
    anuncio_titulo: str | None = None,
    textos_extra: list[str] | None = None,
    acoes_modelo: list[str] | None = None,
) -> SimpleNamespace:
    """O caminho AUTO do `agente.responder` sem banco e sem modelo (a proposta é a do
    rascunho real), com a configuração real dos canais Shopee.

    Parte 2 (itens 6 e 7, 23/10/2026): `buscas` = o que a `buscar_anuncios_da_loja`
    devolveu ({"termo", "acessorios", "modelo", "anuncios": [...]}, como o
    `ctx.registrar_busca`), `fichas_lidas` = o que o `ver_anuncio` leu (com "da_conversa")
    e `anuncio_titulo` = o título do anúncio da conversa (`base.fatos["anuncio"]`).

    Revisão do fechamento (23/10/2026): `textos_extra` = o que outra ferramenta devolveu (o
    `ver_pedido` de OUTRO pedido, no `registro`) e `acoes_modelo` = as ações de pessoa que o
    modelo escolheu (`acao_pessoa` da Proposta)."""
    fichas = list(fichas or [])
    lidas = [dict(f) for f in fichas_lidas or []]
    conversa = SimpleNamespace(
        id=uuid4(),
        plataforma=plataforma,
        canal="chat",
        dados={},
        pedido_marketplace=None,
        etiquetas=[],
        canal_id=None,
        integration_id=None,
        comprador_id=None,
    )
    gatilho = next(x for x in reversed(msgs) if x.autor == "cliente")
    rajada = ia.rajada_do_cliente(msgs, gatilho)
    foco = ia.perguntas_pendentes(msgs, gatilho)
    textos_cliente = [x.texto or "" for x in (foco or rajada)]
    textos_rajada = [x.texto or "" for x in rajada]
    ctxd = ctx_pedido or {"pedido": None, "logistica": None, "chamados": [], "devolucoes": []}
    base = agente_ctx.BaseDoGerar(
        recentes=msgs,
        rajada=rajada,
        foco=foco,
        textos_cliente=textos_cliente,
        textos_rajada=textos_rajada,
        ctx=ctxd,
        valores=dict(valores or {}),
        fatos={"anuncio": anuncio_titulo},
        fatos_gravados={},
        categorias=_categorias_do_manual(),
        regras=[],
        sinais=[],
        motivos=[],
        opcoes_sensiveis=[],
        sinais_do_cliente=[],
        nomes=(),
        modo=constantes.MODO_AUTO,
        forcar=False,
        nf_politica_aprovada=False,
        insistiu=False,
    )
    # acao_pessoa.gatilhos_de_codigo, sem o banco (sem chaves ativas nem UF restrita).
    lista = acao_pessoa.gatilhos(
        conversa, msgs, ctxd, gatilho=gatilho, postado=postado, chaves_ativas=frozenset(),
        agora=AGORA,
    )  # fmt: skip
    ctx = agente_ctx.ContextoAgente.da_conversa(conversa, gatilho, modo=constantes.MODO_AUTO)
    ctx.acoes_codigo = agente_ctx.ordenar_acoes([g.codigo for g in lista])
    ctx.pistas_orientacao = acao_pessoa.pistas_de_orientacao(msgs, gatilho)
    ctx.pistas_turno = acao_pessoa.pistas_do_turno(
        msgs, gatilho, com_pedido=bool(ctxd.get("pedido"))
    )
    # O que o pacote prova (o bloco do pedido, as fichas) e o que as ferramentas leram.
    provados = [agente_ctx.ID_PEDIDO] if bloco else []
    for f in fichas:
        provados.append(agente_ctx.id_ficha(f["item_id"]))
        if f.get("variacoes"):
            provados.append(agente_ctx.id_variacoes(f["item_id"]))
        if f.get("restricao_envio"):
            provados.append(agente_ctx.id_restricao(f["item_id"]))
    ctx.permitir(ids=[*provados, *(ids or [])], textos=[json.dumps(bloco) if bloco else None])
    ctx.permitir(textos=[json.dumps(fichas) if fichas else None], produto=True)
    for i, texto in (fatos_lidos or {}).items():
        ctx.permitir(ids=[i], textos=[texto])
        ctx.registrar_fatos([{"id": i, "texto": texto}])
    for texto in textos_extra or []:
        ctx.permitir(textos=[texto])
    if garantias:
        ctx.registrar_garantias(garantias)
    if garantias is not None:
        ctx.garantia_consultada = True
    # Parte 2: a busca no catálogo (só número permitido; o id citável) e o ver_anuncio.
    for b in buscas or []:
        ids_da_busca = [agente_ctx.id_anuncio(a["item_id"]) for a in b.get("anuncios") or []]
        ctx.permitir(ids=ids_da_busca)
        ctx.permitir_numeros(json.dumps(b, ensure_ascii=False))
        ctx.registrar_busca(b)
    for f in lidas:
        ctx.fichas_lidas.append(f)
        ctx.permitir(
            ids=[agente_ctx.id_ficha(f["item_id"])],
            textos=[json.dumps(f, ensure_ascii=False)],
            produto=True,
        )
    ctx.pistas_turno = list(
        dict.fromkeys([*ctx.pistas_turno, *acao_pessoa.pistas_do_rascunho(p.texto)])
    )
    ctx.acao = p.acao
    ctx.acoes_modelo = [*saida.acoes_do_modelo(p), *(acoes_modelo or [])]
    ctx.evidencias = saida.evidencias(p)
    fatos: dict[str, Any] = {"anuncio": None, "ficha_do_anuncio": fichas}
    if bloco:
        fatos["agente_pedido"] = bloco
    entrada = saida.para_conferir(p, base.ids_categorias)
    entrada["texto"] = prompt.caminho_na_frase(entrada["texto"])
    conferida = await ia.conferir_saida(
        None,
        conversa,
        gatilho,
        **entrada,
        motivos=[],
        ctx=ctxd,
        fatos=fatos,
        valores=dict(valores or {}),
        categorias=base.categorias,
        regras=[],
        textos_cliente=textos_cliente,
        textos_rajada=textos_rajada,
        foco=foco,
        fichas=[*fichas, *lidas],
        garantias_conferidas=ctx.garantias_conferidas,
        garantia_consultada=ctx.garantia_consultada,
        permitidos_extra=ctx.permitidos_extra(),
        modo=constantes.MODO_AUTO,
        forcar=True,
        agente=True,
        fatos_citados=ctx.fatos_citados(ctx.evidencias),
    )
    conferida = checagens.sem_lacuna_vazia(conferida)
    ctx.pedido_da_conversa = bloco
    chk = checagens.conferir(p, conferida, ctx, base, fichas=[*fichas, *lidas], gatilho=gatilho)
    final = checagens.aplicar(
        conferida, chk, acoes_pessoa=ctx.acoes_pessoa, modo=constantes.MODO_AUTO
    )
    nao = v.por_que_nao_verifica(final, p, ctx, base, conversa)
    rascunho = SimpleNamespace(
        prompt_versao="ag1",
        categoria=final.categoria,
        fatos={
            "agente": {
                **ctx.para_fatos(),
                "sem_auto": list(final.sem_auto),
                "avisos_auto": list(final.avisos_auto),
            }
        },
    )
    trava = ia.trava_do_agente(rascunho, SHOPEE_REAL)
    return SimpleNamespace(
        decisao=VAI_AO_VERIFICADOR if nao is None and trava is None else (nao or trava),
        codigos=[x.codigo for x in chk.pendencias],
        motivos=list(final.motivos),
        erros=list(final.erros),
        final=final,
        ctx=ctx,
    )


# ═══════════════ 1. rastreio "sem atualização": o status fresco é o dado ═══════════════

# O evento da Shopee no retrato (o `ultimo_status` do bloco, sem a cidade).
STATUS_SHOPEE = "O pedido está em separação no centro logístico, novas atualizações em até 1 dia."
PEDIDO_1 = "261019AAAA0001"
RASTREIO_1 = "BR000000000001X"
VALORES_1 = {"numero_pedido": PEDIDO_1, "rastreio": RASTREIO_1, "data_envio": "19/10/2026"}


def _bloco_1(status: str | None = STATUS_SHOPEE, **kw: Any) -> dict:
    """O bloco do pedido (`pacote.bloco_do_pedido`) do 2610191… real: postado, não entregue."""
    return {
        "numero": "{numero_pedido}",
        "situacao": "Em andamento",
        "postado": acao_pessoa.POSTADO_SIM,
        "enviado": True,
        "data_envio": "{data_envio}",
        "entregue": False,
        "ultimo_status": status,
        "rastreio": "{rastreio}",
        "chamados": [],
        "devolucoes": [],
        "reclamacoes_abertas": [],
        **kw,
    }


CTX_1 = {
    "pedido": {
        "numero": "90001",
        "numeroloja": PEDIDO_1,
        "situacao": "Em andamento",
        "itens": [{"descricao": "Uranyx A17 Pro Max 12.64 - Laranja", "quantidade": 1}],
    },
    "logistica": {"status": "Pedido postado", "rastreio": RASTREIO_1},
    "chamados": [],
    "devolucoes": [],
}

# Os 5 rascunhos reais (com as lacunas, como o modelo escreveu), as falas do turno e a
# confiança que o modelo deu.
RASCUNHOS_1 = {
    "25d21d7f": (
        ["Meu pedido está sem atualização \nTem como saber se está na minha cidade?", "6", "?",
         "Pedido está sem atualização"],
        "Por aqui não conseguimos informar a cidade onde o pedido está, mas a última "
        "atualização mostra que ele está em separação no centro logístico, com novas "
        "atualizações em até 1 dia. Você pode acompanhar cada movimentação em Minhas Compras "
        "> o pedido > Informações do envio, usando o código de rastreio {rastreio}.",
        ["pedido", "regra:rastreio"],
        0.75,
    ),
    "f06b1b32": (
        ["?"],
        "Entendemos a preocupação com a falta de atualização. Por aqui não temos a cidade "
        "atual do pacote; o último status do seu pedido é que ele está em separação no centro "
        "logístico, com novas atualizações em até 1 dia. Você acompanha o envio em Minhas "
        "Compras > o pedido > Informações do envio.",
        ["pedido"],
        0.8,
    ),
    "b683b08f": (
        ["Ola"],
        "Entendo a preocupação com a falta de atualização. O último status do pedido "
        "{numero_pedido} é de separação no centro logístico, e não temos como confirmar a "
        "cidade em que ele está. As movimentações do envio aparecem em Minhas Compras > o "
        "pedido > Informações do envio.",
        ["pedido"],
        0.7,
    ),
    "300e041a": (
        ["6"],
        "Não consigo confirmar em qual cidade o pacote está neste momento. O último status do "
        'seu pedido é "O pedido está em separação no centro logístico, novas atualizações em '
        'até 1 dia", e o código de rastreio é {rastreio}. Acompanhe as movimentações em Minhas '
        "Compras > o pedido > Informações do envio.",
        ["pedido"],
        0.7,
    ),
    "b4d96294": (
        ["Meu pedido está sem atualização", "Não sei onde esta"],
        "O pedido {numero_pedido} foi postado em {data_envio} e o último status registrado é "
        "de separação no centro logístico. Não informamos a cidade em que o pacote está; você "
        "pode acompanhar as atualizações em Minhas Compras > o pedido > Informações do envio.",
        ["pedido"],
        0.8,
    ),
}  # fmt: skip
# O que o HEAD (e9e301d2) dava, como em produção, e o que fica agora.
ANTES_1 = {
    "25d21d7f": [
        validador.MOTIVO_PRAZO,
        checagens.EVIDENCIA_SEM_PROVA,
        checagens.PEDIDO_SEM_LASTRO,
    ],
    "f06b1b32": [validador.MOTIVO_PRAZO, checagens.PEDIDO_SEM_LASTRO],
    "b683b08f": [checagens.PEDIDO_SEM_LASTRO],
    "300e041a": [validador.MOTIVO_PRAZO, checagens.PEDIDO_SEM_LASTRO],
    "b4d96294": [checagens.PEDIDO_SEM_LASTRO],
}
DEPOIS_1 = {
    # O "regra:rastreio" sem o consultar_manual: a evidência inventada continua segurando.
    "25d21d7f": v.NAO_RODA_PESSOA,
    "f06b1b32": VAI_AO_VERIFICADOR,
    # A confiança do próprio modelo (0,7 < 0,80): a régua de sempre.
    "b683b08f": v.NAO_RODA_CONFIANCA,
    "300e041a": v.NAO_RODA_CONFIANCA,
    "b4d96294": VAI_AO_VERIFICADOR,
}


async def _caso_1(rid: str, bloco: dict | None = None) -> SimpleNamespace:
    falas, texto, evid, conf = RASCUNHOS_1[rid]
    msgs = [*_cli(falas[0]), _menu(), *_cli(*falas[1:])]
    return await _auto(
        _proposta(texto, "rastreio", evid, conf),
        msgs,
        ctx_pedido=CTX_1,
        bloco=bloco or _bloco_1(),
        postado=acao_pessoa.POSTADO_SIM,
        valores=VALORES_1,
    )


@pytest.mark.parametrize("rid", list(RASCUNHOS_1))
async def test_1_os_cinco_rascunhos_reais_passam_o_andamento_e_o_prazo_do_status(rid):
    r = await _caso_1(rid)
    assert checagens.PEDIDO_SEM_LASTRO not in r.codigos, r.motivos
    assert not any(validador.MOTIVO_PRAZO in m for m in r.motivos), r.motivos
    assert r.decisao == DEPOIS_1[rid], (r.decisao, r.motivos)
    if rid == "25d21d7f":
        assert r.codigos == [checagens.EVIDENCIA_SEM_PROVA]


@pytest.mark.parametrize("rid", list(RASCUNHOS_1))
async def test_1_sem_o_status_no_bloco_continua_como_antes(rid):
    """O bloco com o status da linha da logística ("Pedido postado", sem o evento
    fresco): as frases do evento não têm de onde vir — preso, como no HEAD."""
    r = await _caso_1(rid, _bloco_1("Pedido postado"))
    antes = ANTES_1[rid]
    assert checagens.PEDIDO_SEM_LASTRO in r.codigos, r.motivos
    if validador.MOTIVO_PRAZO in antes:
        assert any(validador.MOTIVO_PRAZO in m for m in r.motivos), r.motivos
    assert r.decisao != VAI_AO_VERIFICADOR


@pytest.mark.parametrize(
    "texto",
    [
        # O status dito em parte, com a moldura.
        "Ele está em separação no centro logístico.",
        "O último status do seu pedido é de separação no centro logístico.",
        "A última atualização mostra que o pedido está em separação no centro logístico.",
        # O fragmento sem conteúdo ("em que ele está").
        "Não temos como confirmar a cidade em que ele está; acompanhe em Minhas Compras.",
    ],
)
def test_1_o_status_dito_em_parte_tem_lastro(texto):
    assert (
        checagens.conferir_andamento_sem_lastro(texto, _bloco_1(), None, assunto="rastreio") == []
    )


@pytest.mark.parametrize(
    "texto",
    [
        # O que o status de separação não diz ("parado", "chega").
        "Seu pedido está parado no centro logístico.",
        "Seu pedido chega em até 1 dia.",
        "O último status é de separação no centro logístico, ou seja, chega amanhã.",
        "Ele está em separação no centro logístico e chega amanhã.",
        "Seu pedido está em separação e tudo certo com ele.",
        "O pedido já saiu para entrega.",
        "Seu pedido foi entregue no centro logístico.",
        "Ele não está mais em separação no centro logístico.",
    ],
)
def test_1_o_que_o_status_nao_diz_continua_sem_lastro(texto):
    pend = checagens.conferir_andamento_sem_lastro(texto, _bloco_1(), None, assunto="rastreio")
    assert [p.codigo for p in pend] == [checagens.PEDIDO_SEM_LASTRO], texto


def test_1_o_status_de_outra_etapa_nao_empresta_lastro():
    """Com o status "Aguardando coleta", a separação no centro logístico não tem lastro."""
    bloco = _bloco_1("Aguardando coleta pela transportadora")
    texto = "Ele está em separação no centro logístico."
    pend = checagens.conferir_andamento_sem_lastro(texto, bloco, None, assunto="rastreio")
    assert [p.codigo for p in pend] == [checagens.PEDIDO_SEM_LASTRO]


@pytest.mark.parametrize(
    ("texto", "tira"),
    [
        ("O último status é: em separação no centro logístico, novas atualizações em até 1 dia.",
         True),
        ("Ele está em separação no centro logístico, com novas atualizações em até 1 dia.", True),
        # O mesmo número fora do status: continua prazo.
        ("Seu pedido chega em até 1 dia.", False),
        ("Ele está em separação, e você recebe em até 1 dia.", False),
        ("Novas atualizações devem aparecer em até 2 dias.", False),
        ("Ele está em separação no centro logístico; a entrega leva 1 dia.", False),
    ],
)  # fmt: skip
def test_1_o_prazo_do_validador_so_sai_quando_e_o_do_status(texto, tira):
    assert validador.MOTIVO_PRAZO in validador.validar(
        texto, plataforma="shopee", canal="chat", origem=constantes.ORIGEM_IA
    )
    assert checagens.prazo_do_validador_sem_afirmacao(texto, status=[STATUS_SHOPEE]) is tira
    # Sem o status, só a negativa honesta (a régua da rodada 4).
    assert not checagens.prazo_do_validador_sem_afirmacao(texto)


def test_1_o_prazo_vago_do_status_tambem_e_dado():
    """O evento com "em breve" ("aguarde, em breve novas atualizações"): a regra suave do
    prazo vago não o segura quando a oração só repete o status — e segura fora dele."""
    bloco = _bloco_1("Pedido em trânsito, em breve novas atualizações.")
    assert (
        checagens.conferir_prazo_sem_lastro(
            "Ele está em trânsito, em breve novas atualizações.", [bloco]
        )
        == []
    )  # noqa: E501
    assert checagens.conferir_prazo_sem_lastro("Seu pedido chega em breve.", [bloco])
    assert checagens.conferir_prazo_sem_lastro("Em breve novas atualizações.", [])


# ═══════════════ 2. os falsos alarmes da 1ª camada ═══════════════

# d4f5a308 (Kia, 22/10 14:42): o relógio já recebido e a pergunta da nota.
FALAS_D4F5 = [
    "Olá tudo bem e você? Recebi ontem, tudo certo com o pacote, já estou usando o relógio. "
    "Parabéns pelo trabalho impecável",
    "Vocês enviam nota fiscal?",
]
RASCUNHO_D4F5 = (
    "Que bom saber que chegou tudo certo e que está gostando do relógio, obrigado pela compra! "
    "Sim, todos os pedidos têm nota fiscal, que vai junto com o produto. A nota fiscal do seu "
    "pedido {numero_pedido} é a de número {nf_numero}, e, se precisar do arquivo, nos avise "
    "por aqui."
)
FATO_NF = "Todos os produtos são enviados com nota fiscal, que vai junto com o pedido."
# A regra Apple/Shopee (RJ) da ficha do Apple Watch da Kia.
FICHA_APPLE = {
    "item_id": "22299473628",
    "titulo": "Apple Watch Series 11 - 42mm ou 46mm - 1 Ano de Garantia Apple",
    "de_onde": "item do pedido da conversa",
    "restricao_envio": {
        "fonte": "regra_apple_shopee",
        "fontes": ["regra_apple_shopee"],
        "trecho": None,
        "ufs": ["RJ"],
        "ufs_conforme_pedido": [],
    },
}
MOTIVO_D4F5 = "afirma a entrega sem citar a restrição do anúncio (RJ)"


def _regiao(texto: str, falas: list[str]) -> list:
    return checagens.conferir_regiao(texto, [FICHA_APPLE], None, textos_cliente=falas)


def test_2_d4f5a308_a_pergunta_da_nota_nao_e_de_entrega():
    texto = RASCUNHO_D4F5.replace("{numero_pedido}", "261016AAAA0002").replace("{nf_numero}", "145")
    assert _regiao(texto, FALAS_D4F5) == []
    assert not checagens._pergunta_de_entrega("Vocês enviam nota fiscal?")
    assert not checagens._pergunta_de_entrega("vocês mandam a nota junto?")
    assert not checagens._pergunta_de_entrega("emitem NF-e?")


@pytest.mark.parametrize(
    "fala",
    [
        "Vocês enviam nota fiscal? E entregam no meu bairro?",
        "Vocês enviam pra minha cidade?",
        "manda pro meu endereço?",
        "Chega aqui em casa?",
        "vocês enviam nota fiscal pra quem mora longe? chega lá?",
    ],
)
def test_2_d4f5a308_a_pergunta_de_entrega_continua(fala):
    assert checagens._pergunta_de_entrega(fala)
    assert [p.motivo for p in _regiao("Sim, todos os pedidos têm nota fiscal.", [fala])] == [
        MOTIVO_D4F5
    ]


async def test_2_d4f5a308_no_caminho_auto_vai_ao_verificador():
    ctxd = {
        "pedido": {"numero": "9", "numeroloja": "261016AAAA0002", "situacao": "Entregue"},
        "logistica": {"status": "Pedido entregue"},
        "chamados": [],
        "devolucoes": [],
    }
    bloco = {**_bloco_1("Pedido entregue"), "entregue": True, "nf": "{nf_numero}"}
    r = await _auto(
        _proposta(RASCUNHO_D4F5, "nota_fiscal", ["pedido", "fato:10"], 0.9),
        [*_cli(*FALAS_D4F5), _menu()],
        ctx_pedido=ctxd,
        bloco=bloco,
        postado=acao_pessoa.POSTADO_SIM,
        fichas=[FICHA_APPLE],
        valores={"numero_pedido": "261016AAAA0002", "nf_numero": "145"},
        fatos_lidos={"fato:10": FATO_NF},
    )
    assert r.decisao == VAI_AO_VERIFICADOR, r.motivos


# cdc3507d (Barbosa, 22/10 21:50): "Oi / Boaaa w" (pedido não postado).
RASCUNHO_CDC3 = (
    "Olá! Como podemos ajudar? Se for sobre o seu pedido, conte qual é a sua dúvida que a nossa "
    "equipe responde por aqui."
)


def _promessa(texto: str, assunto: str) -> list[str]:
    return [
        p.codigo
        for p in checagens.conferir_promessa_de_equipe(
            texto, acao="responder", acoes_pessoa=[], assunto=assunto
        )
    ]


@pytest.mark.parametrize("assunto", ["saudacao_ou_vaga", "agradecimento"])
def test_2_cdc3507d_o_convite_da_saudacao_nao_e_promessa(assunto):
    assert _promessa(RASCUNHO_CDC3, assunto) == []
    assert (
        _promessa("Olá! Conte sua dúvida por aqui e a equipe responde aqui mesmo.", assunto) == []
    )


@pytest.mark.parametrize(
    ("texto", "assunto"),
    [
        # Outro assunto: a mesma frase continua promessa.
        (RASCUNHO_CDC3, "rastreio"),
        (RASCUNHO_CDC3, "outro"),
        # Outro verbo, a equipe que vai fazer algo, a frase fora do convite.
        ("Olá! Conte qual é a sua dúvida que a nossa equipe verifica e responde por aqui.",
         "saudacao_ou_vaga"),
        ("Olá! Conte qual é a sua dúvida que a nossa equipe vai analisar e responder.",
         "saudacao_ou_vaga"),
        ("Olá! A nossa equipe responde por aqui.", "saudacao_ou_vaga"),
        ("Olá! Conte a sua dúvida que a equipe retorna por aqui.", "saudacao_ou_vaga"),
        ("Olá! Conte sua dúvida; vamos verificar e a equipe responde por aqui.",
         "saudacao_ou_vaga"),
    ],
)  # fmt: skip
def test_2_cdc3507d_a_promessa_de_verdade_continua(texto, assunto):
    assert _promessa(texto, assunto) == [checagens.PROMESSA_DE_EQUIPE], texto


async def test_2_cdc3507d_no_caminho_auto_vai_ao_verificador():
    ctxd = {
        "pedido": {"numero": "9", "numeroloja": "261023AAAA0003", "situacao": "Em aberto"},
        "logistica": None,
        "chamados": [],
        "devolucoes": [],
    }
    bloco = {**_bloco_1(None), "postado": acao_pessoa.POSTADO_NAO, "enviado": False}
    r = await _auto(
        _proposta(RASCUNHO_CDC3, "saudacao_ou_vaga", ["pedido"], 0.9),
        [*_cli("Oi", "Boaaa w"), _menu()],
        ctx_pedido=ctxd,
        bloco=bloco,
        postado=acao_pessoa.POSTADO_NAO,
    )
    assert r.decisao == VAI_AO_VERIFICADOR, r.motivos


# 3ee1f81e (Barbosa, 22/10 22:09): "Traz um branco / O telefone tem garantia".
RASCUNHO_3EE1 = (
    "Olá! O aparelho Uranyx tem 1 ano de garantia a partir da entrega: os primeiros 90 dias são "
    "com a loja e os 9 meses seguintes são direto com a Uranyx. Se precisar acionar a garantia "
    "nesses primeiros 90 dias, descreva o problema e mande um vídeo curto e fotos por aqui; a "
    "nossa equipe analisa e responde por aqui. Sobre a cor branca, este anúncio tem as opções "
    "Preto, Laranja e Prata, e não há opção branca."
)
FATO_23 = (
    "Aparelhos Uranyx homologados pela Uranyx têm 1 ano de garantia: os 90 primeiros dias com a "
    "loja e os 9 meses seguintes direto com a Uranyx."
)
FATO_24 = (
    "Depois dos 90 primeiros dias, a garantia do aparelho Uranyx é acionada direto com a "
    "Uranyx, pelo app Uranyx ou pelo site uranyx.com.br/sac."
)
FATO_8 = "As malas Charlots têm garantia de 90 dias contra defeitos de fabricação."
FATO_25 = (
    "Os produtos Apple da Kia são novos e lacrados, com garantia Apple de 1 ano a partir da "
    "ativação."
)
# O que o `ver_garantia` devolveu (o pedido não entregue: aguardando; a regra Uranyx).
GARANTIA_URANYX = [
    {
        "pedido": "{numero_pedido}",
        "status": "aguardando",
        "regra": {**ferramentas.REGRA_URANYX, "fatos": ["fato:23", "fato:24"]},
        "periodo": None,
    }
]
FICHA_A17 = {
    "item_id": "22499563693",
    "titulo": "Uranyx A17 Pro Max 16.128 - 12.128 GB - 12.64 Memoria NFC",
    "de_onde": "item do pedido da conversa",
    "variacoes": [
        {"nome": nome, "disponivel": True, "confiavel": True, "fonte": "loja"}
        for nome in ("Laranja,12 GB / 64 GB", "Prata,12 GB / 128 GB", "Preto,12 GB / 128 GB")
    ],
    "variacoes_completas": True,
}


def _erros_do_validador(texto: str, prazos: frozenset) -> list[str]:
    return validador.validar(
        texto,
        plataforma="shopee",
        canal="chat",
        origem=constantes.ORIGEM_IA,
        prazos_de_garantia=prazos,
    )


def test_2_3ee1f81e_os_prazos_do_fato_e_da_regra_com_o_par():
    # Revisão do fechamento (23/10/2026): o prazo de FATO leva a marca "fato:" (e o geral, a
    # marca do produto que o fato diz).
    assert validador.prazos_dos_fatos([FATO_23, FATO_24]) == {
        (1, "ano", "fato:geral:uranyx"),
        (90, "dia", "fato:loja"),
        (9, "mes", "fato:uranyx"),
    }
    assert validador.prazos_dos_fatos([FATO_8]) == {(90, "dia", "fato:geral:charlots")}
    assert validador.prazos_dos_fatos([FATO_25]) == {(1, "ano", "fato:geral:apple")}
    assert validador.prazos_dos_fatos([FATO_NF, "", None]) == frozenset()
    regra = validador.prazos_da_regra(GARANTIA_URANYX)
    # O rascunho real: nenhum prazo sobra, pela regra do ver_garantia OU pelo fato citado.
    for prazos in (regra, validador.prazos_dos_fatos([FATO_23, FATO_24])):
        assert _erros_do_validador(RASCUNHO_3EE1, prazos) == []
    # Sem prazo nenhum (o legado): os dois motivos de sempre.
    assert _erros_do_validador(RASCUNHO_3EE1, frozenset()) == [
        "prazo em números só pode vir do sistema",
        "prazo de garantia só pode vir do sistema",
    ]


@pytest.mark.parametrize(
    "texto",
    [
        # O número do fato com o par ERRADO, ou outro número.
        "A garantia do aparelho é de 90 dias.",
        "A garantia é de 9 meses com a loja.",
        "O aparelho tem 2 anos de garantia.",
        "Os primeiros 90 dias são com a Uranyx.",
        # O demonstrativo sem o prazo dito antes com o par.
        "Se precisar acionar a garantia nesses 90 dias, mande um vídeo por aqui.",
        # Fora da frase da garantia.
        "O reembolso sai em 9 meses.",
        "Seu pedido chega em 1 ano de garantia, quer dizer, em 90 dias.",
    ],
)
def test_2_3ee1f81e_o_prazo_sem_o_par_continua_barrado(texto):
    prazos = validador.prazos_da_regra(GARANTIA_URANYX) | validador.prazos_dos_fatos(
        [FATO_23, FATO_24]
    )
    erros = _erros_do_validador(texto, prazos)
    assert {
        "prazo em números só pode vir do sistema",
        "prazo de garantia só pode vir do sistema",
    } & set(erros), texto  # noqa: E501


async def test_2_3ee1f81e_no_caminho_auto_vai_ao_verificador():
    ctxd = {
        "pedido": {"numero": "9", "numeroloja": "261021AAAA0004", "situacao": "Em andamento"},
        "logistica": {"status": "Pedido postado"},
        "chamados": [],
        "devolucoes": [],
    }
    evid = ["pedido", "fato:23", "fato:24", "variacoes:22499563693", "ficha:22499563693"]
    r = await _auto(
        _proposta(RASCUNHO_3EE1, "garantia", evid, 0.85),
        [*_cli("Traz um branco"), _menu(), *_cli("O telefone tem garantia")],
        ctx_pedido=ctxd,
        bloco=_bloco_1("Pedido postado"),
        postado=acao_pessoa.POSTADO_SIM,
        fichas=[FICHA_A17],
        fatos_lidos={"fato:23": FATO_23, "fato:24": FATO_24},
        garantias=GARANTIA_URANYX,
    )
    assert r.decisao == VAI_AO_VERIFICADOR, r.motivos


# 3fe6b65e (Barbosa, 22/10 23:20): a air fryer entregue, o vidro arranhado.
FALAS_3FE6 = [
    "Te certinho",
    "O vidro veio meio arranha Dinho ",
    "Mas já até usei pq tava precisando",
]
RASCUNHO_3FE6 = (
    "Lamentamos que o vidro tenha chegado arranhado. Para analisarmos, envie por aqui um vídeo "
    "curto mostrando o problema e fotos do vidro, sem dados pessoais na tela. A nossa equipe "
    "analisa e responde por aqui."
)
FICHA_AIR = {
    "item_id": "58212957869",
    "titulo": "Air Fryer Uranyx de Vidro UAF001-M1",
    "de_onde": "item do pedido da conversa",
}


def test_2_3fe6b65e_o_acolhimento_com_que_repete_o_relato():
    oracao = checagens._plano(RASCUNHO_3FE6).split(".")[0]
    assert checagens.relato_ecoado(oracao, FALAS_3FE6)
    assert not checagens._caracteristica_no_relato("o vidro tenha chegado arranhado")
    assert (
        checagens.conferir_orientacao_do_defeito(
            RASCUNHO_3FE6, acao="responder", assunto="defeito", textos_cliente=FALAS_3FE6
        )
        == []
    )
    assert (
        checagens.caracteristicas_fora_do_dado(
            RASCUNHO_3FE6, [FICHA_AIR], textos_cliente=FALAS_3FE6
        )
        == []
    )


@pytest.mark.parametrize(
    ("texto", "falas"),
    [
        # A palavra que o cliente não disse.
        ("Lamentamos que o vidro tenha chegado trincado.", FALAS_3FE6),
        ("Lamentamos que o vidro tenha chegado arranhado.", ["O vidro veio quebrado"]),
        # O diagnóstico, a característica e a condição no relato.
        ("Lamentamos que o vidro tenha chegado arranhado, isso é normal.", FALAS_3FE6),
        ("Lamentamos que o vidro temperado tenha chegado arranhado.", FALAS_3FE6),
        ("Lamentamos que o vidro tenha chegado arranhado de fábrica.", FALAS_3FE6),
        ("Lamentamos que o aparelho original tenha chegado arranhado.",
         ["o aparelho original veio arranhado"]),
    ],
)  # fmt: skip
def test_2_3fe6b65e_o_que_nao_e_eco_continua(texto, falas):
    oracao = checagens._plano(texto).strip(" .")
    assert not checagens.relato_ecoado(oracao, falas), texto
    assert checagens.conferir_orientacao_do_defeito(
        texto, acao="responder", assunto="defeito", textos_cliente=falas
    ), texto


async def test_2_3fe6b65e_no_caminho_auto_vai_ao_verificador():
    ctxd = {
        "pedido": {"numero": "9", "numeroloja": "261010AAAA0005", "situacao": "Entregue"},
        "logistica": {"status": "Pedido entregue"},
        "chamados": [],
        "devolucoes": [],
    }
    r = await _auto(
        _proposta(RASCUNHO_3FE6, "defeito", ["pedido", "ficha:58212957869"], 0.85),
        [*_cli(*FALAS_3FE6), _menu()],
        ctx_pedido=ctxd,
        bloco={**_bloco_1("Pedido entregue"), "entregue": True},
        postado=acao_pessoa.POSTADO_SIM,
        fichas=[FICHA_AIR],
    )
    assert r.decisao == VAI_AO_VERIFICADOR, r.motivos


# ═══════════════ 3. a entrada do verificador traz o dado que o rascunho usou ═══════════════


def _resumo(evidencias: list[str], perguntas: list[str]) -> v.ResumoDoPacote:
    return v.ResumoDoPacote(
        plataforma="Shopee",
        agora="22/10/2026 22:07",
        perguntas=perguntas,
        conversa=[],
        evidencias=evidencias,
        plataforma_codigo="shopee",
        sem_pergunta=False,
        lacunas=(),
    )


def _evidencias_da_entrada(entrada: str) -> str:
    inicio = entrada.index("EVIDÊNCIAS (o que o sistema e as consultas devolveram):")
    return entrada[inicio : entrada.index("RASCUNHO:")]


# 05df6dc5 (Barbosa, 22/10 22:07, "Traz um branco"): o v9 disse que «Este anúncio não tem a
# cor branca» e «as opções são Preto, Laranja e Prata» não tinham lastro. A ficha do pacote
# vai ao verificador como UMA evidência (o JSON com as chaves em ordem: as "variacoes" no
# fim), cortada em `MAX_CHARS_EVIDENCIA`. Reconstruída de ponta a ponta com os dados reais
# do anúncio (`test_e2e_3_…`), a ficha do 05df6dc5 fica em ~3,9 mil caracteres, logo abaixo
# do corte — o mais provável é o verificador ter errado com o dado na frente (as linhas da
# calibração medem); mas a ficha que passa do corte (dois anúncios na conversa, o
# `ver_anuncio` de um anúncio grande) perdia as variações inteiras, e a entrada passa a
# levá-las à parte (`verificador._com_as_variacoes`).
RASCUNHO_05DF = (
    "Este anúncio não tem a cor branca: as opções são Preto, Laranja e Prata. Como seu pedido já "
    "foi postado, a mudança de cor é feita pela devolução ao receber: em Minhas Compras > o "
    "pedido, a Shopee analisa e devolve pela mesma forma de pagamento. A outra cor seria uma "
    "compra nova no anúncio."
)
VARIACOES_A17 = [
    {"nome": nome, "disponivel": disponivel, "confiavel": True, "fonte": "loja"}
    for nome, disponivel in (
        ("A18, Laranja, 16 GB / 128 GB Premium NFC", True),
        ("A18, Prata, 16 GB / 128 GB Premium NFC", True),
        ("A17, Laranja, 12 GB / 128 GB", True),
        ("A17, Laranja, 12 GB / 64 GB", True),
        ("A17, Prata, 12 GB / 128 GB", True),
        ("A17, Prata, 12 GB / 64 GB", False),
        ("A17, Preto, 12 GB / 128 GB", True),
        ("A17, Preto, 12 GB / 64 GB", False),
    )
]


def _ficha_longa() -> dict:
    """A ficha como o pacote a monta (a descrição limpa no teto de 1.200 caracteres, os 20
    atributos do anúncio real e as especificações por fonte) — o tamanho do real."""
    descricao = (
        "URANYX A17 PRO MAX OU A18 PRO MAX - ESCOLHA O SEU\nEste anúncio tem 2 aparelhos: o "
        "A17 Pro Max e o A18 Pro Max. Confira as diferenças abaixo e escolha a variação certa "
        'na hora da compra.\nOPÇÃO 1: A17 PRO MAX\n(variações "12 GB / 64" e "12 GB / 128 GB'
        '")\nArmazenamento: 64 GB ou 128 GB\nMemória RAM: 12 GB (3 GB física + 9 GB virtual)\n'
        "Tela: 6,75 polegadas, 120 Hz\nProcessador: ARS8662 Octa-core (2x Cortex-A76 1,7 GHz + "
        "6x Cortex-A55 1,5 GHz), velocidade média 1,55 GHz\nBateria: 5.160 mAh\nSistema: "
        "Android 15\nCâmera traseira: 13 MP\nCâmera frontal: 5 MP\nNFC: não tem\nRede móvel 4G "
        "e Wi-Fi 5G\nDual chip (2 chips nano)\nOPÇÃO 2: A18 PRO MAX - S5 PREMIUM EDITION (COM "
        'NFC)\n(variação "16 GB / 128 GB Premium NFC")\nACOMPANHA CASE URANYX DE BRINDE\n'
        "Armazenamento: 128 GB\nMemória RAM: 16 GB (4 GB física + 12 GB virtual)\nTela: 6,9 "
        "polegadas, 120 Hz\nProcessador: Unisoc T606 Octa-core\nBateria: 5.000 mAh\nSistema: "
        "Android 15\nCâmera traseira: 50 MP\nCâmera frontal: 8 MP\nNFC: sim\nRede móvel 4G e "
        "Wi-Fi 5G\nDual chip (2 chips nano)…"
    )
    atributos = [
        "Marca: Uranyx", "Battery Capacity: 5160 mAh", "Processor Type: ARS8662", "RAM: 12 GB",
        "Storage Capacity: 64 GB", "Supported Operating System: Android",
        "Screen Size: 6.75 inches", "Primary Camera Resolution: 13 MP", "Build-in Battery: Yes",
        "Number of Cores: Octa-Core", "Model Name: USM003", "Phone Model: A17 Pro Max",
        "Mobile Cable Type: Type C", "SIM Type: Nano", "Cellular: LTE",
        "Phone Type: Smart Phone", "GPS: Yes", "Number of SIM Card Slots: 2", "Quantity: 1",
        "Mobile Phone Features: Touchscreen, Wifi",
    ]  # fmt: skip
    campos = [
        {"campo": campo, "valor": valor, "fonte": "anuncio", "varia_por_modelo": True}
        for campo, valor in (
            ("ram", "12 GB ou 16 GB"), ("ram_fisica", "3 GB ou 4 GB"),
            ("ram_virtual", "9 GB ou 12 GB"), ("armazenamento", "64 GB ou 128 GB"),
            ("rede_5g", "não"), ("wifi_5ghz", "sim"), ("nfc", "A17 não; A18 sim"),
            ("chip", "dual chip (2 nano)"), ("sistema", "Android 15"),
            ("processador", "ARS8662 Octa-core ou Unisoc T606"), ("bateria", "5.160 ou 5.000 mAh"),
            ("tela", "6,75 ou 6,9 polegadas, 120 Hz"), ("camera", "13 MP ou 50 MP"),
        )
    ]  # fmt: skip
    return {
        "atributos": atributos,
        "condicao": {"novo": True, "fonte": "anuncio"},
        "de_onde": "item do pedido da conversa",
        "descricao": descricao,
        "especificacoes": {"campos": campos, "conflitos": [], "conflito_no_anuncio": False},
        "fonte": "loja (Shopee), há menos de um dia",
        "item_id": "22499563693",
        "titulo": "Uranyx A17 Pro Max 16.128 - 12.128 GB - 12.64 Memoria NFC",
        "variacoes": VARIACOES_A17,
        "variacoes_completas": True,
    }


def test_3_a_ficha_que_passa_do_corte_leva_as_variacoes_inteiras():
    """Dois anúncios na conversa (o A17 e o cesto): o JSON das fichas passa do corte e, no
    HEAD, as variações dos dois sumiam da entrada; agora vão à parte, inteiras."""
    fichas = pacote._json([_ficha_longa(), _cesto()])
    assert len(fichas) > v.MAX_CHARS_EVIDENCIA
    cortada = v._uma_linha(fichas, v.MAX_CHARS_EVIDENCIA)
    # O 2º anúncio (o menos recente, no fim) perdia as variações.
    assert "Cesto 4 Litros" not in cortada and "Cesto 6 Litros" not in cortada
    entrada = v.montar_entrada(
        acao="responder",
        acao_pessoa=None,
        texto=RASCUNHO_05DF,
        resumo=_resumo([pacote._json(_bloco_1("Pedido postado")), fichas], ["Traz um branco"]),
        consultas=[],
        assunto="troca_devolucao",
    )
    evid = _evidencias_da_entrada(entrada)
    for variacao in [*VARIACOES_A17, *_cesto()["variacoes"]]:
        assert variacao["nome"] in evid, variacao["nome"]
    # As listas à parte: só o que identifica o anúncio e as variações.
    linhas = [x for x in evid.splitlines() if x.startswith('- {"item_id"')]
    assert [json.loads(x[2:])["item_id"] for x in linhas] == [ITEM_A17, "22995184839"]
    for x in linhas:
        assert set(json.loads(x[2:])) == {"item_id", "titulo", "variacoes", "variacoes_completas"}


def test_3_o_ver_anuncio_grande_tambem():
    """O formato do `ver_anuncio` ("variacoes": {"variacoes": [...], "completas": …})."""
    ficha = {**_ficha_longa(), "descricao": "x " * 1500}
    ficha["variacoes"] = {"variacoes": VARIACOES_A17, "completas": True, "omitidas": 0}
    ficha.pop("variacoes_completas")
    bruto = json.dumps(ficha, ensure_ascii=False, sort_keys=True)
    (_, lista) = v._com_as_variacoes([bruto])
    assert json.loads(lista)["variacoes"]["variacoes"] == VARIACOES_A17


def test_3_a_evidencia_curta_nao_muda():
    curta = pacote._json([FICHA_A17])
    assert len(curta) < v.MAX_CHARS_EVIDENCIA
    assert v._com_as_variacoes([curta, "texto solto"]) == [curta, "texto solto"]
    longa_sem_variacoes = json.dumps({"descricao": "x" * (v.MAX_CHARS_EVIDENCIA + 10)})
    assert v._com_as_variacoes([longa_sem_variacoes]) == [longa_sem_variacoes]


# 073983a2 (Barbosa, 23/10 05:25): "Quero comprar o cesto de 2,5, vocês tem previsão de
# quando retornará ter estoque?" — as três variações esgotadas.
RASCUNHO_0739 = (
    "Olá! No momento o Cesto 2,5 Litros está esgotado no anúncio e não temos previsão de "
    "reposição. Os demais tamanhos também aparecem como esgotados no anúncio neste momento."
)
PERGUNTA_0739 = "Quero comprar o cesto de 2,5, vocês tem previsão de quando retornará ter estoque?"


def _cesto(disponiveis: tuple[bool, bool, bool] = (False, False, False)) -> dict:
    return {
        "item_id": "22995184839",
        "titulo": "Uranyx Cesto De Vidro Para Air Fryers",
        "variacoes": [
            {"nome": nome, "disponivel": d, "confiavel": True, "fonte": "loja"}
            for nome, d in zip(
                ("Cesto 2,5 Litros", "Cesto 4 Litros", "Cesto 6 Litros"), disponiveis, strict=True
            )
        ],
        "variacoes_completas": True,
    }


def _entrada_do_cesto(ficha: dict, texto: str = RASCUNHO_0739) -> str:
    return v.montar_entrada(
        acao="responder",
        acao_pessoa=None,
        texto=texto,
        resumo=_resumo([pacote._json([ficha])], [PERGUNTA_0739]),
        consultas=[],
        assunto="estoque_variacao",
    )


def test_3_073983a2_todas_esgotadas_os_outros_tamanhos_sao_dado():
    entrada = _entrada_do_cesto(_cesto())
    assert v.POLITICA_DE_ESTOQUE in entrada and v.TODAS_ESGOTADAS in entrada
    assert v.todas_esgotadas([pacote._json([_cesto()])])


@pytest.mark.parametrize(
    "disponiveis", [(False, True, False), (False, False, True)], ids=["4L", "6L"]
)
def test_3_073983a2_com_um_tamanho_disponivel_nao(disponiveis):
    """O vizinho ruim: o mesmo texto com o 4 L (ou o 6 L) disponível — a política vem (o
    2,5 esgotado é o citado), a frase "todas esgotadas" não."""
    entrada = _entrada_do_cesto(_cesto(disponiveis))
    assert v.POLITICA_DE_ESTOQUE in entrada and v.TODAS_ESGOTADAS not in entrada


@pytest.mark.parametrize(
    ("disponiveis", "esperado"),
    [
        ((False, False, False), []),
        ((False, True, False), [checagens.VARIACAO_CONTRADIZ]),
        ((False, None, False), [checagens.VARIACAO_SEM_DADO]),
    ],
    ids=["todas_esgotadas", "4L_disponivel", "4L_sem_dado"],
)
def test_3_073983a2_os_demais_esgotados_tambem_na_1a_camada(disponiveis, esperado):
    """O "os demais tamanhos também esgotados" com outro tamanho disponível: antes só o
    verificador conferia (o reposicao-redondo-4l); agora a 1ª camada também."""
    pend = checagens.conferir_variacoes(RASCUNHO_0739, [_cesto(disponiveis)], [PERGUNTA_0739])
    assert [p.codigo for p in pend] == esperado


@pytest.mark.parametrize(
    "texto",
    [
        "Todas as cores estão disponíveis, nenhuma esgotada.",
        "As demais cores seguem disponíveis no anúncio.",
        "Nenhum dos outros tamanhos está esgotado.",
    ],
)
def test_3_os_demais_disponiveis_nao_e_esgotado(texto):
    assert (
        checagens._demais_esgotadas(
            checagens._canonico(texto), checagens._lista_principal([_cesto((True, True, True))])
        )
        == []
    )


def test_3_todas_esgotadas_sem_confiavel_ou_fora_do_estoque_nao():
    nao_confiavel = _cesto()
    nao_confiavel["variacoes"][1]["confiavel"] = False
    assert not v.todas_esgotadas([pacote._json([nao_confiavel])])
    assert not v.todas_esgotadas([])
    # Fora da resposta de estoque, nem a política nem a frase.
    entrada = v.montar_entrada(
        acao="responder",
        acao_pessoa=None,
        texto="Olá! O cesto é de vidro borossilicato.",
        resumo=_resumo([pacote._json([_cesto()])], ["é de vidro?"]),
        consultas=[],
        assunto="espec_eletro",
    )
    assert v.TODAS_ESGOTADAS not in entrada and v.POLITICA_DE_ESTOQUE not in entrada


def test_3_31ab491d_a_instrucao_de_recebimento_vem_na_entrega_instrucoes():
    linhas = v.orientacao_aprovada("entrega_instrucoes", "shopee")
    assert "ORIENTAÇÃO APROVADA DO ASSUNTO (endereco):" in linhas
    assert any("a entrega segue o endereço do pedido" in linha for linha in linhas)
    assert v.assuntos_da_orientacao("entrega_instrucoes") == ["entrega_instrucoes", "endereco"]
    # Os irmãos e os outros assuntos não mudam.
    assert v.assuntos_da_orientacao("reembolso") == ["reembolso", "cancelamento", "troca_devolucao"]
    assert v.assuntos_da_orientacao("rastreio") == ["rastreio"]


def test_3_o_sistema_do_verificador_nao_muda():
    # O item 3 não mudou o SISTEMA (era v9). Parte 2 (item 6, 23/10/2026): o v10 só acrescenta
    # a BUSCA NO CATÁLOGO DA LOJA — o modelo continua o Haiku.
    assert v.VERSAO in ("v10", "v11", "v12") and v.MODELO == "claude-haiku-5-5"


# As linhas novas da calibração (o SISTEMA não muda; a entrada sim): os bons e os ruins.
NOVOS_REDONDO = {
    "boa-redondo-05df6dc5": True,
    "viz-redondo-05df6dc5-branco": False,
    "boa-redondo-073983a2": True,
    "reposicao-redondo-4l": False,
    "boa-redondo-31ab491d-sem-o-quando": True,
    "lastro-redondo-31ab491d": False,
}


def test_3_as_linhas_novas_da_calibracao():
    casos = {c["id"]: c for c in calibrar.ler_casos(calibrar.CASOS_PADRAO)}
    for id_, bom in NOVOS_REDONDO.items():
        caso = casos[id_]
        calibrar.conferir_caso(caso)
        assert caso["esperado"]["ok_para_envio_automatico"] is bom, id_
        assert not calibrar.contaminado(caso), id_
    # A ficha longa: as variações na entrada (o bom e o vizinho ruim, com o Branco).
    for id_ in ("boa-redondo-05df6dc5", "viz-redondo-05df6dc5-branco"):
        evid = _evidencias_da_entrada(calibrar.entrada_do_caso(casos[id_]))
        assert "Prata, 12 GB / 64 GB" in evid, id_
    assert "Branco" in _evidencias_da_entrada(
        calibrar.entrada_do_caso(casos["viz-redondo-05df6dc5-branco"])
    )
    # Todas esgotadas só no bom do cesto.
    assert v.TODAS_ESGOTADAS in calibrar.entrada_do_caso(casos["boa-redondo-073983a2"])
    assert v.TODAS_ESGOTADAS not in calibrar.entrada_do_caso(casos["reposicao-redondo-4l"])
    # A instrução de recebimento nas duas do 31ab491d.
    for id_ in ("boa-redondo-31ab491d-sem-o-quando", "lastro-redondo-31ab491d"):
        assert "a entrega segue o endereço do pedido" in calibrar.entrada_do_caso(casos[id_])


# ═══════════════ 4. só o que o cliente perguntou ═══════════════

# cd4ff3d8 (Barbosa TikTok, 21/10 17:07): o rascunho real.
RASCUNHO_CD4F = (
    "Olá! Seu pedido já foi postado, e você acompanha a previsão de entrega e o andamento do "
    "envio em os pedidos no app do TikTok Shop > o pedido > detalhes do envio. Como ele já foi "
    "enviado, a forma de cancelar agora é a devolução: ao receber, peça a devolução ou o "
    "reembolso pela opção do próprio pedido no app, e o TikTok Shop analisa e devolve pela "
    "mesma forma de pagamento."
)
MENU_TIKTOK = (
    "Agradecemos por entrar em contato. Como posso ajudar hoje?\nQuando o meu pedido chegará?\n"
    "Ainda não recebi o meu pedido.\nHá alguma atualização do meu pedido?\nJá paguei\nComo faço "
    "para cancelar um pedido?"
)
CTX_TK = {
    "pedido": {"numero": "92001", "numeroloja": "577000000000001", "situacao": "Em andamento"},
    "logistica": {"status": "Postado"},
    "chamados": [],
    "devolucoes": [],
}


def _nao_pedido(texto: str, falas: list[str], **kw: Any) -> list[str]:
    return [
        p.codigo
        for p in checagens.conferir_assunto_nao_pedido(
            texto, acao=kw.pop("acao", "responder"), textos_cliente=falas, **kw
        )
    ]


async def test_4_cd4ff3d8_nao_sai_sozinho():
    r = await _auto(
        _proposta(RASCUNHO_CD4F, "rastreio", ["pedido"], 0.8),
        [_m("sistema", MENU_TIKTOK), *_cli("Quando o meu pedido chegará?")],
        ctx_pedido=CTX_TK,
        bloco=_bloco_1("Postado"),
        postado=acao_pessoa.POSTADO_SIM,
        plataforma="tiktok",
    )
    assert r.codigos == [checagens.ASSUNTO_NAO_PEDIDO], r.motivos
    (motivo,) = r.motivos
    assert motivo.startswith("orienta cancelamento que o cliente não pediu (")
    assert r.decisao == v.NAO_RODA_PESSOA
    # Só a resposta ao que perguntou: vai ao verificador.
    so = RASCUNHO_CD4F.split(" Como ele já foi enviado")[0]
    r = await _auto(
        _proposta(so, "rastreio", ["pedido"], 0.8),
        [_m("sistema", MENU_TIKTOK), *_cli("Quando o meu pedido chegará?")],
        ctx_pedido=CTX_TK,
        bloco=_bloco_1("Postado"),
        postado=acao_pessoa.POSTADO_SIM,
        plataforma="tiktok",
    )
    assert r.decisao == VAI_AO_VERIFICADOR, r.motivos


@pytest.mark.parametrize(
    "texto",
    [
        RASCUNHO_CD4F,
        "Seu pedido já foi postado. Caso deseje cancelar, é só pedir a devolução ao receber.",
        "Seu pedido segue no prazo. Se quiser, você pode pedir o reembolso pelo app.",
        "Ele está a caminho; se não gostar, a troca é feita pela devolução.",
        "Seu pedido foi postado. Você quer cancelar?",
        "O último status é Postado. O estorno é feito pela Shopee pela mesma forma de pagamento.",
    ],
)
def test_4_a_orientacao_que_ninguem_pediu_e_suave(texto):
    falas = ["Quando o meu pedido chegará?"]
    assert _nao_pedido(texto, falas, pistas=["rastreio"]) == [checagens.ASSUNTO_NAO_PEDIDO]
    (p,) = checagens.conferir_assunto_nao_pedido(
        texto, acao="responder", textos_cliente=falas, pistas=["rastreio"]
    )
    assert p.so_auto and not p.bloqueia


@pytest.mark.parametrize(
    ("falas", "pistas"),
    [
        (["Quero cancelar meu pedido, quando chega?"], ["cancelamento", "rastreio"]),
        (["Como faço para devolver?"], ["troca_devolucao"]),
        (["Eu tô esperando o dinheiro do celular"], []),
        (["Já solicitei meu reembolso"], ["reembolso"]),
        (["Posso trocar pela outra cor?"], []),
        (["Não quero mais, me arrependi"], []),
        (["3"], ["cancelamento"]),  # a opção do menu (a pista do turno)
        (["Traz um branco"], []),  # com o pedido feito: a cor do pedido
        (["quero mudar o tamanho"], []),
    ],
)
def test_4_quando_o_cliente_pediu_nao_segura(falas, pistas):
    assert _nao_pedido(RASCUNHO_CD4F, falas, pistas=pistas, pedido=_bloco_1("Postado")) == []


def test_4_quando_a_conversa_pede_nao_segura():
    falas = ["ok, como faço?"]
    loja = _m("loja", "Se preferir, você pode pedir a devolução quando receber.")
    texto = "Ao receber, peça a devolução em Minhas Compras > o pedido."
    assert _nao_pedido(texto, falas, recentes=[loja, *_cli(*falas)]) == []
    # Sem a fala da loja: segura.
    assert _nao_pedido(texto, falas, recentes=_cli(*falas)) == [checagens.ASSUNTO_NAO_PEDIDO]
    # O pedido cancelado ou com devolução/reclamação/chamado: o status é do pós-venda.
    for bloco in (
        _bloco_1("Cancelado", postado=acao_pessoa.POSTADO_CANCELADO, situacao="Cancelado"),
        _bloco_1("Postado", devolucoes=[{"estado": "em andamento"}]),
        _bloco_1("Postado", reclamacoes_abertas=[{"tipo": "devolução"}]),
        _bloco_1("Postado", chamados=[{"estado": "aberto"}]),
    ):
        assert _nao_pedido(texto, ["?"], pedido=bloco) == [], bloco
    assert _nao_pedido(texto, ["?"], ctx_conversa={"devolucoes": [{"status": "x"}]}) == []


@pytest.mark.parametrize(
    "texto",
    [
        "Seu pedido foi postado e você acompanha em Minhas Compras > o pedido.",
        # Os falsos amigos: o endereço, o retorno ao remetente.
        "A plataforma não permite trocar o endereço depois do envio.",
        "O último status é de pedido devolvido ao remetente; acompanhe no app.",
    ],
)
def test_4_o_que_nao_e_o_pos_venda_nao_segura(texto):
    assert _nao_pedido(texto, ["Quando chega?"], pistas=["rastreio"]) == []


def test_4_o_rascunho_de_pessoa_nao_e_lido():
    assert _nao_pedido(RASCUNHO_CD4F, ["Quando chega?"], acao="pessoa") == []


def test_4_nenhum_bom_da_calibracao_e_seguro():
    """A trava nova em TODA resposta boa da calibração (as perguntas do caso como o turno):
    nenhuma pendência. Nos ruins que ela pega, só ruins."""
    pegos = set()
    for c in calibrar.ler_casos(calibrar.CASOS_PADRAO):
        perguntas = list(c.get("perguntas") or [])
        pistas = sorted({a for p in perguntas for a in ia.pistas_de_categoria(p)})
        # O bloco do pedido do caso, quando as evidências o trazem.
        pedido = next(
            (e for e in c.get("evidencias") or [] if isinstance(e, dict) and "postado" in e), None
        )
        r = checagens.conferir_assunto_nao_pedido(
            c["texto"],
            acao=c.get("acao") or "responder",
            textos_cliente=perguntas,
            pistas=pistas,
            recentes=calibrar.mensagens_da_conversa(c.get("conversa") or []),
            pedido=pedido,
        )
        if c["esperado"]["ok_para_envio_automatico"]:
            assert r == [], c["id"]
        elif r:
            pegos.add(c["id"])
    assert pegos and all(not c.startswith("boa") for c in pegos), pegos


def test_4_o_bloco_2_manda_responder_so_o_que_foi_perguntado():
    for plataforma in ("shopee", "ml", "tiktok", "amazon"):
        texto = prompt.bloco_da_plataforma(plataforma, _categorias_do_manual())
        cabeca, _assuntos = texto.split("# ASSUNTOS")
        # Parte 2 (item 7, 23/10/2026): as regras vão dentro do COMO PENSAR.
        assert prompt.COMO_PENSAR in cabeca
        assert len(texto) / 3.5 <= 5_000, plataforma  # D1–D3 do dono (23/10/2026)
    s = prompt.COMO_PENSAR
    for trecho in (
        "Responda só o que o cliente perguntou neste turno, em 1 a 3 frases",
        "nunca emende orientação de outro assunto que ele não pediu nem a conversa pede",
        "cancelar, devolver, reembolso, troca, garantia, chamado",
        '"caso deseje cancelar…"',
        "Saudação sem pergunta: pergunte como pode ajudar, sem dizer que a equipe responde.",
    ):
        assert trecho in s, trecho
    # O bloco 1 está no teto e não muda.
    assert prompt.COMO_PENSAR not in prompt.bloco_geral()


# ═══════════════ de ponta a ponta: o `ia._gerar` com o banco e a API falsa ═══════════════
#
# Tudo de verdade como no `test_atendimento_agente_e2e` (o despacho do `_gerar`, o SDK
# sobre o transporte falso, o pacote do banco, a régua, as checagens, a
# `conferir_saida(agente=True)`, o verificador roteirizado e o envio espião), com o canal
# Shopee na configuração REAL de produção.

ITEM_A17 = "22499563693"
TITULO_A17 = "Uranyx A17 Pro Max 16.128 - 12.128 GB - 12.64 Memoria NFC"
# A descrição do anúncio real — o começo é o que a ficha do legado guardou (cortado em
# 735 caracteres); a OPÇÃO 2 segue com as linhas do A18 no mesmo formato, até o teto da
# descrição da ficha (1.200) — e os 20 atributos dele.
DESCRICAO_A17 = (
    "URANYX A17 PRO MAX OU A18 PRO MAX - ESCOLHA O SEU\nEste anúncio tem 2 aparelhos: o A17 Pro "
    "Max e o A18 Pro Max. Confira as diferenças abaixo e escolha a variação certa na hora da "
    'compra.\nOPÇÃO 1: A17 PRO MAX\n(variações "12 GB / 64" e "12 GB / 128 GB")\n'
    "Armazenamento: 64 GB ou 128 GB\nMemória RAM: 12 GB (3 GB física + 9 GB virtual)\nTela: "
    "6,75 polegadas, 120 Hz\nProcessador: ARS8662 Octa-core (2x Cortex-A76 1,7 GHz + 6x "
    "Cortex-A55 1,5 GHz), velocidade média 1,55 GHz\nBateria: 5.160 mAh\nSistema: Android 15\n"
    "Câmera traseira: 13 MP\nCâmera frontal: 5 MP\nNFC: não tem\nRede móvel 4G e Wi-Fi 5G\nDual "
    'chip (2 chips nano)\nOPÇÃO 2: A18 PRO MAX - S5 PREMIUM EDITION (COM NFC)\n(variação "16 GB '
    '/ 128 GB Premium NFC")\nACOMPANHA CASE URANYX DE BRINDE\nArmazenamento: 128 GB\nMemória '
    "RAM: 16 GB (4 GB física + 12 GB virtual)\nTela: 6,9 polegadas, 120 Hz\nProcessador: "
    "Unisoc T606 Octa-core (2x Cortex-A75 1,6 GHz + 6x Cortex-A55 1,6 GHz)\nBateria: 5.000 mAh"
    "\nSistema: Android 15\nCâmera traseira: 50 MP\nCâmera frontal: 8 MP\nNFC: sim\nRede móvel "
    "4G e Wi-Fi 5G\nDual chip (2 chips nano)\nNA CAIXA: aparelho, carregador, cabo USB-C e "
    "manual."
)
ATRIBUTOS_A17 = [
    ("Marca", "Uranyx"), ("Battery Capacity", "5160 mAh"), ("Processor Type", "ARS8662"),
    ("RAM", "12 GB"), ("Storage Capacity", "64 GB"), ("Supported Operating System", "Android"),
    ("Screen Size", "6.75 inches"), ("Primary Camera Resolution", "13 MP"),
    ("Build-in Battery", "Yes"), ("Number of Cores", "Octa-Core"), ("Model Name", "USM003"),
    ("Phone Model", "A17 Pro Max"), ("Mobile Cable Type", "Type C"), ("SIM Type", "Nano"),
    ("Cellular", "LTE"), ("Phone Type", "Smart Phone"), ("GPS", "Yes"),
    ("Number of SIM Card Slots", "2"), ("Quantity", "1"),
    ("Mobile Phone Features", "Touchscreen, Wifi"),
]  # fmt: skip
# Os 8 vínculos do anúncio (variação, SKU, nome do produto no cadastro, a variação no
# anúncio, estoque) — o cadastro chama de "Branco" o que o anúncio chama de "Prata".
VINCULOS_A17 = [
    ("219623382064", "dg088.ci", "Uranyx A18 Pro Max - S5 Edition 16.128 - Laranja",
     "Laranja", "16 GB / 128 GB Premium NFC", 1308),
    ("219623382065", "dg089.ci", "Uranyx A18 Pro Max - S5 Edition 16.128 - Prata",
     "Prata", "16 GB / 128 GB Premium NFC", 1345),
    ("179417245356", "dg053.ci", "Uranyx A17 Pro Max 12.128 - Laranja", "Laranja",
     "12 GB / 128 GB", 520),
    ("179417245357", "dg056.ci", "Uranyx A17 Pro Max 12.64 - Laranja", "Laranja",
     "12 GB / 64 GB", 15),
    ("179417245358", "dg054.ci", "Uranyx A17 Pro Max 12.128 - Branco", "Prata",
     "12 GB / 128 GB", 511),
    ("179417245359", "dg057.ci", "Uranyx A17 Pro Max 12.64 - Branco", "Prata",
     "12 GB / 64 GB", 0),
    ("179417245354", "dg052.ci", "Uranyx A17 Pro Max 12.128 - Preto", "Preto",
     "12 GB / 128 GB", 26),
    ("179417245355", "dg055.ci", "Uranyx A17 Pro Max 12.64 - Preto", "Preto",
     "12 GB / 64 GB", 0),
]  # fmt: skip
# As páginas do site da Uranyx dos dois modelos do anúncio (`atendimento_site_produtos`, a
# 3ª fonte das especificações), como estão em produção.
SITE_A17 = [
    (
        "a17-pro-max",
        "A17 Pro Max",
        "a17 pro max",
        [
            {"valor": "Celulares", "rotulo": "Categoria"},
            {
                "valor": "Conheça as opções disponíveis desta linha com a equipe Uranyx.",
                "rotulo": "Característica",
            },
            {
                "valor": "168,3 × 78,5 × 8,5 mm (altura × largura × espessura)",
                "rotulo": "Dimensões",
            },
            {"valor": "225 g", "rotulo": "Peso"},
            {"valor": '6,75" HD+ 120 Hz', "rotulo": "Tela"},
            {"valor": "Preto, Laranja, Prata", "rotulo": "Opções"},
        ],
    ),
    (
        "s5",
        "A18 Pro Max - S5 Premium Edition",
        "s5",
        [
            {"valor": "Celulares", "rotulo": "Categoria"},
            {"valor": "128 GB de armazenamento interno", "rotulo": "Característica"},
            {"valor": "16 GB de RAM", "rotulo": "Característica"},
            {"valor": "NFC", "rotulo": "Característica"},
            {"valor": "Câmera tripla na traseira", "rotulo": "Característica"},
            {"valor": "Acabamento fosco", "rotulo": "Característica"},
            {"valor": "Acompanha case exclusiva", "rotulo": "Característica"},
            {
                "valor": "aprox. 172 × 79 × 9 mm (altura × largura × espessura)",
                "rotulo": "Dimensões",
            },
            {"valor": "218 g", "rotulo": "Peso"},
            {"valor": '6,9" HD+ 120 Hz', "rotulo": "Tela"},
            {"valor": "Laranja, Branco", "rotulo": "Opções"},
        ],
    ),
]
CORES_A17 = ["Laranja", "Prata", "Preto"]
MEMORIAS_A17 = ["16 GB / 128 GB Premium NFC", "12 GB / 128 GB", "12 GB / 64 GB"]


def _a17_na_loja(lojas) -> dict:
    item = {
        "item_id": int(ITEM_A17),
        "item_name": TITULO_A17,
        "description": DESCRICAO_A17,
        "has_model": True,
        "item_status": "NORMAL",
        "condition": "NEW",
        "attribute_list": [
            {"original_attribute_name": n, "attribute_value_list": [{"original_value_name": x}]}
            for n, x in ATRIBUTOS_A17
        ],
        "brand": {"original_brand_name": "Uranyx"},
    }
    lojas.shopee[ITEM_A17] = item
    lojas.modelos[ITEM_A17] = {
        "tier_variation": [
            {"name": "Cor", "option_list": [{"option": c} for c in CORES_A17]},
            {"name": "Memória", "option_list": [{"option": x} for x in MEMORIAS_A17]},
        ],
        "model": [
            {
                "model_id": int(vid),
                "tier_index": [CORES_A17.index(cor), MEMORIAS_A17.index(memoria)],
                "model_sku": sku,
                "model_status": "MODEL_NORMAL",
                "stock_info_v2": {"summary_info": {"total_available_stock": estoque}},
                "price_info": [{"current_price": 999.9, "original_price": 1299.9}],
            }
            for vid, sku, _nome, cor, memoria, estoque in VINCULOS_A17
        ],
    }
    return item


async def _barbosa_a17(db, make_user, redis_mem, lojas, *, numeroloja: str) -> Any:
    """A conversa da Barbosa no anúncio do A17 com o pedido postado (o 05df6dc5/3ee1f81e)."""
    from app.models import (
        AtendimentoSiteProduto,
        BlingOrder,
        Integration,
        IntegrationPlatform,
        Logistica,
        Product,
        ProductLink,
    )
    from app.models.enums import LinkSyncStatus
    from app.services.atendimento import ficha
    from tests.test_atendimento_ia import _conversa, _importar_manual_base

    await _importar_manual_base(db)
    conversa = await _conversa(db, make_user, pedido=numeroloja, comprador_nome="Cliente Teste")
    conversa.anuncio_id = ITEM_A17
    conversa.anuncio_titulo = None
    integ = await db.get(Integration, conversa.integration_id)
    for vid, sku, nome, cor, memoria, estoque in VINCULOS_A17:
        produto = Product(user_id=integ.user_id, sku=sku, name=nome, stock=estoque)
        db.add(produto)
        await db.flush()
        db.add(
            ProductLink(
                user_id=integ.user_id,
                product_id=produto.id,
                integration_id=integ.id,
                platform=IntegrationPlatform.SHOPEE,
                external_id=ITEM_A17,
                variation_id=vid,
                external_sku=sku,
                listing_title=f"{TITULO_A17} - {cor},{memoria}",
                stock=estoque,
                last_sync_status=LinkSyncStatus.OK,
            )
        )
    for slug, titulo, chave, specs in SITE_A17:
        db.add(
            AtendimentoSiteProduto(
                marca="uranyx",
                slug=slug,
                url=f"https://uranyx.com.br/produto/{slug}",
                titulo=titulo,
                modelo_chave=chave,
                specs=specs,
                lido_em=datetime.now(UTC),
                hash=slug,
            )
        )
    db.add(
        BlingOrder(
            numero="90104",
            numeroloja=numeroloja,
            data=datetime.now(UTC) - timedelta(days=2),
            situacao="15",
            item_index=0,
            item_descricao="Uranyx A17 Pro Max 12.64 - Laranja",
            item_codigo="dg056.ci",
            item_quantidade=1,
        )
    )
    db.add(
        Logistica(
            pedido_bling="90104",
            pedido_marketplace=numeroloja,
            rastreio="BR000000000004X",
            postagem_data=(datetime.now(UTC) - timedelta(days=1)).date(),
            localizacao="Pedido postado",
        )
    )
    await db.commit()
    item = _a17_na_loja(lojas)
    bruta = ficha.ficha_bruta_shopee(item)
    redis_mem.dados[ficha.chave_ficha("shopee", integ.id, ITEM_A17)] = json.dumps(
        {**bruta, "buscada_em": datetime.now(UTC).isoformat()}, ensure_ascii=False
    )
    return conversa


async def _barbosa_rastreio(db, make_user, *, numeroloja: str = PEDIDO_1) -> Any:
    """A conversa do 2610191… (Barbosa): Bling 15 postado, a linha da logística parada em
    "Pedido postado" e o retrato fresco da Shopee com o evento de 22 horas atrás."""
    from app.models import BlingOrder, Logistica
    from tests.test_atendimento_ia import _conversa, _importar_manual_base

    await _importar_manual_base(db)
    conversa = await _conversa(db, make_user, pedido=numeroloja, comprador_nome="Cliente Teste")
    agora = datetime.now(UTC)
    conversa.dados = {
        **(conversa.dados or {}),
        "pedido_mkt": {
            "fonte": "shopee",
            "pedido": numeroloja,
            "status": "SHIPPED",
            "atualizado_em": (agora - timedelta(minutes=5)).isoformat(),
            "logistica": {
                "status": "LOGISTICS_PICKUP_DONE",
                "descricao": STATUS_SHOPEE,
                "atualizado_em": (agora - timedelta(hours=22)).isoformat(),
            },
        },
    }
    db.add(
        BlingOrder(
            numero="90101",
            numeroloja=numeroloja,
            data=agora - timedelta(days=4),
            situacao="15",
            item_index=0,
            item_descricao="Uranyx A17 Pro Max 12.64 - Laranja",
            item_codigo="dg056.ci",
            item_quantidade=1,
        )
    )
    db.add(
        Logistica(
            pedido_bling="90101",
            pedido_marketplace=numeroloja,
            rastreio=RASTREIO_1,
            postagem_data=(agora - timedelta(days=3)).date(),
            localizacao="Pedido postado",
        )
    )
    await db.commit()
    return conversa


async def _canal_real(db, conversa) -> None:
    await _ligar(
        db,
        conversa,
        modo=constantes.MODO_AUTO,
        auto_categorias=AUTO_CATEGORIAS,
        auto_orientacao=AUTO_ORIENTACAO,
        auto_agente=AUTO_AGENTE,
    )


async def test_e2e_1_f06b1b32_o_status_fresco_da_shopee_sai_sozinho(
    db, make_user, agente_ligado, api, envios
):
    conversa = await _barbosa_rastreio(db, make_user)
    await _canal_real(db, conversa)
    await _msg(db, conversa, "Pedido está sem atualização", ha=timedelta(minutes=6))
    _falas, texto, evid, conf = RASCUNHOS_1["f06b1b32"]
    api.roteiro = [_final(texto=texto, assunto="rastreio", confianca=conf, evidencias=evid)]

    r = await ia.gerar_rascunho(db, conversa)

    # O bloco do pedido levou o evento da Shopee (sem a cidade) ao modelo.
    assert "em separação no centro logístico, novas atualizações em até 1 dia" in api.pacote()
    agente = _do_agente(r)
    assert agente["sem_auto"] == [], agente["sem_auto"]
    assert agente["verificador"]["rodou"] is True
    assert [e["texto"] for e in envios] == [r.texto] and _decisao(r) == "enviado", r.motivo


@pytest.mark.parametrize(
    "texto",
    [
        "Olá! Seu pedido chega em até 1 dia; acompanhe em Minhas Compras > o pedido.",
        "Olá! Seu pedido está em separação no centro logístico e chega amanhã.",
    ],
)
async def test_e2e_1_o_que_o_status_nao_diz_nao_sai(
    db, make_user, agente_ligado, api, envios, texto
):
    conversa = await _barbosa_rastreio(db, make_user)
    await _canal_real(db, conversa)
    await _msg(db, conversa, "Pedido está sem atualização", ha=timedelta(minutes=6))
    api.roteiro = [_final(texto=texto, assunto="rastreio", confianca=0.9, evidencias=["pedido"])]

    r = await ia.gerar_rascunho(db, conversa)

    assert _do_agente(r)["sem_auto"], r.motivo
    assert envios == [] and _decisao(r) != "enviado"


async def test_e2e_3_05df6dc5_o_verificador_recebe_todas_as_variacoes(
    db, make_user, agente_ligado, api, envios, redis_mem, lojas
):
    """O 05df6dc5 de ponta a ponta com os dados reais do anúncio (a descrição, os 20
    atributos, os 8 vínculos, as páginas do site): a ficha fica abaixo do corte e as 8
    variações chegam ao verificador — o dado estava na entrada (no HEAD também); o "não"
    do v9 é dele (as linhas boa-redondo-05df6dc5 e viz-redondo-05df6dc5-branco medem)."""
    conversa = await _barbosa_a17(db, make_user, redis_mem, lojas, numeroloja="261021AAAA0004")
    await _canal_real(db, conversa)
    await _msg(db, conversa, "Traz um branco", ha=timedelta(minutes=6))
    await _msg(db, conversa, MENU_SHOPEE, autor="loja", origem="externo", ha=timedelta(minutes=5))
    evid = ["pedido", f"variacoes:{ITEM_A17}", f"ficha:{ITEM_A17}"]
    api.roteiro = [
        _final(texto=RASCUNHO_05DF, assunto="troca_devolucao", confianca=0.85, evidencias=evid)
    ]

    r = await ia.gerar_rascunho(db, conversa)

    agente = _do_agente(r)
    assert agente["verificador"]["rodou"] is True, (r.motivo, agente["sem_auto"])
    entrada = api.verificador[0]["messages"][0]["content"]
    evidencias = _evidencias_da_entrada(entrada)
    for cor in CORES_A17:
        assert cor in evidencias, cor
    # As 8 variações do anúncio, cada uma com o disponivel dela, chegam ao verificador.
    variacoes = [
        x
        for linha in evidencias.splitlines()
        if linha.startswith("- [") or linha.startswith('- {"item_id"')
        for x in v._variacoes_das_evidencias([linha[2:]])
    ]
    assert len({x["nome"] for x in variacoes}) == len(VINCULOS_A17), variacoes
