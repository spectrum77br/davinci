"""API interna do sidecar MEGAcmd.

Envelopa os comandos `mega-*` (que falam com o mega-cmd-server local, com a
sessão persistida em /root/.megaCmd) numa API HTTP mínima consumida SÓ pela
API DaVinci via rede docker interna. Sem porta publicada no host; ainda
assim, como a davinci_net é compartilhada com outros containers, todo
endpoint exige o header X-Sidecar-Token quando MEGA_SIDECAR_TOKEN está
setado no ambiente.
"""
from __future__ import annotations

import hashlib
import io
import os
import re
import secrets
import shutil
import subprocess
import tempfile
import time
import unicodedata

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from starlette.background import BackgroundTask
from pydantic import BaseModel

app = FastAPI(title="megacmd-sidecar")

TOKEN = os.environ.get("MEGA_SIDECAR_TOKEN", "")

_MEGA_URL_RE = re.compile(r"https://mega\.(?:nz|io)/\S+")
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def check_token(x_sidecar_token: str = Header(default="")) -> None:
    if TOKEN and not secrets.compare_digest(x_sidecar_token, TOKEN):
        raise HTTPException(status_code=401, detail="bad sidecar token")


def run(
    cmd: list[str], *, timeout: int = 120, input_text: str | None = None
) -> tuple[int, str]:
    """Roda um comando mega-* e devolve (returncode, stdout+stderr).

    input_text="yes\n" cobre prompts de confirmação (ex.: aviso de
    copyright do mega-export na primeira exportação).
    """
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, input=input_text
        )
    except subprocess.TimeoutExpired:
        return 124, f"timeout after {timeout}s: {' '.join(cmd[:2])}"
    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    return proc.returncode, (out + ("\n" + err if err else "")).strip()


@app.get("/health")
def health(_: None = Depends(check_token)) -> dict:
    rc, out = run(["mega-whoami"], timeout=60)
    m = _EMAIL_RE.search(out)
    logged = rc == 0 and m is not None
    return {"logged_in": logged, "email": m.group(0) if logged and m else None}


class LoginIn(BaseModel):
    email: str
    password: str
    code: str | None = None


@app.post("/login")
def login(body: LoginIn, _: None = Depends(check_token)) -> dict:
    args = ["mega-login"]
    code = (body.code or "").strip()
    if code:
        args.append(f"--auth-code={code}")
    args += [body.email.strip(), body.password]
    rc, out = run(args, timeout=180)
    if rc != 0 and "Already logged in" in out:
        run(["mega-logout"], timeout=60)
        rc, out = run(args, timeout=180)
    if rc != 0:
        # Não ecoar a senha: `out` do megacmd não a contém, mas trunca por via das dúvidas.
        return {"ok": False, "message": out[-400:] or "login failed"}
    return {"ok": True, "message": "logged in"}


def _list_one(root: str) -> tuple[list[dict], bool]:
    """Entradas de primeiro nível de `root`.

    Junta `mega-ls` (nomes puros, 1/linha) com `mega-ls -l` (flags: linhas de
    pasta começam com "d") pra marcar o que é pasta. Se os dois listamentos
    não casarem linha a linha, degrada assumindo tudo como pasta — o
    matching por nome no DaVinci tolera isso.
    """
    rc, names_out = run(["mega-ls", root], timeout=300)
    if rc != 0:
        raise HTTPException(status_code=502, detail=names_out[-400:])
    names = [ln for ln in names_out.splitlines() if ln.strip()]

    flags: list[bool] | None = None
    rc_l, long_out = run(["mega-ls", "-l", root], timeout=300)
    if rc_l == 0:
        rows = [
            ln
            for ln in long_out.splitlines()
            if ln.strip() and not ln.upper().startswith("FLAGS")
        ]
        if len(rows) == len(names):
            flags = [ln.lstrip().startswith("d") for ln in rows]

    base = root.rstrip("/")
    items = [
        {
            "name": name,
            "path": f"{base}/{name}",
            "is_folder": flags[i] if flags is not None else True,
            "level": 1,
            "has_children": False,
        }
        for i, name in enumerate(names)
    ]
    return items, flags is not None


