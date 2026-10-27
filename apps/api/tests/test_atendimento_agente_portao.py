"""O PORTÃO do envio sozinho do agente (redesenho do dono, 27/10/2026).

"Responder corretamente pra tudo sem uma regra atrapalhar a outra": o automático do agente =
acao "responder" com texto + nenhum bloqueio DURO + o "ok" do verificador. O que não é duro
vira AVISO ao verificador (a seção AVISOS DAS CHECAGENS, v14). Um teste por bloqueio duro e
por aviso convertido; o legado (Groq, sem agente) não muda.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from app.services.atendimento import constantes, ia
from app.services.atendimento.agente import acao_pessoa, checagens, verificador
from app.services.atendimento.constantes import MODO_AUTO, MODO_COPILOTO, RASCUNHO_PENDENTE
from scripts.agente_eval import regatilho
from tests.test_atendimento_agente_furos import SAIU, _completo, _sai, _segura
from tests.test_atendimento_agente_redondo import _cli, _menu


def _conferida(**kw: Any) -> ia.SaidaConferida:
    base = {
        "texto": "Olá! Seu pedido segue normalmente.",
        "categoria": "outro",
        "precisa_humano": False,
        "motivos": [],
        "erros": [],
        "status": RASCUNHO_PENDENTE,
    }
    base.update(kw)
    return ia.SaidaConferida(**base)


# ─────────────── 1. a régua: o que é duro ───────────────

D1 = "com a devolução aberta, a resposta "


@pytest.mark.parametrize("codigo", sorted(checagens.DURAS))
def test_cada_codigo_duro_segura_no_automatico(codigo):
    p = checagens._pessoa(codigo, f"motivo {codigo}")
    assert checagens.pendencia_dura(p)
    final = checagens.aplicar(
        _conferida(), checagens.Checagens(pendencias=[p]), acoes_pessoa=[], modo=MODO_AUTO
    )
    assert final.precisa_humano and f"motivo {codigo}" in final.motivos
    assert f"motivo {codigo}" in final.sem_auto


@pytest.mark.parametrize(
    ("codigo", "motivo", "duro"),
    [
        (checagens.ESTADO_CONTRADIZ, "afirma aprovação ou conclusão que só a equipe dá (x)", True),
        (checagens.ESTADO_CONTRADIZ, "diz que o pedido saiu, mas ele ainda não foi postado", False),
        (checagens.ESTADO_SEM_DADO, "afirma o reembolso ou estorno sem o dado", True),
        (checagens.ESTADO_SEM_DADO, 'diz o que a plataforma aprovou ("x")', True),
        (checagens.ESTADO_SEM_DADO, "diz que o pedido saiu sem o dado da postagem", False),
        (checagens.RESPONDE_E_MARCA, D1 + "decide ou dá o resultado", True),
        (checagens.RESPONDE_E_MARCA, D1 + "promete data, prazo ou valor", True),
        (checagens.RESPONDE_E_MARCA, D1 + "fala de Pix ou dados bancários", True),
        (checagens.RESPONDE_E_MARCA, D1 + "promete ação da equipe além", False),
    ],
)  # fmt: skip
def test_os_duros_pelo_motivo(codigo, motivo, duro):
    assert checagens.pendencia_dura(checagens._pessoa(codigo, motivo)) is duro


def test_a_regra_que_bloqueia_continua_bloqueando():
    p = checagens._bloqueia(checagens.RECADO_AO_REVISOR, "o texto fala com o revisor")
    final = checagens.aplicar(
        _conferida(), checagens.Checagens(pendencias=[p]), acoes_pessoa=[], modo=MODO_AUTO
    )
    assert final.status == constantes.RASCUNHO_BLOQUEADO and not final.validador_ok


def test_a_acao_de_pessoa_do_modelo_e_do_codigo_segura():
    final = checagens.aplicar(
        _conferida(),
        checagens.Checagens(),
        acoes_pessoa=[constantes.ACAO_ENVIAR_ARQUIVO_NF],
        modo=MODO_AUTO,
    )
    assert final.precisa_humano and final.sem_auto


# ─────────────── 2. os avisos: o que deixou de segurar sozinho ───────────────

AVISOS = [
    checagens.FICHA_NO_AUTOMATICO,
    checagens.PRODUTO_SEM_FONTE,
    checagens.PRODUTO_CONTRADIZ,
    checagens.VARIACAO_CONTRADIZ,
    checagens.VARIACAO_SEM_DADO,
    checagens.CARACTERISTICA_FORA_DO_ANUNCIO,
    checagens.PEDIDO_SEM_LASTRO,
    checagens.ASSUNTO_NAO_PEDIDO,
    checagens.POLITICA_DA_ENTREGA_SEM_PAR,
    checagens.OUTRO_PRODUTO_SEM_BUSCA,
    checagens.NAO_TEMOS_SEM_FONTE,
    checagens.PROMESSA_DE_EQUIPE,
    checagens.PERGUNTA_REPETIDA,
    checagens.ANUNCIO_SE_CONTRADIZ,
    checagens.EVIDENCIA_SEM_PROVA,
    checagens.REGIAO_SEM_FONTE,
    checagens.DEFEITO_FORA_DA_ORIENTACAO,
    checagens.CONFIANCA_BAIXA,
    checagens.ASSUNTO_DE_PRODUTO,
]


@pytest.mark.parametrize("codigo", AVISOS)
@pytest.mark.parametrize("tipo", ["suave", "pessoa"])
def test_o_que_nao_e_duro_vira_aviso_no_automatico(codigo, tipo):
    p = getattr(checagens, f"_{tipo}")(codigo, f"aviso {codigo}")
    assert not checagens.pendencia_dura(p)
    auto = checagens.aplicar(
        _conferida(), checagens.Checagens(pendencias=[p]), acoes_pessoa=[], modo=MODO_AUTO
    )
    assert auto.avisos == [f"aviso {codigo}"]
    assert not auto.precisa_humano and auto.motivos == [] and auto.sem_auto == []
    # No copiloto, como antes (a pessoa lê de qualquer jeito).
    cop = checagens.aplicar(
        _conferida(), checagens.Checagens(pendencias=[p]), acoes_pessoa=[], modo=MODO_COPILOTO
    )
    assert cop.sem_auto == [f"aviso {codigo}"]


@pytest.mark.parametrize(
    "motivo",
    [
        "categoria fora da lista",
        "a categoria da IA (rastreio) não bate com o texto do cliente (reembolso)",
        ia.MOTIVO_FICHA_NO_AUTOMATICO,
        "pedido não encontrado no sistema",
        "conversa sem pedido identificado",
        "sem dado para {rastreio}",
        "a frase com {rastreio} (sem o dado) saiu do rascunho",
    ],
)
def test_os_motivos_do_legado_que_viram_aviso(motivo):
    c = _conferida(precisa_humano=True, motivos=[motivo], sem_auto=[motivo])
    s = checagens.separar_avisos(c, modo=MODO_AUTO, pessoa_do_modelo=False)
    assert s.motivos == [] and s.sem_auto == [] and motivo in s.avisos and not s.precisa_humano
    # No copiloto não muda; e o "pessoa" do próprio modelo continua valendo.
    assert checagens.separar_avisos(c, modo=MODO_COPILOTO, pessoa_do_modelo=False) is c
    assert checagens.separar_avisos(c, modo=MODO_AUTO, pessoa_do_modelo=True).precisa_humano


@pytest.mark.parametrize(
    "motivo",
    [
        "validador: prazo em números só pode vir do sistema",
        f"{ia.MOTIVO_NUMERO_INVENTADO} (7)",
        ia.MOTIVO_RECUSA_DE_DIREITO,
        ia.MOTIVO_RESPOSTA_DE_GARANTIA,
        ia.MOTIVO_AG_CANCELAMENTO,
        "reclamação/mediação aberta no ML",
        "pedido com chamado ou devolução",
        "palavra de alerta na conversa (procon)",
        ia.MOTIVO_INJECAO,
        ia.MOTIVO_INSISTIU_EM_PESSOA,
        "há pergunta concreta pendente — a resposta genérica não a resolve",
        "condição de lacrado sem confirmação no título do anúncio",
    ],
)
def test_os_motivos_do_legado_que_continuam_duros(motivo):
    c = _conferida(precisa_humano=True, motivos=[motivo], sem_auto=[motivo])
    s = checagens.separar_avisos(c, modo=MODO_AUTO, pessoa_do_modelo=False)
    assert s.motivos == [motivo] and s.sem_auto == [motivo] and s.precisa_humano


@pytest.mark.parametrize(
    ("conf", "codigo"),
    [(0.3, checagens.CONFIANCA_ABAIXO_DO_PISO), (0.6, checagens.CONFIANCA_BAIXA), (0.8, None)],
)
def test_a_confianca_piso_duro_e_aviso(conf, codigo):
    saida = checagens.avisos_do_portao(
        confianca=conf, assunto="rastreio", produto_pelo_anuncio=False, produto_pela_equipe=False
    )
    assert [p.codigo for p in saida] == ([codigo] if codigo else [])
    assert ia.CONFIANCA_AUTO_AGENTE == checagens.CONFIANCA_DURA == 0.5
    assert ia.confianca_minima(False) == ia.CONFIANCA_AUTO  # o legado não muda


def test_o_assunto_de_produto_e_aviso_e_a_trava_o_solta_no_auto_categorias():
    (p,) = checagens.avisos_do_portao(
        confianca=0.9,
        assunto="espec_celular",
        produto_pelo_anuncio=False,
        produto_pela_equipe=False,
    )
    assert p.codigo == checagens.ASSUNTO_DE_PRODUTO and not checagens.pendencia_dura(p)
    assert not checagens.avisos_do_portao(
        confianca=0.9, assunto="espec_celular", produto_pelo_anuncio=True, produto_pela_equipe=False
    )
    canal = SimpleNamespace(auto_categorias=["espec_celular"], auto_agente=[], auto_orientacao=[])
    rascunho = SimpleNamespace(
        categoria="espec_celular", fatos={"agente": {"acoes_pessoa": [], "modo": "auto"}}
    )
    assert ia.trava_do_agente(rascunho, canal) is None
    sem = SimpleNamespace(auto_categorias=[], auto_agente=[], auto_orientacao=[])
    assert ia.trava_do_agente(rascunho, sem) == "assunto_nao_liberado"


# ─────────────── 3. os duros novos (texto) ───────────────


@pytest.mark.parametrize(
    ("texto", "duro"),
    [
        ("Pode me passar a sua chave Pix para o estorno?", True),
        ("Fazemos uma transferência bancária do valor.", True),
        ("Depositamos o valor na sua conta.", True),
        ("A Shopee devolve pela mesma forma de pagamento.", False),
        ("O produto sai do nosso depósito em São Paulo.", False),
    ],
)
def test_a_pix_e_dados_bancarios(texto, duro):
    assert bool(checagens.conferir_pix_ou_banco(texto)) is duro


@pytest.mark.parametrize(
    ("texto", "duro"),
    [
        ("O seu reembolso foi aprovado.", True),
        ("Aprovamos a sua devolução.", True),
        ("Vamos reembolsar o valor hoje.", True),
        ("Vamos enviar outro aparelho para você.", True),
        ("Você vai receber o seu reembolso.", True),
        ("Pode ficar com o produto.", True),
        ("Não precisa devolver.", True),
        ("A Shopee analisa e devolve pela mesma forma de pagamento.", False),
        ("O reembolso é feito pela própria Shopee.", False),
        ("O reembolso será feito pela Shopee, pela mesma forma de pagamento.", False),
        ("A devolução ainda não foi aprovada; acompanhe em Minhas Compras.", False),
    ],
)
def test_a_promessa_do_resultado(texto, duro):
    assert bool(checagens.conferir_promete_resultado(texto)) is duro


@pytest.mark.parametrize(
    ("texto", "generica"),
    [
        ("Olá! Como podemos ajudar com o Apple Watch Series 11?", True),
        ("Boa tarde! Em que posso te ajudar?", True),
        ("Sim, tem NFC. Como posso ajudar em algo mais?", False),
        ("O seu pedido foi postado.", False),
    ],
)
def test_a_resposta_so_generica(texto, generica):
    assert checagens.resposta_so_generica(texto) is generica


def test_a_generica_com_pergunta_no_turno_segura_e_sem_pergunta_nao():
    com = [_menu(), *_cli("Olá!", "Você tem apenas a pulseira do Apple Watch para venda?")]
    texto = "Olá! Como podemos ajudar com o Apple Watch Series 11?"
    (p,) = checagens.conferir_generica_com_pergunta(
        texto, acao="responder", recentes=com, gatilho=com[-1]
    )
    assert p.codigo == checagens.GENERICA_COM_PERGUNTA and checagens.pendencia_dura(p)
    sem = [_menu(), *_cli("Olá!")]
    assert not checagens.conferir_generica_com_pergunta(
        texto, acao="responder", recentes=sem, gatilho=sem[-1]
    )


@pytest.mark.parametrize(
    ("cliente", "duro"),
    [
        (["Agora já trocaram a data", "De novo"], True),
        (["Meu celular chega hj", "Quero o meu celular"], True),
        (["Não recebi o pedido"], True),
        (["Obrigada, chegou certinho"], False),
    ],
)
def test_o_entregue_a_quem_nao_recebeu(cliente, duro):
    texto = 'Pelo pedido, o último status é "Pedido entregue".'
    assert bool(checagens.conferir_entregue_a_quem_nao_recebeu(texto, cliente)) is duro


A17_A18 = [
    {
        "item_id": "1",
        "titulo": "Uranyx A17 Pro Max 12.64 ou 16.128 Premium Edition NFC",
        "especificacoes": {
            "campos": [{"campo": "nfc", "valor": "varia", "varia_por_modelo": True}]
        },
    }
]


def test_o_modelo_que_a_variacao_do_pedido_nao_diz():
    variacao = "Laranja,16 GB / 128 GB Premium NFC"
    pedido = {"itens": [{"descricao": "Uranyx A17 Pro Max", "variacao": variacao}]}
    texto = "A película de outro aparelho não serve no seu Uranyx A17 Pro Max."
    (p,) = checagens.conferir_modelo_sem_o_dado(texto, pedido, A17_A18)
    assert p.codigo == checagens.MODELO_SEM_O_DADO and checagens.pendencia_dura(p)
    # A variação que diz o modelo, ou o anúncio de um modelo só: passa.
    com = {"itens": [{"descricao": "Uranyx", "variacao": "A17 - Laranja"}]}
    assert not checagens.conferir_modelo_sem_o_dado(texto, com, A17_A18)
    um = [{"item_id": "1", "titulo": "Uranyx A17 Pro Max 12.64"}]
    assert not checagens.conferir_modelo_sem_o_dado(texto, pedido, um)


@pytest.mark.parametrize(
    ("texto", "duro"),
    [
        ("Capinha e película não constam entre os itens do anúncio.", True),
        ("O aparelho acompanha apenas o cabo de carregamento.", True),
        ("A case de brinde aparece só na variação 16 GB / 128 GB.", True),
        ("Ele vem com uma capinha transparente e o fone.", False),
    ],
)
def test_o_conteudo_da_caixa_sem_o_dado(texto, duro):
    cliente = ["Vem capinha e película?"]
    assert bool(checagens.conferir_conteudo_sem_o_dado(texto, cliente, acao="responder")) is duro
    # Com a resposta da equipe do mesmo produto citada, não segura (o verificador confere).
    assert not checagens.conferir_conteudo_sem_o_dado(
        texto, cliente, acao="responder", pares_de_produto=["par:1"]
    )


async def test_o_caminho_completo_avisa_sem_segurar_e_segura_o_duro():
    # O andamento sem lastro: aviso (o verificador confere); o prazo que nada sustenta: duro.
    aviso = await _completo(
        "Olá! Seu pedido já está na doca aguardando o caminhão.",
        categoria="prazo_envio",
        cliente="já postou?",
        pedido=SAIU,
    )
    assert not _sai(aviso) and not _segura(aviso) and aviso.avisos
    duro = await _completo(
        "Olá! Seu pedido chega essa semana.", categoria="rastreio", cliente="quando chega?"
    )
    assert _segura(duro)


# ─────────────── 4. D1: o turno informativo de outro assunto ───────────────


def test_d1_o_turno_informativo_de_outro_assunto_responde_e_marca():
    assert acao_pessoa.turno_informativo_da_devolucao(
        ["Obrigado. Vocês vendem também capinha para ele?"]
    )
    assert acao_pessoa.turno_informativo_da_devolucao(["Tô esperando o reembolso"])
    # O pedido, a decisão, o problema e a ameaça continuam fora.
    for fala in ("Vocês podem aceitar a minha devolução?", "veio quebrado", "vou no Procon"):
        assert not acao_pessoa.turno_informativo_da_devolucao([fala]), fala


def test_d1_a_rede_da_etiqueta_respeita_a_marca_e_o_chamado_segura():
    conversa = SimpleNamespace(etiqueta=constantes.ETIQUETA_DEVOLUCAO, etiquetas_secundarias=[])

    def _res(**agente: Any) -> SimpleNamespace:
        return SimpleNamespace(
            conferida=SimpleNamespace(motivos=[]),
            fatos_agente=dict(agente),
            precisa_humano=False,
            motivo="",
        )

    base = SimpleNamespace(ctx={"chamados": [], "devolucoes": [{"status": "em andamento"}]})
    marcado = _res(responde_e_marca=True)
    ia._andamento_pela_etiqueta(marcado, conversa, base)
    assert not marcado.precisa_humano  # 8d9e76e0 / 43979d0f
    sem = _res()
    ia._andamento_pela_etiqueta(sem, conversa, base)
    assert sem.precisa_humano and checagens.EM_ANDAMENTO in sem.fatos_agente["checagens"]
    com_chamado = SimpleNamespace(ctx={"chamados": [{"status": "aberto"}], "devolucoes": []})
    chamado = _res(responde_e_marca=True)
    ia._andamento_pela_etiqueta(chamado, conversa, com_chamado)
    assert chamado.precisa_humano


# ─────────────── 5. o cumprimento espera o reenvio (o 06:53 da Kia) ───────────────


def test_o_cumprimento_do_agente_e_reconhecido():
    assert ia.resposta_generica_do_agente(
        SimpleNamespace(texto="Olá! Como podemos ajudar com o Apple Watch Series 11?")
    )
    assert not ia.resposta_generica_do_agente(SimpleNamespace(texto="O seu pedido foi postado."))
    assert ia.MOTIVO_GENERICA_ESPERA == "generica_espera_o_turno"


# ─────────────── 6. o verificador v14: os avisos na entrada ───────────────


def _entrada(avisos: tuple[str, ...]) -> str:
    resumo = verificador.ResumoDoPacote(
        plataforma="Shopee", agora="27/10/2026 10:00", perguntas=["tem NFC?"], conversa=[],
        evidencias=[], avisos=avisos,
    )  # fmt: skip
    return verificador.montar_entrada(
        acao="responder", acao_pessoa=None, texto="Sim, tem NFC.", resumo=resumo
    )


def test_v14_os_avisos_vao_na_entrada_sem_o_fecho_de_politica():
    assert verificador.VERSAO == "v15"
    assert "AVISOS DAS CHECAGENS" not in _entrada(())
    e = _entrada(("afirma o NFC ('sim') sem fonte — no automático, pessoa confere",))
    assert verificador.CABECALHO_AVISOS in e
    assert "- afirma o NFC ('sim') sem fonte\n" in e and "pessoa confere" not in e
    # O SISTEMA diz que o aviso é dica, não veredito.
    assert "Cada aviso é uma DICA, não um veredito" in verificador.SISTEMA
    assert "«vale para produto só no modelo nomeado na resposta»" in verificador.SISTEMA
    assert "«Tudo bem, sim!»" in verificador.SISTEMA


def test_v14_o_par_do_mesmo_anuncio_vale_para_o_modelo_nomeado():
    (linha,) = verificador.linhas_dos_pares(
        [{"id": "par:1", "mesma_loja": True, "mesmo_anuncio": True, "fonte_de_produto": False,
          "pergunta": "qual tem nfc?", "resposta": "o modelo p1 Pro tem nfc!", "idade_dias": 3}],
        (),
    )[1:]  # fmt: skip
    assert verificador.VALE_PRODUTO_NOMEADO in linha


def test_os_assuntos_liberados_trazem_o_de_produto_do_auto_categorias():
    canal = SimpleNamespace(
        auto_orientacao=[], auto_categorias=["rastreio", "espec_celular"], auto_agente=["rastreio"]
    )
    assert verificador.assuntos_liberados(canal) == ["rastreio", "espec_celular"]


# ─────────────── 7. o re-gatilho offline ───────────────

PACOTE = "\n".join(
    [
        "<dados>",
        'CABEÇALHO: {"agora_sao_paulo":"26/10/2026 10:00","canal":"chat","plataforma":"Shopee"}',
        "PEDIDO DA CONVERSA: "
        + json.dumps({**SAIU, "numero": "{numero_pedido}", "itens": []}, ensure_ascii=False),
        'EVIDÊNCIAS DESTES DADOS (ids): ["pedido"]',
        "COMO A EQUIPE JÁ RESPONDEU CASOS PARECIDOS (x):",
        "- par:7 (esta loja, mesmo anúncio «Capa X», vale para produto, há 3 dias): "
        "cliente «vem película?» → equipe «vai sem película!»",
        "</dados>",
    ]
)


def _registro(texto: str, **kw: Any) -> dict:
    return {
        "id": "r1",
        "integration_id": "2ac2633b-c698-4718-99cb-6e5443a80689",
        "plataforma": "shopee",
        "parada": "fim",
        "decisao": {"acoes_codigo": []},
        "proposta": {
            "acao": "responder",
            "acao_pessoa": None,
            "assunto": "rastreio",
            "confianca": 0.9,
            "evidencias": ["pedido"],
            "motivo_pessoa": "",
            "texto": texto,
        },  # fmt: skip
        "texto_final": texto,
        "resposta_real": "ok!",
        "rastro": {"pacote": PACOTE, "transcricao": ["[26/10 09:59] Cliente: cadê meu pedido?"]},
        **kw,
    }


def test_regatilho_le_as_secoes_do_pacote():
    sec = regatilho.secoes(PACOTE)
    assert sec["PEDIDO DA CONVERSA"]["postado"] == SAIU["postado"]
    (par,) = sec["pares"]
    assert par["id"] == "par:7" and par["fonte_de_produto"] and par["mesmo_anuncio"]
    (m,) = regatilho.mensagens(["[26/10 09:59] Cliente: 6 (opção do menu: Falar com Atendente)"],
                               regatilho._agora(sec["CABEÇALHO"]))  # fmt: skip
    assert m.autor == constantes.AUTOR_CLIENTE and m.texto == "6"


async def test_regatilho_decide_sem_modelo():
    canal = {"modo": "auto", "auto_categorias": ["rastreio"], "auto_agente": ["rastreio"],
             "auto_orientacao": []}  # fmt: skip
    bom = await regatilho.regatilhar(
        _registro("O seu pedido {numero_pedido} já foi postado; acompanhe em Minhas Compras."),
        canal,
    )
    assert bom["decisao"] == regatilho.CHEGA, bom
    pix = await regatilho.regatilhar(_registro("Me passa a sua chave Pix que eu devolvo."), canal)
    assert pix["decisao"].startswith("pessoa:") and pix["bloqueios"]
    fora = await regatilho.regatilhar(_registro("O seu pedido já foi postado."), None)
    assert fora["decisao"] == "pessoa:canal_fora_do_auto"
    res = regatilho.resumo([bom, pix, fora])
    assert res["agora_chega_ao_verificador"] == 1 and res["total"] == 3


def test_regatilho_a_linha_da_calibracao_vira_registro():
    caso = {
        "id": "c1", "acao": "responder", "texto": "Olá!", "perguntas": ["tem o preto?"],
        "conversa": [], "evidencias": [{"item_id": "1", "titulo": "Capa", "variacoes": {
            "completas": True, "variacoes": [{"nome": "Preto", "disponivel": False}]}}],
    }  # fmt: skip
    r = regatilho.registro_da_calibracao(caso)
    sec = regatilho.secoes(r["rastro"]["pacote"])
    assert sec["PRODUTO"][0]["variacoes"] == [{"nome": "Preto", "disponivel": False}]
    assert r["rastro"]["transcricao"][-1].endswith("Cliente: tem o preto?")


# ─────────────── 8. v15: as frases-padrão da loja na Shopee ───────────────

AIR_110_ESGOTADA = {
    "item_id": "1",
    "titulo": "Air Fryer 110v 220v",
    "variacoes": [
        {"nome": "110v", "disponivel": False, "confiavel": True},
        {"nome": "220v", "disponivel": True, "confiavel": True},
    ],
}
FATO_26 = {
    "fatos": [{"id": "fato:26", "texto": "No momento não trabalhamos com capinha nem película."}]
}


def _entrada_shopee(evidencias, texto, perguntas, plataforma="shopee") -> str:
    resumo = verificador.ResumoDoPacote(
        plataforma="Shopee", agora="27/10/2026 10:00", perguntas=perguntas, conversa=[],
        evidencias=[json.dumps(e, ensure_ascii=False) for e in evidencias],
        plataforma_codigo=plataforma,
    )  # fmt: skip
    return verificador.montar_entrada(
        acao="responder", acao_pessoa=None, texto=texto, resumo=resumo, assunto="estoque_variacao"
    )


def test_v15_as_frases_padrao_vao_so_na_shopee():
    assert verificador.VERSAO == "v15"
    e = _entrada_shopee([], "Olá!", ["oi"])
    assert verificador.CABECALHO_FRASES_PADRAO in e
    for frase in verificador.FRASES_PADRAO_SHOPEE:
        assert f"- {frase}" in e
    assert verificador.FRASE_PADRAO_REPOSICAO not in e
    assert verificador.CABECALHO_FRASES_PADRAO not in _entrada_shopee([], "Olá!", ["oi"], "ml")
    # O SISTEMA diz o que a seção vale e que a empatia não é afirmação.
    assert "FRASES-PADRÃO DA LOJA NESTA PLATAFORMA (quando vier)" in verificador.SISTEMA
    assert "empatia e acolhimento" in verificador.SISTEMA


def test_v15_a_reposicao_so_com_a_opcao_esgotada_nas_evidencias():
    texto = "A 110v está esgotada e não temos previsão de reposição."
    com = _entrada_shopee([AIR_110_ESGOTADA], texto, ["tem previsão da 110v?"])
    assert verificador.FRASE_PADRAO_REPOSICAO in com
    disponivel = {
        **AIR_110_ESGOTADA,
        "variacoes": [{"nome": "110v", "disponivel": True, "confiavel": True}],
    }
    sem = _entrada_shopee([disponivel], texto, ["tem previsão da 110v?"])
    assert verificador.FRASE_PADRAO_REPOSICAO not in sem


def test_v15_o_acessorio_so_com_o_fato_que_nega_a_venda():
    com = _entrada_shopee([FATO_26], "Não temos capinha.", ["vende capinha?"])
    assert "a loja não tem anúncio de capinha nem película" in com
    sem = _entrada_shopee([], "Não temos capinha.", ["vende capinha?"])
    assert "a loja não tem anúncio de" not in sem


def test_a_regiao_sem_dado_no_estado_que_o_cliente_cita_e_dura():
    p = checagens._suave(
        checagens.REGIAO_SEM_FONTE,
        "afirma a entrega no estado que o cliente citou sem dado de restrição",
    )
    assert checagens.pendencia_dura(p)
    aviso = checagens._suave(
        checagens.REGIAO_SEM_FONTE, "afirma a entrega sem citar a restrição do anúncio (RJ)"
    )
    assert not checagens.pendencia_dura(aviso)


def test_a_calibracao_tem_o_grupo_das_frases_padrao():
    from scripts.agente_eval import calibrar_verificador as calibrar

    casos = [c for c in calibrar.ler_casos(calibrar.CASOS_PADRAO) if c["grupo"] == "frases_padrao"]
    boas = [c for c in casos if c["esperado"]["ok_para_envio_automatico"]]
    ruins = [c for c in casos if not c["esperado"]["ok_para_envio_automatico"]]
    assert len(boas) >= 8 and len(ruins) >= 6
    assert all(verificador.CABECALHO_FRASES_PADRAO in calibrar.entrada_do_caso(c) for c in casos)
