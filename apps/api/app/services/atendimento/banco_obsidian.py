"""A cópia do banco de respostas para o Obsidian (13/11/2026). PURA (menos `lojas_no_automatico`).

O Eduardo, 13/11/2026: o Postgres em produção é o banco vivo ("em prod é a mesma coisa do
Obsidian") e o cofre do Obsidian no Mac mini é a CÓPIA DE CONTINGÊNCIA, atualizada toda
noite: uma pessoa da equipe precisa conseguir ler e usar sem o DaVinci, e o formato
reimporta se for preciso. Quem busca é o Mac (scripts/obsidian_copia.py, pelo token do
robô): o servidor nunca depende do Mac ligado.

`arquivos` monta a pasta `DaVinci/` inteira (caminho relativo → texto) e `zip_de` a
empacota. FORMATO 1, o mesmo da primeira versão gravada da colheita:

- `Respostas/<tipo>/<chave>.md` — uma nota por resposta, com cabeçalho (id, tipo, loja,
  plataforma, produto, anúncio, status, fonte, datas, o que o cliente fez depois);
- `Tipos/<tipo>.md` — uma por tipo de pergunta: quantas respostas, como a IA se sai, a
  régua (80%, 90% nos sensíveis), em quantas lojas sai sozinho hoje, e as respostas
  agrupadas por loja, da mais nova para a mais antiga;
- `Lojas/<loja (plataforma)>.md` e `Modelos/<código>.md` — o destino dos links;
- `Início.md` (o índice), `_formato.md` (os campos) e `.gerado-pelo-davinci` (a marca que
  deixa o script trocar a pasta; sem ela, ele não mexe).

Texto: só o do banco, que já é mascarado e generalizado (sem nome, pedido, rastreio ou
contato — `banco_respostas`).
"""

from __future__ import annotations

import io
import json
import re
import zipfile
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from app.services.atendimento import tipos_pergunta
from app.services.atendimento.banco_respostas import ia_enviou_igual

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

FORMATO = 1
PASTA = "DaVinci"
MARCA = ".gerado-pelo-davinci"
FUSO = timezone(timedelta(hours=-3))
REGUA_PADRAO = "80%"
REGUA_SENSIVEL = "90%"
MAX_CHARS_LISTA_PERGUNTA = 260
MAX_CHARS_LISTA_RESPOSTA = 400
MAX_POR_TIPO_NO_MODELO = 40

