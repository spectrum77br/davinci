from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Origem = Literal["margem", "logistica", "devolucao", "vendas"]
Canal = Literal["api", "robo", "manual"]


def _clean_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


class ChamadoAnexoOut(BaseModel):
    id: UUID
    mensagem_id: UUID | None = None
    filename: str
    content_type: str
    size_bytes: int
    created_at: datetime


class IaAvaliacaoOut(BaseModel):
    """✓/✗ da pessoa numa decisão da IA de Chamado (24/09)."""

    certo: bool
    correcao: str | None = None
    autor: str | None = None
    quando: datetime


class ChamadoMensagemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    chamado_id: UUID
    direcao: str
    tipo: str
    texto: str
    canal: str
    status: str
    erro: str | None = None
    autor_nome: str | None = None
    enviada_at: datetime | None = None
    created_at: datetime
    anexos: list[ChamadoAnexoOut] = []
    # 24/09: análise da IA de Chamado — pode receber ✓/✗ no próprio histórico.
    da_ia: bool = False
    avaliacao_ia: IaAvaliacaoOut | None = None


class ChamadoOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    data: date | None = None
    pedido_bling: str | None = None
    pedido_marketplace: str | None = None
    plataforma: str | None = None
    conta: str | None = None
    produto: str | None = None
    sku: str | None = None
    # Snapshot gravado na linha; `status_bling_atual` é o lookup VIVO em
    # bling_orders (o que a coluna "status bling" da planilha pede).
    status_bling: str | None = None
    status_bling_atual: str | None = None
    origem: str
    origem_ref: str | None = None
    chamado: str | None = None
    chamado_url: str | None = None
    consulta_portal: str | None = None
    canal: str
    # 22/09: o número acima foi capturado pelo robô NA TELA — nenhuma API responde
    # por ele. A tela usa isto pra dizer a verdade no botão Atualizar ("pus na
    # frente da fila do robô", não "li a plataforma") e pra mostrar o botão também
    # nos casos de canal robô.
    chamado_de_tela: bool = False
    leitura_robo_at: datetime | None = None
    alterar_status_bling: str | None = None
    auto_ligada: bool = False
    auto_dias: int | None = None
    auto_mensagem: str | None = None
    auto_ultimo_envio_at: datetime | None = None
    auto_proximo_envio_at: datetime | None = None
    resolvido: bool = False
    resolvido_at: datetime | None = None
    observacao: str | None = None
    # Resultado do chamado em R$ — coluna "Valor" do Controle (Eduardo 03/09):
    # positivo = lucro, negativo = prejuízo (15/09); None = ainda sem valor.
    valor_recuperado: Decimal | None = None
    # 19/09: sugestão do robô/plataforma (nada fecha sozinho — a pessoa confirma
    # ao concluir). A janela Resolver pré-preenche com ela.
    valor_sugerido: Decimal | None = None
    # 19/09 (só mostrar): custo dos itens do pedido no espelho bling_orders —
    # SUM(preco_custo × quantidade) — e o detalhe "sku × qtd; …" pra pessoa
    # decidir o lucro/prejuízo ao concluir.
    custo_produto: Decimal | None = None
    custo_detalhe: str | None = None
    # 19/09: texto da instrução nossa que o robô ainda não leu (tipo `instrucao`
    # mais nova que a última `analise`) — a linha fica Análise Robô.
    instrucao_pendente: str | None = None
    created_at: datetime
    updated_at: datetime
    mensagens_total: int = 0
    ultima_mensagem_at: datetime | None = None
    # Coluna "Status" da aba. `status_plataforma` é o OFICIAL gravado pela API
    # (services.chamados.STATUS_*, Vinicius 17/09) + desde quando; `status_aba` é
    # o que a linha mostra — 19/09: um dos cinco ABA_* (analise_humano /
    # analise_robo / aguard_plataforma / encerrado / concluido), derivado na hora.
    status_plataforma: str | None = None
    status_plataforma_at: datetime | None = None
    status_aba: str | None = None
    status_aba_at: datetime | None = None
    # 18/09: por que está nesse status ("falta foto na devolução", "ganhamos — lucro de R$ 10")
    status_aba_motivo: str | None = None
    # Última FALA real (nossa ou da plataforma; não análise nem evento) —
    # coluna "Últ. resposta": quando, `enviada` (nós) | `recebida` (plataforma), quem.
    ultima_resposta_at: datetime | None = None
    ultima_resposta_direcao: str | None = None
    ultima_resposta_autor: str | None = None
    anexos_auto: list[ChamadoAnexoOut] = []
    # Jurídico (migration 0248)
    juridico_enviado_at: datetime | None = None
    juridico_enviado_por_nome: str | None = None
    juridico_obs: str | None = None
    juridico_link: str | None = None
    juridico_enviados: list[str] = []  # IDs Threema que receberam (CSV no banco)


