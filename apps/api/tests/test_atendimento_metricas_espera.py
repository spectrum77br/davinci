"""A ESPERA do comprador no tempo de resposta das Métricas (16/11/2026).

O Eduardo pediu o tempo de resposta "calculado certinho": do comprador ESCREVER até alguém da
loja responder DE VERDADE (pessoa ou IA que manda sozinha), ou até o clique "Não precisa de
resposta". A auditoria com os dados de produção do dia achou o relógio errado em cinco lugares,
e cada um tem o teste dele aqui:

1. o cartão do produto/pedido sozinho abria e esticava a espera (46fa04a9: 3.761 min, a IA
   respondeu em 6,6) — agora a espera começa na primeira fala escrita, e a vez só de cartão
   não conta (nem respondida, nem vencida);
2. o "ok/obrigado", o emoji e a figurinha depois da resposta da loja abriam a espera (2b96dbfc:
   5.968 min; a espera de verdade foi de 2,7) — agora não abrem, menos respondendo a uma
   pergunta da loja; "ok mas e o rastreio?" abre;
3. o recibo fixo da IA ("Recebemos o vídeo, obrigado! A equipe analisa e responde por aqui.")
   fechava a espera em 3 min — agora não fecha (nem conta como resposta da IA);
4. (revisto) a espera aberta da conversa fechada ou bloqueada CONTINUA vencida: senão "Fechar"
   apagaria a vencida;
5. avaliação, reclamação, comentário e carrinho entravam como chat — agora só o chat.

As réguas puras são de `metricas_equipe` (`so_cortesia`, `fala_do_comprador`, `esperas`); a
consulta e a conta por loja, de `routers.atendimento._turnos_das_lojas`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import IntegrationPlatform
from app.services.atendimento import constantes, gravar, metricas_equipe
from app.services.atendimento.agente import acao_pessoa
from app.services.atendimento.metricas_equipe import CARTAO, CORTESIA, FALA, Linha
from tests.test_atendimento_ia import _conversa

AGORA = datetime.now(UTC)
RECIBO = "Recebemos a foto, obrigado! A equipe analisa e responde por aqui."
CARTAO_SHOPEE = {"message_type": "item", "content": {"item_id": 123, "shop_id": 9}}
VARIACAO_SHOPEE = {"message_type": "variation_card", "content": {"item_id": 123}}
CARTAO_TIKTOK = {"type": "PRODUCT_CARD", "content": '{"product_id": "77"}'}
FIGURINHA = {
    "message_type": "sticker",
    "content": {"sticker_id": "0012", "sticker_package_id": "x"},
}
# O robô do Duoke (automática pelo texto) e o cartão de pedido que a Shopee põe (pelo payload).
MENU_DUOKE = "Olá, por favor selecione sua dúvida e logo um atendente irá te responder"
CARTAO_DA_LOJA = {"source": "openapi", "message_type": "order", "content": {"order_sn": "X"}}


# ── As réguas puras ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "texto",
    [
        "ok",
        "Ok, obrigado!",
        "obrigada 🙏",
        "Obrigado!!",
        "valeu",
        "Valeu!!",
        "vlw",
        "blz",
        "beleza, obrigado",
        "Tá bom",
        "certo",
        "entendi, obrigada",
        "de nada",
        "Muito obrigado pela atenção",
        "ok 👍",
        "👍",
        "👍🏻",
        "❤️",
        "🙏🙏!",
    ],
)
def test_so_cortesia_reconhece_o_ok_o_obrigado_e_o_emoji(texto):
    assert metricas_equipe.so_cortesia(texto) is True


@pytest.mark.parametrize(
    "texto",
    [
        # Pergunta e pedido derrubam — o caso da auditoria.
        "ok mas e o rastreio?",
        "ok mas e o rastreio",
        "ok?",
        "obrigado, quando chega?",
        "Obrigado, quando chega",
        "👍?",
        "?",
        "alguém?",
        # Cumprimento e espera/cobrança abrem: "oi", "?" e "alguém?" são espera de verdade.
        "Olá",
        "oi",
        "bom dia",
        "boa tarde, obrigado",
        "fico no aguardo",
        "ok, aguardo",
        "Ok aguardando",
        "retorno por favor",
        # Sem a palavra de cortesia ("retorno", "então" sozinhos cobram).
        "retorno",
        "então",
        # "sim" depois de uma pergunta é resposta; risada e o que não se sabe, fala.
        "sim",
        "pode ser",
        "kkk",
        "...",
        "",
        None,
        "Obrigadaaa",
        # Longo demais para ser só cortesia — e o corte da consulta (81 letras) de um longo.
        "obrigado " * 12,
        "obrigado" + " " * 72 + "x",
        "obrigado" + " " * 73,
    ],
)
def test_so_cortesia_nao_aceita_pergunta_cumprimento_nem_cobranca(texto):
    assert metricas_equipe.so_cortesia(texto) is False


def test_fala_do_comprador_cartao_cortesia_e_fala():
    fala = metricas_equipe.fala_do_comprador
    # O cartão sem texto (Shopee item, a variação com o rótulo "[Mensagem]", o TikTok).
    assert fala(tipo="produto", texto=None, payload=CARTAO_SHOPEE) == CARTAO
    assert fala(tipo="outro", texto="[Mensagem]", payload=VARIACAO_SHOPEE) == CARTAO
    assert fala(tipo="produto", texto=None, payload=CARTAO_TIKTOK) == CARTAO
    # Só as chaves do item cru que a consulta traz bastam (a outra vem None).
    assert fala(tipo="pedido", texto=None, payload={"message_type": "order", "type": None}) == (
        CARTAO
    )
    # O cartão COM texto do comprador é fala.
    assert fala(tipo="produto", texto="Tem na cor azul?", payload=CARTAO_SHOPEE) == FALA
    # A figurinha e o texto só de cortesia.
    assert fala(tipo="outro", texto="[Figurinha]", payload=FIGURINHA) == CORTESIA
    assert fala(tipo="figurinha", texto="[Figurinha]") == CORTESIA  # o Zap
    assert fala(tipo="texto", texto="Ok, obrigado!") == CORTESIA
    # Com anexo (a mensagem do ML com foto), foto, vídeo, pergunta: fala.
    assert fala(tipo="texto", texto="ok", tem_anexo=True) == FALA
    assert fala(tipo="imagem", texto=None, tem_anexo=True) == FALA
    assert fala(tipo="video", texto=None, tem_anexo=True) == FALA
    assert fala(tipo="texto", texto="ok mas e o rastreio?") == FALA
    assert fala(tipo="texto", texto="Bom dia") == FALA
    assert fala(tipo="texto", texto=None) == FALA


def _t(minutos: float) -> datetime:
    return AGORA - timedelta(days=1) + timedelta(minutes=minutos)


def _c(minutos: float, fala: str = FALA) -> Linha:
    return Linha(cliente=True, quando=_t(minutos), fala=fala)


def _l(minutos: float, pergunta: bool = False) -> Linha:
    return Linha(cliente=False, quando=_t(minutos), pergunta=pergunta)


def test_esperas_o_cartao_nao_abre_nem_estica():
    esperas = metricas_equipe.esperas
    # Só o cartão, sem resposta: não é espera.
    assert esperas([_c(0, CARTAO)]) == []
    # Só o cartão e a loja respondeu (a saudação): não é tempo de nada.
    assert esperas([_c(0, CARTAO), _l(5)]) == []
    # O cartão, e o comprador só escreve 60 h depois: a espera começa na fala escrita.
    assert esperas([_c(0, CARTAO), _c(3600), _l(3607)]) == [(_t(3600), _t(3607))]
    # O cartão logo antes do texto (o caso comum) também começa no texto.
    assert esperas([_c(0, CARTAO), _c(0.2), _l(10)]) == [(_t(0.2), _t(10))]
    # Escrita primeiro, o cartão depois: a espera já estava aberta e segue.
    assert esperas([_c(0), _c(1, CARTAO), _l(10)]) == [(_t(0), _t(10))]
    # O cartão que RESPONDE a uma pergunta da loja ("qual o pedido?") abre a espera.
    assert esperas([_c(0), _l(10, pergunta=True), _c(12, CARTAO), _l(1212)]) == [
        (_t(0), _t(10)),
        (_t(12), _t(1212)),
    ]


def test_esperas_a_cortesia_nao_abre_e_a_pergunta_de_depois_abre():
    esperas = metricas_equipe.esperas
    # A loja respondeu; "obrigado" e "de nada": nenhuma espera.
    assert esperas([_c(0), _l(10), _c(12, CORTESIA), _l(13)]) == [(_t(0), _t(10))]
    # "ok" e a pergunta nova 4 dias depois: a espera começa na pergunta (2b96dbfc).
    linhas = [_c(0, CORTESIA), _c(5760), _l(5763)]
    assert esperas(linhas) == [(_t(5760), _t(5763))]
    # Figurinhas depois da resposta, a pergunta 3 dias depois (9321a548).
    linhas = [_c(0), _l(10), _c(20, CORTESIA), _c(21, CORTESIA), _c(4320), _l(4410)]
    assert esperas(linhas) == [(_t(0), _t(10)), (_t(4320), _t(4410))]
    # Só cortesia, sem resposta: não é espera (nem vencida).
    assert esperas([_c(0), _l(10), _c(11, CORTESIA)]) == [(_t(0), _t(10))]
    # O "ok" que responde a uma pergunta da loja ("posso cancelar?") abre.
    assert esperas([_c(0), _l(10, pergunta=True), _c(20, CORTESIA), _l(80)]) == [
        (_t(0), _t(10)),
        (_t(20), _t(80)),
    ]
    # ...e uma resposta sem pergunta depois desfaz isso.
    assert esperas([_l(0, pergunta=True), _l(5), _c(20, CORTESIA)]) == []


def test_esperas_o_clique_fecha_e_a_aberta_fica_sem_fim():
    esperas = metricas_equipe.esperas
    # O clique antes da próxima mensagem fecha a espera nele; a fala nova abre outra.
    assert esperas([_c(0), _c(60)], clique=_t(10)) == [(_t(0), _t(10)), (_t(60), None)]
    # O clique depois da última mensagem fecha a aberta.
    assert esperas([_c(0)], clique=_t(30)) == [(_t(0), _t(30))]
    # O clique de antes da espera não vale para ela.
    assert esperas([_c(0, CARTAO), _c(60), _l(90)], clique=_t(10)) == [(_t(60), _t(90))]
    # Depois do clique, o "ok" não abre (o clique não pergunta nada).
    assert esperas([_l(0, pergunta=True), _c(5), _c(20, CORTESIA)], clique=_t(10)) == [
        (_t(5), _t(10))
    ]
    # A que ficou aberta: fim None.
    assert esperas([_c(0), _l(10), _c(20)]) == [(_t(0), _t(10)), (_t(20), None)]


# ── Com o banco: `_turnos_das_lojas` ──────────────────────────────────────────────


async def _msg(
    db: AsyncSession,
    conversa,
    n: str,
    ha: timedelta,
    *,
    autor: str = "cliente",
    texto: str | None = "Tem na cor azul?",
    tipo: str = "texto",
    payload: dict | None = None,
    anexos: list | None = None,
    origem: str | None = None,
) -> None:
    await gravar.gravar_mensagem(
        db,
        conversa,
        externo_id=f"{conversa.externo_id}-{n}",
        autor=autor,
        texto=texto,
        enviada_em=AGORA - ha,
        tipo=tipo,
        payload=payload,
        anexos=anexos,
        origem=origem or (constantes.ORIGEM_CLIENTE if autor == "cliente" else None),
    )
    await db.commit()


async def _loja_fala(db, conversa, n: str, ha: timedelta, texto: str, **kw) -> None:
    kw.setdefault("origem", constantes.ORIGEM_HUMANO)
    await _msg(db, conversa, n, ha, autor="loja", texto=texto, **kw)


async def _turnos(db: AsyncSession) -> dict:
    from app.deps.team_scope import TeamScope
    from app.routers import atendimento as rota

    turnos = await rota._turnos_das_lojas(
        db, TeamScope(unrestricted=True), AGORA - timedelta(days=7), AGORA
    )
    return {k[0]: v for k, v in turnos.items()}


def _conta(loja: dict) -> tuple:
    return (sorted(round(t, 1) for t in loja["tempos"]), loja["recebidas"], loja["vencidas"])


async def test_o_cartao_sozinho_nao_abre_e_nao_estica_a_espera(db: AsyncSession, make_user):
    # (a) Só o cartão, 20 h atrás, e o robô do Duoke: nem espera, nem vencida (Shopee: 12 h).
    so_cartao = await _conversa(db, make_user, externo_id="ct1", comprador_nome="Maria Souza")
    await _msg(
        db, so_cartao, "c", timedelta(hours=20), texto=None, tipo="produto", payload=CARTAO_SHOPEE
    )
    await _loja_fala(
        db,
        so_cartao,
        "r",
        timedelta(hours=20) - timedelta(minutes=1),
        MENU_DUOKE,
        origem=constantes.ORIGEM_EXTERNO,
    )
    # (b) O cartão da variação ("[Mensagem]") e a saudação da pessoa: não é tempo de nada.
    saudado = await _conversa(db, make_user, externo_id="ct2", comprador_nome="Maria Souza")
    await _msg(
        db,
        saudado,
        "c",
        timedelta(hours=5),
        texto="[Mensagem]",
        tipo="outro",
        payload=VARIACAO_SHOPEE,
    )
    await _loja_fala(db, saudado, "r", timedelta(hours=4), "Olá! Em que podemos ajudar?")
    # (c) O cartão 62 h antes da fala escrita; a IA respondeu 6,6 min depois dela (46fa04a9).
    esticado = await _conversa(db, make_user, externo_id="ct3", comprador_nome="Maria Souza")
    await _msg(
        db, esticado, "c", timedelta(hours=64), texto=None, tipo="produto", payload=CARTAO_SHOPEE
    )
    await _loja_fala(
        db,
        esticado,
        "r",
        timedelta(hours=64) - timedelta(minutes=1),
        MENU_DUOKE,
        origem=constantes.ORIGEM_EXTERNO,
    )
    await _msg(db, esticado, "t", timedelta(hours=2), texto="Esse serve no iPhone 15?")
    await _loja_fala(
        db,
        esticado,
        "ia",
        timedelta(hours=2) - timedelta(minutes=6.6),
        "Serve sim!",
        origem=constantes.ORIGEM_IA,
    )
    # (d) O TikTok, igual: o cartão sozinho, vencido no relógio velho (24 h), não conta.
    tiktok = await _conversa(
        db,
        make_user,
        plataforma="tiktok",
        platform=IntegrationPlatform.TIKTOK,
        externo_id="ct4",
        comprador_nome="Maria Souza",
    )
    await _msg(
        db, tiktok, "c", timedelta(hours=30), texto=None, tipo="produto", payload=CARTAO_TIKTOK
    )

    por_loja = await _turnos(db)
    assert _conta(por_loja[so_cartao.integration_id]) == ([], 0, 0)
    assert _conta(por_loja[saudado.integration_id]) == ([], 0, 0)
    assert _conta(por_loja[esticado.integration_id]) == ([6.6], 1, 0)
    assert _conta(por_loja[tiktok.integration_id]) == ([], 0, 0)


async def test_a_cortesia_nao_abre_a_espera_e_a_pergunta_de_depois_abre(
    db: AsyncSession, make_user
):
    # (a) O "ok obrigado" como primeira fala (antes, só o cartão de pedido que a Shopee põe e o
    # robô); 4 dias depois a pergunta, que a IA responde em 2,7 min (2b96dbfc).
    primeira = await _conversa(db, make_user, externo_id="co1", comprador_nome="Maria Souza")
    await _loja_fala(
        db,
        primeira,
        "k",
        timedelta(hours=100),
        "[Pedido]",
        tipo="pedido",
        payload=CARTAO_DA_LOJA,
        origem=constantes.ORIGEM_EXTERNO,
    )
    await _msg(db, primeira, "ok", timedelta(hours=98), texto="ok obrigado")
    await _loja_fala(
        db,
        primeira,
        "m",
        timedelta(hours=98) - timedelta(minutes=1),
        MENU_DUOKE,
        origem=constantes.ORIGEM_EXTERNO,
    )
    await _msg(db, primeira, "q", timedelta(hours=3), texto="Meu pedido ainda não chegou")
    await _loja_fala(
        db,
        primeira,
        "ia",
        timedelta(hours=3) - timedelta(minutes=2.7),
        "Já conferi: ele sai para entrega hoje.",
        origem=constantes.ORIGEM_IA,
    )
    # (b) A loja respondeu; "Obrigada!", figurinha e o "de nada" da loja: nada de espera.
    so_cortesia = await _conversa(db, make_user, externo_id="co2", comprador_nome="Maria Souza")
    await _msg(db, so_cortesia, "q", timedelta(hours=30))
    await _loja_fala(db, so_cortesia, "r", timedelta(hours=29), "Temos sim, na cor azul.")
    await _msg(db, so_cortesia, "ok", timedelta(hours=28), texto="Obrigada!")
    await _msg(
        db,
        so_cortesia,
        "fig",
        timedelta(hours=28) - timedelta(minutes=1),
        texto="[Figurinha]",
        tipo="outro",
        payload=FIGURINHA,
    )
    await _msg(db, so_cortesia, "emo", timedelta(hours=27), texto="👍")
    await _loja_fala(db, so_cortesia, "dn", timedelta(hours=14), "De nada! 😊")
    # (c) "ok mas e o rastreio?" depois da resposta abre — e venceu (Shopee: 12 h).
    rastreio = await _conversa(db, make_user, externo_id="co3", comprador_nome="Maria Souza")
    await _msg(db, rastreio, "q", timedelta(hours=40))
    await _loja_fala(db, rastreio, "r", timedelta(hours=39), "Seu pedido foi enviado ontem.")
    await _msg(db, rastreio, "ok", timedelta(hours=30), texto="ok mas e o rastreio?")
    # (d) O "ok" que responde a pergunta da loja abre: a loja voltou 60 min depois.
    consentiu = await _conversa(db, make_user, externo_id="co4", comprador_nome="Maria Souza")
    await _msg(db, consentiu, "q", timedelta(hours=6), texto="Quero cancelar")
    await _loja_fala(db, consentiu, "r", timedelta(hours=5), "Posso cancelar o pedido agora?")
    await _msg(db, consentiu, "ok", timedelta(hours=4), texto="ok")
    await _loja_fala(db, consentiu, "f", timedelta(hours=3), "Pronto, cancelado.")
    # (e) O "bom dia" depois da resposta é fala (cumprimento abre): a loja voltou em 30 min.
    cumprimento = await _conversa(db, make_user, externo_id="co5", comprador_nome="Maria Souza")
    await _msg(db, cumprimento, "q", timedelta(hours=50))
    await _loja_fala(db, cumprimento, "r", timedelta(hours=49), "Temos sim.")
    await _msg(db, cumprimento, "bd", timedelta(hours=10), texto="bom dia")
    await _loja_fala(db, cumprimento, "r2", timedelta(hours=9, minutes=30), "Bom dia! Diga.")

    por_loja = await _turnos(db)
    assert _conta(por_loja[primeira.integration_id]) == ([2.7], 1, 0)
    assert _conta(por_loja[so_cortesia.integration_id]) == ([60.0], 1, 0)
    assert _conta(por_loja[rastreio.integration_id]) == ([60.0], 2, 1)
    assert _conta(por_loja[consentiu.integration_id]) == ([60.0, 60.0], 2, 0)
    assert _conta(por_loja[cumprimento.integration_id]) == ([30.0, 60.0], 2, 0)


async def test_o_recibo_da_evidencia_nao_fecha_a_espera(db: AsyncSession, make_user):
    assert RECIBO in acao_pessoa.TEXTOS_DO_RECIBO
    # A foto às −10 h, o recibo da IA 2 min depois, a pessoa responde às −2 h: 480 min.
    conversa = await _conversa(db, make_user, externo_id="re1", comprador_nome="Maria Souza")
    await _msg(db, conversa, "q", timedelta(hours=12), texto="A mala chegou quebrada")
    await _loja_fala(db, conversa, "r", timedelta(hours=11), "Pode mandar uma foto da mala?")
    await _msg(
        db,
        conversa,
        "f",
        timedelta(hours=10),
        texto=None,
        tipo="imagem",
        anexos=[{"tipo": "imagem", "url": "https://x/f.jpg"}],
    )
    await _loja_fala(
        db,
        conversa,
        "rc",
        timedelta(hours=10) - timedelta(minutes=2),
        RECIBO,
        origem=constantes.ORIGEM_IA,
        payload={"envio": {"x": 1}},
    )
    await _loja_fala(db, conversa, "p", timedelta(hours=2), "Vamos trocar a sua mala.")
    # Só o recibo depois da foto, há 13 h: vencida sem resposta (o recibo não respondeu).
    sem_pessoa = await _conversa(db, make_user, externo_id="re2", comprador_nome="Maria Souza")
    await _msg(
        db,
        sem_pessoa,
        "f",
        timedelta(hours=13),
        texto=None,
        tipo="video",
        anexos=[{"tipo": "video", "url": "https://x/v.mp4"}],
    )
    await _loja_fala(
        db,
        sem_pessoa,
        "rc",
        timedelta(hours=13) - timedelta(minutes=3),
        "Recebemos o vídeo, obrigado! A equipe analisa e responde por aqui.",
        origem=constantes.ORIGEM_IA,
    )

    por_loja = await _turnos(db)
    assert _conta(por_loja[conversa.integration_id]) == ([60.0, 480.0], 2, 0)
    assert _conta(por_loja[sem_pessoa.integration_id]) == ([], 1, 1)
    # Nem resposta da IA no "saiu sozinho": a da pessoa conta; o recibo, não.
    numeros = await metricas_equipe.numeros_por_loja(db, AGORA - timedelta(days=7), AGORA)
    a = numeros[conversa.integration_id]
    assert (a.respostas, a.ia_enviou, a.equipe_enviou) == (2, 0, 2)
    assert sem_pessoa.integration_id not in numeros


async def test_a_conversa_fechada_ou_bloqueada_continua_vencida(db: AsyncSession, make_user):
    async def _aberta_ha_20h(externo_id: str, situacao: str | None, respondida: bool = False):
        c = await _conversa(db, make_user, externo_id=externo_id, comprador_nome="Maria Souza")
        if respondida:
            await _msg(db, c, "q0", timedelta(hours=22))
            await _loja_fala(db, c, "r0", timedelta(hours=21), "Temos sim.")
        await _msg(db, c, "q", timedelta(hours=20), texto="E o prazo de entrega?")
        if situacao is not None:
            c.situacao = situacao
            await db.commit()
        return c

    fechada = await _aberta_ha_20h("fe1", constantes.CONVERSA_FECHADA)
    bloqueada = await _aberta_ha_20h("bl1", constantes.CONVERSA_BLOQUEADA)
    # Fechar não apaga a vencida: a espera sem resposta continua contando.
    com_resposta = await _aberta_ha_20h("fe2", constantes.CONVERSA_FECHADA, respondida=True)
    aberta = await _aberta_ha_20h("ab1", None)

    por_loja = await _turnos(db)
    assert _conta(por_loja[fechada.integration_id]) == ([], 1, 1)
    assert _conta(por_loja[bloqueada.integration_id]) == ([], 1, 1)
    assert _conta(por_loja[com_resposta.integration_id]) == ([60.0], 2, 1)
    assert _conta(por_loja[aberta.integration_id]) == ([], 1, 1)


async def test_so_o_chat_entra(db: AsyncSession, make_user):
    async def _com_espera(canal: str, externo_id: str, plataforma: str = "shopee"):
        c = await _conversa(
            db,
            make_user,
            canal=canal,
            plataforma=plataforma,
            externo_id=externo_id,
            comprador_nome="Maria Souza",
        )
        await _msg(db, c, "q", timedelta(hours=20), texto="Produto veio com defeito")
        await _loja_fala(
            db,
            c,
            "r",
            timedelta(hours=19),
            "Sentimos muito pela experiência 😔",
            origem=constantes.ORIGEM_EXTERNO,
        )
        await _msg(db, c, "q2", timedelta(hours=15), texto="E agora, o que faço?")
        return c

    chat = await _com_espera(constantes.CANAL_CHAT, "ch1")
    fora = [
        await _com_espera(constantes.CANAL_AVALIACAO, "av1"),
        await _com_espera(constantes.CANAL_RECLAMACAO, "rc1", plataforma="ml"),
        await _com_espera(constantes.CANAL_COMENTARIO, "cm1"),
        await _com_espera(constantes.CANAL_CARRINHO, "cr1"),
    ]

    por_loja = await _turnos(db)
    assert _conta(por_loja[chat.integration_id]) == ([60.0], 2, 1)
    for c in fora:
        assert c.integration_id not in por_loja, c.canal
    # As respostas do "saiu sozinho" também são só do chat.
    numeros = await metricas_equipe.numeros_por_loja(db, AGORA - timedelta(days=7), AGORA)
    assert numeros[chat.integration_id].respostas == 1
    for c in fora:
        assert c.integration_id not in numeros, c.canal
