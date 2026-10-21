"""AQUECE o cache das fichas da amostra antes do eval do agente (spec 9.2, 20/10/2026).

O replay e o eval não vão à loja (`sem_ida_a_loja`): a ficha e as variações
vêm do cache (Redis, `atd:ficha:*` e `atd:variacoes:*`) e do banco. Para todos
os braços verem o MESMO dado, este script lê UMA vez, antes de tudo, cada
anúncio distinto da amostra — a ficha com as variações, pelo `ficha_loja`,
com as cotas dele (por sugestão, por rodada e por minuto; a espera também é
a dele). É a ÚNICA etapa do eval que fala com a loja, e só leitura
(`get_item_base_info`/`get_model_list` na Shopee, o item no ML/TikTok).

Precisa da leitura ligada (`ATENDIMENTO_LEITURA_ATIVA`); sem ela, ou com
`--sem-loja`, só confere o que já está no cache e no catálogo (nenhuma ida à
loja). Recusa nas janelas de postagem. A saída é só contagem.

EXATIDÃO DAS VARIAÇÕES (spec 9.5): quando vai à loja, compara, em cada
variação, o que o agente diria SÓ pelos vínculos (`product_links`, sem a lista
da loja) com o que a loja acabou de devolver. A barra: 0 variação
"disponível e confiável" pelos vínculos que esteja esgotada na loja
(`exatidao.falso_disponivel_confiavel`).

Uso (de dentro de apps/api, no container da api):
    uv run python -m scripts.agente_eval.preaquecer --amostra /tmp/agente_eval/amostra.jsonl
    uv run python -m scripts.agente_eval.preaquecer --amostra … --sem-loja   # só confere
"""

# ruff: noqa: T201

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any
from uuid import UUID

from app.config import get_settings
from app.services.atendimento import ficha as ficha_svc
from app.services.atendimento import ficha_loja, variacoes
from app.services.atendimento.agente import replay

# Entre um anúncio e outro (as cotas por minuto do `ficha_loja` seguram o resto).
PAUSA_S = 1.0
NA_JANELA = 3


def anuncios_distintos(itens: Iterable[Mapping[str, Any]]) -> list[tuple[str, str, str]]:
    """(plataforma, loja, anúncio) de cada anúncio da amostra, sem repetir, na ordem. PURA."""
    vistos: dict[tuple[str, str, str], None] = {}
    for item in itens:
        integ = item.get("integration_id")
        plataforma = item.get("plataforma")
        if not integ or not plataforma:
            continue
        ids = [a.get("item_id") for a in item.get("anuncios") or [] if isinstance(a, Mapping)]
        ids = ids or [item.get("anuncio")]
        for anuncio in ids:
            if anuncio:
                vistos.setdefault((str(plataforma), str(integ), str(anuncio)), None)
    return list(vistos)


def _exatidao_vazia() -> dict[str, int]:
    return {
        "comparadas": 0,
        "falso_disponivel_confiavel": 0,
        "falso_disponivel": 0,
        "falso_esgotado": 0,
        "falso_esgotado_confiavel": 0,
        "sem_par_na_loja": 0,
    }


def barra_9_5(exatidao: Mapping[str, int]) -> str:
    """A barra da spec 9.5: "ok" (nenhuma "disponível e confiável" esgotada na
    loja), "falhou", ou "sem_dado" quando nada foi comparado (a loja não
    devolveu a lista; revisão de 20/10/2026 — antes, 0 comparadas dava "ok"). PURA."""
    if not exatidao.get("comparadas"):
        return "sem_dado"
    return "ok" if exatidao.get("falso_disponivel_confiavel", 0) == 0 else "falhou"


def exatidao_das_variacoes(
    pelos_vinculos: Mapping[str, Any] | None, da_loja: Sequence[Mapping[str, Any]] | None
) -> dict[str, int]:
    """Spec 9.5: o registro SÓ pelos vínculos (`variacoes.montar_variacoes(None, …)`) × a
    lista da loja (`da_shopee`/`da_ml`/`da_tiktok`: `variation_id` e `estoque`). PURA.

    Por variação do registro com par na loja (pelo `variation_id`):
    `falso_disponivel` = disponível pelos vínculos e esgotada na loja (o
    `_confiavel`, o mesmo marcado confiável — a barra é 0); `falso_esgotado`
    = o contrário (e `falso_esgotado_confiavel`, o marcado confiável). Sem a
    lista da loja: nada comparado.
    """
    saida = _exatidao_vazia()
    if not pelos_vinculos or da_loja is None:
        return saida
    estoque = {
        str(v.get("variation_id")): v.get("estoque")
        for v in da_loja
        if isinstance(v, Mapping) and v.get("variation_id") is not None
    }
    for v in pelos_vinculos.get("variacoes") or []:
        vid = v.get("variation_id") if isinstance(v, Mapping) else None
        if vid is None or str(vid) not in estoque:
            saida["sem_par_na_loja"] += 1
            continue
        saida["comparadas"] += 1
        na_loja = int(estoque[str(vid)] or 0) > 0
        if v.get("disponivel") is True and not na_loja:
            saida["falso_disponivel"] += 1
            saida["falso_disponivel_confiavel"] += int(bool(v.get("confiavel")))
        elif v.get("disponivel") is False and na_loja:
            saida["falso_esgotado"] += 1
            # "Acabou" dito com confiança e a loja tem: o agente diria esgotado.
            saida["falso_esgotado_confiavel"] += int(bool(v.get("confiavel")))
    return saida


