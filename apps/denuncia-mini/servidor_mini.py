#!/usr/bin/env python3
"""Sistema de Fiscalização rodando no Mac mini da Makisa (30/09/2026).

O sistema (Flask + SQLite) saiu do servidor da Hostinger e roda aqui, dentro de
`~/Desktop/Denuncias/Ecomerce/fiscalizacao-sistema` — Vinicius: "quero que
fique tudo dentro daquela pasta Denuncias que tá na Mesa".

Por que numa janela do Terminal (atalho "00 - Sistema no Mac mini"): o macOS
barra tarefas de fundo (LaunchAgent) de ler a Mesa ("Operation not
permitted", testado em 30/09). O robô já rodava assim; o sistema segue o mesmo
caminho, e tudo que ele dispara herda a permissão do Terminal.

Uma janela faz quatro coisas:
1. o sistema em http://127.0.0.1:8710 (o robô e o Cowork falam com ele);
2. a cópia pro DaVinci a cada 5 min (`enviar_ao_davinci.py --so-dados`);
3. as provas e o backup do banco pro MEGA a cada 2 min (`app/mega_sync.py`),
   quando o MEGAcmd estiver instalado e logado na conta da empresa;
4. o `status_mac.py` do robô (painel Operação/Status), que antes era um
   LaunchAgent e parou de poder ler a pasta;
5. (01/10) o estado do robô pro DaVinci a cada 60 s: o resumo que o
   `status_mac.py` grava em `Fiscalizacao/_cowork/status_ultimo.json` vai pra
   `POST /api/denuncia/sync/robo` — é a aba Robô de Ouvidoria › Denúncia;
6. (01/10) as provas sob demanda: a cada 5 s pergunta ao DaVinci se alguém
   clicou numa prova que não está lá e sobe só aquele arquivo;
7. (01/10) os botões da aba Robô: a cada 5 s busca os comandos (ligar/desligar
   a rotina automática, rodar um passo agora, "tratado" numa ocorrência),
   executa e responde.
8. (01/10) o "Anexar prova" da ficha do caso no DaVinci: a cada 30 s busca os
   anexos, baixa o arquivo (ou pega o link do vídeo no MEGA) e entrega ao
   sistema daqui (POST /api/v1/provas, que guarda e sobe pro MEGA).
9. (13/10) os botões "Montar pasta jurídico" e "Enviar ao jurídico" da ficha do caso:
   comandos juridico_montar / juridico_enviar → `Fiscalizacao/pasta_juridico.py`
   numa linha à parte (demora: leitura do print + PDF; anexos no Tuta); o andamento
   vai pras rotas /api/denuncia/sync/juridico/pastas/<id>.
Mais o backup diário local do banco em `data/backups` (guarda 30), como fazia
o `run.py` no servidor.

Rodar: `.venv/bin/python` do sistema (tem Flask/Waitress):
    ~/Desktop/Denuncias/Ecomerce/fiscalizacao-sistema/.venv/bin/python servidor_mini.py
"""

import datetime
import glob
import json
import os
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.request

ECOMERCE = os.path.expanduser("~/Desktop/Denuncias/Ecomerce")
SISTEMA = os.path.join(ECOMERCE, "fiscalizacao-sistema")
APP = os.path.join(SISTEMA, "app")
DADOS = os.path.join(SISTEMA, "data")
ROBO = os.path.join(ECOMERCE, "Fiscalizacao")
DAVINCI = os.path.join(ECOMERCE, "DaVinci")
MEGACMD = "/Applications/MEGAcmd.app/Contents/MacOS"
PORTA = int(os.environ.get("FISC_PORT", "8710"))
LOG = os.path.join(DADOS, "servidor_mini.log")


def log(msg):
    linha = "%s %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(linha, flush=True)
    try:
        if os.path.exists(LOG) and os.path.getsize(LOG) > 5 * 1024 * 1024:
            os.replace(LOG, LOG + ".1")
        with open(LOG, "a") as f:
            f.write(linha + "\n")
    except OSError:
        pass


def porta_ocupada(porta):
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", porta)) == 0


