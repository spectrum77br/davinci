"""Robô Jurídico (robô da Ouvidoria, 12/11/2026).

Vinicius: "cria também um robô jurídico, coloca no Ouvidoria › Robôs. A princípio ele só vai
fazer uma função: atualizar a situação e ver se algum falhou… vai só me avisar (Cairo) no
Threema se algum falhou e não atualizou."

Quem atualiza os processos é o Mac mini: às 06:00 o `juridico_pedido_tick` cria o pedido do
dia (`juridico_pedidos`), o mini lê o Diário Oficial (DJEN) e o Jusbrasil de cada processo com
nº e devolve tudo ao DaVinci (`juridico_leituras` e, por processo, `jusbrasil_tentado_em` /
`jusbrasil_erro`). Este robô roda 1× por dia às 07:30, depois disso, e confere:

1. **A atualização das 06:00** — não foi pedida, o mini não pegou, não terminou ou deu erro.
2. **O Diário Oficial** — o mini não leu hoje, ou leu com falha.
3. **O Jusbrasil de cada processo com nº** (menos os encerrados) — falhou/não achou hoje, ou
   ficou de fora da rodada de hoje.

Cada problema vira uma ocorrência na Ouvidoria (fecha sozinha quando some), e a Ouvidoria manda
UMA mensagem no Threema com o que está pendente, pra quem está no "Avisar" do robô (o Cairo).
Nasce `silencioso` (registra e cala) até o "Avisar" ter o Cairo e alguém ligar no painel.
Se o problema é da atualização inteira (1), o robô não cobra processo por processo: seria a
mesma causa repetida em cada linha.
"""

from __future__ import annotations

from datetime import UTC, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import session_scope
from app.models.denuncia import DenunciaCaso
from app.models.juridico import JuridicoLeitura, JuridicoPedido, JuridicoProcesso
from app.services import ouvidoria
from app.services.advisory_lock import SYNC_NAMESPACE

logger = structlog.get_logger()

ROBO = "robo_juridico"
_SWEEP_LOCK_KEY = 0x726A7572  # ascii "rjur"
SP = ZoneInfo("America/Sao_Paulo")
LINK = "/ouvidoria/juridico"
ACAO_ATUALIZAR = 'abrir Ouvidoria › Jurídico e clicar em "Atualizar"'
ACAO_MINI = (
    "ver se o Mac mini está ligado e o vigia do Jurídico rodando; depois clicar em Atualizar"
)


def numero_fmt(n: str | None) -> str:
    """20 dígitos do CNJ → 4206943-64.2026.8.26.0100 (o resto fica como veio)."""
    d = "".join(c for c in (n or "") if c.isdigit())
    if len(d) != 20:
        return (n or "").strip()
    return f"{d[:7]}-{d[7:9]}.{d[9:13]}.{d[13]}.{d[14:16]}.{d[16:]}"


def _hora(dt: datetime | None) -> str:
    return dt.astimezone(SP).strftime("%d/%m %H:%M") if dt else "—"


def _rotulo(p: JuridicoProcesso, codigos: dict[int, str]) -> str:
    """Como o painel chama o processo: Caso 006; senão o pedido; senão o réu."""
    if p.denuncia_caso_id and codigos.get(p.denuncia_caso_id):
        return f"Caso {codigos[p.denuncia_caso_id]}"
    if p.pedido:
        return f"pedido {p.pedido}"
    return p.reu or f"processo {p.id}"