class JuridicoIn(BaseModel):
    observacao: str | None = None


class JuridicoOut(BaseModel):
    chamado: "ChamadoOut"
    sent: list[str]
    failed: list[str]
    link: str


class ChamadoCreate(BaseModel):
    origem: Origem
    data: date | None = None
    pedido_bling: str | None = None
    pedido_marketplace: str | None = None
    plataforma: str | None = None
    conta: str | None = None
    produto: str | None = None
    sku: str | None = None
    status_bling: str | None = None
    origem_ref: str | None = None
    chamado: str | None = None
    chamado_url: str | None = None
    canal: Canal = "manual"
    alterar_status_bling: str | None = None
    observacao: str | None = None

    _clean = field_validator(
        "pedido_bling",
        "pedido_marketplace",
        "plataforma",
        "conta",
        "produto",
        "sku",
        "status_bling",
        "origem_ref",
        "chamado",
        "chamado_url",
        "alterar_status_bling",
        "observacao",
        mode="before",
    )(_clean_optional_text)

    @model_validator(mode="after")
    def _pedido_obrigatorio(self) -> "ChamadoCreate":
        if not self.pedido_bling and not self.pedido_marketplace:
            raise ValueError("pedido_bling ou pedido_marketplace é obrigatório")
        return self


def _consulta_portal(value: str | None) -> str | None:
    """25/09: aceita o link do Portal ou o número; guarda só o ID (vazio = limpa)."""
    import re

    if value is None:
        return None
    v = str(value).strip()
    if not v:
        return ""
    m = re.search(r"(\d{15,})", v)
    if m is None:
        raise ValueError("consulta do Portal: cole o link ou o número da consulta")
    return m.group(1)


class ChamadoPatch(BaseModel):
    data: date | None = None
    pedido_bling: str | None = None
    pedido_marketplace: str | None = None
    plataforma: str | None = None
    conta: str | None = None
    produto: str | None = None
    sku: str | None = None
    origem: Origem | None = None
    origem_ref: str | None = None
    chamado: str | None = None
    chamado_url: str | None = None
    consulta_portal: str | None = None
    canal: Canal | None = None
    alterar_status_bling: str | None = None
    auto_ligada: bool | None = None
    auto_dias: int | None = Field(default=None, ge=1, le=365)
    auto_mensagem: str | None = None
    observacao: str | None = None
    # Resultado do chamado: positivo = lucro, negativo = prejuízo (Eduardo 15/09).
    valor_recuperado: Decimal | None = None

    _clean = field_validator(
        "pedido_bling",
        "pedido_marketplace",
        "plataforma",
        "conta",
        "produto",
        "sku",
        "origem_ref",
        "chamado",
        "chamado_url",
        "alterar_status_bling",
        "auto_mensagem",
        "observacao",
        mode="before",
    )(_clean_optional_text)
    _consulta = field_validator("consulta_portal", mode="before")(_consulta_portal)


class ChamadoPage(BaseModel):
    items: list[ChamadoOut]
    total: int
    limit: int
    offset: int
    plataformas: list[str]
    # Contas pro filtro (Eduardo 15/09: "filtrar por conta, ex. ML Aguiar 2") —
    # restritas à plataforma filtrada, quando há uma.
    contas: list[str] = Field(default_factory=list)


class ChamadoLookupOut(BaseModel):
    data: date | None = None
    pedido_bling: str | None = None
    pedido_marketplace: str | None = None
    plataforma: str | None = None
    conta: str | None = None
    produto: str | None = None
    sku: str | None = None
    status_bling: str | None = None


class SituacoesOut(BaseModel):
    nomes: list[str]


class AlterarStatusIn(BaseModel):
    situacao: str = Field(min_length=1)


class AlterarStatusOut(BaseModel):
    bling_order_id: int
    situacao: str
    situacao_id: int


