"""Regrava os números de Ads do Mercado Livre dos últimos N dias (máx. 90).

Contexto (07/10/2026, docs/marketing-ml-ads.md): o ML desligou o endpoint de
métricas que o DaVinci usava; desde ~09/07 o gasto/impressões das contas do ML
estão zerados em marketing_metrics, e antes disso o gasto estava inflado
(somatório de 7 dias numa linha só). Depois do deploy do conserto, a coleta
de 30 em 30 min já corrige sozinha os ÚLTIMOS 7 DIAS. Este script refaz o
resto da janela que o ML ainda guarda (90 dias).

Por padrão SÓ SIMULA: chama o ML (só leitura), lê o Bling local e imprime o
que cada dia passaria a ter — não grava nada e não renova token (conta com
token vencido aparece como erro 'token_expirado'; rode de novo depois que o
worker renovar). Com --apply grava (cria/atualiza a linha diária; nunca
apaga). Rodar duas vezes não duplica nada.

Uso (no servidor, dentro do container da API):
  docker compose -f docker-compose.yml -f docker-compose.prod.yml \\
    exec -T api python -m scripts.ml_ads_backfill --dias 90
  docker compose ... exec -T api python -m scripts.ml_ads_backfill --dias 90 --apply
  # só uma conta (id da integração; repita a opção para mais de uma):
  docker compose ... exec -T api python -m scripts.ml_ads_backfill --dias 90 \\
    --integracao <id-da-integracao>
  # plano completo por dia em JSON:
  docker compose ... exec -T api python -m scripts.ml_ads_backfill --dias 90 --json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from uuid import UUID

from app.db import SessionLocal
from app.services.marketing.ml_sync import backfill_ml_ads
from app.services.ml_ads import ML_ADS_MAX_DIAS


def _linha(r: dict) -> str:
    nome = (r.get("name") or "").strip() or r.get("integration_id")
    st = r.get("status")
    if st != "ok":
        motivo = r.get("reason") or f"{r.get('code')}: {r.get('message')}"
        return f"  {nome:<14} {st:<14} {motivo}"
    t = r.get("totais") or {}
    linha = (
        f"  {nome:<14} ok             dias={t.get('dias')} criar={t.get('criar')} "
        f"atualizar={t.get('atualizar')} iguais={t.get('iguais')} "
        f"gasto=R$ {t.get('gasto', 0):,.2f} impressoes={t.get('impressoes')} "
        f"vendas_ads=R$ {t.get('vendas_ads', 0):,.2f}"
    )
    avisos = r.get("avisos") or {}
    sem_dado = avisos.get("dias_sem_dado_ml") or []
    if sem_dado:
        linha += (
            f"\n  {'':<14} aviso          {len(sem_dado)} dia(s) fechado(s) sem dado do ML "
            f"({sem_dado[0]} … {sem_dado[-1]}) — ficou o que estava gravado"
        )
    if avisos.get("linhas_repetidas"):
        linha += (
            f"\n  {'':<14} aviso          linha diária repetida em "
            f"{', '.join(avisos['linhas_repetidas'])}"
        )
    return linha


async def main(dias: int, apply_flag: bool, integracoes: list[UUID], como_json: bool) -> int:
    async with SessionLocal() as session:
        resultados = await backfill_ml_ads(
            session,
            dias=dias,
            integration_ids=integracoes or None,
            simular=not apply_flag,
        )
    if como_json:
        print(json.dumps(resultados, ensure_ascii=False, indent=1, default=str))
    else:
        modo = "GRAVANDO" if apply_flag else "SIMULAÇÃO (nada gravado; --apply para gravar)"
        print(f"{modo} — Ads do Mercado Livre, últimos {dias} dias, {len(resultados)} conta(s)")
        for r in resultados:
            print(_linha(r))
    return 1 if any(r.get("status") == "erro" for r in resultados) else 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    p.add_argument("--dias", type=int, default=ML_ADS_MAX_DIAS,
                   help=f"quantos dias para trás, contando hoje (1..{ML_ADS_MAX_DIAS})")
    p.add_argument("--apply", action="store_true", help="grava (sem isto só simula)")
    p.add_argument("--integracao", type=UUID, action="append", default=[],
                   help="id da integração do ML (pode repetir); sem isto, todas com Ads ligado")
    p.add_argument("--json", action="store_true", help="imprime o plano completo por dia")
    a = p.parse_args()
    sys.exit(asyncio.run(main(a.dias, a.apply, a.integracao, a.json)))
