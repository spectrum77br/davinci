"""Reavaliação limitada quando chega um dado que faltava à sugestão.

Sem banco, rede ou relógio implícito. Quem chama confere o turno atual,
ausência de avaliação humana e as travas da conversa antes de regenerar.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from datetime import UTC, datetime, timedelta
from typing import Any

from app.services.atendimento.constantes import CATEGORIAS_SO_HUMANO

CHAVE = "reavaliacao_contexto"
MAX_TENTATIVAS = 2
INTERVALO = timedelta(minutes=5)

_FALTA_PEDIDO = (
    "conversa sem pedido identificado",
    "pedido nao encontrado",
    "falta de numero do pedido",
    "sem pedido vinculado",
)
_FALTA_FICHA = ("sem ficha", "nao consta na ficha", "nao constam na ficha", "ficha indisponivel")
_PROTEGIDOS = (
    "assunto so para pessoa",
    "garantia",
    "reembolso",
    "defeito",
    "cancelamento",
    "cliente insistiu",
    "instrucao para",
    "parece instrucao",
    "procon",
    "xingamento",
    "reclamacao",
    "numero que nao veio",
    "promessa",
    "a ia mudou de assunto",
    "pedido de arquivo",
    "nota fiscal nao recebida",
    "procedimento aprovado",
    "politica nao confirmada",
)


def _plano(valor: Any) -> str:
    texto = unicodedata.normalize("NFKD", str(valor or "").casefold())
    return "".join(c for c in texto if not unicodedata.combining(c))


def _utc(valor: Any) -> datetime | None:
    if isinstance(valor, str):
        try:
            valor = datetime.fromisoformat(valor)
        except ValueError:
            return None
    if not isinstance(valor, datetime):
        return None
    return valor.replace(tzinfo=UTC) if valor.tzinfo is None else valor.astimezone(UTC)


def _fatos(rascunho: Any) -> dict:
    fatos = getattr(rascunho, "fatos", None)
    return fatos if isinstance(fatos, dict) else {}


def _metadados(fatos: dict) -> dict | None:
    dados = fatos.get(CHAVE)
    if dados is None:
        return {"n": 0, "assinaturas_vistas": [], "ultimo_em": None}
    if not isinstance(dados, dict):
        return None
    n, vistas = dados.get("n"), dados.get("assinaturas_vistas")
    if isinstance(n, bool) or not isinstance(n, int) or n < 0:
        return None
    if not isinstance(vistas, list) or any(not isinstance(x, str) for x in vistas):
        return None
    if _utc(dados.get("ultimo_em")) is None:
        return None
    return {"n": n, "assinaturas_vistas": vistas, "ultimo_em": dados["ultimo_em"]}


def pode_reavaliar(rascunho: Any, *, agora: datetime) -> bool:
    """Só retenção explícita por falta de dado, sem reabrir recusa de pessoa."""
    if getattr(rascunho, "status", None) not in ("pendente", "bloqueado"):
        return False
    if getattr(rascunho, "categoria", None) in CATEGORIAS_SO_HUMANO:
        return False
    fatos = _fatos(rascunho)
    if fatos.get("pedida_pela_tela"):
        return False
    motivo = _plano(getattr(rascunho, "motivo", ""))
    if any(x in motivo for x in _PROTEGIDOS):
        return False
    if not any(x in motivo for x in (*_FALTA_PEDIDO, *_FALTA_FICHA, "sem dado para")):
        return False
    # Não repetir uma resposta com outro erro de validação além da lacuna.
    erros = getattr(rascunho, "validador_erros", None) or []
    if any("lacuna" not in _plano(erro) and "sem dado" not in _plano(erro) for erro in erros):
        return False
    agora = _utc(agora)
    criado = _utc(getattr(rascunho, "created_at", None))
    meta = _metadados(fatos)
    if agora is None or criado is None or meta is None or meta["n"] >= MAX_TENTATIVAS:
        return False
    ultimo = _utc(meta["ultimo_em"]) or criado
    return agora - max(criado, ultimo) >= INTERVALO


def _material(fatos: dict) -> dict:
    pedido = fatos.get("pedido")
    pedido = pedido if isinstance(pedido, dict) else None
    lacunas = fatos.get("lacunas")
    lacunas = lacunas if isinstance(lacunas, dict) else {}
    fichas = []
    for ficha in fatos.get("ficha_do_anuncio") or []:
        if not isinstance(ficha, dict):
            continue
        atributos = ficha.get("atributos") or []
        if isinstance(atributos, list):
            atributos = sorted(atributos, key=lambda x: json.dumps(x, sort_keys=True, default=str))
        fichas.append(
            {
                "titulo": ficha.get("titulo"),
                "descricao": ficha.get("descricao"),
                "atributos": atributos,
            }
        )
    fichas.sort(key=lambda x: json.dumps(x, sort_keys=True, ensure_ascii=False, default=str))
    return {
        "pedido": pedido,
        "lacunas": {
            k: lacunas.get(k)
            for k in (
                "numero_pedido",
                "rastreio",
                "nf_numero",
                "transportadora",
                "data_envio",
                "previsao_entrega",
            )
        },
        "ficha_do_anuncio": fichas,
    }


def assinatura(fatos: dict) -> str:
    """Somente fatos materiais; não muda por fonte relativa, ordem ou diagnóstico."""
    texto = json.dumps(_material(fatos), ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(texto.encode()).hexdigest()[:24]


def _ganhou_dado(motivo: str, antes: dict, depois: dict) -> bool:
    antigo, novo = _material(antes), _material(depois)
    if any(x in motivo for x in _FALTA_PEDIDO):
        return not antigo["pedido"] and bool(novo["pedido"])
    if any(x in motivo for x in _FALTA_FICHA):
        velhas = antigo["ficha_do_anuncio"]
        novas = novo["ficha_do_anuncio"]
        # Ficha antes vazia ou enriquecida em conteúdo, nunca só renovada.
        return (
            bool(novas)
            and novas != velhas
            and (
                not velhas
                or sum(len(json.dumps(x, sort_keys=True)) for x in novas)
                > sum(len(json.dumps(x, sort_keys=True)) for x in velhas)
            )
        )
    lacunas = re.findall(r"\{([a-z_]+)\}", motivo)
    return bool(lacunas) and all(
        not antigo["lacunas"].get(k) and bool(novo["lacunas"].get(k)) for k in lacunas
    )


def nova_tentativa(rascunho: Any, fatos_atuais: dict, *, agora: datetime) -> dict | None:
    """Metadados do NOVO rascunho, ou None se ainda não há o dado necessário."""
    if not pode_reavaliar(rascunho, agora=agora):
        return None
    if any(fatos_atuais.get(k) for k in ("chamados_abertos", "devolucoes", "reclamacoes_abertas")):
        return None
    antes = _fatos(rascunho)
    meta = _metadados(antes)
    assert meta is not None
    velha, nova = assinatura(antes), assinatura(fatos_atuais)
    if velha == nova or nova in meta["assinaturas_vistas"]:
        return None
    if not _ganhou_dado(_plano(getattr(rascunho, "motivo", "")), antes, fatos_atuais):
        return None
    vistas = list(dict.fromkeys([*meta["assinaturas_vistas"], velha, nova]))
    return {"n": meta["n"] + 1, "assinaturas_vistas": vistas, "ultimo_em": _utc(agora).isoformat()}
