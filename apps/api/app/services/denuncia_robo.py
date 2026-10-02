"""Aba Robô de Ouvidoria › Denúncia: o resumo do robô do mini, mastigado.

Vinicius, 01/10/2026: "como eu vou saber em que passo ele está, o que está
fazendo". O `status_mac.py` do robô (Mac mini da Makisa) monta a cada 60 s um
resumo com uma lista de `itens` ({chave, estado, detalhe, o_que_fazer,
dados}) — agente, filas M/S/E, uma `tarefa_*` por gatilho de hoje, contas,
problemas — e o mini manda pra cá. Aqui vira o que a tela mostra:

- **agenda** (02/10): cada passo tem chave liga/desliga e horários (tabela
  `denuncia_robo_agenda`, o despertador do mini segue); passo ligado que não
  começou 20 min depois do horário vira ocorrência — o alarme do que
  aconteceu em 30/09 18h e 01/10 06h (ninguém pediu e ninguém viu). Até 02/10
  eram rodadas fixas 06/12/18h com todos os passos juntos;
- **frentes**: o que cada fila do robô está fazendo agora (navegador do
  perfil 50, Anatel/SEI no Safari, escritório);
- **passos** (01/10, desenho da Ouvidoria › Robôs): os passos com onde
  rodam, o que fazem e a última vez de hoje (status, progresso, fim do log);
- **ocorrências**: o que só uma pessoa resolve (tipo "pessoa": captcha,
  assinatura no SEI, código que não chegou, robô parado, mini sem notícia) e
  os avisos. As de origem "agora" somem sozinhas; as do robô (PROBLEMAS.jsonl)
  a pessoa marca "Tratado" (`tratadas`).

Função pura (sem banco) pra ser testada com o resumo de verdade.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

FUSO = ZoneInfo("America/Sao_Paulo")
# passo ligado sem tarefa este tempo depois do horário da agenda = não começou
TOLERANCIA_INICIO = timedelta(minutes=20)
# o mini manda a cada 60 s; passou disso, ele (ou a janela do sistema) parou
SEM_NOTICIA = timedelta(minutes=5)

# passo → (ordem na tela, nome, onde roda, o que faz)
# 02/10 (Vinicius, agente v27 no mini): os passos 2 a 6 viraram dois — 2 procura nos
# quatro sites e 3 denúncias (prints, resultados da Shopee, recusadas de novo e novas).
PASSOS: dict[str, tuple[int, str, str, str]] = {
    "checagem": (0, "Checagem antes da rodada", "perfil 50 + Safari",
                 "confere logins, AdsPower, captcha, Safari e Tuta — não muda nada"),
    # 01/10: o robô lê o Tuta sozinho (o leitor do Claude do mini parou em 17/09).
    "ciclo_emails": (1, "E-mails das plataformas", "escritório + Tuta",
                     "lê no Tuta as respostas das plataformas e aplica nas denúncias"),
    "procura": (2, "Procurar anúncios novos", "perfil 50",
                "Mercado Livre → Shopee → TikTok → Amazon, salva no sistema e confere no "
                "UpSeller quais lojas são nossas"),
    # 01/10 (Vinicius): nas lojas o robô denuncia só o "Nosso", pela conta da MAKISA
    # (config.json → denunciar_nas_lojas no mini); o "Diversos" só é salvo. 02/10: até 40
    # prints por rodada — 1º recusadas do Nosso, 2º os da Anatel, 3º o resto.
    "denuncias": (3, "Denúncias", "perfil 50 + celular",
                  "prints (até 40, Nosso primeiro) → resultados da Shopee → recusadas de novo "
                  "→ novas (ML, Shopee e TikTok; só o Nosso; Amazon ainda não)"),
    "anatel": (4, "Anatel / SEI", "Safari",
               "lê o andamento das antigas e peticiona as novas no SEI "
               "— só depois dos passos 2 e 3"),
    "compras": (5, "Compras de prova", "perfil 50", "atualiza os pedidos da conta compradora"),
    "juridico": (6, "Jurídico", "escritório", "monta a pasta do caso pro advogado (não envia)"),
    # 02/10 (Vinicius): o 7 (Relatório) saiu da rotina — ninguém lia; o programa fica no mini
    "capa_perguntas": (8, "Perguntas nos anúncios disfarçados", "perfil 50",
                       "pergunta ao vendedor de capa/tablet se vende o aparelho"),
    # 01/10 (Vinicius): o "saiu do ar?" levava 3–4 h e virou o último passo, feito com o
    # robô parado (para quando a rodada chega e continua depois).
    "ativos_inativos": (9, "Conferência de anúncios ativos/inativos", "perfil 50",
                        "abre um por um os anúncios que acompanhamos e vê quem saiu do ar; "
                        "roda com o robô parado, para se a rodada chegar e continua depois"),
}
# passos de antes de 02/10: só pra dar nome às rodadas que ainda os têm (não têm botão)
ANTIGOS: dict[str, tuple[int, str]] = {
    "varredura_mercadolivre": (2, "Mercado Livre (antigo)"),
    "varredura_shopee": (2, "Shopee (antigo)"),
    "varredura_tiktok": (2, "TikTok (antigo)"),
    "varredura_amazon": (2, "Amazon (antigo)"),
    "conferencia": (3, "Conferência e recusadas (antigo)"),
    "relatorio": (7, "Relatório (fora da rotina)"),
}


def _ordem_nome(acao: str, nome: str | None) -> tuple[int, str]:
    if acao in PASSOS:
        return PASSOS[acao][0], PASSOS[acao][1]
    if acao in ANTIGOS:
        return ANTIGOS[acao]
    return 50, nome or acao.replace("_", " ")
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


HORARIO = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


def normalizar_horarios(valor: Any) -> list[str] | None:
    """Lista de "HH:MM" sem repetir, em ordem; None se algum não for horário."""
    if not isinstance(valor, list):
        return None
    out = set()
    for h in valor:
        if not isinstance(h, str) or not HORARIO.match(h.strip()):
            return None
        out.add(h.strip())
    return sorted(out)


def _atendido(tarefas: list[dict], acao: str, desde: datetime, ate: datetime | None) -> bool:
    """O horário [desde, ate) da agenda foi atendido: o robô pediu ou começou o passo nesse
    intervalo, ou ele já estava rodando/na fila (o despertador não duplica)."""
    for t in tarefas:
        d = t.get("dados") or {}
        if str(d.get("acao") or "") != acao:
            continue
        pedido, inicio, fim = (_quando(d.get(k)) for k in ("pedido_em", "inicio", "fim"))
        for q in (pedido, inicio):
            if q and q >= desde and (ate is None or q < ate):
                return True
        comeco = inicio or pedido
        seguia = d.get("status") in ("rodando", "fila") or (fim and fim >= desde)
        if comeco and comeco < desde and seguia:
            return True
    return False


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
    tratadas: set[str] | None = None, agenda: dict[str, dict] | None = None,
) -> dict:
    """`agenda`: acao → {"ligado", "horarios"} da tabela denuncia_robo_agenda."""
    agora = agora.astimezone(FUSO)
    resumo = resumo or {}
    tratadas = tratadas or set()
    agenda = agenda or {}
    itens = {i.get("chave"): i for i in resumo.get("itens") or [] if isinstance(i, dict)}
    tarefas = [i for k, i in itens.items() if str(k).startswith("tarefa_")]
    quando = _quando(resumo.get("quando"))
    # 01/10 (Vinicius: "disparamos o passo 1, acompanhamos… depois o passo 2"):
    # despertador desligado = modo manual; o mini manda o despertador.json junto
    # (02/10: com a agenda que ele está seguindo — None = agente antigo, sem agenda).
    desp = resumo.get("despertador") or {}
    manual = desp.get("ligado") is False
    agenda_no_robo = desp.get("agenda") if isinstance(desp.get("agenda"), dict) else None
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
        return _ordem_nome(str(x.get("acao") or ""), x.get("nome"))[1]

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

    # 02/10: passo ligado na agenda que não foi pedido 20 min depois do horário (só com a
    # rotina automática ligada e o mini dando notícia — senão o alarme é outro)
    hoje = agora.date()
    if not manual and conectado:
        for acao, (ordem, nome, _onde, _faz) in PASSOS.items():
            a = agenda.get(acao) or {}
            if not a.get("ligado"):
                continue
            horas = sorted(
                datetime(hoje.year, hoje.month, hoje.day, int(m[1]), int(m[2]), tzinfo=FUSO)
                for m in (HORARIO.match(str(h)) for h in a.get("horarios") or []) if m
            )
            for i, hora in enumerate(horas):
                h = hora.strftime("%H:%M")
                ate = horas[i + 1] - timedelta(minutes=5) if i + 1 < len(horas) else None
                if agora < hora + TOLERANCIA_INICIO:
                    continue
                if _atendido(tarefas, acao, hora - timedelta(minutes=5), ate):
                    continue
                ocorre(f"agora:agenda_{acao}_{h.replace(':', '')}",
                       f"{ordem} · {nome} das {h} não começou",
                       "O despertador do robô não pediu este passo no horário da agenda.",
                       'Conferir se o "6 - Agente da varredura" está aberto no Mac mini',
                       _iso(hora))

    ultimas = _ultimas_de_hoje(tarefas)
    def _agenda(acao: str) -> dict:
        a = agenda.get(acao) or {}
        ag = {"ligado": bool(a.get("ligado")), "horarios": list(a.get("horarios") or [])}
        # o robô já está seguindo esta agenda? (None = agente antigo, que não manda a dele)
        r = (agenda_no_robo or {}).get(acao) or {"ligado": False, "horarios": []}
        igual = (bool(r.get("ligado")) == ag["ligado"]
                 and sorted(r.get("horarios") or []) == ag["horarios"])
        ag["no_robo"] = None if agenda_no_robo is None else igual
        return ag

    passos = [
        {"acao": acao, "ordem": ordem, "nome": nome, "onde": onde, "faz": faz,
         "ultima": ultimas.get(acao), "agenda": _agenda(acao)}
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
        "denuncias_hoje": denuncias_hoje,
    }
