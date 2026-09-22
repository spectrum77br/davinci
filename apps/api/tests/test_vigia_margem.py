"""Robô da Margem (services/vigia_margem) — o DEPOIS do auto-hold.

O que este arquivo trava (22/09/2026):

- quem é "segurado sem decisão": 83955 + pino 'Pendente' + `aprovado_por`
  vazio + auditoria automática de hold mais velha que `segurado_horas`.
  Reprovado pelo robô, 83955 do controle de estoque (status NULL) e hold
  recente ficam de fora;
- a ocorrência do segurado fecha sozinha quando alguém decide (Aprovar grava
  o pino e o autor) — `fechar_nao_vistas` com prefixo;
- margem fora do normal abre enquanto estiver alta, fecha quando o custo é
  corrigido, e NÃO depende da auditoria `alerta_margem_alta` (o dedup do
  Threema) — se dependesse, a ocorrência fecharia sozinha como "sumiu" na
  rodada seguinte ao 1º aviso;
- o HOOK no `margem_auto_hold`: falha ao segurar abre `falha:<pedido>` com a
  operação em `dados`, o tick seguinte que consegue segurar a fecha, e a
  rodada fecha a que já se resolveu por fora;
- o fechamento por prefixo NÃO mata a `falha:` (ela é de evento, a rodada não
  a re-vê);
- robô `desligado`: o hook não grava nada.

O Bling é substituído por um fake (as chamadas HTTP têm teste próprio em
test_margem_auto_hold).
"""
# ruff: noqa: S608
from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    BlingOrder,
    MargemAudit,
    OuvidoriaOcorrencia,
    OuvidoriaRobo,
    SituacaoBling,
)
from app.services import margem_auto_hold
from app.services import ouvidoria as svc
from app.services import vigia_margem as vm

pytestmark = pytest.mark.asyncio

ROBO = vm.ROBO
AGORA = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)
HOJE = date(2026, 9, 22)


# ─── fixtures e semeadura ──────────────────────────────────────────────────


async def _limpar(db: AsyncSession) -> None:
    for tbl in ("ouvidoria_ocorrencias", "ouvidoria_rodadas", "ouvidoria_robos"):
        await db.execute(text(f"DELETE FROM {tbl}"))
    await db.commit()


@pytest_asyncio.fixture(autouse=True)
async def _limpa_ouvidoria(db: AsyncSession):
    await _limpar(db)
    await svc.sincronizar_catalogo(db)
    await db.commit()
    yield
    await _limpar(db)


class FakeBling:
    """Mesmo contrato do fake de test_margem_auto_hold: só o que o hold usa."""

    def __init__(self, *, fail_situacao_for: set[int] | None = None) -> None:
        self.fail_situacao_for = fail_situacao_for or set()
        self.situacao_calls: list[tuple[int, int]] = []

    async def get_order(self, bling_id: int) -> dict:
        return {
            "id": bling_id,
            "numero": "291670",
            "observacoes": None,
            "contato": {"id": 1, "nome": "Cliente"},
            "itens": [{"id": 10, "codigo": "sku-1"}],
        }

    async def update_order(self, bling_id: int, body: dict) -> None:
        return None

    async def update_order_situacao(self, bling_id: int, situacao_id: int) -> None:
        if bling_id in self.fail_situacao_for:
            raise RuntimeError("bling fora do ar")
        self.situacao_calls.append((bling_id, situacao_id))


