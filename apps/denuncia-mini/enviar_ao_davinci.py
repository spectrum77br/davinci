#!/usr/bin/env python3
"""Manda para o DaVinci a cópia de Anúncios, Denúncias e Casos do sistema de
Fiscalização que roda no Mac mini da Makisa.

Roda a cada 5 min pelo LaunchAgent `com.davinci.denuncia-envio`. Só LÊ o banco
do sistema (modo só-leitura do SQLite) e as pastas das provas; nunca escreve
nelas. O DaVinci não alcança o mini (roteador do escritório), por isso é o
mini que manda.

A cada rodada:
1. Para cada tabela, lê todas as linhas, compara com o que já foi mandado
   (`~/.davinci_denuncia_estado.sqlite`, um hash por linha) e manda só o que
   é novo ou mudou, de 500 em 500, mais os ids que sumiram.
2. Pergunta ao DaVinci quais provas ainda estão sem arquivo e sobe os
   arquivos (no máximo `MAX_MB_RODADA` por rodada — a primeira carga, ~4 GB,
   vai aos poucos).

Configuração em `~/.davinci_denuncia.json` (permissão 600):
    {"url": "https://app.hadken.com", "token": "dnc_…",
     "banco": "/caminho/fiscalizacao.db", "provas": "/caminho/provas"}
`banco` aceita `*` (pega o mais novo) — enquanto o sistema não roda no mini,
aponta pro backup diário: `~/Fiscalizacao-Backup/banco/fiscalizacao_*.sqlite`.

Só biblioteca padrão (roda no /usr/bin/python3 do macOS, 3.9).

Uso: python3 enviar_ao_davinci.py [--so-dados] [--max-mb N]
"""

import fcntl
import glob
import hashlib
import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.request

# As variáveis DENUNCIA_* só existem para testar fora do mini.
CONFIG = os.environ.get("DENUNCIA_CONFIG") or os.path.expanduser("~/.davinci_denuncia.json")
ESTADO = os.environ.get("DENUNCIA_ESTADO") or os.path.expanduser("~/.davinci_denuncia_estado.sqlite")
TRAVA = ESTADO + ".lock"
LOG = os.environ.get("DENUNCIA_LOG") or os.path.expanduser("~/Library/Logs/davinci_denuncia.log")

# tabela do sistema → coluna-chave. A ordem importa pouco (o DaVinci não tem
# chave estrangeira entre elas), mas anúncios primeiro deixa a tela coerente.
TABELAS = [
    ("anuncios", "id"),
    ("lojas", "shop_id"),
    ("denuncias", "id"),
    ("casos", "id"),
    ("compras", "id"),
    ("provas", "id"),
    ("verificacoes", "id"),
]
LOTE = 500
MAX_MB_RODADA = 400
# Trava: se mais de 20% das linhas já mandadas de uma tabela "sumirem" numa
# rodada, é banco errado/truncado — não apaga nada no DaVinci e avisa no log.
MAX_SUMIDAS = 0.2


def log(msg):
    linha = time.strftime("%Y-%m-%d %H:%M:%S ") + msg
    print(linha, flush=True)
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        if os.path.exists(LOG) and os.path.getsize(LOG) > 5 * 1024 * 1024:
            os.replace(LOG, LOG + ".1")
        with open(LOG, "a") as f:
            f.write(linha + "\n")
    except OSError:
        pass


def ler_config():
    with open(CONFIG) as f:
        c = json.load(f)
    for k in ("url", "token", "banco", "provas"):
        if not c.get(k):
            raise SystemExit("falta '%s' em %s" % (k, CONFIG))
    c["url"] = c["url"].rstrip("/")
    c["banco"] = os.path.expanduser(c["banco"])
    c["provas"] = os.path.expanduser(c["provas"])
    if "*" in c["banco"]:
        # antes do sistema rodar no mini: o backup do dia (fiscalizacao_AAAA-MM-DD.sqlite)
        # o backup das 03:00 ainda sendo gravado não conta (mudou há < 5 min)
        achados = sorted(f for f in glob.glob(c["banco"]) if time.time() - os.path.getmtime(f) > 300)
        if not achados:
            raise SystemExit("nenhum banco em %s" % c["banco"])
        c["banco"] = achados[-1]
    return c


