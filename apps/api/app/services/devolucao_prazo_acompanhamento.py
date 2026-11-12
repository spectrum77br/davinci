"""Prazos da aba Acompanhamento (Devoluções) — 12/11/2026, Vinicius.

1. **45 dias em devolução** (todas as plataformas): passou do prazo sem o
   pacote chegar e sem nenhuma exceção → lançar "Não devolvido" (sem
   estoque), Bling em Problemas e abrir chamado (o motivo "Não recebido" do
   lançamento abre o chamado sozinho). Exceções:
     • observação com prazo novo ("aguarde 60 dias", "até 60 dias",
       "até 24/11", "09/11 … mais 15 dias") — o prazo passa a ser esse;
     • extraviado (o rastreio ou a observação diz);
     • motivo "não recebido" (o comprador diz que não recebeu: não há pacote
       voltando).
   Pedido já lançado ou com o pacote de volta confirmado não entra.

2. **Amazon, 14 dias sem postar**: devolução só "solicitada" (o cliente não
   levou aos Correios), 14 dias ou mais, sem reembolso → cancelar a
   devolução no Seller Central (a API da Amazon não tem esse comando).

FASE 1 (esta): a tela só AVISA — "vira Não devolvido em N dias" / "passou do
prazo" / "cancelar no Seller Central". Ele pediu uma semana olhando antes de
o sistema lançar sozinho. Funções puras: a tela e, depois, o job usam a mesma
régua.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

PRAZO_PADRAO_DIAS = 45
# Faltando isso (ou menos) para o prazo, a tela já avisa — dá tempo de pôr
# uma observação segurando o pedido.
AVISO_ANTES_DIAS = 5
AMAZON_DIAS_SEM_POSTAR = 14
AMAZON_PEDIDA = "PENDING"  # logistica_rules.AMAZON_DEVOLUCAO_PEDIDA

VENCIDO = "vencido"
VENCE_LOGO = "vence_logo"
EXCECAO = "excecao"


def _sem_acento(texto: str | None) -> str:
    t = unicodedata.normalize("NFKD", texto or "")
    return "".join(c for c in t if not unicodedata.combining(c)).lower()


def _data(dia: str, mes: str, ano: str | None, perto_de: date) -> date | None:
    """dd/mm[/aa] → data; sem ano, o ano que deixa a data mais perto de
    `perto_de` (12/01 lido em dezembro é do ano que vem)."""
    try:
        d, m = int(dia), int(mes)
        if ano:
            a = int(ano)
            a = a + 2000 if a < 100 else a
            return date(a, m, d)
        candidatas = [date(perto_de.year + k, m, d) for k in (-1, 0, 1)]
    except ValueError:
        return None
    return min(candidatas, key=lambda x: abs((x - perto_de).days))


_DATA_NO_COMECO = re.compile(r"^\s*(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?")
_ATE_DATA = re.compile(r"\bate(?:\s+o)?(?:\s+dia)?\s+(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?")
_N_DIAS = re.compile(r"\b(mais\s+)?(\d{1,3})\s*dias\b")


@dataclass(frozen=True)
class PrazoLido:
    ate: date
    trecho: str


def prazos_da_observacao(observacao: str | None, entrada: date, hoje: date) -> list[PrazoLido]:
    """Os prazos que a observação dá (o maior vale, quando passa dos 45 dias):
      • "até 24/11", "resposta até dia 24/11" → o dia;
      • "até 60 dias", "aguarde 60 dias" → 60 dias desde a entrada em
        devolução (número de 45 ou menos não estende: "a Amazon dá 30 dias");
      • "mais 15 dias" → 15 dias desde a data no começo da observação
        ("09/11 …"); sem essa data não dá para saber de quando — ignora.
    """
    t = _sem_acento(observacao)
    if not t.strip():
        return []
    out: list[PrazoLido] = []
    m0 = _DATA_NO_COMECO.match(t)
    escrita = _data(m0.group(1), m0.group(2), m0.group(3), hoje) if m0 else None
    for m in _ATE_DATA.finditer(t):
        d = _data(m.group(1), m.group(2), m.group(3), escrita or hoje)
        if d is not None:
            out.append(PrazoLido(d, m.group(0).strip()))
    for m in _N_DIAS.finditer(t):
        n = int(m.group(2))
        if m.group(1):
            if escrita is not None and n > 0:
                out.append(PrazoLido(escrita + timedelta(days=n), m.group(0).strip()))
        elif n > PRAZO_PADRAO_DIAS:
            out.append(PrazoLido(entrada + timedelta(days=n), m.group(0).strip()))
    return out


def _excecao(
    *, observacao: str | None, localizacao: str | None, motivo_plataforma: str | None
) -> str | None:
    texto = " ".join(_sem_acento(x) for x in (observacao, localizacao))
    if "extravi" in texto:
        return "extraviado"
    motivo = _sem_acento(motivo_plataforma)
    if "nao recebid" in _sem_acento(observacao) or "nao receb" in motivo:
        return "não recebido"
    return None


def _br(d: date) -> str:
    return d.strftime("%d/%m")


def avaliar(
    *,
    fila: str | None,
    plataforma: str | None,
    entrada: date | None,
    hoje: date,
    observacao: str | None,
    localizacao: str | None,
    lancada: bool,
    chegou_em: date | None,
    reembolso: bool | None,
    meli_status: dict | None,
) -> dict[str, Any]:
    """Os campos de prazo de UMA linha do Acompanhamento (o GET e o PATCH
    usam a mesma conta). Tudo None/False = nada a avisar."""
    out: dict[str, Any] = {
        "prazo_estado": None,
        "prazo_ate": None,
        "prazo_dias_restantes": None,
        "prazo_origem": None,
        "prazo_texto": None,
        "amazon_cancelar": False,
        "amazon_cancelar_texto": None,
    }
    if fila != "acompanhamento" or entrada is None or lancada or chegou_em is not None:
        return out
    ms = meli_status or {}
    padrao = entrada + timedelta(days=PRAZO_PADRAO_DIAS)
    lidos = [p for p in prazos_da_observacao(observacao, entrada, hoje) if p.ate > padrao]
    melhor = max(lidos, key=lambda p: p.ate) if lidos else None
    ate = melhor.ate if melhor else padrao
    restantes = (ate - hoje).days
    excecao = _excecao(
        observacao=observacao,
        localizacao=localizacao,
        motivo_plataforma=ms.get("return_reason"),
    )
    origem = f'observação: "{melhor.trecho}"' if melhor else f"{PRAZO_PADRAO_DIAS} dias"
    if excecao and hoje >= padrao:
        out.update(
            prazo_estado=EXCECAO,
            prazo_ate=ate,
            prazo_origem="observacao" if melhor else "padrao",
            prazo_texto=(
                f"Passou de {PRAZO_PADRAO_DIAS} dias, mas fica fora da regra: {excecao}."
            ),
        )
    elif not excecao and restantes <= 0:
        out.update(
            prazo_estado=VENCIDO,
            prazo_ate=ate,
            prazo_dias_restantes=restantes,
            prazo_origem="observacao" if melhor else "padrao",
            prazo_texto=(
                f"Passou do prazo ({origem}, venceu em {_br(ate)}) sem o pacote chegar: "
                "lançar Não devolvido (sem estoque), Bling em Problemas e abrir chamado."
            ),
        )
    elif not excecao and restantes <= AVISO_ANTES_DIAS:
        out.update(
            prazo_estado=VENCE_LOGO,
            prazo_ate=ate,
            prazo_dias_restantes=restantes,
            prazo_origem="observacao" if melhor else "padrao",
            prazo_texto=(
                f"Vira Não devolvido em {restantes} dia(s) ({origem}, vence {_br(ate)}). "
                'Para segurar, escreva na observação o prazo novo: "aguarde 60 dias" '
                'ou "até 30/11".'
            ),
        )
    elif melhor:
        # Dentro do prazo, mas estendido pela observação: a tela mostra de onde.
        out.update(
            prazo_ate=ate,
            prazo_dias_restantes=restantes,
            prazo_origem="observacao",
            prazo_texto=f"Prazo estendido pela {origem}: vence {_br(ate)}.",
        )

    dias = (hoje - entrada).days
    if (
        "amazon" in (plataforma or "").lower()
        and str(ms.get("return_status") or "").strip().upper() == AMAZON_PEDIDA
        and dias >= AMAZON_DIAS_SEM_POSTAR
        and reembolso is not True
    ):
        out["amazon_cancelar"] = True
        out["amazon_cancelar_texto"] = (
            f"{dias} dias e o cliente não postou a devolução (sem reembolso): cancelar a "
            "devolução no Seller Central › Gerenciar devoluções."
        )
    return out
