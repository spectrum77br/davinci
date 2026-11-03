"""A AMOSTRA do eval do agente: os gatilhos congelados, com o rótulo da spec 14 (20/10/2026).

Lê os turnos do cliente dos últimos `--dias` em que a EQUIPE respondeu
(Duoke ou DaVinci, em até 24 h; nunca a automática nem a IA) — a Kia
Shopee primeiro (o piloto, decisão 3 do dono) — e congela cada um num item:
a conversa, o gatilho e o instante dele (o `as_of` do replay), o estrato da
spec 9.1 e o rótulo da spec 14 quando o código consegue dizer:

- C (trava dura): instrução para a IA, Procon/Justiça/golpe/xingamento,
  reclamação aberta, Ag. cancelamento, avaliação baixa;
- A2 (depende de fato): a dúvida de remetente;
- P (pessoa decide): pedido de desconto, brinde, amostra;
- B (ação de pessoa): os outros gatilhos de código da régua 6.3;
- S (sem demanda): só o cartão, só cortesia, nada concreto.

O resto (A1 ou B que só a leitura diz) fica `rotulo: null` — a sessão do
Claude que dá nota rotula junto (o campo `rotulo` da nota vence o daqui).
O conjunto (dev ou teste) vem do comprador (o mesmo comprador nunca cai nos
dois, spec 9.1). Uma conversa entra uma vez (o turno mais recente). Com mais
itens do que `--n`, a escolha segue as cotas da spec 9.1 por estrato, numa
ordem fixa pela `--semente` (rodar de novo dá a mesma amostra).

SÓ LEITURA (sessão `READ ONLY`). Nada de texto no arquivo: só ids, datas,
estrato, rótulo, as pistas (nomes de assunto), os códigos da régua e o
título do anúncio (o aquecimento usa); o comprador vira um hash curto.

Uso (de dentro de apps/api, no container da api):
    uv run python -m scripts.agente_eval.amostra --saida /tmp/agente_eval/amostra.jsonl
    uv run python -m scripts.agente_eval.amostra --loja todas --dias 14 --n 200 \\
        --conjunto teste --saida /tmp/agente_eval/amostra_teste.jsonl

  --loja kia|todas|<uuid>  kia (padrão) = o canal da Kia Shopee; todas = os
                           canais da --plataforma; um uuid de canal ou de loja.
  --plataforma P           shopee (padrão).
  --dias N                 os últimos N dias (padrão 30).
  --desde D --ate D        no lugar do --dias, uma janela explícita em datas de São Paulo
                           (AAAA-MM-DD, o --ate inclusive; 03/11/2026). Pode repetir em pares
                           para dias que não se encostam (--desde 2026-10-30 --ate 2026-10-30
                           --desde 2026-11-03 --ate 2026-11-03); um --desde sozinho vai até agora.
  --excluir A.jsonl ...    tira os gatilhos que já estão nessas amostras (o que outro eval usou).
  --n N                    tamanho da amostra (padrão 60).
  --conjunto dev|teste|todos   (padrão todos).
  --semente S              a ordem da escolha (padrão 20261020).

  --gatilhos ARQ           CONGELA exatamente os turnos dados (03/11/2026): um id de mensagem
                           do cliente por linha (o 1º do turno, como o turnos.sql da análise,
                           ou o próprio gatilho; '#' comenta). Cada id vira o turno do replay que
                           tem essa mensagem na rajada — respondido pela equipe, pela IA ou por
                           ninguém (o `fechado_por` do item). Sem cota, sem --n, sem uma
                           conversa por vez; o item guarda os ids pedidos (`pedidos`). Não se
                           mistura com --desde/--ate.
"""

# ruff: noqa: T201

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import sys
from collections import Counter
from collections.abc import Collection, Iterable, Mapping, Sequence
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any, NamedTuple
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AtendimentoCanal, AtendimentoConversa, AtendimentoMensagem
from app.services.atendimento import constantes, ia, validador
from app.services.atendimento import ficha as ficha_svc
from app.services.atendimento import variacoes as variacoes_svc
from app.services.atendimento.agente import acao_pessoa, replay

