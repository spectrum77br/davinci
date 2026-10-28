"""Marcar a conversa como LIDA na plataforma quando uma PESSOA a abre (14/10/2026).

Decisão do dono (Eduardo, 14/10/2026): "ao passar também tem que marcar como
visto, igual lá no Duoke quando clicar na conversa". Até aqui o DaVinci NUNCA
marcava nada como lido (Shopee sem `read_conversation`, ML com
`mark_as_read=false`, TikTok sem o `Read Message`), porque o Duoke usa o
estado de lida DA PLATAFORMA e a equipe trabalha nele.

A regra nova, no escopo decidido para não esconder conversa de quem ainda
trabalha pelo Duoke:

  - SÓ PESSOA, e só quem MEXE na caixa (`acesso.pode_mexer`, o mesmo
    `atendimento_mexe` do /api/auth/me). Quem só lê nunca marca (a rota dá
    403 antes; aqui é o cinto).
  - SÓ AO ABRIR a conversa na tela: a tela chama POST
    /api/atendimento/conversas/{id}/lida depois de 1,5 s com a conversa
    aberta e a aba visível (`apps/web/lib/atendimentoLida.ts`) — quem só
    passou pela lista com as setas não marca nada —, uma vez por abertura.
  - SÓ NOS CANAIS EM QUE O DAVINCI RESPONDE: modo `auto`, `copiloto` ou
    `humano`, com o envio ligado (`atendimento_envio_ativo`). Em `observar`
    (ou com o envio desligado) quem responde é o Duoke: nada muda lá — o
    detalhe diz `marcar_lida = false`, a tela não chama o /lida (chama o
    /visto, que só grava o "visto no DaVinci", abaixo) e a rota recusa
    (`canal_em_observacao` / `envio_desligado`).
  - A IA, os crons (leitura, importação, reclamações, automações, Caixa
    Humano) e os robôs NUNCA marcam: nenhum deles chama este módulo, e os
    métodos dos clientes que marcam (`chat_read_conversation`,
    `cs_read_message`, `marcar_pack_como_lido`) só são chamados daqui
    (`tests/test_atendimento_lida.py` confere no código).
  - `nao_lidas` só zera DEPOIS que a plataforma aceitou. Não marcou lá (erro,
    sem permissão, timeout), não zera aqui: o Duoke e a plataforma continuam
    vendo não lida, e o DaVinci também. A falha não quebra a tela: a rota
    responde 200 com `marcada = false` e o aviso, e fica no log.
  - A chamada à plataforma nunca é cancelada no meio: passou do tempo, a rota
    responde e ela termina em segundo plano (o refresh token da Shopee e do
    ML é de uso único e pode estar sendo renovado nessa hora).
  - Idempotente: `nao_lidas = 0` responde "já lida" sem ir à plataforma, e
    duas abas abrindo juntas vão uma vez só (trava curta no Redis).

Por plataforma (`PLATAFORMAS_QUE_MARCAM`):

  shopee  chat       POST /api/v2/sellerchat/read_conversation
                     `{conversation_id (int64), last_read_message_id (texto)}`
                     com a mais recente que NÃO é resposta da loja (autor
                     `loja`) e tem o id numérico da Shopee: a do comprador ou
                     a de sistema/afiliado — inclusive a resposta AUTOMÁTICA
                     da própria loja, que a Shopee conta no não lido do
                     vendedor (medido em produção em 14/10/2026; ver
                     `_ultima_de_fora_shopee`).
  tiktok  chat       POST /customer_service/202309/conversations/{id}/messages/read
                     (`Read Message`, sem corpo: a conversa inteira). As lojas
                     ainda estão sem o escopo `seller.customer_service`: hoje
                     a TikTok recusa (105005) e a conversa continua não lida.
  ml      pos_venda  o ML só marca LENDO: GET do pack com `mark_as_read=true`
                     (a doc: só o GET do pack do vendedor muda o estado; os
                     outros recursos nunca), todas as páginas até o mesmo teto
                     da leitura (5 × 20). Leitura dedicada, só neste caso.
  ml      pergunta   não há "lida": a pergunta sai da fila respondida.

  zap     zap        (26/10/2026, Onda 2 de docs/zap/desenho.md §6.7 — o tique
                     azul, decisão do Eduardo: "o cliente PODE ver") POST
                     /{phone_number_id}/messages `{status: "read",
                     message_id: <último wamid do cliente>}` (`zap.marcar_lida`;
                     só até 30 dias depois de receber). Atrás da chave própria
                     `atendimento_zap_marcar_lida` (DESLIGADA) e das chaves do
                     envio (`atendimento_envio_ativo` e
                     `atendimento_zap_envio_ativo`), com o número `envio_ligado`
                     e o canal num modo que envia — e SÓ quando um
                     ADMINISTRADOR abre (só eles veem o Zap).

Não se aplica (a rota recusa com `nao_se_aplica` e a tela nem chama): Amazon
(e-mail; a caixa abre em EXAMINE), o e-mail da Central (Tuta), o Zap com a
chave do tique azul desligada, Temu e
AliExpress (o robô do Mac mini só lê: abrir a conversa no Seller Center é que
marcaria), Magalu (o cliente recusa o `read_by` de propósito — liberar é outra
decisão), o carrinho do site, os comentários das redes, o Direct do Instagram
e as conversas de avaliação e de reclamação (não são chat da plataforma).

VISTO NO DAVINCI (decisão do dono, 15/10/2026: os números da Caixa batem com
o Duoke, e no Duoke abrir a conversa zera o número dela). Separado do "lida
na plataforma" acima:

  - `gravar_visto` grava `conversa.visto_em` quando uma PESSOA que vê a
    caixa (`acesso.pode_ver`; desde 28/10/2026 — antes só quem mexe) abre a
    conversa na tela — o mesmo gatilho (1,5 s aberta com a aba visível, uma
    vez por abertura) — em QUALQUER modo de loja (inclusive `observar`), com
    o envio ligado ou não, em qualquer canal. NUNCA vai à plataforma: é a
    marca do DaVinci que o número da Caixa lê (`pendente.nao_lidas_duoke`: só
    a mensagem do comprador que CHEGOU depois dela conta). 28/10/2026, o
    Eduardo, ao trocar o Duoke pelo DaVinci: "as notificações devem sumir se
    clicado em algum atendimento" — como o Duoke "por loja", quem da equipe
    abrir apaga para todos (só a marca do DaVinci; o /lida na plataforma
    continua só de quem mexe). A IA, os crons e os robôs não chamam.
  - O QUE grava (15/10/2026, a revisão): ATÉ ONDE A PESSOA VIU, no relógio do
    DaVinci — a CHEGADA (`created_at`) da mensagem mais nova que a tela
    mostrava (`visto_ate`: o detalhe manda, a tela devolve no POST). O sync
    traz a mensagem ~1 min depois de o comprador mandar (mediana de 70 s na
    Shopee), então o horário da plataforma não serve; e o "agora" do pedido
    também não: a transação do sync que gravou a mensagem pode ter começado
    antes da abertura (o `created_at` é o `now()` dela) e terminado depois —
    a mensagem que a tela não mostrou ficaria para trás. Sem o `visto_ate`
    (a conversa sem mensagem, a tela antiga), o relógio do banco
    (`clock_timestamp()`); o `visto_ate` nunca passa dele.
  - COM A CONVERSA ABERTA (15/10/2026, a revisão: no Duoke, a conversa aberta
    fica lida): a releitura do detalhe (15 s) que traz mensagem nova — o
    `visto_ate` andou — com a aba visível faz a tela chamar o /visto de novo
    (só o /visto, barato; o /lida continua uma vez por abertura).
  - A tela chama UMA rota por abertura: o POST /conversas/{id}/lida quando o
    detalhe diz `marcar_lida` (ele grava o visto e marca na plataforma como
    sempre) e, senão, o POST /conversas/{id}/visto (rota leve: só o visto —
    a loja em `observar`, o envio desligado, a plataforma sem "lida", nada
    não lido). A decisão (15/10/2026): uma rota à parte em vez de um "modo"
    no /lida, para o /lida continuar dizendo 409 quando não marca na
    plataforma e nada mudar no Duoke por engano.
  - O /lida grava o visto ANTES de tentar a plataforma: a pessoa abriu, com
    ou sem a plataforma aceitar (falha, 409 de corrida com a troca de modo).
  - Idempotente: abrir de novo só anda o carimbo para a frente (nunca para
    trás, `GREATEST`). O `updated_at` da conversa não muda: é marca de
    leitura, não mudança da conversa (como a triagem da Caixa Humano).

Texto de comprador nunca vai para o log — só ids, plataforma e o código do erro.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import DateTime, case, func, literal, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import (
    AtendimentoCanal,
    AtendimentoConversa,
    AtendimentoMensagem,
    Integration,
    User,
    UserRole,
)
from app.redis_client import redis
from app.services.atendimento import acesso, canais_externos, clientes, gravar, zap, zap_erros
from app.services.atendimento import notificacoes as notificacoes_svc
from app.services.atendimento.constantes import (
    AUTOR_CLIENTE,
    AUTOR_SISTEMA,
    CANAL_CHAT,
    CANAL_POS_VENDA,
    CANAL_ZAP,
    MODOS_QUE_ENVIAM,
    PLATAFORMA_WHATSAPP,
    e_chat_de_afiliado,
)

logger = structlog.get_logger()

# As (plataforma, canal) em que a plataforma tem "marcar como lida" pela API.
PLATAFORMAS_QUE_MARCAM: dict[str, frozenset[str]] = {
    "shopee": frozenset({CANAL_CHAT}),
    "tiktok": frozenset({CANAL_CHAT}),
    "ml": frozenset({CANAL_POS_VENDA}),
}

# ── Recusas (a rota devolve 403/409 com o `code`; nada vai à plataforma) ──
RECUSA_SO_LEITURA = "atendimento_so_leitura"
RECUSA_NAO_SE_APLICA = "nao_se_aplica"
RECUSA_SEM_INTEGRACAO = "sem_integracao"
RECUSA_CANAL_EM_OBSERVACAO = "canal_em_observacao"
RECUSA_ENVIO_DESLIGADO = "envio_desligado"

MOTIVO_NAO_SE_APLICA = (
    'Esta conversa não tem "marcar como lida" pelo DaVinci (só o chat da Shopee e '
    "da TikTok e o pós-venda do ML)."
)
MOTIVO_SEM_INTEGRACAO = "A loja desta conversa não está mais conectada ao DaVinci."
MOTIVO_EM_OBSERVACAO = (
    "Loja em modo observar: quem responde é o Duoke, e a conversa continua não lida lá."
)
MOTIVO_ENVIO_DESLIGADO = (
    "O envio pelo DaVinci está desligado: quem responde é o Duoke, e a conversa "
    "continua não lida lá."
)

# ── Resultado (200): `motivo` quando não marcou (ou marcou e não zerou) ──
MOTIVO_JA_LIDA = "ja_lida"
MOTIVO_EM_ANDAMENTO = "em_andamento"
MOTIVO_PLATAFORMA_FALHOU = "plataforma_falhou"
MOTIVO_SEM_MENSAGEM = "sem_mensagem"
# A plataforma marcou, mas chegou mensagem no meio: o número fica com o sync.
MOTIVO_CHEGOU_MENSAGEM = "chegou_mensagem"

AVISO_FALHOU = "Não consegui marcar como lida na plataforma — ela continua não lida lá e no Duoke."
AVISO_SEM_MENSAGEM = (
    "Sem mensagem do comprador com o id da plataforma para marcar — ela continua não lida."
)

# Teto de espera pela plataforma (os clientes têm timeout próprio; este é o
# cinto para um que pendure). A tela não espera isso: ela chama e segue. A
# chamada NÃO é cancelada ao estourar — segue blindada (`_PREPARAR`).
TEMPO_MAXIMO_S = 20
# Duas abas (ou dois cliques) abrindo a mesma conversa: vai uma vez só.
TRAVA_S = 30
# ML: as mesmas páginas da leitura (`ml.PAGINA_PACK` × `ml.MAX_PAGINAS_PACK`).
PAGINA_PACK_ML = 20
MAX_PAGINAS_PACK_ML = 5
# Quantas mensagens recentes olhar procurando o id da Shopee.
_MAX_CANDIDATAS = 20
# "shopee_chat_lida error_param: ..." → só o código (a mensagem pode ecoar dado).
_RE_CODIGO = re.compile(r"^(\w+) ([a-z][a-z0-9_.]*):")


class LidaRecusada(Exception):  # noqa: N818 — como `EnvioRecusado`
    """Não é para marcar esta conversa (regra nossa): nada foi à plataforma."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(code)