async def _exatidao(
    s: Any, plat: Any, integration_id: UUID, anuncio: str, titulo: str | None
) -> dict[str, int]:
    """O registro pelos vínculos × a lista que a loja acabou de devolver (no cache)."""
    em_cache = await variacoes.da_loja_em_cache(plat, integration_id, anuncio)
    if em_cache is None:
        return _exatidao_vazia()
    vinculos = await variacoes.vinculos_do_anuncio(s, plat, integration_id, anuncio)
    pelos_vinculos = variacoes.montar_variacoes(None, vinculos, titulo=titulo)
    return exatidao_das_variacoes(pelos_vinculos, em_cache[0])


def _titulos_e_canais(
    itens: Iterable[Mapping[str, Any]],
) -> tuple[dict[str, str], dict[str, str]]:
    titulos: dict[str, str] = {}
    canais: dict[str, str] = {}
    for item in itens:
        for a in item.get("anuncios") or []:
            if isinstance(a, Mapping) and a.get("item_id") and a.get("titulo"):
                titulos.setdefault(str(a["item_id"]), str(a["titulo"]))
        if item.get("integration_id") and item.get("canal_id"):
            canais.setdefault(str(item["integration_id"]), str(item["canal_id"]))
    return titulos, canais


async def aquecer(
    itens: list[Mapping[str, Any]], *, sem_loja: bool, pausa_s: float = PAUSA_S
) -> dict[str, Any]:
    """Lê cada anúncio distinto (com a ida à loja, salvo `sem_loja`). Só contagens."""
    vai_a_loja = not sem_loja and bool(get_settings().atendimento_leitura_ativa)
    titulos, canais = _titulos_e_canais(itens)
    resumo: dict[str, Any] = {
        "anuncios": 0,
        "com_ficha": 0,
        "sem_ficha": 0,
        "com_variacoes": 0,
        "erros": 0,
        "foi_a_loja": vai_a_loja,
        "exatidao": _exatidao_vazia(),
    }
    for plataforma, integ, anuncio in anuncios_distintos(itens):
        if replay.janela_de_postagem():
            resumo["parou_na_janela"] = True
            break
        resumo["anuncios"] += 1
        plat = ficha_svc.plataforma_do_catalogo(plataforma)
        if plat is None:
            resumo["sem_ficha"] += 1
            continue
        integration_id = UUID(integ)
        canal_id = UUID(canais[integ]) if canais.get(integ) else None
        loja = ficha_loja.Busca(integration_id, canal_id) if vai_a_loja else None
        try:
            async with replay.sessao_replay() as s:
                ficha = await ficha_svc.ficha_do_anuncio(
                    s,
                    plat,
                    anuncio,
                    integration_id,
                    titulo=titulos.get(anuncio),
                    loja=loja,
                    com_variacoes=True,
                    variacoes_na_loja=vai_a_loja,
                )
                if vai_a_loja:
                    titulo = (ficha or {}).get("titulo") or titulos.get(anuncio)
                    for chave, n in (
                        await _exatidao(s, plat, integration_id, anuncio, titulo)
                    ).items():
                        resumo["exatidao"][chave] += n
        except Exception:  # noqa: BLE001 — um anúncio com erro não para o aquecimento
            resumo["erros"] += 1
            continue
        if ficha is None:
            resumo["sem_ficha"] += 1
        else:
            resumo["com_ficha"] += 1
            resumo["com_variacoes"] += int(bool(ficha.get("variacoes")))
        if vai_a_loja and pausa_s > 0:
            await asyncio.sleep(pausa_s)
    # A busca que passou do tempo segue em segundo plano no `ficha_loja` (e grava
    # o cache quando volta): espera ela terminar antes de o processo sair.
    pendentes = list(getattr(ficha_loja, "_EM_SEGUNDO_PLANO", ()))
    if pendentes:
        await asyncio.gather(*pendentes, return_exceptions=True)
    return resumo


async def principal(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Aquece as fichas da amostra (a única ida à loja).")
    p.add_argument("--amostra", type=Path, required=True)
    p.add_argument("--sem-loja", action="store_true", help="só confere o cache e o catálogo")
    p.add_argument("--pausa-s", type=float, default=PAUSA_S)
    args = p.parse_args(argv)
    if janela := replay.janela_de_postagem():
        print(f"dentro da janela de postagem ({janela}): nada rodou")
        return NA_JANELA
    with args.amostra.open(encoding="utf-8") as f:
        itens = [json.loads(linha) for linha in f if linha.strip()]
    resumo = await aquecer(itens, sem_loja=args.sem_loja, pausa_s=max(0.0, args.pausa_s))
    if resumo["foi_a_loja"]:
        # A barra da spec 9.5: nenhuma "disponível e confiável" esgotada na loja.
        resumo["barra_9_5"] = barra_9_5(resumo["exatidao"])
    print(json.dumps(resumo, ensure_ascii=False, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(principal(argv))


if __name__ == "__main__":
    sys.exit(main())
