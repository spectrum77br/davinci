# ruff: noqa: E501, F401, F811 — as frases do eval ficam inteiras; fixtures importadas
"""Os bugs do EVAL REAL do agente (80 conversas da Kia na Shopee, 21/10/2026).

Cada teste reproduz o sintoma de verdade que o eval mostrou (só os itens do
conjunto `dev` foram abertos) e prova o conserto. Sem modelo, sem rede: o SDK
`anthropic` 1.9.0 de verdade sobre o transporte falso do httpx2.

1. A máscara do eval virava o id da evidência em "[CPF]": o motivo saía
   "evidência citada sem prova (variacoes:[CPF])", sem bater com a evidência
   citada nem com o registro (`replay.mascarar_texto`).
2. O número que o modelo VIU no pacote ou nas ferramentas era "número que não
   veio do sistema": o "16.128" do título (RAM e armazenamento) lido junto, o
   "Android 15.0", o zero à esquerda, o cartão que o cliente mandou ("42mm ou
   46mm") — `ContextoAgente.permitidos_extra`/`permitir_numeros`.
3. A ficha no automático disparava em `estoque_variacao`, `troca_devolucao`,
   `opiniao_recomendacao`, `acessorios_avulsos` (a spec 6.2 a deixa para os
   assuntos de dado do produto; estoque e variação são da `conferir_variacoes`).
4. "RuntimeError: generator didn't stop after athrow()" no fim de toda
   conversa: os geradores do Tool Runner ficavam suspensos depois do `break`.
5. "…pela loja pelo próprio app da Shopee" era caminho para fora da plataforma
   (e a recusa da retirada na loja também); "direto com a loja pelo próprio app
   da Shopee", negociação por fora — só no caminho do agente.
6. A lacuna que o código não preencheu ("{rastreio}", "{nf_numero}") ficava no
   rascunho do agente.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import json
import re
import sys
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import httpx2
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.services.atendimento import constantes, ia, validador
from app.services.atendimento.agente import checagens, custo, pacote, replay
from app.services.atendimento.agente import contexto as agente_ctx
from app.services.atendimento.agente.contexto import ContextoAgente
from app.services.atendimento.agente.saida import Proposta
from app.services.atendimento.constantes import (
    MODO_AUTO,
    MODO_COPILOTO,
    RASCUNHO_BLOQUEADO,
    RASCUNHO_PENDENTE,
)
from tests.test_atendimento_agente_e2e import (
    CHAVE,
    _decisao,
    _do_agente,
    _final,
    _ligar,
    _sem_limites,
    agente_ligado,
    api,
    envios,
    ia_ligada,
    telegram,
)
from tests.test_atendimento_agente_furos import _completo, _sai
from tests.test_atendimento_agente_laco import (
    _Api,
    _ferramenta,
    _Relogio,
    _resposta,
    _rodar,
    ajustes,
    pecas,
    redis_falso,
)
from tests.test_atendimento_agente_laco import _final as _final_do_laco
from tests.test_atendimento_conferir_saida import _base as _base_conferir
from tests.test_atendimento_conferir_saida import _chamada
from tests.test_atendimento_ia import (
    RASTREIO,
    _conversa,
    _importar_manual_base,
    _msg,
    _pedido_completo,
)

ITEM = "58265493964"  # o anúncio A18 da Kia: 11 dígitos, a forma de um CPF


def _conv() -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid4(),
        plataforma="shopee",
        canal="chat",
        pedido_marketplace=None,
        anuncio_id=None,
        dados={},
    )


def _proposta(texto: str, assunto: str, evidencias: list[str] | None = None) -> Proposta:
    return Proposta(
        acao="responder",
        texto=texto,
        assunto=assunto,
        confianca=0.9,
        acao_pessoa=None,
        motivo_pessoa="",
        evidencias=evidencias or [],
    )


# ═══════════════ 1. a máscara do eval e os ids de evidência ═══════════════


def test_1_o_motivo_da_evidencia_sem_prova_sai_do_eval_com_o_id_inteiro():
    """O sintoma real: "evidência citada sem prova (variacoes:[CPF])"."""
    ctx = ContextoAgente.da_conversa(_conv(), None, modo=MODO_AUTO)
    ctx.permitir(ids=[agente_ctx.id_ficha(ITEM)])
    (p,) = checagens.conferir_evidencias([f"ficha:{ITEM}", f"variacoes:{ITEM}"], ctx)
    assert p.motivo == f"evidência citada sem prova (variacoes:{ITEM})"
    # O que o eval grava (`decisao.motivos`/`erros`/`avisos_auto`): o id inteiro.
    gravado = replay.mascarar_texto(p.motivo, ["Maria"], ["Campinas"])
    assert gravado == p.motivo and "[CPF]" not in gravado
    # Bate com a evidência citada (`rastro.evidencias`, que vai crua) e com o registro.
    (citada,) = re.findall(r"\((.*)\)", gravado)
    assert citada in [f"ficha:{ITEM}", f"variacoes:{ITEM}"]
    assert ctx.registro.desconhecidas([citada]) == [f"variacoes:{ITEM}"]


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [
        (
            "evidência citada sem prova (variacoes:58265493964, ficha:22299473628, espec:19699870359)",
            "evidência citada sem prova (variacoes:58265493964, ficha:22299473628, espec:19699870359)",
        ),
        (
            "evidência citada sem prova (garantia:pedido:{numero_pedido}, regra:cancelamento)",
            "evidência citada sem prova (garantia:pedido:{numero_pedido}, regra:cancelamento)",
        ),
        (
            "pedido:702-1234567-1234567 e modelo:9b5b51ff-d01c-4476-9586-7763c3310f68.",
            "pedido:702-1234567-1234567 e modelo:9b5b51ff-d01c-4476-9586-7763c3310f68.",
        ),
        # Fora do id, a máscara de sempre: CPF, telefone, nome (até dentro do id).
        (
            "meu cpf é 123.456.789-09, o fone 11 98765-4321 e a Maria viu a ficha:58265493964.",
            "meu cpf é [CPF], o fone [TELEFONE] e a [NOME] viu a ficha:58265493964.",
        ),
        ("o CPF 12345678901 do cliente", "o CPF [CPF] do cliente"),
        ("fato:Maria", "fato:[NOME]"),
    ],
)
def test_1_a_mascara_poupa_so_o_id_da_evidencia(texto, esperado):
    assert replay.mascarar_texto(texto, ["Maria"], []) == esperado
    # Sem os dígitos (o pacote e as ferramentas, já mascarados): como antes.
    assert replay.mascarar_texto("variacoes:58265493964", [], [], digitos=False) == (
        "variacoes:58265493964"
    )


# ═══════════════ 2. o número que o modelo viu ═══════════════


@pytest.mark.parametrize(
    ("textos", "soltos"),
    [
        (['{"titulo":"Uranyx A18 Pro Max NFC 16.128 Gb"}'], {"16", "128"}),
        (['{"valor":"Android 15.0"}'], {"15", "0"}),
        (['{"data_da_compra":"05/09/2026"}'], {"5", "9"}),
        (['{"tela":"6,9 polegadas"}'], {"6", "9"}),
        (['{"x": 12}'], set()),
        ([], set()),
    ],
)
def test_2_as_formas_soltas_dos_numeros(textos, soltos):
    assert set(agente_ctx.numeros_soltos(textos).split()) == soltos


def test_2_a_ram_e_o_armazenamento_do_titulo_nao_sao_inventados():
    """O "16.128 Gb" do título vira "16128" no `ia._numeros`: "16 GB de RAM e 128
    GB" — o dado do anúncio — era "número que não veio do sistema (16, 128)"."""
    ctx = ContextoAgente.da_conversa(_conv(), None, modo=MODO_AUTO)
    # Revisão do r5i: as formas soltas só dos textos de PRODUTO (a ficha).
    ctx.permitir(
        textos=['{"titulo":"Uranyx A18 Pro Max NFC 16.128 Gb Memoria S5 Premium"}'], produto=True
    )
    texto = "Olá! Este modelo tem 16 GB de RAM e 128 GB de armazenamento."
    # O sintoma: com só o registro (antes), os dois números eram inventados.
    assert ia.numeros_inventados(texto, "\n".join(ctx.registro.textos)) == ["16", "128"]
    assert ia.numeros_inventados(texto, "\n".join(ctx.permitidos_extra())) == []
    # O inventado de verdade continua pego.
    assert ia.numeros_inventados(
        "Olá! Ele tem 24 GB de RAM.", "\n".join(ctx.permitidos_extra())
    ) == ["24"]


