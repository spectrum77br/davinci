"""Apagar a mensagem que a LOJA mandou pelo DaVinci (03/11/2026).

O Eduardo: "faz esse negócio então de deletar a mensagem no clique igual o
duoke". É o "Retirar" do Duoke: o comprador deixa de ver a mensagem — a
Shopee troca o texto por "This message was deleted" no `get_message` (o
mesmo `message_id`, o bit 0x200000 no `message_option`; medido em produção,
53 apagadas pelo Duoke em 30 dias). Só a Shopee tem como (o adaptador com
`apagavel` + `apagar_mensagem`): ML, TikTok, Amazon, Magalu, Carrefour, Zap
e e-mail não apagam mensagem enviada pela API (o mapa de 03/11/2026).

As travas (`_alvo`, PURA — a tela recebe o resultado em `pode_apagar_ate` e
não fixa plataforma nem prazo):

  - autor `loja` com origem do DaVinci (`ORIGENS_DAVINCI`) — NUNCA `externo`
    (Duoke/Seller Center): a Shopee só deixa apagar o app que mandou;
  - status `enviada`, com o id da plataforma — o `sim:` do simulador, e
    qualquer uma com o simulador pegando a plataforma, nunca chamam ninguém;
  - o que é da plataforma (`apagavel` do adaptador): na Shopee, chat de
    COMPRADOR, `message_id` numérico, texto/imagem/cartão do pedido/cartão
    do produto/figurinha (vídeo não) e a janela (`JANELA_APAGAR`) contada
    pelo relógio da Shopee.

O resultado (`apagar_mensagem`; quem chama já travou a conversa e releu a
mensagem — o router, como no "conferir" — e commita depois):

  - apagou → `falhou` + `ERRO_APAGADA` + `payload.apagada` {por, user_id,
    em}; a conversa é recalculada (volta para "Falta responder" se era a
    resposta) e, se a apagada era da IA, a IA PAUSA na conversa — nas lojas
    em AUTO ela mandaria de novo a mesma coisa;
  - a plataforma recusou com código → nada muda (`PLATAFORMA_RECUSOU`);
  - timeout/erro sem código → ambíguo: o status fica, `payload.apagar_incerto`
    guarda a tentativa e a tela diz "confira na Shopee". Tentar de novo é
    seguro (apagar duas vezes não manda nada ao comprador), e a leitura
    seguinte vê o bit e marca, se apagou (`shopee._marcar_apagada`).

O texto continua na linha (só a equipe vê). Texto de comprador (e o nosso)
nunca vai para o log — só ids e códigos.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from types import ModuleType
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import AtendimentoConversa, AtendimentoMensagem, Integration
from app.services.atendimento import clientes, gravar
from app.services.atendimento.constantes import (
    AUTOR_LOJA,
    ERRO_APAGADA,
    MSG_ENVIADA,
    MSG_FALHOU,
    ORIGEM_IA,
    ORIGENS_DAVINCI,
    ResultadoEnvio,
)

logger = structlog.get_logger()

# Quem apagou (`payload.apagada.por`): o botão do DaVinci, ou a plataforma
# disse na leitura (a equipe pelo Duoke/Seller Center, ou o nosso incerto).
POR_DAVINCI = "davinci"
POR_PLATAFORMA = "plataforma"

# Códigos estáveis (a tela traduz; o router devolve 409/502).
MENSAGEM_NAO_APAGAVEL = "mensagem_nao_apagavel"
PRAZO_APAGAR_PASSOU = "prazo_apagar_passou"
PLATAFORMA_RECUSOU = "plataforma_recusou"
APAGAR_INCERTO = "apagar_incerto"
SEM_INTEGRACAO = "sem_integracao"

# O id que o simulador dá ao envio que fingiu (`enviar._chamar_plataforma`).
_PREFIXO_SIMULADOR = "sim:"
# Teto da ida à plataforma. A conversa fica TRAVADA enquanto isso (como no
# "conferir"): o sync espera, e a outra aba ouve "tente de novo" — melhor
# que dois "apagar" da mesma mensagem cruzando com a leitura.
TEMPO_MAXIMO_APAGAR_S = 20


class ApagarRecusado(Exception):  # noqa: N818 — português, como `EnvioRecusado`
    """Uma trava impede apagar: nada foi à plataforma e nada mudou."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(code)


