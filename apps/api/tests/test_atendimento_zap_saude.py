# ruff: noqa: S105, S106, F811  (segredos de teste; fixtures importadas do apoio)
"""A SAÚDE, os ALARMES e a LIMPEZA do Zap (RF10, Onda 4) — docs/zap/desenho.md §12, §5.1, §10.5.

A Graph API é de MENTIRA (`tests/zap_apoio.GraphFalso`), o Redis é um falso em
memória e o relógio é passado à mão (`agora=`): nenhum teste fala com a Meta.

O que se garante (27/10/2026) — um alarme quando:
- há falha de assinatura de um app E nenhum evento válido dele na hora
  (proporção: com evento válido, ou com poucas falhas, não alarma);
- a fila de eventos está parada há mais de 10 min (o status que espera a
  mensagem por backoff não conta; o `ignorado` também não);
- há mídia a menos de 24 h de expirar na Meta sem ter sido baixada;
- o token caiu, vence em 7 dias ou a inspeção falhou;
- a Meta restringiu o número ou a qualidade caiu;
- o webhook do número aponta para outro lugar, ou há outro app inscrito (ou
  o nosso não está);
- as respostas do mês chegam a 800 e a 1.000, ou o dia a 70 % do teto;
- um número ATIVO fica calado, em horário comercial, além do que o ritmo dele
  (ou o do app inteiro) explica — o número de pouca mensagem não acende o dia
  todo, e o "Testar conexão" que deu ok zera a conta (11/11/2026).
E também:
- o cron renova da Meta (só GET): a inspeção do token (1×/dia; recusa marca a
  conta, "não respondeu" vira alarme), os apps inscritos e a saúde do número;
- avisar: um Alerta do DaVinci por alarme por dia, para cada admin ativo;
- os dois crons saem na hora com a leitura do Zap desligada e estão no worker;
- a limpeza: mídia vencida perde o arquivo ("mídia expirada"), evento
  processado com mais de 30 dias sai, o pendente fica, a sobra de download sai;
- o `webhook_signature_alert_scan` lê o Zap (por app) e o Instagram;
- o vigia de leitura parada ganha a classe do Zap — e a linha só para admin;
- o webhook conta a falha de assinatura POR APP.
"""

from __future__ import annotations

import os
import time
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import select, update

import app.db as _db
from app import worker
from app.config import get_settings
from app.models import (
    Alert,
    AtendimentoCanal,
    AtendimentoConversa,
    AtendimentoMensagem,
    AtendimentoZapApp,
    AtendimentoZapConta,
    AtendimentoZapEvento,
    AtendimentoZapMidia,
    AtendimentoZapNumero,
    UserRole,
)
from app.services import vigia_leitura_atendimento as vigia
from app.services.atendimento import zap as zap_adaptador
from app.services.atendimento import zap_contas, zap_entrada, zap_midia, zap_saude
from tests.zap_apoio import (  # noqa: F401  (fixtures: zap, graph, cliente_zap)
    APP_ID,
    PNID,
    WABA,
    cadastrar,
    cliente_zap,
    corpo,
    eventos,
    graph,
    gravar_corpo,
    postar,
    processar,
    zap,
)

V = "v26.0"
# Uma quarta-feira às 14 h em São Paulo (17 h UTC): horário comercial.
QUARTA_14H = datetime(2026, 10, 28, 17, 0, tzinfo=UTC)
# A mesma quarta às 03 h em SP: fora do horário comercial.
QUARTA_03H = datetime(2026, 10, 28, 6, 0, tzinfo=UTC)
# Um domingo às 14 h em SP.
DOMINGO_14H = datetime(2026, 11, 1, 17, 0, tzinfo=UTC)


def _codigos(alertas) -> list[str]:
    return sorted(a.codigo for a in alertas)


async def _calcular(agora: datetime, redis_cli=None):
    async with _db.SessionLocal() as s:
        return await zap_saude.calcular(s, agora=agora, redis_cli=redis_cli)


async def _mudar(db, modelo, alvo_id, **valores) -> None:
    await db.execute(update(modelo).where(modelo.id == alvo_id).values(**valores))
    await db.commit()


# ═══════════════════════ as réguas (puras) ═══════════════════════════════


def test_horario_comercial():
    assert zap_saude.em_horario_comercial(QUARTA_14H)
    assert not zap_saude.em_horario_comercial(QUARTA_03H)
    assert not zap_saude.em_horario_comercial(DOMINGO_14H)
    # Sábado às 10 h em SP (13 h UTC) conta; às 20 h, não.
    assert zap_saude.em_horario_comercial(datetime(2026, 10, 31, 13, 0, tzinfo=UTC))
    assert not zap_saude.em_horario_comercial(datetime(2026, 10, 31, 23, 0, tzinfo=UTC))


def test_conexao_na_ordem_do_que_mais_pesa():
    agora = QUARTA_14H
    app = AtendimentoZapApp(app_id=APP_ID, ultimo_evento_em=agora - timedelta(hours=2))
    conta = AtendimentoZapConta(waba_id=WABA, status="ok")
    n = AtendimentoZapNumero(phone_number_id=PNID, saude_status="ok", saude={})
    assert zap_saude.conexao(n, conta, app, 0, agora)[0] == "sem_eventos"
    n.ultimo_recebido_em = agora - timedelta(minutes=5)
    assert zap_saude.conexao(n, conta, app, 0, agora)[0] == "conectado"
    n.saude = {"webhook_outro": [{"onde": "phone_number", "host": "x"}]}
    assert zap_saude.conexao(n, conta, app, 0, agora)[0] == "webhook_outro"
    n.saude_status = "restrito"
    assert zap_saude.conexao(n, conta, app, 0, agora)[0] == "restrito"
    assert zap_saude.conexao(n, conta, app, 5, agora)[0] == "assinatura_falhando"
    conta.status = "token_invalido"
    assert zap_saude.conexao(n, conta, app, 5, agora)[0] == "token_vencido"


# ═══════════════════════ os alarmes ══════════════════════════════════════


