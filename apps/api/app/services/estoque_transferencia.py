"""Transferir estoque — botão do Controle de Estoque (Eduardo, 05/11/2026).

Troca a loja interna "Centro de Distribuição" do Bling: lá a equipe fazia um
pedido de R$ 0,01 com quantidade positiva (saída) e negativa (entrada), ex.
307266 — sai 200 a004.cd, entra 200 a004.pi. Aqui o DaVinci lança direto no
estoque do Bling (POST /estoques, o mesmo das Devoluções e do robô de
prioridade), sem pedido nenhum — não conta como venda em lugar nenhum.

Regras (decididas com o Eduardo):
- Só admin (o router usa `require_admin`).
- Mesmo produto entre lotes (dg010.ra → dg010.ci) OU troca de produto
  (uaf001m1.220 → uaf001m1.110, dg107.ci → dg108.ci); a tela avisa a troca.
- Origem e destino: produto simples, ativo, com id no Bling. Kit não (o
  POST /estoques num kit não mexe nas peças).
- A origem precisa ter o saldo DISPONÍVEL (virtual, ao vivo no Bling) — o
  físico inclui o que está reservado para pedido em aberto.
- Primeiro a SAÍDA na origem; a ENTRADA no destino só depois que a saída deu
  certo. Falha no meio = a peça falta por alguns minutos, nunca sobra peça
  para vender que não existe.
- A entrada leva o custo do cadastro do DESTINO (sem custo o Bling grava 0 e
  mexe no custo médio).
- Nunca lança duas vezes: cada perna é gravada como "enviando" ANTES do POST;
  erro em que o Bling pode ter gravado (5xx/timeout depois de enviar) vira
  "conferindo" e o worker procura o movimento no extrato (stock_movements)
  antes de qualquer outra coisa. Recusa certa (limite, conexão que nem abriu,
  4xx) na SAÍDA encerra a linha (nada mudou); na ENTRADA o worker tenta de
  novo sozinho (a saída já saiu).
"""

from __future__ import annotations

from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

import httpx
import structlog
from sqlalchemy import func, or_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import session_scope
from app.models import (
    Devolution,
    EstoqueTransferencia,
    EstoqueTransferenciaLinha,
    PrioridadeEstoqueMovimento,
    Product,
)
from app.models.enums import AlertSeverity, AlertType
from app.models.stock_movement import StockMovement
from app.services.alerts import emit_alert
from app.services.devolution_stock_return import (
    _do_post_de_estoque,
    _falha_sem_envio,
)
from app.services.sku_tags import SUFFIX_TAGS

logger = structlog.get_logger()

AGUARDANDO = "aguardando"
PENDENTE = "pendente"
ENVIANDO = "enviando"
OK = "ok"
FALHOU = "falhou"
CONFERINDO = "conferindo"
SEM_CONFIRMACAO = "sem_confirmacao"

MAX_LINHAS = 30
MAX_QUANTIDADE = 100_000
# Entrada recusada com certeza (a saída já saiu): o worker tenta de novo
# sozinho — 1,5 / 3 / 6 / 12 / 24 min, como as Devoluções.
ENTRADA_AUTO_MAX = 5
_AUTO_INTERVALO = timedelta(seconds=90)
# O movimento aparece no espelho em segundos (webhook); a janela é curta para
# um lançamento do mesmo produto minutos depois não confirmar por engano.
_JANELA = timedelta(minutes=3)
CONFERIR_JANELA = timedelta(minutes=10)
# "enviando" parado: o processo caiu no meio do POST — confere no extrato.
_ENVIANDO_ORFA = timedelta(minutes=5)
# Entrada "pendente" (a saída deu certo e o processo caiu antes da entrada).
_PENDENTE_ORFA = timedelta(minutes=1)
# Saída "pendente" que ninguém lançou (o processo caiu entre gravar e lançar).
_SAIDA_PENDENTE_ORFA = timedelta(minutes=10)


class TransferenciaInvalidaError(Exception):
    """Alguma linha não passou na conferência: nada foi lançado."""

    def __init__(self, linhas: list[dict]):
        self.linhas = linhas
        super().__init__("transferencia_invalida")


def norm(sku: str | None) -> str:
    return (sku or "").strip().lower()


def produto_do_sku(sku: str | None) -> str:
    """O SKU sem o sufixo de lote (.ci .pi .ra .sa .sp .us .cd): dg010.ra e
    dg010.ci são o mesmo produto; uaf001m1.220 e uaf001m1.110 não (a voltagem
    não é lote) — aí é troca de produto."""
    s = norm(sku)
    partes = s.rsplit(".", 1)
    if len(partes) == 2 and partes[1] in SUFFIX_TAGS:
        return partes[0]
    return s


def _simples_ativo():
    return [
        or_(Product.situacao == "A", Product.situacao.is_(None)),
        or_(Product.formato == "S", Product.formato.is_(None)),
        ~Product.sku.contains("+"),
        Product.bling_product_id.is_not(None),
    ]


