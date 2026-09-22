"""Pedido do Bling que não entra (robô da Ouvidoria) — o que este arquivo trava.

O robô olha o ESTADO: os `background_jobs` de `ingest_bling_order` que
terminaram em falha × a presença do pedido em `bling_orders`.

- "esgotado" ≠ "falhou": FAILED ainda dentro do loop do `ingest_orders_retry_
  sweep` (sweep_attempts < 8 e job recente) NÃO abre nada — abre quando o teto
  de tentativas estourou, quando o job passou da janela de 3 dias do sweep ou
  quando o sweep está desligado por flag;
- `idade_min` (config do robô) segura o esgotado novo demais;
- pedido que já está em `bling_orders` (QUALQUER situação) não abre e fecha a
  ocorrência aberta como "sumiu"; evento de exclusão é ignorado e um job de
  exclusão SUCCEEDED posterior também fecha;
- job podado pelo gc com o pedido AINDA fora → a ocorrência PERSISTE (`rever`),
  nunca fecha por tempo — é o falso "resolveu" mais perigoso do robô;
- conferência ao vivo: Bling responde → título com o número do pedido e cache
  em `dados`; 404/vazio → não abre (pedido apagado antes do ingest); Bling
  instável → abre com "#<id>";
- sweep serializado por advisory lock, tick sai no modo `desligado`, modo
  `silencioso` registra e não manda Threema, e o resumo em linguagem de
  operação.

O Bling ao vivo entra por monkeypatch em `vigia._cliente_bling` (a chamada HTTP
tem teste próprio).
"""
# ruff: noqa: S608 — o `_limpar` monta o DELETE com nomes de tabela fixos.
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    BackgroundJob,
    BackgroundJobStatus,
    BackgroundJobType,
    BlingOrder,
    OuvidoriaOcorrencia,
    OuvidoriaRobo,
)
from app.services import ouvidoria as svc
from app.services import vigia_ingest_bling as vigia

pytestmark = pytest.mark.asyncio

ROBO = vigia.ROBO


# ─── fixtures ──────────────────────────────────────────────────────────────


async def _limpar(db: AsyncSession) -> None:
    for tbl in (
        "ouvidoria_ocorrencias",
        "ouvidoria_rodadas",
        "ouvidoria_robos",
        "background_jobs",
        "bling_orders",
    ):
        await db.execute(text(f"DELETE FROM {tbl}"))
    await db.commit()


@pytest_asyncio.fixture(autouse=True)
async def _limpa(db: AsyncSession):
    await _limpar(db)
    yield
    await _limpar(db)


class _Threema:
    enviados: list[tuple[str, list[str]]] = []

    async def send_to_all(self, texto: str, recipients=None) -> dict:
        self.enviados.append((texto, list(recipients or [])))
        return {"sent": list(recipients or []), "failed": []}


@pytest.fixture(autouse=True)
def _sem_threema(monkeypatch) -> list[tuple[str, list[str]]]:
    _Threema.enviados = []
    monkeypatch.setattr(svc.threema, "ThreemaClient", _Threema)
    return _Threema.enviados


@pytest.fixture(autouse=True)
def _sem_bling(monkeypatch):
    """Padrão dos testes: NÃO há integração Bling — a conferência ao vivo sai
    de cena e o robô é julgado só pelo estado (que é o que a maioria dos casos
    testa). Quem quer conferência troca por `_bling`."""

    async def _nada(session):
        return None

    monkeypatch.setattr(vigia, "_cliente_bling", _nada)


class _BlingFake:
    """get_order: por id devolve o pedido, levanta HTTPStatusError ou erra."""

    def __init__(self, pedidos: dict[int, dict] | None = None, erro: Exception | None = None):
        self.pedidos = pedidos or {}
        self.erro = erro
        self.chamadas: list[int] = []

    async def get_order(self, bling_id: int) -> dict:
        self.chamadas.append(int(bling_id))
        if self.erro is not None:
            raise self.erro
        data = self.pedidos.get(int(bling_id))
        if data is None:
            raise httpx.HTTPStatusError(
                "404", request=httpx.Request("GET", "http://x"),
                response=httpx.Response(404),
            )
        return data


def _usar_bling(monkeypatch, fake: _BlingFake) -> _BlingFake:
    async def _cliente(session):
        return fake

    monkeypatch.setattr(vigia, "_cliente_bling", _cliente)
    return fake