_IMG_EXT = {"jpg", "jpeg", "png", "webp", "gif", "heic", "heif", "bmp", "tif", "tiff", "avif", "jfif"}
_VID_EXT = {"mp4", "mov", "m4v", "avi", "mkv", "webm", "3gp", "mpg", "mpeg", "wmv", "flv"}

# ── embalagens: a subpasta da caixa, dentro da pasta de fotos ──
#
# Eduardo, 29/09/2026: as fotos de caixa, embalagem e arte de impressão ficam
# na pasta do produto, numa subpasta "Embalagens". Tudo lá dentro conta SÓ
# como embalagem: a foto da caixa não pode inflar a contagem de fotos, e o
# portal das agências (que lê /files) não pode mostrar arte de impressão.
# Reconhece o nome como o operador digita no MEGA: "Embalagens", "embalagem",
# "EMBALAGÉNS " — é a mesma normalização do DaVinci (services/mega_fotos.py),
# para os dois lados concordarem sobre o que é embalagem.
_NOMES_EMBALAGEM = {"embalagens", "embalagem"}
# Lixo que o Windows e o Mac deixam ao copiar pasta (medido em 29/09/2026:
# Thumbs.db em /Celular/Fossibot S7, .DS_Store em /Malas/_geral). Ninguém
# subiu isso de propósito; contar como "arquivo de embalagem" seria mentir.
_LIXO_DE_SISTEMA = {"thumbs.db", ".ds_store", "desktop.ini"}
_NAO_ALNUM_RE = re.compile(r"[^a-z0-9]+")


def _norm(nome: str) -> str:
    s = unicodedata.normalize("NFKD", nome)
    s = "".join(ch for ch in s if not unicodedata.combining(ch)).lower()
    return _NAO_ALNUM_RE.sub(" ", s).strip()


def _raiz(path: str) -> str:
    """Caminho absoluto sem barra no fim. NÃO tira espaço: o MEGA tem pasta
    com espaço no fim do nome ("b006 M1 listrada verde claro  "), e cortar
    o espaço é apontar para uma pasta que não existe."""
    return "/" + (path or "").strip("/")


