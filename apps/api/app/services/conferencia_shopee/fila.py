"""A fila da Conferência Shopee: execução → uma coleta por loja → relatório.

O ciclo de uma rodada (contrato §3, §5 e §7):

  1. `criar_execucao` — agenda (terça/quinta) ou "Gerar agora". Fixa as 4
     semanas e os prazos (periodos.planejar) e põe uma coleta `pendente` por
     loja ATIVA, com a foto do nome/grupo/perfil daquele momento. Uma rodada
     coletando por vez: outra → `conferencia_em_andamento` (a agenda só pula).
  2. `lease` — o executor do Mac pede UMA loja: a coleta mais antiga
     `pendente` (ou `coletando` largada há mais de 20 min — o Mac caiu) cuja
     execução ainda coleta, já liberada (`disponivel_apos`) e antes do
     `corte`. FOR UPDATE SKIP LOCKED: dois executores nunca pegam a mesma.
     Coleta que já gastou 3 tentativas não volta: vira `erro`
     ("muitas tentativas").
  3. `registrar_resultado` — o executor devolve status + números.
     `perfil_em_uso` (alguém está com o perfil aberto) e
     `aguardando_afiliados` (a Shopee ainda não publicou ontem) voltam para a
     fila em 10 min sem gastar tentativa; perfil em uso pela 4ª vez, ou
     qualquer um dos dois depois do corte, é final. A cota de 3 é só do
     perfil em uso (`adiamentos_perfil`): esperar os afiliados a manhã toda
     não gasta as voltas de quem achou o perfil aberto. O resto é final:
     guarda os `dados` e o saldo de Ads lido (`conferencia_shopee_saldo`).
  4. `fechar_se_terminou` — sem coleta na fila, calcula o relatório
     (calculo.montar_relatorio) e o congela na execução (`pronto`). Quem
     chama é a rota do resultado (na hora) e o varredor (rede de segurança).
     Se os `dados` de uma loja derrubam o cálculo (executor com defeito), só
     ela perde os números (vira `erro`) e a rodada fecha com o resto.
  5. `varrer` — de 10 em 10 min no worker: execução que passou do `prazo`
     tem o que sobrou marcado `expirada` e fecha com o que chegou.

Travas: quem mexe em várias coletas de uma execução (varredor, cancelar,
fechar) trava ANTES a linha da execução; o lease e o resultado travam só a
coleta. Nada aqui faz commit: quem chama decide (rota ou worker).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import and_, func, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    ConferenciaShopeeColeta,
    ConferenciaShopeeConta,
    ConferenciaShopeeExecucao,
    ConferenciaShopeeSaldo,
)
from app.models.conferencia_shopee import CONFERENCIA_ORIGENS
from app.services.conferencia_shopee import calculo, classificacao, periodos

logger = structlog.get_logger()

# Coleta largada em `coletando` por mais que isso volta para a fila (o Mac
# dormiu, o executor caiu no meio da loja).
LEASE_STALE = timedelta(minutes=20)
MAX_TENTATIVAS = 3
# `perfil_em_uso` volta para a fila até 3 vezes; na 4ª é final. Conta só os
# adiamentos por perfil em uso (`adiamentos_perfil`), não os de afiliados.
MAX_ADIAMENTOS = 3
ADIAMENTO = timedelta(minutes=10)

NA_FILA = ("pendente", "coletando")
# Voltam para a fila (sem gastar tentativa) em vez de encerrar a loja.
REAGENDAR = ("perfil_em_uso", "aguardando_afiliados")
# O que o executor pode mandar. `expirada` é só do varredor; `pendente` e
# `coletando` são da fila.
FINAIS_DO_AGENTE = (
    "ok",
    "parcial",
    "deslogada",
    "perfil_em_uso",
    "sem_automacao",
    "bloqueada",
    "interrompida",
    "erro",
)
STATUS_DO_AGENTE = (*FINAIS_DO_AGENTE, "aguardando_afiliados")

ERRO_MUITAS_TENTATIVAS = "muitas tentativas"
ERRO_AFILIADOS_NO_CORTE = "a Shopee não publicou os afiliados até o corte"
ERRO_PRAZO = "não coletada até o prazo"
ERRO_CANCELADA = "conferência cancelada"
ERRO_DADOS_INVALIDOS = "os números desta loja vieram num formato que o relatório não lê"

_TRAVA_CRIAR = "conferencia_shopee_criar"
_LISTAS_DE_ITENS = ("afiliados_itens", "ads_itens", "vendas_itens")


class FilaError(Exception):
    """Erro de regra da fila; `code` vai para o `detail` da rota."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


