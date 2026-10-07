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
# 07/10/2026 (Vinicius: "mexemos e mexemos e até agora não entendo o que cada passo faz"; "Passos" remodelado a
# partir do mapa do processo): numeração nova — 0 Checagem · 1 Conferência · 2 Procura · 3 Nossos · 4 Diversos ·
# 5 Prints · 6 Anatel · 7 Réplica · 8 Ativos/inativos · 9 Jurídico. O "Tempo parado" saiu (ele: "pode excluir; quando
# ver necessidade criamos novamente") — fora daqui ele também sai da agenda do mini (só vão os passos desta lista).
# A chave de cada passo NÃO muda (agenda e "Rodar" de antes valem). Histórico até 06/10: 02/10 a procura e as
# denúncias viraram passos próprios; 03/10 o Nosso parou nas lojas; 05/10 o passo 1 virou a conferência inteira
# (e-mails + Shopee no perfil 50 + Anatel/SEI) e entrou a Réplica.
PASSOS: dict[str, tuple[int, str, str, str]] = {
    "checagem": (0, "Checagem antes da rodada", "perfil 50 + 148 + contas das lojas + Safari + Tuta",
                 "confere logins, perfis, captcha, Safari e Tuta antes da rodada — não muda nada"),
    "ciclo_emails": (1, "Conferência das respostas", "Tuta + Safari (SEI) + perfil 50",
                     "lê as respostas das denúncias (e-mails no Tuta) e o andamento dos processos da Anatel no SEI"),
    "procura": (2, "Procurar anúncios novos", "perfil 50 + 148 + celular + Safari",
                "procura as marcas no Mercado Livre, Shopee, TikTok e Amazon e salva no sistema"),
    "denuncias": (3, "Denúncias Nossos", "perfil 50 + celular",
                  "denunciava na loja o anúncio com o NOSSO certificado — desligado desde 03/10"),
    "diversos": (4, "Denúncias Diversos", "perfil 50 + 148 (Shopee) + celular",
                 "denuncia uma vez na loja o anúncio com certificado de outra empresa ou nº que não é do aparelho"),
    "prints": (5, "Prints", "perfil 148 (Shopee) + perfil 50 (ML)",
               "tira o print da página de cada anúncio que vai à Anatel"),
    "anatel": (6, "Denúncias Anatel", "Safari (SEI) + Tuta",
               "peticiona no SEI, uma petição por loja, com o print e as provas de cada anúncio"),
    "replica_diversos": (7, "Réplica Denúncias Diversos", "contas das lojas (ML 93/78 · Shopee 155/146)",
                         "denuncia de novo, com a conta de outra empresa, o Diversos que a loja recusou e já está na Anatel"),
    "ativos_inativos": (8, "Ativos/inativos", "perfil 148 (Shopee) + perfil 50",
                        "abre os anúncios denunciados e vê quem saiu do ar"),
    "juridico": (9, "Jurídico", "escritório", "monta a pasta do caso para o advogado — em criação"),
}
# passos que estão sendo refeitos (Jurídico): a tela mostra "em criação" e não deixa ligar nem rodar. Prints saiu
# daqui em 07/10 (agente v36 tem a ação "prints").
EM_CRIACAO = {"juridico"}
# 07/10/2026: o que a tela mostra ao abrir o passo — o passo a passo, quem faz (cada perfil pelo nome), quem confere
# depois e o que ainda está sendo consertado. Texto do mapa do processo, conferido no robô em 07/10.
_LOJAS = "Contas das lojas da Réplica: ML 93 Mega · ML 78 KIA · Shopee 155 Aguiar · Shopee 146 Vortan · TikTok 137 Atv · TikTok 131 Barbosa"
EXPLICACAO: dict[str, dict[str, list[str]]] = {
    "checagem": {
        "passos": [
            "Abre o perfil 50 e confere se Mercado Livre, Shopee, TikTok e Amazon estão logados.",
            "Abre o perfil 148 e faz uma busca de teste na Shopee.",
            "Confere se as contas das lojas da Réplica estão logadas (a Shopee só na 1ª checagem do dia, para não pedir captcha).",
            "No Safari confere UpSeller, gov.br e SEI; confere se o Tuta está recebendo.",
            "Não muda nada: o que estiver errado vira Ocorrência e aviso no Threema.",
        ],
        "quem": ["Perfil 50 (MAKISA)", "Perfil 148 (Luno – Shopee)", _LOJAS, "Safari do Mac mini", "Tuta (sac@makisa)"],
        "confere": ["É a própria conferência: os avisos vão para Ocorrências e para o Threema."],
        "consertando": [],
    },
    "ciclo_emails": {
        "passos": [
            "Lê a caixa sac@makisa no Tuta: respostas do Mercado Livre (inclusive as da Réplica, encaminhadas pelas lojas) e da Shopee.",
            "O sistema transforma cada e-mail em resultado da denúncia: recusada, removida ou em análise.",
            "No Safari lê o andamento de cada processo no SEI (60 por vez) e confere se os anexos que mandamos estão no processo.",
            "Lê os protocolos antigos do Anatel Consumidor (as duas páginas da lista, 15 protocolos).",
            "Não abre mais os avisos da Shopee no perfil 50: o aviso não diz qual anúncio foi recusado.",
        ],
        "quem": ["Tuta (sac@makisa)", "Safari: SEI com a conta do advogado e gov.br"],
        "confere": [
            "Denúncias do Mercado Livre (passo 4) e da Réplica no ML (passo 7) — pelo e-mail.",
            "Processos da Anatel (passo 6) — andamento e anexos no SEI.",
            "TikTok: ainda ninguém lê as respostas — vai entrar a leitura pelo app do celular.",
        ],
        "consertando": [
            "TikTok: ler as respostas pelo app do celular.",
            "Réplica no ML: nenhum e-mail encaminhado da Mega e da KIA chegou no sac@makisa desde 05/10.",
        ],
    },
    "procura": {
        "passos": [
            "Mercado Livre no perfil 50: busca hotwav, oukitel, fossibot, uranyx e oscal.",
            "Shopee: o perfil 50 busca hotwav e uranyx e o 148 busca oukitel, fossibot e oscal, ao mesmo tempo.",
            "TikTok pelo app no celular e pelo site no perfil 50.",
            "Amazon no perfil 50 (só procura — a Amazon não é denunciada por enquanto).",
            "Salva no sistema depois de cada site e confere no UpSeller (Safari) quais lojas são nossas.",
        ],
        "quem": ["Perfil 50 (MAKISA)", "Perfil 148 (Luno – Shopee)", "Celular na nuvem (app do TikTok)", "Safari (UpSeller)"],
        "confere": ["Ainda ninguém confere se a procura cobriu todos os sites (\"ok\" = o programa terminou)."],
        "consertando": [
            "O 148 falha quando o passo 8 ainda está com ele, e o 50 refaz a parte dele.",
        ],
    },
    "denuncias": {
        "passos": [
            "Pegava os anúncios que usam o NOSSO certificado e denunciava na loja (Mercado Livre, Shopee, TikTok).",
            "Denunciava de novo as que a loja recusou (a \"refação\").",
            "Desligado desde 03/10: desde 06/10 o Nosso vai direto à Anatel (passo 6), sem passar pela loja.",
        ],
        "quem": ["Perfil 50 (MAKISA)", "Celular na nuvem (TikTok)"],
        "confere": ["Desligado. As respostas antigas do ML ainda chegam pelo e-mail (passo 1)."],
        "consertando": [],
    },
    "diversos": {
        "passos": [
            "Pega os anúncios Diversos (certificado de outra empresa, ou nº que não é do aparelho) ainda não denunciados na loja.",
            "Denuncia uma vez: Mercado Livre no perfil 50 (até 25 por hora); Shopee no perfil 148, com 2,5 a 5 min entre uma denúncia e outra; TikTok pelo app do celular.",
            "Grava cada denúncia com o print da tela de denúncia.",
        ],
        "quem": ["Perfil 50 (MAKISA) — Mercado Livre", "Perfil 148 (Luno – Shopee) — Shopee", "Celular na nuvem — TikTok"],
        "confere": [
            "Mercado Livre: a resposta chega por e-mail (passo 1).",
            "Shopee: o aviso não diz qual anúncio foi recusado — só o \"saiu do ar\" (passo 8) vale.",
            "TikTok: ninguém lê ainda (vai entrar no passo 1, pelo app).",
            "Saiu do ar: passo 8.",
        ],
        "consertando": ["Sem horário por enquanto: rodar à mão, medir o tempo e depois montar uma vez por dia."],
    },
    "prints": {
        "passos": [
            "Tira o print da página de cada anúncio que vai à Anatel e ainda não tem print que valha.",
            "Só vale print da página do próprio anúncio, sem captcha, com a cópia da página, de até 15 dias.",
            "Captcha na tela: não salva, para aquele site e deixa a conta descansar.",
            "Shopee no perfil 148, com 1 a 2 min entre um print e outro; Mercado Livre no perfil 50.",
            "Se a ligação com o navegador cai no meio, reconecta e tenta o mesmo anúncio de novo.",
        ],
        "quem": ["Perfil 148 (Luno – Shopee)", "Perfil 50 (MAKISA) — Mercado Livre"],
        "confere": ["O detector de print confere antes de salvar e de novo antes de anexar na Anatel."],
        "consertando": ["Sem horário por enquanto: rodar à mão e medir o tempo."],
    },
    "anatel": {
        "passos": [
            "Entra no SEI com a conta do advogado (o código de acesso chega por e-mail no Tuta).",
            "Faz uma petição por loja: Nosso, Diversos com nº que não é do aparelho, e sem nº (TikTok, Mercado Livre e Shopee).",
            "Cada anúncio vai com o print que vale, a consulta pública do nº e, no Nosso, o nosso certificado. Sem print que valha, fica de fora.",
            "Confere de novo que o anúncio está no ar e guarda o nº do processo e os nomes dos anexos.",
            "Começa só depois que a procura (passo 2) termina.",
        ],
        "quem": ["Safari do Mac mini: SEI com a conta do advogado", "Tuta (código de acesso)", "Gustavo (encaminha o código)"],
        "confere": [
            "Passo 1: lê o andamento de cada processo e confere se os anexos estão lá.",
            "Denúncias enviadas › \"prints\": miniaturas do que foi anexado, para conferir de olho.",
        ],
        "consertando": [
            "35 processos foram com print de captcha (25/09 a 06/10): 11 já consertados por peticionamento intercorrente em 07/10; os outros esperam os prints da Shopee.",
            "Assinatura não confirmada na tela: antes de devolver a loja à fila, confere a lista de recibos (em 05/10 isso virou processo duplicado).",
        ],
    },
    "replica_diversos": {
        "passos": [
            "Pega o anúncio Diversos que a loja recusou e que já tem processo na Anatel.",
            "Denuncia de novo com a conta de outra empresa, citando o processo (grupo da loja; a 2ª réplica sai pelo outro grupo).",
            "Mercado Livre e Shopee; a conta da Shopee abre uma vez por dia. Na Shopee vale o anúncio denunciado há 10 dias ou mais e ainda no ar (a recusa nunca é registrada).",
            "Captcha ou login na conta da loja: o perfil fica aberto para alguém resolver.",
        ],
        "quem": ["Grupo 1: ML 93 Mega · Shopee 155 Aguiar · TikTok 137 Atv (sem uso)",
                 "Grupo 2: ML 78 KIA · Shopee 146 Vortan · TikTok 131 Barbosa (sem uso)"],
        "confere": [
            "Mercado Livre: a resposta vai ao e-mail da loja, encaminhada ao sac@makisa e lida no passo 1.",
            "Shopee: os avisos da conta não dizem qual anúncio foi recusado.",
        ],
        "consertando": [
            "Mega e KIA: nenhuma resposta encaminhada chegou no sac@makisa — conferir o encaminhamento do e-mail das lojas.",
            "Aguiar 155: captcha em toda rodada.",
        ],
    },
    "ativos_inativos": {
        "passos": [
            "Abre um por um os anúncios denunciados e vê quem saiu do ar.",
            "Saiu do ar: a denúncia fecha como \"removido\".",
            "Shopee no perfil 148; Mercado Livre, Amazon e TikTok no perfil 50, ao mesmo tempo.",
            "Para quando a checagem das 05:45 chega e continua depois.",
        ],
        "quem": ["Perfil 148 (Luno – Shopee)", "Perfil 50 (MAKISA)"],
        "confere": ["O sistema usa o resultado para fechar as denúncias de anúncio que saiu do ar."],
        "consertando": [
            "Sem horário por enquanto: rodar à mão e medir o tempo (a checagem das 05:45 cortava o passo).",
            "Mercado Livre pela API não dá (responde 403 para anúncio de outro vendedor) — segue no perfil 50.",
        ],
    },
    "juridico": {
        "passos": ["Monta a pasta do caso para o advogado (não envia).", "Em criação: hoje o pacote é montado à mão quando precisa."],
        "quem": ["Escritório"],
        "confere": [],
        "consertando": [],
    },
}
# passos de antes de 02/10: só pra dar nome às rodadas que ainda os têm (não têm botão)
ANTIGOS: dict[str, tuple[int, str]] = {
    "varredura_mercadolivre": (2, "Mercado Livre (antigo)"),
    "varredura_shopee": (2, "Shopee (antigo)"),
    "varredura_tiktok": (2, "TikTok (antigo)"),
    "varredura_amazon": (2, "Amazon (antigo)"),
    "conferencia": (3, "Conferência e recusadas (antigo)"),
    "relatorio": (7, "Relatório (fora da rotina)"),
    "compras": (5, "Compras de prova (fora da lista)"),
    "capa_perguntas": (8, "Perguntas nos anúncios (fora da lista)"),
    # 07/10: o "Tempo parado" saiu da lista (Vinicius: "pode excluir; quando ver necessidade criamos novamente")
    "aproveitar_parado": (8, "Tempo parado (fora da lista)"),
    # 05/10: as partes do passo 1 que rodam no perfil 50 e no Safari (a de e-mail é o próprio
    # ciclo_emails)
    "conferencia_perfil50": (1, "Conferência — resultados da Shopee"),
    "conferencia_safari": (1, "Conferência — Anatel e SEI"),
}
# 05/10: o passo 1 da tela junta as três partes (nome curto na coluna Resultado)
PARTES_PASSO1 = (("ciclo_emails", "e-mails"), ("conferencia_perfil50", "Shopee"),
                 ("conferencia_safari", "Anatel/SEI"))


