"""Varredura das aberturas presas (services/chamados_pendencias).

Eduardo 17/09: "corrija para pegar todos os casos". Até aqui a abertura que a API
não conseguia fazer ficava `pendente`/`falhou` pra sempre — 290112 (R$ 6.700)
parado desde 09/09 sem ninguém saber."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.models import Chamado, ChamadoMensagem, Devolution
from app.services import chamados_pendencias as svc

pytestmark = pytest.mark.asyncio


class _Threema:
    """Cliente de Threema falso: guarda o texto em vez de mandar."""

    enviados: list[str] = []

    async def send_to_all(self, texto: str, recipients=None) -> None:
        self.enviados.append(texto)


def _sem_threema(monkeypatch) -> list[str]:
    _Threema.enviados = []
    monkeypatch.setattr(svc, "_destinatarios", lambda: ["ABCDEFGH"])
    monkeypatch.setattr(svc.threema, "ThreemaClient", _Threema)
    return _Threema.enviados


async def _caso(db, *, pedido: str, erro: str | None, status: str = "pendente",
                plataforma: str = "Shopee", horas: int = 48, custo: float | None = 6700.0,
                canal: str = "api") -> Chamado:
    """Chamado aberto com a mensagem de abertura presa há `horas`."""
    dev = Devolution(conta="marquezini", motivo_devolucao="Não recebido", custo_produto=custo)
    db.add(dev)
    await db.flush()
    ch = Chamado(
        pedido_bling=pedido, plataforma=plataforma, conta="Shopee Marquezini",
        origem="devolucao", canal="api", origem_ref=str(dev.id),
        data=datetime.now(UTC).date(),
    )
    db.add(ch)
    await db.flush()
    presa = datetime.now(UTC) - timedelta(hours=horas)
    msg = ChamadoMensagem(
        chamado_id=ch.id, direcao="enviada", tipo="abertura", canal=canal,
        texto="Contestação da devolução.", autor_nome="sistema", status=status, erro=erro,
        created_at=presa, updated_at=presa,
    )
    db.add(msg)
    await db.flush()
    return ch


async def _abertura(db, ch: Chamado) -> ChamadoMensagem:
    return (
        await db.execute(
            select(ChamadoMensagem).where(
                ChamadoMensagem.chamado_id == ch.id, ChamadoMensagem.tipo == "abertura"
            )
        )
    ).scalars().first()


async def _notas(db, ch: Chamado) -> list[str]:
    rows = (
        await db.execute(
            select(ChamadoMensagem.texto).where(
                ChamadoMensagem.chamado_id == ch.id, ChamadoMensagem.tipo == "sistema"
            )
        )
    ).scalars().all()
    return [r or "" for r in rows]


async def test_sem_caminho_por_api_vai_pra_fila_do_robo(db, monkeypatch):
    """`devolucao_sem_return` (290112, R$ 6.700): a API nunca vai conseguir —
    a abertura passa pro canal robô, que é a fila do lease do Seller Center."""
    enviados = _sem_threema(monkeypatch)
    ch = await _caso(db, pedido="290112", erro="devolucao_sem_return")

    r = await svc.varrer(db)

    assert r["robo"] == 1 and r["novo"] == 1
    msg = await _abertura(db, ch)
    assert (msg.canal, msg.status) == ("robo", "pendente")
    notas = await _notas(db, ch)
    assert any(svc.MARCA in n and "robô" in n for n in notas)
    assert enviados and "290112" in enviados[0] and "6.700,00" in enviados[0]


async def test_nao_repete_carimbo_nem_aviso(db, monkeypatch):
    enviados = _sem_threema(monkeypatch)
    ch = await _caso(db, pedido="292592", erro="devolucao_sem_foto")

    await svc.varrer(db)
    await svc.varrer(db)

    notas = [n for n in await _notas(db, ch) if svc.MARCA in n]
    assert len(notas) == 1
    assert len(enviados) == 1


async def test_espera_da_plataforma_so_vira_alerta_depois_de_3_dias(db, monkeypatch):
    _sem_threema(monkeypatch)
    novo = await _caso(db, pedido="292357", erro="tiktok_recusa_bloqueada", horas=20)
    antigo = await _caso(db, pedido="292358", erro="tiktok_recusa_bloqueada", horas=24 * 5)

    r = await svc.varrer(db)

    assert r["espera"] == 1
    assert not [n for n in await _notas(db, novo) if svc.MARCA in n]
    assert [n for n in await _notas(db, antigo) if svc.MARCA in n]
    # espera não muda de canal: o retry de hora em hora continua
    assert (await _abertura(db, antigo)).canal == "api"


async def test_falha_que_o_acompanhamento_ja_segue_fica_quieta(db, monkeypatch):
    _sem_threema(monkeypatch)
    ch = await _caso(db, pedido="292648", erro="shopee_ja_contestada", status="falhou")

    r = await svc.varrer(db)

    assert r["acompanha"] == 1 and r["novo"] == 0
    assert not [n for n in await _notas(db, ch) if svc.MARCA in n]


async def test_dry_run_nao_grava(db, monkeypatch):
    _sem_threema(monkeypatch)
    ch = await _caso(db, pedido="291728", erro="devolucao_sem_return")

    r = await svc.varrer(db, dry_run=True)

    assert r["robo"] == 1 and r["avisos"]
    assert (await _abertura(db, ch)).canal == "api"
    assert not [n for n in await _notas(db, ch) if svc.MARCA in n]
