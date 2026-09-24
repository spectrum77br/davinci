"""Aba Criativos do Marketing — briefing de imagens/vídeos.

Fluxo: admin (ou quem tiver edit) cadastra as linhas (modelo/marca/sku/
roteiro); o criador de conteúdo anexa um ou mais arquivos; o admin aprova
(V) ou reprova (X). Ao aprovar, TODOS os arquivos sobem pra pasta do
produto no MEGA — o produto é achado pelo SKU na tabela de preços (aba
Produtos) e o destino é o fotos_path dele. pushed_at/pushed_dest
registram o envio.

Permissões: recurso "marketing_criativos" (view/edit); aprovação é
sempre admin. Independente do recurso "marketing" (dashboards de Ads).
"""
from __future__ import annotations

import contextlib
import logging
import re
import shutil
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Annotated, Any
from uuid import UUID, uuid4

import structlog
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, field_validator
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.deps.auth import require_admin, require_permission
from app.models import (
    Marca,
    MarketingCreative,
    MarketingCreativeFile,
    MarketingRoteiro,
    PricingProduct,
    Product,
    ProductLink,
    User,
    UserRole,
)
from app.schemas.marketing_legendas import legenda_opcional
from app.services.marketing import link_criativo
from app.services.marketing.anexos import (
    caminho_confinado,
    mime_seguro,
)
from app.services.mega_fotos import MegaError, sidecar_request

logger = structlog.get_logger()
router = APIRouter(prefix="/api/marketing/creatives", tags=["marketing"])

MAX_FILES_PER_ROW = 20


def _file_out(f: MarketingCreativeFile) -> dict[str, Any]:
    return {
        "id": str(f.id),
        "file_name": f.file_name,
        "file_mime": f.file_mime,
        "file_size": f.file_size,
        # QUANDO o vídeo chegou. A data da LINHA não serve: ela nasce quando a
        # entrega é aberta, e o vídeo pode chegar dias depois — ou de novo,
        # depois de uma recusa, e aí é esta data que diz qual arquivo é o novo.
        "enviado_em": f.created_at.isoformat() if f.created_at else None,
    }


def _row_out(row: MarketingCreative) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "modelo": row.modelo,
        "marca": row.marca,
        "sku": row.sku,
        "equipe": row.equipe,
        "roteiro": row.roteiro,
        # Override da legenda POR VÍDEO — 2º degrau da cascata, acima dos
        # modelos de produto/marca. O `roteiro` NÃO é legenda: é briefing de
        # gravação, e foi o pré-preenchimento dele no modal que pôs prompt de
        # vídeo em inglês na cara do Instagram.
        "legenda": row.legenda,
        # Produto resolvido pelo SKU no salvamento. NULL não trava postagem
        # nenhuma (a legenda cai no padrão da marca), mas a tela mostra — é o
        # que denuncia o SKU digitado errado.
        "product_id": str(row.product_id) if row.product_id else None,
        "files": [_file_out(f) for f in row.files],
        # O briefing virou entidade própria (migration 0299): aqui só o
        # ponteiro e o título, pra célula da planilha ter o que mostrar sem
        # uma segunda chamada. O texto, as imagens de referência e os
        # personagens moram em /api/marketing/roteiros.
        "roteiro_id": str(row.roteiro_id) if row.roteiro_id else None,
        "roteiro_titulo": row.roteiro_ref.titulo if row.roteiro_ref else None,
        "aprovado": row.aprovado,
        # O recado escrito na aprovação/recusa. É o que a agência lê no portal.
        "feedback": row.feedback,
        "feedback_em": row.feedback_em.isoformat() if row.feedback_em else None,
        "pushed_at": row.pushed_at.isoformat() if row.pushed_at else None,
        "pushed_dest": row.pushed_dest,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


