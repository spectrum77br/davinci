"""Legenda automática do robô de postagem — cascata, rodízio e render.

Eduardo, 16/09/2026: *"com base no produto do criativo e se não, cai num
padrão da marca… também tem que sair quando o post sai automático"*. Hoje o
modal pré-preenche a legenda com o `roteiro`, que é prompt de geração do
vídeo em inglês, e dos dois Reels já publicados um saiu SEM legenda nenhuma.

A cascata é uma só, e é esta:

    legenda da postagem → legenda do criativo → modelo do PRODUTO
                        → modelo da MARCA → nenhuma

`resolver()` é chamada UMA vez, quando a postagem é criada (`agendar()`), e o
texto JÁ RENDERIZADO vira snapshot em `marketing_postagens.legenda`. Nunca no
publicador: o que o operador leu no modal tem que ser byte a byte o que vai
pro Instagram, e mexer na biblioteca depois não pode reescrever post nenhum.
O clique manual e o robô automático passam os dois por aqui — o futuro "robô
que escolhe sozinho o aprovado" só precisa chamar a mesma função.

**Rodízio**: entre as variações elegíveis sai a menos usada recentemente
NAQUELA CONTA. Não há tabela de estado: quem responde "quando esse texto saiu
por último aqui" é `marketing_postagens.legenda_modelo_id` + o instante do
post. Mesmo texto + mesmo formato + API + intervalo cravado é exatamente o
retrato que o detector de automação do Instagram procura.

**Sandbox**: o Jinja é o de `services/email_marca.py` — o MESMO ambiente
(`SandboxedEnvironment` sem globals) e a MESMA allowlist de nós da AST. Não
existe um segundo sandbox neste repositório, e não deve existir: sandbox é
coisa que se audita, e auditar dois é auditar mal. O que muda aqui é só a
lista de NOMES aceitos, e por um motivo concreto: a legenda precisa de
`{{ instagram }}`, que o e-mail não tem, e não pode ter `{{ cliente }}` /
`{{ pedido }}`, que o e-mail tem — nome de cliente numa legenda pública do
Instagram é vazamento, não placeholder.

Nada de IA aqui. Post não se edita e IA inventa especificação de celular com
cara de verdade (bateria, memória, Anatel); geração de texto é offline, com
aprovação humana, virando biblioteca — que é justamente o que esta tabela é.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from jinja2 import TemplateError, nodes
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Marca,
    MarketingCreative,
    MarketingCreativeFile,
    MarketingLegendaModelo,
    MarketingPostagem,
    Product,
    RedeSocial,
)
from app.models.marketing_postagem import STATUS_EM_VOO, STATUS_PUBLICADO, STATUS_REVISAR
from app.services.email_marca import (
    _NODES_OK,
    FILTROS_PERMITIDOS,
    TemplateInvalidoError,
    _env,
    formatar_fone,
)

# Teto da legenda na Meta. A constante também existe em
# `services/marketing/postagens.py`: repetida de propósito, porque
# `postagens.agendar()` vai importar ESTE módulo e importar de volta fecharia
# o ciclo. Duas linhas com o mesmo 2200 custam menos que um import circular.
LEGENDA_MAX = 2200

# Placeholders da legenda (documentados na tela). `site` está na lista mas
# fica FORA dos textos por decisão do Eduardo (16/09/2026): nenhuma das duas
# marcas manda pra site. Existir na allowlist é o que permite ligar o
# placeholder depois sem migration nem deploy de código.
PLACEHOLDERS = (
    "marca",
    "produto",
    "produto_modelo",
    "whatsapp",
    "email_sac",
    "instagram",
    "site",
)

# `products.name` é nome de ERP, não de vitrine. Os 12 do acervo seguem uma
# forma só: "[marca] [fabricante] [modelo] [RAM.armazenamento] - [cor]", como
# em "Uranyx Fossibot F109S 24.256 - Preto". Publicar isso cru no Instagram é
# jogar código de estoque na cara do cliente.
#
# A limpeza tira só o que é redundante ou interno:
#   • o prefixo da MARCA, porque `{{ marca }}` já existe e "O Uranyx Uranyx
#     Fossibot…" é o que sairia;
#   • o par RAM.armazenamento, que vira "256 GB" — o número que a pessoa
#     reconhece. A RAM some do rótulo: quem compara RAM lê a ficha, não o Reel.
# O FABRICANTE FICA. A própria conta escreve "O Uranyx Oukitel WP60 une
# resistência militar…" — o nome do fabricante é parte de como a marca fala.
_RE_MEMORIA = re.compile(r"\b(\d{1,3})\.(\d{2,4})\b")

ORIGEM_MANUAL = "manual"
ORIGEM_CRIATIVO = "criativo"
ORIGEM_PRODUTO = "produto"
ORIGEM_MARCA = "marca"
ORIGEM_NENHUMA = "nenhuma"

# Postagem cujo texto CONTA como já usado no rodízio: as em voo, as
# publicadas e as em `revisar` (esta porque "revisar" quer dizer NÃO SABEMOS
# se saiu — tratar como vaga livre é o caminho pra repetir a legenda logo
# depois). `falhou`/`cancelado` não contam: ali está provado que não saiu.
# Mesmo conjunto do `STATUS_OCUPA_CONTA` de postagens.py, montado a partir do
# módulo de MODELOS para não importar o serviço (ver LEGENDA_MAX).
_STATUS_JA_USOU = (*STATUS_EM_VOO, STATUS_PUBLICADO, STATUS_REVISAR)

# "Quando esta postagem acontece (ou aconteceu)": publicado_em quando já saiu,
# senão a hora marcada, senão a criação ("publicar agora"). Mesma expressão do
# `_MOMENTO` de postagens.py, pelo mesmo motivo do import circular.
_MOMENTO = func.coalesce(
    MarketingPostagem.publicado_em,
    MarketingPostagem.agendado_para,
    MarketingPostagem.created_at,
)

# Sentinela do "nunca usada nesta conta". Vive só dentro da chave de ordenação
# (nunca-usada ganha), então nunca é comparada com um datetime naive.
_NUNCA = datetime.min.replace(tzinfo=UTC)

# Artigos, contrações e determinantes que carregam GÊNERO. Colados no
# `{{ produto }}` eles viram erro de concordância na cara do cliente:
# "uso do {{ produto }}" com o produto "Cafeteira" sai "uso do Cafeteira".
# A variação usa o produto como RÓTULO ("Cafeteira X500 — …"), nunca dentro
# de concordância, porque o nome do produto é livre e muda de gênero sem
# avisar. A lista é fechada de propósito: preposição sem gênero ("de", "com",
# "para", "em") passa, que é o jeito certo de escrever.
_DETERMINANTES_COM_GENERO = frozenset(
    {
        "o", "a", "os", "as",
        "um", "uma", "uns", "umas",
        "ao", "aos", "à", "às",
        "do", "da", "dos", "das",
        "no", "na", "nos", "nas",
        "pelo", "pela", "pelos", "pelas",
        "num", "numa", "nuns", "numas",
        "este", "esta", "estes", "estas",
        "esse", "essa", "esses", "essas",
        "aquele", "aquela", "aqueles", "aquelas",
        "deste", "desta", "desse", "dessa", "daquele", "daquela",
        "neste", "nesta", "nesse", "nessa", "naquele", "naquela",
        "todo", "toda", "todos", "todas",
        "meu", "minha", "seu", "sua", "nosso", "nossa",
        "outro", "outra", "mesmo", "mesma", "novo", "nova",
    }
)

# A palavra imediatamente antes de `{{ produto }}` (com ou sem espaço, com ou
# sem filtro depois do nome).
_RE_ANTES_DO_PRODUTO = re.compile(r"(\w+)\s*\{\{-?\s*produto\b", re.IGNORECASE)


def nome_de_vitrine(bruto: str | None, marca_nome: str | None) -> tuple[str, str]:
    """`products.name` → (rótulo completo, só o modelo).

    "Uranyx Fossibot F109S 24.256 - Preto" vira
    ("Fossibot F109S 256 GB Preto", "Fossibot F109S").

    Nome que não segue a forma esperada volta praticamente inteiro (só sem o
    prefixo da marca): é melhor uma legenda com o nome cru do que uma legenda
    com o nome pela metade — e nome de produto novo aparece sem avisar.
    """
    bruto = (bruto or "").strip()
    if not bruto:
        return "", ""
    corpo, _, cor = bruto.partition(" - ")
    corpo, cor = corpo.strip(), cor.strip()
    # Prefixo da marca fora: "{{ marca }} {{ produto }}" é a construção que a
    # conta usa, e repetir o nome ali é o erro mais visível de todos.
    prefixo = (marca_nome or "").strip()
    if prefixo and corpo.lower().startswith(prefixo.lower()):
        corpo = corpo[len(prefixo):].strip()
    memoria = ""
    achou = _RE_MEMORIA.search(corpo)
    if achou:
        memoria = f"{achou.group(2)} GB"
        corpo = (corpo[: achou.start()] + corpo[achou.end():]).strip()
    modelo = " ".join(corpo.split())
    completo = " ".join(x for x in (modelo, memoria, cor) if x)
    return completo, modelo


@dataclass(frozen=True)
class LegendaResolvida:
    """O que a cascata decidiu, pronto pra gravar e pra rotular na tela.

    `indice`/`total_variacoes` alimentam o rótulo do modal ("padrão da marca ·
    variação 2 de 4"): sem eles o operador não tem como saber que existe mais
    de um texto, nem qual saiu — e o rodízio vira mágica invisível.
    """

    texto: str | None
    origem: str
    modelo_id: UUID | None = None
    total_variacoes: int = 0
    indice: int | None = None


_NENHUMA = LegendaResolvida(texto=None, origem=ORIGEM_NENHUMA)


# ──────────────────────────────────────────────────────── contexto e render


def placeholders_de(
    marca: Marca | None,
    produto_nome: str | None,
    rede: RedeSocial | None,
) -> dict[str, str]:
    """Contexto do render, num lugar só.

    Todo placeholder sai como string — nunca `None`: `{{ whatsapp }}` de marca
    sem telefone tem que virar vazio, e não a palavra "None" no meio do Reel.
    O telefone passa pelo `formatar_fone` do e-mail (mesma marca, mesmo
    número, mesmo formato nos dois canais).
    """
    completo, modelo = nome_de_vitrine(produto_nome, marca.nome if marca else None)
    return {
        "marca": (marca.nome if marca else "") or "",
        "produto": completo,
        "produto_modelo": modelo,
        "whatsapp": formatar_fone(marca.sac_fone) if marca else "",
        "email_sac": (marca.sac_email if marca else "") or "",
        "instagram": (rede.conta if rede else "") or "",
        "site": (marca.site if marca else "") or "",
    }


def _checa_no(no: nodes.Node) -> None:
    """Allowlist da AST: texto literal, `{{ placeholder }}` e
    `{{ placeholder|filtro }}`. Mesmo desenho do `_checa_no` de
    email_marca.py — inclusive o motivo de ser allowlist e não blacklist: o
    sandbox do Jinja sozinho NÃO barra `{{ 'x' * 10**9 }}` (aloca antes de
    qualquer teto) nem `{{ 2 ** (2 ** 31) }}` (trava o event loop). Sem nó de
    constante, chamada, atributo ou índice, nada disso chega a existir.
    """
    if isinstance(no, _NODES_OK):
        for filho in no.iter_child_nodes():
            _checa_no(filho)
        return
    if isinstance(no, nodes.Name):
        if no.name not in PLACEHOLDERS:
            raise TemplateInvalidoError(f"placeholder_desconhecido: {no.name}")
        return
    if isinstance(no, nodes.Filter):
        proibido = no.args or no.kwargs or no.dyn_args or no.dyn_kwargs
        if no.name not in FILTROS_PERMITIDOS or proibido:
            raise TemplateInvalidoError(f"filtro_nao_permitido: {no.name}")
        _checa_no(no.node)
        return
    raise TemplateInvalidoError(f"template_bloco_nao_permitido: {type(no).__name__}")


def _checa_template(texto: str) -> None:
    if "{%" in texto:
        raise TemplateInvalidoError("template_bloco_nao_permitido")
    try:
        arvore = _env.parse(texto)
    except TemplateError as e:
        raise TemplateInvalidoError(f"template_invalido: {e}") from e
    _checa_no(arvore)


def artigo_colado(texto: str) -> str | None:
    """O determinante com gênero grudado no `{{ produto }}`, ou None.

    Devolve a palavra (não um bool) porque a mensagem de erro que serve é a
    que diz QUAL: "tire o 'do' antes de {{ produto }}".
    """
    for achado in _RE_ANTES_DO_PRODUTO.finditer(texto or ""):
        palavra = achado.group(1).lower()
        if palavra in _DETERMINANTES_COM_GENERO:
            return palavra
    return None


def validar_modelo(texto: str) -> None:
    """Valida a variação na hora de SALVAR (schema/router de peça C).

    É mais rígida que o render de propósito, e a diferença é toda a
    `artigo_colado`: concordância é regra de ESCRITA, não de segurança. Vale
    barrar quem está digitando — e não vale recusar uma postagem por causa de
    uma preposição numa linha que já estava salva. Reel sem legenda é criativo
    queimado; Reel com "uso do Cafeteira" é só feio.
    """
    if len(texto or "") > LEGENDA_MAX:
        raise TemplateInvalidoError("legenda_muito_longa")
    _checa_template(texto or "")
    palavra = artigo_colado(texto or "")
    if palavra:
        raise TemplateInvalidoError(f"artigo_colado_no_produto: {palavra}")


def renderizar(texto: str, contexto: dict[str, str]) -> str:
    """Template → texto final da legenda, já cortado em 2200.

    O corte é seco, no caractere 2200, mesmo no meio da palavra: quem enxerga
    o resultado é o operador, no modal, ANTES de publicar — cortar no último
    espaço deixaria o texto do modal diferente do texto gravado, e é esse
    "byte a byte" que a feature inteira promete.
    """
    _checa_template(texto or "")
    try:
        saida = _env.from_string(texto or "").render(**contexto)
    except Exception as e:  # OverflowError/ValueError também são recusa, nunca 500
        raise TemplateInvalidoError(f"template_invalido: {type(e).__name__}") from e
    return saida.strip()[:LEGENDA_MAX]


# ───────────────────────────────────────────────────────────────── rodízio


async def _ultimo_uso(
    session: AsyncSession,
    rede: RedeSocial | None,
    ids: list[UUID],
) -> dict[UUID, datetime]:
    """{modelo_id: última vez que esse texto saiu NESTA conta}.

    Por conta (`rede_social_id`), não por marca: a mesma variação publicada no
    @perfil_A não é repetição nenhuma no @perfil_B — são públicos diferentes,
    e é o feed de cada conta que fica repetitivo.
    """
    if rede is None or not ids:
        return {}
    linhas = await session.execute(
        select(MarketingPostagem.legenda_modelo_id, func.max(_MOMENTO))
        .where(
            MarketingPostagem.rede_social_id == rede.id,
            MarketingPostagem.legenda_modelo_id.in_(ids),
            MarketingPostagem.status.in_(_STATUS_JA_USOU),
        )
        .group_by(MarketingPostagem.legenda_modelo_id)
    )
    return {mid: quando for mid, quando in linhas.all() if mid is not None}


def _chave_rodizio(
    modelo: MarketingLegendaModelo,
    usos: dict[UUID, datetime],
) -> tuple[int, datetime, str]:
    """Ordem do rodízio: nunca-usada primeiro, depois a mais antiga; empate
    desempata por `id`. O `id` no fim não é enfeite: sem ele, duas variações
    novas (as duas sem uso) ficariam na ordem que o Postgres devolvesse, e o
    teste do rodízio passaria a depender de sorte."""
    quando = usos.get(modelo.id)
    if quando is None:
        return (0, _NUNCA, str(modelo.id))
    return (1, quando, str(modelo.id))


async def _variacao(
    session: AsyncSession,
    *,
    marca_id: UUID,
    product_id: UUID | None,
    rede: RedeSocial | None,
) -> tuple[MarketingLegendaModelo, int, int] | None:
    """A variação escolhida + (quantas elegíveis, posição da escolhida).

    A posição é na lista ordenada por `id`, não na ordem do rodízio: é rótulo
    de tela ("variação 2 de 4") e precisa ser a MESMA toda vez que alguém abre
    o modal, senão a "variação 2" de hoje é a "variação 3" de amanhã.
    """
    q = select(MarketingLegendaModelo).where(
        MarketingLegendaModelo.marca_id == marca_id,
        MarketingLegendaModelo.ativo.is_(True),
    )
    q = q.where(
        MarketingLegendaModelo.product_id == product_id
        if product_id is not None
        else MarketingLegendaModelo.product_id.is_(None)
    )
    variacoes = list((await session.execute(q.order_by(MarketingLegendaModelo.id))).scalars().all())
    if not variacoes:
        return None
    usos = await _ultimo_uso(session, rede, [v.id for v in variacoes])
    escolhida = min(variacoes, key=lambda v: _chave_rodizio(v, usos))
    return escolhida, len(variacoes), variacoes.index(escolhida) + 1


# ───────────────────────────────────────────────────────────────── cascata


async def _marca_de(
    session: AsyncSession,
    creative: MarketingCreative | None,
    rede: RedeSocial | None,
) -> Marca | None:
    """A marca dona das variações. `creative.marca_id` manda; a conta é o
    plano B, pra criativo antigo que só tem o texto livre em `marca` — quem
    vai receber o post é a conta, e ela é de uma marca só."""
    marca_id = (creative.marca_id if creative else None) or (rede.marca_id if rede else None)
    if marca_id is None:
        return None
    return await session.get(Marca, marca_id)


async def _produto_nome(session: AsyncSession, creative: MarketingCreative | None) -> str:
    """Nome do produto vinculado, ou vazio. Vazio NÃO trava nada: a cascata
    cai pro padrão da marca — só 2 dos 41 criativos de produção têm SKU que
    casa exato, então o degrau da marca é o caminho comum, não a exceção."""
    product_id = creative.product_id if creative else None
    if product_id is None:
        return ""
    produto = await session.get(Product, product_id)
    return ((produto.name if produto else "") or "").strip()


async def _da_biblioteca(
    session: AsyncSession,
    *,
    creative: MarketingCreative | None,
    rede: RedeSocial | None,
    contexto: dict[str, str],
    marca: Marca | None,
) -> LegendaResolvida:
    """Os três degraus que não vêm de fora: criativo → produto → marca."""
    do_criativo = ((creative.legenda if creative else "") or "").strip()
    if do_criativo:
        # Override por vídeo: uma variação só, sem rodízio e sem modelo_id —
        # não é uma linha da biblioteca, é o texto daquele criativo.
        return LegendaResolvida(
            texto=renderizar(do_criativo, contexto),
            origem=ORIGEM_CRIATIVO,
            total_variacoes=1,
            indice=1,
        )
    if marca is None:
        return _NENHUMA
    product_id = creative.product_id if creative else None
    # Produto SEM NOME não sustenta o degrau do produto, mesmo com a FK
    # preenchida: a variação de produto existe justamente para dizer o nome
    # dele, e `{{ produto }}` vazio publicaria "Conheça a  — 2 anos de
    # garantia" no feed. O padrão da marca, um degrau abaixo, é genérico mas
    # está inteiro — e legenda não se edita depois de publicada.
    if product_id is not None and contexto["produto"]:
        escolha = await _variacao(session, marca_id=marca.id, product_id=product_id, rede=rede)
        if escolha is not None:
            return _resolvida(escolha, ORIGEM_PRODUTO, contexto)
    escolha = await _variacao(session, marca_id=marca.id, product_id=None, rede=rede)
    if escolha is not None:
        return _resolvida(escolha, ORIGEM_MARCA, contexto)
    return _NENHUMA


def _resolvida(
    escolha: tuple[MarketingLegendaModelo, int, int],
    origem: str,
    contexto: dict[str, str],
) -> LegendaResolvida:
    modelo, total, indice = escolha
    return LegendaResolvida(
        texto=renderizar(modelo.texto, contexto),
        origem=origem,
        modelo_id=modelo.id,
        total_variacoes=total,
        indice=indice,
    )


async def resolver(
    session: AsyncSession,
    *,
    creative: MarketingCreative | None,
    file: MarketingCreativeFile | None,
    rede: RedeSocial | None,
    legenda_manual: str | None = None,
) -> LegendaResolvida:
    """Resolve a legenda desta postagem: cascata + rodízio + render.

    `legenda_manual` é o que o operador mandou do modal e NÃO passa pelo
    Jinja: é o texto final, digitado por uma pessoa que já leu o resultado na
    tela. Rodar template em cima dele mudaria os bytes e explodiria num `{{`
    perdido no meio de uma frase.

    O detalhe que não é óbvio: a cascata roda MESMO com `legenda_manual`
    preenchido, e quando o texto bate byte a byte com o que ela produziria, a
    resposta é a da cascata (com `modelo_id`), não "manual". É o caminho
    normal — o modal chega pré-preenchido e quase ninguém edita — e sem isso
    TODA postagem gravaria `legenda_modelo_id` NULL, o rodízio nunca giraria e
    a conta publicaria o mesmo texto pra sempre. "Manual" passa a significar o
    que o nome promete: alguém escreveu OUTRA coisa.

    `file` entra na assinatura porque a borda resolve por (criativo, arquivo,
    conta) e o override de legenda pode descer pro arquivo depois; hoje a
    cascata não olha pro arquivo — a legenda é do criativo.

    Levanta `TemplateInvalidoError` quando a variação escolhida não renderiza.
    Recusar a postagem é o certo: publicar não tem desfazer, e uma legenda
    meio renderizada no Instagram não se conserta editando.
    """
    manual = (legenda_manual or "").strip()
    marca = await _marca_de(session, creative, rede)
    contexto = placeholders_de(marca, await _produto_nome(session, creative), rede)
    try:
        da_biblioteca = await _da_biblioteca(
            session, creative=creative, rede=rede, contexto=contexto, marca=marca
        )
    except TemplateInvalidoError:
        # Variação quebrada na biblioteca NÃO pode derrubar quem escreveu a
        # legenda à mão: o texto manual não passa pelo Jinja, então o defeito
        # de outra linha é irrelevante pra ele. Sem este `except`, um
        # `{{ produtos }}` errado numa variação travaria toda postagem daquela
        # marca, inclusive as que nem usam a biblioteca.
        if manual:
            return LegendaResolvida(texto=manual[:LEGENDA_MAX], origem=ORIGEM_MANUAL)
        raise
    if manual and manual != da_biblioteca.texto:
        return LegendaResolvida(texto=manual[:LEGENDA_MAX], origem=ORIGEM_MANUAL)
    return da_biblioteca
