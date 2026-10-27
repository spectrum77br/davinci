"""Zap (WhatsApp Cloud API) — a tela "Números do WhatsApp": cadastro, botões e LGPD (RF10, Onda 4).

Desenho: `docs/zap/desenho.md` §8.4 (a tela), §8.5 (as rotas), §10.1
(credenciais, multi-WABA), §10.2 (webhook), §10.5 (LGPD) e §13.1/§13.3 (o que
o Eduardo faz na Meta e no dia da migração). 27/10/2026.

Quem usa: `routers/atendimento_zap.py` (só ADMINISTRADOR — decisão do Eduardo
de 26/10/2026 — e, para mexer, quem está em ATENDIMENTO_USUARIOS) e o cron de
saúde (`zap_saude`, as leituras da Meta). Tudo o que grava ou fala com a Meta
(até o GET do "Testar") fica atrás da chave `atendimento_zap_cadastro_ativo`
(nasce desligada): desligada, a tela só MOSTRA o que está no banco — o
"pausar" e o "pausar todos" continuam valendo (parar nunca espera chave).

O Eduardo não é técnico e não usa terminal: na produção ele COLA a chave
secreta do app e o token do usuário do sistema direto na tela (campo oculto).
Aqui:

  • a chave e o token chegam só no corpo do POST, são conferidos na Meta
    ANTES de gravar e ficam CIFRADOS (`zap_contas.cifrar_*`); nenhuma
    resposta, log, exceção ou Histórico os leva (a máscara do Histórico pega
    `app_secret`, `access_token` e `pin` pelo nome do campo);
  • a conta (WABA) passa pela inspeção ESTRITA (`zap_api.inspecionar_token`:
    o app certo, os dois escopos, a WABA nas permissões, a validade) e pela
    leitura da WABA e dos números — falhou, NADA é gravado;
  • fora de produção (o computador) só entra o app de TESTE com a WABA de
    teste (todo número +1 555 e o token sem outra WABA): a credencial de
    produção nunca fica no banco local (P4, §10.1) — a mesma trava do
    `scripts/atendimento_zap_local.py cadastrar`;
  • em produção, app de TESTE não cadastra número que não seja de teste;
  • "Inscrever" é ESCRITA na Meta (`POST /{waba}/subscribed_apps`): só pelo
    botão, com confirmação na tela, e sempre conferida pelo `GET` (o app do
    Duoke que continuar inscrito continua recebendo as mensagens);
  • "Registrar" (`POST /{pnid}/register`) só na etapa `migrando` com o caso
    A ou B marcado (no A2 o número já está registrado pelo Duoke) — ou no
    NÚMERO DE TESTE na etapa `teste` (o ensaio real de 27/10/2026: ele também
    volta 133010 "não registrado" até o register com PIN) —, uma tentativa
    por clique, contada ANTES de chamar a Meta (com o número travado), até 3
    em 72 h; o 131031/133016 trava o botão ("não tente de novo; fale com o
    suporte"); o registro que dá certo limpa o "não registrado";
  • a exclusão LGPD (`apagar_contato`) varre o contato, a conversa, as
    mensagens, as mídias (o arquivo some), os pares e o replay do agente e os
    eventos daquele contato — as tabelas de dado do cliente já estão fora do
    gatilho do Histórico, e a rota está em `SEM_CORPO`: a exclusão não deixa
    cópia. `apagar_por_telefone` (o campo da tela) acha TODOS os contatos
    daquele telefone (com e sem o 9º dígito, em todos os números da loja).

Nada aqui commita, menos onde está dito (o "registrar" grava a tentativa
antes de chamar a Meta).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

import httpx
import structlog
from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import (
    AtendimentoAgenteReplay,
    AtendimentoCanal,
    AtendimentoConversa,
    AtendimentoMensagem,
    AtendimentoPar,
    AtendimentoRascunho,
    AtendimentoZapApp,
    AtendimentoZapConta,
    AtendimentoZapContato,
    AtendimentoZapEvento,
    AtendimentoZapMidia,
    AtendimentoZapNumero,
    Marca,
    User,
)
from app.models.atendimento_zap import ZAP_NUMERO_CASOS, ZAP_NUMERO_ETAPAS
from app.security.cipher import decrypt_json, encrypt_json
from app.services.atendimento import (
    canais_externos,
    zap,
    zap_api,
    zap_contas,
    zap_entrada,
    zap_erros,
    zap_midia,
)
from app.services.atendimento.constantes import CANAL_ZAP, MODO_OBSERVAR, PLATAFORMA_WHATSAPP

logger = structlog.get_logger()

# Os campos que o Eduardo marca no webhook do app na Meta (§10.2, §13.1 passo
# 7): só com `messages` não chegam os alarmes de qualidade e restrição, o
# status dos modelos nem o "parar" do cliente.
CAMPOS_DO_WEBHOOK = (
    "messages",
    "user_preferences",
    "account_update",
    "account_alerts",
    "phone_number_quality_update",
    "phone_number_name_update",
    "message_template_status_update",
    "message_template_quality_update",
)

FRASE_CADASTRO_DESLIGADO = (
    "O cadastro do WhatsApp está desligado no servidor. A tela só mostra; "
    "peça para ligar (ATENDIMENTO_ZAP_CADASTRO_ATIVO) quando for cadastrar."
)
LEMBRETE_REVOGAR = (
    "Token novo gravado. Agora revogue o ANTIGO na Meta: Configurações do negócio → "
    "Usuários do sistema → o usuário do DaVinci → Revogar tokens."
)
# O token sem `target_ids` (a Meta não detalhou as contas — o token do usuário
# do sistema de 27/10/2026): aceito, conferido pela leitura da WABA, e a tela
# avisa (decisão do orquestrador de 27/10/2026, §10.1).
AVISO_ALVOS_NAO_DETALHADOS = (
    "Atenção: a Meta não disse quais contas este token enxerga — confira na Meta "
    "que o usuário do sistema é Funcionário e tem só esta conta."
)
TEXTO_APAGADO = "[apagado a pedido]"
# O estado do ANEXO da mensagem apagada a pedido (o registro da mídia fica
# `apagada`, o único que o CHECK aceita; o balão diferencia pela mensagem).
STATUS_ANEXO_APAGADO = "apagada_a_pedido"

# "Registrar" (§8.5, §13.3): a Meta aceita 10 em 72 h e bloqueia o número na
# 10ª (133016); o DaVinci para na 3ª. Só com estes casos (no A2 o número já
# está registrado pelo Duoke).
REGISTRO_MAX = 3
# Os erros da Meta de "número não registrado" (o registro que dá certo limpa).
CODIGOS_NAO_REGISTRADO = (131045, 133010)
REGISTRO_JANELA = timedelta(hours=72)
CASOS_QUE_REGISTRAM = ("A", "B")
# 133016 trava por 72 h; 131031 (conta travada, PIN errado) só sai à mão.
_TRAVA_DO_REGISTRO_HORAS = {133016: 72}

# Etapas que mandam (as mesmas da trava 7 do envio).
ETAPAS_QUE_ENVIAM = zap.ETAPAS_QUE_ENVIAM
# As que DESLIGAM o envio junto.
_ETAPAS_QUE_PAUSAM = ("pausado", "desligado")

_SO_DIGITOS = re.compile(r"[0-9]{5,32}")
_PIN = re.compile(r"[0-9]{6}")

# O que "Reprocessar" reabre (o resto é terminal de propósito).
RESULTADOS_REPROCESSAVEIS = (
    zap_entrada.R_NUMERO_DESCONHECIDO,
    zap_entrada.R_WABA_DESCONHECIDA,
    zap_entrada.R_NUMERO_DE_OUTRA_WABA,
    zap_entrada.R_JSON_INVALIDO,
    zap_entrada.R_ERRO,
)


class Recusa(Exception):  # noqa: N818 — português, como `ZapApiErro`
    """O que a tela recusa, com o código e a frase em português (sem segredo)."""

    def __init__(self, code: str, frase: str, *, status: int = 409):
        self.code = code
        self.frase = frase
        self.status = status
        super().__init__(frase)


def _agora() -> datetime:
    return datetime.now(UTC)


def _iso(quando: datetime | None) -> str | None:
    return quando.astimezone(UTC).isoformat() if quando else None


def _data(bruto: object) -> datetime | None:
    if not isinstance(bruto, str) or not bruto:
        return None
    try:
        quando = datetime.fromisoformat(bruto)
    except ValueError:
        return None
    return quando if quando.tzinfo else quando.replace(tzinfo=UTC)


# ── Réguas (puras) ────────────────────────────────────────────────────────


def producao() -> bool:
    return bool(get_settings().is_prod)


def cadastro_ligado() -> bool:
    return bool(get_settings().atendimento_zap_cadastro_ativo)


def exigir_cadastro() -> None:
    if not cadastro_ligado():
        raise Recusa("zap_cadastro_desligado", FRASE_CADASTRO_DESLIGADO)


def url_do_webhook(app_id: str) -> str:
    """O endereço que o Eduardo cola na Meta (o host principal, o do `api_url`)."""
    return f"{get_settings().api_url.rstrip('/')}/api/webhooks/meta/whatsapp/{app_id}"


def eh_numero_de_teste(display: str | None) -> bool:
    """O número de teste da Meta: um +1 555 (código de área fictício dos EUA). PURA.

    A mesma régua do `scripts/atendimento_zap_local.py` (que importa daqui).
    [confirmar] se a Meta passa a dar número de teste fora do +1 555.
    """
    digitos = re.sub(r"\D", "", display or "")
    return digitos.startswith("1555") and len(digitos) == 11


def _url_normal(url: object) -> str:
    texto = str(url or "").strip()
    if not texto:
        return ""
    partes = urlsplit(texto)
    return f"{partes.scheme.lower()}://{(partes.netloc or '').lower()}{partes.path.rstrip('/')}"


def webhook_em_outro_lugar(cfg: object, nossa_url: str) -> list[dict[str, str]]:
    """Onde o `webhook_configuration` aponta para FORA do DaVinci → `[{onde, host}]`. PURA.

    `phone_number` e `whatsapp_business_account` são o endereço ALTERNATIVO
    (posto no número ou na WABA — pelo Duoke, por exemplo) e têm prioridade
    sobre o do app; `application` é o do próprio app na Meta (diferente = a
    URL colada na Meta não é a nossa). Só o HOST sai daqui (a URL de outro
    sistema pode levar token na query). [confirmar] se o alternativo vale
    para todos os apps da WABA (§14.3).
    """
    if not isinstance(cfg, dict):
        return []
    nossa = _url_normal(nossa_url)
    fora: list[dict[str, str]] = []
    for onde in ("phone_number", "whatsapp_business_account", "application"):
        valor = cfg.get(onde)
        if not valor or _url_normal(valor) == nossa:
            continue
        fora.append({"onde": onde, "host": (urlsplit(str(valor)).hostname or "?")[:120]})
    return fora


FRASE_WEBHOOK_OUTRO = {
    "phone_number": "o NÚMERO tem um endereço alternativo de webhook",
    "whatsapp_business_account": "a CONTA do WhatsApp tem um endereço alternativo de webhook",
    "application": "a URL do webhook colada no app da Meta não é a do DaVinci",
}


def frase_do_webhook(fora: list[dict[str, str]]) -> str | None:
    if not fora:
        return None
    partes = [f"{FRASE_WEBHOOK_OUTRO.get(o['onde'], o['onde'])} ({o['host']})" for o in fora]
    return (
        "As mensagens deste número estão indo para outro lugar: "
        + "; ".join(partes)
        + ". Peça a quem pôs (o Duoke?) para tirar, ou corrija a URL na Meta."
    )


# O que o `GET /{pnid}` diz do número além da qualidade (revisão de 27/10/2026):
# fora da Cloud API (`platform_type` ≠ CLOUD_API — o 133010 do ensaio real
# voltou NOT_APPLICABLE até o register), o `status` ruim e o nome recusado.
PLATAFORMA_DA_API = "CLOUD_API"
STATUS_META_RUINS = ("BANNED", "RESTRICTED", "FLAGGED", "DISCONNECTED")
P_NAO_REGISTRADO = "nao_registrado"
P_STATUS_META = "status_meta"
P_NOME_RECUSADO = "nome_recusado"
P_WEBHOOK_NAO_CONFERIDO = "webhook_nao_conferido"


def problemas_do_numero(numero: AtendimentoZapNumero) -> list[tuple[str, str]]:
    """O que a última leitura da Meta (`saude`) mostrou de errado → `[(código, frase)]`. PURA.

    A frase é a mesma no "Testar", na pill e no alarme. O webhook em outro
    lugar tem frase própria (`frase_do_webhook`).
    """
    saude = numero.saude or {}
    out: list[tuple[str, str]] = []
    plataforma = str(saude.get("plataforma") or "").upper()
    if plataforma and plataforma != PLATAFORMA_DA_API:
        out.append(
            (
                P_NAO_REGISTRADO,
                "este número NÃO está registrado na API do WhatsApp (Cloud API): use "
                "Registrar, com o PIN de 6 dígitos",
            )
        )
    status = str(saude.get("status_meta") or "").upper()
    if status in STATUS_META_RUINS:
        out.append(
            (
                P_STATUS_META,
                f"a Meta marca este número como {status}: veja no WhatsApp Manager",
            )
        )
    if str(numero.nome_status or "").upper() == "DECLINED":
        out.append(
            (
                P_NOME_RECUSADO,
                "a Meta recusou o nome de exibição deste número: corrija no WhatsApp Manager",
            )
        )
    if saude.get("webhook_conferido") is False:
        out.append(
            (
                P_WEBHOOK_NAO_CONFERIDO,
                "não deu para conferir para onde vão as mensagens deste número (a Meta "
                "não devolveu o webhook na última leitura): use Testar conexão",
            )
        )
    return out


def _tentativas_recentes(numero: AtendimentoZapNumero, agora: datetime) -> list[datetime]:
    return [
        t if t.tzinfo else t.replace(tzinfo=UTC)
        for t in (numero.registro_tentativas or [])
        if t is not None and agora - (t if t.tzinfo else t.replace(tzinfo=UTC)) < REGISTRO_JANELA
    ]


def registro_travado(numero: AtendimentoZapNumero, agora: datetime) -> str | None:
    """A Meta travou o "registrar" (131031/133016)? A frase; None = não."""
    trava = (numero.saude or {}).get("registro_travado")
    if not isinstance(trava, dict):
        return None
    code = trava.get("code")
    horas = _TRAVA_DO_REGISTRO_HORAS.get(int(code)) if isinstance(code, int) else None
    if horas is not None:
        em = _data(trava.get("em"))
        if em is not None and agora - em >= timedelta(hours=horas):
            return None
    return zap_erros.frase(code if isinstance(code, int) else None)


@dataclass(frozen=True)
class EstadoRegistro:
    pode: bool
    motivo: str | None
    tentativas_72h: int


def estado_do_registro(numero: AtendimentoZapNumero, agora: datetime) -> EstadoRegistro:
    """O botão "Registrar" aparece? (§8.5) — e, se não, por quê. PURA."""
    n = len(_tentativas_recentes(numero, agora))
    # O número de TESTE da Meta (etapa `teste`) também precisa do register com
    # PIN (o 133010 do ensaio real de 27/10/2026) — sem caso de migração.
    teste = bool(numero.teste) and numero.etapa == "teste"
    if numero.etapa != "migrando" and not teste:
        return EstadoRegistro(
            False,
            "Só na etapa Migrando, no dia da troca (ou no número de teste, na etapa Teste).",
            n,
        )
    if not teste and numero.caso_migracao is None:
        return EstadoRegistro(False, "Marque o caso da migração (A, A2 ou B) antes.", n)
    if not teste and numero.caso_migracao not in CASOS_QUE_REGISTRAM:
        return EstadoRegistro(
            False,
            "No caso A2 o número já está registrado pelo Duoke: registrar de novo só "
            "queimaria tentativas.",
            n,
        )
    travado = registro_travado(numero, agora)
    if travado:
        return EstadoRegistro(False, travado, n)
    if n >= REGISTRO_MAX:
        return EstadoRegistro(
            False,
            f"Já foram {REGISTRO_MAX} tentativas em 72 h: espere ou fale com o suporte da Meta.",
            n,
        )
    if numero.pin_enc is None:
        return EstadoRegistro(False, "Cadastre o PIN de 6 dígitos do número antes.", n)
    return EstadoRegistro(True, None, n)


def frase_do_token(conta: AtendimentoZapConta, agora: datetime) -> tuple[bool, str]:
    """O que a tela diz do token, em português simples — nunca o token. PURA."""
    if conta.status == "token_invalido":
        return False, conta.ultimo_erro or "O token caiu (vencido ou revogado): cole um novo."
    if conta.status == "sem_permissao":
        return False, conta.ultimo_erro or "O token não tem permissão nesta conta."
    erro = (conta.saude or {}).get("inspecao_erro")
    if conta.token_conferido_em is None:
        return False, "Token ainda não conferido."
    if conta.token_nunca_vence:
        frase = "Token ok, nunca vence, escopos ok."
    elif conta.token_expira_em is not None:
        dias = (conta.token_expira_em - agora).days
        if dias < 0:
            return False, "O token venceu: gere outro na Meta e use Trocar token."
        frase = f"Token ok, escopos ok — vence em {dias} dia(s): o certo é validade Nunca."
    else:
        frase = "Token ok, escopos ok."
    if (conta.saude or {}).get("alvos_detalhados") is False:
        frase += f" {AVISO_ALVOS_NAO_DETALHADOS}"
    if erro:
        return True, f"{frase} (a última conferência não respondeu: {erro})"
    return True, frase


# ── O app ─────────────────────────────────────────────────────────────────


async def app_por_ref(session: AsyncSession, app_ref: UUID | str) -> AtendimentoZapApp:
    try:
        uid = UUID(str(app_ref))
    except ValueError:
        raise Recusa("zap_app_nao_encontrado", "App não encontrado.", status=404) from None
    app = await session.get(AtendimentoZapApp, uid)
    if app is None:
        raise Recusa("zap_app_nao_encontrado", "App não encontrado.", status=404)
    return app


async def cadastrar_app(
    session: AsyncSession,
    *,
    nome: str | None,
    app_id: str,
    app_secret: str,
    teste: bool,
    user: User | None,
) -> AtendimentoZapApp:
    """O app da Meta (ID do app + CHAVE SECRETA). Gera o código de verificação. Não commita.

    Fora de produção todo app é de TESTE (o "DaVinci Zap Teste"): a chave do
    app de produção nunca entra no computador.
    """
    exigir_cadastro()
    app_id = (app_id or "").strip()
    if not _SO_DIGITOS.fullmatch(app_id):
        raise Recusa(
            "zap_app_id_invalido", "O ID do app é só números (copie de Configurações → Básico)."
        )
    app_secret = (app_secret or "").strip()
    if not app_secret:
        raise Recusa("zap_chave_vazia", "Cole a chave secreta do app (Configurações → Básico).")
    existe = await session.scalar(
        select(AtendimentoZapApp.id).where(AtendimentoZapApp.app_id == app_id)
    )
    if existe is not None:
        raise Recusa(
            "zap_app_ja_existe",
            "Este app já está cadastrado. Para trocar a chave, use Trocar chave secreta.",
        )
    app = AtendimentoZapApp(
        app_id=app_id,
        nome=((nome or "").strip()[:120] or None),
        segredo_enc=zap_contas.cifrar_segredo_app(app_secret),
        verify_token_enc=zap_contas.cifrar_verify_token(zap_contas.gerar_verify_token()),
        teste=True if not producao() else bool(teste),
        ativo=True,
        criado_por=user.id if user is not None else None,
    )
    session.add(app)
    await session.flush()
    zap_contas.limpar_cache(app_id)
    logger.info("zap_app_cadastrado", app=str(app.id), teste=app.teste)
    return app


def verify_token(app: AtendimentoZapApp) -> str:
    """O código de verificação (só para a rota que revela, registrada no Histórico)."""
    return str(decrypt_json(app.verify_token_enc).get("verify_token") or "")


async def gerar_outro_verify_token(session: AsyncSession, app: AtendimentoZapApp) -> None:
    exigir_cadastro()
    app.verify_token_enc = zap_contas.cifrar_verify_token(zap_contas.gerar_verify_token())
    await session.flush()
    zap_contas.limpar_cache(app.app_id)


def segredo_anterior_ate(app: AtendimentoZapApp, agora: datetime) -> datetime | None:
    """Até quando a chave ANTERIOR ainda vale (a troca de rotina: 1 h)."""
    segredo = decrypt_json(app.segredo_enc)
    ate = _data(segredo.get("anterior_ate"))
    return ate if segredo.get("anterior") and ate and ate > agora else None


async def trocar_chave(
    session: AsyncSession, app: AtendimentoZapApp, *, app_secret: str, motivo: str
) -> datetime | None:
    """Troca a chave secreta: `rotina` (a anterior vale 1 h) ou `vazou` (sai do banco na hora).

    O cache do segredo (60 s, por processo da api) é limpo aqui e de novo na
    rota DEPOIS do commit; os outros processos esquecem no TTL — a tela diz
    "em até 1 minuto". Invalidar todos na hora (Redis) fica pendente.
    """
    exigir_cadastro()
    app_secret = (app_secret or "").strip()
    if not app_secret:
        raise Recusa("zap_chave_vazia", "Cole a chave secreta NOVA do app.")
    agora = _agora()
    zap_contas.trocar_segredo(app, app_secret, motivo=motivo, agora=agora)
    await session.flush()
    logger.warning("zap_chave_trocada", app=str(app.id), motivo=motivo)
    return segredo_anterior_ate(app, agora)


# ── A conta (WABA) e o token ─────────────────────────────────────────────


async def conta_por_ref(session: AsyncSession, conta_ref: UUID | str) -> AtendimentoZapConta:
    try:
        uid = UUID(str(conta_ref))
    except ValueError:
        raise Recusa("zap_conta_nao_encontrada", "Conta não encontrada.", status=404) from None
    conta = await session.get(AtendimentoZapConta, uid)
    if conta is None:
        raise Recusa("zap_conta_nao_encontrada", "Conta não encontrada.", status=404)
    return conta


@dataclass(frozen=True)
class TokenConferido:
    """O que a Meta confirmou do token novo (nada de token aqui)."""

    inspecao: zap_api.InspecaoToken
    waba: dict[str, Any]
    numeros: list[dict[str, Any]]


async def conferir_token_novo(
    app: AtendimentoZapApp, waba_id: str, token: str, *, cliente: httpx.AsyncClient
) -> TokenConferido:
    """A conferência COMPLETA antes de gravar: inspeção estrita, a WABA e os números.

    Fora de produção: só a WABA de TESTE (todo número +1 555; o token sem
    outra WABA nas permissões). Qualquer falta → `Recusa` (nada foi gravado).

    O token SEM `target_ids` (`alvos_detalhados=False`, o que a Meta entregou
    em 27/10/2026) passa: a conferência é a leitura da WABA e dos números COM
    ele (`ler_waba` + `listar_numeros`, logo abaixo) — e "outras WABAs" fica
    não sabido (`saude.outras_wabas=None`), com o aviso na tela.
    """
    try:
        inspecao = await zap_api.inspecionar_token(
            token, app_id=app.app_id, waba_id=waba_id, cliente=cliente
        )
        waba = await zap_api.ler_waba(waba_id, token, cliente=cliente)
        numeros = await zap_api.listar_numeros(waba_id, token, cliente=cliente)
    except zap_api.ZapApiErro as exc:
        # A frase já vem sem o token (`zap_api._redigir_zap`).
        raise Recusa("zap_token_recusado", f"A Meta não aceitou: {exc}") from None
    if not producao():
        outras = {
            str(t)
            for escopo in zap_api.ESCOPOS_OBRIGATORIOS
            for t in inspecao.target_ids.get(escopo, ())
        } - {str(waba_id)}
        reais = [n for n in numeros if not eh_numero_de_teste(n.get("display_phone_number"))]
        if outras or reais:
            raise Recusa(
                "zap_so_teste_fora_de_producao",
                "Neste computador só entra o app de TESTE com o número de teste da Meta: "
                "este token enxerga outra conta ou um número de verdade. A chave de "
                "produção se cola só na tela do DaVinci do servidor.",
            )
    return TokenConferido(inspecao=inspecao, waba=waba, numeros=numeros)


def _aplicar_inspecao(
    conta: AtendimentoZapConta, insp: zap_api.InspecaoToken, agora: datetime
) -> None:
    conta.token_nunca_vence = insp.nunca_vence
    conta.token_expira_em = insp.expira_em
    conta.token_conferido_em = agora
    vencendo = (
        not insp.nunca_vence
        and insp.expira_em is not None
        and insp.expira_em - agora < timedelta(days=7)
    )
    conta.status = "vencendo" if vencendo else "ok"
    conta.ultimo_ok_em = agora
    conta.ultimo_erro = conta.ultimo_erro_code = conta.ultimo_erro_em = None
    saude = dict(conta.saude or {})
    saude.pop("inspecao_erro", None)
    saude.pop("inspecao_erro_em", None)
    saude["alvos_detalhados"] = insp.alvos_detalhados
    # Sem `target_ids` a Meta não disse quais contas o token enxerga: "não
    # sabido" (None), nunca 0.
    saude["outras_wabas"] = (
        len(
            {str(t) for e in zap_api.ESCOPOS_OBRIGATORIOS for t in insp.target_ids.get(e, ())}
            - {conta.waba_id}
        )
        if insp.alvos_detalhados
        else None
    )
    conta.saude = saude


async def numeros_da_meta(
    session: AsyncSession, brutos: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Os números que a Meta mostrou, com "já cadastrado?" e "é de teste?"."""
    pnids = [str(n.get("id") or "") for n in brutos if n.get("id")]
    cadastrados = set(
        (
            await session.execute(
                select(AtendimentoZapNumero.phone_number_id).where(
                    AtendimentoZapNumero.phone_number_id.in_(pnids)
                )
            )
        )
        .scalars()
        .all()
    )
    return [
        {
            "phone_number_id": str(n.get("id")),
            "display_phone_number": str(n.get("display_phone_number") or ""),
            "verified_name": n.get("verified_name"),
            "nome_status": n.get("name_status"),
            "qualidade": n.get("quality_rating"),
            "teste": eh_numero_de_teste(n.get("display_phone_number")),
            "cadastrado": str(n.get("id")) in cadastrados,
        }
        for n in brutos
        if n.get("id")
    ]


