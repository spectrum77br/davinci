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


async def test_r1_o_legado_no_ml_recusa_e_a_shopee_no_envio_nao_muda(db: AsyncSession, make_user):
    texto = NEGATIVAS_REAIS["dc962f66"]
    conversa = await _conversa_ml(db, make_user)
    legado = await _rascunho(db, conversa, texto, {})
    with pytest.raises(enviar.EnvioRecusado):
        await _envio(db, conversa, texto, legado)
    # Os padrões novos valem para o legado do ML, sem a isenção do agente: mais rígido, nunca menos.
    forma_nova = "Não temos como garantir a entrega para amanhã."
    r_novo = await _rascunho(db, conversa, forma_nova, {}, status="substituido")
    with pytest.raises(enviar.EnvioRecusado):
        await _envio(db, conversa, forma_nova, r_novo)
    assert validador.validar(
        forma_nova, plataforma="shopee", canal="chat", origem=constantes.ORIGEM_IA
    ) == []
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
    assert len(casos) == 16
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
        "Por questão de segurança, não podemos alterar o endereço da compra.",
        "A entrega segue o endereço do pedido e a loja não passa recado ao entregador.",
        f"{FIXA_ENDERECO} O celular é o modelo do anúncio.",
    ],
)
def test_r2_com_o_pedido_so_o_texto_fixo_sai(texto):
    assert _no_ml(_proposta(FIXA_ENDERECO)).texto == FIXA_ENDERECO
    assert _no_ml(_proposta(texto)).texto == FIXA_ENDERECO


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
    # Com o pedido no turno, o assunto declarado não muda a saída: o texto fixo.
    novo = _no_ml(prazo)
    assert novo.texto == FIXA_ENDERECO and novo.assunto == "endereco"


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
    canal = {"modo": "auto", "auto_categorias": [], "auto_agente": [], "auto_orientacao": ["desconto"]}
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


def test_r2_outra_parte_do_turno_sem_o_pedido_nao_vira_a_frase():
    # Sem o pedido de mudança, o modelo respondeu a outra coisa: fica para o verificador.
    for texto in ("O prazo aparece no anúncio.", "Seu pedido foi postado hoje, mas ainda sem rastreio."):
        prazo = _proposta(texto, assunto="prazo_antes_compra")
        assert _no_ml(prazo, pede=False) == prazo


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
    # O assunto que o modelo declarou não decide: na pergunta, sem o verbo de mudar, é a cobertura.
    assert _pergunta_coberta(fala, texto, "outro").texto == texto
    # Depois da compra é o pedido.
    assert _pergunta_coberta(fala, texto, "cobertura_regiao", "pos_venda").texto == FIXA_ENDERECO


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
    canal = {"modo": "auto", "auto_categorias": [], "auto_agente": [], "auto_orientacao": ["desconto"]}
    x = await regatilho.regatilhar(
        {"id": "a1", "parada": r.parada, "proposta": {}, "texto_final": r.texto_final,
         "decisao": r.decisao, "plataforma": "ml"},
        canal,
    )  # fmt: skip
    assert x["decisao"] == regatilho.RECIBO and x["rascunho"] == FRASE


# ═══════════════ A segunda rodada de revisão das correções ═══════════════


def _isenta(texto: str, canal: str = "pergunta") -> bool:
    """A negativa passa nas camadas do texto: o validador, o envio e a regra suave."""
    final = validador.normalizar(texto, plataforma="ml", canal=canal)
    motivos = validador.validar(final, plataforma="ml", canal=canal, origem=constantes.ORIGEM_IA)
    if motivos and not (
        motivos == [validador.MOTIVO_PRAZO]
        and checagens.prazo_do_validador_sem_afirmacao(final, plataforma="ml")
    ):
        return False
    return checagens.conferir_prazo_sem_lastro(final, plataforma="ml") == []


# ── Regra 1: o que dizia "chega" com a negação em volta ──

PROMESSAS_DA_REVISAO = [
    # O "e" depois do sujeito é a conjunção quando vem o verbo de chegada.
    "Não temos como garantir a data e chega amanhã.",
    "Não temos como garantir o prazo e chega amanhã.",
    "Não temos como garantir a entrega e chega amanhã.",
    "Não temos como confirmar o envio e chega amanhã.",
    "Não temos como confirmar a previsão e chega amanhã.",
    "Não temos como garantir a data e vai chegar amanhã.",
    "Não temos como garantir o prazo e sai hoje.",
    "Não temos como garantir a entrega e sai amanhã.",
    "Não temos como garantir a data e chega na sexta.",
    # O dia afirmado na frase seguinte, ou depois da negativa do fato.
    "Não temos como garantir a chegada amanhã. A previsão é sexta.",
    "Não temos como garantir a chegada amanhã. Só na sexta.",
    "Não temos como garantir a chegada amanhã. Talvez na sexta.",
    "Não temos como garantir a chegada amanhã. Sexta com certeza.",
    "Não temos como garantir a chegada amanhã. Sexta, sim.",
    "Não temos como garantir a chegada amanhã. Sexta é mais certo.",
    "Não temos entrega para hoje, só na sexta.",
    "Não fazemos entrega para domingo, só na segunda.",
    "Não fazemos entrega para domingo. Segunda sim.",
    # A promessa sem negação nenhuma.
    "Fazemos a entrega na sexta.",
    "Temos entrega na sexta.",
    "Prometemos a chegada na sexta.",
    "A entrega acontecerá na sexta.",
    "Na sexta você recebe.",
    "Terça você recebe.",
    "Na terça está aí.",
    "Você terá o pedido na sexta.",
    "Chega sim.",
    "Sim, chega.",
    "Chega.",
    "Pode comprar que chega.",
    "Pode ficar tranquilo, chega a tempo.",
    # O vão que dá a chegada por certa, fora de qualquer lista de palavras.
    "Não sabemos em que momento chega amanhã.",
    "Não temos como dizer em que parte do dia chega amanhã.",
    "Não temos como informar o minuto exato em que chega amanhã.",
    "Não temos como informar exatamente quando chega amanhã.",
    "Não sei como chega amanhã.",
    "Pode conferir se chega amanhã: chega.",
    "Consulte se chega amanhã, mas chega.",
    "Não temos como garantir a cor chega amanhã.",
    # A negativa de outra coisa depois do dia afirmado.
    "A entrega é amanhã, não temos como garantir o horário.",
    "Não temos como garantir o horário, mas chega amanhã.",
]


@pytest.mark.parametrize("texto", PROMESSAS_DA_REVISAO)
def test_rev2_a_promessa_escondida_nao_passa_nas_camadas(texto):
    assert not _isenta(texto)


NEGATIVAS_DA_REVISAO = [
    # Outro assunto confirmado não desfaz a negativa.
    "O pagamento está confirmado, mas não temos como garantir a chegada amanhã.",
    "Sua compra está confirmada. Não temos como garantir a chegada amanhã.",
    "O pedido está confirmado. Não temos como garantir a entrega amanhã.",
    # O dia da compra, e a lista dos dias que também não valem.
    "Não temos como garantir que chegue amanhã, mesmo comprando hoje.",
    "Não temos como garantir a chegada amanhã, mesmo que você compre hoje.",
    "Não temos como garantir a chegada amanhã, sexta ou sábado.",
    "Não temos como garantir a chegada amanhã, e o prazo para o seu CEP aparece hoje no anúncio.",
    "Não temos como garantir a chegada amanhã, pois o prazo depende de quando a compra é feita hoje.",
    # Quem faz a entrega é o sujeito da negativa, não a afirmação.
    "Não temos como garantir que a transportadora entregue amanhã.",
    "Não conseguimos confirmar com a transportadora se chega amanhã.",
    "Não temos como garantir que a transportadora faça a entrega amanhã.",
    "Não temos como confirmar com a transportadora se a entrega é amanhã.",
    # O substantivo antes da negativa.
    "Sobre a entrega para amanhã, não temos como garantir.",
    "A entrega para hoje não é possível.",
    "A entrega para sábado depende da rotina da transportadora.",
    "Não trabalhamos com entrega para hoje.",
    # As formas da loja como sujeito e do pronome.
    "A loja não garante a chegada amanhã.",
    "Não temos como te garantir a chegada amanhã.",
]


@pytest.mark.parametrize("texto", NEGATIVAS_DA_REVISAO)
def test_rev2_a_negativa_honesta_passa_nas_camadas(texto):
    assert _isenta(texto), texto


async def test_rev2_a_negativa_com_substantivo_antes_passa_no_envio(db: AsyncSession, make_user):
    conversa = await _conversa_ml(db, make_user)
    for texto in (
        "Sobre a entrega para amanhã, não temos como garantir.",
        "A entrega para hoje não é possível.",
        "A entrega para sábado depende da rotina da transportadora.",
    ):
        r = await _rascunho(db, conversa, texto, {"agente": {"isencoes": []}}, status="substituido")
        assert await _envio(db, conversa, texto, r) == texto


