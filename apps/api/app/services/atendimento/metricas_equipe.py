"""As MÉTRICAS POR EQUIPE e a análise do dia (13/11/2026).

O Eduardo: "em métricas ... por equipe e não mais por loja; traz as %; faz o agente 1 vez
por dia trazer uma análise geral; deixa a tela mais simples; abrindo o select, as lojas
todas e as %".

EQUIPE = a divisão da planilha "Separação de contas" (Vendedor 1 = londres, 2 = israel,
3 = marrocos): as lojas de cada pessoa em `atendimento_lojas_pessoa`, montadas pelo mesmo
escopo das telas (`escopo.escopo_do_atendimento`). Por loja e por equipe, no período:

- APROVAÇÃO DA IA: das decisões da equipe sobre o rascunho (o juiz, `juiz_edicao`: igual ou
  só forma = acerto; conteúdo ou outra ação = erro; só saudação fica fora), quantas foram
  acerto — a mesma conta da régua do envio sozinho;
- SAIU SOZINHO: as respostas que a IA mandou (origem `davinci_ia`);
- PELA EQUIPE: as respostas que a pessoa mandou pelo DaVinci (origem `davinci_humano`).

A ANÁLISE DO DIA: uma vez por dia o modelo do agente lê esses números (ontem e 7 dias), os
assuntos que mais erram e os motivos curtos do juiz (já sem dado pessoal) e escreve um
resumo curto; fica no Redis um dia por chave (180 dias) — sem tabela nova; sumiu, gera de
novo. Nada de texto de cliente entra no pedido nem no log.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import Integer, case, func, select

from app.services.atendimento import constantes, juiz_edicao

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

CHAVE_ANALISE = "atd:analise_diaria:{dia}"
CHAVE_ANALISE_ULTIMA = "atd:analise_diaria:ultima"
TTL_ANALISE_S = 180 * 24 * 3600
MAX_TOKENS_ANALISE = 900
MIN_DECISOES_ASSUNTO = 5


@dataclass
class Numeros:
    decisoes: int = 0
    acertos: int = 0
    ia_enviou: int = 0
    equipe_enviou: int = 0

    def somar(self, outro: Numeros) -> None:
        self.decisoes += outro.decisoes
        self.acertos += outro.acertos
        self.ia_enviou += outro.ia_enviou
        self.equipe_enviou += outro.equipe_enviou

    @property
    def aprovacao(self) -> float | None:
        return self.acertos / self.decisoes if self.decisoes else None

    def para_json(self) -> dict[str, Any]:
        a = self.aprovacao
        return {
            "decisoes": self.decisoes,
            "acertos": self.acertos,
            "aprovacao": round(a, 4) if a is not None else None,
            "ia_enviou": self.ia_enviou,
            "equipe_enviou": self.equipe_enviou,
        }


@dataclass
class Equipe:
    user_id: UUID
    nome: str
    integration_ids: set[UUID] = field(default_factory=set)


async def equipes(session: AsyncSession) -> list[Equipe]:
    """As pessoas com lojas na planilha (`atendimento_lojas_pessoa`) e as integrações delas."""
    from app.models import User
    from app.models.atendimento_lojas_pessoa import AtendimentoLojaPessoa
    from app.services.atendimento import escopo

    ids = (await session.scalars(select(AtendimentoLojaPessoa.user_id).distinct())).all()
    saida: list[Equipe] = []
    for user in (await session.scalars(select(User).where(User.id.in_(ids)))).all():
        scope = await escopo.escopo_do_atendimento(session, user)
        if scope.unrestricted:
            continue  # admin com lojas marcadas não é equipe
        nome = (getattr(user, "name", None) or getattr(user, "email", None) or "equipe").strip()
        saida.append(Equipe(user.id, nome, set(scope.integration_ids)))
    return sorted(saida, key=lambda e: e.nome.casefold())


async def numeros_por_loja(
    session: AsyncSession, desde: datetime, ate: datetime
) -> dict[UUID | None, Numeros]:
    """Por integração: as decisões julgadas, os acertos e as respostas da IA e da equipe."""
    from app.models import AtendimentoConversa, AtendimentoMensagem
    from app.models.atendimento import AtendimentoJulgamento as J

    saida: dict[UUID | None, Numeros] = defaultdict(Numeros)
    conta = (
        await session.execute(
            select(
                J.integration_id,
                func.count().label("d"),
                func.sum(case((J.julgamento.in_(tuple(juiz_edicao.ACERTOS)), 1), else_=0))
                .cast(Integer)
                .label("a"),
            )
            .where(
                J.julgamento.in_(tuple(juiz_edicao.ACERTOS | juiz_edicao.ERROS)),
                J.decidido_em >= desde,
                J.decidido_em < ate,
            )
            .group_by(J.integration_id)
        )
    ).all()
    for integ, d, a in conta:
        saida[integ].decisoes += int(d or 0)
        saida[integ].acertos += int(a or 0)
    m, c = AtendimentoMensagem, AtendimentoConversa
    envios = (
        await session.execute(
            select(
                c.integration_id,
                func.count().filter(m.origem == constantes.ORIGEM_IA).label("ia"),
                func.count().filter(m.origem == constantes.ORIGEM_HUMANO).label("eq"),
            )
            .join(c, c.id == m.conversa_id)
            .where(
                m.origem.in_((constantes.ORIGEM_IA, constantes.ORIGEM_HUMANO)),
                m.enviada_em >= desde,
                m.enviada_em < ate,
            )
            .group_by(c.integration_id)
        )
    ).all()
    for integ, ia, eq in envios:
        saida[integ].ia_enviou += int(ia or 0)
        saida[integ].equipe_enviou += int(eq or 0)
    return dict(saida)


async def resumo(session: AsyncSession, *, dias: int, agora: datetime | None = None) -> dict:
    """As equipes (com as % somadas das lojas delas) e as lojas todas, no período."""
    from app.models import Integration

    agora = agora or datetime.now(UTC)
    desde = agora - timedelta(days=dias)
    por_loja = await numeros_por_loja(session, desde, agora)
    times = await equipes(session)
    dono: dict[UUID, str] = {}
    for t in times:
        for i in t.integration_ids:
            dono.setdefault(i, t.nome)
    ids = [i for i in por_loja if i is not None]
    nomes = {
        i: (n, p)
        for i, n, p in (
            await session.execute(
                select(Integration.id, Integration.name, Integration.platform).where(
                    Integration.id.in_(ids)
                )
            )
        ).all()
    }
    lojas = []
    for i, n in por_loja.items():
        if i is None:
            continue
        nome, plat = nomes.get(i, (None, None))
        lojas.append(
            {
                "integration_id": str(i),
                "loja": nome or "—",
                "plataforma": getattr(plat, "value", plat),
                "equipe": dono.get(i),
                **n.para_json(),
            }
        )
    lojas.sort(key=lambda x: (x["equipe"] or "~", -(x["decisoes"] or 0)))
    total = Numeros()
    saida_times = []
    for t in times:
        soma = Numeros()
        for i in t.integration_ids:
            if i in por_loja:
                soma.somar(por_loja[i])
        saida_times.append({"equipe": t.nome, "lojas": len(t.integration_ids), **soma.para_json()})
    for n in por_loja.values():
        total.somar(n)
    return {"dias": dias, "equipes": saida_times, "lojas": lojas, "total": total.para_json()}


# ── A análise do dia ────────────────────────────────────────────────────────────

SISTEMA_ANALISE = (
    "Você é o analista do atendimento ao cliente de uma loja online brasileira (Shopee, "
    "Mercado Livre, TikTok Shop, Amazon). A IA escreve o rascunho de cada resposta; a equipe "
    "aprova, ajusta ou reescreve; o que a equipe aprova em 80% dos casos de um assunto passa a "
    "sair sozinho (90% em reembolso, cancelamento, troca e reclamação). Escreva para o dono, em "
    "português, direto, em no máximo 10 linhas curtas: 1) como foi ontem (a aprovação da IA, "
    "o que saiu sozinho), 2) a tendência dos 7 dias e a equipe que melhorou ou piorou, 3) os "
    "assuntos em que a IA mais erra e o porquê, pelos motivos, 4) uma ou duas ações concretas "
    "(corrigir resposta no banco, regra no manual, assunto para soltar ou segurar). Só os "
    "números dados; nada de inventar; sem dado pessoal."
)


async def dados_da_analise(session: AsyncSession, *, agora: datetime | None = None) -> dict:
    """Os números da análise: ontem, 7 dias, os assuntos que mais erram e os motivos."""
    from app.models.atendimento import AtendimentoJulgamento as J
    from app.models.atendimento import AtendimentoResposta
    from app.services.atendimento import duvida

    agora = agora or datetime.now(UTC)
    ontem = await resumo(session, dias=1, agora=agora)
    semana = await resumo(session, dias=7, agora=agora)
    desde7 = agora - timedelta(days=7)
    por_tipo = (
        await session.execute(
            select(
                J.tipo,
                func.count().label("d"),
                func.sum(case((J.julgamento.in_(tuple(juiz_edicao.ACERTOS)), 1), else_=0))
                .cast(Integer)
                .label("a"),
            )
            .where(
                J.julgamento.in_(tuple(juiz_edicao.ACERTOS | juiz_edicao.ERROS)),
                J.decidido_em >= desde7,
            )
            .group_by(J.tipo)
        )
    ).all()
    assuntos = sorted(
        (
            {
                "assunto": duvida.nome_do_assunto(t),
                "decisoes": int(d),
                "aprovacao": round(int(a or 0) / int(d), 2),
            }
            for t, d, a in por_tipo
            if int(d) >= MIN_DECISOES_ASSUNTO
        ),
        key=lambda x: x["aprovacao"],
    )
    motivos = [
        str(m)[:160]
        for m in (
            await session.scalars(
                select(J.motivo)
                .where(
                    J.julgamento.in_(tuple(juiz_edicao.ERROS)),
                    J.motivo.is_not(None),
                    J.decidido_em >= agora - timedelta(days=1),
                )
                .order_by(J.decidido_em.desc())
                .limit(15)
            )
        ).all()
    ]
    banco_novas = int(
        await session.scalar(
            select(func.count())
            .select_from(AtendimentoResposta)
            .where(AtendimentoResposta.created_at >= agora - timedelta(days=1))
        )
        or 0
    )
    return {
        "ontem": {"total": ontem["total"], "equipes": ontem["equipes"]},
        "semana": {"total": semana["total"], "equipes": semana["equipes"]},
        "assuntos_que_mais_erram": assuntos[:8],
        "motivos_de_erro_de_ontem": motivos,
        "respostas_novas_no_banco_ontem": banco_novas,
    }


async def texto_do_modelo(cliente: Any, *, modelo: str, dados: dict) -> tuple[str | None, Any]:
    """A análise em texto (ou None) e a mensagem do modelo (para o custo). Nunca levanta."""
    import anthropic

    try:
        mensagem = await cliente.messages.create(
            model=modelo,
            max_tokens=MAX_TOKENS_ANALISE,
            system=SISTEMA_ANALISE,
            messages=[
                {
                    "role": "user",
                    "content": "Os números (JSON):\n" + json.dumps(dados, ensure_ascii=False),
                }
            ],
            output_config={"effort": "low"},
            timeout=anthropic.Timeout(90.0, connect=10.0),
        )
    except anthropic.AnthropicError:
        return None, None
    partes = [
        getattr(b, "text", "") for b in getattr(mensagem, "content", []) or [] if b.type == "text"
    ]
    texto = "\n".join(p for p in partes if p).strip()
    return (texto or None), mensagem


def chave_do_dia(dia: str) -> str:
    return CHAVE_ANALISE.format(dia=dia)


async def gerar_analise(
    session: AsyncSession, *, cliente: Any, modelo: str, agora: datetime | None = None
) -> dict | None:
    """Gera a análise do dia (o dia de São Paulo) e guarda no Redis. None se o modelo falhou."""
    from app.redis_client import redis
    from app.services.atendimento import ia
    from app.services.atendimento.agente import custo

    agora = agora or datetime.now(UTC)
    dia = agora.astimezone(ia.SAO_PAULO).date().isoformat()
    dados = await dados_da_analise(session, agora=agora)
    uso = custo.Uso()
    try:
        await custo.conferir_teto(uso=uso)
    except custo.TetoEstourado:
        return None  # o dia do agente já passou do teto: amanhã tem
    texto, mensagem = await texto_do_modelo(cliente, modelo=modelo, dados=dados)
    if mensagem is not None:
        try:
            await custo.registrar(mensagem.usage, str(getattr(mensagem, "model", modelo)), uso=uso)
        except custo.TetoEstourado:
            pass
    if texto is None:
        return None
    analise = {
        "dia": dia,
        "texto": texto,
        "gerada_em": agora.isoformat(),
        "modelo": modelo,
        "numeros": {"ontem": dados["ontem"]["total"], "semana": dados["semana"]["total"]},
    }
    valor = json.dumps(analise, ensure_ascii=False)
    await redis.set(chave_do_dia(dia), valor, ex=TTL_ANALISE_S)
    await redis.set(CHAVE_ANALISE_ULTIMA, valor, ex=TTL_ANALISE_S)
    return analise


async def ultima_analise() -> dict | None:
    """A análise mais recente (ou None: ainda não gerada, ou o Redis fora)."""
    from app.redis_client import redis

    try:
        bruto = await redis.get(CHAVE_ANALISE_ULTIMA)
    except Exception:  # noqa: BLE001 — Redis fora: a tela mostra sem a análise
        return None
    if not bruto:
        return None
    try:
        return json.loads(bruto)
    except (TypeError, ValueError):
        return None