async def test_2_a_conferencia_do_agente_com_os_numeros_do_anuncio():
    ctx = ContextoAgente.da_conversa(_conv(), None, modo=MODO_COPILOTO)
    ctx.permitir(
        textos=['{"titulo":"Celular X 8.256 Gb","especificacoes":{"sistema":"Android 15.0"}}'],
        produto=True,
    )
    conversa, kw = _chamada(
        _base_conferir(
            texto="Olá! Ele tem 8 GB de RAM, 256 GB de armazenamento e vem com o Android 15.",
            categoria="outro",
            ctx={"pedido": None},
        )
    )
    antes = await ia.conferir_saida(
        None, conversa, SimpleNamespace(), **kw, agente=True, permitidos_extra=ctx.registro.textos
    )
    assert any(m.startswith(ia.MOTIVO_NUMERO_INVENTADO) for m in antes.motivos)
    depois = await ia.conferir_saida(
        None,
        conversa,
        SimpleNamespace(),
        **kw,
        agente=True,
        permitidos_extra=ctx.permitidos_extra(),
    )
    assert not any(m.startswith(ia.MOTIVO_NUMERO_INVENTADO) for m in depois.motivos), depois.motivos
    assert depois.status == RASCUNHO_PENDENTE


async def test_2_o_cartao_que_o_cliente_mandou_libera_so_os_numeros(db: AsyncSession, make_user):
    """O "42mm ou 46mm" do cartão (de outro vendedor) estava no pacote e era
    "número que não veio do sistema (11, 42, 46)"; o cartão continua sem ser
    fonte (não entra no registro que o verificador lê)."""
    from tests.test_atendimento_agente_ferramentas import _base as _base_f
    from tests.test_atendimento_agente_ferramentas import _ctx

    conversa = await _conversa(db, make_user, pedido=None)
    conversa.anuncio_id = None
    await db.commit()
    m = await _msg(db, conversa, "42 ou 46mm?")
    m.anexos = [
        {
            "tipo": "produto",
            "item_id": "77777777777",
            "titulo": "Apple Watch Series 11 - 42mm ou 46mm",
            "variacao": "Prata, 46mm",
        }
    ]
    await db.commit()
    base = await _base_f(db, conversa, recentes=[m])
    ctx = _ctx(conversa, base)
    pac = await pacote.montar_pacote(db, conversa, m, base, ctx)
    assert "42mm ou 46mm" in pac.texto
    assert not any("46mm" in t for t in ctx.registro.textos)
    texto = (
        "O anúncio que você enviou, do Apple Watch Series 11 de 42mm ou 46mm, não é desta "
        "loja: aqui só conseguimos falar dos anúncios da nossa loja."
    )
    vistos = "\n".join([json.dumps(pac.fatos, ensure_ascii=False), *ctx.registro.textos])
    assert ia.numeros_inventados(texto, vistos) == ["11", "42", "46"]  # o sintoma
    assert ia.numeros_inventados(texto, "\n".join(ctx.permitidos_extra())) == []


