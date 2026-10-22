"""Os ajustes da tarde AO VIVO (21/10/2026, depois do deploy fd896dd5): Barbosa no
TikTok e na Shopee (itens 9, 10 e 11 da auditoria, o dono aprovou).

Cada item com o rascunho REAL que ficou preso (os textos de produção, sem dado
pessoal), e o que continua preso ao lado. Só o caminho do AGENTE muda; o legado
é o de sempre (os `test_k_*` do e2e e o `test_atendimento_conferir_saida`).

9. cd4ff3d8 (TikTok, 17:07): "Seu pedido já foi postado, e você acompanha a
   previsão de entrega e o andamento do envio em os pedidos no app do TikTok Shop
   > o pedido > detalhes do envio. …" — o pedido estava "Em andamento" no Bling
   (15 = postado; o `postado` do bloco vem da situação do Bling, com ou sem a
   linha da logística): o "já foi postado" tinha lastro. O que segurava era a
   oração do CAMINHO ("o andamento do envio … no app"), que o "e" separa do
   "você acompanha" — "andamento" (e o "Shop" do TikTok Shop) não estavam entre
   as palavras do processo. O mesmo pegava a Kia (15da122e: "o andamento do
   envio e a previsão aparecem em Minhas Compras"). E a gramática: o caminho do
   TikTok vai ao modelo já com a preposição ("nos pedidos do app do TikTok
   Shop"), e o "em os pedidos" que ainda vier sai contraído.
   Revisão: "o andamento" só é o nome do processo sem verbo de estado na oração
   ("seu pedido segue o andamento normalmente", "o andamento já mostra a
   coleta" continuam presos sem o dado).
10. ef4cb373 (Shopee, 16:50): pede_atendente respondido como o atendimento
   ("Como podemos ajudar?…"), certo, com confiança 0,7 (a descrição do campo
   dizia "certa e COMPLETA"; o "como ajudar" não "completava"). Perguntar como
   ajudar é resposta completa — revisão: SÓ no pede_atendente e na saudação; a
   pergunta de qual modelo (confirmar_variacao, que está no AUTO) e o
   "responder" ficam com a régua de sempre. O `CONFIANCA_AUTO` fica.
11. 31a72a93 (TikTok, 17:11): "Lamentamos o transtorno com a bateria
   descarregando rápido. Para analisarmos, envie por aqui um vídeo curto…" — o
   cliente escreveu "tá descarregando muito rápido". O acolhimento que REPETE o
   sintoma que o próprio cliente relatou não é característica do produto nem
   diagnóstico. Revisão: a palavra que o cliente só PERGUNTOU ou CONTESTOU
   ("não é original", "o carregador é turbo?") não é relato — o eco não vale com
   característica (vocabulário além do nome da peça, número, condição, o que
   acompanha).
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from app.services.atendimento import constantes, ia
from app.services.atendimento import manual as manual_svc
from app.services.atendimento.agente import acao_pessoa, checagens, pacote, prompt, saida
from app.services.atendimento.agente import contexto as agente_ctx
from app.services.atendimento.agente.saida import Proposta
from tests.test_atendimento_agente_pacote import _cliente, _loja

# ═══════════════ os dados dos casos reais ═══════════════

# cd4ff3d8 (Barbosa, TikTok, 21/10 17:07): o cliente tocou "Quando o meu pedido
# chegará?" no menu do TikTok; o rascunho real.
PERGUNTA_CHEGA = "Quando o meu pedido chegará?"
RASCUNHO_POSTADO = (
    "Olá! Seu pedido já foi postado, e você acompanha a previsão de entrega e o andamento do "
    "envio em os pedidos no app do TikTok Shop > o pedido > detalhes do envio. Como ele já foi "
    "enviado, a forma de cancelar agora é a devolução: ao receber, peça a devolução ou o "
    "reembolso pela opção do próprio pedido no app, e o TikTok Shop analisa e devolve pela "
    "mesma forma de pagamento."
)
# A frase do caminho como o código a entrega (o "em os" contraído).
CAMINHO_CORRIDO = (
    "você acompanha a previsão de entrega e o andamento do envio nos pedidos do app do TikTok "
    "Shop > o pedido > detalhes do envio"
)
# O status da transportadora na produção ("Postado em <cidade da loja>") — o bloco
# mostra sem a cidade.
STATUS_REAL = "Postado em Americana"


def _bloco_tiktok(**kw: Any) -> dict:
    """O bloco do pedido (`pacote.bloco_do_pedido`) do cd4ff3d8: Bling 15 "Em andamento"."""
    bloco = {
        "numero": "{numero_pedido}",
        "situacao": "Em andamento",
        "data_da_compra": "19/10/2026",
        "itens": [
            {
                "descricao": "Uranyx A18 Pro Max - S5 Edition 16.128 - Prata",
                "variacao": None,
                "quantidade": 1,
            }
        ],
        "postado": acao_pessoa.POSTADO_SIM,
        "enviado": True,
        "data_envio": "{data_envio}",
        "entregue": False,
        "ultimo_status": pacote.status_sem_cidade(STATUS_REAL),
        "rastreio": "{rastreio}",
        "transportadora": None,
        "nf": "{nf_numero}",
        "previsao_entrega": None,
    }
    bloco.update(kw)
    return bloco


# O pedido do TikTok sem a linha da logística (o caso comum lá: só a situação do Bling).
BLOCO_SO_BLING = {"ultimo_status": None, "data_envio": None, "rastreio": None}

# 15da122e (Kia, Shopee, 13:58): a mesma oração do caminho, com "Minhas Compras".
RASCUNHO_KIA = (
    "Entendemos sua preocupação, já que o presente é para o aniversário do seu filho. O pedido "
    "já foi postado, e o andamento do envio e a previsão aparecem em Minhas Compras > o pedido "
    "> Informações do envio."
)

# ef4cb373 (Barbosa, Shopee, 16:50): "Olá / Boa tarde" + o cartão do produto, o menu
# automático da loja e a opção 6 ("Falar com Atendente"); o rascunho real.
MENU_DA_LOJA = (
    "Olá, por favor selecione sua dúvida e logo um dos nossos consultores irá atendê-lo!\n\n"
    "1 - Previsão de entrega / envio\n2 - Nota fiscal\n3 - Encerramento da compra\n"
    "4 - Troca de endereço\n5 - Garantia\n6 - Falar com Atendente"
)
RASCUNHO_ATENDENTE = (
    "Olá! Recebemos o seu contato e a nossa equipe de atendimento está à disposição. Como "
    "podemos ajudar? Conte qual é a sua dúvida sobre o produto anunciado ou sobre um pedido."
)

# 31a72a93 (Barbosa, TikTok, 17:11): o relato e o rascunho reais.
RELATO_BATERIA = "tá descarregando muito rápido"
RASCUNHO_BATERIA = (
    "Lamentamos o transtorno com a bateria descarregando rápido. Para analisarmos, envie por aqui "
    "um vídeo curto mostrando o problema e fotos, sem dados pessoais na tela. A nossa equipe "
    "analisa e responde por aqui."
)
ITEM_DO_PEDIDO = "Uranyx A17 Pro Max 12.128 - Branco"


def _ctx(plataforma: str = "tiktok", modo: str = constantes.MODO_AUTO) -> Any:
    conversa = SimpleNamespace(
        id=uuid4(), plataforma=plataforma, canal="chat", pedido_marketplace=None
    )
    return agente_ctx.ContextoAgente.da_conversa(conversa, None, modo=modo)


# ═══════════════ 9. o "já foi postado" e o caminho do TikTok (cd4ff3d8) ═══════════════


@pytest.mark.parametrize("extra", [{}, BLOCO_SO_BLING], ids=["com_logistica", "so_bling"])
def test_9_o_rascunho_real_tem_lastro(extra):
    bloco = _bloco_tiktok(**extra)
    for texto in (RASCUNHO_POSTADO, prompt.caminho_na_frase(RASCUNHO_POSTADO)):
        assert checagens.conferir_andamento_sem_lastro(texto, bloco, None, assunto="rastreio") == []
        assert checagens.conferir_estado_do_pedido(texto, bloco, None) == []


def test_9_o_postado_do_tiktok_vem_da_situacao_do_bling():
    """O TikTok costuma não ter a linha da logística: a situação crua do Bling decide
    (15 Em andamento = postado; 21 Em digitação = não saiu), como no `entregue`."""
    agora = datetime(2026, 10, 21, 20, 7, tzinfo=UTC)
    assert acao_pessoa.postado_de("15", None, agora=agora) == acao_pessoa.POSTADO_SIM
    assert acao_pessoa.postado_de("83953", None, agora=agora) == acao_pessoa.POSTADO_SIM
    assert acao_pessoa.postado_de("21", None, agora=agora) == acao_pessoa.POSTADO_NAO
    # A transportadora dizendo "postado em <cidade>" também prova.
    assert (
        acao_pessoa.postado_de(None, {"status": STATUS_REAL}, agora=agora)
        == acao_pessoa.POSTADO_SIM
    )


def test_9_o_postado_sem_o_dado_continua_preso():
    nao_saiu = _bloco_tiktok(
        situacao="Em digitação",
        postado=acao_pessoa.POSTADO_NAO,
        enviado=False,
        **BLOCO_SO_BLING,
    )
    (p,) = checagens.conferir_estado_do_pedido(RASCUNHO_POSTADO, nao_saiu, None)
    # O dado contradiz: pessoa nos dois modos (não é regra só do automático).
    assert p.codigo == checagens.ESTADO_CONTRADIZ and not p.so_auto
    nao_se_sabe = _bloco_tiktok(situacao=None, postado=None, enviado=False, **BLOCO_SO_BLING)
    (p,) = checagens.conferir_andamento_sem_lastro(
        RASCUNHO_POSTADO, nao_se_sabe, None, assunto="rastreio"
    )
    assert p.codigo == checagens.PEDIDO_SEM_LASTRO


@pytest.mark.parametrize(
    "texto",
    [
        CAMINHO_CORRIDO + ".",
        "Você acompanha o rastreio e o andamento pelo app do TikTok Shop.",
        RASCUNHO_KIA,
    ],
)
def test_9_o_andamento_como_objeto_do_caminho_nao_afirma_nada(texto):
    bloco = _bloco_tiktok(ultimo_status="Pedido postado")
    assert checagens.conferir_andamento_sem_lastro(texto, bloco, None, assunto="rastreio") == []


# Revisão do item 9 (21/10/2026): o pedido em que nada se sabe, o do TikTok em
# "Em digitação" (Bling 21: não saiu) e a conversa sem pedido.
NADA_SE_SABE = _bloco_tiktok(situacao=None, postado=None, enviado=False, **BLOCO_SO_BLING)
EM_DIGITACAO = _bloco_tiktok(
    situacao="Em digitação", postado=acao_pessoa.POSTADO_NAO, enviado=False, **BLOCO_SO_BLING
)
# O andamento AFIRMADO com "o andamento" de nome e um verbo de estado (segue, mostra,
# indica, traz): o código de antes da isenção prendia; a isenção de toda oração soltava.
ANDAMENTO_COM_VERBO_DE_ESTADO = [
    "Seu pedido segue o andamento normalmente.",
    "Seu pedido segue o andamento dentro do prazo.",
    "O pedido segue o andamento do envio pela transportadora.",
    "Ele segue no andamento do envio.",
    "O andamento do envio já indica a entrega.",
    "O andamento do envio já traz a data de entrega.",
    "O andamento do envio já traz o código de rastreio.",
    "O andamento já mostra a coleta.",
    # Fora do TikTok também.
    "Seu pedido segue o andamento normalmente pelo app da Shopee.",
    # O andamento afirmado + o caminho certo do TikTok, na mesma frase.
    "Olá! Seu pedido segue o andamento normalmente, e você acompanha nos pedidos do app do "
    "TikTok Shop > o pedido > detalhes do envio.",
    "Olá! O andamento do envio já mostra a coleta, e você acompanha nos pedidos do app do "
    "TikTok Shop > o pedido > detalhes do envio.",
]


@pytest.mark.parametrize(
    "bloco",
    [_bloco_tiktok(), NADA_SE_SABE, EM_DIGITACAO, None],
    ids=["postado", "nada_se_sabe", "em_digitacao", "sem_bloco"],
)
@pytest.mark.parametrize(
    "texto",
    [
        "O andamento do envio está normal.",
        "O andamento do seu pedido está atrasado na transportadora.",
        "Seu pedido já foi postado, e o andamento do envio está dentro do previsto.",
        "O andamento do envio nos pedidos do app do TikTok Shop mostra que ele chega amanhã.",
        # "Em andamento" é o estado (não o nome do processo): sem o dado, preso.
        "O pedido segue em andamento.",
        "Sua entrega continua em andamento normalmente.",
        *ANDAMENTO_COM_VERBO_DE_ESTADO,
    ],
)
def test_9_o_andamento_afirmado_continua_preso(texto, bloco):
    (p,) = checagens.conferir_andamento_sem_lastro(texto, bloco, None, assunto="rastreio")
    assert p.codigo == checagens.PEDIDO_SEM_LASTRO


@pytest.mark.parametrize("extra", [{}, BLOCO_SO_BLING], ids=["com_logistica", "so_bling"])
def test_9_o_caminho_sem_verbo_de_estado_continua_livre(extra):
    """A revisão não volta a prender o que o item 9 soltou: o cd4ff3d8 real (cru e
    contraído), o caminho corrido, o "rastreio e o andamento pelo app" e o 15da122e."""
    bloco = _bloco_tiktok(**extra)
    for texto in (
        RASCUNHO_POSTADO,
        prompt.caminho_na_frase(RASCUNHO_POSTADO),
        CAMINHO_CORRIDO + ".",
        "Você acompanha o rastreio e o andamento pelo app do TikTok Shop.",
        RASCUNHO_KIA,
    ):
        assert checagens.conferir_andamento_sem_lastro(texto, bloco, None, assunto="rastreio") == []


def test_9_o_caminho_do_tiktok_cabe_depois_do_em():
    (caminho, *_resto) = prompt.CAMINHOS_APP["tiktok"]
    assert "nos pedidos do app do TikTok Shop > o pedido > detalhes do envio" in caminho
    bloco = prompt.bloco_da_plataforma("tiktok", manual_svc.categorias_padrao())
    assert '"em Minhas compras > o pedido"' in bloco
    assert 'escreva "nos pedidos do app do TikTok Shop > o pedido"' in bloco
    assert "os pedidos no app" not in bloco and "em os pedidos" not in bloco.replace(
        'nunca "em os pedidos"', ""
    )
    # O manual e as orientações que o `consultar_manual` devolve.
    assert (
        prompt.no_app_da_plataforma("Acompanhe em Minhas compras > o pedido.", "tiktok")
        == "Acompanhe nos pedidos do app do TikTok Shop > o pedido."
    )
    assert (
        prompt.no_app_da_plataforma("Confira no Minhas Compras.", "tiktok")
        == "Confira nos pedidos do app do TikTok Shop."
    )
    assert (
        prompt.no_app_da_plataforma("Abra Minhas compras > o pedido.", "tiktok")
        == "Abra os pedidos do app do TikTok Shop > o pedido."
    )
    # A regra suave do "Minhas compras" no TikTok aponta o caminho certo.
    (p,) = checagens.conferir_caminho_da_plataforma("Veja em Minhas compras.", "tiktok")
    assert "nos pedidos do app do TikTok Shop > o pedido" in p.motivo


_CONTRAIDO = RASCUNHO_POSTADO.replace(
    "em os pedidos no app do TikTok Shop", "nos pedidos do app do TikTok Shop"
)


@pytest.mark.parametrize(
    ("errado", "certo"),
    [
        (RASCUNHO_POSTADO, _CONTRAIDO),
        (
            "Em os pedidos do app do TikTok Shop > o pedido.",
            "Nos pedidos do app do TikTok Shop > o pedido.",
        ),
        ("Veja em nos pedidos do app do TikTok Shop.", "Veja nos pedidos do app do TikTok Shop."),
    ],
)
def test_9_o_em_os_pedidos_sai_contraido(errado, certo):
    assert prompt.caminho_na_frase(errado) == certo
    assert prompt.caminho_na_frase(certo) == certo
    assert CAMINHO_CORRIDO in prompt.caminho_na_frase(RASCUNHO_POSTADO)


def test_9_o_resto_do_texto_nao_muda():
    for texto in (
        "",
        RASCUNHO_KIA,
        RASCUNHO_BATERIA,
        "Acompanhe em Minhas compras > o pedido.",
        "Os pedidos no app do TikTok Shop mostram o envio.",
    ):
        assert prompt.caminho_na_frase(texto) == texto


# ═══════════════ 10. a confiança do "perguntar" (ef4cb373) ═══════════════


def test_10_perguntar_como_ajudar_e_resposta_completa_so_no_pede_atendente():
    descricao = saida.esquema()["properties"]["confianca"]["description"]
    # A régua de sempre continua para o resto (o "responder", o "perguntar" de qual
    # modelo do confirmar_variacao): "certa e completa".
    assert descricao.split(".")[0] == "De 0 a 1: o quanto a resposta está certa e completa"
    # A régua nova só no "como ajudar" do pede_atendente e da saudação, sem pendência.
    assert "pede_atendente" in descricao and "saudacao_ou_vaga" in descricao, descricao
    assert "como ajudar" in descricao and "nada pendente" in descricao, descricao
    # Nada que estenda a régua a toda pergunta (revisão do item 10).
    for generico in ("pergunta certa", "mesma confiança", "não o quanto o caso"):
        assert generico not in descricao, descricao
    geral = prompt.bloco_geral()
    (linha,) = [x for x in geral.splitlines() if x.startswith("- confianca:")]
    # Item 13 (21/10/2026, noite): a linha segue com a régua do 0,9 (cada afirmação
    # com lastro e a pergunta respondida) — o começo é o do item 10.
    assert linha.startswith(
        "- confianca: de 0 a 1 (perguntar como ajudar é resposta completa); "
    ), linha
    assert "a pergunta certa" not in geral
    # O limiar do legado não mudou; o do agente é 0,80 desde 22/10/2026.
    assert ia.CONFIANCA_AUTO == 0.85
    assert ia.CONFIANCA_AUTO_AGENTE == 0.80


async def test_10_o_rascunho_real_de_pede_atendente_so_dependia_da_confianca():
    """Com a confiança que a resposta certa merece, nenhuma checagem segura o
    rascunho real (no automático): a confiança era o único freio."""
    recentes = [
        _cliente("Olá \nBoa tarde", 4),
        _loja(MENU_DA_LOJA, 3),
        _cliente("6", 3),
    ]
    textos = ["Olá \nBoa tarde", "6"]
    ctx = _ctx("shopee")
    # O assunto do manual base de produção (as constantes não o trazem).
    categorias = [
        *manual_svc.categorias_padrao(),
        {
            "id": "pede_atendente",
            "nome": "Pede atendente",
            "descricao": "",
            "exemplos": [],
            "so_humano": False,
            "lacunas": [],
            "ordem": 9_990,
        },
    ]
    p = Proposta(
        acao="perguntar",
        texto=RASCUNHO_ATENDENTE,
        assunto="pede_atendente",
        confianca=0.9,
        acao_pessoa=None,
        motivo_pessoa="",
        evidencias=[],
    )
    base = agente_ctx.BaseDoGerar(
        recentes=recentes,
        rajada=[],
        foco=[],
        textos_cliente=textos,
        textos_rajada=textos,
        ctx={"pedido": None, "logistica": None, "chamados": [], "devolucoes": []},
        valores={},
        fatos={"anuncio": None},
        fatos_gravados={},
        categorias=categorias,
        regras=[],
        sinais=[],
        motivos=[],
        opcoes_sensiveis=[],
        sinais_do_cliente=[],
        nomes=(),
        modo=constantes.MODO_AUTO,
        forcar=True,
        nf_politica_aprovada=False,
        insistiu=False,
    )
    conversa = SimpleNamespace(
        id=uuid4(), plataforma="shopee", canal="chat", dados={}, pedido_marketplace=None
    )
    conferida = await ia.conferir_saida(
        None,
        conversa,
        SimpleNamespace(),
        **saida.para_conferir(p, base.ids_categorias),
        motivos=[],
        ctx=base.ctx,
        fatos={"anuncio": None, "ficha_do_anuncio": []},
        valores={},
        categorias=base.categorias,
        regras=[],
        textos_cliente=textos,
        textos_rajada=textos,
        fichas=[],
        garantias_conferidas=[],
        permitidos_extra=ctx.permitidos_extra(),
        modo=constantes.MODO_AUTO,
        forcar=True,
        agente=True,
    )
    chk = checagens.conferir(p, conferida, ctx, base, fichas=[], gatilho=recentes[-1])
    final = checagens.aplicar(
        conferida, chk, acoes_pessoa=ctx.acoes_pessoa, modo=constantes.MODO_AUTO
    )
    assert chk.pendencias == [] and not final.precisa_humano, (chk.pendencias, final.motivos)
    assert final.categoria == "pede_atendente" and final.texto == RASCUNHO_ATENDENTE
    # Pedir atendente uma vez (a opção 6) não é ação de pessoa (decisão de 21/10/2026).
    assert acao_pessoa.pedido_de_pessoa(recentes, recentes[-1]) is None


# ═══════════════ 11. o sintoma do cliente no acolhimento (31a72a93) ═══════════════


def _fora(texto: str, textos_cliente: list[str]) -> list[str]:
    return checagens.caracteristicas_fora_do_dado(
        texto, [], nomes_de_produto=[ITEM_DO_PEDIDO], textos_cliente=textos_cliente
    )


def _defeito(texto: str, textos_cliente: list[str]) -> list:
    return checagens.conferir_orientacao_do_defeito(
        texto, acao="responder", assunto="defeito", textos_cliente=textos_cliente, com_pedido=True
    )


def test_11_o_rascunho_real_passa():
    assert _fora(RASCUNHO_BATERIA, [RELATO_BATERIA]) == []
    assert _defeito(RASCUNHO_BATERIA, [RELATO_BATERIA]) == []
    assert (
        checagens.conferir_ficha_no_automatico(
            RASCUNHO_BATERIA,
            [],
            textos_cliente=[RELATO_BATERIA],
            nomes_de_produto=[ITEM_DO_PEDIDO],
            assunto="defeito",
        )
        == []
    )
    # Também pela régua do texto (assunto genérico) e na pergunta do cliente.
    assert not checagens.afirma_caracteristica(
        RASCUNHO_BATERIA, [], textos_cliente=[RELATO_BATERIA], nomes_de_produto=[ITEM_DO_PEDIDO]
    )


@pytest.mark.parametrize(
    ("texto", "relato"),
    [
        ("Sentimos muito pela bateria descarregando rápido.", RELATO_BATERIA),
        ("Sentimos muito pelo problema na tela piscando.", "a tela fica piscando"),
        ("Lamentamos o ocorrido com o aparelho que não liga.", "o celular não liga mais"),
        ("Pedimos desculpas pelo transtorno com a rodinha quebrada.", "a rodinha chegou quebrada"),
    ],
)
def test_11_o_acolhimento_que_repete_o_relato(texto, relato):
    assert _fora(texto, [relato]) == []
    assert _defeito(texto, [relato]) == []


# Revisão do item 11 (21/10/2026): a característica que o cliente só PERGUNTOU ou
# CONTESTOU, repetida no acolhimento — o eco soltava; antes dele, os DOIS alarmes.
CARACTERISTICA_PERGUNTADA_OU_CONTESTADA = [
    ("Lamentamos o ocorrido com o aparelho original.", "não é original"),
    ("Lamentamos o transtorno com a câmera de 108 MP.", "não é de 108 MP"),
    ("Lamentamos o transtorno com o aparelho com 5G.", "não é 5G?"),
    (
        "Lamentamos o transtorno com o aparelho resistente à água que parou.",
        "não é resistente à água? parou",
    ),
    ("Lamentamos o transtorno com o carregamento rápido.", "era pra ser carregamento rápido"),
    (
        "Lamentamos o transtorno com o carregador turbo carregando devagar.",
        "o carregador é turbo? tá carregando devagar",
    ),
    ("Lamentamos o transtorno com o nfc do aparelho.", "o aparelho tem nfc?"),
    ("Lamentamos o transtorno com a bateria de 5000 mAh.", "não é de 5000 mAh a bateria?"),
    ("Lamentamos o transtorno com o sistema android 14.", "não é android 14 o sistema?"),
    ("Lamentamos o transtorno com o aparelho dual chip.", "não é dual chip o aparelho?"),
    ("Lamentamos o transtorno com a câmera de 50mp.", "a câmera não é de 50mp"),
    ("Lamentamos o transtorno com o aparelho lacrado.", "o aparelho não veio lacrado"),
    ("Lamentamos o transtorno com a bateria de 6000.", "a bateria não é de 6000?"),
    ("Lamentamos o transtorno com o carregador que não acompanha.", "o carregador não acompanha?"),
    ("Lamentamos o transtorno com a biometria do aparelho.", "tem biometria o aparelho?"),
    ("Lamentamos o transtorno com a tela amoled.", "a tela não é amoled?"),
    ("Lamentamos o transtorno com o aparelho desbloqueado.", "não é desbloqueado?"),
    ("Lamentamos o transtorno com a memória de 128.", "a memória não é de 128?"),
    ("Lamentamos o transtorno com o fone que não acompanha.", "o fone não acompanha?"),
    ("Lamentamos o problema com a entrada de fone.", "não tem entrada de fone?"),
]
# A condição fora do vocabulário da ficha ("novo", "seminovo"): o eco também não vale
# (fica o alarme do defeito fora da orientação, como antes).
CONDICAO_PERGUNTADA_OU_CONTESTADA = [
    ("Lamentamos o transtorno com o aparelho novo.", "o aparelho não é novo"),
    ("Lamentamos o transtorno com o aparelho seminovo.", "é seminovo?"),
]


@pytest.mark.parametrize(
    ("texto", "relato"),
    [
        # Sem o relato do cliente (o eco precisa do relato).
        ("Lamentamos o transtorno com a bateria descarregando rápido.", "a bateria dura muito?"),
        # O que o cliente NÃO disse: a causa, o diagnóstico, o valor.
        (
            "Lamentamos o transtorno com a bateria descarregando rápido por causa do uso intenso.",
            RELATO_BATERIA,
        ),
        ("Lamentamos o transtorno com a bateria de 5000 mAh.", RELATO_BATERIA),
        ("Sentimos muito pela bateria, que é normal descarregar rápido.", RELATO_BATERIA),
        ("Lamentamos o transtorno com a bateria descarregando rápido no começo.", RELATO_BATERIA),
        *CARACTERISTICA_PERGUNTADA_OU_CONTESTADA,
        *CONDICAO_PERGUNTADA_OU_CONTESTADA,
    ],
)
def test_11_o_que_vai_alem_do_relato_continua_preso(texto, relato):
    assert _fora(texto, [relato]) or _defeito(texto, [relato]), texto


@pytest.mark.parametrize(("texto", "relato"), CARACTERISTICA_PERGUNTADA_OU_CONTESTADA)
def test_11_a_caracteristica_perguntada_ou_contestada_leva_os_dois_alarmes(texto, relato):
    """Como antes do eco: a característica fora do dado E o defeito fora da orientação."""
    assert _fora(texto, [relato]), texto
    codigos = [p.codigo for p in _defeito(texto, [relato])]
    assert checagens.DEFEITO_FORA_DA_ORIENTACAO in codigos, (texto, codigos)
    assert not checagens.relato_ecoado(checagens._plano(texto).strip(" ."), [relato])


@pytest.mark.parametrize(("texto", "relato"), CONDICAO_PERGUNTADA_OU_CONTESTADA)
def test_11_a_condicao_no_relato_nao_e_eco(texto, relato):
    codigos = [p.codigo for p in _defeito(texto, [relato])]
    assert checagens.DEFEITO_FORA_DA_ORIENTACAO in codigos, (texto, codigos)
    assert not checagens.relato_ecoado(checagens._plano(texto).strip(" ."), [relato])


@pytest.mark.parametrize(
    "relato",
    [
        "carregamento rapido",
        "o carregador turbo carregando devagar",
        "o aparelho original",
        "a bateria de 6000",
        "o aparelho dual chip",
        "o carregador que nao acompanha",
        "o aparelho lacrado",
        "a entrada de fone",
        "o aparelho seminovo",
    ],
)
def test_11_o_relato_com_caracteristica_nao_e_eco(relato):
    assert checagens._caracteristica_no_relato(relato), relato


@pytest.mark.parametrize(
    "relato",
    [
        "a bateria descarregando rapido",
        "a tela piscando",
        "o aparelho que nao liga",
        "a rodinha quebrada",
        "o aparelho travando de novo",
        "a camera embacada",
    ],
)
def test_11_o_nome_da_peca_e_o_sintoma_nao_sao_caracteristica(relato):
    assert not checagens._caracteristica_no_relato(relato), relato


def test_11_sem_os_textos_do_cliente_e_como_antes():
    assert _fora(RASCUNHO_BATERIA, []) == ["bateria"]


def test_11_o_eco_le_o_cliente_e_nao_a_resposta():
    """O termo vale pelo que o CLIENTE escreveu: a mesma frase do agente, com outro
    relato, volta a ser afirmação."""
    assert _fora(RASCUNHO_BATERIA, ["o celular esquenta muito"])
    assert checagens.relato_ecoado(
        "lamentamos o transtorno com a bateria descarregando rapido", [RELATO_BATERIA]
    )
    assert not checagens.relato_ecoado(
        "lamentamos o transtorno com a bateria descarregando rapido", ["oi, boa tarde"]
    )
    assert not checagens.relato_ecoado("a bateria descarregando rapido", [RELATO_BATERIA])
