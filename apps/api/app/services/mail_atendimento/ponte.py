"""A PONTE: o e-mail da Central → a conversa do /atendimento (RF5 e RF6, 08/10/2026).

A Central do outro dev guarda o e-mail cifrado e não sabe de loja nem de
pedido. A ponte roda no worker a cada minuto (`mail_ponte`), SÓ nas caixas
com a ponte LIGADA (`mail_mailbox_settings.ponte_ligada`, nasce desligada) e
só no que chegou DEPOIS do corte (`ponte_desde`: o e-mail antigo nem é
decifrado). Um e-mail por vez, commit por e-mail:

 0. RESERVA: a linha em `mail_message_meta` (INSERT … ON CONFLICT DO NOTHING) é
    a trava — duas voltas nunca processam o mesmo e-mail, e o e-mail que já
    tem meta nunca ganha uma 2ª mensagem na conversa.
 1. FILTRO DA CAIXA: com "só aliases de loja" (a caixa privada da Goslin),
    só passa o e-mail que um alias ligado a uma LOJA recebeu (nunca o
    endereço principal) — o resto fica `privado`, sem pasta, alias nem hash
    em claro.
 2. PASTA (`pastas`/`regras`): a que só se conta (financeiro, Lixeira, Spam…)
    fica `ignorado` — o corpo não vai a lugar nenhum.
 3. SEGURANÇA (`codigos`): código de verificação, senha, novo acesso,
    confirmar e-mail → `seguranca`: nunca conversa, nunca fila. A regra
    ESTRITA (qualquer gatilho de senha/acesso esconde) vale sempre, menos
    quando quem escreve é PESSOA num lugar de pessoa: o formulário do site,
    o cliente numa caixa de site (sac@ da marca), ou a resposta num fio que
    a equipe já tem — e nunca num alias que é LOGIN de loja (crítica de
    08/10: o reset de senha do Bling/Meta para 21max@ virava conversa).
 4. DUPLICADO: o mesmo Message-ID que a ponte JÁ levou à equipe (outra caixa,
    outro alias) → `duplicado` (nunca contra um privado ou ignorado).
 5. QUEM ESCREVEU, a LOJA (`rotear`), o FORMULÁRIO do site (RF6: DE sac@ PARA
    sac@, o cliente vem do corpo), o PEDIDO por plataforma (`pedido`) ou o
    PROTOCOLO (US-26-0001…), o GOLPE (`suspeito`).
 6. A CONVERSA (só uma conversa DA PONTE): o fio do Tuta, o
    In-Reply-To/References (de um e-mail já levado ou da NOSSA resposta), o
    protocolo, o pedido — senão uma conversa nova da loja (`canal='email'`,
    `dados.fonte='tuta'`, `dados.mail`). Na Amazon, o fio que aponta para a
    conversa do Gmail espera a mensagem chegar por lá (nunca grava cópia). O
    aviso das plataformas com API (ML, Shopee, TikTok, Amazon) vai para a
    conversa DA API do pedido (sem pendência). Sem loja → fila "E-mail sem
    loja" (SEM conversa); sem pedido → "E-mail sem vínculo"; mais de 3
    pedidos → `resumo`; pasta vendas → só histórico.
 7. A MENSAGEM leva SÓ o texto NOVO (sem a citação), com os links de acesso
    removidos e o código mascarado, cortado. O corpo inteiro e os anexos
    continuam só cifrados na Central (o cartão decifra na hora).

E a VOLTA da resposta: o status do job da fila dele (`mail_outbox`) passa
para a mensagem `enviando` da conversa (`sincronizar_envios`).

Texto de e-mail nunca vai para o log nem para a meta: só ids, estados e
contagens.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import exists, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AtendimentoConversa,
    AtendimentoMensagem,
    Integration,
    MailMailbox,
    MailMessage,
    MailOutbox,
)
from app.models.mail_atendimento import (
    MailFolder,
    MailMailboxSettings,
    MailMessageMeta,
    MailOutboxMeta,
)
from app.security.cipher import decrypt_json
from app.services.atendimento import gravar
from app.services.atendimento.constantes import (
    AUTOR_CLIENTE,
    AUTOR_LOJA,
    AUTOR_SISTEMA,
    CANAL_AVALIACAO,
    CANAL_EMAIL,
    CHAVE_VEZ_DA_LOJA,
    CONVERSA_ABERTA,
    CONVERSA_FECHADA,
    FONTE_TUTA,
    MSG_ENVIADA,
    MSG_FALHOU,
    MSG_REVISAR,
    ORIGEM_EXTERNO,
)
from app.services.mail_atendimento import caixa as config_caixa
from app.services.mail_atendimento import (
    codigos,
    enderecos,
    pastas,
    pedido,
    regras,
    rotear,
    suspeito,
    texto,
)
from app.services.mail_atendimento.constantes import (
    ALERTA_LOJA_SEM_INTEGRACAO,
    ALERTA_OUTRO_REMETENTE,
    ALERTA_PROTOCOLO_FORMATO,
    ALERTA_PROTOCOLO_INVALIDO,
    ALERTA_PROTOCOLO_OUTRA_MARCA,
    ALERTA_PROTOCOLO_REPETIDO,
    ALERTA_SEM_PROTOCOLO,
    ALERTA_TIPO_X_CAIXA,
    ALERTAS_DO_CHAMADO,
    ASSUNTOS_SEM_PENDENCIA,
    DOMINIOS_DO_TUTA_SISTEMA,
    ESPERA_GMAIL_AMAZON,
    ESPERA_POR_VOLTA,
    ESPERA_RECIBO,
    ESTADO_DUPLICADO,
    ESTADO_ERRO,
    ESTADO_GRAVADO,
    ESTADO_IGNORADO,
    ESTADO_INTERNO,
    ESTADO_NOVO,
    ESTADO_PRIVADO,
    ESTADO_RESUMO,
    ESTADO_SEGURANCA,
    ESTADO_SEM_LOJA,
    ESTADO_SEM_VINCULO,
    ESTADOS_DA_EQUIPE,
    FINALIDADE_ENVIADOS,
    FINALIDADE_VENDAS,
    FINALIDADES_DESTAQUE,
    FINALIDADES_PENDENTES,
    FRASE_DA_FALHA,
    IGNORADO_ALIAS_INTERNO,
    IGNORADO_AVISO_DO_TUTA,
    IGNORADO_DEVOLUCAO,
    IGNORADO_ENVIADO_SEM_CONVERSA,
    IGNORADO_PASTA_NAO_LER,
    IGNORADO_PASTA_SO_CONTAR,
    JOBS_VIVOS,
    LOCAIS_INTERNOS,
    MAX_PEDIDOS_POR_EMAIL,
    PLATAFORMA_SITE,
    PLATAFORMAS_EMAIL_SO_AVISO,
    PONTE_POR_VOLTA,
    PRIVADO_NAO_E_DE_LOJA,
    REGRAS_VERSAO,
    REMETENTE_AVISO,
    REMETENTE_COMPRADOR,
    REMETENTE_NOSSO,
    REMETENTE_TUTA,
    ROTULO_ALERTA_DO_CHAMADO,
    ROTULO_TIPO_CAIXA,
    SEGURANCA_CODIGO,
    SEM_LOJA_ALIAS_SEM_CADASTRO,
    SEM_LOJA_AMBIGUO,
    SEM_LOJA_ENTRADA,
    SEM_LOJA_MARCA_AMBIGUA,
    SEM_LOJA_SEM_ALIAS,
    STATUS_DO_JOB,
    TEXTO_NA_CONVERSA_MAX,
)

logger = structlog.get_logger()

# A marca do que é da ponte: `conversa.dados["mail"]` e `mensagem.payload["mail"]`.
CHAVE = "mail"
# O `externo_id` da conversa de e-mail (por fio, protocolo, ou o 1º e-mail).
PREFIXO_FIO = "mail-fio:"
PREFIXO_PROTOCOLO = "mail-protocolo:"
PREFIXO_EMAIL = "mail:"
# A conversa da loja SEM integração (só a ficha): o id da ficha entra na chave
# (sem integração, a chave da conversa é só canal + id + plataforma: duas
# fichas da mesma plataforma nunca podem cair na mesma conversa).
PREFIXO_FICHA = "mail-loja:"
# Quantos "agrupado em" seguir até o chamado que ficou (RF6).
_MAX_AGRUPADO = 5
# Os motivos de "sem loja" que o fio da conversa pode resolver (a resposta do
# cliente na Entrada, o alias de duas lojas).
_SEM_LOJA_PELO_FIO = (
    SEM_LOJA_ENTRADA,
    SEM_LOJA_AMBIGUO,
    SEM_LOJA_SEM_ALIAS,
    SEM_LOJA_ALIAS_SEM_CADASTRO,
)
# Os canais da conversa da API que recebem o aviso por e-mail da plataforma.
_CANAIS_SEM_AVISO = (CANAL_EMAIL, CANAL_AVALIACAO)
_TEXTO_CORTADO = "[…] (o texto inteiro está no cartão do e-mail)"
# O que o e-mail novo nunca troca numa conversa de chamado que já tem (RF6).
_FIXOS_DO_CHAMADO = ("protocolo", "caixa", "caixa_rotulo", "marca", "marca_nome")


class _Esperar(Exception):  # noqa: N818 — controle de fluxo, não erro
    """O e-mail espera a próxima volta (o recibo da nossa resposta ainda não veio)."""


# ── O e-mail decifrado (só em memória) ────────────────────────────────────


def hash_mid(valor: Any) -> str | None:
    """sha256 do Message-ID sem `<>` nem espaço (maiúsculas como vieram); vazio → None."""
    if not isinstance(valor, str):
        return None
    limpo = "".join(valor.split()).strip("<>")
    if not limpo:
        return None
    return hashlib.sha256(limpo.encode()).hexdigest()


@dataclass
class Email:
    """O e-mail decifrado da Central — nunca vai para o log nem para a meta."""

    message: MailMessage
    conteudo: dict
    de: str
    de_nome: str
    para: list[str]
    cc: list[str]
    delivered_to: list[str]
    reply_to: list[str]
    assunto: str
    texto: str
    tuta: dict = field(default_factory=dict)

    @property
    def recebeu(self) -> list[str]:
        """Quem recebeu, na ordem: o delivered_to (v2), o Para, o Cc."""
        return list(dict.fromkeys([*self.delivered_to, *self.para, *self.cc]))

    @property
    def refs(self) -> list[str]:
        """Os hashes do In-Reply-To e das References (o mais novo primeiro)."""
        brutos = [
            self.conteudo.get("in_reply_to"),
            *reversed(self.conteudo.get("references") or []),
        ]
        saida: list[str] = []
        for bruto in brutos:
            h = hash_mid(bruto)
            if h and h not in saida:
                saida.append(h)
        return saida[:51]


def decifrar(message: MailMessage) -> Email:
    conteudo = decrypt_json(message.content_enc)
    tuta = conteudo.get("tuta") if isinstance(conteudo.get("tuta"), dict) else {}
    reply = conteudo.get("reply_to")
    return Email(
        message=message,
        conteudo=conteudo,
        de=enderecos.normalizar(conteudo.get("from_address")),
        de_nome=" ".join(str(conteudo.get("from_name") or "").split())[:200],
        para=enderecos.lista(conteudo.get("to") or []),
        cc=enderecos.lista(conteudo.get("cc") or []),
        delivered_to=enderecos.lista(conteudo.get("delivered_to") or []),
        reply_to=enderecos.lista([reply] if isinstance(reply, str) else reply or []),
        assunto=" ".join(str(conteudo.get("subject") or "").split())[:998],
        texto=str(conteudo.get("text") or ""),
        tuta=tuta,
    )


def aliases_da_caixa(mailbox: MailMailbox) -> tuple[str, set[str]]:
    """(endereço principal, todos os endereços da caixa) — do config cifrado dela."""
    config = decrypt_json(mailbox.config_enc)
    principal = (
        enderecos.normalizar(config.get("address")) or str(config.get("address", "")).lower()
    )
    todos = {e for e in (enderecos.normalizar(a) for a in config.get("aliases") or []) if e}
    todos.add(principal)
    return principal, todos


def tuta_id_de(message: MailMessage, tuta: dict) -> str | None:
    """O id "lista/elemento" do e-mail no Tuta ("Abrir no Tuta"), quando há."""
    bruto = tuta.get("mail_id")
    if isinstance(bruto, list | tuple) and len(bruto) == 2:
        bruto = f"{bruto[0]}/{bruto[1]}"
    if not bruto and message.source_id.startswith("tuta:"):
        bruto = message.source_id[5:]
    valor = str(bruto or "").replace(",", "/").strip()
    lista, _, elemento = valor.partition("/")
    return f"{lista}/{elemento}"[:191] if lista and elemento else None


# ── Quem escreveu ─────────────────────────────────────────────────────────


def remetente_tipo(de: str | None, nossos: set[str]) -> str:
    if not de:
        return REMETENTE_AVISO
    if de in nossos:
        return REMETENTE_NOSSO
    if enderecos.dominio(de) in DOMINIOS_DO_TUTA_SISTEMA:
        return REMETENTE_TUTA
    if enderecos.e_aviso(de) or suspeito.plataforma_do_dominio(enderecos.dominio(de)):
        return REMETENTE_AVISO
    return REMETENTE_COMPRADOR


def alias_de_loja(
    email: Email,
    *,
    principal: str,
    aliases: set[str],
    e_de_loja: Callable[[str], bool],
    enviado: bool,
) -> str | None:
    """O alias de LOJA pelo qual o e-mail passa no filtro da caixa (None = privado).

    Nunca o endereço principal da caixa; sempre um endereço que é da caixa
    (o recebido — ou, no enviado, o remetente) e está no cadastro de lojas.
    """
    candidatos = [email.de] if enviado else email.recebeu
    for e in candidatos:
        if e and e != principal and e in aliases and e_de_loja(e):
            return e
    return None


def abre_pendencia(*, plataforma: str | None, finalidade: str | None, autor: str) -> bool:
    """O e-mail põe a conversa em "falta responder"? (decisão padrão de 08/10/2026)

    O cliente escrevendo abre sempre, menos na pasta vendas (histórico). O
    aviso da plataforma (sistema) só nas pastas mensagens/problema/reclamação,
    e só onde a plataforma não tem API — nas que têm, a pendência é a da
    conversa da API e o e-mail fica como aviso.
    """
    if autor == AUTOR_CLIENTE:
        return finalidade != FINALIDADE_VENDAS
    if autor != AUTOR_SISTEMA:
        return False
    return finalidade in FINALIDADES_PENDENTES and plataforma not in PLATAFORMAS_EMAIL_SO_AVISO


def assunto_sem_pendencia(assunto: str | None) -> bool:
    plano = " ".join(regras.palavras(assunto or ""))
    return any(plano.startswith(a) for a in ASSUNTOS_SEM_PENDENCIA)


_RE_AUTO_ASSUNTO = re.compile(
    r"^\s*(?:resposta autom[aá]tica|auto(?:matic)?[- ]?reply|out of office|ausente"
    r"|f[eé]rias|automatic reply|respuesta autom[aá]tica)\b",
    re.IGNORECASE,
)


def auto_resposta(email: Email) -> bool:
    """Férias, "recebemos seu e-mail": não é o cliente falando."""
    return bool(_RE_AUTO_ASSUNTO.search(email.assunto or ""))


def _alerta(meta: MailMessageMeta, codigo: str, frase: str) -> None:
    atuais = list(meta.alertas or [])
    if any(isinstance(a, dict) and a.get("codigo") == codigo for a in atuais):
        return
    atuais.append({"codigo": codigo, "texto": frase[:300]})
    meta.alertas = atuais


def _vez_da_loja(conversa: AtendimentoConversa, momento: datetime) -> None:
    """Abre (ou mantém) a vez da loja a partir de `momento` (a régua de `gravar._derivar`)."""
    dados = dict(conversa.dados or {})
    atual = gravar._iso_utc(dados.get(CHAVE_VEZ_DA_LOJA))
    if atual is not None and momento <= atual:
        return
    dados[CHAVE_VEZ_DA_LOJA] = momento.isoformat()
    conversa.dados = dados
    da_loja = gravar._utc(conversa.ultima_da_loja_em)
    if da_loja is None or momento > da_loja:
        if conversa.situacao == CONVERSA_FECHADA:
            conversa.situacao = CONVERSA_ABERTA
        conversa.sem_resposta_necessaria = False


# ── A conversa ────────────────────────────────────────────────────────────


def dados_mail(conversa: AtendimentoConversa) -> dict:
    dados = conversa.dados if isinstance(conversa.dados, dict) else {}
    valor = dados.get(CHAVE)
    return valor if isinstance(valor, dict) else {}


def clientes_da_conversa(conversa: AtendimentoConversa) -> set[str]:
    """Os e-mails de cliente da conversa: o comprador e os dos chamados agrupados nela."""
    saida = {c for c in (dados_mail(conversa).get("clientes") or []) if isinstance(c, str) and c}
    if conversa.comprador_id:
        saida.add(conversa.comprador_id)
    return saida


def _mesma_cliente(
    conversa: AtendimentoConversa,
    contato: str | None,
    campos: dict[str, str | None] | None = None,
) -> bool:
    """O formulário é da cliente da conversa?

    Com e-mail dos dois lados, vale o e-mail (o do formulário é um dos
    clientes da conversa). Sem e-mail para comparar (o formulário sem e-mail,
    ou a conversa sem cliente), desempatam os campos do formulário: o
    telefone (os dois têm: igual = sim, diferente = não) e o nome (de pessoas
    diferentes = não). Sem como saber, vale como sim — o protocolo é a chave
    do chamado."""
    clientes = clientes_da_conversa(conversa)
    if contato and clientes:
        return contato in clientes
    campos = campos or {}
    telefone, da_conversa = campos.get("telefone"), dados_mail(conversa).get("telefone")
    if telefone and da_conversa:
        return telefone == da_conversa
    return enderecos.mesmo_nome(campos.get("nome"), conversa.comprador_nome) is not False


def e_conversa_da_ponte(conversa: AtendimentoConversa) -> bool:
    """A conversa de e-mail que a PONTE criou (`dados.fonte='tuta'` + `dados.mail`)."""
    dados = conversa.dados if isinstance(conversa.dados, dict) else {}
    return conversa.canal == CANAL_EMAIL and dados.get("fonte") == FONTE_TUTA and CHAVE in dados


def ficha_da_conversa(conversa: AtendimentoConversa) -> UUID | None:
    """A ficha (store_info) da conversa da loja SEM integração; None nas outras."""
    if conversa.integration_id is not None or conversa.plataforma == PLATAFORMA_SITE:
        return None
    try:
        return UUID(str(dados_mail(conversa).get("store_info_id") or ""))
    except ValueError:
        return None


def _da_mesma_loja(conversa: AtendimentoConversa, rota: rotear.Rota) -> bool:
    if rota.site:
        return (
            conversa.integration_id is None
            and conversa.plataforma == PLATAFORMA_SITE
            and dados_mail(conversa).get("marca") == rota.marca_slug
        )
    if rota.integration_id is not None:
        return conversa.integration_id == rota.integration_id
    return rota.store_info_id is not None and ficha_da_conversa(conversa) == rota.store_info_id


def da_mesma_loja_sql(conversa: AtendimentoConversa) -> tuple:
    """WHERE das conversas da MESMA loja da conversa (a integração, ou a ficha sem integração)."""
    if conversa.integration_id is not None:
        return (AtendimentoConversa.integration_id == conversa.integration_id,)
    ficha = ficha_da_conversa(conversa)
    if ficha is None:
        return (AtendimentoConversa.id == conversa.id,)
    return (
        AtendimentoConversa.integration_id.is_(None),
        AtendimentoConversa.plataforma == conversa.plataforma,
        AtendimentoConversa.dados[CHAVE]["store_info_id"].astext == str(ficha),
    )


async def conversa_final(
    session: AsyncSession, conversa: AtendimentoConversa
) -> AtendimentoConversa:
    """O chamado que FICOU: segue o "agrupado em" (RF6) — o e-mail novo de um
    chamado agrupado entra no destino, nunca reabre o que foi fechado."""
    vistos = {conversa.id}
    for _ in range(_MAX_AGRUPADO):
        destino = dados_mail(conversa).get("agrupado_em")
        if not destino:
            break
        try:
            proxima = await session.get(AtendimentoConversa, UUID(str(destino)))
        except ValueError:
            break
        if proxima is None or proxima.id in vistos:
            break
        vistos.add(proxima.id)
        conversa = proxima
    return conversa


async def _conversas(session: AsyncSession, ids: set[UUID]) -> list[AtendimentoConversa]:
    """As conversas de e-mail (mais velha primeiro), cada uma já no chamado que FICOU."""
    if not ids:
        return []
    achadas = (
        (
            await session.execute(
                select(AtendimentoConversa)
                .where(AtendimentoConversa.id.in_(ids), AtendimentoConversa.canal == CANAL_EMAIL)
                .order_by(AtendimentoConversa.created_at)
            )
        )
        .scalars()
        .all()
    )
    saida: list[AtendimentoConversa] = []
    for c in achadas:
        final = await conversa_final(session, c)
        if final.canal == CANAL_EMAIL and all(final.id != x.id for x in saida):
            saida.append(final)
    return saida


async def _conversas_do_fio(
    session: AsyncSession, meta: MailMessageMeta, email: Email
) -> set[UUID]:
    """As conversas do mesmo fio do Tuta ou das referências (e-mail já levado ou resposta nossa)."""
    ids: set[UUID] = set()
    if meta.fio_tuta:
        ids |= set(
            (
                await session.execute(
                    select(MailMessageMeta.conversa_id).where(
                        MailMessageMeta.fio_tuta == meta.fio_tuta,
                        MailMessageMeta.conversa_id.is_not(None),
                        MailMessageMeta.estado.in_(ESTADOS_DA_EQUIPE),
                        MailMessageMeta.message_id != meta.message_id,
                    )
                )
            ).scalars()
        )
    refs = email.refs
    if refs:
        ids |= set(
            (
                await session.execute(
                    select(MailMessageMeta.conversa_id).where(
                        MailMessageMeta.mid_hash.in_(refs),
                        MailMessageMeta.conversa_id.is_not(None),
                        MailMessageMeta.estado.in_(ESTADOS_DA_EQUIPE),
                        MailMessageMeta.message_id != meta.message_id,
                    )
                )
            ).scalars()
        )
        ids |= set(
            (
                await session.execute(
                    select(MailOutboxMeta.conversa_id).where(
                        MailOutboxMeta.enviado_mid_hash.in_(refs),
                        MailOutboxMeta.conversa_id.is_not(None),
                    )
                )
            ).scalars()
        )
    return {i for i in ids if i is not None}


async def fio_da_equipe(
    session: AsyncSession, message_id: UUID, fio: str | None, refs: list[str]
) -> bool:
    """O e-mail responde a um fio que a EQUIPE já tem (um e-mail levado à conversa,
    ou uma resposta NOSSA)? Só ids e hashes — nada do e-mail é gravado aqui."""
    if fio:
        achado = await session.scalar(
            select(MailMessageMeta.message_id)
            .where(
                MailMessageMeta.fio_tuta == fio[:191],
                MailMessageMeta.estado.in_(ESTADOS_DA_EQUIPE),
                MailMessageMeta.message_id != message_id,
            )
            .limit(1)
        )
        if achado is not None:
            return True
    if refs:
        achado = await session.scalar(
            select(MailMessageMeta.message_id)
            .where(
                MailMessageMeta.mid_hash.in_(refs),
                MailMessageMeta.estado.in_(ESTADOS_DA_EQUIPE),
                MailMessageMeta.message_id != message_id,
            )
            .limit(1)
        )
        if achado is not None:
            return True
        nossa = await session.scalar(
            select(MailOutboxMeta.outbox_id)
            .where(MailOutboxMeta.enviado_mid_hash.in_(refs))
            .limit(1)
        )
        if nossa is not None:
            return True
    return False


async def de_pessoa_para_seguranca(
    session: AsyncSession,
    email: Email,
    *,
    tipo: str,
    nossos: list[str],
    cad: rotear.Cadastro,
    marca_pasta: str | None,
) -> bool:
    """Quem escreve é PESSOA num lugar de pessoa? (só aí "esqueci minha senha" é atendimento)

    Regra da crítica de 08/10 (o BLOQUEIO): o remetente desconhecido NÃO é
    pessoa por padrão — o reset de senha do Bling, a "nova senha" do Melhor
    Envio e o "novo login" da Meta vinham de domínios fora da lista de
    plataformas e viravam conversa. Pessoa só quando:
      • é o FORMULÁRIO do site (DE uma caixa de site nossa); ou
      • o cliente escreve para uma CAIXA DE SITE (sac@ da marca) ou responde
        num FIO que a equipe já tem — e o remetente tem CARA DE PESSOA (não é
        noreply, security@, account@, notification@, nem de um domínio de
        serviço como facebookmail, google, nuvemshop, shopify: a crítica
        pré-subida de 08/10 viu a senha provisória da Nuvemshop virar chamado
        em atacado@, que pode ser o login do painel do site);
    e NUNCA quando um endereço que recebeu é LOGIN de loja (os aliases da
    conta geral: um reset ali tomaria a conta da loja).
    """
    if tipo == REMETENTE_NOSSO:
        return rotear.e_caixa_de_site(email.de, cad, marca_pasta=marca_pasta)
    if tipo != REMETENTE_COMPRADOR:
        return False
    if any(cad.e_de_loja(a) for a in nossos):
        return False
    if not enderecos.cara_de_pessoa(email.de):
        return False
    if any(rotear.e_caixa_de_site(a, cad, marca_pasta=marca_pasta) for a in nossos):
        return True
    fio = str(email.tuta.get("conversation_id") or "").strip() or None
    return await fio_da_equipe(session, email.message.id, fio, email.refs)


async def _rota_pelo_fio(
    session: AsyncSession, meta: MailMessageMeta, email: Email, nossos: list[str]
) -> rotear.Rota | None:
    """A loja da conversa do MESMO fio — quando o alias sozinho não diz (a resposta na Entrada).

    Só vale se o alias da conversa está entre os que receberam este e-mail
    (ou se ele não traz alias nenhum: cópia oculta).
    """
    candidatas = await _conversas(session, await _conversas_do_fio(session, meta, email))
    chaves = {(c.integration_id, c.plataforma, ficha_da_conversa(c)) for c in candidatas}
    if len(chaves) != 1:
        return None
    c = candidatas[0]
    mail = dados_mail(c)
    alias = mail.get("alias")
    if nossos and alias and alias not in nossos:
        return None
    if c.plataforma == PLATAFORMA_SITE:
        return rotear.Rota(
            alias=alias,
            plataforma=PLATAFORMA_SITE,
            marca_slug=mail.get("marca"),
            marca_nome=mail.get("marca_nome"),
            tipo_caixa=mail.get("caixa"),
        )
    ficha = ficha_da_conversa(c)
    if c.integration_id is None and ficha is None:
        return None
    return rotear.Rota(
        alias=alias,
        integration_id=c.integration_id,
        store_info_id=ficha,
        plataforma=c.plataforma,
        loja_nome=c.conta,
    )


def _esperar_o_gmail(
    meta: MailMessageMeta, email: Email, fora: list[AtendimentoConversa], agora: datetime
) -> None:
    """A Amazon: o fio (ou a referência) aponta para a conversa que veio pelo
    GMAIL (`amazon_email`, fora da ponte), e esta mensagem ainda não chegou por
    lá. Gravar aqui seria pôr uma cópia da ponte DENTRO da conversa do Gmail
    (a tela a trataria como e-mail da ponte, e a mensagem do cliente apareceria
    duas vezes quando o Gmail a lesse): espera a próxima volta, até
    `ESPERA_GMAIL_AMAZON` — depois entra numa conversa à parte, com o aviso."""
    if not any(c.plataforma == "amazon" for c in fora):
        return
    chegou = gravar._utc(email.message.created_at) or agora
    if chegou > agora - ESPERA_GMAIL_AMAZON:
        raise _Esperar
    _alerta(
        meta,
        "amazon_sem_gmail",
        "a Amazon não mandou esta mensagem ao Gmail a tempo: entrou numa conversa à parte "
        "(confira a conversa do Gmail do mesmo pedido)",
    )


async def achar_conversa(
    session: AsyncSession,
    meta: MailMessageMeta,
    email: Email,
    rota: rotear.Rota,
    *,
    pedido_marketplace: str | None,
    agora: datetime | None = None,
    contato: str | None = None,
    so_mesma_cliente: bool = False,
    campos: dict[str, str | None] | None = None,
) -> tuple[AtendimentoConversa | None, str | None]:
    """A conversa onde o e-mail entra (e por quê): fio → referência → protocolo → pedido.

    Só conversa DA PONTE (`e_conversa_da_ponte`): o fio que aponta para outra
    conversa de e-mail (a da Amazon que veio pelo Gmail) nunca recebe a
    mensagem daqui — na Amazon, espera o Gmail (`_esperar_o_gmail`), MESMO
    que o fio também tenha uma conversa da ponte (a "à parte" de uma
    mensagem que já passou da espera): a do Gmail é a de verdade, e a
    mensagem que o Gmail ainda vai ler não pode ficar nas duas.

    PROTOCOLO (RF6): primeiro a conversa onde o remetente (`contato`) já é
    cliente — a dele, mesmo com o número repetido. No FORMULÁRIO
    (`so_mesma_cliente`) só a conversa da MESMA cliente (`_mesma_cliente`:
    o e-mail; sem e-mail, o telefone e o nome dos `campos`): o mesmo número
    no formulário de OUTRA cliente nunca entra na conversa dela — "nunca
    agrupar sozinho": abre conversa própria, com o alerta de protocolo
    repetido (`_protocolo_repetido`). No e-mail DIRETO (a cliente que escreve
    com o protocolo no assunto, às vezes de outro endereço), entra na
    conversa do protocolo só se ela for a ÚNICA — com duas ou mais (o número
    repetido de duas clientes), abre conversa própria. Quem responde vê o
    alerta de outro endereço na faixa e confirma antes (`responder.previa`)."""
    fora: list[AtendimentoConversa] = []
    amazon = rota.plataforma == "amazon"
    achada: tuple[AtendimentoConversa, str] | None = None
    if meta.fio_tuta:
        ids = set(
            (
                await session.execute(
                    select(AtendimentoConversa.id).where(
                        AtendimentoConversa.canal == CANAL_EMAIL,
                        AtendimentoConversa.externo_id == f"{PREFIXO_FIO}{meta.fio_tuta}"[:191],
                    )
                )
            ).scalars()
        )
        ids |= set(
            (
                await session.execute(
                    select(MailMessageMeta.conversa_id).where(
                        MailMessageMeta.fio_tuta == meta.fio_tuta,
                        MailMessageMeta.conversa_id.is_not(None),
                        MailMessageMeta.estado.in_(ESTADOS_DA_EQUIPE),
                        MailMessageMeta.message_id != meta.message_id,
                    )
                )
            ).scalars()
        )
        for c in await _conversas(session, {i for i in ids if i is not None}):
            if not _da_mesma_loja(c, rota):
                continue
            if e_conversa_da_ponte(c):
                if not amazon:
                    return c, "fio"
                achada = achada or (c, "fio")
                continue
            fora.append(c)
    if email.refs:
        for c in await _conversas(session, await _conversas_do_fio(session, meta, email)):
            if not _da_mesma_loja(c, rota):
                continue
            if e_conversa_da_ponte(c):
                if not amazon:
                    return c, "referencia"
                achada = achada or (c, "referencia")
                continue
            fora.append(c)
    if fora and amazon:
        _esperar_o_gmail(meta, email, fora, agora or datetime.now(UTC))
    if achada is not None:
        return achada
    if rota.site and meta.protocolo:
        ids = set(
            (
                await session.execute(
                    select(MailMessageMeta.conversa_id).where(
                        MailMessageMeta.protocolo == meta.protocolo,
                        MailMessageMeta.conversa_id.is_not(None),
                        MailMessageMeta.message_id != meta.message_id,
                    )
                )
            ).scalars()
        )
        ids |= set(
            (
                await session.execute(
                    select(AtendimentoConversa.id).where(
                        AtendimentoConversa.canal == CANAL_EMAIL,
                        AtendimentoConversa.externo_id == f"{PREFIXO_PROTOCOLO}{meta.protocolo}",
                    )
                )
            ).scalars()
        )
        candidatas = [
            c
            for c in await _conversas(session, {i for i in ids if i is not None})
            if _da_mesma_loja(c, rota) and e_conversa_da_ponte(c)
        ]
        if contato:
            for c in candidatas:
                if contato in clientes_da_conversa(c):
                    return c, "protocolo"
        if so_mesma_cliente:
            for c in candidatas:
                if _mesma_cliente(c, contato, campos):
                    return c, "protocolo"
        elif len(candidatas) == 1:
            return candidatas[0], "protocolo"
    if pedido_marketplace and not rota.site:
        if rota.integration_id is not None:
            da_loja: tuple = (AtendimentoConversa.integration_id == rota.integration_id,)
        else:
            da_loja = (
                AtendimentoConversa.integration_id.is_(None),
                AtendimentoConversa.dados[CHAVE]["store_info_id"].astext == str(rota.store_info_id),
            )
        c = (
            await session.execute(
                select(AtendimentoConversa)
                .where(
                    *da_loja,
                    AtendimentoConversa.canal == CANAL_EMAIL,
                    AtendimentoConversa.pedido_marketplace == pedido_marketplace,
                    AtendimentoConversa.dados["fonte"].astext == FONTE_TUTA,
                )
                .order_by(AtendimentoConversa.created_at)
                .limit(1)
            )
        ).scalar_one_or_none()
        if c is not None:
            return await conversa_final(session, c), "pedido"
    return None, None


def _externo_da_conversa(meta: MailMessageMeta, rota: rotear.Rota | None = None) -> str:
    if meta.protocolo and meta.plataforma == PLATAFORMA_SITE:
        return f"{PREFIXO_PROTOCOLO}{meta.protocolo}"
    if meta.fio_tuta:
        base = f"{PREFIXO_FIO}{meta.fio_tuta}"
    else:
        base = f"{PREFIXO_EMAIL}{meta.message_id}"
    if rota is not None and rota.so_ficha:
        return f"{PREFIXO_FICHA}{rota.store_info_id}:{base}"[:191]
    return base[:191]


def _comprador(contato: str | None) -> str | None:
    """O contato do comprador — nunca aviso, noreply nem endereço nosso."""
    if contato and not enderecos.e_aviso(contato):
        return contato[:128]
    return None


def payload_da_mensagem(
    meta: MailMessageMeta, pasta: MailFolder | None, protegido: codigos.Protegido, anexos: int
) -> dict:
    return {
        CHAVE: {
            "message_id": str(meta.message_id),
            "mailbox_id": str(meta.mailbox_id),
            "pasta": pasta.nome if pasta is not None else None,
            "finalidade": meta.finalidade,
            "alias": meta.alias_recebido,
            "assunto": protegido.assunto[:300],
            "suspeito": bool(meta.suspeito),
            "anexos": anexos,
            "codigo_mascarado": protegido.codigo_mascarado,
            "links_removidos": protegido.links_removidos,
            "protocolo": meta.protocolo,
        }
    }


async def gravar_na_conversa(
    session: AsyncSession,
    meta: MailMessageMeta,
    email: Email,
    rota: rotear.Rota,
    *,
    pasta: MailFolder | None,
    protegido: codigos.Protegido,
    autor: str,
    contato: str | None,
    pedido_marketplace: str | None,
    conversa: AtendimentoConversa | None,
    vinculado_por: str | None,
    abre: bool,
    origem: str | None = None,
    principal: str | None = None,
    formulario: dict[str, str | None] | None = None,
    externo: str | None = None,
) -> tuple[AtendimentoConversa, AtendimentoMensagem]:
    """A mensagem do e-mail na conversa (achada ou nova). Não commita.

    `abre` = o e-mail abre a vez da loja. A conversa NOVA que não abre nasce
    "não precisa de resposta" (o histórico de vendas, o aviso das plataformas
    com API). `principal`: o endereço de login da conta, que nunca vira "o
    alias" da conversa (a resposta nunca sai por ele). `formulario`: os
    campos do formulário do site (nome, telefone, pedido — RF6): o nome vira
    o da cliente do chamado e os três ficam em `dados.mail` (o primeiro que
    veio fica). `externo`: a chave da conversa nova quando não é a de sempre
    (o protocolo repetido de outra cliente).
    """
    alias = rota.alias or meta.alias_recebido
    if principal and alias == principal:
        alias = None
    integration = (
        await session.get(Integration, rota.integration_id) if rota.integration_id else None
    )
    criada = False
    mail: dict[str, Any] = {
        "mailbox_id": str(meta.mailbox_id),
        "pasta": pasta.nome if pasta is not None else None,
        "finalidade": meta.finalidade,
        "destaque": meta.finalidade in FINALIDADES_DESTAQUE,
        "alias": alias,
        # A ficha do cadastro (a chave da loja SEM integração; nas outras, informação).
        "store_info_id": str(rota.store_info_id) if rota.store_info_id else None,
    }
    if rota.site:
        mail.update(
            {
                "marca": rota.marca_slug,
                "marca_nome": rota.marca_nome,
                "caixa": rota.tipo_caixa,
                "caixa_rotulo": ROTULO_TIPO_CAIXA.get(rota.tipo_caixa or ""),
                "protocolo": meta.protocolo,
            }
        )
    # Os campos do formulário e os alertas do chamado: SOMAM ao que a conversa
    # já tem (o 1º nome/telefone/pedido fica; o alerta nunca some sozinho).
    extras: dict[str, Any] = {}
    if formulario:
        extras = {
            "cliente_nome": formulario.get("nome"),
            "telefone": formulario.get("telefone"),
            "pedido_citado": formulario.get("pedido"),
        }
    # O "protocolo repetido" só vira alerta DA CONVERSA quando ela é a conversa
    # própria da outra cliente (`externo`); no resto fica só no cartão do e-mail.
    alertas_novos = [
        str(a.get("codigo"))
        for a in (meta.alertas or [])
        if isinstance(a, dict)
        and a.get("codigo") in ALERTAS_DO_CHAMADO
        and a.get("codigo") != ALERTA_PROTOCOLO_REPETIDO
    ]
    if externo:
        alertas_novos.append(ALERTA_PROTOCOLO_REPETIDO)
    if (
        rota.site
        and conversa is not None
        and formulario is None
        and autor == AUTOR_CLIENTE
        and contato
        and contato not in clientes_da_conversa(conversa)
    ):
        # O e-mail direto (ou no fio) de um endereço que NÃO é o da cliente do
        # chamado: a faixa avisa (a resposta iria para ele — a prévia pede
        # confirmação). Ele nunca vira "cliente" do chamado por isso.
        alertas_novos.append(ALERTA_OUTRO_REMETENTE)
    if formulario is not None:
        nome_cliente = formulario.get("nome") or None
    else:
        nome_cliente = (email.de_nome or None) if autor == AUTOR_CLIENTE else None
    if conversa is None:
        plataforma = rota.plataforma or PLATAFORMA_SITE
        if integration is not None:
            plataforma = (
                str(getattr(integration.platform, "value", integration.platform) or "").lower()
                or plataforma
            )
        conversa, criada = await gravar.upsert_conversa(
            session,
            canal=None,
            integration=integration,
            plataforma=plataforma,
            canal_nome=CANAL_EMAIL,
            externo_id=externo or _externo_da_conversa(meta, rota),
            conta=rota.marca_nome if rota.site else (rota.loja_nome if rota.so_ficha else None),
            comprador_id=_comprador(contato),
            comprador_nome=nome_cliente,
            pedido_marketplace=pedido_marketplace,
            dados={"fonte": FONTE_TUTA, CHAVE: mail},
        )
        vinculado_por = vinculado_por or ("pedido" if pedido_marketplace else None)
    else:
        await gravar.travar_linha(session, conversa)
        dados = dict(conversa.dados or {})
        atual = dict(dados.get(CHAVE) or {})
        # O destaque fica se algum e-mail da conversa veio de problema/reclamação.
        mail["destaque"] = bool(atual.get("destaque")) or mail["destaque"]
        # O CHAMADO não muda pelo e-mail novo (RF6): o protocolo principal, o
        # tipo e a marca ficam os da conversa — no chamado agrupado, o e-mail
        # com o protocolo do outro não troca o principal (o mais antigo).
        for chave in _FIXOS_DO_CHAMADO:
            if atual.get(chave):
                mail.pop(chave, None)
        dados[CHAVE] = {**atual, **{k: v for k, v in mail.items() if v is not None}}
        dados["fonte"] = FONTE_TUTA
        conversa.dados = dados
        if conversa.comprador_id is None and autor == AUTOR_CLIENTE:
            c = _comprador(contato)
            if c:
                conversa.comprador_id = c
                conversa.comprador_nome = conversa.comprador_nome or nome_cliente
        if formulario is not None and nome_cliente and not conversa.comprador_nome:
            conversa.comprador_nome = nome_cliente
        if pedido_marketplace and not conversa.pedido_marketplace:
            conversa.pedido_marketplace = pedido_marketplace[:64]
    if rota.site and (any(extras.values()) or alertas_novos):
        dados = dict(conversa.dados or {})
        atual = dict(dados.get(CHAVE) or {})
        for chave, valor in extras.items():
            if valor and not atual.get(chave):
                atual[chave] = valor
        if alertas_novos:
            atual["alertas"] = list(dict.fromkeys([*(atual.get("alertas") or []), *alertas_novos]))
        dados[CHAVE] = atual
        conversa.dados = dados
    momento = gravar._utc(email.message.received_at) or datetime.now(UTC)
    if criada and not abre and autor != AUTOR_LOJA:
        # Histórico: a conversa nasce fora da fila e da métrica (RF5, vendas).
        conversa.sem_resposta_necessaria = True
    if abre:
        _vez_da_loja(conversa, momento)
    await session.flush()
    mensagem, _nova = await gravar.gravar_mensagem(
        session,
        conversa,
        externo_id=f"{PREFIXO_EMAIL}{meta.message_id}",
        autor=autor,
        texto=_texto_da_mensagem(protegido, email),
        enviada_em=momento,
        tipo="texto",
        anexos=[],
        payload=payload_da_mensagem(meta, pasta, protegido, email.message.attachment_count),
        origem=origem,
    )
    meta.conversa_id = conversa.id
    meta.mensagem_id = mensagem.id
    meta.vinculado_por = vinculado_por
    return conversa, mensagem


def _texto_da_mensagem(protegido: codigos.Protegido, email: Email) -> str:
    corpo = protegido.texto.strip()
    if not corpo:
        n = email.message.attachment_count
        corpo = f"(sem texto: {n} anexo{'s' if n != 1 else ''})" if n else "(sem texto)"
    return texto.cortar(corpo, TEXTO_NA_CONVERSA_MAX, _TEXTO_CORTADO)


async def _conversa_da_api(
    session: AsyncSession, integration_id: UUID | None, numero: str
) -> AtendimentoConversa | None:
    """A conversa da API (chat, pós-venda, SAC…) do pedido na loja — a mais recente."""
    if integration_id is None or not numero:
        return None
    return (
        await session.execute(
            select(AtendimentoConversa)
            .where(
                AtendimentoConversa.integration_id == integration_id,
                AtendimentoConversa.canal.not_in(_CANAIS_SEM_AVISO),
                or_(
                    AtendimentoConversa.pedido_marketplace == numero,
                    AtendimentoConversa.dados["pack_id"].astext == numero,
                    AtendimentoConversa.dados["order_id"].astext == numero,
                ),
            )
            .order_by(AtendimentoConversa.ultima_mensagem_em.desc().nulls_last())
            .limit(1)
        )
    ).scalar_one_or_none()


async def _amazon_pelo_gmail(
    session: AsyncSession, email: Email, rota: rotear.Rota
) -> AtendimentoMensagem | None:
    """A mesma mensagem da Amazon que já entrou pelo Gmail (`amazon_email`): liga, não duplica."""
    mid = "".join(str(email.conteudo.get("message_id") or "").split()).strip("<>")
    if not mid or rota.plataforma != "amazon":
        return None
    return (
        await session.execute(
            select(AtendimentoMensagem)
            .join(AtendimentoConversa, AtendimentoConversa.id == AtendimentoMensagem.conversa_id)
            .where(
                AtendimentoMensagem.externo_id.in_([f"<{mid}>", mid]),
                AtendimentoConversa.plataforma == "amazon",
                AtendimentoConversa.canal == CANAL_EMAIL,
                or_(
                    AtendimentoConversa.dados["fonte"].astext.is_(None),
                    AtendimentoConversa.dados["fonte"].astext != FONTE_TUTA,
                ),
            )
            .limit(1)
        )
    ).scalar_one_or_none()


# ── O protocolo dos sites (RF6) ───────────────────────────────────────────


def _conferir_protocolo(meta: MailMessageMeta, email: Email, rota: rotear.Rota) -> None:
    """RF6: lê o protocolo (nunca gera), confere a marca e o tipo × a caixa.

    Sem protocolo no formato → "SEM PROTOCOLO"; se há um número com CARA de
    protocolo fora do formato ("US-2026-0001", "UX-26-0001", "US-26-001"), o
    alerta diz isso (é o site que precisa corrigir) — o número errado nunca
    vira a chave do chamado."""
    prot = pedido.protocolo(email.assunto, texto.legivel(email.texto))
    if prot is None:
        meta.protocolo = None
        if pedido.protocolo_fora_do_formato(email.assunto, texto.legivel(email.texto)):
            _alerta(
                meta, ALERTA_PROTOCOLO_FORMATO, ROTULO_ALERTA_DO_CHAMADO[ALERTA_PROTOCOLO_FORMATO]
            )
        else:
            _alerta(meta, ALERTA_SEM_PROTOCOLO, "SEM PROTOCOLO: formulário sem o número do site")
        return
    meta.protocolo = prot.numero
    if not prot.valido:
        _alerta(
            meta, ALERTA_PROTOCOLO_INVALIDO, f"o protocolo {prot.numero} não existe (sem Atacado)"
        )
    marcas = pedido.MARCA_DA_LETRA.get(prot.letra, ())
    if rota.marca_slug and rota.marca_slug not in marcas:
        _alerta(
            meta, ALERTA_PROTOCOLO_OUTRA_MARCA, "a marca do protocolo não é a da caixa que recebeu"
        )
    if rota.tipo_caixa and rota.tipo_caixa != prot.tipo:
        # Vale o protocolo (RF6), com alerta.
        _alerta(meta, ALERTA_TIPO_X_CAIXA, f"o tipo do protocolo ({prot.tipo}) não é o da caixa")
    rota.tipo_caixa = prot.tipo
    meta.tipo_caixa = prot.tipo


async def _protocolo_repetido(
    session: AsyncSession,
    meta: MailMessageMeta,
    contato: str | None,
    campos: dict[str, str | None] | None = None,
) -> None:
    """O mesmo protocolo em outra conversa (outro cliente) → alerta (nunca junta sozinho).

    "Outro cliente" = não é a cliente da conversa (`_mesma_cliente`: o e-mail
    não é nenhum dos clientes dela — o comprador e os dos chamados agrupados
    nela; sem e-mail, o telefone ou o nome do formulário diferentes)."""
    if not meta.protocolo or not (contato or any((campos or {}).values())):
        return
    outras = (
        await session.execute(
            select(AtendimentoConversa)
            .join(MailMessageMeta, MailMessageMeta.conversa_id == AtendimentoConversa.id)
            .where(
                MailMessageMeta.protocolo == meta.protocolo,
                MailMessageMeta.message_id != meta.message_id,
            )
            .distinct()
        )
    ).scalars()
    if any(not _mesma_cliente(c, contato, campos) for c in outras):
        _alerta(
            meta,
            ALERTA_PROTOCOLO_REPETIDO,
            f"o protocolo {meta.protocolo} já apareceu noutro formulário",
        )


async def _externo_do_protocolo_repetido(
    session: AsyncSession, meta: MailMessageMeta
) -> str | None:
    """A chave da conversa NOVA de um protocolo que já tem conversa (de outra
    cliente, ou de outra marca): `mail-protocolo:<nº>:<id do e-mail>` — a de
    sempre (`mail-protocolo:<nº>`) devolveria a conversa da outra. None = a
    chave de sempre está livre."""
    base = f"{PREFIXO_PROTOCOLO}{meta.protocolo}"
    tomada = await session.scalar(
        select(AtendimentoConversa.id)
        .where(AtendimentoConversa.canal == CANAL_EMAIL, AtendimentoConversa.externo_id == base)
        .limit(1)
    )
    if tomada is None:
        return None
    return f"{base}:{meta.message_id}"[:191]


# ── Enviados: a nossa resposta voltando ou a resposta dada no Tuta ────────


async def _enviado(
    session: AsyncSession,
    meta: MailMessageMeta,
    email: Email,
    *,
    pasta: MailFolder | None,
    protegido: codigos.Protegido,
    rota: rotear.Rota,
    agora: datetime,
) -> None:
    """O e-mail que NÓS mandamos (Enviados): adota a nossa resposta, ou grava a dada no Tuta."""
    # 1. A nossa resposta (pelo Message-ID do recibo).
    if meta.mid_hash:
        nossa = (
            await session.execute(
                select(MailOutboxMeta).where(
                    MailOutboxMeta.enviado_mid_hash == meta.mid_hash,
                    MailOutboxMeta.atendimento_mensagem_id.is_not(None),
                )
            )
        ).scalar_one_or_none()
        if nossa is not None:
            meta.conversa_id = nossa.conversa_id
            meta.mensagem_id = nossa.atendimento_mensagem_id
            meta.vinculado_por = "nossa_resposta"
            meta.estado = ESTADO_GRAVADO
            return
    # 2. Responde a um e-mail que temos: há resposta NOSSA (da conversa) na fila
    # para ele? O job da caixa crua (`origem = caixa`, que o `queue_reply`
    # marca na caixa empresa) não é resposta da conversa: a cópia dele cai no
    # passo 3 e entra no fio como a resposta dada fora do DaVinci — senão a
    # conversa continuava "aguardando" e alguém respondia de novo (crítica
    # pré-subida 2 de 08/10).
    originais = (
        list(
            (
                await session.execute(
                    select(MailMessageMeta.message_id).where(
                        MailMessageMeta.mid_hash.in_(email.refs[:1]),
                        MailMessageMeta.conversa_id.is_not(None),
                    )
                )
            ).scalars()
        )
        if email.refs
        else []
    )
    if originais:
        jobs = (
            await session.execute(
                select(MailOutbox, MailOutboxMeta)
                .join(MailOutboxMeta, MailOutboxMeta.outbox_id == MailOutbox.id)
                .where(
                    MailOutbox.message_id.in_(originais),
                    MailOutboxMeta.origem == "conversa",
                    MailOutbox.status != "failed",
                    MailOutbox.created_at <= email.message.received_at + timedelta(minutes=5),
                )
                .order_by(MailOutbox.created_at.desc())
            )
        ).all()
        for job, liga in jobs:
            if job.status in JOBS_VIVOS:
                if gravar._utc(email.message.created_at) > agora - ESPERA_RECIBO:
                    # O Enviados foi lido antes do recibo: espera a próxima volta.
                    raise _Esperar
                continue
            if liga.enviado_mid_hash not in (None, meta.mid_hash):
                # O recibo disse outro Message-ID: este enviado não é o nosso.
                continue
            meta.conversa_id = liga.conversa_id
            meta.mensagem_id = liga.atendimento_mensagem_id
            meta.vinculado_por = "nossa_resposta"
            meta.estado = ESTADO_GRAVADO
            if liga.enviado_mid_hash is None and meta.mid_hash:
                # O recibo veio sem o Message-ID: este passa a ser o da nossa
                # (um 2º enviado para o mesmo e-mail já não casa).
                liga.enviado_mid_hash = meta.mid_hash
            return
    # 3. A resposta dada DIRETO no Tuta pela equipe: entra no fio como `externo`
    # (só numa conversa da ponte: nunca na da Amazon que veio pelo Gmail).
    candidatas = await _conversas(session, await _conversas_do_fio(session, meta, email))
    conversa = next(
        (
            c
            for c in candidatas
            if e_conversa_da_ponte(c) and (not rota.tem_loja or _da_mesma_loja(c, rota))
        ),
        None,
    )
    if conversa is None:
        meta.estado = ESTADO_IGNORADO
        meta.motivo = IGNORADO_ENVIADO_SEM_CONVERSA
        return
    rota_da_conversa = rotear.Rota(
        alias=email.de,
        integration_id=conversa.integration_id,
        plataforma=conversa.plataforma,
        marca_slug=dados_mail(conversa).get("marca"),
        marca_nome=dados_mail(conversa).get("marca_nome"),
        tipo_caixa=dados_mail(conversa).get("caixa"),
    )
    meta.integration_id = conversa.integration_id
    _conversa, mensagem = await gravar_na_conversa(
        session,
        meta,
        email,
        rota_da_conversa,
        pasta=pasta,
        protegido=protegido,
        autor=AUTOR_LOJA,
        contato=None,
        pedido_marketplace=None,
        conversa=conversa,
        vinculado_por="fio",
        abre=False,
        origem=ORIGEM_EXTERNO,
    )
    mensagem.payload = {
        **(mensagem.payload or {}),
        CHAVE: {**((mensagem.payload or {}).get(CHAVE) or {}), "fora_do_davinci": True},
    }
    meta.estado = ESTADO_GRAVADO


# ── Um e-mail ─────────────────────────────────────────────────────────────


async def _reservar(session: AsyncSession, message: MailMessage) -> MailMessageMeta | None:
    """A trava: a linha da meta. None = outra volta já pegou este e-mail."""
    reservado = await session.scalar(
        pg_insert(MailMessageMeta)
        .values(message_id=message.id, mailbox_id=message.mailbox_id, estado=ESTADO_NOVO)
        .on_conflict_do_nothing(index_elements=["message_id"])
        .returning(MailMessageMeta.message_id)
    )
    if reservado is None:
        return None
    return await session.get(MailMessageMeta, message.id)


def _so_estado(meta: MailMessageMeta, estado: str, motivo: str | None, agora: datetime) -> str:
    """Fecha a meta com o estado e o motivo, sem mais nada em claro."""
    meta.estado = estado
    meta.motivo = motivo
    meta.regras_versao = REGRAS_VERSAO
    meta.processado_em = agora
    return estado


async def processar(
    session: AsyncSession,
    message_id: UUID,
    *,
    agora: datetime | None = None,
    rota_forcada: rotear.Rota | None = None,
) -> str | None:
    """Um e-mail pela ponte → o estado (None = não é com a ponte ou outra volta pegou).

    `rota_forcada`: a loja que uma PESSOA escolheu (fila "sem loja"). Não
    commita (quem chama commita POR E-MAIL). `_Esperar` = deixar para a
    próxima volta (desfazer a transação).
    """
    agora = agora or datetime.now(UTC)
    message = await session.get(MailMessage, message_id)
    if message is None:
        return None
    cfg = await config_caixa.config_da_caixa(session, message.mailbox_id)
    if not cfg.ponte_ligada or (
        cfg.ponte_desde is not None and gravar._utc(message.received_at) < cfg.ponte_desde
    ):
        return None
    meta = await _reservar(session, message)
    if meta is None:
        return None
    mailbox = await session.get(MailMailbox, message.mailbox_id)
    if mailbox is None:
        return None
    email = decifrar(message)
    principal, aliases = aliases_da_caixa(mailbox)
    cad = await rotear.cadastro(session)
    enviado = message.direction == "sent"

    # 1. O filtro da caixa (a privada só leva o e-mail das LOJAS).
    alias_filtro: str | None = None
    if cfg.ponte_so_aliases_de_loja:
        alias_filtro = alias_de_loja(
            email, principal=principal, aliases=aliases, e_de_loja=cad.e_de_loja, enviado=enviado
        )
        if alias_filtro is None:
            return _so_estado(meta, ESTADO_PRIVADO, PRIVADO_NAO_E_DE_LOJA, agora)

    # 2. A pasta.
    pasta, classe = await pastas.garantir(
        session, message.mailbox_id, pastas.do_conteudo(email.conteudo), agora=agora
    )
    meta.folder_id = pasta.id
    if pastas.so_contar(pasta):
        motivo = IGNORADO_PASTA_NAO_LER if pasta.ignorar else IGNORADO_PASTA_SO_CONTAR
        return _so_estado(meta, ESTADO_IGNORADO, motivo, agora)

    # 3. Quem escreveu, e os endereços da caixa que receberam.
    conhecidos = aliases | cad.enderecos_completos() | set(cad.caixas)
    tipo = remetente_tipo(email.de, conhecidos)
    nossos = rotear.nossos_do_email(recebeu=email.recebeu, aliases=aliases)

    # 4. Segurança: código, senha, acesso — nunca vira conversa. Só de PESSOA
    # num lugar de pessoa (o formulário do site, o cliente na caixa de site
    # ou num fio da equipe; nunca num login de loja) a regra afrouxa: aí só o
    # código de acesso de verdade esconde ("esqueci minha senha" de um
    # cliente é atendimento).
    de_pessoa = await de_pessoa_para_seguranca(
        session,
        email,
        tipo=tipo,
        nossos=nossos,
        cad=cad,
        marca_pasta=classe.marca if classe.plataforma == PLATAFORMA_SITE else None,
    )
    if codigos.e_de_seguranca(email.assunto, texto.legivel(email.texto), de_pessoa=de_pessoa):
        meta.codigo_mascarado = True
        return _so_estado(meta, ESTADO_SEGURANCA, SEGURANCA_CODIGO, agora)
    if tipo == REMETENTE_TUTA:
        return _so_estado(meta, ESTADO_IGNORADO, IGNORADO_AVISO_DO_TUTA, agora)
    if (
        not enviado
        and nossos
        and all(
            enderecos.local(a) in LOCAIS_INTERNOS and not cad.e_de_loja(a) and a not in cad.caixas
            for a in nossos
        )
    ):
        # Só para endereços internos (adm@, financeiro@, ti@…): nunca vai para a fila.
        return _so_estado(meta, ESTADO_IGNORADO, IGNORADO_ALIAS_INTERNO, agora)

    # Daqui em diante o e-mail é de loja (ou da caixa da empresa): metadado em claro.
    meta.remetente_tipo = tipo
    meta.mid_hash = hash_mid(email.conteudo.get("message_id"))
    meta.resposta_a_hash = hash_mid(email.conteudo.get("in_reply_to"))
    meta.tuta_id = tuta_id_de(message, email.tuta)
    fio = str(email.tuta.get("conversation_id") or "").strip()
    meta.fio_tuta = fio[:191] or None
    meta.auth_status = str(email.tuta.get("auth_status") or "")[:8] or None
    meta.phishing_status = str(email.tuta.get("phishing_status") or "")[:8] or None
    meta.plataforma = classe.plataforma
    meta.finalidade = classe.finalidade
    meta.alias_recebido = (
        (email.de if enviado else None) or alias_filtro or (nossos[0] if nossos else None)
    )
    meta.regras_versao = REGRAS_VERSAO
    meta.processado_em = agora

    # 5. O mesmo e-mail que a ponte já levou à equipe (outra caixa, outro alias).
    if meta.mid_hash:
        primeiro = await session.scalar(
            select(MailMessageMeta.message_id)
            .where(
                MailMessageMeta.mid_hash == meta.mid_hash,
                MailMessageMeta.estado.in_(ESTADOS_DA_EQUIPE),
                MailMessageMeta.message_id != message.id,
            )
            .limit(1)
        )
        if primeiro is not None:
            meta.duplicado_de = primeiro
            meta.estado = ESTADO_DUPLICADO
            return meta.estado

    protegido = codigos.proteger(email.assunto, texto.texto_novo(email.texto))
    meta.codigo_mascarado = protegido.codigo_mascarado
    avaliado = suspeito.avaliar(
        de_endereco=email.de,
        de_nome=email.de_nome,
        reply_to=email.reply_to,
        auth_status=meta.auth_status,
        phishing_status=meta.phishing_status,
        envelope_diferente=(
            enderecos.normalizar(email.tuta.get("envelope_sender")) or None
            if enderecos.normalizar(email.tuta.get("envelope_sender")) != email.de
            else None
        ),
        cabecalhos=email.conteudo.get("raw_headers")
        if isinstance(email.conteudo.get("raw_headers"), str)
        else None,
        plataforma_pasta=classe.plataforma,
    )
    meta.suspeito = avaliado.suspeito
    meta.suspeito_motivos = avaliado.motivos

    if enviado or (tipo == REMETENTE_NOSSO and classe.finalidade == FINALIDADE_ENVIADOS):
        rota = rotear.rotear(
            recebeu=email.recebeu,
            de=email.de,
            enviado_por_nos=True,
            plataforma_pasta=None,
            marca_pasta=None,
            caixa_pasta=None,
            aliases=aliases,
            cad=cad,
        )
        meta.store_info_id = rota.store_info_id
        meta.integration_id = rota.integration_id
        meta.marca_id = rota.marca_id
        await _enviado(
            session, meta, email, pasta=pasta, protegido=protegido, rota=rota, agora=agora
        )
        return meta.estado

    # 6. A loja.
    rota = rota_forcada or rotear.rotear(
        recebeu=email.recebeu,
        de=email.de,
        enviado_por_nos=False,
        plataforma_pasta=classe.plataforma,
        marca_pasta=classe.marca,
        caixa_pasta=classe.tipo_caixa,
        aliases=aliases,
        cad=cad,
    )
    if rota_forcada is not None and not rota.alias and meta.alias_recebido != principal:
        # Nunca o endereço principal da conta (o login do Tuta) como "o alias".
        rota.alias = meta.alias_recebido
    if rota.motivo_sem_loja == SEM_LOJA_MARCA_AMBIGUA:
        # O mesmo domínio em duas marcas: a letra do protocolo decide (C → Charlots).
        prot = pedido.protocolo(email.assunto, texto.legivel(email.texto))
        if prot is not None:
            rota = rotear.marca_pelo_protocolo(rota, pedido.MARCA_DA_LETRA.get(prot.letra, ()), cad)
    # O formulário do site chega DE sac@ PARA sac@: o cliente está no corpo (E5).
    formulario = False
    contato_form = ""
    campos_form: dict[str, str | None] = {}
    if tipo == REMETENTE_NOSSO:
        if not rota.site:
            return _so_estado(meta, ESTADO_INTERNO, None, agora)
        formulario = True
        contato_form = enderecos.email_do_formulario(texto.legivel(email.texto), conhecidos)
        # Nome, telefone e nº do pedido do formulário (RF6), lidos do texto JÁ
        # protegido (o mesmo que a equipe vê): o nome do chamado é o da
        # cliente, nunca o do site que mandou o e-mail.
        campos_form = enderecos.campos_do_formulario(protegido.texto)
        tipo = REMETENTE_COMPRADOR
        meta.remetente_tipo = tipo
    if rota_forcada is None and not rota.tem_loja and rota.motivo_sem_loja in _SEM_LOJA_PELO_FIO:
        pelo_fio = await _rota_pelo_fio(session, meta, email, nossos)
        if pelo_fio is not None:
            rota = pelo_fio
    meta.alias_recebido = rota.alias or meta.alias_recebido
    meta.store_info_id = rota.store_info_id
    meta.integration_id = rota.integration_id
    meta.marca_id = rota.marca_id
    meta.tipo_caixa = rota.tipo_caixa
    meta.sugestoes = rota.sugestoes
    if not rota.tem_loja:
        meta.estado = ESTADO_SEM_LOJA
        meta.motivo = rota.motivo_sem_loja
        return meta.estado
    meta.motivo = None
    meta.plataforma = rota.plataforma or meta.plataforma
    if rota.so_ficha:
        # A loja é a ficha (sem integração): entra na conversa dela, com o aviso.
        _alerta(meta, "loja_sem_integracao", ALERTA_LOJA_SEM_INTEGRACAO)

    # A Amazon que já entrou pelo Gmail: a mesma mensagem, ligada (nunca duplicada).
    ja = await _amazon_pelo_gmail(session, email, rota)
    if ja is not None:
        meta.conversa_id = ja.conversa_id
        meta.mensagem_id = ja.id
        meta.vinculado_por = "amazon_gmail"
        meta.estado = ESTADO_GRAVADO
        return meta.estado

    autor = AUTOR_CLIENTE if tipo == REMETENTE_COMPRADOR else AUTOR_SISTEMA
    if meta.finalidade == FINALIDADE_VENDAS or auto_resposta(email):
        autor = AUTOR_SISTEMA
    abre = abre_pendencia(plataforma=rota.plataforma, finalidade=meta.finalidade, autor=autor)
    if autor == AUTOR_SISTEMA and assunto_sem_pendencia(email.assunto):
        # Resumo/marketing da plataforma: só histórico.
        abre = False

    # A devolução do servidor do cliente: aviso na conversa do fio (ou nada).
    if enderecos.e_tecnico(email.de):
        candidatas = [
            c
            for c in await _conversas(session, await _conversas_do_fio(session, meta, email))
            if e_conversa_da_ponte(c)
        ]
        if not candidatas:
            return _so_estado(meta, ESTADO_IGNORADO, IGNORADO_DEVOLUCAO, agora)
        await gravar_na_conversa(
            session,
            meta,
            email,
            rota,
            pasta=pasta,
            protegido=protegido,
            autor=AUTOR_SISTEMA,
            contato=None,
            pedido_marketplace=None,
            conversa=candidatas[0],
            vinculado_por="devolucao",
            abre=False,
        )
        _alerta(meta, "devolvido", "o e-mail voltou (devolução do servidor do cliente)")
        meta.estado = ESTADO_GRAVADO
        return meta.estado

    # O pedido (marketplace) ou o protocolo (site, RF6).
    pedido_achado: str | None = None
    if formulario:
        contato = contato_form or None
    else:
        contato = email.de if autor == AUTOR_CLIENTE else None
    if rota.site:
        _conferir_protocolo(meta, email, rota)
        await _protocolo_repetido(session, meta, contato, campos_form if formulario else None)
    else:
        textos = (email.assunto, texto.texto_novo(email.texto))
        citados = pedido.citados(rota.plataforma, *textos)
        existentes: list[str] = []
        for numero in citados:
            if await pedido.existe_na_loja(
                session,
                integration_id=rota.integration_id,
                plataforma=rota.plataforma or "",
                numero=numero,
                store_info_id=rota.store_info_id if rota.so_ficha else None,
            ):
                existentes.append(numero)
        meta.pedidos_citados = [
            {"pedido": n, "existe": n in existentes, "plataforma": rota.plataforma} for n in citados
        ]
        outras = pedido.de_outra_plataforma(rota.plataforma, *textos)
        if outras and not existentes:
            _alerta(
                meta,
                "pasta_x_conteudo",
                f"o e-mail cita pedido no formato de {', '.join(outras)}, não de {rota.plataforma}",
            )
        if citados and not existentes:
            _alerta(meta, "pedido_nao_encontrado", "pedido citado, não encontrado nesta loja")
        if len(existentes) > MAX_PEDIDOS_POR_EMAIL:
            # Resumo diário/lista: fica registrado, sem conversa por pedido.
            meta.estado = ESTADO_RESUMO
            return meta.estado
        if len(existentes) == 1 and not meta.suspeito:
            pedido_achado = existentes[0]
            meta.pedido_marketplace = pedido_achado
        elif len(existentes) > 1:
            _alerta(meta, "varios_pedidos", "o e-mail cita mais de um pedido desta loja: escolha")
        if meta.suspeito and existentes:
            _alerta(meta, "suspeito_sem_vinculo", "remetente suspeito: não foi ligado ao pedido")

    # O aviso da plataforma com API vai para a conversa DA API do pedido.
    if (
        autor == AUTOR_SISTEMA
        and tipo == REMETENTE_AVISO
        and pedido_achado
        and rota.plataforma in PLATAFORMAS_EMAIL_SO_AVISO
        and not meta.suspeito
    ):
        da_api = await _conversa_da_api(session, rota.integration_id, pedido_achado)
        if da_api is not None:
            await gravar.travar_linha(session, da_api)
            mensagem, _nova = await gravar.gravar_mensagem(
                session,
                da_api,
                externo_id=f"{PREFIXO_EMAIL}{meta.message_id}",
                autor=AUTOR_SISTEMA,
                texto=texto.cortar(
                    f"E-mail da plataforma ({pasta.nome}): {protegido.assunto or '(sem assunto)'}"
                    f"\n\n{_texto_da_mensagem(protegido, email)}",
                    TEXTO_NA_CONVERSA_MAX,
                    _TEXTO_CORTADO,
                ),
                enviada_em=gravar._utc(message.received_at),
                payload={
                    CHAVE: {
                        **payload_da_mensagem(meta, pasta, protegido, message.attachment_count)[
                            CHAVE
                        ],
                        "aviso_api": True,
                    }
                },
            )
            meta.conversa_id = da_api.id
            meta.mensagem_id = mensagem.id
            meta.vinculado_por = "aviso_api"
            meta.estado = ESTADO_GRAVADO
            return meta.estado

    conversa, vinculado = await achar_conversa(
        session,
        meta,
        email,
        rota,
        pedido_marketplace=pedido_achado,
        agora=agora,
        contato=contato,
        # O protocolo do FORMULÁRIO só junta com a conversa da mesma cliente (RF6).
        so_mesma_cliente=formulario,
        campos=campos_form if formulario else None,
    )
    externo = None
    if conversa is None and rota.site and meta.protocolo:
        externo = await _externo_do_protocolo_repetido(session, meta)
    conversa, _ = await gravar_na_conversa(
        session,
        meta,
        email,
        rota,
        pasta=pasta,
        protegido=protegido,
        autor=autor,
        contato=contato,
        pedido_marketplace=pedido_achado,
        conversa=conversa,
        vinculado_por=vinculado,
        abre=abre,
        principal=principal,
        formulario=campos_form if formulario else None,
        externo=externo,
    )
    if rota.site:
        meta.estado = ESTADO_GRAVADO
    else:
        meta.estado = ESTADO_GRAVADO if conversa.pedido_marketplace else ESTADO_SEM_VINCULO
    return meta.estado


async def marcar_erro(session: AsyncSession, message_id: UUID, codigo: str) -> None:
    """A ponte falhou num e-mail: a meta `erro` (sem nada do e-mail) — sai da volta."""
    message = await session.get(MailMessage, message_id)
    if message is None:
        return
    await session.execute(
        pg_insert(MailMessageMeta)
        .values(
            message_id=message.id,
            mailbox_id=message.mailbox_id,
            estado=ESTADO_ERRO,
            motivo=codigo[:48],
            regras_versao=REGRAS_VERSAO,
            processado_em=datetime.now(UTC),
        )
        .on_conflict_do_nothing(index_elements=["message_id"])
    )


async def reprocessar(
    session: AsyncSession,
    message_id: UUID,
    *,
    rota_forcada: rotear.Rota | None = None,
    agora: datetime | None = None,
) -> str | None:
    """De novo pela ponte o e-mail SEM LOJA ou com ERRO (cadastro corrigido, loja escolhida).

    Só esses dois: nunca o que já está numa conversa (não cria 2ª mensagem nem
    muda a loja de um e-mail já respondido). Com `rota_forcada` (a pessoa
    escolheu a loja), o roteamento é o dela. Não commita.
    """
    meta = await session.get(MailMessageMeta, message_id)
    if meta is None or meta.estado not in (ESTADO_SEM_LOJA, ESTADO_ERRO):
        return meta.estado if meta is not None else None
    if meta.mensagem_id is not None:
        return meta.estado
    cfg = await config_caixa.config_da_caixa(session, meta.mailbox_id)
    if not cfg.ponte_ligada:
        # Ponte desligada: nada é refeito (o e-mail fica como está, na fila).
        return meta.estado
    try:
        async with session.begin_nested():
            await session.delete(meta)
            await session.flush()
            rotear.esquecer(session)
            estado = await processar(session, message_id, agora=agora, rota_forcada=rota_forcada)
    except _Esperar:
        # A Amazon que ainda espera o Gmail: o e-mail fica como estava (na
        # fila); a pessoa tenta de novo depois.
        return await session.scalar(
            select(MailMessageMeta.estado).where(MailMessageMeta.message_id == message_id)
        )
    if rota_forcada is not None:
        meta = await session.get(MailMessageMeta, message_id)
        if meta is not None and meta.conversa_id is not None and meta.vinculado_por is None:
            meta.vinculado_por = "manual"
    return estado


# ── A volta da resposta: o job da fila dele → a mensagem da conversa ──────


async def sincronizar_envios(session: AsyncSession, *, limite: int = 200) -> int:
    """Passa o status do job (`mail_outbox`) para a mensagem `enviando` da conversa.

    sent → enviada (com a hora do recibo); failed → falhou (o porquê legível;
    a conversa volta para a fila); uncertain → revisar (pode ter saído: alguém
    confere no Tuta). Só mexe quando o job mudou desde a última vez
    (`status_visto`). Não commita.
    """
    linhas = (
        await session.execute(
            select(MailOutboxMeta, MailOutbox)
            .join(MailOutbox, MailOutbox.id == MailOutboxMeta.outbox_id)
            .where(
                MailOutboxMeta.atendimento_mensagem_id.is_not(None),
                MailOutbox.status.in_(("sent", "failed", "uncertain")),
                or_(
                    MailOutboxMeta.status_visto.is_(None),
                    MailOutboxMeta.status_visto != MailOutbox.status,
                ),
            )
            .order_by(MailOutbox.updated_at)
            .limit(limite)
            # O recibo chegou por OUTRA sessão (a rota do agente): relê o job.
            .execution_options(populate_existing=True)
        )
    ).all()
    feitos = 0
    for liga, job in linhas:
        await aplicar_status(session, liga, job)
        feitos += 1
    await session.flush()
    return feitos


async def aplicar_status(session: AsyncSession, liga: MailOutboxMeta, job: MailOutbox) -> None:
    """O status do job na mensagem do /atendimento (e a fila da conversa refeita)."""
    agora = datetime.now(UTC)
    liga.status_visto = job.status
    liga.sincronizado_em = agora
    if job.status == "sent" and job.receipt_enc:
        recibo = decrypt_json(job.receipt_enc)
        mid = hash_mid(recibo.get("message_id")) if isinstance(recibo, dict) else None
        if mid:
            liga.enviado_mid_hash = mid
    if liga.atendimento_mensagem_id is None:
        return
    mensagem = await session.get(AtendimentoMensagem, liga.atendimento_mensagem_id)
    conversa = (
        await session.get(AtendimentoConversa, mensagem.conversa_id)
        if mensagem is not None
        else None
    )
    if mensagem is None or conversa is None:
        return
    await gravar.travar_linha(session, conversa)
    await session.refresh(mensagem)
    novo = STATUS_DO_JOB.get(job.status)
    if novo is None or mensagem.status == novo:
        return
    if mensagem.status == MSG_ENVIADA and novo != MSG_FALHOU:
        return  # já confirmada (conferida por pessoa)
    mensagem.status = novo
    if novo == MSG_ENVIADA:
        mensagem.erro = None
        mensagem.enviada_em = mensagem.enviada_em or gravar._utc(job.completed_at) or agora
    elif novo == MSG_FALHOU:
        codigo = job.error_code or "failed"
        mensagem.erro = (
            f"mail:{codigo} — {FRASE_DA_FALHA.get(codigo, 'o Mac não conseguiu enviar')}"[:500]
        )
    elif novo == MSG_REVISAR:
        codigo = job.error_code or "uncertain"
        mensagem.erro = (
            f"mail:{codigo} — {FRASE_DA_FALHA.get(codigo, 'pode ter saído: confira no Tuta')}"[:500]
        )
    mensagem.payload = {
        **(mensagem.payload or {}),
        "mail_envio": {
            **((mensagem.payload or {}).get("mail_envio") or {}),
            "status": job.status,
            "codigo": job.error_code,
        },
    }
    await gravar.recalcular_conversa(session, conversa)


# ── A volta do worker ─────────────────────────────────────────────────────


async def pendentes(
    session: AsyncSession,
    *,
    limite: int = PONTE_POR_VOLTA,
    excluir: set[UUID] | frozenset[UUID] = frozenset(),
) -> list[UUID]:
    """Os e-mails das caixas com ponte ligada, depois do corte, ainda sem meta
    (fora os `excluir`: os que esperam nesta volta)."""
    sem_meta = ~exists().where(MailMessageMeta.message_id == MailMessage.id)
    consulta = (
        select(MailMessage.id)
        .join(MailMailboxSettings, MailMailboxSettings.mailbox_id == MailMessage.mailbox_id)
        .where(
            MailMailboxSettings.ponte_ligada.is_(True),
            or_(
                MailMailboxSettings.ponte_desde.is_(None),
                MailMessage.received_at >= MailMailboxSettings.ponte_desde,
            ),
            sem_meta,
        )
    )
    if excluir:
        consulta = consulta.where(MailMessage.id.not_in(list(excluir)))
    linhas = (
        await session.execute(
            consulta.order_by(MailMessage.received_at, MailMessage.id).limit(limite)
        )
    ).scalars()
    return list(linhas)


async def rodar(session: AsyncSession, *, limite: int = PONTE_POR_VOLTA) -> dict[str, int]:
    """Uma volta da ponte: os e-mails novos (commit por e-mail) e a volta das respostas.

    Um e-mail que falha vira `erro` (sem nada dele guardado) e não derruba os
    outros; o que espera (o recibo, o Gmail da Amazon) fica para a próxima
    volta e SAI da busca desta: o e-mail de trás é lido na mesma volta (os
    que esperam são os mais velhos e travavam a fila inteira por até 6 h —
    crítica pré-subida 2 de 08/10). `limite` conta os e-mails lidos; os que
    esperam têm um teto à parte (`ESPERA_POR_VOLTA` × o limite).
    """
    resumo = {"processados": 0, "erros": 0, "esperando": 0, "envios": 0}
    estados: dict[str, int] = {}
    # A regra de palavras e o cadastro de lojas são relidos a cada volta (uma
    # pessoa pode ter mudado entre uma e outra).
    regras.esquecer(session)
    rotear.esquecer(session)
    pulados: set[UUID] = set()
    lidos = 0
    while lidos < limite and len(pulados) < limite * ESPERA_POR_VOLTA:
        lote = await pendentes(session, limite=limite - lidos, excluir=pulados)
        if not lote:
            break
        for message_id in lote:
            try:
                estado = await processar(session, message_id)
                await session.commit()
            except _Esperar:
                await session.rollback()
                resumo["esperando"] += 1
                pulados.add(message_id)
                continue
            except Exception as e:  # noqa: BLE001 — um e-mail não derruba os outros
                await session.rollback()
                logger.warning(
                    "mail_ponte_email_falhou", message_id=str(message_id), erro=type(e).__name__
                )
                lidos += 1
                try:
                    await marcar_erro(session, message_id, type(e).__name__)
                    await session.commit()
                except Exception:  # noqa: BLE001
                    await session.rollback()
                    pulados.add(message_id)  # nem a meta de erro: não volta nesta volta
                resumo["erros"] += 1
                continue
            lidos += 1
            if estado is None:
                pulados.add(message_id)  # sem meta (outra volta pegou): não busca de novo
                continue
            resumo["processados"] += 1
            estados[estado] = estados.get(estado, 0) + 1
    try:
        resumo["envios"] = await sincronizar_envios(session)
        await session.commit()
    except Exception as e:  # noqa: BLE001
        await session.rollback()
        logger.warning("mail_ponte_envios_falhou", erro=type(e).__name__)
    if resumo["processados"] or resumo["erros"] or resumo["envios"]:
        logger.info("mail_ponte_volta", **resumo, **{f"estado_{k}": v for k, v in estados.items()})
    return resumo
