"""Senha extra: segunda trava por cima da permissão, para páginas sensíveis.

Nasceu no Valuation; em 25/09/2026 passou a valer também para a tela Empresas
(Eduardo: "a mesma que tem em valuation, senha segura porque tem informações
que muita gente não pode ver"). As duas usam a MESMA senha
(`settings.valuation_password`), mas cada página tem o seu desbloqueio: abrir
o Valuation não abre Empresas.

Como funciona
- A pessoa digita a senha; se bater, recebe uma chave que vale 15 minutos.
- A chave é `<ts>.<HMAC(jwt_secret, "<escopo>:<ts>")>`: não fica guardada em
  lugar nenhum do servidor, e uma chave do Valuation não serve para Empresas.
- Toda rota protegida confere a chave no cabeçalho. Sem ela, o servidor
  recusa — esconder a tela não bastaria, os dados sairiam pela API.

Tentativas erradas (acrescentado em 25/09/2026): a senha tem 6 números e o
único freio era 0,3 s por erro, com pedidos em paralelo à vontade — dava para
testar o milhão de combinações em pouco tempo. Agora, 5 erros seguidos travam
aquela pessoa por 15 minutos, contados no Redis. Se o Redis estiver fora, a
senha continua sendo conferida (só a contagem deixa de valer), para uma queda
do Redis não trancar todo mundo para fora.

Senha vazia = trancado para todos: sem senha configurada, ninguém entra.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import time
from typing import Annotated

import structlog
from fastapi import Header, HTTPException

from app.config import get_settings

logger = structlog.get_logger()

MAX_ERROS = 5
JANELA_ERROS_SEGUNDOS = 15 * 60


def fazer_token(escopo: str) -> tuple[str, int]:
    """(chave, validade em segundos). Formato `<ts>.<assinatura>`."""
    s = get_settings()
    ts = int(time.time())
    assinatura = hmac.new(
        s.jwt_secret.encode(), f"{escopo}:{ts}".encode(), hashlib.sha256
    ).hexdigest()
    return f"{ts}.{assinatura}", s.valuation_unlock_ttl_seconds


def token_valido(token: str | None, escopo: str) -> bool:
    if not token:
        return False
    try:
        ts_txt, assinatura = token.split(".", 1)
        ts = int(ts_txt)
    except (ValueError, AttributeError):
        return False
    s = get_settings()
    if time.time() - ts > s.valuation_unlock_ttl_seconds:
        return False
    esperada = hmac.new(
        s.jwt_secret.encode(), f"{escopo}:{ts}".encode(), hashlib.sha256
    ).hexdigest()
    # Em bytes: compare_digest com texto fora do ASCII levanta TypeError, e um
    # cabeçalho com acento virava erro 500 em vez de "trancado".
    return hmac.compare_digest(assinatura.encode(), esperada.encode())


def _chave_erros(user_id: object) -> str:
    return f"davinci:senha_extra:erros:{user_id}"


async def _redis():
    # Import tardio: o cliente conecta na primeira chamada, e os testes que não
    # tocam em senha não precisam de Redis de pé.
    from app.redis_client import redis

    return redis


async def conferir_senha(senha: str | None, *, user_id: object, escopo: str) -> None:
    """Confere a senha extra ou levanta 401/429. Não devolve nada se bater."""
    esperada = get_settings().valuation_password or ""
    if not esperada:
        # Fail-closed: sem senha configurada ninguém desbloqueia.
        raise HTTPException(401, detail={"code": "senha_nao_configurada"})

    chave = _chave_erros(user_id)
    redis = None
    try:
        redis = await _redis()
        # Conta a tentativa ANTES de conferir. Ler, conferir e só depois somar
        # deixava 400 pedidos em paralelo lerem "0 erros" juntos; o INCR é
        # atômico, então cada pedido recebe um número e só os 5 primeiros
        # chegam a comparar a senha. Acertar zera a contagem logo abaixo.
        tentativa = int(await redis.incr(chave))
        # NX: põe o prazo só se a chave ainda não tiver um. Assim a chave nunca
        # fica sem prazo (trava eterna) e insistir não empurra o fim da trava.
        await redis.expire(chave, JANELA_ERROS_SEGUNDOS, nx=True)
    except Exception:  # noqa: BLE001 - Redis fora não pode trancar todo mundo
        logger.warning("senha_extra_redis_indisponivel", escopo=escopo)
        redis, tentativa = None, 0
    if tentativa > MAX_ERROS:
        raise HTTPException(429, detail={"code": "muitas_tentativas"})

    if not hmac.compare_digest((senha or "").strip().encode(), esperada.encode()):
        logger.info("senha_extra_errada", escopo=escopo, user_id=str(user_id))
        await asyncio.sleep(0.3)
        raise HTTPException(401, detail={"code": "wrong_password"})

    if redis is not None:
        try:
            await redis.delete(chave)
        except Exception:  # noqa: BLE001 - só zera a contagem; a senha já bateu
            logger.warning("senha_extra_redis_indisponivel", escopo=escopo)
    logger.info("senha_extra_desbloqueio", escopo=escopo, user_id=str(user_id))


async def require_empresas_unlock(
    x_empresas_token: Annotated[str | None, Header(alias="X-Empresas-Token")] = None,
) -> None:
    """Trava da tela Empresas, por cima da permissão `empresa`: sem a chave de
    desbloqueio o servidor não entrega CNPJ, IE, IP, certificados nem aceita
    alteração — esconder a tela não bastaria."""
    if not token_valido(x_empresas_token, "empresas"):
        raise HTTPException(401, detail={"code": "empresas_locked"})
