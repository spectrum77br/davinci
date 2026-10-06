# ruff: noqa: E501
"""Relatório do dia do robô de Denúncia no Threema (06/10/2026).

Vinicius: "esse relatório consegue enviar pelo Threema para Cairo, Hary e Roma? todo dia pode
enviar … os de hoje pode mandar". Regras em services/denuncia_relatorio_threema (worker
`denuncia_relatorio_fechar`, :07 de toda hora)."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from sqlalchemy import delete

from app.models import DenunciaRelatorio, ThreemaInformarConfig
from app.services import denuncia_relatorio_threema as rt
from app.services import threema

pytestmark = pytest.mark.asyncio

ONTEM = date(2026, 10, 5)
AS_7H07 = datetime(2026, 10, 6, 10, 7, tzinfo=UTC)  # 07:07 em Brasília
AS_0H07 = datetime(2026, 10, 6, 3, 7, tzinfo=UTC)   # 00:07 — fecha, mas ainda não manda

# os números de 05/10 (os do relatório de verdade, sem as listas)
NUMEROS = {
    "achou": {"total": 28, "lojas_proprias": 8, "descartados": 6, "lista": [],
              "por_site": {"Amazon": {"Nosso": 0, "Diversos": 3, "Outros": 0},
                           "Shopee": {"Nosso": 0, "Diversos": 1, "Outros": 0},
                           "TikTok Shop": {"Nosso": 2, "Diversos": 4, "Outros": 0},
                           "Mercado Livre": {"Nosso": 5, "Diversos": 13, "Outros": 0}}},
    "denunciou": {"total": 102, "de_novo": 0, "replicas": 54, "lista": [],
                  "por_site": {"Shopee": {"Nosso": 0, "Diversos": 58, "Outros": 0},
                               "TikTok Shop": {"Nosso": 0, "Diversos": 4, "Outros": 0},
                               "Mercado Livre": {"Nosso": 0, "Diversos": 40, "Outros": 0}}},
    "anatel": {"lojas": 47, "anuncios": 121, "consumidor": 0, "processos": [], "movimentos": [],
               "situacao": {"Enviada": 153, "Recebida": 1, "Em tratamento": 81,
                            "Respondida — analisar": 0, "Exigência": 0}},
    "respostas": {"total": 171, "lista": [],
                  "por_site": {"Shopee": {"removidos": 3, "recusados": 0, "sem_resposta": 77, "outras": 0},
                               "Mercado Livre": {"removidos": 0, "recusados": 91, "sem_resposta": 0, "outras": 0}}},
    "sairam": {"total": 3, "lista": []},
    "prints": {"capturas": 652, "anuncios": 373, "registros": 359},
    "conferidos": {"anuncios": 652, "fora_do_ar": 3},
}
ANOTACOES = {
    "ultimo_contato": "2026-10-05T23:59:30-03:00",
    "passos": {
        "tarefa_a": {"acao": "checagem", "nome": "Checagem antes da rodada", "status": "erro", "feito": "erro",
                     "inicio": "2026-10-05T05:45:00", "fim": "2026-10-05T05:56:00", "erro": "checagem_pre_rodada.py: código 3"},
        "tarefa_b": {"acao": "anatel", "nome": "Denúncias Anatel", "status": "concluida", "feito": "concluido",
                     "inicio": "2026-10-05T10:23:00", "fim": "2026-10-05T12:06:00"},
    },
    "ocorrencias": {
        "prob:1": {"titulo": "Captcha da Shopee no perfil 50", "tipo": "pessoa", "primeira": "2026-10-05T15:32:00-03:00", "ultima": "2026-10-05T15:40:00-03:00"},
        "prob:2": {"titulo": "Captcha da Shopee no perfil 50", "tipo": "pessoa", "primeira": "2026-10-05T17:56:00-03:00", "ultima": "2026-10-05T18:00:00-03:00"},
        "prob:3": {"titulo": "Perfil 50 abriu depois da recuperação automática", "tipo": "aviso", "primeira": "2026-10-05T23:01:00-03:00", "ultima": "2026-10-05T23:01:00-03:00"},
        "agora:agenda_a": {"titulo": "8 · Tempo parado: prints e ativos/inativos das 12:00 não começou", "tipo": "pessoa", "primeira": "2026-10-05T12:20:00-03:00", "ultima": "2026-10-05T12:40:00-03:00"},
        "agora:agenda_b": {"titulo": "8 · Tempo parado: prints e ativos/inativos das 13:00 não começou", "tipo": "pessoa", "primeira": "2026-10-05T13:20:00-03:00", "ultima": "2026-10-05T13:40:00-03:00"},
    },
}


@pytest.fixture(autouse=True)
async def limpo(db):
    """O conftest não limpa estas tabelas entre os testes."""
    await db.execute(delete(DenunciaRelatorio))
    await db.execute(delete(ThreemaInformarConfig))
    await db.commit()


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


async def _dia(db, dia: date = ONTEM, numeros: dict | None = NUMEROS) -> None:
    db.add(DenunciaRelatorio(dia=dia, anotacoes=ANOTACOES, numeros=numeros,
                             fechado_em=AS_0H07 if numeros is not None else None))
    await db.commit()


async def _cadastro(db, ids: str = "M5TT27JA,9BH6R7HJ,VBS64V3S") -> None:
    db.add(ThreemaInformarConfig(contexto=rt.CONTEXTO, recipients=ids))
    await db.commit()


async def test_texto_tem_os_numeros_do_dia(db):
    from app.services.denuncia_relatorio import montar

    await _dia(db)
    row = await db.get(DenunciaRelatorio, ONTEM)
    msg = rt.texto(montar(ONTEM, row.numeros, row, AS_7H07), "https://app/denuncia?aba=robo&relatorio=2026-10-05")
    assert msg.startswith("📊 Robô de Denúncia — relatório de seg 05/10")
    assert "Achou 28 anúncio(s) novo(s) (Nosso 7 · Diversos 21)" in msg
    assert "ML 18 · TikTok 6 · Amazon 3 · Shopee 1" in msg
    assert "Denunciou 102 nas lojas (54 réplica(s))" in msg
    assert "Shopee 58 · ML 40 · TikTok 4" in msg
    assert "Anatel: 47 loja(s) peticionada(s) no SEI (121 anúncio(s))" in msg
    assert "81 na fiscalização" in msg and "153 enviado(s)" in msg
    assert "removidos 3 · ❌ recusados 91 · ⏳ sem resposta 77" in msg
    assert "Saíram do ar: 3 (652 conferido(s))" in msg
    assert "Prints: 652 (373 anúncio(s))" in msg
    assert "com erro: Checagem antes da rodada" in msg
    # ocorrência de pessoa agrupada por título; aviso não entra
    assert "Precisou de alguém: 3" in msg and "Captcha da Shopee no perfil 50 (2x)" in msg
    assert "recuperação automática" not in msg
    # "… das 12:00 não começou" (um por horário) vira uma linha só
    assert "não começou" not in msg and "2 horário(s) da agenda não começaram na hora" in msg
    assert msg.endswith("Relatório completo e Excel: https://app/denuncia?aba=robo&relatorio=2026-10-05")
    assert len(msg.encode()) < 3500   # limite do send_simple


async def test_manda_o_de_ontem_uma_vez_a_partir_das_7h(db, enviados):
    await _dia(db)
    await _cadastro(db)
    r = await rt.enviar_pendente(db, AS_0H07)
    assert r == {"enviado": False, "motivo": "antes das 7h"} and not enviados
    r = await rt.enviar_pendente(db, AS_7H07)
    await db.commit()
    assert r["enviado"] and r["dia"] == "2026-10-05"
    texto, alvos = enviados[0]
    assert alvos == ["M5TT27JA", "9BH6R7HJ", "VBS64V3S"]
    assert "relatorio=2026-10-05" in texto
    assert (await db.get(DenunciaRelatorio, ONTEM)).threema_enviado_em is not None
    # a hora seguinte (e um restart) não manda de novo
    r = await rt.enviar_pendente(db, datetime(2026, 10, 6, 11, 7, tzinfo=UTC))
    assert r == {"enviado": False, "motivo": "já enviado"} and len(enviados) == 1


async def test_nao_manda_dia_aberto_nem_dia_velho(db, enviados):
    await _cadastro(db)
    await _dia(db, numeros=None)   # ontem ainda sem congelar
    assert (await rt.enviar_pendente(db, AS_7H07))["motivo"] == "relatório de ontem ainda não fechado"
    await _dia(db, dia=date(2026, 10, 4))   # anteontem fechado e nunca enviado: não despeja
    assert not (await rt.enviar_pendente(db, AS_7H07))["enviado"]
    assert not enviados


async def test_sem_cadastro_ou_falha_nao_carimba(db, enviados):
    await _dia(db)
    assert (await rt.enviar_pendente(db, AS_7H07))["motivo"] == "ninguém cadastrado"
    await _cadastro(db, "ABCD1234")
    enviados.falhar = True
    assert (await rt.enviar_pendente(db, AS_7H07))["motivo"] == "envio falhou"
    assert (await db.get(DenunciaRelatorio, ONTEM)).threema_enviado_em is None
    enviados.falhar = False   # a hora seguinte tenta de novo
    assert (await rt.enviar_pendente(db, datetime(2026, 10, 6, 11, 7, tzinfo=UTC)))["enviado"]


async def test_cadastro_pelo_informar(db, client, make_user, auth_as):
    """O botão "Quem recebe o relatório" (Robô › Ocorrências) usa o Informar: admin e o Cairo."""
    cairo = await make_user(email="sa.geral@tutamail.com")
    cairo.threema = "M5TT27JA"
    await db.commit()
    auth_as(cairo)
    r = await client.put("/api/informar/denuncia_relatorio", json={"recipients": ["M5TT27JA", "ZZZZ9999"]})
    assert r.status_code == 200, r.text
    assert (await client.get("/api/informar/denuncia_relatorio")).json()["recipients"] == ["M5TT27JA"]
    assert await rt.destinatarios(db) == ["M5TT27JA"]
    outro = await make_user(email="fulano@x.com")
    auth_as(outro)
    assert (await client.get("/api/informar/denuncia_relatorio")).status_code == 403
