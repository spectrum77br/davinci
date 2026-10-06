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


async def test_texto_curto_com_o_link_do_excel():
    """06/10, depois do 1º envio: "muito grande… coloca só o resumo pequeno e manda o Excel"."""
    msg = rt.texto(ONTEM, NUMEROS, "https://app/x.xlsx")
    assert msg == (
        "📊 Relatório geral do robô de Denúncia — seg 05/10\n"
        "• 28 anúncios novos\n"
        "• 102 denúncias nas lojas\n"
        "• 47 lojas na Anatel (121 anúncios)\n"
        "• Respostas: 3 removidos · 91 recusados · 77 sem resposta\n"
        "• 3 saíram do ar\n"
        "📎 Excel: https://app/x.xlsx"
    )


async def test_link_do_excel_so_daquele_dia_e_por_7_dias():
    link = rt.link_excel(ONTEM, AS_7H07)
    assert "/api/denuncia/relatorios/2026-10-05/excel/link?t=" in link
    t = link.split("t=", 1)[1]
    assert rt.confere_excel(ONTEM, t, AS_7H07)
    assert rt.confere_excel(ONTEM, t, datetime(2026, 10, 12, 12, 0, tzinfo=UTC))   # 6 dias depois
    assert not rt.confere_excel(ONTEM, t, datetime(2026, 10, 13, 11, 0, tzinfo=UTC))  # venceu
    assert not rt.confere_excel(date(2026, 10, 4), t, AS_7H07)   # token de outro dia
    ate, _, assina = t.partition(".")
    assert not rt.confere_excel(ONTEM, f"{int(ate) + 999}.{assina}", AS_7H07)   # prazo mexido
    assert not rt.confere_excel(ONTEM, "", AS_7H07)
    assert not rt.confere_excel(ONTEM, "abc", AS_7H07)


async def test_excel_pelo_link_baixa_sem_login(db, client, auth_as):
    await _dia(db)
    auth_as(None)
    t = rt.token_excel(ONTEM, datetime.now(UTC))
    r = await client.get(f"/api/denuncia/relatorios/2026-10-05/excel/link?t={t}")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("application/vnd.openxmlformats")
    assert r.content[:2] == b"PK"   # xlsx é um zip
    assert (await client.get("/api/denuncia/relatorios/2026-10-05/excel/link?t=1.x")).status_code == 403
    assert (await client.get("/api/denuncia/relatorios/2026-10-05/excel/link")).status_code == 403
    # o Excel normal continua pedindo login
    assert (await client.get("/api/denuncia/relatorios/2026-10-05/excel")).status_code in (401, 403)


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
    assert "/api/denuncia/relatorios/2026-10-05/excel/link?t=" in texto
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


async def test_teste_so_pra_um_e_nao_carimba(db, client, make_user, auth_as, enviados):
    """06/10: "manda só no Cairo um teste… para eu aprovar"."""
    await _dia(db)
    cairo = await make_user(email="cairo@x.com", permissions={"denuncia": {"view": True, "edit": True}})
    cairo.threema = "M5TT27JA"
    await db.commit()
    auth_as(cairo)
    r = await client.post("/api/denuncia/relatorios/2026-10-05/threema/teste", json={"para": "m5tt27ja"})
    assert r.status_code == 200, r.text
    texto, alvos = enviados[0]
    assert alvos == ["M5TT27JA"] and texto.startswith("📊 Relatório geral")
    assert (await db.get(DenunciaRelatorio, ONTEM)).threema_enviado_em is None   # o de verdade segue
    # fora do diretório não manda
    r = await client.post("/api/denuncia/relatorios/2026-10-05/threema/teste", json={"para": "ZZZZ9999"})
    assert r.status_code == 422 and len(enviados) == 1
