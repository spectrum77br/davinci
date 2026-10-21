"""Os ajustes do 1º dia AO VIVO na Kia e do eval cego do TikTok (21/10/2026, dono aprovou).

Cada item com o rascunho REAL que deu errado (os textos de produção), e o que
fica igual ao lado. Só o caminho do AGENTE muda; o legado é o de sempre (os
`test_k_*` do e2e e o `test_atendimento_conferir_saida`).

1. (fontes v1.3, item 10) a condição do produto pelo ANÚNCIO da conversa:
   título, atributo de condição da plataforma ("Condição: Novo") ou descrição;
   o anúncio que se contradiz e a fala da loja que contradiz o anúncio → pessoa.
2. "Quando essa opção voltar ao estoque, ela aparece de novo para seleção no
   próprio anúncio" não é promessa de estoque (1d6f708e).
3. O "Ok Aguardando" / "Obrigada" depois de a equipe tratar ("vamos abrir um
   chamado") não pede rascunho (15da122e) — o e2e prova o DESCARTADO.
5. "Neste anúncio não há capa à venda…" (8d62a0e7): (a) o "não há" é da capa,
   não da cor laranja do aparelho; (b) o fato aprovado citado (fato:11, "as de
   outro modelo não servem") é fonte de "não servem no…".
6. "evidência citada sem prova (regra:rastreio)" (610dcd76): o pacote escreve
   os ids que prova; o prompt manda citar só o que veio.
7. (eval do TikTok) "Minhas compras" numa conversa do TikTok → regra suave; o
   manual chega ao modelo com o caminho do TikTok Shop.
8. (eval do TikTok) "consta entregue" + "não chegou"/"não recebi" → abrir_chamado
   em toda plataforma, também quando a entrega vem da situação do Bling.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from app.services.atendimento import condicao_anuncio, constantes, evidencias, ia, variacoes
from app.services.atendimento import manual as manual_svc
from app.services.atendimento.agente import acao_pessoa, checagens, prompt
from app.services.atendimento.agente import contexto as agente_ctx
from app.services.atendimento.agente.saida import Proposta
from tests.test_atendimento_agente_pacote import _cliente, _codigos, _conv, _gat, _loja

# ═══════════════ os dados dos casos reais ═══════════════

# Kia, Shopee: o anúncio do A18 (os títulos das variações de produção).
ITEM_A18 = "58265493964"
TITULO_A18 = "Uranyx A18 Pro Max NFC 16.128 Gb Memoria S5 Premium"
DESCRICAO_A18 = "Uranyx S5 + Fone UFB10\nMemória interna de 128 GB\nAcompanha case de brinde"


def _v(nome: str, disponivel: bool = True) -> dict:
    return {"nome": nome, "disponivel": disponivel, "confiavel": True, "fonte": "loja"}


VARIACOES_A18 = [
    _v("A18, Branco NFC"),
    _v("A18, Laranja NFC"),
    _v("C17, Azul"),
    _v("C17, Laranja"),
    _v("C17, Prata"),
]


def _ficha_a18(**kw: Any) -> list[dict]:
    ficha = {
        "de_onde": "anúncio ligado à conversa",
        "item_id": ITEM_A18,
        "titulo": TITULO_A18,
        "descricao": DESCRICAO_A18,
        "variacoes": VARIACOES_A18,
        "variacoes_completas": True,
    }
    ficha.update(kw)
    return [ficha]


# 8d62a0e7 (Kia, 21/10 14:30): a pergunta e o rascunho reais.
PERGUNTA_CAPA = (
    "Boa tarde tudo bem ? Vcs tem a capa pro Uranyx A 18 Pró Max na Cor Laranja? De "
    "preferência com símbolo da Maçã ,comprei com vcs o celular e esqueci de comprar a capa que "
    "eu queria"
)
RASCUNHO_CAPA = (
    "Neste anúncio não há capa à venda: o case Uranyx acompanha o aparelho como brinde. Capas "
    "feitas para outro modelo, como as de iPhone, não servem no Uranyx A18 Pro Max, porque as "
    "medidas mudam; a compatível é a do próprio modelo, e peças avulsas só aparecem à venda "
    "quando há um anúncio próprio na loja."
)
EVIDENCIAS_CAPA = ["pedido", f"ficha:{ITEM_A18}", "fato:9", "fato:11", "regra:acessorios_avulsos"]
# Os fatos aprovados da semente (0420: `pecas_avulsas`, `capa_outro_modelo`).
FATO_9 = "Peças e acessórios avulsos só estão à venda quando há um anúncio próprio deles na loja."
FATO_11 = (
    "Capinha e película são feitas para as medidas de cada modelo; as de outro modelo não servem."
)

# 1d6f708e (Kia, 21/10 13:51): o rascunho real.
RASCUNHO_POTE = (
    "Olá! O tamanho 2,5 litros está esgotado no anúncio neste momento. Quando essa opção voltar "
    "ao estoque, ela aparece de novo para seleção no próprio anúncio."
)
TITULO_POTE = "Cesto Organizador 1,5 litros e 2,5 litros"
FICHA_POTE = [
    {
        "de_onde": "anúncio ligado à conversa",
        "item_id": "40123456789",
        "titulo": TITULO_POTE,
        "variacoes": [_v("1,5 litros"), _v("2,5 litros", disponivel=False)],
        "variacoes_completas": True,
    }
]

# 15da122e (Kia, 85176yln23, 21/10 13:55–13:56): a conversa real (sem o nome).
PEDIU_AJUDA = (
    "Boa tarde Tudo bem? Vi aqui que alteram o prazo de entrega Fiquei decepcionada Pq comprei "
    "para presentear meu filho E o aniversário dele é amanhã"
)
CITACAO_DA_LOJA = (
    "「Boa tarde Tudo bem? Vi aqui que alteram o prazo de entrega Fique…」 - - - - - - - - - - - "
    "- - - - boa tarde tudo bem?"
)
VAMOS_ABRIR = "vamos abri um chamado para te ajudar!"


def _fechamento(*cliente: str) -> list:
    return [
        _cliente(PEDIU_AJUDA, 64),
        _cliente("Poderia por favor Ver o que pode fazer para me ajudar", 63),
        _cliente("Boa tarde", 13),
        _loja(CITACAO_DA_LOJA, 1),
        _loja(VAMOS_ABRIR, 1),
        *(_cliente(t, 0) for t in cliente),
    ]


# 610dcd76 (Kia, 21/10 14:04): o rascunho real (o número do pedido vira a lacuna).
RASCUNHO_RASTREIO = (
    "Entendemos a importância da data para o aniversário do seu filho, mas não conseguimos "
    "antecipar a entrega, pois o pedido {numero_pedido} já foi postado. Você pode acompanhar o "
    "envio e a previsão em Minhas Compras > o pedido > Informações do envio."
)


def _ctx(plataforma: str = "shopee", modo: str = constantes.MODO_COPILOTO) -> Any:
    conversa = SimpleNamespace(
        id=uuid4(), plataforma=plataforma, canal="chat", pedido_marketplace=None
    )
    return agente_ctx.ContextoAgente.da_conversa(conversa, None, modo=modo)


def _proposta(texto: str, **kw: Any) -> Proposta:
    base = {
        "acao": "responder",
        "texto": texto,
        "assunto": "outro",
        "confianca": 0.9,
        "acao_pessoa": None,
        "motivo_pessoa": "",
        "evidencias": [f"ficha:{ITEM_A18}"],
    }
    base.update(kw)
    return Proposta(**base)


def _base(textos: list[str], recentes: list | None = None, **kw: Any) -> agente_ctx.BaseDoGerar:
    base = {
        "recentes": recentes or [],
        "rajada": [],
        "foco": [],
        "textos_cliente": textos,
        "textos_rajada": textos,
        "ctx": {"pedido": None, "logistica": None, "chamados": [], "devolucoes": []},
        "valores": {},
        "fatos": {"anuncio": None},
        "fatos_gravados": {},
        "categorias": manual_svc.categorias_padrao(),
        "regras": [],
        "sinais": [],
        "motivos": [],
        "opcoes_sensiveis": [],
        "sinais_do_cliente": [],
        "nomes": (),
        "modo": constantes.MODO_COPILOTO,
        "forcar": True,
        "nf_politica_aprovada": False,
        "insistiu": False,
    }
    base.update(kw)
    return agente_ctx.BaseDoGerar(**base)


async def _conferir(
    texto: str,
    fichas: list[dict],
    textos: list[str],
    *,
    recentes: list | None = None,
    modo: str = constantes.MODO_COPILOTO,
    ctx: Any = None,
    **kw: Any,
) -> tuple[Any, checagens.Checagens]:
    """A `ia.conferir_saida(agente=True)` de verdade e as checagens por cima."""
    from app.services.atendimento.agente import saida

    conversa = SimpleNamespace(
        id=uuid4(), plataforma="shopee", canal="chat", dados={}, pedido_marketplace=None
    )
    base = _base(textos, recentes, modo=modo)
    ctx = ctx or _ctx(modo=modo)
    ctx.permitir(
        ids=[f"ficha:{f['item_id']}" for f in fichas],
        textos=[json.dumps(fichas, ensure_ascii=False)],
    )
    p = _proposta(texto, **kw)
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
        modo=modo,
        forcar=True,
        agente=True,
    )
    chk = checagens.conferir(p, conferida, ctx, base, fichas=fichas, gatilho=None)
    return checagens.aplicar(conferida, chk, acoes_pessoa=ctx.acoes_pessoa, modo=modo), chk


# ═══════════════ 1. a condição pelo anúncio (fontes v1.3, item 10) ═══════════════

# paulovitor85 (o caso do dono): "Esse relógio é recondicionado?" → "É novo, não
# recondicionado" — o anúncio diz "Condição: Novo" (o atributo da Shopee).
PERGUNTA_RELOGIO = "Esse relógio é recondicionado?"
RESPOSTA_RELOGIO = "Olá! O relógio é novo, não é recondicionado."
ITEM_RELOGIO = "22111222333"
TITULO_RELOGIO = "Smartwatch W69 Ultra Tela 2.2 Bluetooth Chamadas"
RAW_RELOGIO = {
    "condition": "NEW",
    "attribute_list": [
        {
            "original_attribute_name": "Condição",
            "attribute_value_list": [{"original_value_name": "Novo"}],
        },
        {
            "original_attribute_name": "Marca",
            "attribute_value_list": [{"original_value_name": "W"}],
        },
    ],
}


def _ficha_relogio(condicao: dict | None, **kw: Any) -> list[dict]:
    ficha = {
        "de_onde": "anúncio ligado à conversa",
        "item_id": ITEM_RELOGIO,
        "titulo": TITULO_RELOGIO,
        "descricao": "Smartwatch com tela de 2,2 polegadas.",
    }
    if condicao:
        ficha["condicao"] = condicao
    ficha.update(kw)
    return [ficha]


def test_1_a_condicao_que_o_anuncio_declara_em_cada_fonte():
    # O atributo de condição da Shopee (e o `condition` do item).
    c = condicao_anuncio.condicao_do_anuncio(TITULO_RELOGIO, None, RAW_RELOGIO)
    assert c == {"novo": {"valor": True, "fontes": ["atributo"]}, "conflito_no_anuncio": False}
    # O ML (`ITEM_CONDITION`) e o `condition` sozinho.
    ml = {
        "attributes": [{"id": "ITEM_CONDITION", "name": "Condição do item", "value_name": "Novo"}]
    }
    assert condicao_anuncio.condicao_do_anuncio("Fone", None, ml)["novo"]["valor"] is True
    assert condicao_anuncio.condicao_do_anuncio("Fone", None, {"condition": "used"}) == {
        "novo": {"valor": False, "fontes": ["atributo"]},
        "conflito_no_anuncio": False,
    }
    # A descrição, só na forma de condição do produto.
    desc = "Produto novo e lacrado, 100% original. Não é recondicionado."
    c = condicao_anuncio.condicao_do_anuncio("Relógio", desc, {})
    assert c["novo"] == {"valor": True, "fontes": ["descricao"]}
    assert c["lacrado"]["valor"] is True and c["original"]["valor"] is True
    # O título (a leitura do legado) e as três juntas.
    c = condicao_anuncio.condicao_do_anuncio("Relógio Novo Lacrado", desc, RAW_RELOGIO)
    assert c["novo"] == {"valor": True, "fontes": ["titulo", "atributo", "descricao"]}
    # O que NÃO é condição do produto: "nova versão", "pode ser usado na água",
    # o "carregador original", a pergunta.
    for texto in (
        "Nova versão com novo design e nova geração de sensores.",
        "Pode ser usado na água e usado para corrida.",
        "Acompanha carregador original e caixa original.",
        "É novo? Pergunte no chat.",
    ):
        assert condicao_anuncio.condicao_do_anuncio("Relógio", texto, {}) is None, texto


def test_1_o_anuncio_que_se_contradiz_e_conflito():
    c = condicao_anuncio.condicao_do_anuncio("Smartwatch W69 Seminovo", None, RAW_RELOGIO)
    assert c["novo"] == {"valor": "conflito", "fontes": ["titulo", "atributo"]}
    assert c["conflito_no_anuncio"] is True
    c = condicao_anuncio.condicao_do_anuncio(
        "Relógio", "Produto novo. Aparelho recondicionado com garantia.", {}
    )
    assert c["novo"]["valor"] == "conflito"


def test_1_o_que_um_texto_afirma_da_condicao():
    assert condicao_anuncio.afirmacoes(RESPOSTA_RELOGIO) == {"novo": True}
    assert condicao_anuncio.afirmacoes("Não, ele não é recondicionado.") == {"novo": True}
    assert condicao_anuncio.afirmacoes("É recondicionado, em ótimo estado.") == {"novo": False}
    assert condicao_anuncio.afirmacoes("Vem lacrado e é original.") == {
        "lacrado": True,
        "original": True,
    }
    # A pergunta e a ressalva não afirmam.
    assert condicao_anuncio.afirmacoes("Ele é novo?") == {}
    assert condicao_anuncio.afirmacoes("Não conseguimos confirmar se é novo.") == {}


@pytest.mark.parametrize("modo", [constantes.MODO_COPILOTO, constantes.MODO_AUTO])
async def test_1_paulovitor85_o_novo_pelo_atributo_passa(modo):
    condicao = condicao_anuncio.condicao_do_anuncio(TITULO_RELOGIO, None, RAW_RELOGIO)
    final, chk = await _conferir(
        RESPOSTA_RELOGIO,
        _ficha_relogio(condicao),
        [PERGUNTA_RELOGIO],
        modo=modo,
        assunto="originalidade_condicao",
        evidencias=[f"ficha:{ITEM_RELOGIO}"],
    )
    # Antes: "condição de novo sem confirmação no título do anúncio" (pessoa).
    assert not any("sem confirmação no título" in m for m in final.motivos), final.motivos
    assert not any(p.codigo.startswith("condicao") for p in chk.pendencias), chk.pendencias
    if modo == constantes.MODO_COPILOTO:
        assert final.precisa_humano is False and final.status == constantes.RASCUNHO_PENDENTE


async def test_1_sem_a_condicao_no_anuncio_a_trava_do_titulo_continua():
    final, _ = await _conferir(
        RESPOSTA_RELOGIO,
        _ficha_relogio(None),
        [PERGUNTA_RELOGIO],
        assunto="originalidade_condicao",
        evidencias=[f"ficha:{ITEM_RELOGIO}"],
    )
    assert "condição de novo sem confirmação no título do anúncio" in final.motivos
    assert final.precisa_humano is True


async def test_1_o_anuncio_que_se_contradiz_vai_para_pessoa_corrigir_anuncio():
    condicao = condicao_anuncio.condicao_do_anuncio("Smartwatch W69 Seminovo", None, RAW_RELOGIO)
    final, chk = await _conferir(
        RESPOSTA_RELOGIO,
        _ficha_relogio(condicao, titulo="Smartwatch W69 Seminovo"),
        [PERGUNTA_RELOGIO],
        assunto="originalidade_condicao",
        evidencias=[f"ficha:{ITEM_RELOGIO}"],
    )
    (p,) = [p for p in chk.pendencias if p.codigo == checagens.ANUNCIO_SE_CONTRADIZ]
    assert "corrigir anúncio" in p.motivo and not p.bloqueia and not p.so_auto
    assert final.precisa_humano is True


async def test_1_a_resposta_contra_o_anuncio_e_a_loja_contra_o_anuncio_vao_para_pessoa():
    condicao = condicao_anuncio.condicao_do_anuncio(TITULO_RELOGIO, None, RAW_RELOGIO)
    # A resposta diz o contrário do anúncio.
    final, chk = await _conferir(
        "Olá! Ele é recondicionado, em ótimo estado.",
        _ficha_relogio(condicao),
        [PERGUNTA_RELOGIO],
        assunto="originalidade_condicao",
        evidencias=[f"ficha:{ITEM_RELOGIO}"],
    )
    assert [p.codigo for p in chk.pendencias if p.codigo.startswith("condicao")] == [
        checagens.CONDICAO_CONTRADIZ
    ]
    assert final.precisa_humano is True
    # A loja disse na conversa o contrário do anúncio ("é seminovo"): o "novo" não sai.
    recentes = [
        _cliente("Ele é novo?", 30),
        _loja("Olá! Esse modelo é seminovo, revisado.", 29),
        _cliente(PERGUNTA_RELOGIO, 1),
    ]
    final, chk = await _conferir(
        RESPOSTA_RELOGIO,
        _ficha_relogio(condicao),
        [PERGUNTA_RELOGIO],
        recentes=recentes,
        assunto="originalidade_condicao",
        evidencias=[f"ficha:{ITEM_RELOGIO}"],
    )
    (p,) = [p for p in chk.pendencias if p.codigo == checagens.CONDICAO_CONTRA_A_LOJA]
    assert not p.bloqueia and not p.so_auto and final.precisa_humano is True


def test_1_so_o_anuncio_da_conversa_prova_e_o_legado_nao_muda():
    condicao = condicao_anuncio.condicao_do_anuncio(TITULO_RELOGIO, None, RAW_RELOGIO)
    # O mesmo produto em OUTRO anúncio (a ficha de outro anúncio) não prova.
    outro = _ficha_relogio(condicao, fonte="mesmo produto em outro anúncio (Mercado Livre)")
    assert checagens.condicao_provada(RESPOSTA_RELOGIO, outro) == frozenset()
    assert checagens.condicao_provada(RESPOSTA_RELOGIO, _ficha_relogio(condicao)) == {"novo"}
    # O que o `ver_anuncio` leu prova só o anúncio da conversa (`da_conversa`).
    lida = {"item_id": ITEM_RELOGIO, "titulo": TITULO_RELOGIO, "condicao": condicao}
    assert checagens.condicao_provada(RESPOSTA_RELOGIO, [{**lida, "da_conversa": False}]) == set()
    assert checagens.condicao_provada(RESPOSTA_RELOGIO, [{**lida, "da_conversa": True}]) == {"novo"}
    # O legado (`evidencias`) continua só com o título.
    fatos = {"anuncio": TITULO_RELOGIO, "ficha_do_anuncio": _ficha_relogio(condicao)}
    assert evidencias.motivos_sem_evidencia(RESPOSTA_RELOGIO, fatos) == [
        "condição de novo sem confirmação no título do anúncio"
    ]


# ═══════════════ 2. a volta ao estoque como condição (1d6f708e) ═══════════════


def test_2_o_rascunho_real_nao_e_promessa_de_estoque():
    plano = variacoes._normal(RASCUNHO_POTE)
    assert variacoes._promete_estoque(plano) is False
    pendencias = checagens.conferir_variacoes(
        RASCUNHO_POTE, FICHA_POTE, ["Quando volta o de 2,5 litros?"]
    )
    assert not any("promessa de estoque" in p.motivo for p in pendencias), pendencias


@pytest.mark.parametrize(
    "texto",
    [
        # A loja promete agir na oração principal: continua promessa.
        "Quando essa opção voltar ao estoque, avisamos você por aqui.",
        "Assim que chegar mais, te aviso.",
        "Se voltar ao estoque, a nossa equipe separa uma para você.",
        # A reposição afirmada (sem condição) e as de antes.
        "Essa opção vai voltar ao estoque na semana que vem.",
        "Vamos repor o laranja em breve.",
        "São as últimas unidades do branco!",
    ],
)
def test_2_a_promessa_de_verdade_continua(texto):
    assert variacoes._promete_estoque(variacoes._normal(texto)) is True, texto


# ═══════════════ 3. o "ok" depois de a loja tratar (15da122e) ═══════════════


def test_3_o_reconhecimento_depois_da_equipe_tratar():
    assert acao_pessoa.so_reconhecimento("Ok Aguardando")
    assert acao_pessoa.so_reconhecimento("Obrigada")
    assert acao_pessoa.so_reconhecimento("fico no aguardo, muito obrigado!")
    assert not acao_pessoa.so_reconhecimento("Ok, mas chega até amanhã?")
    assert not acao_pessoa.so_reconhecimento("sim")
    assert not acao_pessoa.so_reconhecimento("ok, vou devolver")
    recentes = _fechamento("Ok Aguardando", "Obrigada")
    assert acao_pessoa.turno_so_de_reconhecimento(recentes, recentes[-1])
    # Com pergunta nova, anexo, ou depois de uma PERGUNTA da loja: não.
    com_pergunta = _fechamento("Ok", "e chega até amanhã?")
    assert not acao_pessoa.turno_so_de_reconhecimento(com_pergunta, com_pergunta[-1])
    pergunta_da_loja = [
        _cliente(PEDIU_AJUDA, 30),
        _loja("Quer que a gente abra um chamado na Shopee?", 10),
        _cliente("Ok", 1),
    ]
    assert not acao_pessoa.turno_so_de_reconhecimento(pergunta_da_loja, pergunta_da_loja[-1])
    # Só o "em que podemos ajudar?" da loja não tratou nada.
    generica = [
        _cliente(PEDIU_AJUDA, 30),
        _loja("Olá! Em que podemos ajudar?", 10),
        _cliente("Ok", 1),
    ]
    assert not acao_pessoa.turno_so_de_reconhecimento(generica, generica[-1])
    # Sem resposta da loja: não.
    sozinho = [_cliente(PEDIU_AJUDA, 30), _cliente("Obrigada", 1)]
    assert not acao_pessoa.turno_so_de_reconhecimento(sozinho, sozinho[-1])


def test_3_o_prompt_nao_repete_orientacao_nem_a_acao_ja_prometida():
    geral = prompt.bloco_geral()
    assert "a loja já tratou o assunto e o cliente só agradece ou confirma" in geral
    assert (
        "Nunca repita orientação já dada nem peça de novo ação que a equipe já fez ou prometeu"
        in geral
    )


# ═══════════════ 5. a capa (8d62a0e7) ═══════════════


def test_5a_o_nao_ha_da_capa_nao_e_da_cor_do_aparelho():
    pendencias = checagens.conferir_variacoes(RASCUNHO_CAPA, _ficha_a18(), [PERGUNTA_CAPA])
    # Antes: "diz que não tem 'laranja', que está disponível" (bloqueava).
    assert not any("laranja" in p.motivo for p in pendencias), pendencias
    assert pendencias == []


@pytest.mark.parametrize(
    ("texto", "motivo"),
    [
        # O que continua pego: a cor negada, o "não temos" sem objeto, o "essa cor".
        ("Não temos o laranja.", "diz que não tem 'laranja', que está disponível"),
        ("Infelizmente não temos.", "diz que não tem 'laranja', que está disponível"),
        ("Não temos essa cor no momento.", "diz que não tem 'laranja', que está disponível"),
        ("O produto está esgotado.", "diz que não tem 'laranja', que está disponível"),
    ],
)
def test_5a_a_negacao_da_opcao_continua_pega(texto, motivo):
    pendencias = checagens.conferir_variacoes(texto, _ficha_a18(), ["tem o laranja?"])
    assert any(p.motivo == motivo for p in pendencias), (texto, pendencias)


def test_5a_o_sujeito_que_e_outra_coisa_tambem_sai():
    for texto in ("A capa está esgotada.", "Não temos carregador avulso."):
        pendencias = checagens.conferir_variacoes(texto, _ficha_a18(), [PERGUNTA_CAPA])
        assert not any("laranja" in p.motivo for p in pendencias), (texto, pendencias)


def test_5b_o_fato_aprovado_citado_e_fonte():
    dado = [json.dumps({"fatos": [{"texto": FATO_9}, {"texto": FATO_11}]}, ensure_ascii=False)]
    fora = checagens.caracteristicas_fora_do_dado(
        RASCUNHO_CAPA, _ficha_a18(), dados_extra=dado, nomes_de_produto=[TITULO_A18]
    )
    # Sem o fato como fonte (o de antes): "servem no" estava fora do dado.
    assert "servem no" in fora
    fora = checagens.caracteristicas_fora_do_dado(
        RASCUNHO_CAPA,
        _ficha_a18(),
        dados_extra=dado,
        nomes_de_produto=[TITULO_A18],
        fatos_citados=[FATO_9, FATO_11],
    )
    assert "servem no" not in fora, fora
    # A polaridade conta: o fato diz "não servem"; "servem no A18" afirma o contrário.
    fora = checagens.caracteristicas_fora_do_dado(
        "Capas feitas para outro modelo servem no Uranyx A18 Pro Max.",
        _ficha_a18(),
        nomes_de_produto=[TITULO_A18],
        fatos_citados=[FATO_11],
    )
    assert "servem no" in fora
    # E o assunto: o fato da capa não sustenta "serve no carro" do suporte.
    fora = checagens.caracteristicas_fora_do_dado(
        "O suporte não serve no painel do carro.",
        _ficha_a18(),
        nomes_de_produto=[TITULO_A18],
        fatos_citados=[FATO_11],
    )
    assert "serve no" in fora


def test_5b_so_o_fato_lido_e_citado_conta():
    ctx = _ctx()
    ctx.registrar_fatos([{"id": "fato:11", "texto": FATO_11}, {"id": "fato:9", "texto": FATO_9}])
    assert ctx.fatos_citados(EVIDENCIAS_CAPA) == [FATO_9, FATO_11]
    assert ctx.fatos_citados(["fato:11", "fato:99"]) == [FATO_11]
    assert ctx.fatos_citados(["pedido"]) == []
    # A 2ª passada do laço volta ao estado de antes das ferramentas.
    estado = ctx.estado_das_ferramentas()
    ctx.registrar_fatos([{"id": "fato:12", "texto": "outro"}])
    ctx.voltar_ferramentas(estado)
    assert set(ctx.fatos_lidos) == {"fato:11", "fato:9"}


async def test_5_o_rascunho_real_da_capa_passa_no_automatico():
    ctx = _ctx(modo=constantes.MODO_AUTO)
    ctx.registrar_fatos([{"id": "fato:9", "texto": FATO_9}, {"id": "fato:11", "texto": FATO_11}])
    ctx.permitir(
        ids=["pedido", "fato:9", "fato:11", "regra:acessorios_avulsos"],
        textos=[json.dumps({"fatos": [{"texto": FATO_9}, {"texto": FATO_11}]})],
    )
    final, chk = await _conferir(
        RASCUNHO_CAPA,
        _ficha_a18(),
        [PERGUNTA_CAPA],
        modo=constantes.MODO_AUTO,
        ctx=ctx,
        assunto="acessorios_avulsos",
        evidencias=EVIDENCIAS_CAPA,
    )
    codigos = [p.codigo for p in chk.pendencias]
    assert checagens.CARACTERISTICA_FORA_DO_ANUNCIO not in codigos, chk.pendencias
    assert not any(c.startswith("variacao") for c in codigos), chk.pendencias
    assert not any("laranja" in m or "servem" in m for m in final.motivos), final.motivos


# ═══════════════ 6. as evidências que o pacote prova (610dcd76) ═══════════════


def test_6_o_prompt_manda_citar_so_o_que_veio():
    saida = prompt.bloco_geral()
    assert "da linha EVIDÊNCIAS dos <dados>" in saida
    assert '"regra:<assunto>" só com o consultar_manual' in saida


def test_6_a_regra_sem_o_manual_continua_sem_prova():
    ctx = _ctx(modo=constantes.MODO_AUTO)
    ctx.permitir(ids=["pedido"])
    (p,) = checagens.conferir_evidencias(["pedido", "regra:rastreio"], ctx)
    assert p.motivo == "evidência citada sem prova (regra:rastreio)" and p.so_auto


# ═══════════════ 7. o caminho do app no TikTok ═══════════════

# O texto que o eval cego do TikTok viu (5 de 80): "Minhas compras" no TikTok.
NO_TIKTOK = "Olá! Você pode acompanhar o envio em Minhas compras > o pedido > Informações do envio."


def test_7_minhas_compras_no_tiktok_e_regra_suave():
    (p,) = checagens.conferir_caminho_da_plataforma(NO_TIKTOK, "tiktok")
    assert p.codigo == checagens.CAMINHO_DO_APP_ERRADO and p.so_auto and not p.bloqueia
    # Barbosa ao vivo (21/10/2026): o caminho certo já com a preposição.
    assert "nos pedidos do app do TikTok Shop > o pedido" in p.motivo
    # Na Shopee e no ML, é o caminho certo.
    assert checagens.conferir_caminho_da_plataforma(NO_TIKTOK, "shopee") == []
    assert checagens.conferir_caminho_da_plataforma(NO_TIKTOK, "ml") == []
    # O caminho do TikTok passa.
    certo = "Olá! Acompanhe o envio nos pedidos do app do TikTok Shop > o pedido."
    assert checagens.conferir_caminho_da_plataforma(certo, "tiktok") == []


def test_7_o_manual_e_o_bloco_do_tiktok_com_o_caminho_do_tiktok():
    orientacao = prompt.ORIENTACAO["cancelamento"]
    assert "Minhas compras" in orientacao
    no_tiktok = prompt.no_app_da_plataforma(orientacao, "tiktok")
    assert "Minhas compras" not in no_tiktok and "nos pedidos do app do TikTok Shop" in no_tiktok
    assert prompt.no_app_da_plataforma(orientacao, "shopee") == orientacao
    dados = {"regra": {"faca": "Acompanhe em Minhas Compras > o pedido > Informações do envio."}}
    assert prompt.no_app_da_plataforma(dados, "tiktok") == {
        "regra": {
            "faca": "Acompanhe nos pedidos do app do TikTok Shop > o pedido > detalhes do envio."
        }
    }
    tiktok = prompt.bloco_da_plataforma("tiktok", manual_svc.categorias_padrao())
    shopee = prompt.bloco_da_plataforma("shopee", manual_svc.categorias_padrao())
    assert 'No TikTok Shop não existe "Minhas compras"' in tiktok
    assert "não existe" not in shopee


# ═══════════════ 8. consta entregue e não chegou (eval do TikTok) ═══════════════

# O pedido do TikTok sem a linha da logística, entregue pela situação do Bling.
PEDIDO_ENTREGUE_BLING = {
    "pedido": {"numero": "90001", "numeroloja": "577001234567", "situacao": "83953"},
    "logistica": None,
    "chamados": [],
    "devolucoes": [],
}


def test_8_pedido_entregue_le_o_mesmo_dado_do_modelo():
    assert acao_pessoa.pedido_entregue(PEDIDO_ENTREGUE_BLING)
    assert acao_pessoa.pedido_entregue({"pedido": {"situacao": "Entregue"}, "logistica": None})
    assert acao_pessoa.pedido_entregue({"pedido": {}, "logistica": {"entregue_em": "2026-10-20"}})
    assert acao_pessoa.pedido_entregue({"pedido": {}, "logistica": {"status": "Pedido entregue"}})
    assert not acao_pessoa.pedido_entregue({"pedido": {"situacao": "9"}, "logistica": None})
    assert not acao_pessoa.pedido_entregue({})


@pytest.mark.parametrize("plataforma", ["tiktok", "shopee", "ml"])
@pytest.mark.parametrize(
    "texto",
    ["não chegou", "não recebi", "consta entregue mas não recebi", "não me entregaram nada"],
)
def test_8_consta_entregue_e_nao_chegou_abre_chamado_em_toda_plataforma(plataforma, texto):
    recentes = [_cliente(texto, 1)]
    gat = _gat(recentes, conversa=_conv(plataforma=plataforma), ctx=PEDIDO_ENTREGUE_BLING)
    assert constantes.ACAO_ABRIR_CHAMADO in _codigos(gat), (plataforma, texto)
    # O mesmo no pedido que uma ferramenta leu (o outro pedido do comprador).
    outro = acao_pessoa.gatilhos_de_outro_pedido(PEDIDO_ENTREGUE_BLING, None, recentes, None)
    if texto != "consta entregue mas não recebi":
        assert constantes.ACAO_ABRIR_CHAMADO in _codigos(outro), texto


def test_8_sem_a_entrega_nao_abre_chamado():
    recentes = [_cliente("não chegou ainda", 1)]
    ctx = {**PEDIDO_ENTREGUE_BLING, "pedido": {**PEDIDO_ENTREGUE_BLING["pedido"], "situacao": "9"}}
    gat = _gat(recentes, conversa=_conv(plataforma="tiktok"), ctx=ctx, postado="sim")
    assert constantes.ACAO_ABRIR_CHAMADO not in _codigos(gat)


def test_1_o_que_nao_declara_condicao_no_titulo_e_na_descricao():
    for titulo in ("Relógio Smartwatch o Mais Usado do Brasil", "Tênis Usado Para Corrida"):
        assert condicao_anuncio.condicao_do_anuncio(titulo, None, {}) is None, titulo
    assert condicao_anuncio.condicao_do_anuncio("iPhone 11 Usado", None, {}) == {
        "novo": {"valor": False, "fontes": ["titulo"]},
        "conflito_no_anuncio": False,
    }
    for texto in (
        "Compatível com modelos similares. Enviamos cópia da nota fiscal.",
        "Acompanha carregador genérico.",
    ):
        assert condicao_anuncio.condicao_do_anuncio("Relógio", texto, {}) is None, texto
    assert condicao_anuncio.condicao_do_anuncio("Relógio", "Não é réplica.", {}) == {
        "original": {"valor": True, "fontes": ["descricao"]},
        "conflito_no_anuncio": False,
    }