async def cadastrar_conta(
    session: AsyncSession,
    *,
    app: AtendimentoZapApp,
    waba_id: str,
    business_id: str | None,
    access_token: str,
) -> tuple[AtendimentoZapConta, list[dict[str, Any]]]:
    """A conta do WhatsApp (WABA) com o TOKEN do usuário do sistema. Não commita.

    Primeiro tudo o que pode recusar (o formato, a conta repetida, a
    conferência completa na Meta); só então grava, cifrado. Devolve a conta
    e os números que a Meta mostrou (quem escolhe cadastrar é a pessoa).
    """
    exigir_cadastro()
    waba_id = (waba_id or "").strip()
    if not _SO_DIGITOS.fullmatch(waba_id):
        raise Recusa("zap_waba_invalida", "O ID da conta do WhatsApp é só números.")
    if business_id and not _SO_DIGITOS.fullmatch(business_id.strip()):
        raise Recusa("zap_business_invalido", "O ID do portfólio é só números.")
    token = (access_token or "").strip()
    if not token:
        raise Recusa("zap_token_vazio", "Cole o token do usuário do sistema.")
    if await zap_contas.conta_por_waba(session, waba_id) is not None:
        raise Recusa(
            "zap_conta_ja_existe",
            "Esta conta do WhatsApp já está cadastrada: para um token novo, use Trocar token.",
        )
    agora = _agora()
    async with zap_api.novo_cliente() as http:
        conferido = await conferir_token_novo(app, waba_id, token, cliente=http)
    conta = AtendimentoZapConta(
        app_ref=app.id,
        waba_id=waba_id,
        business_id=(business_id or "").strip() or None,
        nome=str(conferido.waba.get("name") or "")[:120] or None,
        token_enc=zap_contas.cifrar_token_conta(
            token,
            scopes=list(conferido.inspecao.escopos),
            system_user_id=conferido.inspecao.system_user_id,
        ),
        token_trocado_em=agora,
        saude={},
    )
    _aplicar_inspecao(conta, conferido.inspecao, agora)
    session.add(conta)
    await session.flush()
    logger.info("zap_conta_cadastrada", conta=str(conta.id), app=str(app.id))
    return conta, await numeros_da_meta(session, conferido.numeros)