# O piloto (decisão 3 do dono): o canal da Kia Shopee.
CANAL_KIA = UUID("1ca982c3-96e0-4000-95f2-e1fe368f954d")
SEMENTE = "20261020"
SAIDA_PADRAO = Path("/tmp/agente_eval/amostra.jsonl")  # noqa: S108 — dentro do container

# Os estratos da spec 9.1, na ordem em que um turno é classificado (o 1º que
# casar), e a cota de cada um num conjunto de teste de 200 itens.
TRAVA = "trava"
ACAO_PESSOA = "acao_pessoa"
SO_FOTO = "so_foto"
SEM_DEMANDA = "sem_demanda"
DEPENDE_DE_FATO = "depende_de_fato"
NOTA_FISCAL = "nota_fiscal"
ESTOQUE_VARIACAO = "estoque_variacao"
ORIENTACAO = "orientacao"
PRAZO_RASTREIO = "prazo_rastreio"
ESPECIFICACAO = "especificacao"
OUTRO = "outro"
ESTRATOS = (
    TRAVA,
    ACAO_PESSOA,
    SO_FOTO,
    SEM_DEMANDA,
    DEPENDE_DE_FATO,
    NOTA_FISCAL,
    ESTOQUE_VARIACAO,
    ORIENTACAO,
    PRAZO_RASTREIO,
    ESPECIFICACAO,
    OUTRO,
)
COTAS_EM_200: dict[str, int] = {
    ORIENTACAO: 50,
    DEPENDE_DE_FATO: 15,
    ACAO_PESSOA: 30,
    TRAVA: 15,
    ESTOQUE_VARIACAO: 30,
    PRAZO_RASTREIO: 20,
    NOTA_FISCAL: 10,
    ESPECIFICACAO: 15,
    SEM_DEMANDA: 10,
    SO_FOTO: 5,
}

# A trava dura (C) da régua 6.3: o que fica com pessoa de qualquer jeito.
TRAVAS = frozenset(
    {
        constantes.ACAO_AG_CANCELAMENTO,
        constantes.ACAO_JURIDICO_FRAUDE_OFENSA,
        constantes.ACAO_RECLAMACAO_ABERTA,
        constantes.ACAO_AVALIACAO_BAIXA,
        constantes.ACAO_INSTRUCAO,
    }
)
# As pistas dos assuntos de orientação (os 14 "só pessoa" do manual base).
PISTAS_DE_ORIENTACAO = frozenset(
    {
        "cancelamento",
        "reembolso",
        "defeito",
        "troca_devolucao",
        "garantia",
        "endereco",
        "desconto",
        "reclamacao_forte",
    }
)
PISTAS_DE_PRAZO = frozenset({"rastreio", "prazo_envio"})
# Especificação, compatibilidade e conteúdo da caixa (sobre a forma de busca
# do validador: sem acento, minúscula).
_ESPECIFICACAO = re.compile(
    r"\b(?:nfc|5g|4g|ram|memoria|armazenamento|\d+\s*gb|bateria|mah|tela|polegadas?|camera"
    r"|processador|android|ios|chip|dual\s*chip|esim|bivolt|voltagem|110v?|220v?|potencia"
    r"|watts?|tamanho|medidas?|altura|largura|profundidade|peso|kg|litros?|capacidade"
    r"|material|tsa|cadeado|rodinhas?|compativel|serve\s+(?:no|na|para|pro|pra)|acompanha"
    r"|vem\s+com|carregador|fone|capinha|pelicula|prova\s+d.?agua|resistente)\b"
)


class Rotulo(NamedTuple):
    """O rótulo de um turno: (rótulo, estrato, fonte do rótulo, pistas, códigos da régua)."""

    rotulo: str | None
    estrato: str
    fonte: str | None
    pistas: tuple[str, ...]
    acoes: tuple[str, ...]


