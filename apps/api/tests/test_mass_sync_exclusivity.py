"""ITEM 1 — exclusividade "só outro massa": enquanto um Sincronizar Todos ou
Vincular Automático está PENDING/RUNNING, um NOVO massa é recusado com 409. O
sync individual por SKU NÃO passa por esse gate (continua livre).

Também cobre o repoint da fila: o enqueue do auto_link vai pra fila de sync
(`get_arq_sync_pool`), não pra default.
"""
from __future__ import annotations

import uuid
from collections.abc import Callable
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    BackgroundJob,
    BackgroundJobStatus,
    BackgroundJobType,
    Integration,
    IntegrationPlatform,
    User,
    UserRole,
)


async def _conta(
    db: AsyncSession,
    dono: User,
    plataforma: IntegrationPlatform,
    *,
    arquivada: bool = False,
    nome: str | None = None,
) -> Integration:
    from datetime import UTC, datetime

    integ = Integration(
        user_id=dono.id,
        platform=plataforma,
        name=nome or f"{plataforma.value}-{uuid.uuid4().hex[:6]}",
        credentials=b"x",
        archived_at=datetime.now(UTC) if arquivada else None,
    )
    db.add(integ)
    await db.commit()
    await db.refresh(integ)
    return integ


async def _running_mass_job(db: AsyncSession, user: User, type_: BackgroundJobType):
    job = BackgroundJob(
        type=type_,
        status=BackgroundJobStatus.RUNNING,
        created_by=user.id,
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)
    return job


@pytest.mark.asyncio
async def test_sync_all_recusa_409_quando_ha_massa_ativo(
    client: AsyncClient,
    db: AsyncSession,
    make_user: Callable,
    auth_as: Callable[[User | None], None],
):
    admin = await make_user(role=UserRole.ADMIN)
    auth_as(admin)
    await _running_mass_job(db, admin, BackgroundJobType.AUTO_LINK)

    r = await client.post("/api/jobs/sync-all", json={})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "sync_already_running"


@pytest.mark.asyncio
async def test_auto_link_recusa_409_quando_ha_massa_ativo(
    client: AsyncClient,
    db: AsyncSession,
    make_user: Callable,
    auth_as: Callable[[User | None], None],
):
    admin = await make_user(role=UserRole.ADMIN)
    auth_as(admin)
    await _running_mass_job(db, admin, BackgroundJobType.SYNC_ALL)

    r = await client.post("/api/jobs/auto-link", json={})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "sync_already_running"


@pytest.mark.asyncio
async def test_auto_link_sem_massa_enfileira_na_fila_de_sync(
    client: AsyncClient,
    db: AsyncSession,
    make_user: Callable,
    auth_as: Callable[[User | None], None],
):
    """Sem massa ativo: o enqueue vai pra fila de sync (get_arq_sync_pool) com o
    job `auto_link_run`, e o endpoint devolve 201. Desde 17/11/2026 a lista de
    contas é montada no servidor: precisa de ao menos 1 conta de marketplace."""
    admin = await make_user(role=UserRole.ADMIN)
    auth_as(admin)
    integ = await _conta(db, admin, IntegrationPlatform.ML)

    fake_pool = AsyncMock()
    fake_pool.enqueue_job = AsyncMock(return_value=type("J", (), {"job_id": "x"}))
    with patch("app.routers.jobs.get_arq_sync_pool", return_value=fake_pool):
        r = await client.post("/api/jobs/auto-link", json={})

    assert r.status_code == 201
    fake_pool.enqueue_job.assert_awaited_once()
    assert fake_pool.enqueue_job.await_args.args[0] == "auto_link_run"
    assert fake_pool.enqueue_job.await_args.args[2] == [str(integ.id)]


@pytest.mark.asyncio
async def test_sync_all_encaminha_integration_ids_pro_worker(
    client: AsyncClient,
    make_user: Callable,
    auth_as: Callable[[User | None], None],
):
    """Selecionar contas no dialog "Sincronizar Todos" tem que ESCOPAR o job:
    `integration_ids` é encaminhado como 6º arg pro worker `sync_all_run`.
    Antes ele ficava só no payload e era ignorado — o job sincronizava TODAS
    as contas (bug "aparece 30 mil links mesmo escolhendo uma conta")."""
    admin = await make_user(role=UserRole.ADMIN)
    auth_as(admin)
    iid = str(uuid.uuid4())

    fake_pool = AsyncMock()
    fake_pool.enqueue_job = AsyncMock(return_value=type("J", (), {"job_id": "x"}))
    with patch("app.routers.sync.get_arq_sync_pool", return_value=fake_pool):
        r = await client.post("/api/jobs/sync-all", json={"integration_ids": [iid]})

    assert r.status_code == 201
    args = fake_pool.enqueue_job.await_args.args
    assert args[0] == "sync_all_run"
    # (fn, job_id, user_id, product_ids, include_all_stock, integration_ids)
    assert args[5] == [iid]