@pytest.mark.parametrize(
    "texto",
    [
        "Não garantimos a chegada amanhã.",
        "Não garantimos que chegue amanhã.",
        "Não temos como garantir que o celular chegue amanhã.",
        "Não temos como garantir que ele chegue amanhã.",
        "Não temos como garantir que o produto chegue amanhã.",
        "Não temos como garantir que o Mercado Livre entregue amanhã.",
        "Não temos como garantir se a entrega será amanhã.",
        "Não temos como garantir chegada amanhã.",
        "Não temos como garantir nem confirmar a chegada amanhã.",
        "Não temos como garantir essa data.",
        "A entrega para amanhã não pode ser garantida.",
        "A loja não garante a chegada amanhã.",
        "Não temos como te garantir a chegada amanhã.",
    ],
)
async def test_rev2_a_negativa_com_outras_palavras_nao_e_fala_de_garantia(texto):
    msgs = _cli("Este celular vai chegar amanhã pq no dia 10 eu não vô está aqui não")
    p = Proposta(
        acao="responder", texto=texto, assunto="prazo_antes_compra", confianca=0.9,
        acao_pessoa=None, motivo_pessoa="", evidencias=["regra:prazo_antes_compra"],
    )  # fmt: skip
    conv = _conv(plataforma="ml", canal="pergunta", pedido_marketplace=None)
    x = await _auto(p, msgs, conversa=conv)
    assert ia.MOTIVO_RESPOSTA_DE_GARANTIA not in x.motivos, x.motivos
    assert ia.MOTIVO_RECUSA_DE_DIREITO not in x.motivos, x.motivos
    assert not any("prazo em números" in m for m in x.motivos), x.motivos
    assert checagens.PRAZO_SEM_LASTRO not in x.codigos


@pytest.mark.parametrize(
    "texto",
    [
        "Não garantimos o aparelho.",
        "Não garantimos a troca.",
        "Não temos como garantir o conserto do celular.",
    ],
)
def test_rev2_a_negativa_da_garantia_continua_com_a_pessoa(texto):
    # A oração tem de falar só da entrega: aqui segue a régua de sempre, e o texto volta intacto.
    assert validador.sem_a_negativa_da_entrega_no_ml(texto) == texto


# ── Regra 2: a entrega errada não recebe a frase ──

# As listas dos 48 canais do ML em produção (05/11/2026): a regra não pode depender de um canal
# de teste mais estreito que o real.
ORIENTACAO_DO_ML = [
    "cancelamento", "defeito", "desconto", "divergencia_anuncio", "endereco",
    "entrega_nao_recebida", "garantia", "item_errado_faltando", "outro", "reembolso",
    "substituicao_estoque", "troca_devolucao",
]  # fmt: skip
AGENTE_DO_ML = [
    "acessorios_avulsos", "agradecimento", "canal_externo_dados", "cobertura_regiao",
    "confirmar_variacao", "entrega_instrucoes", "estoque_variacao", "nota_fiscal",
    "opiniao_recomendacao", "parceria_fora_escopo", "pede_atendente", "pedido_link_midia",
    "pedido_nao_localizado", "prazo_antes_compra", "prazo_envio", "preco_pagamento", "rastreio",
    "saudacao_ou_vaga", "uso_configuracao",
]  # fmt: skip


async def _com_as_listas_do_ml(db: AsyncSession, conversa: Any) -> None:
    await _ligar(
        db, conversa, modo=constantes.MODO_AUTO, auto_orientacao=ORIENTACAO_DO_ML,
        auto_categorias=AGENTE_DO_ML, auto_agente=AGENTE_DO_ML,
    )  # fmt: skip


ENTREGA_ERRADA_DA_REVISAO = [
    "Entregaram no vizinho",
    "Foi entregue em outro bairro",
    "Foi entregue na rua de trás, no vizinho",
    "O correio entregou no número errado",
    "Deixaram meu pedido em outra rua",
    "Meu pedido foi entregue em outro estado",
    "Consta entregue mas não é o meu endereço",
    # O "e" e o "que" antes do verbo só valem para "é entregue" e "que entregue".
    "O correio veio e entregou no endereço errado.",
    "Saiu pra entrega e entregaram no endereço errado.",
    "Disseram que entregaram no endereço errado.",
    "A transportadora disse que entregou na casa errada.",
    "O entregador veio e entregou para outra pessoa.",
    # O endereço antigo é o lugar errado.
    "Deixaram no meu endereço antigo.",
    "Chegou no endereço antigo.",
    "Foi pro endereço antigo.",
    "Levaram para o endereço antigo.",
    "Entregaram no meu endereço antigo.",
]


@pytest.mark.parametrize("fala", ENTREGA_ERRADA_DA_REVISAO)
@pytest.mark.parametrize("modelo", ["acao", "frase"])
async def test_rev2_e2e_a_entrega_errada_vai_para_a_pessoa(
    db: AsyncSession, make_user, agente_ligado, api, envios, fala, modelo
):
    conversa = await _endereco_e2e(db, make_user, canal="pos_venda", pedido="2000001", fala=fala)
    await _ligar(
        db, conversa, modo=constantes.MODO_AUTO,
        auto_orientacao=["endereco", "entrega_nao_recebida"],
    )  # fmt: skip
    api.roteiro = [
        _final(
            acao="pessoa", texto="", assunto="endereco", confianca=0.8,
            acao_pessoa=constantes.ACAO_ALTERAR_ENDERECO, motivo_pessoa="mudar o endereço",
        )
        if modelo == "acao"
        else _final(texto=FIXA_ENDERECO, assunto="endereco", confianca=0.9)
    ]  # fmt: skip

    r = await ia.gerar_rascunho(db, conversa)

    assert constantes.ACAO_ABRIR_CHAMADO in _do_agente(r)["acoes_pessoa"], r.motivo
    assert FIXA_ENDERECO != r.texto and envios == []


def test_rev2_a_proposta_na_entrega_que_falhou_nao_vira_a_frase():
    msgs = _cli("por que não entregaram no endereço certo?")
    p = checagens.endereco_no_ml_da_proposta(_proposta(FIXA_ENDERECO), _ctx(), msgs, msgs[-1])
    assert p.acao == "pessoa" and p.acao_pessoa == constantes.ACAO_ABRIR_CHAMADO and not p.texto
    # A resposta de outro assunto fica para o verificador: não é a frase.
    outro = _proposta("Veja o status em Minhas compras.", assunto="rastreio")
    assert checagens.endereco_no_ml_da_proposta(outro, _ctx(), msgs, msgs[-1]) == outro


@pytest.mark.parametrize(
    "fala",
    [
        # 18f03bf2 e d1b83122: a entrega a caminho do lugar errado, em produção.
        "o pacote está andando em endereço errado",
        "porque ele ta andando em endereço errado",
        "estão entregando no lugar errado",
        "estão entregando no endereço errado",
        "o envio está rodando em endereço errado",
    ],
)
def test_rev2_a_entrega_em_andamento_no_lugar_errado_abre_chamado(fala):
    msgs = _cli(fala)
    ml = _conv(plataforma="ml", canal="pos_venda")
    assert constantes.ACAO_ABRIR_CHAMADO in _gatilhos(ml, msgs, postado=acao_pessoa.POSTADO_SIM)
    assert not acao_pessoa.pede_alterar_endereco(msgs, msgs[-1])


async def test_rev2_e2e_18f03bf2_nao_recebe_a_frase(
    db: AsyncSession, make_user, agente_ligado, api, envios
):
    conversa = await _endereco_e2e(
        db, make_user, canal="pos_venda", pedido="2000001",
        fala="Meu pacote está andando em endereço errado e tenho medo de chegar quebrado",
    )  # fmt: skip
    await _ligar(
        db, conversa, modo=constantes.MODO_AUTO,
        auto_orientacao=["endereco", "entrega_nao_recebida", "rastreio"],
    )  # fmt: skip
    api.roteiro = [_final(texto="Acompanhe em Minhas compras.", assunto="rastreio", confianca=0.9)]

    r = await ia.gerar_rascunho(db, conversa)

    assert constantes.ACAO_ABRIR_CHAMADO in _do_agente(r)["acoes_pessoa"], r.motivo
    assert FIXA_ENDERECO != r.texto and envios == []


