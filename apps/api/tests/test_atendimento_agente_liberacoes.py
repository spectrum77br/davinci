# ruff: noqa: F811, E501 — fixtures importadas do e2e; as tabelas de casos reais
"""Liberações do dono (23/10/2026 ~10:40): "quero que ele responda quase tudo, como um
atendente humano".

L1. Perguntas de PRODUTO (o_que_e_o_anuncio, espec_*, conteudo_da_caixa, duvida_produto)
    deixam de ser "sempre pessoa": vão ao verificador (e podem sair sozinhas) quando a
    resposta se apoia nos dados do anúncio DA CONVERSA (título, atributos, descrição,
    variações, especificações de fonte "anuncio") ou no par da equipe que vale para produto,
    citando a ficha dele. As checagens de sempre continuam; o dado de OUTRO anúncio, do site
    da marca ou do cadastro não libera; a resistência à água continua com pessoa.
L2. originalidade_condicao sai sozinha quando o anúncio da conversa diz a condição (título,
    atributo de condição ou descrição — `condicao_anuncio`); sem o dado, com conflito ou
    com "de fábrica"/"nunca aberto" além do anúncio, pessoa.
L3. cobertura_regiao: com a prova de "nenhuma restrição" (o anúncio, a regra Apple→RJ e a
    loja conferidos), "Enviamos para todo o Brasil; o frete e o prazo aparecem ao informar o
    seu CEP no anúncio."; com restrição, nunca "todo o Brasil"; sem a prova, pessoa.

Os casos têm a forma dos reais (Kia e Barbosa, 22–23/10/2026: o A17 sem NFC, o Apple Watch
"original lacrado?", Maceió, o C2 "possui NFC?"), sem dado de cliente; os títulos e as fichas
são os do catálogo de produção. Nenhuma chamada a LLM, à loja ou à produção.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from app.services.atendimento import constantes, ia, reavaliacao, restricao_envio
from app.services.atendimento.agente import acao_pessoa, checagens, prompt, saida
from app.services.atendimento.agente import contexto as agente_ctx
from app.services.atendimento.agente import verificador as v
from app.services.atendimento.agente.saida import Proposta
from scripts.agente_eval import calibrar_verificador as calibrar
from tests.test_atendimento_agente_e2e import (  # noqa: F401 — fixtures
    _decisao,
    _do_agente,
    _final,
    _kia,
    _ligar,
    _sem_limites,
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
from tests.test_atendimento_agente_redondo import (
    AGORA,
    SHOPEE_REAL,
    VAI_AO_VERIFICADOR,
    _cli,
    _m,
    _menu,
)
from tests.test_atendimento_ia import _msg
from tests.test_atendimento_variacoes import ITEM_A18

VAI = VAI_AO_VERIFICADOR
DE_ONDE = "anúncio ligado à conversa"

# ── Os anúncios (catálogo de produção, 23/10/2026) ──
ITEM_A17 = "22394778711"
A17 = {
    "de_onde": DE_ONDE,
    "item_id": ITEM_A17,
    "titulo": "Uranyx Hotwav A17 Pro Max 12.128 ou 12.64 GB Memoria",
    "atributos": [
        "Marca: Uranyx",
        "Phone Model: A17 Pro Max",
        "Storage Capacity: 128 GB",
        "Screen Size: 6.75 inches",
        "Battery Capacity: 5160 mAh",
        "RAM: 12 GB",
        "NFC: No",
        "Number of Cores: Octa-Core",
    ],
    "descricao": (
        "Hotwav A17 Pro Max + Fone UFB10\nMemoria Interna 128 GB OU 64 GB\n"
        "Processador 8 Core 1.7 GHz\nTela 6.75 120Hz\nBateria 5.160 mAh\nAndroid 15\n"
        "Camera Traseira: 13MP\nCamera Frontal: 5MP\nNFC - Não Tem\nRede Movel 4G\n"
        "Itens inclusos: aparelho, cabo USB-C e fone UFB10"
    ),
    "condicao": {"novo": {"valor": True, "fontes": ["atributo"]}, "conflito_no_anuncio": False},
}
ITEM_WATCH = "22299473628"
WATCH = {
    "de_onde": DE_ONDE,
    "item_id": ITEM_WATCH,
    "titulo": "Apple Watch Series 11 - 42mm ou 46mm - 1 Ano de Garantia Apple",
    "atributos": ["Marca: Apple", "Smart Wear Model: Serie 11", "Item Type: Smartwatch"],
    "descricao": "Produto novo e lacrado.\nO Apple Watch Series 11 traz notificações de frequência cardíaca.",
    "condicao": {
        "novo": {"valor": True, "fontes": ["atributo", "descricao"]},
        "lacrado": {"valor": True, "fontes": ["descricao"]},
        "conflito_no_anuncio": False,
    },
}
ITEM_C2 = "58203678406"
C2 = {
    "de_onde": DE_ONDE,
    "item_id": ITEM_C2,
    "titulo": "Uranyx Oukitel C2 128 Gb Memoria 16 Gb",
    "atributos": ["Marca: Uranyx", "Storage Capacity: 64 GB", "Battery Capacity: 5000 mAh"],
    "descricao": "Oukitel C2\nMemoria Interna 64 ou 128 GB\nTela 6.52 polegadas IPS LCD\nDual SIM\nBateria 5.000 mAh",
    "condicao": {"novo": {"valor": True, "fontes": ["atributo"]}, "conflito_no_anuncio": False},
}
ITEM_MALA = "57001234567"
MALA = {
    "de_onde": DE_ONDE,
    "item_id": ITEM_MALA,
    "titulo": "Mala de Bordo ABS 10kg com Rodinhas 360 Charlots",
    "atributos": ["Material: ABS"],
    "descricao": "Mala de bordo com rodinhas 360 e cadeado.",
}
CONFERIDO = [
    restricao_envio.FONTE_ANUNCIO,
    restricao_envio.FONTE_REGRA_APPLE,
    restricao_envio.FONTE_LOJA,
]
NENHUMA = {"ufs": [], "ufs_conforme_pedido": [], "nenhuma": True, "conferido": CONFERIDO}
RS_LOJA = {
    "ufs": ["RS"],
    "fonte": "loja",
    "fontes": ["loja"],
    "trecho": None,
    "ufs_conforme_pedido": ["RJ"],
    "conferido": CONFERIDO,
}
RJ_APPLE = {
    "ufs": ["RJ"],
    "fonte": "regra_apple_shopee",
    "fontes": ["regra_apple_shopee"],
    "trecho": None,
    "ufs_conforme_pedido": [],
    "conferido": CONFERIDO,
}
TODO_O_BRASIL = (
    "Enviamos para todo o Brasil; o frete e o prazo aparecem ao informar o seu CEP no anúncio."
)


def _p(texto: str, assunto: str, evidencias: list[str], *, confianca: float = 0.9) -> Proposta:
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
    fichas: list[dict] | None = None,
    fichas_lidas: list[dict] | None = None,
    registro: list[dict] | None = None,
    fatos_lidos: dict[str, str] | None = None,
    anuncio_titulo: str | None = None,
    canal: Any = SHOPEE_REAL,
    fatos_base: dict | None = None,
) -> SimpleNamespace:
    """O caminho AUTO do `agente.responder` sem banco e sem modelo, COM a marca prévia que o
    agente passa à `ia.conferir_saida` (`checagens.liberacao_da_proposta`). `registro` = o
    JSON que o `ver_anuncio` devolveu (o de cada `fichas_lidas`)."""
    fichas = list(fichas or [])
    lidas = [dict(f) for f in fichas_lidas or []]
    conversa = SimpleNamespace(
        id=uuid4(), plataforma="shopee", canal="chat", dados={}, pedido_marketplace=None,
        etiquetas=[], canal_id=None, integration_id=None, comprador_id=None,
    )  # fmt: skip
    gatilho = next(x for x in reversed(msgs) if x.autor == "cliente")
    rajada = ia.rajada_do_cliente(msgs, gatilho)
    foco = ia.perguntas_pendentes(msgs, gatilho)
    textos_cliente = [x.texto or "" for x in (foco or rajada)]
    textos_rajada = [x.texto or "" for x in rajada]
    ctxd = {"pedido": None, "logistica": None, "chamados": [], "devolucoes": []}
    base = agente_ctx.BaseDoGerar(
        recentes=msgs, rajada=rajada, foco=foco, textos_cliente=textos_cliente,
        textos_rajada=textos_rajada, ctx=ctxd, valores={},
        fatos={"anuncio": anuncio_titulo, **(fatos_base or {})},
        fatos_gravados={}, categorias=_categorias_do_manual(), regras=[], sinais=[], motivos=[],
        opcoes_sensiveis=[], sinais_do_cliente=[], nomes=(), modo=constantes.MODO_AUTO,
        forcar=False, nf_politica_aprovada=False, insistiu=False,
    )  # fmt: skip
    lista = acao_pessoa.gatilhos(
        conversa, msgs, ctxd, gatilho=gatilho, postado=None, chaves_ativas=frozenset(), agora=AGORA
    )
    ctx = agente_ctx.ContextoAgente.da_conversa(conversa, gatilho, modo=constantes.MODO_AUTO)
    ctx.acoes_codigo = agente_ctx.ordenar_acoes([g.codigo for g in lista])
    ctx.pistas_orientacao = acao_pessoa.pistas_de_orientacao(msgs, gatilho)
    ctx.pistas_turno = acao_pessoa.pistas_do_turno(msgs, gatilho, com_pedido=False)
    provados = []
    for f in fichas:
        provados.append(agente_ctx.id_ficha(f["item_id"]))
        if f.get("variacoes"):
            provados.append(agente_ctx.id_variacoes(f["item_id"]))
        if f.get("restricao_envio"):
            provados.append(agente_ctx.id_restricao(f["item_id"]))
        if f.get("especificacoes"):
            provados.append(agente_ctx.id_especificacoes(f["item_id"]))
    ctx.permitir(ids=provados)
    ctx.permitir(textos=[json.dumps(fichas, ensure_ascii=False) if fichas else None], produto=True)
    for i, texto in (fatos_lidos or {}).items():
        ctx.permitir(ids=[i], textos=[texto])
        ctx.registrar_fatos([{"id": i, "texto": texto}])
    for f in lidas:
        ctx.fichas_lidas.append(f)
        ctx.permitir(ids=[agente_ctx.id_ficha(f["item_id"])])
    for dado in registro or []:
        ids = [agente_ctx.id_ficha(dado["item_id"])]
        if dado.get("especificacoes"):
            ids.append(agente_ctx.id_especificacoes(dado["item_id"]))
        ctx.permitir(ids=ids, textos=[json.dumps(dado, ensure_ascii=False)], produto=True)
    ctx.pistas_turno = list(
        dict.fromkeys([*ctx.pistas_turno, *acao_pessoa.pistas_do_rascunho(p.texto)])
    )
    ctx.acao = p.acao
    ctx.acoes_modelo = saida.acoes_do_modelo(p)
    ctx.evidencias = saida.evidencias(p)
    todas = [*fichas, *lidas]
    entrada = saida.para_conferir(p, base.ids_categorias)
    entrada["texto"] = prompt.caminho_na_frase(entrada["texto"])
    conferida = await ia.conferir_saida(
        None, conversa, gatilho, **entrada, motivos=[], ctx=ctxd,
        fatos={"anuncio": None, "ficha_do_anuncio": fichas}, valores={},
        categorias=base.categorias, regras=[], textos_cliente=textos_cliente,
        textos_rajada=textos_rajada, foco=foco, fichas=todas,
        garantias_conferidas=ctx.garantias_conferidas,
        garantia_consultada=ctx.garantia_consultada, permitidos_extra=ctx.permitidos_extra(),
        modo=constantes.MODO_AUTO, forcar=True, agente=True,
        fatos_citados=ctx.fatos_citados(ctx.evidencias),
        produto_pelo_anuncio=checagens.liberacao_da_proposta(
            p, entrada["texto"], entrada["categoria"], ctx, base, fichas=todas
        ),
    )  # fmt: skip
    conferida = checagens.sem_lacuna_vazia(conferida)
    chk = checagens.conferir(p, conferida, ctx, base, fichas=todas, gatilho=gatilho)
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
    trava = ia.trava_do_agente(rascunho, canal)
    return SimpleNamespace(
        decisao=VAI if nao is None and trava is None else (nao or trava),
        codigos=[x.codigo for x in chk.pendencias],
        motivos=list(final.motivos),
        ctx=ctx,
        rascunho=rascunho,
    )


def _turno(*falas: str) -> list[SimpleNamespace]:
    return [_menu(), *_cli(*falas)]


def _ev(item: str) -> list[str]:
    return [f"ficha:{item}"]


# ═══════════════ L1. produto pelo anúncio da conversa ═══════════════


@pytest.mark.parametrize(
    "cliente,texto,assunto,ficha",
    [
        # Kia 22/10 (o A17 "NFC: No" / "NFC - Não Tem"): a negativa do anúncio.
        ("Esse A17 tem NFC?", "Não, o A17 Pro Max não tem NFC.", "espec_celular", A17),
        (
            "O que vem na caixa?",
            "Acompanha o aparelho, o cabo USB-C e o fone UFB10.",
            "conteudo_da_caixa",
            A17,
        ),
        ("Qual a bateria?", "A bateria é de 5160 mAh.", "espec_celular", A17),
        (
            "Qual o processador?",
            "O processador é 8 Core de 1.7 GHz, com Android 15.",
            "espec_celular",
            A17,
        ),
        # Barbosa 23/10 03:29 ("esse C2 possui NFC?"): o anúncio não diz — a resposta diz que
        # ele não informa, sem afirmar.
        (
            "Olá esse C2 possui NFC?",
            "O anúncio do C2 não informa se ele tem NFC; as especificações dele estão na descrição.",
            "espec_celular",
            C2,
        ),
        (
            "Esse anúncio é do relógio ou só da pulseira?",
            "É o Apple Watch Series 11, nas versões de 42mm ou 46mm.",
            "o_que_e_o_anuncio",
            WATCH,
        ),
    ],
)
async def test_l1_produto_pelo_anuncio_vai_ao_verificador(cliente, texto, assunto, ficha):
    r = await _auto(_p(texto, assunto, _ev(ficha["item_id"])), _turno(cliente), fichas=[ficha])
    assert r.decisao == VAI, (r.decisao, r.codigos, r.motivos)
    assert r.rascunho.fatos["agente"]["produto_pelo_anuncio"] is True


async def test_l1_antes_era_sempre_pessoa_sem_a_marca():
    """O mesmo rascunho sem citar a ficha (nada prova que se apoia no anúncio) segue pessoa."""
    r = await _auto(
        _p("A bateria é de 5160 mAh.", "espec_celular", []), _turno("Qual a bateria?"), fichas=[A17]
    )
    assert r.decisao != VAI
    assert checagens.FICHA_NO_AUTOMATICO in r.codigos
    assert "produto_pelo_anuncio" not in r.rascunho.fatos["agente"]


@pytest.mark.parametrize(
    "cliente,texto,ficha",
    [
        # A característica que a ficha NÃO traz.
        ("Qual o processador?", "O processador é o Helio G99, muito rápido.", A17),
        # O contrário da ficha ("NFC: No").
        ("Esse A17 tem NFC?", "Sim, o A17 tem NFC.", A17),
        # A resposta curta sem o termo no dado ("NFC: No" → "Sim!").
        ("Esse A17 tem NFC?", "Sim!", A17),
        ("Esse A17 tem NFC?", "Tem sim.", A17),
        # A curta "não" para o que o anúncio não diz (o C2 não fala de NFC).
        ("Esse C2 possui NFC?", "Não.", C2),
        # Só pela forma, sem nada do anúncio.
        ("Roda jogos pesados?", "Roda liso, aguenta tranquilo.", A17),
        # O que a ficha não diz, dito como se dissesse.
        ("Tem leitor de digital?", "Tem leitor de digital na lateral.", A17),
        # A paráfrase fora do vocabulário (a régua antiga segurava qualquer afirmação).
        ("Pode molhar?", "Dá para usar debaixo do chuveiro tranquilamente.", A17),
        ("A câmera é boa?", "É ótimo para fotos noturnas, bem nítidas.", A17),
    ],
)
async def test_l1_negativos_seguem_pessoa(cliente, texto, ficha):
    r = await _auto(
        _p(texto, "espec_celular", _ev(ficha["item_id"])), _turno(cliente), fichas=[ficha]
    )
    assert r.decisao != VAI, (texto, r.codigos)
    assert r.rascunho.fatos["agente"].get("produto_pelo_anuncio") is not True


async def test_l1_dado_so_do_site_da_marca_nao_libera():
    """A RAM só nas especificações de fonte "site" (o anúncio não diz): pessoa."""
    ficha = {
        **C2,
        "especificacoes": {
            "campos": [{"campo": "ram", "valor": "4 GB", "fonte": "site"}],
            "conflitos": [],
            "conflito_no_anuncio": False,
        },
    }
    r = await _auto(
        _p(
            "Ele tem 4 GB de RAM física.", "espec_celular", [f"ficha:{ITEM_C2}", f"espec:{ITEM_C2}"]
        ),
        _turno("Quanto de RAM física ele tem?"),
        fichas=[ficha],
    )
    assert r.decisao != VAI
    assert r.rascunho.fatos["agente"].get("produto_pelo_anuncio") is not True


async def test_l1_dado_de_outro_anuncio_nao_libera():
    """O `ver_anuncio` de OUTRO anúncio da loja (o A18 com NFC) citado para o produto da
    conversa (o A17): nunca libera, mesmo com a ficha da conversa citada junto."""
    outro = {
        "item_id": "58266122162",
        "titulo": "Uranyx A18 Pro Max 16.128 Gb NFC",
        "atributos": ["NFC: Yes"],
        "descricao": "NFC: sim",
    }
    r = await _auto(
        _p("Tem sim, com NFC.", "espec_celular", [f"ficha:{ITEM_A17}", "ficha:58266122162"]),
        _turno("Esse tem NFC?"),
        fichas=[A17],
        fichas_lidas=[{"item_id": "58266122162", "titulo": outro["titulo"], "da_conversa": False}],
        registro=[outro],
    )
    assert r.decisao != VAI
    assert r.rascunho.fatos["agente"].get("produto_pelo_anuncio") is not True


async def test_l1_ficha_emprestada_de_outro_anuncio_nao_libera():
    """A ficha do mesmo produto em OUTRO anúncio (`ficha.FONTE_OUTRO_ANUNCIO`): a descrição
    dela não é do anúncio da conversa."""
    emprestada = {
        "de_onde": DE_ONDE,
        "item_id": "999",
        "titulo": "Uranyx A17 Pro Max",
        "fonte": "outro anúncio do mesmo produto (Mercado Livre)",
        "descricao": "Bateria 5.160 mAh",
    }
    r = await _auto(
        _p("A bateria é de 5160 mAh.", "espec_celular", ["ficha:999"]),
        _turno("Qual a bateria?"),
        fichas=[emprestada],
    )
    assert r.decisao != VAI


async def test_l1_resistencia_agua_continua_pessoa_mesmo_com_o_titulo():
    ficha = {
        "de_onde": DE_ONDE,
        "item_id": "19398015412",
        "titulo": "Uranyx Fossibot F109 5G 24.256 - Resistente a Água e Queda - NFC",
    }
    r = await _auto(
        _p(
            "Sim, ele é resistente à água, como diz o anúncio.",
            "resistencia_agua",
            ["ficha:19398015412"],
        ),
        _turno("Pode molhar?"),
        fichas=[ficha],
    )
    assert r.decisao != VAI


async def test_l1_assunto_fora_do_auto_categorias_do_canal_segue_pessoa():
    canal = SimpleNamespace(
        auto_categorias=[a for a in SHOPEE_REAL.auto_categorias if a != "espec_celular"],
        auto_agente=SHOPEE_REAL.auto_agente,
        auto_orientacao=SHOPEE_REAL.auto_orientacao,
    )
    r = await _auto(
        _p("A bateria é de 5160 mAh.", "espec_celular", _ev(ITEM_A17)),
        _turno("Qual a bateria?"),
        fichas=[A17],
        canal=canal,
    )
    assert r.decisao == "assunto_nao_liberado"


async def test_l1_par_da_equipe_que_vale_para_produto_libera():
    """O par do MESMO anúncio, de um modelo (o item 32), sustenta o que acompanha."""
    ficha = {**A17, "descricao": "Memoria Interna 128 GB"}
    p = _p("Sim, vem com película.", "conteudo_da_caixa", ["par:77"])
    msgs = _turno("Vem com película?")
    r0 = await _auto(p, msgs, fichas=[ficha])
    assert r0.decisao != VAI  # sem o par registrado: nada sustenta
    # Com o par do pacote (o `ctx.registrar_pares` do pacote real).
    from tests.test_atendimento_agente_equipe import _exemplo

    par = {
        **_exemplo(77, mesmo_anuncio=True, mesmo_produto=True, fonte_de_produto=True),
        "resposta": "sim, vem com película!",
    }
    ctx = agente_ctx.ContextoAgente.da_conversa(
        SimpleNamespace(
            id=uuid4(),
            plataforma="shopee",
            canal="chat",
            canal_id=None,
            integration_id=None,
            comprador_id=None,
        ),
        msgs[-1],
        modo=constantes.MODO_AUTO,
    )
    ctx.registrar_pares([par])
    pares = [str(x.get("resposta")) for x in ctx.pares_citados(["par:77"], de_produto=True)]
    assert checagens.liberacao_pelo_anuncio(
        "Sim, vem com película.",
        "conteudo_da_caixa",
        evidencias=["par:77"],
        fichas=[ficha],
        ctx=ctx,
        textos_cliente=["Vem com película?"],
        pares_do_produto=pares,
    ), pares


def test_l1_trava_le_a_marca_do_codigo_e_o_auto_categorias():
    def _r(categoria: str, **agente: Any) -> SimpleNamespace:
        return SimpleNamespace(
            categoria=categoria,
            prompt_versao=constantes.PROMPT_VERSAO_AGENTE,
            fatos={"agente": {"modo": "auto", "acoes_pessoa": [], **agente}},
        )

    for categoria in (
        "o_que_e_o_anuncio",
        "espec_celular",
        "conteudo_da_caixa",
        "originalidade_condicao",
    ):
        assert ia.trava_do_agente(_r(categoria, produto_pelo_anuncio=True), SHOPEE_REAL) is None, (
            categoria
        )
        # Sem a marca, como antes (o produto nunca está no `auto_agente`).
        assert ia.trava_do_agente(_r(categoria), SHOPEE_REAL) in (
            "assunto_de_produto",
            "assunto_nao_liberado_agente",
        ), categoria
    # A água nunca; duvida_produto não está no auto_categorias da Shopee.
    assert ia.trava_do_agente(_r("resistencia_agua", produto_pelo_anuncio=True), SHOPEE_REAL)
    assert ia.trava_do_agente(_r("duvida_produto", produto_pelo_anuncio=True), SHOPEE_REAL)
    # A pista de produto além do assunto: só com a marca.
    com_pista = {"pistas_turno": ["espec_celular"]}
    assert (
        ia.trava_do_agente(
            _r("conteudo_da_caixa", produto_pelo_anuncio=True, **com_pista), SHOPEE_REAL
        )
        is None
    )
    assert (
        ia.trava_do_agente(_r("cobertura_regiao", **com_pista), SHOPEE_REAL) == "assunto_de_produto"
    )
    assert (
        ia.trava_do_agente(
            _r("cobertura_regiao", produto_pelo_anuncio=True, pistas_turno=["resistencia_agua"]),
            SHOPEE_REAL,
        )
        == "assunto_de_produto"
    )
    # O sem_auto (qualquer pendência) continua vencendo.
    assert (
        ia.trava_do_agente(
            _r("espec_celular", produto_pelo_anuncio=True, sem_auto=["x"]), SHOPEE_REAL
        )
        == "sem_auto"
    )


def test_l1_reavaliacao_segue_a_marca():
    def _r(**agente: Any) -> SimpleNamespace:
        return SimpleNamespace(
            categoria="espec_celular",
            prompt_versao=constantes.PROMPT_VERSAO_AGENTE,
            fatos={
                "agente": {
                    "modo": "auto",
                    "acoes_pessoa": [],
                    "acoes_codigo": [],
                    "acoes_modelo": [],
                    **agente,
                }
            },
        )

    assert reavaliacao._agente_pode(_r(produto_pelo_anuncio=True), SHOPEE_REAL)
    assert not reavaliacao._agente_pode(_r(), SHOPEE_REAL)


def test_l1_assuntos_liberados_do_verificador_com_a_marca():
    sem = v.assuntos_liberados(SHOPEE_REAL)
    com = v.assuntos_liberados(SHOPEE_REAL, produto=True)
    assert "espec_celular" not in sem and "espec_celular" in com
    assert "resistencia_agua" not in com and "duvida_produto" not in com


def test_l1_marca_so_vai_ao_fatos_quando_ha():
    ctx = agente_ctx.ContextoAgente.da_conversa(
        SimpleNamespace(
            id=uuid4(),
            plataforma="shopee",
            canal="chat",
            canal_id=None,
            integration_id=None,
            comprador_id=None,
        ),
        None,
        modo=constantes.MODO_AUTO,
    )
    assert "produto_pelo_anuncio" not in ctx.para_fatos()
    ctx.produto_pelo_anuncio = True
    assert ctx.para_fatos()["produto_pelo_anuncio"] is True


def test_l1_atributo_em_ingles_vira_portugues():
    assert checagens._atributo_em_portugues("NFC: No") == "NFC: nao"
    assert checagens._atributo_em_portugues("GPS: Yes") == "GPS: sim"
    assert checagens._atributo_em_portugues("SIRIM Approved: Not applicable") is None
    assert (
        checagens._atributo_em_portugues("Screen Size: 6.75 inches")
        == "Screen Size: 6.75 polegadas"
    )


# ═══════════════ L2. condição pelo anúncio ═══════════════


@pytest.mark.parametrize(
    "cliente,texto,ficha",
    [
        ("É novo?", "Sim, ele é novo.", A17),
        # Kia 22/10 17:10 ("é original lacrado"): o anúncio diz novo e lacrado.
        (
            "Esse Apple Watch é novo e lacrado?",
            "Sim, conforme o anúncio, o Apple Watch é novo e lacrado.",
            WATCH,
        ),
        ("É recondicionado?", "Não, ele é novo.", A17),
    ],
)
async def test_l2_condicao_pelo_anuncio_vai_ao_verificador(cliente, texto, ficha):
    r = await _auto(
        _p(texto, "originalidade_condicao", _ev(ficha["item_id"])), _turno(cliente), fichas=[ficha]
    )
    assert r.decisao == VAI, (r.decisao, r.codigos, r.motivos)


@pytest.mark.parametrize(
    "texto,ficha",
    [
        # O anúncio só diz "novo": "lacrado" e "original" não têm o dado.
        ("Sim, é novo e lacrado.", A17),
        ("Sim, é original.", A17),
        # Além do anúncio.
        ("Sim, é novo, nunca aberto.", A17),
        ("Sim, é lacrado de fábrica.", WATCH),
        ("Sim, é 100% original.", WATCH),
        # O anúncio diz o contrário (recondicionado) ou se contradiz.
        (
            "Sim, é novo.",
            {
                **A17,
                "condicao": {
                    "novo": {"valor": False, "fontes": ["titulo"]},
                    "conflito_no_anuncio": False,
                },
            },
        ),
        (
            "Sim, é novo.",
            {
                **A17,
                "condicao": {
                    "novo": {"valor": "conflito", "fontes": ["atributo", "descricao"]},
                    "conflito_no_anuncio": True,
                },
            },
        ),
        # Sem condição nenhuma no anúncio.
        ("Sim, é novo.", {k: x for k, x in A17.items() if k != "condicao"}),
        # Sem afirmar a condição (nada a provar pelo anúncio).
        ("A compra é protegida pela plataforma.", A17),
    ],
)
async def test_l2_negativos_seguem_pessoa(texto, ficha):
    r = await _auto(
        _p(
            texto,
            "originalidade_condicao",
            _ev(ITEM_A17 if ficha.get("item_id") == ITEM_A17 else ficha["item_id"]),
        ),
        _turno("É novo? lacrado? original?"),
        fichas=[ficha],
    )
    assert r.decisao != VAI, (texto, r.codigos)


async def test_l2_condicao_de_outro_anuncio_nao_libera():
    """A condição do `ver_anuncio` de outro anúncio (da_conversa=False) não prova a do da conversa."""
    sem_condicao = {k: x for k, x in A17.items() if k != "condicao"}
    lida = {
        "item_id": ITEM_WATCH,
        "titulo": WATCH["titulo"],
        "condicao": WATCH["condicao"],
        "da_conversa": False,
    }
    r = await _auto(
        _p("Sim, é novo e lacrado.", "originalidade_condicao", [f"ficha:{ITEM_A17}"]),
        _turno("É novo e lacrado?"),
        fichas=[sem_condicao],
        fichas_lidas=[lida],
    )
    assert r.decisao != VAI


async def test_l2_condicao_contra_a_fala_da_loja():
    msgs = [
        *_turno("É novo?"),
        _m("loja", "Esse é seminovo, revisado.", origem="externo"),
        *_cli("Mas é novo mesmo?"),
    ]
    r = await _auto(_p("Sim, é novo.", "originalidade_condicao", _ev(ITEM_A17)), msgs, fichas=[A17])
    assert r.decisao != VAI


# ═══════════════ L3. região: "todo o Brasil" só com a prova ═══════════════


@pytest.mark.parametrize(
    "cliente",
    [
        "Vocês entregam em Manaus?",
        "Entrega pro Amazonas?",
        "Vocês enviam para todo o Brasil?",
    ],
)
async def test_l3_sem_restricao_conferida_todo_o_brasil_vai_ao_verificador(cliente):
    ficha = {**MALA, "restricao_envio": NENHUMA}
    r = await _auto(
        _p(TODO_O_BRASIL, "cobertura_regiao", [f"restricao:{ITEM_MALA}"]),
        _turno(cliente),
        fichas=[ficha],
    )
    assert r.decisao == VAI, (r.decisao, r.codigos, r.motivos)


async def test_l3_estado_fora_da_restricao_vai_ao_verificador():
    """Kia 22/10 16:32 (Maceió): a restrição da loja é RS (e RJ pelo valor); AL recebe."""
    ficha = {**A17, "restricao_envio": RS_LOJA}
    r = await _auto(
        _p(
            "Enviamos para Alagoas, sim; o frete e o prazo aparecem ao informar o seu CEP no anúncio.",
            "cobertura_regiao",
            [f"restricao:{ITEM_A17}"],
        ),
        _turno("Boa tarde vocês fazem entrega para Maceió Alagoas"),
        fichas=[ficha],
    )
    assert r.decisao == VAI, (r.decisao, r.codigos, r.motivos)


@pytest.mark.parametrize(
    "cliente,texto,restricao,codigo",
    [
        # "todo o Brasil" com restrição (loja RS + RJ pelo valor; a regra Apple→RJ).
        ("Vocês entregam em todo lugar?", TODO_O_BRASIL, RS_LOJA, checagens.REGIAO_CONTRADIZ),
        ("Vocês entregam em todo lugar?", TODO_O_BRASIL, RJ_APPLE, checagens.REGIAO_CONTRADIZ),
        # O cliente num estado restrito.
        (
            "Moro em Porto Alegre, entrega aqui no RS?",
            "Enviamos para o RS, sim; o frete aparece pelo CEP.",
            RS_LOJA,
            checagens.REGIAO_CONTRADIZ,
        ),
        (
            "Entrega no RJ?",
            "Sim, entregamos no Rio de Janeiro.",
            RJ_APPLE,
            checagens.REGIAO_CONTRADIZ,
        ),
        # Sem a prova (não se sabe): suave.
        ("Vocês entregam em todo lugar?", TODO_O_BRASIL, None, checagens.REGIAO_SEM_FONTE),
        ("Entrega em Manaus?", TODO_O_BRASIL, None, checagens.REGIAO_SEM_FONTE),
    ],
)
async def test_l3_negativos_seguem_pessoa(cliente, texto, restricao, codigo):
    ficha = {**A17, **({"restricao_envio": restricao} if restricao else {})}
    r = await _auto(
        _p(texto, "cobertura_regiao", [f"ficha:{ITEM_A17}"]), _turno(cliente), fichas=[ficha]
    )
    assert r.decisao != VAI, (texto, r.codigos)
    assert codigo in r.codigos, r.codigos


async def test_l3_pergunta_de_outro_produto_apple_nao_usa_a_prova_da_mala():
    """A prova é da MALA da conversa; o cliente pergunta do iPhone (Apple→RJ)."""
    ficha = {**MALA, "restricao_envio": NENHUMA}
    r = await _auto(
        _p(TODO_O_BRASIL, "cobertura_regiao", [f"restricao:{ITEM_MALA}"]),
        _turno("E o iPhone de vocês, entrega no Rio?"),
        fichas=[ficha],
    )
    assert r.decisao != VAI
    assert checagens.REGIAO_SEM_FONTE in r.codigos


async def test_l3_dois_anuncios_um_com_restricao():
    r = await _auto(
        _p(TODO_O_BRASIL, "cobertura_regiao", [f"restricao:{ITEM_MALA}"]),
        _turno("Vocês entregam em todo lugar?"),
        fichas=[{**MALA, "restricao_envio": NENHUMA}, {**A17, "restricao_envio": RS_LOJA}],
    )
    assert r.decisao != VAI
    assert checagens.REGIAO_CONTRADIZ in r.codigos


async def test_l3_ficha_sem_o_bloco_de_restricao_nao_tem_a_prova():
    """Duas fichas, só uma com a prova (a outra falhou ou não leu a descrição): não se sabe."""
    r = await _auto(
        _p(TODO_O_BRASIL, "cobertura_regiao", [f"restricao:{ITEM_MALA}"]),
        _turno("Vocês entregam em todo lugar?"),
        fichas=[{**MALA, "restricao_envio": NENHUMA}, C2],
    )
    assert r.decisao != VAI


def test_l3_sem_restricao_conferida():
    assert restricao_envio.sem_restricao_conferida(NENHUMA)
    assert not restricao_envio.sem_restricao_conferida(RS_LOJA)
    assert not restricao_envio.sem_restricao_conferida({**NENHUMA, "ufs_conforme_pedido": ["RJ"]})
    assert not restricao_envio.sem_restricao_conferida(None)


async def test_l3_restricao_do_anuncio_conferida(monkeypatch):
    """`conferida=True`: a prova só com a descrição lida; sem ela, None (não se sabe); com
    restrição, a chave "conferido"; sem `conferida`, o formato de antes."""

    async def _textos(_s, _p, _i, _item, titulo):
        return titulo, _textos.descricao

    async def _loja(_s, _i):
        return []

    monkeypatch.setattr(restricao_envio, "_textos_do_anuncio", _textos)
    monkeypatch.setattr(restricao_envio, "restricao_da_loja", _loja)
    plat = SimpleNamespace(value="shopee")
    _textos.descricao = "Mala de bordo com rodinhas."
    r = await restricao_envio.restricao_do_anuncio(
        None, plat, None, ITEM_MALA, MALA["titulo"], conferida=True
    )
    assert restricao_envio.sem_restricao_conferida(r) and r["conferido"] == CONFERIDO
    assert (
        await restricao_envio.restricao_do_anuncio(None, plat, None, ITEM_MALA, MALA["titulo"])
        is None
    )
    _textos.descricao = None
    assert (
        await restricao_envio.restricao_do_anuncio(
            None, plat, None, ITEM_MALA, MALA["titulo"], conferida=True
        )
        is None
    )
    _textos.descricao = "Não enviamos para o Rio de Janeiro."
    r = await restricao_envio.restricao_do_anuncio(
        None, plat, None, ITEM_MALA, MALA["titulo"], conferida=True
    )
    assert r["ufs"] == ["RJ"] and r["conferido"] == CONFERIDO and not r.get("nenhuma")
    r = await restricao_envio.restricao_do_anuncio(
        None, plat, None, "1", "Apple iPhone 15 128GB", conferida=True
    )
    assert r["ufs"] == ["RJ"] and r["fonte"] == restricao_envio.FONTE_ANUNCIO
    _textos.descricao = "Celular novo."
    r = await restricao_envio.restricao_do_anuncio(
        None, plat, None, "1", "Apple iPhone 15 128GB", conferida=True
    )
    assert r["ufs"] == ["RJ"] and r["fonte"] == restricao_envio.FONTE_REGRA_APPLE
    assert "conferido" not in await restricao_envio.restricao_do_anuncio(
        None, plat, None, "1", "Apple iPhone 15 128GB"
    )


# ═══════════════ A entrada do verificador (v11) e o prompt ═══════════════


def test_v11_entrada_traz_a_restricao_e_a_condicao_so_com_as_marcas():
    fichas = [{**MALA, "restricao_envio": NENHUMA}, {**A17, "restricao_envio": RS_LOJA}, WATCH]
    linhas = v.linhas_da_restricao([json.dumps(fichas, ensure_ascii=False)])
    assert linhas[0] == v.CABECALHO_RESTRICAO
    assert any(f"anuncio:{ITEM_MALA}" in x and "nenhuma restrição" in x for x in linhas)
    assert any(
        f"anuncio:{ITEM_A17}" in x and "não envia para RS" in x and "valor do pedido: RJ" in x
        for x in linhas
    )
    cond = v.linhas_da_condicao([json.dumps(fichas, ensure_ascii=False)])
    assert cond[0] == v.CABECALHO_CONDICAO
    assert any(f"anuncio:{ITEM_WATCH}" in x and "lacrado: sim (descrição)" in x for x in cond)
    # Sem as marcas novas (a restrição sem "conferido", a ficha sem "condicao"): nada.
    velha = {
        **MALA,
        "restricao_envio": {
            "ufs": ["RJ"],
            "fonte": "anuncio",
            "trecho": "Não enviamos para o RJ",
            "ufs_conforme_pedido": [],
        },
    }
    assert v.linhas_da_restricao([json.dumps([velha])]) == []
    assert v.linhas_da_condicao([json.dumps([MALA])]) == []


def test_v11_sistema_e_versao():
    assert v.VERSAO in ("v11", "v12", "v13")
    assert (
        "RESTRIÇÃO DE ENVIO POR ESTADO" in v.SISTEMA
        and "CONDIÇÃO DECLARADA NO ANÚNCIO" in v.SISTEMA
    )
    assert v.MODELO == "claude-haiku-5-5"


def test_v11_calibracao_tem_as_liberacoes_e_as_linhas_de_antes_sem_as_secoes_novas():
    casos = calibrar.ler_casos(calibrar.CASOS_PADRAO)
    novos = [c for c in casos if c.get("grupo") == "liberacoes"]
    assert {c["assunto"] for c in novos} >= {
        "espec_celular",
        "conteudo_da_caixa",
        "originalidade_condicao",
        "cobertura_regiao",
    }
    for assunto in ("espec_celular", "originalidade_condicao", "cobertura_regiao"):
        dele = [c for c in novos if c["assunto"] == assunto]
        assert any(c["esperado"]["ok_para_envio_automatico"] for c in dele), assunto
        assert any(not c["esperado"]["ok_para_envio_automatico"] for c in dele), assunto
    for c in casos:
        entrada = calibrar.entrada_do_caso(c)
        tem = v.CABECALHO_RESTRICAO in entrada or v.CABECALHO_CONDICAO in entrada
        # D1–D3 (23/10/2026): as linhas do grupo `dono_d123` (a garantia Apple, com a
        # condição do anúncio) também trazem a seção; e as do `fechar_eval` (o resumo da
        # restrição, o C2 e o relógio, 23/10/2026).
        assert tem == (
            c.get("grupo") in ("liberacoes", "dono_d123", "fechar_eval")
            and (
                "conferido" in json.dumps(c["evidencias"])
                or "condicao" in json.dumps(c["evidencias"])
            )
        ), c["id"]


def test_prompt_bloco_2_tem_as_liberacoes_e_o_bloco_1_nao_muda():
    bloco2 = prompt.bloco_da_plataforma("shopee", _categorias_do_manual())
    assert prompt.PRODUTO_CONDICAO_REGIAO in bloco2
    assert TODO_O_BRASIL in prompt.PRODUTO_CONDICAO_REGIAO
    assert prompt.PRODUTO_CONDICAO_REGIAO not in prompt.bloco_geral()
    assert prompt.COMO_PENSAR in bloco2


async def test_l1_dois_anuncios_na_conversa_o_dado_de_um_nao_vale_para_o_outro():
    """O cartão do A17 e o item do pedido A18 (com NFC): o "tem NFC" citando os dois é
    pessoa; citando só o A17, a ficha dele ("NFC: No") não sustenta."""
    a18 = {
        "de_onde": "item do pedido da conversa",
        "item_id": "58266122162",
        "titulo": "Uranyx A18 Pro Max 16.128 Gb NFC",
        "atributos": ["NFC: Yes"],
    }
    msgs = _turno("Esse tem NFC?")
    r = await _auto(
        _p("Tem sim, ele tem NFC.", "espec_celular", [f"ficha:{ITEM_A17}", "ficha:58266122162"]),
        msgs,
        fichas=[A17, a18],
    )
    assert r.decisao != VAI
    r = await _auto(
        _p("Tem sim, ele tem NFC.", "espec_celular", [f"ficha:{ITEM_A17}"]), msgs, fichas=[A17, a18]
    )
    assert r.decisao != VAI
    # Revisão L1–L3 (achado 2): citando só o A18 (o do pedido) sem o cliente dizer de qual
    # anúncio fala ("Esse tem NFC?"), não se sabe — pessoa; com o "A18" na pergunta, vai ao
    # verificador.
    r = await _auto(
        _p("Tem sim, o A18 tem NFC.", "espec_celular", ["ficha:58266122162"]),
        msgs,
        fichas=[A17, a18],
    )
    assert r.decisao != VAI, (r.codigos, r.motivos)
    r = await _auto(
        _p("Tem sim, o A18 tem NFC.", "espec_celular", ["ficha:58266122162"]),
        _turno("Esse A18 tem NFC?"),
        fichas=[A17, a18],
    )
    assert r.decisao == VAI, (r.codigos, r.motivos)


# ═══════════════ E2E: o `agente.responder` inteiro, com o verificador e o envio ═══════════════


async def _kia_auto(db, make_user, redis_mem, lojas, pergunta: str):
    conversa, _ = await _kia(db, make_user, redis_mem, lojas)
    await _ligar(
        db,
        conversa,
        modo=constantes.MODO_AUTO,
        auto_categorias=SHOPEE_REAL.auto_categorias,
        auto_orientacao=SHOPEE_REAL.auto_orientacao,
        auto_agente=SHOPEE_REAL.auto_agente,
    )
    await _msg(db, conversa, pergunta)
    return conversa


async def test_e2e_l1_o_fone_vem_junto_pela_ficha_sai_sozinho(
    db, make_user, agente_ligado, api, envios, redis_mem, lojas
):
    """O A18 da Kia ("Uranyx S5 + Fone UFB10" na descrição): "o fone vem junto?" pela ficha do
    anúncio da conversa — com a configuração real da Shopee (o produto fora do `auto_agente`),
    a marca do código abre a porta, o verificador confere e o rascunho sai sozinho. Em
    525c0e29 ficava com pessoa ("assunto_de_produto" / a trava da ficha)."""
    conversa = await _kia_auto(db, make_user, redis_mem, lojas, "E o fone vem junto?")
    texto = "Sim, o fone UFB10 vem junto, conforme a descrição do anúncio."
    api.roteiro = [
        _final(texto=texto, assunto="conteudo_da_caixa", evidencias=[f"ficha:{ITEM_A18}"])
    ]

    r = await ia.gerar_rascunho(db, conversa)

    agente_ = _do_agente(r)
    assert agente_.get("produto_pelo_anuncio") is True, r.motivo
    assert agente_["verificador"]["rodou"] is True and agente_["verificador"]["ok"] is True
    assert not r.precisa_humano, r.motivo
    assert [e["texto"] for e in envios] == [r.texto] and _decisao(r) == "enviado"
    # A entrada do verificador: a condição declarada (atributo de condição do A18) e o
    # assunto de produto liberado (a marca do código).
    entrada = json.dumps(api.verificador[-1], ensure_ascii=False)
    assert "conteudo_da_caixa" in entrada.split("ASSUNTOS LIBERADOS")[1].split("\\n")[0]


async def test_e2e_l1_a_caracteristica_fora_da_ficha_nem_chama_o_verificador(
    db, make_user, agente_ligado, api, envios, redis_mem, lojas
):
    conversa = await _kia_auto(db, make_user, redis_mem, lojas, "Ele tem carregamento sem fio?")
    api.roteiro = [
        _final(
            texto="Sim, ele tem carregamento sem fio por indução.",
            assunto="espec_celular",
            evidencias=[f"ficha:{ITEM_A18}"],
        )
    ]

    r = await ia.gerar_rascunho(db, conversa)

    assert _do_agente(r).get("produto_pelo_anuncio") is not True
    assert api.verificador == [] and envios == []
    assert r.precisa_humano


def test_l1_pre_filtro_do_reenvio_le_a_marca():
    """O reenvio (o cron) pega o de produto com a marca do código, sem o `auto_agente`."""
    from sqlalchemy.dialects import postgresql

    from app.models import AtendimentoCanal, AtendimentoRascunho

    compilado = ia._sql_assunto_liberado(AtendimentoRascunho, AtendimentoCanal).compile(
        dialect=postgresql.dialect()
    )
    valores = json.dumps([str(x) for x in compilado.params.values()], ensure_ascii=False)
    assert "produto_pelo_anuncio" in valores
    listas = [list(x) for x in compilado.params.values() if isinstance(x, list | tuple)]
    assert list(constantes.ASSUNTOS_LIBERADOS_PELO_ANUNCIO) in listas
    assert "resistencia_agua" not in constantes.ASSUNTOS_LIBERADOS_PELO_ANUNCIO


# ═══════════════ Revisão L1–L3 (23/10/2026): os achados dos revisores ═══════════════

# O cartão do A17 ("NFC: No") e o item do pedido A18 (5G, 108MP, "NFC: Yes").
A18_PEDIDO = {
    "de_onde": "item do pedido da conversa",
    "item_id": "22394778999",
    "titulo": "Uranyx A18 Pro 5G 8+256GB Camera 108MP",
    "atributos": ["Marca: Uranyx", "NFC: Yes"],
    "descricao": "Celular 5G com camera de 108MP",
}
PEDIDO_A18 = {"pedido": {"itens": [{"descricao": A18_PEDIDO["titulo"]}]}}


@pytest.mark.parametrize(
    "cliente,texto,fichas",
    [
        # Só o item do pedido (o A18) no `fatos`: o "5G" do título dele não é dado do A17.
        ("esse A17 é 5g?", "Sim, ele é 5G.", [A17]),
        ("ele é 5g? e a câmera?", "Sim, o A17 Pro Max é 5G.", [A17]),
        # O cartão e o pedido como fichas, citando só o A17.
        (
            "ele é 5g? e a câmera?",
            "Sim, o A17 Pro Max é 5G e tem câmera de 108MP.",
            [A17, A18_PEDIDO],
        ),
    ],
)
async def test_revisao_l1_titulo_do_pedido_nao_e_dado_do_anuncio_citado(cliente, texto, fichas):
    """Achado 1 (alta): o `fontes_extra` (o título do cartão, os itens do pedido) era dado da
    liberação — o A18 do pedido emprestava o 5G ao A17 citado."""
    r = await _auto(
        _p(texto, "espec_celular", _ev(ITEM_A17)),
        _turno(cliente),
        fichas=fichas,
        anuncio_titulo=A17["titulo"],
        fatos_base=PEDIDO_A18,
    )
    assert r.decisao != VAI, (r.codigos, r.motivos)
    assert r.rascunho.fatos["agente"].get("produto_pelo_anuncio") is not True


async def test_revisao_l1_o_nome_do_pedido_igual_ao_citado_continua_nome():
    """O item do pedido com o MESMO título do anúncio citado (a linha da variação) segue
    sendo nome, não característica: a resposta boa continua indo ao verificador."""
    pedido = {"pedido": {"itens": [{"descricao": f"{A17['titulo']} - Laranja"}]}}
    r = await _auto(
        _p("Não, o A17 Pro Max não tem NFC.", "espec_celular", _ev(ITEM_A17)),
        _turno("Esse A17 tem NFC?"),
        fichas=[A17],
        anuncio_titulo=A17["titulo"],
        fatos_base=pedido,
    )
    assert r.decisao == VAI, (r.codigos, r.motivos)


@pytest.mark.parametrize(
    "cliente,texto,citada,vai",
    [
        # O cliente fala do A17 (o cartão, "NFC: No"); o rascunho cita a ficha do A18.
        ("esse A17 tem NFC?", "Sim, tem NFC.", "22394778999", False),
        # Sem dizer de qual: não se sabe.
        ("Esse tem NFC?", "Sim, tem NFC.", "22394778999", False),
        ("Esse tem NFC?", "Não, o A17 Pro Max não tem NFC.", ITEM_A17, False),
        # Nomeando os dois: não se sabe.
        ("O A17 ou o A18 tem NFC?", "Sim, o A18 tem NFC.", "22394778999", False),
        # O cliente nomeia o do anúncio citado.
        ("esse A17 tem NFC?", "Não, o A17 Pro Max não tem NFC.", ITEM_A17, True),
        ("e o A18, tem NFC?", "Sim, o A18 tem NFC.", "22394778999", True),
    ],
)
async def test_revisao_l1_cartao_e_pedido_so_o_anuncio_que_o_cliente_nomeia(
    cliente, texto, citada, vai
):
    """Achado 2 (alta): com o cartão e o pedido de anúncios diferentes, a ficha citada tem de
    ser a do anúncio de que o cliente fala."""
    r = await _auto(
        _p(texto, "espec_celular", [f"ficha:{citada}"]),
        _turno(cliente),
        fichas=[A17, A18_PEDIDO],
        anuncio_titulo=A17["titulo"],
        fatos_base=PEDIDO_A18,
    )
    assert (r.decisao == VAI) is vai, (r.decisao, r.codigos, r.motivos)


def test_revisao_l1_modelos_da_pergunta():
    assert checagens._modelos_do("esse A17 é 5g? IP68? wifi6? android15") == {"a17"}
    dados = [
        {"item_id": "1", "titulo": A17["titulo"]},
        {"item_id": "2", "titulo": A18_PEDIDO["titulo"]},
    ]
    assert checagens._cliente_fala_so_do(["o A17 tem NFC?"], dados, "1")
    assert not checagens._cliente_fala_so_do(["tem NFC?"], dados, "1")
    assert not checagens._cliente_fala_so_do(["o A17 tem NFC?"], dados, "2")
    assert not checagens._cliente_fala_so_do(["A17 ou A18?"], dados, "1")


@pytest.mark.parametrize(
    "cliente,texto",
    [
        ("Vocês entregam o celular Redmi Note 14 no Rio Grande do Sul?", TODO_O_BRASIL),
        (
            "e o celular A17 de vcs, manda pro RS?",
            "Sim, entregamos no Rio Grande do Sul; o frete e o prazo aparecem ao informar o seu "
            "CEP no anúncio.",
        ),
        ("e o celular de vcs, manda pra qualquer estado?", TODO_O_BRASIL),
        ("E o Samsung de vocês, chega em Manaus?", TODO_O_BRASIL),
    ],
)
async def test_revisao_l3_pergunta_de_outro_produto_nao_usa_a_prova(cliente, texto):
    """Achado 3 (média): a prova de "nenhuma restrição" da MALA não vale para a pergunta de
    outro produto (qualquer um, não só Apple)."""
    r = await _auto(
        _p(texto, "cobertura_regiao", [f"restricao:{ITEM_MALA}"]),
        _turno(cliente),
        fichas=[{**MALA, "restricao_envio": NENHUMA}],
    )
    assert r.decisao != VAI, (r.codigos, r.motivos)
    assert checagens.REGIAO_SEM_FONTE in r.codigos


@pytest.mark.parametrize(
    "cliente", ["Essa mala entrega em Manaus?", "Vocês enviam a mala pra todo o Brasil?"]
)
async def test_revisao_l3_a_pergunta_do_mesmo_produto_usa_a_prova(cliente):
    r = await _auto(
        _p(TODO_O_BRASIL, "cobertura_regiao", [f"restricao:{ITEM_MALA}"]),
        _turno(cliente),
        fichas=[{**MALA, "restricao_envio": NENHUMA}],
    )
    assert r.decisao == VAI, (r.codigos, r.motivos)


def test_revisao_l3_outro_produto_unidade():
    mala = _plano_titulo(MALA["titulo"])
    a17 = _plano_titulo(A17["titulo"])
    assert checagens._pergunta_de_outro_produto(["e o celular, entrega no RS?"], mala)
    assert checagens._pergunta_de_outro_produto(["o A18 entrega no RS?"], a17)
    assert checagens._pergunta_de_outro_produto(["e o redmi?"], a17)
    # O "celular" com o título sem a palavra (o A17) é o mesmo produto.
    assert not checagens._pergunta_de_outro_produto(["esse celular entrega no RS?"], a17)
    assert not checagens._pergunta_de_outro_produto(["esse A17 entrega no RS?"], a17)
    assert not checagens._pergunta_de_outro_produto(["entrega em Manaus?"], mala)


def _plano_titulo(t: str) -> str:
    return " ".join(checagens._plano(t).split())


@pytest.mark.parametrize(
    "descricao",
    [
        "Mala de bordo. ATENÇÃO: não enviamos para a região Norte.",
        "Envio somente para São Paulo e Rio de Janeiro.",
        "Enviamos apenas para as regiões Sul e Sudeste.",
        "Não atendemos o Nordeste com este produto, só enviamos para o Sudeste.",
        "Mala de bordo. Enviamos rápido. Exceto Rio Grande do Sul",
        "Este produto não pode ser enviado para Manaus.",
        "Vendas somente para o estado de SP",
    ],
)
async def test_revisao_l3_frase_de_envio_que_o_leitor_nao_le_tira_a_prova(monkeypatch, descricao):
    """Achado 6 (baixa): a frase que limita o envio sem uma UF que o leitor ache não deixa a
    prova "nenhuma" sair (não se sabe)."""

    async def _textos(_s, _p, _i, _item, titulo):
        return titulo, descricao

    async def _loja(_s, _i):
        return []

    monkeypatch.setattr(restricao_envio, "_textos_do_anuncio", _textos)
    monkeypatch.setattr(restricao_envio, "restricao_da_loja", _loja)
    r = await restricao_envio.restricao_do_anuncio(
        None, SimpleNamespace(value="shopee"), None, ITEM_MALA, MALA["titulo"], conferida=True
    )
    assert r is None or not restricao_envio.sem_restricao_conferida(r), r


@pytest.mark.parametrize(
    "descricao",
    [
        "Mala de bordo com rodinhas 360 e cadeado.",
        "Enviamos em até 24 horas úteis.",
        "Produto novo. Envio imediato!",
        # Real (Shopee, 23/10/2026): o "somente para" que não fala de envio.
        "Envio rápido.\nNFC – somente para abrir portas eletrônicas",
    ],
)
def test_revisao_l3_frase_sem_limite_de_envio_mantem_a_prova(descricao):
    assert not restricao_envio.limitacao_de_envio_nao_lida(MALA["titulo"], descricao)


@pytest.mark.parametrize(
    "titulo,descricao,raw,campo",
    [
        (
            "Apple Watch Series 9 45mm Vitrine",
            "Produto em perfeito estado.",
            {"item": {"condition": "NEW"}},
            "novo",
        ),
        (
            "Apple Watch Series 9 45mm",
            "Produto de exposição, revisado.",
            {"item": {"condition": "NEW"}},
            "novo",
        ),
        (
            "iPhone 13 128GB Lacrado",
            "Produto novo. Pode ter sido aberto para teste de qualidade.",
            None,
            "lacrado",
        ),
        ("Fone Bluetooth Pro 2 Lacrado", "Caixa aberta para conferência.", None, "lacrado"),
    ],
)
def test_revisao_l2_vitrine_e_caixa_aberta_sao_conflito(titulo, descricao, raw, campo):
    """Achado 4 (média): "Vitrine" solto no título (e "de exposição", "caixa aberta") contra o
    atributo/título que diz novo/lacrado: o anúncio se contradiz."""
    from app.services.atendimento import condicao_anuncio

    cond = condicao_anuncio.condicao_do_anuncio(titulo, descricao, raw)
    assert cond and cond.get("conflito_no_anuncio") is True, cond
    assert cond[campo]["valor"] == condicao_anuncio.CONFLITO, cond


async def test_revisao_l2_vitrine_com_atributo_novo_segue_pessoa():
    from app.services.atendimento import condicao_anuncio

    titulo = "Apple Watch Series 9 45mm Vitrine"
    ficha = {
        "de_onde": DE_ONDE,
        "item_id": "33",
        "titulo": titulo,
        "atributos": ["Tamanho: 45mm"],
        "descricao": "Apple Watch Series 9 com tela Retina.",
        "condicao": condicao_anuncio.condicao_do_anuncio(
            titulo, "Apple Watch Series 9 com tela Retina.", {"item": {"condition": "NEW"}}
        ),
    }
    r = await _auto(
        _p("Sim, ele é novo.", "originalidade_condicao", ["ficha:33"]),
        _turno("é novo?"),
        fichas=[ficha],
        anuncio_titulo=titulo,
    )
    assert r.decisao != VAI, (r.codigos, r.motivos)


A17_CORES = {
    **A17,
    "variacoes": [{"nome": "Preto", "disponivel": True}, {"nome": "Azul", "disponivel": True}],
}


@pytest.mark.parametrize("ficha", [A17, A17_CORES])
@pytest.mark.parametrize(
    "texto",
    [
        "Ele tem 12 GB de RAM e 128 GB de memória interna.",
        "Possui 12 GB de RAM e 128 GB de armazenamento.",
    ],
)
async def test_revisao_l1_capacidade_nao_e_estoque(ficha, texto):
    """Achado 5 (média): "Ele tem 12 GB de RAM…" à pergunta de característica era lido como
    disponibilidade de versão (variacao_sem_dado)."""
    ev = _ev(ITEM_A17) + ([f"variacoes:{ITEM_A17}"] if ficha.get("variacoes") else [])
    r = await _auto(
        _p(texto, "espec_celular", ev),
        _turno("quanto de ram e memória?"),
        fichas=[ficha],
        anuncio_titulo=A17["titulo"],
    )
    assert r.decisao == VAI, (r.codigos, r.motivos)


@pytest.mark.parametrize(
    "cliente,texto",
    [
        # A pergunta é de versão/estoque: o "tem" continua disponibilidade.
        ("tem a versão de 256 GB?", "Tem sim, ele tem 256 GB de memória."),
        ("ainda tem o de 128 GB?", "Tem sim, ele tem 128 GB de memória interna."),
        # O número que o anúncio não traz.
        ("quanto de memória?", "Ele tem 256 GB de memória interna."),
    ],
)
async def test_revisao_l1_capacidade_negativos_seguem_pessoa(cliente, texto):
    r = await _auto(
        _p(texto, "espec_celular", _ev(ITEM_A17)),
        _turno(cliente),
        fichas=[A17],
        anuncio_titulo=A17["titulo"],
    )
    assert r.decisao != VAI, (r.codigos, r.motivos)


def test_revisao_l1_capacidade_sem_estoque_unidade():
    assert checagens.capacidade_sem_estoque(True, "espec_celular", ["quanto de ram?"])
    assert not checagens.capacidade_sem_estoque(False, "espec_celular", ["quanto de ram?"])
    assert not checagens.capacidade_sem_estoque(True, "estoque_variacao", ["quanto de ram?"])
    assert not checagens.capacidade_sem_estoque(True, "originalidade_condicao", ["é novo?"])
    assert not checagens.capacidade_sem_estoque(True, "espec_celular", ["tem a de 256gb?"])
    assert not checagens.capacidade_sem_estoque(True, "espec_celular", ["tem em estoque?"])


S7 = {
    "de_onde": DE_ONDE,
    "item_id": "23899338273",
    "titulo": "Fossibot S7+ 128GB 16GB RAM + Fone UFB10 + Smart Watch",
    "atributos": ["Marca: Fossibot"],
    "descricao": (
        "Caixa Slim, o aparelho acompanha apenas o cabo de carregamento!\nFossibot S7+\n"
        "Fone Sem Fio Com Visor UFB10 + Smart Watch Com 7 Pulseiras\nAndroid 16"
    ),
}


async def test_revisao_l1_lista_exclusiva_do_anuncio_nega_o_carregador():
    """Achado 7 (baixa): "o aparelho acompanha apenas o cabo" — o "não vem carregador" tem a
    fonte."""
    r = await _auto(
        _p(
            "Não vem carregador: o aparelho acompanha apenas o cabo de carregamento.",
            "conteudo_da_caixa",
            ["ficha:23899338273"],
        ),
        _turno("vem carregador?"),
        fichas=[S7],
        anuncio_titulo=S7["titulo"],
    )
    assert r.decisao == VAI, (r.codigos, r.motivos)


@pytest.mark.parametrize(
    "texto,ficha,cliente",
    [
        # O fone está no título do S7: "não vem fone" contradiz.
        ("Não vem fone, só o cabo.", S7, "vem fone?"),
        # Sem a lista exclusiva (o A17 não diz "apenas"): o "não vem carregador" sem fonte.
        ("Não vem carregador.", A17, "vem carregador?"),
    ],
)
async def test_revisao_l1_lista_exclusiva_negativos(texto, ficha, cliente):
    r = await _auto(
        _p(texto, "conteudo_da_caixa", [f"ficha:{ficha['item_id']}"]),
        _turno(cliente),
        fichas=[ficha],
        anuncio_titulo=ficha["titulo"],
    )
    assert r.decisao != VAI, (r.codigos, r.motivos)


def test_revisao_busca_sem_no_titulo_diz_o_que_prova():
    """Calibração v10 (boa-catalogo-pelicula-sem-no-titulo): a linha do acessório que nenhum
    título da loja tem diz que ele é o lastro do "não vendemos"; a busca vazia, nunca."""
    linhas = v.linhas_da_busca(
        [
            {"termo": "película A18", "anuncios": [], "sem_no_titulo": ["pelicula"]},
            {"termo": "fone", "anuncios": []},
        ],
        (),
    )
    assert "a loja NÃO vende esse acessório" in linhas[1]
    assert "isso não prova que a loja não vende" in linhas[2]
    assert "mesmo quando o rascunho o chama de «outro anúncio da nossa loja»" in " ".join(
        v.SISTEMA.split()
    )