@pytest.mark.asyncio
async def test_sync_all_sem_selecao_nao_escopa(
    client: AsyncClient,
    make_user: Callable,
    auth_as: Callable[[User | None], None],
):
    """Sem `integration_ids` (sync global), o 6º arg é None — o worker cai no
    caminho não-escopado e conta/sincroniza todos os links."""
    admin = await make_user(role=UserRole.ADMIN)
    auth_as(admin)

    fake_pool = AsyncMock()
    fake_pool.enqueue_job = AsyncMock(return_value=type("J", (), {"job_id": "x"}))
    with patch("app.routers.sync.get_arq_sync_pool", return_value=fake_pool):
        r = await client.post("/api/jobs/sync-all", json={})

    assert r.status_code == 201
    args = fake_pool.enqueue_job.await_args.args
    assert args[5] is None
    # `force` (7º arg) default False quando o body não pede.
    assert args[6] is False


@pytest.mark.asyncio
async def test_sync_all_encaminha_force_pro_worker(
    client: AsyncClient,
    make_user: Callable,
    auth_as: Callable[[User | None], None],
):
    """Marcar "Forçar" no dialog "Sincronizar Todos" tem que encaminhar
    `force=True` como 7º arg pro worker `sync_all_run` — só assim a massa fura
    as travas do marketplace e reativa anúncios que o ML pausou por estoque 0."""
    admin = await make_user(role=UserRole.ADMIN)
    auth_as(admin)

    fake_pool = AsyncMock()
    fake_pool.enqueue_job = AsyncMock(return_value=type("J", (), {"job_id": "x"}))
    with patch("app.routers.sync.get_arq_sync_pool", return_value=fake_pool):
        r = await client.post("/api/jobs/sync-all", json={"force": True})

    assert r.status_code == 201
    args = fake_pool.enqueue_job.await_args.args
    assert args[0] == "sync_all_run"
    # (fn, job_id, user_id, product_ids, include_all_stock, integration_ids, force)
    assert args[6] is True


@pytest.mark.asyncio
async def test_sync_active_endpoint_reflete_massa(
    client: AsyncClient,
    db: AsyncSession,
    make_user: Callable,
    auth_as: Callable[[User | None], None],
):
    admin = await make_user(role=UserRole.ADMIN)
    auth_as(admin)

    r = await client.get("/api/sync/active")
    assert r.status_code == 200
    assert r.json()["active"] is False

    await _running_mass_job(db, admin, BackgroundJobType.SYNC_ALL)
    r = await client.get("/api/sync/active")
    assert r.json()["active"] is True
    assert r.json()["type"] == "sync_all"


# ───────────── 17/11/2026: trava GLOBAL (Atualizar / Vincular / Sincronizar) ─────


def _pool_falso() -> AsyncMock:
    pool = AsyncMock()
    pool.enqueue_job = AsyncMock(return_value=type("J", (), {"job_id": "x"}))
    return pool


async def _job_massa(
    db: AsyncSession,
    dono: User,
    tipo: BackgroundJobType,
    *,
    status: BackgroundJobStatus = BackgroundJobStatus.RUNNING,
    gatilho: str | None = None,
    sinal_ha_min: float | None = None,
    criado_ha_min: float | None = None,
) -> BackgroundJob:
    from datetime import UTC, datetime, timedelta

    agora = datetime.now(UTC)
    job = BackgroundJob(
        type=tipo,
        status=status,
        created_by=dono.id,
        payload={"trigger": gatilho} if gatilho else {},
        started_at=agora if status == BackgroundJobStatus.RUNNING else None,
    )
    if sinal_ha_min is not None:
        job.started_at = agora - timedelta(minutes=sinal_ha_min + 5)
        job.last_heartbeat_at = agora - timedelta(minutes=sinal_ha_min)
    db.add(job)
    await db.commit()
    if criado_ha_min is not None:
        await db.execute(
            BackgroundJob.__table__.update()
            .where(BackgroundJob.id == job.id)
            .values(created_at=agora - timedelta(minutes=criado_ha_min))
        )
        await db.commit()
    await db.refresh(job)
    return job


async def _com_nome(db: AsyncSession, u: User, nome: str, **kw) -> User:
    # make_user (conftest) não recebe nome nem equipe: grava depois.
    u.name = nome
    for k, v in kw.items():
        setattr(u, k, v)
    await db.commit()
    await db.refresh(u)
    return u


async def _dono(db: AsyncSession, make_user: Callable) -> User:
    return await _com_nome(db, await make_user(role=UserRole.ADMIN), "Eduardo")


async def _pessoa_com_edicao(db: AsyncSession, make_user: Callable, **kw) -> User:
    u = await make_user(
        role=UserRole.USER,
        permissions={"produtos": {"view": True, "edit": True}},
    )
    return await _com_nome(db, u, "Vendedor Teste", **kw)


