"""Denúncia — as três telas (Anúncios, Denúncias, Casos) e a porta por onde o
Mac mini da Makisa manda a cópia.

Contexto em `models/denuncia.py`. Duas metades:

1. **`/api/denuncia/sync/*`** — só o remetente cadastrado (`denuncia_remetentes`,
   `Authorization: Bearer <token>`). O `enviar_ao_davinci.py` do mini manda,
   por tabela, as linhas novas ou mudadas e os ids que sumiram. Os ARQUIVOS das
   provas ficam no MEGA (Vinicius, 30/09: disco do servidor curto); a porta de
   arquivo (`PUT …/provas/{id}/arquivo`) existe mas o mini roda `--so-dados`;
   desde 01/10 o arquivo sobe sob demanda: a tela pede (`POST
   /provas/{id}/preparar`), o mini vê em `GET …/sync/provas-pedidas` e sobe só
   aquele, e o DaVinci guarda até `TETO_PROVAS_BYTES`.
2. **Telas** — `require_permission("denuncia", "view")`. Só leitura: quem muda
   algo é o sistema do mini.

Fora do openapi a porta do sync (é o mini, não uma tela).
"""

from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import re
import secrets
import tempfile
from collections import OrderedDict, defaultdict
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any

import structlog
from fastapi import (
    APIRouter,
    Body,
    Depends,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import case, delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.deps.auth import require_permission
from app.models.denuncia import (
    DenunciaAnexo,
    DenunciaAnuncio,
    DenunciaCaso,
    DenunciaCasoExtra,
    DenunciaCompra,
    DenunciaDenuncia,
    DenunciaLoja,
    DenunciaProva,
    DenunciaRelatorio,
    DenunciaRemetente,
    DenunciaRoboAgenda,
    DenunciaRoboComando,
    DenunciaRoboStatus,
    DenunciaRoboTratada,
    DenunciaVerificacao,
)
from app.models.user import User
from app.services import denuncia_painel as painel
from app.services import denuncia_relatorio as relatorio_dia
from app.services.denuncia_robo import PASSOS, montar_painel, normalizar_horarios

logger = structlog.get_logger()

router = APIRouter(prefix="/api/denuncia", tags=["denuncia"])
sync_router = APIRouter(prefix="/api/denuncia/sync", tags=["denuncia"], include_in_schema=False)

_ver = require_permission("denuncia", "view")
_editar = require_permission("denuncia", "edit")

# Pasta das provas dentro de `uploads_dir` (volume compartilhado api/worker).
PASTA_PROVAS = "denuncia/provas"
# "Anexar prova" da ficha do caso (01/10): o arquivo espera aqui até o mini buscar
PASTA_ANEXOS = "denuncia/anexos"
TETO_ANEXO_BYTES = 30 * 1024 * 1024
# tipo na tela → (tipo de prova no sistema do mini, nome na tela). Os tipos do mini são os que o
# checklist do advogado procura (modelos.CHECKLIST_ADVOGADO / db.DOCS_CASO); a devolução vai como
# "Outro" com "Devolução" na observação (o checklist procura "devolu"). Vídeo só como link do MEGA.
TIPOS_ANEXO: dict[str, tuple[str, str]] = {
    "video": ("Vídeo", "Vídeo da embalagem sendo aberta (link do MEGA)"),
    "foto": ("Foto", "Foto do produto / selo Anatel"),
    "nfe": ("NF-e", "NF-e da compra (PDF)"),
    "fatura": ("Fatura do cartão", "Comprovante na fatura do cartão"),
    "pedido": ("Tela do pedido", "Tela do pedido (compra de prova)"),
    "devolucao": ("Outro", "Comprovante do pedido de devolução"),
    "print": ("Print", "Print do anúncio"),
    "outro": ("Outro", "Outro documento"),
}
# Um lote do sync: o mini manda de 500 em 500.
MAX_LINHAS_LOTE = 2000
# Provas sob demanda (01/10): pedido que o mini não atendeu em 10 min caduca
# (mini desligado); o DaVinci guarda os arquivos abertos até este teto e
# apaga os abertos há mais tempo (disco do servidor curto — ver migration 0349).
PEDIDO_PROVA_VALE = timedelta(minutes=10)
TETO_PROVAS_BYTES = 1024 * 1024 * 1024
# Resumo do robô: hoje ~30 itens (um por tarefa do dia + contas); folga larga.
MAX_ITENS_ROBO = 1000
# Grupos que a lista de anúncios esconde por padrão (igual ao sistema do mini).
GRUPOS_ESCONDIDOS = ("DESCARTADO", "FORA DE ESCOPO")


def _txt(v: Any) -> str | None:
    if v is None:
        return None
    s = str(v)
    return s if s != "" else None


def _int(v: Any, padrao: int | None = 0) -> int | None:
    if v is None or v == "":
        return padrao
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return padrao


# tabela do mini → (modelo, coluna-chave, conversor da chave, colunas próprias)
# Cada coluna própria: nome → função que tira o valor da linha original.
TABELAS: dict[str, tuple[type, str, Any, dict[str, Any]]] = {
    "anuncios": (
        DenunciaAnuncio,
        "id",
        str,
        {
            "marketplace": lambda r: _txt(r.get("marketplace")),
            "shop_id": lambda r: _txt(r.get("shop_id")),
            "loja": lambda r: _txt(r.get("loja")),
            "titulo": lambda r: _txt(r.get("titulo")),
            "grupo": lambda r: _txt(r.get("grupo")),
            "escopo": lambda r: _txt(r.get("escopo")),
            "situacao": lambda r: _txt(r.get("situacao")),
            "propria": lambda r: _int(r.get("propria")),
            "vendas": lambda r: _int(r.get("vendas")),
            "visto_primeiro": lambda r: _txt(r.get("visto_primeiro")),
        },
    ),
    "lojas": (
        DenunciaLoja,
        "shop_id",
        str,
        {
            "marketplace": lambda r: _txt(r.get("marketplace")),
            "nome": lambda r: _txt(r.get("nome")),
            "propria": lambda r: _int(r.get("propria")),
        },
    ),
    "denuncias": (
        DenunciaDenuncia,
        "id",
        int,
        {
            "anuncio_id": lambda r: _txt(r.get("anuncio_id")),
            "canal": lambda r: _txt(r.get("canal")),
            "protocolo": lambda r: _txt(r.get("protocolo")),
            "data": lambda r: _txt(r.get("data")),
            "situacao": lambda r: _txt(r.get("situacao")),
            "resultado": lambda r: _txt(r.get("resultado")),
            "tipo": lambda r: _txt(r.get("tipo")),
            "prazo": lambda r: _txt(r.get("prazo")),
        },
    ),
    "casos": (
        DenunciaCaso,
        "id",
        int,
        {
            "codigo": lambda r: _txt(r.get("codigo")),
            "anuncio_id": lambda r: _txt(r.get("anuncio_id")),
            "status": lambda r: _txt(r.get("status")),
        },
    ),
    "compras": (
        DenunciaCompra,
        "id",
        int,
        {
            "anuncio_id": lambda r: _txt(r.get("anuncio_id")),
            "caso_id": lambda r: _int(r.get("caso_id"), None),
            "status": lambda r: _txt(r.get("status")),
        },
    ),
    "provas": (
        DenunciaProva,
        "id",
        int,
        {
            "anuncio_id": lambda r: _txt(r.get("anuncio_id")),
            "caso_id": lambda r: _int(r.get("caso_id"), None),
            "denuncia_id": lambda r: _int(r.get("denuncia_id"), None),
            "tipo": lambda r: _txt(r.get("tipo")),
            "sha256": lambda r: _txt(r.get("sha256")),
            "tamanho": lambda r: _int(r.get("tamanho"), None),
        },
    ),
    "verificacoes": (
        DenunciaVerificacao,
        "id",
        int,
        {"anuncio_id": lambda r: _txt(r.get("anuncio_id"))},
    ),
}


# ─────────────────────────────────────────────────────────────── sync (mini)


async def _remetente(
    session: Annotated[AsyncSession, Depends(get_session)],
    authorization: Annotated[str | None, Header()] = None,
) -> DenunciaRemetente:
    token = ""
    if authorization and authorization[:7].lower() == "bearer ":
        token = authorization[7:].strip()
    if not token:
        raise HTTPException(401, detail={"code": "denuncia_sync_sem_token"})
    h = hashlib.sha256(token.encode()).hexdigest()
    rows = (
        await session.execute(
            select(DenunciaRemetente).where(DenunciaRemetente.revoked_at.is_(None))
        )
    ).scalars().all()
    for r in rows:
        if secrets.compare_digest(r.token_hash, h):
            return r
    raise HTTPException(401, detail={"code": "denuncia_sync_token_invalido"})


@sync_router.post("/pulso")
async def sync_pulso(
    session: Annotated[AsyncSession, Depends(get_session)],
    remetente: Annotated[DenunciaRemetente, Depends(_remetente)],
) -> dict:
    """O mini chama no fim de toda rodada, mesmo sem nada novo — é o "cópia
    de há 3 min" do topo das telas. Sem isso, horário parado (noite, fim de
    semana) pareceria mini desligado."""
    remetente.ultimo_envio_em = datetime.now(UTC)
    await session.commit()
    return {"ok": True}


@sync_router.post("/robo")
async def sync_robo(
    session: Annotated[AsyncSession, Depends(get_session)],
    remetente: Annotated[DenunciaRemetente, Depends(_remetente)],
    corpo: Annotated[dict, Body()],
) -> dict:
    """O resumo do robô (`status_mac.py`, a cada 60 s) — a aba Robô. Guarda
    só o último, por remetente; e anota no relatório do dia (05/10) os passos e
    as ocorrências, que o "último" esquece."""
    itens = corpo.get("itens")
    if not isinstance(itens, list) or len(itens) > MAX_ITENS_ROBO:
        raise HTTPException(422, detail={"code": "denuncia_robo_corpo_invalido"})
    agora = datetime.now(UTC)
    stmt = pg_insert(DenunciaRoboStatus).values(
        remetente=remetente.nome, dados=corpo, recebido_em=agora
    )
    await session.execute(
        stmt.on_conflict_do_update(
            index_elements=["remetente"],
            set_={"dados": stmt.excluded.dados, "recebido_em": stmt.excluded.recebido_em},
        )
    )
    await session.commit()
    try:
        await relatorio_dia.anotar_resumo(session, corpo, agora, await _agenda(session))
        await session.commit()
    except Exception:   # o relatório nunca derruba o estado do robô
        await session.rollback()
        logger.exception("denuncia_relatorio_anotar_falhou")
    return {"ok": True}


# Botões da aba Robô (01/10): o mini busca os comandos pendentes a cada 5 s.
# Comando que ninguém pegou em 1 h (mini desligado) não vale mais.
COMANDO_VALE = timedelta(hours=1)
# "Tratado" vale 3 dias — o robô guarda o problema 24 h; a ocorrência "agora"
# que voltar depois disso é problema novo.
TRATADA_VALE = timedelta(days=3)


@sync_router.get("/robo/comandos")
async def sync_robo_comandos(
    session: Annotated[AsyncSession, Depends(get_session)],
    _r: Annotated[DenunciaRemetente, Depends(_remetente)],
) -> dict:
    rows = (
        await session.execute(
            select(DenunciaRoboComando)
            .where(
                DenunciaRoboComando.entregue_em.is_(None),
                DenunciaRoboComando.pedido_em >= datetime.now(UTC) - COMANDO_VALE,
            )
            .order_by(DenunciaRoboComando.id)
        )
    ).scalars().all()
    return {"comandos": [
        {"id": c.id, "tipo": c.tipo, "dados": c.dados, "pedido_por": c.pedido_por} for c in rows
    ]}


async def _agenda(session: AsyncSession) -> dict[str, dict]:
    """A agenda dos passos (tabela denuncia_robo_agenda): acao → {ligado, horarios}.
    Só os passos atuais."""
    rows = (await session.execute(select(DenunciaRoboAgenda))).scalars().all()
    return {r.acao: {"ligado": bool(r.ligado), "horarios": list(r.horarios or [])}
            for r in rows if r.acao in PASSOS}


@sync_router.get("/robo/agenda")
async def sync_robo_agenda(
    session: Annotated[AsyncSession, Depends(get_session)],
    _r: Annotated[DenunciaRemetente, Depends(_remetente)],
) -> dict:
    """02/10: o mini puxa a agenda a cada minuto e grava no despertador.json do agente (o comando
    "agenda" só adianta o puxão). Passo fora da tabela = desligado."""
    return {"agenda": await _agenda(session)}


@sync_router.post("/robo/comandos/{comando_id}")
async def sync_robo_comando_feito(
    comando_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    _r: Annotated[DenunciaRemetente, Depends(_remetente)],
    corpo: Annotated[dict, Body()],
) -> dict:
    c = (
        await session.execute(
            select(DenunciaRoboComando).where(DenunciaRoboComando.id == comando_id)
        )
    ).scalar_one_or_none()
    if c is None:
        raise HTTPException(404, detail={"code": "denuncia_comando_nao_encontrado"})
    c.entregue_em = datetime.now(UTC)
    c.ok = bool(corpo.get("ok"))
    c.resultado = str(corpo.get("resultado") or "")[:1000]
    await session.commit()
    return {"ok": True}


def _pasta_anexos() -> Path:
    p = Path(get_settings().uploads_dir) / PASTA_ANEXOS
    p.mkdir(parents=True, exist_ok=True)
    return p


def _anexo_resumo(x: DenunciaAnexo) -> dict:
    tipo_mini, nome_tipo = TIPOS_ANEXO.get(x.tipo, ("Outro", x.tipo))
    return {
        "id": x.id, "caso_id": x.caso_id, "anuncio_id": x.anuncio_id, "tipo": x.tipo,
        "tipo_nome": nome_tipo, "tipo_prova": tipo_mini, "nome": x.nome, "tamanho": x.tamanho,
        "sha256": x.sha256, "link": x.link, "obs": x.obs, "tem_arquivo": bool(x.arquivo_local),
        "enviado_por": x.enviado_por,
        "enviado_em": x.enviado_em.isoformat() if x.enviado_em else None,
        "entregue_em": x.entregue_em.isoformat() if x.entregue_em else None,
        "ok": x.ok, "resultado": x.resultado,
    }


@sync_router.get("/anexos")
async def sync_anexos(
    session: Annotated[AsyncSession, Depends(get_session)],
    _r: Annotated[DenunciaRemetente, Depends(_remetente)],
) -> dict:
    """Provas anexadas no DaVinci que o mini ainda não entregou ao sistema de lá (sem prazo:
    mini desligado na hora = entrega quando voltar)."""
    rows = (
        await session.execute(
            select(DenunciaAnexo)
            .where(DenunciaAnexo.entregue_em.is_(None))
            .order_by(DenunciaAnexo.id)
            .limit(20)
        )
    ).scalars().all()
    return {"anexos": [_anexo_resumo(x) for x in rows]}


@sync_router.get("/anexos/{anexo_id}/arquivo")
async def sync_anexo_arquivo(
    anexo_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    _r: Annotated[DenunciaRemetente, Depends(_remetente)],
) -> FileResponse:
    x = (
        await session.execute(select(DenunciaAnexo).where(DenunciaAnexo.id == anexo_id))
    ).scalar_one_or_none()
    if x is None or not x.arquivo_local:
        raise HTTPException(404, detail={"code": "denuncia_anexo_sem_arquivo"})
    caminho = Path(get_settings().uploads_dir) / x.arquivo_local
    if not caminho.is_file():
        raise HTTPException(404, detail={"code": "denuncia_anexo_sem_arquivo"})
    return FileResponse(caminho, filename=x.nome or caminho.name)


@sync_router.post("/anexos/{anexo_id}")
async def sync_anexo_entregue(
    anexo_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    _r: Annotated[DenunciaRemetente, Depends(_remetente)],
    corpo: Annotated[dict, Body()],
) -> dict:
    """O mini entregou (ou não) ao sistema de lá. Entregue com sucesso: o arquivo daqui some
    (a prova volta na cópia, e o original fica no mini e no MEGA)."""
    x = (
        await session.execute(select(DenunciaAnexo).where(DenunciaAnexo.id == anexo_id))
    ).scalar_one_or_none()
    if x is None:
        raise HTTPException(404, detail={"code": "denuncia_anexo_nao_encontrado"})
    x.entregue_em = datetime.now(UTC)
    x.ok = bool(corpo.get("ok"))
    x.resultado = str(corpo.get("resultado") or "")[:1000]
    if x.ok and x.arquivo_local:
        (Path(get_settings().uploads_dir) / x.arquivo_local).unlink(missing_ok=True)
        x.arquivo_local = None
    await session.commit()
    return {"ok": True}


# Rota genérica por último: `/pulso` e `/robo` acima não podem cair aqui.
@sync_router.post("/{tabela}")
async def sync_tabela(
    tabela: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    remetente: Annotated[DenunciaRemetente, Depends(_remetente)],
    corpo: Annotated[dict, Body()],
) -> dict:
    """Grava as linhas novas/mudadas (`linhas`) e apaga as que sumiram no mini
    (`removidos`). Idempotente: mandar a mesma linha duas vezes só regrava."""
    if tabela not in TABELAS:
        raise HTTPException(404, detail={"code": "denuncia_tabela_desconhecida", "tabela": tabela})
    modelo, chave, conv, colunas = TABELAS[tabela]
    linhas = corpo.get("linhas") or []
    removidos = corpo.get("removidos") or []
    if not isinstance(linhas, list) or not isinstance(removidos, list):
        raise HTTPException(422, detail={"code": "denuncia_corpo_invalido"})
    if len(linhas) > MAX_LINHAS_LOTE or len(removidos) > 50_000:
        raise HTTPException(413, detail={"code": "denuncia_lote_grande"})

    valores = []
    for r in linhas:
        if not isinstance(r, dict) or r.get(chave) in (None, ""):
            raise HTTPException(422, detail={"code": "denuncia_linha_sem_chave", "chave": chave})
        try:
            k = conv(r[chave])
        except (TypeError, ValueError):
            raise HTTPException(
                422, detail={"code": "denuncia_chave_invalida", "valor": str(r[chave])[:40]}
            ) from None
        v = {chave: k, "dados": r, "recebido_em": datetime.now(UTC)}
        for col, f in colunas.items():
            v[col] = f(r)
        valores.append(v)

    if valores:
        stmt = pg_insert(modelo).values(valores)
        atualiza = {c: stmt.excluded[c] for c in ["dados", "recebido_em", *colunas.keys()]}
        await session.execute(
            stmt.on_conflict_do_update(index_elements=[chave], set_=atualiza)
        )
    apagados = 0
    if removidos:
        try:
            ids = [conv(x) for x in removidos]
        except (TypeError, ValueError):
            raise HTTPException(422, detail={"code": "denuncia_removido_invalido"}) from None
        res = await session.execute(delete(modelo).where(getattr(modelo, chave).in_(ids)))
        apagados = res.rowcount or 0
        if tabela == "provas":
            # o arquivo vai junto (a linha que sumiu no mini não volta)
            for pid in ids:
                for f in _pasta_provas().glob(f"{pid}.*"):
                    f.unlink(missing_ok=True)
    remetente.ultimo_envio_em = datetime.now(UTC)
    await session.commit()
    logger.info(
        "denuncia_sync", tabela=tabela, gravadas=len(valores), apagadas=apagados,
        remetente=remetente.nome,
    )
    return {"gravadas": len(valores), "apagadas": apagados}


@sync_router.get("/provas-pedidas")
async def sync_provas_pedidas(
    session: Annotated[AsyncSession, Depends(get_session)],
    _r: Annotated[DenunciaRemetente, Depends(_remetente)],
) -> dict:
    """Provas que alguém clicou pra ver e ainda não estão aqui. O mini
    pergunta a cada poucos segundos e sobe só essas (PUT abaixo)."""
    ids = (
        await session.execute(
            select(DenunciaProva.id)
            .where(
                DenunciaProva.arquivo_local.is_(None),
                DenunciaProva.arquivo_pedido_em >= datetime.now(UTC) - PEDIDO_PROVA_VALE,
            )
            .order_by(DenunciaProva.arquivo_pedido_em)
        )
    ).scalars().all()
    return {"ids": list(ids)}


@sync_router.get("/provas-sem-arquivo")
async def sync_provas_sem_arquivo(
    session: Annotated[AsyncSession, Depends(get_session)],
    _r: Annotated[DenunciaRemetente, Depends(_remetente)],
) -> dict:
    """Ids das provas cuja linha já chegou mas o arquivo não."""
    ids = (
        await session.execute(
            select(DenunciaProva.id)
            .where(DenunciaProva.arquivo_local.is_(None))
            .order_by(DenunciaProva.id.desc())
        )
    ).scalars().all()
    return {"ids": list(ids)}


def _pasta_provas() -> Path:
    p = Path(get_settings().uploads_dir) / PASTA_PROVAS
    p.mkdir(parents=True, exist_ok=True)
    return p


_EXT_OK = re.compile(r"^\.[a-z0-9]{1,5}$")


@sync_router.put("/provas/{prova_id}/arquivo")
async def sync_prova_arquivo(
    prova_id: int,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    _r: Annotated[DenunciaRemetente, Depends(_remetente)],
) -> dict:
    """Corpo = o arquivo cru. Confere o sha256 com o da linha (quando a linha
    tem) antes de guardar; arquivo diferente → 409 e nada muda."""
    prova = (
        await session.execute(select(DenunciaProva).where(DenunciaProva.id == prova_id))
    ).scalar_one_or_none()
    if prova is None:
        raise HTTPException(404, detail={"code": "denuncia_prova_sem_linha"})
    nome = str(prova.dados.get("arquivo") or prova.dados.get("nome_original") or "")
    ext = os.path.splitext(nome)[1].lower()
    if not _EXT_OK.match(ext):
        ext = ".bin"
    pasta = _pasta_provas()
    h = hashlib.sha256()
    fd, tmp = tempfile.mkstemp(dir=pasta, prefix=f".{prova_id}-")
    try:
        with os.fdopen(fd, "wb") as f:
            async for chunk in request.stream():
                h.update(chunk)
                f.write(chunk)
        digest = h.hexdigest()
        if prova.sha256 and prova.sha256.lower() != digest:
            raise HTTPException(
                409, detail={"code": "denuncia_prova_sha256_diferente", "recebido": digest}
            )
        destino = pasta / f"{prova_id}{ext}"
        os.replace(tmp, destino)
        tmp = ""
    finally:
        if tmp:
            Path(tmp).unlink(missing_ok=True)
    prova.arquivo_local = f"{PASTA_PROVAS}/{prova_id}{ext}"
    prova.arquivo_pedido_em = None
    apagados = _caber_no_teto(manter=destino)
    if apagados:
        await session.execute(
            update(DenunciaProva).where(DenunciaProva.id.in_(apagados)).values(arquivo_local=None)
        )
    await session.commit()
    return {"ok": True, "sha256": digest}


def _caber_no_teto(manter: Path) -> list[int]:
    """Apaga do disco as provas abertas há mais tempo (mtime: chegada ou
    última abertura) até o total caber em `TETO_PROVAS_BYTES`. Devolve os ids
    apagados — clicando de novo, o mini manda outra vez."""
    arquivos = [f for f in _pasta_provas().iterdir() if f.is_file() and not f.name.startswith(".")]
    total = sum(f.stat().st_size for f in arquivos)
    apagados: list[int] = []
    for f in sorted(arquivos, key=lambda f: f.stat().st_mtime):
        if total <= TETO_PROVAS_BYTES:
            break
        if f == manter:
            continue
        total -= f.stat().st_size
        f.unlink(missing_ok=True)
        if f.stem.isdigit():
            apagados.append(int(f.stem))
    return apagados


# ─────────────────────────────────────────────────────────────── telas


def _prova_resumo(p: DenunciaProva) -> dict:
    d = p.dados or {}
    nome = d.get("nome_original") or os.path.basename(str(d.get("arquivo") or "")) or f"prova {p.id}"
    tipo_mime = mimetypes.guess_type(nome)[0] or mimetypes.guess_type(str(d.get("arquivo") or ""))[0]
    return {
        "id": p.id,
        "anuncio_id": p.anuncio_id,
        "caso_id": p.caso_id,
        "denuncia_id": p.denuncia_id,
        "tipo": p.tipo,
        "nome": nome,
        "mime": tipo_mime,
        "tamanho": p.tamanho,
        "enviado_em": d.get("enviado_em"),
        "enviado_por": d.get("enviado_por"),
        "obs": d.get("obs"),
        # 30/09: as provas ficam no MEGA (conta sac@makisa), não no disco do
        # DaVinci — a tela mostra a pasta e copia o caminho.
        "mega_caminho": d.get("mega_caminho"),
        "mega_em": d.get("mega_em"),
        "tem_arquivo": p.arquivo_local is not None,
    }


@router.get("/resumo")
async def resumo(
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(_ver)],
) -> dict:
    ultimo = (
        await session.execute(
            select(func.max(DenunciaRemetente.ultimo_envio_em)).where(
                DenunciaRemetente.revoked_at.is_(None)
            )
        )
    ).scalar()
    contagens = {}
    for nome, m in (
        ("anuncios", DenunciaAnuncio),
        ("denuncias", DenunciaDenuncia),
        ("casos", DenunciaCaso),
        ("provas", DenunciaProva),
    ):
        contagens[nome] = (await session.execute(select(func.count()).select_from(m))).scalar() or 0
    # prova que o sistema do mini ainda não conseguiu pôr no MEGA (login do
    # MEGA caiu, sem internet…) — a tela avisa quando passa de zero.
    contagens["provas_fora_do_mega"] = (
        await session.execute(
            select(func.count())
            .select_from(DenunciaProva)
            .where(DenunciaProva.dados["mega_em"].astext.is_(None))
        )
    ).scalar() or 0
    return {"ultimo_envio_em": ultimo, **contagens}