# ═══════════════ 3. a ficha no automático ═══════════════

RELOGIO_44 = [
    {
        "item_id": "19699870359",
        "titulo": "Apple Watch Se 2 GPS 44mm",
        "variacoes": [
            {"nome": "Preto, 44mm", "disponivel": False, "confiavel": True},
            {"nome": "Preto (Tecido), 44mm", "disponivel": False, "confiavel": True},
        ],
        "variacoes_completas": True,
    }
]
MACBOOK = [
    {
        "item_id": "58207425520",
        "titulo": "Apple MacBook Neo",
        "variacoes": [
            {"nome": n, "disponivel": False, "confiavel": True}
            for n in ("Rose", "Amarelo", "Azul", "Prata")
        ],
        "variacoes_completas": True,
    }
]
URANYX = [
    {
        "item_id": "58256226914",
        "titulo": "Celular Uranyx F110 Pro 5G Dual Chip Desbloqueado",
        "atributos": ["Dual chip: Sim", "Desbloqueado: Sim"],
        "variacoes": [{"nome": "Preto", "disponivel": True, "confiavel": True}],
        "variacoes_completas": True,
    }
]
URANYX_512 = [
    {
        "item_id": "5",
        "titulo": "Celular Uranyx F110 Pro 5G 8.256",
        "variacoes": [
            {"nome": "Preto 8GB+256GB", "disponivel": True, "confiavel": True},
            {"nome": "Azul 12GB+512GB", "disponivel": True, "confiavel": True},
        ],
        "variacoes_completas": True,
    }
]
KIA = [
    {
        "item_id": "1",
        "titulo": "Celular Uranyx S5 A18 Pro Max",
        "variacoes": [
            {"nome": "Branco NFC", "disponivel": True, "confiavel": True},
            {"nome": "Laranja NFC", "disponivel": True, "confiavel": True},
        ],
        "variacoes_completas": True,
    }
]

# Do conjunto dev do eval (a resposta que a nota deu como enviável) e da mesma família.
# 147a255a2c13: a raiz era o nome da variação com pontuação (ver o teste próprio).
ESTOQUE_147A = (
    "estoque_variacao",
    "Quando vai ter branco ou cor feminina",
    "Olá! No anúncio, as opções listadas hoje são Preto, 44mm e Preto (Tecido), 44mm, e as duas estão esgotadas. Não temos previsão de reposição e não há outra cor, como branco, listada neste anúncio no momento.",
    RELOGIO_44,
)
FALSOS_POSITIVOS = [
    (
        "estoque_variacao",
        "não há mais o macbook neo 8.256GB por 4.469,36?",
        "Olá! No momento, todas as cores desse anúncio (Rose, Amarelo, Azul e Prata) estão esgotadas, e não temos previsão de reposição.",
        MACBOOK,
    ),
    (
        "opiniao_recomendacao",
        "Olá esse celular presta",
        "Olá! Não temos como opinar sobre a qualidade do aparelho, mas o anúncio traz as informações técnicas: o modelo tem dual chip e vem desbloqueado. Para te orientar melhor, conte o que pretende usar no celular.",
        URANYX,
    ),
    (
        "troca_devolucao",
        "comprei o modelo errado, quero o de 512",
        "Olá! A troca é feita pela devolução do próprio pedido, em Minhas compras. A versão de 512 GB de armazenamento aparece nas opções do anúncio.",
        URANYX_512,
    ),
    (
        "acessorios_avulsos",
        "vocês vendem capinha pra ele?",
        "Olá! Este anúncio é só do celular F110 Pro 5G; a capinha não é vendida neste anúncio.",
        URANYX_512,
    ),
]