async def test_assinatura_so_alarma_sem_evento_valido(db, zap):
    app, _conta, _numero = await cadastrar(db)
    chave = zap_contas.CHAVE_FALHA_ASSINATURA_APP.format(app_ref=app.id)
    agora = QUARTA_14H
    # Sem falha nenhuma: nada.
    assert zap_saude.A_ASSINATURA not in _codigos(await _calcular(agora, zap.redis))
    # Falhas E nenhum evento válido: alarma.
    zap.redis.dados[chave] = 7
    alertas = await _calcular(agora, zap.redis)
    (a,) = [x for x in alertas if x.codigo == zap_saude.A_ASSINATURA]
    assert a.app_ref == app.id and "chave secreta" in a.frase
    # Com evento válido na mesma hora (um estranho com curl): não alarma.
    await _mudar(db, AtendimentoZapApp, app.id, ultimo_evento_em=agora - timedelta(minutes=10))
    assert zap_saude.A_ASSINATURA not in _codigos(await _calcular(agora, zap.redis))
    # Poucas falhas: não alarma.
    await _mudar(db, AtendimentoZapApp, app.id, ultimo_evento_em=None)
    zap.redis.dados[chave] = 2
    assert zap_saude.A_ASSINATURA not in _codigos(await _calcular(agora, zap.redis))


async def test_webhook_conta_a_falha_por_app(db, zap, cliente_zap):
    app, _c, _n = await cadastrar(db)
    r = await postar(cliente_zap, corpo("texto"), segredo="segredo-errado")
    assert r.status_code == 403
    chave = zap_contas.CHAVE_FALHA_ASSINATURA_APP.format(app_ref=app.id)
    assert int(zap.redis.dados[chave]) == 1


async def test_fila_parada(db, zap):
    app, _c, _n = await cadastrar(db)
    agora = datetime.now(UTC)
    await gravar_corpo(corpo("texto", wamid="wamid.F"), app=app)
    assert zap_saude.A_FILA not in _codigos(await _calcular(agora + timedelta(minutes=5)))
    assert zap_saude.A_FILA in _codigos(await _calcular(agora + timedelta(minutes=11)))
    # O status que ESPERA a mensagem (backoff até 1 h) não é fila parada.
    (ev,) = await eventos(db)
    await _mudar(db, AtendimentoZapEvento, ev.id, proxima_em=agora + timedelta(minutes=30))
    assert zap_saude.A_FILA not in _codigos(await _calcular(agora + timedelta(minutes=11)))
    # Processado: nada.
    await _mudar(db, AtendimentoZapEvento, ev.id, proxima_em=None, processado_em=agora)
    assert zap_saude.A_FILA not in _codigos(await _calcular(agora + timedelta(hours=2)))


async def test_midia_a_menos_de_24h_de_expirar(db, zap):
    app, _c, numero = await cadastrar(db)
    await gravar_corpo(corpo("imagem", wamid="wamid.M", media_id="MIDS"), app=app)
    await processar()
    m = (await db.execute(select(AtendimentoZapMidia))).scalar_one()
    agora = datetime.now(UTC)
    await _mudar(db, AtendimentoZapMidia, m.id, expira_meta_em=agora + timedelta(hours=30))
    assert zap_saude.A_MIDIA not in _codigos(await _calcular(agora))
    await _mudar(db, AtendimentoZapMidia, m.id, expira_meta_em=agora + timedelta(hours=20))
    (a,) = [x for x in await _calcular(agora) if x.codigo == zap_saude.A_MIDIA]
    assert a.numero_ref == numero.id and "24 h" in a.frase
    await _mudar(db, AtendimentoZapMidia, m.id, status="baixada")
    assert zap_saude.A_MIDIA not in _codigos(await _calcular(agora))


async def test_token_invalido_vencendo_e_nao_conferido(db, zap):
    _app, conta, _n = await cadastrar(db)
    agora = QUARTA_14H
    await _mudar(db, AtendimentoZapConta, conta.id, status="token_invalido")
    assert zap_saude.A_TOKEN_INVALIDO in _codigos(await _calcular(agora))
    await _mudar(
        db,
        AtendimentoZapConta,
        conta.id,
        status="ok",
        token_nunca_vence=False,
        token_expira_em=agora + timedelta(days=5),
    )
    (a,) = [x for x in await _calcular(agora) if x.codigo == zap_saude.A_TOKEN_VENCENDO]
    assert "5 dia" in a.frase
    await _mudar(db, AtendimentoZapConta, conta.id, token_expira_em=agora + timedelta(days=30))
    assert zap_saude.A_TOKEN_VENCENDO not in _codigos(await _calcular(agora))
    await _mudar(db, AtendimentoZapConta, conta.id, saude={"inspecao_erro": "a Meta não respondeu"})
    assert zap_saude.A_TOKEN_NAO_CONFERIDO in _codigos(await _calcular(agora))


async def test_restricao_qualidade_e_webhook_em_outro_lugar(db, zap):
    _app, _c, numero = await cadastrar(db)
    agora = QUARTA_14H
    await _mudar(
        db,
        AtendimentoZapNumero,
        numero.id,
        saude_status="restrito",
        saude={
            "restricao": "A Meta restringiu este número",
            "qualidade": "RED",
            "webhook_outro": [{"onde": "phone_number", "host": "duoke.example"}],
        },
    )
    codigos = _codigos(await _calcular(agora))
    assert {zap_saude.A_RESTRITO, zap_saude.A_QUALIDADE, zap_saude.A_WEBHOOK} <= set(codigos)
    web = next(a for a in await _calcular(agora) if a.codigo == zap_saude.A_WEBHOOK)
    assert "duoke.example" in web.frase
    # O evento de qualidade da Meta (FLAGGED) também.
    await _mudar(
        db,
        AtendimentoZapNumero,
        numero.id,
        saude_status="ok",
        saude={"qualidade_evento": "FLAGGED"},
    )
    assert _codigos(await _calcular(agora)) == [zap_saude.A_QUALIDADE]


async def test_outro_app_inscrito_e_o_nosso_fora(db, zap):
    _app, conta, _n = await cadastrar(db)
    agora = QUARTA_14H
    await _mudar(
        db,
        AtendimentoZapConta,
        conta.id,
        saude={
            "inscricao_em": agora.isoformat(),
            "nosso_inscrito": True,
            "outros_apps": [{"id": "777", "nome": "Duoke"}],
        },
    )
    (a,) = [x for x in await _calcular(agora) if x.codigo == zap_saude.A_OUTRO_APP]
    assert "Duoke" in a.frase
    await _mudar(
        db,
        AtendimentoZapConta,
        conta.id,
        saude={"inscricao_em": agora.isoformat(), "nosso_inscrito": False, "outros_apps": []},
    )
    assert zap_saude.A_NAO_INSCRITO in _codigos(await _calcular(agora))
    # O nosso FORA e o do Duoke dentro: os dois alarmes, e a frase não diz que o
    # DaVinci está inscrito (revisão de 27/10/2026).
    await _mudar(
        db,
        AtendimentoZapConta,
        conta.id,
        saude={
            "inscricao_em": agora.isoformat(),
            "nosso_inscrito": False,
            "outros_apps": [{"id": "777", "nome": "Duoke"}],
        },
    )
    alertas = await _calcular(agora)
    assert {zap_saude.A_OUTRO_APP, zap_saude.A_NAO_INSCRITO} <= set(_codigos(alertas))
    outro = next(a for a in alertas if a.codigo == zap_saude.A_OUTRO_APP)
    assert "NÃO está inscrito" in outro.frase and "além do DaVinci" not in outro.frase