@pytest.mark.parametrize(
    "fala",
    [
        "coloquei o endereço errado e vocês já enviaram, pode mudar?",
        "coloquei o endereço errado, recebi a mensagem que já foi postado",
        "recebi o email da compra, coloquei o endereço errado",
        "recebi a confirmação e vi que está no endereço errado, quero mudar",
        "pode entregar em outro endereço? já me enviaram o código de rastreio",
        "pode entregar em outro endereço? recebi o aviso de que saiu",
        "Se enviou hoje ainda dá para mudar o endereço?",
        "Recebi a confirmação da compra e quero alterar o endereço",
        "Ainda não chegou e quero mudar o endereço de entrega",
        "Já enviou? quero mudar o endereço",
    ],
)
async def test_rev2_e2e_o_verbo_solto_nao_tira_o_pedido_de_mudanca(
    db: AsyncSession, make_user, agente_ligado, api, envios, fala
):
    msgs = _cli(fala)
    assert acao_pessoa.pede_alterar_endereco(msgs, msgs[-1])
    ml = _conv(plataforma="ml", canal="pos_venda")
    assert constantes.ACAO_ABRIR_CHAMADO not in _gatilhos(ml, msgs, postado=acao_pessoa.POSTADO_SIM)
    conversa = await _endereco_e2e(db, make_user, canal="pos_venda", pedido="2000001", fala=fala)
    await _com_as_listas_do_ml(db, conversa)
    api.roteiro = [_final(texto="Claro, para onde devemos enviar?", assunto="endereco", confianca=0.9)]

    r = await ia.gerar_rascunho(db, conversa)

    assert r.texto == FIXA_ENDERECO and not r.precisa_humano, r.motivo
    assert [e["texto"] for e in envios] == [FIXA_ENDERECO], r.fatos


@pytest.mark.parametrize(
    ("canal", "pedido", "fala", "assunto", "texto"),
    [
        # O assunto declarado não desliga a regra.
        ("pergunta", None, "Se eu comprar, dá pra trocar o endereço depois?", "cobertura_regiao", "Sim, pode sim."),
        ("pergunta", None, "Posso mudar o endereço depois de comprar?", "cobertura_regiao", "Sim, enviamos para todo o Brasil."),
        ("pergunta", None, "Dá para mudar o endereço depois de comprar?", "prazo_antes_compra", "Para onde você quer receber?"),
        ("pergunta", None, "Dá para mudar o endereço depois de comprar?", "prazo_antes_compra", "Fale conosco pelas mensagens da loja depois da compra."),
        ("pos_venda", "2000001", "Quero alterar o endereço de entrega", "rastreio", "Para onde você quer receber?"),
        ("pos_venda", "2000001", "Quero alterar o endereço de entrega", "rastreio", "Procure o vendedor pelas mensagens."),
        ("pos_venda", "2000001", "Quero alterar o endereço de entrega", "rastreio", "Fale conosco pelas mensagens."),
        ("pos_venda", "2000001", "Quero alterar o endereço de entrega", "rastreio", "Dá para ajustar, sim."),
        # A negativa com outra coisa depois.
        ("pos_venda", "2000001", "Quero alterar o endereço de entrega", "endereco", "Não alteramos o endereço por aqui. Fale conosco pelas mensagens."),
        ("pos_venda", "2000001", "Quero alterar o endereço de entrega", "endereco", "Não alteramos o endereço pelo chat, ligue para nós."),
        ("pos_venda", "2000001", "Quero alterar o endereço de entrega", "endereco", "Não mudamos o endereço pelo chat. Você consegue alterar em Minhas compras."),
        ("pos_venda", "2000001", "Quero alterar o endereço de entrega", "endereco", "Não alteramos o endereço. O correio altera se você pedir."),
        ("pos_venda", "2000001", "Quero alterar o endereço de entrega", "endereco", "Não alteramos o endereço. Para onde você quer receber?"),
    ],
)
async def test_rev2_e2e_com_o_pedido_a_saida_e_o_texto_fixo(
    db: AsyncSession, make_user, agente_ligado, api, envios, canal, pedido, fala, assunto, texto
):
    conversa = await _endereco_e2e(db, make_user, canal=canal, pedido=pedido, fala=fala)
    await _com_as_listas_do_ml(db, conversa)
    api.roteiro = [_final(texto=texto, assunto=assunto, confianca=0.9)]

    r = await ia.gerar_rascunho(db, conversa)

    assert r.texto == FIXA_ENDERECO and r.categoria == "endereco" and not r.precisa_humano, r.motivo
    assert [e["texto"] for e in envios] == [FIXA_ENDERECO], r.fatos


NEGATIVA_DA_REGRA = NEGATIVAS_REAIS["regra"]


@pytest.mark.parametrize(
    ("fala", "assunto"),
    [
        ("Meu endereço é em Goiás, chega amanhã?", "prazo_antes_compra"),
        ("envia para outro estado?", "prazo_antes_compra"),
        ("envia para outra cidade?", "outro"),
        ("entrega em outra cidade?", "prazo_antes_compra"),
        ("entrega no endereço do meu trabalho?", "entrega_instrucoes"),
    ],
)
def test_rev2_a_regra_do_endereco_nao_apaga_a_negativa_do_prazo(fala, assunto):
    p = _pergunta_coberta(fala, NEGATIVA_DA_REGRA, assunto)
    assert p.texto == NEGATIVA_DA_REGRA and p.assunto == assunto


async def test_rev2_e2e_a_negativa_com_o_endereco_na_fala_sai_na_pergunta(
    db: AsyncSession, make_user, agente_ligado, api, envios
):
    conversa = await _endereco_e2e(
        db, make_user, canal="pergunta", pedido=None, fala="Meu endereço é em Goiás, chega amanhã?"
    )
    await _com_as_listas_do_ml(db, conversa)
    api.roteiro = [_final(texto=NEGATIVA_DA_REGRA, assunto="prazo_antes_compra", confianca=0.9)]

    r = await ia.gerar_rascunho(db, conversa)

    assert r.texto == NEGATIVA_DA_REGRA and not r.precisa_humano, r.motivo
    assert [e["texto"] for e in envios] == [NEGATIVA_DA_REGRA] and _decisao(r) == "enviado", r.fatos


@pytest.mark.parametrize(
    "fala",
    [
        "Quero trocar a mala, qual o endereço para devolver?",
        "Mudei de ideia, qual o endereço da loja de vocês?",
        "Quero trocar a mala qual o endereço para devolver?",
    ],
)
def test_rev2_a_pista_solta_nao_e_pedido_de_mudanca(fala):
    msgs = _cli(fala)
    assert not acao_pessoa.pede_alterar_endereco(msgs, msgs[-1])
    for canal, texto in (("pergunta", "A devolução é pelo Mercado Livre."), ("pos_venda", "Não temos loja física.")):
        p = _proposta(texto)
        assert checagens.endereco_no_ml_da_proposta(p, _ctx(canal=canal), msgs, msgs[-1]) == p


@pytest.mark.parametrize(
    ("fala", "pede"),
    [
        # O verbo de entrega negado não tira o pedido com todas as letras.
        ("Meu pedido ainda não foi entregue, quero mudar o endereço", True),
        ("Se for entregue amanhã, dá para mudar o endereço?", True),
        ("Levou muito tempo, quero mudar o endereço", True),
        # O endereço certo não é pedido de mudança.
        ("O endereço não está errado, por que não entregaram?", False),
    ],
)
def test_rev2_o_pedido_com_todas_as_letras_e_a_entrega_que_falhou(fala, pede):
    msgs = _cli(fala)
    assert acao_pessoa.pede_alterar_endereco(msgs, msgs[-1]) is pede


@pytest.mark.parametrize("canal", ["reclamacao", "avaliacao"])
def test_rev2_a_regra_do_endereco_e_so_da_pergunta_e_do_pos_venda(canal):
    msgs = _cli("Quero alterar o endereço de entrega")
    p = _proposta("", acao="pessoa", acao_pessoa=constantes.ACAO_ALTERAR_ENDERECO)
    assert checagens.endereco_no_ml_da_proposta(p, _ctx(canal=canal), msgs, msgs[-1]) == p


def test_rev2_o_quatro_com_outra_opcao_do_menu_nao_e_pedido():
    # c29cf3c9: apertou 4 e depois 5, e contou o defeito.
    msgs = _turno_do_menu()
    assert acao_pessoa.pede_alterar_endereco(msgs, msgs[-1])
    dois = [*msgs, *_cli("5")]
    assert ia.assuntos_sensiveis_do_menu(dois, dois[-1]) == ["endereco", "garantia"]
    assert not acao_pessoa.pede_alterar_endereco(dois, dois[-1])