# ───────────────────────────────────────────────────────────── criar


async def criar_execucao(
    session: AsyncSession,
    tipo: str,
    origem: str,
    criado_por: str | None,
    agora: datetime,
) -> ConferenciaShopeeExecucao:
    """Uma rodada nova com uma coleta `pendente` por loja ativa.

    `conferencia_em_andamento` se outra ainda coleta (a trava de transação
    impede dois cliques simultâneos de criarem duas); `conferencia_sem_contas`
    se nenhuma loja está ativa."""
    if tipo not in periodos.TIPOS:
        raise FilaError("tipo_invalido")
    if origem not in CONFERENCIA_ORIGENS:
        raise FilaError("origem_invalida")
    await session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": _TRAVA_CRIAR})
    andando = (
        await session.execute(
            select(ConferenciaShopeeExecucao.id)
            .where(ConferenciaShopeeExecucao.status == "coletando")
            .limit(1)
        )
    ).scalar_one_or_none()
    if andando is not None:
        raise FilaError("conferencia_em_andamento")
    contas = (
        await session.execute(
            select(ConferenciaShopeeConta).where(ConferenciaShopeeConta.ativo.is_(True))
        )
    ).scalars().all()
    if not contas:
        raise FilaError("conferencia_sem_contas")

    plano = periodos.planejar(tipo, agora)
    execucao = ConferenciaShopeeExecucao(
        tipo=plano["tipo"],
        origem=origem,
        criado_por=criado_por,
        semanas=plano["semanas"],
        afiliados_ate=plano["afiliados_ate"],
        esperar_afiliados_ate=plano["esperar_afiliados_ate"],
        corte=plano["corte"],
        prazo=plano["prazo"],
        status="coletando",
        criado_em=agora,
    )
    session.add(execucao)
    await session.flush()
    # A fila sai na ordem da lista de lojas (ordem, nome): o `criado_em` de
    # cada coleta anda 1 µs, e o lease pega a mais antiga.
    ordenadas = sorted(contas, key=lambda c: (c.ordem, classificacao.sem_acento(c.nome)))
    for i, conta in enumerate(ordenadas):
        session.add(
            ConferenciaShopeeColeta(
                execucao_id=execucao.id,
                conta_id=conta.id,
                adspower_user_id=conta.adspower_user_id,
                nome=conta.nome,
                grupo=conta.grupo,
                status="pendente",
                criado_em=agora + timedelta(microseconds=i),
            )
        )
    await session.flush()
    logger.info(
        "conferencia_shopee_criada",
        execucao=str(execucao.id),
        tipo=execucao.tipo,
        origem=origem,
        contas=len(ordenadas),
    )
    return execucao


# ───────────────────────────────────────────────────────────── lease


def _iso_utc(quando: datetime) -> str:
    return quando.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def job(
    coleta: ConferenciaShopeeColeta, execucao: ConferenciaShopeeExecucao, login_auto: bool
) -> dict[str, Any]:
    """O Job do contrato §3 — o que o executor precisa para abrir a loja."""
    return {
        "coleta_id": str(coleta.id),
        "execucao_id": str(execucao.id),
        "conta": coleta.nome,
        "adspower_user_id": coleta.adspower_user_id,
        "grupo": coleta.grupo,
        "semanas": [{"inicio": s["inicio"], "fim": s["fim"]} for s in execucao.semanas],
        "afiliados_ate": execucao.afiliados_ate.isoformat(),
        "esperar_afiliados_ate": _iso_utc(execucao.esperar_afiliados_ate),
        "corte": _iso_utc(execucao.corte),
        "login_auto": bool(login_auto),
        "tentativa": coleta.tentativas,
    }


