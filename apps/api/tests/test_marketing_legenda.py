"""Legenda automática do robô de postagem — cascata, rodízio e sandbox.

Cobre `app/services/marketing/legenda.py`, que decide QUAL texto vai junto do
Reel: `legenda da postagem → legenda do criativo → modelo do PRODUTO →
modelo da MARCA → nenhuma`, com rodízio entre as variações da mesma chave.

Por que este arquivo existe, em três frases: legenda não se edita depois de
publicada (o Instagram não deixa mexer em Reel), ela é o ÚNICO texto que a
busca do Instagram e o Google leem daquele vídeo, e quem escreve as variações
é o operador — template de operador é entrada não confiável rodando no
servidor. Cada teste aqui protege um desses três.

Três coisas que o arquivo insiste em provar e não são óbvias:

1. **O rodízio é POR CONTA.** A mesma variação publicada no @perfil_A não é
   repetição no @perfil_B: são públicos diferentes. Se o rodízio fosse por
   marca, abrir uma conta nova faria ela começar já "gasta".
2. **A escolha é determinística.** Nunca-usada primeiro, depois a mais
   antiga, empate por `id`. Sem o `id` no fim a escolha dependeria da ordem
   que o Postgres devolvesse — e um teste que passa por sorte é pior que um
   teste que falta.
3. **O sandbox é allowlist, não blacklist.** `{{ config }}`, `{{ ''.__class__
   }}` e `{% for %}` nem chegam a executar: a AST é recusada antes do render.

Nada de rede aqui: o módulo não fala com a Meta e não importa httpx.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Marca,
    MarketingCreative,
    MarketingCreativeFile,
    MarketingLegendaModelo,
    MarketingPostagem,
    Product,
    RedeSocial,
    User,
)
from app.services.email_marca import TemplateInvalidoError
from app.services.marketing import legenda as svc

pytestmark = pytest.mark.asyncio

AGORA = datetime(2026, 9, 16, 12, 0, tzinfo=UTC)

# Ids fixos e ORDENADOS: o desempate do rodízio é por `id`, então um teste que
# sorteasse os uuids testaria o sorteio, não a regra.
ID_A = uuid.UUID("00000000-0000-4000-8000-00000000000a")
ID_B = uuid.UUID("00000000-0000-4000-8000-00000000000b")
ID_C = uuid.UUID("00000000-0000-4000-8000-00000000000c")


@pytest.fixture(autouse=True)
async def _limpa(db: AsyncSession):
    """`marketing_legenda_modelos` e `marketing_creatives` não estão no
    _CLEANUP_TABLES do conftest — sem isto um teste herdaria as variações do
    outro e o rodízio "escolheria" uma linha que nem deveria existir."""
    yield
    for tabela in (MarketingLegendaModelo, MarketingCreative):
        for linha in (await db.execute(select(tabela))).scalars().all():
            await db.delete(linha)
    await db.commit()


# ─────────────────────────────────────────────────────────────── sementes


async def _marca(db: AsyncSession, nome: str = "Poofy") -> Marca:
    m = Marca(
        nome=nome,
        slug=nome.lower().replace(" ", "-"),
        sac_fone="11988887777",
        sac_email="sac@poofy.com.br",
        site="https://poofy.com.br",
    )
    db.add(m)
    await db.commit()
    await db.refresh(m)
    return m


async def _produto(db: AsyncSession, user: User, nome: str = "Cafeteira Elétrica") -> Product:
    p = Product(user_id=user.id, sku=f"sku-{uuid.uuid4().hex[:8]}", name=nome, stock=0, min_stock=0)
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return p


async def _criativo(
    db: AsyncSession,
    *,
    marca: Marca | None = None,
    produto: Product | None = None,
    legenda: str | None = None,
) -> tuple[MarketingCreative, MarketingCreativeFile]:
    c = MarketingCreative(
        modelo="Cafeteira 500ml",
        marca=marca.slug if marca else None,
        marca_id=marca.id if marca else None,
        product_id=produto.id if produto else None,
        aprovado=True,
        roteiro="a tiktok style video of a coffee machine",  # é isto que NÃO pode virar legenda
        legenda=legenda,
    )
    db.add(c)
    await db.flush()
    f = MarketingCreativeFile(
        creative_id=c.id,
        file_name="video.mp4",
        file_mime="video/mp4",
        file_rel=f"creatives/{c.id}/video.mp4",
    )
    db.add(f)
    await db.commit()
    await db.refresh(c)
    await db.refresh(f)
    return c, f


async def _conta(db: AsyncSession, marca: Marca, conta: str = "poofy.oficial") -> RedeSocial:
    r = RedeSocial(marca_id=marca.id, plataforma="instagram", conta=conta, ativo=True)
    db.add(r)
    await db.commit()
    await db.refresh(r)
    return r


async def _modelo(
    db: AsyncSession,
    marca: Marca,
    texto: str,
    *,
    produto: Product | None = None,
    ativo: bool = True,
    id: uuid.UUID | None = None,
) -> MarketingLegendaModelo:
    m = MarketingLegendaModelo(
        id=id or uuid.uuid4(),
        marca_id=marca.id,
        product_id=produto.id if produto else None,
        texto=texto,
        ativo=ativo,
    )
    db.add(m)
    await db.commit()
    await db.refresh(m)
    return m


async def _ja_saiu(
    db: AsyncSession,
    creative: MarketingCreative,
    file: MarketingCreativeFile,
    rede: RedeSocial,
    modelo: MarketingLegendaModelo,
    *,
    quando: datetime,
    status: str = "publicado",
) -> MarketingPostagem:
    """Histórico da conta, gravado DIRETO: é o único estado do rodízio."""
    p = MarketingPostagem(
        creative_id=creative.id,
        file_id=file.id,
        rede_social_id=rede.id,
        plataforma=rede.plataforma,
        conta=rede.conta,
        status=status,
        legenda_modelo_id=modelo.id,
        publicado_em=quando,
    )
    db.add(p)
    await db.commit()
    return p


# ─────────────────────────────────────────────────────── cascata, degrau a degrau


async def test_manual_ganha_de_todo_o_resto(db: AsyncSession, make_user):
    """Texto que o operador escreveu vence a biblioteca inteira e vai byte a
    byte: é uma pessoa que leu o vídeo e decidiu, e o `{{ }}` que ela porventura
    tenha digitado é texto, não template."""
    marca = await _marca(db)
    await _modelo(db, marca, "Padrão da marca")
    creative, file = await _criativo(db, marca=marca, legenda="Override do vídeo")
    rede = await _conta(db, marca)

    r = await svc.resolver(
        db, creative=creative, file=file, rede=rede, legenda_manual="  Escrito na mão  "
    )

    assert r.origem == svc.ORIGEM_MANUAL
    assert r.texto == "Escrito na mão"
    assert r.modelo_id is None


async def test_legenda_do_criativo_ganha_da_biblioteca(db: AsyncSession, make_user):
    """O override por vídeo é o degrau logo abaixo do manual — e é RENDERIZADO
    (ao contrário do manual): ele fica salvo e serve a vários posts, então o
    `{{ instagram }}` dele tem que virar o @ da conta que vai receber cada um."""
    marca = await _marca(db)
    await _modelo(db, marca, "Padrão da marca")
    creative, file = await _criativo(db, marca=marca, legenda="Só neste vídeo — @{{ instagram }}")
    rede = await _conta(db, marca, conta="poofy.oficial")

    r = await svc.resolver(db, creative=creative, file=file, rede=rede)

    assert r.origem == svc.ORIGEM_CRIATIVO
    assert r.texto == "Só neste vídeo — @poofy.oficial"
    assert (r.modelo_id, r.total_variacoes, r.indice) == (None, 1, 1)


async def test_modelo_do_produto_ganha_do_modelo_da_marca(db: AsyncSession, make_user):
    marca = await _marca(db)
    produto = await _produto(db, await make_user(), "Cafeteira Elétrica 500ml")
    await _modelo(db, marca, "Padrão da marca {{ marca }}")
    do_produto = await _modelo(db, marca, "{{ produto }} — fale com a gente: {{ whatsapp }}",
                               produto=produto)
    creative, file = await _criativo(db, marca=marca, produto=produto)
    rede = await _conta(db, marca)

    r = await svc.resolver(db, creative=creative, file=file, rede=rede)

    assert r.origem == svc.ORIGEM_PRODUTO
    assert r.modelo_id == do_produto.id
    assert r.texto == "Cafeteira Elétrica 500ml — fale com a gente: (11) 98888-7777"


async def test_sem_modelo_do_produto_cai_pro_padrao_da_marca(db: AsyncSession, make_user):
    marca = await _marca(db)
    produto = await _produto(db, await make_user())
    da_marca = await _modelo(db, marca, "{{ marca }} — SAC {{ email_sac }}")
    creative, file = await _criativo(db, marca=marca, produto=produto)
    rede = await _conta(db, marca)

    r = await svc.resolver(db, creative=creative, file=file, rede=rede)

    assert r.origem == svc.ORIGEM_MARCA
    assert r.modelo_id == da_marca.id
    assert r.texto == "Poofy — SAC sac@poofy.com.br"


async def test_criativo_sem_produto_cai_pro_padrao_da_marca(db: AsyncSession, make_user):
    """Sem produto NÃO trava nada — é o caminho comum (só 2 dos 41 criativos
    de produção casam SKU exato)."""
    marca = await _marca(db)
    produto = await _produto(db, await make_user())
    await _modelo(db, marca, "Legenda do produto", produto=produto)
    da_marca = await _modelo(db, marca, "Padrão da marca")
    creative, file = await _criativo(db, marca=marca, produto=None)
    rede = await _conta(db, marca)

    r = await svc.resolver(db, creative=creative, file=file, rede=rede)

    assert (r.origem, r.modelo_id) == (svc.ORIGEM_MARCA, da_marca.id)


async def test_produto_sem_nome_cai_pro_padrao_da_marca(db: AsyncSession, make_user):
    """Produto vinculado mas SEM NOME não sustenta o degrau do produto.

    A variação de produto existe pra dizer o nome dele; com `{{ produto }}`
    vazio o feed receberia "  — 2 anos de garantia" e isso não se edita depois.
    O padrão da marca é genérico, mas está inteiro.
    """
    marca = await _marca(db)
    produto = await _produto(db, await make_user(), nome="   ")
    await _modelo(db, marca, "{{ produto }} — 2 anos de garantia", produto=produto)
    da_marca = await _modelo(db, marca, "Padrão da marca")
    creative, file = await _criativo(db, marca=marca, produto=produto)
    rede = await _conta(db, marca)

    r = await svc.resolver(db, creative=creative, file=file, rede=rede)

    assert (r.origem, r.modelo_id) == (svc.ORIGEM_MARCA, da_marca.id)


async def test_sem_nada_na_biblioteca_a_origem_e_nenhuma(db: AsyncSession, make_user):
    """O último degrau é "nenhuma", NÃO o roteiro do criativo.

    O roteiro é prompt de geração do vídeo em inglês; publicá-lo poria
    instrução de gravação no Instagram. Quem recusa a postagem automática é
    `pode_publicar` (código `sem_legenda`) — aqui só se registra o vazio.
    """
    marca = await _marca(db)
    creative, file = await _criativo(db, marca=marca)
    rede = await _conta(db, marca)

    r = await svc.resolver(db, creative=creative, file=file, rede=rede)

    assert r == svc.LegendaResolvida(
        texto=None, origem=svc.ORIGEM_NENHUMA, modelo_id=None, total_variacoes=0, indice=None
    )
    assert creative.roteiro not in (r.texto or "")


async def test_manual_igual_ao_da_cascata_nao_e_manual(db: AsyncSession, make_user):
    """O modal chega PRÉ-PREENCHIDO com o texto resolvido e quase ninguém
    edita. Se o texto de volta contasse como "manual", toda postagem gravaria
    `legenda_modelo_id` NULL, o rodízio nunca giraria e a conta publicaria a
    mesma variação pra sempre — exatamente o padrão que o detector procura.
    """
    marca = await _marca(db)
    modelo = await _modelo(db, marca, "Padrão da {{ marca }}")
    creative, file = await _criativo(db, marca=marca)
    rede = await _conta(db, marca)

    devolvido = (await svc.resolver(db, creative=creative, file=file, rede=rede)).texto
    r = await svc.resolver(db, creative=creative, file=file, rede=rede, legenda_manual=devolvido)

    assert (r.origem, r.modelo_id) == (svc.ORIGEM_MARCA, modelo.id)
    # Um caractere a mais já é outra decisão, e aí sim é manual.
    outro = await svc.resolver(
        db, creative=creative, file=file, rede=rede, legenda_manual=f"{devolvido}!"
    )
    assert (outro.origem, outro.modelo_id) == (svc.ORIGEM_MANUAL, None)


# ──────────────────────────────────────────────────────────────────── rodízio


async def test_rodizio_escolhe_a_menos_usada_recentemente_e_e_deterministico(
    db: AsyncSession, make_user
):
    """Três variações, histórico na conta: sai a mais antiga; nunca-usada sai
    antes de qualquer uma. E rodar duas vezes dá o MESMO resultado — a escolha
    não pode depender da ordem que o Postgres devolveu as linhas."""
    marca = await _marca(db)
    a = await _modelo(db, marca, "variação A", id=ID_A)
    b = await _modelo(db, marca, "variação B", id=ID_B)
    c = await _modelo(db, marca, "variação C", id=ID_C)
    creative, file = await _criativo(db, marca=marca)
    rede = await _conta(db, marca)

    await _ja_saiu(db, creative, file, rede, a, quando=AGORA - timedelta(hours=2))
    await _ja_saiu(db, creative, file, rede, b, quando=AGORA - timedelta(days=9))

    # C nunca saiu nesta conta: ganha de todas as usadas.
    primeira = await svc.resolver(db, creative=creative, file=file, rede=rede)
    segunda = await svc.resolver(db, creative=creative, file=file, rede=rede)
    assert primeira.modelo_id == c.id
    assert primeira == segunda

    # Com C recém-publicada, a vez é da mais antiga entre as usadas: B.
    await _ja_saiu(db, creative, file, rede, c, quando=AGORA)
    terceira = await svc.resolver(db, creative=creative, file=file, rede=rede)
    assert terceira.modelo_id == b.id
    assert (terceira.total_variacoes, terceira.indice) == (3, 2)


async def test_rodizio_e_por_conta_nao_por_marca(db: AsyncSession, make_user):
    """A mesma variação gasta no @perfil_a não penaliza o @perfil_b: são
    públicos diferentes, e é o feed de cada conta que fica repetitivo. Se
    fosse por marca, uma conta nova já nasceria com a biblioteca 'usada'."""
    marca = await _marca(db)
    a = await _modelo(db, marca, "variação A", id=ID_A)
    b = await _modelo(db, marca, "variação B", id=ID_B)
    creative, file = await _criativo(db, marca=marca)
    conta_a = await _conta(db, marca, conta="poofy.a")
    conta_b = await _conta(db, marca, conta="poofy.b")

    await _ja_saiu(db, creative, file, conta_a, a, quando=AGORA - timedelta(hours=1))

    # Na conta A, a variação A está gasta: sai a B.
    assert (await svc.resolver(db, creative=creative, file=file, rede=conta_a)).modelo_id == b.id
    # Na conta B, nenhuma saiu ainda: desempate por id devolve a A.
    assert (await svc.resolver(db, creative=creative, file=file, rede=conta_b)).modelo_id == a.id


async def test_rodizio_desempata_por_id(db: AsyncSession, make_user):
    """Duas variações nunca usadas: sempre a de menor `id`. É o que torna o
    teste acima (e a tela) reproduzíveis."""
    marca = await _marca(db)
    await _modelo(db, marca, "variação B", id=ID_B)
    a = await _modelo(db, marca, "variação A", id=ID_A)
    creative, file = await _criativo(db, marca=marca)
    rede = await _conta(db, marca)

    r = await svc.resolver(db, creative=creative, file=file, rede=rede)

    assert r.modelo_id == a.id
    assert (r.total_variacoes, r.indice) == (2, 1)


async def test_rodizio_ignora_variacao_desligada(db: AsyncSession, make_user):
    """`ativo=False` some do rodízio sem apagar a linha — as postagens antigas
    continuam apontando pra ela."""
    marca = await _marca(db)
    await _modelo(db, marca, "desligada", id=ID_A, ativo=False)
    viva = await _modelo(db, marca, "viva", id=ID_B)
    creative, file = await _criativo(db, marca=marca)
    rede = await _conta(db, marca)

    r = await svc.resolver(db, creative=creative, file=file, rede=rede)

    assert (r.modelo_id, r.total_variacoes, r.indice) == (viva.id, 1, 1)


async def test_postagem_que_falhou_nao_gasta_a_variacao(db: AsyncSession, make_user):
    """`falhou`/`cancelado` não contam: ali está PROVADO que o texto não saiu.
    Já `agendado` conta — senão agendar três posts de uma vez daria a mesma
    legenda pros três."""
    marca = await _marca(db)
    a = await _modelo(db, marca, "variação A", id=ID_A)
    b = await _modelo(db, marca, "variação B", id=ID_B)
    creative, file = await _criativo(db, marca=marca)
    rede = await _conta(db, marca)

    await _ja_saiu(db, creative, file, rede, a, quando=AGORA, status="falhou")
    assert (await svc.resolver(db, creative=creative, file=file, rede=rede)).modelo_id == a.id

    await _ja_saiu(db, creative, file, rede, a, quando=AGORA, status="agendado")
    assert (await svc.resolver(db, creative=creative, file=file, rede=rede)).modelo_id == b.id


# ───────────────────────────────────────────── armadilha de gênero e render


@pytest.mark.parametrize(
    "texto",
    [
        "Conheça o {{ produto }} da {{ marca }}",
        "uso do {{ produto }} no dia a dia",
        "aproveite a {{ produto }} enquanto dura",
        "leve um {{ produto }} pra casa",
        "Novidade: chegou a nova {{ produto }}",
        "tudo sobre este {{produto}} aqui",
    ],
)
async def test_artigo_colado_no_produto_e_reprovado_no_salvamento(texto: str):
    """Artigo/contração com gênero colado no `{{ produto }}` não salva.

    O nome do produto é texto livre e muda de gênero sem avisar: "uso do
    {{ produto }}" vira "uso do Cafeteira" no feed. A variação usa o produto
    como RÓTULO, nunca dentro de concordância — e a hora de exigir isso é
    quando alguém está digitando, não quando o robô já tem o vídeo na mão.
    """
    with pytest.raises(TemplateInvalidoError) as e:
        svc.validar_modelo(texto)
    assert str(e.value).startswith("artigo_colado_no_produto:")


@pytest.mark.parametrize(
    "texto",
    [
        "{{ produto }} — 2 anos de garantia",
        "Fale com a gente sobre {{ produto }}",
        "Saiba mais de {{ produto }} em @{{ instagram }}",
        "Dúvidas com {{ produto }}? {{ whatsapp }}",
        "Para {{ produto }} e o resto da linha {{ marca }}",
    ],
)
async def test_produto_como_rotulo_passa(texto: str):
    """Preposição sem gênero ("de", "com", "para", "sobre") e o produto como
    rótulo no começo da frase: é assim que se escreve a variação."""
    svc.validar_modelo(texto)


async def test_produto_feminino_nao_quebra_o_texto(db: AsyncSession, make_user):
    """A mesma variação, dois produtos de gêneros diferentes, texto correto
    nos dois. É o teste que prova que a regra acima resolve o problema de
    verdade, e não só barra template."""
    marca = await _marca(db)
    user = await make_user()
    rede = await _conta(db, marca)
    texto = "{{ produto }} é {{ marca }}. Dúvidas: {{ whatsapp }}"

    saidas = []
    for nome in ("Cafeteira Elétrica", "Ventilador de Torre"):
        produto = await _produto(db, user, nome)
        await _modelo(db, marca, texto, produto=produto)
        creative, file = await _criativo(db, marca=marca, produto=produto)
        saidas.append((await svc.resolver(db, creative=creative, file=file, rede=rede)).texto)

    assert saidas == [
        "Cafeteira Elétrica é Poofy. Dúvidas: (11) 98888-7777",
        "Ventilador de Torre é Poofy. Dúvidas: (11) 98888-7777",
    ]


async def test_artigo_colado_ainda_renderiza(db: AsyncSession, make_user):
    """A checagem de gênero é do SALVAMENTO, não do render.

    Concordância é regra de escrita, não de segurança: uma linha salva antes
    da regra existir (ou por outro caminho) tem que continuar publicando. Reel
    com "uso do Cafeteira" é feio; Reel SEM legenda é criativo queimado.
    """
    assert svc.renderizar("uso do {{ produto }}", {"produto": "Cafeteira"}) == "uso do Cafeteira"


async def test_texto_maior_que_2200_e_cortado():
    """A Meta corta em 2200 — melhor cortar aqui, onde o operador VÊ o
    resultado no modal antes de publicar."""
    saida = svc.renderizar("{{ produto }}", {"produto": "x" * 3000})
    assert len(saida) == svc.LEGENDA_MAX == 2200
    assert saida == "x" * 2200


async def test_legenda_manual_tambem_e_cortada_em_2200(db: AsyncSession, make_user):
    marca = await _marca(db)
    creative, file = await _criativo(db, marca=marca)
    rede = await _conta(db, marca)

    r = await svc.resolver(
        db, creative=creative, file=file, rede=rede, legenda_manual="y" * 3000
    )

    assert len(r.texto) == 2200


async def test_modelo_maior_que_2200_nao_salva():
    """No salvamento o teto é recusa, não corte: quem está digitando tem que
    saber que passou do limite, em vez de descobrir no feed."""
    with pytest.raises(TemplateInvalidoError) as e:
        svc.validar_modelo("z" * 2201)
    assert str(e.value) == "legenda_muito_longa"


# ───────────────────────────────────────────────────────────── Jinja hostil


@pytest.mark.parametrize(
    ("texto", "prefixo"),
    [
        # Os clássicos de fuga de sandbox do Jinja: chegam a NOME e ATRIBUTO,
        # e os dois morrem na allowlist da AST, antes de qualquer render.
        ("{{ config }}", "placeholder_desconhecido"),
        ("{{ self }}", "placeholder_desconhecido"),
        ("{{ ''.__class__ }}", "template_bloco_nao_permitido"),
        ("{{ ''.__class__.__mro__[1].__subclasses__() }}", "template_bloco_nao_permitido"),
        ("{{ lipsum.__globals__['os'] }}", "template_bloco_nao_permitido"),
        ("{{ request.application }}", "template_bloco_nao_permitido"),
        # Bloco: barrado pelo `{%` antes até de compilar. Um `for` de 10**9
        # não é fuga nenhuma — é o event loop parado, que derruba a API toda.
        ("{% for i in range(10**9) %}x{% endfor %}", "template_bloco_nao_permitido"),
        ("{% if 1 %}x{% endif %}", "template_bloco_nao_permitido"),
        ("{% set x = 1 %}", "template_bloco_nao_permitido"),
        # Sem nó de constante nem de aritmética, nada disso existe: o sandbox
        # do Jinja sozinho NÃO barra (aloca/calcula antes de qualquer teto).
        ("{{ 'x' * 100000000 }}", "template_bloco_nao_permitido"),
        ("{{ 2 ** (2 ** 31) }}", "template_bloco_nao_permitido"),
        # Placeholder de e-mail que não pode existir em legenda: nome de
        # cliente numa legenda pública do Instagram é vazamento.
        ("{{ cliente }}", "placeholder_desconhecido"),
        ("{{ pedido }}", "placeholder_desconhecido"),
    ],
)
async def test_template_hostil_e_barrado_antes_de_executar(texto: str, prefixo: str):
    for chamada in (svc.validar_modelo, lambda t: svc.renderizar(t, {})):
        with pytest.raises(TemplateInvalidoError) as e:
            chamada(texto)
        assert str(e.value).startswith(prefixo)


async def test_filtro_fora_da_lista_e_barrado():
    """`{{ x|upper }}` passa; qualquer outro filtro (e filtro COM argumento,
    que é por onde se contrabandeia constante) não."""
    assert svc.renderizar("{{ marca|upper }}", {"marca": "Poofy"}) == "POOFY"
    for texto in ("{{ marca|attr('__class__') }}", "{{ marca|map('upper') }}"):
        with pytest.raises(TemplateInvalidoError) as e:
            svc.renderizar(texto, {"marca": "Poofy"})
        assert str(e.value).startswith("filtro_nao_permitido")


async def test_variacao_que_nao_renderiza_recusa_a_postagem(db: AsyncSession, make_user):
    """Template quebrado na biblioteca vira recusa, não legenda pela metade:
    publicar não tem desfazer e Reel não se edita."""
    marca = await _marca(db)
    await _modelo(db, marca, "{{ config }}")
    creative, file = await _criativo(db, marca=marca)
    rede = await _conta(db, marca)

    with pytest.raises(TemplateInvalidoError):
        await svc.resolver(db, creative=creative, file=file, rede=rede)


# ─────────────────────────────────────────────────────────────── contexto


async def test_placeholders_de_nunca_devolve_none(db: AsyncSession, make_user):
    """Marca sem telefone/e-mail e postagem sem conta têm que virar STRING
    VAZIA: `None` no contexto publicaria a palavra "None" no meio do Reel."""
    vazia = Marca(nome="Sem Dados", slug="sem-dados")

    v = svc.placeholders_de(vazia, None, None)

    assert v == {
        "marca": "Sem Dados",
        "produto": "",
        "produto_modelo": "",
        "whatsapp": "",
        "email_sac": "",
        "instagram": "",
        "site": "",
    }
    assert set(v) == set(svc.PLACEHOLDERS)


async def test_placeholders_de_formata_o_whatsapp_como_no_email(db: AsyncSession, make_user):
    """Mesma marca, mesmo número, mesmo formato no e-mail e na legenda —
    `formatar_fone` é reusado justamente pra não haver dois formatos."""
    marca = await _marca(db)
    rede = await _conta(db, marca, conta="poofy.oficial")

    v = svc.placeholders_de(marca, "Cafeteira", rede)

    assert v["whatsapp"] == "(11) 98888-7777"
    assert v["instagram"] == "poofy.oficial"
    assert v["produto"] == "Cafeteira"


async def test_site_existe_na_allowlist_mas_fica_fora_dos_textos(db: AsyncSession, make_user):
    """Decisão do Eduardo (16/09/2026): nenhuma das duas marcas manda pra
    site. O placeholder continua válido — ligar depois é editar o texto na
    tela, sem deploy."""
    marca = await _marca(db)
    svc.validar_modelo("{{ marca }} · {{ site }}")
    assert svc.renderizar("{{ site }}", svc.placeholders_de(marca, "", None)) == (
        "https://poofy.com.br"
    )


async def test_variacao_quebrada_nao_derruba_a_legenda_escrita_a_mao(db):
    """Um `{{ produtos }}` errado numa variação travaria TODA postagem da marca.

    O texto manual não passa pelo Jinja — ele é o que uma pessoa já leu na
    tela. O defeito de outra linha da biblioteca é irrelevante pra ele.
    """
    from app.models import MarketingLegendaModelo
    from app.services.marketing import legenda as svc

    marca = await _marca(db, "Quebrada")
    c, f = await _criativo(db, marca=marca)
    rede = await _conta(db, marca, conta="quebrada.oficial")
    db.add(MarketingLegendaModelo(marca_id=marca.id, texto="oi {{ produtos }}"))
    await db.commit()

    # Sem texto manual, recusa (é o certo: publicar não tem desfazer).
    with pytest.raises(svc.TemplateInvalidoError):
        await svc.resolver(db, creative=c, file=f, rede=rede)

    # Com texto manual, passa — e sai exatamente o que a pessoa escreveu.
    r = await svc.resolver(db, creative=c, file=f, rede=rede, legenda_manual="escrita à mão")
    assert r.origem == svc.ORIGEM_MANUAL
    assert r.texto == "escrita à mão"


# ─────────── nome de vitrine: ERP → legenda (16/09/2026) ───────────


def test_nome_de_vitrine_cobre_os_12_produtos_reais():
    """`products.name` é nome de ERP. Estes 12 são os do acervo em produção.

    Publicar "Uranyx Fossibot F109S 24.256 - Preto" num Reel é jogar código de
    estoque na cara do cliente. O fabricante FICA: a própria conta escreve
    "O Uranyx Oukitel WP60 une resistência militar…".
    """
    from app.services.marketing.legenda import nome_de_vitrine

    esperado = {
        "Oscal Flat 3C 16.128 - Laranja": ("Oscal Flat 3C 128 GB Laranja", "Oscal Flat 3C"),
        "Oscal Marine 1 12.128 - Preto": ("Oscal Marine 1 128 GB Preto", "Oscal Marine 1"),
        "Uranyx Fossibot F109S 24.256 - Preto": (
            "Fossibot F109S 256 GB Preto",
            "Fossibot F109S",
        ),
        "Uranyx Fossibot F112 Pro 5G 24.256 - Verde": (
            "Fossibot F112 Pro 5G 256 GB Verde",
            "Fossibot F112 Pro 5G",
        ),
        "Uranyx Fossibot F117 24.256 - Preto": ("Fossibot F117 256 GB Preto", "Fossibot F117"),
        "Uranyx Oukitel WP53 24.128 - Preto": ("Oukitel WP53 128 GB Preto", "Oukitel WP53"),
        "Uranyx S5 16.128 - Prata": ("S5 128 GB Prata", "S5"),
    }
    for bruto, (completo, modelo) in esperado.items():
        assert nome_de_vitrine(bruto, "Uranyx") == (completo, modelo), bruto


def test_nome_de_vitrine_nao_estraga_o_que_nao_reconhece():
    """Produto novo com nome fora da forma esperada aparece sem avisar.

    Melhor uma legenda com o nome cru do que uma legenda com o nome pela
    metade — o post não se edita.
    """
    from app.services.marketing.legenda import nome_de_vitrine

    assert nome_de_vitrine("Cafeteira Expresso Turbo", "Uranyx") == (
        "Cafeteira Expresso Turbo",
        "Cafeteira Expresso Turbo",
    )
    assert nome_de_vitrine("Uranyx Air Fryer Grill", "Uranyx") == (
        "Air Fryer Grill",
        "Air Fryer Grill",
    )
    assert nome_de_vitrine(None, "Uranyx") == ("", "")
    assert nome_de_vitrine("", None) == ("", "")


async def test_placeholder_produto_sai_limpo_na_legenda(db, make_user):
    """Ponta a ponta: o que a marca escreve com {{ produto }} sai de vitrine."""
    from app.models import MarketingLegendaModelo, Product
    from app.services.marketing import legenda as svc

    dono = await make_user()
    marca = await _marca(db, "Uranyx")
    p = Product(user_id=dono.id, sku="dgtest", name="Uranyx Fossibot F109S 24.256 - Preto")
    db.add(p)
    await db.flush()
    c, f = await _criativo(db, marca=marca)
    c.product_id = p.id
    rede = await _conta(db, marca, conta="uranyx.teste")
    db.add(
        MarketingLegendaModelo(
            marca_id=marca.id,
            texto="{{ marca }} {{ produto }} — só o modelo: {{ produto_modelo }}",
        )
    )
    await db.commit()

    r = await svc.resolver(db, creative=c, file=f, rede=rede)
    assert r.texto == "Uranyx Fossibot F109S 256 GB Preto — só o modelo: Fossibot F109S"
    assert "24.256" not in r.texto


def test_marca_toda_minuscula_sobe_a_primeira_letra():
    """O cadastro guarda "uranyx" em minúscula porque ali é chave de busca.

    Numa legenda pública "da uranyx" salta aos olhos. Só a primeira letra sobe,
    e só quando o nome está todo minúsculo — quem escreveu "LOCAGIL" ou
    "Charlots Park" fica como está.
    """
    from app.models import Marca
    from app.services.marketing.legenda import placeholders_de

    def nome_de(n: str) -> str:
        return placeholders_de(Marca(nome=n, slug="x"), None, None)["marca"]

    assert nome_de("uranyx") == "Uranyx"
    assert nome_de("locagil") == "Locagil"
    assert nome_de("charlots") == "Charlots"
    # Dígito na frente: `capitalize` não tem o que subir, e tudo bem.
    assert nome_de("7buyers") == "7buyers"
    # Quem já escreveu com caixa fica intocado.
    assert nome_de("LOCAGIL") == "LOCAGIL"
    assert nome_de("Charlots Park") == "Charlots Park"
