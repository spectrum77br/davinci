"""Painel do pedido da conversa (RF1 ④) e a nota interna — item 3 do Comunicador (01/10/2026).

O que a pessoa que atende precisa ver AO LADO da conversa, sem abrir cinco
telas: o saldo do que o comprador comprou, a margem do pedido, as
Observações do pedido no Bling, os botões "Abrir no Bling" / "Abrir na
plataforma" e o perfil do AdsPower da loja. Tudo SÓ LEITURA: nada aqui
escreve no Bling nem na plataforma.

O contrato com o outro dev (`davinci-atendimento-nomes-para-o-dev.md`):

  saldo_do_item(session, sku, *, quantidade=None)
      Saldo do SKU no DaVinci (`products.stock`: o saldo VIRTUAL do Bling que
      o webhook grava — já sem o que está reservado para pedidos em aberto),
      com a hora em que a linha do produto mudou por último. Junto: os LOTES
      IRMÃOS (o mesmo produto em outro estoque: o sufixo `.ci/.pi/.ra/.sa/
      .sp/.us/.cd` de `sku_tags.SUFFIX_TAGS`) e, no KIT, cada componente
      (`bling_kit_components`) com quanto o pedido precisa dele. O Bling dá
      ao kit o saldo do MENOR componente — por isso o kit mostra os dois.
      Para CONFIRMAR uma troca vale o saldo AO VIVO do Bling (item 4), não
      este.
  margem_do_pedido(session, pedido, *, ver_lucro=False)
      A MESMA margem da aba Margem: a mesma consulta (o snapshot
      `verificar_margem`, `routers/margens._build_marketplace_items_sql`) e a
      mesma regra da coluna "Margem" da tela (`margemFinal` em
      pages/margem.vue). Pedido fora do snapshot (mais velho que a janela da
      Margem) = "fora da tela de Margem", sem cálculo novo.
  observacoes_bling(bling_id) / invalidar_observacoes(bling_id)
      As Observações do pedido NO BLING, por GET ao vivo do pedido, com
      memória de 5 min (Redis; sem Redis, a do processo). ATENÇÃO:
      `bling_orders.observacao` é um campo LOCAL da aba Margem
      (`routers/margens.py`), não o espelho das Observações do Bling. Quem
      gravar na observação (a linha "TROCA ..." do item 4) chama
      `invalidar_observacoes` depois, para o painel mostrar na hora.
  criar_nota(session, conversa, texto, *, user_id)
      Nota interna: `tipo='nota'`, `origem='davinci_nota'`, autor `equipe`.
      Nunca é enviada, não conta como resposta, não entra na pendência e não
      vai para a IA (ver "Nota interna" abaixo).

E o que a tela usa por aqui:

  links_do_pedido(conversa, pedido, ids_shopee=None)
      "Abrir no Bling" e "Abrir na plataforma" (Shopee: o order_id interno
      de `links_shopee`, lido em `ids_shopee_do_painel`; sem ele, a busca)
  perfil_adspower(session, conversa)  o perfil (campo Servidor do store-info)
  ag_cancelamento_do_pedido(pedido, conversa, observacoes)
      o PORQUÊ do pedido em "Aguardando Cancelamento" (item 4, 02/10/2026):
      o classificador (`ag_cancelamento.classificar`) com o que o painel já
      leu — sem consulta nem GET a mais. `bloco_do_motivo` é o mesmo bloco
      para a lista Ag. cancelamento (`routers/atendimento_troca.py`).
  sugestões de troca (item 4, fase 4b, 05/10/2026): com a falta de estoque
      (`pode_sugerir_troca`) e `atendimento_troca_sugestoes_ativa`, até 3
      produtos parecidos e o texto da oferta (`troca_sugestoes`) — do
      catálogo do DaVinci, sem nenhum GET ao Bling.
  troca de produto (item 4, fase 4c, 07/10/2026): o bloco Ag. cancelamento
      traz a troca ABERTA do pedido (`troca_aberta`, de `troca.
      troca_aberta_do_pedido`, num SAVEPOINT: a tabela pode ainda não existir)
      e o `troca_envio` (o "Trocar" pode, ou o porquê — `troca.
      situacao_da_troca`). Fora de 83955 (a troca parada no meio com o
      pedido em 9 ou já em 6) a troca aberta vem no `troca_aberta` do painel.
  oferta de troca pelo chat (fase 4d, 07/10/2026): o bloco traz também
      `oferta_envio` — o botão "Enviar oferta" pode, ou o porquê de não
      (`troca_oferta.situacao_da_oferta`: as travas da rota e as do envio).
  quem não vê a Margem (07/10/2026, a caixa aberta para a equipe em só
      leitura): o motivo da Margem (`margem_trava`/`margem_reprovada`) vira
      "em análise" (`mascarar_motivo`), sem texto, conflito nem custo; e as
      Observações do Bling vêm sem o recado do robô da Margem
      (`observacoes_sem_margem`).
  painel_da_conversa(session, conversa, *, user)
      monta o GET /conversas/{id}/painel; cada bloco falha SOZINHO (num
      SAVEPOINT): uma consulta que quebre deixa o bloco vazio com o porquê,
      nunca o painel inteiro.

NOTA INTERNA. O autor é `equipe` (não `sistema`) DE PROPÓSITO: a mediação
da Magalu é `sistema`, e o SAC da Magalu decide de quem é a vez e para quem
vai a resposta pela "última fala de quem não é a loja" (`magalu.
_ultimo_pedido_sac`) — uma nota `sistema` mandaria a resposta ao comprador
em vez de à Magalu. Com `equipe`, nenhuma regra de fila, pendência, adoção
ou envio (que filtram por `cliente`/`loja`/`sistema`) a enxerga. As poucas
consultas que pegam "a última mensagem, qualquer uma" pulam a nota por
`constantes.e_nota` (gravar.recalcular/recalcular_conversa e a transcrição
da IA). `criar_nota` não mexe nos carimbos da conversa (ultima_*,
aguardando, prazo).

AdsPower (RF11): o DaVinci NÃO fala com o AdsPower — ele roda no computador
de quem atende. Daqui sai só QUAL perfil abrir (o número do campo
"Servidor" do store-info = `serial_number` no AdsPower); o navegador de quem
clicou chama a API local (`components/AtendimentoAdsPower.vue`).

Texto de comprador nunca vai para o log — só ids, códigos e contagens.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

import app.db as _db
from app.config import get_settings
from app.models import (
    AtendimentoCanal,
    AtendimentoConversa,
    AtendimentoMensagem,
    BlingKitComponent,
    BlingOrder,
    Integration,
    IntegrationPlatform,
    Product,
    SituacaoBling,
    StoreInfo,
    User,
    UserRole,
)
from app.services import links_shopee
from app.services.atendimento.ag_cancelamento import (
    CANCELADO_PLATAFORMA,
    DESCONHECIDO,
    MANUAL,
    MARGEM_REPROVADA,
    MARGEM_TRAVA,
    PEDIDO_CLIENTE,
    POS_NF_MANUAL,
    RESTRICAO_ENVIO,
    SEM_ESTOQUE,
    Motivo,
    classificar,
    cortar,
    status_na_plataforma,
)
from app.services.atendimento.constantes import (
    AUTOR_EQUIPE,
    MSG_RECEBIDA,
    ORIGEM_NOTA,
    PLATAFORMAS_ROBO,
    TIPO_NOTA,
)
from app.services.atendimento.etiqueta_fatos import (
    PedidoBling,
    normalizar_plataforma,
    pedido_bling_da_conversa,
)
from app.services.atendimento.troca_sugestoes import sugestoes_do_pedido, sugestoes_falhou
from app.services.bling_situacoes import (
    NOME_AGUARDANDO_CANCELAMENTO,
    SITUACAO_AGUARDANDO_CANCELAMENTO_STR,
)
from app.services.sku_tags import SUFFIX_TAGS

logger = structlog.get_logger()

# ── Nota interna ──────────────────────────────────────────────────────────
# Quem escreveu a nota: `constantes.AUTOR_EQUIPE` (NÃO é `sistema` — ver lá).
# Teto da nota (é recado para a equipe, não documento).
NOTA_MAX_CARACTERES = 4000


class NotaInvalida(ValueError):  # noqa: N818 — nome do domínio, como EnvioRecusado
    """A nota não pôde ser gravada (vazia, longa demais). `code` é estável."""

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(code)


async def criar_nota(
    session: AsyncSession,
    conversa: AtendimentoConversa,
    texto: str | None,
    *,
    user_id: UUID | None,
) -> AtendimentoMensagem:
    """Grava a NOTA INTERNA na conversa; devolve a mensagem. Nunca commita.

    Só a equipe vê: nada vai à plataforma, a conversa não sai nem entra na
    fila, o prazo não muda e a IA não lê. `user_id` None = nota do sistema
    (o robô do item 4 registrando o que fez, por exemplo).
    """
    limpo = (texto or "").replace("\x00", "").strip()
    if not limpo:
        raise NotaInvalida("nota_vazia", "Escreva a nota antes de salvar.")
    if len(limpo) > NOTA_MAX_CARACTERES:
        raise NotaInvalida(
            "nota_longa", f"A nota passa de {NOTA_MAX_CARACTERES} caracteres — resuma."
        )
    nota = AtendimentoMensagem(
        conversa_id=conversa.id,
        externo_id=None,
        autor=AUTOR_EQUIPE,
        origem=ORIGEM_NOTA,
        autor_user_id=user_id,
        tipo=TIPO_NOTA,
        texto=limpo,
        anexos=[],
        # A hora da nota é a nossa (não há relógio de plataforma).
        enviada_em=datetime.now(UTC),
        status=MSG_RECEBIDA,
        payload={},
    )
    session.add(nota)
    await session.flush()
    logger.info(
        "atendimento_nota_criada",
        conversa_id=str(conversa.id),
        mensagem_id=str(nota.id),
        user_id=str(user_id) if user_id else None,
    )
    return nota


# ── Blocos que falham sozinhos ────────────────────────────────────────────


async def _seguro[T](
    session: AsyncSession,
    etapa: str,
    consulta: Callable[[], Awaitable[T]],
    padrao: T,
    conversa_id: Any,
) -> tuple[T, bool]:
    """Roda um bloco num SAVEPOINT; erro vira (`padrao`, False) + log sem PII."""
    try:
        async with session.begin_nested():
            return await consulta(), True
    except Exception as e:  # noqa: BLE001 — um bloco do painel nunca derruba os outros
        logger.warning(
            "atendimento_painel_falhou",
            etapa=etapa,
            conversa_id=str(conversa_id),
            err=type(e).__name__,
        )
        return padrao, False


def _iso(valor: date | datetime | None) -> str | None:
    if valor is None:
        return None
    if isinstance(valor, datetime) and valor.tzinfo is None:
        valor = valor.replace(tzinfo=UTC)
    return valor.isoformat()


def _num(valor: Any) -> float | None:
    if valor is None:
        return None
    if isinstance(valor, Decimal):
        return float(valor)
    try:
        return float(valor)
    except (TypeError, ValueError):
        return None


# ── Estoque ───────────────────────────────────────────────────────────────
# Os lotes, na ordem em que a tela mostra: os de VENDA primeiro (os que o
# anúncio publica, `estoque_familia.LOTES_DE_VENDA`), depois o CD (Centro de
# Distribuição: ainda não distribuído) e o US (usado).
LOTES: tuple[str, ...] = ("ci", "pi", "ra", "sa", "sp", "cd", "us")
LOTES_DE_VENDA = frozenset({"ci", "pi", "ra", "sa", "sp"})
ROTULO_LOTE = {
    "cd": "Centro de Distribuição (ainda não distribuído)",
    "us": "usado",
}
assert set(LOTES) == set(SUFFIX_TAGS), "lote novo em sku_tags: ponha aqui também"


def _pedacos(sku: str | None) -> list[str]:
    return [p.strip() for p in (sku or "").strip().lower().split("+") if p.strip()]


def _lote_do_pedaco(pedaco: str) -> str | None:
    """`dg053.ci` → `ci`. Número não é lote (`b009.8.12` é tamanho)."""
    if "." not in pedaco:
        return None
    cauda = pedaco.rsplit(".", 1)[1]
    return cauda if cauda in LOTES else None


def lote_do_sku(sku: str | None) -> str | None:
    """O lote do SKU (o mesmo em todos os pedaços com lote); None sem lote ou misturado.

    Pedaço sem lote no kit (acessório `a075`) não atrapalha: `dg053.ci+a075`
    é do lote `ci` — a mesma regra de `estoque_familia.chave_familia`.
    """
    lotes = {_lote_do_pedaco(p) for p in _pedacos(sku)}
    lotes.discard(None)
    return next(iter(lotes)) if len(lotes) == 1 else None


def lotes_irmaos(sku: str | None) -> list[tuple[str, str]]:
    """[(lote, sku daquele lote)] — o mesmo produto em cada estoque, o próprio incluído.

    `dg053.ci+a001.ci` → (ci, dg053.ci+a001.ci), (pi, dg053.pi+a001.pi)...
    Só se monta o nome: quem não existe no catálogo some na consulta. Sem
    lote (ou lotes misturados no kit), lista vazia.
    """
    lote = lote_do_sku(sku)
    if lote is None:
        return []
    pedacos = _pedacos(sku)
    saida = []
    for outro in LOTES:
        novos = [
            f"{p[: -(len(lote) + 1)]}.{outro}" if _lote_do_pedaco(p) == lote else p for p in pedacos
        ]
        saida.append((outro, "+".join(novos)))
    return saida


def _melhor(linhas: list[Any]) -> Any | None:
    """Entre produtos com o mesmo SKU (donos diferentes): o ativo, o mais recente."""
    if not linhas:
        return None
    return sorted(
        linhas,
        key=lambda r: (
            (r.situacao or "A") == "A",
            r.updated_at or datetime.min.replace(tzinfo=UTC),
        ),
        reverse=True,
    )[0]


async def _produtos_por_sku(session: AsyncSession, skus: list[str]) -> dict[str, Any]:
    """{sku em minúsculas: linha do produto} — uma consulta para todos."""
    nomes = sorted({s.strip().lower() for s in skus if s and s.strip()})
    if not nomes:
        return {}
    linhas = (
        await session.execute(
            select(
                Product.sku,
                Product.name,
                Product.stock,
                Product.updated_at,
                Product.situacao,
                Product.formato,
                Product.bling_product_id,
            ).where(func.lower(Product.sku).in_(nomes))
        )
    ).all()
    por_sku: dict[str, list[Any]] = {}
    for r in linhas:
        por_sku.setdefault((r.sku or "").strip().lower(), []).append(r)
    return {k: _melhor(v) for k, v in por_sku.items()}


def _cobre(saldo: int | None, precisa: int | float | None) -> bool | None:
    if saldo is None or precisa is None:
        return None
    return saldo >= precisa


def _quantidade(valor: Any) -> int | None:
    n = _num(valor)
    if n is None or n <= 0:
        return None
    return int(n) if float(n).is_integer() else int(n) + 1


async def _componentes(
    session: AsyncSession, kit_bling_product_id: int, quantidade: int | None
) -> list[dict]:
    """Os componentes do kit, com o saldo de cada um e o que o pedido precisa."""
    comps = (
        await session.execute(
            select(BlingKitComponent.component_bling_product_id, BlingKitComponent.quantidade)
            .where(BlingKitComponent.kit_bling_product_id == kit_bling_product_id)
            .order_by(BlingKitComponent.component_bling_product_id)
        )
    ).all()
    if not comps:
        return []
    ids = [c.component_bling_product_id for c in comps]
    produtos = (
        await session.execute(
            select(
                Product.sku,
                Product.name,
                Product.stock,
                Product.updated_at,
                Product.situacao,
                Product.bling_product_id,
            ).where(Product.bling_product_id.in_(ids))
        )
    ).all()
    por_id: dict[int, list[Any]] = {}
    for p in produtos:
        por_id.setdefault(int(p.bling_product_id), []).append(p)
    saida = []
    for c in comps:
        p = _melhor(por_id.get(int(c.component_bling_product_id), []))
        por_kit = _num(c.quantidade) or 1.0
        precisa = por_kit * quantidade if quantidade is not None else None
        saldo = int(p.stock) if p is not None and p.stock is not None else None
        saida.append(
            {
                "sku": p.sku if p is not None else None,
                "nome": p.name if p is not None else None,
                "existe": p is not None,
                "quantidade_por_kit": por_kit,
                "necessario": precisa,
                "saldo": saldo,
                "atualizado_em": _iso(p.updated_at) if p is not None else None,
                "cobre": _cobre(saldo, precisa),
            }
        )
    return saida


async def saldo_do_item(session: AsyncSession, sku: str | None, *, quantidade: Any = None) -> dict:
    """O saldo do SKU comprado, dos lotes irmãos e (no kit) dos componentes.

    `quantidade` (opcional) = quanto o pedido leva: com ela, `cobre` diz se o
    saldo do lote comprado dá conta (e cada componente, no kit). O saldo é o
    de `products.stock` — o virtual do Bling, já sem o reservado —, com a hora
    da última mudança da linha. Nunca vai ao Bling.
    """
    sku_limpo = (sku or "").strip()
    qtd = _quantidade(quantidade)
    irmaos = lotes_irmaos(sku_limpo)
    candidatos = [sku_limpo] + [s for _, s in irmaos]
    produtos = await _produtos_por_sku(session, candidatos)
    proprio = produtos.get(sku_limpo.lower())
    lote = lote_do_sku(sku_limpo)
    saldo = int(proprio.stock) if proprio is not None and proprio.stock is not None else None

    lotes = []
    outros = 0
    for nome_lote, sku_irmao in irmaos:
        p = produtos.get(sku_irmao)
        if p is None:
            continue
        s = int(p.stock) if p.stock is not None else None
        e_o_proprio = nome_lote == lote
        if not e_o_proprio and s is not None and s > 0 and nome_lote in LOTES_DE_VENDA:
            outros += s
        lotes.append(
            {
                "lote": nome_lote,
                "sku": p.sku,
                "saldo": s,
                "atualizado_em": _iso(p.updated_at),
                "de_venda": nome_lote in LOTES_DE_VENDA,
                "rotulo": ROTULO_LOTE.get(nome_lote),
                "proprio": e_o_proprio,
                "ativo": (p.situacao or "A") == "A",
            }
        )

    kit = proprio is not None and (proprio.formato or "").upper() == "E"
    componentes: list[dict] = []
    if kit and proprio.bling_product_id is not None:
        componentes = await _componentes(session, int(proprio.bling_product_id), qtd)

    return {
        "sku": sku_limpo,
        "nome": proprio.name if proprio is not None else None,
        "existe": proprio is not None,
        "ativo": proprio is not None and (proprio.situacao or "A") == "A",
        "quantidade": qtd,
        "saldo": saldo,
        "atualizado_em": _iso(proprio.updated_at) if proprio is not None else None,
        "cobre": _cobre(saldo, qtd),
        "lote": lote,
        "lotes": lotes,
        # Peças nos OUTROS lotes de venda (o próprio fica de fora; CD e usado
        # também, porque não saem direto na venda).
        "saldo_outros_lotes": outros,
        "kit": kit,
        "componentes": componentes,
    }


async def _itens_do_pedido(
    session: AsyncSession, pedido: PedidoBling | None, conversa: AtendimentoConversa
) -> tuple[list[dict], str]:
    """Os itens a mostrar (sku, descrição, quantidade) e de onde vieram.

    Do Bling quando o pedido está no espelho; senão, do retrato do pedido na
    plataforma (`dados.pedido_mkt.itens`, o SKU do anúncio).
    """
    if pedido is not None:
        linhas = (
            await session.execute(
                select(
                    BlingOrder.item_codigo, BlingOrder.item_descricao, BlingOrder.item_quantidade
                )
                .where(BlingOrder.numero == pedido.numero)
                .order_by(BlingOrder.item_index)
                .limit(20)
            )
        ).all()
        itens = [
            {"sku": r.item_codigo, "descricao": r.item_descricao, "quantidade": r.item_quantidade}
            for r in linhas
            if (r.item_codigo or "").strip()
        ]
        if itens:
            return itens, "bling"
    dados = conversa.dados if isinstance(conversa.dados, dict) else {}
    retrato = dados.get("pedido_mkt") if isinstance(dados.get("pedido_mkt"), dict) else {}
    brutos = retrato.get("itens") if isinstance(retrato.get("itens"), list) else []
    itens = [
        {
            "sku": str(i.get("sku")).strip(),
            "descricao": i.get("titulo") if isinstance(i.get("titulo"), str) else None,
            "quantidade": i.get("quantidade"),
        }
        for i in brutos[:20]
        if isinstance(i, dict) and str(i.get("sku") or "").strip()
    ]
    return itens, ("plataforma" if itens else "nenhum")


# ── Margem ────────────────────────────────────────────────────────────────
# As plataformas em que a aba Margem ancora o saldo na PLATAFORMA (o líquido
# real): sem repasse (e sem saldo digitado à mão), a margem fica EM BRANCO —
# `saldoAncoradoNaPlataforma` / `margemFinal` em pages/margem.vue.
_PLATAFORMAS_SALDO_NA_PLATAFORMA = frozenset({"ml", "shopee", "tiktok"})
_ORDEM_STATUS_MARGEM = {"Reprovado": 0, "Pendente": 1, "Aprovado": 2}


def ve_margem(user: User | None) -> bool:
    """Quem vê a margem: o mesmo de `require_permission("margem", "view")`."""
    if user is None:
        return False
    if user.role == UserRole.ADMIN:
        return True
    return bool(((user.permissions or {}).get("margem") or {}).get("view"))


def ve_lojas(user: User | None) -> bool:
    """Quem vê o perfil do AdsPower da loja: o mesmo de `require_permission("lojas_info", "view")`.

    O nº do perfil abre as sessões logadas da loja; no resto do sistema o
    cadastro de Lojas (`store_info.server`) fica atrás desta permissão. Hoje
    a caixa é só de admin (`SO_ADMIN`); vale quando ela abrir para a equipe.
    """
    if user is None:
        return False
    if user.role == UserRole.ADMIN:
        return True
    return bool(((user.permissions or {}).get("lojas_info") or {}).get("view"))


# O bloco do AdsPower para quem não tem `lojas_info` (o botão fica desligado).
SEM_PERMISSAO_LOJAS = {
    "perfil": None,
    "motivo": "Sem permissão para ver o cadastro de Lojas (perfil do AdsPower).",
    "codigo": "sem_permissao",
}


def ve_lucro(user: User | None) -> bool:
    """O lucro em R$ — como na aba Margem, só administrador."""
    return user is not None and user.role == UserRole.ADMIN


def margem_final(linha: dict) -> float | None:
    """A margem da coluna "Margem" da aba Margem, para UMA linha-item.

    A pós-reembolso; nula ou zero (pedido sem reembolso/ajuste), a do Bling.
    ML/Shopee/TikTok sem repasse real e sem saldo manual: EM BRANCO (a tela
    da Margem não mostra número do Bling nem projeção nesses).
    """
    if (
        (linha.get("plataforma") or "") in _PLATAFORMAS_SALDO_NA_PLATAFORMA
        and linha.get("saldo_plataforma") is None
        and linha.get("saldo_manual") is None
    ):
        return None
    m = _num(linha.get("margem_pos_reembolso"))
    return _num(linha.get("margem_bling")) if m is None or m == 0 else m


async def margem_do_pedido(
    session: AsyncSession, pedido: PedidoBling | str | None, *, ver_lucro: bool = False
) -> dict | None:
    """A margem do pedido como a aba Margem mostra; None sem nº do Bling.

    `{na_margem, status, margem, margem_minima, abaixo_da_minima, itens: [...],
      aviso}`. `margem` do pedido = a do item (um item só) ou Σlucro ÷ Σcusto
    dos itens com margem (é a mesma conta, somada). `ver_lucro=False` tira o
    lucro em R$ (na Margem, só admin vê).
    """
    numero = pedido.numero if isinstance(pedido, PedidoBling) else str(pedido or "").strip()
    if not numero:
        return None
    # Import tardio: o router da Margem é pesado e não precisa subir com o painel.
    from app.routers.margens import _VERIFICAR_MARGEM_TABLE, _build_marketplace_items_sql

    sql = text(
        _build_marketplace_items_sql(
            _VERIFICAR_MARGEM_TABLE, "v.pedido_bling = :pedido", paginate=False
        )
    )
    linhas = [dict(r) for r in (await session.execute(sql, {"pedido": numero})).mappings().all()]
    if not linhas:
        return {
            "na_margem": False,
            "status": None,
            "margem": None,
            "margem_minima": None,
            "abaixo_da_minima": None,
            "itens": [],
            "aviso": (
                "Este pedido não está na tela de Margem (ela guarda só os pedidos recentes). "
                "Busque pelo nº na aba Margem para ver o histórico."
            ),
        }
    itens = []
    soma_lucro = 0.0
    soma_custo = 0.0
    com_margem = 0
    for r in linhas:
        m = margem_final(r)
        custo = _num(r.get("custo_produto"))
        minima = _num(r.get("margem_minima"))
        lucro = m * custo if m is not None and custo is not None else None
        if m is not None and custo:
            soma_lucro += lucro or 0.0
            soma_custo += custo
            com_margem += 1
        item = {
            "sku": r.get("sku"),
            "produto": r.get("produto"),
            "quantidade": r.get("quantidade"),
            "margem": m,
            "margem_minima": minima,
            "abaixo_da_minima": (m < (minima or 0.0)) if m is not None else None,
            "status": r.get("status"),
            "data_especial": bool(r.get("data_especial")),
            "aguardando_repasse": m is None
            and (r.get("plataforma") or "") in _PLATAFORMAS_SALDO_NA_PLATAFORMA,
        }
        if ver_lucro:
            item["lucro"] = lucro
            item["custo"] = custo
        itens.append(item)
    if len(itens) == 1:
        margem = itens[0]["margem"]
    else:
        margem = (soma_lucro / soma_custo) if com_margem == len(itens) and soma_custo else None
    minimas = [i["margem_minima"] for i in itens if i["margem_minima"] is not None]
    minima = max(minimas) if minimas else None
    status = min(
        (i["status"] for i in itens if i["status"] in _ORDEM_STATUS_MARGEM),
        key=lambda s: _ORDEM_STATUS_MARGEM[s],
        default=None,
    )
    saida = {
        "na_margem": True,
        "status": status,
        "margem": margem,
        "margem_minima": minima,
        "abaixo_da_minima": (margem < (minima or 0.0)) if margem is not None else None,
        "itens": itens,
        "aviso": (
            "Aguardando o repasse da plataforma: a Margem deixa em branco até ele chegar."
            if margem is None and any(i["aguardando_repasse"] for i in itens)
            else None
        ),
    }
    if ver_lucro and margem is not None:
        saida["lucro"] = itens[0].get("lucro") if len(itens) == 1 else soma_lucro
    return saida


# ── Observações do Bling (GET ao vivo, cache 5 min, SÓ LEITURA) ───────────
OBSERVACOES_TTL_S = 300
# Falha (Bling fora, 429): não martela o Bling a cada abertura do painel.
OBSERVACOES_TTL_ERRO_S = 60
# Teto da espera pelo Bling: o painel não pendura por causa dele.
OBSERVACOES_TEMPO_MAXIMO_S = 10.0
_CHAVE_OBS = "atendimento:obs_bling:{}"
# Sem Redis (teste, Redis fora), a memória do processo: {bling_id: (expira, valor)}.
_OBS_LOCAL: dict[str, tuple[float, dict]] = {}


def _bling_id(bling_id: Any) -> str | None:
    s = str(bling_id or "").strip()
    return s if s.isdigit() else None


async def _cliente_bling(session: AsyncSession):
    """O cliente do Bling da conta principal (a mesma dos robôs da Margem e da NF).

    Com `integration_id`: se o token vencer no meio, a renovação é gravada
    pelo próprio cliente numa sessão independente (`_persist_bling_creds`) —
    o Bling invalida o refresh token velho, e perder o novo trancaria a conta.
    """
    from app.security.cipher import decrypt_json
    from app.services.marketplaces.bling import BlingClient

    integ = (
        await session.execute(
            select(Integration)
            .where(Integration.platform == IntegrationPlatform.BLING)
            .where(Integration.status == "active")
            .where(Integration.store_id.is_(None))
            .order_by(Integration.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if integ is None:
        return None
    return BlingClient(decrypt_json(integ.credentials), integration_id=integ.id)


async def _ler_cache_obs(chave: str) -> dict | None:
    try:
        from app.redis_client import redis

        bruto = await redis.get(_CHAVE_OBS.format(chave))
        if bruto:
            valor = json.loads(bruto)
            return valor if isinstance(valor, dict) else None
        return None
    except Exception:  # noqa: BLE001 — sem Redis, a memória do processo
        local = _OBS_LOCAL.get(chave)
        if local and local[0] > time.monotonic():
            return local[1]
        return None


async def _gravar_cache_obs(chave: str, valor: dict, ttl: int) -> None:
    _OBS_LOCAL[chave] = (time.monotonic() + ttl, valor)
    try:
        from app.redis_client import redis

        await redis.set(_CHAVE_OBS.format(chave), json.dumps(valor, ensure_ascii=False), ex=ttl)
    except Exception:  # noqa: BLE001, S110 — fica a memória do processo
        pass


async def invalidar_observacoes(bling_id: Any) -> None:
    """Esquece as Observações guardadas do pedido: a próxima leitura vai ao Bling.

    Quem GRAVA na observação do pedido (a linha "TROCA ..." do item 4) chama
    isto logo depois — senão o painel mostraria o texto velho por até 5 min.
    """
    chave = _bling_id(bling_id)
    if chave is None:
        return
    _OBS_LOCAL.pop(chave, None)
    try:
        from app.redis_client import redis

        await redis.delete(_CHAVE_OBS.format(chave))
    except Exception:  # noqa: BLE001, S110 — a memória do processo já foi
        pass


def _erro_bling(e: BaseException) -> tuple[str, str]:
    """(código, frase) da falha ao ler o pedido no Bling — sem corpo de resposta."""
    import httpx

    if isinstance(e, TimeoutError):
        return "bling_demorou", "O Bling demorou para responder — tente de novo em instantes."
    if isinstance(e, httpx.HTTPStatusError):
        st = e.response.status_code
        if st == 404:
            return "bling_nao_achou", "O Bling não achou este pedido (pode ter sido excluído)."
        if st in (401, 403):
            return (
                "bling_sem_acesso",
                "O Bling recusou o acesso — a integração precisa ser reconectada.",
            )
        if st == 429:
            return (
                "bling_limite",
                "O Bling pediu para esperar (muitas consultas agora) — tente de novo em 1 minuto.",
            )
        return "bling_erro", f"O Bling respondeu com erro ({st}) — tente de novo em instantes."
    nome = type(e).__name__
    if "Cloudflare" in nome or "cooldown" in str(e):
        return (
            "bling_limite",
            "O Bling está barrando consultas agora — tente de novo em alguns minutos.",
        )
    if isinstance(e, httpx.HTTPError):
        return "bling_sem_conexao", "Sem conexão com o Bling agora — tente de novo em instantes."
    return "bling_erro", "Não consegui ler o pedido no Bling agora."


def _texto(valor: Any) -> str | None:
    if not isinstance(valor, str):
        return None
    limpo = valor.strip()
    return limpo or None


async def observacoes_bling(
    bling_id: Any, *, session: AsyncSession | None = None, forcar: bool = False
) -> dict:
    """As Observações do pedido no Bling: GET ao vivo, memória de 5 min, SÓ LEITURA.

    `{observacoes, observacoes_internas, lido_em, do_cache, erro, codigo}`.
    Nunca levanta: falha vira `erro` (frase para a tela) + `codigo`, e fica
    1 min na memória para não martelar o Bling. `forcar=True` pula a memória
    (o "atualizar" do painel). `session` opcional (só para achar a
    integração do Bling); sem ela, abre uma.
    """
    chave = _bling_id(bling_id)
    if chave is None:
        return {
            "observacoes": None,
            "observacoes_internas": None,
            "lido_em": None,
            "do_cache": False,
            "erro": "Pedido sem id do Bling — não dá para ler as Observações.",
            "codigo": "sem_bling_id",
        }
    if not forcar:
        guardado = await _ler_cache_obs(chave)
        if guardado is not None:
            return {**guardado, "do_cache": True}

    async def _ler(s: AsyncSession) -> dict:
        # A busca da integração num SAVEPOINT: um erro de banco aqui não pode
        # deixar a transação de quem chamou (o painel) quebrada. O GET ao
        # Bling vem depois, fora dele.
        async with s.begin_nested():
            cliente = await _cliente_bling(s)
        if cliente is None:
            return {
                "observacoes": None,
                "observacoes_internas": None,
                "lido_em": None,
                "erro": "Não há integração do Bling ativa no DaVinci.",
                "codigo": "sem_integracao_bling",
            }
        pedido = await asyncio.wait_for(
            cliente.get_order(int(chave)), timeout=OBSERVACOES_TEMPO_MAXIMO_S
        )
        pedido = pedido if isinstance(pedido, dict) else {}
        return {
            "observacoes": _texto(pedido.get("observacoes")),
            "observacoes_internas": _texto(pedido.get("observacoesInternas")),
            "lido_em": datetime.now(UTC).isoformat(),
            "erro": None,
            "codigo": None,
        }

    try:
        if session is not None:
            valor = await _ler(session)
        else:
            async with _db.SessionLocal() as s:
                valor = await _ler(s)
    except Exception as e:  # noqa: BLE001 — o painel nunca cai por causa do Bling
        codigo, frase = _erro_bling(e)
        logger.warning("atendimento_obs_bling_falhou", bling_id=chave, codigo=codigo)
        valor = {
            "observacoes": None,
            "observacoes_internas": None,
            "lido_em": None,
            "erro": frase,
            "codigo": codigo,
        }
        await _gravar_cache_obs(chave, valor, OBSERVACOES_TTL_ERRO_S)
        return {**valor, "do_cache": False}
    await _gravar_cache_obs(
        chave, valor, OBSERVACOES_TTL_S if valor.get("erro") is None else OBSERVACOES_TTL_ERRO_S
    )
    return {**valor, "do_cache": False}


# ── Links ─────────────────────────────────────────────────────────────────
# Nº que entra numa URL: só letra, dígito e hífen (`conversa.dados` e o
# espelho são texto livre — nada que mexa no resto da URL).
_RE_NUMERO_URL = re.compile(r"^[A-Za-z0-9-]{3,64}$")
# A tela do pedido no Bling (o id interno do pedido, `bling_orders.bling_id`).
URL_PEDIDO_BLING = "https://www.bling.com.br/vendas.php#edit/{}"
# As mesmas páginas da Logística (`vigia_importacao.Pedido.link`). A Shopee
# não está aqui: a página do pedido só abre com o order_id INTERNO, que vem
# de `links_shopee` (sem ele, a busca pelo order_sn).
URL_PEDIDO_PLATAFORMA = {
    "ml": "https://www.mercadolivre.com.br/vendas/{}/detalhe",
    "tiktok": "https://seller-br.tiktok.com/order/detail?order_no={}",
}
_PLATAFORMAS_COM_LINK = ("ml", "shopee", "tiktok")
_ROTULO_PLATAFORMA = {
    "ml": "Abrir no Mercado Livre",
    "shopee": "Abrir na Shopee",
    "tiktok": "Abrir no TikTok",
}


def link_bling(bling_id: Any) -> str | None:
    chave = _bling_id(bling_id)
    return URL_PEDIDO_BLING.format(chave) if chave else None


def _numero_seguro(valor: Any) -> str | None:
    s = str(valor or "").strip()
    return s if _RE_NUMERO_URL.match(s) else None


def numero_na_plataforma(conversa: AtendimentoConversa, pedido: PedidoBling | None) -> str | None:
    """O nº do pedido que vai no "Abrir na plataforma" (só nº limpo), ou None.

    ML: o pack (pedido de carrinho) quando a conversa tem; senão o pedido.
    Shopee e TikTok: o pedido da conversa, senão o numeroLoja do Bling.
    """
    if conversa.plataforma not in _PLATAFORMAS_COM_LINK:
        return None
    dados = conversa.dados if isinstance(conversa.dados, dict) else {}
    candidatos = [conversa.pedido_marketplace]
    if conversa.plataforma == "ml":
        candidatos = [dados.get("pack_id"), conversa.pedido_marketplace, dados.get("order_id")]
    if pedido is not None:
        candidatos.append(pedido.numeroloja)
    return next((n for n in (_numero_seguro(c) for c in candidatos) if n), None)


def links_do_pedido(
    conversa: AtendimentoConversa,
    pedido: PedidoBling | None,
    ids_shopee: links_shopee.IdsShopee | None = None,
) -> dict:
    """`{bling, plataforma: {url, rotulo} | None}` — só https e só nº limpo.

    Shopee (`links_shopee`): a página do pedido só com o order_id interno
    achado com segurança (`ids_shopee`); sem ele, a lista de pedidos
    buscando o order_sn. Amazon, Magalu, Temu e AliExpress já têm o botão na
    conversa (o caso no Seller Central, o portal, o Seller Center): aqui não
    repete.
    """
    plataforma = None
    numero = numero_na_plataforma(conversa, pedido)
    if numero:
        if conversa.plataforma == "shopee":
            order_id = ids_shopee.pedido(conversa.integration_id, numero) if ids_shopee else None
            url = links_shopee.url_pedido_shopee(numero, order_id)
        else:
            url = URL_PEDIDO_PLATAFORMA[conversa.plataforma].format(numero)
        if url:
            plataforma = {"url": url, "rotulo": _ROTULO_PLATAFORMA[conversa.plataforma]}
    return {
        "bling": link_bling(pedido.bling_id) if pedido is not None else None,
        "plataforma": plataforma,
    }


async def ids_shopee_do_painel(
    session: AsyncSession, conversa: AtendimentoConversa, pedido: PedidoBling | None
) -> links_shopee.IdsShopee | None:
    """O order_id interno do pedido da conversa Shopee (uma consulta), ou None."""
    numero = numero_na_plataforma(conversa, pedido)
    if conversa.plataforma != "shopee" or not numero:
        return None
    return await links_shopee.ids_shopee(session, pedidos=[(conversa.integration_id, numero)])


# ── AdsPower (RF11) ───────────────────────────────────────────────────────
# Espelho dos perfis do AdsPower (tabela sem modelo, alimentada pelo
# adspower_sync; `routers/adspower_agent.py`). Só id/nº/nome — a tabela tem
# senha de proxy, que NUNCA se lê aqui.
_ESPELHO_ADSPOWER = "{}.adspower"

MOTIVO_SEM_PERFIL = (
    "Esta loja não tem perfil do AdsPower cadastrado: preencha o campo Servidor "
    "da loja em Lojas (store-info)."
)
MOTIVO_SEM_CADASTRO = (
    "Esta loja não está no cadastro de Lojas (store-info) — sem ele não se sabe "
    "qual perfil do AdsPower abrir. Cadastre a loja (ou ligue a integração a ela)."
)
MOTIVO_AMBIGUO = (
    "Mais de uma loja do cadastro (store-info) bate com esta conta — ligue a "
    "integração à loja certa no cadastro."
)
AVISO_PERFIL_DO_ROBO = (
    "Este é o perfil que o robô do Mac mini usa para ler o chat desta loja. "
    'Abrir em outro computador pode dar "perfil em uso" — e fechar o '
    "navegador dele derruba a leitura até o robô abrir de novo."
)


def _sem_espaco(valor: str | None) -> str:
    return "".join((valor or "").split()).lower()


async def _perfil_no_espelho(
    session: AsyncSession, *, serial: str | None = None, perfil_id: str | None = None
) -> dict | None:
    """{id, serial, nome} do perfil no espelho do AdsPower; None sem ele (ou sem espelho)."""
    if not serial and not perfil_id:
        return None
    tabela = _ESPELHO_ADSPOWER.format(get_settings().database_schema)
    cond = "profile_no = :v" if serial else "id = :v"
    try:
        async with session.begin_nested():
            linha = (
                await session.execute(
                    text(f"SELECT id, profile_no, name FROM {tabela} WHERE {cond} LIMIT 1"),  # noqa: S608
                    {"v": serial or perfil_id},
                )
            ).first()
    except Exception:  # noqa: BLE001 — sem espelho (outro ambiente): não se confere
        return None
    if linha is None:
        return {}
    return {"id": linha.id, "serial": linha.profile_no, "nome": linha.name}


async def _lojas_do_cadastro(
    session: AsyncSession, plataforma: str, nomes: list[str]
) -> list[StoreInfo]:
    alvo = {_sem_espaco(n) for n in nomes if _sem_espaco(n)}
    if not alvo:
        return []
    linhas = (
        (await session.execute(select(StoreInfo).where(StoreInfo.archived_at.is_(None))))
        .scalars()
        .all()
    )
    return [
        s
        for s in linhas
        if normalizar_plataforma(s.platform) == plataforma and _sem_espaco(s.account_name) in alvo
    ]


async def perfil_adspower(session: AsyncSession, conversa: AtendimentoConversa) -> dict:
    """Qual perfil do AdsPower abre a loja desta conversa (campo Servidor do store-info).

    Cada linha do store-info é UMA conta numa plataforma (a KIA do ML e a KIA
    da Amazon têm perfis diferentes). A ligação conversa → linha, em ordem:
      1. `store_info.integration_id` = a integração da conversa;
      2. mesma plataforma + nome da conta (`account_name`, sem espaço e
         minúsculo) = nome da integração — ou o nome da loja que a caixa mostra;
      3. Temu/AliExpress (robô do Mac mini, sem integração): o perfil do
         próprio robô (`atendimento_canais.robo_perfil_id`) e, para o nº, a
         loja do cadastro com o nome que o robô manda.
    Devolve `{perfil, perfil_id, perfil_nome, loja, store_info_id, fonte,
    robo, aviso, motivo, codigo, no_espelho}`; sem perfil, `perfil` None e o
    `motivo` (a frase do botão desativado).
    """
    plataforma = conversa.plataforma
    saida: dict[str, Any] = {
        "perfil": None,
        "perfil_id": None,
        "perfil_nome": None,
        "loja": conversa.conta,
        "store_info_id": None,
        "fonte": None,
        "robo": False,
        "aviso": None,
        "motivo": None,
        "codigo": None,
        "no_espelho": None,
    }
    lojas: list[StoreInfo] = []
    perfil_id = None
    if plataforma in PLATAFORMAS_ROBO:
        canal = (
            await session.get(AtendimentoCanal, conversa.canal_id)
            if conversa.canal_id is not None
            else None
        )
        perfil_id = (canal.robo_perfil_id or "").strip() or None if canal is not None else None
        saida["robo"] = perfil_id is not None
        saida["aviso"] = AVISO_PERFIL_DO_ROBO if perfil_id else None
        if canal is not None:
            from app.services.atendimento import robo

            lojas = await _lojas_do_cadastro(
                session, plataforma, [robo.nome_da_loja(canal), conversa.conta or ""]
            )
            saida["fonte"] = "robo"
    elif conversa.integration_id is not None:
        lojas = list(
            (
                await session.execute(
                    select(StoreInfo).where(
                        StoreInfo.integration_id == conversa.integration_id,
                        StoreInfo.archived_at.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        saida["fonte"] = "integracao"
        if not lojas:
            integ = await session.get(Integration, conversa.integration_id)
            nomes = [integ.name if integ is not None else "", conversa.conta or ""]
            lojas = await _lojas_do_cadastro(session, plataforma, nomes)
            saida["fonte"] = "nome"

    if len(lojas) > 1:
        # Duas linhas do cadastro para a mesma conta: nunca chuta o perfil.
        com_servidor = {(s.server or "").strip() for s in lojas if (s.server or "").strip()}
        if len(com_servidor) > 1:
            saida.update(motivo=MOTIVO_AMBIGUO, codigo="ambiguo")
            return saida
        lojas = sorted(lojas, key=lambda s: not (s.server or "").strip())
    loja = lojas[0] if lojas else None
    if loja is not None:
        saida["store_info_id"] = str(loja.id)
        # O nome que a caixa mostra ("Marquezini") vale mais que o do cadastro ("mega").
        saida["loja"] = saida["loja"] or loja.account_name
    serial = (loja.server or "").strip() if loja is not None else ""
    if serial:
        saida["perfil"] = serial
    if not serial and not perfil_id:
        if loja is None and plataforma not in PLATAFORMAS_ROBO:
            saida.update(motivo=MOTIVO_SEM_CADASTRO, codigo="sem_cadastro")
        else:
            saida.update(motivo=MOTIVO_SEM_PERFIL, codigo="sem_perfil")
        return saida
    espelho = await _perfil_no_espelho(
        session, serial=serial or None, perfil_id=None if serial else perfil_id
    )
    if espelho is None:
        saida["no_espelho"] = None  # sem espelho para conferir
    elif not espelho:
        saida["no_espelho"] = False
        saida["aviso"] = saida["aviso"] or (
            f"O nº {serial or perfil_id} não aparece na lista de perfis do AdsPower que o "
            "DaVinci conhece — se não abrir, confira o campo Servidor da loja."
        )
    else:
        saida["no_espelho"] = True
        saida["perfil_nome"] = espelho.get("nome")
        saida["perfil_id"] = espelho.get("id")
        if not saida["perfil"]:
            saida["perfil"] = str(espelho.get("serial") or "") or None
    if perfil_id and not saida["perfil_id"]:
        saida["perfil_id"] = perfil_id
    return saida


# ── Foto (a situação do botão) ────────────────────────────────────────────


async def situacao_da_foto(session: AsyncSession, conversa: AtendimentoConversa) -> dict:
    """Se o botão de foto pode enviar AGORA, e o porquê quando não pode.

    As mesmas travas do envio de texto (`enviar.motivo_para_nao_enviar`) +
    a plataforma/caixa aceitar foto (`foto.motivo_sem_foto`).
    """
    from app.services.atendimento import enviar, foto

    base = {
        "tipos": sorted(foto.TIPOS_ACEITOS),
        "max_bytes": foto.MAX_BYTES_UPLOAD,
        "legenda_obrigatoria": foto.legenda_obrigatoria(conversa),
        "legenda_permitida": foto.legenda_permitida(conversa),
    }
    motivo = foto.motivo_sem_foto(conversa)
    if motivo:
        return {**base, "pode": False, "motivo": motivo, "codigo": enviar.RECUSA_FOTO_NAO_SUPORTADA}
    recusa = await enviar.motivo_para_nao_enviar(session, conversa)
    if recusa is not None:
        return {**base, "pode": False, "motivo": str(recusa.detail), "codigo": recusa.code}
    return {**base, "pode": True, "motivo": None, "codigo": None}


# ── Aguardando Cancelamento (item 4) ──────────────────────────────────────
# O título do cartão por motivo (`ag_cancelamento.classificar`); o texto é o
# `texto_interno` do motivo, e a tela pinta pelo código.
TITULO_AG_CANCELAMENTO = {
    MARGEM_TRAVA: "Trava interna da Margem",
    MARGEM_REPROVADA: "Reprovado na Margem",
    PEDIDO_CLIENTE: "Cancelamento pedido pelo comprador",
    CANCELADO_PLATAFORMA: "Cancelado na plataforma",
    SEM_ESTOQUE: "Falta de estoque",
    RESTRICAO_ENVIO: "Restrição de envio",
    POS_NF_MANUAL: "Motivo não registrado",
    MANUAL: "Motivo não registrado",
    DESCONHECIDO: "Motivo não conferido",
}
# Só no "movido à mão" a 1ª linha das Observações do Bling entra no cartão:
# é onde a equipe costuma escrever o porquê.
_COM_OBSERVACAO_TOPO = frozenset({POS_NF_MANUAL, MANUAL})
# O recado do robô da Margem nas Observações ("02/10 - Margem DaVinci: pedido
# segurado…", `margem_auto_hold._mensagem` via `compose_observacoes`): não é
# o porquê da equipe — no "movido à mão" leria como trava da Margem (ex.: o
# recado que ficou de um hold antigo, ou do PATCH que voltou "já estava").
_RECADO_DA_MARGEM = re.compile(r"^\d{2}/\d{2}\s*-\s*Margem DaVinci:")


def primeira_linha(texto: str | None) -> str | None:
    """A 1ª linha não vazia das Observações do Bling que não é recado do robô da Margem (<= 300)."""
    for linha in (texto or "").splitlines():
        linha = " ".join(linha.split())
        if linha and not _RECADO_DA_MARGEM.match(linha):
            return cortar(linha)
    return None


def ag_cancelamento_do_pedido(
    pedido: PedidoBling | None,
    conversa: AtendimentoConversa,
    observacoes: dict | None,
    *,
    ve_margem: bool = True,
) -> dict | None:
    """O bloco "Aguardando Cancelamento" do painel — None fora de 83955. PURA.

    O motivo é o do classificador, com o pedido que o painel já leu
    (`pedido_bling_da_conversa` traz, em 83955, a trilha da Margem e os
    fatos da NF) e o status do pedido na plataforma pelo retrato da conversa
    (o comprador pediu?). Nenhuma consulta e nenhum GET a mais: as
    Observações são as que o painel já buscou. `troca_aberta` vem depois
    (`painel_da_conversa`, que lê a troca de produto aberta do pedido).
    `ve_margem` False = o motivo da Margem mascarado (`mascarar_motivo`).
    """
    m = classificar(pedido, status_plataforma=status_na_plataforma(conversa.dados))
    if m is None:
        return None
    obs = (observacoes or {}).get("observacoes") if m.codigo in _COM_OBSERVACAO_TOPO else None
    return bloco_do_motivo(m, observacao_topo=primeira_linha(obs), ve_margem=ve_margem)


# Quem NÃO vê a Margem (07/10/2026: a caixa abriu para toda a equipe em só
# leitura — `acesso.pode_ver`): o motivo da Margem (a trava do robô e a
# reprovação) não diz que é da Margem nem traz o porquê; vira "em análise".
# Os sinais de comportamento (`etiqueta`, `fala_cancelamento`) ficam: a
# tela e a IA seguem iguais. Não é código do classificador (como o
# DESCONHECIDO): o título fica fora de TITULO_AG_CANCELAMENTO.
EM_ANALISE = "em_analise"
TITULO_EM_ANALISE = "Em análise"
TEXTO_EM_ANALISE = "em análise pela equipe"
_MOTIVOS_DA_MARGEM = frozenset({MARGEM_TRAVA, MARGEM_REPROVADA})


def mascarar_motivo(bloco: dict) -> dict:
    """O bloco Ag. cancelamento para quem não vê a Margem. PURA."""
    if bloco.get("codigo") not in _MOTIVOS_DA_MARGEM:
        return bloco
    return {
        **bloco,
        "codigo": EM_ANALISE,
        "titulo": TITULO_EM_ANALISE,
        "texto": TEXTO_EM_ANALISE,
        "skus": [],
        "conflito": None,
        "observacao_topo": None,
    }


def _sem_recado_da_margem(texto: str | None) -> str | None:
    # O recado vem com a data na frente ("02/10 - Margem DaVinci: …",
    # `compose_observacoes`): a linha que o cita sai inteira.
    linhas = [x for x in (texto or "").splitlines() if "margem davinci:" not in x.lower()]
    return "\n".join(linhas).strip() or None


def observacoes_sem_margem(observacoes: dict | None) -> dict | None:
    """As Observações do Bling para quem NÃO vê a Margem: sem o recado do robô. PURA.

    O robô da Margem escreve o porquê nas Observações do pedido ("dd/mm -
    Margem DaVinci: pedido reprovado automaticamente (margem abaixo do
    mínimo)…", `margem_auto_hold._mensagem`): mostrar o bloco cru furaria a
    máscara do cartão (`mascarar_motivo`). Só as linhas do recado saem; o
    resto (a equipe, a troca, o robô de lote) fica.
    """
    if not observacoes:
        return observacoes
    return {
        **observacoes,
        "observacoes": _sem_recado_da_margem(observacoes.get("observacoes")),
        "observacoes_internas": _sem_recado_da_margem(observacoes.get("observacoes_internas")),
    }


def bloco_do_motivo(
    m: Motivo, *, observacao_topo: str | None = None, ve_margem: bool = True
) -> dict:
    """O `AgCancelamentoOut` de um motivo — o do painel e o da lista Ag. cancelamento. PURA.

    `ve_margem` False mascara o motivo da Margem (`mascarar_motivo`).
    """
    bloco = {
        "codigo": m.codigo,
        "titulo": TITULO_AG_CANCELAMENTO.get(m.codigo, NOME_AGUARDANDO_CANCELAMENTO),
        "texto": m.texto_interno,
        "etiqueta": m.etiqueta,
        "fala_cancelamento": m.fala_cancelamento,
        "pode_sugerir_troca": m.pode_sugerir_troca,
        "skus": list(m.skus),
        "conflito": m.conflito,
        "observacao_topo": observacao_topo,
        "troca_aberta": None,
    }
    return bloco if ve_margem else mascarar_motivo(bloco)


async def troca_aberta(session: AsyncSession, numero: str) -> dict | None:
    """A troca de produto ABERTA do pedido (fase 4c), o resumo sem custo; None sem ela."""
    # Import tardio: a troca usa o painel (nota, Observações, cliente do Bling).
    from app.services.atendimento import troca

    return await troca.troca_aberta_do_pedido(session, numero)


def _ag_cancelamento_desconhecido() -> dict:
    """O bloco quando a classificação falhou: o lado seguro, sem falar em cancelamento.

    `etiqueta` vai False, mas NÃO foi conferida (a etiqueta da conversa pode
    estar ligada): a tela não lê o campo.
    """
    return {
        "codigo": DESCONHECIDO,
        "titulo": TITULO_AG_CANCELAMENTO[DESCONHECIDO],
        "texto": "não consegui conferir o motivo agora: não fale em cancelamento antes de conferir",
        "etiqueta": False,
        "fala_cancelamento": False,
        "pode_sugerir_troca": False,
        "skus": [],
        "conflito": None,
        "observacao_topo": None,
        "troca_aberta": None,
    }


# ── O painel inteiro ──────────────────────────────────────────────────────


async def _nome_da_situacao(session: AsyncSession, situacao: str | None) -> str | None:
    if not situacao or not str(situacao).isdigit():
        return situacao
    nome = await session.scalar(select(SituacaoBling.nome).where(SituacaoBling.id == int(situacao)))
    return nome or situacao


async def painel_da_conversa(
    session: AsyncSession,
    conversa: AtendimentoConversa,
    *,
    user: User | None,
    forcar_observacoes: bool = False,
) -> dict:
    """O GET /conversas/{id}/painel: pedido, estoque, margem, observações, links, AdsPower, foto.

    E, com o pedido em "Aguardando Cancelamento", o porquê
    (`ag_cancelamento_do_pedido`, item 4) e, na falta de estoque com a chave
    ligada, as sugestões de troca (`troca_sugestoes`, fase 4b).

    Cada bloco roda sozinho (SAVEPOINT): o que falhar volta vazio com
    `falhou=True` no bloco, e o resto do painel aparece. Nada aqui escreve
    (a não ser a renovação do token do Bling, que o próprio cliente grava).
    """
    cid = conversa.id
    pedido, _ = await _seguro(
        session, "pedido", lambda: pedido_bling_da_conversa(session, conversa), None, cid
    )
    situacao_nome = None
    if pedido is not None:
        situacao_nome, _ = await _seguro(
            session,
            "situacao",
            lambda: _nome_da_situacao(session, pedido.situacao),
            pedido.situacao,
            cid,
        )

    (itens, fonte_itens), ok_itens = await _seguro(
        session, "itens", lambda: _itens_do_pedido(session, pedido, conversa), ([], "nenhum"), cid
    )
    estoque = []
    for item in itens:
        saldo, ok = await _seguro(
            session,
            "estoque",
            lambda item=item: saldo_do_item(session, item["sku"], quantidade=item["quantidade"]),
            None,
            cid,
        )
        estoque.append(
            {
                **(saldo or {"sku": item["sku"], "existe": None}),
                "descricao": item["descricao"],
                "falhou": not ok,
            }
        )

    margem = None
    margem_falhou = False
    if pedido is not None and ve_margem(user):
        margem, ok = await _seguro(
            session,
            "margem",
            lambda: margem_do_pedido(session, pedido, ver_lucro=ve_lucro(user)),
            None,
            cid,
        )
        margem_falhou = not ok

    observacoes = None
    if pedido is not None:
        observacoes = await observacoes_bling(
            pedido.bling_id, session=session, forcar=forcar_observacoes
        )

    # Aguardando Cancelamento (item 4): o porquê, com o que já foi lido.
    ag_cancelamento = None
    if pedido is not None:
        try:
            ag_cancelamento = ag_cancelamento_do_pedido(
                pedido, conversa, observacoes, ve_margem=ve_margem(user)
            )
        except Exception as e:  # noqa: BLE001 — um bloco do painel nunca derruba os outros
            logger.warning(
                "atendimento_painel_falhou",
                etapa="ag_cancelamento",
                conversa_id=str(cid),
                err=type(e).__name__,
            )
            if (pedido.situacao or "").strip() == SITUACAO_AGUARDANDO_CANCELAMENTO_STR:
                ag_cancelamento = _ag_cancelamento_desconhecido()
    # A troca de produto aberta do pedido (fase 4c): num SAVEPOINT — a tabela
    # pode ainda não existir (o deploy do código antes do alembic). Lida em
    # QUALQUER situação: a troca parada no meio (o PATCH 6 que falhou deixa o
    # pedido em 9; o espelho sai de 83955) continua com o Retomar no painel
    # (`troca_aberta` do painel; dentro do bloco quando ainda em 83955).
    aberta = None
    if pedido is not None:
        aberta, _ = await _seguro(
            session, "troca_aberta", lambda: troca_aberta(session, pedido.numero), None, cid
        )
    if ag_cancelamento is not None and pedido is not None:
        ag_cancelamento["troca_aberta"] = aberta
        # Import tardio: a troca e a oferta usam o painel (a nota interna).
        from app.services.atendimento import troca as _troca
        from app.services.atendimento import troca_oferta

        # O "Trocar" (fase 4c) e o "Enviar oferta" (fase 4d): as travas que
        # não dependem do produto, com UMA leitura das do pedido (`memo`).
        memo: dict = {}
        ag_cancelamento["troca_envio"], _ = await _seguro(
            session,
            "troca_envio",
            lambda: _troca.situacao_da_troca(
                session, numero=pedido.numero, motivo=ag_cancelamento, user=user, memo=memo
            ),
            dict(_troca.TROCA_NAO_CONFERIDA),
            cid,
        )
        ag_cancelamento["oferta_envio"], _ = await _seguro(
            session,
            "oferta_envio",
            lambda: troca_oferta.situacao_da_oferta(
                session,
                numero=pedido.numero,
                motivo=ag_cancelamento,
                conversa=conversa,
                user=user,
                memo=memo,
            ),
            dict(troca_oferta.OFERTA_NAO_CONFERIDA),
            cid,
        )

    # Sugestões de troca (item 4, fase 4b): só na falta de estoque, com a
    # chave ligada; do catálogo do DaVinci, sem GET ao Bling.
    sugestoes_troca = None
    if (
        ag_cancelamento is not None
        and ag_cancelamento.get("pode_sugerir_troca")
        and get_settings().atendimento_troca_sugestoes_ativa
    ):
        # Os itens já lidos; se a leitura falhou, os SKUs do espelho (quantidade 1).
        itens_troca = (
            itens if ok_itens else [{"sku": s, "quantidade": None} for s in pedido.skus_itens]
        )
        sugestoes_troca, ok_troca = await _seguro(
            session,
            "troca",
            lambda: sugestoes_do_pedido(
                session, ag_cancelamento["skus"], itens_troca, ve_custo=ve_margem(user)
            ),
            None,
            cid,
        )
        if not ok_troca or sugestoes_troca is None:
            sugestoes_troca = sugestoes_falhou(ve_custo=ve_margem(user))

    if ve_lojas(user):
        adspower, _ = await _seguro(
            session, "adspower", lambda: perfil_adspower(session, conversa), None, cid
        )
    else:
        adspower = dict(SEM_PERMISSAO_LOJAS)
    if adspower is None:
        adspower = {
            "perfil": None,
            "motivo": "Não consegui ler o cadastro da loja agora.",
            "codigo": "falhou",
        }

    envio_foto, _ = await _seguro(
        session, "foto", lambda: situacao_da_foto(session, conversa), None, cid
    )
    # Falhou a leitura do order_id interno: o link sai com a busca pelo order_sn.
    ids_shopee, _ = await _seguro(
        session, "link_shopee", lambda: ids_shopee_do_painel(session, conversa, pedido), None, cid
    )

    observacoes_tela = observacoes if ve_margem(user) else observacoes_sem_margem(observacoes)
    return {
        "pedido": (
            {
                "numero_bling": pedido.numero,
                "bling_id": pedido.bling_id,
                "numeroloja": pedido.numeroloja,
                "situacao_id": pedido.situacao,
                "situacao": situacao_nome,
            }
            if pedido is not None
            else None
        ),
        "estoque": {
            "itens": estoque,
            "fonte": fonte_itens,
            "falhou": not ok_itens,
        },
        "margem": (
            {**margem, "falhou": False}
            if margem is not None
            else ({"falhou": True, "itens": []} if margem_falhou else None)
        ),
        "ve_margem": ve_margem(user),
        # Quem não vê a Margem não lê o recado do robô dela (a máscara do cartão).
        "observacoes_bling": observacoes_tela,
        "links": links_do_pedido(conversa, pedido, ids_shopee),
        "adspower": adspower,
        "envio_foto": envio_foto
        if envio_foto is not None
        else {"pode": False, "motivo": "Não consegui conferir o envio agora.", "codigo": "falhou"},
        "ag_cancelamento": ag_cancelamento,
        "sugestoes_troca": sugestoes_troca,
        "troca_aberta": aberta,
        "gerado_em": datetime.now(UTC).isoformat(),
    }
