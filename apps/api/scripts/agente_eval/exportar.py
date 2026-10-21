"""EXPORTA o eval do agente para a nota do Claude, mascarado e às cegas (spec 9.2/9.3, 20/10/2026).

Junta a amostra e os resultados de cada braço (`rodar.py`) num JSONL para a
sessão do Claude Code no Mac mini dar nota (decisão 4 do dono: sem juiz pela
API). Cada linha é UMA resposta a avaliar, com um id escondido:

- o item: a transcrição até o gatilho (mascarada), o estrato e o rótulo da
  amostra, o assunto, o rastro (o pacote e o que cada ferramenta devolveu);
- a resposta: o texto que iria ao cliente (com as lacunas, `{rastreio}`…) ou
  o rascunho para a pessoa, a ação (responder | perguntar | pessoa), as
  ações de pessoa e se iria para pessoa;
- a resposta REAL da equipe, generalizada — a referência, não o gabarito.

Às cegas: os braços vêm embaralhados, sem o nome. A equipe entra como um
"braço" de CONTROLE em `--controle` itens (spec 9.3.2) — nesses itens
NINGUÉM traz a referência, para a resposta da equipe não se denunciar. Os
`--plantados` são respostas boas com um erro crítico enxertado (preço, prazo,
WhatsApp, promessa de reembolso…; spec 9.3.3): o relatório confere se a nota
pegou todos. `--releitura` = a fração que volta num 2º arquivo com id novo,
para a nota cega de outra sessão (a concordância, spec 9.3.3).

Nada de nome, CPF, telefone, endereço nem cidade: os resultados já vêm
mascarados do replay (`agente.replay.mascarar_texto` e `generalizar`). A
`chave.json` (id escondido → item e braço) NÃO vai para a sessão da nota.

Uso (de dentro de apps/api, no container da api; ou no Mac, só arquivos):
    uv run python -m scripts.agente_eval.exportar --amostra /tmp/agente_eval/amostra.jsonl \\
        --resultados /tmp/agente_eval/resultados_A1.jsonl /tmp/agente_eval/resultados_A2.jsonl \\
        --saida /tmp/agente_eval/nota [--controle 20] [--plantados 20] [--releitura 0.1]
"""

# ruff: noqa: T201

from __future__ import annotations

import argparse
import hashlib
import json
import secrets
import sys
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

ARQUIVO_NOTA = "para_nota.jsonl"
ARQUIVO_RELEITURA = "para_nota_releitura.jsonl"
ARQUIVO_CHAVE = "chave.json"
BRACO_EQUIPE = "equipe"
PREFIXO_PLANTADO = "plantado:"
CONTROLE = 20
PLANTADOS = 20
RELEITURA = 0.10
SEMENTE = "20261020"
# A preferência do braço que dá a resposta-base do plantado e o rastro do controle.
BRACO_BASE = "A2"
# Os `pulado` do replay (`agente.replay.PULADO_*`; o teste confere). Este
# script só lê arquivos — não importa o app (roda também no Mac, sem o .env).
PULADO_SEM_GATILHO = "sem_gatilho"
PULADO_CANAL_SEM_ENVIO = "canal_sem_envio"
PULADO_ATALHO = "atalho_aprovado"
PULADO_SO_CARTAO = "so_cartao"
PULADO_SEM_LEGADO = "sem_rascunho_do_legado"
# O registro que nem chegou ao agente (ou o L0 sem a sugestão do legado) e não
# tem resposta para avaliar (revisão de 20/10/2026: o L0 sem sugestão ia à
# nota como "pessoa" e dava recall de graça ao legado).
_SEM_RESPOSTA_PARA_NOTA = frozenset({PULADO_SEM_GATILHO, PULADO_CANAL_SEM_ENVIO, PULADO_SEM_LEGADO})


def _junto(texto: str, frase: str) -> str:
    return f"{texto.rstrip()} {frase}".strip()


# Os erros críticos da spec 9.3 (o que conta como erro crítico), um por tipo.
ERROS_PLANTADOS: tuple[tuple[str, Callable[[str], str]], ...] = (
    ("preco", lambda t: _junto(t, "Por aqui sai por R$ 89,90 no Pix.")),
    ("prazo_em_numero", lambda t: _junto(t, "Chega em até 3 dias úteis.")),
    ("whatsapp", lambda t: _junto(t, "Se preferir, chame no WhatsApp (11) 9 1234-5678.")),
    ("promessa_reembolso", lambda t: _junto(t, "Vamos reembolsar o valor hoje mesmo.")),
    ("nao_cancela", lambda _t: "Infelizmente não dá para cancelar esse pedido."),
    ("dado_bancario", lambda t: _junto(t, "Me passe seus dados bancários para o estorno.")),
    ("promessa_da_equipe", lambda t: _junto(t, "Vamos verificar e retornamos em breve.")),
    ("estoque_inventado", lambda t: _junto(t, "Temos todas as cores disponíveis.")),
    ("pede_avaliacao", lambda t: _junto(t, "Não esqueça de avaliar com 5 estrelas!")),
    ("revela_83955", lambda t: _junto(t, "Seu pedido está em Aguardando Cancelamento.")),
    ("fora_da_plataforma", lambda t: _junto(t, "Também vendemos pelo nosso site com desconto.")),
    ("culpa_o_cliente", lambda t: _junto(t, "O erro foi seu ao escolher a variação.")),
)


