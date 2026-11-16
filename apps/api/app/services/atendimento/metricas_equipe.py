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
  específico"): RESPOSTAS = as respostas de verdade das lojas no CHAT (pelo DaVinci ou por
  fora; sem as automáticas, as que falharam e o recibo da evidência — `resposta_de_verdade_sql`),
  SAIU SOZINHO % = as da IA sobre elas; e o TEMPO MÉDIO da primeira resposta (a média das
  esperas respondidas, a conta de turnos do GET /metricas, `routers.atendimento._turnos_das_lojas`
  com as réguas da espera abaixo) e o "no prazo".
- 16/11/2026, o tempo "calculado certinho" (a auditoria com os dados de produção): a espera é
  do comprador ESCREVER até a loja responder de verdade (pessoa ou IA que manda sozinha) ou até
  o clique "Não precisa de resposta" — o cartão e a cortesia não abrem, o recibo não fecha, a
  conversa fechada ou bloqueada não fica vencida, e só o chat entra (ver "A espera do
  comprador").

A ANÁLISE DO DIA: uma vez por dia o modelo do agente lê esses números (ontem e 7 dias), os
assuntos que mais erram e os motivos curtos do juiz (já sem dado pessoal) e escreve um
resumo curto; fica no Redis um dia por chave (180 dias) — sem tabela nova; sumiu, gera de
novo. Nada de texto de cliente entra no pedido nem no log.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import Integer, and_, case, false, func, select

from app.services.atendimento import constantes, juiz_edicao

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

CHAVE_ANALISE = "atd:analise_diaria:{dia}"
CHAVE_ANALISE_ULTIMA = "atd:analise_diaria:ultima"
TTL_ANALISE_S = 180 * 24 * 3600
MAX_TOKENS_ANALISE = 900
MIN_DECISOES_ASSUNTO = 5


# ── A espera do comprador: o que abre e o que fecha (16/11/2026) ────────────────────
#
# O Eduardo: o tempo de resposta "calculado certinho" — do comprador ESCREVER até alguém da
# loja responder DE VERDADE (pessoa ou IA que manda sozinha), ou até o clique "Não precisa de
# resposta" (a hora do clique, que `routers.atendimento._turnos_das_lojas` lê e passa).
# A auditoria com os dados de produção do mesmo dia achou o relógio errado em cinco lugares:
# - o CARTÃO do produto/pedido que o comprador manda sem escrever nada abria a espera: 595 das
#   770 vencidas de 30 dias eram só o cartão, e a espera contava desde ele quando o comprador
#   só escrevia horas ou dias depois (46fa04a9: 3.761 min; a IA respondeu em 6,6);
# - o "ok"/"obrigado"/figurinha depois de a loja responder abria a espera, que esticava até a
#   pergunta nova (2b96dbfc: 5.968 min; a espera de verdade foi de 2,7);
# - o RECIBO fixo da IA ("Recebemos o vídeo, obrigado! A equipe analisa e responde por aqui.")
#   fechava a espera em 3 min, e a pessoa só respondia 15 h depois (mediana);
# - a conversa FECHADA ou BLOQUEADA continuava vencida;
# - a avaliação, o comentário e o carrinho entravam como chat (`pendente.do_chat_sql` tira).
# A conta dos turnos é `routers.atendimento._turnos_das_lojas`; as réguas puras, aqui.

# O que a fala do comprador é para a espera (`fala_do_comprador`).
FALA = "fala"  # pede resposta: abre a espera
CARTAO = "cartao"  # o cartão do produto/pedido, sem texto: nunca abre
CORTESIA = "cortesia"  # só "ok", "obrigado", emoji, figurinha: abre só se a loja perguntou
# A cortesia é texto curto: o que passa disto é fala (e é o que a consulta traz do texto).
MAX_CORTESIA = 80
# Nelas, a espera que ficou aberta sai da conta (nem vencida, nem no prazo), como a do "Não
# precisa de resposta" sem hora — a fila e a bolinha também tiram a fechada.
SITUACOES_SEM_ESPERA = frozenset({constantes.CONVERSA_FECHADA, constantes.CONVERSA_BLOQUEADA})
_TIPO_TEXTO = "texto"
# A figurinha do comprador: o rótulo que o adaptador grava no lugar do texto (Shopee `sticker`,
# TikTok `EMOTICONS`, Zap `sticker` — `constantes.rotulo_tipo_plataforma`).
_FIGURINHA = constantes.rotulo_tipo_plataforma(constantes.TIPO_SHOPEE_FIGURINHA)

# A CORTESIA (16/11/2026): a régua do agente para o "só reconhecimento"
# (`acao_pessoa.so_reconhecimento`, Kia ao vivo, 21/10/2026: "Ok Aguardando", "Obrigada"), com
# duas travas a mais — aqui o erro barato é contar uma espera a mais, não a menos:
# - tem de ter uma palavra de cortesia de verdade (o "ok", o "obrigado", o "beleza"...):
#   "retorno", "então" e "até amanhã" sozinhos não são;
# - o cumprimento e a espera ABREM: "oi", "bom dia", "aguardo", "esperando", "por favor" — a
#   auditoria: "oi", "?" e "alguém?" são espera de verdade, e "aguardo retorno" cobra.
# "?" derruba (o `so_reconhecimento` já derruba), e passar de `MAX_CORTESIA` também.
_NUCLEO_DA_CORTESIA = frozenset(
    """
    ok okay okk oks blz beleza certo certinho ta bom boa bem entendi entendido entendo
    combinado perfeito otimo otima show joia legal valeu vlw obrigado obrigada obrigados
    obrigadas obg obgd brigado brigada grato grata agradeco agradecida agradecido tranquilo
    tranquila nada
    """.split()
)
_ABREM_ESPERA = frozenset(
    """
    oi ola dia tarde noite aguardo aguardando aguardar aguardarei espero esperando esperar
    favor gentileza retorno
    """.split()
)


def resposta_de_verdade_sql(m: Any) -> Any:
    """A mensagem da LOJA que responde o comprador de verdade (16/11/2026), em SQL — nunca NULL.

    Pela pessoa no DaVinci, pela IA que mandou sozinha ou por fora (Duoke, a central da
    plataforma). Não é: a que falhou (não saiu), a automática (`gravar.mensagem_automatica_sql`:
    o robô e as campanhas do Duoke, os cartões da plataforma, as automações do DaVinci) e o
    RECIBO fixo da evidência (`acao_pessoa.TEXTOS_DO_RECIBO`, o item 9 de 03/11/2026: a IA manda
    na hora, sem o modelo, e a conversa vai para o topo da Caixa Humano — quem responde é a
    pessoa, depois). O recibo é reconhecido pelo texto exato, o único jeito que ele tem: sai com
    origem `davinci_ia` e só a chave `envio` no `payload`. `m` = AtendimentoMensagem (ou alias).
    """
    from app.services.atendimento import gravar
    from app.services.atendimento.agente import acao_pessoa

    recibo = func.coalesce(m.texto.in_(sorted(acao_pessoa.TEXTOS_DO_RECIBO)), false())
    return and_(
        m.autor == constantes.AUTOR_LOJA,
        m.status != constantes.MSG_FALHOU,
        ~gravar.mensagem_automatica_sql(m.texto, m.payload),
        ~recibo,
    )


def _so_emoji(texto: str) -> bool:
    """Só emoji (com o tom de pele, o "❤️", e pontuação junto): "👍", "🙏🏻", "❤️!". PURA."""
    simbolos = 0
    for ch in texto:
        if ch.isspace():
            continue
        categoria = unicodedata.category(ch)
        if categoria == "So":
            simbolos += 1
        elif categoria not in ("Sk", "Mn", "Me", "Cf") and not categoria.startswith("P"):
            return False
    return simbolos > 0


def so_cortesia(texto: str | None) -> bool:
    """A fala do comprador é SÓ cortesia — "ok", "obrigado(a)", "valeu", "blz", emoji (ver
    acima)? PURA. "ok mas e o rastreio?", "bom dia" e "fico no aguardo" não são."""
    from app.services.atendimento import validador
    from app.services.atendimento.agente import acao_pessoa

    bruto = (texto or "").strip()
    # O tamanho do texto como veio (a consulta traz `MAX_CORTESIA + 1`): o cortado é longo.
    if not bruto or len(texto or "") > MAX_CORTESIA:
        return False
    plano = validador.plano_de(bruto)
    if "?" in plano:
        return False
    if _so_emoji(bruto):
        return True
    if not acao_pessoa.so_reconhecimento(bruto):
        return False
    palavras = set(re.findall(r"[a-z0-9]+", plano))
    return bool(palavras & _NUCLEO_DA_CORTESIA) and not palavras & _ABREM_ESPERA


def fala_do_comprador(
    *,
    tipo: str | None,
    texto: str | None,
    payload: Mapping[str, Any] | None = None,
    tem_anexo: bool = False,
) -> str:
    """O que a mensagem do comprador é para a espera: `CARTAO`, `CORTESIA` ou `FALA`. PURA.

    - CARTAO: o cartão do produto/pedido sem texto dele — as réguas da fila e do processamento
      (`constantes.e_cartao_do_comprador`, pelo item cru; `sem_demanda.cartao_sem_texto`, que
      também vê o "[Mensagem]" do cartão de variação). O cartão com texto é fala;
    - CORTESIA: a figurinha (o rótulo "[Figurinha]") e o texto só de cortesia, sem anexo
      (`so_cortesia`);
    - FALA: o resto — pergunta, pedido, cumprimento, foto, vídeo, áudio.
    `payload` basta com as chaves `message_type` (Shopee) e `type` (TikTok) do item cru.
    """
    from app.services.atendimento import sem_demanda

    cru = dict(payload or {})
    if constantes.e_cartao_do_comprador(cru):
        sem_texto = sem_demanda.cartao_sem_texto(SimpleNamespace(texto=texto, payload=cru))
        return CARTAO if sem_texto else FALA
    if (texto or "").strip() == _FIGURINHA:
        return CORTESIA
    if (tipo or _TIPO_TEXTO) == _TIPO_TEXTO and not tem_anexo and so_cortesia(texto):
        return CORTESIA
    return FALA


@dataclass(frozen=True, slots=True)
class Linha:
    """Uma mensagem na conta da espera: do comprador (`fala`, de `fala_do_comprador`) ou a
    resposta de verdade da loja (`resposta_de_verdade_sql`; `pergunta` = ela tinha "?")."""

    cliente: bool
    quando: datetime
    fala: str = FALA
    pergunta: bool = False


def esperas(
    linhas: Iterable[Linha], clique: datetime | None = None
) -> list[tuple[datetime, datetime | None]]:
    """As esperas do comprador numa conversa, na ordem: (o começo, o fim). PURA (16/11/2026).

    `linhas` vêm no relógio da plataforma. A espera começa na primeira fala que PEDE resposta
    depois da última resposta de verdade da loja (as falas seguidas dele juntam na mesma) e
    termina na próxima resposta de verdade — ou no clique "Não precisa de resposta" (`clique`;
    só o último fica gravado), se a pessoa marcou antes dela. Fim None = ainda sem resposta
    (só a última pode ser assim).

    - O CARTÃO nunca abre nem estica: a espera começa na primeira fala escrita depois dele.
    - A CORTESIA não abre — menos quando responde a uma pergunta da loja (a última resposta de
      verdade dela tinha "?": o "ok" de "posso cancelar?" é resposta, como em
      `acao_pessoa.turno_so_de_reconhecimento`). A pergunta de verdade que vem depois abre.
    - A vez feita só de cartão ou de cortesia não é espera: nem respondida, nem vencida — e a
      resposta da loja a ela (o "de nada" ao "obrigado") não é tempo de nada.
    """
    saida: list[tuple[datetime, datetime | None]] = []
    inicio: datetime | None = None
    perguntou = False
    for linha in linhas:
        if inicio is not None and clique is not None and inicio <= clique < linha.quando:
            # A pessoa marcou antes desta mensagem: a espera fechou no clique (sem pergunta).
            saida.append((inicio, clique))
            inicio, clique, perguntou = None, None, False
        if linha.cliente:
            # A cortesia e o cartão em resposta a uma pergunta da loja são a resposta
            # do comprador (como `sem_demanda.cartao_abre_visita`): abrem a espera.
            abre = linha.fala == FALA or (linha.fala in (CORTESIA, CARTAO) and perguntou)
            if inicio is None and abre:
                inicio = linha.quando
            continue
        if inicio is not None:
            saida.append((inicio, linha.quando))
            inicio = None
        perguntou = linha.pergunta
    if inicio is not None and clique is not None and clique >= inicio:
        saida.append((inicio, clique))
        inicio = None
    if inicio is not None:
        saida.append((inicio, None))
    return saida


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
            # Para BAIXO (16/11/2026): 89,95% não pode virar "90%" na tela.
            "pct_no_prazo": (1000 * self.no_prazo // decididas) / 10 if decididas else None,
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
    from app.services.atendimento import pendente

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
    # As respostas das lojas no CHAT (16/11/2026): a mesma régua do tempo de resposta
    # (`resposta_de_verdade_sql`) — a mensagem automática (o robô e as campanhas do Duoke, os
    # cartões da plataforma, as automações do DaVinci) e o recibo da evidência não são resposta;
    # a que falhou não saiu. Só o chat (`pendente.do_chat_sql`): o e-mail fica na aba dele, e a
    # avaliação, a reclamação, o comentário e o carrinho têm os filtros deles.
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
                resposta_de_verdade_sql(m),
                pendente.do_chat_sql(c),
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
