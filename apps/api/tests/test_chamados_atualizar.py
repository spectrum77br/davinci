"""Botão Atualizar da aba Chamados (09/11/2026).

POST /api/chamados/atualizar pede a leitura pela API de todos os chamados (a
passada do :25, na hora) na fila UI; uma a cada 15 min para a empresa inteira —
quem clica de novo nesse tempo recebe a leitura que já foi pedida. GET
/atualizar/{job_id} dá o andamento. O trabalho do worker não lê junto com o
cron (mesma trava).
"""

from __future__ import annotations

import pytest

from app.services import chamados_atualizar as svc

pytestmark = pytest.mark.asyncio

API = "/api/chamados/atualizar"
EDITA = {"chamados": {"view": True, "edit": True}}
SO_VE = {"chamados": {"view": True}}


class _RedisFalso:
    def __init__(self) -> None:
        self.d: dict[str, str] = {}
        self.ttls: dict[str, int] = {}

    async def set(self, k, v, nx=False, xx=False, ex=None, keepttl=False):
        if nx and k in self.d:
            return None
        if xx and k not in self.d:
            return None
        self.d[k] = v
        if ex is not None:
            self.ttls[k] = ex
        elif not keepttl:
            self.ttls.pop(k, None)
        return True

    async def get(self, k):
        return self.d.get(k)

    async def ttl(self, k):
        return self.ttls.get(k, -1) if k in self.d else -2

    async def delete(self, k):
        self.ttls.pop(k, None)
        return 1 if self.d.pop(k, None) is not None else 0


class _Job:
    def __init__(self, job_id: str) -> None:
        self.job_id = job_id


class _PoolFalso:
    def __init__(self) -> None:
        self.chamadas: list[str] = []
        self.erro: Exception | None = None

    async def enqueue_job(self, nome, *args, **kwargs):
        if self.erro:
            raise self.erro
        self.chamadas.append(nome)
        return _Job(f"job-{len(self.chamadas)}")


@pytest.fixture
def fila(monkeypatch):
    from app.routers import chamados as rota

    redis = _RedisFalso()
    pool = _PoolFalso()

    async def _pool():
        return pool

    monkeypatch.setattr(rota, "redis", redis)
    monkeypatch.setattr(rota, "get_arq_ui_pool", _pool)
    return redis, pool


async def test_pede_a_leitura_e_segura_15_min(client, make_user, auth_as, fila):
    redis, pool = fila
    auth_as(await make_user(permissions=EDITA))

    r = await client.post(API)
    assert r.status_code == 200, r.text
    corpo = r.json()
    assert corpo["enfileirado"] is True and corpo["job_id"] == "job-1"
    assert corpo["pedido_em"] and corpo["pode_atualizar_em"] and corpo["por"]
    assert pool.chamadas == [svc.JOB]
    assert redis.ttls[svc.CHAVE_PEDIDO] == svc.INTERVALO_S
    # O job_id ficou guardado na trava sem mexer no prazo dela.
    assert svc.ler_pedido(redis.d[svc.CHAVE_PEDIDO])["job_id"] == "job-1"

    # Outra pessoa clica dentro dos 15 min: acompanha a mesma leitura.
    auth_as(await make_user(permissions=EDITA))
    r = await client.post(API)
    assert r.status_code == 200, r.text
    corpo2 = r.json()
    assert corpo2["enfileirado"] is False and corpo2["job_id"] == "job-1"
    assert corpo2["por"] == corpo["por"] and corpo2["pode_atualizar_em"]
    assert len(pool.chamadas) == 1


async def test_fila_fora_solta_a_trava(client, make_user, auth_as, fila):
    redis, pool = fila
    auth_as(await make_user(permissions=EDITA))
    pool.erro = RuntimeError("redis caiu")
    r = await client.post(API)
    assert r.status_code == 503 and r.json()["detail"]["code"] == "fila_indisponivel"
    assert svc.CHAVE_PEDIDO not in redis.d


async def test_quem_so_ve_nao_pede_a_leitura(client, make_user, auth_as, fila):
    _redis, pool = fila
    auth_as(await make_user(permissions=SO_VE))
    assert (await client.post(API)).status_code == 403
    assert pool.chamadas == []


async def test_worker_le_e_devolve_o_resumo(monkeypatch):
    from app import worker

    lidas = []

    async def _sync(session, **kw):
        lidas.append(1)
        return {"verificados": 7, "novos": 2, "encerrados": 1, "falhas": 0}

    async def _me():
        lidas.append("me")
        return {"me_lidos": 1, "me_falas": 3, "me_em": "13/11"}

    monkeypatch.setattr(worker.chamados_devolucao_sync, "sync_respostas", _sync)
    monkeypatch.setattr(worker, "_chamados_melhor_envio_passada", _me)
    resumo = await worker.chamados_atualizar_api({"redis": None})
    # 13/11: a mesma passada lê os atendimentos do Melhor Envio (só os números voltam)
    assert resumo == {
        "verificados": 7,
        "novos": 2,
        "encerrados": 1,
        "falhas": 0,
        "me_lidos": 1,
        "me_falas": 3,
    }
    assert lidas == [1, "me"]

    # Com a leitura do :25 em andamento (trava ocupada), não lê de novo.
    class _Ocupado:
        async def set(self, *a, **k):
            return None

    resumo = await worker.chamados_atualizar_api({"redis": _Ocupado()})
    assert resumo == {"pulado_ja_rodando": 1}
    assert lidas == [1, "me"]
