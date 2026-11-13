"""O "BATEU COM A DO BANCO" pelo SENTIDO (13/11/2026).

O Eduardo: "bateu com a do banco, faz sentido, já manda". O agente não copia a resposta da
equipe — escreve com as palavras dele (medido em 13/11: dos 35 rascunhos com o banco, 15
tinham resposta do banco do mesmo tipo e NENHUM bateu letra a letra). Então a conferência é
pelo sentido: o modelo do agente lê a pergunta, o rascunho e a resposta que a equipe deu
antes a uma pergunta do MESMO tipo (no escopo da busca do banco: a mesma loja, o mesmo
modelo) e diz se as duas passam a mesma informação, política e encaminhamento.

Roda só quando vale a pena: régua ligada e o agente lendo o banco (`ctx.regua`,
`ctx.banco_exigido`), rascunho com texto, sem ação de pessoa, fora dos sensíveis e da
cortesia, e com o assunto AINDA fora das listas do canal (o que já sai sozinho não precisa).
O resultado vai para `fatos.agente.banco_confere`; o portão (`ia.bateu_com_o_banco`) lê.
Os textos vão mascarados (`pares.generalizar`); nada de texto no log.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

import pydantic
import structlog

from app.services.atendimento import tipos_pergunta
from app.services.atendimento.agente import pares

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger()

MESMA = "mesma"
DIFERENTE = "diferente"
MAX_CANDIDATAS = 2
MAX_CHARS = 900
MAX_TOKENS = 400
TIMEOUT_S = 30.0

SISTEMA = """\
Você confere se a RESPOSTA PROPOSTA ao cliente diz o mesmo que a RESPOSTA DA EQUIPE. A \
resposta da equipe foi dada antes, a outro cliente da mesma loja, a uma pergunta do mesmo \
tipo; nomes, pedidos, datas e códigos aparecem mascarados ([NOME], [DATA], [CODIGO]…). \
Responda só com o JSON pedido.

- "mesma": a proposta passa a mesma informação, a mesma política e o mesmo encaminhamento \
que a equipe deu. Ignore saudação, tom, tamanho, ordem das frases e os dados de cada pedido \
(data, código, valor do pedido deste cliente).
- "diferente": a proposta afirma algo que a equipe não afirmou, contradiz a equipe, promete \
outra coisa, pede outra ação ao cliente ou responde outra pergunta.