async def _get_row(session: AsyncSession, creative_id: UUID) -> MarketingCreative:
    row = (
        await session.execute(
            select(MarketingCreative).where(MarketingCreative.id == creative_id)
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, detail={"code": "not_found"})
    return row


def _file_dir(row: MarketingCreative) -> Path:
    return Path(get_settings().uploads_dir) / "creatives" / str(row.id)


def _user_equipes(user: User) -> set[str] | None:
    """Equipes (lowercase) que restringem o que o usuário vê. None =
    sem restrição (admin ou usuário sem equipe de marketing) — mesmo
    espírito das stock_tags no Controle de Estoque."""
    if user.role == UserRole.ADMIN:
        return None
    teams = {
        t.strip().lower()
        for t in (user.marketing_teams or [])
        if isinstance(t, str) and t.strip()
    }
    return teams or None


def _ensure_equipe(user: User, row: MarketingCreative) -> None:
    allowed = _user_equipes(user)
    if allowed is None:
        return
    if (row.equipe or "").strip().lower() not in allowed:
        raise HTTPException(403, detail={"code": "fora_da_sua_equipe"})


@router.get("/equipes")
async def list_equipes(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("marketing_criativos", "view"))],
) -> list[str]:
    """Opções de equipe pros selects: união das agências com token de portal
    + equipes de marketing dos usuários + valores já usados nas linhas (pra
    nada órfão sumir).

    O `PORTAL_TOKENS` entra PRIMEIRO e é o dono da grafia: é o nome com que a
    agência entra no portal, e é contra ele que o portal compara. Sem esta
    primeira volta, uma agência só aparecia no select depois de alguém
    escrever o nome dela à mão em algum usuário — e até lá o roteiro não
    tinha como ser endereçado a ela nem a entrega como ser marcada como dela.
    """
    # Import local: `portal_criativos` já importa deste módulo, e um import
    # no topo fecharia o ciclo. Mesmo padrão do `/destinos`.
    from app.routers.portal_criativos import equipes_dos_tokens

    out: dict[str, str] = {}
    for t in equipes_dos_tokens():
        out.setdefault(t.lower(), t)
    for lst in (await session.execute(select(User.marketing_teams))).scalars().all():
        for t in lst or []:
            if isinstance(t, str) and t.strip():
                out.setdefault(t.strip().lower(), t.strip())
    for t in (await session.execute(select(MarketingCreative.equipe))).scalars().all():
        if t and t.strip():
            out.setdefault(t.strip().lower(), t.strip())
    return sorted(out.values(), key=str.lower)


@router.get("")
async def list_creatives(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("marketing_criativos", "view"))],
) -> list[dict[str, Any]]:
    rows = (
        (
            await session.execute(
                select(MarketingCreative).order_by(MarketingCreative.created_at)
            )
        )
        .scalars()
        .all()
    )
    allowed = _user_equipes(user)
    if allowed is not None:
        rows = [r for r in rows if (r.equipe or "").strip().lower() in allowed]
    return [_row_out(r) for r in rows]


class CreativeIn(BaseModel):
    modelo: str
    marca: str | None = None
    sku: str | None = None
    equipe: str | None = None
    # LÁPIDE, por uma versão (migration 0299). O briefing virou
    # /api/marketing/roteiros. O campo continua declarado só pra poder ser
    # RECUSADO: `CreativeIn` é BaseModel puro, e o default do Pydantic v2 é
    # `extra="ignore"` — sem esta linha, um POST antigo mandando `roteiro`
    # continuaria respondendo 200 e jogando o texto fora em silêncio.
    roteiro: str | None = None
    # Legenda escrita à mão pra ESTE vídeo. Vazio vira NULL de propósito: é
    # o NULL que faz a cascata seguir pra biblioteca da marca/produto.
    legenda: str | None = None

    _v_legenda = field_validator("legenda", mode="before")(legenda_opcional)


def _recusa_roteiro(texto: str | None) -> None:
    """400 explícito, não descarte silencioso.

    Quem ainda manda `roteiro` no criativo está escrevendo briefing no lugar
    errado desde a 0299. Aceitar e ignorar faria o texto evaporar sem erro —
    e faria os testes antigos passarem sem provar nada.
    """
    if texto is not None:
        raise HTTPException(
            400,
            detail={
                "code": "roteiro_mudou_de_lugar",
                "onde": "/api/marketing/roteiros",
            },
        )


