"""A camada do atendimento POR CIMA da Central de e-mail (08/10/2026, migration 0387).

A Central do outro dev (`models/mail.py`, migration 0386) é a BASE e não muda:
guarda o e-mail cifrado, o token do agente por caixa e a fila de respostas
humanas (que já não envia duas vezes). Eduardo, 08/10: "vamos apenas
adicionar se formos agregar em algo". Por isso nada nosso entra nas 4 tabelas
dela (o `test_mail_migration.py` dela compara as tabelas da 0386 com os
modelos): o que é nosso fica em tabelas LATERAIS, ligadas por FK.

  mail_mailbox_settings  — 1:1 com a caixa (PK = FK para mail_mailboxes, com
                           CASCADE): quem vê, a ponte para o /atendimento, o
                           remetente estrito, o modo de envio, os tetos, a
                           pausa e o que o agente conta de si (sem segredo).
  mail_folders           — as pastas de cada caixa com ponte, com a
                           plataforma/finalidade pela regra de palavras (ou
                           escolhidas por pessoa) e se o corpo entra no
                           /atendimento (`ler`).
  mail_message_meta      — 1:1 com o e-mail da Central: o que a PONTE decidiu
                           (estado da fila, loja, pedido, protocolo, golpe,
                           conversa e mensagem do /atendimento). SÓ metadado:
                           assunto, corpo e endereço do cliente NUNCA ficam em
                           claro aqui (continuam cifrados na Central).
  mail_outbox_meta       — 1:1 com o job da fila da Central que nasceu de uma
                           resposta pela CONVERSA: liga o job à mensagem
                           `enviando` do /atendimento e guarda quem resolveu um
                           envio incerto.
  atendimento_regras_pasta_email — a regra de palavras das pastas (global,
                           com a semente de 08/10).
  mail_agente_v2         — 1:1 com a caixa: o que o conector v2 conta de si a
                           cada volta (instância, versões, contadores, os
                           aliases ATIVOS da conta no Tuta). Só máquina.
  mail_message_tuta      — 1:1 com o e-mail que chegou pelo v2: em que pasta
                           do Tuta ele está (só a chave da pasta) e se foi
                           apagado. É por ela que o v2 vê movido/apagado.
  mail_reconciliation    — a contagem do dia por pasta (no Tuta × gravado).

SEM LINHA de configuração = os padrões de `PADROES`: caixa PRIVADA, ponte
desligada, remetente como a Central sempre escolheu. A caixa "Goslin — Tuta"
não muda em nada enquanto ninguém criar a linha dela.

Histórico (`historico/sql.EXCLUIDAS`): as pastas e os metadados são escritos
pela MÁQUINA a cada volta da ponte — fora; o mesmo vale para o que o conector
v2 escreve (`mail_agente_v2`, `mail_message_tuta`, `mail_reconciliation`). A
configuração, a ligação fila ↔ conversa (quem resolveu) e a regra de palavras
são de pessoa — ficam.
"""

from datetime import date, datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin

VISIBILIDADES = ("privada", "empresa")
ENVIO_MODOS = ("teste", "real")
AGENTE_TIPOS = ("v1", "v2")

# O que a ponte decidiu de cada e-mail (`mail_message_meta.estado`); o mesmo
# vocabulário de `services/mail_atendimento/constantes.py` (o CHECK confere).
ESTADOS_META = (
    "novo",  # reservado pela ponte (só dentro da transação que o processa)
    "gravado",  # na conversa da loja/site (pedido, protocolo, fio ou aviso na API)
    "sem_vinculo",  # na conversa da loja, sem pedido — "E-mail sem vínculo"
    "sem_loja",  # sem loja e SEM conversa — "E-mail sem loja"
    "ignorado",  # pasta só contar/não ler, spam, aviso do Tuta, alias interno, pessoa
    "interno",  # entre endereços nossos
    "resumo",  # cita mais de 3 pedidos que existem: só registrado
    "duplicado",  # o mesmo Message-ID já foi levado à equipe (outra caixa/alias)
    "seguranca",  # código, senha, acesso: NUNCA vira conversa nem aparece em fila
    "privado",  # caixa "só aliases de loja" e o e-mail não é de loja: é do dono
    "erro",  # a ponte falhou neste e-mail (sem texto guardado): reprocessar
)
# `mail_folders.ler`: o corpo entra no /atendimento | só se conta | nem isso.
LER_PASTA = ("corpo", "so_contar", "nao")
# `mail_outbox_meta.origem`: a resposta pela conversa ou só a resolução de um
# job da caixa crua (sem mensagem do /atendimento).
ORIGENS_ENVIO = ("conversa", "caixa")
RESOLUCOES = ("saiu", "nao_saiu")
TIPOS_REGRA = ("plataforma", "finalidade", "marca")

