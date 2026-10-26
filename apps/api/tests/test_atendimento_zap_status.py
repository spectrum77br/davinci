# ruff: noqa: S105, S106, F811  (segredos de teste; fixtures importadas do apoio)
"""Os STATUS de entrega e leitura do Zap (RF10, Onda 2) — docs/zap/desenho.md §5.6.

A resposta sai de verdade pelo caminho único (`enviar.enviar_resposta`) contra
a Graph API de MENTIRA (`tests/zap_apoio.GraphFalso`); os status chegam como
chegariam do webhook (`statuses[]`, gravados na caixa de entrada) e passam
pelo processador (`zap_entrada.processar_eventos`).

O que se garante:
- a ordem que não volta atrás: enviado (1) < entregue (2) < lido (3) <
  ouvido (4); `read` sem `delivered`; cada horário gravado UMA vez;
- `failed` vira `falhou` com a frase da tabela (`zap_erros`) no balão; um
  `failed` fora de ordem depois de entregue só fica anotado; `delivered`
  depois de `failed` desfaz a falha;
- a linha `enviando` (o envio em curso) nunca é tocada: o status espera e é
  aplicado depois;
- a CONCILIAÇÃO do ambíguo pelo `biz_opaque_callback_data`: a linha
  `revisar` (timeout) ganha o wamid e vira `enviada`/`falhou`, sem reenviar;
- o CUSTO (`pricing`) anotado uma vez por wamid, pelo `pricing.type`
  (`regular` conta; `free_*` não), com o alerta do mês;
- o efeito vindo do WEBHOOK: 131047 fecha a janela e 131050 grava o opt-out
  na hora; o FORTE (368 pausa o número, 190 derruba o token) só na 2ª
  ocorrência em wamids diferentes — na 1ª, só o alerta.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
import pytest
import pytest_asyncio
import structlog
from sqlalchemy import func, select, update

from app.models import (
    AtendimentoCanal,
    AtendimentoConversa,
    AtendimentoMensagem,
    AtendimentoZapConta,
    AtendimentoZapContato,
    AtendimentoZapEvento,
    AtendimentoZapNumero,
    User,
    UserRole,
)
from app.routers import atendimento as rota
from app.services.atendimento import canais_externos, enviar, zap_entrada, zap_erros
from tests.zap_apoio import (  # noqa: F401  (fixtures: zap, graph, envio)
    PNID,
    corpo,
    envio,
    graph,
    gravar_corpo,
    processar,
    zap,
)

URL = "/api/atendimento"
PODE_TUDO = {"atendimento": {"view": True, "edit": True}}


@pytest.fixture(autouse=True)
def _permissao_fina(monkeypatch):
    monkeypatch.setattr(rota, "SO_ADMIN", False)


@pytest_asyncio.fixture
async def pessoa(make_user, auth_as) -> User:
    u = await make_user(permissions=PODE_TUDO, role=UserRole.ADMIN)
    auth_as(u)
    return u


async def _reler(db, modelo, ident):
    return await db.get(modelo, ident, populate_existing=True)


def _agora_ts(minutos: int = 0) -> datetime:
    return (datetime.now(UTC) + timedelta(minutes=minutos)).replace(microsecond=0)


async def _status(
    envio,
    wamid: str,
    situacao: str,
    *,
    biz: str | None = None,
    minutos: int = 0,
    pricing: dict | None = None,
) -> dict:
    """Um `statuses[]` como a Meta manda, gravado e processado."""
    c = corpo("status", alvo=wamid, status=situacao, biz=biz, ts=_agora_ts(minutos))
    st = c["entry"][0]["changes"][0]["value"]["statuses"][0]
    if pricing is not None:
        st["pricing"] = pricing
    elif situacao not in ("sent", "delivered"):
        # A Meta só manda `pricing` no `sent` e no `delivered`.
        st.pop("pricing", None)
    await gravar_corpo(c, app=envio.app)
    return await processar()


async def _falhou(envio, wamid: str, code: int, *, biz: str | None = None) -> dict:
    """Um `failed` com o código da Meta (a fixture traz o 131047)."""
    c = corpo("status_falhou", alvo=wamid, biz=biz, ts=_agora_ts())
    st = c["entry"][0]["changes"][0]["value"]["statuses"][0]
    st["errors"][0]["code"] = code
    if biz is not None:
        st["biz_opaque_callback_data"] = biz
    await gravar_corpo(c, app=envio.app)
    return await processar()


async def _responder(db, envio, pessoa, texto: str) -> AtendimentoMensagem:
    m = await enviar.enviar_resposta(db, envio.conversa, texto, user=pessoa)
    assert m.status == "enviada", (m.status, m.erro)
    return m


def _entrega(m: AtendimentoMensagem) -> dict:
    return ((m.payload or {}).get("zap") or {}).get("entrega") or {}


async def _balao(client, conversa_id, mensagem_id) -> dict:
    r = await client.get(f"{URL}/conversas/{conversa_id}")
    assert r.status_code == 200, r.text
    return next(x for x in r.json()["mensagens"] if x["id"] == str(mensagem_id))


# ═══════════════════════ a ordem que não volta atrás ═════════════════════


async def test_enviado_entregue_lido_ouvido_pela_ordem(db, envio, pessoa, client):
    m = await _responder(db, envio, pessoa, "Oi! Temos sim.")
    assert m.externo_id == "wamid.OUT1"
    # A Meta devolve o nosso id em todo status (o envio mandou).
    assert envio.graph.corpos()[0]["biz_opaque_callback_data"] == str(m.id)

    await _status(envio, "wamid.OUT1", "sent", biz=str(m.id))
    m = await _reler(db, AtendimentoMensagem, m.id)
    assert (_entrega(m)["status"], _entrega(m)["rank"]) == ("sent", 1)
    enviada_em = _entrega(m)["enviada_em"]
    assert (await _balao(client, envio.conversa.id, m.id))["entrega"] == "sent"

    await _status(envio, "wamid.OUT1", "delivered", biz=str(m.id), minutos=1)
    m = await _reler(db, AtendimentoMensagem, m.id)
    assert (_entrega(m)["status"], _entrega(m)["rank"]) == ("delivered", 2)
    assert _entrega(m)["entregue_em"]

    await _status(envio, "wamid.OUT1", "read", biz=str(m.id), minutos=2)
    m = await _reler(db, AtendimentoMensagem, m.id)
    assert (_entrega(m)["status"], _entrega(m)["rank"]) == ("read", 3)
    lida_em = _entrega(m)["lida_em"]
    assert lida_em
    balao = await _balao(client, envio.conversa.id, m.id)
    assert (balao["entrega"], balao["entrega_erro"], balao["status"]) == ("read", None, "enviada")

    # `played` (áudio ouvido) é o mais alto.
    await _status(envio, "wamid.OUT1", "played", biz=str(m.id), minutos=3)
    m = await _reler(db, AtendimentoMensagem, m.id)
    assert (_entrega(m)["status"], _entrega(m)["rank"]) == ("played", 4)
    # Cada horário uma vez só: os anteriores não mudaram.
    assert (_entrega(m)["enviada_em"], _entrega(m)["lida_em"]) == (enviada_em, lida_em)
    assert m.status == "enviada"


async def test_status_atrasado_nao_volta_atras(db, envio, pessoa):
    m = await _responder(db, envio, pessoa, "Oi!")
    # Chega o `read` primeiro (sem `delivered`: a Meta às vezes pula).
    await _status(envio, "wamid.OUT1", "read", biz=str(m.id), minutos=2)
    m = await _reler(db, AtendimentoMensagem, m.id)
    assert (_entrega(m)["status"], _entrega(m)["rank"]) == ("read", 3)
    assert "entregue_em" not in _entrega(m)
    # Os atrasados (`delivered` e `sent` com hora anterior) não rebaixam.
    await _status(envio, "wamid.OUT1", "delivered", biz=str(m.id), minutos=1)
    await _status(envio, "wamid.OUT1", "sent", biz=str(m.id))
    m = await _reler(db, AtendimentoMensagem, m.id)
    assert (_entrega(m)["status"], _entrega(m)["rank"]) == ("read", 3)
    # … mas o horário deles fica anotado (uma vez).
    assert _entrega(m)["entregue_em"] and _entrega(m)["enviada_em"]


async def test_reentrega_do_mesmo_status_nao_faz_nada(db, envio, pessoa):
    m = await _responder(db, envio, pessoa, "Oi!")
    await _status(envio, "wamid.OUT1", "delivered", biz=str(m.id))
    antes = _entrega(await _reler(db, AtendimentoMensagem, m.id))
    # A Meta reenvia o mesmo `delivered`: o UNIQUE da chave já tira.
    r = await _status(envio, "wamid.OUT1", "delivered", biz=str(m.id), minutos=5)
    assert r["processados"] == 0
    assert _entrega(await _reler(db, AtendimentoMensagem, m.id)) == antes


# ═══════════════════════ falhou ══════════════════════════════════════════


async def test_failed_vira_falhou_com_a_frase_da_meta(db, envio, pessoa, client):
    m = await _responder(db, envio, pessoa, "Oi!")
    assert (await _reler(db, AtendimentoConversa, envio.conversa.id)).aguardando_resposta is False
    await _falhou(envio, "wamid.OUT1", 131026, biz=str(m.id))
    m = await _reler(db, AtendimentoMensagem, m.id)
    assert (m.status, m.erro) == ("falhou", "meta:131026")
    assert (_entrega(m)["status"], _entrega(m)["erro_code"]) == ("failed", 131026)
    balao = await _balao(client, envio.conversa.id, m.id)
    assert balao["status"] == "falhou"
    assert balao["entrega_erro"] == zap_erros.frase(131026)
    # A resposta não chegou: a conversa volta a esperar resposta (recalculada).
    assert (await _reler(db, AtendimentoConversa, envio.conversa.id)).aguardando_resposta is True
    # Nada foi reenviado.
    assert len(envio.graph.chamadas) == 1


async def test_failed_depois_de_entregue_so_fica_anotado(db, envio, pessoa):
    m = await _responder(db, envio, pessoa, "Oi!")
    await _status(envio, "wamid.OUT1", "delivered", biz=str(m.id))
    await _falhou(envio, "wamid.OUT1", 131026, biz=str(m.id))
    m = await _reler(db, AtendimentoMensagem, m.id)
    # Entregue é prova de que saiu: o `failed` fora de ordem não desfaz.
    assert (m.status, m.erro) == ("enviada", None)
    assert _entrega(m)["status"] == "delivered" and _entrega(m)["erro_code"] == 131026


async def test_entregue_depois_de_failed_desfaz_a_falha(db, envio, pessoa):
    m = await _responder(db, envio, pessoa, "Oi!")
    await _falhou(envio, "wamid.OUT1", 131026, biz=str(m.id))
    assert (await _reler(db, AtendimentoMensagem, m.id)).status == "falhou"
    await _status(envio, "wamid.OUT1", "delivered", biz=str(m.id), minutos=1)
    m = await _reler(db, AtendimentoMensagem, m.id)
    assert (m.status, m.erro) == ("enviada", None)
    assert _entrega(m)["status"] == "delivered"


# ═══════════════════════ a linha em voo e o alvo ═════════════════════════


async def test_linha_enviando_e_intocada_e_o_status_e_aplicado_depois(db, envio):
    conversa = envio.conversa
    em_voo = AtendimentoMensagem(
        conversa_id=conversa.id,
        autor="loja",
        origem="davinci_humano",
        tipo="texto",
        texto="em voo",
        status="enviando",
    )
    db.add(em_voo)
    await db.commit()
    r = await _falhou(envio, "wamid.EM_VOO", 131026, biz=str(em_voo.id))
    assert r["pendentes"] == 1 and r["processados"] == 0
    m = await _reler(db, AtendimentoMensagem, em_voo.id)
    # Um `failed` processado antes do `_gravar_resultado` não pode virar a linha.
    assert m.status == "enviando" and "zap" not in (m.payload or {})
    # O envio terminou (a Meta tinha aceitado): o status espera e vale depois.
    m.status, m.externo_id = "enviada", "wamid.EM_VOO"
    await db.commit()
    await db.execute(
        update(AtendimentoZapEvento)
        .where(AtendimentoZapEvento.processado_em.is_(None))
        .values(proxima_em=func.now())
    )
    await db.commit()
    assert (await processar())["resultados"] == {"gravado": 1}
    m = await _reler(db, AtendimentoMensagem, em_voo.id)
    assert (m.status, m.erro) == ("falhou", "meta:131026")


async def test_biz_que_nao_e_desta_conversa_nao_vale(db, envio, pessoa):
    m = await _responder(db, envio, pessoa, "Oi!")
    # Um `biz` qualquer (de outra coisa, ou forjado): não acha pela PK; o wamid
    # desconhecido também não — fica pendente (e vira `sem_alvo` em 1 h).
    r = await _status(envio, "wamid.DE_OUTRO", "read", biz=str(uuid4()))
    assert r["pendentes"] == 1
    # O `biz` que não é UUID também não quebra nada.
    r = await _status(envio, "wamid.DE_OUTRO2", "read", biz="nao-e-uuid")
    assert r["pendentes"] == 1
    assert _entrega(await _reler(db, AtendimentoMensagem, m.id)) == {}


async def test_sem_biz_acha_pelo_contato_e_pelo_wamid(db, envio, pessoa):
    m = await _responder(db, envio, pessoa, "Oi!")
    # Status sem `biz_opaque_callback_data` (ex.: mandado por outra versão):
    # o `recipient_id` → contato → conversa → (conversa, wamid).
    await _status(envio, "wamid.OUT1", "delivered")
    assert _entrega(await _reler(db, AtendimentoMensagem, m.id))["status"] == "delivered"


# ═══════════════════════ a conciliação do ambíguo ════════════════════════


async def _ambiguo(db, envio, pessoa, texto: str = "Oi!") -> AtendimentoMensagem:
    envio.graph.mensagens(resposta=httpx.ReadTimeout("a Meta não respondeu"))
    m = await enviar.enviar_resposta(db, envio.conversa, texto, user=pessoa)
    assert (m.status, m.externo_id) == ("revisar", None)
    envio.graph.mensagens()  # volta ao caminho feliz
    return m


async def test_biz_opaque_concilia_o_revisar_que_saiu(db, envio, pessoa, client):
    m = await _ambiguo(db, envio, pessoa)
    await _status(envio, "wamid.REAL", "sent", biz=str(m.id))
    m = await _reler(db, AtendimentoMensagem, m.id)
    assert (m.status, m.erro, m.externo_id) == ("enviada", None, "wamid.REAL")
    assert m.payload["zap"]["conciliado"] is True
    assert m.enviada_em is not None
    # Nada foi reenviado (o POST foi um só, o que deu timeout).
    assert len(envio.graph.chamadas) == 1
    # Os status seguintes já acham pelo wamid também.
    await _status(envio, "wamid.REAL", "read", minutos=1)
    assert _entrega(await _reler(db, AtendimentoMensagem, m.id))["status"] == "read"
    assert (await _balao(client, envio.conversa.id, m.id))["entrega"] == "read"


async def test_biz_opaque_concilia_o_revisar_que_falhou(db, envio, pessoa):
    m = await _ambiguo(db, envio, pessoa)
    await _falhou(envio, "wamid.REAL_FALHOU", 131026, biz=str(m.id))
    m = await _reler(db, AtendimentoMensagem, m.id)
    assert (m.status, m.erro, m.externo_id) == ("falhou", "meta:131026", "wamid.REAL_FALHOU")
    assert m.payload["zap"]["conciliado"] is True


async def test_revisar_marcada_nao_saiu_e_conciliada_pelo_status(db, envio, pessoa, client):
    """Revisão (26/10/2026): na Cloud API não há caixa da Meta onde conferir.
    A `revisar` que alguém marcou "Não saiu" e depois recebe o status (pelo
    `biz_opaque`) SAIU: volta a `enviada`, ganha o wamid e fica conciliada —
    até o `sent` (que não desfaz uma falha da Meta) desfaz a marcação."""
    m = await _ambiguo(db, envio, pessoa)
    r = await client.post(f"{URL}/mensagens/{m.id}/conferir", json={"saiu": False})
    assert r.status_code == 200, r.text
    m = await _reler(db, AtendimentoMensagem, m.id)
    assert (m.status, m.erro) == ("falhou", "conferido: não saiu")
    with structlog.testing.capture_logs() as logs:
        await _status(envio, "wamid.REAL", "sent", biz=str(m.id))
    m = await _reler(db, AtendimentoMensagem, m.id)
    assert (m.status, m.erro, m.externo_id) == ("enviada", None, "wamid.REAL")
    assert m.payload["zap"]["conciliado"] is True
    assert any(x.get("event") == "zap_conferido_nao_saiu_mas_saiu" for x in logs)
    assert len(envio.graph.chamadas) == 1  # nada reenviado


async def test_conciliacao_nao_rouba_o_wamid_de_outra_linha(db, envio, pessoa):
    ok = await _responder(db, envio, pessoa, "Primeira")
    m = await _ambiguo(db, envio, pessoa, "Segunda")
    # Um status com o `biz` da ambígua mas com o wamid que JÁ é da primeira
    # (defeito do lado de lá): concilia o estado, mas não duplica o wamid.
    await _status(envio, ok.externo_id, "sent", biz=str(m.id))
    m = await _reler(db, AtendimentoMensagem, m.id)
    assert m.status == "enviada" and m.externo_id is None
    assert m.payload["zap"]["conciliado"] is True


# ═══════════════════════ o custo, uma vez por wamid ══════════════════════


_REGULAR = {"billable": True, "pricing_model": "PMP", "category": "service", "type": "regular"}
_UTILIDADE = {"billable": True, "pricing_model": "PMP", "category": "utility", "type": "regular"}
_GRATIS = {
    "billable": False,
    "pricing_model": "PMP",
    "category": "service",
    "type": "free_customer_service",
}


async def test_custo_uma_vez_por_wamid_pelo_tipo(db, envio, pessoa, monkeypatch):
    # Os marcos do alerta do mês, pequenos para o teste (o real: 800 e 1.000).
    monkeypatch.setattr(zap_entrada, "ALERTAS_RESPOSTAS_MES", (1, 2))
    m1 = await _responder(db, envio, pessoa, "Primeira")
    m2 = await _responder(db, envio, pessoa, "Segunda")
    with structlog.testing.capture_logs() as logs:
        await _status(envio, "wamid.OUT1", "sent", biz=str(m1.id), pricing=_REGULAR)
        # O `delivered` repete o `pricing` (outra categoria, até): não conta de novo.
        await _status(
            envio, "wamid.OUT1", "delivered", biz=str(m1.id), minutos=1, pricing=_UTILIDADE
        )
        # A segunda saiu grátis (`free_customer_service`): anotada, não cobrada.
        await _status(envio, "wamid.OUT2", "sent", biz=str(m2.id), pricing=_GRATIS)
    e1 = _entrega(await _reler(db, AtendimentoMensagem, m1.id))
    e2 = _entrega(await _reler(db, AtendimentoMensagem, m2.id))
    assert (e1["categoria"], e1["tipo_preco"]) == ("service", "regular")
    assert e1["custo_em"]
    assert (e2["categoria"], e2["tipo_preco"]) == ("service", "free_customer_service")
    # O "N de 1.000 grátis" conta TODA resposta de atendimento (a grátis
    # também — revisão de 26/10/2026): o aviso é antes de a franquia acabar.
    alertas = [x for x in logs if x.get("event") == "zap_respostas_mes"]
    assert [(a["respostas"], a["cobradas"]) for a in alertas] == [(1, 1), (2, 1)]
    # `billable` não é lido (a Meta vai remover): só o `pricing.type`.
    assert "billable" not in e1


# ═══════════════════════ o efeito vindo do webhook ═══════════════════════


async def test_131047_pelo_webhook_fecha_a_janela_e_a_trava_12_recusa(db, envio, pessoa):
    m = await _responder(db, envio, pessoa, "Oi!")
    await _falhou(envio, "wamid.OUT1", 131047, biz=str(m.id))
    m = await _reler(db, AtendimentoMensagem, m.id)
    assert (m.status, m.erro) == ("falhou", "meta:131047")
    conversa = await _reler(db, AtendimentoConversa, envio.conversa.id)
    assert conversa.pode_enviar_ate <= datetime.now(UTC)
    recusa = await enviar.motivo_para_nao_enviar(db, conversa)
    assert recusa is not None and recusa.code == "fora_da_janela"


async def test_131047_processado_depois_da_mensagem_nova_nao_tranca_a_janela(db, envio, pessoa):
    """Revisão (26/10/2026): o `failed` 131047 da resposta M1 chega atrasado
    (adiado pela linha `enviando` ou reentregue) DEPOIS de o cliente escrever
    de novo. A janela que ele reabriu continua aberta: o fechamento vale no
    instante da falha, e o anterior à última mensagem do cliente não conta."""
    m = await _responder(db, envio, pessoa, "Oi!")
    # O cliente escreve (processado antes do status).
    await gravar_corpo(corpo("texto", wamid="wamid.VOLTOU", ts=_agora_ts()), app=envio.app)
    assert (await processar())["processados"] == 1
    # O `failed` de M1, com o timestamp de 2 min atrás (quando a Meta recusou).
    c = corpo("status_falhou", alvo="wamid.OUT1", biz=str(m.id), ts=_agora_ts(-2))
    c["entry"][0]["changes"][0]["value"]["statuses"][0]["errors"][0]["code"] = 131047
    await gravar_corpo(c, app=envio.app)
    await processar()
    m = await _reler(db, AtendimentoMensagem, m.id)
    assert (m.status, m.erro) == ("falhou", "meta:131047")
    conversa = await _reler(db, AtendimentoConversa, envio.conversa.id)
    assert conversa.pode_enviar_ate <= datetime.now(UTC) - timedelta(minutes=1)
    assert await enviar.motivo_para_nao_enviar(db, conversa) is None


async def test_131050_pelo_webhook_grava_o_opt_out_na_hora(db, envio, pessoa):
    m = await _responder(db, envio, pessoa, "Oi!")
    await _falhou(envio, "wamid.OUT1", 131050, biz=str(m.id))
    contato = await _reler(db, AtendimentoZapContato, UUID(envio.conversa.externo_id))
    assert contato.opt_out_tipo == "marketing" and contato.opt_out_em is not None


async def test_368_pelo_webhook_so_pausa_na_segunda_ocorrencia(db, envio, pessoa):
    m1 = await _responder(db, envio, pessoa, "Primeira")
    m2 = await _responder(db, envio, pessoa, "Segunda")
    with structlog.testing.capture_logs() as logs:
        await _falhou(envio, "wamid.OUT1", 368, biz=str(m1.id))
    numero = await _reler(db, AtendimentoZapNumero, envio.numero.id)
    # 1ª vez pelo webhook: só o alerta (um `failed` forjado não pausa o número).
    assert numero.envio_ligado is True and numero.saude_status != "restrito"
    assert any(x.get("event") == "zap_efeito_forte_alerta" for x in logs)
    assert (await _reler(db, AtendimentoMensagem, m1.id)).status == "falhou"
    # 2ª, em OUTRO wamid, nas 24 h: pausa.
    await _falhou(envio, "wamid.OUT2", 368, biz=str(m2.id))
    numero = await _reler(db, AtendimentoZapNumero, envio.numero.id)
    assert numero.envio_ligado is False and numero.saude_status == "restrito"
    assert numero.saude["pausado_pela_meta"]["origem"] == "webhook"
    recusa = await enviar.motivo_para_nao_enviar(
        db, await _reler(db, AtendimentoConversa, envio.conversa.id)
    )
    assert recusa is not None and recusa.code == "zap_numero_pausado"


async def test_190_pelo_webhook_so_derruba_o_token_na_segunda(db, envio, pessoa):
    m1 = await _responder(db, envio, pessoa, "Primeira")
    m2 = await _responder(db, envio, pessoa, "Segunda")
    await _falhou(envio, "wamid.OUT1", 190, biz=str(m1.id))
    assert (await _reler(db, AtendimentoZapConta, envio.conta.id)).status != "token_invalido"
    await _falhou(envio, "wamid.OUT2", 190, biz=str(m2.id))
    conta = await _reler(db, AtendimentoZapConta, envio.conta.id)
    assert conta.status == "token_invalido" and conta.ultimo_erro_code == 190


async def test_ocorrencias_contam_so_o_codigo_e_o_numero(db, envio, pessoa):
    """Dois `failed` de códigos DIFERENTES não somam para o efeito forte."""
    m1 = await _responder(db, envio, pessoa, "Primeira")
    m2 = await _responder(db, envio, pessoa, "Segunda")
    await _falhou(envio, "wamid.OUT1", 368, biz=str(m1.id))
    await _falhou(envio, "wamid.OUT2", 131048, biz=str(m2.id))
    numero = await _reler(db, AtendimentoZapNumero, envio.numero.id)
    assert numero.envio_ligado is True


# ═══════════════════════ o aviso do erro da Meta fica na barra ═══════════
# Revisão da Onda 2 (26/10/2026): o 131042 (pagamento) e o 131045/133010
# (não registrado) põem `saude_status='erro'`; o `_saude_ok` só segurava o
# `restrito` e devolvia o canal a `ok` no mesmo processamento — o número
# ficava pausado sem aviso na barra.


async def _canal_do_numero(db) -> AtendimentoCanal:
    return (
        await db.execute(
            select(AtendimentoCanal)
            .where(AtendimentoCanal.externo_ref == canais_externos.ref_do_zap(PNID))
            .execution_options(populate_existing=True)
        )
    ).scalar_one()


async def test_131042_pelo_webhook_pausa_e_a_barra_mantem_o_aviso(db, envio, pessoa):
    m1 = await _responder(db, envio, pessoa, "Primeira")
    m2 = await _responder(db, envio, pessoa, "Segunda")
    await _falhou(envio, "wamid.OUT1", 131042, biz=str(m1.id))
    await _falhou(envio, "wamid.OUT2", 131042, biz=str(m2.id))
    numero = await _reler(db, AtendimentoZapNumero, envio.numero.id)
    assert (numero.envio_ligado, numero.saude_status) == (False, "erro")
    frase = zap_erros.frase(131042)
    canal = await _canal_do_numero(db)
    assert (canal.status, canal.ultimo_erro) == ("erro", frase)
    # O "oi" seguinte do cliente não apaga o aviso.
    await gravar_corpo(corpo("texto", wamid="wamid.OI2", ts=_agora_ts()), app=envio.app)
    assert (await processar())["processados"] == 1
    canal = await _canal_do_numero(db)
    assert (canal.status, canal.ultimo_erro) == ("erro", frase)
    # A trava 7 diz que foi a Meta (e o porquê), não "pausado no DaVinci".
    recusa = await enviar.motivo_para_nao_enviar(
        db, await _reler(db, AtendimentoConversa, envio.conversa.id)
    )
    assert recusa.code == "zap_numero_pausado"
    assert frase in recusa.detail and "por aviso da Meta" in recusa.detail
    # O admin religa e a resposta sai: o erro passou, a barra volta a `ok`.
    await db.execute(
        update(AtendimentoZapNumero)
        .where(AtendimentoZapNumero.id == envio.numero.id)
        .values(envio_ligado=True)
    )
    await db.commit()
    await _responder(db, envio, pessoa, "Terceira")
    numero = await _reler(db, AtendimentoZapNumero, envio.numero.id)
    assert numero.saude_status == "ok"
    assert not {"erro_meta", "pausado_pela_meta"} & set(numero.saude or {})
    await _status(envio, "wamid.OUT3", "sent")
    assert (await _canal_do_numero(db)).status == "ok"


async def test_131045_pela_api_mantem_o_aviso_no_oi_seguinte(db, envio, pessoa):
    envio.graph.mensagens(resposta=(400, {"error": {"code": 131045, "message": "x"}}))
    m = await enviar.enviar_resposta(db, envio.conversa, "Oi!", user=pessoa)
    assert (m.status, m.erro) == ("falhou", "meta:131045")
    numero = await _reler(db, AtendimentoZapNumero, envio.numero.id)
    assert numero.saude_status == "erro"
    await gravar_corpo(corpo("texto", wamid="wamid.OI2", ts=_agora_ts()), app=envio.app)
    assert (await processar())["processados"] == 1
    canal = await _canal_do_numero(db)
    assert (canal.status, canal.ultimo_erro) == ("erro", zap_erros.frase(131045))