class InstrucaoIn(BaseModel):
    """19/09: recado de uma pessoa PRO ROBÔ (não vai pra plataforma). Vira mensagem
    `instrucao` no histórico; o cérebro lê no `/agent/analisar` e responde."""

    texto: str = Field(min_length=1, max_length=2000)

    @field_validator("texto", mode="before")
    @classmethod
    def _strip(cls, v: str) -> str:
        return (v or "").strip()


class ResolverIn(BaseModel):
    resolvido: bool = True
    # Opcional: situação Bling a aplicar junto (ex. Resolvido / Perdimento).
    situacao: str | None = None
    # Resultado do chamado em R$ (positivo = lucro, negativo = prejuízo).
    # OBRIGATÓRIO ao resolver (Eduardo 15/09) — o router devolve 422
    # `chamado_valor_obrigatorio` sem ele; ignorado ao reabrir.
    valor_recuperado: Decimal | None = None
    # 23/09 (Vinicius, pedido 294554: "fizemos duas disputas e a Shopee recusou"):
    # a MESMA observação da coluna — a janela vem com ela preenchida e grava de
    # volta ao resolver, e o texto entra no evento do histórico. Omitida (cliente
    # antigo) = não mexe; enviada vazia = limpa.
    observacao: str | None = None

    _clean = field_validator("situacao", "observacao", mode="before")(_clean_optional_text)


# ------------------------------------------------------------------ lixeira
# Vinicius, 21/09/2026 (caso 294263): a lixeira do histórico apaga o chamado E
# os lançamentos de devolução do mesmo pedido que a pessoa escolher, com a nova
# situação do Bling obrigatória igual ao resolver. A escolha é por linha porque
# num pedido com 2 linhas uma pode ter voltado pro estoque e a outra não.


class ExclusaoLancamentoOut(BaseModel):
    id: UUID
    sku: str | None = None
    produtos: str | None = None
    condicao_produto: str | None = None
    motivo_devolucao: str | None = None
    data_devolvido_estoque: datetime | None = None
    # Movimento de estoque registrado e ainda não estornado: ao excluir, o back
    # dá baixa no Bling sozinho (mesma regra do DELETE /api/devolutions/{id}).
    estoque_estornavel: bool = False
    estoque_mov_sku: str | None = None
    estoque_mov_qty: int | None = None
    # O motivo é dos que abrem chamado (Não recebido, Extraviado…): vem marcado.
    marcado_padrao: bool = False


class ExclusaoPreviewOut(BaseModel):
    chamado_id: UUID
    pedido_bling: str | None = None
    plataforma: str | None = None
    status_bling_atual: str | None = None
    # Mesma regra do resolver: com pedido no Bling, a nova situação é obrigatória.
    exige_situacao: bool = False
    # A disputa/revisão JÁ foi aberta na plataforma (abertura enviada) — apagar
    # aqui não fecha lá; o front avisa.
    abertura_enviada: bool = False
    # Quem só tem chamados.delete apaga o chamado, não os lançamentos.
    pode_excluir_lancamentos: bool = False
    lancamentos: list[ExclusaoLancamentoOut] = Field(default_factory=list)


class ExcluirIn(BaseModel):
    devolucoes: list[UUID] = Field(default_factory=list)
    situacao: str | None = None

    _clean = field_validator("situacao", mode="before")(_clean_optional_text)

    @field_validator("devolucoes", mode="before")
    @classmethod
    def _sem_null(cls, v: object) -> object:
        # `null` explícito vale como "nenhum lançamento".
        return [] if v is None else v


class ExcluirEstornoOut(BaseModel):
    sku: str | None = None
    qty: int | None = None
    mensagem: str | None = None


class ExcluirOut(BaseModel):
    ok: bool = True
    lancamentos_excluidos: int = 0
    # Só as linhas que deram baixa no Bling ao serem excluídas.
    estornos: list[ExcluirEstornoOut] = Field(default_factory=list)
    situacao: str | None = None


# ------------------------------------------------------------------ robô (agent)
# Contrato do robô de chamados (runner de frete / monitor), autenticado por
# X-Agent-Token — mesmo token do executor de NF.

StatusEnvio = Literal["enviada", "falhou", "pendente", "registrada"]


