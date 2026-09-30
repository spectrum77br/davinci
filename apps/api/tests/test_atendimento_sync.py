"""O cron do atendimento: canais, leitura por canal e alerta de prazo.

- toda loja conectada ganha os seus canais, uma vez, em `observar`; Amazon
  sem caixa de e-mail fica `desligado` (e volta sozinha quando a caixa existe);
- um canal com erro não derruba os outros, e o erro gravado não leva texto
  de comprador nem URL assinada;
- 401/403 vira `sem_escopo`, tentado de novo só de hora em hora;
- a trava por canal impede duas leituras juntas;
- o alerta de prazo avisa uma vez por conversa e nível, sem texto de
  comprador, e devolve a reserva se o Telegram falhar.

Os adaptadores são de outros lotes: aqui entram falsos, pelo contrato.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import (
    AtendimentoCanal,
    AtendimentoConversa,
    AtendimentoMensagem,
    Integration,
    IntegrationPlatform,
    User,
)
from app.redis_client import redis
from app.security.cipher import encrypt_json
from app.services.atendimento import clientes, gravar, sync
from app.services.atendimento import shopee as adaptador_shopee
from app.services.atendimento.constantes import ResultadoSync


@pytest.fixture(autouse=True)
async def _limpa_redis():
    async def apaga():
        for padrao in ("atendimento:sync:canal:*", "atendimento:alerta:*"):
            chaves = [k async for k in redis.scan_iter(padrao)]
            if chaves:
                await redis.delete(*chaves)

    await apaga()
    yield
    await apaga()


@pytest.fixture(autouse=True)
def _cliente_falso(monkeypatch):
    async def cliente(integration):
        return object()

    monkeypatch.setattr(clientes, "cliente_da_integracao", cliente)


@pytest.fixture
def sem_imap(monkeypatch):
    monkeypatch.setattr(get_settings(), "atendimento_amazon_imap_host", "")


async def _integ(
    db: AsyncSession,
    user: User,
    platform: IntegrationPlatform,
    nome: str,
    *,
    arquivada: bool = False,
) -> Integration:
    integ = Integration(
        user_id=user.id,
        platform=platform,
        name=nome,
        credentials=encrypt_json({"access_token": "t"}),
        archived_at=datetime.now(UTC) if arquivada else None,
    )
    db.add(integ)
    await db.commit()
    return integ


async def _canais(db: AsyncSession) -> list[AtendimentoCanal]:
    return list(
        (
            await db.execute(
                select(AtendimentoCanal)
                .order_by(AtendimentoCanal.plataforma, AtendimentoCanal.canal)
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .all()
    )


# ─────────────── garantir_canais ───────────────


async def test_garantir_canais_cria_o_que_falta_e_nao_duplica(db, make_user, sem_imap, monkeypatch):
    user = await make_user()
    await _integ(db, user, IntegrationPlatform.SHOPEE, "kfa")
    await _integ(db, user, IntegrationPlatform.ML, "kfa ml")
    await _integ(db, user, IntegrationPlatform.TIKTOK, "atv")
    await _integ(db, user, IntegrationPlatform.AMAZON, "kfa amz")
    await _integ(db, user, IntegrationPlatform.BLING, "bling")  # sem atendimento
    await _integ(db, user, IntegrationPlatform.SHOPEE, "velha", arquivada=True)

    assert await sync.garantir_canais(db) == 5
    await db.commit()
    canais = await _canais(db)
    assert [(c.plataforma, c.canal, c.modo, c.status) for c in canais] == [
        ("amazon", "email", "observar", "desligado"),
        ("ml", "pergunta", "observar", "novo"),
        ("ml", "pos_venda", "observar", "novo"),
        ("shopee", "chat", "observar", "novo"),
        ("tiktok", "chat", "observar", "novo"),
    ]

    # Segunda rodada: nada novo, nada duplicado.
    assert await sync.garantir_canais(db) == 0
    await db.commit()
    assert len(await _canais(db)) == 5

    # A caixa da Amazon passou a existir: o canal volta a `novo` sozinho.
    s = get_settings()
    monkeypatch.setattr(s, "atendimento_amazon_imap_host", "imap.exemplo.com")
    monkeypatch.setattr(s, "atendimento_amazon_imap_usuario", "atendimento@exemplo.com")
    await sync.garantir_canais(db)
    await db.commit()
    amazon = next(c for c in await _canais(db) if c.plataforma == "amazon")
    assert amazon.status == "desligado"  # sem senha não há login

    monkeypatch.setattr(s, "atendimento_amazon_imap_senha", "senha-de-app-falsa")
    await sync.garantir_canais(db)
    await db.commit()
    amazon = next(c for c in await _canais(db) if c.plataforma == "amazon")
    assert amazon.status == "novo"


# ─────────────── sincronizar_canal ───────────────


async def _canal_shopee(db: AsyncSession, user: User, nome: str) -> AtendimentoCanal:
    integ = await _integ(db, user, IntegrationPlatform.SHOPEE, nome)
    canal = AtendimentoCanal(integration_id=integ.id, plataforma="shopee", canal="chat")
    db.add(canal)
    await db.commit()
    return canal


def _adaptador(monkeypatch, fn) -> list:
    """Troca `shopee.sincronizar`; devolve a lista dos canais que ele leu."""
    lidos: list = []

    async def sincronizar(session, canal, integration, cliente):
        lidos.append(canal.id)
        return await fn(session, canal, integration, cliente)

    monkeypatch.setattr(adaptador_shopee, "sincronizar", sincronizar)
    return lidos


async def test_rodada_ok_grava_e_commita(db, make_user, monkeypatch):
    user = await make_user()
    canal = await _canal_shopee(db, user, "kfa")

    async def ler(session, canal, integration, cliente):
        conversa, _ = await gravar.upsert_conversa(
            session,
            canal=canal,
            integration=integration,
            plataforma="shopee",
            canal_nome="chat",
            externo_id="c-1",
        )
        await gravar.gravar_mensagem(
            session,
            conversa,
            externo_id="m-1",
            autor="cliente",
            texto="oi",
            enviada_em=datetime.now(UTC),
        )
        canal.cursor = {"next": "abc"}
        return ResultadoSync(status="ok", conversas_novas=1, mensagens_novas=1, nao_lidas=3)

    _adaptador(monkeypatch, ler)
    r = await sync.sincronizar_canal(canal.id)
    assert (r.status, r.mensagens_novas) == ("ok", 1)

    (c,) = await _canais(db)
    assert (c.status, c.nao_lidas_plataforma, c.cursor) == ("ok", 3, {"next": "abc"})
    assert c.ultimo_ok_em is not None
    assert await db.scalar(select(func.count()).select_from(AtendimentoMensagem)) == 1


async def test_erro_desfaz_a_rodada_e_nao_grava_texto_de_comprador(db, make_user, monkeypatch):
    user = await make_user()
    canal = await _canal_shopee(db, user, "kfa")

    async def ler(session, canal, integration, cliente):
        conversa, _ = await gravar.upsert_conversa(
            session,
            canal=canal,
            integration=integration,
            plataforma="shopee",
            canal_nome="chat",
            externo_id="c-1",
        )
        raise RuntimeError(
            "shopee_chat HTTP 500: cliente escreveu 'meu cpf 123.456.789-00' "
            "https://partner.shopeemobile.com/api?access_token=SEGREDO"
        )

    _adaptador(monkeypatch, ler)
    r = await sync.sincronizar_canal(canal.id)
    assert r.status == "erro"

    (c,) = await _canais(db)
    assert c.status == "erro"
    assert c.ultimo_erro == "RuntimeError · HTTP 500"
    assert "cpf" not in c.ultimo_erro and "SEGREDO" not in c.ultimo_erro
    assert c.ultimo_erro_em is not None
    # O que a rodada gravou pela metade foi embora com o rollback.
    assert await db.scalar(select(func.count()).select_from(AtendimentoConversa)) == 0


async def test_401_vira_sem_escopo_e_so_volta_de_hora_em_hora(db, make_user, monkeypatch):
    user = await make_user()
    canal = await _canal_shopee(db, user, "tiktok-sem-escopo")

    async def ler(session, canal, integration, cliente):
        resposta = httpx.Response(
            401,
            json={"code": 105005, "message": "Access denied"},
            request=httpx.Request("GET", "https://api.exemplo.com/x?token=SEGREDO"),
        )
        raise httpx.HTTPStatusError("401", request=resposta.request, response=resposta)

    lidos = _adaptador(monkeypatch, ler)
    r = await sync.sincronizar_canal(canal.id)
    assert r.status == "sem_escopo"
    (c,) = await _canais(db)
    assert c.status == "sem_escopo"
    assert c.ultimo_erro == "HTTPStatusError · HTTP 401 · 105005"

    # Rodada seguinte: pulado (não adianta insistir a cada 2 minutos)…
    resumo = await sync.sincronizar_tudo()
    assert resumo["canais"] == 0
    assert len(lidos) == 1

    # …até passar uma hora.
    await db.execute(
        update(AtendimentoCanal).values(ultimo_erro_em=datetime.now(UTC) - timedelta(hours=2))
    )
    await db.commit()
    resumo = await sync.sincronizar_tudo()
    assert resumo["canais"] == 1 and resumo["sem_escopo"] == 1
    assert len(lidos) == 2


async def test_adaptador_que_devolve_sem_escopo(db, make_user, monkeypatch):
    user = await make_user()
    canal = await _canal_shopee(db, user, "poofy")

    async def ler(session, canal, integration, cliente):
        return ResultadoSync(status="sem_escopo", erro="403 PA_UNAUTHORIZED_RESULT_FROM_POLICIES")

    _adaptador(monkeypatch, ler)
    await sync.sincronizar_canal(canal.id)
    (c,) = await _canais(db)
    assert (c.status, c.ultimo_erro) == ("sem_escopo", "403 PA_UNAUTHORIZED_RESULT_FROM_POLICIES")


async def test_rodada_ok_com_parte_das_conversas_falhando_mostra_o_aviso(
    db, make_user, monkeypatch
):
    """O ML devolve `ok` com "N de M conversas com erro": o canal segue `ok`
    (leu), mas o aviso fica em `ultimo_erro` para aparecer em Lojas e modo."""
    user = await make_user()
    canal = await _canal_shopee(db, user, "kfa-parcial")

    async def ler(session, canal, integration, cliente):
        return ResultadoSync(status="ok", erro="2 de 20 conversas com erro: HTTP 404")

    _adaptador(monkeypatch, ler)
    await sync.sincronizar_canal(canal.id)
    (c,) = await _canais(db)
    assert c.status == "ok"
    assert c.ultimo_ok_em is not None and c.ultimo_erro_em is not None
    assert c.ultimo_erro == "2 de 20 conversas com erro: HTTP 404"


def test_erro_de_operacao_reconhece_falta_de_permissao():
    assert sync.erro_de_operacao(RuntimeError("TikTok API error: 105005 Access denied")) == (
        "RuntimeError · 105005",
        True,
    )
    assert sync.erro_de_operacao(RuntimeError("shopee HTTP 403: error_auth x")) == (
        "RuntimeError · HTTP 403 · error_auth",
        True,
    )
    assert sync.erro_de_operacao(httpx.ReadTimeout("x")) == ("timeout", False)
    assert sync.erro_de_operacao(ValueError("comprador disse tal coisa")) == ("ValueError", False)


async def test_canal_travado_nao_le_de_novo(db, make_user, monkeypatch):
    user = await make_user()
    canal = await _canal_shopee(db, user, "kfa")

    async def ler(session, canal, integration, cliente):
        return ResultadoSync()

    lidos = _adaptador(monkeypatch, ler)
    await redis.set(sync._CHAVE_TRAVA.format(canal.id), "outro-processo", ex=60)
    r = await sync.sincronizar_canal(canal.id)
    assert r.status == sync.RESULTADO_PULADO
    assert lidos == []
    # A trava alheia continua lá (só se solta a própria).
    assert await redis.get(sync._CHAVE_TRAVA.format(canal.id)) == "outro-processo"


async def test_sincronizar_tudo_erro_de_um_nao_derruba_os_outros(
    db, make_user, sem_imap, monkeypatch
):
    user = await make_user()
    ruim = await _canal_shopee(db, user, "ruim")
    await _canal_shopee(db, user, "boa")
    await _integ(db, user, IntegrationPlatform.AMAZON, "amz")  # sem IMAP: desligado

    async def ler(session, canal, integration, cliente):
        if canal.id == ruim.id:
            raise RuntimeError("quebrou")
        return ResultadoSync(status="ok", mensagens_novas=2)

    lidos = _adaptador(monkeypatch, ler)
    resumo = await sync.sincronizar_tudo()
    assert resumo["canais_criados"] == 1  # o da Amazon, desligado
    assert (resumo["canais"], resumo["ok"], resumo["erro"]) == (2, 1, 1)
    assert resumo["mensagens_novas"] == 2
    assert len(lidos) == 2  # a Amazon desligada nem entra na rodada
    status = {c.integration_id: c.status for c in await _canais(db)}
    assert sorted(status.values()) == ["desligado", "erro", "ok"]


async def test_sincronizar_tudo_aposenta_envio_preso(db, make_user, monkeypatch):
    user = await make_user()
    canal = await _canal_shopee(db, user, "kfa")
    conversa, _ = await gravar.upsert_conversa(
        db,
        canal=canal,
        integration=None,
        plataforma="shopee",
        canal_nome="chat",
        externo_id="c-1",
    )
    preso = AtendimentoMensagem(
        conversa_id=conversa.id, autor="loja", origem="davinci_humano", status="enviando"
    )
    db.add(preso)
    await db.commit()
    await db.execute(
        update(AtendimentoMensagem).values(created_at=datetime.now(UTC) - timedelta(hours=1))
    )
    await db.commit()

    async def ler(session, canal, integration, cliente):
        return ResultadoSync()

    _adaptador(monkeypatch, ler)
    resumo = await sync.sincronizar_tudo()
    assert resumo["envios_presos"] == 1
    await db.refresh(preso)
    assert preso.status == "revisar"


# ─────────────── alertar_prazos ───────────────


async def _conversa_com_prazo(
    db: AsyncSession, *, externo_id: str, prazo: datetime, pedido: str | None = None
) -> AtendimentoConversa:
    c = AtendimentoConversa(
        plataforma="amazon",
        canal="email",
        conta="kfa amz",
        externo_id=externo_id,
        comprador_nome="Fulana de Tal",
        pedido_marketplace=pedido,
        aguardando_resposta=True,
        prazo_resposta_em=prazo,
        ultima_mensagem_resumo="texto do comprador",
    )
    db.add(c)
    await db.commit()
    return c


async def test_alerta_desligado_nao_avisa(db, monkeypatch):
    monkeypatch.setattr(get_settings(), "atendimento_alerta_telegram", False)
    await _conversa_com_prazo(db, externo_id="a", prazo=datetime.now(UTC) - timedelta(hours=1))
    with patch.object(sync.TelegramClient, "safe_send", new=AsyncMock(return_value=True)) as tg:
        assert await sync.alertar_prazos(db) == 0
    tg.assert_not_awaited()


async def test_alerta_avisa_uma_vez_por_nivel_sem_dado_de_comprador(db, monkeypatch):
    monkeypatch.setattr(get_settings(), "atendimento_alerta_telegram", True)
    agora = datetime.now(UTC)
    vencida = await _conversa_com_prazo(
        db, externo_id="vencida", prazo=agora - timedelta(hours=3), pedido="701-1234567-1234567"
    )
    await _conversa_com_prazo(db, externo_id="vencendo", prazo=agora + timedelta(minutes=50))
    await _conversa_com_prazo(db, externo_id="folgada", prazo=agora + timedelta(hours=10))

    with patch.object(sync.TelegramClient, "safe_send", new=AsyncMock(return_value=True)) as tg:
        assert await sync.alertar_prazos(db) == 2
        (texto,) = [c.args[0] for c in tg.call_args_list]
        assert "1 vencida(s), 1 vencendo" in texto
        assert "701-1234567-1234567" in texto
        assert "Fulana" not in texto and "texto do comprador" not in texto
        # Cada linha abre a conversa direto na tela (o `?conversa=` da página).
        assert f"/atendimento?conversa={vencida.id}" in texto

        # Mesma rodada de novo: nada novo a avisar.
        assert await sync.alertar_prazos(db) == 0
        assert tg.await_count == 1


async def test_alerta_devolve_a_reserva_se_o_telegram_falhar(db, monkeypatch):
    monkeypatch.setattr(get_settings(), "atendimento_alerta_telegram", True)
    await _conversa_com_prazo(db, externo_id="v", prazo=datetime.now(UTC) - timedelta(hours=1))
    with patch.object(sync.TelegramClient, "safe_send", new=AsyncMock(return_value=None)):
        assert await sync.alertar_prazos(db) == 0
    with patch.object(sync.TelegramClient, "safe_send", new=AsyncMock(return_value=True)) as tg:
        assert await sync.alertar_prazos(db) == 1
    tg.assert_awaited_once()


async def test_alerta_nao_se_afoga_em_vencidas_velhas(db, monkeypatch):
    """O "ok, obrigado" que ficou na fila, a primeira leitura de 7 dias: 200
    vencidas VELHAS (já avisadas) enchiam o LIMIT pela ordem de prazo, o
    dedupe as descartava — e a conversa que vence em 1 h nem era lida."""
    monkeypatch.setattr(get_settings(), "atendimento_alerta_telegram", True)
    agora = datetime.now(UTC)
    db.add_all(
        [
            AtendimentoConversa(
                plataforma="amazon", canal="email", conta="kfa amz", externo_id=f"velha-{i}",
                aguardando_resposta=True, prazo_resposta_em=agora - timedelta(days=10, minutes=i),
            )
            for i in range(sync.ALERTA_MAX_CONVERSAS + 5)
        ]
    )
    await db.commit()
    nova = await _conversa_com_prazo(db, externo_id="nova", prazo=agora + timedelta(hours=1))
    with patch.object(sync.TelegramClient, "safe_send", new=AsyncMock(return_value=True)) as tg:
        assert await sync.alertar_prazos(db) == 1
        (texto,) = [c.args[0] for c in tg.call_args_list]
    assert f"?conversa={nova.id}" in texto
    assert "0 vencida(s), 1 vencendo" in texto


# ─────────────── worker ───────────────


async def test_crons_do_worker_respeitam_os_settings(monkeypatch):
    from app import worker
    from app.services.atendimento import ia

    # O worker lê o `_settings` do import, não o `get_settings()` da hora: na
    # suíte inteira, testes que dão `get_settings.cache_clear()` na coleta
    # separam os dois objetos (e o `.env` local pode ter a IA ligada).
    s = worker._settings
    monkeypatch.setattr(s, "atendimento_leitura_ativa", False)
    monkeypatch.setattr(s, "atendimento_ia_ativa", False)
    monkeypatch.setattr(s, "atendimento_alerta_telegram", False)
    tudo = AsyncMock(return_value={})
    gerar = AsyncMock(return_value=0)
    alertar = AsyncMock(return_value=0)
    monkeypatch.setattr(sync, "sincronizar_tudo", tudo)
    monkeypatch.setattr(ia, "gerar_pendentes", gerar)
    monkeypatch.setattr(sync, "alertar_prazos", alertar)
    assert await worker.atendimento_sincronizar({}) is None
    await worker.atendimento_rascunhos({})
    await worker.atendimento_prazos({})
    # Desligados, os três saem ANTES de chamar o serviço (não só porque o
    # serviço confere o setting de novo).
    tudo.assert_not_awaited()
    gerar.assert_not_awaited()
    alertar.assert_not_awaited()

    monkeypatch.setattr(s, "atendimento_leitura_ativa", True)
    monkeypatch.setattr(s, "atendimento_ia_ativa", True)
    monkeypatch.setattr(s, "atendimento_alerta_telegram", True)
    tudo.return_value = {"canais": 0}
    assert await worker.atendimento_sincronizar({}) == {"canais": 0}
    await worker.atendimento_rascunhos({})
    await worker.atendimento_prazos({})
    tudo.assert_awaited_once()
    gerar.assert_awaited_once()
    alertar.assert_awaited_once()

    crons = {c.name: c for c in worker.WorkerSettings.cron_jobs}
    assert {
        "cron:atendimento_sincronizar",
        "cron:atendimento_rascunhos",
        "cron:atendimento_prazos",
    } <= set(crons)
    leitura = crons["cron:atendimento_sincronizar"]
    # Minutos ímpares: nunca no :00/:30 dos crons de token da Shopee/ML (que
    # renovam sem a trava do atendimento — o refresh token é de uso único).
    assert leitura.minute == set(range(1, 60, 2))
    assert not {0, 30} & leitura.minute
    assert leitura.timeout_s == 300
    ia_cron = crons["cron:atendimento_rascunhos"]
    assert ia_cron.minute is None and ia_cron.timeout_s == 300  # todo minuto
    prazos = crons["cron:atendimento_prazos"]
    assert prazos.minute == {0, 15, 30, 45} and prazos.timeout_s == 120
    funcoes = {
        getattr(f, "name", getattr(f, "__name__", None)) for f in worker.WorkerSettings.functions
    }
    assert "atendimento_sincronizar" in funcoes