@router.get("/robo")
async def robo(
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(_ver)],
) -> dict:
    """Aba Robô: em que passo a rodada está, o que cada frente faz agora e o
    que precisa de alguém (regras em `services/denuncia_robo.py`)."""
    st = (
        await session.execute(
            select(DenunciaRoboStatus).order_by(DenunciaRoboStatus.recebido_em.desc()).limit(1)
        )
    ).scalar_one_or_none()
    tratadas = set(
        (
            await session.execute(
                select(DenunciaRoboTratada.chave).where(
                    DenunciaRoboTratada.tratada_em >= datetime.now(UTC) - TRATADA_VALE
                )
            )
        ).scalars().all()
    )
    painel = montar_painel(st.dados if st else None, st.recebido_em if st else None,
                           datetime.now(UTC), tratadas=tratadas, agenda=await _agenda(session))
    comandos = (
        await session.execute(
            select(DenunciaRoboComando).order_by(DenunciaRoboComando.id.desc()).limit(30)
        )
    ).scalars().all()
    painel["comandos"] = [
        {"id": c.id, "tipo": c.tipo, "dados": c.dados, "pedido_por": c.pedido_por,
         "pedido_em": c.pedido_em, "entregue_em": c.entregue_em, "ok": c.ok,
         "resultado": c.resultado,
         "caducou": c.entregue_em is None and datetime.now(UTC) - c.pedido_em > COMANDO_VALE}
        for c in comandos
    ]
    # 05/10: relatório do dia fechado e não lido = linha nas Ocorrências
    painel["relatorios"] = await relatorio_dia.pendentes(session, datetime.now(UTC))
    return painel


