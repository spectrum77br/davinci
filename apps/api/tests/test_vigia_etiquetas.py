"""Vigia de Etiquetas (robô da Ouvidoria) — o que este arquivo trava.

Vinicius, 14/10/2026: pedido que caiu e não teve etiqueta no prazo vira
ocorrência. O prazo segue o Horário Etiqueta da tela Lojas:

- conta sem horário (contínua — Shopee, TikTok, ML correios, Amazon…): 1h30
  depois que o pedido caiu no DaVinci, todo dia;
- conta com horário (ML agência 12:00): o primeiro horário depois que o pedido
  caiu + 1h30 — caiu depois do horário, vai pro dia seguinte;
- sábado: só o horário de sábado e só pedido dos estoques marcados; o resto e
  o domingo vão pro próximo dia útil;
- etiqueta no DaVinci (PDF com conteúdo, ou status_etiqueta ok) ou pedido que
  saiu de Em aberto fecha sozinho; a linha só com a NF não conta como etiqueta;
- loja sem cadastro de Etiqueta, arquivada, ou pedido fora de Em aberto nunca
  vira ocorrência;
- nasce silencioso, com o nome que aparece no painel.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    BlingOrder,
    Logistica,
    NfEtiqueta,
    NfEtiquetaArquivo,
    NfFaturamento,
    OuvidoriaOcorrencia,
    OuvidoriaRobo,
    StoreInfo,
    User,
    UserRole,
    UserStatus,
)
from app.services import ouvidoria as svc
from app.services import vigia_etiquetas as vigia

ROBO = vigia.ROBO
_TZ = ZoneInfo("America/Sao_Paulo")


def _brt(dia: int, hora: int, minuto: int = 0) -> datetime:
    """Outubro/2026 em Brasília: 14 = quarta, 16 = sexta, 17 = sábado,
    18 = domingo, 19 = segunda."""
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
    db: AsyncSession,
    admin: User,
    *,
    plataforma: str = "shopee",
    conta: str = "vortan",
    horarios: str | None = None,
    sabado: str | None = None,
    sabado_tags: str | None = None,
    com_etiqueta: bool = True,
    arquivada: bool = False,
) -> str:
    """Loja com (ou sem) cadastro de Etiqueta; devolve o bling_store_id."""
    etiqueta_id = None
    if com_etiqueta:
        e = NfEtiqueta(plataforma=plataforma, modo="upseller")
        db.add(e)
        await db.flush()
        etiqueta_id = e.id
    loja = f"{uuid.uuid4().int % 10**9}"
    db.add(
        StoreInfo(
            user_id=admin.id,
            platform=plataforma,
            account_name=conta,
            bling_store_id=loja,
            nf_etiqueta_id=etiqueta_id,
            etiqueta_horarios=horarios,
            etiqueta_sabado_horario=sabado,
            etiqueta_sabado_tags=sabado_tags,
            archived_at=datetime.now(UTC) if arquivada else None,
        )
    )
    await db.commit()
    return loja


async def _pedido(
    db: AsyncSession,
    loja: str,
    *,
    caiu: datetime,
    skus: tuple[str, ...] = ("b001.pi",),
    situacao: str = "6",
) -> str:
    numero = f"{uuid.uuid4().int % 10**6}"
    bling_id = uuid.uuid4().int % 10**12
    for i, sku in enumerate(skus):
        db.add(
            BlingOrder(
                bling_id=bling_id,
                numero=numero,
                numeroloja=f"MKT-{numero}",
                data=caiu,
                loja=loja,
                situacao=situacao,
                item_index=i,
                item_codigo=sku,
                created_at=caiu,
            )
        )
    await db.commit()
    return numero


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
    return await vigia.vigia_etiquetas_run(db, agora=agora.astimezone(UTC))


# ─── a régua (sem banco) ───────────────────────────────────────────────────


def test_horarios_de_le_a_tela_lojas():
    assert vigia.horarios_de("12:00, 14:30") == [time(12, 0), time(14, 30)]
    assert vigia.horarios_de(None) == [] and vigia.horarios_de(" ") == []
    assert vigia.horarios_de("06:00, lixo") == [time(6, 0)]


def test_continuo_conta_de_quando_caiu():
    i = vigia.inicio_da_contagem(
        _brt(18, 22), horarios=[], sabado=None, sai_no_sabado=False
    )
    assert i.em == _brt(18, 22) and i.regra.startswith("contínuo")


def test_com_horario_pega_o_proximo_horario():
    doze = [time(12, 0)]
    antes = vigia.inicio_da_contagem(_brt(14, 9), horarios=doze, sabado=None, sai_no_sabado=False)
    assert antes.em == _brt(14, 12) and antes.regra == "horário 12:00 + 1h30"
    depois = vigia.inicio_da_contagem(
        _brt(14, 12, 20), horarios=doze, sabado=None, sai_no_sabado=False
    )
    assert depois.em == _brt(15, 12)


def test_sabado_so_estoque_marcado_e_domingo_vai_pra_segunda():
    doze, seis = [time(12, 0)], time(6, 0)
    sai = vigia.inicio_da_contagem(_brt(16, 15), horarios=doze, sabado=seis, sai_no_sabado=True)
    assert sai.em == _brt(17, 6) and sai.regra == "sábado 06:00 + 1h30"
    nao_sai = vigia.inicio_da_contagem(
        _brt(16, 15), horarios=doze, sabado=seis, sai_no_sabado=False
    )
    assert nao_sai.em == _brt(19, 12)
    domingo = vigia.inicio_da_contagem(_brt(18, 10), horarios=doze, sabado=seis, sai_no_sabado=True)
    assert domingo.em == _brt(19, 12)
    # caiu no sábado depois das 06:00 → segunda
    sab_tarde = vigia.inicio_da_contagem(
        _brt(17, 9), horarios=doze, sabado=seis, sai_no_sabado=True
    )
    assert sab_tarde.em == _brt(19, 12)


def test_etapa_diz_onde_parou():
    assert vigia.etapa(None, None, None, None) == (
        "não passou pela fila da nota do DaVinci", vigia.ACAO_COBRAR
    )
    assert vigia.etapa("ok", None, "processando", None)[0] == "nota emitida, etiqueta não chegou"
    assert vigia.etapa("sem_estoque", None, None, None)[1] == vigia.ACAO_TRAVADO
    assert vigia.etapa("ok", None, "erro", "Erro de impressão")[0] == (
        "etiqueta deu erro: Erro de impressão"
    )


# ─── o robô ────────────────────────────────────────────────────────────────


async def test_continuo_abre_depois_de_1h30_e_fecha_quando_a_etiqueta_chega(db, admin):
    loja = await _loja(db, admin, plataforma="shopee", conta="vortan")
    numero = await _pedido(db, loja, caiu=_brt(14, 10))

    r = await _rodar(db, _brt(14, 11, 20))
    assert await _abertas(db) == [] and r["no_prazo"] == 1
    assert r["resumo"] == "1 pedido sem etiqueta · nenhum fora do prazo"

    r = await _rodar(db, _brt(14, 11, 40))
    (o,) = await _abertas(db)
    assert o.chave == f"pedido:{numero}" and o.pedido == numero
    assert o.conta == "Shopee vortan" and o.plataforma == "shopee"
    assert o.titulo == "Sem etiqueta — prazo 14/10 11:30 passou há 10 min"
    assert o.detalhe == (
        "Caiu 14/10 10:00 · contínuo: 1h30 depois que caiu · "
        "não passou pela fila da nota do DaVinci"
    )
    assert o.link == "/nf-faturamento" and o.dados["pedido_marketplace"] == f"MKT-{numero}"
    assert r["resumo"] == "1 pedido sem etiqueta · 1 fora do prazo"

    # só a NF chegou (linha nasce com o PDF vazio): continua sem etiqueta
    arq = NfEtiquetaArquivo(
        pedido_bling=numero, filename="x.pdf", content_type="application/pdf",
        size_bytes=0, blob=b"",
    )
    db.add(arq)
    await db.commit()
    await _rodar(db, _brt(14, 11, 50))
    assert len(await _abertas(db)) == 1

    arq.blob, arq.size_bytes = b"%PDF-1.4", 8
    await db.commit()
    r = await _rodar(db, _brt(14, 12))
    assert await _abertas(db) == [] and r["sumiram"] == 1


async def test_status_etiqueta_ok_ou_pedido_fora_de_em_aberto_nao_cobra(db, admin):
    loja = await _loja(db, admin)
    ok = await _pedido(db, loja, caiu=_brt(14, 6))
    db.add(NfFaturamento(pedido_bling=ok, status_faturamento="ok", status_etiqueta="ok"))
    await _pedido(db, loja, caiu=_brt(14, 6), situacao="21")
    await _pedido(db, loja, caiu=_brt(14, 6), situacao="83955")
    await db.commit()
    r = await _rodar(db, _brt(14, 12))
    assert await _abertas(db) == [] and r["pedidos_sem_etiqueta"] == 0


async def test_loja_sem_cadastro_de_etiqueta_ou_arquivada_fica_de_fora(db, admin):
    carrefour = await _loja(db, admin, plataforma="carrefour", com_etiqueta=False)
    arquivada = await _loja(db, admin, arquivada=True)
    await _pedido(db, carrefour, caiu=_brt(14, 6))
    await _pedido(db, arquivada, caiu=_brt(14, 6))
    r = await _rodar(db, _brt(14, 12))
    assert await _abertas(db) == [] and r["pedidos_sem_etiqueta"] == 0


async def test_ml_agencia_cobra_as_13h30_e_quem_caiu_depois_vai_pro_dia_seguinte(db, admin):
    loja = await _loja(db, admin, plataforma="ml", conta="marquezini", horarios="12:00")
    manha = await _pedido(db, loja, caiu=_brt(14, 9))
    tarde = await _pedido(db, loja, caiu=_brt(14, 12, 20))

    await _rodar(db, _brt(14, 13, 20))
    assert await _abertas(db) == []

    await _rodar(db, _brt(14, 13, 40))
    (o,) = await _abertas(db)
    assert o.pedido == manha and o.conta == "Mercado Livre marquezini"
    assert "horário 12:00 + 1h30" in o.detalhe and "prazo 14/10 13:30" in o.titulo

    await _rodar(db, _brt(15, 13, 40))
    assert sorted(x.pedido for x in await _abertas(db)) == sorted([manha, tarde])


async def test_sabado_06h_so_para_estoque_marcado(db, admin):
    loja = await _loja(
        db, admin, plataforma="ml", conta="kfa", horarios="12:00", sabado="06:00",
        sabado_tags="pi",
    )
    marcado = await _pedido(db, loja, caiu=_brt(16, 15), skus=("b001.pi",))
    misturado = await _pedido(db, loja, caiu=_brt(16, 15), skus=("b001.pi", "b002.sp"))

    r = await _rodar(db, _brt(17, 8))
    (o,) = await _abertas(db)
    assert o.pedido == marcado and "sábado 06:00 + 1h30" in o.detalhe
    assert r["no_prazo"] == 1

    # o do estoque fechado no sábado só é cobrado segunda 13:30
    await _rodar(db, _brt(19, 13, 40))
    assert sorted(x.pedido for x in await _abertas(db)) == sorted([marcado, misturado])


async def test_amazon_continua_e_etapa_travada(db, admin):
    loja = await _loja(db, admin, plataforma="amazon", conta="kia")
    numero = await _pedido(db, loja, caiu=_brt(13, 20))
    db.add(NfFaturamento(pedido_bling=numero, status_faturamento="sem_estoque"))
    await db.commit()
    await _rodar(db, _brt(14, 1, 44))
    (o,) = await _abertas(db)
    assert o.conta == "Amazon kia" and o.acao == vigia.ACAO_TRAVADO
    assert "contínuo" in o.detalhe and o.detalhe.endswith("sem estoque quando a nota ia sair")
    assert "passou há 4 h" in o.titulo


async def _envio_ml(db: AsyncSession, numero: str, *, status: str, sub: str, desde: datetime):
    db.add(
        Logistica(
            pedido_bling=numero,
            plataforma="Mercado Livre",
            meli_status={"order_status": "paid", "ship_status": status, "ship_substatus": sub},
            status_datas={"ship_status": {"em": desde.astimezone(UTC).isoformat()}},
            status_lido_em=datetime.now(UTC),
        )
    )
    await db.commit()


async def test_ml_programado_espera_o_ml_liberar_e_conta_da_liberacao(db, admin):
    """303265 (injox, 14/10): prazo de fabricação — o ML só solta a etiqueta
    em 23/10. Enquanto o envio está pending/buffered não cobra; quando vira
    ready_to_ship, o 1h30 conta da liberação."""
    loja = await _loja(db, admin, plataforma="ml", conta="injox")
    numero = await _pedido(db, loja, caiu=_brt(11, 21, 20))
    await _envio_ml(db, numero, status="pending", sub="buffered", desde=_brt(11, 21, 20))

    r = await _rodar(db, _brt(14, 15))
    assert await _abertas(db) == [] and r["aguardando_ml"] == 1
    assert r["resumo"] == "1 pedido sem etiqueta · 1 esperando o ML liberar · nenhum fora do prazo"

    lg = (await db.execute(select(Logistica).where(Logistica.pedido_bling == numero))).scalar_one()
    lg.meli_status = {"order_status": "paid", "ship_status": "ready_to_ship",
                      "ship_substatus": "invoice_pending"}
    lg.status_datas = {"ship_status": {"em": _brt(23, 8).astimezone(UTC).isoformat()}}
    await db.commit()
    await _rodar(db, _brt(23, 9, 20))
    assert await _abertas(db) == []
    await _rodar(db, _brt(23, 9, 40))
    (o,) = await _abertas(db)
    assert "ML liberou 23/10 08:00" in o.detalhe and "prazo 23/10 09:30" in o.titulo
    assert "contínuo: 1h30 depois que o ML liberou" in o.detalhe


async def test_ml_liberado_antes_de_cair_conta_de_quando_caiu(db, admin):
    loja = await _loja(db, admin, plataforma="ml", conta="kia")
    numero = await _pedido(db, loja, caiu=_brt(14, 10))
    await _envio_ml(
        db, numero, status="ready_to_ship", sub="invoice_pending", desde=_brt(14, 9, 59)
    )
    await _rodar(db, _brt(14, 11, 40))
    (o,) = await _abertas(db)
    assert "ML liberou" not in o.detalhe and "prazo 14/10 11:30" in o.titulo


async def test_pedido_fora_da_janela_nao_entra(db, admin):
    loja = await _loja(db, admin)
    await _pedido(db, loja, caiu=_brt(14, 10) - timedelta(days=8))
    r = await _rodar(db, _brt(14, 12))
    assert await _abertas(db) == [] and r["pedidos_sem_etiqueta"] == 0


async def test_sweep_e_serializado_pelo_advisory_lock(db):
    from app.services.advisory_lock import SYNC_NAMESPACE

    got = (
        await db.execute(
            text("SELECT pg_try_advisory_xact_lock(:ns, :key)"),
            {"ns": SYNC_NAMESPACE, "key": vigia._SWEEP_LOCK_KEY},
        )
    ).scalar()
    assert got
    assert await vigia.vigia_etiquetas_sweep() == {"skipped": "lock_busy"}
    await db.rollback()


async def test_nasce_silencioso_com_o_nome_do_painel(db):
    await svc.sincronizar_catalogo(db)
    await db.commit()
    robo = await db.get(OuvidoriaRobo, ROBO)
    assert robo.modo == "silencioso" and robo.nome == "Vigia de Etiquetas"
    assert robo.config == {"cadencia_min": 10, "prazo_min": 90, "janela_dias": 7}