async def robo_juridico_run(session: AsyncSession, *, agora: datetime | None = None) -> dict:
    agora = agora or datetime.now(UTC)
    hoje = agora.astimezone(SP).date()
    inicio_dia = datetime.combine(hoje, time.min, tzinfo=SP)
    # O dia vai na chave: ocorrência "ignorada" nunca reabre, e falha de hoje é aviso novo
    # (a de ontem que não se repetiu fecha como "sumiu").
    dia = hoje.isoformat()
    async with ouvidoria.Rodada(session, ROBO) as r:
        for k in ("processos", "falhas", "novas", "persistem", "sumiram"):
            r.contadores[k] = 0

        async def registrar(chave: str, titulo: str, detalhe: str, acao: str, **dados: Any) -> None:
            row = await r.registrar(
                chave=chave,
                titulo=titulo[:200],
                detalhe=detalhe,
                acao=acao,
                link=LINK,
                severidade="pessoa",
                precisa_pessoa=True,
                dados=dados,
                agora=agora,
            )
            r.contadores["falhas"] += 1
            if row.fechada_em is None:
                r.contadores["novas" if row.aberta_em == agora else "persistem"] += 1

        # 1) a atualização das 06:00 (pedido do cron) de hoje
        pedido = (
            await session.execute(
                select(JuridicoPedido)
                .where(JuridicoPedido.origem == "cron", JuridicoPedido.pedido_em >= inicio_dia)
                .order_by(JuridicoPedido.pedido_em.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        problema: str | None = None
        acao = ACAO_ATUALIZAR
        if pedido is None:
            problema = "a atualização das 06:00 não foi pedida hoje"
        elif pedido.pego_em is None:
            problema = (
                f"o Mac mini não pegou a atualização das 06:00 (pedida {_hora(pedido.pedido_em)})"
            )
            acao = ACAO_MINI
        elif pedido.terminado_em is None:
            parou = f" — parou em: {pedido.progresso}" if pedido.progresso else ""
            problema = (
                f"a atualização das 06:00 começou {_hora(pedido.pego_em)} e não terminou{parou}"
            )
            acao = ACAO_MINI
        elif pedido.ok is False:
            problema = f"a atualização das 06:00 falhou: {pedido.resultado or 'sem motivo'}"
        if problema:
            await registrar(
                f"atualizacao:{dia}",
                f"Jurídico não atualizou hoje: {problema}"[:200],
                f"A atualização diária dos processos (Diário Oficial + Jusbrasil) não terminou "
                f"bem: {problema}. Enquanto isso, nenhum processo foi atualizado hoje.",
                acao,
                pedido_id=pedido.id if pedido else None,
            )

        # 2) o Diário Oficial lido hoje pelo mini — só se a atualização terminou bem (se ela
        #    falhou, o aviso do passo 1 já diz tudo; cobrar de novo seria a mesma causa)
        terminou_bem = problema is None and pedido is not None
        if terminou_bem:
            leitura = (
                await session.execute(
                    select(JuridicoLeitura)
                    .where(JuridicoLeitura.quando >= pedido.pedido_em)
                    .order_by(JuridicoLeitura.quando.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if leitura is None:
                await registrar(
                    f"diario:{dia}",
                    "Diário Oficial não foi lido hoje",
                    "A atualização das 06:00 terminou, mas o Mac mini não mandou a leitura do "
                    "Diário Oficial de hoje.",
                    ACAO_ATUALIZAR,
                )
            elif leitura.falhas or leitura.erro:
                motivo = leitura.erro or f"{leitura.falhas} processo(s) com falha"
                await registrar(
                    f"diario:{dia}",
                    f"Diário Oficial com falha hoje: {motivo}"[:200],
                    f"Leitura de {_hora(leitura.quando)}: {leitura.processos} processo(s) lidos, "
                    f"{leitura.falhas} com falha. {leitura.erro or ''}".strip(),
                    ACAO_ATUALIZAR,
                    falhas=leitura.falhas,
                )

        # 3) o Jusbrasil de cada processo com nº — só quando a atualização do dia terminou
        if terminou_bem and pedido.jusbrasil:
            processos = (
                (
                    await session.execute(
                        select(JuridicoProcesso).where(
                            JuridicoProcesso.numero.is_not(None),
                            func.btrim(JuridicoProcesso.numero) != "",
                            JuridicoProcesso.situacao != "encerrado",
                        )
                    )
                )
                .scalars()
                .all()
            )
            if pedido.processo_id is not None:  # pedido de um processo só (não é o do cron)
                processos = [p for p in processos if p.id == pedido.processo_id]
            ids_caso = {p.denuncia_caso_id for p in processos if p.denuncia_caso_id}
            codigos: dict[int, str] = {}
            if ids_caso:
                for cid, cod in (
                    await session.execute(
                        select(DenunciaCaso.id, DenunciaCaso.codigo).where(
                            DenunciaCaso.id.in_(ids_caso)
                        )
                    )
                ).all():
                    if cod:
                        codigos[cid] = str(cod)
            r.contadores["processos"] = len(processos)
            for p in processos:
                nome = _rotulo(p, codigos)
                num = numero_fmt(p.numero)
                tentou_hoje = (
                    p.jusbrasil_tentado_em is not None
                    and p.jusbrasil_tentado_em >= pedido.pedido_em
                )
                if tentou_hoje and p.jusbrasil_erro:
                    await registrar(
                        f"jusbrasil:{p.id}:{dia}",
                        f"{nome}: Jusbrasil falhou — {p.jusbrasil_erro}"[:200],
                        f"Processo {num} ({nome}). Tentativa {_hora(p.jusbrasil_tentado_em)}: "
                        f"{p.jusbrasil_erro}. Processo novo às vezes demora a aparecer no "
                        "Jusbrasil; a rodada de amanhã tenta de novo.",
                        ACAO_ATUALIZAR,
                        processo_id=p.id,
                        numero=num,
                    )
                elif not tentou_hoje:
                    await registrar(
                        f"jusbrasil:{p.id}:{dia}",
                        f"{nome}: Jusbrasil não foi atualizado hoje",
                        f"Processo {num} ({nome}) ficou de fora da atualização de hoje "
                        f"(última tentativa: {_hora(p.jusbrasil_tentado_em)}).",
                        ACAO_ATUALIZAR,
                        processo_id=p.id,
                        numero=num,
                    )

        r.contadores["sumiram"] = await r.fechar_nao_vistas()
        n = r.contadores["falhas"]
        r.resumo = (
            f"{n} falha(s) na atualização de hoje"
            if n
            else f"atualização de hoje ok · {r.contadores['processos']} processo(s)"
        )
    aviso = await ouvidoria.avisar_pendentes(session, ROBO, agora=agora)
    return {**dict(r.contadores), "avisadas": aviso.get("avisadas", 0), "resumo": r.resumo}


async def robo_juridico_sweep() -> dict:
    """Tick do worker (07:30 BRT) e "Rodar agora": sessão própria, serializado por advisory lock."""
    async with session_scope() as trava:
        got = (
            await trava.execute(
                text("SELECT pg_try_advisory_xact_lock(:ns, :key)"),
                {"ns": SYNC_NAMESPACE, "key": _SWEEP_LOCK_KEY},
            )
        ).scalar()
        if not got:
            return {"skipped": "lock_busy"}
        async with session_scope() as session:
            return await robo_juridico_run(session)