def _ordem_nome(acao: str, nome: str | None) -> tuple[int, str]:
    if acao in PASSOS:
        return PASSOS[acao][0], PASSOS[acao][1]
    if acao in ANTIGOS:
        return ANTIGOS[acao]
    return 50, nome or acao.replace("_", " ")
FRENTES = (
    ("M", "Navegador (perfil 50)", "Mercado Livre, Shopee, TikTok, Amazon e conferências"),
    ("S", "Anatel / SEI (Safari)",
     "conferência da Anatel e do SEI (passo 1) e as petições (passo 6)"),
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


def _juntar_passo1(ultimas: dict[str, dict]) -> dict | None:
    """05/10: a última vez de hoje do passo 1 = as três partes (e-mails, Shopee, Anatel/SEI)
    numa linha só. Rodando se alguma roda; erro se alguma deu erro; o resultado diz como foi
    cada uma."""
    partes = [(nome, ultimas.get(acao)) for acao, nome in PARTES_PASSO1]
    partes = [(n, u) for n, u in partes if u]
    if not partes:
        return None
    sts = [u["status"] for _, u in partes]
    status = next((x for x in ("rodando", "fila", "erro") if x in sts), "concluida")
    marca = {"concluida": "ok", "rodando": "rodando", "fila": "na fila", "erro": "erro"}
    inicios = [u["inicio"] for _, u in partes if u.get("inicio")]
    fins = [u["fim"] for _, u in partes if u.get("fim")]
    return {
        "status": status,
        "inicio": min(inicios) if inicios else None,
        "fim": max(fins) if fins and status not in ("rodando", "fila") else None,
        "progresso": " · ".join(f"{n} {marca.get(u['status'], u['status'])}" for n, u in partes),
        "erro": " · ".join(f"{n}: {u['erro']}" for n, u in partes if u.get("erro")),
        "log": "\n\n".join(f"── {n} ──\n{u.get('log') or ''}" for n, u in partes)[-6000:],
        "vezes": max(u.get("vezes") or 1 for _, u in partes),
    }


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
    passo1 = _juntar_passo1(ultimas)
    if passo1:
        ultimas["ciclo_emails"] = passo1
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
         "ultima": ultimas.get(acao), "agenda": _agenda(acao),
         "explicacao": EXPLICACAO.get(acao), "em_criacao": acao in EM_CRIACAO}
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
