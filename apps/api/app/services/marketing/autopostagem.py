"""O robô que escolhe o vídeo e a hora sozinho (Eduardo, 24/09/2026).

"tem posts por dia e intervalo, mas onde boto que horas ele começa?"

A pergunta expôs que a seção "Publicação automática" prometia mais do que
entregava: `postagem_max_dia` e `postagem_intervalo_min` eram GUARDA-CORPOS —
recusavam agendamento apertado demais — e nada mais. Quem escolhia o vídeo e a
hora era sempre uma pessoa. As 14 postagens que existiam em produção tinham
todas `origem = manual`.

Este módulo é a peça que faltava. Com a conta configurada (interruptor ligado +
hora de início), ele monta a grade do dia:

    18h de início, intervalo 60, teto 2  →  um às 18h, outro às 19h

REGRA DE ESCOLHA: o criativo aprovado MAIS RECENTE que ainda não saiu naquela
conta. Escolhida pelo Eduardo — "tacamos do mais recente". Continua sendo o
padrão, mas desde 25/09/2026 dá pra passar um vídeo na frente: "eu ia querer
um video mais antigo rodasse antes". O criativo com `fila_posicao` preenchida
fura a fila (o menor primeiro) e só depois vem o resto, do mais recente pro
mais antigo. A ordem mora em `ordem_da_fila()`, que o robô e a tela "Fila do
robô" usam juntos — a tela não pode mostrar uma ordem e o robô seguir outra.

ELE AGENDA, NÃO PUBLICA. Cria a postagem em `agendado`, que aparece na tela e
dá pra cancelar antes de sair; o publicador que já existe faz o resto. Duas
razões: nada acontece invisível, e a lógica de publicar não vira duas cópias
que divergem — foi exatamente isso que quebrou o modal do TikTok em 23/09.

E ele passa pelo `agendar()` de sempre, com as MESMAS guardas do clique
humano: criativo aprovado, arquivo no disco, conta ativa, teto do dia,
intervalo, nada em voo pro mesmo vídeo. Um robô que pula as guardas do humano
é um robô que publica o que o humano não deixaria.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import (
    MarketingCreative,
    MarketingCreativeFile,
    MarketingPostagem,
    RedeSocial,
)
from app.services.marketing.postagens import (
    BRT,
    PLATAFORMAS_SUPORTADAS,
    STATUS_OCUPA_CONTA,
    RoboError,
    _espacamento_min,
    _teto_dia,
    agendar,
    motivo_do_arquivo,
)

logger = structlog.get_logger()


async def contas_ligadas(
    session: AsyncSession, *, marca_id: UUID | None = None
) -> list[RedeSocial]:
    """Contas onde o robô tem permissão de publicar sozinho.

    Exige as DUAS coisas: o interruptor ligado E a hora preenchida. O
    interruptor sozinho não basta porque, até hoje, ele significava outra coisa
    ("pode executar o que foi agendado à mão") — e há contas com ele ligado por
    esse motivo. Tratar essas como autorizadas faria o robô começar a publicar
    sozinho no dia do deploy, em conta que ninguém pediu.

    `marca_id` restringe a uma marca (a tela da fila); sem ele, todas (a rodada).
    """
    stmt = select(RedeSocial).where(
        RedeSocial.ativo.is_(True),
        RedeSocial.postagem_auto.is_(True),
        RedeSocial.postagem_hora_inicio.isnot(None),
    )
    if marca_id is not None:
        stmt = stmt.where(RedeSocial.marca_id == marca_id)
    return list((await session.execute(stmt)).scalars().all())


def horarios_do_dia(rede: RedeSocial, settings, *, agora: datetime) -> list[datetime]:
    """A grade de horários de hoje para esta conta.

    18h de início, 60 de intervalo, teto 2 → [18:00, 19:00].

    A grade é do dia CORRENTE em Brasília e não atravessa a meia-noite: o post
    que não coube hoje não vira madrugada de amanhã sozinho. Se a conta pedir
    mais posts do que cabem entre a hora de início e o fim do dia, os que não
    couberem simplesmente não saem — silêncio é melhor que post às 3h.
    """
    hoje = agora.astimezone(BRT).replace(
        hour=int(rede.postagem_hora_inicio or 0), minute=0, second=0, microsecond=0
    )
    passo = timedelta(minutes=max(1, _espacamento_min(rede, settings)))
    grade = []
    for i in range(max(0, _teto_dia(rede, settings))):
        quando = hoje + passo * i
        if quando.date() != hoje.date():
            break
        grade.append(quando)
    return grade


async def _ja_ocupados(session: AsyncSession, rede: RedeSocial, dia: datetime) -> int:
    """Quantos posts esta conta já tem no dia (agendados, em voo ou no ar).

    Conta o que OCUPA a conta, não só o publicado: postagem agendada pelo robô
    de manhã tem que impedir a rodada da tarde de agendar por cima. Cancelado e
    falhou não contam — ali está provado que não saiu.
    """
    ini = dia.astimezone(BRT).replace(hour=0, minute=0, second=0, microsecond=0)
    return (
        await session.execute(
            select(func.count())
            .select_from(MarketingPostagem)
            .where(
                MarketingPostagem.rede_social_id == rede.id,
                MarketingPostagem.status.in_(STATUS_OCUPA_CONTA),
                func.coalesce(
                    MarketingPostagem.agendado_para, MarketingPostagem.publicado_em,
                    MarketingPostagem.created_at,
                )
                >= ini,
                func.coalesce(
                    MarketingPostagem.agendado_para, MarketingPostagem.publicado_em,
                    MarketingPostagem.created_at,
                )
                < ini + timedelta(days=1),
            )
        )
    ).scalar_one()


def ordem_da_fila() -> tuple:
    """A ordem em que o robô tira os vídeos da fila — UMA fonte só.

    Usada por `proximo_criativo` (o robô) e por `fila` (a tela "Fila do
    robô"). Se cada lado tivesse o seu ORDER BY, bastava alguém mexer num pra
    tela prometer um vídeo e o robô postar outro.

      1. `fila_posicao` ASC, NULLS LAST — quem o Eduardo passou na frente sai
         primeiro, o menor antes; NULL é a ordem normal e vem depois de todos.
      2. o criativo mais recente — o "tacamos do mais recente" de sempre.
      3. dentro do criativo, o arquivo mais recente (a linha aceita vários).

    Os `id` no fim só desempatam data igual: sem eles o Postgres pode
    devolver empates em ordem diferente a cada consulta, e a tela e o robô
    discordariam justo no empate. O `id` do criativo antes do arquivo mantém
    os vídeos de uma mesma linha juntos na fila.
    """
    return (
        MarketingCreative.fila_posicao.asc().nulls_last(),
        MarketingCreative.created_at.desc(),
        MarketingCreative.id.desc(),
        MarketingCreativeFile.created_at.desc(),
        MarketingCreativeFile.id.desc(),
    )


def _candidatos(marca_id: UUID) -> list:
    """O que PODE entrar na fila da marca — o robô e a tela usam o mesmo."""
    return [
        MarketingCreative.marca_id == marca_id,
        MarketingCreative.aprovado.is_(True),
        MarketingCreativeFile.file_rel.isnot(None),
        # SÓ VÍDEO. O upload aceita imagem e PDF (a linha do criativo
        # guarda referência, arte, carrossel), e sem este filtro o
        # robô escolheria o JPG mais recente e tentaria publicá-lo
        # como Reel. O modal manual já pré-seleciona vídeo; aqui não
        # havia ninguém pra pré-selecionar.
        MarketingCreativeFile.file_mime.ilike("video/%"),
    ]


async def proximo_criativo(
    session: AsyncSession,
    rede: RedeSocial,
    *,
    excluir: set[UUID] | frozenset[UUID] = frozenset(),
) -> tuple[MarketingCreative, MarketingCreativeFile] | None:
    """O primeiro da fila da marca que ainda não saiu nesta conta.

    Sem ninguém mexer na fila, é o aprovado MAIS RECENTE — "tacamos do mais
    recente", escolha do Eduardo. Consequência que vale saber: um criativo
    novo fura a fila dos antigos, e um vídeo antigo que nunca saiu pode nunca
    sair se a produção não parar. Era exatamente isso que ele queria poder
    contornar (25/09/2026): o criativo com `fila_posicao` sai antes, em
    qualquer conta onde ainda não saiu. Ver `ordem_da_fila()`.

    "Já saiu nesta conta" é por ARQUIVO, não por criativo: a linha do criativo
    aceita vários vídeos, e cada um é uma publicação diferente. E é POR
    CONTA: o vídeo que furou a fila e já saiu no TikTok continua na frente no
    Instagram até sair lá — e no TikTok o robô volta ao mais recente.

    `excluir` são arquivos que a rodada já viu serem recusados NESTA conta
    por motivo do próprio vídeo (ver `MOTIVOS_DO_VIDEO`): pula pro seguinte.
    """
    if rede.marca_id is None:
        return None
    # QUALQUER postagem deste arquivo nesta conta, em qualquer estado, tira o
    # arquivo da fila do robô. Sem filtro de status DE PROPÓSITO.
    #
    # A tentação é reusar `STATUS_OCUPA_CONTA`, que é a lista do teto diário —
    # e foi o que eu fiz primeiro. Mas ela exclui `cancelado` e `falhou`, com
    # razão (ali está provado que não saiu, então não devem segurar vaga). Pro
    # ROBÔ a pergunta é outra, e as duas respostas divergem justo onde dói:
    #
    #   cancelado  uma PESSOA disse não pra este vídeo nesta conta. Repropor
    #              uma hora depois transforma o "não" dela num "sim"
    #              automático — e no ar não tem desfazer.
    #   falhou     pode ter falhado COM container criado na Meta, ou seja, o
    #              vídeo talvez tenha saído. É exatamente o estado em que o
    #              botão de retentar humano se RECUSA a agir
    #              ("postagem_precisa_conferir_na_meta"), porque retentar ali
    #              duplica o Reel. O robô entraria por uma porta onde essa
    #              guarda não existe.
    #
    # Retomar o que falhou é decisão de gente, no botão de retentar, que sabe
    # conferir a conta antes. O robô só propõe o que nunca foi tentado.
    saiu = select(MarketingPostagem.file_id).where(
        MarketingPostagem.rede_social_id == rede.id
    )
    filtros = [*_candidatos(rede.marca_id), MarketingCreativeFile.id.notin_(saiu)]
    if excluir:
        filtros.append(MarketingCreativeFile.id.notin_(list(excluir)))
    linha = (
        await session.execute(
            select(MarketingCreative, MarketingCreativeFile)
            .join(MarketingCreativeFile, MarketingCreativeFile.creative_id == MarketingCreative.id)
            .where(*filtros)
            .order_by(*ordem_da_fila())
            .limit(1)
        )
    ).first()
    return (linha[0], linha[1]) if linha else None


@dataclass
class ItemFila:
    criativo: MarketingCreative
    arquivo: MarketingCreativeFile
    # As contas onde este vídeo AINDA não saiu (nem foi tentado).
    pendente_em: list[RedeSocial]
    # Por que o robô vai PULAR este arquivo em toda conta ("arquivo_sumiu",
    # "sem_arquivo"), ou None. A posição continua a dele — quem decide tirar
    # da fila é gente —, mas a tela avisa em vez de prometer um vídeo que
    # não vai sair.
    bloqueado: str | None = None


async def contas_da_fila(session: AsyncSession, marca_id: UUID) -> list[RedeSocial]:
    """As contas contra as quais a fila da marca é medida.

    As do robô, quando existem — é a fila DELE. Sem nenhuma conta com o robô
    ligado, caem as contas ativas das plataformas que o robô sabe publicar:
    a marca que ainda não ligou o robô também precisa ver e arrumar a fila
    ANTES de ligar, senão o primeiro dia sai na ordem que ninguém escolheu.
    """
    contas = await contas_ligadas(session, marca_id=marca_id)
    if not contas:
        contas = list(
            (
                await session.execute(
                    select(RedeSocial).where(
                        RedeSocial.marca_id == marca_id,
                        RedeSocial.ativo.is_(True),
                        RedeSocial.plataforma.in_(PLATAFORMAS_SUPORTADAS),
                    )
                )
            )
            .scalars()
            .all()
        )
    return sorted(contas, key=lambda r: (r.plataforma, (r.conta or "").lower(), str(r.id)))


async def fila(
    session: AsyncSession, marca_id: UUID, *, so_priorizados: bool = False
) -> list[ItemFila]:
    """A fila do robô para a marca, na ordem em que ele vai tirar.

    Um item por ARQUIVO de vídeo que ainda não saiu em pelo menos uma das
    `contas_da_fila`. Mesmos candidatos e mesma ordem do `proximo_criativo`;
    "já saiu" também igual — qualquer postagem, em qualquer estado.

    `so_priorizados` corta nos que furaram a fila: é o que a listagem precisa
    pra numerar o selo "#n na fila" sem carregar a fila inteira.
    """
    contas = await contas_da_fila(session, marca_id)
    if not contas:
        return []
    stmt = (
        select(MarketingCreative, MarketingCreativeFile)
        .join(MarketingCreativeFile, MarketingCreativeFile.creative_id == MarketingCreative.id)
        .where(*_candidatos(marca_id))
        .order_by(*ordem_da_fila())
    )
    if so_priorizados:
        stmt = stmt.where(MarketingCreative.fila_posicao.isnot(None))
    linhas = (await session.execute(stmt)).all()
    if not linhas:
        return []
    saiu = {
        (fid, rid)
        for fid, rid in (
            await session.execute(
                select(MarketingPostagem.file_id, MarketingPostagem.rede_social_id).where(
                    MarketingPostagem.rede_social_id.in_([c.id for c in contas]),
                    MarketingPostagem.file_id.in_([f.id for _c, f in linhas]),
                )
            )
        ).all()
    }
    itens = []
    for criativo, arquivo in linhas:
        pendente = [c for c in contas if (arquivo.id, c.id) not in saiu]
        if pendente:
            itens.append(ItemFila(criativo, arquivo, pendente, motivo_do_arquivo(arquivo)))
    return itens


def posicoes_efetivas(itens: list[ItemFila]) -> dict[UUID, int]:
    """criativo → 1, 2, 3… entre os que furaram a fila E ainda têm onde sair.

    O número gravado em `fila_posicao` é só chave de ordem: o vídeo que furou
    a fila e já saiu em todas as contas continua com o número dele no banco.
    Mostrar esse número cru deixava o selo "#1 na fila" num vídeo que saiu
    semana passada — e o de verdade aparecia como "#2". A tela recebe a
    posição que o robô de fato vai seguir.
    """
    out: dict[UUID, int] = {}
    for it in itens:
        if it.criativo.fila_posicao is not None and it.criativo.id not in out:
            out[it.criativo.id] = len(out) + 1
    return out


# Recusas do `agendar()` que dizem respeito AO VÍDEO, não à conta: outro vídeo
# pode sair na mesma vaga. `sem_legenda` e `legenda_template_invalido` entram
# porque a legenda pode vir do próprio criativo (ou do produto dele) — mas
# também podem ser da marca inteira (biblioteca vazia ou variação quebrada), e
# aí TODO vídeo seria recusado: por isso o teto de pulos abaixo.
MOTIVOS_DO_VIDEO = frozenset({
    "sem_arquivo",
    "arquivo_sumiu",
    "sem_legenda",
    "legenda_template_invalido",
    "video_ja_usado_em_outra_marca",
})
# Quantos vídeos recusados a rodada pula numa conta antes de desistir dela.
# Sem teto, uma marca sem legenda cadastrada faria a rodada tentar a fila
# inteira, vídeo por vídeo, a cada passada.
MAX_PULOS_POR_CONTA = 5


async def rodada(session: AsyncSession, *, agora: datetime | None = None) -> dict[str, int]:
    """Uma passada: agenda o que couber na grade de hoje de cada conta."""
    agora = agora or datetime.now(UTC)
    settings = get_settings()
    r = {"contas": 0, "agendadas": 0, "sem_criativo": 0, "recusadas": 0}

    for rede in await contas_ligadas(session):
        r["contas"] += 1
        # Quais vagas da grade de hoje JÁ CHEGARAM — todas, sem janela.
        agora_brt = agora.astimezone(BRT)
        chegaram = [h for h in horarios_do_dia(rede, settings, agora=agora) if h <= agora_brt]
        if not chegaram:
            continue
        # As que chegaram mas ainda não foram preenchidas. A grade é
        # sequencial: os `ocupados` primeiros horários são os que já saíram,
        # então a próxima vaga livre é a de índice `ocupados`.
        #
        # A PRIMEIRA versão disto filtrava a grade pela janela de atraso ANTES
        # de subtrair os ocupados — e com a configuração real do Eduardo (12h e
        # 19h, intervalo de 7 horas) o post das 19h nunca saía. Às 19h a vaga
        # das 12h já estava fora da janela de 6h, sobrava só a das 19h, e ela
        # era subtraída do post das 12h: 1 − 1 = 0. Com intervalo curto as
        # duas vagas cabem na janela e o erro não aparece — por isso os testes
        # passavam. Pego por simulação do dia inteiro antes de ligar.
        ocupados = await _ja_ocupados(session, rede, agora)
        livres = chegaram[ocupados:]
        # E só as que chegaram HÁ POUCO. O teto de atraso é o mesmo do
        # promotor de agendadas: preencher "8" às 20h não pode despejar os
        # posts das 08h, 09h… nos minutos seguintes, fora da janela que a
        # pessoa acabou de escolher.
        piso = agora_brt - timedelta(minutes=settings.marketing_postagem_atraso_max_min)
        cabem = len([h for h in livres if h >= piso])
        agendadas_aqui = 0
        pulados: set[UUID] = set()
        while agendadas_aqui < cabem:
            achado = await proximo_criativo(session, rede, excluir=pulados)
            if achado is None:
                r["sem_criativo"] += 1
                break
            criativo, arquivo = achado
            # Guardado antes: a recusa `postagem_em_voo` vem de um rollback,
            # que expira os objetos da sessão.
            arquivo_id = arquivo.id
            try:
                # Sem `agendado_para`: publica no próximo ciclo. O horário da
                # grade já passou — marcar hora no passado seria pedir pro
                # promotor tratar como atraso e mandar pra `revisar`.
                await agendar(
                    session,
                    creative=criativo,
                    file=arquivo,
                    redes=[rede],
                    user_id=None,
                    # Ele se IDENTIFICA, em vez de deixar o agendar() deduzir
                    # pela hora. Sem isto ele passava por clique humano e as
                    # guardas que existem só pro robô não disparavam — a pior
                    # delas, `sem_legenda`, deixava sair Reel sem legenda.
                    automatico=True,
                    # E a postagem nasce marcada como dele: é por `origem` que
                    # o publicador decide reexaminar o interruptor na hora de
                    # publicar. Gravada como `manual`, desligar não segurava.
                    origem="robo",
                )
                r["agendadas"] += 1
                agendadas_aqui += 1
            except RoboError as e:
                # As guardas do humano valendo pro robô: teto, intervalo, vídeo
                # em voo. Não é erro — é o sistema funcionando.
                logger.info(
                    "autopostagem_recusada",
                    conta=rede.conta,
                    motivo=str(e),
                    file=str(arquivo_id),
                )
                r["recusadas"] += 1
                # Recusa que é DO VÍDEO (sumiu do disco, sem legenda…) pula só
                # ele nesta conta e tenta o seguinte. Com `break` aqui, o vídeo
                # que alguém passou na frente e sumiu do disco travava a conta
                # pra sempre: `fila_posicao` o devolve primeiro toda rodada, ao
                # contrário do "mais recente", que um vídeo novo desbanca.
                # Recusa da CONTA (teto, intervalo, token…) para a conta.
                if e.code in MOTIVOS_DO_VIDEO and len(pulados) < MAX_PULOS_POR_CONTA:
                    pulados.add(arquivo_id)
                    continue
                break
    if any(r.values()):
        logger.info("autopostagem_rodada", **r)
    return r
