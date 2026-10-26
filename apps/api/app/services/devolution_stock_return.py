"""Retorno de produto devolvido ao estoque Bling + patch da situação do pedido.

Disparo (a partir do router): ao ADICIONAR a devolução ou ao ligar o toggle
"devolver estoque". Extraviado patcha a situação já no add (sem estoque);
Novo/Usado/Trocado/Manutenção só agem com o toggle ligado.

Estoque, por condição efetiva:
  Novo / Usado  → o operador escolhe no modal o destino:
                    * `estoque_destino_sku`  — bin já existente `base.<sufixo>`
                      (entrada direta de N unidades nesse SKU);
                    * `estoque_nova_tag`     — nenhuma variante existe, então
                      cria `z000N.<tag>` (sequencial pelo maior z na tabela
                      products), nome = nome original + " AVULSO" (Novo) /
                      " AVULSO SALVADO" (Usado), clonando a categoria do
                      produto original, com estoque inicial = quantidade.
  Trocado       → o item que volta é `troca_sku` (Novo/Usado em
                  `troca_condicao`); mesma lógica de destino sobre esse SKU.
  Manutenção    → `manutencao_destino` escolhido no modal:
                    * Novo/Usado → mesma lógica de estoque;
                    * Sucata     → não mexe no estoque (patch sucata no pedido).
  Extraviado / Sucata (condição) / outros → não mexe no estoque.

Situação do pedido (valor único no Bling), precedência pior→melhor:
  qualquer Extraviado → 83960; senão qualquer Sucata (CONDIÇÃO, 10/09: "não é
  resolvido nem extraviado, é perdimento") → 83956 Perdimento; senão
  Manutenção pendente → 84677; senão qualquer Trocado OU todos os itens
  resolvidos (Novo/Usado/Manutenção→Sucata) → 545902.
  Obs.: Manutenção com destino Sucata (modal) continua RESOLVIDO (545902) —
  o Bling rejeita a transição pra 545901 "Manutenção - Sucata".
  "Entregue" e "Não devolvido" (legado) são NEUTROS: não contam no cálculo.

Transições diretas no Bling (API v3) podem ser rejeitadas com 400 (ex.:
Resolvido→Extraviado, Resolvido→Manutenção). Na recusa o patch faz um DESVIO
validado (caso real 291700): passa por "Aguardando Devolução" (83957) e
reaplica o alvo. "Mesma situação" (400 cód. 50) conta como sucesso
idempotente, sem desvio. Se até o desvio for recusado (4xx), o operador
recebe um alerta (sino/Telegram) para ajustar a situação manualmente.

Todas as funções públicas de estoque retornam um dict:
  ok: bool, action: str, sku: str|None, bling_product_id: int|None, message: str
(+ `incerto: True` quando o Bling respondeu erro DEPOIS de receber a entrada).

Resultado guardado na linha (alembic 0421, caso 300588 de 15/10/2026):
`estoque_status` ok | falhou | conferindo | sem_confirmacao. "falhou" = o Bling
recusou ou nem recebeu (nada entrou: dá para tentar de novo). "conferindo" =
o Bling respondeu 5xx/timeout depois de receber (casos 300334/298826: GRAVOU e
respondeu 504) — `conferir_entradas_pendentes` procura o movimento no espelho
(stock_movements) e confirma, ou desiste em 10 min ("sem_confirmacao": a
pessoa confere no extrato antes de tentar de novo, senão dobra o estoque).
Enquanto a entrada não está ok, o item NÃO conta como resolvido na situação do
pedido (o pedido não vai para Resolvido com o produto fora do estoque).
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import httpx
import structlog
from sqlalchemy import func, or_, select

from app.config import get_settings
from app.models import (
    BlingOrder,
    Devolution,
    Integration,
    PrioridadeEstoqueMovimento,
    Product,
    StockMovement,
)
from app.models.enums import AlertSeverity, AlertType, IntegrationPlatform
from app.security.cipher import decrypt_json, encrypt_json
from app.services.alerts import emit_alert
from app.services.margem_audit import record_margem_audit
from app.services.marketplaces.bling import BlingClient, BlingCloudflareError
from app.services.sku_tags import SUFFIX_TAGS as _SKU_SUFFIX_TAGS
from app.services.sku_tags import classify_sku_tag

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger()

_STOCK_CONDICOES = {"Novo", "Usado"}
# Condições que disparam retorno de estoque a partir do router. Trocado e
# Manutenção entram porque o destino/condição efetiva vêm do modal.
_STOCK_TRIGGER_CONDICOES = _STOCK_CONDICOES | {"Trocado", "Manutenção"}

# Situações do pedido no Bling (idSituacao).
SITUACAO_RESOLVIDO = 545902
SITUACAO_EXTRAVIADO = 83960
# Condição Sucata (10/09) → Perdimento (mesmo id que o Financeiro usa na Taxa
# de Perdimento, routers/financeiro._VAL_SIT_PERDIMENTO).
SITUACAO_PERDIMENTO = 83956
SITUACAO_MANUTENCAO = 84677
SITUACAO_AGUARDANDO_DEVOLUCAO = 83957
# 545901 ("Manutenção - Sucata") existe mas o Bling rejeita a transição (400);
# por isso Sucata resolve para 545902 (resolvido).

# Condições NEUTRAS no cálculo da situação: não contam nem travam o "todos
# resolvidos". "Entregue" = cliente ficou com o item; "Não devolvido" = legado
# (não existe mais no seletor do front, mas sobrevive em linhas antigas).
_NEUTRAL_CONDICOES = {"Entregue", "Não devolvido"}

_SITUACAO_NOMES = {
    SITUACAO_RESOLVIDO: "Resolvido",
    SITUACAO_EXTRAVIADO: "Extraviado",
    SITUACAO_PERDIMENTO: "Perdimento",
    SITUACAO_MANUTENCAO: "Manutenção",
}

# Sufixos regionais válidos — fonte única em app.services.sku_tags.
_SUFFIX_TAGS = _SKU_SUFFIX_TAGS

# Mala (b+dígito) e Eletro (u…) voltam direto no próprio SKU — sem tag/bin.
_MALA_OR_ELETRO_RE = re.compile(r"^(b[0-9]|u)", re.IGNORECASE)

# Sentinel em `estoque_nova_tag` para "criar z000N sem sufixo" (z0093, não z0093.us).
NOVA_TAG_SEM = "-"

# Resultado da entrada no Bling, guardado na linha (Devolution.estoque_status).
ESTOQUE_OK = "ok"
ESTOQUE_FALHOU = "falhou"
ESTOQUE_CONFERINDO = "conferindo"
ESTOQUE_SEM_CONFIRMACAO = "sem_confirmacao"
# Entrada não confirmada: o item não conta como resolvido no pedido.
ESTOQUE_PENDENTES = frozenset({ESTOQUE_FALHOU, ESTOQUE_CONFERINDO, ESTOQUE_SEM_CONFIRMACAO})
# Quanto a conferência espera o movimento aparecer no espelho (stock_movements
# chega por webhook em segundos; 674 de 675 entradas registradas desde 16/09
# apareceram em até 3 min).
CONFERIR_JANELA = timedelta(minutes=10)
# Folga antes da tentativa: o relógio do webhook e o do request não batem ao ms.
_CONFERIR_FOLGA = timedelta(seconds=5)
# Janela em que a entrada de UMA tentativa aparece no espelho (o POST leva até
# ~1 min e o webhook chega em segundos). Curta de propósito: uma "Correção de
# estoque" ou entrada manual no mesmo SKU minutos depois não pode confirmar a
# devolução por engano (checagem de 19/10).
_CANDIDATO_JANELA = timedelta(minutes=3)


# Recusa em que o Bling com certeza NÃO gravou (excesso de chamadas, Cloudflare,
# conexão que nem abriu): o worker tenta de novo sozinho (21/10, caso 297390 —
# o 429 das 09:15 fez a thatcher excluir e lançar de novo à mão).
ESTOQUE_AUTO = "auto_retry"
AUTO_MAX = 5
# Espera antes de cada tentativa: 1,5 / 3 / 6 / 12 / 24 min (dobra a cada
# vez) — cobre ~45 min de Bling ocupado/bloqueado, não só 10 (revisão 21/10).
_AUTO_INTERVALO = timedelta(seconds=90)
# Chave que o worker usa para reaplicar a situação do pedido quando o Bling
# recusou o PATCH de situação por excesso de chamadas/rede (revisão 21/10).
SITUACAO_PENDENTE_KEY = "devolucao:situacao_pendente"


def _falha_sem_envio(exc: Exception) -> bool:
    if isinstance(exc, RuntimeError) and str(exc).startswith("bling_cf_cooldown_active"):
        return True  # renovação do token em espera do Cloudflare: nada foi enviado
    return isinstance(
        exc, BlingCloudflareError | httpx.ConnectError | httpx.ConnectTimeout | httpx.PoolTimeout
    )


def _situacao_transitoria(exc: Exception) -> bool:
    """Erro do PATCH de situação que não é "transição recusada" (excesso de
    chamadas, 5xx, rede): não faz o desvio por Aguardando Devolução — tenta de
    novo depois."""
    if _falha_sem_envio(exc):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code >= 500
    return isinstance(exc, httpx.TransportError)


def status_estoque(row: Any) -> str | None:
    """Status efetivo da entrada: linha de antes da 0421 com movimento
    registrado conta como ok."""
    st = getattr(row, "estoque_status", None)
    if (
        st is None
        and getattr(row, "estoque_mov_bling_id", None) is not None
        and getattr(row, "estoque_mov_revertido_at", None) is None
    ):
        return ESTOQUE_OK
    return st


class EntradaIncertaError(Exception):
    """O POST /estoques saiu e o Bling respondeu erro DEPOIS de receber (5xx,
    timeout de leitura, conexão caída na resposta). A entrada pode ter sido
    gravada — casos 300334/298826 (15/10): 504 com o movimento no Bling."""

    def __init__(self, pid: int, sku: str | None, action: str, causa: Exception):
        self.pid = int(pid)
        self.sku = sku
        self.action = action
        self.causa = causa
        super().__init__(str(causa))


def _do_post_de_estoque(exc: Exception) -> bool:
    """O erro veio do próprio POST /estoques? (o `_request` também renova o
    token antes/depois — um 5xx do /oauth/token acontece ANTES de enviar)."""
    try:
        req = exc.request  # type: ignore[attr-defined]
    except (AttributeError, RuntimeError):
        return False
    return req.method == "POST" and req.url.path.rstrip("/").endswith("/estoques")


def _resposta_incerta(exc: Exception) -> bool:
    """Erro que pode ter acontecido DEPOIS de o Bling gravar. 429/503 não
    entram: o `_request` já tentou 3× e o Bling não processa o que recusa por
    limite (vira BlingCloudflareError). Conexão que nem abriu também não, nem
    erro de outra chamada (renovar o token)."""
    if not _do_post_de_estoque(exc):
        return False
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        return code >= 500 and code != 503
    if isinstance(exc, httpx.ConnectError | httpx.ConnectTimeout | httpx.PoolTimeout):
        return False
    return isinstance(exc, httpx.TimeoutException | httpx.RemoteProtocolError | httpx.ReadError)


def _texto_erro_bling(exc: Exception, *, estorno: bool = False) -> str:
    """Motivo da falha em português, para a linha e o aviso. `estorno` = a
    operação era a SAÍDA que desfaz uma entrada."""
    oque = "o estorno (saída)" if estorno else "a entrada"
    nada = "O estoque continua lá." if estorno else "Nada entrou no estoque."
    if isinstance(exc, BlingCloudflareError):
        return f"O Bling estava sobrecarregado (limite de chamadas) e recusou {oque}. {nada}"
    if isinstance(exc, httpx.ConnectError | httpx.ConnectTimeout | httpx.PoolTimeout):
        return f"Não deu para falar com o Bling (conexão). {nada}"
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        detalhe = ""
        try:
            err = (exc.response.json() or {}).get("error") or {}
            detalhe = str(err.get("description") or err.get("message") or "").strip()
        except Exception:  # noqa: BLE001
            detalhe = ""
        base = f"O Bling recusou {oque} (HTTP {code})"
        return f"{base}: {detalhe[:200]}" if detalhe else f"{base}."
    return f"Erro no Bling: {str(exc)[:200]}"


async def _lancar_entrada(
    client: BlingClient,
    pid: int,
    *,
    qty: int,
    obs: str,
    cost: float | None,
    sku: str | None,
    action: str,
) -> None:
    """POST /estoques de entrada. Erro depois de o Bling receber vira
    EntradaIncertaError (a conferência decide); o resto sobe como está."""
    try:
        await client.update_stock_by_id(pid, qty=qty, operation="E", observacao=obs, custo=cost)
    except Exception as exc:
        if _resposta_incerta(exc):
            raise EntradaIncertaError(pid, sku, action, exc) from exc
        raise


class ProdutoCriadoSemEntradaError(Exception):
    """O z000N foi criado no Bling e a ENTRADA falhou (com certeza). A nova
    tentativa tem que lançar NESSE produto — senão nasce outro z e o primeiro
    fica órfão (revisão de 15/10)."""

    def __init__(self, pid: int, sku: str, causa: Exception):
        self.pid = int(pid)
        self.sku = sku
        self.causa = causa
        super().__init__(str(causa))


async def _entrada_em_produto_criado(
    client: BlingClient, pid: int, *, qty: int, obs: str, cost: float | None,
    sku: str, action: str,
) -> None:
    try:
        await _lancar_entrada(client, pid, qty=qty, obs=obs, cost=cost, sku=sku, action=action)
    except EntradaIncertaError:
        raise
    except Exception as exc:
        raise ProdutoCriadoSemEntradaError(pid, sku, exc) from exc


def _is_mala_or_eletro(sku: str | None) -> bool:
    return bool(_MALA_OR_ELETRO_RE.match((sku or "").strip()))

type StockResult = dict[str, Any]


def _sku_base(sku: str) -> str:
    """Remove o sufixo regional conhecido do SKU, retornando a base.
    `x001.sp` → `x001`; `x001` → `x001`."""
    if "." in sku:
        head, tail = sku.rsplit(".", 1)
        if tail.lower() in _SUFFIX_TAGS:
            return head
    return sku


def _sku_tag(sku: str | None) -> str | None:
    """Tag de operador do SKU (sem ponto), espelhando o Controle de Estoque."""
    return classify_sku_tag(sku)


def _result(
    ok: bool,
    action: str,
    *,
    sku: str | None = None,
    bling_product_id: int | None = None,
    message: str = "",
) -> StockResult:
    return {
        "ok": ok,
        "action": action,
        "sku": sku,
        "bling_product_id": bling_product_id,
        "message": message,
    }


async def _get_bling_client(session: AsyncSession) -> BlingClient | None:
    integ = (
        await session.execute(
            select(Integration)
            .where(Integration.platform == IntegrationPlatform.BLING)
            .limit(1)
        )
    ).scalar_one_or_none()
    if integ is None:
        return None
    creds = decrypt_json(integ.credentials)

    async def _persist(new_creds: dict) -> None:
        integ.credentials = encrypt_json(new_creds)
        exp = new_creds.get("expires_at")
        if exp:
            integ.token_expires_at = datetime.fromtimestamp(int(exp), tz=UTC)
        await session.commit()

    return BlingClient(creds, on_token_refresh=_persist, integration_id=integ.id)


# ── Situação do pedido ───────────────────────────────────────────────────

def _resolution_of(row: Devolution) -> str:
    """Classifica a 'resolução' do item:
    extraviado | perdimento | manutencao (pendente) | resolvido | unresolved.

    Manutenção→Sucata resolve como 'resolvido' (o pedido vai para 545902 —
    o Bling rejeita a situação 545901 "Sucata" na transição). Item cuja entrada
    no estoque não foi confirmada (falhou/conferindo/sem_confirmacao) NÃO é
    resolvido: caso 300588, o pedido foi para Resolvido com a mala fora do
    estoque."""
    res = _resolution_by_condicao(row)
    if res == "resolvido" and status_estoque(row) in ESTOQUE_PENDENTES:
        return "unresolved"
    return res


# Pendência de entrada que puxa o pedido de volta para Aguardando Devolução
# (pedido de 2 linhas: a 1ª entrou e mandou Resolvido, a 2ª falhou). Só a de
# pedido de entrada recente: devolução antiga que alguém reprocessa e falha só
# deixa de contar como resolvida — o pedido não reabre (re-carimbaria o "em
# devolução desde" e voltaria ao Acompanhamento).
_PUXA_DE_VOLTA_ATE = timedelta(days=3)


def _pendente_do_backfill(row: Any) -> bool:
    return (
        status_estoque(row) == ESTOQUE_SEM_CONFIRMACAO
        and getattr(row, "estoque_tentativa_at", None) is None
    )


def _entrada_pendente_nova(row: Any) -> bool:
    if status_estoque(row) not in ESTOQUE_PENDENTES or _pendente_do_backfill(row):
        return False
    # Idade da DEVOLUÇÃO (created_at), não do carimbo da entrada: ligar o
    # "devolver estoque" semanas depois (Manutenção que volta) re-carimba a data.
    criada_em = getattr(row, "created_at", None)
    return criada_em is None or datetime.now(UTC) - criada_em <= _PUXA_DE_VOLTA_ATE


def _resolution_by_condicao(row: Devolution) -> str:
    c = (row.condicao_produto or "").strip()
    if c == "Extraviado":
        return "extraviado"
    # Sucata como CONDIÇÃO (10/09) → Perdimento. Não confundir com
    # Manutenção→destino Sucata (modal), que resolve o pedido.
    if c == "Sucata":
        return "perdimento"
    if c == "Manutenção":
        d = (row.manutencao_destino or "").strip()
        if d in _STOCK_CONDICOES or d == "Sucata":
            return "resolvido"
        return "manutencao"  # ainda em manutenção (destino não escolhido)
    if c in ("Novo", "Usado", "Trocado"):
        return "resolvido"
    return "unresolved"


def _order_situacao_target(rows: list[Devolution]) -> int | None:
    """Situação única do pedido pela precedência pior→melhor.

    Extraviado > Perdimento (Sucata) > Manutenção(pendente) > Trocado/Resolvido. "Resolvido" por
    Novo/Usado/Sucata só quando TODOS os itens estão resolvidos; um Trocado
    força resolvido. Manutenção pendente mantém o pedido "em manutenção" até o
    técnico escolher Novo/Usado/Sucata.

    "Entregue" e "Não devolvido" (legado) são neutros — ignorados no cálculo;
    se TODOS os itens forem neutros, não há patch.
    """
    rows = [
        r
        for r in rows
        if (r.condicao_produto or "").strip() not in _NEUTRAL_CONDICOES
        # Linha antiga do backfill (0421: nunca tentada por este código) fica
        # fora da conta — não trava nem destrava o pedido (revisão de 15/10).
        and not _pendente_do_backfill(r)
    ]
    if not rows:
        return None
    res = [_resolution_of(r) for r in rows]
    conds = [(r.condicao_produto or "").strip() for r in rows]
    if "extraviado" in res:
        return SITUACAO_EXTRAVIADO
    if "perdimento" in res:
        return SITUACAO_PERDIMENTO
    if "manutencao" in res:
        return SITUACAO_MANUTENCAO
    # Entrada no Bling que falhou / está sendo conferida / tentada e não
    # confirmada: o pedido volta (ou fica) em Aguardando Devolução — mesmo que
    # outra linha já o tenha mandado para Resolvido (pedido de 2 itens: o 1º
    # entra, o 2º leva 429). Linha antiga do backfill (sem tentativa_at) não
    # mexe: só deixa de contar como resolvida.
    if any(_entrada_pendente_nova(r) for r in rows):
        return SITUACAO_AGUARDANDO_DEVOLUCAO
    # Pendência de devolução antiga: o pedido fica onde está (nem Resolvido
    # por um Trocado irmão — checagem de 19/10).
    if any(status_estoque(r) in ESTOQUE_PENDENTES for r in rows):
        return None
    # Trocado força resolvido — só o que teve a entrada confirmada (o item
    # trocado que não entrou no estoque não resolve o pedido).
    if any(
        c == "Trocado" and status_estoque(r) not in ESTOQUE_PENDENTES
        for r, c in zip(rows, conds, strict=True)
    ):
        return SITUACAO_RESOLVIDO
    if all(r == "resolvido" for r in res):
        return SITUACAO_RESOLVIDO
    return None


def _is_same_situacao_error(status: int, body: str) -> bool:
    """Erro 400 código 50 do Bling: "A venda possui a mesma situação".
    Não é falha — o pedido já está onde queríamos (idempotente)."""
    return status == 400 and "mesma situa" in (body or "").lower()


async def mudar_situacao_com_desvio(
    client: Any, bling_id: int, alvo: int, situacao_atual: str | int | None
) -> bool:
    """Muda a situação do pedido no Bling com a regra de DESVIO das mudanças
    feitas por pessoa (endpoint manual da aba Lançamentos e a volta da situação
    ao excluir o lançamento): se o Bling recusar a ida direta (caminhos de mão
    única), passa por "Aguardando Devolução" e reaplica o alvo. "Mesma
    situação" é sucesso. Devolve True quando precisou do desvio; levanta o erro
    do Bling (httpx) quando nem o desvio passou. Não mexe no espelho local."""
    try:
        await client.update_order_situacao(int(bling_id), int(alvo))
        return False
    except httpx.HTTPStatusError as exc:
        if _is_same_situacao_error(exc.response.status_code, exc.response.text or ""):
            return False  # já está no alvo — idempotente
        if (
            str(situacao_atual) == str(SITUACAO_AGUARDANDO_DEVOLUCAO)
            or int(alvo) == SITUACAO_AGUARDANDO_DEVOLUCAO
        ):
            raise
    await client.update_order_situacao(int(bling_id), SITUACAO_AGUARDANDO_DEVOLUCAO)
    await client.update_order_situacao(int(bling_id), int(alvo))
    return True


async def _alert_manual_situacao(
    session: AsyncSession, actor_id: UUID | None, pedido: str, target: int
) -> None:
    """Alerta (sino + Telegram) pedindo ajuste manual da situação no Bling —
    usado quando a API rejeita a transição (grafo de mão única). Best-effort."""
    if actor_id is None:
        return
    nome = _SITUACAO_NOMES.get(int(target), str(target))
    try:
        await emit_alert(
            session,
            user_id=actor_id,
            type=AlertType.GENERIC,
            severity=AlertSeverity.WARNING,
            title=f"Pedido {pedido}: mude a situação no Bling para {nome} (manual)",
            message=(
                f"O Bling não aceitou a mudança pela API (transição bloqueada). "
                f"Abra o pedido {pedido} no Bling e altere a situação para "
                f"{nome} manualmente."
            ),
            dedupe_key=f"devolucao_situacao_manual:{pedido}:{int(target)}",
            payload={"pedido_bling": pedido, "situacao_alvo": int(target)},
        )
    except Exception:  # noqa: BLE001
        logger.warning("devolution_situacao_alert_failed", pedido_bling=pedido)


async def apply_order_situacao(
    session: AsyncSession,
    pedido_bling: str | None,
    *,
    actor_id: UUID | None = None,
) -> StockResult | None:
    """Recalcula e aplica a situação do pedido no Bling a partir de TODOS os
    itens de devolução do mesmo `pedido_bling` (== BlingOrder.numero).
    Best-effort: nunca levanta para o caller.

    `actor_id` é o usuário que disparou a mudança (vindo do router), gravado na
    trilha de auditoria. None = sistema."""
    pedido = (pedido_bling or "").strip()
    if not pedido:
        return None
    rows = (
        await session.execute(select(Devolution).where(Devolution.pedido_bling == pedido))
    ).scalars().all()
    target = _order_situacao_target(list(rows))
    if target is None:
        return None

    # `numero` se repete entre anos — pega o pedido mais recente.
    order_row = (
        await session.execute(
            select(BlingOrder.bling_id, BlingOrder.situacao)
            .where(BlingOrder.numero == pedido, BlingOrder.bling_id.is_not(None))
            .order_by(BlingOrder.data.desc().nullslast())
            .limit(1)
        )
    ).first()
    bling_id = order_row.bling_id if order_row else None
    situacao_antiga = order_row.situacao if order_row else None
    if not bling_id:
        logger.warning("devolution_situacao_no_bling_id", pedido_bling=pedido, target=target)
        return _result(
            False, "situacao_no_bling_id",
            message=f"Pedido {pedido} sem bling_id — situação não atualizada",
        )

    client = await _get_bling_client(session)
    if client is None:
        return _result(False, "no_integration", message="Nenhuma integração Bling encontrada")

    alvo = int(target)
    try:
        try:
            await client.update_order_situacao(int(bling_id), alvo)
            logger.info(
                "devolution_situacao_patched",
                pedido_bling=pedido, bling_id=int(bling_id), situacao=alvo,
            )
        except Exception as exc:  # noqa: BLE001
            if isinstance(exc, httpx.HTTPStatusError) and _is_same_situacao_error(
                exc.response.status_code, exc.response.text or ""
            ):
                # 400 cód. 50 "mesma situação": já está no alvo — sucesso
                # idempotente, sem desvio e sem nova linha de auditoria.
                logger.info(
                    "devolution_situacao_unchanged",
                    pedido_bling=pedido, bling_id=int(bling_id), situacao=alvo,
                )
                return _result(
                    True, "situacao_unchanged",
                    message=f"Pedido {pedido} já estava na situação {alvo}",
                )
            if _situacao_transitoria(exc):
                logger.warning(
                    "devolution_situacao_transitoria",
                    pedido_bling=pedido, situacao=alvo, error=str(exc),
                )
                return _result(False, "situacao_transitoria", message=str(exc))
            # O Bling recusa certas transições diretas com 400 — caso real:
            # pedido 291700 estava "Resolvido" (itens Novo entraram antes) e a
            # ida pra "Problemas" (83960, item Extraviado) foi rejeitada em
            # silêncio. Desvio validado: passa por "Aguardando Devolução" e
            # reaplica o alvo (funciona p/ →Extraviado e →Manutenção).
            if alvo == SITUACAO_AGUARDANDO_DEVOLUCAO:
                logger.error(
                    "devolution_situacao_patch_error",
                    pedido_bling=pedido, situacao=alvo, error=str(exc),
                )
                return _result(False, "situacao_error", message=str(exc))
            try:
                await client.update_order_situacao(
                    int(bling_id), SITUACAO_AGUARDANDO_DEVOLUCAO
                )
                await client.update_order_situacao(int(bling_id), alvo)
                logger.info(
                    "devolution_situacao_patched_via_desvio",
                    pedido_bling=pedido, bling_id=int(bling_id), situacao=alvo,
                    erro_direto=str(exc),
                )
            except Exception as exc2:  # noqa: BLE001
                logger.error(
                    "devolution_situacao_patch_error",
                    pedido_bling=pedido, situacao=alvo,
                    error=f"direto: {exc} | desvio: {exc2}",
                )
                if (
                    isinstance(exc2, httpx.HTTPStatusError)
                    and exc2.response.status_code in (400, 403, 409, 422)
                ):
                    # Nem o desvio passou e não é rede/5xx (retry não ajuda):
                    # avisa o operador p/ ajustar a situação na mão no Bling.
                    await _alert_manual_situacao(session, actor_id, pedido, alvo)
                return _result(False, "situacao_error", message=str(exc2))
        await record_margem_audit(
            session,
            acao="situacao",
            pedido_bling=pedido,
            bling_id=bling_id,
            valor_antigo=situacao_antiga,
            valor_novo=alvo,
            origem="devolucao",
            mudado_por=actor_id,
        )
        return _result(True, "situacao_patched", message=f"Pedido {pedido} → situação {alvo}")
    except Exception as exc:  # noqa: BLE001
        # Cinto de segurança (auditoria/alerta nunca derrubam o caller).
        logger.error(
            "devolution_situacao_patch_error",
            pedido_bling=pedido, situacao=alvo, error=str(exc),
        )
        return _result(False, "situacao_error", message=str(exc))


# ── Registro / estorno do movimento de estoque ──────────────────────────────

def record_stock_movement(row: Devolution, sr: StockResult | None) -> None:
    """Grava em `row` o que foi efetivamente lançado no Bling, pra permitir o
    estorno depois. Só registra entradas reais (ok + produto resolvido); no-op
    pra falhas, 'sucata_no_stock' e afins. Limpa um estorno anterior — uma nova
    entrada zera o `revertido_at`. NÃO faz commit (o caller persiste)."""
    if not sr or not sr.get("ok") or not sr.get("bling_product_id"):
        return
    row.estoque_mov_sku = sr.get("sku")
    row.estoque_mov_bling_id = int(sr["bling_product_id"])
    row.estoque_mov_action = sr.get("action")
    row.estoque_mov_qty = max(1, int(row.quantidade or 1))
    row.estoque_mov_revertido_at = None
    row.estoque_mov_ref = None


def _limpar_conferencia(row: Devolution) -> None:
    row.estoque_conferir_bling_id = None
    row.estoque_conferir_sku = None
    row.estoque_conferir_action = None


def limpar_status_estoque(row: Devolution) -> None:
    """A linha deixou de pedir entrada (condição sem estoque ou "devolver
    estoque" desligado): o resultado da tentativa anterior não vale mais."""
    row.estoque_status = None
    row.estoque_erro = None
    _limpar_conferencia(row)


def _fixar_produto_criado(row: Devolution, sr: StockResult) -> None:
    """O z000N foi criado e a entrada não se confirmou: a próxima tentativa
    (e a conferência) usam ESSE produto, sem criar outro."""
    if sr.get("produto_criado") and sr.get("sku"):
        row.estoque_destino_sku = sr["sku"]
        row.estoque_nova_tag = None
        row.estoque_suffix = None


def registrar_resultado_estoque(
    row: Devolution, sr: StockResult | None, *, inicio: datetime
) -> None:
    """Grava na linha o resultado da entrada no Bling (substitui o
    `record_stock_movement` solto no router). NÃO faz commit.

    `inicio` = hora de ANTES de chamar o Bling: a conferência procura o
    movimento a partir dela (o webhook chega antes do 504 voltar — revisão de
    15/10). ok com produto → movimento registrado + status ok; ok sem produto
    (Manutenção→Sucata) → não se aplica; incerto → conferindo (o worker
    confere); qualquer outra falha → falhou, com o motivo."""
    row.estoque_tentativa_at = inicio
    if sr is None:
        limpar_status_estoque(row)
        return
    if sr.get("incerto") and sr.get("bling_product_id"):
        row.estoque_status = ESTOQUE_CONFERINDO
        row.estoque_erro = sr.get("message") or None
        row.estoque_conferir_bling_id = int(sr["bling_product_id"])
        row.estoque_conferir_sku = sr.get("sku")
        row.estoque_conferir_action = sr.get("acao_entrada") or "entry_existing"
        _fixar_produto_criado(row, sr)
        return
    if sr.get("ok"):
        row.estoque_auto_tentativas = 0
        if sr.get("bling_product_id"):
            record_stock_movement(row, sr)
            row.estoque_status = ESTOQUE_OK
        else:
            row.estoque_status = None
        row.estoque_erro = None
        _limpar_conferencia(row)
        return
    row.estoque_status = ESTOQUE_FALHOU
    motivo = sr.get("message") or "O Bling não confirmou a entrada."
    _limpar_conferencia(row)
    _fixar_produto_criado(row, sr)
    if sr.get("limite"):
        if (row.estoque_auto_tentativas or 0) < AUTO_MAX:
            # O Bling não gravou nada: o worker tenta de novo sozinho.
            row.estoque_conferir_action = ESTOQUE_AUTO
            motivo = f"{motivo} O DaVinci tenta de novo sozinho a cada 2 min."
        else:
            motivo = (
                f"{motivo} O DaVinci já tentou {AUTO_MAX} vezes sozinho: use Tentar de novo."
            )
    row.estoque_erro = motivo[:500]


async def retentar_entradas_recusadas(
    session: AsyncSession, *, agora: datetime | None = None, limite: int = 10
) -> dict[str, int]:
    """Worker: tenta de novo, sozinho, a entrada que o Bling recusou por
    excesso de chamadas (marca ESTOQUE_AUTO; o Bling não gravou nada, então é
    seguro). Uma linha por transação, com FOR UPDATE SKIP LOCKED e a linha
    marcada "conferindo" ANTES de chamar o Bling — um "Tentar de novo" da tela
    no meio recebe 409, nunca lança 2×. Até AUTO_MAX vezes, a cada ~2 min."""
    agora = agora or datetime.now(UTC)
    out = {"tentadas": 0, "entraram": 0}
    confirmados: list[str] = []
    for _ in range(limite):
        row = (
            await session.execute(
                select(Devolution)
                .where(
                    Devolution.estoque_status == ESTOQUE_FALHOU,
                    Devolution.estoque_conferir_action == ESTOQUE_AUTO,
                    Devolution.estoque_auto_tentativas < AUTO_MAX,
                    Devolution.devolver_estoque.is_(True),
                    Devolution.condicao_produto.in_(tuple(_STOCK_TRIGGER_CONDICOES)),
                    or_(
                        Devolution.estoque_tentativa_at.is_(None),
                        Devolution.estoque_tentativa_at
                        <= agora
                        - func.make_interval(
                            0, 0, 0, 0, 0, 0,
                            _AUTO_INTERVALO.total_seconds()
                            * func.power(2, Devolution.estoque_auto_tentativas),
                        ),
                    ),
                )
                .order_by(Devolution.estoque_tentativa_at.asc().nullsfirst())
                .limit(1)
                .with_for_update(skip_locked=True)
            )
        ).scalar_one_or_none()
        if row is None:
            break
        if estorno_pendente(row):
            # Entrada anterior ainda sem estorno: isso a pessoa resolve na tela.
            row.estoque_conferir_action = None
            await session.commit()
            continue
        inicio = datetime.now(UTC)
        row.estoque_status = ESTOQUE_CONFERINDO
        row.estoque_conferir_action = None
        row.estoque_tentativa_at = inicio
        row.estoque_auto_tentativas = (row.estoque_auto_tentativas or 0) + 1
        await session.commit()
        await session.refresh(row)
        sr = await return_product_to_bling_stock(session, row)
        registrar_resultado_estoque(row, sr, inicio=inicio)
        await session.commit()
        out["tentadas"] += 1
        logger.info(
            "devolution_stock_retentativa",
            devolution_id=str(row.id), pedido_bling=row.pedido_bling,
            tentativa=row.estoque_auto_tentativas, status=row.estoque_status,
        )
        if status_estoque(row) == ESTOQUE_OK:
            out["entraram"] += 1
            if row.pedido_bling:
                confirmados.append(row.pedido_bling)
        elif sr is not None and sr.get("limite"):
            # O Bling segue recusando: as outras linhas esperam o próximo tick
            # (não gastam tentativa numa tempestade de 429).
            break
    for pedido in dict.fromkeys(confirmados):
        await aplicar_situacao_ou_guardar(session, pedido)
    return out


async def aplicar_situacao_ou_guardar(session: AsyncSession, pedido: str) -> None:
    """Situação do pedido aplicada pelo SISTEMA (worker): se o Bling recusar
    por excesso de chamadas/rede, guarda o pedido para o próximo tick tentar de
    novo — senão o pedido ficava em Aguardando Devolução com a entrada já feita
    (revisão de 21/10)."""
    from app.redis_client import redis as _redis

    res = await apply_order_situacao(session, pedido, actor_id=None)
    await session.commit()
    try:
        if res is not None and res.get("action") == "situacao_transitoria":
            await _redis.sadd(SITUACAO_PENDENTE_KEY, pedido)
        else:
            await _redis.srem(SITUACAO_PENDENTE_KEY, pedido)
    except Exception:  # noqa: BLE001
        logger.warning("devolution_situacao_pendente_redis_falhou", pedido_bling=pedido)


async def reaplicar_situacoes_pendentes(session: AsyncSession, *, limite: int = 20) -> int:
    """Worker: reaplica a situação dos pedidos que o Bling recusou por excesso
    de chamadas (guardados por aplicar_situacao_ou_guardar)."""
    from app.redis_client import redis as _redis

    pedidos = list(await _redis.smembers(SITUACAO_PENDENTE_KEY))[:limite]
    for pedido in pedidos:
        await aplicar_situacao_ou_guardar(session, pedido)
    return len(pedidos)


def registrar_ja_entrou(row: Devolution, *, pid: int, sku: str) -> None:
    """A pessoa conferiu no extrato do Bling que a entrada EXISTE (linha "sem
    confirmação"): registra o movimento como se tivesse confirmado — volta a
    ser possível estornar ao excluir/desligar. NÃO faz commit."""
    record_stock_movement(
        row, _result(True, "entry_conferida_manual", sku=sku, bling_product_id=int(pid))
    )
    row.estoque_status = ESTOQUE_OK
    row.estoque_erro = None
    _limpar_conferencia(row)


# Marca (em estoque_conferir_action) do estorno incerto que veio de uma
# EXCLUSÃO: conferido, a linha só precisa ser excluída de novo.
ESTORNO_DA_EXCLUSAO = "estorno_exclusao"


def registrar_estorno_conferido(row: Devolution) -> bool:
    """A pessoa conferiu no extrato que o ESTORNO (saída) que o Bling respondeu
    com erro saiu de fato: marca a entrada anterior como estornada, sem lançar
    nada. Se a linha ainda pede entrada (troca de condição/destino), falta a
    entrada nova → "falhou" para o Tentar de novo lançar; se o estorno era de
    uma exclusão, nada falta (a pessoa exclui de novo, sem baixa). Devolve se
    era de exclusão. NÃO faz commit."""
    da_exclusao = row.estoque_conferir_action == ESTORNO_DA_EXCLUSAO
    row.estoque_mov_revertido_at = datetime.now(UTC)
    _limpar_conferencia(row)
    if da_exclusao:
        row.estoque_status = None
        row.estoque_erro = None
    elif row.devolver_estoque and (row.condicao_produto or "").strip() in _STOCK_TRIGGER_CONDICOES:
        row.estoque_status = ESTOQUE_FALHOU
        row.estoque_erro = (
            "Estorno conferido. Falta lançar a entrada no destino novo: use Tentar de novo."
        )
    else:
        row.estoque_status = None
        row.estoque_erro = None
    return da_exclusao


def marcar_estorno_incerto(row: Devolution, rev: StockResult) -> None:
    """O estorno pode ter saído (5xx/timeout): a linha fica "sem confirmação"
    com o texto do estorno — ninguém estorna de novo sem conferir. NÃO faz
    commit."""
    row.estoque_status = ESTOQUE_SEM_CONFIRMACAO
    row.estoque_erro = (rev.get("message") or "O estorno pode ter saído no Bling.")[:500]
    row.estoque_tentativa_at = datetime.now(UTC)
    _limpar_conferencia(row)


def estorno_pendente(row: Any) -> bool:
    return (
        getattr(row, "estoque_mov_bling_id", None) is not None
        and getattr(row, "estoque_mov_revertido_at", None) is None
    )


async def conferir_entradas_pendentes(
    session: AsyncSession, *, agora: datetime | None = None
) -> dict[str, int]:
    """Worker: confere as linhas "conferindo" no espelho de movimentos.

    Confirma quando há uma entrada (E) do mesmo produto, com a mesma
    quantidade, a partir da tentativa, que nenhuma outra coisa explica: tira as
    já usadas por outra conferência e desconta as linhas ok do mesmo produto
    tentadas na mesma janela (cada uma tem a sua entrada). Na dúvida NÃO
    confirma — passados 10 min vira sem_confirmacao e a pessoa confere no
    extrato. Confirmada → recalcula a situação do pedido."""
    agora = agora or datetime.now(UTC)
    rows = (
        (
            await session.execute(
                select(Devolution)
                .where(Devolution.estoque_status == ESTOQUE_CONFERINDO)
                .order_by(Devolution.estoque_tentativa_at.asc().nullsfirst())
                .with_for_update(skip_locked=True)
            )
        )
        .scalars()
        .all()
    )
    out = {"conferidas": len(rows), "confirmadas": 0, "sem_confirmacao": 0}
    confirmados: list[str] = []
    for row in rows:
        pid = row.estoque_conferir_bling_id
        t0 = row.estoque_tentativa_at or row.data_devolvido_estoque or agora
        qty = max(1, int(row.quantidade or 1))
        if pid:
            # Quem mais pode ser dono de uma entrada do mesmo produto perto da
            # tentativa: outra devolução (já ok, ou também conferindo) e o robô
            # de prioridade (lança E em componente de kit). Na dúvida, não
            # confirma. A busca dos candidatos começa no mais cedo deles — senão
            # a 2ª devolução do mesmo SKU, segundos depois, nunca confirmava.
            quando = func.coalesce(
                Devolution.estoque_tentativa_at, Devolution.data_devolvido_estoque
            )
            de, ate = t0 - _CANDIDATO_JANELA, t0 + _CANDIDATO_JANELA
            n_dev, primeira_dev = (
                await session.execute(
                    select(func.count(), func.min(quando))
                    .select_from(Devolution)
                    .where(
                        Devolution.id != row.id,
                        Devolution.estoque_mov_ref.is_(None),
                        or_(
                            Devolution.estoque_mov_bling_id == int(pid),
                            Devolution.estoque_conferir_bling_id == int(pid),
                        ),
                        quando >= de,
                        quando <= ate,
                    )
                )
            ).one()
            robo_quando = func.coalesce(
                PrioridadeEstoqueMovimento.lancado_at, PrioridadeEstoqueMovimento.updated_at
            )
            n_robo, primeira_robo = (
                await session.execute(
                    select(func.count(), func.min(robo_quando))
                    .select_from(PrioridadeEstoqueMovimento)
                    .where(
                        PrioridadeEstoqueMovimento.bling_product_id == int(pid),
                        PrioridadeEstoqueMovimento.operacao == "E",
                        PrioridadeEstoqueMovimento.quantidade == qty,
                        PrioridadeEstoqueMovimento.status.in_(("ok", "incerto")),
                        robo_quando.between(de, ate),
                    )
                )
            ).one()
            concorrentes = int(n_dev or 0) + int(n_robo or 0)
            inicio_busca = min(x for x in (t0, primeira_dev, primeira_robo) if x is not None)
            usados = select(Devolution.estoque_mov_ref).where(
                Devolution.estoque_mov_ref.is_not(None)
            )
            candidatos = (
                (
                    await session.execute(
                        select(StockMovement.id)
                        .where(
                            StockMovement.bling_product_id == int(pid),
                            StockMovement.tipo == "E",
                            StockMovement.quantidade == qty,
                            StockMovement.date >= inicio_busca - _CONFERIR_FOLGA,
                            StockMovement.date <= t0 + _CANDIDATO_JANELA,
                            StockMovement.id.not_in(usados),
                        )
                        .order_by(StockMovement.date.asc())
                    )
                )
                .scalars()
                .all()
            )
            if len(candidatos) > int(concorrentes):
                record_stock_movement(
                    row,
                    _result(
                        True,
                        row.estoque_conferir_action or "entry_existing",
                        sku=row.estoque_conferir_sku,
                        bling_product_id=int(pid),
                    ),
                )
                row.estoque_mov_ref = candidatos[0]
                row.estoque_status = ESTOQUE_OK
                row.estoque_erro = None
                _limpar_conferencia(row)
                out["confirmadas"] += 1
                if row.pedido_bling:
                    confirmados.append(row.pedido_bling)
                logger.info(
                    "devolution_stock_conferida",
                    devolution_id=str(row.id),
                    bling_product_id=int(pid),
                    candidatos=len(candidatos),
                    concorrentes=int(concorrentes),
                )
                continue
        if agora - t0 >= CONFERIR_JANELA:
            sku = row.estoque_conferir_sku or row.estoque_destino_sku or row.sku or "do item"
            row.estoque_status = ESTOQUE_SEM_CONFIRMACAO
            row.estoque_erro = (
                f"O Bling respondeu com erro e a entrada não apareceu em 10 min. "
                f"Confira o extrato do SKU {sku} no Bling antes de tentar de novo."
            )
            out["sem_confirmacao"] += 1
            logger.warning(
                "devolution_stock_sem_confirmacao",
                devolution_id=str(row.id),
                bling_product_id=pid,
            )
    await session.commit()
    for pedido in dict.fromkeys(confirmados):
        await aplicar_situacao_ou_guardar(session, pedido)
    return out


async def reverse_stock_movement(
    session: AsyncSession, row: Devolution
) -> StockResult | None:
    """Estorna (dá baixa "S") a entrada de estoque registrada em `row`, quando o
    operador desliga "devolver estoque" depois — ex.: item entrou como Usado e
    numa verificação posterior virou Sucata, então não pode ficar vendável.

    Best-effort: nunca levanta. Retorna None quando não há nada a estornar
    (sem movimento registrado ou já estornado). Damos BAIXA na mesma quantidade
    (não excluímos o produto): o z-SKU criado fica com estoque 0 e some das
    vendas, sem quebrar histórico/movimentos no Bling."""
    pid = row.estoque_mov_bling_id
    if not pid or row.estoque_mov_revertido_at is not None:
        return None
    qty = max(1, int(row.estoque_mov_qty or row.quantidade or 1))
    mov_sku = row.estoque_mov_sku or row.sku
    obs = (
        f"Estorno devolução pedido {row.pedido_bling}"
        if row.pedido_bling
        else "Estorno devolução"
    )
    ctx = {
        "devolution_id": str(row.id),
        "bling_product_id": int(pid),
        "mov_sku": mov_sku,
        "qty": qty,
    }

    client = await _get_bling_client(session)
    if client is None:
        logger.warning("devolution_stock_reverse_no_integration", **ctx)
        return _result(False, "no_integration", message="Nenhuma integração Bling encontrada")
    try:
        await client.update_stock_by_id(int(pid), qty=qty, operation="S", observacao=obs)
        row.estoque_mov_revertido_at = datetime.now(UTC)
        logger.info("devolution_stock_reversed", **ctx)
        return _result(
            True, "stock_reversed", sku=mov_sku, bling_product_id=int(pid),
            message=f"Estoque estornado · −{qty} em {mov_sku or pid}",
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("devolution_stock_reverse_error", error=str(exc), **ctx)
        if _resposta_incerta(exc):
            # A saída pode ter sido gravada (5xx/timeout depois de enviar):
            # estornar de novo daria baixa 2× (revisão de 15/10).
            return _result(
                False, "reverse_incerto", sku=mov_sku, bling_product_id=int(pid),
                message=(
                    f"O estorno (saída de {qty} un. em {mov_sku or pid}) pode ter saído no "
                    f"Bling. Confira no extrato de {mov_sku or pid} a saída \"{obs}\" antes "
                    "de repetir."
                ),
            )
        # Ação própria (15/10): a tela não pode dizer "NÃO entrou" de um ESTORNO.
        return _result(
            False, "reverse_error", sku=mov_sku, message=_texto_erro_bling(exc, estorno=True)
        )


# ── Estoque ────────────────────────────────────────────────────────────────

async def return_product_to_bling_stock(
    session: AsyncSession, row: Devolution, *, obs_override: str | None = None
) -> StockResult | None:
    """Best-effort: lê as escolhas dos modais persistidas em `row` e devolve as
    unidades ao estoque Bling. Retorna um dict de resultado; nunca levanta.

    `obs_override` define a observação do movimento no Bling (usado pela correção
    manual de estoque); quando ausente, usa "Devolução pedido <n>"."""
    condicao = (row.condicao_produto or "").strip()
    if condicao not in _STOCK_TRIGGER_CONDICOES:
        return None

    # Resolve SKU e condição efetivos (Trocado/Manutenção vêm do modal).
    if condicao == "Trocado":
        eff_sku = (row.troca_sku or "").strip()
        eff_condicao = (row.troca_condicao or "").strip()
        if not eff_sku:
            return _result(False, "no_troca_sku", message="Trocado sem SKU de retorno")
        if eff_condicao not in _STOCK_CONDICOES:
            return _result(
                False, "no_troca_condicao", sku=eff_sku,
                message="Trocado sem condição (Novo/Usado) selecionada",
            )
    elif condicao == "Manutenção":
        dest = (row.manutencao_destino or "").strip()
        if dest == "Sucata":
            return _result(True, "sucata_no_stock", message="Manutenção → Sucata: estoque mantido")
        if dest not in _STOCK_CONDICOES:
            return _result(
                False, "no_manutencao_destino",
                message="Manutenção sem destino (Novo/Usado/Sucata) selecionado",
            )
        eff_sku = (row.sku or "").strip()
        eff_condicao = dest
    else:  # Novo / Usado
        eff_sku = (row.sku or "").strip()
        eff_condicao = condicao

    qty = max(1, int(row.quantidade or 1))
    obs = (obs_override or "").strip() or (
        f"Devolução pedido {row.pedido_bling}" if row.pedido_bling else "Devolução"
    )
    ctx = {
        "devolution_id": str(row.id),
        "condicao": condicao,
        "eff_condicao": eff_condicao,
        "sku": row.sku,
        "eff_sku": eff_sku,
        "qty": qty,
        "destino_sku": row.estoque_destino_sku,
        "nova_tag": row.estoque_nova_tag,
    }

    client = await _get_bling_client(session)
    if client is None:
        logger.warning("devolution_stock_no_bling_integration", **ctx)
        return _result(False, "no_integration", message="Nenhuma integração Bling encontrada")

    # Custo a carimbar no lançamento de estoque (alimenta o custo médio do Bling
    # e a coluna "Preço de Custo" do extrato). Resolvido uma vez e passado a
    # TODOS os caminhos — entrada em bin existente também leva custo, não só a
    # criação de avulso.
    cost = await _resolve_cost(session, row, eff_sku)

    try:
        # 1) Destino existente escolhido no modal: entrada direta nesse bin.
        if row.estoque_destino_sku and (dest_sku := row.estoque_destino_sku.strip()):
            return await _return_to_existing(session, client, dest_sku, qty, obs, ctx, cost)
        # 2) Criar produto novo z000N(.<tag>) — sentinel "-" = sem sufixo.
        if row.estoque_nova_tag and (raw_nt := row.estoque_nova_tag.strip()):
            tag = None if raw_nt == NOVA_TAG_SEM else raw_nt.lower().lstrip(".")
            return await _create_z_product(
                client, session, tag, eff_sku, eff_condicao, row, qty, obs, ctx, cost
            )
        # 3) Legado: modal `.sp` antigo (sufixo regional direto).
        if row.estoque_suffix and (suffix := row.estoque_suffix.strip().lower().lstrip(".")):
            return await _return_with_suffix(session, client, eff_sku, suffix, row, qty, obs, ctx, cost)
        # 4) Mala/Eletro NOVO: entrada direta no próprio SKU (salvaguarda — o
        #    front já manda estoque_destino_sku). Usado segue a lógica de usados.
        if eff_condicao == "Novo" and _is_mala_or_eletro(eff_sku):
            return await _return_to_existing(session, client, eff_sku, qty, obs, ctx, cost)
        # 5) Sem destino do modal — comportamento direto pelo SKU.
        if eff_condicao == "Novo":
            return await _return_novo(session, client, eff_sku, qty, obs, ctx, cost)
        return await _return_usado(session, client, eff_sku, row, qty, obs, ctx, cost)
    except EntradaIncertaError as exc:
        logger.warning(
            "devolution_stock_return_incerto",
            error=str(exc.causa),
            bling_product_id=exc.pid,
            **ctx,
        )
        return {
            **_result(
                False,
                "unconfirmed",
                sku=exc.sku,
                bling_product_id=exc.pid,
                message=(
                    "O Bling respondeu com erro depois de receber a entrada — ela pode "
                    "ter entrado. O DaVinci confere sozinho em até 10 min: não lance de novo."
                ),
            ),
            "incerto": True,
            "acao_entrada": exc.action,
            "produto_criado": exc.action.startswith("product_created"),
        }
    except ProdutoCriadoSemEntradaError as exc:
        logger.error(
            "devolution_stock_return_error",
            error=str(exc.causa), produto_criado=exc.sku, bling_product_id=exc.pid, **ctx,
        )
        return {
            **_result(
                False, "error", sku=exc.sku, bling_product_id=exc.pid,
                message=(
                    f"{_texto_erro_bling(exc.causa)} O produto {exc.sku} foi criado; "
                    "a nova tentativa lança nele."
                ),
            ),
            "produto_criado": True,
            "limite": _falha_sem_envio(exc.causa),
        }
    except Exception as exc:  # noqa: BLE001
        logger.error("devolution_stock_return_error", error=str(exc), **ctx)
        return {
            **_result(False, "error", message=_texto_erro_bling(exc)),
            "limite": _falha_sem_envio(exc),
        }


async def _local_bling_product_id(session: AsyncSession, sku: str) -> int | None:
    """bling_product_id da tabela local products (ativo primeiro). Robusto para
    SKUs com ponto (ex.: b043.12), que a busca por código no Bling não acha."""
    if not sku:
        return None
    pid = (
        await session.execute(
            select(Product.bling_product_id)
            .where(
                func.lower(func.btrim(Product.sku)) == sku.strip().lower(),
                Product.bling_product_id.is_not(None),
            )
            .order_by((Product.situacao == "A").desc().nullslast())
            .limit(1)
        )
    ).scalar_one_or_none()
    return int(pid) if pid else None


async def _resolve_product_id(
    session: AsyncSession, client: BlingClient, sku: str
) -> int | None:
    """Resolve o bling_product_id: tabela local primeiro, fallback na busca
    por código no Bling."""
    pid = await _local_bling_product_id(session, sku)
    if pid:
        return pid
    product = await client.find_active_product_by_sku(sku)
    return int(product["id"]) if product else None


def _sku_root(sku: str) -> str:
    """Raiz do SKU p/ achar variantes semelhantes: 1º componente do kit, sem
    qualquer extensão após o 1º ponto. `b048.12` → `b048`; `x001.ra` → `x001`."""
    first = (sku or "").split("+")[0].strip()
    return first.split(".")[0].lower()


async def _resolve_cost_from_similar(session: AsyncSession, sku: str | None) -> float | None:
    """Custo (cost_price) de um produto original/semelhante na tabela local.

    Prioridade: SKU exato → qualquer variante com a mesma raiz (`b048.*`),
    sempre exigindo cost_price > 0 e preferindo ativo. `None` se nada bater.
    O exato vem primeiro porque cada variante tem custo próprio (b048.12 ≠
    b048); a raiz é só fallback quando a variante exata não existe."""
    if not sku:
        return None
    s = sku.strip().lower()
    root = _sku_root(s)
    if not root:
        return None
    exact = func.lower(func.btrim(Product.sku)) == s
    val = (
        await session.execute(
            select(Product.cost_price)
            .where(
                or_(
                    exact,
                    func.lower(Product.sku) == root,
                    func.lower(Product.sku).like(f"{root}.%"),
                ),
                Product.cost_price.is_not(None),
                Product.cost_price > 0,
            )
            .order_by(exact.desc(), (Product.situacao == "A").desc().nullslast())
            .limit(1)
        )
    ).scalar_one_or_none()
    return float(val) if val is not None else None


async def _resolve_cost(
    session: AsyncSession, row: Devolution, eff_sku: str | None
) -> float | None:
    """Custo a gravar no produto avulso: o custo da própria devolução quando
    presente (>0); senão busca do produto original/semelhante pelo SKU."""
    if row.custo_produto and row.custo_produto > 0:
        return float(row.custo_produto)
    return await _resolve_cost_from_similar(session, eff_sku)


# Fornecedor padrão (âncora do precoCusto no Bling V3) — resolvido 1x e cacheado.
_default_supplier_id: int | None = None
_default_supplier_resolved = False


async def _get_default_supplier_id(client: BlingClient) -> int | None:
    """Resolve (e cacheia) o contato.id do fornecedor padrão do config."""
    global _default_supplier_id, _default_supplier_resolved
    if _default_supplier_resolved:
        return _default_supplier_id
    name = get_settings().bling_default_supplier_name
    try:
        _default_supplier_id = (
            await client.find_contato_id_by_name(name) if name else None
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("devolution_supplier_resolve_failed", error=str(exc))
        _default_supplier_id = None
    _default_supplier_resolved = True
    return _default_supplier_id


async def _persist_cost(
    client: BlingClient, product_id: int, cost: float | None, ctx: dict
) -> None:
    """Persiste o precoCusto do produto recém-criado via fornecedor padrão.
    No-op silencioso quando não há custo (>0) ou fornecedor resolvível —
    nunca derruba o fluxo de estoque (a entrada já foi/será lançada)."""
    if not cost or cost <= 0:
        return
    supplier_id = await _get_default_supplier_id(client)
    if not supplier_id:
        logger.warning("devolution_stock_cost_no_supplier", **ctx)
        return
    try:
        await client.link_supplier_to_product(
            product_id=int(product_id), supplier_id=int(supplier_id), cost_price=float(cost)
        )
        logger.info(
            "devolution_stock_cost_linked",
            bling_product_id=int(product_id), cost=float(cost), **ctx,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "devolution_stock_cost_link_failed",
            bling_product_id=int(product_id), error=str(exc), **ctx,
        )


async def _return_to_existing(
    session: AsyncSession, client: BlingClient, dest_sku: str, qty: int, obs: str,
    ctx: dict, cost: float | None = None,
) -> StockResult:
    """Entrada de N unidades num bin já existente escolhido no modal."""
    pid = await _resolve_product_id(session, client, dest_sku)
    if pid is None:
        logger.warning("devolution_stock_dest_not_found", dest_sku=dest_sku, **ctx)
        return _result(
            False, "sku_not_found", sku=dest_sku,
            message=f"SKU {dest_sku} não encontrado no Bling",
        )
    await _lancar_entrada(
        client, pid, qty=qty, obs=obs, cost=cost, sku=dest_sku, action="entry_existing"
    )
    logger.info("devolution_stock_entry_existing", dest_sku=dest_sku, bling_product_id=pid, **ctx)
    return _result(
        True, "entry_existing", sku=dest_sku, bling_product_id=pid,
        message=f"SKU {dest_sku} · +{qty} unidade(s)",
    )


async def _max_z_in_products(session: AsyncSession) -> int:
    """Maior número de z-SKU já presente na tabela products (ignora sufixo)."""
    skus = (
        await session.execute(select(Product.sku).where(Product.sku.op("~*")("^z[0-9]")))
    ).scalars().all()
    max_n = 0
    for s in skus:
        m = re.match(r"(?i)^z0*([0-9]+)", s or "")
        if m:
            max_n = max(max_n, int(m.group(1)))
    return max_n


async def _next_z_sku_for_tag(
    session: AsyncSession, client: BlingClient, tag: str | None
) -> str:
    """Próximo `z000N.<tag>` (ou `z000N` sem sufixo quando tag=None) sequencial
    pelo maior z em products, garantindo que ainda não exista no Bling."""
    n = await _max_z_in_products(session) + 1
    while n < 100000:
        candidate = f"z{n:04d}.{tag}" if tag else f"z{n:04d}"
        if not await client.product_exists_by_sku(candidate):
            return candidate
        n += 1
    raise RuntimeError("Espaço de z-SKU esgotado")


async def _ensure_local_product(
    session: AsyncSession, bling_product_id: int, ctx: dict
) -> None:
    """Garante a linha local em `products` pro avulso recém-criado, na hora.

    Idempotente e best-effort: nunca derruba o retorno de estoque. Resolve o
    dono pelo ProductLink Bling (mesma atribuição single-tenant do webhook)."""
    from app.models.product import ProductLink
    from app.services.bling_product_create import run_auto_create_product_from_bling

    try:
        link = (
            await session.execute(
                select(ProductLink)
                .where(ProductLink.platform == IntegrationPlatform.BLING)
                .limit(1)
            )
        ).scalar_one_or_none()
        if link is None:
            logger.warning("devolution_local_product_no_link", **ctx)
            return
        await run_auto_create_product_from_bling(
            session, bling_product_id=bling_product_id, user_id=link.user_id
        )
    except Exception as exc:  # noqa: BLE001
        # Webhook/job ainda é o fallback; só logamos.
        logger.warning(
            "devolution_local_product_failed",
            bling_product_id=bling_product_id, error=str(exc), **ctx,
        )


async def _create_z_product(
    client: BlingClient, session: AsyncSession, tag: str | None,
    eff_sku: str, eff_condicao: str, row: Devolution, qty: int, obs: str, ctx: dict,
    cost: float | None = None,
) -> StockResult:
    """Cria `z000N.<tag>` (ou `z000N` sem sufixo se tag=None) clonando
    nome/categoria do original e lança qty."""
    target_sku = await _next_z_sku_for_tag(session, client, tag)

    # Produto original: resolve id pela tabela local (robusto p/ SKUs com ponto)
    # e busca o detalhe no Bling para clonar nome/categoria.
    category_id = None
    bling_name = None
    orig_pid = await _resolve_product_id(session, client, eff_sku) if eff_sku else None
    if orig_pid:
        try:
            raw = await client.get_product(orig_pid)
            bling_name = (raw or {}).get("nome")
            cat = (raw or {}).get("categoria")
            if isinstance(cat, dict):
                category_id = cat.get("id")
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "devolution_stock_clone_original_failed", orig_pid=orig_pid, error=str(exc)
            )
    base_name = (bling_name or row.produtos or eff_sku or target_sku).strip()
    suffix_label = " AVULSO SALVADO" if eff_condicao == "Usado" else " AVULSO"
    name = f"{base_name}{suffix_label}"

    if cost is None:
        cost = await _resolve_cost(session, row, eff_sku)
    new_data = await client.create_product(
        sku=target_sku, name=name, price=cost, category_id=category_id
    )
    product_id = (new_data or {}).get("id")
    if not product_id:
        logger.warning("devolution_stock_create_z_no_id", target_sku=target_sku, **ctx)
        return _result(
            False, "create_failed", sku=target_sku,
            message=f"Falha ao criar produto {target_sku} no Bling",
        )

    pid = int(product_id)
    await _persist_cost(client, pid, cost, ctx)
    # Cria a linha local em `products` JÁ — sem isso o avulso só apareceria na
    # busca da devolução depois que o webhook `product.created` do Bling
    # dispara o job arq `auto_create_product_from_bling`, que é frágil (o Bling
    # V3 perde webhooks). Quando o job não rodava, o z ficava invisível pra
    # seleção. Reaproveita o mesmo serviço (idempotente) de forma síncrona.
    # ANTES da entrada (15/10): o webhook de estoque só grava o movimento de
    # produto que já está em products — a conferência precisa dele.
    await _ensure_local_product(session, pid, ctx)
    await _entrada_em_produto_criado(
        client, pid, qty=qty, obs=obs, cost=cost, sku=target_sku, action="product_created_avulso"
    )
    logger.info(
        "devolution_stock_created_avulso",
        target_sku=target_sku, bling_product_id=pid, name=name, **ctx,
    )
    return _result(
        True, "product_created_avulso", sku=target_sku, bling_product_id=pid,
        message=f"Produto {target_sku} criado · +{qty} unidade(s)",
    )


async def _return_with_suffix(
    session: AsyncSession, client: BlingClient, eff_sku: str, suffix: str,
    row: Devolution, qty: int, obs: str, ctx: dict, cost: float | None = None,
) -> StockResult:
    """Legado (modal `.sp`): entrada em base.<suffix>; cria se não existir."""
    if not eff_sku:
        logger.warning("devolution_stock_suffix_no_sku", **ctx)
        return _result(False, "no_sku", message="Produto sem SKU — estoque não atualizado")

    if cost is None:
        cost = await _resolve_cost(session, row, eff_sku)
    target_sku = f"{_sku_base(eff_sku)}.{suffix}"
    product = await client.find_active_product_by_sku(target_sku)
    if product is not None:
        pid = int(product["id"])
        await _lancar_entrada(
            client, pid, qty=qty, obs=obs, cost=cost, sku=target_sku, action="entry_suffix"
        )
        logger.info(
            "devolution_stock_entry_suffix", target_sku=target_sku, bling_product_id=pid, **ctx
        )
        return _result(
            True, "entry_suffix", sku=target_sku, bling_product_id=pid,
            message=f"SKU {target_sku} · +{qty} unidade(s)",
        )

    nome = row.produtos or f"{target_sku}"
    new_data = await client.create_product(sku=target_sku, name=nome, price=cost)
    product_id = (new_data or {}).get("id")
    if not product_id:
        logger.warning("devolution_stock_suffix_create_no_id", target_sku=target_sku, **ctx)
        return _result(
            False, "create_failed", sku=target_sku,
            message=f"Falha ao criar produto {target_sku} no Bling",
        )

    pid = int(product_id)
    await _persist_cost(client, pid, cost, ctx)
    await _lancar_entrada(
        client, pid, qty=qty, obs=obs, cost=cost, sku=target_sku, action="product_created_suffix"
    )
    logger.info(
        "devolution_stock_created_suffix", target_sku=target_sku, bling_product_id=pid, **ctx
    )
    return _result(
        True, "product_created_suffix", sku=target_sku, bling_product_id=pid,
        message=f"Produto {target_sku} criado no Bling · +{qty} unidade(s)",
    )


async def _return_novo(
    session: AsyncSession, client: BlingClient, sku: str, qty: int, obs: str, ctx: dict,
    cost: float | None = None,
) -> StockResult:
    if not sku:
        logger.warning("devolution_stock_novo_no_sku", **ctx)
        return _result(False, "no_sku", message="Produto sem SKU — estoque não atualizado")

    pid = await _resolve_product_id(session, client, sku)
    if pid is None:
        logger.warning("devolution_stock_novo_sku_not_found", **ctx)
        return _result(
            False, "sku_not_found", sku=sku,
            message=f"SKU {sku} não encontrado no Bling",
        )

    await _lancar_entrada(client, pid, qty=qty, obs=obs, cost=cost, sku=sku, action="entry_novo")
    logger.info("devolution_stock_entry_novo", bling_product_id=pid, **ctx)
    return _result(
        True, "entry_novo", sku=sku, bling_product_id=pid,
        message=f"SKU {sku} · +{qty} unidade(s)",
    )


async def _return_usado(
    session: AsyncSession, client: BlingClient, sku: str, row: Devolution, qty: int,
    obs: str, ctx: dict, cost: float | None = None,
) -> StockResult:
    sku = sku or ""
    if not sku:
        sku_usado = None
    elif "." in sku:
        sku_usado = sku.rsplit(".", 1)[0] + ".us"
    elif len(sku) > 3:
        sku_usado = sku[:-3] + ".us"
    else:
        sku_usado = sku + ".us"

    if cost is None:
        cost = await _resolve_cost(session, row, sku)
    pid_usado = await _resolve_product_id(session, client, sku_usado) if sku_usado else None
    product = {"id": pid_usado} if pid_usado else None

    if product is not None:
        pid = int(product["id"])
        await _lancar_entrada(
            client, pid, qty=qty, obs=obs, cost=cost, sku=sku_usado, action="entry_usado"
        )
        logger.info(
            "devolution_stock_entry_usado", sku_usado=sku_usado, bling_product_id=pid, **ctx
        )
        return _result(
            True, "entry_usado", sku=sku_usado, bling_product_id=pid,
            message=f"SKU {sku_usado} · +{qty} unidade(s)",
        )

    # Não encontrado — cria sob o próximo z-SKU (sem tag, fallback legado)
    z_sku = await client.find_next_z_sku()
    nome = row.produtos or (f"Usado - {sku}" if sku else "Produto Usado")
    category_id = await client.get_category_id_by_name("Usado")

    new_data = await client.create_product(
        sku=z_sku, name=nome, price=cost, category_id=category_id
    )
    product_id = (new_data or {}).get("id")
    if not product_id:
        logger.warning("devolution_stock_create_product_no_id", z_sku=z_sku, **ctx)
        return _result(
            False, "create_failed", sku=z_sku,
            message=f"Falha ao criar produto {z_sku} no Bling",
        )

    pid = int(product_id)
    await _persist_cost(client, pid, cost, ctx)
    await _ensure_local_product(session, pid, ctx)
    await _entrada_em_produto_criado(
        client, pid, qty=qty, obs=obs, cost=cost, sku=z_sku, action="product_created_usado"
    )
    logger.info(
        "devolution_stock_created_usado",
        z_sku=z_sku, bling_product_id=pid, original_sku=sku or None, **ctx,
    )
    return _result(
        True, "product_created_usado", sku=z_sku, bling_product_id=pid,
        message=f"Produto {z_sku} criado no Bling · +{qty} unidade(s)",
    )
