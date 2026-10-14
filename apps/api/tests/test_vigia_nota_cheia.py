"""Vigia de Nota Cheia (robô da Ouvidoria) — o que este arquivo trava.

Vinicius, 14/10/2026: "o que importa pra mim é saber se tudo foi emitido nota
cheia — 100% dos pedidos". A nota cheia é a coluna NF produto do painel Notas
fiscais (o mesmo cálculo da tela).

- os envios de um dia só são cobrados às 07:00 do dia seguinte;
- UMA ocorrência por dia, com a lista por conta — não uma por pedido; o
  número acompanha e fecha sozinha quando o último pedido ganha a nota;
- nota baixa (embalagem) não conta como nota cheia;
- etiqueta depois das 22h é do dia seguinte;
- loja sem faturador e pedido cancelado ficam de fora;
- "Tratado" segura 24 h e, se ainda faltar nota, volta só com o que falta;
- nasce silencioso, com o nome que aparece no painel.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    BlingOrder,
    NfEtiquetaArquivo,
    NfFaturador,
    NfNota,
    OuvidoriaOcorrencia,
    OuvidoriaRobo,
    StoreInfo,
    User,
    UserRole,
    UserStatus,
)
from app.services import ouvidoria as svc
from app.services import vigia_nota_cheia as vigia

ROBO = vigia.ROBO
_TZ = ZoneInfo("America/Sao_Paulo")


def _brt(dia: int, hora: int, minuto: int = 0) -> datetime:
    """Outubro/2026 em Brasília: 13 = terça, 14 = quarta."""
    return datetime(2026, 10, dia, hora, minuto, tzinfo=_TZ)


async def _limpar(db: AsyncSession) -> None:
    for tbl in ("ouvidoria_ocorrencias", "ouvidoria_rodadas", "ouvidoria_robos"):
        await db.execute(text(f"DELETE FROM {tbl}"))  # noqa: S608
    await db.commit()


@pytest_asyncio.fixture(autouse=True)
async def _limpa(db: AsyncSession):
    await _limpar(db)
    yield
    await _limpar(db)


@pytest_asyncio.fixture
async def admin(db: AsyncSession) -> User:
    email = f"adm-{uuid.uuid4().hex[:6]}@davinci-test.com"
    u = User(open_id=f"email:{email}", email=email, role=UserRole.ADMIN, status=UserStatus.ACTIVE)
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


async def _loja(
    db: AsyncSession, admin: User, *, plataforma: str = "shopee", conta: str = "barbosa",
    com_faturador: bool = True,
) -> str:
    faturador_id = None
    if com_faturador:
        f = NfFaturador(nome="upseller 2%", modo="upseller", percentual=2)
        db.add(f)
        await db.flush()
        faturador_id = f.id
    loja = f"{uuid.uuid4().int % 10**9}"
    db.add(
        StoreInfo(
            user_id=admin.id, platform=plataforma, account_name=conta,
            bling_store_id=loja, nf_faturador_id=faturador_id, cnpj="11111111000111",
        )
    )
    await db.commit()
    return loja


async def _envio(
    db: AsyncSession, loja: str, *, etiqueta: datetime, situacao: str = "15",
    total: float = 200.0,
) -> str:
    """Pedido enviado: etiqueta no DaVinci e data de envio no Bling."""
    numero = f"{uuid.uuid4().int % 10**6}"
    db.add(
        BlingOrder(
            bling_id=uuid.uuid4().int % 10**12, numero=numero, numeroloja=f"MKT-{numero}",
            loja=loja, situacao=situacao, item_index=0, item_codigo="b001.pi",
            total=total, documento_destinatario="12345678900",
            em_andamento_data=etiqueta.astimezone(_TZ).date(), data=etiqueta,
        )
    )
    db.add(
        NfEtiquetaArquivo(
            pedido_bling=numero, filename="e.pdf", content_type="application/pdf",
            size_bytes=4, blob=b"%PDF", created_at=etiqueta,
        )
    )
    await db.commit()
    return numero


async def _nota(db: AsyncSession, numero: str, *, valor: float, em: datetime) -> None:
    """NF que o coletor trouxe em XML, já casada com o pedido."""
    db.add(
        NfNota(
            chave=f"{uuid.uuid4().int % 10**44:044d}", pedido_bling=numero,
            numero=str(uuid.uuid4().int % 10**6), valor=valor, data_emissao=em,
            situacao="100", emitente_cnpj="22222222000122", destinatario_doc="12345678900",
            xml=b"<nfe/>",
        )
    )
    await db.commit()


async def _abertas(db: AsyncSession) -> list[OuvidoriaOcorrencia]:
    return list(
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


async def _rodar(db: AsyncSession, agora: datetime) -> dict:
    return await vigia.vigia_nota_cheia_run(db, agora=agora.astimezone(UTC))


# ─── a régua (sem banco) ───────────────────────────────────────────────────


def test_etiqueta_depois_das_22h_e_do_dia_seguinte():
    assert vigia.dia_do_envio(_brt(13, 21, 59), None, corte_hora=22) == date(2026, 10, 13)
    assert vigia.dia_do_envio(_brt(13, 22, 0), None, corte_hora=22) == date(2026, 10, 14)
    assert vigia.dia_do_envio(None, date(2026, 10, 13), corte_hora=22) == date(2026, 10, 13)
    assert vigia.conferencia_do_dia(date(2026, 10, 13), hora=7) == _brt(14, 7)


# ─── o robô ────────────────────────────────────────────────────────────────


async def test_um_dia_vira_uma_ocorrencia_e_fecha_quando_todos_tem_nota(db, admin):
    loja = await _loja(db, admin, conta="barbosa")
    com = await _envio(db, loja, etiqueta=_brt(13, 10))
    await _nota(db, com, valor=200, em=_brt(13, 22, 40))
    sem1 = await _envio(db, loja, etiqueta=_brt(13, 11))
    sem2 = await _envio(db, loja, etiqueta=_brt(13, 15))
    await _nota(db, sem2, valor=4, em=_brt(13, 15, 1))  # só a nota baixa

    r = await _rodar(db, _brt(14, 6, 59))
    assert await _abertas(db) == [] and r["envios"] == 0

    r = await _rodar(db, _brt(14, 7, 8))
    (o,) = await _abertas(db)
    assert o.chave == "dia:2026-10-13"
    assert o.titulo == "Envios de 13/10 (ter) — 2 de 3 pedidos sem nota cheia"
    assert o.detalhe == f"Shopee barbosa (2): {', '.join(sorted([sem1, sem2]))}"
    assert o.link == "/notas-fiscais?de=2026-10-13&ate=2026-10-14&nf=sem_produto"
    assert o.pedido is None and o.dados["sem_nota"] == 2
    assert "1 dia com pedido sem nota cheia (2)" in r["resumo"]

    await _nota(db, sem1, valor=200, em=_brt(14, 9))
    await _rodar(db, _brt(14, 10, 8))
    (o,) = await _abertas(db)
    assert o.titulo.endswith("1 de 3 pedidos sem nota cheia") and o.detalhe.endswith(sem2)

    await _nota(db, sem2, valor=200, em=_brt(14, 11))
    r = await _rodar(db, _brt(14, 12, 8))
    assert await _abertas(db) == [] and r["sumiram"] == 1
    assert r["resumo"].endswith("todos com nota cheia")


async def test_lista_por_conta_e_etiqueta_tarde_vai_pro_outro_dia(db, admin):
    barbosa = await _loja(db, admin, conta="barbosa")
    temu = await _loja(db, admin, plataforma="temu", conta="eron")
    a = await _envio(db, barbosa, etiqueta=_brt(13, 9))
    b = await _envio(db, temu, etiqueta=_brt(13, 9))
    c = await _envio(db, temu, etiqueta=_brt(13, 12))
    await _envio(db, barbosa, etiqueta=_brt(13, 22, 30))  # lote de 14/10

    await _rodar(db, _brt(14, 7, 8))
    (o,) = await _abertas(db)
    assert o.titulo == "Envios de 13/10 (ter) — 3 de 3 pedidos sem nota cheia"
    assert o.detalhe.split("\n") == [
        f"Temu eron (2): {', '.join(sorted([b, c]))}",
        f"Shopee barbosa (1): {a}",
    ]


async def test_loja_sem_faturador_e_cancelado_ficam_de_fora(db, admin):
    sem_fat = await _loja(db, admin, conta="makisa", com_faturador=False)
    loja = await _loja(db, admin)
    await _envio(db, sem_fat, etiqueta=_brt(13, 9))
    await _envio(db, loja, etiqueta=_brt(13, 9), situacao="12")
    await _envio(db, loja, etiqueta=_brt(13, 9), situacao="83955")
    r = await _rodar(db, _brt(14, 7, 8))
    assert await _abertas(db) == [] and r["envios"] == 0


async def test_tratado_segura_24h_e_volta_com_o_que_falta(db, admin):
    loja = await _loja(db, admin)
    await _envio(db, loja, etiqueta=_brt(13, 9))
    await _envio(db, loja, etiqueta=_brt(13, 10))
    await _rodar(db, _brt(14, 7, 8))
    (o,) = await _abertas(db)
    await svc.tratar(
        db, o.id, usuario="cairo sa", fechamento="tratada", agora=_brt(14, 8).astimezone(UTC)
    )
    await db.commit()

    await _rodar(db, _brt(14, 9, 8))
    assert await _abertas(db) == []
    await _rodar(db, _brt(15, 9, 8))
    (o2,) = await _abertas(db)
    assert o2.id != o.id and o2.titulo.endswith("2 de 2 pedidos sem nota cheia")


async def test_sweep_e_serializado_pelo_advisory_lock(db):
    from app.services.advisory_lock import SYNC_NAMESPACE

    got = (
        await db.execute(
            text("SELECT pg_try_advisory_xact_lock(:ns, :key)"),
            {"ns": SYNC_NAMESPACE, "key": vigia._SWEEP_LOCK_KEY},
        )
    ).scalar()
    assert got
    assert await vigia.vigia_nota_cheia_sweep() == {"skipped": "lock_busy"}
    await db.rollback()


async def test_nasce_silencioso_com_o_nome_do_painel(db):
    await svc.sincronizar_catalogo(db)
    await db.commit()
    robo = await db.get(OuvidoriaRobo, ROBO)
    assert robo.modo == "silencioso" and robo.nome == "Vigia de Nota Cheia"
    assert robo.config == {
        "cadencia_min": 60, "hora_conferencia": 7, "corte_hora": 22, "janela_dias": 7,
    }