def _classificar(raiz: str, linhas: list[str]) -> dict:
    """Separa a saída do `mega-find` em fotos, vídeos e embalagens.

    Função pura (sem chamar o MEGA) para dar para testar com a saída real.
    O `mega-find` imprime um caminho ABSOLUTO por linha — a própria pasta,
    cada subpasta e cada arquivo, sem barra no fim de pasta (conferido em
    produção em 29/09/2026). Por isso pasta e arquivo só se distinguem pelo
    nome: é arquivo o que tem "." na folha e não é pai de outra linha (pasta
    com ponto no nome e conteúdo dentro deixa de virar arquivo fantasma).

    `nome` sai RELATIVO à raiz ("M1 listrada/b005.jpg",
    "Embalagens/caixa.pdf"): é ele que volta na hora de baixar.
    `embalagens_pasta` é o caminho absoluto da subpasta de embalagens se ela
    existir, mesmo vazia — o DaVinci usa para achar a pasta criada à mão.
    """
    base = _raiz(raiz)
    prefixo = base.rstrip("/") + "/"
    rels: list[str] = []
    for linha in linhas:
        # Só a quebra de linha: espaço no fim é parte do nome da pasta.
        caminho = linha.rstrip("\r\n")
        if caminho != "/":
            caminho = caminho.rstrip("/")
        if not caminho.strip() or caminho == base:
            continue
        if caminho.startswith(prefixo):
            rel = caminho[len(prefixo) :]
        else:
            # Caminho fora da raiz pedida (não deveria acontecer): fica só a
            # folha, como a versão anterior fazia.
            rel = caminho.rsplit("/", 1)[-1]
        if rel:
            rels.append(rel)
    pais = {r.rsplit("/", 1)[0] for r in rels if "/" in r}

    def _eh_pasta_emb(rel: str) -> bool:
        seg = rel.split("/", 1)[0]
        if _norm(seg) not in _NOMES_EMBALAGEM:
            return False
        return "/" in rel or rel in pais or "." not in seg

    pastas_emb = sorted({r.split("/", 1)[0] for r in rels if _eh_pasta_emb(r)})
    fotos = videos = embalagens = 0
    arquivos: list[dict] = []
    for rel in rels:
        if rel in pais:
            continue  # pasta com conteúdo
        folha = rel.rsplit("/", 1)[-1]
        if "." not in folha:
            continue  # pasta (vazia) ou arquivo sem extensão
        ext = folha.rsplit(".", 1)[-1].lower()
        if "/" in rel and rel.split("/", 1)[0] in pastas_emb:
            if folha.lower() in _LIXO_DE_SISTEMA:
                continue
            grupo = "embalagem"
            embalagens += 1
        elif ext in _IMG_EXT:
            grupo = "foto"
            fotos += 1
        elif ext in _VID_EXT:
            grupo = "video"
            videos += 1
        else:
            grupo = "outro"
        arquivos.append({"nome": rel, "ext": ext, "grupo": grupo})
    arquivos.sort(key=lambda a: a["nome"])

    escolhida = None
    if pastas_emb:
        # Duas pastas ("Embalagens" e "embalagem")? As duas contam; o destino
        # de envio é a com o nome padrão, ou sempre a mesma pela ordem.
        escolhida = "Embalagens" if "Embalagens" in pastas_emb else pastas_emb[0]
    return {
        "fotos": fotos,
        "videos": videos,
        "embalagens": embalagens,
        "embalagens_pasta": prefixo + escolhida if escolhida else None,
        "arquivos": arquivos,
    }


def _count_media(path: str) -> dict:
    """fotos/vídeos/embalagens da pasta, recursivo, via `mega-find`.

    fotos e vídeos NÃO contam o que está na subpasta de embalagens."""
    raiz = _raiz(path)
    rc, out = run(["mega-find", raiz], timeout=300)
    if rc != 0:
        raise HTTPException(status_code=502, detail=out[-400:])
    return _classificar(raiz, out.splitlines())


@app.get("/folders")
def folders(
    root: str = "/",
    depth: int = 1,
    media_counts: int = 0,
    _: None = Depends(check_token),
) -> dict:
    """Lista pastas de `root`; depth=2 desce um nível dentro de cada pasta.

    Com depth=2, pastas de nível 1 que contêm subpastas ganham
    has_children=True (containers de marca, ex.: /Uranyx) e as subpastas
    entram na lista com level=2 — o matching usa as subpastas e ignora o
    container.

    media_counts=1 acrescenta fotos/videos (contagem por extensão) em cada
    pasta-folha — um mega-find por pasta, então só ligue quando precisar.
    Vem junto embalagens/embalagens_pasta (ver `_classificar`); as chaves
    fotos/videos continuam iguais porque o Marketing e o portal leem elas.
    """
    items, parsed = _list_one(root)
    if depth >= 2:
        merged: list[dict] = []
        for it in items:
            merged.append(it)
            if not it["is_folder"]:
                continue
            try:
                kids, _ = _list_one(it["path"])
            except HTTPException:
                kids = []
            sub = [k for k in kids if k["is_folder"]]
            it["has_children"] = bool(sub)
            for k in sub:
                k["level"] = 2
                merged.append(k)
        items = merged
    if media_counts:
        for it in items:
            if not it["is_folder"] or it["has_children"]:
                continue
            try:
                c = _count_media(it["path"])
            except HTTPException:
                it["fotos"] = it["videos"] = it["embalagens"] = None
                it["embalagens_pasta"] = None
                continue
            for chave in ("fotos", "videos", "embalagens", "embalagens_pasta"):
                it[chave] = c[chave]
    return {"root": root, "items": items, "flags_parsed": parsed}


