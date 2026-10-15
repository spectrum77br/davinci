"""Melhor Envio — confere o frete da impressão tipo "próprio" (só Amazon).

Fluxo da spec (áudio 3): depois que a NF é emitida no processo "próprio", vai no
Melhor Envio, cota a etiqueta com os dados do produto e pega o VALOR A PAGAR. Esse
valor tem que estar DENTRO do frete PROJETADO (Tabela de Preços → Contas,
`pricing_accounts.shipping{1..5}`). Se o menor frete cotado couber no projetado, o
sistema LIBERA a geração da etiqueta — ele só libera, NÃO paga nem segue o processo.

Este módulo tem duas camadas:
- PURA (sem rede): `parse_cotacoes` normaliza a resposta do ME, `escolher_menor`
  pega a mais barata válida e `conferir_frete` decide libera/bloqueia vs o
  projetado. É o coração do "confere frete" e roda nos testes sem token.
- CLIENTE: `MelhorEnvioClient.calcular_frete` chama o endpoint de cotação
  (`POST /api/v2/me/shipment/calculate`). Token vem do `.env`
  (`melhor_envio_token`); sem isso levanta MelhorEnvioConfigError.

Doc: https://docs.melhorenvio.com.br — o /calculate é só cotação (não cria carrinho,
não compra, não paga).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING

import httpx
import structlog

from app.config import get_settings

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger()

ME_BASE_PROD = "https://melhorenvio.com.br"
ME_BASE_SANDBOX = "https://sandbox.melhorenvio.com.br"
_CALCULATE_PATH = "/api/v2/me/shipment/calculate"


class MelhorEnvioConfigError(RuntimeError):
    """Token do Melhor Envio ausente."""


class MelhorEnvioApiError(RuntimeError):
    """Falha HTTP ao cotar (status != 2xx)."""

    def __init__(self, status: int, body: str) -> None:
        self.status = status
        self.body = body
        super().__init__(f"melhor_envio_{status}: {body[:160]}")


@dataclass(frozen=True)
class Cotacao:
    """Uma opção de frete cotada no Melhor Envio."""

    servico_id: int | None
    servico_nome: str
    empresa: str
    preco: Decimal | None
    prazo_dias: int | None
    erro: str | None


@dataclass(frozen=True)
class ResultadoConferencia:
    """Decisão do 'confere frete': libera a etiqueta ou não."""

    libera: bool
    motivo: str
    menor_frete: Decimal | None
    servico_escolhido: str | None
    empresa_escolhida: str | None
    prazo_dias: int | None
    frete_projetado: Decimal | None
    diferenca: Decimal | None  # projetado - menor_frete (>0 = folga, <0 = estouro)


def _to_decimal(value: object) -> Decimal | None:
    """Converte preço do ME (str/num) em Decimal; None se não parsear."""
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def parse_cotacoes(payload: object) -> list[Cotacao]:
    """Normaliza a resposta do /calculate (lista de opções) em `Cotacao`.

    Cada item vem `{id, name, price, delivery_time, company:{name}}` ou, quando o
    serviço não atende, `{id, name, error: "..."}` (sem price). Preserva os dois —
    o filtro de válidas fica no `escolher_menor`.
    """
    if not isinstance(payload, list):
        return []
    out: list[Cotacao] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        company = item.get("company")
        empresa = ""
        if isinstance(company, dict):
            empresa = str(company.get("name") or "")
        prazo = item.get("delivery_time")
        if isinstance(prazo, dict):
            prazo = prazo.get("days") or prazo.get("max")
        try:
            prazo_dias = int(prazo) if prazo is not None else None
        except (ValueError, TypeError):
            prazo_dias = None
        sid = item.get("id")
        try:
            servico_id = int(sid) if sid is not None else None
        except (ValueError, TypeError):
            servico_id = None
        out.append(
            Cotacao(
                servico_id=servico_id,
                servico_nome=str(item.get("name") or ""),
                empresa=empresa,
                preco=_to_decimal(item.get("price")),
                prazo_dias=prazo_dias,
                erro=(str(item["error"]) if item.get("error") else None),
            )
        )
    return out


def escolher_menor(cotacoes: list[Cotacao]) -> Cotacao | None:
    """A cotação válida (sem erro, com preço > 0) mais barata; None se não houver."""
    validas = [c for c in cotacoes if c.erro is None and c.preco is not None and c.preco > 0]
    if not validas:
        return None
    return min(validas, key=lambda c: c.preco)  # type: ignore[return-value,arg-type]


def conferir_frete(
    cotacoes: list[Cotacao], frete_projetado: Decimal | None
) -> ResultadoConferencia:
    """Decide se libera a etiqueta: menor frete cotado ≤ frete projetado.

    - sem cotação válida → não libera (`sem_cotacao`).
    - sem frete projetado → não libera (`sem_frete_projetado`) — não dá pra
      confirmar que cabe no orçamento sem o valor de referência.
    - menor ≤ projetado → libera (`dentro_do_projetado`).
    - menor > projetado → não libera (`acima_do_projetado`).
    """
    menor = escolher_menor(cotacoes)
    if menor is None:
        return ResultadoConferencia(
            libera=False,
            motivo="sem_cotacao",
            menor_frete=None,
            servico_escolhido=None,
            empresa_escolhida=None,
            prazo_dias=None,
            frete_projetado=frete_projetado,
            diferenca=None,
        )
    if frete_projetado is None:
        return ResultadoConferencia(
            libera=False,
            motivo="sem_frete_projetado",
            menor_frete=menor.preco,
            servico_escolhido=menor.servico_nome,
            empresa_escolhida=menor.empresa,
            prazo_dias=menor.prazo_dias,
            frete_projetado=None,
            diferenca=None,
        )
    diferenca = frete_projetado - menor.preco  # type: ignore[operator]
    dentro = menor.preco <= frete_projetado  # type: ignore[operator]
    return ResultadoConferencia(
        libera=dentro,
        motivo="dentro_do_projetado" if dentro else "acima_do_projetado",
        menor_frete=menor.preco,
        servico_escolhido=menor.servico_nome,
        empresa_escolhida=menor.empresa,
        prazo_dias=menor.prazo_dias,
        frete_projetado=frete_projetado,
        diferenca=diferenca,
    )


class MelhorEnvioClient:
    def __init__(self, token: str | None = None, sandbox: bool | None = None) -> None:
        s = get_settings()
        self.token = (token if token is not None else s.melhor_envio_token or "").strip()
        self.sandbox = s.melhor_envio_sandbox if sandbox is None else sandbox

    @property
    def base_url(self) -> str:
        return ME_BASE_SANDBOX if self.sandbox else ME_BASE_PROD

    def _require_config(self) -> None:
        if not self.token:
            raise MelhorEnvioConfigError("melhor_envio_token_missing")

    async def calcular_frete(
        self,
        *,
        from_cep: str,
        to_cep: str,
        produtos: list[dict],
        servicos: str | None = None,
    ) -> list[Cotacao]:
        """Cota o frete no Melhor Envio (só consulta, não compra).

        `produtos` = itens no formato do ME
        (`{id,width,height,length,weight,insurance_value,quantity}`, dimensões em
        cm e peso em kg). `servicos` = ids separados por vírgula pra restringir as
        transportadoras (None = todas). Retorna a lista de `Cotacao`.
        """
        self._require_config()
        body: dict = {
            "from": {"postal_code": _so_digitos(from_cep)},
            "to": {"postal_code": _so_digitos(to_cep)},
            "products": produtos,
        }
        if servicos:
            body["services"] = servicos
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "DaVinci NF (contato@davinci)",
        }
        url = f"{self.base_url}{_CALCULATE_PATH}"
        async with httpx.AsyncClient(timeout=30) as cli:
            resp = await cli.post(url, json=body, headers=headers)
        if resp.status_code // 100 != 2:
            logger.warning(
                "melhor_envio_calc_failed", status=resp.status_code, body=resp.text[:200]
            )
            raise MelhorEnvioApiError(resp.status_code, resp.text)
        return parse_cotacoes(resp.json())


def _so_digitos(cep: str | None) -> str:
    return "".join(ch for ch in (cep or "") if ch.isdigit())


# ---------------------------------------------------------------------------
# Token guardado no painel (15/10/2026) + leituras só-leitura da conta e dos envios
# ---------------------------------------------------------------------------
CHAVE_CONFIG_ME = "melhor_envio_token"
_UA = "DaVinci Conciliacao (contato@davinci)"


async def token_do_painel(session: AsyncSession) -> tuple[str | None, dict]:
    """Token colado em Conciliação › Amazon › Melhor Envio (cifrado), com o detalhe
    (quando, quem, conta testada). Sem linha → (None, {})."""
    from app.models import ConfiguracaoSegura
    from app.security.cipher import decrypt_json

    row = await session.get(ConfiguracaoSegura, CHAVE_CONFIG_ME)
    if row is None:
        return None, {}
    try:
        dados = decrypt_json(row.valor)
    except Exception:  # noqa: BLE001
        return None, {"erro": "token guardado ilegível"}
    detalhe = {
        **(row.detalhe or {}),
        "atualizado_em": row.updated_at.isoformat() if row.updated_at else None,
        "atualizado_por": row.atualizado_por,
    }
    return (str(dados.get("token") or "").strip() or None), detalhe


async def guardar_token_do_painel(
    session: AsyncSession, token: str, *, por: str | None, detalhe: dict | None = None
) -> None:
    from app.models import ConfiguracaoSegura
    from app.security.cipher import encrypt_json

    row = await session.get(ConfiguracaoSegura, CHAVE_CONFIG_ME)
    if row is None:
        row = ConfiguracaoSegura(chave=CHAVE_CONFIG_ME, valor=b"", detalhe={})
        session.add(row)
    row.valor = encrypt_json({"token": token.strip()})
    row.detalhe = dict(detalhe or {})
    row.atualizado_por = por
    await session.flush()


async def apagar_token_do_painel(session: AsyncSession) -> bool:
    from app.models import ConfiguracaoSegura

    row = await session.get(ConfiguracaoSegura, CHAVE_CONFIG_ME)
    if row is None:
        return False
    await session.delete(row)
    await session.flush()
    return True


async def cliente_me(session: AsyncSession) -> MelhorEnvioClient:
    """Cliente com o token do painel; sem ele, o do `.env` (pode estar vencido)."""
    token, _ = await token_do_painel(session)
    return MelhorEnvioClient(token=token) if token else MelhorEnvioClient()


def _headers_leitura(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Accept": "application/json", "User-Agent": _UA}


async def ler_conta(token: str) -> tuple[int, dict]:
    """GET /api/v2/me — quem é o dono do token (só nome/empresa são usados)."""
    async with httpx.AsyncClient(timeout=20) as cli:
        r = await cli.get(f"{ME_BASE_PROD}/api/v2/me", headers=_headers_leitura(token))
    try:
        corpo = r.json() if r.content else {}
    except ValueError:
        corpo = {"texto": r.text[:200]}
    return r.status_code, corpo if isinstance(corpo, dict) else {"corpo": corpo}


async def ler_envios(token: str, *, page: int = 1, q: str | None = None) -> tuple[int, dict]:
    """GET /api/v2/me/orders — os envios da conta (preço pago, etiquetas, tags com o
    nº do pedido). Só leitura."""
    params: dict[str, str | int] = {"page": page}
    if q:
        params["q"] = q
    async with httpx.AsyncClient(timeout=30) as cli:
        r = await cli.get(
            f"{ME_BASE_PROD}/api/v2/me/orders", headers=_headers_leitura(token), params=params
        )
    try:
        corpo = r.json() if r.content else {}
    except ValueError:
        corpo = {"texto": r.text[:200]}
    return r.status_code, corpo if isinstance(corpo, dict) else {"data": corpo}


def nome_da_conta(corpo: dict) -> str | None:
    """Nome curto da conta pra mostrar na tela (sem e-mail/documento)."""
    empresa = corpo.get("company") if isinstance(corpo.get("company"), dict) else None
    if empresa and empresa.get("name"):
        return str(empresa["name"])[:80]
    nome = " ".join(str(corpo.get(k) or "") for k in ("firstname", "lastname")).strip()
    return nome[:80] or None
