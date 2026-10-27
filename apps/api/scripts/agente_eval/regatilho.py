"""RE-GATILHO do eval do agente: a decisão do automático com o código ATUAL (27/10/2026).

O portão do dono (27/10/2026): o rascunho do agente sai sozinho com acao "responder" e texto,
nenhum bloqueio DURO da 1ª camada e o "ok" do verificador; o resto da 1ª camada vira AVISO
para o verificador. Este script relê os resultados de um eval (`scripts.agente_eval.rodar`,
JSONL: id, decisao, texto_final, proposta, rastro {pacote, transcricao, ferramentas,
checagens, evidencias, passos}, resposta_real, plataforma, integration_id) e, SEM chamar o
agente, refaz a 1ª camada sobre a MESMA Proposta (`agente.conferir_proposta`, o código do
`agente.responder`) com o pacote, a conversa e as ferramentas reconstruídos do rastro:

  python -m scripts.agente_eval.regatilho --resultados /tmp/agente_eval/resultados_S1.jsonl \\
      [--saida /tmp/agente_eval/regatilho.jsonl] [--config-canal <uuid> | --config-canal-json f]
      [--verificar] [--teto-usd 1.0] [--limite N] [--resumo-so]

- sem `--verificar` (o padrão; determinístico, sem chave, roda no Mac): a decisão é
  `chega_ao_verificador` (passou em todo bloqueio duro e na régua do canal),
  `pessoa:<o que segura>` ou `descartado` (nada a responder);
- `--verificar` (precisa da chave Claude no .env — roda no servidor, de dentro do container
  da api, fora das janelas de postagem e com o OK do Eduardo): os que chegam ao verificador
  passam pelo verificador de verdade (Haiku 5.5, `verificador.MODELO`/`EFFORT`): `auto` ou
  `pessoa:verificador`; o custo conta no teto do DIA do agente (as chaves de replay da
  conversa) e para no `--teto-usd`;
- `--config-canal <uuid>`: o `modo`, `auto_categorias`, `auto_orientacao` e `auto_agente`
  DESSE canal valem para todo registro (o padrão do dono: o da Kia,
  1ca982c3-96e0-4000-95f2-e1fe368f954d); sem ele, o canal de cada registro (lido do banco)
  — a loja que não está no automático tem as listas vazias. `--config-canal-json` lê o mesmo
  de um arquivo ({"modo", "auto_categorias", "auto_orientacao", "auto_agente"}) — no Mac.

Saída: uma linha por registro (id, loja, categoria, decisao, bloqueios duros, avisos, o
rascunho e a resposta_real lado a lado, a decisão de antes do eval) e o resumo (a taxa de
auto/chega ao verificador no total, por loja e por categoria, e o custo). Só lê: nada vai
ao banco, nada é enviado, os textos já vêm mascarados do eval.

Reconstrução (o que o eval gravou; o resto, o mais perto possível): as falas pela
`transcricao` (a última do cliente é o gatilho; as automáticas como `davinci_auto`); o pedido,
as fichas, o estado da conversa (as marcas D1 e da devolução de rotina) e as respostas da
equipe pelas seções do `pacote`; as ações de pessoa do CÓDIGO pela decisão gravada (a régua
delas lê o banco e não mudou); o que as ferramentas devolveram pelo `rastro.ferramentas`
(os ids, os fatos do manual, as fichas do `ver_anuncio`, a busca no catálogo, a garantia).
Na árvore sem `agente.conferir_proposta` (antes do portão), o mesmo caminho pelas funções
de lá (`_conferir_antigo`) — o "antes" da comparação.
"""

# ruff: noqa: T201

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

from app.services.atendimento import constantes, ia
from app.services.atendimento.agente import (
    acao_pessoa,
    checagens,
    ferramentas,
    prompt,
    saida,
    verificador,
)
from app.services.atendimento.agente import contexto as agente_ctx

KIA = "1ca982c3-96e0-4000-95f2-e1fe368f954d"
MANUAL_BASE = Path(__file__).resolve().parents[1] / "atendimento_manual_base.json"
TETO_USD = 1.0
CHEGA = "chega_ao_verificador"
AUTO = "auto"
DESCARTADO = "descartado"
# Os nomes das lojas que o relatório usa (integration_id → nome curto); o resto, o prefixo.
LOJAS = {
    "2ac2633b-c698-4718-99cb-6e5443a80689": "KIA",
    "3d5dc93a-3344-4d63-9465-03afdf1ac1b5": "BARBOSA",
    "e866973b-b510-4a32-84db-8b87bf86a777": "VITA",
}
_SECAO = re.compile(
    r"^(CABEÇALHO|CARTÕES|PEDIDO DA CONVERSA|ESTADO DA CONVERSA|PRODUTO(?: \([^)]*\))?"
    r"|EVIDÊNCIAS DESTES DADOS(?: \([^)]*\))?|COMPRAS DO COMPRADOR NESTA LOJA"
    r"|PERGUNTAS AINDA SEM RESPOSTA NESTE TURNO[^:]*|COMO A EQUIPE JÁ RESPONDEU[^:]*"
    r"|CONVERSA(?: \([^)]*\))?):\s?(.*)$"
)
_FALA = re.compile(r"^\[(\d\d)/(\d\d) (\d\d):(\d\d)\] ([^:\[]+?)(?: \[([^\]]*)\])?: ?(.*)$", re.S)
_PAR = re.compile(r"^- (par:\d+) \(([^)]*)\): cliente «(.*)» → equipe «(.*)»$")
_DADOS = re.compile(r'<dados fonte="([^"]+)">(.*)</dados>', re.S)
_OPCAO_DO_MENU = re.compile(r"\s*\(opção do menu: [^)]*\)\s*$")
_ACESSORIO_NO_NENHUM = re.compile(r"'([^']+)'")
# Valores de mentira para as lacunas que o pedido tem (o texto sai com o dado, como ao vivo).
_VALORES = {
    "numero_pedido": "2610000000000X",
    "rastreio": "BR000000000BR",
    "data_envio": "01/10/2026",
    "nf_numero": "1234",
    "transportadora": "Transportadora",
    "previsao_entrega": "05/11/2026",
}


def loja_de(r: Mapping[str, Any]) -> str:
    integ = str(r.get("integration_id") or "")
    return LOJAS.get(integ) or (integ[:8] or "?")


async def _nomes_das_lojas(integracoes: set[str]) -> None:
    """Os nomes das lojas (`integrations.name`) no `LOJAS`, quando há banco. Só lê."""
    from sqlalchemy import select

    from app.models import Integration

    async with _leitura() as s:
        linhas = (await s.execute(select(Integration.id, Integration.name))).all()
    for i, nome in linhas:
        if str(i) in integracoes and nome:
            LOJAS.setdefault(str(i), " ".join(str(nome).split()).upper())


def _leitura() -> Any:
    """A sessão SÓ DE LEITURA do replay (`replay.sessao_replay`: transação read-only)."""
    from app.services.atendimento.agente import replay

    return replay.sessao_replay()


# ── O pacote em seções ────────────────────────────────────────────────────


