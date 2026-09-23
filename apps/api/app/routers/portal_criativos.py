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
from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.db import get_session
from app.models.marketing import MarketingCreative, MarketingCreativeFile
from app.models.marketing_personagem import MarketingPersonagem, MarketingPersonagemArquivo
from app.models.marketing_personagem_requisicao import (
    STATUS_PENDENTE,
    MarketingPersonagemRequisicao,
)
from app.models.marketing_roteiro import MarketingRoteiro, MarketingRoteiroRef
from app.routers.marketing_creatives import (
    MAX_BYTES_ARQUIVO,
    MAX_FILES_PER_ROW,
    _file_dir,
)
from app.services.marketing.anexos import (
    MIMES_PERSONAGEM,
    MIMES_REFERENCIA,
    MIMES_VIDEO,
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
        # Sai daqui porque a versão herda o título do original quando a agência
        # não escreve um: sem este campo, a aba Roteiros do portal mostrava dois
        # cards com título, marca e SKU idênticos e nada dizendo qual é qual.
        # Não vaza nada: só quem enxerga a ideia consegue criar versão dela.
        "origem_id": str(row.origem_id) if row.origem_id else None,
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


@router.get("/personagens/requisicoes")
async def listar_minhas_requisicoes(
    session: Annotated[AsyncSession, Depends(get_session)],
    equipe: Annotated[str, Depends(equipe_do_token)],
) -> dict[str, Any]:
    """Os pedidos DESTA agência, com o veredito e o motivo da recusa.

    Sem esta rota a recusa era um buraco: o motivo ficava guardado no banco e
    quem pediu nunca lia — então o mesmo personagem voltava na semana seguinte,
    igual, e alguém gastava o mesmo tempo de novo. Guardar o porquê só vale se
    o porquê chegar de volta.

    Declarada ANTES de `/personagens/{personagem_id}` de propósito: o FastAPI
    casa na ordem, e depois dela "requisicoes" viraria um UUID inválido — 422
    em vez da fila.
    """
    linhas = (
        (
            await session.execute(
                select(MarketingPersonagemRequisicao)
                .where(MarketingPersonagemRequisicao.equipe == equipe)
                .order_by(MarketingPersonagemRequisicao.created_at.desc())
                .limit(LIMITE_PADRAO)
            )
        )
        .scalars()
        .all()
    )
    return {
        "equipe": equipe,
        "requisicoes": [
            {
                "id": str(r.id),
                "nome": r.nome,
                "descricao": r.descricao,
                "status": r.status,
                "motivo": r.motivo,
                "criado_em": r.created_at.isoformat() if r.created_at else None,
                "decidido_em": r.decidido_em.isoformat() if r.decidido_em else None,
            }
            for r in linhas
        ],
    }


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
    rec: MarketingRoteiroRef | MarketingPersonagemArquivo | MarketingCreativeFile,
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

    # O vínculo aponta pro BRIEFING DA CASA, não pro texto que a agência
    # escreveu em cima dele. A rota existe para responder "qual ideia virou
    # criativo aprovado"; se a entrega feita a partir de uma versão apontasse
    # pra versão, a resposta seria o texto da própria agência e a pergunta
    # ficaria sem dono. Entregando pela ideia direto, `origem_id` é None e isto
    # é o mesmo `roteiro.id` de sempre.
    raiz_id = roteiro.origem_id or roteiro.id

    # `_sincronizar_entregas` (marketing_roteiros.py) já abre uma linha VAZIA
    # por agência endereçada assim que o briefing fica visível. Criar outra
    # aqui deixava duas linhas da mesma agência pro mesmo roteiro — uma com o
    # vídeo e uma vazia que nunca some, as duas contadas em "em análise".
    # Reaproveitar a vazia é o que faz os dois caminhos de entrega
    # concordarem. Linha que já tem arquivo NÃO é reaproveitada: a segunda
    # entrega é entrega de verdade, não engano.
    row = (
        await session.execute(
            select(MarketingCreative)
            .where(
                MarketingCreative.roteiro_id == raiz_id,
                func.lower(func.coalesce(MarketingCreative.equipe, "")) == equipe.lower(),
                MarketingCreative.aprovado.is_(None),
            )
            .order_by(MarketingCreative.created_at)
        )
    ).scalars().first()
    if row is not None and row.files:
        row = None

    if row is None:
        # `marca` e `sku` descem do roteiro em vez de virem do formulário: o
        # briefing já sabe de que produto está falando, e deixar a agência
        # digitar isso de novo cria divergência entre a linha e o roteiro.
        row = MarketingCreative(
            id=uuid4(),
            modelo=roteiro.titulo[:190],
            marca=roteiro.marca,
            marca_id=roteiro.marca_id,
            sku=roteiro.sku,
            product_id=roteiro.product_id,
            equipe=equipe,
            roteiro_id=raiz_id,
            aprovado=None,
            # A coleção nasce explícita: sem isto, o primeiro acesso depois do
            # flush trata `files` como relação ainda não carregada e tenta ir
            # ao banco de dentro do laço síncrono de gravação — MissingGreenlet.
            files=[],
        )
        session.add(row)
    await session.flush()

    entraram = _gravar_arquivos(row, files)
    await session.commit()
    logger.info(
        "portal_entrega_do_roteiro",
        creative_id=str(row.id),
        roteiro_id=str(raiz_id),
        veio_da_versao=str(roteiro.id) if roteiro.origem_id else None,
        equipe=equipe,
        arquivos=entraram,
    )
    # Sem os visíveis, `_linha_out` devolvia `roteiro_id: null` — a resposta
    # escondia justamente o vínculo que esta rota existe para criar. O mesmo
    # WHERE da listagem decide, então um briefing desligado no meio do caminho
    # continua vindo nulo, como em toda outra tela.
    return _linha_out(row, await _roteiros_visiveis(session, [row], equipe))


class VersaoIn(BaseModel):
    """O texto da agência. Título é opcional: sem ele, herda o do original."""

    texto: str
    titulo: str | None = None


@router.post("/roteiros/{roteiro_id}/versao")
async def criar_versao(
    roteiro_id: UUID,
    payload: VersaoIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    equipe: Annotated[str, Depends(equipe_do_token)],
) -> dict[str, Any]:
    """A agência escreve a versão dela. O original não é tocado.

    Roteiro virou ideia, e ideia se discute — mas deixar a agência reescrever o
    campo apagaria o "antes". Sem o par original/versão, some a forma de saber
    se a ideia que a casa propôs prestava, que é a mesma comparação que o
    `aprovado` do criativo existe para permitir. Some também qualquer defesa
    contra dois lados salvando o mesmo `texto`: a tabela não tem versionamento,
    e último a salvar ganharia em silêncio.

    A versão nasce endereçada SÓ à agência que a escreveu. A ideia da casa
    continua valendo para as duas; a leitura que uma delas fez é dela.
    """
    original = (
        await session.execute(
            select(MarketingRoteiro).where(
                MarketingRoteiro.id == roteiro_id, *_visivel_pra_fora(equipe)
            )
        )
    ).scalar_one_or_none()
    if original is None:
        raise HTTPException(404, detail={"code": "roteiro_nao_encontrado"})

    texto = (payload.texto or "").strip()
    if not texto:
        raise HTTPException(400, detail={"code": "texto_obrigatorio"})
    # Versão de versão vira corrente sem fim e ninguém acha mais o começo: a
    # origem aponta sempre para a ideia de partida.
    raiz = original.origem_id or original.id

    row = MarketingRoteiro(
        id=uuid4(),
        titulo=(payload.titulo or original.titulo)[:160],
        texto=texto,
        marca=original.marca,
        marca_id=original.marca_id,
        sku=original.sku,
        product_id=original.product_id,
        equipe_destino=equipe,
        origem_id=raiz,
        ativo=True,
    )
    session.add(row)
    await session.commit()
    logger.info(
        "portal_versao_de_roteiro",
        roteiro_id=str(row.id),
        origem_id=str(raiz),
        equipe=equipe,
    )
    return {"id": str(row.id), "origem_id": str(raiz), "titulo": row.titulo}


class RequisicaoIn(BaseModel):
    """Proposta de personagem vinda da agência.

    Os dois campos de origem são obrigatórios no schema, e não só no banco, para
    o erro chegar como 422 explicando o que falta — e não como 500 de constraint.
    """

    nome: str
    descricao: str | None = None
    justificativa: str | None = None
    origem_imagem: str
    origem_voz: str
    cessao: bool = False
    cessao_obs: str | None = None


@router.post("/personagens/requisicao", status_code=201)
async def requisitar_personagem(
    payload: RequisicaoIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    equipe: Annotated[str, Depends(equipe_do_token)],
) -> dict[str, Any]:
    """A agência PROPÕE um personagem. Ninguém é criado aqui.

    O personagem só nasce quando alguém de dentro aprova — e a requisição existe
    justamente para que esse alguém veja a procedência antes de dizer sim.
    Súmula 403 do STJ: uso comercial de imagem de pessoa basta para indenizar,
    sem prova de prejuízo. Rosto sem origem declarada não deveria nem chegar à
    mesa de quem decide.

    O ARQUIVO não sobe por aqui de propósito: quem guarda a foto e o MP3 é quem
    responde por eles. A casa carrega os arquivos depois de aprovar, junto com o
    papel da cessão.
    """
    nome = payload.nome.strip()
    if not nome:
        raise HTTPException(400, detail={"code": "nome_obrigatorio"})
    for campo in ("origem_imagem", "origem_voz"):
        if not (getattr(payload, campo) or "").strip():
            raise HTTPException(400, detail={"code": f"{campo}_obrigatorio"})

    # Nome repetido em personagem já é erro no cadastro interno; aqui a conferência
    # é para a agência saber na hora, em vez de esperar a recusa de alguém.
    existe = (
        await session.execute(
            select(MarketingPersonagem.id).where(
                func.lower(MarketingPersonagem.nome) == nome.lower()
            )
        )
    ).first()
    if existe is not None:
        raise HTTPException(409, detail={"code": "personagem_ja_existe"})

    row = MarketingPersonagemRequisicao(
        id=uuid4(),
        nome=nome,
        descricao=(payload.descricao or "").strip() or None,
        justificativa=(payload.justificativa or "").strip() or None,
        origem_imagem=payload.origem_imagem.strip(),
        origem_voz=payload.origem_voz.strip(),
        cessao=bool(payload.cessao),
        cessao_obs=(payload.cessao_obs or "").strip() or None,
        equipe=equipe,
        status=STATUS_PENDENTE,
    )
    session.add(row)
    await session.commit()
    logger.info("portal_requisicao_personagem", requisicao_id=str(row.id), equipe=equipe)
    return {"id": str(row.id), "status": row.status}


# ───────────────────── vídeos de referência ─────────────────────


@router.get("/referencias")
async def listar_referencias(
    session: Annotated[AsyncSession, Depends(get_session)],
    equipe: Annotated[str, Depends(equipe_do_token)],
) -> dict[str, Any]:
    """Os vídeos JÁ APROVADOS, das duas agências, como material de referência.

    Eduardo, 23/09/2026: "uma aba de referências e taca alguns vídeos do DaVinci
    lá também pra eles terem referências (...) pode ser dos aprovados, por
    enquanto só de todos".

    É a única rota daqui que atravessa a fronteira de equipe de propósito — e
    ela só atravessa o que a casa JÁ APROVOU. Aprovado é o que a casa assinou
    embaixo, então é exatamente o que serve de exemplo; pendente e reprovado
    continuam invisíveis do lado de fora.

    `equipe` NÃO sai na resposta. Quem fez não ajuda a aprender com a peça, e
    nomear o autor transformaria a aba em placar entre duas agências que
    competem. O dia em que a casa quiser dar crédito, o campo entra aqui.
    """
    linhas = (
        (
            await session.execute(
                select(MarketingCreative)
                .options(selectinload(MarketingCreative.files))
                .where(MarketingCreative.aprovado.is_(True))
                .order_by(MarketingCreative.created_at.desc())
                .limit(LIMITE_PADRAO)
            )
        )
        .scalars()
        .all()
    )

    saida = []
    for row in linhas:
        videos = [f for f in row.files if (f.file_mime or "").lower() in MIMES_VIDEO]
        # Linha aprovada sem vídeo tocável não é referência, é ruído na grade.
        if not videos:
            continue
        saida.append(
            {
                "id": str(row.id),
                "modelo": row.modelo,
                "marca": row.marca,
                "sku": row.sku,
                "criado_em": row.created_at.isoformat() if row.created_at else None,
                "arquivos": [
                    {
                        "id": str(f.id),
                        "nome": f.file_name,
                        "mime": f.file_mime,
                        "tamanho": f.file_size,
                    }
                    for f in videos
                ],
            }
        )
    return {"equipe": equipe, "referencias": saida}


@router.get("/referencias/{creative_id}/arquivo/{file_id}")
async def baixar_referencia_video(
    creative_id: UUID,
    file_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    equipe: Annotated[str, Depends(equipe_do_token)],
    download: bool = False,
) -> FileResponse:
    """Os bytes do vídeo de referência.

    A trava é a MESMA da listagem — só criativo aprovado — e está repetida aqui
    de propósito: se esta rota tivesse um WHERE próprio, tirar a aprovação de um
    vídeo sumiria com o card e continuaria servindo os bytes para sempre a quem
    tivesse anotado o id. É o mesmo raciocínio do `_visivel_pra_fora`.
    """
    row = (
        await session.execute(
            select(MarketingCreative)
            .options(selectinload(MarketingCreative.files))
            .where(MarketingCreative.id == creative_id, MarketingCreative.aprovado.is_(True))
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, detail={"code": "nao_encontrado"})
    rec = next((f for f in row.files if f.id == file_id), None)
    if rec is None or (rec.file_mime or "").lower() not in MIMES_VIDEO:
        raise HTTPException(404, detail={"code": "nao_encontrado"})
    return _entrega(rec, permitidos=MIMES_VIDEO, baixar=download)


# ──────────────── vídeo autoral: nasce pronto e vai pra revisão ────────────────


@router.post("/criativos/proposta")
async def propor_video(
    files: Annotated[list[UploadFile], File(...)],
    session: Annotated[AsyncSession, Depends(get_session)],
    equipe: Annotated[str, Depends(equipe_do_token)],
    titulo: Annotated[str, Form()],
    conceito: Annotated[str, Form()] = "",
    marca: Annotated[str, Form()] = "",
    sku: Annotated[str, Form()] = "",
) -> dict[str, Any]:
    """A agência publica um vídeo DELA, com o conceito junto, e cai na revisão.

    Eduardo, 23/09/2026: "era pra ser mais um publicar vídeo para análise, que
    aí eles preenchiam e já poderiam enviar o vídeo, e esse iria para revisão
    direto".

    Não há fila nova: a linha nasce `aprovado=None`, que é exatamente a fila de
    revisão que já existe na aba Criativos — com aprovar, recusar e feedback
    prontos. Inventar uma segunda fila para a mesma pergunta ("este vídeo
    presta?") seria dois lugares para alguém esquecer de olhar.

    O CONCEITO vira um roteiro ligado por `roteiro_id`. É o que o modelo diz
    que esse campo é — "o briefing que esta linha cumpre" — e resolve de uma
    vez duas coisas: quem revisa lê a intenção ao lado do vídeo, e o elo
    roteiro→criativo (5 de 49 em 22/09) passa a nascer preenchido também no
    caminho autoral. `marketing_creatives.roteiro` NÃO é usada: está deprecada
    desde a 0299 e ninguém lê dela.
    """
    titulo = (titulo or "").strip()
    if not titulo:
        raise HTTPException(400, detail={"code": "titulo_obrigatorio"})
    if not files:
        raise HTTPException(400, detail={"code": "sem_arquivo"})
    if len(files) > MAX_FILES_PER_ROW:
        raise HTTPException(400, detail={"code": "muitos_arquivos"})

    conceito = (conceito or "").strip()
    marca_txt = (marca or "").strip() or None
    sku_txt = (sku or "").strip() or None

    briefing = None
    if conceito:
        # `ativo=False`, e este é o ponto: o vídeo ainda não foi revisado, então
        # o conceito dele não pode entrar na lista como briefing valendo. Nascia
        # ligado e aparecia na aba Ideias da própria agência no mesmo instante,
        # ao lado do que a casa escreveu — como se já tivesse sido aceito.
        #
        # Desligado, ele existe para quem revisa (a linha do criativo aponta
        # para cá e a tela mostra o texto ao lado do vídeo) e para o registro.
        # Se a casa quiser adotar o conceito como briefing de verdade, liga o
        # olho — um clique, deliberado. Aprovar um VÍDEO não é a mesma decisão
        # que adotar a IDEIA dele para os próximos.
        briefing = MarketingRoteiro(
            id=uuid4(),
            titulo=titulo[:160],
            texto=conceito,
            marca=marca_txt,
            sku=sku_txt,
            equipe_destino=equipe,
            ativo=False,
        )
        session.add(briefing)
        await session.flush()

    row = MarketingCreative(
        id=uuid4(),
        modelo=titulo[:190],
        marca=marca_txt,
        sku=sku_txt,
        equipe=equipe,
        roteiro_id=briefing.id if briefing else None,
        aprovado=None,
        files=[],
    )
    session.add(row)
    await session.flush()

    entraram = _gravar_arquivos(row, files)
    await session.commit()
    logger.info(
        "portal_video_autoral",
        creative_id=str(row.id),
        roteiro_id=str(briefing.id) if briefing else None,
        equipe=equipe,
        arquivos=entraram,
    )
    return _linha_out(row, await _roteiros_visiveis(session, [row], equipe))
