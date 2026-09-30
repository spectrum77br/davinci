"""Trava de produção da NFS-e pela NFE.io (29/09/2026).

Na NFE.io não existe "modo teste" na nota: teste ou produção é da Inscrição
Municipal de cada empresa (`environment`). Empresa em `Development` (ou
`Staging`) gera nota simulada e sempre pode; empresa em `Production` gera nota
REAL e só sai com `ENV=production` E `NFSE_PRODUCAO_LIBERADA=true`. No
localhost o `.env` não libera: nenhuma nota real sai daqui.
"""

from __future__ import annotations

from app.config import get_settings

PRODUCAO = "Production"
# Ambientes em que a NFE.io simula a prefeitura (nota sem valor fiscal).
AMBIENTES_DE_TESTE = ("Development", "Staging")

MSG_PRODUCAO_BLOQUEADA = (
    "Esta empresa está em PRODUÇÃO na NFE.io (a nota seria real) e este servidor "
    "não está liberado para emitir nota real."
)


def producao_liberada() -> bool:
    s = get_settings()
    return bool(s.is_prod and s.nfse_producao_liberada)


def eh_teste(ambiente: str | None) -> bool:
    """Selo "TESTE — nota simulada". Ambiente desconhecido não é teste."""
    return ambiente in AMBIENTES_DE_TESTE


def pode_emitir(ambiente_da_empresa: str | None) -> bool:
    """Development/Staging sempre; qualquer outro valor (Production, vazio ou
    desconhecido) é tratado como nota real e só com a produção liberada."""
    if eh_teste(ambiente_da_empresa):
        return True
    return producao_liberada()