def secoes(pacote: str | None) -> dict[str, Any]:
    """As seções do pacote (JSON lido; as de lista, as linhas). PURA."""
    saida_: dict[str, Any] = {"pares": [], "perguntas": []}
    atual = None
    for linha in (pacote or "").split("\n"):
        m = _SECAO.match(linha)
        if m:
            nome = m.group(1).split(" (")[0]
            atual = nome
            resto = m.group(2)
            if nome in ("CABEÇALHO", "CARTÕES", "PEDIDO DA CONVERSA", "ESTADO DA CONVERSA",
                        "PRODUTO", "EVIDÊNCIAS DESTES DADOS"):  # fmt: skip
                try:
                    saida_[nome] = json.loads(resto)
                except (ValueError, TypeError):
                    saida_[nome] = None
            continue
        if atual and atual.startswith("COMO A EQUIPE") and (p := _PAR.match(linha)):
            saida_["pares"].append(_par_da_linha(p))
        elif atual and atual.startswith("PERGUNTAS") and linha.startswith("- "):
            saida_["perguntas"].append(linha[2:])
    return saida_


def _par_da_linha(m: re.Match[str]) -> dict:
    rotulos = m.group(2)
    titulo = re.search(r"mesmo anúncio «([^»]*)»", rotulos)
    dias = re.search(r"há (\d+) dia", rotulos)
    vale = re.search(r"(?<!não )vale para produto(?! só)", rotulos) is not None
    return {
        "id": m.group(1),
        "pergunta": m.group(3),
        "resposta": m.group(4),
        "mesma_loja": "esta loja" in rotulos,
        "mesmo_anuncio": "mesmo anúncio" in rotulos,
        "mesmo_produto": "mesmo anúncio" in rotulos or "mesmo produto" in rotulos,
        "fonte_de_produto": vale,
        "com_anuncio": "sem anúncio" not in rotulos,
        "idade_dias": int(dias.group(1)) if dias else (0 if "hoje" in rotulos else 1),
        **({"titulo_do_anuncio": titulo.group(1)} if titulo else {}),
    }


def _agora(cab: Mapping[str, Any] | None) -> datetime:
    texto = str((cab or {}).get("agora_sao_paulo") or "")
    try:
        local = datetime.strptime(texto, "%d/%m/%Y %H:%M")
    except ValueError:
        return datetime.now(UTC)
    return local.replace(tzinfo=UTC) + timedelta(hours=3)


def mensagens(transcricao: Sequence[str], agora: datetime) -> list[SimpleNamespace]:
    """As falas da transcrição como mensagens (a ordem e a hora de São Paulo). PURA."""
    saida_: list[SimpleNamespace] = []
    for i, linha in enumerate(transcricao or []):
        m = _FALA.match(str(linha))
        if not m:
            continue
        dia, mes, hh, mm, quem, marca, texto = m.groups()
        try:
            quando = datetime(agora.year, int(mes), int(dia), int(hh), int(mm), tzinfo=UTC)
        except ValueError:
            continue
        quando += timedelta(hours=3, seconds=i)
        if quando > agora + timedelta(days=2):
            quando = quando.replace(year=agora.year - 1)
        quem = quem.strip().lower()
        autor = {"cliente": constantes.AUTOR_CLIENTE, "loja": "loja"}.get(quem, "sistema")
        auto = bool(marca and "autom" in marca)
        origem = (
            constantes.ORIGEM_AUTO if auto else ("cliente" if autor == "cliente" else "externo")
        )
        if autor == constantes.AUTOR_CLIENTE:
            texto = _OPCAO_DO_MENU.sub("", texto)
        saida_.append(
            SimpleNamespace(
                id=uuid4(),
                autor=autor,
                texto=texto,
                tipo="texto",
                payload={},
                origem=origem,
                status="recebida" if autor == constantes.AUTOR_CLIENTE else "enviada",
                enviada_em=quando,
                created_at=quando,
                anexos=[],
                autor_user_id=None,
                erro=None,
                rascunho_id=None,
            )
        )
    return saida_


def _ctx_do_pedido(bloco: Mapping[str, Any] | None) -> dict:
    """O contexto do pedido (o formato de `contexto_svc.contexto_da_conversa`) pelo bloco."""
    if not isinstance(bloco, Mapping):
        return {"pedido": None, "logistica": None, "chamados": [], "devolucoes": []}
    reclamacoes = [
        {
            "id": f"r{i}",
            "plataforma": "Shopee",
            "tipo": "devolucao",
            "tipo_rotulo": r.get("tipo"),
            "status": r.get("estado"),
            "aberta": True,
            "acao_pendente": r.get("acao_pendente"),
            "encerrada_em": None,
        }
        for i, r in enumerate(bloco.get("reclamacoes") or [])
        if isinstance(r, Mapping)
    ]
    return {
        "pedido": {
            "numero": "90000",
            "numeroloja": _VALORES["numero_pedido"],
            "situacao": bloco.get("situacao"),
            "itens": bloco.get("itens"),
            "data_da_compra": bloco.get("data_da_compra"),
        },
        "logistica": {
            "status": bloco.get("ultimo_status"),
            "data_envio": "2026-10-01" if bloco.get("data_envio") else None,
        },
        "chamados": list(bloco.get("chamados") or []),
        "devolucoes": [
            {"status": d.get("estado")}
            for d in bloco.get("devolucoes") or []
            if isinstance(d, Mapping)
        ],
        "reclamacoes": reclamacoes,
    }


def _valores(bloco: Mapping[str, Any] | None) -> dict[str, str | None]:
    bloco = bloco if isinstance(bloco, Mapping) else {}
    campos = {"numero_pedido": "numero", "rastreio": "rastreio", "data_envio": "data_envio",
              "nf_numero": "nf", "transportadora": "transportadora",
              "previsao_entrega": "previsao_entrega"}  # fmt: skip
    return {
        lacuna: (_VALORES[lacuna] if str(bloco.get(campo) or "").strip() else None)
        for lacuna, campo in campos.items()
    }


def _categorias() -> list[dict]:
    dados = json.loads(MANUAL_BASE.read_text())
    return [
        {
            "id": c["id"],
            "nome": c["nome"],
            "descricao": c.get("descricao") or "",
            "exemplos": c.get("exemplos") or [],
            "so_humano": bool(c.get("so_humano")),
            "lacunas": c.get("lacunas") or [],
            "ordem": (i + 1) * 10,
        }
        for i, c in enumerate(dados["categorias"])
    ]


# ── As ferramentas de volta ao contexto ───────────────────────────────────


def _dados_da_ferramenta(resultado: str | None) -> tuple[str | None, Any]:
    m = _DADOS.search(resultado or "")
    if not m:
        return None, None
    try:
        return m.group(1), json.loads(m.group(2))
    except ValueError:
        return m.group(1), None


def _uranyx_antiga(regra: Any) -> bool:
    return isinstance(regra, Mapping) and (
        "loja_dias" in regra or "caminho_depois_da_loja" in regra or "uranyx_meses" in regra
    )


