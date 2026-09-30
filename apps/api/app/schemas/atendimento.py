"""Contratos da tela `/atendimento` (routers/atendimento.py).

Os ids de conversa e de mensagem são TEXTO, não UUID: a mesma lista mostra as
conversas do marketplace (`<uuid>`) e as DMs do Instagram (`ig:<uuid>`, só
leitura), e a tela não precisa saber de onde cada uma veio para abrir.

Os valores de modo, categoria, plataforma e canal são validados contra
`services/atendimento/constantes.py` — o mesmo vocabulário do banco. A
categoria da REGRA do manual é a exceção: a lista oficial mora na tabela
`atendimento_categorias` (o manual base importado), então aqui só se confere
o formato e o router confere se ela existe.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.services.atendimento.constantes import (
    CANAIS_POR_PLATAFORMA,
    MODOS,
    PLATAFORMAS_CAIXA,
    PRIORIDADE_REGRA_PADRAO,
    TIPO_REGRA_CATEGORIA,
    TIPOS_REGRA,
)

# Mesmo formato dos ids de `constantes.CATEGORIAS` e da coluna String(32).
_FORMATO_CATEGORIA = re.compile(r"^[a-z0-9_]{1,32}$")


def _texto_opcional(valor: str | None) -> str | None:
    if valor is None:
        return None
    valor = valor.strip()
    return valor or None


def _plataforma(valor: str | None) -> str | None:
    """Vazio/"todas" = vale para todas; senão uma plataforma da caixa (Temu e
    AliExpress, lidas pelo robô, inclusive: a regra do manual vale para a
    sugestão da IA delas)."""
    v = (valor or "").strip().lower()
    if v in ("", "todas"):
        return None
    if v not in PLATAFORMAS_CAIXA:
        raise ValueError(f"plataforma desconhecida: {v}")
    return v


def _canal(valor: str | None) -> str | None:
    v = (valor or "").strip().lower()
    if v in ("", "todos"):
        return None
    canais = {c for cs in CANAIS_POR_PLATAFORMA.values() for c in cs}
    if v not in canais:
        raise ValueError(f"canal desconhecido: {v}")
    return v


def _tipo_regra(valor: str | None) -> str | None:
    """Tipo da regra do manual (P7), um de `constantes.TIPOS_REGRA`.

    `seguranca` vale para toda mensagem e vem PRIMEIRO no prompt;
    `categoria` só entra quando a mensagem foi classificada naquele assunto
    (sem assunto = geral); `estilo` (tom, assinatura) vem por último. Só as
    de `categoria` podem bater umas com as outras.
    """
    if valor is None:
        return None
    v = str(valor).strip().lower()
    if v not in TIPOS_REGRA:
        raise ValueError(f"tipo de regra desconhecido: {v[:40]}")
    return v


def _categoria(valor: str | None) -> str | None:
    """Vazio/"geral" = regra geral (vale para qualquer assunto); senão um id de categoria.

    Só o formato: se a categoria EXISTE (e está ativa) quem confere é o
    router, contra a tabela do manual base (`manual.categorias_ativas`).
    """
    v = (valor or "").strip().lower()
    if v in ("", "geral", "todas"):
        return None
    if not _FORMATO_CATEGORIA.match(v):
        raise ValueError(f"categoria inválida: {v[:40]}")
    return v


# ── Conversa ──────────────────────────────────────────────────────────────


class ConversaResumoOut(BaseModel):
    """Uma linha da lista (marketplace ou Instagram)."""

    id: str
    plataforma: str
    canal: str
    conta: str | None = None
    integration_id: UUID | None = None
    comprador_nome: str | None = None
    # URL da foto que a API de chat entregou (Shopee/TikTok); None = a tela
    # mostra as iniciais (ML, Amazon, Instagram).
    comprador_avatar: str | None = None
    pedido_marketplace: str | None = None
    anuncio_titulo: str | None = None
    ultima_mensagem_em: datetime | None = None
    ultima_mensagem_resumo: str | None = None
    # texto | imagem | produto | pedido | outro — a prévia "[Pedido]",
    # "[Produto]", "[Imagem]" da lista, como no Duoke. None = sem mensagem.
    ultima_mensagem_tipo: str | None = None
    ultima_autor: str | None = None
    aguardando_resposta: bool = False
    prazo_resposta_em: datetime | None = None
    situacao: str
    nao_lidas: int = 0
    tem_rascunho: bool = False
    atribuido_a: UUID | None = None
    atribuido_a_nome: str | None = None
    ia_pausada: bool = False
    sem_resposta_necessaria: bool = False
    somente_leitura: bool = False
    # Há resposta NOSSA em `revisar` (timeout, envio interrompido): pode ter
    # saído ou não. Continua contando como resposta (não se responde por
    # cima), mas alguém precisa conferir na plataforma — filtro "A conferir".
    envio_a_conferir: bool = False


class ConversaOut(ConversaResumoOut):
    """A conversa aberta: a linha da lista + o que só o detalhe precisa."""

    comprador_id: str | None = None
    anuncio_id: str | None = None
    bloqueio_motivo: str | None = None
    pode_enviar_ate: datetime | None = None
    # Amazon: os links do rodapé do e-mail do comprador, que o leitor da caixa
    # (`services/atendimento/amazon_email.py`) guarda em `conversa.dados`.
    # Campos soltos, não o `dados` inteiro: a tela só precisa destes, e o
    # `dados` carrega pedido, anúncio e reclamação à toa. Só saem se forem o
    # https do próprio Seller Central (vieram de e-mail, texto de fora);
    # qualquer outra coisa — ou outra plataforma — sai None.
    # - `amazon_link_sem_resposta`: o "Não é necessária resposta" da Amazon,
    #   já assinado, do e-mail MAIS NOVO do comprador. Quem abre é a pessoa,
    #   numa aba nova (abrir marca o caso como resolvido na Amazon);
    # - `amazon_link_caso` / `amazon_caso_id`: o botão "Abrir no Seller
    #   Central" (a tela monta o link pelo id quando só ele existe).
    amazon_link_sem_resposta: str | None = None
    amazon_link_caso: str | None = None
    amazon_caso_id: str | None = None
    # - `amazon_copia_a_conferir_em`: a Central respondeu alguém com este nome
    #   (a cópia da resposta empatou entre duas ou mais conversas, e nenhuma
    #   saiu da fila). Só enquanto esta aguarda e nenhuma pergunta mais nova
    #   chegou: a tela pede para conferir no Seller Central.
    amazon_copia_a_conferir_em: datetime | None = None


class ListaConversasOut(BaseModel):
    itens: list[ConversaResumoOut]
    # `ultima_mensagem_em` do último item quando há mais página; None = fim.
    proximo: datetime | None = None


class MensagemOut(BaseModel):
    id: str
    autor: str
    origem: str
    # Nome de quem da equipe enviou (davinci_humano) ou do comprador.
    autor_nome: str | None = None
    tipo: str
    texto: str | None = None
    anexos: list[Any] = Field(default_factory=list)
    enviada_em: datetime | None = None
    status: str
    erro: str | None = None
    # "Saiu" pelo simulador (só local): não chegou a ninguém. Vem do que o
    # envio gravou, não da chave de hoje — com a Amazon na exceção do
    # simulador, a resposta dela CHEGA ao comprador (a tela avisa pelo campo).
    simulado: bool = False


class RascunhoOut(BaseModel):
    id: UUID
    texto: str | None = None
    categoria: str | None = None
    confianca: float | None = None
    precisa_humano: bool = True
    motivo: str | None = None
    validador_erros: list[Any] = Field(default_factory=list)
    status: str
    created_at: datetime | None = None


class EnvioOut(BaseModel):
    """Se a pessoa pode responder AGORA — as mesmas travas do envio."""

    pode_enviar: bool
    # Texto para a faixa da tela; `codigo` é o estável (mesmo da recusa).
    motivo: str | None = None
    codigo: str | None = None
    limite_caracteres: int
    # None = não há loja por trás (Amazon sem conta identificada): não existe
    # modo a mostrar — a tela pede para escolher a conta.
    modo: str | None = None
    sla_horas: int
    # Canal em `observar` OU envio desligado: quem responde é o Duoke/Seller
    # Center, e a tela troca a caixa de envio pelo painel "O que a IA
    # responderia" (o primeiro teste em produção é só observando).
    modo_observacao: bool = False


class AvaliacaoResumoOut(BaseModel):
    """O 👍/👎 que a pessoa deu à sugestão (e a correção, quando errou)."""

    nota: str | None = None
    correcao: str | None = None


class RespostaRealOut(BaseModel):
    """A primeira resposta da LOJA depois da mensagem que a sugestão responde.

    Qualquer origem (Duoke/Seller Center, equipe pelo DaVinci): é o outro
    lado do "IA × equipe". `mensagem_id` é para a tela pôr a comparação logo
    abaixo da resposta real.
    """

    mensagem_id: str
    texto: str | None = None
    enviada_em: datetime | None = None
    origem: str


class SugestaoOut(BaseModel):
    """Uma sugestão da IA que NÃO saiu pelo DaVinci (o que a IA teria respondido)."""

    id: UUID
    texto: str | None = None
    categoria: str | None = None
    confianca: float | None = None
    # pendente | substituido | bloqueado | descartado
    status: str
    created_at: datetime | None = None
    precisa_humano: bool = True
    validador_erros: list[Any] = Field(default_factory=list)
    # A mensagem do cliente que ela responde (None = a mensagem sumiu).
    mensagem_gatilho_id: str | None = None
    avaliacao: AvaliacaoResumoOut | None = None
    resposta_real: RespostaRealOut | None = None


class ConversaDetalheOut(BaseModel):
    conversa: ConversaOut
    mensagens: list[MensagemOut]
    rascunho: RascunhoOut | None = None
    # O formato de `contexto.contexto_da_conversa` (pedido, logística,
    # chamados, devoluções) — livre aqui, quem fixa é o serviço.
    contexto: dict[str, Any] = Field(default_factory=dict)
    envio: EnvioOut
    # Retrato do pedido NA PLATAFORMA (`conversa.dados["pedido_mkt"]`, feito
    # por services/atendimento/enriquecer.py) — o painel "Pedido" do Duoke.
    # Livre aqui: quem fixa o formato é o enriquecimento (spec 2.3).
    pedido_mkt: dict[str, Any] | None = None
    # Cartão do anúncio da pergunta do ML (`conversa.dados["produto"]`).
    produto: dict[str, Any] | None = None
    # Sugestões da IA que não saíram pelo DaVinci, da mais velha para a mais
    # nova, cada uma com a resposta real ao lado (modo observação).
    sugestoes: list[SugestaoOut] = Field(default_factory=list)
    # Cartão "Cliente" do topo do painel da direita (parte 2, P5), feito por
    # `services/atendimento/cliente.py::cartao_cliente`: desde quando compra,
    # compras e total gasto, devoluções/cancelamentos, avaliações, perguntas
    # antes de comprar, `sinais` (recorrente, avaliou_mal, reclamacao_aberta,
    # ja_pediu_devolucao, primeira_compra) e a `linha_do_tempo`. Livre aqui
    # (quem fixa o formato é o serviço); {} = sem dado (ou o serviço falhou —
    # o cartão nunca esconde a conversa).
    cliente: dict[str, Any] = Field(default_factory=dict)
    # O botão "atualizar" do painel Pedido tem o que fazer? Leitura ligada,
    # plataforma com retrato (Shopee/ML), loja conectada e canal não
    # desligado — os mesmos portões do POST /pedido/atualizar. False = a tela
    # esconde o botão em vez de mostrar o 409.
    pedido_atualizavel: bool = False


class PedidoAtualizarOut(BaseModel):
    """Resultado do botão "atualizar" do painel Pedido."""

    pedido_mkt: dict[str, Any] | None = None
    produto: dict[str, Any] | None = None
    # False = o retrato não mudou agora; o painel continua com o que já tinha.
    atualizado: bool = False
    # Por que não atualizou: `recente` (clique repetido dentro de 60 s — nem
    # foi à loja), `limite` (teto do minuto por loja ou por pessoa — nem foi
    # à loja), `sem_alteracao` (foi, e o enriquecimento não renovou: API sem
    # resposta útil ou nada a buscar), `falhou` (erro ao falar com a loja).
    motivo: str | None = None


class ConversaUnicaOut(BaseModel):
    conversa: ConversaOut


class ResponderIn(BaseModel):
    # Sem `min_length`: texto vazio é recusa do VALIDADOR (`texto_invalido`,
    # com o motivo em português), não um 422 genérico do pydantic.
    texto: str = Field(max_length=10_000)
    rascunho_id: UUID | None = None
    # A última mensagem que a pessoa viu na tela. Se a loja respondeu depois
    # dela (outra pessoa, a IA, o Duoke), o envio volta 409 `conversa_mudou`
    # — duas pessoas na mesma conversa não mandam duas respostas. Vazio = não
    # confere (tela antiga).
    ultima_vista_id: UUID | None = None
    # "Vi que mudou, envie mesmo assim" (depois do `conversa_mudou`).
    confirmar: bool = False


class ResponderOut(BaseModel):
    mensagem: MensagemOut


class ConversaPatch(BaseModel):
    """Só os campos enviados mudam. `atribuido_a: null` desatribui.

    `integration_id` só vale para a conversa da Amazon que chegou SEM conta
    identificada (o e-mail não disse de qual das contas era): a pessoa
    escolhe a conta Amazon, e a conversa passa a poder ser respondida.
    """

    atribuido_a: UUID | None = None
    ia_pausada: bool | None = None
    situacao: Literal["aberta", "fechada"] | None = None
    sem_resposta_necessaria: bool | None = None
    integration_id: UUID | None = None


class ConferirIn(BaseModel):
    """A pessoa conferiu na plataforma a resposta em `revisar`: saiu ou não saiu."""

    saiu: bool


class ConferirOut(BaseModel):
    mensagem: MensagemOut
    conversa: ConversaOut


class RascunhoUnicoOut(BaseModel):
    rascunho: RascunhoOut | None = None
    # Só no POST /conversas/{id}/rascunho sem sugestão: por que não veio
    # (sem_chave, provedor_falhou, conversa_fechada, conversa_bloqueada,
    # ia_pausada, sem_mensagem_do_cliente). A tela traduz o código. Exceção:
    # o limite do provedor vem como FRASE (`ia.MOTIVO_LIMITE_PROVEDOR`, "limite
    # do provedor (tente de novo em 1 min)") — a tela mostra como veio o motivo
    # que não conhece.
    motivo: str | None = None


# ── Avaliação da sugestão ─────────────────────────────────────────────────


class DescartarIn(BaseModel):
    """Por que a sugestão não serve — é o material da revisão do manual."""

    motivo: str = Field(min_length=1, max_length=2000)

    _limpa = field_validator("motivo", mode="before")(_texto_opcional)


class AvaliacaoIn(BaseModel):
    nota: Literal["ok", "erro"]
    correcao: str | None = Field(default=None, max_length=4000)

    _limpa = field_validator("correcao", mode="before")(_texto_opcional)


class AvaliacaoOut(BaseModel):
    id: UUID
    rascunho_id: UUID
    acao: str
    texto_final: str | None = None
    similaridade: float | None = None
    motivo: str | None = None
    nota: str | None = None
    correcao: str | None = None


class AvaliacaoUnicaOut(BaseModel):
    avaliacao: AvaliacaoOut


# ── Canal ─────────────────────────────────────────────────────────────────


class CanalOut(BaseModel):
    id: UUID
    # None = loja do robô do Mac mini (Temu/AliExpress: sem integração).
    integration_id: UUID | None = None
    # O perfil do AdsPower que o robô mantém aberto nessa loja.
    robo_perfil_id: str | None = None
    plataforma: str
    canal: str
    # Nome da LOJA (apelido da loja ligada à integração, sem o prefixo da
    # plataforma: "Shopee Marquezini" → "Marquezini", como no Duoke); sem
    # loja ligada, o nome da integração (`lojas.nome_da_loja`).
    conta: str | None = None
    # O nome da integração, para quem configura distinguir as conexões.
    integracao: str | None = None
    modo: str
    status: str
    nao_lidas_plataforma: int | None = None
    ultimo_ok_em: datetime | None = None
    ultimo_erro_em: datetime | None = None
    ultimo_erro: str | None = None
    auto_categorias: list[str] = Field(default_factory=list)
    sla_horas: int
    limite_caracteres: int


class CanalPatch(BaseModel):
    modo: str | None = None
    auto_categorias: list[str] | None = None

    @field_validator("modo")
    @classmethod
    def _modo_valido(cls, v: str | None) -> str | None:
        if v is not None and v not in MODOS:
            raise ValueError(f"modo desconhecido: {v}")
        return v

    @field_validator("auto_categorias")
    @classmethod
    def _categorias_validas(cls, v: list[str] | None) -> list[str] | None:
        """Só o formato e sem repetição. Se o assunto EXISTE quem confere é o
        router, contra o manual base (`manual.categorias_ativas`, que cai em
        `constantes.CATEGORIAS` com a tabela vazia) — e é lá também que a
        lista ganha a ordem do manual (o JSONB compara igual entre edições)."""
        if v is None:
            return None
        invalidas = [c for c in v if not _FORMATO_CATEGORIA.match(c)]
        if invalidas:
            raise ValueError(f"categoria inválida: {', '.join(c[:40] for c in invalidas)}")
        return list(dict.fromkeys(v))


# ── Manual (regras) e respostas prontas (modelos) ─────────────────────────


class RegraIn(BaseModel):
    """QUANDO acontecer isto → FAÇA aquilo. Plataforma/canal vazios = todas.

    `categoria` só tem efeito no tipo `categoria` (vazia = geral): nos tipos
    `seguranca` e `estilo` a regra vale para toda mensagem, e o router a
    grava sem categoria.
    """

    quando: str = Field(min_length=1, max_length=2000)
    faca: str = Field(min_length=1, max_length=4000)
    plataforma: str | None = None
    canal: str | None = None
    ativa: bool = True
    tipo: str = TIPO_REGRA_CATEGORIA
    categoria: str | None = None
    # Menor = mais importante (vem antes no prompt, dentro do mesmo tipo).
    prioridade: int = Field(default=PRIORIDADE_REGRA_PADRAO, ge=0, le=10_000)

    _limpa = field_validator("quando", "faca", mode="before")(_texto_opcional)
    _plat = field_validator("plataforma", mode="before")(_plataforma)
    _can = field_validator("canal", mode="before")(_canal)
    _cat = field_validator("categoria", mode="before")(_categoria)

    @field_validator("tipo", mode="before")
    @classmethod
    def _tipo_valido(cls, v: str | None) -> str:
        # `null`/vazio = o padrão (tela antiga, que não manda o tipo).
        return _tipo_regra(v) if v not in (None, "") else TIPO_REGRA_CATEGORIA


class RegraPatch(BaseModel):
    quando: str | None = Field(default=None, min_length=1, max_length=2000)
    faca: str | None = Field(default=None, min_length=1, max_length=4000)
    # `null` explícito = passa a valer para todas.
    plataforma: str | None = None
    canal: str | None = None
    ativa: bool | None = None
    tipo: str | None = None
    # `null` explícito = passa a ser geral (qualquer assunto).
    categoria: str | None = None
    prioridade: int | None = Field(default=None, ge=0, le=10_000)

    _limpa = field_validator("quando", "faca", mode="before")(_texto_opcional)
    _plat = field_validator("plataforma", mode="before")(_plataforma)
    _can = field_validator("canal", mode="before")(_canal)
    _cat = field_validator("categoria", mode="before")(_categoria)
    _tip = field_validator("tipo", mode="before")(_tipo_regra)


class RegraOut(BaseModel):
    id: UUID
    quando: str
    faca: str
    plataforma: str | None = None
    canal: str | None = None
    ativa: bool
    tipo: str = TIPO_REGRA_CATEGORIA
    categoria: str | None = None
    prioridade: int = PRIORIDADE_REGRA_PADRAO
    updated_at: datetime | None = None
    # As regras ATIVAS com que esta bate (mesmo assunto, plataforma e canal)
    # — a tela pinta de vermelho (GET /regras e a resposta do PATCH). Vêm de
    # antes da trava (manual importado, regra criada antes da parte 2): a
    # API não deixa nascer conflito novo, mas não apaga o que já existe.
    em_conflito: bool = False
    conflita_com: list[UUID] = Field(default_factory=list)


class ListaRegrasOut(BaseModel):
    """GET /regras: as regras e os conflitos que já existem entre elas.

    `conflitos` é o que `manual.conflitos_existentes` devolve (quem fixa o
    formato é o serviço); cada regra também traz `conflita_com` já resolvido.
    """

    regras: list[RegraOut]
    conflitos: list[dict[str, Any]] = Field(default_factory=list)


class CategoriaOut(BaseModel):
    """Um assunto da taxonomia oficial (tabela `atendimento_categorias`).

    Tabela vazia (manual base ainda não importado) = `constantes.CATEGORIAS`
    (nome e descrição de `constantes.CATEGORIAS_INFO`). `extra="ignore"`: o
    serviço pode mandar mais campos sem quebrar a tela.
    """

    model_config = ConfigDict(extra="ignore")

    id: str
    nome: str | None = None
    descricao: str | None = None
    exemplos: list[Any] = Field(default_factory=list)
    # Assunto em que a IA pode sugerir, mas quem envia é sempre pessoa.
    so_humano: bool = False
    # Lacunas que o código preenche nesse assunto ({rastreio}, {nf_numero}...).
    lacunas: list[Any] = Field(default_factory=list)
    ordem: int | None = None


class ModeloIn(BaseModel):
    titulo: str = Field(min_length=1, max_length=200)
    texto: str = Field(min_length=1, max_length=4000)
    plataforma: str | None = None
    canal: str | None = None
    # Assunto da resposta pronta (manual base, P7); vazio = qualquer assunto.
    # Se EXISTE no manual, quem confere é o router (422 categoria_invalida).
    categoria: str | None = None
    ativo: bool = True
    ordem: int = 0

    _limpa = field_validator("titulo", "texto", mode="before")(_texto_opcional)
    _plat = field_validator("plataforma", mode="before")(_plataforma)
    _can = field_validator("canal", mode="before")(_canal)
    _cat = field_validator("categoria", mode="before")(_categoria)


class ModeloPatch(BaseModel):
    titulo: str | None = Field(default=None, min_length=1, max_length=200)
    texto: str | None = Field(default=None, min_length=1, max_length=4000)
    plataforma: str | None = None
    canal: str | None = None
    # `null` explícito = passa a valer para qualquer assunto.
    categoria: str | None = None
    ativo: bool | None = None
    ordem: int | None = None

    _limpa = field_validator("titulo", "texto", mode="before")(_texto_opcional)
    _plat = field_validator("plataforma", mode="before")(_plataforma)
    _can = field_validator("canal", mode="before")(_canal)
    _cat = field_validator("categoria", mode="before")(_categoria)


class ModeloOut(BaseModel):
    id: UUID
    titulo: str
    texto: str
    plataforma: str | None = None
    canal: str | None = None
    categoria: str | None = None
    ativo: bool
    ordem: int


# ── Resumo, sincronização e métricas ──────────────────────────────────────


class PlataformaResumoOut(BaseModel):
    plataforma: str
    aguardando: int
    vencendo: int
    vencidas: int
    # Conversas com resposta nossa em `revisar` (ver `envio_a_conferir`).
    a_conferir: int = 0
    # Soma das não lidas das lojas da plataforma (o número da plataforma).
    nao_lidas: int = 0


class LojaResumoOut(BaseModel):
    """Uma linha da barra de lojas (como a do Duoke): toda loja conectada, até com zero."""

    integration_id: UUID | None = None
    # Loja do robô do Mac mini (Temu/AliExpress, sem integração): o id do
    # canal dela, que filtra a lista (`/conversas?canal_id=`).
    canal_id: UUID | None = None
    plataforma: str
    # Nome da LOJA ("Marquezini", não "Shopee Marquezini" nem o apelido da
    # integração) — o mesmo que o Duoke mostra (`lojas.nome_da_loja`).
    conta: str | None = None
    # O nome da integração, para o `title` (quem tem duas conexões na mesma loja).
    integracao: str | None = None
    # Soma de `conversa.nao_lidas` — o número que a PLATAFORMA dá (o mesmo
    # do Duoke): o DaVinci nunca marca como lido.
    nao_lidas: int = 0
    aguardando: int
    vencidas: int
    # O pior estado entre os canais da loja (parado > sessao_caiu >
    # sem_escopo > erro > desligado > novo > ok): a loja que não está sendo
    # lida aparece apagada na barra. `parado` = o robô do Mac mini sem sinal
    # (Temu/AliExpress). None = conversas sem canal (loja desconectada,
    # Amazon sem conta).
    status_canal: str | None = None
    # O porquê, para o `title` da barra (texto de operação, sem dado pessoal).
    status_motivo: str | None = None


class FlagsOut(BaseModel):
    leitura_ativa: bool
    envio_ativo: bool
    ia_ativa: bool
    auto_ativo: bool
    simulador: bool
    # Plataformas que ESCAPAM do simulador (`atendimento_simulador_exceto`,
    # normalizado como no envio): o que se responde nelas CHEGA ao comprador.
    # Vazio sem o simulador.
    simulador_exceto: list[str] = Field(default_factory=list)
    alerta_telegram: bool


class ResumoOut(BaseModel):
    plataformas: list[PlataformaResumoOut]
    # Total do filtro "A conferir" (todas as plataformas).
    a_conferir: int = 0
    lojas: list[LojaResumoOut]
    canais: list[CanalOut]
    flags: FlagsOut


class SincronizarOut(BaseModel):
    enfileirado: bool
    # Por que não enfileirou: `recente` (clique repetido) ou `leitura_desligada`.
    motivo: str | None = None


class MetricaLojaOut(BaseModel):
    integration_id: UUID | None = None
    conta: str | None = None
    plataforma: str
    # Vezes que o cliente falou e passou a esperar (uma "vez" junta as
    # mensagens seguidas dele, até a loja responder).
    recebidas: int
    respondidas: int
    mediana_primeira_resposta_min: float | None = None
    p90_primeira_resposta_min: float | None = None
    # % respondidas dentro do prazo da plataforma, sobre as que já se
    # decidiram (respondidas + as sem resposta com prazo vencido).
    pct_no_prazo: float | None = None


class MetricaIaOut(BaseModel):
    rascunhos: int
    enviou_igual: int
    editou: int
    descartou: int
    escreveu_do_zero: int
    # 👍/👎 dados pela pessoa no período (inclusive no modo observação, em que
    # nada sai pelo DaVinci): é a nota do teste "IA × equipe".
    nota_ok: int = 0
    nota_erro: int = 0


class MetricasOut(BaseModel):
    dias: int
    lojas: list[MetricaLojaOut]
    ia: MetricaIaOut
