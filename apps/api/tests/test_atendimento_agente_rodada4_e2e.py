# ruff: noqa: F811 — fixtures importadas do e2e
"""Rodada 4 (21/10/2026) pelo `ia.gerar_rascunho` de verdade: o contrato novo da
camada determinística ponta a ponta.

As respostas BOAS que a lente utilidade da re-revisão final viu indo para
pessoa ou bloqueadas (achados 6 a 16) saem sozinhas no AUTOMÁTICO — com o
assunto liberado e o verificador dando "ok" (a `Api` falsa do e2e) — e ficam
limpas no COPILOTO (pendentes, sem pessoa, sem aviso). As regressões (achados
0, 1, 2, 19 e 22) não saem sozinhas; no copiloto a sugestão fica (nunca
bloqueada), com o aviso guardado em `fatos.agente.avisos` (que a tela mostra)
e o motivo do "não sai sozinho" em `fatos.agente.sem_auto`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import BlingOrder, Logistica
from app.services.atendimento import ia
from app.services.atendimento.agente import checagens
from app.services.atendimento.constantes import (
    ASSUNTOS_ORIENTACAO,
    MODO_AUTO,
    MODO_COPILOTO,
    RASCUNHO_BLOQUEADO,
    RASCUNHO_PENDENTE,
)
from tests.test_atendimento_agente_e2e import (  # noqa: F401 — fixtures
    ITEM_A18,
    ITEM_A18_HOJE,
    NAO_DO_VERIFICADOR,
    _aviso_no_verificador,
    _decisao,
    _do_agente,
    _do_verificador,
    _final,
    _kia,
    _ligar,
    _pedido_uranyx,
    _sem_a_regua_dos_dias,
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
from tests.test_atendimento_ia import (
    _conversa,
    _importar_manual_base,
    _msg,
    _pedido_completo,
)

ASSUNTOS = [
    "prazo_envio",
    "rastreio",
    "cobertura_regiao",
    "outro",
    "pede_atendente",
    "confirmar_variacao",
    *ASSUNTOS_ORIENTACAO,
]
VA = [("ver_anuncio", {"item_id": ITEM_A18})]
RESTR = f"restricao:{ITEM_A18}"
P = ["pedido"]


async def _montar(db: AsyncSession, make_user, redis_mem, lojas, setup: str):
    if setup in ("kia", "kia_rj"):
        descricao = None
        if setup == "kia_rj":
            descricao = ITEM_A18_HOJE["description"] + "\nNão enviamos para o Rio de Janeiro.\n"
        conversa, _ = await _kia(db, make_user, redis_mem, lojas, descricao=descricao)
        return conversa
    if setup == "uranyx_entregue":
        return await _pedido_uranyx(db, make_user, entregue_em=datetime(2026, 9, 1, 15, tzinfo=UTC))
    await _importar_manual_base(db)
    await _pedido_completo(db)
    if setup == "nao_postado":
        await db.execute(update(BlingOrder).values(situacao="6"))
        await db.execute(update(Logistica).values(postagem_data=None, localizacao=None))
    elif setup == "cancelado":
        await db.execute(update(BlingOrder).values(situacao="12"))
        await db.execute(update(Logistica).values(postagem_data=None, localizacao=None))
    elif setup == "entregue":
        await db.execute(
            update(Logistica).values(
                entregue_em=datetime(2026, 9, 29, 14, tzinfo=UTC),
                localizacao="Objeto entregue ao destinatário",
            )
        )
    await db.commit()
    return await _conversa(db, make_user)


async def _gerar(db, make_user, redis_mem, lojas, api, modo, caso):
    _id, setup, clientes, ferramentas, texto, assunto, evidencias = caso
    conversa = await _montar(db, make_user, redis_mem, lojas, setup)
    await _ligar(
        db,
        conversa,
        modo=modo,
        auto_categorias=ASSUNTOS,
        auto_orientacao=list(ASSUNTOS_ORIENTACAO),
        auto_agente=ASSUNTOS,
    )
    clientes = [clientes] if isinstance(clientes, str) else clientes
    for i, t in enumerate(clientes):
        await _msg(db, conversa, t, ha=timedelta(minutes=5 + len(clientes) - i))
    api.roteiro = [
        *(_usa(n, **a) for n, a in ferramentas),
        _final(texto=texto, assunto=assunto, evidencias=evidencias),
    ]
    return await ia.gerar_rascunho(db, conversa)


# (id do achado, setup, cliente, ferramentas, texto, assunto, evidências)
BOAS = [
    (
        "6-B03",
        "postado",
        "vcs entregam no sábado?",
        [],
        "Olá! A entrega no sábado depende da rotina da transportadora. A previsão do seu pedido "
        "aparece em Minhas Compras, no pedido.",
        "rastreio",
        P,
    ),
    (
        "7-R01",
        "entregue",
        "vocês fazem o reembolso?",
        [],
        "Olá! O reembolso não é feito pela loja: você solicita a devolução em Minhas Compras, no "
        "pedido, e a Shopee analisa e devolve o valor pela mesma forma de pagamento.",
        "reembolso",
        P,
    ),
    (
        "7-R03",
        "cancelado",
        "o reembolso já caiu?",
        [],
        "Olá! O reembolso não cai na hora: ele é feito pela Shopee, pela mesma forma de "
        "pagamento, no prazo definido por ela. Você acompanha em Minhas Compras, no pedido.",
        "reembolso",
        P,
    ),
    (
        "8-D04",
        "uranyx_entregue",
        ["o celular não liga mais", "posso mandar o vídeo por aqui?"],
        [],
        "Pode sim! Pode enviar.",
        "defeito",
        [],
    ),
    (
        "9-N11",
        "nao_postado",
        "quando posta?",
        [],
        "Olá! O seu pedido aguarda a postagem, que é feita dentro do prazo de envio informado no "
        "anúncio. Quando ele for postado, o rastreio aparece em Minhas Compras.",
        "prazo_envio",
        P,
    ),
    (
        "9-B09",
        "postado",
        "não entendi o rastreio",
        [],
        "Olá! O rastreio mostra cada etapa do caminho do pedido. O último status do seu é Objeto "
        "em trânsito, ou seja, ele está sendo transportado.",
        "rastreio",
        P,
    ),
    (
        "10-E01",
        "entregue",
        "não gostei, quero devolver",
        [],
        "Olá! Sinto muito que o produto não tenha agradado. Você pode solicitar a devolução pelo "
        "app, em Minhas Compras, no pedido; a Shopee analisa e faz o reembolso pela mesma forma "
        "de pagamento.",
        "troca_devolucao",
        P,
    ),
    (
        "10-R11",
        "entregue",
        "a Shopee que devolve o dinheiro ou vocês?",
        [],
        "Olá! Quem faz o reembolso é a Shopee, pela mesma forma de pagamento da compra, depois "
        "que você solicita a devolução em Minhas Compras.",
        "reembolso",
        P,
    ),
    (
        "11-E02",
        "entregue",
        "quero trocar por uma maior",
        [],
        "Olá! A troca é feita pela devolução do próprio pedido: em Minhas Compras, abra o pedido "
        "e solicite a devolução. Depois, é só comprar o tamanho que preferir no anúncio.",
        "troca_devolucao",
        P,
    ),
    (
        "12-N15",
        "nao_postado",
        "consegue mandar hoje?",
        [],
        "Olá! Não conseguimos confirmar se o pedido sai hoje. Ele é postado dentro do prazo de "
        "envio do anúncio, e o rastreio aparece em Minhas Compras quando for enviado.",
        "prazo_envio",
        P,
    ),
    (
        "12-B05",
        "postado",
        "chega até sexta?",
        [],
        "Olá! Não conseguimos garantir a entrega até sexta. A previsão do seu pedido é "
        "{previsao_entrega}, e você acompanha em Minhas Compras.",
        "rastreio",
        P,
    ),
    (
        "13-A02",
        "postado",
        "tem alguém aí?",
        [],
        "Olá! Estou aqui, sim. Como posso ajudar?",
        "pede_atendente",
        P,
    ),
    (
        "13-D05",
        "entregue",
        ["a alça soltou", "posso mandar foto?"],
        [],
        "Claro, pode enviar por aqui! A equipe analisa e responde por aqui.",
        "defeito",
        [],
    ),
    (
        "14-U04",
        "uranyx_entregue",
        "o touch tá falhando, vocês trocam?",
        [],
        "Sinto muito! Para a equipe analisar, envie por aqui um vídeo curto mostrando o touch "
        "falhando e fotos do aparelho. A equipe responde por aqui, e troca, devolução ou "
        "reembolso seguem depois da análise.",
        "defeito",
        [],
    ),
    (
        "14-D07",
        "uranyx_entregue",
        "o touch tá falhando, vocês trocam?",
        [],
        "Sinto muito! Para a equipe analisar, envie por aqui um vídeo curto mostrando o touch "
        "falhando e fotos do aparelho. A equipe responde por aqui, e troca, devolução ou "
        "reembolso seguem depois da análise.",
        "troca_devolucao",
        [],
    ),
    (
        "15-U05",
        "uranyx_entregue",
        "o nfc não funciona",
        [],
        "Sinto muito! Envie por aqui um vídeo curto mostrando a tentativa de uso do NFC e fotos "
        "do aparelho, sem dados pessoais na tela, que a equipe analisa e responde por aqui.",
        "defeito",
        [],
    ),
    (
        "16-P09",
        "postado",
        "a mala que comprei é a preta né?",
        [],
        "Olá! Sim, o seu pedido é da Mala de bordo ABS 10kg preta, junto com 2 Cadeados TSA.",
        "confirmar_variacao",
        P,
    ),
    (
        "16-N07",
        "nao_postado",
        "a mala é a preta, né?",
        [],
        "Olá! Sim, o seu pedido é da Mala de bordo ABS 10kg preta, com 2 Cadeados TSA.",
        "confirmar_variacao",
        P,
    ),
]


@pytest.mark.parametrize("caso", BOAS, ids=[c[0] for c in BOAS])
async def test_a_resposta_boa_sai_sozinha_no_automatico(
    db: AsyncSession, make_user, agente_ligado, api, envios, redis_mem, lojas, caso
):
    r = await _gerar(db, make_user, redis_mem, lojas, api, MODO_AUTO, caso)
    agente = _do_agente(r)
    assert r.status == RASCUNHO_PENDENTE and not r.precisa_humano, (caso[0], r.motivo)
    assert agente["sem_auto"] == [] and agente["avisos"] == [], agente
    assert envios and _decisao(r) == "enviado", (caso[0], _decisao(r))


# A régua de ação de pessoa do CÓDIGO continua dura. Até 21/10/2026, "tem alguém
# aí?" no copiloto (sem a IA como atendente da loja) era "insistiu em falar com
# pessoa"; com a decisão do dono (o agente é o atendente da loja, em qualquer
# modo), o 1º pedido é respondido — e fica limpo no copiloto também.
CODIGO_NO_COPILOTO: set[str] = set()


@pytest.mark.parametrize("caso", BOAS, ids=[c[0] for c in BOAS])
async def test_a_resposta_boa_fica_limpa_no_copiloto(
    db: AsyncSession, make_user, agente_ligado, api, envios, redis_mem, lojas, caso
):
    r = await _gerar(db, make_user, redis_mem, lojas, api, MODO_COPILOTO, caso)
    agente = _do_agente(r)
    assert not envios and r.status == RASCUNHO_PENDENTE, (caso[0], r.motivo)
    assert agente["avisos"] == [], agente["avisos"]
    if caso[0] in CODIGO_NO_COPILOTO:
        assert r.precisa_humano and agente["acoes_pessoa"], r.motivo
        return
    assert not r.precisa_humano and agente["sem_auto"] == [], (caso[0], r.motivo)


RUINS = [
    # achados 0 e 19: a dupla negação/litotes do RJ — PESSOA (a restrição é certa)
    (
        "0-DN01",
        "kia_rj",
        "moro no RJ, entrega?",
        VA,
        "Olá! Nunca deixamos de enviar para o Rio de Janeiro.",
        "cobertura_regiao",
        [RESTR],
    ),
    (
        "19-frete",
        "kia_rj",
        "moro no RJ, entrega?",
        VA,
        "Olá! Infelizmente o frete para o Rio de Janeiro fica um pouco mais caro.",
        "cobertura_regiao",
        [RESTR],
    ),
    # achados 1 e 22: a minimização do defeito — SUAVE
    (
        "1-N01",
        "uranyx_entregue",
        "esquenta muito, é normal?",
        [],
        "Olá! É normal sim, não precisa se preocupar.",
        "defeito",
        [],
    ),
    (
        "22-DS1",
        "uranyx_entregue",
        "o celular esquenta muito",
        [],
        "Olá! Pode ser só a capinha, tire e veja se melhora.",
        "defeito",
        [],
    ),
    # achado 2: "é + advérbio + rápido" à pergunta do produto — SUAVE (a ficha)
    ("2-Y01", "kia", "o celular é rápido?", [], "Olá! Sim, é super rápido!", "outro", []),
    # achado 5: a recusa de direito em rastreio — SUAVE, com o motivo próprio
    (
        "5-X01",
        "postado",
        "cadê meu pedido? se não chegar quero meu dinheiro",
        [],
        "Olá! Seu pedido está em trânsito, e depois de enviado não aceitamos devolução.",
        "rastreio",
        P,
    ),
    # achado 20: o andamento por outro sujeito — SUAVE
    (
        "20-expedicao",
        "nao_postado",
        "quando vocês enviam?",
        [],
        "Olá! A expedição já está cuidando do seu pedido.",
        "prazo_envio",
        P,
    ),
]
PESSOA_NOS_DOIS = {"0-DN01", "19-frete"}
AVISOS = {
    "1-N01": checagens.MOTIVO_DEFEITO_FORA_DA_ORIENTACAO,
    "22-DS1": checagens.MOTIVO_DEFEITO_FORA_DA_ORIENTACAO,
    "2-Y01": ia.MOTIVO_FICHA_NO_AUTOMATICO,
    "5-X01": ia.MOTIVO_RECUSA_DE_DIREITO,
    "20-expedicao": "diz o andamento do pedido sem o dado que o prova",
}


@pytest.mark.parametrize("caso", RUINS, ids=[c[0] for c in RUINS])
async def test_a_regressao_nao_sai_sozinha_mesmo_com_o_ok_do_verificador(
    db: AsyncSession, make_user, agente_ligado, api, envios, redis_mem, lojas, caso
):
    # O portão do dono (27/10/2026): o que não é duro chega ao verificador COM o aviso — e o
    # "não" dele segura; o duro nem chega a ele (não paga a requisição).
    api.vereditos = [_do_verificador(**NAO_DO_VERIFICADOR)]
    r = await _gerar(db, make_user, redis_mem, lojas, api, MODO_AUTO, caso)
    agente = _do_agente(r)
    assert not envios, (caso[0], r.motivo)
    assert r.status == RASCUNHO_PENDENTE and r.precisa_humano, (caso[0], r.status, r.motivo)
    assert agente["sem_auto"], agente
    if agente["verificador"]["rodou"]:
        _aviso_no_verificador(api)
    if caso[0] in AVISOS:
        assert AVISOS[caso[0]] in agente["avisos"], agente["avisos"]


@pytest.mark.parametrize("caso", RUINS, ids=[c[0] for c in RUINS])
async def test_a_regressao_no_copiloto_fica_com_o_aviso_sem_bloquear(
    db: AsyncSession, make_user, agente_ligado, api, envios, redis_mem, lojas, caso
):
    from app.routers.atendimento import _rascunho_out

    r = await _gerar(db, make_user, redis_mem, lojas, api, MODO_COPILOTO, caso)
    agente = _do_agente(r)
    assert r.status == RASCUNHO_PENDENTE and r.status != RASCUNHO_BLOQUEADO, (caso[0], r.motivo)
    # (a ficha no automático só existe no automático: no copiloto o Y01 é limpo)
    assert r.texto and (agente["sem_auto"] or caso[0] == "2-Y01"), agente
    if caso[0] in PESSOA_NOS_DOIS:
        # A contradição quase certa: pessoa também no copiloto.
        assert r.precisa_humano and "RJ" in (r.motivo or ""), r.motivo
    else:
        # A regra suave: a sugestão fica limpa para enviar, com o aviso na tela
        # (a da ficha no automático só existe no automático: no copiloto a
        # pessoa já confere o produto).
        assert not r.precisa_humano, r.motivo
        if caso[0] != "2-Y01":
            assert AVISOS[caso[0]] in agente["avisos"]
            tela = _rascunho_out(r)
            assert tela.agente is not None and AVISOS[caso[0]] in tela.agente.avisos
    assert not envios