def garantia_de_hoje(dados: Mapping[str, Any]) -> dict:
    """O `ver_garantia` gravado com a regra Uranyx de ANTES (o "1 ano: 90 dias + 9 meses" e o
    caminho da Uranyx) como o código de hoje o devolveria (o dono, 27/10/2026): a regra nova,
    sem as datas de fim nem o período (`ferramentas.sem_prazo_uranyx`). PURA."""

    def _hoje(g: Mapping[str, Any]) -> dict:
        regra = g.get("regra")
        if not _uranyx_antiga(regra):
            return dict(g)
        nova = dict(ferramentas.REGRA_URANYX)
        if isinstance(regra, Mapping) and regra.get("fatos"):
            nova["fatos"] = list(regra["fatos"])
        return ferramentas.sem_prazo_uranyx({**g, "regra": nova})

    saida = _hoje(dados)
    if isinstance(dados.get("garantias"), list):
        saida["garantias"] = [_hoje(g) if isinstance(g, Mapping) else g for g in dados["garantias"]]
    return saida


def reaplicar_ferramentas(ctx: Any, rastro: Mapping[str, Any]) -> None:
    """O que cada ferramenta devolveu volta ao `ctx` como no `ferramentas.executar`.

    v16: o `ver_garantia` gravado com a regra Uranyx de antes volta como o de hoje
    (`garantia_de_hoje`)."""
    passos = list(rastro.get("passos") or [])
    for i, f in enumerate(rastro.get("ferramentas") or []):
        nome = f.get("ferramenta")
        _fonte, dados = _dados_da_ferramenta(f.get("resultado"))
        ids = [str(x) for x in (passos[i].get("ids") if i < len(passos) else []) or []]
        ctx.registrar_passo(nome, dict(f.get("args") or {}), 0, ids=ids)
        if not isinstance(dados, Mapping) or dados.get("ja_consultado"):
            continue
        if nome == ferramentas.VER_GARANTIA:
            dados = garantia_de_hoje(dados)
        provados = [x for x in ids if not x.startswith(agente_ctx.PREFIXOS_SEM_PROVA)]
        provados += [str(x) for x in dados.get("evidencias") or [] if isinstance(x, str)]
        corpo = json.dumps(dados, ensure_ascii=False, sort_keys=True)
        if nome == ferramentas.BUSCAR_ANUNCIOS_DA_LOJA:
            ctx.permitir(ids=provados)
            ctx.permitir_numeros(corpo)
            from app.services.atendimento.agente import catalogo

            lido = catalogo.ler_termo(dados.get("termo"))
            ctx.registrar_busca(
                {
                    "termo": dados.get("termo"),
                    "acessorios": sorted(lido.acessorios),
                    "modelo": lido.modelo,
                    "anuncios": [
                        {
                            **a,
                            "titulo_plano": " ".join(
                                str(a.get("nome_curto") or a.get("titulo") or "").lower().split()
                            ),
                        }
                        for a in dados.get("anuncios") or []
                        if isinstance(a, Mapping)
                    ],
                    "sem_no_titulo": _ACESSORIO_NO_NENHUM.findall(str(dados.get("nenhum") or ""))
                    if "título" in str(dados.get("nenhum") or "")
                    else [],
                }
            )
            continue
        if nome == ferramentas.BUSCAR_RESPOSTAS_EQUIPE:
            # Os pares citáveis da ferramenta (o portão, 27/10/2026); os de tom, nada.
            citaveis = [
                {**e, "id": e.get("par")}
                for e in dados.get("exemplos") or []
                if isinstance(e, Mapping) and e.get("par")
            ]
            if citaveis:
                ctx.registrar_pares(citaveis)
            continue
        ctx.permitir(ids=provados, textos=[corpo], produto=nome == ferramentas.VER_ANUNCIO)
        if nome == ferramentas.VER_ANUNCIO and dados.get("item_id"):
            ctx.fichas_lidas.append(
                {
                    "item_id": dados.get("item_id"),
                    "titulo": dados.get("titulo"),
                    "variacoes": [
                        dict(v) for v in dados.get("variacoes") or [] if isinstance(v, Mapping)
                    ],
                    "variacoes_completas": dados.get("variacoes_completas") is True,
                    "condicao": dados.get("condicao"),
                    "da_conversa": str(dados.get("item_id")) in ctx.anuncios_do_pacote,
                }
            )
        if nome == ferramentas.VER_GARANTIA:
            ctx.registrar_garantias(
                [
                    ferramentas._para_validador(g)
                    for g in (dados.get("garantias") or [dados])
                    if isinstance(g, Mapping)
                ]
            )
        if dados.get("fatos"):
            ctx.registrar_fatos([x for x in dados["fatos"] if isinstance(x, Mapping)])


# ── Um registro ───────────────────────────────────────────────────────────