@pytest.mark.parametrize(
    ("assunto", "cliente", "texto", "fichas"),
    FALSOS_POSITIVOS,
    ids=[f"{a}-{i}" for i, (a, *_r) in enumerate(FALSOS_POSITIVOS)],
)
async def test_3_a_ficha_no_automatico_nao_dispara_fora_dos_assuntos_de_produto(
    assunto, cliente, texto, fichas
):
    # O sintoma: a régua pelo texto, sem o assunto (como era), segurava.
    assert checagens.conferir_ficha_no_automatico(texto, fichas, textos_cliente=[cliente])
    assert (
        checagens.conferir_ficha_no_automatico(
            texto, fichas, textos_cliente=[cliente], assunto=assunto
        )
        == []
    )
    auto = await _completo(texto, categoria=assunto, modo=MODO_AUTO, cliente=cliente, fichas=fichas)
    assert ia.MOTIVO_FICHA_NO_AUTOMATICO not in auto.motivos, auto.motivos
    assert ia.MOTIVO_FICHA_NO_AUTOMATICO not in auto.sem_auto


async def test_3_a_resposta_de_estoque_do_eval_sai_sozinha():
    """147a255a2c13 (dev): a nota deu enviável; só a ficha no automático segurava."""
    assunto, cliente, texto, fichas = ESTOQUE_147A
    auto = await _completo(
        texto,
        categoria=assunto,
        modo=MODO_AUTO,
        cliente=cliente,
        fichas=fichas,
        fatos={"ficha_do_anuncio": fichas},  # o pacote põe as fichas nos fatos
    )
    assert _sai(auto), (auto.motivos, auto.sem_auto)


def test_3_o_nome_da_variacao_com_pontuacao_nao_e_caracteristica():
    """A raiz do 147a: "Preto (Tecido), 44mm" dava a palavra "(tecido)," e a
    comparação com a palavra limpa ("tecido") falhava — em qualquer assunto."""
    texto = ESTOQUE_147A[2]
    assert "tecido" in checagens._opcoes_das_fichas(RELOGIO_44)
    assert not checagens.afirma_caracteristica(texto, RELOGIO_44)
    assert checagens.conferir_ficha_no_automatico(texto, RELOGIO_44, assunto="outro") == []


@pytest.mark.parametrize(
    ("assunto", "cliente", "texto", "fichas"),
    [
        # Os assuntos de dado do produto (spec 6.2 e a decisão do dono): fica.
        (
            "espec_outros",
            "42 ou 46mm?",
            "O anúncio tem o relógio de 44mm com a tela Retina.",
            RELOGIO_44,
        ),
        (
            "originalidade_condicao",
            "é original?",
            "Conforme o título, este Apple Watch é original e lacrado.",
            RELOGIO_44,
        ),
        ("resistencia_agua", "pode molhar?", "Pode sim, ele é resistente à água.", RELOGIO_44),
        # O `outro`, onde a pergunta de característica se esconde.
        ("outro", "o cadeado é TSA?", "Sim, o cadeado é TSA.", []),
        ("outro", "o celular é rápido?", "Olá! Sim, é super rápido!", KIA),
        # A PERGUNTA de característica respondida com a característica, em qualquer assunto.
        (
            "estoque_variacao",
            "a versão branca tem nfc?",
            "Olá! Tem sim, a opção Branco NFC é a versão com NFC.",
            KIA,
        ),
        (
            "confirmar_variacao",
            "a versão branca tem nfc?",
            "Olá! Tem sim, a opção Branco NFC é a versão com NFC.",
            KIA,
        ),
        (
            "opiniao_recomendacao",
            "é bom pra jogos? roda free fire?",
            "Roda sim, ele roda Free Fire sem travar.",
            KIA,
        ),
    ],
)
async def test_3_a_ficha_no_automatico_continua_onde_vale(assunto, cliente, texto, fichas):
    auto = await _completo(texto, categoria=assunto, modo=MODO_AUTO, cliente=cliente, fichas=fichas)
    assert not _sai(auto)
    assert ia.MOTIVO_FICHA_NO_AUTOMATICO in auto.sem_auto, auto.sem_auto