async def test_o_que_a_meta_mostrou_do_numero_vira_pill_e_alarme(db, zap):
    """platform_type fora da Cloud API, status ruim, nome recusado, webhook não conferido
    e o erro da Meta no número (131042/133010) — revisão de 27/10/2026."""
    app, conta, numero = await cadastrar(db)
    agora = QUARTA_14H

    async def _pill() -> str:
        n = await db.get(AtendimentoZapNumero, numero.id, populate_existing=True)
        c = await db.get(AtendimentoZapConta, conta.id, populate_existing=True)
        a = await db.get(AtendimentoZapApp, app.id, populate_existing=True)
        return zap_saude.conexao(n, c, a, 0, agora)[0]

    # O número de teste fora da API (o 133010 do ensaio real): pill e alarme.
    await _mudar(db, AtendimentoZapNumero, numero.id, saude={"plataforma": "NOT_APPLICABLE"})
    assert await _pill() == "nao_registrado"
    (a,) = [x for x in await _calcular(agora) if x.codigo == zap_saude.A_NAO_REGISTRADO]
    assert "Registrar" in a.frase and a.nivel == zap_saude.ERRO
    # Na preparação é o esperado: sem alarme (a pill continua dizendo).
    await _mudar(db, AtendimentoZapNumero, numero.id, etapa="preparando")
    assert zap_saude.A_NAO_REGISTRADO not in _codigos(await _calcular(agora))
    # O 133010 do envio já alarma (A_ERRO_NUMERO): não repete.
    await _mudar(
        db,
        AtendimentoZapNumero,
        numero.id,
        etapa="teste",
        saude_status="erro",
        saude={
            "plataforma": "NOT_APPLICABLE",
            "erro_meta": {"code": 133010, "frase": "Este número não está registrado"},
        },
    )
    codigos = _codigos(await _calcular(agora))
    assert zap_saude.A_ERRO_NUMERO in codigos and zap_saude.A_NAO_REGISTRADO not in codigos
    # Pagamento (131042): o alarme do erro com a frase da Meta.
    await _mudar(
        db,
        AtendimentoZapNumero,
        numero.id,
        saude={"erro_meta": {"code": 131042, "frase": "Problema de pagamento na conta Meta"}},
    )
    (a,) = [x for x in await _calcular(agora) if x.codigo == zap_saude.A_ERRO_NUMERO]
    assert "pagamento" in a.frase
    # Status ruim (BANNED), nome recusado e webhook não conferido.
    await _mudar(
        db,
        AtendimentoZapNumero,
        numero.id,
        saude_status="ok",
        nome_status="DECLINED",
        saude={"status_meta": "BANNED", "webhook_conferido": False},
    )
    assert await _pill() == "restrito"
    codigos = set(_codigos(await _calcular(agora)))
    assert {
        zap_saude.A_STATUS_META,
        zap_saude.A_NOME_RECUSADO,
        zap_saude.A_WEBHOOK_NAO_CONFERIDO,
    } <= codigos
    await _mudar(
        db, AtendimentoZapNumero, numero.id, nome_status=None, saude={"webhook_conferido": False}
    )
    assert await _pill() == "webhook_nao_conferido"
    # Tudo conferido e na Cloud API: nada.
    await _mudar(
        db,
        AtendimentoZapNumero,
        numero.id,
        saude={"plataforma": "CLOUD_API", "status_meta": "CONNECTED", "webhook_conferido": True},
    )
    assert _codigos(await _calcular(agora)) == []


async def test_respostas_do_mes_e_teto_do_dia(db, zap, monkeypatch):
    _app, _c, numero = await cadastrar(db)
    agora = QUARTA_14H
    canal_id = numero.canal_id

    async def respostas(session, canal_ids, quando):
        return {canal_id: (respostas.valor, 3)}

    async def hoje(session, cid, quando):
        return hoje.valor

    monkeypatch.setattr(zap_entrada, "respostas_do_mes", respostas)
    monkeypatch.setattr(zap_adaptador, "envios_do_numero_hoje", hoje)
    respostas.valor, hoje.valor = 799, 0
    await _mudar(db, AtendimentoZapNumero, numero.id, teto_dia=100)
    assert _codigos(await _calcular(agora)) == []
    respostas.valor = 800
    assert _codigos(await _calcular(agora)) == [zap_saude.A_RESPOSTAS_800]
    respostas.valor = 1000
    assert _codigos(await _calcular(agora)) == [zap_saude.A_RESPOSTAS_1000]
    respostas.valor, hoje.valor = 0, 69
    assert _codigos(await _calcular(agora)) == []
    hoje.valor = 70
    (a,) = await _calcular(agora)
    assert a.codigo == zap_saude.A_TETO and "70 de 100" in a.frase


async def test_respostas_do_mes_conta_uma_vez_por_wamid(db, zap):
    """A régua do contador (a mesma do alerta da Onda 2): resposta com custo de `service`."""
    from app.models import AtendimentoConversa, AtendimentoMensagem

    app, _c, numero = await cadastrar(db)
    await gravar_corpo(corpo("texto", wamid="wamid.R"), app=app)
    await processar()
    conversa = (await db.execute(select(AtendimentoConversa))).scalar_one()
    agora = datetime.now(UTC)
    for i, tipo in enumerate(("regular", "free_customer_service", "regular")):
        db.add(
            AtendimentoMensagem(
                conversa_id=conversa.id,
                externo_id=f"wamid.OUT{i}",
                autor="loja",
                origem="humano",
                texto="ok",
                enviada_em=agora,
                status="enviada",
                payload={
                    "zap": {
                        "entrega": {
                            "categoria": "service",
                            "tipo_preco": tipo,
                            "custo_em": agora.isoformat(),
                        }
                    }
                },
            )
        )
    await db.commit()
    async with _db.SessionLocal() as s:
        r = await zap_entrada.respostas_do_mes(s, [numero.canal_id], agora)
    assert r == {numero.canal_id: (3, 2)}


