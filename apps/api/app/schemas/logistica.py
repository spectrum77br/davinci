from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, Field


class StatusDetalheOut(BaseModel):
    """Uma linha do balãozinho da coluna "Status Plataforma": o campo já
    traduzido + desde quando está assim."""

    campo: str
    rotulo: str
    valor: str
    # ISO-8601 UTC de quando esse campo mudou; None quando ainda não há carimbo
    # (linha antiga, que se carimba sozinha no próximo 🔄/recarregar).
    em: str | None = None
    # plataforma = data oficial do canal | aprox = melhor estimativa do canal |
    # davinci = instante em que o DaVinci viu mudar. Ver logistica_datas.
    fonte: str | None = None


class LogisticaProdutoOut(BaseModel):
    """Um item do pedido (espelho bling_orders, uma linha por item): alimenta a
    coluna "Produto" do painel — nome + SKU. Derivado, só leitura."""

    sku: str | None = None
    nome: str | None = None
    quantidade: int | None = None


class MensagemClienteOut(BaseModel):
    """Uma mensagem mandada (ou tentada) ao comprador da Amazon pelo robô.
    `enviado_em` vazio + `erro` = falhou; o robô retenta até 3 vezes."""

    evento: str
    evento_label: str = ""
    assunto: str = ""
    enviado_em: datetime | None = None
    erro: str | None = None
    tentativas: int = 0


class ChamadoAbaOut(BaseModel):
    """Ticket do mesmo pedido na aba Chamados (o aberto mais recente; se só há
    encerrado, o encerrado mais recente). É o que responde "já abriram chamado?"
    quando a linha ainda não tem protocolo: ticket aberto SEM nº = alguém ainda
    precisa abrir na plataforma (na Amazon, o SAFE-T no Seller Central — não há
    API); ticket com nº = aberto (o motor copia o nº pra linha em até 5 min)."""

    id: UUID
    chamado: str | None = None
    canal: str
    origem: str
    resolvido: bool = False
    data: date | None = None
    created_at: datetime


class MensagemTemplateOut(BaseModel):
    """Texto de um evento da mensagem ao cliente (tela Logística › Mensagens ao
    cliente). `padrao`=True quando ainda é o texto do código (sem linha no banco)."""

    evento: str
    label: str
    assunto: str
    corpo: str
    ativo: bool = True
    padrao: bool = True


class MensagemTemplateIn(BaseModel):
    assunto: str
    corpo: str
    ativo: bool = True


class MensagemTesteIn(BaseModel):
    """Teste do texto de um evento: vai pra um e-mail SEU (nunca pro cliente),
    montado com o pedido Bling dado ou com um exemplo."""

    email: str
    pedido_bling: str | None = None


class MensagemTesteOut(BaseModel):
    ok: bool = True
    assunto: str
    corpo: str
    pedido: str = ""
    # O teste levou o cartão de rastreio anexado? (o exemplo não tem rastreio
    # que o 17track conheça, então sai sem imagem)
    cartao: bool = False


class MensagemAgoraIn(BaseModel):
    """Disparo controlado: manda a mensagem de um evento pro COMPRADOR de um
    pedido escolhido, agora. Diferente do teste, esta chega no cliente."""

    pedido_bling: str


class MensagemAgoraOut(BaseModel):
    ok: bool = True
    assunto: str
    corpo: str
    pedido: str = ""
    destinatario: str = ""
    cartao: bool = False


class MensagensClienteConfigOut(BaseModel):
    """Estado do envio ao cliente: a chave global (.env) + os textos + os campos
    que o texto pode usar entre chaves."""

    envio_ligado: bool
    remetente: str
    placeholders: dict[str, str] = Field(default_factory=dict)
    templates: list[MensagemTemplateOut] = Field(default_factory=list)


