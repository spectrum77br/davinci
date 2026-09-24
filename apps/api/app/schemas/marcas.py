"""Schemas de Cadastros › Marcas e Redes Sociais (15/09/2026).

Regras de senha (iguais nas duas entidades, padrão nf_faturador):
  • Create: `senha` texto → cifra; vazio/None → sem senha.
  • Patch:  chave AUSENTE → não altera; "" ou null → LIMPA; texto → cifra.
  • Out:    nunca devolve a senha — só `has_senha`.
Só `field_validator` por campo (nunca model_validator mode="before"): um
validador de modelo injetaria chaves e o `exclude_unset` do PATCH gravaria
None em campo que o front não mandou (regressão já vista em companies).
`senha` fica com `repr=False`: o Sentry serializa as variáveis locais das
frames com repr(), e o repr do body pydantic carregaria a senha em claro num
500 — o EventScrubber só mascara CHAVES de dict, não texto dentro de string.
"""

from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.segments import _slugify

InpiStatus = Literal["nao_registrado", "aguardando", "registrado", "indeferido", "expirado"]
# As 5 redes da planilha (enums.RedeSocialPlataforma).
Plataforma = Literal["instagram", "facebook", "twitter", "tiktok", "youtube"]
VerificacaoStatus = Literal["nao_solicitado", "em_andamento", "verificado", "recusado"]
EmailContexto = Literal[
    "sac", "ml", "shopee", "amazon", "aliexpress", "temu", "tiktok", "shein", "magalu",
    "site", "geral",
]

_TEXTO_MARCA = (
    "usuario",
    "email",
    "dominio_br",
    "dominio",
    "dono_dominio",
    "classe",
    "funcao",
    "tipo",
    "obs",
)
_TEXTO_REDE = ("usuario", "email", "obs", "verificacao_obs", "adspower_user_id")


def _texto(v: Any) -> str | None:
    """Texto livre: strip; vazio vira None."""
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _senha(v: Any) -> str | None:
    """Senha: não faz strip (espaço pode ser parte); vazio vira None."""
    if v is None:
        return None
    s = str(v)
    return s if s else None


def _handle(v: Any) -> str | None:
    """@ público sem o "@" inicial; vazio/"?" vira None."""
    if v is None:
        return None
    s = str(v).strip().lstrip("@").strip()
    if not s or s == "?":
        return None
    if len(s) > 128:
        raise ValueError("conta_too_long")
    return s


def _email(v: Any) -> str | None:
    if v is None:
        return None
    s = str(v).strip().lower()
    if not s:
        return None
    if "@" not in s or " " in s or s.startswith("@") or s.endswith("@"):
        raise ValueError("email_invalido")
    return s


def _digitos(v: Any) -> str | None:
    """Fone: só dígitos (aceita int da planilha, "11 98351-7003", etc.)."""
    if v is None:
        return None
    s = "".join(ch for ch in str(v) if ch.isdigit())
    if not s:
        return None
    if len(s) > 20:
        raise ValueError("fone_too_long")
    return s


def _url(v: Any) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    if not (s.startswith("http://") or s.startswith("https://")):
        raise ValueError("url_invalid")
    return s


def _site(v: Any) -> str | None:
    try:
        return _url(v)
    except ValueError as e:
        raise ValueError("site_invalido") from e


def _data(v: Any) -> Any:
    """<Input type=date> manda "" quando limpo — vira None (o pydantic faz o
    resto: "2034-07-04" → date)."""
    if v == "":
        return None
    return v


def _nome(v: Any) -> str:
    if v is None:
        raise ValueError("nome_required")
    s = str(v).strip()
    if not s:
        raise ValueError("nome_required")
    if len(s) > 128:
        raise ValueError("nome_too_long")
    return s


def _slug(v: Any) -> str | None:
    if v is None or v == "":
        return None
    s = _slugify(str(v))
    if not s:
        raise ValueError("slug_invalid")
    if len(s) > 128:
        raise ValueError("slug_too_long")
    return s


# ======================================================================= Marcas


