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
    def __init__(self, veredito: str):
        self.messages = self
        self.veredito = veredito
        self.pedidos: list[dict] = []

    async def create(self, **kwargs):
        self.pedidos.append(kwargs)
        return SimpleNamespace(
            content=[
                SimpleNamespace(
                    type="text", text=f'{{"veredito": "{self.veredito}", "motivo": "igual"}}'
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
    assert "PERGUNTA QUE A EQUIPE RESPONDEU: a loja é de qual estado?" in conteudo
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
    antes, depois = prompt.bloco_geral(), prompt.bloco_geral(True)
    for velho, novo in prompt.TROCAS_DA_VIRADA:
        assert antes.count(velho) == 1, velho[:40]
        assert velho not in depois and novo in depois
    assert 'texto "" (vazio)' not in depois
    assert "só o TOM" not in depois
    assert "De nada, tenha um ótimo dia!" in depois
    for velho, novo in prompt.TROCAS_DA_VIRADA_NA_PLATAFORMA:
        assert prompt.bloco_da_plataforma("shopee", []).count(velho) == 1
        assert novo in prompt.bloco_da_plataforma("shopee", [], True)
    # Sem a virada, o sistema de sempre (e o hash dele).
    assert prompt.sistema("shopee", [])[0]["text"] == antes
    assert prompt.hash_do_sistema("shopee", []) != prompt.hash_do_sistema("shopee", [], True)


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
