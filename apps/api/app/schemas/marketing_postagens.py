"""Schemas do robô de postagem dos criativos (Eduardo, 15/09/2026).

Regra que vale pro arquivo inteiro: **token não sai em Out nenhum**. A conta
aparece com `has_token` (tem ou não), nunca com o valor — o mesmo desenho das
senhas em schemas/marcas.py, e o models/marca.py já tinha decidido que a
credencial de publicação mora cifrada em tabela filha, fora de qualquer
resposta de API.

Só `field_validator` por campo (nunca model_validator mode="before"): um
validador de modelo injetaria chaves e o `exclude_unset` do PATCH gravaria
None em campo que o front não mandou — regressão já vista em companies.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Teto da legenda na Meta (Instagram e Facebook). Barrar aqui é o que evita
# subir um vídeo de 80 MB pra Graph API recusar o texto no último passo.
LEGENDA_MAX = 2200


def _legenda(v: Any) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    if len(s) > LEGENDA_MAX:
        raise ValueError("legenda_too_long")
    return s


def _quando(v: Any) -> Any:
    """`<input type="datetime-local">` limpo manda "" — vira None (= publicar
    agora). O pydantic faz o resto; a conversão pra UTC é do serviço
    (`postagens.para_utc`), porque naive aqui significa Brasília."""
    if v == "":
        return None
    return v


def _ids(v: Any) -> Any:
    """Contas marcadas no modal: tira repetido mantendo a ordem (clicar duas
    vezes na mesma conta não pode virar duas postagens — e o índice único do
    banco devolveria um 409 confuso)."""
    if not isinstance(v, list):
        return v
    vistos: list[Any] = []
    for item in v:
        if item not in vistos:
            vistos.append(item)
    return vistos


class PostagemCreate(BaseModel):
    creative_id: UUID
    file_id: UUID
    # Uma postagem por conta marcada; o serviço recusa a operação inteira no
    # primeiro motivo (o operador marcou as N esperando as N).
    rede_social_ids: list[UUID] = Field(min_length=1)
    legenda: str | None = None
    # ISO com offset, ou naive = Brasília. None = publicar no próximo tick.
    agendado_para: datetime | None = None
    # share_to_feed, thumb_offset… (espelha MarketingCommand.payload)
    opcoes: dict[str, Any] = {}

    _v_legenda = field_validator("legenda", mode="before")(_legenda)
    _v_quando = field_validator("agendado_para", mode="before")(_quando)
    _v_ids = field_validator("rede_social_ids", mode="before")(_ids)


class PostagemPatch(BaseModel):
    """Só enquanto a postagem está `agendado` — depois disso o vídeo já está a
    caminho da Meta e mudar a legenda aqui não mudaria nada lá."""

    legenda: str | None = None
    agendado_para: datetime | None = None

    _v_legenda = field_validator("legenda", mode="before")(_legenda)
    _v_quando = field_validator("agendado_para", mode="before")(_quando)


class PostagemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    creative_id: UUID
    file_id: UUID
    # Nome do arquivo (join): a linha do criativo pode ter vários e a tela
    # precisa dizer QUAL vídeo foi publicado.
    file_name: str | None = None
    # NULL quando a conta foi apagada depois — por isso plataforma/conta são
    # SNAPSHOT da hora do agendamento, e não um join.
    rede_social_id: UUID | None = None
    plataforma: str
    conta: str | None = None
    legenda: str | None = None
    status: str
    origem: str
    agendado_para: datetime | None = None
    publicado_em: datetime | None = None
    post_url: str | None = None
    # Erro traduzido / "DRY: publicaria em …" do modo seco.
    result: str | None = None
    # O vídeo CHEGOU a subir pra Meta. Não é o id do post (esse é `post_url`
    # quando confirmado): é a marca de que existe algo lá pra conferir antes
    # de qualquer retentativa. A tela usa pra avisar "não republique".
    tem_container: bool = False
    attempts: int = 0
    marca_nome: str | None = None
    created_at: datetime
    updated_at: datetime


class ContaParaPostarOut(BaseModel):
    """Uma conta da marca no modal. `motivo` é o CÓDIGO da recusa
    (conta_sem_token, conta_inativa, plataforma_nao_suportada…) — o front
    traduz e mostra no checkbox desabilitado."""

    model_config = ConfigDict(from_attributes=True)

    rede_social_id: UUID
    plataforma: str
    conta: str | None = None
    ativo: bool = True
    # Existe credencial cifrada pra essa conta. O token em si NUNCA sai daqui.
    has_token: bool = False
    # Interruptor e tetos DESTA conta (redes_sociais): a tela mostra "o robô
    # não posta sozinho aqui" e o teto em vigor sem o operador ir noutra aba.
    postagem_auto: bool = False
    postagem_max_dia: int | None = None
    postagem_intervalo_min: int | None = None
    pode_postar: bool = False
    motivo: str | None = None


__all__ = [
    "LEGENDA_MAX",
    "ContaParaPostarOut",
    "PostagemCreate",
    "PostagemOut",
    "PostagemPatch",
]