async def produtos_por_sku(session: AsyncSession, skus: set[str]) -> dict[str, Product]:
    """SKU (minúsculo) → produto simples ativo com id no Bling. O ativo vence
    o de situação desconhecida quando há duas linhas do mesmo SKU."""
    chaves = {norm(s) for s in skus if norm(s)}
    if not chaves:
        return {}
    rows = (
        (
            await session.execute(
                select(Product)
                .where(func.lower(func.btrim(Product.sku)).in_(chaves), *_simples_ativo())
                .order_by((Product.situacao == "A").desc().nullslast())
            )
        )
        .scalars()
        .all()
    )
    out: dict[str, Product] = {}
    for p in rows:
        out.setdefault(norm(p.sku), p)
    return out


async def _kits(session: AsyncSession, skus: set[str]) -> set[str]:
    """Os SKUs pedidos que existem mas são kit (formato E ou com '+')."""
    chaves = {norm(s) for s in skus if norm(s)}
    if not chaves:
        return set()
    rows = (
        (
            await session.execute(
                select(func.lower(func.btrim(Product.sku))).where(
                    func.lower(func.btrim(Product.sku)).in_(chaves),
                    or_(Product.formato == "E", Product.sku.contains("+")),
                )
            )
        )
        .scalars()
        .all()
    )
    return set(rows) | {c for c in chaves if "+" in c}


async def buscar_produtos(session: AsyncSession, q: str, limit: int = 20) -> list[dict]:
    """Busca da tela: produto simples ativo por SKU ou nome."""
    termo = (q or "").strip()
    if not termo:
        return []
    like = f"%{termo}%"
    rows = (
        (
            await session.execute(
                select(Product)
                .where(
                    or_(Product.sku.ilike(like), Product.name.ilike(like)),
                    *_simples_ativo(),
                )
                .order_by(
                    (func.lower(Product.sku) == termo.lower()).desc(),
                    func.lower(Product.sku),
                )
                .limit(limit * 2)
            )
        )
        .scalars()
        .all()
    )
    vistos: set[str] = set()
    out = []
    for p in rows:
        k = norm(p.sku)
        if k in vistos:
            continue
        vistos.add(k)
        out.append(
            {
                "sku": p.sku.strip(),
                "nome": p.name,
                "virtual": int(p.stock or 0),
                "fisico": int(p.stock or 0) + int(p.reserved_stock or 0),
            }
        )
        if len(out) >= limit:
            break
    return out


async def saldos_bling(client: Any, ids: set[int]) -> dict[int, tuple[int, int]]:
    """Saldo AO VIVO no Bling: id → (físico, virtual). Id que o Bling não
    devolveu fica de fora (a conferência barra a linha)."""
    out: dict[int, tuple[int, int]] = {}
    lista = sorted(ids)
    for i in range(0, len(lista), 50):
        params = [("idsProdutos[]", str(b)) for b in lista[i : i + 50]]
        r = await client._request("GET", "/estoques/saldos", params=params)
        r.raise_for_status()
        for row in (r.json() or {}).get("data") or []:
            try:
                bid = int(((row.get("produto") or {}).get("id")) or 0)
                fis = row.get("saldoFisicoTotal")
                vir = row.get("saldoVirtualTotal")
                v = int(float(vir if vir is not None else fis))
                f = int(float(fis if fis is not None else vir))
            except (TypeError, ValueError):
                continue
            if bid:
                out[bid] = (f, v)
    return out


def _custo_do_destino(destino: Product, origem: Product) -> Decimal | None:
    """Custo do cadastro do destino (o do Bling primeiro). Destino sem custo e
    mesmo produto (só o lote muda): o da origem."""
    for c in (destino.bling_cost_price, destino.cost_price):
        if c is not None and Decimal(c) > 0:
            return Decimal(c)
    if produto_do_sku(destino.sku) == produto_do_sku(origem.sku):
        for c in (origem.bling_cost_price, origem.cost_price):
            if c is not None and Decimal(c) > 0:
                return Decimal(c)
    return None