PRETA = [
    {
        "item_id": "1",
        "titulo": "Celular X",
        "variacoes": [
            {"nome": "Preta", "disponivel": True, "confiavel": True},
            {"nome": "Azul", "disponivel": True, "confiavel": True},
        ],
        "variacoes_completas": True,
    }
]


@pytest.mark.parametrize(
    ("texto", "termo"),
    [
        # O exemplo da revisão 2 (a característica escondida no estoque).
        ("Temos sim a cor preta disponível, e a tela é AMOLED.", "amoled"),
        ("Temos sim a cor preta disponível, e o processador é octa-core.", "octa-core"),
        ("Temos sim a cor preta disponível, e ele roda Free Fire sem travar.", "free fire"),
        ("Temos sim a preta, e o carregador vem na caixa.", "carregador"),
    ],
)
async def test_3_a_caracteristica_que_o_anuncio_nao_traz_continua_pega(texto, termo):
    """Fora dos assuntos de produto, a característica que ninguém perguntou e que
    o dado não traz não sai sozinha (regra suave própria, não a da ficha)."""
    auto = await _completo(
        texto,
        categoria="estoque_variacao",
        modo=MODO_AUTO,
        cliente="tem preta?",
        fichas=PRETA,
        fatos={"ficha_do_anuncio": PRETA},
    )
    assert not _sai(auto) and auto.status == RASCUNHO_PENDENTE
    motivo = next(m for m in auto.sem_auto if m.startswith("afirma característica que os dados"))
    assert termo in motivo and ia.MOTIVO_FICHA_NO_AUTOMATICO not in auto.sem_auto
    # No copiloto: limpa (a régua é do automático, como a da ficha).
    copiloto = await _completo(
        texto,
        categoria="estoque_variacao",
        modo=MODO_COPILOTO,
        cliente="tem preta?",
        fichas=PRETA,
        fatos={"ficha_do_anuncio": PRETA},
    )
    assert not any(m.startswith("afirma característica que os dados") for m in copiloto.sem_auto)


def test_3_a_caracteristica_que_o_anuncio_traz_passa():
    assert checagens.caracteristicas_fora_do_dado(FALSOS_POSITIVOS[1][2], URANYX) == []
    # Pelo que a ferramenta devolveu (`ctx.registro.textos`) também.
    texto = "Temos sim a preta, e a tela é AMOLED."
    assert checagens.caracteristicas_fora_do_dado(texto, PRETA) == ["amoled"]
    assert (
        checagens.caracteristicas_fora_do_dado(
            texto, PRETA, dados_extra=['{"descricao":"Tela AMOLED de 6,7 polegadas"}']
        )
        == []
    )
    # ff45583ea14e (dev): só o NOME da característica ("variam conforme a versão")
    # vale quando o dado o traz (as especificações do anúncio).
    variam = "A memória RAM e o armazenamento variam conforme a versão escolhida."
    com_espec = [
        {
            **PRETA[0],
            "especificacoes": {
                "campos": [
                    {"campo": "ram", "valor": "8 GB", "fonte": "anuncio"},
                    {"campo": "armazenamento", "valor": "256 GB", "fonte": "anuncio"},
                ]
            },
        }
    ]
    assert checagens.caracteristicas_fora_do_dado(variam, com_espec) == []
    assert checagens.caracteristicas_fora_do_dado(variam, PRETA) != []


# ═══════════════ 4. os geradores do Tool Runner ═══════════════


@contextlib.contextmanager
def _geradores_do_runner():
    """Os geradores assíncronos do `_beta_runner.py` do SDK que começaram aqui."""
    vistos: list[Any] = []
    primeira, fim = sys.get_asyncgen_hooks()

    def _primeira(gerador: Any) -> None:
        if gerador.ag_code.co_filename.endswith("_beta_runner.py"):
            vistos.append(gerador)
        if primeira is not None:
            primeira(gerador)

    sys.set_asyncgen_hooks(firstiter=_primeira, finalizer=fim)
    try:
        yield vistos
    finally:
        sys.set_asyncgen_hooks(firstiter=primeira, finalizer=fim)


def _todos_fechados(vistos: list[Any]) -> bool:
    nomes = {g.ag_code.co_name for g in vistos}
    # O do `async for`, o `__run__` e o `_handle_request` (o `asynccontextmanager`).
    assert {"__aiter__", "__run__", "_handle_request"} <= nomes, nomes
    return all(inspect.getasyncgenstate(g) == inspect.AGEN_CLOSED for g in vistos)


async def test_4_o_fim_fecha_os_geradores_do_runner(pecas):
    with _geradores_do_runner() as vistos:
        r, api, _ = await _rodar(pecas, [_resposta([_ferramenta()], "tool_use"), _final_do_laco()])
    assert r.parada == "fim" and len(api.pedidos) == 2
    assert _todos_fechados(vistos)


