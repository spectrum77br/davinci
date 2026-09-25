"""Varredura das aberturas presas (services/chamados_pendencias).

Eduardo 17/09: "corrija para pegar todos os casos". Até aqui a abertura que a API
não conseguia fazer ficava `pendente`/`falhou` pra sempre — 290112 (R$ 6.700)
parado desde 09/09 sem ninguém saber."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from urllib.parse import parse_qs

import httpx
import pytest
import respx
from app.models import Chamado, ChamadoMensagem, Devolution
from app.services import chamados_pendencias as svc
from sqlalchemy import select

pytestmark = pytest.mark.asyncio


class _Threema:
    """Cliente de Threema falso: guarda o texto em vez de mandar."""

    enviados: list[str] = []

    def __init__(self, *args, **kwargs) -> None:
        """Aceita `contexto=` como o cliente de verdade (conversas separadas)."""

    async def send_to_all(self, texto: str, recipients=None) -> None:
        self.enviados.append(texto)


def _sem_threema(monkeypatch) -> list[str]:
    _Threema.enviados = []
    monkeypatch.setattr(svc, "_destinatarios", lambda: ["ABCDEFGH"])
    monkeypatch.setattr(svc.threema, "ThreemaClient", _Threema)
    return _Threema.enviados


async def _caso(db, *, pedido: str, erro: str | None, status: str = "pendente",
                plataforma: str = "Shopee", horas: int = 48, custo: float | None = 6700.0,
                canal: str = "api", origem: str = "devolucao",
                origem_ref: str | None = None) -> Chamado:
    """Chamado aberto com a mensagem de abertura presa há `horas`."""
    dev = Devolution(conta="marquezini", motivo_devolucao="Não recebido", custo_produto=custo)
    db.add(dev)
    await db.flush()
    ch = Chamado(
        pedido_bling=pedido, plataforma=plataforma, conta="Shopee Marquezini",
        origem=origem, canal="api", origem_ref=origem_ref or str(dev.id),
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
    enviados = _sem_threema(monkeypatch)
    ch = await _caso(db, pedido="291728", erro="devolucao_sem_return")

    r = await svc.varrer(db, dry_run=True)

    assert r["robo"] == 1 and r["avisos"]
    assert (await _abertura(db, ch)).canal == "api"
    assert not [n for n in await _notas(db, ch) if svc.MARCA in n]
    assert enviados == []


@pytest.fixture
def threema_separado(monkeypatch):
    """Cliente real, credenciais fictícias e HTTP interceptado: nenhum envio externo."""
    valores = {
        "threema_gateway_id": "*GLOBAL1",
        "threema_gateway_secret": "global-test-secret",
        "threema_separate_chats": True,
        "threema_context_channels": {
            "logistica": "geral", "importacao": "estoque", "juridico": "desativado",
        },
        "threema_recipients": "RESERVA1",
    }
    for canal in ("logistica", "margem", "estoque", "devolucoes", "juridico", "importacao", "flex"):
        valores[f"threema_{canal}_gateway_id"] = ""
        valores[f"threema_{canal}_gateway_secret"] = ""
    valores["threema_devolucoes_gateway_id"] = "*DEVOL01"
    valores["threema_devolucoes_gateway_secret"] = "devolucoes-test-secret"  # noqa: S105 — fictícia
    config = SimpleNamespace(**valores)
    monkeypatch.setattr(svc.threema, "get_settings", lambda: config)
    monkeypatch.setattr(svc, "_destinatarios", lambda: ["ABCDEFGH"])
    enviados = []

    def receber(request):
        enviados.append({k: v[0] for k, v in parse_qs(request.content.decode()).items()})
        return httpx.Response(200, text="message-id-ficticio")

    with respx.mock(assert_all_mocked=True, assert_all_called=False) as http:
        http.post("https://msgapi.threema.ch/send_simple").mock(side_effect=receber)
        yield config, enviados


@pytest.mark.parametrize(("origem", "origem_ref", "remetente"), [
    ("devolucao", None, "*DEVOL01"),
    ("vendas", "tiktok_reembolso:caso-antigo", "*DEVOL01"),
    ("logistica", None, "*GLOBAL1"),
    ("manual", None, "*GLOBAL1"),
    ("vendas", "outro-reembolso", "*GLOBAL1"),
])
async def test_lote_unico_usa_remetente_da_origem(
    db, threema_separado, origem, origem_ref, remetente,
):
    _, enviados = threema_separado
    await _caso(
        db, pedido="900001", erro="devolucao_sem_foto", origem=origem, origem_ref=origem_ref,
    )

    r = await svc.varrer(db)

    assert r["novo"] == 1
    assert len(enviados) == 1
    assert enviados[0]["from"] == remetente
    assert enviados[0]["to"] == "ABCDEFGH"  # não usa a lista geral de reserva
    assert "900001" in enviados[0]["text"]


async def test_lote_misto_separa_conversas_preserva_resumo_e_dedup(db, threema_separado):
    _, enviados = threema_separado
    dev = await _caso(db, pedido="900011", erro="devolucao_sem_foto")
    geral = await _caso(db, pedido="900012", erro="devolucao_sem_foto", origem="logistica")
    legado = await _caso(
        db, pedido="900013", erro="devolucao_sem_return", origem="vendas",
        origem_ref="tiktok_reembolso:antigo",
    )

    r = await svc.varrer(db)

    assert r["novo"] == 3 and r["humano"] == 2 and r["robo"] == 1
    assert len(r["avisos"]) == 3  # contrato continua uma lista plana na ordem da varredura
    for pedido, linha in zip(("900011", "900012", "900013"), r["avisos"], strict=True):
        assert pedido in linha
    assert len(enviados) == 2
    textos = {e["from"]: e["text"] for e in enviados}
    assert "900011" in textos["*DEVOL01"] and "900013" in textos["*DEVOL01"]
    assert "900012" not in textos["*DEVOL01"]
    assert "900012" in textos["*GLOBAL1"]
    assert "900011" not in textos["*GLOBAL1"] and "900013" not in textos["*GLOBAL1"]
    assert {e["to"] for e in enviados} == {"ABCDEFGH"}
    assert (await _abertura(db, legado)).canal == "robo"
    for ch in (dev, geral, legado):
        assert len([n for n in await _notas(db, ch) if svc.MARCA in n]) == 1

    await svc.varrer(db)
    assert len(enviados) == 2
    for ch in (dev, geral, legado):
        assert len([n for n in await _notas(db, ch) if svc.MARCA in n]) == 1


@pytest.mark.parametrize("primeira_origem", ["devolucao", "logistica"])
async def test_falha_de_um_canal_nao_impede_outro(db, threema_separado, primeira_origem):
    config, enviados = threema_separado
    if primeira_origem == "devolucao":
        config.threema_devolucoes_gateway_secret = ""
        segunda_origem, remetente = "logistica", "*GLOBAL1"
    else:
        config.threema_gateway_secret = ""
        segunda_origem, remetente = "devolucao", "*DEVOL01"
    primeiro = await _caso(db, pedido="900021", erro="devolucao_sem_foto", origem=primeira_origem)
    segundo = await _caso(db, pedido="900022", erro="devolucao_sem_foto", origem=segunda_origem)

    r = await svc.varrer(db)

    assert r["novo"] == 2 and len(r["avisos"]) == 2
    assert len(enviados) == 1
    assert enviados[0]["from"] == remetente
    assert "900022" in enviados[0]["text"] and "900021" not in enviados[0]["text"]
    # Mantém o carimbo da mudança de rota, mesmo quando o aviso é best-effort.
    for ch in (primeiro, segundo):
        assert len([n for n in await _notas(db, ch) if svc.MARCA in n]) == 1


async def test_dry_run_misto_nao_envia_nem_carimba(db, threema_separado):
    _, enviados = threema_separado
    casos = [
        await _caso(db, pedido="900031", erro="devolucao_sem_return"),
        await _caso(db, pedido="900032", erro="devolucao_sem_return", origem="logistica"),
    ]

    r = await svc.varrer(db, dry_run=True)

    assert r["robo"] == 2 and len(r["avisos"]) == 2
    assert enviados == []
    for ch in casos:
        assert (await _abertura(db, ch)).canal == "api"
        assert await _notas(db, ch) == []


async def test_limite_de_linhas_e_por_conversa(db, threema_separado):
    _, enviados = threema_separado
    for numero in range(16):
        await _caso(db, pedido=f"DEV{numero:02d}", erro="devolucao_sem_foto")
    await _caso(db, pedido="GERAL01", erro="devolucao_sem_foto", origem="logistica")

    r = await svc.varrer(db)

    assert len(r["avisos"]) == 17
    textos = {e["from"]: e["text"] for e in enviados}
    assert "DEV14" in textos["*DEVOL01"] and "DEV15" not in textos["*DEVOL01"]
    assert textos["*DEVOL01"].endswith("\n…")
    assert "GERAL01" in textos["*GLOBAL1"] and not textos["*GLOBAL1"].endswith("\n…")
