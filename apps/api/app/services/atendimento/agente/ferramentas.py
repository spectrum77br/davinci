"""As 6 ferramentas do agente, só de leitura (spec 4 + fontes v1.3, 20/10/2026).

Ordem FIXA (o cache do prefixo depende dela; a ablação do eval só tira):

1. `ver_anuncio(item_id)` — ficha + variações (disponível/esgotado,
   confiável) + `restricao_envio` + especificações por fonte (anúncio >
   DaVinci > site);
2. `ver_pedido(numero)` — OUTRO pedido do mesmo comprador nesta loja (o da
   conversa está no pacote): o mesmo bloco do pacote, com `postado`,
   `etiqueta_padrao_loja` e `origem` provada;
3. `ver_garantia(numero | None)` — o registro do Painel ou o CÁLCULO do
   Painel (entrega → 3 meses de hardware e 12 de software, só Uranyx; a
   Charlots pelo fato da marca; as outras linhas sem prazo próprio);
4. `consultar_manual(assunto, pergunta | None)` — regra do assunto,
   orientação do agente, respostas prontas aprovadas e fatos aprovados;
5. `buscar_respostas_equipe(pergunta, item_id | None)` — pares
   generalizados (só TOM: nunca fato, nunca número);
6. `sugerir_troca(numero | None)` — opções de troca (regra de custo do
   sistema) para o rascunho de pessoa (null = o pedido da conversa).

Regras comuns (spec 4.1):

- `@beta_async_tool(strict=True)` sobre uma função com a assinatura EXATA
  do modelo, sem valor padrão (`str | None` explícito); o esquema leva
  `additionalProperties: false` (exigido pelo `strict`);
- cada consulta na SUA `sessao.sessao_leitura()` (só leitura, 5 s), tudo
  convertido para dicionário lá dentro;
- ESCOPO: pedido só desta conversa ou do mesmo comprador nesta loja
  (`atendimento_pedidos_comprador`, as conversas do comprador na loja, o
  espelho do Bling pelo `numeroloja` dele); anúncio só citado na conversa
  ou da mesma integração em `product_links` (vivo). Fora: "não localizado
  neste atendimento";
- saída `<dados fonte="...">{json compacto, sort_keys, ensure_ascii=False}
  </dados>` pelo `ctx.cortar_resultado`; texto livre pelo `ia.mascarar`
  (identificador não: um item da Shopee tem 11 dígitos); o pedido da
  conversa sai como `{numero_pedido}`; status da transportadora sem cidade;
  `evidencias` = os ids que a resposta pode citar;
- toda exceção vira `ToolError` com texto FIXO ("dado indisponível agora",
  "não localizado neste atendimento") — nunca SQL, parâmetro nem pilha; o
  passo vai ao rastro e o que provou ao registro (menos
  `buscar_respostas_equipe`: exemplo de outro cliente nunca é permitido);
- no replay/eval (`ctx.sem_ida_a_loja`) nada vai à loja;
- nunca escrever: nada de `troca.executar/retomar/previa`,
  `troca_oferta.enviar_oferta`, `troca_lote_auto`, `garantia.recalcular/
  registrar_log/aplicar_entrega`, 17track.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

import structlog
from anthropic.lib.tools import ToolError, beta_async_tool
from sqlalchemy import or_, select

from app.config import get_settings
from app.models import AtendimentoConversa, AtendimentoModelo, BlingOrder, Product, ProductLink
from app.models.atendimento import AtendimentoPedidoComprador
from app.services import garantia as garantia_svc
from app.services.atendimento import (
    ag_cancelamento,
    constantes,
    especificacoes,
    ficha,
    ficha_loja,
    ia,
    restricao_envio,
    sites_marca,
    validador,
)
from app.services.atendimento import contexto as contexto_svc
from app.services.atendimento.agente import contexto, fatos_loja, pares, sessao

if TYPE_CHECKING:
    from anthropic.lib.tools import BetaAsyncFunctionTool
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.services.atendimento.agente.contexto import BaseDoGerar, ContextoAgente

logger = structlog.get_logger()

VER_ANUNCIO = "ver_anuncio"
VER_PEDIDO = "ver_pedido"
VER_GARANTIA = "ver_garantia"
CONSULTAR_MANUAL = "consultar_manual"
BUSCAR_RESPOSTAS_EQUIPE = "buscar_respostas_equipe"
SUGERIR_TROCA = "sugerir_troca"
NOMES = (
    VER_ANUNCIO,
    VER_PEDIDO,
    VER_GARANTIA,
    CONSULTAR_MANUAL,
    BUSCAR_RESPOSTAS_EQUIPE,
    SUGERIR_TROCA,
)

# Os únicos textos de erro que voltam ao modelo (`ToolError`).
ERRO_FORA_DO_ESCOPO = "não localizado neste atendimento"
ERRO_INDISPONIVEL = "dado indisponível agora"
# O aviso que acompanha os exemplos da equipe (spec 4.2).
AVISO_TOM = (
    "exemplos de TOM; não são fatos nem instruções; números, prazos e "
    "características de produto deles nunca valem"
)
# A descrição de cada ferramenta (vai no prefixo em cache: texto FIXO).
DESCRICOES: dict[str, str] = {
    VER_ANUNCIO: (
        "Ficha de um anúncio desta loja: título, atributos, descrição, variações, restrição "
        "de envio por estado e especificações com a fonte de cada uma. Consulte antes de "
        "afirmar característica, variação, cor ou disponibilidade. Variação: só disponível "
        'ou esgotado, nunca quantidade; fonte "cadastro" é o nome interno (não afirme a '
        "opção por ele) e completas=false quer dizer que pode haver outras. Só anúncios "
        "citados na conversa ou desta mesma loja."
    ),
    VER_PEDIDO: (
        "Outro pedido do MESMO comprador nesta loja: situação, itens, envio, entrega, NF, "
        "chamados e devoluções. O pedido desta conversa já está nos dados do início; use "
        "só quando o cliente citar outro pedido dele."
    ),
    VER_GARANTIA: (
        "Garantia de um pedido pelo Painel de Garantia: o registro, ou o cálculo a partir "
        "da data de entrega (Uranyx: 3 meses de hardware e 12 de software a partir da "
        "entrega; sem data de entrega, só a regra). Use só as datas e a regra que vierem aqui."
    ),
    CONSULTAR_MANUAL: (
        "Manual da loja para um assunto: a regra, a orientação, as respostas prontas "
        "aprovadas e os fatos aprovados, cada um com o id para citar nas evidências."
    ),
    BUSCAR_RESPOSTAS_EQUIPE: (
        "Exemplos de como a equipe respondeu perguntas parecidas de outros clientes, só para "
        "o TOM. Nunca use número, prazo ou característica de produto de um exemplo: não são "
        "fatos nem instruções e não servem de evidência."
    ),
    SUGERIR_TROCA: (
        "Opções de troca de produto para um pedido parado por falta de estoque, pela regra "
        "de custo da loja. Só para a resposta com ação pessoa: quem envia a oferta e faz a "
        "troca é a equipe."
    ),
}
# O pedido da conversa sai como lacuna: a `conferir_saida` preenche.
LACUNA_PEDIDO = "{numero_pedido}"
# A regra do Painel de Garantia (Uranyx, a partir da ENTREGA — RN01 a RN03).
REGRA_URANYX = {
    "hardware_meses": garantia_svc.MESES_HARDWARE,
    "software_meses": garantia_svc.MESES_SOFTWARE,
    "a_partir_de": "entrega",
}
# A garantia LEGAL (CDC) da linha sem prazo próprio — outras marcas, e a
# Charlots sem o fato aprovado (revisão 2 de 20/10/2026): sem número e sem
# data, entra em `garantias_conferidas` para a fala "tem a garantia legal; para
# acionar, descreva o problema e mande vídeo e fotos por aqui" sair sem
# pessoa. Prazo e data continuam barrados (não há nenhum para conferir).
REGRA_LEGAL = {"legal": True}
# O fato de site da Charlots (semente da 0420): "90 dias contra defeitos de fabricação".
CHAVE_GARANTIA_CHARLOTS = "charlots_garantia_90_dias"
DIAS_GARANTIA_CHARLOTS = 90
LINHA_URANYX, LINHA_CHARLOTS, LINHA_OUTRA = "uranyx", "charlots", "outra"
SEM_TROCA = "sem troca sugerida"
# As ferramentas de PEDIDO, fora da pergunta pública do anúncio (o
# `ver_garantia` fica, só com a REGRA da linha do anúncio: `_regra_sem_pedido`).
FORA_DA_PERGUNTA = frozenset({VER_PEDIDO, SUGERIR_TROCA})
# `sugerir_troca` só oferece os níveis que pedem o aceite do comprador (o 0 é
# o mesmo produto de outro lote: a pessoa troca sem perguntar).
NIVEIS_TROCA = (1, 2)
MAX_RESPOSTAS_PRONTAS = 5
MAX_CHARS_TEXTO = 600
MAX_CHARS_PRODUTO = 60
MAX_OPCOES_TROCA = 6
# O que se aceita como número de pedido e id de anúncio (o resto nem vai ao banco).
_RX_NUMERO = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{2,63}")
_RX_ITEM = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")
# Chaves de IDENTIFICADOR: não passam pelo `mascarar` (11 dígitos de um item
# da Shopee viraria "[NUMERO_PESSOAL]").
_SEM_MASCARA = frozenset(
    {
        "item_id",
        "pedido",
        "numero",
        "evidencias",
        "id",
        "rastreio",
        "nf",
        "data_envio",
        "previsao_entrega",
        "data_da_compra",
        "inicio",
        "fim_hardware",
        "fim_software",
    }
)
# Dado pessoal, de dinheiro e de identidade interna que nunca sai de uma
# ferramenta (defesa: o bloco do pedido do pacote já não os tem). "valor"
# não entra: é o valor de uma especificação ("NFC": "sim").
_CHAVES_PROIBIDAS = frozenset(
    {
        "cpf",
        "documento",
        "documento_destinatario",
        "nome_destinatario",
        "cliente_nome",
        "endereco",
        "endereco_destino",
        "telefone",
        "email",
        "cep",
        "cidade",
        "valor_total",
        "total",
        "preco",
        "custo",
        "dif_custo_pct",
        "sku",
        "skus",
        "variation_id",
        "produtos_vinculados",
    }
)
# As chaves de id interno: fora do texto de números permitidos (`_sem_ids`).
_IDS_INTERNOS = frozenset({"evidencias", "id", "fato"})
# A localização da transportadora sem a cidade: "Entregue → São Paulo/SP",
# "Em trânsito para Curitiba/PR".
_CIDADE_NO_FIM = re.compile(
    r"(?:\s*(?:\b(?:em|de|para|por)\b|[-–,])\s*)?(?:[A-ZÀ-Ý][\wÀ-ÿ'.]*\s?){1,5}/[A-Z]{2}\s*$"
)


class ForaDoEscopo(Exception):  # noqa: N818 — é a resposta "não localizado", não bug
    """O número/anúncio não é desta conversa nem deste comprador nesta loja."""


@dataclass(frozen=True)
class Resultado:
    """O que uma ferramenta achou, antes do embrulho (`executar`).

    `dados` = o JSON que o modelo vê; `ids` = as evidências que ele prova
    (vão para o registro e o rastro); `permitir_texto` = o JSON entra nos
    números/nomes permitidos (False só em `buscar_respostas_equipe`);
    `garantias` = só `ver_garantia`: o que vai para
    `ctx.registrar_garantias`; `ids_rastro` = ids que só vão ao rastro
    (os `par:<id>` dos exemplos).
    """

    dados: dict
    ids: tuple[str, ...] = ()
    permitir_texto: bool = True
    garantias: tuple[dict, ...] = ()
    ids_rastro: tuple[str, ...] = ()


# ── Formato ───────────────────────────────────────────────────────────────


def _json(dados: Any) -> str:
    texto = json.dumps(
        dados, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=_serializar
    )
    # Texto de anúncio/cliente não fecha a moldura nem abre outra.
    return re.sub(r"<(/?)(dados)", r"\\u003c\1\2", texto, flags=re.IGNORECASE)


def _serializar(valor: Any) -> Any:
    if isinstance(valor, datetime | date):
        return valor.isoformat()
    if isinstance(valor, UUID):
        return str(valor)
    return str(valor)


def formatar(nome: str, dados: Mapping[str, Any]) -> str:
    """`<dados fonte="nome">{json}</dados>`. PURA.

    JSON compacto, `sort_keys=True`, `ensure_ascii=False` (o cache e a
    leitura); "<dados"/"</dados" dentro de um texto vira `\\u003c`.
    """
    return f'<dados fonte="{nome}">{_json(dict(dados))}</dados>'


def _mascarar(valor: Any, nomes: tuple[str | None, ...], chave: str | None = None) -> Any:
    """Texto livre pelo `ia.mascarar`; identificador e dado proibido, não. PURA."""
    if isinstance(valor, Mapping):
        return {
            str(k): _mascarar(v, nomes, str(k))
            for k, v in valor.items()
            if str(k) not in _CHAVES_PROIBIDAS
        }
    if isinstance(valor, list | tuple):
        return [_mascarar(v, nomes, chave) for v in valor]
    if isinstance(valor, str) and chave not in _SEM_MASCARA:
        return ia.mascarar(valor, nomes)
    return valor


def _sem_ids(valor: Any) -> Any:
    """O dado sem os ids internos (fato, modelo, garantia, evidências). PURA.

    É o texto que vira "número permitido": o "17" de `fato:17` e os dígitos
    de um UUID de resposta pronta não podem liberar "17 dias" ou "4 dias"
    na resposta.
    """
    if isinstance(valor, Mapping):
        return {k: _sem_ids(v) for k, v in valor.items() if k not in _IDS_INTERNOS}
    if isinstance(valor, list | tuple):
        return [_sem_ids(v) for v in valor]
    return valor


def sem_cidade(status: str | None) -> str | None:
    """O status da transportadora sem a cidade ("Entregue → São Paulo/SP" → "Entregue"). PURA."""
    if not status:
        return status
    texto = str(status).split("→")[0].strip(" -–")
    texto = _CIDADE_NO_FIM.sub("", texto).strip(" -–,")
    return texto or None


def _cortar(texto: Any, teto: int = MAX_CHARS_TEXTO) -> str | None:
    t = " ".join(str(texto or "").split())
    if not t:
        return None
    return t if len(t) <= teto else t[: teto - 1].rstrip() + "…"


# O JSON de um resultado cabe neste teto ANTES do corte do `ctx` (que corta o
# texto no meio e quebra o JSON): a moldura, as evidências e a máscara.
TETO_JSON = contexto.MAX_CHARS_RESULTADO - 400


def caber(
    dados: dict, *, listas: Sequence[str] = (), textos: Sequence[str] = (), teto: int = TETO_JSON
) -> dict:
    """Encolhe o resultado até o JSON caber no `teto`: tira do FIM das `listas`
    (a ordem delas é a de importância), depois encurta os `textos`. PURA (cópia)."""
    saida = dict(dados)
    for chave in listas:
        if isinstance(saida.get(chave), list):
            saida[chave] = list(saida[chave])
    while len(_json(saida)) > teto:
        cheia = next((c for c in reversed(listas) if saida.get(c)), None)
        if cheia is None:
            break
        saida[cheia].pop()
    for chave in textos:
        excesso = len(_json(saida)) - teto
        texto = saida.get(chave)
        if excesso <= 0:
            break
        if isinstance(texto, str) and texto:
            saida[chave] = _cortar(texto, max(40, len(texto) - excesso - 2)) or None
    return saida


# ── O embrulho comum ──────────────────────────────────────────────────────


async def executar(
    ctx: ContextoAgente,
    nome: str,
    args: Mapping[str, Any],
    consulta: Callable[[], Awaitable[Resultado]],
) -> str:
    """O embrulho comum: mede, roda `consulta`, registra e formata. Nunca deixa exceção escapar.

    `ForaDoEscopo` → `ToolError(ERRO_FORA_DO_ESCOPO)`; qualquer outra →
    `ToolError(ERRO_INDISPONIVEL)` (log só com o tipo do erro). Sucesso:
    `ctx.permitir(ids=..., textos=[json] se permitir_texto)`,
    `ctx.registrar_garantias(...)` (sempre no `ver_garantia`: consultou,
    mesmo sem achar), `ctx.cortar_resultado(formatar(...))`,
    `ctx.registrar_passo(nome, args, ms, ids=ids+ids_rastro, cortado=...)`.
    O erro também vai ao rastro (`erro=`).
    """
    t0 = time.monotonic()

    def _ms() -> float:
        return (time.monotonic() - t0) * 1000

    try:
        r = await consulta()
        dados = _mascarar(r.dados, ctx.nomes)
        if r.ids and not dados.get("evidencias"):
            dados["evidencias"] = sorted(set(r.ids))
        bruto = formatar(nome, dados)
        texto = ctx.cortar_resultado(bruto)
        if texto != contexto.SEM_ORCAMENTO:
            ctx.permitir(ids=r.ids, textos=[_json(_sem_ids(dados))] if r.permitir_texto else [])
            if nome == VER_GARANTIA or r.garantias:
                ctx.registrar_garantias(r.garantias)
        ctx.registrar_passo(nome, args, _ms(), ids=[*r.ids, *r.ids_rastro], cortado=texto != bruto)
        return texto
    except ForaDoEscopo:
        ctx.registrar_passo(nome, args, _ms(), erro=ERRO_FORA_DO_ESCOPO)
        raise ToolError(ERRO_FORA_DO_ESCOPO) from None
    except Exception as exc:  # noqa: BLE001 — o modelo só vê o texto fixo
        logger.warning(
            "atendimento_agente_ferramenta_falhou",
            ferramenta=nome,
            conversa_id=str(ctx.conversa_id),
            err=type(exc).__name__,
        )
        ctx.registrar_passo(nome, args, _ms(), erro=ERRO_INDISPONIVEL)
        raise ToolError(ERRO_INDISPONIVEL) from None


# ── Escopo ────────────────────────────────────────────────────────────────


def _norm(numero: Any) -> str:
    return " ".join(str(numero or "").split()).lstrip("#").strip().lower()


def _numero_valido(numero: Any) -> str:
    """O número como o modelo mandou, limpo — ou `ForaDoEscopo` (nem vai ao banco)."""
    bruto = "".join(str(numero or "").split()).lstrip("#")
    if not _RX_NUMERO.fullmatch(bruto):
        raise ForaDoEscopo
    return bruto


def _item_valido(item_id: Any) -> str:
    bruto = " ".join(str(item_id or "").split())
    if not _RX_ITEM.fullmatch(bruto):
        raise ForaDoEscopo
    return bruto


@dataclass(frozen=True)
class Alvo:
    """O pedido que a ferramenta vai ler, já no escopo.

    `rotulo` = como ele aparece ao modelo (`{numero_pedido}` no da conversa;
    o número nos outros); `numero_bling`/`numeroloja` = as chaves do espelho
    (None quando o espelho não tem).
    """

    rotulo: str
    numero_bling: str | None
    numeroloja: str | None
    da_conversa: bool


def alvo_da_conversa(conversa: Any, base: BaseDoGerar) -> Alvo | None:
    """O pedido da CONVERSA (o do pacote), ou None sem pedido. PURA."""
    pedido = base.ctx.get("pedido") if isinstance(base.ctx, Mapping) else None
    pedido = pedido if isinstance(pedido, Mapping) else {}
    chaves = (
        contexto_svc._chaves_do_pedido(conversa)
        if conversa is not None and (getattr(conversa, "pedido_marketplace", None) or "").strip()
        else []
    )
    numero_bling = str(pedido.get("numero") or "").strip() or None
    numeroloja = str(pedido.get("numeroloja") or "").strip() or (chaves[0] if chaves else None)
    if numero_bling is None and numeroloja is None:
        return None
    return Alvo(LACUNA_PEDIDO, numero_bling, numeroloja, True)


def _da_conversa(numero: str, conversa: Any, base: BaseDoGerar) -> bool:
    alvo = alvo_da_conversa(conversa, base)
    if alvo is None:
        return False
    chaves = (
        contexto_svc._chaves_do_pedido(conversa)
        if (getattr(conversa, "pedido_marketplace", None) or "").strip()
        else []
    )
    conhecidas = {_norm(x) for x in (*chaves, alvo.numero_bling, alvo.numeroloja) if x}
    return _norm(numero) in conhecidas


async def _pedidos_do_comprador(
    s: AsyncSession, integration_id: UUID | None, comprador_id: str | None
) -> set[str]:
    """Os pedidos do mesmo comprador NESTA loja que o banco conhece (normalizados)."""
    comprador = (comprador_id or "").strip()
    if integration_id is None or not comprador:
        return set()
    t = AtendimentoPedidoComprador
    indice = (
        await s.scalars(
            select(t.pedido).where(t.integration_id == integration_id, t.comprador_id == comprador)
        )
    ).all()
    c = AtendimentoConversa
    das_conversas = (
        await s.scalars(
            select(c.pedido_marketplace).where(
                c.integration_id == integration_id,
                c.comprador_id == comprador,
                c.pedido_marketplace.is_not(None),
            )
        )
    ).all()
    return {_norm(p) for p in (*indice, *das_conversas) if p}


async def pedido_no_escopo(
    s: AsyncSession, ctx: ContextoAgente, base: BaseDoGerar, conversa: Any, numero: Any
) -> Alvo:
    """O pedido `numero` se for desta conversa ou do mesmo comprador nesta loja — ou `ForaDoEscopo`.

    Conferência (spec 4.1): o pedido da conversa (`_chaves_do_pedido` e o nº
    do Bling achado); o que o pacote pôs no escopo; o índice
    `atendimento_pedidos_comprador` e as conversas do MESMO comprador na
    MESMA integração; o nº curto do Bling só pelo `numeroloja` dele estar
    nessa lista (o nº do Bling é sequencial: um dígito trocado é a compra de
    outra pessoa). Achou: `ctx.aceitar_pedido` das chaves.
    """
    if numero is None or " ".join(str(numero).split()) in (LACUNA_PEDIDO, LACUNA_PEDIDO[1:-1]):
        # A lacuna (o modelo só vê `{numero_pedido}` no pacote) ou null = o
        # pedido DESTA conversa (revisão de 20/10/2026).
        if alvo := alvo_da_conversa(conversa, base):
            return alvo
        raise ForaDoEscopo
    bruto = _numero_valido(numero)
    if _da_conversa(bruto, conversa, base) and (alvo := alvo_da_conversa(conversa, base)):
        return alvo
    linhas = (
        await s.execute(
            select(BlingOrder.numero, BlingOrder.numeroloja)
            .where(
                or_(
                    BlingOrder.numero == bruto,
                    BlingOrder.numeroloja.in_(sorted({bruto, bruto.upper(), bruto.lower()})),
                ),
                BlingOrder.numero.is_not(None),
            )
            .order_by(BlingOrder.data.desc().nulls_last())
            .limit(20)
        )
    ).all()
    alvo_n = _norm(bruto)
    do_comprador = await _pedidos_do_comprador(s, ctx.integration_id, ctx.comprador_id)
    escolhida = None
    if ctx.pedido_no_escopo(bruto) or alvo_n in do_comprador:
        escolhida = next(
            (
                linha
                for linha in linhas
                if _norm(linha.numeroloja) == alvo_n or _norm(linha.numero) == alvo_n
            ),
            None,
        )
        aceito = True
    else:
        escolhida = next(
            (
                linha
                for linha in linhas
                if _norm(linha.numero) == alvo_n and _norm(linha.numeroloja) in do_comprador
            ),
            None,
        )
        aceito = escolhida is not None
    if not aceito:
        raise ForaDoEscopo
    numero_bling = str(escolhida.numero) if escolhida is not None else None
    numeroloja = (
        str(escolhida.numeroloja)
        if escolhida is not None and escolhida.numeroloja
        else (bruto if alvo_n != _norm(numero_bling) else None)
    )
    ctx.aceitar_pedido(bruto, numero_bling, numeroloja)
    return Alvo(bruto, numero_bling, numeroloja, False)


async def _anuncio_da_loja(
    s: AsyncSession, plataforma: Any, integration_id: UUID | None, item_id: str
) -> bool:
    if integration_id is None:
        return False
    achado = await s.scalar(
        select(ProductLink.id)
        .where(
            ProductLink.platform == plataforma,
            ProductLink.integration_id == integration_id,
            ProductLink.external_id == item_id,
            ProductLink.morto_desde.is_(None),
        )
        .limit(1)
    )
    return achado is not None


def _conversa_do_pedido(ctx: ContextoAgente, alvo: Alvo) -> SimpleNamespace:
    """Uma "conversa" só com o pedido `alvo`, para o `contexto` e a garantia lerem.

    Id NOVO: as reclamações e avaliações ligadas à conversa de verdade não
    entram no bloco de OUTRO pedido (só as do pedido, pelas chaves).
    """
    canal = constantes.CANAL_CHAT if ctx.canal == constantes.CANAL_PERGUNTA else ctx.canal
    return SimpleNamespace(
        id=uuid4(),
        plataforma=ctx.plataforma,
        canal=canal,
        pedido_marketplace=alvo.numeroloja or alvo.numero_bling or alvo.rotulo,
        dados={},
        comprador_id=ctx.comprador_id,
        comprador_nome=None,
        integration_id=ctx.integration_id,
        canal_id=ctx.canal_id,
        anuncio_id=None,
        anuncio_titulo=None,
        externo_id="",
        conta=None,
    )


# ── 1. ver_anuncio ────────────────────────────────────────────────────────

_CHAVES_VARIACAO = ("nome", "disponivel", "confiavel", "fonte", "selecionada")


def _variacoes(f: Mapping[str, Any] | None) -> dict | None:
    if not f or not isinstance(f.get("variacoes"), list) or not f["variacoes"]:
        return None
    lista = [
        {k: v[k] for k in _CHAVES_VARIACAO if k in v}
        for v in f["variacoes"]
        if isinstance(v, Mapping) and v.get("nome")
    ]
    if not lista:
        return None
    return {
        "variacoes": lista,
        "completas": f.get("variacoes_completas") is True,
        "omitidas": int(f.get("variacoes_omitidas") or 0),
    }


async def dados_ver_anuncio(
    ctx: ContextoAgente,
    item_id: str,
    *,
    base: BaseDoGerar | None = None,
    conversa: Any = None,
    loja: ficha_loja.Busca | None = None,
) -> Resultado:
    """O anúncio (formato em `interfaces.md`). Escopo: citado ou da mesma integração.

    `loja` = a `ficha_loja.Busca` da EXECUÇÃO (uma por sugestão, com as
    cotas dela); None (ou `ctx.sem_ida_a_loja`) = nada vai à loja.
    """
    item = _item_valido(item_id)
    plat = ficha.plataforma_do_catalogo(ctx.plataforma)
    if plat is None:
        raise ForaDoEscopo
    citados = (
        ficha.anuncios_da_conversa(conversa, base.recentes)
        if conversa is not None and base is not None
        else []
    )
    citado = next((a for a in citados if a.item_id == item), None)
    if ctx.sem_ida_a_loja:
        loja = None
    async with sessao.sessao_leitura() as s:
        # O cartão do anúncio de OUTRO vendedor que o cliente mandou ("vocês têm
        # este?") não é fonte (revisão 2 de 20/10/2026: o título "5G NFC IP68"
        # dele liberava "Sim, tem NFC"): só o anúncio que o escopo já aceitou
        # (o da conversa, os itens do pedido, as fichas — o pacote só aceita o
        # cartão desta loja) ou um vínculo vivo desta integração.
        if not ctx.anuncio_citado(item) and not await _anuncio_da_loja(
            s, plat, ctx.integration_id, item
        ):
            raise ForaDoEscopo
        ctx.aceitar_anuncio(item)
        titulo_atual = citado.titulo if citado is not None else None
        f = await ficha.ficha_do_anuncio(
            s,
            plat,
            item,
            ctx.integration_id,
            titulo=titulo_atual,
            variation_id=citado.variation_id if citado is not None else None,
            loja=loja,
            com_variacoes=True,
            variacoes_na_loja=loja is not None,
        )
        titulo = (f or {}).get("titulo") or titulo_atual
        restricao = await restricao_envio.restricao_do_anuncio(
            s, plat, ctx.integration_id, item, titulo
        )
        produtos = await especificacoes.produtos_do_anuncio(s, plat, ctx.integration_id, item)
        espec = await especificacoes.especificacoes_do_produto(s, f, produtos, titulo)
    dados: dict[str, Any] = {"item_id": item, "titulo": titulo}
    ids = []
    if f or titulo or (espec.get("campos") if isinstance(espec, Mapping) else None):
        # A trava da ficha no automático vale para o que se leu aqui também —
        # a ficha, o título ou as especificações (revisão 2 de 20/10/2026) —, e
        # as variações devolvidas entram na conferência de estoque e cor.
        ctx.fichas_lidas.append(
            {
                "item_id": item,
                "titulo": titulo,
                "variacoes": [
                    dict(v) for v in (f or {}).get("variacoes") or [] if isinstance(v, Mapping)
                ],
                "variacoes_completas": (f or {}).get("variacoes_completas") is True,
            }
        )
    if f:
        dados["atributos"] = list(f.get("atributos") or [])
        dados["descricao"] = f.get("descricao")
        dados["fonte"] = f.get("fonte")
        if f.get("titulo_do_outro_anuncio"):
            dados["titulo_do_outro_anuncio"] = f["titulo_do_outro_anuncio"]
        ids.append(contexto.id_ficha(item))
    else:
        dados["sem_ficha"] = True
    dados["variacoes"] = _variacoes(f)
    if dados["variacoes"]:
        ids.append(contexto.id_variacoes(item))
    dados["restricao_envio"] = (
        {k: restricao.get(k) for k in ("ufs", "fonte", "trecho", "ufs_conforme_pedido")}
        if restricao
        else None
    )
    if restricao:
        ids.append(contexto.id_restricao(item))
    dados["especificacoes"] = {
        "campos": list(espec.get("campos") or []),
        "conflitos": list(espec.get("conflitos") or []),
        "conflito_no_anuncio": bool(espec.get("conflito_no_anuncio")),
    }
    if dados["especificacoes"]["campos"] or dados["especificacoes"]["conflitos"]:
        ids.append(contexto.id_especificacoes(item))
    return Resultado(
        dados=caber(dados, listas=("atributos",), textos=("descricao",)), ids=tuple(ids)
    )


# ── 2. ver_pedido ─────────────────────────────────────────────────────────


def _bloco_limpo(bloco: Mapping[str, Any]) -> dict:
    saida = dict(bloco)
    if isinstance(saida.get("ultimo_status"), str):
        saida["ultimo_status"] = sem_cidade(saida["ultimo_status"])
    return saida


async def dados_ver_pedido(
    ctx: ContextoAgente, base: BaseDoGerar, numero: str, *, conversa: Any = None
) -> Resultado:
    """Outro pedido do comprador nesta loja (o bloco de `pacote.bloco_do_pedido`)."""
    from app.services.atendimento.agente import pacote

    async with sessao.sessao_leitura() as s:
        alvo = await pedido_no_escopo(s, ctx, base, conversa, numero)
        if alvo.da_conversa:
            return Resultado(
                dados={
                    "mesmo_pedido_da_conversa": True,
                    "pedido": LACUNA_PEDIDO,
                    "aviso": "é o pedido desta conversa: os dados já estão no início",
                },
                ids=(contexto.ID_PEDIDO,),
            )
        ctx_pedido = await contexto_svc.contexto_da_conversa(s, _conversa_do_pedido(ctx, alvo))
        if not ctx_pedido.get("pedido"):
            raise ForaDoEscopo
        bloco = await pacote.bloco_do_pedido(
            s,
            ctx_pedido,
            plataforma=ctx.plataforma,
            integration_id=ctx.integration_id,
            conversa_dados={},
            lacunas=False,
            agora=ctx.agora(),
        )
        if not bloco:
            raise ForaDoEscopo
        ctx.aceitar_pedido(
            ctx_pedido["pedido"].get("numero"), ctx_pedido["pedido"].get("numeroloja")
        )
        # A régua de estado roda neste pedido também, depois do laço
        # (`acao_pessoa.gatilhos_de_outro_pedido`; revisão de 20/10/2026).
        ctx.registrar_pedido_lido(ctx_pedido, bloco.get("postado"))
    return Resultado(dados=_bloco_limpo(bloco), ids=(contexto.id_pedido(alvo.rotulo),))


async def _registrar_outro_pedido(s: AsyncSession, ctx: ContextoAgente, alvo: Alvo) -> None:
    """O contexto e o `postado` de OUTRO pedido (não o da conversa) no `ctx` (a
    régua de estado depois do laço). Só lê; sem o pedido no contexto, nada."""
    if alvo.da_conversa:
        return
    from app.services.atendimento.agente import acao_pessoa

    ctx_pedido = await contexto_svc.contexto_da_conversa(s, _conversa_do_pedido(ctx, alvo))
    pedido = ctx_pedido.get("pedido") if isinstance(ctx_pedido, Mapping) else None
    if not pedido:
        return
    log = ctx_pedido.get("logistica") if isinstance(ctx_pedido.get("logistica"), Mapping) else {}
    postado = await acao_pessoa.estado_de_postagem(
        s, numero_bling=pedido.get("numero"), logistica=dict(log), agora=ctx.agora()
    )
    ctx.registrar_pedido_lido(ctx_pedido, postado)


# ── 3. ver_garantia ───────────────────────────────────────────────────────


def _hoje(ctx: ContextoAgente) -> date:
    """O dia da conta de garantia: o de hoje (o mesmo do Painel), ou o do `as_of` no replay."""
    if ctx.modo_replay and ctx.as_of is not None:
        return garantia_svc.dia_em_sp(ctx.as_of)
    return garantia_svc.hoje()


def _iso(valor: date | None) -> str | None:
    return valor.isoformat() if valor is not None else None


def _para_validador(g: Mapping[str, Any]) -> dict:
    """O item de `garantias_conferidas` (o que `validador.conferir_garantia` lê)."""
    return {
        "inicio": g.get("inicio"),
        "fim_hardware": g.get("fim_hardware"),
        "fim_software": g.get("fim_software"),
        "status": g.get("status"),
        "produto": g.get("produto"),
        "regra": g.get("regra"),
        **({"antes_da_compra": g["antes_da_compra"]} if "antes_da_compra" in g else {}),
    }


# O acessório "a0NN" é das duas marcas (`sites_marca._SKU_MARCA` o tira de
# propósito): só é Uranyx com "uranyx" no nome (a001, a003, a016–a020).
_ACESSORIO_A0NN = re.compile(r"^a0\d{2}", re.IGNORECASE)


def linha_do_item(sku: str | None, descricao: str | None) -> str:
    """A linha de UM item: Uranyx (`u*`/`dg\\d`, ou `a0NN` com "uranyx" no
    nome), Charlots (`b\\d`/`bp\\d`) ou outra (revisão de 20/10/2026: o
    `garantia.e_uranyx` do Painel põe todo `a0NN` na Uranyx — a balança de
    mala e o kit de rodinhas ganhavam "3/12 meses"). PURA."""
    codigo = (sku or "").strip()
    if sites_marca._SKU_MARCA[sites_marca.URANYX].match(codigo):
        return LINHA_URANYX
    if sites_marca._SKU_MARCA[sites_marca.CHARLOTS].match(codigo):
        return LINHA_CHARLOTS
    if _ACESSORIO_A0NN.match(codigo) and "uranyx" in validador.plano_de(descricao or ""):
        return LINHA_URANYX
    return LINHA_OUTRA


def _linha_do_pedido(info: garantia_svc.PedidoInfo) -> tuple[str, dict | None]:
    """(linha do produto, item que dá o nome) — a Charlots vence (o pedido com
    mala e acessório é da mala); depois a Uranyx; senão outra (garantia legal)."""
    itens = [i for i in info.itens if isinstance(i, Mapping)]
    linhas = [(linha_do_item(i.get("sku"), i.get("descricao")), i) for i in itens]
    for linha in (LINHA_CHARLOTS, LINHA_URANYX):
        if item := next((i for x, i in linhas if x == linha), None):
            return linha, item
    return LINHA_OUTRA, itens[0] if itens else None


async def _calculo(s: AsyncSession, ctx: ContextoAgente, alvo: Alvo) -> Resultado:
    """O cálculo do Painel (a prévia de `routers/garantias.py`), sem gravar nada."""
    base_dados: dict[str, Any] = {"pedido": alvo.rotulo, "garantia_registrada": False}
    termo = alvo.numero_bling or alvo.numeroloja
    info = await garantia_svc.buscar_pedido(s, termo) if termo else None
    # O pedido casado pelo NÚMERO (buscar_pedido também casa pelo nº do
    # marketplace, e um pedido mais novo com aquele `numeroloja` é outra
    # compra — contexto.garantia_para_ia).
    if info is None or (alvo.numero_bling and info.numero != alvo.numero_bling):
        return Resultado(
            dados={**base_dados, "pedido_no_sistema": False},
            ids=(contexto.id_garantia_calculada(alvo.rotulo),),
        )
    entrega = await garantia_svc.resolver_entrega(s, info.numero, info.numeroloja)
    inicio = entrega.dia if entrega is not None else None
    linha, item = _linha_do_pedido(info)
    ids = [contexto.id_garantia_calculada(alvo.rotulo)]
    sem_data = garantia_svc.entregue_sem_data(inicio, info.situacao_id)
    fim_hw = fim_sw = None
    status = rotulo = None
    regra: dict | None = None
    if linha == LINHA_URANYX:
        fim_hw, fim_sw = garantia_svc.calcular_prazos(inicio) if inicio else (None, None)
        status = garantia_svc.status_em(inicio, fim_hw, fim_sw, _hoje(ctx))
        if sem_data:
            status = contexto_svc.STATUS_ENTREGUE_SEM_DATA
        rotulo = contexto_svc.STATUS_GARANTIA_IA[status]
        regra = dict(REGRA_URANYX)
    elif linha == LINHA_CHARLOTS:
        fato = await fatos_loja.fato_da_chave(s, CHAVE_GARANTIA_CHARLOTS, agora=ctx.agora())
        if fato is not None:
            regra = {
                "dias": DIAS_GARANTIA_CHARLOTS,
                "a_partir_de": "entrega",
                "fato": fato["id"],
                "texto": fato["texto"],
            }
            ids.append(fato["id"])
            # Revisão "furos" de 20/10/2026: o STATUS da Charlots também (antes
            # ficava None e o "90 dias" da regra livrava a promessa de
            # cobertura de uma entrega de 10 meses atrás).
            fim_hw = fim_sw = fim_charlots(inicio)
            status = status_charlots(inicio, _hoje(ctx))
            if sem_data:
                status = contexto_svc.STATUS_ENTREGUE_SEM_DATA
            rotulo = contexto_svc.STATUS_GARANTIA_IA[status]
    if regra is None:
        regra = dict(REGRA_LEGAL)
    origem = (
        contexto_svc._ORIGEM_DA_DATA_IA.get(entrega.origem)
        or garantia_svc.ORIGEM_ROTULOS.get(entrega.origem)
        if entrega is not None
        else None
    )
    dados = {
        **base_dados,
        "linha": linha,
        "produto": _cortar((item or {}).get("descricao"), MAX_CHARS_PRODUTO),
        "produto_uranyx": linha == LINHA_URANYX,
        "status": status,
        "status_rotulo": rotulo,
        "inicio": _iso(inicio),
        "inicio_origem": origem,
        "fim_hardware": _iso(fim_hw),
        "fim_software": _iso(fim_sw),
        "entregue_sem_data": sem_data,
        "regra": regra,
    }
    return Resultado(dados=dados, ids=tuple(ids), garantias=(_para_validador(dados),))


def fim_charlots(inicio: date | None) -> date | None:
    """O último dia coberto da garantia da Charlots (90 dias a partir da entrega). PURA."""
    return inicio + timedelta(days=DIAS_GARANTIA_CHARLOTS) if inicio is not None else None


def status_charlots(inicio: date | None, dia: date) -> str:
    """O status da garantia da Charlots no `dia` (o mesmo `status_em` do Painel). PURA."""
    fim = fim_charlots(inicio)
    return garantia_svc.status_em(inicio, fim, fim, dia)


# A conversa já é de DEPOIS da compra (revisão "furos" de 20/10/2026): o
# pedido, o cartão de pedido ou o cliente falando da compra/do uso ("comprei
# faz 1 ano", "meu celular parou"). Aí o prazo da regra da linha NÃO livra a
# promessa de cobertura (sem status, ninguém sabe se ainda vale).
_FALA_DE_COMPRA_FEITA = re.compile(
    r"\b(?:comprei|compramos|recebi|recebemos|chegou|ganhei|ganhamos|pedi|tenho\s+(?:um|uma|esse"
    r"|este|essa|esta|o|a)|meu\s+(?:celular|aparelho|relogio|smartwatch|produto|telefone|fone"
    r"|pedido)|minha\s+(?:mala|compra|fritadeira|air\s+fryer)|faz\s+\d+\s*(?:dias?|semanas?|mes"
    r"|meses|anos?)|ha\s+\d+\s*(?:dias?|semanas?|mes|meses|anos?)|parou|quebrou|estragou"
    r"|deu\s+(?:defeito|problema)|nao\s+(?:liga|carrega|funciona)|com\s+defeito)\b"
)


def depois_da_compra(conversa: Any, base: BaseDoGerar) -> bool:
    """A conversa é de depois da compra (há pedido ou o cliente fala dela)? PURA.

    Na pergunta PÚBLICA do anúncio só conta a fala do cliente (o pedido de
    outra conversa não diz nada da pergunta).
    """
    from app.services.atendimento.agente import acao_pessoa

    texto = validador.plano_de(" ".join(t for t in base.textos_cliente if isinstance(t, str)))
    if _FALA_DE_COMPRA_FEITA.search(texto):
        return True
    if getattr(conversa, "canal", None) == constantes.CANAL_PERGUNTA:
        return False
    return acao_pessoa.ha_pedido(conversa, base.ctx, base.recentes)


async def dados_ver_garantia(
    ctx: ContextoAgente, base: BaseDoGerar, conversa: Any, numero: str | None
) -> Resultado:
    """O registro do Painel (`contexto.garantia_para_ia`) ou o cálculo do Painel.

    `numero=None` = o pedido da conversa. 1º o REGISTRO do Painel daquele
    pedido; sem registro, o CÁLCULO (entrega → prazos, só Uranyx; a Charlots
    pelo fato aprovado da marca; as outras linhas sem prazo próprio). Nunca
    CPF, nome nem a NF do destinatário (o `PedidoInfo` tem: não sai daqui).
    """
    textos = list(base.textos_cliente)
    async with sessao.sessao_leitura() as s:
        if ctx.canal == constantes.CANAL_PERGUNTA:
            # A pergunta PÚBLICA: nada de pedido nem de registro do Painel
            # (a resposta é visível para todos) — só a regra da linha.
            return await _regra_sem_pedido(s, ctx, base, conversa)
        if numero is None or not str(numero).strip():
            alvo = alvo_da_conversa(conversa, base)
        else:
            alvo = await pedido_no_escopo(s, ctx, base, conversa, numero)
            await _registrar_outro_pedido(s, ctx, alvo)
        if alvo is None:
            # Sem pedido: o registro do Painel só pelo que o comprador citou.
            achadas = (
                (await contexto_svc.garantia_para_ia(s, conversa, textos, None))["garantias"]
                if conversa is not None
                else []
            )
            if not achadas:
                # Antes da compra (ou sem o pedido): a REGRA da linha do
                # anúncio da conversa (revisão de 20/10/2026).
                return await _regra_sem_pedido(s, ctx, base, conversa)
            registro = achadas
        else:
            if alvo.da_conversa and conversa is not None:
                quem, pedido, citados = conversa, base.ctx.get("pedido"), textos
            else:
                quem = _conversa_do_pedido(ctx, alvo)
                pedido, citados = {"numero": alvo.numero_bling}, []
            achadas = (await contexto_svc.garantia_para_ia(s, quem, citados, pedido))["garantias"]
            registro = [g for g in achadas if g.get("de_onde") == contexto_svc.DE_ONDE_PEDIDO]
            if not registro:
                return await _calculo(s, ctx, alvo)
    # O pedido já diz de qual compra é (o "de onde" só vale na garantia
    # achada pelo que o comprador citou, sem pedido na conversa).
    garantias = [
        {
            **{k: v for k, v in g.items() if k != "de_onde" or alvo is None},
            "regra": dict(REGRA_URANYX),
        }
        for g in registro
    ]
    return Resultado(
        dados={
            "pedido": alvo.rotulo if alvo is not None else None,
            "garantia_registrada": True,
            "garantias": garantias,
        },
        ids=tuple(contexto.id_garantia(g["id"]) for g in registro),
        garantias=tuple(_para_validador(g) for g in garantias),
    )


async def _regra_sem_pedido(
    s: AsyncSession, ctx: ContextoAgente, base: BaseDoGerar, conversa: Any
) -> Resultado:
    """A regra de garantia da LINHA do anúncio da conversa, sem pedido. Sem data.

    A linha pelos produtos vinculados ao anúncio (`linha_do_item`): Uranyx →
    3 meses de hardware e 12 de software a partir da entrega; Charlots → o
    fato aprovado da marca (sem ele, nenhuma regra); outra → nenhuma (a
    garantia legal). Vai também para `garantias_conferidas` (status None):
    o validador aceita o prazo da regra, nunca uma data.
    """
    dados: dict[str, Any] = {"pedido": None, "garantia_registrada": False, "sem_pedido": True}
    citados = (
        ficha.anuncios_da_conversa(conversa, base.recentes)
        if conversa is not None and getattr(conversa, "plataforma", None)
        else []
    )
    item_id = citados[0].item_id if citados else getattr(conversa, "anuncio_id", None)
    plat = ficha.plataforma_do_catalogo(ctx.plataforma)
    if not item_id or plat is None or ctx.integration_id is None:
        # Sem saber o produto: o agente pergunta qual é (fontes v1.3, decisão 3).
        return Resultado(dados=dados)
    linhas = {
        linha_do_item(sku, nome)
        for nome, sku in (
            await s.execute(
                select(Product.name, Product.sku)
                .join(ProductLink, ProductLink.product_id == Product.id)
                .where(
                    ProductLink.integration_id == ctx.integration_id,
                    ProductLink.platform == plat,
                    ProductLink.external_id == str(item_id),
                    ProductLink.morto_desde.is_(None),
                )
                .limit(especificacoes.MAX_PRODUTOS)
            )
        ).all()
    }
    regra: dict | None = None
    ids: list[str] = []
    if linhas == {LINHA_URANYX}:
        regra = dict(REGRA_URANYX)
    elif linhas == {LINHA_CHARLOTS}:
        fato = await fatos_loja.fato_da_chave(s, CHAVE_GARANTIA_CHARLOTS, agora=ctx.agora())
        if fato is not None:
            regra = {
                "dias": DIAS_GARANTIA_CHARLOTS,
                "a_partir_de": "entrega",
                "fato": fato["id"],
                "texto": fato["texto"],
            }
            ids.append(fato["id"])
    linha = next(iter(linhas)) if len(linhas) == 1 else LINHA_OUTRA
    regra = regra or dict(REGRA_LEGAL)
    dados.update({"linha": linha, "regra": regra})
    # Só ANTES da compra o prazo da regra livra a frase de garantia (revisão
    # "furos": "comprei esse celular aqui faz 1 ano" + "está coberto pela
    # garantia de 3 meses" saía).
    depois = depois_da_compra(conversa, base)
    return Resultado(
        dados=dados,
        ids=tuple(ids),
        garantias=(
            {"status": None, "regra": regra, **({"antes_da_compra": False} if depois else {})},
        ),
    )


# ── 4. consultar_manual ───────────────────────────────────────────────────


def _do_assunto(regras: Iterable[Any], assunto: str) -> list[dict]:
    """As regras de ASSUNTO (`categoria`) do assunto, na ordem do prompt (`organizar_manual`)."""
    return [
        {"quando": " ".join(str(r.quando).split()), "faca": " ".join(str(r.faca).split())}
        for r in ia.organizar_manual(list(regras), assunto)
        if ia._tipo(r) == constantes.TIPO_REGRA_CATEGORIA and r.categoria == assunto
    ]


# O que as decisões do dono mudaram na regra do manual base (revisão "furos"
# de 20/10/2026; o "Manual v2" com `faca_agente` ainda não existe): sai junto
# da regra do assunto, para o modelo não receber ordens que brigam. Vale por
# cima da regra só no que diz.
POR_CIMA_DA_REGRA: dict[str, str] = {
    "estoque_variacao": (
        "Decisão do dono: diga disponível ou esgotado pelas variações do ver_anuncio "
        "(confiavel=true); sem o dado confiável, dado_indisponivel. O resto da regra vale."
    ),
    "confirmar_variacao": (
        "Decisão do dono: diga disponível ou esgotado pelas variações do ver_anuncio "
        "(confiavel=true); sem o dado confiável, dado_indisponivel."
    ),
    "garantia": (
        "Decisão do dono: o prazo da regra e as datas que o ver_garantia devolveu podem ser "
        "ditos, como vieram (sem número de outra fonte). Garantia vencida ou só de software: "
        "pessoa. Nunca diga que a equipe confirma o prazo (é promessa): diga como acionar."
    ),
    "defeito": (
        "Decisão do dono: acolha e peça vídeo curto e fotos por aqui; a equipe analisa e "
        "responde por aqui. Nunca diga que a equipe confirma, troca ou reenvia (é promessa)."
    ),
    "cobertura_regiao": (
        "Decisão do dono: cite a frase do anúncio sobre o estado e oriente conferir o prazo e o "
        "frete pelo CEP; nunca 'sim' sozinho, 'todo o Brasil' nem 'entrega nacional'."
    ),
    "prazo_antes_compra": (
        "Decisão do dono: o prazo é o que o anúncio mostra pelo CEP; nunca 'essa semana', "
        "'em poucos dias', 'antes de' uma data, nem número que não veio dos dados."
    ),
}


def _skus_do_pedido(base: BaseDoGerar) -> list[str]:
    pedido = base.ctx.get("pedido") if isinstance(base.ctx, Mapping) else None
    itens = (pedido or {}).get("itens") if isinstance(pedido, Mapping) else None
    return [str(i.get("sku")) for i in itens or [] if isinstance(i, Mapping) and i.get("sku")]


async def dados_consultar_manual(
    ctx: ContextoAgente,
    base: BaseDoGerar,
    assunto: str,
    pergunta: str | None,
    *,
    conversa: Any = None,
) -> Resultado:
    """Regra do assunto, orientação do agente, respostas prontas e fatos aprovados."""
    from app.services.atendimento.agente import prompt

    assunto = " ".join(str(assunto or "").split())
    validos = base.ids_categorias
    if assunto not in validos:
        return Resultado(dados={"assunto_invalido": True, "assuntos": list(validos)})
    regras = _do_assunto(base.regras, assunto)
    orientacao = prompt.ORIENTACAO.get(assunto)
    citados = (
        ficha.anuncios_da_conversa(conversa, base.recentes)
        if conversa is not None and getattr(conversa, "plataforma", None)
        else []
    )
    item_id = citados[0].item_id if citados else (getattr(conversa, "anuncio_id", None) or None)
    titulo = (citados[0].titulo if citados else None) or getattr(conversa, "anuncio_titulo", None)
    skus = _skus_do_pedido(base)
    async with sessao.sessao_leitura() as s:
        m = AtendimentoModelo
        modelos = (
            await s.execute(
                select(m.id, m.texto)
                .where(
                    m.ativo.is_(True),
                    m.categoria == assunto,
                    or_(m.plataforma.is_(None), m.plataforma == ctx.plataforma),
                    or_(m.canal.is_(None), m.canal == ctx.canal),
                )
                .order_by(m.ordem, m.titulo, m.id)
                .limit(MAX_RESPOSTAS_PRONTAS)
            )
        ).all()
        prontas = [
            {"id": contexto.id_modelo(linha.id), "texto": _cortar(linha.texto)}
            for linha in modelos
            if (linha.texto or "").strip()
        ]
        fatos: list[dict] = []
        if not ctx.sem_fatos:
            plat = ficha.plataforma_do_catalogo(ctx.plataforma)
            produtos = (
                await especificacoes.produtos_do_anuncio(s, plat, ctx.integration_id, item_id)
                if item_id and plat is not None
                else []
            )
            fatos = await fatos_loja.fatos_aplicaveis(
                s,
                plataforma=ctx.plataforma,
                integration_id=ctx.integration_id,
                item_id=item_id,
                product_ids=produtos,
                skus=skus,
                marca=sites_marca.marca_do_anuncio(titulo, skus),
                assunto=assunto,
                pergunta=pergunta,
                agora=ctx.agora(),
            )
    dados: dict[str, Any] = {
        "assunto": assunto,
        "regra": regras[0] if regras else None,
        "orientacao": orientacao,
        "respostas_prontas": prontas,
        "fatos": [{k: f.get(k) for k in ("id", "texto", "escopo")} for f in fatos],
    }
    if por_cima := POR_CIMA_DA_REGRA.get(assunto):
        dados["por_cima_da_regra"] = por_cima
    if len(regras) > 1:
        dados["mais_regras"] = regras[1:]
    # O que cabe: os fatos (já por relevância) valem mais que as respostas
    # prontas, e estas mais que as regras extras.
    dados = caber(dados, listas=("fatos", "respostas_prontas", "mais_regras"))
    if not dados.get("mais_regras"):
        dados.pop("mais_regras", None)
    ids = [contexto.id_regra(assunto)] if regras or orientacao else []
    ids += [p["id"] for p in dados["respostas_prontas"]] + [f["id"] for f in dados["fatos"]]
    return Resultado(dados=dados, ids=tuple(ids))


# ── 5. buscar_respostas_equipe ────────────────────────────────────────────


async def dados_buscar_respostas_equipe(
    ctx: ContextoAgente, pergunta: str, item_id: str | None
) -> Resultado:
    """Até 5 pares generalizados (`pares.buscar`), com `AVISO_TOM`. Nunca permite nada."""
    item = " ".join(str(item_id or "").split())
    async with sessao.sessao_leitura() as s:
        achados = await pares.buscar(
            s,
            pergunta=str(pergunta or ""),
            plataforma=ctx.plataforma,
            integration_id=ctx.integration_id,
            item_id=item if _RX_ITEM.fullmatch(item) else None,
            conversa_id=ctx.conversa_id,
            as_of=ctx.agora(),
            canal_no_automatico=ctx.modo == constantes.MODO_AUTO,
        )
    dados = caber({"aviso": AVISO_TOM, "exemplos": list(achados)}, listas=("exemplos",))
    return Resultado(
        dados={
            "aviso": AVISO_TOM,
            "exemplos": [{k: v for k, v in e.items() if k != "id"} for e in dados["exemplos"]],
        },
        permitir_texto=False,
        ids_rastro=tuple(contexto.id_par(e["id"]) for e in dados["exemplos"]),
    )


# ── 6. sugerir_troca ──────────────────────────────────────────────────────


async def _itens_do_pedido(s: AsyncSession, numero_bling: str) -> list[dict]:
    linhas = (
        await s.execute(
            select(BlingOrder.item_codigo, BlingOrder.item_descricao, BlingOrder.item_quantidade)
            .where(BlingOrder.numero == numero_bling)
            .order_by(BlingOrder.item_index)
        )
    ).all()
    return [
        {
            "sku": linha.item_codigo,
            "descricao": linha.item_descricao,
            "quantidade": linha.item_quantidade,
        }
        for linha in linhas
        if linha.item_codigo or linha.item_descricao
    ]


async def dados_sugerir_troca(
    ctx: ContextoAgente, base: BaseDoGerar, numero: str | None, *, conversa: Any = None
) -> Resultado:
    """Opções de troca (`troca_sugestoes.sugestoes_do_pedido`, `ve_custo=False`).

    Só para o pedido no escopo que está em Aguardando Cancelamento por
    FALTA DE ESTOQUE (`ag_cancelamento.classificar`: `SEM_ESTOQUE` com
    `pode_sugerir_troca`) e com a sugestão de troca ligada no sistema; só
    os níveis 1 e 2 dentro da regra (`motivo_fora` vazio). Sem custo, sem %,
    sem SKU. A pessoa manda a oferta e faz a troca pelo painel.
    """
    from app.services.atendimento import etiqueta_fatos, troca_sugestoes

    async with sessao.sessao_leitura() as s:
        alvo = await pedido_no_escopo(s, ctx, base, conversa, numero)
        await _registrar_outro_pedido(s, ctx, alvo)
        sem = Resultado(
            dados={"pedido": alvo.rotulo, "opcoes": [], "motivo": SEM_TROCA},
            ids=(contexto.id_troca(alvo.rotulo),),
        )
        if not get_settings().atendimento_troca_sugestoes_ativa or not alvo.numero_bling:
            return sem
        pedido = await etiqueta_fatos.pedido_bling(s, [alvo.numero_bling])
        if pedido is None or pedido.numero != alvo.numero_bling:
            return sem
        status = (
            ag_cancelamento.status_na_plataforma(getattr(conversa, "dados", None))
            if alvo.da_conversa
            else None
        )
        motivo = ag_cancelamento.classificar(pedido, status_plataforma=status)
        if (
            motivo is None
            or motivo.codigo != ag_cancelamento.SEM_ESTOQUE
            or not motivo.pode_sugerir_troca
            or not motivo.skus
        ):
            return sem
        itens = await _itens_do_pedido(s, alvo.numero_bling)
        bloco = await troca_sugestoes.sugestoes_do_pedido(s, motivo.skus, itens, ve_custo=False)
    opcoes: list[dict] = []
    texto_oferta = None
    em_falta: list[str] = []
    for item in bloco.get("itens") or []:
        validas = [
            sug
            for sug in item.get("sugestoes") or []
            if sug.get("nivel") in NIVEIS_TROCA and sug.get("motivo_fora") is None
        ]
        if not validas:
            continue
        if item.get("nome_original"):
            em_falta.append(_cortar(item["nome_original"], 120) or "")
        for sug in validas:
            opcoes.append(
                {
                    "nome": _cortar(sug.get("nome"), 160),
                    "nivel": sug.get("nivel"),
                    "mesmo_produto": bool(sug.get("mesmo_produto")),
                }
            )
            texto_oferta = texto_oferta or sug.get("texto_oferta")
    if not opcoes:
        return sem
    # Opção de troca = o pedido está parado em Ag. cancelamento por falta de
    # estoque: a resposta é da pessoa (o código marca; revisão de 20/10/2026).
    ctx.acoes_codigo = contexto.ordenar_acoes(ctx.acoes_codigo, [constantes.ACAO_AG_CANCELAMENTO])
    return Resultado(
        dados={
            "pedido": alvo.rotulo,
            "item_em_falta": [n for n in em_falta if n],
            "opcoes": opcoes[:MAX_OPCOES_TROCA],
            "texto_oferta": texto_oferta,
        },
        ids=(contexto.id_troca(alvo.rotulo),),
    )


# ── As ferramentas desta execução ─────────────────────────────────────────


def ferramentas(
    ctx: ContextoAgente, base: BaseDoGerar, conversa: Any
) -> list[BetaAsyncFunctionTool[Any]]:
    """As ferramentas desta execução, na ordem de `NOMES`, menos `ctx.ferramentas_fora`.

    Cada uma é um `@beta_async_tool(strict=True)` sobre uma função interna
    com a assinatura EXATA do modelo (sem `ctx`): `ver_anuncio(item_id:
    str)`, `ver_pedido(numero: str)`, `ver_garantia(numero: str | None)`,
    `consultar_manual(assunto: str, pergunta: str | None)`,
    `buscar_respostas_equipe(pergunta: str, item_id: str | None)`,
    `sugerir_troca(numero: str | None)` — e que devolve `await executar(ctx, nome,
    args, lambda: dados_*(...))`. A descrição vem da docstring (Args: …).
    Uma `ficha_loja.Busca` só por execução (as cotas de ida à loja da
    sugestão); nenhuma no replay.
    """
    loja = None if ctx.sem_ida_a_loja else ficha_loja.Busca(ctx.integration_id, ctx.canal_id)

    @beta_async_tool(strict=True, description=DESCRICOES[VER_ANUNCIO])
    async def ver_anuncio(item_id: str) -> str:
        """O anúncio.

        Args:
            item_id: o id do anúncio na plataforma, como aparece no cartão ou na conversa.
        """
        return await executar(
            ctx,
            VER_ANUNCIO,
            {"item_id": item_id},
            lambda: dados_ver_anuncio(ctx, item_id, base=base, conversa=conversa, loja=loja),
        )

    @beta_async_tool(strict=True, description=DESCRICOES[VER_PEDIDO])
    async def ver_pedido(numero: str) -> str:
        """Outro pedido do comprador.

        Args:
            numero: o número do pedido (na plataforma ou no sistema), como o cliente escreveu.
        """
        return await executar(
            ctx,
            VER_PEDIDO,
            {"numero": numero},
            lambda: dados_ver_pedido(ctx, base, numero, conversa=conversa),
        )

    @beta_async_tool(strict=True, description=DESCRICOES[VER_GARANTIA])
    async def ver_garantia(numero: str | None) -> str:
        """A garantia.

        Args:
            numero: o número do pedido; null para o pedido desta conversa.
        """
        return await executar(
            ctx,
            VER_GARANTIA,
            {"numero": numero},
            lambda: dados_ver_garantia(ctx, base, conversa, numero),
        )

    @beta_async_tool(strict=True, description=DESCRICOES[CONSULTAR_MANUAL])
    async def consultar_manual(assunto: str, pergunta: str | None) -> str:
        """O manual do assunto.

        Args:
            assunto: o id do assunto, da lista de assuntos.
            pergunta: a dúvida do cliente em poucas palavras (acha os fatos); null para todos.
        """
        return await executar(
            ctx,
            CONSULTAR_MANUAL,
            {"assunto": assunto, "pergunta": pergunta},
            lambda: dados_consultar_manual(ctx, base, assunto, pergunta, conversa=conversa),
        )

    @beta_async_tool(strict=True, description=DESCRICOES[BUSCAR_RESPOSTAS_EQUIPE])
    async def buscar_respostas_equipe(pergunta: str, item_id: str | None) -> str:
        """Exemplos da equipe.

        Args:
            pergunta: a dúvida do cliente em poucas palavras.
            item_id: o id do anúncio da conversa (prefere exemplos dele); null sem anúncio.
        """
        return await executar(
            ctx,
            BUSCAR_RESPOSTAS_EQUIPE,
            {"pergunta": pergunta, "item_id": item_id},
            lambda: dados_buscar_respostas_equipe(ctx, pergunta, item_id),
        )

    @beta_async_tool(strict=True, description=DESCRICOES[SUGERIR_TROCA])
    async def sugerir_troca(numero: str | None) -> str:
        """As opções de troca.

        Args:
            numero: o número de outro pedido do mesmo comprador; null para o pedido desta conversa.
        """
        return await executar(
            ctx,
            SUGERIR_TROCA,
            {"numero": numero},
            lambda: dados_sugerir_troca(ctx, base, numero, conversa=conversa),
        )

    # A pergunta PÚBLICA do anúncio (ML): a resposta fica visível para todos —
    # nada de pedido nem troca; a garantia só pela regra da linha (revisão de
    # 20/10/2026; o legado nem busca pedido nela).
    fora = set(ctx.ferramentas_fora)
    if ctx.canal == constantes.CANAL_PERGUNTA:
        fora |= FORA_DA_PERGUNTA
    saida: list[BetaAsyncFunctionTool[Any]] = []
    for ferramenta in (
        ver_anuncio,
        ver_pedido,
        ver_garantia,
        consultar_manual,
        buscar_respostas_equipe,
        sugerir_troca,
    ):
        if ferramenta.name in fora:
            continue
        # `strict` exige o objeto fechado (o SDK não acrescenta sozinho).
        ferramenta.input_schema = {**ferramenta.input_schema, "additionalProperties": False}
        saida.append(ferramenta)
    return saida
