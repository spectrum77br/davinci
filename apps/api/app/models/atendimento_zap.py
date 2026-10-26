"""Canal Zap (WhatsApp Cloud API oficial da Meta) no Atendimento — RF10, Onda 1.

Desenho aprovado em `docs/zap/desenho.md` (§4.2). Cada número de WhatsApp das
lojas vira um CANAL EXTERNO (`atendimento_canais`, `plataforma='whatsapp'`,
`canal='zap'`, `externo_ref='zap:<phone_number_id>'`, sem integração): a
conversa, as mensagens, a fila e a etiqueta são as de sempre. Estas seis
tabelas guardam só o que é do WhatsApp:

  `atendimento_zap_apps`    — um app da Meta (pode haver vários: o
                              "DaVinci Zap Teste" do computador e o "DaVinci
                              Zap" do servidor). Guarda o app secret (que
                              assina o webhook) e o verify token, CIFRADOS.
  `atendimento_zap_contas`  — uma WABA (conta WhatsApp Business) e o token do
                              usuário do sistema FUNCIONÁRIO que a enxerga,
                              CIFRADO.
  `atendimento_zap_numeros` — um número: o botão "pausar" (`envio_ligado`,
                              nasce false), a etapa da migração do Duoke, a
                              lista de teste, o teto do dia e a saúde.
  `atendimento_zap_contatos`— o cliente NUM número. O `id` dele é o
                              `externo_id` da conversa (premissa P3): o
                              telefone pode faltar (BSUID), mudar ou vir sem
                              o 9º dígito, e o número de celular é reciclado.
  `atendimento_zap_eventos` — a caixa de entrada do webhook, UM item por
                              linha (mensagem, status, erro, preferência,
                              conta). O webhook só grava e responde 200; o
                              worker processa, um commit por evento. O UNIQUE
                              da `chave` é a idempotência da reentrega da Meta.
  `atendimento_zap_midias`  — foto, áudio, vídeo e documento. O schema nasce
                              na Onda 1 (a mídia é registrada `pendente`); o
                              baixador vem na Onda 3.

Segredos: as colunas `*_enc` são BYTEA com `app.security.cipher.encrypt_json`
(AES-GCM sobre a `CREDENTIALS_KEY`), como `integrations.credentials` e
`dm_contas.token_enc`. Nenhum endpoint devolve o valor; quem cifra e decifra
é `services/atendimento/zap_contas.py`, só na memória do processo.

Histórico (§4.3): `contatos`, `eventos` e `midias` ficam FORA do gatilho
(`historico/sql.EXCLUIDAS`) — telefone, BSUID, nome do perfil, texto do
cliente e nome de arquivo não podem ser copiados por 365 dias (nem pela
própria exclusão LGPD). `apps`, `contas` e `numeros` ficam COM: os `*_enc`
saem como `{_arquivo: true}`, e pausar um número, trocar a etapa ou o token é
exatamente o que o Histórico serve para mostrar.

Os valores das colunas de estado estão nos CHECKs abaixo (e no §4.2 do
desenho); a migration é a `0430_atendimento_zap`.
"""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CHAR,
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Identity,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import DateTime

from app.models.base import Base, TimestampMixin

# ── Vocabulário das colunas de estado (um lugar só: model, migration e serviço) ──
# A migration 0430 copia estes textos de propósito (migration não importa
# model); `tests/test_atendimento_migration.py` confere que os dois batem.
ZAP_CONTA_STATUS = ("novo", "ok", "token_invalido", "sem_permissao", "vencendo", "erro")
ZAP_NUMERO_ETAPAS = (
    "teste",
    "preparando",
    "aguardando_duoke",
    "migrando",
    "ativo",
    "pausado",
    "desligado",
)
ZAP_NUMERO_SAUDE = ("novo", "ok", "restrito", "erro", "sem_eventos")
ZAP_OPT_OUT_TIPOS = ("todos", "marketing")
ZAP_EVENTO_TIPOS = ("mensagem", "status", "erro", "preferencia", "conta", "ignorado")
ZAP_EVENTO_RESULTADOS = (
    "gravado",
    "repetido",
    "numero_desconhecido",
    "waba_desconhecida",
    "numero_de_outra_waba",
    "sem_alvo",
    "objeto_inesperado",
    "json_invalido",
    "erro",
)
ZAP_MIDIA_DIRECOES = ("entrada", "saida")
ZAP_MIDIA_STATUS = (
    "pendente",
    "baixando",
    "baixada",
    "grande_demais",
    "falhou",
    "expirada",
    "apagada",
)