# ── o silêncio de estranhar (11/11/2026) ─────────────────────────────────


def _sp(dia: int, hora: int, minuto: int = 0) -> datetime:
    """Um horário de outubro/2026 em São Paulo (UTC−3), em UTC."""
    return datetime(2026, 10, dia, hora + 3, minuto, tzinfo=UTC)


# Toda hora comercial de 21/10 (quarta) às 9 h de 28/10 com mensagem: o número
# movimentado. Uma por dia, às 10 h: o número quieto (o normal em 11/11).
CHEIO = [
    _sp(d, h, 30)
    for d in range(21, 29)
    for h in range(8, 20)
    if datetime(2026, 10, d).weekday() < 6 and _sp(d, h, 30) < _sp(28, 10)
]
QUIETO = [_sp(d, 10, 5) for d in (21, 22, 23, 24, 26, 27)]
# Duas por dia (10 h e 15 h): ritmo bastante para julgar, mas folgado.
MEIO = [_sp(d, h, 5) for d in (21, 22, 23, 24, 26, 27) for h in (10, 15)]


def test_horas_comerciais():
    # Sábado 19 h → segunda 9 h: 1 h no sábado, nada no domingo, 1 h na segunda.
    assert zap_saude.horas_comerciais(_sp(31, 19), datetime(2026, 11, 2, 12, tzinfo=UTC)) == 2
    # De madrugada a madrugada: as 12 h do dia.
    assert zap_saude.horas_comerciais(_sp(28, 3), _sp(29, 3)) == 12
    assert zap_saude.horas_comerciais(_sp(28, 14), _sp(28, 14)) == 0
    assert zap_saude.horas_comerciais(_sp(28, 15), _sp(28, 14)) == 0


def test_limite_do_silencio_pelo_ritmo():
    # Toda hora com mensagem: o piso de 3 h.
    assert zap_saude.limite_do_silencio(60, 60) == 3
    # Metade das horas: ln(0,01)/ln(0,5) ≈ 6,6 h.
    assert round(zap_saude.limite_do_silencio(30, 60), 1) == 6.6
    # Uma hora a cada 12 (8 de 96): ~53 h — mais de quatro dias comerciais.
    assert zap_saude.limite_do_silencio(8, 96) > 48
    # Sem base: menos de 8 horas com mensagem (o número quieto de 11/11 — com
    # 3 ou 4 mensagens soltas o ritmo sai inflado), ou menos de um dia de história.
    assert zap_saude.limite_do_silencio(0, 72) is None
    assert zap_saude.limite_do_silencio(7, 72) is None
    assert zap_saude.limite_do_silencio(10, 11) is None


def test_o_silencio_fica_fora_da_conta_do_ritmo():
    """A história vai só até o último sinal: senão o próprio silêncio baixaria o ritmo."""
    agora = QUARTA_14H
    s = zap_saude.avaliar_silencio(_sp(28, 10), agora, CHEIO, None)
    assert s.estranho and s.limite_h == 3 and s.horas == 4 and s.desde == _sp(28, 10)
    # A história é a semana que termina no sinal (21/10 10 h → 28/10 10 h): as
    # 4 h caladas não entram (com elas, a base seria 76).
    assert s.base_h == 72 and s.ativas == 72
    # Calado há mais de uma semana: a história acabou, sem base.
    s = zap_saude.avaliar_silencio(agora - timedelta(days=8), agora, CHEIO, None)
    assert not s.estranho and s.limite_h is None
    # Duas por dia, calado desde ontem às 15 h: normal no ritmo dele.
    s = zap_saude.avaliar_silencio(_sp(27, 15, 5), agora, MEIO, None)
    assert not s.estranho and s.limite_h > s.horas
    # Uma por dia: ritmo de menos para julgar.
    s = zap_saude.avaliar_silencio(_sp(27, 10, 5), agora, QUIETO, None)
    assert not s.estranho and s.limite_h is None


def test_a_historia_nao_encolhe_com_o_silencio():
    """Revisão de 11/11: com a história presa em `agora`, quanto mais calado, menor
    a base e maior o peso da última mensagem — o limite caía e acusava à toa."""
    sinal = _sp(27, 15, 5)
    limites = {
        zap_saude.avaliar_silencio(sinal, quando, MEIO, None).limite_h
        for quando in (_sp(28, 9), _sp(28, 18), _sp(29, 17), _sp(30, 12))
    }
    assert len(limites) == 1  # o mesmo limite, calado há 2 h ou há 2 dias


def test_o_teste_ok_so_move_o_comeco_da_contagem():
    """O "Testar conexão" ok zera a contagem, mas o ritmo segue o da semana antes do
    último sinal REAL: o trecho calado até o teste não baixa o ritmo (revisão de 11/11)."""
    sinal = _sp(27, 15, 5)  # 2 por dia: limite ≈ 25,3 h comerciais
    antes = zap_saude.avaliar_silencio(sinal, _sp(29, 17), MEIO, None)
    assert antes.estranho and 25 < antes.limite_h < 26
    teste = _sp(29, 16)
    depois = zap_saude.avaliar_silencio(teste, _sp(29, 17), MEIO, None, historia_ate=sinal)
    assert not depois.estranho and depois.limite_h == antes.limite_h
    # 26 h comerciais depois do teste (sábado 31/10, 18 h), ainda calado: volta a acusar.
    volta = zap_saude.avaliar_silencio(teste, _sp(31, 18), MEIO, None, historia_ate=sinal)
    assert volta.estranho and volta.limite_h == antes.limite_h


def test_texto_do_ritmo_nunca_mostra_silencio_menor_que_o_limite():
    s = zap_saude.Silencio(True, 6.8, 6.64, 30, 60)
    texto = zap_saude.ritmo_em_texto(s)
    assert "calado há 6 h comerciais (alarma a partir de 6 h)" in texto