@dataclass
class ResultadoLida:
    marcada: bool
    motivo: str | None = None
    aviso: str | None = None


# Zap (26/10/2026): as recusas próprias do tique azul.
RECUSA_ZAP_NUMERO_PAUSADO = "zap_numero_pausado"
MOTIVO_ZAP_SO_ADMIN = "Só administrador marca a conversa do Zap como lida no WhatsApp."
MOTIVO_ZAP_ENVIO_DESLIGADO = (
    "Zap: o envio pelo DaVinci está desligado — quem responde é o Duoke, e o "
    "cliente não vê o tique azul."
)
MOTIVO_ZAP_SEM_NUMERO = "Este número de WhatsApp não está mais cadastrado no DaVinci."
MOTIVO_ZAP_NUMERO_PAUSADO = "Este número está pausado no DaVinci: o tique azul não vai."
MOTIVO_ZAP_EM_OBSERVACAO = (
    "Este número está em observar: quem responde é o Duoke, e o cliente não vê o tique azul."
)
RECUSA_ZAP_FORA_DOS_TESTES = "zap_fora_dos_testes"
MOTIVO_ZAP_FORA_DOS_TESTES = (
    "Modo teste: o tique azul só vai para os números de teste (ou o contato está bloqueado)."
)


def e_zap(conversa: AtendimentoConversa) -> bool:
    return conversa.plataforma == PLATAFORMA_WHATSAPP and conversa.canal == CANAL_ZAP


