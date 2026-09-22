"""Portal do time de criação — a porta estreita para fora (Eduardo, 21/09/2026).

"criaremos no hostinger um dominio php html e nela teremos a tela de marketing
uma tela de roteiros (...) pra eles tambem verem que estao fazendo o criativo
mas sem acesso claro a outras coisas".

Duas agências TERCEIRAS. Elas não têm conta no DaVinci e não vão ter: quem
autentica pessoa é o site delas. O que atravessa a internet é uma chamada
servidor-a-servidor com um token no header, guardado no servidor PHP — o
navegador do criativo nunca o vê, e o CORS do DaVinci não muda.

## Por que um router separado, e não um filtro no de sempre

O `marketing_creatives` também permite PATCH da linha, DELETE do arquivo,
DELETE da linha e o `aprovar` que empurra pro MEGA. Pendurar "mas só se for
agência" em cada um deles é o tipo de guarda que alguém esquece de repetir na
próxima rota. Aqui a superfície é a lista de rotas deste arquivo, e é curta:
ler as entregas da própria equipe, ler os roteiros endereçados a ela, ler o
catálogo de personagens, baixar os bytes desses dois, anexar arquivo.
Nada mais.

## As quatro travas

1. **Fecha por padrão.** Token não configurado, ausente ou diferente → 401,
   com `compare_digest`, antes de qualquer trabalho. É o desenho do
   `_require_agent_token` (routers/marketing.py:975) e o oposto do webhook do
   Bling, que não rejeita nunca — a revisão de 18/09 achou isso lá.
2. **Não devolve `User`.** Sem User não há `role`, e o atalho de admin que
   fura todos os gates não é alcançado nem por acidente.
3. **A equipe vai no WHERE.** Nunca em memória, nunca opcional. E NÃO uso o
   `_user_equipes` do outro router: lá `return teams or None` significa
   "sem restrição" — usuário sem equipe vê tudo. Aqui equipe vazia é 401.
4. **Serializador com lista branca.** O `_row_out` de lá entrega `legenda`,
   `product_id` e `pushed_dest` (o caminho da pasta no MEGA). Nada disso é da
   conta de terceiro.
5. **Rota de bytes nunca busca a filha pelo id.** Ela resolve o PAI com
   exatamente o mesmo WHERE da listagem e só então procura o arquivo dentro
   dele. Sem isso, desligar um roteiro tira o card da tela mas continua
   entregando a imagem pra sempre a quem anotou o id.

## As DUAS regras de NULL, que são opostas

`MarketingCreative.equipe` NULL = **ninguém de fora vê** (linha sem dono).
`MarketingRoteiro.equipe_destino` NULL = **as duas agências veem** (briefing
sem destinatário específico — a regra que o Eduardo pediu em 21/09/2026).

Cada uma tem um helper só, com nome diferente: `_da_equipe` e
`_enderecado_a`. Usar o errado não compila numa query plausível, porque as
colunas estão em tabelas diferentes.

## O que este portal NÃO faz

Não aprova, não apaga, não edita a linha, não fala com o MEGA. O envio pro
MEGA continua acontecendo só no clique do admin dentro do DaVinci.
"""

from __future__ import annotations

import secrets
from hashlib import sha256
from pathlib import Path
from typing import Annotated, Any
from uuid import UUID, uuid4

import structlog
from fastapi import APIRouter, Depends, File, Header, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.db import get_session
from app.models.marketing import MarketingCreative, MarketingCreativeFile
from app.models.marketing_personagem import MarketingPersonagem, MarketingPersonagemArquivo
from app.models.marketing_roteiro import MarketingRoteiro, MarketingRoteiroRef
from app.routers.marketing_creatives import (
    MAX_BYTES_ARQUIVO,
    MAX_FILES_PER_ROW,
    _file_dir,
)
from app.services.marketing.anexos import (
    MIMES_PERSONAGEM,
    MIMES_REFERENCIA,
    caminho_confinado,
    mime_seguro,
)

logger = structlog.get_logger()