def pedir(cfg, metodo, rota, corpo=None, dados=None, timeout=120):
    url = cfg["url"] + rota
    h = {"Authorization": "Bearer " + cfg["token"], "User-Agent": "denuncia-mini/1"}
    if corpo is not None:
        dados = json.dumps(corpo, ensure_ascii=False).encode()
        h["Content-Type"] = "application/json"
    elif dados is not None:
        h["Content-Type"] = "application/octet-stream"
    req = urllib.request.Request(url, data=dados, method=metodo, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        corpo_erro = e.read().decode(errors="replace")[:300]
        raise RuntimeError("%s %s → %s %s" % (metodo, rota, e.code, corpo_erro)) from None


def abrir_banco(caminho):
    """Só leitura. Banco em modo WAL sem o `-shm` ao lado (o backup do dia, que
    ninguém abriu ainda) não abre em `mode=ro` — aí vai `immutable=1`, que lê o
    arquivo como está (backup/sistema parado não tem nada pendente no WAL)."""
    try:
        c = sqlite3.connect("file:%s?mode=ro" % caminho, uri=True, timeout=30)
        c.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchall()
        return c
    except sqlite3.OperationalError:
        return sqlite3.connect("file:%s?mode=ro&immutable=1" % caminho, uri=True)


def abrir_estado():
    e = sqlite3.connect(ESTADO)
    e.execute("CREATE TABLE IF NOT EXISTS enviado(tabela TEXT, chave TEXT, hash TEXT, PRIMARY KEY(tabela, chave))")
    return e


def hash_linha(d):
    return hashlib.sha1(json.dumps(d, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def enviar_tabela(cfg, banco, estado, tabela, chave):
    colunas = [r[1] for r in banco.execute('PRAGMA table_info("%s")' % tabela)]
    sql = 'SELECT * FROM "%s"' % tabela
    if "teste" in colunas:  # linhas do modo teste da API nunca saem do mini
        sql += " WHERE COALESCE(teste, 0) = 0"
    atuais = {}
    for r in banco.execute(sql):
        d = dict(zip(colunas, r))
        if d.get(chave) is None:
            continue
        atuais[str(d[chave])] = d
    ja = dict(estado.execute("SELECT chave, hash FROM enviado WHERE tabela=?", (tabela,)).fetchall())
    mudadas = []
    for k, d in atuais.items():
        h = hash_linha(d)
        if ja.get(k) != h:
            mudadas.append((k, h, d))
    sumidas = [k for k in ja if k not in atuais]
    if len(ja) >= 50 and len(sumidas) > MAX_SUMIDAS * len(ja):
        raise RuntimeError(
            "%s: %d de %d linhas sumiram de uma vez — não apaguei nada (banco certo?)"
            % (tabela, len(sumidas), len(ja))
        )
    for i in range(0, len(mudadas), LOTE):
        parte = mudadas[i:i + LOTE]
        pedir(cfg, "POST", "/api/denuncia/sync/" + tabela, {"linhas": [d for _, _, d in parte]})
        estado.executemany(
            "INSERT OR REPLACE INTO enviado(tabela, chave, hash) VALUES(?,?,?)",
            [(tabela, k, h) for k, h, _ in parte],
        )
        estado.commit()
    if sumidas:
        pedir(cfg, "POST", "/api/denuncia/sync/" + tabela, {"removidos": sumidas})
        estado.executemany("DELETE FROM enviado WHERE tabela=? AND chave=?", [(tabela, k) for k in sumidas])
        estado.commit()
    return len(mudadas), len(sumidas)


def enviar_arquivos(cfg, banco, max_mb):
    faltam = pedir(cfg, "GET", "/api/denuncia/sync/provas-sem-arquivo").get("ids") or []
    if not faltam:
        return 0, 0, 0
    caminhos = dict(banco.execute("SELECT id, arquivo FROM provas"))
    subidos, bytes_, sem_arquivo = 0, 0, 0
    for pid in faltam:
        rel = caminhos.get(pid)
        caminho = os.path.join(cfg["provas"], rel) if rel else None
        if not caminho or not os.path.isfile(caminho):
            sem_arquivo += 1
            continue
        tam = os.path.getsize(caminho)
        if subidos and bytes_ + tam > max_mb * 1024 * 1024:
            break
        with open(caminho, "rb") as f:
            conteudo = f.read()
        try:
            pedir(cfg, "PUT", "/api/denuncia/sync/provas/%d/arquivo" % pid, dados=conteudo, timeout=600)
        except RuntimeError as e:
            log("prova %s não subiu: %s" % (pid, e))
            continue
        subidos += 1
        bytes_ += tam
    return subidos, bytes_, sem_arquivo


def main():
    so_dados = "--so-dados" in sys.argv
    max_mb = MAX_MB_RODADA
    if "--max-mb" in sys.argv:
        max_mb = int(sys.argv[sys.argv.index("--max-mb") + 1])
    trava = open(TRAVA, "w")
    try:
        fcntl.flock(trava, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log("rodada anterior ainda em andamento — pulei")
        return 0
    cfg = ler_config()
    banco = abrir_banco(cfg["banco"])
    estado = abrir_estado()
    inicio = time.time()
    partes = []
    try:
        for tabela, chave in TABELAS:
            n, s = enviar_tabela(cfg, banco, estado, tabela, chave)
            if n or s:
                partes.append("%s +%d -%d" % (tabela, n, s))
        if not so_dados:
            subidos, b, sem = enviar_arquivos(cfg, banco, max_mb)
            if subidos or sem:
                partes.append("provas: %d arquivos (%.1f MB)%s" % (
                    subidos, b / 1048576.0, ", %d sem arquivo no mini" % sem if sem else ""))
        # sinal de vida: o topo das telas mostra "cópia de há N min" mesmo
        # quando nada mudou (noite, fim de semana)
        pedir(cfg, "POST", "/api/denuncia/sync/pulso", {})
    except Exception as e:  # noqa: BLE001 — a próxima rodada tenta de novo
        log("ERRO: %s" % e)
        return 1
    finally:
        banco.close()
        estado.close()
    log("ok em %.0fs%s" % (time.time() - inicio, (": " + "; ".join(partes)) if partes else " (nada novo)"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