def se_aplica(conversa: AtendimentoConversa) -> bool:
    """A plataforma desta conversa tem "marcar como lida" pela API?

    O chat de AFILIADO da Shopee não (15/10/2026): o `read_conversation` sem
    `business_type=11` não acha a conversa (o `get_message` e o
    `get_one_conversation` respondem `param_error` — medido), e com ele nunca
    foi medido. O "visto no DaVinci" (`gravar_visto`) continua valendo.

    O Zap (26/10/2026): só com a chave do tique azul
    (`atendimento_zap_marcar_lida`, desligada) — o cliente VÊ que alguém leu.
    """
    if e_zap(conversa):
        return bool(get_settings().atendimento_zap_marcar_lida)
    if e_chat_de_afiliado(conversa.plataforma, conversa.dados):
        return False
    return conversa.canal in PLATAFORMAS_QUE_MARCAM.get(conversa.plataforma, frozenset())


async def _canal_e_loja(
    session: AsyncSession, conversa: AtendimentoConversa
) -> tuple[AtendimentoCanal | None, Integration | None]:
    canal = (
        await session.get(AtendimentoCanal, conversa.canal_id, populate_existing=True)
        if conversa.canal_id is not None
        else None
    )
    integration = (
        await session.get(Integration, canal.integration_id, populate_existing=True)
        if canal is not None and canal.integration_id is not None
        else None
    )
    return canal, integration


