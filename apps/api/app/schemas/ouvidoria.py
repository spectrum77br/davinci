"""Schemas da Ouvidoria › Robôs (routers/ouvidoria.py)."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

Modo = Literal["ligado", "silencioso", "desligado"]
Saude = Literal["ok", "falhando", "parado", "desligado"]
StatusFiltro = Literal["abertas", "fechadas", "todas"]


class DestinatarioOut(BaseModel):
    id: str
    nome: str


class RodadaOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    iniciada_em: datetime
    terminada_em: datetime | None = None
    duracao_ms: int | None = None
    ok: bool
    resumo: str | None = None
    contadores: dict = {}
    erro: str | None = None


class RoboOut(BaseModel):
    chave: str
    nome: str
    descricao: str | None = None
    area: str | None = None
    cadencia_texto: str | None = None
    plataformas: list[str] = []
    modo: Modo
    modo_alterado_por: str | None = None
    modo_alterado_em: datetime | None = None
    # Override salvo na tela (texto cru); os IDs efetivos vêm resolvidos em
    # `threema_destinatarios` + `threema_origem` (robo | env | geral | None).
    threema_recipients: str | None = None
    threema_destinatarios: list[DestinatarioOut] = []
    threema_origem: str | None = None
    reaviso_horas: int
    # Config efetiva (padrão do catálogo por baixo do que está salvo).
    config: dict = {}
    # chave da config → rótulo com a unidade ("Cadência esperada (min)"), dos
    # `Parametro` do catálogo: a tela mostra isso em vez do nome cru da chave,
    # sem ter que conhecer robô por robô. Chave sem rótulo aparece crua.
    config_rotulos: dict[str, str] = {}
    ultima_rodada_em: datetime | None = None
    ultima_rodada_ok: bool | None = None
    ultima_rodada_resumo: str | None = None
    ultima_rodada_duracao_ms: int | None = None
    ultima_falha_em: datetime | None = None
    ultima_falha_erro: str | None = None
    abertas: int = 0
    abertas_pessoa: int = 0
    rodadas_hoje: int = 0
    rodadas_hoje_ok: int = 0
    saude: Saude


class ContaOut(BaseModel):
    """Ocorrência de conta (`conta:<integration_id>`) aberta — a conta que o
    robô não conseguiu olhar."""

    conta: str | None = None
    plataforma: str | None = None
    titulo: str
    aberta_em: datetime
    dados: dict = {}


class RoboDetalheOut(RoboOut):
    rodadas: list[RodadaOut] = []
    contas: list[ContaOut] = []


class RoboPatch(BaseModel):
    modo: Modo | None = None
    # "" limpa o override (volta pro env). None = não mexe.
    threema_recipients: str | None = None
    reaviso_horas: int | None = Field(default=None, ge=1, le=24 * 30)
    # Chaves da config (a tela manda o objeto completo). O router valida as
    # chaves numéricas contra os limites do catálogo (`Parametro` em
    # services/ouvidoria) e mescla por cima do que está salvo — 422 com a
    # frase pra tela quando um valor não serve.
    config: dict | None = None

    @field_validator("threema_recipients")
    @classmethod
    def _strip(cls, v: str | None) -> str | None:
        return v.strip() if isinstance(v, str) else v


class RodarOut(BaseModel):
    agendado: bool
    chave: str


class OcorrenciaOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    robo_chave: str
    robo_nome: str | None = None
    chave: str
    plataforma: str | None = None
    conta: str | None = None
    pedido: str | None = None
    titulo: str
    detalhe: str | None = None
    acao: str | None = None
    link: str | None = None
    severidade: str
    precisa_pessoa: bool
    dados: dict = {}
    aberta_em: datetime
    ultima_vista_em: datetime
    avisada_em: datetime | None = None
    reavisada_em: datetime | None = None
    fechada_em: datetime | None = None
    fechamento: str | None = None
    fechada_por: str | None = None


class ResumoOut(BaseModel):
    abertas: int = 0
    abertas_pessoa: int = 0
    novas_hoje: int = 0
    sumiram_7d: int = 0
    tratadas_7d: int = 0
    contas_sem_vigilancia: int = 0


class PorRoboOut(BaseModel):
    chave: str
    nome: str
    abertas: int = 0


class OcorrenciasPage(BaseModel):
    itens: list[OcorrenciaOut]
    # Quantas casam com o filtro (a lista corta em `limit`).
    total: int
    resumo: ResumoOut
    por_robo: list[PorRoboOut]
