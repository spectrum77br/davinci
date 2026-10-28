"""SAC do site na garantia (28/10/2026).

Pedido do Eduardo: "lembrando que tem o SAC também, que precisamos ter ali e
ver o e-mail do SAC". O formulário de uranyx.com.br/sac chega por e-mail (de
sac@ para sac@) e a ponte já faz dele uma conversa por PROTOCOLO
(`mail-protocolo:US-26-NNNN`, plataforma `site`, canal `email`) — o corpo do
formulário fica numa mensagem, uma linha por campo, o RÓTULO colado no VALOR
("CPF123.456.789-09", "Número do pedido261027QC4PXFRR", "Número da nota
fiscal (se disponível)—"; o travessão é campo vazio).

Aqui o robô lê o CPF, a nota fiscal e o nº do pedido do formulário e LIGA a
conversa do protocolo à garantia (ponteiro em `garantia_conversas`, como as
conversas dos marketplaces) — só quando o CPF do formulário é o CPF da
garantia (o da NF de produto) E a nota (qualquer NF do pedido: o cliente
dos Correios às vezes só tem a de embalagem) ou o pedido também batem. O
pedido citado sozinho nunca liga (`mail_atendimento/chamados.py`: "pode ser o
pedido de outra pessoa"); o CPF sozinho também não (a mesma pessoa compra
mais de um aparelho).

Só lê o que a ponte já gravou; nada sai para fora.
"""

from __future__ import annotations

import re
from datetime import datetime

from sqlalchemy import exists, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.atendimento import AtendimentoConversa, AtendimentoMensagem
from app.models.garantia import Garantia, GarantiaConversa
from app.models.nf import NfNota
from app.services import garantia as svc
from app.services.mail_atendimento.ponte import PREFIXO_PROTOCOLO

# O campo vazio do e-mail do site.
_VAZIO = {"", "—", "–", "-"}
# Rótulo no começo da linha, com ou sem a explicação entre parênteses e com ou
# sem ":" — o valor vem colado. `CPF` não pode continuar em letra ("CPFs").
_CAMPOS = {
    "cpf": re.compile(r"^\s*CPF(?![A-Za-zÀ-ÿ])\s*:?\s*(.*)$", re.IGNORECASE),
    "pedido": re.compile(r"^\s*N[uú]mero do pedido(?:\s*\([^)]*\))?\s*:?\s*(.*)$", re.IGNORECASE),
    "nota_fiscal": re.compile(
        r"^\s*N[uú]mero da nota fiscal(?:\s*\([^)]*\))?\s*:?\s*(.*)$", re.IGNORECASE
    ),
    "local_compra": re.compile(r"^\s*Local da compra(?:\s*\([^)]*\))?\s*:?\s*(.*)$", re.IGNORECASE),
}
# Pedido de verdade tem 5+ caracteres (o "9" do formulário de teste não é).
PEDIDO_MIN = 5


def campos_do_sac(texto: str | None) -> dict[str, str | None]:
    """CPF (11 dígitos válidos), nota fiscal (só dígitos, sem zero à
    esquerda), pedido (letras e números, maiúsculo) e local da compra do corpo
    do formulário. Campo vazio, ausente ou sem cara do que é → None. PURA."""
    achados: dict[str, str] = {}
    for linha in (texto or "").splitlines():
        for chave, rx in _CAMPOS.items():
            if chave in achados:
                continue
            m = rx.match(linha)
            if m:
                achados[chave] = m.group(1).strip()
                break
    cpf = svc.so_digitos(achados.get("cpf"))
    nf = svc.normalizar_nf(achados.get("nota_fiscal"))
    pedido = re.sub(r"[^0-9A-Za-z]", "", achados.get("pedido") or "").upper()
    local = (achados.get("local_compra") or "").strip()
    return {
        "cpf": cpf if svc.cpf_valido(cpf) else None,
        "nota_fiscal": nf if nf and len(nf) <= 9 else None,
        "pedido": pedido if len(pedido) >= PEDIDO_MIN else None,
        "local_compra": None if local in _VAZIO else local,
    }