async def _conferir_zap(
    session: AsyncSession, conversa: AtendimentoConversa, user: User
) -> tuple[AtendimentoCanal, None]:
    """As travas do tique azul no Zap (as do envio, sem as da mensagem). Canal e número RELIDOS."""
    if user.role != UserRole.ADMIN:
        raise LidaRecusada(RECUSA_SO_LEITURA, MOTIVO_ZAP_SO_ADMIN)
    if not get_settings().atendimento_zap_envio_ativo:
        raise LidaRecusada(RECUSA_ENVIO_DESLIGADO, MOTIVO_ZAP_ENVIO_DESLIGADO)
    canal = (
        await session.get(AtendimentoCanal, conversa.canal_id, populate_existing=True)
        if conversa.canal_id is not None
        else None
    )
    numero = await zap.numero_do_pnid(session, canais_externos.pnid_do_canal(canal))
    if canal is None or numero is None:
        raise LidaRecusada(RECUSA_SEM_INTEGRACAO, MOTIVO_ZAP_SEM_NUMERO)
    if numero.etapa not in zap.ETAPAS_QUE_ENVIAM or not numero.envio_ligado:
        raise LidaRecusada(RECUSA_ZAP_NUMERO_PAUSADO, MOTIVO_ZAP_NUMERO_PAUSADO)
    if canal.modo not in MODOS_QUE_ENVIAM:
        raise LidaRecusada(RECUSA_CANAL_EM_OBSERVACAO, MOTIVO_ZAP_EM_OBSERVACAO)
    # O contato: as mesmas réguas do envio (§6.3 travas 10 e 11) — bloqueado
    # ou apagado (LGPD) não recebe nada, e fora de produção só a lista de teste.
    contato = await zap.contato_da_conversa(session, conversa)
    if (
        contato is None
        or contato.bloqueado
        or contato.apagado_em is not None
        or not zap.destino_liberado(
            contato.wa_id, zap.lista_de_teste(numero), producao=get_settings().is_prod
        )
    ):
        raise LidaRecusada(RECUSA_ZAP_FORA_DOS_TESTES, MOTIVO_ZAP_FORA_DOS_TESTES)
    return canal, None


async def conferir(
    session: AsyncSession, conversa: AtendimentoConversa, user: User | None
) -> tuple[AtendimentoCanal, Integration | None]:
    """As travas que não dependem da plataforma; levanta `LidaRecusada`.

    A ordem: a pessoa (quem só lê, ou ninguém — IA/cron —, nunca), a
    conversa (plataforma com "lida"), o servidor (envio ligado) e a loja
    (conectada, num modo em que o DaVinci responde). Canal e loja RELIDOS.
    No Zap (sem loja), o número no lugar da loja (`_conferir_zap`).
    """
    # 28/10/2026: quem RESPONDE (toda a equipe que vê a caixa) também marca.
    if user is None or not acesso.pode_responder(user):
        raise LidaRecusada(RECUSA_SO_LEITURA, "Só quem responde no Atendimento marca como lida.")
    if not se_aplica(conversa):
        raise LidaRecusada(RECUSA_NAO_SE_APLICA, MOTIVO_NAO_SE_APLICA)
    if not get_settings().atendimento_envio_ativo:
        raise LidaRecusada(RECUSA_ENVIO_DESLIGADO, MOTIVO_ENVIO_DESLIGADO)
    if e_zap(conversa):
        return await _conferir_zap(session, conversa, user)
    canal, integration = await _canal_e_loja(session, conversa)
    if canal is None or integration is None or integration.archived_at is not None:
        raise LidaRecusada(RECUSA_SEM_INTEGRACAO, MOTIVO_SEM_INTEGRACAO)
    if canal.modo not in MODOS_QUE_ENVIAM:
        raise LidaRecusada(RECUSA_CANAL_EM_OBSERVACAO, MOTIVO_EM_OBSERVACAO)
    return canal, integration


async def marcar_ao_abrir(
    session: AsyncSession, conversa: AtendimentoConversa, user: User | None
) -> bool:
    """A tela deve chamar o POST /lida ao abrir esta conversa? (o `marcar_lida` do detalhe)

    As MESMAS travas da rota (`conferir`) e algo para marcar (`nao_lidas` > 0):
    tela e API nunca discordam. Nunca levanta — no pior caso, False.
    """
    zap_conversa = e_zap(conversa)
    if not zap_conversa and (conversa.nao_lidas or 0) <= 0:
        return False
    try:
        await conferir(session, conversa, user)
        if zap_conversa:
            # O Zap não tem `nao_lidas`: algo a marcar = mensagem do cliente
            # que a Meta ainda não recebeu como lida (`_wamid_para_marcar`).
            return await _wamid_para_marcar(session, conversa) is not None
    except LidaRecusada:
        return False
    except Exception as e:  # noqa: BLE001 — o detalhe da conversa nunca cai por isto
        logger.warning("atendimento_lida_conferir_falhou", erro=type(e).__name__)
        return False
    return True


def visto_ate(mensagens: Any) -> datetime | None:
    """Até onde a tela mostra a conversa: a CHEGADA (`created_at`) da mensagem
    mais nova entre as do detalhe. PURA. None = sem mensagem.

    Vai no detalhe (`visto_ate`) e volta no POST /visto ou /lida — o
    `visto_em` que o número da Caixa compara com a chegada da mensagem.
    """
    chegadas = [_utc_ou_none(getattr(m, "created_at", None)) for m in mensagens or ()]
    validas = [c for c in chegadas if c is not None]
    return max(validas) if validas else None


def _utc_ou_none(quando: Any) -> datetime | None:
    if not isinstance(quando, datetime):
        return None
    return quando.replace(tzinfo=UTC) if quando.tzinfo is None else quando.astimezone(UTC)