class LogisticaOut(BaseModel):
    id: UUID
    data: date | None = None
    pedido_bling: str | None = None
    pedido_marketplace: str | None = None
    plataforma: str | None = None
    conta: str | None = None
    # Itens do pedido (nome + SKU), do espelho bling_orders pelo pedido_bling.
    # Vazio quando a linha não tem pedido_bling ou o espelho não conhece o
    # pedido. Derivado — o front só renderiza (coluna "Produto").
    produtos: list[LogisticaProdutoOut] = Field(default_factory=list)
    meli_status: dict[str, str] = Field(default_factory=dict)
    # Assinatura em PT p/ exibir na coluna "Status Plataforma" (derivada de
    # meli_status; vazia quando não há assinatura). O front só renderiza.
    status_plataforma: str = ""
    # A mesma assinatura ABERTA campo a campo, com a data de cada um — alimenta
    # o balãozinho da coluna. Derivada (meli_status × status_datas × plataforma).
    status_detalhe: list[StatusDetalheOut] = Field(default_factory=list)
    rastreio: str | None = None
    localizacao: str | None = None
    # Quando a `localizacao` veio dos CORREIOS (push/pull do 17track). Vazio = o
    # que está na coluna ainda é o proxy do marketplace, não o físico. Só leitura.
    localizacao_at: datetime | None = None
    rastreio_lido_em: datetime | None = None
    # Última LEITURA do Status Plataforma (enrich/sweep), mesmo sem mudança.
    status_lido_em: datetime | None = None
    # Divergência ML × rastreio físico dos Correios (auto-calculada; só leitura).
    divergencia: str | None = None
    status_bling: str | None = None
    chamado: str | None = None
    observacao: str | None = None
    # Abertura automática do chamado (motor do recarregar): última tentativa e
    # motivo da recusa (só leitura; vazio = nunca tentou / abriu).
    chamado_auto_at: datetime | None = None
    chamado_auto_erro: str | None = None
    # ---- Amazon (projeto de 15/09/2026) ----
    # 'dba' | 'proprio' | 'fba' | None — separa as abas "Amazon DBA" e "Envio
    # próprio" (services/logistica_amazon_canal). Só leitura.
    amazon_canal: str | None = None
    amazon_canal_label: str = ""
    servico_envio: str | None = None
    postagem_data: date | None = None
    # Previsão de entrega dos Correios (objeto de postagem do Bling) e data
    # máxima de entrega da Amazon (LatestDeliveryDate). Só leitura.
    previsao_correios: date | None = None
    prazo_entrega_amazon: date | None = None
    entregue_em: datetime | None = None
    problema_correios: str | None = None
    cliente_nome: str | None = None
    cliente_email: str | None = None
    # Última leitura do pedido no Bling (serviço, rastreio, contato). No Envio
    # próprio sem `…BR` a tela mostra "Bling lido há X" pra deixar claro que a
    # etiqueta ainda não chegou — a cidade na Localização é só o destino.
    bling_enriquecido_em: datetime | None = None
    # Carimbos dos avisos Threema do robô (um por tipo).
    aviso_previsao_correios_at: datetime | None = None
    aviso_prazo_amazon_3d_at: datetime | None = None
    aviso_prazo_amazon_vencido_at: datetime | None = None
    # Mensagens mandadas ao comprador (histórico; só leitura).
    mensagens_cliente: list[MensagemClienteOut] = Field(default_factory=list)
    # Ticket da aba Chamados do pedido (ver ChamadoAbaOut). Só leitura.
    chamado_aba: ChamadoAbaOut | None = None
    # Suspensão de entrega no Melhor Envio pelo robô: pendente | solicitada |
    # falhou (+ quando foi pedida e o detalhe do robô). Só leitura.
    suspensao_status: str | None = None
    suspensao_em: datetime | None = None
    suspensao_detalhe: str | None = None
    # Casador da aba Status: regra que casa com a chave (status_plataforma)
    # deste pedido. `acao_match`=achou regra; `acao_status_id`=id da linha da
    # aba Status que casou; `acao_resumo`=o que o sistema faria (só leitura,
    # derivado — o front só renderiza).
    acao_match: bool = False
    acao_status_id: UUID | None = None
    acao_resumo: list[str] = Field(default_factory=list)
    # `acao_monitorar`=alguma regra casada pede monitoramento (fica no painel);
    # `acao_resolvido`=chegou ao fim da cadeia de status (nada mais a fazer). O
    # front oculta a linha quando resolvido E sem monitorar (painel de pendências).
    acao_monitorar: bool = False
    acao_resolvido: bool = False
    created_by: UUID | None = None
    created_at: datetime
    updated_at: datetime


