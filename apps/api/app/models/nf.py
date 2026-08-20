from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    Numeric,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
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
    - `sku_fonte` = de onde vem o SKU: 'principal' (ou vazio) usa o SKU do
      produto no Bling principal; qualquer outro valor é o SKU LITERAL da NF
      ('a001' no bling avulso celular, 'e3'/'m200' nos upseller — lá o produto
      é casado pelo SKU e só existem os de embalagem/mala).
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
    # 'principal'/vazio = SKU do produto do Bling principal; senão SKU literal.
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
      me2 / …). É o nome do modelo tal como está na planilha. O resolver casa
      automático: a família da mala (M1..M6 → abs, P1..P6 → pp, ME1 → me1,
      ME2 → me2) vem do NOME do produto, então o SKU do pedido resolve o modelo
      sem vínculo manual.
    - `tamanho` = tamanho (ou faixa) do catálogo. Pode ser um único ('20') ou uma
      FAIXA com segmentos separados por ponto ('08.10' = 8 e 10). NULL nos itens
      sem tamanho (acessórios: toy, encosto, mochila, kits).
    - `valor` = valor cheio da NF pra essa (modelo, tamanho).
    - `ncm` = NCM da NF (default 4202.12.10).
    """

    __tablename__ = "nf_catalogo_mala"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    modelo: Mapped[str] = mapped_column(Text, nullable=False)
    tamanho: Mapped[str | None] = mapped_column(Text, nullable=True)
    valor: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
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


class NfEtiquetaHorario(Base, TimestampMixin):
    """QUANDO as etiquetas de cada plataforma são impressas no DaVinci.

    Mesma granularidade da aba Etiqueta: uma linha por PLATAFORMA, valendo pra
    todas as lojas dela.

    - `modo` = 'continuo' (imprime assim que a NF fecha — Shopee/TikTok, fluxo
      todo automático) ou 'horario' (só nos horários cadastrados — ML/Amazon).
    - `horarios` = lista de "HH:MM" no fuso de Brasília (America/Sao_Paulo),
      ex. ["10:00", "14:00"]. Vazia no modo contínuo.

    Por enquanto é só CADASTRO — nenhum worker lê isso ainda.
    """

    __tablename__ = "nf_etiqueta_horario"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    plataforma: Mapped[str] = mapped_column(Text, nullable=False)
    # 'continuo' | 'horario'
    modo: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'horario'"))
    horarios: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
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


class NfCommand(Base, TimestampMixin):
    """OUTBOX da importação da planilha avulsa (Fase 3a-4). Um comando =
    UM faturador com o subconjunto de pedidos daquela loja/AdsPower + o CSV
    CONGELADO no momento do enfileiramento (imune a drift dos dados).

    O executor LOCAL (marionete/AdsPower, fora deste repo) faz poll de
    `POST /nf-cadastro/agent/lease` (X-Agent-Token), abre o AdsPower do
    faturador, loga no Bling destino, importa a planilha como VENDA AVULSA e
    reporta em `POST /nf-cadastro/agent/commands/{id}/result`. O login
    (ads_power/usuario/senha descriptografada) é entregue no LEASE, nunca
    persistido no comando.

    - `numeros` = pedidos do Bling cobertos por este comando (JSONB).
    - `planilha` = CSV congelado (bytea) — o arquivo que o executor sobe.
    - `status` = pending → claimed → done | failed.
    - A cada done/failed a etapa `status_faturamento` de cada pedido em
      `nf_faturamento` é atualizada (ok / erro).
    """

    __tablename__ = "nf_command"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    faturador_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("nf_faturador.id", ondelete="SET NULL"),
        nullable=True,
    )
    action: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'import_avulsa'")
    )
    numeros: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    planilha: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    nome_arquivo: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'pending'")
    )
    result: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    source: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'manual'")
    )
    created_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # AdsPower do comando (etiqueta ML: perfil do cadastro Etiqueta, NÃO do
    # faturador). None → o lease cai no ads_power do faturador (fluxo antigo).
    ads_power: Mapped[str | None] = mapped_column(Text, nullable=True)


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


class NfEtiquetaArquivo(Base, TimestampMixin):
    """Etiqueta (já TRANSFORMADA pela regra de visualização — remetente =
    destinatário, sem número/cód. barras/chave da NF, sem nome do marketplace)
    guardada por pedido, pronta pra impressão em Controle de Estoque → Pedidos.

    O blob fica no próprio banco (bytea), servido por endpoint autenticado por
    cookie — mesmo desenho de `logistica_status_anexo`. Chaveada por
    `pedido_bling` (igual `nf_faturamento`): uma etiqueta por pedido, a etapa de
    transformação regrava (upsert) quando reprocessa.
    """

    __tablename__ = "nf_etiqueta_arquivo"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    # Número do pedido no Bling (chave de casamento com bling_orders.numero).
    pedido_bling: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    filename: Mapped[str] = mapped_column(Text, nullable=False)
    content_type: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default=text("0")
    )
    blob: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    # DANFE do Bling (fluxo correios/ML): a NF vem do Bling via "Gerar PDF
    # DANFE", capturada pela marionete e gravada aqui. Quando presente, o botão
    # "Imprimir Etiqueta" serve etiqueta + NF juntadas (correios não aceita
    # declaração). NULL = fluxo agência (só etiqueta).
    nf_pdf: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    nf_size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # Quando a etiqueta saiu da impressora pela PRIMEIRA vez (a reimpressão não
    # sobrescreve). NULL = nunca impressa. É o sinal que o Controle de Estoque
    # usa pra marcar "Impressa" e evitar duplicidade na impressão em lote.
    impressa_em: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
