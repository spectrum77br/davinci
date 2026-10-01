"""Aba Robô de Ouvidoria › Denúncia: o resumo do robô do mini, mastigado.

Vinicius, 01/10/2026: "como eu vou saber em que passo ele está, o que está
fazendo". O `status_mac.py` do robô (Mac mini da Makisa) monta a cada 60 s um
resumo com uma lista de `itens` ({chave, estado, detalhe, o_que_fazer,
dados}) — agente, filas M/S/E, uma `tarefa_*` por gatilho de hoje, contas,
problemas — e o mini manda pra cá. Aqui vira o que a tela mostra:

- **rodadas** de hoje (06h, 12h, 18h), cada uma com os passos e o estado
  (feita, rodando, com erro, não começou…). "Não começou" é o alarme do que
  aconteceu em 30/09 18h e 01/10 06h: ninguém pediu a rodada e ninguém viu;
- **frentes**: o que cada fila do robô está fazendo agora (navegador do
  perfil 50, Anatel/SEI no Safari, escritório);
- **passos** (01/10, desenho da Ouvidoria › Robôs): os 12 passos com onde
  rodam, o que fazem e a última vez de hoje (status, progresso, fim do log);
- **ocorrências**: o que só uma pessoa resolve (tipo "pessoa": captcha,
  assinatura no SEI, código que não chegou, robô parado, mini sem notícia) e
  os avisos. As de origem "agora" somem sozinhas; as do robô (PROBLEMAS.jsonl)
  a pessoa marca "Tratado" (`tratadas`).

Função pura (sem banco) pra ser testada com o resumo de verdade.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

FUSO = ZoneInfo("America/Sao_Paulo")
RODADAS = (6, 12, 18)
CHECAGEM_ANTES = timedelta(minutes=15)
# rodada sem nenhuma tarefa este tempo depois da hora cheia = não começou
TOLERANCIA_INICIO = timedelta(minutes=20)
# o mini manda a cada 60 s; passou disso, ele (ou a janela do sistema) parou
SEM_NOTICIA = timedelta(minutes=5)
# passos que só existem quando a rodada foi pedida. E-mails e conferência
# também rodam pelo relógio do robô (a cada 3 h) — sozinhos não são rodada:
# em 01/10 06h eles rodaram e nenhuma varredura foi pedida.
NUCLEO = {
    "checagem", "varredura_mercadolivre", "varredura_shopee", "varredura_tiktok",
    "varredura_amazon", "anatel",
}

# passo → (ordem na tela, nome, onde roda, o que faz)
PASSOS: dict[str, tuple[int, str, str, str]] = {
    "checagem": (0, "Checagem antes da rodada", "perfil 50 + Safari",
                 "confere logins, AdsPower, captcha, Safari e Tuta — não muda nada"),
    "ciclo_emails": (1, "E-mails das plataformas", "escritório",
                     "aplica nas denúncias as respostas que as plataformas mandaram"),
    "varredura_mercadolivre": (2, "Mercado Livre", "perfil 50",
                               "procura anúncios novos e denuncia (Nosso e Diversos)"),
    "varredura_shopee": (3, "Shopee", "perfil 50",
                         "procura anúncios novos e denuncia (Nosso e Diversos)"),
    "varredura_tiktok": (4, "TikTok", "perfil 50 + celular",
                         "procura no site; denuncia pelo app no celular na nuvem"),
    "varredura_amazon": (5, "Amazon", "perfil 50", "procura e registra (ainda não denuncia)"),
    "conferencia": (6, 'Conferência e "saiu do ar?"', "perfil 50",
                    "print no ato, resultados da Shopee, refação e quem saiu do ar"),
    "anatel": (7, "Anatel / SEI", "Safari",
               "lê o andamento das antigas e peticiona as novas no SEI"),
    "compras": (8, "Compras de prova", "perfil 50", "atualiza os pedidos da conta compradora"),
    "juridico": (9, "Jurídico", "escritório", "monta a pasta do caso pro advogado (não envia)"),
    "relatorio": (10, "Relatório", "escritório", "denunciados × responderam × resolvidos"),
    "capa_perguntas": (11, "Perguntas nos anúncios disfarçados", "perfil 50",
                       "pergunta ao vendedor de capa/tablet se vende o aparelho"),
}
FRENTES = (
    ("M", "Navegador (perfil 50)", "Mercado Livre, Shopee, TikTok, Amazon e conferências"),
    ("S", "Anatel / SEI (Safari)", "denúncias à Anatel e leitura das antigas"),
    ("E", "Escritório", "e-mails, jurídico e relatório"),
)
# itens do resumo que, com estado "erro", só uma pessoa resolve
CONTAS = {
    "adspower": "AdsPower",
    "perfil_50": "Perfil 50 do AdsPower",
    "tuta": "App Tuta",
    "fiscalizacao": "Sistema de fiscalização",
    "conta_ml": "Conta do Mercado Livre",
    "conta_shopee": "Conta da Shopee",
    "conta_tiktok": "Conta do TikTok",
    "conta_amazon": "Conta da Amazon",
    "conta_upseller": "UpSeller",
    "sessao_anatel": "Anatel Consumidor",
    "sessao_sei": "SEI da Anatel",
    "disco": "Disco do Mac mini",
    "anticaptcha": "Anticaptcha",
    "pausa": "Pausa",
}
CANAIS_HOJE = {
    "ml": "Mercado Livre",
    "shopee": "Shopee",
    "tiktok": "TikTok",
    "anatel_sei": "Anatel (SEI)",
    "anatel_consumidor": "Anatel Consumidor",
}


def _quando(v: Any) -> datetime | None:
    """ISO do mini ("2026-10-01T10:14:32-03:00" ou sem fuso = Brasília)."""
    if not v:
        return None
    try:
        d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=FUSO)


def _iso(d: datetime | None) -> str | None:
    return d.isoformat(timespec="seconds") if d else None


def _passos_da_rodada(tarefas: list[dict], janela: str) -> list[dict]:
    """Um passo por ação. A mesma ação pedida de novo na janela (retomada à
    mão, `_r1609`) conta como tentativa — vale a mais nova."""
    por_acao: dict[str, dict] = {}
    for t in tarefas:
        d = t.get("dados") or {}
        if d.get("janela") != janela:
            continue
        acao = str(d.get("acao") or "")
        pedido = str(d.get("pedido_em") or "")
        ja = por_acao.get(acao)
        tentativas = (ja["tentativas"] + 1) if ja else 1
        if ja and pedido < ja["_pedido"]:
            ja["tentativas"] = tentativas
            continue
        ordem, nome = PASSOS.get(acao, (50, d.get("nome") or acao.replace("_", " "), "", ""))[:2]
        por_acao[acao] = {
            "acao": acao,
            "nome": nome,
            "ordem": ordem,
            "status": d.get("status") or "fila",
            "inicio": d.get("inicio"),
            "fim": d.get("fim"),
            "progresso": d.get("progresso") or "",
            "erro": d.get("erro") or "",
            "log": (d.get("log") or "")[-3000:] if d.get("status") in ("rodando", "erro") else "",
            "tentativas": tentativas,
            "_pedido": pedido,
        }
    passos = sorted(por_acao.values(), key=lambda p: (p["ordem"], p["_pedido"]))
    for p in passos:
        p.pop("_pedido")
    return passos


def _estado_rodada(passos: list[dict], hora: datetime, agora: datetime, manual: bool) -> str:
    if any(p["acao"] in NUCLEO for p in passos):
        st = {p["status"] for p in passos}
        if "rodando" in st:
            return "rodando"
        if "fila" in st:
            return "na_fila"
        return "com_erro" if "erro" in st else "feita"
    if manual:
        return "manual"   # ninguém pede sozinho: rodada sem passo não é alarme
    if agora < hora - CHECAGEM_ANTES:
        return "futura"
    if agora < hora + TOLERANCIA_INICIO:
        return "aguardando"
    return "nao_comecou"


def _ultimas_de_hoje(tarefas: list[dict]) -> dict[str, dict]:
    """Última vez de cada passo hoje (qualquer janela) + quantas vezes rodou."""
    por_acao: dict[str, dict] = {}
    for t in tarefas:
        d = t.get("dados") or {}
        acao = str(d.get("acao") or "")
        pedido = str(d.get("pedido_em") or "")
        ja = por_acao.get(acao)
        vezes = (ja["vezes"] + 1) if ja else 1
        if ja and pedido < ja["_pedido"]:
            ja["vezes"] = vezes
            continue
        por_acao[acao] = {
            "status": d.get("status") or "fila",
            "inicio": d.get("inicio"),
            "fim": d.get("fim"),
            "progresso": d.get("progresso") or "",
            "erro": d.get("erro") or "",
            "log": (d.get("log") or "")[-4000:],
            "vezes": vezes,
            "_pedido": pedido,
        }
    for v in por_acao.values():
        v.pop("_pedido")
    return por_acao


def _chave_problema(p: dict) -> tuple[str, str | None]:
    """Chave estável da ocorrência que veio do robô (_canal/PROBLEMAS.jsonl):
    a dele, quando tem (aí o "Tratado" também resolve lá no mini), senão um
    hash de quando + tarefa + problema."""
    if p.get("chave"):
        return "prob:" + str(p["chave"]), str(p["chave"])
    base = "|".join(str(p.get(k) or "") for k in ("quando", "tarefa", "problema"))
    return "prob:" + hashlib.sha256(base.encode()).hexdigest()[:16], None


def montar_painel(
    resumo: dict | None, recebido_em: datetime | None, agora: datetime,
    tratadas: set[str] | None = None,
) -> dict:
    agora = agora.astimezone(FUSO)
    resumo = resumo or {}
    tratadas = tratadas or set()
    itens = {i.get("chave"): i for i in resumo.get("itens") or [] if isinstance(i, dict)}
    tarefas = [i for k, i in itens.items() if str(k).startswith("tarefa_")]
    quando = _quando(resumo.get("quando"))
    # 01/10 (Vinicius: "disparamos o passo 1, acompanhamos… depois o passo 2"):
    # despertador desligado = modo manual; o mini manda o despertador.json junto.
    manual = (resumo.get("despertador") or {}).get("ligado") is False
    recebido = recebido_em.astimezone(FUSO) if recebido_em else None
    conectado = bool(recebido and agora - recebido <= SEM_NOTICIA)

    # Ocorrências (como em Ouvidoria › Robôs): "agora" = estado do momento,
    # some sozinha quando o problema some; "robo" = o que o robô registrou em
    # PROBLEMAS.jsonl (fica 24 h lá) — a pessoa marca como tratada.
    ocorrencias: list[dict] = []

    def ocorre(chave: str, titulo: str, detalhe: str = "", fazer: str = "", quando_: Any = None,
               tipo: str = "pessoa", origem: str = "agora", robo_chave: str | None = None) -> None:
        if chave in tratadas:
            return
        ocorrencias.append({
            "chave": chave, "titulo": titulo, "detalhe": detalhe, "o_que_fazer": fazer,
            "quando": quando_, "tipo": tipo, "origem": origem, "robo_chave": robo_chave,
        })

    if not recebido:
        ocorre("agora:mini", "O Mac mini nunca mandou o estado do robô",
               fazer='Conferir no mini se a janela "00 - Sistema no Mac mini" está aberta')
    elif not conectado:
        minutos = int((agora - recebido).total_seconds() // 60)
        ocorre("agora:mini", f"O Mac mini não dá notícia há {minutos} min",
               "Mini desligado, sem internet ou a janela do sistema fechada.",
               'Conferir o Mac mini e a janela "00 - Sistema no Mac mini"', _iso(recebido))

    ag = itens.get("agente") or {}
    ag_dados = ag.get("dados") or {}
    if ag.get("estado") == "erro":
        ocorre("agora:agente", "Robô parado", ag.get("detalhe") or "", ag.get("o_que_fazer") or "")

    def nome_passo(x: dict) -> str:
        return PASSOS.get(x.get("acao"), (0, x.get("nome")))[1]

    frentes = []
    for fila, nome, faz in FRENTES:
        it = itens.get(f"fila_{fila}") or {}
        d = it.get("dados") or {}
        rod = d.get("rodando") or None
        frentes.append({
            "fila": fila,
            "nome": nome,
            "faz": faz,
            "estado": it.get("estado") or "desconhecido",
            "fazendo": nome_passo(rod) if rod else None,
            "acao": rod.get("acao") if rod else None,
            "desde": rod.get("desde") if rod else None,
            "progresso": (rod or {}).get("progresso") or "",
            "n_proximos": d.get("n_proximos") or 0,
            "proximos": [nome_passo(p) for p in (d.get("proximos") or [])][:5],
            "presa": bool(d.get("presa")),
        })
        if it.get("estado") == "erro":
            ocorre(f"agora:fila_{fila}", f"{nome}: {it.get('detalhe') or 'com erro'}", "",
                   it.get("o_que_fazer") or "")
    shopee = itens.get("fila_shopee") or {}
    if shopee.get("estado") == "erro":
        ocorre("agora:muro_shopee", shopee.get("detalhe") or "Shopee com verificação", "",
               shopee.get("o_que_fazer") or "")

    sei = itens.get("sei_assinatura") or {}
    if sei.get("estado") not in (None, "ok"):
        ocorre("agora:sei", "SEI esperando a assinatura da titular", sei.get("detalhe") or "",
               sei.get("o_que_fazer") or "Assinar no Safari do Mac mini (senha SEI da titular)")

    for chave, nome in CONTAS.items():
        it = itens.get(chave) or {}
        if it.get("estado") in ("erro", "atencao"):
            ocorre(f"agora:{chave}", nome, it.get("detalhe") or "", it.get("o_que_fazer") or "",
                   tipo="pessoa" if it.get("estado") == "erro" else "aviso")

    for p in ((itens.get("problemas") or {}).get("dados") or {}).get("lista") or []:
        chave, robo_chave = _chave_problema(p)
        ocorre(chave, p.get("tarefa") or "Problema", p.get("problema") or "",
               p.get("pergunta") or "", p.get("quando"),
               tipo="pessoa" if p.get("bloqueia") else "aviso", origem="robo",
               robo_chave=robo_chave)

    hoje = agora.date()
    rodadas = []
    for h in RODADAS:
        hora = datetime(hoje.year, hoje.month, hoje.day, h, tzinfo=FUSO)
        janela = f"{hoje.isoformat()}_{h:02d}h"
        passos_rodada = _passos_da_rodada(tarefas, janela)
        estado = _estado_rodada(passos_rodada, hora, agora, manual)
        if estado == "nao_comecou":
            ocorre(f"agora:rodada_{janela}", f"A rodada das {h:02d}h não começou",
                   "Nenhuma varredura foi pedida para esta rodada.",
                   'Conferir se o "6 - Agente da varredura" está aberto no Mac mini', janela)
        rodadas.append({"hora": h, "janela": janela, "estado": estado, "passos": passos_rodada})

    ultimas = _ultimas_de_hoje(tarefas)
    passos = [
        {"acao": acao, "ordem": ordem, "nome": nome, "onde": onde, "faz": faz,
         "ultima": ultimas.get(acao)}
        for acao, (ordem, nome, onde, faz) in sorted(PASSOS.items(), key=lambda x: x[1][0])
    ]

    # quem precisa de gente primeiro; dentro do tipo, as do momento na ordem
    # em que entraram (mini sem notícia e robô parado no topo) e depois as do
    # robô, da mais nova pra mais velha
    agora_ = [o for o in ocorrencias if o["origem"] == "agora"]
    do_robo = sorted((o for o in ocorrencias if o["origem"] != "agora"),
                     key=lambda o: str(o["quando"] or ""), reverse=True)
    ocorrencias = sorted(agora_ + do_robo, key=lambda o: o["tipo"] != "pessoa")

    canais = ((itens.get("denuncias_hoje") or {}).get("dados") or {}).get("canais") or {}
    denuncias_hoje = [
        {"canal": nome, "enviadas": int((canais.get(k) or {}).get("enviadas") or 0),
         "refeitas": int((canais.get(k) or {}).get("refeitas") or 0)}
        for k, nome in CANAIS_HOJE.items()
        if k in canais
    ]

    return {
        "modo": "manual" if manual else "automatico",
        "recebido_em": _iso(recebido),
        "quando": _iso(quando),
        "conectado": conectado,
        "agente": {
            "estado": ag.get("estado") or "desconhecido",
            "detalhe": ag.get("detalhe") or "",
            "versao": ag_dados.get("versao"),
            "desde": ag_dados.get("desde"),
        },
        "frentes": frentes,
        "passos": passos,
        "ocorrencias": ocorrencias,
        "rodadas": rodadas,
        "denuncias_hoje": denuncias_hoje,
    }
