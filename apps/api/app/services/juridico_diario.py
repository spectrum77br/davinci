"""Jurídico — as publicações dos processos, lidas no Diário oficial (11/11/2026).

Vinicius: "no jurídico vamos começar a vincular esse Jusbrasil … deixar ele atualizando os
últimos passos sozinho". O Jusbrasil só libera isso por contrato (API paga) e proíbe robô no
site; a fonte oficial e grátis é o **Diário de Justiça Eletrônico Nacional (DJEN)** do CNJ, onde
todos os tribunais publicam intimações, decisões, citações e distribuições — sem login, por nº.

**Quem consulta o DJEN não é o servidor**: o DJEN (atrás do CloudFront) responde 403 ao servidor
de produção, que fica fora do Brasil (testado 11/11 13:30). De um IP do Brasil funciona, e o
DJEN libera CORS. Por isso ("faz nos dois", Vinicius):
- o **Mac mini da Makisa** roda todo dia às 07:30 (`juridico_diario.py`, LaunchAgent
  `com.davinci.juridico-diario`): pega a lista em `GET /api/juridico/sync/processos`, consulta o
  DJEN e manda as publicações em `POST /api/juridico/sync/processos/{id}/diario` (mesmo token
  da cópia da Denúncia) e o resumo em `POST /api/juridico/sync/leitura`;
- o **navegador** (botão "Buscar no Diário" no topo do painel e na janela do processo) consulta
  o DJEN e manda em `POST /api/juridico/processos/{id}/diario` (+ `POST /api/juridico/leituras`).

Cada leitura fica em `juridico_leituras`; o topo do painel mostra a última ("Diário lido hoje
07:31 pelo Mac mini") e fica vermelho quando o mini passa de um dia sem ler. Não é robô da
Ouvidoria: lá só entra o que o servidor roda (tick + "Rodar agora").

Aqui fica só o que o DaVinci faz com o que chega: lançar no histórico as comunicações que ainda
não estão lá (`externo_id` = id da comunicação no DJEN), sem as canceladas, e preencher a Vara
vazia com o órgão da publicação mais nova.
O DJEN só tem o que é PUBLICADO; passo interno do cartório ("conclusos") não aparece.
"""

from __future__ import annotations

import hashlib
import html
import re
from datetime import UTC, date, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.juridico import (
    JuridicoLeitura,
    JuridicoMovimentacao,
    JuridicoPedido,
    JuridicoProcesso,
)

logger = structlog.get_logger()



def numero_20(numero: str | None) -> str | None:
    """O nº CNJ só com os 20 dígitos (4206943-64.2026.8.26.0100 → 42069436420268260100)."""
    d = re.sub(r"\D", "", numero or "")
    return d if len(d) == 20 else None


def texto_limpo(bruto: str | None) -> str:
    """O texto da publicação vem em HTML: vira texto com quebras de linha."""
    t = bruto or ""
    t = re.sub(r"<(style|script)\b.*?</\1>", " ", t, flags=re.S | re.I)
    t = re.sub(r"<\s*(br|/p|/tr|/section|/div|/h\d)\b[^>]*>", "\n", t, flags=re.I)
    t = re.sub(r"<\s*/?\s*td\b[^>]*>", " ", t, flags=re.I)
    t = html.unescape(re.sub(r"<[^>]+>", " ", t))
    t = re.sub(r"[ \t ]+", " ", t)
    linhas = [ln.strip() for ln in t.splitlines()]
    return "\n".join(ln for ln in linhas if ln).strip()


def resumo(item: dict) -> str:
    """O que a tabela e a linha do tempo mostram: o tipo da publicação e do documento."""
    tipo = (item.get("tipoComunicacao") or "Publicação").strip()
    doc = (item.get("tipoDocumento") or "").strip()
    if tipo.lower().startswith("lista de distribui"):
        orgao = (item.get("nomeOrgao") or "").strip()
        return f"Distribuído para {orgao}" if orgao else "Distribuído"
    if doc and doc.lower() not in ("outros", tipo.lower()):
        return f"{tipo} · {doc[:1].upper()}{doc[1:].lower()}"
    return tipo