class AgentRegistrarIn(BaseModel):
    """O robô abriu (ou tentou abrir) um chamado na plataforma: registra a
    linha na aba + a mensagem de abertura no histórico. Idempotente por
    (pedido_bling, origem) enquanto o chamado estiver aberto."""

    pedido_bling: str = Field(min_length=1)
    origem: Origem = "margem"
    plataforma: str | None = "ml"
    conta: str | None = None
    pedido_marketplace: str | None = None
    origem_ref: str | None = None
    chamado: str | None = None
    chamado_url: str | None = None
    mensagem: str | None = None
    status_envio: StatusEnvio = "enviada"
    erro: str | None = None
    observacao: str | None = None

    _clean = field_validator(
        "pedido_bling",
        "plataforma",
        "conta",
        "pedido_marketplace",
        "origem_ref",
        "chamado",
        "chamado_url",
        "mensagem",
        "erro",
        "observacao",
        mode="before",
    )(_clean_optional_text)


class AgentRegistrarOut(BaseModel):
    chamado_id: UUID
    mensagem_id: UUID | None = None
    criado: bool


class AgentTarefaOut(BaseModel):
    """Uma réplica pendente pro robô executar. `abrir` = chamado ainda sem
    protocolo (formulário); `responder` = já tem protocolo (página do caso)."""

    tipo: Literal["abrir", "responder"]
    mensagem_id: UUID
    chamado_id: UUID
    pedido_bling: str | None = None
    pedido_marketplace: str | None = None
    conta: str | None = None
    plataforma: str | None = None
    chamado: str | None = None
    chamado_url: str | None = None
    texto: str
    anexos: list[UUID] = []


class AgentLeaseIn(BaseModel):
    limite: int = Field(default=10, ge=1, le=100)
    # Só um tipo de tarefa: o robô do formulário pega `abrir` e o do Tuta pega
    # `responder` — sem isso um lease marcava `enviando` as tarefas do outro.
    tipo: Literal["abrir", "responder"] | None = None
    # Plataforma que ESTE robô atende. Vazio = consumidor padrão (o robô do
    # formulário do ML, cujo código não muda): recebe tudo MENOS TikTok/Shopee.
    # "tiktok" / "shopee" = só as tarefas dessa plataforma (abrir no Seller
    # Center); "ml" = só Mercado Livre. Mesmo desenho do lease de NF.
    plataforma: str | None = None


class AgentLeaseOut(BaseModel):
    tarefas: list[AgentTarefaOut]


class AgentResultadoIn(BaseModel):
    mensagem_id: UUID
    ok: bool
    erro: str | None = None
    # Protocolo/URL capturados ao abrir (só vêm na tarefa `abrir`).
    chamado: str | None = None
    chamado_url: str | None = None

    _clean = field_validator("erro", "chamado", "chamado_url", mode="before")(_clean_optional_text)


class AgentRecebidaIn(BaseModel):
    """O monitor leu uma resposta da plataforma: vai pro histórico como
    `recebida`. Identifica o chamado por id OU por (pedido_bling, chamado)."""

    chamado_id: UUID | None = None
    pedido_bling: str | None = None
    chamado: str | None = None
    texto: str = Field(min_length=1)
    resumo: str | None = None
    resolvido: bool = False
    # 22/09: a hora que a PLATAFORMA mostra, não a do POST. Sem isto a resposta do
    # Agente Shopee de 19/09 21:42 entrava no histórico com a hora da leitura, e a
    # coluna "Últ. resposta" mentia. Opcional: o monitor antigo não muda.
    quando: datetime | None = None

    _clean = field_validator("pedido_bling", "chamado", "resumo", mode="before")(
        _clean_optional_text
    )


class AgentRecebidaOut(BaseModel):
    chamado_id: UUID
    mensagem_id: UUID
    resolvido: bool


class AgentLeituraIn(BaseModel):
    """Quais casos o robô deve RELER na tela da plataforma agora.

    `plataformas` é OBRIGATÓRIO e não tem default de propósito: no `/agent/lease`,
    plataforma vazia significa "tudo menos TikTok/Shopee", e repetir essa regra
    aqui seria a pior armadilha possível — o caso que motivou esta fila (Shopee)
    ficaria invisível pra quem chamasse sem parâmetro. O robô DECLARA em que
    Seller Center ele está logado."""

    limite: int = Field(default=10, ge=1, le=50)
    plataformas: list[str] = Field(min_length=1)
    # Só os casos desta loja (um perfil de navegador por conta evita captcha).
    conta: str | None = None

    _clean = field_validator("conta", mode="before")(_clean_optional_text)


