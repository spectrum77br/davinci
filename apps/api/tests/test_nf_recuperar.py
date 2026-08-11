"""Recuperador de NF (`run_recuperar_nf`): retry com teto 3, destrava de lease
expirado e re-encadeamento de pedidos 'processando' órfãos.

O service usa `session_scope()` — o conftest troca o engine module-level, então
dá pra chamar direto e conferir o resultado pela fixture `db` (mesmo schema).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    NfCommand,
    NfFaturador,
    NfFaturamento,
    User,
    UserRole,
    UserStatus,
)
from app.security.cipher import encrypt
from app.services import nf_emissao_gerar
from app.services.nf_recuperar import run_recuperar_nf


@pytest_asyncio.fixture
async def admin(db: AsyncSession) -> User:
    email = f"adm-{uuid.uuid4().hex[:6]}@davinci-test.com"
    u = User(open_id=f"email:{email}", email=email, role=UserRole.ADMIN, status=UserStatus.ACTIVE)
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


async def _seed_faturador(db: AsyncSession, *, modo: str = "upseller") -> NfFaturador:
    f = NfFaturador(
        nome=f"faturador {uuid.uuid4().hex[:6]}", modo=modo, nf_cheia=True,
        sku_fonte="principal", nome_fonte="produto", ncm="4202.12.10",
        ads_power="perfil-58", usuario="user-a", senha_enc=encrypt("segredoA"),
    )
    db.add(f)
    await db.flush()
    return f


def _seed_cmd(
    db: AsyncSession, *, action: str, status: str, numeros: list[str],
    attempts: int = 1, claimed_at: datetime | None = None,
    completed_at: datetime | None = None, faturador_id=None,
    ads_power: str | None = None,
) -> NfCommand:
    cmd = NfCommand(
        faturador_id=faturador_id, action=action, numeros=numeros,
        planilha=b"planilha", nome_arquivo="arq.xlsx", status=status,
        attempts=attempts, claimed_at=claimed_at, completed_at=completed_at,
        source="auto", ads_power=ads_power,
    )
    db.add(cmd)
    return cmd


@pytest.mark.asyncio
async def test_retry_failed_volta_pra_fila(db: AsyncSession):
    """Failed recente com tentativa sobrando volta pending e o painel re-marca
    'processando'; attempts fica intacto (é o lease que incrementa)."""
    _seed_cmd(
        db, action="import_avulsa", status="failed", numeros=["830001"],
        attempts=1, completed_at=datetime.now(UTC) - timedelta(minutes=30),
    )
    db.add(NfFaturamento(pedido_bling="830001", status_faturamento="erro", erro_faturamento="timeout"))
    await db.commit()

    summary = await run_recuperar_nf()
    assert summary["retry"] == 1
    assert summary["esgotados"] == 0

    db.expire_all()
    cmd = (await db.execute(select(NfCommand))).scalar_one()
    assert cmd.status == "pending"
    assert cmd.claimed_at is None
    assert cmd.completed_at is None
    assert cmd.attempts == 1
    fat = (
        await db.execute(
            select(NfFaturamento).where(NfFaturamento.pedido_bling == "830001")
        )
    ).scalar_one()
    assert fat.status_faturamento == "processando"
    assert fat.erro_faturamento is None


@pytest.mark.asyncio
async def test_retry_respeita_teto_e_janela(db: AsyncSession):
    """attempts>=3 ou falha mais velha que 24h NÃO ressuscitam."""
    _seed_cmd(
        db, action="import_avulsa", status="failed", numeros=["830001"],
        attempts=3, completed_at=datetime.now(UTC) - timedelta(minutes=30),
    )
    _seed_cmd(
        db, action="import_avulsa", status="failed", numeros=["830002"],
        attempts=1, completed_at=datetime.now(UTC) - timedelta(days=2),
    )
    await db.commit()

    summary = await run_recuperar_nf()
    assert summary["retry"] == 0

    db.expire_all()
    cmds = (await db.execute(select(NfCommand))).scalars().all()
    assert {c.status for c in cmds} == {"failed"}


@pytest.mark.asyncio
async def test_retry_nao_duplica_com_comando_ativo(db: AsyncSession):
    """Se já há comando ativo da MESMA action cobrindo o pedido (re-enfileirar
    humano), o failed não volta — evita rodar o mesmo pedido duas vezes."""
    _seed_cmd(
        db, action="import_avulsa", status="failed", numeros=["830001"],
        attempts=1, completed_at=datetime.now(UTC) - timedelta(minutes=30),
    )
    _seed_cmd(db, action="import_avulsa", status="pending", numeros=["830001"], attempts=0)
    await db.commit()

    summary = await run_recuperar_nf()
    assert summary["retry"] == 0

    db.expire_all()
    cmds = (await db.execute(select(NfCommand))).scalars().all()
    assert sorted(c.status for c in cmds) == ["failed", "pending"]


@pytest.mark.asyncio
async def test_destrava_claimed_expirado(db: AsyncSession):
    """Claimed velho com tentativa sobrando volta pending; claimed fresco fica."""
    _seed_cmd(
        db, action="imprimir_etiqueta", status="claimed", numeros=["830001"],
        attempts=1, claimed_at=datetime.now(UTC) - timedelta(hours=1),
    )
    _seed_cmd(
        db, action="imprimir_etiqueta", status="claimed", numeros=["830002"],
        attempts=1, claimed_at=datetime.now(UTC) - timedelta(minutes=10),
    )
    await db.commit()

    summary = await run_recuperar_nf()
    assert summary["destravados"] == 1
    assert summary["esgotados"] == 0

    db.expire_all()
    por_numero = {
        c.numeros[0]: c for c in (await db.execute(select(NfCommand))).scalars().all()
    }
    assert por_numero["830001"].status == "pending"
    assert por_numero["830001"].claimed_at is None
    assert por_numero["830002"].status == "claimed"


@pytest.mark.asyncio
async def test_claimed_esgotado_vira_failed(db: AsyncSession):
    """Claimed expirado SEM tentativa sobrando fecha failed de vez e a etapa
    do painel vira 'erro'."""
    _seed_cmd(
        db, action="imprimir_etiqueta", status="claimed", numeros=["830001"],
        attempts=3, claimed_at=datetime.now(UTC) - timedelta(hours=1),
    )
    await db.commit()

    summary = await run_recuperar_nf()
    assert summary["esgotados"] == 1
    assert summary["destravados"] == 0

    db.expire_all()
    cmd = (await db.execute(select(NfCommand))).scalar_one()
    assert cmd.status == "failed"
    assert "lease expirado" in (cmd.result or "")
    assert cmd.completed_at is not None
    fat = (
        await db.execute(
            select(NfFaturamento).where(NfFaturamento.pedido_bling == "830001")
        )
    ).scalar_one()
    assert fat.status_etiqueta == "erro"
    assert "lease expirado" in (fat.erro_etiqueta or "")


@pytest.mark.asyncio
async def test_orfao_faturamento_reencadeia_emissao(db: AsyncSession):
    """Pedido 'processando' sem comando ativo mas com import_avulsa done:
    faturador upseller ganha `emitir_nf_upseller`; faturador bling ganha
    `emitir_nf_bling` (o import não emite a NF — o elo perdido é a emissão)."""
    fat_up = await _seed_faturador(db, modo="upseller")
    fat_bl = await _seed_faturador(db, modo="bling")
    fat_up_id = fat_up.id
    fat_bl_id = fat_bl.id
    _seed_cmd(
        db, action="import_avulsa", status="done", numeros=["830001"],
        faturador_id=fat_up_id,
        completed_at=datetime.now(UTC) - timedelta(hours=2),
    )
    _seed_cmd(
        db, action="import_avulsa", status="done", numeros=["830002"],
        faturador_id=fat_bl.id,
        completed_at=datetime.now(UTC) - timedelta(hours=2),
    )
    db.add(NfFaturamento(pedido_bling="830001", status_faturamento="processando"))
    db.add(NfFaturamento(pedido_bling="830002", status_faturamento="processando"))
    await db.commit()

    summary = await run_recuperar_nf()
    assert summary["orfaos_faturamento"] == 2

    db.expire_all()
    novos = (
        await db.execute(
            select(NfCommand).where(NfCommand.action == "emitir_nf_upseller")
        )
    ).scalars().all()
    assert len(novos) == 1
    assert novos[0].status == "pending"
    assert novos[0].numeros == ["830001"]
    assert novos[0].faturador_id == fat_up_id
    assert novos[0].ads_power == "perfil-58"
    novos_bl = (
        await db.execute(
            select(NfCommand).where(NfCommand.action == "emitir_nf_bling")
        )
    ).scalars().all()
    assert len(novos_bl) == 1
    assert novos_bl[0].status == "pending"
    assert novos_bl[0].numeros == ["830002"]
    assert novos_bl[0].faturador_id == fat_bl_id
    assert novos_bl[0].ads_power == "perfil-58"
    fats = {
        f.pedido_bling: f
        for f in (await db.execute(select(NfFaturamento))).scalars().all()
    }
    assert fats["830001"].status_faturamento == "processando"  # segue em curso
    assert fats["830002"].status_faturamento == "processando"  # emissão em fila

    # 2ª passada: o comando novo cobre o 830001 → nada a fazer
    summary2 = await run_recuperar_nf()
    assert summary2["orfaos_faturamento"] == 0


@pytest.mark.asyncio
async def test_orfao_faturamento_sem_rastro_regera(
    db: AsyncSession, admin: User, monkeypatch: pytest.MonkeyPatch
):
    """Sem comando done que cubra o pedido: re-gera o import do zero; pedido
    que o gerador pular vira 'erro' (senão voltaria a cada tick)."""
    fat = await _seed_faturador(db, modo="upseller")
    db.add(NfFaturamento(pedido_bling="830001", status_faturamento="processando"))
    db.add(NfFaturamento(pedido_bling="830002", status_faturamento="processando"))
    await db.commit()
    fat_id = fat.id

    async def _fake_gerar(_session, numeros):
        assert sorted(numeros) == ["830001", "830002"]
        return nf_emissao_gerar.ResultadoPorFaturador(
            blocos=[
                nf_emissao_gerar.BlocoFaturador(
                    faturador_id=fat_id, numeros=["830001"],
                    planilha=b"xlsx", nome_arquivo="nf_avulsa_1.xlsx",
                )
            ],
            pulados=[
                nf_emissao_gerar.PedidoPulado(
                    numero="830002", motivo="não encontrado no davinci"
                )
            ],
        )

    monkeypatch.setattr(nf_emissao_gerar, "gerar_por_faturador", _fake_gerar)

    summary = await run_recuperar_nf()
    assert summary["orfaos_faturamento"] == 1

    db.expire_all()
    cmd = (
        await db.execute(
            select(NfCommand).where(NfCommand.action == "import_avulsa")
        )
    ).scalar_one()
    assert cmd.status == "pending"
    assert cmd.source == "auto"
    assert cmd.numeros == ["830001"]
    assert cmd.planilha == b"xlsx"
    fats = {
        f.pedido_bling: f
        for f in (await db.execute(select(NfFaturamento))).scalars().all()
    }
    assert fats["830001"].status_faturamento == "processando"
    assert fats["830002"].status_faturamento == "erro"
    assert fats["830002"].erro_faturamento == "não encontrado no davinci"


@pytest.mark.asyncio
async def test_orfao_etiqueta_reencadeia_captura(db: AsyncSession):
    """Etiqueta 'processando' sem comando ativo mas com a emissão done:
    re-cria `imprimir_etiqueta` no MESMO perfil AdsPower do elo anterior."""
    fat = await _seed_faturador(db, modo="upseller")
    fat_id = fat.id
    _seed_cmd(
        db, action="emitir_nf_upseller", status="done", numeros=["830001"],
        faturador_id=fat_id, ads_power="perfil-58",
        completed_at=datetime.now(UTC) - timedelta(hours=2),
    )
    db.add(NfFaturamento(pedido_bling="830001", status_etiqueta="processando"))
    await db.commit()

    summary = await run_recuperar_nf()
    assert summary["orfaos_etiqueta"] == 1

    db.expire_all()
    novo = (
        await db.execute(
            select(NfCommand).where(NfCommand.action == "imprimir_etiqueta")
        )
    ).scalar_one()
    assert novo.status == "pending"
    assert novo.numeros == ["830001"]
    assert novo.faturador_id == fat_id
    assert novo.ads_power == "perfil-58"

    # 2ª passada: o comando novo cobre o pedido → não duplica
    summary2 = await run_recuperar_nf()
    assert summary2["orfaos_etiqueta"] == 0
    db.expire_all()
    cmds = (
        await db.execute(
            select(NfCommand).where(NfCommand.action == "imprimir_etiqueta")
        )
    ).scalars().all()
    assert len(cmds) == 1


@pytest.mark.asyncio
async def test_orfao_etiqueta_sem_rastro_reimporta(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
):
    """Sem rastro do elo anterior: refaz a importação da etiqueta (fluxo ML,
    `import_etiqueta` com o AdsPower do cadastro Etiqueta)."""
    db.add(NfFaturamento(pedido_bling="830001", status_etiqueta="processando"))
    await db.commit()
    etiqueta_id = uuid.uuid4()

    async def _fake_gerar_etiqueta(_session, numeros):
        assert numeros == ["830001"]
        return nf_emissao_gerar.ResultadoEtiqueta(
            blocos=[
                nf_emissao_gerar.BlocoEtiqueta(
                    etiqueta_id=etiqueta_id, ads_power="58",
                    numeros=["830001"], planilha=b"xlsx",
                    nome_arquivo="nf_etiqueta_1.xlsx",
                )
            ],
            pulados=[],
        )

    monkeypatch.setattr(
        nf_emissao_gerar, "gerar_etiqueta_upseller", _fake_gerar_etiqueta
    )

    summary = await run_recuperar_nf()
    assert summary["orfaos_etiqueta"] == 1

    db.expire_all()
    cmd = (
        await db.execute(
            select(NfCommand).where(NfCommand.action == "import_etiqueta")
        )
    ).scalar_one()
    assert cmd.status == "pending"
    assert cmd.source == "auto"
    assert cmd.numeros == ["830001"]
    assert cmd.ads_power == "58"
    assert cmd.faturador_id is None