ROTULO: dict[str, str] = {
    "espec_celular": "Ficha técnica do celular",
    "estoque_variacao": "Cor, variação e estoque",
    "conteudo_da_caixa": "O que vem na caixa",
    "agradecimento": "Agradecimento ou confirmação",
    "defeito": "Defeito ou chegou danificado",
    "rastreio": "Rastreio e atraso",
    "espec_mala": "Malas, medidas e rodas",
    "cancelamento": "Cancelamento",
    "cobertura_regiao": "Entrega para estado ou cidade",
    "troca_devolucao": "Troca ou devolução",
    "reembolso": "Reembolso e estorno",
    "originalidade_condicao": "Original, lacrado e confiança",
    "nota_fiscal": "Nota fiscal",
    "garantia": "Garantia e conserto",
    "saudacao_ou_vaga": "Saudação ou frase vaga",
    "resistencia_agua": "Água, poeira e queda",
    "pede_atendente": "Pede atendente ou opção do menu",
    "preco_pagamento": "Preço, frete e pagamento",
    "uso_configuracao": "Como usar ou configurar",
    "desconto": "Desconto, brinde e atacado",
    "pedido_link_midia": "Pede link, foto ou vídeo",
    "item_errado_faltando": "Item errado ou faltando",
    "espec_eletro": "Air fryer e eletroportáteis",
    "espec_outros": "Apple e outros produtos",
    "prazo_antes_compra": "Prazo antes da compra",
    "reclamacao_forte": "Reclamação forte, Procon ou processo",
    "entrega_instrucoes": "Retirada e transportadora",
    "opiniao_recomendacao": "Opinião e comparação",
    "prazo_envio": "Pago e ainda não postado",
    "endereco": "Endereço",
    "entrega_nao_recebida": "Consta entregue ou extravio",
    "confirmar_variacao": "Confirmar a variação comprada",
    "acessorios_avulsos": "Acessórios avulsos",
    "canal_externo_dados": "Contato fora da plataforma",
    "parceria_fora_escopo": "Parceria e divulgação",
    "o_que_e_o_anuncio": "É o aparelho ou só a capa",
    "pedido_nao_localizado": "Pedido não localizado",
    "posvenda_pergunta_publica": "Assunto de compra na pergunta pública",
    "marca_autorizacao": "Marca, autorização ou fiscalização",
    "substituicao_estoque": "Aviso de falta de estoque",
    "divergencia_anuncio": "Informação conflitante no anúncio",
    "outro": "Outro assunto",
    tipos_pergunta.INDEFINIDO: "Sem tipo definido",
}
GRUPO_ROTULO = {
    tipos_pergunta.GRUPO_PRODUTO: "Produto e política da loja",
    tipos_pergunta.GRUPO_PEDIDO: "Status do pedido",
    tipos_pergunta.GRUPO_PESSOA: "Problema com a compra",
    tipos_pergunta.GRUPO_CORTESIA: "Cortesia e contato",
    tipos_pergunta.INDEFINIDO: "Sem tipo definido",
}
ORDEM_GRUPOS = (
    tipos_pergunta.GRUPO_PRODUTO,
    tipos_pergunta.GRUPO_PEDIDO,
    tipos_pergunta.GRUPO_PESSOA,
    tipos_pergunta.GRUPO_CORTESIA,
    tipos_pergunta.INDEFINIDO,
)
PLATAFORMA_ROTULO = {
    "shopee": "Shopee",
    "ml": "Mercado Livre",
    "tiktok": "TikTok",
    "amazon": "Amazon",
    "magalu": "Magalu",
    "carrefour": "Carrefour",
    "temu": "Temu",
    "aliexpress": "AliExpress",
}
DEPOIS_ROTULO = {
    "resolveu": "agradeceu ou confirmou",
    "continuou": "seguiu a conversa",
    "nao_resolveu": "voltou reclamando ou repetiu",
    "sem_retorno": "não escreveu mais",
}
_PROIBIDO_NO_NOME = re.compile(r'[\\/:*?"<>|#^\[\]]')
# As etiquetas das notas: o grafo do Obsidian pinta cada uma de uma cor (o Eduardo, 13/11/2026).
ETIQUETA_TIPO = "davinci/tipo"
ETIQUETA_SENSIVEL = "davinci/sensivel"
ETIQUETA_LOJA = "davinci/loja"
ETIQUETA_MODELO = "davinci/modelo"
ETIQUETA_RESPOSTA = "davinci/resposta"
ETIQUETA_INICIO = "davinci/inicio"


def rotulo(tipo: str) -> str:
    return ROTULO.get(tipo, tipo)


def nome_de_arquivo(texto: str) -> str:
    """O nome de nota que o Obsidian aceita (sem / : ? # [ ] …). PURA."""
    return " ".join(_PROIBIDO_NO_NOME.sub(" ", texto).split()).strip(" .") or "sem nome"


def _plat(p: str | None) -> str:
    return PLATAFORMA_ROTULO.get(p or "", p or "")


def _nota_tipo(tipo: str) -> str:
    return nome_de_arquivo(rotulo(tipo))


def _nota_loja(plataforma: str, loja: str | None) -> str:
    return nome_de_arquivo(f"{loja or 'sem loja'} ({_plat(plataforma)})")


def _yaml(valor: Any) -> str:
    if isinstance(valor, bool):
        return "true" if valor else "false"
    if isinstance(valor, int | float):
        return str(valor)
    if valor is None:
        return '""'
    if isinstance(valor, list | tuple):
        return json.dumps(list(valor), ensure_ascii=False)
    return json.dumps(str(valor), ensure_ascii=False)