def montar(r: Mapping[str, Any]) -> SimpleNamespace:
    """Conversa, gatilho, base, contexto e pacote reconstruídos do registro. PURA (sem banco)."""
    rastro = r.get("rastro") or {}
    sec = secoes(rastro.get("pacote"))
    agora = _agora(sec.get("CABEÇALHO"))
    msgs = mensagens(rastro.get("transcricao") or [], agora)
    gatilho = next((m for m in reversed(msgs) if m.autor == constantes.AUTOR_CLIENTE), None)
    if gatilho is None:
        return SimpleNamespace(erro="sem fala do cliente na transcrição")
    estado = (
        sec.get("ESTADO DA CONVERSA") if isinstance(sec.get("ESTADO DA CONVERSA"), dict) else {}
    )
    bloco = (
        sec.get("PEDIDO DA CONVERSA") if isinstance(sec.get("PEDIDO DA CONVERSA"), dict) else None
    )
    fichas = [f for f in sec.get("PRODUTO") or [] if isinstance(f, dict)]
    canal_txt = str((sec.get("CABEÇALHO") or {}).get("canal") or "")
    conversa = SimpleNamespace(
        id=UUID(str(r.get("conversa_id"))) if r.get("conversa_id") else uuid4(),
        plataforma=r.get("plataforma") or "shopee",
        canal=constantes.CANAL_PERGUNTA if "pergunta" in canal_txt.lower() else "chat",
        dados={},
        pedido_marketplace=_VALORES["numero_pedido"] if bloco else None,
        etiqueta=constantes.ETIQUETA_DEVOLUCAO if estado.get("devolucao_em_andamento") else None,
        etiquetas_secundarias=[],
        canal_id=None,
        integration_id=UUID(str(r["integration_id"])) if r.get("integration_id") else None,
        comprador_id=None,
        comprador_nome=None,
        anuncio_id=None,
    )
    ctxd = _ctx_do_pedido(bloco)
    rajada = ia.rajada_do_cliente(msgs, gatilho)
    foco = ia.perguntas_pendentes(msgs, gatilho)
    textos_cliente = [m.texto for m in rajada if m.texto]
    textos_rajada = [m.texto or "" for m in (foco or rajada)]
    sinais = acao_pessoa.sinais_do_agente(
        ia.sinais_com_codigo(textos_cliente, agente=True), msgs, gatilho
    )
    motivos = list(dict.fromkeys(f for _c, f in sinais))
    if ia.turno_incompleto(msgs, gatilho):
        motivos.append(ia.MOTIVO_TURNO_INCOMPLETO)
    valores = _valores(bloco)
    base = agente_ctx.BaseDoGerar(
        recentes=msgs,
        rajada=rajada,
        foco=foco,
        textos_cliente=textos_cliente,
        textos_rajada=textos_rajada,
        ctx=ctxd,
        valores=valores,
        fatos={"anuncio": None, "ficha_do_anuncio": fichas},
        fatos_gravados={
            "devolucoes": len(ctxd.get("devolucoes") or []),
            "chamados_abertos": len(ctxd.get("chamados") or []),
        },
        categorias=_categorias(),
        regras=[],
        sinais=sinais,
        motivos=motivos,
        opcoes_sensiveis=ia.assuntos_sensiveis_do_menu(msgs, gatilho),
        sinais_do_cliente=[],
        nomes=(),
        modo=constantes.MODO_AUTO,
        forcar=False,
        nf_politica_aprovada=False,
        insistiu=ia.insistiu_em_pessoa(msgs),
    )
    ctx = agente_ctx.ContextoAgente.da_conversa(
        conversa, gatilho, modo=constantes.MODO_AUTO, extra={"sem_ida_a_loja": True}
    )
    itens = [str(f.get("item_id")) for f in fichas if f.get("item_id")]
    for c in sec.get("CARTÕES") or []:
        for it in (c or {}).get("itens") or [] if isinstance(c, Mapping) else []:
            if isinstance(it, Mapping) and it.get("item_id"):
                itens.append(str(it["item_id"]))
    ctx.aceitar_anuncio(*itens)
    ctx.anuncios_do_pacote = frozenset(ctx.anuncios)
    ids = [str(x) for x in sec.get("EVIDÊNCIAS DESTES DADOS") or [] if isinstance(x, str)]
    ctx.permitir(ids=ids, textos=[json.dumps(bloco, ensure_ascii=False) if bloco else None])
    ctx.permitir(
        textos=[json.dumps(fichas, ensure_ascii=False) if fichas else None,
                json.dumps(sec.get("CARTÕES"), ensure_ascii=False) if sec.get("CARTÕES") else None],
        produto=True,
    )  # fmt: skip
    if sec["pares"]:
        ctx.registrar_pares(sec["pares"])
    decisao = r.get("decisao") or {}
    ctx.acoes_codigo = agente_ctx.ordenar_acoes([str(a) for a in decisao.get("acoes_codigo") or []])
    if r.get("recalcular_acoes"):
        # A linha da calibração não tem as ações gravadas: a régua do código, sem o banco.
        lista = acao_pessoa.gatilhos(
            conversa,
            msgs,
            ctxd,
            gatilho=gatilho,
            postado=(bloco or {}).get("postado"),
            chaves_ativas=frozenset(),
            agora=agora,
        )
        ctx.acoes_codigo = agente_ctx.ordenar_acoes([g.codigo for g in lista])
    ctx.devolucao_de_rotina = bool(estado.get("devolucao_sem_decisao_da_loja"))
    # O D1 gravado no eval ou o do código ATUAL (o portão relaxou o turno informativo).
    ctx.responde_e_marca = bool(estado.get("responde_e_marca")) or bool(
        acao_pessoa.responde_e_marca_do_turno(
            conversa, ctxd, msgs, gatilho, [], acoes=ctx.acoes_codigo, agora=agora
        )
    )
    ctx.pistas_orientacao = acao_pessoa.pistas_de_orientacao(msgs, gatilho)
    ctx.pistas_turno = acao_pessoa.pistas_do_turno(msgs, gatilho, com_pedido=bool(bloco))
    reaplicar_ferramentas(ctx, rastro)
    pac = SimpleNamespace(
        texto=rastro.get("pacote") or "",
        fatos={
            "anuncio": None,
            "ficha_do_anuncio": fichas,
            **({"agente_pedido": bloco} if bloco else {}),
        },
        fichas=fichas,
        pedido=bloco,
        postado=(bloco or {}).get("postado"),
        pediu_evidencia=acao_pessoa.pediu_evidencia(msgs, gatilho),
    )
    return SimpleNamespace(
        erro=None, conversa=conversa, gatilho=gatilho, base=base, ctx=ctx, pac=pac, sec=sec
    )


def _canal_ns(canal: Mapping[str, Any] | None) -> SimpleNamespace | None:
    if canal is None:
        return None
    return SimpleNamespace(
        modo=canal.get("modo"),
        auto_categorias=list(canal.get("auto_categorias") or []),
        auto_orientacao=list(canal.get("auto_orientacao") or []),
        auto_agente=list(canal.get("auto_agente") or []),
    )


async def _conferir(m: SimpleNamespace, p: saida.Proposta) -> Any:
    """A 1ª camada: o `agente.conferir_proposta` (o código do `responder`) ou, na árvore de
    antes do portão, o mesmo caminho pelas funções de lá."""
    from app.services.atendimento import agente

    fn = getattr(agente, "conferir_proposta", None)
    if fn is not None:
        return await fn(
            None, m.conversa, m.gatilho, m.base, m.ctx, p, m.pac, forcar_conferencia=True
        )
    return await _conferir_antigo(m, p)


