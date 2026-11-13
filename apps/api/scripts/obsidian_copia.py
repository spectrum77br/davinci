"""A cópia noturna do banco de respostas para o cofre do Obsidian (Mac mini, 13/11/2026).

O Eduardo: o Postgres em produção é o banco vivo e o cofre do Obsidian
(`/Users/thorfinn/DaVinci-Atendimento`) é a cópia de contingência, atualizada toda
noite, que a equipe consegue ler sem o DaVinci. QUEM BUSCA É O MAC: este script pede o
zip ao DaVinci (GET /api/atendimento/robo/banco-obsidian, com o token do robô do Mac
mini) e troca a pasta `DaVinci/` do cofre. O servidor nunca chama o Mac nem depende
dele ligado; Mac desligado = a cópia fica como estava e se atualiza quando ele voltar
(o LaunchAgent roda às 03:30 e ao ligar).

Regras do cofre (o Eduardo é o dono dele):
- só mexe em `<cofre>/DaVinci/`; nunca na nota de boas-vindas, no `.obsidian` ou no
  que ele criar;
- só troca a pasta se ela tiver a marca `.gerado-pelo-davinci` (foi este script que a
  criou); sem a marca, não mexe e avisa;
- troca de uma vez (extrai numa pasta oculta ao lado e renomeia): o Obsidian nunca vê
  a pasta pela metade;
- o zip só pode ter caminhos dentro de `DaVinci/` (nada de `..` nem caminho absoluto).

Falhou (rede, token, zip estranho, pasta sem a marca)? Notificação do macOS, a nota
`DaVinci/Cópia falhou.md` no cofre (quando a pasta é nossa) e saída 1, que fica no log
do LaunchAgent. A próxima cópia boa apaga a nota.

Só biblioteca padrão (roda com o python3 do macOS). Uso:
    python3 obsidian_copia.py --url https://app.hadken.com \
        --token-arquivo ~/.davinci/robo_atendimento.token \
        --cofre /Users/thorfinn/DaVinci-Atendimento
"""

# ruff: noqa: T201

from __future__ import annotations

import argparse
import io
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path, PurePosixPath

PASTA = "DaVinci"
MARCA = ".gerado-pelo-davinci"
NOTA_FALHA = "Cópia falhou.md"
TITULO_AVISO = "Cópia do DaVinci para o Obsidian falhou"
ROTA = "/api/atendimento/robo/banco-obsidian"
TENTATIVAS = 3
ESPERA_S = 20
TIMEOUT_S = 120
MAX_ZIP_BYTES = 200 * 1024 * 1024


class CopiaRecusada(Exception):  # noqa: N818 — o nome diz o que aconteceu
    """A cópia não pode seguir (o motivo vai na notificação e na nota)."""


def baixar(
    url: str, token: str, *, tentativas: int = TENTATIVAS, espera_s: float = ESPERA_S
) -> bytes:
    """O zip do DaVinci, com até `tentativas` (rede cai; 401/404 não adianta repetir)."""
    pedido = urllib.request.Request(  # noqa: S310 — a URL é a do DaVinci, do LaunchAgent
        url.rstrip("/") + ROTA,
        headers={"Authorization": f"Bearer {token}", "User-Agent": "davinci-obsidian-copia/1"},
    )
    ultimo = "sem resposta"
    for i in range(max(1, tentativas)):
        try:
            with urllib.request.urlopen(pedido, timeout=TIMEOUT_S) as resp:  # noqa: S310
                corpo = resp.read(MAX_ZIP_BYTES + 1)
            if len(corpo) > MAX_ZIP_BYTES:
                raise CopiaRecusada("o zip passou do tamanho máximo")
            return corpo
        except urllib.error.HTTPError as e:
            if e.code in (401, 403, 404):
                raise CopiaRecusada(
                    f"o DaVinci recusou a cópia (HTTP {e.code}): confira o token"
                ) from None
            ultimo = f"HTTP {e.code}"
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            ultimo = f"rede: {type(e).__name__}"
        if i + 1 < tentativas:
            time.sleep(espera_s)
    raise CopiaRecusada(f"não consegui falar com o DaVinci ({ultimo})")