@app.get("/media_counts")
def media_counts_one(path: str, _: None = Depends(check_token)) -> dict:
    c = _count_media(path)
    return {
        "path": path,
        "fotos": c["fotos"],
        "videos": c["videos"],
        "embalagens": c["embalagens"],
        "embalagens_pasta": c["embalagens_pasta"],
    }


@app.get("/debug/ls")
def debug_ls(path: str = "/", long: bool = True, _: None = Depends(check_token)) -> dict:
    cmd = ["mega-ls"] + (["-l"] if long else []) + [path]
    rc, out = run(cmd, timeout=300)
    return {"rc": rc, "out": out[-8000:]}


# ── leitura de arquivo, pro portal das agências mostrar foto de produto ──
#
# O sidecar só sabia exportar link e subir. Mostrar foto no portal por LINK
# PÚBLICO do MEGA seria publicar a pasta inteira da linha, sem revogação e com
# o material do fornecedor junto. Com estes dois, os bytes passam pelo DaVinci
# e pela mesma trava de equipe das outras rotas.

# O que o /files entrega em cada `tipo`. "imagens" é o padrão porque é o que
# o portal das agências sempre pediu (sem passar tipo) — e continua sem a
# subpasta de embalagens, que é material interno.
_GRUPO_POR_TIPO = {"imagens": "foto", "videos": "video", "embalagens": "embalagem"}


@app.get("/files")
def files(path: str, tipo: str = "imagens", _: None = Depends(check_token)) -> dict:
    """Lista os arquivos de uma pasta, RECURSIVO, de um tipo só.

    Recursivo porque as pastas não têm o mesmo formato: `/Celular/<modelo>`
    guarda as fotos soltas, e `/Malas/<linha>` guarda uma subpasta por modelo
    (`M1 listrada`, `M2 lisa`…). Lendo só o primeiro nível, as 791 fotos de
    malas — a maior parte do acervo — apareciam como zero.

    Usa `mega-find` e a mesma `_classificar` da contagem que alimenta
    `fotos_count`/`embalagens_count`; assim a lista e o número não divergem.

    `nome` vem RELATIVO à pasta pedida, com a subpasta junto quando houver
    (embalagem vem como "Embalagens/caixa.pdf") — é ele que volta na hora de
    baixar. `ext` em minúsculas, sem o ponto.
    """
    grupo = _GRUPO_POR_TIPO.get(tipo)
    if grupo is None:
        raise HTTPException(400, "tipo inválido")
    raiz = _raiz(path)
    rc, out = run(["mega-find", raiz], timeout=300)
    if rc != 0:
        return {"rc": rc, "out": out[-2000:], "arquivos": []}
    c = _classificar(raiz, out.splitlines())
    arquivos = [
        {"nome": a["nome"], "ext": a["ext"], "imagem": a["ext"] in _IMG_EXT}
        for a in c["arquivos"]
        if a["grupo"] == grupo
    ]
    return {"rc": 0, "arquivos": arquivos}


@app.get("/file")
def file(path: str, _: None = Depends(check_token)):
    """Baixa UM arquivo e devolve os bytes.

    `mega-get` não escreve em stdout, então o arquivo desce para um diretório
    temporário e é removido depois de servido — o container não acumula cópia
    do acervo.
    """
    _validar_caminho(path)
    if _identidade(path) is None:  # não existe, ou é pasta (ver _identidade)
        raise HTTPException(404, "arquivo não encontrado")
    tmp, destino = _baixar(path)
    return FileResponse(
        destino,
        filename=destino.rsplit("/", 1)[-1],
        background=BackgroundTask(shutil.rmtree, tmp, ignore_errors=True),
    )


