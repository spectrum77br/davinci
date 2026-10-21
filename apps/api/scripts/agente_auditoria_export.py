"""A AUDITORIA diária do agente: o dia dos envios sozinhos e do replay, mascarado (20/10/2026).

Spec 8 (rollout): o Claude dá nota a 100% dos envios AUTOMÁTICOS do agente
(rotina agendada no Mac mini, só leitura), e a uma amostra do replay noturno
contra a resposta real da equipe. Erro crítico vira alerta e o assunto sai do
automático (a reversão é do dono/da rotina, não deste script).

Exporta, de UM dia (São Paulo), uma linha por registro:

- `envio_automatico`: cada mensagem que a IA mandou SOZINHA a partir de um
  rascunho do agente (`prompt_versao="ag1"`): a transcrição até o gatilho, o
  texto enviado (generalizado: lacunas, sem nome/número/código), o assunto, a
  confiança, as evidências e o rastro enxuto (`fatos.agente`), e o que veio
  DEPOIS (as próximas falas do cliente e da equipe, generalizadas);
- `replay`: as linhas de `atendimento_agente_replay` do dia (já gravadas
  mascaradas pelo replay): a proposta, a decisão simulada, o rastro e a
  resposta real da equipe.

Nada de nome, CPF, telefone, endereço nem cidade (`replay.mascarar_texto`,
`replay.generalizar`, com os nomes e o destino do pedido). SÓ LEITURA (sessão
`READ ONLY`); nada é gravado.

Uso (de dentro de apps/api, no container da api):
    uv run python -m scripts.agente_auditoria_export                      # ontem → stdout
    uv run python -m scripts.agente_auditoria_export --dia 2026-11-04 --saida /tmp/auditoria.jsonl
    (do Mac: ssh davinci-prod "docker exec davinci-api-1 uv run python -m \\
        scripts.agente_auditoria_export --dia 2026-11-04" > auditoria_2026-11-04.jsonl)

  --dia AAAA-MM-DD   o dia de São Paulo (padrão: ontem).
  --plataforma P     só uma plataforma (padrão: todas).
  --sem-replay       só os envios automáticos.
  --saida ARQ        o JSONL (padrão: a saída padrão).
"""

# ruff: noqa: T201

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from collections.abc import Mapping
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AtendimentoConversa, AtendimentoMensagem, AtendimentoRascunho
from app.models.atendimento import AtendimentoAgenteReplay
from app.services.atendimento import constantes, ia
from app.services.atendimento.agente import acao_pessoa, pacote, replay

TIPO_ENVIO = "envio_automatico"
TIPO_REPLAY = "replay"
# O que veio depois do envio: até 4 falas em 24 h.
MAX_DEPOIS = 4


def _id(*partes: Any) -> str:
    return hashlib.sha256(":".join(str(p) for p in partes).encode()).hexdigest()[:12]


def _limites(dia: date) -> tuple[datetime, datetime]:
    inicio = datetime.combine(dia, time(0), tzinfo=ia.SAO_PAULO)
    return inicio, inicio + timedelta(days=1)


def _passos(fatos_agente: Mapping[str, Any], nomes, termos) -> list[dict]:
    saida = []
    for passo in fatos_agente.get("rastro") or []:
        if not isinstance(passo, Mapping):
            continue
        p = dict(passo)
        p["args"] = {
            k: replay.mascarar_texto(v, nomes, termos, digitos=False) if isinstance(v, str) else v
            for k, v in (p.get("args") or {}).items()
        }
        saida.append(p)
    return saida


async def _envio(
    session: AsyncSession, mensagem: AtendimentoMensagem, rascunho: AtendimentoRascunho
) -> dict[str, Any] | None:
    conversa = await session.get(AtendimentoConversa, mensagem.conversa_id)
    if conversa is None:
        return None
    nomes = tuple(
        n
        for n in (
            conversa.comprador_nome,
            conversa.comprador_id,
            *await ia._nomes_do_pedido(session, conversa.pedido_marketplace),
        )
        if n
    )
    termos = await replay.termos_do_pedido(session, conversa)
    fatos = rascunho.fatos if isinstance(rascunho.fatos, dict) else {}
    lacunas = fatos.get("lacunas") if isinstance(fatos.get("lacunas"), dict) else {}
    agente = fatos.get("agente") if isinstance(fatos.get("agente"), dict) else {}
    gatilho = (
        await session.get(AtendimentoMensagem, rascunho.mensagem_gatilho_id)
        if rascunho.mensagem_gatilho_id
        else None
    )
    transcricao: list[str] = []
    if gatilho is not None:
        recentes = list(await replay.mensagens_ate(session, conversa, ia._momento(gatilho)))
        transcricao = [
            replay.mascarar_texto(linha, nomes, termos, digitos=False)
            for linha in pacote.transcricao(
                recentes,
                gatilho,
                nomes,
                pediu_evidencia=acao_pessoa.pediu_evidencia(recentes, gatilho),
            )
        ]
    depois = [
        m
        for m in await replay.mensagens_depois(session, conversa, mensagem, limite=MAX_DEPOIS * 3)
        if m.origem != constantes.ORIGEM_NOTA and (m.texto or "").strip()
    ][:MAX_DEPOIS]
    enviado = replay.generalizar(replay.relacunar(mensagem.texto, lacunas), nomes, termos)
    return {
        "tipo": TIPO_ENVIO,
        "id": _id(TIPO_ENVIO, mensagem.id),
        "conversa_id": str(conversa.id),
        "rascunho_id": str(rascunho.id),
        "plataforma": conversa.plataforma,
        "integration_id": str(conversa.integration_id) if conversa.integration_id else None,
        "enviada_em": ia._momento(mensagem).isoformat(),
        "categoria": rascunho.categoria,
        "confianca": rascunho.confianca,
        "modelo": rascunho.modelo,
        "acao": agente.get("acao"),
        "acoes_pessoa": list(agente.get("acoes_pessoa") or []),
        "evidencias": list(agente.get("evidencias") or []),
        "checagens": list(agente.get("checagens") or []),
        "avisos_auto": list(agente.get("avisos_auto") or []),
        "rastro": _passos(agente, nomes, termos),
        "transcricao": transcricao,
        "texto_enviado": enviado,
        "depois": [
            {
                "autor": m.autor,
                "automatica": not replay.da_equipe(m) and m.autor != constantes.AUTOR_CLIENTE,
                "texto": replay.generalizar(m.texto, nomes, termos),
            }
            for m in depois
        ],
    }