router = APIRouter(prefix="/api/portal", tags=["portal-criativos"])

# Teto do que uma listagem devolve. Agência com muito histórico não derruba a
# chamada nem entrega o catálogo inteiro de uma vez.
LIMITE_PADRAO = 200


def _mapa_tokens() -> dict[str, str]:
    """`"tok:equipe,tok:equipe"` → {token: equipe}. Linha torta é ignorada."""
    bruto = get_settings().portal_tokens or ""
    mapa: dict[str, str] = {}
    for parte in bruto.split(","):
        token, _, equipe = parte.partition(":")
        token, equipe = token.strip(), equipe.strip()
        if token and equipe:
            mapa[token] = equipe
    return mapa


def equipes_dos_tokens() -> list[str]:
    """As agências que TÊM porta — o vocabulário de equipe do lado de fora.

    O `PORTAL_TOKENS` é a única fonte que sabe de uma agência ANTES de ela
    ter a primeira linha ou o primeiro usuário interno. Enquanto o nome só
    morava aqui, o select "Vai para" do roteiro não oferecia a agência
    recém-cadastrada e o briefing não tinha como ser endereçado a ela.
    Devolve só os NOMES; o token não sai desta função.
    """
    vistos: dict[str, str] = {}
    for equipe in _mapa_tokens().values():
        vistos.setdefault(equipe.strip().lower(), equipe.strip())
    return sorted(vistos.values(), key=str.lower)


async def equipe_do_token(
    x_portal_token: Annotated[str | None, Header(alias="X-Portal-Token")] = None,
) -> str:
    """Devolve a EQUIPE dona do token. Nunca devolve usuário, nunca abre sem token."""
    mapa = _mapa_tokens()
    if not mapa or not x_portal_token:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, detail={"code": "portal_nao_autorizado"}
        )
    # compare_digest contra CADA token cadastrado: comparar por `in` daria a
    # resposta em tempo variável e entregaria o token caractere a caractere.
    # `compare_digest` com str exige ASCII puro e levanta TypeError fora
    # disso — um token com acento derrubava a porta em 500 em vez de 401, e
    # 500 numa rota de autenticação é informação de graça pra quem sonda.
    enviado = x_portal_token.encode("utf-8", "surrogatepass")
    for token, equipe in mapa.items():
        if secrets.compare_digest(enviado, token.encode("utf-8", "surrogatepass")):
            return equipe
    raise HTTPException(
        status.HTTP_401_UNAUTHORIZED, detail={"code": "portal_nao_autorizado"}
    )


def _arquivo_out(f: MarketingCreativeFile) -> dict[str, Any]:
    # `file_rel` fica de fora: é caminho no disco do servidor.
    return {
        "id": str(f.id),
        "nome": f.file_name,
        "tamanho": f.file_size,
        "enviado_em": f.created_at.isoformat() if f.created_at else None,
    }


def _linha_out(
    row: MarketingCreative, visiveis: frozenset[UUID] = frozenset()
) -> dict[str, Any]:
    """LISTA BRANCA. Campo novo no modelo não vaza sozinho por aqui."""
    return {
        "id": str(row.id),
        "modelo": row.modelo,
        "marca": row.marca,
        "sku": row.sku,
        "roteiro": row.roteiro,
        # None = ainda não olharam; True = aprovado; False = recusado.
        "aprovado": row.aprovado,
        # Booleano em vez da data e do caminho: a agência precisa saber que
        # foi entregue, não onde o arquivo mora.
        "entregue": row.pushed_at is not None,
        "arquivos": [_arquivo_out(f) for f in row.files],
        # Qual briefing esta entrega cumpre. O texto, as imagens e os
        # personagens vêm por /api/portal/roteiros — aqui só o ponteiro, pra
        # o site conseguir ligar uma tela na outra. Vem NULL quando o roteiro
        # não está visível pra esta agência (desligado, sem texto ou de
        # outra): o link existir e cair em 404 é pior que não existir, e no
        # dia do deploy TODO roteiro migrado está desligado.
        "roteiro_id": str(row.roteiro_id) if row.roteiro_id in visiveis else None,
        # O recado da aprovação/recusa. Sem ele o "recusado" da tela é um
        # beco: regravar sem saber o quê é o que faz a agência entregar a
        # mesma coisa de novo.
        "feedback": row.feedback,
        "feedback_em": row.feedback_em.isoformat() if row.feedback_em else None,
        "criado_em": row.created_at.isoformat() if row.created_at else None,
    }