async def test_4_o_max_turnos_fecha_sem_rodar_a_ferramenta(pecas):
    respostas = [_resposta([_ferramenta(item_id=str(i))], "tool_use") for i in range(2)]
    with _geradores_do_runner() as vistos:
        r, api, _ = await _rodar(pecas, respostas, extra={"max_turnos": 2})
    assert r.parada == "max_iteracoes" and len(api.pedidos) == 2
    # Fechar não roda a ferramenta do último turno (o comportamento de antes).
    assert [a["item_id"] for _n, a in pecas.chamadas_ferramenta] == ["0"]
    assert _todos_fechados(vistos)


async def test_4_o_prazo_fecha_sem_rodar_a_ferramenta(pecas, monkeypatch):
    from app.services.atendimento import agente

    relogio = _Relogio(passo=50.0)
    monkeypatch.setattr(agente, "_relogio", relogio)
    api = _Api([_resposta([_ferramenta()], "tool_use")], pecas.eventos)
    api.ao_pedir = relogio.andar
    with _geradores_do_runner() as vistos:
        r, _, _ = await _rodar(pecas, [], api=api)
    assert r.parada == "prazo" and pecas.chamadas_ferramenta == []
    assert _todos_fechados(vistos)


async def test_4_o_teto_no_meio_fecha_os_geradores(pecas, monkeypatch):
    monkeypatch.setattr(get_settings(), "atendimento_agente_teto_usd_conversa", 0.0001)
    caro = _resposta([_ferramenta()], "tool_use", uso={"input_tokens": 5000})
    with _geradores_do_runner() as vistos:
        r, api, _ = await _rodar(pecas, [caro, _final_do_laco()])
    assert r.parada == "teto" and len(api.pedidos) == 1 and pecas.chamadas_ferramenta == []
    assert _todos_fechados(vistos)


async def test_4_o_erro_do_provedor_no_meio_tambem_fecha(pecas):
    falha = httpx2.Response(529, json={"type": "error", "error": {"type": "overloaded_error"}})
    with _geradores_do_runner() as vistos:
        r, _, _ = await _rodar(pecas, [_resposta([_ferramenta()], "tool_use"), falha])
    assert r.parada == "erro_provedor"
    assert all(inspect.getasyncgenstate(g) == inspect.AGEN_CLOSED for g in vistos)


async def test_4_fechar_nunca_derruba_a_resposta():
    from app.services.atendimento import agente

    class _Quebrado:
        async def aclose(self) -> None:
            raise RuntimeError("generator didn't stop after athrow()")

    fechados: list[str] = []

    class _Bom:
        async def aclose(self) -> None:
            fechados.append("bom")

    runner = SimpleNamespace(_iterator=_Bom())
    await agente.fechar_runner(runner, _Quebrado())
    assert fechados == ["bom"]
    await agente.fechar_runner(SimpleNamespace(), None)  # sem os geradores: nada


def test_4_nada_sobra_para_o_fim_do_loop(pecas):
    """O log real: "an error occurred during closing of asynchronous generator
    <BaseAsyncToolRunner.__run__>" + "generator didn't stop after athrow()",
    do `loop.shutdown_asyncgens()` no fim do `asyncio.run` do eval (Python 3.12,
    o da imagem; no 3.14 o mesmo gerador sobrava sem o erro). Um loop próprio,
    como o do eval: ao fechar, nenhum gerador do runner está vivo."""
    erros: list[dict] = []
    vivos: list[str] = []

    async def _eval() -> None:
        asyncio.get_running_loop().set_exception_handler(lambda _loop, c: erros.append(c))
        for _ in range(3):
            await _rodar(pecas, [_resposta([_ferramenta()], "tool_use"), _final_do_laco()])

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_eval())
        vivos = [
            g.ag_code.co_name
            for g in list(getattr(loop, "_asyncgens", ()))
            if g.ag_code.co_filename.endswith("_beta_runner.py")
            and inspect.getasyncgenstate(g) != inspect.AGEN_CLOSED
        ]
        loop.run_until_complete(loop.shutdown_asyncgens())
    finally:
        loop.close()
    assert vivos == []
    assert not [c for c in erros if "asynchronous generator" in str(c.get("message"))], erros


# ═══════════════ 5. o caminho pelo próprio app ═══════════════


