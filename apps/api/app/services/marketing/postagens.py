"""Robô de postagem dos criativos — REGRAS (Eduardo, 15/09/2026).

Este módulo é o miolo de decisão do robô: quem pode postar, quando entra na
fila, quem sai pro publicador e o que fazer com o resultado. Ele NÃO fala com
a Meta — nenhum import de httpx aqui. A conversa com a Graph API mora em
`services/marketing/meta_client.py` e o ritmo (crons) no `worker.py`; deste
jeito toda a regra fica testável sem rede, como `services/logistica_robo.py`
(a fila do Melhor Envio) já faz.

Ciclo da linha (`marketing_postagens` é agenda E outbox, igual ao
LogisticaRoboComando):

    agendar()               → `agendado` (com hora) | `pendente` (publicar já)
    promover_agendadas()    → `agendado` vencido vira `pendente`
                              (ou `revisar`, quando venceu demais)
    proximas_para_publicar()→ entrega os `pendente` ao publicador
    registrar_resultado()   → `publicado` | `falhou` | `revisar`
    marcar_falha()          → volta pra fila até esgotar `tentativas_max`

O cuidado que atravessa o arquivo inteiro: **publicar não tem desfazer**. Por
isso a trava de duplicidade é um índice único PARCIAL no banco (a checagem em
Python é só pra mensagem amigável — molde do `_has_open_flash_command`, em
flash.py), `proximas_para_publicar` NÃO faz commit do claim (quem chama muda
o status e comita na mesma transação, com a linha travada) e `retentar` se
recusa a rodar numa postagem que já tem id externo.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import func, select, true, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import (
    MarketingCreative,
    MarketingCreativeFile,
    MarketingPostagem,
    RedeSocial,
    RedeSocialToken,
)
from app.models.marketing_postagem import (
    STATUS_AGENDADO,
    STATUS_CANCELADO,
    STATUS_EM_VOO,
    STATUS_FALHOU,
    STATUS_PENDENTE,
    STATUS_PUBLICADO,
    STATUS_PUBLICANDO,
    STATUS_REVISAR,
)
from app.services.marketing import legenda as legenda_svc

logger = structlog.get_logger()

# A borda (modal, datetime-local) escreve em horário de Brasília; o banco só
# guarda UTC — mesma regra de services/marketing/scheduling.py.
BRT = ZoneInfo("America/Sao_Paulo")

# Hoje o robô só sabe publicar pela Graph API da Meta. TikTok exige
# consentimento humano por upload (Content Sharing Guidelines) e o YouTube
# publicaria privado enquanto o app não for verificado — as duas ficam
# documentadas como fora de escopo e a conta aparece na tela com o motivo.
# `youtube` entrou em 18/09/2026: o upload é resumável e sai PÚBLICO mesmo
# com projeto não auditado — provado por teste, contra o que a doc diz.
# Plataformas publicadas pelo EXECUTOR LOCAL (o Mac com AdsPower), e nao pelo
# servidor. O TikTok recusou a auditoria DUAS vezes (Content Posting API e
# Business API) e a rota de rascunho exige o mesmo formulario — conferido no
# portal em 23/09/2026. Sem API, a publicacao passa pelo navegador logado, que
# so existe na maquina do Eduardo. Duas consequencias carregadas daqui:
#   1. NAO exigem token: a credencial e a sessao do navegador;
#   2. NAO entram na fila do worker — se entrassem, o servidor tentaria
#      publicar pela Graph API da Meta e quebraria o que hoje funciona.
PLATAFORMAS_EXECUTOR_LOCAL = ("tiktok",)

PLATAFORMAS_SUPORTADAS = ("instagram", "facebook", "youtube", *PLATAFORMAS_EXECUTOR_LOCAL)

# Limite da legenda na Meta (mesmo número no schema, que barra antes de
# chegar aqui — a constante fica nos dois lados por clareza).
LEGENDA_MAX = 2200

# Postagem que OCUPA a conta pro cálculo de limite/intervalo: as em voo, as
# publicadas e as em `revisar` — esta última porque "revisar" quer dizer NÃO
# SABEMOS se saiu (o reconciliador põe ali o que a Meta não confirmou), e
# contar como vaga livre é o que produz o segundo Reel.
# `falhou`/`cancelado` não seguram vaga: ali está provado que não saiu.
STATUS_OCUPA_CONTA = (*STATUS_EM_VOO, STATUS_PUBLICADO, STATUS_REVISAR)

# Só destes estados dá pra cancelar — `containering`/`publicando` já estão com
# a Meta na linha, e cancelar ali seria mentira.
STATUS_CANCELAVEIS = (STATUS_AGENDADO, STATUS_PENDENTE, STATUS_REVISAR)

# "Quando esta postagem acontece (ou aconteceu)": publicado_em quando já saiu,
# senão a hora marcada, senão a criação ("publicar agora"). É o eixo do
# limite diário e do espaçamento.
_MOMENTO = func.coalesce(
    MarketingPostagem.publicado_em,
    MarketingPostagem.agendado_para,
    MarketingPostagem.created_at,
)


class RoboError(Exception):
    """Motivo de recusa com código estável pro router (detail={"code": ...}).

    Mesma forma do RoboError de `logistica_robo`, em classe própria pra não
    acoplar Marketing à Logística — as duas filas só se parecem.
    """

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def para_utc(valor: datetime | None) -> datetime | None:
    """Datetime da borda → UTC. Naive = Brasília.

    O `<input type="datetime-local">` manda "2026-09-16T19:30" SEM offset: o
    usuário digitou a hora de Brasília e é isso que ele quer. Um `.replace(
    tzinfo=UTC)` aqui adiantaria o post em 3 horas. Quando o front manda ISO
    com offset ("…-03:00"), o offset manda — os dois caminhos têm que cair no
    MESMO instante.
    """
    if valor is None:
        return None
    if valor.tzinfo is None:
        valor = valor.replace(tzinfo=BRT)
    return valor.astimezone(UTC)


def _agora(agora: datetime | None) -> datetime:
    return agora if agora is not None else datetime.now(UTC)


# Quanto a guarda de espaçamento tolera, nas postagens automáticas, pra
# absorver a demora de publicar. Ver o comentário no uso, em `pode_publicar`.
_TOLERANCIA_GRADE = timedelta(minutes=10)


# ────────────────────────────────────────────────────────────── guardas


def _teto_dia(rede: RedeSocial, settings) -> int:
    """Teto de posts por dia DESTA conta; NULL = o padrão do servidor.
    (Eduardo, 15/09/2026: "tem que ser configurável para ser automático".)"""
    if rede.postagem_max_dia is not None:
        return max(0, rede.postagem_max_dia)
    return settings.marketing_postagem_max_dia


def _espacamento_min(rede: RedeSocial, settings) -> int:
    """Minutos entre dois posts DESTA conta; NULL = o padrão do servidor."""
    if rede.postagem_intervalo_min is not None:
        return max(0, rede.postagem_intervalo_min)
    return settings.marketing_postagem_intervalo_min


def motivo_do_arquivo(file: MarketingCreativeFile | None) -> str | None:
    """O que impede ESTE ARQUIVO de sair em qualquer conta — só olha o disco.

    Separada do resto das guardas porque a tela "Fila do robô" precisa avisar,
    por vídeo, o que o robô vai pular (o vídeo antigo que alguém passou na
    frente é justamente o que mais tende a ter sumido do disco).
    """
    if file is None or not (file.file_rel or "").strip():
        return "sem_arquivo"
    # O vídeo é lido do disco do servidor na hora de publicar; se sumiu (disco
    # trocado, limpeza), é melhor descobrir agora do que no meio do upload.
    if not (Path(get_settings().uploads_dir) / file.file_rel).exists():
        return "arquivo_sumiu"
    return None


def pode_publicar_local(
    creative: MarketingCreative | None,
    file: MarketingCreativeFile | None,
    rede: RedeSocial | None,
    token: RedeSocialToken | None,
    *,
    automatico: bool = False,
) -> str | None:
    """Parte das guardas que NÃO toca no banco (criativo, arquivo, conta).

    Separada porque a listagem de contas do modal (`GET /contas`) precisa do
    motivo por conta sem gastar uma query por linha. Quem vai gravar usa
    `pode_publicar`, que roda esta e depois as do banco.
    """
    # A trava real da aprovação: tri-state (NULL = pendente, False = reprovado)
    # — só `True` libera. Postar criativo não aprovado é o erro caro aqui.
    if creative is None or creative.aprovado is not True:
        return "criativo_nao_aprovado"
    motivo = motivo_do_arquivo(file)
    if motivo:
        return motivo
    if rede is None or not rede.ativo:
        return "conta_inativa"
    return motivo_da_conta(rede, token, automatico=automatico)


def motivo_da_conta(
    rede: RedeSocial, token: RedeSocialToken | None, *, automatico: bool = False
) -> str | None:
    """A cauda da regra: o que impede ESTA CONTA de publicar, sem olhar vídeo.

    Vive aqui, e não no router, porque o modal e o publicador precisam dizer a
    MESMA coisa. Quando eram duas cópias, acrescentar o TikTok consertou o
    publicador e deixou o modal mostrando "plataforma não suportada" numa conta
    que já publica.

    `automatico` só o PUBLICADOR passa: o modal não sabe se o clique vai ser
    manual ou da agenda, e o interruptor `postagem_auto` vale só pro robô.

    Autossuficiente de propósito: repete a checagem de `ativo` que o chamador
    de cima também faz. Sem ela, quem chama só esta função (o modal) deixava
    de ver "conta inativa".
    """
    if not rede.ativo:
        return "conta_inativa"
    plataforma = (rede.plataforma or "").strip().lower()
    if plataforma not in PLATAFORMAS_SUPORTADAS:
        return "plataforma_nao_suportada"
    # A checagem de token vem DEPOIS da de plataforma, e pula as do executor
    # local: ali nao existe token nenhum — quem autentica e o navegador ja
    # logado no perfil do AdsPower. Exigir token aqui deixaria a conta do
    # TikTok eternamente com "conta_sem_token" na tela, que e o que acontece
    # hoje.
    # `revogado` é definitivo (alguém tirou o app da conta) — sem token novo
    # não há o que tentar. `expirado` NÃO entra: o cron de refresh renova
    # sozinho antes do tick.
    if plataforma not in PLATAFORMAS_EXECUTOR_LOCAL and (
        token is None or not token.token_enc or token.status == "revogado"
    ):
        return "conta_sem_token"
    # Sem perfil do AdsPower o executor nao sabe QUAL navegador abrir — e
    # abrir o errado publica na conta de outra marca.
    if plataforma in PLATAFORMAS_EXECUTOR_LOCAL and not (rede.adspower_user_id or "").strip():
        return "conta_sem_perfil_adspower"
    # Interruptor por conta (`redes_sociais.postagem_auto`): vale pro que o
    # ROBÔ faz sozinho (agendamento). O clique manual do operador passa —
    # quem decidiu foi uma pessoa olhando o vídeo.
    if automatico and not rede.postagem_auto:
        return "conta_sem_postagem_auto"
    return None


async def pode_publicar(
    creative: MarketingCreative | None,
    file: MarketingCreativeFile | None,
    rede: RedeSocial | None,
    token: RedeSocialToken | None,
    *,
    session: AsyncSession | None = None,
    quando: datetime | None = None,
    automatico: bool = False,
    excluir_id: UUID | None = None,
) -> str | None:
    """None quando dá pra postar; senão o CÓDIGO do motivo.

    Sem `session` roda só as guardas locais (útil pra pintar a tela). Com
    `session` acrescenta as que dependem do banco — reuso do vídeo em outra
    marca, postagem em voo, teto diário e espaçamento.

    `quando` é o instante pretendido (a hora agendada, ou agora): teto e
    espaçamento são medidos ali, senão agendar pra semana que vem esbarraria
    no post de ontem.

    `excluir_id` tira UMA linha da conta (ela mesma). É o que permite
    RE-CHECAR as guardas na hora de publicar: sem isso a postagem esbarraria
    em si própria ("postagem_em_voo") e nada sairia.
    """
    motivo = pode_publicar_local(creative, file, rede, token, automatico=automatico)
    if motivo or session is None:
        return motivo
    # Daqui pra baixo creative/file/rede existem — a guarda local já barraria.
    settings = get_settings()
    quando = _agora(quando)

    # Mesmo vídeo em duas marcas: Instagram e TikTok punem conteúdo repetido
    # entre contas em SILÊNCIO (a conta perde recomendação, nenhum erro de
    # API). Só trava quando os DOIS lados têm sha256 e marca — sem hash (ou
    # sem marca) não dá pra afirmar que é o mesmo vídeo de outra gente.
    fora = (
        (MarketingPostagem.id != excluir_id) if excluir_id is not None else true()
    )

    if file.sha256 and creative.marca_id is not None:
        usado = (
            await session.execute(
                select(func.count())
                .select_from(MarketingPostagem)
                .join(
                    MarketingCreativeFile,
                    MarketingCreativeFile.id == MarketingPostagem.file_id,
                )
                .join(
                    MarketingCreative,
                    MarketingCreative.id == MarketingPostagem.creative_id,
                )
                .where(
                    MarketingCreativeFile.sha256 == file.sha256,
                    MarketingPostagem.status.in_(STATUS_OCUPA_CONTA),
                    MarketingCreative.marca_id.isnot(None),
                    MarketingCreative.marca_id != creative.marca_id,
                    fora,
                )
            )
        ).scalar_one()
        if usado:
            return "video_ja_usado_em_outra_marca"

    # Pré-checagem do índice único parcial `uq_marketing_postagem_em_voo`:
    # existe pra dar mensagem boa; quem garante de verdade é o banco (duas
    # abas clicando junto passam por aqui e param lá).
    em_voo = (
        await session.execute(
            select(func.count())
            .select_from(MarketingPostagem)
            .where(
                MarketingPostagem.file_id == file.id,
                MarketingPostagem.rede_social_id == rede.id,
                MarketingPostagem.status.in_(STATUS_EM_VOO),
                fora,
            )
        )
    ).scalar_one()
    if em_voo:
        return "postagem_em_voo"

    # `revisar` neste par vídeo+conta quer dizer que a tentativa anterior ficou
    # SEM RESPOSTA da Meta. Criar outra por cima é a receita do Reel repetido:
    # primeiro alguém confere a conta e cancela (ou o reconciliador fecha).
    em_duvida = (
        await session.execute(
            select(func.count())
            .select_from(MarketingPostagem)
            .where(
                MarketingPostagem.file_id == file.id,
                MarketingPostagem.rede_social_id == rede.id,
                MarketingPostagem.status == STATUS_REVISAR,
                fora,
            )
        )
    ).scalar_one()
    if em_duvida:
        return "postagem_em_revisao"

    # Teto por conta nas 24h ANTERIORES ao instante pretendido (o técnico da
    # Meta é bem mais alto — 100/24h no IG; o baixo aqui é editorial, ninguém
    # quer a conta da marca despejando vídeo).
    # Conta as duas janelas de 24h que ENCOSTAM no instante pretendido — a que
    # termina nele e a que começa nele. Só a de trás deixaria o operador furar
    # o teto agendando PRA TRÁS (marcar 6 posts às 8h da manhã de um dia cheio
    # de posts da tarde: cada um só enxergaria os anteriores).
    momentos = (
        await session.execute(
            select(_MOMENTO)
            .select_from(MarketingPostagem)
            .where(
                MarketingPostagem.rede_social_id == rede.id,
                MarketingPostagem.status.in_(STATUS_OCUPA_CONTA),
                _MOMENTO > quando - timedelta(hours=24),
                _MOMENTO < quando + timedelta(hours=24),
                fora,
            )
        )
    ).scalars().all()
    teto = _teto_dia(rede, settings)
    antes = sum(1 for m in momentos if m is not None and m <= quando)
    depois = sum(1 for m in momentos if m is not None and m > quando)
    if antes >= teto or depois >= teto:
        return "limite_diario"

    # Espaçamento: janela nos DOIS sentidos. Agendar 10 min ANTES de um post
    # já marcado colaria os dois do mesmo jeito que 10 min depois.
    folga = timedelta(minutes=_espacamento_min(rede, settings))
    # Folga pro que é AUTOMÁTICO. O robô de autopostagem usa o intervalo pra
    # montar a grade (12h + 420 min = 19h) e a guarda usa o MESMO número como
    # mínimo. Quando os dois coincidem, a demora natural de publicar empurra o
    # primeiro post pra depois da vaga dele (saiu às 12:03, não às 12:02), e às
    # 19:02 a guarda mede 6h59m — um minuto "cedo demais". O segundo post do
    # dia caía pra 20h, uma hora atrasado, todo dia.
    #
    # Pego simulando o dia inteiro com a configuração real do Eduardo antes de
    # ligar. Dez minutos absorvem a demora de publicar sem tirar o sentido da
    # guarda: com 90 de intervalo, dois posts automáticos ainda ficam a pelo
    # menos 80 minutos um do outro. O clique humano "publicar agora" continua
    # com o mínimo exato — ali não existe grade pra desalinhar.
    if automatico:
        folga = max(timedelta(0), folga - _TOLERANCIA_GRADE)
    perto = (
        await session.execute(
            select(func.count())
            .select_from(MarketingPostagem)
            .where(
                MarketingPostagem.rede_social_id == rede.id,
                MarketingPostagem.status.in_(STATUS_OCUPA_CONTA),
                _MOMENTO > quando - folga,
                _MOMENTO < quando + folga,
                fora,
            )
        )
    ).scalar_one()
    if perto:
        return "intervalo_curto"
    return None


async def tokens_por_rede(
    session: AsyncSession, rede_ids: list[UUID]
) -> dict[UUID, RedeSocialToken]:
    """Token de cada conta, em UMA query (o modal lista N contas)."""
    if not rede_ids:
        return {}
    rows = (
        await session.execute(
            select(RedeSocialToken).where(RedeSocialToken.rede_social_id.in_(rede_ids))
        )
    ).scalars().all()
    return {t.rede_social_id: t for t in rows}


# ───────────────────────────────────────────────────────────── agendar


async def agendar(
    session: AsyncSession,
    *,
    creative: MarketingCreative,
    file: MarketingCreativeFile,
    redes: list[RedeSocial],
    legenda: str | None = None,
    agendado_para: datetime | None = None,
    opcoes: dict[str, Any] | None = None,
    user_id: UUID | None = None,
    # Quem está apertando o botão. Inferir isso da presença de hora marcada
    # funcionou enquanto só existiam duas situações (clique agora / agendar
    # pra depois). Com o robô de autopostagem (24/09/2026) passou a existir
    # uma terceira — robô publicando AGORA —, e a dedução a classificava como
    # clique humano: as guardas que existem SÓ pro robô (`sem_legenda`,
    # `conta_sem_postagem_auto`) deixavam de disparar, e ele publicava Reel
    # sem legenda em marca sem biblioteca cadastrada.
    automatico: bool = False,
    # `manual` = pessoa; `agenda` = o promotor tirou da agenda; `robo` = a
    # autopostagem escolheu sozinha. Não é só rastreabilidade: `revalidar`
    # decide por AQUI se reexamina as guardas do robô na hora de publicar, e
    # com tudo gravado como `manual` desligar o interruptor não segurava o que
    # já estava na fila.
    origem: str = "manual",
) -> list[MarketingPostagem]:
    """Cria UMA postagem por conta. Sem hora = `pendente` (sai no próximo tick).

    Recusa a operação INTEIRA no primeiro motivo (RoboError com o código): o
    operador marcou 3 contas esperando as 3; publicar 2 e engolir a terceira
    num toast seria pior que voltar o erro.

    A LEGENDA É RESOLVIDA POR CONTA, dentro do laço — nunca uma vez pro lote.
    Parece detalhe e não é: `{{ instagram }}` é o @ de CADA conta, e o rodízio
    de variações é por conta. Resolver fora do laço mandaria o texto da
    primeira (com o @ dela) pra todas as outras.

    `legenda` só chega preenchida quando o operador REESCREVEU o texto na tela;
    aí ela vale igual pra todas as contas, porque foi uma pessoa que escolheu.
    Vindo vazia, cada conta recebe o que a cascata decidir pra ela.
    """
    if not redes:
        raise RoboError("sem_conta")
    if file.creative_id != creative.id:
        raise RoboError("sem_arquivo")
    # O criativo é DE UMA MARCA e só sai nas contas DELA. Sem isto, um id de
    # conta trocado no payload publicaria o vídeo da Uranyx no perfil da
    # Charlots — e no ar não tem desfazer.
    if creative.marca_id is None:
        raise RoboError("criativo_sem_marca")
    if any(r.marca_id != creative.marca_id for r in redes):
        raise RoboError("conta_de_outra_marca")

    quando = para_utc(agendado_para)
    if quando is not None:
        horizonte = timedelta(days=get_settings().marketing_postagem_horizonte_dias)
        agora = datetime.now(UTC)
        if quando > agora + horizonte:
            # Agenda de meses à frente é quase sempre ano/mês digitado errado
            # ("2027" no lugar de "2026"), e o post ficaria pendurado sem
            # ninguém lembrar. O Facebook, no agendamento nativo, para em 29
            # dias pelo mesmo motivo.
            raise RoboError("agendamento_longe_demais")
        if quando < agora - timedelta(minutes=5):
            # Hora no passado vira post imediato sem o operador perceber — e
            # ainda desloca o teto diário pra trás. Folga de 5 min pro
            # relógio do navegador.
            raise RoboError("agendamento_no_passado")
    # Hora marcada implica robô (quem executa depois é ele), mas o contrário
    # não vale: o robô de autopostagem publica AGORA e mesmo assim é robô.
    # Por isso o parâmetro manda, e a hora só soma.
    automatico = automatico or quando is not None
    tokens = await tokens_por_rede(session, [r.id for r in redes])
    criadas: list[MarketingPostagem] = []
    for rede in redes:
        motivo = await pode_publicar(
            creative,
            file,
            rede,
            tokens.get(rede.id),
            session=session,
            quando=quando,
            automatico=automatico,
        )
        if motivo:
            raise RoboError(motivo)
        try:
            resolvida = await legenda_svc.resolver(
                session,
                creative=creative,
                file=file,
                rede=rede,
                legenda_manual=legenda,
            )
        except legenda_svc.TemplateInvalidoError as e:
            # Variação quebrada na biblioteca. Recusar é o certo: legenda meio
            # renderizada no Instagram não se conserta editando o post.
            raise RoboError("legenda_template_invalido") from e
        if resolvida.texto is None:
            # Reel sem legenda é criativo queimado — a legenda é o único texto
            # que a busca do Instagram e o Google leem daquele vídeo. Pro robô
            # (hora marcada) isso é recusa seca; o clique manual passa, porque
            # ali tem gente olhando e a tela já avisou.
            if automatico:
                raise RoboError("sem_legenda")
        criadas.append(
            MarketingPostagem(
                creative_id=creative.id,
                file_id=file.id,
                rede_social_id=rede.id,
                # SNAPSHOT: apagar a conta (FK SET NULL) não pode apagar o
                # "onde isso foi publicado" — mesma ideia de
                # MarketingCommand.platform.
                plataforma=rede.plataforma,
                conta=rede.conta,
                legenda=resolvida.texto,
                # Qual variação saiu — é daqui que o rodízio da PRÓXIMA
                # postagem descobre o que já foi usado nesta conta.
                legenda_modelo_id=resolvida.modelo_id,
                opcoes=opcoes or {},
                agendado_para=quando,
                status=STATUS_AGENDADO if quando else STATUS_PENDENTE,
                origem=origem,
                created_by=user_id,
            )
        )
    session.add_all(criadas)
    try:
        await session.commit()
    except IntegrityError as e:
        # O índice parcial é a trava real contra post duplicado: quem chegou
        # em segundo vê o mesmo código da pré-checagem.
        await session.rollback()
        raise RoboError("postagem_em_voo") from e
    for p in criadas:
        await session.refresh(p)
    logger.info(
        "marketing_postagem_agendada",
        creative=str(creative.id),
        file=str(file.id),
        contas=len(criadas),
        agendado_para=quando.isoformat() if quando else None,
    )
    return criadas


# ──────────────────────────────────────────────────────── fila / worker


async def promover_agendadas(
    session: AsyncSession, *, agora: datetime | None = None
) -> dict[str, int]:
    """`agendado` que venceu vira `pendente` (origem `agenda`).

    Atraso grande demais (worker parado a noite inteira) NÃO sai sozinho:
    vira `revisar` e alguém decide — despejar de madrugada os posts das 19h é
    justamente o que a agenda existe pra evitar. Mesmo cuidado de catch-up do
    valuation_estoque_catchup.
    """
    agora = _agora(agora)
    limite = timedelta(minutes=get_settings().marketing_postagem_atraso_max_min)
    rows = (
        await session.execute(
            select(MarketingPostagem)
            .where(
                MarketingPostagem.status == STATUS_AGENDADO,
                MarketingPostagem.agendado_para.isnot(None),
                MarketingPostagem.agendado_para <= agora,
            )
            .order_by(MarketingPostagem.agendado_para.asc())
            .with_for_update(skip_locked=True)
        )
    ).scalars().all()

    promovidas = revisar = 0
    for p in rows:
        atraso = agora - p.agendado_para
        if atraso > limite:
            p.status = STATUS_REVISAR
            p.result = (
                f"agendado para {p.agendado_para.astimezone(BRT):%d/%m/%Y %H:%M} (BRT) e "
                f"não saiu: atraso de {int(atraso.total_seconds() // 60)} min, acima do "
                f"limite de {int(limite.total_seconds() // 60)} min. Reagende se ainda faz sentido."
            )[:2000]
            p.completed_at = agora
            revisar += 1
        else:
            p.status = STATUS_PENDENTE
            p.origem = "agenda"
            promovidas += 1
    if rows:
        await session.commit()
    logger.info(
        "marketing_postagens_promover",
        checados=len(rows), promovidas=promovidas, revisar=revisar,
    )
    return {"checados": len(rows), "promovidas": promovidas, "revisar": revisar}


def _query_do_lease(limit: int):
    """A query do lease, separada pra poder ser LIDA (e testada) inteira.

    DISTINCT ON (a conta) pega a mais antiga de cada; vai numa subquery porque
    o Postgres não aceita DISTINCT junto de FOR UPDATE.
    """
    candidatas = (
        select(MarketingPostagem.id)
        .where(
            MarketingPostagem.status == STATUS_PENDENTE,
            # O servidor NUNCA publica as do executor local. Sem esta linha o
            # worker levaria a postagem de TikTok pro branch da Meta e
            # quebraria — e pior, quebraria depois de marcar `publicando`.
            MarketingPostagem.plataforma.notin_(PLATAFORMAS_EXECUTOR_LOCAL),
        )
        .order_by(MarketingPostagem.rede_social_id, _MOMENTO.asc())
        .distinct(MarketingPostagem.rede_social_id)
    )
    return (
        select(MarketingPostagem)
        .where(
            MarketingPostagem.id.in_(candidatas),
            # O status PRECISA ser repetido AQUI FORA — não é redundância.
            # Na subquery ele é avaliado no snapshot do início do comando; sob
            # READ COMMITTED, quando uma linha que o outro worker acabou de
            # virar `publicando` é destravada no meio deste SELECT, o Postgres
            # re-avalia (EvalPlanQual) apenas este WHERE externo. Sem a
            # repetição os dois workers levam a MESMA linha — e o Reel sai
            # duas vezes no perfil da marca.
            MarketingPostagem.status == STATUS_PENDENTE,
            MarketingPostagem.plataforma.notin_(PLATAFORMAS_EXECUTOR_LOCAL),
        )
        .order_by(_MOMENTO.asc())
        .limit(limit)
        .with_for_update(skip_locked=True)
    )


async def proximas_para_publicar(
    session: AsyncSession, *, limit: int = 5
) -> list[MarketingPostagem]:
    """Pendentes prontas pro publicador: as mais antigas, UMA POR CONTA.

    Uma por conta por tick porque o tick é de 1 minuto e duas postagens
    seguidas na mesma conta em segundos é exatamente o padrão que a Meta lê
    como spam (e o espaçamento de `pode_publicar` já cuidaria do resto).

    É um LEASE de verdade (molde do `lease` da logística): pega com FOR UPDATE
    SKIP LOCKED, carimba `claimed_at`, vira `publicando` e COMITA. O claim
    tinha que ser durável porque publicar não tem desfazer: se a linha
    continuasse `pendente` durante o upload (que dura mais que o tick de 1
    min), o tick seguinte a pegaria de novo e o vídeo sairia duas vezes. O
    preço é que um worker morto no meio deixa a linha `publicando` — e esse é
    exatamente o caso que a reconciliação recolhe, perguntando à Meta pelo
    `container_id` antes de qualquer retry.
    """
    rows = (await session.execute(_query_do_lease(limit))).scalars().all()
    agora = datetime.now(UTC)
    for p in rows:
        p.claimed_at = agora
        p.status = STATUS_PUBLICANDO
    if rows:
        await session.commit()
    logger.info("marketing_postagens_lease", leased=len(rows))
    return list(rows)


async def revalidar(
    session: AsyncSession, postagem: MarketingPostagem
) -> str | None:
    """Roda as guardas DE NOVO, no instante em que o vídeo vai subir.

    Entre agendar e publicar passam horas: o criativo pode ter sido reprovado,
    a conta desativada, o `postagem_auto` desligado, o arquivo sumido do
    disco, e a conta pode ter recebido outros posts nesse meio-tempo. Agendar
    não é autorizar pra sempre — o que vale é a situação AGORA.

    `excluir_id` tira a própria linha da conta (ela já está `publicando`,
    senão bateria em si mesma). Devolve o código do motivo, ou None pra seguir.
    """
    creative = await session.get(MarketingCreative, postagem.creative_id)
    file = await session.get(MarketingCreativeFile, postagem.file_id)
    rede = (
        await session.get(RedeSocial, postagem.rede_social_id)
        if postagem.rede_social_id
        else None
    )
    token = (
        (
            await session.execute(
                select(RedeSocialToken).where(
                    RedeSocialToken.rede_social_id == postagem.rede_social_id
                )
            )
        ).scalars().first()
        if postagem.rede_social_id
        else None
    )
    return await pode_publicar(
        creative,
        file,
        rede,
        token,
        session=session,
        # Agora é agora: o teto e o espaçamento valem pelo relógio real, não
        # pela hora que estava marcada (é isso que evita a rajada de catch-up
        # quando o worker volta depois de uma parada).
        quando=None,
        # Tudo que não foi uma pessoa: `agenda` (saiu da agenda) e `robo` (a
        # autopostagem escolheu). Faltar `robo` aqui fazia o interruptor
        # deixar de ser botão de pânico — desligar não segurava o que o robô
        # já tinha posto na fila.
        automatico=postagem.origem in ("agenda", "robo"),
        excluir_id=postagem.id,
    )


# Motivos que o TEMPO resolve sozinho: a postagem volta pra fila e tenta de
# novo no próximo tick, sem gastar tentativa. Qualquer outro motivo é decisão
# (criativo reprovado, conta desligada) e vira falha.
MOTIVOS_ADIAVEIS = ("intervalo_curto", "limite_diario")

# Teto de quanto uma postagem pode ficar rodando na fila sendo adiada antes de
# alguém olhar. 48h cobre um fim de semana inteiro de conta cheia; passou
# disso, publicar fora de hora não é mais o que o operador pediu.
_ADIAMENTO_MAX = timedelta(hours=48)


async def adiar(
    session: AsyncSession, postagem: MarketingPostagem, *, motivo: str
) -> MarketingPostagem:
    """Devolve pra fila (`pendente`) sem gastar tentativa.

    É o desfecho de quem esbarrou no espaçamento ou no teto diário da conta na
    hora de publicar: nada deu errado, só ainda não é hora. Depois de
    `_ADIAMENTO_MAX` insistindo, vira `revisar` — fila que anda pra sempre
    sozinha é fila que ninguém percebe que travou.
    """
    agora = datetime.now(UTC)
    nascida = postagem.agendado_para or postagem.created_at or agora
    if nascida.tzinfo is None:
        nascida = nascida.replace(tzinfo=UTC)
    texto = {
        "intervalo_curto": "aguardando o intervalo mínimo entre posts da conta",
        "limite_diario": "aguardando: a conta já bateu o limite de posts do dia",
    }.get(motivo, motivo)
    if agora - nascida > _ADIAMENTO_MAX:
        return await registrar_resultado(
            session,
            postagem,
            status=STATUS_REVISAR,
            result=(
                f"{texto} há mais de {int(_ADIAMENTO_MAX.total_seconds() // 3600)}h — "
                "reagende para uma janela livre ou cancele."
            ),
        )
    postagem.status = STATUS_PENDENTE
    postagem.claimed_at = None
    postagem.result = texto[:2000]
    await session.commit()
    logger.info("marketing_postagem_adiada", postagem=str(postagem.id), motivo=motivo)
    return postagem


async def devolver_para_fila(
    session: AsyncSession, ids: list[UUID], *, motivo: str
) -> int:
    """Solta a reserva de postagens que o tick NÃO chegou a processar.

    Só pode ser chamado pra linha em que NENHUMA chamada à Meta aconteceu —
    aí voltar pra `pendente` é seguro e o próximo tick pega. É o par do lease:
    sem isto, um tick que estoura o tempo deixaria as reservadas paradas em
    `publicando` até o reconciliador mandá-las pra `revisar` sem motivo.
    """
    if not ids:
        return 0
    await session.execute(
        update(MarketingPostagem)
        .where(
            MarketingPostagem.id.in_(ids),
            # Trava de segurança: se alguma já andou (container criado,
            # publicada), NÃO se mexe.
            MarketingPostagem.status == STATUS_PUBLICANDO,
            MarketingPostagem.container_id.is_(None),
        )
        .values(status=STATUS_PENDENTE, claimed_at=None, result=motivo[:2000])
    )
    await session.commit()
    logger.info("marketing_postagens_devolvidas", quantas=len(ids), motivo=motivo)
    return len(ids)


# ──────────────────────────────────────────────────────────── resultado


async def _linha(
    session: AsyncSession, postagem: MarketingPostagem | UUID, *, travar: bool = False
) -> MarketingPostagem:
    """Aceita a linha OU o id dela.

    O router já tem o objeto na mão (acabou de carregar pra checar permissão);
    o worker carrega a fila e depois só carrega o id pra frente. Resolver aqui
    evita que cada chamador tenha que lembrar qual dos dois é — e, com a linha
    já na sessão, `session.get` devolve o mesmo objeto sem ir ao banco.
    """
    linha_id = postagem.id if isinstance(postagem, MarketingPostagem) else postagem
    if travar:
        # Cancelar e retentar leem o status, decidem e escrevem. Sem o FOR
        # UPDATE, dois cliques (ou um clique junto com o tick do worker) leem
        # o MESMO status antigo e os dois passam pela checagem — é assim que
        # se cancela uma postagem que já foi pro ar.
        linha = (
            await session.execute(
                select(MarketingPostagem)
                .where(MarketingPostagem.id == linha_id)
                .with_for_update()
                # Sem isto o SQLAlchemy devolve o objeto que o router já tinha
                # na sessão, com os atributos ANTIGOS — a trava pegaria a
                # linha certa e a decisão sairia do retrato velho.
                .execution_options(populate_existing=True)
            )
        ).scalars().first()
        if linha is None:
            raise RoboError("postagem_nao_encontrada")
        return linha
    if isinstance(postagem, MarketingPostagem):
        return postagem
    linha = await session.get(MarketingPostagem, linha_id)
    if linha is None:
        raise RoboError("postagem_nao_encontrada")
    return linha


async def registrar_resultado(
    session: AsyncSession,
    postagem: MarketingPostagem | UUID,
    *,
    status: str,
    result: str | None = None,
    post_url: str | None = None,
    post_external_id: str | None = None,
    container_id: str | None = None,
) -> MarketingPostagem:
    """Carimba o desfecho (ou um passo do meio) da postagem.

    `container_id` é gravado ANTES de publicar (status `containering`): é ele
    que permite a reconciliação perguntar "saiu ou não?" em vez de retentar
    cego. Campos vindos None não apagam o que já estava — um passo do meio
    não pode zerar o id do container do passo anterior.
    """
    postagem = await _linha(session, postagem)
    postagem.status = status
    if result is not None:
        postagem.result = result[:2000] or None
    if post_url is not None:
        postagem.post_url = post_url
    if post_external_id is not None:
        postagem.post_external_id = post_external_id
    if container_id is not None:
        postagem.container_id = container_id
    agora = datetime.now(UTC)
    if status == STATUS_PUBLICADO and postagem.publicado_em is None:
        postagem.publicado_em = agora
    if status in (STATUS_PUBLICADO, STATUS_FALHOU, STATUS_CANCELADO, STATUS_REVISAR):
        postagem.completed_at = agora
    await session.commit()
    logger.info(
        "marketing_postagem_resultado",
        postagem=str(postagem.id),
        status=status,
        plataforma=postagem.plataforma,
        conta=postagem.conta,
    )
    return postagem


async def marcar_falha(
    session: AsyncSession,
    postagem: MarketingPostagem | UUID,
    *,
    result: str,
    permanente: bool = False,
) -> MarketingPostagem:
    """Erro em que o post NÃO saiu: volta pra fila até esgotar `tentativas_max`.

    Quem chama só pode usar isto quando tem certeza de que nada foi publicado
    (token recusado, arquivo ilegível, erro antes do passo final). Se houver
    dúvida, o certo é `registrar_resultado(..., status="revisar")` — retentar
    um post que talvez tenha saído publica duas vezes.

    `permanente=True` para o que não adianta repetir (container expirado,
    conteúdo recusado): queima as tentativas de uma vez.
    """
    postagem = await _linha(session, postagem)
    postagem.attempts = (postagem.attempts or 0) + 1
    esgotou = permanente or postagem.attempts >= (postagem.tentativas_max or 3)
    if esgotou:
        return await registrar_resultado(
            session, postagem, status=STATUS_FALHOU, result=result
        )
    postagem.status = STATUS_PENDENTE
    postagem.claimed_at = None
    # Container de uma tentativa que falhou não serve pra próxima (o do
    # Instagram expira em 24h e é de um upload que não foi adiante).
    postagem.container_id = None
    postagem.result = (result or "")[:2000] or None
    await session.commit()
    logger.info(
        "marketing_postagem_retry",
        postagem=str(postagem.id),
        attempts=postagem.attempts,
        tentativas_max=postagem.tentativas_max,
    )
    return postagem


# ───────────────────────────────────────────────────────── ações da tela


async def cancelar(
    session: AsyncSession, postagem: MarketingPostagem | UUID
) -> MarketingPostagem:
    """Cancela enquanto ainda não foi pra Meta.

    `containering`/`publicando` ficam de fora de propósito: ali o upload já
    começou e "cancelado" na tela seria mentira — a reconciliação é quem
    fecha esses.
    """
    postagem = await _linha(session, postagem, travar=True)
    if postagem.status not in STATUS_CANCELAVEIS:
        raise RoboError("postagem_nao_cancelavel")
    return await registrar_resultado(
        session, postagem, status=STATUS_CANCELADO, result="cancelado na tela"
    )


async def retentar(
    session: AsyncSession, postagem: MarketingPostagem | UUID
) -> MarketingPostagem:
    """Recoloca na fila uma postagem que falhou (só ela; nunca `publicando`)."""
    postagem = await _linha(session, postagem, travar=True)
    if postagem.status != STATUS_FALHOU:
        raise RoboError("postagem_nao_falhou")
    # Cinto de segurança: se ficou id/URL externo, o post SAIU (falhou depois,
    # no carimbo do resultado). Retentar aqui duplicaria na conta da marca.
    if postagem.post_external_id or postagem.post_url:
        raise RoboError("postagem_ja_publicada")
    # Container gravado = o upload chegou na Meta e a chamada que publica pode
    # ter saído. NÃO dá pra retentar às cegas: quem tira a dúvida é o
    # reconciliador (consulta o id na Meta) ou o operador, cancelando e
    # criando uma postagem nova depois de olhar a conta.
    if postagem.container_id:
        raise RoboError("postagem_precisa_conferir_na_meta")
    postagem.status = STATUS_PENDENTE
    # Zera as tentativas: a decisão é nova e humana, não o eco da anterior.
    postagem.attempts = 0
    postagem.origem = "manual"
    postagem.claimed_at = None
    postagem.completed_at = None
    postagem.container_id = None
    postagem.result = None
    await session.commit()
    logger.info("marketing_postagem_retentar", postagem=str(postagem.id))
    return postagem
