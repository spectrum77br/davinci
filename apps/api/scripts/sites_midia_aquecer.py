"""Pré-gera as fotos e vídeos com marca d'água dos sites (SPEC-midia §1.5).

Depois do deploy da api, para a 1ª abertura de cada produto já sair do cache.
Percorre TODA a Tabela de Preços dentro da raiz de cada site (`/Malas` na
Charlots; `/Celular` e `/uranyx` na Uranyx, fora Apple) — um superconjunto
pequeno do que está publicado —, com as mesmas exclusões da listagem, e gera
o que falta: fotos em paralelo de 2, vídeos UM por vez, sob a mesma trava de
arquivo da api. Para antes de passar de 90% do teto do cache, com aviso.

Antes de medir o cache, varre: apaga a versão velha da marca (trocou o logo
ou uma constante, as chaves mudam), grupo órfão e tmp largado — senão a
versão velha contaria no teto e a rodada pararia sem gerar a nova.

Uso (no VPS; `-d` = em segundo plano, uma queda do SSH não mata a rodada):

    docker exec -d davinci-api-1 nice -n 19 python -m scripts.sites_midia_aquecer \\
        --log /data/uploads/sites-midia/aquecer.log
    python -m scripts.sites_midia_aquecer --site uranyx --so-fotos --dry-run

`--dry-run` só conta (o que a varredura apagaria, pastas, fotos, vídeos, o que
já está pronto) e não apaga nem gera nada. A saída é
curta: contagens, MB gravados, tempo e erros. Nunca token nem caminho.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from app import db
from app.config import get_settings
from app.services import sites_midia as midia
from app.services import sites_midia_derivados as d

MB = 1024 * 1024


class _Saida:
    def __init__(self, log: str | None) -> None:
        self.arquivo = Path(log) if log else None

    def __call__(self, texto: str) -> None:
        linha = f"{datetime.now().isoformat(timespec='seconds')} {texto}"
        print(linha, flush=True)
        if self.arquivo is not None:
            self.arquivo.parent.mkdir(parents=True, exist_ok=True)
            with self.arquivo.open("a", encoding="utf-8") as fh:
                fh.write(linha + "\n")


def _bytes_do_grupo(g: d.Grupo) -> int:
    total = 0
    try:
        for p in g.dir.glob(g.k + ".*"):
            if ".tmp." not in p.name:
                total += p.stat().st_size
    except OSError:
        pass
    return total


async def aquecer(
    sites: list[str], *, so_fotos: bool, dry_run: bool, saida: Callable[[str], None]
) -> int:
    """Devolve o número de erros (0 = tudo certo)."""
    inicio = time.perf_counter()
    teto = get_settings().sites_midia_cache_mb * MB
    # Primeiro a varredura: depois de trocar a marca (versão nova), a velha
    # ainda está no disco e contaria no teto — o aquecimento pararia em 90%
    # sem gerar a nova. No `--dry-run` só conta o que sairia.
    if dry_run:
        r = await asyncio.to_thread(d.varrer, simular=True)
        saida(
            f"varredura: seriam apagados {r['apagados']} arquivos de versão velha/órfãos "
            f"({r['liberado'] // MB} MB)"
        )
    else:
        r = await asyncio.to_thread(d.varrer)
        saida(
            f"varredura: {r['apagados']} arquivos de versão velha/órfãos apagados "
            f"({r['liberado'] // MB} MB)"
        )
    usado = await asyncio.to_thread(d.tamanho_cache)
    saida(f"inicio sites={','.join(sites)} cache={usado // MB} MB teto={teto // MB} MB")
    gravado = erros = indisponiveis = 0
    parou = False

    for site in sites:
        async with db.SessionLocal() as session:
            pastas = await midia.pastas_do_site(session, site, None)
        try:
            itens = await midia.itens_das_pastas(site, pastas, limite=None)
        except midia.ListagemIndisponivel:
            saida(f"{site}: sidecar não listou nenhuma das {len(pastas)} pastas")
            erros += 1
            continue
        fotos = [i for i in itens if i.tipo == "foto"]
        videos = [] if so_fotos else [i for i in itens if i.tipo == "video"]
        prontas = sum(d.foto_pronta(d.grupo(site, i.caminho)) for i in fotos)
        prontos = sum(d.video_pronto(d.grupo(site, i.caminho)) for i in videos)
        saida(
            f"{site}: pastas={len(pastas)} fotos={len(fotos)} (prontas {prontas}) "
            f"videos={len(videos)} (prontos {prontos})"
        )
        if dry_run:
            continue

        sem = asyncio.Semaphore(2)

        async def _foto(item: midia.Item, site: str = site, sem: asyncio.Semaphore = sem) -> None:
            nonlocal usado, gravado, erros, indisponiveis, parou
            async with sem:
                g = d.grupo(site, item.caminho)
                if parou or d.foto_pronta(g):
                    return
                if usado > teto * 0.9:
                    parou = True
                    return
                try:
                    await d.garantir_foto(site, item.caminho)
                except d.MidiaIndisponivel:
                    indisponiveis += 1
                    return
                except d.ErroDeMidia as exc:
                    erros += 1
                    saida(f"{site}: foto {item.id} {exc.codigo}")
                    return
                novo = _bytes_do_grupo(g)
                usado += novo
                gravado += novo

        await asyncio.gather(*(_foto(i) for i in fotos))

        for item in videos:
            g = d.grupo(site, item.caminho)
            if parou or d.video_pronto(g):
                continue
            if usado > teto * 0.9:
                parou = True
                break
            t0 = time.perf_counter()
            try:
                await d.gerar_video(site, item.caminho)
            except d.MidiaIndisponivel:
                indisponiveis += 1
                continue
            except d.ErroDeMidia as exc:
                erros += 1
                saida(f"{site}: video {item.id} {exc.codigo}")
                continue
            novo = _bytes_do_grupo(g)
            usado += novo
            gravado += novo
            saida(f"{site}: video {item.id} {novo // 1024} KB em {time.perf_counter() - t0:.0f} s")
        if parou:
            break

    if parou:
        saida(f"AVISO: parei em 90% do teto ({usado // MB} de {teto // MB} MB)")
    saida(
        f"fim gravado={gravado / MB:.1f} MB cache={usado // MB} MB "
        f"indisponiveis={indisponiveis} erros={erros} "
        f"tempo={time.perf_counter() - inicio:.0f} s"
    )
    return erros


def main() -> None:
    sites_validos = sorted(d.MARCAS)
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--site", choices=sites_validos, help="só este site (padrão: os dois)")
    ap.add_argument("--so-fotos", action="store_true", help="não gera vídeo")
    ap.add_argument("--dry-run", action="store_true", help="só conta, não gera nada")
    ap.add_argument("--log", help="anexa a saída também neste arquivo")
    args = ap.parse_args()
    sites = [args.site] if args.site else sites_validos

    async def _rodar() -> int:
        try:
            return await aquecer(
                sites, so_fotos=args.so_fotos, dry_run=args.dry_run, saida=_Saida(args.log)
            )
        finally:
            await db.engine.dispose()

    sys.exit(1 if asyncio.run(_rodar()) else 0)


if __name__ == "__main__":
    main()