async def trocar_token(
    session: AsyncSession, conta: AtendimentoZapConta, *, access_token: str
) -> AtendimentoZapConta:
    """O TOKEN novo: as mesmas conferências; o antigo só sai depois de tudo dar certo."""
    exigir_cadastro()
    token = (access_token or "").strip()
    if not token:
        raise Recusa("zap_token_vazio", "Cole o token NOVO do usuário do sistema.")
    app = await app_por_ref(session, conta.app_ref)
    agora = _agora()
    async with zap_api.novo_cliente() as http:
        conferido = await conferir_token_novo(app, conta.waba_id, token, cliente=http)
    conta.token_enc = zap_contas.cifrar_token_conta(
        token,
        scopes=list(conferido.inspecao.escopos),
        system_user_id=conferido.inspecao.system_user_id,
    )
    conta.token_trocado_em = agora
    conta.nome = str(conferido.waba.get("name") or "")[:120] or conta.nome
    _aplicar_inspecao(conta, conferido.inspecao, agora)
    await session.flush()
    logger.warning("zap_token_trocado", conta=str(conta.id))
    return conta


async def descobrir(session: AsyncSession, conta: AtendimentoZapConta) -> list[dict[str, Any]]:
    """Os números da conta na Meta — SÓ GET (mas fala com a Meta: só com a chave ligada)."""
    exigir_cadastro()
    token = _token_ou_recusa(conta)
    try:
        async with zap_api.novo_cliente() as http:
            brutos = await zap_api.listar_numeros(conta.waba_id, token, cliente=http)
    except zap_api.ZapApiErro as exc:
        await _efeito_de_credencial(session, conta, exc)
        raise Recusa("zap_meta_recusou", f"A Meta não respondeu à leitura: {exc}") from None
    return await numeros_da_meta(session, brutos)


