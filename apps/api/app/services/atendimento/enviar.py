"""O caminho ÚNICO de saída de uma resposta do atendimento.

Pessoa pela tela e IA no automático saem por aqui — e por nenhum outro lugar.
Mensagem para comprador não se desenvia; por isso a ordem abaixo é rígida:

  0. A CONVERSA É TRAVADA (FOR NO KEY UPDATE) e RELIDA do banco antes de
     tudo: duas abas, duas pessoas ou duas rodadas da IA na mesma conversa
     passam aqui uma de cada vez, e cada uma decide sobre o estado de AGORA
     (não sobre o objeto que ela leu minutos antes). A pessoa espera no
     máximo 5 s pela trava (`conversa_ocupada`: a rodada do sync está
     gravando esta conversa — tente de novo).

  1. TRAVAS (`EnvioRecusado`, nada vai à plataforma):
       somente_leitura     — plataforma sem adaptador de envio (Instagram) ou
                             lida pelo robô do Mac mini (Temu, AliExpress:
                             "responda no Seller Center");
       simulador_em_producao — o simulador (só local) ligado em produção;
       envio_desligado     — `settings.atendimento_envio_ativo` desligado;
       conversa_bloqueada  — a plataforma não deixa mais responder (ou a
                             janela de resposta dela fechou);
       sem_integracao      — conversa sem canal/loja ativa por trás (Amazon
                             sem conta identificada: escolher a conta);
       canal_em_observacao — o canal está em `observar` (o Duoke responde);
       auto_desligado      — origem IA sem o automático ligado no canal;
       nao_aguarda         — origem IA numa conversa que não espera mais a
                             IA (pausada, fechada, "não precisa de resposta",
                             ou a loja já respondeu depois da pergunta);
       texto_invalido      — o validador reprovou (detail = motivos);
       envio_repetido      — a MESMA resposta saiu nesta conversa há menos de
                             2 min (clique duplo, aba duplicada);
       conversa_mudou      — alguém respondeu depois da última mensagem que a
                             pessoa viu (a tela manda `ultima_vista_id`;
                             `confirmar` envia mesmo assim);
       envio_em_andamento  — já há uma resposta EM VOO nesta conversa.

  2. EM VOO: a mensagem nasce `enviando` e é COMMITADA antes de falar com a
     plataforma. O índice parcial `uq_atendimento_envio_em_voo` é quem barra
     duas abas (ou a pessoa e o automático) apertando "Enviar" juntas: só um
     INSERT passa, o outro vira `envio_em_andamento`. Commitar ANTES também
     deixa a linha visível para o sync, que a ADOTA se a plataforma a
     devolver antes de nós terminarmos (gravar.gravar_mensagem).

  3. A PLATAFORMA responde e a linha vira:
       enviada  — saiu (com o id da plataforma quando ela devolve);
       revisar  — AMBÍGUO (timeout, erro sem código): pode ter saído. NUNCA
                  se retenta em cima — a tela mostra e a pessoa confere;
       falhou   — não saiu (o cliente continua esperando na fila);
     e, com `bloqueio`, a conversa vira `bloqueada` com o motivo.

  4. O RASCUNHO da IA ganha o que a pessoa fez com ele (avaliação), que é o
     material do aprendizado: enviou igual, editou, ou escreveu do zero.

`settings.atendimento_simulador` (SÓ LOCAL) troca o passo 3 por um sucesso
fingido (`sim:<uuid>`): é como a tela se testa de ponta a ponta sem escrever
para comprador nenhum. Em produção ele RECUSA o envio: "Enviar" que diz que
saiu e não saiu é pior que "não saiu". `settings.atendimento_simulador_exceto`
(também só local) lista plataformas que escapam do simulador e saem de
verdade — é o teste de responder pela tela local a um e-mail real da Amazon
com as outras lojas ainda no simulador.

FOTO (01/10/2026): `enviar_foto` é o mesmo caminho para UMA imagem (Shopee,
TikTok e pós-venda do ML; `foto.py`): mesmas travas, mesma linha em voo,
mesma régua de resultado. O upload para a plataforma só acontece aqui —
com o envio desligado, nada sobe.

Texto de comprador (e o nosso) nunca vai para o log — só ids e estados.
"""

from __future__ import annotations

import asyncio
import importlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import ModuleType
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import func, select, update
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import is_unique_violation
from app.models import (
    AtendimentoAvaliacao,
    AtendimentoCanal,
    AtendimentoConversa,
    AtendimentoMensagem,
    AtendimentoRascunho,
    Integration,
    User,
)
from app.services.atendimento import clientes, gravar
from app.services.atendimento.constantes import (
    AUTOR_LOJA,
    AVALIACAO_OBSERVOU,
    CONVERSA_BLOQUEADA,
    CONVERSA_FECHADA,
    MODO_AUTO,
    MODOS_QUE_ENVIAM,
    MSG_ENVIADA,
    MSG_ENVIANDO,
    MSG_FALHOU,
    MSG_REVISAR,
    ORIGEM_EXTERNO,
    ORIGEM_HUMANO,
    ORIGEM_IA,
    ORIGENS_DAVINCI,
    PLATAFORMAS_ROBO,
    PLATAFORMAS_SEM_AUTO,
    RASCUNHO_EDITADO,
    RASCUNHO_ENVIADO,
    RASCUNHO_PENDENTE,
    RASCUNHO_SUBSTITUIDO,
    ResultadoEnvio,
    reclamacao_aberta,
)

logger = structlog.get_logger()

_SP = ZoneInfo("America/Sao_Paulo")

# Import TARDIO por nome de módulo: o envio não carrega os adaptadores
# à toa, e o teste troca um adaptador com monkeypatch neste dicionário.
ADAPTADORES: dict[str, str] = {
    "shopee": "app.services.atendimento.shopee",
    "ml": "app.services.atendimento.ml",
    "tiktok": "app.services.atendimento.tiktok",
    "amazon": "app.services.atendimento.amazon_email",
    # Pergunta, chat e SAC pela API do vendedor. Toda resposta passa pela
    # MODERAÇÃO da Magalu: "enviada" aqui é "a Magalu recebeu" (o payload do
    # envio diz `moderacao`), e o validador já barra o que ela barraria.
    "magalu": "app.services.atendimento.magalu",
}