Na dúvida, "diferente"."""


class Veredito(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(extra="forbid")
    veredito: Literal["mesma", "diferente"]
    motivo: str


def esquema() -> dict:
    from anthropic.lib._parse._transform import transform_schema

    return transform_schema(pydantic.TypeAdapter(Veredito).json_schema())


def _corta(t: str | None) -> str:
    t = " ".join(str(t or "").split())
    return t if len(t) <= MAX_CHARS else t[: MAX_CHARS - 1] + "…"


def entrada(pergunta: str | None, proposta: str | None, da_equipe: str | None) -> str:
    """A mensagem do usuário (os textos já mascarados). PURA."""
    return (
        "<dados>\n"
        f"PERGUNTA DO CLIENTE: {_corta(pergunta) or '(sem texto)'}\n"
        f"RESPOSTA PROPOSTA: {_corta(proposta)}\n"
        f"RESPOSTA DA EQUIPE: {_corta(da_equipe)}\n"
        "</dados>"
    )


def argumentos(texto: str, *, modelo: str) -> dict[str, Any]:
    """Os argumentos de `cliente.messages.create`. PURA (sem thinking/temperature)."""
    return {
        "model": modelo,
        "max_tokens": MAX_TOKENS,
        "system": SISTEMA,
        "messages": [{"role": "user", "content": texto}],
        "output_config": {"effort": "low", "format": {"type": "json_schema", "schema": esquema()}},
    }


def veredito_da_mensagem(mensagem: Any) -> Veredito | None:
    for bloco in reversed(list(getattr(mensagem, "content", None) or [])):
        if getattr(bloco, "type", None) != "text" or not getattr(bloco, "text", None):
            continue
        try:
            return Veredito.model_validate_json(bloco.text)
        except pydantic.ValidationError:
            continue
    return None


def candidatas(ctx: Any, categoria: str | None) -> list[tuple[str, str]]:
    """As respostas do banco do MESMO tipo que o pacote mostrou (id, resposta). PURA."""
    from app.services.atendimento.agente import contexto as agente_ctx

    saida = []
    for i, par in (getattr(ctx, "pares_do_pacote", None) or {}).items():
        if (
            i.startswith(agente_ctx.PREFIXO_BANCO)
            and par.get("tipo") == categoria
            and (par.get("resposta") or "").strip()
        ):
            saida.append((i, str(par["resposta"])))
    return saida[:MAX_CANDIDATAS]


def vale_conferir(ctx: Any, categoria: str | None, texto: str | None) -> bool:
    """As condições de custo e de regra para chamar o modelo. PURA."""
    if not (getattr(ctx, "regua", False) and getattr(ctx, "banco_exigido", False)):
        return False
    if not (texto or "").strip() or not categoria:
        return False
    if getattr(ctx, "acoes_pessoa", None):
        return False
    if categoria in tipos_pergunta.SENSIVEIS:
        return False
    return tipos_pergunta.grupo(categoria) != tipos_pergunta.GRUPO_CORTESIA


async def _assunto_ja_sai(session: AsyncSession, ctx: Any, categoria: str) -> bool:
    from app.models import AtendimentoCanal

    if getattr(ctx, "canal_id", None) is None:
        return False
    canal = await session.get(AtendimentoCanal, ctx.canal_id)
    if canal is None:
        return False
    listas = set(canal.auto_categorias or []) | set(canal.auto_orientacao or [])
    return categoria in listas


async def conferir(
    session: AsyncSession,
    ctx: Any,
    *,
    categoria: str | None,
    texto: str | None,
    pergunta: str | None,
    cliente: Any,
    modelo: str,
) -> dict[str, Any]:
    """`{"banco_confere": {...}}` quando conferiu; `{}` quando não valia. Nunca levanta."""
    import anthropic

    from app.services.atendimento.agente import custo

    try:
        if cliente is None or not vale_conferir(ctx, categoria, texto):
            return {}
        cands = candidatas(ctx, categoria)
        if not cands or await _assunto_ja_sai(session, ctx, categoria):
            return {}
        nomes = list(getattr(ctx, "nomes", ()) or ())
        proposta = pares.generalizar(texto or "", nomes)
        pergunta_m = pares.generalizar(pergunta or "", nomes)
        gasto = 0.0
        for i, da_equipe in cands:
            uso = custo.Uso()
            try:
                await custo.conferir_teto(uso=uso)
            except custo.TetoEstourado:
                break
            try:
                mensagem = await cliente.messages.create(
                    **argumentos(entrada(pergunta_m, proposta, da_equipe), modelo=modelo),
                    timeout=anthropic.Timeout(TIMEOUT_S, connect=10.0),
                )
            except anthropic.AnthropicError:
                break
            try:
                gasto += await custo.registrar(
                    mensagem.usage, str(getattr(mensagem, "model", modelo)), uso=uso
                )
            except custo.TetoEstourado:
                gasto += uso.custo_usd
            v = veredito_da_mensagem(mensagem)
            if v is not None and v.veredito == MESMA:
                return {
                    "banco_confere": {
                        "id": i,
                        "veredito": MESMA,
                        "motivo": v.motivo[:160],
                        "custo_usd": round(gasto, 6),
                    }
                }
        return {"banco_confere": {"id": None, "veredito": DIFERENTE, "custo_usd": round(gasto, 6)}}
    except Exception as e:  # noqa: BLE001 — a conferência nunca derruba o rascunho
        logger.warning("atendimento_confere_banco_falhou", err=type(e).__name__)
        return {}