@pytest.mark.parametrize(
    ("fala", "texto", "canal"),
    [
        ("Entrega no meu endereço?", "O endereço é o que você escolhe na compra, mas o prazo e o frete aparecem no anúncio ao informar o CEP.", "pergunta"),
        ("Quero confirmar o endereço de entrega", "A entrega segue o endereço do pedido, mas você confere em Minhas compras.", "pos_venda"),
        ("qual endereço está no pedido?", "A entrega segue o endereço do pedido que você vê hoje em Minhas compras.", "pos_venda"),
    ],
)
def test_rev2_a_palavra_comum_nao_e_a_ressalva(fala, texto, canal):
    msgs = _cli(fala)
    p = _proposta(texto)
    assert checagens.endereco_no_ml_da_proposta(p, _ctx(canal=canal), msgs, msgs[-1]) == p
    assert checagens.conferir_endereco_no_ml(
        texto, acao="responder", assunto="endereco", plataforma="ml"
    ) == []


def test_rev2_a_ressalva_colada_a_negativa_continua():
    for texto in ("Não alteramos o endereço agora.", "Não alteramos o endereço, mas a transportadora troca."):
        (trava,) = checagens.conferir_endereco_no_ml(
            texto, acao="responder", assunto="endereco", plataforma="ml"
        )
        assert trava.codigo == checagens.ENDERECO_NO_ML


@pytest.mark.parametrize("assunto", ["entrega_instrucoes", "rastreio"])
def test_rev2_a_frase_do_bloco_1_nao_segura_outro_assunto(assunto):
    texto = (
        "A plataforma não permite alterar o endereço depois do envio; acompanhe em Minhas compras. "
        "A entrega segue o endereço do pedido e a loja não passa recado ao entregador."
    )
    assert checagens.conferir_endereco_no_ml(
        texto, acao="responder", assunto=assunto, plataforma="ml"
    ) == []
    # Com o pedido no turno, a ressalva conta: a saída é o texto fixo.
    assert checagens.conferir_endereco_no_ml(
        texto, acao="responder", assunto=assunto, plataforma="ml", pede=True
    )


@pytest.mark.parametrize(
    ("fala", "segura"),
    [
        ("comprei no endereço errado, tem como mudar?", False),
        ("comprei errado o endereço", False),
        ("comprei com o endereço errado", False),
        ("comprei errado, quero outro", True),
        ("comprei errado o endereço e me arrependi", True),
    ],
)
def test_rev2_comprei_no_endereco_errado_nao_segura_o_envio(fala, segura):
    msgs = _cli(fala)
    ml = _conv(plataforma="ml", canal="pos_venda")
    assert (constantes.ACAO_SEGURAR_ENVIO in _gatilhos(ml, msgs, postado=None)) is segura
    # Fora do ML, o gatilho 7 não muda.
    if "endereço" in fala and "com o" not in fala:
        assert constantes.ACAO_SEGURAR_ENVIO in _gatilhos(_conv(), msgs, postado=None)


# ── Regra 3: a frase só para a fala que cabe inteira no pedido ──


@pytest.mark.parametrize(
    "fala",
    [
        # A dúvida sobre o preço.
        "Esse preço vale para 20 unidades?",
        "Esse preço é só para 20 unidades?",
        "Esse valor é mesmo por 20 unidades?",
        # Outro produto, a capacidade, a negação e a compra feita.
        "Qual o preço da capa pra 20 malas?",
        "Qual o preço do cadeado para 20 malas?",
        "Qual o valor dessa mala? 15 peças cabem nela",
        "Qual o valor da mala pra levar 20 peças?",
        "Nem quero 20 unidades, só uma, qual o valor?",
        "Queria 20 unidades mas agora só uma, qual o valor?",
        # O multiplicador e os lotes que detalham o total.
        "Quero 10 caixas, 20 unidades em cada, qual valor?",
        "Quero 30 pacotes, 20 unidades por pacote, qual valor?",
        "Quero 20 unidades de cada, qual o valor?",
        # A outra dúvida, a contraoferta e a personalização.
        "Essa mala é original? Quero 20 unidades, qual o valor",
        "Tem loja física? Quero 20 unidades, qual o valor",
        "Quero 20 unidades, aceita cartão? qual o valor",
        "Quero 20 unidades, são novas? qual o valor",
        "Quero 20 unidades, qual o valor e o peso",
        "Quero 20 unidades, qual o valor em 3 vezes",
        "Preciso de 20 malas pra amanhã, qual o valor?",
        "Consegue um valor melhor pra 20 unidades a 150 cada?",
        "Quero 20 unidades, faço 150 cada, qual o desconto?",
        "Preciso de 20 malas para bordar, qual o valor?",
        "Preciso de 20 malas c/ cadeado, qual o valor?",
    ],
)
async def test_rev2_e2e_o_que_nao_cabe_no_pedido_nao_recebe_a_frase(
    db: AsyncSession, make_user, agente_ligado, api, envios, recusa_ativa, fala
):
    assert acao_pessoa.quantidade_de_atacado(validador.plano_de(fala)) is None
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
    ("fala", "quantidade"),
    [
        # O que nomeia outra coisa junto do lote: não é o pedido puro.
        ("Quero 20 malas tiffany, qual o valor?", 20),
        ("Quero 20 malas champanhe, qual o valor?", 20),
        ("Quero 20 malas mostarda, qual o valor?", 20),
        ("Quero 20 malas rose gold, qual o valor?", 20),
        ("Quero 20 malas caqui, qual o valor?", 20),
        ("Quero 20 malas de despacho, qual o valor?", 20),
        ("Quero 20 malas de 24, qual o valor?", 20),
        ("Quero 20 malas grandes, qual o valor?", 20),
        ("Quero 20 unidades da G, qual o valor?", 20),
        ("Preta, 20 unidades, qual o valor?", 20),
        ("Quero 20 unidades de rodinha, qual o valor?", 20),
        ("Quero 20 unidades de cadeado, qual o valor?", 20),
        ("Quero 20 unidades de celular, qual valor?", 20),
        ("Valor das 20 unidades da minha compra", 20),
        ("Compro 20 malas no CNPJ, qual o valor?", 20),
    ],
)
async def test_rev2_e2e_o_lote_com_outro_nome_vai_para_a_pessoa(
    db: AsyncSession, make_user, agente_ligado, api, envios, recusa_ativa, fala, quantidade
):
    assert acao_pessoa.quantidade_de_atacado(validador.plano_de(fala)) == quantidade
    conversa = await _atacado_e2e(db, make_user, fala, estoque=500)
    api.roteiro = []

    r = await ia.gerar_rascunho(db, conversa)

    assert r is not None and not (r.texto or "").strip() and r.precisa_humano, r.motivo
    assert f"{quantidade} unidades de uma variação" in (r.motivo or "")
    assert api.clientes == [] and envios == []


@pytest.mark.parametrize(
    ("fala", "quantidade"),
    [
        ("Quero 100 ou 20 unidades, qual o valor?", 100),
        ("Quero entre 20 e 30 malas, qual o valor?", 30),
        ("preço para 20 a 40 unidades", 40),
    ],
)
def test_rev2_na_faixa_vale_o_maior_numero(fala, quantidade):
    assert acao_pessoa.quantidade_de_atacado(validador.plano_de(fala)) == quantidade


@pytest.mark.parametrize(
    "fala",
    [
        "Consegue um valor melhor pra 20UN??",
        "tem desconto para 20 unidades??",
        "quanto ficam 50 unidades?",
        "quanto custam 30 malas?",
        "20 unidades fica quanto?",
        "20 peças sai por quanto?",
        "quanto sairia 20 unidades?",
        "quanto custaria 20 unidades?",
        "20 unidades sai quanto?",
        "preço p/ 30 unidades",
        "preço para 30 pçs",
        "gostei da mala e quero 20 unidades, qual o valor?",
        "tenho uma loja e preciso de 30 malas, qual o valor?",
        "Boa tarde e preciso de 30 malas, tem desconto?",
        "Sou lojista, preciso de 30 malas, qual o valor?",
        "quero 20 unidades desse modelo, qual o valor?",
        "Qual o valor de 30 malas desse modelo?",
        "consegue um valor melhor pra 20UN desse modelo?",
        "tem desconto pra 20 unidades desse tamanho?",
        "Consegue um desconto comprando 20 unidades desse?",
        "Quero 20 unidades da mala, qual o valor?",
    ],
)
async def test_rev2_e2e_o_pedido_de_verdade_recebe_a_frase(
    db: AsyncSession, make_user, agente_ligado, api, envios, recusa_ativa, fala
):
    conversa = await _atacado_e2e(db, make_user, fala)
    api.roteiro = []

    r = await ia.gerar_rascunho(db, conversa)

    assert r.texto == FRASE and not r.precisa_humano, r.motivo
    assert api.clientes == [] and [e["texto"] for e in envios] == [FRASE]