async def _seed_linha(
    db: AsyncSession,
    *,
    pedido: str,
    bling_id: int,
    situacao: str,
    status: str | None,
    aprovado_por: uuid.UUID | None = None,
    margem: float | None = 0.5,
    minima: float | None = 0.10,
    lucro: float | None = 120.5,
    saldo_gap: bool = False,
    plataforma: str = "amazon",
    loja: str | None = "Amazon kfa",
    produto: str = "Fone XPTO",
    pedido_marketplace: str | None = "MP-1",
) -> None:
    """Uma linha em bling_orders + a linha-item correspondente no snapshot.

    Mesmos valores em fração do snapshot de produção (0.5 = 50%).
    `saldo_gap` → valorbase 100 vs líquido 80 (divergência real > R$0,01),
    que é o motivo típico de um hold com pino 'Pendente' (margem baixa
    reprova direto desde 11/09).
    """
    await db.merge(SituacaoBling(id=6, nome="Em aberto"))
    await db.merge(SituacaoBling(id=83955, nome="Aguardando Cancelamento"))
    db.add(
        BlingOrder(
            bling_id=bling_id,
            numero=pedido,
            item_codigo=f"sku-{pedido}",
            item_index=0,
            situacao=situacao,
            status=status,
            aprovado_por=aprovado_por,
        )
    )
    await db.commit()
    await db.execute(
        text(
            """
            INSERT INTO verificar_margem (
                bling_order_item_id, pedido_bling, pedido_marketplace, bling_id,
                sku, produto, situacao, situacao_nome,
                plataforma_bling, loja_nome, item_proportion,
                bling_status_margem, marketplace_margem, margem_minima,
                marketplace_lucro, bling_valorbase_item,
                marketplace_liquido_base_margem_item
            )
            VALUES (
                :id, :pedido, :pedido_mp, :bling_id,
                :sku, :produto, :situacao, :situacao_nome,
                :plataforma, :loja, 1,
                :status, :margem, :minima,
                :lucro, :valorbase,
                :liquido
            )
            """
        ),
        {
            "id": str(uuid.uuid4()),
            "pedido": pedido,
            "pedido_mp": pedido_marketplace,
            "bling_id": bling_id,
            "sku": f"sku-{pedido}",
            "produto": produto,
            "situacao": situacao,
            "situacao_nome": (
                "Aguardando Cancelamento" if situacao == "83955" else "Em aberto"
            ),
            "plataforma": plataforma,
            "loja": loja,
            "status": status,
            "margem": margem,
            "minima": minima,
            "lucro": lucro,
            "valorbase": 100 if saldo_gap else None,
            "liquido": 80 if saldo_gap else None,
        },
    )
    await db.commit()


async def _auditoria_hold(
    db: AsyncSession, *, pedido: str, bling_id: int, ha_horas: float
) -> None:
    """A linha que o `_hold_one` grava ao segurar — é ela que dá a IDADE."""
    db.add(
        MargemAudit(
            created_at=AGORA - timedelta(hours=ha_horas),
            pedido_bling=pedido,
            bling_id=str(bling_id),
            acao="situacao",
            valor_antigo="6",
            valor_novo="83955",
            origem="margens_auto",
            mudado_por=None,
        )
    )
    await db.commit()


async def _seed_segurado(
    db: AsyncSession,
    *,
    pedido: str,
    bling_id: int,
    ha_horas: float = 30,
    status: str | None = "Pendente",
    com_auditoria: bool = True,
    **kw,
) -> None:
    await _seed_linha(
        db, pedido=pedido, bling_id=bling_id, situacao="83955", status=status, **kw
    )
    if com_auditoria:
        await _auditoria_hold(db, pedido=pedido, bling_id=bling_id, ha_horas=ha_horas)