class MarcaCreate(BaseModel):
    nome: str
    slug: str | None = None
    inpi_status: InpiStatus = "nao_registrado"
    usuario: str | None = None
    senha: str | None = Field(default=None, repr=False)
    email: str | None = None
    dominio_br: str | None = None
    dominio: str | None = None
    dono_dominio: str | None = None
    dominio_validade: date | None = None
    classe: str | None = None
    funcao: str | None = None
    tipo: str | None = None
    obs: str | None = None
    ativo: bool = True
    # assinatura dos e-mails (fone/e-mail/senha das redes e a verificação do
    # Zap são da aba Redes Sociais — MarcaSocialPatch, gate redes_sociais:edit)
    company_id: UUID | None = None
    site: str | None = None

    _v_nome = field_validator("nome", mode="before")(_nome)
    _v_slug = field_validator("slug", mode="before")(_slug)
    _v_texto = field_validator(*_TEXTO_MARCA, mode="before")(_texto)
    _v_email = field_validator("email", mode="before")(_email)
    _v_senha = field_validator("senha", mode="before")(_senha)
    _v_data = field_validator("dominio_validade", mode="before")(_data)
    _v_site = field_validator("site", mode="before")(_site)


class MarcaPatch(BaseModel):
    nome: str | None = None
    slug: str | None = None
    inpi_status: InpiStatus | None = None
    usuario: str | None = None
    # Ausente = não altera; "" ou null = limpa; texto = cifra.
    senha: str | None = Field(default=None, repr=False)
    email: str | None = None
    dominio_br: str | None = None
    dominio: str | None = None
    dono_dominio: str | None = None
    dominio_validade: date | None = None
    classe: str | None = None
    funcao: str | None = None
    tipo: str | None = None
    obs: str | None = None
    ativo: bool | None = None
    company_id: UUID | None = None
    site: str | None = None

    @field_validator("nome", mode="before")
    @classmethod
    def _v_nome(cls, v: Any) -> str | None:
        return None if v is None else _nome(v)

    _v_slug = field_validator("slug", mode="before")(_slug)
    _v_texto = field_validator(*_TEXTO_MARCA, mode="before")(_texto)
    _v_email = field_validator("email", mode="before")(_email)
    _v_senha = field_validator("senha", mode="before")(_senha)
    _v_data = field_validator("dominio_validade", mode="before")(_data)
    _v_site = field_validator("site", mode="before")(_site)


class MarcaSocialPatch(BaseModel):
    """Colunas da MARCA que a aba Redes Sociais edita (linha da pivot da
    planilha): fone/usuário/senha das redes, verificação do Zap, função,
    tipo, obs. Gate: `redes_sociais:edit` (routers/redes_sociais.py) — é o
    ÚNICO dono da senha das redes/SAC; a aba Marcas só lê fone/e-mail."""

    sac_fone: str | None = None
    sac_email: str | None = None
    sac_senha: str | None = Field(default=None, repr=False)
    whatsapp_verificacao_status: VerificacaoStatus | None = None
    whatsapp_verificacao_obs: str | None = None
    funcao: str | None = None
    tipo: str | None = None
    obs: str | None = None

    _v_texto = field_validator(
        "funcao", "tipo", "obs", "whatsapp_verificacao_obs", mode="before"
    )(_texto)
    _v_email = field_validator("sac_email", mode="before")(_email)
    _v_senha = field_validator("sac_senha", mode="before")(_senha)
    _v_fone = field_validator("sac_fone", mode="before")(_digitos)


class MarcaOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    nome: str
    slug: str
    inpi_status: str
    usuario: str | None = None
    # A senha nunca é devolvida; só sinaliza se está preenchida.
    has_senha: bool = False
    email: str | None = None
    dominio_br: str | None = None
    dominio: str | None = None
    dono_dominio: str | None = None
    dominio_validade: date | None = None
    classe: str | None = None
    funcao: str | None = None
    tipo: str | None = None
    obs: str | None = None
    ativo: bool
    sac_fone: str | None = None
    sac_email: str | None = None
    has_sac_senha: bool = False
    whatsapp_verificacao_status: str = "nao_solicitado"
    whatsapp_verificacao_obs: str | None = None
    company_id: UUID | None = None
    # Razão social da empresa da assinatura (join; None sem empresa).
    empresa_razao_social: str | None = None
    site: str | None = None
    has_logo: bool = False
    created_at: datetime
    updated_at: datetime


