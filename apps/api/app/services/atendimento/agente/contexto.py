"""O estado de UMA execução do agente (spec 3.2 e 4.1, 20/10/2026).

Dois objetos, nenhum vai ao modelo:

- `BaseDoGerar`: o que o `ia._gerar` JÁ leu antes de despachar para o agente
  (passos 1 a 4 da spec 3.1) — mensagens, contexto do pedido, lacunas,
  manual, sinais. O agente não relê nada disso.
- `ContextoAgente`: o que a execução vai juntando — o ESCOPO (que pedidos e
  anúncios as ferramentas aceitam: "não localizado neste atendimento" fora
  dele), o REGISTRO de evidências (ids que a resposta pode citar e os textos
  com números e nomes permitidos, que alimentam `ia.numeros_inventados` pelo
  `permitidos_extra` da `ia.conferir_saida`), as garantias que `ver_garantia`
  devolveu, o RASTRO enxuto (ferramenta, argumentos, ms, ids — nunca o
  conteúdo), as ações de pessoa do código e o uso/custo.

`ContextoAgente.para_fatos()` é o `rascunho.fatos["agente"]` — o que a
Caixa Humano, o envio e a reavaliação leem (`acoes_pessoa`, `acao`).

Os exemplos de OUTROS clientes (`buscar_respostas_equipe`) nunca entram no
registro: nem id (`par:` é recusado aqui) nem texto — número de exemplo é
número inventado. Item 32 da auditoria ao vivo (22/10/2026): os pares que o
PACOTE mostra (`pacote.secao_da_equipe`) ficam à parte, em `pares_do_pacote` — o
`par:<id>` deles pode ser citado (`checagens.conferir_evidencias`) e o texto vai ao
verificador como RESPOSTA DA EQUIPE CITADA, mas nunca vira número nem nome permitido.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, TypedDict
from uuid import UUID

from app.services.atendimento import constantes
from app.services.atendimento import contexto as contexto_svc
from app.services.atendimento.agente.custo import Uso

# ── Ids de evidência (spec 3.3.3) ─────────────────────────────────────────
# O que a `Proposta.evidencias` pode citar. Quem PROVA o id (pacote ou
# ferramenta) o registra com `ContextoAgente.permitir`; a checagem
# (`checagens.conferir_evidencias`) recusa id fora do registro.
ID_PEDIDO = "pedido"  # o pedido DA CONVERSA, que vem no pacote


def id_pedido(numero: str) -> str:
    """Outro pedido do comprador, lido por `ver_pedido`."""
    return f"pedido:{_limpo(numero)}"


def id_ficha(item_id: str) -> str:
    return f"ficha:{_limpo(item_id)}"


def id_variacoes(item_id: str) -> str:
    return f"variacoes:{_limpo(item_id)}"


def id_restricao(item_id: str) -> str:
    """A restrição de envio por UF do anúncio (`restricao_envio`)."""
    return f"restricao:{_limpo(item_id)}"


def id_especificacoes(item_id: str) -> str:
    """As especificações por fonte do anúncio (`especificacoes`)."""
    return f"espec:{_limpo(item_id)}"


def id_garantia(garantia_id: int | str) -> str:
    """Um registro do Painel de Garantia (`garantias.id`)."""
    return f"garantia:{_limpo(str(garantia_id))}"


def id_garantia_calculada(numero: str) -> str:
    """A garantia CALCULADA pelo Painel (sem registro) para o pedido `numero`."""
    return f"garantia:pedido:{_limpo(numero)}"


def id_fato(fato_id: int | str) -> str:
    """Um fato aprovado (`atendimento_fatos.id`)."""
    return f"fato:{_limpo(str(fato_id))}"


def id_regra(assunto: str) -> str:
    """A regra (e a orientação do agente) do assunto no manual."""
    return f"regra:{_limpo(assunto)}"


def id_modelo(modelo_id: Any) -> str:
    """Uma resposta pronta aprovada (`atendimento_modelos`)."""
    return f"modelo:{_limpo(str(modelo_id))}"


def id_troca(numero: str) -> str:
    """As opções de troca de `sugerir_troca` para o pedido."""
    return f"troca:{_limpo(numero)}"


def id_par(par_id: Any) -> str:
    """Um exemplo da equipe — só para o RASTRO; nunca é evidência."""
    return f"par:{_limpo(str(par_id))}"


def id_anuncio(item_id: str) -> str:
    """Um anúncio DESTA loja que a busca no catálogo achou (`buscar_anuncios_da_loja`,
    item 6 da rodada "redondo", 23/10/2026): prova só que a loja vende o produto e se
    ele está disponível — a conferência G1 (`checagens.conferir_outro_produto_da_loja`)."""
    return f"anuncio:{_limpo(item_id)}"


# O id de evidência ESCRITO num texto (o motivo "evidência citada sem prova
# (variacoes:58265493964)", o aviso, o erro) — a forma dos `id_*` acima. A
# máscara de dígitos (`ia.mascarar`) lia os 11 dígitos do anúncio da Shopee
# como CPF e o motivo do eval de 21/10 saiu "(variacoes:[CPF])", sem bater com
# a evidência citada nem com o registro: quem mascara texto com id de
# evidência deixa o id como está (`replay.mascarar_texto`).
EVIDENCIA_NO_TEXTO = re.compile(
    r"(?<![\w:])(?:pedido|ficha|variacoes|restricao|espec|garantia(?::pedido)?|fato|regra"
    r"|modelo|troca|par|anuncio):[\w{}-]+(?:\.[\w{}-]+)*"
)


# Prefixos que nunca provam nada (exemplo de outro cliente).
PREFIXOS_SEM_PROVA = ("par:",)


def _limpo(valor: str | None) -> str:
    return " ".join(str(valor or "").split())


# ── Orçamento das ferramentas (spec 4.1, regra 3) ─────────────────────────
# ~1.500 tokens por resultado e ~6 mil por conversa (português em JSON dá
# perto de 3,5 caracteres por token no tokenizador do Haiku 5.5).
MAX_CHARS_RESULTADO = 5_000
MAX_CHARS_FERRAMENTAS = 20_000
AVISO_CORTE = " …[cortado: passou do limite desta consulta]"
SEM_ORCAMENTO = (
    "limite de consultas desta conversa atingido: responda com o que já tem ou encaminhe"
)
# O rastro guarda o argumento curto (o modelo vê o texto mascarado; nada
# além disso entra) e poucos ids por passo.
MAX_CHARS_ARG_RASTRO = 160
MAX_IDS_RASTRO = 20
MAX_PASSOS_RASTRO = 40
MAX_EVIDENCIAS = 20


def ordenar_acoes(*listas: Iterable[str | None]) -> list[str]:
    """A união das ações de pessoa, na ordem da régua (`constantes.ACOES_PESSOA`). PURA.

    Código fora da régua some (o modelo não inventa ação nova); repetida
    entra uma vez. É a regra do `acao_pessoa.juntar`: o código sempre entra,
    o modelo só ACRESCENTA.
    """
    vistas = {a for lista in listas for a in lista if a}
    return [a for a in constantes.ACOES_PESSOA if a in vistas]


# ── O que o `_gerar` já leu ───────────────────────────────────────────────


@dataclass(frozen=True)
class BaseDoGerar:
    """O que o `ia._gerar` leu antes de chamar `agente.responder` (spec 3.1, passos 1-4).

    Os nomes são os das variáveis do `_gerar` (ver `interfaces.md`):

    - `recentes`: `_mensagens_recentes` (o `HistoricoMensagens`, gatilho incluso);
    - `rajada`: `rajada_do_cliente(recentes, gatilho)`;
    - `foco`: `perguntas_pendentes(recentes, gatilho)`;
    - `textos_cliente` / `textos_rajada`: os do `_gerar`;
    - `ctx`: `contexto_svc.contexto_da_conversa` (dicionário; formato do legado);
    - `valores`: `valores_das_lacunas(conversa, ctx)`;
    - `fatos`: `_fatos_para_o_modelo` (+ `sinais_do_cliente`), o formato que a
      `conferir_saida` e `evidencias.motivos_sem_evidencia` leem;
    - `fatos_gravados`: o que vai no `rascunho.fatos` (o agente acrescenta "agente");
    - `categorias`: `manual_svc.categorias_ativas` (os assuntos);
    - `regras`: `_regras_aplicaveis` (o manual INTEIRO do canal, `todas_regras`);
    - `sinais`: `sinais_com_codigo` já com a régua do pedido de pessoa do
      agente (`acao_pessoa.sinais_do_agente`, 21/10/2026: o `HUMANO_ATENDENTE`
      só fica com `pedido_de_pessoa`) (código `HUMANO_*`, frase);
    - `motivos`: os motivos de ANTES do modelo que valem para o agente — os
      de `sinais`, `MOTIVO_TURNO_INCOMPLETO` e os `SINAIS_PARA_PESSOA` do
      cartão; SEM a "opção de menu de assunto só para pessoa", a NF que exige
      pessoa, a instrução de recebimento e a dúvida de remetente (no agente
      eles viram ação de pessoa ou orientação: spec 6.2);
    - `opcoes_sensiveis`: `assuntos_sensiveis_do_menu(recentes, gatilho)`;
    - `sinais_do_cliente`: os códigos de `_sinais_do_cliente`;
    - `nomes`: os nomes para a máscara (`mascarar`); nunca vão ao modelo;
    - `modo`: `MODO_AUTO` (canal no automático, `so_admin`) ou `MODO_COPILOTO`;
    - `forcar`: o "Sugerir" da tela;
    - `nf_politica_aprovada`: o do `_gerar`;
    - `insistiu`: `insistiu_em_pessoa(recentes)`.
    """

    recentes: Sequence[Any]
    rajada: Sequence[Any]
    foco: Sequence[Any]
    textos_cliente: list[str]
    textos_rajada: list[str]
    ctx: dict
    valores: dict[str, str | None]
    fatos: dict
    fatos_gravados: dict
    categorias: list[dict]
    regras: Sequence[Any]
    sinais: list[tuple[str, str]]
    motivos: list[str]
    opcoes_sensiveis: list[str]
    sinais_do_cliente: list[str]
    nomes: tuple[str | None, ...]
    modo: str
    forcar: bool
    nf_politica_aprovada: bool
    insistiu: bool

    @property
    def ids_categorias(self) -> list[str]:
        return [c["id"] for c in self.categorias]

    @property
    def codigos_dos_sinais(self) -> list[str]:
        """Os códigos `HUMANO_*` dos sinais do texto, sem repetir."""
        return list(dict.fromkeys(codigo for codigo, _frase in self.sinais))


# ── O que a execução recebe de fora (replay, eval, teste) ─────────────────


class ExtraAgente(TypedDict, total=False):
    """O `ctx_extra` do `agente.responder`. Toda chave é opcional.

    - `as_of`: o instante do replay/eval (os pares só de antes dele);
    - `modo_replay`: replay noturno/eval — nada é gravado nem enviado, e a
      `conferir_saida` roda com `forcar=True` (a loja já respondeu depois);
    - `sem_ida_a_loja`: as ferramentas não vão à loja (o padrão no replay);
    - `ferramentas_fora`: nomes de ferramenta tirados (ablação do eval);
    - `sem_fatos`: `fatos_loja` não devolve nada (o eval "sem os fatos novos");
    - `modelo`, `effort`, `max_turnos`: troca do configurado (braços do eval);
    - `cliente`: o `anthropic.AsyncAnthropic` já montado (teste: transporte
      falso do httpx2; eval: o braço Sonnet);
    - `prazo_s`: o prazo cooperativo desta execução (o "Sugerir" e a rodada
      do cron: `ia.prazo_do_agente`); sem ele, o configurado;
    - `teto_conversa_usd`: o teto da conversa desta execução (o eval: o braço
      Sonnet custa ~10× o Haiku; 0 = sem teto da conversa); sem ele, o
      configurado.
    """

    as_of: datetime
    modo_replay: bool
    sem_ida_a_loja: bool
    ferramentas_fora: frozenset[str]
    sem_fatos: bool
    modelo: str
    effort: str
    max_turnos: int
    cliente: Any
    prazo_s: float
    teto_conversa_usd: float


_TIPOS_EXTRA: dict[str, type | tuple[type, ...]] = {
    "as_of": datetime,
    "modo_replay": bool,
    "sem_ida_a_loja": bool,
    "ferramentas_fora": (frozenset, set, tuple, list),
    "sem_fatos": bool,
    "modelo": str,
    "effort": str,
    "max_turnos": int,
    "cliente": object,
    "prazo_s": (int, float),
    "teto_conversa_usd": (int, float),
}


def ler_extra(extra: Mapping[str, Any] | None) -> ExtraAgente:
    """Confere o `ctx_extra` (chave desconhecida ou tipo errado = TypeError). PURA."""
    saida: dict[str, Any] = {}
    for chave, valor in (extra or {}).items():
        tipo = _TIPOS_EXTRA.get(chave)
        if tipo is None:
            raise TypeError(f"ctx_extra: chave desconhecida {chave!r}")
        if not isinstance(valor, tipo) or (
            (tipo is int or tipo == (int, float)) and isinstance(valor, bool)
        ):
            raise TypeError(f"ctx_extra[{chave!r}]: tipo {type(valor).__name__}")
        if chave == "ferramentas_fora":
            valor = frozenset(str(v) for v in valor)
        if chave == "as_of" and valor.tzinfo is None:
            raise TypeError("ctx_extra['as_of']: datetime sem fuso")
        saida[chave] = valor
    return ExtraAgente(**saida)  # type: ignore[typeddict-item]


# ── Registro de evidências e rastro ───────────────────────────────────────

# O número com separador ("16.128", "6,9", "15.0", "2.256") e cada grupo de
# dígitos.
_NUMERO_COMPOSTO = re.compile(r"\d+(?:[.,]\d+)+")
_DIGITOS = re.compile(r"\d+")
# O valor em dinheiro nunca se parte (revisão r5i de 21/10/2026: "R$ 349,90"
# liberava o "90" de "Ainda restam 90 unidades"): com "R$" ou com os centavos
# (",dd" no fim do número).
_DINHEIRO = re.compile(r"R\$\s*\d[\d.]*(?:,\d+)?|\d[\d.]*,\d{2}(?!\d)")


def numeros_soltos(textos: Iterable[str | None]) -> str:
    """As formas SOLTAS dos números dos `textos`, que o `ia.numeros_inventados`
    não enxerga sozinho (21/10/2026). PURA. "" quando não há nenhuma.

    O `ia._numeros` junta o número com separador ("16.128" → "16128", "Android
    15.0" → "150", "6,9" → "69"): a RAM e o armazenamento do título da Uranyx
    ("16.128 Gb") escritos como "16 GB de RAM e 128 GB" — o dado do anúncio —
    viravam "número que não veio do sistema". Aqui entra cada grupo de dígitos
    do número com separador e o número sem o zero à esquerda ("05/11" → "5").
    Só o agente usa (`ContextoAgente.permitidos_extra`), e só com os textos de
    PRODUTO (título, atributos, especificações, cartões; revisão r5i). O valor
    em dinheiro ("R$ 349,90", "349,90") não se parte.
    """
    extras: dict[str, None] = {}
    for texto in textos:
        if not texto:
            continue
        texto = _DINHEIRO.sub(" ", texto)
        for composto in _NUMERO_COMPOSTO.findall(texto):
            extras.update(dict.fromkeys(_DIGITOS.findall(composto)))
        for grupo in _DIGITOS.findall(texto):
            sem_zero = grupo.lstrip("0") or "0"
            if sem_zero != grupo:
                extras[sem_zero] = None
    return " ".join(extras)


@dataclass
class RegistroEvidencias:
    """O que a resposta pode citar: ids (evidência) e textos (números/nomes)."""

    ids: set[str] = field(default_factory=set)
    textos: list[str] = field(default_factory=list)

    def permitir_ids(self, *ids: str | None) -> None:
        for bruto in ids:
            i = _limpo(bruto)
            if not i:
                continue
            if i.startswith(PREFIXOS_SEM_PROVA):
                raise ValueError(f"id que não é prova: {i!r} (exemplo de outro cliente)")
            self.ids.add(i)

    def permitir_textos(self, *textos: str | None) -> None:
        """Texto (o JSON que a ferramenta devolveu) com números e nomes permitidos."""
        self.textos.extend(t for t in textos if t)

    def permite(self, evidencia: str | None) -> bool:
        return _limpo(evidencia) in self.ids

    def desconhecidas(self, evidencias: Iterable[str | None]) -> list[str]:
        """As evidências citadas que nada provou (na ordem, sem repetir)."""
        return list(
            dict.fromkeys(e for e in (_limpo(x) for x in evidencias) if e and not self.permite(e))
        )


@dataclass(frozen=True)
class PassoRastro:
    """Uma chamada de ferramenta, ENXUTA: sem o conteúdo devolvido."""

    ferramenta: str
    args: dict[str, Any]
    ms: int
    ids: tuple[str, ...]
    erro: str | None = None
    cortado: bool = False

    def para_fatos(self) -> dict:
        saida: dict[str, Any] = {
            "ferramenta": self.ferramenta,
            "args": self.args,
            "ms": self.ms,
            "ids": list(self.ids),
        }
        if self.erro:
            saida["erro"] = self.erro
        if self.cortado:
            saida["cortado"] = True
        return saida


def _arg_curto(valor: Any) -> Any:
    if valor is None or isinstance(valor, bool | int | float):
        return valor
    texto = _limpo(str(valor))
    return texto if len(texto) <= MAX_CHARS_ARG_RASTRO else texto[: MAX_CHARS_ARG_RASTRO - 1] + "…"


def _normalizar_pedido(numero: str | None) -> str:
    return _limpo(numero).lstrip("#").strip().lower()


# ── O contexto da execução ────────────────────────────────────────────────


@dataclass
class ContextoAgente:
    """O estado de UMA execução do agente (uma sugestão). Mutável; nunca vai ao modelo.

    Monte com `ContextoAgente.da_conversa(...)`. O pacote (`pacote.montar_pacote`)
    acrescenta ao escopo o que achou (pedido do Bling, anúncios dos cartões);
    cada ferramenta confere o escopo, registra o passo e permite o que provou.
    """

    conversa_id: UUID
    gatilho_id: UUID | None
    canal_id: UUID | None
    integration_id: UUID | None
    plataforma: str
    canal: str
    modo: str
    comprador_id: str | None = None
    # Para a máscara (`ia.mascarar`) e nada mais: nunca vão ao modelo.
    nomes: tuple[str | None, ...] = ()
    # Os nomes INTERNOS da loja (`integrations.name`, a conta): nunca vão ao
    # modelo; a checagem barra no texto (`checagens.conferir_nomes_internos`).
    nomes_da_loja: tuple[str, ...] = ()
    as_of: datetime | None = None
    modo_replay: bool = False
    sem_ida_a_loja: bool = False
    ferramentas_fora: frozenset[str] = frozenset()
    sem_fatos: bool = False
    # O teto da conversa desta execução (`ExtraAgente.teto_conversa_usd`);
    # None = o configurado.
    teto_conversa_usd: float | None = None
    # Escopo (normalizados: sem espaço, sem "#", minúsculo).
    pedidos: set[str] = field(default_factory=set)
    anuncios: set[str] = field(default_factory=set)
    registro: RegistroEvidencias = field(default_factory=RegistroEvidencias)
    # O texto do pacote que só libera NÚMERO (`permitir_numeros`, 21/10/2026).
    numeros_vistos: list[str] = field(default_factory=list)
    # Os textos de PRODUTO (as fichas, o `ver_anuncio`, os cartões, os itens do
    # pedido): só deles saem as formas soltas (`numeros_soltos`; revisão r5i).
    textos_de_produto: list[str] = field(default_factory=list)
    # O que `ver_garantia` devolveu (o formato do bloco do Painel: inicio,
    # fim_hardware, fim_software, status, produto — e `regra` no cálculo).
    garantias_conferidas: list[dict] = field(default_factory=list)
    garantia_consultada: bool = False
    rastro: list[PassoRastro] = field(default_factory=list)
    chars_de_ferramenta: int = 0
    # As ações de pessoa do CÓDIGO (`acao_pessoa.gatilhos_de_codigo`) e as do
    # MODELO (a `Proposta.acao_pessoa`): `acoes_pessoa` junta as duas.
    acoes_codigo: list[str] = field(default_factory=list)
    acoes_modelo: list[str] = field(default_factory=list)
    # Os assuntos de orientação que o TURNO toca (`acao_pessoa.pistas_de_orientacao`):
    # o envio sozinho exige todos no `auto_orientacao` do canal.
    pistas_orientacao: list[str] = field(default_factory=list)
    # Rodada 4b (achado 2 da revisão r4): TODAS as pistas de assunto do turno
    # (`acao_pessoa.pistas_do_turno`) e as do rascunho (as lacunas do assunto,
    # `acao_pessoa.pistas_do_rascunho`): o envio sozinho exige liberada cada uma
    # que o assunto declarado não cobre (`ia.trava_do_agente`).
    pistas_turno: list[str] = field(default_factory=list)
    # Os OUTROS pedidos que as ferramentas leram (o contexto e o `postado` de
    # cada um): depois do laço, a régua de estado roda neles também
    # (`acao_pessoa.gatilhos_de_outro_pedido`). Nunca vão ao modelo nem ao rascunho.
    pedidos_lidos: list[tuple[dict, str | None]] = field(default_factory=list)
    # As fichas que o `ver_anuncio` leu (a trava da ficha no automático da
    # `conferir_saida` vale para elas como para as do pacote).
    fichas_lidas: list[dict] = field(default_factory=list)
    # Os fatos APROVADOS que o `consultar_manual` devolveu (`fato:<id>` → o
    # texto; Kia ao vivo, 21/10/2026): o fato citado nas evidências é fonte da
    # característica (`checagens.caracteristicas_fora_do_dado`).
    fatos_lidos: dict[str, str] = field(default_factory=dict)
    # O bloco do pedido DA CONVERSA (`Pacote.pedido`; revisão "furos" de
    # 20/10/2026): o estado e a origem que a resposta afirma são conferidos
    # contra ele (`checagens.conferir_estado_do_pedido`/`conferir_origem`).
    pedido_da_conversa: dict | None = None
    # Item 32 da auditoria ao vivo (22/10/2026): os pares da equipe que o PACOTE
    # mostrou (`par:<id>` → {"pergunta", "resposta", "mesma_loja", "mesmo_anuncio",
    # "mesmo_produto", "fonte_de_produto", …}). Citáveis; nunca no `registro` (nada de
    # número permitido).
    pares_do_pacote: dict[str, dict] = field(default_factory=dict)
    # Item 32 (3c): a devolução já aberta na plataforma sem nada para a loja decidir
    # (`acao_pessoa.devolucao_de_rotina`): o envio sozinho não para no "em andamento"
    # dela (`ia.andamento_no_rascunho` lê a marca no `para_fatos`).
    devolucao_de_rotina: bool = False
    # Item 6 da rodada "redondo" (23/10/2026): o que a `buscar_anuncios_da_loja` achou —
    # `anuncios_buscados` (item_id → {"nome_curto", "variacoes_casadas", "disponivel",
    # "da_conversa", "titulo_plano"}) e `buscas_catalogo` (cada busca: {"termo",
    # "acessorios", "modelo", "achados"}). Nunca no `registro.textos` (não é fonte de
    # característica): o verificador as lê à parte (BUSCA NO CATÁLOGO DA LOJA) e a
    # conferência G1 exige o `anuncio:<id>` delas.
    anuncios_buscados: dict[str, dict] = field(default_factory=dict)
    buscas_catalogo: list[dict] = field(default_factory=list)
    # Revisão do fechamento (23/10/2026): os anúncios que o PACOTE aceitou (o da conversa,
    # os cartões desta loja, os itens do pedido, as fichas), antes das ferramentas — o
    # "da_conversa" da busca no catálogo (o `ver_anuncio` de outro anúncio da loja também
    # o põe em `anuncios`).
    anuncios_do_pacote: frozenset[str] = frozenset()
    uso: Uso = field(default_factory=Uso)
    # Preenchidos pelo laço (`agente.responder`).
    acao: str | None = None
    evidencias: list[str] = field(default_factory=list)
    parada: str | None = None
    turnos: int = 0
    modelo: str | None = None
    effort: str | None = None
    avisos_auto: list[str] = field(default_factory=list)
    # Rodada 4 (21/10/2026), o contrato novo da camada determinística: os
    # avisos das regras SUAVES (a pessoa vê, nos dois modos) e por que o
    # rascunho não pode sair sozinho (suave, pessoa ou bloqueio) — a trava do
    # envio (`ia.trava_do_agente`) lê `sem_auto`.
    avisos: list[str] = field(default_factory=list)
    sem_auto: list[str] = field(default_factory=list)
    # As isenções do envio (revisão r5i de 21/10/2026): "caminho_uranyx" = a
    # frase do caminho da Uranyx na garantia foi liberada pelo `ver_garantia`
    # (o `enviar` revalida o texto sem ela, só neste rascunho).
    isencoes: list[str] = field(default_factory=list)
    ms: int | None = None
    # O `max_tokens` da passada em curso (ajuste r3): a estimativa do custo da
    # requisição que estourou o tempo usa o da passada, não o da 1ª.
    max_tokens_da_passada: int | None = None

    @classmethod
    def da_conversa(
        cls,
        conversa: Any,
        gatilho: Any,
        *,
        modo: str,
        nomes: Sequence[str | None] = (),
        extra: Mapping[str, Any] | None = None,
    ) -> ContextoAgente:
        """O contexto de uma conversa (`AtendimentoConversa`), com o escopo inicial.

        Escopo inicial: as chaves do pedido da conversa
        (`contexto._chaves_do_pedido`) e o anúncio da conversa
        (`conversa.anuncio_id`). No replay, `as_of` é o instante do gatilho
        quando não veio no `extra`, e a ida à loja fica desligada.
        """
        ext = ler_extra(extra)
        replay = bool(ext.get("modo_replay", False))
        as_of = ext.get("as_of")
        if as_of is None and replay and gatilho is not None:
            as_of = getattr(gatilho, "enviada_em", None) or getattr(gatilho, "created_at", None)
        ctx = cls(
            conversa_id=conversa.id,
            gatilho_id=getattr(gatilho, "id", None),
            canal_id=getattr(conversa, "canal_id", None),
            integration_id=getattr(conversa, "integration_id", None),
            plataforma=conversa.plataforma,
            canal=conversa.canal,
            modo=modo,
            comprador_id=getattr(conversa, "comprador_id", None),
            nomes=tuple(nomes),
            as_of=as_of,
            modo_replay=replay,
            sem_ida_a_loja=bool(ext.get("sem_ida_a_loja", replay)),
            ferramentas_fora=ext.get("ferramentas_fora", frozenset()),
            sem_fatos=bool(ext.get("sem_fatos", False)),
            modelo=ext.get("modelo"),
            effort=ext.get("effort"),
            teto_conversa_usd=(
                float(ext["teto_conversa_usd"]) if "teto_conversa_usd" in ext else None
            ),
        )
        if (getattr(conversa, "pedido_marketplace", None) or "").strip():
            ctx.aceitar_pedido(*contexto_svc._chaves_do_pedido(conversa))
        ctx.aceitar_anuncio(getattr(conversa, "anuncio_id", None))
        return ctx

    # ── tempo ──

    def agora(self) -> datetime:
        """O "agora" das consultas: o `as_of` do replay ou o relógio (UTC)."""
        return self.as_of or datetime.now(UTC)

    # ── escopo ──

    def aceitar_pedido(self, *numeros: str | None) -> None:
        """Põe no escopo (pedido desta conversa ou do mesmo comprador nesta loja)."""
        self.pedidos.update(n for n in (_normalizar_pedido(x) for x in numeros) if n)

    def pedido_no_escopo(self, numero: str | None) -> bool:
        n = _normalizar_pedido(numero)
        return bool(n) and n in self.pedidos

    def aceitar_anuncio(self, *item_ids: str | None) -> None:
        """Põe no escopo um anúncio CITADO (conversa, cartão, item do pedido)."""
        self.anuncios.update(i for i in (_limpo(x) for x in item_ids) if i)

    def anuncio_citado(self, item_id: str | None) -> bool:
        """O anúncio foi citado na conversa? (o da mesma integração em
        `product_links` a ferramenta confere no banco)."""
        i = _limpo(item_id)
        return bool(i) and i in self.anuncios

    # ── evidências ──

    def permitir(
        self,
        *,
        ids: Iterable[str | None] = (),
        textos: Iterable[str | None] = (),
        produto: bool = False,
    ) -> None:
        """O que o pacote ou uma ferramenta PROVOU: ids citáveis e textos
        permitidos. `produto` = os textos são do anúncio (as fichas, o
        `ver_anuncio`): deles saem também as formas soltas dos números."""
        textos = [t for t in textos if t]
        self.registro.permitir_ids(*ids)
        self.registro.permitir_textos(*textos)
        if produto:
            self.textos_de_produto.extend(textos)

    def permitir_numeros(self, *textos: str | None, produto: bool = False) -> None:
        """Texto do pacote que o modelo VIU e que só libera NÚMERO (21/10/2026).

        Os cartões (inclusive o do anúncio de outro vendedor, que nunca é fonte
        de característica), os itens do pedido e as compras do comprador: o
        número escrito ali não foi inventado pelo modelo. Não entra no registro
        (`registro.textos`), que é a evidência que o verificador lê. O
        cabeçalho (a hora de agora, o prazo de resposta) e o estado da conversa
        NÃO entram (revisão r5i de 21/10/2026: o minuto 35 liberava "Temos 35
        unidades"). `produto` = texto de produto (os cartões, os itens): dele
        saem também as formas soltas.
        """
        textos_ok = [t for t in textos if t]
        self.numeros_vistos.extend(textos_ok)
        if produto:
            self.textos_de_produto.extend(textos_ok)

    def permitidos_extra(self) -> list[str]:
        """O `permitidos_extra` da `ia.conferir_saida`: o registro, os números
        do pacote e, quando houver, as formas soltas dos textos de PRODUTO
        (`numeros_soltos`; nunca do pedido, do dinheiro, da hora ou das datas
        do cabeçalho)."""
        textos = [*self.registro.textos, *self.numeros_vistos]
        soltos = numeros_soltos(self.textos_de_produto)
        return [*textos, soltos] if soltos else textos

    def registrar_pedido_lido(self, ctx_pedido: Mapping[str, Any], postado: str | None) -> None:
        """Um OUTRO pedido lido por ferramenta (ver `pedidos_lidos`)."""
        if isinstance(ctx_pedido, Mapping) and ctx_pedido.get("pedido"):
            self.pedidos_lidos.append((dict(ctx_pedido), postado))

    def registrar_fatos(self, fatos: Iterable[Mapping[str, Any]]) -> None:
        """Os fatos aprovados que uma ferramenta devolveu ({"id": "fato:<id>", "texto"})."""
        for f in fatos:
            i, texto = _limpo(f.get("id")), f.get("texto")
            if i and isinstance(texto, str) and texto.strip():
                self.fatos_lidos[i] = texto

    def fatos_citados(self, evidencias: Iterable[str | None]) -> list[str]:
        """Os textos dos fatos aprovados LIDOS que a resposta cita (na ordem, sem repetir)."""
        return [
            self.fatos_lidos[i]
            for i in dict.fromkeys(_limpo(e) for e in evidencias if e)
            if i in self.fatos_lidos
        ]

    def registrar_pares(self, pares: Iterable[Mapping[str, Any]]) -> None:
        """Os pares da equipe que o pacote mostrou (`pares.exemplos_do_turno`)."""
        for par in pares:
            i = _limpo(par.get("id"))
            if i.startswith(PREFIXOS_SEM_PROVA):
                self.pares_do_pacote[i] = dict(par)

    def pares_citados(
        self, evidencias: Iterable[str | None], *, de_produto: bool = False
    ) -> list[dict]:
        """Os pares do pacote que a resposta cita (na ordem, sem repetir); com
        `de_produto`, só os que valem para produto (`fonte_de_produto`: a conversa com UM
        anúncio, de UM modelo, e o par desse anúncio ou do mesmo conjunto de produtos —
        revisão do item 32, 22/10/2026)."""
        saida = []
        for i in dict.fromkeys(_limpo(e) for e in evidencias if e):
            par = self.pares_do_pacote.get(i)
            if par is not None and (not de_produto or par.get("fonte_de_produto") is True):
                saida.append(par)
        return saida

    def registrar_garantias(self, garantias: Iterable[Mapping[str, Any]]) -> None:
        """O que `ver_garantia` devolveu vira `garantias_conferidas` (sem repetir)."""
        self.garantia_consultada = True
        for g in garantias:
            item = dict(g)
            if item not in self.garantias_conferidas:
                self.garantias_conferidas.append(item)

    def registrar_busca(self, busca: Mapping[str, Any]) -> None:
        """Uma busca no catálogo da loja (`buscar_anuncios_da_loja`): o termo e os
        anúncios achados (`busca["anuncios"]`: os dicionários de `catalogo.Achado`).

        Revisão do fechamento (23/10/2026): cada busca guarda os SEUS achados ("anuncios":
        o mesmo anúncio buscado por "A18" e por "C17" tem o disponível de cada termo — as
        variações casadas de cada um) e os acessórios que nenhum título da loja tem
        ("sem_no_titulo", `catalogo.sem_no_titulo`); `anuncios_buscados` fica com o último."""
        achados = [dict(a) for a in busca.get("anuncios") or [] if isinstance(a, Mapping)]
        for a in achados:
            item = _limpo(a.get("item_id"))
            if item:
                self.anuncios_buscados[item] = {k: v for k, v in a.items() if k != "item_id"}
        self.buscas_catalogo.append(
            {
                "termo": _limpo(busca.get("termo")),
                "acessorios": sorted(busca.get("acessorios") or []),
                "modelo": bool(busca.get("modelo")),
                "achados": [_limpo(a.get("item_id")) for a in achados if a.get("item_id")],
                "anuncios": [
                    {**a, "item_id": _limpo(a.get("item_id"))} for a in achados if a.get("item_id")
                ],
                "sem_no_titulo": sorted(str(x) for x in busca.get("sem_no_titulo") or []),
            }
        )

    def estado_das_ferramentas(self) -> tuple:
        """O que as ferramentas mudam no `ctx` (para a 2ª passada voltar a ele)."""
        return (
            self.chars_de_ferramenta,
            set(self.registro.ids),
            list(self.registro.textos),
            [dict(g) for g in self.garantias_conferidas],
            self.garantia_consultada,
            list(self.fichas_lidas),
            list(self.pedidos_lidos),
            list(self.acoes_codigo),
            set(self.pedidos),
            set(self.anuncios),
            dict(self.fatos_lidos),
            {k: dict(v) for k, v in self.anuncios_buscados.items()},
            [dict(b) for b in self.buscas_catalogo],
            list(self.numeros_vistos),
        )

    def voltar_ferramentas(self, estado: tuple) -> None:
        """Volta ao `estado_das_ferramentas` (o rastro fica: é o registro do que rodou)."""
        (
            self.chars_de_ferramenta,
            ids,
            textos,
            garantias,
            self.garantia_consultada,
            fichas,
            pedidos_lidos,
            acoes,
            pedidos,
            anuncios,
            fatos_lidos,
            buscados,
            buscas,
            numeros_vistos,
        ) = estado
        self.registro.ids = set(ids)
        self.registro.textos = list(textos)
        self.garantias_conferidas = [dict(g) for g in garantias]
        self.fichas_lidas = list(fichas)
        self.pedidos_lidos = list(pedidos_lidos)
        self.acoes_codigo = list(acoes)
        self.pedidos = set(pedidos)
        self.anuncios = set(anuncios)
        self.fatos_lidos = dict(fatos_lidos)
        self.anuncios_buscados = {k: dict(v) for k, v in buscados.items()}
        self.buscas_catalogo = [dict(b) for b in buscas]
        self.numeros_vistos = list(numeros_vistos)

    # ── rastro e orçamento ──

    def cortar_resultado(self, texto: str) -> str:
        """O resultado de uma ferramenta dentro do orçamento (por resultado e por conversa).

        Passou do limite: corta com aviso. Orçamento da conversa esgotado:
        devolve `SEM_ORCAMENTO`. Conta o que sai.
        """
        restante = MAX_CHARS_FERRAMENTAS - self.chars_de_ferramenta
        if restante <= len(AVISO_CORTE):
            return SEM_ORCAMENTO
        teto = min(MAX_CHARS_RESULTADO, restante)
        if len(texto) > teto:
            texto = texto[: teto - len(AVISO_CORTE)].rstrip() + AVISO_CORTE
        self.chars_de_ferramenta += len(texto)
        return texto

    def registrar_passo(
        self,
        ferramenta: str,
        args: Mapping[str, Any],
        ms: float,
        *,
        ids: Iterable[str] = (),
        erro: str | None = None,
        cortado: bool = False,
    ) -> PassoRastro:
        """Uma chamada de ferramenta no rastro (argumentos curtos, sem o resultado)."""
        passo = PassoRastro(
            ferramenta=ferramenta,
            args={str(k): _arg_curto(v) for k, v in args.items()},
            ms=max(0, int(round(ms))),
            ids=tuple(list(dict.fromkeys(_limpo(i) for i in ids if i))[:MAX_IDS_RASTRO]),
            erro=(_limpo(erro)[:MAX_CHARS_ARG_RASTRO] or None) if erro else None,
            cortado=cortado,
        )
        if len(self.rastro) < MAX_PASSOS_RASTRO:
            self.rastro.append(passo)
        return passo

    # ── decisão ──

    @property
    def acoes_pessoa(self) -> list[str]:
        """As do código mais as do modelo, na ordem da régua."""
        return ordenar_acoes(self.acoes_codigo, self.acoes_modelo)

    def para_fatos(self) -> dict:
        """O `rascunho.fatos["agente"]` (spec 3.3.3): enxuto, sem conteúdo de ferramenta.

        Item 32 (22/10/2026): "pares_do_pacote" (os ids que o pacote mostrou) e
        "devolucao_de_rotina" só quando há — o registro de antes não muda.
        """
        extra: dict[str, Any] = {}
        if self.pares_do_pacote:
            extra["pares_do_pacote"] = list(self.pares_do_pacote)
            # Decisão do dono (22/10/2026): a pergunta de produto sai sozinha com a
            # resposta da equipe do MESMO anúncio, de UM modelo (`ia.trava_do_agente`).
            de_produto = [
                i for i, par in self.pares_do_pacote.items() if par.get("fonte_de_produto") is True
            ]
            if de_produto:
                extra["pares_de_produto"] = de_produto
        if self.devolucao_de_rotina:
            extra["devolucao_de_rotina"] = True
        if self.buscas_catalogo:
            # Item 6 da rodada "redondo" (23/10/2026): o termo e os anúncios achados.
            extra["buscas_catalogo"] = [
                {"termo": b.get("termo"), "achados": list(b.get("achados") or [])}
                for b in self.buscas_catalogo
            ]
        return {
            **extra,
            "versao": constantes.PROMPT_VERSAO_AGENTE,
            "modelo": self.modelo,
            "effort": self.effort,
            "acao": self.acao,
            "acoes_pessoa": self.acoes_pessoa,
            "acoes_codigo": ordenar_acoes(self.acoes_codigo),
            "acoes_modelo": ordenar_acoes(self.acoes_modelo),
            # O envio sozinho lê os dois (revisão de 20/10/2026): o modo em que
            # o rascunho nasceu (o do copiloto não sai sozinho se o canal virar
            # automático depois) e as pistas de orientação do turno.
            "modo": self.modo,
            "pistas_orientacao": list(self.pistas_orientacao),
            "pistas_turno": list(self.pistas_turno),
            "evidencias": list(dict.fromkeys(_limpo(e) for e in self.evidencias if e))[
                :MAX_EVIDENCIAS
            ],
            "rastro": [p.para_fatos() for p in self.rastro],
            "garantia_consultada": self.garantia_consultada,
            "parada": self.parada,
            "turnos": self.turnos,
            "ms": self.ms,
            "uso": self.uso.para_fatos(),
            "custo_usd": round(self.uso.custo_usd, 6),
            "avisos_auto": list(self.avisos_auto),
            "avisos": list(self.avisos),
            "sem_auto": list(self.sem_auto),
            "isencoes": list(self.isencoes),
            "modo_replay": self.modo_replay,
            "as_of": self.as_of.isoformat() if self.as_of else None,
        }
