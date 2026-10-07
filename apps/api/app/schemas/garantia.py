"""Contrato do Painel de Garantia Uranyx (routers/garantias.py).

CPF/NF/nome são conferidos no router (erro com `campo`, embaixo do campo na
tela); aqui ficam só tipos, tamanhos e o `extra="forbid"` — que é o que
recusa (422) quem tentar mandar a data inicial ou os prazos (RN01).
"""

from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _texto(v):
    return v.strip() if isinstance(v, str) else v


class GarantiaCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Nº do pedido no Bling ou no marketplace.
    pedido: str = Field(min_length=1, max_length=64)
    nf_numero: str = Field(min_length=1, max_length=20)
    nf_serie: str | None = Field(default=None, max_length=5)
    cliente_nome: str = Field(min_length=3, max_length=200)
    cpf: str = Field(min_length=11, max_length=20)

    _v_texto = field_validator(
        "pedido", "nf_numero", "nf_serie", "cliente_nome", "cpf", mode="before"
    )(_texto)


class GarantiaUpdate(BaseModel):
    """Correção do que foi INFORMADO. Data inicial, prazos e data de cadastro
    não estão aqui de propósito (RN01/RN05)."""

    model_config = ConfigDict(extra="forbid")

    pedido: str | None = Field(default=None, min_length=1, max_length=64)
    nf_numero: str | None = Field(default=None, min_length=1, max_length=20)
    nf_serie: str | None = Field(default=None, max_length=5)
    cliente_nome: str | None = Field(default=None, min_length=3, max_length=200)
    cpf: str | None = Field(default=None, min_length=11, max_length=20)

    _v_texto = field_validator(
        "pedido", "nf_numero", "nf_serie", "cliente_nome", "cpf", mode="before"
    )(_texto)


class AtendimentoCreate(BaseModel):
    """Vincular à garantia (§5.1): o resto vem da conversa."""

    model_config = ConfigDict(extra="forbid")

    conversa_id: str = Field(min_length=1, max_length=64)
    tipo_problema: Literal["hardware", "software"]
    solucao: str = Field(min_length=3, max_length=4000)
    # Vazio = resumo automático (as últimas falas do cliente).
    resumo: str | None = Field(default=None, max_length=4000)
    # Vazio = as mensagens mais recentes da conversa (sem as de sistema).
    mensagem_ids: list[UUID] | None = Field(default=None, max_length=200)

    _v_texto = field_validator("solucao", "resumo", "conversa_id", mode="before")(_texto)


# Filtro de status da lista: os 4 do documento, o recorte "HW vence em 30d"
# do cartão e o "Aguardando" de pedido que o Bling já dá como entregue.
StatusFiltro = Literal[
    "todos",
    "aguardando_entrega",
    "entregue_sem_data",
    "ativa",
    "somente_software",
    "expirada",
    "hw_vence_30d",
]


class ListaFiltro(BaseModel):
    """POST /api/garantias/lista. POST (e não GET com query) porque a busca
    leva nome e CPF: no corpo, eles não vão para o log de acesso do uvicorn
    nem do Caddy (que gravam o endereço com a query)."""

    model_config = ConfigDict(extra="forbid")

    busca: str | None = Field(default=None, max_length=100)
    status: StatusFiltro = "todos"
    periodo: Literal["cadastro", "entrega"] = "cadastro"
    de: date | None = None
    ate: date | None = None
    com_atendimento_no_mes: bool = False
    limite: int = Field(default=200, ge=1, le=500)
    offset: int = Field(default=0, ge=0)

    _v_texto = field_validator("busca", mode="before")(_texto)


class BuscaVinculo(BaseModel):
    """POST /api/garantias/busca ("Vincular à garantia"): o termo no corpo,
    pelo mesmo motivo da lista."""

    model_config = ConfigDict(extra="forbid")

    q: str = Field(min_length=1, max_length=100)

    _v_texto = field_validator("q", mode="before")(_texto)


class PessoaRef(BaseModel):
    id: UUID | None = None
    nome: str


class PrazoOut(BaseModel):
    fim: date
    dias_total: int
    dias_restantes: int
    coberto_hoje: bool


class EntregaOut(BaseModel):
    data: date | None = None
    origem: str | None = None
    origem_rotulo: str | None = None
    # Instante cru da fonte (UTC) e a última conferência do robô.
    em: datetime | None = None
    verificada_em: datetime | None = None


class GarantiaLinhaOut(BaseModel):
    id: int
    cliente_nome: str
    cpf_mascarado: str | None
    nf_numero: str
    nf_serie: str
    pedido_bling: str
    pedido_marketplace: str | None = None
    plataforma: str | None = None
    conta: str | None = None
    data_inicio: date | None = None
    fim_hardware: date | None = None
    fim_software: date | None = None
    status: str
    status_rotulo: str
    # "Aguardando entrega" de pedido que o Bling JÁ dá como entregue: falta
    # a data no DaVinci (o rótulo vira "Entregue — sem data no DaVinci").
    entregue_sem_data: bool = False
    atendimentos: int = 0
    criado_em: datetime


class IndicadoresOut(BaseModel):
    ativas: int
    somente_software: int
    hw_vence_30d: int
    atendimentos_no_mes: int
    aguardando_entrega: int
    # Dentro de `aguardando_entrega`: as que o Bling já dá como entregues.
    entregue_sem_data: int
    expiradas: int
    total: int


class GarantiaListaOut(BaseModel):
    itens: list[GarantiaLinhaOut]
    total: int
    indicadores: IndicadoresOut
    hoje: date


class ItemOut(BaseModel):
    descricao: str | None = None
    sku: str | None = None
    quantidade: int | None = None
    uranyx: bool = False


