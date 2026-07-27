from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import Boolean, ForeignKey, Integer, Numeric, Text, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class NfFaturador(Base, TimestampMixin):
    """Cadastro do FATURADOR (emissor da NF). Cada linha é um TIPO de faturador,
    e a lista é EXTENSÍVEL — o admin pode incluir um tipo novo manualmente e a
    regra dele é programada depois.

    Tipos iniciais (spec 25/07): bling avulso, bling avulso celular, bling
    exclusivo (0,1%), upseller 2%, upseller 1%, upseller 70%, upseller 100%.

    Como cada tipo emite:
    - `modo` = destino do fluxo: 'bling' (manda uma planilha p/ outro Bling) ou
      'upseller' (manda pro serviço Upseller).
    - `nf_cheia` = True emite pelo valor cheio do pedido (avulso); False usa
      `percentual` do valor (exclusivo 0,1% / upseller 2,1,70,100%).
    - `sku_fonte` = de onde vem o SKU: 'principal' (SKU do produto no Bling
      principal) ou 'a001' (SKU fixo a001).
    - `nome_fonte` = 'produto' (nome do produto) ou 'embalagem' (nome fixo).
    - `ncm` = NCM default (4202.12.10 na maioria; 3923.21.10 nos upseller de
      mala 70/100). A Joana valida/corrige o NCM quando não bate.
    - `ads_power`/`usuario`/`senha_enc` = credenciais do destino (AdsPower da
      contabilidade). Senha guardada criptografada.
    """

    __tablename__ = "nf_faturador"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    nome: Mapped[str] = mapped_column(Text, nullable=False)
    # 'bling' | 'upseller' (destino do fluxo de emissão).
    modo: Mapped[str] = mapped_column(Text, nullable=False)
    # True = NF pelo valor cheio do pedido; False = usa percentual.
    nf_cheia: Mapped[bool] = mapped_column(
        nullable=False, default=False, server_default=text("false")
    )
    # Percentual do valor a emitir quando não é NF cheia (0.1 / 2 / 1 / 70 / 100).
    percentual: Mapped[Decimal | None] = mapped_column(Numeric(6, 3), nullable=True)
    # 'principal' (SKU do produto do Bling principal) | 'a001' (SKU fixo).
    sku_fonte: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 'produto' (nome do produto) | 'embalagem' (nome fixo).
    nome_fonte: Mapped[str | None] = mapped_column(Text, nullable=True)
    ncm: Mapped[str | None] = mapped_column(Text, nullable=True)
    ads_power: Mapped[str | None] = mapped_column(Text, nullable=True)
    usuario: Mapped[str | None] = mapped_column(Text, nullable=True)
    senha_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    observacao: Mapped[str | None] = mapped_column(Text, nullable=True)
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    created_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )


class NfCatalogoMala(Base, TimestampMixin):
    """Catálogo de valor CHEIO da NF de MALA (aba `catalogo mala` do xlsx). A NF
    cheia de mala NÃO usa o valor de VENDA — usa um valor fixo por (modelo,
    tamanho), casado com o NCM 4202.12.10.

    - `modelo` = rótulo do catálogo (abs / pp / pp ziper duplo + roda / me1 /
      me2 / …). É o nome do modelo tal como está na planilha.
    - `tamanho` = tamanho (ou faixa) do catálogo. Pode ser um único ('20') ou uma
      FAIXA com segmentos separados por ponto ('08.10' = 8 e 10). NULL nos itens
      sem tamanho (acessórios: toy, encosto, mochila, kits).
    - `valor` = valor cheio da NF pra essa (modelo, tamanho).
    - `sku_base` = VÍNCULO editável com o código-base do SKU da mala (ex. `b001`).
      É o que amarra o catálogo ao produto: o SKU carrega base+tamanho, e o
      resolver casa `sku_base` + `tamanho`. Começa NULL — o admin preenche na
      tela (não dá pra derivar com segurança, é valor fiscal). Enquanto NULL, o
      motor cai no valor de venda (comportamento seguro atual).
    - `ncm` = NCM da NF (default 4202.12.10).
    """

    __tablename__ = "nf_catalogo_mala"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    modelo: Mapped[str] = mapped_column(Text, nullable=False)
    tamanho: Mapped[str | None] = mapped_column(Text, nullable=True)
    valor: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    # Código-base do SKU da mala (ex. b001). NULL = sem vínculo (motor usa venda).
    sku_base: Mapped[str | None] = mapped_column(Text, nullable=True)
    ncm: Mapped[str | None] = mapped_column(
        Text, nullable=True, server_default=text("'4202.12.10'")
    )
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    created_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )


class NfEtiqueta(Base, TimestampMixin):
    """Cadastro da ETIQUETA (onde a NF já emitida é inserida na plataforma p/
    liberar a etiqueta). Excel aba NF R14–R22: `plataforma | regra etiqueta`.
    Lista EXTENSÍVEL.

    Regras (spec 25/07):
    - `plataforma` = amazon / ml / shopee / tiktok / magalu / temu / shein /
      aliexpress / …
    - `modo` = 'amazon' (insere a NF no site da Amazon, agenda e libera a
      etiqueta — usa AdsPower) ou 'upseller' (insere tudo pelo Upseller e
      imprime a etiqueta por lá). Só a Amazon foge do upseller. NULL =
      plataforma ainda sem regra (temu/shein/aliexpress).
    - `ads_power` = perfil AdsPower (usado no modo amazon).
    """

    __tablename__ = "nf_etiqueta"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    plataforma: Mapped[str] = mapped_column(Text, nullable=False)
    # 'amazon' | 'upseller' | NULL (sem regra ainda).
    modo: Mapped[str | None] = mapped_column(Text, nullable=True)
    ads_power: Mapped[str | None] = mapped_column(Text, nullable=True)
    observacao: Mapped[str | None] = mapped_column(Text, nullable=True)
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    created_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )


class NfFaturamento(Base, TimestampMixin):
    """Status por ETAPA de um pedido no fluxo de NF automática (faturamento →
    etiqueta → impressão). Uma linha por pedido; a AUTOMAÇÃO (fases seguintes)
    grava/atualiza os status aqui. O painel (aba NF R37–R39) LÊ isso via LEFT
    JOIN com os pedidos do Bling — pedido sem linha aqui aparece 'pendente'.

    Cada `status_*` é um texto curto ('ok' | 'erro' | 'pendente' | ...) e o
    `erro_*` guarda a mensagem quando a etapa falhou (ex. "Erro de impressão").
    """

    __tablename__ = "nf_faturamento"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    # Número do pedido no Bling (chave de casamento com bling_orders.numero).
    pedido_bling: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    status_faturamento: Mapped[str | None] = mapped_column(Text, nullable=True)
    erro_faturamento: Mapped[str | None] = mapped_column(Text, nullable=True)
    status_etiqueta: Mapped[str | None] = mapped_column(Text, nullable=True)
    erro_etiqueta: Mapped[str | None] = mapped_column(Text, nullable=True)
    status_impressao: Mapped[str | None] = mapped_column(Text, nullable=True)
    erro_impressao: Mapped[str | None] = mapped_column(Text, nullable=True)


class NfImpressao(Base, TimestampMixin):
    """Cadastro da IMPRESSÃO (como a etiqueta é impressa depois que a NF foi
    emitida + inserida na plataforma). Excel aba NF R26–R29:
    `tipo | regra impressao | visualização | etiqueta | declaração | nota`.
    Lista EXTENSÍVEL. Nota: uma loja pode ter 2 faturadores/impressões.

    3 tipos iniciais (spec 25/07):
    - `agencia`: pega etiqueta + declaração no upseller → Controle de Estoque
      (usa_etiqueta + usa_declaracao). Declaração NÃO é alterada.
    - `correios`: pega etiqueta + NF no upseller (Correios não aceita
      declaração, só NF) → usa_etiqueta + usa_nota. Usado pelo Mercado Livre.
    - `proprio`: faz a etiqueta no Melhor Envio (só a Amazon), confere o valor
      vs frete projetado (Tabela de Preços → Contas) → usa_etiqueta + usa_nota.

    `visualizacao` = regra de tratamento ao imprimir (remetente=destinatário,
    apaga número/cód. barras/chave da NF, apaga nome do marketplace).
    """

    __tablename__ = "nf_impressao"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    tipo: Mapped[str] = mapped_column(Text, nullable=False)
    observacao: Mapped[str | None] = mapped_column(Text, nullable=True)
    visualizacao: Mapped[str | None] = mapped_column(Text, nullable=True)
    usa_etiqueta: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    usa_declaracao: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    usa_nota: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    created_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
