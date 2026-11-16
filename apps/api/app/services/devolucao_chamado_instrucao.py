# ruff: noqa: E501, S608
"""Chamado aberto pela instrução da IA (Vinicius, 16/11/2026).

"isso o sim aliado à instrução da IA ele analisa e abre chamado." Na janela da
devolução, quando nenhum motivo lançado abre chamado sozinho, a pessoa marca
"Abrir chamado: Sim" e escreve a "Instrução IA" (ex.: "cliente diz que veio sem o
carregador, mas o vídeo mostra o kit completo — contestar"). A cada 10 min este
robô pega esses pedidos e a IA lê o lançamento (condição, motivo, observação de
cada peça), as provas, a observação do pedido e a instrução; escolhe o motivo da
plataforma que a instrução pede e escreve o texto que vai no chamado. O sistema
grava o motivo e o texto nas peças que a instrução cita, abre o chamado e daí é o
caminho de sempre: a conferência olha antes de sair (este lançamento só sai com a
conferência — sem resposta dela, segura) e a contestação vai pela API ou, se a
plataforma não aceita mais, pela fila da tela. A instrução entra no chamado pra
IA de Chamado.

Fica com a pessoa (etiqueta vermelha "IA não abriu", com o porquê): instrução que
não dá pra entender, motivo que não abre chamado ou que pede outra ação na tela
(Bloqueado pede a senha ao comprador — escolha o motivo no lançamento). A mesma
instrução não é relida; escrever uma nova faz a IA olhar de novo.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pydantic
import structlog
from sqlalchemy import text

from app.config import get_settings
from app.services import devolucao_conferencia as conferencia

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger()
SCHEMA = get_settings().database_schema

MARCA = "Chamado pela instrução"
NAO_ABRIU = "a IA não abriu"
TETO_POR_RODADA = 5
MAX_CHARS_TEXTO = 300
MAX_CHARS_POR_QUE = 300
ILEGIVEL = "a IA não conseguiu ler a instrução — escreva de outro jeito"
TIMEOUT_S = 45.0
MAX_TOKENS = 4096
# Motivos que a IA pode escolher (os que abrem chamado; Bloqueado fica de fora: no
# lançamento ele também pede a senha ao comprador — a pessoa escolhe lá).
MOTIVOS = ("Golpe", "Item faltando", "Não recebido", "Danificado (Outros)")

SISTEMA = """Você abre chamados de devolução de uma loja que vende em marketplaces (Mercado \
Livre, Shopee, TikTok Shop, Amazon). A equipe lançou o que voltou (ou não voltou) de cada \
produto e marcou que QUER abrir chamado, mas nenhum motivo lançado abre chamado sozinho. Ela \
deixou uma INSTRUÇÃO dizendo o que quer. Seu trabalho: escolher o motivo da contestação e \
escrever o texto que a plataforma vai ler.

O que cada MOTIVO diz à plataforma:
- Golpe: chegou (ou o comprador alega que recebeu) outro produto, um objeto qualquer ou vazio \
no lugar do que foi enviado; ou o pacote que voltou veio com outra coisa dentro.
- Item faltando: faltam itens que foram enviados (no pacote que voltou, ou o comprador alega \
que faltou).
- Não recebido: o pacote de volta NÃO chegou até a loja (o texto da plataforma diz isso).
- Danificado (Outros): o produto voltou danificado/quebrado (a plataforma pede foto do dano).

Condições: Novo/Usado = voltou e entrou no estoque; Perdimento = voltou sem conserto; \
Extraviado = nada chegou; Não devolvido = o comprador ficou com o produto; Manutenção = foi pro \
técnico; Trocado = voltou outro produto.