@router.post("")
async def create_creative(
    payload: CreativeIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("marketing_criativos", "edit"))],
) -> dict[str, Any]:
    _recusa_roteiro(payload.roteiro)
    modelo = payload.modelo.strip()
    if not modelo:
        raise HTTPException(400, detail={"code": "modelo_obrigatorio"})
    equipe = (payload.equipe or "").strip() or None
    allowed = _user_equipes(user)
    if allowed is not None and (equipe is None or equipe.lower() not in allowed):
        # Usuário restrito: a linha nasce na equipe dele (senão sumiria
        # da própria listagem). Usa a primeira equipe com grafia original.
        firsts = [t.strip() for t in (user.marketing_teams or []) if isinstance(t, str) and t.strip()]
        equipe = sorted(firsts, key=str.lower)[0]
    row = MarketingCreative(
        id=uuid4(),
        modelo=modelo,
        marca=(payload.marca or "").strip() or None,
        marca_id=await _marca_id_do_texto(session, payload.marca),
        sku=(payload.sku or "").strip() or None,
        product_id=await _product_id_do_sku(session, payload.sku),
        equipe=equipe,
        legenda=payload.legenda,
        created_by=user.id,
    )
    session.add(row)
    await session.commit()
    row = await _get_row(session, row.id)
    return _row_out(row)


async def _marca_id_do_texto(session: AsyncSession, marca: str | None) -> UUID | None:
    """Resolve o texto livre da coluna `marca` para o id do cadastro.

    A coluna `marca` é texto digitado na célula da planilha; `marca_id` é a
    ligação de verdade com Cadastros › Marcas, e é ELA que o robô de postagem
    usa pra decidir em quais contas o vídeo pode sair. Sem esta resolução as
    duas divergem calado: quem troca a marca na tela muda só o texto, o id
    continua apontando pra marca antiga, e o robô recusa a publicação dizendo
    "essa conta é de outra marca" numa linha que na tela parece certa.

    Casa por `slug` e também por `nome` (a marca renomeada mantém o slug
    antigo — "charlots" com slug "poofy" é o caso real que provocou isto).
    """
    alvo = (marca or "").strip().lower()
    if not alvo:
        return None
    return await session.scalar(
        select(Marca.id).where(
            or_(func.lower(Marca.slug) == alvo, func.lower(Marca.nome) == alvo)
        ).limit(1)
    )


async def _product_id_do_sku(session: AsyncSession, sku: str | None) -> UUID | None:
    """Resolve o SKU do criativo para o id do produto, pela ponte dos anúncios.

    Medido nos 41 criativos de produção: `product_links.external_sku` casa
    39; `pricing_products` casa 21 e `products.sku`, zero. Por isso a corrente
    é criativo → product_links → products, e não o SKU cru do cadastro.

    Casa pelo SKU inteiro OU pela BASE (`dg017.pi` → `dg017`), porque o
    sufixo é variante de cor e o anúncio costuma estar cadastrado só na base.
    O casamento exato ganha do casamento por base, e o desempate segue por
    data/id: cor diferente do mesmo produto cai no mesmo `product_id`, mas a
    ordem precisa ser determinística — senão a legenda troca de produto entre
    dois salvamentos sem ninguém mexer em nada.

    Resolvido no SALVAMENTO, nunca na hora de publicar. Casar string no
    instante do post é o que faz a legenda mudar sozinha quando alguém
    renomeia um anúncio — mesmo motivo do `_marca_id_do_texto` acima.

    Quando nem o exato nem a base têm anúncio, sobram dois degraus (Eduardo,
    24/09/2026 — três criativos `dg046.sp` saíram com a legenda genérica da
    marca: o anúncio existe só como `dg046.pi`, e o `dg046.sp` só no
    cadastro):

      IRMÃO   anúncio de OUTRA variante da mesma base, avulso (sem "+", que é
              kit). Só vale quando todos os irmãos apontam pro MESMO produto:
              o `dg023` tem irmãos avulso, usado e kit 2 — escolher um deles
              por sorteio de data faria a legenda falar de celular usado.
      CADASTRO `products.sku` igual ao SKU inteiro. Identifica o produto,
              mas sem anúncio não traz a frase de ficha (bateria, tela…).

    O irmão ganha do cadastro quando os dois dão o MESMO nome — é o mesmo
    aparelho, e pelo irmão vêm o nome E a ficha, e o Desempenho junta os
    vídeos das duas variantes no mesmo produto. Com nomes diferentes, o
    cadastro ganha: o SKU inteiro é a identidade, o irmão é palpite.
    """
    alvo = (sku or "").strip().lower()
    if not alvo:
        return None
    base = alvo.split(".")[0]
    externo = func.lower(func.btrim(ProductLink.external_sku))
    direto = await session.scalar(
        select(ProductLink.product_id)
        .where(externo.in_([alvo, base]))
        .order_by((externo == alvo).desc(), ProductLink.created_at, ProductLink.id)
        .limit(1)
    )
    if direto is not None:
        return direto

    irmaos = (
        await session.execute(
            select(ProductLink.product_id)
            .where(
                func.split_part(externo, ".", 1) == base,
                ~externo.contains("+"),
                ProductLink.product_id.isnot(None),
            )
            .distinct()
        )
    ).scalars().all()
    irmao = irmaos[0] if len(irmaos) == 1 else None

    cadastro = (
        await session.execute(
            select(Product.id, Product.name)
            .where(func.lower(func.btrim(Product.sku)) == alvo)
            .order_by(Product.created_at, Product.id)
            .limit(1)
        )
    ).first()
    if cadastro is None:
        return irmao
    if irmao is None:
        return cadastro.id
    nome_irmao = await session.scalar(select(Product.name).where(Product.id == irmao))
    mesmo_nome = (nome_irmao or "").strip().lower() == (cadastro.name or "").strip().lower()
    return irmao if mesmo_nome else cadastro.id