@pytest.mark.asyncio
async def test_massa_de_outro_usuario_da_409_no_sincronizar_com_quem_e_desde(
    client: AsyncClient,
    db: AsyncSession,
    make_user: Callable,
    auth_as: Callable[[User | None], None],
):
    """A varredura das 10h roda em nome do dono: antes, outro usuário podia
    clicar Sincronizar ao mesmo tempo. Agora a trava é global e o 409 diz o
    que está rodando, quem disparou e desde quando."""
    dono = await _dono(db, make_user)
    job = await _job_massa(
        db, dono, BackgroundJobType.AUTO_LINK, gatilho="varredura_diaria"
    )
    outro = await _pessoa_com_edicao(db, make_user)
    auth_as(outro)

    with patch("app.routers.sync.get_arq_sync_pool", return_value=_pool_falso()) as pool:
        r = await client.post("/api/jobs/sync-all", json={})
    assert r.status_code == 409
    d = r.json()["detail"]
    assert d["code"] == "sync_already_running"
    assert d["job_id"] == str(job.id)
    assert d["tipo"] == "auto_link"
    assert d["gatilho"] == "varredura_diaria"
    assert d["quem"] == "Eduardo"
    assert d["desde"]
    assert d["rotulo"] == "a varredura automática de vínculos"
    pool.assert_not_called()


@pytest.mark.asyncio
async def test_massa_de_outro_usuario_da_409_no_vincular(
    client: AsyncClient,
    db: AsyncSession,
    make_user: Callable,
    auth_as: Callable[[User | None], None],
):
    dono = await _dono(db, make_user)
    await _job_massa(db, dono, BackgroundJobType.SYNC_ALL, gatilho="daily_sync")
    outro = await _pessoa_com_edicao(db, make_user)
    await _conta(db, outro, IntegrationPlatform.ML)
    auth_as(outro)

    r = await client.post("/api/jobs/auto-link", json={})
    assert r.status_code == 409
    d = r.json()["detail"]
    assert d["code"] == "sync_already_running"
    assert (d["tipo"], d["gatilho"], d["quem"]) == ("sync_all", "daily_sync", "Eduardo")


@pytest.mark.asyncio
async def test_atualizar_import_listings_conta_como_massa(
    client: AsyncClient,
    db: AsyncSession,
    make_user: Callable,
    auth_as: Callable[[User | None], None],
):
    """O ATUALIZAR (IMPORT_LISTINGS, trigger 'botao_atualizar') segura os
    outros dois botões, de qualquer pessoa."""
    dono = await _dono(db, make_user)
    await _job_massa(
        db, dono, BackgroundJobType.IMPORT_LISTINGS, gatilho="botao_atualizar"
    )
    outro = await _pessoa_com_edicao(db, make_user)
    await _conta(db, outro, IntegrationPlatform.SHOPEE)
    auth_as(outro)

    r1 = await client.post("/api/jobs/sync-all", json={})
    r2 = await client.post("/api/jobs/auto-link", json={})
    assert r1.status_code == r2.status_code == 409
    assert r1.json()["detail"]["tipo"] == "import_listings"
    assert r1.json()["detail"]["rotulo"] == "o Atualizar"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tipo",
    [
        BackgroundJobType.SYNC_ALL,
        BackgroundJobType.AUTO_LINK,
        BackgroundJobType.IMPORT_LISTINGS,
    ],
)
async def test_massa_calada_ha_mais_de_15_min_nao_trava(
    client: AsyncClient,
    db: AsyncSession,
    make_user: Callable,
    auth_as: Callable[[User | None], None],
    tipo: BackgroundJobType,
):
    """RUNNING sem sinal de vida há mais de 15 min = worker morreu: não trava
    mais ninguém (vale para os 3 tipos; o Vincular e o Atualizar dão sinal a
    cada 60 s pelo `sinal_de_vida`)."""
    dono = await make_user(role=UserRole.ADMIN)
    await _job_massa(db, dono, tipo, sinal_ha_min=20)
    auth_as(dono)
    with patch("app.routers.sync.get_arq_sync_pool", return_value=_pool_falso()):
        r = await client.post("/api/jobs/sync-all", json={})
    assert r.status_code == 201


@pytest.mark.asyncio
async def test_massa_com_sinal_recente_trava(
    client: AsyncClient,
    db: AsyncSession,
    make_user: Callable,
    auth_as: Callable[[User | None], None],
):
    dono = await make_user(role=UserRole.ADMIN)
    await _job_massa(db, dono, BackgroundJobType.AUTO_LINK, sinal_ha_min=2)
    auth_as(dono)
    r = await client.post("/api/jobs/sync-all", json={})
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_pending_so_trava_enquanto_e_recente(
    client: AsyncClient,
    db: AsyncSession,
    make_user: Callable,
    auth_as: Callable[[User | None], None],
):
    """PENDING de 1h é job que o worker nunca vai pegar (ex.: API publicada
    antes do worker conhecer a função): não pode prender os botões de todos."""
    dono = await make_user(role=UserRole.ADMIN)
    job = await _job_massa(
        db,
        dono,
        BackgroundJobType.SYNC_ALL,
        status=BackgroundJobStatus.PENDING,
        criado_ha_min=5,
    )
    auth_as(dono)
    r = await client.post("/api/jobs/sync-all", json={})
    assert r.status_code == 409

    from datetime import timedelta

    await db.execute(
        BackgroundJob.__table__.update()
        .where(BackgroundJob.id == job.id)
        .values(created_at=job.created_at - timedelta(minutes=60))
    )
    await db.commit()
    with patch("app.routers.sync.get_arq_sync_pool", return_value=_pool_falso()):
        r = await client.post("/api/jobs/sync-all", json={})
    assert r.status_code == 201