async def _comando(session: AsyncSession, u: User, tipo: str, dados: dict) -> dict:
    c = DenunciaRoboComando(tipo=tipo, dados=dados, pedido_por=u.name or u.email)
    session.add(c)
    await session.flush()
    cid = c.id
    await session.commit()
    logger.info("denuncia_robo_comando", tipo=tipo, dados=dados, por=u.email)
    return {"id": cid}


@router.post("/robo/automatico")
async def robo_automatico(
    session: Annotated[AsyncSession, Depends(get_session)],
    u: Annotated[User, Depends(_editar)],
    corpo: Annotated[dict, Body()],
) -> dict:
    """Liga/desliga a rotina automática do robô (despertador das 06/12/18h e
    o ciclo de e-mails de 3 em 3 h). Desligada = só roda o passo pedido."""
    if not isinstance(corpo.get("ligado"), bool):
        raise HTTPException(422, detail={"code": "denuncia_robo_ligado_invalido"})
    return await _comando(session, u, "automatico", {"ligado": corpo["ligado"]})


@router.post("/robo/ocorrencias/tratar")
async def robo_tratar(
    session: Annotated[AsyncSession, Depends(get_session)],
    u: Annotated[User, Depends(_editar)],
    corpo: Annotated[dict, Body()],
) -> dict:
    """"Tratado" numa ocorrência do robô: some da lista. Se o problema tem
    chave própria no robô, o mini também grava a resolução lá."""
    chave = corpo.get("chave")
    if not isinstance(chave, str) or not chave.startswith(("prob:", "agora:")) or len(chave) > 300:
        raise HTTPException(422, detail={"code": "denuncia_ocorrencia_invalida"})
    stmt = pg_insert(DenunciaRoboTratada).values(
        chave=chave, titulo=str(corpo.get("titulo") or "")[:300], tratada_por=u.name or u.email,
        tratada_em=datetime.now(UTC),
    )
    await session.execute(stmt.on_conflict_do_update(
        index_elements=["chave"],
        set_={"tratada_por": stmt.excluded.tratada_por, "tratada_em": stmt.excluded.tratada_em},
    ))
    robo_chave = corpo.get("robo_chave")
    if isinstance(robo_chave, str) and robo_chave and chave == f"prob:{robo_chave}":
        session.add(DenunciaRoboComando(
            tipo="resolver", dados={"chave": robo_chave}, pedido_por=u.name or u.email
        ))
    await session.commit()
    return {"ok": True}


@router.post("/robo/passo")
async def robo_passo(
    session: Annotated[AsyncSession, Depends(get_session)],
    u: Annotated[User, Depends(_editar)],
    corpo: Annotated[dict, Body()],
) -> dict:
    """Pede ao robô um passo da rodada agora (o mesmo que o pedir_tarefa.py
    --retomar faz no mini)."""
    acao = corpo.get("acao")
    if acao not in PASSOS:
        raise HTTPException(422, detail={"code": "denuncia_robo_passo_invalido", "acao": acao})
    return await _comando(session, u, "passo", {"acao": acao})


@router.put("/robo/agenda/{acao}")
async def robo_agenda(
    acao: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    u: Annotated[User, Depends(_editar)],
    corpo: Annotated[dict, Body()],
) -> dict:
    """02/10 (Vinicius): liga/desliga um passo e/ou troca os horários dele. O despertador do mini
    passa a seguir na hora (comando "agenda"; e o mini relê a agenda a cada minuto)."""
    if acao not in PASSOS:
        raise HTTPException(422, detail={"code": "denuncia_robo_passo_invalido", "acao": acao})
    if "ligado" in corpo and not isinstance(corpo["ligado"], bool):
        raise HTTPException(422, detail={"code": "denuncia_robo_ligado_invalido"})
    horarios = None
    if "horarios" in corpo:
        horarios = normalizar_horarios(corpo["horarios"])
        if horarios is None:
            raise HTTPException(422, detail={"code": "denuncia_robo_horario_invalido"})
    if "ligado" not in corpo and horarios is None:
        raise HTTPException(422, detail={"code": "denuncia_robo_agenda_vazia"})
    row = await session.get(DenunciaRoboAgenda, acao)
    if row is None:
        row = DenunciaRoboAgenda(acao=acao, ligado=False, horarios=[])
        session.add(row)
    if "ligado" in corpo:
        row.ligado = corpo["ligado"]
    if horarios is not None:
        row.horarios = horarios
    row.atualizado_por = u.name or u.email
    await session.flush()
    dados = {"acao": acao, "ligado": row.ligado, "horarios": list(row.horarios or [])}
    await _comando(session, u, "agenda", dados)   # commita a linha junto
    return dados


# ───────────────────── relatório do dia (05/10/2026) — regras em services/denuncia_relatorio.py


def _dia_valido(dia: date) -> date:
    hoje = datetime.now(UTC).astimezone(relatorio_dia.FUSO).date()
    if dia > hoje or dia < date(2026, 9, 1):
        raise HTTPException(404, detail={"code": "denuncia_relatorio_dia_invalido"})
    return dia


