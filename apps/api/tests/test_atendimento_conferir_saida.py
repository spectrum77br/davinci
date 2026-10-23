# ruff: noqa: F811 — fixtures importadas dos testes da IA
"""`ia.conferir_saida` (20/10/2026): a conferência da saída do modelo, fora do `_gerar`.

O que se prova:

(a) LEGADO IGUAL. `_oraculo` é o bloco do `_gerar` de origin (e1df5d20,
    "O código confere o que o modelo disse" até o `AtendimentoRascunho`)
    copiado sem mudar a régua — só os nomes do módulo ganharam `ia.`. A
    `conferir_saida` com `agente=False` tem de dar o MESMO rascunho (texto,
    assunto, pessoa, motivo na mesma ordem, validador, status): direto, numa
    grade de casos, e pelo `_gerar` de verdade (um espião na chamada, com o
    banco de teste e o modelo falso).
(b) `agente=True`: cada trava de ASSUNTO que muda, uma por teste — e o que
    fica igual (`MOTIVO_FICHA_NO_AUTOMATICO`).
(c) As travas duras (contato fora da plataforma, R$, %, prazo em número,
    promessa de reembolso, dado pessoal, número inventado, data de garantia
    inventada, 83955, chamado) barram igual com `agente=True`.

E as duas mudanças pequenas do mesmo lote: o regex de injeção reconhece as
chaves da saída do agente SÓ no caminho dele (`agente=True`; o legado como em
origin, revisão de 20/10/2026), e o modelo do agente é o Haiku 5.5.

Nada de provedor: o modelo do `_gerar` é o falso de `test_atendimento_ia`.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AtendimentoConversa, AtendimentoRegra
from app.services.atendimento import gravar, ia, validador
from app.services.atendimento import manual as manual_svc
from app.services.atendimento.constantes import (
    MODO_AUTO,
    MODO_COPILOTO,
    ORIGEM_HUMANO,
    ORIGEM_IA,
    RASCUNHO_BLOQUEADO,
    RASCUNHO_PENDENTE,
    RASCUNHO_SUBSTITUIDO,
)
from tests.test_atendimento_ia import (  # noqa: F401 — fixtures
    PEDIDO_MKT,
    ModeloSequencia,
    _conversa,
    _manual_em_camadas,
    _msg,
    _pedido_completo,
    _saida,
    ia_ligada,
    modelo,
)

# ─────────────── o oráculo: o bloco de origin (e1df5d20) ───────────────


async def _oraculo(
    session,
    conversa,
    gatilho,
    *,
    saida: ia.SaidaModelo,
    motivos: list[str],
    classificada: str | None,
    so_humano: set[str],
    textos_cliente: list[str],
    textos_rajada: list[str],
    ids_categorias: list[str],
    foco,
    fichas,
    so_admin: bool,
    texto_ficha: str | None,
    politica_aprovada: Any,
    fatos: dict,
    garantia: dict | None,
    regras,
    categorias: list[dict],
    ctx: dict,
    nf_politica_aprovada: bool,
    valores: dict,
    garantias_conferidas: list[dict] | None,
    forcar: bool,
) -> dict:
    # ── O código confere o que o modelo disse ── (ia.py de e1df5d20)
    categoria = saida.categoria
    if not saida.categoria_valida:
        motivos.append("categoria fora da lista")
    if classificada is not None:
        if saida.categoria_valida and saida.categoria != classificada:
            motivos.append(
                f"a IA mudou de assunto entre a classificação ({classificada}) e a "
                f"resposta ({saida.categoria})"
            )
        categoria = classificada
    if categoria in so_humano:
        motivos.append(f"assunto só para pessoa ({categoria})")
    motivos += ia.motivos_da_categoria(categoria, textos_cliente, textos_rajada, ids_categorias)
    if (
        foco
        and categoria in ("agradecimento", "saudacao_ou_vaga", ia.CATEGORIA_PEDE_ATENDENTE)
        and not (
            categoria == ia.CATEGORIA_PEDE_ATENDENTE
            and all(ia._ATENDENTE.search(validador.plano_de(m.texto or "")) for m in foco)
        )
    ):
        motivos.append("há pergunta concreta pendente — a resposta genérica não a resolve")
    if (
        fichas
        and so_admin
        and categoria in ia.ficha_svc.CATEGORIAS_DA_FICHA
        and texto_ficha is None
        and politica_aprovada is None
    ):
        motivos.append(ia.MOTIVO_FICHA_NO_AUTOMATICO)
    permitidos = "\n".join(
        [
            json.dumps(fatos, ensure_ascii=False),
            ia.datas_da_garantia_por_extenso(garantia),
            *(f"{r.quando} {r.faca}" for r in regras),
            *(c.get("descricao") or "" for c in categorias),
        ]
    )
    inventados = ia.numeros_inventados(saida.resposta, permitidos)
    if inventados:
        motivos.append(f"{ia.MOTIVO_NUMERO_INVENTADO} ({', '.join(inventados[:5])})")
    if ctx.get("chamados") or ctx.get("devolucoes"):
        motivos.append("pedido com chamado ou devolução")
    if ia.em_aguardando_cancelamento(ctx.get("pedido")):
        motivos.append(ia.MOTIVO_AG_CANCELAMENTO)
    if ia.reclamacao_aberta(conversa.dados):
        motivos.append("reclamação/mediação aberta no ML")
    elif ia._reclamacoes_abertas(ctx):
        motivos.append("reclamação ou devolução aberta na plataforma")
    if any(n <= ia.NOTA_BAIXA_AVALIACAO for n in ia._avaliacoes_pendentes(ctx)):
        motivos.append("avaliação de nota baixa (1–3) sem resposta da loja")
    if conversa.pedido_marketplace and ctx.get("pedido") is None:
        motivos.append("pedido não encontrado no sistema")
    elif (
        categoria in ia._CATEGORIAS_DE_PEDIDO
        and ctx.get("pedido") is None
        and not (categoria == "nota_fiscal" and nf_politica_aprovada)
    ):
        motivos.append("conversa sem pedido identificado")

    preenchido, sem_dado, desconhecidas = ia.preencher(saida.resposta, valores)
    if sem_dado:
        motivos.append("sem dado para " + ", ".join("{" + n + "}" for n in sem_dado))
    if desconhecidas:
        motivos.append("lacuna desconhecida " + ", ".join("{" + n + "}" for n in desconhecidas))

    final = validador.normalizar(preenchido, plataforma=conversa.plataforma, canal=conversa.canal)
    from app.services.atendimento import evidencias

    sem_evidencia = evidencias.motivos_sem_evidencia(final, fatos)
    erros = validador.validar(
        final, plataforma=conversa.plataforma, canal=conversa.canal, origem=ORIGEM_IA
    )
    erros = list(
        dict.fromkeys(
            [
                *erros,
                *validador.conferir_garantia(
                    final,
                    garantias_conferidas,
                    outras_datas=(
                        valores.get("previsao_entrega"),
                        valores.get("data_envio"),
                        (fatos.get("pedido") or {}).get("data_da_compra"),
                    ),
                    conferir_promessa=garantia is not None,
                ),
            ]
        )
    )
    if erros:
        motivos.append("validador: " + "; ".join(erros))
    if sem_evidencia:
        motivos.extend(sem_evidencia)
    if validador.fala_de_garantia(final, garantias_conferidas):
        motivos.append(ia.MOTIVO_RESPOSTA_DE_GARANTIA)
    status = (
        RASCUNHO_PENDENTE
        if final and not validador.so_da_ia(erros) and not inventados and not sem_evidencia
        else RASCUNHO_BLOQUEADO
    )
    if (
        status == RASCUNHO_PENDENTE
        and not forcar
        and await ia._loja_respondeu_depois(session, conversa, gatilho)
    ):
        status = RASCUNHO_SUBSTITUIDO
        motivos.append("a loja respondeu enquanto a IA escrevia")
    motivo = "; ".join([*motivos, *([saida.motivo] if saida.motivo else [])])
    return {
        "texto": final or None,
        "categoria": categoria,
        "precisa_humano": saida.precisa_humano or bool(motivos),
        "motivo": motivo[:1000] or None,
        "validador_ok": not erros and not sem_evidencia,
        "validador_erros": [*erros, *sem_evidencia],
        "status": status,
    }


async def _oraculo_de(session, conversa, gatilho, kw: dict) -> dict:
    """O oráculo com os MESMOS dados que a `conferir_saida` recebeu."""
    motivos = list(kw.get("motivos", ()))
    if kw.get("nao_classificou"):
        # Em origin, este motivo entrava na hora da classificação — antes
        # de todos os da conferência e depois de todos os de antes do modelo.
        motivos.append("a IA não classificou o assunto — as regras do assunto ficaram de fora")
    categorias = kw["categorias"]
    return await _oraculo(
        session,
        conversa,
        gatilho,
        saida=ia.SaidaModelo(
            categoria=kw["categoria"],
            categoria_valida=kw.get("categoria_valida", True),
            precisa_humano=kw["precisa_humano"],
            confianca=0.9,
            resposta=kw["texto"],
            motivo=kw.get("motivo_modelo", ""),
        ),
        motivos=motivos,
        classificada=kw.get("classificada"),
        so_humano=manual_svc.ids_so_humano(categorias),
        textos_cliente=kw["textos_cliente"],
        textos_rajada=kw["textos_rajada"],
        ids_categorias=[c["id"] for c in categorias],
        foco=kw.get("foco", ()),
        fichas=kw.get("fichas", ()),
        so_admin=kw["modo"] == MODO_AUTO,
        texto_ficha="literal" if kw.get("resposta_aprovada") else None,
        politica_aprovada=None,
        fatos=kw["fatos"],
        garantia=(
            {"consultada": True, "garantias": kw["garantias_conferidas"] or []}
            if kw.get("garantia_consultada")
            else None
        ),
        regras=kw.get("regras", ()),
        categorias=categorias,
        ctx=kw["ctx"],
        nf_politica_aprovada=kw.get("nf_politica_aprovada", False),
        valores=kw["valores"],
        garantias_conferidas=kw["garantias_conferidas"],
        forcar=kw.get("forcar", False),
    )


def _campos(c: ia.SaidaConferida) -> dict:
    """A `SaidaConferida` nas colunas do rascunho, como o `_gerar` grava."""
    return {
        "texto": c.texto or None,
        "categoria": c.categoria,
        "precisa_humano": c.precisa_humano,
        "motivo": c.motivo,
        "validador_ok": c.validador_ok,
        "validador_erros": c.erros,
        "status": c.status,
    }


def _do_rascunho(r) -> dict:
    return {
        "texto": r.texto,
        "categoria": r.categoria,
        "precisa_humano": r.precisa_humano,
        "motivo": r.motivo,
        "validador_ok": r.validador_ok,
        "validador_erros": r.validador_erros,
        "status": r.status,
    }


# ─────────────── entradas da grade (sem banco: `forcar=True`) ───────────────

CATEGORIAS = manual_svc.categorias_padrao()
GARANTIA_ATIVA = {
    "id": 7,
    "status": "ativa",
    "status_rotulo": "Ativa",
    "inicio": "2026-10-07",
    "fim_hardware": "2027-01-07",
    "fim_software": "2027-10-07",
    "produto": "Celular F110 Pro 5G",
}
_CONVERSA = SimpleNamespace(
    id=uuid4(), plataforma="shopee", canal="chat", dados={}, pedido_marketplace=None
)


def _msg_falsa(texto: str) -> SimpleNamespace:
    return SimpleNamespace(texto=texto)


def _base(**mudar: Any) -> dict:
    kw: dict[str, Any] = {
        "texto": "Olá! Seu pedido {numero_pedido} foi enviado em {data_envio}.",
        "categoria": "rastreio",
        "categoria_valida": True,
        "precisa_humano": False,
        "motivo_modelo": "dados do sistema",
        "motivos": [],
        "ctx": {"pedido": {"numero": PEDIDO_MKT, "situacao": "Atendido"}},
        "fatos": {
            "pedido": {
                "data_da_compra": "20/09/2026",
                "itens": [{"descricao": "Mala de bordo ABS 10kg", "quantidade": 1}],
            }
        },
        "valores": {
            "numero_pedido": PEDIDO_MKT,
            "data_envio": "24/09/2026",
            "rastreio": None,
            "transportadora": None,
            "previsao_entrega": None,
            "nf_numero": None,
        },
        "categorias": CATEGORIAS,
        "regras": [],
        "textos_cliente": ["cadê meu pedido?"],
        "textos_rajada": ["cadê meu pedido?"],
        "foco": [],
        "fichas": [],
        "resposta_aprovada": False,
        "garantias_conferidas": [],
        "garantia_consultada": False,
        "modo": MODO_COPILOTO,
        "classificada": None,
        "nao_classificou": False,
        "nf_politica_aprovada": False,
        "forcar": True,
    }
    kw.update(mudar)
    return kw


def _cliente(texto: str) -> dict:
    return {"textos_cliente": [texto], "textos_rajada": [texto]}


GRADE: list[tuple[str, dict]] = [
    ("ok_com_lacunas", {}),
    ("categoria_fora_da_lista", {"categoria": "outro", "categoria_valida": False}),
    ("mudou_de_assunto", {"classificada": "rastreio", "categoria": "nota_fiscal"}),
    ("fica_o_classificado", {"classificada": "prazo_envio", "categoria": "rastreio"}),
    ("nao_classificou", {"nao_classificou": True, "motivos": ["cliente pediu atendente"]}),
    (
        "assunto_so_humano",
        {"categoria": "cancelamento", **_cliente("quero cancelar o pedido"), "texto": "Ok!"},
    ),
    (
        "so_humano_do_manual",
        {
            "categoria": "duvida_produto",
            "categorias": [*CATEGORIAS[:3], {**CATEGORIAS[3], "so_humano": True}],
            **_cliente("tem a mala azul?"),
            "texto": "Olá!",
        },
    ),
    ("pista_so_humano", {**_cliente("chegou quebrado, quero reembolso")}),
    ("categoria_nao_bate", {**_cliente("qual a nota fiscal?"), "texto": "Olá!"}),
    (
        "generica_com_pergunta_pendente",
        {"categoria": "agradecimento", "foco": [_msg_falsa("qual o prazo?")], "texto": "Nós!"},
    ),
    (
        "pede_atendente_so_com_pedido_de_atendente",
        {
            "categoria": ia.CATEGORIA_PEDE_ATENDENTE,
            "foco": [_msg_falsa("quero falar com um atendente")],
            "texto": "Sou o atendimento da loja, pode falar!",
        },
    ),
    (
        "ficha_no_automatico",
        {
            "categoria": "duvida_produto",
            "fichas": [{"titulo": "Mala"}],
            "modo": MODO_AUTO,
            **_cliente("a mala tem rodinha?"),
            "texto": "Tem sim, quatro rodinhas.",
        },
    ),
    (
        "ficha_com_resposta_aprovada",
        {
            "categoria": "duvida_produto",
            "fichas": [{"titulo": "Mala"}],
            "modo": MODO_AUTO,
            "resposta_aprovada": True,
            **_cliente("a mala tem rodinha?"),
            "texto": "Tem sim.",
        },
    ),
    ("numero_inventado", {"texto": "Olá! Custa 199,90 e mede 55 cm."}),
    ("numero_dos_fatos", {"texto": "A mala é a de 10kg."}),
    (
        "numero_de_ferramenta_sem_permitido",
        {"texto": "O modelo tem 8 GB de RAM."},
    ),
    ("chamado", {"ctx": {"pedido": {"numero": PEDIDO_MKT}, "chamados": [{"id": 1}]}}),
    (
        "ag_cancelamento",
        {"ctx": {"pedido": {"numero": PEDIDO_MKT, "situacao": "Aguardando Cancelamento"}}},
    ),
    (
        "reclamacao_ml",
        {"conversa": {"dados": {"claim_ids": [1]}}},
    ),
    (
        "reclamacao_plataforma",
        {"ctx": {"pedido": {"numero": PEDIDO_MKT}, "reclamacoes": [{"aberta": True}]}},
    ),
    (
        "avaliacao_baixa",
        {
            "ctx": {
                "pedido": {"numero": PEDIDO_MKT},
                "avaliacoes": [{"pendente": True, "estrelas": 2}],
            }
        },
    ),
    ("pedido_nao_encontrado", {"conversa": {"pedido_marketplace": PEDIDO_MKT}, "ctx": {}}),
    ("sem_pedido_identificado", {"ctx": {}, "texto": "Olá! Pode me passar o número?"}),
    (
        "nf_aprovada_sem_pedido",
        {
            "ctx": {},
            "categoria": "nota_fiscal",
            "nf_politica_aprovada": True,
            **_cliente("vem com nota fiscal?"),
            "texto": "Olá! Todos os pedidos acompanham nota fiscal.",
        },
    ),
    ("lacuna_sem_dado", {"texto": "O código de rastreio é {rastreio}."}),
    ("lacuna_desconhecida", {"texto": "Olá {nome_cliente}!"}),
    ("contato_fora", {"texto": "Chama no WhatsApp 11 98888-7777."}),
    ("prazo_em_numero", {"texto": "Chega em 3 dias úteis."}),
    ("sem_evidencia", {"texto": "É original, pode ficar tranquilo."}),
    (
        "garantia_com_data_do_bloco",
        {
            "categoria": "garantia",
            **_cliente("meu celular ainda tem garantia?"),
            "garantias_conferidas": [GARANTIA_ATIVA],
            "garantia_consultada": True,
            "texto": "Sua garantia de hardware vai até 07/01/2027.",
        },
    ),
    (
        "garantia_com_data_inventada",
        {
            "categoria": "garantia",
            **_cliente("meu celular ainda tem garantia?"),
            "garantias_conferidas": [GARANTIA_ATIVA],
            "garantia_consultada": True,
            "texto": "Sua garantia de hardware vai até 08/01/2027.",
        },
    ),
    (
        "promessa_sem_consulta",
        {"texto": "Seu aparelho está coberto pela garantia."},
    ),
    (
        "pergunta_pre_venda",
        {
            "conversa": {"canal": "pergunta"},
            "garantias_conferidas": None,
            "categoria": "duvida_produto",
            **_cliente("tem garantia?"),
            "texto": "Tem garantia de fábrica.",
        },
    ),
    ("texto_vazio", {"texto": "   "}),
    ("motivo_comprido", {"motivo_modelo": "x" * 1200, "motivos": ["y" * 50]}),
    ("sem_motivo_do_modelo", {"motivo_modelo": "", "precisa_humano": True}),
    ("emoji_na_amazon", {"conversa": {"plataforma": "amazon"}, "texto": "Olá! 😀"}),
    (
        "regras_e_motivos_de_antes",
        {
            "regras": [SimpleNamespace(quando="prazo de 7 dias", faca="diga 7 dias")],
            "motivos": ["cliente pediu atendente", "recado ao entregador"],
            "texto": "O prazo de troca é de 7 dias.",
        },
    ),
]


def _chamada(caso: dict) -> tuple[Any, dict]:
    kw = dict(caso)
    conversa = SimpleNamespace(**{**vars(_CONVERSA), **kw.pop("conversa", {})})
    return conversa, kw


# ─────────────── (a) o legado: igual a origin ───────────────


@pytest.mark.parametrize("nome,mudar", GRADE, ids=[n for n, _ in GRADE])
async def test_legado_igual_a_origin_direto(nome, mudar):
    conversa, kw = _chamada(_base(**mudar))
    gatilho = SimpleNamespace()
    esperado = await _oraculo_de(None, conversa, gatilho, kw)
    conferida = await ia.conferir_saida(None, conversa, gatilho, **kw)
    assert _campos(conferida) == esperado
    assert conferida.avisos_auto == []
    assert conferida.bloqueado is (esperado["status"] == RASCUNHO_BLOQUEADO)


async def test_a_grade_cobre_todos_os_desfechos():
    """A grade não é só de casos felizes: pendente, bloqueado e cada trava."""
    saidas = []
    for _nome, mudar in GRADE:
        conversa, kw = _chamada(_base(**mudar))
        saidas.append(await ia.conferir_saida(None, conversa, SimpleNamespace(), **kw))
    assert {s.status for s in saidas} == {RASCUNHO_PENDENTE, RASCUNHO_BLOQUEADO}
    assert any(not s.precisa_humano for s in saidas)
    motivos = " | ".join(m for s in saidas for m in s.motivos)
    for trecho in (
        "categoria fora da lista",
        "mudou de assunto",
        "não classificou",
        "assunto só para pessoa",
        "o cliente fala de assunto só para pessoa",
        "não bate com o texto do cliente",
        "há pergunta concreta pendente",
        ia.MOTIVO_FICHA_NO_AUTOMATICO,
        ia.MOTIVO_NUMERO_INVENTADO,
        "pedido com chamado ou devolução",
        ia.MOTIVO_AG_CANCELAMENTO,
        "reclamação/mediação aberta no ML",
        "reclamação ou devolução aberta na plataforma",
        "avaliação de nota baixa",
        "pedido não encontrado no sistema",
        "conversa sem pedido identificado",
        "sem dado para {rastreio}",
        "lacuna desconhecida {nome_cliente}",
        "validador:",
        ia.MOTIVO_RESPOSTA_DE_GARANTIA,
    ):
        assert trecho in motivos, trecho


@pytest.fixture
def espiao(monkeypatch):
    """Espião na `conferir_saida` do `_gerar`: guarda o que ela recebeu, o que
    devolveu e o que o oráculo de origin daria com os mesmos dados."""
    chamadas: list[tuple[dict, ia.SaidaConferida, dict]] = []
    real = ia.conferir_saida

    async def _espia(session, conversa, gatilho, **kw):
        conferida = await real(session, conversa, gatilho, **kw)
        esperado = await _oraculo_de(session, conversa, gatilho, kw)
        chamadas.append((kw, conferida, esperado))
        return conferida

    monkeypatch.setattr(ia, "conferir_saida", _espia)
    return chamadas


def _confere_pelo_gerar(espiao, r) -> tuple[dict, ia.SaidaConferida]:
    assert r is not None and len(espiao) == 1
    kw, conferida, esperado = espiao[0]
    assert kw.get("agente", False) is False  # o legado nunca liga o agente
    assert _do_rascunho(r) == _campos(conferida) == esperado
    return kw, conferida


async def test_legado_pelo_gerar_rascunho_ok(
    db: AsyncSession, make_user, ia_ligada, modelo, espiao
):
    await _pedido_completo(db)
    conversa = await _conversa(db, make_user)
    await _msg(db, conversa, "cadê meu pedido? ainda não chegou")
    modelo.saida = _saida(
        "Olá! Seu pedido {numero_pedido} foi enviado em {data_envio} pela {transportadora}."
    )

    r = await ia.gerar_rascunho(db, conversa)

    kw, conferida = _confere_pelo_gerar(espiao, r)
    assert r.status == RASCUNHO_PENDENTE and r.precisa_humano is False
    assert kw["modo"] == MODO_COPILOTO and kw["garantia_consultada"] is False


async def test_legado_pelo_gerar_assunto_so_humano_no_automatico(
    db: AsyncSession, make_user, ia_ligada, modelo, espiao
):
    conversa = await _conversa(
        db, make_user, modo="auto", auto_categorias=["troca_devolucao"], pedido=None
    )
    await _msg(db, conversa, "quero trocar, veio a cor errada")
    modelo.saida = _saida(
        "Sentimos muito! Vamos verificar.", categoria="troca_devolucao", confianca=0.99
    )

    r = await ia.gerar_rascunho(db, conversa)

    kw, _ = _confere_pelo_gerar(espiao, r)
    assert kw["modo"] == MODO_AUTO
    assert "assunto só para pessoa (troca_devolucao)" in r.motivo


async def test_legado_pelo_gerar_numero_inventado_bloqueia(
    db: AsyncSession, make_user, ia_ligada, modelo, espiao
):
    conversa = await _conversa(db, make_user, pedido=None)
    await _msg(db, conversa, "quanto custa a mala grande?")
    modelo.saida = _saida("A grande custa 299,90 e mede 75 cm.", categoria="duvida_produto")

    r = await ia.gerar_rascunho(db, conversa)

    _confere_pelo_gerar(espiao, r)
    assert r.status == RASCUNHO_BLOQUEADO and ia.MOTIVO_NUMERO_INVENTADO in r.motivo


async def test_legado_pelo_gerar_classificacao_fora_do_formato(
    db: AsyncSession, make_user, ia_ligada, monkeypatch, espiao
):
    await _pedido_completo(db)
    await _manual_em_camadas(db)
    monkeypatch.setattr(ia, "_chamar_modelo", ModeloSequencia(["não sei classificar", _saida()]))
    conversa = await _conversa(db, make_user)
    await _msg(db, conversa, "cadê meu pedido?")

    r = await ia.gerar_rascunho(db, conversa)

    kw, _ = _confere_pelo_gerar(espiao, r)
    assert kw["nao_classificou"] is True and kw["classificada"] is None
    # O motivo segue no mesmo lugar de antes: antes dos da conferência.
    assert r.motivo.startswith(ia.MOTIVO_NAO_CLASSIFICOU)
    assert [x.quando for x in kw["regras"]] != []  # o manual que o modelo viu


async def test_legado_pelo_gerar_assunto_que_muda(
    db: AsyncSession, make_user, ia_ligada, monkeypatch, espiao
):
    await _pedido_completo(db)
    await _manual_em_camadas(db)
    monkeypatch.setattr(
        ia,
        "_chamar_modelo",
        ModeloSequencia(
            [{"categoria": "rastreio", "confianca": 0.9}, _saida(categoria="nota_fiscal")]
        ),
    )
    conversa = await _conversa(db, make_user)
    await _msg(db, conversa, "cadê meu pedido?")

    r = await ia.gerar_rascunho(db, conversa)

    _confere_pelo_gerar(espiao, r)
    assert r.categoria == "rastreio" and "mudou de assunto" in r.motivo


async def test_legado_pelo_gerar_loja_respondeu_no_meio(
    db: AsyncSession, make_user, ia_ligada, monkeypatch, espiao
):
    import app.db as _db

    conversa = await _conversa(db, make_user, pedido=None)
    await _msg(db, conversa, "tem a azul?", ha=timedelta(minutes=10))
    saida = _saida("Temos sim!", categoria="duvida_produto")

    async def pessoa_responde_no_meio(sistema, usuario, **_):
        async with _db.SessionLocal() as tela:
            c = await tela.get(AtendimentoConversa, conversa.id)
            await gravar.gravar_mensagem(
                tela,
                c,
                externo_id="l-tela-conferir",
                autor="loja",
                texto="Temos, sim!",
                enviada_em=datetime.now(UTC),
                origem=ORIGEM_HUMANO,
            )
            await tela.commit()
        return json.dumps(saida), {"model": "falso"}

    monkeypatch.setattr(ia, "_chamar_modelo", pessoa_responde_no_meio)
    r = await ia.gerar_rascunho(db, conversa)

    _confere_pelo_gerar(espiao, r)
    assert r.status == RASCUNHO_SUBSTITUIDO and r.motivo.endswith(
        f"{ia.MOTIVO_LOJA_RESPONDEU}; dados do sistema"
    )


async def test_legado_pelo_gerar_com_regras_e_manual(
    db: AsyncSession, make_user, ia_ligada, modelo, espiao
):
    """Número que só existe no MANUAL que o modelo viu não é inventado."""
    db.add(AtendimentoRegra(quando="cliente pergunta da troca", faca="o prazo é de 7 dias"))
    await db.commit()
    conversa = await _conversa(db, make_user, pedido=None)
    await _msg(db, conversa, "posso trocar?")
    modelo.saida = _saida("Pode sim, em até 7 dias pelo app.", categoria="troca_devolucao")

    r = await ia.gerar_rascunho(db, conversa)

    _confere_pelo_gerar(espiao, r)
    assert ia.MOTIVO_NUMERO_INVENTADO not in (r.motivo or "")


# ─────────────── (b) agente=True: o que muda ───────────────


async def _as_duas(**mudar: Any) -> tuple[ia.SaidaConferida, ia.SaidaConferida]:
    """A mesma entrada no legado e no agente."""
    conversa, kw = _chamada(_base(**mudar))
    legado = await ia.conferir_saida(None, conversa, SimpleNamespace(), **kw)
    agente = await ia.conferir_saida(None, conversa, SimpleNamespace(), **kw, agente=True)
    return legado, agente


@pytest.mark.parametrize("categoria", ["cancelamento", "troca_devolucao", "garantia", "reembolso"])
async def test_agente_assunto_so_humano_nao_manda_para_pessoa(categoria):
    legado, agente = await _as_duas(
        categoria=categoria,
        **_cliente("como faço?"),
        texto="Pelo app, em Minhas compras, no pedido.",
    )
    assert f"assunto só para pessoa ({categoria})" in legado.motivos
    assert agente.motivos == [] and agente.precisa_humano is False
    assert agente.status == RASCUNHO_PENDENTE and agente.categoria == categoria


async def test_agente_so_humano_do_manual_nao_manda_para_pessoa():
    categorias = [{**c, "so_humano": c["id"] == "duvida_produto"} for c in CATEGORIAS]
    legado, agente = await _as_duas(
        categoria="duvida_produto",
        categorias=categorias,
        **_cliente("tem a mala azul?"),
        texto="Olá! As cores estão no anúncio.",
    )
    assert "assunto só para pessoa (duvida_produto)" in legado.motivos
    assert agente.motivos == []


async def test_agente_pista_de_assunto_so_humano_nao_manda_para_pessoa():
    legado, agente = await _as_duas(
        categoria="reembolso",
        **_cliente("quero meu reembolso"),
        texto="O reembolso é feito pela Shopee, pelo app.",
    )
    assert any("o cliente fala de assunto só para pessoa" in m for m in legado.motivos)
    assert not any("o cliente fala de assunto" in m for m in agente.motivos)
    assert agente.precisa_humano is False


async def test_agente_sem_mudou_de_assunto_nem_nao_classificou():
    legado, agente = await _as_duas(
        classificada="rastreio", categoria="nota_fiscal", **_cliente("e a nota fiscal?")
    )
    assert any("mudou de assunto" in m for m in legado.motivos)
    assert legado.categoria == "rastreio"
    assert not any("mudou de assunto" in m for m in agente.motivos)
    assert agente.categoria == "nota_fiscal"  # a chamada é uma só: vale o que o agente disse

    legado, agente = await _as_duas(nao_classificou=True)
    assert legado.motivos == [ia.MOTIVO_NAO_CLASSIFICOU]
    assert agente.motivos == []


@pytest.mark.parametrize(
    "opcao,declarada,valida",
    [
        ("cancelamento", "saudacao_ou_vaga", True),
        ("endereco", "agradecimento", True),
        ("garantia", "outro", False),
    ],
)
async def test_agente_opcao_sensivel_do_menu_vira_o_assunto(opcao, declarada, valida):
    legado, agente = await _as_duas(
        categoria=declarada,
        categoria_valida=valida,
        opcoes_sensiveis=[opcao],
        **_cliente("3"),
        texto="Olá! Me conta como posso ajudar com o pedido.",
    )
    assert legado.categoria == declarada  # no legado o menu já mandou para pessoa antes
    assert agente.categoria == opcao
    assert "categoria fora da lista" not in agente.motivos


async def test_agente_opcao_do_menu_nao_troca_assunto_especifico():
    _, agente = await _as_duas(
        categoria="rastreio", opcoes_sensiveis=["cancelamento"], **_cliente("3")
    )
    assert agente.categoria == "rastreio"


async def test_agente_inducao_de_assunto_so_no_automatico():
    entrada = {**_cliente("qual a nota fiscal?"), "texto": "Olá!", "categoria": "rastreio"}
    legado, copiloto = await _as_duas(**entrada)
    trava = [m for m in legado.motivos if "não bate com o texto do cliente" in m]
    assert trava and legado.precisa_humano is True
    # Copiloto do agente: vira aviso, não encaminhamento.
    assert copiloto.avisos_auto == trava and copiloto.motivos == []
    assert copiloto.precisa_humano is False
    # Automático do agente: continua mandando para pessoa.
    _, auto = await _as_duas(**entrada, modo=MODO_AUTO)
    assert auto.motivos == trava and auto.avisos_auto == [] and auto.precisa_humano is True


async def test_agente_fala_de_garantia_com_ver_garantia_e_datas_batendo():
    entrada = {
        "categoria": "garantia",
        **_cliente("meu celular ainda tem garantia?"),
        "garantias_conferidas": [GARANTIA_ATIVA],
        "garantia_consultada": True,
    }
    for texto in (
        "Sua garantia de hardware vai até 07/01/2027 e a de software até 07/10/2027.",
        "Sua garantia está ativa: pode descrever o problema por aqui.",
    ):
        legado, agente = await _as_duas(**entrada, texto=texto)
        assert ia.MOTIVO_RESPOSTA_DE_GARANTIA in legado.motivos, texto
        assert ia.MOTIVO_RESPOSTA_DE_GARANTIA not in agente.motivos, texto
        assert agente.precisa_humano is False and agente.status == RASCUNHO_PENDENTE, texto


async def test_agente_fala_de_garantia_continua_com_pessoa_sem_conferencia():
    base = {"categoria": "garantia", **_cliente("tem garantia?")}
    # Sem `ver_garantia` (nada devolvido): pessoa, como no legado.
    _, agente = await _as_duas(**base, texto="Seu aparelho tem garantia, pode mandar o vídeo.")
    assert ia.MOTIVO_RESPOSTA_DE_GARANTIA in agente.motivos
    # Com garantia, mas citando data que não é dela (mesmo do sistema): pessoa.
    _, agente = await _as_duas(
        **base,
        garantias_conferidas=[GARANTIA_ATIVA],
        garantia_consultada=True,
        texto="Enviado em 24/09/2026; a garantia de hardware vai até 07/01/2027.",
    )
    assert agente.erros == [] and ia.MOTIVO_RESPOSTA_DE_GARANTIA in agente.motivos
    # Data de garantia inventada: bloqueia nos dois.
    legado, agente = await _as_duas(
        **base,
        garantias_conferidas=[GARANTIA_ATIVA],
        garantia_consultada=True,
        texto="A garantia de hardware vai até 08/01/2027.",
    )
    assert legado.status == agente.status == RASCUNHO_BLOQUEADO
    assert agente.erros == legado.erros and ia.MOTIVO_RESPOSTA_DE_GARANTIA in agente.motivos


async def test_agente_ficha_no_automatico_continua():
    entrada = {
        "categoria": "espec_celular",
        "fichas": [{"titulo": "Celular F110"}],
        **_cliente("tem NFC?"),
        "texto": "Tem NFC sim.",
    }
    _, auto = await _as_duas(**entrada, modo=MODO_AUTO)
    assert auto.motivos == [ia.MOTIVO_FICHA_NO_AUTOMATICO] and auto.precisa_humano is True
    _, copiloto = await _as_duas(**entrada, modo=MODO_COPILOTO)
    assert ia.MOTIVO_FICHA_NO_AUTOMATICO not in copiloto.motivos


async def test_agente_que_pergunta_pode_pedir_o_numero_do_pedido():
    texto = "Olá! Pode me passar o número do pedido? Ele aparece em Minhas compras."
    for mudar in (
        {"ctx": {}},  # assunto de pedido sem pedido
        {"ctx": {}, "conversa": {"pedido_marketplace": PEDIDO_MKT}},  # número não achado
    ):
        conversa, kw = _chamada(_base(**mudar, texto=texto))
        legado = await ia.conferir_saida(None, conversa, None, **kw, perguntar=True)
        responde = await ia.conferir_saida(None, conversa, None, **kw, agente=True)
        pergunta = await ia.conferir_saida(None, conversa, None, **kw, agente=True, perguntar=True)
        assert legado.precisa_humano is True  # `perguntar` sozinho não muda o legado
        # Revisão de 20/10/2026: pedir o número ou orientar a conferir em Minhas
        # compras não vai para pessoa — como pergunta OU como resposta (spec 6.2).
        assert responde.motivos == [] and responde.precisa_humano is False
        assert pergunta.motivos == [] and pergunta.precisa_humano is False


async def test_agente_sem_pedido_que_afirma_o_estado_do_pedido_vai_para_pessoa():
    """Revisão de 20/10/2026: sem o pedido no sistema, o texto que AFIRMA o estado
    do pedido (enviado, entregue, cancelado, reembolso feito) vai para pessoa —
    na pergunta também; a orientação de produto com o número que não aparece, não."""
    for texto in (
        "Seu pedido já foi enviado e está a caminho! Pode me passar o número?",
        "Seu pedido foi cancelado e o reembolso já foi feito pela plataforma.",
        "Seu pedido já foi entregue!",
    ):
        for mudar in ({"ctx": {}}, {"ctx": {}, "conversa": {"pedido_marketplace": PEDIDO_MKT}}):
            conversa, kw = _chamada(_base(**mudar, texto=texto))
            for perguntar in (False, True):
                c = await ia.conferir_saida(
                    None, conversa, None, **kw, agente=True, perguntar=perguntar
                )
                assert c.precisa_humano is True, (texto, mudar, perguntar)
                assert any("pedido" in m for m in c.motivos), c.motivos
    # Orientação de rastreio e pergunta de produto sem o pedido: sai.
    orientacao = "Olá! Você acompanha o envio em Minhas Compras > o pedido > Informações do envio."
    for texto, categoria, cliente in (
        (orientacao, "rastreio", "cadê meu pedido?"),
        ("Sim, a mala tem quatro rodinhas.", "duvida_produto", "a mala tem rodinhas?"),
    ):
        conversa, kw = _chamada(
            _base(
                ctx={},
                conversa={"pedido_marketplace": PEDIDO_MKT},
                texto=texto,
                categoria=categoria,
                **_cliente(cliente),
            )
        )
        c = await ia.conferir_saida(None, conversa, None, **kw, agente=True)
        assert "pedido não encontrado no sistema" not in c.motivos, (texto, c.motivos)
        assert "conversa sem pedido identificado" not in c.motivos, (texto, c.motivos)


async def test_agente_numeros_das_ferramentas_sao_permitidos():
    entrada = {"texto": "O modelo tem 8 GB de RAM e 256 GB.", "categoria": "espec_celular"}
    _, sem = await _as_duas(**entrada)
    assert sem.status == RASCUNHO_BLOQUEADO
    _, com = await _as_duas(**entrada, permitidos_extra=['{"ram": "8 GB", "rom": "256 GB"}'])
    assert com.status == RASCUNHO_PENDENTE
    assert not any(ia.MOTIVO_NUMERO_INVENTADO in m for m in com.motivos)


# ─────────────── (c) as travas duras: iguais com agente=True ───────────────

TRAVAS_DURAS = [
    ("whatsapp", "Me chama no WhatsApp que resolvo por lá.", False, "WhatsApp"),
    ("telefone", "Liga no 11 98888-7777 que resolvo.", True, "telefone"),
    ("email", "Manda um e-mail para loja@exemplo.com.br.", False, "e-mail"),
    ("pix", "Faço o estorno por PIX se preferir.", False, "PIX"),
    ("valor", "O frete fica R$ 25,00.", True, "valor em R$"),
    ("percentual", "Tem 10% de desconto no app.", True, "percentual"),
    ("prazo", "Chega em 3 dias úteis.", True, "prazo em números"),
    ("promessa", "Vamos reembolsar o valor integral.", True, "promessa"),
    ("estornado", "O valor será estornado ainda hoje.", True, "promessa"),
    ("dado_pessoal", "Me informe seu CPF para eu localizar.", True, "dado pessoal"),
]


@pytest.mark.parametrize(
    "nome,texto,bloqueia,trecho", TRAVAS_DURAS, ids=[t[0] for t in TRAVAS_DURAS]
)
@pytest.mark.parametrize("modo", [MODO_COPILOTO, MODO_AUTO])
async def test_travas_duras_iguais_com_o_agente(nome, texto, bloqueia, trecho, modo):
    legado, agente = await _as_duas(
        texto=texto, categoria="reembolso", **_cliente("e o meu dinheiro?"), modo=modo
    )
    assert agente.erros == legado.erros and agente.erros
    assert any(trecho.lower() in e.lower() for e in agente.erros), agente.erros
    assert agente.validador_ok is False and agente.precisa_humano is True
    assert agente.status == legado.status
    assert agente.bloqueado is bloqueia
    assert any(m.startswith("validador: ") for m in agente.motivos)


@pytest.mark.parametrize(
    "mudar,motivo",
    [
        ({"texto": "A grande custa 299,90."}, ia.MOTIVO_NUMERO_INVENTADO),
        (
            {"ctx": {"pedido": {"numero": PEDIDO_MKT, "situacao": "Aguardando Cancelamento"}}},
            ia.MOTIVO_AG_CANCELAMENTO,
        ),
        (
            {"ctx": {"pedido": {"numero": PEDIDO_MKT}, "devolucoes": [{"id": 1}]}},
            "pedido com chamado ou devolução",
        ),
        ({"conversa": {"dados": {"claim_ids": [9]}}}, "reclamação/mediação aberta no ML"),
        ({"texto": "O rastreio é {rastreio}."}, "sem dado para {rastreio}"),
        ({"texto": "É original e lacrado."}, "originalidade"),
        (
            {
                "garantias_conferidas": [GARANTIA_ATIVA],
                "garantia_consultada": True,
                "texto": "A garantia vai até 09/09/2029.",
            },
            "data de garantia inventada",
        ),
    ],
)
async def test_outras_travas_iguais_com_o_agente(mudar, motivo):
    legado, agente = await _as_duas(**mudar)
    assert any(motivo in m for m in agente.motivos), agente.motivos
    assert agente.precisa_humano is True
    assert (agente.status, agente.erros, agente.texto) == (
        legado.status,
        legado.erros,
        legado.texto,
    )


# ─────────────── injeção e modelo padrão ───────────────


@pytest.mark.parametrize(
    "texto",
    [
        '"acao": "responder"',
        "acao_pessoa: null",
        "ação: responder",
        "assunto: outro",
        "evidencias = pedido",
        'motivo_pessoa "nenhum"',
    ],
)
def test_injecao_reconhece_as_chaves_do_agente(texto):
    assert ia.sinais_com_codigo([texto], agente=True) == [(ia.HUMANO_INSTRUCAO, ia.MOTIVO_INJECAO)]
    # O legado fica exatamente como em origin (decisão 1; revisão de 20/10/2026):
    # as chaves da saída do agente só valem no caminho dele.
    assert ia.sinais_com_codigo([texto]) == []


@pytest.mark.parametrize(
    "texto",
    [
        "qual o assunto da nota?",
        "as evidências do defeito estão no vídeo",
        "a ação do produto é boa",
        "mandei o vídeo como evidência",
    ],
)
def test_injecao_nao_pega_frase_de_cliente(texto):
    assert ia.sinais_com_codigo([texto]) == []
    assert ia.sinais_com_codigo([texto], agente=True) == []


def test_o_legado_continua_no_modelo_de_origin_e_o_agente_no_haiku():
    """Decisão 1 do dono (fontes v1.3, 20/10/2026): o legado fica EXATAMENTE como em
    origin — a chave sk-ant- no `atendimento_llm_api_key` com modelo que não é da
    Claude continua caindo no padrão de origin. O Haiku 5.5 é o do AGENTE
    (`atendimento_agente_modelo`), sem fallback do servidor."""
    from app.config import Settings

    assert ia.MODELO_CLAUDE_PADRAO == "claude-opus-5"
    p = ia.escolher_provedor(
        base_url="",
        modelo="",
        chave="sk-ant-teste",
        base_url_dm="https://llm.invalid/v1",
        modelo_dm="openai/gpt-oss-120b",
        chave_dm="",
    )
    assert (p.tipo, p.modelo) == (ia.PROVEDOR_CLAUDE, "claude-opus-5")
    # Item 5 da rodada "redondo" (23/10/2026): o agente passou ao Sonnet 5.5 (o verificador
    # continua no Haiku); o legado não muda nada.
    assert Settings.model_fields["atendimento_agente_modelo"].default == "claude-sonnet-5-5"
    assert not ia._com_fallback("claude-haiku-5-5")
    pedido = ia.pedido_claude("claude-haiku-5-5", "S", "U", max_tokens=900, fallback=False)
    assert not {"temperature", "top_p", "top_k", "thinking", "fallbacks"} & set(pedido)