def rotular(conversa: Any, historico: Sequence[Any], turno: replay.Turno) -> Rotulo:
    """O estrato da spec 9.1 e o rótulo da spec 14 QUANDO o código sabe. PURA.

    `historico` = as mensagens até o gatilho (`Turno.historico`). A régua é a
    de `acao_pessoa.gatilhos` sem o pedido (o estado de postagem não entra:
    `segurar_envio`/`alterar_endereco` ficam para a leitura).
    """
    recentes = list(historico) or list(turno.rajada)
    gatilho = turno.gatilho
    rajada = ia.rajada_do_cliente(recentes, gatilho)
    textos = [m.texto for m in rajada if (m.texto or "").strip()]
    foco = ia.perguntas_pendentes(recentes, gatilho)
    textos_foco = [m.texto or "" for m in (foco or rajada)]
    plano = validador.plano_de(" ".join(textos))
    pistas = tuple(ia.pistas_de_categoria(plano))
    acoes = tuple(
        g.codigo
        for g in acao_pessoa.gatilhos(
            conversa,
            recentes,
            {},
            gatilho=gatilho,
            # O estado de postagem não entra: como postado, sem os gatilhos 7
            # e 8 (o None conta como "pode não ter saído" desde 20/10/2026).
            postado=acao_pessoa.POSTADO_SIM,
            chaves_ativas=frozenset(),
        )
    )

    def r(rotulo: str | None, estrato: str) -> Rotulo:
        return Rotulo(rotulo, estrato, "codigo" if rotulo else None, pistas, acoes)

    if set(acoes) & TRAVAS:
        return r("C", TRAVA)
    if any(ia.duvida_de_remetente(t) for t in textos_foco):
        return r("A2", DEPENDE_DE_FATO)
    if constantes.ACAO_DESCONTO_BRINDE in acoes:
        return r("P", ACAO_PESSOA)
    if acoes:
        return r("B", ACAO_PESSOA)
    if not textos and not ia._so_cartao(rajada):
        return r(None, SO_FOTO)
    if ia._so_cartao(rajada) or not foco:
        return r("S", SEM_DEMANDA)
    if "nota_fiscal" in pistas:
        return r(None, NOTA_FISCAL)
    if variacoes_svc.pede_variacao(textos_foco):
        return r(None, ESTOQUE_VARIACAO)
    if set(pistas) & PISTAS_DE_ORIENTACAO:
        return r(None, ORIENTACAO)
    if set(pistas) & PISTAS_DE_PRAZO:
        return r(None, PRAZO_RASTREIO)
    if _ESPECIFICACAO.search(validador.plano_de(" ".join(textos_foco))):
        return r(None, ESPECIFICACAO)
    return r(None, OUTRO)


def _hash(*partes: Any, n: int = 12) -> str:
    return hashlib.sha256(":".join(str(p) for p in partes).encode()).hexdigest()[:n]


def id_do_item(gatilho_id: Any) -> str:
    """O id do item: um hash curto do gatilho (estável; não revela o id da mensagem)."""
    return _hash("item", gatilho_id)


def comprador_do(conversa: Any) -> str:
    """O comprador como hash curto (a loja + o id dele na plataforma; sem ele, a conversa)."""
    return _hash("comprador", conversa.integration_id, conversa.comprador_id or conversa.id, n=10)


def conjunto_do(comprador: str) -> str:
    """dev (1 em 5 compradores) ou teste — o mesmo comprador, sempre o mesmo conjunto. PURA."""
    return "dev" if int(_hash("conjunto", comprador), 16) % 5 == 0 else "teste"


def _ordem(semente: str, item: Mapping[str, Any]) -> str:
    return _hash(semente, item["id"], n=16)


def escolher(
    itens: Sequence[Mapping[str, Any]],
    *,
    n: int,
    semente: str = SEMENTE,
    cotas: Mapping[str, int] = COTAS_EM_200,
) -> list[dict]:
    """Até `n` itens: primeiro a cota de cada estrato (a da spec 9.1, na escala de `n`),
    depois o que faltar, na ordem da semente. Determinístico. PURA."""
    ordenados = sorted((dict(i) for i in itens), key=lambda i: _ordem(semente, i))
    if len(ordenados) <= n:
        return ordenados
    escolhidos: list[dict] = []
    ids: set[str] = set()
    for estrato in ESTRATOS:
        cota = round(n * cotas.get(estrato, 0) / 200)
        for item in (i for i in ordenados if i.get("estrato") == estrato):
            if cota <= 0:
                break
            escolhidos.append(item)
            ids.add(item["id"])
            cota -= 1
    for item in ordenados:
        if len(escolhidos) >= n:
            break
        if item["id"] not in ids:
            escolhidos.append(item)
            ids.add(item["id"])
    return sorted(escolhidos[:n], key=lambda i: _ordem(semente, i))