@dataclass
class ResultadoApagar:
    """`code` None = apagou; senão `PLATAFORMA_RECUSOU` ou `APAGAR_INCERTO`."""

    code: str | None = None
    # O código (e a frase curta) da plataforma, para a tela.
    erro: str | None = None
    ia_pausada: bool = False

    @property
    def apagou(self) -> bool:
        return self.code is None


# ── As travas ─────────────────────────────────────────────────────────────


def _adaptador_que_apaga(plataforma: str) -> ModuleType | None:
    """O adaptador da plataforma, se ele sabe apagar (`apagavel` + `apagar_mensagem`)."""
    from app.services.atendimento import enviar  # tardio: o enviar carrega muita coisa

    try:
        modulo = enviar.adaptador(plataforma)
    except (KeyError, ImportError):
        return None
    if not callable(getattr(modulo, "apagavel", None)) or not callable(
        getattr(modulo, "apagar_mensagem", None)
    ):
        return None
    return modulo


def _simulador_pega(plataforma: str) -> bool:
    """O simulador está com esta plataforma (ou ligado em produção)? Aí nada vai a ela."""
    from app.services.atendimento import enviar

    s = get_settings()
    return bool(s.atendimento_simulador and s.is_prod) or enviar.vai_para_o_simulador(plataforma)


def _alvo(
    conversa: AtendimentoConversa, mensagem: AtendimentoMensagem
) -> tuple[ModuleType, str, datetime] | None:
    """(adaptador, tipo na plataforma, até quando) — ou None: esta não se apaga. Sem banco.

    O barato primeiro: a tela chama isto para cada mensagem da conversa
    (`pode_apagar_ate`), e quase todas param no autor.
    """
    if mensagem.conversa_id != conversa.id or mensagem.autor != AUTOR_LOJA:
        return None
    if mensagem.origem not in ORIGENS_DAVINCI or mensagem.status != MSG_ENVIADA:
        return None
    externo = str(mensagem.externo_id or "").strip()
    if not externo or externo.startswith(_PREFIXO_SIMULADOR):
        return None
    modulo = _adaptador_que_apaga(conversa.plataforma)
    if modulo is None or _simulador_pega(conversa.plataforma):
        return None
    prazo = modulo.apagavel(conversa, mensagem)
    if prazo is None:
        return None
    tipo, ate = prazo
    return modulo, tipo, ate


def pode_apagar_ate(
    mensagem: AtendimentoMensagem,
    conversa: AtendimentoConversa,
    *,
    agora: datetime | None = None,
) -> datetime | None:
    """Até quando o botão "apagar" vale para esta mensagem; None = não aparece.

    Só com TODAS as travas passando e ainda dentro da janela. É o campo
    `pode_apagar_ate` da `MensagemOut` (calculado, sem coluna): a tela
    mostra o botão com a contagem até aqui, e a rota confere tudo de novo.
    """
    alvo = _alvo(conversa, mensagem)
    if alvo is None:
        return None
    ate = alvo[2]
    return ate if (agora or datetime.now(UTC)) < ate else None


# ── A marca na linha ──────────────────────────────────────────────────────


def marcar_apagada(
    mensagem: AtendimentoMensagem,
    *,
    por: str,
    user_id: UUID | None = None,
    resposta: dict | None = None,
) -> None:
    """A linha vira `falhou` + `ERRO_APAGADA`, com `payload.apagada`. Sem banco.

    O texto NÃO muda: o original fica para a equipe (a tela mostra riscado).
    `resposta` = o que a plataforma devolveu ao apagar (sem texto), para
    conferir o formato no primeiro uso real. Quem chama recalcula a conversa.
    """
    apagada: dict[str, Any] = {"por": por, "em": datetime.now(UTC).isoformat()}
    if user_id is not None:
        apagada["user_id"] = str(user_id)
    if resposta:
        apagada["resposta"] = dict(resposta)
    mensagem.status = MSG_FALHOU
    mensagem.erro = ERRO_APAGADA
    # Dicionário NOVO: mutar o JSONB no lugar não marca a coluna como suja.
    mensagem.payload = {**(mensagem.payload or {}), "apagada": apagada}