async def _roteiro_valido(session: AsyncSession, roteiro_id: UUID | None) -> UUID | None:
    """404 quando o roteiro não existe — não 500 do banco no commit.

    Sem esta conferência o vínculo errado só aparece como IntegrityError lá na
    frente, com a mensagem crua da FK na cara do operador.
    """
    if roteiro_id is None:
        return None
    existe = await session.scalar(
        select(MarketingRoteiro.id).where(MarketingRoteiro.id == roteiro_id)
    )
    if existe is None:
        raise HTTPException(404, detail={"code": "roteiro_nao_encontrado"})
    return existe


class CreativePatch(BaseModel):
    modelo: str | None = None
    marca: str | None = None
    sku: str | None = None
    equipe: str | None = None
    roteiro: str | None = None  # LÁPIDE — ver `_recusa_roteiro`
    # Qual briefing esta linha cumpre. `null` desvincula.
    roteiro_id: UUID | None = None
    legenda: str | None = None
    # Recado pro time de criação. Sai do DaVinci e aparece no portal deles —
    # dá pra escrever sem recusar nada (um ajuste fino, um "faltou o SKU na
    # tela"), e o `aprovar` também carimba este campo.
    feedback: str | None = None

    _v_legenda = field_validator("legenda", mode="before")(legenda_opcional)


@router.patch("/{creative_id}")
async def patch_creative(
    creative_id: UUID,
    payload: CreativePatch,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("marketing_criativos", "edit"))],
) -> dict[str, Any]:
    row = await _get_row(session, creative_id)
    _ensure_equipe(user, row)
    data = payload.model_dump(exclude_unset=True)
    _recusa_roteiro(data.get("roteiro"))
    trocou_roteiro = "roteiro_id" in data
    if trocou_roteiro:
        row.roteiro_id = await _roteiro_valido(session, data["roteiro_id"])
    if "modelo" in data:
        modelo = (data["modelo"] or "").strip()
        if not modelo:
            raise HTTPException(400, detail={"code": "modelo_obrigatorio"})
        row.modelo = modelo
    if "marca" in data:
        row.marca = (data["marca"] or "").strip() or None
        # O id acompanha o texto SEMPRE — inclusive virando NULL quando a
        # célula é esvaziada ou aponta pra uma marca que não está no cadastro.
        row.marca_id = await _marca_id_do_texto(session, row.marca)
    if "sku" in data:
        row.sku = (data["sku"] or "").strip() or None
        # O produto acompanha o SKU SEMPRE — inclusive virando NULL quando a
        # célula é esvaziada ou aponta pra um SKU sem anúncio. Mesma regra do
        # `marca_id` logo acima: célula certa na tela e vínculo apontando pro
        # produto antigo é divergência que ninguém vê até a legenda sair
        # falando do produto errado.
        row.product_id = await _product_id_do_sku(session, row.sku)
    if "equipe" in data:
        nova = (data["equipe"] or "").strip() or None
        allowed = _user_equipes(user)
        if allowed is not None and (nova is None or nova.lower() not in allowed):
            raise HTTPException(403, detail={"code": "fora_da_sua_equipe"})
        row.equipe = nova
    if "legenda" in data:
        row.legenda = data["legenda"]
    if "feedback" in data:
        texto = (data["feedback"] or "").strip()
        row.feedback = texto or None
        # Carimbo junto: sem a data, a agência não sabe se o recado é deste
        # vídeo que ela acabou de mandar ou da versão de duas semanas atrás.
        row.feedback_em = datetime.now(timezone.utc) if texto else None
    await session.commit()
    if trocou_roteiro:
        # `roteiro_ref` foi resolvido no carregamento, ANTES de o ponteiro
        # mudar. Sem recarregar, a resposta do PATCH volta com o título do
        # roteiro antigo (ou None) e a célula da planilha pisca errado.
        await session.refresh(row)
    return _row_out(row)