def a_cada(segundos, nome, fn):
    """Roda `fn` pra sempre, com intervalo; erro não derruba o sistema."""
    def laco():
        time.sleep(20)  # deixa o sistema subir antes
        while True:
            try:
                fn()
            except Exception as e:  # noqa: BLE001
                log("%s: erro %s" % (nome, e))
            time.sleep(segundos)
    threading.Thread(target=laco, name=nome, daemon=True).start()


def rodar(nome, args, cwd, env=None, timeout=1800):
    p = subprocess.run(args, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)
    saida = (p.stdout + p.stderr).strip().splitlines()
    ultima = saida[-1] if saida else ""
    if p.returncode != 0:
        log("%s: saiu com %s — %s" % (nome, p.returncode, ultima[:300]))
    return p.returncode, ultima


def davinci():
    script = os.path.join(DAVINCI, "enviar_ao_davinci.py")
    if os.path.exists(script):
        rodar("davinci", ["/usr/bin/python3", script, "--so-dados"], cwd=DAVINCI)


def mega():
    if not os.path.exists(os.path.join(MEGACMD, "mega-whoami")):
        return  # MEGAcmd ainda não instalado: as provas ficam só aqui por enquanto
    env = dict(os.environ, PATH=MEGACMD + ":" + os.environ.get("PATH", ""))
    rodar("mega", [sys.executable, "mega_sync.py"], cwd=APP, env=env)


def backup_diario():
    pasta = os.path.join(DADOS, "backups")
    os.makedirs(pasta, exist_ok=True)
    nome = os.path.join(pasta, "fiscalizacao_%s.sqlite" % datetime.date.today().isoformat())
    if os.path.exists(nome):
        return
    src = sqlite3.connect(os.path.join(DADOS, "fiscalizacao.db"))
    dst = sqlite3.connect(nome)
    src.backup(dst)
    dst.close()
    src.close()
    for velho in sorted(glob.glob(os.path.join(pasta, "fiscalizacao_*.sqlite")))[:-30]:
        os.remove(velho)
    log("backup local: %s" % os.path.basename(nome))


_robo = {"enviado": None}


