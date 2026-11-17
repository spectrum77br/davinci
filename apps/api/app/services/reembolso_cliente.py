"""As regras do reembolso ao cliente (Novos procedimentos 3/6, decisões do dono de 17/11/2026).

O reembolso tipo Cliente continua na tabela `refunds` (a tela Pós-venda ›
Reembolso), agora também lançado pela aba Pedido da conversa do Atendimento
(`services/atendimento/reembolso_conversa.py`). Aqui fica só o que as duas
telas precisam concordar — sem banco:

  • MOTIVOS e FORMAS (os códigos que a API aceita e o rótulo da tela);
  • a APROVAÇÃO DO ADM (`requer_aprovacao`): acima de R$ 100, OU acima de 25%
    do valor pago pelo comprador, OU o valor inteiro (>= o pago). Sem o valor
    pago não dá para conferir os 25%: vai para o ADM (o lado seguro);
  • a SITUAÇÃO (`situacao`): Aguardando ADM (pede aprovação e ainda não tem)
    → Em aberto (`conferido` false) → Finalizado (`conferido` true, marcado por
    quem paga na tela Reembolso). "Não aplicado" é da frente 4/6 — não existe
    aqui de propósito;
  • os DADOS DO PIX protegidos (`proteger_dados_pagamento` /
    `ler_dados_pagamento`): gravados cifrados (AES-GCM, a mesma chave das
    credenciais), lidos só para ADM. Cifrados porque o gatilho do Histórico
    copia a linha inteira de `refunds` para `historico_alteracao` — com a cifra,
    o Pix não vai para lá (nem para os backups) em texto. Nunca vão para log.

Valores em CENTAVOS nas contas (sem erro de float no "> 25%").
"""

from __future__ import annotations

from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Protocol

import structlog

from app.security import cipher

logger = structlog.get_logger()

TIPO_CLIENTE = "Cliente"
ORIGEM_ATENDIMENTO = "atendimento"

# A lista do dono (17/11/2026), na ordem da tela.
MOTIVOS: dict[str, str] = {
    "defeito_avaria": "Defeito ou avaria",
    "produto_errado": "Produto errado ou cor trocada",
    "faltou_peca": "Faltou peça",
    "frete": "Frete",
    "outro": "Outro",
}
MOTIVO_OUTRO = "outro"
FORMAS: dict[str, str] = {"pix": "Pix", "cupom": "Cupom"}
FORMA_PIX = "pix"

# Acima disto o ADM aprova (decisão 3): R$ 100 ou 25% do valor pago.
LIMITE_VALOR = 100
LIMITE_PCT = 25

SITUACAO_AGUARDANDO_ADM = "aguardando_adm"
SITUACAO_EM_ABERTO = "em_aberto"
SITUACAO_FINALIZADO = "finalizado"
ROTULO_SITUACAO: dict[str, str] = {
    SITUACAO_AGUARDANDO_ADM: "Aguardando ADM",
    SITUACAO_EM_ABERTO: "Em aberto",
    SITUACAO_FINALIZADO: "Finalizado",
}

# Teto dos textos livres (é recado para quem paga, não documento).
MAX_DETALHE = 1000
MAX_DADOS_PAGAMENTO = 500


class _ComSituacao(Protocol):
    conferido: bool
    requer_aprovacao: bool | None
    aprovado_em: datetime | None


def centavos(valor: Any) -> int | None:
    """O valor em centavos (arredonda meio para cima); None quando não é número."""
    if valor is None or isinstance(valor, bool):
        return None
    try:
        d = Decimal(str(valor))
    except (InvalidOperation, ValueError):
        return None
    if not d.is_finite():
        return None
    return int((d * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def requer_aprovacao(valor_centavos: int, pago_centavos: int | None) -> bool:
    """O ADM precisa aprovar? (decisão 3, 17/11/2026). PURA.

    Acima de R$ 100, OU acima de 25% do valor pago, OU o valor inteiro (>= o
    pago). Sem o valor pago (ou zerado), não dá para conferir os 25%: o ADM
    confere.
    """
    if valor_centavos > LIMITE_VALOR * 100:
        return True
    if pago_centavos is None or pago_centavos <= 0:
        return True
    if valor_centavos * 100 > LIMITE_PCT * pago_centavos:
        return True
    return valor_centavos >= pago_centavos


def aguardando_adm(r: _ComSituacao) -> bool:
    """Pede o ADM e ainda não foi aprovado: não pode ser Finalizado."""
    return bool(r.requer_aprovacao) and r.aprovado_em is None


def situacao(r: _ComSituacao) -> str:
    """A situação do reembolso (decisão 8): aguardando_adm → em_aberto → finalizado.

    Finalizado vem primeiro: é o fato do pagamento (a API não deixa finalizar
    o que aguarda o ADM, então as duas marcas juntas não acontecem por ela).
    """
    if r.conferido:
        return SITUACAO_FINALIZADO
    if aguardando_adm(r):
        return SITUACAO_AGUARDANDO_ADM
    return SITUACAO_EM_ABERTO


def texto_limpo(valor: Any) -> str | None:
    """Texto sem as pontas; vazio vira None."""
    if valor is None:
        return None
    texto = str(valor).strip()
    return texto or None


def proteger_dados_pagamento(texto: str | None) -> str | None:
    """Os dados do Pix cifrados para gravar (None continua None)."""
    limpo = texto_limpo(texto)
    return cipher.encrypt(limpo) if limpo is not None else None


def ler_dados_pagamento(cifra: str | None, *, refund_id: Any = None) -> str | None:
    """Os dados do Pix em texto — SÓ para quem é ADM (quem chama confere).

    A cifra que não abre (chave trocada, linha mexida à mão) volta None, com
    um aviso no log só com o id — nunca o conteúdo.
    """
    if not cifra:
        return None
    try:
        return cipher.decrypt(cifra)
    except Exception as e:  # noqa: BLE001 — a tela segue sem o Pix; o dado nunca vai ao log
        logger.warning(
            "reembolso_dados_pagamento_ilegivel",
            refund_id=str(refund_id) if refund_id is not None else None,
            err=type(e).__name__,
        )
        return None