@pytest.mark.parametrize(
    "fala",
    [
        "Quero a M2 roxa",
        "Quero a preta",
        "Gostaria da roxa escuro",
        "Preciso da preta",
        "Pode ser a roxa",
        "M2 roxa, consegue?",
        "Pode criar pra mim?",
        "Queria aquele desconto na roxa",
        "Pode fazer esse valor nas 20?",
        "Qual a altura da mala?",
    ],
)
async def test_rev2_e2e_a_volta_ao_mesmo_anuncio_vai_sempre_para_a_pessoa(
    db: AsyncSession, make_user, agente_ligado, api, envios, recusa_ativa, fala
):
    conversa = await _atacado_e2e(db, make_user, fala)
    await _frase_ja_enviada(db, conversa, item=conversa.anuncio_id)
    api.roteiro = []

    r = await ia.gerar_rascunho(db, conversa)

    assert r is not None and not (r.texto or "").strip() and r.precisa_humano, r.motivo
    assert acao_pessoa.MOTIVO_ATACADO_SEGUIMENTO in (r.motivo or "")
    assert api.clientes == [] and envios == []


async def test_rev2_o_agradecimento_no_mesmo_anuncio_nao_e_a_volta(
    db: AsyncSession, make_user, recusa_ativa
):
    conversa = await _atacado_e2e(db, make_user, "Obrigado!")
    await _frase_ja_enviada(db, conversa, item=conversa.anuncio_id)
    assert await _decide(db, conversa, "Obrigado!") is None


@pytest.mark.parametrize(
    ("fala", "esperado"),
    [
        ("Quero a M2 roxa", True),
        ("Pode ser a roxa", True),
        ("Gostaria da roxa escuro", True),
        ("Preciso da preta", True),
        ("Tem na cor azul", False),
    ],
)
def test_rev2_quero_e_a_volta_natural_em_outro_anuncio(fala, esperado):
    assert acao_pessoa._responde_a_frase(validador.plano_de(fala)) is esperado


# ── Regra 3: a frase passa pela régua do canal ──


async def test_rev2_a_frase_nao_sai_com_o_desconto_desligado_no_canal(
    db: AsyncSession, make_user, agente_ligado, api, envios, recusa_ativa
):
    conversa = await _atacado_e2e(db, make_user, "consegue um valor melhor pra 20UN?")
    await _ligar(db, conversa, modo=constantes.MODO_AUTO, auto_orientacao=[], auto_categorias=[])
    api.roteiro = []

    r = await ia.gerar_rascunho(db, conversa)

    assert r.texto == FRASE and envios == []
    assert _decisao(r) == "orientacao_nao_liberada", r.fatos


async def test_rev2_a_frase_do_copiloto_nao_sai_quando_o_canal_vira_auto(
    db: AsyncSession, make_user, agente_ligado, api, envios, recusa_ativa
):
    conversa = await _atacado_e2e(db, make_user, "consegue um valor melhor pra 20UN?")
    await _ligar(db, conversa, modo=constantes.MODO_COPILOTO, auto_orientacao=["desconto"])
    api.roteiro = []
    r = await ia.gerar_rascunho(db, conversa)
    assert r.texto == FRASE and envios == [] and _do_agente(r)["modo"] == constantes.MODO_COPILOTO
    canal = await _ligar(db, conversa, modo=constantes.MODO_AUTO, auto_orientacao=["desconto"])

    assert ia.trava_do_agente(r, canal, ligado=True) == "gerado_no_copiloto"
    assert not await ia._talvez_enviar(db, conversa, r)
    assert envios == []


async def test_rev2_o_regatilho_le_a_regua_do_canal_na_frase():
    base = {"id": "a1", "parada": "x", "proposta": {}, "texto_final": FRASE,
            "decisao": {"atacado": True}, "plataforma": "ml"}  # fmt: skip
    livre = {"modo": "auto", "auto_categorias": [], "auto_agente": [], "auto_orientacao": ["desconto"]}
    x = await regatilho.regatilhar(base, livre)
    assert x["decisao"] == regatilho.RECIBO
    y = await regatilho.regatilhar(base, {**livre, "auto_orientacao": []})
    assert y["decisao"] == "pessoa:orientacao_nao_liberada"
    z = await regatilho.regatilhar(base, {**livre, "modo": "copiloto"})
    assert z["decisao"] == "pessoa:canal_fora_do_auto"
    assert (await regatilho.regatilhar(base, None))["decisao"] == "pessoa:canal_fora_do_auto"


# ── Os pontos de chamada que nenhum teste via ──


async def test_rev2_e2e_o_pedido_lido_por_ferramenta_usa_a_plataforma_da_conversa(
    db: AsyncSession, make_user, agente_ligado, api, envios, monkeypatch
):
    # Sem a plataforma em `gatilhos_de_outro_pedido`, o outro pedido abriria `alterar_endereco`.
    conversa = await _endereco_e2e(
        db, make_user, canal="pos_venda", pedido="2000001", fala="Quero alterar o endereço de entrega"
    )
    real = agente_ctx.ContextoAgente.da_conversa

    def _com_outro_pedido(*a: Any, **k: Any) -> Any:
        ctx = real(*a, **k)
        ctx.pedidos_lidos.append((dict(PEDIDO_NAO_POSTADO), acao_pessoa.POSTADO_NAO))
        return ctx

    monkeypatch.setattr(agente_ctx.ContextoAgente, "da_conversa", staticmethod(_com_outro_pedido))
    api.roteiro = [_final(texto=FIXA_ENDERECO, assunto="endereco", confianca=0.9)]

    r = await ia.gerar_rascunho(db, conversa)

    assert _do_agente(r)["acoes_pessoa"] == [] and r.texto == FIXA_ENDERECO, r.motivo
    assert [e["texto"] for e in envios] == [FIXA_ENDERECO]


async def test_rev2_a_regra_suave_do_agente_recebe_a_plataforma_da_conversa():
    # `checagens.conferir` passa a plataforma: sem ela, o vão não vale na regra suave.
    msgs = _cli("Esta mala chega amanhã?")
    p = Proposta(
        acao="responder", texto="Não temos como garantir o horário da entrega amanhã.",
        assunto="prazo_antes_compra", confianca=0.9, acao_pessoa=None, motivo_pessoa="",
        evidencias=["regra:prazo_antes_compra"],
    )  # fmt: skip
    ml = await _auto(p, msgs, conversa=_conv(plataforma="ml", canal="pergunta", pedido_marketplace=None))
    assert checagens.PRAZO_SEM_LASTRO in ml.codigos
    shopee = await _auto(p, msgs, conversa=_conv(pedido_marketplace=None))
    assert checagens.PRAZO_SEM_LASTRO not in shopee.codigos


async def test_rev2_o_envio_que_falhou_nao_conta_como_frase_recebida(
    db: AsyncSession, make_user, recusa_ativa
):
    conversa = await _atacado_e2e(db, make_user, "É o modelo M2 roxo")
    m = await _msg_ia(db, conversa, FRASE, autor="loja", origem="externo", ha=timedelta(days=1))
    agora = datetime.now(UTC)
    assert await acao_pessoa._atacado_ja_respondido(db, conversa, agora)
    m.status = constantes.MSG_FALHOU
    await db.commit()
    assert not await acao_pessoa._atacado_ja_respondido(db, conversa, agora)


async def test_rev2_o_replay_decide_o_seguimento_no_relogio_do_as_of(
    db: AsyncSession, make_user, recusa_ativa, ajustes
):
    # A frase saiu DEPOIS da fala: no relógio dela ainda não aconteceu. No de agora, seria a volta.
    conversa = await _atacado_e2e(db, make_user, "consegue um valor melhor pra 20UN?")
    gatilho = await db.scalar(
        select(AtendimentoMensagem).where(AtendimentoMensagem.conversa_id == conversa.id)
    )
    await _msg_ia(db, conversa, FRASE, autor="loja", origem="externo", ha=timedelta(minutes=5))
    falsa = _ApiDoReplay([])

    async with replay.sessao_replay() as s:
        c = await s.get(AtendimentoConversa, conversa.id)
        r = await replay.rodar_gatilho(
            s, c, _momento(gatilho), gravar=False, cliente=falsa.cliente()
        )

    assert falsa.pedidos == [] and r.texto_final == FRASE


# ═══════════════ Terceira revisão: as correções da segunda ═══════════════

