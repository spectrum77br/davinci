"""A regra das pastas: pasta → plataforma, finalidade, marca e se o corpo entra (RF5).

O spec (RF5): "só as pastas do Tuta cujo nome tem o nome de uma plataforma. A
plataforma é a palavra no fim do nome da pasta (sem diferenciar
maiúsculas/acentos)". A tabela de palavras é CONFIGURÁVEL
(`atendimento_regras_pasta_email`): nova plataforma = nova linha, sem mexer
no código. Três tipos de palavra:

  plataforma — a ÚLTIMA palavra do NOME ("vendas ml" → ml, "reclamação ml" →
               ml); casa INTEIRA ("ali" não casa dentro de "magalu");
  finalidade — a PRIMEIRA palavra ("vendas" → histórico; "mensagens",
               "problema", "reclamação" → pendente);
  marca      — qualquer palavra, para as caixas dos sites (RF6: "*uranyx sac",
               "*charlots") quando a pasta não tem palavra de plataforma; a
               caixa (sac | atacado | duvidas) sai de outra palavra do nome.

A pasta que começa com uma palavra de `PASTAS_SO_CONTAR` (financeiro,
contabilidade, devoluções, envio, retido, avisos…) só se CONTA, mesmo com
plataforma no nome (decisão padrão de 08/10, até o dono liberar).

Pasta do sistema: a Entrada e os Enviados entram (resposta do cliente,
formulário, e-mail direto; a resposta dada no Tuta); Lixeira, Spam,
Rascunhos, Arquivo e Agendados só são CONTADOS. Pasta pessoal sem palavra de
plataforma nem de marca: só contada (aviso "escolher plataforma ou ignorar").
Uma PESSOA pode escolher a plataforma (ou "ignorar") de qualquer pasta — vale
mais que a regra. Veio do wt-tuta (`tuta/regras.py`), agora por caixa.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.mail_atendimento import AtendimentoRegraPastaEmail, MailFolder
from app.services.mail_atendimento.constantes import (
    FINALIDADE_ENTRADA,
    FINALIDADE_ENVIADOS,
    KIND_ENTRADA,
    KIND_ENVIADOS,
    KIND_IMPORTADOS,
    KIND_MARCADOR,
    KIND_PELO_NOME,
    KIND_PESSOAL,
    KINDS_SISTEMA,
    PASTAS_SO_CONTAR,
    PLATAFORMA_SITE,
    PLATAFORMAS_PASTA,
)

TIPO_PLATAFORMA = "plataforma"
TIPO_FINALIDADE = "finalidade"
TIPO_MARCA = "marca"
TIPOS = (TIPO_PLATAFORMA, TIPO_FINALIDADE, TIPO_MARCA)

# `mail_folders.ler`
LER_CORPO = "corpo"
LER_SO_CONTAR = "so_contar"
LER_NAO = "nao"

# A semente da migration 0387 — vale com a tabela VAZIA (ou fora do ar).
SEMENTE: tuple[tuple[str, str, str], ...] = (
    (TIPO_PLATAFORMA, "ml", "ml"),
    (TIPO_PLATAFORMA, "mercadolivre", "ml"),
    (TIPO_PLATAFORMA, "shopee", "shopee"),
    (TIPO_PLATAFORMA, "amazon", "amazon"),
    (TIPO_PLATAFORMA, "tiktok", "tiktok"),
    (TIPO_PLATAFORMA, "temu", "temu"),
    (TIPO_PLATAFORMA, "magalu", "magalu"),
    (TIPO_PLATAFORMA, "ali", "aliexpress"),
    (TIPO_PLATAFORMA, "aliexpress", "aliexpress"),
    (TIPO_PLATAFORMA, "shein", "shein"),
    (TIPO_FINALIDADE, "vendas", "vendas"),
    (TIPO_FINALIDADE, "venda", "vendas"),
    (TIPO_FINALIDADE, "mensagens", "mensagens"),
    (TIPO_FINALIDADE, "mensagem", "mensagens"),
    (TIPO_FINALIDADE, "problema", "problema"),
    (TIPO_FINALIDADE, "problemas", "problema"),
    (TIPO_FINALIDADE, "reclamacao", "reclamacao"),
    (TIPO_FINALIDADE, "reclamacoes", "reclamacao"),
    (TIPO_MARCA, "uranyx", "uranyx"),
    (TIPO_MARCA, "charlots", "charlots-park"),
    (TIPO_MARCA, "7buyers", "7buyers"),
    (TIPO_MARCA, "poofy", "poofy"),
    (TIPO_MARCA, "makisa", "makisa"),
    (TIPO_MARCA, "locagil", "locagil"),
)
FINALIDADES_VALIDAS = ("vendas", "mensagens", "problema", "reclamacao")
# A caixa do site pela palavra do nome da pasta ("*uranyx sac").
CAIXAS_DO_SITE = {
    "sac": "sac",
    "atacado": "atacado",
    "duvidas": "duvidas",
    "duvida": "duvidas",
}

_PALAVRA = re.compile(r"[a-z0-9]+")
_CHAVE_CACHE = "mail_atendimento_regras"


def palavras(nome: str | None) -> list[str]:
    """O nome em palavras, sem acento e minúsculo ("*Reclamação ML" → [reclamacao, ml])."""
    base = unicodedata.normalize("NFKD", nome or "")
    sem_acento = "".join(ch for ch in base if not unicodedata.combining(ch))
    return _PALAVRA.findall(sem_acento.lower())


def normalizar_palavra(palavra: str | None) -> str:
    """Uma palavra da regra como ela é comparada ('Reclamação' → 'reclamacao')."""
    achadas = palavras(palavra)
    return achadas[0] if len(achadas) == 1 else ""


@dataclass(frozen=True)
class Regras:
    plataforma: dict[str, str] = field(default_factory=dict)
    finalidade: dict[str, str] = field(default_factory=dict)
    marca: dict[str, str] = field(default_factory=dict)


def regras_padrao() -> Regras:
    return _montar((t, p, v) for t, p, v in SEMENTE)


def _montar(linhas) -> Regras:
    mapa: dict[str, dict[str, str]] = {t: {} for t in TIPOS}
    for tipo, palavra, valor in linhas:
        chave = normalizar_palavra(palavra)
        if tipo in mapa and chave and valor:
            mapa[tipo][chave] = str(valor).strip().lower()
    return Regras(
        plataforma=mapa[TIPO_PLATAFORMA],
        finalidade=mapa[TIPO_FINALIDADE],
        marca=mapa[TIPO_MARCA],
    )


async def carregar(session: AsyncSession) -> Regras:
    """As regras ativas da tabela (cache por sessão); tabela vazia = a semente."""
    cache = session.info.get(_CHAVE_CACHE)
    if isinstance(cache, Regras):
        return cache
    linhas = (
        await session.execute(
            select(
                AtendimentoRegraPastaEmail.tipo,
                AtendimentoRegraPastaEmail.palavra,
                AtendimentoRegraPastaEmail.valor,
                AtendimentoRegraPastaEmail.ativa,
            )
        )
    ).all()
    regras = _montar((t, p, v) for t, p, v, ativa in linhas if ativa) if linhas else regras_padrao()
    session.info[_CHAVE_CACHE] = regras
    return regras


def esquecer(session: AsyncSession) -> None:
    session.info.pop(_CHAVE_CACHE, None)


@dataclass(frozen=True)
class Classe:
    """O que a pasta é: plataforma, finalidade, marca/caixa (site) e se o corpo entra."""

    plataforma: str | None = None
    finalidade: str | None = None
    marca: str | None = None
    tipo_caixa: str | None = None
    ler: bool = False
    # sistema | pessoal | marcador
    tipo: str = "pessoal"


def tipo_da_pasta(tipo_tuta: str | None) -> str:
    kind = str(tipo_tuta or KIND_PESSOAL)
    if kind == KIND_MARCADOR:
        return "marcador"
    if kind in KINDS_SISTEMA:
        return "sistema"
    return "pessoal"


def nome_da_pasta(caminho: str | None) -> str:
    """A última parte do caminho ("INBOX/vendas ml" → "vendas ml")."""
    partes = [p.strip() for p in (caminho or "").replace("\\", "/").split("/") if p.strip()]
    return " ".join((partes[-1] if partes else "").split())[:200]


def tipo_pelo_nome(nome: str | None) -> str:
    """O tipo do Tuta de uma pasta que veio só com o NOME (agente v1, IMAP).

    "INBOX" → Entrada; "Sent" → Enviados; "Trash"/"Lixeira" → Lixeira… O resto
    é pasta pessoal. O nome do Tuta já em português também casa.
    """
    chave = " ".join(palavras(nome))
    return KIND_PELO_NOME.get(chave, KIND_PESSOAL)


def classificar(nome: str | None, tipo_tuta: str | None, regras: Regras) -> Classe:
    """A pasta pela REGRA (sem a escolha de pessoa). PURA."""
    kind = str(tipo_tuta or KIND_PESSOAL)
    tipo = tipo_da_pasta(kind)
    if kind == KIND_ENTRADA:
        return Classe(finalidade=FINALIDADE_ENTRADA, ler=True, tipo=tipo)
    if kind == KIND_ENVIADOS:
        return Classe(finalidade=FINALIDADE_ENVIADOS, ler=True, tipo=tipo)
    if tipo == "sistema" or kind == KIND_MARCADOR:
        # Lixeira, Spam, Rascunhos, Arquivo, Agendados e marcadores: só contados.
        return Classe(tipo=tipo)
    if kind not in (KIND_PESSOAL, KIND_IMPORTADOS):
        return Classe(tipo=tipo)
    ps = palavras(nome)
    if not ps:
        return Classe(tipo=tipo)
    finalidade = regras.finalidade.get(ps[0])
    plataforma = regras.plataforma.get(ps[-1])
    if ps[0] in PASTAS_SO_CONTAR:
        # "devoluções ml", "financeiro": só contar (decisão padrão).
        return Classe(plataforma=plataforma, finalidade=finalidade, tipo=tipo)
    if plataforma in PLATAFORMAS_PASTA and plataforma != PLATAFORMA_SITE:
        return Classe(plataforma=plataforma, finalidade=finalidade, ler=True, tipo=tipo)
    marca = next((regras.marca[p] for p in ps if p in regras.marca), None)
    if marca:
        caixa = next((CAIXAS_DO_SITE[p] for p in ps if p in CAIXAS_DO_SITE), None)
        return Classe(
            plataforma=PLATAFORMA_SITE,
            finalidade=finalidade,
            marca=marca,
            tipo_caixa=caixa,
            ler=True,
            tipo=tipo,
        )
    return Classe(finalidade=finalidade, tipo=tipo)


def efetiva(pasta: MailFolder, regras: Regras) -> Classe:
    """A pasta como vale: a escolha de pessoa (plataforma, finalidade, ignorar) ganha da regra."""
    pela_regra = classificar(pasta.nome, pasta.tipo_tuta, regras)
    if pasta.ignorar:
        return Classe(
            plataforma=pela_regra.plataforma,
            finalidade=pela_regra.finalidade,
            marca=pela_regra.marca,
            tipo_caixa=pela_regra.tipo_caixa,
            ler=False,
            tipo=pela_regra.tipo,
        )
    plataforma = pasta.plataforma_manual or pela_regra.plataforma
    finalidade = pasta.finalidade_manual or pela_regra.finalidade
    if not pasta.plataforma_manual:
        return Classe(
            plataforma=plataforma,
            finalidade=finalidade,
            marca=pela_regra.marca,
            tipo_caixa=pela_regra.tipo_caixa,
            ler=pela_regra.ler,
            tipo=pela_regra.tipo,
        )
    marca = pela_regra.marca if plataforma == PLATAFORMA_SITE else None
    return Classe(
        plataforma=plataforma,
        finalidade=finalidade,
        marca=marca,
        tipo_caixa=pela_regra.tipo_caixa if marca else None,
        ler=plataforma in PLATAFORMAS_PASTA,
        tipo=pela_regra.tipo,
    )


def ler_da_pasta(pasta: MailFolder, classe: Classe) -> str:
    """O `ler` gravado: `nao` (pessoa mandou ignorar), `corpo` ou `so_contar`."""
    if pasta.ignorar:
        return LER_NAO
    return LER_CORPO if classe.ler else LER_SO_CONTAR


def reclassificar(pasta: MailFolder, regras: Regras) -> Classe:
    """Grava na pasta o que a regra diz e o `ler` de agora; devolve a classe que vale."""
    pela_regra = classificar(pasta.nome, pasta.tipo_tuta, regras)
    classe = efetiva(pasta, regras)
    pasta.plataforma = pela_regra.plataforma
    pasta.finalidade = pela_regra.finalidade
    pasta.ler = ler_da_pasta(pasta, classe)
    # Pasta do sistema e a que a regra já reconhece não pedem revisão.
    if tipo_da_pasta(pasta.tipo_tuta) == "sistema" or classe.ler:
        pasta.revisada = True
    return classe
