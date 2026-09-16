"""Legenda automática — a BORDA (routers/marketing_legendas.py + criativos).

Cobre o que a API promete pra tela: o CRUD da biblioteca de variações, a
prévia renderizada, a legenda que o modal de publicar mostra no lugar do
`roteiro` e o vínculo criativo → produto, resolvido no salvamento.

A decisão que este arquivo protege acima de todas: **o modal para de
pré-preencher a legenda com o `roteiro`**. `roteiro` é prompt de geração do
vídeo, em inglês ("a tiktok style video of a coffee machine"); publicá-lo põe
instrução de produção no feed da marca. Por isso quase toda semente aqui tem
um `roteiro` em inglês, e mais de um teste confere que ele NÃO aparece na
resposta.

A cascata e o rodízio em si são de `services/marketing/legenda.py` e têm
teste próprio (test_marketing_legenda.py). Aqui a pergunta é outra: a borda
chama a função certa, com os argumentos certos, e só deixa escrever quem tem
`edit`? Prévia, em particular, NÃO PODE GRAVAR — é o endpoint que o operador
chama a cada tecla.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Integration,
    IntegrationPlatform,
    Marca,
    MarketingCreative,
    MarketingCreativeFile,
    MarketingLegendaModelo,
    Product,
    ProductLink,
    RedeSocial,
    User,
    UserRole,
)
from app.security.cipher import encrypt_json

pytestmark = pytest.mark.asyncio

API = "/api/marketing/legendas"
API_CRIATIVOS = "/api/marketing/creatives"

# O prompt de vídeo que mora em `roteiro`. Se este texto aparecer em qualquer
# resposta de legenda, o pré-preenchimento errado voltou.
ROTEIRO = "a tiktok style video of a coffee machine, cinematic lighting"


@pytest.fixture(scope="module", autouse=True)
def _monta_router():
    """`app/main.py` só inclui os routers de Marketing quando
    `enable_marketing` está ligado, e o ambiente de teste não liga a flag —
    sem isto todo GET/POST daqui viraria 404 e o arquivo passaria vazio."""
    from app.main import app
    from app.routers import marketing_creatives, marketing_legendas

    for mod, prefixo in ((marketing_legendas, API), (marketing_creatives, API_CRIATIVOS)):
        if not any(getattr(r, "path", "").startswith(prefixo) for r in app.routes):
            app.include_router(mod.router)


@pytest.fixture(autouse=True)
async def _limpa(db: AsyncSession):
    """`marketing_legenda_modelos` e `marketing_creatives` não estão no
    _CLEANUP_TABLES do conftest — sem isto um teste herdaria a biblioteca do
    outro e a cascata escolheria uma variação que nem deveria existir."""
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
    )
    db.add(m)
    await db.commit()
    await db.refresh(m)
    return m


async def _produto(
    db: AsyncSession, user: User, *, nome: str = "Cafeteira Elétrica", sku: str | None = None
) -> Product:
    p = Product(
        user_id=user.id,
        sku=sku or f"sku-{uuid.uuid4().hex[:8]}",
        name=nome,
        stock=0,
        min_stock=0,
    )
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return p


async def _anuncio(
    db: AsyncSession, user: User, produto: Product, external_sku: str
) -> ProductLink:
    """O anúncio do produto num marketplace — é `product_links.external_sku`
    que casa com o SKU do criativo (39 dos 41 casos medidos em produção)."""
    integ = Integration(
        user_id=user.id,
        platform=IntegrationPlatform.ML,
        name=f"ml-{uuid.uuid4().hex[:6]}",
        credentials=encrypt_json({"access_token": "fake"}),
    )
    db.add(integ)
    await db.flush()
    link = ProductLink(
        user_id=user.id,
        product_id=produto.id,
        integration_id=integ.id,
        platform=IntegrationPlatform.ML,
        external_id=f"MLB{uuid.uuid4().hex[:8]}",
        external_sku=external_sku,
    )
    db.add(link)
    await db.commit()
    await db.refresh(link)
    return link


async def _conta(db: AsyncSession, marca: Marca, conta: str = "poofy.oficial") -> RedeSocial:
    r = RedeSocial(marca_id=marca.id, plataforma="instagram", conta=conta, ativo=True)
    db.add(r)
    await db.commit()
    await db.refresh(r)
    return r


async def _criativo(
    db: AsyncSession,
    *,
    marca: Marca,
    produto: Product | None = None,
    legenda: str | None = None,
) -> tuple[MarketingCreative, MarketingCreativeFile]:
    c = MarketingCreative(
        modelo="Cafeteira 500ml",
        marca=marca.slug,
        marca_id=marca.id,
        product_id=produto.id if produto else None,
        aprovado=True,
        roteiro=ROTEIRO,
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


async def _modelo(
    db: AsyncSession, marca: Marca, texto: str, *, produto: Product | None = None
) -> MarketingLegendaModelo:
    m = MarketingLegendaModelo(
        marca_id=marca.id, product_id=produto.id if produto else None, texto=texto
    )
    db.add(m)
    await db.commit()
    await db.refresh(m)
    return m


async def _quantos(db: AsyncSession) -> int:
    return (
        await db.execute(select(func.count()).select_from(MarketingLegendaModelo))
    ).scalar_one()


async def _user(make_user, auth_as, *, edit: bool, role: UserRole = UserRole.USER) -> User:
    u = await make_user(
        role=role,
        permissions={"marketing_criativos": {"view": True, "edit": edit}},
    )
    auth_as(u)
    return u


# ══════════════════════════════════════════════════════════════ permissão


async def test_view_le_mas_nao_escreve(client, db, make_user, auth_as):
    """`view` é quem monta o post; `edit` é quem mexe na biblioteca.

    A variação vale pra TODOS os posts futuros da marca — quem só tem
    permissão de olhar a tela não pode trocar o texto que vai sair sozinho na
    conta oficial daqui pra frente.
    """
    marca = await _marca(db)
    modelo = await _modelo(db, marca, "{{ marca }} — novidade da semana")
    await _user(make_user, auth_as, edit=False)

    assert (await client.get(API)).status_code == 200
    corpo = {"marca_id": str(marca.id), "texto": "{{ marca }} — outra"}
    assert (await client.post(API, json=corpo)).status_code == 403
    assert (await client.patch(f"{API}/{modelo.id}", json={"ativo": False})).status_code == 403
    assert (await client.delete(f"{API}/{modelo.id}")).status_code == 403
    # Nenhuma das três passou pelo banco.
    assert await _quantos(db) == 1


# ═══════════════════════════════════════════════════════════════ prévia


async def test_preview_renderiza_com_dados_reais_e_nao_grava(client, db, make_user, auth_as):
    """A prévia é o endpoint chamado a cada tecla: renderiza e não grava.

    Renderizar com os dados REAIS (nome da marca, WhatsApp formatado, @ da
    conta, nome do produto) é o que deixa o operador ver a armadilha de
    concordância antes de salvar — e é o que faz a contagem 412/2200 valer,
    já que `{{ produto }}` tem 13 caracteres no editor e 17 no Instagram.
    """
    user = await _user(make_user, auth_as, edit=True, role=UserRole.ADMIN)
    marca = await _marca(db)
    await _conta(db, marca)
    produto = await _produto(db, user)

    r = await client.post(
        f"{API}/preview",
        json={
            "texto": (
                "{{ produto }} na {{ marca }}. "
                "Fale com a gente: {{ whatsapp }} — @{{ instagram }}"
            ),
            "marca_id": str(marca.id),
            "product_id": str(produto.id),
        },
    )
    assert r.status_code == 200, r.text
    corpo = r.json()
    assert corpo["texto"] == (
        "Cafeteira Elétrica na Poofy. Fale com a gente: (11) 98888-7777 — @poofy.oficial"
    )
    assert corpo["tamanho"] == len(corpo["texto"])
    # Prévia não é rascunho salvo: a biblioteca continua vazia.
    assert await _quantos(db) == 0


async def test_preview_recusa_placeholder_que_nao_existe(client, db, make_user, auth_as):
    """`{{ produtoo }}` tem que morrer na tela de quem digitou.

    Gravado, ele só apareceria na hora de publicar — e ali não tem operador
    olhando, tem cron: a postagem seria recusada (ou sairia sem legenda) num
    horário em que ninguém está vendo.
    """
    await _user(make_user, auth_as, edit=True, role=UserRole.ADMIN)
    marca = await _marca(db)

    r = await client.post(
        f"{API}/preview",
        json={"texto": "Conheça {{ produtoo }}", "marca_id": str(marca.id)},
    )
    assert r.status_code == 422
    assert "placeholder_desconhecido" in r.text


async def test_salvar_recusa_artigo_colado_no_produto(client, db, make_user, auth_as):
    """A armadilha do spec: "uso do {{ produto }}" vira "uso do Cafeteira".

    O nome do produto é livre e muda de gênero sem avisar, então a variação
    usa o produto como RÓTULO, nunca dentro de concordância. Barrado no
    salvamento, que é onde tem gente pra corrigir.
    """
    await _user(make_user, auth_as, edit=True, role=UserRole.ADMIN)
    marca = await _marca(db)

    r = await client.post(
        API,
        json={"marca_id": str(marca.id), "texto": "Aproveite o uso do {{ produto }} hoje"},
    )
    assert r.status_code == 422
    assert "artigo_colado_no_produto" in r.text
    assert await _quantos(db) == 0


# ═══════════════════════════════════════════════════════════════ CRUD


async def test_crud_da_biblioteca_e_filtros(client, db, make_user, auth_as):
    """Criar, listar filtrando, editar e apagar — com o padrão da marca
    listado ANTES das variações de produto (a ordem em que o operador confere
    "o que sai quando o criativo não tem produto")."""
    user = await _user(make_user, auth_as, edit=True, role=UserRole.ADMIN)
    marca = await _marca(db)
    produto = await _produto(db, user)

    padrao = await client.post(
        API, json={"marca_id": str(marca.id), "texto": "{{ marca }} — qualidade de verdade"}
    )
    assert padrao.status_code == 201, padrao.text
    assert padrao.json()["product_id"] is None
    assert padrao.json()["marca_nome"] == "Poofy"

    do_produto = await client.post(
        API,
        json={
            "marca_id": str(marca.id),
            "product_id": str(produto.id),
            "texto": "{{ produto }} — agora na {{ marca }}",
        },
    )
    assert do_produto.status_code == 201, do_produto.text
    assert do_produto.json()["product_nome"] == "Cafeteira Elétrica"

    todas = (await client.get(API, params={"marca_id": str(marca.id)})).json()
    assert [x["id"] for x in todas] == [padrao.json()["id"], do_produto.json()["id"]]
    so_produto = (await client.get(API, params={"product_id": str(produto.id)})).json()
    assert [x["id"] for x in so_produto] == [do_produto.json()["id"]]

    # `product_id: null` explícito rebaixa a variação a padrão da marca —
    # `exclude_unset` é o que separa "manda null" de "não mandou o campo".
    virou_padrao = await client.patch(
        f"{API}/{do_produto.json()['id']}", json={"product_id": None, "ativo": False}
    )
    assert virou_padrao.status_code == 200, virou_padrao.text
    assert virou_padrao.json()["product_id"] is None
    assert virou_padrao.json()["ativo"] is False
    assert virou_padrao.json()["texto"] == "{{ produto }} — agora na {{ marca }}"

    assert (await client.delete(f"{API}/{padrao.json()['id']}")).status_code == 200
    assert await _quantos(db) == 1


async def test_patch_nao_deixa_a_variacao_sem_marca(client, db, make_user, auth_as):
    """`marca_id` não é campo de PATCH: a coluna é NOT NULL e um `null`
    explícito viraria IntegrityError (500) num PATCH que o front acha
    inofensivo. Pydantic ignora o campo desconhecido e a marca fica."""
    await _user(make_user, auth_as, edit=True, role=UserRole.ADMIN)
    marca = await _marca(db)
    outra = await _marca(db, "Charlots")
    modelo = await _modelo(db, marca, "{{ marca }} — novidade")

    r = await client.patch(f"{API}/{modelo.id}", json={"marca_id": str(outra.id)})
    assert r.status_code == 200, r.text
    assert r.json()["marca_id"] == str(marca.id)


# ══════════════════════════════════════════════════════ legenda resolvida


async def _resolvida(client, creative, file, rede) -> dict:
    r = await client.get(
        f"{API}/resolvida",
        params={
            "creative_id": str(creative.id),
            "file_id": str(file.id),
            "rede_social_id": str(rede.id),
        },
    )
    assert r.status_code == 200, r.text
    return r.json()


async def test_resolvida_cai_no_padrao_da_marca(client, db, make_user, auth_as):
    """Criativo sem produto vinculado usa o padrão da MARCA — o caminho
    comum (só 2 dos 41 criativos de produção têm SKU que casa exato).

    O rótulo do modal sai daqui: "padrão da marca · variação 1 de 2".
    """
    await _user(make_user, auth_as, edit=True, role=UserRole.ADMIN)
    marca = await _marca(db)
    rede = await _conta(db, marca)
    await _modelo(db, marca, "{{ marca }} — leve pra casa")
    await _modelo(db, marca, "Conheça a {{ marca }}")
    c, f = await _criativo(db, marca=marca)

    corpo = await _resolvida(client, c, f, rede)
    assert corpo["origem"] == "marca"
    assert corpo["total_variacoes"] == 2
    assert corpo["indice"] in (1, 2)
    assert corpo["texto"] in ("Poofy — leve pra casa", "Conheça a Poofy")


async def test_resolvida_prefere_o_modelo_do_produto(client, db, make_user, auth_as):
    """Com produto vinculado, a legenda dele ganha do padrão da marca — é a
    frase do Eduardo ("com base no produto do criativo e se não, cai num
    padrão da marca") virada em asserção."""
    user = await _user(make_user, auth_as, edit=True, role=UserRole.ADMIN)
    marca = await _marca(db)
    rede = await _conta(db, marca)
    produto = await _produto(db, user)
    await _modelo(db, marca, "{{ marca }} — padrão genérico")
    await _modelo(db, marca, "{{ produto }} · {{ marca }}", produto=produto)
    c, f = await _criativo(db, marca=marca, produto=produto)

    corpo = await _resolvida(client, c, f, rede)
    assert corpo["origem"] == "produto"
    assert corpo["texto"] == "Cafeteira Elétrica · Poofy"
    assert corpo["total_variacoes"] == 1


async def test_resolvida_sem_biblioteca_recusa_em_vez_de_usar_o_roteiro(
    client, db, make_user, auth_as
):
    """Sem nenhuma variação cadastrada a resposta é "nenhuma" — e vazia.

    Este é o teste que guarda a feature inteira: o caminho antigo devolveria
    o `roteiro` (prompt de vídeo em inglês) e o modal publicaria aquilo. Sem
    legenda o robô automático RECUSA; o clique manual passa com aviso.
    """
    await _user(make_user, auth_as, edit=True, role=UserRole.ADMIN)
    marca = await _marca(db)
    rede = await _conta(db, marca)
    c, f = await _criativo(db, marca=marca)

    corpo = await _resolvida(client, c, f, rede)
    assert corpo["origem"] == "nenhuma"
    assert corpo["texto"] is None
    assert corpo["total_variacoes"] == 0
    assert "tiktok style video" not in str(corpo)


async def test_resolvida_com_arquivo_de_outro_criativo_e_404(client, db, make_user, auth_as):
    """O modal manda sempre o `file_id`; um que não é daquele criativo é tela
    velha (vídeo trocado ou apagado) e não pode virar legenda de nada."""
    await _user(make_user, auth_as, edit=True, role=UserRole.ADMIN)
    marca = await _marca(db)
    rede = await _conta(db, marca)
    c, _ = await _criativo(db, marca=marca)
    _, outro_arquivo = await _criativo(db, marca=marca)

    r = await client.get(
        f"{API}/resolvida",
        params={
            "creative_id": str(c.id),
            "file_id": str(outro_arquivo.id),
            "rede_social_id": str(rede.id),
        },
    )
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "file_not_found"


# ═════════════════════════════════════════════ criativo → produto/legenda


async def test_product_id_resolvido_pelo_sufixo_de_variante(client, db, make_user, auth_as):
    """SKU `dg017.pi` casa com o anúncio cadastrado na BASE `dg017`.

    O sufixo é variante de cor e quase nenhum anúncio está cadastrado com
    ele. Sem cair na base, o criativo ficaria sem produto e a legenda perderia
    o degrau do produto — calada, que é o pior jeito de errar.
    """
    user = await _user(make_user, auth_as, edit=True, role=UserRole.ADMIN)
    produto = await _produto(db, user)
    await _anuncio(db, user, produto, "dg017")

    r = await client.post(
        API_CRIATIVOS,
        json={"modelo": "Cafeteira 500ml", "sku": "dg017.pi", "roteiro": ROTEIRO},
    )
    assert r.status_code == 200, r.text
    assert r.json()["product_id"] == str(produto.id)


async def test_product_id_prefere_o_sku_exato_ao_da_base(client, db, make_user, auth_as):
    """Com anúncio na base E na variante, ganha o exato.

    Duas cores do mesmo produto costumam cair no mesmo `product_id`, mas nem
    sempre — e a ordem precisa ser determinística: legenda que troca de
    produto entre dois salvamentos, sem ninguém mexer em nada, é impossível
    de depurar depois que o Reel já saiu.
    """
    user = await _user(make_user, auth_as, edit=True, role=UserRole.ADMIN)
    base = await _produto(db, user, nome="Cafeteira Elétrica")
    variante = await _produto(db, user, nome="Cafeteira Elétrica Pink")
    await _anuncio(db, user, base, "dg017")
    await _anuncio(db, user, variante, "dg017.pi")

    r = await client.post(API_CRIATIVOS, json={"modelo": "Cafeteira", "sku": "DG017.PI"})
    assert r.status_code == 200, r.text
    assert r.json()["product_id"] == str(variante.id)


async def test_patch_do_sku_arrasta_o_product_id(client, db, make_user, auth_as):
    """Trocar o SKU na célula muda o vínculo junto — inclusive pra NULL.

    Mesma regra do `marca_id`: célula certa na tela e vínculo apontando pro
    produto antigo é divergência que ninguém vê, até a legenda sair falando
    do produto errado.
    """
    user = await _user(make_user, auth_as, edit=True, role=UserRole.ADMIN)
    produto = await _produto(db, user)
    await _anuncio(db, user, produto, "dg017")
    criado = (
        await client.post(API_CRIATIVOS, json={"modelo": "Cafeteira", "sku": "dg017"})
    ).json()
    assert criado["product_id"] == str(produto.id)

    virou = await client.patch(f"{API_CRIATIVOS}/{criado['id']}", json={"sku": "nao-existe"})
    assert virou.status_code == 200, virou.text
    assert virou.json()["product_id"] is None


async def test_legenda_do_criativo_ganha_da_biblioteca(client, db, make_user, auth_as):
    """O override por vídeo entra pelo POST/PATCH do criativo e é o degrau
    acima da biblioteca — o operador escreveu para ESTE vídeo.

    Também é template: `{{ marca }}` no override tem que sair renderizado,
    senão o Instagram receberia as chaves cruas.
    """
    await _user(make_user, auth_as, edit=True, role=UserRole.ADMIN)
    marca = await _marca(db)
    rede = await _conta(db, marca)
    await _modelo(db, marca, "{{ marca }} — padrão que não deve sair")

    criado = (
        await client.post(
            API_CRIATIVOS,
            json={
                "modelo": "Cafeteira",
                "marca": marca.slug,
                "roteiro": ROTEIRO,
                "legenda": "Só hoje: {{ marca }} com frete combinado no direct",
            },
        )
    ).json()
    assert criado["legenda"].startswith("Só hoje:")
    creative = await db.get(MarketingCreative, uuid.UUID(criado["id"]))
    arquivo = MarketingCreativeFile(
        creative_id=creative.id,
        file_name="video.mp4",
        file_mime="video/mp4",
        file_rel=f"creatives/{creative.id}/video.mp4",
    )
    db.add(arquivo)
    await db.commit()

    corpo = await _resolvida(client, creative, arquivo, rede)
    assert corpo["origem"] == "criativo"
    assert corpo["texto"] == "Só hoje: Poofy com frete combinado no direct"


async def test_resolvida_antes_de_marcar_a_conta(client, db, make_user, auth_as):
    """O modal abre e JÁ mostra a legenda, sem conta marcada ainda.

    A tela chama este endpoint assim que o operador escolhe o vídeo — o
    `rede_social_id` só existe depois do checkbox. Exigir a conta aqui faria o
    modal abrir com o campo vazio e aviso de erro, que é exatamente o estado
    que esta feature veio apagar. Sem conta o rodízio não gira e
    `{{ instagram }}` sai vazio; o texto aparece igual.
    """
    await _user(make_user, auth_as, edit=True, role=UserRole.ADMIN)
    marca = await _marca(db)
    await _modelo(db, marca, "{{ marca }} — direto de fábrica @{{ instagram }}")
    c, f = await _criativo(db, marca=marca)

    r = await client.get(
        f"{API}/resolvida", params={"creative_id": str(c.id), "file_id": str(f.id)}
    )
    assert r.status_code == 200, r.text
    assert r.json()["origem"] == "marca"
    assert r.json()["texto"] == "Poofy — direto de fábrica @"


async def test_resolvida_recusa_conta_de_outra_marca(client, db, make_user, auth_as):
    """A prévia tem que concordar com o `agendar`.

    Mostrar uma legenda renderizada com o @ de outra marca e depois recusar a
    gravação é o pior dos dois mundos: o operador confia no que leu e leva um
    erro que não explica nada.
    """
    from app.models import Marca, MarketingCreative, RedeSocial

    dona = Marca(nome="DonaLeg", slug="donaleg")
    outra = Marca(nome="OutraLeg", slug="outraleg")
    db.add_all([dona, outra])
    await db.flush()
    c = MarketingCreative(modelo="video 30s", marca=dona.slug, marca_id=dona.id)
    alheia = RedeSocial(marca_id=outra.id, plataforma="instagram", conta="alheia.oficial")
    db.add_all([c, alheia])
    await db.commit()
    await db.refresh(c)
    await db.refresh(alheia)

    u = await make_user(permissions={"marketing_criativos": {"view": True, "edit": True}})
    auth_as(u)
    r = await client.get(
        "/api/marketing/legendas/resolvida",
        params={"creative_id": str(c.id), "rede_social_id": str(alheia.id)},
    )
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "conta_de_outra_marca"