def robo():
    """Manda pro DaVinci o último resumo do status_mac.py, se mudou."""
    caminho = os.path.join(ROBO, "_cowork", "status_ultimo.json")
    if not os.path.exists(caminho):
        return
    marca = os.path.getmtime(caminho)
    desp = os.path.join(ROBO, "despertador.json")
    if os.path.exists(desp):
        marca = max(marca, os.path.getmtime(desp))
    if marca == _robo["enviado"]:
        return  # status_mac.py parado: o DaVinci acusa "sem notícia" sozinho
    with open(caminho, encoding="utf-8") as f:
        resumo = json.load(f)
    with open(os.path.expanduser("~/.davinci_denuncia.json")) as f:
        cfg = json.load(f)
    corpo = {"quando": resumo.get("quando"), "itens": resumo.get("itens") or []}
    try:  # 01/10: despertador desligado = modo manual (a aba Robô não acusa rodada que não começou)
        with open(os.path.join(ROBO, "despertador.json"), encoding="utf-8") as f:
            d = json.load(f)
        # 02/10: + a agenda que o despertador está seguindo (a aba Robô mostra "esperando o robô aplicar")
        corpo["despertador"] = {"ligado": d.get("ligado", True), "rodadas": d.get("rodadas"),
                                "agenda": d.get("agenda"),
                                # 16/10: + a janela da noite que o robô segue (a aba Robô mostra se já aplicou)
                                "janela_noite": d.get("janela_noite")}
    except (OSError, ValueError):
        pass
    req = urllib.request.Request(
        cfg["url"].rstrip("/") + "/api/denuncia/sync/robo",
        data=json.dumps(corpo, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={"Authorization": "Bearer " + cfg["token"], "Content-Type": "application/json",
                 "User-Agent": "denuncia-mini/1"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        r.read()
    _robo["enviado"] = marca


_provas = {"falhou": {}, "aviso_rede": 0.0}


def _env():
    """O mesmo config/banco/rotas da cópia (enviar_ao_davinci.py, só biblioteca padrão)."""
    if DAVINCI not in sys.path:
        sys.path.insert(0, DAVINCI)
    import enviar_ao_davinci as env  # noqa: E402

    return env


def _perguntar(env, cfg, rota):
    try:
        return env.pedir(cfg, "GET", rota, timeout=30)
    except (OSError, RuntimeError) as e:
        if time.time() - _provas["aviso_rede"] > 600:  # sem internet: avisa de 10 em 10 min, não a cada 5 s
            log("DaVinci não respondeu (%s)" % e)
            _provas["aviso_rede"] = time.time()
        return None


def provas_pedidas():
    """Prova que alguém abriu no DaVinci e o arquivo não está lá: sobe só ela."""
    env = _env()
    cfg = env.ler_config()
    r = _perguntar(env, cfg, "/api/denuncia/sync/provas-pedidas")
    if r is None:
        return
    ids = r.get("ids") or []
    ids = [i for i in ids if time.time() - _provas["falhou"].get(i, 0) > 300]
    if not ids:
        return
    banco = env.abrir_banco(cfg["banco"])
    try:
        caminhos = dict(banco.execute(
            "SELECT id, arquivo FROM provas WHERE id IN (%s)" % ",".join("?" * len(ids)), ids))
    finally:
        banco.close()
    for pid in ids:
        rel = caminhos.get(pid)
        caminho = os.path.join(cfg["provas"], rel) if rel else None
        if not caminho or not os.path.isfile(caminho):
            log("provas: %s pedida no DaVinci, mas o arquivo não está neste Mac (%s)" % (pid, rel))
            _provas["falhou"][pid] = time.time()
            continue
        with open(caminho, "rb") as f:
            dados = f.read()
        try:
            env.pedir(cfg, "PUT", "/api/denuncia/sync/provas/%d/arquivo" % pid, dados=dados, timeout=600)
        except (OSError, RuntimeError) as e:
            log("provas: %s não subiu — %s" % (pid, e))
            _provas["falhou"][pid] = time.time()
            continue
        log("provas: %s enviada ao DaVinci (%d KB)" % (pid, len(dados) // 1024))


# ordem de cada passo na fila do agente (ORDEM_ACAO do agente_varredura.py)
# 02/10 (agente v27): 2 procura e 3 denúncias no lugar de varredura_<canal> (2–5) e conferência (6)
ORDEM_PASSO = {"checagem": 0, "ciclo_emails": 1, "procura": 2, "denuncias": 3,
               "varredura_mercadolivre": 2, "varredura_shopee": 2, "varredura_tiktok": 2,
               "varredura_amazon": 2, "conferencia": 3, "anatel": 4,
               "compras": 5, "juridico": 6, "relatorio": 7, "diversos": 7, "capa_perguntas": 8,
               "ativos_inativos": 20,   # 01/10: o "saiu do ar?" virou passo próprio (agente v23)
               # 06/10: o "Rodar" da aba Robô respondia "passo desconhecido" pros passos 6 e 8 (ações extras do
               # agente, ordem do acoes_extras.json)
               "replica_diversos": 6, "aproveitar_parado": 15,
               "prints": 7}   # 07/10: passo 5 próprio (agente v36)


def _gravar(caminho, texto):
    tmp = caminho + ".tmp"   # o agente lê _gatilhos/*.json: nunca vê arquivo pela metade
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(texto)
    os.replace(tmp, caminho)


def _automatico(ligado, por):
    """Liga/desliga a rotina automática: o despertador do agente (06/12/18h) e o
    ciclo de e-mails de 3 em 3 h (arquivo _varredura_ciclo_off)."""
    p = os.path.join(ROBO, "despertador.json")
    with open(p, encoding="utf-8") as f:
        d = json.load(f)
    d["ligado"] = ligado
    d["_mudado_por"] = "DaVinci (%s) em %s" % (por, time.strftime("%d/%m %H:%M"))
    _gravar(p, json.dumps(d, ensure_ascii=False, indent=1) + "\n")
    off = os.path.join(ROBO, "_varredura_ciclo_off")
    if ligado:
        if os.path.exists(off):
            os.remove(off)
    else:
        _gravar(off, "Modo manual pedido pelo DaVinci (%s) em %s. Apague para voltar ao automático.\n"
                % (por, time.strftime("%d/%m %H:%M")))
    return True, "rotina automática %s" % ("ligada" if ligado else "desligada")


def agenda(por=None):
    """02/10 (Vinicius): a agenda dos passos (liga/desliga e horários) mora no DaVinci; a cada minuto
    (e na hora, pelo comando "agenda") vem pra cá e vai pro despertador.json — o agente relê a cada
    30 s. Só grava se mudou. Devolve (ok, texto)."""
    env = _env()
    cfg = env.ler_config()
    r = _perguntar(env, cfg, "/api/denuncia/sync/robo/agenda")
    if r is None or not isinstance(r.get("agenda"), dict):
        return False, "não consegui ler a agenda do DaVinci"
    nova = {a: {"ligado": bool(v.get("ligado")), "horarios": sorted(v.get("horarios") or [])}
            for a, v in r["agenda"].items() if isinstance(v, dict)}
    # 16/10 (Vinicius: "regra horario inicio e fim"): a janela da noite vem junto; o agente (v37) relê a cada volta.
    # DaVinci sem a janela (versão antiga) = mantém a que está no despertador.
    jn = r.get("janela_noite")
    p = os.path.join(ROBO, "despertador.json")
    with open(p, encoding="utf-8") as f:
        d = json.load(f)
    janela = ({"ligado": bool(jn.get("ligado")), "inicio": str(jn.get("inicio") or ""), "fim": str(jn.get("fim") or "")}
              if isinstance(jn, dict) else d.get("janela_noite"))
    ligados = ", ".join("%s %s" % (a, "/".join(v["horarios"]) or "sem horário")
                        for a, v in sorted(nova.items()) if v["ligado"]) or "nenhum passo ligado"
    if janela:
        ligados += " · janela da noite %s às %s%s" % (janela["inicio"], janela["fim"], "" if janela["ligado"] else " (desligada)")
    if d.get("agenda") == nova and d.get("janela_noite") == janela:
        return True, "agenda já estava aplicada (%s)" % ligados
    d["agenda"] = nova
    if janela:
        d["janela_noite"] = janela
    d["_agenda"] = ("02/10/2026: agenda dos passos vinda do DaVinci (aba Robô) — o despertador segue isto "
                    "em vez das rodadas fixas; mudar lá, não aqui. Atualizada em %s%s."
                    % (time.strftime("%d/%m %H:%M"), " por %s" % por if por else ""))
    _gravar(p, json.dumps(d, ensure_ascii=False, indent=1) + "\n")
    log("agenda do DaVinci aplicada no despertador: %s" % ligados)
    return True, "agenda aplicada (%s)" % ligados


def _passo(acao, por):
    """O mesmo gatilho que o pedir_tarefa.py <acao> --retomar grava."""
    if acao not in ORDEM_PASSO:
        return False, "passo desconhecido: %s" % acao
    gat = os.path.join(ROBO, "_gatilhos")
    for f in sorted(os.listdir(gat)):
        if not (f.startswith(acao + "_") and f.endswith(".json")) or f.endswith((".feito.json", ".rodando.json", ".erro.json")):
            continue
        base = os.path.join(gat, f[:-5])
        if os.path.exists(base + ".feito.json"):
            continue
        if os.path.exists(base + ".rodando.json"):
            return True, "já está rodando (%s)" % f
        if time.time() - os.path.getmtime(os.path.join(gat, f)) < 5 * 3600:
            return True, "já estava na fila (%s)" % f
    agora = datetime.datetime.now()
    jan = "%s_%02dh" % (agora.strftime("%Y-%m-%d"), max(h for h in (0, 6, 12, 18) if h <= agora.hour))
    nome = "%s_%s_r%s.json" % (acao, jan, agora.strftime("%H%M%S"))
    _gravar(os.path.join(gat, nome), json.dumps({
        "acao": acao, "ordem": ORDEM_PASSO[acao], "janela": jan, "forcar": True,
        "origem": "DaVinci (%s)" % por, "pedido_em": agora.isoformat(timespec="seconds")}, ensure_ascii=False))
    return True, "pedido ao robô (%s)" % nome


def _resolver(chave, por):
    """"Tratado" na aba Robô: grava a resolução no _canal/PROBLEMAS.jsonl do
    robô (o status_mac.py tira da lista quem tem {"chave": X, "resolvido": …})."""
    if not chave:
        return False, "sem chave"
    linha = json.dumps({"chave": chave, "resolvido": datetime.datetime.now().isoformat(timespec="seconds"),
                        "por": "DaVinci (%s)" % por}, ensure_ascii=False)
    with open(os.path.join(ROBO, "_canal", "PROBLEMAS.jsonl"), "a", encoding="utf-8") as f:
        f.write(linha + "\n")
    return True, "problema %s marcado como resolvido no robô" % chave


def _sistema(metodo, rota, corpo):
    """A API do sistema daqui (a mesma do robô: ~/.fiscalizacao.json)."""
    if ROBO not in sys.path:
        sys.path.insert(0, ROBO)
    from fiscalizacao_api import Fiscalizacao  # noqa: E402

    return Fiscalizacao()._req(metodo, rota, corpo, timeout=60)


def _criar_caso(dados, por):
    """Botão "criar" / "enviar para caso" da aba Anúncios e denúncias (01/10): um caso por loja."""
    corpo = {k: dados.get(k) for k in ("shop_id", "marketplace", "loja", "anuncio_ids")}
    corpo["por"] = "DaVinci (%s)" % por
    r = _sistema("POST", "/api/v1/casos/por-loja", corpo)
    if r.get("ja_existia"):
        return True, "a loja já tinha o %s — não abri outro" % r.get("caso")
    return True, "%s aberto (%s, %s anúncio%s)" % (
        r.get("caso"), dados.get("loja") or dados.get("shop_id"), r.get("anuncios"), "s" if (r.get("anuncios") or 0) > 1 else "")


def _excluir_caso(dados, por):
    """Lixeira da aba Casos (01/10): estorna o caso — nada é apagado no sistema daqui."""
    r = _sistema("POST", "/api/v1/casos/%d/excluir" % int(dados.get("caso_id")),
                 {"por": "DaVinci (%s)" % por, "motivo": dados.get("motivo") or ""})
    if r.get("ja_estava"):
        return True, "%s já estava excluído" % r.get("caso")
    return True, "%s excluído (estornado)" % r.get("caso")


def _trocar_principal(dados, por):
    """Lista de compra da aba Casos (13/10): o anúncio escolhido vira o principal do caso."""
    r = _sistema("POST", "/api/v1/casos/%d/principal" % int(dados.get("caso_id")),
                 {"anuncio_id": dados.get("anuncio_id"), "por": "DaVinci (%s)" % por})
    if r.get("ja_era"):
        return True, "%s já tinha o anúncio %s como principal" % (r.get("caso"), r.get("anuncio_id"))
    return True, "%s: anúncio principal %s → %s" % (r.get("caso"), r.get("de"), r.get("anuncio_id"))


# 13/10/2026 (Vinicius: "eu acionar esses botão dentro dos casos e enviar quando eu quiser"): o passo 9 (Jurídico) saiu da
# lista; a pasta do advogado é montada e enviada pelos botões da ficha do caso. O comando é respondido na hora ("recebido") e o
# trabalho roda numa linha à parte — o laço dos comandos (5 s) não fica parado esperando o PDF ou o Tuta.
PY_ROBO = "/Library/Frameworks/Python.framework/Versions/3.12/bin/python3"   # tem Pillow, Quartz e Vision (leitura do print)
PASTAS_JURIDICO = os.path.join(ROBO, "Juridico", "DaVinci")
_juridico_estado = {"andamento": set()}   # (comando, pasta) rodando agora — não pega o mesmo duas vezes


def _juridico_rodar(args, timeout):
    p = subprocess.run([PY_ROBO, os.path.join(ROBO, "pasta_juridico.py")] + args, cwd=ROBO,
                       capture_output=True, text=True, timeout=timeout)
    linhas = [x for x in (p.stdout or "").strip().splitlines() if x.startswith("{")]
    if p.returncode != 0 or not linhas:
        erro = ((p.stderr or "") + (p.stdout or "")).strip().splitlines()
        raise RuntimeError((erro[-1] if erro else "saiu com %s" % p.returncode)[:400])
    return json.loads(linhas[-1])


def _juridico_montar(dados):
    env = _env()
    cfg = env.ler_config()
    pid = int(dados["pasta_id"])
    rota = "/api/denuncia/sync/juridico/pastas/%d" % pid
    try:
        env.pedir(cfg, "POST", rota, corpo={"status": "montando", "resultado": "montando a pasta no mini"}, timeout=30)
        r = _juridico_rodar(["montar", "--caso-id", str(int(dados["caso_id"])), "--pasta-id", str(pid),
                             "--selo", "sim" if dados.get("selo_anatel") == "sim" else "nao"], timeout=1800)
        with open(r["zip"], "rb") as f:
            env.pedir(cfg, "PUT", rota + "/arquivo", dados=f.read(), timeout=600)
        info = {k: r.get(k) for k in ("arquivos", "avisos", "nome_zip", "para", "cc", "de", "assunto", "tamanho",
                                       "captura", "codigo_exibido", "certificado", "certificado_makisa")}
        res = "pasta montada: %d arquivos, %.1f MB" % (len(r.get("arquivos") or []), (r.get("tamanho") or 0) / 1048576.0)
        env.pedir(cfg, "POST", rota, corpo={"status": "pronta", "resultado": res, "info": info}, timeout=30)
        log("jurídico: pasta %s do caso %s — %s" % (pid, dados.get("caso_id"), res))
    except Exception as e:  # noqa: BLE001
        log("jurídico: pasta %s do caso %s falhou — %s" % (pid, dados.get("caso_id"), e))
        try:
            env.pedir(cfg, "POST", rota, corpo={"status": "erro", "resultado": str(e)[:500]}, timeout=30)
        except (OSError, RuntimeError):
            pass
    finally:
        _juridico_estado["andamento"].discard(("juridico_montar", pid))


def _juridico_pasta(pid):
    """O resultado.json da montagem mais nova desta pasta (Juridico/DaVinci/<pasta>_<quando>/)."""
    achados = sorted(glob.glob(os.path.join(PASTAS_JURIDICO, "%d_*" % pid, "resultado.json")))
    if not achados:
        raise RuntimeError("a pasta %d não está neste Mac — monte de novo" % pid)
    with open(achados[-1], encoding="utf-8") as f:
        return json.load(f)


def _juridico_enviar(dados):
    env = _env()
    cfg = env.ler_config()
    pid = int(dados["pasta_id"])
    rota = "/api/denuncia/sync/juridico/pastas/%d/envio" % pid
    try:
        env.pedir(cfg, "POST", rota, corpo={"status": "enviando", "resultado": "enviando pelo Tuta do mini"}, timeout=30)
        res = _juridico_pasta(pid)
        corpo_arq = os.path.join(os.path.dirname(res["zip"]), "corpo_email.txt")
        with open(corpo_arq, "w", encoding="utf-8") as f:
            f.write(res.get("corpo") or "")
        cc = dados.get("cc") or ""
        cc = ", ".join(cc) if isinstance(cc, list) else str(cc)
        r = _juridico_rodar(["enviar", "--pasta-dir", res["pasta"], "--para", str(dados["para"]), "--cc", cc,
                             "--assunto", res.get("assunto") or "", "--corpo-arquivo", corpo_arq], timeout=4500)   # fila: 1 envio por vez
        env.pedir(cfg, "POST", rota, corpo={"status": "enviado", "resultado": r.get("resultado"),
                                            "enviado_em": r.get("enviado_em")}, timeout=30)
        log("jurídico: pasta %s enviada — %s" % (pid, r.get("resultado")))
        if not dados.get("teste"):
            # o sistema daqui também registra: caso "Com jurídico", data, e-mail e anexos (dossiê como prova)
            try:
                _sistema("POST", "/api/v1/juridico/%s/enviado" % res["anuncio_id"], {
                    "advogado": (res.get("para") or {}).get("nome"), "data": (r.get("enviado_em") or "")[:16],
                    "assunto": res.get("assunto"), "corpo": res.get("corpo"), "para": dados["para"], "cc": cc,
                    "anexos": [{"nome": a["nome"], "bytes": a["bytes"]} for a in res.get("arquivos") or []]})
            except Exception as e:  # noqa: BLE001 — o e-mail já saiu; o DaVinci já marcou pela data
                log("jurídico: e-mail saiu, mas o registro no sistema daqui falhou — %s" % e)
            davinci()
    except Exception as e:  # noqa: BLE001
        log("jurídico: envio da pasta %s falhou — %s" % (pid, e))
        try:
            env.pedir(cfg, "POST", rota, corpo={"status": "erro", "resultado": str(e)[:500]}, timeout=30)
        except (OSError, RuntimeError):
            pass
    finally:
        _juridico_estado["andamento"].discard(("juridico_enviar", pid))


def _juridico(tipo, dados):
    chave = (tipo, int(dados.get("pasta_id") or 0))
    if chave in _juridico_estado["andamento"]:
        return True, "já está em andamento"
    _juridico_estado["andamento"].add(chave)
    alvo = _juridico_montar if tipo == "juridico_montar" else _juridico_enviar
    threading.Thread(target=alvo, args=(dados,), name=tipo, daemon=True).start()
    return True, "recebido — %s" % ("montando a pasta" if tipo == "juridico_montar" else "enviando pelo Tuta")


def comandos():
    """Botões da aba Robô do DaVinci (e o criar/excluir caso das abas Anúncios e Casos)."""
    env = _env()
    cfg = env.ler_config()
    r = _perguntar(env, cfg, "/api/denuncia/sync/robo/comandos")
    for c in (r or {}).get("comandos") or []:
        dados, por = c.get("dados") or {}, c.get("pedido_por") or "?"
        try:
            if c.get("tipo") == "automatico":
                ok, res = _automatico(bool(dados.get("ligado")), por)
            elif c.get("tipo") == "passo":
                ok, res = _passo(dados.get("acao"), por)
            elif c.get("tipo") == "agenda":
                ok, res = agenda(por)
            elif c.get("tipo") == "resolver":
                ok, res = _resolver(dados.get("chave"), por)
            elif c.get("tipo") == "criar_caso":
                ok, res = _criar_caso(dados, por)
            elif c.get("tipo") == "excluir_caso":
                ok, res = _excluir_caso(dados, por)
            elif c.get("tipo") == "trocar_principal":
                ok, res = _trocar_principal(dados, por)
            elif c.get("tipo") in ("juridico_montar", "juridico_enviar"):
                ok, res = _juridico(c["tipo"], dados)
            else:
                ok, res = False, "comando desconhecido: %s" % c.get("tipo")
        except Exception as e:  # noqa: BLE001
            ok, res = False, "erro: %s" % e
        log("comando %s do DaVinci (%s, %s): %s" % (c.get("id"), por, dados, res))
        env.pedir(cfg, "POST", "/api/denuncia/sync/robo/comandos/%d" % c["id"],
                  corpo={"ok": ok, "resultado": res}, timeout=30)
        if c.get("tipo") in ("automatico", "agenda"):
            robo()   # a aba Robô já mostra ligada/desligada (e a agenda aplicada), sem esperar o minuto
        elif c.get("tipo") in ("criar_caso", "excluir_caso", "trocar_principal") and ok:
            davinci()   # o caso novo (ou a exclusão) aparece no DaVinci já, sem esperar os 5 min da cópia


_anexos = {"falhou": {}}


def _baixar(cfg, rota, destino):
    req = urllib.request.Request(cfg["url"] + rota, headers={
        "Authorization": "Bearer " + cfg["token"], "User-Agent": "denuncia-mini/1"})
    with urllib.request.urlopen(req, timeout=300) as r, open(destino, "wb") as f:
        while True:
            bloco = r.read(1024 * 1024)
            if not bloco:
                break
            f.write(bloco)


def _entregar_anexo(cfg, x, pasta):
    """Entrega um anexo ao sistema daqui. Devolve o texto do resultado; erro sobe (tenta de novo)."""
    if ROBO not in sys.path:
        sys.path.insert(0, ROBO)
    from fiscalizacao_api import Fiscalizacao  # noqa: E402  (só biblioteca padrão, ~/.fiscalizacao.json)

    partes = ["Anexado pelo DaVinci (%s) — %s" % (x.get("enviado_por") or "?", x.get("tipo_nome") or x.get("tipo"))]
    if x.get("tipo") == "devolucao":
        partes.append("Devolução pedida")   # o checklist do advogado procura "devolu"
    if x.get("obs"):
        partes.append(x["obs"])
    if x.get("link"):
        partes.append(x["link"])
    obs = " · ".join(partes)
    if x.get("tem_arquivo"):
        nome = os.path.basename(x.get("nome") or "") or "anexo_%d.bin" % x["id"]
        caminho = os.path.join(pasta, "%d_%s" % (x["id"], nome))
        _baixar(cfg, "/api/denuncia/sync/anexos/%d/arquivo" % x["id"], caminho)
    else:   # vídeo: só o link do MEGA (regra do DaVinci) — vira um .txt com o link
        caminho = os.path.join(pasta, "video_link_%d.txt" % x["id"])
        with open(caminho, "w", encoding="utf-8") as f:
            f.write("%s\n%s\n" % (x.get("link") or "", obs))
    r = Fiscalizacao().enviar_prova(x["anuncio_id"], caminho, tipo=x.get("tipo_prova") or "Outro", obs=obs)
    arqs = r.get("arquivos") or []
    dup = any(a.get("duplicado") for a in arqs)
    return "prova guardada no sistema do mini (%s)%s" % (x.get("tipo_prova"), " — já estava lá" if dup else "")


def anexos():
    """"Anexar prova" da ficha do caso no DaVinci."""
    env = _env()
    cfg = env.ler_config()
    r = _perguntar(env, cfg, "/api/denuncia/sync/anexos")
    pend = [x for x in (r or {}).get("anexos") or []
            if time.time() - _anexos["falhou"].get(x["id"], 0) > 600]
    if not pend:
        return
    import tempfile
    with tempfile.TemporaryDirectory(prefix="anexos_") as pasta:
        for x in pend:
            try:
                res, ok = _entregar_anexo(cfg, x, pasta), True
            except (OSError, RuntimeError, SystemExit) as e:   # rede, sistema fora do ar: tenta de novo em 10 min
                log("anexo %s (%s) não entregue — %s" % (x["id"], x.get("tipo"), e))
                _anexos["falhou"][x["id"]] = time.time()
                continue
            log("anexo %s do DaVinci (%s, caso %s): %s" % (x["id"], x.get("tipo"), x.get("caso_id"), res))
            env.pedir(cfg, "POST", "/api/denuncia/sync/anexos/%d" % x["id"],
                      corpo={"ok": ok, "resultado": res}, timeout=30)


def status_mac():
    """Mantém o status_mac.py do robô vivo (ele mesmo roda em laço)."""
    py = "/usr/local/bin/python3" if os.path.exists("/usr/local/bin/python3") else "/usr/bin/python3"
    while True:
        try:
            p = subprocess.Popen([py, "status_mac.py"], cwd=ROBO,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            p.wait()
            log("status_mac: parou (%s), religando em 30 s" % p.returncode)
        except Exception as e:  # noqa: BLE001
            log("status_mac: erro %s" % e)
        time.sleep(30)


def main():
    if porta_ocupada(PORTA):
        print("O sistema já está rodando neste Mac (porta %d). Pode fechar esta janela." % PORTA)
        return 0
    if not os.path.exists(os.path.join(DADOS, "fiscalizacao.db")):
        print("Falta o banco em %s/fiscalizacao.db" % DADOS)
        return 1
    os.environ["FISC_DATA"] = DADOS
    os.environ.setdefault("FISC_HOST", "127.0.0.1")
    os.environ["FISC_PORT"] = str(PORTA)
    os.chdir(APP)
    sys.path.insert(0, APP)
    from app import app  # noqa: E402 — o Flask do sistema

    a_cada(300, "davinci", davinci)
    a_cada(120, "mega", mega)
    a_cada(3600, "backup", backup_diario)
    a_cada(60, "robo", robo)
    a_cada(5, "provas", provas_pedidas)
    a_cada(5, "comandos", comandos)
    a_cada(60, "agenda", agenda)
    a_cada(30, "anexos", anexos)
    threading.Thread(target=status_mac, name="status_mac", daemon=True).start()

    from waitress import serve
    log("Sistema de Fiscalização no ar em http://127.0.0.1:%d (Mac mini). Deixe esta janela aberta." % PORTA)
    serve(app, host="127.0.0.1", port=PORTA, threads=8,
          max_request_body_size=app.config["MAX_CONTENT_LENGTH"], channel_timeout=300,
          ident="fiscalizacao")
    return 0


if __name__ == "__main__":
    sys.exit(main())