def frase_da_meta(exc: zap_api.ZapApiErro) -> str:
    """A frase da tabela única (`zap_erros`) quando ela diz algo; senão a do erro (sem token)."""
    if exc.code is not None:
        info = zap_erros.traduzir(exc.code)
        if not info.ambiguo and not info.frase.startswith(zap_erros.FRASE_DESCONHECIDO):
            return info.frase
    return str(exc)


def _token_ou_recusa(conta: AtendimentoZapConta) -> str:
    token = zap.token_valido(conta)
    if token is None:
        raise Recusa(
            "zap_sem_credencial",
            "A conexão com a Meta desta conta caiu: cole um token novo (Trocar token).",
        )
    return token


async def _efeito_de_credencial(
    session: AsyncSession, conta: AtendimentoZapConta, exc: zap_api.ZapApiErro
) -> None:
    """190/0 (token caiu) e 10/200 (sem permissão) marcam a conta.

    Pela resposta da NOSSA chamada (vem pelo TLS do graph.facebook.com).
    """
    info = zap_erros.traduzir(exc.code) if exc.code is not None else None
    if info is not None and info.efeito in (
        zap_erros.EFEITO_TOKEN_INVALIDO,
        zap_erros.EFEITO_SEM_PERMISSAO,
    ):
        await zap_erros.aplicar_efeito(
            session, info.efeito, numero_id=None, conta_id=conta.id, code=exc.code
        )


# ── Inscrição (subscribed_apps) ───────────────────────────────────────────


