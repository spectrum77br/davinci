"""API interna do sidecar MEGAcmd.

Envelopa os comandos `mega-*` (que falam com o mega-cmd-server local, com a
sessão persistida em /root/.megaCmd) numa API HTTP mínima consumida SÓ pela
API DaVinci via rede docker interna. Sem porta publicada no host; ainda
assim, como a davinci_net é compartilhada com outros containers, todo
endpoint exige o header X-Sidecar-Token quando MEGA_SIDECAR_TOKEN está
setado no ambiente.
"""
from __future__ import annotations

import os
import re
import secrets
import shutil
import subprocess
import tempfile

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
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


@app.get("/folders")
def folders(root: str = "/", _: None = Depends(check_token)) -> dict:
    """Lista as entradas de primeiro nível de `root`.

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
    items = []
    for i, name in enumerate(names):
        items.append(
            {
                "name": name,
                "path": f"{base}/{name}",
                "is_folder": flags[i] if flags is not None else True,
            }
        )
    return {"root": root, "items": items, "flags_parsed": flags is not None}


@app.get("/debug/ls")
def debug_ls(path: str = "/", long: bool = True, _: None = Depends(check_token)) -> dict:
    cmd = ["mega-ls"] + (["-l"] if long else []) + [path]
    rc, out = run(cmd, timeout=300)
    return {"rc": rc, "out": out[-8000:]}


class ExportIn(BaseModel):
    path: str


@app.post("/export")
def export(body: ExportIn, _: None = Depends(check_token)) -> dict:
    # 1) já existe link? `mega-export <path>` lista as exportações atuais.
    rc, out = run(["mega-export", body.path], timeout=90)
    m = _MEGA_URL_RE.search(out)
    if not m:
        # 2) cria o link público ("yes" aceita o aviso de copyright).
        rc, out = run(
            ["mega-export", "-a", body.path], timeout=120, input_text="yes\n"
        )
        m = _MEGA_URL_RE.search(out)
    if not m:
        raise HTTPException(status_code=502, detail=out[-400:] or "export failed")
    return {"url": m.group(0).rstrip(").,")}


class MkdirIn(BaseModel):
    path: str


@app.post("/mkdir")
def mkdir(body: MkdirIn, _: None = Depends(check_token)) -> dict:
    rc, out = run(["mega-mkdir", "-p", body.path], timeout=90)
    if rc != 0 and "already exists" not in out.lower():
        raise HTTPException(status_code=502, detail=out[-400:])
    return {"ok": True}


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