class AgentCasoLeituraOut(BaseModel):
    """Um caso pra reler. NÃO tem `texto`: leitura nunca posta nada.

    `chamado_url` pode vir vazio (nem todo robô devolveu a URL ao abrir). Nesse caso
    o robô acha a página pelo `chamado` — que é o protocolo na tela daquela
    plataforma. Exigir a URL deixava esses casos sem ninguém lendo."""

    chamado_id: UUID
    chamado: str
    chamado_url: str | None = None
    pedido_bling: str | None = None
    pedido_marketplace: str | None = None
    conta: str | None = None
    plataforma: str | None = None
    # Última leitura confirmada — `null` = nunca foi lido.
    leitura_robo_at: datetime | None = None
    # 25/09 (executor de leitura): `devolucao` = busca o pedido no Seller Center;
    # `portal` = consulta do Portal de Atendimento, abrir direto a `chamado_url`.
    tipo: Literal["devolucao", "portal", "ambos"] = "devolucao"
    # 25/09 (294571): consulta do Portal ligada ao chamado (`tipo` portal/ambos) —
    # ler em `consulta_url`. Na devolução, `chamado` segue sendo a solicitação.
    consulta_portal: str | None = None
    consulta_url: str | None = None


class AgentLeituraOut(BaseModel):
    casos: list[AgentCasoLeituraOut]


class AgentLeitorFilaIn(BaseModel):
    """Fila do executor de leitura (24/09, 296012): devoluções da Shopee
    contestadas pela API, pra reler o "Histórico da Solicitação" no Seller
    Center. `chamado` na resposta é o nº da solicitação; o robô busca pelo
    `pedido_marketplace`.

    `contas`: as lojas em que o robô tem perfil (nome como no chamado, ex.
    "Shopee Vortan"); `null` = todas. `espiar`: lista sem marcar a entrega —
    modo seco e conferência."""

    limite: int = Field(default=10, ge=1, le=200)
    contas: list[str] | None = None
    espiar: bool = False
    # 25/09 (292592): inclui as consultas do Portal de Atendimento ao Vendedor
    # (caso aberto na tela; `tipo: "portal"` + `chamado_url`). Desligado por
    # padrão: a versão antiga do executor não sabe ler essa página.
    portal: bool = False
    # 25/09 (294571): inclui chamado com `consulta_portal` (consulta aberta à mão
    # além da devolução) — `tipo: ambos` ou `portal`. Só pra quem sabe ler os dois.
    consultas: bool = False


class AgentFalaLidaIn(BaseModel):
    """Uma fala DELES lida na tela. `quando` é obrigatório: sem a hora da
    plataforma a fala entra com a hora do POST e a coluna "Últ. resposta" mente
    (foi o que aconteceu com a resposta de 19/09 21:42 no 292592)."""

    texto: str = Field(min_length=1)
    quando: datetime
    # O nome que a tela mostra ("Agente Shopee"); vazio = "página do caso".
    autor: str | None = None

    _clean = field_validator("autor", mode="before")(_clean_optional_text)


class AgentLeituraResultadoIn(BaseModel):
    """O que o robô viu na página. UMA chamada por caso, obrigatória mesmo quando
    não há nada novo — é ela que diz "continuo lendo". Sem ela o robô some em
    silêncio e ninguém descobre, que é o bug original."""

    chamado_id: UUID
    ok: bool = True
    erro: str | None = None
    # A conversa COMPLETA da página: contexto, não resposta.
    historico: str | None = None
    # Só o que NÃO foi escrito por nós. Na dúvida, mande: o servidor descarta o
    # eco da nossa própria fala e a repetida.
    falas: list[AgentFalaLidaIn] = []
    # A tela mostra o caso fechado pela plataforma.
    encerrado: bool = False
    # 25/09 (296012): o que a tela está PEDINDO de nós, com prazo — ex. "A Shopee
    # pede evidência até 26/09/2026 … Upload Evidence". Vira aviso no chamado (uma
    # vez por texto) e a IA/pessoa age antes de vencer.
    pendencias: list[str] = []

    _clean = field_validator("erro", "historico", mode="before")(_clean_optional_text)