# ── Códigos de recusa (estáveis: a tela traduz, o router devolve 409/422) ──
RECUSA_SOMENTE_LEITURA = "somente_leitura"
RECUSA_ENVIO_DESLIGADO = "envio_desligado"
RECUSA_CONVERSA_BLOQUEADA = "conversa_bloqueada"
RECUSA_SEM_INTEGRACAO = "sem_integracao"
RECUSA_CANAL_EM_OBSERVACAO = "canal_em_observacao"
RECUSA_AUTO_DESLIGADO = "auto_desligado"
RECUSA_TEXTO_INVALIDO = "texto_invalido"
RECUSA_ENVIO_EM_ANDAMENTO = "envio_em_andamento"
RECUSA_SIMULADOR_EM_PRODUCAO = "simulador_em_producao"
RECUSA_NAO_AGUARDA = "nao_aguarda"
RECUSA_ENVIO_REPETIDO = "envio_repetido"
RECUSA_CONVERSA_MUDOU = "conversa_mudou"
RECUSA_CONVERSA_OCUPADA = "conversa_ocupada"
# Foto na resposta (01/10/2026, `enviar_foto` e `foto.py`):
#   foto_nao_suportada — a plataforma/caixa não aceita foto pelo DaVinci;
#   foto_invalida      — o arquivo não serve (tipo, tamanho) ou a legenda
#                        não cabe nesta plataforma (Shopee/TikTok: foto sozinha).
RECUSA_FOTO_NAO_SUPORTADA = "foto_nao_suportada"
RECUSA_FOTO_INVALIDA = "foto_invalida"

# Temu/AliExpress (lidas pelo robô do Mac mini): nada sai pelo DaVinci.
_NOME_SELLER_CENTER = {"temu": "Temu", "aliexpress": "AliExpress"}


def motivo_seller_center(plataforma: str) -> str:
    """O porquê de não enviar numa loja do robô — o texto que a tela mostra."""
    nome = _NOME_SELLER_CENTER.get(plataforma, plataforma)
    return (
        f"{nome}: responda no Seller Center — aqui o DaVinci só lê (pelo robô do "
        "Mac mini) e mostra a sugestão da IA para copiar."
    )


# Amazon cujo e-mail não disse a conta: a pessoa escolhe a conta na tela
# (PATCH /conversas/{id} com `integration_id`), e aí dá para responder.
MOTIVO_AMAZON_SEM_CONTA = "Escolha de qual conta Amazon é esta conversa."

# Similaridade (difflib, 0–1) a partir da qual "a pessoa enviou a sugestão
# como veio". Abaixo disso ela EDITOU — e a versão dela é a que ensina.
LIMIAR_ENVIOU_IGUAL = 0.97

# Teto de espera pela plataforma. Os clientes têm timeout próprio; este é o
# cinto de segurança para um adaptador que pendure. Estourou = ambíguo.
TEMPO_MAXIMO_ENVIO_S = 90

# Linha `enviando` mais velha que isto é de um processo que morreu no meio
# (deploy, kill). Vira `revisar` — pode ter saído — e solta a trava de
# "uma em voo por conversa", senão a conversa ficaria sem resposta para sempre.
ENVIO_PRESO = timedelta(minutes=10)

# A mesma resposta de novo nesta janela é clique duplo (ou aba duplicada),
# não intenção: o comprador receberia a frase duas vezes.
JANELA_REPETIDO = timedelta(minutes=2)
_STATUS_QUE_SAIRAM = (MSG_ENVIANDO, MSG_ENVIADA, MSG_REVISAR)

# Erros de banco no passo 3 que se resolvem tentando de novo: deadlock,
# serialização e o UNIQUE do id da plataforma (o sync gravou a mesma
# mensagem no meio). A mensagem JÁ SAIU — nunca devolver 500 por isso.
_SQLSTATE_TRANSITORIO = ("40P01", "40001", "23505")
TENTATIVAS_RESULTADO = 3


