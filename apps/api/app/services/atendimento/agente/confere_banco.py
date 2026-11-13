"""O "BATEU COM A DO BANCO" pelo SENTIDO (13/11/2026).

O Eduardo: "bateu com a do banco, faz sentido, já manda". O agente não copia a resposta da
equipe — escreve com as palavras dele (medido em 13/11: dos 35 rascunhos com o banco, 15
tinham resposta do banco do mesmo tipo e NENHUM bateu letra a letra). Então a conferência é
pelo sentido: o modelo do agente lê a pergunta, o rascunho e a resposta que a equipe deu
antes a uma pergunta do MESMO tipo (no escopo da busca do banco: a mesma loja, o mesmo
modelo) e diz se as duas passam a mesma informação, política e encaminhamento.

Roda só quando vale a pena: régua ligada e o agente lendo o banco (`ctx.regua`,
`ctx.banco_exigido`), rascunho com texto e com o assunto AINDA fora das listas do canal (o que
já sai sozinho não precisa). O resultado vai para `fatos.agente.banco_confere`; o portão
(`ia.bateu_com_o_banco`) lê. Os textos vão mascarados (`pares.generalizar`); nada de texto no
log.

A VIRADA (13/11/2026, o Eduardo: "não existirá mais esse 'precisa de pessoa'... se bater e for
parecido, ele responde normal como qualquer outra conversa"; "os sensíveis abaixa pra 85%"):
- os sensíveis e a cortesia também conferem;
- o rascunho que a 1ª camada SEGURA para pessoa confere (`solta_a_pessoa`): bateu, a ação de
  pessoa vira marca de depois do envio (`ctx.banco_bateu`; menos
  `contexto.ACOES_QUE_O_BANCO_NAO_SOLTA`), a 1ª camada roda de novo com a marca e os motivos
  de estado e decisão viram aviso (`checagens.motivo_que_o_banco_solta`); as regras de texto
  e o verificador continuam;
- as candidatas: as respostas do banco que o rascunho CITOU (par:b…), as do mesmo tipo e as
  mais parecidas; o modelo lê também a pergunta que a equipe respondeu ("diferente" se era
  outra).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, Literal

import pydantic
import structlog

from app.services.atendimento.agente import pares

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger()

MESMA = "mesma"
DIFERENTE = "diferente"
# A virada (13/11/2026): UMA chamada com todas as respostas do banco que o pacote mostrou (o
# teste de 242 conversas: com 3 chamadas, uma por candidata na ordem do pacote, só 12 de 124
# bateram — a candidata da vez costumava ser de outra pergunta).
MAX_CANDIDATAS = 12
# As respostas achadas no banco pelo próprio RASCUNHO (o índice cobre a pergunta e a resposta da
# equipe): a resposta parecida que a equipe já deu se acha melhor pela resposta do que pela
# pergunta do cliente (o teste de 242 conversas: as 8 do pacote, pela pergunta, eram muitas
# vezes de outro assunto).
MAX_PELO_RASCUNHO = 6
MAX_CHARS = 900
MAX_CHARS_CASO = 420
MAX_TOKENS = 500
TIMEOUT_S = 30.0

SISTEMA = """\
Você confere se a RESPOSTA PROPOSTA ao cliente segue o que a equipe já respondeu num caso \
parecido. Abaixo vêm CASOS DO BANCO: a pergunta de outro cliente e a resposta que a equipe da \
loja deu; nomes, pedidos, datas e códigos aparecem mascarados ([NOME], [DATA], [CODIGO]…). \
Responda só com o JSON pedido.

- "mesma": um dos casos é o MESMO pedido do cliente (o mesmo assunto e o mesmo tipo de \
pergunta) e a proposta segue o que a equipe fez nele: a mesma informação ou política e o \
mesmo encaminhamento. Pode ter outras palavras, mais cortesia ou o dado do pedido deste \
cliente; não pode afirmar o que a equipe não afirmou, prometer outra coisa nem pedir outra \
ação ao cliente. Diga o id desse caso.
- "diferente": nenhum caso é o mesmo pedido, ou a proposta contradiz, promete ou orienta \
diferente da equipe. O id fica null.