def _em(coluna: str, valores: tuple[str, ...], *, nulo: bool = False) -> str:
    lista = ", ".join(f"'{v}'" for v in valores)
    regra = f"{coluna} IN ({lista})"
    return f"{coluna} IS NULL OR {regra}" if nulo else regra


class AtendimentoZapApp(Base, TimestampMixin):
    """Um app da Meta no Gerenciador DOS NÚMEROS (não o do Instagram).

    O `app_id` vai na URL do webhook (`/api/webhooks/meta/whatsapp/{app_id}`):
    é público, não é segredo — a autenticação é a assinatura HMAC do corpo
    com o app secret DESTE app.

    `segredo_enc` = `encrypt_json({"atual", "anterior", "anterior_ate"})`. A
    Meta troca a chave na hora (não há período em que as duas valem): na troca
    DE ROTINA o anterior vale só 1 h; na troca POR VAZAMENTO ele é apagado na
    hora. `verify_token_enc` = `encrypt_json({"verify_token": <token>})`, o
    token do handshake, gerado pelo DaVinci (`secrets.token_urlsafe(32)`),
    cifrado para poder ser mostrado de novo ao admin (sob clique, registrado no
    Histórico).

    `teste` = o "DaVinci Zap Teste" (só a WABA de teste; o único que pode ter
    credencial no banco do computador). A falha de assinatura NUNCA vem para
    cá (só Redis): um estranho com curl não escreve no banco.
    """

    __tablename__ = "atendimento_zap_apps"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    app_id: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    # Ex.: "DaVinci Zap — Gerenciador X".
    nome: Mapped[str | None] = mapped_column(String(120), nullable=True)
    segredo_enc: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    verify_token_enc: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    teste: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    ativo: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    # Saúde: o último evento VÁLIDO (assinatura conferida) deste app.
    ultimo_evento_em: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    criado_por: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )


class AtendimentoZapConta(Base, TimestampMixin):
    """Uma WABA (conta WhatsApp Business) e o token que a enxerga.

    `token_enc` = `encrypt_json({"access_token", "scopes", "system_user_id",
    "tipo": "system_user"})` — token de usuário do sistema FUNCIONÁRIO, só com
    as WABAs atribuídas (o administrador enxerga todas do portfólio).

    `token_expira_em`/`token_nunca_vence` são gravados SÓ pela inspeção
    estrita (`zap_api.inspecionar_token`): `expires_at=0` vira
    `token_nunca_vence=true` de forma explícita, e inspeção que falha é erro
    (não grava nada). `inscrito_em` = o `POST /{waba}/subscribed_apps` deu
    certo E o `GET` mostrou só o nosso app.
    """

    __tablename__ = "atendimento_zap_contas"
    __table_args__ = (CheckConstraint(_em("status", ZAP_CONTA_STATUS), name="status"),)

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    # O app que assina os eventos desta WABA. RESTRICT: apagar o app com conta
    # pendurada é engano (o webhook dela ficaria sem segredo).
    app_ref: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("atendimento_zap_apps.id", ondelete="RESTRICT"),
        nullable=False,
    )
    waba_id: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    # O portfólio (Gerenciador de Negócios). O BSUID é por portfólio.
    business_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    nome: Mapped[str | None] = mapped_column(String(120), nullable=True)
    token_enc: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    token_expira_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    token_nunca_vence: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    # Última inspeção ok; última troca (lembrete da rotação semestral).
    token_conferido_em: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    token_trocado_em: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    inscrito_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="novo", server_default=text("'novo'")
    )
    ultimo_ok_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Frase em português, NUNCA com o token.
    ultimo_erro: Mapped[str | None] = mapped_column(Text, nullable=True)
    # O `error.code` da Meta (131047, 190, 368…).
    ultimo_erro_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ultimo_erro_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AtendimentoZapNumero(Base, TimestampMixin):
    """Um número de WhatsApp — o botão "pausar" mora aqui.

    `phone_number_id` é a chave do webhook (`metadata.phone_number_id`),
    único como o `uq_dm_contas_ig_user_id` do Instagram: o mesmo número em
    duas linhas faria a mensagem cair na conversa errada sem erro nenhum.

    `envio_ligado` nasce FALSE e é lido do banco a cada envio: "pausar" e
    "pausar todos" valem na hora, sem reiniciar nada. Nenhuma lista libera
    uma chave desligada: `destinos_teste` (somada à global
    `atendimento_zap_destinos_teste`) só RESTRINGE, comparada pelo número
    normalizado (a mesma regra de `contatos.wa_id_norm`).

    `etapa` é o "um por vez" da migração do Duoke (teste → preparando →
    aguardando_duoke → migrando → ativo; pausado; desligado).
    `registro_tentativas` guarda cada `POST /{pnid}/register`: a Meta bloqueia
    o número por 72 h na 10ª em 72 h (133016) e o DaVinci para na 3ª.
    `pin_enc` = `encrypt_json({"pin": <pin>})`, o PIN da verificação em 2
    etapas, mostrado só sob clique.
    """

    __tablename__ = "atendimento_zap_numeros"
    __table_args__ = (
        CheckConstraint(_em("etapa", ZAP_NUMERO_ETAPAS), name="etapa"),
        CheckConstraint(_em("saude_status", ZAP_NUMERO_SAUDE), name="saude_status"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    conta_ref: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("atendimento_zap_contas.id", ondelete="RESTRICT"),
        nullable=False,
    )
    phone_number_id: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    # Como a Meta mostra ("+55 11 9xxxx-xxxx"). É a `conta` da conversa.
    display_phone_number: Mapped[str] = mapped_column(String(32), nullable=False)
    # Nome de exibição e se está aprovado (APPROVED, PENDING_REVIEW…).
    verified_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    nome_status: Mapped[str | None] = mapped_column(String(24), nullable=True)
    # O nome da linha na barra ("Charlots · (11) 9xxxx-xxxx"); sem ele, o
    # `verified_name` ou o número.
    nome_na_barra: Mapped[str | None] = mapped_column(String(120), nullable=True)
    # Para o nome e, no futuro, o escopo da equipe e o `sac_fone`.
    marca_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("marcas.id", ondelete="SET NULL"),
        nullable=True,
    )
    # O canal `zap:<pnid>` em `atendimento_canais` (`canais_externos.garantir_canal`).
    canal_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("atendimento_canais.id", ondelete="SET NULL"),
        nullable=True,
        unique=True,
    )
    # Número de teste da Meta.
    teste: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    etapa: Mapped[str] = mapped_column(
        String(16), nullable=False, default="preparando", server_default=text("'preparando'")
    )
    envio_ligado: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    destinos_teste: Mapped[list[str]] = mapped_column(
        ARRAY(String(32)), nullable=False, default=list, server_default=text("'{}'")
    )
    # Teto de envios por dia DESTE número (a partir do volume medido no
    # Duoke). NULL = `atendimento_zap_max_dia_numero`.
    teto_dia: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pin_enc: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    registro_tentativas: Mapped[list[datetime]] = mapped_column(
        ARRAY(DateTime(timezone=True)),
        nullable=False,
        default=list,
        server_default=text("'{}'"),
    )
    # quality_rating, messaging_limit, name_status, throughput.
    saude: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    saude_status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="novo", server_default=text("'novo'")
    )
    ultimo_recebido_em: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    ultimo_enviado_em: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    ultimo_ok_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ultimo_erro: Mapped[str | None] = mapped_column(Text, nullable=True)
    ultimo_erro_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ultimo_erro_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # O roteiro da migração (§13.3 do desenho).
    duoke_liberado_em: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    migrado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    observacao: Mapped[str | None] = mapped_column(Text, nullable=True)


