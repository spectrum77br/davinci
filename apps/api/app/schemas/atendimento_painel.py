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


class TrocaAbertaOut(BaseModel):
    """A troca de produto em andamento no pedido (item 4, fase 4c, 07/10/2026) — sem custo.

    `services/atendimento/troca.resumo_aberta`. Aberta = qualquer estado
    menos `concluida`/`abortada`; parada no meio, a tela oferece Retomar.
    """

    id: str
    # iniciada, item_trocado, em_atendido, em_aberto, nf_liberada, incerta.
    estado: str
    sku_antigo: str
    sku_novo: str
    nivel: int
    # O robô de lote trocou sozinho (nível 0).
    automatica: bool = False
    criado_por_nome: str | None = None
    created_at: datetime | None = None
    # Onde e por que parou (o código estável e a frase).
    codigo_erro: str | None = None
    erro: str | None = None
    # Ninguém está conduzindo a troca agora: dá para Retomar.
    pode_retomar: bool = False


class OfertaEnvioOut(BaseModel):
    """O botão "Enviar oferta" pode? (item 4, fase 4d, 07/10/2026)

    `services/atendimento/troca_oferta.situacao_da_oferta`: as travas da rota
    POST /pedidos/{n}/troca/oferta que não dependem do produto, e as do envio.
    Desligado, a tela mostra o botão cinza com o `texto_motivo`.
    """

    disponivel: bool = False
    # O código estável (o mesmo do 409 da rota): troca_desligada,
    # pedido_fora_do_piloto, motivo_nao_permite, troca_em_andamento,
    # sem_conversa, atendimento_so_leitura, envio_desligado, canal_nao_envia
    # ou outro do envio; `falhou` = não deu para conferir. None = pode.
    motivo: str | None = None
    texto_motivo: str | None = None
    # Podendo: a fala mais recente da conversa quando o botão foi montado — a
    # tela a devolve como `ultima_vista_id` (409 `conversa_mudou` se chegou
    # fala do cliente ou da loja depois).
    ultima_mensagem_id: UUID | None = None


class TrocaEnvioOut(BaseModel):
    """O botão "Trocar" pode? (item 4, fase 4c, 08/10/2026)

    `services/atendimento/troca.situacao_da_troca`: as travas da troca que
    não dependem do produto escolhido (quem pode, a chave, o piloto, o
    motivo, a troca aberta e as do pedido), só banco. Desligado, a tela
    mostra o botão cinza com o `texto_motivo`; a prévia confere tudo de novo.
    """

    disponivel: bool = False
    # O código estável (o mesmo do 409 da prévia): troca_desligada,
    # pedido_fora_do_piloto, motivo_nao_permite, plataforma_sem_conferencia,
    # em_fila_nf… ou atendimento_so_leitura; `falhou` = não deu para conferir.
    motivo: str | None = None
    texto_motivo: str | None = None


class AgCancelamentoOut(BaseModel):
    """Por que o pedido está em "Aguardando Cancelamento" (83955) — item 4, 02/10/2026.

    O motivo de `ag_cancelamento.classificar` com o pedido que o painel já
    leu (`painel.ag_cancelamento_do_pedido`): nenhuma consulta nem GET a
    mais. Só leitura.
    """

    # sem_estoque, restricao_envio, margem_trava, margem_reprovada,
    # pedido_cliente, cancelado_plataforma, pos_nf_manual, manual — ou
    # `desconhecido` (não deu para conferir: o lado seguro, sem falar em
    # cancelamento), ou `em_analise` (o motivo da Margem para quem não vê a
    # Margem: `painel.mascarar_motivo`).
    codigo: str
    titulo: str
    # O texto INTERNO do motivo (pode falar da Margem: é a equipe que lê).
    texto: str
    # == a etiqueta Ag. cancelamento (`etiqueta_fatos.ag_cancelamento_visivel`).
    # Em `desconhecido` vai False sem ter sido conferida (a tela não lê).
    etiqueta: bool = False
    # Pode falar em cancelamento com o comprador?
    fala_cancelamento: bool = False
    # A troca de produto pode ser sugerida (só falta de estoque).
    pode_sugerir_troca: bool = False
    # Os SKUs em falta (só com a marca de falta de estoque viva): os do erro
    # que ainda estão no pedido.
    skus: list[str] = Field(default_factory=list)
    # A trava da Margem venceu, mas a NF também marcou falta de estoque ou restrição.
    conflito: str | None = None
    # A 1ª linha das Observações do Bling — só no "movido à mão" (manual e
    # pos_nf_manual), onde a equipe costuma escrever o porquê.
    observacao_topo: str | None = None
    # A troca de produto em andamento no pedido (fase 4c); None sem ela.
    troca_aberta: TrocaAbertaOut | None = None
    # O botão "Trocar" (fase 4c): pode, ou o porquê de não.
    troca_envio: TrocaEnvioOut = Field(default_factory=TrocaEnvioOut)
    # O botão "Enviar oferta" (fase 4d): pode, ou o porquê de não.
    oferta_envio: OfertaEnvioOut = Field(default_factory=OfertaEnvioOut)


