"""O robô de mudanças (rodada.py) de ponta a ponta, sem rede e sem IA:
um "origin" de mentira (repositório nu), um Claude de mentira (script que edita
um arquivo e responde no formato do --output-format json), o DaVinci de
mentira (fila em memória) e o servidor de mentira (ssh falso).

Rodar: uv run --directory apps/api --no-sync pytest ../mudancas-robo/tests -q
"""

from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

AQUI = Path(__file__).resolve().parent.parent


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def mundo(tmp_path, monkeypatch):
    raiz = tmp_path / "raiz"
    raiz.mkdir()
    for nome in ("REGRAS.md", "permissoes.json"):
        (raiz / nome).write_text((AQUI / nome).read_text())
    origem = tmp_path / "origem.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origem))
    semente = tmp_path / "semente"
    _git(tmp_path, "clone", "-q", str(origem), str(semente))
    (semente / "apps/web").mkdir(parents=True)
    (semente / "apps/web/texto.txt").write_text("Olá\n")
    (semente / "outro.txt").write_text("um\n")
    _git(semente, "add", "-A")
    _git(semente, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "início")
    _git(semente, "push", "-q", "origin", "HEAD:main")
    repo = tmp_path / "repo"
    _git(tmp_path, "clone", "-q", str(origem), str(repo))
    _git(repo, "config", "user.email", "robo@t")
    _git(repo, "config", "user.name", "robo")

    claude = tmp_path / "claude_falso.py"
    claude.write_text(
        "import json, os, pathlib, sys\n"
        "acao = os.environ.get('FALSO_ACAO', 'texto')\n"
        "if acao == 'texto':\n"
        "    pathlib.Path('apps/web/texto.txt').write_text('Oi\\n')\n"
        "    fim = {'resultado': 'pronta', 'resumo': 'Troquei Olá por Oi', 'testes': 'nada'}\n"
        "elif acao == 'migration':\n"
        "    p = pathlib.Path('apps/api/alembic/versions'); p.mkdir(parents=True)\n"
        "    (p / 'x.py').write_text('x = 1\\n')\n"
        "    fim = {'resultado': 'pronta', 'resumo': 'criei migration', 'testes': ''}\n"
        "elif acao == 'equipe':\n"
        "    fim = {'resultado': 'precisa_equipe', 'resumo': 'Precisa de coluna nova', 'testes': ''}\n"
        "assert '--permission-mode' in sys.argv and 'dontAsk' in sys.argv\n"
        "assert os.environ.get('CLAUDE_CODE_OAUTH_TOKEN') == 'tok-falso'\n"
        "texto = 'Pronto.\\n' + json.dumps(fim, ensure_ascii=False)\n"
        "print(json.dumps({'type': 'result', 'is_error': False, 'result': texto}))\n"
    )
    exe = tmp_path / "claude"
    exe.write_text(f"#!/bin/sh\nexec {sys.executable} {claude} \"$@\"\n")
    exe.chmod(0o755)

    monkeypatch.setenv("MUDANCAS_RAIZ", str(raiz))
    monkeypatch.setenv("DAVINCI_API_URL", "http://davinci.invalido")
    monkeypatch.setenv("MUDANCAS_AGENT_TOKEN", "t")
    sys.path.insert(0, str(AQUI))
    import rodada

    rodada = importlib.reload(rodada)
    monkeypatch.setattr(rodada, "RAIZ", raiz)
    monkeypatch.setattr(rodada, "REPO", repo)
    monkeypatch.setattr(rodada, "PASTA", tmp_path / "mudancas")
    monkeypatch.setitem(rodada.ENV, "CLAUDE_BIN", str(exe))
    monkeypatch.setitem(rodada.ENV, "CLAUDE_CODE_OAUTH_TOKEN", "tok-falso")

    fila: list[dict] = []
    enviados: list[tuple[str, dict]] = []

    def _api(metodo, caminho, corpo=None, timeout=60):
        if caminho == "/pegar":
            return {"trabalho": fila.pop(0) if fila else None}
        enviados.append((caminho, corpo or {}))
        return {"ok": True}

    monkeypatch.setattr(rodada, "api", _api)
    servidor = {"livre": "10", "trava": "LIVRE", "deploy_rc": 0, "comandos": []}

    def _ssh(comando, timeout=120):
        servidor["comandos"].append(comando)
        if comando.startswith("df "):
            return 0, f"{servidor['livre']}\n{servidor['trava']}\n"
        if "davinci-deploy" in comando:
            return servidor["deploy_rc"], "== ok"
        return 0, "davinci-api-1 Up 2 minutes (healthy)\n"

    monkeypatch.setattr(rodada, "ssh", _ssh)
    yield {
        "rodada": rodada,
        "fila": fila,
        "enviados": enviados,
        "origem": origem,
        "semente": semente,
        "servidor": servidor,
        "raiz": raiz,
    }
    sys.path.remove(str(AQUI))


def _trabalho(acao="fazer", **extra):
    return {
        "id": "id-1",
        "codigo": "M-1",
        "acao": acao,
        "titulo": "Trocar o texto",
        "descricao": "Onde diz Olá, deve dizer Oi",
        "onde": "tela X",
        "pedido_por_nome": "harry potter",
        "tentativa": 1,
        **extra,
    }


def _resultados(m) -> list[dict]:
    return [c for p, c in m["enviados"] if p.endswith("/resultado")]


def test_sem_trabalho_sai_quieto(mundo):
    assert mundo["rodada"].main() == 0
    assert mundo["enviados"] == []
    assert (mundo["raiz"] / "estado" / "ultima-rodada").exists()
    assert not (mundo["raiz"] / "estado" / "rodada.trava").exists()