def _baixar(path: str) -> tuple[str, str]:
    """`mega-get` de UM arquivo para um diretório temporário: (tmp, arquivo).
    Quem chama apaga o tmp depois de usar."""
    nome = _validar_caminho(path)
    tmp = tempfile.mkdtemp(prefix="megafile")
    rc, out = run(["mega-get", path, tmp], timeout=600)
    destino = os.path.join(tmp, nome)
    if rc != 0 or not os.path.isfile(destino):
        shutil.rmtree(tmp, ignore_errors=True)
        raise HTTPException(404, f"não baixou: {out[-300:]}")
    return tmp, destino


def _validar_caminho(path: str) -> str:
    """Devolve o nome do arquivo, ou HTTPException 400."""
    nome = path.rsplit("/", 1)[-1]
    # `..` fora: o nome vem da listagem, mas chega por parâmetro. Subpasta é
    # legítima (as malas têm uma por modelo), subir de nível não é.
    # Curinga e trecho "." também: o MEGAcmd trata "*" e "?" como padrão e
    # resolve "." (conferido em produção, 29/09/2026). Com eles, "./Embalagens/
    # caixa.jpg" furava a trava de embalagens do portal, e "*" fazia o
    # `mega-get` — que é recursivo — baixar a pasta inteira do produto para
    # o disco antes do 404. Vale para todos que chamam /file (Tabela de
    # Preços e portal das agências). Nenhum fotos_path de produção tem "*" ou
    # "?"; trecho vazio ("//") não é barrado aqui porque o caminho inteiro
    # inclui a pasta do produto — quem chama já recusa no nome do arquivo.
    # ".." barrado como TRECHO do caminho, não como pedaço de nome: o arquivo
    # real "manual-uranyx-p01..af" (Panelas ferro, 29/09) era recusado inteiro.
    if (
        not nome
        or any(c in path for c in "*?")
        or any(s.strip() in (".", "..") for s in path.split("/"))
    ):
        raise HTTPException(400, "caminho inválido")
    return nome


# Prévia de arquivo de gráfica (29/09/2026): na aba Embalagens a caixa da
# Uranyx aparecia só como ícone "PDF"/"AF" — o Eduardo: "está aparecendo em
# pdf". PDF e AI (salvo com compatibilidade PDF, o padrão do Illustrator) saem
# pela 1ª página no pdftoppm (poppler-utils); o Affinity grava a prévia do
# documento como o ÚLTIMO PNG do arquivo (os anteriores são as fotos e logos
# usados dentro da arte — conferido nos 20 .af da pasta CAIXAS); o PSD o
# Pillow abre pela imagem composta.
_EXT_PDF = {"pdf", "ai"}
_EXT_AFFINITY = {"af", "afdesign", "afphoto", "afpub"}
_PNG_INICIO = b"\x89PNG\r\n\x1a\n"


def _png_do_affinity(caminho: str) -> bytes | None:
    with open(caminho, "rb") as fh:
        dados = fh.read()
    ultimo = None
    pos = dados.find(_PNG_INICIO)
    while pos >= 0:
        fim = dados.find(b"IEND", pos)
        if fim < 0:
            break
        ultimo = (pos, fim + 8)
        pos = dados.find(_PNG_INICIO, fim)
    return dados[ultimo[0] : ultimo[1]] if ultimo else None