@router.get("/relatorios")
async def relatorios(
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(_ver)],
    limite: Annotated[int, Query(ge=1, le=120)] = 60,
) -> dict:
    """Os relatórios guardados (do mais novo pro mais velho), com os números da manchete. Hoje
    não entra (ainda em andamento: abre por `/relatorios/{hoje}`)."""
    rows = (await session.execute(
        select(DenunciaRelatorio).where(DenunciaRelatorio.fechado_em.is_not(None))
        .order_by(DenunciaRelatorio.dia.desc()).limit(limite)
    )).scalars().all()
    return {
        "hoje": datetime.now(UTC).astimezone(relatorio_dia.FUSO).date().isoformat(),
        "dias": [{"dia": r.dia.isoformat(), "lido_em": r.lido_em, "lido_por": r.lido_por,
                  **relatorio_dia.manchete(r.numeros)} for r in rows],
    }


@router.get("/relatorios/{dia}")
async def relatorio(
    dia: date,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(_ver)],
) -> dict:
    """O relatório de um dia: hoje ao vivo; dia que passou congelado (congela na 1ª vez)."""
    rel = await relatorio_dia.relatorio(session, _dia_valido(dia), datetime.now(UTC))
    await session.commit()
    return rel


@router.get("/relatorios/{dia}/excel")
async def relatorio_excel(
    dia: date,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(_ver)],
) -> StreamingResponse:
    """Vinicius, 05/10: "esse relatório tem que sair em Excel" — abas Resumo, Anúncios novos,
    Denúncias nas lojas, Anatel, Respostas, Saíram do ar e Robô (passos + ocorrências)."""
    rel = await relatorio_dia.relatorio(session, _dia_valido(dia), datetime.now(UTC))
    await session.commit()
    return StreamingResponse(
        relatorio_dia.excel(rel),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="robo-denuncia-{dia.isoformat()}.xlsx"'
        },
    )


@router.get("/relatorios/{dia}/excel/link")
async def relatorio_excel_link(
    dia: date,
    session: Annotated[AsyncSession, Depends(get_session)],
    t: str = "",
) -> StreamingResponse:
    """06/10 (Vinicius): o Excel do link que vai no Threema — baixa SEM login (o Roma não tem),
    só com o token assinado daquele dia, que vale 7 dias (services/denuncia_relatorio_threema)."""
    from app.services.denuncia_relatorio_threema import confere_excel

    agora = datetime.now(UTC)
    if not confere_excel(dia, t, agora):
        raise HTTPException(403, detail={"code": "denuncia_relatorio_link_invalido"})
    rel = await relatorio_dia.relatorio(session, _dia_valido(dia), agora)
    await session.commit()
    return StreamingResponse(
        relatorio_dia.excel(rel),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="robo-denuncia-{dia.isoformat()}.xlsx"'
        },
    )


@router.post("/relatorios/{dia}/lido")
async def relatorio_lido(
    dia: date,
    session: Annotated[AsyncSession, Depends(get_session)],
    u: Annotated[User, Depends(_editar)],
) -> dict:
    """"Lido": o relatório sai das Ocorrências (continua na lista dos anteriores)."""
    row = await session.get(DenunciaRelatorio, dia)
    if row is None or row.fechado_em is None:
        raise HTTPException(404, detail={"code": "denuncia_relatorio_nao_fechado"})
    row.lido_em = datetime.now(UTC)
    row.lido_por = u.name or u.email
    await session.commit()
    return {"ok": True}


_ORDEM_ANUNCIOS = {
    "vendas": (DenunciaAnuncio.vendas.desc(), DenunciaAnuncio.id),
    "novos": (DenunciaAnuncio.visto_primeiro.desc().nulls_last(), DenunciaAnuncio.id),
    "loja": (DenunciaAnuncio.loja, DenunciaAnuncio.vendas.desc()),
    "grupo": (DenunciaAnuncio.grupo, DenunciaAnuncio.vendas.desc()),
}


# "Por loja" (01/10): loja sem shop_id no anúncio vem com este valor no filtro.
SEM_LOJA = "_sem"


def _filtro_anuncios(
    q: str | None, marketplace: str | None, grupo: str | None, situacao: str | None,
    loja: str | None, den: str | None, propria: str,
) -> tuple[list, Any]:
    """Recorte da aba Anúncios — o mesmo nas visões "por anúncio" e "por loja"."""
    A = DenunciaAnuncio
    nden = (
        select(func.count())
        .select_from(DenunciaDenuncia)
        .where(DenunciaDenuncia.anuncio_id == A.id)
        .correlate(A)
        .scalar_subquery()
    )
    conds = []
    if propria in ("0", "1"):
        conds.append(A.propria == int(propria))
    if grupo:
        conds.append(A.grupo == grupo)
    else:
        conds.append(func.coalesce(A.grupo, "").notin_(GRUPOS_ESCONDIDOS))
    if situacao:
        conds.append(A.situacao == situacao)
    if marketplace:
        conds.append(A.marketplace == marketplace)
    if loja == SEM_LOJA:
        conds.append(A.shop_id.is_(None))
    elif loja:
        conds.append(A.shop_id == loja)
    if den == "com":
        conds.append(nden > 0)
    elif den == "sem":
        conds.append(nden == 0)
    if q and q.strip():
        t = f"%{q.strip()}%"
        conds.append(
            or_(
                A.id.ilike(t),
                A.loja.ilike(t),
                A.titulo.ilike(t),
                A.shop_id.ilike(t),
                A.dados["hom"].astext.ilike(t),
                A.dados["inmetro"].astext.ilike(t),
            )
        )
    return conds, nden


async def _numeros_e_opcoes(session: AsyncSession, conds: list, nden: Any, total: int) -> dict:
    """Números do topo e opções dos filtros, sobre o mesmo recorte (sem limite)."""
    A = DenunciaAnuncio
    base = select(A.situacao, func.count()).where(*conds).group_by(A.situacao)
    por_situacao = {k or "": v for k, v in (await session.execute(base)).all()}
    com_den = (
        await session.execute(select(func.count()).select_from(A).where(*conds, nden > 0))
    ).scalar() or 0
    marketplaces = (
        await session.execute(select(A.marketplace).distinct().order_by(A.marketplace))
    ).scalars().all()
    grupos = (
        await session.execute(select(A.grupo).distinct().order_by(A.grupo))
    ).scalars().all()
    return {
        "numeros": {
            "total": total,
            "ativos": por_situacao.get("ativo", 0),
            "fora_do_ar": por_situacao.get("fora do ar", 0),
            "com_denuncia": com_den,
        },
        "opcoes": {
            "marketplaces": [m for m in marketplaces if m],
            "grupos": [g for g in grupos if g],
        },
    }


@router.get("/anuncios")
async def listar_anuncios(
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(_ver)],
    q: str | None = None,
    marketplace: str | None = None,
    grupo: str | None = None,
    situacao: str | None = None,
    loja: str | None = None,
    den: Annotated[str | None, Query(pattern="^(com|sem)$")] = None,
    propria: Annotated[str, Query(pattern="^(0|1|todas)$")] = "0",
    ordem: str = "vendas",
    limite: Annotated[int, Query(ge=1, le=1000)] = 200,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict:
    A = DenunciaAnuncio
    conds, nden = _filtro_anuncios(q, marketplace, grupo, situacao, loja, den, propria)
    nprov = (
        select(func.count())
        .select_from(DenunciaProva)
        .where(DenunciaProva.anuncio_id == A.id)
        .correlate(A)
        .scalar_subquery()
    )
    caso = (
        select(DenunciaCaso.codigo)
        .where(DenunciaCaso.anuncio_id == A.id)
        .correlate(A)
        .limit(1)
        .scalar_subquery()
    )
    total = (await session.execute(select(func.count()).select_from(A).where(*conds))).scalar() or 0
    rows = (
        await session.execute(
            select(A, nden.label("nden"), nprov.label("nprov"), caso.label("caso"))
            .where(*conds)
            .order_by(*_ORDEM_ANUNCIOS.get(ordem, _ORDEM_ANUNCIOS["vendas"]))
            .limit(limite)
            .offset(offset)
        )
    ).all()
    itens = []
    for a, n_den, n_prov, cod in rows:
        d = a.dados or {}
        itens.append(
            {
                "id": a.id,
                "marketplace": a.marketplace,
                "shop_id": a.shop_id,
                "loja": a.loja,
                "titulo": a.titulo,
                "url": d.get("url"),
                "hom": d.get("hom"),
                "inmetro": d.get("inmetro"),
                "certificacao": d.get("certificacao"),
                "grupo": a.grupo,
                "escopo": a.escopo,
                "vendas": a.vendas,
                "situacao": a.situacao,
                "propria": a.propria,
                "marca_uranyx": d.get("marca_uranyx"),
                "a_conferir": d.get("a_conferir"),
                "visto_primeiro": a.visto_primeiro,
                "visto_ultimo": d.get("visto_ultimo"),
                "saiu_em": d.get("saiu_em"),
                "nden": n_den,
                "nprov": n_prov,
                "caso": cod,
            }
        )
    return {"total": total, "itens": itens, **await _numeros_e_opcoes(session, conds, nden, total)}


_ORDEM_LOJAS = {"vendas", "anuncios", "denuncias", "nome"}


@router.get("/anuncios/lojas")
async def anuncios_por_loja(
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(_ver)],
    q: str | None = None,
    marketplace: str | None = None,
    grupo: str | None = None,
    situacao: str | None = None,
    den: Annotated[str | None, Query(pattern="^(com|sem)$")] = None,
    propria: Annotated[str, Query(pattern="^(0|1|todas)$")] = "0",
    ordem: str = "vendas",
    limite: Annotated[int, Query(ge=1, le=2000)] = 500,
) -> dict:
    """Vinicius, 01/10: "por loja — essa loja quantos anúncios tem, a soma das
    vendas". Os anúncios do mesmo recorte da lista, somados por marketplace +
    loja; abrir a loja = GET /anuncios?loja=<shop_id>."""
    A = DenunciaAnuncio
    conds, nden = _filtro_anuncios(q, marketplace, grupo, situacao, None, den, propria)
    dens = (
        select(DenunciaDenuncia.anuncio_id.label("aid"), func.count().label("n"))
        .group_by(DenunciaDenuncia.anuncio_id)
        .subquery()
    )

    def conta(cond: Any) -> Any:
        return func.sum(case((cond, 1), else_=0))

    n_den = func.coalesce(func.sum(dens.c.n), 0)
    vendas = func.coalesce(func.sum(A.vendas), 0)
    n_anuncios = func.count()
    ordenar = {
        "vendas": (vendas.desc(), n_anuncios.desc()),
        "anuncios": (n_anuncios.desc(), vendas.desc()),
        "denuncias": (n_den.desc(), vendas.desc()),
        "nome": (func.max(A.loja),),
    }[ordem if ordem in _ORDEM_LOJAS else "vendas"]
    q_lojas = (
        select(
            A.marketplace,
            A.shop_id,
            func.max(A.loja).label("loja"),
            n_anuncios.label("anuncios"),
            conta(A.situacao == "ativo").label("no_ar"),
            conta(A.situacao == "fora do ar").label("fora_do_ar"),
            vendas.label("vendas"),
            conta(A.grupo == "GRUPO 1").label("nosso"),
            conta(A.grupo == "GRUPO 2").label("diversos"),
            n_den.label("denuncias"),
            conta(dens.c.n > 0).label("com_denuncia"),
            func.max(A.visto_primeiro).label("ultimo_achado"),
        )
        .outerjoin(dens, dens.c.aid == A.id)
        .where(*conds)
        .group_by(A.marketplace, A.shop_id)
    )
    total_lojas = (
        await session.execute(select(func.count()).select_from(q_lojas.subquery()))
    ).scalar() or 0
    rows = (await session.execute(q_lojas.order_by(*ordenar).limit(limite))).all()
    itens = [
        {
            "marketplace": r.marketplace,
            "shop_id": r.shop_id,
            "chave": r.shop_id or SEM_LOJA,
            "loja": r.loja,
            "anuncios": r.anuncios,
            "no_ar": r.no_ar,
            "fora_do_ar": r.fora_do_ar,
            "vendas": int(r.vendas or 0),
            "nosso": r.nosso,
            "diversos": r.diversos,
            "outros": r.anuncios - r.nosso - r.diversos,
            "denuncias": int(r.denuncias or 0),
            "com_denuncia": r.com_denuncia,
            "ultimo_achado": r.ultimo_achado,
        }
        for r in rows
    ]
    total = (await session.execute(select(func.count()).select_from(A).where(*conds))).scalar() or 0
    return {
        "total": total_lojas,
        "itens": itens,
        **await _numeros_e_opcoes(session, conds, nden, total),
    }


