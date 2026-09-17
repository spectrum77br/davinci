"""Marcas, Redes Sociais e Padrões de e-mail — abas do grupo Cadastros
(Eduardo, 15/09/2026).

Vêm da planilha `redes sociais.xlsx` (abas `marcas` e `r.social`): a operação
tem várias marcas (poofy, locagil, 7buyers, Charlots Park…) com registro no
INPI e domínios próprios, e cada marca tem contas nas redes sociais. Mais pra
frente as redes vão receber envio AUTOMÁTICO de vídeos por plataforma — por
isso `redes_sociais` é UMA linha por (marca, plataforma, conta): cada linha
vira o alvo de um canal/token de publicação.

Como na planilha (aba r.social), fone / usuário(e-mail) / senha são da MARCA
(`sac_*`, compartilhados pelas contas); a conta só guarda e-mail/fone/senha
próprios quando DIFEREM ("herda" quando NULL). A senha da marca é a das redes
e do SAC; a senha do registro (INPI/registro.br) é `senha_enc`.

Senhas: cifradas com app.security.cipher.encrypt (AES-GCM, chave
CREDENTIALS_KEY) — mesmo esquema de nf_faturador e store_info. Nunca saem em
listagem (`has_*` só diz se existe); o valor só volta nos GET .../senha, que
exigem permissão de edit e ficam no log.

`marca_email_padroes`: modelos de e-mail por marca e canal (SAC, Mercado
Livre…) com logo e assinatura da empresa — renderizados por
app/services/email_marca.py (prévia, teste e uso por robôs/automações).

Sem relationship entre as tabelas (de propósito): apagar a marca cascateia no
banco (FK ON DELETE CASCADE) e nenhum Out aninha a outra tabela — as abas têm
permissões separadas (`marcas`, `redes_sociais`, `email_padroes`).
"""

from datetime import date
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    Date,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class Marca(Base, TimestampMixin):
    """Uma marca da operação. Colunas espelham a aba `marcas` da planilha
    ('atuação' virou `classe` a pedido do Eduardo). `validade` da planilha é
    a validade do DOMÍNIO (vem depois de dono_dominio e está preenchida em
    marcas cujo INPI ainda é 'aguardando'), daí `dominio_validade`.
    `funcao`/`tipo`/`sac_*`/`obs` vêm da aba r.social: são por marca (linha
    da pivot). `slug` é a chave estável pra outras telas (ex.: Criativos,
    padrões de e-mail) apontarem pra marca sem depender do nome de exibição.
    `company_id`/`site`/`logo` alimentam a assinatura dos e-mails.
    """

    __tablename__ = "marcas"
    __table_args__ = (UniqueConstraint("slug", name="uq_marcas_slug"),)

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    nome: Mapped[str] = mapped_column(Text, nullable=False)
    slug: Mapped[str] = mapped_column(String(128), nullable=False)
    # Situação no INPI — valores em enums.MarcaInpiStatus (String, sem PG enum).
    inpi_status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="nao_registrado",
        server_default=text("'nao_registrado'"),
    )
    # Login/senha/e-mail do registro, como estão na aba `marcas`.
    usuario: Mapped[str | None] = mapped_column(Text, nullable=True)
    senha_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    email: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Texto livre: pode listar mais de um ("a.com.br, b.com.br").
    dominio_br: Mapped[str | None] = mapped_column(Text, nullable=True)
    dominio: Mapped[str | None] = mapped_column(Text, nullable=True)
    dono_dominio: Mapped[str | None] = mapped_column(Text, nullable=True)
    dominio_validade: Mapped[date | None] = mapped_column(Date, nullable=True)
    classe: Mapped[str | None] = mapped_column(Text, nullable=True)
    funcao: Mapped[str | None] = mapped_column(Text, nullable=True)
    tipo: Mapped[str | None] = mapped_column(Text, nullable=True)
    obs: Mapped[str | None] = mapped_column(Text, nullable=True)
    ativo: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    # ---- aba r.social (linha da marca): fone / usuário(e-mail) / senha das
    # redes e do SAC. `sac_fone` só dígitos e é o WhatsApp da marca.
    sac_fone: Mapped[str | None] = mapped_column(Text, nullable=True)
    sac_email: Mapped[str | None] = mapped_column(Text, nullable=True)
    sac_senha_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Selo de verificado do WhatsApp (Meta Verified) — enums.VerificacaoStatus.
    whatsapp_verificacao_status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="nao_solicitado",
        server_default=text("'nao_solicitado'"),
    )
    whatsapp_verificacao_obs: Mapped[str | None] = mapped_column(Text, nullable=True)
    # ---- assinatura dos e-mails: empresa (razão social/CNPJ), site e logo.
    company_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    site: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Logo em bytes no banco (≤ 1 MB, png/jpeg/gif validado pelos magic
    # bytes): vai no backup e não depende do volume de uploads. `deferred`:
    # os bytes só vêm quando alguém pede (GET /logo, e-mail) — a listagem e
    # os grids não carregam 1 MB por marca. `logo_mime` é o indicador de
    # "tem logo" (has_logo) sem tocar nos bytes.
    logo: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True, deferred=True)
    logo_mime: Mapped[str | None] = mapped_column(String(64), nullable=True)