@pytest.mark.asyncio
async def test_sync_active_e_global_com_rotulo(
    client: AsyncClient,
    db: AsyncSession,
    make_user: Callable,
    auth_as: Callable[[User | None], None],
):
    """/api/sync/active mostra a massa de QUALQUER pessoa: a tela de quem não
    disparou também desabilita os botões e diz quem e desde quando."""
    dono = await _dono(db, make_user)
    outro = await _pessoa_com_edicao(db, make_user)
    auth_as(outro)

    r = await client.get("/api/sync/active")
    assert r.status_code == 200
    assert r.json()["active"] is False
    assert r.json()["job_id"] is None

    job = await _job_massa(db, dono, BackgroundJobType.SYNC_ALL, gatilho="daily_sync")
    r = await client.get("/api/sync/active")
    corpo = r.json()
    assert corpo["active"] is True
    assert corpo["job_id"] == str(job.id)
    assert corpo["type"] == "sync_all"
    assert corpo["status"] == "running"
    assert corpo["gatilho"] == "daily_sync"
    assert corpo["quem"] == "Eduardo"
    assert corpo["desde"]
    assert corpo["rotulo"] == "a sincronização automática da noite"


@pytest.mark.asyncio
async def test_dois_disparos_ao_mesmo_tempo_o_segundo_leva_409(
    client: AsyncClient,
    make_user: Callable,
    auth_as: Callable[[User | None], None],
):
    """Enquanto um POST de massa segura a trava 'mass' (entre conferir e gravar
    a linha PENDING), o outro clique recebe 409 na hora — não fica esperando
    preso numa conexão da API."""
    from app.db import SessionLocal
    from app.services.advisory_lock import travar_disparo_de_massa

    dono = await make_user(role=UserRole.ADMIN)
    auth_as(dono)
    async with SessionLocal() as outro_clique:
        assert await travar_disparo_de_massa(outro_clique) is True
        with patch("app.routers.sync.get_arq_sync_pool", return_value=_pool_falso()) as p:
            r = await client.post("/api/jobs/sync-all", json={})
        assert r.status_code == 409
        assert r.json()["detail"]["code"] == "sync_already_running"
        assert r.json()["detail"]["rotulo"] == "outro disparo em andamento"
        p.assert_not_called()
        await outro_clique.rollback()
    with patch("app.routers.sync.get_arq_sync_pool", return_value=_pool_falso()):
        r = await client.post("/api/jobs/sync-all", json={})
    assert r.status_code == 201


# ───────────── Vincular: contas montadas no servidor ─────────────────────────


@pytest.mark.asyncio
async def test_vincular_sem_conta_da_400(
    client: AsyncClient,
    db: AsyncSession,
    make_user: Callable,
    auth_as: Callable[[User | None], None],
):
    """Sem conta de marketplace no escopo: 400 sem_contas — nunca o antigo
    'integration_ids vazio = todas as integrações'."""
    admin = await make_user(role=UserRole.ADMIN)
    await _conta(db, admin, IntegrationPlatform.BLING)
    await _conta(db, admin, IntegrationPlatform.ML, arquivada=True)
    auth_as(admin)
    with patch("app.routers.jobs.get_arq_sync_pool", return_value=_pool_falso()) as p:
        r = await client.post("/api/jobs/auto-link", json={})
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "sem_contas"
    p.assert_not_called()