def _anuncios(conversa: Any, historico: Sequence[Any]) -> list[dict]:
    return [
        {"item_id": a.item_id, "titulo": a.titulo, "variation_id": a.variation_id}
        for a in ficha_svc.anuncios_da_conversa(conversa, list(historico))[
            : ficha_svc.MAX_ANUNCIOS_FICHA
        ]
    ]


def _iso(quando: datetime | None) -> str | None:
    return quando.isoformat() if quando else None


def _item(conversa: Any, turno: replay.Turno) -> dict:
    """O item da amostra (sem texto nenhum: ids, datas, estrato, rótulo, pistas, anúncios)."""
    rot = rotular(conversa, turno.historico, turno)
    comprador = comprador_do(conversa)
    anuncios = _anuncios(conversa, turno.historico)
    return {
        "id": id_do_item(turno.gatilho.id),
        "conversa_id": str(conversa.id),
        "gatilho_id": str(turno.gatilho.id),
        "gatilho_em": _iso(ia._momento(turno.gatilho)),
        "resposta_em": _iso(ia._momento(turno.resposta[0])) if turno.resposta else None,
        # Quem fechou a vez (03/11/2026): "equipe", "ia" ou None (ninguém) — o `--gatilhos`
        # também congela os turnos que a IA respondeu ou que ficaram sem resposta.
        "fechado_por": turno.fechado_por,
        "plataforma": conversa.plataforma,
        "canal": conversa.canal,
        "integration_id": str(conversa.integration_id) if conversa.integration_id else None,
        "canal_id": str(conversa.canal_id) if conversa.canal_id else None,
        "comprador": comprador,
        "conjunto": conjunto_do(comprador),
        "estrato": rot.estrato,
        "rotulo": rot.rotulo,
        "rotulo_fonte": rot.fonte,
        "pistas": list(rot.pistas),
        "acoes_codigo": list(rot.acoes),
        "anuncios": anuncios,
        "anuncio": anuncios[0]["item_id"] if anuncios else None,
    }


def janelas_sp(
    desde: Sequence[date], ate: Sequence[date], *, agora: datetime
) -> list[tuple[datetime, datetime]]:
    """As janelas [início, fim) do `--desde`/`--ate` (datas de São Paulo; o `--ate` inclusive,
    até a meia-noite seguinte). PURA.

    Em pares, na ordem (o 1º `--desde` com o 1º `--ate`...); um `--desde` sozinho vai até
    `agora`. Outra combinação, ou fim antes do início: ValueError.
    """
    if len(ate) != len(desde) and not (len(desde) == 1 and not ate):
        raise ValueError("cada --desde precisa do seu --ate (só um --desde sozinho vai até agora)")

    def meia_noite(dia: date) -> datetime:
        return datetime.combine(dia, time.min, tzinfo=ia.SAO_PAULO).astimezone(UTC)

    saida_: list[tuple[datetime, datetime]] = []
    for i, d in enumerate(desde):
        inicio = meia_noite(d)
        fim = meia_noite(ate[i] + timedelta(days=1)) if ate else agora
        if fim <= inicio:
            raise ValueError(f"janela vazia: --desde {d.isoformat()} depois do fim")
        saida_.append((inicio, fim))
    return saida_


def gatilhos_usados(caminhos: Iterable[Path]) -> frozenset[str]:
    """Os `gatilho_id` das amostras dadas (o `--excluir`: o que outro eval já usou)."""
    usados: set[str] = set()
    for caminho in caminhos:
        usados.update(str(i["gatilho_id"]) for i in ler_jsonl(caminho) if i.get("gatilho_id"))
    return frozenset(usados)


