"""A NF do SAC dos sites Charlots e Uranyx existe no DaVinci? (Eduardo, 27/10/2026)

"No SAC dos dois sites o número da NF passa a ser obrigatório; ao enviar, o
site pergunta ao DaVinci se a NF existe; se não existir, o SAC não é enviado"
e o cliente vai para o "Fale com a gente". Servidor a servidor: o PHP do site
(Hostinger) chama com o MESMO Bearer do `/api/sites/estoque`; o navegador
nunca fala com o DaVinci (sem CORS, sem captcha: o site já tem honeypot e
limite por IP antes de chamar).

## Contrato

    POST /api/sites/validar-nf
    Authorization: Bearer <token do site>   (SITES_ESTOQUE_TOKENS: o token diz o site)
    {"nf": "<como o cliente digitou>", "data_compra": "AAAA-MM-DD", "pedido": "<opcional>"}

- **200 `{"encontrada": true|false}`** e nada mais: nunca dado do cliente,
  nunca eco do que veio. NF que não existe, NF de outra marca e NF que existe
  mas com data/pedido que não batem dão o MESMO `false` (mesma consulta, mesma
  forma de resposta).
- **409 `sites_nf_sem_cobertura`**: não achou E a compra é de antes de
  `COBERTURA_DESDE`. O DaVinci não tem as NFs dessa época completas (medido
  em 27/10/2026, abaixo), então não sabe dizer "não existe". O site trata
  como qualquer não-200: aceita o SAC marcado "NF não conferida".
- 401 token (o de `sites_estoque`), 422 corpo malformado (`code` + `campo`,
  nunca o valor), 429 passou do limite do site.

`pedido` vazio, ausente, nulo ou estranho = ausente (nunca 422). Campos a mais
são ignorados.

## Onde estão as NFs (medido em produção, 27/10/2026)

- `nf_nota` (XML das notas, coletor desde 19/08/2026): 19,4 mil notas
  autorizadas (`situacao = '100'`), 22 emitentes, séries 1 a 5, número de 1 a
  4 dígitos SEM zero à esquerda; `pedido_bling` liga ao pedido. Cobre ~92%
  dos pedidos entregues de set/2026 e ~98% de out/2026.
- `bling_notas_emitidas` (contas de emissão do Pós Vendas, jan a ago/2026):
  14,7 mil notas, número com 6 dígitos e zeros à esquerda; `complemento` é o
  nº do pedido no marketplace (`bling_orders.numeroloja`). Só a Uranyx, e só
  ~35-55% dos pedidos de fev-jul. Antes de 2026, nada — por isso o 409.

## A regra

1. **Número** (`numeros_da_nf`): só dígitos e sem zero à esquerda, dos dois
   lados; série ignorada. Os candidatos são o texto inteiro só em dígitos e
   cada grupo de dígitos separado por algo que não seja ponto ("000.012.345"
   → 12345; "1/12345" → 12345, 1 e 112345). Os 44 dígitos da chave de acesso
   valem pela chave e pelo nNF dela.
2. **Marca** (`MARCA_SKU`): a NF tem de ser de um pedido do Bling com pelo
   menos um item da marca do token, fora das lojas `interno`. Uranyx: `u*`,
   `dg<dígito>` e acessório `a0NN.<sufixo>` (fone, carregador, relógio — os de
   celular têm sufixo regional); Charlots: `b<dígito>`, `bp<dígito>` e os
   acessórios de mala sem sufixo de `sku_tags` (rodinha, balança, chaveiro).
   Salvado (`z*`), Apple (`i*`) e o resto não contam para nenhuma.
3. **2º fator** (`confere`), qualquer um basta:
   - a chave de acesso inteira (44 dígitos) bateu;
   - o `pedido` bate com o nº do pedido no Bling ou no marketplace (só letras
     e dígitos, sem diferenciar maiúsculas, zero à esquerda ignorado);
   - a `data_compra` cai entre `DIAS_ANTES` dias antes da mais cedo e
     `DIAS_DEPOIS` dias depois da mais tarde entre a data do pedido e a da
     emissão da NF (dias de São Paulo). Folgado de propósito: `false` barra um
     cliente de verdade, e quem confunde costuma pôr a data da entrega.

O número sozinho NÃO prova nada: a NF tem 1 a 4 dígitos e, numa janela de 21
dias, 2.765 números distintos da Uranyx (908 dos 1.000 primeiros). Esta porta
barra número inventado alto, NF de outra empresa e compra de outra época — não
fraude de quem sabe o que digita. O pedido é o fator forte.

## As travas

- Token e recorte: `site_do_token` (fecha por padrão; site sem marca → 401).
- 30 chamadas/min por site (o site já limita 6/h por IP). Redis fora do ar
  não vira 500: falha aberto, com log.
- Fora do openapi; corpo até 4 KiB; resposta `Cache-Control: no-store`.
- Log só com site, resultado, por onde casou e o tempo. Nunca a NF, o pedido,
  a data ou o IP do cliente.
- Só leitura, sem migration: as duas tabelas de NF são varridas (~25 ms em
  produção com 34 mil notas); o pedido entra pelos índices de `numero` e
  `numeroloja`.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Annotated, Any
from zoneinfo import ZoneInfo

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.routers.sites_estoque import site_do_token
from app.services.rate_limit import RateLimitError, sliding_window_check
from app.services.sku_tags import _SKU_TAG_OVERRIDES

logger = structlog.get_logger()

# Fora do openapi, como as outras portas dos sites.
router = APIRouter(prefix="/api/sites", tags=["sites"], include_in_schema=False)

FUSO = ZoneInfo("America/Sao_Paulo")

LIMITE_CHAMADAS_MIN = 30
MAX_CORPO = 4096
MAX_NF = 60
MAX_DIGITOS = 44
MAX_PEDIDO = 100
MAX_CANDIDATOS = 6
# O número da NF-e (nNF) tem até 9 dígitos.
MAX_DIGITOS_NUMERO = 9
DIAS_ANTES = 5
DIAS_DEPOIS = 15
# Antes disto o DaVinci não tem as NFs completas (o coletor do `nf_nota`
# começou em 19/08/2026 e só pegou o mês inteiro a partir de setembro).
COBERTURA_DESDE = date(2026, 9, 1)
DATA_MINIMA = date(2015, 1, 1)

# Acessórios de mala sem sufixo (a006, a015, a073…a080): a mesma lista que põe
# esses SKUs no time da mala.
ACESSORIOS_MALA: tuple[str, ...] = tuple(
    sorted(
        s
        for s, tag in _SKU_TAG_OVERRIDES.items()
        if tag == "mala" and re.fullmatch(r"a0[0-9]{2}", s)
    )
)
# Sobre `lower(item_codigo)`; `(^|\+)` olha também cada componente de kit.
MARCA_SKU: dict[str, str] = {
    "uranyx": r"(^|\+)(u|dg[0-9]|a0[0-9]{2}\.)",
    "charlots": r"(^|\+)(b[0-9]|bp[0-9]|(" + "|".join(ACESSORIOS_MALA) + r")(\+|$))",
}

_SEM_CACHE = {"Cache-Control": "no-store"}

_RX_GRUPO = re.compile(r"[0-9][0-9.]*")
_RX_DATA_ISO = re.compile(r"([0-9]{4})-([0-9]{2})-([0-9]{2})(?:[T ][0-9:.+\-Z]*)?")
_RX_DATA_BR = re.compile(r"([0-9]{2})/([0-9]{2})/([0-9]{4})")

_SQL = text(
    """
    WITH notas AS (
        SELECT n.pedido_bling AS numero,
               (n.data_emissao AT TIME ZONE 'America/Sao_Paulo')::date AS nf_dia,
               coalesce(n.chave = :chave, false) AS pela_chave
        FROM nf_nota n
        WHERE n.situacao = '100'
          AND n.pedido_bling IS NOT NULL
          AND (ltrim(n.numero, '0') = ANY(:numeros) OR n.chave = :chave)
        UNION ALL
        SELECT o.numero,
               b.data_emissao::date,
               coalesce(b.chave_acesso = :chave, false)
        FROM bling_notas_emitidas b
        JOIN bling_orders o ON o.numeroloja = b.complemento
        WHERE b.situacao IN (5, 6, 7)
          AND b.complemento IS NOT NULL
          AND o.numero IS NOT NULL
          AND (ltrim(b.numero, '0') = ANY(:numeros) OR b.chave_acesso = :chave)
    )
    SELECT nt.numero,
           nt.nf_dia,
           bool_or(nt.pela_chave) AS pela_chave,
           min((o.data AT TIME ZONE 'America/Sao_Paulo')::date) AS pedido_dia,
           array_agg(DISTINCT o.numeroloja) FILTER (WHERE o.numeroloja IS NOT NULL) AS lojas
    FROM notas nt
    JOIN bling_orders o ON o.numero = nt.numero
    WHERE NOT EXISTS (
        SELECT 1 FROM store_info si
        WHERE si.bling_store_id = o.loja AND lower(btrim(si.platform)) = 'interno'
    )
    GROUP BY nt.numero, nt.nf_dia
    HAVING bool_or(lower(o.item_codigo) ~ :marca)
    LIMIT 200
    """
)


@dataclass(frozen=True)
class Consulta:
    numeros: list[str]
    chave: str | None
    data_compra: date
    pedido: str | None


@dataclass(frozen=True)
class Achado:
    """Uma NF candidata da marca: o pedido dela e as duas datas."""

    numero: str
    lojas: tuple[str, ...]
    pedido_dia: date | None
    nf_dia: date | None
    pela_chave: bool = False


def _invalido(campo: str) -> HTTPException:
    # Só o nome do campo: nunca o valor que veio.
    return HTTPException(
        422, detail={"code": "sites_nf_corpo_invalido", "campo": campo}, headers=_SEM_CACHE
    )


def hoje() -> date:
    return datetime.now(FUSO).date()


def numeros_da_nf(bruto: str) -> tuple[list[str], str | None]:
    """(números candidatos sem zero à esquerda, chave de 44 dígitos ou None). PURA.

    "000.012.345" → ["12345"]; "1/12345" → ["112345", "1", "12345"];
    "Série 001 Nº 000.012.345" → ["1", "12345"] (os 12 dígitos juntos passam
    de 9 depois de tirar os zeros: não é número de NF).
    """
    digitos = re.sub(r"[^0-9]", "", bruto)
    candidatos: list[str] = []

    def somar(d: str) -> None:
        n = d.lstrip("0")
        if 1 <= len(n) <= MAX_DIGITOS_NUMERO and n not in candidatos:
            candidatos.append(n)

    if len(digitos) == MAX_DIGITOS:
        # Chave de acesso: cUF(2) AAMM(4) CNPJ(14) mod(2) série(3) nNF(9) …
        # Os grupos de 4 em 4 da chave impressa não são número de NF.
        somar(digitos[25:34])
        return candidatos, digitos
    somar(digitos)
    for grupo in _RX_GRUPO.findall(bruto):
        somar(re.sub(r"[^0-9]", "", grupo))
    return candidatos[:MAX_CANDIDATOS], None


def ler_data(valor: Any) -> date | None:
    """ "AAAA-MM-DD" (aceita também "DD/MM/AAAA" e um horário depois). PURA."""
    if not isinstance(valor, str):
        return None
    s = valor.strip()
    m = _RX_DATA_ISO.fullmatch(s)
    if m:
        ano, mes, dia = (int(x) for x in m.groups())
    else:
        m = _RX_DATA_BR.fullmatch(s)
        if not m:
            return None
        dia, mes, ano = (int(x) for x in m.groups())
    try:
        return date(ano, mes, dia)
    except ValueError:
        return None


def normalizar_pedido(valor: Any) -> str | None:
    """Só letras e dígitos, em maiúsculas; vazio, nulo ou estranho = ausente. PURA."""
    if isinstance(valor, bool) or not isinstance(valor, (str, int)):
        return None
    s = str(valor)
    if len(s) > MAX_PEDIDO:
        return None
    return re.sub(r"[^0-9A-Za-z]", "", s).upper() or None


def ler_corpo(bruto: bytes) -> Consulta:
    """O corpo validado — ou 422 com o nome do campo (nunca o valor)."""
    if len(bruto) > MAX_CORPO:
        raise _invalido("corpo")
    try:
        corpo = json.loads(bruto or b"null")
    except (ValueError, UnicodeDecodeError):
        raise _invalido("corpo") from None
    if not isinstance(corpo, dict):
        raise _invalido("corpo")

    nf = corpo.get("nf")
    if isinstance(nf, int) and not isinstance(nf, bool):
        nf = str(nf)
    if not isinstance(nf, str):
        raise _invalido("nf")
    nf = nf.strip()
    digitos = sum(c.isascii() and c.isdigit() for c in nf)
    if not nf or len(nf) > MAX_NF or digitos == 0 or digitos > MAX_DIGITOS:
        raise _invalido("nf")

    data_compra = ler_data(corpo.get("data_compra"))
    if data_compra is None or not (DATA_MINIMA <= data_compra <= hoje() + timedelta(days=1)):
        raise _invalido("data_compra")

    numeros, chave = numeros_da_nf(nf)
    return Consulta(
        numeros=numeros,
        chave=chave,
        data_compra=data_compra,
        pedido=normalizar_pedido(corpo.get("pedido")),
    )


def _formas_do_pedido(valor: str | None) -> set[str]:
    n = re.sub(r"[^0-9A-Za-z]", "", valor or "").upper()
    return {n, n.lstrip("0")} - {""}


def confere(achado: Achado, consulta: Consulta) -> str | None:
    """Por onde a NF candidata bate com o 2º fator ("chave", "pedido", "data") — ou None. PURA."""
    if achado.pela_chave:
        return "chave"
    if consulta.pedido:
        do_cliente = _formas_do_pedido(consulta.pedido)
        do_pedido = _formas_do_pedido(achado.numero)
        for loja in achado.lojas:
            do_pedido |= _formas_do_pedido(loja)
        if do_cliente & do_pedido:
            return "pedido"
    dias = [d for d in (achado.pedido_dia, achado.nf_dia) if d is not None]
    if dias and (
        min(dias) - timedelta(days=DIAS_ANTES)
        <= consulta.data_compra
        <= max(dias) + timedelta(days=DIAS_DEPOIS)
    ):
        return "data"
    return None


async def notas_da_marca(session: AsyncSession, site: str, consulta: Consulta) -> list[Achado]:
    """As NFs com aquele número (ou chave) em pedidos da marca do site."""
    if not consulta.numeros and not consulta.chave:
        return []
    linhas = await session.execute(
        _SQL,
        {"numeros": consulta.numeros, "chave": consulta.chave, "marca": MARCA_SKU[site]},
    )
    return [
        Achado(
            numero=str(r.numero),
            lojas=tuple(r.lojas or ()),
            pedido_dia=r.pedido_dia,
            nf_dia=r.nf_dia,
            pela_chave=bool(r.pela_chave),
        )
        for r in linhas
    ]


async def site_com_limite(site: Annotated[str, Depends(site_do_token)]) -> str:
    """Token primeiro (401), depois o limite do site (429) — antes de ler o corpo."""
    if site not in MARCA_SKU:
        # Site com recorte de estoque mas sem regra de marca: porta sem parede.
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail={"code": "sites_nao_autorizado"})
    try:
        await sliding_window_check(
            key=f"sites_validar_nf:rl:{site}",
            limit=LIMITE_CHAMADAS_MIN,
            window_seconds=60,
        )
    except RateLimitError as e:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"code": "sites_limite"},
            headers={"Retry-After": str(e.retry_after)},
        ) from None
    except Exception as e:  # noqa: BLE001 — Redis fora do ar não derruba o SAC do site
        logger.warning("sites_validar_nf_rate_limit_indisponivel", site=site, err=str(e)[:120])
    return site


@router.post("/validar-nf")
async def validar_nf(
    request: Request,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_session)],
    site: Annotated[str, Depends(site_com_limite)],
) -> dict[str, bool]:
    inicio = time.perf_counter()
    response.headers["Cache-Control"] = "no-store"
    tamanho = request.headers.get("content-length")
    if tamanho and tamanho.isdigit() and int(tamanho) > MAX_CORPO:
        raise _invalido("corpo")
    try:
        consulta = ler_corpo(await request.body())
    except HTTPException as e:
        logger.info("sites_validar_nf_invalido", site=site, campo=e.detail.get("campo"))
        raise

    achados = await notas_da_marca(session, site, consulta)
    via = next((v for v in (confere(a, consulta) for a in achados) if v), None)

    if via:
        resultado = "encontrada"
    elif consulta.data_compra < COBERTURA_DESDE:
        resultado = "sem_cobertura"
    else:
        resultado = "fora_da_janela" if achados else "sem_nota"
    # Nunca a NF, o pedido, a data nem o IP: só o desfecho e por onde casou.
    logger.info(
        "sites_validar_nf",
        site=site,
        resultado=resultado,
        via=via,
        com_pedido=consulta.pedido is not None,
        ms=round((time.perf_counter() - inicio) * 1000),
    )
    if resultado == "sem_cobertura":
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail={"code": "sites_nf_sem_cobertura"}, headers=_SEM_CACHE
        )
    return {"encontrada": via is not None}
