"""O manual da IA do atendimento: assuntos, regras sem conflito e o manual base.

Parte 2 (P7, 28/09/2026): "manual da IA sem regra batendo com regra". Três
peças moram aqui, num lugar só, lidas pela IA, pelo router e pelo script
`scripts/atendimento_manual.py`:

  ASSUNTOS (`atendimento_categorias`) — a lista oficial com que a IA
  classifica a mensagem, pela DESCRIÇÃO de cada um. Tabela vazia = a lista
  de `constantes.CATEGORIAS` (com `constantes.CATEGORIAS_INFO`), que é o que
  vale até o manual base entrar. `so_humano` do banco ACRESCENTA a
  `constantes.CATEGORIAS_SO_HUMANO`, nunca tira: troca, cancelamento,
  reembolso... são direito do consumidor (CDC) e dinheiro, e liberar um
  deles para a IA é mudança de CÓDIGO revisada, não uma linha num JSON.

  CONFLITO — duas regras ATIVAS do tipo `categoria` para o mesmo (assunto,
  plataforma, canal). As duas entram no mesmo prompt, e a IA escolhe qual
  obedecer — o manual deixa de mandar. A API recusa a segunda (409
  `regra_conflitante`) e o importador recusa o arquivo. Regra GERAL (sem
  assunto) não conflita: o manual tem muitas, e cada uma fala de uma coisa.
  Plataforma/canal casam EXATOS (NULL com NULL): a regra da Shopee que
  detalha a geral é especialização, não briga.

  MANUAL BASE — `importar_manual`/`exportar_manual`, no formato
  `{"categorias": [...], "regras": [...], "respostas_prontas": [...]}`.
  Valida tudo antes de gravar (formato, assunto que existe, plataforma e
  canal que existem, conflito dentro do arquivo e com o banco) e só grava
  se nada falhou. Reimportar o mesmo arquivo não duplica: assunto casa
  pelo id, regra por (tipo, assunto, plataforma, canal, QUANDO), resposta
  pronta por (título, plataforma, canal).

Quem grava regra pega a trava `TRAVA_REGRAS` (advisory lock da transação)
antes de conferir o conflito: duas abas salvando, ou a tela e o importador
ao mesmo tempo, passariam as duas pela conferência e nasceriam as duas
regras que batem.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AtendimentoCategoria, AtendimentoModelo, AtendimentoRegra
from app.services.atendimento import validador
from app.services.atendimento.constantes import (
    CANAIS_POR_PLATAFORMA,
    CATEGORIAS,
    CATEGORIAS_INFO,
    CATEGORIAS_SO_HUMANO,
    LACUNAS,
    ORIGEM_HUMANO,
    PLATAFORMAS,
    PRIORIDADE_REGRA_PADRAO,
    TIPO_REGRA_CATEGORIA,
    TIPOS_REGRA,
)

logger = structlog.get_logger()

# A MESMA chave que o router pega ao criar/editar regra (routers/atendimento.py).
TRAVA_REGRAS = "atendimento:regras"

# Mesmo formato dos ids de `constantes.CATEGORIAS` e da coluna String(32).
_FORMATO_ID = re.compile(r"^[a-z0-9_]{1,32}$")
# Os mesmos tetos da tela (schemas/atendimento.py): o que o importador grava
# a tela consegue editar depois.
MAX_QUANDO = 2000
MAX_FACA = 4000
MAX_TITULO = 200
MAX_TEXTO_RESPOSTA = 4000
MAX_PRIORIDADE = 10_000
MAX_EXEMPLOS_CATEGORIA = 30

_CHAVES_ARQUIVO = ("categorias", "regras", "respostas_prontas")
_CHAVES_CATEGORIA = ("id", "nome", "descricao", "exemplos", "so_humano", "lacunas", "ativa")
_CHAVES_REGRA = ("tipo", "categoria", "plataforma", "canal", "prioridade", "quando", "faca")
_CHAVES_RESPOSTA = ("titulo", "texto", "plataforma", "canal", "categoria")


async def travar_regras(session: AsyncSession) -> None:
    """Trava da TRANSAÇÃO de quem grava regra (solta no commit/rollback)."""
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": TRAVA_REGRAS}
    )


# ── Assuntos ──────────────────────────────────────────────────────────────


def categorias_padrao() -> list[dict]:
    """A lista das constantes, no formato de `categorias_ativas` (tabela vazia)."""
    return [
        {
            "id": cid,
            "nome": CATEGORIAS_INFO.get(cid, (cid, ""))[0],
            "descricao": CATEGORIAS_INFO.get(cid, (cid, ""))[1],
            "exemplos": [],
            "so_humano": cid in CATEGORIAS_SO_HUMANO,
            "lacunas": [],
            "ordem": (i + 1) * 10,
        }
        for i, cid in enumerate(CATEGORIAS)
    ]


def _lista_de_textos(valor: Any) -> list[str]:
    if not isinstance(valor, list):
        return []
    return [str(v).strip() for v in valor if isinstance(v, str) and v.strip()]


def _categoria_dict(c: AtendimentoCategoria) -> dict:
    return {
        "id": c.id,
        "nome": c.nome,
        "descricao": c.descricao or "",
        "exemplos": _lista_de_textos(c.exemplos),
        # O banco acrescenta, nunca tira (ver o topo do módulo).
        "so_humano": bool(c.so_humano) or c.id in CATEGORIAS_SO_HUMANO,
        "lacunas": [x for x in _lista_de_textos(c.lacunas) if x in LACUNAS],
        "ordem": c.ordem,
    }


async def categorias_ativas(session: AsyncSession) -> list[dict]:
    """Os assuntos com que a IA classifica: os ATIVOS do banco, ou os das constantes.

    Cada um: `{"id", "nome", "descricao", "exemplos", "so_humano", "lacunas",
    "ordem"}`, na ordem do manual. `so_humano` já vem somado com
    `constantes.CATEGORIAS_SO_HUMANO`.
    """
    linhas = (
        (
            await session.execute(
                select(AtendimentoCategoria)
                .where(AtendimentoCategoria.ativa.is_(True))
                .order_by(AtendimentoCategoria.ordem, AtendimentoCategoria.id)
            )
        )
        .scalars()
        .all()
    )
    if not linhas:
        return categorias_padrao()
    return [_categoria_dict(c) for c in linhas]


def ids_so_humano(categorias: list[dict]) -> set[str]:
    """Os assuntos que só pessoa responde — os das constantes SEMPRE entre eles."""
    return {c["id"] for c in categorias if c.get("so_humano")} | set(CATEGORIAS_SO_HUMANO)


# ── Conflito entre regras ─────────────────────────────────────────────────


def _limpo(valor: Any) -> str | None:
    if valor is None:
        return None
    v = str(valor).strip()
    return v or None


def regra_dict(r: AtendimentoRegra) -> dict:
    """A regra em dicionário JSON (o 409 `regra_conflitante` devolve isto)."""
    return {
        "id": str(r.id),
        "tipo": r.tipo,
        "categoria": r.categoria,
        "plataforma": r.plataforma,
        "canal": r.canal,
        "prioridade": r.prioridade,
        "quando": r.quando,
        "faca": r.faca,
        "ativa": r.ativa,
    }


def _pode_conflitar(tipo: str | None, categoria: str | None, ativa: bool) -> bool:
    return ativa and (tipo or TIPO_REGRA_CATEGORIA) == TIPO_REGRA_CATEGORIA and bool(categoria)


async def conflitos_da_regra(
    session: AsyncSession, regra_dados: dict, ignorar_id: UUID | str | None = None
) -> list[dict]:
    """As regras ATIVAS com que esta (criada ou editada) bateria.

    `regra_dados` = os campos da regra como ficariam (`tipo`, `categoria`,
    `plataforma`, `canal`, `ativa`; o que faltar vale o padrão: tipo
    `categoria`, ativa). `ignorar_id` = a própria regra, na edição. Lista
    vazia = pode gravar. Só regra ativa, do tipo `categoria` e COM assunto
    conflita; plataforma e canal casam exatos (vazio com vazio).
    """
    tipo = _limpo(regra_dados.get("tipo")) or TIPO_REGRA_CATEGORIA
    categoria = _limpo(regra_dados.get("categoria"))
    ativa = regra_dados.get("ativa")
    if not _pode_conflitar(tipo, categoria, True if ativa is None else bool(ativa)):
        return []
    consulta = select(AtendimentoRegra).where(
        AtendimentoRegra.ativa.is_(True),
        AtendimentoRegra.tipo == TIPO_REGRA_CATEGORIA,
        AtendimentoRegra.categoria == categoria,
        AtendimentoRegra.plataforma.is_not_distinct_from(_limpo(regra_dados.get("plataforma"))),
        AtendimentoRegra.canal.is_not_distinct_from(_limpo(regra_dados.get("canal"))),
    )
    if ignorar_id is not None:
        consulta = consulta.where(AtendimentoRegra.id != UUID(str(ignorar_id)))
    regras = (
        (await session.execute(consulta.order_by(AtendimentoRegra.created_at))).scalars().all()
    )
    return [regra_dict(r) for r in regras]


async def conflitos_existentes(session: AsyncSession) -> list[dict]:
    """Os conflitos que JÁ estão no banco (de antes da trava) — a tela pinta de vermelho.

    Um item por grupo: `{"categoria", "plataforma", "canal", "regra_ids":
    [str, ...], "regras": [regra_dict, ...]}` (regras na ordem de criação).
    """
    regras = (
        (
            await session.execute(
                select(AtendimentoRegra)
                .where(
                    AtendimentoRegra.ativa.is_(True),
                    AtendimentoRegra.tipo == TIPO_REGRA_CATEGORIA,
                    AtendimentoRegra.categoria.is_not(None),
                    AtendimentoRegra.categoria != "",
                )
                .order_by(AtendimentoRegra.created_at)
            )
        )
        .scalars()
        .all()
    )
    grupos: dict[tuple, list[AtendimentoRegra]] = defaultdict(list)
    for r in regras:
        grupos[(r.categoria, r.plataforma, r.canal)].append(r)
    return [
        {
            "categoria": categoria,
            "plataforma": plataforma,
            "canal": canal,
            "regra_ids": [str(r.id) for r in lista],
            "regras": [regra_dict(r) for r in lista],
        }
        for (categoria, plataforma, canal), lista in grupos.items()
        if len(lista) > 1
    ]


# ── Manual base: importar / exportar ──────────────────────────────────────


@dataclass
class Relatorio:
    """O que a importação fez (ou faria, no `--seco`). Sem texto de comprador."""

    categorias_novas: list[str] = field(default_factory=list)
    categorias_atualizadas: list[str] = field(default_factory=list)
    categorias_iguais: int = 0
    regras_novas: list[str] = field(default_factory=list)
    regras_atualizadas: list[str] = field(default_factory=list)
    regras_iguais: int = 0
    respostas_novas: list[str] = field(default_factory=list)
    respostas_atualizadas: list[str] = field(default_factory=list)
    respostas_iguais: int = 0
    erros: list[str] = field(default_factory=list)
    conflitos: list[str] = field(default_factory=list)
    gravado: bool = False

    @property
    def ok(self) -> bool:
        return not self.erros and not self.conflitos

    def linhas(self) -> list[str]:
        """O relatório para o terminal, uma linha por fato."""
        saida: list[str] = []
        for rotulo, lista in (("ERRO", self.erros), ("CONFLITO", self.conflitos)):
            saida += [f"{rotulo}: {x}" for x in lista]
        saida.append(
            f"assuntos: {len(self.categorias_novas)} novos, "
            f"{len(self.categorias_atualizadas)} atualizados, {self.categorias_iguais} iguais"
        )
        saida += [f"  + {x}" for x in self.categorias_novas]
        saida += [f"  ~ {x}" for x in self.categorias_atualizadas]
        saida.append(
            f"regras: {len(self.regras_novas)} novas, {len(self.regras_atualizadas)} "
            f"atualizadas, {self.regras_iguais} iguais"
        )
        saida += [f"  + {x}" for x in self.regras_novas]
        saida += [f"  ~ {x}" for x in self.regras_atualizadas]
        saida.append(
            f"respostas prontas: {len(self.respostas_novas)} novas, "
            f"{len(self.respostas_atualizadas)} atualizadas, {self.respostas_iguais} iguais"
        )
        saida += [f"  + {x}" for x in self.respostas_novas]
        saida += [f"  ~ {x}" for x in self.respostas_atualizadas]
        return saida


def _chave_texto(valor: str) -> str:
    """Forma de comparar o QUANDO/título: sem caixa, espaço único."""
    return " ".join(valor.casefold().split())


def _rotulo_regra(r: dict) -> str:
    return (
        f"[{r['tipo']}] {r['categoria'] or 'geral'} · {r['plataforma'] or 'todas'}/"
        f"{r['canal'] or 'todos'}: QUANDO {r['quando'][:70]}"
    )


def _rotulo_resposta(r: dict) -> str:
    return f"{r['titulo'][:70]} · {r['plataforma'] or 'todas'}/{r['canal'] or 'todos'}"


class _Leitor:
    """Confere o arquivo item a item, acumulando os erros (não para no primeiro)."""

    def __init__(self) -> None:
        self.erros: list[str] = []

    def erro(self, onde: str, msg: str) -> None:
        self.erros.append(f"{onde}: {msg}")

    def chaves(self, item: Any, permitidas: tuple[str, ...], onde: str) -> bool:
        if not isinstance(item, dict):
            self.erro(onde, "tem de ser um objeto {...}")
            return False
        desconhecidas = sorted(set(item) - set(permitidas))
        if desconhecidas:
            self.erro(onde, f"chave desconhecida: {', '.join(desconhecidas)}")
        return True

    def texto(
        self, item: dict, chave: str, onde: str, *, maximo: int, obrigatorio: bool = True
    ) -> str | None:
        valor = item.get(chave)
        if valor is None or (isinstance(valor, str) and not valor.strip()):
            if obrigatorio:
                self.erro(onde, f"`{chave}` vazio")
            return None
        if not isinstance(valor, str):
            self.erro(onde, f"`{chave}` tem de ser texto")
            return None
        valor = valor.strip()
        if len(valor) > maximo:
            self.erro(onde, f"`{chave}` passa de {maximo} caracteres")
        return valor

    def lista(self, item: dict, chave: str, onde: str) -> list[str]:
        valor = item.get(chave)
        if valor is None:
            return []
        if not isinstance(valor, list) or not all(isinstance(v, str) for v in valor):
            self.erro(onde, f"`{chave}` tem de ser uma lista de textos")
            return []
        return [v.strip() for v in valor if v.strip()]

    def booleano(self, item: dict, chave: str, onde: str, *, padrao: bool) -> bool:
        valor = item.get(chave, padrao)
        if not isinstance(valor, bool):
            self.erro(onde, f"`{chave}` tem de ser true ou false")
            return padrao
        return valor

    def plataforma_canal(self, item: dict, onde: str) -> tuple[str | None, str | None]:
        plataforma = _limpo(item.get("plataforma"))
        if plataforma is not None:
            plataforma = plataforma.lower()
            if plataforma == "todas":
                plataforma = None
            elif plataforma not in PLATAFORMAS:
                self.erro(onde, f"plataforma desconhecida: {plataforma}")
                plataforma = None
        canal = _limpo(item.get("canal"))
        if canal is not None:
            canal = canal.lower()
            validos = (
                CANAIS_POR_PLATAFORMA.get(plataforma, ())
                if plataforma
                else tuple({c for cs in CANAIS_POR_PLATAFORMA.values() for c in cs})
            )
            if canal not in validos:
                self.erro(onde, f"canal {canal} não existe em {plataforma or 'nenhuma plataforma'}")
                canal = None
        return plataforma, canal


def _ler_categorias(leitor: _Leitor, bruto: Any) -> list[dict]:
    if bruto is None:
        return []
    if not isinstance(bruto, list):
        leitor.erro("categorias", "tem de ser uma lista")
        return []
    saida: list[dict] = []
    vistos: set[str] = set()
    for i, item in enumerate(bruto, 1):
        onde = f"categorias[{i}]"
        if not leitor.chaves(item, _CHAVES_CATEGORIA, onde):
            continue
        cid = _limpo(item.get("id"))
        if not cid or not _FORMATO_ID.match(cid):
            leitor.erro(onde, "`id` tem de ser minúsculo, sem acento, [a-z0-9_] até 32")
            continue
        onde = f"categoria {cid}"
        if cid in vistos:
            leitor.erro(onde, "id repetido no arquivo")
            continue
        vistos.add(cid)
        lacunas = leitor.lista(item, "lacunas", onde)
        fora = [x for x in lacunas if x not in LACUNAS]
        if fora:
            leitor.erro(
                onde, f"lacuna desconhecida: {', '.join(fora)} (vale: {', '.join(LACUNAS)})"
            )
        exemplos = leitor.lista(item, "exemplos", onde)
        if len(exemplos) > MAX_EXEMPLOS_CATEGORIA:
            leitor.erro(onde, f"mais de {MAX_EXEMPLOS_CATEGORIA} exemplos")
        saida.append(
            {
                "id": cid,
                "nome": leitor.texto(item, "nome", onde, maximo=200) or cid,
                # A IA classifica pela descrição: sem ela, o assunto é só um nome.
                "descricao": leitor.texto(item, "descricao", onde, maximo=1000) or "",
                "exemplos": [e[:300] for e in exemplos],
                "so_humano": leitor.booleano(item, "so_humano", onde, padrao=False),
                "lacunas": [x for x in lacunas if x in LACUNAS],
                "ativa": leitor.booleano(item, "ativa", onde, padrao=True),
                "ordem": i * 10,
            }
        )
    return saida


def _ler_regras(leitor: _Leitor, bruto: Any) -> list[dict]:
    if bruto is None:
        return []
    if not isinstance(bruto, list):
        leitor.erro("regras", "tem de ser uma lista")
        return []
    saida: list[dict] = []
    # A regra se identifica por (tipo, assunto, plataforma, canal, QUANDO) —
    # é por essa chave que a reimportação acha a linha do banco. Duas do
    # arquivo com a mesma chave cairiam na MESMA regra do banco: a segunda
    # sobrescreveria o FAÇA da primeira a cada reimportação (o manual base
    # tinha duas de estilo "Sempre."), e o "reimportar não muda nada" some.
    vistas: dict[tuple, str] = {}
    for i, item in enumerate(bruto, 1):
        onde = f"regras[{i}]"
        if not leitor.chaves(item, _CHAVES_REGRA, onde):
            continue
        tipo = (_limpo(item.get("tipo")) or TIPO_REGRA_CATEGORIA).lower()
        if tipo not in TIPOS_REGRA:
            leitor.erro(onde, f"tipo desconhecido: {tipo} (vale: {', '.join(TIPOS_REGRA)})")
            continue
        categoria = _limpo(item.get("categoria"))
        if categoria is not None and tipo != TIPO_REGRA_CATEGORIA:
            # Segurança e estilo valem para TODA mensagem: assunto nelas
            # daria a impressão de que só valem ali.
            leitor.erro(onde, f"regra de {tipo} vale para todos os assuntos: tire a categoria")
            categoria = None
        prioridade = item.get("prioridade", PRIORIDADE_REGRA_PADRAO)
        if (
            isinstance(prioridade, bool)
            or not isinstance(prioridade, int)
            or not 0 <= prioridade <= MAX_PRIORIDADE
        ):
            leitor.erro(onde, f"`prioridade` tem de ser um número inteiro de 0 a {MAX_PRIORIDADE}")
            prioridade = PRIORIDADE_REGRA_PADRAO
        plataforma, canal = leitor.plataforma_canal(item, onde)
        quando = leitor.texto(item, "quando", onde, maximo=MAX_QUANDO)
        faca = leitor.texto(item, "faca", onde, maximo=MAX_FACA)
        if quando is None or faca is None:
            continue
        chave = _chave_regra(tipo, categoria, plataforma, canal, quando)
        if chave in vistas:
            leitor.erro(
                onde,
                f"repete a regra de {vistas[chave]} (mesmo tipo, assunto, plataforma, canal e "
                "QUANDO) — junte as duas ou mude o QUANDO",
            )
            continue
        vistas[chave] = onde
        saida.append(
            {
                "tipo": tipo,
                "categoria": categoria,
                "plataforma": plataforma,
                "canal": canal,
                "prioridade": prioridade,
                "quando": quando,
                "faca": faca,
                "_onde": onde,
            }
        )
    return saida


def _pares(plataforma: str | None, canal: str | None) -> list[tuple[str, str]]:
    """Os (plataforma, canal) em que uma resposta pronta pode ser usada."""
    return [
        (p, c)
        for p, canais in CANAIS_POR_PLATAFORMA.items()
        if plataforma in (None, p)
        for c in canais
        if canal in (None, c)
    ]


def _motivos_da_resposta(texto: str, plataforma: str | None, canal: str | None) -> list[str]:
    """O que o envio barraria nesta resposta pronta, onde ela vale.

    Lacuna fica de fora: resposta pronta pode ter `{rastreio}` para a pessoa
    completar (o envio barra até ela completar). O limite de caracteres só
    conta com plataforma E canal definidos: a resposta "para todas" de 800
    caracteres serve ao chat da Shopee, e o envio barra no pós-venda do ML.
    """
    motivos: list[str] = []
    for p, c in _pares(plataforma, canal):
        for m in validador.validar(texto, plataforma=p, canal=c, origem=ORIGEM_HUMANO):
            if m.startswith(validador._MOTIVO_LACUNA):
                continue
            if m.startswith("passa do limite") and (plataforma is None or canal is None):
                continue
            motivos.append(m)
    return list(dict.fromkeys(motivos))


def _ler_respostas(leitor: _Leitor, bruto: Any) -> list[dict]:
    if bruto is None:
        return []
    if not isinstance(bruto, list):
        leitor.erro("respostas_prontas", "tem de ser uma lista")
        return []
    saida: list[dict] = []
    vistas: set[tuple] = set()
    for i, item in enumerate(bruto, 1):
        onde = f"respostas_prontas[{i}]"
        if not leitor.chaves(item, _CHAVES_RESPOSTA, onde):
            continue
        titulo = leitor.texto(item, "titulo", onde, maximo=MAX_TITULO)
        texto_ = leitor.texto(item, "texto", onde, maximo=MAX_TEXTO_RESPOSTA)
        plataforma, canal = leitor.plataforma_canal(item, onde)
        if titulo is None or texto_ is None:
            continue
        chave = (_chave_texto(titulo), plataforma, canal)
        if chave in vistas:
            leitor.erro(onde, "título repetido para a mesma plataforma e canal")
            continue
        vistas.add(chave)
        motivos = _motivos_da_resposta(texto_, plataforma, canal)
        if motivos:
            leitor.erro(onde, "o envio barraria: " + "; ".join(motivos))
        saida.append(
            {
                "titulo": titulo,
                "texto": texto_,
                "plataforma": plataforma,
                "canal": canal,
                "categoria": _limpo(item.get("categoria")),
                "ordem": i * 10,
                "_onde": onde,
            }
        )
    return saida


def _chave_regra(tipo: str, categoria: str | None, plataforma, canal, quando: str) -> tuple:
    return (tipo, categoria, plataforma, canal, _chave_texto(quando))


async def importar_manual(
    session: AsyncSession, dados: Any, *, seco: bool = False
) -> Relatorio:
    """Importa o manual base. Só grava se TUDO passou; `seco` só conta.

    Não commita (flush só): quem chama decide — o script commita no fim, o
    teste lê na mesma sessão. Nunca apaga nem desativa o que não está no
    arquivo: tirar regra é decisão de pessoa, na tela.
    """
    rel = Relatorio()
    leitor = _Leitor()
    if not isinstance(dados, dict):
        rel.erros.append('o arquivo tem de ser um objeto {"categorias", "regras", ...}')
        return rel
    desconhecidas = sorted(set(dados) - set(_CHAVES_ARQUIVO))
    if desconhecidas:
        leitor.erro("arquivo", f"chave desconhecida: {', '.join(desconhecidas)}")
    categorias = _ler_categorias(leitor, dados.get("categorias"))
    regras = _ler_regras(leitor, dados.get("regras"))
    respostas = _ler_respostas(leitor, dados.get("respostas_prontas"))

    if not seco:
        # Conferir e gravar sob a mesma trava da tela (ver o topo).
        await travar_regras(session)

    # ── O que já está no banco ──
    cats_banco = {
        c.id: c for c in (await session.execute(select(AtendimentoCategoria))).scalars().all()
    }
    regras_banco = list(
        (
            await session.execute(select(AtendimentoRegra).order_by(AtendimentoRegra.created_at))
        )
        .scalars()
        .all()
    )
    respostas_banco = list(
        (
            await session.execute(
                select(AtendimentoModelo).order_by(AtendimentoModelo.created_at)
            )
        )
        .scalars()
        .all()
    )

    # Assuntos que valerão depois da importação: os ativos do arquivo e os
    # ativos do banco que o arquivo não mexe; nenhum = as constantes.
    do_arquivo = {c["id"]: c for c in categorias}
    conhecidas = {cid for cid, c in do_arquivo.items() if c["ativa"]} | {
        cid for cid, c in cats_banco.items() if c.ativa and cid not in do_arquivo
    }
    if not conhecidas:
        conhecidas = set(CATEGORIAS)
    for r in regras:
        if r["categoria"] is not None and r["categoria"] not in conhecidas:
            leitor.erro(r["_onde"], f"categoria {r['categoria']} não existe (nem no arquivo)")
    for r in respostas:
        if r["categoria"] is not None and r["categoria"] not in conhecidas:
            leitor.erro(r["_onde"], f"categoria {r['categoria']} não existe (nem no arquivo)")

    # ── Conflitos: dentro do arquivo e com o banco ──
    por_identidade: dict[tuple, AtendimentoRegra] = {}
    for r in regras_banco:
        chave = _chave_regra(r.tipo, r.categoria, r.plataforma, r.canal, r.quando)
        # Duas iguais no banco: a ativa é a que se atualiza.
        if chave not in por_identidade or (r.ativa and not por_identidade[chave].ativa):
            por_identidade[chave] = r
    no_arquivo: dict[tuple, dict] = {}
    for r in regras:
        if not _pode_conflitar(r["tipo"], r["categoria"], True):
            continue
        grupo = (r["categoria"], r["plataforma"], r["canal"])
        if grupo in no_arquivo:
            rel.conflitos.append(
                f"{r['_onde']} e {no_arquivo[grupo]['_onde']}: duas regras para "
                f"{r['categoria']} · {r['plataforma'] or 'todas'}/{r['canal'] or 'todos'}"
            )
            continue
        no_arquivo[grupo] = r
        mesma = por_identidade.get(
            _chave_regra(r["tipo"], r["categoria"], r["plataforma"], r["canal"], r["quando"])
        )
        for existente in regras_banco:
            if (
                existente is mesma
                or not existente.ativa
                or not _pode_conflitar(existente.tipo, existente.categoria, True)
                or (existente.categoria, existente.plataforma, existente.canal) != grupo
            ):
                continue
            rel.conflitos.append(
                f"{r['_onde']} bate com a regra ativa {existente.id} "
                f"(QUANDO {existente.quando[:70]}) — desative uma delas na tela antes"
            )

    rel.erros += leitor.erros
    if not rel.ok:
        logger.info(
            "atendimento_manual_recusado", erros=len(rel.erros), conflitos=len(rel.conflitos)
        )
        return rel

    # ── Plano (e gravação, fora do seco) ──
    for c in categorias:
        existente = cats_banco.get(c["id"])
        campos = {k: c[k] for k in ("nome", "descricao", "exemplos", "so_humano", "lacunas",
                                    "ativa", "ordem")}
        if existente is None:
            rel.categorias_novas.append(f"{c['id']} ({c['nome']})")
            if not seco:
                session.add(AtendimentoCategoria(id=c["id"], **campos))
        elif any(getattr(existente, k) != v for k, v in campos.items()):
            rel.categorias_atualizadas.append(f"{c['id']} ({c['nome']})")
            if not seco:
                for k, v in campos.items():
                    setattr(existente, k, v)
        else:
            rel.categorias_iguais += 1

    for r in regras:
        existente = por_identidade.get(
            _chave_regra(r["tipo"], r["categoria"], r["plataforma"], r["canal"], r["quando"])
        )
        if existente is None:
            rel.regras_novas.append(_rotulo_regra(r))
            if not seco:
                session.add(
                    AtendimentoRegra(
                        tipo=r["tipo"],
                        categoria=r["categoria"],
                        plataforma=r["plataforma"],
                        canal=r["canal"],
                        prioridade=r["prioridade"],
                        quando=r["quando"],
                        faca=r["faca"],
                        ativa=True,
                    )
                )
        elif (existente.faca, existente.prioridade, existente.ativa) != (
            r["faca"],
            r["prioridade"],
            True,
        ):
            rel.regras_atualizadas.append(_rotulo_regra(r))
            if not seco:
                existente.faca = r["faca"]
                existente.prioridade = r["prioridade"]
                existente.ativa = True
        else:
            rel.regras_iguais += 1

    resp_por_chave: dict[tuple, AtendimentoModelo] = {}
    for m in respostas_banco:
        resp_por_chave.setdefault((_chave_texto(m.titulo), m.plataforma, m.canal), m)
    for r in respostas:
        existente = resp_por_chave.get((_chave_texto(r["titulo"]), r["plataforma"], r["canal"]))
        if existente is None:
            rel.respostas_novas.append(_rotulo_resposta(r))
            if not seco:
                session.add(
                    AtendimentoModelo(
                        titulo=r["titulo"],
                        texto=r["texto"],
                        plataforma=r["plataforma"],
                        canal=r["canal"],
                        categoria=r["categoria"],
                        ordem=r["ordem"],
                        ativo=True,
                    )
                )
        elif (existente.texto, existente.categoria, existente.ativo) != (
            r["texto"],
            r["categoria"],
            True,
        ):
            rel.respostas_atualizadas.append(_rotulo_resposta(r))
            if not seco:
                existente.texto = r["texto"]
                existente.categoria = r["categoria"]
                existente.ativo = True
        else:
            rel.respostas_iguais += 1

    if not seco:
        await session.flush()
        rel.gravado = True
    logger.info(
        "atendimento_manual_importado" if not seco else "atendimento_manual_seco",
        categorias_novas=len(rel.categorias_novas),
        categorias_atualizadas=len(rel.categorias_atualizadas),
        regras_novas=len(rel.regras_novas),
        regras_atualizadas=len(rel.regras_atualizadas),
        respostas_novas=len(rel.respostas_novas),
        respostas_atualizadas=len(rel.respostas_atualizadas),
    )
    return rel


async def exportar_manual(session: AsyncSession) -> dict:
    """O manual ATIVO no formato do importador (exportar → importar não muda nada).

    Sem assunto no banco, exporta os das constantes: é o ponto de partida
    para escrever o manual base.
    """
    cats = (
        (
            await session.execute(
                select(AtendimentoCategoria)
                .where(AtendimentoCategoria.ativa.is_(True))
                .order_by(AtendimentoCategoria.ordem, AtendimentoCategoria.id)
            )
        )
        .scalars()
        .all()
    )
    categorias = (
        [
            {
                "id": c.id,
                "nome": c.nome,
                "descricao": c.descricao or "",
                "exemplos": _lista_de_textos(c.exemplos),
                # O do BANCO (sem somar as constantes): reimportar não muda nada.
                "so_humano": bool(c.so_humano),
                "lacunas": _lista_de_textos(c.lacunas),
            }
            for c in cats
        ]
        if cats
        else [
            {k: c[k] for k in ("id", "nome", "descricao", "exemplos", "so_humano", "lacunas")}
            for c in categorias_padrao()
        ]
    )
    ordem_tipo = {t: i for i, t in enumerate(TIPOS_REGRA)}
    regras = sorted(
        (
            await session.execute(
                select(AtendimentoRegra)
                .where(AtendimentoRegra.ativa.is_(True))
                .order_by(AtendimentoRegra.created_at)
            )
        )
        .scalars()
        .all(),
        key=lambda r: (
            ordem_tipo.get(r.tipo, len(TIPOS_REGRA)),
            r.prioridade,
            r.categoria or "",
            r.plataforma or "",
            r.canal or "",
        ),
    )
    respostas = (
        (
            await session.execute(
                select(AtendimentoModelo)
                .where(AtendimentoModelo.ativo.is_(True))
                .order_by(AtendimentoModelo.ordem, AtendimentoModelo.titulo)
            )
        )
        .scalars()
        .all()
    )
    return {
        "categorias": categorias,
        "regras": [
            {
                "tipo": r.tipo,
                "categoria": r.categoria,
                "plataforma": r.plataforma,
                "canal": r.canal,
                "prioridade": r.prioridade,
                "quando": r.quando,
                "faca": r.faca,
            }
            for r in regras
        ],
        "respostas_prontas": [
            {
                "titulo": m.titulo,
                "texto": m.texto,
                "plataforma": m.plataforma,
                "canal": m.canal,
                "categoria": m.categoria,
            }
            for m in respostas
        ],
    }