class RoboLeaseIn(BaseModel):
    """Executor local pedindo trabalho (X-Agent-Token)."""

    limit: int = Field(default=5, ge=1, le=20)


class RoboComandoOut(BaseModel):
    id: UUID
    logistica_id: UUID
    acao: str
    payload: dict = Field(default_factory=dict)
    attempts: int = 0


class RoboLeaseOut(BaseModel):
    comandos: list[RoboComandoOut] = Field(default_factory=list)


class RoboResultadoIn(BaseModel):
    """Desfecho de um comando: done (o robô clicou em Solicitar) ou failed
    (não achou o envio, sem login, tela mudou…). `result` é o detalhe."""

    status: str = Field(pattern="^(done|failed)$")
    result: str | None = None


class AtualizarRastreioOut(BaseModel):
    """Botão ⟳ da Localização: `resultado` = atualizado | consultando |
    encerrado | sem_quota | recusado | 17track_indisponivel |
    sem_rastreio_no_bling (linha Amazon ainda sem `…BR`: o Bling foi lido na
    hora e também não tem o código); `linha` = a linha já com o que o
    17track/Bling devolveu."""

    resultado: str
    detalhe: str | None = None
    linha: LogisticaOut


class LogisticaCreate(BaseModel):
    data: date | None = None
    pedido_bling: str | None = None
    pedido_marketplace: str | None = None
    plataforma: str | None = None
    conta: str | None = None
    meli_status: dict[str, str] = Field(default_factory=dict)
    rastreio: str | None = None
    localizacao: str | None = None
    status_bling: str | None = None
    chamado: str | None = None
    observacao: str | None = None


class LogisticaPatch(BaseModel):
    data: date | None = None
    pedido_bling: str | None = None
    pedido_marketplace: str | None = None
    plataforma: str | None = None
    conta: str | None = None
    meli_status: dict[str, str] | None = None
    rastreio: str | None = None
    localizacao: str | None = None
    status_bling: str | None = None
    chamado: str | None = None
    observacao: str | None = None


# ---- Aba "Status" (logistica_status) ----


class AnexoOut(BaseModel):
    id: UUID
    filename: str
    content_type: str
    size_bytes: int
    created_at: datetime


class LogisticaStatusOut(BaseModel):
    id: UUID
    plataforma: str | None = None
    status_plataforma: str | None = None
    status_atual: str | None = None
    alterar_status_bling: str | None = None
    monitoramento: bool = False
    abrir_chamado: bool = False
    abrir_reembolso: bool = False
    mensagem_chamado: str | None = None
    mensagem_bling: str | None = None
    mensagem_threema: str | None = None
    threema_recipients: str | None = None
    anexos: list[AnexoOut] = Field(default_factory=list)
    created_by: UUID | None = None
    created_at: datetime
    updated_at: datetime


class LogisticaStatusCreate(BaseModel):
    plataforma: str | None = None
    status_plataforma: str | None = None
    status_atual: str | None = None
    alterar_status_bling: str | None = None
    monitoramento: bool = False
    abrir_chamado: bool = False
    abrir_reembolso: bool = False
    mensagem_chamado: str | None = None
    mensagem_bling: str | None = None
    mensagem_threema: str | None = None
    threema_recipients: str | None = None


class LogisticaStatusPatch(BaseModel):
    plataforma: str | None = None
    status_plataforma: str | None = None
    status_atual: str | None = None
    alterar_status_bling: str | None = None
    monitoramento: bool | None = None
    abrir_chamado: bool | None = None
    abrir_reembolso: bool | None = None
    mensagem_chamado: str | None = None
    mensagem_bling: str | None = None
    mensagem_threema: str | None = None
    threema_recipients: str | None = None


