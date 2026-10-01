"""Contratos do painel do pedido, da nota interna, da foto e do AdsPower (01/10/2026).

`routers/atendimento_painel.py` + `services/atendimento/painel.py`. Tudo
opcional por bloco: cada bloco do painel falha sozinho e volta vazio, com
`falhou=True` ou o `erro` dele — a tela desenha o que vier.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.atendimento import MensagemOut


class PedidoPainelOut(BaseModel):
    numero_bling: str
    bling_id: int | None = None
    numeroloja: str | None = None
    situacao_id: str | None = None
    situacao: str | None = None


class LoteOut(BaseModel):
    lote: str
    sku: str
    saldo: int | None = None
    atualizado_em: datetime | None = None
    # ci/pi/ra/sa/sp = lotes de venda; cd (Centro de Distribuição) e us (usado) não.
    de_venda: bool = True
    rotulo: str | None = None
    proprio: bool = False
    ativo: bool = True


class ComponenteOut(BaseModel):
    sku: str | None = None
    nome: str | None = None
    existe: bool = False
    quantidade_por_kit: float = 1.0
    necessario: float | None = None
    saldo: int | None = None
    atualizado_em: datetime | None = None
    cobre: bool | None = None


class ItemEstoqueOut(BaseModel):
    sku: str | None = None
    descricao: str | None = None
    nome: str | None = None
    # None = não deu para ler (o bloco falhou para este item).
    existe: bool | None = None
    ativo: bool = False
    quantidade: int | None = None
    # `products.stock`: o saldo virtual do Bling (já sem o reservado).
    saldo: int | None = None
    atualizado_em: datetime | None = None
    cobre: bool | None = None
    lote: str | None = None
    lotes: list[LoteOut] = Field(default_factory=list)
    saldo_outros_lotes: int = 0
    kit: bool = False
    componentes: list[ComponenteOut] = Field(default_factory=list)
    falhou: bool = False


class EstoqueOut(BaseModel):
    itens: list[ItemEstoqueOut] = Field(default_factory=list)
    # bling = itens do pedido no Bling; plataforma = do retrato da plataforma;
    # nenhum = sem itens conhecidos.
    fonte: str = "nenhum"
    falhou: bool = False


class ItemMargemOut(BaseModel):
    sku: str | None = None
    produto: str | None = None
    quantidade: int | None = None
    # Fração (0.165 = 16,5%), como na aba Margem.
    margem: float | None = None
    margem_minima: float | None = None
    abaixo_da_minima: bool | None = None
    status: str | None = None
    data_especial: bool = False
    aguardando_repasse: bool = False
    # Só administrador (como na aba Margem).
    lucro: float | None = None
    custo: float | None = None


class MargemOut(BaseModel):
    na_margem: bool = False
    status: str | None = None
    margem: float | None = None
    margem_minima: float | None = None
    abaixo_da_minima: bool | None = None
    lucro: float | None = None
    itens: list[ItemMargemOut] = Field(default_factory=list)
    aviso: str | None = None
    falhou: bool = False


class ObservacoesBlingOut(BaseModel):
    observacoes: str | None = None
    observacoes_internas: str | None = None
    lido_em: datetime | None = None
    do_cache: bool = False
    erro: str | None = None
    codigo: str | None = None


class LinkOut(BaseModel):
    url: str
    rotulo: str


class LinksOut(BaseModel):
    bling: str | None = None
    plataforma: LinkOut | None = None


class AdsPowerOut(BaseModel):
    # O nº do perfil no AdsPower (campo Servidor do store-info = serial_number).
    perfil: str | None = None
    # O id do perfil (`user_id` da API local), quando se sabe (espelho/robô).
    perfil_id: str | None = None
    perfil_nome: str | None = None
    loja: str | None = None
    store_info_id: str | None = None
    fonte: str | None = None
    # O perfil é o do robô do Mac mini (Temu/AliExpress).
    robo: bool = False
    aviso: str | None = None
    # Sem perfil: a frase do botão desativado (e o código estável).
    motivo: str | None = None
    codigo: str | None = None
    # O nº existe no espelho do AdsPower? None = não deu para conferir.
    no_espelho: bool | None = None


class EnvioFotoOut(BaseModel):
    pode: bool = False
    motivo: str | None = None
    codigo: str | None = None
    tipos: list[str] = Field(default_factory=list)
    max_bytes: int = 0
    legenda_obrigatoria: bool = False
    legenda_permitida: bool = False


class PainelOut(BaseModel):
    pedido: PedidoPainelOut | None = None
    estoque: EstoqueOut = Field(default_factory=EstoqueOut)
    margem: MargemOut | None = None
    # Quem não tem a permissão da Margem não recebe o bloco (a tela diz por quê).
    ve_margem: bool = False
    observacoes_bling: ObservacoesBlingOut | None = None
    links: LinksOut = Field(default_factory=LinksOut)
    adspower: AdsPowerOut = Field(default_factory=AdsPowerOut)
    envio_foto: EnvioFotoOut = Field(default_factory=EnvioFotoOut)
    gerado_em: datetime | None = None


class NotaIn(BaseModel):
    # O teto de verdade (e o "vazia") é do serviço, com o código em português.
    texto: str = Field(max_length=10_000)


class NotaOut(BaseModel):
    mensagem: MensagemOut


class AdsPowerAbertoIn(BaseModel):
    """O que a tela conta depois de chamar a API local do AdsPower."""

    conversa_id: UUID | None = None
    # aberto  = o AdsPower respondeu que abriu;
    # enviado = o pedido chegou ao AdsPower, mas o navegador não deixou ler a resposta;
    # erro    = não abriu (o `codigo` diz por quê).
    resultado: Literal["aberto", "enviado", "erro"]
    codigo: str | None = Field(default=None, max_length=40)


class AdsPowerAbertoOut(BaseModel):
    registrado: bool
    perfil: str | None = None