async def conferir_inscricao(
    session: AsyncSession,
    conta: AtendimentoZapConta,
    app: AtendimentoZapApp,
    token: str,
    *,
    cliente: httpx.AsyncClient,
    agora: datetime,
) -> dict[str, Any]:
    """`GET /{waba}/subscribed_apps`: o nosso app está lá — e SÓ ele? Grava na `saude`."""
    apps = await zap_api.apps_inscritos(conta.waba_id, token, cliente=cliente)
    nosso = any(a["id"] == app.app_id for a in apps)
    outros = [a for a in apps if a["id"] != app.app_id]
    conta.saude = {
        **(conta.saude or {}),
        "inscritos": apps,
        "outros_apps": outros,
        "nosso_inscrito": nosso,
        "inscricao_em": _iso(agora),
    }
    conta.inscrito_em = (conta.inscrito_em or agora) if (nosso and not outros) else None
    if outros:
        logger.error("zap_outro_app_inscrito", conta=str(conta.id), outros=len(outros))
    return {"inscrito": nosso and not outros, "nosso": nosso, "apps": apps, "outros_apps": outros}


def frase_da_inscricao(r: dict[str, Any]) -> str:
    if r["outros_apps"]:
        nomes = ", ".join(a.get("nome") or a["id"] for a in r["outros_apps"])
        if not r["nosso"]:
            return (
                f"Atenção: o app do DaVinci NÃO está inscrito nesta conta, e {nomes} está. "
                "As mensagens vão só para lá: clique em Inscrever e peça para removerem o "
                "app deles desta conta."
            )
        return (
            f"Atenção: além do DaVinci, está inscrito: {nomes}. As mensagens dos clientes "
            "também vão para outro lugar — peça para removerem o app deles desta conta."
        )
    if not r["nosso"]:
        return "O app do DaVinci NÃO aparece inscrito nesta conta: clique em Inscrever."
    return "Pronto: só o app do DaVinci está inscrito nesta conta."


async def inscrever(session: AsyncSession, conta: AtendimentoZapConta) -> dict[str, Any]:
    """`POST /{waba}/subscribed_apps` (ESCRITA na Meta) e a conferência pelo `GET`."""
    exigir_cadastro()
    app = await app_por_ref(session, conta.app_ref)
    token = _token_ou_recusa(conta)
    agora = _agora()
    try:
        async with zap_api.novo_cliente() as http:
            ok = await zap_api.inscrever_app(conta.waba_id, token, cliente=http)
            r = await conferir_inscricao(session, conta, app, token, cliente=http, agora=agora)
    except zap_api.ZapApiErro as exc:
        await _efeito_de_credencial(session, conta, exc)
        raise Recusa("zap_meta_recusou", f"A Meta não aceitou a inscrição: {exc}") from None
    if not ok:
        logger.warning("zap_inscrever_sem_sucesso", conta=str(conta.id))
    await session.flush()
    logger.warning("zap_inscrito", conta=str(conta.id), inscrito=r["inscrito"])
    return {**r, "frase": frase_da_inscricao(r)}


async def so_conferir_inscricao(
    session: AsyncSession, conta: AtendimentoZapConta
) -> dict[str, Any]:
    """Só o `GET /{waba}/subscribed_apps` (o botão "Conferir"; nada muda na Meta)."""
    exigir_cadastro()
    app = await app_por_ref(session, conta.app_ref)
    token = _token_ou_recusa(conta)
    try:
        async with zap_api.novo_cliente() as http:
            r = await conferir_inscricao(session, conta, app, token, cliente=http, agora=_agora())
    except zap_api.ZapApiErro as exc:
        await _efeito_de_credencial(session, conta, exc)
        raise Recusa("zap_meta_recusou", f"A Meta não respondeu: {exc}") from None
    await session.flush()
    return {**r, "frase": frase_da_inscricao(r)}


# ── O número ──────────────────────────────────────────────────────────────


async def numero_por_ref(session: AsyncSession, numero_ref: UUID | str) -> AtendimentoZapNumero:
    try:
        uid = UUID(str(numero_ref))
    except ValueError:
        raise Recusa("zap_numero_nao_encontrado", "Número não encontrado.", status=404) from None
    numero = await session.get(AtendimentoZapNumero, uid)
    if numero is None:
        raise Recusa("zap_numero_nao_encontrado", "Número não encontrado.", status=404)
    return numero


async def _canal_do_numero(session: AsyncSession, numero: AtendimentoZapNumero) -> AtendimentoCanal:
    canal = await canais_externos.garantir_canal(
        session,
        externo_ref=canais_externos.ref_do_zap(numero.phone_number_id),
        plataforma=PLATAFORMA_WHATSAPP,
        canal=CANAL_ZAP,
        nome=numero.nome_na_barra or numero.verified_name or numero.display_phone_number,
    )
    numero.canal_id = canal.id
    return canal


async def cadastrar_numero(
    session: AsyncSession,
    *,
    conta: AtendimentoZapConta,
    phone_number_id: str,
    nome_na_barra: str | None = None,
    marca_id: UUID | None = None,
    teto_dia: int | None = None,
) -> AtendimentoZapNumero:
    """O número escolhido: confere na Meta (só GET) e nasce em OBSERVAR, sem envio. Não commita."""
    exigir_cadastro()
    pnid = (phone_number_id or "").strip()
    if not _SO_DIGITOS.fullmatch(pnid):
        raise Recusa("zap_numero_invalido", "O ID do número é só números.")
    if await zap_contas.numero_por_pnid(session, pnid) is not None:
        raise Recusa("zap_numero_ja_existe", "Este número já está cadastrado.")
    app = await app_por_ref(session, conta.app_ref)
    token = _token_ou_recusa(conta)
    try:
        async with zap_api.novo_cliente() as http:
            brutos = await zap_api.listar_numeros(conta.waba_id, token, cliente=http)
    except zap_api.ZapApiErro as exc:
        await _efeito_de_credencial(session, conta, exc)
        raise Recusa("zap_meta_recusou", f"A Meta não respondeu: {exc}") from None
    achado = next((n for n in brutos if str(n.get("id") or "") == pnid), None)
    if achado is None:
        raise Recusa(
            "zap_numero_fora_da_conta",
            "Este número não aparece nesta conta do WhatsApp na Meta.",
        )
    display = str(achado.get("display_phone_number") or "")[:32]
    teste = eh_numero_de_teste(display)
    if not producao() and not teste:
        raise Recusa(
            "zap_so_teste_fora_de_producao",
            "Neste computador só entra o número de teste da Meta.",
        )
    if app.teste and not teste:
        raise Recusa(
            "zap_app_de_teste",
            "Este app é o de TESTE: ele só cadastra o número de teste. Número de verdade "
            'entra pelo app de produção ("DaVinci Zap").',
        )
    if marca_id is not None and await session.get(Marca, marca_id) is None:
        raise Recusa("zap_marca_nao_encontrada", "Marca não encontrada.", status=404)
    numero = AtendimentoZapNumero(
        conta_ref=conta.id,
        phone_number_id=pnid,
        display_phone_number=display or pnid,
        verified_name=(str(achado.get("verified_name") or "")[:120]) or None,
        nome_status=(str(achado.get("name_status") or "")[:24]) or None,
        nome_na_barra=((nome_na_barra or "").strip()[:120] or None),
        marca_id=marca_id,
        teste=teste,
        etapa="teste" if teste else "preparando",
        envio_ligado=False,
        teto_dia=teto_dia,
        saude={"qualidade": achado.get("quality_rating")} if achado.get("quality_rating") else {},
    )
    session.add(numero)
    await session.flush()
    canal = await _canal_do_numero(session, numero)
    canal.modo = MODO_OBSERVAR
    await zap_entrada.reabrir_eventos_do_numero(session, pnid)
    await session.flush()
    logger.info("zap_numero_cadastrado", numero=str(numero.id), teste=teste)
    return numero


