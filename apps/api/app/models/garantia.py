"""Painel de Garantia Uranyx (Pós-venda › Garantias, 07/10/2026).

Documento "Painel de Garantia — Uranyx (DaVinci)": cada garantia tem um prazo
de HARDWARE (entrega + 3 meses) e um de SOFTWARE (entrega + 12 meses), conta a
partir da DATA DE ENTREGA do pedido (RN01, preenchida pelo sistema, nunca pela
pessoa) e guarda os atendimentos do Comunicador (o /atendimento do DaVinci)
que alguém vinculou a ela (RN07). As regras e os pontos que o dono decidiu
ficam num lugar só: `app/services/garantia.py`.

Quatro tabelas:

* `garantias` — o cadastro (pedido, NF, nome, CPF) e os prazos calculados.
  O status NÃO é coluna: sai da data de hoje contra os prazos, na leitura
  (o "muda sozinho" do critério de aceite).
* `garantia_atendimentos` — o atendimento copiado da conversa (data/hora,
  atendente, link, mensagens, anexos, tipo, cobertura, solução). SÓ RECEBE
  LINHA NOVA: o gatilho `garantia_so_insercao` recusa UPDATE, DELETE e
  TRUNCATE no próprio banco (§5.3 "nunca editados ou apagados").
* `garantia_atendimento_anexos` — o ARQUIVO do anexo (foto/vídeo/print),
  baixado do CDN na hora do vínculo: a URL da Shopee/TikTok expira, e a
  garantia é prova para auditoria. Só inserção, como o atendimento.
* `garantia_log` — quem consultou, cadastrou, alterou, viu o CPF, buscou pelo
  CPF completo, registrou atendimento, e o robô que recalculou a entrega
  (§6). Só inserção.

Garantia automática (28/10/2026, migration 0434): o robô cria a garantia do
pedido Uranyx pela NF de PRODUTO (`origem='robo'`, sem `criado_por`) e liga as
conversas do pedido sozinho:

* `garantia_conversas` — PONTEIRO garantia × conversa (sem cópia do texto): o
  histórico do painel é lido na hora em `atendimento_mensagens`. A cópia
  congelada continua sendo só o "Registrar atendimento" (`garantia_atendimentos`).
* `garantia_pendencias` — o pedido que o robô viu e NÃO criou (motivo), para
  não reler a cada volta e para a tela de exceções.

Garantia × SAC (06/11/2026, migration 0459): o prazo passa a ser UM por
produto (`pricing_products.garantia_meses`, a coluna "Garantia (meses)" da
Tabela de Preços) e a garantia guarda o prazo da venda e o fim dele
(`prazo_meses`, `fim_garantia`). `fim_hardware`/`fim_software` continuam
gravados como antes: a IA do Atendimento lê os dois.

* `garantia_sac` — um caso do SAC do site (uranyx.com.br/sac) por conversa
  de protocolo: o formulário lido (`campos`), a garantia ligada (pelo robô
  ou à mão), quem CONFIRMOU ("Garantia acionada", entra na aba Atendimento)
  e quem ENCERROU (a pessoa, ou o robô quando o caso terminou sozinho).

LGPD: as quatro ficam FORA do gatilho do Histórico (`historico/sql.py`,
EXCLUIDAS) — senão CPF, nome e o texto do comprador seriam copiados para
`historico_alteracao` por 365 dias. O Histórico geral continua registrando
o EVENTO (quem, quando, tela), sem o corpo do pedido (`nomes.SEM_CORPO`).
"""

from datetime import date, datetime
from uuid import UUID

