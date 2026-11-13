"""A VIRADA do banco de respostas (13/11/2026): "não existirá mais esse 'precisa de pessoa'".

O Eduardo: "ele responde se tiver algo parecido no banco mesmo sendo, entre aspas, ação de
pessoa"; "ele só não responde quando realmente não conseguir — tem muito contexto de conversas
delicadas no banco e, se bater e for parecido, ele responde normal como qualquer outra
conversa"; "de qualquer forma ele sempre deixará uma sugestão — agora a parte de sugestão é o
próprio chat". Aqui:
- a resposta do banco no pacote leva o TIPO (sem ele, o "bateu com a do banco" nunca rodava);
- o rascunho que pede pessoa e bate pelo sentido responde e marca (menos instrução, NF e
  endereço), e o portão não para no assunto só de pessoa nem no "em andamento";
- a triagem só segura o rascunho que bateu pelo que nunca sai sozinho;
- o sistema da virada: o texto nunca vazio, a cortesia no agradecimento, o banco como dado.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

from app.services.atendimento import constantes, ia
from app.services.atendimento.agente import checagens, confere_banco, pacote, prompt, saida
from app.services.atendimento.agente import contexto as agente_ctx


def _ctx(**campos: Any) -> agente_ctx.ContextoAgente:
    ctx = agente_ctx.ContextoAgente(
        conversa_id="c",
        gatilho_id=None,
        canal_id=None,
        integration_id=None,
        plataforma="shopee",
        canal="chat",
        modo="auto",
        nomes=("Maria Souza",),
    )
    ctx.regua = True
    ctx.banco_exigido = True
    ctx.registrar_pares(
        [
            {
                "id": "par:b1",
                "tipo": "prazo_antes_compra",
                "pergunta": "a loja é de qual estado?",
                "resposta": "Enviamos de SP!",
            },
            {
                "id": "par:b2",
                "tipo": "defeito",
                "pergunta": "o aparelho desliga sozinho",
                "resposta": "Nem molhou? Manda um vídeo curto do problema por aqui.",
            },
        ]
    )
    for k, v in campos.items():
        setattr(ctx, k, v)
    return ctx


class _Cliente:
    def __init__(self, veredito: str, caso: str | None = "par:b1"):
        self.messages = self
        self.veredito = veredito
        self.caso = caso if veredito in ("mesma", "serve") else None
        self.pedidos: list[dict] = []

    async def create(self, **kwargs):
        self.pedidos.append(kwargs)
        return SimpleNamespace(
            content=[
                SimpleNamespace(
                    type="text",
                    text=json.dumps(
                        {"id": self.caso, "veredito": self.veredito, "motivo": "igual"}
                    ),
                )
            ],
            stop_reason="end_turn",
            model=kwargs["model"],
            usage=SimpleNamespace(
                input_tokens=300,
                output_tokens=20,
                cache_read_input_tokens=0,
                cache_creation_input_tokens=0,
            ),
        )


def _proposta(**campos: Any) -> saida.Proposta:
    base = {
        "acao": "pessoa",
        "texto": "Enviamos de SP!",
        "assunto": "prazo_antes_compra",
        "confianca": 0.8,
        "acao_pessoa": constantes.ACAO_DADO_INDISPONIVEL,
        "motivo_pessoa": "confirmar de onde sai",
        "evidencias": ["par:b1"],
    }
    return saida.Proposta(**{**base, **campos})


async def _soltar(ctx, p: saida.Proposta, cliente, acoes=None) -> bool:
    acoes = list(acoes if acoes is not None else [*ctx.acoes_codigo, *saida.acoes_do_modelo(p)])
    return await confere_banco.solta_a_pessoa(
        ctx,
        texto=p.texto,
        categoria=p.assunto,
        acoes=acoes,
        evidencias=saida.evidencias(p),
        pergunta="Essa empresa é de qual estado?",
        cliente=cliente,
        modelo="claude-opus-5-5",
    )


# ── O rascunho que pede pessoa e bate com o banco responde e marca ──────────────


async def test_bateu_com_o_banco_a_pessoa_vira_marca_de_depois_do_envio():
    ctx = _ctx(acoes_codigo=[constantes.ACAO_PEDIU_PESSOA])
    p = _proposta()
    cliente = _Cliente("mesma")
    assert await _soltar(ctx, p, cliente)
    assert ctx.banco_bateu == "par:b1" and ctx.responde_e_marca is True
    # A proposta "pessoa" vira "responder" e a ação fica para depois.
    nova, depois = checagens.responde_e_marca_da_proposta(p, ctx)
    assert nova.acao == "responder" and nova.acao_pessoa is None
    assert depois == [constantes.ACAO_DADO_INDISPONIVEL]
    ctx.acoes_modelo = depois
    assert ctx.acoes_pessoa == []
    fatos = ctx.para_fatos()
    assert fatos["banco_bateu"] == "par:b1" and fatos["responde_e_marca"] is True
    assert set(fatos["acoes_pos_envio"]) == {
        constantes.ACAO_PEDIU_PESSOA,
        constantes.ACAO_DADO_INDISPONIVEL,
    }
    # A conferência leu a pergunta que a equipe respondeu, com o nome mascarado.
    (pedido,) = cliente.pedidos
    conteudo = str(pedido["messages"])
    assert "par:b1 | cliente: «a loja é de qual estado?» | equipe: «Enviamos de SP!»" in conteudo
    assert "Maria Souza" not in conteudo
    # A conferência de depois do verificador reaproveita o registro (sem chamar de novo).
    assert (
        await confere_banco.conferir(
            None, ctx, categoria="x", texto="y", pergunta="z", cliente=cliente, modelo="m"
        )
    )["banco_confere"]["veredito"] == "mesma"
    assert len(cliente.pedidos) == 1


async def test_diferente_continua_pessoa():
    ctx = _ctx()
    p = _proposta()
    assert not await _soltar(ctx, p, _Cliente("diferente"))
    assert ctx.banco_bateu is None and ctx.responde_e_marca is False
    assert ctx.banco_confere["veredito"] == "diferente"
    nova, depois = checagens.responde_e_marca_da_proposta(p, ctx)
    assert nova.acao == "pessoa" and depois == []


async def test_o_banco_nao_solta_instrucao_nf_nem_endereco_nem_fora_da_virada():
    for acao in (
        constantes.ACAO_INSTRUCAO,
        constantes.ACAO_ENVIAR_ARQUIVO_NF,
        constantes.ACAO_ALTERAR_ENDERECO,
    ):
        cliente = _Cliente("mesma")
        ctx = _ctx()
        assert not await _soltar(ctx, _proposta(acao_pessoa=acao), cliente)
        assert cliente.pedidos == [], acao
        # A do código também segura.
        ctx2 = _ctx(acoes_codigo=[acao])
        assert not await _soltar(ctx2, _proposta(), _Cliente("mesma"))
    fora = _ctx(regua=False)
    assert not await _soltar(fora, _proposta(), _Cliente("mesma"))
    # Sem texto não há o que conferir.
    assert not await _soltar(_ctx(), _proposta(texto=""), _Cliente("mesma"))


def test_depois_do_envio_so_com_a_marca_do_banco():
    ctx = _ctx(acoes_codigo=[constantes.ACAO_SEGURAR_ENVIO, constantes.ACAO_ENVIAR_ARQUIVO_NF])
    assert ctx.acoes_pessoa == [constantes.ACAO_SEGURAR_ENVIO, constantes.ACAO_ENVIAR_ARQUIVO_NF]
    ctx.banco_bateu = "par:b1"
    # A NF que o sistema não anexou continua segurando.
    assert ctx.acoes_pessoa == [constantes.ACAO_ENVIAR_ARQUIVO_NF]
    assert ctx.acoes_pos_envio == [constantes.ACAO_SEGURAR_ENVIO]


# ── O portão: bateu com o banco, o assunto e o "em andamento" não param ──────────


def _rascunho(categoria: str, agente: dict, **fatos: Any) -> SimpleNamespace:
    return SimpleNamespace(
        categoria=categoria,
        texto="texto",
        prompt_versao=constantes.PROMPT_VERSAO_AGENTE,
        fatos={"agente": {"modo": "auto", "acoes_pessoa": [], **agente}, **fatos},
    )


def _bateu(**extra: Any) -> dict:
    return {
        "regua": True,
        "banco_exigido": True,
        "banco_tipos": {"par:b1": "reembolso"},
        "banco_confere": {"id": "par:b1", "veredito": "mesma"},
        **extra,
    }


def test_bateu_solta_o_assunto_sensivel_so_de_pessoa_e_o_andamento():
    canal = SimpleNamespace(auto_categorias=[], auto_agente=[], auto_orientacao=[])
    # Sensível de orientação fora da lista.
    assert ia.trava_do_agente(_rascunho("reembolso", _bateu()), canal) is None
    sem = _rascunho("reembolso", {**_bateu(), "banco_confere": None})
    assert ia.trava_do_agente(sem, canal) == "orientacao_nao_liberada"
    # Só de pessoa (reclamação forte) e a devolução em andamento.
    assert ia.trava_do_agente(_rascunho("reclamacao_forte", _bateu()), canal) is None
    com_devolucao = _rascunho("reembolso", _bateu(), devolucoes=1)
    assert ia.trava_do_agente(com_devolucao, canal) is None
    assert (
        ia.trava_do_agente(
            _rascunho("reembolso", {**_bateu(), "banco_confere": None}, devolucoes=1), canal
        )
        == "em_andamento"
    )


def test_bateu_nao_solta_o_conteudo_nem_a_acao_nem_o_copiloto():
    canal = SimpleNamespace(auto_categorias=[], auto_agente=[], auto_orientacao=[])
    assert ia.trava_do_agente(_rascunho("reembolso", _bateu(avisos_auto=["x"])), canal) == (
        "avisos_auto"
    )
    assert ia.trava_do_agente(_rascunho("reembolso", _bateu(sem_auto=True)), canal) == "sem_auto"
    assert ia.trava_do_agente(_rascunho("reembolso", _bateu(modo="copiloto")), canal) == (
        "gerado_no_copiloto"
    )
    acao = _rascunho("reembolso", _bateu())
    acao.fatos["agente"]["acoes_pessoa"] = [constantes.ACAO_ENVIAR_ARQUIVO_NF]
    assert ia.trava_do_agente(acao, canal) == "acao_pessoa"
    assert ia.trava_do_agente(_rascunho("reembolso", _bateu()), canal, ligado=False) == (
        "agente_desligado"
    )


def test_a_triagem_so_segura_o_que_nunca_sai_sozinho():
    motivos = (
        constantes.HUMANO_RECLAMACAO,
        constantes.HUMANO_DEVOLUCAO,
        constantes.HUMANO_ALERTA,
        constantes.HUMANO_ACAO_PESSOA,
        constantes.HUMANO_ASSUNTO,
        constantes.HUMANO_INSTRUCAO,
        constantes.HUMANO_OFERTA_BRINDE,
    )
    assert ia.motivos_que_o_banco_deixa(motivos, [constantes.ACAO_ANALISAR_EVIDENCIA]) == (
        constantes.HUMANO_INSTRUCAO,
        constantes.HUMANO_OFERTA_BRINDE,
    )
    # A ação do turno que o banco não solta (a NF) continua segurando.
    assert constantes.HUMANO_ACAO_PESSOA in ia.motivos_que_o_banco_deixa(
        motivos, [constantes.ACAO_ENVIAR_ARQUIVO_NF]
    )
    r = _rascunho("reembolso", _bateu())
    assert ia.bateu_com_o_banco_no_rascunho(r)
    assert not ia.bateu_com_o_banco_no_rascunho(_rascunho("reembolso", {"regua": True}))
    assert not ia.bateu_com_o_banco_no_rascunho(None)


# ── O pacote e o sistema da virada ───────────────────────────────────────────────


def test_o_pacote_da_virada_mostra_mais_respostas_como_dado():
    pares = [
        {
            "id": f"par:b{i}",
            "pergunta": "pergunta " * 6,
            "resposta": "resposta da equipe " * 6,
            "mesma_loja": True,
        }
        for i in range(10)
    ]
    texto, cabem = pacote.secao_da_equipe(pares)
    texto_b, cabem_b = pacote.secao_da_equipe(pares, banco=True)
    assert texto.startswith(pacote.EQUIPE_CABECALHO)
    assert texto_b.startswith(pacote.BANCO_CABECALHO) and "Valem como DADO" in texto_b
    assert len(cabem_b) > len(cabem)
    assert pacote.MAX_PARES_BANCO == 8


def test_o_sistema_da_virada_nunca_deixa_a_caixa_vazia():
    antes, depois = prompt.bloco_geral(), prompt.bloco_geral(virada=True)
    for velho, novo in prompt.TROCAS_DA_VIRADA:
        assert antes.count(velho) == 1, velho[:40]
        assert novo in depois
        if velho not in novo:  # a troca que só acrescenta mantém a frase antiga
            assert velho not in depois
    assert 'texto "" (vazio)' not in depois
    assert "só o TOM" not in depois
    assert "De nada, tenha um ótimo dia!" in depois
    for velho, novo in prompt.TROCAS_DA_VIRADA_NA_PLATAFORMA:
        assert prompt.bloco_da_plataforma("shopee", []).count(velho) == 1
        assert novo in prompt.bloco_da_plataforma("shopee", [], virada=True)
    # Sem a virada, o sistema de sempre (e o hash dele).
    assert prompt.sistema("shopee", [])[0]["text"] == antes
    assert prompt.hash_do_sistema("shopee", []) != prompt.hash_do_sistema("shopee", [], virada=True)


def test_a_regua_nunca_vale_no_e_mail_nem_fora_do_banco(monkeypatch):
    """O agente de e-mail (SAC dos sites, Amazon) segue as regras dele: a marca da régua nunca
    vai ao rascunho do e-mail (o "atendente + Procon" continua segurando lá)."""
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "atendimento_regua_ativa", True)
    assert agente_ctx.regua_vale_para("shopee", "chat")
    assert not agente_ctx.regua_vale_para("site", "email")
    assert not agente_ctx.regua_vale_para("amazon", constantes.CANAL_EMAIL)
    assert not agente_ctx.regua_vale_para("whatsapp", "chat")
    monkeypatch.setattr(get_settings(), "atendimento_regua_ativa", False)
    assert not agente_ctx.regua_vale_para("shopee", "chat")


def test_bateu_com_o_banco_os_motivos_de_estado_viram_aviso():
    from app.services.atendimento.agente.checagens import motivo_que_o_banco_solta

    soltos = [
        "pedido com chamado ou devolução",
        "reclamação ou devolução aberta na plataforma",
        ia.MOTIVO_RESPOSTA_DE_GARANTIA,
        ia.MOTIVO_RECUSA_DE_DIREITO,
        "o cliente avaliou mal a loja",
        "cliente insistiu em falar com uma pessoa (3º pedido seguido)",
        "ação de pessoa: Procon, Justiça, golpe ou ofensa — o cliente ameaçou",
        "ação de pessoa: Segurar o envio (cancelamento antes da postagem)",
    ]
    assert all(motivo_que_o_banco_solta(m) for m in soltos)
    presos = [
        "ação de pessoa: Enviar o arquivo da NF — anexar",
        "ação de pessoa: Alterar o endereço antes da postagem",
        "ação de pessoa: Texto com cara de instrução",
        'manda para fora da plataforma ("nosso endereço")',
        "promete o que só pessoa cumpre",
    ]
    assert not any(motivo_que_o_banco_solta(m) for m in presos)


def test_separar_avisos_na_virada():
    from app.services.atendimento.agente.checagens import separar_avisos

    conferida = SimpleNamespace(
        texto="Pode devolver pelo app.",
        motivos=[
            "reclamação ou devolução aberta na plataforma",
            "cliente insistiu em falar com uma pessoa",
            'manda para fora da plataforma ("nosso endereço")',
        ],
        avisos=[],
        sem_auto=[],
        precisa_humano=True,
    )
    import dataclasses as _dc

    @_dc.dataclass
    class _C:
        texto: str
        motivos: list
        avisos: list
        sem_auto: list
        precisa_humano: bool

    c = _C(**vars(conferida))
    so_regua = separar_avisos(c, modo="auto", pessoa_do_modelo=False, regua=True)
    assert "cliente insistiu em falar com uma pessoa" in so_regua.avisos
    assert "reclamação ou devolução aberta na plataforma" in so_regua.motivos
    bateu = separar_avisos(c, modo="auto", pessoa_do_modelo=False, banco_bateu=True, regua=True)
    assert bateu.motivos == ['manda para fora da plataforma ("nosso endereço")']
    assert bateu.precisa_humano is True  # a regra de texto continua dura
    sem = _C(**{**vars(conferida), "motivos": conferida.motivos[:2]})
    assert (
        separar_avisos(sem, modo="auto", pessoa_do_modelo=False, banco_bateu=True).precisa_humano
        is False
    )


async def test_uma_chamada_com_todos_os_casos_e_o_id_fora_nao_vale():
    ctx = _ctx()
    p = _proposta()
    cliente = _Cliente("mesma", caso="par:b2")
    assert await _soltar(ctx, p, cliente)
    assert ctx.banco_bateu == "par:b2"
    (pedido,) = cliente.pedidos  # uma chamada só, com os dois casos
    conteudo = str(pedido["messages"])
    assert "- par:b1 |" in conteudo and "- par:b2 |" in conteudo
    inventado = _ctx()
    assert not await _soltar(inventado, _proposta(), _Cliente("mesma", caso="par:b99"))
    assert inventado.banco_confere["veredito"] == "diferente"


def test_a_correcao_da_pessoa_vem_antes_na_busca():
    """O Eduardo: alguém corrigiu a sugestão — na próxima parecida, ele acerta."""
    from app.services.atendimento import banco_respostas as b

    comum = {"mesmo_tipo": True, "mesmo_modelo": False, "mesmo_anuncio": False, "mesma_loja": True}
    escreveu = b.nota_banco(0.5, idade_dias=1, **comum)
    corrigiu = b.nota_banco(0.5, idade_dias=1, correcao=True, **comum)
    assert corrigiu > escreveu
    # A correção de 1 mês ainda vem antes da resposta de ontem com o mesmo texto.
    assert b.nota_banco(0.5, idade_dias=30, correcao=True, **comum) > escreveu


async def test_o_rascunho_tambem_busca_no_banco(monkeypatch):
    """A resposta parecida que a equipe já deu se acha pelo RASCUNHO (o índice do banco cobre a
    pergunta e a resposta): ela entra entre as candidatas e o portão a conhece."""
    from app.services.atendimento import banco_respostas

    base = {"mesmo_anuncio": False, "mesmo_modelo": False, "item_id": None, "idade_dias": 3}
    achados = [
        {
            "id": "b77",
            "pergunta": "a loja fica onde?",
            "resposta": "Enviamos de São Paulo para todo o Brasil!",
            "tipo": "indefinido",
            "mesma_loja": True,
            **base,
        },
        {  # outra loja, e não é da ficha do produto: não entra
            "id": "b78",
            "pergunta": "a loja fica onde?",
            "resposta": "Enviamos do Paraná para todo o Brasil!",
            "tipo": "indefinido",
            "mesma_loja": False,
            **base,
        },
    ]

    async def falso(session, **kw):
        return achados

    monkeypatch.setattr(banco_respostas, "buscar", falso)
    ctx = _ctx()
    cliente = _Cliente("mesma", caso="par:b77")
    assert await confere_banco.solta_a_pessoa(
        ctx,
        texto="Enviamos de SP!",
        categoria="prazo_antes_compra",
        acoes=[constantes.ACAO_DADO_INDISPONIVEL],
        evidencias=[],
        pergunta="de onde vocês enviam?",
        cliente=cliente,
        modelo="m",
        session=object(),
    )
    assert ctx.banco_bateu == "par:b77" and ctx.banco_pelo_rascunho == ["par:b77"]
    assert ctx.para_fatos()["banco_tipos"]["par:b77"] == "indefinido"
    assert ia.bateu_com_o_banco(ctx.para_fatos(), "prazo_antes_compra", "Enviamos de SP!")
    conteudo = str(cliente.pedidos[0]["messages"])
    assert "par:b77" in conteudo and "par:b78" not in conteudo


def test_na_virada_a_origem_da_equipe_vai_ao_pacote_e_prova_a_origem():
    """ "Essa empresa é de qual estado?": a equipe da MESMA loja responde "enviamos de SP" —
    na virada essa resposta vai ao pacote e, citada, prova a origem."""
    from app.services.atendimento.agente import pares as pares_svc

    assert not pares_svc.serve_ao_pacote("Enviamos de SP para todo o Brasil!")
    assert pares_svc.serve_ao_pacote("Enviamos de SP para todo o Brasil!", virada=True)
    # A promessa continua fora, com ou sem a virada.
    assert not pares_svc.serve_ao_pacote("Vamos verificar e enviaremos amanhã!", virada=True)
    ctx = _ctx()
    ctx.pares_do_pacote["par:b1"]["mesma_loja"] = True
    ctx.evidencias = ["par:b1"]
    assert checagens._origem_provada(None, ctx)
    ctx.pares_do_pacote["par:b1"]["mesma_loja"] = False  # de outra loja não prova
    assert not checagens._origem_provada(None, ctx)


def test_o_reset_negado_nao_e_sugestao_de_reset():
    """Victor, 13/11: "se o aparelho não dá nenhum sinal de vida, não precisa tentar o reset"
    segurava como "sugere reset ou formatação"."""
    from app.services.atendimento import validador

    nao = (
        "Se o aparelho não dá nenhum sinal de vida, não precisa tentar o reset.",
        "Não precisa formatar o aparelho.",
        "Sem fazer o reset, deixe carregando por 2 horas.",
    )
    sim = (
        "Faça um reset forçado segurando os botões.",
        "Se não ligar, faça o reset de fábrica.",
        "Não esqueça de fazer o reset depois de atualizar.",
        "Não deixe de fazer o reset.",
        "Tente restaurar as configurações de fábrica.",
    )
    assert not any(validador.sugere_reset(t) for t in nao)
    assert all(validador.sugere_reset(t) for t in sim)


def test_pedido_aguardando_pagamento_nao_pede_segurar_o_envio():
    """Mini, 13/11: "cancela essa compra" com o cartão "aguardando pagamento" (o pedido fora do
    sistema, com o número na conversa) pedia "segurar o envio" — não há envio a segurar."""
    from app.services.atendimento.agente import acao_pessoa

    def cartao(status):
        return SimpleNamespace(
            anexos=[{"tipo": "pedido", "pedido": "2611136DF7CD2X", "status": status}],
            payload={},
        )

    conversa = SimpleNamespace(canal="chat", pedido_marketplace="2611136DF7CD2X")
    assert not acao_pessoa.pode_nao_ter_saido(None, conversa, ctx={}, recentes=[cartao("UNPAID")])
    # Pago (o pedido no sistema) ou pronto para enviar: segura como sempre.
    assert acao_pessoa.pode_nao_ter_saido(
        None, conversa, ctx={}, recentes=[cartao("READY_TO_SHIP")]
    )
    assert acao_pessoa.pode_nao_ter_saido(
        None, conversa, ctx={"pedido": {"x": 1}}, recentes=[cartao("UNPAID")]
    )


def test_a_oferta_do_brinde_so_trava_a_resposta_que_fala_do_brinde():
    """O Eduardo, 13/11: "sinto que está mandando coisa pro humano que ele pode fazer" — a oferta
    da AirTag prendia a conversa por 7 dias; na virada, só a resposta que fala do brinde."""
    base = {"regua": True, "modo": "auto", "acoes_pessoa": []}

    def r(texto, agente=base):
        return SimpleNamespace(
            texto=texto,
            categoria="cancelamento",
            prompt_versao=constantes.PROMPT_VERSAO_AGENTE,
            fatos={"agente": dict(agente)},
        )

    assert ia.brinde_nao_trava(r("Você pode cancelar pelo próprio pedido, em Minhas Compras."))
    assert not ia.brinde_nao_trava(r("O brinde vai junto com o seu celular."))
    assert not ia.brinde_nao_trava(r("A AirTag de presente segue no pedido."))
    # Fora da virada (sem a régua), como antes: trava.
    assert not ia.brinde_nao_trava(r("Pode cancelar pelo app.", {"modo": "auto"}))
    assert not ia.brinde_nao_trava(None)


async def test_mesma_pergunta_a_resposta_da_equipe_vira_a_sugestao():
    """O Eduardo, 13/11: "tem respostas suficientes no banco". A mesma pergunta de um caso do
    banco, com a resposta da equipe servindo a este cliente: ela vira a sugestão."""
    ctx = _ctx()
    p = _proposta(texto="Não temos essa informação no anúncio.")
    assert await _soltar(ctx, p, _Cliente("serve", caso="par:b1"))
    assert ctx.texto_do_banco == "Enviamos de SP!" and ctx.banco_bateu == "par:b1"
    assert ctx.para_fatos()["texto_do_banco"] is True
    assert ia.bateu_com_o_banco(
        {**ctx.para_fatos(), "banco_confere": ctx.banco_confere}, "prazo_antes_compra", "x"
    )
    # A resposta da equipe com máscara de dado (código, data de outro pedido) não serve.
    mascarada = _ctx()
    mascarada.pares_do_pacote["par:b1"]["resposta"] = "Seu código de rastreio é [CODIGO]."
    assert not await _soltar(mascarada, p, _Cliente("serve", caso="par:b1"))
    assert mascarada.texto_do_banco is None
    assert mascarada.banco_confere["veredito"] == "diferente"