async def lease(
    session: AsyncSession, agente: str, agora: datetime, *, login_auto: bool = True
) -> tuple[dict[str, Any] | None, set[UUID]]:
    """(job | None, execuções com coleta encerrada por "muitas tentativas").

    A segunda parte é para quem chama fechar essas execuções DEPOIS do
    commit (fechar_se_terminou trava a execução; aqui só a coleta)."""
    limite_stale = agora - LEASE_STALE
    esgotadas: set[UUID] = set()
    while True:
        linha = (
            await session.execute(
                select(ConferenciaShopeeColeta, ConferenciaShopeeExecucao)
                .join(
                    ConferenciaShopeeExecucao,
                    ConferenciaShopeeExecucao.id == ConferenciaShopeeColeta.execucao_id,
                )
                .where(
                    ConferenciaShopeeExecucao.status == "coletando",
                    ConferenciaShopeeExecucao.corte > agora,
                    or_(
                        ConferenciaShopeeColeta.status == "pendente",
                        and_(
                            ConferenciaShopeeColeta.status == "coletando",
                            or_(
                                ConferenciaShopeeColeta.claimed_at.is_(None),
                                ConferenciaShopeeColeta.claimed_at < limite_stale,
                            ),
                        ),
                    ),
                    or_(
                        ConferenciaShopeeColeta.disponivel_apos.is_(None),
                        ConferenciaShopeeColeta.disponivel_apos <= agora,
                    ),
                )
                .order_by(
                    ConferenciaShopeeColeta.criado_em.asc(), ConferenciaShopeeColeta.id.asc()
                )
                .limit(1)
                # Só a COLETA: travar a execução junto faria o segundo
                # executor pular todas as lojas dela.
                .with_for_update(skip_locked=True, of=ConferenciaShopeeColeta)
                .execution_options(populate_existing=True)
            )
        ).first()
        if linha is None:
            return None, esgotadas
        coleta, execucao = linha
        if coleta.tentativas >= MAX_TENTATIVAS:
            coleta.status = "erro"
            coleta.erro = ERRO_MUITAS_TENTATIVAS
            coleta.concluido_em = agora
            await session.flush()
            esgotadas.add(execucao.id)
            logger.warning(
                "conferencia_shopee_muitas_tentativas",
                coleta=str(coleta.id),
                conta=coleta.nome,
                tentativas=coleta.tentativas,
            )
            continue
        coleta.status = "coletando"
        coleta.claimed_at = agora
        coleta.tentativas += 1
        coleta.agente = (agente or "").strip()[:100] or None
        await session.flush()
        logger.info(
            "conferencia_shopee_lease",
            coleta=str(coleta.id),
            conta=coleta.nome,
            tentativa=coleta.tentativas,
            agente=coleta.agente,
        )
        return job(coleta, execucao, login_auto), esgotadas


# ───────────────────────────────────────────────────────────── resultado


def _erro(texto: str | None) -> str | None:
    t = (texto or "").strip()
    return t[:2000] or None


def _saldo(dados: Mapping | None) -> Decimal | None:
    """`dados.saldo_ads` em R$, se for número (cabe em numeric(14,2))."""
    if not isinstance(dados, Mapping):
        return None
    v = dados.get("saldo_ads")
    if isinstance(v, bool) or not isinstance(v, int | float):
        return None
    v = float(v)
    if v != v or abs(v) >= 1e12:
        return None
    return calculo.meio_para_cima(v, 2)


def _lido_em(dados: Mapping, agora: datetime) -> datetime:
    bruto = dados.get("coletado_em")
    if isinstance(bruto, str) and bruto:
        try:
            q = datetime.fromisoformat(bruto.replace("Z", "+00:00"))
        except ValueError:
            return agora
        return q if q.tzinfo else q.replace(tzinfo=UTC)
    return agora