@router.get("/anuncios/{anuncio_id}")
async def ver_anuncio(
    anuncio_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(_ver)],
) -> dict:
    a = (
        await session.execute(select(DenunciaAnuncio).where(DenunciaAnuncio.id == anuncio_id))
    ).scalar_one_or_none()
    if a is None:
        raise HTTPException(404, detail={"code": "denuncia_anuncio_nao_encontrado"})
    loja = None
    if a.shop_id:
        loja = (
            await session.execute(select(DenunciaLoja).where(DenunciaLoja.shop_id == a.shop_id))
        ).scalar_one_or_none()
    dens = (
        await session.execute(
            select(DenunciaDenuncia)
            .where(DenunciaDenuncia.anuncio_id == anuncio_id)
            .order_by(DenunciaDenuncia.data.desc(), DenunciaDenuncia.id.desc())
        )
    ).scalars().all()
    provas = (
        await session.execute(
            select(DenunciaProva)
            .where(DenunciaProva.anuncio_id == anuncio_id)
            .order_by(DenunciaProva.id.desc())
        )
    ).scalars().all()
    verifs = (
        await session.execute(
            select(DenunciaVerificacao)
            .where(DenunciaVerificacao.anuncio_id == anuncio_id)
            .order_by(DenunciaVerificacao.id.desc())
            .limit(60)
        )
    ).scalars().all()
    casos = _cobertura(await _casos_ativos(session))(a)
    compras = (
        await session.execute(
            select(DenunciaCompra).where(DenunciaCompra.anuncio_id == anuncio_id)
        )
    ).scalars().all()
    # 01/10: a ficha mostra tudo junto — o status "na loja"/"na Anatel" e, em cada denúncia com
    # protocolo (a petição do SEI é por loja), os outros anúncios que ela cobre
    st = (await _status_dos_anuncios(session, [a]))[0]
    A = DenunciaAnuncio
    chaves = {(d.canal, d.protocolo) for d in dens if d.protocolo}
    junto: dict[str, list[dict]] = defaultdict(list)
    if chaves:
        rows = (
            await session.execute(
                select(
                    DenunciaDenuncia.canal, DenunciaDenuncia.protocolo,
                    A.id, A.loja, A.titulo, A.situacao,
                )
                .join(A, A.id == DenunciaDenuncia.anuncio_id)
                .where(
                    DenunciaDenuncia.protocolo.in_([p for _, p in chaves]),
                    DenunciaDenuncia.anuncio_id != anuncio_id,
                )
            )
        ).all()
        for canal, proto, aid, lj, tit, sit in rows:
            if (canal, proto) in chaves:
                junto[f"{canal}|{proto}"].append(
                    {"id": aid, "loja": lj, "titulo": tit, "situacao": sit}
                )
    return {
        "anuncio": a.dados,
        "loja": loja.dados if loja else None,
        "status": {"na_loja": st["loja_st"], "na_anatel": st["anatel_st"]},
        "junto": junto,
        "denuncias": [{**(d.dados or {}), "id": d.id} for d in dens],
        "provas": [_prova_resumo(p) for p in provas],
        "verificacoes": [v.dados for v in verifs],
        "casos": [c.dados for c in casos],
        "compras": [c.dados for c in compras],
    }


def _den_dict(d: DenunciaDenuncia) -> dict:
    return {**(d.dados or {}), "id": d.id, "anuncio_id": d.anuncio_id, "canal": d.canal,
            "protocolo": d.protocolo, "data": d.data, "situacao": d.situacao,
            "resultado": d.resultado, "tipo": d.tipo, "prazo": d.prazo}


def _anuncio_base(a: DenunciaAnuncio) -> dict:
    d = a.dados or {}
    return {
        "id": a.id, "marketplace": a.marketplace, "shop_id": a.shop_id, "loja": a.loja,
        "titulo": a.titulo, "url": d.get("url"), "hom": d.get("hom"), "inmetro": d.get("inmetro"),
        "grupo": a.grupo, "vendas": a.vendas, "situacao": a.situacao, "propria": a.propria,
        "visto_primeiro": a.visto_primeiro, "saiu_em": d.get("saiu_em"),
    }


# ── caso por loja e lixeira (01/10/2026) ──────────────────────────────────────────────────────
# Vinicius: "o caso vai ser por loja informando todos os anúncios da loja" e "faz um botão excluir
# caso… ele estorna tudo e a loja volta a ficar zerada". Quem cria/exclui é o sistema do mini (o
# DaVinci só pede, pelo mesmo canal dos botões do Robô); até a cópia trazer o resultado, a tela já
# mostra "criando…" / esconde o caso. Excluído no mini nunca é apagado: status "Excluído".
CASO_EXCLUIDO = "Excluído"
# pedido entregue ao mini, mas a cópia (a cada 5 min — o mini também manda na hora) ainda não chegou
PEDIDO_CASO_VALE = timedelta(minutes=15)
FALHA_CASO_VALE = timedelta(hours=24)


def _anuncios_do_caso(k: DenunciaCaso) -> list[str]:
    """O anúncio principal e, no caso por loja, a lista que o mini guardou (JSON em `anuncios`)."""
    bruto = (k.dados or {}).get("anuncios")
    try:
        lista = json.loads(bruto) if isinstance(bruto, str) else (bruto or [])
    except ValueError:
        lista = []
    ids = [k.anuncio_id] if k.anuncio_id else []
    for x in lista if isinstance(lista, list) else []:
        if x and str(x) not in ids:
            ids.append(str(x))
    return ids


def _loja_do_caso(k: DenunciaCaso) -> tuple[str | None, str | None]:
    d = k.dados or {}
    return (_txt(d.get("marketplace")), _txt(d.get("shop_id")))


async def _pedidos_de_caso(session: AsyncSession) -> dict:
    """criar/excluir caso pedidos ao mini que a cópia ainda não mostra, e os que deram errado."""
    agora = datetime.now(UTC)
    rows = (
        await session.execute(
            select(DenunciaRoboComando)
            .where(
                DenunciaRoboComando.tipo.in_(("criar_caso", "excluir_caso")),
                DenunciaRoboComando.pedido_em >= agora - FALHA_CASO_VALE,
            )
            .order_by(DenunciaRoboComando.id)
        )
    ).scalars().all()
    # o caso novo já chegou na cópia (linha da loja recebida depois do pedido)? sai do "criando…"
    chegou: dict[str, datetime] = {}
    if any(c.tipo == "criar_caso" for c in rows):
        for k in (await session.execute(select(DenunciaCaso))).scalars():
            shop = _loja_do_caso(k)[1]
            if shop and (shop not in chegou or k.recebido_em > chegou[shop]):
                chegou[shop] = k.recebido_em
    criando: set[tuple[str | None, str]] = set()
    excluindo: set[int] = set()
    falhas: list[dict] = []
    for c in rows:
        d = c.dados or {}
        no_ar = (c.entregue_em is None and agora - c.pedido_em <= COMANDO_VALE) or (
            c.ok and c.entregue_em is not None and agora - c.entregue_em <= PEDIDO_CASO_VALE
        )
        if no_ar and c.tipo == "criar_caso" and c.entregue_em is not None:
            veio = chegou.get(str(d.get("shop_id") or ""))
            no_ar = not (veio and veio > c.pedido_em)
        if no_ar:
            if c.tipo == "criar_caso" and d.get("shop_id"):
                criando.add((d.get("marketplace"), str(d["shop_id"])))
            elif c.tipo == "excluir_caso" and d.get("caso_id") is not None:
                excluindo.add(int(d["caso_id"]))
            continue
        if c.entregue_em is None or c.ok is False:
            o_que = (
                f"criar o caso da loja {d.get('loja') or d.get('shop_id')}"
                if c.tipo == "criar_caso" else f"excluir o {d.get('codigo') or 'caso'}"
            )
            falhas.append({
                "id": c.id, "tipo": c.tipo, "texto": o_que, "pedido_em": c.pedido_em,
                "por": c.pedido_por,
                "resultado": c.resultado if c.entregue_em
                else "o robô do mini não pegou o pedido em 1 h (Mac desligado?)",
            })
    return {"criando": criando, "excluindo": excluindo, "falhas": falhas}


async def _casos_ativos(session: AsyncSession, pedidos: dict | None = None) -> list[DenunciaCaso]:
    """Casos que valem nas telas: sem os excluídos (e os que estão sendo excluídos agora)."""
    pedidos = pedidos if pedidos is not None else await _pedidos_de_caso(session)
    casos = (await session.execute(select(DenunciaCaso).order_by(DenunciaCaso.id))).scalars().all()
    return [k for k in casos if k.status != CASO_EXCLUIDO and k.id not in pedidos["excluindo"]]


def _cobertura(casos: list[DenunciaCaso]) -> Any:
    """Índice "casos de um anúncio": ele é o principal ou está na lista do caso, ou o caso é da
    loja dele (o caso por loja cobre também anúncio achado depois). Devolve f(anúncio) → casos."""
    por_anuncio: dict[str, list[DenunciaCaso]] = defaultdict(list)
    por_loja: dict[str, list[tuple[str | None, DenunciaCaso]]] = defaultdict(list)
    for k in casos:
        for i in _anuncios_do_caso(k):
            por_anuncio[i].append(k)
        mp, shop = _loja_do_caso(k)
        if shop:
            por_loja[shop].append((mp, k))

    def de(a: Any) -> list[DenunciaCaso]:
        out = {k.id: k for k in por_anuncio.get(a.id, [])}
        for mp, k in por_loja.get(a.shop_id or "", []):
            if not mp or not a.marketplace or mp == a.marketplace:
                out[k.id] = k
        return [out[i] for i in sorted(out)]

    return de


