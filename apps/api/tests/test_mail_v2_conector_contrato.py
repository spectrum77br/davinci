"""O que o conector do Tuta (Rust, `apps/tuta-conector`) manda de VERDADE passa
na Central de e-mail — 08/10/2026.

O conector grava as amostras do que manda (`tests/dados/contrato-amostras.json`,
pelo teste `contrato_amostras_para_a_central` dele, contra o Tuta falso
cifrado de verdade). Aqui elas passam pelos schemas da Central (o v1 do outro
dev, congelado, e o v2 nosso) e pelas rotas de verdade: o sinal, o /v2/sync,
o /v2/ingest (o e-mail entra e a ponte lê o bloco do Tuta), a contagem, as
mudanças e o recibo. Mudou a forma de um lado = este teste quebra.

Sem o diretório do conector no checkout, pula.
"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import select

from app.models import UserRole
from app.models.mail import MailMessage
from app.models.mail_atendimento import MailMessageTuta
from app.schemas.mail import Heartbeat, Receipt
from app.schemas.mail_v2 import ChangesIn, CountIn, IngestV2, MessageInV2, SyncIn
from app.security.cipher import decrypt_json
from app.services.mail_atendimento import pastas

AMOSTRAS = (
    Path(__file__).resolve().parents[2]
    / "tuta-conector"
    / "tests"
    / "dados"
    / "contrato-amostras.json"
)

pytestmark = pytest.mark.skipif(not AMOSTRAS.exists(), reason="sem apps/tuta-conector no checkout")


def _amostras() -> dict:
    return json.loads(AMOSTRAS.read_text(encoding="utf-8"))


def test_as_amostras_passam_nos_schemas():
    a = _amostras()
    Heartbeat.model_validate(a["heartbeat"])
    for sinal in a["estados"]:
        Heartbeat.model_validate(sinal)
    Receipt.model_validate(a["receipt"])
    SyncIn.model_validate(a["sync"])
    corpo = IngestV2.model_validate(a["ingest"])
    for item in corpo.messages:
        m = MessageInV2.model_validate(item)
        assert m.source_id == f"tuta:{m.tuta.mail_id}"
        assert m.text_from_html
    CountIn.model_validate(a["count"])
    ChangesIn.model_validate(a["changes"])
    # Os estados do conector viram só os 3 da Central.
    assert {s["state"] for s in a["estados"]} == {"online", "login_required", "error"}


@pytest.fixture
async def caixa(client, make_user, auth_as):
    admin = await make_user(role=UserRole.ADMIN)
    auth_as(admin)
    r = await client.post(
        "/api/mail/mailboxes",
        json={"label": "Geral — Tuta", "address": "geral@tuta.com", "aliases": ["21max@tuta.com"]},
    )
    assert r.status_code == 201, r.text
    box = r.json()
    return {
        "id": box["id"],
        "base": f"/api/mail/agent/{box['id']}",
        "headers": {"Authorization": f"Bearer {box['agent_token']}"},
    }


async def test_as_amostras_entram_pelas_rotas_de_verdade(client, caixa, db):
    a = _amostras()
    h = caixa["headers"]
    r = await client.post(f"{caixa['base']}/heartbeat", headers=h, json=a["heartbeat"])
    assert r.status_code == 200, r.text
    r = await client.post(f"{caixa['base']}/v2/sync", headers=h, json=a["sync"])
    assert r.status_code == 200, r.text
    leitura = {f["key"]: f["read"] for f in r.json()["folders"]}
    item = a["ingest"]["messages"][0]
    assert leitura[item["tuta"]["folder_key"]] == "corpo"
    r = await client.post(f"{caixa['base']}/v2/ingest", headers=h, json=a["ingest"])
    assert r.status_code == 200, r.text
    assert [x["status"] for x in r.json()["results"]] == ["accepted"]
    m = await db.scalar(
        select(MailMessage).where(
            MailMessage.mailbox_id == UUID(caixa["id"]), MailMessage.source_id == item["source_id"]
        )
    )
    conteudo = decrypt_json(m.content_enc)
    # A ponte lê o que o conector mandou (pasta pela chave, o id do Tuta).
    assert pastas.do_conteudo(conteudo).chave == item["tuta"]["folder_key"]
    assert m.attachment_count == len(item["attachments"])
    assert await db.get(MailMessageTuta, m.id) is not None
    r = await client.post(f"{caixa['base']}/v2/count", headers=h, json=a["count"])
    assert r.status_code == 200, r.text
    r = await client.post(f"{caixa['base']}/v2/changes", headers=h, json=a["changes"])
    assert r.status_code == 200, r.text
    assert r.json() == {"updated": 1, "unknown": 0}
