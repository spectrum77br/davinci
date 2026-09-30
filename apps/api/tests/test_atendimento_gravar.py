# ruff: noqa: S105  (tokens de teste de um cliente falso, nada real)
"""A porta de escrita do atendimento: idempotência, autor × origem, fila e token.

Estes testes medem as garantias que os adaptadores (Shopee, ML, TikTok,
Amazon) e o envio vão TOMAR COMO CERTAS:

- a mesma mensagem, trazida pela consulta em toda rodada, vira UMA linha;
- a nossa própria resposta, voltando pelo sync, é ADOTADA — não duplica;
- resposta dada fora do DaVinci é `externo` e aposenta o rascunho da IA;
- a fila (aguardando/prazo/situação) sai certa para cada plataforma;
- as duas travas do banco (envio em voo, rascunho pendente) seguram;
- o token renovado sobrevive ao rollback de quem chamou, e só um renova.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

import app.db as _db
from app.models import (
    AtendimentoCanal,
    AtendimentoConversa,
    AtendimentoMensagem,
    AtendimentoRascunho,
    Integration,
    IntegrationPlatform,
    User,
)
from app.security.cipher import decrypt_json, encrypt_json
from app.services.atendimento import clientes, gravar
from app.services.atendimento.constantes import (
    limite_caracteres,
    sla_horas,
)
from app.services.marketplaces import factory

T0 = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)


# ─────────────── fixtures ───────────────


async def _integ(
    db: AsyncSession,
    user: User,
    platform: IntegrationPlatform = IntegrationPlatform.SHOPEE,
    nome: str = "kfa",
    creds: dict | None = None,
) -> Integration:
    integ = Integration(
        user_id=user.id,
        platform=platform,
        name=nome,
        credentials=encrypt_json(
            creds or {"access_token": "velho", "refresh_token": "rt-velho", "expires_at": 1}
        ),
    )
    db.add(integ)
    await db.commit()
    await db.refresh(integ)
    return integ


async def _canal(db: AsyncSession, integ: Integration, plataforma: str, canal: str):
    c = AtendimentoCanal(
        integration_id=integ.id, plataforma=plataforma, canal=canal
    )
    db.add(c)
    await db.commit()
    return c


async def _conversa(
    db: AsyncSession,
    make_user,
    *,
    platform: IntegrationPlatform = IntegrationPlatform.SHOPEE,
    plataforma: str = "shopee",
    canal: str = "chat",
    externo_id: str = "conv-1",
) -> AtendimentoConversa:
    user = await make_user()
    integ = await _integ(db, user, platform)
    c = await _canal(db, integ, plataforma, canal)
    conversa, criada = await gravar.upsert_conversa(
        db,
        canal=c,
        integration=integ,
        plataforma=plataforma,
        canal_nome=canal,
        externo_id=externo_id,
    )
    assert criada
    await db.commit()
    return conversa


async def _contar(db: AsyncSession, conversa_id) -> int:
    return int(
        await db.scalar(
            select(func.count())
            .select_from(AtendimentoMensagem)
            .where(AtendimentoMensagem.conversa_id == conversa_id)
        )
    )


# ─────────────── upsert_conversa ───────────────


async def test_upsert_idempotente_e_none_nao_sobrescreve(db: AsyncSession, make_user):
    user = await make_user()
    integ = await _integ(db, user, nome="Loja KFA")
    canal = await _canal(db, integ, "shopee", "chat")

    c1, criada1 = await gravar.upsert_conversa(
        db,
        canal=canal,
        integration=integ,
        plataforma="shopee",
        canal_nome="chat",
        externo_id=123456789,  # a Shopee manda número; vira texto
        comprador_nome="Ana",
        pedido_marketplace="250925ABC",
        nao_lidas=2,
        dados={"to_id": 1},
    )
    await db.commit()
    assert criada1
    assert c1.externo_id == "123456789"
    assert c1.conta == "Loja KFA"  # snapshot do nome da integração
    assert c1.canal_id == canal.id

    # Leitura parcial seguinte: sem nome, sem pedido — não pode apagar nada.
    c2, criada2 = await gravar.upsert_conversa(
        db,
        canal=canal,
        integration=integ,
        plataforma="shopee",
        canal_nome="chat",
        externo_id="123456789",
        comprador_nome=None,
        anuncio_titulo="Mala 20",
        nao_lidas=0,
        dados={"pinned": True},
    )
    await db.commit()
    assert not criada2
    assert c2.id == c1.id
    assert c2.comprador_nome == "Ana"
    assert c2.pedido_marketplace == "250925ABC"
    assert c2.anuncio_titulo == "Mala 20"
    assert c2.nao_lidas == 0  # zero é valor, não "ausente"
    assert c2.dados == {"to_id": 1, "pinned": True}

    total = await db.scalar(select(func.count()).select_from(AtendimentoConversa))
    assert total == 1


async def test_upsert_foto_do_comprador_none_e_vazio_nao_apagam(db: AsyncSession, make_user):
    """A foto (`to_avatar` da Shopee) é como os outros campos: a leitura que não
    a traz — ou que traz vazio — não apaga a que já estava. E só URL que serve
    num `<img>` entra: `javascript:`/`data:`/outro site sem esquema, não."""
    user = await make_user()
    integ = await _integ(db, user)
    canal = await _canal(db, integ, "shopee", "chat")

    async def upsert(**kw):
        c, _ = await gravar.upsert_conversa(
            db, canal=canal, integration=integ, plataforma="shopee", canal_nome="chat",
            externo_id="av-1", **kw,
        )
        await db.commit()
        return c

    sem_foto = await upsert(comprador_nome="Ana")
    assert sem_foto.comprador_avatar is None

    foto = "https://cf.shopee.com.br/file/br-11134233-abc_tn"
    c = await upsert(comprador_avatar=f"  {foto} ")
    assert c.comprador_avatar == foto
    for nada in (None, "", "   ", "javascript:alert(1)", "data:image/png;base64,AAAA",
                 "//outro.site/x.png", "https://x/" + "a" * 3000, "https://a b/c.png"):
        c = await upsert(comprador_avatar=nada)
        assert c.comprador_avatar == foto, nada

    # Trocou a foto na plataforma: a nova vale. Caminho da própria web (a
    # semente local serve PNGs em /atendimento-demo/) também.
    c = await upsert(comprador_avatar="https://cf.shopee.com.br/file/nova")
    assert c.comprador_avatar == "https://cf.shopee.com.br/file/nova"
    c = await upsert(comprador_avatar="/atendimento-demo/1.png")
    assert c.comprador_avatar == "/atendimento-demo/1.png"

    # Conversa que NASCE com foto também a guarda (o INSERT usa os mesmos campos).
    nova, criada = await gravar.upsert_conversa(
        db, canal=canal, integration=integ, plataforma="shopee", canal_nome="chat",
        externo_id="av-2", comprador_avatar="http://cdn.exemplo/foto.jpg",
    )
    assert criada and nova.comprador_avatar == "http://cdn.exemplo/foto.jpg"
    assert gravar.url_avatar(123) is None


def test_tipo_previa_da_lista():
    assert [gravar.tipo_previa(t) for t in ("texto", "imagem", "produto", "pedido")] == [
        "texto", "imagem", "produto", "pedido",
    ]
    assert [gravar.tipo_previa(t) for t in ("video", "arquivo", "outro", "sticker")] == [
        "outro"
    ] * 4
    assert gravar.tipo_previa(None) is None


async def test_upsert_mesma_chave_em_outro_canal_e_outra_conversa(
    db: AsyncSession, make_user
):
    """O ML tem pergunta e pós-venda na MESMA integração: ids podem colidir."""
    user = await make_user()
    integ = await _integ(db, user, IntegrationPlatform.ML)
    a, _ = await gravar.upsert_conversa(
        db, canal=None, integration=integ, plataforma="ml", canal_nome="pergunta",
        externo_id="42",
    )
    b, criada = await gravar.upsert_conversa(
        db, canal=None, integration=integ, plataforma="ml", canal_nome="pos_venda",
        externo_id="42",
    )
    assert criada
    assert a.id != b.id


async def test_corrida_na_criacao_nao_derruba_a_rodada(
    db: AsyncSession, make_user, monkeypatch
):
    """Outro processo cria a mesma conversa/mensagem entre a busca e o INSERT.

    O UNIQUE barra, o SAVEPOINT desfaz só aquela linha e o trabalho que já
    estava na sessão (o resto da rodada do sync) continua de pé.
    """
    conversa = await _conversa(db, make_user)
    await gravar.gravar_mensagem(
        db, conversa, externo_id="m-1", autor="cliente", texto="Oi", enviada_em=T0
    )
    await db.commit()

    reais = (gravar._buscar_conversa, gravar._mensagem_por_externo)
    cego = {"conversa": True, "mensagem": True}

    async def _busca_cega(session, **kw):
        if cego["conversa"]:
            cego["conversa"] = False
            return None  # "não achei" — mas a linha existe
        return await reais[0](session, **kw)

    async def _msg_cega(session, conversa_id, externo_id):
        if cego["mensagem"]:
            cego["mensagem"] = False
            return None
        return await reais[1](session, conversa_id, externo_id)

    monkeypatch.setattr(gravar, "_buscar_conversa", _busca_cega)
    monkeypatch.setattr(gravar, "_mensagem_por_externo", _msg_cega)

    # Trabalho da rodada que já estava na sessão antes da corrida.
    conversa.nao_lidas = 7
    integ = await db.get(Integration, conversa.integration_id)
    mesma, criada = await gravar.upsert_conversa(
        db, canal=None, integration=integ, plataforma="shopee", canal_nome="chat",
        externo_id=conversa.externo_id, comprador_nome="Bia",
    )
    assert not criada and mesma.id == conversa.id
    assert mesma.comprador_nome == "Bia"

    m, criada = await gravar.gravar_mensagem(
        db, conversa, externo_id="m-1", autor="cliente", texto="Oi", enviada_em=T0
    )
    assert not criada
    await db.commit()
    await db.refresh(conversa)
    assert conversa.nao_lidas == 7
    assert await _contar(db, conversa.id) == 1


# ─────────────── gravar_mensagem: idempotência e origem ───────────────


async def test_gravar_mensagem_idempotente(db: AsyncSession, make_user):
    conversa = await _conversa(db, make_user)
    m1, criada1 = await gravar.gravar_mensagem(
        db, conversa, externo_id="m-1", autor="cliente", texto="Oi, cadê meu pedido?",
        enviada_em=T0,
    )
    await db.commit()
    m2, criada2 = await gravar.gravar_mensagem(
        db, conversa, externo_id="m-1", autor="cliente", texto="Oi, cadê meu pedido?",
        enviada_em=T0,
    )
    await db.commit()
    assert criada1 and not criada2
    assert m1.id == m2.id
    assert await _contar(db, conversa.id) == 1


async def test_origem_do_cliente_e_da_loja_por_fora(db: AsyncSession, make_user):
    conversa = await _conversa(db, make_user)
    cli, _ = await gravar.gravar_mensagem(
        db, conversa, externo_id="m-1", autor="cliente", texto="Tem na cor azul?",
        enviada_em=T0,
    )
    loja, criada = await gravar.gravar_mensagem(
        db, conversa, externo_id="m-2", autor="loja", texto="Temos sim!",
        enviada_em=T0 + timedelta(minutes=5),
    )
    await db.commit()
    assert cli.origem == "cliente" and cli.status == "recebida"
    # Ninguém mandou isso pelo DaVinci: foi o Duoke / Seller Center.
    assert criada
    assert loja.origem == "externo" and loja.status == "enviada"
    sis, _ = await gravar.gravar_mensagem(
        db, conversa, externo_id="m-3", autor="sistema", texto=None, tipo="pedido",
        enviada_em=T0 + timedelta(minutes=6),
    )
    assert sis.origem == "sistema"


async def test_autor_desconhecido_e_erro_de_programacao(db: AsyncSession, make_user):
    conversa = await _conversa(db, make_user)
    with pytest.raises(ValueError):
        await gravar.gravar_mensagem(
            db, conversa, externo_id="x", autor="comprador", texto="oi", enviada_em=T0
        )


async def test_adota_a_nossa_resposta_quando_o_sync_a_traz_de_volta(
    db: AsyncSession, make_user
):
    conversa = await _conversa(db, make_user)
    # O envio pelo DaVinci: linha em voo, ainda sem id da plataforma.
    nossa = AtendimentoMensagem(
        conversa_id=conversa.id, autor="loja", origem="davinci_humano",
        texto="Olá! Seu pedido já foi enviado — chega em breve.", status="enviando",
    )
    db.add(nossa)
    await db.commit()

    # A plataforma devolve a mesma frase, sem acento/travessão, pela consulta.
    volta, criada = await gravar.gravar_mensagem(
        db, conversa, externo_id="shp-777", autor="loja",
        texto="ola  seu pedido ja foi enviado chega em breve",
        enviada_em=datetime.now(UTC), payload={"message_id": "shp-777"},
    )
    await db.commit()
    assert not criada
    assert volta.id == nossa.id
    assert volta.origem == "davinci_humano"  # continua nossa, não vira `externo`
    assert volta.externo_id == "shp-777"
    assert volta.status == "enviada"
    assert volta.payload["sync"] == {"message_id": "shp-777"}
    assert await _contar(db, conversa.id) == 1

    # E a rodada seguinte, com o mesmo id, é idempotente.
    de_novo, criada = await gravar.gravar_mensagem(
        db, conversa, externo_id="shp-777", autor="loja", texto="qualquer",
        enviada_em=datetime.now(UTC),
    )
    assert not criada and de_novo.id == nossa.id


async def test_ambigua_que_saiu_e_adotada(db: AsyncSession, make_user):
    """`revisar` (timeout) que a plataforma mostra = saiu. A adoção é a prova."""
    conversa = await _conversa(db, make_user)
    nossa = AtendimentoMensagem(
        conversa_id=conversa.id, autor="loja", origem="davinci_ia",
        texto="Obrigado pela compra!", status="revisar", erro="timeout",
    )
    db.add(nossa)
    await db.commit()
    volta, _ = await gravar.gravar_mensagem(
        db, conversa, externo_id="e-1", autor="loja", texto="Obrigado pela compra!",
        enviada_em=datetime.now(UTC),
    )
    assert volta.id == nossa.id
    assert volta.status == "enviada" and volta.erro is None


async def test_nao_adota_fora_da_janela_nem_texto_diferente(db: AsyncSession, make_user):
    conversa = await _conversa(db, make_user)
    db.add(
        AtendimentoMensagem(
            conversa_id=conversa.id, autor="loja", origem="davinci_humano",
            texto="Seu pedido saiu hoje.", status="enviada",
        )
    )
    await db.commit()
    agora = datetime.now(UTC)
    outra, criada = await gravar.gravar_mensagem(
        db, conversa, externo_id="e-1", autor="loja", texto="Seu pedido saiu ontem.",
        enviada_em=agora,
    )
    assert criada and outra.origem == "externo"
    longe, criada = await gravar.gravar_mensagem(
        db, conversa, externo_id="e-2", autor="loja", texto="Seu pedido saiu hoje.",
        enviada_em=agora + timedelta(hours=2),
    )
    assert criada and longe.origem == "externo"
    # Falhou = não saiu: nunca é adotada.
    db.add(
        AtendimentoMensagem(
            conversa_id=conversa.id, autor="loja", origem="davinci_humano",
            texto="Pode confirmar o endereço?", status="falhou",
        )
    )
    await db.commit()
    volta, criada = await gravar.gravar_mensagem(
        db, conversa, externo_id="e-3", autor="loja", texto="Pode confirmar o endereço?",
        enviada_em=datetime.now(UTC),
    )
    assert criada and volta.origem == "externo"


# ─────────────── recalcular: fila, prazo, situação ───────────────


@pytest.mark.parametrize(
    ("platform", "plataforma", "canal", "horas"),
    [
        (IntegrationPlatform.SHOPEE, "shopee", "chat", 12),
        (IntegrationPlatform.ML, "ml", "pergunta", 1),
        (IntegrationPlatform.ML, "ml", "pos_venda", 24),
        (IntegrationPlatform.TIKTOK, "tiktok", "chat", 24),
        (IntegrationPlatform.AMAZON, "amazon", "email", 24),
    ],
)
async def test_prazo_pelo_sla_de_cada_canal(
    db: AsyncSession, make_user, platform, plataforma, canal, horas
):
    conversa = await _conversa(db, make_user, platform=platform, plataforma=plataforma,
                               canal=canal)
    await gravar.gravar_mensagem(
        db, conversa, externo_id="c-1", autor="cliente", texto="Oi", enviada_em=T0
    )
    await db.commit()
    assert sla_horas(plataforma, canal) == horas
    assert conversa.aguardando_resposta is True
    assert conversa.prazo_resposta_em == T0 + timedelta(hours=horas)
    assert conversa.situacao == "aberta"
    assert conversa.ultima_do_cliente_em == T0
    assert conversa.ultima_autor == "cliente"


def test_limites_e_padroes():
    assert limite_caracteres("ml", "pos_venda") == 350
    assert limite_caracteres("ml", "pergunta") == 2000
    assert limite_caracteres("xpto", "chat") == 1000
    assert sla_horas("xpto", "chat") == 24


async def test_resposta_da_loja_tira_da_fila(db: AsyncSession, make_user):
    conversa = await _conversa(db, make_user)
    await gravar.gravar_mensagem(
        db, conversa, externo_id="c-1", autor="cliente", texto="Oi", enviada_em=T0
    )
    await gravar.gravar_mensagem(
        db, conversa, externo_id="l-1", autor="loja", texto="Olá!",
        enviada_em=T0 + timedelta(minutes=3),
    )
    await db.commit()
    assert conversa.aguardando_resposta is False
    assert conversa.prazo_resposta_em is None
    assert conversa.situacao == "respondida"
    assert conversa.ultima_da_loja_em == T0 + timedelta(minutes=3)
    assert conversa.ultima_mensagem_resumo == "Olá!"

    # Mensagem VELHA chegando atrasada não muda a última.
    await gravar.gravar_mensagem(
        db, conversa, externo_id="c-0", autor="cliente", texto="(antiga)",
        enviada_em=T0 - timedelta(days=1),
    )
    assert conversa.ultima_mensagem_resumo == "Olá!"
    assert conversa.aguardando_resposta is False

    # Cliente volta a falar: volta para a fila, prazo conta da NOVA mensagem.
    await gravar.gravar_mensagem(
        db, conversa, externo_id="c-2", autor="cliente", texto="E o rastreio?",
        enviada_em=T0 + timedelta(hours=1),
    )
    assert conversa.aguardando_resposta is True
    assert conversa.prazo_resposta_em == T0 + timedelta(hours=13)
    assert conversa.situacao == "aberta"


async def test_resumo_uma_linha_ate_160(db: AsyncSession, make_user):
    conversa = await _conversa(db, make_user)
    longo = "linha um\n\nlinha   dois " + "x" * 300
    await gravar.gravar_mensagem(
        db, conversa, externo_id="c-1", autor="cliente", texto=longo, enviada_em=T0
    )
    assert "\n" not in conversa.ultima_mensagem_resumo
    assert len(conversa.ultima_mensagem_resumo) == 160
    assert conversa.ultima_mensagem_resumo.startswith("linha um linha dois x")
    assert conversa.ultima_mensagem_resumo.endswith("…")
    await gravar.gravar_mensagem(
        db, conversa, externo_id="c-2", autor="cliente", texto=None, tipo="imagem",
        enviada_em=T0 + timedelta(minutes=1),
    )
    assert conversa.ultima_mensagem_resumo == "[imagem]"
    assert gravar.resumo(None) == ""


async def test_fechada_e_bloqueada_preservadas(db: AsyncSession, make_user):
    conversa = await _conversa(db, make_user)
    await gravar.gravar_mensagem(
        db, conversa, externo_id="c-1", autor="cliente", texto="Oi", enviada_em=T0
    )
    conversa.situacao = "fechada"
    await gravar.recalcular_conversa(db, conversa)
    await db.commit()
    assert conversa.situacao == "fechada"
    # Fechada por pessoa = resolvida: sai da fila e do alerta de prazo.
    assert conversa.aguardando_resposta is False
    assert conversa.prazo_resposta_em is None

    conversa.situacao = "bloqueada"
    conversa.bloqueio_motivo = "blocked_by_time"
    await gravar.recalcular_conversa(db, conversa)
    assert conversa.situacao == "bloqueada"
    # Bloqueada segue na fila: alguém precisa agir por outro caminho.
    assert conversa.aguardando_resposta is True

    # Uma resposta da loja não "desbloqueia" nada sozinha.
    await gravar.gravar_mensagem(
        db, conversa, externo_id="l-1", autor="loja", texto="Oi!",
        enviada_em=T0 + timedelta(minutes=1),
    )
    assert conversa.situacao == "bloqueada"


async def test_cliente_escreve_de_novo_reabre_fechada(db: AsyncSession, make_user):
    conversa = await _conversa(db, make_user)
    await gravar.gravar_mensagem(
        db, conversa, externo_id="c-1", autor="cliente", texto="Obrigado!", enviada_em=T0
    )
    conversa.situacao = "fechada"
    conversa.sem_resposta_necessaria = True
    await gravar.recalcular_conversa(db, conversa)
    await db.commit()

    # A mesma mensagem de novo (rodada seguinte do sync) não reabre.
    await gravar.gravar_mensagem(
        db, conversa, externo_id="c-1", autor="cliente", texto="Obrigado!", enviada_em=T0
    )
    assert conversa.situacao == "fechada"

    await gravar.gravar_mensagem(
        db, conversa, externo_id="c-2", autor="cliente", texto="Mas veio quebrado",
        enviada_em=T0 + timedelta(days=1),
    )
    assert conversa.situacao == "aberta"
    assert conversa.sem_resposta_necessaria is False
    assert conversa.aguardando_resposta is True


async def test_sem_resposta_necessaria_tira_da_fila(db: AsyncSession, make_user):
    conversa = await _conversa(
        db, make_user, platform=IntegrationPlatform.AMAZON, plataforma="amazon",
        canal="email",
    )
    await gravar.gravar_mensagem(
        db, conversa, externo_id="<a@b>", autor="cliente", texto="Obrigado!", enviada_em=T0
    )
    assert conversa.aguardando_resposta is True
    conversa.sem_resposta_necessaria = True
    await gravar.recalcular_conversa(db, conversa)
    assert conversa.aguardando_resposta is False
    assert conversa.prazo_resposta_em is None
    # Ninguém respondeu: NÃO vira "respondida" (D5). A situação fica como
    # estava; a tela mostra o selo pelo campo `sem_resposta_necessaria`.
    assert conversa.situacao == "aberta"
    await db.commit()

    # Mensagem nova do cliente zera o "não precisa" e volta para a fila.
    await gravar.gravar_mensagem(
        db, conversa, externo_id="<c@b>", autor="cliente", texto="E a nota fiscal?",
        enviada_em=T0 + timedelta(hours=1),
    )
    assert conversa.sem_resposta_necessaria is False
    assert conversa.aguardando_resposta is True
    assert conversa.situacao == "aberta"


async def test_resposta_que_falhou_nao_conta(db: AsyncSession, make_user):
    conversa = await _conversa(db, make_user)
    await gravar.gravar_mensagem(
        db, conversa, externo_id="c-1", autor="cliente", texto="Oi", enviada_em=T0
    )
    nossa = AtendimentoMensagem(
        conversa_id=conversa.id, autor="loja", origem="davinci_humano", texto="Olá",
        status="enviando", enviada_em=T0 + timedelta(minutes=1),
    )
    db.add(nossa)
    await gravar.recalcular_conversa(db, conversa)
    # Em voo conta como resposta: não se responde por cima de uma que pode sair.
    assert conversa.aguardando_resposta is False

    nossa.status = "falhou"
    await gravar.recalcular_conversa(db, conversa)
    assert conversa.aguardando_resposta is True
    assert conversa.ultima_da_loja_em is None
    assert conversa.prazo_resposta_em == T0 + timedelta(hours=12)


async def test_recalcular_conversa_sem_mensagens(db: AsyncSession, make_user):
    conversa = await _conversa(db, make_user)
    await gravar.recalcular_conversa(db, conversa)
    assert conversa.ultima_mensagem_em is None
    assert conversa.aguardando_resposta is False


# ─────────────── travas do banco ───────────────


async def test_uma_resposta_em_voo_por_conversa(db: AsyncSession, make_user):
    conversa = await _conversa(db, make_user)
    db.add(
        AtendimentoMensagem(
            conversa_id=conversa.id, autor="loja", origem="davinci_humano",
            texto="primeira", status="enviando",
        )
    )
    await db.commit()
    # Enviada + enviando convivem; outra conversa também pode ter a dela.
    db.add(
        AtendimentoMensagem(
            conversa_id=conversa.id, autor="loja", origem="davinci_humano",
            texto="antiga", status="enviada",
        )
    )
    await db.commit()

    db.add(
        AtendimentoMensagem(
            conversa_id=conversa.id, autor="loja", origem="davinci_ia",
            texto="segunda", status="enviando",
        )
    )
    with pytest.raises(IntegrityError) as exc:
        await db.commit()
    assert "uq_atendimento_envio_em_voo" in str(exc.value)
    await db.rollback()
    assert _db.is_unique_violation(exc.value)


async def test_um_rascunho_pendente_por_conversa(db: AsyncSession, make_user):
    conversa = await _conversa(db, make_user)
    db.add(AtendimentoRascunho(conversa_id=conversa.id, texto="a", status="pendente"))
    db.add(AtendimentoRascunho(conversa_id=conversa.id, texto="b", status="descartado"))
    await db.commit()
    db.add(AtendimentoRascunho(conversa_id=conversa.id, texto="c", status="pendente"))
    with pytest.raises(IntegrityError) as exc:
        await db.commit()
    assert "uq_atendimento_rascunho_pendente" in str(exc.value)
    await db.rollback()


async def test_resposta_por_fora_aposenta_o_rascunho(db: AsyncSession, make_user):
    conversa = await _conversa(db, make_user)
    await gravar.gravar_mensagem(
        db, conversa, externo_id="c-1", autor="cliente", texto="Cadê?",
        enviada_em=datetime.now(UTC) - timedelta(minutes=10),
    )
    rascunho = AtendimentoRascunho(conversa_id=conversa.id, texto="Já saiu!")
    db.add(rascunho)
    await db.commit()

    # Resposta por fora MAIS VELHA que a sugestão (sync atrasado): fica.
    await gravar.gravar_mensagem(
        db, conversa, externo_id="l-0", autor="loja", texto="Um momento",
        enviada_em=datetime.now(UTC) - timedelta(minutes=5),
    )
    await db.commit()
    assert rascunho.status == "pendente"

    # Duoke respondeu depois da sugestão: ela não serve mais.
    await gravar.gravar_mensagem(
        db, conversa, externo_id="l-1", autor="loja", texto="Enviado hoje, segue o código",
        enviada_em=datetime.now(UTC) + timedelta(minutes=1),
    )
    await db.commit()
    await db.refresh(rascunho)
    assert rascunho.status == "substituido"


async def test_recalcular_conversa_tambem_aposenta_rascunho(db: AsyncSession, make_user):
    conversa = await _conversa(db, make_user)
    rascunho = AtendimentoRascunho(conversa_id=conversa.id, texto="Sugestão")
    db.add(rascunho)
    await db.commit()
    db.add(
        AtendimentoMensagem(
            conversa_id=conversa.id, autor="loja", origem="externo", texto="Respondi",
            status="enviada", enviada_em=datetime.now(UTC) + timedelta(minutes=1),
        )
    )
    await gravar.recalcular_conversa(db, conversa)
    await db.commit()
    await db.refresh(rascunho)
    assert rascunho.status == "substituido"


def test_normalizar_para_comparar():
    a = gravar.normalizar_para_comparar("Olá!  Seu pedido — “já” saiu… 😀")
    b = gravar.normalizar_para_comparar("ola seu pedido ja saiu")
    assert a == b == "ola seu pedido ja saiu"
    assert gravar.normalizar_para_comparar(None) == ""


# ─────────────── clientes: token em sessão própria + trava ───────────────


class _ClienteFalso:
    """Imita o contrato dos clientes reais: `creds`, `refresh()` e o callback."""

    def __init__(self, creds, on_token_refresh=None, integration_id=None):
        self.creds = dict(creds)
        self._on_refresh = on_token_refresh
        self.renovacoes = 0

    async def refresh(self) -> None:
        self.renovacoes += 1
        self.creds.update(
            {
                "access_token": f"novo-{self.renovacoes}",
                "refresh_token": f"rt-novo-{self.renovacoes}",
                "expires_at": 2_000_000_000,
            }
        )
        if self._on_refresh:
            await self._on_refresh(self.creds)

    async def chamada(self) -> str:
        """Um método de API qualquer que renova antes (como `_request`)."""
        await self.refresh()
        return self.creds["access_token"]


@pytest.fixture
def cliente_falso(monkeypatch):
    feitos: list[_ClienteFalso] = []

    def _client_for(platform, creds, on_token_refresh=None, integration_id=None):
        c = _ClienteFalso(creds, on_token_refresh, integration_id)
        feitos.append(c)
        return c

    monkeypatch.setattr(factory, "client_for", _client_for)
    return feitos


def _trava(monkeypatch, *, pegou: bool) -> list[str]:
    chamadas: list[str] = []

    @asynccontextmanager
    async def _lock(integration_id):
        chamadas.append(f"lock:{integration_id}")
        yield pegou

    async def _espera(integration_id):
        chamadas.append(f"espera:{integration_id}")
        return True

    monkeypatch.setattr(clientes, "token_refresh_lock", _lock)
    monkeypatch.setattr(clientes, "wait_for_refresh", _espera)
    return chamadas


async def _creds_no_banco(integration_id) -> tuple[dict, datetime | None]:
    async with _db.SessionLocal() as s:
        integ = await s.get(Integration, integration_id)
        return decrypt_json(integ.credentials), integ.token_expires_at


async def test_token_novo_sobrevive_ao_rollback_de_quem_chamou(
    db: AsyncSession, make_user, cliente_falso, monkeypatch
):
    chamadas = _trava(monkeypatch, pegou=True)
    user = await make_user()
    integ = await _integ(db, user)

    # A sessão da rodada de sync: começa a gravar coisas...
    async with _db.SessionLocal() as sessao_sync:
        integ_sync = await sessao_sync.get(Integration, integ.id)
        cliente = await clientes.cliente_da_integracao(integ_sync)
        sessao_sync.add(
            AtendimentoCanal(integration_id=integ.id, plataforma="shopee", canal="chat")
        )
        await sessao_sync.flush()
        assert await cliente.chamada() == "novo-1"
        # O espelho na instância do chamador NÃO a marca como alterada.
        assert decrypt_json(integ_sync.credentials)["access_token"] == "novo-1"
        assert integ_sync not in sessao_sync.dirty
        # ...e dá rollback por qualquer motivo depois.
        await sessao_sync.rollback()

    creds, expira = await _creds_no_banco(integ.id)
    assert creds["access_token"] == "novo-1"
    assert creds["refresh_token"] == "rt-novo-1"  # uso único: não pode se perder
    assert expira == datetime.fromtimestamp(2_000_000_000, tz=UTC)
    # O canal da rodada que deu rollback não ficou.
    assert await db.scalar(select(func.count()).select_from(AtendimentoCanal)) == 0
    assert chamadas == [f"lock:{integ.id}"]
    assert cliente_falso[0].renovacoes == 1


async def test_quem_nao_pega_a_trava_espera_e_rele_do_banco(
    db: AsyncSession, make_user, cliente_falso, monkeypatch
):
    chamadas = _trava(monkeypatch, pegou=False)
    user = await make_user()
    integ = await _integ(db, user)
    cliente = await clientes.cliente_da_integracao(integ)

    # Outro processo (o cron de token, o envio pela tela) renovou e gravou.
    await clientes.gravar_credenciais(
        integ.id, {"access_token": "do-outro", "refresh_token": "rt-do-outro",
                   "expires_at": 2_000_000_000},
    )
    await cliente.refresh()

    assert cliente_falso[0].renovacoes == 0  # não gastou o refresh token velho
    assert cliente.creds["access_token"] == "do-outro"
    assert chamadas == [f"lock:{integ.id}", f"espera:{integ.id}"]


async def test_pegou_a_trava_mas_o_banco_ja_tem_token_novo(
    db: AsyncSession, make_user, cliente_falso, monkeypatch
):
    """Conferência dupla: alguém renovou entre montar o cliente e renovar."""
    _trava(monkeypatch, pegou=True)
    user = await make_user()
    integ = await _integ(db, user)
    cliente = await clientes.cliente_da_integracao(integ)
    await clientes.gravar_credenciais(
        integ.id, {"access_token": "renovado-antes", "refresh_token": "rt-2",
                   "expires_at": 2_000_000_000},
    )
    await cliente.refresh()
    assert cliente_falso[0].renovacoes == 0
    assert cliente.creds["refresh_token"] == "rt-2"


async def test_expiracao_em_varios_formatos():
    assert clientes._expira_em({"expires_at": 1_900_000_000}) == datetime.fromtimestamp(
        1_900_000_000, tz=UTC
    )
    assert clientes._expira_em({"token_expires_at": "1900000000"}) is not None
    assert clientes._expira_em({"expires_at": "2026-10-01T00:00:00Z"}) == datetime(
        2026, 10, 1, tzinfo=UTC
    )
    assert clientes._expira_em({"expires_at": "lixo"}) is None
    assert clientes._expira_em({}) is None


# ─────────────── cron de token do worker: a MESMA trava (SEG-6) ───────────────


class _ClienteDoCron:
    """Cliente falso do cron: anota com qual refresh token renovou."""

    usados: list[str] = []

    def __init__(self, creds, on_token_refresh=None):
        self.creds = dict(creds)
        self._on_refresh = on_token_refresh

    async def refresh(self) -> None:
        _ClienteDoCron.usados.append(self.creds.get("refresh_token"))
        self.creds.update(
            {"access_token": "do-cron", "refresh_token": "rt-do-cron", "expires_at": 2_000_000_000}
        )
        if self._on_refresh:
            await self._on_refresh(self.creds)


@pytest.fixture
def cron_de_token(monkeypatch):
    import app.services.token_refresh_lock as trava_mod
    from app import worker

    _ClienteDoCron.usados = []
    monkeypatch.setattr(worker, "ShopeeClient", _ClienteDoCron)
    estado: dict = {"pegou": True, "antes": None, "chamadas": []}

    @asynccontextmanager
    async def _lock(integration_id):
        estado["chamadas"].append(str(integration_id))
        if estado["antes"] is not None:
            await estado["antes"](integration_id)
        if isinstance(estado["pegou"], Exception):
            raise estado["pegou"]
        yield estado["pegou"]

    monkeypatch.setattr(trava_mod, "token_refresh_lock", _lock)
    return worker, estado


async def _integ_vencendo(db: AsyncSession, user: User) -> Integration:
    integ = await _integ(db, user)
    integ.token_expires_at = datetime.now(UTC) + timedelta(minutes=10)
    await db.commit()
    return integ


async def test_cron_de_token_rele_o_banco_dentro_da_trava(
    db: AsyncSession, make_user, cron_de_token
):
    """O cron lia os refresh tokens de todas as lojas no começo e renovava sem
    trava: se o atendimento renovasse a loja no meio, o cron gastava o refresh
    token de uso único que já tinha morrido. Agora ele passa pela trava do
    atendimento e relê as credenciais do banco lá dentro."""
    worker, estado = cron_de_token
    user = await make_user()
    integ = await _integ_vencendo(db, user)

    async def _atendimento_renovou_antes(integration_id):
        await clientes.gravar_credenciais(
            integration_id,
            {"access_token": "do-atend", "refresh_token": "rt-do-atend", "expires_at": 1},
        )

    estado["antes"] = _atendimento_renovou_antes
    await worker._refresh_tokens_for(IntegrationPlatform.SHOPEE, expiring_within_s=6 * 3600)

    assert estado["chamadas"] == [str(integ.id)]
    assert _ClienteDoCron.usados == ["rt-do-atend"]  # nunca o "rt-velho" já morto
    creds, _ = await _creds_no_banco(integ.id)
    assert creds["refresh_token"] == "rt-do-cron"


async def test_cron_de_token_pula_a_loja_com_a_trava_ocupada(
    db: AsyncSession, make_user, cron_de_token
):
    worker, estado = cron_de_token
    user = await make_user()
    integ = await _integ_vencendo(db, user)
    estado["pegou"] = False

    await worker._refresh_tokens_for(IntegrationPlatform.SHOPEE, expiring_within_s=6 * 3600)

    assert estado["chamadas"] == [str(integ.id)]
    assert _ClienteDoCron.usados == []  # o atendimento está renovando: não gasta o token


async def test_cron_de_token_sem_redis_renova_como_antes(
    db: AsyncSession, make_user, cron_de_token
):
    """Redis fora do ar não pode deixar as lojas sem renovação."""
    worker, estado = cron_de_token
    user = await make_user()
    await _integ_vencendo(db, user)
    estado["pegou"] = ConnectionError("redis fora")

    await worker._refresh_tokens_for(IntegrationPlatform.SHOPEE, expiring_within_s=6 * 3600)

    assert _ClienteDoCron.usados == ["rt-velho"]


# ─────────────── revisão de 25/09: NUL, retrato velho, sugestão velha ───────────────


async def test_id_maior_que_a_coluna_nao_derruba_a_gravacao(db: AsyncSession, make_user):
    """Outro jeito de "mensagem envenenada": um id da plataforma maior que a
    coluna (Message-ID de e-mail gigante, pedido com lixo) dava erro de banco
    em toda rodada. A porta única corta no tamanho da coluna, sempre igual —
    a rodada seguinte continua idempotente."""
    conversa = await _conversa(db, make_user)
    grande = "x" * 400
    c2, _ = await gravar.upsert_conversa(
        db,
        canal=None,
        integration=None,
        plataforma="amazon",
        canal_nome="email",
        externo_id=grande,
        comprador_id=grande,
        pedido_marketplace=grande,
        anuncio_id=grande,
    )
    m, criada = await gravar.gravar_mensagem(
        db, conversa, externo_id=grande, autor="cliente", texto="oi", enviada_em=T0
    )
    await db.commit()
    assert criada
    await db.refresh(c2)
    assert (len(c2.externo_id), len(c2.comprador_id)) == (191, 128)
    assert (len(c2.pedido_marketplace), len(c2.anuncio_id)) == (64, 64)
    _, criada = await gravar.gravar_mensagem(
        db, conversa, externo_id=grande, autor="cliente", texto="oi", enviada_em=T0
    )
    assert not criada
    c3, criada_c = await gravar.upsert_conversa(
        db, canal=None, integration=None, plataforma="amazon", canal_nome="email",
        externo_id=grande,
    )
    assert (c3.id, criada_c) == (c2.id, False)


async def test_nul_no_texto_e_no_payload_nao_derruba_a_gravacao(db: AsyncSession, make_user):
    """O Postgres recusa NUL em TEXT e em JSONB. Uma mensagem com \\x00 vinda da
    plataforma derrubava a rodada inteira — e voltava primeiro em toda rodada:
    a loja parava de entrar para sempre. A porta única tira o NUL."""
    conversa = await _conversa(db, make_user)
    c2, _ = await gravar.upsert_conversa(
        db,
        canal=None,
        integration=None,
        plataforma="amazon",
        canal_nome="email",
        externo_id="thread\x00-1",
        comprador_nome="Ana\x00 Paula",
        dados={"assunto": "pedido\x00", "lista": ["a\x00b"]},
    )
    m, criada = await gravar.gravar_mensagem(
        db,
        conversa,
        externo_id="m\x00-1",
        autor="cliente",
        texto="foto\x00quebrada",
        enviada_em=T0,
        anexos=[{"nome": "x\x00.jpg"}],
        payload={"texto": "oi\x00", "partes": [{"k\x00": "v\x00"}]},
    )
    await db.commit()
    assert criada
    await db.refresh(m)
    assert m.texto == "fotoquebrada"
    assert m.externo_id == "m-1"
    assert m.payload == {"texto": "oi", "partes": [{"k": "v"}]}
    assert m.anexos == [{"nome": "x.jpg"}]
    assert conversa.ultima_mensagem_resumo == "fotoquebrada"
    await db.refresh(c2)
    assert (c2.externo_id, c2.comprador_nome) == ("thread-1", "Ana Paula")
    assert c2.dados == {"assunto": "pedido", "lista": ["ab"]}
    # Idempotente pelo id JÁ limpo: a rodada seguinte não duplica.
    _, criada = await gravar.gravar_mensagem(
        db, conversa, externo_id="m\x00-1", autor="cliente", texto="foto\x00quebrada",
        enviada_em=T0,
    )
    assert criada is False


async def test_fechar_no_meio_da_rodada_nao_some_com_a_mensagem_nova(
    db: AsyncSession, make_user
):
    """O sync carregou a conversa (aberta, esperando); a pessoa FECHOU enquanto
    ele falava com a API; aí ele grava a mensagem nova do cliente. Com o
    retrato velho, a conversa ficava fechada e fora da fila com a mensagem
    nova dentro. Agora o gravar trava e relê a conversa antes de derivar."""
    conversa = await _conversa(db, make_user)
    await gravar.gravar_mensagem(
        db, conversa, externo_id="c-1", autor="cliente", texto="Cadê?", enviada_em=T0
    )
    await db.commit()

    async with _db.SessionLocal() as sync_s:
        # 1. a rodada do sync lê a conversa…
        c_sync = await sync_s.get(AtendimentoConversa, conversa.id)
        assert (c_sync.situacao, c_sync.aguardando_resposta) == ("aberta", True)
        await sync_s.commit()  # (sem trava: nada mudou nela ainda)

        # 2. …a pessoa fecha pela tela…
        async with _db.SessionLocal() as tela:
            c_tela = await tela.get(AtendimentoConversa, conversa.id)
            c_tela.situacao = "fechada"
            await gravar.recalcular_conversa(tela, c_tela)
            await tela.commit()

        # 3. …e o sync grava a mensagem nova com o objeto que leu antes.
        await gravar.gravar_mensagem(
            sync_s, c_sync, externo_id="c-2", autor="cliente", texto="Ainda não chegou!",
            enviada_em=T0 + timedelta(hours=1),
        )
        await sync_s.commit()

    await db.refresh(conversa)
    assert conversa.situacao == "aberta"
    assert conversa.aguardando_resposta is True
    assert conversa.ultima_autor == "cliente"
    # O prazo é o da mensagem NOVA (Shopee: 12 h).
    assert conversa.prazo_resposta_em == T0 + timedelta(hours=13)


async def test_resposta_depois_da_pergunta_aposenta_sugestao_mesmo_mais_velha_que_ela(
    db: AsyncSession, make_user
):
    """A IA escreve minutos depois da pergunta (90 s de silêncio + a rodada do
    sync). O Duoke respondeu nesse meio: a resposta é MAIS VELHA que a
    sugestão, mas é posterior à pergunta — a sugestão não vale mais."""
    conversa = await _conversa(db, make_user)
    agora = datetime.now(UTC)
    pergunta, _ = await gravar.gravar_mensagem(
        db, conversa, externo_id="c-1", autor="cliente", texto="Cadê?",
        enviada_em=agora - timedelta(minutes=5),
    )
    rascunho = AtendimentoRascunho(
        conversa_id=conversa.id, mensagem_gatilho_id=pergunta.id, texto="Já saiu!"
    )
    db.add(rascunho)
    await db.commit()

    await gravar.gravar_mensagem(
        db, conversa, externo_id="l-1", autor="loja", texto="Enviado ontem.",
        enviada_em=agora - timedelta(minutes=4),
    )
    await db.commit()
    await db.refresh(rascunho)
    assert rascunho.status == "substituido"
    assert conversa.aguardando_resposta is False


async def test_resposta_antes_da_pergunta_nao_aposenta(db: AsyncSession, make_user):
    conversa = await _conversa(db, make_user)
    agora = datetime.now(UTC)
    await gravar.gravar_mensagem(
        db, conversa, externo_id="l-0", autor="loja", texto="Olá!",
        enviada_em=agora - timedelta(minutes=9),
    )
    pergunta, _ = await gravar.gravar_mensagem(
        db, conversa, externo_id="c-1", autor="cliente", texto="Cadê?",
        enviada_em=agora - timedelta(minutes=5),
    )
    rascunho = AtendimentoRascunho(
        conversa_id=conversa.id, mensagem_gatilho_id=pergunta.id, texto="Já saiu!"
    )
    db.add(rascunho)
    await db.commit()
    # A resposta velha chega atrasada pelo sync (id novo): era para a pergunta anterior.
    await gravar.gravar_mensagem(
        db, conversa, externo_id="l-velha", autor="loja", texto="Bom dia",
        enviada_em=agora - timedelta(minutes=8),
    )
    await db.commit()
    await db.refresh(rascunho)
    assert rascunho.status == "pendente"


async def test_trava_da_conversa_nao_segura_quem_so_referencia_a_conversa(
    db: AsyncSession, make_user
):
    """A rodada do sync segura a conversa enquanto grava — mas a IA gravando um
    rascunho (FK para a conversa) não pode esperar a rodada inteira acabar."""
    import asyncio

    conversa = await _conversa(db, make_user)
    async with _db.SessionLocal() as sync_s:
        c = await sync_s.get(AtendimentoConversa, conversa.id)
        assert await gravar.travar_linha(sync_s, c)
        async with _db.SessionLocal() as ia_s:
            ia_s.add(AtendimentoRascunho(conversa_id=conversa.id, texto="sugestão"))
            await asyncio.wait_for(ia_s.commit(), 3)
        # Mas quem MUDA a conversa espera (e, com `espera`, desiste).
        async with _db.SessionLocal() as tela:
            outra = await tela.get(AtendimentoConversa, conversa.id)
            assert await gravar.travar_linha(tela, outra, espera="200ms") is False
            await tela.rollback()
        await sync_s.rollback()
