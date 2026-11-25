"""Incluir estoque — botão "Incluir" do estoque do produto em Produtos 2 (M-43, Harry 24/11/2026).

Lança uma ENTRADA avulsa direto no estoque do Bling (POST /estoques, o mesmo da transferência, das
Devoluções e do robô de prioridade), pela API do DaVinci e não pelo ERP: o modo Consulta do ERP
recusa gravação, e lá a entrada seria apagada pelo espelho na rodada seguinte.

Mesmas proteções da transferência (services/estoque_transferencia.py):
- Só admin (o router usa `require_admin`).
- Só produto simples (formato S, sem "+", sem composição), ativo e com id no Bling. Kit não tem
  estoque próprio: "Kit não tem estoque próprio: inclua nas peças".
- A linha é gravada ANTES de lançar e marcada "enviando" (transação própria, já commitada) antes
  do POST: um 2º clique, o worker ou outro processo nunca lançam a mesma entrada de novo. A mesma
  Idempotency-Key devolve a mesma entrada.
- O custo atual do produto vai no lançamento (sem ele o Bling grava custo 0 e mexe no custo
  médio); a pessoa não digita custo.
- A observação digitada vai no lançamento do Bling, junto com "Entrada DaVinci #N" e quem lançou.
- Erro em que o Bling pode ter gravado (5xx/timeout depois de enviar) vira "conferindo": o worker
  procura o movimento no extrato (stock_movements) antes de qualquer outra coisa — nunca lança de
  novo às cegas. 10 min sem achar: "sem_confirmacao" e aviso no sino de quem lançou. Recusa
  certa (limite de chamadas, conexão que nem abriu, 4xx) = "falhou": nada entrou.
- "pendente" esquecido (o processo caiu entre gravar e lançar — nada foi enviado): o worker lança
  depois de 1 min, a não ser que já haja uma entrada igual mais nova do mesmo produto (a pessoa
  incluiu de novo) ou tenham passado 30 min — aí vira "falhou" com aviso no sino (nada entrou).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

import httpx
import structlog
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import session_scope
from app.models import BlingKitComponent, EstoqueEntrada, Product
from app.models.enums import AlertSeverity, AlertType
from app.services import estoque_transferencia as transf
from app.services.alerts import emit_alert
from app.services.devolution_stock_return import _falha_sem_envio

logger = structlog.get_logger()

PENDENTE = transf.PENDENTE
ENVIANDO = transf.ENVIANDO
OK = transf.OK
FALHOU = transf.FALHOU
CONFERINDO = transf.CONFERINDO
SEM_CONFIRMACAO = transf.SEM_CONFIRMACAO

MAX_QUANTIDADE = transf.MAX_QUANTIDADE
KIT = "Kit não tem estoque próprio: inclua nas peças"
TEXTO_OK = "Entrada lançada. O saldo atualiza em alguns minutos."

# "enviando" parado: o processo caiu no meio do POST — confere no extrato.
_ENVIANDO_ORFA = timedelta(minutes=5)
# "pendente" (nada foi enviado): o worker lança depois de 1 min…
_PENDENTE_ORFA = timedelta(minutes=1)
# …mas não depois de 30: a pessoa já pode ter incluído de novo por outro caminho.
_PENDENTE_VELHA = timedelta(minutes=30)


class EntradaInvalidaError(Exception):
    """A entrada não pode ser lançada: nada foi gravado nem lançado."""

    def __init__(self, code: str, message: str, status: int = 422):
        self.code = code
        self.message = message
        self.status = status
        super().__init__(code)


def norm(sku: str | None) -> str:
    return (sku or "").strip().lower()


async def por_idem(session: AsyncSession, idem_key: str) -> EstoqueEntrada | None:
    return (
        await session.execute(select(EstoqueEntrada).where(EstoqueEntrada.idem_key == idem_key))
    ).scalar_one_or_none()


async def produto_para_entrada(session: AsyncSession, sku: str) -> Product:
    """O produto que recebe a entrada: simples, ativo, com id no Bling. Senão
    EntradaInvalidaError com o motivo para a tela (kit primeiro: a mensagem
    do pedido)."""
    chave = norm(sku)
    if not chave:
        raise EntradaInvalidaError("nao_encontrado", "Informe o código do produto.")
    if "+" in chave:
        raise EntradaInvalidaError("kit", KIT)
    rows = (
        (
            await session.execute(
                select(Product)
                .where(func.lower(func.btrim(Product.sku)) == chave)
                .order_by((Product.situacao == "A").desc().nullslast())
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        raise EntradaInvalidaError("nao_encontrado", f"Produto {chave} não encontrado no DaVinci.")
    # Linha excluída no Bling não decide nada se houver outra viva do mesmo SKU.
    vivos = [p for p in rows if (p.situacao or "").upper() != "E"] or list(rows)
    if any((p.formato or "").upper() == "E" for p in vivos):
        raise EntradaInvalidaError("kit", KIT)
    pids = {int(p.bling_product_id) for p in vivos if p.bling_product_id}
    if pids:
        composto = (
            await session.execute(
                select(BlingKitComponent.kit_bling_product_id)
                .where(BlingKitComponent.kit_bling_product_id.in_(pids))
                .limit(1)
            )
        ).first()
        if composto is not None:
            raise EntradaInvalidaError("kit", KIT)
    if any((p.formato or "").upper() == "V" for p in vivos):
        raise EntradaInvalidaError(
            "nao_simples",
            f"{chave} é produto com variações: o estoque fica nas variações, inclua nelas.",
        )
    ativos = [p for p in rows if p.situacao in ("A", None)]
    if not ativos:
        excluido = all((p.situacao or "").upper() == "E" for p in rows)
        raise EntradaInvalidaError(
            "inativo",
            f"{chave} está {'excluído' if excluido else 'inativo'} no Bling: "
            "ative o produto no Bling antes de incluir estoque.",
        )
    com_bling = [p for p in ativos if p.bling_product_id]
    if not com_bling:
        raise EntradaInvalidaError(
            "sem_bling", f"{chave} não tem id do Bling no DaVinci: não dá para lançar no Bling."
        )
    return com_bling[0]


async def custo_atual(client: Any, produto: Product) -> Decimal | None:
    """O custo do cadastro (o do Bling primeiro); sem custo no DaVinci, o
    `precoCusto` atual do Bling. None = o produto não tem custo em lugar
    nenhum (o lançamento sai sem custo, como sairia à mão no Bling)."""
    for c in (produto.bling_cost_price, produto.cost_price):
        if c is not None and Decimal(c) > 0:
            return Decimal(c)
    sku = (produto.sku or "").strip()
    try:
        custos = await client.cost_by_skus({sku})
    except Exception:  # noqa: BLE001 — sem custo é o mesmo que no Bling à mão
        logger.warning("estoque_entrada_custo_bling_falhou", sku=sku)
        return None
    valor = custos.get(sku)
    if valor is not None and valor > 0:
        return Decimal(str(valor))
    return None


async def criar(
    session: AsyncSession,
    client: Any,
    *,
    idem_key: str,
    user_id: UUID | None,
    user_nome: str | None,
    sku: str,
    quantidade: int,
    observacao: str | None,
) -> tuple[EstoqueEntrada, bool]:
    """Grava a entrada (sem lançar). Mesma `idem_key` = a mesma entrada de
    antes (`False` no 2º item): a tela repetiu o POST — com outro produto ou
    outra quantidade, é recusada (409). Produto que não pode receber entrada
    → EntradaInvalidaError e nada gravado."""
    existente = await por_idem(session, idem_key)
    if existente is not None:
        if norm(existente.sku) != norm(sku) or int(existente.quantidade) != int(quantidade):
            raise EntradaInvalidaError(
                "idempotency_conflito",
                "Esta janela já lançou outra entrada. Feche e abra de novo para incluir outra.",
                409,
            )
        return existente, False
    if not isinstance(quantidade, int) or not (1 <= quantidade <= MAX_QUANTIDADE):
        raise EntradaInvalidaError(
            "quantidade", f"Quantidade precisa ser um número inteiro de 1 a {MAX_QUANTIDADE}."
        )
    produto = await produto_para_entrada(session, sku)
    custo = await custo_atual(client, produto)
    e = EstoqueEntrada(
        idem_key=idem_key,
        sku=produto.sku.strip(),
        bling_product_id=int(produto.bling_product_id),
        quantidade=int(quantidade),
        custo=custo,
        observacao=(observacao or "").strip()[:500] or None,
        criado_por=user_id,
        criado_por_nome=(user_nome or "").strip() or None,
        status=PENDENTE,
    )
    session.add(e)
    try:
        await session.commit()
    except IntegrityError:
        # Dois POSTs com a mesma chave ao mesmo tempo: fica o primeiro.
        await session.rollback()
        existente = await por_idem(session, idem_key)
        if existente is None:
            raise
        return existente, False
    await session.refresh(e)
    return e, True


# ── Lançamento ────────────────────────────────────────────────────────────


def observacao_bling(e: EstoqueEntrada) -> str:
    """A observação do lançamento no extrato do Bling: o que a pessoa digitou
    primeiro (é o que ela quer ler), depois "Entrada DaVinci #N" (para achar
    no extrato) e quem lançou."""
    partes = []
    if e.observacao:
        partes.append(e.observacao[:300])
    partes.append(f"Entrada DaVinci #{e.numero}")
    if e.criado_por_nome:
        partes.append(e.criado_por_nome)
    return " · ".join(partes)


def texto_erro(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        detalhe = ""
        try:
            err = (exc.response.json() or {}).get("error") or {}
            detalhe = str(err.get("description") or err.get("message") or "").strip()
        except Exception:  # noqa: BLE001
            detalhe = ""
        base = f"O Bling recusou a entrada (HTTP {code})"
        return f"{base}: {detalhe[:200]}" if detalhe else f"{base}."
    if _falha_sem_envio(exc):
        return "O Bling estava ocupado (limite de chamadas ou conexão) e recusou a entrada."
    return f"O Bling não respondeu direito sobre a entrada: {str(exc)[:160]}"


def _id_do_lancamento(data: Any) -> int | None:
    try:
        lid = int((data or {}).get("id"))
    except (TypeError, ValueError, AttributeError):
        return None
    return lid if lid > 0 else None


async def _marcar_enviando(entrada_id: UUID, permitidos: tuple[str, ...]) -> datetime | None:
    """Trava a entrada ANTES do POST (transação própria, já commitada): quem
    vier depois vê "enviando" e não lança de novo. Devolve a marca da
    tentativa, ou None se ela não estava num estado que permite lançar."""
    agora = datetime.now(UTC)
    async with session_scope() as s:
        e = (
            await s.execute(
                select(EstoqueEntrada).where(EstoqueEntrada.id == entrada_id).with_for_update()
            )
        ).scalar_one_or_none()
        if e is None or e.status not in permitidos:
            return None
        e.status = ENVIANDO
        e.tentativa_at = agora
        e.erro = None
    return agora


async def _gravar_se(
    entrada_id: UUID, *, de: tuple[str, ...], marca: datetime | None = None, **campos: Any
) -> bool:
    """Grava só se a entrada ainda está num dos estados `de` (e, com `marca`,
    na mesma tentativa): uma resposta atrasada do Bling não volta o estado."""
    async with session_scope() as s:
        e = (
            await s.execute(
                select(EstoqueEntrada).where(EstoqueEntrada.id == entrada_id).with_for_update()
            )
        ).scalar_one_or_none()
        if e is None or e.status not in de:
            logger.warning("estoque_entrada_gravacao_ignorada", entrada=str(entrada_id))
            return False
        if marca is not None and e.tentativa_at != marca:
            logger.warning("estoque_entrada_resposta_atrasada", entrada=str(entrada_id))
            return False
        for k, v in campos.items():
            setattr(e, k, v)
    return True


async def _carregar(entrada_id: UUID) -> EstoqueEntrada | None:
    async with session_scope() as s:
        e = await s.get(EstoqueEntrada, entrada_id)
        if e is not None:
            s.expunge(e)
        return e


async def executar(
    client: Any, entrada_id: UUID, *, permitidos: tuple[str, ...] = (PENDENTE,)
) -> str | None:
    """UM POST /estoques (operação E) da entrada, já marcada "enviando", e
    grava o resultado: ok | falhou (com certeza não gravou) | conferindo (pode
    ter gravado: o worker confere no extrato). None = não estava para lançar."""
    marca = await _marcar_enviando(entrada_id, permitidos)
    if marca is None:
        return None
    e = await _carregar(entrada_id)
    if e is None:
        return None
    try:
        data = await client.update_stock_by_id(
            int(e.bling_product_id),
            int(e.quantidade),
            operation="E",
            observacao=observacao_bling(e),
            custo=float(e.custo) if e.custo else None,
            repetir_503=False,
        )
    except Exception as exc:  # noqa: BLE001 — classificado abaixo
        status = transf._classificar(exc)
        erro = texto_erro(exc)
        if status == CONFERINDO:
            erro = (
                f"{erro} O Bling pode ter gravado: o DaVinci confere no extrato antes de "
                "qualquer outra coisa."
            )
        await _gravar_se(entrada_id, de=(ENVIANDO,), marca=marca, status=status, erro=erro)
        logger.warning(
            "estoque_entrada_erro",
            entrada=e.numero,
            sku=e.sku,
            status=status,
            erro=str(exc)[:200],
        )
        return status
    await _gravar_se(
        entrada_id,
        de=(ENVIANDO,),
        marca=marca,
        status=OK,
        lancada_at=datetime.now(UTC),
        erro=None,
        bling_lancamento_id=_id_do_lancamento(data),
    )
    logger.info("estoque_entrada_ok", entrada=e.numero, sku=e.sku, qtd=e.quantidade)
    return OK


# ── Worker: termina as pendentes e confere as duvidosas no extrato ───────


async def _conferir(session: AsyncSession, e: EstoqueEntrada, agora: datetime) -> str | None:
    """Entrada "conferindo": procura o E no espelho do extrato a partir da
    tentativa, descontando quem mais pode ser dono de uma entrada igual
    (`estoque_transferencia.procurar_entrada_no_extrato`). 10 min sem achar:
    sem_confirmacao — erro aqui é o lado seguro (a peça falta, não sobra)."""
    t0 = e.tentativa_at or e.updated_at or agora
    achado = await transf.procurar_entrada_no_extrato(
        session, pid=int(e.bling_product_id), qtd=int(e.quantidade), t0=t0, entrada_id=e.id
    )
    if achado is not None:
        e.status = OK
        e.mov_ref = achado
        e.lancada_at = agora
        e.erro = None
        return OK
    if agora - t0 >= transf.CONFERIR_JANELA:
        e.status = SEM_CONFIRMACAO
        e.erro = (
            f"O Bling respondeu com erro e a entrada de {e.quantidade} em {e.sku} não apareceu "
            f'no extrato em 10 min. Confira o extrato do SKU no Bling (procure "Entrada DaVinci '
            f'#{e.numero}"): se não estiver lá, inclua de novo.'
        )
        return SEM_CONFIRMACAO
    return None


async def _avisar(session: AsyncSession, e: EstoqueEntrada, titulo: str, msg: str) -> None:
    """Sino de quem lançou (best-effort, savepoint). Um aviso por estado e
    tentativa."""
    if e.criado_por is None:
        return
    quando = e.tentativa_at.isoformat() if e.tentativa_at else "-"
    try:
        async with session.begin_nested():
            await emit_alert(
                session,
                user_id=e.criado_por,
                type=AlertType.GENERIC,
                escopo="pessoal",  # M-19: só a pessoa vê
                severity=AlertSeverity.WARNING,
                title=titulo,
                message=msg,
                dedupe_key=f"estoque_entrada:{e.id}:{e.status}:{quando}",
                payload={"entrada": e.numero, "entrada_id": str(e.id), "sku": e.sku},
            )
    except Exception:  # noqa: BLE001
        logger.warning("estoque_entrada_aviso_falhou", entrada=str(e.id))


async def _parar(session: AsyncSession, e: EstoqueEntrada, erro: str) -> None:
    e.status = FALHOU
    e.erro = erro
    await _avisar(session, e, f"Entrada #{e.numero}: {e.sku} não entrou", erro)


async def processar_pendentes(client: Any, *, agora: datetime | None = None) -> dict[str, int]:
    """Worker (a cada 2 min). Sem pendência = 1 SELECT e zero Bling.
    - "pendente" há 1 min (nada foi enviado): lança — salvo se há entrada
      igual mais nova do mesmo produto (a pessoa incluiu de novo) ou se já
      passaram 30 min: aí "falhou" (nada entrou) com aviso no sino;
    - "enviando" há 5 min (caiu no meio do POST) → "conferindo";
    - "conferindo" → procura no extrato (ok | sem_confirmacao em 10 min, com
      aviso no sino)."""
    agora = agora or datetime.now(UTC)
    out = {"lancadas": 0, "conferidas": 0, "confirmadas": 0, "sem_confirmacao": 0, "paradas": 0}
    a_lancar: list[UUID] = []
    async with session_scope() as s:
        rows = (
            (
                await s.execute(
                    select(EstoqueEntrada)
                    .where(EstoqueEntrada.status.in_((PENDENTE, ENVIANDO, CONFERINDO)))
                    .order_by(EstoqueEntrada.created_at)
                    .with_for_update(skip_locked=True)
                )
            )
            .scalars()
            .all()
        )
        for e in rows:
            if e.status == PENDENTE:
                idade = agora - (e.created_at or agora)
                if idade < _PENDENTE_ORFA:
                    continue
                if idade >= _PENDENTE_VELHA:
                    await _parar(
                        s,
                        e,
                        "O DaVinci parou antes de lançar no Bling: nada entrou. "
                        "Inclua de novo se ainda precisar.",
                    )
                    out["paradas"] += 1
                    continue
                mais_nova = (
                    await s.execute(
                        select(EstoqueEntrada.numero)
                        .where(
                            EstoqueEntrada.id != e.id,
                            EstoqueEntrada.bling_product_id == e.bling_product_id,
                            EstoqueEntrada.quantidade == e.quantidade,
                            EstoqueEntrada.created_at > e.created_at,
                            EstoqueEntrada.status != FALHOU,
                        )
                        .limit(1)
                    )
                ).scalar_one_or_none()
                if mais_nova is not None:
                    await _parar(
                        s,
                        e,
                        f"Não lancei: a entrada #{mais_nova}, igual e mais nova, foi incluída "
                        "depois desta. Nada entrou por esta.",
                    )
                    out["paradas"] += 1
                    continue
                a_lancar.append(e.id)
                continue
            if e.status == ENVIANDO:
                quando = e.tentativa_at or e.updated_at
                if quando is None or agora - quando < _ENVIANDO_ORFA:
                    continue
                e.status = CONFERINDO
            out["conferidas"] += 1
            r = await _conferir(s, e, agora)
            if r == OK:
                out["confirmadas"] += 1
            elif r == SEM_CONFIRMACAO:
                out["sem_confirmacao"] += 1
                await _avisar(s, e, f"Entrada #{e.numero}: confira o extrato de {e.sku}", e.erro)
    for entrada_id in a_lancar:
        r = await executar(client, entrada_id)
        out["lancadas"] += 1
        if r == FALHOU:
            async with session_scope() as s:
                e = await s.get(EstoqueEntrada, entrada_id)
                if e is not None:
                    await _avisar(
                        s,
                        e,
                        f"Entrada #{e.numero}: {e.sku} não entrou",
                        f"O Bling recusou a entrada de {e.quantidade} em {e.sku}: nada entrou. "
                        f"{e.erro or ''}".strip(),
                    )
    return out


# ── Tela ──────────────────────────────────────────────────────────────────


def situacao(e: EstoqueEntrada) -> tuple[str, str]:
    """(estado, texto) para a tela. estado: ok | pendente | enviando |
    sem_confirmacao | falhou. "conferindo" (o Bling respondeu erro depois de
    receber) aparece como "enviando": ainda não acabou e não é para incluir de
    novo."""
    q, sku = e.quantidade, e.sku
    if e.status == OK:
        return "ok", TEXTO_OK
    if e.status == FALHOU:
        return "falhou", f"Não lançou — nada entrou no estoque. {e.erro or ''}".strip()
    if e.status == SEM_CONFIRMACAO:
        return "sem_confirmacao", e.erro or (
            f"Confira no extrato do Bling se entrou {q} em {sku} "
            f'(procure "Entrada DaVinci #{e.numero}").'
        )
    if e.status == CONFERINDO:
        return "enviando", (
            f"O Bling respondeu com erro depois de receber a entrada de {q} em {sku}: o DaVinci "
            "está conferindo no extrato se ela entrou (até 10 min) e avisa no sino se precisar "
            "de você. Não inclua de novo."
        )
    if e.status == ENVIANDO:
        return "enviando", f"Lançando a entrada de {q} em {sku} no Bling… Não inclua de novo."
    return "pendente", (
        f"Entrada de {q} em {sku} gravada; o DaVinci lança no Bling em alguns minutos. "
        "Não inclua de novo."
    )