async def gravar_visto(
    session: AsyncSession,
    conversa: AtendimentoConversa,
    user: User | None,
    ate: datetime | None = None,
    *,
    notificacoes_migracao_id: str | None = None,
) -> datetime | None:
    """Grava o "visto no DaVinci" (`visto_em`) e devolve o carimbo.

    Uma PESSOA que vê a caixa abriu a conversa na tela: em qualquer modo de
    loja e em qualquer canal, sem ir à plataforma. `ate` = o `visto_ate` do
    detalhe que a tela mostrava (a chegada da mensagem mais nova); sem ele, o
    relógio do banco — e nunca depois dele (`LEAST`). Quem não vê (ou
    ninguém — IA, crons) → `LidaRecusada(RECUSA_SO_LEITURA)`, nada gravado. Idempotente:
    nunca anda para trás. Commita e relê a conversa.

    O marco nativo só avança quando a tela confirma o snapshot_id da epoch
    que realmente exibiu. Um POST antigo que atravessa a importação pode
    atualizar o visto legado, mas nunca consumir o seed recém-importado.
    """
    if user is None or not acesso.pode_ver(user):
        raise LidaRecusada(RECUSA_SO_LEITURA, "Só quem vê o Atendimento marca como vista.")
    # O relógio do BANCO (o mesmo do `created_at` da mensagem), na hora do
    # UPDATE — o `now()` seria o começo desta transação.
    relogio = func.clock_timestamp()
    ate_utc = _utc_ou_none(ate)
    marco = (
        relogio
        if ate_utc is None
        else func.least(literal(ate_utc, DateTime(timezone=True)), relogio)
    )
    try:
        migracao_id = str(UUID(notificacoes_migracao_id)) if notificacoes_migracao_id else None
    except (TypeError, ValueError, AttributeError):
        migracao_id = None
    mesma_migracao = (
        notificacoes_svc.estado_valido_sql(AtendimentoConversa)
        & (AtendimentoConversa.notificacoes_iniciais["snapshot_id"].astext == migracao_id)
        if migracao_id is not None
        else False
    )
    await session.execute(
        update(AtendimentoConversa)
        .where(AtendimentoConversa.id == conversa.id)
        .values(
            # GREATEST ignora o NULL: a primeira abertura grava o marco.
            visto_em=func.greatest(AtendimentoConversa.visto_em, marco),
            notificacoes_visto_em=case(
                (
                    mesma_migracao,
                    func.greatest(AtendimentoConversa.notificacoes_visto_em, marco),
                ),
                else_=AtendimentoConversa.notificacoes_visto_em,
            ),
            # Marca de leitura, não mudança da conversa.
            updated_at=AtendimentoConversa.updated_at,
        )
        .execution_options(synchronize_session=False)
    )
    await session.commit()
    await session.refresh(conversa)
    return conversa.visto_em


async def zerar_nao_lidas(
    session: AsyncSession,
    user: User | None,
    *,
    plataformas: Iterable[str] = (),
    integration_id: Any = None,
    escopo: Any = None,
) -> int:
    """O "Zerar não lidas" — o "Limpar não lidas" do Duoke (15/10/2026). Devolve
    quantas conversas foram marcadas como vistas.

    O Duoke tem o botão para zerar o não lido de tudo de uma vez (foi o que
    zerou o Duoke inteiro em 14/10/2026 às 12:35 — `pendente.ZERADO_NO_DUOKE_EM`).
    Aqui é o "visto no DaVinci" em lote: `visto_em` = agora (o relógio do
    banco, nunca para trás) nas conversas do número (o chat e a reclamação do
    ML, `pendente.do_numero`) com mensagem dentro do
    número (`pendente.desde_do_numero`), da loja (`integration_id`), das
    plataformas (`plataformas`) ou de todas — dentro do escopo da equipe (`escopo`, o WHERE do
    router). Nunca vai à plataforma, não muda o `updated_at`. Só quem mexe na
    caixa (`acesso.pode_mexer`); senão `LidaRecusada(RECUSA_SO_LEITURA)`.
    """
    if user is None or not acesso.pode_mexer(user):
        raise LidaRecusada(RECUSA_SO_LEITURA, "Só quem mexe no Atendimento zera as não lidas.")
    from app.services.atendimento import pendente

    filtros = [
        pendente.do_numero_sql(AtendimentoConversa),
        or_(
            notificacoes_svc.estado_valido_sql(AtendimentoConversa),
            AtendimentoConversa.ultima_mensagem_em > pendente.desde_do_numero(),
        ),
    ]
    plataformas = sorted({p for p in plataformas if p})
    if plataformas:
        filtros.append(AtendimentoConversa.plataforma.in_(plataformas))
    if integration_id is not None:
        filtros.append(AtendimentoConversa.integration_id == integration_id)
    if escopo is not None:
        filtros.append(escopo)
    # Até a mensagem mais nova que JÁ ESTAVA gravada (o snapshot do comando),
    # nunca depois do relógio: a que o sync está gravando no meio do clique
    # (o `created_at` é o começo da transação dele) continua contando — como o
    # `visto_ate` do /visto.
    ultima = (
        select(func.max(AtendimentoMensagem.created_at))
        .where(AtendimentoMensagem.conversa_id == AtendimentoConversa.id)
        .correlate(AtendimentoConversa)
        .scalar_subquery()
    )
    r = await session.execute(
        update(AtendimentoConversa)
        .where(*filtros)
        .values(
            visto_em=func.greatest(
                AtendimentoConversa.visto_em, func.least(func.clock_timestamp(), ultima)
            ),
            notificacoes_visto_em=case(
                (
                    notificacoes_svc.estado_valido_sql(AtendimentoConversa),
                    func.greatest(
                        AtendimentoConversa.notificacoes_visto_em,
                        func.least(func.clock_timestamp(), ultima),
                    ),
                ),
                else_=AtendimentoConversa.notificacoes_visto_em,
            ),
            updated_at=AtendimentoConversa.updated_at,
        )
        .execution_options(synchronize_session=False)
    )
    await session.commit()
    total = int(r.rowcount or 0)
    logger.info(
        "atendimento_nao_lidas_zeradas",
        user_id=str(getattr(user, "id", "")),
        plataformas=plataformas,
        integration_id=str(integration_id) if integration_id else None,
        conversas=total,
    )
    return total


