"""Log de cada ida à NFE.io em `nfse_chamada` (01/10/2026).

Era `emissao._log`; veio para cá porque a integração de empresas
(`integracao.py`) também registra os POSTs dela e não pode importar `emissao`
(que importa `empresas`). Só operação, HTTP, códigos e tempo: nunca a chave, o
corpo (certificado, senha) ou dado do tomador.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.nfse import NfseChamada
from app.services.nfse import nfeio


def registrar(
    session: AsyncSession,
    r: nfeio.Resposta | None,
    company_id: UUID | None,
    emissao_id: UUID | None,
    ambiente: str | None,
) -> None:
    """Uma linha por ida à NFE.io: operação, HTTP, códigos e tempo. Nunca a
    chave, o corpo ou dado do tomador."""
    if r is None:
        return
    session.add(
        NfseChamada(
            company_id=company_id,
            emissao_id=emissao_id,
            operacao=r.operacao,
            ambiente=ambiente,
            http_status=r.status,
            codigos=r.codigos or None,
            erro=r.erro_rede,
            duracao_ms=r.duracao_ms,
        )
    )