class ThreemaDestinatarioOut(BaseModel):
    """Um destinatário do Threema pro seletor do front (`id` + `nome`)."""

    id: str
    nome: str


class EnviarThreemaIn(BaseModel):
    """Corpo do envio: destinatários escolhidos (None/vazio = usa a lista fixa
    do `.env`) + pedido/loja opcionais pra compor o cabeçalho da mensagem."""

    recipients: list[str] | None = None
    pedido: str | None = None
    loja: str | None = None


class EnviarThreemaOut(BaseModel):
    """Resultado do envio da mensagem_threema de uma linha da aba Status."""

    sent: list[str] = Field(default_factory=list)
    failed: list[str] = Field(default_factory=list)


class MensagemBlingPreviewOut(BaseModel):
    """Dry-run da Mensagem Bling: o que SERIA escrito nas Observações do pedido
    (nenhuma escrita foi feita). `put_body` é o corpo exato do PUT."""

    bling_order_id: int
    mensagem: str
    observacoes_atual: str | None = None
    observacoes_novo: str
    put_body: dict


class MensagemBlingOut(BaseModel):
    """Resultado de aplicar a Mensagem Bling nas Observações do pedido."""

    bling_order_id: int
    observacoes_novo: str


class StatusBlingPreviewOut(BaseModel):
    """Dry-run da mudança de situação: mostra a transição da regra
    (`situacao_de` → `situacao_alvo`, nome + id), a situação ATUAL do pedido no
    Bling e se a mudança se aplica. `ja_no_alvo`=pedido já está na situação
    alvo; `aplicavel`=pedido está no `situacao_de` (ou a regra não exige um "de"
    específico), então a mudança pode seguir sem regredir."""

    bling_order_id: int
    situacao_de: str | None = None
    situacao_de_id: int | None = None
    situacao_alvo: str
    situacao_alvo_id: int
    situacao_atual_id: int | None = None
    situacao_atual_nome: str | None = None
    ja_no_alvo: bool = False
    # Robô já aplicou esse alvo com esta assinatura e um humano tirou dali:
    # não reaplica até a plataforma mudar (09/09).
    override_humano: bool = False
    aplicavel: bool = True


class StatusBlingOut(BaseModel):
    """Resultado de mudar a situação do pedido no Bling."""

    bling_order_id: int
    situacao_alvo: str
    situacao_alvo_id: int


class RecarregarOut(BaseModel):
    """Confirmação de que a recarga em massa (enriquecer ML + aplicar status no
    Bling) foi enfileirada em background. `job_id` deixa o front acompanhar o
    andamento em GET /recarregar/{job_id} e avisar quando terminar."""

    enqueued: bool = True
    job_id: str | None = None


class RecarregarStatusOut(BaseModel):
    """Andamento da recarga em massa: `status` é o estado do job no arq
    (queued/deferred/in_progress/complete/failed/not_found). Quando complete,
    `resumo` traz os contadores do serviço (status_refresh, cleanup,
    enrich_updated, status_aplicados…) pro toast do front."""

    status: str
    resumo: dict[str, int] | None = None


# ---- Sugestão de Status Bling (a partir dos status do Meli) ----


class SugestaoIn(BaseModel):
    """Seleção parcial dos 8 campos de status do Meli."""

    meli_status: dict[str, str] = Field(default_factory=dict)


class CandidatoOut(BaseModel):
    status_bling: str
    matches: int


class SugestaoOut(BaseModel):
    candidatos: list[CandidatoOut]


class OpcoesOut(BaseModel):
    """Campos + opções distintas (pra popular os selects do formulário)."""

    field_order: list[str]
    field_labels: dict[str, str]
    field_options: dict[str, list[str]]
    # Nomes das situações do Bling (davinci.situacao_bling), pra o dropdown de
    # "Alterar Status Bling" na aba Status.
    status_bling_options: list[str] = Field(default_factory=list)