def test_fazer_e_publicar(mundo, monkeypatch):
    m = mundo
    m["fila"].append(_trabalho())
    m["rodada"].main()
    [r] = _resultados(m)
    assert r["status"] == "pronta", r
    assert r["branch"] == "mudanca/M-1" and len(r["commit"]) == 40
    assert "-Olá" in r["diff"] and "+Oi" in r["diff"]
    assert r["arquivos"] == [{"caminho": "apps/web/texto.txt", "mais": 1, "menos": 1}]
    assert r["resumo"] == "Troquei Olá por Oi"
    # nada foi para o origin ainda
    assert "Oi" not in subprocess.run(
        ["git", "show", "main:apps/web/texto.txt"],
        cwd=m["origem"], capture_output=True, text=True,
    ).stdout

    # alguém publica outra coisa no main antes da aprovação (sem conflito)
    (m["semente"] / "outro.txt").write_text("dois\n")
    _git(m["semente"], "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qam", "outro")
    _git(m["semente"], "push", "-q", "origin", "HEAD:main")

    m["fila"].append(_trabalho("publicar", commit=r["commit"], branch=r["branch"]))
    m["rodada"].main()
    r2 = _resultados(m)[-1]
    assert r2["status"] == "publicada", r2
    main_texto = subprocess.run(
        ["git", "show", "main:apps/web/texto.txt"], cwd=m["origem"], capture_output=True, text=True
    ).stdout
    assert main_texto == "Oi\n"
    assert any("davinci-deploy" in c for c in m["servidor"]["comandos"])
    assert r2["commit"] != r["commit"]  # rebaseado em cima do "outro"


def test_publicar_espera_disco_e_trava(mundo):
    m = mundo
    m["fila"].append(_trabalho())
    m["rodada"].main()
    commit = _resultados(m)[-1]["commit"]
    m["servidor"]["livre"] = "2"
    m["fila"].append(_trabalho("publicar", commit=commit, branch="mudanca/M-1"))
    m["rodada"].main()
    r = _resultados(m)[-1]
    assert r["status"] == "aprovada" and "GB livres" in r["erro"]
    m["servidor"]["livre"] = "10"
    m["servidor"]["trava"] = "OCUPADA"
    m["fila"].append(_trabalho("publicar", commit=commit, branch="mudanca/M-1"))
    m["rodada"].main()
    r = _resultados(m)[-1]
    assert r["status"] == "aprovada" and "outro deploy" in r["erro"]
    assert not any("davinci-deploy" in c for c in m["servidor"]["comandos"])


def test_publicar_recusa_copia_diferente_da_aprovada(mundo):
    m = mundo
    m["fila"].append(_trabalho())
    m["rodada"].main()
    wt = m["rodada"].PASTA / "M-1"
    (wt / "outro.txt").write_text("mexido depois\n")
    _git(wt, "commit", "-qam", "mexida fora do robô")
    m["fila"].append(
        _trabalho("publicar", commit=_resultados(m)[-1]["commit"], branch="mudanca/M-1")
    )
    m["rodada"].main()
    r = _resultados(m)[-1]
    assert r["status"] == "falhou" and "não é a mudança aprovada" in r["erro"]


def test_conflito_com_o_main_falha_sem_empurrar(mundo):
    m = mundo
    m["fila"].append(_trabalho())
    m["rodada"].main()
    commit = _resultados(m)[-1]["commit"]
    (m["semente"] / "apps/web/texto.txt").write_text("Olá, mudado por outra pessoa\n")
    _git(m["semente"], "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qam", "x")
    _git(m["semente"], "push", "-q", "origin", "HEAD:main")
    m["fila"].append(_trabalho("publicar", commit=commit, branch="mudanca/M-1"))
    m["rodada"].main()
    r = _resultados(m)[-1]
    assert r["status"] == "falhou" and "não encaixa" in r["erro"]
    assert not any("davinci-deploy" in c for c in m["servidor"]["comandos"])


def test_arquivo_proibido_vira_precisa_equipe(mundo, monkeypatch):
    monkeypatch.setenv("FALSO_ACAO", "migration")
    mundo["fila"].append(_trabalho())
    mundo["rodada"].main()
    r = _resultados(mundo)[-1]
    assert r["status"] == "precisa_equipe" and "alembic" in r["erro"]


def test_claude_pede_a_equipe(mundo, monkeypatch):
    monkeypatch.setenv("FALSO_ACAO", "equipe")
    mundo["fila"].append(_trabalho())
    mundo["rodada"].main()
    r = _resultados(mundo)[-1]
    assert r["status"] == "precisa_equipe" and r["erro"] == "Precisa de coluna nova"


def test_permissoes_bloqueiam_o_perigoso():
    regras = json.loads((AQUI / "permissoes.json").read_text())["permissions"]
    for item in ("Bash(git push*)", "Bash(curl *)", "Bash(ssh *)", "WebFetch", "Read(**/.env)",
                 "Read(~/.ssh/**)", "Edit(apps/api/alembic/**)", "Bash(git commit*)"):
        assert item in regras["deny"], item
    assert "Read" not in regras["allow"] and "Read(./**)" in regras["allow"]
    padrao = __import__("re").compile
    rodada_src = (AQUI / "rodada.py").read_text()
    assert "PROIBIDOS" in rodada_src and padrao(r"MAX_ARQUIVOS = \d+").search(rodada_src)
    assert os.access(AQUI / "instalar.sh", os.X_OK) and os.access(AQUI / "guardar_token.sh", os.X_OK)