async def ler_saude_do_numero(
    session: AsyncSession,
    numero: AtendimentoZapNumero,
    app: AtendimentoZapApp,
    token: str,
    *,
    cliente: httpx.AsyncClient,
    agora: datetime,
) -> list[dict[str, str]]:
    """`GET /{pnid}` → a `saude` do número (qualidade, limite, nome, plataforma, webhook).

    Devolve o "fora". Grava só o que é da Meta (nunca a URL de outro sistema:
    só o host). Se a Meta recusou campos e a leitura veio SEM o
    `webhook_configuration`, o "fora" anterior FICA (apagá-lo calaria o
    alarme sem ter conferido) e `webhook_conferido=False` vira aviso.
    """
    dados = await zap_api.ler_numero(numero.phone_number_id, token, cliente=cliente)
    cfg = dados.get("webhook_configuration")
    conferido = isinstance(cfg, dict)
    saude = dict(numero.saude or {})
    fora = (
        webhook_em_outro_lugar(cfg, url_do_webhook(app.app_id))
        if conferido
        else [o for o in (saude.get("webhook_outro") or []) if isinstance(o, dict)]
    )
    vazao = dados.get("throughput") if isinstance(dados.get("throughput"), dict) else {}
    saude.update(
        {
            "qualidade": dados.get("quality_rating") or saude.get("qualidade"),
            "limite": dados.get("messaging_limit_tier") or saude.get("limite"),
            "verificacao": dados.get("code_verification_status") or saude.get("verificacao"),
            "vazao": vazao.get("level") or saude.get("vazao"),
            "status_meta": dados.get("status") or saude.get("status_meta"),
            "plataforma": dados.get("platform_type") or saude.get("plataforma"),
            "webhook_outro": fora,
            "webhook_conferido": conferido,
            "testado_em": _iso(agora),
        }
    )
    saude.pop("testar_erro", None)
    numero.saude = saude
    if dados.get("verified_name"):
        numero.verified_name = str(dados["verified_name"])[:120]
    if dados.get("name_status"):
        numero.nome_status = str(dados["name_status"])[:24]
    if dados.get("display_phone_number"):
        numero.display_phone_number = str(dados["display_phone_number"])[:32]
    if fora:
        logger.error(
            "zap_webhook_em_outro_lugar", numero=str(numero.id), onde=[o["onde"] for o in fora]
        )
    return fora


async def testar(session: AsyncSession, numero: AtendimentoZapNumero) -> dict[str, Any]:
    """O botão "Testar conexão": lê a saúde do número na Meta (só GET) e confere o webhook.

    Mesmo só lendo, fala com a Meta com o token guardado: só com a chave do
    cadastro ligada (desligada, a tela só mostra o que já está no banco).
    """
    exigir_cadastro()
    conta = await conta_por_ref(session, numero.conta_ref)
    app = await app_por_ref(session, conta.app_ref)
    token = _token_ou_recusa(conta)
    agora = _agora()
    try:
        async with zap_api.novo_cliente() as http:
            fora = await ler_saude_do_numero(session, numero, app, token, cliente=http, agora=agora)
    except zap_api.ZapApiErro as exc:
        await _efeito_de_credencial(session, conta, exc)
        numero.saude = {**(numero.saude or {}), "testar_erro": str(exc)[:300]}
        await session.flush()
        raise Recusa("zap_meta_recusou", f"O teste falhou: {frase_da_meta(exc)}") from None
    conta.ultimo_ok_em = agora
    await session.flush()
    return {"webhook_outro": [o["onde"] for o in fora], "frase": frase_do_teste(numero, fora)}


def frase_do_teste(numero: AtendimentoZapNumero, fora: list[dict[str, str]]) -> str:
    """A frase do "Testar conexão": só diz "vêm para o DaVinci" com tudo conferido. PURA."""
    partes = [f for _c, f in problemas_do_numero(numero)]
    webhook = frase_do_webhook(fora)
    if webhook:
        partes.insert(0, webhook.rstrip("."))
    if partes:
        return "A Meta respondeu, mas: " + "; ".join(partes) + "."
    return "Conexão ok: a Meta respondeu e as mensagens deste número vêm para o DaVinci."


def normalizar_destinos(lista: list[str]) -> list[str]:
    saida: list[str] = []
    for d in lista:
        norm = zap_contas.normalizar_wa_id(d)
        if norm is None:
            raise Recusa(
                "zap_destino_invalido",
                "Número de teste inválido: só dígitos, com o 55 e o DDD.",
                status=422,
            )
        saida.append(norm)
    return sorted(set(saida))


async def alterar_numero(
    session: AsyncSession, numero: AtendimentoZapNumero, campos: dict[str, Any]
) -> AtendimentoZapNumero:
    """Etapa, caso, envio, lista de teste, teto, marca, nome, observação e PIN. Não commita.

    Ligar o envio só nas etapas que mandam (`teste`, `ativo`) — a trava 7 do
    envio recusaria do mesmo jeito, mas a tela não pode dizer "ligado" de um
    número que não manda. Pausado/desligado tiram o envio junto.
    """
    exigir_cadastro()
    agora = _agora()
    if "etapa" in campos:
        etapa = campos["etapa"]
        if etapa not in ZAP_NUMERO_ETAPAS:
            raise Recusa("zap_etapa_invalida", "Etapa desconhecida.", status=422)
        numero.etapa = etapa
        if etapa in _ETAPAS_QUE_PAUSAM:
            numero.envio_ligado = False
        if etapa == "ativo" and numero.migrado_em is None:
            numero.migrado_em = agora
    if "caso_migracao" in campos:
        caso = campos["caso_migracao"]
        if caso is not None and caso not in ZAP_NUMERO_CASOS:
            raise Recusa("zap_caso_invalido", "O caso é A, A2 ou B.", status=422)
        numero.caso_migracao = caso
    if "destinos_teste" in campos:
        numero.destinos_teste = normalizar_destinos(list(campos["destinos_teste"] or []))
    if "teto_dia" in campos:
        teto = campos["teto_dia"]
        if teto is not None and int(teto) < 0:
            raise Recusa("zap_teto_invalido", "O teto do dia não pode ser negativo.", status=422)
        numero.teto_dia = teto
    if "marca_id" in campos:
        marca = campos["marca_id"]
        if marca is not None and await session.get(Marca, marca) is None:
            raise Recusa("zap_marca_nao_encontrada", "Marca não encontrada.", status=404)
        numero.marca_id = marca
    if "observacao" in campos:
        numero.observacao = (campos["observacao"] or "").strip()[:2000] or None
    if "pin" in campos:
        pin = (campos["pin"] or "").strip()
        if pin and not _PIN.fullmatch(pin):
            raise Recusa("zap_pin_invalido", "O PIN tem 6 números.", status=422)
        numero.pin_enc = encrypt_json({"pin": pin}) if pin else None
    if "nome_na_barra" in campos:
        numero.nome_na_barra = (campos["nome_na_barra"] or "").strip()[:120] or None
        await _canal_do_numero(session, numero)
    if campos.get("envio_ligado") is True:
        if numero.etapa not in ETAPAS_QUE_ENVIAM:
            raise Recusa(
                "zap_etapa_nao_envia",
                "Este número ainda não manda mensagens nesta etapa: mude para Teste ou "
                "No DaVinci antes de ligar o envio.",
            )
        numero.envio_ligado = True
    elif campos.get("envio_ligado") is False:
        numero.envio_ligado = False
    await session.flush()
    logger.info(
        "zap_numero_alterado",
        numero=str(numero.id),
        campos=sorted(k for k in campos if k != "pin"),
    )
    return numero


async def pausar(session: AsyncSession, numero: AtendimentoZapNumero) -> AtendimentoZapNumero:
    """O botão "Pausar" (vale na hora; não precisa da chave do cadastro)."""
    numero.envio_ligado = False
    await session.flush()
    logger.warning("zap_numero_pausado", numero=str(numero.id))
    return numero


async def pausar_todos(session: AsyncSession) -> int:
    """O botão de emergência: TODOS os números param de mandar, na hora."""
    res = await session.execute(
        update(AtendimentoZapNumero)
        .where(AtendimentoZapNumero.envio_ligado.is_(True))
        .values(envio_ligado=False)
        .execution_options(synchronize_session=False)
    )
    n = int(res.rowcount or 0)
    logger.warning("zap_pausar_todos", numeros=n)
    return n


def ver_pin(numero: AtendimentoZapNumero) -> str | None:
    if numero.pin_enc is None:
        return None
    return str(decrypt_json(numero.pin_enc).get("pin") or "") or None


