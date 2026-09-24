"""Quanto cada marca rendeu nas redes — a tela de métricas.

Pedido do Eduardo (23/09/2026): ver as views e interações POR MARCA, com o
total somado e o detalhe de cada rede.

Duas contas diferentes moram aqui, e confundi-las é o jeito mais fácil de a
tela mentir:

  ACUMULADO   quanto o vídeo tem HOJE. Sai do retrato mais NOVO de cada
              postagem. Somar todos os retratos multiplicaria o mesmo vídeo
              por quantas vezes ele foi medido.

  NO PERÍODO  quanto ele GANHOU na janela escolhida. Sai da diferença entre o
              retrato mais novo e o mais velho dentro da janela. É o número
              que responde "esta semana rendeu mais que a passada".

E uma ressalva que a tela precisa carregar, não esconder em rodapé: "view" não
quer dizer a mesma coisa nas três redes — o limiar de segundos é diferente em
cada uma. O total somado serve pra sentir tendência; comparar só vale dentro da
mesma plataforma. É por isso que o detalhe por rede vem junto, sempre.
"""

from datetime import UTC, datetime, timedelta
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import Marca, MarketingPostagem, MarketingPostagemMetrica, User
from app.deps.auth import require_permission
from app.services.marketing.metricas import foi_removido

router = APIRouter(prefix="/api/marketing/metricas", tags=["marketing_metricas"])

_NUMEROS = ("views", "curtidas", "comentarios", "compartilhamentos", "salvamentos", "alcance")


def _soma(acc: dict[str, int | None], linha: Any, sinal: int = 1) -> None:
    """Soma tratando NULO como ausência, não como zero.

    A diferença aparece na tela: se nenhuma postagem da marca reportou
    `salvamentos`, o campo fica NULO e a tela escreve "—". Zerar diria que
    ninguém salvou, que é uma afirmação que não temos como fazer.
    """
    for campo in _NUMEROS:
        v = getattr(linha, campo, None)
        if v is None:
            continue
        acc[campo] = (acc.get(campo) or 0) + sinal * v


