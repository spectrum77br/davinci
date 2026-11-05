# ruff: noqa: F811, E501 — fixtures importadas; as tabelas de casos reais
"""A análise de 31/10–02/11 (an0311, 03/11/2026; o dono: "aplique as correções"), itens 5 e 6.

Os falsos positivos da 1ª camada, do validador e do verificador que seguravam respostas BOAS
nas 5 lojas no automático (os rascunhos REAIS, com o prefixo da conversa da análise), e — para
cada liberação — o espelho que continua segurando (a promessa de reembolso, aprovação, data,
desconto ou Pix; o dado de produto inventado; a afirmação sem lastro):

1. o número que o próprio cliente escreveu (73907524 "quero uma tv 43", 7a8f6a5d, 47a86f5e);
2. o "eu confiro"/"conferimos pelas opções do anúncio" do próprio atendimento (f7121a4b,
   8f751e9d, a1929b16, 73907524);
3. a pergunta de esclarecimento ao pedido vago (974d4398);
4. a garantia antes da compra pela política geral, como a equipe responde (aaf4a63e, a80987a3,
   4ed67ae1) e a estendida com a plataforma (2b4106c1) — nunca "90 dias"/"1 ano" da Uranyx;
5. o verificador v17: o que o atendimento já enviou (910f3c9d), o estado citado na restrição
   (c5190f5d), as regras novas do `SISTEMA` e as linhas da calibração (grupo `an0311`);
6. o corte da frase sem lastro (ad4d4b72, 910f3c9d): só com o "ok" do texto cortado.

Sem dado de cliente (nenhum nome, telefone, CPF, endereço, rastreio ou pedido real). Nenhuma
chamada a LLM, à loja ou à produção.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.atendimento import agente, constantes, ia, restricao_envio
from app.services.atendimento.agente import checagens, ferramentas
from app.services.atendimento.agente import contexto as agente_ctx
from app.services.atendimento.agente import verificador as v
from app.services.atendimento.agente.saida import Proposta
from app.services.atendimento.constantes import MODO_AUTO, MODO_COPILOTO, RASCUNHO_PENDENTE
from scripts.agente_eval import calibrar_verificador as calibrar
from tests import test_atendimento_agente_verificador as _ver
from tests.test_atendimento_agente_furos import _completo, _segura
from tests.test_atendimento_agente_laco import _categorias_do_manual
from tests.test_atendimento_agente_verificador import (  # noqa: F401 — fixtures
    _no_auto,
    _ok,
    _sem_a_regua_dos_dias,
    _sem_limites,
    agente_ligado,
    api,
    envios,
    ia_ligada,
    modelo,
    teto_limpo,
)
from tests.test_atendimento_ia import RASTREIO

AGORA = datetime(2026, 11, 2, 20, 0, tzinfo=UTC)
SEM_PEDIDO: dict[str, Any] = {
    "ctx": {},
    "fatos": {},
    "valores": dict.fromkeys(constantes.LACUNAS),
}
URANYX = [{"status": None, "regra": dict(ferramentas.REGRA_URANYX)}]
# O anúncio da conversa (Uranyx, sem pedido): o nome do modelo é dado do anúncio.
FICHA_URANYX = {
    "item_id": "58209990001", "titulo": "Celular Uranyx A17 Pro Max 128 GB", "da_conversa": True,
    "atributos": ["Marca: Uranyx", "Phone Model: A17 Pro Max"],
}  # fmt: skip


def _m(autor: str, texto: str | None, *, origem: str | None = None, minutos: int = 0):
    quando = AGORA - timedelta(minutes=60 - minutos)
    return SimpleNamespace(
        id=uuid4(), autor=autor, texto=texto, tipo="texto", payload={}, origem=origem or autor,
        status="recebida" if autor == "cliente" else "enviada", enviada_em=quando,
        created_at=quando, anexos=[], autor_user_id=None, erro=None,
    )  # fmt: skip


def _conv(**campos: Any) -> SimpleNamespace:
    base = {
        "id": uuid4(), "plataforma": "shopee", "canal": "chat", "dados": {},
        "pedido_marketplace": None, "etiqueta": constantes.ETIQUETA_PRE_VENDA,
        "etiquetas_secundarias": [], "etiquetas": [], "canal_id": None, "integration_id": None,
        "comprador_id": None, "situacao": "aberta", "aguardando_resposta": True,
    }  # fmt: skip
    base.update(campos)
    return SimpleNamespace(**base)


def _p(texto: str, assunto: str, *, acao: str = "responder", confianca: float = 0.9) -> Proposta:
    return Proposta(
        acao=acao, texto=texto, assunto=assunto, confianca=confianca, acao_pessoa=None,
        motivo_pessoa="", evidencias=[],
    )  # fmt: skip


async def _pela_proposta(
    p: Proposta,
    msgs: list,
    *,
    garantias: list[dict] | None = None,
    fichas: list[dict] | None = None,
    pedido: dict | None = None,
    conversa: Any = None,
    modo: str = MODO_AUTO,
) -> Any:
    """A 1ª camada INTEIRA do agente — o `agente.conferir_proposta` de verdade (a mesma do
    `responder`), sem banco e sem modelo."""
    conversa = conversa or _conv()
    gatilho = next(x for x in reversed(msgs) if x.autor == "cliente")
    rajada = ia.rajada_do_cliente(msgs, gatilho)
    foco = ia.perguntas_pendentes(msgs, gatilho)
    ctxd = {"pedido": None, "logistica": None, "chamados": [], "devolucoes": []}
    base = agente_ctx.BaseDoGerar(
        recentes=msgs, rajada=rajada, foco=foco, textos_cliente=[x.texto or "" for x in (foco or rajada)],
        textos_rajada=[x.texto or "" for x in rajada], ctx=ctxd, valores=dict.fromkeys(constantes.LACUNAS),
        fatos={"anuncio": None}, fatos_gravados={}, categorias=_categorias_do_manual(), regras=[],
        sinais=[], motivos=[], opcoes_sensiveis=[], sinais_do_cliente=[], nomes=(), modo=modo,
        forcar=False, nf_politica_aprovada=False, insistiu=False,
    )  # fmt: skip
    ctx = agente_ctx.ContextoAgente.da_conversa(conversa, gatilho, modo=modo)
    if garantias is not None:
        ctx.garantias_conferidas = [dict(g) for g in garantias]
        ctx.garantia_consultada = True
        ctx.permitir(textos=[json.dumps({"garantias": garantias}, ensure_ascii=False)])
    fichas = list(fichas or [])
    if fichas:
        ctx.permitir(textos=[json.dumps(fichas, ensure_ascii=False)], produto=True)
    pac = SimpleNamespace(
        fatos={"anuncio": None, "ficha_do_anuncio": fichas}, fichas=fichas, pedido=pedido,
        pediu_evidencia=False,
    )  # fmt: skip
    return await agente.conferir_proposta(
        None, conversa, gatilho, base, ctx, p, pac, forcar_conferencia=True
    )


def _de_garantia(final: ia.SaidaConferida) -> list[str]:
    """Os motivos de garantia que seguravam os rascunhos da análise."""
    marcas = (
        ia.MOTIVO_RESPOSTA_DE_GARANTIA,
        ia.MOTIVO_RECUSA_DE_DIREITO,
        "promessa de cobertura sem garantia ativa",
        "nega a garantia",
        "prazo de garantia só pode vir do sistema",
    )
    return [m for m in final.motivos if any(x in m for x in marcas)]


# ─────────────── 1. o número que o próprio cliente escreveu ───────────────


@pytest.mark.parametrize(
    ("cliente", "texto"),
    [
        # 73907524 (Vortan, 02/11 23:55) — o rascunho real.
        ("quero uma tv 43*", "Olá! Você tem em mente algum modelo ou anúncio de TV 43 que viu na nossa loja? Se tiver, me diga o nome para eu conferir."),
        # 7a8f6a5d (Vortan, 01/11 10:05).
        ("Este NFC Premium e 18 ou 17", "Olá! Sobre qual produto você está falando? Se puder, me diga o nome ou envie o anúncio, para eu conferir se ele é o modelo 18 ou 17."),
        # 47a86f5e (Vortan, 01/11 10:51).
        ("Oi boa tarde vcs tem o sabor iphone com a tela de 6.9", "Olá! Para eu conferir a tela de 6.9 e a cor laranja, qual é o produto? Pode me dizer o nome ou enviar o anúncio por aqui."),
        # o separador trocado ("6.9" do cliente, "6,9" no texto).
        ("tem com a tela de 6.9?", "Qual é o anúncio da tela de 6,9 que você viu?"),
    ],
)  # fmt: skip
async def test_o_numero_do_cliente_nao_e_inventado(cliente, texto):
    final = await _completo(
        texto, categoria="confirmar_variacao", acao="perguntar", cliente=cliente, **SEM_PEDIDO
    )
    assert not any(m.startswith(ia.MOTIVO_NUMERO_INVENTADO) for m in final.motivos), final.motivos
    assert final.status == RASCUNHO_PENDENTE and not _segura(final), final.motivos


@pytest.mark.parametrize(
    ("cliente", "texto", "numero"),
    [
        # o valor, o prazo, a data, a parcela, a porcentagem e a quantidade continuam inventados
        ("faz por 500?", "Fazemos por 500 sim.", "500"),
        ("faz 500 reais?", "Sim, sai a 500 reais.", "500"),
        ("chega dia 15?", "Chega dia 15 sim.", "15"),
        ("chega em 3 dias?", "Chega em 3 dias.", "3"),
        ("chega 15/11?", "Sim, chega 15/11.", "15"),
        ("parcela em 12x?", "Pode parcelar em 12x.", "12"),
        ("tem desconto de 10%?", "Damos 10% de desconto.", "10"),
        ("tem 43 unidades?", "Temos 43 unidades.", "43"),
        ("a garantia é de 1 ano?", "Sim, 1 ano de garantia.", "1"),
        # o número que o cliente NÃO escreveu
        ("quero uma tv 43", "Temos a TV 50 polegadas.", "50"),
        # o número longo (pedido, telefone): nunca do cliente
        ("meu pedido é 26110148", "O pedido 26110148 está certo.", "26110148"),
    ],
)  # fmt: skip
async def test_o_numero_do_cliente_como_valor_prazo_ou_quantidade_continua_inventado(
    cliente, texto, numero
):
    final = await _completo(texto, categoria="outro", cliente=cliente, **SEM_PEDIDO)
    inventado = [m for m in final.motivos if m.startswith(ia.MOTIVO_NUMERO_INVENTADO)]
    assert inventado and numero in inventado[0], final.motivos
    assert _segura(final)


def test_numeros_do_cliente_puro():
    assert checagens.numeros_do_cliente("a TV 43", ["43"], ["quero uma tv 43"]) == ["43"]
    assert checagens.numeros_do_cliente("a TV 43", ["43"], ["quero uma tv"]) == []
    # uma ocorrência de valor basta para continuar inventado
    assert checagens.numeros_do_cliente("TV 43 por 43", ["43"], ["tv 43"]) == []
    assert checagens.numeros_do_cliente("x", [], ["43"]) == []


async def test_o_legado_nao_muda_com_o_numero_do_cliente():
    from tests.test_atendimento_conferir_saida import _base, _chamada

    conversa, kw = _chamada(
        _base(
            texto="Olá! Você quer a TV 43?",
            categoria="outro",
            textos_cliente=["quero uma tv 43"],
            textos_rajada=["quero uma tv 43"],
        )
    )
    conferida = await ia.conferir_saida(None, conversa, SimpleNamespace(), **kw)
    assert any(m.startswith(ia.MOTIVO_NUMERO_INVENTADO) for m in conferida.motivos)


# ─────────────── 2. o próprio atendimento confere ───────────────


@pytest.mark.parametrize(
    ("texto", "acao", "assunto", "cliente"),
    [
        # f7121a4b (Kia, 02/11 17:30) — "promete ação da equipe ('confiro')".
        ("Entendi, obrigado por avisar. Qual é o modelo ou o título do anúncio que você está vendo? Assim eu confiro as opções que esse anúncio tem para escolher.", "perguntar", "confirmar_variacao", "Não tem essa opção"),
        # 8f751e9d (Barbosa, 31/10 15:20).
        ("Olá! Para eu te ajudar, qual modelo e cor do telefone você procura? Assim confiro se ele está disponível ou esgotado no anúncio.", "perguntar", "confirmar_variacao", "esse telefone vai demorar a voltar?"),
        # a1929b16 (Vortan, 02/11 17:37) — o verificador chamou de promessa.
        ("Olá! Você escolhe o aparelho: as opções disponíveis aparecem para seleção no próprio anúncio, e o pedido segue a variação que você selecionar. Se quiser, me diga qual modelo você tem em vista e conferimos pelas opções do anúncio.", "responder", "estoque_variacao", "eu que escolho o aparelho ou vocês mandam o que tiver disponível?"),
        # 73907524 — "me diga o nome para eu conferir".
        ("Olá! Qual é o modelo de TV que você viu na nossa loja? Me diga o nome para eu conferir.", "perguntar", "confirmar_variacao", "quero uma tv"),
    ],
)  # fmt: skip
def test_o_atendimento_que_confere_no_anuncio_nao_e_promessa_dura(texto, acao, assunto, cliente):
    assert (
        checagens.conferir_promessa_dura(
            texto, acao=acao, assunto=assunto, textos_cliente=[cliente]
        )
        == []
    )


@pytest.mark.parametrize(
    "texto",
    [
        # conferir com alguém da loja, mesmo junto do pedido do dado
        "Qual é o número do pedido? Assim eu verifico com a transportadora.",
        "Me diga o número do pedido que eu verifico com a transportadora.",
        "Me diga a cor e eu confiro com o estoque.",
        # a promessa de retorno, a equipe que confere
        "Vamos verificar e retornamos. Qual é o número do pedido?",
        "Me diga o modelo e a equipe confere e te avisa por aqui.",
        "Qual é o modelo? Vou verificar e te retorno.",
        "Me diga a cor que conferimos e te avisamos assim que tivermos retorno.",
        # sem o pedido do dado, o "conferimos" é promessa
        "Conferimos pelas opções do anúncio e retornamos.",
    ],
)
def test_a_promessa_da_equipe_continua_dura(texto):
    (p,) = checagens.conferir_promessa_dura(
        texto, acao="responder", assunto="estoque_variacao", textos_cliente=["qual cor tem?"]
    )
    assert p.codigo == checagens.PROMESSA_DURA and checagens.pendencia_dura(p)


# ─────────────── 3. a pergunta de esclarecimento ao pedido vago ───────────────


def _conferida(**kw: Any) -> ia.SaidaConferida:
    base = {
        "texto": "Olá!",
        "categoria": "saudacao_ou_vaga",
        "precisa_humano": True,
        "motivos": [ia.MOTIVO_PERGUNTA_PENDENTE],
        "erros": [],
        "status": RASCUNHO_PENDENTE,
        "sem_auto": [ia.MOTIVO_PERGUNTA_PENDENTE],
    }
    base.update(kw)
    return ia.SaidaConferida(**base)


def test_a_pergunta_de_esclarecimento_vira_aviso():
    # 974d4398 (Mini, 01/11 07:34): "Quero adquirir mais produto" — o rascunho real.
    c = _conferida(texto="Olá! Que bom que quer comprar mais. Qual produto ou modelo você procura?")
    s = checagens.separar_avisos(c, modo=MODO_AUTO, pessoa_do_modelo=False)
    assert s.motivos == [] and s.sem_auto == [] and not s.precisa_humano
    assert checagens.MOTIVO_ESCLARECIMENTO_COM_PENDENTE in s.avisos


@pytest.mark.parametrize(
    "texto",
    [
        "Olá! Como podemos ajudar?",  # o genérico
        "Olá! Em que podemos ajudar sobre esse produto ou pedido?",
        "Olá! Seu pedido segue normalmente.",  # não pergunta nada
    ],
)
def test_o_generico_com_pergunta_pendente_continua_duro(texto):
    s = checagens.separar_avisos(_conferida(texto=texto), modo=MODO_AUTO, pessoa_do_modelo=False)
    assert s.motivos == [ia.MOTIVO_PERGUNTA_PENDENTE] and s.precisa_humano and s.sem_auto


async def test_974d4398_pelo_caminho_inteiro():
    msgs = [_m("cliente", "Bom dia"), _m("cliente", "Quero adquirir mais produto", minutos=2)]
    c = await _pela_proposta(
        _p(
            "Olá! Que bom que quer comprar mais. Qual produto ou modelo você procura?",
            "saudacao_ou_vaga",
        ),
        msgs,
    )
    assert not c.final.precisa_humano and not c.final.sem_auto, c.final.motivos
    # O genérico, no mesmo turno, continua com pessoa.
    g = await _pela_proposta(_p("Olá! Como podemos ajudar?", "saudacao_ou_vaga"), msgs)
    assert g.final.precisa_humano, g.final.motivos


# ─────────────── 4. a garantia antes da compra ───────────────

# Os rascunhos reais (Barbosa e Vortan), sem pedido na conversa. Revisão do an0311: o turno em que
# o cliente pergunta QUANTO TEMPO dura a garantia (aaf4a63e 22:23/22:33, 4ed67ae1, a 2ª metade do
# a80987a3 21:14) não entra — a política sem prazo não o responde (`GARANTIA_DO_PRAZO`, abaixo);
# o texto desses rascunhos fica aqui com a pergunta só de "tem garantia?".
GARANTIA_BOAS = [
    # a80987a3 (01/11 21:14) — só a 1ª metade ("Gostaria de saber se tem garantia?").
    ("Gostaria de saber se tem garantia?", "Olá! Sim, o aparelho tem garantia. Se houver algum problema, descreva o defeito e mande um vídeo curto e fotos por aqui; a nossa equipe analisa e responde por aqui."),
    # a80987a3 (02/11 20:49): "Em que consiste a garantia?"
    ("Em que consiste a garantia?", "A garantia cobre defeitos do aparelho e conta a partir da entrega. Ela não cobre aparelho quebrado, molhado ou com mau uso. Para acionar a garantia, descreva o problema e mande um vídeo curto e fotos por aqui; a nossa equipe analisa e responde por aqui."),
    # o texto do aaf4a63e (02/11 22:33) à pergunta sem o prazo.
    ("Vem nota fiscal? Tem garantia?", "Sim, o produto tem garantia e todos os pedidos vão com nota fiscal, que segue junto com o produto. Se houver algum defeito, é só descrever o problema e mandar um vídeo curto e fotos por aqui."),
    # o texto do aaf4a63e (02/11 22:23) — a regra da exclusão junto.
    ("Tem garantia?", "Sim, o aparelho tem garantia, e a nossa garantia não cobre aparelho quebrado, molhado ou com mau uso. Se houver algum problema, é só descrever e mandar um vídeo curto e fotos por aqui."),
    # o texto do 4ed67ae1 (Vortan, 31/10 09:31) — o nome do modelo ("A17") não é prazo.
    ("Ele tem garantia?", "O Uranyx A17 Pro Max tem garantia, e, se houver algum problema depois da compra, é só descrever e mandar um vídeo curto e fotos por aqui. A compra também é protegida pela Shopee, com a opção de devolução pelo próprio pedido."),
]  # fmt: skip


@pytest.mark.parametrize(("cliente", "texto"), GARANTIA_BOAS)
@pytest.mark.parametrize("garantias", [None, URANYX], ids=["sem_ver_garantia", "regra_uranyx"])
async def test_a_politica_geral_antes_da_compra_chega_ao_verificador(cliente, texto, garantias):
    c = await _pela_proposta(
        _p(texto, "garantia"), [_m("cliente", cliente)], garantias=garantias, fichas=[FICHA_URANYX]
    )
    assert _de_garantia(c.final) == [], c.final.motivos
    assert not c.final.precisa_humano and not c.final.sem_auto, c.final.motivos
    assert c.final.status == RASCUNHO_PENDENTE


# Revisão do an0311 (achado 7): as perguntas REAIS de quanto tempo (sem o fato do prazo — do
# aparelho Uranyx, nunca "90 dias"/"1 ano" — a política geral não as responde): ficam com pessoa
# como no main, sem depender do "não responde" do verificador.
GARANTIA_DO_PRAZO = [
    ("Gostaria de saber se tem garantia? E de cuantos meses e a garantia?", GARANTIA_BOAS[0][1]),  # a80987a3 21:14
    ("Vem nota fiscal Tem garantia quanto tempo ??", GARANTIA_BOAS[2][1]),  # aaf4a63e 22:33
    ("Vem nota fiscal Tem garantia quanto tempo ??", GARANTIA_BOAS[3][1]),  # aaf4a63e 22:23
    ("Quanto tempo ele tem de garantia mesmo?", GARANTIA_BOAS[4][1]),  # 4ed67ae1
    ("qual o prazo da garantia?", GARANTIA_BOAS[1][1]),
    ("a garantia dura quanto?", GARANTIA_BOAS[1][1]),
    ("tem garantia de quantos meses?", GARANTIA_BOAS[0][1]),
]  # fmt: skip


@pytest.mark.parametrize(("cliente", "texto"), GARANTIA_DO_PRAZO)
@pytest.mark.parametrize("garantias", [None, URANYX], ids=["sem_ver_garantia", "regra_uranyx"])
async def test_a_pergunta_do_prazo_da_garantia_fica_com_pessoa(cliente, texto, garantias):
    c = await _pela_proposta(
        _p(texto, "garantia"), [_m("cliente", cliente)], garantias=garantias, fichas=[FICHA_URANYX]
    )
    assert _de_garantia(c.final), c.final.motivos
    assert c.final.precisa_humano or c.final.sem_auto or c.final.status != RASCUNHO_PENDENTE


@pytest.mark.parametrize(
    ("cliente", "forma"),
    [
        # a pergunta hipotética antes da compra continua com a política geral
        ("e se não ligar, a garantia cobre?", checagens.GARANTIA_GERAL),
        ("se vier com problema tem garantia?", checagens.GARANTIA_GERAL),
        # o "quanto tempo" que não é da garantia não segura
        ("quanto tempo demora a entrega? e tem garantia?", checagens.GARANTIA_GERAL),
        # o prazo DA garantia segura
        ("tem garantia de quanto tempo?", None),
        ("a garantia vale até quando?", None),
        ("qual a validade da garantia?", None),
    ],
)
def test_o_prazo_da_garantia_e_a_hipotese_puro(cliente, forma):
    ctx = agente_ctx.ContextoAgente.da_conversa(_conv(), None, modo=MODO_AUTO)
    base = SimpleNamespace(
        textos_rajada=[cliente], textos_cliente=[cliente], foco=[], ctx={}, fatos={}
    )
    texto = "A garantia cobre defeitos do aparelho. Ela não cobre aparelho quebrado, molhado ou com mau uso."
    assert checagens.garantia_geral_pre_venda(texto, ctx=ctx, base=base, fichas=[]) == forma


async def test_a_estendida_com_a_plataforma_vale_tambem_com_pedido():
    # 2b4106c1 (Barbosa, 02/11 18:32): a conversa tinha um pedido; o cliente pergunta da estendida.
    texto = "Olá! A garantia estendida contratada na Shopee é tratada direto com a Shopee, e é por ela que se aciona se houver algum defeito. Se quiser saber algo sobre o aparelho, é só perguntar por aqui."
    cliente = "Se eu colocar Garantia estendida aí responsabilidade de vcs caso haver um defeito."
    c = await _pela_proposta(
        _p(texto, "garantia"),
        [_m("cliente", cliente)],
        conversa=_conv(pedido_marketplace="X1", etiqueta="pos_venda"),
    )
    assert _de_garantia(c.final) == [], c.final.motivos


# Os espelhos ruins: o que continua com pessoa (ou bloqueado).
GARANTIA_RUINS = [
    ("tem garantia?", "Sim, o aparelho tem garantia de 90 dias."),  # o prazo (Uranyx: nunca)
    ("tem garantia?", "Sim, tem garantia de um ano com a loja."),
    ("tem garantia?", "Tem garantia sim; depois, acione pelo app Uranyx ou pelo SAC."),
    ("tem garantia?", "Tem garantia e qualquer defeito a gente troca na hora."),
    ("tem garantia?", "Tem garantia, mas não cobre a bateria."),  # a exclusão inventada
    ("tem garantia?", "Não tem garantia."),
    ("tem garantia?", "Tem a garantia legal do Código de Defesa do Consumidor."),
    ("tem garantia?", "Seu aparelho está coberto pela garantia."),
    ("tem garantia?", "Tem garantia total, cobre tudo."),
    ("tem garantia?", "Tem garantia e o conserto é por nossa conta."),
    ("tem garantia?", "Tem garantia. O reembolso é garantido se der defeito."),
    # uma frase boa e outra com o prazo: toda frase de garantia tem de ser a política
    ("tem garantia?", "O aparelho tem garantia. A garantia é de 90 dias a partir da entrega."),
]  # fmt: skip


@pytest.mark.parametrize(("cliente", "texto"), GARANTIA_RUINS)
@pytest.mark.parametrize("garantias", [None, URANYX], ids=["sem_ver_garantia", "regra_uranyx"])
async def test_o_espelho_ruim_da_garantia_continua_com_pessoa(cliente, texto, garantias):
    c = await _pela_proposta(_p(texto, "garantia"), [_m("cliente", cliente)], garantias=garantias)
    assert c.final.precisa_humano or c.final.sem_auto or c.final.status != RASCUNHO_PENDENTE, (
        c.final.motivos
    )


@pytest.mark.parametrize(
    ("mudar", "cliente"),
    [
        ({"pedido": {"numero": "{numero_pedido}", "entregue": True}}, "tem garantia?"),  # depois da compra
        ({}, "meu celular quebrou a tela, a garantia cobre?"),  # relata o defeito
        ({}, "comprei ontem, tem garantia?"),  # já comprou
        ({"garantias": [{"status": "ativa", "regra": dict(ferramentas.REGRA_URANYX)}]}, "tem garantia?"),
        ({"modo": MODO_COPILOTO}, "tem garantia?"),
        ({}, "qual a cor?"),  # o cliente não fala de garantia
    ],
)  # fmt: skip
async def test_fora_da_pre_venda_a_politica_geral_nao_vale(mudar, cliente):
    texto = "A garantia cobre defeitos do aparelho. Ela não cobre aparelho quebrado, molhado ou com mau uso."
    msgs = [_m("cliente", cliente)]
    gatilho = msgs[-1]
    modo = mudar.get("modo", MODO_AUTO)
    ctx = agente_ctx.ContextoAgente.da_conversa(_conv(), gatilho, modo=modo)
    base = SimpleNamespace(
        textos_rajada=[cliente], textos_cliente=[cliente], foco=[], ctx={}, fatos={}
    )
    forma = checagens.garantia_geral_pre_venda(
        texto,
        ctx=ctx,
        base=base,
        fichas=[],
        pedido=mudar.get("pedido"),
        garantias=mudar.get("garantias"),
    )
    assert forma is None


def test_o_produto_apple_fica_com_o_d2():
    ficha = {"item_id": "1", "titulo": "Apple Watch SE 3", "da_conversa": True}
    ctx = agente_ctx.ContextoAgente.da_conversa(_conv(), None, modo=MODO_AUTO)
    base = SimpleNamespace(
        textos_rajada=["tem garantia?"], textos_cliente=["tem garantia?"], foco=[], ctx={}, fatos={}
    )
    texto = "O aparelho tem garantia; se aparecer defeito, mande um vídeo por aqui."
    assert checagens.garantia_geral_pre_venda(texto, ctx=ctx, base=base, fichas=[ficha]) is None
    assert (
        checagens.garantia_geral_pre_venda(texto, ctx=ctx, base=base, fichas=[])
        == checagens.GARANTIA_GERAL
    )


@pytest.mark.parametrize(
    "extra",
    [
        "Pode pagar por Pix que liberamos na hora.",  # Pix
        "O reembolso foi aprovado.",  # a aprovação
        "Fazemos 10% de desconto à vista.",  # o desconto
        "Chega amanhã sem falta.",  # a data
    ],
)
async def test_a_politica_geral_nao_libera_o_resto_do_texto(extra):
    texto = f"Sim, o aparelho tem garantia e cobre defeitos. {extra}"
    c = await _pela_proposta(
        _p(texto, "garantia"), [_m("cliente", "tem garantia?")], garantias=URANYX
    )
    assert c.final.precisa_humano or c.final.sem_auto or c.final.status != RASCUNHO_PENDENTE, (
        c.final.motivos
    )


def test_a_frase_da_politica_geral_pura():
    assert checagens.frase_da_garantia_geral("tem garantia sim")
    assert checagens.frase_da_garantia_geral("nao cobre aparelho quebrado, molhado ou com mau uso")
    assert checagens.frase_da_garantia_geral(
        "a garantia estendida e direto com a shopee", so_estendida=True
    )
    assert not checagens.frase_da_garantia_geral("tem garantia", so_estendida=True)
    assert not checagens.frase_da_garantia_geral("tem garantia de 3 meses")
    assert not checagens.frase_da_garantia_geral("tem garantia vitalicia")
    assert not checagens.frase_da_garantia_geral("a garantia ja venceu")
    assert not checagens.frase_da_garantia_geral("nao cobre o carregador")


# ─────────────── 5. o verificador v17 ───────────────


def _do_agente_com_ok(m: Any) -> dict[str, str]:
    """A fala `m` saiu do rascunho do agente com o "ok" do verificador no MESMO texto."""
    m.rascunho_id = uuid4()
    return {str(m.rascunho_id): v.assinatura_do_texto(m.texto)}


def test_o_que_o_atendimento_ja_enviou():
    msgs = [
        _m("cliente", "Qual é o de 6.9 polegadas"),
        _m("loja", "Olá! O de 6,9 polegadas é o A18 Pro Max, na variação 16 GB / 128 GB Premium NFC.", origem=constantes.ORIGEM_IA, minutos=2),
        _m("loja", "1 - Previsão de entrega / envio 2 - Nota fiscal 6 - Falar com Atendente", origem=constantes.ORIGEM_AUTO, minutos=2),
        _m("loja", "sim são celulares!", origem=constantes.ORIGEM_HUMANO, minutos=3),
        _m("cliente", "Me manda o link pra eu pegar o 18 com tela de 6.9", minutos=8),
    ]  # fmt: skip
    conferidas = _do_agente_com_ok(msgs[1])
    (linha,) = v.ja_enviado_da_conversa(msgs, msgs[-1], (), conferidas)
    assert "A18 Pro Max" in linha and linha.startswith("[02/11 ")
    # A seção só com resposta do atendimento; sem ela, a entrada de antes.
    assert v.linhas_do_ja_enviado(()) == []
    assert v.linhas_do_ja_enviado((linha,))[0] == v.CABECALHO_JA_ENVIADO
    assert v.ja_enviado_da_conversa(msgs[3:], msgs[-1], (), conferidas) == ()


def test_o_ja_enviado_mascara_o_nome_e_fica_nas_ultimas():
    msgs = [
        _m("loja", f"Olá Fulano! resposta {i}", origem=constantes.ORIGEM_IA, minutos=i)
        for i in range(5)
    ]
    conferidas: dict[str, str] = {}
    for m in msgs:
        conferidas |= _do_agente_com_ok(m)
    linhas = v.ja_enviado_da_conversa(msgs, None, ("Fulano",), conferidas)
    assert len(linhas) == v.MAX_JA_ENVIADO and "resposta 4" in linhas[-1]
    assert all("Fulano" not in x for x in linhas)


def _resumo(**kw: Any) -> v.ResumoDoPacote:
    base = {
        "plataforma": "Shopee", "agora": "02/11/2026 17:00", "perguntas": ["x"],
        "conversa": [], "evidencias": [], "plataforma_codigo": "shopee",
    }  # fmt: skip
    base.update(kw)
    return v.ResumoDoPacote(**base)


def test_a_entrada_traz_o_ja_enviado_so_quando_ha():
    sem = v.montar_entrada(acao="responder", acao_pessoa=None, texto="Olá!", resumo=_resumo())
    assert v.CABECALHO_JA_ENVIADO not in sem
    com = v.montar_entrada(
        acao="responder",
        acao_pessoa=None,
        texto="Olá!",
        resumo=_resumo(ja_enviado=("[01/11 14:16] o A18",)),
    )
    assert v.CABECALHO_JA_ENVIADO in com and "- [01/11 14:16] o A18" in com
    assert com.index(v.CABECALHO_JA_ENVIADO) < com.index("RASCUNHO:")


FICHA_RJ = {
    "item_id": "77", "titulo": "Uranyx A18 Pro Max",
    "restricao_envio": {"conferido": True, "ufs": ["RJ"], "ufs_conforme_pedido": ["AM"], "fonte": "anuncio"},
}  # fmt: skip


@pytest.mark.parametrize(
    ("ufs", "trecho"),
    [
        (("CE",), "o estado que o cliente citou: CE (ceara) — recebe"),
        (("RJ",), "o estado que o cliente citou: RJ (rio de janeiro) — NÃO recebe"),
        (("AM",), "o estado que o cliente citou: AM (amazonas) — depende do valor do pedido"),
    ],
)
def test_a_restricao_diz_o_estado_que_o_cliente_citou(ufs, trecho):
    linhas = v.linhas_da_restricao([FICHA_RJ], ufs)
    assert any(trecho in x for x in linhas), linhas


def test_sem_estado_citado_a_restricao_e_a_de_antes():
    linhas = v.linhas_da_restricao([FICHA_RJ])
    assert linhas == [
        v.CABECALHO_RESTRICAO,
        "- anuncio:77 «Uranyx A18 Pro Max»: não envia para RJ (fonte: anuncio); depende do valor "
        "do pedido: AM; os demais estados recebem",
    ]


def test_o_resumo_le_o_estado_do_turno():
    base = SimpleNamespace(textos_rajada=["entregam no Ceará?"], textos_cliente=[], foco=[])
    assert v._ufs_do_turno(base) == ("CE",)
    assert restricao_envio.ufs_citadas("moro em Niterói") == ["RJ"]


def test_o_sistema_v17_tem_as_regras_novas_e_mantem_as_travas():
    s = v.SISTEMA
    assert v.VERSAO == "v19"
    # as liberações (an0311)
    assert "O QUE O ATENDIMENTO JÁ ENVIOU NESTA CONVERSA" in s
    assert "conferimos pelas opções do anúncio" in s
    assert "«quero uma TV 43»" in s
    assert "o estado que o cliente citou" in s
    assert "Antes da compra (sem pedido), a quem pergunta se tem" in s
    # as travas que não afrouxaram
    assert "«90 dias», «1 ano», «9 meses», o app Uranyx" in s
    assert "«a equipe confere/retorna»" in s
    assert "nunca sustenta estado, data, prazo ou valor de" in s
    assert "Seja rígido." in s
    padrao = "\n".join(v.FRASES_PADRAO_GARANTIA_SHOPEE)
    assert "nunca «90 dias» nem «1 ano»" in padrao


def test_as_linhas_da_calibracao_an0311():
    casos = [c for c in calibrar.ler_casos(calibrar.CASOS_PADRAO) if c["grupo"] == "an0311"]
    assert len(casos) >= 12
    boas = [c for c in casos if c["esperado"]["ok_para_envio_automatico"]]
    ruins = [c for c in casos if not c["esperado"]["ok_para_envio_automatico"]]
    # cada liberação com o espelho ruim
    assert len(ruins) >= len(boas) - 1 and len(boas) >= 6
    for c in casos:
        entrada = calibrar.entrada_do_caso(c)
        assert (v.CABECALHO_JA_ENVIADO in entrada) is bool(c.get("ja_enviado")), c["id"]
        if c.get("ufs_do_cliente"):
            assert "o estado que o cliente citou" in entrada, c["id"]
    # O ao vivo e a calibração montam a mesma seção.
    (c910,) = [c for c in casos if c["id"] == "an0311-boa-910f3c9d-ja-enviado"]
    assert "A18 Pro Max (Premium Edition)" in calibrar.entrada_do_caso(c910)


# ─────────────── 6. o corte da frase sem lastro ───────────────


def _registro(*afirmacoes: str, problemas: list[str] | None = None, **kw: Any) -> dict:
    return {
        "rodou": True, "ok": False, "falha": None,
        "problemas": [v.SEM_LASTRO] if problemas is None else problemas,
        "afirma_sem_lastro": list(afirmacoes), **kw,
    }  # fmt: skip


AD4D4B72 = "Olá! No anúncio, o WP200 Pro aparece com memória interna de 512 GB, nas variações Preto e Verde; não há opção de 1 TB. A opção de 256 GB do WP200 Pro está esgotada."
F910F3C9D = 'É o mesmo anúncio que você enviou: não mandamos link por aqui. Nele, é só escolher a variação "16 GB / 128 GB Premium NFC", que é o A18 Pro Max de tela de 6,9 polegadas e está disponível em Laranja e Prata.'


def test_o_corte_tira_so_a_frase_sem_lastro():
    # ad4d4b72 (Mini, 31/10 14:45) — o veredito real.
    resto, frase = v.frase_para_cortar(
        AD4D4B72, _registro("A opção de 256 GB do WP200 Pro está esgotada")
    )
    assert frase.startswith("A opção de 256 GB")
    assert (
        resto
        == "Olá! No anúncio, o WP200 Pro aparece com memória interna de 512 GB, nas variações Preto e Verde; não há opção de 1 TB."
    )
    # 910f3c9d (Vortan, 01/11 14:22) — a afirmação dita com outras palavras.
    resto, frase = v.frase_para_cortar(
        F910F3C9D,
        _registro(
            "o anúncio tem o A18 Pro Max de tela de 6,9 polegadas na variação 16 GB / 128 GB Premium NFC"
        ),
    )
    assert frase.startswith("Nele, é só escolher") and resto.startswith("É o mesmo anúncio")


@pytest.mark.parametrize(
    ("texto", "registro"),
    [
        # outra marca junto: nunca corta
        (AD4D4B72, _registro("A opção de 256 GB do WP200 Pro está esgotada", problemas=[v.SEM_LASTRO, v.PROMETE])),
        (AD4D4B72, _registro("A opção de 256 GB do WP200 Pro está esgotada", problemas=[v.SEM_LASTRO, v.NAO_RESPONDE])),
        # o "ok", a falha, o que não rodou
        (AD4D4B72, {**_registro("A opção de 256 GB"), "ok": True}),
        (AD4D4B72, {**_registro("A opção de 256 GB"), "falha": "tempo"}),
        (AD4D4B72, {**_registro("A opção de 256 GB"), "rodou": False}),
        (AD4D4B72, None),
        # afirmações em duas frases
        (AD4D4B72, _registro("A opção de 256 GB do WP200 Pro está esgotada", "memória interna de 512 GB, nas variações Preto e Verde")),
        # o resto é só cortesia ou o genérico
        ("Olá! Seu pedido já foi entregue.", _registro("Seu pedido já foi entregue")),
        ("Olá! Como podemos ajudar? O A18 tem NFC.", _registro("O A18 tem NFC")),
        # uma frase só
        ("Seu pedido já foi enviado; o rastreio é {rastreio}.", _registro("já foi enviado")),
        # c81bc743 (Vortan 01/11 12:24): a frase sem lastro É a resposta ("Sim, …") — segura
        ("Olá! Sim, o anúncio traz dual chip: são 2 entradas para chip Nano. Sobre cartão de memória, a ficha lista memória expansível, mas não detalha o formato do cartão.", _registro("são 2 entradas para chip Nano")),
        # a afirmação que não cai em frase nenhuma
        (AD4D4B72, _registro("o carregador de 65 W acompanha")),
        # demais afirmações
        (AD4D4B72, _registro("A opção de 256 GB", "WP200 Pro está esgotada", "256 GB esgotada", "opção de 256")),
    ],
)  # fmt: skip
def test_o_corte_nao_vale(texto, registro):
    assert v.frase_para_cortar(texto, registro) is None


TEXTO_COM_SOBRA = "Olá! Seu pedido já foi enviado; o rastreio é {rastreio}. Ele foi embrulhado com papel de presente."


async def test_o_corte_sai_so_com_o_ok_do_texto_cortado(
    db: AsyncSession, make_user, agente_ligado, envios, api, teto_limpo
):
    conversa = await _no_auto(db, make_user, agente_ligado)
    api.agente = [_ver._mensagem_do_agente(texto=TEXTO_COM_SOBRA)]
    api.veredito = [
        _ok(
            afirma_sem_lastro=["Ele foi embrulhado com papel de presente"],
            ok_para_envio_automatico=False,
        ),
        _ok(),
    ]

    r = await ia.gerar_rascunho(db, conversa)

    cortado = f"Olá! Seu pedido já foi enviado; o rastreio é {RASTREIO}."
    assert r.texto == cortado and r.status == RASCUNHO_PENDENTE and not r.precisa_humano, r.motivo
    assert [e["texto"] for e in envios] == [cortado]
    fatos = r.fatos["agente"]
    corte = fatos["corte_sem_lastro"]
    assert corte["saiu"] is True and corte["frase_tirada"].startswith("Ele foi embrulhado")
    assert corte["verificador_antes"]["problemas"] == [v.SEM_LASTRO]
    # o registro do verificador é o do texto que saiu (a trava do envio confere o MESMO texto)
    assert fatos["verificador"]["ok"] is True
    assert fatos["verificador"]["texto_sha"] == v.assinatura_do_texto(r.texto)
    assert v.trava(r, ligado=True) is None
    # o 2º pedido do verificador leu o texto cortado
    assert len(api.pedidos) == 2
    assert "papel de presente" not in api.pedidos[1]["messages"][0]["content"].split("RASCUNHO:")[1]


async def test_sem_o_ok_do_texto_cortado_fica_o_de_antes_para_pessoa(
    db: AsyncSession, make_user, agente_ligado, envios, api, teto_limpo
):
    conversa = await _no_auto(db, make_user, agente_ligado)
    api.agente = [_ver._mensagem_do_agente(texto=TEXTO_COM_SOBRA)]
    api.veredito = [
        _ok(
            afirma_sem_lastro=["Ele foi embrulhado com papel de presente"],
            ok_para_envio_automatico=False,
        ),
        _ok(responde_o_que_foi_perguntado=False, ok_para_envio_automatico=False),
    ]

    r = await ia.gerar_rascunho(db, conversa)

    assert (
        r.texto
        == f"Olá! Seu pedido já foi enviado; o rastreio é {RASTREIO}. Ele foi embrulhado com papel de presente."
    )
    assert r.precisa_humano and "verificador: afirma sem lastro" in r.motivo and envios == []
    fatos = r.fatos["agente"]
    assert fatos["corte_sem_lastro"]["saiu"] is False
    assert fatos["verificador"]["ok"] is False
    assert v.trava(r, ligado=True) == v.TRAVA_REPROVOU


async def test_com_outra_marca_nem_tenta_o_corte(
    db: AsyncSession, make_user, agente_ligado, envios, api, teto_limpo
):
    conversa = await _no_auto(db, make_user, agente_ligado)
    api.agente = [_ver._mensagem_do_agente(texto=TEXTO_COM_SOBRA)]
    api.veredito = [
        _ok(
            afirma_sem_lastro=["Ele foi embrulhado com papel de presente"],
            promete_acao_da_equipe=True,
            ok_para_envio_automatico=False,
        )
    ]

    r = await ia.gerar_rascunho(db, conversa)

    assert r.precisa_humano and envios == [] and len(api.pedidos) == 1
    assert "corte_sem_lastro" not in r.fatos["agente"]


# ─────────────── 7. a revisão adversarial (03/11/2026) ───────────────
#
# Cada caso é o que a revisão achou chegando ao verificador SEM nenhum motivo no commit 2e48acf8
# (e segurado no main 329d7b16): agora a 1ª camada o segura de novo; as liberações da análise
# continuam (os testes das seções 1–6).


def _chega_ao_verificador(c: Any) -> bool:
    f = c.final
    return (
        not c.sem_resposta
        and f is not None
        and f.status == RASCUNHO_PENDENTE
        and not f.precisa_humano
        and not getattr(f, "sem_auto", None)
    )


# Achado 1: a frase da garantia que INVERTE a regra do dono.
GARANTIA_INVERTIDA = [
    ("a garantia cobre se molhar?", "Sim! A garantia cobre defeitos e também danos por água.", None),
    ("a garantia cobre queda?", "Tem garantia contra quedas e tela quebrada.", None),
    ("tem garantia?", "A garantia cobre defeitos e mau uso.", URANYX),
    ("a garantia cobre se molhar?", "A garantia não cobre aparelho quebrado, mas cobre aparelho molhado.", None),
    ("tem garantia?", "Tem garantia, e depois da Shopee você aciona direto com a Uranyx.", URANYX),
    ("tem garantia?", "Tem garantia direto com a loja, sem precisar da Shopee.", None),
    ("quanto tempo de garantia?", "O aparelho tem garantia anual.", URANYX),
    ("tem garantia?", "O aparelho tem garantia anual.", URANYX),  # sem a pergunta do prazo
    ("cobre tela quebrada?", "Sim, tem garantia, inclusive para tela quebrada.", None),
    ("a garantia cobre a bateria?", "Sim, a garantia cobre defeitos, inclusive a bateria viciada.", None),
    ("tem garantia?", "Tem garantia. Depois é só acionar direto com a Uranyx.", URANYX),  # a oração sem "garantia"
    ("tem garantia?", "Tem garantia e a garantia cobre a tela.", None),
    ("tem garantia?", "A garantia não cobre arranhões, mas sim aparelho molhado.", None),
]  # fmt: skip


@pytest.mark.parametrize(("cliente", "texto", "garantias"), GARANTIA_INVERTIDA)
async def test_a_garantia_que_inverte_a_regra_fica_com_pessoa(cliente, texto, garantias):
    c = await _pela_proposta(
        _p(texto, "garantia"), [_m("cliente", cliente)], garantias=garantias, fichas=[FICHA_URANYX]
    )
    assert not _chega_ao_verificador(c), c.final.motivos


def test_a_frase_que_inverte_a_regra_nao_e_a_politica_pura():
    plano = checagens._plano
    for frase in (
        "A garantia cobre defeitos e também danos por água.",
        "Tem garantia contra quedas.",
        "A garantia cobre defeitos e mau uso.",
        "A garantia não cobre aparelho quebrado, mas cobre aparelho molhado.",
        "Tem garantia, e depois da Shopee você aciona direto com a Uranyx.",
        "Tem garantia direto com a loja, sem precisar da Shopee.",
        "O aparelho tem garantia anual.",
        "A garantia cobre defeitos, inclusive a bateria.",
        "A garantia cobre a tela.",
        "Tem garantia contra roubo.",
    ):
        assert not checagens.frase_da_garantia_geral(plano(frase)), frase
    # a regra (e as formas da política) continuam valendo
    for frase in (
        "Ela não cobre aparelho quebrado, molhado ou com mau uso.",
        "A nossa garantia não cobre aparelho quebrado, molhado ou com mau uso.",
        "A garantia não cobre quebrado nem molhado.",
        "A garantia cobre defeitos de fabricação.",
        "A garantia é contra defeitos de fabricação.",
        "Tem garantia sim, e a devolução é pela Shopee.",
    ):
        assert checagens.frase_da_garantia_geral(plano(frase)), frase


# Achado 2: o número do cliente dito como preço, frete, parcela, hora, prazo, quantidade ou spec.
NUMERO_COMO_VALOR = [
    ("custa 899?", "Sim, o preço é 899.", "outro"),
    ("o frete é 25?", "Sim, o frete fica 25.", "outro"),
    ("posso parcelar em 12?", "Sim, em 12 sem juros.", "outro"),
    ("vocês postam até as 15?", "Sim, postamos até as 15.", "prazo_envio"),
    ("chega em 5?", "Sim, chega em 5.", "prazo_entrega"),
    ("tem 3 disponíveis?", "Sim, são 3 disponíveis.", "estoque_variacao"),
    ("o valor é 150?", "O valor é 150, sim.", "outro"),
    ("a bateria é de 6000 mAh?", "Sim, a bateria é de 6000 mAh.", "espec_celular"),
    ("a câmera é de 108?", "Sim, a câmera é de 108 MP.", "espec_celular"),
    ("a tela é 6.9?", "Sim, a tela é de 6,9 polegadas.", "espec_celular"),
]  # fmt: skip


@pytest.mark.parametrize(("cliente", "texto", "assunto"), NUMERO_COMO_VALOR)
async def test_o_numero_do_cliente_como_valor_fica_inventado_pelo_caminho_inteiro(
    cliente, texto, assunto
):
    c = await _pela_proposta(_p(texto, assunto), [_m("cliente", cliente)], fichas=[FICHA_URANYX])
    assert not _chega_ao_verificador(c), c.final.motivos
    assert any(m.startswith(ia.MOTIVO_NUMERO_INVENTADO) for m in c.final.motivos), c.final.motivos


@pytest.mark.parametrize(
    ("texto", "numero", "cliente"),
    [
        ("Sim, sai 899 à vista.", "899", "sai 899?"),
        ("Fica 899 no total.", "899", "fica 899?"),
        ("Isso, o preço dele é 899 hoje.", "899", "é 899?"),
        ("Sim, sobram 3 no estoque.", "3", "tem 3?"),
        ("Pedidos feitos até as 15 saem no mesmo dia.", "15", "até as 15?"),
        ("Sim, temos a TV 43.", "43", "tem tv 43?"),  # a frase que confirma
        ("Bom dia, sim, a tela de 6,9 está disponível.", "6,9", "tem a tela de 6.9?"),
        ("O de 128 sai por 128.", "128", "o de 128?"),
    ],
)
def test_o_numero_do_cliente_so_sai_nomeando_o_produto_puro(texto, numero, cliente):
    assert checagens.numeros_do_cliente(texto, [numero], [cliente]) == []


@pytest.mark.parametrize(
    ("texto", "numero", "cliente"),
    [
        ("Qual anúncio de TV 43 você viu?", "43", "quero uma tv 43"),
        ("Qual é o anúncio da tela de 6,9 que você viu?", "6,9", "a tela de 6.9"),
        ("Para eu conferir se ele é o modelo 18 ou 17, qual é o anúncio?", "17", "é 18 ou 17"),
        ("Qual anúncio do iPhone 15 você viu?", "15", "tem iphone 15?"),
    ],
)
def test_o_numero_que_nomeia_o_produto_continua_do_cliente_puro(texto, numero, cliente):
    assert checagens.numeros_do_cliente(texto, [numero], [cliente]) == [numero]


# Achado 3: o "eu confiro" com alguém da loja nas palavras logo depois continua DURO.
@pytest.mark.parametrize(
    "texto",
    [
        "Qual é o número do pedido? Assim eu verifico junto à transportadora.",
        "Qual a cor que você quer? Assim eu consulto o estoque.",
        "Qual o modelo? Assim eu verifico junto ao fornecedor.",
        "Me diga o modelo que eu verifico com nossa equipe.",
        "Qual o número do pedido? Para eu verificar na transportadora.",
        "Qual a cor? Assim eu vejo no depósito.",
        "Qual é a cor? Assim eu confirmo a reserva.",
        "Qual é o modelo? Assim eu confiro com o setor responsável.",
    ],
)
def test_conferir_com_alguem_da_loja_continua_promessa_dura(texto):
    (p,) = checagens.conferir_promessa_dura(
        texto, acao="perguntar", assunto="estoque_variacao", textos_cliente=["tem azul?"]
    )
    assert p.codigo == checagens.PROMESSA_DURA and checagens.pendencia_dura(p)


@pytest.mark.parametrize(
    ("cliente", "texto", "assunto"),
    [
        ("cadê meu pedido?", "Qual é o número do pedido? Assim eu verifico junto à transportadora.", "rastreio_entrega"),
        ("tem azul?", "Qual a cor que você quer? Assim eu consulto o estoque.", "estoque_variacao"),
        ("vai voltar?", "Qual o modelo? Assim eu verifico junto ao fornecedor.", "estoque_variacao"),
    ],
)  # fmt: skip
async def test_conferir_com_alguem_da_loja_pelo_caminho_inteiro(cliente, texto, assunto):
    c = await _pela_proposta(_p(texto, assunto), [_m("cliente", cliente)], fichas=[FICHA_URANYX])
    assert not _chega_ao_verificador(c), c.final.motivos


# Achado 4: a estendida com o defeito relatado não é "é com a Shopee".
ESTENDIDA_COM_PEDIDO = {"numero": "{numero_pedido}", "entregue": True, "situacao": "concluido"}


@pytest.mark.parametrize(
    "cliente",
    [
        "meu celular parou de funcionar, comprei com garantia estendida da shopee, o que faço?",
        "o aparelho não está ligando, tenho o seguro da shopee",
        "deu defeito na tela, a garantia estendida cobre?",
    ],
)
async def test_a_estendida_com_o_defeito_relatado_fica_com_pessoa(cliente):
    texto = "A garantia estendida contratada na Shopee é tratada direto com a Shopee, e é por ela que se aciona."
    c = await _pela_proposta(
        _p(texto, "garantia_defeito"),
        [_m("cliente", cliente)],
        pedido=ESTENDIDA_COM_PEDIDO,
        conversa=_conv(pedido_marketplace="X1", etiqueta="pos_venda"),
        fichas=[FICHA_URANYX],
    )
    assert not _chega_ao_verificador(c), c.final.motivos
    assert _de_garantia(c.final), c.final.motivos


def test_a_estendida_hipotetica_continua_liberada_puro():
    ctx = agente_ctx.ContextoAgente.da_conversa(_conv(), None, modo=MODO_AUTO)
    texto = "A garantia estendida contratada na Shopee é tratada direto com a Shopee."
    for cliente, forma in (
        ("Se eu colocar Garantia estendida aí responsabilidade de vcs caso haver um defeito.", checagens.GARANTIA_ESTENDIDA),
        ("se vier com defeito, a garantia estendida é com quem?", checagens.GARANTIA_ESTENDIDA),
        ("meu celular parou, a garantia estendida é com quem?", None),
    ):  # fmt: skip
        base = SimpleNamespace(
            textos_rajada=[cliente], textos_cliente=[cliente], foco=[], ctx={}, fatos={}
        )
        assert (
            checagens.garantia_geral_pre_venda(
                texto, ctx=ctx, base=base, fichas=[], pedido=ESTENDIDA_COM_PEDIDO
            )
            == forma
        ), cliente


# Achado 5: o corte nunca tira a frase que é a resposta (com o cumprimento ou a interjeição antes,
# ou a 1ª frase depois do cumprimento).
@pytest.mark.parametrize(
    ("texto", "afirmacao"),
    [
        (
            "Bom dia, sim, o anúncio traz dual chip. Ele vem com capinha.",
            "o anúncio traz dual chip",
        ),
        ("Oi, sim, tem NFC. Ele também é dual chip.", "tem NFC"),
        ("Isso mesmo, tem NFC. Ele também é dual chip.", "tem NFC"),
        ("Olá! O A17 Pro Max tem NFC. Ele também é dual chip.", "O A17 Pro Max tem NFC"),
        ("Boa tarde! Infelizmente não tem NFC. Ele é dual chip.", "não tem NFC"),
        ("Claro! Acompanha carregador de 65 W. A cor é preta.", "Acompanha carregador de 65 W"),
    ],
)
def test_o_corte_nao_tira_a_frase_que_e_a_resposta(texto, afirmacao):
    assert v.frase_para_cortar(texto, _registro(afirmacao)) is None


CE443965 = "Olá! Lamentamos o transtorno: seu pedido foi postado em 09/10, e o acompanhamento do envio está em Minhas Compras > o pedido. Como o pedido já foi enviado, o reembolso é feito pela devolução: ao receber, peça a devolução em Minhas Compras."


def test_o_corte_nao_tira_a_frase_que_mais_fala_do_que_o_cliente_perguntou():
    # ce443965 (21/10, no replay): a frase sem lastro é a do reembolso que o cliente perguntou.
    afirmacao = "o reembolso é feito pela devolução"
    assert (
        v.frase_para_cortar(CE443965, _registro(afirmacao), ["e o reembolso, como fica?"]) is None
    )
    # sem as falas do cliente, a regra de antes (o corte)
    assert v.frase_para_cortar(CE443965, _registro(afirmacao)) is not None
    # 910f3c9d: o turno ("me manda o link…") partilha tanto com a frase que fica — o corte vale
    resto, frase = v.frase_para_cortar(
        F910F3C9D,
        _registro("o A18 Pro Max de tela de 6,9 polegadas na variação 16 GB / 128 GB Premium NFC"),
        ["Me manda o link pra eu pega o 18 com tela de 6.9"],
    )
    assert frase.startswith("Nele, é só escolher")
    # ad4d4b72 com a pergunta da memória: a frase que fica é a que mais responde
    assert v.frase_para_cortar(
        AD4D4B72, _registro("A opção de 256 GB do WP200 Pro está esgotada"), ["tem o WP200 Pro de 1 TB de memória?"]
    ) is not None  # fmt: skip


def test_o_corte_do_acolhimento_vale():
    texto = "Olá! Sinto muito pelo problema com a bateria. Para acionar, descreva o problema e mande um vídeo curto por aqui."
    resto, frase = v.frase_para_cortar(
        texto, _registro("Sinto muito pelo problema com a bateria"), ["a bateria não segura carga"]
    )
    assert frase.startswith("Sinto muito") and resto.startswith("Olá! Para acionar")
    # o acolhimento que também informa não é só acolhimento
    assert not v._so_acolhimento_do_corte(
        "Lamentamos o transtorno: seu pedido foi postado em 09/10."
    )


# Achado 6: só a fala do agente que o verificador liberou, com o MESMO texto, entra no JÁ ENVIOU.
def test_o_ja_enviado_so_com_a_prova_do_ok():
    gat = _m("cliente", "me manda o link", minutos=30)
    legado = _m(
        "loja",
        "Sim, ele é original e vem lacrado, com bateria de 6000 mAh.",
        origem=constantes.ORIGEM_IA,
        minutos=5,
    )
    nf = _m("loja", "Segue a nota fiscal do seu pedido.", origem=constantes.ORIGEM_IA, minutos=6)
    nf.rascunho_id = uuid4()  # o rascunho do agente que NÃO teve o ok (fora de `conferidas`)
    editada = _m(
        "loja",
        "O A18 Pro Max tem NFC e carregador de 65 W.",
        origem=constantes.ORIGEM_IA,
        minutos=7,
    )
    conferidas = _do_agente_com_ok(editada)
    editada.texto = "O A18 Pro Max tem NFC."  # o texto que saiu não é o que o verificador viu
    boa = _m(
        "loja", "O A18 Pro Max tem tela de 6,9 polegadas.", origem=constantes.ORIGEM_IA, minutos=8
    )
    conferidas |= _do_agente_com_ok(boa)
    msgs = [legado, nf, editada, boa, gat]
    (linha,) = v.ja_enviado_da_conversa(msgs, gat, (), conferidas)
    assert "6,9 polegadas" in linha
    # sem a prova (None, vazio), nenhuma — a seção não vai
    assert v.ja_enviado_da_conversa(msgs, gat, ()) == ()
    assert v.ja_enviado_da_conversa(msgs, gat, (), {}) == ()


async def test_respostas_conferidas_le_so_o_ok_do_agente(
    db: AsyncSession, make_user, agente_ligado
):
    from app.models.atendimento import AtendimentoRascunho
    from tests.test_atendimento_agente_ligacao import _do_agente
    from tests.test_atendimento_ia import _msg

    conversa = await _do_agente(db, make_user, modo="auto", auto_categorias=["rastreio"])
    gat = await _msg(db, conversa, "tem NFC?", ha=timedelta(hours=3))
    textos = {
        "ok": "O A18 Pro Max tem NFC.",
        "nao_ok": "O A18 Pro Max tem carregador de 65 W.",
        "legado": "Sim, ele é original e lacrado.",
        "sem_sha": "Ele é dual chip.",
    }
    fatos = {
        "ok": {"agente": {"verificador": {"ok": True, "texto_sha": v.assinatura_do_texto(textos["ok"])}}},
        "nao_ok": {"agente": {"verificador": {"ok": False, "texto_sha": v.assinatura_do_texto(textos["nao_ok"])}}},
        "legado": {"agente": {"verificador": {"ok": True, "texto_sha": v.assinatura_do_texto(textos["legado"])}}},
        "sem_sha": {"agente": {"verificador": {"ok": True}}},
    }  # fmt: skip
    msgs = []
    for i, chave in enumerate(textos):
        r = AtendimentoRascunho(
            conversa_id=conversa.id,
            mensagem_gatilho_id=gat.id,
            texto=textos[chave],
            categoria="outro",
            status="enviado",
            prompt_versao="v10" if chave == "legado" else constantes.PROMPT_VERSAO_AGENTE,
            fatos=fatos[chave],
        )
        db.add(r)
        await db.flush()
        m = await _msg(
            db,
            conversa,
            textos[chave],
            autor="loja",
            origem=constantes.ORIGEM_IA,
            ha=timedelta(hours=2, minutes=50 - i),
        )
        m.rascunho_id = r.id
        msgs.append((chave, m, r))
    await db.commit()
    agora = await _msg(db, conversa, "me manda o link", ha=timedelta(minutes=5))
    recentes = [gat, *(m for _c, m, _r in msgs), agora]

    conferidas = await v.respostas_conferidas(db, recentes, agora)

    (ok,) = [r for c, _m, r in msgs if c == "ok"]
    assert conferidas == {str(ok.id): v.assinatura_do_texto(textos["ok"])}
    (linha,) = v.ja_enviado_da_conversa(recentes, agora, (), conferidas)
    assert textos["ok"] in linha
    # sem sessão ou sem fala do agente: nenhuma (nunca levanta)
    assert await v.respostas_conferidas(None, recentes, agora) == {}
    assert await v.respostas_conferidas(db, [gat, agora], agora) == {}


async def test_o_verificador_ao_vivo_le_so_o_ja_enviado_com_o_ok(
    db: AsyncSession, make_user, agente_ligado, envios, api, teto_limpo
):
    from app.models.atendimento import AtendimentoRascunho
    from tests.test_atendimento_ia import _msg

    conversa = await _no_auto(db, make_user, agente_ligado)
    gat = await _msg(db, conversa, "o pedido já saiu?", ha=timedelta(hours=4))
    conferida = "Olá! O seu pedido segue em preparação na loja, sem data de envio ainda."
    legado = "Olá! Seu pedido é original e vem lacrado com nota fiscal."
    for i, (texto, versao, ok) in enumerate(
        [(conferida, constantes.PROMPT_VERSAO_AGENTE, True), (legado, "v10", True)]
    ):
        r = AtendimentoRascunho(
            conversa_id=conversa.id, mensagem_gatilho_id=gat.id, texto=texto, categoria="rastreio",
            status="enviado", prompt_versao=versao,
            fatos={"agente": {"verificador": {"ok": ok, "texto_sha": v.assinatura_do_texto(texto)}}},
        )  # fmt: skip
        db.add(r)
        await db.flush()
        m = await _msg(
            db,
            conversa,
            texto,
            autor="loja",
            origem=constantes.ORIGEM_IA,
            ha=timedelta(hours=3, minutes=50 - i),
        )
        m.rascunho_id = r.id
    await db.commit()
    api.agente = [_ver._mensagem_do_agente()]
    api.veredito = [_ok()]

    await ia.gerar_rascunho(db, conversa)

    assert api.pedidos, "o verificador não foi chamado"
    entrada = api.pedidos[0]["messages"][0]["content"]
    secao = entrada.split(v.CABECALHO_JA_ENVIADO, 1)[1].split("\n\n", 1)[0]
    assert conferida in secao and legado not in secao


# Achado 7: a linha da calibração do a80987a3 21:14 com a pergunta REAL; os espelhos da revisão.
def test_a_calibracao_tem_a_pergunta_real_e_os_espelhos_da_revisao():
    casos = {c["id"]: c for c in calibrar.ler_casos(calibrar.CASOS_PADRAO)}
    assert "an0311-boa-a80987a3-tem-garantia" not in casos
    real = casos["an0311-ruim-a80987a3-quantos-meses"]
    assert "cuantos meses" in real["perguntas"][0]
    assert real["esperado"]["responde_o_que_foi_perguntado"] is False
    assert real["esperado"]["ok_para_envio_automatico"] is False
    for id_ in (
        "an0311-ruim-garantia-cobre-agua",
        "an0311-ruim-garantia-mas-cobre-molhado",
        "an0311-ruim-garantia-direto-com-a-uranyx",
        "an0311-ruim-garantia-direto-com-a-loja",
        "an0311-ruim-garantia-anual",
        "an0311-ruim-numero-do-cliente-como-preco",
        "an0311-ruim-numero-do-cliente-como-prazo",
        "an0311-ruim-confiro-junto-a-transportadora",
        "an0311-ruim-consulto-o-estoque",
        "an0311-ruim-estendida-com-defeito",
        "an0311-ruim-corte-sem-a-resposta",
    ):
        assert casos[id_]["grupo"] == "an0311"
        assert casos[id_]["esperado"]["ok_para_envio_automatico"] is False, id_
