"""Sonda da Open API da Temu — só leitura (26/11/2026).

Pedido do Vinicius: antes de pôr a Temu na Logística e nas Devoluções, ver na
API da loja Barbosa (integração do Marketing, app "Stock Sync Hub") o que a
Temu devolve de pedido, envio, rastreio e pós-venda, comparando com a tela do
Seller Center. Primeiros casos: 306833 (extraviado pela Anjun, reembolsado sem
devolução), 307603 e 308799 (rastreio parado).

- Só as APIs de `APIS_LEITURA_PEDIDOS` (consultas). O cliente da Temu recusa
  qualquer outra coisa antes de sair; aqui a lista é conferida de novo.
- Dado do comprador nunca volta: endereço, nome, telefone, e-mail e documento
  saem como "***" (a política da Temu manda não guardar; a sonda não guarda
  nada, mas a resposta passa pelo navegador de quem chamou).
- Não grava nada no banco.
"""

from __future__ import annotations

import re
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Integration, IntegrationPlatform
from app.security.cipher import decrypt_json
from app.services.marketplaces.temu import APIS_LEITURA_PEDIDOS, TemuClient, TemuErro

OCULTO = "***"

# Chave que carrega dado do comprador (nome, contato, documento).
# `goodsName`, `skuName` etc. passam: não casam com nada daqui.
_CHAVE_PESSOAL = re.compile(
    r"receipt|receiver|consignee|buyer|recipient|"
    r"mobile|phone|telephone|email|e_mail|^mail$|"
    r"cpf|cnpj|document|tax_?(code|number|id)|id_?card|"
    r"first_?name|last_?name|full_?name|^name$",
    re.IGNORECASE,
)
# Endereço: oculto em tudo, menos no rastreio, onde o "endereço" do evento é a
# cidade da base da transportadora (é isso que mostra o pacote parado).
_CHAVE_ENDERECO = re.compile(r"address|addr|street|house|zip|post_?code|postal", re.IGNORECASE)
_APIS_COM_LOCAL_DO_EVENTO = frozenset({"temu.track.trackinginfo.get"})


class SondaRecusada(ValueError):  # noqa: N818 — nome em português, como o resto
    """API fora da lista de consultas, ou integração que não serve."""


def ocultar_pessoal(valor: Any, *, manter_endereco: bool = False) -> Any:
    """Copia a resposta trocando o valor de toda chave pessoal por "***"
    (inclusive quando o valor é um objeto ou lista inteira)."""
    if isinstance(valor, dict):
        saida: dict[str, Any] = {}
        for k, v in valor.items():
            pessoal = isinstance(k, str) and (
                _CHAVE_PESSOAL.search(k) or (not manter_endereco and _CHAVE_ENDERECO.search(k))
            )
            if pessoal and v not in (None, "", [], {}):
                saida[k] = OCULTO
            else:
                saida[k] = ocultar_pessoal(v, manter_endereco=manter_endereco)
        return saida
    if isinstance(valor, list):
        return [ocultar_pessoal(v, manter_endereco=manter_endereco) for v in valor]
    return valor


async def _integracao(session: AsyncSession, integration_id: UUID | None) -> Integration:
    q = select(Integration).where(
        Integration.platform == IntegrationPlatform.TEMU,
        Integration.archived_at.is_(None),
    )
    if integration_id is not None:
        q = q.where(Integration.id == integration_id)
    achadas = list((await session.execute(q)).scalars().all())
    if not achadas:
        raise SondaRecusada("nenhuma integração Temu ativa")
    if len(achadas) > 1:
        raise SondaRecusada("mais de uma integração Temu: informe integration_id")
    return achadas[0]


async def sondar(
    session: AsyncSession,
    api: str,
    params: dict[str, Any] | None = None,
    integration_id: UUID | None = None,
) -> dict[str, Any]:
    """Chama UMA consulta da Temu e devolve a resposta sem dado pessoal."""
    api = (api or "").strip()
    if api not in APIS_LEITURA_PEDIDOS:
        raise SondaRecusada(f"{api or '(vazio)'} não é uma das consultas liberadas")
    integ = await _integracao(session, integration_id)
    cliente = TemuClient(decrypt_json(integ.credentials))
    try:
        corpo = await cliente._chamar(api, dict(params or {}))  # noqa: SLF001
    except TemuErro as e:
        return {"integracao": integ.name, "api": api, "ok": False, "erro": str(e)}
    return {
        "integracao": integ.name,
        "api": api,
        "ok": True,
        "resposta": ocultar_pessoal(
            corpo.get("result", corpo), manter_endereco=api in _APIS_COM_LOCAL_DO_EVENTO
        ),
    }
