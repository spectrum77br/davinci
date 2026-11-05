# ruff: noqa: F811, E501 — fixtures importadas; as tabelas de casos reais
"""As regras do dono de 05/11/2026 no Mercado Livre (a análise de 04/11 14:23 a 05/11 07:12).

- Regra 1: a negativa honesta do prazo sai por TODAS as camadas (5590d694, 60f9cb15, dc962f66), e
  a promessa real continua recusada.
- Regra 2: a loja nunca altera o endereço de uma compra no ML (dc3e318f, 91e36877).
- Regra 3: o pedido de preço por quantidade ganha a resposta fixa do atacado (070f1828, 378b1cd9,
  8f27c582), e a quantidade acima do estoque (d75b1521) ou o estoque desconhecido vão para pessoa.

API falsa (httpx MockTransport dos testes): nada chama o Claude nem a plataforma. O verificador
de verdade só roda na calibração do dono, no servidor (o grupo `an0511`).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AtendimentoCanal,
    AtendimentoConversa,
    AtendimentoMensagem,
    AtendimentoRascunho,
    Integration,
    IntegrationPlatform,
    Product,
    ProductLink,
)
from app.models.enums import LinkSyncStatus
from app.services.atendimento import constantes, enviar, gravar, humano, ia, validador
from app.services.atendimento.agente import (
    acao_pessoa,
    checagens,
    fatos_loja,
    ferramentas,
    prompt,
    replay,
)
from app.services.atendimento.agente import contexto as agente_ctx
from app.services.atendimento.agente import verificador as v
from app.services.atendimento.agente.saida import Proposta
from scripts.agente_eval import calibrar_verificador as calibrar
from scripts.agente_eval import regatilho
from tests.test_atendimento_agente_decisoes_dono import AGORA, _auto, _cli, _conv, _menu
from tests.test_atendimento_agente_e2e import (  # noqa: F401 — fixtures
    _decisao,
    _do_agente,
    _final,
    _ligar,
    _usa,
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
from tests.test_atendimento_agente_replay import _Api as _ApiDoReplay
from tests.test_atendimento_agente_replay import _momento, ajustes  # noqa: F401 — fixture
from tests.test_atendimento_ia import _conversa as _conversa_ia
from tests.test_atendimento_ia import _importar_manual_base
from tests.test_atendimento_ia import _msg as _msg_ia

FIXA_ENDERECO = constantes.RESPOSTA_ENDERECO_ML
FRASE = constantes.FRASE_ATACADO_ML

# ═══════════════ Regra 1: a negativa honesta do prazo ═══════════════

NEGATIVAS_REAIS = {
    "5590d694": (
        "Não temos como garantir a entrega para amanhã. O prazo e o frete para o seu CEP aparecem "
        "no anúncio ao informar o CEP, antes de comprar. Se você já comprou, fale com a loja pelas "
        "mensagens da compra."
    ),
    "60f9cb15": (
        "Não temos como garantir a chegada amanhã. O frete e o prazo de entrega para o seu CEP "
        "aparecem no anúncio ao informar o CEP. Se você já comprou, fale com a loja pelas mensagens "
        "da compra, em Minhas compras."
    ),
    "dc962f66": (
        "Não temos como garantir a chegada amanhã. O prazo e o frete para o seu CEP aparecem no "
        "anúncio ao informar o CEP, e a previsão de entrega é definida pelo Mercado Livre. Se já "
        "comprou, fale com a loja pelas mensagens da compra."
    ),
    "regra": (
        "Não temos como garantir a chegada amanhã. O prazo e o frete para o seu CEP aparecem no "
        "anúncio ao informar o CEP. Se já comprou, fale com a loja pelas mensagens da compra."
    ),
}
PROMESSAS = [
    "Chega amanhã.",
    "Sim, chega amanhã.",
    "A chegada é amanhã.",
    "Garantimos a chegada amanhã.",
    "Garantimos a entrega para amanhã.",
    "A entrega será na sexta.",
    "A entrega é hoje.",
    "Entrega para hoje.",
    "A entrega está prevista para amanhã.",
    "A postagem é hoje.",
    # O vão entre a negação e o prazo dá a chegada por certa.
    "Não temos como garantir o horário da chegada amanhã.",
    "Não podemos garantir o horário da entrega amanhã.",
    "Não temos como confirmar o horário de entrega amanhã.",
    "Não temos como garantir a cor e chega amanhã.",
    "Não temos como confirmar o horário porque chega amanhã.",
    "Não temos como informar o horário pois chega amanhã.",
    "Não podemos informar a hora que chega amanhã.",
    "Não conseguimos informar o horário em que chega amanhã.",
    # Depois da negação, outro dia afirmado na mesma frase.
    "Não temos como garantir a chegada amanhã e sim na sexta.",
    "Não temos como garantir a chegada amanhã, só na sexta.",
    "Não temos como garantir a chegada amanhã, porém chega na sexta.",
    # O "não" que tranquiliza.
    "Não se preocupe que chega amanhã.",
]
NEGATIVAS_QUE_FICAM = [
    "Não temos como confirmar se ele sai hoje.",
    "Não podemos prometer o envio para hoje.",
    "Não conseguimos garantir a entrega até sexta.",
    "Não é possível garantir a chegada amanhã.",
    "Não temos como garantir a chegada amanhã nem na sexta.",
    "Confira se chega amanhã.",
]


def _ml(texto: str, canal: str = "pergunta") -> list[str]:
    final = validador.normalizar(texto, plataforma="ml", canal=canal)
    return validador.validar(final, plataforma="ml", canal=canal, origem=constantes.ORIGEM_IA)


@pytest.mark.parametrize("caso", sorted(NEGATIVAS_REAIS))
def test_r1_a_negativa_real_passa_nas_camadas_sem_o_modelo(caso):
    texto = NEGATIVAS_REAIS[caso]
    # O validador cru acusa o prazo, e é só isso que a isenção tira.
    assert _ml(texto) == [validador.MOTIVO_PRAZO]
    assert checagens.prazo_do_validador_sem_afirmacao(texto, plataforma="ml")
    assert checagens.conferir_prazo_sem_lastro(texto, plataforma="ml") == []


@pytest.mark.parametrize("caso", sorted(NEGATIVAS_REAIS))
async def test_r1_a_negativa_real_na_1a_camada_do_agente(caso):
    msgs = _cli("Este celular vai chegar amanhã pq no dia 10 eu não vô está aqui não")
    p = Proposta(
        acao="responder", texto=NEGATIVAS_REAIS[caso], assunto="prazo_antes_compra", confianca=0.9,
        acao_pessoa=None, motivo_pessoa="", evidencias=["regra:prazo_antes_compra"],
    )  # fmt: skip
    conv = _conv(plataforma="ml", canal="pergunta", pedido_marketplace=None)
    x = await _auto(p, msgs, conversa=conv)
    assert not any("prazo em números" in m for m in x.motivos), x.motivos
    assert checagens.PRAZO_SEM_LASTRO not in x.codigos


@pytest.mark.parametrize("texto", PROMESSAS)
def test_r1_a_promessa_continua_recusada(texto):
    assert validador.MOTIVO_PRAZO in _ml(texto), texto
    assert not checagens.prazo_do_validador_sem_afirmacao(texto, plataforma="ml"), texto


@pytest.mark.parametrize("texto", NEGATIVAS_QUE_FICAM)
def test_r1_as_outras_negativas_continuam_isentas(texto):
    assert checagens.prazo_do_validador_sem_afirmacao(texto, plataforma="ml"), texto


def test_r1_o_vao_do_estoque_nao_afirma_prazo():
    # A rodada 4b: "garantir a cor até a semana que vem" e o estoque nos próximos dias.
    for texto in (
        "Não temos como garantir a cor até a semana que vem.",
        "Não temos como garantir como estará o estoque nos próximos dias.",
    ):
        assert checagens.prazo_do_validador_sem_afirmacao(texto, plataforma="ml"), texto


def test_r1_so_a_ia_e_so_o_ml():
    # A pessoa escreve o que quiser (o `validar` só confere o prazo da IA).
    for texto in ("A chegada é amanhã.", "Entrega para hoje."):
        assert validador.validar(
            texto, plataforma="ml", canal="pergunta", origem=constantes.ORIGEM_HUMANO
        ) == []
    # As outras plataformas não mudam: nem a promessa nova nem a negativa nova.
    for plataforma, canal in (("shopee", "chat"), ("tiktok", "chat"), ("amazon", "email")):
        for texto in ("A chegada é amanhã.", "Não temos como garantir a entrega para amanhã."):
            motivos = validador.validar(
                texto, plataforma=plataforma, canal=canal, origem=constantes.ORIGEM_IA
            )
            assert validador.MOTIVO_PRAZO not in motivos, (plataforma, texto)
    assert validador.padroes_de_prazo("shopee") == validador._PRAZO
    assert len(validador.padroes_de_prazo("ml")) > len(validador._PRAZO)
    # A régua da negação também: fora do ML, o vão e o dia depois seguem como eram.
    for texto in (
        "Não temos como garantir o horário da chegada amanhã.",
        "Não temos como garantir a chegada amanhã, só na sexta.",
    ):
        assert checagens.prazo_do_validador_sem_afirmacao(texto)
        assert checagens.prazo_do_validador_sem_afirmacao(texto, plataforma="shopee")
    horario = "Não temos como garantir o horário da entrega amanhã."
    assert checagens.conferir_prazo_sem_lastro(horario, plataforma="shopee") == []
    (segura,) = checagens.conferir_prazo_sem_lastro(horario, plataforma="ml")
    assert segura.codigo == checagens.PRAZO_SEM_LASTRO


# ── O envio (o portão que recusou dc962f66) ──


async def _conversa_ml(
    db: AsyncSession,
    make_user,
    *,
    canal: str = "pergunta",
    pedido: str | None = None,
    titulo: str = "Mala Charlot's Park M1-m6 Violeta-escuro Lisa",
    item: str | None = "MLB7000000001",
    externo_id: str = "ml-1",
) -> AtendimentoConversa:
    conversa = await _conversa_ia(
        db, make_user, plataforma="ml", canal=canal, platform=IntegrationPlatform.ML,
        pedido=pedido, externo_id=externo_id,
    )  # fmt: skip
    conversa.anuncio_titulo = titulo
    conversa.anuncio_id = item
    await db.commit()
    return conversa


async def _rascunho(
    db: AsyncSession, conversa: Any, texto: str, fatos: dict, *, status: str = "pendente"
) -> AtendimentoRascunho:
    r = AtendimentoRascunho(
        conversa_id=conversa.id, texto=texto, categoria="prazo_antes_compra", status=status,
        precisa_humano=False, validador_ok=True, prompt_versao="ag1", fatos=fatos,
    )  # fmt: skip
    db.add(r)
    await db.commit()
    await db.refresh(r)
    return r


async def _envio(db: AsyncSession, conversa: Any, texto: str, r: Any) -> str:
    """Os dois passos do envio: o `_preparar_texto` e, na recusa, a isenção do agente."""
    try:
        return enviar._preparar_texto(texto, conversa=conversa, origem=constantes.ORIGEM_IA)
    except enviar.EnvioRecusado as recusa:
        return await enviar._texto_com_isencao_do_agente(
            db, conversa, texto, origem=constantes.ORIGEM_IA, rascunho_id=r.id, recusa=recusa
        )


@pytest.mark.parametrize("caso", sorted(NEGATIVAS_REAIS))
async def test_r1_o_envio_aceita_a_negativa_do_agente(db: AsyncSession, make_user, caso):
    conversa = await _conversa_ml(db, make_user)
    r = await _rascunho(db, conversa, NEGATIVAS_REAIS[caso], {"agente": {"isencoes": []}})
    assert await _envio(db, conversa, NEGATIVAS_REAIS[caso], r) == NEGATIVAS_REAIS[caso]


@pytest.mark.parametrize(
    "texto",
    [
        "Garantimos a chegada amanhã.",
        "A chegada é amanhã.",
        "Não temos como garantir o horário da chegada amanhã.",
        "Não temos como garantir a chegada amanhã, só na sexta.",
        # O motivo de outro tipo continua recusando: só o do prazo sai.
        "Não temos como garantir a chegada amanhã. Custa R$ 199,90.",
    ],
)
async def test_r1_o_envio_recusa_a_promessa_do_agente(db: AsyncSession, make_user, texto):
    conversa = await _conversa_ml(db, make_user)
    r = await _rascunho(db, conversa, texto, {"agente": {"isencoes": []}})
    with pytest.raises(enviar.EnvioRecusado):
        await _envio(db, conversa, texto, r)


async def test_r1_o_legado_e_a_shopee_no_envio_nao_mudam(db: AsyncSession, make_user):
    texto = NEGATIVAS_REAIS["dc962f66"]
    conversa = await _conversa_ml(db, make_user)
    legado = await _rascunho(db, conversa, texto, {})
    with pytest.raises(enviar.EnvioRecusado):
        await _envio(db, conversa, texto, legado)
    shopee = await _conversa_ia(db, make_user, pedido=None, externo_id="sh-1")
    r = await _rascunho(db, shopee, texto, {"agente": {"isencoes": []}})
    with pytest.raises(enviar.EnvioRecusado):
        await _envio(db, shopee, texto, r)


# ── O verificador: o v19 e a calibração ──


def test_v19_o_sistema_diz_o_que_vale():
    s = v.SISTEMA
    assert v.VERSAO == "v19"
    assert "o motivo é contexto, não outra pergunta" in s
    # O item 7 novo não muda o critério das outras plataformas (a entrada traz a PLATAFORMA).
    assert "Só com PLATAFORMA Mercado Livre: a pergunta de se chega em certo dia" in s
    assert "sem pedido o texto não precisa decidir se o cliente já comprou" in s
    assert "com pedido nas EVIDÊNCIAS, mandar ao anúncio não trata" in s
    assert "não permite à loja alterar o endereço" in s


def _casos_an0511() -> dict[str, dict]:
    return {c["id"]: c for c in calibrar.ler_casos(calibrar.CASOS_PADRAO) if c["grupo"] == "an0511"}


def test_a_calibracao_tem_os_casos_e_os_espelhos():
    casos = _casos_an0511()
    assert len(casos) == 17
    boas = {k for k, c in casos.items() if c["esperado"]["ok_para_envio_automatico"]}
    assert boas == {k for k in casos if k.startswith("an0511-boa-")}
    assert len(boas) == 7
    # O falso bloqueio de 60f9cb15 tem de passar, com o texto real e o turno real.
    falso = casos["an0511-boa-60f9cb15"]
    assert falso["texto"] == NEGATIVAS_REAIS["60f9cb15"]
    assert falso["esperado"]["responde_o_que_foi_perguntado"] is True
    assert not any("numero" in e for e in falso["evidencias"])
    assert casos["an0511-boa-5590d694"]["texto"] == NEGATIVAS_REAIS["5590d694"]
    assert casos["an0511-boa-dc962f66"]["texto"] == NEGATIVAS_REAIS["dc962f66"]
    # O texto enganoso de dc3e318f é o espelho; a resposta aprovada é a boa dos quatro cenários.
    assert "só é possível" in casos["an0511-ruim-endereco-dc3e318f"]["texto"]
    for k in (
        "an0511-boa-endereco-pergunta",
        "an0511-boa-endereco-nao-postado",
        "an0511-boa-endereco-postado",
        "an0511-boa-endereco-opcao-4",
    ):
        assert casos[k]["texto"] == FIXA_ENDERECO
    assert all(not calibrar.contaminado(c) for c in casos.values())
    # O critério da pergunta do dia é só do ML: o mesmo texto, na Shopee, não é o bom.
    shopee = casos["an0511-ruim-shopee-sem-a-regra-do-ml"]
    assert shopee["texto"] == falso["texto"] and shopee["plataforma"] == "shopee"
    assert shopee["esperado"]["ok_para_envio_automatico"] is False


def test_a_calibracao_do_endereco_le_a_orientacao_do_ml():
    casos = _casos_an0511()
    entrada = calibrar.entrada_do_caso(casos["an0511-boa-endereco-nao-postado"])
    assert "No Mercado Livre a loja nunca altera o endereço" in entrada
    assert "Não postado: alterar_endereco" not in entrada


# ═══════════════ Regra 2: o endereço no Mercado Livre ═══════════════

PEDIDO_NAO_POSTADO = {
    "pedido": {
        "numero": "90001", "numeroloja": "2000001", "situacao": "Em aberto",
        "itens": [{"descricao": "Celular", "quantidade": 1}],
    },
    "logistica": None, "chamados": [], "devolucoes": [],
}  # fmt: skip
TRES_FALAS_91E36877 = [
    "Olá! Recebemos o seu pedido de alterar o endereço de entrega do celular. Por aqui, não é preciso enviar o endereço nem outros dados pessoais.",
    "Olá! Recebemos o seu pedido de alteração do endereço de entrega. Não é preciso mandar dados pessoais por aqui.",
    "Olá! Recebemos o seu pedido de alterar o endereço de entrega. Não é preciso mandar o endereço por aqui, pois dados pessoais não são necessários nesta conversa.",
]
ENGANOSA_DC3E318F = (
    "Depois da compra paga, a alteração do endereço só é possível se o pedido ainda não tiver sido "
    "enviado. Depois do envio, a plataforma não permite alterar o endereço, e a entrega segue o "
    "endereço do pedido. Se você já comprou, fale com a loja pelas mensagens da compra (Minhas "
    "compras > a compra > mensagens)."
)


def _gatilhos(conv: Any, msgs: list, *, postado: str | None, ctx: dict | None = None) -> list[str]:
    return [
        g.codigo
        for g in acao_pessoa.gatilhos(
            conv, msgs, ctx or PEDIDO_NAO_POSTADO, gatilho=msgs[-1], postado=postado,
            chaves_ativas=frozenset(), agora=AGORA,
        )
    ]  # fmt: skip


def _turno_do_menu() -> list:
    return [_menu(), *_cli("4")]


@pytest.mark.parametrize(
    "msgs",
    [
        _cli("Quero alterar o endereço de entrega"),
        _cli("O CEP está errado"),
        _cli("Quero mudar o destinatário"),
        _cli("pode entregar na casa da minha mãe?"),
        _turno_do_menu(),
    ],
    ids=["pede", "cep", "destinatario", "outra-casa", "opcao-4"],
)
@pytest.mark.parametrize("postado", [acao_pessoa.POSTADO_NAO, None])
def test_r2_no_ml_a_pista_nao_abre_alterar_endereco(msgs, postado):
    ml = _conv(plataforma="ml", canal="pos_venda")
    assert constantes.ACAO_ALTERAR_ENDERECO not in _gatilhos(ml, msgs, postado=postado)
    assert acao_pessoa.pede_alterar_endereco(msgs, msgs[-1])
    # Fora do ML, a regra de sempre: não postado, alterar_endereco.
    shopee = _conv()
    assert constantes.ACAO_ALTERAR_ENDERECO in _gatilhos(shopee, msgs, postado=postado)


def test_r2_a_caixa_humano_e_o_outro_pedido_seguem_o_mesmo():
    msgs = _cli("Quero alterar o endereço de entrega")
    t = humano.triar(
        _conv(plataforma="ml", canal="pos_venda"), msgs, PEDIDO_NAO_POSTADO, so_humano=(),
        gatilho=msgs[-1], agente=True, postado=acao_pessoa.POSTADO_NAO, agora=AGORA,
    )  # fmt: skip
    assert constantes.ACAO_ALTERAR_ENDERECO not in t.assuntos
    assert constantes.HUMANO_ACAO_PESSOA not in t.motivos
    t = humano.triar(
        _conv(), msgs, PEDIDO_NAO_POSTADO, so_humano=(), gatilho=msgs[-1], agente=True,
        postado=acao_pessoa.POSTADO_NAO, agora=AGORA,
    )  # fmt: skip
    assert constantes.ACAO_ALTERAR_ENDERECO in t.assuntos
    # O OUTRO pedido lido por ferramenta.
    def dele(**kw: Any) -> list[str]:
        return [
            g.codigo
            for g in acao_pessoa.gatilhos_de_outro_pedido(
                PEDIDO_NAO_POSTADO, acao_pessoa.POSTADO_NAO, msgs, msgs[-1], **kw
            )
        ]

    assert constantes.ACAO_ALTERAR_ENDERECO in dele()
    assert constantes.ACAO_ALTERAR_ENDERECO in dele(plataforma="shopee")
    assert constantes.ACAO_ALTERAR_ENDERECO not in dele(plataforma="ml")


def _proposta(texto: str = "", **kw: Any) -> Proposta:
    campos = {
        "acao": "responder", "texto": texto, "assunto": "endereco", "confianca": 0.8,
        "acao_pessoa": None, "motivo_pessoa": "", "evidencias": [],
    }  # fmt: skip
    return Proposta(**{**campos, **kw})


def _ctx(plataforma: str = "ml", canal: str = "pos_venda") -> Any:
    return agente_ctx.ContextoAgente.da_conversa(
        _conv(plataforma=plataforma, canal=canal), None, modo=constantes.MODO_AUTO
    )


def _no_ml(p: Proposta, *, pede: bool = True, plataforma: str = "ml") -> Proposta:
    msgs = _cli("Quero alterar o endereço de entrega" if pede else "Bom dia, tudo bem?")
    return checagens.endereco_no_ml_da_proposta(p, _ctx(plataforma), msgs, msgs[-1])


@pytest.mark.parametrize(
    "texto",
    [
        ENGANOSA_DC3E318F,
        *TRES_FALAS_91E36877,
        "",
        "A alteração do endereço só é possível antes do envio.",
        "Antes do envio, fale com a loja pelas mensagens da compra para alterar o endereço.",
        "Vamos alterar o endereço da compra para você.",
        "Me passe o novo endereço que eu altero.",
        "Informe o endereço correto por aqui.",
        # As palavras que as réguas do legado leem como outra coisa.
        "Não conseguimos trocar o endereço de uma compra: o Mercado Livre não permite.",
        "A loja não faz a troca de endereço de uma compra no Mercado Livre.",
        "A loja não pode corrigir o endereço da compra.",
        "Por aqui não precisa mandar o endereço novo. O Mercado Livre não permite alterar dados.",
    ],
)
def test_r2_a_proposta_errada_vira_a_resposta_aprovada(texto):
    novo = _no_ml(_proposta(texto))
    assert novo.texto == FIXA_ENDERECO
    assert novo.acao == "responder" and novo.acao_pessoa is None and novo.motivo_pessoa == ""
    assert novo.assunto == "endereco" and "regra:endereco" in novo.evidencias
    assert novo.confianca >= 0.9


def test_r2_a_acao_do_modelo_e_o_assunto_publico_viram_resposta():
    novo = _no_ml(
        _proposta(
            TRES_FALAS_91E36877[0], acao="pessoa", acao_pessoa=constantes.ACAO_ALTERAR_ENDERECO,
            motivo_pessoa="alterar o endereço",
        )
    )  # fmt: skip
    assert novo.acao == "responder" and novo.acao_pessoa is None and novo.texto == FIXA_ENDERECO
    publica = _no_ml(
        _proposta("A equipe verifica.", acao="pessoa", assunto="posvenda_pergunta_publica",
                  acao_pessoa=constantes.ACAO_ALTERAR_ENDERECO)
    )  # fmt: skip
    assert publica.assunto == "endereco" and publica.texto == FIXA_ENDERECO
    publica2 = _no_ml(_proposta("Fale com a loja.", assunto="posvenda_pergunta_publica"))
    assert publica2.assunto == "endereco" and publica2.texto == FIXA_ENDERECO
    # Sem a pista, o assunto público segue o caminho dele.
    igual = _proposta("Fale com a loja.", assunto="posvenda_pergunta_publica")
    assert _no_ml(igual, pede=False) == igual


@pytest.mark.parametrize(
    "texto",
    [
        FIXA_ENDERECO,
        "Por questão de segurança, não podemos alterar o endereço da compra.",
        "A entrega segue o endereço do pedido e a loja não passa recado ao entregador.",
        f"{FIXA_ENDERECO} O celular é o modelo do anúncio.",
    ],
)
def test_r2_o_texto_que_ja_nega_fica_como_o_modelo_escreveu(texto):
    assert _no_ml(_proposta(texto)).texto == texto


def test_r2_as_outras_plataformas_e_as_outras_acoes_ficam():
    errado = _proposta(ENGANOSA_DC3E318F)
    assert _no_ml(errado, plataforma="shopee") == errado
    pessoa = _proposta(
        ENGANOSA_DC3E318F, acao="pessoa", acao_pessoa=constantes.ACAO_ALTERAR_ENDERECO
    )
    assert _no_ml(pessoa, plataforma="shopee") == pessoa
    # A outra ação de pessoa continua com a pessoa, no ML também.
    outra = _proposta("", acao="pessoa", acao_pessoa=constantes.ACAO_PEDIU_PESSOA)
    assert _no_ml(outra) == outra
    # Outro assunto, sem a pista, não muda.
    prazo = _proposta("O prazo aparece no anúncio.", assunto="prazo_antes_compra")
    assert _no_ml(prazo, pede=False) == prazo
    # Com a pista, outro assunto que não fala de mudar o endereço também não muda.
    assert _no_ml(prazo) == prazo
    # O texto que já nega, em outro assunto, fica como veio.
    misto = _proposta(f"{FIXA_ENDERECO} O prazo aparece no anúncio.", assunto="prazo_antes_compra")
    assert _no_ml(misto) == misto


def test_r2_o_assunto_errado_com_a_fala_errada_vira_a_resposta_aprovada():
    # "Trocar o endereço" lido como troca: a recusa de direito seguraria a resposta certa.
    novo = _no_ml(_proposta("Não conseguimos trocar o endereço.", assunto="troca_devolucao"))
    assert novo.texto == FIXA_ENDERECO and novo.assunto == "endereco"
    errado = _proposta("Não conseguimos trocar o endereço.", assunto="troca_devolucao")
    assert _no_ml(errado, pede=False) == errado


@pytest.mark.parametrize(
    ("canal", "postado", "ctx", "msgs"),
    [
        ("pergunta", None, None, _cli("Tem como mudar o endereço depois que você finaliza a compra e paga")),
        ("pos_venda", acao_pessoa.POSTADO_NAO, PEDIDO_NAO_POSTADO, _cli("Quero alterar o endereço de entrega")),
        ("pos_venda", acao_pessoa.POSTADO_SIM, PEDIDO_NAO_POSTADO, _cli("Quero alterar o endereço de entrega")),
        ("pos_venda", None, PEDIDO_NAO_POSTADO, _cli("Quero alterar o endereço de entrega")),
        ("pos_venda", acao_pessoa.POSTADO_NAO, PEDIDO_NAO_POSTADO, _turno_do_menu()),
    ],
    ids=["pergunta", "nao-postado", "postado", "desconhecido", "opcao-4"],
)
async def test_r2_a_resposta_aprovada_passa_nas_camadas_sem_o_modelo(canal, postado, ctx, msgs):
    assert validador.validar(
        FIXA_ENDERECO, plataforma="ml", canal=canal, origem=constantes.ORIGEM_IA
    ) == []
    assert len(FIXA_ENDERECO) <= 350
    FIXA_ENDERECO.encode("iso-8859-1")
    conv = _conv(
        plataforma="ml", canal=canal, pedido_marketplace=None if ctx is None else "2000001"
    )
    p = _proposta(FIXA_ENDERECO, confianca=0.9, evidencias=["regra:endereco"])
    x = await _auto(p, msgs, ctx_pedido=ctx, postado=postado, conversa=conv)
    assert x.motivos == [], x.motivos
    assert checagens.ENDERECO_NO_ML not in x.codigos
    assert x.ctx.acoes_pessoa == []


@pytest.mark.parametrize(
    "texto",
    [
        ENGANOSA_DC3E318F,
        "Envie o novo endereço por aqui.",
        "Informe o endereço correto por aqui.",
        "Pode me passar o endereço completo?",
        "A alteração do endereço só é possível antes do envio.",
        "Dá para alterar se o pedido ainda não foi enviado.",
        "Antes do envio, fale com a loja para alterar o endereço.",
        "Se quiser alterar o endereço, fale com a loja pelas mensagens da compra.",
        "Vamos alterar o endereço.",
        "A loja não altera o endereço da compra para a Rua Major Branco.",
        "A loja não altera o endereço para o CEP 72979-000.",
    ],
)
def test_r2_a_trava_do_que_escapou(texto):
    (p,) = checagens.conferir_endereco_no_ml(
        texto, acao="responder", assunto="endereco", plataforma="ml"
    )
    assert p.codigo == checagens.ENDERECO_NO_ML and checagens.ENDERECO_NO_ML in checagens.DURAS
    # Fora do ML, não.
    assert checagens.conferir_endereco_no_ml(
        texto, acao="responder", assunto="endereco", plataforma="shopee"
    ) == []


@pytest.mark.parametrize(
    "texto",
    [
        FIXA_ENDERECO,
        "Não precisa mandar o endereço por aqui. A loja não altera o endereço da compra.",
        "A entrega segue o endereço do pedido e a loja não passa recado ao entregador.",
    ],
)
def test_r2_a_trava_deixa_a_resposta_certa(texto):
    assert checagens.conferir_endereco_no_ml(
        texto, acao="responder", assunto="endereco", plataforma="ml"
    ) == []


def test_r2_a_trava_nao_le_outro_assunto():
    # "Fale com a loja" é a nota da pergunta pública nos outros assuntos do ML.
    assert checagens.conferir_endereco_no_ml(
        NEGATIVAS_REAIS["regra"], acao="responder", assunto="prazo_antes_compra", plataforma="ml"
    ) == []


def test_r2_a_orientacao_por_plataforma():
    no_ml = prompt.orientacao_do_assunto("endereco", "ml")
    assert "nunca altera" in no_ml and "alterar_endereco" not in no_ml
    # A instrução de recebimento fica (31ab491d lê a orientação do endereço).
    assert "a loja não passa recado ao entregador" in no_ml
    for plataforma in ("shopee", "tiktok", "magalu", "amazon", None):
        assert prompt.orientacao_do_assunto("endereco", plataforma) == prompt.ORIENTACAO["endereco"]
    assert prompt.orientacao_do_assunto("desconto", "ml") == prompt.ORIENTACAO["desconto"]
    linhas = "\n".join(v.orientacao_aprovada("endereco", "ml"))
    assert "nunca altera" in linhas and "Não postado" not in linhas
    assert "Não postado: alterar_endereco" in "\n".join(v.orientacao_aprovada("endereco", "shopee"))
    assert "nunca altera" in "\n".join(v.orientacao_aprovada("entrega_instrucoes", "ml"))


def test_r2_o_bloco_do_ml_traz_a_nota_e_cabe():
    cats = _categorias_do_manual()
    ml = prompt.bloco_da_plataforma("ml", cats)
    assert FIXA_ENDERECO in ml and "a ação alterar_endereco não existe aqui" in ml
    assert len(ml) / 3.5 <= 5_250
    for outra in ("shopee", "tiktok", "amazon"):
        assert FIXA_ENDERECO not in prompt.bloco_da_plataforma(outra, cats)


# ── De ponta a ponta, com a API falsa ──


async def _endereco_e2e(db, make_user, *, canal, pedido, fala):
    await _importar_manual_base(db)
    conversa = await _conversa_ml(db, make_user, canal=canal, pedido=pedido, titulo="Celular A17")
    await _ligar(db, conversa, modo=constantes.MODO_AUTO, auto_orientacao=["endereco"])
    await _msg_ia(db, conversa, fala, ha=timedelta(minutes=30))
    await db.refresh(conversa)
    return conversa


async def test_r2_e2e_91e36877_o_modelo_pede_a_acao_e_o_agente_responde(
    db: AsyncSession, make_user, agente_ligado, api, envios
):
    conversa = await _endereco_e2e(
        db, make_user, canal="pos_venda", pedido="2000001",
        fala="Quero alterar o endereço de entrega",
    )  # fmt: skip
    api.roteiro = [
        _final(
            acao="pessoa", texto=TRES_FALAS_91E36877[2], assunto="endereco", confianca=0.8,
            acao_pessoa=constantes.ACAO_ALTERAR_ENDERECO, motivo_pessoa="alterar o endereço",
        )
    ]  # fmt: skip

    r = await ia.gerar_rascunho(db, conversa)

    agente = _do_agente(r)
    assert r.texto == FIXA_ENDERECO and r.categoria == "endereco" and not r.precisa_humano, r.motivo
    assert agente["acoes_pessoa"] == [] and agente["acao"] == "responder"
    # O verificador recebe a orientação do ML, e nunca o "Não postado: alterar_endereco".
    (pedido,) = api.verificador
    entrada = pedido["messages"][0]["content"]
    assert "No Mercado Livre a loja nunca altera o endereço" in entrada
    assert "Não postado: alterar_endereco" not in entrada
    assert [e["texto"] for e in envios] == [FIXA_ENDERECO] and _decisao(r) == "enviado", r.fatos


async def test_r2_e2e_dc3e318f_a_frase_enganosa_nao_sai_na_pergunta_publica(
    db: AsyncSession, make_user, agente_ligado, api, envios
):
    conversa = await _endereco_e2e(
        db, make_user, canal="pergunta", pedido=None,
        fala="Tem como mudar o endereço depois que você finaliza a compra e paga",
    )  # fmt: skip
    api.roteiro = [_final(texto=ENGANOSA_DC3E318F, assunto="endereco", confianca=0.9)]

    r = await ia.gerar_rascunho(db, conversa)

    assert r.texto == FIXA_ENDERECO
    assert [e["texto"] for e in envios] == [FIXA_ENDERECO] and _decisao(r) == "enviado", r.fatos


async def test_r2_e2e_o_manual_do_ml_vem_com_a_regra_do_dono(
    db: AsyncSession, make_user, agente_ligado, api, envios
):
    conversa = await _endereco_e2e(
        db, make_user, canal="pos_venda", pedido="2000001",
        fala="Quero alterar o endereço de entrega",
    )  # fmt: skip
    api.roteiro = [
        _usa("consultar_manual", assunto="endereco", pergunta="alterar o endereço"),
        _final(texto=FIXA_ENDERECO, assunto="endereco", confianca=0.9),
    ]

    await ia.gerar_rascunho(db, conversa)

    resultado = api.resultado(1)
    assert "No Mercado Livre a loja nunca altera" in resultado
    assert ferramentas.POR_CIMA_DA_REGRA_NA_PLATAFORMA[("ml", "endereco")] in resultado


# ═══════════════ Regra 3: o atacado no Mercado Livre ═══════════════


@pytest.mark.parametrize(
    ("fala", "esperado"),
    [
        # Os quatro reais.
        ("consegue um valor melhor pra comprar 20UN?", 20),
        ("consegue um valor melhor pra 15UN?", 15),
        ("Consegue um valor melhor pra 20UN?", 20),
        ("Quero um orçamento para 60 delas", 60),
        ("tem desconto para 20 unidades?", 20),
        ("preciso de 100 frasqueiras, qual o melhor preço", 100),
        ("valor por 20 unidades?", 20),
        ("Tenho interesse em comprar 2 unidades vc consegue melhorar o preço?", 2),
        ("consegue preço para 20 ou 30 unidades?", 30),
        ("Preciso de 50 unidades, qual o valor?", 50),
        ("Valor de 20 malas?", 20),
        ("20 unidades, qual o valor?", 20),
        ("quanto fica 30 unidades?", 30),
        ("Bom dia, 20 unidades qual o valor?", 20),
        # As três falas reais da volta, que a régua do seguimento lê (`e92cc16f` tem o "com").
        ("queria os 20% de desconto para 20 UN no atacado https://mercadolivre.com.br/x", 20),
        ("oii gostaria de 20% de desconto para 15UN no atacado https://mercadolivre.com.br/x", 15),
        ("oii, gostaria do link com 20% off para 20UN https://mercadolivre.com.br/x", None),
        # A quantidade inteira: o "mil", a dúzia e os dois lotes (o estoque é conferido com ela).
        ("Quero 20 mil malas, qual o valor?", 20_000),
        ("Quero 2 mil unidades, qual o valor?", 2_000),
        ("Preciso de 1.000 unidades, qual o valor?", 1_000),
        ("Preciso de 30 duzias, qual o valor?", 360),
        ("Quero 40 malas P e 40 malas G, qual o valor?", 80),
        ("preço para 20 unidades ou 30 unidades?", 30),
        ("Quero 40 unidades, 40 unidades da outra, qual o valor?", 80),
        # A mesma quantidade repetida em outra fala do turno não soma.
        ("quero 20 unidades consegue preço pra 20 unidades?", 20),
        # A cortesia não é outra dúvida.
        ("Consegue um valor melhor pra 20UN? Obrigado", 20),
        ("Oi, tudo bem? Consegue um valor melhor pra 20UN?", 20),
        ("Boa tarde! Tudo bom? Consegue um valor melhor pra 20UN? Valeu!", 20),
        ("Consegue um valor melhor pra 20UN? E envia pra SC?", None),
        # O número sem a unidade não é a quantidade: é o percentual, o valor, o tamanho, o peso.
        ("Quero 20 por cento de desconto, consegue?", None),
        ("Queria 15 por cento de desconto nessa mala", None),
        ("quero 20 de desconto", None),
        ("Preciso de 30 de desconto pra fechar", None),
        ("quero 24 qual o preço?", None),
        ("Quero 28 qual o valor?", None),
        ("Preciso de 23 quilos, qual o valor?", None),
        ("quero 24 preta, qual o valor?", None),
        ("quero 15 kilos, valor?", None),
        ("Quero 20 da roxa, qual o valor?", None),
        ("se eu comprar 20 tem desconto?", None),
        ("Quero 20 celulares, qual valor?", None),
        # Negação, compra passada, venda, descrição e dúvida sobre o preço não pedem o lote.
        ("Não quero 20 unidades, só uma, qual o valor?", None),
        ("Nao preciso de 20 malas, so uma. tem desconto?", None),
        ("Comprei 30 malas e paguei valor errado", None),
        ("Tenho 20 malas pra vender, qual valor vocês pagam?", None),
        ("Cabem 15 peças de roupa nessa mala? qual o valor", None),
        ("Quanto custa a mala de 20 peças?", None),
        ("Quanto custa a mala para 20 peças de roupa?", None),
        ("Esse preço é para 20 unidades?", None),
        # A personalização sem as palavras de sempre.
        ("Valor de 20 malas com meu nome gravado?", None),
        ("20 malas com a marca da minha empresa, qual valor?", None),
        ("você consegue personalizar com o nome da empresa para um pedido de 30 unidades ?", None),
        # O que a quantidade não é.
        ("desconto para 10 de novembro?", None),
        ("comprar 20 de novembro tem desconto", None),
        ("kit 10 peças qual o valor?", None),
        ("kit com 20 peças qual o valor", None),
        ("mala de 20 polegadas qual o valor?", None),
        ("qual valor da mala 28 polegadas?", None),
        ("parcela em 10x tem desconto?", None),
        ("quero 20", None),
        ("tem desconto?", None),
        ("20% de desconto?", None),
        # A contraoferta, a personalização e a outra dúvida junto: o caminho de sempre.
        ("quero 20 reais de desconto", None),
        ("20 unidades por 1000?", None),
        ("faz 20 unidades por 1000 reais?", None),
        ("quero 30 unidades personalizadas com logo, qual o valor?", None),
        ("pra 20 unidades tem desconto? envia pra SC?", None),
        ("pra 20 unidades tem desconto e qual o prazo", None),
        ("qual o frete e o desconto pra 20 unidades", None),
        ("20 unidades com desconto, troca o endereço?", None),
        ("tem 20 unidades em estoque por esse valor", None),
    ],
)
def test_r3_a_quantidade_do_pedido(fala, esperado):
    assert acao_pessoa.quantidade_de_atacado(validador.plano_de(fala)) == esperado


@pytest.mark.parametrize(
    ("titulo", "esperado"),
    [
        ("Frasqueira Rígida De Mão De Viagem 12 Polegadas Roxo Escuro M2", True),
        ("Mala Charlot's Park M1-m6 Violeta-escuro Lisa", True),
        ("Frasqueira Maleta Maquiagem Profissional Com Alça Necessaire Viagem 8 Polegadas Organizado", True),
        ("Mala Viagem Policarbonato Aluminio Usb Tsa 10kg 20 Polegadas Bordo - Branco", True),
        ("Cappa ** Uranyx Hotwav A17 Pro Max 128 Gb Memoria 12 Gb Ram Laranja - 12.128", False),
        ("Celular Doogee Note 128gb", False),
        ("Capa Para Mala Viagem Tamanho M", False),
        ("Etiqueta De Mala", False),
        # Os kits reais de mala.
        ("Kit 3 Malas 24/20/14 Despacho Bordo + Frasqueira Pp Azul-claro P5", True),
        ("Conjunto De Mala De Bordo 20 Em Polipropileno Rose Gold Com Mochila", True),
        ("Jogo 4 Malas 24 23kg 20 10kg + 2 Frasqueiras + Mochila Abs Azul-claro M1", True),
        ("Kit Jogo De Malas Viagem 2 Peças Bordo 18 Polegadas 10kg", True),
        ("Frasqueira Necessaire 8 E 10 Polegadas Rígida Maquiagem Abs Azul Claro M1", True),
        # Os acessórios reais de KFA e Marquezini que citam a mala.
        ("Necessaire Maquiagem Bolsa Organizador Mala Viagem 14 Polegadas", False),
        ("Nécessaire De Maquiagem Organizador Para Mala Charlot's Park", False),
        ("Par De Rodinhas 360 Graus Reposição Mala De Viagem Silicone Preto M1-p4", False),
        ("Chaveiro De Pelúcia Preto Para Mala Mochila E Bolsa", False),
        ("Balança Digital Portátil Para Mala De Viagem 50kg Gancho Preto", False),
        ("Kit Capas Para Mala De Viagem", False),
        ("Maleta De Ferramentas", False),
        ("", False),
        (None, False),
    ],
)
def test_r3_a_familia_pelo_titulo(titulo, esperado):
    assert acao_pessoa.anuncio_de_mala(titulo) is esperado


def _vinculo(estoque=100, produto=100, status="ok", anuncio="active"):
    return SimpleNamespace(
        estoque=estoque, estoque_produto=produto, status=status, anuncio_status=anuncio
    )


@pytest.mark.parametrize(
    ("vinculos", "quantidade", "esperado"),
    [
        ([_vinculo(427, 427)], 20, True),
        ([_vinculo(137, 137)], 15, True),
        ([_vinculo(266, 266)], 20, True),
        ([_vinculo(3, 3)], 60, False),  # d75b1521
        ([_vinculo(20, 20)], 20, True),
        ([_vinculo(19, 500)], 20, False),
        ([_vinculo(500, 19)], 20, False),  # o vínculo velho: vale o menor
        ([_vinculo(3, 3), _vinculo(40, 40)], 20, True),  # a variação que cobre
        ([], 20, None),
        ([_vinculo(None, 100)], 20, None),
        ([_vinculo(100, None)], 20, None),
        ([_vinculo(status="retryable")], 20, None),
        ([_vinculo(anuncio="paused")], 20, None),
        ([_vinculo(anuncio=None)], 20, None),
        ([_vinculo(40, 40), _vinculo(None, None)], 20, None),
    ],
)
def test_r3_o_estoque(vinculos, quantidade, esperado):
    assert acao_pessoa.estoque_cobre(vinculos, quantidade) is esperado


def test_r3_a_frase_e_a_da_equipe_e_so_o_percentual_a_barra():
    assert FRASE == "20% de desconto, informe o modelo específico que envie um anúncio de atacado"
    FRASE.encode("iso-8859-1")
    for canal in ("pergunta", "pos_venda"):
        final = validador.normalizar(FRASE, plataforma="ml", canal=canal)
        assert final == FRASE
        assert validador.validar(
            final, plataforma="ml", canal=canal, origem=constantes.ORIGEM_IA
        ) == [validador.MOTIVO_PERCENTUAL]


# ── A decisão, com banco ──


async def _anuncio_com_estoque(
    db: AsyncSession, conversa: Any, *, estoque: int | None, produto: int | None = None,
    status: LinkSyncStatus = LinkSyncStatus.OK, anuncio: str | None = "active",
) -> None:  # fmt: skip
    integ = await db.get(Integration, conversa.integration_id)
    item = Product(
        user_id=integ.user_id, sku=f"MALA-{conversa.anuncio_id}", name="Mala de viagem",
        stock=estoque if produto is None else produto,
    )  # fmt: skip
    db.add(item)
    await db.flush()
    db.add(
        ProductLink(
            user_id=integ.user_id, product_id=item.id, integration_id=integ.id,
            platform=IntegrationPlatform.ML, external_id=conversa.anuncio_id,
            variation_id=None, external_sku=item.sku, listing_title=conversa.anuncio_titulo,
            stock=estoque, last_sync_status=status, anuncio_status=anuncio,
        )
    )  # fmt: skip
    await db.commit()


@pytest.fixture
def recusa_ativa(monkeypatch):
    """O fato `recusa_desconto` ativo, como em produção (id 15, global)."""

    async def _chaves(*_a: Any, **_k: Any) -> frozenset[str]:
        return frozenset({acao_pessoa.CHAVE_RECUSA_DESCONTO})

    monkeypatch.setattr(fatos_loja, "chaves_ativas", _chaves)


async def _atacado_e2e(
    db: AsyncSession, make_user, fala: str, *, estoque: int | None = 100, sem_vinculo: bool = False,
    canal: str = "pergunta", titulo: str = "Mala Charlot's Park M1-m6 Violeta-escuro Lisa",
    pedido: str | None = None, **kw: Any,
) -> AtendimentoConversa:  # fmt: skip
    await _importar_manual_base(db)
    conversa = await _conversa_ml(db, make_user, canal=canal, titulo=titulo, pedido=pedido)
    await _ligar(
        db, conversa, modo=constantes.MODO_AUTO, auto_categorias=["desconto"],
        auto_orientacao=["desconto"], auto_agente=[],
    )  # fmt: skip
    if not sem_vinculo:
        await _anuncio_com_estoque(db, conversa, estoque=estoque, **kw)
    await _msg_ia(db, conversa, fala, ha=timedelta(minutes=30))
    await db.refresh(conversa)
    return conversa


@pytest.mark.parametrize(
    ("caso", "fala", "titulo", "estoque"),
    [
        ("070f1828", "consegue um valor melhor pra comprar 20UN?", "Frasqueira Rígida De Mão De Viagem 12 Polegadas Roxo Escuro M2", 427),
        ("378b1cd9", "consegue um valor melhor pra 15UN?", "Mala Charlot's Park M1-m6 Violeta-escuro Lisa", 137),
        ("8f27c582", "Consegue um valor melhor pra 20UN?", "Frasqueira Maleta Maquiagem Profissional Com Alça Necessaire Viagem 8 Polegadas Organizado", 266),
    ],
)
async def test_r3_e2e_os_tres_reais_saem_sem_o_modelo(
    db: AsyncSession, make_user, agente_ligado, api, envios, recusa_ativa, caso, fala, titulo, estoque
):
    conversa = await _atacado_e2e(db, make_user, fala, estoque=estoque, titulo=titulo)
    api.roteiro = []  # o modelo não é chamado

    r = await ia.gerar_rascunho(db, conversa)

    assert r is not None and r.texto == FRASE and not r.precisa_humano, r.motivo
    assert api.clientes == [] and api.verificador == []  # sem custo
    agente = _do_agente(r)
    assert agente["acoes_pessoa"] == [] and agente[ia.FATO_ATACADO] is True
    assert agente["isencoes"] == [checagens.ISENCAO_ATACADO_ML]
    assert r.categoria == "desconto"
    assert ia.atacado_no_rascunho(r)
    assert [e["texto"] for e in envios] == [FRASE] and _decisao(r) == "enviado", r.fatos


async def test_r3_e2e_d75b1521_acima_do_estoque_vai_para_pessoa(
    db: AsyncSession, make_user, agente_ligado, api, envios, recusa_ativa
):
    conversa = await _atacado_e2e(
        db, make_user, "Quero um orçamento para 60 delas", estoque=3,
        titulo="Mala Viagem Policarbonato Aluminio Usb Tsa 10kg 20 Polegadas Bordo - Branco",
    )  # fmt: skip
    api.roteiro = []

    r = await ia.gerar_rascunho(db, conversa)

    assert r is not None and not (r.texto or "").strip() and r.precisa_humano, r.motivo
    assert _do_agente(r)["acoes_pessoa"] == [constantes.ACAO_DESCONTO_BRINDE]
    assert "60 unidades, acima do estoque" in (r.motivo or "")
    assert api.clientes == [] and envios == []


@pytest.mark.parametrize(
    "kw",
    [
        {"sem_vinculo": True},
        {"estoque": None},
        {"estoque": 100, "status": LinkSyncStatus.RETRYABLE},
        {"estoque": 100, "anuncio": "paused"},
    ],
    ids=["sem-vinculo", "sem-estoque", "sem-sincronia", "anuncio-parado"],
)
async def test_r3_e2e_estoque_desconhecido_vai_para_pessoa(
    db: AsyncSession, make_user, agente_ligado, api, envios, recusa_ativa, kw
):
    conversa = await _atacado_e2e(db, make_user, "consegue um valor melhor pra 20UN?", **kw)
    api.roteiro = []

    r = await ia.gerar_rascunho(db, conversa)

    assert r is not None and not (r.texto or "").strip() and r.precisa_humano, r.motivo
    assert _do_agente(r)["acoes_pessoa"] == [constantes.ACAO_DESCONTO_BRINDE]
    assert "não se determina" in (r.motivo or "")
    assert api.clientes == [] and envios == []


async def test_r3_e2e_o_pos_venda_sem_anuncio_vai_para_pessoa(
    db: AsyncSession, make_user, agente_ligado, api, envios, recusa_ativa
):
    conversa = await _atacado_e2e(
        db, make_user, "consegue um valor melhor pra 20UN?", canal="pos_venda", pedido="2000001"
    )
    conversa.anuncio_id = None
    conversa.anuncio_titulo = None
    await db.commit()
    api.roteiro = []

    r = await ia.gerar_rascunho(db, conversa)

    assert r is not None and not (r.texto or "").strip() and r.precisa_humano, r.motivo
    assert _do_agente(r)["acoes_pessoa"] == [constantes.ACAO_DESCONTO_BRINDE]
    assert api.clientes == [] and envios == []


async def test_r3_e2e_o_seguimento_do_mesmo_comprador_vai_para_pessoa(
    db: AsyncSession, make_user, agente_ligado, api, envios, recusa_ativa
):
    # A pergunta de ontem, já respondida com a frase; hoje o comprador manda o modelo.
    conversa = await _atacado_e2e(db, make_user, "É o modelo M2 roxo")
    outra, _ = await gravar.upsert_conversa(
        db,
        canal=await db.get(AtendimentoCanal, conversa.canal_id),
        integration=await db.get(Integration, conversa.integration_id),
        plataforma="ml", canal_nome="pergunta", externo_id="ml-ontem",
        comprador_id=conversa.comprador_id, comprador_nome=None, pedido_marketplace=None,
        anuncio_titulo=conversa.anuncio_titulo,
    )  # fmt: skip
    await db.commit()
    await _msg_ia(db, outra, FRASE, autor="loja", origem="externo", ha=timedelta(days=1))
    api.roteiro = []

    r = await ia.gerar_rascunho(db, conversa)

    assert r is not None and not (r.texto or "").strip() and r.precisa_humano, r.motivo
    assert acao_pessoa.MOTIVO_ATACADO_SEGUIMENTO in (r.motivo or "")
    assert api.clientes == [] and envios == []


async def test_r3_a_janela_do_seguimento(db: AsyncSession, make_user, recusa_ativa):
    conversa = await _atacado_e2e(db, make_user, "É o modelo M2 roxo")
    await _msg_ia(db, conversa, FRASE, autor="loja", origem="externo", ha=timedelta(days=8))
    agora = datetime.now(UTC)
    assert not await acao_pessoa._atacado_ja_respondido(db, conversa, agora)
    assert await acao_pessoa._atacado_ja_respondido(db, conversa, agora - timedelta(days=2))
    # No replay, o que saiu depois do `as_of` ainda não aconteceu.
    assert not await acao_pessoa._atacado_ja_respondido(db, conversa, agora - timedelta(days=9))


async def _decide(db: AsyncSession, conversa: Any, fala: str) -> Any:
    msgs = _cli(fala)
    base = SimpleNamespace(recentes=msgs, ctx={}, sinais=[], sinais_do_cliente=[])
    return await acao_pessoa.decisao_do_atacado(db, conversa, base, msgs[-1])


@pytest.mark.parametrize(
    "fala",
    [
        "tem desconto?",  # varejo sem quantidade
        "consegue um valor melhor pra 10UN?",  # abaixo do que os dados sustentam
        "faz 20 unidades por 1000 reais?",
    ],
)
async def test_r3_o_que_nao_e_atacado_segue_o_caminho_de_sempre(
    db: AsyncSession, make_user, recusa_ativa, fala
):
    conversa = await _atacado_e2e(db, make_user, fala)
    assert await _decide(db, conversa, fala) is None


async def test_r3_celular_e_outra_loja_seguem_o_caminho_de_sempre(
    db: AsyncSession, make_user, recusa_ativa
):
    fala = "consegue um valor melhor pra 20UN?"
    conversa = await _atacado_e2e(db, make_user, fala, titulo="Celular Doogee Note 128gb")
    assert await _decide(db, conversa, fala) is None
    # A loja de mala fora do recorte (Forpaper), com o anúncio e o estoque que cobriam.
    conversa.anuncio_titulo = "Mala de Viagem"
    assert await _decide(db, conversa, fala) == (acao_pessoa.ATACADO_RESPONDE, "")
    conversa.conta = "Forpaper"
    assert await _decide(db, conversa, fala) is None
    conversa.conta = "KFA 2"
    assert await _decide(db, conversa, fala) is None
    # Os nomes como estão em produção: "Marquezini", "KFA".
    conversa.conta = "Marquezini"
    assert await _decide(db, conversa, fala) == (acao_pessoa.ATACADO_RESPONDE, "")
    conversa.conta = "KFA"
    assert await _decide(db, conversa, fala) == (acao_pessoa.ATACADO_RESPONDE, "")


@pytest.mark.parametrize("plataforma", ["shopee", "tiktok", "amazon", "magalu"])
async def test_r3_as_outras_plataformas_nao_mudam(plataforma):
    fala = "consegue um valor melhor pra 20UN?"
    conv = SimpleNamespace(plataforma=plataforma, canal="pergunta", conta="kfa")
    # Sem sessão: nem chega a ler o banco.
    assert await _decide(None, conv, fala) is None


async def test_r3_sem_a_recusa_padrao_a_regua_decide_antes(db: AsyncSession, make_user):
    # Sem o fato `recusa_desconto`, o gatilho 13 já é da pessoa: a resposta fixa não passa por
    # cima da régua, e a triagem concordaria.
    fala = "consegue um valor melhor pra 20UN?"
    conversa = await _atacado_e2e(db, make_user, fala)
    assert await _decide(db, conversa, fala) is None


async def test_r3_e2e_o_celular_vai_ao_modelo(
    db: AsyncSession, make_user, agente_ligado, api, envios, recusa_ativa
):
    conversa = await _atacado_e2e(
        db, make_user, "consegue um valor melhor pra 20UN?", titulo="Celular Doogee Note 128gb"
    )
    api.roteiro = [
        _final(
            acao="pessoa", texto="", assunto="desconto", confianca=0.8,
            acao_pessoa=constantes.ACAO_DESCONTO_BRINDE, motivo_pessoa="preço por quantidade",
        )
    ]  # fmt: skip

    r = await ia.gerar_rascunho(db, conversa)

    assert api.pedidos and r is not None and not ia.atacado_no_rascunho(r)
    assert FRASE != r.texto and envios == []


# ── O envio da frase ──


def _fatos_do_atacado(**kw: Any) -> dict:
    return {
        "agente": {
            "isencoes": [checagens.ISENCAO_ATACADO_ML],
            "acoes_pessoa": [],
            ia.FATO_ATACADO: True,
            **kw,
        }
    }


async def test_r3_o_envio_so_aceita_a_frase_igual(db: AsyncSession, make_user):
    conversa = await _conversa_ml(db, make_user)
    r = await _rascunho(db, conversa, FRASE, _fatos_do_atacado())
    assert await _envio(db, conversa, FRASE, r) == FRASE
    for mexida in (
        FRASE.replace("20%", "30%"),
        FRASE.replace("específico", "especifico"),
        f"{FRASE}.",
        f"Olá! {FRASE}",
        f"{FRASE} Custa R$ 100,00.",
    ):
        with pytest.raises(enviar.EnvioRecusado):
            await _envio(db, conversa, mexida, r)
    # A frase certa sem a marca, de outra origem ou fora do ML: a mesma recusa.
    sem_marca = await _rascunho(
        db, conversa, FRASE, {"agente": {"isencoes": []}}, status="substituido"
    )
    with pytest.raises(enviar.EnvioRecusado):
        await _envio(db, conversa, FRASE, sem_marca)
    legado = await _rascunho(db, conversa, FRASE, {}, status="substituido")
    with pytest.raises(enviar.EnvioRecusado):
        await _envio(db, conversa, FRASE, legado)
    shopee = await _conversa_ia(db, make_user, pedido=None, externo_id="sh-2")
    na_shopee = await _rascunho(db, shopee, FRASE, _fatos_do_atacado())
    with pytest.raises(enviar.EnvioRecusado):
        await _envio(db, shopee, FRASE, na_shopee)


def test_r3_a_marca_do_rascunho_exige_a_frase_intacta():
    base = {
        "prompt_versao": constantes.PROMPT_VERSAO_AGENTE,
        "texto": FRASE,
        "fatos": _fatos_do_atacado(),
    }
    assert ia.atacado_no_rascunho(SimpleNamespace(**base))
    assert not ia.atacado_no_rascunho(SimpleNamespace(**{**base, "texto": f"{FRASE}!"}))
    assert not ia.atacado_no_rascunho(SimpleNamespace(**{**base, "prompt_versao": "v10"}))
    assert not ia.atacado_no_rascunho(
        SimpleNamespace(**{**base, "fatos": _fatos_do_atacado(acoes_pessoa=["desconto_brinde"])})
    )
    assert not ia.atacado_no_rascunho(SimpleNamespace(**{**base, "fatos": {"agente": {}}}))
    assert not ia.atacado_no_rascunho(None)


def test_r3_o_rascunho_da_pessoa_nao_tem_texto_nem_a_marca():
    conv = _conv(plataforma="ml", canal="pergunta", pedido_marketplace=None)
    gat = _cli("Quero um orçamento para 60 delas")[0]
    r = ia.atacado_do_agente(
        conv, gat, (acao_pessoa.ATACADO_PESSOA, "motivo"), modo=constantes.MODO_AUTO
    )
    assert r.texto in (None, "") and r.fatos_agente["acoes_pessoa"] == [
        constantes.ACAO_DESCONTO_BRINDE
    ]
    assert r.motivo == "motivo" and ia.FATO_ATACADO not in r.fatos_agente


async def test_r3_o_reenvio_acha_a_frase_presa_na_pausa(db: AsyncSession, make_user):
    conversa = await _conversa_ml(db, make_user)
    r = await _rascunho(db, conversa, FRASE, _fatos_do_atacado())
    outro = await _rascunho(db, conversa, "Olá!", {"agente": {"isencoes": []}}, status="substituido")
    k, x = AtendimentoCanal, AtendimentoRascunho
    linhas = await db.scalars(
        select(x.id)
        .join(AtendimentoConversa, AtendimentoConversa.id == x.conversa_id)
        .join(k, k.id == AtendimentoConversa.canal_id)
        .where(ia._sql_assunto_liberado(x, k), x.conversa_id == conversa.id)
    )
    achados = set(linhas)
    assert r.id in achados and outro.id not in achados


# ── A paridade do replay e do re-gatilho ──


async def test_r3_a_paridade_no_replay_e_no_regatilho(db: AsyncSession, make_user, recusa_ativa):
    fala = "consegue um valor melhor pra 20UN?"
    conversa = await _atacado_e2e(db, make_user, fala)
    msgs = _cli(fala)
    base = agente_ctx.BaseDoGerar(
        recentes=msgs, rajada=msgs, foco=msgs, textos_cliente=[fala], textos_rajada=[fala], ctx={},
        valores={}, fatos={"anuncio": None}, fatos_gravados={}, categorias=_categorias_do_manual(),
        regras=[], sinais=[], motivos=[], opcoes_sensiveis=[], sinais_do_cliente=[], nomes=(),
        modo=constantes.MODO_AUTO, forcar=False, nf_politica_aprovada=False, insistiu=False,
    )  # fmt: skip
    extra = {"as_of": datetime.now(UTC), "modo_replay": True, "sem_ida_a_loja": True}

    r = await replay.como_o_gerar(db, conversa, msgs[-1], base, extra)

    assert r.texto == FRASE and r.fatos_agente.get(ia.FATO_ATACADO) is True
    canal = {"modo": "auto", "auto_categorias": [], "auto_agente": [], "auto_orientacao": []}
    x = await regatilho.regatilhar(
        {"id": "a1", "parada": r.parada, "proposta": {}, "texto_final": r.texto,
         "decisao": {"atacado": True}, "plataforma": "ml"},
        canal,
    )  # fmt: skip
    assert x["decisao"] == regatilho.RECIBO and x["rascunho"] == FRASE
    # Com outro texto, não conta como sozinho.
    y = await regatilho.regatilhar(
        {"id": "a2", "parada": r.parada, "proposta": {}, "texto_final": "outro",
         "decisao": {"atacado": True}, "plataforma": "ml"},
        canal,
    )  # fmt: skip
    assert y["decisao"] != regatilho.RECIBO


def test_o_agente_nao_ganhou_frase_nova_de_atacado_no_prompt():
    # A resposta é do código: o modelo não a lê.
    assert FRASE not in prompt.bloco_geral()
    assert FRASE not in prompt.bloco_da_plataforma("ml", _categorias_do_manual())


# ═══════════════ A revisão do diff (05/11/2026) ═══════════════

# ── Regra 1: o que a isenção nova não pode perder nem deixar passar ──


@pytest.mark.parametrize(
    "texto",
    [
        # A negativa do próprio fato não usa o verbo de garantir e passava antes do `_PRAZO_NO_ML`.
        "Não temos entrega para hoje. O prazo e o frete para o seu CEP aparecem no anúncio ao informar o CEP.",
        "A transportadora não faz entrega para domingo.",
        "Não fazemos entrega para domingo.",
        "Não temos envio para hoje.",
        "Não há coleta para hoje.",
        "Não há previsão de envio para hoje.",
        "A entrega para hoje não pode ser garantida.",
    ],
)
async def test_r1_a_negativa_do_fato_passa_no_validador_e_no_envio(db: AsyncSession, make_user, texto):
    assert _ml(texto) == []
    assert checagens.conferir_prazo_sem_lastro(texto, plataforma="ml") == []
    conversa = await _conversa_ml(db, make_user)
    r = await _rascunho(db, conversa, texto, {"agente": {"isencoes": []}})
    assert await _envio(db, conversa, texto, r) == texto


@pytest.mark.parametrize(
    "texto",
    [
        "Temos entrega para hoje.",
        "Não se preocupe que a entrega para hoje está garantida.",
        "Fazemos entrega para domingo.",
        "A entrega para hoje está garantida.",
    ],
)
def test_r1_a_promessa_parecida_com_a_negativa_do_fato_fica_recusada(texto):
    assert validador.MOTIVO_PRAZO in _ml(texto), texto


def test_r1_a_negativa_do_fato_junto_da_negativa_do_verbo():
    # O trecho negado do fato não derruba a isenção do resto da resposta.
    texto = "Não temos entrega para hoje. Não temos como garantir a chegada amanhã."
    assert checagens.prazo_do_validador_sem_afirmacao(texto, plataforma="ml")


@pytest.mark.parametrize(
    "texto",
    [
        "Não temos como confirmar se a entrega é amanhã. O prazo e o frete para o seu CEP aparecem no anúncio ao informar o CEP.",
        "Não temos como garantir que a entrega é amanhã.",
        "Não temos como garantir que o pedido é entregue amanhã.",
    ],
)
async def test_r1_o_e_de_ligacao_no_vao_nao_afirma_o_prazo(db: AsyncSession, make_user, texto):
    assert checagens.prazo_do_validador_sem_afirmacao(texto, plataforma="ml"), texto
    assert checagens.conferir_prazo_sem_lastro(texto, plataforma="ml") == []
    conversa = await _conversa_ml(db, make_user)
    r = await _rascunho(db, conversa, texto, {"agente": {"isencoes": []}})
    assert await _envio(db, conversa, texto, r) == texto


@pytest.mark.parametrize(
    "texto",
    [
        "Não temos como confirmar se o pedido é entregue essa semana.",
        "Não sabemos se o envio é feito antes do Natal.",
    ],
)
def test_r1_o_e_de_ligacao_na_regra_suave(texto):
    assert checagens.conferir_prazo_sem_lastro(texto, plataforma="ml") == []


@pytest.mark.parametrize(
    "texto",
    [
        "Não tem como não garantir a chegada amanhã.",
        "Não temos como não garantir a chegada amanhã.",
        "Não preciso nem dizer que chega amanhã.",
        "Confira se chega amanhã, chega sim.",
        "Pode ver se chega amanhã que chega.",
        "Quer saber se chega amanhã? Chega.",
        "Não temos como garantir a chegada amanhã. Sexta sim.",
        "Não confirmamos a chegada amanhã porque já está confirmada.",
        "Não temos como informar a transportadora da entrega para amanhã.",
    ],
)
async def test_r1_a_afirmacao_escondida_na_negativa_nao_tem_isencao(
    db: AsyncSession, make_user, texto
):
    assert validador.MOTIVO_PRAZO in _ml(texto)
    assert not checagens.prazo_do_validador_sem_afirmacao(texto, plataforma="ml"), texto
    conversa = await _conversa_ml(db, make_user)
    r = await _rascunho(db, conversa, texto, {"agente": {"isencoes": []}})
    with pytest.raises(enviar.EnvioRecusado):
        await _envio(db, conversa, texto, r)


def test_r1_a_negacao_sem_dupla_negacao_fica_isenta():
    for texto in (
        "Não vendemos em loja e não temos como garantir a chegada amanhã.",
        "Não sei, não temos como garantir a chegada amanhã.",
        "A chegada não está confirmada: não temos como garantir a chegada amanhã.",
    ):
        assert checagens.prazo_do_validador_sem_afirmacao(texto, plataforma="ml"), texto


@pytest.mark.parametrize(
    "texto",
    [
        "Não temos como garantir a chegada amanhã nem depois de amanhã.",
        "Não temos como garantir a chegada amanhã ou depois de amanhã.",
    ],
)
def test_r1_o_depois_de_amanha_e_alternativa(texto):
    assert checagens.prazo_do_validador_sem_afirmacao(texto, plataforma="ml"), texto
    assert checagens.conferir_prazo_sem_lastro(texto, plataforma="ml") == []


# ── Regra 2: o que ainda dizia que dá para alterar, sem pista ou em outro assunto ──

PEDE_ENDERECO = "Quero alterar o endereço de entrega"
ERRA_ENDERECO = "Se eu errar o endereço na compra vocês arrumam?"
MUDA_PUBLICA = "Tem como mudar o endereço depois que você finaliza a compra?"


@pytest.mark.parametrize(
    ("canal", "pedido", "fala", "texto", "assunto"),
    [
        # O texto que diz que dá, no assunto do endereço e sem a pista antiga no turno.
        ("pergunta", None, ERRA_ENDERECO, "Sim, é possível alterar o endereço.", "endereco"),
        ("pergunta", None, ERRA_ENDERECO, "Sim, o endereço pode ser alterado pela loja.", "endereco"),
        ("pergunta", None, ERRA_ENDERECO, "Dá para mudar o endereço depois da compra.", "endereco"),
        ("pergunta", None, ERRA_ENDERECO, "Arrumamos sim.", "endereco"),
        # A negação que vira condição.
        ("pos_venda", "2000001", PEDE_ENDERECO, "Não alteramos o endereço por enquanto, depois de postado é possível.", "endereco"),
        ("pos_venda", "2000001", PEDE_ENDERECO, "Não alteramos o endereço agora, só depois da postagem.", "endereco"),
        ("pos_venda", "2000001", PEDE_ENDERECO, "Não alteramos o endereço por aqui. Depois que postar, a transportadora muda.", "endereco"),
        ("pos_venda", "2000001", PEDE_ENDERECO, "Não alteramos o endereço sem antes postar. Depois é possível.", "endereco"),
        ("pos_venda", "2000001", PEDE_ENDERECO, "Não alteramos o endereço, mas a transportadora troca se você ligar para ela.", "endereco"),
        ("pos_venda", "2000001", PEDE_ENDERECO, "Não alteramos o endereço. Se ainda precisar, nos envie o novo.", "endereco"),
        # O turno pede a mudança e o modelo declara outro assunto, sem o verbo no texto.
        ("pos_venda", "2000001", "CEP errado no pedido", "Pode nos informar o CEP correto?", "outro"),
        ("pos_venda", "2000001", "CEP errado no pedido", "Qual o CEP correto?", "status_pedido"),
        ("pos_venda", "2000001", "CEP errado no pedido", "Me passe o CEP certo, por favor.", "outro"),
        ("pos_venda", "2000001", "CEP errado no pedido", "Vamos atualizar o CEP para você.", "outro"),
        ("pos_venda", "2000001", "CEP errado no pedido", "Fale com a loja pelas mensagens da compra.", "outro"),
        ("pos_venda", "2000001", "Consegue enviar para outra cidade?", "Consigo sim, é só me passar a cidade.", "outro"),
        ("pergunta", None, MUDA_PUBLICA, "Sim, é possível.", "outro"),
        ("pos_venda", "2000001", "Preciso que entregue na casa da minha mãe", "Tudo bem, vamos enviar para a casa da sua mãe.", "outro"),
        ("pos_venda", "2000001", "Preciso que entregue na casa da minha mãe", "Tudo bem, vamos enviar para a casa da sua mãe.", "endereco"),
        ("pos_venda", "2000001", "Quero mudar o endereço, errei o número", "Já ajustei.", "status_pedido"),
        # A negativa que repete o endereço abreviado.
        ("pos_venda", "2000001", PEDE_ENDERECO, "Não alteramos o endereço: a entrega segue para Av. Principal, 1000.", "endereco"),
        ("pos_venda", "2000001", PEDE_ENDERECO, "Não alteramos o endereço. Seu pedido vai para R. das Flores 123.", "endereco"),
    ],
)
async def test_r2_e2e_o_que_diz_que_da_vira_a_resposta_aprovada(
    db: AsyncSession, make_user, agente_ligado, api, envios, canal, pedido, fala, texto, assunto
):
    conversa = await _endereco_e2e(db, make_user, canal=canal, pedido=pedido, fala=fala)
    api.roteiro = [_final(texto=texto, assunto=assunto, confianca=0.9)]

    r = await ia.gerar_rascunho(db, conversa)

    assert r.texto == FIXA_ENDERECO and r.categoria == "endereco" and not r.precisa_humano, r.motivo
    assert [e["texto"] for e in envios] == [FIXA_ENDERECO] and _decisao(r) == "enviado", r.fatos


@pytest.mark.parametrize(
    "texto",
    [
        "Sim, é possível.",
        "Consigo sim.",
        "Depois do envio não é possível, antes dá.",
        "Fale com a loja pelas mensagens da compra.",
        "Qual o CEP correto?",
        "Me passe a cidade, por favor.",
    ],
)
def test_r2_a_trava_le_o_pedido_em_outro_assunto(texto):
    (p,) = checagens.conferir_endereco_no_ml(
        texto, acao="responder", assunto="outro", plataforma="ml", pede=True
    )
    assert p.codigo == checagens.ENDERECO_NO_ML
    # Sem o pedido no turno, outro assunto segue o caminho dele.
    if "CEP" not in texto and "endereço" not in texto:
        assert checagens.conferir_endereco_no_ml(
            texto, acao="responder", assunto="outro", plataforma="ml"
        ) == []


def test_r2_a_outra_parte_do_turno_mista_nao_vira_a_frase():
    # O modelo respondeu à outra parte, sem nada errado no endereço: fica para o verificador.
    for texto in ("O prazo aparece no anúncio.", "Seu pedido foi postado hoje, mas ainda sem rastreio."):
        prazo = _proposta(texto, assunto="prazo_antes_compra")
        assert _no_ml(prazo) == prazo


def _pergunta_coberta(fala: str, texto: str, assunto: str, canal: str = "pergunta") -> Proposta:
    msgs = _cli(fala)
    p = _proposta(texto, assunto=assunto)
    return checagens.endereco_no_ml_da_proposta(p, _ctx(canal=canal), msgs, msgs[-1])


def test_r2_a_cobertura_na_pergunta_publica_nao_e_pedido_de_mudanca():
    fala = "Envia para outra cidade?"
    # Antes da compra é a cobertura; o "sim, enviamos" é a resposta.
    texto = "Sim, enviamos para todo o Brasil."
    novo = _pergunta_coberta(fala, texto, "cobertura_regiao")
    assert novo.texto == texto and novo.assunto == "cobertura_regiao"
    # Depois da compra, ou lida como outra coisa, é o pedido.
    assert _pergunta_coberta(fala, texto, "cobertura_regiao", "pos_venda").texto == FIXA_ENDERECO
    assert _pergunta_coberta(fala, texto, "outro").texto == FIXA_ENDERECO


@pytest.mark.parametrize(
    ("fala", "texto"),
    [
        ("Entrega no meu endereço em Manaus?", "Entregamos em todo o Brasil; confira informando o CEP no anúncio."),
        ("vocês entregam no meu endereço?", "O prazo e o frete para o seu CEP aparecem no anúncio ao informar o CEP."),
        ("vocês entregam no meu endereço?", "Enviamos para o seu CEP: confira o frete no anúncio."),
        ("vocês entregam no meu endereço?", "Informe o CEP no anúncio para ver o frete."),
        ("confirma o endereço de entrega?", "A entrega segue o endereço do pedido, em Minhas compras."),
        ("o frete é pelo cep?", "O frete só é possível calcular com o CEP, no anúncio."),
        ("vocês entregam no meu endereço?", "Entregamos. Se já comprou, fale com a loja pelas mensagens da compra."),
    ],
)
def test_r2_quem_nao_pede_a_mudanca_nao_recebe_a_frase(fala, texto):
    msgs = _cli(fala)
    assert not acao_pessoa.pede_alterar_endereco(msgs, msgs[-1])
    p = _proposta(texto)
    assert checagens.endereco_no_ml_da_proposta(p, _ctx(canal="pergunta"), msgs, msgs[-1]) == p
    assert checagens.conferir_endereco_no_ml(
        texto, acao="responder", assunto="endereco", plataforma="ml"
    ) == []


async def test_r2_e2e_a_pergunta_de_prazo_com_o_assunto_endereco_sai_como_veio(
    db: AsyncSession, make_user, agente_ligado, api, envios
):
    texto = "O prazo e o frete para o seu CEP aparecem no anúncio ao informar o CEP."
    conversa = await _endereco_e2e(
        db, make_user, canal="pergunta", pedido=None, fala="vocês entregam no meu endereço?"
    )
    api.roteiro = [_final(texto=texto, assunto="endereco", confianca=0.9)]

    r = await ia.gerar_rascunho(db, conversa)

    assert r.texto == texto and r.texto != FIXA_ENDERECO
    assert [e["texto"] for e in envios] == [texto] and _decisao(r) == "enviado", r.fatos


def test_r2_sem_o_pedido_so_o_da_para_alterar_vira_a_frase():
    msgs = _cli("vocês entregam no meu endereço?")
    ctx = _ctx(canal="pergunta")
    dizer = _proposta("Sim, é possível alterar o endereço depois.")
    assert checagens.endereco_no_ml_da_proposta(dizer, ctx, msgs, msgs[-1]).texto == FIXA_ENDERECO
    # O pedido do dado e o endereço repetido não viram a frase, que não responderia: pessoa.
    for texto in ("Qual o seu CEP?", "Entregamos na Rua Major Branco."):
        p = _proposta(texto)
        assert checagens.endereco_no_ml_da_proposta(p, ctx, msgs, msgs[-1]) == p
        (trava,) = checagens.conferir_endereco_no_ml(
            texto, acao="responder", assunto="endereco", plataforma="ml"
        )
        assert trava.codigo == checagens.ENDERECO_NO_ML


# ── Regra 2: a entrega errada x o pedido de mudança no gatilho 11 ──

PEDE_MUDAR = [
    "coloquei o endereço errado",
    "eu tenho como pedir pra entregar em outro endereço",
    "pode entregar para outra pessoa?",
    "dá pra entregar em outro endereço depois de comprar?",
    "pode ser entregue em outro endereço?",
    "digitei a casa errada",
]
ENTREGA_FEITA = [
    "entregaram no endereço errado",
    "entregaram na casa errada",
    "foi entregue em outro endereço",
    "deixaram com outra pessoa",
    "o carteiro deixou em outra casa",
    "mandaram para o endereço errado",
]


@pytest.mark.parametrize("fala", PEDE_MUDAR)
def test_r2_no_ml_pedir_a_mudanca_nao_abre_chamado(fala):
    msgs = _cli(fala)
    ml = _conv(plataforma="ml", canal="pos_venda")
    assert _gatilhos(ml, msgs, postado=acao_pessoa.POSTADO_NAO) == []
    assert _gatilhos(ml, msgs, postado=None) == []
    assert acao_pessoa.pede_alterar_endereco(msgs, msgs[-1])


@pytest.mark.parametrize("fala", ENTREGA_FEITA)
def test_r2_no_ml_a_entrega_errada_continua_com_chamado(fala):
    msgs = _cli(fala)
    ml = _conv(plataforma="ml", canal="pos_venda")
    assert constantes.ACAO_ABRIR_CHAMADO in _gatilhos(ml, msgs, postado=acao_pessoa.POSTADO_SIM)
    assert not acao_pessoa.pede_alterar_endereco(msgs, msgs[-1])


@pytest.mark.parametrize("fala", ["coloquei o endereço errado", "pode entregar para outra pessoa?"])
def test_r2_fora_do_ml_o_gatilho_11_nao_muda(fala):
    msgs = _cli(fala)
    assert constantes.ACAO_ABRIR_CHAMADO in _gatilhos(_conv(), msgs, postado=acao_pessoa.POSTADO_SIM)


@pytest.mark.parametrize(
    "fala",
    [
        # Reais do ML (180 dias): a reclamação de não ter recebido e o endereço da NF.
        "falaram que ia entregar no meu endereço e não entregaram",
        "Eu comprei um celular para entregar no meu endereço e não entregaram deixaram lá no correio",
        "Endereço da nota ta errado",
    ],
)
def test_r2_a_reclamacao_e_a_nota_nao_sao_pedido_de_mudanca(fala):
    msgs = _cli(fala)
    assert not acao_pessoa.pede_alterar_endereco(msgs, msgs[-1])
    p = _proposta("Vamos ver o que aconteceu com a entrega.", assunto="entrega_nao_recebida")
    assert checagens.endereco_no_ml_da_proposta(p, _ctx(), msgs, msgs[-1]) == p


@pytest.mark.parametrize(
    ("canal", "pedido", "fala"),
    [
        ("pos_venda", "2000001", "coloquei o endereço errado"),
        ("pos_venda", "2000001", "eu tenho como pedir pra entregar em outro endereço"),
        ("pos_venda", "2000001", "pode entregar para outra pessoa?"),
        ("pergunta", None, "dá pra entregar em outro endereço depois de comprar?"),
    ],
)
async def test_r2_e2e_o_pedido_escrito_como_entrega_errada_sai_sozinho(
    db: AsyncSession, make_user, agente_ligado, api, envios, canal, pedido, fala
):
    conversa = await _endereco_e2e(db, make_user, canal=canal, pedido=pedido, fala=fala)
    # O turno também toca a pista "entrega_nao_recebida": as duas liberadas, como nos canais do ML.
    await _ligar(
        db, conversa, modo=constantes.MODO_AUTO,
        auto_orientacao=["endereco", "entrega_nao_recebida"],
    )  # fmt: skip
    api.roteiro = [_final(texto=FIXA_ENDERECO, assunto="endereco", confianca=0.9)]

    r = await ia.gerar_rascunho(db, conversa)

    assert not r.precisa_humano and _do_agente(r)["acoes_pessoa"] == [], r.motivo
    assert [e["texto"] for e in envios] == [FIXA_ENDERECO] and _decisao(r) == "enviado", r.fatos


async def test_r2_e2e_a_entrega_errada_vai_para_a_pessoa(
    db: AsyncSession, make_user, agente_ligado, api, envios
):
    conversa = await _endereco_e2e(
        db, make_user, canal="pos_venda", pedido="2000001", fala="entregaram no endereço errado"
    )
    api.roteiro = [_final(texto="Vamos verificar a entrega.", assunto="outro", confianca=0.9)]

    r = await ia.gerar_rascunho(db, conversa)

    assert r.precisa_humano and constantes.ACAO_ABRIR_CHAMADO in _do_agente(r)["acoes_pessoa"]
    assert FIXA_ENDERECO != r.texto and envios == []


# ── Regra 3: o que a frase pública não pode responder ──


@pytest.mark.parametrize(
    "fala",
    [
        "Quero 20 por cento de desconto, consegue?",
        "quero 20 de desconto",
        "quero 24 qual o preço?",
        "Preciso de 23 quilos, qual o valor?",
        "Não quero 20 unidades, só uma, qual o valor?",
        "Nao preciso de 20 malas, so uma. tem desconto?",
        "Comprei 30 malas e paguei valor errado",
        "Tenho 20 malas pra vender, qual valor vocês pagam?",
        "Cabem 15 peças de roupa nessa mala? qual o valor",
        "Quanto custa a mala de 20 peças?",
        "Esse preço é para 20 unidades?",
        "Valor de 20 malas com meu nome gravado?",
        "20 malas com a marca da minha empresa, qual valor?",
    ],
)
async def test_r3_e2e_o_que_nao_pede_o_lote_vai_ao_modelo(
    db: AsyncSession, make_user, agente_ligado, api, envios, recusa_ativa, fala
):
    conversa = await _atacado_e2e(db, make_user, fala)
    api.roteiro = [
        _final(
            acao="pessoa", texto="", assunto="desconto", confianca=0.8,
            acao_pessoa=constantes.ACAO_DESCONTO_BRINDE, motivo_pessoa="ver com a equipe",
        )
    ]  # fmt: skip

    r = await ia.gerar_rascunho(db, conversa)

    assert api.pedidos and FRASE != r.texto and envios == []


@pytest.mark.parametrize(
    ("fala", "estoque", "motivo"),
    [
        ("Quero 20 mil malas, qual o valor?", 100, "20000 unidades, acima"),
        ("Preciso de 30 duzias, qual o valor?", 100, "360 unidades, acima"),
        ("Quero 40 unidades e 40 malas, qual o valor?", 50, "80 unidades, acima"),
        ("Quero 40 malas P e 40 malas G, qual o valor?", 500, "80 unidades de uma variação"),
        ("Quero 20 unidades da roxa, qual o valor?", 500, "20 unidades de uma variação"),
        ("Quero 20 unidades do modelo M2, qual o valor?", 500, "20 unidades de uma variação"),
    ],
)
async def test_r3_e2e_a_quantidade_inteira_e_a_variacao_vao_para_pessoa(
    db: AsyncSession, make_user, agente_ligado, api, envios, recusa_ativa, fala, estoque, motivo
):
    conversa = await _atacado_e2e(db, make_user, fala, estoque=estoque)
    api.roteiro = []

    r = await ia.gerar_rascunho(db, conversa)

    assert r is not None and not (r.texto or "").strip() and r.precisa_humano, r.motivo
    assert motivo in (r.motivo or "")
    assert _do_agente(r)["acoes_pessoa"] == [constantes.ACAO_DESCONTO_BRINDE]
    assert api.clientes == [] and envios == []


@pytest.mark.parametrize(
    "titulo",
    [
        "Necessaire Maquiagem Bolsa Organizador Mala Viagem",
        "Par De Rodinhas 360 Graus Reposição Mala De Viagem",
        "Chaveiro De Pelúcia Preto Para Mala Mochila E Bolsa",
    ],
)
async def test_r3_e2e_o_acessorio_que_cita_a_mala_vai_ao_modelo(
    db: AsyncSession, make_user, agente_ligado, api, envios, recusa_ativa, titulo
):
    conversa = await _atacado_e2e(
        db, make_user, "Preciso de 50 unidades, qual o valor?", estoque=800, titulo=titulo
    )
    api.roteiro = [
        _final(
            acao="pessoa", texto="", assunto="desconto", confianca=0.8,
            acao_pessoa=constantes.ACAO_DESCONTO_BRINDE, motivo_pessoa="preço por quantidade",
        )
    ]  # fmt: skip

    r = await ia.gerar_rascunho(db, conversa)

    assert api.pedidos and FRASE != r.texto and envios == []


@pytest.mark.parametrize(
    "fala",
    [
        "Consegue um valor melhor pra 20UN? Obrigado",
        "Oi, tudo bem? Consegue um valor melhor pra 20UN?",
    ],
)
async def test_r3_e2e_a_cortesia_nao_tira_a_frase(
    db: AsyncSession, make_user, agente_ligado, api, envios, recusa_ativa, fala
):
    conversa = await _atacado_e2e(db, make_user, fala)
    api.roteiro = []

    r = await ia.gerar_rascunho(db, conversa)

    assert r.texto == FRASE and not r.precisa_humano, r.motivo
    assert api.clientes == [] and [e["texto"] for e in envios] == [FRASE]


# ── Regra 3: o seguimento só na volta do mesmo assunto ──


async def _frase_ja_enviada(
    db: AsyncSession, conversa: Any, *, item: str = "MLB7000000002", externo: str = "ml-ontem"
) -> None:
    """Outra pergunta do mesmo comprador na mesma loja, em `item`, já com a frase."""
    outra, _ = await gravar.upsert_conversa(
        db,
        canal=await db.get(AtendimentoCanal, conversa.canal_id),
        integration=await db.get(Integration, conversa.integration_id),
        plataforma="ml", canal_nome="pergunta", externo_id=externo,
        comprador_id=conversa.comprador_id, comprador_nome=None, pedido_marketplace=None,
        anuncio_titulo=conversa.anuncio_titulo,
    )  # fmt: skip
    outra.anuncio_id = item
    await db.commit()
    await _msg_ia(db, outra, FRASE, autor="loja", origem="externo", ha=timedelta(days=1))


async def test_r3_e2e_o_mesmo_comprador_em_outro_anuncio_ganha_a_frase(
    db: AsyncSession, make_user, agente_ligado, api, envios, recusa_ativa
):
    # 070f1828 e 378b1cd9: a equipe respondeu a frase nos dois, com dois minutos de diferença.
    conversa = await _atacado_e2e(db, make_user, "consegue um valor melhor pra 15UN?", estoque=137)
    await _frase_ja_enviada(db, conversa)
    api.roteiro = []

    r = await ia.gerar_rascunho(db, conversa)

    assert r.texto == FRASE and not r.precisa_humano, r.motivo
    assert [e["texto"] for e in envios] == [FRASE]


@pytest.mark.parametrize(
    "fala",
    [
        # As três falas reais do comprador de 070f1828, no dia seguinte, no mesmo anúncio.
        "queria os 20% de desconto para 20 UN no atacado https://mercadolivre.com.br/x",
        "oii gostaria de 20% de desconto para 15UN no atacado https://mercadolivre.com.br/x",
        "oii, gostaria do link com 20% off para 20UN https://mercadolivre.com.br/x",
        "consegue um valor melhor pra 20UN?",
    ],
)
async def test_r3_e2e_a_volta_no_mesmo_anuncio_vai_para_pessoa(
    db: AsyncSession, make_user, agente_ligado, api, envios, recusa_ativa, fala
):
    conversa = await _atacado_e2e(db, make_user, fala)
    await _frase_ja_enviada(db, conversa, item=conversa.anuncio_id)
    api.roteiro = []

    r = await ia.gerar_rascunho(db, conversa)

    assert r is not None and not (r.texto or "").strip() and r.precisa_humano, r.motivo
    assert acao_pessoa.MOTIVO_ATACADO_SEGUIMENTO in (r.motivo or "")
    assert api.clientes == [] and envios == []


@pytest.mark.parametrize(
    "fala", ["Qual a altura da mala?", "qual o peso", "Obrigado", "tem na cor azul?"]
)
async def test_r3_e2e_outra_pergunta_depois_da_frase_vai_ao_modelo(
    db: AsyncSession, make_user, agente_ligado, api, envios, recusa_ativa, fala
):
    conversa = await _atacado_e2e(db, make_user, fala)
    await _frase_ja_enviada(db, conversa)
    api.roteiro = [_final(texto="Veja na descrição do anúncio.", assunto="outro", confianca=0.9)]

    r = await ia.gerar_rascunho(db, conversa)

    assert api.pedidos, r.motivo
    assert acao_pessoa.MOTIVO_ATACADO_SEGUIMENTO not in (r.motivo or "")


async def test_r3_a_primeira_vez_citando_o_anuncio_vai_para_pessoa(
    db: AsyncSession, make_user, recusa_ativa
):
    # Sem a frase na janela, o link e os 20% dizem que a frase não responde.
    fala = "queria os 20% de desconto para 20 UN no atacado https://mercadolivre.com.br/x"
    conversa = await _atacado_e2e(db, make_user, fala)
    caminho, motivo = await _decide(db, conversa, fala)
    assert caminho == acao_pessoa.ATACADO_PESSOA and "citando o anúncio" in motivo


@pytest.mark.parametrize(
    ("fala", "esperado"),
    [
        ("É o modelo M2 roxo", True),
        ("esse mesmo", True),
        ("https://mercadolivre.com.br/x", True),
        ("Qual a altura da mala?", False),
        ("qual o peso", False),
        ("Obrigado", False),
    ],
)
def test_r3_a_volta_da_frase(fala, esperado):
    assert acao_pessoa._responde_a_frase(validador.plano_de(fala)) is esperado


# ── A paridade de verdade: o `rodar_gatilho` grava a marca que o re-gatilho lê ──


async def test_r3_o_replay_grava_a_marca_do_atacado(
    db: AsyncSession, make_user, recusa_ativa, ajustes
):
    fala = "consegue um valor melhor pra 20UN?"
    conversa = await _atacado_e2e(db, make_user, fala)
    falsa = _ApiDoReplay([])
    gatilho = await db.scalar(
        select(AtendimentoMensagem).where(AtendimentoMensagem.conversa_id == conversa.id)
    )

    async with replay.sessao_replay() as s:
        c = await s.get(AtendimentoConversa, conversa.id)
        r = await replay.rodar_gatilho(
            s, c, _momento(gatilho), gravar=False, cliente=falsa.cliente()
        )

    assert falsa.pedidos == [] and r.texto_final == FRASE
    assert r.decisao["atacado"] is True
    canal = {"modo": "auto", "auto_categorias": [], "auto_agente": [], "auto_orientacao": []}
    x = await regatilho.regatilhar(
        {"id": "a1", "parada": r.parada, "proposta": {}, "texto_final": r.texto_final,
         "decisao": r.decisao, "plataforma": "ml"},
        canal,
    )  # fmt: skip
    assert x["decisao"] == regatilho.RECIBO and x["rascunho"] == FRASE
