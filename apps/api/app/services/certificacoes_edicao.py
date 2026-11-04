"""Separa o cadastro editável dos dados originais consultados nas fontes oficiais."""

from app.models.financeiro import FinanceiroSuprimentos


def aplicar_campos_oficiais(row: FinanceiroSuprimentos, values: dict) -> None:
    """Atualiza somente campos que o usuário ainda não cadastrou/corrigiu."""
    manuais = set(row.campos_manuais or [])
    for field, value in values.items():
        if field not in manuais:
            setattr(row, field, value)