def _chave_trava(conversa_id: Any) -> str:
    return f"atd:lida:{conversa_id}"


async def _pegar_trava(conversa_id: Any) -> bool:
    """Só um pedido por conversa por vez. Redis fora do ar = deixa passar
    (marcar duas vezes na plataforma não faz mal; travar a tela faria)."""
    try:
        return bool(await redis.set(_chave_trava(conversa_id), "1", nx=True, ex=TRAVA_S))
    except Exception as e:  # noqa: BLE001
        logger.warning("atendimento_lida_trava_indisponivel", erro=type(e).__name__)
        return True


async def _soltar_trava(conversa_id: Any) -> None:
    try:
        await redis.delete(_chave_trava(conversa_id))
    except Exception:  # noqa: BLE001, S110 — a trava expira sozinha
        pass


def _erro_curto(exc: BaseException) -> str:
    """O código do erro para o log — nunca a mensagem inteira da plataforma."""
    if isinstance(exc, TimeoutError):
        return "timeout"
    resposta = getattr(exc, "response", None)
    status = getattr(resposta, "status_code", None)
    if isinstance(status, int):
        return f"http_{status}"
    achou = _RE_CODIGO.match(str(exc))
    if isinstance(exc, RuntimeError) and achou:
        return achou.group(2)
    return type(exc).__name__


# ── Plataformas ──────────────────────────────────────────────────────────
#
# Em dois tempos, por causa do refresh token de USO ÚNICO (Shopee e ML):
#   1. `_preparar_*`, na sessão do request: confere e busca no banco o que a
#      plataforma precisa e devolve a CHAMADA pronta (uma corrotina que só
#      leva o cliente e ids) ou o código do erro;
#   2. a chamada roda numa tarefa BLINDADA (`asyncio.shield`, como o
#      `cliente._ml_ao_vivo`): passou do `TEMPO_MAXIMO_S`, a rota responde
#      "não marcou" e a tarefa segue até o fim. Se o cliente estava renovando
#      o token nessa hora, o novo chega ao banco (sessão própria do
#      `clientes`) — cancelar no meio perderia o refresh token, e a loja
#      ficaria sem acesso até alguém reautorizar à mão. Por isso a tarefa
#      nunca toca a sessão do request, que fecha quando a rota responde.

# A chamada pronta: None = a plataforma marcou; texto = o código do erro.
_Chamada = Callable[[], Awaitable[str | None]]

# Chamadas que passaram do tempo e seguem em segundo plano (referência forte,
# senão o coletor de lixo pode matar a tarefa no meio).
_EM_SEGUNDO_PLANO: set[asyncio.Future] = set()


def _em_segundo_plano(coro: Awaitable[Any]) -> asyncio.Future:
    tarefa = asyncio.ensure_future(coro)
    _EM_SEGUNDO_PLANO.add(tarefa)
    tarefa.add_done_callback(_EM_SEGUNDO_PLANO.discard)
    return tarefa


async def _ultima_de_fora_shopee(session: AsyncSession, conversa_id: Any) -> str:
    """O id da Shopee da mensagem mais recente que NÃO é resposta da loja ("" = nenhuma).

    Entram a do comprador (`cliente`) e a de `sistema` — inclusive a resposta
    AUTOMÁTICA da própria loja (`auto_reply`/`offwork_autoreply`, que vem com
    o `from_shop_id` da loja): a Shopee a conta no não lido do vendedor.
    Medido em produção em 14/10/2026 (só contagens, todas as lojas): 17
    conversas da Shopee com não lidas em que, depois da última resposta da
    loja, só há automáticas da própria loja — 13 delas sem mensagem nenhuma
    do comprador (a f7b34b27 da Kia: 5 automáticas, 2 não lidas; a 4847d51f:
    1 automática, 1 não lida). Parar na do comprador deixaria a automática
    depois dela não lida lá (e no Duoke) com o `nao_lidas` zerado aqui, e
    essas 13 nem teriam ponteiro. Fica de fora só a `loja` — a resposta de
    gente (nossa ou do Duoke) e o cartão que a loja manda —, que não entra
    no não lido do vendedor.
    """
    momento = func.coalesce(AtendimentoMensagem.enviada_em, AtendimentoMensagem.created_at)
    ids = (
        await session.scalars(
            select(AtendimentoMensagem.externo_id)
            .where(
                AtendimentoMensagem.conversa_id == conversa_id,
                AtendimentoMensagem.autor.in_((AUTOR_CLIENTE, AUTOR_SISTEMA)),
                AtendimentoMensagem.externo_id.is_not(None),
            )
            .order_by(momento.desc(), AtendimentoMensagem.created_at.desc())
            .limit(_MAX_CANDIDATAS)
        )
    ).all()
    for mid in ids:
        texto = str(mid or "").strip()
        if texto.isdigit():
            return texto
    return ""


async def _preparar_shopee(
    session: AsyncSession, conversa: AtendimentoConversa, cliente: Any
) -> _Chamada | str:
    conversation_id = str(conversa.externo_id or "").strip()
    if not conversation_id.isdigit():
        return "shopee conversa_sem_id"
    ultima = await _ultima_de_fora_shopee(session, conversa.id)
    if not ultima:
        return MOTIVO_SEM_MENSAGEM

    async def chamar() -> str | None:
        await cliente.chat_read_conversation(conversation_id, ultima)
        return None

    return chamar