async def conferir(session: AsyncSession, client: Any, linhas: list[dict]) -> list[dict]:
    """Confere as linhas SEM lançar nada: produtos, kit, saldo disponível ao
    vivo da origem (somando as linhas da mesma origem). Cada linha volta com
    `erros` (vazio = pode transferir) e os números para a tela."""
    skus = {norm(x.get("origem")) for x in linhas} | {norm(x.get("destino")) for x in linhas}
    produtos = await produtos_por_sku(session, skus)
    kits = await _kits(session, skus)
    ids_origem = {
        int(produtos[norm(x.get("origem"))].bling_product_id)
        for x in linhas
        if norm(x.get("origem")) in produtos
    }
    saldos = await saldos_bling(client, ids_origem) if ids_origem else {}

    pedido_por_origem: dict[str, int] = defaultdict(int)
    for x in linhas:
        try:
            pedido_por_origem[norm(x.get("origem"))] += max(0, int(x.get("quantidade") or 0))
        except (TypeError, ValueError):
            continue

    out = []
    for i, x in enumerate(linhas):
        origem, destino = norm(x.get("origem")), norm(x.get("destino"))
        erros: list[str] = []
        try:
            qtd = int(x.get("quantidade"))
        except (TypeError, ValueError):
            qtd = 0
        if qtd < 1 or qtd > MAX_QUANTIDADE:
            erros.append("Quantidade precisa ser um número inteiro maior que zero.")
        if not origem or not destino:
            erros.append("Preencha a origem e o destino.")
        elif origem == destino:
            erros.append("Origem e destino são o mesmo SKU.")
        po, pd = produtos.get(origem), produtos.get(destino)
        for sku, prod, papel in ((origem, po, "origem"), (destino, pd, "destino")):
            if not sku or prod is not None:
                continue
            if sku in kits:
                erros.append(f"{sku} é kit: kit não tem estoque próprio, transfira as peças.")
            else:
                erros.append(
                    f"{sku} ({papel}) não existe no Bling como produto ativo — "
                    "cadastre o lote no Bling antes."
                )
        fisico = virtual = None
        if po is not None:
            saldo = saldos.get(int(po.bling_product_id))
            if saldo is None:
                erros.append(f"Não deu para ler o saldo de {origem} no Bling agora.")
            else:
                fisico, virtual = saldo
                pedido = pedido_por_origem.get(origem, 0)
                if pedido > virtual:
                    erros.append(
                        f"{origem} tem {virtual} disponível no Bling"
                        + (
                            f" (físico {fisico}, o resto está reservado para pedidos)"
                            if fisico != virtual
                            else ""
                        )
                        + f" e a transferência pede {pedido}."
                    )
        custo = _custo_do_destino(pd, po) if po is not None and pd is not None else None
        out.append(
            {
                "ordem": i,
                "origem": origem,
                "destino": destino,
                "quantidade": qtd,
                "origem_nome": po.name if po is not None else None,
                "destino_nome": pd.name if pd is not None else None,
                "saldo_fisico": fisico,
                "saldo_virtual": virtual,
                "troca_produto": bool(
                    origem and destino and produto_do_sku(origem) != produto_do_sku(destino)
                ),
                "custo": float(custo) if custo is not None else None,
                "erros": erros,
                "_origem_id": int(po.bling_product_id) if po is not None else None,
                "_destino_id": int(pd.bling_product_id) if pd is not None else None,
            }
        )
    return out


async def por_idem(session: AsyncSession, idem_key: str) -> EstoqueTransferencia | None:
    return (
        await session.execute(
            select(EstoqueTransferencia).where(EstoqueTransferencia.idem_key == idem_key)
        )
    ).scalar_one_or_none()


async def criar(
    session: AsyncSession,
    client: Any,
    *,
    idem_key: str,
    user_id: UUID | None,
    user_nome: str | None,
    observacao: str | None,
    linhas: list[dict],
) -> tuple[EstoqueTransferencia, bool]:
    """Grava a transferência (sem lançar). Mesma `idem_key` = a mesma
    transferência de antes (`False` no 2º item): a tela repetiu o POST.
    Linha que não passa na conferência → TransferenciaInvalidaError e nada gravado."""
    existente = await por_idem(session, idem_key)
    if existente is not None:
        return existente, False
    conferidas = await conferir(session, client, linhas)
    if any(c["erros"] for c in conferidas):
        raise TransferenciaInvalidaError(
            [{k: v for k, v in c.items() if not k.startswith("_")} for c in conferidas]
        )
    t = EstoqueTransferencia(
        idem_key=idem_key,
        criado_por=user_id,
        criado_por_nome=(user_nome or "").strip() or None,
        observacao=(observacao or "").strip()[:500] or None,
    )
    session.add(t)
    try:
        await session.flush()
    except IntegrityError:
        # Dois POSTs com a mesma chave ao mesmo tempo: fica o primeiro.
        await session.rollback()
        existente = await por_idem(session, idem_key)
        if existente is None:
            raise
        return existente, False
    for c in conferidas:
        session.add(
            EstoqueTransferenciaLinha(
                transferencia_id=t.id,
                ordem=c["ordem"],
                origem_sku=c["origem"],
                origem_bling_id=c["_origem_id"],
                destino_sku=c["destino"],
                destino_bling_id=c["_destino_id"],
                quantidade=c["quantidade"],
                custo=Decimal(str(c["custo"])) if c["custo"] is not None else None,
                saida_status=PENDENTE,
                entrada_status=AGUARDANDO,
            )
        )
    await session.commit()
    await session.refresh(t)
    return t, True


# ── Lançamento ────────────────────────────────────────────────────────────


def _classificar(exc: Exception) -> str:
    """FALHOU = o Bling com certeza não gravou (pode repetir); CONFERINDO = pode
    ter gravado. Na dúvida, CONFERINDO — repetir às cegas dobraria o
    lançamento. O 503 do POST não é repetido (`repetir_503=False`) e cai aqui
    como dúvida: o Bling pode ter gravado antes de responder."""
    if _falha_sem_envio(exc):
        return FALHOU
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        if code >= 500 and _do_post_de_estoque(exc):
            return CONFERINDO
        return FALHOU  # 4xx, ou erro de outra chamada (renovar o token) antes de enviar
    if isinstance(exc, httpx.ConnectError | httpx.ConnectTimeout | httpx.PoolTimeout):
        return FALHOU
    return CONFERINDO