async def _tem_postagem(session: AsyncSession, file_id: UUID) -> bool:
    """Existe postagem (de qualquer estado) presa a este arquivo?"""
    from app.models.marketing_postagem import MarketingPostagem

    return bool(
        (
            await session.execute(
                select(func.count())
                .select_from(MarketingPostagem)
                .where(MarketingPostagem.file_id == file_id)
            )
        ).scalar_one()
    )


# MIME que pode ser servido INLINE, no mesmo origin do app. Qualquer outro
# desce como anexo octet-stream (`mime_seguro`, em services/marketing/
# anexos.py): `file_mime` vem do `Content-Type` que o NAVEGADOR DO UPLOADER
# mandou — quem sobe o arquivo escolhe o valor. Servir isso inline deixa
# alguém com permissão de editar criativo publicar um `text/html` e rodar
# script na sessão de quem abrir o "vídeo", no mesmo domínio do DaVinci.
# Teto por arquivo. O criativo real tem 26-41 MB; 200 MB dá folga larga e
# ainda impede que um upload sozinho encha o disco do servidor (não há cota
# por equipe, e o disco é o mesmo da API e do Postgres).
MAX_BYTES_ARQUIVO = 200 * 1024 * 1024

_MIME_INLINE_OK = frozenset({
    "video/mp4",
    "video/quicktime",
    "video/webm",
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/gif",
    "application/pdf",
})
# O endpoint PÚBLICO só existe pra Meta baixar Reel. Ali a lista é menor
# ainda: só vídeo.
_MIME_VIDEO_OK = frozenset({"video/mp4", "video/quicktime", "video/webm"})