@pytest_asyncio.fixture
async def user_id(make_user):
    """Só o id: a sessão de teste é a mesma que a rodada usa e o `expire_all`
    dos helpers invalidaria o objeto User (lazy load fora do greenlet)."""
    return (await make_user()).id


async def _job(
    db: AsyncSession,
    user_id,
    bling_id: int,
    *,
    status: BackgroundJobStatus = BackgroundJobStatus.FAILED,
    sweep_attempts: int = 8,
    event: str = "pedido.criacao",
    erro: str | None = "RuntimeError: bling_order_empty",
    idade_min: int = 120,
    finished_min: int = 60,
) -> BackgroundJob:
    """Um BackgroundJob de ingest. `created_at` tem server_default now(), então
    a idade é forçada por UPDATE depois do insert."""
    agora = datetime.now(UTC)
    job = BackgroundJob(
        type=BackgroundJobType.INGEST_BLING_ORDER,
        status=status,
        created_by=user_id,
        total=1,
        payload={
            "trigger": "webhook_bling",
            "event": event,
            "delivery_id": str(uuid.uuid4()),
            "bling_order_id": bling_id,
            "user_id": str(user_id),
            "sweep_attempts": sweep_attempts,
        },
        error=erro,
        finished_at=(
            agora - timedelta(minutes=finished_min)
            if status
            in (
                BackgroundJobStatus.FAILED,
                BackgroundJobStatus.SUCCEEDED,
                BackgroundJobStatus.CANCELLED,
            )
            else None
        ),
    )
    db.add(job)
    await db.commit()
    await db.execute(
        text("UPDATE background_jobs SET created_at = :c WHERE id = :i"),
        {"c": agora - timedelta(minutes=idade_min), "i": job.id},
    )
    await db.commit()
    await db.refresh(job)
    return job


async def _no_banco(db: AsyncSession, bling_id: int, situacao: str = "6") -> None:
    db.add(
        BlingOrder(
            bling_id=bling_id,
            numero=str(bling_id),
            item_index=0,
            situacao=situacao,
        )
    )
    await db.commit()


async def _abertas(db: AsyncSession) -> list[OuvidoriaOcorrencia]:
    db.expire_all()
    return list(
        (
            await db.execute(
                select(OuvidoriaOcorrencia)
                .where(
                    OuvidoriaOcorrencia.robo_chave == ROBO,
                    OuvidoriaOcorrencia.fechada_em.is_(None),
                )
                .order_by(OuvidoriaOcorrencia.chave)
            )
        )
        .scalars()
        .all()
    )


async def _todas(db: AsyncSession) -> list[OuvidoriaOcorrencia]:
    db.expire_all()
    return list(
        (
            await db.execute(
                select(OuvidoriaOcorrencia)
                .where(OuvidoriaOcorrencia.robo_chave == ROBO)
                .order_by(OuvidoriaOcorrencia.aberta_em)
            )
        )
        .scalars()
        .all()
    )


async def _rodar(db: AsyncSession) -> dict:
    out = await vigia.vigia_ingest_bling_run(db)
    db.expire_all()
    return out


# ─── quando abre ───────────────────────────────────────────────────────────


async def test_esgotado_por_tentativas_abre(db, user_id):
    await _job(db, user_id, 111, sweep_attempts=8)
    out = await _rodar(db)

    abertas = await _abertas(db)
    assert [o.chave for o in abertas] == ["pedido:111"]
    o = abertas[0]
    assert o.titulo == "Pedido #111 do Bling não entrou no DaVinci"
    assert o.plataforma == "bling" and o.pedido == "111"
    assert o.severidade == "pessoa" and o.precisa_pessoa is True
    assert o.link == "/sincronizacoes?type=ingest_bling_order&status=failed"
    assert o.dados["bling_id"] == 111 and o.dados["tentativas_sweep"] == 8
    assert "bling_order_empty" in o.detalhe
    # 3 do arq × (8 + 1) rodadas do sweep.
    assert "27 tentativas" in o.detalhe
    assert out["novas"] == 1 and out["esgotados"] == 1 and out["entraram"] == 0


async def test_esgotado_por_idade_do_sweep_abre(db, user_id):
    """Job com poucas tentativas mas fora da janela de 3 dias: o sweep não
    olha mais pra ele, então ninguém vai re-tentar."""
    await _job(db, user_id, 222, sweep_attempts=1, idade_min=60 * 24 * 4)
    await _rodar(db)
    assert [o.chave for o in await _abertas(db)] == ["pedido:222"]