async def montar(
    session: AsyncSession,
    *,
    canal_ids: Iterable[UUID] | None,
    plataforma: str | None = "shopee",
    dias: int = 30,
    n: int = 60,
    semente: str = SEMENTE,
    conjunto: str | None = None,
    por_conversa: int = 1,
    agora: datetime | None = None,
    janelas: Sequence[tuple[datetime, datetime]] | None = None,
    excluir: Collection[str] = frozenset(),
) -> list[dict]:
    """Os itens da amostra (dicionários prontos para o JSONL). Só lê.

    `janelas` = os [início, fim) do `--desde`/`--ate` (sem elas, os últimos `dias`);
    `excluir` = os `gatilho_id` que ficam de fora (o `--excluir`).
    """
    agora = agora or datetime.now(UTC)
    if not janelas:
        janelas = [(agora - timedelta(days=max(1, dias)), agora)]
    canais = list(canal_ids) if canal_ids is not None else None
    candidatos: list[dict] = []
    ids: set[str] = set()
    # Uma conversa entra pelos turnos mais recentes: a janela mais nova primeiro e, dentro
    # dela, o gatilho mais novo primeiro (os turnos de uma conversa vêm no mesmo lote).
    vistos: Counter[Any] = Counter()
    for inicio, fim in sorted(janelas, key=lambda j: j[1], reverse=True):
        # Lote a lote (a Shopee inteira não cabe na memória de uma vez).
        async for achados in replay.turnos_em_lotes(
            session,
            inicio=inicio,
            fim=fim,
            plataformas=[plataforma] if plataforma else None,
            canal_ids=canais,
        ):
            for conversa, turno in sorted(
                achados, key=lambda par: ia._momento(par[1].gatilho), reverse=True
            ):
                if str(turno.gatilho.id) in excluir:
                    continue
                if vistos[conversa.id] >= max(1, por_conversa):
                    continue
                item = _item(conversa, turno)
                if (conjunto and item["conjunto"] != conjunto) or item["id"] in ids:
                    continue
                vistos[conversa.id] += 1
                ids.add(item["id"])
                candidatos.append(item)
    return escolher(candidatos, n=n, semente=semente)


# ── O `--gatilhos`: exatamente os turnos dados (03/11/2026) ───────────────

_SEPARADORES = re.compile(r"[\s|,;]+")
# Os ids no `IN` de uma vez.
LOTE_DE_IDS = 500
# Faltas do `--gatilhos` (o resumo conta; nada some calado).
FALTA_NAO_ACHADO = "nao_achado"
FALTA_CANAL_SEM_ENVIO = "canal_sem_envio"
FALTA_NAO_E_DO_CLIENTE = "nao_e_do_cliente"
FALTA_MESMO_TURNO = "mesmo_turno"
FALTA_FORA_DO_CONJUNTO = "fora_do_conjunto"


def ler_gatilhos(caminho: Path) -> list[UUID]:
    """Os ids do `--gatilhos`: o 1º campo de cada linha ('#' comenta; vazio pula; repetido
    sai). Um campo que não é uuid: SystemExit com a linha."""
    ids: dict[UUID, None] = {}
    for n, linha in enumerate(caminho.read_text(encoding="utf-8").splitlines(), start=1):
        campos = [c for c in _SEPARADORES.split(linha.split("#", 1)[0]) if c]
        if not campos:
            continue
        try:
            ids.setdefault(UUID(campos[0]), None)
        except ValueError:
            erro = f"{caminho}:{n}: não é um id de mensagem: {campos[0][:40]!r}"
            raise SystemExit(erro) from None
    return list(ids)


def turno_do_pedido(turnos_: Sequence[replay.Turno], mensagem_id: Any) -> replay.Turno | None:
    """O turno que tem a mensagem pedida na rajada (o 1º id do turno, como no turnos.sql da
    análise, ou o próprio gatilho). A mensagem que não é do cliente: None. PURA."""
    alvo = str(mensagem_id)
    for t in turnos_:
        if str(t.gatilho.id) == alvo or any(str(m.id) == alvo for m in t.rajada):
            return t
    return None