@pytest.mark.parametrize(
    "texto",
    [
        "A troca pode ser solicitada e acompanhada pela loja pelo próprio app da Shopee.",
        "O atendimento é feito pela loja pelo próprio app da Shopee.",
        "A troca é acompanhada pela loja dentro do app da Shopee.",
        "A compra deve ser feita na loja através do app da Shopee.",
        "Pode falar com a loja pelo chat da Shopee.",
        # O pedido de retirada (o eval: "comprar e receber só pela plataforma").
        "Não fazemos retirada na loja: a compra e o recebimento são feitos só pelo próprio app da Shopee.",
        "Não trabalhamos com retirada na loja; a compra e a entrega são pelo app da Shopee.",
        "Não é possível retirar na loja: a compra e a entrega são feitas pelo app da Shopee.",
        "A compra e a entrega são feitas somente pelo app da Shopee, sem retirada na loja.",
        "A retirada na loja não está disponível; a compra e a entrega são pelo app da Shopee.",
        "Não temos loja física: a compra e a entrega são pelo próprio app da Shopee.",
        "Não é possível vir buscar o produto: a compra e a entrega são pelo app da Shopee.",
    ],
)
def test_5_o_proprio_app_da_plataforma_nao_e_caminho_para_fora(texto):
    assert checagens.conferir_caminho_da_plataforma(texto, "shopee") == []


@pytest.mark.parametrize(
    "texto",
    [
        "Procure a loja Uranyx Oficial na Shopee.",
        "Procure a loja Uranyx pelo app da Shopee.",
        "Temos outra loja Mega Eletronicos na Shopee.",
        "Pode retirar na nossa loja amanhã.",
        "Pode vir buscar na loja.",
        "Não tem problema retirar na loja.",
        "Não se preocupe, pode retirar na nossa loja.",
        "Não temos loja física, mas pode vir buscar no nosso galpão.",
        "Não oferecemos retirada na loja, mas pode visitar nossa loja.",
    ],
)
def test_5_o_caminho_para_fora_continua_bloqueado(texto):
    (p,) = checagens.conferir_caminho_da_plataforma(texto, "shopee")
    assert p.codigo == checagens.CAMINHO_FORA_DA_PLATAFORMA and p.bloqueia


@pytest.mark.parametrize(
    "texto",
    [
        "A compra é feita direto com a loja pelo próprio app da Shopee.",
        "Você pode comprar direto com a loja pelo próprio app da Shopee.",
        "Pode comprar direto conosco aqui na Shopee.",
        "Não fazemos venda por fora: a compra é feita pela loja pelo próprio app da Shopee.",
        "Não vendemos fora da plataforma; a compra é só pelo app da Shopee.",
    ],
)
async def test_5_negociacao_por_fora_so_aparente_continua_no_agente(texto):
    """Revisão do r5i (21/10/2026): a isenção do r5a não tinha efeito de ponta a
    ponta — o envio (`enviar._preparar_texto`) revalida e recusa para toda
    origem. O agente fica com o motivo, como o legado; o prompt manda escrever
    "a compra, o pagamento e a entrega são só pelo app da plataforma"."""
    legado, agente = await _as_duas_conferencias(texto)
    assert validador.MOTIVO_POR_FORA in legado.erros and legado.precisa_humano
    assert validador.MOTIVO_POR_FORA in agente.erros, agente.erros


@pytest.mark.parametrize(
    "texto",
    [
        "Compra direto comigo que sai mais barato.",
        "Pode pagar por fora no pix que fica mais barato.",
        "A gente vende direto com a loja fora da plataforma.",
        "Pode comprar direto com a loja, por fora sai mais barato.",
        "Pode comprar direto com a loja.",
    ],
)
async def test_5_a_negociacao_por_fora_de_verdade_continua_no_agente(texto):
    _legado, agente = await _as_duas_conferencias(texto)
    assert validador.MOTIVO_POR_FORA in agente.erros


async def _as_duas_conferencias(texto: str) -> tuple[ia.SaidaConferida, ia.SaidaConferida]:
    conversa, kw = _chamada(
        _base_conferir(texto=texto, categoria="outro", **{"textos_cliente": ["posso retirar aí?"]})
    )
    legado = await ia.conferir_saida(None, conversa, SimpleNamespace(), **kw)
    agente = await ia.conferir_saida(None, conversa, SimpleNamespace(), **kw, agente=True)
    return legado, agente


async def test_5_a_resposta_da_retirada_sai_sozinha_no_automatico():
    texto = "Olá! Não fazemos retirada na loja: a compra e o recebimento são feitos só pelo próprio app da Shopee."
    auto = await _completo(
        texto, categoria="outro", modo=MODO_AUTO, cliente="posso retirar aí na loja?"
    )
    assert auto.status == RASCUNHO_PENDENTE and not auto.erros, (auto.motivos, auto.erros)
    assert not any("fora da plataforma" in m for m in auto.sem_auto), auto.sem_auto


# ═══════════════ 6. a lacuna sem dado no rascunho ═══════════════


