"""O elo do criativo com o cadastro: texto livre → `marca_id` e `product_id`.

`marca` e `sku` são texto digitado — na célula da aba Criativos, no formulário
do portal das agências, no pedido de ideia. O que o resto do sistema usa são os
ids: o robô de autopostagem filtra a fila por `marca_id`, a legenda automática
acha a marca e o produto pelos ids, o Desempenho junta vídeos por `product_id`.

Fica em `services/` e não num router por causa de um incidente (Eduardo,
29/09/2026): em 28/09 cinco vídeos entraram pelo "conceito próprio" do portal
(`portal_criativos.propor_video`) com `marca="uranyx"` e o SKU preenchidos,
mas com `marca_id` e `product_id` NULL — só o PATCH da aba Criativos resolvia
o texto, e o portal gravava o texto cru. Resultado: "sem legenda" na tela, o
robô de postagem ignorando os vídeos (filtra por `marca_id`) e nenhuma legenda
do produto. Com a resolução num lugar só, todo caminho que cria criativo — a
aba interna, os roteiros e as duas portas do portal — chama a MESMA função, e
um caminho novo não tem outra de onde copiar.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Marca, Product, ProductLink


async def _marca_id_do_texto(session: AsyncSession, marca: str | None) -> UUID | None:
    """Resolve o texto livre da coluna `marca` para o id do cadastro.

    A coluna `marca` é texto digitado na célula da planilha; `marca_id` é a
    ligação de verdade com Cadastros › Marcas, e é ELA que o robô de postagem
    usa pra decidir em quais contas o vídeo pode sair. Sem esta resolução as
    duas divergem calado: quem troca a marca na tela muda só o texto, o id
    continua apontando pra marca antiga, e o robô recusa a publicação dizendo
    "essa conta é de outra marca" numa linha que na tela parece certa.

    Casa por `slug` e também por `nome` (a marca renomeada mantém o slug
    antigo — "charlots" com slug "poofy" é o caso real que provocou isto).
    """
    alvo = (marca or "").strip().lower()
    if not alvo:
        return None
    return await session.scalar(
        select(Marca.id).where(
            or_(func.lower(Marca.slug) == alvo, func.lower(Marca.nome) == alvo)
        ).limit(1)
    )


async def _product_id_do_sku(session: AsyncSession, sku: str | None) -> UUID | None:
    """Resolve o SKU do criativo para o id do produto, pela ponte dos anúncios.

    Medido nos 41 criativos de produção: `product_links.external_sku` casa
    39; `pricing_products` casa 21 e `products.sku`, zero. Por isso a corrente
    é criativo → product_links → products, e não o SKU cru do cadastro.

    Casa pelo SKU inteiro OU pela BASE (`dg017.pi` → `dg017`), porque o
    sufixo é variante de cor e o anúncio costuma estar cadastrado só na base.
    O casamento exato ganha do casamento por base, e o desempate segue por
    data/id: cor diferente do mesmo produto cai no mesmo `product_id`, mas a
    ordem precisa ser determinística — senão a legenda troca de produto entre
    dois salvamentos sem ninguém mexer em nada.

    Resolvido no SALVAMENTO, nunca na hora de publicar. Casar string no
    instante do post é o que faz a legenda mudar sozinha quando alguém
    renomeia um anúncio — mesmo motivo do `_marca_id_do_texto` acima.

    Quando nem o exato nem a base têm anúncio, sobram dois degraus (Eduardo,
    24/09/2026 — três criativos `dg046.sp` saíram com a legenda genérica da
    marca: o anúncio existe só como `dg046.pi`, e o `dg046.sp` só no
    cadastro):

      IRMÃO   anúncio de OUTRA variante da mesma base, avulso (sem "+", que é
              kit). Só vale quando todos os irmãos apontam pro MESMO produto:
              o `dg023` tem irmãos avulso, usado e kit 2 — escolher um deles
              por sorteio de data faria a legenda falar de celular usado.
      CADASTRO `products.sku` igual ao SKU inteiro. Identifica o produto,
              mas sem anúncio não traz a frase de ficha (bateria, tela…).

    O irmão ganha do cadastro quando os dois dão o MESMO nome — é o mesmo
    aparelho, e pelo irmão vêm o nome E a ficha, e o Desempenho junta os
    vídeos das duas variantes no mesmo produto. Com nomes diferentes, o
    cadastro ganha: o SKU inteiro é a identidade, o irmão é palpite.

    Irmão USADO não conta (29/09/2026): o `dg019` da agência não tem anúncio
    nem cadastro, e o único irmão avulso era o `dg019.us` — "USADO - AVULSO".
    O vídeo de aparelho novo ia ligar no usado e a legenda com `{{ produto }}`
    publicaria "USADO" no Instagram. Quem quer o usado escreve `.us` e casa
    pelo exato, lá em cima.
    """
    alvo = (sku or "").strip().lower()
    if not alvo:
        return None
    base = alvo.split(".")[0]
    externo = func.lower(func.btrim(ProductLink.external_sku))
    direto = await session.scalar(
        select(ProductLink.product_id)
        .where(externo.in_([alvo, base]))
        .order_by((externo == alvo).desc(), ProductLink.created_at, ProductLink.id)
        .limit(1)
    )
    if direto is not None:
        return direto

    irmaos = (
        await session.execute(
            select(ProductLink.product_id)
            .join(Product, Product.id == ProductLink.product_id)
            .where(
                func.split_part(externo, ".", 1) == base,
                ~externo.contains("+"),
                ~externo.like("%.us"),
                ~Product.name.ilike("%usado%"),
            )
            .distinct()
        )
    ).scalars().all()
    irmao = irmaos[0] if len(irmaos) == 1 else None

    cadastro = (
        await session.execute(
            select(Product.id, Product.name)
            .where(func.lower(func.btrim(Product.sku)) == alvo)
            .order_by(Product.created_at, Product.id)
            .limit(1)
        )
    ).first()
    if cadastro is None:
        return irmao
    if irmao is None:
        return cadastro.id
    nome_irmao = await session.scalar(select(Product.name).where(Product.id == irmao))
    mesmo_nome = (nome_irmao or "").strip().lower() == (cadastro.name or "").strip().lower()
    return irmao if mesmo_nome else cadastro.id