class AgentLeituraResultadoOut(BaseModel):
    chamado_id: UUID
    falas_novas: int
    ecos: int
    pendencias_novas: int = 0
    duplicadas: int
    historico_alterado: bool
    encerrado: bool
    proxima_leitura_at: datetime


class AgentAnalisarIn(BaseModel):
    """Chamados com trabalho pro cérebro: resposta da plataforma ainda não
    analisada (última `recebida` mais nova que a última `analise`), bloqueio da
    API sem análise depois dele (19/09, `bloqueio`) ou instrução nossa pendente
    (19/09, `instrucao` — qualquer canal, mesmo Encerrado). `plataforma` e
    `canais` filtram SÓ a resposta nova: instrução e bloqueio saem sempre."""

    limite: int = Field(default=20, ge=1, le=100)
    plataforma: str | None = "ml"
    # 09/09: o cérebro passou a olhar TODOS os chamados da aba — `robo` (frete
    # ML, pode responder), `api` (devolução Shopee/TikTok/ML, só decide/avisa)
    # e `manual` (aberto por pessoa: só avisa, nunca age em cima de humano).
    canais: list[Literal["robo", "api", "manual"]] = Field(default=["robo"], min_length=1)


class AgentMensagemOut(BaseModel):
    id: UUID
    direcao: str
    tipo: str
    status: str
    autor_nome: str | None = None
    created_at: datetime
    texto: str


class AgentInstrucaoOut(BaseModel):
    """19/09: instrução de uma pessoa que o robô ainda não leu — obedecer com
    prioridade sobre a regra normal; a análise (POST /agent/analise) consome."""

    texto: str
    autor: str | None = None
    quando: datetime


class AgentBloqueioOut(BaseModel):
    """19/09: a plataforma não libera a ação pela API (`erro` da última fala nossa,
    pendente/enviando). Vinicius: o robô procura OUTRO caminho — `responder` é
    aceito em canal api com bloqueio e vira tarefa no Seller Center."""

    erro: str
    motivo: str | None = None
    desde: datetime | None = None


class AgentChamadoAnaliseOut(BaseModel):
    chamado_id: UUID
    chamado: str | None = None
    chamado_url: str | None = None
    pedido_bling: str | None = None
    pedido_marketplace: str | None = None
    conta: str | None = None
    plataforma: str | None = None
    canal: str
    origem: str
    resolvido: bool
    valor_recuperado: Decimal | None = None
    valor_sugerido: Decimal | None = None
    # 19/09: status oficial da plataforma (ganhamos/perdemos/encerrado = Encerrado,
    # falta pessoa fechar) — o robô só volta aqui com instrução.
    status_plataforma: str | None = None
    observacao: str | None = None
    created_at: datetime
    mensagens: list[AgentMensagemOut]
    instrucao: AgentInstrucaoOut | None = None
    bloqueio: AgentBloqueioOut | None = None
    # Prints capturados na abertura (e os sem mensagem) — o cérebro pode
    # reanexá-los na réplica quando o ML pede "os comprovantes" de novo.
    anexos_abertura: list[UUID] = []
    # 25/09: todos os arquivos do chamado (sem conteúdo) — a IA escolhe as
    # fotos do "Upload Evidence" da Shopee e baixa por /agent/anexos/{id}.
    anexos: list[ChamadoAnexoOut] = []
    replicas_robo: int = 0
    analises: int = 0


class AgentAnalisarOut(BaseModel):
    chamados: list[AgentChamadoAnaliseOut]


AcaoAnalise = Literal["esperar", "responder", "resolver", "humano"]


class AgentAnaliseIn(BaseModel):
    """Decisão do cérebro sobre a última resposta da plataforma (ou sobre uma
    instrução/bloqueio): registra a análise no histórico e executa a ação
    (enfileira réplica pro robô, pede humano, espera). 19/09: `resolver` NÃO
    fecha mais — põe o chamado em Encerrado e `valor_recuperado` vira
    SUGESTÃO (`Chamado.valor_sugerido`, em qualquer ação; negativo = prejuízo);
    a pessoa conclui pela aba."""

    chamado_id: UUID
    classe: str = Field(min_length=1, max_length=60)
    resumo: str = Field(min_length=1, max_length=600)
    acao: AcaoAnalise
    texto_replica: str | None = None
    reanexar_abertura: bool = False
    valor_recuperado: Decimal | None = None
    observacao: str | None = None
    # `humano` num chamado que o monitor antigo já tinha fechado: reabre.
    reabrir: bool = False

    _clean = field_validator("texto_replica", "observacao", mode="before")(_clean_optional_text)

    @model_validator(mode="after")
    def _replica_precisa_texto(self) -> "AgentAnaliseIn":
        if self.acao == "responder" and not (self.texto_replica or "").strip():
            raise ValueError("texto_replica é obrigatório quando acao=responder")
        return self


