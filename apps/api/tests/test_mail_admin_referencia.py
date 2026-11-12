# ruff: noqa: F401, F811 — fixtures da ponte compartilhadas
"""A busca indexada do original preserva a autorização das cópias legadas."""

from uuid import uuid4

import pytest
from sqlalchemy import select

from app.models import AtendimentoConversa, AtendimentoMensagem, UserRole
from app.models.mail_admin import MailLixeira
from app.services.mail_atendimento import administracao
from tests.test_mail_ponte import AGORA, _config, cena
from tests.test_mail_venda_rotas import _caso


@pytest.mark.parametrize(
    "referencia",
    [
        "original", "orfao", "invalido", "maiuscula", "espacos",
        "quebra", "sem_hifens", "vazia", None,
    ],
)
async def test_referencia_json_valida_usa_original_e_invalida_mantem_fechamento(
    db, cena, client, make_user, auth_as, referencia
):
    meta = await _caso(db, cena)
    conversa_id, mensagem_id, original_id = meta.conversa_id, meta.mensagem_id, meta.message_id
    pessoa = await make_user(role=UserRole.USER)
    mensagem = await db.get(AtendimentoMensagem, mensagem_id)
    valor = {
        "original": str(original_id),
        "orfao": str(uuid4()),
        "invalido": "não-é-uuid'::uuid",
        "maiuscula": str(original_id).upper(),
        "espacos": str(original_id) + " ",
        "quebra": str(original_id) + "\n",
        "sem_hifens": str(original_id).replace("-", ""),
        "vazia": "",
        None: None,
    }[referencia]
    mensagem.payload = {
        **mensagem.payload,
        "mail": {**mensagem.payload["mail"], "message_id": valor},
    }
    # A ligação auxiliar desapareceu após reprocessar. Só a cópia pode provar
    # o original excluído (12/11/2026: sem regra por pessoa, não há "restrita").
    await db.delete(meta)
    db.add(MailLixeira(message_id=original_id, excluido_em=AGORA, excluido_por=cena.dono.id))
    await db.commit()
    visivel = await db.scalar(
        select(AtendimentoConversa.id).where(
            AtendimentoConversa.id == conversa_id,
            administracao.filtro_conversa(pessoa),
        )
    )
    assert (visivel is None) == (valor == str(original_id))