async def _preparar_tiktok(
    session: AsyncSession, conversa: AtendimentoConversa, cliente: Any
) -> _Chamada | str:
    del session
    conversation_id = str(conversa.externo_id or "").strip()

    async def chamar() -> str | None:
        resp = await cliente.cs_read_message(conversation_id)
        codigo = resp.get("code") if isinstance(resp, dict) else None
        if codigo in (0, "0"):
            return None
        # 105005/401 = o app sem `seller.customer_service` (hoje, todas as lojas).
        return f"tiktok code={codigo}"

    return chamar


async def _preparar_ml(
    session: AsyncSession, conversa: AtendimentoConversa, cliente: Any
) -> _Chamada | str:
    del session
    seller_id = str((getattr(cliente, "creds", None) or {}).get("user_id") or "").strip()
    if not seller_id:
        return "ml sem_seller_id"
    pack_id = str(conversa.externo_id or "").strip()

    async def chamar() -> str | None:
        await cliente.marcar_pack_como_lido(
            pack_id,
            seller_id,
            limite=PAGINA_PACK_ML,
            max_paginas=MAX_PAGINAS_PACK_ML,
        )
        return None

    return chamar


_PREPARAR = {
    "shopee": _preparar_shopee,
    "tiktok": _preparar_tiktok,
    "ml": _preparar_ml,
}


async def _wamid_para_marcar(session: AsyncSession, conversa: AtendimentoConversa) -> str | None:
    """A última mensagem do cliente (≤ 30 dias) que AINDA não foi marcada; None = nada a marcar.

    O Zap não tem `nao_lidas` (a Meta não manda contador): a marca é o
    `dados.zap.lida_wamid` — o wamid que a Meta já aceitou como lido.
    """
    wamid = await zap.ultima_de_entrada(session, conversa, datetime.now(UTC))
    if wamid is None:
        return None
    dados = conversa.dados if isinstance(conversa.dados, dict) else {}
    zap_dados = dados.get("zap") if isinstance(dados.get("zap"), dict) else {}
    return None if zap_dados.get("lida_wamid") == wamid else wamid


# A Meta recusou o `message_id` de um jeito que não muda tentando de novo
# (pedido inválido: o wamid não existe para ela, passou dos 30 dias) [confirmar
# no ensaio do §11.5]. O wamid fica gravado como "já tratado": sem isto, cada
# abertura da conversa repetia a chamada e o aviso de falha. Limite, token e
# permissão NÃO entram (passam, ou a credencial é trocada).
_LIDA_RECUSA_DEFINITIVA = frozenset({100, 131009})


async def _gravar_lida(
    session: AsyncSession,
    conversa: AtendimentoConversa,
    wamid: str,
    *,
    recusada: int | None = None,
) -> None:
    """Grava `dados.zap.lida_wamid` (e `lida_em`) sobre a linha RELIDA e travada. Commita.

    A chamada à Meta leva até `TEMPO_MAXIMO_S`; nesse meio-tempo o
    processador do webhook pode ter gravado outra chave de `dados` (o `ctwa`
    do anúncio, o `comprador_plataforma`). Regravar a cópia lida no começo
    do pedido (a sessão não expira no commit) apagaria isso (revisão,
    26/10/2026): trava, relê e só então mescla a chave do Zap.
    """
    await gravar.travar_linha(session, conversa)
    dados = dict(conversa.dados or {})
    zap_dados = dict(dados.get("zap") or {})
    zap_dados["lida_wamid"] = wamid
    zap_dados["lida_em"] = datetime.now(UTC).isoformat()
    if recusada is not None:
        zap_dados["lida_recusada"] = recusada
    else:
        zap_dados.pop("lida_recusada", None)
    conversa.dados = {**dados, "zap": zap_dados}
    await session.commit()
    await session.refresh(conversa)


async def _marcar_no_zap(
    session: AsyncSession, conversa: AtendimentoConversa, *, user: User | None
) -> ResultadoLida:
    """O tique azul (26/10/2026, §6.7): marca a última mensagem do CLIENTE como lida na Meta.

    As travas já passaram (`conferir`). Sem nada novo para marcar: "já lida".
    A Meta aceitou: grava `dados.zap.lida_wamid` (a próxima abertura não vai
    de novo). Falhou: `marcada=False` com o aviso — a tela não quebra; na
    recusa definitiva (`_LIDA_RECUSA_DEFINITIVA`) o wamid também fica
    gravado, para a abertura seguinte não repetir a chamada. O
    token é decifrado só aqui, na memória; a chamada não renova token
    nenhum, então o teto de tempo pode cancelar (marcar de novo é idempotente).
    """
    wamid = await _wamid_para_marcar(session, conversa)
    if wamid is None:
        return ResultadoLida(marcada=False, motivo=MOTIVO_JA_LIDA)
    if not await _pegar_trava(conversa.id):
        return ResultadoLida(marcada=False, motivo=MOTIVO_EM_ANDAMENTO)
    log = {
        "conversa_id": str(conversa.id),
        "plataforma": conversa.plataforma,
        "canal": conversa.canal,
        "user_id": str(user.id) if user is not None else None,
    }
    try:
        canal = (
            await session.get(AtendimentoCanal, conversa.canal_id) if conversa.canal_id else None
        )
        numero = await zap.numero_do_pnid(session, canais_externos.pnid_do_canal(canal))
        token = (
            zap.token_valido(await zap.conta_do_numero(session, numero))
            if numero is not None
            else None
        )
        if numero is None or token is None:
            erro: str | None = "zap sem_credencial"
        else:
            try:
                falha = await asyncio.wait_for(
                    zap.marcar_lida(numero.phone_number_id, token, wamid), timeout=TEMPO_MAXIMO_S
                )
                erro = f"zap {falha}" if falha else None
            except Exception as e:  # noqa: BLE001 — a tela não quebra; fica no log
                erro = f"zap {_erro_curto(e)}"
        if erro:
            logger.warning("atendimento_lida_plataforma_falhou", erro=erro, **log)
            code = zap_erros.codigo_do_erro(erro.removeprefix("zap "))
            if code in _LIDA_RECUSA_DEFINITIVA:
                await _gravar_lida(session, conversa, wamid, recusada=code)
            return ResultadoLida(marcada=False, motivo=MOTIVO_PLATAFORMA_FALHOU, aviso=AVISO_FALHOU)
        await _gravar_lida(session, conversa, wamid)
        logger.info("atendimento_lida_marcada", zerou=False, **log)
        return ResultadoLida(marcada=True)
    finally:
        await _soltar_trava(conversa.id)