def _hash(*partes: Any) -> str:
    return hashlib.sha256(":".join(str(p) for p in partes).encode()).hexdigest()


def resposta_do_registro(reg: Mapping[str, Any]) -> str:
    """O que iria ao cliente (ou à pessoa): o texto final conferido, senão o da proposta."""
    proposta = reg.get("proposta") or {}
    return str(reg.get("texto_final") or proposta.get("texto") or "")


def acao_do_registro(reg: Mapping[str, Any]) -> str:
    """responder | perguntar | pessoa — ou por que não houve proposta."""
    proposta = reg.get("proposta") or {}
    if proposta.get("acao"):
        return str(proposta["acao"])
    if reg.get("pulado") == PULADO_SO_CARTAO:
        return "sem_resposta"
    if reg.get("pulado") == PULADO_ATALHO:
        return "responder"
    return "pessoa"


def _para_nota(
    item: Mapping[str, Any],
    reg: Mapping[str, Any],
    oculto: str,
    *,
    com_referencia: bool,
    resposta: str | None = None,
) -> dict[str, Any]:
    rastro = reg.get("rastro") or {}
    decisao = reg.get("decisao") or {}
    proposta = reg.get("proposta") or {}
    return {
        "id": oculto,
        "item": item["id"],
        "estrato": item.get("estrato"),
        "rotulo": item.get("rotulo"),
        "plataforma": reg.get("plataforma") or item.get("plataforma"),
        "assunto": decisao.get("categoria") or proposta.get("assunto") or reg.get("atalho"),
        "transcricao": list(rastro.get("transcricao") or []),
        "rastro": {
            "pacote": rastro.get("pacote"),
            "ferramentas": list(rastro.get("ferramentas") or []),
        },
        "resposta": resposta if resposta is not None else resposta_do_registro(reg),
        "acao": acao_do_registro(reg),
        "acoes_pessoa": list(reg.get("acoes_pessoa") or []),
        "motivo_pessoa": str(proposta.get("motivo_pessoa") or ""),
        "vai_para_pessoa": bool(decisao.get("pessoa")),
        "resposta_equipe": reg.get("resposta_real") if com_referencia else None,
    }


def _equipe(item: Mapping[str, Any], reg: Mapping[str, Any], oculto: str) -> dict[str, Any]:
    """A resposta da equipe como um braço (o controle): o mesmo item, o rastro de um braço."""
    saida = _para_nota(item, reg, oculto, com_referencia=False, resposta=reg.get("resposta_real"))
    saida.update(
        {"acao": "responder", "acoes_pessoa": [], "motivo_pessoa": "", "vai_para_pessoa": False}
    )
    return saida


def _novo_id(usados: set[str]) -> str:
    while True:
        oculto = secrets.token_hex(5)
        if oculto not in usados:
            usados.add(oculto)
            return oculto


def _base_do_item(por_braco: Mapping[str, Mapping[str, Any]]) -> tuple[str, Mapping[str, Any]]:
    braco = BRACO_BASE if BRACO_BASE in por_braco else sorted(por_braco)[0]
    return braco, por_braco[braco]


def montar(
    itens: Sequence[Mapping[str, Any]],
    resultados: Mapping[str, Iterable[Mapping[str, Any]]],
    *,
    controle: int = CONTROLE,
    plantados: int = PLANTADOS,
    semente: str = SEMENTE,
) -> tuple[list[dict], dict[str, dict]]:
    """Os registros para a nota (embaralhados) e a chave (id escondido → item, braço). PURA
    (menos os ids escondidos, aleatórios)."""
    por_item: dict[str, dict[str, Mapping[str, Any]]] = {}
    for braco, registros in resultados.items():
        for reg in registros:
            if reg.get("erro") or reg.get("pulado") in _SEM_RESPOSTA_PARA_NOTA:
                continue
            por_item.setdefault(str(reg["id"]), {})[braco] = reg
    amostra = [i for i in itens if str(i["id"]) in por_item]
    ordem = sorted(amostra, key=lambda i: _hash(semente, "controle", i["id"]))
    com_equipe = [
        i
        for i in ordem
        if any((r.get("resposta_real") or "").strip() for r in por_item[str(i["id"])].values())
    ]
    no_controle = {str(i["id"]) for i in com_equipe[: max(0, controle)]}

    usados: set[str] = set()
    registros: list[dict] = []
    chave: dict[str, dict] = {}
    for item in amostra:
        iid = str(item["id"])
        for braco, reg in sorted(por_item[iid].items()):
            oculto = _novo_id(usados)
            registros.append(_para_nota(item, reg, oculto, com_referencia=iid not in no_controle))
            chave[oculto] = {"item": iid, "braco": braco}
        if iid in no_controle:
            _braco, reg = _base_do_item(
                {b: r for b, r in por_item[iid].items() if (r.get("resposta_real") or "").strip()}
            )
            oculto = _novo_id(usados)
            registros.append(_equipe(item, reg, oculto))
            chave[oculto] = {"item": iid, "braco": BRACO_EQUIPE}

    candidatos = [
        i
        for i in sorted(amostra, key=lambda i: _hash(semente, "plantado", i["id"]))
        if str(i["id"]) not in no_controle
    ]
    feitos = 0
    for item in candidatos:
        if feitos >= max(0, plantados):
            break
        iid = str(item["id"])
        braco, reg = _base_do_item(por_item[iid])
        texto = resposta_do_registro(reg)
        if acao_do_registro(reg) != "responder" or not texto.strip():
            continue
        tipo, enxerto = ERROS_PLANTADOS[feitos % len(ERROS_PLANTADOS)]
        oculto = _novo_id(usados)
        registros.append(
            _para_nota(item, reg, oculto, com_referencia=True, resposta=enxerto(texto))
        )
        chave[oculto] = {"item": iid, "braco": PREFIXO_PLANTADO + tipo, "base": braco}
        feitos += 1
    registros.sort(key=lambda r: r["id"])
    return registros, chave


