"""O robô de mudanças (rodada.py) de ponta a ponta, sem rede e sem IA:
um "origin" de mentira (repositório nu), um Claude de mentira (script que edita
arquivos e responde no formato do --output-format json), o DaVinci de mentira
(fila em memória) e o servidor de mentira (ssh falso).

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
ARQ = "apps/web/lib/texto.ts"
VELHO = "export const t = 'Olá'\n"
NOVO = "export const t = 'Oi'\n"


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def _commit(cwd: Path, msg: str) -> None:
    _git(cwd, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qam", msg)


@pytest.fixture
def mundo(tmp_path, monkeypatch):
    raiz = tmp_path / "raiz"
    raiz.mkdir()
    pasta = tmp_path / "mudancas"
    for nome in ("REGRAS.md", "REGRAS_PERGUNTA.md", "caixa.sb", "testar.sh", "caixa_pytest.py",
                 "conferir_web.cjs"):
        (raiz / nome).write_text((AQUI / nome).read_text())
    (raiz / "permissoes.json").write_text(
        (AQUI / "permissoes.json").read_text().replace("__RAIZ__", str(raiz))
    )
    (raiz / ".env").write_text(f"PASTA_MUDANCAS={pasta}\n")
    origem = tmp_path / "origem.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origem))
    semente = tmp_path / "semente"
    _git(tmp_path, "clone", "-q", str(origem), str(semente))
    (semente / "apps/web/lib").mkdir(parents=True)
    (semente / ARQ).write_text(VELHO)
    (semente / "outro.txt").write_text("um\n")
    _git(semente, "add", "-A")
    _commit(semente, "início")
    _git(semente, "push", "-q", "origin", "HEAD:main")
    repo = tmp_path / "repo"
    _git(tmp_path, "clone", "-q", str(origem), str(repo))

    claude = tmp_path / "claude_falso.py"
    claude.write_text(
        "import json, os, pathlib, sys\n"
        "acao = pathlib.Path(__file__).with_name('acao').read_text()\n"
        f"ARQ, NOVO = {ARQ!r}, {NOVO!r}\n"
        "assert '--permission-mode' in sys.argv and 'dontAsk' in sys.argv\n"
        "a = sys.argv\n"
        "ferramentas = 'Read,Glob,Grep' if acao.startswith('pergunta') else 'Read,Edit,Write,Glob,Grep'\n"
        "assert a[a.index('--tools') + 1] == ferramentas, a[a.index('--tools') + 1]\n"
        "assert a[a.index('--setting-sources') + 1] == 'user' and '--strict-mcp-config' in a\n"
        "assert os.environ.get('CLAUDE_CODE_OAUTH_TOKEN') == 'tok-falso'\n"
        "assert os.environ.get('CLAUDE_CODE_DISABLE_AUTO_MEMORY') == '1'\n"
        "cfg = pathlib.Path(os.environ['CLAUDE_CONFIG_DIR'])\n"
        "assert cfg.is_dir() and cfg.name.startswith(('M-1-', 'P-1-')), cfg\n"
        "(cfg / 'sobra').write_text('x')  # tem de sumir depois\n"
        "assert 'SSH_AUTH_SOCK' not in os.environ\n"
        "if acao == 'texto':\n"
        "    pathlib.Path(ARQ).write_text(NOVO)\n"
        "    fim = {'resultado': 'pronta', 'resumo': 'Troquei Olá por Oi', 'testes': 'nada'}\n"
        "elif acao == 'migration':\n"
        "    p = pathlib.Path('apps/api/alembic/versions'); p.mkdir(parents=True)\n"
        "    (p / 'x.py').write_text('x = 1\\n')\n"
        "    fim = {'resultado': 'pronta', 'resumo': 'criei migration', 'testes': ''}\n"
        "elif acao == 'atributos':\n"
        "    pathlib.Path('.gitattributes').write_text('*.ts -diff\\n')\n"
        "    pathlib.Path(ARQ).write_text(NOVO)\n"
        "    fim = {'resultado': 'pronta', 'resumo': 'x', 'testes': ''}\n"
        "elif acao == 'gitfile':\n"
        "    pathlib.Path('.git').write_text('gitdir: /tmp/repo-do-atacante\\n')\n"
        "    pathlib.Path(ARQ).write_text(NOVO)\n"
        "    fim = {'resultado': 'pronta', 'resumo': 'x', 'testes': ''}\n"
        "elif acao == 'teste_quebrado':\n"
        "    p = pathlib.Path('apps/web/tests'); p.mkdir(parents=True)\n"
        "    (p / 'quebra.cjs').write_text(\"require('fs').writeFileSync('RODOU.txt','x');process.exit(1)\\n\")\n"
        "    fim = {'resultado': 'pronta', 'resumo': 'teste novo', 'testes': ''}\n"
        "elif acao == 'hatch':\n"
        "    pathlib.Path('apps/api').mkdir(parents=True, exist_ok=True)\n"
        "    pathlib.Path('apps/api/hatch.toml').write_text('[build.hooks.custom]\\n')\n"
        "    fim = {'resultado': 'pronta', 'resumo': 'x', 'testes': ''}\n"
        "elif acao == 'fora':\n"
        "    pathlib.Path('docs').mkdir()\n"
        "    pathlib.Path('docs/x.md').write_text('oi\\n')\n"
        "    fim = {'resultado': 'pronta', 'resumo': 'x', 'testes': ''}\n"
        "elif acao == 'invisivel':\n"
        "    pathlib.Path(ARQ).write_text(\"export const t = 'Oi' // \\u202e ok\\n\")\n"
        "    fim = {'resultado': 'pronta', 'resumo': 'x', 'testes': ''}\n"
        "elif acao == 'tela_quebrada':\n"
        "    pathlib.Path(ARQ).write_text(\"export const t: number = (1\\n\")\n"
        "    fim = {'resultado': 'pronta', 'resumo': 'x', 'testes': ''}\n"
        "elif acao == 'pergunta':\n"
        "    assert not any(pathlib.Path('.').glob('**/*.novo'))\n"
        "    resposta = ('A Margem segura o pedido quando a margem fica negativa.\\n'\n"
        "                '```python\\nTOKEN = \"abcdef123456789\"\\nchave = \"sk-ant-AAAAAAAAAAAAAAAAAAAAAAAAAAAA\"\\n```')\n"
        "    print(json.dumps({'type': 'result', 'is_error': False, 'result': resposta}))\n"
        "    print('⚠ Permission mode forced to default — aviso de verdade do Claude Code', file=sys.stderr)\n"
        "    sys.exit(0)\n"
        "elif acao == 'pergunta_limite':\n"
        "    print(json.dumps({'type': 'result', 'is_error': True, 'api_error_status': 429,\n"
        "                      'result': 'Claude AI usage limit reached|1763400000'}))\n"
        "    sys.exit(1)\n"
        "elif acao == 'pergunta_sobrecarga':\n"
        "    print(json.dumps({'type': 'result', 'is_error': True,\n"
        "                      'result': 'API Error: Repeated 529 Overloaded errors'}))\n"
        "    sys.exit(1)\n"
        "elif acao == 'pergunta_vazia':\n"
        "    print(json.dumps({'type': 'result', 'is_error': False, 'result': ''}))\n"
        "    sys.exit(0)\n"
        "elif acao == 'equipe':\n"
        "    fim = {'resultado': 'precisa_equipe', 'resumo': 'Precisa de coluna nova', 'testes': ''}\n"
        "texto = 'Pronto.\\n' + json.dumps(fim, ensure_ascii=False)\n"
        "print(json.dumps({'type': 'result', 'is_error': False, 'result': texto}))\n"
        "print('⚠ Permission mode forced to default — aviso de verdade do Claude Code', file=sys.stderr)\n"
    )
    exe = tmp_path / "claude"
    exe.write_text(f"#!/bin/sh\nexec {sys.executable} {claude} \"$@\"\n")
    exe.chmod(0o755)
    arq_acao = tmp_path / "acao"
    arq_acao.write_text("texto")

    monkeypatch.setenv("MUDANCAS_RAIZ", str(raiz))
    monkeypatch.setenv("DAVINCI_API_URL", "http://davinci.invalido")
    monkeypatch.setenv("MUDANCAS_AGENT_TOKEN", "t")
    monkeypatch.setenv("SSH_AUTH_SOCK", "/tmp/agente-falso")
    sys.path.insert(0, str(AQUI))
    import rodada

    rodada = importlib.reload(rodada)
    monkeypatch.setattr(rodada, "RAIZ", raiz)
    monkeypatch.setattr(rodada, "TESTAR", raiz / "testar.sh")
    monkeypatch.setattr(rodada, "REPO", repo)
    monkeypatch.setattr(rodada, "PASTA", pasta)
    monkeypatch.setitem(rodada.ENV, "CLAUDE_BIN", str(exe))
    monkeypatch.setitem(rodada.ENV, "CLAUDE_CODE_OAUTH_TOKEN", "tok-falso")
    monkeypatch.setattr(rodada.time, "sleep", lambda s: None)

    fila: list[dict] = []
    enviados: list[tuple[str, dict]] = []
    api_fora = {"vezes": 0}

    def _api(metodo, caminho, corpo=None, timeout=60):
        if caminho == "/pegar":
            return {"trabalho": fila.pop(0) if fila else None}
        if api_fora["vezes"] > 0:
            api_fora["vezes"] -= 1
            raise OSError("rede caiu")
        enviados.append((caminho, corpo or {}))
        return {"ok": True}

    monkeypatch.setattr(rodada, "api", _api)
    servidor = {
        "head": None, "livre": "20000000000", "trava": "LIVRE", "build": "NAO",
        "registrado": None, "desde": "", "img_api": "", "img_web": "", "deploy_rc": "0",
        "livre_depois_da_limpeza": None, "comandos": [],
    }

    def _ssh(comando, timeout=120):
        servidor["comandos"].append(comando)
        if comando.startswith("cd /opt/davinci"):
            head = servidor["head"] or _git(origem, "rev-parse", "main")
            reg = servidor["registrado"] if servidor["registrado"] is not None else head[:8]
            linhas = [
                f"head={head}", f"livre={servidor['livre']}", f"trava={servidor['trava']}",
                f"build={servidor['build']}", f"registrado={reg}", f"desde={servidor['desde']}",
                f"img_api={servidor['img_api']}", f"img_web={servidor['img_web']}",
            ]
            return 0, "\n".join(linhas) + "\n"
        if comando.startswith("flock -n /run/davinci/deploy.lock sh -c"):
            if servidor["trava"] != "LIVRE" or servidor["build"] != "NAO":
                return 0, "limpo=NAO\n"
            if servidor["livre_depois_da_limpeza"] is not None:
                servidor["livre"] = servidor["livre_depois_da_limpeza"]
            return 0, "limpo=SIM\n"
        if "setsid -f" in comando:
            return 0, "INICIADO\n"
        if comando.startswith("cat "):
            return 0, servidor["deploy_rc"] + "\n"
        if comando.startswith("tail "):
            return 0, "== ok"
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
        "acao": arq_acao,
        "api_fora": api_fora,
        "repo": repo,
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
        "adiamentos": 0,
        **extra,
    }


def _resultados(m) -> list[dict]:
    return [c for p, c in m["enviados"] if p.endswith("/resultado")]


def _fazer(m) -> dict:
    m["fila"].append(_trabalho())
    m["rodada"].main()
    return _resultados(m)[-1]


def _publicar(m, r, **extra) -> dict:
    m["fila"].append(_trabalho("publicar", commit=r["commit"], branch=r["branch"], **extra))
    m["rodada"].main()
    return _resultados(m)[-1]


def _main_texto(m) -> str:
    return subprocess.run(
        ["git", "show", f"main:{ARQ}"], cwd=m["origem"], capture_output=True, text=True
    ).stdout


def test_sem_trabalho_sai_quieto(mundo):
    assert mundo["rodada"].main() == 0
    assert mundo["enviados"] == []
    assert (mundo["raiz"] / "estado" / "ultima-rodada").exists()
    assert not (mundo["raiz"] / "estado" / "rodada.trava").exists()


def test_fazer_e_publicar(mundo):
    m = mundo
    r = _fazer(m)
    assert r["status"] == "pronta", r
    assert r["branch"] == "mudanca/M-1" and len(r["commit"]) == 40
    assert "-export const t = 'Olá'" in r["diff"] and "+export const t = 'Oi'" in r["diff"]
    assert r["arquivos"] == [{"caminho": ARQ, "mais": 1, "menos": 1}]
    assert r["resumo"] == "Troquei Olá por Oi"
    assert "Nada desta mudança rodou" in r["testes"]
    assert _main_texto(m) == VELHO  # nada foi para o origin ainda
    assert not list((m["rodada"].PASTA / ".claude-robo").glob("M-1-*"))  # config do Claude apagada

    # outra pessoa publica (e o servidor já está com ela) antes da aprovação
    (m["semente"] / "outro.txt").write_text("dois\n")
    _commit(m["semente"], "outro")
    _git(m["semente"], "push", "-q", "origin", "HEAD:main")

    r2 = _publicar(m, r)
    assert r2["status"] == "publicada", r2
    assert _main_texto(m) == NOVO
    assert any("setsid -f" in c and "setsid -f" in c for c in m["servidor"]["comandos"])
    conf = (m["raiz"] / "registros" / "M-1-conferencias.log").read_text()
    assert "a tela compila (1 arquivo(s)): ok" in conf  # o .ts mexido foi compilado na caixa
    assert r2["commit"] != r["commit"]  # rebaseado em cima do "outro"
    assert not (m["rodada"].PASTA / "M-1").exists()  # cópia removida depois do ar


def test_nao_publica_com_mudanca_de_outra_pessoa_pendente(mundo):
    m = mundo
    r = _fazer(m)
    m["servidor"]["head"] = "f" * 40  # o servidor ainda não tem o main de agora
    r2 = _publicar(m, r)
    assert r2["status"] == "aprovada" and "outras pessoas" in r2["erro"]
    assert _main_texto(m) == VELHO
    assert not any("setsid -f" in c for c in m["servidor"]["comandos"])


def test_publicar_espera_disco_e_trava(mundo):
    m = mundo
    r = _fazer(m)
    m["servidor"]["livre"] = "5900000000"  # 5,9 GB: o df -B1G mostraria "6"
    r2 = _publicar(m, r)
    assert r2["status"] == "aprovada" and "5.9 GB livres mesmo depois de apagar" in r2["erro"], r2
    assert any(c.startswith("flock -n /run/davinci/deploy.lock sh -c") for c in m["servidor"]["comandos"])
    m["servidor"]["livre"] = "20000000000"
    m["servidor"]["trava"] = "OCUPADA"
    r2 = _publicar(m, r)
    assert r2["status"] == "aprovada" and "outro deploy" in r2["erro"]
    m["servidor"]["trava"] = "LIVRE"
    m["servidor"]["build"] = "SIM"  # alguém rodando docker compose na mão
    r2 = _publicar(m, r)
    assert r2["status"] == "aprovada" and "build" in r2["erro"]
    assert not any("setsid -f" in c for c in m["servidor"]["comandos"])


def test_disco_curto_limpa_o_que_nao_interfere_e_publica(mundo):
    m = mundo
    r = _fazer(m)
    m["servidor"]["livre"] = "4600000000"
    m["servidor"]["livre_depois_da_limpeza"] = "6200000000"
    r2 = _publicar(m, r)
    assert r2["status"] == "publicada", r2
    limpeza = [c for c in m["servidor"]["comandos"] if c.startswith("flock -n")]
    assert limpeza and "docker builder prune -f" in limpeza[0] and "docker image prune -f" in limpeza[0]
    # nunca o que interfere: volumes, tudo do cache (-a), containers, sistema
    for perigo in ("volume", "prune -a", "prune --all", "system prune", "container", "rm -"):
        assert perigo not in limpeza[0], perigo


def test_nao_limpa_com_build_de_outra_pessoa_rodando(mundo):
    m = mundo
    r = _fazer(m)
    m["servidor"]["livre"] = "4600000000"
    m["servidor"]["build"] = "SIM"
    m["servidor"]["livre_depois_da_limpeza"] = "6200000000"
    r2 = _publicar(m, r)
    assert r2["status"] == "aprovada"
    assert not any(c.startswith("flock -n") for c in m["servidor"]["comandos"])


def test_deploy_que_falha_nao_repete_as_cegas(mundo):
    m = mundo
    r = _fazer(m)
    m["servidor"]["deploy_rc"] = "1"  # o build falhou
    r2 = _publicar(m, r)
    assert r2["status"] == "falhou" and "JÁ no main" in r2["erro"] and "git revert" in r2["erro"]
    assert _main_texto(m) == NOVO
    assert sum("setsid -f" in c for c in m["servidor"]["comandos"]) == 1


def test_deploy_que_nem_comeca_adia(mundo):
    m = mundo
    r = _fazer(m)
    m["servidor"]["deploy_rc"] = "3"  # davinci-deploy RECUSADO (servidor fora do main…)
    r2 = _publicar(m, r)
    assert r2["status"] == "aprovada" and "não começou" in r2["erro"]


def test_ja_no_main_refaz_so_o_deploy_ou_vira_publicada(mundo):
    m = mundo
    antes = _git(m["origem"], "rev-parse", "main")
    r = _fazer(m)
    m["servidor"]["deploy_rc"] = "3"
    _publicar(m, r)  # empurrou; o deploy não começou
    pushes = _git(m["origem"], "rev-list", "--count", "main")
    # o servidor ainda roda o de antes: só refaz o deploy, sem push de novo
    m["servidor"]["registrado"] = antes[:8]
    m["servidor"]["deploy_rc"] = "0"
    m["servidor"]["comandos"].clear()
    r3 = _publicar(m, r)
    assert r3["status"] == "publicada", r3
    assert _git(m["origem"], "rev-list", "--count", "main") == pushes
    assert any("setsid -f" in c for c in m["servidor"]["comandos"])
    # alguém já publicou (o registrado tem a mudança): publicada sem deploy
    m["servidor"]["registrado"] = None
    m["servidor"]["comandos"].clear()
    r4 = _publicar(m, r)
    assert r4["status"] == "publicada"
    assert not any("setsid -f" in c for c in m["servidor"]["comandos"])


def test_mudanca_desfeita_no_main_nao_vira_publicada(mundo):
    m = mundo
    r = _fazer(m)
    m["servidor"]["deploy_rc"] = "3"
    _publicar(m, r)  # empurrou; o deploy não começou
    _git(m["semente"], "pull", "-q", "origin", "main")
    _git(m["semente"], "-c", "user.email=t@t", "-c", "user.name=t", "revert", "--no-edit", "HEAD")
    _git(m["semente"], "push", "-q", "origin", "HEAD:main")
    m["servidor"]["deploy_rc"] = "0"
    r3 = _publicar(m, r)
    assert r3["status"] == "falhou" and "desfeita" in r3["erro"]


def test_servidor_com_git_na_frente_do_que_esta_no_ar(mundo):
    """O git do servidor no main não prova que o main está no ar (deploy que
    falhou no build, reset --hard de outra sessão): o robô espera."""
    m = mundo
    antes = _git(m["origem"], "rev-parse", "main")
    r = _fazer(m)
    (m["semente"] / "outro.txt").write_text("dois\n")
    _commit(m["semente"], "outro, ainda não no ar")
    _git(m["semente"], "push", "-q", "origin", "HEAD:main")
    m["servidor"]["registrado"] = antes[:8]  # o davinci-deploy registrou o de antes
    r2 = _publicar(m, r)
    assert r2["status"] == "aprovada" and "ainda não foram ao ar" in r2["erro"], r2
    assert not any("setsid -f" in c for c in m["servidor"]["comandos"])
    # deploy feito à mão: imagens mais novas do que o HEAD → está no ar
    m["servidor"].update(desde="1000", img_api="2000", img_web="2000")
    r3 = _publicar(m, r)
    assert r3["status"] == "publicada", r3


def test_publicar_usa_copia_nova_do_commit_aprovado(mundo):
    """O que ficou na pasta depois do Claude (commit a mais, arquivo ignorado)
    não vai junto: a publicação parte do commit aprovado."""
    m = mundo
    r = _fazer(m)
    wt = m["rodada"].PASTA / "M-1"
    (wt / "outro.txt").write_text("mexido depois\n")
    _commit(wt, "mexida fora do robô")
    (wt / "plantado.txt").write_text("x\n")
    r2 = _publicar(m, r)
    assert r2["status"] == "publicada", r2
    mostra = subprocess.run(["git", "show", "main:outro.txt"], cwd=m["origem"],
                            capture_output=True, text=True).stdout
    assert mostra == "um\n" and _main_texto(m) == NOVO


def test_nada_roda_antes_de_aprovar_e_teste_quebrado_nao_publica(mundo):
    m = mundo
    m["acao"].write_text("teste_quebrado")
    r = _fazer(m)
    assert r["status"] == "pronta", r
    assert not list(m["rodada"].PASTA.rglob("RODOU.txt"))  # o fazer não rodou nada
    r2 = _publicar(m, r)
    assert r2["status"] == "falhou" and "conferências falharam" in r2["erro"], r2
    assert list(m["rodada"].PASTA.rglob("RODOU.txt"))  # rodou só depois, na caixa
    assert "quebra.cjs" not in _git(m["origem"], "ls-tree", "-r", "--name-only", "main")
    assert not any("setsid -f" in c for c in m["servidor"]["comandos"])


def test_commit_com_cara_de_comando_nao_roda(mundo, tmp_path):
    m = mundo
    alvo = tmp_path / "pwned"
    r = {"commit": f"HEAD;touch {alvo};#", "branch": "mudanca/M-1"}
    r2 = _publicar(m, r)
    assert r2["status"] == "falhou" and not alvo.exists()
    r2 = _publicar(m, {"commit": "a" * 40, "branch": "origin/main"})
    assert r2["status"] == "falhou" and "estranho" in r2["erro"]


def test_conflito_com_o_main_falha_sem_empurrar(mundo):
    m = mundo
    r = _fazer(m)
    (m["semente"] / ARQ).write_text("export const t = 'mudado por outra pessoa'\n")
    _commit(m["semente"], "x")
    _git(m["semente"], "push", "-q", "origin", "HEAD:main")
    r2 = _publicar(m, r)
    assert r2["status"] == "falhou" and "não encaixa" in r2["erro"]
    assert not any("setsid -f" in c for c in m["servidor"]["comandos"])


def test_arquivo_proibido_vira_precisa_equipe(mundo):
    mundo["acao"].write_text("migration")
    r = _fazer(mundo)
    assert r["status"] == "precisa_equipe" and "alembic" in r["erro"]


def test_gitattributes_nao_esconde_o_diff(mundo):
    mundo["acao"].write_text("atributos")
    r = _fazer(mundo)
    assert r["status"] == "precisa_equipe" and ".gitattributes" in r["erro"]


def test_arquivo_git_mexido_nao_passa(mundo):
    """O .git da cópia aponta o git do robô (fora da caixa) para um repositório
    do atacante, com config que executa comando: o robô para."""
    mundo["acao"].write_text("gitfile")
    r = _fazer(mundo)
    assert r["status"] == "falhou" and ".git" in r["erro"]


def test_arquivo_fora_das_pastas_permitidas(mundo):
    mundo["acao"].write_text("hatch")  # hatch.toml faria o uv sync rodar código
    r = _fazer(mundo)
    assert r["status"] == "precisa_equipe" and "hatch.toml" in r["erro"], r
    mundo["fila"].clear()
    mundo["acao"].write_text("fora")
    m2 = _fazer(mundo)
    assert m2["status"] == "precisa_equipe" and "fora das pastas" in m2["erro"], m2


def test_caractere_invisivel_vai_para_a_equipe(mundo):
    mundo["acao"].write_text("invisivel")
    r = _fazer(mundo)
    assert r["status"] == "precisa_equipe" and "U+202E" in r["erro"], r


def test_tela_que_nao_compila_nao_publica(mundo):
    m = mundo
    m["acao"].write_text("tela_quebrada")
    r = _fazer(m)
    assert r["status"] == "pronta", r
    r2 = _publicar(m, r)
    assert r2["status"] == "falhou" and "a tela compila" in r2["erro"] and "NÃO" in r2["erro"], r2
    assert _main_texto(m) == VELHO


def test_claude_pede_a_equipe(mundo):
    mundo["acao"].write_text("equipe")
    r = _fazer(mundo)
    assert r["status"] == "precisa_equipe" and r["erro"] == "Precisa de coluna nova"


def test_erro_de_rede_devolve_para_a_fila(mundo):
    m = mundo
    m["rodada"].REPO = m["repo"]
    _git(m["repo"], "remote", "set-url", "origin", "/caminho/que/nao/existe")
    m["fila"].append(_trabalho())
    m["rodada"].main()
    r = _resultados(m)[-1]
    assert r["status"] == "pedida" and "erro do ambiente" in r["erro"]


def test_resultado_que_nao_chegou_e_reenviado(mundo):
    m = mundo
    m["api_fora"]["vezes"] = 1  # a rede cai na hora de mandar o resultado
    m["fila"].append(_trabalho())
    m["rodada"].main()
    assert _resultados(m) == []
    pendentes = list((m["raiz"] / "estado" / "pendentes").glob("*.json"))
    assert len(pendentes) == 1
    m["rodada"].main()  # a próxima rodada reenvia antes de pegar trabalho
    assert _resultados(m)[-1]["status"] == "pronta"
    assert not list((m["raiz"] / "estado" / "pendentes").glob("*.json"))


def test_conferencias_rodam_na_caixa(mundo, tmp_path):
    """O testar.sh (o único jeito de rodar código da mudança) não lê a chave
    nem a pasta do robô, não escreve fora da pasta do pedido e não tem rede."""
    m = mundo
    _fazer(m)
    wt = m["rodada"].PASTA / "M-1"
    espiao = wt / "espiao.cjs"
    fora = tmp_path / "fora.txt"
    espiao.write_text(
        "const fs=require('fs');const r=[];\n"
        f"try{{fs.readFileSync('{os.path.expanduser('~/.ssh/coolify')}');r.push('LEU_CHAVE')}}catch(e){{}}\n"
        f"try{{fs.readdirSync('{m['raiz']}');r.push('LEU_RAIZ')}}catch(e){{}}\n"
        f"try{{fs.writeFileSync('{fora}','x');r.push('ESCREVEU_FORA')}}catch(e){{}}\n"
        "try{fs.writeFileSync('dentro.txt','x');r.push('ESCREVEU_DENTRO')}catch(e){}\n"
        "try{fs.writeFileSync('.git','gitdir: /tmp/x');r.push('ESCREVEU_PONTEIRO_GIT')}catch(e){}\n"
        "const cp=require('child_process');\n"
        "if(cp.spawnSync('/usr/bin/osascript',['-e','return 1']).status===0)r.push('RODOU_OSASCRIPT');\n"
        "try{require('vue');r.push('VUE_OK')}catch(e){r.push('SEM_VUE_'+e.code)}\n"
        "console.log(r.join(','))\n"
    )
    rc, out = m["rodada"].testar(wt, "node", "espiao.cjs", timeout=60)
    assert "ESCREVEU_DENTRO" in out and "VUE_OK" in out, out
    for proibido in ("LEU_CHAVE", "LEU_RAIZ", "ESCREVEU_FORA", "ESCREVEU_PONTEIRO_GIT",
                     "RODOU_OSASCRIPT"):
        assert proibido not in out, out
    assert not fora.exists()
    # fora da pasta de um pedido o testar.sh nem roda
    rc, out = m["rodada"].testar(tmp_path, "node", "x.cjs", timeout=60)
    assert rc == 2 and "só roda dentro" in out


def test_permissoes_bloqueiam_o_perigoso():
    regras = json.loads((AQUI / "permissoes.json").read_text())["permissions"]
    for item in ("WebFetch", "Read(~/.ssh/**)", "Read(//**/.env)", "Edit(apps/api/alembic/**)",
                 "Edit(**/conftest.py)", "Write(**/.gitattributes)"):
        assert item in regras["deny"], item
    # o Claude do robô não roda NADA: só arquivos da pasta do pedido (Glob/Grep
    # sem caminho valeriam para QUALQUER pasta do Mac)
    assert regras["allow"] == ["Read(./**)", "Edit(./**)", "Write(./**)"]
    assert regras["blockReadsOutsideWorkingDirectories"] is True
    assert "Bash" in regras["deny"] and "Write(**/hatch.toml)" in regras["deny"]
    assert json.loads((AQUI / "permissoes.json").read_text())["autoMemoryEnabled"] is False
    assert os.access(AQUI / "instalar.sh", os.X_OK) and os.access(AQUI / "testar.sh", os.X_OK)


def _pergunta(m) -> dict:
    m["fila"].append({
        "id": "id-p1", "codigo": "P-1", "acao": "responder", "tipo": "pergunta",
        "titulo": "Como a Margem segura um pedido?",
        "descricao": "Como a Margem decide segurar um pedido?", "onde": "Margem",
        "pedido_por_nome": "harry potter", "tentativa": 1, "adiamentos": 0,
    })
    m["rodada"].main()
    return _resultados(m)[-1]


def test_pergunta_le_o_codigo_e_responde_sem_mudar_nada(mundo):
    m = mundo
    m["acao"].write_text("pergunta")
    antes_main = _git(m["origem"], "rev-parse", "main")
    antes_ramos = _git(m["repo"], "branch", "--list")
    r = _pergunta(m)
    assert r["status"] == "respondida", r
    assert r["resposta"].startswith("A Margem segura o pedido")
    # segredo com cara conhecida não sai, mesmo dentro do trecho de código
    assert "abcdef123456789" not in r["resposta"] and "sk-ant-AAAA" not in r["resposta"]
    assert r["resposta"].count("[oculto]") == 2
    # nada foi commitado, empurrado nem criado; a cópia e a configuração sumiram
    assert _git(m["origem"], "rev-parse", "main") == antes_main
    assert _git(m["repo"], "branch", "--list") == antes_ramos
    assert not (m["rodada"].PASTA / "P-1").exists()
    assert not list((m["rodada"].PASTA / ".claude-robo").glob("P-1-*"))
    assert "P-1" not in _git(m["repo"], "worktree", "list")
    assert not any("setsid -f" in c for c in m["servidor"]["comandos"])


def test_pergunta_sem_resposta_falha(mundo):
    mundo["acao"].write_text("pergunta_vazia")
    r = _pergunta(mundo)
    assert r["status"] == "falhou" and "não conseguiu responder" in r["erro"]


def test_ocultar_segredos(mundo):
    o = mundo["rodada"].ocultar_segredos
    assert o('API_KEY = "abc123456"') == 'API_KEY = "[oculto]"'
    assert o("senha: 'minhasenha1'") == "senha: '[oculto]'"
    assert o("postgresql://user:segredo@host/db") == "postgresql://[oculto]@host/db"
    assert o("ghp_" + "a" * 30) == "[oculto]"
    assert "eyJ" not in o("Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdefghijklmnop")
    # texto comum não muda
    comum = "A margem é calculada como (preço − custo) ÷ preço; token de 3 letras não conta."
    assert o(comum) == comum


def test_pergunta_com_claude_indisponivel_volta_para_a_fila(mundo):
    """Limite de uso / sobrecarga do Claude não é falha da pergunta: volta para
    a fila (o DaVinci espera 10 min e não gasta tentativa)."""
    for acao in ("pergunta_limite", "pergunta_sobrecarga"):
        mundo["acao"].write_text(acao)
        r = _pergunta(mundo)
        assert r["status"] == "pedida" and "não está disponível agora" in r["erro"], (acao, r)
        assert not (mundo["rodada"].PASTA / "P-1").exists()


def test_faxina_limpa_config_velha_das_perguntas(mundo):
    import os as _os
    import time as _time

    r = mundo["rodada"]
    velha = r.PASTA / ".claude-robo" / "P-9-1"
    velha.mkdir(parents=True)
    antigo = _time.time() - 30 * 86400
    _os.utime(velha, (antigo, antigo))
    r.faxina()
    assert not velha.exists()