# Os padrões da linha (e de quem ainda não tem linha). Mudam junto com os
# `server_default` abaixo e com a 0387.
PADROES: dict = {
    "visibilidade": "privada",
    "ponte_ligada": False,
    "ponte_desde": None,
    "ponte_so_aliases_de_loja": True,
    "remetente_estrito": False,
    "envio_modo": "teste",
    "destinatarios_teste": [],
    "teto_hora": 30,
    "teto_dia": 300,
    "teto_conta_hora": 100,
    "envio_pausado_ate": None,
    "envio_pausa_motivo": None,
    "agente_tipo": None,
    "agente_info": {},
}


def _lista(valores: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in valores)


class MailMailboxSettings(Base, TimestampMixin):
    """A configuração NOSSA de uma caixa da Central.

    `visibilidade`:
      • `privada` (padrão) — a caixa é do dono dela e dos admins, como a
        Central sempre fez. Nada do envio muda.
      • `empresa` — a caixa das lojas (a GERAL). A caixa inteira continua só
        do dono e dos admins (o `allowed()` da Central não muda), mas pela
        caixa crua quem responde, liga o envio, troca a chave do Mac, mexe nos
        aliases ou resolve um envio "incerto" precisa também MEXER no
        /atendimento (`acesso.pode_mexer`). E o modo de envio, a lista de
        teste, os tetos e a pausa daqui valem na hora de enfileirar.

    `ponte_*`: o que da caixa vai para a conversa do /atendimento (etapa D).
    Nasce DESLIGADA; `ponte_desde` é o corte (e-mail anterior nunca entra);
    `ponte_so_aliases_de_loja` = só e-mail que chegou num alias ligado a uma
    loja do cadastro (o endereço principal e o resto ficam de fora).

    `remetente_estrito`: a resposta sai SÓ por um endereço da caixa que
    recebeu o e-mail (Para/Cc, ou o `delivered_to` que o conector v2 mandar).
    Sem nenhum, não responde — nunca cai no endereço principal, como a
    Central faz quando nenhum alias bate. Padrão DESLIGADO (a Goslin segue
    como está até o dono dela ligar).

    `envio_modo`: `teste` (padrão) = numa caixa `empresa`, a resposta só entra
    na fila se o destinatário estiver em `destinatarios_teste`; `real` = sai
    para o cliente (sempre por clique de pessoa). Tetos: `teto_hora` e
    `teto_dia` contam as respostas desta caixa que entraram na fila (menos as
    que falharam); `teto_conta_hora` é o que o agente diz que a CONTA do Tuta
    enviou na última hora (o Tuta aceita 100/h somando tudo).

    `agente_tipo`/`agente_info`: escritos pelo agente v2 (etapa C) — versões,
    instância e contadores, nunca segredo nem conteúdo.
    """

    __tablename__ = "mail_mailbox_settings"
    __table_args__ = (
        CheckConstraint(f"visibilidade IN ({_lista(VISIBILIDADES)})", name="visibilidade"),
        CheckConstraint(f"envio_modo IN ({_lista(ENVIO_MODOS)})", name="envio_modo"),
        CheckConstraint(
            f"agente_tipo IS NULL OR agente_tipo IN ({_lista(AGENTE_TIPOS)})", name="agente_tipo"
        ),
        CheckConstraint("teto_hora >= 0 AND teto_dia >= 0 AND teto_conta_hora >= 0", name="tetos"),
        CheckConstraint("jsonb_typeof(destinatarios_teste) = 'array'", name="destinatarios_lista"),
        CheckConstraint("jsonb_typeof(agente_info) = 'object'", name="agente_info_objeto"),
        CheckConstraint("jsonb_typeof(leitores) = 'array'", name="leitores_lista"),
    )

    # CASCADE: a configuração não sobrevive à caixa.
    mailbox_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("mail_mailboxes.id", ondelete="CASCADE"),
        primary_key=True,
    )
    visibilidade: Mapped[str] = mapped_column(
        String(12), nullable=False, default="privada", server_default=text("'privada'")
    )
    ponte_ligada: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    ponte_desde: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ponte_so_aliases_de_loja: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    remetente_estrito: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    envio_modo: Mapped[str] = mapped_column(
        String(8), nullable=False, default="teste", server_default=text("'teste'")
    )
    destinatarios_teste: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    teto_hora: Mapped[int] = mapped_column(
        Integer, nullable=False, default=30, server_default=text("30")
    )
    teto_dia: Mapped[int] = mapped_column(
        Integer, nullable=False, default=300, server_default=text("300")
    )
    teto_conta_hora: Mapped[int] = mapped_column(
        Integer, nullable=False, default=100, server_default=text("100")
    )
    envio_pausado_ate: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    envio_pausa_motivo: Mapped[str | None] = mapped_column(String(200))
    agente_tipo: Mapped[str | None] = mapped_column(String(4))
    agente_info: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    # Quem mudou por último (o Histórico guarda o resto: a tabela é de pessoa).
    updated_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    # "Quem mais vê" (09/10/2026, migration 0389): user_ids (texto) que LEEM a
    # caixa inteira além do dono e dos admins — lista, mensagem e anexo; nunca
    # respondem, resolvem, configuram nem trocam a chave. Só conta quem está
    # ATIVO (`services/mail_atendimento/leitores.pode_ver_caixa`). Quem mexe na
    # lista: admin que MEXE no /atendimento; quem/quando ficam ao lado.
    leitores: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    leitores_updated_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    leitores_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MailFolder(Base, TimestampMixin):
    """Uma pasta de uma caixa da Central (só das caixas com a PONTE ligada).

    `chave` é o que identifica a pasta para o agente: no v1 (IMAP) o nome que
    ele manda em `folder`; no v2 a chave da pasta no Tuta (etapa C). O
    `tipo_tuta` é o MailSetKind (1 Entrada, 2 Enviados, 3 Lixeira, 5 Spam…);
    no v1 sai do nome ("INBOX" = Entrada).

    `ler` (calculado pela regra + a escolha de pessoa):
      • `corpo`     — o e-mail desta pasta entra no /atendimento;
      • `so_contar` — fica só na caixa inteira (dono/admin): pasta sem
                      plataforma (financeiro, contabilidade…), Lixeira, Spam,
                      Rascunhos e a pasta nova que ninguém revisou;
      • `nao`       — uma pessoa mandou ignorar.
    """

    __tablename__ = "mail_folders"
    __table_args__ = (
        UniqueConstraint("mailbox_id", "chave"),
        CheckConstraint(f"ler IN ({_lista(LER_PASTA)})", name="ler"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    mailbox_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("mail_mailboxes.id", ondelete="CASCADE"),
        nullable=False,
    )
    chave: Mapped[str] = mapped_column(String(191), nullable=False)
    nome: Mapped[str] = mapped_column(Text, nullable=False)
    caminho: Mapped[str | None] = mapped_column(Text)
    tipo_tuta: Mapped[str | None] = mapped_column(String(8))
    pai: Mapped[str | None] = mapped_column(String(191))
    # O que a REGRA de palavras diz.
    plataforma: Mapped[str | None] = mapped_column(String(16))
    finalidade: Mapped[str | None] = mapped_column(String(16))
    # O que uma PESSOA escolheu (vale mais que a regra).
    plataforma_manual: Mapped[str | None] = mapped_column(String(16))
    finalidade_manual: Mapped[str | None] = mapped_column(String(16))
    ignorar: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    ler: Mapped[str] = mapped_column(
        String(10), nullable=False, default="so_contar", server_default=text("'so_contar'")
    )
    revisada: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    vista_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sumiu_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ultima_contagem: Mapped[int | None] = mapped_column(Integer)


class MailMessageMeta(Base, TimestampMixin):
    """O que a PONTE decidiu de um e-mail da Central (1:1, CASCADE).

    Em claro SÓ o que não é dado do cliente: a loja, a pasta, o alias NOSSO
    que recebeu, o estado, o protocolo, o nº do pedido e hashes (sha256 do
    Message-ID e do In-Reply-To, sem o texto). O assunto, o corpo e o
    endereço do cliente continuam só cifrados na Central.

    Para o e-mail que NÃO vai para a equipe (`privado`, `seguranca`, pasta
    `so_contar`), quase nada é gravado: o estado, o motivo e a versão das
    regras — nem o alias nem o hash (um e-mail privado nunca vira "duplicado
    de" nada nem aponta para a caixa privada).

    `conversa_id`/`mensagem_id`: a mensagem do /atendimento que a ponte
    gravou (uma só por e-mail: é a trava de idempotência da ponte).
    """

    __tablename__ = "mail_message_meta"
    __table_args__ = (
        CheckConstraint(f"estado IN ({_lista(ESTADOS_META)})", name="estado"),
        Index("ix_mail_message_meta_mailbox_id_estado", "mailbox_id", "estado"),
        Index("ix_mail_message_meta_estado_integration_id", "estado", "integration_id"),
    )

    message_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("mail_messages.id", ondelete="CASCADE"),
        primary_key=True,
    )
    mailbox_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("mail_mailboxes.id", ondelete="CASCADE"),
        nullable=False,
    )
    folder_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("mail_folders.id", ondelete="SET NULL")
    )
    estado: Mapped[str] = mapped_column(
        String(16), nullable=False, default="novo", server_default=text("'novo'")
    )
    # Por que está assim (sem loja: o motivo do roteamento; ignorado: a pasta,
    # o alias interno, o aviso do Tuta…). Código, nunca texto do e-mail.
    motivo: Mapped[str | None] = mapped_column(String(48))
    # [{store_info_id, nome, plataforma, integration_id, sugestao}]
    sugestoes: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    # [{codigo, texto}] — texto de operação, sem dado do cliente.
    alertas: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    # comprador | aviso | nosso | tuta
    remetente_tipo: Mapped[str | None] = mapped_column(String(16))
    alias_recebido: Mapped[str | None] = mapped_column(String(254))
    plataforma: Mapped[str | None] = mapped_column(String(16))
    finalidade: Mapped[str | None] = mapped_column(String(16))
    store_info_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("store_info.id", ondelete="SET NULL")
    )
    integration_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("integrations.id", ondelete="SET NULL")
    )
    marca_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("marcas.id", ondelete="SET NULL")
    )
    # sac | atacado | duvidas (RF6)
    tipo_caixa: Mapped[str | None] = mapped_column(String(16))
    protocolo: Mapped[str | None] = mapped_column(String(32), index=True)
    # [{pedido, existe, plataforma}]
    pedidos_citados: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    pedido_marketplace: Mapped[str | None] = mapped_column(String(64))
    # fio | referencia | protocolo | pedido | aviso_api | amazon_gmail | manual …
    vinculado_por: Mapped[str | None] = mapped_column(String(24))
    suspeito: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    suspeito_motivos: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    # Os avisos do PRÓPRIO Tuta (quando o conector v2 mandar).
    auth_status: Mapped[str | None] = mapped_column(String(8))
    phishing_status: Mapped[str | None] = mapped_column(String(8))
    codigo_mascarado: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    # "lista/elemento" do e-mail no Tuta ("Abrir no Tuta") e o fio do Tuta.
    tuta_id: Mapped[str | None] = mapped_column(String(191))
    fio_tuta: Mapped[str | None] = mapped_column(String(191), index=True)
    # sha256 do Message-ID e do In-Reply-To (sem `<>`), sem o texto.
    mid_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    resposta_a_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    conversa_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("atendimento_conversas.id", ondelete="SET NULL"),
        index=True,
    )
    mensagem_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("atendimento_mensagens.id", ondelete="SET NULL")
    )
    duplicado_de: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("mail_messages.id", ondelete="SET NULL")
    )
    regras_versao: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    processado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MailOutboxMeta(Base, TimestampMixin):
    """A ligação de um job da fila da Central com o /atendimento (1:1, CASCADE).

    `origem = conversa`: a resposta saiu pela conversa (`responder.py`); a
    mensagem `enviando` do /atendimento é `atendimento_mensagem_id` e o
    worker da ponte passa o status do job para ela (enviada, falhou,
    revisar). `origem = caixa`: só a resolução de um job da caixa crua
    (POST /api/mail/outbox/{job}/resolve) — quem resolveu e quando.

    `enviado_mid_hash`: sha256 do Message-ID com que o Tuta enviou (do
    recibo) — a resposta do cliente a ELE acha a conversa.

    `recibo_tardio`/`recibo_tardio_em`: o recibo do Mac que chegou DEPOIS de
    uma pessoa resolver o job (a Central responde 409 e não muda nada; aqui
    fica registrado — "saiu depois da conferência").

    `caixa_empresa`: o job NASCEU numa caixa `empresa` (gravado no
    `queue_reply`). O freio, a pausa e o modo teste do lease valem por ele, e
    não pela visibilidade de agora: a caixa que volta para `privada` nunca
    solta o que estava segurado (crítica pré-subida de 08/10). A linha nasce
    com `origem = caixa` na caixa crua; a resposta da conversa a muda para
    `conversa`.
    """

    __tablename__ = "mail_outbox_meta"
    __table_args__ = (
        UniqueConstraint("atendimento_mensagem_id"),
        CheckConstraint(f"origem IN ({_lista(ORIGENS_ENVIO)})", name="origem"),
        CheckConstraint(
            f"resolucao IS NULL OR resolucao IN ({_lista(RESOLUCOES)})", name="resolucao"
        ),
    )

    outbox_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("mail_outbox.id", ondelete="CASCADE"),
        primary_key=True,
    )
    atendimento_mensagem_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("atendimento_mensagens.id", ondelete="SET NULL")
    )
    conversa_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("atendimento_conversas.id", ondelete="SET NULL"),
        index=True,
    )
    origem: Mapped[str] = mapped_column(
        String(16), nullable=False, default="conversa", server_default=text("'conversa'")
    )
    caixa_empresa: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    enviado_mid_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    resolvido_por: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    resolvido_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolucao: Mapped[str | None] = mapped_column(String(10))
    # O status do job que JÁ passou para a mensagem (o worker só mexe de novo
    # quando o job mudar).
    status_visto: Mapped[str | None] = mapped_column(String(16))
    sincronizado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    recibo_tardio: Mapped[str | None] = mapped_column(String(16))
    recibo_tardio_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AtendimentoRegraPastaEmail(Base, TimestampMixin):
    """A regra de palavras das pastas (a `regra_pasta_email` do spec, RF5).

    `tipo`:
      plataforma — a ÚLTIMA palavra do nome da pasta → plataforma ("vendas ml" → ml);
      finalidade — a PRIMEIRA palavra → vendas | mensagens | problema | reclamacao;
      marca      — qualquer palavra → site da marca ("*uranyx sac" → uranyx, RF6).
    A palavra é guardada sem acento e minúscula; casa inteira ("ali" não casa
    dentro de "magalu"). Nova plataforma = nova linha, sem mexer no código.
    Tabela vazia = a semente de `services/mail_atendimento/regras.SEMENTE`.
    """

    __tablename__ = "atendimento_regras_pasta_email"
    __table_args__ = (
        UniqueConstraint("tipo", "palavra"),
        CheckConstraint(f"tipo IN ({_lista(TIPOS_REGRA)})", name="tipo"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    tipo: Mapped[str] = mapped_column(String(16), nullable=False)
    palavra: Mapped[str] = mapped_column(String(64), nullable=False)
    valor: Mapped[str] = mapped_column(String(64), nullable=False)
    ativa: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )


class MailAgenteV2(Base, TimestampMixin):
    """O que o conector v2 (o NOSSO, do Mac) conta de si, por caixa (1:1, CASCADE).

    Escrito a cada `/v2/sync` (60–120 s): fica FORA do Histórico (é máquina) e
    fora de `mail_mailbox_settings` (que é de pessoa e está no Histórico).

    `instancia` é o id que o conector guarda no Mac, POR CONTA: outra
    instância batendo na mesma caixa em menos de 3 min = dois agentes
    (`dois_agentes_em`; a segunda recebe 409 e para). Um reinício do mesmo
    Mac manda a mesma instância — não é alarme.

    `contadores`: só números (lidos, ilegíveis, enviados na hora pela CONTA
    do Tuta…); `aliases_conta`: os endereços ATIVOS da conta como o Tuta
    mostra (minúsculos) — separados da lista da caixa (`config_enc`), que é
    quem a Central deixa responder.
    """

    __tablename__ = "mail_agente_v2"
    __table_args__ = (
        CheckConstraint("jsonb_typeof(contadores) = 'object'", name="contadores_objeto"),
        CheckConstraint("jsonb_typeof(aliases_conta) = 'array'", name="aliases_lista"),
    )

    mailbox_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("mail_mailboxes.id", ondelete="CASCADE"),
        primary_key=True,
    )
    instancia: Mapped[str] = mapped_column(String(64), nullable=False)
    versao_agente: Mapped[str | None] = mapped_column(String(64))
    versao_tuta: Mapped[str | None] = mapped_column(String(32))
    contadores: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    aliases_conta: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    visto_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    dois_agentes_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MailMessageTuta(Base, TimestampMixin):
    """Onde o e-mail que chegou pelo v2 está no Tuta (1:1, CASCADE). SÓ ids.

    `folder_key` é a chave da pasta (o id do MailSet, a mesma de
    `mail_folders.chave`) — nunca o nome. `apagado_em`: foi para a Lixeira
    ou sumiu de vez no Tuta. A Central ignora o e-mail repetido no ingest,
    então a mudança de pasta chega pela contagem (`/v2/count`) e pelas
    mudanças (`/v2/changes`), que também regravam a pasta no conteúdo
    cifrado (a tela da Central mostra a pasta certa).
    """

    __tablename__ = "mail_message_tuta"
    __table_args__ = (
        Index("ix_mail_message_tuta_mailbox_id_folder_key", "mailbox_id", "folder_key"),
    )

    message_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("mail_messages.id", ondelete="CASCADE"),
        primary_key=True,
    )
    mailbox_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("mail_mailboxes.id", ondelete="CASCADE"),
        nullable=False,
    )
    folder_key: Mapped[str] = mapped_column(String(191), nullable=False)
    movido_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    apagado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MailReconciliation(Base, TimestampMixin):
    """A contagem do dia de uma pasta (`/v2/count` com `day`).

    `no_tuta`: quantos e-mails a pasta tinha no Tuta naquele dia (Brasília);
    `gravados`: quantos desses estão na Central (só nas pastas de `corpo`;
    nas de "só contar" é nulo — o corpo nunca sobe); `faltando`: no Tuta e
    não na Central (o conector manda de novo); `a_mais`: na Central como
    desta pasta e não mais nela no Tuta (movido/apagado, a conferir).
    """

    __tablename__ = "mail_reconciliation"
    __table_args__ = (UniqueConstraint("mailbox_id", "folder_id", "dia"),)

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    mailbox_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("mail_mailboxes.id", ondelete="CASCADE"),
        nullable=False,
    )
    folder_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("mail_folders.id", ondelete="CASCADE"),
        nullable=False,
    )
    dia: Mapped[date] = mapped_column(Date, nullable=False)
    no_tuta: Mapped[int] = mapped_column(Integer, nullable=False)
    gravados: Mapped[int | None] = mapped_column(Integer)
    faltando: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    a_mais: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    ok: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    motivo: Mapped[str | None] = mapped_column(String(48))
    conferido_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