async def turnos_dos_gatilhos(
    session: AsyncSession, ids: Sequence[UUID]
) -> tuple[list[tuple[Any, replay.Turno, UUID]], Counter[str]]:
    """(conversa, turno, id pedido) de cada id — a mesma leitura do `replay.turnos_em_lotes`
    (sem a que falhou e sem a nota; do `MARGEM_ANTES` antes do 1º pedido até a resposta de
    24 h depois do último), mas SÓ as conversas dos ids e sem filtrar quem respondeu. Só lê."""
    m, c = AtendimentoMensagem, AtendimentoConversa
    momento = ia._momento_col()
    faltas: Counter[str] = Counter()
    achadas: dict[UUID, tuple[UUID, datetime]] = {}
    for i in range(0, len(ids), LOTE_DE_IDS):
        linhas = await session.execute(
            select(m.id, m.conversa_id, momento).where(m.id.in_(list(ids[i : i + LOTE_DE_IDS])))
        )
        for mid, cid, quando in linhas.all():
            achadas[mid] = (cid, quando)
    por_conversa: dict[UUID, list[tuple[UUID, datetime]]] = {}
    for mid in ids:
        if mid not in achadas:
            faltas[FALTA_NAO_ACHADO] += 1
            continue
        cid, quando = achadas[mid]
        por_conversa.setdefault(cid, []).append((mid, quando))
    saida_: list[tuple[Any, replay.Turno, UUID]] = []
    for cid, pedidos in por_conversa.items():
        conversa = await session.get(c, cid)
        if conversa is None or constantes.motivo_canal_sem_envio(
            conversa.canal, conversa.plataforma, conversa.dados
        ):
            faltas[FALTA_CANAL_SEM_ENVIO] += len(pedidos)
            continue
        momentos = [q for _, q in pedidos]
        mensagens = (
            (
                await session.execute(
                    select(m)
                    .where(
                        m.conversa_id == cid,
                        momento >= min(momentos) - replay.MARGEM_ANTES,
                        momento
                        < max(momentos) + replay.MARGEM_DEPOIS + replay.JANELA_RESPOSTA_REAL,
                        m.status != constantes.MSG_FALHOU,
                        m.origem != constantes.ORIGEM_NOTA,
                    )
                    .order_by(momento, m.created_at)
                )
            )
            .scalars()
            .all()
        )
        turnos_ = replay.turnos(list(mensagens))
        for mid, _quando in pedidos:
            turno = turno_do_pedido(turnos_, mid)
            if turno is None:
                faltas[FALTA_NAO_E_DO_CLIENTE] += 1
                continue
            saida_.append((conversa, turno, mid))
    return saida_, faltas


async def montar_dos_gatilhos(
    session: AsyncSession, ids: Sequence[UUID], *, conjunto: str | None = None
) -> tuple[list[dict], dict[str, int]]:
    """Os itens dos ids do `--gatilhos` (na ordem do gatilho) e as faltas. Dois ids do mesmo
    turno do replay viram UM item com os dois em `pedidos`. Só lê."""
    achados, faltas = await turnos_dos_gatilhos(session, ids)
    itens: dict[str, dict] = {}
    for conversa, turno, pedido in achados:
        item = _item(conversa, turno)
        if conjunto and item["conjunto"] != conjunto:
            faltas[FALTA_FORA_DO_CONJUNTO] += 1
            continue
        if item["id"] in itens:
            itens[item["id"]]["pedidos"].append(str(pedido))
            faltas[FALTA_MESMO_TURNO] += 1
            continue
        item["pedidos"] = [str(pedido)]
        itens[item["id"]] = item
    return sorted(itens.values(), key=lambda i: (i["gatilho_em"] or "", i["id"])), dict(faltas)