async def _status_dos_anuncios(
    session: AsyncSession, anuncios: list[DenunciaAnuncio],
) -> list[dict]:
    """Cada anúncio com o status "na loja" e "na Anatel" (services/denuncia_painel)."""
    ids = [a.id for a in anuncios]
    dens: dict[str, list[dict]] = defaultdict(list)
    com_print: set[str] = set()
    if ids:
        for d in (
            await session.execute(
                select(DenunciaDenuncia).where(DenunciaDenuncia.anuncio_id.in_(ids))
            )
        ).scalars():
            dens[d.anuncio_id].append(_den_dict(d))
        com_print = set(
            (
                await session.execute(
                    select(DenunciaProva.anuncio_id)
                    .where(
                        DenunciaProva.anuncio_id.in_(ids), DenunciaProva.tipo == "Captura no ato",
                    )
                    .distinct()
                )
            ).scalars()
        )
    # 01/10 (Vinicius): coluna "Caso" — o número do caso e um botão que leva direto a ele. O caso
    # por loja cobre todos os anúncios dela; "criando…" enquanto o mini não devolve o caso novo.
    pedidos = await _pedidos_de_caso(session)
    casos_de = _cobertura(await _casos_ativos(session, pedidos) if ids else [])
    out = []
    for a in anuncios:
        base = _anuncio_base(a)
        base["casos"] = [{"id": k.id, "codigo": k.codigo, "status": k.status} for k in casos_de(a)]
        base["caso_pendente"] = not base["casos"] and bool(a.shop_id) and (
            (a.marketplace, a.shop_id) in pedidos["criando"] or (None, a.shop_id) in pedidos["criando"]
        )
        ds = dens.get(a.id, [])
        lst = painel.status_loja(ds, a.grupo)
        base["loja_st"] = painel.rotular(lst, painel.LOJA)
        ana = painel.status_anatel(base, ds, lst, a.id in com_print)
        base["anatel_st"] = painel.rotular(ana, painel.ANATEL)
        base["ultima_denuncia"] = max((x.get("data") or "" for x in ds), default="") or None
        base["nden"] = len(ds)
        out.append(base)
    return out


@router.get("/painel")
async def painel_anuncios_e_denuncias(
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(_ver)],
    visao: Annotated[str, Query(pattern="^(lojas|anuncios)$")] = "lojas",
    q: str | None = None,
    marketplace: str | None = None,
    grupo: str | None = None,
    situacao: str | None = None,
    loja: str | None = None,
    na_loja: str | None = None,
    na_anatel: str | None = None,
    propria: Annotated[str, Query(pattern="^(0|1|todas)$")] = "0",
    ordem: str = "vendas",
    limite: Annotated[int, Query(ge=1, le=2000)] = 500,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict:
    """Vinicius, 01/10: as abas Anúncios e Denúncias numa só ("são quase as mesmas
    informações") — por loja ou por anúncio, cada um com o que a loja fez com a nossa
    denúncia e onde ele está no caminho da Anatel. na_loja / na_anatel filtram pelas
    chaves de services/denuncia_painel (LOJA, ANATEL)."""
    A = DenunciaAnuncio
    conds, _nden = _filtro_anuncios(q, marketplace, grupo, situacao, loja, None, propria)
    anuncios = (await session.execute(select(A).where(*conds))).scalars().all()
    itens = await _status_dos_anuncios(session, list(anuncios))
    if na_loja in painel.LOJA:
        itens = [a for a in itens if a["loja_st"]["chave"] == na_loja]
    if na_anatel in painel.ANATEL:
        itens = [a for a in itens if a["anatel_st"]["chave"] == na_anatel]
    lojas = painel.somar_lojas(itens)
    marketplaces = (await session.execute(select(A.marketplace).distinct())).scalars().all()
    grupos = (await session.execute(select(A.grupo).distinct())).scalars().all()
    resp: dict[str, Any] = {
        "falhas_caso": (await _pedidos_de_caso(session))["falhas"],
        "numeros": painel.numeros(itens, lojas),
        "opcoes": {
            "marketplaces": sorted(m for m in marketplaces if m),
            "grupos": sorted(g for g in grupos if g),
            "na_loja": [
                {"chave": k, "rotulo": v[0]} for k, v in painel.LOJA.items() if k != "vazio"
            ],
            "na_anatel": [
                {"chave": k, "rotulo": v[0]} for k, v in painel.ANATEL.items() if k != "nada"
            ],
        },
    }
    if visao == "lojas":
        lojas = painel.ordenar_lojas(lojas, ordem)
        return {**resp, "total": len(lojas), "itens": lojas[offset: offset + limite]}
    ordens = {
        "vendas": lambda x: (-(x["vendas"] or 0), x["id"]),
        "recentes": lambda x: (x["visto_primeiro"] or "",),
        "loja": lambda x: ((x["loja"] or "").lower(), -(x["vendas"] or 0)),
    }
    itens.sort(key=ordens.get(ordem, ordens["vendas"]), reverse=(ordem == "recentes"))
    return {**resp, "total": len(itens), "itens": itens[offset: offset + limite]}


def _chave_grupo(d: DenunciaDenuncia) -> tuple:
    # mesma regra do sistema do mini: o mesmo protocolo que cobre vários
    # anúncios é UMA denúncia.
    return (d.canal, d.protocolo) if d.protocolo else ("_id", d.id)


async def _denuncias_agrupadas(session: AsyncSession) -> list[dict]:
    """Todas as denúncias, uma linha por denúncia (mesmo canal + protocolo = uma
    só), com os anúncios — e a loja de cada um — que ela cobre."""
    D, A = DenunciaDenuncia, DenunciaAnuncio
    rows = (
        await session.execute(
            select(D, A.loja, A.titulo, A.situacao, A.shop_id, A.marketplace)
            .outerjoin(A, A.id == D.anuncio_id)
            .order_by(D.data.desc().nulls_last(), D.id.desc())
        )
    ).all()
    grupos: OrderedDict[tuple, dict] = OrderedDict()
    for d, loja, titulo, sit_anuncio, shop_id, mp in rows:
        k = _chave_grupo(d)
        g = grupos.get(k)
        if g is None:
            dd = d.dados or {}
            g = grupos[k] = {
                "id": d.id,
                "ids": [],
                "canal": d.canal,
                "protocolo": d.protocolo,
                "data": d.data,
                "hora": dd.get("hora"),
                "situacao": d.situacao,
                "resultado": d.resultado,
                "resultado_confirmado": dd.get("resultado_confirmado"),
                "prazo": d.prazo,
                "tipo": d.tipo,
                "tentativa": dd.get("tentativa"),
                "motivo": dd.get("motivo"),
                "refazer": dd.get("refazer"),
                "anuncios": [],
            }
        g["ids"].append(d.id)
        g["anuncios"].append(
            {"id": d.anuncio_id, "loja": loja, "titulo": titulo, "situacao": sit_anuncio,
             "shop_id": shop_id, "marketplace": mp}
        )
    return list(grupos.values())


def _resumo_e_filtro(
    todos: list[dict], canal: str | None, situacao: str | None, tipo: str | None, q: str | None,
) -> tuple[dict, list[dict]]:
    resumo: dict[str, dict[str, int]] = {}
    for g in todos:
        r = resumo.setdefault(g["canal"] or "—", {"total": 0})
        r["total"] += 1
        r[g["situacao"] or "—"] = r.get(g["situacao"] or "—", 0) + 1
    filtrados = todos
    if canal:
        filtrados = [g for g in filtrados if g["canal"] == canal]
    if situacao:
        filtrados = [g for g in filtrados if g["situacao"] == situacao]
    if tipo:
        filtrados = [g for g in filtrados if (g["tipo"] or "") == tipo]
    if q and q.strip():
        t = q.strip().lower()
        filtrados = [
            g
            for g in filtrados
            if t in (g["protocolo"] or "").lower()
            or any(
                t in (x["id"] or "").lower() or t in (x["loja"] or "").lower()
                for x in g["anuncios"]
            )
        ]
    return resumo, filtrados


def _opcoes_denuncias(todos: list[dict], resumo: dict) -> dict:
    return {
        "canais": sorted(resumo.keys()),
        "situacoes": sorted({g["situacao"] for g in todos if g["situacao"]}),
    }


@router.get("/denuncias")
async def listar_denuncias(
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(_ver)],
    canal: str | None = None,
    situacao: str | None = None,
    tipo: str | None = None,
    q: str | None = None,
    loja: str | None = None,
    limite: Annotated[int, Query(ge=1, le=2000)] = 500,
) -> dict:
    todos = await _denuncias_agrupadas(session)
    resumo, filtrados = _resumo_e_filtro(todos, canal, situacao, tipo, q)
    if loja:   # abrir a loja na visão "por loja"
        alvo = None if loja == SEM_LOJA else loja
        filtrados = [g for g in filtrados if any(x["shop_id"] == alvo for x in g["anuncios"])]
    return {
        "total": len(filtrados),
        "itens": filtrados[:limite],
        "resumo": resumo,
        "opcoes": _opcoes_denuncias(todos, resumo),
    }


# resultado de uma denúncia → coluna da visão por loja
_RESOLVEU = ("Anúncio removido", "Anúncio ajustado", "Loja suspensa")


def _desfecho(g: dict) -> str:
    if (g.get("resultado") or "") in _RESOLVEU:
        return "removidas"
    if "improcedente" in f"{g.get('resultado') or ''} {g.get('situacao') or ''}".lower():
        return "recusadas"
    return "aguardando"


@router.get("/denuncias/lojas")
async def denuncias_por_loja(
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(_ver)],
    canal: str | None = None,
    situacao: str | None = None,
    tipo: str | None = None,
    q: str | None = None,
    limite: Annotated[int, Query(ge=1, le=2000)] = 500,
) -> dict:
    """Vinicius, 01/10: as denúncias somadas por loja — quantas, o resultado,
    por qual canal e a última. Abrir a loja = GET /denuncias?loja=<shop_id>."""
    todos = await _denuncias_agrupadas(session)
    resumo, filtrados = _resumo_e_filtro(todos, canal, situacao, tipo, q)
    lojas: dict[tuple, dict] = {}
    for g in filtrados:
        quando = f"{g.get('data') or ''} {g.get('hora') or ''}".strip()
        for chave_loja in {(x["marketplace"], x["shop_id"]) for x in g["anuncios"]}:
            mp, shop = chave_loja
            lj = lojas.get(chave_loja)
            if lj is None:
                lj = lojas[chave_loja] = {
                    "marketplace": mp, "shop_id": shop, "chave": shop or SEM_LOJA, "loja": None,
                    "denuncias": 0, "removidas": 0, "recusadas": 0, "aguardando": 0,
                    "canais": {}, "_anuncios": set(), "_no_ar": set(), "ultima": "",
                }
            lj["denuncias"] += 1
            lj[_desfecho(g)] += 1
            lj["canais"][g["canal"] or "—"] = lj["canais"].get(g["canal"] or "—", 0) + 1
            for x in g["anuncios"]:
                if (x["marketplace"], x["shop_id"]) != chave_loja:
                    continue
                lj["loja"] = lj["loja"] or x["loja"]
                lj["_anuncios"].add(x["id"])
                if x["situacao"] == "ativo":
                    lj["_no_ar"].add(x["id"])
            lj["ultima"] = max(lj["ultima"], quando)
    itens = []
    for lj in lojas.values():
        lj["anuncios"] = len(lj.pop("_anuncios"))
        lj["anuncios_no_ar"] = len(lj.pop("_no_ar"))
        lj["ultima"] = lj["ultima"] or None
        itens.append(lj)
    itens.sort(key=lambda x: (-x["denuncias"], x["loja"] or ""))
    return {
        "total": len(itens),
        "itens": itens[:limite],
        "resumo": resumo,
        "opcoes": _opcoes_denuncias(todos, resumo),
    }