PROMESSAS_DA_REV3 = [
    # O dia com "na" e qualquer verbo: a mesma promessa de "garantimos a chegada amanhã".
    "Podemos garantir a entrega na sexta.",
    "Consigo garantir a chegada na sexta.",
    "Dá para garantir a entrega na sexta.",
    "Confirmamos a entrega na sexta.",
    "Asseguramos a entrega na sexta.",
    "Pode contar com a entrega na sexta.",
    "A entrega na sexta é certa.",
    "Sim, a entrega na sexta.",
    "Aguarde a entrega na sexta.",
    "A transportadora entrega na sexta.",
    "A previsão é sexta.",
    "Previsto para sexta.",
    "Sexta ele chega.",
    "Sim, sexta.",
    # A lista de dias só nega colada ao prazo; solta, afirma.
    "Não temos como garantir a chegada amanhã. A previsão é sexta ou sábado.",
    "Não temos como garantir a chegada amanhã, só sexta ou sábado.",
    "Não temos como garantir a chegada amanhã, só na sexta ou no sábado.",
    "Não temos como garantir a chegada amanhã, mas deve ser sexta ou sábado.",
    "Não temos como garantir a chegada amanhã, mas sexta ou sábado com certeza.",
    "Não temos como garantir a chegada amanhã. Será ou sexta ou sábado.",
    "Não temos entrega para hoje. A previsão é sexta ou sábado.",
    # A compra não isenta o que vem depois dela, e só o "hoje" é o dia da compra.
    "Não temos como garantir a chegada amanhã. Sua compra vem na sexta.",
    "Não temos como garantir a chegada amanhã. Sua compra está prevista para sexta.",
    "Não temos como garantir a chegada amanhã. Comprando hoje vem na sexta.",
    "Não temos como garantir a chegada amanhã. A previsão do anúncio é sexta.",
    # O que parece negativa do fato e não nega.
    "A entrega para amanhã depende só de você comprar agora.",
    "Sobre a entrega para amanhã, não temos como garantir o horário.",
    "A entrega para amanhã não está garantida, mas é bem provável.",
    # O "mas" que desfaz a negativa, com qualquer palavra.
    "Não temos como confirmar se chega amanhã, mas tudo indica que sim.",
    "Não temos como garantir a chegada amanhã, mas é bem provável.",
    "Não temos como garantir a chegada amanhã, mas vai chegar.",
    "Não temos como garantir a chegada amanhã, mas chegará.",
    "Não temos como garantir a chegada amanhã, porém chega.",
    "Não temos como garantir a chegada amanhã, mas pode ficar tranquilo.",
    # O "e" antes da chegada é a conjunção.
    "Não temos como garantir a data e será entregue amanhã.",
    "Não temos como garantir o prazo e será entregue amanhã.",
    "Não temos como garantir a data e entrega amanhã.",
    # A promessa sem data não depende da pontuação.
    "Chega, pode comprar.",
    "Chega, sim.",
    # A negativa que termina em afirmação sem "mas".
    "Não temos como garantir que a air fryer chegue amanhã, ela chega.",
    # A oração de dentro afirma.
    "Não temos como garantir a chegada do pedido que sai hoje.",
    # O validador tira a "garantia de entrega" da conta: a regra suave é quem vê.
    "Não temos garantia de entrega amanhã, mas chega.",
    "Não temos garantia de entrega amanhã, só na sexta.",
    # Fora de qualquer lista de verbos.
    "Pode esperar na sexta.",
    "Vai na sexta.",
    "Sexta-feira ele estará aí.",
    "Entrega garantida na sexta.",
]


@pytest.mark.parametrize("texto", PROMESSAS_DA_REV3)
def test_rev3_a_promessa_nao_passa_nas_camadas(texto):
    assert not _isenta(texto), texto


NEGATIVAS_DA_REV3 = [
    # O vão fala da entrega com palavras e produtos que nenhuma lista fecha.
    "Não consigo confirmar daqui se chega amanhã.",
    "Não conseguimos confirmar aqui se o pedido sai hoje.",
    "Não temos como garantir que a air fryer chegue amanhã.",
    "Não temos como garantir a chegada da sua air fryer amanhã.",
    "Não temos como garantir a chegada do roteador amanhã.",
    "Não temos como garantir a chegada do Uranyx amanhã.",
    "Não temos como garantir que chegue exatamente amanhã.",
    "Não temos como garantir a chegada na sua residência amanhã.",
    "Não consigo confirmar daqui se sai hoje nem se chega até a data que você precisa.",
    # A negativa real de a7f1e4b8 e as formas com o substantivo.
    "Não temos acesso ao transporte, então não conseguimos garantir se a nova tentativa será hoje.",
    "Não temos como garantir uma data exata de entrega.",
    "Não conseguimos garantir o dia exato da chegada.",
    "Não temos como garantir um prazo exato.",
    "Não conseguimos garantir uma data específica.",
    "Não garantimos data exata de entrega.",
    "Não conseguimos garantir que ele chegue a tempo.",
    "Não temos garantia de entrega amanhã.",
    "Não há garantia de entrega amanhã.",
    "Não é garantido que chegue amanhã.",
    # A orientação depois do "mas" e o outro "chega".
    "Não temos como garantir a chegada amanhã, mas veja quando chega no anúncio.",
    "Não temos como garantir a chegada amanhã, mas confira se chega no anúncio ao informar o CEP.",
    "Não temos como garantir a chegada amanhã, mas veja se chega a tempo no anúncio ao informar o CEP.",
    "Não temos como informar o dia em que chega.",
    "Não sabemos o dia em que chega.",
    # O pagamento e a compra confirmados, em qualquer ordem.
    "Sua compra foi aprovada e está confirmada. Não temos como garantir a chegada amanhã.",
    "O pagamento foi aprovado e está confirmado, mas não temos como garantir a chegada amanhã.",
    "Não temos como garantir a chegada amanhã. O pagamento foi aprovado e está confirmado.",
    # O dia colado ao substantivo é a mesma negativa do "para".
    "A chegada amanhã não pode ser garantida.",
    "A entrega amanhã não é garantida.",
    "A entrega amanhã não está garantida.",
    "A chegada amanhã não é garantida.",
    "Sobre a chegada amanhã, não temos como garantir.",
    "Quanto à entrega amanhã, não temos como garantir.",
    "Sobre a entrega para amanhã, não temos como garantir.",
    "Quanto à entrega para amanhã, não temos como garantir.",
    "Não temos como antecipar a entrega para amanhã.",
    "Não temos como agendar a entrega para amanhã.",
    "Para amanhã, não temos como garantir a entrega.",
    "Amanhã não temos como garantir.",
    # O dia que também não vale.
    "Não temos como garantir a chegada amanhã, nem antes de sexta.",
    "Não temos como garantir a chegada amanhã, nem até domingo.",
    "Não temos como garantir a chegada amanhã, a transportadora não entrega sábado nem domingo.",
    "Não temos como garantir a chegada amanhã, porque hoje é feriado.",
    # A entrega no dia, sem promessa.
    "A entrega no sábado depende da rotina da transportadora.",
    "A transportadora não entrega no sábado.",
    "Não podemos garantir a entrega na sexta.",
    "A tentativa de entrega na segunda tentativa depende da transportadora.",
    # O dia que não é prazo.
    "O pedido foi postado na sexta.",
    "As medidas estão na segunda foto do anúncio.",
]


@pytest.mark.parametrize("texto", NEGATIVAS_DA_REV3)
async def test_rev3_a_negativa_sai_por_todas_as_camadas(db: AsyncSession, make_user, texto):
    # A 1ª camada inteira, e não só as funções do prazo: a garantia e a recusa também barravam.
    p = Proposta(
        acao="responder", texto=texto, assunto="prazo_antes_compra", confianca=0.9,
        acao_pessoa=None, motivo_pessoa="", evidencias=["regra:prazo_antes_compra"],
    )  # fmt: skip
    conv = _conv(plataforma="ml", canal="pergunta", pedido_marketplace=None)
    x = await _auto(p, _cli("chega amanhã?"), conversa=conv)
    assert x.motivos == [], x.motivos
    assert checagens.PRAZO_SEM_LASTRO not in x.codigos
    conversa = await _conversa_ml(db, make_user)
    r = await _rascunho(db, conversa, texto, {"agente": {"isencoes": []}})
    assert await _envio(db, conversa, texto, r) == texto


@pytest.mark.parametrize(
    "texto",
    [
        "Não garantimos o aparelho depois da entrega.",
        "Não garantimos o celular depois do prazo.",
        "Não temos como garantir o produto depois da entrega.",
        "O produto não tem nem garantia depois da entrega.",
        "Produto não garantido depois da entrega.",
        "Não garantimos o aparelho nem um dia.",
        "Não temos garantia depois da entrega.",
        "Não garantimos que a garantia do aparelho seja hoje.",
    ],
)
@pytest.mark.parametrize("canal", ["pergunta", "pos_venda"])
async def test_rev3_a_garantia_do_produto_continua_com_a_pessoa(texto, canal):
    assert validador.sem_a_negativa_da_entrega_no_ml(texto) == texto
    p = Proposta(
        acao="responder", texto=texto, assunto="garantia", confianca=0.9,
        acao_pessoa=None, motivo_pessoa="", evidencias=["regra:garantia"],
    )  # fmt: skip
    conv = _conv(
        plataforma="ml", canal=canal, pedido_marketplace=None if canal == "pergunta" else "1"
    )
    x = await _auto(p, _cli("tem garantia?"), conversa=conv)
    assert ia.MOTIVO_RESPOSTA_DE_GARANTIA in x.motivos or validador.MOTIVO_NEGA_GARANTIA in x.motivos


