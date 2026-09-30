"""Recepção do robô do Mac mini: Temu e AliExpress na caixa (30/09/2026).

Temu e AliExpress não têm API de chat. Decisão do Eduardo: um robô no Mac mini
mantém um perfil do AdsPower por loja com a LISTA de conversas do Seller
Center aberta (nenhuma conversa selecionada — abrir marca como lida para a
equipe inteira) e só ESCUTA o que a página já recebe. Ele manda para cá
(routers/atendimento_robo.py, com token):

  pulso   — "estou vivo, lendo esta loja" (a cada minuto, mais ou menos);
  eventos — cópias cruas das respostas de fetch/XHR e dos quadros de
            WebSocket que a página recebeu.

Quem INTERPRETA é o DaVinci (`robo_temu`, `robo_aliexpress`, Python puro e
testável); aqui só se grava, pela porta única (`gravar`), como o leitor da
Amazon faz com o e-mail:

  • a LOJA é um canal SEM integração (`atendimento_canais.robo_perfil_id` =
    o perfil do AdsPower; migration 0347): o cron do sync não o enxerga, e
    nenhum worker que percorre `integrations` (factory → TemuClient) fica
    sabendo dele;
  • idempotência: a mesma mensagem duas vezes não duplica (id da plataforma,
    UNIQUE no banco); a conversa é única por (canal, id);
  • a resposta dada NO SELLER CENTER chega como mensagem da loja com origem
    `externo` e tira a conversa da fila (e aposenta a sugestão da IA), como a
    cópia do Seller Central da Amazon;
  • a resposta do ROBÔ DA PLATAFORMA (Temu `context.robot`, AliExpress
    `im_ai`) entra como `sistema`: não é a equipe respondendo, a conversa
    continua na fila. Quando a lista só mostra essa resposta e a plataforma
    diz "sem resposta", a pergunta que o robô não viu entra como MARCADOR do
    comprador (`robo_leitura.marcador_nao_vista`), adotado pela pergunta de
    verdade quando ela chegar;
  • "leitura parada": sem sinal (pulso ou evento) há mais de
    `atendimento_robo_parado_min` minutos, a loja aparece APAGADA na barra de
    lojas (`status_efetivo`, calculado na leitura — quando o robô morre,
    nenhum pulso chega para avisar).

O envio para essas lojas é BLOQUEADO (`enviar`: responda no Seller Center) e
o modo da loja não passa de observar/copiloto (`MODOS_ROBO`). A IA sugere
normalmente (o cron da IA junta a conversa com este canal para saber o modo).

Texto de comprador nunca vai para o log: só ids, contagens e NOMES de campo
que o leitor não reconheceu.
"""

from __future__ import annotations

import hashlib
import importlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import ModuleType
from typing import Any
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import structlog
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import DBAPIError, InterfaceError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import AtendimentoCanal, AtendimentoConversa, AtendimentoMensagem
from app.services.atendimento import gravar
from app.services.atendimento.constantes import (
    AUTOR_CLIENTE,
    AUTOR_LOJA,
    AUTOR_SISTEMA,
    CANAL_CHAT,
    MODO_OBSERVAR,
    MODOS_ROBO,
    MSG_ENVIADA,
    MSG_RECEBIDA,
    ORIGEM_CLIENTE,
    ORIGEM_EXTERNO,
    ORIGEM_SISTEMA,
    PLATAFORMAS_ROBO,
    STATUS_CANAL_PARADO,
    STATUS_CANAL_SESSAO_CAIU,
)
from app.services.atendimento.robo_leitura import (
    PREFIXO_PREVIA,
    ConversaLida,
    Evento,
    Leitura,
    MensagemLida,
)

logger = structlog.get_logger()

# O leitor de cada plataforma, importado na hora (como os adaptadores do
# `enviar`): o teste troca um leitor por monkeypatch neste dicionário.
LEITORES: dict[str, str] = {
    "temu": "app.services.atendimento.robo_temu",
    "aliexpress": "app.services.atendimento.robo_aliexpress",
}