async def _abertas(db: AsyncSession) -> dict[str, OuvidoriaOcorrencia]:
    rows = (
        (
            await db.execute(
                select(OuvidoriaOcorrencia).where(
                    OuvidoriaOcorrencia.robo_chave == ROBO,
                    OuvidoriaOcorrencia.fechada_em.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    return {o.chave: o for o in rows}


async def _todas(db: AsyncSession, chave: str) -> list[OuvidoriaOcorrencia]:
    return list(
        (
            await db.execute(
                select(OuvidoriaOcorrencia).where(
                    OuvidoriaOcorrencia.robo_chave == ROBO,
                    OuvidoriaOcorrencia.chave == chave,
                )
            )
        )
        .scalars()
        .all()
    )


# ─── segurado sem decisão ──────────────────────────────────────────────────


async def test_segurado_ha_mais_de_24h_abre_com_os_numeros_da_aba(db: AsyncSession):
    await _seed_segurado(db, pedido="291670", bling_id=111, ha_horas=30, saldo_gap=True)

    res = await vm.vigia_margem_run(db, agora=AGORA)

    o = (await _abertas(db))["segurado:291670"]
    assert o.titulo == "Pedido segurado há 30 h sem decisão"
    assert o.severidade == "robo_segurou" and o.precisa_pessoa is True
    assert o.acao == vm.ACAO_DECIDIR
    assert o.link == "/margem?pedido=291670"
    assert o.plataforma == "amazon" and o.conta == "Amazon kfa"
    # O número do BLING no campo `pedido` (é o que a aba e o link usam); o da
    # plataforma fica em `dados`.
    assert o.pedido == "291670" and o.dados["pedido_marketplace"] == "MP-1"
    assert "saldo divergente" in o.detalhe
    assert "margem 50% (mínimo 10%)" in o.detalhe
    assert "lucro R$ 120,50" in o.detalhe
    assert o.dados["horas"] == 30 and o.dados["bling_id"] == 111
    assert res["segurados"] == 1 and res["segurados_novos"] == 1
    assert res["resumo"] == "1 segurado · 0 falhas · 0 margem alta"


async def test_segurado_ha_10h_ainda_nao_abre(db: AsyncSession):
    await _seed_segurado(db, pedido="291670", bling_id=111, ha_horas=10, saldo_gap=True)

    res = await vm.vigia_margem_run(db, agora=AGORA)

    assert await _abertas(db) == {}
    assert res["segurados"] == 0


async def test_reprovado_pelo_robo_nao_e_segurado(db: AsyncSession):
    """Reprovado SAI da aba Pendentes — não há decisão parada a cobrar (quem
    revisita esses é a reavaliação dos reprovados)."""
    await _seed_segurado(db, pedido="291670", bling_id=111, status="Reprovado")

    await vm.vigia_margem_run(db, agora=AGORA)

    assert await _abertas(db) == {}


async def test_83955_do_controle_de_estoque_fica_de_fora(db: AsyncSession):
    """83955 com status NULL é de outro fluxo (falta de estoque): não tem
    auditoria de hold do robô e nunca esteve na Margem."""
    await _seed_segurado(db, pedido="291670", bling_id=111, status=None, com_auditoria=False)

    await vm.vigia_margem_run(db, agora=AGORA)

    assert await _abertas(db) == {}


async def test_83955_com_pino_mas_sem_auditoria_do_robo_fica_de_fora(db: AsyncSession):
    """Sem a auditoria automática não há QUANDO — e não foi o robô que segurou."""
    await _seed_segurado(db, pedido="291670", bling_id=111, com_auditoria=False)

    await vm.vigia_margem_run(db, agora=AGORA)

    assert await _abertas(db) == {}


async def test_pessoa_aprovou_o_hold_e_a_ocorrencia_fecha_sozinha(db: AsyncSession):
    await _seed_segurado(db, pedido="291670", bling_id=111, saldo_gap=True)
    await vm.vigia_margem_run(db, agora=AGORA)
    aberta = (await _abertas(db))["segurado:291670"]

    # Aprovar na aba: pino 'Aprovado' + autor gravado.
    await db.execute(
        text(
            "UPDATE bling_orders SET situacao = '6', status = 'Aprovado' "
            "WHERE bling_id = 111"
        )
    )
    await db.commit()
    res = await vm.vigia_margem_run(db, agora=AGORA + timedelta(minutes=30))

    await db.refresh(aberta)
    assert aberta.fechamento == "sumiu" and aberta.fechada_por == "robô"
    assert res["sumiram"] == 1 and res["segurados"] == 0


async def test_segurado_sem_linha_no_snapshot_abre_assim_mesmo(db: AsyncSession):
    """O pedido está preso no Bling de verdade; o rebuild do snapshot pode
    não ter trazido a linha (pedido antigo). Esconder seria esconder
    justamente o caso mais esquecido."""
    await _seed_segurado(db, pedido="291670", bling_id=111)
    await db.execute(text("DELETE FROM verificar_margem"))
    await db.commit()

    await vm.vigia_margem_run(db, agora=AGORA)

    o = (await _abertas(db))["segurado:291670"]
    assert "sem dados de margem no snapshot" in o.detalhe
    assert o.plataforma is None and o.pedido == "291670"


# ─── margem fora do normal ─────────────────────────────────────────────────


async def test_margem_alta_abre_e_fecha_quando_o_custo_e_corrigido(db: AsyncSession):
    await _seed_linha(
        db, pedido="291671", bling_id=222, situacao="6", status=None, margem=0.75,
        lucro=300.0,
    )

    res = await vm.vigia_margem_run(db, agora=AGORA)

    o = (await _abertas(db))["margem_alta:291671"]
    assert o.titulo == "Margem fora do normal (75%) — confira o custo"
    assert o.severidade == "baixa" and o.precisa_pessoa is False
    assert o.acao == vm.ACAO_CUSTO and o.link == "/margem?pedido=291671"
    assert "lucro R$ 300,00" in o.detalhe and "Fone XPTO" in o.detalhe
    assert res["margem_alta"] == 1 and res["margem_alta_novas"] == 1
    assert res["resumo"] == "0 segurados · 0 falhas · 1 margem alta"

    # Custo corrigido no cadastro → a margem cai e a linha some da consulta.
    await db.execute(text("UPDATE verificar_margem SET marketplace_margem = 0.22"))
    await db.commit()
    res2 = await vm.vigia_margem_run(db, agora=AGORA + timedelta(minutes=30))

    await db.refresh(o)
    assert o.fechamento == "sumiu" and res2["sumiram"] == 1


async def test_margem_alta_nao_depende_do_dedup_do_threema(db: AsyncSession):
    """O `NOT EXISTS` da auditoria `alerta_margem_alta` é o dedup do Threema:
    se o robô da Ouvidoria o herdasse, a linha sumiria da consulta depois do
    1º aviso e a ocorrência fecharia como "sumiu" sem nada ter sido feito."""
    await _seed_linha(
        db, pedido="291671", bling_id=222, situacao="6", status=None, margem=0.75
    )
    db.add(
        MargemAudit(
            pedido_bling="291671",
            acao="alerta_margem_alta",
            valor_antigo=None,
            valor_novo="75.0%",
            origem="margens_auto",
            mudado_por=None,
        )
    )
    await db.commit()

    await vm.vigia_margem_run(db, agora=AGORA)

    assert "margem_alta:291671" in await _abertas(db)


async def test_margem_alta_so_em_em_aberto(db: AsyncSession):
    """Fora da janela de triagem o cadastro já não é corrigível a tempo."""
    await _seed_linha(
        db, pedido="291671", bling_id=222, situacao="83955", status="Pendente",
        margem=0.75,
    )

    await vm.vigia_margem_run(db, agora=AGORA)

    assert "margem_alta:291671" not in await _abertas(db)


# ─── hook: falha do robô ───────────────────────────────────────────────────


async def test_falha_do_hold_abre_ocorrencia_e_o_tick_seguinte_fecha(db: AsyncSession):
    # Pedido em Em aberto com saldo divergente → candidato a hold (pino
    # 'Pendente', sem reprovo: margem 50% está acima da mínima).
    await _seed_linha(
        db, pedido="291670", bling_id=111, situacao="6", status=None, saldo_gap=True
    )

    res = await margem_auto_hold.run(db, client=FakeBling(fail_situacao_for={111}), hoje=HOJE)
    assert res["failed"] == 1 and res["held"] == 0

    o = (await _abertas(db))["falha:291670"]
    assert o.titulo == "Não consegui segurar o pedido 291670 no Bling"
    assert o.severidade == "pessoa" and o.precisa_pessoa is True
    assert o.acao == vm.ACAO_CONFERIR_BLING and o.link == "/margem?pedido=291670"
    assert o.dados["operacao"] == "segurar" and o.dados["tentativas"] == 1
    assert "bling fora do ar" in o.detalhe
    assert o.plataforma == "amazon" and o.conta == "Amazon kfa"

    # Falhou de novo: mesma linha, contador sobe (a chave é uma por pedido).
    await margem_auto_hold.run(db, client=FakeBling(fail_situacao_for={111}), hoje=HOJE)
    await db.refresh(o)
    assert o.dados["tentativas"] == 2 and len(await _todas(db, "falha:291670")) == 1

    # Agora o Bling respondeu: o ponto de SUCESSO fecha a ocorrência.
    res3 = await margem_auto_hold.run(db, client=FakeBling(), hoje=HOJE)
    assert res3["held"] == 1
    await db.refresh(o)
    assert o.fechada_em is not None and o.fechamento == "sumiu"


async def test_rodada_fecha_falha_que_ja_se_resolveu_por_fora(db: AsyncSession):
    """Rede de segurança: alguém segurou o pedido na mão no Bling e o espelho
    já está em 83955 — a operação que falhava está feita."""
    await _seed_segurado(db, pedido="291670", bling_id=111, ha_horas=1)
    await margem_auto_hold._ouvidoria_falha(
        db,
        pedido_bling="291670",
        bling_id=111,
        operacao="segurar",
        erro="bling fora do ar",
    )
    falha = (await _abertas(db))["falha:291670"]

    res = await vm.vigia_margem_run(db, agora=AGORA)

    await db.refresh(falha)
    assert falha.fechamento == "sumiu"
    assert res["falhas_fechadas"] == 1 and res["falhas_abertas"] == 0


async def test_fechar_nao_vistas_com_prefixo_nao_mata_a_falha(db: AsyncSession):
    """A `falha:` é de EVENTO — a rodada não a re-vê. Sem o prefixo no
    `fechar_nao_vistas` ela morreria como "sumiu" na primeira rodada."""
    # Pedido ainda em Em aberto: a operação 'liberar' NÃO está concluída.
    await _seed_linha(db, pedido="291670", bling_id=111, situacao="6", status=None)
    await margem_auto_hold._ouvidoria_falha(
        db, pedido_bling="291670", bling_id=111, operacao="liberar", erro="timeout"
    )
    falha = (await _abertas(db))["falha:291670"]

    res = await vm.vigia_margem_run(db, agora=AGORA)

    await db.refresh(falha)
    assert falha.fechada_em is None
    assert res["falhas_abertas"] == 1 and res["falhas_fechadas"] == 0
    assert res["sumiram"] == 0
    assert res["resumo"] == "0 segurados · 1 falha · 0 margem alta"


async def test_hook_nao_grava_com_o_robo_desligado(db: AsyncSession):
    robo = await db.get(OuvidoriaRobo, ROBO)
    robo.modo = "desligado"
    await db.commit()

    await margem_auto_hold._ouvidoria_falha(
        db, pedido_bling="291670", bling_id=111, operacao="segurar", erro="x"
    )

    assert await _abertas(db) == {}


async def test_hook_nasce_silencioso_e_registra_mesmo_assim(db: AsyncSession):
    """`silencioso` = registra no painel e não manda Threema — é o modo com
    que os 6 robôs de 22/09 nascem."""
    robo = await db.get(OuvidoriaRobo, ROBO)
    assert robo.modo == "silencioso"

    await margem_auto_hold._ouvidoria_falha(
        db, pedido_bling="291670", bling_id=111, operacao="segurar", erro="x"
    )

    assert "falha:291670" in await _abertas(db)


# ─── resumo / rodada ───────────────────────────────────────────────────────


async def test_resumo_conta_segurados_falhas_abertas_e_margem_alta(db: AsyncSession):
    await _seed_segurado(db, pedido="291670", bling_id=111, saldo_gap=True)
    await _seed_segurado(db, pedido="291672", bling_id=333, saldo_gap=True)
    await _seed_linha(
        db, pedido="291671", bling_id=222, situacao="6", status=None, margem=0.75
    )
    await margem_auto_hold._ouvidoria_falha(
        db, pedido_bling="291671", bling_id=222, operacao="liberar", erro="timeout"
    )

    res = await vm.vigia_margem_run(db, agora=AGORA)

    assert res["resumo"] == "2 segurados · 1 falha · 1 margem alta"
    assert set(await _abertas(db)) == {
        "segurado:291670",
        "segurado:291672",
        "margem_alta:291671",
        "falha:291671",
    }
    rodada = (
        await db.execute(
            text(
                "SELECT ok, resumo, contadores FROM ouvidoria_rodadas "
                "WHERE robo_chave = :r"
            ),
            {"r": ROBO},
        )
    ).one()
    assert rodada.ok is True and rodada.resumo == res["resumo"]
    assert rodada.contadores["segurados_novos"] == 2


async def test_segunda_rodada_so_carimba_a_mesma_ocorrencia(db: AsyncSession):
    await _seed_segurado(db, pedido="291670", bling_id=111, saldo_gap=True)
    await vm.vigia_margem_run(db, agora=AGORA)
    o = (await _abertas(db))["segurado:291670"]

    depois = AGORA + timedelta(hours=2)
    res = await vm.vigia_margem_run(db, agora=depois)

    await db.refresh(o)
    assert o.fechada_em is None and o.ultima_vista_em == depois
    assert o.titulo == "Pedido segurado há 32 h sem decisão"
    assert res["segurados"] == 1 and res["segurados_novos"] == 0
    assert len(await _todas(db, "segurado:291670")) == 1


async def test_sweep_e_serializado_pelo_advisory_lock(db: AsyncSession, monkeypatch):
    """Sessão que segura o lock do sweep → o outro sweep sai na hora. O lock
    é o mesmo que o router lê pra desabilitar o botão "Rodar agora"."""
    from app.services.advisory_lock import SYNC_NAMESPACE

    got = (
        await db.execute(
            text("SELECT pg_try_advisory_xact_lock(:ns, :key)"),
            {"ns": SYNC_NAMESPACE, "key": vm._SWEEP_LOCK_KEY},
        )
    ).scalar()
    assert got
    assert await vm.vigia_margem_sweep() == {"skipped": "lock_busy"}
    await db.rollback()  # solta o lock

    chamado = []

    async def _run(session):
        chamado.append(1)
        return {"ok": True}

    monkeypatch.setattr(vm, "vigia_margem_run", _run)
    assert await vm.vigia_margem_sweep() == {"ok": True} and chamado == [1]


async def test_operacao_concluida_por_operacao():
    """A situação que significa "já resolveu" depende da operação gravada."""
    assert vm.operacao_concluida("segurar", "83955", "Pendente") is True
    assert vm.operacao_concluida("segurar", "6", None) is False
    assert vm.operacao_concluida("liberar", "6", "Aprovado") is True
    assert vm.operacao_concluida("liberar", "6", "Pendente") is False
    assert vm.operacao_concluida("voltar_pendente", "83955", "Pendente") is True
    assert vm.operacao_concluida(None, "83955", "Pendente") is False
    # REPROVAR exige o pino 'Reprovado': 83955 + 'Pendente' é o pedido apenas
    # SEGURADO esperando decisão — a reprovação continua sem ter acontecido.
    assert vm.operacao_concluida("reprovar", "83955", "Reprovado") is True
    assert vm.operacao_concluida("reprovar", "83955", "Pendente") is False
    # Segurar aceita qualquer pino: reprovado também está segurado.
    assert vm.operacao_concluida("segurar", "83955", "Reprovado") is True