def _terminou_depois_do_tempo(conversa_id: Any, log: dict, tarefa: asyncio.Future) -> None:
    """A chamada que passou do tempo acabou: registra como foi e solta a trava.

    Marcou lá (erro None) e `nao_lidas` ficou: o número fica com o sync, que
    relê o da plataforma — nada aqui volta ao banco (a sessão já fechou).
    """
    if tarefa.cancelled():
        erro: str | None = "cancelada"
    else:
        exc = tarefa.exception()
        erro = _erro_curto(exc) if exc is not None else tarefa.result()
    logger.info("atendimento_lida_terminou_depois_do_tempo", erro=erro, **log)
    _em_segundo_plano(_soltar_trava(conversa_id))


async def _zerar(session: AsyncSession, conversa: AtendimentoConversa, ultima_antes) -> bool:
    """Zera `nao_lidas` SE não chegou mensagem desde que a pessoa abriu (False = chegou).

    Chegou mensagem no meio (o sync gravou enquanto a plataforma respondia):
    a da Shopee pode ter ficado depois do `last_read_message_id` — o número
    fica com o sync, que relê o da plataforma.
    """
    feito = await session.execute(
        update(AtendimentoConversa)
        .where(
            AtendimentoConversa.id == conversa.id,
            AtendimentoConversa.ultima_mensagem_em.is_not_distinct_from(ultima_antes),
        )
        .values(nao_lidas=0)
        .execution_options(synchronize_session=False)
    )
    await session.commit()
    await session.refresh(conversa)
    return bool(feito.rowcount)


async def marcar_como_lida(
    session: AsyncSession,
    conversa: AtendimentoConversa,
    *,
    user: User | None,
) -> ResultadoLida:
    """Marca a conversa como lida NA PLATAFORMA e, se ela aceitou, zera `nao_lidas`.

    Recusa por regra → `LidaRecusada` (nada vai à plataforma). Falha da
    plataforma não levanta: `marcada=False` com o aviso, e `nao_lidas` fica.
    """
    canal, integration = await conferir(session, conversa, user)
    if e_zap(conversa):
        return await _marcar_no_zap(session, conversa, user=user)
    if (conversa.nao_lidas or 0) <= 0:
        return ResultadoLida(marcada=False, motivo=MOTIVO_JA_LIDA)
    if not await _pegar_trava(conversa.id):
        return ResultadoLida(marcada=False, motivo=MOTIVO_EM_ANDAMENTO)
    ultima_antes = conversa.ultima_mensagem_em
    log = {
        "conversa_id": str(conversa.id),
        "plataforma": conversa.plataforma,
        "canal": conversa.canal,
        "modo": canal.modo,
        "user_id": str(user.id) if user is not None else None,
    }
    tarefa: asyncio.Future | None = None
    try:
        try:
            cliente = await clientes.cliente_da_integracao(integration)
            preparo = await _PREPARAR[conversa.plataforma](session, conversa, cliente)
            if isinstance(preparo, str):
                erro: str | None = preparo
            else:
                tarefa = _em_segundo_plano(preparo())
                erro = await asyncio.wait_for(asyncio.shield(tarefa), timeout=TEMPO_MAXIMO_S)
        except Exception as e:  # noqa: BLE001 — a tela não quebra; fica no log
            erro = f"{conversa.plataforma} {_erro_curto(e)}"
        if erro == MOTIVO_SEM_MENSAGEM:
            logger.info("atendimento_lida_sem_mensagem", **log)
            return ResultadoLida(
                marcada=False, motivo=MOTIVO_SEM_MENSAGEM, aviso=AVISO_SEM_MENSAGEM
            )
        if erro:
            logger.warning("atendimento_lida_plataforma_falhou", erro=erro, **log)
            return ResultadoLida(marcada=False, motivo=MOTIVO_PLATAFORMA_FALHOU, aviso=AVISO_FALHOU)
        zerou = await _zerar(session, conversa, ultima_antes)
        logger.info("atendimento_lida_marcada", zerou=zerou, **log)
        return ResultadoLida(marcada=True, motivo=None if zerou else MOTIVO_CHEGOU_MENSAGEM)
    finally:
        if tarefa is not None and not tarefa.done():
            # Passou do tempo e segue blindada: a trava fica até ela acabar
            # (uma segunda abertura agora iria junto com ela à plataforma).
            tarefa.add_done_callback(partial(_terminou_depois_do_tempo, conversa.id, log))
        else:
            await _soltar_trava(conversa.id)
