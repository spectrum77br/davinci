"""As MÉTRICAS POR EQUIPE e a análise do dia (13/11/2026).

O Eduardo: "em métricas ... por equipe e não mais por loja; traz as %; faz o agente 1 vez
por dia trazer uma análise geral; deixa a tela mais simples; abrindo o select, as lojas
todas e as %".

EQUIPE = a divisão da planilha "Separação de contas" (Vendedor 1 = Londres, 2 = Israel,
3 = Marrocos): a pessoa de cada equipe de vendas (`users.sales_teams` 101/102/103,
`team_scope.NOMES_EQUIPES`) e as lojas dela em `atendimento_lojas_pessoa`, montadas pelo mesmo
escopo das telas (`escopo.escopo_do_atendimento`). Quem não é de uma das três (o login da
empresa com as lojas todas, 16/11/2026) não é equipe. Por loja e por equipe, no período:

- APROVAÇÃO DA IA: das decisões da equipe sobre o rascunho (o juiz, `juiz_edicao`: igual ou
  só forma = acerto; conteúdo ou outra ação = erro; só saudação fica fora), quantas foram
  acerto — a mesma conta da régua do envio sozinho;
- SAIU SOZINHO: as respostas que a IA mandou (origem `davinci_ia`);
- PELA EQUIPE: as respostas que a pessoa mandou pelo DaVinci (origem `davinci_humano`);
- 16/11/2026 (o Eduardo: "uma lista em cima a % de mensagens enviadas automático, daí a lista
  mostrando tempo de resposta de cada equipe pegando a média geral, e se abrir mostra
  específico"): RESPOSTAS = as respostas das lojas no CHAT (pelo DaVinci ou por fora, sem as
  automáticas nem as que falharam — a régua de `gravar.mensagem_automatica_sql`), SAIU
  SOZINHO % = as da IA sobre elas; e o TEMPO MÉDIO da primeira resposta (a média de todas as
  esperas do cliente, a conta de turnos do GET /metricas, `routers.atendimento._turnos_das_lojas`)
  e o "no prazo".

A ANÁLISE DO DIA: uma vez por dia o modelo do agente lê esses números (ontem e 7 dias), os
assuntos que mais erram e os motivos curtos do juiz (já sem dado pessoal) e escreve um
resumo curto; fica no Redis um dia por chave (180 dias) — sem tabela nova; sumiu, gera de
novo. Nada de texto de cliente entra no pedido nem no log.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Mapping
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
    # 16/11/2026: as respostas das lojas no chat (todas as origens, sem as automáticas) e, dos
    # turnos do cliente, os minutos de cada primeira resposta, as no prazo e as vencidas.
    respostas: int = 0
    tempos: list[float] = field(default_factory=list)
    no_prazo: int = 0
    vencidas: int = 0

    def somar(self, outro: Numeros) -> None:
        self.decisoes += outro.decisoes
        self.acertos += outro.acertos
        self.ia_enviou += outro.ia_enviou
        self.equipe_enviou += outro.equipe_enviou
        self.respostas += outro.respostas
        self.tempos.extend(outro.tempos)
        self.no_prazo += outro.no_prazo
        self.vencidas += outro.vencidas

    @property
    def aprovacao(self) -> float | None:
        return self.acertos / self.decisoes if self.decisoes else None

    @property
    def sozinho(self) -> float | None:
        """Das respostas das lojas no chat, a parte que a IA mandou sozinha."""
        return min(1.0, self.ia_enviou / self.respostas) if self.respostas else None

    def para_json(self) -> dict[str, Any]:
        a = self.aprovacao
        so = self.sozinho
        n = len(self.tempos)
        decididas = n + self.vencidas
        return {
            "decisoes": self.decisoes,
            "acertos": self.acertos,
            "aprovacao": round(a, 4) if a is not None else None,
            "ia_enviou": self.ia_enviou,
            "equipe_enviou": self.equipe_enviou,
            "respostas": self.respostas,
            # Por fora do DaVinci (Duoke, a central da plataforma).
            "fora": max(0, self.respostas - self.ia_enviou - self.equipe_enviou),
            "pct_sozinho": round(so, 4) if so is not None else None,
            "respondidas": n,
            "tempo_medio_min": round(sum(self.tempos) / n, 1) if n else None,
            "pct_no_prazo": round(100 * self.no_prazo / decididas, 1) if decididas else None,
        }


@dataclass
class Equipe:
    user_id: UUID
    nome: str
    integration_ids: set[UUID] = field(default_factory=set)
    numero: int | None = None


async def equipes(session: AsyncSession) -> list[Equipe]:
    """As equipes do atendimento — Londres, Israel e Marrocos (`users.sales_teams`) — e as
    integrações das lojas das pessoas delas (`atendimento_lojas_pessoa`), na ordem do número.

    16/11/2026: a tela mostrava o login "cairo sa" (as 165 lojas, sem equipe de vendas) como
    equipe. Só é equipe quem é de uma das três; duas pessoas da mesma equipe somam as lojas.
    """
    from app.deps.team_scope import NOMES_EQUIPES
    from app.models import User
    from app.models.atendimento_lojas_pessoa import AtendimentoLojaPessoa
    from app.services.atendimento import escopo

    ids = (await session.scalars(select(AtendimentoLojaPessoa.user_id).distinct())).all()
    por_numero: dict[int, Equipe] = {}
    for user in (await session.scalars(select(User).where(User.id.in_(ids)))).all():
        numeros = [n for n in (getattr(user, "sales_teams", None) or []) if n in NOMES_EQUIPES]
        if not numeros:
            continue  # sem equipe de vendas (o login da empresa) não é equipe
        scope = await escopo.escopo_do_atendimento(session, user)
        if scope.unrestricted:
            continue  # admin com lojas marcadas não é equipe
        numero = min(numeros)
        equipe = por_numero.setdefault(
            numero, Equipe(user.id, NOMES_EQUIPES[numero], numero=numero)
        )
        equipe.integration_ids |= set(scope.integration_ids)
    return [por_numero[n] for n in sorted(por_numero)]


async def numeros_por_loja(
    session: AsyncSession, desde: datetime, ate: datetime
) -> dict[UUID | None, Numeros]:
    """Por integração: as decisões julgadas, os acertos e as respostas da IA e da equipe."""
    from app.models import AtendimentoConversa, AtendimentoMensagem
    from app.models.atendimento import AtendimentoJulgamento as J
    from app.services.atendimento import gravar, separa_email

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
    # As respostas das lojas no CHAT (16/11/2026): a mesma régua da fila e do tempo de resposta
    # — a mensagem automática (o robô e as campanhas do Duoke, os cartões da plataforma, as
    # automações do DaVinci) não é resposta; a que falhou não saiu; o e-mail fica na aba dele.
    m, c = AtendimentoMensagem, AtendimentoConversa
    momento = func.coalesce(m.enviada_em, m.created_at)
    envios = (
        await session.execute(
            select(
                c.integration_id,
                func.count().filter(m.origem == constantes.ORIGEM_IA).label("ia"),
                func.count().filter(m.origem == constantes.ORIGEM_HUMANO).label("eq"),
                func.count().label("todas"),
            )
            .join(c, c.id == m.conversa_id)
            .where(
                m.autor == constantes.AUTOR_LOJA,
                m.status != constantes.MSG_FALHOU,
                ~gravar.mensagem_automatica_sql(m.texto, m.payload),
                separa_email.na_caixa_sql(c),
                momento >= desde,
                momento < ate,
            )
            .group_by(c.integration_id)
        )
    ).all()
    for integ, ia, eq, todas in envios:
        saida[integ].ia_enviou += int(ia or 0)
        saida[integ].equipe_enviou += int(eq or 0)
        saida[integ].respostas += int(todas or 0)
    return dict(saida)


async def resumo(
    session: AsyncSession,
    *,
    dias: int,
    agora: datetime | None = None,
    turnos: Mapping[tuple[UUID | None, str, UUID | None], Mapping[str, Any]] | None = None,
    nomes_das_lojas: Mapping[UUID, str] | None = None,
) -> dict:
    """As equipes (com as % somadas das lojas delas), "sem equipe" e as lojas todas, no período.

    `turnos` = os de `routers.atendimento._turnos_das_lojas` no mesmo período (o tempo de
    resposta; sem eles, nada de tempo); `nomes_das_lojas` = o nome de hoje de cada loja (o da
    Caixa), no lugar do nome da integração.
    """
    from app.models import Integration

    agora = agora or datetime.now(UTC)
    desde = agora - timedelta(days=dias)
    por_loja = await numeros_por_loja(session, desde, agora)
    for (integ, _plataforma, _robo), t in (turnos or {}).items():
        if integ is None:
            continue  # a loja do robô (sem integração) não é de equipe
        n = por_loja.setdefault(integ, Numeros())
        n.tempos.extend(float(x) for x in t.get("tempos") or [])
        n.no_prazo += int(t.get("no_prazo") or 0)
        n.vencidas += int(t.get("vencidas") or 0)
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
                "loja": (nomes_das_lojas or {}).get(i) or nome or "—",
                "plataforma": getattr(plat, "value", plat),
                "equipe": dono.get(i),
                **n.para_json(),
            }
        )
    lojas.sort(key=lambda x: (x["equipe"] or "~", -(x["respostas"] or 0), -(x["decisoes"] or 0)))
    total = Numeros()
    saida_times = []
    for t in times:
        soma = Numeros()
        for i in t.integration_ids:
            if i in por_loja:
                soma.somar(por_loja[i])
        saida_times.append({"equipe": t.nome, "lojas": len(t.integration_ids), **soma.para_json()})
    sem = Numeros()
    sem_lojas = 0
    for i, n in por_loja.items():
        total.somar(n)
        if i is not None and i not in dono:
            sem.somar(n)
            sem_lojas += 1
    return {
        "dias": dias,
        "equipes": saida_times,
        "sem_equipe": {"lojas": sem_lojas, **sem.para_json()},
        "lojas": lojas,
        "total": total.para_json(),
    }


# ── A análise do dia ────────────────────────────────────────────────────────────

SISTEMA_ANALISE = (
    "Você é o analista do atendimento ao cliente de uma loja online brasileira (Shopee, "
    "Mercado Livre, TikTok Shop, Amazon). A IA escreve o rascunho de cada resposta; a equipe "
    "aprova, ajusta ou reescreve; o que a equipe aprova em 75% dos casos de um assunto passa a "
    "sair sozinho (85% em reembolso, cancelamento, troca e reclamação). Escreva para o dono, em "
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
