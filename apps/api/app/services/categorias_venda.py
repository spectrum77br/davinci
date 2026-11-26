"""Categorias de venda (M-75, Harry 25/11/2026): os grupos de margem/frete da aba Contas.

NÃO é um cadastro novo: são os segmentos FILHOS das raízes de Cadastros › Segmentos (tabela
`segments`), mostrados e editados também em Cadastros › Produtos. A raiz é o "centro de custo"
(Celular, Eletro, Mala; Catálogo só aparece na lista) e o filho é a categoria de venda.
M-76: o menu de centros de custo vem de Cadastros › Centro de custo (tabela centros_custo, cujos
fixos apontam para essas raízes); centro novo, sem raiz, ainda não recebe categoria de venda.

O que liga o produto à margem/frete da conta é a POSIÇÃO do filho: sort_order 0..4 →
margin1..5 / shipping1..5 (routers/pricing._segment_index, services/pricing/push,
services/nf_frete_auto). E a aba Contas desenha os cabeçalhos pela ordem dos filhos ATIVOS
(pages/pricing/[tab].vue, TYPE_HEADERS): o N-ésimo ativo fica sobre margin{N}.

Por isso as regras abaixo, que seguram as posições (valem aqui e em Cadastros › Segmentos):
- a nova entra no FIM do centro de custo (maior sort_order + 1 — antes gravava 0, a mesma
  posição da primeira); com 5 (contando as inativas) é recusada: só existem 5 pares
  margem/frete. Também é recusada se a última do centro estiver inativa (a nova ficaria ativa
  depois de uma inativa e os cabeçalhos da aba Contas sairiam do lugar);
- ao nascer na posição N, as contas daquele centro com o vínculo slot{N} vazio passam a apontar
  para ela (a mesma regra de routers/pricing._default_slot_segments): sem isso a página Margem
  ficaria sem o frete "Projetado" dos produtos dela;
- excluir pela aba Segmentos: só a ÚLTIMA do centro de custo — nenhuma posição é renumerada,
  então nenhuma outra muda de coluna e nenhuma margem/frete das contas é mexida (a aba Segmentos
  não é só do administrador, e margem/frete das contas são só dele — M-10);
- excluir pela lixeira de Cadastros › Produtos (só administrador, M-91, Harry 26/11/2026):
  qualquer uma, inclusive do meio. Na mesma transação as categorias das posições seguintes sobem
  uma posição e, em TODAS as contas do centro (kit, as duas linhas do par do ML e a coluna de
  catálogo, cujas margens próprias são o `margens_catalogo`), margem, frete e vínculo slot de
  cada posição seguinte andam junto (N+1 → N …); a última posição ocupada fica vazia e a
  margem/frete da excluída somem. Assim cada categoria que fica guarda, em cada conta, a mesma
  margem e o mesmo frete, e o preço de quem não foi transferido não muda (ver colunas_ao_excluir);
- em qualquer das duas telas, o uso é conferido só na tabela dela (M-90, Harry 26/11/2026): produtos da
  Tabela de Preços (`pricing_products.segment_id`) e contas apontando DIRETO para ela
  (`pricing_accounts.segment_id`). Em uso, a exclusão pede o DESTINO (outra categoria de venda
  ATIVA do mesmo centro): produtos e contas passam para ela e a categoria sai, na mesma
  transação. Os produtos da tela Produtos (`products.segment_id`, sobra da carga antiga) não
  travam: vão junto para o destino ou, sem destino, ficam sem segmento (NULL — o mesmo que o
  banco já faria, FK ON DELETE SET NULL). Os vínculos automáticos slot1..5 NÃO contam como uso
  (toda conta aponta para todas as categorias do seu centro): são limpos na mesma transação;
- desativar: só a última ATIVA; reativar: só se as de antes estiverem ativas. Assim as ativas
  ficam sempre nas primeiras posições e nenhum cabeçalho escorrega de coluna;
- a posição e o centro de custo de uma categoria não mudam depois de criada (PATCH de
  sort_order/parent_id recusado), pelo mesmo motivo;
- "Apple" e "Queima de estoque" são procuradas pelo NOME em outras partes do sistema
  (portal_criativos, sites_custos, sites_midia, a view vw_bling_pedidos): não trocam de nome, e
  nenhuma outra passa a se chamar assim.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import CentroCusto, PricingAccount, PricingProduct, Product, Segment
from app.models.pricing import CANAL_KIT
from app.schemas.segments import _slugify
from app.services import centros_custo
from app.services.categorias_ordem import posicao_categoria
from app.services.produto_cadastros import ordem

# O tipo "Categoria de venda" da tela Cadastros › Produtos não tem linha em
# produto_cadastro_tipos (as linhas são os segmentos): id fixo só para a tela.
TIPO_ID = UUID("7d3c6a10-75a0-4c1e-9b0e-ca7e90c1a75e")
TIPO_NOME = "Categoria de venda"
CHAVE = "categoria_venda"

# Só existem 5 pares margem/frete por conta (margin1..5 / shipping1..5).
LIMITE = 5

# Procuradas pelo nome (lower(name) = 'apple' / 'queima de estoque') fora daqui.
NOMES_PROCURADOS = ("Apple", "Queima de estoque")

# M-76: centro de custo sem raiz em Cadastros › Segmentos (todo centro novo) ainda não recebe
# categoria de venda — ver centro_para_nova_categoria.
MSG_SO_FIXOS = "Categoria de venda só em Celular, Eletro e Mala por enquanto."

_SLOTS = (
    PricingAccount.slot1_segment_id,
    PricingAccount.slot2_segment_id,
    PricingAccount.slot3_segment_id,
    PricingAccount.slot4_segment_id,
    PricingAccount.slot5_segment_id,
)

# M-91: o que a conta guarda pela POSIÇÃO da categoria (0..4 → margin1..5 / shipping1..5 /
# slot1..5_segment_id). Na coluna de catálogo, margin1..5 são as margens próprias
# (`margens_catalogo`). Excluir uma do meio anda os três juntos.
_POR_POSICAO = tuple(
    tuple(f"{campo}{n}{fim}" for n in range(1, LIMITE + 1))
    for campo, fim in (("margin", ""), ("shipping", ""), ("slot", "_segment_id"))
)


def _chave(nome: str | None) -> str:
    return " ".join((nome or "").split()).casefold()


def nome_procurado(nome: str | None) -> str | None:
    """O nome fixo ("Apple" / "Queima de estoque") se `nome` for um deles (sem olhar maiúsculas)."""
    k = _chave(nome)
    return next((n for n in NOMES_PROCURADOS if n.casefold() == k), None)


def _recusa(code: str, mensagem: str, **extra: object) -> HTTPException:
    return HTTPException(
        status.HTTP_409_CONFLICT, detail={"code": code, "mensagem": mensagem, **extra}
    )


def _qtd(n: int, um: str, varios: str) -> str:
    return f"1 {um}" if n == 1 else f"{n:,} {varios}".replace(",", ".")


def ordem_centro(raiz: Segment) -> tuple[int, tuple[str, str]]:
    """Celular, Eletro, Mala (M-23) e o resto (Catálogo…) depois, por nome."""
    return (posicao_categoria(raiz.slug), ordem(raiz.name))


# ---------------------------------------------------------------- leitura


async def raiz_de(session: AsyncSession, seg: Segment) -> Segment | None:
    """O centro de custo (a raiz) se `seg` é filho direto de uma raiz; senão None."""
    if seg.parent_id is None:
        return None
    pai = await session.get(Segment, seg.parent_id)
    if pai is None or pai.parent_id is not None:
        return None
    return pai


async def travar_centro(session: AsyncSession, raiz_id: UUID) -> None:
    """Trava a raiz do centro até o fim da transação: duas pessoas criando, excluindo ou
    (des)ativando categorias do MESMO centro ao mesmo tempo esperam uma pela outra, e a posição
    (que escolhe a margem/frete) nunca fica repetida nem com buraco."""
    await session.execute(select(Segment.id).where(Segment.id == raiz_id).with_for_update())


async def filhas(session: AsyncSession, raiz_id: UUID) -> list[Segment]:
    """As categorias de venda do centro de custo, na ordem das colunas (inativas também)."""
    return list(
        (
            await session.execute(
                select(Segment)
                .where(Segment.parent_id == raiz_id)
                .order_by(Segment.sort_order, Segment.name)
            )
        ).scalars()
    )


async def listar(session: AsyncSession) -> list[tuple[Segment, Segment]]:
    """[(categoria de venda, centro de custo)]: centros em Celular, Eletro, Mala e o resto
    depois; dentro de cada centro, na ordem das colunas de margem/frete."""
    q = select(Segment).order_by(Segment.sort_order, Segment.name)
    linhas = list((await session.execute(q)).scalars())
    raizes = {s.id: s for s in linhas if s.parent_id is None}
    vendas = [(s, raizes[s.parent_id]) for s in linhas if s.parent_id in raizes]
    vendas.sort(key=lambda par: (ordem_centro(par[1]), par[0].sort_order, ordem(par[0].name)))
    return vendas


async def centros_do_menu(session: AsyncSession) -> list[tuple[CentroCusto, Segment | None]]:
    """O ÚNICO lugar que monta a lista de centros de custo onde se inclui categoria de venda
    (o menu do "Novo cadastro" e a conferência do servidor). M-76: é a lista de
    Cadastros › Centro de custo, inteira — Celular, Eletro e Mala (com a raiz) e os centros novos
    (sem raiz: aparecem, mas centro_para_nova_categoria recusa). O Catálogo não é centro de custo
    e não entra."""
    return await centros_custo.listar(session)


async def centro_para_nova_categoria(
    session: AsyncSession, centro_id: UUID
) -> tuple[CentroCusto, Segment]:
    """O centro de custo escolhido no menu e a raiz dele.

    `centro_id` é o id do centro (Cadastros › Centro de custo); o id da raiz também é aceito
    (a tela da M-75, antes da M-76, mandava o id do segmento-raiz).

    Centro SEM raiz → recusa (MSG_SO_FIXOS). Criar a 1ª categoria dele exigiria uma raiz nova em
    `segments`, e uma raiz — mesmo criada desativada — aparece: na aba Segmentos (onde quem
    reativa vira botão na Tabela de Preços) e, pelas categorias dela (ativas), no menu de
    segmento e no filtro da tela Produtos, onde escolher uma faria o produto sair do departamento
    na tela e perder a DUIMP/mensagem da nota (o segmento do produto vence a Tabela de Preços em
    nf_emissao_gerar._texto_segmento_por_sku). Mais seguro: só Celular, Eletro e Mala por
    enquanto."""
    par = next(
        (
            (c, r)
            for c, r in await centros_do_menu(session)
            if c.id == centro_id or (c.segment_id is not None and c.segment_id == centro_id)
        ),
        None,
    )
    if par is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail={"code": "centro_nao_encontrado", "mensagem": "Centro de custo não encontrado."},
        )
    centro, raiz = par
    if raiz is None or raiz.parent_id is not None:
        raise _recusa("categoria_venda_centro_sem_raiz", MSG_SO_FIXOS)
    return centro, raiz


# ---------------------------------------------------------------- incluir


async def sem_repetir_no_centro(
    session: AsyncSession, raiz: Segment, nome: str, menos: UUID | None = None
) -> None:
    """O mesmo nome pode repetir em centros diferentes (Acessórios no Celular e na Mala), não
    no mesmo."""
    for s in await filhas(session, raiz.id):
        if s.id != menos and _chave(s.name) == _chave(nome):
            raise _recusa("nome_repetido", f"Já existe “{s.name}” em {raiz.name}.")


async def conferir_nova(session: AsyncSession, raiz: Segment, nome: str) -> int:
    """Confere a nova categoria de venda e devolve a posição dela (sort_order): o fim do centro."""
    fixo = nome_procurado(nome)
    if fixo:
        raise _recusa(
            "categoria_venda_nome_reservado",
            f"“{fixo}” é um nome que outras partes do sistema procuram; escolha outro nome.",
        )
    await travar_centro(session, raiz.id)
    await sem_repetir_no_centro(session, raiz, nome)
    atuais = await filhas(session, raiz.id)
    if len(atuais) >= LIMITE:
        raise _recusa(
            "categoria_venda_limite",
            f"Este centro de custo já tem {LIMITE} categorias de venda.",
        )
    posicao = max((s.sort_order for s in atuais), default=-1) + 1
    if posicao >= LIMITE:
        raise _recusa(
            "categoria_venda_limite",
            f"As {LIMITE} colunas de margem/frete de {raiz.name} já estão ocupadas.",
        )
    if atuais and not atuais[-1].active:
        raise _recusa(
            "categoria_venda_depois_de_inativa",
            f"A última categoria de venda de {raiz.name} (“{atuais[-1].name}”) está inativa: a "
            "nova ficaria depois dela e os nomes das colunas da aba Contas sairiam do lugar. "
            "Reative ou exclua a inativa antes.",
        )
    return posicao


async def slug_livre(session: AsyncSession, raiz: Segment, nome: str) -> str:
    """O slug do nome; se outra categoria do centro já tem esse slug (ex.: uma renomeada, que
    guarda o slug antigo), acrescenta -2, -3… O slug da que existe não muda (a Importação acha
    as da Mala pelo slug)."""
    base = _slugify(nome)
    if not base:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "nome_sem_letras", "mensagem": "O nome precisa ter letras ou números."},
        )
    usados = set(
        (await session.execute(select(Segment.slug).where(Segment.parent_id == raiz.id))).scalars()
    )
    slug, n = base[:128], 2
    while slug in usados:
        sufixo = f"-{n}"
        slug, n = base[: 128 - len(sufixo)] + sufixo, n + 1
    return slug


async def preencher_slots(session: AsyncSession, raiz: Segment, seg: Segment) -> int:
    """A nova categoria na posição N vira o vínculo slot{N} das contas daquele centro que estão
    com ele VAZIO (as que apontam para outra categoria ficam como estão). Mesma regra de
    routers/pricing._default_slot_segments. Devolve quantas contas mudaram."""
    n = int(seg.sort_order)
    if not 0 <= n < LIMITE:
        return 0
    await session.flush()  # o id da nova
    coluna = _SLOTS[n]
    r = await session.execute(
        update(PricingAccount)
        .where(PricingAccount.segment_id == raiz.id, coluna.is_(None))
        .values({coluna.key: seg.id})
        .execution_options(synchronize_session=False)
    )
    return int(r.rowcount or 0)


# ---------------------------------------------------------------- renomear e ativar


def conferir_troca_de_nome(antigo: str, novo: str) -> None:
    """ "Apple" e "Queima de estoque" não trocam de nome, e nenhuma outra passa a se chamar assim.
    Só maiúsculas/espaços ("apple" → "Apple") passa: a busca não olha maiúsculas."""
    if _chave(antigo) == _chave(novo):
        return
    fixo = nome_procurado(antigo)
    if fixo:
        raise _recusa(
            "categoria_venda_nome_fixo",
            f"“{antigo}” não pode trocar de nome: outras partes do sistema procuram esta "
            f"categoria pelo nome “{fixo}”.",
        )
    fixo = nome_procurado(novo)
    if fixo:
        raise _recusa(
            "categoria_venda_nome_reservado",
            f"“{fixo}” é um nome que outras partes do sistema procuram; escolha outro nome.",
        )


async def conferir_ativacao(
    session: AsyncSession, seg: Segment, raiz: Segment, ativa: bool
) -> None:
    """Desativar só a última ativa; reativar só com as de antes ativas (as ativas ficam sempre
    nas primeiras posições e os cabeçalhos da aba Contas não escorregam)."""
    await travar_centro(session, raiz.id)
    irmas = [s for s in await filhas(session, raiz.id) if s.id != seg.id]
    if not ativa:
        depois = [s for s in irmas if s.sort_order > seg.sort_order and s.active]
        if depois:
            raise _recusa(
                "categoria_venda_desativar_do_meio",
                f"Só dá para desativar a última categoria de venda ativa de {raiz.name} "
                f"(“{depois[-1].name}”): desativar “{seg.name}” faria os nomes das colunas de "
                "margem/frete da aba Contas saírem do lugar.",
            )
    else:
        antes = [s for s in irmas if s.sort_order < seg.sort_order and not s.active]
        if antes:
            raise _recusa(
                "categoria_venda_reativar_depois_de_inativa",
                f"Reative antes “{antes[0].name}”: “{seg.name}” vem depois dela em {raiz.name} "
                "e ficaria na coluna de margem/frete errada na aba Contas.",
            )


def recusar_mudanca_de_lugar() -> HTTPException:
    return _recusa(
        "categoria_venda_posicao_fixa",
        "A posição e o centro de custo de uma categoria de venda não mudam: as colunas de "
        "margem/frete da aba Contas sairiam do lugar.",
    )


# ---------------------------------------------------------------- excluir


@dataclass
class Uso:
    produtos_tabela: int
    # products.segment_id (a primeira tela Produtos): M-90 — não trava a exclusão.
    produtos_cadastro: int
    contas: int
    subsegmentos: int

    @property
    def produtos(self) -> int:
        """M-90: só os da tabela dela (Tabela de Preços)."""
        return self.produtos_tabela

    @property
    def em_uso(self) -> bool:
        """Em uso NA TABELA DELA (Tabela de Preços e contas): pede o destino ao excluir."""
        return bool(self.produtos_tabela or self.contas)


async def contar_uso(session: AsyncSession, seg_id: UUID) -> Uso:
    """Quem aponta para a categoria: produtos (Tabela de Preços e tela Produtos) e contas que
    apontam direto para ela. Os vínculos slot1..5 não contam (são limpos ao excluir)."""

    async def conta(q) -> int:
        return int((await session.execute(q)).scalar_one() or 0)

    return Uso(
        produtos_tabela=await conta(
            select(func.count(PricingProduct.id)).where(PricingProduct.segment_id == seg_id)
        ),
        produtos_cadastro=await conta(
            select(func.count(Product.id)).where(Product.segment_id == seg_id)
        ),
        contas=await conta(
            select(func.count(PricingAccount.id)).where(PricingAccount.segment_id == seg_id)
        ),
        subsegmentos=await conta(select(func.count(Segment.id)).where(Segment.parent_id == seg_id)),
    )


@dataclass
class Bloqueio:
    code: str
    mensagem: str
    produtos: int
    contas: int


def _texto_uso(uso: Uso) -> str:
    """ "2 produtos da Tabela de Preços e 1 conta" (só as partes que existem)."""
    partes: list[str] = []
    if uso.produtos_tabela:
        partes.append(
            _qtd(
                uso.produtos_tabela,
                "produto da Tabela de Preços",
                "produtos da Tabela de Preços",
            )
        )
    if uso.contas:
        partes.append(_qtd(uso.contas, "conta", "contas"))
    return " e ".join(partes)


def mensagem_em_uso(seg: Segment, raiz: Segment, uso: Uso) -> str:
    """O quadro vermelho da lixeira quando a categoria está em uso na tabela dela (M-90)."""
    if uso.produtos_tabela + uso.contas == 1:
        quem = "ele vai" if uso.produtos_tabela else "ela vai"
    else:
        quem = "eles vão"
    return (
        f"“{seg.name}” ({raiz.name}) está em uso: {_texto_uso(uso)}. Escolha para qual "
        f"categoria de venda {quem} antes de excluir."
    )


def avisos_exclusao(seg: Segment, uso: Uso) -> list[str]:
    """Os avisos da confirmação SEM destino (M-90): o nome reservado e os produtos da tela
    Produtos que ficam sem segmento. Com destino a tela diz que eles "vão junto"."""
    avisos: list[str] = []
    if nome_procurado(seg.name):
        avisos.append(
            f"“{seg.name}” é um nome reservado: depois de excluída, não dá para criar de novo "
            "pela tela."
        )
    if uso.produtos_cadastro and not uso.em_uso:
        n = uso.produtos_cadastro
        avisos.append(
            _qtd(n, "produto da tela Produtos", "produtos da tela Produtos")
            + (" fica sem segmento." if n == 1 else " ficam sem segmento.")
        )
    return avisos


async def destinos(session: AsyncSession, seg: Segment, raiz: Segment) -> list[Segment]:
    """Para onde os produtos e as contas podem ir (M-90): as outras categorias de venda ATIVAS
    do mesmo centro de custo (a mesma tabela da aba Contas), na ordem das colunas."""
    return [s for s in await filhas(session, raiz.id) if s.id != seg.id and s.active]


@dataclass
class Colunas:
    """M-91: o que acontece com as colunas de margem/frete da aba Contas ao excluir pela lixeira
    de Cadastros › Produtos. `posicao` = a da excluída (0..4); `ultima` = a última posição
    ocupada do centro (contando a excluída); `sobem` = as categorias que andam uma posição, na
    ordem das colunas (vazia quando a excluída é a última)."""

    posicao: int
    ultima: int
    sobem: list[Segment]


async def colunas_ao_excluir(
    session: AsyncSession, seg: Segment, raiz: Segment
) -> Colunas | None:
    """As colunas que andam ao excluir `seg` (M-91). None = nenhuma coluna é mexida, como antes:
    posição fora de 0..4 ou outra categoria do centro na MESMA posição (dados antigos, de quando a
    nova nascia na posição 0) — aí a coluna continua sendo da outra e nada pode andar."""
    pos = int(seg.sort_order)
    irmas = [s for s in await filhas(session, raiz.id) if s.id != seg.id]
    if not 0 <= pos < LIMITE:
        return None
    if any(not 0 <= int(s.sort_order) < LIMITE or int(s.sort_order) == pos for s in irmas):
        return None
    sobem = [s for s in irmas if s.sort_order > pos]
    ultima = max([pos, *(int(s.sort_order) for s in sobem)])
    return Colunas(posicao=pos, ultima=ultima, sobem=sobem)


async def contas_do_centro(
    session: AsyncSession, raiz: Segment, categorias: list[Segment]
) -> list[PricingAccount]:
    """Todas as linhas de conta do centro de custo: as que apontam para a raiz (o normal — kit,
    as duas linhas do par do ML e a coluna de catálogo, que copia o `segment_id` da base) e as que
    apontam direto para uma categoria dele. Lidas de novo do banco (populate_existing): as
    transferências de excluir são UPDATE direto, que a sessão não enxerga."""
    ids = [raiz.id, *(s.id for s in categorias)]
    return list(
        (
            await session.execute(
                select(PricingAccount)
                .where(PricingAccount.segment_id.in_(ids))
                .execution_options(populate_existing=True)
            )
        ).scalars()
    )


def quantas_contas(contas: list[PricingAccount]) -> int:
    """Quantas contas a aba Contas mostra (o par do ML é uma linha; a coluna de catálogo vai
    junto da base)."""
    return len({c.par_id or c.id for c in contas if c.canal == CANAL_KIT})


def andar_colunas(conta: PricingAccount, colunas: Colunas) -> None:
    """Na conta, a margem, o frete e o vínculo slot de cada posição seguinte à excluída andam
    uma posição (N+1 → N, até a última ocupada) e a última ocupada fica vazia. Posições depois
    da última ocupada (sem categoria) não mudam."""
    for campos in _POR_POSICAO:
        for j in range(colunas.posicao, colunas.ultima):
            setattr(conta, campos[j], getattr(conta, campos[j + 1]))
        setattr(conta, campos[colunas.ultima], None)


async def previa_colunas(
    session: AsyncSession, seg: Segment, raiz: Segment
) -> tuple[list[str], int]:
    """Para a janela da lixeira (M-91): os nomes das categorias que andam uma posição e em
    quantas contas do centro a margem/frete da excluída são apagados."""
    colunas = await colunas_ao_excluir(session, seg, raiz)
    if colunas is None:
        return [], 0
    contas = await contas_do_centro(session, raiz, await filhas(session, raiz.id))
    return [s.name for s in colunas.sobem], quantas_contas(contas)


async def bloqueio_exclusao(
    session: AsyncSession, seg: Segment, raiz: Segment, *, pela_lixeira: bool = False
) -> tuple[Uso, Bloqueio | None]:
    """(uso, o porquê de não poder excluir — None = pode).

    M-90: estar em uso na tabela dela NÃO é bloqueio — a exclusão pede o destino (ver excluir).
    Só bloqueia quando não há destino possível (nenhuma outra ativa no centro).
    M-91: pela lixeira de Cadastros › Produtos (`pela_lixeira`) a do meio também sai (as
    seguintes andam uma posição); pela aba Segmentos continua só a última."""
    await travar_centro(session, raiz.id)
    uso = await contar_uso(session, seg.id)
    if uso.subsegmentos:
        return uso, Bloqueio(
            "categoria_venda_com_subsegmentos",
            f"“{seg.name}” ({raiz.name}) tem subsegmentos em Cadastros › Segmentos; tire-os antes.",
            0,
            0,
        )
    depois = [s for s in await filhas(session, raiz.id) if s.sort_order > seg.sort_order]
    if depois and not pela_lixeira:
        return uso, Bloqueio(
            "categoria_venda_do_meio",
            f"Pela aba Segmentos só dá para excluir a última categoria de venda de {raiz.name} "
            f"(“{depois[-1].name}”). Para excluir “{seg.name}”, use a lixeira de Cadastros › "
            "Produtos: lá as colunas seguintes da aba Contas andam uma posição, levando a margem "
            "e o frete de cada conta.",
            0,
            0,
        )
    if depois and await colunas_ao_excluir(session, seg, raiz) is None:
        # Dados antigos (duas categorias na mesma posição ou posição fora de 1..5): andar as
        # colunas trocaria a margem de alguém. Fica como antes da M-91.
        return uso, Bloqueio(
            "categoria_venda_do_meio",
            f"Só dá para excluir a última categoria de venda de {raiz.name} "
            f"(“{depois[-1].name}”): as posições das categorias de {raiz.name} estão "
            "repetidas ou fora das 5 colunas de margem/frete; fale com a equipe.",
            0,
            0,
        )
    if uso.em_uso and not await destinos(session, seg, raiz):
        return uso, Bloqueio(
            "categoria_venda_sem_destino",
            f"“{seg.name}” ({raiz.name}) está em uso: {_texto_uso(uso)}, e não há outra "
            f"categoria de venda ativa em {raiz.name} para recebê-los.",
            uso.produtos,
            uso.contas,
        )
    return uso, None


def recusa(b: Bloqueio) -> HTTPException:
    return _recusa(b.code, b.mensagem, produtos=b.produtos, contas=b.contas)


async def excluir(
    session: AsyncSession,
    seg: Segment,
    raiz: Segment,
    destino_id: UUID | None = None,
    *,
    pela_lixeira: bool = False,
) -> Segment | None:
    """Confere, transfere e exclui (sem commit — quem chama faz um commit só: se algo falhar,
    nada muda). Devolve o destino (None = sem transferência).

    M-90: em uso na tabela dela (Tabela de Preços / contas) exige `destino_id` — outra categoria
    de venda ativa do mesmo centro; sem ele, 409 `categoria_venda_em_uso` (a aba Segmentos, que
    não pergunta o destino, continua recusando). Os produtos da tela Produtos vão para o destino
    ou, sem destino, ficam sem segmento. Limpa os vínculos slot1..5 que apontam para ela na
    mesma transação — a FK já é ON DELETE SET NULL, mas assim não depende do banco.

    M-91 (`pela_lixeira`, Cadastros › Produtos): pode ser a do meio. As seguintes sobem uma
    posição e, em todas as contas do centro, margem/frete/vínculo de cada posição seguinte andam
    junto (andar_colunas); a margem/frete da excluída somem. Aba Segmentos: como antes."""
    uso, bloqueio = await bloqueio_exclusao(session, seg, raiz, pela_lixeira=pela_lixeira)
    if bloqueio is not None:
        raise recusa(bloqueio)
    # Antes de mexer em nada: as posições como estão agora.
    colunas = await colunas_ao_excluir(session, seg, raiz) if pela_lixeira else None
    destino: Segment | None = None
    if destino_id is not None:
        opcoes = await destinos(session, seg, raiz)
        destino = next((s for s in opcoes if s.id == destino_id), None)
        if destino is None:
            raise _recusa(
                "categoria_venda_destino_invalido",
                f"Escolha outra categoria de venda ativa de {raiz.name} para receber os "
                f"produtos e as contas de “{seg.name}”.",
            )
    elif uso.em_uso:
        # Só chega aqui sem destino pela aba Segmentos (ou se o uso mudou com a janela aberta).
        raise _recusa(
            "categoria_venda_em_uso",
            mensagem_em_uso(seg, raiz, uso)
            + " A transferência é feita pela lixeira de Cadastros › Produtos.",
            produtos=uso.produtos,
            contas=uso.contas,
        )
    if destino is not None:
        # Tabela de Preços e contas passam para o destino (mesmo centro: o `department` dos
        # produtos não muda).
        await session.execute(
            update(PricingProduct)
            .where(PricingProduct.segment_id == seg.id)
            .values(segment_id=destino.id)
            .execution_options(synchronize_session=False)
        )
        await session.execute(
            update(PricingAccount)
            .where(PricingAccount.segment_id == seg.id)
            .values(segment_id=destino.id)
            .execution_options(synchronize_session=False)
        )
    # Tela Produtos: vão junto para o destino ou ficam sem segmento.
    await session.execute(
        update(Product)
        .where(Product.segment_id == seg.id)
        .values(segment_id=destino.id if destino is not None else None)
        .execution_options(synchronize_session=False)
    )
    for coluna in _SLOTS:
        await session.execute(
            update(PricingAccount)
            .where(coluna == seg.id)
            .values({coluna.key: None})
            .execution_options(synchronize_session=False)
        )
    if colunas is not None:
        # M-91: as contas do centro (lidas DEPOIS das transferências e da limpeza acima) andam as
        # colunas; as categorias seguintes sobem uma posição. Mesma transação.
        irmas = [s for s in await filhas(session, raiz.id) if s.id != seg.id]
        for conta in await contas_do_centro(session, raiz, irmas):
            andar_colunas(conta, colunas)
        for s in colunas.sobem:
            s.sort_order = int(s.sort_order) - 1
    await session.delete(seg)
    await session.flush()
    return destino