class AnexoOut(BaseModel):
    mensagem_id: str | None = None
    tipo: str
    nome: str | None = None
    url_original: str | None = None
    baixado: bool = False
    # Por que não baixou: sem_link (ML), link_expirado, maior_que_8mb,
    # tipo_nao_suportado, host_nao_permitido, limite_de_anexos, erro_ao_baixar.
    motivo: str | None = None
    # GET autenticado do arquivo guardado.
    arquivo_url: str | None = None
    content_type: str | None = None
    tamanho: int | None = None


class AtendimentoOut(BaseModel):
    id: int
    garantia_id: int
    data_atendimento: datetime
    atendente: PessoaRef
    conversa_id: UUID
    conversa_link: str
    conversa_plataforma: str | None = None
    conversa_canal: str | None = None
    conversa_conta: str | None = None
    conversa_pedido: str | None = None
    resumo: str
    mensagens: list[dict[str, Any]]
    anexos: list[AnexoOut]
    tipo_problema: str
    # Congelada no vínculo (auditoria) …
    cobertura: str
    cobertura_rotulo: str
    fim_considerado: date | None = None
    # … e a de hoje, com os prazos atuais (difere se a entrega foi corrigida
    # depois, ou se a garantia estava Aguardando entrega).
    cobertura_hoje: str
    cobertura_hoje_rotulo: str
    solucao: str
    criado_em: datetime


class PermissoesOut(BaseModel):
    cadastrar: bool
    registrar_atendimento: bool
    ver_cpf: bool
    ver_log: bool


class GarantiaDetalheOut(GarantiaLinhaOut):
    # Completo só para quem pode ver (`garantias_cpf`); senão, mascarado.
    cpf: str | None
    cpf_completo: bool
    nf_chave: str | None = None
    nf_emitente_cnpj: str | None = None
    itens: list[ItemOut]
    entrega: EntregaOut
    hardware: PrazoOut | None = None
    software: PrazoOut | None = None
    criado_por: PessoaRef
    atualizado_em: datetime | None = None
    atualizado_por: PessoaRef | None = None
    atendimentos_lista: list[AtendimentoOut]
    permissoes: PermissoesOut


class GarantiaSalvaOut(GarantiaDetalheOut):
    # Avisos que não bloquearam: pedido_sem_produto_uranyx,
    # pedido_ja_tem_garantia, cpf_diferente_do_pedido, nf_nao_e_do_pedido,
    # nf_de_embalagem, aguardando_entrega, entregue_sem_data.
    # O CPF completo só sai no GET do detalhe (que grava "consultou"); a
    # resposta de cadastrar/corrigir/conferir a entrega vem mascarada.
    avisos: list[str] = []


class LogOut(BaseModel):
    id: int
    acao: str
    campo: str | None = None
    valor_anterior: str | None = None
    valor_novo: str | None = None
    detalhe: str | None = None
    pessoa: PessoaRef
    em: datetime


class NotaSugeridaOut(BaseModel):
    numero: str
    serie: str
    chave: str
    emitida_em: datetime | None = None
    valor: float | None = None
    papel: Literal["produto", "embalagem", "indefinida"]


class PrazosPreviaOut(BaseModel):
    data_inicio: date | None = None
    fim_hardware: date | None = None
    fim_software: date | None = None
    status: str
    status_rotulo: str
    entregue_sem_data: bool = False


class PedidoParaCadastroOut(BaseModel):
    pedido_bling: str
    pedido_marketplace: str | None = None
    plataforma: str | None = None
    conta: str | None = None
    data_pedido: datetime | None = None
    situacao: str | None = None
    itens: list[ItemOut]
    produto_uranyx: bool
    entrega: EntregaOut
    prazos: PrazosPreviaOut
    notas: list[NotaSugeridaOut]
    nf_sugerida: NotaSugeridaOut | None = None
    nome_sugerido: str | None = None
    # 'nf' (comprador da NF de produto) | 'destinatario_entrega'
    nome_origem: str | None = None
    # Completo só para quem vê CPF; o mascarado sempre que houver.
    cpf_sugerido: str | None = None
    cpf_sugerido_mascarado: str | None = None
    garantias_existentes: list[GarantiaLinhaOut]
    avisos: list[str]


class VinculoOut(BaseModel):
    atendimento_id: int
    garantia_id: int
    data_atendimento: datetime
    tipo_problema: str
    cobertura: str
    cobertura_rotulo: str


class SituacaoConversaOut(BaseModel):
    """O bloco "Garantia" do painel da conversa. Nunca traz o CPF."""

    conversa_id: UUID
    pedido_encontrado: bool
    pedido_bling: str | None = None
    pedido_marketplace: str | None = None
    produto_uranyx: bool = False
    cpf_conhecido: bool = False
    # §5.3: o CPF do pedido não tem garantia cadastrada (pedido com produto
    # Uranyx). Só acende quando o DaVinci SABE o CPF.
    alerta_cpf_sem_garantia: bool = False
    garantias: list[GarantiaLinhaOut]
    # O que a busca do "Vincular à garantia" já vem preenchida (ponto 4).
    busca_sugerida: str | None = None
    vinculos: list[VinculoOut]


class RegrasOut(BaseModel):
    meses_hardware: int
    meses_software: int
    ultimo_dia_coberto: bool
    cadastro_antes_da_entrega: bool
    garantia_por: str
    vinculo_automatico: bool
    recalcular_quando_entrega_mudar: bool
    dias_alerta_hardware: int
    # Data do atendimento sem mensagens escolhidas: a 1ª do cliente no trecho
    # atual da conversa (pausa de até N dias não abre trecho novo).
    dias_pausa_atendimento: int
    status: dict[str, str]
    coberturas: dict[str, str]
    origens_entrega: dict[str, str]