class SugestaoTrocaOut(BaseModel):
    """Um produto parecido para trocar o item em falta (item 4, fase 4b, 05/10/2026).

    De `troca_sugestoes.sugerir`, com o estoque do DaVinci (não o ao vivo).
    """

    sku: str
    nome: str | None = None
    # 0 = o mesmo produto em outro lote (o robô de lote faria; sem aceite);
    # 1 = o mesmo modelo em outra cor (ou o mesmo produto, com aceite);
    # 2 = outro modelo com a mesma especificação.
    nivel: int
    # O mesmo produto, de outro lote de venda.
    mesmo_produto: bool = False
    # `products.stock` e a hora da linha do produto.
    estoque: int | None = None
    estoque_em: datetime | None = None
    # `products.bling_product_id` (a troca da 4c usa).
    produto_id: int | None = None
    # Quanto o NOSSO custo muda (%): só para quem vê a Margem (senão None).
    dif_custo_pct: float | None = None
    # None = elegível; sem_estoque, custo_acima ou custo_abaixo_piso (esmaecida) —
    # para quem não vê a Margem, os do custo vêm `fora_da_regra`.
    motivo_fora: str | None = None
    # A oferta ao comprador para ESTA sugestão (só na elegível; o nível 0 não tem).
    texto_oferta: str | None = None


class ItemTrocaOut(BaseModel):
    """As sugestões de UM item em falta do pedido."""

    sku_original: str
    quantidade: int
    nome_original: str | None = None
    # Até 3 elegíveis, na ordem (com estoque, nível, custo, estoque).
    sugestoes: list[SugestaoTrocaOut] = Field(default_factory=list)
    # Até 3 parecidas que ficaram de fora (`motivo_fora`).
    fora: list[SugestaoTrocaOut] = Field(default_factory=list)
    # O texto da oferta da 1ª sugestão (None no nível 0 ou sem sugestão).
    texto_oferta: str | None = None
    # Nenhum parecido no catálogo (nem de fora).
    sem_parecido: bool = False
    # O porquê de não haver parecido (fora do catálogo, salvado, sem custo…).
    motivo_sem_sugestao: str | None = None


class SugestoesTrocaOut(BaseModel):
    """Sugestões de troca do pedido em falta de estoque (item 4, fase 4b). Só leitura, sem Bling."""

    itens: list[ItemTrocaOut] = Field(default_factory=list)
    # "o item em falta já não está no pedido: <skus>" (trocado à mão depois da marca).
    aviso: str | None = None
    # Quando o catálogo (estoque do DaVinci) foi lido — memória de 10 min.
    catalogo_lido_em: datetime | None = None
    # Quem pediu vê a Margem (recebe `dif_custo_pct`).
    ve_custo: bool = False
    # A montagem quebrou (o resto do painel segue).
    falhou: bool = False


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
    # Só com o pedido em "Aguardando Cancelamento" (item 4); None no resto.
    ag_cancelamento: AgCancelamentoOut | None = None
    # Só na falta de estoque com `atendimento_troca_sugestoes_ativa` (fase 4b).
    sugestoes_troca: SugestoesTrocaOut | None = None
    # A troca de produto aberta do pedido em QUALQUER situação (fase 4c): a
    # parada no meio com o pedido fora de 83955 (em 9, ou em 6 sem a NF
    # liberada) só aparece aqui — com o Retomar.
    troca_aberta: TrocaAbertaOut | None = None
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