async def _historia(db, numero, momentos: list[datetime], conversa: str = "h") -> None:
    """Mensagens de cliente no canal do número, nos horários dados."""
    conversa = AtendimentoConversa(
        canal_id=numero.canal_id,
        plataforma="whatsapp",
        canal="zap",
        externo_id=f"{conversa}-{numero.id}",
    )
    db.add(conversa)
    await db.flush()
    for i, quando in enumerate(momentos):
        db.add(
            AtendimentoMensagem(
                conversa_id=conversa.id,
                externo_id=f"h{i}",
                autor="cliente",
                origem="cliente",
                texto="oi",
                enviada_em=quando,
            )
        )
    await db.commit()


async def _ativo(db, numero, *, sinal: datetime, criado: datetime, **mais) -> None:
    await _mudar(
        db,
        AtendimentoZapNumero,
        numero.id,
        etapa="ativo",
        ultimo_recebido_em=sinal,
        ultimo_ok_em=sinal,
        created_at=criado,
        **mais,
    )
    await _mudar(db, AtendimentoCanal, numero.canal_id, ultimo_ok_em=sinal)


async def test_sem_webhook_pelo_ritmo_do_numero(db, zap):
    _app, _c, numero = await cadastrar(db)
    agora = QUARTA_14H
    await _historia(db, numero, CHEIO)
    await _ativo(db, numero, sinal=_sp(28, 10), criado=agora - timedelta(days=10))
    (a,) = [x for x in await _calcular(agora) if x.codigo == zap_saude.A_SEM_WEBHOOK]
    assert "4 h comerciais" in a.frase and "Testar conexão" in a.frase
    # De madrugada e no domingo, não.
    assert zap_saude.A_SEM_WEBHOOK not in _codigos(await _calcular(QUARTA_03H))
    assert zap_saude.A_SEM_WEBHOOK not in _codigos(await _calcular(DOMINGO_14H))
    # Há 2 h: ainda não.
    await _ativo(db, numero, sinal=agora - timedelta(hours=2), criado=agora - timedelta(days=10))
    assert zap_saude.A_SEM_WEBHOOK not in _codigos(await _calcular(agora))
    # Número de teste (não ativo): nunca.
    await _ativo(db, numero, sinal=_sp(28, 10), criado=agora - timedelta(days=10))
    await _mudar(db, AtendimentoZapNumero, numero.id, etapa="teste")
    assert zap_saude.A_SEM_WEBHOOK not in _codigos(await _calcular(agora))


async def test_numero_quieto_nao_acende_o_dia_todo(db, zap):
    """O caso de 11/11: 1 mensagem por dia, calado há 4 h ou há um dia — normal."""
    _app, _c, numero = await cadastrar(db)
    agora = QUARTA_14H
    await _historia(db, numero, QUIETO)
    await _ativo(db, numero, sinal=_sp(28, 10), criado=agora - timedelta(days=10))
    assert zap_saude.A_SEM_WEBHOOK not in _codigos(await _calcular(agora))
    await _ativo(db, numero, sinal=_sp(27, 10, 5), criado=agora - timedelta(days=10))
    assert zap_saude.A_SEM_WEBHOOK not in _codigos(await _calcular(agora))
    # Duas por dia, calado desde ontem às 15 h: também normal.
    await _historia(db, numero, MEIO, conversa="meio")
    await _ativo(db, numero, sinal=_sp(27, 15, 5), criado=agora - timedelta(days=10))
    assert zap_saude.A_SEM_WEBHOOK not in _codigos(await _calcular(agora))
    # Sem história nenhuma (número novo): sem base, não alarma.
    await _ativo(db, numero, sinal=_sp(28, 10), criado=agora - timedelta(hours=8))
    assert zap_saude.A_SEM_WEBHOOK not in _codigos(await _calcular(agora))


async def test_testar_conexao_ok_zera_a_conta_do_silencio(db, zap):
    _app, _c, numero = await cadastrar(db)
    agora = QUARTA_14H
    await _historia(db, numero, CHEIO)
    teste = {"teste_ok_em": (agora - timedelta(hours=1)).isoformat()}
    await _ativo(db, numero, sinal=_sp(28, 10), criado=agora - timedelta(days=10), saude=teste)
    assert zap_saude.A_SEM_WEBHOOK not in _codigos(await _calcular(agora))
    # O teste também envelhece: 4 h depois dele, calado de novo, acusa.
    teste = {"teste_ok_em": (agora - timedelta(hours=4)).isoformat()}
    await _mudar(db, AtendimentoZapNumero, numero.id, saude=teste)
    assert zap_saude.A_SEM_WEBHOOK in _codigos(await _calcular(agora))


async def test_app_inteiro_calado_acusa_antes_do_numero_sozinho(db, zap):
    """Dois números do mesmo app, cada um com mensagem em metade das horas (sozinho,
    6,6 h de folga); somados, toda hora. Os dois calados há 4 h: acusa pelo app."""
    _app, _c, um = await cadastrar(db)
    _app, _c, outro = await cadastrar(
        db, pnid="930000000000777", display="5511900000777", nome_na_barra="Outro · Zap"
    )
    agora = QUARTA_14H
    pares = [t for t in CHEIO if t.hour % 2 == 0]
    impares = [t for t in CHEIO if t.hour % 2 == 1]
    await _historia(db, um, pares)
    await _historia(db, outro, impares)
    criado = agora - timedelta(days=10)
    await _ativo(db, um, sinal=_sp(28, 10), criado=criado)
    await _ativo(db, outro, sinal=_sp(28, 10), criado=criado)
    async with _db.SessionLocal() as s:
        numeros = list((await s.execute(select(AtendimentoZapNumero))).scalars().all())
        calados = await zap_saude.silencios(s, numeros, agora)
    assert calados.por_numero[um.id].estranho and calados.por_numero[um.id].pelo_app
    assert calados.por_numero[outro.id].estranho and calados.por_numero[outro.id].pelo_app
    assert calados.geral is None  # um app só: o do app já cobre
    alertas = [x for x in await _calcular(agora) if x.codigo == zap_saude.A_SEM_WEBHOOK]
    assert len(alertas) == 2 and all("mesmo app" in a.frase for a in alertas)
    # O "Testar conexão" ok num deles vale para o app: os dois saem (revisão de 11/11).
    teste = {"teste_ok_em": (agora - timedelta(minutes=10)).isoformat()}
    await _mudar(db, AtendimentoZapNumero, um.id, saude=teste)
    assert zap_saude.A_SEM_WEBHOOK not in _codigos(await _calcular(agora))
    await _mudar(db, AtendimentoZapNumero, um.id, saude={})
    # O outro recebeu há 1 h: o app está vivo, e o primeiro (4 h) ainda está no ritmo dele.
    await _ativo(db, outro, sinal=agora - timedelta(hours=1), criado=criado)
    assert zap_saude.A_SEM_WEBHOOK not in _codigos(await _calcular(agora))


