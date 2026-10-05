# ruff: noqa: E501
"""Robô de Denúncia precisando de alguém → aviso no Threema (05/10/2026).

Cairo: "isso coloca para avisar no Threema… só o Cairo recebe, que sou eu… avisar os 3, tudo que colocou
ali" — captcha na tela, robô parado, Mac mini sem notícia e SEI pedindo código. Regras em
services/denuncia_robo_aviso (worker a cada 2 min)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete

from app.models import DenunciaRoboStatus, ThreemaInformarConfig
from app.services import denuncia_robo_aviso as aviso
from app.services import threema

pytestmark = pytest.mark.asyncio

AGORA = datetime(2026, 10, 5, 4, 3, tzinfo=UTC)  # 01:03 em Brasília — o captcha do ML de 05/10

CAPTCHA_ML = {
    "quando": "2026-10-05 01:03", "tarefa": "Captcha da Mercado Livre no perfil 50",
    "problema": "Mercado Livre pediu verificação em: anúncio MLB5186826153",
    "pergunta": "Leonardo: resolver a verificação na tela do perfil 50", "bloqueia": True,
    "chave": "captcha-mercadolivre50-2026-10-05_01",
}
SEI_1 = {
    "quando": "2026-10-05 01:00", "tarefa": "código de acesso do sei",
    "problema": "o site pediu um código por e-mail e não chegou ao Tuta em 3 min",
    "pergunta": "Encaminhar o e-mail do código para o Tuta", "bloqueia": True,
}
SEI_2 = {**SEI_1, "quando": "2026-10-05 01:01"}
CORRIGIR = {
    "quando": "2026-10-05 01:02", "tarefa": "Corrigir na loja: anúncio nosso com nº de homologação de terceiro",
    "problema": "13 anúncio(s) das nossas lojas declaram nº de outro titular", "bloqueia": True,
}


@pytest.fixture(autouse=True)
async def limpo(db):
    """O conftest não limpa estas tabelas entre os testes (status é uma linha por remetente)."""
    await db.execute(delete(DenunciaRoboStatus))
    await db.execute(delete(ThreemaInformarConfig))
    await db.commit()


class RedisFake:
    def __init__(self) -> None:
        self.d: dict[str, str] = {}

    async def set(self, k, v, nx=False, ex=None):
        if nx and k in self.d:
            return None
        self.d[k] = v
        return True

    async def expire(self, k, s):
        return k in self.d

    async def delete(self, k):
        self.d.pop(k, None)
        return 1


@pytest.fixture
def redis_fake(monkeypatch):
    r = RedisFake()
    monkeypatch.setattr(aviso, "redis", r)
    return r


class Lista(list):
    pass


@pytest.fixture
def enviados(monkeypatch):
    lst = Lista()
    lst.falhar = False

    async def _send_to_all(self, text, recipients=None):
        if lst.falhar:
            return {"sent": [], "failed": list(recipients or [])}
        lst.append((text, list(recipients or [])))
        return {"sent": list(recipients or []), "failed": []}

    monkeypatch.setattr(threema.ThreemaClient, "send_to_all", _send_to_all)
    monkeypatch.setattr(threema.ThreemaClient, "disabled", property(lambda self: False))
    return lst


def _resumo(problemas=(), agente: str = "ok") -> dict:
    return {
        "quando": AGORA.isoformat(),
        "itens": [
            {"chave": "agente", "estado": agente,
             "detalhe": "o agente não roda há 12 min" if agente == "erro" else "v33 no ar",
             "o_que_fazer": 'Abrir o "6 - Agente da varredura"' if agente == "erro" else ""},
            {"chave": "problemas", "estado": "erro" if problemas else "ok",
             "dados": {"lista": list(problemas)}},
        ],
    }


async def _status(db, resumo: dict, recebido: datetime) -> None:
    db.add(DenunciaRoboStatus(remetente="Mac mini da Makisa", dados=resumo, recebido_em=recebido))
    await db.commit()


async def _cadastro(db, contexto: str, ids: str) -> None:
    db.add(ThreemaInformarConfig(contexto=contexto, recipients=ids))
    await db.commit()


async def test_categorias():
    assert aviso.categoria({"tipo": "pessoa", "chave": "agora:mini", "titulo": "O Mac mini não dá notícia há 9 min"}) == "mini"
    assert aviso.categoria({"tipo": "pessoa", "chave": "prob:x", "titulo": "Verificação de segurança do Mercado Livre no perfil 50"}) == "captcha"
    assert aviso.categoria({"tipo": "pessoa", "chave": "agora:muro_shopee", "titulo": "Shopee com verificação"}) == "captcha"
    assert aviso.categoria({"tipo": "pessoa", "chave": "prob:y", "titulo": "código de acesso do sei"}) == "sei"
    assert aviso.categoria({"tipo": "pessoa", "chave": "agora:sei", "titulo": "SEI esperando a assinatura da titular"}) == "sei"
    assert aviso.categoria({"tipo": "pessoa", "chave": "agora:agente", "titulo": "Robô parado"}) == "parado"
    assert aviso.categoria({"tipo": "pessoa", "chave": "agora:agenda_procura_1710", "titulo": "2 · Procurar das 17:10 não começou"}) == "parado"
    assert aviso.categoria({"tipo": "pessoa", "chave": "prob:z", "titulo": "Perfil 50 do AdsPower não abre (05/10 00:17)"}) == "parado"
    # o que não é dos quatro, ou é só aviso, não manda Threema
    assert aviso.categoria({"tipo": "pessoa", "chave": "prob:w", "titulo": CORRIGIR["tarefa"]}) is None
    assert aviso.categoria({"tipo": "aviso", "chave": "prob:v", "titulo": "Captcha da Shopee"}) is None


async def test_captcha_do_ml_avisa_uma_vez(db, redis_fake, enviados):
    await _cadastro(db, "chamados_ia", "ABCD1234")
    await _status(db, _resumo([CAPTCHA_ML, CORRIGIR]), AGORA)
    r = await aviso.rodar(db, AGORA)
    assert r["avisadas"] == 1
    texto, alvos = enviados[0]
    assert alvos == ["ABCD1234"]
    assert "🧩" in texto and "Mercado Livre" in texto and "10 min" in texto
    assert "/denuncia?aba=robo" in texto
    assert "Corrigir na loja" not in texto
    # 2 min depois o painel ainda mostra o captcha: não repete
    r2 = await aviso.rodar(db, AGORA + timedelta(minutes=2))
    assert r2["avisadas"] == 0
    assert len(enviados) == 1


async def test_linha_velha_do_robo_nao_avisa(db, redis_fake, enviados):
    """A lista do mini guarda 24 h: a 1ª volta depois de um deploy não manda o dia inteiro."""
    await _cadastro(db, "chamados_ia", "ABCD1234")
    await _status(db, _resumo([{**CAPTCHA_ML, "quando": "2026-10-04 20:00"}]), AGORA)
    r = await aviso.rodar(db, AGORA)
    assert r["avisadas"] == 0
    assert enviados == []


async def test_mini_sem_noticia_e_robo_parado(db, redis_fake, enviados):
    await _cadastro(db, "chamados_ia", "ABCD1234")
    await _status(db, _resumo(agente="erro"), AGORA - timedelta(minutes=10))
    r = await aviso.rodar(db, AGORA)
    assert r["avisadas"] == 2
    texto, _ = enviados[0]
    assert "📡" in texto and "⛔" in texto
    # continua parado: não repete a cada 2 min
    r2 = await aviso.rodar(db, AGORA + timedelta(minutes=2))
    assert r2["avisadas"] == 0
    assert len(enviados) == 1


async def test_sei_mesmo_tipo_uma_por_hora(db, redis_fake, enviados):
    await _cadastro(db, "chamados_ia", "ABCD1234")
    await _status(db, _resumo([SEI_1, SEI_2]), AGORA)
    r = await aviso.rodar(db, AGORA)
    assert r["avisadas"] == 1
    assert "🔐" in enviados[0][0]


async def test_envio_que_falha_tenta_de_novo(db, redis_fake, enviados):
    await _cadastro(db, "chamados_ia", "ABCD1234")
    await _status(db, _resumo([CAPTCHA_ML]), AGORA)
    enviados.falhar = True
    r = await aviso.rodar(db, AGORA)
    assert r["avisadas"] == 0 and r["erro"] == "envio falhou"
    enviados.falhar = False
    r2 = await aviso.rodar(db, AGORA + timedelta(minutes=2))
    assert r2["avisadas"] == 1
    assert len(enviados) == 1


async def test_cadastro_proprio_manda_e_vazio_e_ninguem(db, redis_fake, enviados):
    await _cadastro(db, "chamados_ia", "ABCD1234")
    await _cadastro(db, "denuncia_robo", "XYZ98765")
    await _status(db, _resumo([CAPTCHA_ML]), AGORA)
    r = await aviso.rodar(db, AGORA)
    assert r["avisadas"] == 1
    assert enviados[0][1] == ["XYZ98765"]


async def test_cadastro_salvo_vazio_nao_manda(db, redis_fake, enviados):
    await _cadastro(db, "chamados_ia", "ABCD1234")
    await _cadastro(db, "denuncia_robo", "")
    await _status(db, _resumo([CAPTCHA_ML]), AGORA)
    r = await aviso.rodar(db, AGORA)
    assert r["avisadas"] == 0 and r["erro"] == "ninguém cadastrado"
    assert enviados == []