def conferir_zip(corpo: bytes) -> zipfile.ZipFile:
    """O zip é o da pasta DaVinci/ e só tem caminhos seguros dentro dela."""
    try:
        z = zipfile.ZipFile(io.BytesIO(corpo))
    except zipfile.BadZipFile:
        raise CopiaRecusada("o DaVinci não mandou um zip válido") from None
    nomes = z.namelist()
    if f"{PASTA}/{MARCA}" not in nomes or f"{PASTA}/Início.md" not in nomes:
        raise CopiaRecusada("o zip não tem a pasta DaVinci completa")
    for nome in nomes:
        partes = PurePosixPath(nome).parts
        if (
            nome.startswith("/")
            or "\\" in nome
            or ".." in partes
            or not partes
            or partes[0] != PASTA
        ):
            raise CopiaRecusada("o zip tem um caminho fora da pasta DaVinci")
    return z


def trocar_pasta(cofre: Path, z: zipfile.ZipFile) -> int:
    """Extrai numa pasta oculta e troca `cofre/DaVinci` de uma vez. Devolve quantos arquivos."""
    if not cofre.is_dir():
        raise CopiaRecusada(f"o cofre não existe: {cofre}")
    destino = cofre / PASTA
    if destino.exists() and not (destino / MARCA).exists():
        raise CopiaRecusada("a pasta DaVinci do cofre não foi criada pela cópia: não mexo nela")
    novo = cofre / f".{PASTA}.novo"
    velho = cofre / f".{PASTA}.velho"
    for resto in (novo, velho):
        if resto.exists():
            shutil.rmtree(resto)
    novo.mkdir()
    z.extractall(novo)
    if destino.exists():
        destino.rename(velho)
    (novo / PASTA).rename(destino)
    shutil.rmtree(novo, ignore_errors=True)
    shutil.rmtree(velho, ignore_errors=True)
    return sum(len(arquivos) for _, _, arquivos in os.walk(destino))


def avisar_falha(cofre: Path, motivo: str, *, notificar: bool = True) -> None:
    """Nota no cofre (só se a pasta for nossa) e notificação do macOS."""
    destino = cofre / PASTA
    if (destino / MARCA).exists():
        quando = datetime.now().strftime("%d/%m/%Y %H:%M")
        (destino / NOTA_FALHA).write_text(
            f"# A cópia do DaVinci falhou\n\n{quando}: {motivo}\n\n"
            "As notas desta pasta são da última cópia boa. A próxima cópia tenta de novo "
            "(às 03:30 ou quando o Mac ligar).\n",
            encoding="utf-8",
        )
    if notificar and sys.platform == "darwin":
        texto = motivo.replace('"', "'")[:180]
        subprocess.run(  # noqa: S603
            [
                "/usr/bin/osascript",
                "-e",
                f'display notification "{texto}" with title "{TITULO_AVISO}"',
            ],
            check=False,
            timeout=10,
        )


def copiar(
    url: str, token: str, cofre: Path, *, tentativas: int = TENTATIVAS, espera_s: float = ESPERA_S
) -> int:
    corpo = baixar(url, token, tentativas=tentativas, espera_s=espera_s)
    return trocar_pasta(cofre, conferir_zip(corpo))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--url", required=True, help="o DaVinci (ex.: https://app.hadken.com)")
    ap.add_argument(
        "--token-arquivo", required=True, help="arquivo com o token do robô do Mac mini"
    )
    ap.add_argument("--cofre", required=True, help="a pasta do cofre do Obsidian")
    args = ap.parse_args(argv)
    cofre = Path(os.path.expanduser(args.cofre))
    try:
        token = Path(os.path.expanduser(args.token_arquivo)).read_text(encoding="utf-8").strip()
        if not token:
            raise CopiaRecusada("o arquivo do token está vazio")
        n = copiar(args.url, token, cofre)
    except CopiaRecusada as e:
        print(f"{datetime.now().isoformat(timespec='seconds')} FALHOU: {e}")
        avisar_falha(cofre, str(e))
        return 1
    except OSError as e:
        motivo = f"erro no disco do Mac ({type(e).__name__})"
        print(f"{datetime.now().isoformat(timespec='seconds')} FALHOU: {motivo}")
        avisar_falha(cofre, motivo)
        return 1
    print(f"{datetime.now().isoformat(timespec='seconds')} ok: {n} arquivos em {cofre / PASTA}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