async def test_todos_os_numeros_calados_acusam_uma_linha_geral(db, zap):
    """Dois apps, um número cada, cada um com mensagem em metade das horas: sozinhos,
    4 h calados é normal; somados (toda hora), não — o webhook do DaVinci caiu?"""
    _app, _c, um = await cadastrar(db)
    _app, _c, outro = await cadastrar(
        db,
        app_id="910000000000777",
        waba="920000000000777",
        pnid="930000000000777",
        display="5511900000777",
        nome_na_barra="Outro · Zap",
    )
    agora = QUARTA_14H
    criado = agora - timedelta(days=10)
    await _historia(db, um, [t for t in CHEIO if t.hour % 2 == 0])
    await _historia(db, outro, [t for t in CHEIO if t.hour % 2 == 1])
    await _ativo(db, um, sinal=_sp(28, 10), criado=criado)
    await _ativo(db, outro, sinal=_sp(28, 10), criado=criado)
    alertas = [x for x in await _calcular(agora) if x.codigo == zap_saude.A_SEM_WEBHOOK]
    assert len(alertas) == 1 and alertas[0].numero_ref is None
    assert alertas[0].nivel == zap_saude.ERRO and "Nenhum número" in alertas[0].frase
    (lp,) = await _parado(agora)
    assert lp.tipo == vigia.TIPO_GERAL and lp.plataforma == "whatsapp"
    assert lp.motivo == vigia.MOTIVO_GERAL_ZAP and lp.desde == _sp(28, 10)
    # O "Testar conexão" não zera a linha geral: ele lê a Meta, não o nosso webhook.
    teste = {"teste_ok_em": (agora - timedelta(minutes=10)).isoformat()}
    await _mudar(db, AtendimentoZapNumero, um.id, saude=teste)
    await _mudar(db, AtendimentoZapNumero, outro.id, saude=teste)
    assert [lp.tipo for lp in await _parado(agora)] == [vigia.TIPO_GERAL]
    # Um deles recebeu há 1 h: o webhook está vivo.
    await _ativo(db, outro, sinal=agora - timedelta(hours=1), criado=criado)
    assert zap_saude.A_SEM_WEBHOOK not in _codigos(await _calcular(agora))
    assert await _parado(agora) == []


# ═══════════════════════ avisar ══════════════════════════════════════════


async def test_avisar_um_alerta_por_alarme_por_dia_para_cada_admin(db, zap, make_user):
    a1 = await make_user(role=UserRole.ADMIN)
    a2 = await make_user(role=UserRole.ADMIN)
    await make_user(role=UserRole.USER)
    alerta = zap_saude.Alerta(zap_saude.A_FILA, zap_saude.ERRO, "A fila do WhatsApp está parada")
    async with _db.SessionLocal() as s:
        assert await zap_saude.avisar(s, [alerta], agora=QUARTA_14H) == 1
        await s.commit()
    async with _db.SessionLocal() as s:
        assert await zap_saude.avisar(s, [alerta], agora=QUARTA_14H) == 0
        await s.commit()
    linhas = (await db.execute(select(Alert))).scalars().all()
    assert sorted(a.user_id for a in linhas) == sorted([a1.id, a2.id])
    assert all(a.title.startswith("WhatsApp:") for a in linhas)
    # No dia seguinte avisa de novo.
    async with _db.SessionLocal() as s:
        assert await zap_saude.avisar(s, [alerta], agora=QUARTA_14H + timedelta(days=1)) == 1
        await s.commit()


# ═══════════════════════ o cron (Meta, só GET) ═══════════════════════════


def _debug(**dados) -> dict:
    base = {
        "app_id": APP_ID,
        "is_valid": True,
        "expires_at": 0,
        "scopes": ["whatsapp_business_messaging", "whatsapp_business_management"],
        "granular_scopes": [
            {"scope": "whatsapp_business_messaging", "target_ids": [WABA]},
            {"scope": "whatsapp_business_management", "target_ids": [WABA]},
        ],
    }
    base.update(dados)
    return {"data": base}


def _meta_ok(graph, *, debug=None, inscritos=None, webhook=None) -> None:
    graph.quando("GET", f"/{V}/debug_token", debug or _debug())
    graph.quando(
        "GET",
        f"/{V}/{WABA}/subscribed_apps",
        {
            "data": inscritos
            if inscritos is not None
            else [{"whatsapp_business_api_data": {"id": APP_ID, "name": "DaVinci"}}]
        },
    )
    graph.quando(
        "GET",
        f"/{V}/{PNID}",
        {
            "id": PNID,
            "display_phone_number": "+55 11 90000-0000",
            "quality_rating": "GREEN",
            "messaging_limit_tier": "TIER_1K",
            "webhook_configuration": webhook or {},
        },
    )


async def _renovar(agora=None):
    async with _db.SessionLocal() as s:
        return await zap_saude.renovar_da_meta(s, agora=agora)


async def test_renovar_da_meta_so_get_e_grava_a_saude(db, zap, graph):
    _app, conta, numero = await cadastrar(db)
    _meta_ok(
        graph,
        inscritos=[
            {"whatsapp_business_api_data": {"id": APP_ID, "name": "DaVinci"}},
            {"whatsapp_business_api_data": {"id": "777", "name": "Duoke"}},
        ],
        webhook={"phone_number": "https://duoke.example/hook?token=segredo-do-duoke"},
    )
    r = await _renovar()
    assert r == {"contas": 1, "numeros": 1, "erros": 0}
    assert {c.method for c in graph.chamadas} == {"GET"}
    c = await db.get(AtendimentoZapConta, conta.id, populate_existing=True)
    assert c.token_conferido_em is not None and c.status == "ok" and c.token_nunca_vence
    assert c.saude["outros_apps"] == [{"id": "777", "nome": "Duoke"}]
    assert c.inscrito_em is None
    n = await db.get(AtendimentoZapNumero, numero.id, populate_existing=True)
    assert n.saude["qualidade"] == "GREEN" and n.saude["limite"] == "TIER_1K"
    # Só o HOST do outro sistema (a URL dele pode levar token).
    assert n.saude["webhook_outro"] == [{"onde": "phone_number", "host": "duoke.example"}]
    assert "segredo-do-duoke" not in str(n.saude)
    # A inspeção é uma vez por dia: a segunda rodada não pede de novo.
    antes = len([x for x in graph.chamadas if x.url.path.endswith("/debug_token")])
    await _renovar()
    assert len([x for x in graph.chamadas if x.url.path.endswith("/debug_token")]) == antes