def _data(item: dict) -> date | None:
    v = str(item.get("data_disponibilizacao") or item.get("datadisponibilizacao") or "")[:10]
    try:
        return datetime.strptime(v, "%Y-%m-%d").date()
    except ValueError:
        return None


async def lancar(session: AsyncSession, p: JuridicoProcesso, itens: list[dict]) -> int:
    """Lança as comunicações que ainda não estão no histórico; devolve quantas entraram."""
    novas = 0
    mais_nova: tuple[date, str] | None = None
    for it in itens:
        if it.get("ativo") is False or it.get("data_cancelamento"):
            continue  # publicação cancelada no Diário
        quando = _data(it)
        externo = str(it.get("id") or it.get("hash") or "").strip()
        if quando is None or not externo:
            continue
        orgao = (it.get("nomeOrgao") or "").strip() or None
        if orgao and (mais_nova is None or quando >= mais_nova[0]):
            mais_nova = (quando, orgao)
        ins = (
            pg_insert(JuridicoMovimentacao)
            .values(
                processo_id=p.id,
                data=quando,
                texto=resumo(it)[:500],
                fonte="djen",
                criado_por="Diário oficial (DJEN)",
                externo_id=externo,
                orgao=orgao,
                tipo=(it.get("tipoComunicacao") or None),
                texto_completo=texto_limpo(it.get("texto"))[:60000] or None,
                link=(it.get("link") or None),
            )
            .on_conflict_do_nothing(
                index_elements=["processo_id", "externo_id"],
                index_where=text("externo_id IS NOT NULL"),
            )
            .returning(JuridicoMovimentacao.id)
        )
        if (await session.execute(ins)).scalar_one_or_none() is not None:
            novas += 1
    if mais_nova and not (p.vara or "").strip():
        p.vara = mais_nova[1]
    return novas


# campos da comunicação do DJEN que o DaVinci usa (o resto é descartado)
_CAMPOS_ITEM = (
    "id", "hash", "data_disponibilizacao", "datadisponibilizacao", "tipoComunicacao",
    "tipoDocumento", "nomeOrgao", "texto", "link", "ativo", "data_cancelamento",
)
MAX_ITENS = 1000


def itens_validos(corpo: Any) -> list[dict]:
    """O que o navegador ou o mini mandou: lista de comunicações do DJEN, só os campos usados."""
    itens = (corpo or {}).get("itens") if isinstance(corpo, dict) else None
    if not isinstance(itens, list) or len(itens) > MAX_ITENS:
        raise ValueError("itens")
    return [{k: it.get(k) for k in _CAMPOS_ITEM} for it in itens if isinstance(it, dict)]


async def registrar_leitura(
    session: AsyncSession, origem: str, por: str | None, corpo: dict
) -> JuridicoLeitura:
    """Uma leitura do Diário (do Mac mini ou do botão do navegador), com o que ela achou."""
    def n(k: str) -> int:
        try:
            return max(0, int(corpo.get(k) or 0))
        except (TypeError, ValueError):
            return 0

    lei = JuridicoLeitura(
        origem=origem,
        por=(por or "")[:200] or None,
        processos=n("processos"),
        publicacoes=n("publicacoes"),
        novas=n("novas"),
        falhas=n("falhas"),
        erro=str(corpo.get("erro") or "").strip()[:500] or None,
    )
    session.add(lei)
    return lei


# ── Jusbrasil (11/11/2026, Vinicius: "o jusbrasil dá bastante as movimentações … trabalhar com
# os dois"). O robô do Mac mini lê a página do processo no perfil 50 (logado pela MAKISA), um
# processo por vez e devagar, e manda os "Andamento" do Histórico — citação expedida/confirmada,
# prazo decorrido, procuração juntada… — que o Diário não tem. As "Publicação" ficam de fora: já
# vêm do Diário com o texto inteiro. Avisado antes: os termos do Jusbrasil proíbem robô; ele
# assumiu o risco ("se fizer devagar, ele não vai perceber").
_LINK_JUSBRASIL = re.compile(r"^https://(www\.)?jusbrasil\.com\.br/processos/\d+/")
JUSBRASIL_INTERVALO_BOTAO = timedelta(hours=2)
PEDIDO_MORTO = timedelta(hours=3)


