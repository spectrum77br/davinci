"""Os AVISOS dos assuntos sensíveis (13/11/2026, `alertas_sensiveis.py` e `regua.aplicar`).

O Eduardo: cada envio sozinho de assunto sensível avisa (e entra na revisão diária); e ele
quer saber quando um assunto sensível passa de 90%. Nada de texto de cliente no aviso.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AtendimentoRascunho
from app.services.atendimento import alertas_sensiveis, constantes, gravar
from app.services.atendimento.agente import custo
from tests.test_atendimento_ia import _conversa


async def _envio_da_ia(db: AsyncSession, conversa, categoria: str, n: str, quando: datetime):
    r = AtendimentoRascunho(
        conversa_id=conversa.id,
        texto="Seu reembolso é feito pela Shopee.",
        categoria=categoria,
        status="enviado",
    )
    db.add(r)
    await db.flush()
    m, _ = await gravar.gravar_mensagem(
        db,
        conversa,
        externo_id=f"{conversa.externo_id}-{n}",
        autor="loja",
        texto="Seu reembolso é feito pela Shopee, Maria.",
        enviada_em=quando,
        origem=constantes.ORIGEM_IA,
    )
    m.rascunho_id = r.id
    await db.commit()


def test_as_frases_nao_levam_texto_de_cliente():
    f = alertas_sensiveis.frase_do_envio(
        "reembolso", "Kia", "shopee", "https://x/atendimento?conversa=1"
    )
    assert "Reembolso" in f and "Kia · shopee" in f and "conversa=1" in f
    r = alertas_sensiveis.aviso_da_regua("cancelamento", "Vita", 0.93, 30)
    assert "Cancelamento" in r and "93%" in r and "30 decisões" in r and "Vita" in r


async def test_um_aviso_por_envio_sensivel_e_o_cursor_anda(
    db: AsyncSession, make_user, monkeypatch
):
    from app.redis_client import redis

    avisos: list[str] = []

    async def falso(texto):
        avisos.append(texto)
        return True

    monkeypatch.setattr(custo, "avisar_telegram", falso)
    await redis.delete(alertas_sensiveis.CHAVE_CURSOR)
    agora = datetime.now(UTC)
    conversa = await _conversa(db, make_user, comprador_nome="Maria Souza")
    await _envio_da_ia(db, conversa, "reembolso", "s1", agora - timedelta(minutes=3))
    await _envio_da_ia(db, conversa, "rastreio", "n1", agora - timedelta(minutes=2))

    assert await alertas_sensiveis.avisar_envios_sensiveis(db, agora=agora) == 1
    (aviso,) = avisos
    assert "Reembolso" in aviso and str(conversa.id) in aviso
    assert "Maria" not in aviso and "Shopee, Maria" not in aviso
    # A próxima rodada não repete.
    assert (
        await alertas_sensiveis.avisar_envios_sensiveis(db, agora=agora + timedelta(minutes=5)) == 0
    )
    assert len(avisos) == 1
