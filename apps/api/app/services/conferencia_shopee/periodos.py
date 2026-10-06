"""As 4 semanas de uma rodada da Conferência Shopee e os prazos dela.

Tudo no fuso de Brasília (America/Sao_Paulo; o Brasil não tem horário de verão
desde 2019). As semanas são fixadas quando a execução é CRIADA: uma coleta
atrasada continua cobrindo o mesmo período.

  • `semanal` (terça): S1 = a semana fechada anterior, segunda a domingo;
    S2, S3, S4 = as 3 semanas segunda–domingo antes dela.
  • `parcial` (quinta): S1 = segunda da semana atual até ontem; S2..S4 = o
    mesmo trecho de dias 7, 14 e 21 dias antes (seg–qua × seg–qua).
  • `parcial` numa segunda (ainda não há dia nenhum) vira `semanal`.

Prazos de uma rodada criada no instante T:
  • esperar_afiliados_ate — 15:00 do dia se T < 15:00; senão T + 30 min. Os
    afiliados de ontem saem por volta de 12:10–12:50.
  • corte — nenhuma loja nova começa depois: 17:30 do dia se T < 17:30 (às
    18h o robô de horários do Ads usa os mesmos perfis); senão T + 3 h
    (rodada manual à noite).
  • prazo — corte + 30 min: o varredor marca o que sobrou como `expirada` e
    fecha o relatório.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

FUSO = ZoneInfo("America/Sao_Paulo")

TIPOS = ("semanal", "parcial")
N_SEMANAS = 4

_ESPERAR_AFILIADOS = time(15, 0)
_ESPERAR_AFILIADOS_DEPOIS = timedelta(minutes=30)
_CORTE = time(17, 30)
_CORTE_DEPOIS = timedelta(hours=3)
_PRAZO_DEPOIS_DO_CORTE = timedelta(minutes=30)

_DIAS = ("segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo")


def no_fuso(quando: datetime) -> datetime:
    """O instante no fuso de Brasília (sem fuso = já é UTC)."""
    if quando.tzinfo is None:
        quando = quando.replace(tzinfo=UTC)
    return quando.astimezone(FUSO)


def tipo_da_agenda(dia: date) -> str | None:
    """Tipo da rodada que a agenda cria neste dia: terça `semanal`, quinta
    `parcial`; nos outros dias, nenhuma."""
    return {1: "semanal", 3: "parcial"}.get(dia.weekday())


def tipo_efetivo(tipo: str, hoje: date) -> str:
    """`parcial` numa segunda não tem dia nenhum para mostrar: vira `semanal`."""
    if tipo not in TIPOS:
        raise ValueError(f"tipo desconhecido: {tipo!r}")
    if tipo == "parcial" and hoje.weekday() == 0:
        return "semanal"
    return tipo


def semanas(tipo: str, hoje: date) -> list[dict[str, str]]:
    """As 4 semanas da rodada, `[{"inicio": "AAAA-MM-DD", "fim": …}]`, 0 = S1
    (a do relatório), 3 = S4 (a mais antiga). Já aplica `tipo_efetivo`."""
    tipo = tipo_efetivo(tipo, hoje)
    if tipo == "semanal":
        fim = hoje - timedelta(days=hoje.weekday() + 1)  # o último domingo antes de hoje
        inicio = fim - timedelta(days=6)
    else:
        inicio = hoje - timedelta(days=hoje.weekday())  # segunda desta semana
        fim = hoje - timedelta(days=1)
    return [
        {
            "inicio": (inicio - timedelta(days=7 * k)).isoformat(),
            "fim": (fim - timedelta(days=7 * k)).isoformat(),
        }
        for k in range(N_SEMANAS)
    ]


def rotulo(inicio: str | date, fim: str | date) -> str:
    """"28/09–04/10" (traço meia-risca, sem ano)."""
    i = inicio if isinstance(inicio, date) else date.fromisoformat(inicio)
    f = fim if isinstance(fim, date) else date.fromisoformat(fim)
    return f"{i:%d/%m}–{f:%d/%m}"


def dia_da_semana(dia: str | date) -> str:
    """"segunda", "terça", … (minúsculo, como no meio de uma frase)."""
    d = dia if isinstance(dia, date) else date.fromisoformat(dia)
    return _DIAS[d.weekday()]


def prazos(criado_em: datetime) -> dict[str, datetime]:
    """`esperar_afiliados_ate`, `corte` e `prazo` (em UTC) de uma rodada
    criada em `criado_em`."""
    t = no_fuso(criado_em)
    dia = t.date()
    esperar = datetime.combine(dia, _ESPERAR_AFILIADOS, tzinfo=FUSO)
    if t >= esperar:
        esperar = t + _ESPERAR_AFILIADOS_DEPOIS
    corte = datetime.combine(dia, _CORTE, tzinfo=FUSO)
    if t >= corte:
        corte = t + _CORTE_DEPOIS
    return {
        "esperar_afiliados_ate": esperar.astimezone(UTC),
        "corte": corte.astimezone(UTC),
        "prazo": (corte + _PRAZO_DEPOIS_DO_CORTE).astimezone(UTC),
    }


def planejar(tipo: str, criado_em: datetime) -> dict[str, Any]:
    """Tudo o que a execução guarda na criação: `tipo` (já efetivo),
    `semanas`, `afiliados_ate` (= S1.fim, date) e os três prazos (UTC)."""
    hoje = no_fuso(criado_em).date()
    tipo = tipo_efetivo(tipo, hoje)
    sem = semanas(tipo, hoje)
    return {
        "tipo": tipo,
        "semanas": sem,
        "afiliados_ate": date.fromisoformat(sem[0]["fim"]),
        **prazos(criado_em),
    }
