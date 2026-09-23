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
from app.models import Marca, MarketingPostagemMetrica, User
from app.deps.auth import require_permission

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
             "posts": 0, "coletado_em": None, "erro": None},
        )
        alvo["posts"] += 1
        plat["posts"] += 1
        _soma(alvo["acumulado"], n)
        _soma(plat["acumulado"], n)
        # Crescimento na janela: o novo menos o velho do MESMO post.
        v = velhos.get(n.postagem_id)
        if v is not None and v.dia != n.dia:
            _soma(alvo["no_periodo"], n)
            _soma(alvo["no_periodo"], v, sinal=-1)
            _soma(plat["no_periodo"], n)
            _soma(plat["no_periodo"], v, sinal=-1)
        # Quando esta rede foi lida pela última vez, e se deu erro. Coleta
        # falha baixo — sem esta data a tela mostra número velho como se
        # fosse de hoje.
        if plat["coletado_em"] is None or n.dia > plat["coletado_em"]:
            plat["coletado_em"] = n.dia
            plat["erro"] = n.erro

    saida = sorted(por_marca.values(), key=lambda x: -(x["acumulado"].get("views") or 0))
    for m in saida:
        m["plataformas"] = sorted(m["plataformas"].values(), key=lambda x: x["plataforma"])
    return {"dias": dias, "desde": desde, "marcas": saida}