@pytest.mark.asyncio
async def test_vincular_monta_as_contas_no_servidor(
    client: AsyncClient,
    db: AsyncSession,
    make_user: Callable,
    auth_as: Callable[[User | None], None],
):
    """Entram ML, Shopee, TikTok, Amazon e Magalu não arquivadas. Ficam fora:
    arquivada, Bling e Carrefour (sem leitor de vínculo). O corpo só estreita
    (o que não é do escopo é ignorado). O job leva trigger 'botao_vincular'."""
    admin = await make_user(role=UserRole.ADMIN)
    ml = await _conta(db, admin, IntegrationPlatform.ML)
    shopee = await _conta(db, admin, IntegrationPlatform.SHOPEE)
    tiktok = await _conta(db, admin, IntegrationPlatform.TIKTOK)
    amazon = await _conta(db, admin, IntegrationPlatform.AMAZON)
    magalu = await _conta(db, admin, IntegrationPlatform.MAGALU)
    arquivada = await _conta(db, admin, IntegrationPlatform.ML, arquivada=True)
    await _conta(db, admin, IntegrationPlatform.BLING)
    await _conta(db, admin, IntegrationPlatform.CARREFOUR)
    auth_as(admin)

    pool = _pool_falso()
    with patch("app.routers.jobs.get_arq_sync_pool", return_value=pool):
        r = await client.post("/api/jobs/auto-link", json={})
    assert r.status_code == 201
    args = pool.enqueue_job.await_args.args
    esperadas = {str(i.id) for i in (ml, shopee, tiktok, amazon, magalu)}
    assert set(args[2]) == esperadas and len(args[2]) == 5
    job = await db.get(BackgroundJob, uuid.UUID(r.json()["job_id"]))
    assert job.payload["trigger"] == "botao_vincular"
    assert set(job.payload["integration_ids"]) == esperadas

    # O corpo só estreita: a arquivada e um id qualquer são ignorados.
    await db.execute(BackgroundJob.__table__.delete())
    await db.commit()
    pool = _pool_falso()
    with patch("app.routers.jobs.get_arq_sync_pool", return_value=pool):
        r = await client.post(
            "/api/jobs/auto-link",
            json={"integration_ids": [str(shopee.id), str(arquivada.id), str(uuid.uuid4())]},
        )
    assert r.status_code == 201
    assert pool.enqueue_job.await_args.args[2] == [str(shopee.id)]

    # Só ids fora do escopo → 400.
    await db.execute(BackgroundJob.__table__.delete())
    await db.commit()
    r = await client.post(
        "/api/jobs/auto-link", json={"integration_ids": [str(arquivada.id)]}
    )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "sem_contas"


async def _lojas_de_equipe(db: AsyncSession, dono: User) -> tuple[Integration, Integration]:
    """Duas contas ML: uma da equipe Londres (101), outra da Israel (102)."""
    from app.models.pricing import StoreInfo

    londres = await _conta(db, dono, IntegrationPlatform.ML, nome="ml-londres")
    israel = await _conta(db, dono, IntegrationPlatform.ML, nome="ml-israel")
    for integ, equipe in ((londres, 101), (israel, 102)):
        db.add(
            StoreInfo(
                user_id=dono.id,
                platform="ml",
                account_name=integ.name,
                integration_id=integ.id,
                sales_team=equipe,
            )
        )
    await db.commit()
    return londres, israel


@pytest.mark.asyncio
async def test_vincular_de_usuario_de_equipe_so_le_as_contas_da_equipe(
    client: AsyncClient,
    db: AsyncSession,
    make_user: Callable,
    auth_as: Callable[[User | None], None],
):
    dono = await make_user(role=UserRole.ADMIN)
    londres, israel = await _lojas_de_equipe(db, dono)
    vendedor = await _pessoa_com_edicao(db, make_user, sales_teams=[101])
    auth_as(vendedor)

    pool = _pool_falso()
    with patch("app.routers.jobs.get_arq_sync_pool", return_value=pool):
        r = await client.post("/api/jobs/auto-link", json={})
    assert r.status_code == 201
    assert pool.enqueue_job.await_args.args[2] == [str(londres.id)]

    await db.execute(BackgroundJob.__table__.delete())
    await db.commit()
    r = await client.post("/api/jobs/auto-link", json={"integration_ids": [str(israel.id)]})
    assert r.status_code == 400


# ───────────── Sincronizar: escopo de equipe no servidor ─────────────────────


@pytest.mark.asyncio
async def test_sincronizar_de_usuario_de_equipe_fica_nas_contas_da_equipe(
    client: AsyncClient,
    db: AsyncSession,
    make_user: Callable,
    auth_as: Callable[[User | None], None],
):
    """Antes o servidor aceitava null (= todas as lojas) de qualquer um: o
    'Sincronizar Todos' do vendedor de uma equipe enviava para as lojas das
    outras. Agora null vira o escopo dele e o corpo é cortado pelo escopo."""
    dono = await make_user(role=UserRole.ADMIN)
    londres, israel = await _lojas_de_equipe(db, dono)
    vendedor = await _pessoa_com_edicao(db, make_user, sales_teams=[101])
    auth_as(vendedor)

    pool = _pool_falso()
    with patch("app.routers.sync.get_arq_sync_pool", return_value=pool):
        r = await client.post("/api/jobs/sync-all", json={})
    assert r.status_code == 201
    # (fn, job_id, user_id, product_ids, include_all_stock, integration_ids, force)
    assert pool.enqueue_job.await_args.args[5] == [str(londres.id)]
    job = await db.get(BackgroundJob, uuid.UUID(r.json()["job_id"]))
    assert job.payload["integration_ids"] == [str(londres.id)]

    await db.execute(BackgroundJob.__table__.delete())
    await db.commit()
    pool = _pool_falso()
    with patch("app.routers.sync.get_arq_sync_pool", return_value=pool):
        r = await client.post(
            "/api/jobs/sync-all",
            json={"integration_ids": [str(londres.id), str(israel.id)]},
        )
    assert r.status_code == 201
    assert pool.enqueue_job.await_args.args[5] == [str(londres.id)]

    await db.execute(BackgroundJob.__table__.delete())
    await db.commit()
    r = await client.post("/api/jobs/sync-all", json={"integration_ids": [str(israel.id)]})
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "sem_contas"