def gravar_jsonl(caminho: Path, itens: Iterable[Mapping[str, Any]]) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    with caminho.open("w", encoding="utf-8") as f:
        for item in itens:
            f.write(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n")


def ler_jsonl(caminho: Path) -> list[dict]:
    with caminho.open(encoding="utf-8") as f:
        return [json.loads(linha) for linha in f if linha.strip()]


async def canais_da_loja(
    session: AsyncSession, loja: str, plataforma: str | None
) -> list[UUID] | None:
    """O `--loja`: kia → o canal do piloto; todas → None (todos da plataforma); uuid → o
    canal, ou os canais da loja (integração) com esse id."""
    if loja == "kia":
        return [CANAL_KIA]
    if loja == "todas":
        return None
    alvo = UUID(loja)
    c = AtendimentoCanal
    consulta = select(c.id).where(or_(c.id == alvo, c.integration_id == alvo))
    if plataforma:
        consulta = consulta.where(c.plataforma == plataforma)
    ids = list((await session.execute(consulta)).scalars().all())
    if not ids:
        raise SystemExit(f"nenhum canal com o id {loja}")
    return ids


def _resumo(itens: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    return {
        "itens": len(itens),
        "por_estrato": dict(Counter(i["estrato"] for i in itens).most_common()),
        "por_rotulo": dict(Counter(i.get("rotulo") or "?" for i in itens).most_common()),
        "por_conjunto": dict(Counter(i["conjunto"] for i in itens).most_common()),
        "por_fechado": dict(
            Counter(i.get("fechado_por") or "ninguem" for i in itens).most_common()
        ),
    }


def _data(valor: str) -> date:
    try:
        return date.fromisoformat(valor)
    except ValueError:
        raise argparse.ArgumentTypeError(f"data AAAA-MM-DD: {valor!r}") from None


async def principal(argv: list[str] | None = None, *, agora: datetime | None = None) -> int:
    p = argparse.ArgumentParser(description="A amostra do eval do agente (só leitura).")
    p.add_argument("--loja", default="kia", help="kia | todas | <uuid de canal ou loja>")
    p.add_argument("--plataforma", default="shopee")
    p.add_argument("--dias", type=int, default=30)
    p.add_argument("--desde", type=_data, action="append", default=[], help="AAAA-MM-DD (SP)")
    p.add_argument("--ate", type=_data, action="append", default=[], help="AAAA-MM-DD, inclusive")
    p.add_argument("--excluir", type=Path, nargs="+", action="extend", default=[])
    p.add_argument("--gatilhos", type=Path, help="um id de mensagem por linha: só esses turnos")
    p.add_argument("--n", type=int, default=60)
    p.add_argument("--conjunto", choices=("dev", "teste", "todos"), default="todos")
    p.add_argument("--semente", default=SEMENTE)
    p.add_argument("--por-conversa", type=int, default=1)
    p.add_argument("--saida", type=Path, default=SAIDA_PADRAO)
    args = p.parse_args(argv)
    conjunto = None if args.conjunto == "todos" else args.conjunto
    agora = agora or datetime.now(UTC)
    extra: dict[str, Any] = {}
    if args.gatilhos is not None:
        if args.desde or args.ate:
            p.error("--gatilhos não se mistura com --desde/--ate (os ids já dizem os turnos)")
        ids = ler_gatilhos(args.gatilhos)
        async with replay.sessao_replay() as s:
            itens, faltas = await montar_dos_gatilhos(s, ids, conjunto=conjunto)
        extra = {"pedidos": len(ids), "faltas": faltas}
    else:
        try:
            janelas = janelas_sp(args.desde, args.ate, agora=agora) if args.desde else None
        except ValueError as e:
            p.error(str(e))
        if args.ate and not args.desde:
            p.error("--ate sem --desde")
        excluir = gatilhos_usados(args.excluir)
        async with replay.sessao_replay() as s:
            canais = await canais_da_loja(s, args.loja, args.plataforma)
            itens = await montar(
                s,
                canal_ids=canais,
                plataforma=args.plataforma,
                dias=args.dias,
                n=max(1, args.n),
                semente=args.semente,
                conjunto=conjunto,
                por_conversa=args.por_conversa,
                agora=agora,
                janelas=janelas,
                excluir=excluir,
            )
        if janelas:
            extra["janelas"] = [[_iso(a), _iso(b)] for a, b in janelas]
        if args.excluir:
            extra["excluidos_conhecidos"] = len(excluir)
    gravar_jsonl(args.saida, itens)
    print(
        json.dumps(
            {"saida": str(args.saida), **_resumo(itens), **extra}, ensure_ascii=False, indent=2
        )
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(principal(argv))


if __name__ == "__main__":
    sys.exit(main())