async def test_um_numero_que_falha_nao_cala_os_outros_nem_os_alarmes(db, zap, graph, make_user):
    """O `rollback` do item que falhou expira a sessão inteira: o próximo item e o
    log não podem ler atributo expirado (MissingGreenlet derrubava a rodada e os
    alarmes ficavam mudos) — revisão de 27/10/2026."""
    await make_user(role=UserRole.ADMIN)
    # O que falha é o PRIMEIRO (cadastrado antes): o rollback dele expira o segundo.
    await cadastrar(db)
    _a, _c, segundo = await cadastrar(db, pnid="930000000000002", nome_na_barra="Segundo")
    _meta_ok(graph, inscritos=[{"whatsapp_business_api_data": {"id": "777", "name": "Duoke"}}])
    graph.quando("GET", f"/{V}/{PNID}", httpx.ConnectTimeout("a Meta não respondeu"))
    graph.quando(
        "GET",
        f"/{V}/930000000000002",
        {"id": "930000000000002", "quality_rating": "GREEN", "webhook_configuration": {}},
    )
    assert await _renovar() == {"contas": 1, "numeros": 1, "erros": 1}
    n2 = await db.get(AtendimentoZapNumero, segundo.id, populate_existing=True)
    assert n2.saude["qualidade"] == "GREEN"
    # A rodada inteira segue e avisa (o outro app inscrito).
    r = await zap_saude.rodada()
    assert r is not None and r["meta"]["erros"] == 1 and r["avisados"] >= 1


async def test_inspecao_que_recusa_marca_a_conta_e_a_que_nao_responde_alarma(db, zap, graph):
    _app, conta, _n = await cadastrar(db)
    _meta_ok(graph, debug=_debug(is_valid=False))
    await _renovar()
    c = await db.get(AtendimentoZapConta, conta.id, populate_existing=True)
    assert c.status == "token_invalido"
    # Token caído não volta à Meta.
    graph.chamadas.clear()
    await _renovar()
    assert graph.chamadas == []
    # A Meta que não responde: alarme, a conta fica como estava.
    await _mudar(db, AtendimentoZapConta, conta.id, status="ok", token_conferido_em=None)
    _meta_ok(graph)
    graph.quando("GET", f"/{V}/debug_token", httpx.ConnectTimeout("fora"))
    await _renovar()
    c = await db.get(AtendimentoZapConta, conta.id, populate_existing=True)
    assert c.status == "ok" and c.saude.get("inspecao_erro")
    assert zap_saude.A_TOKEN_NAO_CONFERIDO in _codigos(await _calcular(datetime.now(UTC)))


async def test_rodada_sai_cedo_com_a_leitura_desligada(zap, graph, monkeypatch):
    monkeypatch.setattr(zap.settings, "atendimento_zap_leitura_ativa", False)
    assert await zap_saude.rodada() is None
    assert await zap_saude.limpeza() is None
    assert await worker.atendimento_zap_saude({}) is None
    assert await worker.atendimento_zap_limpeza({}) is None
    assert graph.chamadas == []


async def test_rodada_inteira_avisa_os_admins(db, zap, graph, make_user):
    await make_user(role=UserRole.ADMIN)
    _app, conta, _n = await cadastrar(db)
    _meta_ok(graph, inscritos=[{"whatsapp_business_api_data": {"id": "777", "name": "Duoke"}}])
    r = await zap_saude.rodada()
    assert r is not None and r["avisados"] >= 1
    titulos = [a.title for a in (await db.execute(select(Alert))).scalars().all()]
    assert "WhatsApp: outro app inscrito na conta" in titulos


def test_crons_do_zap_no_worker():
    nomes = {c.name for c in worker.WorkerSettings.cron_jobs}
    assert {"cron:atendimento_zap_saude", "cron:atendimento_zap_limpeza"} <= nomes
    saude = next(
        c for c in worker.WorkerSettings.cron_jobs if c.name == "cron:atendimento_zap_saude"
    )
    assert saude.minute == {2, 17, 32, 47}
    limpeza = next(
        c for c in worker.WorkerSettings.cron_jobs if c.name == "cron:atendimento_zap_limpeza"
    )
    assert limpeza.hour == {4}
    # O worker mínimo do localhost não liga a saúde (não chama a Meta sozinho).
    assert "cron:atendimento_zap_saude" not in {c.name for c in worker.WorkerSettingsZap.cron_jobs}


# ═══════════════════════ a limpeza (retenção) ════════════════════════════


async def test_limpeza_midia_eventos_e_sobras(db, zap, tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "uploads_dir", str(tmp_path))
    app, _c, _n = await cadastrar(db)
    agora = datetime.now(UTC)
    await gravar_corpo(corpo("imagem", wamid="wamid.V", media_id="MIDV"), app=app)
    await gravar_corpo(corpo("texto", wamid="wamid.NOVO"), app=app)
    await processar()
    m = (await db.execute(select(AtendimentoZapMidia))).scalar_one()
    origem = tmp_path / "f.jpg"
    origem.write_bytes(b"\xff\xd8\xff\xe0\x00\x10JFIF\x00" + b"j" * 300)
    async with _db.SessionLocal() as s:
        assert await zap_midia.guardar_local(s, m.id, origem) == "baixada"
    m = await db.get(AtendimentoZapMidia, m.id, populate_existing=True)
    arquivo = tmp_path / m.caminho
    assert arquivo.exists()
    # Um evento processado VELHO, um pendente velho e os de hoje.
    velho = agora - timedelta(days=31)
    await gravar_corpo(corpo("texto", wamid="wamid.VELHO_PEND"), app=app)
    evs = {e.chave: e for e in await eventos(db)}
    await _mudar(db, AtendimentoZapEvento, evs["m:wamid.NOVO"].id, recebido_em=velho)
    await _mudar(db, AtendimentoZapEvento, evs["m:wamid.VELHO_PEND"].id, recebido_em=velho)
    # A sobra de um download que caiu (2 dias) e uma de agora.
    partes = tmp_path / zap_midia.PASTA_TEMP
    partes.mkdir(parents=True, exist_ok=True)
    sobra = partes / f"x{zap_midia.SUFIXO_TEMP}"
    sobra.write_bytes(b"x")
    os.utime(sobra, (time.time() - 2 * 86400,) * 2)
    em_curso = partes / f"y{zap_midia.SUFIXO_TEMP}"
    em_curso.write_bytes(b"y")
    async with _db.SessionLocal() as s:
        r = await zap_saude.limpar(s, agora=agora + timedelta(days=181))
    assert r["midias"] == 1 and r["partes"] == 1
    assert not arquivo.exists() and not sobra.exists() and em_curso.exists()
    m = await db.get(AtendimentoZapMidia, m.id, populate_existing=True)
    assert m.status == "apagada" and m.caminho is None
    chaves = {e.chave for e in await eventos(db)}
    # O processado velho saiu; o PENDENTE velho fica (apagar perderia mensagem).
    assert "m:wamid.NOVO" not in chaves
    assert "m:wamid.VELHO_PEND" in chaves
    assert r["pendentes_velhos"] == 1