def test_rev3_o_que_vem_junto_nao_e_prazo():
    assert validador.MOTIVO_PRAZO not in _ml("O carregador chega sim junto com o celular.")
    assert validador.MOTIVO_PRAZO in _ml("Chega sim.")


async def test_rev3_as_outras_plataformas_nao_leem_o_dia_nem_a_negativa_do_ml():
    for plataforma, texto in (
        ("shopee", "Confirmamos a entrega na sexta."),
        ("shopee", "A previsão é sexta."),
        ("tiktok", "Sim, sexta."),
    ):
        final = validador.normalizar(texto, plataforma=plataforma, canal="pergunta")
        assert validador.MOTIVO_PRAZO not in validador.validar(
            final, plataforma=plataforma, canal="pergunta", origem=constantes.ORIGEM_IA
        )
    # O padrão antigo continua sem a saída do ML.
    assert validador.MOTIVO_PRAZO in validador.validar(
        "A chegada amanhã não é garantida.",
        plataforma="shopee", canal="pergunta", origem=constantes.ORIGEM_IA,
    )  # fmt: skip


# ── Regra 2: o pedido com outras palavras e o que não é pedido ──


@pytest.mark.parametrize(
    ("fala", "texto", "canal"),
    [
        ("Quero mudar o local de entrega depois de comprar", "Só é possível antes do envio. Fale com a loja pelas mensagens da compra.", "pergunta"),
        ("Pode alterar a rua depois da compra?", "Sim, antes do envio é só falar com a loja.", "pergunta"),
        ("Se eu comprar pode trocar o local depois?", "Sim, antes do envio é só falar com a loja.", "pergunta"),
        ("Posso receber na casa da minha mãe depois de comprar?", "Sim, é só nos avisar antes do envio.", "pergunta"),
        ("Pode alterar a rua?", "Sim, antes do envio.", "pos_venda"),
        ("Tem como acrescentar o complemento?", "Sim, vou incluir o complemento no pedido.", "pos_venda"),
        ("Tem como acrescentar o complemento?", "Claro, pode me mandar o complemento por aqui.", "pos_venda"),
        ("Troca o local de entrega", "Feito, local de entrega trocado.", "pos_venda"),
        ("O endereço certo é outro", "Sem problema, fale com a loja pelas mensagens da compra.", "pos_venda"),
        ("Meu endereço correto é outro", "Pode nos enviar o correto, por favor?", "pos_venda"),
        # As falas reais: 91e36877, dc4d717c e c7254e1c.
        ("Rua do Pedido quadra 13 lote 07 Cep 70000000", "Endereço anotado, obrigado!", "pos_venda"),
        ("Esse endereço não é mais válido", "Certo, obrigado por avisar.", "pos_venda"),
        ("Complemento de endereço", "Pode nos enviar o complemento, por favor?", "pos_venda"),
    ],
)
def test_rev3_o_pedido_com_outros_nomes_so_recebe_o_texto_fixo(fala, texto, canal):
    msgs = _cli(fala)
    assert acao_pessoa.pede_alterar_endereco(msgs, msgs[-1], canal=canal)
    p = checagens.endereco_no_ml_da_proposta(_proposta(texto), _ctx(canal=canal), msgs, msgs[-1])
    assert p.texto == FIXA_ENDERECO


@pytest.mark.parametrize(
    ("fala", "texto", "assunto", "canal"),
    [
        ("Não quero mudar o endereço, quanto tempo leva?", "O prazo aparece no anúncio.", "prazo_antes_compra", "pergunta"),
        ("Posso comprar e receber em outro endereço?", "Sim, você escolhe o endereço na hora da compra.", "cobertura_regiao", "pergunta"),
        ("Se eu comprar pode ser entregue em outro endereço?", "Sim, você escolhe na compra.", "cobertura_regiao", "pergunta"),
        ("Tá dando erro no meu cep", "Tente informar de novo no anúncio.", "prazo_antes_compra", "pergunta"),
        ("Deu erro no CEP", "Tente informar de novo no anúncio.", "prazo_antes_compra", "pergunta"),
        ("Mudando o CEP muda o frete?", "O frete aparece no anúncio.", "prazo_antes_compra", "pergunta"),
        ("Se eu trocar de cep o frete muda?", "O frete aparece no anúncio.", "prazo_antes_compra", "pergunta"),
        ("se o endereço for diferente muda o frete?", "O frete aparece no anúncio.", "prazo_antes_compra", "pergunta"),
        ("se eu mudar o cep o frete muda?", "O frete aparece no anúncio.", "prazo_antes_compra", "pergunta"),
        ("Vocês mudaram de endereço?", "Não temos loja física.", "fatos_loja", "pergunta"),
        ("Tem outro endereço para devolver?", "A devolução é pelo Mercado Livre.", "troca_devolucao", "pergunta"),
        ("Vai chegar amanhã? Dia 10 estarei em outro endereço", "O prazo aparece no anúncio.", "prazo_antes_compra", "pergunta"),
        ("Não mudei o endereço, por que não chegou?", "Acompanhe em Minhas compras.", "rastreio", "pos_venda"),
        ("Não tenho interesse em trocar o endereço, só quero a nota", "A nota vai em Minhas compras.", "nf", "pos_venda"),
        ("mandaram o produto errado no meu endereço", "Sentimos muito pelo ocorrido.", "item_errado_faltando", "pos_venda"),
        ("chegou produto errado no meu endereço", "Sentimos muito pelo ocorrido.", "item_errado_faltando", "pos_venda"),
        ("quero trocar a mala qual o endereço para devolver", "A devolução é pelo Mercado Livre.", "troca_devolucao", "pos_venda"),
        ("preciso trocar o produto qual o endereço", "A devolução é pelo Mercado Livre.", "troca_devolucao", "pos_venda"),
        ("apareceu endereço incorreto no rastreio, o que faço?", "Acompanhe em Minhas compras.", "rastreio", "pos_venda"),
        # O endereço da NF de 9f3dd292.
        ("Somente endereço, cidade e telefone estão errados", "A nota vai em Minhas compras.", "nf", "pos_venda"),
    ],
)
def test_rev3_o_que_nao_pede_a_mudanca_mantem_a_resposta_do_modelo(fala, texto, assunto, canal):
    msgs = _cli(fala)
    assert not acao_pessoa.pede_alterar_endereco(msgs, msgs[-1], canal=canal)
    p = _proposta(texto, assunto=assunto)
    assert checagens.endereco_no_ml_da_proposta(p, _ctx(canal=canal), msgs, msgs[-1]) == p


async def test_rev3_e2e_a_resposta_certa_nao_vira_a_frase_na_pergunta_publica(
    db: AsyncSession, make_user, agente_ligado, api, envios
):
    fala = "Não quero mudar o endereço, quanto tempo leva?"
    conversa = await _endereco_e2e(db, make_user, canal="pergunta", pedido=None, fala=fala)
    texto = "O prazo e o frete aparecem no anúncio ao informar o CEP."
    api.roteiro = [_final(texto=texto, assunto="prazo_antes_compra", confianca=0.9)]

    r = await ia.gerar_rascunho(db, conversa)

    assert r.texto == texto and FIXA_ENDERECO != r.texto


@pytest.mark.parametrize(
    "fala",
    [
        "estou indo para outra cidade, quero mudar o endereço de entrega",
        "vou me mudar, estou indo para outra cidade, pode alterar o endereço?",
        "estou indo pra outro estado, tem como trocar o endereço?",
        "a compra foi com o endereço errado, tem como mudar?",
        "foi o endereço errado que coloquei, pode alterar?",
        "o pedido foi com o cep errado, consegue corrigir?",
        "coloquei errado, foi para outro endereço na compra, tem como alterar?",
    ],
)
def test_rev3_o_foi_e_o_indo_nao_seguram_o_pedido_de_mudanca(fala):
    # Sem o pedido na mesma fala, são a entrega: `test_rev2_e2e_a_entrega_errada_vai_para_a_pessoa`.
    msgs = _cli(fala)
    assert not acao_pessoa.entrega_relatada_no_turno(msgs, msgs[-1])
    p = _proposta(FIXA_ENDERECO, assunto="endereco")
    saida = checagens.endereco_no_ml_da_proposta(p, _ctx(), msgs, msgs[-1])
    assert saida.acao == "responder" and saida.texto == FIXA_ENDERECO
    assert _gatilhos(_conv(plataforma="ml"), msgs, postado=acao_pessoa.POSTADO_SIM) == []


