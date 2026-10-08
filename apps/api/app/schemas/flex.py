"""Respostas da API do Flex por anúncio (routers/flex.py)."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class FlexContaOut(BaseModel):
    """Uma conta de `flex_contas`. `existe=False`: o id não é de uma conta
    ML/Shopee ativa (digitado errado, arquivada, outra plataforma).

    O Flex DA CONTA (`flex_conta`, conferido pelo motor de hora em hora — a
    tela não chama a plataforma): `flex_ativo` True = pode ter Flex (ML:
    assinatura "in"; Shopee: Entrega Direta ligada na loja), False = não
    pode, None = ainda não conferida. `flex_status` é o valor cru ("in",
    "out", "pending", "http_404", "sem_canal"…) e `flex_motivo` a frase que
    os anúncios dela mostram quando a conta não pode."""

    id: UUID
    nome: str | None = None
    plataforma: str | None = None
    existe: bool = True
    flex_ativo: bool | None = None
    flex_status: str | None = None
    flex_detalhe: str | None = None
    flex_motivo: str | None = None
    # De onde o motoboy do Flex sai (ML): CEP(s) só com dígitos e cidade(s)
    # da assinatura; `flex_origem_ok` True = na cidade do local de saída,
    # False = fora ou não lida (o motor não mexe na conta), None = conta sem
    # Flex (ou da Shopee).
    flex_origem_cep: str | None = None
    flex_origem_cidade: str | None = None
    flex_origem_ok: bool | None = None
    flex_lido_em: datetime | None = None
    flex_erro: str | None = None
    # Descoberta dos anúncios da conta (ML): quando, se leu tudo, quantos
    # ids e quantos o DaVinci não conhecia.
    descoberta_em: datetime | None = None
    descoberta_ok: bool | None = None
    descoberta_total: int | None = None
    descoberta_novos: int | None = None
    descoberta_erro: str | None = None


class FlexLocalOut(BaseModel):
    """De onde o motoboy do Flex sai (aba Flex › Local de saída): a cidade
    (como o ML mostra na saída do Flex de cada conta) e o lote do estoque de
    lá. O motor só mexe na conta cuja saída é nessa cidade."""

    cidade: str
    lote: str
    atualizado_em: datetime | None = None
    atualizado_por: str | None = None  # nome de quem trocou
    # Só na resposta da troca: anúncios com Flex LIGADO em contas que deixam
    # de ser controladas (a saída do Flex delas não é na cidade nova) — o
    # robô não mexe mais neles; a emergência continua desligando.
    ligados_fora: int | None = None


class FlexLocalIn(BaseModel):
    cidade: str = Field(min_length=1, max_length=100)
    lote: str = Field(min_length=2, max_length=3)
    # O local que a tela mostrava ao abrir o formulário: se outro admin trocou
    # nesse meio tempo, 409 (ninguém sobrescreve sem ver).
    cidade_antes: str | None = Field(default=None, max_length=200)
    lote_antes: str | None = Field(default=None, max_length=3)


class FlexConfigOut(BaseModel):
    """A configuração em vigor — os números como o motor os usa (já
    normalizados). Nenhum segredo."""

    modo: str
    pode_escrever: bool
    contas: list[FlexContaOut]
    n_liga: int
    n_desliga: int
    kits: bool
    max_anuncios_por_familia: int
    teto_escritas_por_rodada: int
    shopee_canais: list[str]
    shopee_escrita: bool
    intervalo_min: int
    pedido_no_sp: bool
    # Local de saída do Flex (editável por admin) e as cidades de saída que
    # o motor leu nas contas (sugestões para trocar).
    local: FlexLocalOut | None = None
    pode_alterar_local: bool = False
    cidades_vistas: list[str] = []
    lotes: list[str] = []


class FlexAnuncioOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    integration_id: UUID
    conta: str | None = None
    external_id: str
    plataforma: str
    titulo: str | None = None
    desejado: str
    motivo: str | None = None
    # O mesmo motivo numa frase para o dono (services/flex_textos) — o
    # `motivo` cru continua aqui para quem confere a trilha.
    motivo_claro: str | None = None
    observado: str | None = None
    observado_em: datetime | None = None
    aguardando_aprovacao: bool
    aprovado_por: UUID | None = None
    aprovado_em: datetime | None = None
    aplicado_em: datetime | None = None
    tentativas: int
    proxima_tentativa: datetime | None = None
    ultimo_erro: str | None = None
    recusa: str | None = None
    saldo_sp: int | None = None
    familias: str | None = None
    # Fila de leitura: leitura que falhou espera (`proxima_leitura`).
    leitura_em: datetime | None = None
    leitura_falhas: int = 0
    proxima_leitura: datetime | None = None
    # Status do anúncio na plataforma (active, paused, under_review,
    # inactive, closed) e quando foi visto.
    status_anuncio: str | None = None
    status_em: datetime | None = None
    atualizado_em: datetime


class FlexResumoOut(BaseModel):
    """Contagem de TODOS os anúncios avaliados (sem os filtros da lista) —
    o quadro do topo da tela."""

    # Anúncios que a regra avaliou (tem linha de estado) — inclui os que a
    # plataforma ainda não leu (`nao_lidos`): conferidos = avaliados − nao_lidos.
    avaliados: int = 0
    ligados: int = 0  # a plataforma diz que o Flex está ligado
    aguardando: int = 0  # esperando uma pessoa aprovar o ligar
    desligar: int = 0  # ligado na plataforma, mas a regra quer desligado
    nao_lidos: int = 0  # o DaVinci ainda não leu o estado na plataforma
    # Flex ligado (última leitura) em conta liberada que o robô NÃO controla:
    # sem Flex conferido, ou com a saída do Flex fora da cidade do local de
    # saída. Ninguém desliga sozinho — só a emergência ou uma pessoa.
    sem_controle: int = 0


class FlexAnunciosOut(BaseModel):
    total: int
    itens: list[FlexAnuncioOut]
    resumo: FlexResumoOut = Field(default_factory=FlexResumoOut)


class FlexAprovarOut(BaseModel):
    aprovado: bool
    modo: str
    aplicado: bool
    estado: FlexAnuncioOut | None = None
    motor: dict[str, Any] | None = None
    # A rodada do motor estava em andamento: a ligação foi para a fila do
    # worker (tenta de novo em seguida) — a aprovação continua valendo.
    na_fila: bool = False


class FlexEmergenciaOut(BaseModel):
    """Um "Desligar tudo": o job e o andamento (a tela consulta até
    `status` ser "concluida" ou "falhou"). `resumo`: alvos, desligados,
    restantes… — e `motivo` quando não havia o que fazer (sem job, `id`
    None)."""

    id: int | None = None
    status: str | None = None
    modo: str
    escreve: bool
    pedido_em: datetime | None = None
    iniciado_em: datetime | None = None
    terminado_em: datetime | None = None
    erro: str | None = None
    # Já havia uma emergência na fila/rodando: a resposta é ela (não cria
    # outra).
    ja_em_andamento: bool = False
    resumo: dict[str, Any] = Field(default_factory=dict)


class FlexSincronizarOut(BaseModel):
    enfileirado: bool
    modo: str
    motivo: str | None = None


class FlexLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    criado_em: datetime
    integration_id: UUID | None = None
    conta: str | None = None
    external_id: str | None = None
    plataforma: str | None = None
    bling_id: int | None = None
    sku: str | None = None
    saldo_sp: int | None = None
    acao: str
    estado_antes: str | None = None
    estado_depois: str | None = None
    modo: str
    resultado: str
    erro: str | None = None
    motivo: str | None = None
    por: UUID | None = None


class FlexPedidoOut(BaseModel):
    """Pedido Flex detectado (flex_pedido) com o que o Bling diz dele."""

    bling_id: int
    numero: str | None = None
    plataforma: str
    integration_id: UUID | None = None
    conta: str | None = None
    numeroloja: str | None = None
    envio_tipo: str | None = None
    prazo: datetime | None = None
    detectado_em: datetime
    no_sp: bool
    # O lote do local de saída deste pedido (flex_pedido.lote).
    lote: str = "sp"
    alerta: str | None = None
    situacao: str | None = None
    skus: list[str] = []
    # Saiu (em andamento/atendido) sem ter ido ao .sp: o Bling baixou o outro
    # lote e a peça saiu de São Bernardo — o saldo Flex segue descontando até
    # uma pessoa marcar que acertou o estoque no Bling.
    acerto_pendente: bool = False
    acertado_em: datetime | None = None


class FlexAcertoOut(BaseModel):
    bling_id: int
    acertado_em: datetime
