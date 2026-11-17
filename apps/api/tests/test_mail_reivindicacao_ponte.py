# ruff: noqa: F401, F811 — fixtures da ponte compartilhadas
"""Reivindicação A a Z / estorno da Amazon com banco (16/11/2026, pedido 306097).

  C) `reivindicacao.gravar`: cria a linha em `atendimento_reclamacoes`, o
     mesmo e-mail duas vezes não duplica, a decisão posterior atualiza o status
     (e encerra), e o e-mail mais velho não volta o status para trás;
  B) a ponte: o e-mail da decisão vai para a FILA (a vez da loja), mesmo na
     pasta Informativa, sem criar chamado (regra do dono, 21/10: e-mail nunca
     cria chamado); o aviso comum da Amazon continua sem pendência;
  A) `consultar_pedido`: o bloco "Atendimento" (as conversas do pedido e os
     avisos da plataforma) e o caso "Na plataforma"; nada de "Chamados: nenhum".

As partes puras estão em `test_mail_reivindicacao.py`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.models import (
    AtendimentoConversa,
    AtendimentoMensagem,
    AtendimentoReclamacao,
    BlingOrder,
    Chamado,
    StoreInfo,
    UserRole,
)
from app.services import claude_tarefas
from app.services.mail_atendimento import reivindicacao
from tests.test_mail_ponte import (
    AGORA,
    _config,
    categorizar,
    cena,
    contar,
    conversa,
    entrar,
    mensagens,
    meta_de,
    pedido_existe,
    rodar,
)
from tests.test_mail_reivindicacao import (
    ATOZ,
    DECISAO_ASSUNTO,
    DECISAO_TEXTO,
    PEDIDO,
)

ABERTA_ASSUNTO = f"Reivindicação da Garantia de A a Z referente ao pedido {PEDIDO}"
ABERTA_TEXTO = "Um cliente registrou uma reivindicação. Responda em até 3 dias."


async def _linhas(db) -> list[AtendimentoReclamacao]:
    return list(
        (
            await db.execute(
                select(AtendimentoReclamacao)
                .where(AtendimentoReclamacao.plataforma == "amazon")
                .execution_options(populate_existing=True)
            )
        ).scalars()
    )


# ─────────────── C: o espelho ───────────────


async def test_gravar_cria_atualiza_e_nao_duplica(db):
    t0 = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)
    t1 = datetime(2026, 11, 16, 16, 9, tzinfo=UTC)
    aberta = reivindicacao.ler(de=ATOZ, plataforma=None, assunto=ABERTA_ASSUNTO, texto=ABERTA_TEXTO)
    concedida = reivindicacao.ler(
        de=ATOZ, plataforma=None, assunto=DECISAO_ASSUNTO, texto=DECISAO_TEXTO
    )
    assert aberta is not None and concedida is not None

    linha = await reivindicacao.gravar(db, aberta, quando=t0, assunto=ABERTA_ASSUNTO)
    await db.commit()
    [linha] = await _linhas(db)
    assert (linha.externo_id, linha.tipo, linha.status) == (
        f"atoz:{PEDIDO}",
        "reclamacao",
        "aberta",
    )
    assert linha.encerrada_em is None and linha.aberta_em == t0
    assert linha.prazo_em == t0 + timedelta(days=3)
    assert linha.dados["acao_texto"].startswith("responder à reivindicação")

    # O mesmo e-mail de novo: a mesma linha.
    await reivindicacao.gravar(db, aberta, quando=t0, assunto=ABERTA_ASSUNTO)
    await db.commit()
    assert len(await _linhas(db)) == 1

    # A decisão (depois): concedida, encerrada, com o valor e o recurso.
    await reivindicacao.gravar(db, concedida, quando=t1, assunto=DECISAO_ASSUNTO)
    await db.commit()
    [linha] = await _linhas(db)
    assert linha.status == "concedida" and linha.encerrada_em == t1 and linha.prazo_em is None
    assert linha.aberta_em == t0
    assert linha.dados["valor_reembolso"] == 865.59 and linha.dados["moeda"] == "BRL"
    assert linha.dados["recurso_ate"] == (t1 + timedelta(days=30)).isoformat()
    assert linha.motivo.startswith("Problema com a entrega")

    # O e-mail mais velho relido fora de ordem não reabre o caso.
    await reivindicacao.gravar(db, aberta, quando=t0, assunto=ABERTA_ASSUNTO)
    await db.commit()
    [linha] = await _linhas(db)
    assert linha.status == "concedida" and linha.encerrada_em == t1


async def test_gravar_so_com_o_remetente_do_modelo(db):
    copia = reivindicacao.ler(
        de="seller-support@amazon.com.br",
        plataforma="amazon",
        assunto=f"Retirada do ODR - reivindicação A a Z do pedido {PEDIDO}",
        texto="---Original Message---",
    )
    assert copia is not None and not copia.oficial
    assert await reivindicacao.gravar(db, copia, quando=AGORA) is None
    assert await _linhas(db) == []


# ─────────────── B: a ponte ───────────────


async def test_decisao_da_reivindicacao_vai_para_a_fila_sem_criar_chamado(db, cena):
    await pedido_existe(db, cena.amazon, PEDIDO, plataforma="amazon")
    primeiro = await entrar(
        db,
        cena.geral,
        pasta="INBOX",
        para=["mike14@tuta.com"],
        de=ATOZ,
        de_nome="Amazon",
        assunto=ABERTA_ASSUNTO,
        corpo=ABERTA_TEXTO,
        recebido_em=AGORA - timedelta(hours=2),
    )
    await rodar(db)
    m = await meta_de(db, primeiro)
    assert (m.estado, m.vinculado_por, m.pedido_marketplace) == ("gravado", "pedido", PEDIDO)
    c = await conversa(db, m.conversa_id)
    assert c.pedido_marketplace == PEDIDO
    assert c.aguardando_resposta is True and c.sem_resposta_necessaria is False
    msg = (await mensagens(db, c.id))[-1]
    assert msg.autor == "sistema" and msg.payload["mail"]["tipo_email"] == "chamado"
    [linha] = await _linhas(db)
    assert (linha.status, linha.conversa_id, linha.integration_id) == (
        "aberta",
        c.id,
        cena.amazon.id,
    )
    assert linha.encerrada_em is None

    decisao = await entrar(
        db,
        cena.geral,
        pasta="INBOX",
        para=["mike14@tuta.com"],
        de=ATOZ,
        de_nome="Amazon",
        assunto=DECISAO_ASSUNTO,
        corpo=DECISAO_TEXTO,
    )
    await rodar(db)
    m2 = await meta_de(db, decisao)
    assert m2.estado == "gravado" and m2.conversa_id == c.id
    [linha] = await _linhas(db)
    assert linha.status == "concedida" and linha.encerrada_em is not None
    assert linha.dados["valor_reembolso"] == 865.59
    # Regra do dono (21/10): e-mail nunca cria chamado.
    assert await contar(db, Chamado) == 0
    assert m2.chamado_id is None


async def test_reivindicacao_na_pasta_informativa_liga_como_a_entrada(db, cena):
    """A pasta "problemas" é Informativa (27/10): a decisão de 16/11 entrou lá."""
    await categorizar(db, cena.geral, "problemas", "informativo")
    await pedido_existe(db, cena.amazon, PEDIDO, plataforma="amazon")
    mid = await entrar(
        db,
        cena.geral,
        pasta="problemas",
        para=["mike14@tuta.com"],
        de=ATOZ,
        de_nome="Amazon",
        assunto=DECISAO_ASSUNTO,
        corpo=DECISAO_TEXTO,
    )
    await rodar(db)
    m = await meta_de(db, mid)
    assert m.estado == "gravado" and m.conversa_id is not None
    c = await conversa(db, m.conversa_id)
    assert c.aguardando_resposta is True
    assert [linha.status for linha in await _linhas(db)] == ["concedida"]


async def test_aviso_comum_da_amazon_continua_sem_pendencia(db, cena):
    await pedido_existe(db, cena.amazon, PEDIDO, plataforma="amazon")
    mid = await entrar(
        db,
        cena.geral,
        pasta="INBOX",
        para=["mike14@tuta.com"],
        de="donotreply@amazon.com.br",
        de_nome="Amazon",
        assunto=f"Reembolso de 865.59 BRL iniciado para o pedido {PEDIDO}",
        corpo=f"Reembolso de 865.59 BRL iniciado para o pedido {PEDIDO}",
    )
    await rodar(db)
    m = await meta_de(db, mid)
    c = await conversa(db, m.conversa_id)
    assert c.aguardando_resposta is False and c.sem_resposta_necessaria is True
    msg = (await mensagens(db, c.id))[-1]
    assert msg.payload["mail"]["tipo_email"] == "informativo"
    assert await _linhas(db) == []


# ─────────────── A: o resumo do pedido ───────────────


async def test_consultar_pedido_mostra_o_atendimento_e_a_reivindicacao(db, make_user):
    admin = await make_user(role=UserRole.ADMIN)
    db.add(
        StoreInfo(user_id=admin.id, platform="amazon", account_name="Kia", bling_store_id="5009")
    )
    db.add(
        BlingOrder(
            bling_id=306097,
            numero="306097",
            numeroloja=PEDIDO,
            situacao="15",
            loja="5009",
            data=datetime(2026, 10, 26, 12, 0, tzinfo=UTC),
            item_index=0,
            item_codigo="dg053.ci",
            item_descricao="Celular",
        )
    )
    cliente = AtendimentoConversa(
        plataforma="amazon",
        canal="email",
        conta="Kia",
        externo_id="<thread-306097@marketplace.amazon.com.br>",
        pedido_marketplace=PEDIDO,
        situacao="respondida",
        ultima_mensagem_em=datetime(2026, 11, 14, 10, 22, tzinfo=UTC),
        ultima_do_cliente_em=datetime(2026, 11, 13, 21, 34, tzinfo=UTC),
        ultima_da_loja_em=datetime(2026, 11, 14, 10, 22, tzinfo=UTC),
        dados={"assunto": "Pedido não recebido"},
    )
    avisos = AtendimentoConversa(
        plataforma="amazon",
        canal="email",
        conta="Kia",
        externo_id="tuta:kia:306097",
        pedido_marketplace=PEDIDO,
        situacao="respondida",
        sem_resposta_necessaria=True,
        ultima_mensagem_em=datetime(2026, 11, 16, 19, 17, tzinfo=UTC),
        dados={"fonte": "tuta", "mail": {"tipo_email": "venda"}},
    )
    db.add_all([cliente, avisos])
    await db.flush()
    db.add_all(
        [
            AtendimentoMensagem(
                conversa_id=avisos.id,
                externo_id="mail:1",
                autor="sistema",
                origem="sistema",
                texto="Nós concedemos uma reivindicação…",
                enviada_em=datetime(2026, 11, 16, 16, 9, tzinfo=UTC),
                payload={"mail": {"assunto": DECISAO_ASSUNTO}},
            ),
            AtendimentoMensagem(
                conversa_id=avisos.id,
                externo_id="mail:2",
                autor="sistema",
                origem="sistema",
                texto="Reembolso iniciado",
                enviada_em=datetime(2026, 11, 16, 19, 17, tzinfo=UTC),
                payload={"mail": {"assunto": f"Reembolso de 865.59 BRL iniciado — {PEDIDO}"}},
            ),
            # A fala do cliente não vira "aviso" (nem o texto dela sai no resumo).
            AtendimentoMensagem(
                conversa_id=cliente.id,
                externo_id="<m1@marketplace.amazon.com.br>",
                autor="cliente",
                origem="cliente",
                texto="Pedido não recebido. Quero meu dinheiro.",
                enviada_em=datetime(2026, 11, 13, 21, 34, tzinfo=UTC),
                payload={"mail": {"assunto": "Pedido não recebido"}},
            ),
        ]
    )
    concedida = reivindicacao.ler(
        de=ATOZ, plataforma=None, assunto=DECISAO_ASSUNTO, texto=DECISAO_TEXTO
    )
    await reivindicacao.gravar(
        db,
        concedida,
        quando=datetime(2026, 11, 16, 16, 9, tzinfo=UTC),
        conversa_id=avisos.id,
        assunto=DECISAO_ASSUNTO,
    )
    await db.commit()

    texto = await claude_tarefas.consultar_pedido(db, dono=admin, args={"pedido": "306097"})
    assert (
        f"Na plataforma: Reivindicação A a Z na Amazon (pedido {PEDIDO}) · ENCERRADA em 16/11"
    ) in texto
    assert "Concedida ao comprador — R$ 865,59 debitado da conta" in texto
    assert (
        "Atendimento: e-mail Amazon (Kia) [Caixa] · respondida · última do cliente 13/11 18:34"
        " · última da loja 14/11 07:22 · assunto: Pedido não recebido"
    ) in texto
    assert 'Atendimento: e-mail Amazon (Kia) [aba E-mail] · marcada "não precisa de resposta"' in (
        texto
    )
    assert f"aviso da plataforma 16/11 13:09: {DECISAO_ASSUNTO}" in texto
    assert "aviso da plataforma 16/11 16:17: Reembolso de 865.59 BRL" in texto
    # A conversa com o cliente vem antes da de e-mail (aba E-mail).
    assert texto.index("[Caixa]") < texto.index("[aba E-mail]")
    assert "Quero meu dinheiro" not in texto
    assert "Chamados abertos pela equipe (aba Chamados): nenhum." in texto
    assert "Chamados: nenhum" not in texto

    # Pelo nº na Amazon, o mesmo resumo.
    texto = await claude_tarefas.consultar_pedido(db, dono=admin, args={"pedido": PEDIDO})
    assert "Atendimento: e-mail Amazon (Kia) [Caixa]" in texto


# ─────────────── 16/11 (Eduardo, revisão) ───────────────


async def _linha(db, externo_id: str) -> AtendimentoReclamacao:
    return (
        await db.execute(
            select(AtendimentoReclamacao)
            .where(AtendimentoReclamacao.externo_id == externo_id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()


async def test_texto_sem_situacao_nao_reabre_nem_muda_a_linha(db):
    """O e-mail do modelo cujo texto não diz a situação (`reconhecido=False`):
    não reabre a decisão, não troca o status da aberta — só conta como notícia."""
    t0 = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)
    t1 = datetime(2026, 11, 16, 16, 9, tzinfo=UTC)
    t2 = datetime(2026, 11, 17, 9, 0, tzinfo=UTC)
    concedida = reivindicacao.ler(
        de=ATOZ, plataforma=None, assunto=DECISAO_ASSUNTO, texto=DECISAO_TEXTO
    )
    palpite = reivindicacao.ler(
        de=ATOZ,
        plataforma=None,
        assunto=f"Atualização da reivindicação do pedido {PEDIDO}",
        texto="Veja suas reivindicações abertas no Seller Central.",
    )
    assert concedida is not None and palpite is not None and not palpite.reconhecido

    await reivindicacao.gravar(db, concedida, quando=t1, assunto=DECISAO_ASSUNTO)
    await reivindicacao.gravar(db, palpite, quando=t2)
    await db.commit()
    linha = await _linha(db, f"atoz:{PEDIDO}")
    assert linha.status == "concedida" and linha.encerrada_em == t1
    assert linha.dados["status_texto"].startswith("Concedida ao comprador")
    assert linha.dados["email_em"] == t1.isoformat()
    assert linha.dados["visto_em"] == t2.isoformat()

    # A aberta com prazo: o palpite não troca o status nem o prazo.
    outro = "702-1111111-2222222"
    aberta = reivindicacao.ler(
        de=ATOZ,
        plataforma=None,
        assunto=f"Reivindicação da Garantia de A a Z referente ao pedido {outro}",
        texto=ABERTA_TEXTO,
    )
    sem_situacao = reivindicacao.ler(
        de=ATOZ,
        plataforma=None,
        assunto=f"Atualização da reivindicação do pedido {outro}",
        texto="Veja suas reivindicações abertas no Seller Central.",
    )
    await reivindicacao.gravar(db, aberta, quando=t0)
    await reivindicacao.gravar(db, sem_situacao, quando=t1)
    await db.commit()
    linha = await _linha(db, f"atoz:{outro}")
    assert linha.status == "aberta" and linha.encerrada_em is None
    assert linha.prazo_em == t0 + timedelta(days=3)
    assert linha.dados["status_texto"] == "Reivindicação aberta na Amazon — a Amazon espera a loja"
    assert linha.dados["visto_em"] == t1.isoformat()

    # O primeiro e-mail do caso sem situação cria a linha (aberta, avisando).
    terceiro = "702-3333333-4444444"
    so_palpite = reivindicacao.ler(
        de=ATOZ,
        plataforma=None,
        assunto=f"Atualização da reivindicação do pedido {terceiro}",
        texto="Veja suas reivindicações abertas no Seller Central.",
    )
    await reivindicacao.gravar(db, so_palpite, quando=t1)
    await db.commit()
    linha = await _linha(db, f"atoz:{terceiro}")
    assert linha.status == "aberta" and linha.encerrada_em is None
    assert linha.dados["status_reconhecido"] is False
    assert "não diz a situação" in linha.dados["status_texto"]


async def test_aberta_sem_noticia_fecha_depois_de_30_dias(db):
    t0 = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)
    aberta = reivindicacao.ler(de=ATOZ, plataforma=None, assunto=ABERTA_ASSUNTO, texto=ABERTA_TEXTO)
    concedida = reivindicacao.ler(
        de=ATOZ,
        plataforma=None,
        assunto=DECISAO_ASSUNTO.replace(PEDIDO, "702-5555555-6666666"),
        texto=DECISAO_TEXTO.replace(PEDIDO, "702-5555555-6666666"),
    )
    await reivindicacao.gravar(db, aberta, quando=t0, assunto=ABERTA_ASSUNTO)
    await reivindicacao.gravar(db, concedida, quando=t0)
    await db.commit()
    prazo = t0 + timedelta(days=3)

    # Antes de 30 dias do prazo: nada.
    agora = prazo + reivindicacao.SEM_NOTICIA - timedelta(minutes=1)
    assert await reivindicacao.encerrar_sem_noticia(db, agora=agora) == 0
    await db.commit()
    assert (await _linha(db, f"atoz:{PEDIDO}")).encerrada_em is None

    # Passou: fecha com `sem_noticia`; a decidida fica como estava.
    agora = prazo + reivindicacao.SEM_NOTICIA + timedelta(minutes=1)
    assert await reivindicacao.encerrar_sem_noticia(db, agora=agora) == 1
    await db.commit()
    linha = await _linha(db, f"atoz:{PEDIDO}")
    assert linha.status == "sem_noticia" and linha.encerrada_em == agora
    assert linha.prazo_em is None and linha.dados["acao_texto"] is None
    assert linha.dados["encerrada_por"] == "sem_noticia"
    assert linha.dados["status_texto"].startswith(
        "Reivindicação sem notícia da Amazon desde 16/11/2026"
    )
    decidida = await _linha(db, "atoz:702-5555555-6666666")
    assert decidida.status == "concedida" and decidida.encerrada_em == t0
    # De novo: nada (idempotente).
    assert await reivindicacao.encerrar_sem_noticia(db, agora=agora) == 0

    # A decisão que chega depois ainda atualiza a linha.
    depois = agora + timedelta(days=1)
    concedida_306097 = reivindicacao.ler(
        de=ATOZ, plataforma=None, assunto=DECISAO_ASSUNTO, texto=DECISAO_TEXTO
    )
    await reivindicacao.gravar(db, concedida_306097, quando=depois, assunto=DECISAO_ASSUNTO)
    await db.commit()
    linha = await _linha(db, f"atoz:{PEDIDO}")
    assert linha.status == "concedida" and linha.encerrada_em == depois
    assert linha.dados["encerrada_por"] is None


async def test_volta_da_ponte_fecha_a_aberta_sem_noticia(db):
    agora = datetime.now(UTC)
    velha = agora - timedelta(days=40)
    db.add(
        AtendimentoReclamacao(
            plataforma="amazon",
            externo_id=f"cb:{PEDIDO}",
            tipo="reclamacao",
            status="informacao_necessaria",
            pedido_marketplace=PEDIDO,
            aberta_em=velha,
            prazo_em=velha + timedelta(days=7),
            dados={"fonte": "email", "tipo_caso": "chargeback", "email_em": velha.isoformat()},
        )
    )
    await db.commit()
    await rodar(db)
    linha = await _linha(db, f"cb:{PEDIDO}")
    assert linha.status == "sem_noticia" and linha.encerrada_em is not None
    assert linha.dados["status_texto"].startswith("Estorno sem notícia da Amazon")


async def test_decisao_na_pasta_chamado_atualiza_o_espelho(db, cena, monkeypatch):
    """A pasta de Chamado vai para a Pós-venda (sem conversa), mas a decisão
    oficial também encerra a linha — antes saía sem espelhar."""
    from app.services.mail_atendimento import posvenda

    async def _sem_chamado(session, email, pasta, **kw):
        return posvenda.LigacaoChamado(chamado_id=None, motivo="chamado_sem_chamado")

    monkeypatch.setattr(posvenda, "ligar_ao_chamado", _sem_chamado)
    await categorizar(db, cena.geral, "problemas", "chamado")
    await pedido_existe(db, cena.amazon, PEDIDO, plataforma="amazon")
    mid = await entrar(
        db,
        cena.geral,
        pasta="problemas",
        para=["mike14@tuta.com"],
        de=ATOZ,
        de_nome="Amazon",
        assunto=DECISAO_ASSUNTO,
        corpo=DECISAO_TEXTO,
    )
    await rodar(db)
    m = await meta_de(db, mid)
    assert m.conversa_id is None
    [linha] = await _linhas(db)
    assert linha.status == "concedida" and linha.encerrada_em is not None
    assert linha.integration_id == cena.amazon.id and linha.conversa_id is None
    # Regra do dono (21/10): e-mail nunca cria chamado.
    assert await contar(db, Chamado) == 0