class EmpresaRef(BaseModel):
    """Empresa pra vincular à marca (assinatura) — listada sob `marcas:view`,
    porque o modal de Marcas não pode depender de `empresa:view`."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    apelido: str
    razao_social: str
    cnpj: str | None = None


class MarcaRefEmail(BaseModel):
    """Marca enxuta pro grid de E-mails (email_padroes:view)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    nome: str
    slug: str
    ativo: bool
    sac_email: str | None = None
    has_logo: bool = False
    updated_at: datetime


class MarcaRef(BaseModel):
    """Marca enxuta pros grids de Redes Sociais e E-mails — sem o login/e-mail
    do registro (INPI) nem domínios, que são da permissão `marcas`. Traz as
    colunas da linha da marca na aba r.social (fone/usuário/senha das redes,
    verificação do Zap, função, tipo, obs)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    nome: str
    slug: str
    ativo: bool
    classe: str | None = None
    funcao: str | None = None
    tipo: str | None = None
    obs: str | None = None
    sac_fone: str | None = None
    sac_email: str | None = None
    has_sac_senha: bool = False
    whatsapp_verificacao_status: str = "nao_solicitado"
    whatsapp_verificacao_obs: str | None = None
    has_logo: bool = False
    updated_at: datetime


# =============================================================== Redes Sociais


class RedeSocialCreate(BaseModel):
    marca_id: UUID
    plataforma: Plataforma
    conta: str | None = None
    usuario: str | None = None
    url: str | None = None
    email: str | None = None
    fone: str | None = None
    senha: str | None = Field(default=None, repr=False)
    verificacao_status: VerificacaoStatus = "nao_solicitado"
    verificacao_obs: str | None = None
    obs: str | None = None
    # Perfil do AdsPower que o executor local abre pra publicar nesta conta.
    # Só nas plataformas sem API (hoje o TikTok). Vazio vira NULL.
    adspower_user_id: str | None = None
    ativo: bool = True

    _v_conta = field_validator("conta", mode="before")(_handle)
    _v_texto = field_validator(*_TEXTO_REDE, mode="before")(_texto)
    _v_email = field_validator("email", mode="before")(_email)
    _v_fone = field_validator("fone", mode="before")(_digitos)
    _v_url = field_validator("url", mode="before")(_url)
    _v_senha = field_validator("senha", mode="before")(_senha)


class RedeSocialPatch(BaseModel):
    marca_id: UUID | None = None
    plataforma: Plataforma | None = None
    conta: str | None = None
    usuario: str | None = None
    url: str | None = None
    email: str | None = None
    fone: str | None = None
    # Ausente = não altera; "" ou null = limpa; texto = cifra.
    senha: str | None = Field(default=None, repr=False)
    verificacao_status: VerificacaoStatus | None = None
    verificacao_obs: str | None = None
    obs: str | None = None
    ativo: bool | None = None
    # Postagem automática dos criativos (robô): interruptor + tetos DESTA
    # conta. null nos tetos = usa o padrão do servidor.
    postagem_auto: bool | None = None
    postagem_max_dia: int | None = None
    postagem_intervalo_min: int | None = None
    # A partir de que hora (0-23, Brasília) o robô publica sozinho nesta conta.
    # NULO = não publica sozinho, mesmo com postagem_auto ligado.
    postagem_hora_inicio: int | None = Field(default=None, ge=0, le=23)
    # Perfil do AdsPower que o executor local abre pra publicar nesta conta.
    # Só nas plataformas sem API (hoje o TikTok). Vazio vira NULL.
    adspower_user_id: str | None = None

    _v_conta = field_validator("conta", mode="before")(_handle)
    _v_texto = field_validator(*_TEXTO_REDE, mode="before")(_texto)
    _v_email = field_validator("email", mode="before")(_email)
    _v_fone = field_validator("fone", mode="before")(_digitos)
    _v_url = field_validator("url", mode="before")(_url)
    _v_senha = field_validator("senha", mode="before")(_senha)


class RedeSocialOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    marca_id: UUID
    marca_nome: str = ""
    plataforma: str
    conta: str | None = None
    usuario: str | None = None
    url: str | None = None
    email: str | None = None
    fone: str | None = None
    has_senha: bool = False
    # Valores EFETIVOS = da conta, senão herdados da marca (sac_*).
    email_efetivo: str | None = None
    fone_efetivo: str | None = None
    has_senha_efetiva: bool = False
    senha_origem: Literal["conta", "marca"] | None = None
    verificacao_status: str = "nao_solicitado"
    verificacao_obs: str | None = None
    obs: str | None = None
    ativo: bool
    # Postagem automática (robô dos criativos) — ver models/marca.py.
    postagem_auto: bool = False
    postagem_max_dia: int | None = None
    postagem_intervalo_min: int | None = None
    # A partir de que hora (0-23, Brasília) o robô publica sozinho nesta conta.
    # NULO = não publica sozinho, mesmo com postagem_auto ligado.
    postagem_hora_inicio: int | None = Field(default=None, ge=0, le=23)
    # Perfil do AdsPower (só TikTok). A tela usa pra avisar que falta.
    adspower_user_id: str | None = None
    # Credencial de publicação (redes_sociais_tokens): NUNCA o token, só o
    # que a tela precisa mostrar.
    has_token: bool = False
    token_status: str | None = None
    token_conta_externa: str | None = None
    token_expires_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class RedesSociaisGridRow(BaseModel):
    marca: MarcaRef
    # Chave = plataforma (todas as plataformas presentes, lista vazia quando
    # a marca não tem conta nela).
    cells: dict[str, list[RedeSocialOut]]


class RedesSociaisGridOut(BaseModel):
    plataformas: list[str]
    rows: list[RedesSociaisGridRow]


class ConectarContaIn(BaseModel):
    """Token de publicação colado pelo operador na tela (nunca trafega em
    chat/log). `external_user_id` é opcional: quando vazio, o backend
    descobre pelo próprio token (as Páginas do portfólio e a conta do
    Instagram ligada a cada uma)."""

    access_token: str = Field(repr=False)
    external_user_id: str | None = None

    @field_validator("access_token", mode="before")
    @classmethod
    def _v_token(cls, v: Any) -> str:
        s = str(v or "").strip()
        if len(s) < 20:
            raise ValueError("token_invalido")
        return s


class ContaExternaOut(BaseModel):
    """O que o token enxerga: cada Página e a conta do Instagram ligada a
    ela — é o que a tela mostra pro operador escolher/conferir."""

    page_id: str | None = None
    page_nome: str | None = None
    ig_user_id: str | None = None
    ig_username: str | None = None


class ConexaoOut(BaseModel):
    ok: bool
    external_user_id: str | None = None
    external_username: str | None = None
    token_expires_at: datetime | None = None
    # O que o token enxerga (pra conferência visual na tela).
    contas: list[ContaExternaOut] = []


class SenhaOut(BaseModel):
    senha: str
    # De onde veio: 'conta' (senha própria) ou 'marca' (herdada de sac_senha).
    origem: Literal["conta", "marca"] | None = None


# ============================================================ Padrões de e-mail


def _template(v: Any) -> str:
    """assunto/corpo: obrigatório, só expressões {{ }} (validado de novo no
    serviço, que compila em sandbox)."""
    if v is None:
        raise ValueError("template_required")
    s = str(v)
    if not s.strip():
        raise ValueError("template_required")
    if len(s) > 10_000:
        raise ValueError("template_too_long")
    # Whitelist da AST (services/email_marca): template inválido não é SALVO —
    # senão o /render dos robôs passaria a falhar até alguém apagar o registro.
    from app.services.email_marca import TemplateInvalidoError, validar_template

    try:
        validar_template(s)
    except TemplateInvalidoError as e:
        raise ValueError(str(e)) from e
    return s


class EmailPadraoCreate(BaseModel):
    marca_id: UUID
    contexto: EmailContexto
    nome: str
    remetente_nome: str | None = None
    remetente_email: str | None = None
    assunto: str
    corpo: str
    incluir_logo: bool = True
    incluir_assinatura: bool = True
    ativo: bool = True

    _v_nome = field_validator("nome", mode="before")(_nome)
    _v_texto = field_validator("remetente_nome", mode="before")(_texto)
    _v_email = field_validator("remetente_email", mode="before")(_email)
    _v_tpl = field_validator("assunto", "corpo", mode="before")(_template)


class EmailPadraoPatch(BaseModel):
    marca_id: UUID | None = None
    contexto: EmailContexto | None = None
    nome: str | None = None
    remetente_nome: str | None = None
    remetente_email: str | None = None
    assunto: str | None = None
    corpo: str | None = None
    incluir_logo: bool | None = None
    incluir_assinatura: bool | None = None
    ativo: bool | None = None

    @field_validator("nome", mode="before")
    @classmethod
    def _v_nome(cls, v: Any) -> str | None:
        return None if v is None else _nome(v)

    @field_validator("assunto", "corpo", mode="before")
    @classmethod
    def _v_tpl(cls, v: Any) -> str | None:
        return None if v is None else _template(v)

    _v_texto = field_validator("remetente_nome", mode="before")(_texto)
    _v_email = field_validator("remetente_email", mode="before")(_email)


class EmailPadraoOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    marca_id: UUID
    marca_nome: str = ""
    contexto: str
    nome: str
    remetente_nome: str | None = None
    remetente_email: str | None = None
    assunto: str
    corpo: str
    incluir_logo: bool
    incluir_assinatura: bool
    ativo: bool
    created_at: datetime
    updated_at: datetime


class EmailPadroesGridRow(BaseModel):
    marca: MarcaRefEmail
    cells: dict[str, list[EmailPadraoOut]]


class EmailPadroesGridOut(BaseModel):
    contextos: list[str]
    rows: list[EmailPadroesGridRow]


class EmailPreviewIn(BaseModel):
    """Prévia de um rascunho (ainda não salvo)."""

    marca_id: UUID
    assunto: str
    corpo: str
    incluir_logo: bool = True
    incluir_assinatura: bool = True
    remetente_nome: str | None = None
    remetente_email: str | None = None
    # Valores de exemplo pros placeholders de contexto (cliente, pedido…).
    variaveis: dict[str, str] = {}

    _v_tpl = field_validator("assunto", "corpo", mode="before")(_template)
    _v_texto = field_validator("remetente_nome", mode="before")(_texto)
    _v_email = field_validator("remetente_email", mode="before")(_email)


class EmailRenderIn(BaseModel):
    variaveis: dict[str, str] = {}


class EmailRenderOut(BaseModel):
    assunto: str
    html: str
    text: str
    from_email: str | None = None
    from_name: str | None = None
    reply_to: str | None = None
    # content-ids das imagens inline que o HTML referencia (logo, whatsapp).
    inline_images: list[str] = []
    # O que falta na marca pra assinatura sair completa (prévia mostra).
    avisos: list[str] = []
    # Placeholders desconhecidos no template (só na prévia).
    variaveis_desconhecidas: list[str] = []


class EmailTesteIn(BaseModel):
    para: str
    variaveis: dict[str, str] = {}

    @field_validator("para", mode="before")
    @classmethod
    def _v_para(cls, v: Any) -> str:
        s = _email(v)
        if not s:
            raise ValueError("email_invalido")
        return s


class EmailTesteOut(BaseModel):
    ok: bool
    # 'mailjet' = saiu de verdade; 'console' = ambiente sem chave, só no log.
    sender: str
    para: str


__all__ = [
    "ConectarContaIn",
    "ConexaoOut",
    "ContaExternaOut",
    "EmailContexto",
    "EmailPadraoCreate",
    "EmailPadraoOut",
    "EmailPadraoPatch",
    "EmailPadroesGridOut",
    "EmailPadroesGridRow",
    "EmailPreviewIn",
    "EmailRenderIn",
    "EmailRenderOut",
    "EmailTesteIn",
    "EmailTesteOut",
    "EmpresaRef",
    "InpiStatus",
    "MarcaCreate",
    "MarcaOut",
    "MarcaPatch",
    "MarcaRef",
    "MarcaRefEmail",
    "MarcaSocialPatch",
    "Plataforma",
    "RedeSocialCreate",
    "RedeSocialOut",
    "RedeSocialPatch",
    "RedesSociaisGridOut",
    "RedesSociaisGridRow",
    "SenhaOut",
    "VerificacaoStatus",
]
