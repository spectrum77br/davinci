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
import mimetypes
import os
import re
import secrets
import tempfile
from collections import OrderedDict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.deps.auth import require_permission
from app.models.denuncia import (
    DenunciaAnuncio,
    DenunciaCaso,
    DenunciaCompra,
    DenunciaDenuncia,
    DenunciaLoja,
    DenunciaProva,
    DenunciaRemetente,
    DenunciaRoboComando,
    DenunciaRoboStatus,
    DenunciaRoboTratada,
    DenunciaVerificacao,
)
from app.models.user import User
from app.services.denuncia_robo import PASSOS, montar_painel

logger = structlog.get_logger()

router = APIRouter(prefix="/api/denuncia", tags=["denuncia"])
sync_router = APIRouter(prefix="/api/denuncia/sync", tags=["denuncia"], include_in_schema=False)

_ver = require_permission("denuncia", "view")
_editar = require_permission("denuncia", "edit")

# Pasta das provas dentro de `uploads_dir` (volume compartilhado api/worker).
PASTA_PROVAS = "denuncia/provas"
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
    só o último, por remetente."""
    itens = corpo.get("itens")
    if not isinstance(itens, list) or len(itens) > MAX_ITENS_ROBO:
        raise HTTPException(422, detail={"code": "denuncia_robo_corpo_invalido"})
    stmt = pg_insert(DenunciaRoboStatus).values(
        remetente=remetente.nome, dados=corpo, recebido_em=datetime.now(UTC)
    )
    await session.execute(
        stmt.on_conflict_do_update(
            index_elements=["remetente"],
            set_={"dados": stmt.excluded.dados, "recebido_em": stmt.excluded.recebido_em},
        )
    )
    await session.commit()
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
                           datetime.now(UTC), tratadas=tratadas)
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


_ORDEM_ANUNCIOS = {
    "vendas": (DenunciaAnuncio.vendas.desc(), DenunciaAnuncio.id),
    "novos": (DenunciaAnuncio.visto_primeiro.desc().nulls_last(), DenunciaAnuncio.id),
    "loja": (DenunciaAnuncio.loja, DenunciaAnuncio.vendas.desc()),
    "grupo": (DenunciaAnuncio.grupo, DenunciaAnuncio.vendas.desc()),
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
    nden = (
        select(func.count())
        .select_from(DenunciaDenuncia)
        .where(DenunciaDenuncia.anuncio_id == A.id)
        .correlate(A)
        .scalar_subquery()
    )
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
    if loja:
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
    # números do topo, sobre o mesmo recorte (sem limite)
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
        "total": total,
        "itens": itens,
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
    casos = (
        await session.execute(select(DenunciaCaso).where(DenunciaCaso.anuncio_id == anuncio_id))
    ).scalars().all()
    compras = (
        await session.execute(
            select(DenunciaCompra).where(DenunciaCompra.anuncio_id == anuncio_id)
        )
    ).scalars().all()
    return {
        "anuncio": a.dados,
        "loja": loja.dados if loja else None,
        "denuncias": [d.dados for d in dens],
        "provas": [_prova_resumo(p) for p in provas],
        "verificacoes": [v.dados for v in verifs],
        "casos": [c.dados for c in casos],
        "compras": [c.dados for c in compras],
    }


def _chave_grupo(d: DenunciaDenuncia) -> tuple:
    # mesma regra do sistema do mini: o mesmo protocolo que cobre vários
    # anúncios é UMA denúncia.
    return (d.canal, d.protocolo) if d.protocolo else ("_id", d.id)


@router.get("/denuncias")
async def listar_denuncias(
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(_ver)],
    canal: str | None = None,
    situacao: str | None = None,
    tipo: str | None = None,
    q: str | None = None,
    limite: Annotated[int, Query(ge=1, le=2000)] = 500,
) -> dict:
    D, A = DenunciaDenuncia, DenunciaAnuncio
    rows = (
        await session.execute(
            select(D, A.loja, A.titulo, A.situacao)
            .outerjoin(A, A.id == D.anuncio_id)
            .order_by(D.data.desc().nulls_last(), D.id.desc())
        )
    ).all()
    grupos: OrderedDict[tuple, dict] = OrderedDict()
    for d, loja, titulo, sit_anuncio in rows:
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
            {"id": d.anuncio_id, "loja": loja, "titulo": titulo, "situacao": sit_anuncio}
        )
    todos = list(grupos.values())
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
    situacoes = sorted({g["situacao"] for g in todos if g["situacao"]})
    return {
        "total": len(filtrados),
        "itens": filtrados[:limite],
        "resumo": resumo,
        "opcoes": {"canais": sorted(resumo.keys()), "situacoes": situacoes},
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


@router.get("/casos")
async def listar_casos(
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(_ver)],
) -> dict:
    C, A = DenunciaCaso, DenunciaAnuncio
    ncompras = (
        select(func.count())
        .select_from(DenunciaCompra)
        .where(DenunciaCompra.caso_id == C.id)
        .correlate(C)
        .scalar_subquery()
    )
    nprovas = (
        select(func.count())
        .select_from(DenunciaProva)
        .where(or_(DenunciaProva.caso_id == C.id, DenunciaProva.anuncio_id == C.anuncio_id))
        .correlate(C)
        .scalar_subquery()
    )
    rows = (
        await session.execute(
            select(C, A.loja, A.titulo, A.marketplace, ncompras, nprovas)
            .outerjoin(A, A.id == C.anuncio_id)
            .order_by(C.id.desc())
        )
    ).all()
    itens = []
    for c, loja, titulo_anuncio, mp, n_compras, n_provas in rows:
        d = c.dados or {}
        itens.append(
            {
                "id": c.id,
                "codigo": c.codigo,
                "titulo": d.get("titulo"),
                "status": c.status,
                "anuncio_id": c.anuncio_id,
                "loja": loja,
                "titulo_anuncio": titulo_anuncio,
                "marketplace": mp,
                "aberto_em": d.get("aberto_em"),
                "juridico": d.get("juridico"),
                "juridico_enviado_em": d.get("juridico_enviado_em"),
                "ncompras": n_compras,
                "nprovas": n_provas,
            }
        )
    por_status: dict[str, int] = {}
    for i in itens:
        por_status[i["status"] or "—"] = por_status.get(i["status"] or "—", 0) + 1
    return {"total": len(itens), "itens": itens, "por_status": por_status}


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
    dens = []
    if c.anuncio_id:
        dens = (
            await session.execute(
                select(DenunciaDenuncia)
                .where(DenunciaDenuncia.anuncio_id == c.anuncio_id)
                .order_by(DenunciaDenuncia.data.desc(), DenunciaDenuncia.id.desc())
            )
        ).scalars().all()
    return {
        "caso": c.dados,
        "anuncio": anuncio.dados if anuncio else None,
        "compras": [x.dados for x in compras],
        "provas": [_prova_resumo(p) for p in provas],
        "denuncias": [d.dados for d in dens],
    }


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