async def _roteiros_visiveis(
    session: AsyncSession, linhas: list[MarketingCreative], equipe: str
) -> frozenset[UUID]:
    """Dos roteiros apontados por estas entregas, quais esta agência VÊ.

    Uma consulta só, com exatamente o mesmo WHERE da listagem de roteiros —
    é o que garante que o link da tela de entregas e a tela de roteiros nunca
    discordem sobre o que existe.
    """
    ids = {r.roteiro_id for r in linhas if r.roteiro_id}
    if not ids:
        return frozenset()
    achados = (
        await session.execute(
            select(MarketingRoteiro.id).where(
                MarketingRoteiro.id.in_(ids), *_visivel_pra_fora(equipe)
            )
        )
    ).scalars().all()
    return frozenset(achados)


def _da_equipe(equipe: str):
    """O filtro, num lugar só — e sempre no WHERE."""
    return func.lower(func.coalesce(MarketingCreative.equipe, "")) == equipe.lower()


async def _linha_da_equipe(
    session: AsyncSession, creative_id: UUID, equipe: str
) -> MarketingCreative:
    """404 (não 403) quando a linha é de outra equipe.

    Dizer "existe, mas não é sua" conta pra agência de fora que a linha
    existe. Do lado de fora, o que não é seu simplesmente não existe.
    """
    row = (
        await session.execute(
            select(MarketingCreative)
            .options(selectinload(MarketingCreative.files))
            .where(MarketingCreative.id == creative_id, _da_equipe(equipe))
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, detail={"code": "nao_encontrado"})
    return row


@router.get("/criativos")
async def listar_criativos(
    session: Annotated[AsyncSession, Depends(get_session)],
    equipe: Annotated[str, Depends(equipe_do_token)],
) -> dict[str, Any]:
    """A tela de marketing: o que a agência entregou e o que foi aprovado."""
    linhas = (
        (
            await session.execute(
                select(MarketingCreative)
                .options(selectinload(MarketingCreative.files))
                .where(_da_equipe(equipe))
                .order_by(MarketingCreative.created_at.desc())
                .limit(LIMITE_PADRAO)
            )
        )
        .scalars()
        .all()
    )
    visiveis = await _roteiros_visiveis(session, linhas, equipe)
    return {"equipe": equipe, "criativos": [_linha_out(r, visiveis) for r in linhas]}

# ─── roteiros ──────────────────────────────────────────────────────────────


def _enderecado_a(equipe: str):
    """O filtro do BRIEFING — e aqui NULL significa o contrário do de cima.

    `equipe_destino` vazio = o roteiro é para as duas agências. É a regra que
    o Eduardo pediu ("uma regra também de que se não preenchido vai para os
    2"). Não confundir com `_da_equipe`, que trata NULL como "de ninguém".
    """
    return or_(
        MarketingRoteiro.equipe_destino.is_(None),
        func.lower(func.btrim(MarketingRoteiro.equipe_destino)) == equipe.lower(),
    )


def _visivel_pra_fora(equipe: str):
    """TODAS as condições pra um roteiro existir do lado de fora, num lugar só.

    A listagem e a rota de BYTES usam esta mesma tupla. Se a rota de bytes
    tivesse um WHERE próprio, desligar um roteiro tiraria o card da tela e
    continuaria servindo as imagens pra sempre a quem tivesse anotado o id.
    """
    return (
        MarketingRoteiro.ativo.is_(True),
        # Roteiro sem texto é linha recém-criada, ainda sendo escrita. É este
        # filtro que permite o roteiro nascer visível sem um passo de
        # "publicar": ele só aparece quando tem o que ler.
        MarketingRoteiro.texto.is_not(None),
        func.length(func.btrim(MarketingRoteiro.texto)) > 0,
        _enderecado_a(equipe),
    )


def _roteiro_out(row: MarketingRoteiro) -> dict[str, Any]:
    """LISTA BRANCA. `created_by` e `file_rel` não são da conta de terceiro."""
    return {
        "id": str(row.id),
        "titulo": row.titulo,
        "texto": row.texto,
        "marca": row.marca,
        "sku": row.sku,
        "referencias": [
            {
                "id": str(r.id),
                "tipo": r.tipo,
                "titulo": r.titulo,
                "url": r.url,
                "nome": r.file_name,
                "mime": r.file_mime,
                "tamanho": r.file_size,
            }
            for r in row.refs
        ],
        # `ativo` é o interruptor, e ele tem que valer AQUI também: a
        # listagem do catálogo e a rota de bytes já filtram por ele, então um
        # personagem desligado ficava saindo só por dentro do roteiro — com a
        # foto dando 404 e a etiqueta do gerador (que é a parte que importa)
        # ainda na mão da agência.
        "personagens": [
            _personagem_out(v.personagem)
            for v in row.personagens
            if v.personagem and v.personagem.ativo
        ],
        "criado_em": row.created_at.isoformat() if row.created_at else None,
    }


async def _roteiro_visivel(
    session: AsyncSession, roteiro_id: UUID, equipe: str
) -> MarketingRoteiro:
    """404 (não 403) pra roteiro de outra agência, desligado ou sem texto."""
    row = (
        await session.execute(
            select(MarketingRoteiro)
            .where(MarketingRoteiro.id == roteiro_id, *_visivel_pra_fora(equipe))
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, detail={"code": "nao_encontrado"})
    return row


@router.get("/roteiros")
async def listar_roteiros(
    session: Annotated[AsyncSession, Depends(get_session)],
    equipe: Annotated[str, Depends(equipe_do_token)],
) -> dict[str, Any]:
    """A tela de roteiros: o briefing endereçado a esta agência — mais os que
    não têm destinatário, que são de todas.

    Quem escreve é a equipe interna, no DaVinci. Aqui é leitura: não existe
    rota de escrita de roteiro neste arquivo, de propósito.
    """
    linhas = (
        (
            await session.execute(
                select(MarketingRoteiro)
                .where(*_visivel_pra_fora(equipe))
                .order_by(MarketingRoteiro.created_at.desc())
                .limit(LIMITE_PADRAO)
            )
        )
        .scalars()
        .all()
    )
    return {"equipe": equipe, "roteiros": [_roteiro_out(r) for r in linhas]}


@router.get("/roteiros/{roteiro_id}")
async def ver_roteiro(
    roteiro_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    equipe: Annotated[str, Depends(equipe_do_token)],
) -> dict[str, Any]:
    """Um roteiro só — a tela de "abrir" do portal, e o link colável.

    Existe porque a listagem tem teto de 200: procurar dentro da lista faria
    a tela de detalhe sumir pra quem já tem histórico.
    """
    return {
        "equipe": equipe,
        "roteiro": _roteiro_out(await _roteiro_visivel(session, roteiro_id, equipe)),
    }


@router.get("/roteiros/{roteiro_id}/referencia/{ref_id}")
async def baixar_referencia(
    roteiro_id: UUID,
    ref_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    equipe: Annotated[str, Depends(equipe_do_token)],
) -> FileResponse:
    """Imagem de referência do briefing, pro site da agência mostrar.

    Quem chama é o SERVIDOR PHP (com o token no header), que repassa os bytes
    pro navegador do criativo — o token continua sem sair do servidor.

    Repare que a busca começa pelo ROTEIRO, com `_roteiro_visivel`, e só
    depois procura a referência dentro dele. Resolver a referência pelo id
    direto pareceria igual e serviria arquivo de roteiro desligado.
    """
    row = await _roteiro_visivel(session, roteiro_id, equipe)
    rec = next((r for r in row.refs if r.id == ref_id and r.tipo == "imagem"), None)
    if rec is None:
        raise HTTPException(404, detail={"code": "nao_encontrado"})
    return _entrega(rec, permitidos=MIMES_REFERENCIA)


# ─── personagens ───────────────────────────────────────────────────────────


def _personagem_out(p: MarketingPersonagem) -> dict[str, Any]:
    return {
        "id": str(p.id),
        "nome": p.nome,
        "descricao": p.descricao,
        # A etiqueta que o gerador de vídeo entende — é o que a agência cola
        # dentro do prompt. Sem ela o roteiro descreve uma pessoa genérica e
        # cada geração inventa outro rosto.
        "referencia": p.referencia,
        # O vídeo de referência: como a persona se move e fala.
        "video_url": p.video_url,
        # É o ARQUIVO que a agência baixa e leva pro gerador dela — a etiqueta
        # acima é atalho e pode quebrar (ela é um id interno da ferramenta que
        # criou o rosto). Por isso foto e voz saem separadas e completas.
        "imagens": [_arq_out(a) for a in p.arquivos if a.tipo == "imagem"],
        "vozes": [_arq_out(a) for a in p.arquivos if a.tipo == "voz"],
    }


def _arq_out(a: MarketingPersonagemArquivo) -> dict[str, Any]:
    return {"id": str(a.id), "nome": a.file_name, "mime": a.file_mime, "tamanho": a.file_size}


@router.get("/personagens")
async def listar_personagens(
    session: Annotated[AsyncSession, Depends(get_session)],
    equipe: Annotated[str, Depends(equipe_do_token)],
) -> dict[str, Any]:
    """O elenco da casa. Catálogo GLOBAL — a única rota daqui sem recorte por
    agência, porque personagem não tem dono de equipe. É decisão, não
    esquecimento: o dia em que personagem precisar de destinatário, ele ganha
    `equipe_destino` e entra no molde do roteiro."""
    linhas = (
        (
            await session.execute(
                select(MarketingPersonagem)
                .where(MarketingPersonagem.ativo.is_(True))
                .order_by(func.lower(MarketingPersonagem.nome))
                .limit(LIMITE_PADRAO)
            )
        )
        .scalars()
        .all()
    )
    return {"equipe": equipe, "personagens": [_personagem_out(p) for p in linhas]}


@router.get("/personagens/{personagem_id}")
async def ver_personagem(
    personagem_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    equipe: Annotated[str, Depends(equipe_do_token)],
) -> dict[str, Any]:
    """Um personagem só — a tela de "abrir" do portal.

    Mesmo WHERE da listagem (`ativo`): desligado responde 404 aqui também, e
    não só some da grade.
    """
    row = (
        await session.execute(
            select(MarketingPersonagem).where(
                MarketingPersonagem.id == personagem_id,
                MarketingPersonagem.ativo.is_(True),
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, detail={"code": "nao_encontrado"})
    return {"equipe": equipe, "personagem": _personagem_out(row)}


@router.get("/personagens/{personagem_id}/arquivo/{arquivo_id}")
async def baixar_arquivo_personagem(
    personagem_id: UUID,
    arquivo_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    equipe: Annotated[str, Depends(equipe_do_token)],
    download: bool = False,
) -> FileResponse:
    """A foto do rosto ou o MP3 da voz — e é isto que a agência LEVA.

    Mesma regra da referência: resolve o PAI com o WHERE da listagem
    (`ativo`) e só então procura o arquivo dentro dele. Personagem desligado
    para de servir arquivo, não só de aparecer na lista.
    """
    pai = (
        await session.execute(
            select(MarketingPersonagem).where(
                MarketingPersonagem.id == personagem_id,
                MarketingPersonagem.ativo.is_(True),
            )
        )
    ).scalar_one_or_none()
    if pai is None:
        raise HTTPException(404, detail={"code": "nao_encontrado"})
    rec = next((a for a in pai.arquivos if a.id == arquivo_id), None)
    if rec is None:
        raise HTTPException(404, detail={"code": "nao_encontrado"})
    return _entrega(rec, permitidos=MIMES_PERSONAGEM, baixar=download)


def _entrega(
    rec: MarketingRoteiroRef | MarketingPersonagemArquivo,
    *,
    permitidos: frozenset[str],
    baixar: bool = False,
) -> FileResponse:
    """Os bytes, com as duas travas que valem pra qualquer arquivo daqui.

    `baixar` força `attachment`: a agência precisa do arquivo NA MÃO dela, não
    só na tela — é ele que funciona em qualquer gerador.
    """
    caminho = caminho_confinado(rec.file_rel)
    if caminho is None or not caminho.is_file():
        raise HTTPException(404, detail={"code": "nao_encontrado"})
    media_type, disposicao = mime_seguro(rec.file_mime, permitidos=permitidos)
    return FileResponse(
        caminho,
        filename=rec.file_name or "arquivo",
        media_type=media_type,
        content_disposition_type="attachment" if baixar else disposicao,
        headers={
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, max-age=300",
        },
    )


def _gravar_arquivos(row: MarketingCreative, files: list[UploadFile]) -> list[str]:
    """Grava os anexos de uma linha e devolve os nomes que entraram.

    Está fora do endpoint porque agora há DOIS caminhos de entrega — a linha
    que a equipe interna abriu e a entrega que nasce do roteiro — e os tetos
    de tamanho, a contagem de arquivos e o sha256 têm que ser os mesmos nos
    dois. Duplicar esse laço é como as duas portas passariam a aceitar coisas
    diferentes sem ninguém perceber.
    """
    base = _file_dir(row)
    base.mkdir(parents=True, exist_ok=True)
    existentes = {f.file_name: f for f in row.files}
    entraram: list[str] = []

    for up in files:
        nome = Path(up.filename or "arquivo").name
        if not nome or nome in {".", ".."}:
            raise HTTPException(400, detail={"code": "nome_invalido"})
        caminho = base / nome
        digest = sha256()
        escrito = 0
        with caminho.open("wb") as fh:
            while pedaco := up.file.read(1024 * 1024):
                escrito += len(pedaco)
                if escrito > MAX_BYTES_ARQUIVO:
                    fh.close()
                    caminho.unlink(missing_ok=True)
                    raise HTTPException(
                        413,
                        detail={
                            "code": "arquivo_grande_demais",
                            "arquivo": nome,
                            "max_mb": MAX_BYTES_ARQUIVO // (1024 * 1024),
                        },
                    )
                digest.update(pedaco)
                fh.write(pedaco)

        antigo = existentes.get(nome)
        if antigo is not None:
            row.files.remove(antigo)
        rec = MarketingCreativeFile(
            id=uuid4(),
            file_name=nome,
            file_mime=up.content_type or "application/octet-stream",
            file_size=caminho.stat().st_size,
            file_rel=f"creatives/{row.id}/{nome}",
            sha256=digest.hexdigest(),
        )
        row.files.append(rec)
        existentes[nome] = rec
        entraram.append(nome)
    return entraram


@router.post("/criativos/{creative_id}/arquivo")
async def enviar_arquivo(
    creative_id: UUID,
    files: Annotated[list[UploadFile], File(...)],
    session: Annotated[AsyncSession, Depends(get_session)],
    equipe: Annotated[str, Depends(equipe_do_token)],
) -> dict[str, Any]:
    """Anexa vídeo numa linha da PRÓPRIA equipe.

    Mesmas guardas do caminho interno, e os mesmos tetos importados de lá pra
    não divergirem com o tempo: grava em streaming (nunca o arquivo inteiro na
    memória), aborta e apaga ao passar do teto, e o sha256 sai do mesmo fluxo
    de bytes — é ele que denuncia o mesmo vídeo publicado em duas marcas.
    """
    row = await _linha_da_equipe(session, creative_id, equipe)
    if row.pushed_at is not None:
        raise HTTPException(409, detail={"code": "ja_enviado_pro_mega"})
    if not files:
        raise HTTPException(400, detail={"code": "sem_arquivo"})
    if len(row.files) + len(files) > MAX_FILES_PER_ROW:
        raise HTTPException(400, detail={"code": "muitos_arquivos"})

    entraram = _gravar_arquivos(row, files)

    # Arquivo novo volta a linha pra "pendente" — mesmo comportamento do
    # caminho interno. A agência precisa ver isso na tela dela, senão parece
    # que a aprovação foi desfeita sem motivo.
    row.aprovado = None
    await session.commit()
    logger.info("portal_upload", creative_id=str(row.id), equipe=equipe, arquivos=entraram)
    # Sem o conjunto de visíveis, `roteiro_id` sai NULL — conservador de
    # propósito: o site recarrega a lista logo depois do envio, e é lá que o
    # link (se houver) aparece.
    return _linha_out(row)

@router.post("/roteiros/{roteiro_id}/entrega")
async def entregar_do_roteiro(
    roteiro_id: UUID,
    files: Annotated[list[UploadFile], File(...)],
    session: Annotated[AsyncSession, Depends(get_session)],
    equipe: Annotated[str, Depends(equipe_do_token)],
) -> dict[str, Any]:
    """A agência entrega o vídeo A PARTIR do roteiro, e o vínculo nasce junto.

    Por que esta rota existe, e não só a de anexar numa linha já aberta: em
    22/09/2026, 5 dos 49 criativos aprovados tinham `roteiro_id` preenchido.
    O elo entre o briefing e a peça que saiu dele é o dado mais valioso da
    operação — é ele que diz qual roteiro virou criativo aprovado — e ele
    dependia de alguém lembrar de escolher a linha certa. Aqui não depende:
    quem entrega já está dentro do roteiro, então o vínculo é consequência.

    A linha nasce PENDENTE (`aprovado=None`), igual ao caminho de cima. Quem
    aprova continua sendo a equipe interna; entregar não é aprovar.
    """
    roteiro = (
        await session.execute(
            select(MarketingRoteiro).where(
                MarketingRoteiro.id == roteiro_id, *_visivel_pra_fora(equipe)
            )
        )
    ).scalar_one_or_none()
    # Mesmo filtro da listagem e da rota de bytes: roteiro desligado, sem texto
    # ou endereçado a outra agência não existe pra quem está do lado de fora —
    # e não existir tem que significar a mesma coisa nas três portas.
    if roteiro is None:
        raise HTTPException(404, detail={"code": "roteiro_nao_encontrado"})
    if not files:
        raise HTTPException(400, detail={"code": "sem_arquivo"})
    if len(files) > MAX_FILES_PER_ROW:
        raise HTTPException(400, detail={"code": "muitos_arquivos"})

    # `marca` e `sku` descem do roteiro em vez de virem do formulário: o
    # briefing já sabe de que produto está falando, e deixar a agência digitar
    # isso de novo é criar divergência entre a linha e o roteiro que a gerou.
    row = MarketingCreative(
        id=uuid4(),
        modelo=roteiro.titulo[:190],
        marca=roteiro.marca,
        marca_id=roteiro.marca_id,
        sku=roteiro.sku,
        product_id=roteiro.product_id,
        equipe=equipe,
        roteiro_id=roteiro.id,
        aprovado=None,
        # A coleção nasce explícita: sem isto, o primeiro acesso depois do
        # flush trata `files` como relação ainda não carregada e tenta ir ao
        # banco de dentro do laço síncrono de gravação — MissingGreenlet.
        files=[],
    )
    session.add(row)
    await session.flush()

    entraram = _gravar_arquivos(row, files)
    await session.commit()
    logger.info(
        "portal_entrega_do_roteiro",
        creative_id=str(row.id),
        roteiro_id=str(roteiro.id),
        equipe=equipe,
        arquivos=entraram,
    )
    return _linha_out(row)