class AtendimentoZapContato(Base, TimestampMixin):
    """O cliente num número. O `id` é o `externo_id` da conversa (P3).

    Casa por `(numero, bsuid)` e, sem ele, por `(numero, wa_id_norm)` — os
    dois UNIQUE PARCIAIS abaixo. `wa_id` é como veio (é o `to` do envio);
    `wa_id_norm` é só dígitos com o 9º dígito brasileiro posto SÓ em número
    com cara de celular, e serve só para CASAR, nunca para enviar. Mesmo
    telefone com BSUID diferente = outra pessoa (número reciclado): contato
    novo, e o antigo perde o `wa_id_norm`.

    `ultima_recebida_em` é o relógio da janela de 24 h (`GREATEST(atual, ts
    da Meta)`: reentrega atrasada nunca encolhe a janela). `opt_in_*` é a
    prova para mensagem que a LOJA inicia (modelo, Onda 5); `opt_out_tipo`
    `todos` barra qualquer modelo, `marketing` só o de marketing (131050).

    Sem gatilho do Histórico (telefone, BSUID e nome do perfil).
    """

    __tablename__ = "atendimento_zap_contatos"
    __table_args__ = (
        CheckConstraint(_em("opt_out_tipo", ZAP_OPT_OUT_TIPOS, nulo=True), name="opt_out_tipo"),
        Index(
            "uq_atendimento_zap_contatos_bsuid",
            "numero_ref",
            "bsuid",
            unique=True,
            postgresql_where=text("bsuid IS NOT NULL"),
        ),
        Index(
            "uq_atendimento_zap_contatos_wa_id_norm",
            "numero_ref",
            "wa_id_norm",
            unique=True,
            postgresql_where=text("wa_id_norm IS NOT NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    # Nome à mão: pela convenção ficaria a 1 caractere do teto do Postgres.
    numero_ref: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey(
            "atendimento_zap_numeros.id",
            ondelete="RESTRICT",
            name="fk_atendimento_zap_contatos_numero",
        ),
        nullable=False,
    )
    # `user_id` da Meta (por portfólio).
    bsuid: Mapped[str | None] = mapped_column(String(64), nullable=True)
    wa_id: Mapped[str | None] = mapped_column(String(20), nullable=True)
    wa_id_norm: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # `contacts[].profile.name`.
    nome_perfil: Mapped[str | None] = mapped_column(String(120), nullable=True)
    ultima_recebida_em: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # 72 h do anúncio CTWA (só afeta o custo).
    janela_gratis_ate: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    opt_in_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    opt_in_origem: Mapped[str | None] = mapped_column(String(32), nullable=True)
    opt_out_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    opt_out_tipo: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # O horário (da Meta) do último gesto de preferência APLICADO (`stop`/
    # `resume` do `user_preferences`, "PARAR" na mensagem): a Meta não garante
    # a ordem, e o gesto mais antigo que chega depois não vale.
    preferencia_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    bloqueado: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    # Pedido de exclusão LGPD (os dados acima são anonimizados).
    apagado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    conversa_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("atendimento_conversas.id", ondelete="SET NULL"),
        nullable=True,
        unique=True,
    )


class AtendimentoZapEvento(Base):
    """A caixa de entrada do webhook: UM item por linha.

    `chave` (UNIQUE) é a idempotência da reentrega da Meta, gravada com
    `INSERT … ON CONFLICT (chave) DO NOTHING`:

      m:<wamid>                       — mensagem
      s:<wamid>:<status>              — status (o UNIQUE já é o histórico de
                                        status: não há tabela à parte)
      e:<sha256 de entry.id + entry.time + item canônico>
                                      — erro, conta, preferência, modelo e
                                        objeto inesperado (o `entry.time`
                                        entra para um `account_update` igual,
                                        dias depois, NÃO ser engolido)
      j:<sha256 do corpo>             — JSON inválido (o corpo cru no payload)

    `payload` = o item + `metadata` + SÓ o `contacts[]` do remetente daquele
    item (o `value` pode trazer outros clientes do mesmo lote).

    A fila: evento novo nasce com `proxima_em` NULL; quem pega usa
    `processado_em IS NULL AND (proxima_em IS NULL OR proxima_em <= now())`
    (`ix_atendimento_zap_eventos_fila`, parcial). O `ignorado` já nasce com
    `processado_em`. `erro` é só o TIPO do erro, nunca o texto do cliente.
    Retenção de 30 dias. Sem gatilho do Histórico.
    """

    __tablename__ = "atendimento_zap_eventos"
    __table_args__ = (
        CheckConstraint(_em("tipo", ZAP_EVENTO_TIPOS), name="tipo"),
        CheckConstraint(_em("resultado", ZAP_EVENTO_RESULTADOS, nulo=True), name="resultado"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=False), primary_key=True)
    chave: Mapped[str] = mapped_column(String(191), nullable=False, unique=True)
    app_ref: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("atendimento_zap_apps.id", ondelete="SET NULL"),
        nullable=True,
    )
    waba_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    phone_number_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # `messages`, `user_preferences`, `account_update`,
    # `message_template_status_update`… (NULL no JSON inválido).
    campo: Mapped[str | None] = mapped_column(String(48), nullable=True)
    tipo: Mapped[str] = mapped_column(String(16), nullable=False)
    payload: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    recebido_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    processado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    proxima_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    tentativas: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    resultado: Mapped[str | None] = mapped_column(String(32), nullable=True)
    erro: Mapped[str | None] = mapped_column(String(200), nullable=True)


