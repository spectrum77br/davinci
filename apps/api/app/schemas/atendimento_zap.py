"""Contratos da tela "Números do WhatsApp" (Zap, RF10, Onda 4 — docs/zap/desenho.md §8.4/§8.5).

Nenhum `*Out` leva segredo: a chave secreta do app, o token, o código de
verificação e o PIN nunca saem por aqui (o código e o PIN só pelas duas
rotas que revelam, registradas no Histórico). Os `*In` que levam segredo
usam os nomes que a máscara do Histórico reconhece (`app_secret`,
`access_token`, `pin`) — a rota grava o corpo, mascarado.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

EtapaZap = Literal[
    "teste", "preparando", "aguardando_duoke", "migrando", "ativo", "pausado", "desligado"
]
CasoZap = Literal["A", "A2", "B"]


class ChavesZapOut(BaseModel):
    # O EFETIVO (com a chave geral junto).
    leitura: bool
    envio: bool
    cadastro: bool
    producao: bool
    # A lista de teste GLOBAL está preenchida? (quantos/quais, não)
    lista_de_teste: bool


class AppZapOut(BaseModel):
    id: UUID
    app_id: str
    nome: str | None = None
    teste: bool
    ativo: bool
    ultimo_evento_em: datetime | None = None
    webhook_url: str
    # Até quando a chave ANTERIOR ainda vale (troca de rotina).
    segredo_anterior_ate: datetime | None = None
    falhas_de_assinatura: int = 0


class TokenZapOut(BaseModel):
    ok: bool
    frase: str
    nunca_vence: bool
    expira_em: datetime | None = None
    conferido_em: datetime | None = None
    trocado_em: datetime | None = None


class AppInscritoOut(BaseModel):
    id: str
    nome: str | None = None


class ContaZapOut(BaseModel):
    id: UUID
    app_ref: UUID
    waba_id: str
    business_id: str | None = None
    nome: str | None = None
    status: str
    token: TokenZapOut
    inscrito_em: datetime | None = None
    inscricao_conferida_em: datetime | None = None
    nosso_inscrito: bool | None = None
    outros_apps: list[AppInscritoOut] = Field(default_factory=list)
    ultimo_erro: str | None = None
    ultimo_erro_em: datetime | None = None


class ConexaoOut(BaseModel):
    codigo: str
    frase: str


class RegistroOut(BaseModel):
    pode: bool
    motivo: str | None = None
    tentativas_72h: int = 0


class NumeroZapOut(BaseModel):
    id: UUID
    conta_ref: UUID
    phone_number_id: str
    display_phone_number: str
    verified_name: str | None = None
    nome_status: str | None = None
    nome_na_barra: str | None = None
    marca_id: UUID | None = None
    canal_id: UUID | None = None
    modo: str | None = None
    teste: bool
    etapa: str
    caso_migracao: str | None = None
    envio_ligado: bool
    # Os celulares da lista de teste DESTE número (normalizados) — do dono, só admin vê.
    destinos_teste: list[str] = Field(default_factory=list)
    teto_dia: int | None = None
    teto_efetivo: int
    enviados_hoje: int = 0
    respostas_mes: int = 0
    cobradas_mes: int = 0
    saude_status: str
    qualidade: str | None = None
    limite: str | None = None
    conexao: ConexaoOut
    ultimo_recebido_em: datetime | None = None
    ultimo_enviado_em: datetime | None = None
    ultimo_erro: str | None = None
    ultimo_erro_em: datetime | None = None
    tem_pin: bool = False
    registro: RegistroOut
    webhook_outro: list[str] = Field(default_factory=list)
    testado_em: datetime | None = None
    observacao: str | None = None
    duoke_liberado_em: datetime | None = None
    migrado_em: datetime | None = None


class AlertaZapOut(BaseModel):
    codigo: str
    nivel: str
    frase: str
    app_ref: UUID | None = None
    conta_ref: UUID | None = None
    numero_ref: UUID | None = None


class EventosParadosOut(BaseModel):
    total: int = 0
    por_resultado: dict[str, int] = Field(default_factory=dict)


class TelaZapOut(BaseModel):
    chaves: ChavesZapOut
    campos_do_webhook: list[str]
    apps: list[AppZapOut]
    contas: list[ContaZapOut]
    numeros: list[NumeroZapOut]
    alertas: list[AlertaZapOut]
    eventos_parados: EventosParadosOut


class AppZapIn(BaseModel):
    nome: str | None = Field(default=None, max_length=120)
    app_id: str = Field(min_length=1, max_length=32)
    # A CHAVE SECRETA do app (Configurações → Básico): só neste corpo, nunca volta.
    app_secret: str = Field(min_length=1, max_length=256)
    teste: bool = False


class WebhookZapOut(BaseModel):
    url: str
    campos: list[str]


class VerifyTokenOut(BaseModel):
    verify_token: str


class TrocarChaveIn(BaseModel):
    app_secret: str = Field(min_length=1, max_length=256)
    motivo: Literal["rotina", "vazou"]


class TrocarChaveOut(BaseModel):
    ok: bool = True
    anterior_ate: datetime | None = None


class ContaZapIn(BaseModel):
    app_ref: UUID
    waba_id: str = Field(min_length=1, max_length=32)
    business_id: str | None = Field(default=None, max_length=32)
    # O TOKEN do usuário do sistema: só neste corpo, nunca volta.
    access_token: str = Field(min_length=1, max_length=4096)


class NumeroDaMetaOut(BaseModel):
    phone_number_id: str
    display_phone_number: str
    verified_name: str | None = None
    nome_status: str | None = None
    qualidade: str | None = None
    teste: bool
    cadastrado: bool


class ContaCadastradaOut(BaseModel):
    conta: ContaZapOut
    numeros: list[NumeroDaMetaOut]


class TrocarTokenIn(BaseModel):
    access_token: str = Field(min_length=1, max_length=4096)


class TokenTrocadoOut(BaseModel):
    conta: ContaZapOut
    lembrete: str


class NumerosDaMetaOut(BaseModel):
    numeros: list[NumeroDaMetaOut]


class InscricaoOut(BaseModel):
    inscrito: bool
    apps: list[AppInscritoOut]
    outros_apps: list[AppInscritoOut]
    frase: str


class NumeroZapIn(BaseModel):
    conta_ref: UUID
    phone_number_id: str = Field(min_length=1, max_length=32)
    nome_na_barra: str | None = Field(default=None, max_length=120)
    marca_id: UUID | None = None
    teto_dia: int | None = Field(default=None, ge=0, le=100000)


class NumeroZapPatch(BaseModel):
    envio_ligado: bool | None = None
    etapa: EtapaZap | None = None
    caso_migracao: CasoZap | None = None
    destinos_teste: list[str] | None = Field(default=None, max_length=10)
    teto_dia: int | None = Field(default=None, ge=0, le=100000)
    marca_id: UUID | None = None
    nome_na_barra: str | None = Field(default=None, max_length=120)
    observacao: str | None = Field(default=None, max_length=2000)
    # O PIN de 6 dígitos da verificação em 2 etapas: cifrado, nunca volta
    # (só pela rota que revela). Vazio = apaga.
    pin: str | None = Field(default=None, pattern=r"^([0-9]{6})?$")


class TestarOut(BaseModel):
    numero: NumeroZapOut
    webhook_outro: list[str]
    frase: str


class PausarTodosOut(BaseModel):
    pausados: int


class RegistrarIn(BaseModel):
    pin: str | None = Field(default=None, pattern=r"^([0-9]{6})?$")


class RegistrarOut(BaseModel):
    ok: bool
    frase: str
    numero: NumeroZapOut


class PinOut(BaseModel):
    pin: str | None = None


class ReprocessadosOut(BaseModel):
    reabertos: int


class OkOut(BaseModel):
    ok: bool = True


class ContatoApagadoOut(BaseModel):
    ok: bool = True
    mensagens: int = 0
    midias: int = 0
    eventos: int = 0


class ApagarPorTelefoneIn(BaseModel):
    # O telefone do cliente que pediu a exclusão (com DDD; com ou sem 55 e o 9).
    telefone: str = Field(min_length=8, max_length=32)


class ContatosApagadosOut(BaseModel):
    ok: bool = True
    contatos: int = 0
    mensagens: int = 0
    midias: int = 0
    eventos: int = 0