async def _conferir_antigo(m: SimpleNamespace, p: saida.Proposta) -> Any:
    """O `agente._responder` de antes do portão, do `com_par_da_politica` ao `aplicar`."""
    from app.services.atendimento import agente

    conversa, gatilho, base, ctx, pac = m.conversa, m.gatilho, m.base, m.ctx, m.pac
    p = checagens.com_par_da_politica(p, ctx)
    p, pos = checagens.responde_e_marca_da_proposta(p, ctx)
    ctx.pistas_turno = list(
        dict.fromkeys([*ctx.pistas_turno, *acao_pessoa.pistas_do_rascunho(p.texto)])
    )
    ctx.acao = p.acao
    ctx.acoes_modelo = [*saida.acoes_do_modelo(p), *pos]
    ctx.evidencias = saida.evidencias(p)
    garantias = ctx.garantias_conferidas
    if conversa.canal == constantes.CANAL_PERGUNTA:
        garantias = [g for g in garantias if g.get("regra") and not g.get("status")] or None
    entrada = saida.para_conferir(p, base.ids_categorias)
    entrada["motivo_modelo"] = checagens.motivo_sem_lacuna(entrada["motivo_modelo"], base.valores)
    entrada["texto"] = prompt.caminho_na_frase(entrada["texto"])
    fichas = [*pac.fichas, *ctx.fichas_lidas]
    conferida = await ia.conferir_saida(
        None, conversa, gatilho, **entrada, motivos=base.motivos, ctx=base.ctx, fatos=pac.fatos,
        valores=base.valores, categorias=base.categorias, regras=base.regras,
        textos_cliente=base.textos_cliente, textos_rajada=base.textos_rajada, foco=base.foco,
        fichas=fichas, resposta_aprovada=False, garantias_conferidas=garantias,
        garantia_consultada=ctx.garantia_consultada, permitidos_extra=ctx.permitidos_extra(),
        modo=base.modo, nf_politica_aprovada=base.nf_politica_aprovada,
        opcoes_sensiveis=base.opcoes_sensiveis, forcar=True, agente=True,
        devolucao_de_rotina=ctx.devolucao_de_rotina or ctx.responde_e_marca,
        produto_pela_equipe=bool(ctx.pares_citados(ctx.evidencias, de_produto=True)),
        fatos_citados=ctx.fatos_citados(ctx.evidencias),
        produto_pelo_anuncio=checagens.liberacao_da_proposta(
            p, entrada["texto"], entrada["categoria"], ctx, base, fichas=fichas
        ),
        garantia_apple_pelo_fato=checagens.garantia_apple_da_proposta(
            p, entrada["texto"], ctx, base, fichas=fichas
        ),
    )  # fmt: skip
    lacunas = checagens.lacunas_no_texto(conferida.texto)
    conferida = checagens.sem_lacuna_vazia(conferida)
    conferida = checagens.sem_o_texto_vazio_de_pessoa(
        conferida, acao=saida.PESSOA if p.acao == saida.PESSOA or ctx.acoes_pessoa else p.acao
    )
    p_conferir = (
        p.model_copy(update={"texto": checagens.sem_as_frases_das_lacunas(p.texto, lacunas)})
        if lacunas
        else p
    )
    ctx.pedido_da_conversa = pac.pedido
    chk = checagens.conferir(p_conferir, conferida, ctx, base, fichas=fichas, gatilho=gatilho)
    ja = {x.codigo for x in chk.pendencias}
    extra = [
        x
        for x in checagens.conferir_andamento(
            base.ctx,
            etiquetas=acao_pessoa.etiquetas_da_conversa(conversa),
            motivos=conferida.motivos,
            rotina=ctx.devolucao_de_rotina or ctx.responde_e_marca,
        )
        if x.codigo not in ja
    ]
    if extra:
        chk = checagens.Checagens(pendencias=[*chk.pendencias, *extra])
    ja_tratado = agente._ja_tratado_pela_loja(p, conferida, ctx, base, gatilho)
    if ja_tratado or agente._nao_pede_resposta(p, conferida, ctx, base):
        return SimpleNamespace(proposta=p, conferida=conferida, chk=chk, final=None, codigos=[],
                               sem_resposta=True, ja_tratado=ja_tratado)  # fmt: skip
    final = checagens.aplicar(conferida, chk, acoes_pessoa=ctx.acoes_pessoa, modo=base.modo)
    agente._guardar_avisos(ctx, final)
    return SimpleNamespace(
        proposta=p, conferida=conferida, chk=chk, final=final,
        codigos=[x.codigo for x in chk.pendencias], sem_resposta=False, ja_tratado=False,
    )  # fmt: skip


def _pela_etiqueta(m: SimpleNamespace, c: Any) -> str | None:
    """A rede do `_gerar` por cima do resultado (`ia._andamento_pela_etiqueta`): o motivo que
    ela poria, ou None."""
    res = SimpleNamespace(
        conferida=c.final,
        fatos_agente={**m.ctx.para_fatos(), "checagens": list(c.codigos)},
        precisa_humano=False,
        motivo="",
    )
    ia._andamento_pela_etiqueta(res, m.conversa, m.base)
    return res.motivo or None if res.precisa_humano else None


def _antes(r: Mapping[str, Any]) -> str:
    d = r.get("decisao") or {}
    motivo = d.get("motivo")
    if (
        r.get("parada") == "sem_resposta"
        or motivo == "rascunho_nao_pendente"
        and not d.get("pessoa")
    ):
        return (
            DESCARTADO
            if d.get("sem_resposta") or r.get("parada") == "sem_resposta"
            else str(motivo)
        )
    return CHEGA if motivo == "enviaria" else f"pessoa:{motivo}"


async def regatilhar(
    r: Mapping[str, Any], canal: Mapping[str, Any] | None, *, cliente: Any = None
) -> dict:
    """A decisão de um registro com o código atual (e, com `cliente`, o verificador)."""
    proposta = r.get("proposta") or {}
    linha = {
        "id": r.get("id"),
        "loja": loja_de(r),
        "categoria": (r.get("decisao") or {}).get("categoria") or proposta.get("assunto"),
        "antes": _antes(r),
        "rascunho_antes": r.get("texto_final"),
        "resposta_real": r.get("resposta_real"),
    }
    if r.get("pulado") or r.get("parada") is None:
        return {**linha, "decisao": "pulado", "bloqueios": [str(r.get("pulado"))], "avisos": []}
    if r.get("atalho") or r.get("parada") == "atalho_aprovado":
        # A resposta aprovada sem modelo (NF, cartão, política): o código de sempre.
        return {**linha, "decisao": "atalho", "bloqueios": [], "avisos": []}
    if not proposta or r.get("parada") not in ("fim", "sem_resposta"):
        return {**linha, "decisao": f"pessoa:parada_{r.get('parada') or 'sem_rascunho'}",
                "bloqueios": [str(r.get("parada"))], "avisos": [], "rascunho": None}  # fmt: skip
    m = montar(r)
    if m.erro:
        return {**linha, "decisao": "erro", "erro": m.erro, "bloqueios": [], "avisos": []}
    p = saida.Proposta(**{k: proposta.get(k) for k in saida.Proposta.model_fields if k in proposta})
    c = await _conferir(m, p)
    if c.sem_resposta:
        return {**linha, "decisao": DESCARTADO, "bloqueios": [], "avisos": [], "rascunho": None}
    final = c.final
    categoria = ia.assunto_do_agente(final.categoria, c.proposta.acao, m.base.ids_categorias)
    linha["categoria"] = categoria
    duros = [*final.motivos, *(a for a in m.ctx.acoes_pessoa if a not in final.motivos)]
    motivo = verificador.por_que_nao_verifica(final, c.proposta, m.ctx, m.base, m.conversa)
    if motivo is None:
        motivo = verificador.por_que_nao_verifica_no_canal(
            categoria, m.ctx, m.base, m.conversa, _canal_ns(canal), checagens_feitas=c.codigos
        )
    if motivo is None and (etiqueta := _pela_etiqueta(m, c)):
        motivo, duros = "em_andamento_pela_etiqueta", [*duros, etiqueta]
    avisos = list(getattr(final, "avisos", None) or [])
    out = {
        **linha,
        "bloqueios": duros if motivo else [],
        "avisos": avisos,
        "rascunho": final.texto,
        "pares_citados": [x.get("id") for x in m.ctx.pares_citados(m.ctx.evidencias)],
        "pares_de_produto": [
            x.get("id") for x in m.ctx.pares_citados(m.ctx.evidencias, de_produto=True)
        ],
    }
    if motivo is not None:
        return {**out, "decisao": f"pessoa:{motivo}"}
    if cliente is None:
        return {**out, "decisao": CHEGA}
    resumo = verificador.resumo_do_pacote(
        m.ctx, m.base, m.gatilho, pediu_evidencia=m.pac.pediu_evidencia, avisos=avisos
    )
    texto = verificador.texto_para_verificar(final.texto, m.base.valores, m.base.nomes)
    m.ctx.modo_replay = True  # o gasto vai às chaves do replay/eval (o teto do dia é um só)
    v = await verificador.verificar(
        m.ctx, c.proposta, texto, resumo, m.ctx.rastro, cliente=cliente,
        prazo_s=verificador.TIMEOUT_S,
        nomes=m.base.nomes, assunto=final.categoria,
        liberados=verificador.assuntos_liberados(
            _canal_ns(canal), produto=m.ctx.produto_pelo_anuncio
        ),
        declarado=categoria,
    )  # fmt: skip
    return {
        **out,
        "decisao": AUTO if v.ok else "pessoa:verificador",
        "verificador": {
            "ok": v.ok,
            "falha": v.falha,
            "motivo": None if v.ok else v.motivo(),
            # v16: o que o verificador escreveu (o "não liberou" sem marca não dizia o porquê).
            "motivos": list(v.veredito.motivos) if v.veredito is not None else [],
            "afirma_sem_lastro": (
                list(v.veredito.afirma_sem_lastro) if v.veredito is not None else []
            ),
            "custo_usd": round(v.uso.custo_usd, 6),
        },  # fmt: skip
        "custo_usd": round(v.uso.custo_usd, 6),
    }