async def registrar(
    session: AsyncSession, numero: AtendimentoZapNumero, *, pin: str | None = None
) -> dict[str, Any]:
    """O botão "Registrar" — UMA tentativa por clique. COMMITA a tentativa antes da Meta.

    Se a Meta não responde, a tentativa conta do mesmo jeito (ela pode ter
    chegado lá). 131031/133016 travam o botão (`zap_erros.aplicar_efeito`).
    """
    exigir_cadastro()
    agora = _agora()
    if pin is not None and pin.strip() and not _PIN.fullmatch(pin.strip()):
        raise Recusa("zap_pin_invalido", "O PIN tem 6 números.", status=422)
    # Trava o número até o commit da tentativa: dois cliques (ou duas abas)
    # juntos não leem o mesmo contador e não vão os dois à Meta.
    travado = await session.get(
        AtendimentoZapNumero, numero.id, with_for_update=True, populate_existing=True
    )
    if travado is None:
        raise Recusa("zap_numero_nao_encontrado", "Número não encontrado.", status=404)
    numero = travado
    if pin is not None and pin.strip():
        numero.pin_enc = encrypt_json({"pin": pin.strip()})
    estado = estado_do_registro(numero, agora)
    if not estado.pode:
        raise Recusa("zap_registro_bloqueado", estado.motivo or "Registrar não está liberado.")
    conta = await conta_por_ref(session, numero.conta_ref)
    token = _token_ou_recusa(conta)
    pin_usado = ver_pin(numero) or ""
    numero_id, pnid, conta_id = numero.id, numero.phone_number_id, conta.id
    numero.registro_tentativas = [*_tentativas_recentes(numero, agora), agora]
    await session.commit()
    logger.warning(
        "zap_registrar_tentativa", numero=str(numero_id), tentativa=estado.tentativas_72h + 1
    )
    try:
        async with zap_api.novo_cliente() as http:
            ok = await zap_api.registrar_numero(pnid, token, pin_usado, cliente=http)
    except zap_api.ZapApiErro as exc:
        info = zap_erros.traduzir(exc.code) if exc.code is not None else None
        efeito = info.efeito if info is not None else None
        credencial = efeito in (zap_erros.EFEITO_TOKEN_INVALIDO, zap_erros.EFEITO_SEM_PERMISSAO)
        if credencial or efeito in (
            zap_erros.EFEITO_PAUSAR_E_TRAVAR_REGISTRO,
            zap_erros.EFEITO_TRAVAR_REGISTRO,
        ):
            await zap_erros.aplicar_efeito(
                session,
                efeito,
                numero_id=None if credencial else numero_id,
                conta_id=conta_id if credencial else None,
                code=exc.code,
                origem="api",
                agora=agora,
            )
            await session.commit()
        raise Recusa(
            "zap_registro_falhou", f"O registro não deu certo: {frase_da_meta(exc)}"
        ) from None
    numero = await numero_por_ref(session, numero_id)
    saude = {**(numero.saude or {}), "registrado_em": _iso(agora)}
    if ok:
        # Registrado: o "não registrado" (131045/133010 e a plataforma fora da
        # API) sai na hora — a pill, a barra e o alarme do cron param de dizer.
        erro = saude.get("erro_meta") if isinstance(saude.get("erro_meta"), dict) else {}
        if erro.get("code") in CODIGOS_NAO_REGISTRADO:
            saude.pop("erro_meta", None)
            if numero.saude_status == "erro":
                numero.saude_status = "ok"
        if str(saude.get("plataforma") or "").upper() != PLATAFORMA_DA_API:
            saude.pop("plataforma", None)
    numero.saude = saude
    await session.flush()
    return {
        "ok": bool(ok),
        "frase": "Número registrado na API do WhatsApp."
        if ok
        else "A Meta respondeu sem confirmar o registro: confira com Testar conexão.",
    }


# ── Eventos parados ───────────────────────────────────────────────────────


async def eventos_parados(session: AsyncSession) -> dict[str, Any]:
    """Quantos eventos caíram fora (número/conta desconhecidos, JSON inválido, erro)."""
    linhas = (
        await session.execute(
            select(AtendimentoZapEvento.resultado, func.count(AtendimentoZapEvento.id))
            .where(AtendimentoZapEvento.resultado.in_(RESULTADOS_REPROCESSAVEIS))
            .group_by(AtendimentoZapEvento.resultado)
        )
    ).all()
    por = {r: int(n) for r, n in linhas}
    return {"total": sum(por.values()), "por_resultado": por}


async def reprocessar_evento(session: AsyncSession, evento_id: int) -> None:
    """Põe UM evento parado de volta na fila. JSON inválido: tenta ler o corpo de novo."""
    exigir_cadastro()
    ev = await session.get(AtendimentoZapEvento, evento_id)
    if ev is None:
        raise Recusa("zap_evento_nao_encontrado", "Evento não encontrado.", status=404)
    if ev.resultado not in RESULTADOS_REPROCESSAVEIS:
        raise Recusa(
            "zap_evento_nao_reprocessa",
            "Este evento já foi gravado (ou não é dos que voltam para a fila).",
        )
    if ev.resultado == zap_entrada.R_JSON_INVALIDO:
        import json

        cru = (ev.payload or {}).get("corpo")
        try:
            corpo = json.loads(cru) if isinstance(cru, str) else None
        except ValueError:
            corpo = None
        if not isinstance(corpo, dict):
            raise Recusa("zap_corpo_invalido", "O corpo continua inválido: não dá para ler.")
        itens = zap_entrada.itens_do_corpo(corpo)
        await zap_entrada.gravar_itens(session, itens, app_ref=ev.app_ref)
        return
    await zap_entrada.reabrir_evento(session, ev)


async def reprocessar_parados(session: AsyncSession) -> int:
    """O botão "Reprocessar ignorados": reabre o que a fila pode tentar de novo (não o JSON)."""
    exigir_cadastro()
    res = await session.execute(
        update(AtendimentoZapEvento)
        .where(
            AtendimentoZapEvento.resultado.in_(
                [r for r in RESULTADOS_REPROCESSAVEIS if r != zap_entrada.R_JSON_INVALIDO]
            )
        )
        .values(processado_em=None, proxima_em=None, resultado=None, tentativas=0, erro=None)
        .execution_options(synchronize_session=False)
    )
    return int(res.rowcount or 0)


# ── LGPD: apagar os dados de um contato (§10.5) ───────────────────────────


def _variantes_do_telefone(*valores: str | None) -> set[str]:
    """O telefone como pode ter vindo nos eventos: como veio, normalizado e sem o 9º dígito."""
    out: set[str] = set()
    for v in valores:
        if not v:
            continue
        digitos = re.sub(r"\D", "", v)
        if not digitos:
            continue
        out.add(digitos)
        norm = zap_contas.normalizar_wa_id(digitos)
        if norm:
            out.add(norm)
            if len(norm) == 13 and norm.startswith("55") and norm[4] == "9":
                out.add(norm[:4] + norm[5:])
    return out


# O que fica no `payload.zap` de uma mensagem apagada: só o que não é do
# cliente (a entrega — status e horários — e o tipo).
_ZAP_QUE_FICA = ("tipo_meta", "entrega", "conciliado")


def _payload_limpo(payload: dict | None) -> dict:
    p = dict(payload or {})
    z = p.get("zap")
    if isinstance(z, dict):
        p["zap"] = {k: v for k, v in z.items() if k in _ZAP_QUE_FICA}
    return p


def _anexos_limpos(anexos: list | None) -> list:
    saida = []
    for a in anexos or []:
        if isinstance(a, dict):
            saida.append(
                {
                    k: v
                    for k, v in {
                        "tipo": a.get("tipo"),
                        "midia_id": a.get("midia_id"),
                        # O balão diz "apagada a pedido do cliente" (a
                        # retenção de 180 dias diz "mídia expirada").
                        "status": STATUS_ANEXO_APAGADO if a.get("midia_id") else a.get("status"),
                    }.items()
                    if v is not None
                }
            )
    return saida