Responda:
- motivo: um dos motivos acima, o que a instrução pede e os fatos sustentam; vazio se nenhum serve.
- skus: os SKUs (exatamente como na entrada) dos produtos de que o chamado trata; vazio = todos.
- texto: o que vai pra plataforma, em português do Brasil, educado e firme, primeira pessoa do \
plural, só com fatos da entrada (não invente datas, números nem o que um vídeo mostra além do \
que a entrada diz), sem dados pessoais do comprador e sem link (o sistema põe os links). No \
máximo 300 caracteres. Não escreva o pedido final ("solicitamos…"): o sistema põe.
- precisa_pessoa: true quando a instrução não dá pra entender, pede algo que não é contestar \
(ex.: só um recado interno), contradiz o que foi lançado, ou o caso pede o motivo Bloqueado \
(senha/conta do comprador); senão false.
- por_que: quando precisa_pessoa é true, por que (uma frase curta, pra pessoa da equipe)."""


class Resposta(pydantic.BaseModel):
    motivo: str = ""
    skus: list[str] = pydantic.Field(default_factory=list)
    texto: str = ""
    precisa_pessoa: bool = False
    por_que: str = ""


def esquema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "motivo": {"type": "string", "enum": ["", *MOTIVOS]},
            "skus": {"type": "array", "items": {"type": "string"}},
            "texto": {"type": "string"},
            "precisa_pessoa": {"type": "boolean"},
            "por_que": {"type": "string"},
        },
        "required": ["motivo", "skus", "texto", "precisa_pessoa", "por_que"],
        "additionalProperties": False,
    }


def _limpo(t: str | None, limite: int) -> str:
    return " ".join(str(t or "").split())[:limite]


def _fim(t: str | None, limite: int) -> str:
    t = " ".join(str(t or "").split())
    return ("…" + t[-limite:]) if len(t) > limite else t


def _cortar_frase(t: str, limite: int) -> str:
    t = " ".join(str(t or "").split())
    if len(t) <= limite:
        return t
    corte = t[:limite]
    fim = corte.rfind(". ")
    if fim >= limite // 2:
        return corte[: fim + 1]
    return corte[: corte.rfind(" ")].rstrip(" ,;:") + "."


@dataclass(frozen=True)
class Peca:
    sku: str
    produto: str
    condicao: str
    motivo: str
    observacao: str


def entrada(
    *,
    plataforma: str,
    pecas: list[Peca],
    observacao_pedido: str | None,
    instrucao: str,
    video_envio: bool,
    video_abertura: bool,
    fotos: int,
) -> str:
    """O texto que vai pra IA. PURA."""
    linhas = [f"Plataforma: {plataforma or 'não informada'}", "", "Produtos lançados:"]
    for p in pecas:
        linhas.append(
            f'- SKU "{p.sku or "sem SKU"}" ({p.produto or "sem nome"}): condição "{p.condicao or "—"}"; '
            f'motivo "{p.motivo or "—"}"; observação: "{p.observacao or "—"}"'
        )
    provas = [
        "vídeo da expedição (embalagem): " + ("sim" if video_envio else "não"),
        "vídeo da abertura do pacote que voltou: " + ("sim" if video_abertura else "não"),
        f"fotos: {fotos}",
    ]
    linhas += [
        "",
        "Provas: " + "; ".join(provas),
        f'Observação do pedido (interna): "{_fim(observacao_pedido, 600) or "—"}"',
        "",
        f'INSTRUÇÃO da equipe: "{_limpo(instrucao, 1200)}"',
    ]
    return "\n".join(linhas)


def argumentos(texto: str, *, modelo: str) -> dict[str, Any]:
    return {
        "model": modelo,
        "max_tokens": MAX_TOKENS,
        "system": SISTEMA,
        "messages": [{"role": "user", "content": texto}],
        "output_config": {
            "effort": "medium",
            "format": {"type": "json_schema", "schema": esquema()},
        },
        "betas": ["server-side-fallback-2026-07-01"],
        "fallbacks": "default",
    }


def resposta_da_mensagem(mensagem: Any) -> Resposta | None:
    if getattr(mensagem, "stop_reason", None) not in ("end_turn", "stop_sequence"):
        return None
    for bloco in reversed(list(getattr(mensagem, "content", None) or [])):
        if getattr(bloco, "type", None) != "text" or not getattr(bloco, "text", None):
            continue
        try:
            r = Resposta.model_validate_json(bloco.text)
        except pydantic.ValidationError:
            continue
        if r.motivo not in ("", *MOTIVOS):
            continue
        return r
    return None


async def perguntar(texto: str) -> Resposta | None:
    """Uma requisição à IA. Nunca levanta (sem chave / fora do ar = None)."""
    import anthropic

    from app.services.atendimento import agente_config, ia

    settings = get_settings()
    chave_api = agente_config.chave_do_agente(settings)
    if not chave_api:
        return None
    try:
        async with anthropic.AsyncAnthropic(
            api_key=chave_api, base_url=ia.BASE_CLAUDE, max_retries=2
        ) as cliente:
            mensagem = await cliente.beta.messages.create(
                **argumentos(texto, modelo=settings.devolucao_conferencia_modelo),
                timeout=anthropic.Timeout(TIMEOUT_S, connect=10.0),
            )
    except anthropic.AnthropicError as e:
        logger.warning("devolucao_chamado_instrucao_erro", err=type(e).__name__)
        return None
    # Respondeu mas não deu pra usar (recusa, resposta cortada, JSON ruim): não é
    # "fora do ar" — vai pra pessoa, senão o mesmo pedido travaria a fila.
    return resposta_da_mensagem(mensagem) or Resposta(precisa_pessoa=True, por_que=ILEGIVEL)


@dataclass(frozen=True)
class Decisao:
    motivo: str | None = None
    skus: tuple[str, ...] = ()
    texto: str = ""
    pessoa: str | None = None


def decidir(r: Resposta | None, skus_do_pedido: list[str]) -> Decisao:
    """Junta a resposta com as travas. (Decisao() = a IA não respondeu.) PURA."""
    if r is None:
        return Decisao()
    por_que = _limpo(r.por_que, MAX_CHARS_POR_QUE)
    if r.precisa_pessoa or not r.motivo:
        return Decisao(pessoa=por_que or "a instrução não diz um motivo de contestação")
    texto = _cortar_frase(r.texto, MAX_CHARS_TEXTO)
    if len(texto) < 20:
        return Decisao(pessoa="a IA não escreveu o texto do chamado")
    conhecidos = {s.strip().lower(): s for s in skus_do_pedido if (s or "").strip()}
    citados = [s.strip().lower() for s in r.skus if (s or "").strip()]
    escolhidos = tuple(dict.fromkeys(conhecidos[s] for s in citados if s in conhecidos))
    if citados and not escolhidos:
        # Citou SKU que não está no pedido: não espalha o motivo em tudo.
        return Decisao(pessoa="a IA citou um produto que não está no pedido")
    return Decisao(motivo=r.motivo, skus=escolhidos, texto=texto)


def chave_da_instrucao(em: datetime | None, texto: str) -> str:
    """Identifica ESTA instrução no recado (uma nova faz a IA olhar de novo). PURA."""
    quando = em.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S") if em else "sem data"
    return f"[instrução de {quando} · {len(texto)} caracteres]"


_RE_NAO_ABRIU = re.compile(
    re.escape(MARCA) + r" \([^)]*\): " + re.escape(NAO_ABRIU) + r" — ([^\n]+?) \[instrução de"
)


def ultimo_nao_abriu(observacao: str | None) -> str | None:
    """O porquê do último "a IA não abriu" na Observação do pedido (a etiqueta da
    janela mostra). PURA."""
    achados = _RE_NAO_ABRIU.findall(observacao or "")
    return achados[-1].strip() if achados else None


# ── Rodada ───────────────────────────────────────────────────────────────

# Fora da lista: pedido cujo motivo já abre chamado (o caminho normal cuida) e a
# instrução que a IA já leu e deixou pra pessoa (a chave dela está na Observação).
_CHAVE_SQL = (
    "'[instrução de ' || COALESCE(to_char(r.instrucao_ia_em AT TIME ZONE 'UTC', "
    "'YYYY-MM-DD HH24:MI:SS'), 'sem data') || ' · ' || "
    "length(btrim(r.instrucao_ia_pendente)) || ' caracteres]'"
)
_CANDIDATOS_SQL = f"""
SELECT r.pedido_bling
FROM "{SCHEMA}".devolucao_rastreio r
WHERE r.abrir_chamado IS TRUE
  AND COALESCE(btrim(r.instrucao_ia_pendente), '') <> ''
  AND EXISTS (SELECT 1 FROM "{SCHEMA}".devolutions d WHERE btrim(d.pedido_bling) = r.pedido_bling)
  AND NOT EXISTS (SELECT 1 FROM "{SCHEMA}".devolutions d
                  WHERE btrim(d.pedido_bling) = r.pedido_bling
                    AND lower(btrim(COALESCE(d.motivo_devolucao, ''))) IN :abrem)
  AND NOT EXISTS (SELECT 1 FROM "{SCHEMA}".chamados c
                  WHERE c.pedido_bling = r.pedido_bling AND c.origem = 'devolucao')
  AND position({_CHAVE_SQL} in COALESCE(r.observacao, '')) = 0
  AND (r.resolvido_at IS NULL OR (r.resolvido_at AT TIME ZONE 'America/Sao_Paulo')::date < (
          SELECT max(bo.aguardando_devolucao_data) FROM "{SCHEMA}".bling_orders bo
          WHERE bo.numero = r.pedido_bling))
  {{{{um}}}}