# ── A calibração do verificador pelo caminho AUTO (o diferencial de segurança) ──


def _e_pedido(e: Any) -> bool:
    return isinstance(e, Mapping) and "postado" in e and "itens" in e


def _e_ficha(e: Any) -> bool:
    return isinstance(e, Mapping) and bool(e.get("item_id")) and "titulo" in e


def _ficha_do_caso(e: Mapping[str, Any]) -> dict:
    """A ficha da calibração no formato do pacote (as variações em lista, com o "completas")."""
    f = dict(e)
    v = f.get("variacoes")
    if isinstance(v, Mapping):
        f["variacoes"] = [dict(x) for x in v.get("variacoes") or [] if isinstance(x, Mapping)]
        f["variacoes_completas"] = v.get("completas") is True
    return f


def _embrulho(nome: str, dados: Any) -> str:
    return f'<dados fonte="{nome}">{json.dumps(dados, ensure_ascii=False)}</dados>'


def registro_da_calibracao(caso: Mapping[str, Any]) -> dict:
    """A linha de `calibracao_verificador.jsonl` como um registro do eval (o pacote, a
    transcrição e as ferramentas sintetizados das EVIDÊNCIAS do caso). PURA."""
    agora = str(caso.get("agora") or "20/10/2026 10:00")
    evid = list(caso.get("evidencias") or [])
    bloco = next((e for e in evid if _e_pedido(e)), None)
    fichas = [_ficha_do_caso(e) for e in evid if _e_ficha(e)]
    linhas = [
        "<dados>",
        "CABEÇALHO: " + json.dumps({"agora_sao_paulo": agora, "canal": "chat", "plataforma": "x"}),
        "PEDIDO DA CONVERSA: " + (json.dumps(bloco, ensure_ascii=False) if bloco else "nenhum"),
        *(
            ["ESTADO DA CONVERSA: " + json.dumps({"responde_e_marca": True})]
            if caso.get("pos_envio")
            else []
        ),
        "PRODUTO (fichas): " + json.dumps(fichas, ensure_ascii=False),
        "EVIDÊNCIAS DESTES DADOS (ids): "
        + json.dumps([*(["pedido"] if bloco else []), *(f"ficha:{f['item_id']}" for f in fichas)]),
    ]
    if caso.get("pares"):
        linhas.append("COMO A EQUIPE JÁ RESPONDEU CASOS PARECIDOS:")
        for par in caso["pares"]:
            rot = ["esta loja" if par.get("mesma_loja") else "outra loja"]
            if par.get("mesmo_anuncio"):
                rot.append(f"mesmo anúncio «{par.get('titulo_do_anuncio') or ''}»")
            elif par.get("mesmo_produto"):
                rot.append("mesmo produto")
            rot.append(
                "vale para produto" if par.get("fonte_de_produto") else "não vale para produto"
            )
            rot.append(f"há {int(par.get('idade_dias') or 1)} dias")
            linhas.append(
                f"- {par['id']} ({', '.join(rot)}): cliente «{par.get('pergunta') or ''}» → "
                f"equipe «{par.get('resposta') or ''}»"
            )
    dia, hora = agora.split(" ")[0][:5], agora.split(" ")[1]
    conversa = [str(x) for x in caso.get("conversa") or []]
    ditas = " ".join(conversa)
    for pergunta in caso.get("perguntas") or []:
        if str(pergunta) not in ditas:
            conversa.append(f"[{dia} {hora}] Cliente: {pergunta}")
    ferramentas_: list[dict] = []
    passos: list[dict] = []
    ids_fatos: list[str] = []
    for e in evid:
        if e is bloco or _e_ficha(e):
            continue
        if isinstance(e, Mapping) and ("fim_hardware" in e or ("regra" in e and "status" in e)):
            nome = ferramentas.VER_GARANTIA
        elif isinstance(e, Mapping) and ("fatos" in e or "regra" in e or "orientacao" in e):
            nome = "consultar_manual"
            ids_fatos += [str(f.get("id")) for f in e.get("fatos") or [] if isinstance(f, Mapping)]
        else:
            nome = "ver_dado"
        corpo = e if isinstance(e, Mapping) else {"texto": str(e)}
        ferramentas_.append(
            {
                "ferramenta": nome,
                "args": {},
                "resultado": _embrulho(nome, corpo),
            }
        )
        passos.append({"ids": [str(x) for x in corpo.get("evidencias") or []]})
    for b in caso.get("busca") or []:
        ferramentas_.append(
            {
                "ferramenta": ferramentas.BUSCAR_ANUNCIOS_DA_LOJA,
                "args": {"termo": b.get("termo")},
                "resultado": _embrulho("buscar_anuncios_da_loja", b),
            }
        )
        passos.append({"ids": [f"anuncio:{a.get('item_id')}" for a in b.get("anuncios") or []]})
    evidencias = [
        *(["pedido"] if bloco else []),
        *(f"ficha:{f['item_id']}" for f in fichas),
        *ids_fatos,
        *(str(p_.get("id")) for p_ in caso.get("pares") or []),
        *(
            f"anuncio:{a.get('item_id')}"
            for b in caso.get("busca") or []
            for a in b.get("anuncios") or []
        ),
    ]
    return {
        "id": caso.get("id"),
        "grupo": caso.get("grupo"),
        "plataforma": caso.get("plataforma") or "shopee",
        "parada": "fim",
        # O conservador: só o TEXTO segura (as ações de pessoa do código, que a linha da
        # calibração não tem, ficam de fora — elas só seguram mais).
        "recalcular_acoes": False,
        "decisao": {"categoria": caso.get("assunto")},
        "proposta": {
            "acao": caso.get("acao") or "responder",
            "acao_pessoa": caso.get("acao_pessoa"),
            "assunto": caso.get("declarado") or caso.get("assunto") or "outro",
            "confianca": 0.9,
            "evidencias": evidencias,
            "motivo_pessoa": "",
            "texto": caso.get("texto") or "",
        },
        "texto_final": caso.get("texto"),
        "rastro": {
            "pacote": "\n".join([*linhas, "</dados>"]),
            "transcricao": conversa,
            "ferramentas": ferramentas_,
            "passos": passos,
        },
    }