class AtendimentoZapMidia(Base, TimestampMixin):
    """Foto, áudio, vídeo e documento do Zap (schema na Onda 1, baixador na Onda 3).

    Contraria DE PROPÓSITO o "nunca o arquivo" de `atendimento_mensagens`, e
    só para o Zap: o id da mídia vale 7 dias na Meta e, depois da migração,
    não há celular onde olhar. `caminho` é relativo a `uploads_dir`
    (`atendimento/zap/<numero>/<aaaa>/<mm>/<uuid>.<ext>`, gerado pelo
    servidor); `mime` é o declarado, `mime_detectado` o da assinatura do
    arquivo (é este que vale). UNIQUE(numero, media_id): não baixa duas vezes.

    Sem gatilho do Histórico (nome do arquivo do cliente).
    """

    __tablename__ = "atendimento_zap_midias"
    __table_args__ = (
        CheckConstraint(_em("direcao", ZAP_MIDIA_DIRECOES), name="direcao"),
        CheckConstraint(_em("status", ZAP_MIDIA_STATUS), name="status"),
        # O `media_id` é por número: a mesma mídia não é baixada duas vezes.
        UniqueConstraint("numero_ref", "media_id"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    numero_ref: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("atendimento_zap_numeros.id", ondelete="RESTRICT"),
        nullable=False,
    )
    # CASCADE: a mídia é da mensagem. Índice: o balão acha a mídia pela mensagem.
    mensagem_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("atendimento_mensagens.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    direcao: Mapped[str] = mapped_column(String(16), nullable=False)
    # O id da Meta (entrada: vem no webhook; saída: depois do upload).
    media_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    mime: Mapped[str | None] = mapped_column(String(100), nullable=True)
    mime_detectado: Mapped[str | None] = mapped_column(String(100), nullable=True)
    sha256_meta: Mapped[str | None] = mapped_column(String(100), nullable=True)
    tamanho: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # Saneado.
    nome_original: Mapped[str | None] = mapped_column(String(255), nullable=True)
    caminho: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sha256_local: Mapped[str | None] = mapped_column(CHAR(64), nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pendente", server_default=text("'pendente'")
    )
    tentativas: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    proxima_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Recebida + 7 dias (o id da mídia na Meta).
    expira_meta_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Retenção (`atendimento_zap_retencao_midia_dias`).
    apagar_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    erro: Mapped[str | None] = mapped_column(String(200), nullable=True)
    baixada_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# A fila do processador (`zap_entrada.processar_eventos`): só o que falta
# processar, o evento novo (`proxima_em` NULL) primeiro, depois por ordem de
# chegada. Fora do `__table_args__` porque o NULLS FIRST é expressão da coluna.
Index(
    "ix_atendimento_zap_eventos_fila",
    AtendimentoZapEvento.proxima_em.asc().nulls_first(),
    AtendimentoZapEvento.id,
    postgresql_where=text("processado_em IS NULL"),
)