from sqlalchemy import (
    DDL,
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    event,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

GARANTIA_TIPOS_PROBLEMA = ("hardware", "software")
# `sem_data_de_entrega`: o atendimento chegou quando a garantia ainda estava
# "Aguardando entrega" — não há prazo para comparar. A tela mostra também a
# cobertura de HOJE (com a data que apareceu depois), calculada na leitura.
GARANTIA_COBERTURAS = ("coberto", "fora_da_garantia", "sem_data_de_entrega")
GARANTIA_LOG_ACOES = (
    "cadastrou",
    "alterou",
    "consultou",
    "registrou_atendimento",
    "recalculou",
    # Busca pelo CPF completo que achou a garantia (lista ou "Vincular à
    # garantia"): o CPF entra mascarado; a busca tem teto por pessoa.
    "buscou_cpf",
    # Garantia automática (28/10/2026): o robô criou pela NF de produto; o
    # robô descartou (pedido cancelado antes de qualquer atendimento).
    "criou_automatico",
    "descartou",
    # O pedido do Bling voltou de Cancelado: a do robô volta para a lista.
    "reativou",
    # Pedido de garantia (28/10/2026): o robô achou o pedido (mensagem, SAC,
    # devolução); a pessoa marcou ou disse "não é pedido de garantia".
    "pedido_automatico",
    "marcou_pedido",
    "tirou_pedido",
    # Garantia × SAC (06/11/2026): a pessoa confirmou o atendimento do SAC
    # ("Garantia acionada"), encerrou o caso (ou o robô, quando terminou
    # sozinho) e o SAC foi ligado à garantia (pelo robô ou à mão).
    "confirmou_atendimento",
    "encerrou_atendimento",
    "vinculou_sac",
)
GARANTIA_ORIGENS = ("manual", "robo")
# De onde veio o pedido de garantia (`garantia_pedido`).
GARANTIA_PEDIDO_ORIGENS = ("mensagem", "sac", "devolucao", "atendimento", "manual", "pessoa")
# Como a conversa chegou à garantia: o nº do pedido na conversa (`pedido`) ou o
# índice comprador × pedido da loja (`comprador` — Shopee/TikTok, uma conversa
# por comprador com todos os pedidos dele).
GARANTIA_LIGACOES = ("pedido", "comprador")
# Como o caso do SAC chegou à garantia (06/11/2026, a ordem da busca do
# robô): o nº do pedido da plataforma, o nº da NF, o CPF + o produto — ou a
# pessoa ("SAC sem pedido" → vincular).
GARANTIA_SAC_VINCULOS = ("pedido", "nf", "cpf_produto", "manual")
# Quem encerrou o caso: a pessoa (botão "Encerrar atendimento") ou o robô,
# quando o caso terminou sozinho (técnico, devolução, decisão da plataforma).
GARANTIA_SAC_ENCERRAMENTOS = ("pessoa", "terminou")
# O prazo do produto, em meses (a coluna da Tabela de Preços).
GARANTIA_MESES_MAX = 120
# As tabelas que só recebem linha nova (gatilho `garantia_so_insercao`).
GARANTIA_TABELAS_SO_INSERCAO = (
    "garantia_atendimentos",
    "garantia_atendimento_anexos",
    "garantia_log",
)
FUNCAO_SO_INSERCAO = "garantia_so_insercao"
# Quem PRECISA apagar (pedido de eliminação do titular pela LGPD, feito por
# quem administra o banco) liga esta marca na transação:
#   SET LOCAL davinci.garantia_expurgo = 'sim';
# Nenhuma rota do DaVinci liga. O conftest liga para limpar entre testes.
MARCA_EXPURGO = "davinci.garantia_expurgo"


def _lista_sql(valores: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in valores)


def sql_funcao_so_insercao(schema: str) -> str:
    """A função do gatilho. Uma só para as três tabelas (o nome da tabela e a
    operação vão na mensagem). Sem `%` no corpo: o mesmo texto passa pelo
    `DDL` do create_all, que usa `%(schema)s`."""
    return f"""
CREATE OR REPLACE FUNCTION "{schema}".{FUNCAO_SO_INSERCAO}() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF coalesce(current_setting('{MARCA_EXPURGO}', true), '') = 'sim' THEN
        IF TG_OP = 'DELETE' THEN
            RETURN OLD;
        ELSIF TG_OP = 'UPDATE' THEN
            RETURN NEW;
        END IF;
        RETURN NULL;
    END IF;
    RAISE EXCEPTION USING
        ERRCODE = 'restrict_violation',
        MESSAGE = 'garantia_so_insercao: ' || TG_OP || ' em ' || TG_TABLE_NAME
            || ' não é permitido — atendimento, anexo e log da garantia só recebem linha nova';
END;
$$
"""


def sql_gatilhos_so_insercao(schema: str, tabela: str) -> list[str]:
    """Os dois gatilhos de uma tabela: por linha (UPDATE/DELETE) e por
    comando (TRUNCATE, que não passa pelos gatilhos de linha)."""
    return [
        f'CREATE TRIGGER {tabela}_so_insercao BEFORE UPDATE OR DELETE ON "{schema}".{tabela}'
        f' FOR EACH ROW EXECUTE FUNCTION "{schema}".{FUNCAO_SO_INSERCAO}()',
        f'CREATE TRIGGER {tabela}_sem_truncate BEFORE TRUNCATE ON "{schema}".{tabela}'
        f' FOR EACH STATEMENT EXECUTE FUNCTION "{schema}".{FUNCAO_SO_INSERCAO}()',
    ]


class Garantia(Base):
    __tablename__ = "garantias"
    __table_args__ = (
        # RN06: o CPF não se repete para a mesma NF. Série vazia = não
        # informada (o número da NF tem 2 a 4 dígitos e há 2 séries e 3
        # emitentes: o número sozinho não identifica a nota).
        UniqueConstraint("cpf", "nf_numero", "nf_serie", name="uq_garantias_cpf_nf"),
        CheckConstraint("cpf ~ '^[0-9]{11}$'", name="cpf_digitos"),
        CheckConstraint("nf_numero ~ '^[1-9][0-9]{0,8}$'", name="nf_numero_digitos"),
        CheckConstraint("nf_serie ~ '^([1-9][0-9]{0,2})?$'", name="nf_serie_digitos"),
        CheckConstraint("nf_chave IS NULL OR nf_chave ~ '^[0-9]{44}$'", name="nf_chave_digitos"),
        # Os prazos existem juntos ou não existem (Aguardando entrega).
        CheckConstraint(
            "(data_inicio IS NULL) = (fim_hardware IS NULL)"
            " AND (data_inicio IS NULL) = (fim_software IS NULL)"
            " AND (data_inicio IS NULL) = (entrega_origem IS NULL)",
            name="prazos_completos",
        ),
        CheckConstraint(
            "data_inicio IS NULL OR (fim_hardware > data_inicio AND fim_software >= fim_hardware)",
            name="prazos_em_ordem",
        ),
        # O fim único (06/11/2026) só existe com a entrega e o prazo do produto.
        CheckConstraint(
            "fim_garantia IS NULL OR (data_inicio IS NOT NULL AND prazo_meses IS NOT NULL"
            " AND fim_garantia > data_inicio)",
            name="fim_garantia_ok",
        ),
        # O robô cria sem pessoa; o cadastro na tela sempre tem quem cadastrou.
        CheckConstraint(f"origem IN ({_lista_sql(GARANTIA_ORIGENS)})", name="origem_valida"),
        CheckConstraint("origem <> 'manual' OR criado_por IS NOT NULL", name="manual_tem_autor"),
        CheckConstraint(
            f"solicitacao_origem IS NULL OR solicitacao_origem IN "
            f"({_lista_sql(GARANTIA_PEDIDO_ORIGENS)})",
            name="solicitacao_origem_valida",
        ),
        # Uma NF identificada pela chave (casada com o XML do pedido) tem uma
        # garantia só (ponto 3: uma garantia por NF).
        Index(
            "uq_garantias_nf_chave",
            "nf_chave",
            unique=True,
            postgresql_where=text("nf_chave IS NOT NULL"),
        ),
        Index("ix_garantias_pedido_bling", "pedido_bling"),
        Index("ix_garantias_pedido_marketplace", "pedido_marketplace"),
        Index("ix_garantias_nf_numero", "nf_numero"),
        Index("ix_garantias_loja", "loja"),
        Index("ix_garantias_fim_garantia", "fim_garantia"),
    )

    # "ID da garantia" do documento.
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # Nº do pedido no Bling (chave canônica; a pessoa pode digitar o do
    # marketplace, a busca acha os dois).
    pedido_bling: Mapped[str] = mapped_column(String(20), nullable=False)
    pedido_marketplace: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Loja do pedido no Bling (`bling_orders.loja`): o escopo por equipe.
    loja: Mapped[str | None] = mapped_column(String(32), nullable=True)
    plataforma: Mapped[str | None] = mapped_column(String(32), nullable=True)
    conta: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # NF informada (só dígitos, sem zero à esquerda) e, quando casa com o XML
    # do pedido (`nf_nota`), a chave de 44 dígitos e o emitente.
    nf_numero: Mapped[str] = mapped_column(String(9), nullable=False)
    nf_serie: Mapped[str] = mapped_column(String(3), nullable=False, server_default="")
    nf_chave: Mapped[str | None] = mapped_column(String(44), nullable=True)
    nf_emitente_cnpj: Mapped[str | None] = mapped_column(String(14), nullable=True)
    cliente_nome: Mapped[str] = mapped_column(String(200), nullable=False)
    # Só os 11 dígitos. A tela formata; a lista mostra mascarado (§6).
    cpf: Mapped[str] = mapped_column(String(11), nullable=False, index=True)
    # RN01: data de entrega do pedido (dia em São Paulo). Nunca vem do corpo
    # de um pedido HTTP: o schema recusa o campo.
    data_inicio: Mapped[date | None] = mapped_column(Date, nullable=True)
    fim_hardware: Mapped[date | None] = mapped_column(Date, nullable=True)  # RN02/RN04
    fim_software: Mapped[date | None] = mapped_column(Date, nullable=True)  # RN03/RN04
    # O prazo ÚNICO da venda (06/11/2026): o maior prazo que o produto teve na
    # Tabela de Preços desde o dia da venda (services/garantia_prazo.py). NULL
    # = o produto não tem prazo cadastrado ("Prazo não cadastrado").
    prazo_meses: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    # Entrega + prazo_meses (RN04: o dia que não existe vira o último; vale
    # até ele, inclusive). NULL sem entrega ou sem prazo.
    fim_garantia: Mapped[date | None] = mapped_column(Date, nullable=True)
    # De onde veio a data (services/garantia.FONTES_ENTREGA) e o instante
    # cru da fonte — é com ele que o robô percebe a correção (ponto 5).
    entrega_origem: Mapped[str | None] = mapped_column(String(20), nullable=True)
    entrega_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    entrega_verificada_em: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Itens do pedido na hora do cadastro ([{descricao, sku, quantidade}]):
    # a garantia é por NF (ponto 3) e o detalhe mostra o que ela cobre.
    itens: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    # RN05: quando foi lançada, independente da entrega.
    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    # NULL = o robô (origem 'robo').
    criado_por: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    origem: Mapped[str] = mapped_column(
        String(16), nullable=False, default="manual", server_default="manual"
    )
    # A garantia do robô cujo pedido foi cancelado ANTES de qualquer
    # atendimento sai da lista (não se apaga: o log aponta para ela).
    descartada_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    descartada_motivo: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Pedido de garantia (28/10/2026): o Painel mostra só a garantia que o
    # cliente PEDIU; a venda sem pedido fica por trás (o "vendidas" do Resumo).
    solicitada_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    solicitacao_origem: Mapped[str | None] = mapped_column(String(16), nullable=True)
    solicitacao_ref: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # "Não é pedido de garantia" (a pessoa): sai do Painel e o robô não marca de novo.
    nao_solicitada_em: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    nao_solicitada_por: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    atualizado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    atualizado_por: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )


class GarantiaAtendimento(Base):
    """Um atendimento do Comunicador gravado na garantia (§5.2). Cópia, não
    referência: a conversa pode mudar ou sumir, a garantia guarda o que foi
    visto. `conversa_id` SEM FK pelo mesmo motivo."""

    __tablename__ = "garantia_atendimentos"
    __table_args__ = (
        CheckConstraint(
            f"tipo_problema IN ({_lista_sql(GARANTIA_TIPOS_PROBLEMA)})", name="tipo_valido"
        ),
        CheckConstraint(
            f"cobertura IN ({_lista_sql(GARANTIA_COBERTURAS)})", name="cobertura_valida"
        ),
        Index("ix_garantia_atendimentos_garantia", "garantia_id", "data_atendimento"),
        Index("ix_garantia_atendimentos_data", "data_atendimento"),
        Index("ix_garantia_atendimentos_conversa", "conversa_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    garantia_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("garantias.id", ondelete="RESTRICT"), nullable=False
    )
    # Relógio da plataforma (a mensagem do cliente), não o do clique.
    data_atendimento: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Quem vinculou (na fase de observação nenhuma conversa tem atendente
    # atribuído: quem clica em "Vincular à garantia" é o atendente).
    atendente_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    atendente_nome: Mapped[str] = mapped_column(Text, nullable=False)
    conversa_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    conversa_link: Mapped[str] = mapped_column(Text, nullable=False)
    conversa_plataforma: Mapped[str | None] = mapped_column(String(16), nullable=True)
    conversa_canal: Mapped[str | None] = mapped_column(String(16), nullable=True)
    conversa_conta: Mapped[str | None] = mapped_column(Text, nullable=True)
    conversa_externo_id: Mapped[str | None] = mapped_column(String(191), nullable=True)
    conversa_pedido: Mapped[str | None] = mapped_column(String(64), nullable=True)
    resumo: Mapped[str] = mapped_column(Text, nullable=False)
    # [{id, autor, origem, tipo, texto, enviada_em, anexos}] — as mensagens
    # copiadas da conversa, em ordem.
    mensagens: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    # [{mensagem_id, tipo, url, nome, baixado, anexo_id, motivo}] — o que a
    # conversa tinha de anexo; `anexo_id` aponta o arquivo guardado.
    anexos: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    tipo_problema: Mapped[str] = mapped_column(String(10), nullable=False)
    # Calculada no vínculo (§5.1 passo 4) e congelada: auditoria do que se
    # decidiu na hora. `fim_considerado` = o fim do prazo do tipo informado.
    cobertura: Mapped[str] = mapped_column(String(20), nullable=False)
    fim_considerado: Mapped[date | None] = mapped_column(Date, nullable=True)
    solucao: Mapped[str] = mapped_column(Text, nullable=False)
    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class GarantiaAtendimentoAnexo(Base):
    """O arquivo de um anexo do atendimento (bytea, como `chamado_anexo`)."""

    __tablename__ = "garantia_atendimento_anexos"
    __table_args__ = (Index("ix_garantia_atendimento_anexos_atendimento", "atendimento_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    atendimento_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("garantia_atendimentos.id", ondelete="RESTRICT"), nullable=False
    )
    mensagem_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    tipo: Mapped[str] = mapped_column(String(16), nullable=False)
    nome: Mapped[str | None] = mapped_column(Text, nullable=True)
    url_original: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_type: Mapped[str] = mapped_column(Text, nullable=False)
    tamanho: Mapped[int] = mapped_column(BigInteger, nullable=False)
    blob: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class GarantiaLog(Base):
    """Quem consultou ou alterou a garantia (§6). `user_id` NULL = o robô
    (recálculo da entrega). CPF entra aqui só mascarado."""

    __tablename__ = "garantia_log"
    __table_args__ = (
        CheckConstraint(f"acao IN ({_lista_sql(GARANTIA_LOG_ACOES)})", name="acao_valida"),
        Index("ix_garantia_log_garantia", "garantia_id", "em"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    garantia_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("garantias.id", ondelete="RESTRICT"), nullable=False
    )
    acao: Mapped[str] = mapped_column(String(30), nullable=False)
    campo: Mapped[str | None] = mapped_column(String(30), nullable=True)
    valor_anterior: Mapped[str | None] = mapped_column(Text, nullable=True)
    valor_novo: Mapped[str | None] = mapped_column(Text, nullable=True)
    detalhe: Mapped[str | None] = mapped_column(Text, nullable=True)
    user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    user_nome: Mapped[str] = mapped_column(Text, nullable=False)
    em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class GarantiaConversa(Base):
    """A conversa do Atendimento ligada à garantia (28/10/2026). Ponteiro: o
    texto e as fotos ficam em `atendimento_mensagens` e o painel lê na hora.
    CASCADE nos dois lados — o ponteiro não é prova de nada (a prova é o
    "Registrar atendimento", copiado e congelado)."""

    __tablename__ = "garantia_conversas"
    __table_args__ = (
        CheckConstraint(f"como IN ({_lista_sql(GARANTIA_LIGACOES)})", name="como_valido"),
        Index("ix_garantia_conversas_conversa", "conversa_id"),
    )

    garantia_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("garantias.id", ondelete="CASCADE"), primary_key=True
    )
    conversa_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("atendimento_conversas.id", ondelete="CASCADE"),
        primary_key=True,
    )
    como: Mapped[str] = mapped_column(String(16), nullable=False)
    ligada_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class GarantiaSac(Base):
    """Um caso do SAC do site (06/11/2026): o formulário de uranyx.com.br/sac
    que a ponte do e-mail gravou numa conversa de protocolo
    (`mail-protocolo:US-26-NNNN`). Só o formulário abre caso — o e-mail comum
    de sac@ (Reclame Aqui, proposta comercial) fica na fila de E-mail.

    O robô (`garantia_sac.sincronizar_sac`) liga o caso à garantia pelo nº do
    pedido da plataforma, pela NF ou pelo CPF + o produto; o que não liga fica
    em "SAC sem pedido" (garantia NULL) para a pessoa vincular. Confirmar =
    "Garantia acionada" (entra na aba Atendimento); encerrar tira da aba.
    `campos` tem CPF e endereço: a tabela fica fora do Histórico geral
    (`historico/sql.py`, EXCLUIDAS)."""

    __tablename__ = "garantia_sac"
    __table_args__ = (
        CheckConstraint(
            f"vinculo IS NULL OR vinculo IN ({_lista_sql(GARANTIA_SAC_VINCULOS)})",
            name="vinculo_valido",
        ),
        CheckConstraint(
            f"encerrado_motivo IS NULL OR encerrado_motivo IN "
            f"({_lista_sql(GARANTIA_SAC_ENCERRAMENTOS)})",
            name="encerrado_motivo_valido",
        ),
        # Só se confirma o caso ligado a uma garantia; só se encerra o confirmado.
        CheckConstraint(
            "confirmado_em IS NULL OR garantia_id IS NOT NULL", name="confirmado_com_garantia"
        ),
        CheckConstraint(
            "encerrado_em IS NULL OR confirmado_em IS NOT NULL", name="encerrado_confirmado"
        ),
        CheckConstraint(
            "(encerrado_em IS NULL) = (encerrado_motivo IS NULL)", name="encerrado_com_motivo"
        ),
        Index("ix_garantia_sac_garantia_id", "garantia_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    conversa_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("atendimento_conversas.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    # A mensagem do formulário na conversa (sem FK: a conversa manda).
    mensagem_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    protocolo: Mapped[str] = mapped_column(Text, nullable=False)
    # A hora do formulário (o "Aberto há N dias" conta daqui).
    recebido_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # O formulário lido (`garantia_sac.campos_do_formulario`).
    campos: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    garantia_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("garantias.id", ondelete="SET NULL"), nullable=True
    )
    vinculo: Mapped[str | None] = mapped_column(Text, nullable=True)
    vinculado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    vinculado_por: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    confirmado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    confirmado_por: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    encerrado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    encerrado_por: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    encerrado_motivo: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class GarantiaPendencia(Base):
    """Pedido Uranyx com NF de produto que o robô NÃO transformou em garantia
    (CPF que não é CPF, NF de produto repetida, acessório que não é Uranyx…).
    Uma linha por pedido, regravada a cada nova tentativa (`visto_em`)."""

    __tablename__ = "garantia_pendencias"

    pedido_bling: Mapped[str] = mapped_column(String(20), primary_key=True)
    motivo: Mapped[str] = mapped_column(String(40), nullable=False)
    detalhe: Mapped[str | None] = mapped_column(Text, nullable=True)
    visto_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    tentativas: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )


# O banco garante o "só adicionar" também no schema dos testes (create_all):
# a migration 0380 roda os MESMOS comandos em produção.
# A função nasce com `garantias`, que as três tabelas referenciam (o
# create_all cria antes).
event.listen(Garantia.__table__, "after_create", DDL(sql_funcao_so_insercao("%(schema)s")))
for _tabela in (GarantiaAtendimento, GarantiaAtendimentoAnexo, GarantiaLog):
    for _comando in sql_gatilhos_so_insercao("%(schema)s", _tabela.__tablename__):
        event.listen(_tabela.__table__, "after_create", DDL(_comando))