def _saida(texto: str, **kw: Any) -> ia.SaidaConferida:
    base = {
        "texto": texto,
        "categoria": "rastreio",
        "precisa_humano": True,
        "motivos": [
            "sem dado para {rastreio}",
            "validador: lacuna não preenchida ({rastreio}); valor em R$ só pode vir do sistema",
        ],
        "erros": ["lacuna não preenchida ({rastreio})", "valor em R$ só pode vir do sistema"],
        "status": RASCUNHO_PENDENTE,
    }
    base.update(kw)
    return ia.SaidaConferida(**base)


def test_6_a_frase_da_lacuna_sem_dado_sai_do_rascunho():
    s = checagens.sem_lacuna_vazia(
        _saida(
            "Olá! Seu pedido foi enviado pela {transportadora} e o código de rastreio é "
            "{rastreio}. Você acompanha a entrega em Minhas Compras, no próprio pedido."
        )
    )
    assert s.texto == "Olá! Você acompanha a entrega em Minhas Compras, no próprio pedido."
    assert "{" not in s.texto and s.status == RASCUNHO_PENDENTE and s.precisa_humano
    motivo = checagens.MOTIVO_LACUNA_TIRADA.format(lacunas="{transportadora}, {rastreio}")
    assert motivo in s.motivos and motivo in s.sem_auto
    # O buraco saiu: o "lacuna não preenchida" do validador também; o resto fica.
    assert s.erros == ["valor em R$ só pode vir do sistema"]
    assert "validador: valor em R$ só pode vir do sistema" in s.motivos
    assert "sem dado para {rastreio}" in s.motivos


@pytest.mark.parametrize(
    "texto",
    [
        "Olá! O código de rastreio do seu pedido é {rastreio}.",
        "Olá! A nota fiscal do pedido é a {nf_numero}. Qualquer dúvida, estamos à disposição!",
        "{previsao_entrega}",
    ],
)
def test_6_sem_resposta_no_resto_fica_bloqueado_e_sem_texto(texto):
    s = checagens.sem_lacuna_vazia(_saida(texto))
    assert s.texto == "" and s.status == RASCUNHO_BLOQUEADO and s.precisa_humano
    (nome,) = checagens.lacunas_no_texto(texto)
    motivo = checagens.MOTIVO_LACUNA_SEM_RESPOSTA.format(lacunas="{" + nome + "}")
    assert motivo in s.motivos and motivo in s.erros and motivo in s.sem_auto


def test_6_sem_lacuna_nada_muda():
    s = _saida("Olá! Você acompanha a entrega em Minhas Compras.")
    assert checagens.sem_lacuna_vazia(s) is s


@pytest.mark.parametrize("modo", [MODO_COPILOTO, MODO_AUTO])
async def test_6_o_rascunho_do_agente_nunca_sai_com_a_lacuna(
    db: AsyncSession, make_user, agente_ligado, api, envios, modo
):
    """Pelo `ia.gerar_rascunho` de verdade: o pedido tem rastreio e não tem NF."""
    await _importar_manual_base(db)
    await _pedido_completo(db)
    conversa = await _conversa(db, make_user)
    await _ligar(db, conversa, modo=modo, auto_categorias=["rastreio"])
    await _msg(db, conversa, "cadê meu pedido? e a nota?")
    api.roteiro = [
        _final(
            texto="Olá! Seu pedido já foi enviado; o rastreio é {rastreio}. A nota fiscal é a {nf_numero}.",
            assunto="rastreio",
            evidencias=["pedido"],
        )
    ]
    r = await ia.gerar_rascunho(db, conversa)
    assert r.texto == f"Olá! Seu pedido já foi enviado; o rastreio é {RASTREIO}.", r.texto
    assert "{" not in r.texto and r.status == RASCUNHO_PENDENTE and r.precisa_humano
    assert checagens.MOTIVO_LACUNA_TIRADA.format(lacunas="{nf_numero}") in (r.motivo or "")
    assert envios == []  # pessoa confere (o motivo "sem dado" do legado continua)
    assert checagens.MOTIVO_LACUNA_TIRADA.format(lacunas="{nf_numero}") in _do_agente(r)["sem_auto"]


async def test_6_so_a_lacuna_sem_dado_vira_bloqueado_sem_texto(
    db: AsyncSession, make_user, agente_ligado, api, envios
):
    await _importar_manual_base(db)
    await _pedido_completo(db)
    conversa = await _conversa(db, make_user)
    await _ligar(db, conversa, modo=MODO_COPILOTO)
    await _msg(db, conversa, "qual o número da nota?")
    api.roteiro = [
        _final(
            texto="Olá! A nota fiscal do seu pedido é a {nf_numero}.",
            assunto="nota_fiscal",
            evidencias=["pedido"],
        )
    ]
    r = await ia.gerar_rascunho(db, conversa)
    assert r.texto is None and r.status == RASCUNHO_BLOQUEADO and r.precisa_humano
    assert checagens.MOTIVO_LACUNA_SEM_RESPOSTA.format(lacunas="{nf_numero}") in (r.motivo or "")
    assert envios == []
