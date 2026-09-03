"""Safety-net de webhooks perdidos (services/bling_orders_safety_net.py):
quais situações entram como candidatas a stale.

21 = Em digitação (etiqueta enviada, canônico desde 03/09/2026); 83965 =
Enviado Etiqueta (legado). As duas têm que continuar sendo varridas até os
pedidos legados drenarem; 15 tem varredura própria (branch "Em andamento").
"""
from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    BlingOrder,
    Integration,
    IntegrationPlatform,
    User,
    UserRole,
    UserStatus,
)
from app.security.cipher import encrypt_json
from app.services import bling_orders_safety_net as sn
from app.services.bling_situacoes import SITUACOES_ENVIADO_ETIQUETA_STR

# ─── Constantes (puro) ────────────────────────────────────────────────────


def test_stale_candidates_cobrem_em_aberto_e_etiqueta_canonica_e_legada():
    assert set(sn._STALE_CANDIDATE_SITUACOES) == {"6", "21", "83965"}


def test_stale_candidates_seguem_a_fonte_unica():
    # Trocar a canônica em bling_situacoes.py tem que refletir aqui sem edição.
    for s in SITUACOES_ENVIADO_ETIQUETA_STR:
        assert s in sn._STALE_CANDIDATE_SITUACOES
    # bling_orders.situacao é TEXT: a tupla é de str, não de int.
    assert all(isinstance(s, str) for s in sn._STALE_CANDIDATE_SITUACOES)


def test_em_andamento_nao_e_candidato_de_envio():
    # 15 tem o próprio branch (janela por em_andamento_data), não este.
    assert sn._SITUACAO_EM_ANDAMENTO not in sn._STALE_CANDIDATE_SITUACOES


# ─── find_stale_order_ids (DB) ────────────────────────────────────────────


async def _bling_integration(db: AsyncSession) -> Integration:
    owner = User(
        open_id=f"email:sn-{uuid.uuid4().hex[:6]}@davinci-test.com",
        email=f"sn-{uuid.uuid4().hex[:6]}@davinci-test.com",
        role=UserRole.ADMIN, status=UserStatus.ACTIVE, permissions={},
    )
    db.add(owner)
    await db.commit()
    await db.refresh(owner)
    integ = Integration(
        user_id=owner.id,
        platform=IntegrationPlatform.BLING,
        name="Bling Test",
        credentials=encrypt_json({
            "access_token": "tok", "refresh_token": "ref",
            "client_id": "cid", "client_secret": "csec",
            "expires_at": int(datetime.now(UTC).timestamp()) + 3600,
        }),
    )
    db.add(integ)
    await db.commit()
    await db.refresh(integ)
    return integ


async def _pedido(
    db: AsyncSession, *, bling_id: int, situacao: str,
    em_andamento_data: date | None = None,
) -> None:
    db.add(BlingOrder(
        bling_id=bling_id, numero=str(bling_id), item_codigo=f"sku-{bling_id}",
        item_index=0, situacao=situacao, data=datetime.now(UTC),
        em_andamento_data=em_andamento_data,
    ))
    await db.commit()


@pytest.mark.asyncio
async def test_find_stale_inclui_6_21_e_83965_sem_data(db: AsyncSession):
    """Envio sem em_andamento_data: 6 (Em aberto), 21 (Em digitação, canônico)
    e 83965 (Enviado Etiqueta, legado) entram; 15 sem data e etiqueta COM
    data ficam fora deste branch."""
    integ = await _bling_integration(db)
    await _pedido(db, bling_id=960001, situacao="6")
    await _pedido(db, bling_id=960002, situacao="21")
    await _pedido(db, bling_id=960003, situacao="83965")
    await _pedido(db, bling_id=960004, situacao="15")
    await _pedido(db, bling_id=960005, situacao="21",
                  em_andamento_data=datetime.now(UTC).date())
    # Sync local "velha" o bastante pra passar a idade mínima (15 min).
    await db.execute(text(
        "UPDATE bling_orders SET updated_at = :ts WHERE bling_id BETWEEN 960001 AND 960005"
    ), {"ts": datetime.now(UTC) - timedelta(minutes=30)})
    await db.commit()

    rows = await sn.find_stale_order_ids(db)
    ids = {bid for bid, _ in rows}
    assert {960001, 960002, 960003} <= ids
    assert not ids & {960004, 960005}
    assert all(uid == integ.user_id for _, uid in rows)
