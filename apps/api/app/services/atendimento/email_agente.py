"""O AGENTE do atendimento respondendo E-MAIL — a extensão do atendimento (12/11/2026).

O Eduardo: "a chave usa mesma do atendimento e como se fosse a extensao do
atendimento pro email so" e "ue ve o agente do atendimento e igual ele so que
pros emails". Então é o MESMO agente (Claude com as ferramentas, os fatos, as
respostas da equipe, as checagens e o verificador — `ia._gerar` →
`agente.responder`), com a MESMA chave:

  loja (e-mail que a ponte ligou a uma integração) → o canal da loja onde o
      cliente fala depois da compra (`CANAL_DA_CHAVE`: chat na Shopee/TikTok,
      pós-venda no ML, SAC na Magalu, e-mail na Amazon). O botão "Agente
      (Claude)" e o modo desse canal (copiloto = sugere; automático = sai
      sozinho nos assuntos liberados da loja) valem para o e-mail dela;
  site (o SAC da Uranyx e da Charlots, sem loja) → a linha do site no
      atendimento (`site:<site>`), ligada pela aba E-mail › Agente (só admin).

O que é SÓ do e-mail fica aqui:
  - a TRAVA (`motivo_fora`): o agente nunca responde caixa privada nem
    segurança (essas nem viram conversa — `ponte`), nem o histórico de vendas,
    o aviso de plataforma, o remetente suspeito ou o encaminhado; no site, só a
    caixa SAC;
  - as REGRAS do e-mail (`mail_agente_regras`, aba E-mail › Agente) e o
    formulário de SAC do site (`FORMULARIOS_SAC`), que entram no pacote do
    agente (`bloco_do_pacote`);
  - o envio: pelo MESMO caminho da pessoa (`responder.enfileirar_resposta`: a
    fila da Central, a assinatura da marca/loja, a citação), com o usuário do
    agente (`usuario_do_agente`) — a Central exige um autor.

A liberação vale por conversa e por rodada (`liberar`, um ContextVar): o
`ia._gerar`, o `_talvez_enviar`, o verificador e a triagem da Caixa Humano
perguntam `liberada(conversa)` no lugar da trava antiga
(`constantes.MOTIVO_EMAIL_SO_PESSOA`, que segue valendo para todo o resto). O
envio relê a chave do banco (`chave_liberada`), nunca confia só na marca.

Nada aqui commita, exceto `gerar_pendentes` (pelo `ia.gerar_rascunho`).
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import AtendimentoCanal, AtendimentoConversa, User, UserRole, UserStatus
from app.models.mail_agente import MailAgenteRegra
from app.services.atendimento import agente_config
from app.services.atendimento.constantes import (
    CANAL_EMAIL,
    CONVERSA_BLOQUEADA,
    CONVERSA_FECHADA,
    FONTE_TUTA,
    MODO_AUTO,
    MODO_COPILOTO,
    PLATAFORMA_SITE,
    URL_SITE,
)

logger = structlog.get_logger()

# As LOJAS ficam de fora por enquanto (o dono, 12/11/2026: "deixe por eqt so os sites");
# ligar aqui é o e-mail da loja seguir a chave do atendimento dela.
LOJAS_LIGADAS = False
# O canal da loja cuja chave vale para o e-mail dela (o "depois da compra").
CANAL_DA_CHAVE: dict[str, str] = {
    "shopee": "chat",
    "tiktok": "chat",
    "ml": "pos_venda",
    "magalu": "sac",
    "amazon": "email",
    "carrefour": "chat",
}
# A marca do e-mail do site (`dados.mail.marca`) → o site da linha do atendimento.
# A Charlots do chamado é a `charlots-park` (a assinatura é a da `poofy`).
SITE_DA_MARCA: dict[str, str] = {
    "uranyx": "uranyx",
    "charlots": "charlots",
    "charlots-park": "charlots",
    "poofy": "charlots",
}
# Os modos em que o agente escreve: copiloto (sugere) e automático (sai sozinho).
MODOS_DO_AGENTE = (MODO_COPILOTO, MODO_AUTO)
# Só a caixa SAC do site (a Admin/dúvidas/atacado fica com a pessoa).
CAIXA_SAC = "sac"
# A regra que o código também conhece (a semente da 0477).
CHAVE_FORMULARIO = "formulario_sac"


@dataclass(frozen=True)
class Formulario:
    """O formulário de SAC do site (levantado nas páginas em 12/11/2026)."""

    url: str
    campos: tuple[str, ...]


FORMULARIOS_SAC: dict[str, Formulario] = {
    "uranyx": Formulario(
        url="https://uranyx.com.br/sac",
        campos=(
            "dados do comprador: nome completo (como na compra), CPF, e-mail, "
            "telefone/WhatsApp, CEP e endereço completo",
            "dados da compra: onde comprou (site, marketplace ou loja), nome da loja/vendedor, "
            "número do pedido, data da compra, data de recebimento e nota fiscal (se tiver)",
            "dados do produto: modelo, cor, voltagem e número de série ou lote (se houver)",
            "o defeito: quando apareceu, a descrição detalhada, se houve queda, líquido ou "
            "voltagem diferente, e o tipo de conexão usada (tomada, extensão ou adaptador)",
            "documentação: foto do produto completo, da etiqueta de identificação, da "
            "embalagem, do comprovante do pedido, fotos do defeito e um vídeo curto do defeito",
        ),
    ),
    "charlots": Formulario(
        url="https://charlots.com.br/sac",
        campos=(
            "dados do comprador: nome completo (como na compra), CPF, e-mail, "
            "telefone/WhatsApp, CEP e endereço completo",
            "dados da compra: onde comprou (site, marketplace ou loja), nome da loja/vendedor, "
            "número do pedido, data da compra, data de recebimento e número da nota fiscal",
            "dados da mala: modelo, cor, tamanho e o código/etiqueta (se houver)",
            "o defeito: quando apareceu, a descrição detalhada e se houve queda, impacto forte, "
            "excesso de peso ou contato com líquidos",
            "documentação: foto do produto completo, da etiqueta/código, da embalagem, do "
            "comprovante do pedido, fotos do defeito e um vídeo curto do defeito",
        ),
    ),
}

# Por que o agente não responde este e-mail (os códigos de `motivo_fora`).
FORA_NAO_E_EMAIL = "nao_e_email_da_ponte"
FORA_SEM_LOJA = "sem_loja"
FORA_CAIXA = "caixa_nao_e_sac"
FORA_SEM_EMAIL = "sem_email"
FORA_SUSPEITO = "remetente_suspeito"
FORA_HISTORICO = "historico_de_vendas"
FORA_NAO_E_CLIENTE = "nao_e_cliente"
FORA_ENCAMINHADO = "encaminhado"
FORA_INFORMATIVO = "informativo"
FORA_EM_MASSA = "em_massa"

# O usuário do agente: o autor do job na fila da Central (sem senha: ninguém loga).
OPEN_ID_AGENTE = "system:agente-email"
EMAIL_AGENTE = "agente-email@hadken.com"
NOME_AGENTE = "Agente de e-mail (DaVinci)"


@dataclass(frozen=True)
class Chave:
    """A chave que liberou o agente nesta conversa de e-mail (relida a cada uso)."""

    canal_id: UUID
    modo: str
    site: str | None = None
    via_formulario: bool = False
    # A equipe já respondeu nesta conversa (um atendimento em andamento).
    em_andamento: bool = False

    @property
    def formulario(self) -> Formulario | None:
        return FORMULARIOS_SAC.get(self.site or "")


# ── A liberação da rodada ─────────────────────────────────────────────────

_LIBERADAS: ContextVar[dict[UUID, Chave] | None] = ContextVar(
    "atendimento_email_agente", default=None
)


@contextmanager
def liberar(conversa_id: UUID, chave: Chave) -> Iterator[None]:
    """Durante o bloco, a conversa de e-mail é do agente (com esta chave)."""
    token = _LIBERADAS.set({**(_LIBERADAS.get() or {}), conversa_id: chave})
    try:
        yield
    finally:
        _LIBERADAS.reset(token)


def chave_da(conversa: Any) -> Chave | None:
    """A chave liberada nesta rodada para a conversa (None = a trava de sempre)."""
    conversa_id = getattr(conversa, "id", None)
    return (_LIBERADAS.get() or {}).get(conversa_id) if conversa_id is not None else None


def liberada(conversa: Any) -> bool:
    return chave_da(conversa) is not None


def canal_id(conversa: Any) -> UUID | None:
    """O canal da CHAVE da conversa: o dela, ou o da loja/site no e-mail liberado."""
    proprio = getattr(conversa, "canal_id", None)
    if proprio is not None:
        return proprio
    chave = chave_da(conversa)
    return chave.canal_id if chave is not None else None


# ── Quem é o e-mail ───────────────────────────────────────────────────────


def e_email_da_ponte(conversa: Any) -> bool:
    dados = getattr(conversa, "dados", None)
    dados = dados if isinstance(dados, dict) else {}
    return (
        getattr(conversa, "canal", None) == CANAL_EMAIL
        and dados.get("fonte") == FONTE_TUTA
        and isinstance(dados.get("mail"), dict)
    )


def _mail(conversa: Any) -> dict:
    dados = getattr(conversa, "dados", None)
    mail = dados.get("mail") if isinstance(dados, dict) else None
    return mail if isinstance(mail, dict) else {}


def site_da_conversa(conversa: Any) -> str | None:
    """O site (uranyx/charlots) da conversa de e-mail do site; None nas outras. PURA."""
    if getattr(conversa, "plataforma", None) != PLATAFORMA_SITE:
        return None
    return SITE_DA_MARCA.get(str(_mail(conversa).get("marca") or "").strip().lower())


def via_formulario(conversa: Any) -> bool:
    """O atendimento já veio pelo formulário de SAC do site (tem protocolo)? PURA."""
    return bool(str(_mail(conversa).get("protocolo") or "").strip())


async def canal_da_chave(session: AsyncSession, conversa: Any) -> AtendimentoCanal | None:
    """O canal cuja chave vale para este e-mail (None = nenhuma)."""
    if not e_email_da_ponte(conversa):
        return None
    if conversa.plataforma == PLATAFORMA_SITE:
        site = site_da_conversa(conversa)
        if site is None:
            return None
        return await canal_do_site(session, site)
    if not LOJAS_LIGADAS:
        # O dono, 12/11/2026: "deixe por enquanto só os sites" — várias lojas estão no
        # automático do atendimento e o e-mail delas ficaria respondido sem ninguém ver.
        return None
    nome = CANAL_DA_CHAVE.get(conversa.plataforma or "")
    if conversa.integration_id is None or nome is None:
        return None
    return await session.scalar(
        select(AtendimentoCanal).where(
            AtendimentoCanal.integration_id == conversa.integration_id,
            AtendimentoCanal.canal == nome,
        )
    )


async def canal_do_site(session: AsyncSession, site: str) -> AtendimentoCanal | None:
    """A linha do site no atendimento (`site:<site>`), a chave do e-mail do SAC dele."""
    return await session.scalar(
        select(AtendimentoCanal)
        .where(AtendimentoCanal.externo_ref == f"site:{site}")
        .order_by(AtendimentoCanal.created_at)
        .limit(1)
    )


async def motivo_fora(session: AsyncSession, conversa: Any) -> str | None:
    """Por que o agente nunca responde este e-mail (o código), ou None.

    A caixa PRIVADA e a SEGURANÇA nem viram conversa (a ponte). Aqui: só o
    e-mail de cliente, de loja com integração ou da caixa SAC do site, não
    suspeito, não histórico de vendas, não encaminhado.
    """
    from app.services.mail_atendimento import responder
    from app.services.mail_atendimento.constantes import (
        FINALIDADE_VENDAS,
        REMETENTE_COMPRADOR,
        SEM_LOJA_ENCAMINHADO,
    )

    if not e_email_da_ponte(conversa):
        return FORA_NAO_E_EMAIL
    mail = _mail(conversa)
    if conversa.plataforma == PLATAFORMA_SITE:
        if site_da_conversa(conversa) is None:
            return FORA_SEM_LOJA
        if str(mail.get("caixa") or "").strip().lower() != CAIXA_SAC:
            return FORA_CAIXA
    elif conversa.integration_id is None:
        return FORA_SEM_LOJA
    if mail.get("tipo_email") == "informativo":
        return FORA_INFORMATIVO
    achado = await responder.email_a_responder(session, conversa)
    if achado is None:
        return FORA_SEM_EMAIL
    meta, _message = achado
    if meta.suspeito:
        return FORA_SUSPEITO
    if meta.finalidade == FINALIDADE_VENDAS:
        return FORA_HISTORICO
    if meta.remetente_tipo != REMETENTE_COMPRADOR:
        return FORA_NAO_E_CLIENTE
    if meta.motivo == SEM_LOJA_ENCAMINHADO:
        return FORA_ENCAMINHADO
    if parece_em_massa(await _ultima_do_cliente(session, conversa)):
        return FORA_EM_MASSA
    return None


# O e-mail EM MASSA (levantamento do SAC de 12/11/2026: a maior parte do sac@ é
# propaganda, aviso de sistema e notificação): nem vai ao modelo (custo e risco).
_MARCAS_EM_MASSA = (
    "abrir no navegador",
    "ver no navegador",
    "veja online",
    "não consegue visualizar",
    "nao consegue visualizar",
    "caso não esteja visualizando",
    "configurar recebimento de e-mails",
    "unsubscribe",
    "descadastr",
    "cancelar inscrição",
    "rather not get more emails",
    "responder a este e-mail escr",
    "/// painel",
    "abrir no painel",
)
_RX_INVISIVEIS = re.compile("[\u034f\u200b-\u200f\u00ad\ufeff]")


def parece_em_massa(texto: str | None) -> bool:
    """Newsletter, propaganda, aviso de sistema ou notificação do painel do site? PURA."""
    t = (texto or "").lower()
    if any(m in t for m in _MARCAS_EM_MASSA):
        return True
    return len(_RX_INVISIVEIS.findall(t)) >= 8


async def _ultima_do_cliente(session: AsyncSession, conversa: Any) -> str | None:
    from app.models import AtendimentoMensagem
    from app.services.atendimento.constantes import AUTOR_CLIENTE

    return await session.scalar(
        select(AtendimentoMensagem.texto)
        .where(
            AtendimentoMensagem.conversa_id == conversa.id,
            AtendimentoMensagem.autor == AUTOR_CLIENTE,
        )
        .order_by(AtendimentoMensagem.created_at.desc())
        .limit(1)
    )


async def em_andamento(session: AsyncSession, conversa: Any) -> bool:
    """A equipe (ou o agente) já respondeu nesta conversa?"""
    from app.models import AtendimentoMensagem
    from app.services.atendimento.constantes import AUTOR_LOJA

    return bool(
        await session.scalar(
            select(AtendimentoMensagem.id)
            .where(
                AtendimentoMensagem.conversa_id == conversa.id,
                AtendimentoMensagem.autor == AUTOR_LOJA,
            )
            .limit(1)
        )
    )


async def chave_liberada(session: AsyncSession, conversa: Any) -> Chave | None:
    """A chave do agente LIGADA para este e-mail (relida do banco), ou None.

    Ligada = o interruptor geral e a chave Claude (`agente_config.agente_ligado`),
    o botão do canal da chave, o modo copiloto ou automático, e a trava do
    e-mail (`motivo_fora`) sem motivo.
    """
    s = get_settings()
    if not (s.atendimento_ia_ativa and s.atendimento_agente_ativo):
        return None
    if agente_config.chave_do_agente(s) is None or not e_email_da_ponte(conversa):
        return None
    canal = await canal_da_chave(session, conversa)
    if canal is None or canal.modo not in MODOS_DO_AGENTE:
        return None
    if not agente_config.agente_ligado(s, canal):
        return None
    if await motivo_fora(session, conversa) is not None:
        return None
    return Chave(
        canal_id=canal.id,
        modo=canal.modo,
        site=site_da_conversa(conversa),
        via_formulario=via_formulario(conversa),
        em_andamento=await em_andamento(session, conversa),
    )


# ── As regras e o pacote ──────────────────────────────────────────────────


def id_da_regra(regra: Any) -> str:
    """O id que o agente cita em `evidencias` quando segue a regra ("email:formulario_sac")."""
    return f"email:{regra.chave or str(regra.id).replace('-', '')[-8:]}"


def vale_no_site(regra: Any, site: str | None) -> bool:
    sites = [str(x) for x in (getattr(regra, "sites", None) or [])]
    return not sites or (site is not None and site in sites)


async def regras_ativas(session: AsyncSession, site: str | None = None) -> list[MailAgenteRegra]:
    """As regras ligadas que valem neste site (sem site: todas as ligadas)."""
    linhas = (
        await session.execute(
            select(MailAgenteRegra)
            .where(MailAgenteRegra.ativa.is_(True))
            .order_by(MailAgenteRegra.ordem, MailAgenteRegra.created_at)
        )
    ).scalars()
    return [r for r in linhas if site is None or vale_no_site(r, site)]


def bloco_do_pacote(chave: Chave, regras: list[Any]) -> str:
    """O bloco E-MAIL do pacote do agente (o que muda quando o canal é e-mail). PURA."""
    linhas = [
        "Esta conversa é por E-MAIL (não é chat). Escreva como e-mail: cumprimento curto "
        '("Olá, <nome>!" ou "Olá!"), parágrafos curtos, linguagem cordial e direta.',
        "NÃO escreva assinatura, despedida com nome, cargo, telefone ou e-mail no fim: o "
        "sistema põe a assinatura da marca/loja embaixo do seu texto. Termine no conteúdo "
        '(pode fechar com "Ficamos à disposição.").',
    ]
    formulario = chave.formulario
    if formulario is not None:
        linhas.append(
            "E-mail do SAC do site. "
            + (
                "O atendimento VEIO pelo formulário de SAC do site (tem protocolo)."
                if chave.via_formulario
                else "O atendimento NÃO veio pelo formulário de SAC do site (não tem protocolo)."
            )
        )
        linhas.append(f"Formulário de SAC do site: {formulario.url}")
        linhas.append(f"Site oficial da marca: {URL_SITE.get(chave.site or '', '')}")
        linhas.append("O que o formulário pede:")
        linhas.extend(f"- {c}" for c in formulario.campos)
        linhas.append("Ao enviar o formulário, o cliente recebe o número do protocolo na hora.")
    if chave.em_andamento:
        linhas.append("A equipe JÁ respondeu nesta conversa: é um atendimento em andamento.")
    if regras:
        linhas.append(
            "REGRAS DO E-MAIL (do dono; valem acima das regras gerais do chat). Quando a "
            "resposta seguir uma delas, cite o id dela (o que está entre colchetes) em "
            "`evidencias`:"
        )
        for r in regras:
            linhas.append(f"- [{id_da_regra(r)}] {r.titulo}: {r.instrucao}")
    return "\n".join(linhas)


def links_permitidos(conversa: Any) -> list[str]:
    """As URLs que o agente pode escrever neste e-mail (o formulário de SAC do site)."""
    chave = chave_da(conversa)
    formulario = chave.formulario if chave is not None else None
    if formulario is None:
        return []
    return [formulario.url, *([URL_SITE[chave.site]] if chave.site in URL_SITE else [])]


_RX_URL = re.compile(r"https?://\S+", re.IGNORECASE)


def site_liberado(conversa_id: Any) -> bool:
    """A conversa é um e-mail do SAC do SITE liberado para o agente nesta rodada?"""
    chave = chave_da(SimpleNamespace(id=conversa_id))
    return chave is not None and chave.site is not None


def pedido_do_formulario(texto: str | None, chave: Chave | None) -> bool:
    """O rascunho é o pedido de abrir pelo formulário de SAC (a regra `formulario_sac`)?

    Só no SAC do site, no atendimento que NÃO veio pelo formulário, e com o link
    do formulário da marca no texto (e nenhum outro link). PURA.
    """
    if chave is None or chave.via_formulario or chave.em_andamento or chave.formulario is None:
        return False
    alvo = chave.formulario.url.rstrip("/").lower()
    site = URL_SITE.get(chave.site or "", "").rstrip("/").lower()
    urls = {u.rstrip(".,;:!?)").rstrip("/").lower() for u in _RX_URL.findall(texto or "")}
    return alvo in urls and urls <= {alvo, site}


# As travas do ASSUNTO (`ia.trava_do_agente` e o só-pessoa do manual) que o pedido do
# formulário passa por cima. As outras (ação de pessoa, regra suave, copiloto,
# andamento, agente desligado) continuam valendo.
TRAVAS_DO_ASSUNTO = frozenset(
    {
        "assunto_nao_liberado",
        "assunto_nao_liberado_agente",
        "orientacao_nao_liberada",
        "assunto_de_produto",
        "assunto_so_humano",
    }
)


async def ids_que_saem(session: AsyncSession, chave: Chave | None) -> frozenset[str]:
    """Os ids das regras ligadas, deste site, marcadas "responde sozinho"."""
    if chave is None:
        return frozenset()
    return frozenset(
        id_da_regra(r) for r in await regras_ativas(session, chave.site) if r.responde_sozinho
    )


def libera_pela_regra(
    evidencias: Any, texto: str | None, chave: Chave | None, que_saem: frozenset[str]
) -> bool:
    """O rascunho cita uma regra do e-mail que "responde sozinho"? PURA.

    Só com a chave no automático. A do formulário (`email:formulario_sac`) vale só
    se o texto é o pedido do formulário (`pedido_do_formulario`: o link da marca,
    sem protocolo, sem atendimento em andamento).
    """
    if chave is None or chave.modo != MODO_AUTO:
        return False
    citadas = {" ".join(str(e or "").split()) for e in (evidencias or [])} & que_saem
    formulario = f"email:{CHAVE_FORMULARIO}"
    if formulario in citadas and not pedido_do_formulario(texto, chave):
        citadas.discard(formulario)
    return bool(citadas)


def evidencias_do_rascunho(rascunho: Any) -> list[str]:
    fatos = getattr(rascunho, "fatos", None)
    agente = fatos.get("agente") if isinstance(fatos, dict) else None
    evid = agente.get("evidencias") if isinstance(agente, dict) else None
    return [str(e) for e in evid] if isinstance(evid, list) else []


async def sai_pela_regra(session: AsyncSession, conversa: Any, rascunho: Any) -> bool:
    """O rascunho do e-mail liberado segue uma regra que "responde sozinho" (no automático)?"""
    chave = chave_da(conversa)
    if chave is None or chave.modo != MODO_AUTO:
        return False
    return libera_pela_regra(
        evidencias_do_rascunho(rascunho),
        getattr(rascunho, "texto", None),
        chave,
        await ids_que_saem(session, chave),
    )


# ── O usuário do agente ───────────────────────────────────────────────────


async def usuario_do_agente(session: AsyncSession) -> User:
    """Get-or-create do usuário do agente (sem senha → ninguém loga com ele)."""
    u = await session.scalar(select(User).where(User.open_id == OPEN_ID_AGENTE))
    if u is None:
        u = User(
            open_id=OPEN_ID_AGENTE,
            email=EMAIL_AGENTE,
            name=NOME_AGENTE,
            role=UserRole.USER,
            status=UserStatus.ACTIVE,
        )
        session.add(u)
        await session.flush()
    return u


# ── A rodada do cron ──────────────────────────────────────────────────────


def _sql_email_da_ponte(c: Any = AtendimentoConversa):
    return (
        (c.canal == CANAL_EMAIL)
        & (func.btrim(c.dados["fonte"].astext) == FONTE_TUTA)
        & c.dados.has_key("mail")
    )


async def candidatas(session: AsyncSession, *, limite: int = 40) -> list[UUID]:
    """As conversas de e-mail da ponte esperando a loja (o mesmo corte do cron do chat)."""
    from app.services.atendimento import ia

    agora = datetime.now(UTC)
    c = AtendimentoConversa
    return list(
        (
            await session.execute(
                select(c.id)
                .where(
                    _sql_email_da_ponte(c),
                    or_(c.plataforma == PLATAFORMA_SITE, c.integration_id.is_not(None)),
                    c.aguardando_resposta.is_(True),
                    c.sem_resposta_necessaria.is_(False),
                    c.ia_pausada.is_(False),
                    c.atribuido_a.is_(None),
                    c.situacao.not_in((CONVERSA_FECHADA, CONVERSA_BLOQUEADA)),
                    c.ultima_do_cliente_em.is_not(None),
                    c.ultima_do_cliente_em <= agora - ia.ESPERA_SILENCIO,
                    c.ultima_do_cliente_em >= agora - ia.JANELA_PENDENTES,
                    ~ia._sql_chegou_ha_pouco(agora),
                    ~ia._sql_ja_tratada(),
                )
                .order_by(c.ultima_do_cliente_em.asc())
                .limit(limite)
            )
        ).scalars()
    )


async def gerar_pendentes(session: AsyncSession, *, limite: int = 5) -> int:
    """O agente escreve (e, no automático, envia) os e-mails esperando. Devolve quantos.

    Roda dentro da rodada da IA (`ia.gerar_pendentes`, a mesma trava): só
    conversas com a chave LIGADA (`chave_liberada`), na mesma régua do chat.
    """
    from app.services.atendimento import ia

    s = get_settings()
    if not (s.atendimento_ia_ativa and s.atendimento_agente_ativo):
        return 0
    if agente_config.chave_do_agente(s) is None or ia._agente_no_limite():
        return 0
    pausadas = set(ia._conversas_em_pausa_do_agente())
    geradas = 0
    for conversa_id in await candidatas(session):
        if geradas >= limite or ia._agente_no_limite():
            break
        if conversa_id in pausadas:
            continue
        conversa = await session.get(AtendimentoConversa, conversa_id, populate_existing=True)
        if conversa is None:
            continue
        chave = await chave_liberada(session, conversa)
        if chave is None:
            continue
        with liberar(conversa.id, chave):
            if await ia.gerar_rascunho(session, conversa) is not None:
                geradas += 1
    if geradas:
        logger.info("atendimento_email_agente_rodada", geradas=geradas)
    return geradas


# ── A tela (E-mail › Agente) ──────────────────────────────────────────────


async def onde_responde(session: AsyncSession) -> dict[str, list[dict]]:
    """Onde o agente responde e-mail agora: os sites (com a linha deles) e as lojas.

    Loja = o canal da chave com o agente marcado e em copiloto/automático. Só
    nomes e modos (a tela; nada do cliente).
    """
    from app.models import Integration
    from app.services.atendimento import lojas

    s = get_settings()
    disponivel = bool(s.atendimento_agente_ativo and agente_config.chave_do_agente(s))
    sites: list[dict] = []
    for site, formulario in FORMULARIOS_SAC.items():
        canal = await canal_do_site(session, site)
        ligado = bool(canal is not None and canal.agente is True and canal.modo in MODOS_DO_AGENTE)
        sites.append(
            {
                "site": site,
                "nome": {"uranyx": "Uranyx", "charlots": "Charlots"}.get(site, site),
                "canal_id": str(canal.id) if canal is not None else None,
                "ligado": ligado,
                "modo": canal.modo if ligado and canal is not None else None,
                "formulario": formulario.url,
            }
        )
    lojas_on: list[dict] = []
    if not LOJAS_LIGADAS:
        return {"disponivel": disponivel, "sites": sites, "lojas": lojas_on}
    linhas = (
        await session.execute(
            select(AtendimentoCanal, Integration)
            .join(Integration, Integration.id == AtendimentoCanal.integration_id)
            .where(
                AtendimentoCanal.agente.is_(True),
                AtendimentoCanal.modo.in_(MODOS_DO_AGENTE),
                Integration.archived_at.is_(None),
            )
        )
    ).all()
    for canal, integration in linhas:
        if CANAL_DA_CHAVE.get(canal.plataforma) != canal.canal:
            continue
        nome = await lojas.nome_da_loja(session, integration)
        lojas_on.append(
            {
                "canal_id": str(canal.id),
                "plataforma": canal.plataforma,
                "loja": nome or integration.name,
                "modo": canal.modo,
            }
        )
    lojas_on.sort(key=lambda x: (x["plataforma"], (x["loja"] or "").lower()))
    return {"disponivel": disponivel, "sites": sites, "lojas": lojas_on}


async def ligar_site(session: AsyncSession, site: str, *, modo: str | None) -> AtendimentoCanal:
    """Liga (copiloto/automático) ou desliga (None) o agente no e-mail do SAC do site.

    Grava na linha do site no atendimento (a chave dele): `agente` e `modo`.
    Desligar volta a linha para observar (o carrinho do site é só leitura em
    qualquer modo). Não commita. `LookupError` se o site não tem linha.
    """
    from app.services.atendimento.constantes import MODO_OBSERVAR

    canal = await canal_do_site(session, site)
    if canal is None:
        raise LookupError(site)
    if modo is None:
        canal.agente = False
        canal.modo = MODO_OBSERVAR
    else:
        if modo not in MODOS_DO_AGENTE:
            raise ValueError(modo)
        canal.agente = True
        canal.modo = modo
    await session.flush()
    return canal