def _data_br(v: Any) -> date | None:
    s = str(v or "").strip()
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s[:10], fmt).date()
        except ValueError:
            continue
    return None


async def lancar_jusbrasil(
    session: AsyncSession, p: JuridicoProcesso, link: str | None, andamentos: list[dict]
) -> int:
    """Os andamentos lidos no Jusbrasil entram no histórico (uma vez cada: data + texto)."""
    link = (link or "").strip()
    if link and _LINK_JUSBRASIL.match(link) and not (p.jusbrasil_link or "").strip():
        p.jusbrasil_link = link[:500]
    novas = 0
    for a in andamentos[:MAX_ITENS]:
        if not isinstance(a, dict):
            continue
        quando = _data_br(a.get("data"))
        texto = " ".join(str(a.get("texto") or "").split())
        if quando is None or not texto:
            continue
        chave = hashlib.sha256(f"{quando.isoformat()}|{texto}".encode()).hexdigest()[:24]
        ins = (
            pg_insert(JuridicoMovimentacao)
            .values(
                processo_id=p.id,
                data=quando,
                texto=texto[:500],
                fonte="jusbrasil",
                criado_por="Jusbrasil",
                externo_id=f"jb:{chave}",
                tipo=(str(a.get("tipo") or "Andamento"))[:60],
                texto_completo=texto if len(texto) > 500 else None,
                link=p.jusbrasil_link,
            )
            .on_conflict_do_nothing(
                index_elements=["processo_id", "externo_id"],
                index_where=text("externo_id IS NOT NULL"),
            )
            .returning(JuridicoMovimentacao.id)
        )
        if (await session.execute(ins)).scalar_one_or_none() is not None:
            novas += 1
    return novas


async def pedido_aberto(
    session: AsyncSession, agora: datetime | None = None
) -> JuridicoPedido | None:
    """O pedido que ainda não terminou (e não morreu: mini sem dar notícia há mais de 3 h)."""
    agora = agora or datetime.now(UTC)
    return (
        await session.execute(
            select(JuridicoPedido)
            .where(
                JuridicoPedido.terminado_em.is_(None),
                JuridicoPedido.pedido_em >= agora - PEDIDO_MORTO,
            )
            .order_by(JuridicoPedido.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def pedir_leitura(
    session: AsyncSession, origem: str, por: str | None = None, agora: datetime | None = None
) -> tuple[JuridicoPedido | None, str]:
    """Cria o pedido ao Mac mini. Às 06:00 ('cron') sempre com Jusbrasil; no botão ('botao'), o
    Jusbrasil só se a última leitura dele (ou pedido) tiver mais de 2 h — senão não pede nada (o
    Diário o próprio navegador já buscou). Já tem um em andamento: devolve esse."""
    agora = agora or datetime.now(UTC)
    aberto = await pedido_aberto(session, agora)
    if aberto is not None:
        return aberto, "em_andamento"
    if origem == "botao":
        limite = agora - JUSBRASIL_INTERVALO_BOTAO
        recente = (
            await session.execute(
                select(JuridicoPedido.id)
                .where(JuridicoPedido.jusbrasil.is_(True), JuridicoPedido.pedido_em >= limite)
                .limit(1)
            )
        ).scalar_one_or_none()
        lido = (
            await session.execute(
                select(JuridicoLeitura.id)
                .where(JuridicoLeitura.origem == "jusbrasil", JuridicoLeitura.quando >= limite)
                .limit(1)
            )
        ).scalar_one_or_none()
        if recente is not None or lido is not None:
            return None, "jusbrasil_lido_ha_pouco"
    ped = JuridicoPedido(origem=origem, pedido_por=por, jusbrasil=True, pedido_em=agora)
    session.add(ped)
    await session.flush()
    return ped, "pedido"