def texto_erro(exc: Exception, perna: str) -> str:
    oque = "a saída da origem" if perna == "saida" else "a entrada no destino"
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
    if _falha_sem_envio(exc):
        return f"O Bling estava ocupado (limite de chamadas ou conexão) e recusou {oque}."
    return f"O Bling não respondeu direito sobre {oque}: {str(exc)[:160]}"


def observacao_bling(t: EstoqueTransferencia, linha: EstoqueTransferenciaLinha) -> str:
    quem = f" · {t.criado_por_nome}" if t.criado_por_nome else ""
    motivo = f" · {t.observacao[:120]}" if t.observacao else ""
    return (
        f"Transferência DaVinci #{t.numero}: {linha.origem_sku} -> {linha.destino_sku}"
        f" ({linha.quantidade}){quem}{motivo}"
    )


def _texto_saida_duvidosa(t: EstoqueTransferencia, linha: EstoqueTransferenciaLinha) -> str:
    return (
        f"O Bling respondeu com erro depois de receber a saída de {linha.quantidade} em "
        f"{linha.origem_sku}: pode ter saído ou não. Confira o extrato do SKU no Bling "
        f'(procure "Transferência DaVinci #{t.numero}") e use "Está no extrato" ou '
        '"Não está — lançar de novo". A entrada no destino só sai depois disso.'
    )


@asynccontextmanager
async def _trava_origem(pid: int):
    """Uma SAÍDA por produto de origem por vez — clique, worker e "tentar de
    novo": o saldo lido logo antes vale até o POST acabar, e duas
    transferências da mesma origem ao mesmo tempo não passam as duas."""
    chave = {"k": f"estoque_transferencia:{int(pid)}"}
    async with session_scope() as s:
        await s.execute(text("SET LOCAL lock_timeout = '90s'"))
        await s.execute(text("SELECT pg_advisory_lock(hashtext(:k))"), chave)
        try:
            yield
        finally:
            await s.execute(text("SELECT pg_advisory_unlock(hashtext(:k))"), chave)