async def diferencial(casos: Sequence[Mapping[str, Any]], verif: Mapping[str, Any]) -> dict:
    """Cada linha RUIM (`esperado.bloqueia`) da calibração pelo caminho AUTO: segura por um
    bloqueio DURO ou depende só do verificador — e, com a saída de uma calibração
    (`verif`: id → linha com `obtido.bloqueia`), se o verificador a deixou escapar."""
    canal = {"modo": constantes.MODO_AUTO, "auto_categorias": [c["id"] for c in _categorias()],
             "auto_orientacao": list(constantes.ASSUNTOS_ORIENTACAO),
             "auto_agente": [c["id"] for c in _categorias()]}  # fmt: skip
    so_verificador, duros, escapes = [], Counter(), []
    for caso in casos:
        if not (caso.get("esperado") or {}).get("bloqueia") and (caso.get("esperado") or {}).get(
            "ok_para_envio_automatico", True
        ):
            continue
        do_caso = canal
        if isinstance(caso.get("liberados"), list):
            # A régua do canal do caso (os ASSUNTOS LIBERADOS que ele traz).
            lib = [str(a) for a in caso["liberados"]]
            do_caso = {
                "modo": constantes.MODO_AUTO,
                "auto_categorias": lib,
                "auto_agente": lib,
                "auto_orientacao": [a for a in lib if a in constantes.ASSUNTOS_ORIENTACAO],
            }
        x = await regatilhar(registro_da_calibracao(caso), do_caso)
        decisao = str(x.get("decisao"))
        if decisao == CHEGA:
            linha = {"id": caso["id"], "grupo": caso.get("grupo"), "avisos": x.get("avisos")}
            v = verif.get(caso["id"])
            if v is not None:
                linha["verificador_v13_segurou"] = bool((v.get("obtido") or {}).get("bloqueia"))
                if not linha["verificador_v13_segurou"]:
                    escapes.append(linha)
            so_verificador.append(linha)
        else:
            for b in x.get("bloqueios") or [decisao]:
                duros[re.sub(r"\(.*|«.*", "", str(b))[:70].strip()] += 1
    return {
        "so_verificador": so_verificador,
        "escapes": escapes,
        "duros": dict(duros.most_common()),
    }


# ── As respostas da equipe do mesmo produto (o pedido do dono de 27/10/2026) ──


async def pares_do_banco(registros: Sequence[Mapping[str, Any]]) -> dict:
    """Para cada registro, os pares que o pacote mostrou no eval × os que a busca NOVA
    (`pares.exemplos_do_turno`: primeiro o mesmo anúncio e o mesmo produto) mostraria no
    mesmo instante (`gatilho_em`). Só lê o banco (a sessão do replay)."""
    from app.services.atendimento.agente import pacote
    from app.services.atendimento.agente import pares as pares_svc

    linhas = []
    async with _leitura() as s:
        for r in registros:
            m = montar(r)
            if getattr(m, "erro", None) or not r.get("gatilho_em"):
                continue
            busca = pacote._turno_para_a_busca(m.base)
            itens = sorted(m.ctx.anuncios_do_pacote)
            if not busca or not itens:
                continue
            novos = await pares_svc.exemplos_do_turno(
                s,
                pergunta=busca,
                plataforma=r.get("plataforma") or "shopee",
                integration_id=UUID(str(r["integration_id"])),
                item_ids=itens,
                conversa_id=UUID(str(r["conversa_id"])),
                as_of=datetime.fromisoformat(str(r["gatilho_em"])),
                canal_no_automatico=True,
            )
            antes = m.sec.get("pares") or []
            linhas.append(
                {
                    "id": r.get("id"),
                    "antes_mesmo_anuncio": sum(bool(p.get("mesmo_anuncio")) for p in antes),
                    "antes_de_produto": sum(bool(p.get("fonte_de_produto")) for p in antes),
                    "agora_mesmo_anuncio": sum(bool(p.get("mesmo_anuncio")) for p in novos),
                    "agora_de_produto": sum(bool(p.get("fonte_de_produto")) for p in novos),
                    "agora": [
                        {k: p.get(k) for k in ("id", "pergunta", "resposta", "fonte_de_produto")}
                        for p in novos
                        if p.get("mesmo_anuncio") or p.get("mesmo_produto")
                    ][:3],
                }
            )
    return {
        "turnos_com_anuncio": len(linhas),
        "com_par_do_mesmo_anuncio_antes": sum(x["antes_mesmo_anuncio"] > 0 for x in linhas),
        "com_par_do_mesmo_anuncio_agora": sum(x["agora_mesmo_anuncio"] > 0 for x in linhas),
        "com_par_de_produto_antes": sum(x["antes_de_produto"] > 0 for x in linhas),
        "com_par_de_produto_agora": sum(x["agora_de_produto"] > 0 for x in linhas),
        "linhas": linhas,
    }


# ── O resumo ──────────────────────────────────────────────────────────────


def _taxa(n: int, total: int) -> str:
    return f"{n}/{total} ({(100.0 * n / total) if total else 0:.0f}%)"


def resumo(linhas: Sequence[Mapping[str, Any]]) -> dict:
    """O resumo: a decisão de antes e a de agora, no total, por loja e por categoria."""
    passa = (AUTO, CHEGA)

    def _grupo(chave: str) -> dict:
        g: dict[str, Counter] = defaultdict(Counter)
        for x in linhas:
            k = str(x.get(chave) or "?")
            g[k]["total"] += 1
            g[k]["antes"] += x.get("antes") == CHEGA
            g[k]["agora"] += x.get("decisao") in passa
            g[k]["auto"] += x.get("decisao") == AUTO
        return {k: dict(v) for k, v in sorted(g.items())}

    total = len(linhas)
    duros = Counter()
    avisos = Counter()
    for x in linhas:
        if str(x.get("decisao")).startswith("pessoa:"):
            duros[str(x["decisao"])[7:]] += 1
        for a in x.get("avisos") or []:
            avisos[re.sub(r"\(.*", "", str(a))[:70].strip()] += 1
    return {
        "total": total,
        "antes_chega_ao_verificador": sum(x.get("antes") == CHEGA for x in linhas),
        "agora_chega_ao_verificador": sum(x.get("decisao") in passa for x in linhas),
        "agora_auto": sum(x.get("decisao") == AUTO for x in linhas),
        "descartados": sum(x.get("decisao") == DESCARTADO for x in linhas),
        "com_par_de_produto": sum(bool(x.get("pares_de_produto")) for x in linhas),
        "com_par_citado": sum(bool(x.get("pares_citados")) for x in linhas),
        "custo_usd": round(sum(float(x.get("custo_usd") or 0) for x in linhas), 6),
        "pessoa_por_motivo": dict(duros.most_common()),
        "avisos_mais_comuns": dict(avisos.most_common(15)),
        "por_loja": _grupo("loja"),
        "por_categoria": _grupo("categoria"),
    }