@router.get("")
async def metricas(
    session: Annotated[AsyncSession, Depends(get_session)],
    # Mesma permissão da aba Criativos: quem vê o que foi publicado vê o que rendeu.
    _u: Annotated[User, Depends(require_permission("marketing_criativos", "view"))],
    dias: Annotated[int, Query(ge=1, le=365)] = 30,
) -> dict[str, Any]:
    """Números por marca e por plataforma, na janela pedida."""
    desde = datetime.now(UTC) - timedelta(days=dias)

    # O retrato mais NOVO e o mais VELHO de cada postagem dentro da janela.
    # DISTINCT ON é o jeito do Postgres de pegar "a primeira linha de cada
    # grupo" sem subquery correlacionada.
    novo = (
        select(MarketingPostagemMetrica)
        .where(MarketingPostagemMetrica.dia >= desde)
        .order_by(MarketingPostagemMetrica.postagem_id, MarketingPostagemMetrica.dia.desc())
        .distinct(MarketingPostagemMetrica.postagem_id)
    ).subquery()
    velho = (
        select(MarketingPostagemMetrica)
        .where(MarketingPostagemMetrica.dia >= desde)
        .order_by(MarketingPostagemMetrica.postagem_id, MarketingPostagemMetrica.dia.asc())
        .distinct(MarketingPostagemMetrica.postagem_id)
    ).subquery()

    novos = (await session.execute(select(novo))).all()
    velhos = {v.postagem_id: v for v in (await session.execute(select(velho))).all()}

    marcas = {
        m.id: m.nome
        for m in (await session.execute(select(Marca))).scalars().all()
    }
    # O link e a legenda vivem na postagem, não na métrica — a métrica guarda
    # só o que muda a cada dia.
    ids = [n.postagem_id for n in novos]
    posts = {
        p.id: p
        for p in (
            await session.execute(
                select(MarketingPostagem).where(MarketingPostagem.id.in_(ids))
            )
        ).scalars().all()
    } if ids else {}

    # marca -> plataforma -> números
    por_marca: dict[str, dict[str, Any]] = {}
    for n in novos:
        nome = marcas.get(n.marca_id, "(marca removida)")
        alvo = por_marca.setdefault(
            nome,
            {"marca": nome, "acumulado": {}, "no_periodo": {}, "plataformas": {}, "posts": 0},
        )
        plat = alvo["plataformas"].setdefault(
            n.plataforma,
            {"plataforma": n.plataforma, "acumulado": {}, "no_periodo": {},
             "posts": 0, "coletado_em": None, "erro": None, "videos": []},
        )
        # Vídeo removido não entra em conta nenhuma: ele não está no ar, não
        # está rendendo, e contá-lo faria a marca parecer ter mais material
        # publicado do que tem.
        removido = foi_removido(n.erro)
        if not removido:
            alvo["posts"] += 1
            plat["posts"] += 1
            _soma(alvo["acumulado"], n)
            _soma(plat["acumulado"], n)
        v = velhos.get(n.postagem_id)
        # Crescimento na janela: o novo menos o velho do MESMO post.
        if not removido and v is not None and v.dia != n.dia:
            _soma(alvo["no_periodo"], n)
            _soma(alvo["no_periodo"], v, sinal=-1)
            _soma(plat["no_periodo"], n)
            _soma(plat["no_periodo"], v, sinal=-1)
        # O vídeo em si — é o nível que o Eduardo pediu pra poder abrir e ver
        # de onde vem cada número. Sem isto, "a marca fez 9 views" não diz
        # QUAL vídeo fez, que é o que serve pra decidir o que produzir.
        post = posts.get(n.postagem_id)
        ganho = {}
        if v is not None and v.dia != n.dia:
            _soma(ganho, n)
            _soma(ganho, v, sinal=-1)
        plat["videos"].append(
            {
                "postagem_id": str(n.postagem_id),
                "post_url": getattr(post, "post_url", None),
                "publicado_em": getattr(post, "publicado_em", None),
                # Primeira linha da legenda: é como o Eduardo reconhece o vídeo
                # na lista. A legenda inteira não cabe e não ajuda.
                "titulo": ((getattr(post, "legenda", None) or "").strip().splitlines() or [""])[0][:80],
                "acumulado": {k: getattr(n, k) for k in _NUMEROS if getattr(n, k) is not None},
                "no_periodo": ganho,
                "coletado_em": n.dia,
                # Removido NÃO é falha de leitura: a leitura funcionou e a
                # resposta foi "isto não está mais aqui". Misturar os dois põe
                # alerta em cima de post apagado de propósito.
                "removido": removido,
                "erro": None if removido else n.erro,
            }
        )
        # Quando esta rede foi lida pela última vez, e se deu erro. Coleta
        # falha baixo — sem esta data a tela mostra número velho como se
        # fosse de hoje. Vídeo removido não conta como erro da rede.
        if plat["coletado_em"] is None or n.dia > plat["coletado_em"]:
            plat["coletado_em"] = n.dia
        if n.erro and not foi_removido(n.erro):
            plat["erro"] = n.erro

    # Marca (ou rede) cujos vídeos foram TODOS apagados não tem o que reportar,
    # e linha só de travessão é ruído — pedido do Eduardo em 24/09/2026, depois
    # de apagar os testes da charlots e da 7buyers. Não some em silêncio: o nome
    # vai em `sem_video_no_ar`, pra ele não se perguntar "cadê a charlots".
    sem_video: list[str] = []
    saida: list[dict[str, Any]] = []
    for m in sorted(por_marca.values(), key=lambda x: -(x["acumulado"].get("views") or 0)):
        redes = [p for p in m["plataformas"].values() if p["posts"]]
        if not redes:
            sem_video.append(m["marca"])
            continue
        m["plataformas"] = sorted(redes, key=lambda x: x["plataforma"])
        for plat in m["plataformas"]:
            # O vídeo apagado some da lista junto — ele já não conta em nada.
            plat["videos"] = [v for v in plat["videos"] if not v["removido"]]
            # Mais views primeiro: a pergunta é "o que rendeu", não "o que saiu".
            plat["videos"].sort(key=lambda v: -(v["acumulado"].get("views") or 0))
        saida.append(m)
    return {
        "dias": dias,
        "desde": desde,
        "marcas": saida,
        "sem_video_no_ar": sorted(sem_video),
    }
