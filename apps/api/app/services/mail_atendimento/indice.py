"""O ÍNDICE LEVE da aba E-mail › Caixas: a pasta e a loja de cada e-mail (09/10/2026).

O dono pediu (print da aba E-mail › Caixas): "visualmente na aba de e-mail
poderia trazer o nome da loja aqui também; deixar separado e visualmente
igual ao Tuta". A lista da Central do outro dev mostra remetente, assunto e
data; a PASTA e os endereços que dizem a LOJA ficam dentro do conteúdo
cifrado. Saber a pasta de cada e-mail é decifrar todos — e o leitor dele traz
as ~40 pastas da conta (milhares de e-mails). Medido com 20 mil e-mails
(`tests/test_mail_caixa_volume.py`, 09/10): sem índice, cada clique seria
~0,7 s só para saber as pastas e ~66 s com a loja e a segurança (3,3 ms por
e-mail, quase tudo `codigos.e_de_seguranca`); pelo índice, as consultas
levam < 7 ms e as rotas ~40–60 ms. Daí `mail_caixa_indice`, 1 linha por
e-mail, SÓ com:

  • a PASTA: a de sistema pelo tipo do Tuta ("s1" Entrada, "s6" Rascunhos…);
    a pessoal por uma chave OPACA (HMAC da chave da pasta, com segredo do
    servidor). O NOME nunca fica em claro (crítica de 08/10: a pasta de um
    e-mail privado nunca vira linha em claro) — ele sai do e-mail decifrado
    na hora de mostrar (um por pasta, ou os da página);
  • SEGURANÇA: `codigos.e_de_seguranca`, a regra ESTRITA (sem o afrouxo "de
    pessoa", que precisa do fio da equipe): a lista não mostra o assunto;
  • a loja PROVÁVEL: `rotear.rotear` (PURO, sobre `rotear.cadastro`), com os
    endereços da caixa e a plataforma da pasta (`regras.classificar`, ou a
    escolha de pessoa já gravada em `mail_folders`) — a ficha, a integração
    ou a marca, ou "sem loja", ou "privado" (caixa "só aliases de loja" e o
    e-mail não chegou num alias de loja, como a ponte decide).

Nada de assunto, remetente, texto ou endereço no índice. E o índice NUNCA
grava meta, nunca cria conversa, nunca muda estado nem pasta da ponte
(`mail_folders` é só LIDA).

O SELO que a tela mostra junta as duas fontes (`colunas_do_selo`, em SQL, a
mesma conta para listar, contar e filtrar): o que a PONTE decidiu
(`mail_message_meta`: estado e loja), quando o e-mail passou por ela; senão o
provável daqui (`fonte = indice`).

Quem preenche: o job do worker (`mail_caixa_indice`, a cada minuto no
segundo 30 — longe da ponte e dos outros crons, que começam no segundo 0)
refaz tudo o que falta ou ficou velho: o e-mail sem linha, o e-mail que mudou
(`fonte_em` ≠ o `updated_at` dele: o conector v2 regrava a pasta ao mover), a
BASE que mudou (`base`: hash do cadastro de lojas, das regras de palavras, dos
endereços da caixa e da escolha de pessoa nas pastas) e a versão deste
cálculo (`INDICE_VERSAO`). Um lote de cada caixa por vez (uma fila grande numa
caixa não deixa a outra esperando). A própria rota faz só o URGENTE — o
e-mail sem linha ou que mudou de pasta, até `ROTA_MAXIMO`, o mais novo
primeiro (a 1ª página de "Todas" fica sempre certa); a linha de base velha
continua mostrando o provável de antes até o job passar.

O cálculo é CPU (a regra de segurança, ~1,2 ms por KB de texto). Para não
travar a API nem o worker (revisão de 09/10: com o job no mesmo processo, a
ponte ficava 6 a 9 vezes mais lenta, e um e-mail de 2 MB parava o event loop
por 3 s): a regra lê no máximo `TETO_TEXTO` do texto (e-mail de código é
curto; o assunto vai sempre inteiro); depois de cada e-mail, o job descansa
o mesmo tempo que gastou nele (no máximo metade do event loop) e a rota,
metade; e a volta do job confere o prazo a cada e-mail, não só entre lotes.

Texto de e-mail nunca vai para o log: só ids, códigos e contagens.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
import unicodedata
from dataclasses import dataclass
from functools import cache
from typing import Any
from uuid import UUID

import structlog
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from sqlalchemy import String, and_, case, cast, func, literal, not_, or_, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.mail import MailMailbox, MailMessage
from app.models.mail_atendimento import MailCaixaIndice, MailFolder, MailMessageMeta
from app.services.mail_atendimento import caixa as config_caixa
from app.services.mail_atendimento import (
    codigos,
    pastas,
    pedido,
    ponte,
    regras,
    rotear,
    texto,
)
from app.services.mail_atendimento.constantes import (
    ESTADO_DUPLICADO,
    ESTADO_GRAVADO,
    ESTADO_IGNORADO,
    ESTADO_INTERNO,
    ESTADO_PRIVADO,
    ESTADO_RESUMO,
    ESTADO_SEGURANCA,
    ESTADO_SEM_LOJA,
    ESTADO_SEM_VINCULO,
    FINALIDADE_ENVIADOS,
    IGNORADO_ALIAS_INTERNO,
    IGNORADO_AVISO_DO_TUTA,
    IGNORADO_DEVOLUCAO,
    IGNORADO_ENVIADO_SEM_CONVERSA,
    KIND_AGENDADOS,
    KIND_ARQUIVO,
    KIND_ENTRADA,
    KIND_ENVIADOS,
    KIND_LIXEIRA,
    KIND_PESSOAL,
    KIND_RASCUNHOS,
    KIND_SPAM,
    KIND_TODOS,
    PLATAFORMA_SITE,
    REMETENTE_NOSSO,
    SEM_LOJA_MARCA_AMBIGUA,
)

logger = structlog.get_logger()

# A versão do cálculo (pasta, segurança, loja provável). Mudar o cálculo =
# subir este número: o job refaz tudo, aos poucos.
INDICE_VERSAO = 1
# E-mails decifrados por lote (um commit por lote no job; ~0,7 s de CPU).
LOTE = 200
# A rota indexa na hora no máximo isto (~0,3 s de CPU; o mais novo primeiro);
# o resto, o job.
ROTA_MAXIMO = 100
# A rota não espera trava de linha para gravar o índice: passou disto, fica
# para o job (a tela mostra "organizando").
ROTA_LOCK_TIMEOUT = "1s"
# Na rota, o descanso depois de cada e-mail é metade do tempo gasto nele
# (as outras chamadas da API andam no meio; ~0,5 s para os 100).
ROTA_PAUSA = 0.5
# Uma volta do job: no máximo isto de e-mails ou de segundos (todas as caixas).
# O cron começa no segundo 30 e a volta termina antes do segundo 0 seguinte,
# quando a ponte e os outros crons do worker começam.
JOB_MAXIMO = 3000
JOB_SEGUNDOS = 25.0
# Depois de cada e-mail o job descansa `JOB_PAUSA` × o tempo que gastou nele
# (1,0 = no máximo metade do event loop do worker fica com o índice).
JOB_PAUSA = 1.0
# A trava da caixa ocupada (a rota indexando agora): o job tenta de novo na
# próxima passada, até isto de vezes por volta, esperando `TRAVA_ESPERA` se
# todas as caixas com fila estiverem ocupadas.
TRAVA_TENTATIVAS = 10
TRAVA_ESPERA = 0.5
# A regra de segurança custa ~1,2 ms por KB de texto e o schema da Central
# aceita até 2 MB: ela lê no máximo isto (o assunto vai sempre inteiro; e-mail
# de código tem 1–3 KB de texto). O HTML cru é cortado antes de virar texto
# (o maior e-mail de produção em 09/10: ~65 KB). Pior caso medido: ~30 ms.
TETO_CRU = 128 * 1024
TETO_TEXTO = 16 * 1024
# O relógio do prazo da volta e o descanso entre um e-mail e outro (o teste
# troca estes dois, sem mexer no `time` nem no `asyncio` do processo).
_relogio = time.monotonic


async def _descansar(segundos: float) -> None:
    await asyncio.sleep(segundos)


# ── O selo ────────────────────────────────────────────────────────────────
SELO_LOJA = "loja"
SELO_SITE = "site"
SELO_SEM_LOJA = "sem_loja"
SELO_PRIVADO = "privado"
SELO_SEGURANCA = "seguranca"
# As chaves de filtro que não são loja.
CHAVES_FIXAS = (SELO_SEM_LOJA, SELO_SEGURANCA, SELO_PRIVADO)
ROTULO_FIXO = {
    SELO_SEM_LOJA: "sem loja",
    SELO_SEGURANCA: "segurança",
    SELO_PRIVADO: "privado",
}
# O que a lista mostra no lugar do assunto de um e-mail de segurança.
ASSUNTO_DE_SEGURANCA = "e-mail de acesso/código"
FONTE_PONTE = "ponte"
FONTE_INDICE = "indice"
# A segurança da PONTE (o passo 4 de `ponte.processar`, com o afrouxo "de
# pessoa") só vale quando ela chegou nesse passo. LISTA DE PERMISSÃO: os
# estados e os motivos de "ignorado" que a ponte só alcança DEPOIS da regra de
# segurança. Qualquer outro — novo, erro, privado, ignorado pela pasta, o
# ignorado POR PESSOA (a fila deixa ignorar um e-mail em ERRO, que pode ter
# falhado antes da segurança — revisão de 09/10) ou um estado que alguém crie
# amanhã — = a ponte não olhou: vale a regra estrita daqui. Na dúvida, esconde.
_ESTADOS_DEPOIS_DA_SEGURANCA = (
    ESTADO_GRAVADO,
    ESTADO_SEM_VINCULO,
    ESTADO_SEM_LOJA,
    ESTADO_INTERNO,
    ESTADO_RESUMO,
    ESTADO_DUPLICADO,
)
_IGNORADOS_DEPOIS_DA_SEGURANCA = (
    IGNORADO_AVISO_DO_TUTA,
    IGNORADO_ALIAS_INTERNO,
    IGNORADO_ENVIADO_SEM_CONVERSA,
    IGNORADO_DEVOLUCAO,
)

# ── As pastas ─────────────────────────────────────────────────────────────
# As de sistema, na ORDEM do Tuta (`folderTypeToOrder`: Entrada, Rascunhos,
# Agendados, Enviados, Lixeira, Arquivo, Spam), com o nome em português; as
# pessoais vêm depois, na ordem alfabética do Tuta (`ordem_das_pastas`).
ORDEM_SISTEMA = (
    KIND_ENTRADA,
    KIND_RASCUNHOS,
    KIND_AGENDADOS,
    KIND_ENVIADOS,
    KIND_LIXEIRA,
    KIND_ARQUIVO,
    KIND_SPAM,
    KIND_TODOS,
)
NOME_SISTEMA = {
    KIND_ENTRADA: "Entrada",
    KIND_RASCUNHOS: "Rascunhos",
    KIND_ENVIADOS: "Enviados",
    KIND_LIXEIRA: "Lixeira",
    KIND_ARQUIVO: "Arquivo",
    KIND_SPAM: "Spam",
    KIND_AGENDADOS: "Agendados",
    KIND_TODOS: "Todos",
}
# O e-mail que não se conseguiu ler (conteúdo estragado): uma "pasta" à parte.
PASTA_ILEGIVEL = "x"
NOME_ILEGIVEL = "Não lidos (conteúdo ilegível)"


@cache
def _segredo() -> bytes:
    """A chave do HMAC da pasta (derivada da chave do servidor; nunca a mesma da cifra)."""
    bruto = get_settings().credentials_key.encode()
    return HKDF(
        algorithm=hashes.SHA256(), length=32, salt=b"davinci-mail-caixa-v1", info=b"pasta"
    ).derive(bruto)


def chave_da_pasta(mailbox_id: UUID, lida: pastas.PastaDoEmail) -> str:
    """A chave da pasta no índice: "s<tipo>" (sistema) ou "p" + HMAC (pessoal). PURA."""
    if lida.tipo_tuta in NOME_SISTEMA:
        return f"s{lida.tipo_tuta}"
    dig = hmac.new(_segredo(), f"{mailbox_id}\n{lida.chave}".encode(), hashlib.sha256)
    return "p" + dig.hexdigest()[:31]


def nome_da_pasta(lida: pastas.PastaDoEmail) -> str:
    """O nome que a tela mostra: o do sistema em português ("INBOX" → Entrada) ou o da pasta."""
    return NOME_SISTEMA.get(lida.tipo_tuta) or lida.nome


def tipo_da_chave(chave: str) -> str:
    if chave.startswith("s"):
        return "sistema"
    if chave == PASTA_ILEGIVEL:
        return "ilegivel"
    return "pessoal"


def _classe_do_caractere(c: str) -> int:
    """A ordem de cada caractere no `localeCompare` do Tuta (ICU): espaço <
    pontuação ("*", "_", "-") < símbolo < número < letra."""
    if c.isspace():
        return 0
    categoria = unicodedata.category(c)
    if categoria.startswith("P"):
        return 1
    if categoria.startswith("S"):
        return 2
    if c.isdigit():
        return 3
    return 4


def ordem_alfabetica(nome: str | None) -> tuple:
    """A ordem das pastas pessoais do Tuta (`compareCustom` = `name.localeCompare`).

    Sem acento e sem maiúscula, mas COM a pontuação: o "*" do dono ("*7buyers",
    "*avisos") vem antes das letras, como no Tuta — é para isso que ele põe o
    "*". O nome inteiro desempata ("avila" × "Ávila"). PURA.
    """
    bruto = nome or ""
    sem_acento = "".join(
        c for c in unicodedata.normalize("NFKD", bruto) if not unicodedata.combining(c)
    ).casefold()
    return (tuple((_classe_do_caractere(c), c) for c in sem_acento), bruto)


def ordem_das_pastas(item: dict) -> tuple:
    """Sistema na ordem do Tuta; depois as pessoais na ordem alfabética do Tuta."""
    kind = item["chave"][1:] if item["tipo"] == "sistema" else None
    if kind in ORDEM_SISTEMA:
        return (0, ORDEM_SISTEMA.index(kind), ((), ""))
    if item["tipo"] == "ilegivel":
        return (2, 0, ((), ""))
    return (1, 0, ordem_alfabetica(str(item.get("nome") or "")))


# ── A base: o que decide o selo de uma caixa ─────────────────────────────


@dataclass
class Base:
    """O que decide o selo provável numa caixa (lido uma vez; a `assinatura` vai na linha)."""

    mailbox_id: UUID
    so_aliases_de_loja: bool
    principal: str
    aliases: set[str]
    cad: rotear.Cadastro
    regras: regras.Regras
    # chave → a pasta com a escolha de PESSOA (plataforma/finalidade à mão).
    pastas_de_pessoa: dict[str, MailFolder]
    assinatura: str


def _assinatura(
    *,
    so_aliases: bool,
    principal: str,
    aliases: set[str],
    cad: rotear.Cadastro,
    r: regras.Regras,
    de_pessoa: dict[str, MailFolder],
) -> str:
    """O hash (16 hex) de tudo que muda o selo. Os NOMES (da loja, da marca) não entram:
    saem na hora de mostrar. Nada daqui fica em claro (só o hash)."""
    partes: dict[str, Any] = {
        "v": INDICE_VERSAO,
        "so": so_aliases,
        "caixa": [principal, sorted(aliases)],
        "regras": [
            sorted(r.plataforma.items()),
            sorted(r.finalidade.items()),
            sorted(r.marca.items()),
        ],
        "lojas": sorted(
            [
                str(lj.store_info_id),
                lj.plataforma,
                lj.endereco,
                list(lj.parcial or ()),
                str(lj.integration_id or ""),
                lj.sem_dominio,
            ]
            for lj in cad.lojas
        ),
        "caixas_site": sorted(
            [e, str(c.marca_id), c.slug, c.tipo or ""] for e, c in cad.caixas.items()
        ),
        "marcas": sorted(
            [slug, str(mid), list(doms)] for slug, (mid, _n, doms) in cad.marcas.items()
        ),
        "pastas": sorted(
            [
                chave,
                p.nome,
                p.tipo_tuta or "",
                p.plataforma_manual or "",
                p.finalidade_manual or "",
            ]
            for chave, p in de_pessoa.items()
        ),
    }
    bruto = json.dumps(partes, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(bruto.encode()).hexdigest()[:16]


async def base_da_caixa(session: AsyncSession, mailbox: MailMailbox) -> Base:
    cfg = await config_caixa.config_da_caixa(session, mailbox.id)
    principal, aliases = ponte.aliases_da_caixa(mailbox)
    cad = await rotear.cadastro(session)
    r = await regras.carregar(session)
    de_pessoa: dict[str, MailFolder] = {}
    for p in (
        await session.scalars(
            select(MailFolder).where(
                MailFolder.mailbox_id == mailbox.id,
                or_(
                    MailFolder.plataforma_manual.is_not(None),
                    MailFolder.finalidade_manual.is_not(None),
                ),
            )
        )
    ).all():
        # Cópia solta (fora da sessão): só leitura, sobrevive aos commits do job.
        de_pessoa[p.chave] = MailFolder(
            chave=p.chave,
            nome=p.nome,
            tipo_tuta=p.tipo_tuta,
            ignorar=bool(p.ignorar),
            plataforma_manual=p.plataforma_manual,
            finalidade_manual=p.finalidade_manual,
        )
    return Base(
        mailbox_id=mailbox.id,
        so_aliases_de_loja=cfg.ponte_so_aliases_de_loja,
        principal=principal,
        aliases=aliases,
        cad=cad,
        regras=r,
        pastas_de_pessoa=de_pessoa,
        assinatura=_assinatura(
            so_aliases=cfg.ponte_so_aliases_de_loja,
            principal=principal,
            aliases=aliases,
            cad=cad,
            r=r,
            de_pessoa=de_pessoa,
        ),
    )


# ── O cálculo de um e-mail (PURO sobre a base) ────────────────────────────


@dataclass(frozen=True)
class Provavel:
    selo: str
    store_info_id: UUID | None = None
    integration_id: UUID | None = None
    marca_id: UUID | None = None
    plataforma: str | None = None


def classe_da_pasta(lida: pastas.PastaDoEmail, base: Base) -> regras.Classe:
    """A plataforma/marca da pasta: a escolha de pessoa (se houver), senão a regra."""
    de_pessoa = base.pastas_de_pessoa.get(lida.chave)
    if de_pessoa is not None:
        return regras.efetiva(de_pessoa, base.regras)
    return regras.classificar(lida.nome, lida.tipo_tuta, base.regras)


def do_roteamento(rota: rotear.Rota) -> Provavel:
    if not rota.tem_loja:
        return Provavel(SELO_SEM_LOJA)
    if rota.site:
        return Provavel(SELO_SITE, marca_id=rota.marca_id, plataforma=PLATAFORMA_SITE)
    return Provavel(
        SELO_LOJA,
        store_info_id=rota.store_info_id,
        integration_id=rota.integration_id,
        plataforma=rota.plataforma,
    )


def texto_para_regras(email: ponte.Email) -> str:
    """O texto legível que a regra de segurança e o protocolo leem: no máximo
    `TETO_TEXTO` (o HTML cru cortado em `TETO_CRU` antes de virar texto). PURA."""
    return texto.legivel(email.texto[:TETO_CRU])[:TETO_TEXTO]


def provavel(
    email: ponte.Email,
    *,
    enviado: bool,
    classe: regras.Classe,
    base: Base,
    legivel: str | None = None,
) -> Provavel:
    """A loja PROVÁVEL, na ordem da ponte (filtro da caixa → remetente → loja). PURA.

    Sem o que precisa do banco (o fio da conversa, o duplicado): é só para
    mostrar. Nunca grava nada. `legivel`: o `texto_para_regras` já calculado.
    """
    cad = base.cad
    if base.so_aliases_de_loja and (
        ponte.alias_de_loja(
            email,
            principal=base.principal,
            aliases=base.aliases,
            e_de_loja=cad.e_de_loja,
            enviado=enviado,
        )
        is None
    ):
        return Provavel(SELO_PRIVADO)
    conhecidos = base.aliases | cad.enderecos_completos() | set(cad.caixas)
    tipo = ponte.remetente_tipo(email.de, conhecidos)
    if enviado or (tipo == REMETENTE_NOSSO and classe.finalidade == FINALIDADE_ENVIADOS):
        return do_roteamento(
            rotear.rotear(
                recebeu=email.recebeu,
                de=email.de,
                enviado_por_nos=True,
                plataforma_pasta=None,
                marca_pasta=None,
                caixa_pasta=None,
                aliases=base.aliases,
                cad=cad,
            )
        )
    rota = rotear.rotear(
        recebeu=email.recebeu,
        de=email.de,
        enviado_por_nos=False,
        plataforma_pasta=classe.plataforma,
        marca_pasta=classe.marca,
        caixa_pasta=classe.tipo_caixa,
        aliases=base.aliases,
        cad=cad,
    )
    if rota.motivo_sem_loja == SEM_LOJA_MARCA_AMBIGUA:
        # O mesmo domínio em duas marcas: a letra do protocolo decide (como a ponte).
        corpo = texto_para_regras(email) if legivel is None else legivel
        prot = pedido.protocolo(email.assunto, corpo)
        if prot is not None:
            rota = rotear.marca_pelo_protocolo(rota, pedido.MARCA_DA_LETRA.get(prot.letra, ()), cad)
    return do_roteamento(rota)


def de_seguranca(email: ponte.Email, legivel: str | None = None) -> bool:
    """A regra ESTRITA de segurança (código, senha, acesso) no assunto e no texto
    (o texto até `TETO_TEXTO`: `texto_para_regras`)."""
    corpo = texto_para_regras(email) if legivel is None else legivel
    return codigos.e_de_seguranca(email.assunto, corpo)


def calcular(message: MailMessage, base: Base) -> dict[str, Any]:
    """A linha do índice de um e-mail (decifra só em memória)."""
    linha: dict[str, Any] = {
        "message_id": message.id,
        "mailbox_id": message.mailbox_id,
        "recebido_em": message.received_at,
        "fonte_em": message.updated_at,
        "base": base.assinatura,
        "regras_versao": INDICE_VERSAO,
        "store_info_id": None,
        "integration_id": None,
        "marca_id": None,
        "plataforma": None,
    }
    try:
        email = ponte.decifrar(message)
        lida = pastas.do_conteudo(email.conteudo)
        classe = classe_da_pasta(lida, base)
        legivel = texto_para_regras(email)
        prov = provavel(
            email,
            enviado=message.direction == "sent",
            classe=classe,
            base=base,
            legivel=legivel,
        )
        seguro = de_seguranca(email, legivel)
    except Exception as e:  # noqa: BLE001 — um e-mail estragado não para o índice
        # Fecha para o lado seguro: pasta à parte, sem loja, assunto escondido.
        logger.warning(
            "mail_caixa_indice_ilegivel", message_id=str(message.id), err=type(e).__name__
        )
        linha.update(
            pasta_chave=PASTA_ILEGIVEL,
            pasta_tipo=KIND_PESSOAL,
            seguranca=True,
            selo=SELO_SEM_LOJA,
        )
        return linha
    linha.update(
        pasta_chave=chave_da_pasta(message.mailbox_id, lida),
        pasta_tipo=lida.tipo_tuta[:8] or KIND_PESSOAL,
        seguranca=seguro,
        selo=prov.selo,
        store_info_id=prov.store_info_id,
        integration_id=prov.integration_id,
        marca_id=prov.marca_id,
        plataforma=prov.plataforma[:16] if prov.plataforma else None,
    )
    return linha


# ── Preencher ─────────────────────────────────────────────────────────────


def _faltando(base: Base, *, so_urgente: bool = False):
    """Os e-mails da caixa a (re)fazer no índice.

    `so_urgente`: só o que a tela mostraria ERRADO — o e-mail sem linha (some
    da lista) e o que mudou (a pasta pode ter mudado). Sem ele, também a linha
    de base ou versão velha (o provável de antes: o job refaz aos poucos).
    A caixa vai também no ON do LEFT JOIN: o índice é lido pelo índice
    `(mailbox_id, …)`, sem varrer as linhas das outras caixas.
    """
    i = MailCaixaIndice
    condicoes = [i.message_id.is_(None), i.fonte_em != MailMessage.updated_at]
    if not so_urgente:
        condicoes += [i.base != base.assinatura, i.regras_versao != INDICE_VERSAO]
    return (
        select(MailMessage.id)
        .outerjoin(i, and_(i.message_id == MailMessage.id, i.mailbox_id == base.mailbox_id))
        .where(MailMessage.mailbox_id == base.mailbox_id, or_(*condicoes))
    )


async def faltam(session: AsyncSession, base: Base) -> int:
    """Quantos e-mails a lista ainda não mostra certo (sem linha ou mudados)."""
    consulta = _faltando(base, so_urgente=True).subquery()
    return int(await session.scalar(select(func.count()).select_from(consulta)) or 0)


def _trava(mailbox_id: UUID) -> int:
    """A chave (bigint) do advisory lock da caixa: uma volta de cada vez por caixa."""
    dig = hashlib.sha256(b"mail_caixa_indice:" + mailbox_id.bytes).digest()
    return int.from_bytes(dig[:8], "big", signed=True)


async def pegar_trava(session: AsyncSession, mailbox_id: UUID) -> bool:
    """Trava da caixa até o fim da TRANSAÇÃO (o job e a rota não decifram o mesmo lote juntos)."""
    return bool(await session.scalar(select(func.pg_try_advisory_xact_lock(_trava(mailbox_id)))))


_CAMPOS = (
    "mailbox_id",
    "recebido_em",
    "pasta_chave",
    "pasta_tipo",
    "seguranca",
    "selo",
    "store_info_id",
    "integration_id",
    "marca_id",
    "plataforma",
    "fonte_em",
    "base",
    "regras_versao",
)


async def indexar(
    session: AsyncSession,
    base: Base,
    *,
    maximo: int | None = None,
    so_urgente: bool = False,
    pausa: float = 0.0,
    prazo: float | None = None,
) -> int:
    """Indexa até `maximo` (padrão: um `LOTE`) e-mails da caixa, o mais novo primeiro.

    Não commita: devolve quantos indexou; quem chama trava a caixa
    (`pegar_trava`) e commita. `so_urgente`: ver `_faltando`. Depois de cada
    e-mail, a vez volta ao event loop por `pausa` × o tempo gasto nele (0 = só
    devolve a vez). `prazo` (no `_relogio`): conferido a cada e-mail —
    passou, grava o que já calculou e para.
    """
    maximo = LOTE if maximo is None else maximo
    feitos = 0
    while feitos < maximo:
        lote = min(LOTE, maximo - feitos)
        ids = list(
            (
                await session.scalars(
                    _faltando(base, so_urgente=so_urgente)
                    .order_by(MailMessage.received_at.desc(), MailMessage.id.desc())
                    .limit(lote)
                )
            ).all()
        )
        if not ids:
            break
        mensagens = (
            await session.scalars(
                select(MailMessage)
                .where(MailMessage.id.in_(ids))
                .order_by(MailMessage.received_at.desc(), MailMessage.id.desc())
            )
        ).all()
        linhas = []
        parou = False
        for m in mensagens:
            inicio = time.perf_counter()
            linhas.append(calcular(m, base))
            # ~3 ms de CPU por e-mail: a vez volta ao event loop entre um e
            # outro — e, no job, descansa o mesmo tanto (`pausa`).
            await _descansar((time.perf_counter() - inicio) * pausa if pausa > 0 else 0)
            if prazo is not None and _relogio() >= prazo:
                parou = True
                break
        if linhas:
            stmt = pg_insert(MailCaixaIndice).values(linhas)
            await session.execute(
                stmt.on_conflict_do_update(
                    index_elements=["message_id"],
                    set_={
                        **{c: getattr(stmt.excluded, c) for c in _CAMPOS},
                        "indexado_em": func.now(),
                    },
                )
            )
        if parou:
            feitos += len(linhas)
            break
        feitos += len(ids)
        if len(ids) < lote:
            break
    return feitos


async def preparar(
    session: AsyncSession, mailbox: MailMailbox, *, maximo: int | None = None
) -> tuple[Base, int]:
    """Para a ROTA: indexa o URGENTE (sem linha ou mudado; até `maximo`, padrão
    `ROTA_MAXIMO`, se ninguém está indexando a caixa agora) e devolve (a base,
    quantos a lista ainda não mostra certo). Não commita.

    Conta primeiro: sem nada faltando (o normal), não trava nem procura de novo.
    A gravação não espera trava de linha mais que `ROTA_LOCK_TIMEOUT`: fica
    para o job.
    """
    maximo = ROTA_MAXIMO if maximo is None else maximo
    base = await base_da_caixa(session, mailbox)
    n = await faltam(session, base)
    if n and maximo > 0 and await pegar_trava(session, mailbox.id):
        await session.execute(text(f"SET LOCAL lock_timeout = '{ROTA_LOCK_TIMEOUT}'"))
        try:
            async with session.begin_nested():
                await indexar(session, base, maximo=maximo, so_urgente=True, pausa=ROTA_PAUSA)
        except DBAPIError as e:
            # O e-mail sumiu no meio (caixa apagada) ou uma trava demorou: a
            # lista sai assim mesmo; o job refaz na próxima volta.
            logger.warning(
                "mail_caixa_indice_rota_adiada",
                mailbox_id=str(mailbox.id),
                err=type(e).__name__,
                codigo=getattr(e.orig, "sqlstate", None) or getattr(e.orig, "pgcode", None),
            )
        n = await faltam(session, base)
    return base, n


async def rodar(
    session: AsyncSession,
    *,
    maximo: int | None = None,
    segundos: float | None = None,
    pausa: float | None = None,
) -> dict[str, int]:
    """Uma volta do job: todas as caixas, UM LOTE DE CADA por vez (commit por
    lote), até o teto (`JOB_MAXIMO` e-mails ou `JOB_SEGUNDOS`, conferido a cada
    e-mail). Caixa com a trava ocupada (a rota indexando agora): tenta de novo
    na próxima passada (até `TRAVA_TENTATIVAS`), sem largar a fila."""
    maximo = JOB_MAXIMO if maximo is None else maximo
    segundos = JOB_SEGUNDOS if segundos is None else segundos
    pausa = JOB_PAUSA if pausa is None else pausa
    prazo = _relogio() + segundos
    feitos = 0
    bases: dict[UUID, Base] = {}
    for mailbox_id in (
        await session.scalars(select(MailMailbox.id).order_by(MailMailbox.id))
    ).all():
        mailbox = await session.get(MailMailbox, mailbox_id)
        if mailbox is not None:
            bases[mailbox_id] = await base_da_caixa(session, mailbox)
    await session.commit()
    ativas = list(bases)
    ocupadas: dict[UUID, int] = {}
    tocadas: set[UUID] = set()
    while ativas and feitos < maximo and _relogio() < prazo:
        andou = False
        for mailbox_id in list(ativas):
            if feitos >= maximo or _relogio() >= prazo:
                break
            if not await pegar_trava(session, mailbox_id):
                await session.commit()
                ocupadas[mailbox_id] = ocupadas.get(mailbox_id, 0) + 1
                if ocupadas[mailbox_id] >= TRAVA_TENTATIVAS:
                    ativas.remove(mailbox_id)
                continue
            pedido_agora = min(LOTE, maximo - feitos)
            n = await indexar(
                session, bases[mailbox_id], maximo=pedido_agora, pausa=pausa, prazo=prazo
            )
            await session.commit()
            feitos += n
            andou = andou or n > 0
            if n:
                tocadas.add(mailbox_id)
            if n < pedido_agora:
                ativas.remove(mailbox_id)
        if ativas and not andou:
            # Todas as caixas com fila estavam ocupadas: espera um pouco.
            await _descansar(TRAVA_ESPERA)
    if feitos:
        logger.info("mail_caixa_indice_volta", indexados=feitos, caixas=len(tocadas))
    return {"indexados": feitos, "caixas": len(tocadas)}


# ── O selo efetivo (a ponte ou o provável), em SQL ────────────────────────


def _uuid_texto(coluna) -> Any:
    return cast(coluna, String)


def colunas_do_selo(i=MailCaixaIndice, m=MailMessageMeta) -> dict[str, Any]:
    """As expressões do selo EFETIVO de cada linha (com `m` = a meta, LEFT JOIN).

      • `selo`  — seguranca | privado | sem_loja | loja | site;
      • `chave` — a chave do FILTRO: "f:<ficha>" | "i:<integração>" |
                  "m:<marca>" | sem_loja | seguranca | privado;
      • `fonte` — ponte (a meta decidiu) | indice (o provável daqui).

    A ponte decide quando o e-mail passou por ela (estado, loja). A segurança:
    a da ponte; e a estrita daqui sempre que a ponte não chegou ao passo da
    segurança (sem meta, novo, erro, privado, ignorado pela pasta ou por
    pessoa…): `_ESTADOS_DEPOIS_DA_SEGURANCA` é uma lista de PERMISSÃO.
    `avaliada` nunca é NULL (senão o CASE leria "não é segurança").
    """
    tem_meta = m.message_id.is_not(None)
    avaliada = and_(
        tem_meta,
        or_(
            m.estado.in_(_ESTADOS_DEPOIS_DA_SEGURANCA),
            and_(
                m.estado == ESTADO_IGNORADO,
                m.motivo.is_not(None),
                m.motivo.in_(_IGNORADOS_DEPOIS_DA_SEGURANCA),
            ),
        ),
    )
    seguranca = or_(m.estado == ESTADO_SEGURANCA, and_(i.seguranca.is_(True), not_(avaliada)))
    meta_tem_loja = or_(
        m.store_info_id.is_not(None), m.integration_id.is_not(None), m.marca_id.is_not(None)
    )
    so_marca = and_(m.store_info_id.is_(None), m.integration_id.is_(None), m.marca_id.is_not(None))
    indice_tem_loja = or_(
        i.store_info_id.is_not(None), i.integration_id.is_not(None), i.marca_id.is_not(None)
    )
    selo = case(
        (seguranca, literal(SELO_SEGURANCA)),
        (m.estado == ESTADO_PRIVADO, literal(SELO_PRIVADO)),
        (m.estado == ESTADO_SEM_LOJA, literal(SELO_SEM_LOJA)),
        (so_marca, literal(SELO_SITE)),
        (meta_tem_loja, literal(SELO_LOJA)),
        (and_(i.selo.in_((SELO_LOJA, SELO_SITE)), not_(indice_tem_loja)), literal(SELO_SEM_LOJA)),
        else_=i.selo,
    )
    chave = case(
        (seguranca, literal(SELO_SEGURANCA)),
        (m.estado == ESTADO_PRIVADO, literal(SELO_PRIVADO)),
        (m.estado == ESTADO_SEM_LOJA, literal(SELO_SEM_LOJA)),
        (m.store_info_id.is_not(None), literal("f:") + _uuid_texto(m.store_info_id)),
        (m.integration_id.is_not(None), literal("i:") + _uuid_texto(m.integration_id)),
        (m.marca_id.is_not(None), literal("m:") + _uuid_texto(m.marca_id)),
        (i.selo == SELO_PRIVADO, literal(SELO_PRIVADO)),
        (i.store_info_id.is_not(None), literal("f:") + _uuid_texto(i.store_info_id)),
        (i.integration_id.is_not(None), literal("i:") + _uuid_texto(i.integration_id)),
        (i.marca_id.is_not(None), literal("m:") + _uuid_texto(i.marca_id)),
        else_=literal(SELO_SEM_LOJA),
    )
    fonte = case(
        (m.estado == ESTADO_SEGURANCA, literal(FONTE_PONTE)),
        (seguranca, literal(FONTE_INDICE)),
        (m.estado.in_((ESTADO_PRIVADO, ESTADO_SEM_LOJA)), literal(FONTE_PONTE)),
        (meta_tem_loja, literal(FONTE_PONTE)),
        else_=literal(FONTE_INDICE),
    )
    return {"selo": selo, "chave": chave, "fonte": fonte}


def linhas_da_caixa(mailbox_id: UUID):
    """O SELECT base (índice ⟕ meta) de uma caixa, com o selo efetivo — como subquery."""
    i = MailCaixaIndice
    m = MailMessageMeta
    col = colunas_do_selo(i, m)
    return (
        select(
            i.message_id,
            i.recebido_em,
            i.pasta_chave,
            i.pasta_tipo,
            col["selo"].label("selo"),
            col["chave"].label("chave"),
            col["fonte"].label("fonte"),
            m.estado.label("estado"),
        )
        .select_from(i)
        .outerjoin(m, m.message_id == i.message_id)
        .where(i.mailbox_id == mailbox_id)
        .subquery("caixa")
    )


# ── A chave da loja: uma loja, uma entrada no filtro ──────────────────────


def canonica(chave: str, cad: rotear.Cadastro) -> str:
    """ "i:<integração>" de uma ficha só do cadastro → "f:<ficha>" (a mesma loja, uma entrada)."""
    if not chave.startswith("i:"):
        return chave
    fichas = [lj for lj in cad.lojas if f"i:{lj.integration_id}" == chave]
    return f"f:{fichas[0].store_info_id}" if len(fichas) == 1 else chave


def equivalentes(chave: str, cad: rotear.Cadastro) -> list[str]:
    """As chaves cruas que caem nesta entrada do filtro (a ficha e a integração dela)."""
    alvo = canonica(chave, cad)
    saida = {alvo}
    if alvo.startswith("f:"):
        for lj in cad.lojas:
            if f"f:{lj.store_info_id}" == alvo and lj.integration_id is not None:
                integ = f"i:{lj.integration_id}"
                if canonica(integ, cad) == alvo:
                    saida.add(integ)
    return sorted(saida)