class AgentAnaliseOut(BaseModel):
    chamado_id: UUID
    analise_id: UUID
    replica_id: UUID | None = None
    resolvido: bool


class AgentExemplosIn(BaseModel):
    """24/09 (Hermes): casos que o cérebro JÁ analisou, com a conversa inteira e
    como terminaram — é daí que saem as regras do cérebro novo (Vinicius:
    "montar do histórico") e os exemplos que ele consulta. Do mais novo pro mais
    velho, paginado por `offset`."""

    limite: int = Field(default=50, ge=1, le=200)
    offset: int = Field(default=0, ge=0)
    desde: datetime | None = None
    plataforma: str | None = None

    _clean = field_validator("plataforma", mode="before")(_clean_optional_text)


class AgentExemplosOut(BaseModel):
    total: int
    chamados: list[AgentChamadoAnaliseOut]


class AgentCasoIn(BaseModel):
    """24/09 (Hermes): achar um chamado quando a pessoa pede "no chamado do pedido
    X, faz tal coisa" — por pedido do Bling, protocolo ou id. Abertos primeiro."""

    pedido_bling: str | None = None
    chamado: str | None = None
    chamado_id: UUID | None = None

    _clean = field_validator("pedido_bling", "chamado", mode="before")(_clean_optional_text)

    @model_validator(mode="after")
    def _um_filtro(self) -> "AgentCasoIn":
        if not (self.pedido_bling or self.chamado or self.chamado_id):
            raise ValueError("informe pedido_bling, chamado ou chamado_id")
        return self


class AgentCerebroIn(BaseModel):
    """24/09: `exclusivo` = o cérebro que chama passa a ser o ÚNICO. O token
    antigo (NF) segue valendo pras mãos do Eduardo, mas o `/agent/analisar` vem
    vazio pra ele e o `/agent/analise` recusa. `null` só consulta."""

    exclusivo: bool | None = None


class AgentRegraOut(BaseModel):
    """Regra do manual da IA de Chamado — "quando acontecer isso, faça isso"."""

    quando: str
    faca: str
    # NULL = vale pra todas as plataformas
    plataforma: str | None = None


class AgentAprendizadoOut(BaseModel):
    """Uma decisão da IA que a pessoa avaliou (✓/✗) — vai pra IA a cada passada."""

    certo: bool
    pedido_bling: str | None = None
    plataforma: str | None = None
    # o texto da análise que ela gravou
    decisao: str
    # no ✗: o que era o certo
    correcao: str | None = None


class AgentCerebroOut(BaseModel):
    nome: str
    exclusivo: bool
    # 24/09: a pessoa liga/desliga na aba IA de Chamado; desligada não decide nada
    ligada: bool = False
    last_used_at: datetime | None = None
    # Última vez que o cérebro antigo bateu depois da troca (ainda rodando?).
    legado_ignorado_at: datetime | None = None
    # O manual (só as regras ativas), lido pela IA a cada passada.
    regras: list[AgentRegraOut] = []
    # 24/09: as correções (✗) e confirmações (✓) mais recentes da pessoa.
    aprendizado: list[AgentAprendizadoOut] = []


class AgentHistoricoIn(BaseModel):
    """15/09 (Eduardo: "preciso de todo o contexto da conversa"): conversa COMPLETA
    da página do caso (todas as falas desde a abertura na plataforma), guardada à
    parte como contexto pra quem analisa. Não é resposta nova — não passa pelo
    cérebro. Identifica o chamado por id OU por (chamado[, pedido_bling])."""

    chamado_id: UUID | None = None
    pedido_bling: str | None = None
    chamado: str | None = None
    texto: str = Field(min_length=1)

    _clean = field_validator("pedido_bling", "chamado", mode="before")(_clean_optional_text)