@router.post("/{creative_id}/arquivo")
async def upload_arquivos(
    creative_id: UUID,
    files: Annotated[list[UploadFile], File(...)],
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("marketing_criativos", "edit"))],
) -> dict[str, Any]:
    row = await _get_row(session, creative_id)
    _ensure_equipe(user, row)
    if row.pushed_at is not None:
        raise HTTPException(409, detail={"code": "ja_enviado_pro_mega"})
    if not files:
        raise HTTPException(400, detail={"code": "sem_arquivo"})
    if len(row.files) + len(files) > MAX_FILES_PER_ROW:
        raise HTTPException(400, detail={"code": "muitos_arquivos"})

    base = _file_dir(row)
    base.mkdir(parents=True, exist_ok=True)
    existing = {f.file_name: f for f in row.files}
    added: list[str] = []
    for up in files:
        name = Path(up.filename or "arquivo").name
        if not name or name in {".", ".."}:
            raise HTTPException(400, detail={"code": "nome_invalido"})
        old = existing.get(name)
        if old is not None and await _tem_postagem(session, old.id):
            # Substituir o registro apaga o antigo, e a FK das postagens é
            # CASCADE: o histórico do que já foi publicado (e as agendadas)
            # iria junto, calado. Quem quer trocar o vídeo sobe com outro nome.
            raise HTTPException(
                409, detail={"code": "arquivo_com_postagem", "arquivo": name}
            )
        abs_path = base / name
        # O sha256 sai do MESMO fluxo de bytes que está sendo gravado: é ele
        # que permite dizer "esse vídeo já rodou em outra marca" (Instagram e
        # TikTok punem conteúdo repetido entre contas, e em silêncio).
        digest = sha256()
        escrito = 0
        with abs_path.open("wb") as fh:
            while pedaco := up.file.read(1024 * 1024):
                escrito += len(pedaco)
                if escrito > MAX_BYTES_ARQUIVO:
                    # Aborta no meio e não deixa lixo: sem isto um POST grande
                    # enche o disco antes de qualquer validação.
                    fh.close()
                    abs_path.unlink(missing_ok=True)
                    raise HTTPException(
                        413,
                        detail={
                            "code": "arquivo_grande_demais",
                            "arquivo": name,
                            "max_mb": MAX_BYTES_ARQUIVO // (1024 * 1024),
                        },
                    )
                digest.update(pedaco)
                fh.write(pedaco)
        if old is not None:  # mesmo nome substitui o registro antigo
            row.files.remove(old)
        rec = MarketingCreativeFile(
            id=uuid4(),
            file_name=name,
            file_mime=up.content_type or "application/octet-stream",
            file_size=abs_path.stat().st_size,
            file_rel=f"creatives/{row.id}/{name}",
            sha256=digest.hexdigest(),
        )
        row.files.append(rec)
        existing[name] = rec
        added.append(name)

    row.aprovado = None  # arquivo novo volta pra "pendente"
    await session.commit()
    logger.info(
        "creative_files_upload",
        creative_id=str(row.id),
        files=added,
        user_id=str(user.id),
    )
    return _row_out(row)


_RX_LINK_NO_PATH = re.compile(r"(/api/marketing/creatives/video/)[^/\s?]+")


def mascarar_link_no_access_log() -> None:
    """Tira o token do link assinado do access log do uvicorn.

    O token vai no PATH (a Meta busca por GET, sem header nosso), e o access
    log guarda o path inteiro. Quem lê o log do container ganharia 15 minutos
    de acesso ao vídeo sem sessão nenhuma. Mesmo molde do
    `claude_conector.mascarar_token_no_access_log`.
    """

    class _Mascara(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            args = record.args
            if isinstance(args, tuple) and len(args) >= 3 and isinstance(args[2], str):
                record.args = (*args[:2], _RX_LINK_NO_PATH.sub(r"\1***", args[2]), *args[3:])
            return True

    logging.getLogger("uvicorn.access").addFilter(_Mascara())


@router.get("/video/{token}", include_in_schema=False)
async def video_publico(
    token: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> FileResponse:
    """Vídeo do criativo por LINK ASSINADO, sem sessão — é assim que a Meta
    baixa o arquivo pra publicar o Reel quando a marca não tem Página do
    Facebook (trilha Instagram Login usa `video_url`, e a doc exige servidor
    público). Token HMAC de 15 min gerado no momento de publicar
    (services/marketing/link_criativo.py); fora disso o acesso continua sendo
    só pelo download autenticado abaixo. Declarado ANTES das rotas
    /{creative_id}/… pra não ser capturado por elas."""
    file_id = link_criativo.validar_token(token)
    if file_id is None:
        raise HTTPException(404, detail={"code": "link_invalido_ou_expirado"})
    rec = (
        await session.execute(
            select(MarketingCreativeFile).where(MarketingCreativeFile.id == file_id)
        )
    ).scalar_one_or_none()
    if rec is None:
        raise HTTPException(404, detail={"code": "arquivo_nao_encontrado"})
    abs_path = caminho_confinado(rec.file_rel)
    if abs_path is None or not abs_path.is_file():
        raise HTTPException(404, detail={"code": "arquivo_sumiu"})
    # Esta porta é aberta: quem tiver o link de 15 min baixa sem sessão. Então
    # ela serve VÍDEO e nada mais — nunca o MIME que o uploader escolheu.
    media_type, _ = mime_seguro(rec.file_mime, permitidos=_MIME_VIDEO_OK)
    if media_type == "application/octet-stream":
        raise HTTPException(404, detail={"code": "arquivo_nao_e_video"})
    logger.info(
        "criativo_video_publico_servido",
        file_id=str(rec.id),
        creative_id=str(rec.creative_id),
        bytes=rec.file_size,
    )
    return FileResponse(
        abs_path,
        media_type=media_type,
        filename=rec.file_name,
        headers={"Cache-Control": "private, max-age=60", "X-Content-Type-Options": "nosniff"},
    )


@router.get("/{creative_id}/arquivo/{file_id}")
async def download_arquivo(
    creative_id: UUID,
    file_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("marketing_criativos", "view"))],
    download: bool = False,
) -> FileResponse:
    row = await _get_row(session, creative_id)
    _ensure_equipe(user, row)
    rec = next((f for f in row.files if f.id == file_id), None)
    if rec is None:
        raise HTTPException(404, detail={"code": "sem_arquivo"})
    abs_path = caminho_confinado(rec.file_rel)
    if abs_path is None or not abs_path.is_file():
        raise HTTPException(404, detail={"code": "arquivo_sumiu"})
    # inline = abre no navegador (preview de imagem/vídeo); ?download=1 força
    # baixar. O MIME NUNCA é o do uploader: fora da allowlist vira anexo
    # octet-stream, senão um `text/html` subido como "criativo" rodaria script
    # em app.hadken.com com o cookie de sessão de quem clicasse.
    media_type, disposicao = mime_seguro(rec.file_mime, permitidos=_MIME_INLINE_OK)
    return FileResponse(
        abs_path,
        filename=rec.file_name,
        media_type=media_type,
        content_disposition_type="attachment" if download else disposicao,
        headers={"X-Content-Type-Options": "nosniff"},
    )


