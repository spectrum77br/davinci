"""Credencial da conta de Shopee Vídeo — app por conta, troca e renovação.

O app de vídeo ("DaVinci Videos", Shopee Video Management) é POR CONTA: o
Eduardo digita partner_id + partner_key na tela, e eles ficam cifrados no
mesmo blob dos tokens (`redes_sociais_tokens.token_enc`, `cipher.encrypt_json`)
— nada em .env, nada em `integrations`. O blob:

    {partner_id, partner_key,                     ← digitados na tela
     access_token, refresh_token, expires_at,     ← do token/get e da renovação
     user_id, shop_id, autorizado_em, renovado_em}

O que fica EM CLARO na linha, pra tela e pros crons não decifrarem nada:

  • `external_user_id` = user_id da loja. É ele que diz "autorizada": antes do
    login na Shopee a linha existe só com o partner (status `pendente`).
  • `token_expires_at` = validade do REFRESH (30 dias, recarimbada a cada
    renovação) — nunca a do access de 4 h. É o que o `meta_token_refresh`
    varre pra avisar os admins quando a cadeia estiver pra morrer.
  • `status`: pendente | ok | expirado (a cadeia morreu: autorizar de novo).

O cuidado que atravessa o arquivo: **o refresh token é de USO ÚNICO**.
Renovou, o velho morre. Por isso a renovação acontece numa SESSÃO PRÓPRIA,
com a LINHA do token travada (SELECT … FOR UPDATE): quem chega em segundo
espera o commit do primeiro e RELÊ o token novo, em vez de gastar o refresh
que já morreu. O par novo é gravado e comitado ANTES de ser usado — a
mesma regra do `services/atendimento/clientes.py` para as lojas, e o mesmo
desenho da Magalu (trava de linha, não Redis), que não depende de mais nada
estar de pé.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

import app.db as _db
from app.models import RedeSocial, RedeSocialToken
from app.security.cipher import decrypt_json, encrypt_json
from app.services.marketing.shopee_video import (
    PROVEDOR_SHOPEE,
    ClienteShopeeVideo,
    ShopeeVideoError,
)

logger = structlog.get_logger()

STATUS_PENDENTE = "pendente"
STATUS_OK = "ok"
STATUS_EXPIRADO = "expirado"

# Refresh token: 30 dias, uso único. Access token: 4 h; renova quando faltam
# menos de 5 min (o velho ainda vale 5 min depois do novo nascer).
VALIDADE_REFRESH = timedelta(days=30)
MARGEM_ACCESS_S = 300
# O cron diário renova a cadeia quando faltam menos de 20 dias pro refresh
# vencer — sem post nenhum, a cadeia nunca chega perto do fim.
RENOVAR_QUANDO_FALTAM = timedelta(days=20)

# Erros da renovação que querem dizer "a cadeia morreu": só uma pessoa
# autorizando de novo resolve. Tentar em loop não adianta (e suja o log).
_REAUTORIZAR = (
    "refresh_token",
    "not exist",
    "not found",
    "user_no_linked",
    "user_banned",
    "no linked",
    "error_auth",
    "invalid_code",
)


MSG_REAUTORIZAR = "a autorização da Shopee venceu — autorize de novo em Cadastros › Redes Sociais"


class ContaShopeeError(Exception):
    """Motivo estável (código) + texto em português, SEM segredo."""

    def __init__(self, code: str, mensagem: str = "") -> None:
        self.code = code
        self.mensagem = mensagem or code
        super().__init__(self.mensagem)


class ReautorizarError(ContaShopeeError):
    """A cadeia de tokens morreu: só autorizando de novo na Shopee."""


@dataclass(slots=True, repr=False)
class Credencial:
    """O que uma chamada de vídeo precisa. `repr` sem nada secreto."""

    rede_social_id: UUID
    partner_id: int
    partner_key: str
    access_token: str
    user_id: int
    shop_id: int | None
    expires_at: int

    def __repr__(self) -> str:
        return f"Credencial(rede={self.rede_social_id}, user_id={self.user_id})"

    def cliente(self) -> ClienteShopeeVideo:
        return ClienteShopeeVideo(
            self.partner_id,
            self.partner_key,
            access_token=self.access_token,
            user_id=self.user_id,
        )


# ──────────────────────────────────────────────────────────────── blob


def blob_de(tok: RedeSocialToken | None) -> dict[str, Any]:
    """O blob decifrado (ou {}). Erro de cifra vira {} — e NUNCA é logado
    com o conteúdo: pode carregar pedaço do material cifrado."""
    if tok is None or not tok.token_enc:
        return {}
    try:
        d = decrypt_json(tok.token_enc)
    except Exception:  # noqa: BLE001
        logger.warning("shopee_video_blob_ilegivel", rede_social_id=str(tok.rede_social_id))
        return {}
    return d if isinstance(d, dict) else {}


def tem_partner(blob: dict[str, Any]) -> bool:
    return bool(_int(blob.get("partner_id")) and str(blob.get("partner_key") or "").strip())


def autorizada(tok: RedeSocialToken | None) -> bool:
    """Autorizada = a Shopee devolveu o user_id da loja (o resto é pendente)."""
    return bool(tok is not None and tok.token_enc and (tok.external_user_id or "").strip())


def _int(v: Any) -> int | None:
    try:
        n = int(str(v).strip())
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


async def _token_da_rede(
    session: AsyncSession, rede_social_id: UUID, *, travar: bool = False
) -> RedeSocialToken | None:
    q = select(RedeSocialToken).where(RedeSocialToken.rede_social_id == rede_social_id)
    if travar:
        q = q.with_for_update()
    return (await session.execute(q.execution_options(populate_existing=True))).scalars().first()


# ─────────────────────────────────────────────────────────── app (partner)


async def salvar_partner(
    session: AsyncSession,
    rede: RedeSocial,
    *,
    partner_id: int,
    partner_key: str,
    user_id: UUID | None,
) -> RedeSocialToken:
    """Grava o app de vídeo DESTA conta (cifrado). Não comita.

    Trocar de app (outro partner_id ou outra chave) derruba a autorização:
    o token é emitido PARA um app, e misturar o token de um com a chave de
    outro só produz "Wrong sign" no meio da publicação. Mesmo app de novo
    (reautorizar) mantém o que existe até o retorno da Shopee trocar.
    """
    tok = await _token_da_rede(session, rede.id, travar=True)
    if tok is None:
        tok = RedeSocialToken(rede_social_id=rede.id)
        session.add(tok)
    blob = blob_de(tok)
    mesmo_app = (
        _int(blob.get("partner_id")) == int(partner_id)
        and str(blob.get("partner_key") or "") == partner_key
    )
    if not mesmo_app:
        blob = {}
        tok.external_user_id = None
        tok.external_username = None
        tok.token_expires_at = None
        tok.status = STATUS_PENDENTE
        tok.last_error = None
    blob["partner_id"] = int(partner_id)
    blob["partner_key"] = partner_key
    tok.token_enc = encrypt_json(blob)
    tok.provedor = PROVEDOR_SHOPEE
    if not autorizada(tok):
        tok.status = STATUS_PENDENTE
    tok.connected_by = user_id
    logger.info(
        "shopee_video_app_salvo",
        rede_social_id=str(rede.id),
        trocou_app=not mesmo_app,
    )
    return tok


def escolher_user_id(resposta: dict[str, Any], shop_id_esperado: int) -> tuple[int, int]:
    """(user_id, shop_id) da loja certa na resposta do `token/get`.

    "The shop_id_list and user_id_list are in a one-to-one order" (guia 669):
    o user_id é o do MESMO índice da loja esperada. A loja esperada é a da
    integração ligada à conta — outra loja na lista quer dizer que alguém
    entrou na Shopee com o login errado, e publicar na loja errada não tem
    desfazer.
    """
    lojas = [_int(x) for x in (resposta.get("shop_id_list") or [])]
    usuarios = [_int(x) for x in (resposta.get("user_id_list") or [])]
    if not any(usuarios):
        raise ContaShopeeError(
            "autorizacao_sem_user_id",
            "a autorização não trouxe o user_id — confira se o app é do tipo "
            "Shopee Video Management e se o link usou auth_type=seller",
        )
    if lojas and any(lojas):
        if shop_id_esperado not in lojas:
            raise ContaShopeeError(
                "loja_errada",
                "a Shopee autorizou OUTRA loja — entre com o login principal da loja "
                "ligada a esta conta e autorize de novo",
            )
        i = lojas.index(shop_id_esperado)
        if i >= len(usuarios) or not usuarios[i]:
            raise ContaShopeeError(
                "autorizacao_sem_user_id",
                "a autorização trouxe a loja mas não o user_id dela",
            )
        return int(usuarios[i]), shop_id_esperado
    # Sem lista de lojas: só aceita quando há UM usuário e nenhuma dúvida.
    unicos = [u for u in usuarios if u]
    if len(unicos) != 1:
        raise ContaShopeeError(
            "autorizacao_ambigua",
            "a autorização trouxe mais de um usuário e nenhuma loja — autorize de novo pela loja",
        )
    return int(unicos[0]), shop_id_esperado


def gravar_autorizacao(
    tok: RedeSocialToken,
    *,
    resposta: dict[str, Any],
    user_id: int,
    shop_id: int,
    nome_loja: str | None,
    agora: datetime | None = None,
) -> None:
    """Grava o par de tokens do `token/get` no blob. Não comita (o callback
    comita junto com o `consumed_at` do state, numa transação só)."""
    agora = agora or datetime.now(UTC)
    access = str(resposta.get("access_token") or "").strip()
    refresh = str(resposta.get("refresh_token") or "").strip()
    if not access or not refresh:
        raise ContaShopeeError("autorizacao_sem_token", "a Shopee não devolveu os tokens")
    blob = blob_de(tok)
    blob.update(
        access_token=access,
        refresh_token=refresh,
        expires_at=int(agora.timestamp()) + _expira_em_s(resposta.get("expire_in")),
        user_id=int(user_id),
        shop_id=int(shop_id),
        autorizado_em=agora.isoformat(),
    )
    tok.token_enc = encrypt_json(blob)
    tok.provedor = PROVEDOR_SHOPEE
    tok.external_user_id = str(user_id)
    tok.external_username = (nome_loja or "").strip() or f"loja {shop_id}"
    tok.token_expires_at = agora + VALIDADE_REFRESH
    tok.status = STATUS_OK
    tok.last_error = None
    tok.last_ok_at = agora
    tok.connected_at = agora


def _expira_em_s(bruto: Any) -> int:
    """`expire_in` em segundos — no exemplo da doc aparece um epoch, então
    número grande demais vira o padrão de 4 h."""
    n = _int(bruto) or 14400
    return n if n < 10**6 else 14400


def e_reautorizar(e: ShopeeVideoError) -> bool:
    return e.tem(*_REAUTORIZAR)


# ──────────────────────────────────────────────────────── uso e renovação


async def credencial(
    rede_social_id: UUID,
    *,
    recusado: str | None = None,
    forcar: bool = False,
    agora: datetime | None = None,
) -> Credencial:
    """Credencial pronta pra usar — renovando ANTES, se precisar.

    `recusado` é o access_token que a Shopee acabou de recusar: renova só se
    o do banco ainda for ele (outro processo pode ter renovado no meio).
    `forcar` renova de qualquer jeito (o cron de manutenção).

    Levanta `ReautorizarError` (a cadeia morreu — a linha fica `expirado` e
    a tela pede autorizar de novo) ou `ContaShopeeError` (falta configurar,
    ou a Shopee não respondeu: tenta no próximo ciclo).
    """
    agora = agora or datetime.now(UTC)
    # `_db.SessionLocal` lido na hora (os testes trocam o engine).
    async with _db.SessionLocal() as s:
        tok = await _token_da_rede(s, rede_social_id, travar=True)
        blob = blob_de(tok)
        if tok is None or not tem_partner(blob):
            raise ContaShopeeError(
                "conta_sem_app_shopee",
                "a conta não tem o app de vídeo da Shopee (partner_id/partner_key)",
            )
        user_id = _int(blob.get("user_id"))
        access = str(blob.get("access_token") or "")
        refresh = str(blob.get("refresh_token") or "")
        if not autorizada(tok) or not user_id or not refresh:
            raise ContaShopeeError(
                "conta_sem_autorizacao_shopee",
                "a loja ainda não autorizou o app de vídeo — clique em Autorizar na Shopee",
            )
        if tok.status == STATUS_EXPIRADO:
            raise ReautorizarError("conta_shopee_reautorizar", MSG_REAUTORIZAR)
        expira = _int(blob.get("expires_at")) or 0
        precisa = (
            forcar
            or not access
            or expira - MARGEM_ACCESS_S <= int(agora.timestamp())
            or (recusado is not None and recusado == access)
        )
        if not precisa:
            await s.rollback()  # solta a trava
            return _credencial(rede_social_id, blob)

        cliente = ClienteShopeeVideo(int(blob["partner_id"]), str(blob["partner_key"]))
        try:
            resp = await cliente.renovar(refresh, user_id)
        except ShopeeVideoError as e:
            if e_reautorizar(e):
                tok.status = STATUS_EXPIRADO
                tok.last_error = (
                    f"a renovação do token da Shopee foi recusada ({e.texto()}) — autorize de novo"
                )[:500]
                await s.commit()
                logger.warning(
                    "shopee_video_reautorizar",
                    rede_social_id=str(rede_social_id),
                    code=e.code,
                    request_id=e.request_id,
                )
                raise ReautorizarError("conta_shopee_reautorizar", MSG_REAUTORIZAR) from None
            tok.last_error = f"renovação do token da Shopee falhou: {e.texto()}"[:500]
            await s.commit()
            raise ContaShopeeError("renovacao_falhou", tok.last_error) from None
        novo_access = str(resp.get("access_token") or "").strip()
        novo_refresh = str(resp.get("refresh_token") or "").strip()
        if not novo_access or not novo_refresh:
            await s.rollback()
            raise ContaShopeeError("renovacao_falhou", "a Shopee renovou sem devolver os tokens")
        blob.update(
            access_token=novo_access,
            refresh_token=novo_refresh,
            expires_at=int(agora.timestamp()) + _expira_em_s(resp.get("expire_in")),
            renovado_em=agora.isoformat(),
        )
        tok.token_enc = encrypt_json(blob)
        tok.token_expires_at = agora + VALIDADE_REFRESH
        tok.status = STATUS_OK
        tok.last_error = None
        try:
            # ANTES de usar: o refresh velho já morreu na Shopee.
            await s.commit()
        except Exception:
            logger.exception(
                "shopee_video_token_persistir_falhou", rede_social_id=str(rede_social_id)
            )
            raise
        logger.info("shopee_video_token_renovado", rede_social_id=str(rede_social_id))
        return _credencial(rede_social_id, blob)


def _credencial(rede_social_id: UUID, blob: dict[str, Any]) -> Credencial:
    return Credencial(
        rede_social_id=rede_social_id,
        partner_id=int(blob["partner_id"]),
        partner_key=str(blob["partner_key"]),
        access_token=str(blob.get("access_token") or ""),
        user_id=int(blob["user_id"]),
        shop_id=_int(blob.get("shop_id")),
        expires_at=_int(blob.get("expires_at")) or 0,
    )


async def registrar_saude(rede_social_id: UUID, *, erro: str | None) -> None:
    """`last_ok_at`/`last_error` da linha (o que a tela mostra), numa sessão
    própria e só nessas colunas — nunca reescreve o blob."""
    agora = datetime.now(UTC)
    valores: dict[str, Any] = (
        {"last_error": erro[:500]} if erro else {"last_ok_at": agora, "last_error": None}
    )
    async with _db.SessionLocal() as s:
        await s.execute(
            update(RedeSocialToken)
            .where(RedeSocialToken.rede_social_id == rede_social_id)
            .values(**valores)
        )
        await s.commit()


async def renovar_todas(*, agora: datetime | None = None) -> dict[str, int]:
    """Cron diário: renova a cadeia das contas autorizadas que faltam menos
    de 20 dias pro refresh vencer. Sem post nenhum, a cadeia nunca chega ao
    fim (30 dias) — e com post ela já renova sozinha a cada uso."""
    agora = agora or datetime.now(UTC)
    async with _db.SessionLocal() as s:
        ids = (
            (
                await s.execute(
                    select(RedeSocialToken.rede_social_id).where(
                        RedeSocialToken.provedor == PROVEDOR_SHOPEE,
                        RedeSocialToken.status == STATUS_OK,
                        RedeSocialToken.external_user_id.isnot(None),
                        RedeSocialToken.token_enc.isnot(None),
                        RedeSocialToken.token_expires_at.isnot(None),
                        RedeSocialToken.token_expires_at <= agora + RENOVAR_QUANDO_FALTAM,
                    )
                )
            )
            .scalars()
            .all()
        )
    r = {"contas": len(ids), "renovadas": 0, "reautorizar": 0, "falhas": 0}
    for rid in ids:
        try:
            await credencial(rid, forcar=True, agora=agora)
            r["renovadas"] += 1
        except ReautorizarError:
            r["reautorizar"] += 1
        except Exception as e:  # noqa: BLE001 — uma conta não derruba as outras
            r["falhas"] += 1
            logger.warning(
                "shopee_video_renovar_falhou",
                rede_social_id=str(rid),
                err=type(e).__name__,
                code=getattr(e, "code", None),
            )
    if ids:
        logger.info("shopee_video_token_refresh", **r)
    return r