def _pode_reagendar(
    coleta: ConferenciaShopeeColeta,
    execucao: ConferenciaShopeeExecucao | None,
    status: str,
    agora: datetime,
) -> bool:
    if execucao is None or execucao.status != "coletando" or agora >= execucao.corte:
        return False  # depois do corte nenhuma loja começa: reagendar só a deixaria expirar
    if status == "perfil_em_uso" and coleta.adiamentos_perfil >= MAX_ADIAMENTOS:
        return False
    return True


async def registrar_resultado(
    session: AsyncSession,
    coleta_id: UUID,
    status: str,
    erro: str | None,
    dados: Mapping[str, Any] | None,
    agora: datetime,
) -> ConferenciaShopeeColeta:
    """Grava o que o executor devolveu. Volta a coleta (status já gravado);
    se ela saiu da fila, quem chama faz commit e `fechar_se_terminou`."""
    if status not in STATUS_DO_AGENTE:
        raise FilaError("status_invalido")
    coleta = (
        await session.execute(
            select(ConferenciaShopeeColeta)
            .where(ConferenciaShopeeColeta.id == coleta_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if coleta is None:
        raise FilaError("coleta_nao_encontrada")
    if coleta.status not in NA_FILA:
        # Expirada pelo varredor, cancelada, ou entregue duas vezes.
        raise FilaError("coleta_ja_concluida")
    execucao = await session.get(ConferenciaShopeeExecucao, coleta.execucao_id)
    erro = _erro(erro)

    if status in REAGENDAR and _pode_reagendar(coleta, execucao, status, agora):
        coleta.status = "pendente"
        coleta.disponivel_apos = agora + ADIAMENTO
        coleta.adiamentos += 1
        if status == "perfil_em_uso":
            coleta.adiamentos_perfil += 1
        # Adiar não gasta tentativa.
        coleta.tentativas = max(0, coleta.tentativas - 1)
        coleta.claimed_at = None
        coleta.erro = erro
        await session.flush()
        logger.info(
            "conferencia_shopee_adiada",
            coleta=str(coleta.id),
            conta=coleta.nome,
            motivo=status,
            adiamentos=coleta.adiamentos,
            adiamentos_perfil=coleta.adiamentos_perfil,
        )
        return coleta

    final = status
    if status == "aguardando_afiliados":
        final = "erro"
        erro = erro or ERRO_AFILIADOS_NO_CORTE
    coleta.status = final
    coleta.erro = erro
    coleta.concluido_em = agora
    if isinstance(dados, Mapping):
        coleta.dados = dict(dados)
        valor = _saldo(dados)
        if valor is not None:
            session.add(
                ConferenciaShopeeSaldo(
                    adspower_user_id=coleta.adspower_user_id,
                    lido_em=_lido_em(dados, agora),
                    valor=valor,
                    execucao_id=coleta.execucao_id,
                )
            )
    await session.flush()
    logger.info(
        "conferencia_shopee_resultado",
        coleta=str(coleta.id),
        conta=coleta.nome,
        status=final,
        com_dados=coleta.dados is not None,
    )
    return coleta


# ───────────────────────────────────────────────────────────── relatório


def _itens(dados: Mapping | None) -> set[str]:
    saida: set[str] = set()
    semanas = (dados or {}).get("semanas")
    for semana in semanas if isinstance(semanas, list) else []:
        if not isinstance(semana, Mapping):
            continue
        for lista in _LISTAS_DE_ITENS:
            itens = semana.get(lista)
            for item in itens if isinstance(itens, list) else []:
                if isinstance(item, Mapping) and item.get("item_id") is not None:
                    saida.add(str(item["item_id"]))
    return saida


async def _saldos(
    session: AsyncSession, usuarios: Iterable[str], semanas: list[dict]
) -> dict[str, list[tuple[datetime, float]]]:
    """Leituras de saldo que podem valer para S2..S4: dia (Brasília) entre o
    fim de S4 e o fim de S2 + 2 (a janela de calculo.saldo_da_semana)."""
    usuarios = sorted({u for u in usuarios if u})
    if not usuarios or len(semanas) < 2:
        return {}
    fins = [date.fromisoformat(s["fim"]) for s in semanas[1:]]
    de = datetime.combine(min(fins), time(0), tzinfo=periodos.FUSO)
    ate = datetime.combine(max(fins) + timedelta(days=3), time(0), tzinfo=periodos.FUSO)
    linhas = (
        await session.execute(
            select(
                ConferenciaShopeeSaldo.adspower_user_id,
                ConferenciaShopeeSaldo.lido_em,
                ConferenciaShopeeSaldo.valor,
            ).where(
                ConferenciaShopeeSaldo.adspower_user_id.in_(usuarios),
                ConferenciaShopeeSaldo.lido_em >= de,
                ConferenciaShopeeSaldo.lido_em < ate,
            )
        )
    ).all()
    saida: dict[str, list[tuple[datetime, float]]] = defaultdict(list)
    for usuario, lido_em, valor in linhas:
        saida[usuario].append((lido_em, float(valor)))
    return dict(saida)


async def montar(
    session: AsyncSession, execucao: ConferenciaShopeeExecucao, agora: datetime
) -> dict[str, Any]:
    """O relatório da execução a partir das coletas guardadas (não grava)."""
    linhas = (
        await session.execute(
            select(ConferenciaShopeeColeta, ConferenciaShopeeConta.conta_key)
            .outerjoin(
                ConferenciaShopeeConta,
                ConferenciaShopeeConta.id == ConferenciaShopeeColeta.conta_id,
            )
            .where(ConferenciaShopeeColeta.execucao_id == execucao.id)
            .order_by(ConferenciaShopeeColeta.criado_em, ConferenciaShopeeColeta.id)
            # O UPDATE do _encerrar_fila não passa pelas coletas já na sessão.
            .execution_options(populate_existing=True)
        )
    ).all()
    coletas: list[dict[str, Any]] = []
    chaves_celular: set[str] = set()
    itens: set[str] = set()
    for coleta, conta_key in linhas:
        coletas.append(
            {
                "conta_id": coleta.conta_id,
                "conta_key": conta_key,
                "adspower_user_id": coleta.adspower_user_id,
                "nome": coleta.nome,
                "grupo": coleta.grupo,
                "status": coleta.status,
                "erro": coleta.erro,
                "dados": coleta.dados,
            }
        )
        # Só o Celular separa eletro; Mala nem consulta o vínculo.
        if coleta.grupo == "celular" and conta_key and coleta.dados:
            chaves_celular.add(conta_key)
            itens |= _itens(coleta.dados)
    mapa = await classificacao.carregar_mapa_davinci(session, chaves_celular, item_ids=itens)
    saldos = await _saldos(session, (c["adspower_user_id"] for c in coletas), execucao.semanas)
    return calculo.montar_relatorio(
        {
            "id": execucao.id,
            "tipo": execucao.tipo,
            "origem": execucao.origem,
            "semanas": execucao.semanas,
            "afiliados_ate": execucao.afiliados_ate,
            "criado_em": execucao.criado_em,
        },
        coletas,
        mapa_davinci=mapa,
        saldos=saldos,
        gerado_em=agora,
    )


async def finalizar(
    session: AsyncSession, execucao: ConferenciaShopeeExecucao, agora: datetime
) -> ConferenciaShopeeExecucao:
    """Calcula e congela o relatório; a execução vira `pronto`."""
    execucao.relatorio = await montar(session, execucao, agora)
    execucao.status = "pronto"
    execucao.finalizado_em = agora
    await session.flush()
    logger.info("conferencia_shopee_pronta", execucao=str(execucao.id))
    return execucao


def _so_esta_coleta(
    execucao: ConferenciaShopeeExecucao, coleta: ConferenciaShopeeColeta, agora: datetime
) -> None:
    """O cálculo com UMA loja (sem vínculo do DaVinci nem saldos): levanta se
    os `dados` dela sozinhos derrubam o relatório."""
    calculo.montar_relatorio(
        {
            "id": execucao.id,
            "tipo": execucao.tipo,
            "origem": execucao.origem,
            "semanas": execucao.semanas,
            "afiliados_ate": execucao.afiliados_ate,
            "criado_em": execucao.criado_em,
        },
        [
            {
                "conta_id": coleta.conta_id,
                "conta_key": None,
                "adspower_user_id": coleta.adspower_user_id,
                "nome": coleta.nome,
                "grupo": coleta.grupo,
                "status": coleta.status,
                "erro": coleta.erro,
                "dados": coleta.dados,
            }
        ],
        gerado_em=agora,
    )


async def _descartar_dados_que_quebram(
    session: AsyncSession, execucao: ConferenciaShopeeExecucao, agora: datetime
) -> int:
    """As lojas cujos `dados` derrubam o cálculo sozinhas viram `erro` SEM os
    dados (o resto da rodada segue). Devolve quantas."""
    coletas = (
        await session.execute(
            select(ConferenciaShopeeColeta)
            .where(ConferenciaShopeeColeta.execucao_id == execucao.id)
            .execution_options(populate_existing=True)
        )
    ).scalars().all()
    descartadas = 0
    for coleta in coletas:
        if coleta.dados is None:
            continue
        try:
            _so_esta_coleta(execucao, coleta, agora)
        except Exception as e:  # qualquer forma torta: o relatório não pode travar
            logger.warning(
                "conferencia_shopee_dados_descartados",
                coleta=str(coleta.id),
                conta=coleta.nome,
                erro=f"{type(e).__name__}: {e}"[:300],
            )
            if coleta.status in calculo.STATUS_COM_DADOS:
                coleta.status = "erro"  # sem números, não é mais ok/parcial
            coleta.erro = _erro(f"{ERRO_DADOS_INVALIDOS} ({type(e).__name__})")
            coleta.dados = None
            coleta.concluido_em = coleta.concluido_em or agora
            descartadas += 1
    await session.flush()
    return descartadas


async def _finalizar_com_rede(
    session: AsyncSession, execucao: ConferenciaShopeeExecucao, agora: datetime
) -> ConferenciaShopeeExecucao | None:
    """`finalizar` num savepoint. Se o cálculo quebrar, tira os `dados` das
    lojas que o derrubam e tenta UMA vez mais; sem culpada (ou quebrou de
    novo) → None e a rodada fica para a próxima volta do varredor. Sem isso,
    um payload torto quebrava o varredor a cada 10 min e a rodada ficava
    `coletando` para sempre (e travava a agenda e o "Gerar agora")."""
    execucao_id = execucao.id
    try:
        async with session.begin_nested():
            return await finalizar(session, execucao, agora)
    except Exception:
        logger.exception("conferencia_shopee_calculo_falhou", execucao=str(execucao_id))
    # O rollback do savepoint pode ter expirado a execução: relê (já travada).
    execucao = await _travar(session, execucao_id)
    if execucao is None or execucao.status != "coletando":
        return None
    if not await _descartar_dados_que_quebram(session, execucao, agora):
        return None
    try:
        async with session.begin_nested():
            return await finalizar(session, execucao, agora)
    except Exception:
        logger.exception("conferencia_shopee_calculo_falhou_de_novo", execucao=str(execucao_id))
        return None


async def _travar(
    session: AsyncSession, execucao_id: UUID, *, pular_travada: bool = False
) -> ConferenciaShopeeExecucao | None:
    return (
        await session.execute(
            select(ConferenciaShopeeExecucao)
            .where(ConferenciaShopeeExecucao.id == execucao_id)
            .with_for_update(skip_locked=pular_travada)
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()


async def _tem_fila(session: AsyncSession, execucao_id: UUID) -> bool:
    return (
        await session.execute(
            select(ConferenciaShopeeColeta.id)
            .where(
                ConferenciaShopeeColeta.execucao_id == execucao_id,
                ConferenciaShopeeColeta.status.in_(NA_FILA),
            )
            .limit(1)
        )
    ).scalar_one_or_none() is not None


async def fechar_se_terminou(
    session: AsyncSession, execucao_id: UUID, agora: datetime
) -> ConferenciaShopeeExecucao | None:
    """Fecha a execução se ela ainda coleta e não sobrou coleta na fila.
    Chamar DEPOIS do commit do resultado: com a execução travada, o último a
    chegar sempre enxerga o resultado dos outros. None = nada a fechar."""
    execucao = await _travar(session, execucao_id)
    if execucao is None or execucao.status != "coletando":
        return None
    if await _tem_fila(session, execucao.id):
        return None
    return await _finalizar_com_rede(session, execucao, agora)


async def _encerrar_fila(
    session: AsyncSession, execucao_id: UUID, agora: datetime, motivo: str
) -> None:
    """O que sobrou na fila vira `expirada` (o erro de antes, se havia, fica:
    diz por que a loja não andou)."""
    await session.execute(
        update(ConferenciaShopeeColeta)
        .where(
            ConferenciaShopeeColeta.execucao_id == execucao_id,
            ConferenciaShopeeColeta.status.in_(NA_FILA),
        )
        .values(
            status="expirada",
            concluido_em=agora,
            erro=func.coalesce(ConferenciaShopeeColeta.erro, motivo),
        )
        .execution_options(synchronize_session=False)
    )


async def varrer(session: AsyncSession, agora: datetime) -> list[ConferenciaShopeeExecucao]:
    """De 10 em 10 min (worker): execução que passou do prazo expira o resto
    e fecha; execução sem fila que ficou aberta (o fechamento da rota falhou)
    também fecha. Execução travada por outro (rota fechando agora) fica para
    a próxima volta."""
    ids = (
        await session.execute(
            select(ConferenciaShopeeExecucao.id).where(
                ConferenciaShopeeExecucao.status == "coletando"
            )
        )
    ).scalars().all()
    fechadas: list[ConferenciaShopeeExecucao] = []
    for execucao_id in ids:
        execucao = await _travar(session, execucao_id, pular_travada=True)
        if execucao is None or execucao.status != "coletando":
            continue
        if execucao.prazo <= agora:
            await _encerrar_fila(session, execucao.id, agora, ERRO_PRAZO)
        elif await _tem_fila(session, execucao.id):
            continue
        fechada = await _finalizar_com_rede(session, execucao, agora)
        if fechada is not None:
            fechadas.append(fechada)
    return fechadas


async def recalcular(
    session: AsyncSession, execucao: ConferenciaShopeeExecucao, agora: datetime
) -> ConferenciaShopeeExecucao:
    """Refaz o relatório com os `dados` guardados (regra nova, vínculo novo
    no DaVinci). Só de execução pronta; `finalizado_em` não muda."""
    travada = await _travar(session, execucao.id)
    if travada is None or travada.status != "pronto":
        raise FilaError("conferencia_nao_pronta")
    travada.relatorio = await montar(session, travada, agora)
    await session.flush()
    logger.info("conferencia_shopee_recalculada", execucao=str(travada.id))
    return travada


async def cancelar(
    session: AsyncSession, execucao: ConferenciaShopeeExecucao, agora: datetime
) -> ConferenciaShopeeExecucao:
    """Para a rodada: o que estava na fila vira `expirada`, sem relatório."""
    travada = await _travar(session, execucao.id)
    if travada is None or travada.status != "coletando":
        raise FilaError("conferencia_nao_coletando")
    await _encerrar_fila(session, travada.id, agora, ERRO_CANCELADA)
    travada.status = "cancelado"
    travada.finalizado_em = agora
    await session.flush()
    logger.info("conferencia_shopee_cancelada", execucao=str(travada.id))
    return travada