async def test_limpeza_nao_apaga_evento_recente(db, zap):
    app, _c, _n = await cadastrar(db)
    await gravar_corpo(corpo("texto", wamid="wamid.HOJE"), app=app)
    await processar()
    async with _db.SessionLocal() as s:
        r = await zap_saude.limpar(s, agora=datetime.now(UTC))
    assert r["eventos"] == 0
    assert [e.chave for e in await eventos(db)] == ["m:wamid.HOJE"]


# ═══════════════════════ o scan de assinatura do worker ══════════════════


async def test_scan_de_assinatura_le_o_zap_e_o_instagram(db, zap, make_user, monkeypatch):
    await make_user(role=UserRole.ADMIN)
    app, _c, _n = await cadastrar(db)
    monkeypatch.setattr(worker, "redis", zap.redis)
    zap.redis.dados[zap_contas.CHAVE_FALHA_ASSINATURA_APP.format(app_ref=app.id)] = 9
    zap.redis.dados["webhook:meta:sig_fail_count"] = 12
    await worker.webhook_signature_alert_scan({})
    titulos = sorted(a.title for a in (await db.execute(select(Alert))).scalars().all())
    assert "WhatsApp: assinatura do webhook falhando" in titulos
    assert "Webhook do Instagram/Facebook: assinatura inválida" in titulos
    # De novo na mesma hora/dia: sem alerta em dobro.
    await worker.webhook_signature_alert_scan({})
    assert len((await db.execute(select(Alert))).scalars().all()) == len(titulos)


# ═══════════════════════ o vigia de leitura parada ═══════════════════════


async def test_vigia_classe_do_zap(db, zap):
    _app, _c, numero = await cadastrar(db)
    canal = await db.get(AtendimentoCanal, numero.canal_id)
    assert vigia.classe_do_canal(canal) == vigia.CLASSE_ZAP
    assert vigia.LIMITES[vigia.CLASSE_ZAP] == timedelta(hours=3)


async def _parado(agora) -> list:
    async with _db.SessionLocal() as s:
        return [
            lp for lp in await vigia.leitura_parada(s, agora=agora) if lp.plataforma == "whatsapp"
        ]


async def test_vigia_so_ativo_em_horario_comercial_e_pelo_ritmo(db, zap):
    _app, _c, numero = await cadastrar(db)
    agora = QUARTA_14H
    criado = agora - timedelta(days=10)
    await _historia(db, numero, CHEIO)
    await _ativo(db, numero, sinal=_sp(28, 10), criado=criado)
    # Etapa teste: não vigia.
    await _mudar(db, AtendimentoZapNumero, numero.id, etapa="teste")
    assert await _parado(agora) == []
    await _mudar(db, AtendimentoZapNumero, numero.id, etapa="ativo")
    (lp,) = await _parado(agora)
    assert lp.motivo == vigia.MOTIVO_ZAP and lp.limite_min == 180
    assert "horas comerciais" in (lp.detalhe or "")
    assert await _parado(QUARTA_03H) == []
    assert await _parado(DOMINGO_14H) == []
    await _ativo(db, numero, sinal=agora - timedelta(hours=2), criado=criado)
    assert await _parado(agora) == []


async def test_vigia_nao_acende_o_numero_quieto(db, zap):
    """O aviso de 11/11 ("Jlas2 sem ler há 15 h", com o teste dando ok): sai da faixa."""
    _app, _c, numero = await cadastrar(db)
    agora = QUARTA_14H
    await _historia(db, numero, QUIETO)
    # Calado há 4 h (dentro da janela da regra antiga, que acendia aqui).
    await _ativo(db, numero, sinal=_sp(28, 10), criado=agora - timedelta(days=10))
    assert await _parado(agora) == []
    # E com duas por dia, calado desde ontem às 15 h: também não.
    await _historia(db, numero, MEIO, conversa="meio")
    await _ativo(db, numero, sinal=_sp(27, 15, 5), criado=agora - timedelta(days=10))
    assert await _parado(agora) == []


async def test_linha_do_zap_parado_so_para_admin(client, db, zap, make_user, auth_as, monkeypatch):
    from app.routers import atendimento as rota

    monkeypatch.setattr(rota, "SO_ADMIN", False)
    _app, _c, numero = await cadastrar(db)
    await _mudar(db, AtendimentoZapNumero, numero.id, etapa="ativo")

    async def parado(session, *, nomes=None, agora=None):
        return [
            vigia.LeituraParada(
                chave=f"canal:{numero.canal_id}",
                tipo=vigia.TIPO_LOJA,
                plataforma="whatsapp",
                loja="Charlots · Zap",
                motivo=vigia.MOTIVO_ZAP,
                acao=vigia.ACAO_ZAP,
                desde=None,
                nunca_leu=False,
                minutos=200,
                limite_min=180,
                canal_id=numero.canal_id,
            )
        ]

    monkeypatch.setattr(vigia, "leitura_parada", parado)
    pode = {"atendimento": {"view": True, "edit": True}}
    auth_as(await make_user(permissions=pode, role=UserRole.ADMIN))
    r = await client.get("/api/atendimento/resumo")
    assert [lp["plataforma"] for lp in r.json()["leitura_parada"]] == ["whatsapp"]
    auth_as(await make_user(permissions=pode, role=UserRole.USER))
    r = await client.get("/api/atendimento/resumo")
    assert r.json()["leitura_parada"] == []