async def test_sweep_desligado_torna_qualquer_falha_final(db, user_id, monkeypatch):
    await _job(db, user_id, 333, sweep_attempts=0)
    assert await _abertas(db) == []
    assert (await _rodar(db))["em_retentativa"] == 1

    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "enable_ingest_orders_retry_sweep", False)
    await _rodar(db)
    assert [o.chave for o in await _abertas(db)] == ["pedido:333"]


# ─── quando NÃO abre ───────────────────────────────────────────────────────


async def test_ainda_no_loop_do_sweep_nao_abre(db, user_id):
    await _job(db, user_id, 444, sweep_attempts=3, idade_min=90)
    out = await _rodar(db)
    assert await _abertas(db) == []
    assert out["em_retentativa"] == 1 and out["esgotados"] == 0


async def test_novo_demais_para_a_idade_minima_nao_abre(db, user_id):
    """Esgotou as tentativas, mas chegou há 10 min: o padrão é 30."""
    await _job(db, user_id, 555, sweep_attempts=8, idade_min=10, finished_min=2)
    out = await _rodar(db)
    assert await _abertas(db) == [] and out["em_retentativa"] == 1


async def test_evento_de_exclusao_nao_abre(db, user_id):
    await _job(db, user_id, 666, event="pedido.exclusao")
    out = await _rodar(db)
    assert await _abertas(db) == [] and out["esgotados"] == 0


async def test_pedido_no_banco_nao_abre_e_fecha_a_aberta(db, user_id):
    await _job(db, user_id, 777)
    await _rodar(db)
    assert len(await _abertas(db)) == 1

    # O pedido entrou (à mão, pela rede diária, tanto faz).
    await _no_banco(db, 777)
    out = await _rodar(db)
    assert await _abertas(db) == []
    assert out["entraram"] == 1 and out["sumiram"] == 1
    fechada = (await _todas(db))[0]
    assert fechada.fechamento == "sumiu" and fechada.fechada_por == "robô"


async def test_pedido_excluido_no_banco_conta_como_entrou(db, user_id):
    """`situacao='excluido'`: a linha existe, o pedido passou pelo DaVinci."""
    await _job(db, user_id, 788)
    await _rodar(db)
    await _no_banco(db, 788, situacao="excluido")
    out = await _rodar(db)
    assert await _abertas(db) == [] and out["entraram"] == 1


async def test_exclusao_bem_sucedida_posterior_fecha(db, user_id):
    await _job(db, user_id, 799)
    await _rodar(db)
    assert len(await _abertas(db)) == 1

    await _job(
        db, user_id, 799,
        status=BackgroundJobStatus.SUCCEEDED,
        event="pedido.exclusao",
        erro=None,
        idade_min=5,
        finished_min=1,
    )
    out = await _rodar(db)
    assert await _abertas(db) == []
    assert out["nao_existe_no_bling"] == 1 and out["sumiram"] == 1


# ─── quando NUNCA fecha ────────────────────────────────────────────────────


async def test_job_podado_com_pedido_ainda_fora_mantem_a_ocorrencia(db, user_id):
    """O `background_jobs_gc` poda os FAILED 7 dias depois. Sem o passo de
    `rever`, TODA ocorrência com mais de 7 dias fecharia como "sumiu" com o
    pedido ainda fora do banco — falso "resolveu"."""
    job_id = (await _job(db, user_id, 888)).id
    await _rodar(db)
    aberta = (await _abertas(db))[0]
    vista_antes = aberta.ultima_vista_em

    await db.execute(
        text("DELETE FROM background_jobs WHERE id = :i"), {"i": job_id}
    )
    await db.commit()
    out = await _rodar(db)

    abertas = await _abertas(db)
    assert [o.chave for o in abertas] == ["pedido:888"]
    assert abertas[0].ultima_vista_em > vista_antes
    assert out["persistem"] == 1 and out["sumiram"] == 0


async def test_job_de_volta_para_a_fila_mantem_a_ocorrencia(db, user_id):
    """Alguém re-disparou o ingest (PENDING): saiu dos candidatos, mas o
    pedido continua fora — a ocorrência fica e só conta em `na_fila`."""
    job_id = (await _job(db, user_id, 899)).id
    await _rodar(db)
    await db.execute(
        text(
            "UPDATE background_jobs SET status = 'pending', finished_at = NULL "
            "WHERE id = :i"
        ),
        {"i": job_id},
    )
    await db.commit()

    out = await _rodar(db)
    assert [o.chave for o in await _abertas(db)] == ["pedido:899"]
    assert out["persistem"] == 1 and out["na_fila"] == 1 and out["sumiram"] == 0


