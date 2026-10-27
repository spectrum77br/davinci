"""As CHECAGENS do agente por cima da `ia.conferir_saida` (spec 6.5, 20/10/2026).

- variações: a calibração com ERROS PLANTADOS — as 19 frases que a revisão
  de 20/10/2026 viu passar (`scratchpad/agente/sondas/sonda1.py`) e mais as
  que a camada conservadora pega (plural, diminutivo e acento da cor, o
  estoque dito com outras palavras, o "só temos", o "todas as cores", a cor
  PARECIDA e a cor que a conferência não conhece); as respostas certas ao
  lado não podem ser pegas;
- a fonte da característica do produto (NFC, RAM, 5G, o que acompanha): a
  ficha, as especificações por fonte, o fato aprovado — o valor do site que
  o anúncio contradiz bloqueia, o anúncio que se contradiz vai para pessoa;
- evidência citada sem prova, promessa da equipe (com a exceção do pedido
  de evidência), outra plataforma ou fabricante, a guarda do "perguntar",
  a devolução/chamado em andamento, as ações de pessoa;
- `aplicar`: bloqueia → erros e BLOQUEADO; pessoa → motivo; `so_auto` só no
  automático; SUBSTITUIDO só vira BLOQUEADO;
- o caso real da Kia ("Vc tem o bordo" / "E fone vem junto") pela
  `ia.conferir_saida(agente=True)` de verdade e as checagens por cima; e o
  caso robertasoares831 (produto desconhecido: afirmar característica sem
  fonte vai para pessoa).
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from app.services.atendimento import constantes, ia
from app.services.atendimento import especificacoes as espec
from app.services.atendimento import manual as manual_svc
from app.services.atendimento.agente import checagens
from app.services.atendimento.agente import contexto as agente_ctx
from app.services.atendimento.agente.saida import Proposta
from app.services.atendimento.ia import SaidaConferida

# ─────────────── as fichas ───────────────

TITULO_A18 = "Uranyx A18 Pro Max NFC 16.128 Gb Memoria S5 Premium"
ITEM_A18 = "58265493964"
DESCRICAO_A18 = (
    "Uranyx S5 + Fone UFB10\nMemória interna de 128 GB\nTela 6.9 HD+ 120 Hz\nNFC - Sim\n"
    "Fone UFB10\nANC: sim"
)
PERGUNTA_BORDO = "Vc tem o bordo"
PERGUNTA_FONE = "E fone vem junto"


def _v(nome: str, disponivel: bool | None = True, confiavel: bool = True) -> dict:
    return {"nome": nome, "disponivel": disponivel, "confiavel": confiavel, "fonte": "loja"}


KIA = [_v("A18, Branco NFC"), _v("A18, Laranja NFC")]
KIA_ESG = [KIA[0], _v("A18, Laranja NFC", disponivel=False)]
VINHO = [_v("Vinho"), _v("Preto")]
S7 = [_v("Dourado, 8.128 GB", disponivel=False), _v("Dourado, 12.256 GB")]
BORDO_INCERTO = [_v("Bordô", confiavel=False), _v("Preto")]


def _f(variacoes: list[dict] | None, *, completas: bool = True, titulo: str = TITULO_A18) -> list:
    ficha: dict[str, Any] = {"item_id": ITEM_A18, "titulo": titulo, "descricao": DESCRICAO_A18}
    if variacoes is not None:
        ficha["variacoes"] = variacoes
        ficha["variacoes_completas"] = completas
    return [ficha]


DUAS_1A_INCOMPLETA = [
    {"titulo": TITULO_A18, "variacoes": KIA, "variacoes_completas": False},
    {"titulo": "Outro", "variacoes": KIA, "variacoes_completas": True},
]
DUAS_BRANCO = [
    {
        "titulo": "Capa X",
        "variacoes": [_v("Branco", disponivel=False), _v("Preto")],
        "variacoes_completas": True,
    },
    {"titulo": "Capa Y", "variacoes": [_v("Branco"), _v("Azul")], "variacoes_completas": True},
]
B = PERGUNTA_BORDO

# ─────────────── variações: a calibração ───────────────

# (resposta, fichas, o que o cliente escreveu, contradiz o dado?) — toda resposta
# aqui está ERRADA e tem de ser pega (rodada 4: a contradição vai para pessoa nos
# dois modos; antes, bloqueava).
REVISAO_19 = [
    ("Temos sim! A cor bordô é linda.", _f(KIA), [B], True),
    ("Sim, o bordô está com estoque.", _f(KIA), [B], True),
    ("O bordô a gente tem sim!", _f(KIA), [B], True),
    ("Sim, ainda há unidades disponíveis!", _f(KIA), [B], True),
    ("O laranja não temos mais.", _f(KIA), [B], True),
    ("Temos sim! O laranja é o mais vendido.", _f(KIA_ESG), ["tem laranja?"], True),
    ("Temos sim! O laranja é o mais vendido.", _f(KIA_ESG), ["quais cores tem?"], True),
    ("Temos o dourado 8.128 disponível!", _f(S7, titulo="S7"), ["tem dourado?"], True),
    (
        "No momento não temos a opção de 128 GB. As disponíveis são Branco e Laranja.",
        _f(KIA),
        [B],
        True,
    ),
    ("No momento não temos o bordô.", DUAS_1A_INCOMPLETA, [B], False),
    # Dois anúncios e a resposta não diz de qual: pessoa, SEM bloqueio (revisão 2
    # de 20/10/2026 — a "Capa Y" tem o branco: a resposta pode estar certa).
    ("Temos o branco disponível!", DUAS_BRANCO, ["tem branco?"], False),
    ("O branco tá disponível e o laranja esgotado.", _f(KIA), ["tem branco ou laranja?"], True),
    ("Temos o branco, o laranja não temos.", _f(KIA), ["tem branco ou laranja?"], True),
    ("O laranja esgotou.", _f(KIA), ["tem laranja?"], True),
    ("Bordô temos sim!", _f(KIA), [B], True),
    ("Temos sim, pode comprar!", _f(KIA), [B], True),
    ("Temos o de 256 GB!", _f(KIA), ["tem de 256?"], False),
    ("No momento não temos 220V.", _f(KIA), ["tem 220v?"], False),
    ("Não há o bordô.", _f(KIA, completas=False), [B], False),
]
# As que a camada conservadora pega (antes passavam pela conferência).
CAMADA_CONSERVADORA = [
    # Plural, diminutivo e acento da cor.
    ("Temos sim os bordôs", _f(KIA), [B], True),
    ("Temos o bordozinho sim", _f(KIA), [B], True),
    ("Laranjinha temos!", _f(KIA_ESG), ["tem laranja?"], True),
    # O estoque com outras palavras.
    ("Ainda há unidades.", _f(KIA), [B], True),
    ("O bordô e o fone vêm sim.", _f(KIA), [B], True),
    ("A cor bordô está entre as opções.", _f(KIA), [B], True),
    ("O branco zerou.", _f(KIA), ["tem branco?"], True),
    ("A cor laranja saiu de linha.", _f(KIA), ["tem laranja?"], True),
    ("Laranja não vem mais.", _f(KIA), ["tem laranja?"], True),
    ("Não trabalhamos mais com o laranja.", _f(KIA), ["tem laranja?"], True),
    ("O laranja voltou ao estoque.", _f(KIA_ESG), ["tem laranja?"], True),
    # "Só temos X" com outra opção disponível.
    ("No momento só o branco.", _f(KIA), ["tem laranja?"], True),
    ("Só temos o branco.", _f(KIA), ["tem laranja?"], True),
    ("Apenas o branco está disponível.", _f(KIA), ["tem laranja?"], True),
    # "Todas as cores" com uma esgotada.
    ("Temos as duas cores disponíveis.", _f(KIA_ESG), ["quais cores?"], True),
    ("Ambas as cores estão disponíveis.", _f(KIA_ESG), ["quais cores?"], True),
    ("Todas as opções estão disponíveis.", _f(KIA_ESG), ["quais cores?"], True),
    # A cor parecida (bordô × vinho): na dúvida, pessoa.
    ("Temos sim, o vinho está disponível.", _f(VINHO), [B], False),
    ("Não temos o bordô, mas o vinho está disponível.", _f(VINHO), [B], False),
    ("No momento não temos o bordô.", _f(VINHO), [B], False),
    # Bordô na lista, mas sem estoque confiável.
    ("Temos sim! A cor bordô é linda.", _f(BORDO_INCERTO), [B], False),
    # A cor que a conferência não conhece, fora das variações.
    ("Temos o marinho disponível!", _f(KIA), ["quais cores?"], True),
    ("Temos sim na cor gelo.", _f(KIA, completas=False), ["tem gelo?"], False),
    # Sem a lista nenhuma.
    ("Temos sim na cor bordô.", _f(None), [B], False),
    # O "Sim!" deduzido: pessoa.
    ("Sim!", _f(KIA), [B], False),
    ("Tem sim, bordô!", _f(KIA), [B], True),
]
CERTAS = [
    (
        "Olá! No momento não temos na cor bordô: as opções deste anúncio são Branco NFC e "
        "Laranja NFC, as duas disponíveis.",
        _f(KIA),
        [B],
    ),
    ("Olá! No momento não temos na cor bordo!", _f(KIA), [B, PERGUNTA_FONE]),
    ("Temos apenas Branco e Laranja; o bordô não temos.", _f(KIA), [B]),
    ("Temos o branco disponível!", _f(KIA), ["tem branco?"]),
    ("Temos as duas cores disponíveis.", _f(KIA), ["quais cores?"]),
    ("É só selecionar a cor laranja no anúncio.", _f(KIA), ["tem laranja?"]),
    ("O laranja está esgotado no momento, mas o branco temos sim.", _f(KIA_ESG), ["tem laranja?"]),
    ("Só o branco está disponível; o laranja esgotou.", _f(KIA_ESG), ["quais cores?"]),
    ("Seu pedido foi enviado há 2 dias, na cor branca.", _f(KIA), ["e meu pedido?"]),
    ("Temos sim, o vinho está disponível.", _f(VINHO), ["tem vinho?"]),
    ("Você prefere o branco ou o laranja?", _f(KIA), ["tem branco?"]),
    ("Confira no anúncio se o branco está disponível.", _f(KIA), ["tem branco?"]),
    ("Não temos como confirmar a cor bordô por aqui.", _f(KIA), [B]),
    (
        "Temos o dourado 12.256 disponível; o 8.128 está esgotado.",
        _f(S7, titulo="S7"),
        ["dourado?"],
    ),
    ("Temos a cor laranjinha disponível!", _f(KIA), ["tem laranja?"]),
]


def test_a_calibracao_tem_mais_de_25_frases_e_as_19_da_revisao():
    assert len(REVISAO_19) == 19
    assert len(REVISAO_19) + len(CAMADA_CONSERVADORA) >= 25
    assert len({r for r, *_ in REVISAO_19 + CAMADA_CONSERVADORA}) >= 40


def _pessoa_nos_dois_modos(p: checagens.PendenciaAgente) -> bool:
    """Rodada 4 (21/10/2026), o contrato novo: a contradição com o dado (a cor
    esgotada dada como disponível, a que não está na lista completa) vai para
    PESSOA nos dois modos, sem bloquear; a falta do dado é a regra suave."""
    return not p.bloqueia and not p.so_auto


# A 4ª coluna (antes "bloqueia") é a contradição com o dado: pessoa nos dois
# modos; sem ela, a regra suave. Nada daqui bloqueia (rodada 4).
@pytest.mark.parametrize(("resposta", "fichas", "cliente", "contradiz"), REVISAO_19)
def test_as_19_da_revisao_sao_pegas(resposta, fichas, cliente, contradiz):
    pendencias = checagens.conferir_variacoes(resposta, fichas, cliente)
    assert pendencias, resposta
    assert not any(p.bloqueia for p in pendencias), pendencias
    assert any(_pessoa_nos_dois_modos(p) for p in pendencias) is contradiz, pendencias
    assert {p.codigo for p in pendencias} <= {
        checagens.VARIACAO_CONTRADIZ,
        checagens.VARIACAO_SEM_DADO,
    }
    assert all(
        _pessoa_nos_dois_modos(p) == (p.codigo == checagens.VARIACAO_CONTRADIZ) for p in pendencias
    )


@pytest.mark.parametrize(("resposta", "fichas", "cliente", "contradiz"), CAMADA_CONSERVADORA)
def test_a_camada_conservadora_pega(resposta, fichas, cliente, contradiz):
    pendencias = checagens.conferir_variacoes(resposta, fichas, cliente)
    assert pendencias, resposta
    assert not any(p.bloqueia for p in pendencias), pendencias
    assert any(_pessoa_nos_dois_modos(p) for p in pendencias) is contradiz, pendencias


@pytest.mark.parametrize(("resposta", "fichas", "cliente"), CERTAS)
def test_as_respostas_certas_passam(resposta, fichas, cliente):
    assert checagens.conferir_variacoes(resposta, fichas, cliente) == []


def test_motivos_das_variacoes_dizem_o_que_contradiz():
    (p,) = checagens.conferir_variacoes("Temos sim os bordôs", _f(KIA), [B])
    # Rodada 4: a contradição é pessoa nos dois modos (sem bloquear).
    assert p.motivo == "diz que tem 'bordo', que não está nas variações"
    assert _pessoa_nos_dois_modos(p)
    (p,) = checagens.conferir_variacoes("Só temos o branco.", _f(KIA), ["tem laranja?"])
    assert p.motivo == "diz que só tem 'branco', mas 'A18, Laranja NFC' está disponível"
    (p,) = checagens.conferir_variacoes("Temos sim, o vinho está disponível.", _f(VINHO), [B])
    assert p.motivo == (
        "o cliente pediu a cor 'bordo' e o anúncio tem 'Vinho' — confira se é a mesma cor"
    )
    assert checagens.conferir_variacoes("", _f(KIA), [B]) == []


# ─────────────── a fonte da característica do produto ───────────────


def _ficha_kia(site_ram: str | None = None) -> list[dict]:
    ficha = {"item_id": ITEM_A18, "titulo": TITULO_A18, "descricao": DESCRICAO_A18}
    do_site = (
        {"ram": [espec.Achado("ram", site_ram, frozenset({site_ram.split()[0]}))]}
        if site_ram
        else {}
    )
    ficha["especificacoes"] = espec.juntar(espec.do_anuncio(ficha, TITULO_A18), {}, do_site)
    return [ficha]


def _fonte(texto: str, fichas: list[dict], ctx: Any = None, **kw: Any) -> list[tuple[str, bool]]:
    """(motivo, a contradição com o dado). Rodada 4 (21/10/2026), o contrato novo:
    a característica que o anúncio contradiz vai para PESSOA nos dois modos (o
    True, antes "bloqueia"); sem fonte é a regra suave (False). Nada bloqueia."""
    pendencias = checagens.conferir_fonte_produto(texto, fichas, ctx, **kw)
    assert not any(p.bloqueia for p in pendencias), pendencias
    return [(p.motivo, not p.so_auto) for p in pendencias]


def test_fonte_kia_nfc_ram_e_o_fone_que_vem():
    fichas = _ficha_kia(site_ram="8 GB")
    assert _fonte("Sim, ele tem NFC!", fichas) == []
    assert _fonte("Sim, o fone UFB10 acompanha o aparelho.", fichas) == []
    assert _fonte("Ele tem 16 GB de RAM e 128 GB de memória interna.", fichas) == []
    assert _fonte("Ele tem 8 GB de RAM + 8 GB de RAM virtual.", fichas) == []
    assert _fonte("Não, ele não tem NFC.", fichas) == [
        ("diz o NFC 'não', mas o anúncio diz 'sim'", True)
    ]
    # O site diz 8 GB; o anúncio, 16: o valor do site não vale.
    assert _fonte("Ele tem 8 GB de RAM.", fichas) == [
        ("usa o valor de site para a RAM (8 GB), que o anúncio contradiz (16 GB)", True)
    ]
    assert _fonte("Ele tem 12 GB de RAM.", fichas) == [
        ("diz a RAM '12 GB', mas o anúncio diz '16 GB'", True)
    ]
    assert _fonte("O fone não acompanha.", fichas) == [
        ("diz que o fone não acompanha, mas o anúncio o cita", True)
    ]
    # Sem fonte: pessoa (dado indisponível).
    assert _fonte("Acompanha carregador sim.", fichas) == [
        ("diz que acompanha carregador sem fonte (ficha ou fato)", False)
    ]
    assert _fonte("Ele é 5G.", fichas) == [
        ("afirma o 5G ('sim') sem fonte (anúncio, especificações ou fato)", False)
    ]
    # Pergunta, ressalva e orientação não afirmam.
    for neutra in (
        "Ele tem NFC?",
        "Confira no anúncio se ele tem NFC.",
        "Não temos essa informação sobre o 5G.",
        "",
    ):
        assert _fonte(neutra, fichas) == [], neutra


def test_fonte_o_anuncio_que_se_contradiz_vai_para_pessoa():
    ficha = {"titulo": "Celular X NFC", "atributos": ["NFC: Não"]}
    ficha["especificacoes"] = espec.juntar(espec.do_anuncio(ficha, ficha["titulo"]), {}, {})
    assert ficha["especificacoes"]["conflito_no_anuncio"] is True
    (p,) = checagens.conferir_fonte_produto("Ele tem NFC sim.", [ficha])
    assert p.codigo == checagens.ANUNCIO_SE_CONTRADIZ and not p.bloqueia
    assert "corrigir anúncio" in p.motivo


def test_fonte_pelo_que_as_ferramentas_devolveram_e_nunca_pela_regra_do_manual():
    ctx = agente_ctx.ContextoAgente.da_conversa(
        SimpleNamespace(id=uuid4(), plataforma="shopee", canal="chat", pedido_marketplace=None),
        None,
        modo=constantes.MODO_COPILOTO,
    )
    # A regra do manual e as respostas prontas falam de NFC — não são fonte.
    manual = {
        "assunto": "espec_celular",
        "regra": {"quando": "perguntam de NFC", "faca": "diga que tem NFC"},
        "respostas_prontas": [{"id": "modelo:1", "texto": "Tem NFC sim!"}],
        "fatos": [],
    }
    ctx.permitir(ids=["regra:espec_celular"], textos=[json.dumps(manual, ensure_ascii=False)])
    assert _fonte("Sim, tem NFC.", [], ctx)[0][1] is False  # sem fonte: pessoa
    # O fato aprovado é fonte.
    fato = {"fatos": [{"id": "fato:4", "texto": "O Uranyx S5 tem NFC.", "escopo": "marca"}]}
    ctx.permitir(ids=["fato:4"], textos=[json.dumps(fato, ensure_ascii=False)])
    assert _fonte("Sim, tem NFC.", [], ctx) == []
    # O `ver_anuncio` com as especificações (o 5G "não" do anúncio).
    anuncio = {
        "item_id": "1",
        "titulo": "Celular Y 4G",
        "especificacoes": {
            "campos": [{"campo": "rede_5g", "valor": "não", "fonte": "anuncio"}],
            "conflitos": [],
            "conflito_no_anuncio": False,
        },
    }
    ctx.permitir(ids=["ficha:1"], textos=[json.dumps(anuncio, ensure_ascii=False)])
    assert _fonte("Ele é 5G sim.", [], ctx) == [("diz o 5G 'sim', mas o anúncio diz 'não'", True)]


def test_fonte_o_que_acompanha_na_oracao_do_objeto():
    fichas = [
        {
            "titulo": "Celular X",
            "descricao": "Celular X + Fone UFB10\nCabo USB-C incluso\nBateria sem vício",
        }
    ]
    # "Não vem com carregador, só o cabo": o cabo VEM (e a ficha o cita).
    assert _fonte("Ele não vem com carregador, só o cabo.", fichas) == [
        ("diz que não acompanha carregador sem fonte (ficha ou fato)", False)
    ]
    assert _fonte("O fone acompanha sem custo.", fichas) == []
    assert _fonte("O cabo vem junto e o fone também.", fichas) == []
    assert _fonte("Vem sem fone.", fichas) == [
        ("diz que o fone não acompanha, mas o anúncio o cita", True)
    ]
    # A ficha que nega ("Não acompanha carregador", "Sem fone de ouvido").
    sem = [{"titulo": "Celular Y", "descricao": "Não acompanha carregador. Sem fone de ouvido."}]
    assert _fonte("Não acompanha carregador.", sem) == []
    assert _fonte("Acompanha carregador.", sem) == [
        ("diz que acompanha carregador, mas o anúncio diz que não", True)
    ]


def test_variacoes_nao_pega_o_que_nao_e_estoque():
    malas = [
        {
            "titulo": "Conjunto de malas 3 peças",
            "variacoes": [_v("Azul"), _v("Preto")],
            "variacoes_completas": True,
        }
    ]
    cliente = ["tem na cor azul? quantas peças?"]
    for texto in (
        "O conjunto tem 3 peças: P, M e G.",
        "O conjunto tem 3 peças e temos na cor azul.",
        "A embalagem é creme, bem bonita.",
        "Seu pedido já está a caminho.",
    ):
        assert checagens.conferir_variacoes(texto, malas, cliente) == [], texto
    # "Azul marinho" pedido e "Azul" no anúncio: pessoa confere.
    (p,) = checagens.conferir_variacoes("Temos sim na cor azul.", malas, ["tem azul marinho?"])
    assert not p.bloqueia and "'marinho'" in p.motivo


def test_fonte_robertasoares831_sem_produto_nao_afirma():
    """Sem ficha nenhuma (o produto desconhecido): afirmar vai para pessoa."""
    assert _fonte("Sim, ele vem com carregador e tem NFC.", []) == [
        ("afirma o NFC ('sim') sem fonte (anúncio, especificações ou fato)", False),
        ("diz que acompanha carregador sem fonte (ficha ou fato)", False),
    ]
    # A ressalva tira só a oração dela: o NFC continua afirmado.
    assert _fonte("Sim, ele tem NFC, e se for o caso posso explicar.", []) == [
        ("afirma o NFC ('sim') sem fonte (anúncio, especificações ou fato)", False)
    ]
    # O título do anúncio da conversa (fontes_extra) já é fonte.
    assert _fonte("Sim, tem NFC.", [], fontes_extra=["Celular Z NFC 8.256"]) == []


# ─────────────── evidência, promessa, caminho, pergunta, andamento ───────────────


def _ctx(**kw: Any) -> agente_ctx.ContextoAgente:
    conversa = SimpleNamespace(
        id=uuid4(), plataforma=kw.pop("plataforma", "shopee"), canal="chat", pedido_marketplace=None
    )
    return agente_ctx.ContextoAgente.da_conversa(conversa, None, modo=constantes.MODO_COPILOTO)


def test_evidencia_citada_sem_prova_so_vale_no_automatico():
    ctx = _ctx()
    ctx.permitir(ids=["pedido", f"ficha:{ITEM_A18}"])
    assert checagens.conferir_evidencias(["pedido", f"ficha:{ITEM_A18}"], ctx) == []
    (p,) = checagens.conferir_evidencias(["pedido", "fato:99", "par:3", "fato:99"], ctx)
    assert p.codigo == checagens.EVIDENCIA_SEM_PROVA and p.so_auto and not p.bloqueia
    assert p.motivo == "evidência citada sem prova (fato:99, par:3)"


def test_promessa_de_equipe_e_a_excecao_do_pedido_de_evidencia():
    def _p(texto: str, acao: str = "responder", assunto: str = "outro") -> list[str]:
        return [
            p.motivo
            for p in checagens.conferir_promessa_de_equipe(
                texto, acao=acao, acoes_pessoa=[], assunto=assunto
            )
        ]

    assert _p("Vamos verificar com a transportadora e retornaremos.") == [
        'promete ação da equipe numa orientação ("vamos verificar")'
    ]
    assert _p("Verificaremos o seu pedido.") != []
    assert _p("A nossa equipe vai analisar e te responde.") != []
    assert _p("Já encaminhei para o setor responsável.") != []
    # Pessoa/pergunta: a promessa não é orientação.
    assert _p("Vamos verificar.", acao="pessoa") == []
    assert _p("Vamos verificar?", acao="perguntar") == []
    # Negada não é promessa; nem a plataforma ou a transportadora como sujeito
    # (a orientação de reembolso da spec 6.4), nem o fato passado.
    assert _p("Infelizmente não vamos conseguir alterar o pedido.") == []
    assert _p("A Shopee vai analisar o reembolso e devolver pela mesma forma de pagamento.") == []
    assert _p("A transportadora vai entrar em contato para agendar a entrega.") == []
    assert _p("A loja já enviou o seu pedido.") == []
    assert _p("Vou te enviar o código assim que sair.") != []
    # A orientação de defeito (spec 6.4): pede o vídeo e diz que a equipe analisa.
    defeito = (
        "Sentimos muito! Envie por aqui um vídeo curto mostrando o defeito e fotos; a nossa "
        "equipe vai analisar e responder por aqui."
    )
    assert _p(defeito, assunto="defeito") == []
    assert _p(defeito, assunto="item_errado_faltando") == []
    assert _p(defeito, assunto="rastreio") != []
    assert _p("A nossa equipe vai analisar e responder por aqui.", assunto="defeito") != []


@pytest.mark.parametrize(
    ("texto", "plataforma", "pego"),
    [
        ("Abra a devolução pelo Mercado Livre.", "shopee", "cita outra plataforma (Mercado Livre)"),
        ("Compre pela Amazon que chega antes.", "shopee", "cita outra plataforma (Amazon)"),
        ("Acompanhe pelo app da Shopee.", "ml", "cita outra plataforma (Shopee)"),
        ("Procure a assistência técnica autorizada.", "shopee", "manda para fora da plataforma"),
        ("Entre em contato com o fabricante.", "tiktok", "manda para fora da plataforma"),
        ("Acompanhe pelo app da Shopee, em Minhas compras.", "shopee", None),
        ("O frasco tem 500 ml.", "shopee", None),
        ("Entregamos no Amazonas também.", "shopee", None),
        ("O reembolso é feito pelo Mercado Pago, no app do Mercado Livre.", "ml", None),
    ],
)
def test_caminho_da_plataforma(texto, plataforma, pego):
    pendencias = checagens.conferir_caminho_da_plataforma(texto, plataforma)
    if pego is None:
        assert pendencias == []
    else:
        (p,) = pendencias
        assert p.bloqueia and p.codigo == checagens.CAMINHO_FORA_DA_PLATAFORMA
        assert p.motivo.startswith(pego)


def _m(autor: str, texto: str, minutos: int) -> SimpleNamespace:
    from datetime import UTC, datetime, timedelta

    quando = datetime(2026, 10, 20, 15, tzinfo=UTC) - timedelta(minutes=minutos)
    return SimpleNamespace(
        id=uuid4(),
        autor=autor,
        texto=texto,
        tipo="texto",
        origem=constantes.ORIGEM_CLIENTE if autor == "cliente" else "externo",
        payload={},
        anexos=[],
        status=constantes.MSG_RECEBIDA,
        enviada_em=quando,
        created_at=quando,
    )


def test_guarda_do_perguntar_uma_por_tema():
    recentes = [
        _m("cliente", "oi, quero saber do meu pedido", 30),
        _m("loja", "Olá! Qual é o número do pedido?", 20),
        _m("cliente", "é aquele que comprei semana passada", 5),
    ]
    gatilho = recentes[-1]
    (p,) = checagens.conferir_pergunta(
        "perguntar", recentes, gatilho, texto="Pode me informar o número do pedido?"
    )
    assert p.codigo == checagens.PERGUNTA_REPETIDA and not p.bloqueia
    # Tema diferente (o pedido × a cor): passa.
    assert (
        checagens.conferir_pergunta(
            "perguntar", recentes, gatilho, texto="Qual a cor que você quer?"
        )
        == []
    )
    # Tema que não dá para saber conta como o mesmo.
    assert checagens.conferir_pergunta("perguntar", recentes, gatilho, texto="Como assim?") != []
    # Responder não é perguntar.
    assert checagens.conferir_pergunta("responder", recentes, gatilho, texto="x") == []
    # A pergunta de cortesia ("algo mais?") não é de esclarecimento.
    cortesia = [*recentes[:1], _m("loja", "Pronto! Posso ajudar em algo mais?", 20), recentes[-1]]
    assert checagens.conferir_pergunta("perguntar", cortesia, gatilho, texto="Qual?") == []
    # Sem fala da loja antes: a 1ª pergunta passa.
    assert checagens.conferir_pergunta("perguntar", [recentes[0]], recentes[0], texto="Qual?") == []


def test_devolucao_ou_chamado_em_andamento_nunca_sai_sozinho():
    (p,) = checagens.conferir_andamento({"devolucoes": [{"status": "em andamento"}]})
    assert p.codigo == checagens.EM_ANDAMENTO and not p.bloqueia and not p.so_auto
    assert checagens.conferir_andamento({"chamados": [{"status": "aberto"}]}) != []
    assert checagens.conferir_andamento({}, etiquetas=[constantes.ETIQUETA_DEVOLUCAO]) != []
    # O legado já mandou ("pedido com chamado ou devolução"): não repete.
    assert (
        checagens.conferir_andamento(
            {"chamados": [{"status": "aberto"}]}, motivos=["pedido com chamado ou devolução"]
        )
        == []
    )
    assert checagens.conferir_andamento({}) == []


def test_acao_de_pessoa_na_ordem_da_regua():
    lista = checagens.conferir_acao_de_pessoa(["segurar_envio"], ["excecao", "ag_cancelamento"])
    assert [p.motivo for p in lista] == [
        "ação de pessoa: Pedido em Ag. cancelamento",
        "ação de pessoa: Segurar o envio (cancelamento antes da postagem)",
        "ação de pessoa: Exceção à política",
    ]
    assert all(not p.bloqueia and not p.so_auto for p in lista)


# ─────────────── aplicar ───────────────


def _conferida(**kw: Any) -> SaidaConferida:
    base = {
        "texto": "Olá! Seu pedido segue normalmente.",
        "categoria": "outro",
        "precisa_humano": False,
        "motivos": [],
        "erros": [],
        "status": constantes.RASCUNHO_PENDENTE,
    }
    base.update(kw)
    return SaidaConferida(**base)


def test_aplicar_sem_nada_devolve_a_mesma():
    c = _conferida()
    assert checagens.aplicar(c, checagens.Checagens(), acoes_pessoa=[], modo="auto") is c


def test_aplicar_acoes_bloqueio_so_auto_e_substituido():
    c = _conferida(motivo_modelo="ação de pessoa: Exceção à política — cliente quer outra cor")
    chk = checagens.Checagens(
        [
            checagens.PendenciaAgente("variacao_contradiz", "diz que tem 'bordo'", True, False),
            checagens.PendenciaAgente(
                "evidencia_sem_prova", "evidência sem prova (x)", False, True
            ),
            checagens.PendenciaAgente("promessa_de_equipe", "promete ação", False, False),
        ]
    )
    final = checagens.aplicar(
        c, chk, acoes_pessoa=["segurar_envio", "excecao"], modo=constantes.MODO_COPILOTO
    )
    assert final.precisa_humano is True and final.status == constantes.RASCUNHO_BLOQUEADO
    # A ação que o motivo do modelo já diz não se repete.
    assert final.motivos == [
        "ação de pessoa: Segurar o envio (cancelamento antes da postagem)",
        "diz que tem 'bordo'",
        "promete ação",
    ]
    assert final.erros == ["diz que tem 'bordo'"] and not final.validador_ok
    assert final.avisos_auto == ["evidência sem prova (x)"]
    assert final.motivo.endswith("— cliente quer outra cor")
    # No automático (o portão, 27/10/2026), o que não é duro é AVISO ao verificador.
    auto = checagens.aplicar(c, chk, acoes_pessoa=[], modo=constantes.MODO_AUTO)
    assert "evidência sem prova (x)" in auto.avisos and auto.avisos_auto == []
    assert "evidência sem prova (x)" not in auto.motivos
    # SUBSTITUIDO (a loja respondeu) só muda para BLOQUEADO.
    sub = _conferida(status=constantes.RASCUNHO_SUBSTITUIDO)
    so_pessoa = checagens.Checagens([checagens.PendenciaAgente("x", "pessoa", False, False)])
    assert (
        checagens.aplicar(sub, so_pessoa, acoes_pessoa=[], modo="auto").status
        == constantes.RASCUNHO_SUBSTITUIDO
    )
    assert (
        checagens.aplicar(sub, chk, acoes_pessoa=[], modo="auto").status
        == constantes.RASCUNHO_BLOQUEADO
    )


# ─────────────── o caso da Kia de ponta a ponta ───────────────


def _proposta(texto: str, **kw: Any) -> Proposta:
    base = {
        "acao": "responder",
        "texto": texto,
        "assunto": "outro",
        "confianca": 0.9,
        "acao_pessoa": None,
        "motivo_pessoa": "",
        "evidencias": [f"ficha:{ITEM_A18}", f"variacoes:{ITEM_A18}"],
    }
    base.update(kw)
    return Proposta(**base)


def _base_kia(textos: list[str]) -> agente_ctx.BaseDoGerar:
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
        modo=constantes.MODO_COPILOTO,
        forcar=True,
        nf_politica_aprovada=False,
        insistiu=False,
    )


async def _kia(texto: str) -> tuple[SaidaConferida, checagens.Checagens]:
    conversa = SimpleNamespace(
        id=uuid4(),
        plataforma="shopee",
        canal="chat",
        dados={},
        pedido_marketplace=None,
        anuncio_id=ITEM_A18,
    )
    fichas = _f(KIA)
    fichas[0]["especificacoes"] = espec.juntar(espec.do_anuncio(fichas[0], TITULO_A18), {}, {})
    textos = [PERGUNTA_BORDO, PERGUNTA_FONE]
    base = _base_kia(textos)
    ctx = agente_ctx.ContextoAgente.da_conversa(conversa, None, modo=constantes.MODO_COPILOTO)
    ctx.permitir(
        ids=[f"ficha:{ITEM_A18}", f"variacoes:{ITEM_A18}"],
        textos=[json.dumps(fichas, ensure_ascii=False)],
    )
    p = _proposta(texto)
    from app.services.atendimento.agente import saida

    conferida = await ia.conferir_saida(
        None,
        conversa,
        SimpleNamespace(),
        **saida.para_conferir(p, base.ids_categorias),
        motivos=base.motivos,
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
        modo=constantes.MODO_COPILOTO,
        forcar=True,
        agente=True,
    )
    chk = checagens.conferir(p, conferida, ctx, base, fichas=fichas, gatilho=None)
    return checagens.aplicar(conferida, chk, acoes_pessoa=ctx.acoes_pessoa, modo=base.modo), chk


async def test_kia_a_resposta_certa_passa_e_a_errada_vai_para_pessoa():
    certa, chk = await _kia(
        "Olá! No momento não temos na cor bordô. Sim, o fone UFB10 acompanha o aparelho."
    )
    assert chk.pendencias == []
    assert certa.status == constantes.RASCUNHO_PENDENTE and certa.validador_ok
    assert certa.precisa_humano is False

    # Rodada 4 (21/10/2026), o contrato novo: a contradição com a ficha vai para
    # PESSOA nos dois modos — a sugestão fica (pendente, com o texto), sem bloquear.
    errada, chk = await _kia("Temos sim! A cor bordô é linda. O fone não vem junto.")
    assert errada.status == constantes.RASCUNHO_PENDENTE and errada.erros == []
    assert "diz que tem 'bordo', que não está nas variações" in errada.motivos
    assert "diz que o fone não acompanha, mas o anúncio o cita" in errada.motivos
    assert errada.precisa_humano is True and errada.sem_auto


async def test_kia_promessa_e_outra_plataforma_por_cima_da_conferencia():
    final, chk = await _kia(
        "Vamos verificar com o estoque e retornaremos. Se preferir, compre pelo Mercado Livre."
    )
    codigos = {p.codigo for p in chk.pendencias}
    assert {checagens.PROMESSA_DE_EQUIPE, checagens.CAMINHO_FORA_DA_PLATAFORMA} <= codigos
    assert final.status == constantes.RASCUNHO_BLOQUEADO and final.precisa_humano