@router.get("/denuncias/{denuncia_id}")
async def ver_denuncia(
    denuncia_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(_ver)],
) -> dict:
    D = DenunciaDenuncia
    d = (await session.execute(select(D).where(D.id == denuncia_id))).scalar_one_or_none()
    if d is None:
        raise HTTPException(404, detail={"code": "denuncia_nao_encontrada"})
    if d.protocolo:
        irmas = (
            await session.execute(
                select(D).where(D.canal == d.canal, D.protocolo == d.protocolo).order_by(D.id)
            )
        ).scalars().all()
    else:
        irmas = [d]
    ids = [x.id for x in irmas]
    anuncio_ids = [x.anuncio_id for x in irmas if x.anuncio_id]
    anuncios = (
        await session.execute(select(DenunciaAnuncio).where(DenunciaAnuncio.id.in_(anuncio_ids)))
    ).scalars().all()
    provas = (
        await session.execute(
            select(DenunciaProva)
            .where(DenunciaProva.denuncia_id.in_(ids))
            .order_by(DenunciaProva.id)
        )
    ).scalars().all()
    return {
        "denuncia": d.dados,
        "linhas": [x.dados for x in irmas],
        "anuncios": [
            {
                "id": a.id,
                "loja": a.loja,
                "titulo": a.titulo,
                "situacao": a.situacao,
                "url": (a.dados or {}).get("url"),
            }
            for a in anuncios
        ],
        "provas": [_prova_resumo(p) for p in provas],
    }


# 01/10 (Vinicius: "o caso 2 aparece com jurídico mas não foi enviado"): o status na tela sai do
# que aconteceu — enviado ao advogado = "Com jurídico"; compra entregue = "Produto recebido"; compra
# feita = "Aguardando produto"; sem compra = "Aberto". "Ajuizado" e "Encerrado" (marcados à mão no
# sistema do mini) valem como estão. O status gravado lá vai junto em "status_mini".
def _status_caso(status: str | None, enviado_em: Any, compra: dict | None) -> str:
    if status in ("Ajuizado", "Encerrado"):
        return status
    if enviado_em:
        return "Com jurídico"
    if compra and (compra.get("entregue_em") or str(compra.get("status") or "").lower().startswith("receb")):
        return "Produto recebido"
    if compra:
        return "Aguardando produto"
    return "Aberto"


def _extra_dict(x: DenunciaCasoExtra | None) -> dict:
    campos = ("compra_data", "compra_loja", "compra_pedido", "compra_previsao", "processo_numero",
              "processo_link", "mov_data", "mov_texto", "mov_status", "atualizado_por")
    if x is None:
        return dict.fromkeys(campos)
    out = {k: getattr(x, k) for k in campos}
    for k in ("compra_data", "compra_previsao", "mov_data"):
        out[k] = out[k].isoformat() if out[k] else None
    return out


@router.get("/casos")
async def listar_casos(
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(_ver)],
) -> dict:
    C, A = DenunciaCaso, DenunciaAnuncio
    pedidos = await _pedidos_de_caso(session)
    rows = (
        await session.execute(
            select(C, A.loja, A.titulo, A.marketplace, A.dados, A.vendas, A.shop_id)
            .outerjoin(A, A.id == C.anuncio_id)
            .where(or_(C.status.is_(None), C.status != CASO_EXCLUIDO))
            .order_by(C.id.desc())
        )
    ).all()
    rows = [r for r in rows if r[0].id not in pedidos["excluindo"]]
    # a compra de prova do caso: ligada pelo caso OU pelo anúncio (as de 29/09 vieram só com o
    # anúncio — a tabela mostrava "—" para os casos 004–006); vale a mais recente
    compras_por_caso: dict[int, dict] = {}
    compras_por_anuncio: dict[str, dict] = {}
    for k in (await session.execute(select(DenunciaCompra).order_by(DenunciaCompra.id))).scalars():
        d = {**(k.dados or {}), "status": k.status}
        if k.caso_id is not None:
            compras_por_caso[k.caso_id] = d
        if k.anuncio_id:
            compras_por_anuncio[k.anuncio_id] = d
    extras = {
        x.caso_id: x for x in (await session.execute(select(DenunciaCasoExtra))).scalars()
    }
    # 02/10 (Vinicius: "ver se tá com certificado nosso ou diversos igual tem na aba anúncios"):
    # o grupo de cada anúncio do caso — no caso por loja, quantos de cada
    ids_casos = {i for r in rows for i in _anuncios_do_caso(r[0])}
    grupo_de = dict(
        (await session.execute(select(A.id, A.grupo).where(A.id.in_(ids_casos)))).all()
    ) if ids_casos else {}
    itens = []
    for c, loja, titulo_anuncio, mp, ad, vendas, shop_id in rows:
        grupos = [grupo_de.get(i) for i in _anuncios_do_caso(c)]
        d = c.dados or {}
        ad = ad or {}
        cp = compras_por_caso.get(c.id) or compras_por_anuncio.get(c.anuncio_id or "")
        compra = (
            {k: cp.get(k) for k in ("pedido", "status", "valor_pago", "data", "entregue_em", "comprador")}
            if cp else None
        )
        itens.append(
            {
                "id": c.id,
                "codigo": c.codigo,
                "titulo": d.get("titulo"),
                "status": _status_caso(c.status, d.get("juridico_enviado_em"), compra),
                "status_mini": c.status,
                "anuncio_id": c.anuncio_id,
                "loja": loja,
                "titulo_anuncio": titulo_anuncio,
                "marketplace": mp,
                "aberto_em": d.get("aberto_em"),
                "juridico": d.get("juridico"),
                "juridico_enviado_em": d.get("juridico_enviado_em"),
                "url": ad.get("url"),
                "hom": ad.get("hom"),
                # 02/10: o preço que a varredura leu no anúncio principal (lista de compra)
                "preco": ad.get("preco"),
                "preco_em": ad.get("preco_em"),
                "vendas": vendas,
                "shop_id": shop_id or _loja_do_caso(c)[1],
                "n_anuncios": len(_anuncios_do_caso(c)),
                "por_loja": bool(_loja_do_caso(c)[1]),
                "grupo": ad.get("grupo"),
                "nosso": grupos.count("GRUPO 1"),
                "diversos": grupos.count("GRUPO 2"),
                "compra": compra,
                "extra": _extra_dict(extras.get(c.id)),
            }
        )
    por_status: dict[str, int] = {}
    for i in itens:
        por_status[i["status"] or "—"] = por_status.get(i["status"] or "—", 0) + 1
    return {"total": len(itens), "itens": itens, "por_status": por_status,
            "falhas_caso": pedidos["falhas"]}


@router.post("/casos/criar")
async def criar_casos(
    session: Annotated[AsyncSession, Depends(get_session)],
    u: Annotated[User, Depends(_editar)],
    corpo: Annotated[dict, Body()],
) -> dict:
    """Aba Anúncios e denúncias: "criar" (uma loja) ou "enviar para caso" (as marcadas). Um caso
    por loja com TODOS os anúncios dela (concorrentes) — o mais vendido no ar é o principal. Loja
    que já tem caso (ou pedido em andamento) fica de fora.
    Corpo: {lojas: [{marketplace, shop_id}]}."""
    lojas = corpo.get("lojas")
    if not isinstance(lojas, list) or not lojas or len(lojas) > 200:
        raise HTTPException(422, detail={"code": "denuncia_lojas_invalidas"})
    pedidos = await _pedidos_de_caso(session)
    casos_de = _cobertura(await _casos_ativos(session, pedidos))
    A = DenunciaAnuncio  # noqa: N806 — mesmo apelido das outras consultas daqui
    criados: list[dict] = []
    pulados: list[dict] = []
    vistos: set[tuple] = set()
    for item in lojas:
        if not isinstance(item, dict):
            raise HTTPException(422, detail={"code": "denuncia_lojas_invalidas"})
        shop = _txt(item.get("shop_id"))
        mp = _txt(item.get("marketplace"))
        if not shop:
            pulados.append({"loja": item.get("loja"), "motivo": "sem o código da loja"})
            continue
        if (mp, shop) in vistos:
            continue
        vistos.add((mp, shop))
        conds = [A.shop_id == shop, A.propria == 0]
        if mp:
            conds.append(A.marketplace == mp)
        anuncios = (await session.execute(select(A).where(*conds))).scalars().all()
        nome = next((a.loja for a in anuncios if a.loja), None) or shop
        if not anuncios:
            pulados.append({"loja": nome, "motivo": "nenhum anúncio de concorrente nesta loja"})
            continue
        if (mp, shop) in pedidos["criando"] or (None, shop) in pedidos["criando"]:
            pulados.append({"loja": nome, "motivo": "o caso já está sendo criado"})
            continue
        ja = {k.codigo for a in anuncios for k in casos_de(a)}
        if ja:
            codigos = ", ".join(sorted(c or "?" for c in ja))
            pulados.append({"loja": nome, "motivo": f"já tem caso ({codigos})"})
            continue
        ordem = sorted(anuncios, key=lambda a: (a.situacao != "ativo", -(a.vendas or 0), a.id))
        dados = {"shop_id": shop, "marketplace": mp or ordem[0].marketplace, "loja": nome,
                 "anuncio_ids": [a.id for a in ordem]}
        session.add(
            DenunciaRoboComando(tipo="criar_caso", dados=dados, pedido_por=u.name or u.email)
        )
        criados.append({"loja": nome, "shop_id": shop, "anuncios": len(ordem)})
    await session.commit()
    logger.info("denuncia_criar_casos", criados=len(criados), pulados=len(pulados), por=u.email)
    return {"criados": criados, "pulados": pulados}


@router.post("/casos/{caso_id}/excluir")
async def excluir_caso(
    caso_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    u: Annotated[User, Depends(_editar)],
    corpo: Annotated[dict | None, Body()] = None,
) -> dict:
    """Lixeira da aba Casos: pede ao mini para estornar o caso (lá ele vira "Excluído" — nada é
    apagado) e a loja volta a ficar sem caso. A tela já esconde o caso enquanto o mini faz."""
    c = await session.get(DenunciaCaso, caso_id)
    if c is None:
        raise HTTPException(404, detail={"code": "denuncia_caso_nao_encontrado"})
    if c.status == CASO_EXCLUIDO:
        raise HTTPException(409, detail={"code": "denuncia_caso_ja_excluido"})
    if caso_id in (await _pedidos_de_caso(session))["excluindo"]:
        return {"ok": True, "ja_pedido": True}
    motivo = str((corpo or {}).get("motivo") or "").strip()[:500]
    session.add(DenunciaRoboComando(
        tipo="excluir_caso", dados={"caso_id": caso_id, "codigo": c.codigo, "motivo": motivo},
        pedido_por=u.name or u.email,
    ))
    await session.commit()
    logger.info("denuncia_excluir_caso", caso=c.codigo, por=u.email)
    return {"ok": True}