def _abrir_para_previa(caminho: str, ext: str, lado: int, tmp: str):
    """Imagem (Pillow) que representa o arquivo, ou HTTPException 422."""
    from PIL import Image

    if ext in _EXT_PDF:
        saida = os.path.join(tmp, "pagina1")
        rc, out = run(
            ["pdftoppm", "-f", "1", "-l", "1", "-singlefile", "-png",
             "-scale-to", str(lado), caminho, saida],
            timeout=180,
        )
        if rc == 124 or rc < 0:
            # Estourou o tempo ou foi morto (memória): problema da hora, não do
            # arquivo — 503 não fica anotado como "sem prévia".
            raise HTTPException(503, f"prévia do PDF não terminou: {out[-200:]}")
        if rc != 0 or not os.path.isfile(saida + ".png"):
            raise HTTPException(422, f"sem prévia do PDF: {out[-200:]}")
        return Image.open(saida + ".png")
    if ext in _EXT_AFFINITY:
        png = _png_do_affinity(caminho)
        if not png:
            raise HTTPException(422, "arquivo do Affinity sem prévia embutida")
        return Image.open(io.BytesIO(png))
    return Image.open(caminho)


# Cache das prévias (29/09/2026). Eduardo: "quando eu clico para ver as fotos
# demoram muito para renderizar […] para todos precisa ser rápido". Cada
# miniatura custava um `mega-get` do arquivo inteiro (0,3 s a 5 s; um .af de
# caixa tem 60 MB) em TODA abertura por outra pessoa ou outro navegador. Agora
# o JPEG gerado fica em disco (volume próprio, sobrevive ao rebuild) e a
# chave inclui tamanho+data do arquivo no MEGA (`mega-ls -l`, ~70 ms): trocou
# o arquivo com o mesmo nome, a chave muda e a prévia é refeita sozinha.
# Numa descida só saem as DUAS medidas que a tela usa (320 da grade e 1600 do
# visor) — pré-aquecer uma já deixa a outra pronta.
_PREVIAS_DIR = os.environ.get("PREVIAS_DIR", "/srv/previas")
_PREVIA_VERSAO = "1"  # mude se o jeito de gerar mudar (invalida o cache todo)
_LADOS_PADRAO = (320, 1600)
_LS_ARQUIVO_RE = re.compile(r"^-\S*\s+\S+\s+(\d+)\s+(\S+\s+\S+)\s")


def _identidade(path: str) -> str | None:
    """"tamanho|data" do ARQUIVO no MEGA, ou None se não existe ou é pasta.

    Pasta também responde ao `mega-ls -l` (lista os filhos) — por isso a
    linha tem de terminar no próprio nome. Sem essa conferência, uma subpasta
    com ponto no nome ("M2 v1.5") passava por arquivo e o `mega-get`, que é
    recursivo, descia a pasta inteira para o disco antes do 404.
    """
    nome = path.rsplit("/", 1)[-1]
    rc, out = run(["mega-ls", "-l", path], timeout=60)
    if rc != 0:
        return None
    for ln in out.splitlines():
        m = _LS_ARQUIVO_RE.match(ln)
        # O nome INTEIRO depois da data (conferido em produção: arquivo sai
        # numa linha só, com o nome simples). "endswith" deixava passar uma
        # pasta "M2 v1.5" que tivesse dentro um arquivo "capa M2 v1.5".
        if m and ln[m.end():].rstrip() == nome.rstrip():
            return f"{m.group(1)}|{m.group(2)}"
    return None


def _arquivo_cache(path: str, ident: str, lado: int, sufixo: str = ".jpg") -> str:
    chave = hashlib.sha1(
        f"{_PREVIA_VERSAO}\0{path}\0{ident}\0{lado}".encode("utf-8", "surrogateescape")
    ).hexdigest()
    return os.path.join(_PREVIAS_DIR, chave[:2], chave + sufixo)


def _gravar_cache(destino: str, dados: bytes) -> None:
    # Escreve ao lado e troca de uma vez: quem ler no meio nunca pega meio JPEG.
    tmp = None
    try:
        os.makedirs(os.path.dirname(destino), exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(destino), suffix=".tmp")
        with os.fdopen(fd, "wb") as fh:
            fh.write(dados)
        os.replace(tmp, destino)
    except OSError:
        # Cache é otimização: disco cheio não pode derrubar a prévia — mas
        # também não pode deixar ".tmp" pela metade para trás.
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass


def _gerar_previas(destino: str, ext: str, lados: list[int], tmp: str) -> dict[int, bytes]:
    import warnings

    from PIL import Image

    maior = max(lados)
    try:
        # Acima de ~89 Mpx o Pillow só AVISA; aqui vira erro (422). Com 4
        # prévias em paralelo no pré-aquecimento, um PSD de 80x60 cm a 300 dpi
        # passava de 800 MB por prévia (medido na revisão de 29/09).
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            return _reduzir(destino, ext, lados, maior, tmp)
    except (OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise HTTPException(422, f"não abriu como imagem: {exc}") from exc


def _reduzir(destino: str, ext: str, lados: list[int], maior: int, tmp: str) -> dict[int, bytes]:
    from PIL import Image, ImageOps

    with _abrir_para_previa(destino, ext, maior, tmp) as im:
        im.draft("RGB", (maior * 2, maior * 2))  # JPEG grande decodifica já reduzido
        # Reduz ANTES de girar/converter: as cópias e conversões abaixo
        # passam a ser do tamanho da prévia, não da imagem inteira. Paleta
        # e 1 bit convertem antes, senão a redução sai serrilhada.
        if im.mode == "P":
            im = im.convert("RGBA")
        elif im.mode == "1":
            im = im.convert("L")
        im.thumbnail((maior * 2, maior * 2))
        im = ImageOps.exif_transpose(im)
        if im.mode in ("RGBA", "LA", "P"):
            im = im.convert("RGBA")
            fundo = Image.new("RGB", im.size, (255, 255, 255))
            fundo.paste(im, mask=im.getchannel("A"))
            im = fundo
        elif im.mode != "RGB":
            im = im.convert("RGB")
        saidas: dict[int, bytes] = {}
        for lado in sorted(lados, reverse=True):
            copia = im.copy()
            copia.thumbnail((lado, lado))
            buf = io.BytesIO()
            copia.save(buf, "JPEG", quality=80, optimize=True, progressive=lado > 320)
            saidas[lado] = buf.getvalue()
        return saidas


def _jpeg(dados: bytes, cache: str) -> Response:
    return Response(content=dados, media_type="image/jpeg", headers={"X-Previa-Cache": cache})


@app.get("/thumb")
def thumb(path: str, lado: int = 320, _: None = Depends(check_token)) -> Response:
    """Miniatura JPEG de UMA imagem, ou prévia de PDF/AI/Affinity/PSD (lado
    maior = `lado`, entre 64 e 1600 — 1600 é a prévia grande do visor).

    O painel de mídias da Tabela de Preços pede 24 fotos de uma vez, e as
    fotos do fornecedor têm 0,3 a 1,5 MB cada: medido em 29/09/2026, a aba
    Fotos do Fossibot S7 baixava 24,8 MB para desenhar quadrados de 112 px
    (o MEGA entregava as 24 em 2,5 s; o resto era a internet de quem abria).
    A redução acontece aqui, onde o arquivo já desce do MEGA, e só a
    miniatura atravessa a API. O que não abre → 422 (e fica anotado no cache
    para não baixar de novo): imagem a API entrega inteira; arquivo de
    gráfica fica com o ícone.
    """
    _validar_caminho(path)
    lado = min(1600, max(64, lado))
    ident = _identidade(path)
    if ident is None:
        raise HTTPException(404, "arquivo não encontrado")
    pedido = _arquivo_cache(path, ident, lado)
    try:
        with open(pedido, "rb") as fh:
            return _jpeg(fh.read(), "hit")
    except OSError:
        pass
    falhou = _arquivo_cache(path, ident, 0, ".falhou")
    try:
        # Validade de 7 dias: se o que faltava era do servidor (poppler, fonte),
        # a prévia volta sozinha depois de um deploy.
        if time.time() - os.path.getmtime(falhou) < 7 * 86400:
            raise HTTPException(422, "sem prévia (já tentado)")
    except OSError:
        pass

    ext = path.rsplit(".", 1)[-1].lower() if "." in path.rsplit("/", 1)[-1] else ""
    lados = sorted({*_LADOS_PADRAO, lado})
    tmp, destino = _baixar(path)
    try:
        try:
            saidas = _gerar_previas(destino, ext, lados, tmp)
        except HTTPException as exc:
            if exc.status_code == 422:  # o arquivo não tem prévia; 503 é da hora
                _gravar_cache(falhou, b"")
            raise
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    for lado_gerado, dados in saidas.items():
        _gravar_cache(_arquivo_cache(path, ident, lado_gerado), dados)
    return _jpeg(saidas[lado], "miss")


class ExportIn(BaseModel):
    path: str


def _export_link(path: str) -> tuple[str | None, str]:
    # 1) já existe link? `mega-export <path>` lista as exportações atuais.
    rc, out = run(["mega-export", path], timeout=90)
    m = _MEGA_URL_RE.search(out)
    if not m:
        # 2) cria o link público ("yes" aceita o aviso de copyright).
        rc, out = run(["mega-export", "-a", path], timeout=120, input_text="yes\n")
        m = _MEGA_URL_RE.search(out)
    return (m.group(0).rstrip(").,") if m else None), out


@app.post("/export")
def export(body: ExportIn, _: None = Depends(check_token)) -> dict:
    url, out = _export_link(body.path)
    if not url:
        raise HTTPException(status_code=502, detail=out[-400:] or "export failed")
    return {"url": url}


class MkdirIn(BaseModel):
    path: str


@app.post("/mkdir")
def mkdir(body: MkdirIn, _: None = Depends(check_token)) -> dict:
    rc, out = run(["mega-mkdir", "-p", body.path], timeout=90)
    if rc != 0 and "already exists" not in out.lower():
        raise HTTPException(status_code=502, detail=out[-400:])
    return {"ok": True}


class ScaffoldIn(BaseModel):
    root: str
    names: list[str]


@app.post("/scaffold")
def scaffold(body: ScaffoldIn, _: None = Depends(check_token)) -> dict:
    """Cria <root>/<name> pra cada nome e devolve o link público de cada
    pasta. Idempotente: pasta existente só tem o link (re)exportado."""
    stripped = body.root.strip().strip("/")
    base = f"/{stripped}" if stripped else ""
    results = []
    for raw in body.names[:300]:
        name = raw.strip().strip("/").replace("/", "-")
        if not name:
            continue
        path = f"{base}/{name}"
        rc, out = run(["mega-mkdir", "-p", path], timeout=90)
        if rc != 0 and "already exists" not in out.lower():
            results.append(
                {"name": name, "path": path, "url": None, "error": out[-300:]}
            )
            continue
        url, exp_out = _export_link(path)
        results.append(
            {
                "name": name,
                "path": path,
                "url": url,
                "error": None if url else (exp_out[-300:] or "export failed"),
            }
        )
    return {"root": base or "/", "results": results}


@app.post("/upload")
def upload(
    dest: str = Form(...),
    files: list[UploadFile] = File(...),
    _: None = Depends(check_token),
) -> dict:
    if not files:
        raise HTTPException(status_code=400, detail="no files")
    with tempfile.TemporaryDirectory() as td:
        paths: list[str] = []
        for f in files:
            name = os.path.basename(f.filename or "") or f"foto_{len(paths)}.jpg"
            local = os.path.join(td, name)
            with open(local, "wb") as fh:
                shutil.copyfileobj(f.file, fh)
            paths.append(local)
        run(["mega-mkdir", "-p", dest], timeout=90)  # ok se já existe
        dest_dir = dest if dest.endswith("/") else dest + "/"
        rc, out = run(["mega-put", "-c", *paths, dest_dir], timeout=3600)
        if rc != 0:
            raise HTTPException(status_code=502, detail=out[-400:] or "upload failed")
    return {"uploaded": len(paths), "dest": dest}