def _cabecalho(campos: Mapping[str, Any]) -> str:
    return "---\n" + "\n".join(f"{k}: {_yaml(v)}" for k, v in campos.items()) + "\n---\n"


def _uma_linha(texto: str | None, teto: int | None = None) -> str:
    t = " ".join(str(texto or "").split())
    if teto and len(t) > teto:
        t = t[: teto - 1].rstrip() + "…"
    return t


def _data(valor: datetime | None) -> str:
    return valor.astimezone(FUSO).strftime("%Y-%m-%d") if valor else ""


def _aviso(agora: datetime) -> str:
    return (
        f"> [!info] Gerado pelo DaVinci em {agora.astimezone(FUSO).strftime('%d/%m/%Y %H:%M')}. "
        "Não edite aqui: a próxima cópia reescreve esta pasta. Para mudar uma resposta, "
        "corrija no DaVinci.\n"
    )


def _regua(tipo: str) -> str:
    return REGUA_SENSIVEL if tipo in tipos_pergunta.SENSIVEIS else REGUA_PADRAO


def arquivos(
    respostas: Sequence[Any],
    *,
    contagens: Mapping[str, Mapping[str, Any]],
    no_automatico: Mapping[str, Sequence[str]],
    agora: datetime,
) -> dict[str, str]:
    """A pasta `DaVinci/` inteira: caminho relativo (com `DaVinci/`) → conteúdo. PURA.

    `respostas` = as linhas ativas do banco (objetos com os campos de
    `AtendimentoResposta`); `contagens` = `banco_respostas.contagens_por_tipo`;
    `no_automatico` = tipo → lojas em que ele sai sozinho hoje (`lojas_no_automatico`).
    """
    saida: dict[str, str] = {}
    aviso = _aviso(agora)
    quando = agora.astimezone(FUSO).strftime("%Y-%m-%d %H:%M")

    def link_tipo(t: str) -> str:
        return f"[[{_nota_tipo(t)}]]"

    def link_loja(p: str, loja: str | None) -> str:
        return f"[[{_nota_loja(p, loja)}]]"

    def link_modelo(m: str) -> str:
        return f"[[{nome_de_arquivo(m)}]]"

    por_tipo: dict[str, list[Any]] = defaultdict(list)
    por_loja: dict[tuple[str, str | None], list[Any]] = defaultdict(list)
    por_modelo: dict[str, list[Any]] = defaultdict(list)
    for r in respostas:
        por_tipo[r.tipo].append(r)
        por_loja[(r.plataforma, r.loja)].append(r)
        for m in r.modelos or ():
            por_modelo[m].append(r)

    # Respostas/<tipo>/<chave>.md
    for r in respostas:
        modelos = list(r.modelos or ())
        campos = {
            "id": r.chave,
            "tipo": r.tipo,
            "tipo_nome": rotulo(r.tipo),
            "plataforma": r.plataforma,
            "canal": r.canal,
            "loja": r.loja or "",
            "produto": modelos,
            "anuncio": r.item_id or "",
            "status": r.status,
            "fonte": r.fonte,
            "perguntado_em": _data(r.perguntado_em),
            "respondido_em": _data(r.respondido_em),
            "cliente_depois": r.cliente_depois or "",
            "formato": FORMATO,
            "tags": [ETIQUETA_RESPOSTA],
        }
        links = " · ".join(
            [link_tipo(r.tipo), link_loja(r.plataforma, r.loja)] + [link_modelo(m) for m in modelos]
        )
        corpo = (
            f"**Pergunta do cliente**\n> {_uma_linha(r.pergunta)}\n\n"
            f"**Resposta da equipe**\n> {_uma_linha(r.resposta)}\n\n{links}\n"
        )
        saida[f"{PASTA}/Respostas/{_nota_tipo(r.tipo)}/{r.chave}.md"] = (
            _cabecalho(campos) + "\n" + corpo
        )

    # Tipos/<tipo>.md
    for tipo, lista in por_tipo.items():
        decisoes, taxa = ia_enviou_igual(contagens.get(tipo, {}))
        lojas_auto = sorted(no_automatico.get(tipo, ()))
        sensivel = tipo in tipos_pergunta.SENSIVEIS
        campos = {
            "tipo": tipo,
            "tipo_nome": rotulo(tipo),
            "grupo": GRUPO_ROTULO.get(tipos_pergunta.grupo(tipo), ""),
            "sensivel": sensivel,
            "regua_envio_sozinho": _regua(tipo),
            "respostas_no_banco": len(lista),
            "respostas_30_dias": int(contagens.get(tipo, {}).get("recentes", 0)),
            "ia_decisoes_60_dias": decisoes,
            "ia_enviou_igual": f"{round(100 * taxa)}%" if taxa is not None else "",
            "lojas_com_envio_sozinho_liberado_hoje": len(lojas_auto),
            "atualizado_em": quando,
            "formato": FORMATO,
            "tags": [ETIQUETA_TIPO, ETIQUETA_SENSIVEL] if sensivel else [ETIQUETA_TIPO],
        }
        partes = [_cabecalho(campos), f"# {rotulo(tipo)}\n", aviso]
        resumo = (
            f"Respostas da equipe no banco: **{len(lista)}** · nos últimos 30 dias: "
            f"**{campos['respostas_30_dias']}**"
        )
        if taxa is not None:
            resumo += f" · a IA enviou igual em **{round(100 * taxa)}%** de {decisoes} decisões"
        resumo += f" · régua para sair sozinho: **{_regua(tipo)}**" + (
            " (sensível)" if sensivel else ""
        )
        partes.append(resumo + "\n")
        partes.append(
            f"Sai sozinho hoje em **{len(lojas_auto)}** lojas"
            + (f": {', '.join(lojas_auto)}" if lojas_auto else "")
            + ".\n"
        )
        if tipo == tipos_pergunta.INDEFINIDO:
            partes.append(
                "Perguntas que a palavra-chave não classificou (seguimento de conversa, frase "
                "vaga). O agente responde lendo a conversa inteira.\n"
            )
        grupos: dict[tuple[str, str | None], list[Any]] = defaultdict(list)
        for r in lista:
            grupos[(r.plataforma, r.loja)].append(r)
        for (plataforma, loja), rs in sorted(grupos.items(), key=lambda x: (-len(x[1]), str(x[0]))):
            partes.append(
                f"\n## {loja or 'sem loja'} · {_plat(plataforma)} ({len(rs)})\n"
                f"{link_loja(plataforma, loja)}\n"
            )
            for r in sorted(rs, key=lambda r: r.respondido_em, reverse=True):
                modelos = " · ".join(link_modelo(m) for m in (r.modelos or ()))
                extra = f" · {modelos}" if modelos else ""
                partes.append(
                    f"- **P:** {_uma_linha(r.pergunta, MAX_CHARS_LISTA_PERGUNTA)}\n"
                    f"  **R:** {_uma_linha(r.resposta, MAX_CHARS_LISTA_RESPOSTA)}\n"
                    f"  <small>{_data(r.respondido_em)}{extra} · [[{r.chave}|abrir]]</small>\n"
                )
        saida[f"{PASTA}/Tipos/{_nota_tipo(tipo)}.md"] = "\n".join(partes)

    # Lojas/<loja (plataforma)>.md
    for (plataforma, loja), rs in por_loja.items():
        tipos = Counter(r.tipo for r in rs)
        campos = {
            "loja": loja or "",
            "plataforma": plataforma,
            "respostas_no_banco": len(rs),
            "atualizado_em": quando,
            "formato": FORMATO,
            "tags": [ETIQUETA_LOJA],
        }
        partes = [
            _cabecalho(campos),
            f"# {loja or 'sem loja'} · {_plat(plataforma)}\n",
            aviso,
            f"Respostas da equipe no banco: **{len(rs)}**\n",
            "## Por tipo de pergunta\n",
        ]
        partes += [f"- {link_tipo(t)}: {n}" for t, n in tipos.most_common()]
        modelos = Counter(m for r in rs for m in (r.modelos or ()))
        if modelos:
            partes.append("\n## Modelos com mais perguntas\n")
            partes += [f"- {link_modelo(m)}: {n}" for m, n in modelos.most_common(15)]
        saida[f"{PASTA}/Lojas/{_nota_loja(plataforma, loja)}.md"] = "\n".join(partes) + "\n"

    # Modelos/<código>.md
    for modelo, rs in por_modelo.items():
        campos = {
            "produto": modelo,
            "respostas_no_banco": len(rs),
            "lojas": len({(r.plataforma, r.loja) for r in rs}),
            "atualizado_em": quando,
            "formato": FORMATO,
            "tags": [ETIQUETA_MODELO],
        }
        partes = [
            _cabecalho(campos),
            f"# {modelo}\n",
            aviso,
            f"O que a equipe já respondeu sobre o {modelo}: **{len(rs)}** respostas.\n",
        ]
        por_t: dict[str, list[Any]] = defaultdict(list)
        for r in rs:
            por_t[r.tipo].append(r)
        for tipo, lista in sorted(por_t.items(), key=lambda x: -len(x[1])):
            partes.append(f"\n## {rotulo(tipo)} ({len(lista)})\n{link_tipo(tipo)}\n")
            for r in sorted(lista, key=lambda r: r.respondido_em, reverse=True)[
                :MAX_POR_TIPO_NO_MODELO
            ]:
                partes.append(
                    f"- **P:** {_uma_linha(r.pergunta, 200)}\n"
                    f"  **R:** {_uma_linha(r.resposta, 300)}\n"
                    f"  <small>{r.loja or 'sem loja'} · {_plat(r.plataforma)} · "
                    f"{_data(r.respondido_em)} · [[{r.chave}|abrir]]</small>\n"
                )
        saida[f"{PASTA}/Modelos/{nome_de_arquivo(modelo)}.md"] = "\n".join(partes)

    # Início.md
    tipos_ordem = sorted(por_tipo, key=lambda t: (-len(por_tipo[t]), t))
    partes = [
        _cabecalho(
            {
                "gerado_em": quando,
                "respostas_no_banco": len(respostas),
                "tipos": len(por_tipo),
                "lojas": len(por_loja),
                "modelos": len(por_modelo),
                "formato": FORMATO,
                "tags": [ETIQUETA_INICIO],
            }
        ),
        "# Respostas da equipe\n",
        aviso,
        f"Tudo o que a equipe respondeu aos clientes e serve de referência: **{len(respostas)}** "
        f"respostas, em **{len(por_tipo)}** tipos de pergunta, **{len(por_loja)}** lojas e "
        f"**{len(por_modelo)}** modelos. Sem dado pessoal: nomes, pedidos, rastreios e contatos "
        "saíram antes de chegar aqui.\n",
        "Esta pasta é a cópia de contingência do banco de respostas do DaVinci, refeita toda "
        "noite. Se o DaVinci parar, a equipe acha aqui como respondemos cada tipo de pergunta, "
        "por loja e por modelo. As notas em `Respostas/` têm cabeçalho para reimportar no "
        "DaVinci (veja [[_formato]]).\n",
    ]
    for g in ORDEM_GRUPOS:
        ts = [t for t in tipos_ordem if tipos_pergunta.grupo(t) == g]
        if not ts:
            continue
        partes.append(f"\n## {GRUPO_ROTULO[g]}\n")
        partes.append("| Tipo | No banco | Últimos 30 dias | IA enviou igual | Régua |")
        partes.append("|---|---:|---:|---:|---:|")
        for t in ts:
            decisoes, taxa = ia_enviou_igual(contagens.get(t, {}))
            ia_txt = f"{round(100 * taxa)}% ({decisoes})" if taxa is not None else "—"
            partes.append(
                f"| {link_tipo(t)} | {len(por_tipo[t])} | "
                f"{int(contagens.get(t, {}).get('recentes', 0))} | {ia_txt} | {_regua(t)} |"
            )
    partes.append("\n## Lojas\n")
    partes += [
        f"- {link_loja(p, loja)}: {len(rs)}"
        for (p, loja), rs in sorted(por_loja.items(), key=lambda x: -len(x[1]))
    ]
    partes.append("\n## Modelos\n")
    partes += [
        f"- {link_modelo(m)}: {len(rs)}"
        for m, rs in sorted(por_modelo.items(), key=lambda x: -len(x[1]))
    ]
    saida[f"{PASTA}/Início.md"] = "\n".join(partes) + "\n"

    saida[f"{PASTA}/_formato.md"] = "\n".join(
        [
            _cabecalho({"formato": FORMATO}),
            "# Formato das notas (versão 1)\n",
            aviso,
            "Cada resposta é uma nota em `Respostas/<tipo>/<id>.md`. O cabeçalho é o que o "
            "DaVinci lê se precisar reimportar (só para recuperação: quem edita é o DaVinci).\n",
            "| Campo | O que é |",
            "|---|---|",
            "| id | identificador fixo da resposta (o mesmo em toda cópia) |",
            "| tipo / tipo_nome | o assunto da pergunta (um dos assuntos do agente) |",
            "| plataforma / canal | shopee, ml, tiktok, amazon… / chat, pergunta, pos_venda… |",
            "| loja | a loja onde foi respondida |",
            "| produto | os códigos de modelo do anúncio (vazio quando não dá para saber) |",
            "| anuncio | o id do anúncio na plataforma |",
            "| status | ativa (só as ativas vêm para a cópia) |",
            "| fonte | equipe_fora (Duoke/Seller Center), equipe_escreveu ou equipe_ajustou "
            "(no DaVinci) |",
            "| perguntado_em / respondido_em | datas (as recentes pesam mais) |",
            "| cliente_depois | o que o cliente fez em até 72 h: "
            + ", ".join(f"{k} = {v}" for k, v in DEPOIS_ROTULO.items())
            + " |",
            "| formato | versão deste formato |",
            "\nO corpo tem a pergunta do cliente (mascarada) e a resposta da equipe, mais os links "
            "para o tipo, a loja e o modelo.\n",
        ]
    )
    saida[f"{PASTA}/{MARCA}"] = agora.astimezone(FUSO).isoformat()
    return saida


def zip_de(conteudo: Mapping[str, str]) -> bytes:
    """O zip da pasta (UTF-8, comprimido). PURA."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for caminho in sorted(conteudo):
            z.writestr(caminho, conteudo[caminho].encode("utf-8"))
    return buf.getvalue()


async def lojas_no_automatico(session: AsyncSession) -> dict[str, list[str]]:
    """Tipo → as lojas em que ele sai sozinho hoje (canal no Automático com o assunto liberado)."""
    from app.models import AtendimentoCanal, Integration

    linhas = (
        await session.execute(
            select(AtendimentoCanal.plataforma, Integration.name, AtendimentoCanal.auto_categorias)
            .join(Integration, Integration.id == AtendimentoCanal.integration_id)
            .where(AtendimentoCanal.modo == "auto")
        )
    ).all()
    saida: dict[str, set[str]] = defaultdict(set)
    for plataforma, nome, categorias in linhas:
        for tipo in _lista(categorias):
            saida[tipo].add(f"{(nome or '').strip()} ({_plat(plataforma)})")
    return {t: sorted(lojas) for t, lojas in saida.items()}


def _lista(valor: Any) -> Iterable[str]:
    return [str(v) for v in valor] if isinstance(valor, list | tuple) else []