# O que o robô diz no pulso → a saúde do canal (o vocabulário da barra).
ESTADO_INICIANDO = "iniciando"
ESTADO_LENDO = "lendo"
ESTADO_SESSAO_CAIU = "sessao_caiu"
ESTADO_ERRO = "erro"
ESTADOS_PULSO = (ESTADO_INICIANDO, ESTADO_LENDO, ESTADO_SESSAO_CAIU, ESTADO_ERRO)
_STATUS_DO_ESTADO = {
    ESTADO_INICIANDO: "novo",
    ESTADO_LENDO: "ok",
    ESTADO_SESSAO_CAIU: STATUS_CANAL_SESSAO_CAIU,
    ESTADO_ERRO: "erro",
}

# Parâmetro de URL que abre UMA conversa no Seller Center — e com isso a
# página marca como lida para a equipe inteira (Temu `?posn=<pedido>`,
# AliExpress deeplink com sessão/digest). A aba do robô nunca pode estar
# assim: o pulso que chega com um deles põe a loja em erro, bem à vista.
_PARAMETROS_QUE_ABREM_CONVERSA = frozenset(
    {"posn", "convid", "conv_id", "sessionid", "sessionviewid", "digest"}
)

# Quantas mensagens recentes da conversa a prévia da lista (AliExpress, sem
# id) confere antes de entrar (ver `_previa_repetida`).
_ULTIMAS_PARA_PREVIA = 5
# A mesma mensagem tem a mesma hora na lista (lastMessageTime) e no sync
# (sendTime); a folga cobre arredondamento e relógio.
_FOLGA_HORA_PREVIA = timedelta(minutes=5)
# Quantas prévias a mensagem de verdade procura para adotar.
_PREVIAS_PARA_ADOTAR = 20
_TEXTO_OPERACAO_MAX = 300

_ORIGEM_DO_AUTOR = {
    AUTOR_CLIENTE: ORIGEM_CLIENTE,
    AUTOR_LOJA: ORIGEM_EXTERNO,
    AUTOR_SISTEMA: ORIGEM_SISTEMA,
}


class RoboRecusado(Exception):  # noqa: N818 — como o `EnvioRecusado`: é o nome do contrato
    """O pedido do robô não pode ser aceito (config errada, não defeito): vira 4xx."""

    def __init__(self, code: str, detail: str, status: int = 422) -> None:
        self.code = code
        self.detail = detail
        self.status = status
        super().__init__(code)


@dataclass
class ResultadoRobo:
    gravadas: int = 0
    conversas: int = 0
    ignorados: int = 0
    erros: int = 0


# ── Ajudantes ─────────────────────────────────────────────────────────────


def _curto(valor: str | None, tamanho: int = _TEXTO_OPERACAO_MAX) -> str | None:
    t = " ".join((valor or "").split())
    return t[:tamanho] or None


def dados_robo(canal: AtendimentoCanal) -> dict:
    """O `cursor["robo"]` do canal: loja, estado, último pulso, último evento."""
    robo = (canal.cursor or {}).get("robo")
    return robo if isinstance(robo, dict) else {}


def nome_da_loja(canal: AtendimentoCanal) -> str:
    """O nome da loja que o robô manda (config dele), ou o perfil do AdsPower."""
    nome = _curto(str(dados_robo(canal).get("loja") or ""), 120)
    return nome or f"perfil {canal.robo_perfil_id}"


def eh_do_robo(canal: AtendimentoCanal | None) -> bool:
    return canal is not None and canal.robo_perfil_id is not None


def modo_permitido(plataforma: str | None, modo: str | None) -> bool:
    """Loja do robô: só observar/copiloto (nada sai pelo DaVinci)."""
    return plataforma not in PLATAFORMAS_ROBO or modo in MODOS_ROBO


def url_sem_segredo(url: str | None) -> str | None:
    """Esquema, host e caminho (+ a rota do `#`), SEM query: pode levar token."""
    try:
        partes = urlsplit(url or "")
    except ValueError:
        return None
    if not partes.scheme or not partes.netloc:
        return None
    rota = (partes.fragment or "").split("?", 1)[0]
    return f"{partes.scheme}://{partes.netloc}{partes.path}" + (f"#{rota}" if rota else "")[:200]


def conversa_aberta_na_url(url: str | None) -> str | None:
    """O parâmetro que abre uma conversa (e marca lido), se a URL tiver um."""
    try:
        partes = urlsplit(url or "")
    except ValueError:
        return None
    consultas = [partes.query]
    if "?" in (partes.fragment or ""):
        consultas.append(partes.fragment.split("?", 1)[1])
    for consulta in consultas:
        for chave in parse_qs(consulta, keep_blank_values=True):
            if chave.lower() in _PARAMETROS_QUE_ABREM_CONVERSA:
                return chave
    return None


