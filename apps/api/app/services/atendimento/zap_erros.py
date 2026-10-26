"""Zap (WhatsApp Cloud API) — a tabela ÚNICA dos erros da Meta (RF10, Onda 2).

Desenho: `docs/zap/desenho.md` §6.5 (a tabela), §6.2 item 7 (quem aplica o
efeito) e §5.6 item 4 (o efeito forte vindo do webhook).

Cada código da Meta → `ErroZap(classe, frase, efeito)`:

  classe   o que aconteceu com a mensagem (bloqueio = a janela de 24 h
           fechou; falhou = não saiu; pausa = não saiu E o número para;
           credencial = o token caiu; numero = o número não está pronto na
           API; aviso = configuração do número; modelo = o modelo pago
           (Onda 5); ambiguo = pode ter saído);
  frase    o que a pessoa lê no balão (sem código, sem dado do cliente);
  efeito   o que muda no DaVinci (pausar o número, marcar o token inválido,
           fechar a janela…) — ou None.

A tradução mora SÓ aqui (o `zap_api._erro_zap` também lê daqui). Os
dicionários `_ERRO_POR_CODE` do `marketing/meta_client.py` NÃO mudam: as
frases do Instagram e do Facebook para 10, 200, 368 e 190 são outras, e os
testes de Redes Sociais dependem delas.

O ADAPTADOR (`zap.enviar_texto`) não grava nada: ele só descreve o efeito em
`ResultadoEnvio.payload['zap']['efeito']`, e o `enviar.enviar_resposta`
chama `aplicar_efeito` DEPOIS de gravar o resultado, numa transação curta e
própria — dentro do adaptador o efeito se perderia no rollback do laço de
`_gravar_resultado`. O processador do webhook (`zap_entrada._status`) chama
o mesmo `aplicar_efeito`, mas o efeito FORTE (pausar o número, marcar o
token) só vale com DUAS ocorrências em wamids diferentes em 24 h
(`forte_pelo_webhook`): quem roubasse o app secret não pausaria o número com
um `failed` forjado. A resposta da NOSSA chamada vem pelo TLS do
`graph.facebook.com` e vale na primeira.

Os códigos marcados [confirmar] não foram vistos num payload real ainda; o
comportamento deles é o conservador (não saiu, ou "não sabemos se saiu").
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger()

# ── Classes ────────────────────────────────────────────────────────────────
CLASSE_BLOQUEIO = "bloqueio"
CLASSE_FALHOU = "falhou"
CLASSE_PAUSA = "pausa"
CLASSE_CREDENCIAL = "credencial"
CLASSE_NUMERO = "numero"
CLASSE_AVISO = "aviso"
CLASSE_MODELO = "modelo"
CLASSE_AMBIGUO = "ambiguo"

# ── Efeitos (o que muda no DaVinci) ───────────────────────────────────────
# A janela de 24 h fecha AGORA (`conversa.pode_enviar_ate = agora`): a trava
# 12 do envio lê esse campo e para de deixar mandar (corrige relógio).
EFEITO_FECHAR_JANELA = "fechar_janela"
# 131050: o cliente não quer marketing (`opt_out_tipo='marketing'`).
EFEITO_OPT_OUT_MARKETING = "opt_out_marketing"
# 368/131048: a Meta restringiu o número — `envio_ligado=false`,
# `saude_status='restrito'` e alerta.
EFEITO_PAUSAR_NUMERO = "pausar_numero"
# 131031: a conta do número foi travada (pode ser PIN errado) — pausa E trava
# o "registrar" (a tela da Onda 4 lê `saude.registro_travado`).
EFEITO_PAUSAR_E_TRAVAR_REGISTRO = "pausar_e_travar_registro"
# 133016: tentativas de registro demais — o "registrar" trava por 72 h.
EFEITO_TRAVAR_REGISTRO = "travar_registro"
# 131042: problema de pagamento na conta Meta — pausa e alerta.
EFEITO_PAUSAR_PAGAMENTO = "pausar_pagamento"
# 131045/133010: o número não está registrado na API.
EFEITO_ERRO_NUMERO = "erro_numero"
# 190/0: o token caiu (vencido, revogado, senha trocada).
EFEITO_TOKEN_INVALIDO = "token_invalido"  # noqa: S105 — nome do efeito, não segredo
# 10/200: o token não tem permissão nesta conta.
EFEITO_SEM_PERMISSAO = "sem_permissao"
# 132000–132016: problema no modelo (Onda 5 sincroniza; hoje, só o log).
EFEITO_SINCRONIZAR_MODELOS = "sincronizar_modelos"

# Os que mexem no NÚMERO ou na CONTA: pelo webhook, só com 2 ocorrências
# (§5.6 item 4). Fechar a janela e o opt-out de marketing valem na hora.
EFEITOS_FORTES = frozenset(
    {
        EFEITO_PAUSAR_NUMERO,
        EFEITO_PAUSAR_E_TRAVAR_REGISTRO,
        EFEITO_TRAVAR_REGISTRO,
        EFEITO_PAUSAR_PAGAMENTO,
        EFEITO_ERRO_NUMERO,
        EFEITO_TOKEN_INVALIDO,
        EFEITO_SEM_PERMISSAO,
    }
)
# Os que PAUSAM o número (`envio_ligado=false`).
EFEITOS_QUE_PAUSAM = frozenset(
    {EFEITO_PAUSAR_NUMERO, EFEITO_PAUSAR_E_TRAVAR_REGISTRO, EFEITO_PAUSAR_PAGAMENTO}
)

# Pelo webhook, o efeito forte só vale com este tanto de wamids diferentes em 24 h.
OCORRENCIAS_PARA_EFEITO_FORTE = 2

FRASE_AMBIGUO = "Não sabemos se saiu: a Meta não respondeu. Confira antes de repetir."
FRASE_DESCONHECIDO = "A Meta recusou a mensagem"


@dataclass(frozen=True)
class ErroZap:
    code: int | None
    classe: str
    frase: str
    efeito: str | None = None

    @property
    def ambiguo(self) -> bool:
        return self.classe == CLASSE_AMBIGUO


_FRASE_JANELA = (
    "Passaram 24 h desde a última mensagem do cliente: o WhatsApp só aceita um modelo aprovado."
)
_FRASE_LIMITE = "Mensagens demais em pouco tempo: espere um minuto antes de mandar de novo."
_FRASE_RESTRITO = "A Meta restringiu este número. Veja no WhatsApp Manager."
_FRASE_TRAVADO = (
    "A conta deste número foi travada pela Meta (pode ser PIN errado). "
    "Não tente de novo; fale com o suporte da Meta."
)
_FRASE_REGISTRO = (
    "Tentativas de registro demais: a Meta bloqueou o registro deste número por 72 h. "
    "Não tente de novo; fale com o suporte da Meta."
)
_FRASE_PAGAMENTO = "Problema de pagamento na conta Meta: as mensagens não estão saindo."
_FRASE_NAO_REGISTRADO = "Este número não está registrado na API do WhatsApp."
_FRASE_TOKEN = "A conexão com a Meta caiu: é preciso um token novo."  # noqa: S105 — frase
_FRASE_PERMISSAO = "O token não tem permissão nesta conta do WhatsApp."
_FRASE_MIDIA = "Tipo ou arquivo não aceito pelo WhatsApp."
_FRASE_MODELO = "Problema no modelo de mensagem."

# §6.5 do desenho. Código → (classe, frase, efeito).
_TABELA: dict[int, tuple[str, str, str | None]] = {
    131047: (CLASSE_BLOQUEIO, _FRASE_JANELA, EFEITO_FECHAR_JANELA),
    # O código antigo da mesma regra (antes da v16 da Graph) [confirmar].
    470: (CLASSE_BLOQUEIO, _FRASE_JANELA, EFEITO_FECHAR_JANELA),
    131026: (
        CLASSE_FALHOU,
        "Não foi entregue: o número pode não ter WhatsApp, ou o aplicativo do cliente "
        "está desatualizado.",
        None,
    ),
    131050: (
        CLASSE_FALHOU,
        "O cliente pediu para não receber mensagens de marketing da loja.",
        EFEITO_OPT_OUT_MARKETING,
    ),
    131049: (
        CLASSE_FALHOU,
        "A Meta segurou a mensagem de marketing para este cliente; tente em 24 h.",
        None,
    ),
    # Limites: nunca retentado sozinho (a pessoa manda de novo depois).
    4: (CLASSE_FALHOU, _FRASE_LIMITE, None),
    80007: (CLASSE_FALHOU, _FRASE_LIMITE, None),
    80008: (CLASSE_FALHOU, _FRASE_LIMITE, None),
    130429: (CLASSE_FALHOU, _FRASE_LIMITE, None),
    131056: (CLASSE_FALHOU, _FRASE_LIMITE, None),
    131048: (CLASSE_PAUSA, _FRASE_RESTRITO, EFEITO_PAUSAR_NUMERO),
    368: (CLASSE_PAUSA, _FRASE_RESTRITO, EFEITO_PAUSAR_NUMERO),
    131031: (CLASSE_PAUSA, _FRASE_TRAVADO, EFEITO_PAUSAR_E_TRAVAR_REGISTRO),
    133016: (CLASSE_NUMERO, _FRASE_REGISTRO, EFEITO_TRAVAR_REGISTRO),
    # Desde 01/10/2026 barra também a resposta comum [confirmar na página de preços].
    131042: (CLASSE_PAUSA, _FRASE_PAGAMENTO, EFEITO_PAUSAR_PAGAMENTO),
    131045: (CLASSE_NUMERO, _FRASE_NAO_REGISTRADO, EFEITO_ERRO_NUMERO),
    133010: (CLASSE_NUMERO, _FRASE_NAO_REGISTRADO, EFEITO_ERRO_NUMERO),
    131037: (
        CLASSE_AVISO,
        "O nome de exibição deste número ainda não foi aprovado pela Meta.",
        None,
    ),
    131051: (CLASSE_FALHOU, _FRASE_MIDIA, None),
    131052: (CLASSE_FALHOU, _FRASE_MIDIA, None),
    131053: (CLASSE_FALHOU, _FRASE_MIDIA, None),
    131057: (CLASSE_FALHOU, "A conta está em manutenção na Meta: tente mais tarde.", None),
    190: (CLASSE_CREDENCIAL, _FRASE_TOKEN, EFEITO_TOKEN_INVALIDO),
    0: (CLASSE_CREDENCIAL, _FRASE_TOKEN, EFEITO_TOKEN_INVALIDO),
    10: (CLASSE_CREDENCIAL, _FRASE_PERMISSAO, EFEITO_SEM_PERMISSAO),
    200: (CLASSE_CREDENCIAL, _FRASE_PERMISSAO, EFEITO_SEM_PERMISSAO),
    # Número de teste: o destino não está entre os destinatários cadastrados
    # no painel da Meta ("Recipient phone number not in allowed list" na
    # tabela de códigos da Cloud API; até 5 destinatários). Conferido só pela
    # documentação (26/10/2026) — o ensaio do §11.5 confirma com a resposta real.
    131030: (
        CLASSE_FALHOU,
        "Número de teste: este celular não está na lista de destinatários de teste da Meta.",
        None,
    ),
    100: (
        CLASSE_FALHOU,
        "A Meta não aceitou o pedido (confira o id da conta ou do número).",
        None,
    ),
    131008: (CLASSE_FALHOU, "A Meta não aceitou a mensagem (faltou um campo).", None),
    131009: (CLASSE_FALHOU, "A Meta não aceitou a mensagem (um campo veio inválido).", None),
    131021: (CLASSE_FALHOU, "O destino é o próprio número da loja.", None),
    # "Erro desconhecido"/"serviço indisponível": pode ter saído — `revisar`,
    # nunca retentado [confirmar com a Meta se 131000 garante que não saiu].
    1: (CLASSE_AMBIGUO, FRASE_AMBIGUO, None),
    2: (CLASSE_AMBIGUO, FRASE_AMBIGUO, None),
    131000: (CLASSE_AMBIGUO, FRASE_AMBIGUO, None),
}
_MODELOS = range(132000, 132017)


def traduzir(code: int | None) -> ErroZap:
    """O código da Meta → `ErroZap`. Sem código (timeout, 5xx cru) = AMBÍGUO. PURA."""
    if code is None:
        return ErroZap(None, CLASSE_AMBIGUO, FRASE_AMBIGUO, None)
    if code in _TABELA:
        classe, frase, efeito = _TABELA[code]
        return ErroZap(code, classe, frase, efeito)
    if code in _MODELOS:
        return ErroZap(code, CLASSE_MODELO, _FRASE_MODELO, EFEITO_SINCRONIZAR_MODELOS)
    return ErroZap(code, CLASSE_FALHOU, f"{FRASE_DESCONHECIDO} (código {code}).", None)


def frase(code: int | None) -> str:
    """Só a frase (para o `zap_api` e para o balão)."""
    return traduzir(code).frase


def codigo_do_erro(erro: str | None) -> int | None:
    """`"meta:131047"` (o `erro` gravado na mensagem) → 131047. PURA."""
    if not erro or not erro.startswith("meta:"):
        return None
    resto = erro[5:].split(" ", 1)[0]
    return int(resto) if resto.isdigit() else None


def frase_da_mensagem(erro: str | None) -> str | None:
    """A frase do balão para o `erro` gravado (`meta:<code>`); None = não é da Meta."""
    if not erro or not erro.startswith("meta:"):
        return None
    code = codigo_do_erro(erro)
    if code is None:
        return FRASE_AMBIGUO if erro.startswith("meta:sem_resposta") else FRASE_DESCONHECIDO + "."
    return frase(code)


# ── Aplicar o efeito ──────────────────────────────────────────────────────


def _agora() -> datetime:
    return datetime.now(UTC)


async def aplicar_efeito(
    session: AsyncSession,
    efeito: str | None,
    *,
    numero_id: UUID | None,
    conta_id: UUID | None = None,
    conversa_id: UUID | None = None,
    contato_id: UUID | None = None,
    code: int | None = None,
    origem: str = "api",
    agora: datetime | None = None,
) -> bool:
    """Aplica o efeito no banco (NÃO commita). True = mudou alguma coisa.

    `origem` = "api" (a resposta da NOSSA chamada) ou "webhook" (o processador
    só chama aqui com o forte depois das 2 ocorrências — `forte_pelo_webhook`).
    Os alvos são RELIDOS por id (`populate_existing`): o efeito vale sobre o
    estado de agora, não sobre o objeto que o envio leu antes da Meta responder.
    """
    from app.models import (
        AtendimentoCanal,
        AtendimentoConversa,
        AtendimentoZapConta,
        AtendimentoZapContato,
        AtendimentoZapNumero,
    )

    if not efeito:
        return False
    agora = agora or _agora()
    frase_efeito = traduzir(code).frase if code is not None else None
    log: dict[str, Any] = {
        "efeito": efeito,
        "code": code,
        "origem": origem,
        "numero": str(numero_id) if numero_id else None,
    }
    if efeito == EFEITO_FECHAR_JANELA:
        if conversa_id is None:
            return False
        conversa = await session.get(AtendimentoConversa, conversa_id, populate_existing=True)
        if conversa is None:
            return False
        conversa.pode_enviar_ate = agora
        await session.flush()
        logger.info("zap_efeito", **log)
        return True
    if efeito == EFEITO_OPT_OUT_MARKETING:
        if contato_id is None:
            return False
        contato = await session.get(AtendimentoZapContato, contato_id, populate_existing=True)
        if contato is None:
            return False
        if contato.opt_out_tipo != "todos":
            # `todos` já barra mais que o marketing: não rebaixa.
            contato.opt_out_em = contato.opt_out_em or agora
            contato.opt_out_tipo = "marketing"
        await session.flush()
        logger.info("zap_efeito", **log)
        return True
    if efeito in (EFEITO_TOKEN_INVALIDO, EFEITO_SEM_PERMISSAO):
        conta = None
        if conta_id is not None:
            conta = await session.get(AtendimentoZapConta, conta_id, populate_existing=True)
        elif numero_id is not None:
            numero = await session.get(AtendimentoZapNumero, numero_id, populate_existing=True)
            if numero is not None:
                conta = await session.get(
                    AtendimentoZapConta, numero.conta_ref, populate_existing=True
                )
        if conta is None:
            return False
        conta.status = efeito
        conta.ultimo_erro, conta.ultimo_erro_code, conta.ultimo_erro_em = (
            frase_efeito,
            code,
            agora,
        )
        await session.flush()
        # Alerta: a tela de números (Onda 4) e o alarme leem a conta; aqui, o log.
        logger.error("zap_efeito_credencial", **log)
        return True
    if efeito == EFEITO_SINCRONIZAR_MODELOS:
        # Onda 5 (modelos): por ora só fica no log.
        logger.warning("zap_efeito_modelos", **log)
        return False
    if numero_id is None:
        return False
    numero = await session.get(AtendimentoZapNumero, numero_id, populate_existing=True)
    if numero is None:
        return False
    numero.ultimo_erro, numero.ultimo_erro_code, numero.ultimo_erro_em = (
        frase_efeito,
        code,
        agora,
    )
    saude = dict(numero.saude or {})
    if efeito in EFEITOS_QUE_PAUSAM:
        numero.envio_ligado = False
        numero.saude_status = "erro" if efeito == EFEITO_PAUSAR_PAGAMENTO else "restrito"
        saude["pausado_pela_meta"] = {"code": code, "em": agora.isoformat(), "origem": origem}
        if numero.saude_status == "restrito" and frase_efeito:
            # A barra mantém o aviso (`zap_entrada._saude_ok` lê a `restricao`).
            saude["restricao"] = frase_efeito
            saude["restricao_em"] = agora.isoformat()
    if efeito in (EFEITO_PAUSAR_E_TRAVAR_REGISTRO, EFEITO_TRAVAR_REGISTRO):
        saude["registro_travado"] = {"code": code, "em": agora.isoformat()}
    if efeito == EFEITO_ERRO_NUMERO:
        numero.saude_status = "erro"
    if numero.saude_status == "erro" and frase_efeito:
        # Pagamento (131042) e número não registrado (131045/133010): a barra
        # mantém o aviso até o número voltar a mandar (`zap_entrada._saude_ok`
        # lê daqui; `zap.depois_do_envio` limpa) — revisão de 26/10/2026. O
        # `ultimo_erro` não serve: o erro passageiro seguinte o sobrescreve.
        saude["erro_meta"] = {"code": code, "frase": frase_efeito, "em": agora.isoformat()}
    numero.saude = saude
    if numero.canal_id is not None and frase_efeito:
        canal = await session.get(AtendimentoCanal, numero.canal_id, populate_existing=True)
        if canal is not None:
            canal.status = "erro"
            canal.ultimo_erro = frase_efeito[:500]
            canal.ultimo_erro_em = agora
    await session.flush()
    # Alerta (o alarme de verdade é da Onda 4): pausar o número é sempre alto.
    logger.error("zap_efeito_numero", **log)
    return True
