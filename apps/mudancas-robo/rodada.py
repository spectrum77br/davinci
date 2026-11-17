#!/usr/bin/env python3
"""Robô de mudanças do DaVinci (Sistema › Mudanças) — o despertador do Mac
(com.davinci.mudancas-robo) chama a cada minuto.

Eduardo, 17/11/2026: o chefe pede correções rápidas no sistema pelo Claude
dele (`pedir_mudanca` no conector); este robô FAZ a mudança numa cópia
separada do código (worktree), roda as conferências e devolve o diff; SÓ o
Eduardo ou o Thorfinn aprovam na tela; aprovado, este robô PUBLICA (push no
main + `davinci-deploy` no servidor, com a trava e as recusas dele).

Uma rodada:
  1. Pergunta ao DaVinci se há trabalho (`/pegar`, sem IA, não gasta o plano).
  2. Nada → sai. `fazer` → Claude Code sem tela na worktree `mudanca/M-N`,
     com as REGRAS.md e a lista fechada de permissoes.json; depois o robô
     confere os arquivos (lista proibida, tamanho), roda ruff/pytest dos
     arquivos mexidos, faz o commit e manda o diff. `publicar` → confere que
     a cópia é a APROVADA (mesmo patch), rebaseia no main, confere o disco e a
     trava do servidor, empurra e roda o `davinci-deploy`.
  3. Um trabalho por vez (trava por PID); enquanto trabalha, avisa que está
     vivo a cada 5 min (senão o DaVinci devolve o pedido para a fila).

Só biblioteca padrão do Python (roda com o /usr/bin/python3 do Mac).
"""

from __future__ import annotations

import glob
import json
import os
import re
import subprocess
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

RAIZ = Path(os.environ.get("MUDANCAS_RAIZ") or Path.home() / "DaVinci" / "mudancas-robo")
CASA = Path.home()


def _carregar_env(*arquivos: Path) -> dict:
    out: dict = {}
    for arq in arquivos:
        if not arq.exists():
            continue
        for linha in arq.read_text(encoding="utf-8").splitlines():
            linha = linha.strip()
            if not linha or linha.startswith("#") or "=" not in linha:
                continue
            chave, valor = linha.split("=", 1)
            chave = chave.replace("export ", "").strip()
            out[chave] = valor.strip().strip('"').strip("'")
    return out


ENV = _carregar_env(RAIZ / ".env", RAIZ / ".env.claude")


def _cfg(chave: str, padrao: str | None = None) -> str:
    valor = ENV.get(chave) or os.environ.get(chave) or padrao
    if valor is None:
        raise SystemExit(f"falta {chave} em {RAIZ / '.env'}")
    return valor


API = _cfg("DAVINCI_API_URL").rstrip("/")
TOKEN = _cfg("MUDANCAS_AGENT_TOKEN")
ROBO = _cfg("ROBO_NOME", "mac-eduardo")
REPO = Path(os.path.expanduser(_cfg("REPO", str(CASA / "davinci"))))
PASTA = Path(os.path.expanduser(_cfg("PASTA_MUDANCAS", str(CASA / "davinci-mudancas"))))
SSH_DESTINO = _cfg("DEPLOY_SSH", "root@46.225.188.20")
SSH_CHAVE = os.path.expanduser(_cfg("DEPLOY_SSH_CHAVE", str(CASA / ".ssh" / "coolify")))
DB_TESTES = _cfg("DATABASE_URL_TESTES", "postgresql+asyncpg://admmarketing@localhost:5432/davinci_segduimp")
NODE_PATH = os.path.expanduser(_cfg("NODE_PATH_WEB", str(CASA / "davinci" / "apps" / "web" / "node_modules")))
MODELO = _cfg("MODELO", "claude-opus-5-5")
ESFORCO = _cfg("ESFORCO", "high")
LIMITE_MIN = int(_cfg("LIMITE_MINUTOS", "40"))
DISCO_MIN_GB = float(_cfg("DISCO_MIN_GB", "3.5"))
PATH_ROBO = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:" + str(CASA / ".local" / "bin")