@router.delete("/{creative_id}/arquivo/{file_id}")
async def delete_arquivo(
    creative_id: UUID,
    file_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("marketing_criativos", "edit"))],
) -> dict[str, Any]:
    row = await _get_row(session, creative_id)
    _ensure_equipe(user, row)
    if row.pushed_at is not None:
        raise HTTPException(409, detail={"code": "ja_enviado_pro_mega"})
    rec = next((f for f in row.files if f.id == file_id), None)
    if rec is None:
        raise HTTPException(404, detail={"code": "sem_arquivo"})
    abs_path = Path(get_settings().uploads_dir) / rec.file_rel
    with contextlib.suppress(OSError):
        abs_path.unlink(missing_ok=True)
    name = rec.file_name
    row.files.remove(rec)
    await session.commit()
    logger.info(
        "creative_file_delete",
        creative_id=str(row.id),
        file=name,
        user_id=str(user.id),
    )
    return _row_out(row)


def _skus_a_tentar(sku: str) -> list[str]:
    """O SKU escrito e, depois, a LINHA dele — nessa ordem.

    O criativo carrega o SKU da VARIANTE ("dg023.ra" é o preto avulso,
    "dg023.ra+a003.ra" é o combo com fone); a Tabela de Preços carrega a LINHA
    ("dg023"), porque a pasta de fotos no MEGA é da linha, não de cada cor.

    Sem esta queda o operador ficava entre dois cadastros: escrevia a variante
    e a aprovação não achava a pasta, escrevia a linha e a legenda perdia o
    produto (`product_links` só tem as variantes). Medido em 16/09/2026: dos 13
    SKUs distintos dos criativos, 1 casa exato e 13 casam pela linha.

    O exato vem primeiro de propósito: se um dia a Tabela de Preços listar a
    variante, é ela que manda.
    """
    escrito = (sku or "").strip()
    base = escrito.split(".")[0].strip()
    return [x for x in dict.fromkeys([escrito, base]) if x]


def _match_product_by_sku(
    products: list[PricingProduct], sku: str
) -> PricingProduct | None:
    want = sku.strip().lower()
    for p in products:
        parts = [s.strip().lower() for s in (p.sku or "").split(",")]
        if want in parts:
            return p
    return None


class AprovarIn(BaseModel):
    aprovado: bool
    # Recado que vai junto da decisão. Na RECUSA é o que evita a agência
    # regravar no escuro; na aprovação serve de elogio/ajuste fino. Ausente
    # (campo não enviado) mantém o recado anterior; string vazia apaga.
    feedback: str | None = None


