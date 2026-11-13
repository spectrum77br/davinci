"""E-mail › Agente: as regras do agente no e-mail (+ as 4 primeiras)

12/11/2026, o Eduardo: o agente do atendimento responde o e-mail como
extensão do atendimento, por enquanto SÓ no SAC dos sites (Uranyx e
Charlots), e as regras próprias do e-mail ficam numa parte nova da aba
E-mail — cada uma diz onde vale (os sites) e se a resposta que a segue sai
sozinha. As 4 primeiras vêm do levantamento dos e-mails do SAC (60 dias):

  1. reclamação de produto sem o formulário de SAC → pedir o formulário,
     completo e bem organizado (sai sozinha; o link e o que ele pede vêm do
     código, `email_agente.FORMULARIOS_SAC`);
  2. o que não é cliente (propaganda, fornecedor, parceiro, cobrança, aviso
     de sistema ou do Reclame Aqui) → não responder, fica com a pessoa;
  3. atendimento que a equipe já está tocando → não pedir o formulário de
     novo, fica com a pessoa;
  4. onde comprar / quando chega um modelo → o site oficial, sem prometer
     data (sugestão para a equipe conferir).

Nada muda no deploy: o agente só responde e-mail do site que o admin ligar
na aba E-mail › Agente.

Revision ID: 0480_mail_agente_regras
Revises: 0479_atendimento_respostas
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op
from app.config import get_settings

revision: str = "0480_mail_agente_regras"
down_revision: str | None = "0479_atendimento_respostas"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None
SCHEMA = get_settings().database_schema

REGRAS = [
    {
        "id": "6f1d3c2e-8a4b-4c7d-9e21-0a5b7c9d1e01",
        "chave": "formulario_sac",
        "titulo": "Reclamação de produto no SAC sem o formulário do site",
        "instrucao": (
            "Quando o cliente escreve direto no e-mail do SAC reclamando de defeito ou de "
            "problema no produto e o atendimento NÃO veio pelo formulário de SAC do site (sem "
            "protocolo), não tente resolver o caso por e-mail. Responda com um e-mail completo "
            "e bem organizado:\n"
            "1. cumprimente pelo nome, agradeça o contato e lamente o problema numa frase;\n"
            "2. explique que, para a equipe técnica analisar com agilidade, o atendimento é "
            "aberto pelo formulário de SAC do site, e ponha o link do formulário sozinho numa "
            "linha;\n"
            "3. liste em tópicos (•) o que ter em mãos, como está nos dados do e-mail: os "
            "dados de quem comprou (os mesmos da compra), os dados da compra, os dados do "
            "produto, a descrição do defeito, as fotos e o vídeo curto mostrando o defeito;\n"
            "4. diga que, ao enviar, o número do protocolo chega na hora e a equipe segue o "
            "atendimento por ele;\n"
            '5. feche com "Ficamos à disposição."\n'
            "Não peça os dados por e-mail e não prometa troca, reembolso nem prazo. Se o "
            "e-mail já veio pelo formulário (tem protocolo) ou a equipe já está atendendo esse "
            "caso, siga o atendimento normal."
        ),
        "sites": [],
        "responde_sozinho": True,
        "ordem": 0,
    },
    {
        "id": "6f1d3c2e-8a4b-4c7d-9e21-0a5b7c9d1e02",
        "chave": None,
        "titulo": "E-mail que não é de cliente",
        "instrucao": (
            "Se o e-mail não é de um consumidor falando de um produto ou de uma compra da "
            "marca — propaganda, newsletter, fornecedor oferecendo produto, proposta comercial "
            "ou de parceiro (certificação, assinatura de documentos), crédito, cobrança, aviso "
            "de sistema, de domínio ou de hospedagem, notificação do Reclame Aqui ou do painel "
            'do site — não responda: passe para a pessoa com o motivo "não é cliente".'
        ),
        "sites": [],
        "responde_sozinho": False,
        "ordem": 1,
    },
    {
        "id": "6f1d3c2e-8a4b-4c7d-9e21-0a5b7c9d1e03",
        "chave": None,
        "titulo": "Atendimento que a equipe já está tocando",
        "instrucao": (
            "Se a conversa já tem resposta da equipe (troca, garantia, envio pelos Correios, "
            "logística reversa, Reclame Aqui), é um atendimento em andamento: não peça o "
            "formulário de novo e não prometa troca, reembolso nem prazo — passe para a "
            "pessoa, com um resumo curto do que o cliente pediu agora."
        ),
        "sites": [],
        "responde_sozinho": False,
        "ordem": 2,
    },
    {
        "id": "6f1d3c2e-8a4b-4c7d-9e21-0a5b7c9d1e04",
        "chave": None,
        "titulo": "Onde comprar ou quando chega um modelo",
        "instrucao": (
            "Quando o cliente pergunta onde comprar um produto, uma peça ou um acessório, ou "
            "quando um modelo (cor, voltagem, tamanho) volta ao estoque: agradeça, indique o "
            "site oficial da marca (o link está nos dados do e-mail) e diga que a equipe "
            "confirma a disponibilidade do item. Não prometa data de chegada nem preço."
        ),
        "sites": [],
        "responde_sozinho": False,
        "ordem": 3,
    },
]


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '10s'")
    tabela = op.create_table(
        "mail_agente_regras",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("chave", sa.String(32), nullable=True),
        sa.Column("titulo", sa.String(120), nullable=False),
        sa.Column("instrucao", sa.Text(), nullable=False),
        sa.Column("ativa", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("sites", pg.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column(
            "responde_sozinho", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("ordem", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "updated_by",
            pg.UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        *[
            sa.Column(
                n, sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
            )
            for n in ("created_at", "updated_at")
        ],
        sa.UniqueConstraint("chave", name="uq_mail_agente_regras_chave"),
        sa.CheckConstraint("jsonb_typeof(sites) = 'array'", name="sites_lista"),
        schema=SCHEMA,
    )
    op.bulk_insert(tabela, REGRAS)


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '10s'")
    op.drop_table("mail_agente_regras", schema=SCHEMA)