ORDER BY r.instrucao_ia_em NULLS LAST
LIMIT 50
"""


def _sql(um_pedido: bool = False):
    from sqlalchemy import bindparam

    from app.services import chamados as chamados_svc

    q = _CANDIDATOS_SQL.replace("{{um}}", "AND r.pedido_bling = :pedido" if um_pedido else "")
    return text(q).bindparams(bindparam("abrem", expanding=True)), {
        "abrem": sorted(chamados_svc.MOTIVOS_ABREM_CHAMADO)
    }


def _hoje_txt(agora: datetime) -> str:
    from zoneinfo import ZoneInfo

    return agora.astimezone(ZoneInfo("America/Sao_Paulo")).strftime("%d/%m %H:%M")


def _anotar(rast: Any, texto: str) -> None:
    atual = (rast.observacao or "").strip()
    if texto in atual:
        return
    rast.observacao = f"{atual}\n{texto}".strip() if atual else texto


def _assinatura(linhas: list[Any]) -> list[tuple]:
    """O que a IA leu das peças (mudou no meio = não grava). PURA."""
    return sorted(
        (str(d.id), d.sku, d.motivo_devolucao, d.observacao, d.condicao_produto, d.link_envio)
        for d in linhas
    )


async def _linhas(session: AsyncSession, pedido: str) -> list[Any]:
    from sqlalchemy import func, select

    from app.models import Devolution

    return list(
        (
            await session.execute(
                select(Devolution)
                .where(func.btrim(Devolution.pedido_bling) == pedido)
                .order_by(Devolution.created_at)
                .execution_options(populate_existing=True)
            )
        ).scalars()
    )


async def _travas_do_lancamento(session: AsyncSession, alvo: list[Any]) -> str | None:
    """As travas do lançamento normal (routers/devolutions): mala/eletro com motivo
    que abre chamado precisa do Link envio; pedido na Fraude precisa do vídeo.
    Devolve o porquê de não poder (vira recado pra pessoa) ou None."""
    from fastapi import HTTPException

    from app.routers import devolutions as antigo

    for d in alvo:
        try:
            await antigo._exigir_video_fraude(session, d)
            antigo._exigir_link_envio(d)
        except HTTPException as e:
            det = e.detail if isinstance(e.detail, dict) else {}
            return str(det.get("message") or det.get("code") or "trava do lançamento")
    return None


async def um_pedido(session: AsyncSession, pedido: str, *, agora: datetime, pergunta=None) -> str:
    """Trata UM pedido. Devolve o que aconteceu (vai pro resumo)."""
    from app.models import DevolucaoRastreio
    from app.services import chamados_devolucao as cd

    rast = await session.get(DevolucaoRastreio, pedido)
    instrucao = ((rast.instrucao_ia_pendente if rast is not None else None) or "").strip()
    if rast is None or not instrucao or rast.abrir_chamado is not True:
        return "nada"
    chave = chave_da_instrucao(rast.instrucao_ia_em, instrucao)
    if chave in (rast.observacao or ""):
        return "já lida"  # esta instrução já foi lida e ficou pra pessoa
    linhas = await _linhas(session, pedido)
    if not linhas:
        return "sem lançamento"
    lido = _assinatura(linhas)
    pecas = [
        Peca(
            sku=(d.sku or "").strip()[:80],
            produto=_limpo(d.produtos, 120),
            condicao=conferencia.condicao_na_tela(d.condicao_produto),
            motivo=_limpo(d.motivo_devolucao, 80),
            observacao=_limpo(d.observacao, 400),
        )
        for d in linhas
    ]
    anexos = await cd.anexos_de(session, [d.id for d in linhas])
    fotos = len([a for a in anexos if (a.content_type or "").lower() in cd.FOTO_TIPOS_IMAGEM])
    plataforma = await cd._plataforma_da_conta(session, linhas[0].conta) or ""
    texto_ia = entrada(
        plataforma=plataforma,
        pecas=pecas,
        observacao_pedido=rast.observacao,
        instrucao=instrucao,
        video_envio=bool(
            (rast.video_link or "").strip() or any((d.link_envio or "").strip() for d in linhas)
        ),
        video_abertura=any((d.link_abertura or "").strip() for d in linhas),
        fotos=fotos,
    )
    resposta = await (pergunta or perguntar)(texto_ia)
    if resposta is None:
        return "a IA não respondeu"  # fora do ar: tenta na próxima rodada
    decisao = decidir(resposta, [p.sku for p in pecas])
    # A IA levou um tempo: trava a linha do pedido e confere se nada mudou no meio
    # (Não marcado, instrução nova, pedido resolvido, peça editada, chamado aberto).
    await session.refresh(rast, with_for_update=True)
    sql, params = _sql(um_pedido=True)
    ainda = (await session.execute(sql, params | {"pedido": pedido})).first()
    if (
        not ainda
        or rast.abrir_chamado is not True
        or (rast.instrucao_ia_pendente or "").strip() != instrucao
    ):
        await session.rollback()
        return "mudou no meio"
    linhas = await _linhas(session, pedido)
    if _assinatura(linhas) != lido:
        await session.rollback()
        return "mudou no meio"
    if decisao.pessoa:
        _anotar(rast, f"{MARCA} ({_hoje_txt(agora)}): {NAO_ABRIU} — {decisao.pessoa} {chave}")
        await session.commit()
        return f"pessoa: {decisao.pessoa}"
    alvo = [d for d in linhas if not decisao.skus or (d.sku or "").strip() in decisao.skus]
    alvo = alvo or linhas
    antes = {(d.motivo_devolucao or "—"): None for d in alvo}
    for d in alvo:
        d.motivo_devolucao = decisao.motivo
    trava = await _travas_do_lancamento(session, alvo)
    if trava:
        await session.rollback()
        await session.refresh(rast, with_for_update=True)
        _anotar(rast, f"{MARCA} ({_hoje_txt(agora)}): {NAO_ABRIU} — {trava} {chave}")
        await session.commit()
        return f"pessoa: {trava}"
    for d in alvo:
        # O que a pessoa escreveu fica (e vai junto pra plataforma); a IA soma o dela.
        obs = (d.observacao or "").strip()
        d.observacao = (
            decisao.texto if not obs or decisao.texto in obs else f"{obs} {decisao.texto}"
        )
    _anotar(
        rast,
        f"{MARCA} ({_hoje_txt(agora)}): {conferencia.MARCA_IA_INSTRUCAO} — motivo "
        f"{decisao.motivo} em {', '.join(d.sku or '—' for d in alvo)} (antes: {', '.join(antes)}); "
        "a conferência olha antes de sair.",
    )
    await session.flush()
    ch = await cd.garantir_chamado(session, alvo[0])
    if ch is None:
        # Plataforma sem API (Amazon…): o chamado nasce com o aviso de abrir na mão e
        # não tem disparo — grava assim mesmo (é o que o lançamento normal faz).
        existe = await cd.chamados_svc.chamado_da_devolucao(session, alvo[0])
        if existe is None:
            await session.rollback()
            await session.refresh(rast, with_for_update=True)
            _anotar(
                rast, f"{MARCA} ({_hoje_txt(agora)}): {NAO_ABRIU} — o chamado não abriu {chave}"
            )
            await session.commit()
            return "pessoa: o chamado não abriu"
        await session.commit()
        return f"registrado (sem API): {decisao.motivo}"
    await session.commit()
    await cd.agendar_disparo(session, ch, alvo[0])
    logger.info("devolucao_chamado_pela_instrucao", pedido_bling=pedido, motivo=decisao.motivo)
    return f"aberto: {decisao.motivo}"


async def rodada(
    session: AsyncSession, *, agora: datetime | None = None, pergunta=None
) -> dict[str, Any]:
    agora = agora or datetime.now(UTC)
    sql, params = _sql()
    pedidos = (await session.execute(sql, params)).scalars().all()
    resumo: dict[str, Any] = {"candidatos": len(pedidos), "abertos": 0, "pessoa": 0, "pedidos": {}}
    if not pedidos or not conferencia.ligada():
        resumo["ia_ligada"] = conferencia.ligada()
        return resumo
    perguntas = 0
    for pedido in pedidos:
        if perguntas >= TETO_POR_RODADA:
            break
        try:
            feito = await um_pedido(session, pedido, agora=agora, pergunta=pergunta)
        except Exception as e:  # noqa: BLE001 — um pedido ruim não para a rodada
            await session.rollback()
            feito = f"falhou: {type(e).__name__}"
            logger.error(
                "devolucao_chamado_instrucao_falhou", pedido_bling=pedido, err=str(e)[:200]
            )
        if feito not in ("nada", "já lida", "sem lançamento"):
            perguntas += 1  # tudo que chegou na IA conta no teto
        if feito.startswith(("aberto", "registrado")):
            resumo["abertos"] += 1
        elif feito.startswith("pessoa"):
            resumo["pessoa"] += 1
        resumo["pedidos"][pedido] = feito
        if feito == "a IA não respondeu":
            break  # IA fora do ar: não insiste nesta rodada
    return resumo