async def exportar_dia(
    session: AsyncSession,
    dia: date,
    *,
    plataforma: str | None = None,
    com_replay: bool = True,
) -> list[dict[str, Any]]:
    """Os registros do dia (os envios sozinhos do agente e o replay), mascarados. Só lê."""
    inicio, fim = _limites(dia)
    m, r = AtendimentoMensagem, AtendimentoRascunho
    consulta = (
        select(m, r)
        .join(r, r.id == m.rascunho_id)
        .where(
            m.origem == constantes.ORIGEM_IA,
            r.prompt_versao == constantes.PROMPT_VERSAO_AGENTE,
            ia._momento_col() >= inicio,
            ia._momento_col() < fim,
        )
        .order_by(ia._momento_col(), m.created_at)
    )
    registros: list[dict[str, Any]] = []
    for mensagem, rascunho in (await session.execute(consulta)).all():
        envio = await _envio(session, mensagem, rascunho)
        if envio is not None and (plataforma is None or envio["plataforma"] == plataforma):
            registros.append(envio)
    if com_replay:
        t = AtendimentoAgenteReplay
        consulta_r = select(t).where(t.dia == dia).order_by(t.gatilho_em, t.id)
        if plataforma is not None:
            consulta_r = consulta_r.where(t.plataforma == plataforma)
        for linha in (await session.execute(consulta_r)).scalars().all():
            registros.append(
                {
                    "tipo": TIPO_REPLAY,
                    "id": _id(TIPO_REPLAY, linha.gatilho_id),
                    "conversa_id": str(linha.conversa_id),
                    "plataforma": linha.plataforma,
                    "integration_id": str(linha.integration_id) if linha.integration_id else None,
                    "gatilho_em": linha.gatilho_em.isoformat(),
                    "proposta": linha.proposta,
                    "decisao": linha.decisao,
                    "rastro": linha.rastro,
                    "resposta_real": linha.resposta_real,
                    "parada": linha.parada,
                    "modelo": linha.modelo,
                    "effort": linha.effort,
                    "turnos": linha.turnos,
                    "ms": linha.ms,
                    "custo_usd": float(linha.custo_usd) if linha.custo_usd is not None else None,
                }
            )
    return registros


def _dia(texto: str) -> date:
    try:
        return date.fromisoformat(texto)
    except ValueError as e:
        raise argparse.ArgumentTypeError(f"dia inválido: {texto!r} (use AAAA-MM-DD)") from e


async def principal(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="O dia do agente para a auditoria (mascarado).")
    p.add_argument("--dia", type=_dia, default=None)
    p.add_argument("--plataforma", default=None)
    p.add_argument("--sem-replay", action="store_true")
    p.add_argument("--saida", type=Path, default=None)
    args = p.parse_args(argv)
    dia = args.dia or (replay.agora_sp() - timedelta(days=1)).date()
    async with replay.sessao_replay() as s:
        registros = await exportar_dia(
            s, dia, plataforma=args.plataforma, com_replay=not args.sem_replay
        )
    linhas = [json.dumps(r, ensure_ascii=False, sort_keys=True, default=str) for r in registros]
    if args.saida is None:
        for linha in linhas:
            print(linha)
    else:
        args.saida.parent.mkdir(parents=True, exist_ok=True)
        args.saida.write_text("".join(linha + "\n" for linha in linhas), encoding="utf-8")
        envios = sum(1 for r in registros if r["tipo"] == TIPO_ENVIO)
        print(
            json.dumps(
                {"dia": dia.isoformat(), "envios": envios, "replay": len(registros) - envios},
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(principal(argv))


if __name__ == "__main__":
    sys.exit(main())
