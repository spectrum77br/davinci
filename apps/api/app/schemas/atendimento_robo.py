"""Contrato robô do Mac mini → DaVinci (routers/atendimento_robo.py).

O robô mantém um perfil do AdsPower por loja (Temu, AliExpress) com o chat do
Seller Center aberto e só ESCUTA. Este é o formato FIXO do que ele manda — o
robô e a recepção foram construídos em paralelo contra ele. Campo a mais é
ignorado (o robô pode crescer sem quebrar a recepção); campo que falta ou
fora do formato é 422, sem ecoar o valor (pode ser texto de comprador).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

# O `user_id` de perfil da API local do AdsPower ("k1dkegpc").
_PERFIL = r"^[A-Za-z0-9_-]{1,64}$"

# Até 200 eventos por chamada e 2 MB por corpo (o contrato). O router ainda
# recusa (413) a chamada inteira acima de `EVENTOS_MAX_BYTES`.
EVENTOS_MAX = 200
CORPO_MAX = 2 * 1024 * 1024


class PulsoIn(BaseModel):
    perfil_id: str = Field(pattern=_PERFIL)
    plataforma: Literal["temu", "aliexpress"]
    loja: str = Field(min_length=1, max_length=200)
    estado: Literal["iniciando", "lendo", "sessao_caiu", "erro"]
    url: str = Field(default="", max_length=4096)
    detalhe: str | None = Field(default=None, max_length=4000)
    versao: str = Field(default="", max_length=200)


class EventoIn(BaseModel):
    tipo: Literal["http", "ws"]
    url: str = Field(default="", max_length=16384)
    # http: o verbo; ws: como ler o corpo ("texto", "binario" em base64, "json").
    metodo: str | None = None
    status: int | None = None
    recebido_em: datetime | None = None
    corpo: str = Field(default="", max_length=CORPO_MAX)

    # Os campos INFORMATIVOS não derrubam a leva: um carimbo ilegível ou um
    # status estranho num evento viram None (o leitor não depende deles). O
    # que decide o que o evento é (tipo, corpo) continua estrito.
    @field_validator("metodo", mode="before")
    @classmethod
    def _metodo(cls, v: Any) -> str | None:
        return (v.strip()[:16] or None) if isinstance(v, str) else None

    @field_validator("status", mode="before")
    @classmethod
    def _status(cls, v: Any) -> int | None:
        if isinstance(v, bool):
            return None
        if isinstance(v, int):
            return v
        return int(v) if isinstance(v, str) and v.strip().isdigit() else None

    @field_validator("recebido_em", mode="before")
    @classmethod
    def _recebido_em(cls, v: Any) -> Any:
        if isinstance(v, datetime) or v is None:
            return v
        if isinstance(v, str):
            try:
                return datetime.fromisoformat(v.strip().replace("Z", "+00:00"))
            except ValueError:
                return None
        return None


class EventosIn(BaseModel):
    perfil_id: str = Field(pattern=_PERFIL)
    plataforma: Literal["temu", "aliexpress"]
    loja: str = Field(min_length=1, max_length=200)
    eventos: list[EventoIn] = Field(default_factory=list, max_length=EVENTOS_MAX)


class RoboOkOut(BaseModel):
    ok: bool = True


class EventosOut(BaseModel):
    ok: bool = True
    # Mensagens NOVAS gravadas (a mesma mensagem de novo não conta).
    gravadas: int
    # Conversas lidas nesta leva (criadas ou atualizadas).
    conversas: int
    # Eventos que o leitor NÃO reconheceu (rota/quadro desconhecido). Os de
    # controle (batimento, ack, contador) são reconhecidos e não contam aqui.
    ignorados: int
    # Conversas que falharam ao gravar (as outras entraram). Campo a mais no
    # contrato: o robô pode ignorar.
    erros: int = 0
