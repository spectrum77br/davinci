"""Leitura das chaves `flex_*` do .env (app/config.py), já normalizadas.

As chaves ficam em texto (como `estoque_familia_prefixos`) para um erro de
digitação no .env não derrubar a api na subida: aqui o valor errado vira o
lado SEGURO — modo desconhecido é "desligado", id de conta inválido é
ignorado (e a lista vazia é "nenhuma conta").
"""

from __future__ import annotations

from uuid import UUID

import structlog

from app.config import get_settings
from app.models import UserStatus
from app.models.flex import FLEX_MODOS

logger = structlog.get_logger()

MODO_DESLIGADO = "desligado"
MODO_OBSERVAR = "observar"
# Modos em que o DaVinci pode ESCREVER na plataforma (só nas contas
# permitidas, respeitando o teto por rodada).
MODOS_QUE_ESCREVEM = frozenset({"piloto", "ativo"})


def modo(valor: str | None = None) -> str:
    """`flex_modo` normalizado; desconhecido vale "desligado"."""
    bruto = get_settings().flex_modo if valor is None else valor
    m = (bruto or "").strip().lower()
    if m in FLEX_MODOS:
        return m
    logger.warning("flex_modo_desconhecido", valor=str(bruto)[:40])
    return MODO_DESLIGADO


def contas(valor: str | None = None) -> frozenset[UUID]:
    """`flex_contas`: integration_id permitidos. Vazio = nenhuma conta."""
    bruto = get_settings().flex_contas if valor is None else valor
    out: set[UUID] = set()
    for pedaco in (bruto or "").split(","):
        p = pedaco.strip()
        if not p:
            continue
        try:
            out.add(UUID(p))
        except ValueError:
            logger.warning("flex_conta_invalida", valor=p[:60])
    return frozenset(out)


def _cep8(texto: str, *, fim: bool) -> int | None:
    """CEP em 8 dígitos (inteiro). 5 dígitos vira a faixa inteira: início
    "09600" → 09600000; fim "09899" → 09899999."""
    d = "".join(ch for ch in texto if ch.isdigit())
    if len(d) == 5:
        d += "999" if fim else "000"
    return int(d) if len(d) == 8 else None


def _faixa(pedaco: str) -> tuple[int, int] | None:
    """Uma faixa: "09600-09899" ou "09600000-09899999" (início-fim), um CEP
    só ("09750-000", "09750000") ou um prefixo de 5 dígitos ("09600")."""
    partes = [p.strip() for p in pedaco.split("-")]
    tamanhos = [len(p) for p in partes]
    if not all(p.isdigit() for p in partes):
        return None
    if tamanhos == [5, 3]:  # um CEP com hífen
        n = int(partes[0] + partes[1])
        return n, n
    if len(partes) == 1 and tamanhos[0] in (5, 8):
        a, b = _cep8(partes[0], fim=False), _cep8(partes[0], fim=True)
    elif len(partes) == 2 and tamanhos[0] in (5, 8) and tamanhos[1] in (5, 8):
        a, b = _cep8(partes[0], fim=False), _cep8(partes[1], fim=True)
    else:
        return None
    if a is None or b is None or b < a:
        return None
    return a, b


def faixas_origem(valor: str | None = None) -> tuple[tuple[int, int], ...] | None:
    """`flex_origem_ceps` → faixas (início, fim) em CEP de 8 dígitos.

    None = trava desligada (valor vazio). Tupla vazia = nenhuma faixa válida
    (erro de digitação): NENHUMA conta passa — o lado seguro."""
    bruto = get_settings().flex_origem_ceps if valor is None else valor
    if not (bruto or "").strip():
        return None
    out: list[tuple[int, int]] = []
    for pedaco in bruto.split(","):
        p = pedaco.strip()
        if not p:
            continue
        f = _faixa(p)
        if f is None:
            logger.warning("flex_origem_cep_invalido", valor=p[:40])
            continue
        out.append(f)
    return tuple(out)


def origem_permitida(cep: str | None, faixas: tuple[tuple[int, int], ...] | None) -> bool | None:
    """A saída do Flex (CEP) está numa faixa permitida? None = trava desligada
    (`faixas` None). CEP vazio ou ilegível = False (não se sabe de onde sai)."""
    if faixas is None:
        return None
    digitos = "".join(ch for ch in (cep or "") if ch.isdigit())
    if len(digitos) != 8:
        return False
    n = int(digitos)
    return any(ini <= n <= fim for ini, fim in faixas)


def origem_permitida_todas(ceps: str | None) -> bool | None:
    """Todas as origens da conta (CEPs separados por vírgula) estão nas faixas
    de `flex_origem_ceps`? None = trava desligada. Sem CEP = False."""
    faixas = faixas_origem()
    if faixas is None:
        return None
    lista = [c.strip() for c in (ceps or "").split(",") if c.strip()]
    return bool(lista) and all(origem_permitida(c, faixas) for c in lista)


def pode_escrever(modo_atual: str | None = None) -> bool:
    """O modo deixa escrever na plataforma? (observar/desligado: nunca)."""
    return modo(modo_atual) in MODOS_QUE_ESCREVEM


def usuarios(valor: str | None = None) -> frozenset[str]:
    """`flex_usuarios`: nomes (minúsculos) de quem vê o Flex. Vazio = ninguém."""
    bruto = get_settings().flex_usuarios if valor is None else valor
    return frozenset(p.strip().lower() for p in (bruto or "").split(",") if p.strip())


def pode_ver(user) -> bool:
    """O usuário está na lista de quem vê o Flex? (sem exceção para admin)"""
    if user is None or getattr(user, "status", None) != UserStatus.ACTIVE:
        return False
    return (getattr(user, "name", None) or "").strip().lower() in usuarios()
