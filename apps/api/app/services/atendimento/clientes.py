"""O cliente de API de cada integração, para o atendimento.

Parece só um `factory.client_for`, e seria — se não fosse o token.

Refresh token da Shopee e do ML é de USO ÚNICO: renovou, o antigo morre. Os
outros jeitos de montar cliente no repo gravam o token novo na sessão de
quem chamou (`integration.credentials = ...; flush`). No atendimento essa
sessão é a da rodada de sync, que pode dar rollback por qualquer motivo
depois — e aí o token novo se perde, o velho já morreu, e a loja fica sem
acesso até alguém reautorizar na mão. Por isso, aqui:

  1. o token novo é gravado numa SESSÃO PRÓPRIA, com commit NA HORA;
  2. a renovação passa pela trava por integração (`token_refresh_lock`):
     o sync de 2 em 2 minutos e o envio pela tela podem querer renovar a
     mesma loja no mesmo segundo — só um renova; quem não pega a trava
     espera e RELÊ as credenciais do banco.

  Os crons de token do worker (`shopee_token_refresh`, `ml_token_refresh`,
  `tiktok_token_refresh` → `worker._refresh_tokens_for`) passam pela MESMA
  trava: quem não pega pula a loja naquele ciclo, e quem pega relê as
  credenciais do banco antes de renovar (o cron carrega todas as lojas no
  começo; o refresh token que ele tem em memória pode já ter morrido). Sem
  Redis, o cron renova como sempre renovou. O sync do atendimento continua
  nos minutos ÍMPARES, longe do :00/:30 deles.

Os quatro clientes (Shopee, ML, TikTok, Amazon) renovam SEMPRE por
`self.refresh()` — inclusive o `_ensure_fresh_token` da TikTok e o 401 do
ML —, então embrulhar o `refresh` da instância cobre todos os caminhos.

Cuidado de quem chama: a sessão do chamador não pode estar com a linha da
integração travada (UPDATE sem commit) quando o cliente renovar — o UPDATE
daqui esperaria por ela. Por isso nada aqui marca a integração da sessão do
chamador como alterada.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import select, update
from sqlalchemy.orm.attributes import set_committed_value

import app.db as _db
from app.models import Integration
from app.security.cipher import decrypt_json, encrypt_json
from app.services.atendimento.constantes import PLATAFORMAS
from app.services.marketplaces import factory
from app.services.token_refresh_lock import token_refresh_lock, wait_for_refresh

logger = structlog.get_logger()

# A Magalu NÃO passa pelo embrulho abaixo: o `MagaluClient` montado com
# `integration_id` já renova sob a trava da LINHA da integração (SELECT ... FOR
# NO KEY UPDATE), relê o token do banco antes de renovar e só usa o novo depois
# do commit — o mesmo caminho do estoque, do preço e da importação de anúncios.
# Um segundo caminho (Redis + releitura aqui) só abriria espaço para os dois
# discordarem sobre um refresh token que também é de uso único.
PLATAFORMAS_COM_TRAVA_PROPRIA = frozenset({"magalu"})


def _expira_em(creds: dict) -> datetime | None:
    """Validade do token, em claro, para a coluna `token_expires_at`.

    TikTok guarda `token_expires_at`; Shopee/ML/Amazon, `expires_at` — em
    epoch, e a Amazon às vezes em ISO. Formato estranho vira None (a coluna
    fica como estava), nunca erro: não vale perder um token por causa dela.
    """
    bruto = creds.get("token_expires_at") or creds.get("expires_at")
    if bruto in (None, "", 0):
        return None
    try:
        if isinstance(bruto, int | float) or str(bruto).strip().isdigit():
            return datetime.fromtimestamp(int(bruto), tz=UTC)
        quando = datetime.fromisoformat(str(bruto).strip().replace("Z", "+00:00"))
    except (TypeError, ValueError, OverflowError, OSError):
        return None
    return quando if quando.tzinfo else quando.replace(tzinfo=UTC)


async def gravar_credenciais(integration_id: UUID, creds: dict) -> bytes:
    """Grava as credenciais numa sessão PRÓPRIA e commita na hora. Devolve o blob."""
    blob = encrypt_json(creds)
    valores: dict[str, Any] = {"credentials": blob}
    expira = _expira_em(creds)
    if expira is not None:
        valores["token_expires_at"] = expira
    # `_db.SessionLocal` lido na hora da chamada (os testes trocam o engine).
    async with _db.SessionLocal() as s:
        await s.execute(
            update(Integration).where(Integration.id == integration_id).values(**valores)
        )
        await s.commit()
    logger.info("atendimento_token_persistido", integration_id=str(integration_id))
    return blob


async def ler_credenciais(integration_id: UUID) -> dict | None:
    """As credenciais como estão no banco AGORA (sessão própria)."""
    async with _db.SessionLocal() as s:
        blob = await s.scalar(
            select(Integration.credentials).where(Integration.id == integration_id)
        )
    return decrypt_json(blob) if blob else None


def _token(creds: dict | None) -> str:
    return str((creds or {}).get("access_token") or "")


def _travar_renovacao(cliente: Any, integration_id: UUID) -> None:
    """Troca o `refresh` da instância por um que passa pela trava da integração."""
    original = getattr(cliente, "refresh", None)
    if original is None:
        return

    async def refresh_com_trava(*args: Any, **kwargs: Any) -> None:
        token_antes = _token(cliente.creds)
        async with token_refresh_lock(integration_id) as pegou:
            if pegou:
                # Conferência dupla: outro processo pode ter renovado entre a
                # montagem deste cliente e agora. Renovar de novo com o
                # refresh token que ESTE cliente tem em memória seria usar um
                # token já morto (uso único) — adota o do banco.
                no_banco = await ler_credenciais(integration_id)
                if _token(no_banco) and _token(no_banco) != token_antes:
                    cliente.creds = dict(no_banco)
                    logger.info(
                        "atendimento_token_ja_renovado", integration_id=str(integration_id)
                    )
                    return
                await original(*args, **kwargs)
                return
        # Outro processo está renovando: espera ele terminar e usa o que ele
        # gravou. Se a espera estourar, relê do mesmo jeito — o pior caso é
        # uma chamada com token vencido, que falha e é tentada na próxima rodada.
        liberou = await wait_for_refresh(integration_id)
        no_banco = await ler_credenciais(integration_id)
        if no_banco:
            cliente.creds = dict(no_banco)
        logger.info(
            "atendimento_token_relido", integration_id=str(integration_id), liberou=liberou
        )

    cliente.refresh = refresh_com_trava


async def cliente_da_integracao(integration: Integration) -> Any:
    """Cliente pronto para a API da loja, com renovação de token segura.

    O token renovado é gravado numa sessão própria (commit imediato) e
    espelhado na `integration` recebida SEM marcá-la como alterada — a
    sessão do chamador não regrava credencial (nem velha por cima da nova).
    """
    integration_id = integration.id
    creds = decrypt_json(integration.credentials)

    async def _persistir(novas: dict) -> None:
        try:
            blob = await gravar_credenciais(integration_id, novas)
        except Exception:
            # O refresh token velho já morreu: se isto falhar, a loja fica sem
            # acesso até reautorizar. Tem que aparecer no log, alto.
            logger.exception(
                "atendimento_token_persistir_falhou", integration_id=str(integration_id)
            )
            raise
        set_committed_value(integration, "credentials", blob)
        expira = _expira_em(novas)
        if expira is not None:
            set_committed_value(integration, "token_expires_at", expira)

    cliente = factory.client_for(
        integration.platform,
        creds,
        on_token_refresh=_persistir,
        integration_id=integration_id,
    )
    plataforma = getattr(integration.platform, "value", integration.platform)
    if plataforma in PLATAFORMAS and plataforma not in PLATAFORMAS_COM_TRAVA_PROPRIA:
        _travar_renovacao(cliente, integration_id)
    return cliente