def pausar_ia_se_era_dela(conversa: AtendimentoConversa, mensagem: AtendimentoMensagem) -> bool:
    """A apagada era da IA: a IA pausa na conversa (True = pausou agora). Sem banco.

    A conversa volta para "Falta responder" e, nas lojas em AUTO, a IA
    mandaria de novo a mesma coisa (ou parecida) no minuto seguinte. Quem
    apagou decide quando ela volta (o "retomar IA" da conversa).
    """
    if mensagem.origem != ORIGEM_IA or conversa.ia_pausada:
        return False
    conversa.ia_pausada = True
    return True


def apagada(mensagem: AtendimentoMensagem) -> bool:
    """A mensagem foi apagada na plataforma (pelo DaVinci ou por fora)? PURA."""
    return mensagem.status == MSG_FALHOU and mensagem.erro == ERRO_APAGADA


# ── Apagar ────────────────────────────────────────────────────────────────


async def _chamar_plataforma(
    modulo: ModuleType,
    session: AsyncSession,
    conversa: AtendimentoConversa,
    integration: Integration,
    mensagem: AtendimentoMensagem,
    tipo: str,
) -> ResultadoEnvio:
    """Fala com a plataforma. Nunca levanta (a régua do `enviar._chamar_plataforma`)."""
    try:
        cliente = await clientes.cliente_da_integracao(integration)
    except Exception as e:  # noqa: BLE001 — nada saiu: a falha foi ao montar o cliente
        return ResultadoEnvio(ok=False, erro=f"cliente_indisponivel: {type(e).__name__}")
    try:
        return await asyncio.wait_for(
            modulo.apagar_mensagem(session, conversa, integration, cliente, mensagem, tipo=tipo),
            timeout=TEMPO_MAXIMO_APAGAR_S,
        )
    except TimeoutError:
        return ResultadoEnvio(ok=False, ambiguo=True, erro="timeout")
    except Exception as e:  # noqa: BLE001 — o adaptador promete não levantar: pode ter apagado
        return ResultadoEnvio(ok=False, ambiguo=True, erro=f"erro_inesperado: {type(e).__name__}")


async def apagar_mensagem(
    session: AsyncSession,
    conversa: AtendimentoConversa,
    mensagem: AtendimentoMensagem,
    *,
    user_id: UUID | None,
) -> ResultadoApagar:
    """Apaga na plataforma a mensagem da loja; levanta `ApagarRecusado` nas travas.

    Quem chama já TRAVOU a conversa e RELEU a mensagem (o estado é o de
    agora) e commita depois — também no incerto, que grava a tentativa.
    Nunca commita.
    """
    alvo = _alvo(conversa, mensagem)
    if alvo is None:
        raise ApagarRecusado(
            MENSAGEM_NAO_APAGAVEL,
            "Só dá para apagar a mensagem que o DaVinci mandou no chat da Shopee e que já saiu.",
        )
    modulo, tipo, ate = alvo
    if datetime.now(UTC) >= ate:
        raise ApagarRecusado(
            PRAZO_APAGAR_PASSOU, "Passou o prazo: a Shopee só deixa apagar logo depois de mandar."
        )
    integration = (
        await session.get(Integration, conversa.integration_id)
        if conversa.integration_id is not None
        else None
    )
    if integration is None or integration.archived_at is not None:
        raise ApagarRecusado(
            SEM_INTEGRACAO, "A loja desta conversa não está mais conectada ao DaVinci."
        )

    resultado = await _chamar_plataforma(modulo, session, conversa, integration, mensagem, tipo)

    if resultado.ok:
        marcar_apagada(mensagem, por=POR_DAVINCI, user_id=user_id, resposta=resultado.payload)
        pausou = pausar_ia_se_era_dela(conversa, mensagem)
        # Fila e prazo: a resposta apagada não conta mais (`falhou`).
        await gravar.recalcular_conversa(session, conversa)
        return ResultadoApagar(ia_pausada=pausou)
    erro = (resultado.erro or "erro")[:160]
    if resultado.ambiguo:
        mensagem.payload = {
            **(mensagem.payload or {}),
            "apagar_incerto": {
                "user_id": str(user_id) if user_id is not None else None,
                "em": datetime.now(UTC).isoformat(),
                "erro": erro,
            },
        }
        return ResultadoApagar(code=APAGAR_INCERTO, erro=erro)
    return ResultadoApagar(code=PLATAFORMA_RECUSOU, erro=erro)
