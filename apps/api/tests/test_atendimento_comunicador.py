"""A integração do Comunicador (itens 1, 2 e 3; 01/10/2026).

As três frentes (reclamações, etiqueta, painel) foram feitas em separado;
aqui se confere o que só existe depois de juntar:

- os routers novos (`atendimento_painel`, `atendimento_reclamacoes`) estão
  no `app.main`, uma vez cada, com a mesma trava da caixa (`_so_admin`);
- os dois crons novos (`atendimento_reclamacoes`, `atendimento_etiquetas`)
  estão no worker, a cada 10 min, em minutos que não batem com a leitura das
  caixas (ímpares), com os crons de token (:00/:30) nem com o preço da
  Amazon/espelho de NF-e ({4,14,…}); e saem na hora com a leitura desligada
  OU com o interruptor próprio desligado (o padrão: o deploy não liga nada);
- a etiqueta não chama de Reclamação o `claim_ids` do pack que a busca de
  reclamações já classificou (a devolução do ML vem em `claim_ids` também),
  nem o `claim_ids` velho de reclamação encerrada ou de cancelamento (o ML
  não esvazia a lista: só o chat bloqueado pela reclamação liga a reserva);
- a reclamação do ML (gravada pelo ORDER) chega à conversa do pack de
  carrinho no formato de PRODUÇÃO (`pedido_marketplace` = pack, o order só
  no retrato `pedido_mkt`) — etiqueta, cartão, cron e Bling;
- o contexto da IA e o `consultar_pedido` mostram a reclamação da plataforma;
- as devoluções do cron commitam em lotes, e o recálculo da reclamação não
  passa por cima de uma conversa travada por outro processo.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

from fastapi.routing import APIRoute
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import app.db as _db
from app import worker
from app.config import Settings
from app.main import app
from app.models import AtendimentoConversa, AtendimentoReclamacao, BlingOrder, UserRole
from app.routers import atendimento as rota
from app.routers import atendimento_painel, atendimento_reclamacoes
from app.services import claude_tarefas
from app.services.atendimento import (
    contexto,
    etiqueta_cron,
    etiqueta_fatos,
    gravar,
    ia,
    reclamacoes,
)
from app.services.atendimento.etiqueta import calcular

URL = "/api/atendimento"
T0 = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


# ─────────────── main.py ───────────────


def test_routers_do_comunicador_estao_no_main_uma_vez_com_a_trava():
    esperadas = {
        ("GET", f"{URL}/conversas/{{conversa_id}}/painel"): atendimento_painel,
        ("POST", f"{URL}/conversas/{{conversa_id}}/notas"): atendimento_painel,
        ("POST", f"{URL}/conversas/{{conversa_id}}/foto"): atendimento_painel,
        ("POST", f"{URL}/adspower/aberto"): atendimento_painel,
        ("GET", f"{URL}/conversas/{{conversa_id}}/reclamacoes"): atendimento_reclamacoes,
        ("POST", f"{URL}/conversas/{{conversa_id}}/etiqueta"): rota,
    }
    for (metodo, caminho), modulo in esperadas.items():
        rotas = [
            r
            for r in app.routes
            if isinstance(r, APIRoute) and r.path == caminho and metodo in r.methods
        ]
        assert len(rotas) == 1, (metodo, caminho, len(rotas))
        assert rotas[0].endpoint.__module__ == modulo.__name__, caminho
        assert rota._so_admin in [d.call for d in rotas[0].dependant.dependencies], caminho


# ─────────────── worker.py ───────────────


def test_crons_do_comunicador_no_worker():
    crons = {c.name: c for c in worker.WorkerSettings.cron_jobs}
    rec = crons["cron:atendimento_reclamacoes"]
    eti = crons["cron:atendimento_etiquetas"]
    assert rec.minute == {6, 16, 26, 36, 46, 56}
    assert eti.minute == {8, 18, 28, 38, 48, 58}
    leitura = crons["cron:atendimento_sincronizar"].minute
    amazon_nfe = crons["cron:pos_vendas_notas_sync"].minute
    for c in (rec, eti):
        assert not c.run_at_startup
        assert not {0, 30} & c.minute, c.name  # crons de token (refresh de uso único)
        assert not leitura & c.minute, c.name  # leitura das caixas (ímpares)
        assert not amazon_nfe & c.minute, c.name  # preço da Amazon + espelho de NF-e
    # A trava da rodada das reclamações dura 9 min: o arq mata o job antes.
    assert rec.timeout_s == reclamacoes.TRAVA_TTL_S == 540
    assert eti.timeout_s == 600 and eti.timeout_s < etiqueta_cron.RODADA_TTL_S
    funcoes = {
        getattr(f, "name", getattr(f, "__name__", None)) for f in worker.WorkerSettings.functions
    }
    assert {"atendimento_reclamacoes", "atendimento_etiquetas"} <= funcoes


async def test_crons_do_comunicador_saem_com_a_leitura_desligada(monkeypatch):
    # O worker lê o `_settings` do import (ver test_atendimento_sync).
    s = worker._settings
    rodada_rec = AsyncMock(return_value={"ml": {}})
    rodada_eti = AsyncMock(return_value={"conversas": 0})
    monkeypatch.setattr(reclamacoes, "atendimento_reclamacoes", rodada_rec)
    monkeypatch.setattr(etiqueta_cron, "atendimento_etiquetas", rodada_eti)

    # O interruptor próprio nasce DESLIGADO: com a leitura já ligada em
    # produção, o deploy sozinho não liga nenhum dos dois crons.
    assert Settings.model_fields["atendimento_reclamacoes_ativa"].default is False
    assert Settings.model_fields["atendimento_etiquetas_ativa"].default is False

    monkeypatch.setattr(s, "atendimento_reclamacoes_ativa", True)
    monkeypatch.setattr(s, "atendimento_etiquetas_ativa", True)
    monkeypatch.setattr(s, "atendimento_leitura_ativa", False)
    assert await worker.atendimento_reclamacoes({}) is None
    assert await worker.atendimento_etiquetas({}) is None
    monkeypatch.setattr(s, "atendimento_leitura_ativa", True)
    monkeypatch.setattr(s, "atendimento_reclamacoes_ativa", False)
    monkeypatch.setattr(s, "atendimento_etiquetas_ativa", False)
    assert await worker.atendimento_reclamacoes({}) is None
    assert await worker.atendimento_etiquetas({}) is None
    rodada_rec.assert_not_awaited()
    rodada_eti.assert_not_awaited()

    monkeypatch.setattr(s, "atendimento_reclamacoes_ativa", True)
    monkeypatch.setattr(s, "atendimento_etiquetas_ativa", True)
    assert await worker.atendimento_reclamacoes({}) == {"ml": {}}
    assert await worker.atendimento_etiquetas({}) == {"conversas": 0}
    rodada_rec.assert_awaited_once()
    rodada_eti.assert_awaited_once()

    # Erro inesperado no serviço vira log, nunca derruba o worker.
    rodada_rec.side_effect = RuntimeError("quebrou")
    rodada_eti.side_effect = RuntimeError("quebrou")
    assert await worker.atendimento_reclamacoes({}) is None
    assert await worker.atendimento_etiquetas({}) is None


# ─────────────── claim_ids do pack × tabela de reclamações ───────────────


async def _pack(
    db: AsyncSession, externo: str, claims: list, substatus: str | None = "blocked_by_mediation"
) -> AtendimentoConversa:
    c = AtendimentoConversa(
        plataforma="ml",
        canal="pos_venda",
        externo_id=externo,
        pedido_marketplace=f"20000{externo[-4:]}",
        dados={
            "pack_id": f"2000{externo[-4:]}",
            "claim_ids": claims,
            "status_ml": "blocked" if substatus else "active",
            "substatus_ml": substatus,
        },
    )
    db.add(c)
    await db.commit()
    return c


async def _linha(
    db: AsyncSession, externo_id: str, tipo: str, *, encerrada: bool = False
) -> AtendimentoReclamacao:
    r = AtendimentoReclamacao(
        plataforma="ml",
        externo_id=externo_id,
        tipo=tipo,
        status="closed" if encerrada else "opened",
        pedido_marketplace=None,
        aberta_em=T0 - timedelta(days=2),
        encerrada_em=T0 - timedelta(hours=1) if encerrada else None,
    )
    db.add(r)
    await db.commit()
    return r


async def test_claim_do_pack_ja_lido_vale_pela_tabela(db):
    # Devolução do ML (type returns): o pack traz o id em `claim_ids`, a
    # busca de reclamações já gravou como DEVOLUÇÃO — roxo, não vermelho.
    dev = await _pack(db, "pack-0001", ["7001"])
    r = await _linha(db, "7001", "devolucao")
    r.conversa_id = dev.id
    await db.commit()
    # Reclamação que a tabela já ENCERROU: um `claim_ids` velho do pack (o
    # pack só é relido quando chega mensagem) não reabre.
    velha = await _pack(db, "pack-0002", ["7002"])
    await _linha(db, "7002", "reclamacao", encerrada=True)
    # Id que a busca ainda não leu: o pack continua valendo (a rede antiga).
    nova = await _pack(db, "pack-0003", ["7003"])
    # Duas no pack: a conhecida (devolução) não esconde a desconhecida.
    duas = await _pack(db, "pack-0004", ["7001", "7004"])

    fatos = await etiqueta_fatos.fatos_em_lote(db, [dev, velha, nova, duas])
    assert calcular(fatos[dev.id]).etiqueta == "devolucao"
    assert not fatos[dev.id].reclamacao_aberta
    assert calcular(fatos[velha.id]).etiqueta == "pos_venda"
    assert calcular(fatos[nova.id]).etiqueta == "reclamacao"
    assert fatos[nova.id].motivo_reclamacao == "Reclamação 7003 aberta no ML"
    assert fatos[duas.id].motivo_reclamacao == "Reclamação 7004 aberta no ML"
    # A porta de uma conversa dá o mesmo que o lote.
    for c in (dev, velha, nova, duas):
        assert await etiqueta_fatos.fatos_da_conversa(db, c) == fatos[c.id]


# ─────────────── revisão: claim_ids velho não é Reclamação ───────────────


async def test_claim_ids_velho_de_reclamacao_encerrada_ou_cancelamento_nao_e_reclamacao(db):
    # Produção (01/10/2026): o ML deixa em `claim_ids` a reclamação já
    # encerrada (fora da janela de 15 dias da busca: nunca vira linha) e a de
    # cancelamento (cancel_purchase, que a busca ignora). Só o chat bloqueado
    # PELA reclamação liga a reserva do pack.
    encerrada = await _pack(db, "pack-0011", ["8001"], substatus=None)
    cancelada = await _pack(db, "pack-0012", ["8002"], substatus="blocked_by_cancelled_order")
    tempo = await _pack(db, "pack-0013", ["8003"], substatus="blocked_by_time")
    aberta = await _pack(db, "pack-0014", ["8004"], substatus="blocked_by_claim")
    fatos = await etiqueta_fatos.fatos_em_lote(db, [encerrada, cancelada, tempo, aberta])
    for c in (encerrada, cancelada, tempo):
        assert not fatos[c.id].reclamacao_aberta, c.externo_id
        assert calcular(fatos[c.id]).etiqueta == "pos_venda", c.externo_id
    assert fatos[aberta.id].motivo_reclamacao == "Reclamação 8004 aberta no ML"

    # Pela leitura do pack (o gravar): o mesmo — e o ML bloquear o chat pela
    # mediação (só o substatus muda) é acontecimento que recalcula.
    async def ler(status: str, substatus: str | None) -> AtendimentoConversa:
        c, _ = await gravar.upsert_conversa(
            db,
            canal=None,
            integration=None,
            plataforma="ml",
            canal_nome="pos_venda",
            externo_id="pack-g2",
            pedido_marketplace="2000000000000777",
            dados={
                "pack_id": "2000000000000777",
                "claim_ids": ["8005"],
                "status_ml": status,
                "substatus_ml": substatus,
            },
        )
        await db.commit()
        return c

    assert (await ler("active", None)).etiqueta == "pos_venda"
    assert (await ler("blocked", "blocked_by_mediation")).etiqueta == "reclamacao"


# ─────────────── revisão: pack de carrinho no formato de produção ───────────────

PACK_CARRINHO = "2000009300000001"
ORDER_CARRINHO = "2000018509205724"
RECLAMACAO = "5582543195"


async def _pack_producao(
    db: AsyncSession, pack: str, pedido_do_retrato: str, *, ultima: datetime | None = None
) -> AtendimentoConversa:
    """O pack do pós-venda do ML como em PRODUÇÃO: `pedido_marketplace` = pack,
    sem `order_id` à vista; o order só no retrato do pedido (`pedido_mkt`)."""
    c = AtendimentoConversa(
        plataforma="ml",
        canal="pos_venda",
        externo_id=pack,
        pedido_marketplace=pack,
        dados={
            "pack_id": pack,
            "status_ml": "active",
            "substatus_ml": None,
            "claim_ids": [],
            "pedido_mkt": {"fonte": "ml", "pedido": pedido_do_retrato, "itens": []},
        },
        ultima_mensagem_em=ultima or T0 - timedelta(days=40),
    )
    db.add(c)
    await db.commit()
    return c


async def _reclamacao_ml(
    db: AsyncSession,
    externo: str,
    pedido: str,
    *,
    pack: str | None = None,
    tipo: str = "mediacao",
    prazo: datetime | None = None,
) -> AtendimentoReclamacao:
    r = AtendimentoReclamacao(
        plataforma="ml",
        externo_id=externo,
        tipo=tipo,
        status="opened",
        pedido_marketplace=pedido,
        aberta_em=T0 - timedelta(days=3),
        prazo_em=prazo,
        dados={"fonte": "ml_claims", "etapa": "dispute", **({"pack_id": pack} if pack else {})},
    )
    db.add(r)
    await db.commit()
    return r


def _bling(numero: str, numeroloja: str, situacao: str = "15") -> BlingOrder:
    return BlingOrder(
        bling_id=int(numero),
        numero=numero,
        numeroloja=numeroloja,
        situacao=situacao,
        loja="204897261",
        data=T0 - timedelta(days=14),
        item_index=0,
        item_codigo="SKU-1",
        item_descricao="Mochila",
    )


async def test_reclamacao_do_order_chega_ao_pack_de_carrinho_formato_de_producao(db):
    # Carrinho com UM pedido: o order só aparece no retrato. O Bling só tem o
    # ORDER em `numeroloja` (14 dos 53 carrinhos em produção).
    pack = await _pack_producao(db, PACK_CARRINHO, ORDER_CARRINHO)
    db.add(_bling("297840", ORDER_CARRINHO))
    await db.commit()
    await _reclamacao_ml(db, RECLAMACAO, ORDER_CARRINHO, prazo=T0 + timedelta(days=2))

    assert etiqueta_fatos.chaves_do_pedido(pack) == [PACK_CARRINHO, ORDER_CARRINHO]
    pedido = await etiqueta_fatos.pedido_bling_da_conversa(db, pack)
    assert pedido is not None and pedido.numero == "297840"
    fatos = await etiqueta_fatos.fatos_da_conversa(db, pack)
    assert fatos.motivo_reclamacao == f"Mediação {RECLAMACAO} aberta no ML"
    assert calcular(fatos).etiqueta == "reclamacao"
    assert [r.externo_id for r in await reclamacoes.reclamacoes_da_conversa(db, pack)] == [
        RECLAMACAO
    ]
    do_order = await etiqueta_fatos.conversas_do_pedido(db, "mercadolivre", ORDER_CARRINHO)
    assert [c.id for c in do_order] == [pack.id]
    # Parada há 40 dias, sem etiqueta: o cron a acha pela reclamação aberta.
    assert pack.id in await etiqueta_cron.ids_da_rodada(db, agora=T0)

    # Carrinho com VÁRIOS pedidos: o retrato mostra o próprio pack, e a
    # reclamação (de um dos orders) casa pelo `pack_id` que o leitor gravou.
    multi = await _pack_producao(db, "2000009300000002", "2000009300000002")
    await _reclamacao_ml(
        db, "5582549999", "2000018500000001", pack="2000009300000002", tipo="devolucao"
    )
    fatos_multi = await etiqueta_fatos.fatos_da_conversa(db, multi)
    assert calcular(fatos_multi).etiqueta == "devolucao"
    assert [r.externo_id for r in await reclamacoes.reclamacoes_da_conversa(db, multi)] == [
        "5582549999"
    ]
    assert multi.id in await etiqueta_cron.ids_da_rodada(db, agora=T0)
    # O lote dá o mesmo que a porta de uma conversa.
    lote = await etiqueta_fatos.fatos_em_lote(db, [pack, multi])
    assert (lote[pack.id], lote[multi.id]) == (fatos, fatos_multi)


async def test_pack_do_pedido_pelo_retrato_e_sem_atalho_do_bling(db):
    class Cliente:
        def __init__(self, corpo: dict) -> None:
            self.corpo, self.chamadas = corpo, []

        async def pedido(self, order_id: str) -> dict:
            self.chamadas.append(order_id)
            return self.corpo

    # A conversa do pack (formato de produção) já diz o pack: nenhum GET.
    await _pack_producao(db, PACK_CARRINHO, ORDER_CARRINHO)
    cli = Cliente({})
    assert await reclamacoes._pack_do_pedido(db, cli, ORDER_CARRINHO) == PACK_CARRINHO
    assert cli.chamadas == []
    # Sem conversa, e com o pedido achado no Bling pelo ORDER: o pack é
    # buscado igual — a conversa do comprador é a do pack.
    db.add(_bling("297841", "2000018500000002"))
    await db.commit()
    cli = Cliente({"id": 2000018500000002, "pack_id": 2000009300000003})
    assert await reclamacoes._pack_do_pedido(db, cli, "2000018500000002") == "2000009300000003"
    assert cli.chamadas == ["2000018500000002"]


# ─────────────── revisão: contexto da IA e consultar_pedido ───────────────


async def test_contexto_e_consultar_pedido_mostram_a_reclamacao_da_plataforma(db, make_user):
    pack = await _pack_producao(db, PACK_CARRINHO, ORDER_CARRINHO)
    db.add(_bling("297840", ORDER_CARRINHO))
    await db.commit()
    await _reclamacao_ml(db, RECLAMACAO, ORDER_CARRINHO, prazo=T0 + timedelta(days=2))

    ctx = await contexto.contexto_da_conversa(db, pack)
    assert ctx["pedido"]["numero"] == "297840"  # o Bling achado pelo order do retrato
    [r] = ctx["reclamacoes"]
    assert (r["numero"], r["tipo"], r["aberta"]) == (RECLAMACAO, "mediacao", True)
    assert r["prazo_em"] == (T0 + timedelta(days=2)).isoformat()
    assert ia._fatos_para_o_modelo(pack, ctx, {})["reclamacoes_abertas"] == 1
    assert contexto.vazio()["reclamacoes"] == []

    admin = await make_user(role=UserRole.ADMIN)
    texto = await claude_tarefas.consultar_pedido(db, dono=admin, args={"pedido": "297840"})
    assert (
        f"Na plataforma: Mediação {RECLAMACAO} no Mercado Livre (pedido {ORDER_CARRINHO})"
        " · ABERTA"
    ) in texto
    assert "prazo para responder" in texto
    assert "Devolução: nenhuma." not in texto
    assert "Devolução: nenhuma lançada na aba Devoluções" in texto
    # Pelo nº na plataforma também.
    texto = await claude_tarefas.consultar_pedido(
        db, dono=admin, args={"pedido": ORDER_CARRINHO}
    )
    assert f"Mediação {RECLAMACAO}" in texto


# ─────────────── revisão: cron das devoluções e trava da conversa ───────────────


async def test_cron_liga_as_devolucoes_commitando_em_lotes(db, monkeypatch):
    from app.config import get_settings
    from app.services.atendimento import reclamacoes_devolucoes

    s = get_settings()
    monkeypatch.setattr(s, "atendimento_leitura_ativa", True)
    monkeypatch.setattr(s, "atendimento_reclamacoes_ativa", True)
    monkeypatch.setattr(reclamacoes, "_pegar_trava", AsyncMock(return_value=(True, None)))
    monkeypatch.setattr(
        reclamacoes, "sincronizar_todas_ml", AsyncMock(return_value={"contas": 0})
    )
    ligar = AsyncMock(return_value={"casos": 0})
    monkeypatch.setattr(reclamacoes_devolucoes, "ligar_devolucoes", ligar)
    assert await reclamacoes.atendimento_reclamacoes(None) == {
        "ml": {"contas": 0},
        "devolucoes": {"casos": 0},
    }
    assert ligar.await_args.kwargs["commit_a_cada"] == reclamacoes.COMMIT_DEVOLUCOES_A_CADA
    assert reclamacoes.COMMIT_DEVOLUCOES_A_CADA > 0


async def test_recalculo_da_reclamacao_pula_a_conversa_travada(db):
    pack = await _pack_producao(db, PACK_CARRINHO, ORDER_CARRINHO, ultima=T0)
    await _reclamacao_ml(db, RECLAMACAO, ORDER_CARRINHO)
    async with _db.SessionLocal() as outro:
        # A tela (troca à mão) ou o sync está com o pack travado agora.
        await outro.execute(
            select(AtendimentoConversa.id)
            .where(AtendimentoConversa.id == pack.id)
            .with_for_update(key_share=True)
        )
        alvo = await reclamacoes.conversas_para_etiqueta(db, "ml", [ORDER_CARRINHO])
        assert [c.id for c in alvo] == [pack.id]
        assert await reclamacoes.recalcular_etiquetas(db, alvo, motivo="teste", agora=T0) == 0
        await db.commit()
        await outro.rollback()
    await db.refresh(pack)
    assert pack.etiqueta is None
    # Livre: trava, relê e recalcula.
    alvo = await reclamacoes.conversas_para_etiqueta(db, "ml", [ORDER_CARRINHO])
    await reclamacoes.recalcular_etiquetas(db, alvo, motivo="teste", agora=T0)
    await db.commit()
    assert alvo[0].etiqueta == "reclamacao"


# ─────────────── revisão: AdsPower e Ag. cancelamento ───────────────


async def test_perfil_do_adspower_so_com_permissao_de_lojas(db, make_user):
    from app.services.atendimento import painel

    pack = await _pack_producao(db, PACK_CARRINHO, ORDER_CARRINHO)
    sem = await make_user(permissions={"atendimento": {"view": True}})
    com = await make_user(
        permissions={"atendimento": {"view": True}, "lojas_info": {"view": True}}
    )
    admin = await make_user(role=UserRole.ADMIN)
    assert [painel.ve_lojas(u) for u in (sem, com, admin, None)] == [False, True, True, False]
    dados = await painel.painel_da_conversa(db, pack, user=sem)
    assert dados["adspower"]["codigo"] == "sem_permissao"
    assert dados["adspower"]["perfil"] is None
    dados = await painel.painel_da_conversa(db, pack, user=admin)
    assert dados["adspower"]["codigo"] != "sem_permissao"


def test_reprovacao_por_pessoa_em_pedido_que_o_robo_segurou_aparece():
    from dataclasses import replace
    from types import SimpleNamespace
    from uuid import uuid4

    robo = etiqueta_fatos.PedidoBling(
        numero="1",
        situacao="83955",
        pino_margem="Pendente",
        origem_ag_cancelamento="margens_auto",
    )
    assert not etiqueta_fatos.ag_cancelamento_visivel(robo)
    # O robô reprovou (margem abaixo da mínima): continua a trava interna.
    assert not etiqueta_fatos.ag_cancelamento_visivel(replace(robo, pino_margem="Reprovado"))
    # A PESSOA reprovou na aba Margem (o Reprovar não grava trilha de situação
    # com o pedido já em 83955): é cancelamento de verdade.
    pessoa = replace(robo, pino_margem="Reprovado", pino_por_pessoa=True)
    assert etiqueta_fatos.ag_cancelamento_visivel(pessoa)
    # Pelas linhas do espelho: `aprovado_por` no item que tem o pino.
    linha = SimpleNamespace(
        numero="1",
        numeroloja="X",
        bling_id=1,
        situacao="83955",
        status="Reprovado",
        loja=None,
        data=T0,
        item_index=0,
        aprovado_por=uuid4(),
    )
    assert etiqueta_fatos._pedido_das_linhas(["X"], [linha]).pino_por_pessoa is True
    sem_pessoa = SimpleNamespace(**{**linha.__dict__, "aprovado_por": None})
    assert etiqueta_fatos._pedido_das_linhas(["X"], [sem_pessoa]).pino_por_pessoa is False