# ─── conferência ao vivo no Bling ──────────────────────────────────────────


async def test_bling_responde_preenche_numero_e_situacao(db, user_id, monkeypatch):
    fake = _usar_bling(
        monkeypatch,
        _BlingFake({900: {"numero": "40311", "situacao": {"id": 6}}}),
    )
    await _job(db, user_id, 900)
    out = await _rodar(db)

    o = (await _abertas(db))[0]
    assert o.titulo == "Pedido 40311 do Bling não entrou no DaVinci"
    assert o.pedido == "40311"
    assert o.dados["numero"] == "40311" and o.dados["situacao_bling"] == "6"
    assert o.dados["conferido_bling_em"]
    assert "é o DaVinci que não gravou" in o.detalhe
    assert out["conferidos_bling"] == 1 and fake.chamadas == [900]

    # Rodada seguinte NÃO re-consulta (o número está no cache de `dados`).
    await _rodar(db)
    assert fake.chamadas == [900]


async def test_bling_404_nao_abre_e_fecha_a_aberta(db, user_id, monkeypatch):
    """Pedido de teste apagado antes do ingest: abrir seria ruído 'pessoa'."""
    await _job(db, user_id, 901)
    await _rodar(db)
    assert len(await _abertas(db)) == 1

    _usar_bling(monkeypatch, _BlingFake({}))
    out = await _rodar(db)
    assert await _abertas(db) == []
    assert out["nao_existe_no_bling"] == 1 and out["sumiram"] == 1


async def test_bling_instavel_abre_com_o_id_cru(db, user_id, monkeypatch):
    _usar_bling(monkeypatch, _BlingFake(erro=httpx.ReadTimeout("estourou")))
    await _job(db, user_id, 902)
    out = await _rodar(db)

    o = (await _abertas(db))[0]
    assert o.titulo == "Pedido #902 do Bling não entrou no DaVinci"
    assert "não consegui conferir no Bling" in o.detalhe
    assert out["bling_falhou"] == 1
    assert "Bling não respondeu à conferência" in out["resumo"]


async def test_bling_falhou_conta_pedidos_e_o_resumo_mostra_o_numero(db, user_id, monkeypatch):
    """`bling_falhou` é CONTAGEM, não bandeira: 1 soluço e 3 pedidos sem
    conferência têm que aparecer diferentes na última rodada do painel."""
    _usar_bling(monkeypatch, _BlingFake(erro=httpx.ReadTimeout("estourou")))
    for bid in (930, 931, 932):
        await _job(db, user_id, bid)

    out = await _rodar(db)

    assert out["bling_falhou"] == 3
    assert "Bling não respondeu à conferência de 3" in out["resumo"]


async def test_teto_de_jobs_pega_os_mais_recentes(db, user_id, monkeypatch):
    """No dia em que a importação para em massa, a cota de jobs lidos não pode
    ficar toda com os mais VELHOS (que já viraram ocorrência) — aí pedido novo
    nenhum ganharia linha até os antigos envelhecerem."""
    monkeypatch.setattr(vigia, "_LIMITE_JOBS", 2)
    await _job(db, user_id, 940, idade_min=300)
    await _job(db, user_id, 941, idade_min=200)
    await _job(db, user_id, 942, idade_min=100)

    out = await _rodar(db)

    assert out["jobs_falhos"] == 2
    assert [o.chave for o in await _abertas(db)] == ["pedido:941", "pedido:942"]


async def test_teto_de_conferencias_por_rodada(db, user_id, monkeypatch):
    fake = _usar_bling(
        monkeypatch,
        _BlingFake({910: {"numero": "1"}, 911: {"numero": "2"}, 912: {"numero": "3"}}),
    )
    monkeypatch.setattr(vigia, "_MAX_CONFERENCIAS_BLING", 2)
    for bid in (910, 911, 912):
        await _job(db, user_id, bid)

    await _rodar(db)
    assert len(fake.chamadas) == 2
    # O que passou do teto abriu do mesmo jeito, só sem o número.
    assert len(await _abertas(db)) == 3
    assert await _rodar(db) and len(fake.chamadas) == 3


async def test_sem_integracao_bling_abre_sem_conferir(db, user_id):
    await _job(db, user_id, 913)
    out = await _rodar(db)
    o = (await _abertas(db))[0]
    assert o.titulo == "Pedido #913 do Bling não entrou no DaVinci"
    assert "sem integração cadastrada" in o.detalhe
    assert out["bling_falhou"] == 1