class RedeSocial(Base, TimestampMixin):
    """Uma conta de uma marca em uma plataforma (instagram, tiktok…).

    `conta` é o @ público (sem o "@"); pode ficar NULL quando a marca ainda
    não tem conta naquela plataforma mas já tem e-mail/fone/senha reservados
    (a planilha tem essas linhas). `usuario` é o login quando difere do @
    (no instagram a planilha trazia os dois). `email`/`fone`/`senha_enc` são
    OVERRIDES: NULL = herda os `sac_*` da marca (como na planilha, uma
    credencial por marca). Unicidades (índices parciais abaixo, declarados
    também na migration 0277): a MESMA conta não pode existir em duas marcas
    na mesma plataforma, e cada (marca, plataforma) tem no máximo UMA linha
    sem conta.

    Verificação (selo Meta Verified): `verificacao_status` +
    `verificacao_obs` só registram o andamento — o pedido é feito no app da
    plataforma pela equipe.

    Auto-postagem (futuro): tokens/ids externos entram em colunas próprias
    desta tabela (ou tabela filha por rede_social_id), cifrados com
    app.security.cipher — NUNCA em `integrations` (aquela é de marketplace:
    'tiktok' lá é TikTok Shop, com FK de loja; aqui é conta de conteúdo).
    """

    __tablename__ = "redes_sociais"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    marca_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("marcas.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Valores em enums.RedeSocialPlataforma (String, sem PG enum).
    plataforma: Mapped[str] = mapped_column(String(32), nullable=False)
    conta: Mapped[str | None] = mapped_column(Text, nullable=True)
    usuario: Mapped[str | None] = mapped_column(Text, nullable=True)
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    email: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Só dígitos (validado no schema).
    fone: Mapped[str | None] = mapped_column(Text, nullable=True)
    senha_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    # enums.VerificacaoStatus (String, sem PG enum).
    verificacao_status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="nao_solicitado",
        server_default=text("'nao_solicitado'"),
    )
    verificacao_obs: Mapped[str | None] = mapped_column(Text, nullable=True)
    # ---- postagem automática dos criativos (migration 0279). O robô só
    # publica em conta com `postagem_auto` ligada — é o interruptor por conta.
    # Os tetos são por conta e NULL = usa o padrão do servidor
    # (settings.marketing_postagem_max_dia / _intervalo_min): o Eduardo pediu
    # que os limites fossem configuráveis pra rodar automático sem babá.
    postagem_auto: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    postagem_max_dia: Mapped[int | None] = mapped_column(Integer, nullable=True)
    postagem_intervalo_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # ---- resposta automática de DM (migration 0288). Mesmo interruptor por
    # conta que o `postagem_auto`, e pelo mesmo motivo: ligar uma marca de
    # cada vez é toggle de linha, não deploy. Nasce DESLIGADO — responder
    # cliente sem ninguém ter lido o que o robô diria é o erro caro.
    dm_auto: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    obs: Mapped[str | None] = mapped_column(Text, nullable=True)
    ativo: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )


class MarcaEmailAssinatura(Base, TimestampMixin):
    """Rodapé independente do assunto/corpo, único por marca e canal."""

    __tablename__ = "marca_email_assinaturas"
    __table_args__ = (
        UniqueConstraint("marca_id", "contexto", name="uq_marca_email_assinatura_canal"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    marca_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("marcas.id", ondelete="CASCADE"), nullable=False,
    )
    contexto: Mapped[str] = mapped_column(String(32), nullable=False)
    texto: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    incluir_logo: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    incluir_dados_marca: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    ativo: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )


class MarcaEmailPadrao(Base, TimestampMixin):
    """Padrão de e-mail de uma marca num contexto (SAC, Mercado Livre…).

    `assunto` e `corpo` são templates Jinja (só expressões `{{ }}`, sem
    blocos `{% %}`) renderizados em sandbox por services/email_marca.py com
    as variáveis da marca (marca, empresa, cnpj, site, whatsapp, email_sac) e
    as de contexto (cliente, pedido, produto, plataforma). O HTML final leva
    o logo da marca no topo e a assinatura da empresa (razão social, site,
    WhatsApp com ícone, e-mail SAC) quando `incluir_logo`/`incluir_assinatura`.
    `remetente_*` vazios = nome da marca / `sac_email` da marca / EMAIL_FROM.
    """

    __tablename__ = "marca_email_padroes"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    marca_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("marcas.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # enums.EmailContexto (String, sem PG enum).
    contexto: Mapped[str] = mapped_column(String(32), nullable=False)
    nome: Mapped[str] = mapped_column(Text, nullable=False)
    remetente_nome: Mapped[str | None] = mapped_column(Text, nullable=True)
    remetente_email: Mapped[str | None] = mapped_column(Text, nullable=True)
    assunto: Mapped[str] = mapped_column(Text, nullable=False)
    corpo: Mapped[str] = mapped_column(Text, nullable=False)
    incluir_logo: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    incluir_assinatura: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    ativo: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )


# Índices únicos parciais — no model (create_all dos testes) E na migration
# 0277, pra o 409 via IntegrityError valer nos dois lugares.
Index(
    "uq_redes_sociais_plataforma_conta",
    RedeSocial.plataforma,
    func.lower(RedeSocial.conta),
    unique=True,
    postgresql_where=text("conta IS NOT NULL"),
)
Index(
    "uq_redes_sociais_marca_plataforma_sem_conta",
    RedeSocial.marca_id,
    RedeSocial.plataforma,
    unique=True,
    postgresql_where=text("conta IS NULL"),
)
# Nome do padrão único por (marca, contexto) sem caixa — "Resposta SAC" e
# "resposta sac" são o mesmo padrão.
Index(
    "uq_marca_email_padroes_marca_contexto_nome",
    MarcaEmailPadrao.marca_id,
    MarcaEmailPadrao.contexto,
    func.lower(MarcaEmailPadrao.nome),
    unique=True,
)