@pytest.mark.asyncio
async def test_sincronizar_sem_permissao_da_403(
    client: AsyncClient,
    make_user: Callable,
    auth_as: Callable[[User | None], None],
):
    sem = await make_user(role=UserRole.USER, permissions={"produtos": {"view": True}})
    auth_as(sem)
    assert (await client.post("/api/jobs/sync-all", json={})).status_code == 403
    assert (await client.post("/api/jobs/auto-link", json={})).status_code == 403


# ───────────── ciclo de vida do job do Vincular (worker / run_auto_link) ─────


async def _job_vincular(db: AsyncSession, dono: User, **kw) -> BackgroundJob:
    job = BackgroundJob(
        type=BackgroundJobType.AUTO_LINK,
        status=BackgroundJobStatus.PENDING,
        created_by=dono.id,
        payload={"trigger": "botao_vincular"},
        **kw,
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)
    return job


@pytest.mark.asyncio
async def test_auto_link_run_com_lista_vazia_falha_sem_contas(
    db: AsyncSession, make_user: Callable, monkeypatch
):
    """[] (lista vazia) é diferente de None: nunca cai no 'todas as contas'."""
    from app import worker
    from app.services import auto_link as al

    dono = await make_user(role=UserRole.ADMIN)
    await _conta(db, dono, IntegrationPlatform.ML)
    job = await _job_vincular(db, dono)
    chamado = AsyncMock()
    monkeypatch.setattr(al, "_link_with_retry", chamado)

    await worker.auto_link_run({}, str(job.id), [])
    await db.refresh(job)
    assert job.status == BackgroundJobStatus.FAILED
    assert job.error == "sem_contas"
    assert job.finished_at is not None
    chamado.assert_not_called()


@pytest.mark.asyncio
async def test_auto_link_run_espelha_so_no_botao(
    db: AsyncSession, make_user: Callable, monkeypatch
):
    """trigger 'botao_vincular' → espelhar_lista=True; a varredura das 10h
    (trigger 'varredura_diaria') não espelha."""
    from app import worker

    dono = await make_user(role=UserRole.ADMIN)
    integ = await _conta(db, dono, IntegrationPlatform.ML)
    vistos: list[bool] = []

    async def _run(session, *, job_id, integration_ids, espelhar_lista=False):
        vistos.append(espelhar_lista)

    monkeypatch.setattr(worker, "run_auto_link", _run)
    job = await _job_vincular(db, dono)
    await worker.auto_link_run({}, str(job.id), [str(integ.id)])
    job.payload = {"trigger": "varredura_diaria"}
    await db.commit()
    await worker.auto_link_run({}, str(job.id), [str(integ.id)])
    assert vistos == [True, False]


def _link_ok(stats: dict | None = None):
    async def _f(session, job_id, integ):
        return dict(stats or {"created": 0}), False

    return _f


@pytest.mark.asyncio
async def test_run_auto_link_interrompido_vira_failed_nao_running(
    db: AsyncSession, make_user: Callable, monkeypatch
):
    """Timeout do arq / reinício chegam como CancelledError (não é Exception):
    o job terminava com finished_at e status 'running' para sempre na tela."""
    import asyncio

    from app.services import auto_link as al

    dono = await make_user(role=UserRole.ADMIN)
    integ = await _conta(db, dono, IntegrationPlatform.ML)
    job = await _job_vincular(db, dono)

    async def _cortado(session, job_id, integ):
        raise asyncio.CancelledError

    monkeypatch.setattr(al, "_link_with_retry", _cortado)
    with pytest.raises(asyncio.CancelledError):
        await al.run_auto_link(db, job_id=job.id, integration_ids=[integ.id])
    await db.refresh(job)
    assert job.status == BackgroundJobStatus.FAILED
    assert job.error == "interrompido (timeout/reinício)"
    assert job.finished_at is not None


@pytest.mark.asyncio
async def test_run_auto_link_rodada_repetida_recomeca_do_zero(
    db: AsyncSession, make_user: Callable, monkeypatch
):
    """O arq roda de novo o job interrompido: processed volta a 0, o erro e o
    finished_at da rodada cortada saem, e o fim é gravado normalmente."""
    from datetime import UTC, datetime

    from app.services import auto_link as al

    dono = await make_user(role=UserRole.ADMIN)
    integ = await _conta(db, dono, IntegrationPlatform.ML)
    job = await _job_vincular(
        db,
        dono,
        processed=7,
        error="interrompido (timeout/reinício)",
        finished_at=datetime.now(UTC),
    )
    job.status = BackgroundJobStatus.FAILED
    await db.commit()

    monkeypatch.setattr(al, "_link_with_retry", _link_ok())
    await al.run_auto_link(db, job_id=job.id, integration_ids=[integ.id])
    await db.refresh(job)
    assert job.status == BackgroundJobStatus.SUCCEEDED
    assert job.processed == 0
    assert job.error is None
    assert job.finished_at is not None