_CAMPOS_EXTRA_TEXTO = ("compra_loja", "compra_pedido", "processo_numero", "processo_link",
                       "mov_texto", "mov_status")
_CAMPOS_EXTRA_DATA = ("compra_data", "compra_previsao", "mov_data")


@router.put("/casos/{caso_id}/extra")
async def editar_caso_extra(
    caso_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    u: Annotated[User, Depends(_editar)],
    corpo: Annotated[dict, Body()],
) -> dict:
    """Onde comprou / pedido / previsão de entrega e o processo (nº, link do Jusbrasil, última
    movimentação). Só os campos enviados mudam; "" apaga."""
    c = (
        await session.execute(select(DenunciaCaso).where(DenunciaCaso.id == caso_id))
    ).scalar_one_or_none()
    if c is None:
        raise HTTPException(404, detail={"code": "denuncia_caso_nao_encontrado"})
    x = await session.get(DenunciaCasoExtra, caso_id)
    if x is None:
        x = DenunciaCasoExtra(caso_id=caso_id)
        session.add(x)
    for k in _CAMPOS_EXTRA_TEXTO:
        if k in corpo:
            v = str(corpo[k] or "").strip()[:2000]
            setattr(x, k, v or None)
    for k in _CAMPOS_EXTRA_DATA:
        if k in corpo:
            v = str(corpo[k] or "").strip()
            try:
                setattr(x, k, datetime.strptime(v[:10], "%Y-%m-%d").date() if v else None)
            except ValueError:
                raise HTTPException(422, detail={"code": "denuncia_data_invalida", "campo": k}) from None
    link = x.processo_link or ""
    if link and not link.startswith(("https://", "http://")):
        raise HTTPException(422, detail={"code": "denuncia_link_invalido"})
    x.atualizado_por = u.name or u.email
    await session.commit()
    return {"ok": True, "extra": _extra_dict(x)}


@router.get("/casos/{caso_id}")
async def ver_caso(
    caso_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(_ver)],
) -> dict:
    c = (await session.execute(select(DenunciaCaso).where(DenunciaCaso.id == caso_id))).scalar_one_or_none()
    if c is None:
        raise HTTPException(404, detail={"code": "denuncia_caso_nao_encontrado"})
    anuncio = None
    if c.anuncio_id:
        anuncio = (
            await session.execute(select(DenunciaAnuncio).where(DenunciaAnuncio.id == c.anuncio_id))
        ).scalar_one_or_none()
    compras = (
        await session.execute(
            select(DenunciaCompra)
            .where(or_(DenunciaCompra.caso_id == caso_id, DenunciaCompra.anuncio_id == c.anuncio_id))
            .order_by(DenunciaCompra.id)
        )
    ).scalars().all()
    provas = (
        await session.execute(
            select(DenunciaProva)
            .where(or_(DenunciaProva.caso_id == caso_id, DenunciaProva.anuncio_id == c.anuncio_id))
            .order_by(DenunciaProva.id.desc())
        )
    ).scalars().all()
    # caso por loja: a lista de anúncios dela e as denúncias de todos
    excluido = (c.dados or {}).get("excluido_anuncio_id")
    ids = _anuncios_do_caso(c) or ([excluido] if excluido else [])
    dens = []
    if ids:
        dens = (
            await session.execute(
                select(DenunciaDenuncia)
                .where(DenunciaDenuncia.anuncio_id.in_(ids))
                .order_by(DenunciaDenuncia.data.desc(), DenunciaDenuncia.id.desc())
            )
        ).scalars().all()
    do_caso = []
    if len(ids) > 1:
        por_id = {
            a.id: a for a in (
                await session.execute(select(DenunciaAnuncio).where(DenunciaAnuncio.id.in_(ids)))
            ).scalars()
        }
        do_caso = [
            {"id": i, "titulo": por_id[i].titulo, "situacao": por_id[i].situacao,
             "vendas": por_id[i].vendas, "grupo": por_id[i].grupo,
             "url": (por_id[i].dados or {}).get("url"), "preco": (por_id[i].dados or {}).get("preco")}
            for i in ids if i in por_id
        ]
    return {
        "caso": c.dados,
        "anuncios_do_caso": do_caso,
        "anuncio": anuncio.dados if anuncio else None,
        "compras": [x.dados for x in compras],
        "provas": [_prova_resumo(p) for p in provas],
        "denuncias": [d.dados for d in dens],
        "anexos": await _anexos_do_caso(session, caso_id),
        "extra": _extra_dict(await session.get(DenunciaCasoExtra, caso_id)),
        "status_tela": _status_caso(
            c.status, (c.dados or {}).get("juridico_enviado_em"),
            {**(compras[-1].dados or {}), "status": compras[-1].status} if compras else None,
        ),
        "tipos_anexo": [{"chave": k, "nome": v[1]} for k, v in TIPOS_ANEXO.items()],
    }


async def _anexos_do_caso(session: AsyncSession, caso_id: int) -> list[dict]:
    rows = (
        await session.execute(
            select(DenunciaAnexo)
            .where(DenunciaAnexo.caso_id == caso_id)
            .order_by(DenunciaAnexo.id.desc())
        )
    ).scalars().all()
    return [_anexo_resumo(x) for x in rows]


@router.post("/casos/{caso_id}/anexos")
async def anexar_ao_caso(
    caso_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    u: Annotated[User, Depends(_editar)],
    tipo: Annotated[str, Form()],
    obs: Annotated[str, Form()] = "",
    link: Annotated[str, Form()] = "",
    arquivo: Annotated[UploadFile | None, File()] = None,
) -> dict:
    """Vinicius, 01/10: "chegou o produto, onde eu vou colocar as provas?" — anexa aqui; o mini
    busca e entrega ao sistema de lá (que guarda e sobe pro MEGA). Vídeo só como link do MEGA."""
    c = (
        await session.execute(select(DenunciaCaso).where(DenunciaCaso.id == caso_id))
    ).scalar_one_or_none()
    if c is None or not c.anuncio_id:
        raise HTTPException(404, detail={"code": "denuncia_caso_nao_encontrado"})
    if tipo not in TIPOS_ANEXO:
        raise HTTPException(422, detail={"code": "denuncia_anexo_tipo_invalido"})
    link = (link or "").strip()
    if tipo == "video":
        if not re.match(r"^https://mega\.nz/\S+#\S+$", link):
            raise HTTPException(422, detail={"code": "denuncia_anexo_video_so_link_mega"})
        arquivo = None
    elif arquivo is None or not arquivo.filename:
        raise HTTPException(422, detail={"code": "denuncia_anexo_sem_arquivo"})
    x = DenunciaAnexo(
        caso_id=caso_id, anuncio_id=c.anuncio_id, tipo=tipo, link=link or None,
        obs=(obs or "").strip()[:1000] or None, enviado_por=u.name or u.email,
        nome=(arquivo.filename if arquivo else None),
    )
    session.add(x)
    await session.flush()
    if arquivo is not None:
        ext = os.path.splitext(arquivo.filename or "")[1].lower()
        if not _EXT_OK.match(ext):
            ext = ".bin"
        destino = _pasta_anexos() / f"{x.id}{ext}"
        h, total = hashlib.sha256(), 0
        try:
            with open(destino, "wb") as f:
                while chunk := await arquivo.read(1024 * 1024):
                    total += len(chunk)
                    if total > TETO_ANEXO_BYTES:
                        raise HTTPException(413, detail={"code": "denuncia_anexo_grande_demais"})
                    h.update(chunk)
                    f.write(chunk)
        except HTTPException:
            destino.unlink(missing_ok=True)
            raise
        x.arquivo_local, x.tamanho, x.sha256 = f"{PASTA_ANEXOS}/{x.id}{ext}", total, h.hexdigest()
    await session.commit()
    return {"ok": True, "anexos": await _anexos_do_caso(session, caso_id)}


@router.post("/provas/{prova_id}/preparar")
async def preparar_prova(
    prova_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(_ver)],
) -> dict:
    """Clique em "abrir": o arquivo já está aqui → pronto; senão pede ao
    mini e a tela pergunta de novo a cada 2 s até chegar."""
    p = (
        await session.execute(select(DenunciaProva).where(DenunciaProva.id == prova_id))
    ).scalar_one_or_none()
    if p is None:
        raise HTTPException(404, detail={"code": "denuncia_prova_nao_encontrada"})
    agora = datetime.now(UTC)
    if p.arquivo_local and (Path(get_settings().uploads_dir) / p.arquivo_local).exists():
        return {"pronto": True}
    p.arquivo_local = None
    if p.arquivo_pedido_em is None or agora - p.arquivo_pedido_em > PEDIDO_PROVA_VALE:
        p.arquivo_pedido_em = agora
    pedido = p.arquivo_pedido_em
    await session.commit()
    esperando = int((agora - pedido).total_seconds())
    return {"pronto": False, "pedido_em": pedido, "esperando_s": esperando}


@router.get("/provas/{prova_id}/arquivo")
async def baixar_prova(
    prova_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(_ver)],
    baixar: bool = False,
) -> FileResponse:
    p = (
        await session.execute(select(DenunciaProva).where(DenunciaProva.id == prova_id))
    ).scalar_one_or_none()
    if p is None:
        raise HTTPException(404, detail={"code": "denuncia_prova_nao_encontrada"})
    if not p.arquivo_local:
        raise HTTPException(404, detail={"code": "denuncia_prova_sem_arquivo"})
    caminho = Path(get_settings().uploads_dir) / p.arquivo_local
    if not caminho.exists():
        raise HTTPException(404, detail={"code": "denuncia_prova_sumiu_do_disco"})
    # aberta agora: fica por último na fila do teto (_caber_no_teto)
    os.utime(caminho)
    resumo_p = _prova_resumo(p)
    mime = resumo_p["mime"] or "application/octet-stream"
    headers = {"Cache-Control": "private, max-age=3600", "X-Content-Type-Options": "nosniff"}
    # Página salva do marketplace (html/svg/xml) rodaria o script dela na
    # origem do DaVinci, com o cookie de quem abriu: sempre baixa, e com CSP
    # sandbox por garantia. Imagem, PDF e vídeo abrem na aba.
    ativo = not mime.startswith(("image/", "video/", "audio/")) and mime != "application/pdf"
    if ativo or mime == "image/svg+xml":
        baixar = True
        headers["Content-Security-Policy"] = "sandbox"
    return FileResponse(
        caminho,
        media_type=mime,
        filename=resumo_p["nome"],
        content_disposition_type="attachment" if baixar else "inline",
        headers=headers,
    )