@pytest.mark.parametrize(
    ("fala", "canal"),
    [
        # A palavra do lugar em outra conversa: nada de frase fixa em público.
        ("Aceita troca no local?", "pergunta"),
        ("Onde coloco o número de série?", "pos_venda"),
        ("Envia para a rua das Flores, cep 70000000?", "pergunta"),
        ("O frete muda se eu trocar o CEP?", "pergunta"),
        ("Não tem problema se não conseguiu incluir o número do apartamento.", "pos_venda"),
        ("Vai chegar amanhã? Dia 10 estarei em outro endereço", "pos_venda"),
    ],
)
def test_rev3_a_palavra_do_lugar_nao_e_o_pedido(fala, canal):
    msgs = _cli(fala)
    assert not acao_pessoa.pede_alterar_endereco(msgs, msgs[-1], canal=canal)


@pytest.mark.parametrize(
    "fala",
    [
        "o carteiro deixou outro aviso",
        "levou outra semana pra chegar",
        "o correio entregou outro dia e veio com defeito",
        "meu pedido foi entregue e quero comprar outra unidade",
        "foi entregue hoje, qual o código para outra compra?",
        "entregaram o produto errado",
        "entregaram a cor errada",
        "foi entregue, mas o carregador está errado",
        "Entregaram o modelo errado",
        "Entregaram outro produto",
        "Entregaram outro dia",
        "O produto entregue está errado",
    ],
)
def test_rev3_o_lugar_errado_e_o_lugar(fala):
    assert not acao_pessoa._entrega_errada_no_ml(validador.plano_de(fala))


@pytest.mark.parametrize(
    "texto",
    [
        "A plataforma não permite alterar o endereço depois do envio.",
        "Depois da postagem, o endereço não pode ser alterado.",
        "Depois do envio, a plataforma não permite alterar o endereço.",
        "Após o envio, não é possível alterar o endereço.",
        "A entrega segue o endereço do pedido, e a plataforma não permite alterar o endereço depois da postagem.",
    ],
)
def test_rev3_o_que_o_bloco_1_ensina_em_qualquer_ordem_nao_segura_outro_assunto(texto):
    assert checagens.conferir_endereco_no_ml(
        texto, acao="responder", assunto="entrega_instrucoes", plataforma="ml"
    ) == []


def test_rev3_o_so_depois_do_envio_continua_na_trava():
    (trava,) = checagens.conferir_endereco_no_ml(
        "Só depois da postagem é possível alterar o endereço.",
        acao="responder", assunto="entrega_instrucoes", plataforma="ml",
    )  # fmt: skip
    assert trava.codigo == checagens.ENDERECO_NO_ML


# ── Regra 3: a leitura dos lotes e a gramática do pedido ──


@pytest.mark.parametrize(
    ("fala", "quantidade"),
    [
        # Lotes somam, com ou sem a palavra que liga.
        ("Quero 40 e 40 malas, qual o valor?", 80),
        ("Quero 15 e 15 unidades, qual o valor?", 30),
        ("Quero 100 e 20 unidades, qual o valor?", 120),
        ("Quero 40 malas. Quero 40 frasqueiras. Qual o valor?", 80),
        ("Quero 40 malas 40 frasqueiras, qual o valor?", 80),
        ("Quero 40 malas e 40 frasqueiras, qual o valor?", 80),
        # O detalhe do total não soma: abaixo do mínimo de verdade.
        ("Quero 10 unidades, 5 malas e 5 frasqueiras, qual o valor?", 10),
        ("Quero 10 malas, 5 unidades e 5 unidades, qual valor?", 10),
        ("Quero 5 malas, 5 frasqueiras e 10 unidades, qual valor?", 10),
        ("Quero 8 malas, 4 delas grandes e 4 delas pequenas, qual o valor?", 8),
        ("Quero 10 malas, 5 delas P e 5 delas G, qual o valor?", 10),
        # A faixa e o "ou" continuam valendo o maior.
        ("Quero entre 20 e 30 malas, qual o valor?", 30),
        ("Quero 20 malas ou 30 malas, qual o valor?", 30),
    ],
)
def test_rev3_a_leitura_dos_lotes(fala, quantidade):
    assert acao_pessoa.quantidade_de_atacado(validador.plano_de(fala)) == quantidade


@pytest.mark.parametrize(
    ("fala", "estoque"),
    [
        ("Quero 40 e 40 malas, qual o valor?", 50),
        ("Quero 15 e 15 unidades, qual o valor?", 20),
        ("Quero 100 e 20 unidades, qual o valor?", 110),
        ("Quero 40 malas. Quero 40 frasqueiras. Qual o valor?", 50),
        ("Quero 40 malas 40 frasqueiras, qual o valor?", 50),
    ],
)
async def test_rev3_e2e_os_lotes_acima_do_estoque_vao_para_a_pessoa(
    db: AsyncSession, make_user, agente_ligado, api, envios, recusa_ativa, fala, estoque
):
    conversa = await _atacado_e2e(db, make_user, fala, estoque=estoque)
    api.roteiro = []

    r = await ia.gerar_rascunho(db, conversa)

    assert r is not None and FRASE != r.texto and r.precisa_humano and envios == []


@pytest.mark.parametrize(
    "fala",
    [
        "Quero 10 unidades, 5 malas e 5 frasqueiras, qual o valor?",
        "Quero 10 malas, 5 unidades e 5 unidades, qual valor?",
        "É preço para 20 malas?",
        "É valor por 20 unidades?",
        "É desconto de 20 unidades?",
        "É atacado para 20 unidades?",
        "Quanto ficou 20 malas?",
        "Por quanto saiu 20 malas?",
        "Quanto saiu as 20 unidades?",
        "Oi é preço para 20 malas?",
    ],
)
async def test_rev3_e2e_o_que_nao_e_o_pedido_nao_recebe_a_frase(
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
    "fala",
    [
        "Consegue um valor melhor pra 20UN? Grato",
        "Consegue um valor melhor pra 20UN, por gentileza?",
        "Consegue um valor melhor pra 20UN? Aguardo",
        "Amigo, quanto fica 20 unidades?",
        "Tem desconto na compra de 20 unidades?",
        "melhor preço para 20 unidades",
        "vc dá desconto pra 20 unidades?",
        "quanto de desconto para 20 unidades?",
        "vende no atacado? preciso de 50 unidades",
        "tem preço especial para 20 unidades?",
        "melhora o preço pra 20 unidades?",
        "Tenho interesse em 20 unidades, qual o valor?",
        "quero fazer um pedido de 30 unidades, qual o valor?",
        "preço unitário para 20 unidades?",
        "estou precisando de 20 unidades, qual o valor?",
    ],
)
async def test_rev3_e2e_o_pedido_natural_recebe_a_frase(
    db: AsyncSession, make_user, agente_ligado, api, envios, recusa_ativa, fala
):
    conversa = await _atacado_e2e(db, make_user, fala)
    api.roteiro = []

    r = await ia.gerar_rascunho(db, conversa)

    assert r is not None and r.texto == FRASE and api.pedidos == []


@pytest.mark.parametrize(
    "fala",
    [
        "Fiz um pedido de 20 unidades, qual o valor?",
        "Meu pedido de 20 unidades, qual o valor?",
        "Valor das 20 unidades da minha compra",
    ],
)
def test_rev3_a_compra_feita_nao_e_o_pedido(fala):
    pedido = acao_pessoa._pedido_de_atacado(validador.plano_de(fala))
    assert pedido is None or pedido[1]


@pytest.mark.parametrize(
    "fala",
    [
        "consegue um valor melhor pra 20UN desse anúncio?",
        "Tem desconto pra 20 unidades nesse modelo?",
        "consegue um valor melhor pra 20UN nessa mala?",
        "consegue um valor melhor pra 20UN nesse produto?",
        "consegue um valor melhor pra 20UN nesse anúncio?",
        "consegue um valor melhor pra 20UN neste item?",
        "consegue um valor melhor pra 20UN iguais?",
        "consegue um valor melhor pra 20UN da mesma?",
        "20 unidades iguais, qual o valor?",
    ],
)
def test_rev3_apontar_o_anuncio_nao_nomeia_a_variacao(fala):
    assert acao_pessoa._pedido_de_atacado(validador.plano_de(fala)) == (20, False)

