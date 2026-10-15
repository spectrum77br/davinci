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

## A única escrita de briefing: ideia NOVA (13/10/2026)

Marco, 13/10/2026, pensando no Claude conectado ao portal: "ela lê, pode gerar
novas?". `POST /roteiros` cria uma ideia — nunca edita nem apaga uma que já
existe (para isso continua valendo a versão, `POST /roteiros/{id}/versao`). A
ideia nasce endereçada à agência do token; "as duas" (`equipe_destino` NULL)
só quando o portal pede, e quem decide se a pessoa pode pedir é o portal (só o
login admin, que enxerga as duas agências).
"""

from __future__ import annotations

import re
import secrets
import statistics
import unicodedata
from collections import Counter
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from pathlib import Path
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

import structlog
from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    Response,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import Text, func, literal, or_, select
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.db import get_session
from app.models.marca import Marca
from app.models.marketing import MarketingCreative, MarketingCreativeFile
from app.models.marketing_ideia_requisicao import MarketingIdeiaRequisicao
from app.models.marketing_personagem import MarketingPersonagem, MarketingPersonagemArquivo
from app.models.marketing_personagem_requisicao import (
    STATUS_PENDENTE,
    MarketingPersonagemRequisicao,
)
from app.models.marketing_postagem import STATUS_PUBLICADO, MarketingPostagem
from app.models.marketing_roteiro import (
    MarketingRoteiro,
    MarketingRoteiroPersonagem,
    MarketingRoteiroRef,
)
from app.models.pricing import PricingProduct
from app.models.product import Product
from app.models.segment import Segment
from app.routers.marketing_creatives import (
    MAX_BYTES_ARQUIVO,
    MAX_FILES_PER_ROW,
    _file_dir,
)
from app.services.marketing.anexos import (
    _EXT_IMAGEM,
    MIMES_IMAGEM,
    MIMES_PERSONAGEM,
    MIMES_REFERENCIA,
    MIMES_VIDEO,
    caminho_confinado,
    mime_da_extensao,
    mime_seguro,
)
from app.services.marketing.anexos import disposicao_segura as _disposicao
from app.services.marketing.medidas_video import eh_video_sql, medir_arquivos
from app.services.marketing.vinculos import _marca_id_do_texto, _product_id_do_sku
from app.services.mega_fotos import MegaError, eh_de_embalagens, sidecar_bytes, sidecar_request
from app.services.rate_limit import RateLimitError, sliding_window_check

logger = structlog.get_logger()

router = APIRouter(prefix="/api/portal", tags=["portal-criativos"])

# Teto do que uma listagem devolve. Agência com muito histórico não derruba a
# chamada nem entrega o catálogo inteiro de uma vez.
LIMITE_PADRAO = 200

# As IDEIAS têm teto próprio, bem mais alto. Em 13/10/2026 havia 264 ideias
# visíveis e a lista cortava em 200: as 64 mais antigas simplesmente não
# existiam no portal, sem aviso nenhum. O padrão cobre o catálogo inteiro com
# folga; quem quiser página pede `limite`/`offset`, e o total vem no
# cabeçalho `X-Total-Count`.
LIMITE_ROTEIROS = 2000
LIMITE_ROTEIROS_MAX = 5000
# `resumo=1`: o texto da ideia cortado para o card da lista. O texto inteiro
# chega a milhares de caracteres e a lista só mostra o começo.
RESUMO_CARACTERES = 300

# Ideia nova vinda do portal (e do Claude conectado a ele): um teto por
# agência, para um laço errado não encher a aba Roteiros de lixo.
LIMITE_IDEIAS_MIN = 30
MAX_PERSONAGENS_IDEIA = 5
TITULO_MAX = 160
TEXTO_MAX = 20000

# O gerador informado no envio (migration 0393). Texto livre: a lista de IAs
# muda toda semana, e quem mantém o select é o portal.
IA_MODELO_MAX = 64
# NUMERIC(10,2): até 99.999.999,99.
_IA_CREDITOS_TETO = Decimal("100000000")

# Caractere de controle no texto que vem de fora. O \x00 derrubava o INSERT em
# 500 (o Postgres não guarda NUL em texto) — e, na ideia nova, depois de gastar
# uma das 30 vagas do minuto. Os outros não têm o que fazer num briefing.
# Quebra de linha e tabulação são texto e passam.
_CONTROLE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


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
        # Sai para a tela saber se dá pra TOCAR: sem isto o portal não
        # consegue distinguir vídeo de qualquer outro anexo.
        "mime": f.file_mime,
        "tamanho": f.file_size,
        "enviado_em": f.created_at.isoformat() if f.created_at else None,
        # A IA que a agência declarou para ESTE vídeo (migration 0393).
        "ia_modelo": f.ia_modelo,
        "ia_creditos": float(f.ia_creditos) if f.ia_creditos is not None else None,
    }


def _ia_da_linha(row: MarketingCreative) -> MarketingCreativeFile | None:
    """O último envio da linha em que a agência declarou a IA.

    A IA mora no arquivo; a linha mostra a do envio mais recente que disse
    alguma coisa. Reenviar sem preencher não apaga da tela o que a agência já
    tinha dito — o vídeo novo só fica sem IA no relatório, que é por arquivo.
    `files` vem na ordem do envio (o relacionamento ordena por `created_at`
    e o arquivo novo entra no fim da lista).
    """
    return next(
        (f for f in reversed(row.files) if f.ia_modelo is not None or f.ia_creditos is not None),
        None,
    )


def _linha_out(
    row: MarketingCreative, visiveis: frozenset[UUID] = frozenset()
) -> dict[str, Any]:
    """LISTA BRANCA. Campo novo no modelo não vaza sozinho por aqui."""
    ia = _ia_da_linha(row)
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
        # O que a PRÓPRIA agência declarou no envio (migration 0393) — volta
        # para ela ver o que informou. É a do último envio que declarou
        # (`_ia_da_linha`); cada vídeo tem a sua em `arquivos`. NULL = não
        # informado.
        "ia_modelo": ia.ia_modelo if ia else None,
        "ia_creditos": float(ia.ia_creditos) if ia and ia.ia_creditos is not None else None,
        "criado_em": row.created_at.isoformat() if row.created_at else None,
    }


def _ia_do_envio(
    ia_modelo: str | None, ia_creditos: str | None
) -> tuple[str | None, Decimal | None]:
    """Os dois campos OPCIONAIS de IA do formulário de envio: vazio = NULL.

    Conferidos ANTES de gravar o vídeo em disco: recusar depois deixaria
    arquivo órfão (o mesmo cuidado do "outro personagem" do vídeo autoral).
    Crédito aceita vírgula — é o que a pessoa digita no Brasil.
    """
    modelo = (ia_modelo or "").strip() or None
    if modelo is not None and (len(modelo) > IA_MODELO_MAX or _CONTROLE.search(modelo)):
        raise HTTPException(400, detail={"code": "ia_modelo_invalido", "max": IA_MODELO_MAX})
    bruto = (ia_creditos or "").strip().replace(",", ".")
    if not bruto:
        return modelo, None
    try:
        creditos = Decimal(bruto)
    except InvalidOperation:
        raise HTTPException(400, detail={"code": "ia_creditos_invalido"}) from None
    # "Até 2 casas": 12,5 e 12,50 passam; 12,555 não — arredondar calado
    # gravaria um número que ninguém digitou.
    if (
        not creditos.is_finite()
        or creditos < 0
        or creditos >= _IA_CREDITOS_TETO
        or creditos != creditos.quantize(Decimal("0.01"))
    ):
        raise HTTPException(400, detail={"code": "ia_creditos_invalido"})
    # `+ 0` tira o sinal de "-0", que o Decimal guarda.
    return modelo, creditos.quantize(Decimal("0.01")) + Decimal("0.00")


def _aplica_ia(
    row: MarketingCreative, entraram: list[str], modelo: str | None, creditos: Decimal | None
) -> None:
    """A IA declarada vai para os vídeos DESTE envio, e só para eles.

    Gravada na linha, o reenvio pela aba Entregas (que anexa à linha que já
    tem vídeo) trocava a IA do vídeo antigo pela do novo e repetia os
    créditos nos dois — o relatório, que é por arquivo, contava errado.
    """
    for f in row.files:
        if f.file_name in entraram:
            f.ia_modelo = modelo
            f.ia_creditos = creditos


async def _medir_os_novos(row: MarketingCreative, entraram: list[str]) -> None:
    """Duração e resolução dos vídeos que acabaram de entrar (migration 0393).

    No mesmo commit do arquivo. `medir_arquivos` nunca levanta: ffprobe que
    falha deixa NULL e a entrega segue — medir é para o relatório, não é
    condição de envio.
    """
    await medir_arquivos(f for f in row.files if f.file_name in entraram)


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


def _roteiro_out(
    row: MarketingRoteiro,
    entrega: dict[str, Any] | None = None,
    *,
    resumo: bool = False,
) -> dict[str, Any]:
    """LISTA BRANCA. `created_by` e `file_rel` não são da conta de terceiro.

    `entrega` vem pronta de `_entregas_por_ideia` (uma consulta para a página
    inteira); None = esta agência ainda não mandou vídeo para a ideia.
    """
    texto = row.texto
    if resumo and texto and len(texto) > RESUMO_CARACTERES:
        texto = texto[:RESUMO_CARACTERES].rstrip() + "…"
    return {
        "id": str(row.id),
        # Sai daqui porque a versão herda o título do original quando a agência
        # não escreve um: sem este campo, a aba Roteiros do portal mostrava dois
        # cards com título, marca e SKU idênticos e nada dizendo qual é qual.
        # Não vaza nada: só quem enxerga a ideia consegue criar versão dela.
        "origem_id": str(row.origem_id) if row.origem_id else None,
        # O texto veio de FORA da casa: ideia nova criada pelo portal
        # (`criado_pela_equipe`) ou versão escrita por uma agência
        # (`origem_id`). O Claude conectado ao portal lê as ideias das duas
        # agências no login admin, e precisa saber qual texto é de terceiro
        # para tratá-lo como dado, não como instrução. Booleano e não o nome
        # da agência: quem fez não sai para a outra (o mesmo cuidado das
        # Referências).
        "escrita_no_portal": row.criado_pela_equipe is not None or row.origem_id is not None,
        "titulo": row.titulo,
        "texto": texto,
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
        # "Esta ideia já foi feita?" — a pergunta que a agência fazia de cabeça
        # ao abrir a lista de ideias. Só da agência do TOKEN: a entrega da
        # outra agência não é da conta desta (as duas competem pelo mesmo
        # briefing).
        "entrega": entrega,
    }


def _raiz(row: MarketingRoteiro) -> UUID:
    """A ideia da casa por trás da linha. A entrega feita a partir de uma
    versão aponta para a RAIZ (`entregar_do_roteiro`), então é pela raiz que
    se pergunta "já foi feita"."""
    return row.origem_id or row.id


def _estado_da_entrega(aprovados: int, pendentes: int) -> str:
    """Um selo só por ideia: aprovado vence (a casa já assinou embaixo de um
    vídeo dela); senão, havendo vídeo esperando olhar, está em análise; senão,
    tudo o que foi mandado foi recusado."""
    if aprovados:
        return "aprovado"
    if pendentes:
        return "em_analise"
    return "recusado"


async def _entregas_por_ideia(
    session: AsyncSession, raizes: set[UUID], equipe: str
) -> dict[UUID, dict[str, Any]]:
    """O resumo da entrega DESTA agência por ideia raiz — uma consulta só.

    Conta só linha COM arquivo: a linha vazia que `_sincronizar_entregas`
    abria em 22/09 é entrega aberta, não vídeo mandado. `videos` é o número de
    arquivos dessas linhas (o portal só envia vídeo; em 13/10/2026 eram 181
    arquivos, todos video/mp4). "Publicado" é ter postagem que de fato SAIU
    (status publicado) — agendada, cancelada ou falha não é vídeo no ar.
    """
    if not raizes:
        return {}
    publicada = (
        select(MarketingPostagem.id)
        .where(
            MarketingPostagem.creative_id == MarketingCreative.id,
            MarketingPostagem.status == STATUS_PUBLICADO,
        )
        .exists()
    )
    linhas = (
        await session.execute(
            select(
                MarketingCreative.roteiro_id,
                MarketingCreative.aprovado,
                func.count(MarketingCreativeFile.id),
                func.max(MarketingCreativeFile.created_at),
                publicada,
            )
            .join(MarketingCreativeFile, MarketingCreativeFile.creative_id == MarketingCreative.id)
            .where(MarketingCreative.roteiro_id.in_(raizes), _da_equipe(equipe))
            .group_by(MarketingCreative.id)
        )
    ).all()
    soma: dict[UUID, dict[str, Any]] = {}
    for roteiro_id, aprovado, arquivos, ultimo, publicado in linhas:
        s = soma.setdefault(
            roteiro_id,
            {"aprovados": 0, "pendentes": 0, "videos": 0, "ultimo": None, "publicado": False},
        )
        if aprovado is True:
            s["aprovados"] += 1
        elif aprovado is None:
            s["pendentes"] += 1
        s["videos"] += int(arquivos or 0)
        if ultimo is not None and (s["ultimo"] is None or ultimo > s["ultimo"]):
            s["ultimo"] = ultimo
        s["publicado"] = s["publicado"] or bool(publicado)
    return {
        rid: {
            "estado": _estado_da_entrega(s["aprovados"], s["pendentes"]),
            "videos": s["videos"],
            "ultimo_em": s["ultimo"].isoformat() if s["ultimo"] else None,
            "publicado": s["publicado"],
        }
        for rid, s in soma.items()
    }


def _entregue_pela_equipe(equipe: str, *extra):
    """EXISTS correlacionado: esta agência mandou vídeo para a ideia raiz da
    linha de `marketing_roteiros` da consulta de fora. É o filtro de situação
    da lista — a mesma regra do `_entregas_por_ideia`, no WHERE, para o total
    e a paginação baterem com o que a tela mostra."""
    tem_arquivo = (
        select(MarketingCreativeFile.id)
        .where(MarketingCreativeFile.creative_id == MarketingCreative.id)
        .exists()
    )
    return (
        select(MarketingCreative.id)
        .where(
            MarketingCreative.roteiro_id
            == func.coalesce(MarketingRoteiro.origem_id, MarketingRoteiro.id),
            _da_equipe(equipe),
            tem_arquivo,
            *extra,
        )
        .correlate(MarketingRoteiro)
        .exists()
    )


def _filtro_de_situacao(situacao: str, equipe: str):
    if situacao == "feitas":
        return _entregue_pela_equipe(equipe)
    if situacao == "a_fazer":
        return ~_entregue_pela_equipe(equipe)
    if situacao == "aprovadas":
        return _entregue_pela_equipe(equipe, MarketingCreative.aprovado.is_(True))
    if situacao == "em_analise":
        # Mesma precedência do selo: com um aprovado, a ideia é "aprovada",
        # mesmo que outro vídeo dela ainda espere análise.
        return _entregue_pela_equipe(equipe, MarketingCreative.aprovado.is_(None)) & ~(
            _entregue_pela_equipe(equipe, MarketingCreative.aprovado.is_(True))
        )
    return None


def _filtro_de_personagem(bruto: str | None):
    """`personagem_id`: o UUID de um personagem, ou "nenhum" (ideia sem
    personagem). Conta só personagem LIGADO — o desligado já não aparece no
    card (`_roteiro_out`), e filtrar por ele mostraria ideia "sem" a pessoa."""
    valor = (bruto or "").strip()
    if not valor:
        return None
    ligado = (
        select(MarketingRoteiroPersonagem.id)
        .join(
            MarketingPersonagem,
            MarketingPersonagem.id == MarketingRoteiroPersonagem.personagem_id,
        )
        .where(
            MarketingRoteiroPersonagem.roteiro_id == MarketingRoteiro.id,
            MarketingPersonagem.ativo.is_(True),
        )
    )
    if valor.lower() == "nenhum":
        return ~ligado.exists()
    try:
        alvo = UUID(valor)
    except ValueError:
        raise HTTPException(400, detail={"code": "personagem_invalido"}) from None
    return ligado.where(MarketingRoteiroPersonagem.personagem_id == alvo).exists()


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


Situacao = Literal["todas", "a_fazer", "feitas", "em_analise", "aprovadas"]


@router.get("/roteiros")
async def listar_roteiros(
    response: Response,
    session: Annotated[AsyncSession, Depends(get_session)],
    equipe: Annotated[str, Depends(equipe_do_token)],
    limite: Annotated[int, Query(ge=1, le=LIMITE_ROTEIROS_MAX)] = LIMITE_ROTEIROS,
    offset: Annotated[int, Query(ge=0)] = 0,
    personagem_id: Annotated[str | None, Query(max_length=64)] = None,
    situacao: Situacao = "todas",
    desde_dias: Annotated[int | None, Query(gt=0, le=36500)] = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
    resumo: bool = False,
) -> dict[str, Any]:
    """A tela de roteiros: o briefing endereçado a esta agência — mais os que
    não têm destinatário, que são de todas.

    Quem escreve o briefing da casa é a equipe interna, no DaVinci. Daqui só
    nasce versão (`/versao`) ou ideia NOVA (`POST /roteiros`); nenhuma rota
    deste arquivo edita ou apaga roteiro existente, de propósito.

    O corpo é o MESMO de antes de 13/10/2026 (o portal que está no ar precisa
    continuar lendo entre os dois deploys), mais o campo `entrega` em cada
    ideia. O total vai no cabeçalho `X-Total-Count`, não no corpo.

    Filtros, todos opcionais e combináveis:
    - `situacao`: a_fazer / feitas (esta agência mandou vídeo) / em_analise /
      aprovadas — a mesma regra do selo `entrega`;
    - `personagem_id`: UUID, ou "nenhum" para ideia sem personagem;
    - `desde_dias`: criadas nos últimos N dias;
    - `q`: busca em título, texto, marca e SKU, sem diferenciar maiúsculas.
    """
    filtros: list[Any] = list(_visivel_pra_fora(equipe))
    por_situacao = _filtro_de_situacao(situacao, equipe)
    if por_situacao is not None:
        filtros.append(por_situacao)
    por_personagem = _filtro_de_personagem(personagem_id)
    if por_personagem is not None:
        filtros.append(por_personagem)
    if desde_dias:
        desde = datetime.now(UTC) - timedelta(days=desde_dias)
        filtros.append(MarketingRoteiro.created_at >= desde)
    # Controle sai da busca em vez de recusar: um \x00 no termo derrubava a
    # consulta em 500, e o resto do que foi digitado ainda é uma busca.
    termo = _CONTROLE.sub("", q or "").strip()
    if termo:
        # `autoescape`: "50%" ou "dg_23" digitados na busca não viram curinga.
        filtros.append(
            or_(
                *(
                    col.icontains(termo, autoescape=True)
                    for col in (
                        MarketingRoteiro.titulo,
                        MarketingRoteiro.texto,
                        MarketingRoteiro.marca,
                        MarketingRoteiro.sku,
                    )
                )
            )
        )

    total = (
        await session.execute(select(func.count()).select_from(MarketingRoteiro).where(*filtros))
    ).scalar_one()
    linhas = (
        (
            await session.execute(
                select(MarketingRoteiro)
                .where(*filtros)
                # `id` desempata: sem ele, duas ideias criadas no mesmo instante
                # (o import) trocariam de página entre uma chamada e outra.
                .order_by(MarketingRoteiro.created_at.desc(), MarketingRoteiro.id.desc())
                .offset(offset)
                .limit(limite)
            )
        )
        .scalars()
        .all()
    )
    entregas = await _entregas_por_ideia(session, {_raiz(r) for r in linhas}, equipe)
    response.headers["X-Total-Count"] = str(total)
    return {
        "equipe": equipe,
        "roteiros": [_roteiro_out(r, entregas.get(_raiz(r)), resumo=resumo) for r in linhas],
    }


@router.get("/roteiros/{roteiro_id}")
async def ver_roteiro(
    roteiro_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    equipe: Annotated[str, Depends(equipe_do_token)],
) -> dict[str, Any]:
    """Um roteiro só — a tela de "abrir" do portal, e o link colável.

    Existe porque a listagem tem teto: procurar dentro da lista faria a tela
    de detalhe sumir pra quem já tem histórico.
    """
    row = await _roteiro_visivel(session, roteiro_id, equipe)
    entregas = await _entregas_por_ideia(session, {_raiz(row)}, equipe)
    return {"equipe": equipe, "roteiro": _roteiro_out(row, entregas.get(_raiz(row)))}


class IdeiaIn(BaseModel):
    """Ideia nova vinda do portal. Os tetos são os das colunas: estourar vira
    422 explicando o campo, e não 500 de constraint."""

    titulo: str = Field(max_length=TITULO_MAX)
    texto: str = Field(max_length=TEXTO_MAX)
    marca: str | None = Field(default=None, max_length=64)
    sku: str | None = Field(default=None, max_length=512)
    personagem_ids: list[UUID] = Field(default_factory=list, max_length=MAX_PERSONAGENS_IDEIA)
    # Quem pode pedir "todas" é decisão do PORTAL (só o login admin, que
    # enxerga as duas agências). Daqui, o token só sabe de uma equipe.
    para: Literal["minha_equipe", "todas"] = "minha_equipe"


async def _limite_de_ideias(equipe: str) -> None:
    """30 ideias por minuto por agência. Redis fora do ar deixa passar, como
    no estoque dos sites: o teto é contra laço errado, não é trava de
    segurança — e criar ideia não pode depender do Redis."""
    try:
        await sliding_window_check(
            key=f"portal_ideias:rl:{equipe.strip().lower()}",
            limit=LIMITE_IDEIAS_MIN,
            window_seconds=60,
        )
    except RateLimitError as e:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"code": "limite_de_ideias"},
            headers={"Retry-After": str(e.retry_after)},
        ) from None
    except Exception as e:  # noqa: BLE001 — Redis fora do ar não derruba a criação
        logger.warning("portal_ideias_rate_limit_indisponivel", equipe=equipe, err=str(e)[:120])


@router.post("/roteiros", status_code=201)
async def criar_ideia(
    payload: IdeiaIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    equipe: Annotated[str, Depends(equipe_do_token)],
) -> dict[str, Any]:
    """Ideia NOVA, escrita do lado de fora — pela pessoa no portal ou pelo
    Claude conectado a ele (Marco, 13/10/2026: "ela lê, pode gerar novas?").

    Nasce ligada (`ativo`) e com texto, então aparece na hora na lista de
    quem ela endereça — igual ao roteiro que a equipe interna salva com texto.
    `created_by` fica NULL: quem escreveu não tem conta no DaVinci. A agência
    do token fica GRAVADA em `criado_pela_equipe` (e no log,
    `portal_ideia_criada`): é o que separa, na aba Roteiros, texto de terceiro
    de briefing da casa.

    A marca e o produto se resolvem pelo texto, como na criação interna
    (`marketing_roteiros.criar`): é o `marca_id` que leva a entrega para a
    fila certa do robô de postagem.
    """
    # Antes do teto por minuto: texto que o banco recusaria não gasta vaga.
    for campo in ("titulo", "texto", "marca", "sku"):
        if _CONTROLE.search(getattr(payload, campo) or ""):
            raise HTTPException(400, detail={"code": "caractere_invalido", "campo": campo})
    await _limite_de_ideias(equipe)
    titulo = payload.titulo.strip()
    if not titulo:
        raise HTTPException(400, detail={"code": "titulo_obrigatorio"})
    texto = payload.texto.strip()
    if not texto:
        raise HTTPException(400, detail={"code": "texto_obrigatorio"})
    marca = (payload.marca or "").strip() or None
    sku = (payload.sku or "").strip() or None

    # Só personagem LIGADO: o desligado some do catálogo e do card, e ligá-lo
    # aqui criaria ideia pedindo um rosto que a agência não consegue baixar.
    ids = list(dict.fromkeys(payload.personagem_ids))
    if ids:
        ativos = set(
            (
                await session.execute(
                    select(MarketingPersonagem.id).where(
                        MarketingPersonagem.id.in_(ids), MarketingPersonagem.ativo.is_(True)
                    )
                )
            ).scalars()
        )
        faltam = [str(i) for i in ids if i not in ativos]
        if faltam:
            raise HTTPException(
                400, detail={"code": "personagem_nao_encontrado", "personagem_ids": faltam}
            )

    row = MarketingRoteiro(
        id=uuid4(),
        titulo=titulo,
        texto=texto,
        marca=marca,
        marca_id=await _marca_id_do_texto(session, marca),
        sku=sku,
        product_id=await _product_id_do_sku(session, sku),
        # NULL = as duas agências (a regra invertida — ver o docstring do model).
        equipe_destino=None if payload.para == "todas" else equipe,
        ativo=True,
        created_by=None,
        # A porta por onde o texto entrou. Com "todas" (que só o login admin
        # do portal pede) é o token que o portal usou, não necessariamente
        # quem escreveu — mas continua sendo texto que veio de fora da casa.
        criado_pela_equipe=equipe,
    )
    session.add(row)
    for pid in ids:
        session.add(MarketingRoteiroPersonagem(id=uuid4(), roteiro_id=row.id, personagem_id=pid))
    await session.commit()
    rid = row.id
    logger.info(
        "portal_ideia_criada",
        roteiro_id=str(rid),
        equipe=equipe,
        para=payload.para,
        personagens=len(ids),
    )
    # Relê do banco: os elos de personagem entraram por `session.add`, e sem
    # o expire o identity map devolveria a ideia com a coleção vazia (mesmo
    # tropeço do `ligar_personagem` da aba interna). O id sai ANTES do
    # expire: lido depois, ele mesmo iria ao banco fora do await.
    session.expire_all()
    nova = await _roteiro_visivel(session, rid, equipe)
    return {"equipe": equipe, "roteiro": _roteiro_out(nova, None)}


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
    ia_modelo: Annotated[str, Form()] = "",
    ia_creditos: Annotated[str, Form()] = "",
) -> dict[str, Any]:
    """Anexa vídeo numa linha da PRÓPRIA equipe.

    Mesmas guardas do caminho interno, e os mesmos tetos importados de lá pra
    não divergirem com o tempo: grava em streaming (nunca o arquivo inteiro na
    memória), aborta e apaga ao passar do teto, e o sha256 sai do mesmo fluxo
    de bytes — é ele que denuncia o mesmo vídeo publicado em duas marcas.

    `ia_modelo`/`ia_creditos`: opcionais (migration 0393), ver `_ia_do_envio`.
    """
    row = await _linha_da_equipe(session, creative_id, equipe)
    if row.pushed_at is not None:
        raise HTTPException(409, detail={"code": "ja_enviado_pro_mega"})
    if not files:
        raise HTTPException(400, detail={"code": "sem_arquivo"})
    if len(row.files) + len(files) > MAX_FILES_PER_ROW:
        raise HTTPException(400, detail={"code": "muitos_arquivos"})
    modelo, creditos = _ia_do_envio(ia_modelo, ia_creditos)

    entraram = _gravar_arquivos(row, files)
    await _medir_os_novos(row, entraram)
    _aplica_ia(row, entraram, modelo, creditos)

    # Arquivo novo volta a linha pra "pendente" — mesmo comportamento do
    # caminho interno. A agência precisa ver isso na tela dela, senão parece
    # que a aprovação foi desfeita sem motivo. A prioridade na fila do robô
    # cai junto, igual ao caminho interno.
    row.aprovado = None
    row.fila_posicao = None
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
    ia_modelo: Annotated[str, Form()] = "",
    ia_creditos: Annotated[str, Form()] = "",
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
    modelo, creditos = _ia_do_envio(ia_modelo, ia_creditos)

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
    # O roteiro normalmente já traz os dois ids (`marketing_roteiros` resolve
    # no salvamento), mas roteiro antigo, ou salvo antes de o anúncio existir,
    # pode ter o texto sem o id — e a linha vazia reaproveitada lá em cima
    # também. Sem isto a entrega caía na revisão igual aos cinco vídeos do
    # conceito próprio em 28/09/2026: "sem legenda" e fora da fila do robô de
    # postagem (ver `services/marketing/vinculos.py`, 29/09/2026). Só preenche
    # o que FALTA: o id que o roteiro já tem é vínculo escolhido por gente, e
    # não é trocado por um palpite tirado do texto.
    if row.marca_id is None:
        row.marca_id = await _marca_id_do_texto(session, row.marca)
    if row.product_id is None:
        row.product_id = await _product_id_do_sku(session, row.sku)
    # Depois da resolução, e não antes: as duas consultas acima disparariam o
    # autoflush e a linha nova sairia num INSERT seguido de UPDATE. Na linha
    # reaproveitada, que já está na sessão, o `add` não faz nada.
    session.add(row)
    await session.flush()

    entraram = _gravar_arquivos(row, files)
    await _medir_os_novos(row, entraram)
    _aplica_ia(row, entraram, modelo, creditos)
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

    # O NOME da marca, e não só o código gravado. O seletor de Criativos
    # mostra `nome` e grava `slug`, e os dois divergem onde a marca foi
    # renomeada: a Poofy virou "charlots" e continua gravando `poofy`. Sem
    # isto a agência lia "poofy" em vídeo da Charlot's, e o filtro por empresa
    # do portal precisaria de um de-para escrito à mão — que envelheceria na
    # próxima marca. Marca apagada ou código solto cai no próprio código.
    nomes = {
        (slug or "").lower(): nome
        for slug, nome in (await session.execute(select(Marca.slug, Marca.nome))).all()
    }

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
                "marca_nome": nomes.get((row.marca or "").lower(), row.marca),
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
    personagem_id: Annotated[str, Form()] = "",
    personagem_outro: Annotated[str, Form()] = "",
    ia_modelo: Annotated[str, Form()] = "",
    ia_creditos: Annotated[str, Form()] = "",
) -> dict[str, Any]:
    """A agência publica um vídeo DELA, com o conceito junto, e cai na revisão.

    Eduardo, 23/09/2026: "era pra ser mais um publicar vídeo para análise, que
    aí eles preenchiam e já poderiam enviar o vídeo, e esse iria para revisão
    direto".

    Não há fila nova: a linha nasce `aprovado=None`, que é exatamente a fila de
    revisão que já existe na aba Criativos — com aprovar, recusar e feedback
    prontos. Inventar uma segunda fila para a mesma pergunta ("este vídeo
    presta?") seria dois lugares para alguém esquecer de olhar.

    O CONCEITO, quando vem, abre um PEDIDO — não um roteiro. São duas perguntas
    diferentes sobre a mesma entrega: "este vídeo presta?" é o `aprovado` do
    criativo, e "esta ideia vira briefing nosso?" é a fila de pedidos. Fazer o
    conceito nascer roteiro punha texto de terceiro na lista do que a casa
    escreveu, sem ninguém ter decidido nada.
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
    modelo, creditos = _ia_do_envio(ia_modelo, ia_creditos)

    # Qual persona está na peça. Vem do elenco e é conferida contra ele: id
    # inventado no formulário não pode virar ponteiro no banco.
    #
    # Ou "outro": um personagem que NÃO está no elenco (pedido de 25/09/2026).
    # Aí a descrição passa a ser obrigatória — sem ela, a escolha ficaria
    # idêntica a "sem persona" no banco, e a casa não saberia que tem na peça
    # alguém fora do elenco aprovado. A conferência vem ANTES de criar a linha e de
    # gravar o vídeo em disco — recusar depois deixava arquivo órfão — e não
    # fica só no portal: a API é a última porta.
    persona = None
    outro = None
    escolhido = (personagem_id or "").strip()
    if escolhido == "outro":
        outro = (personagem_outro or "").strip()[:500]
        if not outro:
            raise HTTPException(400, detail={"code": "personagem_outro_obrigatorio"})
    elif escolhido:
        try:
            alvo = UUID(escolhido)
        except ValueError:
            raise HTTPException(400, detail={"code": "personagem_invalido"}) from None
        persona = await session.scalar(
            select(MarketingPersonagem.id).where(
                MarketingPersonagem.id == alvo, MarketingPersonagem.ativo.is_(True)
            )
        )
        if persona is None:
            raise HTTPException(404, detail={"code": "personagem_nao_encontrado"})

    # Texto E id, sempre juntos. Até 29/09/2026 esta linha gravava só o texto
    # livre: os cinco vídeos do conceito próprio de 28/09 (marca "uranyx", SKU
    # preenchido) entraram com `marca_id` e `product_id` NULL, apareceram
    # "sem legenda" na aba Criativos e o robô de postagem, que filtra por
    # `marca_id`, nunca os viu. É a mesma resolução do PATCH da aba interna.
    # Marca fora do cadastro não barra a entrega: a linha nasce com o id NULL
    # e a equipe corrige a célula na revisão — recusar aqui jogaria fora o
    # vídeo que a agência acabou de subir.
    row = MarketingCreative(
        id=uuid4(),
        modelo=titulo[:190],
        marca=marca_txt,
        marca_id=await _marca_id_do_texto(session, marca_txt),
        sku=sku_txt,
        product_id=await _product_id_do_sku(session, sku_txt),
        equipe=equipe,
        aprovado=None,
        files=[],
    )
    session.add(row)
    await session.flush()

    entraram = _gravar_arquivos(row, files)
    await _medir_os_novos(row, entraram)
    _aplica_ia(row, entraram, modelo, creditos)

    # O conceito NÃO vira roteiro aqui. A lista de roteiros é o que a casa
    # escreveu; texto que chega de fora não entra nela por chegada — nem
    # desligado, porque desligado ele ainda aparece na lista de quem escreve.
    # Vira pedido, e o roteiro nasce no sim.
    #
    # Pedido nasce se houver conceito OU persona OU outro personagem: dizer
    # "usei a Márcia" — ou "uma feirante criada só para este vídeo" — já é
    # informação que quem decide precisa, mesmo sem texto junto.
    if conceito or persona or outro:
        session.add(
            MarketingIdeiaRequisicao(
                id=uuid4(),
                titulo=titulo[:160],
                descricao=conceito,
                marca=marca_txt,
                sku=sku_txt,
                equipe=equipe,
                personagem_id=persona,
                personagem_outro=outro,
                creative_id=row.id,
            )
        )

    await session.commit()
    logger.info(
        "portal_video_autoral",
        creative_id=str(row.id),
        com_conceito=bool(conceito),
        equipe=equipe,
        arquivos=entraram,
    )
    return _linha_out(row, await _roteiros_visiveis(session, [row], equipe))


@router.get("/criativos/{creative_id}/arquivo/{file_id}")
async def baixar_entrega(
    creative_id: UUID,
    file_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    equipe: Annotated[str, Depends(equipe_do_token)],
    download: bool = False,
) -> FileResponse:
    """O vídeo que a PRÓPRIA agência entregou.

    Faltava: o portal listava nome e tamanho e não tinha como tocar o arquivo —
    quem entregou não conseguia rever o que mandou, nem conferir se subiu o
    corte certo depois de uma recusa.

    `_linha_da_equipe` decide quem vê: linha de outra agência responde 404, não
    403, porque do lado de fora o que não é seu não existe.
    """
    row = await _linha_da_equipe(session, creative_id, equipe)
    rec = next((f for f in row.files if f.id == file_id), None)
    if rec is None:
        raise HTTPException(404, detail={"code": "nao_encontrado"})
    return _entrega(rec, permitidos=MIMES_VIDEO, baixar=download)


# ──────────────── relatório: o que a agência entregou na janela ────────────────

# Faixas de duração do relatório, em segundos (limite de cima incluído). O
# vídeo curto é o que a casa pede; a faixa diz se a agência está entregando
# isso ou esticando.
_FAIXAS: tuple[tuple[str, float | None], ...] = (
    ("ate_10", 10),
    ("11_15", 15),
    ("16_20", 20),
    ("21_30", 30),
    ("31_45", 45),
    ("acima_45", None),
)


def _faixa(segundos: float) -> str:
    # Segundo inteiro, arredondado para cima a partir de ,5: o MP4 de "15 s"
    # sai do editor com 15,07 s (quadros a mais no fim), e sem arredondar os
    # vídeos de 15 s caíam todos em "16 a 20 s" (Mindset, 13/10: 30 de 31).
    inteiro = int(segundos + 0.5)
    for nome, teto in _FAIXAS:
        if teto is None or inteiro <= teto:
            return nome
    return _FAIXAS[-1][0]


def _num(valor: Decimal | float | None) -> float | None:
    return round(float(valor), 2) if valor is not None else None


@router.get("/relatorio")
async def relatorio(
    session: Annotated[AsyncSession, Depends(get_session)],
    equipe: Annotated[str, Depends(equipe_do_token)],
    dias: Annotated[int, Query(ge=1, le=365)] = 30,
) -> dict[str, Any]:
    """Os vídeos que ESTA agência mandou nos últimos `dias` (Marco, 13/10/2026).

    A janela é pela data do ARQUIVO, não da linha: a linha nasce quando a
    entrega é aberta e o vídeo pode chegar dias depois — ou de novo, depois de
    uma recusa, e aí os dois envios contam (foram dois vídeos produzidos).

    Só vídeo (`medidas_video.eh_video`). Duração NULL (arquivo de antes da
    0393 ainda sem backfill, ou que o ffprobe não leu) entra na contagem de
    vídeos e em `sem_duracao`, nunca como zero nas médias.

    Recorte e lista branca de sempre: `_da_equipe` no WHERE, e o título da
    ideia só sai se ela estiver visível para esta agência (mesma regra do
    `roteiro_id` da aba Entregas).
    """
    desde = datetime.now(UTC) - timedelta(days=dias)
    # Por ARQUIVO: a postagem aponta para um arquivo, e é este vídeo que
    # saiu (ou não) numa rede. `IN` sem correlação, e não EXISTS por linha:
    # `marketing_postagens.file_id` não tem índice (o único é o parcial dos
    # status em voo), e o EXISTS fazia um Seq Scan nas postagens POR ARQUIVO
    # (154 voltas na janela de 365 dias da Bill Gates em 13/10/2026). Assim o
    # Postgres lê as postagens publicadas uma vez e consulta um hash.
    # (`file_id` é NOT NULL: o IN nunca vê NULL, e o não publicado sai falso.)
    publicado = MarketingCreativeFile.id.in_(
        select(MarketingPostagem.file_id).where(MarketingPostagem.status == STATUS_PUBLICADO)
    ).label("publicado")
    linhas = (
        await session.execute(
            select(
                MarketingCreativeFile.id.label("arquivo_id"),
                MarketingCreativeFile.created_at.label("enviado_em"),
                MarketingCreativeFile.duracao_s,
                MarketingCreativeFile.largura,
                MarketingCreativeFile.altura,
                MarketingCreative.id.label("creative_id"),
                MarketingCreative.modelo,
                MarketingCreative.roteiro_id,
                MarketingCreative.marca,
                MarketingCreative.sku,
                MarketingCreative.aprovado,
                # Do ARQUIVO (migration 0393): dois vídeos da mesma linha
                # podem ter saído de IAs diferentes.
                MarketingCreativeFile.ia_modelo,
                MarketingCreativeFile.ia_creditos,
                publicado,
            )
            .join(MarketingCreative, MarketingCreative.id == MarketingCreativeFile.creative_id)
            .where(
                _da_equipe(equipe),
                MarketingCreativeFile.created_at >= desde,
                eh_video_sql(),
            )
            .order_by(MarketingCreativeFile.created_at.desc(), MarketingCreativeFile.id)
        )
    ).all()

    ids = {r.roteiro_id for r in linhas if r.roteiro_id}
    titulos: dict[UUID, str] = {}
    if ids:
        titulos = dict(
            (
                await session.execute(
                    select(MarketingRoteiro.id, MarketingRoteiro.titulo).where(
                        MarketingRoteiro.id.in_(ids), *_visivel_pra_fora(equipe)
                    )
                )
            )
            .tuples()
            .all()
        )

    duracoes = [float(r.duracao_s) for r in linhas if r.duracao_s is not None]
    faixas = {nome: 0 for nome, _ in _FAIXAS}
    for d in duracoes:
        faixas[_faixa(d)] += 1
    por_ia = Counter((r.ia_modelo or "").strip() or "nao_informada" for r in linhas)

    return {
        "equipe": equipe,
        "dias": dias,
        "desde": desde.isoformat(),
        "total_videos": len(linhas),
        "total_segundos": round(sum(duracoes), 2),
        "media_s": round(statistics.fmean(duracoes), 2) if duracoes else None,
        "mediana_s": round(statistics.median(duracoes), 2) if duracoes else None,
        "sem_duracao": len(linhas) - len(duracoes),
        "faixas": faixas,
        # Do mais usado para o menos; empate em ordem alfabética.
        "por_ia": dict(sorted(por_ia.items(), key=lambda kv: (-kv[1], kv[0].lower()))),
        "videos": [
            {
                "creative_id": str(r.creative_id),
                "arquivo_id": str(r.arquivo_id),
                "titulo": r.modelo,
                "roteiro_id": str(r.roteiro_id) if r.roteiro_id in titulos else None,
                "roteiro_titulo": titulos.get(r.roteiro_id) if r.roteiro_id else None,
                "marca": r.marca,
                "sku": r.sku,
                "enviado_em": r.enviado_em.isoformat() if r.enviado_em else None,
                "duracao_s": _num(r.duracao_s),
                "largura": r.largura,
                "altura": r.altura,
                "aprovado": r.aprovado,
                "publicado": bool(r.publicado),
                "ia_modelo": r.ia_modelo,
                "ia_creditos": _num(r.ia_creditos),
            }
            for r in linhas
        ],
    }


# ──────────────── o catálogo: foto do produto para a agência ────────────────
#
# As fotos moram no MEGA, em pasta por LINHA de produto. Elas chegam aqui pelo
# sidecar, e não por link público: um link do MEGA publica a pasta inteira, sem
# revogação e com o material do fornecedor junto.
#
# Só as duas famílias que a operação usa hoje — /Malas (Charlot's e Poofy) e
# /Celular + /uranyx. O resto do catálogo não é assunto de quem produz vídeo.
_RAIZES_DO_PORTAL = ("/Malas", "/Celular", "/uranyx")


def _raiz_permitida(caminho: str) -> bool:
    limpo = "/" + (caminho or "").strip().strip("/")
    return any(limpo == r or limpo.startswith(r + "/") for r in _RAIZES_DO_PORTAL)


def _marca_da_pasta(caminho: str) -> str:
    """A marca sai da RAIZ da pasta, não de um campo: é ela que a operação usa
    pra separar as duas linhas, e não existe coluna de marca na tabela de
    preços. `/Malas` é Charlot's (e Poofy, a marca antiga); o resto é Uranyx."""
    limpo = "/" + (caminho or "").strip().strip("/")
    return "charlots-park" if limpo.startswith("/Malas") else "uranyx"


# ── busca e variantes por cor (15/10/2026) ──
#
# O Claude conectado ao portal criava ideia com o NOME do produto no campo
# `sku` ("Uranyx A18 Pro Max S5 Edition laranja", "Uranyx_C68 Plus_Preto"):
# ele não tinha onde achar o código. A listagem só mandava produto com foto, e
# o A18 (dg088/dg089) e os três C68 Plus estão com a pasta vazia; e mesmo com
# o produto na mão, a linha "dg088,dg089" não diz qual código é a laranja.

# Quem busca quer achar UM produto: 20 cobre as memórias e tamanhos de uma
# linha sem devolver o catálogo inteiro a cada palavra curta ("uranyx").
LIMITE_BUSCA_PRODUTOS = 20
BUSCA_PRODUTO_MAX = 80

# Os lotes de VENDA, na ordem em que o nome da variante é procurado. `.cd`
# (Centro de Distribuição) e `.us` (usado) ficam de fora de propósito: o nome
# do usado é "... - USADO - AVULSO", e é exatamente a palavra que não pode ir
# parar num vídeo.
_LOTES_DE_VENDA = ("pi", "ci", "sa", "sp", "ra")

# A mala termina o nome com o código do fornecedor entre parênteses ("Mala
# Losangulo M4 tamanho 24 - Cinza Escuro (DT - XS5426 - DT14)"). Sem tirá-lo,
# o último " - " daria "DT14)" como cor.
_PARENTESE_FINAL = re.compile(r"\s*\([^()]*\)\s*$")


def _dobra(texto: str) -> str:
    """Minúscula, sem acento e com "_" e "-" virando espaço — a forma em que a
    busca e o catálogo se comparam. "Relógio" e "relogio" são a mesma palavra,
    e "Uranyx_C68" (o jeito em que o Claude escreveu o SKU) vira "uranyx c68"."""
    s = unicodedata.normalize("NFKD", texto or "")
    s = "".join(c for c in s if not unicodedata.combining(c)).casefold()
    return " ".join(s.replace("_", " ").replace("-", " ").split())


def _cor_do_nome(nome: str | None) -> str | None:
    """O pedaço depois do último " - " — é onde o cadastro do Bling põe a cor
    ("Uranyx C68 Plus 16.128 - cinza"). Nome sem " - " não tem cor a dizer."""
    limpo = _PARENTESE_FINAL.sub("", nome or "").strip()
    _, separador, cor = limpo.rpartition(" - ")
    return (cor.strip() or None) if separador else None


async def _variantes(session: AsyncSession, codigos: set[str]) -> dict[str, tuple[str, str]]:
    """Código base (minúsculo) → (SKU do cadastro, nome), numa consulta só.

    O nome por cor mora em `products` (o cadastro do Bling), não na tabela de
    preços: lá está "dg088.ci" = "Uranyx A18 Pro Max - S5 Edition 16.128 -
    Laranja". Só o ATIVO (`situacao='A'`; há milhares de excluídos na tabela)
    e nunca kit — o kit tem "+" no SKU e o nome dele fala do fone e do relógio.

    Lote de venda primeiro, na ordem de `_LOTES_DE_VENDA`; sem nenhum, o SKU
    igual ao código. Esse degrau não é enfeite: as malas são cadastradas sem
    lote ("b039.24" é a mala), e em 15/10/2026 eram 986 dos 1.169 códigos das
    famílias do portal.

    Junta com `unnest` em vez de `= ANY`: são milhares de chaves (as malas têm
    53 códigos por linha, vezes seis formas cada), e o ANY com parâmetro faria
    a comparação elemento a elemento em cada linha de `products`. Com o join o
    Postgres faz um hash: ~11 ms em produção com as 7.014 chaves de todas as
    famílias (15/10/2026).
    """
    if not codigos:
        return {}
    formas = {c: [f"{c}.{lote}" for lote in _LOTES_DE_VENDA] + [c] for c in codigos}
    chaves = (
        func.unnest(literal(sorted({f for fs in formas.values() for f in fs}), ARRAY(Text)))
        .table_valued("chave")
        .render_derived(name="chaves")
    )
    achados: dict[str, tuple[str, str]] = {}
    for sku, nome in (
        await session.execute(
            select(Product.sku, Product.name)
            .join(chaves, func.lower(Product.sku) == chaves.c.chave)
            .where(Product.situacao == "A", Product.sku.not_like("%+%"))
            # Ordem estável: o mesmo SKU ativo em duas contas dá sempre o
            # mesmo nome, e não um sorteio entre duas chamadas.
            .order_by(Product.sku, Product.name)
        )
    ).tuples():
        achados.setdefault(sku.lower(), (sku, nome))
    saida: dict[str, tuple[str, str]] = {}
    for codigo, fs in formas.items():
        escolhida = next((f for f in fs if f in achados), None)
        if escolhida is not None:
            saida[codigo] = achados[escolhida]
    return saida


@router.get("/produtos")
async def listar_produtos(
    response: Response,
    session: Annotated[AsyncSession, Depends(get_session)],
    equipe: Annotated[str, Depends(equipe_do_token)],
    busca: Annotated[str | None, Query(max_length=BUSCA_PRODUTO_MAX)] = None,
    todos: bool = False,
) -> dict[str, Any]:
    """Os produtos com pasta de fotos, para a agência saber como a peça É.

    `sku` da tabela de preços guarda a LINHA inteira separada por vírgula (uma
    pasta serve todas as cores), então sai a lista — é assim que a agência acha
    o código que está escrito na ideia. `variantes` diz o que cada código É:
    `{"codigo", "nome", "cor"}`, com `nome`/`cor` NULL quando o cadastro não
    tem variante ativa daquele código.

    Sem parâmetro, a lista é a MESMA de antes de 15/10/2026 (o portal no ar
    depende dela), só com `variantes` a mais em cada produto. Os dois
    parâmetros, opcionais, existem para o Claude achar o código pelo nome:

    - `busca` (até 80 caracteres): palavras, sem diferenciar maiúscula nem
      acento, com "_" e "-" valendo espaço. Cada palavra tem de aparecer no
      nome do produto, no nome de uma das variantes ou no começo de um dos
      códigos ("dg08" acha o dg088). No máximo 20 produtos; o total casado vai
      no cabeçalho `X-Total-Count`.
    - `todos=1`: inclui os produtos das famílias do portal com a pasta vazia
      (`fotos` 0) — a situação `sem_foto` da rota `/produtos/por-codigo`.
    """
    # Duas travas, e as duas vieram de olhar o dado real em 23/09/2026:
    #
    # 1. `fotos_count > 0`. Dos 86 produtos com pasta, 39 têm a pasta VAZIA —
    #    conferi por amostragem e estão vazias mesmo. Card sem foto é card que
    #    a agência abre à toa, e ainda dispara uma ida ao MEGA pra descobrir
    #    que não tem nada. `todos=1` tira só esta trava: quem pede quer o
    #    CÓDIGO do produto, não a foto (15/10/2026).
    #
    # 2. Fora o segmento Apple. `/Celular` mistura os robustos da Uranyx
    #    (Fossibot, Oukitel, Oscal) com revenda de iPhone, MacBook e Watch —
    #    11 produtos que não são marca da casa e não interessam a quem produz
    #    vídeo. O segmento é o separador certo porque é campo curado; o nome da
    #    pasta seria adivinhação. Vale com `todos` também.
    #
    # A família continua saindo da RAIZ da pasta (`_raiz_permitida`), igual à
    # `/produtos/por-codigo`: produto sem pasta nenhuma não tem família a
    # provar e não sai, nem com `todos`.
    apple = select(Segment.id).where(func.lower(Segment.name) == "apple").scalar_subquery()
    filtros: list[Any] = [
        PricingProduct.fotos_path.is_not(None),
        PricingProduct.fotos_path != "",
        PricingProduct.segment_id.not_in(apple),
    ]
    if not todos:
        filtros.append(PricingProduct.fotos_count > 0)
    linhas = [
        p
        for p in (
            await session.execute(
                select(PricingProduct).where(*filtros).order_by(PricingProduct.name)
            )
        ).scalars()
        if _raiz_permitida(p.fotos_path or "")
    ]
    skus_de = {p.id: [s.strip() for s in (p.sku or "").split(",") if s.strip()] for p in linhas}
    variantes = await _variantes(
        session, {c.lower() for codigos in skus_de.values() for c in codigos}
    )

    # Controle sai da busca em vez de recusar, como no `q` das ideias: o resto
    # do que foi digitado ainda é uma busca. Busca que não sobra palavra
    # nenhuma ("__", "  ") é a listagem de sempre.
    palavras = _dobra(_CONTROLE.sub("", busca or "")).split()
    if palavras:

        def casa(p: PricingProduct) -> bool:
            # O nome da variante entra junto com o do produto: o C68 Plus da
            # tabela de preços não diz cor nenhuma, e "c68 dourado" só acha
            # alguma coisa pelo cadastro. A quebra de linha separa os nomes
            # para uma palavra não nascer da emenda de dois — e palavra nunca
            # tem quebra de linha, então o separador nunca casa sozinho.
            achado = [variantes.get(c.lower()) for c in skus_de[p.id]]
            nomes = "\n".join([_dobra(p.name)] + [_dobra(v[1]) for v in achado if v])
            # O código vale pelo COMEÇO ("dg08" acha o dg088, "08" não acha
            # nada), e o SKU do cadastro conta também ("dg088.ci").
            codigos = [_dobra(c) for c in skus_de[p.id]] + [_dobra(v[0]) for v in achado if v]
            return all(
                w in nomes or any(c.startswith(w) for c in codigos) for w in palavras
            )

        linhas = [p for p in linhas if casa(p)]
        response.headers["X-Total-Count"] = str(len(linhas))
        logger.info(
            "portal_produtos_busca",
            equipe=equipe,
            busca=" ".join(palavras),
            todos=todos,
            achados=len(linhas),
        )
        linhas = linhas[:LIMITE_BUSCA_PRODUTOS]

    def variantes_de(p: PricingProduct) -> list[dict[str, Any]]:
        saida = []
        for codigo in skus_de[p.id]:
            achada = variantes.get(codigo.lower())
            nome = achada[1] if achada else None
            saida.append({"codigo": codigo, "nome": nome, "cor": _cor_do_nome(nome)})
        return saida

    return {
        "equipe": equipe,
        "produtos": [
            {
                "id": str(p.id),
                "nome": p.name,
                "skus": skus_de[p.id],
                "pasta": p.fotos_path,
                "marca": _marca_da_pasta(p.fotos_path or ""),
                "fotos": p.fotos_count or 0,
                "variantes": variantes_de(p),
            }
            for p in linhas
        ],
    }


# Teto de códigos por consulta. Um produto da casa chega a 53 códigos numa
# linha só (as malas por cor e tamanho); 80 cobre isso com folga e ainda
# impede que a consulta vire um OR de mil termos.
_MAX_CODIGOS_POR_CONSULTA = 80


@router.get("/produtos/por-codigo")
async def produtos_por_codigo(
    session: Annotated[AsyncSession, Depends(get_session)],
    equipe: Annotated[str, Depends(equipe_do_token)],
    sku: Annotated[list[str] | None, Query()] = None,
) -> dict[str, Any]:
    """Quem da casa já tem estes códigos — INCLUSIVE produto escondido.

    O portal deixa a agência cadastrar o produto que falta (29/09/2026). E
    "falta", quase sempre, quer dizer "existe aqui, mas a pasta do MEGA está
    vazia": a listagem acima só manda produto com foto. Sem esta consulta o
    cadastro viraria um SEGUNDO produto com o mesmo código, e no dia em que a
    pasta fosse preenchida os dois apareceriam lado a lado. Com ela, o portal
    junta as fotos ao produto que já existe.

    Só leitura. `situacao` de cada produto casado:

    - `visivel`  — já aparece na listagem do portal;
    - `sem_foto` — família do portal, pasta vazia: é o que a agência pode completar;
    - `apple`    — revenda Apple, que o portal não mostra (ver a listagem).

    Produto FORA das famílias do portal não sai daqui: o nome dele não é
    assunto de agência, e um código adivinhado não pode virar consulta ao
    catálogo interno inteiro. Para o portal, esse código está livre.
    """
    pedidos = {s.strip().lower() for s in (sku or []) if s and s.strip()}
    if not pedidos:
        return {"produtos": []}
    if len(pedidos) > _MAX_CODIGOS_POR_CONSULTA:
        raise HTTPException(400, detail={"code": "codigos_demais"})

    apple = set(
        (
            await session.execute(select(Segment.id).where(func.lower(Segment.name) == "apple"))
        ).scalars()
    )
    # O `sku` guarda a linha inteira separada por vírgula, então o banco só
    # PRÉ-filtra por substring; quem decide é a comparação exata abaixo, código
    # a código. `autoescape`: código com `_` ou `%` não pode virar curinga.
    candidatos = (
        (
            await session.execute(
                select(PricingProduct)
                .where(
                    or_(
                        *(
                            func.lower(PricingProduct.sku).contains(s, autoescape=True)
                            for s in pedidos
                        )
                    )
                )
                # Ordem estável: quando a mesma pasta vem em mais de uma linha,
                # é sempre a mesma que representa as outras (e recebe as fotos).
                .order_by(PricingProduct.id)
            )
        )
        .scalars()
        .all()
    )
    saida: list[dict[str, Any]] = []
    # A mesma pasta em mais de uma linha da tabela de preços (a casa replica a
    # linha por departamento): para o portal a pasta é o produto, então as
    # linhas viram UMA entrada. Sem isto o portal via "produtos diferentes da
    # casa" e recusava o cadastro para sempre.
    por_pasta: dict[str, dict[str, Any]] = {}
    for p in candidatos:
        codigos = [c.strip() for c in (p.sku or "").split(",") if c.strip()]
        casados = [c for c in codigos if c.lower() in pedidos]
        if not casados or not _raiz_permitida(p.fotos_path or ""):
            continue
        if p.segment_id in apple:
            # Só o fato, sem nome: basta para o portal recusar o cadastro.
            saida.append({"situacao": "apple", "casados": casados})
            continue
        situacao = "visivel" if (p.fotos_count or 0) > 0 else "sem_foto"
        pasta = "/" + (p.fotos_path or "").strip().strip("/")
        ja = por_pasta.get(pasta)
        if ja is not None:
            vistos = {c.lower() for c in ja["skus"]}
            ja["skus"] += [c for c in codigos if c.lower() not in vistos]
            vistos = {c.lower() for c in ja["casados"]}
            ja["casados"] += [c for c in casados if c.lower() not in vistos]
            if situacao == "visivel":
                ja["situacao"] = "visivel"
            continue
        item = {
            "id": str(p.id),
            "nome": p.name,
            "marca": _marca_da_pasta(p.fotos_path or ""),
            "skus": codigos,
            "casados": casados,
            "situacao": situacao,
        }
        por_pasta[pasta] = item
        saida.append(item)
    logger.info(
        "portal_produtos_por_codigo", equipe=equipe, pedidos=len(pedidos), casados=len(saida)
    )
    return {"produtos": saida}


async def _produto_do_portal(session: AsyncSession, produto_id: UUID) -> PricingProduct:
    row = await session.get(PricingProduct, produto_id)
    if row is None or not _raiz_permitida(row.fotos_path or ""):
        # 404 e não 403: produto fora das famílias do portal não existe pra
        # quem está do lado de fora.
        raise HTTPException(404, detail={"code": "nao_encontrado"})
    return row


@router.get("/produtos/{produto_id}/fotos")
async def listar_fotos_do_produto(
    produto_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    equipe: Annotated[str, Depends(equipe_do_token)],
) -> dict[str, Any]:
    """Os nomes das IMAGENS da pasta. Vídeo fica de fora de propósito.

    A pasta do produto acumula os criativos aprovados (a aprovação empurra o
    MP4 pra cá), e quem vem ver o produto quer o produto — o vídeo dos outros
    tem lugar próprio, na aba de referência.
    """
    row = await _produto_do_portal(session, produto_id)
    try:
        # Sem `tipo` o sidecar devolve só imagens — e, desde 29/09/2026, já
        # sem a subpasta de embalagens, que é material interno.
        resp = await sidecar_request("GET", "/files", params={"path": row.fotos_path})
    except MegaError as e:
        raise HTTPException(503, detail={"code": "mega_indisponivel", "message": str(e)}) from e
    # O sidecar conta heic, tiff e bmp como imagem (é o mesmo conjunto da
    # contagem antiga). Aqui só passa o que o navegador DESENHA — listar um
    # .heic que a tela não abre é prometer foto que não aparece.
    fotos = [
        a
        for a in (resp.get("arquivos") or [])
        if a.get("imagem") and Path(str(a.get("nome", ""))).suffix.lower() in _EXT_IMAGEM
    ]
    return {
        "produto": row.name,
        "pasta": row.fotos_path,
        "marca": _marca_da_pasta(row.fotos_path or ""),
        # Os códigos, na ficha e não só na grade. É por ELE que a agência acha
        # o produto: a ideia diz "usa o b039", não "usa a ABS 12". Sai aqui a
        # lista INTEIRA — o card da grade mostra cinco e esconde o resto, e até
        # hoje clicar no card perdia o código de vista.
        "skus": [s.strip() for s in (row.sku or "").split(",") if s.strip()],
        "fotos": fotos,
    }


# `_disposicao` (o Content-Disposition que sobrevive a nome em chinês) nasceu
# aqui e mudou para services/marketing/anexos.py como `disposicao_segura`: a
# Tabela de Preços também serve arquivo do MEGA. O nome antigo continua
# importável daqui.


@router.get("/produtos/{produto_id}/foto")
async def baixar_foto_do_produto(
    produto_id: UUID,
    nome: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    equipe: Annotated[str, Depends(equipe_do_token)],
) -> Response:
    """Os bytes de UMA foto.

    `nome` é conferido contra a listagem da pasta antes de virar caminho: sem
    isso, um `..` no parâmetro viraria leitura de outra pasta da conta MEGA.
    """
    row = await _produto_do_portal(session, produto_id)
    # Subpasta é legítima — `/Malas/<linha>` guarda uma por modelo. Subir de
    # nível não é, e barra no começo viraria caminho absoluto lá do outro lado.
    if ".." in nome or not nome.strip() or nome.startswith("/"):
        raise HTTPException(400, detail={"code": "nome_invalido"})
    # O MEGAcmd resolve "." no caminho e trata "*" e "?" como curinga
    # (conferido em produção: `mega-ls /Celular/./` e `mega-ls /Cel*lar` listam
    # /Celular). Com isso "./Embalagens/caixa.jpg", "Embalagen?/caixa.jpg" ou
    # "*/caixa.jpg" chegavam à caixa sem que o 1º trecho fosse "Embalagens" — e
    # a trava logo abaixo só olha o 1º trecho. Nome que vem da listagem nunca
    # tem trecho vazio, "." ou curinga, então nada legítimo é barrado. O strip
    # é só na comparação: pasta com espaço no fim do nome continua valendo.
    if any(s.strip() in ("", ".", "..") for s in nome.split("/")) or any(
        c in nome for c in "*?"
    ):
        raise HTTPException(400, detail={"code": "nome_invalido"})
    # A subpasta de embalagens (caixa, arte de impressão) é material interno:
    # a listagem acima já não a mostra, e aqui ela também não desce — senão
    # bastaria adivinhar "Embalagens/caixa.jpg" para baixar.
    if eh_de_embalagens(nome):
        raise HTTPException(400, detail={"code": "nome_invalido"})
    # O MIME sai da EXTENSÃO, nunca do que o MEGA disser: é a mesma regra das
    # outras rotas de bytes daqui, e é ela que impede servir HTML como imagem.
    # Vem ANTES do download: extensão que a tela não desenha nem chega a gastar
    # uma ida ao MEGA — e é isto que impede pedir por aqui o MP4 que a agência
    # entregou, porque ele mora na mesma pasta.
    media, disposicao = mime_seguro(
        mime_da_extensao(nome, tabela=_EXT_IMAGEM), permitidos=MIMES_IMAGEM
    )

    # SEM re-listar a pasta. A versão anterior conferia o nome contra a
    # listagem a cada foto — uma ida extra ao MEGA por miniatura, e a grade
    # pede 24 de uma vez. O nome já está preso: sem `..`, sem barra inicial e
    # com extensão de imagem, o pior caso é ler outro arquivo DESTA pasta, que
    # é exatamente o que a agência tem direito de ver.
    try:
        bruto = await sidecar_bytes("/file", params={"path": f"{row.fotos_path}/{nome}"})
    except MegaError as e:
        raise HTTPException(503, detail={"code": "mega_indisponivel", "message": str(e)}) from e
    return Response(
        content=bruto,
        media_type=media,
        headers={
            "Content-Disposition": _disposicao(disposicao, nome),
            "X-Content-Type-Options": "nosniff",
            # Conteúdo de catálogo muda pouco e a volta ao MEGA é cara.
            "Cache-Control": "private, max-age=86400",
        },
    )