Na dúvida, "diferente"."""


class Veredito(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(extra="forbid")
    id: str | None
    veredito: Literal["mesma", "diferente"]
    motivo: str


def esquema() -> dict:
    from anthropic.lib._parse._transform import transform_schema

    return transform_schema(pydantic.TypeAdapter(Veredito).json_schema())


def _corta(t: str | None, teto: int = MAX_CHARS) -> str:
    t = " ".join(str(t or "").split())
    return t if len(t) <= teto else t[: teto - 1] + "…"


def entrada(
    pergunta: str | None,
    proposta: str | None,
    casos: Sequence[tuple[str, str, str]],
) -> str:
    """A mensagem do usuário (os textos já mascarados). PURA."""
    linhas = [
        "<dados>",
        f"PERGUNTA DO CLIENTE: {_corta(pergunta) or '(sem texto)'}",
        f"RESPOSTA PROPOSTA: {_corta(proposta)}",
        "CASOS DO BANCO:",
    ]
    for i, pergunta_da_equipe, da_equipe in casos:
        linhas.append(
            f"- {i} | cliente: «{_corta(pergunta_da_equipe, MAX_CHARS_CASO) or '(sem texto)'}» "
            f"| equipe: «{_corta(da_equipe, MAX_CHARS_CASO)}»"
        )
    linhas.append("</dados>")
    return "\n".join(linhas)


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


def candidatas(
    ctx: Any, categoria: str | None, evidencias: Sequence[str] = ()
) -> list[tuple[str, str, str]]:
    """As respostas do banco que o pacote mostrou para conferir: (id, pergunta, resposta) —
    primeiro as que o rascunho CITOU, depois as do MESMO tipo, depois as mais parecidas (a
    ordem do pacote); até `MAX_CANDIDATAS`. O tipo do banco nem sempre é o do agente (o
    "defeito" do banco para a "troca_devolucao" dele; muito "indefinido"): a conferência lê as
    duas perguntas e diz "diferente" quando a da equipe era outra. PURA."""
    from app.services.atendimento.agente import contexto as agente_ctx

    do_pacote = getattr(ctx, "pares_do_pacote", None) or {}

    def _serve(i: str) -> bool:
        par = do_pacote.get(i)
        return (
            par is not None
            and i.startswith(agente_ctx.PREFIXO_BANCO)
            and bool((par.get("resposta") or "").strip())
        )

    citadas = [i for i in dict.fromkeys(str(e or "").strip() for e in evidencias) if _serve(i)]
    do_tipo = [i for i, par in do_pacote.items() if _serve(i) and par.get("tipo") == categoria]
    pelo_rascunho = [i for i in getattr(ctx, "banco_pelo_rascunho", None) or [] if _serve(i)]
    outras = [i for i in do_pacote if _serve(i)]
    ids = list(dict.fromkeys([*citadas, *do_tipo, *pelo_rascunho, *outras]))[:MAX_CANDIDATAS]
    return [
        (i, str(do_pacote[i].get("pergunta") or ""), str(do_pacote[i]["resposta"])) for i in ids
    ]


async def _pelo_rascunho(
    session: AsyncSession | None,
    ctx: Any,
    *,
    categoria: str | None,
    texto: str | None,
    pergunta: str | None,
) -> None:
    """Busca no banco as respostas parecidas com o RASCUNHO e as põe entre as candidatas
    (`ctx.banco_pelo_rascunho`, registradas em `pares_do_pacote`: o portão lê o
    `banco_tipos`). As mesmas regras do pacote: da mesma loja (fora da ficha do produto),
    sem dado pessoal nem máscara de dado. Uma vez por execução. Nunca levanta."""
    from app.services.atendimento import banco_respostas
    from app.services.atendimento.agente import contexto as agente_ctx

    if session is None or not (texto or "").strip() or getattr(ctx, "banco_pelo_rascunho", None):
        return
    try:
        achados = await banco_respostas.buscar(
            session,
            pergunta=" ".join(f"{pergunta or ''} {texto}".split())[:600],
            plataforma=ctx.plataforma,
            integration_id=ctx.integration_id,
            item_ids=sorted(getattr(ctx, "anuncios_do_pacote", None) or ()),
            modelos=(),
            tipo=categoria,
            conversa_id=ctx.conversa_id,
            as_of=ctx.agora(),
            limite=MAX_PELO_RASCUNHO * 3,
        )
    except Exception as e:  # noqa: BLE001 — sem a busca, confere só com o pacote
        logger.warning("atendimento_confere_banco_busca_falhou", err=type(e).__name__)
        return
    ja = set(getattr(ctx, "pares_do_pacote", None) or {})
    novos: list[dict] = []
    for a in achados:
        i = agente_ctx.id_par(a["id"])
        if i in ja:
            continue
        if not a["mesma_loja"] and a["tipo"] not in banco_respostas.TIPOS_SO_DO_PRODUTO:
            continue
        if not pares.serve_ao_pacote(a["resposta"]) or pares.tem_mascara_de_dado(a["pergunta"]):
            continue
        novos.append(
            {
                "id": i,
                "pergunta": pares.para_o_pacote(a["pergunta"], pares.MAX_CHARS_PERGUNTA_PACOTE),
                "resposta": pares.para_o_pacote(a["resposta"], pares.MAX_CHARS_RESPOSTA_PACOTE),
                "tipo": a["tipo"],
                "mesma_loja": bool(a["mesma_loja"]),
                "mesmo_anuncio": bool(a["mesmo_anuncio"]),
                "mesmo_produto": bool(a["mesmo_modelo"]),
                "fonte_de_produto": False,
                "com_anuncio": bool(a.get("item_id")),
                "idade_dias": int(a["idade_dias"]),
            }
        )
        if len(novos) >= MAX_PELO_RASCUNHO:
            break
    ctx.registrar_pares(novos)
    ctx.banco_pelo_rascunho = [n["id"] for n in novos]


def _virada(ctx: Any) -> bool:
    return bool(getattr(ctx, "regua", False) and getattr(ctx, "banco_exigido", False))


def vale_conferir(ctx: Any, categoria: str | None, texto: str | None) -> bool:
    """As condições de custo e de regra para chamar o modelo (o rascunho SEM ação de pessoa,
    para soltar o assunto ainda fora das listas). PURA. Desde a virada, os sensíveis (85%) e
    a cortesia também conferem."""
    if not _virada(ctx):
        return False
    if not (texto or "").strip() or not categoria:
        return False
    return not getattr(ctx, "acoes_pessoa", None)


def vale_soltar_a_pessoa(
    ctx: Any, *, texto: str | None, categoria: str | None, acoes: Sequence[str] = ()
) -> bool:
    """O rascunho que a 1ª camada segura pode ser solto pela conferência com o banco
    (`solta_a_pessoa`)? PURA. Só na virada, com texto, e sem ação das que o banco não solta."""
    from app.services.atendimento.agente import contexto as agente_ctx

    if not _virada(ctx) or not (texto or "").strip() or not categoria:
        return False
    return not (set(acoes or ()) & agente_ctx.ACOES_QUE_O_BANCO_NAO_SOLTA)


async def _assunto_ja_sai(session: AsyncSession, ctx: Any, categoria: str) -> bool:
    from app.models import AtendimentoCanal

    if getattr(ctx, "canal_id", None) is None:
        return False
    canal = await session.get(AtendimentoCanal, ctx.canal_id)
    if canal is None:
        return False
    listas = set(canal.auto_categorias or []) | set(canal.auto_orientacao or [])
    return categoria in listas


async def _conferir_candidatas(
    ctx: Any,
    cands: Sequence[tuple[str, str, str]],
    *,
    texto: str | None,
    pergunta: str | None,
    cliente: Any,
    modelo: str,
) -> dict[str, Any]:
    """O registro da conferência (uma chamada com todas as candidatas): {"id", "veredito",
    "motivo", "custo_usd"}. O id que o modelo deu fora das candidatas não vale. Pode levantar
    (quem chama segura)."""
    import anthropic

    from app.services.atendimento.agente import custo

    # O teste (replay) gasta do teto dos testes, nunca do dia do agente ao vivo.
    replay = bool(getattr(ctx, "modo_replay", False))
    nomes = list(getattr(ctx, "nomes", ()) or ())
    proposta = pares.generalizar(texto or "", nomes)
    pergunta_m = pares.generalizar(pergunta or "", nomes)
    uso = custo.Uso()
    nada = {"id": None, "veredito": DIFERENTE, "custo_usd": 0.0}
    try:
        await custo.conferir_teto(uso=uso, replay=replay)
    except custo.TetoEstourado:
        return nada
    try:
        mensagem = await cliente.messages.create(
            **argumentos(entrada(pergunta_m, proposta, cands), modelo=modelo),
            timeout=anthropic.Timeout(TIMEOUT_S, connect=10.0),
        )
    except anthropic.AnthropicError:
        return nada
    try:
        gasto = await custo.registrar(
            mensagem.usage, str(getattr(mensagem, "model", modelo)), uso=uso, replay=replay
        )
    except custo.TetoEstourado:
        gasto = uso.custo_usd
    v = veredito_da_mensagem(mensagem)
    ids = [i for i, _p, _r in cands]
    if v is not None and v.veredito == MESMA:
        escolhido = v.id if v.id in ids else (ids[0] if len(ids) == 1 else None)
        if escolhido:
            return {
                "id": escolhido,
                "veredito": MESMA,
                "motivo": v.motivo[:160],
                "custo_usd": round(gasto, 6),
            }
    return {"id": None, "veredito": DIFERENTE, "custo_usd": round(gasto, 6)}


async def solta_a_pessoa(
    ctx: Any,
    *,
    texto: str | None,
    categoria: str | None,
    acoes: Sequence[str],
    evidencias: Sequence[str],
    pergunta: str | None,
    cliente: Any,
    modelo: str,
    session: AsyncSession | None = None,
) -> bool:
    """A virada: o rascunho que pede PESSOA diz o mesmo que a resposta da equipe no banco?
    Bateu: `ctx.banco_bateu` (o par) e o "responde e marca" (`ctx.responde_e_marca`) — as ações
    de pessoa ficam para depois do envio. Sempre grava o registro em `ctx.banco_confere`
    quando conferiu. Nunca levanta."""
    try:
        if cliente is None or not vale_soltar_a_pessoa(
            ctx, texto=texto, categoria=categoria, acoes=acoes
        ):
            return False
        await _pelo_rascunho(session, ctx, categoria=categoria, texto=texto, pergunta=pergunta)
        cands = candidatas(ctx, categoria, evidencias)
        if not cands:
            return False
        registro = await _conferir_candidatas(
            ctx, cands, texto=texto, pergunta=pergunta, cliente=cliente, modelo=modelo
        )
        registro["pessoa"] = True
        ctx.banco_confere = registro
        if registro.get("veredito") == MESMA and registro.get("id"):
            ctx.banco_bateu = registro["id"]
            ctx.responde_e_marca = True
            return True
        return False
    except Exception as e:  # noqa: BLE001 — a conferência nunca derruba o rascunho
        logger.warning("atendimento_confere_banco_pessoa_falhou", err=type(e).__name__)
        return False


async def conferir(
    session: AsyncSession,
    ctx: Any,
    *,
    categoria: str | None,
    texto: str | None,
    pergunta: str | None,
    cliente: Any,
    modelo: str,
    evidencias: Sequence[str] = (),
) -> dict[str, Any]:
    """`{"banco_confere": {...}}` quando conferiu; `{}` quando não valia. Nunca levanta.
    A conferência de antes (`solta_a_pessoa`) já feita: o registro dela."""
    if getattr(ctx, "banco_confere", None) is not None:
        return {"banco_confere": ctx.banco_confere}
    try:
        if cliente is None or not vale_conferir(ctx, categoria, texto):
            return {}
        if await _assunto_ja_sai(session, ctx, categoria or ""):
            return {}
        await _pelo_rascunho(session, ctx, categoria=categoria, texto=texto, pergunta=pergunta)
        cands = candidatas(ctx, categoria, evidencias)
        if not cands:
            return {}
        registro = await _conferir_candidatas(
            ctx, cands, texto=texto, pergunta=pergunta, cliente=cliente, modelo=modelo
        )
        return {"banco_confere": registro}
    except Exception as e:  # noqa: BLE001 — a conferência nunca derruba o rascunho
        logger.warning("atendimento_confere_banco_falhou", err=type(e).__name__)
        return {}