def _aplica_feedback(row: MarketingCreative, texto: str | None) -> None:
    """None = não mexe (quem aprovou não escreveu nada); "" = apaga o recado."""
    if texto is None:
        return
    limpo = texto.strip()
    row.feedback = limpo or None
    row.feedback_em = datetime.now(timezone.utc) if limpo else None


@router.post("/{creative_id}/aprovar")
async def aprovar_creative(
    creative_id: UUID,
    payload: AprovarIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_admin)],
) -> dict[str, Any]:
    row = await _get_row(session, creative_id)
    _aplica_feedback(row, payload.feedback)

    if payload.aprovado is False:
        row.aprovado = False
        await session.commit()
        return _row_out(row)

    if row.pushed_at is not None:  # já foi pro MEGA — só garante o V
        row.aprovado = True
        await session.commit()
        return _row_out(row)

    recs = list(row.files)
    if not recs:
        raise HTTPException(400, detail={"code": "sem_arquivo"})
    sku = (row.sku or "").strip()
    if not sku:
        raise HTTPException(400, detail={"code": "sem_sku"})
    uploads_dir = Path(get_settings().uploads_dir)
    paths: list[tuple[MarketingCreativeFile, Path]] = []
    for rec in recs:
        abs_path = uploads_dir / rec.file_rel
        if not abs_path.is_file():
            raise HTTPException(404, detail={"code": "arquivo_sumiu"})
        paths.append((rec, abs_path))

    product = None
    for tentativa in _skus_a_tentar(sku):
        candidates = (
            (
                await session.execute(
                    select(PricingProduct).where(PricingProduct.sku.ilike(f"%{tentativa}%"))
                )
            )
            .scalars()
            .all()
        )
        product = _match_product_by_sku(list(candidates), tentativa)
        if product is not None:
            break
    if product is None:
        raise HTTPException(404, detail={"code": "produto_nao_encontrado"})
    dest = (product.fotos_path or "").strip()
    if not dest:
        raise HTTPException(400, detail={"code": "produto_sem_pasta"})

    try:
        with contextlib.ExitStack() as stack:
            await sidecar_request(
                "POST",
                "/upload",
                data={"dest": dest},
                files=[
                    (
                        "files",
                        (
                            rec.file_name,
                            stack.enter_context(abs_path.open("rb")),
                            rec.file_mime or "application/octet-stream",
                        ),
                    )
                    for rec, abs_path in paths
                ],
                timeout=3600.0,
            )
    except MegaError as exc:
        raise HTTPException(
            502, detail={"code": "mega_error", "message": str(exc)}
        ) from exc

    fotos_count = videos_count = None
    try:
        cnt = await sidecar_request(
            "GET", "/media_counts", params={"path": dest}, timeout=300.0
        )
        fotos_count = int(cnt["fotos"])
        videos_count = int(cnt["videos"])
    except (MegaError, KeyError, TypeError, ValueError):
        pass  # contagem é cosmética; o envio em si já deu certo
    if fotos_count is not None:
        siblings = (
            (
                await session.execute(
                    select(PricingProduct).where(PricingProduct.fotos_path == dest)
                )
            )
            .scalars()
            .all()
        )
        for sib in siblings:
            sib.fotos_count = fotos_count
            sib.videos_count = videos_count

    row.aprovado = True
    row.pushed_at = datetime.now(timezone.utc)
    row.pushed_dest = dest
    await session.commit()
    logger.info(
        "creative_pushed_to_mega",
        creative_id=str(row.id),
        sku=sku,
        dest=dest,
        n_files=len(paths),
        user_id=str(user.id),
    )
    out = _row_out(row)
    out["enviados"] = len(paths)
    out["fotos_count"] = fotos_count
    out["videos_count"] = videos_count
    return out


@router.delete("/{creative_id}")
async def delete_creative(
    creative_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("marketing_criativos", "edit"))],
) -> dict[str, str]:
    row = await _get_row(session, creative_id)
    _ensure_equipe(user, row)
    if row.pushed_at is not None and user.role != UserRole.ADMIN:
        raise HTTPException(403, detail={"code": "ja_enviado_pro_mega"})
    base = _file_dir(row)
    if base.exists():
        shutil.rmtree(base, ignore_errors=True)
    await session.delete(row)
    await session.commit()
    return {"status": "deleted"}