# Arquivos que o robô NUNCA entrega (igual às REGRAS.md; o robô confere de novo
# aqui, fora do Claude — a lista de permissões pode ter furo).
PROIBIDOS = re.compile(
    r"^(apps/api/alembic/|apps/api/app/deps/|apps/api/app/security/|infra/|deploy/"
    r"|apps/executor|apps/mudancas-robo/|apps/ia-de-chamado/|\.claude/|\.github/"
    r"|caddy/|scripts/deploy)"
    r"|(^|/)(\.env[^/]*|docker-compose[^/]*|Caddyfile|pyproject\.toml|uv\.lock|package\.json"
    r"|pnpm-lock\.yaml|pnpm-workspace\.yaml)$"
)
MAX_ARQUIVOS = 12
MAX_LINHAS = 400
TRAILER = "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"


# ── utilidades ────────────────────────────────────────────────────────────


def log(msg: str) -> None:
    pasta = RAIZ / "registros"
    pasta.mkdir(parents=True, exist_ok=True)
    with open(pasta / f"{datetime.now():%Y-%m-%d}.log", "a", encoding="utf-8") as f:
        f.write(f"{datetime.now():%H:%M:%S} {msg}\n")


def api(metodo: str, caminho: str, corpo: dict | None = None, timeout: int = 60) -> dict:
    dados = json.dumps(corpo or {}).encode("utf-8")
    req = urllib.request.Request(
        f"{API}/api/sistema-mudancas-robo{caminho}",
        data=dados if metodo != "GET" else None,
        method=metodo,
        headers={"Content-Type": "application/json", "X-Mudancas-Token": TOKEN},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 — URL do .env
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        corpo_erro = e.read()[:500].decode("utf-8", "replace")
        raise RuntimeError(f"DaVinci respondeu {e.code} em {caminho}: {corpo_erro}") from e


def rodar(
    cmd: list[str], cwd: Path | None = None, env: dict | None = None, timeout: int = 600
) -> tuple[int, str]:
    try:
        p = subprocess.run(  # noqa: S603 — comandos fixos do robô
            cmd,
            cwd=str(cwd) if cwd else None,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired as e:
        saida = (e.stdout or b"") + (e.stderr or b"") if isinstance(e.stdout, bytes) else ""
        if isinstance(saida, bytes):
            saida = saida.decode("utf-8", "replace")
        return 124, f"{saida}\n(passou de {timeout // 60} min e foi interrompido)"


def git(cwd: Path, *args: str, timeout: int = 300) -> str:
    rc, out = rodar(["git", *args], cwd=cwd, env=_env_base(), timeout=timeout)
    if rc != 0:
        raise RuntimeError(f"git {' '.join(args)} falhou ({rc}): {out[-800:]}")
    return out.strip()


def _env_base() -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE")}
    env["PATH"] = PATH_ROBO
    env["HOME"] = str(CASA)
    return env


def ssh(comando: str, timeout: int = 120) -> tuple[int, str]:
    return rodar(
        [
            "ssh",
            "-i",
            SSH_CHAVE,
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=20",
            SSH_DESTINO,
            comando,
        ],
        env=_env_base(),
        timeout=timeout,
    )


def claude_bin() -> str:
    """O Claude Code: o de ~/.local/bin, ou o mais novo que vem dentro do app."""
    fixo = ENV.get("CLAUDE_BIN")
    if fixo and Path(fixo).exists():
        return fixo
    local = CASA / ".local" / "bin" / "claude"
    if local.exists():
        return str(local)
    achados = glob.glob(
        str(
            CASA
            / "Library/Application Support/Claude/claude-code/*/*/claude.app/Contents/MacOS/claude"
        )
    )
    if not achados:
        raise RuntimeError("não achei o Claude Code neste Mac")

    def _versao(caminho: str) -> tuple:
        partes = Path(caminho).parts
        v = partes[partes.index("claude-code") + 1]
        return tuple(int(x) for x in re.findall(r"\d+", v))

    return max(achados, key=_versao)


class Vivo:
    """Avisa o DaVinci a cada 5 min que o trabalho segue (renova o prazo)."""

    def __init__(self, mudanca_id: str) -> None:
        self._id = mudanca_id
        self._parar = threading.Event()
        self._t = threading.Thread(target=self._laco, daemon=True)

    def _laco(self) -> None:
        while not self._parar.wait(300):
            try:
                api("POST", f"/{self._id}/vivo", {"robo": ROBO})
            except Exception as e:  # noqa: BLE001
                log(f"vivo falhou: {e}")

    def __enter__(self) -> Vivo:
        self._t.start()
        return self

    def __exit__(self, *a: object) -> None:
        self._parar.set()


def resultado(mid: str, status: str, **dados: object) -> None:
    corpo = {"robo": ROBO, "status": status, **dados}
    for tentativa in range(3):
        try:
            api("POST", f"/{mid}/resultado", corpo, timeout=120)
            log(f"resultado {status} enviado")
            return
        except Exception as e:  # noqa: BLE001
            log(f"resultado {status} falhou (tentativa {tentativa + 1}): {e}")
            time.sleep(10)


# ── fazer ─────────────────────────────────────────────────────────────────


def _pedido_texto(t: dict) -> str:
    partes = [
        f"PEDIDO {t['codigo']} (feito por {t.get('pedido_por_nome') or '?'} pelo Claude dele):",
        f"Título: {t['titulo']}",
        f"Onde: {t.get('onde') or '(não disse)'}",
        "Descrição:",
        t["descricao"],
        "",
        "Faça a mudança seguindo as regras do sistema (append). Termine com a linha JSON.",
    ]
    return "\n".join(partes)


def _json_final(texto: str) -> dict:
    for linha in reversed([x.strip() for x in (texto or "").splitlines() if x.strip()]):
        if linha.startswith("{") and linha.endswith("}"):
            try:
                dado = json.loads(linha)
            except ValueError:
                continue
            if isinstance(dado, dict) and "resultado" in dado:
                return dado
    return {}


def _numstat(wt: Path) -> list[dict]:
    out = git(wt, "diff", "--cached", "--numstat")
    arquivos = []
    for linha in out.splitlines():
        partes = linha.split("\t")
        if len(partes) != 3:
            continue
        mais, menos, caminho = partes
        arquivos.append(
            {
                "caminho": caminho,
                "mais": int(mais) if mais.isdigit() else 0,
                "menos": int(menos) if menos.isdigit() else 0,
            }
        )
    return arquivos


def _conferencias(wt: Path, arquivos: list[dict]) -> str:
    """ruff nos .py da API mexidos e pytest nos testes mexidos (o robô roda
    de novo, fora do Claude)."""
    env = _env_base()
    env["DATABASE_URL"] = DB_TESTES
    env["DAVINCI_TEST_SCHEMA"] = "davinci_test_robo_mudancas"
    linhas: list[str] = []
    py = [
        a["caminho"].removeprefix("apps/api/")
        for a in arquivos
        if a["caminho"].startswith("apps/api/") and a["caminho"].endswith(".py")
    ]
    if py:
        rc, out = rodar(
            ["uv", "run", "--directory", "apps/api", "--no-sync", "ruff", "check", *py],
            cwd=wt,
            env=env,
            timeout=300,
        )
        linhas.append(f"[robô] ruff ({len(py)} arquivo(s)): {'ok' if rc == 0 else 'COM AVISOS'}")
        if rc != 0:
            linhas.append(out[-3000:])
    testes = [p for p in py if p.startswith("tests/") and Path(p).name.startswith("test_")]
    if testes:
        rc, out = rodar(
            [
                "uv",
                "run",
                "--directory",
                "apps/api",
                "--no-sync",
                "pytest",
                "-q",
                "-p",
                "no:cacheprovider",
                *testes,
            ],
            cwd=wt,
            env=env,
            timeout=1200,
        )
        ultima = (out.strip().splitlines() or ["?"])[-1]
        linhas.append(f"[robô] pytest {' '.join(testes)}: {ultima}")
        if rc != 0:
            linhas.append(out[-5000:])
    web = [a["caminho"] for a in arquivos if a["caminho"].startswith("apps/web/tests/")]
    for teste in web:
        envw = dict(env, NODE_PATH=NODE_PATH)
        rc, out = rodar(["node", teste], cwd=wt, env=envw, timeout=300)
        linhas.append(f"[robô] node {teste}: {'ok' if rc == 0 else 'FALHOU'}")
        if rc != 0:
            linhas.append(out[-3000:])
    return "\n".join(linhas)


def fazer(t: dict) -> None:
    mid, codigo = t["id"], t["codigo"]
    branch = f"mudanca/{codigo}"
    wt = PASTA / codigo
    log(f"{codigo}: fazer (tentativa {t.get('tentativa')}) — {t['titulo']}")
    PASTA.mkdir(parents=True, exist_ok=True)
    git(REPO, "fetch", "--prune", "origin")
    if wt.exists():
        rodar(["git", "worktree", "remove", "--force", str(wt)], cwd=REPO, env=_env_base())
    git(REPO, "worktree", "prune")
    git(REPO, "worktree", "add", "-B", branch, str(wt), "origin/main")
    if (wt / "apps/api/pyproject.toml").exists():
        rc, out = rodar(["uv", "sync", "--frozen", "--quiet"], cwd=wt / "apps/api", env=_env_base())
        if rc != 0:
            raise RuntimeError(f"uv sync falhou: {out[-800:]}")

    env = _env_base()
    env.update(
        {
            "CLAUDE_CONFIG_DIR": str(RAIZ / "claude"),
            "CLAUDE_CODE_OAUTH_TOKEN": _cfg("CLAUDE_CODE_OAUTH_TOKEN"),
            "DATABASE_URL": DB_TESTES,
            "DAVINCI_TEST_SCHEMA": "davinci_test_robo_mudancas",
            "NODE_PATH": NODE_PATH,
        }
    )
    regras = (RAIZ / "REGRAS.md").read_text(encoding="utf-8")
    cmd = [
        claude_bin(),
        "-p",
        _pedido_texto(t),
        "--append-system-prompt",
        regras,
        "--settings",
        str(RAIZ / "permissoes.json"),
        "--model",
        MODELO,
        "--effort",
        ESFORCO,
        "--permission-mode",
        "dontAsk",
        "--permission-prompts",
        "none",
        "--output-format",
        "json",
    ]
    log(f"{codigo}: Claude começou ({MODELO}, {ESFORCO})")
    rc, out = rodar(cmd, cwd=wt, env=env, timeout=LIMITE_MIN * 60)
    (RAIZ / "registros" / f"{codigo}-claude.json").write_text(out[-200_000:], encoding="utf-8")
    log(f"{codigo}: Claude terminou (saída {rc})")
    if rc == 124:
        resultado(mid, "falhou", erro=f"o robô passou de {LIMITE_MIN} min sem terminar")
        return
    try:
        saida = json.loads(out.strip().splitlines()[-1]) if out.strip() else {}
    except ValueError:
        saida = {}
    texto = str(saida.get("result") or "")
    if saida.get("is_error") or rc != 0:
        resultado(mid, "falhou", erro=f"o Claude do robô deu erro: {(texto or out)[-1500:]}")
        return
    final = _json_final(texto)
    resumo = str(final.get("resumo") or texto[-3000:])
    testes = str(final.get("testes") or "")

    git(wt, "add", "-A")
    arquivos = _numstat(wt)
    if final.get("resultado") == "precisa_equipe":
        resultado(mid, "precisa_equipe", resumo=resumo, erro=resumo, testes=testes)
        return
    if not arquivos:
        resultado(mid, "falhou", resumo=resumo, erro="o robô terminou sem mudar nenhum arquivo")
        return
    proibidos = [a["caminho"] for a in arquivos if PROIBIDOS.search(a["caminho"])]
    linhas = sum(a["mais"] + a["menos"] for a in arquivos)
    if proibidos or len(arquivos) > MAX_ARQUIVOS or linhas > MAX_LINHAS:
        motivo = (
            f"mexe em arquivo que só a equipe muda: {', '.join(proibidos)}"
            if proibidos
            else f"mudança grande demais para o robô ({len(arquivos)} arquivos, {linhas} linhas)"
        )
        resultado(mid, "precisa_equipe", resumo=resumo, erro=motivo, testes=testes)
        return

    conferencias = _conferencias(wt, arquivos)
    mensagem = (
        f"mudança {codigo}: {t['titulo']}\n\n"
        f"Pedida por {t.get('pedido_por_nome') or '?'} pelo Claude (Sistema › Mudanças),\n"
        "feita pelo robô de mudanças; só vai para o ar depois de aprovada.\n\n"
        f"{resumo[:1500]}\n\n{TRAILER}\n"
    )
    git(wt, "commit", "-q", "-m", mensagem)
    commit = git(wt, "rev-parse", "HEAD")
    diff = git(wt, "show", "--format=", "--no-color", "HEAD", timeout=120)
    resultado(
        mid,
        "pronta",
        resumo=resumo,
        testes="\n".join(x for x in (testes, conferencias) if x),
        diff=diff,
        branch=branch,
        commit=commit,
        arquivos=arquivos,
    )


# ── publicar ──────────────────────────────────────────────────────────────


def _patch_id(cwd: Path, rev: str) -> str:
    rc, out = rodar(
        ["sh", "-c", f"git show --format= {rev} | git patch-id --stable"], cwd=cwd, env=_env_base()
    )
    return out.split()[0] if rc == 0 and out.strip() else ""


def publicar(t: dict) -> None:
    mid, codigo = t["id"], t["codigo"]
    aprovado = str(t.get("commit") or "")
    branch = t.get("branch") or f"mudanca/{codigo}"
    wt = PASTA / codigo
    log(f"{codigo}: publicar (commit aprovado {aprovado[:10]})")
    if not wt.exists():
        git(REPO, "worktree", "prune")
        git(REPO, "worktree", "add", str(wt), branch)
    git(wt, "fetch", "--prune", "origin")
    patch_aprovado = _patch_id(wt, aprovado)
    if not patch_aprovado or _patch_id(wt, "HEAD") != patch_aprovado:
        resultado(mid, "falhou", erro="a cópia local não é a mudança aprovada — peça de novo")
        return

    ja_no_main = rodar(
        ["git", "merge-base", "--is-ancestor", "HEAD", "origin/main"], cwd=wt, env=_env_base()
    )[0] == 0
    if not ja_no_main:
        rc, out = rodar(["git", "rebase", "-q", "origin/main"], cwd=wt, env=_env_base())
        if rc != 0:
            rodar(["git", "rebase", "--abort"], cwd=wt, env=_env_base())
            resultado(
                mid,
                "falhou",
                erro="o sistema mudou depois da aprovação e a mudança não encaixa mais — "
                "peça de novo",
            )
            return
        if _patch_id(wt, "HEAD") != patch_aprovado:
            resultado(mid, "falhou", erro="depois de atualizar, a mudança ficou diferente da aprovada")
            return

    # Servidor: disco (build com disco cheio derruba o Redis) e trava do deploy.
    rc, out = ssh("df --output=avail -B1G / | tail -1; flock -n /run/davinci/deploy.lock true && echo LIVRE || echo OCUPADA")
    if rc != 0:
        resultado(mid, "aprovada", erro=f"não consegui falar com o servidor: {out[-300:]}")
        return
    partes = out.split()
    livre = float(partes[0]) if partes and partes[0].isdigit() else 0.0
    if livre < DISCO_MIN_GB:
        resultado(mid, "aprovada", erro=f"o servidor tem só {livre:.0f} GB livres — publicar depois")
        return
    if "OCUPADA" in out:
        resultado(mid, "aprovada", erro="outro deploy está rodando no servidor — tento de novo")
        return

    if not ja_no_main:
        rc, out = rodar(["git", "push", "-q", "origin", "HEAD:main"], cwd=wt, env=_env_base(), timeout=120)
        if rc != 0:
            resultado(mid, "aprovada", erro=f"o push foi recusado (o main andou?): {out[-300:]}")
            return
    commit = git(wt, "rev-parse", "HEAD")
    log(f"{codigo}: no main em {commit[:10]} — deploy")
    rc, out = ssh("SUDO_USER=robo-mudancas /usr/local/sbin/davinci-deploy", timeout=45 * 60)
    (RAIZ / "registros" / f"{codigo}-deploy.log").write_text(out[-100_000:], encoding="utf-8")
    log(f"{codigo}: deploy terminou (saída {rc})")
    if rc == 0:
        resultado(mid, "publicada", commit=commit)
    elif rc == 4:
        resultado(mid, "aprovada", erro="outro deploy pegou a trava antes — tento de novo")
    elif rc == 5:
        # "PUBLICADO COM PROBLEMA" já foi alarme falso (contêiner ainda subindo).
        time.sleep(60)
        rc2, ps = ssh(
            "docker ps --format '{{.Names}} {{.Status}}' | grep -E 'davinci-(api|web|worker)'"
        )
        if rc2 == 0 and ps.strip() and all(
            "Up" in ln and "unhealthy" not in ln and "Restarting" not in ln
            for ln in ps.strip().splitlines()
        ):
            resultado(mid, "publicada", commit=commit)
        else:
            resultado(mid, "falhou", erro=f"publicado com problema — conferir: {out[-1500:]}")
    else:
        resultado(mid, "falhou", erro=f"o deploy recusou ou falhou (saída {rc}): {out[-1500:]}")


# ── a rodada ──────────────────────────────────────────────────────────────


def main() -> int:
    (RAIZ / "estado").mkdir(parents=True, exist_ok=True)
    trava = RAIZ / "estado" / "rodada.trava"
    if trava.exists():
        try:
            os.kill(int(trava.read_text().strip()), 0)
            return 0  # a rodada anterior ainda está trabalhando
        except (ValueError, ProcessLookupError, PermissionError):
            pass
    trava.write_text(str(os.getpid()))
    try:
        (RAIZ / "estado" / "ultima-rodada").write_text(f"{datetime.now():%Y-%m-%d %H:%M:%S}")
        try:
            t = api("POST", "/pegar", {"robo": ROBO}).get("trabalho")
        except Exception as e:  # noqa: BLE001
            log(f"pegar falhou: {e}")
            return 1
        if not t:
            return 0
        with Vivo(t["id"]):
            try:
                if t.get("acao") == "publicar":
                    publicar(t)
                else:
                    fazer(t)
            except Exception as e:  # noqa: BLE001
                log(f"{t.get('codigo')}: ERRO {e}\n{traceback.format_exc()}")
                resultado(t["id"], "falhou", erro=f"erro do robô: {str(e)[:1500]}")
        return 0
    finally:
        trava.unlink(missing_ok=True)


if __name__ == "__main__":
    sys.exit(main())
