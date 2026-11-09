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
from sqlalchemy import or_, select, update
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


# Os DDDs que existem (Anatel).
DDDS = frozenset(
    {11, 12, 13, 14, 15, 16, 17, 18, 19, 21, 22, 24, 27, 28}
    | set(range(31, 36))
    | {37, 38}
    | set(range(41, 47))
    | {47, 48, 49, 51, 53, 54, 55}
    | set(range(61, 70))
    | {71, 73, 74, 75, 77, 79}
    | set(range(81, 90))
    | set(range(91, 100))
)
_RX_RAMAL = re.compile(r"\s*(?:ramal|ext\.?|r\.)\s*\d.*$", re.IGNORECASE | re.DOTALL)
_RX_OPERADORA_XX = re.compile(r"^\s*0\s*x\s*x\s*", re.IGNORECASE)


def telefone(texto: str | None) -> str | None:
    """O telefone brasileiro só com dígitos e o 55 na frente (o formato do
    wa.me): "5519988887777". Aceita "(19) 98888-7777", "+55 19 98888-7777",
    "(019) 98888-7777" (o 0 de longa distância), "0xx19…" e "015 19 …" (o
    código da operadora), e o que vem antes do link que o e-mail do SAC às
    vezes cola ("… — https://wa.me/…"); o ramal sai. O celular antigo sem o 9
    ganha o 9. Sem DDD, DDD que não existe, número estrangeiro, mascarado
    ("9****-7777") ou com cara de número de teste → None. PURA."""
    t = re.split(r"\s[—–]\s|https?://", texto or "", maxsplit=1)[0]
    t = _RX_RAMAL.sub("", t)
    if "*" in t:
        return None
    t = _RX_OPERADORA_XX.sub("0", t).strip()
    d = svc.so_digitos(t)
    # Número de outro país: "+1 …", "00 1 …".
    if (t.startswith("+") and not d.startswith("55")) or (
        d.startswith("00") and not d.startswith("0055")
    ):
        return None
    if d.startswith("0055"):
        d = d[2:]
    if d.startswith("55") and (len(d) in (12, 13) or (len(d) == 14 and d[2] == "0")):
        d = d[2:]  # o 55 do país ("+55 (019) …" traz o 0 junto)
    if len(d) in (11, 12) and d.startswith("0"):  # 0 + DDD + número
        d = d[1:]
    elif len(d) in (13, 14) and d.startswith("0"):  # 0 + operadora + DDD + número
        d = d[3:]
    if len(d) not in (10, 11) or not d[:2].isdigit() or int(d[:2]) not in DDDS:
        return None
    numero = d[2:]
    if len(numero) == 9 and numero[0] != "9":
        return None  # 9 dígitos é celular, e o celular começa com 9
    if len(numero) == 8 and numero[0] in "6789":
        numero = "9" + numero  # o celular de antes do 9º dígito
    elif len(numero) == 8 and numero[0] not in "2345":
        return None
    if len(set(numero[1:])) <= 1:
        return None  # "99999-9999", "3333-3333"
    return "55" + d[:2] + numero


def eh_celular(numero: str) -> bool:
    """O telefone (já no formato de `telefone`) é celular — o que tem WhatsApp."""
    return len(numero) == 13


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
    """(telefone no formato de `telefone`, e-mail) do cadastro do contato do
    pedido no Bling. O celular vale mais que o telefone fixo."""
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
    fones = [f for f in (telefone(c.get("celular")), telefone(c.get("telefone"))) if f]
    # O celular (o que tem WhatsApp) vale mais que o fixo, venha de qual campo vier.
    fone = next((f for f in fones if eh_celular(f)), fones[0] if fones else None)
    email = email_valido(c.get("email")) or email_valido(c.get("emailNotaFiscal"))
    return fone, email


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
        pedido_lido = g.pedido_bling
        try:
            fone, email = await ler_do_bling(client, bling_id)
        except httpx.HTTPStatusError as e:
            if e.response.status_code != 404:
                resumo["falhas"] += 1
                continue
            fone, email = None, None  # pedido apagado no Bling: nada a ler
        except Exception as e:  # noqa: BLE001 — rede/Bling fora: próxima volta
            resumo["falhas"] += 1
            logger.warning("garantia_contato_falhou", garantia=g.id, err=str(e)[:200])
            continue
        # Só grava se ninguém trocou o pedido enquanto o Bling respondia: o
        # contato lido é do pedido de antes (revisão de 09/11/2026).
        gravou = await session.execute(
            update(Garantia)
            .where(
                Garantia.id == g.id,
                Garantia.pedido_bling == pedido_lido,
                Garantia.contato_lido_em.is_(None),
            )
            .values(contato_telefone=fone, contato_email=email, contato_lido_em=datetime.now(UTC))
            .execution_options(synchronize_session=False)
        )
        if not gravou.rowcount:
            continue
        resumo["lidos"] += 1
        resumo["com_telefone"] += fone is not None
        resumo["com_email"] += email is not None
    await session.flush()
    return resumo
