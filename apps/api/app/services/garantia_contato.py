"""O contato do comprador na garantia (09/11/2026).

Pedido do Eduardo: o e-mail e o número da pessoa, já no formato do WhatsApp,
na aba Dados dos Detalhes da garantia ("quero que as informações apareçam aqui
somente").

De onde vem o contato (medido em 09/11/2026, só lendo):
- **Bling**, o cadastro do contato do pedido (GET /pedidos/vendas/{id} →
  `contato.id` → GET /contatos/{id}). Nos pedidos Amazon vêm o telefone da
  entrega (10 de 12 pedidos) e o e-mail repassador da Amazon
  (`…@marketplace.amazon.com.br`, que só entrega mensagem pela Amazon).
  Mercado Livre, Shopee e TikTok vêm vazios (12 de 12 cada): o robô nem
  pergunta ao Bling por eles.
- **Formulário do SAC do site**: o e-mail e o telefone que o próprio cliente
  digitou. Vale mais que o do Bling (a tela escolhe; ver routers/garantias).
- A NF e as APIs das plataformas não trazem nada (pesquisa de 06/11/2026).

O robô (`preencher`, no worker) lê o Bling UMA vez por garantia e guarda em
`garantias.contato_telefone/contato_email/contato_lido_em`."""

from __future__ import annotations

import re
from datetime import UTC, datetime

import httpx
import structlog
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.garantia import Garantia
from app.services import garantia as svc

logger = structlog.get_logger()

# O Bling não tem o contato do comprador destas (medido em 09/11/2026).
PLATAFORMAS_SEM_CONTATO = frozenset({"ml", "shopee", "tiktok"})
# Por volta do robô: 2 GETs por garantia, dentro do limite do Bling.
POR_RODADA = 40
# O e-mail que só repassa a mensagem pela plataforma.
_DOMINIOS_REPASSADORES = (
    "marketplace.amazon.",
    "mercadolivre.",
    "mercadolibre.",
    "shopee.",
    "tiktok",
)
_RX_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def whatsapp(telefone: str | None) -> str | None:
    """O telefone no formato do wa.me: só dígitos, com o 55 na frente.
    Aceita "(19) 98888-7777", "+55 19 98888-7777" e o que vem antes do link
    que o e-mail do SAC às vezes cola ("… — https://wa.me/…"). Sem DDD, com
    zero na frente, mascarado ou com cara de número de teste → None. PURA."""
    texto = re.split(r"\s[—–]\s|https?://", telefone or "", maxsplit=1)[0]
    if "*" in texto:
        return None
    d = svc.so_digitos(texto)
    if len(d) in (12, 13) and d.startswith("55"):
        d = d[2:]
    if len(d) not in (10, 11) or d.startswith("0") or len(set(d[2:])) <= 2:
        return None
    return "55" + d


def telefone_legivel(numero: str) -> str:
    """ "5519988887777" → "(19) 98888-7777". PURA."""
    d = numero[2:]
    return f"({d[:2]}) {d[2:-4]}-{d[-4:]}"


def telefone_mascarado(numero: str) -> str:
    """ "5519988887777" → "(19) 9****-7777" (quem não vê o CPF completo). PURA."""
    d = numero[2:]
    meio = d[2:-4]
    return f"({d[:2]}) {meio[:1]}{'*' * (len(meio) - 1)}-{d[-4:]}"


def email_valido(email: str | None) -> str | None:
    e = (email or "").strip().lower()
    return e if _RX_EMAIL.match(e) and "*" not in e else None


def email_repassador(email: str) -> bool:
    """O e-mail da plataforma que só repassa a mensagem por ela (o da Amazon)."""
    dominio = email.rsplit("@", 1)[-1].lower()
    return any(x in dominio for x in _DOMINIOS_REPASSADORES)


def email_mascarado(email: str) -> str:
    """ "maria@gmail.com" → "m***@gmail.com". PURA."""
    usuario, _, dominio = email.partition("@")
    return f"{usuario[:1]}***@{dominio}"


async def ler_do_bling(client, bling_id: int) -> tuple[str | None, str | None]:
    """(telefone no formato do wa.me, e-mail) do cadastro do contato do pedido
    no Bling. O celular vale mais que o telefone fixo."""
    r = await client._request("GET", f"/pedidos/vendas/{bling_id}")
    r.raise_for_status()
    pedido = r.json().get("data") or {}
    contato = pedido.get("contato") if isinstance(pedido.get("contato"), dict) else {}
    try:
        contato_id = int((contato or {}).get("id") or 0)
    except (TypeError, ValueError):
        contato_id = 0
    if not contato_id:
        return None, None
    c = await client.get_contato(contato_id)
    telefone = whatsapp(c.get("celular")) or whatsapp(c.get("telefone"))
    email = email_valido(c.get("email")) or email_valido(c.get("emailNotaFiscal"))
    return telefone, email


async def preencher(session: AsyncSession, *, limite: int = POR_RODADA, client=None) -> dict:
    """Uma volta: as garantias que o robô ainda não leu no Bling (as mais
    novas primeiro), menos as plataformas cujo contato o Bling não tem. Bling
    fora do ar ou erro de rede: a garantia fica para a próxima volta. NÃO
    commita."""
    from app.services import logistica_amazon_bling, logistica_bling  # tardio: import circular

    resumo = {
        "alvo": 0,
        "lidos": 0,
        "com_telefone": 0,
        "com_email": 0,
        "sem_pedido": 0,
        "falhas": 0,
    }
    alvo = list(
        (
            await session.execute(
                select(Garantia)
                .where(
                    Garantia.contato_lido_em.is_(None),
                    Garantia.pedido_bling.isnot(None),
                    or_(
                        Garantia.plataforma.is_(None),
                        Garantia.plataforma.notin_(sorted(PLATAFORMAS_SEM_CONTATO)),
                    ),
                )
                .order_by(Garantia.id.desc())
                .limit(limite)
            )
        ).scalars()
    )
    resumo["alvo"] = len(alvo)
    if not alvo:
        return resumo
    if client is None:
        try:
            client = await logistica_bling._bling_client(session)
        except Exception as e:  # noqa: BLE001 — sem Bling, tenta na próxima volta
            logger.warning("garantia_contato_sem_bling", err=str(e)[:200])
            return resumo
    ids = await logistica_amazon_bling._bling_ids(session, [g.pedido_bling or "" for g in alvo])
    for g in alvo:
        bling_id = ids.get((g.pedido_bling or "").strip())
        if not bling_id:
            # O espelho do pedido ainda não chegou: tenta na próxima volta.
            resumo["sem_pedido"] += 1
            continue
        try:
            telefone, email = await ler_do_bling(client, bling_id)
        except httpx.HTTPStatusError as e:
            if e.response.status_code != 404:
                resumo["falhas"] += 1
                continue
            telefone, email = None, None  # pedido apagado no Bling: nada a ler
        except Exception as e:  # noqa: BLE001 — rede/Bling fora: próxima volta
            resumo["falhas"] += 1
            logger.warning("garantia_contato_falhou", garantia=g.id, err=str(e)[:200])
            continue
        g.contato_telefone = telefone
        g.contato_email = email
        g.contato_lido_em = datetime.now(UTC)
        resumo["lidos"] += 1
        resumo["com_telefone"] += telefone is not None
        resumo["com_email"] += email is not None
    await session.flush()
    return resumo
