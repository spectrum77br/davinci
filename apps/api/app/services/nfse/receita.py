"""Consulta do CNPJ na Receita (via BrasilAPI) pra ajudar a preencher a empresa.

Eduardo (29/09): "dados reais do que testar". Só sugere — município IBGE,
regime (MEI / Simples / nenhum) e razão social — e NÃO grava nada: quem
confere e salva é a pessoa, na tela.

BrasilAPI (https://brasilapi.com.br/api/cnpj/v1/{cnpj}) é pública, sem chave,
e repassa os dados abertos do CNPJ da Receita Federal. Campos usados:
`razao_social`, `municipio`, `uf`, `codigo_municipio_ibge` (número),
`opcao_pelo_simples` e `opcao_pelo_mei` (true/false/null).
"""

from __future__ import annotations

import re
from typing import Any

import httpx
import structlog

from app.services.nfse.erros import NfseError
from app.services.nfse.municipios import nome_do_codigo

logger = structlog.get_logger()

URL = "https://brasilapi.com.br/api/cnpj/v1/{cnpj}"
FONTE = "BrasilAPI (Receita Federal)"
TIMEOUT = 15.0

_SEM_RESPOSTA = (
    "A consulta da Receita (BrasilAPI) não respondeu agora. Tente de novo em alguns minutos."
)


def _bool(v: Any) -> bool | None:
    return v if isinstance(v, bool) else None


def regime_sugerido(simples: bool | None, mei: bool | None) -> int | None:
    """opSimpNac: 2 MEI · 3 Simples ME/EPP (não MEI) · 1 nenhum dos dois ·
    None se a Receita não disse."""
    if mei is True:
        return 2
    if simples is True:
        return 3
    if simples is False:
        return 1  # fora do Simples não tem como ser MEI
    return None


def mapear(cnpj: str, dados: dict) -> dict:
    cod = re.sub(r"\D", "", str(dados.get("codigo_municipio_ibge") or ""))
    cmun = cod if len(cod) == 7 else None
    uf = (str(dados.get("uf") or "").strip().upper()) or None
    nome = (str(dados.get("municipio") or "").strip()) or None
    oficial = nome_do_codigo(cmun)
    if oficial is not None:
        # A Receita manda "SAO PAULO"; o IBGE tem "São Paulo".
        nome, uf = oficial
    simples = _bool(dados.get("opcao_pelo_simples"))
    mei = _bool(dados.get("opcao_pelo_mei"))
    return {
        "cnpj": cnpj,
        "razao_social": (str(dados.get("razao_social") or "").strip()) or None,
        "municipio_nome": nome,
        "uf": uf,
        "cmun_ibge": cmun,
        "simples": simples,
        "mei": mei,
        "op_simp_nac_sugerido": regime_sugerido(simples, mei),
        "fonte": FONTE,
    }


async def consultar(cnpj: str) -> dict:
    cnpj = re.sub(r"\D", "", cnpj or "")
    if len(cnpj) != 14:
        raise NfseError(422, "sem_cnpj", "A empresa não tem CNPJ cadastrado.")
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as cli:
            r = await cli.get(URL.format(cnpj=cnpj), headers={"Accept": "application/json"})
    except httpx.HTTPError as e:
        logger.warning("nfse_receita_sem_resposta", erro=type(e).__name__)
        raise NfseError(502, "receita_sem_resposta", _SEM_RESPOSTA) from e
    if r.status_code in (400, 404):
        # 400 = CNPJ que a Receita nem reconhece como válido.
        raise NfseError(404, "cnpj_nao_encontrado", "A Receita não achou esse CNPJ.")
    if r.status_code != 200:
        logger.warning("nfse_receita_http", http=r.status_code)
        raise NfseError(502, "receita_sem_resposta", _SEM_RESPOSTA)
    try:
        dados = r.json()
    except ValueError as e:
        raise NfseError(502, "receita_sem_resposta", _SEM_RESPOSTA) from e
    if not isinstance(dados, dict):
        raise NfseError(502, "receita_sem_resposta", _SEM_RESPOSTA)
    return mapear(cnpj, dados)
