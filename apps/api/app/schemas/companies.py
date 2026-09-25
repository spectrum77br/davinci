import ipaddress
import re
from datetime import date, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, model_validator
from stdnum.br import cnpj as br_cnpj
from stdnum.exceptions import ValidationError as StdValidationError

# 27 BR states + DF. Anything else in `uf` is treated as a foreign-
# company marker — skips the strict CNPJ checksum so the operator can
# register US LLCs, Chinese suppliers, etc. with their local tax id.
_BR_UFS: frozenset[str] = frozenset({
    "AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA", "MT",
    "MS", "MG", "PA", "PB", "PR", "PE", "PI", "RJ", "RN", "RS", "RO",
    "RR", "SC", "SP", "SE", "TO",
})


def _normalize_uf(v: Any) -> str | None:
    if v is None or v == "":
        return None
    s = str(v).upper().strip()
    if len(s) != 2 or not s.isalpha():
        raise ValueError("uf_invalid")
    return s


def _normalize_cnpj(v: Any, *, strict: bool) -> str | None:
    """`strict=True` runs the BR checksum (raises on invalid). `False`
    just keeps the digit characters (truncated to the 14-char column
    width) — used when the company is foreign so a US EIN or Chinese
    tax id can be stored without faking a BR CNPJ."""
    if v is None or v == "":
        return None
    digits = "".join(c for c in str(v) if c.isdigit())
    if not digits:
        return None
    if strict:
        try:
            br_cnpj.validate(digits)
        except StdValidationError as e:
            raise ValueError(f"cnpj_invalid: {e}") from e
    return digits[:14]


# Um IPv4 no começo do texto: "72.60.155.3", "72.60.155.3:1080" ou a linha
# inteira do proxy como o AdsPower exporta, "72.60.155.3:1080:usuario:senha".
_IPV4_NA_FRENTE = re.compile(r"^(\d{1,3}(?:\.\d{1,3}){3})(?::|$)")
_ESQUEMA = re.compile(r"^[a-z][a-z0-9+.-]*://", re.IGNORECASE)


def _normalize_ip(v: Any) -> str | None:
    """IP público de saída da empresa, na forma canônica, ou None.

    Aceita o jeito como o IP costuma ser colado e fica só com o endereço:
      72.60.155.3:1080                  (com a porta)
      72.60.155.3:1080:usuario:senha    (a linha inteira do proxy)
      socks5://usuario:senha@72.60.155.3:1080
      [2606:4700::1111]:1080            (IPv6 entre colchetes)
    A unicidade é sobre o IP: para o marketplace, porta e usuário não mudam
    quem é a máquina.

    Levanta ValueError só com um CÓDIGO ("ip_invalido", "ip_nao_publico"),
    nunca com o texto digitado: ele pode ser a linha do proxy, com a senha.
    Por isso quem chama NÃO deve deixar o erro subir como validação do
    formulário — o 422 do FastAPI devolveria o texto inteiro na resposta.
    """
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    s = _ESQUEMA.sub("", s)
    if "@" in s:
        s = s.rsplit("@", 1)[1]
    if s.startswith("["):
        fim = s.find("]")
        if fim < 0:
            raise ValueError("ip_invalido")
        s = s[1:fim]
    elif (m := _IPV4_NA_FRENTE.match(s)) is not None:
        s = m.group(1)
    try:
        ip = ipaddress.ip_address(s)
    except ValueError:
        raise ValueError("ip_invalido") from None
    # "::ffff:72.60.155.3" é o mesmo IPv4 escrito como IPv6: sem isto ele
    # passaria como "outro IP" e furaria a regra de um por empresa.
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    # IP de rede interna (192.168.x, 10.x, 127.0.0.1...) não é o que o
    # marketplace enxerga: dez empresas com IPs internos diferentes podem sair
    # todas pelo mesmo IP público, e a trava daria uma falsa garantia.
    if not ip.is_global:
        raise ValueError("ip_nao_publico")
    return str(ip)


def _normalize_company_payload(data: Any) -> Any:
    """Cross-field normalisation: validates uf first, then applies the
    appropriate cnpj rule based on whether uf is a BR state. Runs in
    `mode='before'` so the inner field validators see the cleaned
    values.

    Only touches keys that are actually present in the payload — a PATCH
    with just `obs` must NOT inject `uf=None`/`cnpj=None`, otherwise those
    keys become "set" and `model_dump(exclude_unset=True)` wipes the
    stored CNPJ/UF."""
    if not isinstance(data, dict):
        return data
    uf = _normalize_uf(data.get("uf"))
    if "uf" in data:
        data["uf"] = uf
    if "cnpj" in data:
        strict = uf is None or uf in _BR_UFS
        data["cnpj"] = _normalize_cnpj(data.get("cnpj"), strict=strict)
    if "responsavel_nome" in data:
        # "" e "   " viram NULL: senão a tela ganha uma opção-fantasma no
        # filtro "todos responsáveis".
        data["responsavel_nome"] = (data.get("responsavel_nome") or "").strip() or None
    return data