async def _marcar_enviando(
    linha_id: UUID, perna: str, permitidos: tuple[str, ...]
) -> datetime | None:
    """Trava a perna ANTES do POST (transação própria, já commitada): um 2º
    clique, o worker ou outro processo veem "enviando" e não lançam de novo.
    Devolve a marca da tentativa (o `*_tentativa_at`), ou None se a perna não
    estava num estado que permite lançar."""
    agora = datetime.now(UTC)
    async with session_scope() as s:
        linha = (
            await s.execute(
                select(EstoqueTransferenciaLinha)
                .where(EstoqueTransferenciaLinha.id == linha_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if linha is None or getattr(linha, f"{perna}_status") not in permitidos:
            return None
        setattr(linha, f"{perna}_status", ENVIANDO)
        setattr(linha, f"{perna}_tentativa_at", agora)
        setattr(linha, f"{perna}_erro", None)
    return agora


async def _gravar_se(
    linha_id: UUID,
    perna: str,
    *,
    de: tuple[str, ...],
    marca: datetime | None = None,
    **campos: Any,
) -> bool:
    """Grava só se a perna ainda está num dos estados `de` (e, com `marca`, na
    mesma tentativa). Uma resposta atrasada do Bling — o worker já tirou a
    perna de "enviando" — não volta o estado para trás."""
    async with session_scope() as s:
        linha = (
            await s.execute(
                select(EstoqueTransferenciaLinha)
                .where(EstoqueTransferenciaLinha.id == linha_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if linha is None or getattr(linha, f"{perna}_status") not in de:
            logger.warning(
                "estoque_transferencia_gravacao_ignorada", linha=str(linha_id), perna=perna
            )
            return False
        if marca is not None and getattr(linha, f"{perna}_tentativa_at") != marca:
            logger.warning(
                "estoque_transferencia_resposta_atrasada", linha=str(linha_id), perna=perna
            )
            return False
        for k, v in campos.items():
            setattr(linha, k, v)
    return True


async def _lancar_perna(
    client: Any,
    t: EstoqueTransferencia,
    linha: EstoqueTransferenciaLinha,
    perna: str,
    marca: datetime,
) -> str:
    """POST de UMA perna (já marcada "enviando" com `marca`) e grava o
    resultado. Saída em dúvida vai direto para a pessoa conferir no extrato:
    o espelho do extrato não traz a observação, então uma venda do mesmo SKU
    passaria por ela — e a entrada sairia sem a saída (peça sobrando)."""
    saida = perna == "saida"
    try:
        await client.update_stock_by_id(
            int(linha.origem_bling_id if saida else linha.destino_bling_id),
            int(linha.quantidade),
            operation="S" if saida else "E",
            observacao=observacao_bling(t, linha),
            custo=None if saida else (float(linha.custo) if linha.custo else None),
            repetir_503=False,
        )
    except Exception as exc:  # noqa: BLE001 — classificado abaixo
        status = _classificar(exc)
        erro = texto_erro(exc, perna)
        if saida and status == CONFERINDO:
            status, erro = SEM_CONFIRMACAO, _texto_saida_duvidosa(t, linha)
        campos: dict[str, Any] = {f"{perna}_status": status, f"{perna}_erro": erro}
        if not saida and status == FALHOU:
            campos["entrada_auto_tentativas"] = int(linha.entrada_auto_tentativas or 0) + 1
        await _gravar_se(linha.id, perna, de=(ENVIANDO,), marca=marca, **campos)
        logger.warning(
            "estoque_transferencia_perna_erro",
            transferencia=t.numero,
            linha=str(linha.id),
            perna=perna,
            status=status,
            erro=str(exc)[:200],
        )
        if status == SEM_CONFIRMACAO:
            async with session_scope() as s:
                await _avisar(
                    s,
                    t,
                    linha,
                    perna,
                    marca,
                    f"Transferência #{t.numero}: confira o extrato de {linha.origem_sku}",
                    erro,
                )
        return status
    campos = {
        f"{perna}_status": OK,
        f"{perna}_lancada_at": datetime.now(UTC),
        f"{perna}_erro": None,
    }
    if saida:
        campos["entrada_status"] = PENDENTE
    await _gravar_se(linha.id, perna, de=(ENVIANDO,), marca=marca, **campos)
    logger.info(
        "estoque_transferencia_perna_ok",
        transferencia=t.numero,
        perna=perna,
        sku=linha.origem_sku if saida else linha.destino_sku,
        qtd=linha.quantidade,
    )
    return OK


async def _carregar(
    linha_id: UUID,
) -> tuple[EstoqueTransferencia, EstoqueTransferenciaLinha] | None:
    async with session_scope() as s:
        linha = await s.get(EstoqueTransferenciaLinha, linha_id)
        if linha is None:
            return None
        t = await s.get(EstoqueTransferencia, linha.transferencia_id)
        s.expunge(linha)
        s.expunge(t)
        return t, linha


async def _lancar_saida(
    client: Any,
    t: EstoqueTransferencia,
    linha: EstoqueTransferenciaLinha,
    permitidos: tuple[str, ...],
) -> str | None:
    """Saída com o saldo DISPONÍVEL conferido de novo, ao vivo, sob a trava do
    produto de origem (o saldo pode ter mudado desde o "Conferir": venda,
    outra transferência, "tentar de novo" horas depois). Sem saldo, a linha
    fica recusada e nada sai."""
    pid = int(linha.origem_bling_id)
    entrou = False
    try:
        async with _trava_origem(pid):
            entrou = True
            try:
                saldo = await saldos_bling(client, {pid})
            except Exception:  # noqa: BLE001 — leitura do saldo no Bling
                await _gravar_se(
                    linha.id,
                    "saida",
                    de=permitidos,
                    saida_status=FALHOU,
                    saida_erro="Não deu para ler o saldo da origem no Bling na hora de lançar.",
                )
                return FALHOU
            virtual = (saldo.get(pid) or (None, None))[1]
            if virtual is None or virtual < int(linha.quantidade):
                await _gravar_se(
                    linha.id,
                    "saida",
                    de=permitidos,
                    saida_status=FALHOU,
                    saida_erro=(
                        f"Na hora de lançar, {linha.origem_sku} tinha "
                        f"{virtual if virtual is not None else 'saldo desconhecido'} disponível "
                        f"no Bling e a linha pede {linha.quantidade}."
                    ),
                )
                return FALHOU
            marca = await _marcar_enviando(linha.id, "saida", permitidos)
            if marca is None:
                return None
            return await _lancar_perna(client, t, linha, "saida", marca)
    except Exception:
        if entrou:
            raise
        # A trava não veio em 90 s: outra saída do mesmo produto travada.
        await _gravar_se(
            linha.id,
            "saida",
            de=permitidos,
            saida_status=FALHOU,
            saida_erro="Outra transferência do mesmo SKU estava lançando agora.",
        )
        return FALHOU


async def executar_linha(
    client: Any,
    linha_id: UUID,
    *,
    saida_permitidos: tuple[str, ...] = (PENDENTE,),
    entrada_permitidos: tuple[str, ...] = (PENDENTE,),
) -> None:
    """Saída e, se ela deu certo, entrada. Cada perna só sai se estiver num
    dos estados permitidos (a trava "enviando" impede lançamento em dobro)."""
    par = await _carregar(linha_id)
    if par is None:
        return
    t, linha = par
    if linha.saida_status != OK:
        if linha.saida_status not in saida_permitidos:
            return
        if await _lancar_saida(client, t, linha, saida_permitidos) != OK:
            return
        entrada_permitidos = (*entrada_permitidos, PENDENTE)
        par = await _carregar(linha_id)
        if par is None:
            return
        t, linha = par
    marca = await _marcar_enviando(linha_id, "entrada", entrada_permitidos)
    if marca is not None:
        await _lancar_perna(client, t, linha, "entrada", marca)


async def executar(client: Any, transferencia_id: UUID) -> None:
    """Lança as linhas em ordem (cada uma: saída e depois entrada)."""
    async with session_scope() as s:
        ids = (
            (
                await s.execute(
                    select(EstoqueTransferenciaLinha.id)
                    .where(EstoqueTransferenciaLinha.transferencia_id == transferencia_id)
                    .order_by(EstoqueTransferenciaLinha.ordem)
                )
            )
            .scalars()
            .all()
        )
    for linha_id in ids:
        await executar_linha(client, linha_id)


# ── Conferência da ENTRADA no extrato e tentativas (worker) ──────────────


async def _conferir_entrada(
    session: AsyncSession, linha: EstoqueTransferenciaLinha, agora: datetime
) -> str | None:
    """Entrada "conferindo": procura o E no espelho do extrato a partir da
    tentativa (movimento de antes do POST nunca é dela). Confirma só quando
    sobra candidato depois de descontar quem mais pode ser dono de uma entrada
    igual (outra transferência, devolução, robô de prioridade). Erro aqui é o
    lado seguro — a peça falta, não sobra. 10 min sem achar: sem_confirmacao."""
    pid = int(linha.destino_bling_id)
    qtd = int(linha.quantidade)
    t0 = linha.entrada_tentativa_at or linha.updated_at or agora
    de, ate = t0, t0 + _JANELA
    tl = EstoqueTransferenciaLinha

    usados_transf = (
        (
            await session.execute(
                select(tl.saida_mov_ref)
                .where(tl.saida_mov_ref.is_not(None))
                .union(select(tl.entrada_mov_ref).where(tl.entrada_mov_ref.is_not(None)))
            )
        )
        .scalars()
        .all()
    )
    usados_dev = (
        (
            await session.execute(
                select(Devolution.estoque_mov_ref).where(Devolution.estoque_mov_ref.is_not(None))
            )
        )
        .scalars()
        .all()
    )
    usados = {u for u in (*usados_transf, *usados_dev) if u is not None}
    # Donos possíveis de um E igual, tentados um pouco antes (o movimento
    # deles pode cair dentro da janela desta) ou dentro dela.
    de_dono = t0 - _JANELA
    n_transf = (
        await session.execute(
            select(func.count())
            .select_from(tl)
            .where(
                tl.id != linha.id,
                tl.destino_bling_id == pid,
                tl.quantidade == qtd,
                tl.entrada_status.in_((OK, CONFERINDO, ENVIANDO)),
                tl.entrada_mov_ref.is_(None),
                tl.entrada_tentativa_at.between(de_dono, ate),
            )
        )
    ).scalar_one()
    quando = func.coalesce(Devolution.estoque_tentativa_at, Devolution.data_devolvido_estoque)
    n_dev = (
        await session.execute(
            select(func.count())
            .select_from(Devolution)
            .where(
                Devolution.estoque_mov_ref.is_(None),
                or_(
                    Devolution.estoque_mov_bling_id == pid,
                    Devolution.estoque_conferir_bling_id == pid,
                ),
                quando.between(de_dono, ate),
            )
        )
    ).scalar_one()
    robo_quando = func.coalesce(
        PrioridadeEstoqueMovimento.lancado_at, PrioridadeEstoqueMovimento.updated_at
    )
    n_robo = (
        await session.execute(
            select(func.count())
            .select_from(PrioridadeEstoqueMovimento)
            .where(
                PrioridadeEstoqueMovimento.bling_product_id == pid,
                PrioridadeEstoqueMovimento.operacao == "E",
                PrioridadeEstoqueMovimento.quantidade == qtd,
                PrioridadeEstoqueMovimento.status.in_(("ok", "incerto")),
                robo_quando.between(de_dono, ate),
            )
        )
    ).scalar_one()
    candidatos = [
        c
        for c in (
            await session.execute(
                select(StockMovement.id)
                .where(
                    StockMovement.bling_product_id == pid,
                    StockMovement.tipo == "E",
                    StockMovement.quantidade == qtd,
                    StockMovement.date.between(de, ate),
                )
                .order_by(StockMovement.date.asc())
            )
        )
        .scalars()
        .all()
        if c not in usados
    ]
    if len(candidatos) > int(n_transf) + int(n_dev) + int(n_robo):
        linha.entrada_status = OK
        linha.entrada_mov_ref = candidatos[0]
        linha.entrada_lancada_at = agora
        linha.entrada_erro = None
        return OK
    if agora - t0 >= CONFERIR_JANELA:
        linha.entrada_status = SEM_CONFIRMACAO
        linha.entrada_erro = (
            f"O Bling respondeu com erro e a entrada de {qtd} em {linha.destino_sku} não "
            "apareceu no extrato em 10 min. Confira o extrato do SKU no Bling antes de "
            "lançar de novo."
        )
        return SEM_CONFIRMACAO
    return None


async def _avisar(
    session: AsyncSession,
    t: EstoqueTransferencia,
    linha: EstoqueTransferenciaLinha,
    perna: str,
    marca: datetime | None,
    titulo: str,
    msg: str,
) -> None:
    """Sino de quem transferiu (best-effort, savepoint). Um aviso por tentativa
    da perna — a 2ª vez que ela trava também avisa."""
    if t.criado_por is None:
        return
    quando = marca.isoformat() if marca else "-"
    try:
        async with session.begin_nested():
            await emit_alert(
                session,
                user_id=t.criado_por,
                type=AlertType.GENERIC,
                severity=AlertSeverity.WARNING,
                title=titulo,
                message=msg,
                dedupe_key=f"estoque_transferencia:{linha.id}:{perna}:{quando}",
                payload={"transferencia": t.numero, "linha_id": str(linha.id)},
            )
    except Exception:  # noqa: BLE001
        logger.warning("estoque_transferencia_aviso_falhou", linha=str(linha.id))


async def processar_pendentes(client: Any, *, agora: datetime | None = None) -> dict[str, int]:
    """Worker (a cada 2 min). Sem pendência = 1 SELECT e zero Bling.
    - saída "pendente" há 10 min (o processo caiu antes de lançar): recusada,
      nada saiu — a pessoa usa "Tentar de novo" (que confere o saldo de novo);
    - saída "enviando" há 5 min (caiu no meio do POST): vai para a pessoa
      conferir no extrato — o worker nunca confirma saída sozinho;
    - entrada "enviando" parada → "conferindo"; "conferindo" → procura no
      extrato (ok | sem_confirmacao em 10 min);
    - entrada pendente/recusada (a saída já saiu) → lança de novo, até
      ENTRADA_AUTO_MAX vezes; depois avisa e fica para a pessoa."""
    agora = agora or datetime.now(UTC)
    tl = EstoqueTransferenciaLinha
    out = {"conferidas": 0, "confirmadas": 0, "sem_confirmacao": 0, "entradas": 0, "paradas": 0}
    a_lancar: list[UUID] = []
    async with session_scope() as s:
        linhas = (
            (
                await s.execute(
                    select(tl)
                    .where(
                        or_(
                            tl.saida_status.in_((PENDENTE, ENVIANDO, CONFERINDO)),
                            tl.entrada_status.in_((ENVIANDO, CONFERINDO, PENDENTE, FALHOU)),
                        )
                    )
                    .order_by(tl.created_at)
                    .with_for_update(skip_locked=True)
                )
            )
            .scalars()
            .all()
        )
        for linha in linhas:
            t = await s.get(EstoqueTransferencia, linha.transferencia_id)
            st = linha.saida_status
            if st != OK:
                quando = linha.saida_tentativa_at or linha.updated_at or linha.created_at
                if st == PENDENTE and quando is not None and agora - quando >= _SAIDA_PENDENTE_ORFA:
                    linha.saida_status = FALHOU
                    linha.saida_erro = (
                        'O DaVinci parou antes de lançar a saída. Use "Tentar de novo".'
                    )
                    out["paradas"] += 1
                elif (
                    st == ENVIANDO and quando is not None and agora - quando >= _ENVIANDO_ORFA
                ) or (st == CONFERINDO):
                    linha.saida_status = SEM_CONFIRMACAO
                    linha.saida_erro = _texto_saida_duvidosa(t, linha)
                    out["sem_confirmacao"] += 1
                    await _avisar(
                        s,
                        t,
                        linha,
                        "saida",
                        linha.saida_tentativa_at,
                        f"Transferência #{t.numero}: confira o extrato de {linha.origem_sku}",
                        linha.saida_erro,
                    )
                continue
            st = linha.entrada_status
            quando = linha.entrada_tentativa_at or linha.updated_at
            if st == ENVIANDO and quando is not None and agora - quando >= _ENVIANDO_ORFA:
                linha.entrada_status = st = CONFERINDO
            if st == CONFERINDO:
                out["conferidas"] += 1
                r = await _conferir_entrada(s, linha, agora)
                if r == OK:
                    out["confirmadas"] += 1
                elif r == SEM_CONFIRMACAO:
                    out["sem_confirmacao"] += 1
                    await _avisar(
                        s,
                        t,
                        linha,
                        "entrada",
                        linha.entrada_tentativa_at,
                        f"Transferência #{t.numero}: confira o extrato de {linha.destino_sku}",
                        linha.entrada_erro or "",
                    )
            elif st == PENDENTE:
                ref = linha.saida_lancada_at or linha.updated_at
                if ref is None or agora - ref >= _PENDENTE_ORFA:
                    a_lancar.append(linha.id)
            elif st == FALHOU:
                n = int(linha.entrada_auto_tentativas or 0)
                if n >= ENTRADA_AUTO_MAX:
                    await _avisar(
                        s,
                        t,
                        linha,
                        "entrada",
                        linha.entrada_tentativa_at,
                        f"Transferência #{t.numero}: {linha.destino_sku} não entrou",
                        f"Saiu {linha.quantidade} de {linha.origem_sku}, mas o Bling recusou "
                        f'a entrada em {linha.destino_sku} {n} vezes. Use "Tentar de novo" '
                        'ou, se lançou à mão no Bling, "Já lancei no Bling".',
                    )
                    continue
                espera = _AUTO_INTERVALO * (2 ** max(0, n - 1))
                ref = linha.entrada_tentativa_at or linha.updated_at
                if ref is None or agora - ref >= espera:
                    a_lancar.append(linha.id)
    for linha_id in a_lancar:
        await executar_linha(client, linha_id, entrada_permitidos=(PENDENTE, FALHOU))
        out["entradas"] += 1
    return out


# ── Resolução à mão (a pessoa conferiu o extrato) ────────────────────────


class AcaoInvalidaError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(code)


def perna_parada(linha: EstoqueTransferenciaLinha) -> str:
    return "saida" if linha.saida_status != OK else "entrada"


async def resolver(
    session: AsyncSession,
    client: Any,
    linha_id: UUID,
    *,
    acao: str,
    conferi_extrato: bool,
    quem: str,
    tentativa: datetime | None = None,
) -> None:
    """`ja_lancou`: a pessoa viu o movimento no extrato do Bling (ou lançou à
    mão uma entrada recusada) — marca ok, sem lançar; se era a saída, segue
    para a entrada. `tentar_de_novo`: lança de novo a perna parada (a saída
    confere o saldo de novo). Perna sem confirmação exige `conferi_extrato`, e
    a `tentativa` que a pessoa viu tem de ser a atual — senão uma aba antiga
    lançaria de novo depois de uma tentativa mais nova."""
    linha = (
        await session.execute(
            select(EstoqueTransferenciaLinha)
            .where(EstoqueTransferenciaLinha.id == linha_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if linha is None:
        raise AcaoInvalidaError("linha_nao_encontrada", "Linha não encontrada.")
    perna = perna_parada(linha)
    st = getattr(linha, f"{perna}_status")
    atual = getattr(linha, f"{perna}_tentativa_at")
    if (
        tentativa is not None
        and atual is not None
        and abs((atual - tentativa).total_seconds()) > 0.001
    ):
        raise AcaoInvalidaError(
            "linha_mudou", "A linha mudou desde que a tela foi aberta. Atualize a lista."
        )
    if st in (PENDENTE, ENVIANDO, CONFERINDO, AGUARDANDO):
        raise AcaoInvalidaError(
            "em_andamento", "O DaVinci ainda está lançando ou conferindo no Bling (até 10 min)."
        )
    if st == SEM_CONFIRMACAO and not conferi_extrato:
        raise AcaoInvalidaError(
            "conferir_extrato",
            "Confira o extrato do SKU no Bling antes: se o movimento está lá, use "
            '"Está no extrato"; se não está, confirme que conferiu para lançar de novo.',
        )
    agora = datetime.now(UTC)
    if acao == "ja_lancou":
        pode = st == SEM_CONFIRMACAO or (perna == "entrada" and st == FALHOU and conferi_extrato)
        if not pode:
            raise AcaoInvalidaError(
                "nada_a_resolver",
                'Esta saída foi recusada pelo Bling: nada saiu. Use "Tentar de novo".',
            )
        setattr(linha, f"{perna}_status", OK)
        setattr(linha, f"{perna}_lancada_at", agora)
        setattr(linha, f"{perna}_erro", None)
        linha.resolvido_manual = f"{perna} marcada como lançada por {quem} em {agora:%d/%m %H:%M}"
        if perna == "saida":
            linha.entrada_status = PENDENTE
        await session.commit()
        if perna == "saida":
            await executar_linha(client, linha.id)
        return
    if acao != "tentar_de_novo":
        raise AcaoInvalidaError("acao_desconhecida", "Ação desconhecida.")
    if st not in (FALHOU, SEM_CONFIRMACAO):
        raise AcaoInvalidaError("nada_a_resolver", "Esta perna não está parada.")
    setattr(linha, f"{perna}_status", PENDENTE)
    linha.resolvido_manual = f"{perna} tentada de novo por {quem} em {agora:%d/%m %H:%M}"
    if perna == "entrada":
        linha.entrada_auto_tentativas = 0
    await session.commit()
    await executar_linha(client, linha.id)


# ── Tela ──────────────────────────────────────────────────────────────────


def situacao_da_linha(linha: EstoqueTransferenciaLinha) -> tuple[str, str]:
    """(estado, texto) para a tela. estado: ok | andamento | falhou | atencao."""
    s, e = linha.saida_status, linha.entrada_status
    q, o, d = linha.quantidade, linha.origem_sku, linha.destino_sku
    if s == OK and e == OK:
        return "ok", f"Transferido: saiu {q} de {o} e entrou {q} em {d}."
    if s == FALHOU:
        return "falhou", f"Não transferiu — nada mudou no estoque. {linha.saida_erro or ''}".strip()
    if s == SEM_CONFIRMACAO:
        return "atencao", linha.saida_erro or f"Confira no extrato se saiu {q} de {o}."
    if s != OK:
        return "andamento", f"Lançando a saída de {o} no Bling…"
    if e == SEM_CONFIRMACAO:
        return "atencao", (
            f"Saiu {q} de {o}. " + (linha.entrada_erro or f"Confira no extrato se entrou em {d}.")
        )
    if e == FALHOU:
        n = int(linha.entrada_auto_tentativas or 0)
        if n >= ENTRADA_AUTO_MAX:
            return "atencao", (
                f"Saiu {q} de {o}, mas o Bling recusou a entrada em {d} {n} vezes. "
                f"{linha.entrada_erro or ''}".strip()
            )
        return "andamento", (
            f"Saiu {q} de {o}; a entrada em {d} foi recusada e o DaVinci tenta de novo "
            "sozinho em alguns minutos."
        )
    return "andamento", f"Saiu {q} de {o}; entrando em {d}…"
