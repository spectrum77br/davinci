# ruff: noqa: S105, S106, F811  (segredos de teste; fixtures importadas do apoio)
"""O ENVIO do Zap (WhatsApp Cloud API, RF10, Onda 2) — docs/zap/desenho.md §6.

A Graph API é de MENTIRA (`tests/zap_apoio.GraphFalso`, `httpx.MockTransport`):
nenhum teste fala com a Meta, e a chamada não prevista reprova.

O que se garante:
- as chaves nascem DESLIGADAS (envio, tique azul) e, desligadas, NADA sai;
- cada trava do §6.3 BARRA, NA ORDEM (1 só pessoa · 2 simulador em produção ·
  3 freio único · 4 chave do Zap · 5 conversa bloqueada · 6 número sumido ·
  7 número pausado · 8 observar · 9 credencial · 10 contato bloqueado · 11
  lista de teste · 12 janela de 24 h · 13 laço · 14 teto do número), e com
  todas abertas a faixa diz "pode";
- fail-closed fora de produção (sem lista de teste nada sai); a lista casa
  com e sem o 9º dígito; o "pausar todos" vale na hora; a janela com o
  `min(contato + 24 h, conversa.pode_enviar_ate)`; o laço; o teto;
- o sucesso: o wamid no `externo_id`, o `message_status` em
  `payload.envio.zap`, o `biz_opaque_callback_data` no corpo, o token SÓ no
  header (nunca na URL, no corpo, no log nem no `repr`);
- os erros da Meta (§6.5): a tabela inteira; 131047 fecha a janela e a trava
  12 passa a recusar; 190 marca o token; 368 pausa o número — e o efeito
  sobrevive a um rollback do `_gravar_resultado`; timeout e 5xx sem código =
  `revisar` sem retentativa; 4xx sem código = `falhou`;
- o simulador com a exceção `whatsapp` (só o Zap sai de verdade) e em
  produção (recusa); a IA e a automática nunca;
- o tique azul (decisão de 26/10/2026), atrás da chave própria: só admin, só
  com o envio ligado, a última mensagem do cliente, uma vez.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
import structlog
from sqlalchemy import delete, select, update
from sqlalchemy.exc import DBAPIError

import app.db as _db
from app.config import get_settings
from app.models import (
    AtendimentoCanal,
    AtendimentoConversa,
    AtendimentoMensagem,
    AtendimentoZapConta,
    AtendimentoZapContato,
    AtendimentoZapNumero,
    User,
    UserRole,
)
from app.routers import atendimento as rota
from app.services.atendimento import (
    canais_externos,
    enviar,
    lida,
    zap_api,
    zap_contas,
    zap_erros,
)
from app.services.atendimento import zap as zap_svc  # `zap` é a fixture do apoio
from app.services.atendimento.constantes import (
    MOTIVO_ZAP_SEM_ENVIO,
    MOTIVO_ZAP_SO_PESSOA,
    ORIGEM_AUTO,
    ORIGEM_HUMANO,
    ORIGEM_IA,
    ResultadoEnvio,
    motivo_canal_sem_envio,
)
from app.services.atendimento.enviar import EnvioRecusado
from tests.zap_apoio import (  # noqa: F401  (fixtures: zap, graph, envio)
    PNID,
    T0,
    WA_ID,
    RedisFalso,
    conversa_do_numero,
    corpo,
    envio,
    graph,
    gravar_corpo,
    processar,
    zap,
)

TOKEN = "token-de-mentira"  # o do `zap_apoio.cadastrar` (cifrado no banco)
URL = "/api/atendimento"
PODE_TUDO = {"atendimento": {"view": True, "edit": True}}
CAMINHO_POST = f"/v26.0/{PNID}/messages"
# O mesmo celular SEM o 9º dígito (como a Meta às vezes manda).
WA_ID_SEM_9 = "551187654321"


@pytest.fixture(autouse=True)
def _permissao_fina(monkeypatch):
    monkeypatch.setattr(rota, "SO_ADMIN", False)


@pytest_asyncio.fixture
async def pessoa(make_user, auth_as) -> User:
    u = await make_user(permissions=PODE_TUDO, role=UserRole.ADMIN)
    auth_as(u)
    return u


async def _conversa(db) -> AtendimentoConversa:
    return await conversa_do_numero(db)


async def _reler(db, modelo, ident):
    return await db.get(modelo, ident, populate_existing=True)


async def _contato(db, conversa) -> AtendimentoZapContato:
    from uuid import UUID

    return await _reler(db, AtendimentoZapContato, UUID(conversa.externo_id))


async def _canal(db) -> AtendimentoCanal:
    return (
        await db.execute(
            select(AtendimentoCanal)
            .where(AtendimentoCanal.externo_ref == canais_externos.ref_do_zap(PNID))
            .execution_options(populate_existing=True)
        )
    ).scalar_one()


async def _motivo(db, conversa, origem=ORIGEM_HUMANO):
    recusa = await enviar.motivo_para_nao_enviar(db, conversa, origem=origem)
    return recusa.code if recusa is not None else None


# ═══════════════════════ as chaves nascem desligadas ══════════════════════


def test_as_chaves_do_envio_nascem_desligadas():
    from app.config import Settings

    padrao = Settings.model_fields
    assert padrao["atendimento_zap_envio_ativo"].default is False
    assert padrao["atendimento_zap_marcar_lida"].default is False
    assert padrao["atendimento_zap_destinos_teste"].default == ""
    assert padrao["atendimento_zap_max_contato_10min"].default == 8
    assert padrao["atendimento_zap_max_dia_numero"].default == 300
    assert padrao["atendimento_envio_ativo"].default is False
    # O número nasce sem envio (o botão "pausar" ligado).
    assert AtendimentoZapNumero.__table__.c.envio_ligado.server_default.arg.text == "false"


async def test_chave_do_zap_desligada_nada_sai(db, envio, pessoa, monkeypatch):
    monkeypatch.setattr(envio.settings, "atendimento_zap_envio_ativo", False)
    with pytest.raises(EnvioRecusado) as e:
        await enviar.enviar_resposta(db, envio.conversa, "Oi, tudo bem?", user=pessoa)
    assert e.value.code == "zap_envio_desligado"
    assert envio.graph.chamadas == []
    # Nem linha em voo ficou.
    assert (
        await db.scalar(
            select(AtendimentoMensagem.id).where(
                AtendimentoMensagem.conversa_id == envio.conversa.id,
                AtendimentoMensagem.autor == "loja",
            )
        )
    ) is None


async def test_cinto_do_adaptador_nada_sai_com_a_chave_desligada_no_meio(db, envio, monkeypatch):
    """O `_destino_zap` passou e a chave caiu antes da Meta: o adaptador confere de novo."""
    destino = await enviar._destino(db, envio.conversa, origem=ORIGEM_HUMANO)
    assert destino.zap is not None and destino.integration is None
    monkeypatch.setattr(envio.settings, "atendimento_zap_envio_ativo", False)
    r = await zap_svc.enviar_texto(db, envio.conversa, destino.zap, "Oi", mensagem_id=None)
    assert (r.ok, r.ambiguo, r.erro) == (False, False, "zap_cinto:envio_desligado")
    monkeypatch.setattr(envio.settings, "atendimento_zap_envio_ativo", True)
    monkeypatch.setattr(envio.settings, "atendimento_zap_destinos_teste", "5511900001111")
    fora = SimpleNamespace(
        **{**destino.zap.__dict__, "destinos_teste": zap_svc.lista_de_teste(envio.numero)}
    )
    r = await zap_svc.enviar_texto(db, envio.conversa, fora, "Oi", mensagem_id=None)
    assert r.erro == "zap_cinto:fora_dos_testes"
    fechada = SimpleNamespace(
        **{**destino.zap.__dict__, "janela_ate": datetime.now(UTC) - timedelta(seconds=1)}
    )
    r = await zap_svc.enviar_texto(db, envio.conversa, fechada, "Oi", mensagem_id=None)
    assert r.erro in ("zap_cinto:fora_dos_testes", "zap_cinto:fora_da_janela")
    assert envio.graph.chamadas == []


# ═══════════════════════ as travas, NA ORDEM (§6.3) ═══════════════════════


async def test_cada_trava_barra_na_ordem(db, envio, monkeypatch):
    """Tudo quebrado ao mesmo tempo; conserta uma por uma, na ordem do §6.3:
    a recusa é sempre a PRÓXIMA da lista. No fim, a faixa diz "pode"."""
    s = envio.settings
    conversa = envio.conversa
    contato = await _contato(db, conversa)
    numero_id, conta_id = envio.numero.id, envio.conta.id
    ultima_ok = contato.ultima_recebida_em
    env_original = s.env

    async def numero_valores(**v):
        await db.execute(
            update(AtendimentoZapNumero).where(AtendimentoZapNumero.id == numero_id).values(**v)
        )
        await db.commit()

    async def laco(sim: bool):
        if sim:
            for i in range(8):
                db.add(
                    AtendimentoMensagem(
                        conversa_id=conversa.id,
                        autor="loja",
                        origem=ORIGEM_HUMANO,
                        tipo="texto",
                        texto=f"tentativa {i}",
                        status="falhou",
                        erro="meta:131026",
                    )
                )
        else:
            await db.execute(
                delete(AtendimentoMensagem).where(
                    AtendimentoMensagem.conversa_id == conversa.id,
                    AtendimentoMensagem.autor == "loja",
                )
            )
        await db.commit()

    # ── quebra tudo ──
    monkeypatch.setattr(s, "atendimento_simulador", True)
    monkeypatch.setattr(s, "env", "production")
    monkeypatch.setattr(s, "atendimento_envio_ativo", False)
    monkeypatch.setattr(s, "atendimento_zap_envio_ativo", False)
    conversa.situacao = "bloqueada"
    conversa.bloqueio_motivo = "o WhatsApp bloqueou"
    await db.commit()
    await numero_valores(phone_number_id="930000000000999", envio_ligado=False, teto_dia=0)
    await db.execute(
        update(AtendimentoCanal)
        .where(AtendimentoCanal.externo_ref == canais_externos.ref_do_zap(PNID))
        .values(modo="observar")
    )
    await db.execute(
        update(AtendimentoZapConta)
        .where(AtendimentoZapConta.id == conta_id)
        .values(status="token_invalido")
    )
    await db.execute(
        update(AtendimentoZapContato)
        .where(AtendimentoZapContato.id == contato.id)
        .values(bloqueado=True, ultima_recebida_em=datetime.now(UTC) - timedelta(hours=25))
    )
    await db.commit()
    monkeypatch.setattr(s, "atendimento_zap_destinos_teste", "5511900001111")
    await laco(True)

    origem = ORIGEM_IA

    async def conserta_1():
        nonlocal origem
        origem = ORIGEM_HUMANO

    async def conserta_2():
        monkeypatch.setattr(s, "atendimento_simulador", False)
        monkeypatch.setattr(s, "env", env_original)

    async def conserta_3():
        monkeypatch.setattr(s, "atendimento_envio_ativo", True)

    async def conserta_4():
        monkeypatch.setattr(s, "atendimento_zap_envio_ativo", True)

    async def conserta_5():
        conversa.situacao = "aberta"
        conversa.bloqueio_motivo = None
        await db.commit()

    async def conserta_6():
        await numero_valores(phone_number_id=PNID)

    async def conserta_7():
        await numero_valores(envio_ligado=True)

    async def conserta_8():
        await db.execute(
            update(AtendimentoCanal)
            .where(AtendimentoCanal.externo_ref == canais_externos.ref_do_zap(PNID))
            .values(modo="humano")
        )
        await db.commit()

    async def conserta_9():
        await db.execute(
            update(AtendimentoZapConta)
            .where(AtendimentoZapConta.id == conta_id)
            .values(status="ok")
        )
        await db.commit()

    async def conserta_10():
        await db.execute(
            update(AtendimentoZapContato)
            .where(AtendimentoZapContato.id == contato.id)
            .values(bloqueado=False)
        )
        await db.commit()

    async def conserta_11():
        monkeypatch.setattr(s, "atendimento_zap_destinos_teste", WA_ID)

    async def conserta_12():
        await db.execute(
            update(AtendimentoZapContato)
            .where(AtendimentoZapContato.id == contato.id)
            .values(ultima_recebida_em=ultima_ok)
        )
        await db.commit()

    async def conserta_13():
        await laco(False)

    async def conserta_14():
        await numero_valores(teto_dia=None)

    ordem = [
        ("canal_sem_envio", conserta_1),
        ("simulador_em_producao", conserta_2),
        ("envio_desligado", conserta_3),
        ("zap_envio_desligado", conserta_4),
        ("conversa_bloqueada", conserta_5),
        ("sem_integracao", conserta_6),
        ("zap_numero_pausado", conserta_7),
        ("canal_em_observacao", conserta_8),
        ("zap_sem_credencial", conserta_9),
        ("zap_contato_bloqueado", conserta_10),
        ("zap_fora_dos_testes", conserta_11),
        ("fora_da_janela", conserta_12),
        ("zap_laco", conserta_13),
        ("zap_teto_dia", conserta_14),
    ]
    vistos = []
    for esperado, conserta in ordem:
        recusa = await enviar.motivo_para_nao_enviar(db, conversa, origem=origem)
        assert recusa is not None, f"esperava {esperado}"
        vistos.append(recusa.code)
        assert recusa.code == esperado, (vistos, recusa.detail)
        assert isinstance(recusa.detail, str) and recusa.detail
        await conserta()
    # Tudo aberto: a faixa e o botão dizem "pode".
    assert await _motivo(db, conversa) is None
    # E nada foi à Meta em nenhuma das recusas.
    assert envio.graph.chamadas == []


async def test_frases_das_travas_do_zap(db, envio, monkeypatch):
    recusa = await enviar.motivo_para_nao_enviar(db, envio.conversa, origem=ORIGEM_IA)
    assert (recusa.code, recusa.detail) == ("canal_sem_envio", MOTIVO_ZAP_SO_PESSOA)
    recusa = await enviar.motivo_para_nao_enviar(db, envio.conversa, origem=ORIGEM_AUTO)
    assert (recusa.code, recusa.detail) == ("canal_sem_envio", MOTIVO_ZAP_SO_PESSOA)
    monkeypatch.setattr(envio.settings, "atendimento_zap_envio_ativo", False)
    recusa = await enviar.motivo_para_nao_enviar(db, envio.conversa)
    assert recusa.detail == "Zap: o envio pelo DaVinci está desligado. Responda pelo Duoke."


async def test_etapa_que_nao_envia_e_pausar(db, envio):
    for etapa in ("preparando", "aguardando_duoke", "migrando", "pausado", "desligado"):
        await db.execute(
            update(AtendimentoZapNumero)
            .where(AtendimentoZapNumero.id == envio.numero.id)
            .values(etapa=etapa)
        )
        await db.commit()
        assert await _motivo(db, envio.conversa) == "zap_numero_pausado", etapa
    for etapa in ("teste", "ativo"):
        await db.execute(
            update(AtendimentoZapNumero)
            .where(AtendimentoZapNumero.id == envio.numero.id)
            .values(etapa=etapa)
        )
        await db.commit()
        assert await _motivo(db, envio.conversa) is None, etapa


async def test_pausar_todos_vale_na_hora(db, envio, pessoa):
    assert await _motivo(db, envio.conversa) is None
    # O "pausar todos" de OUTRA sessão (a tela, o script): sem reiniciar nada.
    async with _db.SessionLocal() as outra:
        await outra.execute(update(AtendimentoZapNumero).values(envio_ligado=False))
        await outra.commit()
    assert await _motivo(db, envio.conversa) == "zap_numero_pausado"
    with pytest.raises(EnvioRecusado) as e:
        await enviar.enviar_resposta(db, envio.conversa, "Oi!", user=pessoa)
    assert e.value.code == "zap_numero_pausado"
    assert envio.graph.chamadas == []


async def test_fail_closed_fora_de_producao_sem_lista_de_teste(db, envio, monkeypatch):
    monkeypatch.setattr(envio.settings, "atendimento_zap_destinos_teste", "")
    assert not envio.settings.is_prod
    assert await _motivo(db, envio.conversa) == "zap_fora_dos_testes"
    # A lista do NÚMERO também serve (e também só restringe).
    await db.execute(
        update(AtendimentoZapNumero)
        .where(AtendimentoZapNumero.id == envio.numero.id)
        .values(destinos_teste=[WA_ID])
    )
    await db.commit()
    assert await _motivo(db, envio.conversa) is None
    await db.execute(
        update(AtendimentoZapNumero)
        .where(AtendimentoZapNumero.id == envio.numero.id)
        .values(destinos_teste=["5511900001111"])
    )
    await db.commit()
    assert await _motivo(db, envio.conversa) == "zap_fora_dos_testes"
    # Em produção, sem lista nenhuma, todos recebem (a lista só restringe).
    await db.execute(
        update(AtendimentoZapNumero)
        .where(AtendimentoZapNumero.id == envio.numero.id)
        .values(destinos_teste=[])
    )
    await db.commit()
    monkeypatch.setattr(envio.settings, "env", "production")
    assert await _motivo(db, envio.conversa) is None


def test_lista_de_teste_casa_com_e_sem_o_9o_digito(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "atendimento_zap_destinos_teste", f" {WA_ID_SEM_9} , +55 (21) 3333-4444")
    numero = SimpleNamespace(destinos_teste=[])
    lista = zap_svc.lista_de_teste(numero)
    assert zap_contas.normalizar_wa_id(WA_ID) in lista
    assert zap_svc.destino_liberado(WA_ID, lista, producao=False)
    assert zap_svc.destino_liberado(WA_ID_SEM_9, lista, producao=False)
    assert zap_svc.destino_liberado("552133334444", lista, producao=False)  # fixo: sem 9
    assert not zap_svc.destino_liberado("5511900001111", lista, producao=False)
    monkeypatch.setattr(s, "atendimento_zap_destinos_teste", WA_ID)
    lista = zap_svc.lista_de_teste(numero)
    assert zap_svc.destino_liberado(WA_ID_SEM_9, lista, producao=False)
    # Sem lista: só em produção.
    assert not zap_svc.destino_liberado(WA_ID, frozenset(), producao=False)
    assert zap_svc.destino_liberado(WA_ID, frozenset(), producao=True)
    assert not zap_svc.destino_liberado(None, frozenset(), producao=True)


async def test_contato_sem_telefone_so_bsuid_nao_sai(db, envio):
    contato = await _contato(db, envio.conversa)
    await db.execute(
        update(AtendimentoZapContato)
        .where(AtendimentoZapContato.id == contato.id)
        .values(wa_id=None, wa_id_norm=None, bsuid="BR.so-bsuid")
    )
    await db.commit()
    assert await _motivo(db, envio.conversa) == "zap_contato_sem_telefone"


async def test_contato_apagado_lgpd_nao_sai(db, envio):
    contato = await _contato(db, envio.conversa)
    await db.execute(
        update(AtendimentoZapContato)
        .where(AtendimentoZapContato.id == contato.id)
        .values(apagado_em=datetime.now(UTC))
    )
    await db.commit()
    assert await _motivo(db, envio.conversa) == "zap_contato_bloqueado"


def test_janela_uma_fonte_so_com_o_min():
    agora = datetime.now(UTC)
    ultima = agora - timedelta(hours=2)
    # Sem nada da conversa: a do contato (+24 h − 60 s).
    assert zap_svc.fim_da_janela(ultima, None) == ultima + timedelta(hours=24) - timedelta(
        seconds=60
    )
    # O 131047 fechou a conversa AGORA: vale o menor.
    assert zap_svc.fim_da_janela(ultima, agora) == agora - timedelta(seconds=60)
    # Nunca recebeu: nunca abriu.
    assert zap_svc.fim_da_janela(None, agora + timedelta(hours=5)) is None
    # Fechada ANTES da última mensagem do cliente: ela reabriu (o status da
    # falha processado depois da mensagem nova — revisão de 26/10/2026).
    assert zap_svc.fim_da_janela(agora, agora - timedelta(minutes=2)) == agora + timedelta(
        hours=24
    ) - timedelta(seconds=60)


async def test_janela_de_24h_no_servidor(db, envio):
    contato = await _contato(db, envio.conversa)
    # 23 h 58 min atrás: ainda dá (a margem é de 60 s).
    await db.execute(
        update(AtendimentoZapContato)
        .where(AtendimentoZapContato.id == contato.id)
        .values(ultima_recebida_em=datetime.now(UTC) - timedelta(hours=23, minutes=58))
    )
    await db.commit()
    assert await _motivo(db, envio.conversa) is None
    # 23 h 59 min 30 s atrás: dentro da margem do servidor — não sai.
    await db.execute(
        update(AtendimentoZapContato)
        .where(AtendimentoZapContato.id == contato.id)
        .values(ultima_recebida_em=datetime.now(UTC) - timedelta(hours=23, minutes=59, seconds=30))
    )
    await db.commit()
    assert await _motivo(db, envio.conversa) == "fora_da_janela"


# ═══════════════════════ o sucesso ═══════════════════════════════════════


async def test_sucesso_grava_o_wamid_o_status_e_o_token_so_no_header(db, envio, pessoa):
    with structlog.testing.capture_logs() as logs:
        m = await enviar.enviar_resposta(db, envio.conversa, "Oi! Temos a mala sim.", user=pessoa)
    assert m.status == "enviada"
    assert m.externo_id == "wamid.OUT1"
    assert m.payload["envio"]["zap"]["message_status"] == "accepted"
    (req,) = envio.graph.chamadas
    assert req.method == "POST" and req.url.path == CAMINHO_POST
    corpo_post = envio.graph.corpos()[0]
    assert corpo_post == {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": WA_ID,
        "type": "text",
        "text": {"body": "Oi! Temos a mala sim.", "preview_url": False},
        "biz_opaque_callback_data": str(m.id),
    }
    # O token: SÓ no header.
    assert req.headers["authorization"].endswith(TOKEN)
    assert TOKEN not in str(req.url)
    assert TOKEN not in req.content.decode()
    texto_dos_logs = repr(logs)
    assert TOKEN not in texto_dos_logs
    assert "Temos a mala" not in texto_dos_logs  # texto de cliente/loja nunca vai pro log
    assert WA_ID not in texto_dos_logs
    # O número anotou o último envio.
    numero = await _reler(db, AtendimentoZapNumero, envio.numero.id)
    assert numero.ultimo_enviado_em is not None
    # A conversa saiu de "aguardando".
    conversa = await _reler(db, AtendimentoConversa, envio.conversa.id)
    assert conversa.aguardando_resposta is False


async def test_destino_nao_mostra_o_token_no_repr(db, envio):
    destino = await enviar._destino(db, envio.conversa, origem=ORIGEM_HUMANO)
    assert TOKEN not in repr(destino)
    assert TOKEN not in repr(destino.zap)
    assert destino.zap.token == TOKEN


async def test_resposta_segurada_pela_meta(db, envio, pessoa, client):
    envio.graph.mensagens(
        resposta={
            "messages": [{"id": "wamid.SEGURA", "message_status": "held_for_quality_assessment"}]
        }
    )
    m = await enviar.enviar_resposta(db, envio.conversa, "Oi!", user=pessoa)
    assert m.status == "enviada"
    assert m.payload["envio"]["zap"]["message_status"] == "held_for_quality_assessment"
    r = await client.get(f"{URL}/conversas/{envio.conversa.id}")
    assert r.status_code == 200, r.text
    nossa = next(x for x in r.json()["mensagens"] if x["id"] == str(m.id))
    assert nossa["retida_pela_meta"] is True and nossa["entrega"] is None


async def test_200_sem_wamid_e_ambiguo(db, envio, pessoa):
    envio.graph.mensagens(resposta={"messages": []})
    m = await enviar.enviar_resposta(db, envio.conversa, "Oi!", user=pessoa)
    assert m.status == "revisar" and m.erro == "meta:sem_wamid"


# ═══════════════════════ os erros da Meta (§6.5) ═════════════════════════


@pytest.mark.parametrize(
    ("code", "classe", "efeito"),
    [
        (131047, "bloqueio", "fechar_janela"),
        (131026, "falhou", None),
        (131050, "falhou", "opt_out_marketing"),
        (131049, "falhou", None),
        (131056, "falhou", None),
        (130429, "falhou", None),
        (131048, "pausa", "pausar_numero"),
        (368, "pausa", "pausar_numero"),
        (131031, "pausa", "pausar_e_travar_registro"),
        (133016, "numero", "travar_registro"),
        (131042, "pausa", "pausar_pagamento"),
        (131045, "numero", "erro_numero"),
        (133010, "numero", "erro_numero"),
        (131037, "aviso", None),
        (131051, "falhou", None),
        (131052, "falhou", None),
        (131053, "falhou", None),
        (131057, "falhou", None),
        (132000, "modelo", "sincronizar_modelos"),
        (132016, "modelo", "sincronizar_modelos"),
        (190, "credencial", "token_invalido"),
        (0, "credencial", "token_invalido"),
        (10, "credencial", "sem_permissao"),
        (200, "credencial", "sem_permissao"),
        (131030, "falhou", None),
        (131000, "ambiguo", None),
        (None, "ambiguo", None),
    ],
)
def test_tabela_dos_erros_da_meta(code, classe, efeito):
    erro = zap_erros.traduzir(code)
    assert (erro.classe, erro.efeito) == (classe, efeito)
    assert erro.frase and "meta:" not in erro.frase
    assert erro.ambiguo is (classe == "ambiguo")


def test_frases_e_efeitos_fortes():
    assert "só aceita um modelo aprovado" in zap_erros.frase(131047)
    assert "Não tente de novo" in zap_erros.frase(131031)
    assert "72 h" in zap_erros.frase(133016)
    assert "token novo" in zap_erros.frase(190)
    assert zap_erros.frase(999999) == "A Meta recusou a mensagem (código 999999)."
    assert zap_erros.codigo_do_erro("meta:131047") == 131047
    assert zap_erros.codigo_do_erro("meta:sem_resposta") is None
    assert zap_erros.codigo_do_erro("timeout") is None
    assert zap_erros.frase_da_mensagem("meta:368") == zap_erros.frase(368)
    assert zap_erros.frase_da_mensagem("meta:sem_resposta") == zap_erros.FRASE_AMBIGUO
    assert zap_erros.frase_da_mensagem("cliente_indisponivel: X") is None
    # Fortes: só os que mexem no número ou na conta.
    assert zap_erros.EFEITO_FECHAR_JANELA not in zap_erros.EFEITOS_FORTES
    assert zap_erros.EFEITO_OPT_OUT_MARKETING not in zap_erros.EFEITOS_FORTES
    for forte in ("pausar_numero", "pausar_e_travar_registro", "token_invalido", "sem_permissao"):
        assert forte in zap_erros.EFEITOS_FORTES


def test_ambiguo_pelo_zap_api():
    assert zap_api.eh_ambiguo(zap_api.ZapApiErro("x"))  # timeout: sem resposta
    assert zap_api.eh_ambiguo(zap_api.ZapApiErro("x", http_status=502))
    assert zap_api.eh_ambiguo(zap_api.ZapApiErro("x", http_status=302))
    assert not zap_api.eh_ambiguo(zap_api.ZapApiErro("x", http_status=400))
    assert not zap_api.eh_ambiguo(zap_api.ZapApiErro("x", code=190, http_status=401))
    assert zap_api.eh_ambiguo(zap_api.ZapApiErro("x", code=131000, http_status=500))
    assert not zap_api.eh_ambiguo(zap_api.ZapApiErro("x", code=131047, http_status=400))


def _erro_meta(code: int, status: int = 400) -> tuple[int, dict]:
    return status, {
        "error": {
            "message": f"(#{code}) recusado",
            "type": "OAuthException",
            "code": code,
            "error_data": {"messaging_product": "whatsapp", "details": "detalhe"},
            "fbtrace_id": "AbC",
        }
    }


async def test_131047_fecha_a_janela_e_a_trava_12_recusa(db, envio, pessoa):
    envio.graph.mensagens(resposta=_erro_meta(131047))
    m = await enviar.enviar_resposta(db, envio.conversa, "Oi!", user=pessoa)
    assert (m.status, m.erro) == ("falhou", "meta:131047")
    assert m.payload["envio"]["zap"]["efeito"] == "fechar_janela"
    conversa = await _reler(db, AtendimentoConversa, envio.conversa.id)
    assert conversa.pode_enviar_ate <= datetime.now(UTC)
    # A trava 12 recusa na hora (sem ir à Meta de novo).
    antes = len(envio.graph.chamadas)
    assert await _motivo(db, conversa) == "fora_da_janela"
    with pytest.raises(EnvioRecusado) as e:
        await enviar.enviar_resposta(db, conversa, "Oi de novo!", user=pessoa)
    assert e.value.code == "fora_da_janela"
    assert len(envio.graph.chamadas) == antes
    # O cliente escreve de novo: a janela reabre.
    await gravar_corpo(
        corpo("texto", wamid="wamid.VOLTOU", ts=datetime.now(UTC).replace(microsecond=0)),
        app=envio.app,
    )
    await processar()
    # A rota relê a conversa a cada pedido; aqui, à mão (o processador é outra sessão).
    conversa = await _reler(db, AtendimentoConversa, envio.conversa.id)
    assert conversa.pode_enviar_ate > datetime.now(UTC) + timedelta(hours=23)
    assert await _motivo(db, conversa) is None


async def test_190_marca_o_token_invalido(db, envio, pessoa):
    envio.graph.mensagens(resposta=_erro_meta(190, 401))
    m = await enviar.enviar_resposta(db, envio.conversa, "Oi!", user=pessoa)
    assert (m.status, m.erro) == ("falhou", "meta:190")
    conta = await _reler(db, AtendimentoZapConta, envio.conta.id)
    assert conta.status == "token_invalido" and conta.ultimo_erro_code == 190
    assert "token novo" in conta.ultimo_erro and TOKEN not in conta.ultimo_erro
    assert await _motivo(db, envio.conversa) == "zap_sem_credencial"


async def test_368_pausa_o_numero_e_sobrevive_ao_rollback_do_resultado(
    db, envio, pessoa, monkeypatch
):
    envio.graph.mensagens(resposta=_erro_meta(368, 403))

    async def quebra(*_a, **_kw):
        raise DBAPIError("UPDATE …", None, Exception("o banco caiu no meio"))

    monkeypatch.setattr(enviar, "_aplicar_resultado", quebra)
    m = await enviar.enviar_resposta(db, envio.conversa, "Oi!", user=pessoa)
    # O resultado não foi gravado (a linha segue `enviando`; vira `revisar` em 10 min)…
    assert m.status == "enviando"
    # … mas o número PAROU (o efeito vem depois, numa transação própria).
    numero = await _reler(db, AtendimentoZapNumero, envio.numero.id)
    assert numero.envio_ligado is False and numero.saude_status == "restrito"
    assert numero.ultimo_erro_code == 368
    assert numero.saude["pausado_pela_meta"]["origem"] == "api"
    canal = await _canal(db)
    assert canal.status == "erro" and "restringiu" in canal.ultimo_erro
    monkeypatch.undo()


async def test_131050_grava_o_opt_out_de_marketing(db, envio, pessoa):
    envio.graph.mensagens(resposta=_erro_meta(131050))
    m = await enviar.enviar_resposta(db, envio.conversa, "Oi!", user=pessoa)
    assert m.status == "falhou"
    contato = await _contato(db, envio.conversa)
    assert contato.opt_out_tipo == "marketing" and contato.opt_out_em is not None


async def test_timeout_e_revisar_sem_retentativa(db, envio, pessoa):
    envio.graph.mensagens(resposta=httpx.ReadTimeout("a Meta não respondeu"))
    m = await enviar.enviar_resposta(db, envio.conversa, "Oi!", user=pessoa)
    assert (m.status, m.erro) == ("revisar", "meta:sem_resposta")
    assert len(envio.graph.chamadas) == 1  # NUNCA de novo sozinho
    # A linha em `revisar` não é "em voo": a pessoa confere antes de mandar outra.
    assert m.externo_id is None


async def test_5xx_sem_codigo_e_ambiguo_e_4xx_sem_codigo_falhou(db, envio, pessoa):
    envio.graph.mensagens(resposta=httpx.Response(502, text="<html>bad gateway</html>"))
    m = await enviar.enviar_resposta(db, envio.conversa, "Primeira", user=pessoa)
    assert m.status == "revisar"
    envio.graph.mensagens(resposta=httpx.Response(400, text="<html>bad request</html>"))
    m2 = await enviar.enviar_resposta(db, envio.conversa, "Segunda", user=pessoa)
    assert (m2.status, m2.erro) == ("falhou", "meta:http_400")
    assert len(envio.graph.chamadas) == 2


async def test_erro_desconhecido_131000_e_ambiguo(db, envio, pessoa):
    envio.graph.mensagens(resposta=_erro_meta(131000, 500))
    m = await enviar.enviar_resposta(db, envio.conversa, "Oi!", user=pessoa)
    assert (m.status, m.erro) == ("revisar", "meta:131000")


async def test_balao_que_falhou_traz_a_frase_da_meta(db, envio, pessoa, client):
    envio.graph.mensagens(resposta=_erro_meta(131026))
    m = await enviar.enviar_resposta(db, envio.conversa, "Oi!", user=pessoa)
    r = await client.get(f"{URL}/conversas/{envio.conversa.id}")
    assert r.status_code == 200, r.text
    nossa = next(x for x in r.json()["mensagens"] if x["id"] == str(m.id))
    assert nossa["status"] == "falhou"
    assert nossa["entrega_erro"] == zap_erros.frase(131026)
    # A mensagem do cliente não tem tique.
    dele = next(x for x in r.json()["mensagens"] if x["autor"] == "cliente")
    assert dele["entrega"] is None and dele["entrega_erro"] is None


# ═══════════════════════ simulador, laço, teto ═══════════════════════════


async def test_simulador_com_a_excecao_whatsapp_so_o_zap_sai(db, envio, pessoa, monkeypatch):
    monkeypatch.setattr(envio.settings, "atendimento_simulador", True)
    monkeypatch.setattr(envio.settings, "atendimento_simulador_exceto", "")
    m = await enviar.enviar_resposta(db, envio.conversa, "Fingido", user=pessoa)
    assert m.status == "enviada" and m.externo_id.startswith("sim:")
    assert envio.graph.chamadas == []
    monkeypatch.setattr(envio.settings, "atendimento_simulador_exceto", "whatsapp")
    m = await enviar.enviar_resposta(db, envio.conversa, "De verdade", user=pessoa)
    assert m.status == "enviada" and m.externo_id == "wamid.OUT1"
    assert len(envio.graph.chamadas) == 1


async def test_simulador_em_producao_recusa(db, envio, pessoa, monkeypatch):
    monkeypatch.setattr(envio.settings, "atendimento_simulador", True)
    monkeypatch.setattr(envio.settings, "env", "production")
    with pytest.raises(EnvioRecusado) as e:
        await enviar.enviar_resposta(db, envio.conversa, "Oi!", user=pessoa)
    assert e.value.code == "simulador_em_producao"
    assert envio.graph.chamadas == []


_PALAVRAS = ("um", "dois", "tres", "quatro", "cinco", "seis", "sete", "oito", "nove", "dez")


async def test_laco_nove_envios_ao_mesmo_contato_em_10_min(db, envio, pessoa):
    for i in range(8):
        m = await enviar.enviar_resposta(
            db, envio.conversa, f"Resposta {_PALAVRAS[i]}", user=pessoa
        )
        assert m.status == "enviada", i
    with pytest.raises(EnvioRecusado) as e:
        await enviar.enviar_resposta(db, envio.conversa, "Resposta nove", user=pessoa)
    assert e.value.code == "zap_laco"
    assert len(envio.graph.chamadas) == 8


async def test_teto_do_dia_do_numero(db, envio, pessoa, monkeypatch):
    await db.execute(
        update(AtendimentoZapNumero)
        .where(AtendimentoZapNumero.id == envio.numero.id)
        .values(teto_dia=2)
    )
    await db.commit()
    for i in range(2):
        m = await enviar.enviar_resposta(
            db, envio.conversa, f"Resposta {_PALAVRAS[i]}", user=pessoa
        )
        assert m.status == "enviada"
    with pytest.raises(EnvioRecusado) as e:
        await enviar.enviar_resposta(db, envio.conversa, "Resposta tres", user=pessoa)
    assert e.value.code == "zap_teto_dia"
    # Sem o teto próprio, vale o padrão da chave.
    await db.execute(
        update(AtendimentoZapNumero)
        .where(AtendimentoZapNumero.id == envio.numero.id)
        .values(teto_dia=None)
    )
    await db.commit()
    monkeypatch.setattr(envio.settings, "atendimento_zap_max_dia_numero", 3)
    m = await enviar.enviar_resposta(db, envio.conversa, "Resposta quatro", user=pessoa)
    assert m.status == "enviada"
    assert await _motivo(db, envio.conversa) == "zap_teto_dia"


def test_inicio_do_dia_e_o_de_sao_paulo():
    # 02:00 UTC = 23:00 do dia anterior em São Paulo.
    agora = datetime(2026, 10, 27, 2, 0, tzinfo=UTC)
    assert zap_svc.inicio_do_dia(agora) == datetime(2026, 10, 26, 3, 0, tzinfo=UTC)


# ═══════════════════════ o resto do caminho único ════════════════════════


async def test_ia_e_automatica_nunca_no_zap(db, envio, pessoa):
    with pytest.raises(EnvioRecusado) as e:
        await enviar.enviar_resposta(db, envio.conversa, "Oi!", user=None, origem=ORIGEM_IA)
    assert (e.value.code, e.value.detail) == ("canal_sem_envio", MOTIVO_ZAP_SO_PESSOA)
    assert envio.graph.chamadas == []
    # O Zap no formato ANTIGO continua recusado para sempre (P2).
    assert motivo_canal_sem_envio("zap", "shopee", {}) == MOTIVO_ZAP_SEM_ENVIO
    assert "whatsapp" not in enviar.ADAPTADORES


async def test_foto_no_zap_ainda_nao(db, envio, pessoa):
    from app.services.atendimento import foto as foto_mod

    assert foto_mod.motivo_sem_foto(envio.conversa)
    foto = SimpleNamespace(
        sha256="0" * 64, nome="a.jpg", mime="image/jpeg", tamanho=10, conteudo=b"x"
    )
    with pytest.raises(EnvioRecusado) as e:
        await enviar.enviar_foto(db, envio.conversa, foto, user=pessoa)
    assert e.value.code == "foto_nao_suportada"
    assert envio.graph.chamadas == []


async def test_chamar_plataforma_zap_sem_destino_nunca_cai_em_outro_adaptador(db, envio):
    r = await enviar._chamar_plataforma(db, envio.conversa, None, "Oi!")
    assert r == ResultadoEnvio(ok=False, erro="zap_sem_destino")
    assert envio.graph.chamadas == []


async def test_rota_responder_pelo_zap(db, envio, pessoa, client):
    r = await client.post(
        f"{URL}/conversas/{envio.conversa.id}/responder", json={"texto": "Oi pela tela!"}
    )
    assert r.status_code in (200, 201), r.text
    assert envio.graph.corpos()[0]["text"]["body"] == "Oi pela tela!"
    # O compositor diz "pode" com tudo ligado, e mostra a trava quando algo cai.
    r = await client.get(f"{URL}/conversas/{envio.conversa.id}")
    env = r.json()["envio"]
    assert env["limite_caracteres"] == 4096 and env["modo_observacao"] is False


async def test_validador_roda_antes_de_sair(db, envio, pessoa):
    with pytest.raises(EnvioRecusado) as e:
        await enviar.enviar_resposta(
            db, envio.conversa, "Me passa o número do cartão, por favor", user=pessoa
        )
    assert e.value.code == "texto_invalido"
    assert envio.graph.chamadas == []
    # Contato da loja no Zap passa (é o canal da marca).
    m = await enviar.enviar_resposta(
        db, envio.conversa, "Veja em www.charlots.com.br ou ligue (11) 98888-7777", user=pessoa
    )
    assert m.status == "enviada"


async def test_modo_humano_do_numero_so_admin_liga(db, envio, make_user, auth_as, client):
    """O PATCH /canais: o canal do número é só de admin — quem não é nem o
    acha (decisão de 26/10/2026: só administrador vê o Zap); copiloto e
    automático, nunca (a IA fica fora do Zap)."""
    canal = await _canal(db)
    url = f"{URL}/canais/{canal.id}"
    equipe = await make_user(permissions=PODE_TUDO)
    auth_as(equipe)
    for modo in ("observar", "humano"):
        r = await client.patch(url, json={"modo": modo})
        assert (r.status_code, r.json()["detail"]["code"]) == (404, "canal_nao_encontrado")
    admin = await make_user(permissions=PODE_TUDO, role=UserRole.ADMIN)
    auth_as(admin)
    r = await client.patch(url, json={"modo": "observar"})
    assert r.status_code == 200, r.text
    for modo in ("copiloto", "auto"):
        r = await client.patch(url, json={"modo": modo})
        assert (r.status_code, r.json()["detail"]["code"]) == (422, "modo_invalido_externo"), modo
    r = await client.patch(url, json={"modo": "humano"})
    assert r.status_code == 200, r.text
    assert (await _canal(db)).modo == "humano"


async def test_so_admin_ve_e_responde_o_zap(db, envio, make_user, auth_as, client, monkeypatch):
    """Decisão do Eduardo (26/10/2026): só ADMINISTRADOR vê o Zap. A pessoa
    ativa sem equipe (escopo sem restrição) não acha a conversa, nem a linha
    na lista e na barra, nem o canal; e o caminho único recusa a resposta
    dela mesmo que ela chegue lá por outro caminho."""
    conversa_id = str(envio.conversa.id)
    equipe = await make_user(permissions=PODE_TUDO)
    auth_as(equipe)
    for so_admin in (False, True):  # com a permissão fina e na fase de observação
        monkeypatch.setattr(rota, "SO_ADMIN", so_admin)
        r = await client.get(f"{URL}/conversas/{conversa_id}")
        assert (r.status_code, r.json()["detail"]["code"]) == (404, "conversa_nao_encontrada")
        r = await client.get(f"{URL}/conversas", params={"plataforma": "whatsapp"})
        assert r.status_code == 200, r.text
        assert r.json()["itens"] == []
        r = await client.get(f"{URL}/resumo")
        assert r.status_code == 200, r.text
        assert not [lj for lj in r.json()["lojas"] if str(lj.get("externo_ref")).startswith("zap:")]
        r = await client.get(f"{URL}/canais")
        assert r.status_code == 200, r.text
        assert not [c for c in r.json() if c["plataforma"] == "whatsapp"]
    monkeypatch.setattr(rota, "SO_ADMIN", False)
    r = await client.post(f"{URL}/conversas/{conversa_id}/responder", json={"texto": "Oi"})
    assert (r.status_code, r.json()["detail"]["code"]) == (404, "conversa_nao_encontrada")
    with pytest.raises(EnvioRecusado) as e:
        await enviar.enviar_resposta(db, envio.conversa, "Oi", user=equipe)
    assert e.value.code == "zap_so_admin"
    with pytest.raises(EnvioRecusado) as e:
        await enviar.enviar_resposta(db, envio.conversa, "Oi", user=None)
    assert e.value.code == "zap_so_admin"
    assert envio.graph.chamadas == []
    # O admin vê e responde.
    admin = await make_user(permissions=PODE_TUDO, role=UserRole.ADMIN)
    auth_as(admin)
    r = await client.get(f"{URL}/conversas", params={"plataforma": "whatsapp"})
    assert [i["id"] for i in r.json()["itens"]] == [conversa_id]
    assert (await client.get(f"{URL}/conversas/{conversa_id}")).status_code == 200
    r = await client.post(f"{URL}/conversas/{conversa_id}/responder", json={"texto": "Oi"})
    assert r.status_code in (200, 201), r.text


# ═══════════════════════ o tique azul (decisão de 26/10/2026) ════════════


@pytest.fixture
def redis_lida(monkeypatch):
    r = RedisFalso()
    monkeypatch.setattr(lida, "redis", r)
    return r


async def test_tique_azul_nasce_desligado(db, envio, pessoa, redis_lida):
    assert envio.settings.atendimento_zap_marcar_lida is False
    assert lida.se_aplica(envio.conversa) is False
    assert await lida.marcar_ao_abrir(db, envio.conversa, pessoa) is False
    with pytest.raises(lida.LidaRecusada) as e:
        await lida.marcar_como_lida(db, envio.conversa, user=pessoa)
    assert e.value.code == lida.RECUSA_NAO_SE_APLICA
    assert envio.graph.chamadas == []


async def test_tique_azul_ligado_marca_a_ultima_do_cliente_uma_vez(
    db, envio, pessoa, redis_lida, monkeypatch, client
):
    monkeypatch.setattr(envio.settings, "atendimento_zap_marcar_lida", True)
    assert await lida.marcar_ao_abrir(db, envio.conversa, pessoa) is True
    r = await client.post(f"{URL}/conversas/{envio.conversa.id}/lida")
    assert r.status_code == 200, r.text
    assert r.json()["marcada"] is True
    (req,) = envio.graph.chamadas
    assert req.url.path == CAMINHO_POST
    assert envio.graph.corpos()[0] == {
        "messaging_product": "whatsapp",
        "status": "read",
        "message_id": "wamid.OI",
    }
    assert TOKEN not in str(req.url)
    # Já marcada: a próxima abertura não vai de novo.
    conversa = await _conversa(db)
    assert await lida.marcar_ao_abrir(db, conversa, pessoa) is False
    r = await client.post(f"{URL}/conversas/{envio.conversa.id}/lida")
    assert r.json()["marcada"] is False and r.json()["motivo"] == lida.MOTIVO_JA_LIDA
    assert len(envio.graph.chamadas) == 1


async def test_tique_azul_so_admin_e_so_com_o_envio(db, envio, make_user, redis_lida, monkeypatch):
    monkeypatch.setattr(envio.settings, "atendimento_zap_marcar_lida", True)
    quem_mexe = await make_user(permissions=PODE_TUDO)
    monkeypatch.setattr(lida.acesso, "pode_mexer", lambda _u: True)
    with pytest.raises(lida.LidaRecusada) as e:
        await lida.marcar_como_lida(db, envio.conversa, user=quem_mexe)
    assert e.value.code == lida.RECUSA_SO_LEITURA
    admin = await make_user(permissions=PODE_TUDO, role=UserRole.ADMIN)
    monkeypatch.setattr(envio.settings, "atendimento_zap_envio_ativo", False)
    with pytest.raises(lida.LidaRecusada) as e:
        await lida.marcar_como_lida(db, envio.conversa, user=admin)
    assert e.value.code == lida.RECUSA_ENVIO_DESLIGADO
    monkeypatch.setattr(envio.settings, "atendimento_zap_envio_ativo", True)
    await db.execute(update(AtendimentoZapNumero).values(envio_ligado=False))
    await db.commit()
    with pytest.raises(lida.LidaRecusada) as e:
        await lida.marcar_como_lida(db, envio.conversa, user=admin)
    assert e.value.code == lida.RECUSA_ZAP_NUMERO_PAUSADO
    assert envio.graph.chamadas == []


async def test_tique_azul_respeita_a_lista_de_teste_e_o_contato(
    db, envio, pessoa, redis_lida, monkeypatch
):
    monkeypatch.setattr(envio.settings, "atendimento_zap_marcar_lida", True)
    # Fora de produção, só a lista de teste (o celular do cliente fora dela).
    monkeypatch.setattr(envio.settings, "atendimento_zap_destinos_teste", "5511900001111")
    assert await lida.marcar_ao_abrir(db, envio.conversa, pessoa) is False
    with pytest.raises(lida.LidaRecusada) as e:
        await lida.marcar_como_lida(db, envio.conversa, user=pessoa)
    assert e.value.code == lida.RECUSA_ZAP_FORA_DOS_TESTES
    # De volta na lista, mas o contato bloqueado: também não.
    monkeypatch.setattr(envio.settings, "atendimento_zap_destinos_teste", WA_ID)
    contato = await _contato(db, envio.conversa)
    await db.execute(
        update(AtendimentoZapContato)
        .where(AtendimentoZapContato.id == contato.id)
        .values(bloqueado=True)
    )
    await db.commit()
    with pytest.raises(lida.LidaRecusada) as e:
        await lida.marcar_como_lida(db, envio.conversa, user=pessoa)
    assert e.value.code == lida.RECUSA_ZAP_FORA_DOS_TESTES
    assert envio.graph.chamadas == []


def test_marcar_lida_do_zap_so_e_chamado_pelo_lida():
    """Ninguém além do `lida.py` marca como lida na Meta (nem a IA, nem os crons)."""
    app_dir = Path(__file__).resolve().parents[1] / "app"
    quem = sorted(
        str(p.relative_to(app_dir))
        for p in app_dir.rglob("*.py")
        if re.search(r"\bzap\.marcar_lida\(", p.read_text(encoding="utf-8"))
    )
    assert quem == ["services/atendimento/lida.py"]
    assert T0 <= datetime.now(UTC)


# ═══════════════════════ o script: cadastrar com o app de TESTE ══════════
# Revisão da Onda 2 (26/10/2026): o token que enxerga OUTRA WABA (além da de
# teste) não entra no banco local — mandaria pelos números de produção.

_APP_TESTE = "1510263727600907"
_WABA_TESTE = "922501156908169"
_PNID_TESTE = "937856119421901"
_SEGREDO_TESTE = "segredo-do-app-de-teste-0123456789abcdef"
_TOKEN_TESTE = "EAAtokenDEtesteDEmentira000000000000"


def _graph_do_cadastro(graph, *, alvos: list[str]) -> None:
    escopos = ["whatsapp_business_messaging", "whatsapp_business_management"]
    graph.quando(
        "GET",
        "/v26.0/debug_token",
        {
            "data": {
                "app_id": _APP_TESTE,
                "type": "SYSTEM_USER",
                "is_valid": True,
                "expires_at": 0,
                "scopes": escopos,
                "granular_scopes": [{"scope": e, "target_ids": alvos} for e in escopos],
                "user_id": "61500000000001",
            }
        },
    )
    graph.quando("GET", f"/v26.0/{_WABA_TESTE}", {"id": _WABA_TESTE, "name": "WABA de teste"})
    graph.quando(
        "GET",
        f"/v26.0/{_WABA_TESTE}/phone_numbers",
        {
            "data": [
                {
                    "id": _PNID_TESTE,
                    "display_phone_number": "+1 555-169-8240",
                    "verified_name": "Test Number",
                }
            ]
        },
    )


@pytest.fixture
def local(monkeypatch):
    """O script só roda no localhost e com a CREDENTIALS_KEY trocada."""
    from scripts import atendimento_zap_local as script

    s = get_settings()
    monkeypatch.setattr(s, "api_url", "http://127.0.0.1:8000")
    monkeypatch.setattr(s, "credentials_key", "chave-local-de-teste-0123456789abcdef")
    return script


async def test_cadastrar_teste_recusa_token_que_enxerga_outra_waba(db, zap, graph, local, capsys):
    _graph_do_cadastro(graph, alvos=[_WABA_TESTE, "922500000000999"])
    async with _db.SessionLocal() as s:
        with pytest.raises(local.Recusado):
            await local.cadastrar_teste(
                s,
                app_id=_APP_TESTE,
                waba_id=_WABA_TESTE,
                business_id=None,
                app_secret=_SEGREDO_TESTE,
                token=_TOKEN_TESTE,
            )
    saida = capsys.readouterr().out
    assert "enxerga outras 1 conta(s) do WhatsApp" in saida
    assert _TOKEN_TESTE not in saida and _SEGREDO_TESTE not in saida
    # Nada foi gravado (nem o app, nem a conta, nem o número).
    from app.models import AtendimentoZapApp

    assert (
        await db.scalar(select(AtendimentoZapApp.id).where(AtendimentoZapApp.app_id == _APP_TESTE))
    ) is None
    assert (
        await db.scalar(
            select(AtendimentoZapConta.id).where(AtendimentoZapConta.waba_id == _WABA_TESTE)
        )
    ) is None


async def test_cadastrar_teste_so_com_a_waba_de_teste_grava_desligado(
    db, zap, graph, local, capsys
):
    _graph_do_cadastro(graph, alvos=[_WABA_TESTE])
    async with _db.SessionLocal() as s:
        saida = await local.cadastrar_teste(
            s,
            app_id=_APP_TESTE,
            waba_id=_WABA_TESTE,
            business_id=None,
            app_secret=_SEGREDO_TESTE,
            token=_TOKEN_TESTE,
        )
    assert saida["envio_ligado"] is False and saida["modo"] == "observar"
    assert [n["phone_number_id"] for n in saida["numeros"]] == [_PNID_TESTE]
    assert _TOKEN_TESTE not in str(saida) + capsys.readouterr().out
    numero = (
        await db.execute(
            select(AtendimentoZapNumero).where(AtendimentoZapNumero.phone_number_id == _PNID_TESTE)
        )
    ).scalar_one()
    assert (numero.teste, numero.envio_ligado) == (True, False)


# ═══════════════════════ o tique azul: revisão da Onda 2 ═════════════════


async def test_tique_azul_nao_apaga_o_que_o_webhook_gravou_no_meio(
    db, envio, pessoa, redis_lida, monkeypatch
):
    """A chamada à Meta leva até 20 s; o processador do webhook grava
    `dados.zap.ctwa` nesse meio-tempo. O tique azul mescla sobre a linha
    RELIDA — não regrava a cópia do começo do pedido."""
    monkeypatch.setattr(envio.settings, "atendimento_zap_marcar_lida", True)
    conversa_id = envio.conversa.id

    async def meta_demora_e_o_webhook_grava(pnid, token, wamid):
        async with _db.SessionLocal() as outra:
            c = await outra.get(AtendimentoConversa, conversa_id)
            dados = dict(c.dados or {})
            c.dados = {
                **dados,
                "zap": {**(dados.get("zap") or {}), "ctwa": {"source_id": "anuncio-1"}},
            }
            await outra.commit()
        return None

    monkeypatch.setattr(zap_svc, "marcar_lida", meta_demora_e_o_webhook_grava)
    r = await lida.marcar_como_lida(db, envio.conversa, user=pessoa)
    assert r.marcada is True
    conversa = await _reler(db, AtendimentoConversa, conversa_id)
    assert conversa.dados["zap"]["ctwa"] == {"source_id": "anuncio-1"}
    assert conversa.dados["zap"]["lida_wamid"] == "wamid.OI"


async def test_tique_azul_ignora_o_oi_simulado(db, envio, pessoa, redis_lida, monkeypatch):
    """O "oi" do script local (`wamid.LOCAL…`) não vai à Meta: ela não o conhece."""
    monkeypatch.setattr(envio.settings, "atendimento_zap_marcar_lida", True)
    await gravar_corpo(
        corpo("texto", wamid=f"{zap_svc.WAMID_SIMULADO}abc123", ts=datetime.now(UTC)),
        app=envio.app,
    )
    assert (await processar())["processados"] == 1
    conversa = await _conversa(db)
    assert await zap_svc.ultima_de_entrada(db, conversa, datetime.now(UTC)) == "wamid.OI"
    r = await lida.marcar_como_lida(db, conversa, user=pessoa)
    assert r.marcada is True
    assert envio.graph.corpos()[0]["message_id"] == "wamid.OI"


async def test_tique_azul_recusa_definitiva_nao_repete(db, envio, pessoa, redis_lida, monkeypatch):
    monkeypatch.setattr(envio.settings, "atendimento_zap_marcar_lida", True)
    # Limite (130429): passa — tenta de novo na próxima abertura.
    envio.graph.mensagens(resposta=_erro_meta(130429))
    r = await lida.marcar_como_lida(db, envio.conversa, user=pessoa)
    assert (r.marcada, r.motivo) == (False, lida.MOTIVO_PLATAFORMA_FALHOU)
    assert "lida_wamid" not in ((await _conversa(db)).dados.get("zap") or {})
    # Pedido inválido (100): a Meta nunca vai aceitar este wamid — fica tratado.
    envio.graph.mensagens(resposta=_erro_meta(100))
    r = await lida.marcar_como_lida(db, await _conversa(db), user=pessoa)
    assert (r.marcada, r.motivo) == (False, lida.MOTIVO_PLATAFORMA_FALHOU)
    zap_dados = (await _conversa(db)).dados["zap"]
    assert (zap_dados["lida_wamid"], zap_dados["lida_recusada"]) == ("wamid.OI", 100)
    chamadas = len(envio.graph.chamadas)
    r = await lida.marcar_como_lida(db, await _conversa(db), user=pessoa)
    assert (r.marcada, r.motivo) == (False, lida.MOTIVO_JA_LIDA)
    assert len(envio.graph.chamadas) == chamadas


# ═══════════════════════ o Histórico (revisão da Onda 2) ═════════════════


async def test_historico_nao_atribui_a_pessoa_o_carimbo_nem_a_pausa_da_meta(db, envio, pessoa):
    """`atendimento_zap_numeros` fica COM o gatilho do Histórico (§3.2). O
    "último envio" (carimbo de máquina) virava uma alteração da pessoa a cada
    resposta, e a pausa pela Meta (368) aparecia como feita por quem clicou
    em Enviar."""
    from sqlalchemy import text

    from app.historico import contexto as hctx

    ator = hctx.Ator(
        metodo="POST",
        caminho="/api/atendimento/conversas/x/responder",
        grava=True,
        escrita=True,
        user_id=pessoa.id,
        nome="Pessoa",
    )
    tok = hctx.abrir(ator)
    try:
        await db.commit()  # a próxima transação nasce marcada
        m = await enviar.enviar_resposta(db, envio.conversa, "Oi!", user=pessoa)
        assert m.status == "enviada"
        envio.graph.mensagens(resposta=_erro_meta(368))
        m = await enviar.enviar_resposta(db, envio.conversa, "De novo", user=pessoa)
        assert m.erro == "meta:368"
    finally:
        hctx.fechar(tok)
    numero = await _reler(db, AtendimentoZapNumero, envio.numero.id)
    assert numero.ultimo_enviado_em is not None and numero.envio_ligado is False
    tabelas = {
        t for (t,) in (await db.execute(text("select tabela from historico_alteracao"))).all()
    }
    assert "atendimento_zap_numeros" not in tabelas