class CompanyBase(BaseModel):
    razao_social: str
    apelido: str
    responsavel_id: UUID | None = None
    responsavel_nome: str | None = None
    uf: str | None = None
    cnpj: str | None = None
    inscricao_estadual: str | None = None
    site_url: str | None = None
    operacao: str | None = None
    contabilidade: str | None = None
    ip: str | None = None
    obs: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _normalize(cls, data: Any) -> Any:
        return _normalize_company_payload(data)


class CompanyCreate(CompanyBase):
    pass


class CompanyPatch(BaseModel):
    razao_social: str | None = None
    apelido: str | None = None
    responsavel_id: UUID | None = None
    responsavel_nome: str | None = None
    uf: str | None = None
    cnpj: str | None = None
    inscricao_estadual: str | None = None
    site_url: str | None = None
    operacao: str | None = None
    contabilidade: str | None = None
    ip: str | None = None
    obs: str | None = None
    enabled_marketplaces: list[str] | None = None

    @model_validator(mode="before")
    @classmethod
    def _normalize(cls, data: Any) -> Any:
        return _normalize_company_payload(data)


class CompanyOut(CompanyBase):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    enabled_marketplaces: list[str] = []
    # Situação no AdsPower — só leitura, quem grava é o serviço do Mac.
    ip_adspower: str | None = None
    ip_adspower_em: datetime | None = None
    ip_adspower_erro: str | None = None
    created_at: datetime
    updated_at: datetime


class StoreOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    company_id: UUID
    marketplace: str
    apelido_override: str | None = None
    status: str
    integration_id: UUID | None = None
    bling_store_id: int | None = None
    notes: str | None = None
    created_at: datetime
    updated_at: datetime


class StoreCreate(BaseModel):
    company_id: UUID
    marketplace: str
    apelido_override: str | None = None
    status: str | None = None
    notes: str | None = None
    bling_store_id: int | None = None


class StoreAccountCreate(BaseModel):
    company_id: UUID
    marketplace: str
    phone_id: UUID
    email_id: UUID
    server_id: UUID


class StorePatch(BaseModel):
    apelido_override: str | None = None
    status: str | None = None
    notes: str | None = None
    bling_store_id: int | None = None
    integration_id: UUID | None = None


class GridStoreCell(BaseModel):
    # `id` is None when the cell came from store_info alone (no Store row in
    # `stores` for that company+marketplace) — the green check still shows up
    # but per-cell actions like "Remover" are disabled on the frontend.
    id: UUID | None = None
    status: str
    label: str
    integration_id: UUID | None = None
    bling_store_id: int | None = None
    from_store_info: bool = False


class CertificadoResumo(BaseModel):
    """O que a tabela de Empresas mostra do certificado digital.

    Nunca o arquivo nem a senha — só se existe, se tem senha guardada e quando
    vence. Arquivo e senha continuam atrás das rotas de admin de
    `routers/company_certificates.py`.
    """

    id: UUID
    filename: str
    has_password: bool
    expires_at: date | None = None
    total: int = 1


class CompanyGridRow(BaseModel):
    company: CompanyOut
    stores: dict[str, GridStoreCell | None]
    # Só vem preenchido para admin; para os demais fica None e a coluna some.
    certificado: CertificadoResumo | None = None


class CompanyGridOut(BaseModel):
    marketplaces: list[str]
    rows: list[CompanyGridRow]


class CompanyDetailOut(CompanyOut):
    stores: list[StoreOut] = []


class CadastroBase(BaseModel):
    tipo: str
    provedor: str | None = None
    responsavel_id: UUID | None = None
    codigo: str
    label: str | None = None
    status: str | None = None
    obs: str | None = None


class CadastroCreate(CadastroBase):
    store_ids: list[UUID] | None = None


class CadastroPatch(BaseModel):
    tipo: str | None = None
    provedor: str | None = None
    responsavel_id: UUID | None = None
    codigo: str | None = None
    label: str | None = None
    status: str | None = None
    obs: str | None = None


class CadastroStoreLink(BaseModel):
    store_id: UUID
    alias: str | None = None


class CadastroStoresPut(BaseModel):
    links: list[CadastroStoreLink]


class CadastroRawLinkResolve(BaseModel):
    store_id: UUID
    alias: str | None = None


class CadastroOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    tipo: str
    provedor: str | None = None
    responsavel_id: UUID | None = None
    codigo: str
    label: str | None = None
    status: str
    obs: str | None = None
    raw_links: dict[str, str] = {}
    created_at: datetime
    updated_at: datetime


class CadastroDetailOut(CadastroOut):
    stores: list[CadastroStoreLink] = []


class CadastroGridStoreCell(BaseModel):
    # `store_id` is None when the cell came from store_info alone (the code
    # is in `store_info.phone`/`email` for that platform but no Store row
    # was linked via `cadastros_stores`).
    store_id: UUID | None = None
    alias: str | None = None
    company_apelido: str
    store_status: str
    from_store_info: bool = False


class CadastroGridRow(BaseModel):
    cadastro: CadastroOut
    cells: dict[str, list[CadastroGridStoreCell]]


class CadastroGridOut(BaseModel):
    marketplaces: list[str]
    rows: list[CadastroGridRow]