async def apagar_contato(session: AsyncSession, contato_id: UUID) -> dict[str, int]:
    """O pedido de exclusão (LGPD, §10.5). Varre TUDO e COMMITA; o arquivo some depois do commit.

    - o contato: telefone, BSUID e nome anonimizados, `apagado_em` (a trava 10
      do envio não deixa mais mandar nada para ele);
    - a conversa: comprador, avatar, o resumo da última mensagem e o `dados`
      do Zap (CTWA, BSUID);
    - as mensagens (de todos os autores): o texto vira "[apagado a pedido]",
      o `payload.zap` perde referral, localização, vCard e citação, e o anexo
      perde nome e URL;
    - os rascunhos da conversa;
    - as mídias: o arquivo some, o nome do arquivo sai, o registro fica
      `apagada`;
    - os pares (pergunta/resposta generalizadas) e o replay do agente da
      conversa — o indexador não reindexa a conversa apagada sozinho;
    - os eventos do webhook daquele contato (mensagens, status e
      preferências — pelos wamids da conversa e pelo telefone/BSUID) e os
      `ignorado` sem número cujo corpo cru cita o telefone ou o BSUID.
    As três tabelas do Zap com dado do cliente estão fora do gatilho do
    Histórico, e as do atendimento que mudam aqui também: nada é copiado.
    """
    contato = await session.get(AtendimentoZapContato, contato_id, with_for_update=True)
    if contato is None:
        raise Recusa("zap_contato_nao_encontrado", "Contato não encontrado.", status=404)
    numero = await session.get(AtendimentoZapNumero, contato.numero_ref)
    agora = _agora()
    telefones = _variantes_do_telefone(contato.wa_id, contato.wa_id_norm)
    bsuid = contato.bsuid
    conversa_id = contato.conversa_id
    wamids: list[str] = []
    mensagens = 0
    caminhos: list[str] = []
    midias = 0
    if conversa_id is not None:
        conversa = await session.get(AtendimentoConversa, conversa_id, with_for_update=True)
        msgs = list(
            (
                await session.execute(
                    select(AtendimentoMensagem)
                    .where(AtendimentoMensagem.conversa_id == conversa_id)
                    .with_for_update()
                )
            )
            .scalars()
            .all()
        )
        for m in msgs:
            if m.externo_id:
                wamids.append(m.externo_id)
            m.texto = TEXTO_APAGADO
            m.payload = _payload_limpo(m.payload)
            m.anexos = _anexos_limpos(m.anexos)
        mensagens = len(msgs)
        ids = [m.id for m in msgs]
        if ids:
            for midia in (
                (
                    await session.execute(
                        select(AtendimentoZapMidia)
                        .where(AtendimentoZapMidia.mensagem_id.in_(ids))
                        .with_for_update()
                    )
                )
                .scalars()
                .all()
            ):
                if midia.caminho:
                    caminhos.append(midia.caminho)
                midia.caminho = None
                midia.nome_original = None
                midia.status = zap_midia.M_APAGADA
                midia.proxima_em = None
                midias += 1
        await session.execute(
            update(AtendimentoRascunho)
            .where(AtendimentoRascunho.conversa_id == conversa_id)
            .values(texto=TEXTO_APAGADO, motivo=None, fatos={})
            .execution_options(synchronize_session=False)
        )
        if conversa is not None:
            conversa.comprador_id = None
            conversa.comprador_nome = None
            conversa.comprador_avatar = None
            conversa.ultima_mensagem_resumo = TEXTO_APAGADO
            dados = dict(conversa.dados or {})
            dados.pop("comprador_plataforma", None)
            zap_dados = dados.get("zap") if isinstance(dados.get("zap"), dict) else {}
            dados["zap"] = {
                k: v for k, v in zap_dados.items() if k in ("numero_id", "contato_id")
            } | {"apagado_em": _iso(agora)}
            conversa.dados = dados
        # O que o agente guardou da conversa (até 180 dias): sai junto.
        await session.execute(
            delete(AtendimentoPar).where(AtendimentoPar.conversa_id == conversa_id)
        )
        await session.execute(
            delete(AtendimentoAgenteReplay).where(
                AtendimentoAgenteReplay.conversa_id == conversa_id
            )
        )
    # Os eventos do webhook daquele contato (até 30 dias guardados).
    ev = 0
    if numero is not None:
        p = AtendimentoZapEvento.payload
        alvos = []
        if wamids:
            alvos.append(AtendimentoZapEvento.chave.in_([f"m:{w}" for w in wamids]))
            alvos.append(p["status"]["id"].astext.in_(wamids))
        if telefones:
            lista = sorted(telefones)
            alvos += [
                p["mensagem"]["from"].astext.in_(lista),
                p["contato"]["wa_id"].astext.in_(lista),
                p["status"]["recipient_id"].astext.in_(lista),
                p["preferencia"]["wa_id"].astext.in_(lista),
            ]
        if bsuid:
            alvos += [
                p["mensagem"]["from_user_id"].astext == bsuid,
                p["contato"]["user_id"].astext == bsuid,
                p["status"]["recipient_user_id"].astext == bsuid,
                p["preferencia"]["user_id"].astext == bsuid,
            ]
        if alvos:
            res = await session.execute(
                delete(AtendimentoZapEvento).where(
                    and_(
                        AtendimentoZapEvento.phone_number_id == numero.phone_number_id,
                        or_(*alvos),
                    )
                )
            )
            ev = int(res.rowcount or 0)
    # Os `ignorado` sem número (JSON inválido, objeto inesperado) guardam o
    # corpo CRU: saem os que citam o telefone ou o BSUID (são raros).
    crus = [
        AtendimentoZapEvento.payload["corpo"].astext.contains(t, autoescape=True)
        for t in sorted(telefones) + ([bsuid] if bsuid else [])
    ]
    if crus:
        res = await session.execute(
            delete(AtendimentoZapEvento).where(
                AtendimentoZapEvento.phone_number_id.is_(None), or_(*crus)
            )
        )
        ev += int(res.rowcount or 0)
    contato.wa_id = contato.wa_id_norm = contato.bsuid = contato.nome_perfil = None
    contato.bloqueado = True
    contato.apagado_em = contato.apagado_em or agora
    await session.commit()
    for rel in caminhos:
        zap_midia.remover_arquivo(rel)
    # Só ids e contagens no log (nunca o que foi apagado).
    logger.warning(
        "zap_contato_apagado",
        contato=str(contato_id),
        mensagens=mensagens,
        midias=midias,
        eventos=ev,
    )
    return {"mensagens": mensagens, "midias": midias, "eventos": ev}


async def apagar_por_telefone(session: AsyncSession, telefone: str) -> dict[str, int]:
    """O pedido de exclusão pelo TELEFONE (o campo da tela "Números do WhatsApp").

    Acha TODOS os contatos daquele telefone — com e sem o 9º dígito, com ou
    sem o 55, em TODOS os números da loja (quem falou com dois números tem
    dois contatos) — e apaga cada um (`apagar_contato`, que commita). Nenhum
    achado → 404 (nada apagado). O telefone não vai para o log.
    """
    digitos = re.sub(r"\D", "", telefone or "")
    if len(digitos) in (10, 11):  # DDD + número, sem o 55
        digitos = f"55{digitos}"
    if not 12 <= len(digitos) <= 15:
        raise Recusa(
            "zap_telefone_invalido",
            "Digite o telefone com DDD (ex.: 55 11 98765-4321).",
            status=422,
        )
    variantes = sorted(_variantes_do_telefone(digitos))
    ids = list(
        (
            await session.execute(
                select(AtendimentoZapContato.id)
                .where(
                    or_(
                        AtendimentoZapContato.wa_id_norm.in_(variantes),
                        AtendimentoZapContato.wa_id.in_(variantes),
                    )
                )
                .order_by(AtendimentoZapContato.id)
            )
        )
        .scalars()
        .all()
    )
    if not ids:
        raise Recusa(
            "zap_contato_nao_encontrado",
            "Nenhum contato do WhatsApp com este telefone (já apagado ou nunca falou com a loja).",
            status=404,
        )
    total = {"contatos": 0, "mensagens": 0, "midias": 0, "eventos": 0}
    for contato_id in ids:
        r = await apagar_contato(session, contato_id)
        total["contatos"] += 1
        for k in ("mensagens", "midias", "eventos"):
            total[k] += r[k]
    return total