@pytest.mark.asyncio
async def test_run_auto_link_espelho_com_erro_de_banco_nao_prende_o_job(
    db: AsyncSession, make_user: Callable, monkeypatch
):
    """Crítica [alta]: o espelho na aba Anúncios rodava na sessão principal; um
    erro de banco nele abortava a transação, o status final se perdia e o job
    ficava RUNNING prendendo a trava global. Agora roda numa sessão própria:
    o job termina SUCCEEDED, com `lista_anuncios_erro` e finished_at."""
    from sqlalchemy import text

    from app.services import auto_link as al
    from app.services import produtos_botoes

    dono = await make_user(role=UserRole.ADMIN)
    integ = await _conta(db, dono, IntegrationPlatform.ML)
    job = await _job_vincular(db, dono)

    async def _espelho_quebrado(session, ids):
        await session.execute(text("SELECT 1/0"))

    monkeypatch.setattr(al, "_link_with_retry", _link_ok({"created": 2}))
    monkeypatch.setattr(produtos_botoes, "espelhar_vinculos_na_lista", _espelho_quebrado)
    await al.run_auto_link(
        db, job_id=job.id, integration_ids=[integ.id], espelhar_lista=True
    )
    await db.refresh(job)
    assert job.status == BackgroundJobStatus.SUCCEEDED
    assert job.finished_at is not None
    assert job.result["created"] == 2
    assert "lista_anuncios" not in job.result
    assert "DivisionByZero" in job.result["lista_anuncios_erro"] or (
        "division by zero" in job.result["lista_anuncios_erro"]
    )


@pytest.mark.asyncio
async def test_run_auto_link_espelho_ok_vai_para_o_resultado(
    db: AsyncSession, make_user: Callable, monkeypatch
):
    from app.services import auto_link as al
    from app.services import produtos_botoes

    dono = await make_user(role=UserRole.ADMIN)
    integ = await _conta(db, dono, IntegrationPlatform.ML)
    job = await _job_vincular(db, dono)
    recebidos: list = []

    async def _espelho(session, ids):
        recebidos.append(list(ids))
        return {"passou_a_ter_produto": 1, "trocou_produto": 0, "amostra": []}

    monkeypatch.setattr(al, "_link_with_retry", _link_ok())
    monkeypatch.setattr(produtos_botoes, "espelhar_vinculos_na_lista", _espelho)
    await al.run_auto_link(db, job_id=job.id, integration_ids=[integ.id])
    await db.refresh(job)
    assert "lista_anuncios" not in job.result  # sem espelhar_lista: não chama
    assert recebidos == []

    await al.run_auto_link(
        db, job_id=job.id, integration_ids=[integ.id], espelhar_lista=True
    )
    await db.refresh(job)
    assert job.status == BackgroundJobStatus.SUCCEEDED
    assert job.result["lista_anuncios"]["passou_a_ter_produto"] == 1
    assert recebidos == [[integ.id]]


# ───────────── sinal de vida ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sinal_de_vida_bate_numa_sessao_propria_e_para_no_fim(
    db: AsyncSession, make_user: Callable
):
    import asyncio
    from datetime import UTC, datetime

    from app.services.job_details import sinal_de_vida

    dono = await make_user(role=UserRole.ADMIN)
    vivo = await _job_massa(db, dono, BackgroundJobType.AUTO_LINK)
    vivo.last_heartbeat_at = None
    terminado = await _job_massa(db, dono, BackgroundJobType.SYNC_ALL)
    terminado.last_heartbeat_at = None
    terminado.finished_at = datetime.now(UTC)
    await db.commit()

    async with sinal_de_vida(vivo.id, intervalo=0.05), sinal_de_vida(
        terminado.id, intervalo=0.05
    ):
        await asyncio.sleep(0.4)
    await db.refresh(vivo)
    await db.refresh(terminado)
    assert vivo.last_heartbeat_at is not None
    assert terminado.last_heartbeat_at is None  # só bate em job não terminado

    marca = vivo.last_heartbeat_at
    await asyncio.sleep(0.2)
    await db.refresh(vivo)
    assert vivo.last_heartbeat_at == marca  # parou ao sair do bloco


# ── robôs que disparam massa sozinhos respeitam a trava global (17/11/2026) ──


async def _atualizar_rodando(db: AsyncSession, dono: User) -> BackgroundJob:
    from datetime import UTC, datetime

    agora = datetime.now(UTC)
    job = BackgroundJob(
        type=BackgroundJobType.IMPORT_LISTINGS,
        status=BackgroundJobStatus.RUNNING,
        created_by=dono.id,
        started_at=agora,
        last_heartbeat_at=agora,
        payload={"trigger": "botao_atualizar"},
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)
    return job


async def _auto_links(db: AsyncSession) -> list[BackgroundJob]:
    from sqlalchemy import select

    db.expire_all()
    return list(
        (
            await db.execute(
                select(BackgroundJob).where(BackgroundJob.type == BackgroundJobType.AUTO_LINK)
            )
        ).scalars()
    )