class AgentHistoricoOut(BaseModel):
    chamado_id: UUID
    mensagem_id: UUID
    alterado: bool


class AgentPagamentoMlIn(BaseModel):
    """15/09: fatos do pagamento da venda no ML (liberação/estorno/envio) pro
    agente de chamados decidir — e encerrar sozinho o que ficou sem prejuízo."""

    pedidos_bling: list[str] = Field(min_length=1, max_length=50)


class AgentPagamentoMlItem(BaseModel):
    pedido_bling: str
    ok: bool
    erro: str | None = None
    venda: str | None = None
    conta: str | None = None
    pedido_status: str | None = None
    pagamento_status: str | None = None
    pagamento_detalhe: str | None = None
    valor_pago: float | None = None
    pago_em: datetime | None = None
    liberado_em: datetime | None = None
    estorno_valor: float | None = None
    estorno_em: datetime | None = None
    estorno_fonte: str | None = None
    envio_status: str | None = None
    envio_substatus: str | None = None
    sem_prejuizo: bool = False
    resumo: str = ""


class AgentPagamentoMlOut(BaseModel):
    pedidos: list[AgentPagamentoMlItem]



# ─── Aba Chamados › IA de Chamado (24/09) ────────────────────────────────────


class IaRegraIn(BaseModel):
    """Uma regra do manual: "quando acontecer isso" → "faça isso". Texto livre —
    a IA interpreta. `plataforma` vazia = vale pra todas."""

    quando: str = Field(min_length=1, max_length=2000)
    faca: str = Field(min_length=1, max_length=4000)
    plataforma: str | None = None

    _clean = field_validator("quando", "faca", "plataforma", mode="before")(_clean_optional_text)


class IaRegraPatch(BaseModel):
    quando: str | None = Field(default=None, min_length=1, max_length=2000)
    faca: str | None = Field(default=None, min_length=1, max_length=4000)
    # mandar `null` explícito = passa a valer pra todas
    plataforma: str | None = None
    ativa: bool | None = None

    _clean = field_validator("quando", "faca", "plataforma", mode="before")(_clean_optional_text)


class IaRegraOut(BaseModel):
    id: UUID
    quando: str
    faca: str
    plataforma: str | None = None
    ativa: bool
    autor: str | None = None
    created_at: datetime
    updated_at: datetime


class IaAvaliacaoIn(BaseModel):
    """✓ acertou / ✗ errou. No ✗ a correção é obrigatória: é ela que a IA segue
    ao refazer o chamado e que fica de aprendizado."""

    certo: bool
    correcao: str | None = Field(default=None, max_length=4000)
    # ✗ com refazer: a correção vira instrução e a IA refaz o chamado. Ao FECHAR o
    # chamado (janela Resolver) não há o que refazer — só fica o aprendizado.
    refazer: bool = True

    _clean = field_validator("correcao", mode="before")(_clean_optional_text)

    @model_validator(mode="after")
    def _errou_precisa_correcao(self) -> "IaAvaliacaoIn":
        if not self.certo and not self.correcao:
            raise ValueError("no ✗ (errou), diga o que era o certo")
        return self


class IaDecisaoOut(BaseModel):
    """Uma análise que a IA gravou num chamado (o que ela decidiu e por quê)."""

    mensagem_id: UUID
    chamado_id: UUID
    pedido_bling: str | None = None
    plataforma: str | None = None
    conta: str | None = None
    quando: datetime
    texto: str
    avaliacao: IaAvaliacaoOut | None = None


class IaEstadoIn(BaseModel):
    ligada: bool


class IaEstadoOut(BaseModel):
    nome: str
    ligada: bool
    # o cérebro antigo (do Eduardo) está travado?
    exclusivo: bool
    # última vez que a IA chamou o DaVinci (ela chama a cada passada)
    ultima_passada: datetime | None = None
    # chamados com trabalho pra IA agora (resposta nova, instrução ou bloqueio)
    esperando: int
    regras: list[IaRegraOut]
    decisoes: list[IaDecisaoOut]


class AgentShopeeProvaIn(BaseModel):
    """25/09: prova da disputa Shopee pela IA de Chamado (`consultar` só lê)."""

    chamado_id: UUID
    acao: Literal["consultar", "enviar"] = "consultar"
    texto: str | None = Field(default=None, max_length=1000)