class EnvioRecusado(Exception):  # noqa: N818 — nome do contrato (spec seção 5)
    """O envio NÃO aconteceu, por uma trava nossa (nada foi à plataforma).

    `code` é estável (a tela traduz); `detail` é o texto para a pessoa ou,
    em `texto_invalido`, a lista de motivos do validador.
    """

    def __init__(self, code: str, detail: str | list[str] = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(code)


def adaptador(plataforma: str) -> ModuleType:
    """O módulo adaptador da plataforma (import tardio pelo `ADAPTADORES`)."""
    return importlib.import_module(ADAPTADORES[plataforma])


@dataclass
class _Destino:
    """Por onde a resposta sai: o canal (modo) e a loja (credenciais)."""

    canal: AtendimentoCanal
    integration: Integration


# ── Travas ────────────────────────────────────────────────────────────────


async def _destino(
    session: AsyncSession, conversa: AtendimentoConversa, *, origem: str
) -> _Destino:
    """Confere as travas que não dependem do texto; levanta `EnvioRecusado`.

    A ordem é a da explicação para a pessoa: primeiro o que é do sistema
    (somente leitura, envio desligado), depois o que é da conversa
    (bloqueada), depois o que é da loja (sem integração, em observação).

    Canal e loja são RELIDOS do banco (`populate_existing`): a IA roda
    minutos com a mesma sessão, e a loja posta em `observar` (ou a categoria
    tirada do automático) no meio disso tem que valer já.
    """
    if conversa.plataforma in PLATAFORMAS_ROBO:
        # Temu/AliExpress: o robô do Mac mini só LÊ o Seller Center (enviar
        # marcaria a conversa como lida e desligaria o robô da Temu). A
        # sugestão da IA fica na tela para copiar.
        raise EnvioRecusado(RECUSA_SOMENTE_LEITURA, motivo_seller_center(conversa.plataforma))
    if conversa.plataforma not in ADAPTADORES:
        raise EnvioRecusado(
            RECUSA_SOMENTE_LEITURA,
            "Esta conversa é só de leitura aqui: responda pela própria plataforma.",
        )
    settings = get_settings()
    if settings.atendimento_simulador and settings.is_prod:
        # O simulador finge que saiu. Em produção isso é a conversa sair da
        # fila com o comprador sem resposta — recusa, e alto no log.
        logger.error("atendimento_simulador_em_producao", conversa_id=str(conversa.id))
        raise EnvioRecusado(
            RECUSA_SIMULADOR_EM_PRODUCAO,
            "O simulador de envio (só para teste local) está ligado em produção: "
            "nada foi enviado. Desligue ATENDIMENTO_SIMULADOR.",
        )
    if not settings.atendimento_envio_ativo:
        raise EnvioRecusado(
            RECUSA_ENVIO_DESLIGADO,
            "O envio pelo DaVinci está desligado (ATENDIMENTO_ENVIO_ATIVO).",
        )
    if conversa.situacao == CONVERSA_BLOQUEADA:
        raise EnvioRecusado(
            RECUSA_CONVERSA_BLOQUEADA,
            conversa.bloqueio_motivo
            or "A plataforma não deixa mais responder nesta conversa.",
        )
    limite = conversa.pode_enviar_ate
    if limite is not None and limite.tzinfo is None:
        limite = limite.replace(tzinfo=UTC)
    if limite is not None and datetime.now(UTC) > limite:
        raise EnvioRecusado(
            RECUSA_CONVERSA_BLOQUEADA,
            "A janela de resposta da plataforma para esta conversa já fechou.",
        )
    if conversa.plataforma == "amazon" and conversa.integration_id is None:
        raise EnvioRecusado(RECUSA_SEM_INTEGRACAO, MOTIVO_AMAZON_SEM_CONTA)
    canal = (
        await session.get(AtendimentoCanal, conversa.canal_id, populate_existing=True)
        if conversa.canal_id is not None
        else None
    )
    integration = (
        await session.get(Integration, canal.integration_id, populate_existing=True)
        if canal is not None
        else None
    )
    if canal is None or integration is None or integration.archived_at is not None:
        raise EnvioRecusado(
            RECUSA_SEM_INTEGRACAO,
            "A loja desta conversa não está mais conectada ao DaVinci.",
        )
    if canal.modo not in MODOS_QUE_ENVIAM:
        raise EnvioRecusado(
            RECUSA_CANAL_EM_OBSERVACAO,
            "Esta loja está em modo observar: quem responde é o Duoke/Seller Center.",
        )
    if origem == ORIGEM_IA:
        if not settings.atendimento_auto_ativo or canal.modo != MODO_AUTO:
            # Cinto de segurança: quem decide o automático é a IA (ia.py), mas
            # resposta sem pessoa só sai com o automático ligado nos DOIS lugares.
            raise EnvioRecusado(
                RECUSA_AUTO_DESLIGADO,
                "Envio automático desligado para esta loja.",
            )
        motivo = _motivo_ia_sem_vez(conversa)
        if motivo:
            raise EnvioRecusado(RECUSA_NAO_AGUARDA, motivo)
    return _Destino(canal=canal, integration=integration)


def _motivo_ia_sem_vez(conversa: AtendimentoConversa) -> str | None:
    """Por que a IA não responde ESTA conversa agora (None = pode).

    A pessoa pode escrever de novo numa conversa respondida ou fechada — ela
    sabe o que faz. A IA não: a tela pode ter pausado a IA, fechado a conversa
    ou marcado "não precisa de resposta" enquanto o modelo escrevia, e a loja
    (inclusive a própria IA, numa rodada paralela) pode já ter respondido.
    """
    if conversa.ia_pausada:
        return "A IA está pausada nesta conversa."
    if conversa.situacao == CONVERSA_FECHADA:
        return "A conversa foi fechada."
    if conversa.sem_resposta_necessaria:
        return "A conversa foi marcada como não precisa de resposta."
    if not conversa.aguardando_resposta:
        return "A conversa já foi respondida."
    if reclamacao_aberta(conversa.dados):
        return "Há reclamação/mediação aberta no ML: quem responde é pessoa."
    if conversa.plataforma in PLATAFORMAS_ROBO:
        return motivo_seller_center(conversa.plataforma)
    if conversa.plataforma in PLATAFORMAS_SEM_AUTO:
        return (
            "Na Amazon a IA não responde sozinha: a resposta dada no Seller Central"
            " não chega ao DaVinci."
        )
    return None


def _momento_col():
    return func.coalesce(AtendimentoMensagem.enviada_em, AtendimentoMensagem.created_at)


async def _tem_envio_em_voo(session: AsyncSession, conversa_id: UUID) -> bool:
    """Há resposta `enviando` RECENTE? A presa (processo morto) não trava a tela.

    A linha `enviando` mais velha que `ENVIO_PRESO` é de um envio que morreu
    no meio; o próprio envio a aposenta (vira `revisar`) antes de gravar a
    nova. Sem este corte, com a leitura desligada ninguém a aposentaria, e a
    tela travaria a caixa de resposta para sempre.
    """
    return (
        await session.scalar(
            select(AtendimentoMensagem.id)
            .where(
                AtendimentoMensagem.conversa_id == conversa_id,
                AtendimentoMensagem.status == MSG_ENVIANDO,
                AtendimentoMensagem.created_at >= datetime.now(UTC) - ENVIO_PRESO,
            )
            .limit(1)
        )
    ) is not None


async def motivo_para_nao_enviar(
    session: AsyncSession, conversa: AtendimentoConversa, *, origem: str = ORIGEM_HUMANO
) -> EnvioRecusado | None:
    """A recusa que o envio daria AGORA (sem olhar o texto); None = pode enviar.

    É o que a tela mostra na faixa acima da caixa de resposta — as MESMAS
    travas do envio, para a faixa nunca dizer "pode" e o botão dizer "não".
    """
    try:
        await _destino(session, conversa, origem=origem)
    except EnvioRecusado as recusa:
        return recusa
    if await _tem_envio_em_voo(session, conversa.id):
        return EnvioRecusado(
            RECUSA_ENVIO_EM_ANDAMENTO, "Há uma resposta sendo enviada nesta conversa."
        )
    return None


async def travar_conversa(session: AsyncSession, conversa: AtendimentoConversa) -> None:
    """Trava e relê a conversa, esperando no máximo 5 s; ocupada → `conversa_ocupada`.

    A rodada do sync segura a conversa enquanto grava (e fala com a API da
    loja): em vez de a tela pendurar, a pessoa ouve "tente de novo".
    """
    if not await gravar.travar_linha(session, conversa, espera=gravar.ESPERA_TRAVA_TELA):
        raise EnvioRecusado(
            RECUSA_CONVERSA_OCUPADA,
            "A leitura da loja está gravando esta conversa agora. Tente de novo em "
            "alguns segundos.",
        )


async def aposentar_envios_presos(
    session: AsyncSession, *, conversa_id: UUID | None = None
) -> int:
    """`enviando` esquecido por processo morto vira `revisar`; devolve quantos.

    Sem isto, um deploy no meio de um envio deixaria a conversa com uma linha
    em voo para sempre — e o índice de "uma em voo" recusaria toda resposta
    seguinte. `revisar`, e não `falhou`: a plataforma pode ter recebido.
    Nunca commita.
    """
    q = (
        update(AtendimentoMensagem)
        .where(
            AtendimentoMensagem.status == MSG_ENVIANDO,
            AtendimentoMensagem.created_at < datetime.now(UTC) - ENVIO_PRESO,
        )
        .values(status=MSG_REVISAR, erro="envio_interrompido")
        .execution_options(synchronize_session=False)
    )
    if conversa_id is not None:
        q = q.where(AtendimentoMensagem.conversa_id == conversa_id)
    resultado = await session.execute(q)
    presos = int(resultado.rowcount or 0)
    if presos:
        logger.warning(
            "atendimento_envio_preso_revisar",
            conversa_id=str(conversa_id) if conversa_id else None,
            quantidade=presos,
        )
    return presos


# ── Texto ─────────────────────────────────────────────────────────────────


def _preparar_texto(texto: str | None, *, conversa: AtendimentoConversa, origem: str) -> str:
    """Normaliza e valida; levanta `texto_invalido` com os motivos.

    O validador é do lote do cérebro e é importado na hora: o router (que o
    app carrega sempre) não depende dele para subir.
    """
    from app.services.atendimento import validador

    normalizado = validador.normalizar(
        texto or "", plataforma=conversa.plataforma, canal=conversa.canal
    )
    motivos = list(
        validador.validar(
            normalizado, plataforma=conversa.plataforma, canal=conversa.canal, origem=origem
        )
    )
    if not (normalizado or "").strip() and not motivos:
        motivos = ["A resposta está vazia."]
    if motivos:
        raise EnvioRecusado(RECUSA_TEXTO_INVALIDO, motivos)
    return normalizado


# Mora no `gravar` (a resposta de fora também é comparada lá); o nome fica
# aqui porque é o que o router e a semente usam.
similaridade = gravar.similaridade


# ── Plataforma ────────────────────────────────────────────────────────────


def fora_do_simulador() -> list[str]:
    """As plataformas que escapam do simulador ligado (vazio sem o simulador).

    Um lugar só para o envio (`vai_para_o_simulador`) e a tela (`/resumo`):
    se cada um normalizasse a lista do seu jeito, a faixa diria "NÃO chega
    ao comprador" numa plataforma que manda de verdade.
    """
    s = get_settings()
    if not s.atendimento_simulador:
        return []
    return sorted(
        {p.strip().lower() for p in (s.atendimento_simulador_exceto or "").split(",") if p.strip()}
    )


def vai_para_o_simulador(plataforma: str) -> bool:
    """O envio desta plataforma cai no simulador (e não sai de verdade)?

    Com `atendimento_simulador`, tudo cai nele MENOS as plataformas de
    `atendimento_simulador_exceto`. A exceção existe para o teste local de
    responder pela tela a um e-mail REAL da Amazon (SMTP do Gmail) com a
    Shopee, o ML e o TikTok ainda fingindo — sem ela, ligar o envio real de
    uma loja exigiria desligar o simulador de todas.
    """
    if not get_settings().atendimento_simulador:
        return False
    return plataforma not in fora_do_simulador()


async def _chamar_plataforma(
    session: AsyncSession,
    conversa: AtendimentoConversa,
    integration: Integration,
    texto: str,
) -> ResultadoEnvio:
    """Fala com a plataforma. Nunca levanta: o que der errado vira resultado."""
    s = get_settings()
    if s.atendimento_simulador and s.is_prod:
        # Cinto (o `_destino` já recusa): em produção o simulador NUNCA
        # finge sucesso — e também não manda de verdade, nem para plataforma
        # da exceção, porque quem ligou o simulador achava que nada sairia.
        return ResultadoEnvio(ok=False, erro="simulador_em_producao")
    if vai_para_o_simulador(conversa.plataforma):
        return ResultadoEnvio(
            ok=True, externo_id=f"sim:{uuid4()}", payload={"simulador": True}
        )
    try:
        # Amazon responde por e-mail (SMTP pelos settings), não pela API da
        # loja: montar o cliente SP-API ali só daria um jeito a mais de falhar.
        cliente = (
            None
            if conversa.plataforma == "amazon"
            else await clientes.cliente_da_integracao(integration)
        )
    except Exception as e:  # noqa: BLE001
        # Nada saiu: a falha foi ao montar o cliente (credencial, token).
        return ResultadoEnvio(ok=False, erro=f"cliente_indisponivel: {type(e).__name__}")
    try:
        modulo = adaptador(conversa.plataforma)
        return await asyncio.wait_for(
            modulo.enviar_texto(session, conversa, integration, cliente, texto),
            timeout=TEMPO_MAXIMO_ENVIO_S,
        )
    except TimeoutError:
        return ResultadoEnvio(ok=False, ambiguo=True, erro="timeout")
    except Exception as e:  # noqa: BLE001
        # O adaptador promete não levantar; se levantou, não sabemos se a
        # plataforma recebeu — é exatamente o caso ambíguo.
        return ResultadoEnvio(ok=False, ambiguo=True, erro=f"erro_inesperado: {type(e).__name__}")


async def _externo_id_livre(
    session: AsyncSession, mensagem: AtendimentoMensagem, externo_id: str
) -> bool:
    """O id da plataforma ainda não está em OUTRA linha desta conversa?

    Só acontece se o sync gravou a nossa resposta como `externo` (texto que a
    plataforma devolveu diferente demais para ser adotado) no meio do envio.
    Gravar o mesmo id aqui estouraria o UNIQUE e deixaria a linha `enviando`.
    """
    dono = await session.scalar(
        select(AtendimentoMensagem.id).where(
            AtendimentoMensagem.conversa_id == mensagem.conversa_id,
            AtendimentoMensagem.externo_id == externo_id,
            AtendimentoMensagem.id != mensagem.id,
        )
    )
    return dono is None


async def _aplicar_resultado(
    session: AsyncSession,
    conversa: AtendimentoConversa,
    mensagem: AtendimentoMensagem,
    resultado: ResultadoEnvio,
) -> None:
    """Grava o que a plataforma disse na linha (e na conversa, se bloqueou)."""
    payload = {**(mensagem.payload or {}), "envio": dict(resultado.payload or {})}
    adotada = mensagem.status == MSG_ENVIADA and mensagem.externo_id is not None
    if adotada:
        # O sync já trouxe a nossa resposta de volta e a adotou: é a prova de
        # que saiu, valha o que valer o retorno (mesmo um timeout).
        mensagem.payload = payload
        return
    if resultado.ok:
        mensagem.status = MSG_ENVIADA
        mensagem.erro = None
        mensagem.enviada_em = datetime.now(UTC)
        externo_id = str(resultado.externo_id) if resultado.externo_id else None
        if externo_id and await _externo_id_livre(session, mensagem, externo_id):
            mensagem.externo_id = externo_id
        elif externo_id:
            logger.warning(
                "atendimento_envio_externo_id_repetido",
                conversa_id=str(conversa.id),
                mensagem_id=str(mensagem.id),
            )
    elif resultado.ambiguo:
        mensagem.status = MSG_REVISAR
        mensagem.erro = (resultado.erro or "envio_ambiguo")[:500]
    else:
        mensagem.status = MSG_FALHOU
        mensagem.erro = (resultado.erro or resultado.bloqueio or "envio_falhou")[:500]
    mensagem.payload = payload
    url = (resultado.payload or {}).get("imagem_url")
    if resultado.ok and mensagem.tipo == "imagem" and isinstance(url, str) and url.startswith("https://"):
        # A foto que saiu: o balão mostra a imagem pela URL da plataforma
        # (a nossa cópia nunca é guardada).
        primeiro = (mensagem.anexos or [{}])[0]
        mensagem.anexos = [{**(primeiro if isinstance(primeiro, dict) else {}), "url": url}]
    if resultado.bloqueio:
        conversa.situacao = CONVERSA_BLOQUEADA
        conversa.bloqueio_motivo = resultado.bloqueio[:500]


# ── Rascunho ──────────────────────────────────────────────────────────────


async def _rascunho_da_conversa(
    session: AsyncSession, conversa: AtendimentoConversa, rascunho_id: UUID | None
) -> AtendimentoRascunho | None:
    if rascunho_id is None:
        return None
    rascunho = await session.get(AtendimentoRascunho, rascunho_id)
    if rascunho is None or rascunho.conversa_id != conversa.id:
        # Rascunho de outra conversa (aba velha, id trocado): não se avalia
        # sugestão alheia com o texto desta.
        logger.warning(
            "atendimento_envio_rascunho_alheio",
            conversa_id=str(conversa.id),
            rascunho_id=str(rascunho_id),
        )
        return None
    return rascunho


def _promover_observacao(av: AtendimentoAvaliacao, campos: dict) -> None:
    """A nota do modo observação ganha a ação de verdade; a nota e a correção ficam.

    A pessoa deu 👍/👎 na sugestão ainda pendente (`observou`) e depois ela
    saiu pelo DaVinci (ou foi substituída pela resposta escrita do zero): o
    que conta para a IA é o que saiu — `enviou_igual`/`editou` com o texto
    final vira exemplo, desde que a nota não tenha sido 👎.
    """
    av.acao = campos["acao"]
    av.texto_final = campos.get("texto_final")
    av.similaridade = campos.get("similaridade")
    if av.user_id is None:
        av.user_id = campos.get("user_id")


async def _gravar_avaliacao(session: AsyncSession, **campos) -> None:
    """Uma avaliação por rascunho (UNIQUE): a segunda tentativa não derruba o envio.

    A que já existe só muda se for a provisória do modo observação (`observou`).
    """
    ja_tem = await _avaliacao_de(session, campos["rascunho_id"])
    if ja_tem is not None:
        if ja_tem.acao == AVALIACAO_OBSERVOU:
            _promover_observacao(ja_tem, campos)
        return
    await session.flush()
    try:
        async with session.begin_nested():
            session.add(AtendimentoAvaliacao(**campos))
            await session.flush()
    except IntegrityError:
        # Corrida com a nota da tela (dois cliques): a linha dela ganhou.
        # Se for a provisória, recebe a ação de verdade.
        logger.info("atendimento_avaliacao_ja_existia", rascunho_id=str(campos["rascunho_id"]))
        outra = await _avaliacao_de(session, campos["rascunho_id"])
        if outra is not None and outra.acao == AVALIACAO_OBSERVOU:
            _promover_observacao(outra, campos)


async def _pendente_da_conversa(
    session: AsyncSession, conversa: AtendimentoConversa
) -> AtendimentoRascunho | None:
    return (
        await session.execute(
            select(AtendimentoRascunho)
            .where(
                AtendimentoRascunho.conversa_id == conversa.id,
                AtendimentoRascunho.status == RASCUNHO_PENDENTE,
            )
            .limit(1)
        )
    ).scalar_one_or_none()


async def _avaliar_rascunho(
    session: AsyncSession,
    conversa: AtendimentoConversa,
    mensagem: AtendimentoMensagem,
    *,
    rascunho: AtendimentoRascunho | None,
    texto_digitado: str,
    user: User | None,
    origem: str,
) -> None:
    """Registra o que a pessoa fez com a sugestão da IA.

    - veio `rascunho_id`: similaridade ≥ 0.97 → `enviou_igual`, senão
      `editou` — com o texto que SAIU (é ele que vira exemplo). Vale também
      para a sugestão que ficou `substituido` enquanto a pessoa escrevia (aba
      aberta antes de a IA refazer): ela USOU aquele texto, e é isso que conta;
    - não veio, mas havia sugestão pendente: ela fica `substituido` e a
      avaliação é `escreveu_do_zero`;
    - veio uma sugestão velha e há OUTRA pendente (a IA refez no meio): a
      pendente sai da caixa (`substituido`) sem avaliação — a pessoa nem a
      viu, não é um "não serviu".

    Em todos os casos, depois de uma resposta da loja nenhuma sugestão fica
    pendente: ela seria para uma pergunta já respondida, e a tela a poria na
    caixa de novo (o comprador receberia duas respostas).

    Envio AUTOMÁTICO (origem IA) marca o rascunho `enviado` e NÃO cria
    avaliação: exemplo aprovado é o que PESSOA aprovou. Se a IA contasse
    as próprias respostas como aprovadas, ela se ensinaria sozinha — e o
    manual só muda por pessoa.
    """
    if origem == ORIGEM_IA:
        if rascunho is not None and rascunho.status == RASCUNHO_PENDENTE:
            rascunho.status = RASCUNHO_ENVIADO
        return
    avaliado = False
    if (
        rascunho is not None
        and rascunho.status in (RASCUNHO_PENDENTE, RASCUNHO_SUBSTITUIDO)
        and not await _ja_avaliado(session, rascunho.id)
    ):
        sim = similaridade(texto_digitado, rascunho.texto)
        igual = sim >= LIMIAR_ENVIOU_IGUAL
        rascunho.status = RASCUNHO_ENVIADO if igual else RASCUNHO_EDITADO
        await _gravar_avaliacao(
            session,
            rascunho_id=rascunho.id,
            acao="enviou_igual" if igual else "editou",
            texto_final=mensagem.texto,
            similaridade=sim,
            user_id=user.id if user else None,
        )
        avaliado = True
    if origem != ORIGEM_HUMANO:
        return
    pendente = await _pendente_da_conversa(session, conversa)
    if pendente is None or (rascunho is not None and pendente.id == rascunho.id):
        return
    pendente.status = RASCUNHO_SUBSTITUIDO
    if rascunho is None and not avaliado:
        # A sugestão estava na caixa e a pessoa escreveu outra coisa.
        await _gravar_avaliacao(
            session,
            rascunho_id=pendente.id,
            acao="escreveu_do_zero",
            texto_final=mensagem.texto,
            similaridade=similaridade(texto_digitado, pendente.texto),
            user_id=user.id if user else None,
        )


async def _avaliacao_de(
    session: AsyncSession, rascunho_id: UUID
) -> AtendimentoAvaliacao | None:
    return (
        await session.execute(
            select(AtendimentoAvaliacao).where(AtendimentoAvaliacao.rascunho_id == rascunho_id)
        )
    ).scalar_one_or_none()


async def _ja_avaliado(session: AsyncSession, rascunho_id: UUID) -> bool:
    """Já tem a avaliação DE VERDADE? A nota do modo observação não conta."""
    av = await _avaliacao_de(session, rascunho_id)
    return av is not None and av.acao != AVALIACAO_OBSERVOU


# ── Conferências sob a trava ──────────────────────────────────────────────


async def _conferir_repetido(
    session: AsyncSession, conversa: AtendimentoConversa, normalizado: str
) -> None:
    """A mesma resposta já saiu (ou está saindo) nesta conversa há pouco? → `envio_repetido`.

    Duplo clique, aba duplicada, a pessoa e a IA com a mesma frase: o índice
    de "uma em voo" só barra o que é SIMULTÂNEO; depois que a primeira saiu,
    a segunda passaria. Compara pelo texto normalizado (espaço, acento e
    pontuação não fazem duas respostas diferentes).
    """
    alvo = gravar.normalizar_para_comparar(normalizado)
    if not alvo:
        return
    textos = (
        await session.execute(
            select(AtendimentoMensagem.texto).where(
                AtendimentoMensagem.conversa_id == conversa.id,
                AtendimentoMensagem.origem.in_(ORIGENS_DAVINCI),
                AtendimentoMensagem.status.in_(_STATUS_QUE_SAIRAM),
                AtendimentoMensagem.created_at >= datetime.now(UTC) - JANELA_REPETIDO,
            )
        )
    ).scalars()
    if any(gravar.normalizar_para_comparar(t) == alvo for t in textos):
        raise EnvioRecusado(
            RECUSA_ENVIO_REPETIDO,
            "Esta mesma resposta acabou de ser enviada nesta conversa (há menos de "
            "2 minutos). Ela não foi enviada de novo.",
        )


async def _resposta_da_loja_depois(
    session: AsyncSession, conversa: AtendimentoConversa, referencia: AtendimentoMensagem
) -> AtendimentoMensagem | None:
    """A resposta da loja (que não falhou) mais recente DEPOIS de `referencia`."""
    momento = _momento_col()
    quando = referencia.enviada_em or referencia.created_at
    return (
        await session.execute(
            select(AtendimentoMensagem)
            .where(
                AtendimentoMensagem.conversa_id == conversa.id,
                AtendimentoMensagem.autor == AUTOR_LOJA,
                AtendimentoMensagem.status != MSG_FALHOU,
                AtendimentoMensagem.id != referencia.id,
                momento >= quando,
            )
            .order_by(momento.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def _conferir_mudou(
    session: AsyncSession, conversa: AtendimentoConversa, ultima_vista_id: UUID
) -> None:
    """Alguém respondeu depois da última mensagem que a pessoa VIU? → `conversa_mudou`.

    Duas pessoas no mesmo filtro abrem a mesma conversa; uma envia, a outra
    continua escrevendo em cima da sugestão. Sem esta conferência o comprador
    recebe duas respostas. Id estranho (de outra conversa, apagado) não trava:
    a tela antiga que não manda o id também não trava.
    """
    vista = await session.get(AtendimentoMensagem, ultima_vista_id)
    if vista is None or vista.conversa_id != conversa.id:
        return
    outra = await _resposta_da_loja_depois(session, conversa, vista)
    if outra is None:
        return
    if outra.origem == ORIGEM_HUMANO and outra.autor_user_id is not None:
        autor = await session.get(User, outra.autor_user_id)
        quem = (autor.name or autor.email or "Alguém da equipe") if autor else "Alguém da equipe"
    elif outra.origem == ORIGEM_IA:
        quem = "A IA"
    elif outra.origem == ORIGEM_EXTERNO:
        quem = "Alguém fora do DaVinci (Duoke/Seller Center)"
    else:
        quem = "A loja"
    hora = (outra.enviada_em or outra.created_at).astimezone(_SP).strftime("%H:%M")
    raise EnvioRecusado(
        RECUSA_CONVERSA_MUDOU,
        f"{quem} respondeu às {hora} enquanto você escrevia. Confira a conversa antes de "
        "enviar.",
    )


async def _conferir_gatilho_ia(
    session: AsyncSession, conversa: AtendimentoConversa, rascunho: AtendimentoRascunho | None
) -> None:
    """IA: a loja já respondeu a pergunta que esta sugestão responde? → `nao_aguarda`.

    Duas rodadas da IA na mesma conversa (a do minuto anterior demorou)
    geram duas sugestões para a MESMA pergunta. A primeira sai; a segunda
    chega aqui com a trava da conversa e encontra a resposta dela.
    """
    if rascunho is None or rascunho.mensagem_gatilho_id is None:
        return
    if rascunho.status != RASCUNHO_PENDENTE:
        raise EnvioRecusado(RECUSA_NAO_AGUARDA, "A sugestão não está mais pendente.")
    gatilho = await session.get(AtendimentoMensagem, rascunho.mensagem_gatilho_id)
    if gatilho is not None and await _resposta_da_loja_depois(session, conversa, gatilho):
        raise EnvioRecusado(RECUSA_NAO_AGUARDA, "A conversa já foi respondida.")


# ── Envio ─────────────────────────────────────────────────────────────────


async def _preparar_envio(
    session: AsyncSession,
    conversa: AtendimentoConversa,
    texto: str,
    *,
    user: User | None,
    rascunho_id: UUID | None,
    origem: str,
    ultima_vista_id: UUID | None,
    confirmar: bool,
) -> tuple[AtendimentoMensagem, _Destino, str, AtendimentoRascunho | None]:
    """Passos 0 e 1 do envio: trava, confere e grava a linha em voo (sem commit)."""
    await travar_conversa(session, conversa)
    destino = await _destino(session, conversa, origem=origem)
    normalizado = _preparar_texto(texto, conversa=conversa, origem=origem)
    rascunho = await _rascunho_da_conversa(session, conversa, rascunho_id)
    if rascunho is not None:
        await session.refresh(rascunho)
    if origem == ORIGEM_IA:
        await _conferir_gatilho_ia(session, conversa, rascunho)
    await _conferir_repetido(session, conversa, normalizado)
    if ultima_vista_id is not None and not confirmar:
        await _conferir_mudou(session, conversa, ultima_vista_id)

    # ── 1. a linha em voo (commitada por quem chamou ANTES da plataforma) ──
    await aposentar_envios_presos(session, conversa_id=conversa.id)
    mensagem = AtendimentoMensagem(
        conversa_id=conversa.id,
        externo_id=None,
        autor=AUTOR_LOJA,
        origem=origem,
        autor_user_id=user.id if user is not None else None,
        tipo="texto",
        texto=normalizado,
        anexos=[],
        # Relógio da PLATAFORMA: só existe depois que ela aceitar.
        enviada_em=None,
        status=MSG_ENVIANDO,
        rascunho_id=rascunho.id if rascunho is not None else None,
        payload={},
    )
    await session.flush()  # pendências de quem chamou fora do SAVEPOINT
    try:
        async with session.begin_nested():
            session.add(mensagem)
            await session.flush()
    except IntegrityError as e:
        if not is_unique_violation(e):
            raise  # FK (conversa apagada no meio) não é "envio em andamento"
        raise EnvioRecusado(
            RECUSA_ENVIO_EM_ANDAMENTO, "Há uma resposta sendo enviada nesta conversa."
        ) from e
    return mensagem, destino, normalizado, rascunho


async def enviar_resposta(
    session: AsyncSession,
    conversa: AtendimentoConversa,
    texto: str,
    *,
    user: User | None,
    rascunho_id: UUID | None = None,
    origem: str = ORIGEM_HUMANO,
    ultima_vista_id: UUID | None = None,
    confirmar: bool = False,
) -> AtendimentoMensagem:
    """Envia a resposta pela plataforma; devolve a mensagem gravada.

    Levanta `EnvioRecusado` quando uma trava impede o envio (nada saiu).
    Erro da plataforma NÃO levanta: vira `falhou`/`revisar` na mensagem.
    COMMITA duas vezes — antes de chamar a plataforma (a linha em voo) e
    depois (o resultado) —, então quem chama não deve ter nada pendente
    que não queira commitar junto. Numa recusa, nada fica gravado nem
    travado (o savepoint do passo 0 é desfeito).

    `ultima_vista_id` é a última mensagem que a pessoa viu na tela; se a loja
    respondeu depois dela, recusa com `conversa_mudou` (outra pessoa, a IA ou
    o Duoke respondeu enquanto ela escrevia). `confirmar=True` é a pessoa
    dizendo "vi, envie mesmo assim".
    """
    # ── 0. a conversa travada e relida: daqui em diante, o estado é o de AGORA
    # Tudo até a linha em voo roda num SAVEPOINT: numa recusa ele é desfeito
    # e a trava da conversa SOLTA na hora (trava pega num savepoint desfeito
    # não fica), sem estragar a transação nem os objetos de quem chamou.
    ponto = await session.begin_nested()
    try:
        mensagem, destino, normalizado, rascunho = await _preparar_envio(
            session,
            conversa,
            texto,
            user=user,
            rascunho_id=rascunho_id,
            origem=origem,
            ultima_vista_id=ultima_vista_id,
            confirmar=confirmar,
        )
    except BaseException:
        await ponto.rollback()
        raise
    await ponto.commit()
    # Em voo já conta como resposta na fila: a IA não gera por cima e a
    # conversa sai de "aguardando" enquanto a plataforma responde.
    gravar.recalcular(conversa, [mensagem])
    await session.commit()
    logger.info(
        "atendimento_envio_iniciado",
        conversa_id=str(conversa.id),
        mensagem_id=str(mensagem.id),
        plataforma=conversa.plataforma,
        origem=origem,
        simulador=get_settings().atendimento_simulador,
    )

    # ── 2. a plataforma (ou o simulador) ──────────────────────────────────
    resultado = await _chamar_plataforma(session, conversa, destino.integration, normalizado)

    # ── 3. o resultado, sobre o estado ATUAL do banco ─────────────────────
    await _gravar_resultado(
        session,
        conversa,
        mensagem,
        resultado,
        rascunho=rascunho,
        texto_digitado=texto,
        user=user,
        origem=origem,
    )
    logger.info(
        "atendimento_envio_concluido",
        conversa_id=str(conversa.id),
        mensagem_id=str(mensagem.id),
        plataforma=conversa.plataforma,
        status=mensagem.status,
        bloqueio=bool(resultado.bloqueio),
    )
    return mensagem


def _sqlstate(e: BaseException) -> str | None:
    orig = getattr(e, "orig", None)
    causa = getattr(orig, "__cause__", None) or orig
    return getattr(causa, "sqlstate", None)


async def _gravar_resultado(
    session: AsyncSession,
    conversa: AtendimentoConversa,
    mensagem: AtendimentoMensagem,
    resultado: ResultadoEnvio,
    *,
    rascunho: AtendimentoRascunho | None,
    texto_digitado: str,
    user: User | None,
    origem: str,
    avaliar: bool = True,
) -> None:
    """Passo 3: grava o que a plataforma disse. A mensagem JÁ SAIU (ou pode ter saído).

    `avaliar=False` (a foto): a foto não é resposta escrita no lugar da
    sugestão da IA — não vira avaliação "escreveu do zero" (o rascunho que
    ela tornar velho sai da caixa pelo `recalcular_conversa`, como sempre).

    A ordem das travas é a do sync (`gravar`): primeiro a CONVERSA, depois a
    mensagem. O sync que traz a nossa resposta de volta trava a conversa e
    depois adota a mensagem; se aqui fosse ao contrário, os dois se
    travariam um ao outro (deadlock) e o envio que saiu devolveria erro.

    Relê tudo depois de travar: o sync pode ter adotado a linha (ou mexido
    na conversa) enquanto a plataforma respondia. Deadlock, serialização ou
    o UNIQUE do id da plataforma (o sync gravou a mesma mensagem no meio)
    desfazem só este passo e ele roda de novo — ele é idempotente. Se nem
    assim gravar, a linha fica `enviando` (vira `revisar` em 10 min e aparece
    em "A conferir") e a pessoa recebe a mensagem como está, nunca um 500
    por um envio que pode ter saído.
    """
    for tentativa in range(1, TENTATIVAS_RESULTADO + 1):
        try:
            await gravar.travar_linha(session, conversa)
            await session.refresh(mensagem)
            if rascunho is not None:
                await session.refresh(rascunho)
            await _aplicar_resultado(session, conversa, mensagem, resultado)
            if avaliar and mensagem.status in (MSG_ENVIADA, MSG_REVISAR):
                await _avaliar_rascunho(
                    session,
                    conversa,
                    mensagem,
                    rascunho=rascunho,
                    texto_digitado=texto_digitado,
                    user=user,
                    origem=origem,
                )
            await gravar.recalcular_conversa(session, conversa)
            await session.commit()
            return
        except DBAPIError as e:
            estado = _sqlstate(e)
            await session.rollback()
            logger.warning(
                "atendimento_envio_resultado_repetir",
                conversa_id=str(conversa.id),
                mensagem_id=str(mensagem.id),
                tentativa=tentativa,
                sqlstate=estado,
                err=type(e).__name__,
            )
            if estado not in _SQLSTATE_TRANSITORIO or tentativa == TENTATIVAS_RESULTADO:
                logger.error(
                    "atendimento_envio_resultado_nao_gravado",
                    conversa_id=str(conversa.id),
                    mensagem_id=str(mensagem.id),
                    ok=resultado.ok,
                    ambiguo=resultado.ambiguo,
                )
                # Rollback expirou tudo: devolve os objetos legíveis (o router
                # monta a resposta com eles).
                for obj in (conversa, mensagem):
                    try:
                        await session.refresh(obj)
                    except Exception:  # noqa: BLE001, S110 — sessão quebrada; o log já está
                        pass
                return


# ── Foto (01/10/2026) ─────────────────────────────────────────────────────
# A foto sai pelo MESMO caminho do texto: as mesmas travas (`_destino`), a
# mesma linha em voo commitada antes da plataforma, o mesmo "uma em voo por
# conversa", a mesma régua de resultado (enviada / revisar / falhou). O que
# muda: a plataforma (upload + mensagem com a imagem, `foto.enviar_imagem`),
# a conferência de repetida (pela impressão digital da imagem, não pelo
# texto) e a avaliação da sugestão da IA (foto não é resposta escrita).
# Upload para a plataforma SÓ aqui — com o envio desligado, a trava
# `envio_desligado` recusa antes de qualquer byte sair do DaVinci.

TEMPO_MAXIMO_FOTO_S = 150


async def _conferir_foto_repetida(
    session: AsyncSession, conversa: AtendimentoConversa, sha256: str
) -> None:
    """A mesma imagem já saiu (ou está saindo) nesta conversa há pouco? → `envio_repetido`."""
    repetida = await session.scalar(
        select(AtendimentoMensagem.id)
        .where(
            AtendimentoMensagem.conversa_id == conversa.id,
            AtendimentoMensagem.origem.in_(ORIGENS_DAVINCI),
            AtendimentoMensagem.status.in_(_STATUS_QUE_SAIRAM),
            AtendimentoMensagem.created_at >= datetime.now(UTC) - JANELA_REPETIDO,
            AtendimentoMensagem.payload["foto"]["sha256"].astext == sha256,
        )
        .limit(1)
    )
    if repetida is not None:
        raise EnvioRecusado(
            RECUSA_ENVIO_REPETIDO,
            "Esta mesma foto acabou de ser enviada nesta conversa (há menos de 2 minutos). "
            "Ela não foi enviada de novo.",
        )


async def _preparar_foto(
    session: AsyncSession,
    conversa: AtendimentoConversa,
    foto,
    *,
    legenda: str | None,
    user: User | None,
    ultima_vista_id: UUID | None,
    confirmar: bool,
) -> tuple[AtendimentoMensagem, _Destino, str | None]:
    """Passos 0 e 1 da foto: trava, confere e grava a linha em voo (sem commit)."""
    from app.services.atendimento import foto as foto_mod

    await travar_conversa(session, conversa)
    destino = await _destino(session, conversa, origem=ORIGEM_HUMANO)
    motivo = foto_mod.motivo_sem_foto(conversa)
    if motivo:
        raise EnvioRecusado(RECUSA_FOTO_NAO_SUPORTADA, motivo)
    legenda_ok: str | None = None
    if foto_mod.legenda_obrigatoria(conversa):
        # ML: a foto vai DENTRO de uma mensagem — o texto passa pelo validador
        # como qualquer resposta (vazio = `texto_invalido`).
        legenda_ok = _preparar_texto(legenda, conversa=conversa, origem=ORIGEM_HUMANO)
    elif (legenda or "").strip():
        raise EnvioRecusado(
            RECUSA_FOTO_INVALIDA,
            "Nesta plataforma a foto vai sozinha: mande o texto pela caixa de resposta.",
        )
    await _conferir_foto_repetida(session, conversa, foto.sha256)
    if ultima_vista_id is not None and not confirmar:
        await _conferir_mudou(session, conversa, ultima_vista_id)

    await aposentar_envios_presos(session, conversa_id=conversa.id)
    mensagem = AtendimentoMensagem(
        conversa_id=conversa.id,
        externo_id=None,
        autor=AUTOR_LOJA,
        origem=ORIGEM_HUMANO,
        autor_user_id=user.id if user is not None else None,
        tipo="imagem",
        texto=legenda_ok,
        # A URL só existe depois do upload (`_aplicar_resultado` põe).
        anexos=[{"tipo": "imagem", "nome": foto.nome, "mime": foto.mime, "tamanho": foto.tamanho}],
        enviada_em=None,
        status=MSG_ENVIANDO,
        rascunho_id=None,
        payload={"foto": {"sha256": foto.sha256, "mime": foto.mime, "bytes": foto.tamanho}},
    )
    await session.flush()
    try:
        async with session.begin_nested():
            session.add(mensagem)
            await session.flush()
    except IntegrityError as e:
        if not is_unique_violation(e):
            raise
        raise EnvioRecusado(
            RECUSA_ENVIO_EM_ANDAMENTO, "Há uma resposta sendo enviada nesta conversa."
        ) from e
    return mensagem, destino, legenda_ok


async def _chamar_plataforma_foto(
    session: AsyncSession,
    conversa: AtendimentoConversa,
    integration: Integration,
    foto,
    legenda: str | None,
) -> ResultadoEnvio:
    """Sobe a foto e manda a mensagem. Nunca levanta (como `_chamar_plataforma`)."""
    from app.services.atendimento import foto as foto_mod

    s = get_settings()
    if s.atendimento_simulador and s.is_prod:
        return ResultadoEnvio(ok=False, erro="simulador_em_producao")
    if vai_para_o_simulador(conversa.plataforma):
        return ResultadoEnvio(ok=True, externo_id=f"sim:{uuid4()}", payload={"simulador": True})
    try:
        cliente = await clientes.cliente_da_integracao(integration)
    except Exception as e:  # noqa: BLE001
        return ResultadoEnvio(ok=False, erro=f"cliente_indisponivel: {type(e).__name__}")
    try:
        return await asyncio.wait_for(
            foto_mod.enviar_imagem(session, conversa, integration, cliente, foto, legenda),
            timeout=TEMPO_MAXIMO_FOTO_S,
        )
    except TimeoutError:
        return ResultadoEnvio(ok=False, ambiguo=True, erro="timeout")
    except Exception as e:  # noqa: BLE001 — sem saber se saiu: ambíguo
        return ResultadoEnvio(ok=False, ambiguo=True, erro=f"erro_inesperado: {type(e).__name__}")


async def enviar_foto(
    session: AsyncSession,
    conversa: AtendimentoConversa,
    foto,
    *,
    legenda: str | None = None,
    user: User | None,
    ultima_vista_id: UUID | None = None,
    confirmar: bool = False,
) -> AtendimentoMensagem:
    """Envia UMA foto (já conferida: `foto.validar_foto`) na conversa; devolve a mensagem.

    O mesmo contrato do `enviar_resposta`: `EnvioRecusado` = nada saiu (trava
    nossa); erro da plataforma vira `falhou`/`revisar` na mensagem; COMMITA
    antes de falar com a plataforma e depois. Só pessoa manda foto (a IA
    nunca). `legenda` só no ML (obrigatória lá); Shopee/TikTok: foto sozinha.
    """
    ponto = await session.begin_nested()
    try:
        mensagem, destino, legenda_ok = await _preparar_foto(
            session,
            conversa,
            foto,
            legenda=legenda,
            user=user,
            ultima_vista_id=ultima_vista_id,
            confirmar=confirmar,
        )
    except BaseException:
        await ponto.rollback()
        raise
    await ponto.commit()
    # Em voo já conta como resposta na fila (como o texto).
    gravar.recalcular(conversa, [mensagem])
    await session.commit()
    logger.info(
        "atendimento_foto_iniciada",
        conversa_id=str(conversa.id),
        mensagem_id=str(mensagem.id),
        plataforma=conversa.plataforma,
        bytes=foto.tamanho,
        simulador=get_settings().atendimento_simulador,
    )
    resultado = await _chamar_plataforma_foto(
        session, conversa, destino.integration, foto, legenda_ok
    )
    await _gravar_resultado(
        session,
        conversa,
        mensagem,
        resultado,
        rascunho=None,
        texto_digitado=legenda_ok or "",
        user=user,
        origem=ORIGEM_HUMANO,
        avaliar=False,
    )
    logger.info(
        "atendimento_foto_concluida",
        conversa_id=str(conversa.id),
        mensagem_id=str(mensagem.id),
        plataforma=conversa.plataforma,
        status=mensagem.status,
        bloqueio=bool(resultado.bloqueio),
    )
    return mensagem
