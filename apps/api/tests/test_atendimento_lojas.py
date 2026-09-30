# ruff: noqa: S105  (tokens de teste de clientes falsos, nada real)
"""O nome da LOJA na caixa (`lojas.nome_da_loja`), não o da integração.

A integração "mega" é a loja "Shopee Marquezini", e o Duoke mostra
"Marquezini". O que se garante aqui:

- o nome vem de `stores.apelido_override`, senão de `companies.apelido`, da
  loja ligada pelos DOIS vínculos que existem no banco
  (`stores.integration_id` e `integrations.store_id`); sem loja, o nome da
  integração, como está;
- o prefixo da plataforma sai ("Shopee Marquezini" → "Marquezini");
- uma consulta por integração por sessão (cache da rodada);
- os quatro adaptadores (Shopee, ML, TikTok, Amazon) gravam esse nome no
  `conta` da conversa — e o nome novo da loja vale na rodada seguinte.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import event, update
from sqlalchemy.ext.asyncio import AsyncSession

import app.db as _db
from app.config import get_settings
from app.models import (
    Company,
    Integration,
    IntegrationPlatform,
    Store,
    User,
)
from app.models.enums import Marketplace
from app.security.cipher import encrypt_json
from app.services.atendimento import amazon_email, lojas
from app.services.atendimento import ml as ml_atd
from tests.test_atendimento_amazon_email import (
    CAIXA,
    PEDIDO_KIA,
    SEM_CONTA,
    CaixaFalsa,
    _conta,
    _email,
    _instalar_caixa,
    _pedido_no_bling,
)
from tests.test_atendimento_amazon_email import RedisFalso as RedisAmazon
from tests.test_atendimento_amazon_email import _conversas as _conversas_amazon
from tests.test_atendimento_ml import T0, ClienteFalso, _pergunta
from tests.test_atendimento_ml import _canal as _canal_ml
from tests.test_atendimento_ml import _conversa as _conversa_ml
from tests.test_atendimento_shopee import AGORA, ShopeeFalso, msg
from tests.test_atendimento_shopee import _canal as _canal_shopee
from tests.test_atendimento_shopee import _conversas as _conversas_shopee
from tests.test_atendimento_shopee import _rodar as _rodar_shopee
from tests.test_atendimento_tiktok import TikTokFalso, tmsg
from tests.test_atendimento_tiktok import _canal as _canal_tiktok
from tests.test_atendimento_tiktok import _conversas as _conversas_tiktok
from tests.test_atendimento_tiktok import _rodar as _rodar_tiktok

# ─────────────── cadastro ───────────────


async def _integ(
    db: AsyncSession,
    user: User,
    nome: str,
    plataforma: IntegrationPlatform = IntegrationPlatform.SHOPEE,
) -> Integration:
    integ = Integration(
        user_id=user.id,
        platform=plataforma,
        name=nome,
        credentials=encrypt_json({"access_token": "t"}),
    )
    db.add(integ)
    await db.commit()
    await db.refresh(integ)
    return integ


async def _loja(
    db: AsyncSession,
    *,
    empresa: str,
    marketplace: Marketplace,
    override: str | None = None,
    integration: Integration | None = None,
    vinculo: str = "loja",
) -> Store:
    """Empresa + loja. `vinculo`: "loja" (`stores.integration_id`) ou
    "integracao" (`integrations.store_id`) — os dois jeitos que o banco tem."""
    company = Company(razao_social=f"{empresa} LTDA", apelido=empresa)
    db.add(company)
    await db.flush()
    store = Store(
        company_id=company.id,
        marketplace=marketplace,
        apelido_override=override,
        integration_id=integration.id if integration is not None and vinculo == "loja" else None,
    )
    db.add(store)
    await db.flush()
    if integration is not None and vinculo == "integracao":
        integration.store_id = store.id
    await db.commit()
    return store


@pytest.fixture
def contar_consultas():
    """Quantas consultas SQL o engine de teste faz (para o cache da rodada)."""
    contagem = {"n": 0}

    def _antes(*_a, **_kw) -> None:
        contagem["n"] += 1

    motor = _db.engine.sync_engine
    event.listen(motor, "before_cursor_execute", _antes)
    yield contagem
    event.remove(motor, "before_cursor_execute", _antes)


# ─────────────── prefixo ───────────────


@pytest.mark.parametrize(
    ("nome", "plataforma", "esperado"),
    [
        ("Shopee Marquezini", "shopee", "Marquezini"),
        ("shopee - Marquezini", "shopee", "Marquezini"),
        ("  Shopee   Marquezini  ", "shopee", "Marquezini"),
        ("Amazon KFA", "amazon", "KFA"),
        ("Mercado Livre X", "ml", "X"),
        ("ML X", "ml", "X"),
        ("MercadoLivre: X", "ml", "X"),
        ("TikTok Shop Jlas", "tiktok", "Jlas"),
        ("TikTok Jlas", "tiktok", "Jlas"),
        # O nome não some; sem separador não é prefixo.
        ("Shopee", "shopee", "Shopee"),
        ("Shopeezinha", "shopee", "Shopeezinha"),
        ("MLK Store", "ml", "MLK Store"),
        # Só o prefixo da PRÓPRIA plataforma (o de outra é erro de cadastro à vista).
        ("Amazon KFA", "shopee", "Amazon KFA"),
        ("Marquezini", "shopee", "Marquezini"),
    ],
)
def test_sem_prefixo_da_plataforma(nome, plataforma, esperado):
    assert lojas.sem_prefixo(nome, plataforma) == esperado


# ─────────────── de onde vem o nome ───────────────


async def test_loja_ligada_por_stores_integration_id(db, make_user):
    integ = await _integ(db, await make_user(), "mega")
    await _loja(
        db,
        empresa="Marquezini Comércio",
        marketplace=Marketplace.SHOPEE,
        override="Shopee Marquezini",
        integration=integ,
        vinculo="loja",
    )
    assert await lojas.nome_da_loja(db, integ) == "Marquezini"


async def test_loja_ligada_por_integrations_store_id(db, make_user):
    """Sem `apelido_override` (ou em branco), vale o apelido da EMPRESA."""
    integ = await _integ(db, await make_user(), "mega")
    await _loja(
        db,
        empresa="Marquezini",
        marketplace=Marketplace.SHOPEE,
        override="   ",
        integration=integ,
        vinculo="integracao",
    )
    assert await lojas.nome_da_loja(db, integ) == "Marquezini"


async def test_os_dois_vinculos_em_lojas_diferentes_vale_o_da_loja(db, make_user):
    integ = await _integ(db, await make_user(), "mega")
    await _loja(db, empresa="Antiga", marketplace=Marketplace.SHOPEE, integration=integ,
                vinculo="integracao")
    await _loja(db, empresa="Marquezini", marketplace=Marketplace.SHOPEE, integration=integ,
                vinculo="loja")
    assert await lojas.nome_da_loja(db, integ) == "Marquezini"


async def test_sem_loja_cai_no_nome_da_integracao_como_esta(db, make_user):
    user = await make_user()
    mega = await _integ(db, user, "mega")
    ml = await _integ(db, user, "ML KFA", IntegrationPlatform.ML)
    assert await lojas.nome_da_loja(db, mega) == "mega"
    # O nome que a equipe digitou na integração não é mexido.
    assert await lojas.nome_da_loja(db, ml) == "ML KFA"
    assert await lojas.nome_da_loja(db, None) == ""


async def test_cache_da_rodada_uma_consulta_por_integracao(db, make_user, contar_consultas):
    integ = await _integ(db, await make_user(), "mega")
    loja = await _loja(db, empresa="Marquezini", marketplace=Marketplace.SHOPEE,
                       override="Shopee Marquezini", integration=integ)

    antes = contar_consultas["n"]
    assert await lojas.nome_da_loja(db, integ) == "Marquezini"
    assert await lojas.nome_da_loja(db, integ) == "Marquezini"
    assert contar_consultas["n"] - antes == 1

    # O cadastro muda no meio da rodada: a rodada segue com o nome que leu...
    async with _db.SessionLocal() as tela:
        await tela.execute(
            update(Store).where(Store.id == loja.id).values(apelido_override="Shopee Marq Outlet")
        )
        await tela.commit()
    assert await lojas.nome_da_loja(db, integ) == "Marquezini"
    # ...e a rodada seguinte (sessão nova) já pega o nome novo.
    async with _db.SessionLocal() as outra:
        assert await lojas.nome_da_loja(outra, integ) == "Marq Outlet"
    lojas.esquecer(db)
    assert await lojas.nome_da_loja(db, integ) == "Marq Outlet"


async def test_upsert_conversa_sem_conta_grava_o_nome_da_loja(db, make_user):
    """Quem grava sem `conta` (a semente local, um adaptador novo) também sai com a loja.

    Os adaptadores já passam `lojas.nome_da_loja`; o `upsert_conversa` é a
    porta única, então é ele que garante o nome da loja quando ninguém mandou.
    """
    from app.services.atendimento import gravar

    user = await make_user()
    mega = await _integ(db, user, "mega")
    await _loja(db, empresa="Marquezini", marketplace=Marketplace.SHOPEE,
                override="Shopee Marquezini", integration=mega, vinculo="integracao")
    velasco = await _integ(db, user, "VELASCO")  # sem loja ligada

    c1, criada = await gravar.upsert_conversa(
        db, canal=None, integration=mega, plataforma="shopee", canal_nome="chat", externo_id="1"
    )
    c2, _ = await gravar.upsert_conversa(
        db, canal=None, integration=velasco, plataforma="shopee", canal_nome="chat",
        externo_id="2",
    )
    assert criada and (c1.conta, c2.conta) == ("Marquezini", "VELASCO")
    # `conta` explícita continua valendo (é o que os adaptadores mandam).
    c1, _ = await gravar.upsert_conversa(
        db, canal=None, integration=mega, plataforma="shopee", canal_nome="chat",
        externo_id="1", conta="Outro nome",
    )
    assert c1.conta == "Outro nome"


# ─────────────── os adaptadores gravam o nome da loja ───────────────


@pytest.fixture
def teto(monkeypatch):
    monkeypatch.setattr(get_settings(), "atendimento_sync_max_conversas", 40)


async def test_shopee_conta_e_o_nome_da_loja_e_acompanha_o_cadastro(db, make_user, teto):
    integ, canal = await _canal_shopee(db, await make_user())  # integração "Loja KFA"
    loja = await _loja(db, empresa="Marquezini", marketplace=Marketplace.SHOPEE,
                       override="Shopee Marquezini", integration=integ, vinculo="loja")
    f = ShopeeFalso()
    f.conversa("A", msg("A", AGORA - timedelta(hours=1), texto="cadê?"))

    await _rodar_shopee(db, canal, integ, f)
    assert (await _conversas_shopee(db))["A"].conta == "Marquezini"

    # A loja muda de nome; na rodada seguinte (outra sessão) a conversa acompanha.
    loja.apelido_override = "Shopee Marquezini Outlet"
    await db.commit()
    lojas.esquecer(db)
    f.conversa("A", msg("A", AGORA - timedelta(minutes=5), texto="e aí?"))
    await _rodar_shopee(db, canal, integ, f)
    assert (await _conversas_shopee(db))["A"].conta == "Marquezini Outlet"


async def test_ml_pergunta_conta_e_o_nome_da_loja(db, make_user, monkeypatch):
    monkeypatch.setattr(ml_atd, "_agora", lambda: T0 + timedelta(minutes=30))
    canal, integ = await _canal_ml(db, make_user, "pergunta")  # integração "ML KFA"
    await _loja(db, empresa="KFA Distribuidora", marketplace=Marketplace.ML,
                override="Mercado Livre KFA", integration=integ, vinculo="integracao")
    cli = ClienteFalso()
    cli.abertas = [_pergunta(101, quando=T0)]

    await ml_atd.sincronizar(db, canal, integ, cli)
    await db.commit()

    assert (await _conversa_ml(db, "q:101")).conta == "KFA"


async def test_tiktok_conta_e_o_nome_da_loja(db, make_user, teto):
    integ, canal = await _canal_tiktok(db, await make_user())  # integração "TikTok Jlas"
    await _loja(db, empresa="Jlas", marketplace=Marketplace.TIKTOK,
                override="TikTok Shop Jlas Oficial", integration=integ, vinculo="loja")
    f = TikTokFalso()
    f.conversa("C1", tmsg(AGORA - timedelta(hours=1), texto="cadê meu pedido?"))

    await _rodar_tiktok(db, canal, integ, f)

    assert (await _conversas_tiktok(db))["C1"].conta == "Jlas Oficial"


@pytest.fixture
def caixa_amazon(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "atendimento_amazon_imap_host", "imap.teste.local")
    monkeypatch.setattr(s, "atendimento_amazon_imap_usuario", "atendimento@loja-teste.com.br")
    monkeypatch.setattr(s, "atendimento_amazon_imap_senha", "senha-de-app")
    monkeypatch.setattr(s, "atendimento_amazon_imap_pasta", "INBOX")
    r = RedisAmazon()
    monkeypatch.setattr(amazon_email, "redis", r)
    return r


async def test_amazon_conta_e_o_nome_da_loja_e_sem_conta_continua(
    db, make_user, caixa_amazon, monkeypatch
):
    """O e-mail "+kfa" vai para a integração "kfa", ligada à loja "Amazon KFA
    Oficial": a conversa mostra "KFA Oficial". O e-mail sem dono segue na fila
    "sem conta" (não há loja para mostrar)."""
    _instalar_caixa(
        monkeypatch,
        CaixaFalsa(
            {
                10: _email(),  # para atendimento+kfa@...
                11: _email(
                    para="atendimento@loja-teste.com.br",
                    de='"Ana - Amazon Marketplace" <qwe456ana@marketplace.amazon.com.br>',
                    assunto="Dúvida",
                    message_id="<m-ana@a>",
                    texto="Oi?",
                    marcadores=False,
                ),
            }
        ),
    )
    user = await make_user()
    kfa, canal_kfa = await _conta(db, user, "kfa")
    await _loja(db, empresa="KFA", marketplace=Marketplace.AMAZON,
                override="Amazon KFA Oficial", integration=kfa, vinculo="integracao")

    r = await amazon_email.sincronizar(db, canal_kfa, kfa, None)
    await db.commit()

    assert r.status == "ok"
    contas = sorted((c.integration_id is not None, c.conta) for c in await _conversas_amazon(db))
    assert contas == [(False, SEM_CONTA), (True, "KFA Oficial")]



async def test_amazon_conversa_sem_conta_adotada_ganha_o_nome_da_loja(
    db, make_user, caixa_amazon, monkeypatch
):
    """A conversa que nasceu "sem conta" e é adotada quando o pedido aparece no
    Bling passa a mostrar o nome da LOJA da conta que a adotou."""
    caixa = _instalar_caixa(
        monkeypatch,
        CaixaFalsa({10: _email(para=CAIXA, assunto=f"Pedido {PEDIDO_KIA}", texto="Oi?")}),
    )
    user = await make_user()
    kfa, canal_kfa = await _conta(db, user, "kfa")
    kia, _canal_kia = await _conta(db, user, "kia", bling_loja_id=555)
    await _loja(db, empresa="KIA", marketplace=Marketplace.AMAZON,
                override="Amazon KIA Store", integration=kia, vinculo="loja")
    await amazon_email.sincronizar(db, canal_kfa, kfa, None)
    await db.commit()
    [orfa] = await _conversas_amazon(db)
    assert (orfa.integration_id, orfa.conta) == (None, SEM_CONTA)

    await _pedido_no_bling(db, PEDIDO_KIA, "555")
    caixa.emails[11] = _email(
        para=CAIXA,
        assunto=f"RE: Pedido {PEDIDO_KIA}",
        message_id="<m2@a>",
        data="Thu, 24 Sep 2026 12:00:00 -0300",
        texto="Alguém?",
    )
    caixa_amazon.proxima_rodada()
    await amazon_email.sincronizar(db, canal_kfa, kfa, None)
    await db.commit()

    [adotada] = await _conversas_amazon(db)
    assert (adotada.id, adotada.integration_id, adotada.conta) == (orfa.id, kia.id, "KIA Store")
