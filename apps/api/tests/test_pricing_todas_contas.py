"""Acesso a TODAS as contas só na Tabela de Preços.

Eduardo, 10/09/2026: "para o usuário israel, em tabela de preços, pode fazer
aparecer todas as contas somente na tabela de preços para ele" e "para as
outras abas, não quero que apareça nada das outras contas". israel é da
equipe 2; a cerca por equipe continua valendo em todo o resto do sistema.
"""

from __future__ import annotations

import pytest

from app.models import UserRole
from app.routers.pricing import _escopo_precos

pytestmark = pytest.mark.asyncio


class _User:
    """Usuário mínimo: o helper só olha role, sales_teams e permissions."""

    def __init__(self, permissions: dict | None = None, teams: list | None = None) -> None:
        self.role = UserRole.USER
        self.sales_teams = teams if teams is not None else [102]
        self.permissions = permissions or {}


async def test_com_a_permissao_ve_todas_as_contas(db):
    israel = _User({"tabela_precos_todas_contas": {"view": True, "edit": False, "delete": False}})

    escopo = await _escopo_precos(db, israel)

    assert escopo.unrestricted is True


async def test_sem_a_permissao_continua_cercado_pela_equipe(db):
    colega = _User({"tabela_precos": {"view": True}})

    escopo = await _escopo_precos(db, colega)

    assert escopo.unrestricted is False


async def test_permissao_so_de_edicao_nao_libera(db):
    """A tela mostra três caixinhas; quem libera é a de ver."""
    meio = _User({"tabela_precos_todas_contas": {"view": False, "edit": True, "delete": True}})

    escopo = await _escopo_precos(db, meio)

    assert escopo.unrestricted is False


async def test_permissao_com_formato_estranho_nao_quebra(db):
    """Chave gravada como booleano (formato antigo) não pode derrubar a tela."""
    torto = _User({"tabela_precos_todas_contas": True})

    escopo = await _escopo_precos(db, torto)

    assert escopo.unrestricted is False


async def test_usuario_sem_equipe_segue_vendo_tudo(db):
    """Quem não está em equipe nenhuma nunca foi cercado — não muda nada."""
    sem_equipe = _User({}, teams=[])

    escopo = await _escopo_precos(db, sem_equipe)

    assert escopo.unrestricted is True