def imprimir(res: Mapping[str, Any]) -> None:
    t = res["total"]
    print(f"registros: {t}")
    print("antes  — chega ao verificador:", _taxa(res["antes_chega_ao_verificador"], t))
    print("agora  — chega ao verificador:", _taxa(res["agora_chega_ao_verificador"], t))
    if res["agora_auto"]:
        print("agora  — auto (verificador ok):", _taxa(res["agora_auto"], t))
    print(
        "descartados:",
        res["descartados"],
        "| com par de produto citado:",
        res["com_par_de_produto"],
    )
    print("custo do verificador (US$):", res["custo_usd"])
    print("pessoa por motivo:", json.dumps(res["pessoa_por_motivo"], ensure_ascii=False))
    for nome in ("por_loja", "por_categoria"):
        print(nome + ":")
        for k, v in res[nome].items():
            linha = (
                f"  {k:28s} total {v.get('total', 0):3d}  antes {v.get('antes', 0):3d}"
                f"  agora {v.get('agora', 0):3d}"
            )
            if res["agora_auto"]:
                linha += f"  auto {v.get('auto', 0):3d}"
            print(linha)


# ── A linha de comando ────────────────────────────────────────────────────


async def _canal_do_banco(canal_id: str) -> dict | None:
    from app.models.atendimento import AtendimentoCanal

    async with _leitura() as s:
        k = await s.get(AtendimentoCanal, UUID(canal_id))
        if k is None:
            return None
        return {"modo": k.modo, "auto_categorias": list(k.auto_categorias or []),
                "auto_orientacao": list(k.auto_orientacao or []),
                "auto_agente": list(k.auto_agente or [])}  # fmt: skip


async def _canais_por_integracao(integracoes: set[str]) -> dict[str, dict]:
    """O canal de cada loja (sem `--config-canal`): a loja fora do automático, listas vazias."""
    from sqlalchemy import select

    from app.models.atendimento import AtendimentoCanal

    saida_: dict[str, dict] = {}
    async with _leitura() as s:
        for k in (await s.scalars(select(AtendimentoCanal))).all():
            integ = str(getattr(k, "integration_id", "") or "")
            if integ in integracoes and k.modo == constantes.MODO_AUTO:
                saida_[integ] = {"modo": k.modo, "auto_categorias": list(k.auto_categorias or []),
                                 "auto_orientacao": list(k.auto_orientacao or []),
                                 "auto_agente": list(k.auto_agente or [])}  # fmt: skip
    return saida_


async def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--resultados", required=True)
    ap.add_argument("--saida")
    ap.add_argument("--config-canal")
    ap.add_argument("--config-canal-json")
    ap.add_argument("--verificar", action="store_true")
    ap.add_argument("--teto-usd", type=float, default=TETO_USD)
    ap.add_argument("--limite", type=int)
    ap.add_argument("--resumo-so", action="store_true")
    ap.add_argument("--lojas-json", help='{"<integration_id>": "NOME"} (sem banco)')
    ap.add_argument(
        "--calibracao",
        action="store_true",
        help="--resultados é a calibracao_verificador.jsonl: o diferencial de segurança",
    )
    ap.add_argument("--verificador-saida", help="a saída de uma calibração (obtido.bloqueia)")
    ap.add_argument(
        "--pares-do-banco",
        action="store_true",
        help="compara os pares do pacote do eval com a busca nova (precisa do banco)",
    )
    a = ap.parse_args(argv)
    registros = [json.loads(x) for x in Path(a.resultados).read_text().splitlines() if x.strip()]
    if a.pares_do_banco:
        res = await pares_do_banco(registros)
        print(json.dumps({k: v for k, v in res.items() if k != "linhas"}, ensure_ascii=False))
        if a.saida:
            Path(a.saida).write_text(json.dumps(res, ensure_ascii=False, indent=1))
        return 0
    if a.calibracao:
        verif = {}
        if a.verificador_saida:
            for x in Path(a.verificador_saida).read_text().splitlines():
                if x.strip():
                    linha = json.loads(x)
                    verif[linha.get("id")] = linha
        res = await diferencial(registros, verif)
        print(json.dumps(res, ensure_ascii=False, indent=1))
        if a.saida:
            Path(a.saida).write_text(json.dumps(res, ensure_ascii=False, indent=1))
        return 0
    if a.limite:
        registros = registros[: a.limite]
    if a.lojas_json:
        LOJAS.update(json.loads(Path(a.lojas_json).read_text()))
    else:
        try:
            await _nomes_das_lojas({str(r.get("integration_id")) for r in registros})
        except Exception as e:  # noqa: BLE001 — sem banco: o prefixo do id
            print(f"sem os nomes das lojas ({type(e).__name__})", file=sys.stderr)
    canal_fixo: dict | None = None
    if a.config_canal_json:
        canal_fixo = json.loads(Path(a.config_canal_json).read_text())
    elif a.config_canal:
        canal_fixo = await _canal_do_banco(a.config_canal)
        if canal_fixo is None:
            print(f"canal {a.config_canal} não encontrado", file=sys.stderr)
            return 2
    por_loja: dict[str, dict] = {}
    if canal_fixo is None:
        try:
            por_loja = await _canais_por_integracao(
                {str(r.get("integration_id")) for r in registros}
            )
        except Exception as e:  # noqa: BLE001 — sem banco: toda loja fora do automático
            print(f"sem os canais do banco ({type(e).__name__}): listas vazias", file=sys.stderr)
    cliente = None
    pilha = None
    if a.verificar:
        import contextlib

        import anthropic

        from app.config import get_settings
        from app.services.atendimento import agente_config

        chave = agente_config.chave_do_agente(get_settings())
        if not chave:
            print("sem a chave Claude do agente (ATENDIMENTO_AGENTE_API_KEY)", file=sys.stderr)
            return 2
        pilha = contextlib.AsyncExitStack()
        cliente = await pilha.enter_async_context(
            anthropic.AsyncAnthropic(api_key=chave, base_url=ia.BASE_CLAUDE, max_retries=0)
        )
    linhas: list[dict] = []
    gasto = 0.0
    try:
        for r in registros:
            canal = (
                canal_fixo if canal_fixo is not None else por_loja.get(str(r.get("integration_id")))
            )
            usar = cliente if (cliente is not None and gasto < a.teto_usd) else None
            try:
                x = await regatilhar(r, canal, cliente=usar)
            except Exception as e:  # noqa: BLE001 — um registro ruim não para o resto
                x = {"id": r.get("id"), "loja": loja_de(r), "decisao": "erro",
                     "erro": f"{type(e).__name__}: {str(e)[:200]}", "antes": _antes(r)}  # fmt: skip
            gasto += float(x.get("custo_usd") or 0)
            linhas.append(x)
            if not a.resumo_so:
                campos = ("id", "loja", "categoria", "antes", "decisao")
                print(json.dumps({k: x.get(k) for k in campos},
                                 ensure_ascii=False))  # fmt: skip
    finally:
        if pilha is not None:
            await pilha.aclose()
    res = resumo(linhas)
    imprimir(res)
    if a.saida:
        with open(a.saida, "w") as f:
            for x in linhas:
                f.write(json.dumps(x, ensure_ascii=False) + "\n")
            f.write(json.dumps({"resumo": res}, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