def _hora(valor: Any) -> datetime | None:
    if not isinstance(valor, str):
        return None
    try:
        d = datetime.fromisoformat(valor)
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=UTC)


def ultimo_sinal(canal: AtendimentoCanal) -> datetime | None:
    """O último pulso OU a última leva de eventos: qualquer um prova que o robô vive."""
    robo = dados_robo(canal)
    horas = [
        h
        for h in (_hora(robo.get("ultimo_pulso_em")), _hora(robo.get("ultimo_evento_em")))
        if h is not None
    ]
    return max(horas) if horas else None


def _duracao(delta: timedelta) -> str:
    minutos = max(0, int(delta.total_seconds() // 60))
    if minutos < 60:
        return f"{minutos} min"
    horas = minutos // 60
    if horas < 48:
        return f"{horas} h"
    return f"{horas // 24} dias"


def status_efetivo(
    canal: AtendimentoCanal, agora: datetime | None = None
) -> tuple[str, str | None]:
    """(status, motivo) do canal do robô como a barra de lojas deve mostrar.

    Sem sinal há mais de `atendimento_robo_parado_min` minutos = `parado`,
    seja qual for o último estado que o robô disse: morto, ele não avisa. O
    motivo é texto de operação (sem dado de comprador).
    """
    agora = agora or datetime.now(UTC)
    limite = timedelta(minutes=max(1, int(get_settings().atendimento_robo_parado_min or 5)))
    sinal = ultimo_sinal(canal)
    if sinal is None:
        return STATUS_CANAL_PARADO, "o robô do Mac mini ainda não deu sinal desta loja"
    if agora - sinal > limite:
        return (
            STATUS_CANAL_PARADO,
            f"o robô do Mac mini não dá sinal há {_duracao(agora - sinal)} "
            f"(perfil {canal.robo_perfil_id} no AdsPower)",
        )
    return canal.status, canal.ultimo_erro


# SQLSTATE de falha que passa sozinha: conexão (08), transação desfeita por
# deadlock/serialização (40), falta de recurso (53), intervenção do operador
# (57: banco reiniciando, consulta cancelada) e trava indisponível (55P03).
_SQLSTATE_PASSAGEIRO = ("08", "40", "53", "57", "55P03")


def erro_passageiro(e: BaseException) -> bool:
    """O erro some se o robô mandar de novo (banco/conexão), ou é do dado?"""
    if isinstance(e, OperationalError | InterfaceError | TimeoutError | ConnectionError):
        return True
    if isinstance(e, DBAPIError):
        if e.connection_invalidated:
            return True
        orig = getattr(e, "orig", None)
        causa = getattr(orig, "__cause__", None) or orig
        estado = str(getattr(causa, "sqlstate", None) or getattr(orig, "pgcode", None) or "")
        return estado.startswith(_SQLSTATE_PASSAGEIRO)
    return False


def _leitor(plataforma: str) -> ModuleType:
    return importlib.import_module(LEITORES[plataforma])


# ── Canal ─────────────────────────────────────────────────────────────────


async def canal_do_robo(
    session: AsyncSession, *, perfil_id: str, plataforma: str, loja: str
) -> AtendimentoCanal:
    """Acha ou cria o canal do perfil (nasce em `observar`, como toda loja). Não commita.

    `ON CONFLICT DO NOTHING` no UNIQUE do perfil: dois pedidos do robô ao
    mesmo tempo (pulso e eventos) não brigam. Perfil já registrado em OUTRA
    plataforma é erro de configuração do robô (409), nunca uma troca calada —
    as conversas da loja ficariam com a plataforma errada.
    """
    if plataforma not in PLATAFORMAS_ROBO:
        raise RoboRecusado("plataforma_invalida", f"plataforma sem robô: {plataforma[:20]}")
    await session.execute(
        pg_insert(AtendimentoCanal)
        .values(
            id=uuid4(),
            integration_id=None,
            robo_perfil_id=perfil_id,
            plataforma=plataforma,
            canal=CANAL_CHAT,
            modo=MODO_OBSERVAR,
            status="novo",
            cursor={"robo": {"loja": _curto(loja, 120)}},
            auto_categorias=[],
        )
        .on_conflict_do_nothing(index_elements=["robo_perfil_id"])
    )
    canal = (
        await session.execute(
            select(AtendimentoCanal)
            .where(AtendimentoCanal.robo_perfil_id == perfil_id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    if canal.plataforma != plataforma:
        raise RoboRecusado(
            "perfil_de_outra_plataforma",
            f"o perfil {perfil_id} já é de uma loja {canal.plataforma}",
            status=409,
        )
    return canal


def _com_robo(canal: AtendimentoCanal, **campos: Any) -> None:
    """Mescla campos em `cursor["robo"]` (dicionário NOVO: o JSONB muda de verdade)."""
    canal.cursor = {**(canal.cursor or {}), "robo": {**dados_robo(canal), **campos}}


# ── Pulso ─────────────────────────────────────────────────────────────────


async def registrar_pulso(
    session: AsyncSession,
    *,
    perfil_id: str,
    plataforma: str,
    loja: str,
    estado: str,
    url: str | None,
    detalhe: str | None,
    versao: str | None,
) -> AtendimentoCanal:
    """O robô disse como está a loja: grava o sinal e a saúde do canal. Não commita."""
    if estado not in ESTADOS_PULSO:
        raise RoboRecusado("estado_invalido", f"estado desconhecido: {str(estado)[:20]}")
    canal = await canal_do_robo(session, perfil_id=perfil_id, plataforma=plataforma, loja=loja)
    # A tela pode estar trocando o modo desta loja agora: a mesma trava dela.
    await gravar.travar_linha(session, canal)
    agora = datetime.now(UTC)
    status = _STATUS_DO_ESTADO[estado]
    detalhe_curto = _curto(detalhe)
    erro: str | None = None
    aberta = conversa_aberta_na_url(url)
    if aberta:
        status = "erro"
        erro = (
            f"a aba do robô está numa conversa (parâmetro {aberta} na URL) — isso marca "
            "como lida no Seller Center; volte a aba para a lista de conversas"
        )
    elif estado == ESTADO_SESSAO_CAIU:
        erro = "o Seller Center saiu da conta no perfil do AdsPower: alguém precisa entrar de novo"
        if detalhe_curto:
            erro += f" ({detalhe_curto})"
    elif estado == ESTADO_ERRO:
        erro = f"o robô avisou erro: {detalhe_curto}" if detalhe_curto else "o robô avisou erro"
    _com_robo(
        canal,
        loja=_curto(loja, 120),
        estado=estado,
        url=url_sem_segredo(url),
        versao=_curto(versao, 64),
        detalhe=detalhe_curto,
        ultimo_pulso_em=agora.isoformat(),
    )
    canal.status = status
    if status == "ok":
        canal.ultimo_ok_em = agora
    if erro:
        canal.ultimo_erro_em = agora
        canal.ultimo_erro = erro[:_TEXTO_OPERACAO_MAX]
    await session.flush()
    logger.info(
        "atendimento_robo_pulso",
        perfil_id=perfil_id,
        plataforma=plataforma,
        estado=estado,
        status=status,
        conversa_aberta=bool(aberta),
    )
    return canal


# ── Eventos ───────────────────────────────────────────────────────────────


def _momento():
    return func.coalesce(AtendimentoMensagem.enviada_em, AtendimentoMensagem.created_at)


async def _existe(session: AsyncSession, conversa_id, externo_id: str) -> bool:
    return (
        await session.scalar(
            select(AtendimentoMensagem.id)
            .where(
                AtendimentoMensagem.conversa_id == conversa_id,
                AtendimentoMensagem.externo_id == externo_id,
            )
            .limit(1)
        )
    ) is not None


def _payload(m: MensagemLida) -> dict:
    return {**m.payload, "robo_plataforma": True} if m.robo_plataforma else dict(m.payload)


def _mesmo_texto(a: str | None, b: str | None) -> bool:
    """O mesmo texto para o leitor (sem acento, pontuação, caixa); só emoji/sinal
    compara como veio (normalizado, "👍" e "!!!" ficariam vazios)."""
    na, nb = gravar.normalizar_para_comparar(a), gravar.normalizar_para_comparar(b)
    if na or nb:
        return na == nb
    ca, cb = " ".join((a or "").split()), " ".join((b or "").split())
    return bool(ca) and ca == cb


def _mesmo_autor(da_previa: str, outro: str) -> bool:
    # Prévia de autor desconhecido (sistema) casa com qualquer um: a lista
    # não disse quem escreveu, a mensagem de verdade diz.
    return da_previa in (outro, AUTOR_SISTEMA)


async def _adotada(session: AsyncSession, conversa_id, previa_id: str) -> bool:
    """Uma mensagem de verdade já ADOTOU a prévia com este id (e o id sumiu)?"""
    return (
        await session.scalar(
            select(AtendimentoMensagem.id)
            .where(
                AtendimentoMensagem.conversa_id == conversa_id,
                AtendimentoMensagem.payload["previa_adotada"].astext == previa_id,
            )
            .limit(1)
        )
    ) is not None


async def _previa_do_mensagem_id(session: AsyncSession, conversa_id, mensagem_id: str) -> bool:
    return (
        await session.scalar(
            select(AtendimentoMensagem.id)
            .where(
                AtendimentoMensagem.conversa_id == conversa_id,
                AtendimentoMensagem.payload["mensagem_id"].astext == mensagem_id,
            )
            .limit(1)
        )
    ) is not None


async def _ja_tem_o_id(session: AsyncSession, conversa_id, previa_id: str) -> bool:
    return await _existe(session, conversa_id, previa_id) or await _adotada(
        session, conversa_id, previa_id
    )


async def _recentes(session: AsyncSession, conversa_id, n: int) -> list[AtendimentoMensagem]:
    return list(
        (
            await session.execute(
                select(AtendimentoMensagem)
                .where(AtendimentoMensagem.conversa_id == conversa_id)
                .order_by(_momento().desc(), AtendimentoMensagem.created_at.desc())
                .limit(n)
            )
        )
        .scalars()
        .all()
    )


async def _previa_repetida(
    session: AsyncSession, conversa: AtendimentoConversa, m: MensagemLida
) -> bool:
    """A prévia da lista é uma mensagem que a conversa JÁ tem?

    A lista repete a última mensagem a cada atualização, e a mensagem de
    verdade pode ter chegado antes (empurrão/sync). Mas o texto igual NÃO
    basta: "Hello", "Ok", "[Image]" se repetem, e a pergunta nova que só a
    lista trouxe (robô fora) sumiria da fila (revisão 30/09). Então:

      • com o id da última mensagem (a lista real traz): SÓ o id decide;
      • com a hora da mensagem: mesmo texto, mesmo autor e a mesma hora;
      • só com a hora em que a lista foi servida: se a lista não é mais nova
        que o que já está gravado, é uma das mensagens de lá (mesmo texto e
        autor); se é mais nova, a prévia É a última mensagem da conversa.
    """
    dados = m.payload or {}
    if dados.get("nao_vista"):
        return await _nao_vista_repetida(session, conversa, m)
    mensagem_id = dados.get("mensagem_id")
    if isinstance(mensagem_id, str) and mensagem_id:
        return (
            await _ja_tem_o_id(session, conversa.id, m.externo_id)
            or await _existe(session, conversa.id, mensagem_id)
            # Prévia gravada com outro formato de id (antes de 30/09 era o
            # hash do texto), mas do mesmo id de mensagem: a mesma.
            or await _previa_do_mensagem_id(session, conversa.id, mensagem_id)
        )
    if dados.get("hora") == "mensagem" and m.enviada_em is not None:
        if await _ja_tem_o_id(session, conversa.id, m.externo_id):
            return True
        return any(
            _mesmo_autor(m.autor, r.autor)
            and _mesmo_texto(r.texto, m.texto)
            and r.enviada_em is not None
            and abs(gravar._utc(r.enviada_em) - m.enviada_em) <= _FOLGA_HORA_PREVIA
            for r in await _recentes(session, conversa.id, _ULTIMAS_PARA_PREVIA)
        )
    recentes = await _recentes(session, conversa.id, _ULTIMAS_PARA_PREVIA)
    if not recentes:
        return False
    servida_em = m.enviada_em or datetime.now(UTC)
    ultima = recentes[0]
    if servida_em <= gravar._quando(ultima) + _FOLGA_HORA_PREVIA:
        # A lista não é mais nova que o que já temos (servida antes da
        # resposta que o sync trouxe, por exemplo): é uma daquelas mensagens.
        return any(
            _mesmo_autor(m.autor, r.autor) and _mesmo_texto(r.texto, m.texto) for r in recentes
        )
    return _mesmo_autor(m.autor, ultima.autor) and _mesmo_texto(ultima.texto, m.texto)


async def _nao_vista_repetida(
    session: AsyncSession, conversa: AtendimentoConversa, m: MensagemLida
) -> bool:
    """O marcador da pergunta não vista sobra quando a conversa já está com o
    comprador por último: a pergunta já está representada (e, se a pessoa
    fechou a conversa assim, o marcador não a reabre)."""
    if await _ja_tem_o_id(session, conversa.id, m.externo_id):
        return True
    await gravar.travar_linha(session, conversa)  # relê: a leva pode ter gravado agora
    do_cliente = gravar._utc(conversa.ultima_do_cliente_em)
    da_loja = gravar._utc(conversa.ultima_da_loja_em)
    return do_cliente is not None and (da_loja is None or do_cliente > da_loja)


async def _id_da_previa_nova(
    session: AsyncSession, conversa: AtendimentoConversa, m: MensagemLida
) -> str:
    """O id para a prévia que é mensagem NOVA.

    O id sem hora (hash só do texto) pode já existir: "Hello" de novo depois
    da resposta da loja. Aí ele ganha o id da mensagem que era a última —
    estável: relida, a lista cai no "é a última mensagem" e nem chega aqui.
    """
    if not await _ja_tem_o_id(session, conversa.id, m.externo_id):
        return m.externo_id
    ultima = await _recentes(session, conversa.id, 1)
    sufixo = hashlib.sha256(str(ultima[0].id if ultima else uuid4()).encode()).hexdigest()[:8]
    return f"{m.externo_id}:{sufixo}"


def _adota_pelo_texto(p: AtendimentoMensagem, m: MensagemLida) -> bool:
    if (p.payload or {}).get("nao_vista") or not _mesmo_autor(p.autor, m.autor):
        return False
    if not _mesmo_texto(p.texto, m.texto):
        return False
    # Prévia com a hora da mensagem: a mesma mensagem tem a mesma hora
    # ("Hello" de hoje não adota a prévia do "Hello" da semana passada).
    if (p.payload or {}).get("hora") == "mensagem" and p.enviada_em and m.enviada_em:
        return abs(gravar._utc(p.enviada_em) - m.enviada_em) <= _FOLGA_HORA_PREVIA
    return True


def _adota_o_marcador(
    conversa: AtendimentoConversa, p: AtendimentoMensagem, m: MensagemLida
) -> bool:
    """A pergunta de verdade que o marcador "não vista" representava: do
    comprador, depois da última resposta da loja e não depois do marcador
    (a resposta automática veio logo em seguida a ela)."""
    if m.autor != AUTOR_CLIENTE or not (p.payload or {}).get("nao_vista"):
        return False
    if m.enviada_em is None or p.enviada_em is None:
        return True
    if m.enviada_em > gravar._utc(p.enviada_em) + _FOLGA_HORA_PREVIA:
        return False
    da_loja = gravar._utc(conversa.ultima_da_loja_em)
    return da_loja is None or m.enviada_em > da_loja


async def _adotar_previa(
    session: AsyncSession, conversa: AtendimentoConversa, m: MensagemLida
) -> bool:
    """A mensagem de verdade chegou: a prévia (ou o marcador) ganha o id dela.

    Sem isto a conversa mostraria a mesma frase duas vezes (a prévia da lista
    e a mensagem do sync). Pelo id da última mensagem primeiro (a lista real
    traz), pelo texto quando a prévia não tem id, e o marcador da pergunta
    não vista pela pergunta do comprador. Trava a CONVERSA antes (a ordem de
    `gravar`) e confere de novo depois da trava: outro pedido do robô pode
    ter gravado. Grava pela MESMA limpeza do `gravar_mensagem` (NUL, tamanho
    do id) — a mensagem envenenada derrubava a leva aqui (revisão 30/09).
    """
    previas = (
        (
            await session.execute(
                select(AtendimentoMensagem)
                .where(
                    AtendimentoMensagem.conversa_id == conversa.id,
                    AtendimentoMensagem.externo_id.like(f"{PREFIXO_PREVIA}%"),
                )
                .order_by(_momento().desc())
                .limit(_PREVIAS_PARA_ADOTAR)
            )
        )
        .scalars()
        .all()
    )
    previa = (
        next((p for p in previas if (p.payload or {}).get("mensagem_id") == m.externo_id), None)
        or next((p for p in previas if _adota_pelo_texto(p, m)), None)
        or next((p for p in previas if _adota_o_marcador(conversa, p, m)), None)
    )
    if previa is None:
        return False
    await gravar.travar_linha(session, conversa)
    if await _existe(session, conversa.id, m.externo_id):
        return True
    await session.refresh(previa)
    if not (previa.externo_id or "").startswith(PREFIXO_PREVIA):
        return False  # outro pedido adotou esta prévia para outra mensagem
    era_marcador = bool((previa.payload or {}).get("nao_vista"))
    externo_id, texto, anexos, payload = gravar.sanear_mensagem(
        externo_id=m.externo_id, texto=m.texto, anexos=m.anexos, payload=_payload(m)
    )
    # O id que a prévia tinha fica no payload: a lista relida (com o mesmo
    # id de prévia) sabe que esta mensagem já está aqui.
    payload["previa_adotada"] = previa.externo_id
    previa.externo_id = externo_id
    previa.autor = m.autor
    previa.origem = _ORIGEM_DO_AUTOR.get(m.autor, ORIGEM_SISTEMA)
    previa.status = MSG_ENVIADA if m.autor == AUTOR_LOJA else MSG_RECEBIDA
    previa.tipo = m.tipo
    previa.anexos = anexos
    previa.payload = payload
    if texto or era_marcador:
        # O texto do marcador é nosso: a pergunta de verdade o substitui
        # sempre (mesmo foto, sem texto).
        previa.texto = texto
    if m.enviada_em is not None:
        previa.enviada_em = m.enviada_em
    await session.flush()
    # A hora e o autor podem ter mudado: a fila se refaz do banco.
    await gravar.recalcular_conversa(session, conversa)
    logger.info(
        "atendimento_robo_previa_adotada",
        conversa_id=str(conversa.id),
        mensagem_id=str(previa.id),
        marcador=era_marcador,
    )
    return True


async def _gravar_mensagem(
    session: AsyncSession, conversa: AtendimentoConversa, m: MensagemLida
) -> bool:
    """Uma mensagem lida → banco; True = linha nova."""
    externo_id = m.externo_id
    if m.previa:
        if await _previa_repetida(session, conversa, m):
            return False
        externo_id = await _id_da_previa_nova(session, conversa, m)
    else:
        if await _existe(session, conversa.id, m.externo_id):
            return False
        if await _adotar_previa(session, conversa, m):
            return False
    _mensagem, nova = await gravar.gravar_mensagem(
        session,
        conversa,
        externo_id=externo_id,
        autor=m.autor,
        texto=m.texto,
        enviada_em=m.enviada_em,
        tipo=m.tipo,
        anexos=m.anexos,
        payload=_payload(m),
    )
    return nova


async def _gravar_conversa(
    session: AsyncSession, canal: AtendimentoCanal, loja: str, conv: ConversaLida
) -> int:
    """Uma conversa lida (e as mensagens dela) → banco; devolve as mensagens novas."""
    conversa, _criada = await gravar.upsert_conversa(
        session,
        canal=canal,
        integration=None,
        plataforma=canal.plataforma,
        canal_nome=canal.canal,
        externo_id=conv.externo_id,
        conta=loja,
        comprador_id=conv.comprador_id,
        comprador_nome=conv.comprador_nome,
        comprador_avatar=conv.comprador_avatar,
        pedido_marketplace=conv.pedido,
        nao_lidas=conv.nao_lidas,
    )
    # `dados["robo"]` mesclado CHAVE A CHAVE: a lista traz os grupos, o
    # empurrão traz o prazo — um não apaga o outro.
    atual = (conversa.dados or {}).get("robo")
    atual = atual if isinstance(atual, dict) else {}
    novo = {**atual, **conv.dados, "perfil_id": canal.robo_perfil_id}
    if novo != atual:
        conversa.dados = {**(conversa.dados or {}), "robo": gravar.sem_nul(novo)}
    novas = 0
    # As de verdade antes das prévias (a prévia confere o que já está lá), e
    # na ordem da plataforma.
    piso = datetime.min.replace(tzinfo=UTC)
    for m in sorted(conv.mensagens.values(), key=lambda x: (x.previa, x.enviada_em or piso)):
        novas += int(await _gravar_mensagem(session, conversa, m))
    await session.flush()
    return novas


async def _registrar_leva(session: AsyncSession, canal_id, leitura: Leitura) -> None:
    """Os sinais da loja que a leva trouxe (contadores, erro da plataforma, o que não se leu)."""
    canal = await session.get(AtendimentoCanal, canal_id, populate_existing=True)
    if canal is None:
        return
    await gravar.travar_linha(session, canal)
    agora = datetime.now(UTC)
    campos: dict[str, Any] = {"ultimo_evento_em": agora.isoformat()}
    if "precisa_responder" in leitura.sinais:
        campos["precisa_responder"] = leitura.sinais["precisa_responder"]
    if "erro_plataforma" in leitura.sinais:
        # Só o código (54001 = captcha da Temu; RGV587 = antirrobô do AliExpress).
        campos["erro_plataforma"] = leitura.sinais["erro_plataforma"]
        campos["erro_plataforma_em"] = agora.isoformat()
    if leitura.desconhecidos:
        # A última leva com algo que o leitor não conhece: NOMES de campo, o
        # material para ajustar o leitor quando a página mudar.
        campos["desconhecidos"] = dict(leitura.desconhecidos.most_common(20))
        campos["desconhecidos_em"] = agora.isoformat()
    _com_robo(canal, **campos)
    if "nao_lidas_loja" in leitura.sinais:
        canal.nao_lidas_plataforma = leitura.sinais["nao_lidas_loja"]
    await session.flush()


async def receber_eventos(
    session: AsyncSession,
    *,
    perfil_id: str,
    plataforma: str,
    loja: str,
    eventos: list[Evento],
) -> ResultadoRobo:
    """Uma leva de eventos do robô → conversas e mensagens na caixa.

    Commita POR CONVERSA (`gravar.fim_do_item`), como a rodada do sync: a
    trava de cada conversa dura só a gravação dela, e a tela ("Fechar",
    trocar o modo) não espera a leva inteira. Uma conversa que falha é
    desfeita sozinha e contada em `erros` (as outras entram). Só levanta
    (500: o robô manda de novo, a gravação é idempotente) quando NENHUMA
    entrou E a falha é do banco que passa sozinha (conexão, trava, deadlock).
    Erro de DADO (NUL, valor que não cabe, UNIQUE) é do formato: reenviar
    daria o mesmo erro para sempre, com a fila da loja presa atrás dele no
    robô e o pulso dizendo "lendo" (revisão 30/09) — vai para `erros`.
    """
    canal = await canal_do_robo(session, perfil_id=perfil_id, plataforma=plataforma, loja=loja)
    canal_id = canal.id
    await session.commit()
    leitura = _leitor(plataforma).ler(eventos)
    resultado = ResultadoRobo(ignorados=leitura.ignorados)
    loja_nome = _curto(loja, 120) or nome_da_loja(canal)
    passageiro: BaseException | None = None
    for conv in leitura.conversas.values():
        try:
            canal = await session.get(AtendimentoCanal, canal_id)
            if canal is None:
                break
            resultado.gravadas += await _gravar_conversa(session, canal, loja_nome, conv)
            await gravar.fim_do_item(session)
            resultado.conversas += 1
        except Exception as e:  # noqa: BLE001 — uma conversa não derruba a leva
            await session.rollback()
            resultado.erros += 1
            if erro_passageiro(e):
                passageiro = e
            logger.warning(
                "atendimento_robo_conversa_falhou",
                perfil_id=perfil_id,
                plataforma=plataforma,
                erro=type(e).__name__,
                passageiro=erro_passageiro(e),
            )
    if passageiro is not None and resultado.conversas == 0:
        raise passageiro
    await _registrar_leva(session, canal_id, leitura)
    await session.commit()
    logger.info(
        "atendimento_robo_eventos",
        perfil_id=perfil_id,
        plataforma=plataforma,
        eventos=len(eventos),
        usados=leitura.usados,
        controle=leitura.controle,
        ignorados=leitura.ignorados,
        conversas=resultado.conversas,
        gravadas=resultado.gravadas,
        erros=resultado.erros,
        reconhecidos=dict(leitura.reconhecidos),
        desconhecidos=dict(leitura.desconhecidos.most_common(20)),
    )
    return resultado