@pytest.mark.asyncio
async def test_varredura_das_10h_espera_o_atualizar_terminar(
    db: AsyncSession, make_user: Callable, monkeypatch
):
    """Achado da revisão: a varredura criava o AUTO_LINK e lia todas as contas
    com um Atualizar rodando. Agora espera a massa terminar."""
    import asyncio
    from datetime import UTC, datetime

    from app import worker
    from app.models import Product
    from app.services import vinculo_saude

    dono = await make_user(role=UserRole.ADMIN)
    db.add(Product(user_id=dono.id, sku="var-1", name="v", stock=1, min_stock=0))
    await db.commit()
    await _conta(db, dono, IntegrationPlatform.ML)
    atualizar = await _atualizar_rodando(db, dono)

    monkeypatch.setattr(worker, "_VARREDURA_ESPERA_MAX_S", 10.0)
    monkeypatch.setattr(worker, "_VARREDURA_ESPERA_INTERVALO_S", 0.05)
    rodou: list[str] = []

    async def _auto(ctx, job_id, ids):
        rodou.append(job_id)

    async def _limpeza(**kw):
        return 0

    monkeypatch.setattr(worker, "auto_link_run", _auto)
    monkeypatch.setattr(vinculo_saude, "apagar_mortos_em_lotes", _limpeza)

    tarefa = asyncio.create_task(worker.varredura_vinculos({}))
    await asyncio.sleep(0.4)
    assert await _auto_links(db) == [] and rodou == []
    assert not tarefa.done()

    atualizar.status = BackgroundJobStatus.SUCCEEDED
    atualizar.finished_at = datetime.now(UTC)
    await db.commit()
    await asyncio.wait_for(tarefa, timeout=10)
    jobs = await _auto_links(db)
    assert len(jobs) == 1 and rodou == [str(jobs[0].id)]
    assert jobs[0].payload["trigger"] == "varredura_diaria"


@pytest.mark.asyncio
async def test_varredura_sem_vaga_pula_o_vincular_e_limpa_igual(
    db: AsyncSession, make_user: Callable, monkeypatch
):
    from app import worker
    from app.models import Product
    from app.services import vinculo_saude

    dono = await make_user(role=UserRole.ADMIN)
    db.add(Product(user_id=dono.id, sku="var-2", name="v", stock=1, min_stock=0))
    await db.commit()
    await _conta(db, dono, IntegrationPlatform.ML)
    await _atualizar_rodando(db, dono)
    monkeypatch.setattr(worker, "_VARREDURA_ESPERA_MAX_S", 0.2)
    monkeypatch.setattr(worker, "_VARREDURA_ESPERA_INTERVALO_S", 0.05)
    limpou: list[dict] = []

    async def _auto(ctx, job_id, ids):
        raise AssertionError("não devia rodar")

    async def _limpeza(**kw):
        limpou.append(kw)
        return 0

    monkeypatch.setattr(worker, "auto_link_run", _auto)
    monkeypatch.setattr(vinculo_saude, "apagar_mortos_em_lotes", _limpeza)
    await worker.varredura_vinculos({})
    assert await _auto_links(db) == []
    assert len(limpou) == 1 and "pular_ocultos" not in limpou[0]


@pytest.mark.asyncio
async def test_noturna_nao_enfileira_com_massa_e_sai_no_tique_livre(
    db: AsyncSession, make_user: Callable
):
    """A sincronização da noite não cria o SYNC_ALL com uma massa rodando; no
    tique seguinte, livre, sai (a janela vai até 4 h depois do horário)."""
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import select

    from app.models import UserSettings
    from app.worker import SP_TZ, daily_sync_scheduler

    dono = await make_user(role=UserRole.ADMIN)
    agora_sp = datetime.now(SP_TZ).replace(second=0, microsecond=0)
    horario = max(agora_sp.replace(hour=0, minute=0), agora_sp - timedelta(minutes=30))
    db.add(UserSettings(user_id=dono.id, daily_sync_enabled=True,
                        daily_sync_time=horario.time()))
    await db.commit()
    atualizar = await _atualizar_rodando(db, dono)

    pool = AsyncMock()
    pool.enqueue_job = AsyncMock(return_value=type("J", (), {"job_id": "arq-noite"}))

    async def _sync_alls():
        db.expire_all()
        return list(
            (
                await db.execute(
                    select(BackgroundJob).where(BackgroundJob.type == BackgroundJobType.SYNC_ALL)
                )
            ).scalars()
        )

    with patch("app.worker.get_arq_sync_pool", AsyncMock(return_value=pool)):
        await daily_sync_scheduler({})
        assert await _sync_alls() == []
        pool.enqueue_job.assert_not_awaited()

        atualizar.status = BackgroundJobStatus.SUCCEEDED
        atualizar.finished_at = datetime.now(UTC)
        await db.commit()
        await daily_sync_scheduler({})
    [job] = await _sync_alls()
    assert job.payload["trigger"] == "daily_sync" and job.arq_job_id == "arq-noite"