def protocolo_da_conversa(c: AtendimentoConversa) -> str | None:
    """`mail-protocolo:US-26-0004` → `US-26-0004` (None se não é do SAC)."""
    ext = c.externo_id or ""
    if not ext.startswith(PREFIXO_PROTOCOLO):
        return None
    dados = c.dados if isinstance(c.dados, dict) else {}
    mail = dados.get("mail") if isinstance(dados.get("mail"), dict) else {}
    return (mail.get("protocolo") or ext[len(PREFIXO_PROTOCOLO) :].split(":", 1)[0]) or None


async def _formulario(session: AsyncSession, conversa_id) -> dict[str, str | None] | None:
    """Os campos do 1º e-mail da conversa que tem CPF e (nota ou pedido)."""
    m = AtendimentoMensagem
    textos = (
        await session.execute(
            select(m.texto)
            .where(m.conversa_id == conversa_id, m.texto.is_not(None))
            .order_by(func.coalesce(m.enviada_em, m.created_at), m.id)
        )
    ).scalars()
    for texto in textos:
        campos = campos_do_sac(texto)
        if campos["cpf"] and (campos["nota_fiscal"] or campos["pedido"]):
            return campos
    return None


async def garantias_do_formulario(session: AsyncSession, campos: dict) -> list[int]:
    """As garantias (vivas) do CPF do formulário cuja NF ou pedido também
    batem. NF = a da garantia ou qualquer NF do mesmo pedido (a de embalagem
    que o cliente recebeu por fora); pedido = o da plataforma ou o do Bling."""
    cpf, nf, pedido = campos.get("cpf"), campos.get("nota_fiscal"), campos.get("pedido")
    if not cpf or not (nf or pedido):
        return []
    g = Garantia
    conds = []
    if nf:
        conds.append(g.nf_numero == nf)
        conds.append(
            exists().where(
                NfNota.pedido_bling == g.pedido_bling,
                func.ltrim(func.regexp_replace(NfNota.numero, r"\D", "", "g"), "0") == nf,
            )
        )
    if pedido:
        conds.append(
            func.upper(func.regexp_replace(g.pedido_marketplace, r"[^0-9A-Za-z]", "", "g"))
            == pedido
        )
        conds.append(g.pedido_bling == pedido)
    return list(
        (
            await session.execute(
                select(g.id)
                .where(g.cpf == cpf, g.descartada_em.is_(None), or_(*conds))
                .order_by(g.id)
            )
        ).scalars()
    )


async def ligar_sac(session: AsyncSession, *, desde: datetime | None = None) -> int:
    """Liga os protocolos do SAC (ainda sem garantia) à garantia do
    formulário. `desde` = só as conversas criadas/mexidas depois (a volta de
    15 min); sem ele, todas (a rodada diária e o retroativo). NÃO commita.
    Devolve quantas ligações novas."""
    c = AtendimentoConversa
    q = select(c.id).where(
        c.externo_id.like(PREFIXO_PROTOCOLO + "%"),
        ~exists().where(GarantiaConversa.conversa_id == c.id),
    )
    if desde is not None:
        q = q.where(
            or_(c.created_at >= desde, c.updated_at >= desde, c.ultima_mensagem_em >= desde)
        )
    novas = 0
    for conversa_id in list((await session.execute(q)).scalars()):
        campos = await _formulario(session, conversa_id)
        if not campos:
            continue
        for gid in await garantias_do_formulario(session, campos):
            ins = (
                pg_insert(GarantiaConversa)
                .values(garantia_id=gid, conversa_id=conversa_id, como="pedido")
                .on_conflict_do_nothing()
            )
            novas += (await session.execute(ins)).rowcount or 0
    return novas