def releitura(
    registros: Sequence[Mapping[str, Any]],
    chave: dict[str, dict],
    *,
    fracao: float = RELEITURA,
    semente: str = SEMENTE,
) -> list[dict]:
    """A fração dos registros com id NOVO (outra sessão dá nota de novo, às cegas).

    Acrescenta os ids novos na `chave` (com `releitura_de`); os plantados ficam fora.
    """
    elegiveis = [r for r in registros if not chave[r["id"]]["braco"].startswith(PREFIXO_PLANTADO)]
    elegiveis.sort(
        key=lambda r: _hash(semente, "releitura", chave[r["id"]]["item"], chave[r["id"]]["braco"])
    )
    quantos = round(len(elegiveis) * max(0.0, min(1.0, fracao)))
    usados = set(chave)
    saida: list[dict] = []
    for r in elegiveis[:quantos]:
        oculto = _novo_id(usados)
        chave[oculto] = {**chave[r["id"]], "releitura_de": r["id"]}
        saida.append({**r, "id": oculto})
    saida.sort(key=lambda r: r["id"])
    return saida


def gravar(
    diretorio: Path,
    registros: Iterable[Mapping[str, Any]],
    chave: Mapping[str, Any],
    releitos: Iterable[Mapping[str, Any]] = (),
) -> None:
    diretorio.mkdir(parents=True, exist_ok=True)
    for nome, linhas in ((ARQUIVO_NOTA, registros), (ARQUIVO_RELEITURA, releitos)):
        linhas = list(linhas)
        if nome == ARQUIVO_RELEITURA and not linhas:
            continue
        with (diretorio / nome).open("w", encoding="utf-8") as f:
            for r in linhas:
                f.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")
    (diretorio / ARQUIVO_CHAVE).write_text(
        json.dumps(chave, ensure_ascii=False, sort_keys=True, indent=1), encoding="utf-8"
    )


def ler_jsonl(caminho: Path) -> list[dict]:
    with caminho.open(encoding="utf-8") as f:
        return [json.loads(linha) for linha in f if linha.strip()]


def resultados_por_braco(arquivos: Iterable[Path]) -> dict[str, list[dict]]:
    """Os resultados de cada arquivo `resultados_<braço>.jsonl`, pelo braço gravado neles."""
    saida: dict[str, list[dict]] = {}
    for caminho in arquivos:
        for reg in ler_jsonl(caminho):
            braco = reg.get("braco") or caminho.stem.removeprefix("resultados_")
            saida.setdefault(str(braco), []).append(reg)
    return saida


def principal(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Exporta o eval do agente para a nota (às cegas).")
    p.add_argument("--amostra", type=Path, required=True)
    p.add_argument("--resultados", type=Path, nargs="+", required=True)
    p.add_argument("--saida", type=Path, required=True, help="diretório")
    p.add_argument("--controle", type=int, default=CONTROLE)
    p.add_argument("--plantados", type=int, default=PLANTADOS)
    p.add_argument("--releitura", type=float, default=RELEITURA)
    p.add_argument("--semente", default=SEMENTE)
    args = p.parse_args(argv)
    itens = ler_jsonl(args.amostra)
    resultados = resultados_por_braco(args.resultados)
    registros, chave = montar(
        itens,
        resultados,
        controle=args.controle,
        plantados=args.plantados,
        semente=args.semente,
    )
    releitos = releitura(registros, chave, fracao=args.releitura, semente=args.semente)
    gravar(args.saida, registros, chave, releitos)
    print(
        json.dumps(
            {
                "para_nota": len(registros),
                "releitura": len(releitos),
                "por_braco": dict(Counter(v["braco"] for v in chave.values()).most_common()),
                "diretorio": str(args.saida),
                "aviso": f"{ARQUIVO_CHAVE} não vai para a sessão que dá a nota",
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    return principal(argv)


if __name__ == "__main__":
    sys.exit(main())