# ─── resumo, modo e sweep ──────────────────────────────────────────────────


async def test_resumo_em_linguagem_de_operacao(db, user_id):
    await _job(db, user_id, 920)
    await _job(db, user_id, 921)
    await _job(db, user_id, 922, sweep_attempts=0, idade_min=90)  # ainda no loop
    await _job(db, user_id, 923, status=BackgroundJobStatus.PENDING, idade_min=90)
    await _no_banco(db, 921)

    out = await _rodar(db)
    assert out["resumo"].startswith("1 esgotado · 1 entrou")
    assert "1 na fila" in out["resumo"]
    rodada = (
        await db.execute(
            text(
                "SELECT resumo, ok FROM ouvidoria_rodadas WHERE robo_chave = :r"
            ),
            {"r": ROBO},
        )
    ).one()
    assert rodada.ok is True and rodada.resumo == out["resumo"]


async def test_silencioso_registra_e_nao_manda_threema(db, user_id, _sem_threema):
    await svc.sincronizar_catalogo(db)
    await db.commit()
    robo = await db.get(OuvidoriaRobo, ROBO)
    # O robô nasce silencioso (modo_padrao do catálogo).
    assert robo.modo == "silencioso"
    robo.threema_recipients = "ABCDEFGH"
    await db.commit()

    await _job(db, user_id, 930)
    out = await _rodar(db)
    assert len(await _abertas(db)) == 1
    assert out["avisadas"] == 0 and _sem_threema == []

    robo.modo = "ligado"
    await db.commit()
    await _rodar(db)
    assert len(_sem_threema) == 1 and "Pedido #930" in _sem_threema[0][0]


async def test_sweep_e_serializado_pelo_advisory_lock(db, monkeypatch):
    from app.services.advisory_lock import SYNC_NAMESPACE

    got = (
        await db.execute(
            text("SELECT pg_try_advisory_xact_lock(:ns, :key)"),
            {"ns": SYNC_NAMESPACE, "key": vigia._SWEEP_LOCK_KEY},
        )
    ).scalar()
    assert got
    assert await vigia.vigia_ingest_bling_sweep() == {"skipped": "lock_busy"}
    await db.rollback()  # solta o lock

    chamado: list[int] = []

    async def _run(session):
        chamado.append(1)
        return {"ok": True}

    monkeypatch.setattr(vigia, "vigia_ingest_bling_run", _run)
    assert await vigia.vigia_ingest_bling_sweep() == {"ok": True} and chamado == [1]


async def test_tick_nao_roda_com_o_robo_desligado(db, monkeypatch):
    from app import worker

    chamadas: list[int] = []

    async def _sweep():
        chamadas.append(1)
        return {"novas": 0}

    monkeypatch.setattr(vigia, "vigia_ingest_bling_sweep", _sweep)
    await svc.sincronizar_catalogo(db)
    robo = await db.get(OuvidoriaRobo, ROBO)
    robo.modo = "desligado"
    await db.commit()
    await worker.vigia_ingest_bling_tick({})
    assert chamadas == []

    for modo in ("silencioso", "ligado"):
        robo.modo = modo
        await db.commit()
        await worker.vigia_ingest_bling_tick({})
    assert chamadas == [1, 1]


# ─── a regra pura ──────────────────────────────────────────────────────────


async def test_esgotado_e_uma_funcao_pura():
    agora = datetime.now(UTC)

    def _j(**kw) -> BackgroundJob:
        base = {
            "status": BackgroundJobStatus.FAILED,
            "finished_at": agora - timedelta(minutes=10),
            "payload": {"sweep_attempts": 0},
        }
        j = BackgroundJob(**{**base, **kw})
        j.created_at = kw.get("created_at", agora - timedelta(hours=2))
        return j

    assert not vigia.esgotado(_j(), agora, True)
    assert vigia.esgotado(_j(payload={"sweep_attempts": 8}), agora, True)
    assert vigia.esgotado(_j(), agora, False)  # sweep desligado
    assert vigia.esgotado(_j(created_at=agora - timedelta(days=4)), agora, True)
    assert not vigia.esgotado(
        _j(status=BackgroundJobStatus.PENDING, finished_at=None), agora, True
    )
    # Payload torto (mexido à mão) não derruba a rodada.
    assert not vigia.esgotado(_j(payload={"sweep_attempts": "x"}), agora, True)
