"""O catálogo das mensagens automáticas do DaVinci — tudo PURO (05/10/2026).

Eduardo, 05/10/2026: recriar no DaVinci as mensagens automáticas que o Duoke
manda hoje, "daí tal dia manda tal mensagem", começando em MODO SECO (o
DaVinci registra o que mandaria, para quem e quando, e não envia nada) para
comparar com o Duoke antes de ligar. Desenho, medições e decisões em
`docs/atendimento-automacoes.md`; o motor (banco, cron, envio) é
`automacoes.py`, o comparador `automacoes_comparar.py`.

Aqui fica o que NÃO fala com banco nem rede, para o teste não precisar de
nenhum dos dois:

  • o CATÁLOGO: cada automação com plataforma, canal, gatilho, atraso,
    validade, horário, condições, os textos padrão (os do Duoke, em português
    do Brasil e com o nome DENTRO do texto, nunca numa bolha separada) e as
    lojas onde o Duoke manda hoje (a semente da migration 0366);
  • a ASSINATURA de cada modelo do Duoke (o começo do texto normalizado) —
    é por ela que o comparador acha "o que o Duoke mandou";
  • a CLASSIFICAÇÃO de uma mensagem já gravada (`classificar`): dígito de
    opção, cartão, mensagem automática pela régua, modelo do Duoke ou nossa.
    Sai daqui sem texto nenhum: só o que a decisão precisa;
  • os GATILHOS que nascem de uma conversa (`gatilhos_da_conversa`) e a
    DECISÃO de cada linha (`decidir`) sobre um dicionário de fatos;
  • a RENDERIZAÇÃO do texto (`{comprador}`) e o validador.

Texto de comprador nunca sai daqui: a classificação lê o texto e devolve só
números e códigos.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.services.atendimento.constantes import (
    AUTOR_CLIENTE,
    AUTOR_LOJA,
    CANAL_CHAT,
    CANAL_POS_VENDA,
    ETIQUETA_AG_CANCELAMENTO,
    ETIQUETA_DEVOLUCAO,
    ETIQUETA_RECLAMACAO,
    MSG_FALHOU,
    ORIGEM_AUTO,
    ORIGEM_EXTERNO,
    ORIGEM_HUMANO,
    ORIGEM_IA,
    ORIGEM_SISTEMA,
    e_cartao_do_comprador,
    e_mensagem_automatica,
    marca_automacao,
    normalizar_inicio,
)

SP = ZoneInfo("America/Sao_Paulo")

# ── Vocabulário ───────────────────────────────────────────────────────────

MODO_DESLIGADO = "desligado"
MODO_SIMULAR = "simular"
MODO_ENVIAR = "enviar"
MODOS = (MODO_DESLIGADO, MODO_SIMULAR, MODO_ENVIAR)

ESTADO_AGENDADO = "agendado"
ESTADO_SIMULADO = "simulado"
ESTADO_ENVIANDO = "enviando"
ESTADO_ENVIADO = "enviado"
ESTADO_PULADO = "pulado"
ESTADO_FALHOU = "falhou"
ESTADO_REVISAR = "revisar"
ESTADO_SO_DUOKE = "so_duoke"
ESTADOS = (
    ESTADO_AGENDADO,
    ESTADO_SIMULADO,
    ESTADO_ENVIANDO,
    ESTADO_ENVIADO,
    ESTADO_PULADO,
    ESTADO_FALHOU,
    ESTADO_REVISAR,
    ESTADO_SO_DUOKE,
)
# O que conta como "já mandado" (ou "vai sair") na própria conversa: o modo
# seco conta o simulado como se tivesse saído — é o que o comprador teria
# visto. `agendado` também: a decisão é daqui a pouco.
ESTADOS_QUE_SAEM = (
    ESTADO_AGENDADO,
    ESTADO_SIMULADO,
    ESTADO_ENVIANDO,
    ESTADO_ENVIADO,
    ESTADO_REVISAR,
)

DUOKE_PENDENTE = "pendente"
DUOKE_MANDOU = "mandou"
DUOKE_NAO_MANDOU = "nao_mandou"
DUOKE_NAO_SE_APLICA = "nao_se_aplica"

ALVO_CONVERSA = "conversa"
ALVO_COMPRADOR = "comprador"
ALVO_PEDIDO = "pedido"
ALVO_DUOKE = "duoke"

FAMILIA_CONVERSA = "conversa"
FAMILIA_PEDIDO = "pedido"

# Os tipos de mensagem (o "o quê", igual no Duoke e no DaVinci).
TIPO_MENU = "menu"
TIPO_OPCAO = "opcao"
TIPO_AGUARDE = "aguarde"
TIPO_CONVITE = "convite"
TIPO_DUVIDA_1 = "duvida_1"
TIPO_DUVIDA_2 = "duvida_2"
TIPO_PEDIDO_RECEBIDO = "pedido_recebido"
TIPO_ENTREGUE = "entregue"
TIPO_POS = "pos"

# Os gatilhos (o "quando").
GATILHO_MENSAGEM = "mensagem"
GATILHO_OPCAO = "opcao"
GATILHO_SEGUINTE = "seguinte"  # nasce da decisão da anterior (26 h / 24 h)
GATILHO_PEDIDO_PAGO = "pedido_pago"
GATILHO_ENTREGUE = "entregue"
GATILHO_CONCLUIDO = "concluido"

# Motivos estáveis (a tela traduz; o registro guarda o código).
MOTIVOS: dict[str, str] = {
    "regra_desligada": "Regra desligada",
    "envio_desligado": "Regra em enviar, mas a chave de envio está desligada",
    "atrasado": "Passou da validade (motor parado ou atrasado)",
    "pessoa_respondeu": "Alguém da equipe já respondeu",
    "loja_respondeu": 'A última fala é da loja (no TikTok, o "já segue" conta, como no Duoke)',
    "ja_mandado": "Já mandado dentro do intervalo",
    "ja_recebeu": "O comprador já recebeu antes",
    "nao_e_primeira": "Não é a 1ª mensagem do comprador na conversa",
    "ja_comprou": "O comprador já comprou na loja",
    "avaliou": "O comprador já avaliou o pedido",
    "reclamacao_aberta": "Reclamação ou mediação aberta",
    "devolucao": "Devolução ou reembolso no pedido",
    "pedido_cancelado": "Pedido cancelado",
    "ja_concluido": "O pedido já foi concluído",
    "status_mudou": "O pedido mudou de status",
    "opcao_ja_respondida": "A mesma opção já foi respondida nesta sessão",
    "sem_texto": "A regra não tem texto",
    "sem_cartao_produto": "O comprador não mandou o cartão de um produto",
    "fora_da_janela_shopee": "Fora da janela de mensagem da Shopee",
    "conversa_bloqueada": "A conversa está bloqueada",
    "via_agente": "Pack atendido pelo Agente do ML",
    "ia_no_automatico": "A loja está com a IA no automático",
    "loja_sem_acesso": "A leitura da loja está sem acesso ou com erro",
    "teto_dia": "Passou do teto do dia",
    "teto_comprador": "Passou do teto de mensagens para este comprador",
    "texto_invalido": "O texto não passa no validador",
    "sem_comprador": "Sem o comprador do pedido",
    "sem_conversa": "Sem conversa com o comprador (envio sem conversa ainda não existe)",
    "disputa_com_pessoa": "Reclamação ou devolução com pessoa atendendo",
    "duoke_mandou": "O Duoke já mandou (Duoke ainda ligado?)",
    "campanha_sem_auto_reply": "Campanha da Shopee sem a resposta automática confirmada",
    "envio_recusado": "O envio recusou",
}

# Diferenças combinadas de propósito: ficam FORA da conta da %.
DIVERGENCIAS: dict[str, str] = {
    "horario_comercial": "Entregue: o DaVinci manda das 9h às 20h; o Duoke de madrugada",
    "concluiu_entre_horarios": (
        "Entregue: o pedido foi concluído entre o horário do DaVinci e o do Duoke"
    ),
    "menu_fim_de_sessao": "Menu do Duoke sem mensagem nova do comprador (fim de sessão)",
    "disputa_com_pessoa": "Menu pulado em reclamação/devolução com pessoa atendendo",
    "motor_atrasado": "O motor estava parado ou atrasado (passou da validade)",
    "teto_dia": "Passou do teto do dia (só no modo seco)",
    "teto_comprador": "Passou do teto por comprador (só no modo seco)",
    "sem_logistica": "Pedido fora da Logística: o DaVinci não vê o entregue nem o concluído",
    "regra_desligada": "A regra foi desligada depois do gatilho",
    "pessoa_respondeu": "Combinado: não vai quando a equipe já respondeu",
    "exclusao_disputa": (
        "Campanha não vai para quem está em reclamação ou devolução (o Duoke manda)"
    ),
    "opcao_repetida": (
        "O Duoke repete a resposta da opção a cada 12 h; o DaVinci responde uma vez"
    ),
}

# O que trava a troca (tolerância zero): só DaVinci para quem devolveu ou cancelou.
ALERTAS: dict[str, str] = {
    "devolucao": "Mandaria para quem devolveu ou pediu reembolso",
    "cancelado": "Mandaria para pedido cancelado",
    "reclamacao": "Mandaria com reclamação aberta",
}

# O comprador escreveu SÓ o número da opção (o menu tem 1 a 6).
_DIGITO = re.compile(r"^\s*(?:op[cç][aã]o\s*)?([1-6])\s*[.)\-]?\s*$", re.IGNORECASE)

PLACEHOLDER_COMPRADOR = "{comprador}"
PLACEHOLDERS: dict[str, str] = {
    "comprador": "o usuário do comprador na plataforma (some se não houver)",
}

# ── Textos padrão (os do Duoke, em pt-BR, com o nome dentro) ──────────────

TEXTO_MENU = (
    "Olá, por favor selecione sua dúvida e logo um dos nossos consultores irá atendê-lo!\n\n"
    "1 - Previsão de entrega / envio\n"
    "2 - Nota fiscal\n"
    "3 - Encerramento da compra\n"
    "4 - Troca de endereço\n"
    "5 - Garantia\n"
    "6 - Falar com Atendente"
)
TEXTO_OPCAO_1_SHOPEE = (
    "A entrega é feita pela Shopee, não temos acesso ao transporte, pode seguir o prazo "
    "informado na hora da compra por favor!\n\n"
    "Todas informações de envio e transporte estão em > Informações do Envio dentro do pedido!"
)
TEXTO_OPCAO_1_ML = (
    "A entrega é feita pelo Mercado Livre, não temos acesso ao transporte, pode seguir o "
    "prazo informado na hora da compra por favor!\n\n"
    "Para rastrear, entre no app do Mercado Livre, na opção mais no canto inferior direito "
    "da tela --> minhas compras --> detalhes do envio\n\n"
    "Se precisar de uma palavra-chave, o Meli informa no app 1 dia antes da entrega."
)
TEXTO_OPCAO_2 = (
    "Todos nossos produtos são enviados com nota fiscal que vai anexada a caixa do produto, "
    "caso não chegue ou queira antecipadamente, podemos enviar por aqui.\n\n"
    "As vezes pode ocorrer da nota inserida no pedido contenha algum campo errado, pois ela "
    "é inserida pelo sistema automaticamente.\n\n"
    "Conforme sua solicitação, enviaremos a nota em ate 1 dia util.\n\n"
    "Atenciosamente."
)
# ML: até 350 caracteres. O do Duoke tem 364; sem o "Atenciosamente." do fim, 347.
TEXTO_OPCAO_2_ML = TEXTO_OPCAO_2.removesuffix("\n\nAtenciosamente.")
TEXTO_OPCAO_3 = (
    "O pedido pode ser encerrado a qualquer momento antes do envio pelo comprador.\n\n"
    "Caso o pedido esteja em trânsito, deve-se recusar o recebimento do produto no ato "
    "da entrega."
)
TEXTO_OPCAO_5 = (
    "Por favor, descreva qual é o defeito do produto e envie-nos uma foto.\n\n"
    "Assim que tivermos um atendente disponível, já responderemos com a solicitação.\n\n"
    "O prazo de garantia é de 90 dias; a garantia não cobre mau uso!\n\n"
    "Atenciosamente"
)
TEXTO_OPCAO_6 = (
    "Descreva sua dúvida que assim que um atendente estiver disponível ele irá te responder!"
)
# O do Duoke está em português de Portugal ("equipa"); este depende do OK do Eduardo.
TEXTO_AGUARDE = (
    "Olá! Recebemos sua mensagem. Estamos com muitos atendimentos neste momento, mas já "
    "vamos te responder por aqui. Obrigado por aguardar!"
)
TEXTO_CONVITE_SHOPEE = (
    "{comprador} já segue nossa loja aqui na Shopee? Seguindo você recebe ofertas "
    "exclusivas, cupons e novidades em primeira mão 🚀"
)
TEXTO_CONVITE_TIKTOK = (
    "{comprador} já segue nossa loja aqui no TikTok? Seguindo você recebe ofertas "
    "exclusivas, cupons e novidades em primeira mão 🚀"
)
TEXTO_DUVIDA_1 = "Ficou alguma dúvida sobre o produto? Estou aqui pra te ajudar!"
TEXTO_DUVIDA_2 = "Tudo bem? Caso ainda esteja em dúvida, posso te explicar melhor sobre o produto"
TEXTO_PEDIDO_RECEBIDO = (
    "Oi! Recebemos seu pedido e já estamos preparando pra envio. Em breve você receberá o "
    "código de rastreio. Obrigado pela compra!"
)
TEXTO_ENTREGUE_CELULAR = (
    "Oi, {comprador}! Tudo bem? 😊 Confirmamos a entrega do seu pedido! Por se tratar de um "
    "produto de valor, recomendo abrir a embalagem gravando um vídeo contínuo, mostrando a "
    "caixa lacrada até a retirada do produto. Para a segurança dos nossos clientes, todos os "
    "pedidos são filmados e pesados antes do envio, garantindo que tudo saia daqui em "
    "perfeito estado. Qualquer coisa, tô por aqui pra ajudar!"
)
TEXTO_ENTREGUE_MALA = (
    "Oi, {comprador}! 🧳✨ Que alegria saber que sua mala já chegou! Espero que tenha gostado "
    "e que ela te acompanhe em muitas viagens incríveis. Qualquer dúvida sobre o produto, é "
    "só me chamar por aqui 😊"
)
TEXTO_POS_CONCLUSAO = (
    "Oi, {comprador}! Só passando para saber se está tudo certo com o seu produto. Se sim e "
    "puder avaliar, agradeço muito! Se tiver qualquer problema, me avisa que eu resolvo."
)

PARTE_CARTAO = {"tipo": "cartao_pedido"}
PARTE_FIGURINHA = {"tipo": "figurinha", "figurinha": "0007", "pacote": "br_shoppito"}
TIPOS_PARTE = ("texto", "cartao_pedido", "figurinha")


def _texto(t: str) -> dict:
    return {"tipo": "texto", "texto": t}


# ── As lojas onde o Duoke manda hoje (levantamento de 05/10/2026) ─────────
# O nome da INTEGRAÇÃO (`integrations.name`, strip + lower), como a
# Logística casa a conta. Fora do alcance (sem acesso pelo DaVinci): TikTok
# Inova e Poofy, ML Poofy e ML lucas mei — ali continua no Duoke.

SHOPEE_MENU = frozenset(
    {
        "barbosa",
        "inova",
        "jlas",
        "kfa",
        "kia",
        "mega",
        "minas",
        "mini",
        "poofy",
        "victor mei",
        "vita",
        "vortan",
    }
)
SHOPEE_CAMPANHAS = SHOPEE_MENU | {"atv"}
SHOPEE_MALA = frozenset({"inova", "kfa", "minas", "poofy"})
TIKTOK_AGUARDE = frozenset({"mini", "barbosa", "atv", "eron"})
TIKTOK_CONVITE = frozenset({"atv", "barbosa", "mini", "injox", "jlas", "eron"})
TIKTOK_DUVIDA = frozenset({"atv", "barbosa", "mini", "eron"})
ML_MENU = frozenset(
    {
        "aguiar",
        "barbosa",
        "counhago",
        "forpaper",
        "injox",
        "inova",
        "jlas2",
        "kfa",
        "kfa2",
        "kia",
        "marquezini",
        "mini",
        "velasco",
        "victor mei",
        "zorvex",
    }
)


def nome_normalizado(nome: str | None) -> str:
    """O nome da integração para casar com as listas acima."""
    return " ".join((nome or "").split()).lower()


# ── O catálogo ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Automacao:
    """Uma automação do catálogo. Os padrões valem para a loja SEM regra."""

    codigo: str
    plataforma: str
    canal: str
    nome: str
    tipo: str
    gatilho: str
    alvo: str
    familia: str
    descricao: str
    partes: tuple[dict, ...]
    atraso_min: int
    validade: timedelta
    lojas_duoke: frozenset[str]
    janela: tuple[time, time] | None = None
    condicoes: dict = field(default_factory=dict)
    opcao: int | None = None
    # Campanha da Shopee: hoje sai como resposta automática (`auto_reply`).
    campanha: bool = False
    # Sem texto até o painel do Duoke dizer qual é (opção 4): não liga.
    travada: bool = False
    # A automação que nasce da decisão desta (2 h → 26 h / 24 h).
    seguinte: str | None = None
    # No modo enviar, quanto esperar depois do `devido_em` (e da leitura da
    # loja passar dele) para ver se o Duoke ainda mandou: a troca não duplica.
    espera_duoke: timedelta = timedelta(0)
    # A janela de comparação com o Duoke: (base, de, até) — base é
    # "devido" ou "evento".
    comparar: tuple[str, timedelta, timedelta] = (
        "devido",
        timedelta(minutes=-5),
        timedelta(minutes=20),
    )
    # O maior atraso do Duoke depois do gatilho (o "só Duoke" só conta a
    # mensagem do Duoke depois de o motor estar ligado há tanto tempo).
    atraso_max_duoke: timedelta = timedelta(minutes=30)
    # Diferença combinada de propósito: a tela mostra o selo.
    diferenca_combinada: str | None = None
    # Quanto o "só Duoke" espera antes de dar a mensagem do Duoke como sem
    # par: a nossa linha pode ser decidida DEPOIS da mensagem do Duoke (o
    # entregue: o Duoke sai de madrugada e nós às 9h) — antes disso, a
    # mensagem ainda vai casar com ela.
    so_duoke_espera: timedelta = timedelta(hours=1)

    @property
    def espera_padrao(self) -> timedelta:
        return timedelta(minutes=self.atraso_min)


def _min(n: float) -> timedelta:
    return timedelta(minutes=n)


def _h(n: float) -> timedelta:
    return timedelta(hours=n)


def _opcoes(plataforma: str, canal: str, lojas: frozenset[str]) -> list[Automacao]:
    textos = {
        1: TEXTO_OPCAO_1_ML if plataforma == "ml" else TEXTO_OPCAO_1_SHOPEE,
        2: TEXTO_OPCAO_2_ML if plataforma == "ml" else TEXTO_OPCAO_2,
        3: TEXTO_OPCAO_3,
        4: None,
        5: TEXTO_OPCAO_5,
        6: TEXTO_OPCAO_6,
    }
    nomes = {
        1: "previsão de entrega",
        2: "nota fiscal",
        3: "encerramento da compra",
        4: "troca de endereço",
        5: "garantia",
        6: "falar com atendente",
    }
    saida = []
    for n, texto in textos.items():
        saida.append(
            Automacao(
                codigo=f"{plataforma}_opcao_{n}",
                plataforma=plataforma,
                canal=canal,
                nome=f"Resposta da opção {n} ({nomes[n]})",
                tipo=TIPO_OPCAO,
                gatilho=GATILHO_OPCAO,
                alvo=ALVO_CONVERSA,
                familia=FAMILIA_CONVERSA,
                descricao=(
                    f"O comprador escreve só o {n} com o menu valendo (12 h): responde na "
                    "hora (o Duoke responde 12 h depois do menu)."
                ),
                partes=() if texto is None else (_texto(texto),),
                atraso_min=1,
                validade=_min(30),
                lojas_duoke=frozenset() if texto is None else lojas,
                condicoes={"sessao_h": 12, "opcao_sem_sessao": True},
                opcao=n,
                travada=texto is None,
                comparar=("evento", timedelta(0), _h(13)),
                atraso_max_duoke=_h(13),
                diferenca_combinada="na_hora",
            )
        )
    return saida


def _montar() -> dict[str, Automacao]:
    lista: list[Automacao] = [
        Automacao(
            codigo="shopee_menu",
            plataforma="shopee",
            canal=CANAL_CHAT,
            nome='Menu "selecione sua dúvida"',
            tipo=TIPO_MENU,
            gatilho=GATILHO_MENSAGEM,
            alvo=ALVO_CONVERSA,
            familia=FAMILIA_CONVERSA,
            descricao="Mensagem do comprador sem robô (menu ou opção) nas últimas 12 h → 1 min.",
            partes=(_texto(TEXTO_MENU),),
            atraso_min=1,
            validade=_min(30),
            lojas_duoke=SHOPEE_MENU,
            condicoes={"sessao_h": 12, "pular_em_disputa_com_pessoa": True},
            espera_duoke=_min(2),
            atraso_max_duoke=_min(20),
        ),
        *_opcoes("shopee", CANAL_CHAT, SHOPEE_MENU),
        Automacao(
            codigo="shopee_aguarde",
            plataforma="shopee",
            canal=CANAL_CHAT,
            nome='"Recebemos sua mensagem, aguarde"',
            tipo=TIPO_AGUARDE,
            gatilho=GATILHO_MENSAGEM,
            alvo=ALVO_CONVERSA,
            familia=FAMILIA_CONVERSA,
            descricao=(
                "1ª mensagem do comprador sem resposta de pessoa → 10 min; no máximo um a cada 4 h."
            ),
            partes=(_texto(TEXTO_AGUARDE),),
            atraso_min=10,
            validade=_min(30),
            lojas_duoke=frozenset({"atv"}),
            condicoes={"intervalo_h": 4},
            espera_duoke=_min(2),
            atraso_max_duoke=_h(4),
        ),
        Automacao(
            codigo="shopee_convite",
            plataforma="shopee",
            canal=CANAL_CHAT,
            nome="Convite para seguir a loja",
            tipo=TIPO_CONVITE,
            gatilho=GATILHO_MENSAGEM,
            alvo=ALVO_COMPRADOR,
            familia=FAMILIA_CONVERSA,
            descricao=(
                "Mensagem do comprador que nunca recebeu o convite → 1 min; uma vez por comprador."
            ),
            partes=(_texto(TEXTO_CONVITE_SHOPEE),),
            atraso_min=1,
            validade=_min(30),
            lojas_duoke=SHOPEE_CAMPANHAS,
            campanha=True,
            espera_duoke=_min(2),
            atraso_max_duoke=_min(20),
        ),
        Automacao(
            codigo="shopee_duvida_2h",
            plataforma="shopee",
            canal=CANAL_CHAT,
            nome='"Ficou alguma dúvida?" (2 h)',
            tipo=TIPO_DUVIDA_1,
            gatilho=GATILHO_MENSAGEM,
            alvo=ALVO_CONVERSA,
            familia=FAMILIA_CONVERSA,
            descricao=(
                "2 h depois da 1ª mensagem do ciclo (7 dias), só para quem nunca comprou "
                "na loja; vai mesmo que a equipe já tenha respondido, como o Duoke."
            ),
            partes=(_texto(TEXTO_DUVIDA_1),),
            atraso_min=120,
            validade=_h(3),
            lojas_duoke=SHOPEE_CAMPANHAS,
            condicoes={
                "ciclo_dias": 7,
                "so_quem_nunca_comprou": True,
                "nao_se_pessoa_respondeu": False,
            },
            campanha=True,
            seguinte="shopee_duvida_26h",
            espera_duoke=_min(5),
            comparar=("devido", _min(-30), _min(45)),
            atraso_max_duoke=_h(3),
        ),
        Automacao(
            codigo="shopee_duvida_26h",
            plataforma="shopee",
            canal=CANAL_CHAT,
            nome='"Caso ainda esteja em dúvida" (26 h)',
            tipo=TIPO_DUVIDA_2,
            gatilho=GATILHO_SEGUINTE,
            alvo=ALVO_CONVERSA,
            familia=FAMILIA_CONVERSA,
            descricao='24 h depois do "ficou alguma dúvida", com a figurinha; as mesmas condições.',
            partes=(_texto(TEXTO_DUVIDA_2), dict(PARTE_FIGURINHA)),
            atraso_min=24 * 60,
            validade=_h(3),
            lojas_duoke=SHOPEE_CAMPANHAS,
            condicoes={"so_quem_nunca_comprou": True, "nao_se_pessoa_respondeu": False},
            campanha=True,
            espera_duoke=_min(5),
            comparar=("devido", _min(-30), _min(45)),
            atraso_max_duoke=_h(27),
        ),
        Automacao(
            codigo="shopee_pedido_recebido",
            plataforma="shopee",
            canal=CANAL_CHAT,
            nome="Pedido recebido",
            tipo=TIPO_PEDIDO_RECEBIDO,
            gatilho=GATILHO_PEDIDO_PAGO,
            alvo=ALVO_PEDIDO,
            familia=FAMILIA_PEDIDO,
            descricao="Pedido pago visto no Bling → 5 min (o Duoke sai 5 min depois do Bling).",
            partes=(dict(PARTE_CARTAO), _texto(TEXTO_PEDIDO_RECEBIDO)),
            atraso_min=5,
            validade=_h(6),
            lojas_duoke=SHOPEE_CAMPANHAS,
            campanha=True,
            espera_duoke=_min(3),
            comparar=("evento", timedelta(0), _h(1)),
            atraso_max_duoke=_h(1),
        ),
        Automacao(
            codigo="shopee_entregue",
            plataforma="shopee",
            canal=CANAL_CHAT,
            nome='Entregue (celular: filmar a abertura; mala: "sua mala chegou")',
            tipo=TIPO_ENTREGUE,
            gatilho=GATILHO_ENTREGUE,
            alvo=ALVO_PEDIDO,
            familia=FAMILIA_PEDIDO,
            descricao=(
                "Pedido entregue e ainda não concluído (varredura da Logística) → das 9h às "
                "20h (o Duoke manda de madrugada)."
            ),
            partes=(dict(PARTE_CARTAO), _texto(TEXTO_ENTREGUE_CELULAR)),
            atraso_min=0,
            validade=_h(36),
            lojas_duoke=SHOPEE_CAMPANHAS,
            janela=(time(9, 0), time(20, 0)),
            campanha=True,
            comparar=("evento", timedelta(0), _h(36)),
            atraso_max_duoke=_h(36),
            diferenca_combinada="horario_comercial",
            so_duoke_espera=_h(14),
        ),
        Automacao(
            codigo="shopee_pos_conclusao",
            plataforma="shopee",
            canal=CANAL_CHAT,
            nome='Pós-conclusão ("está tudo certo?")',
            tipo=TIPO_POS,
            gatilho=GATILHO_CONCLUIDO,
            alvo=ALVO_PEDIDO,
            familia=FAMILIA_PEDIDO,
            descricao="4 h depois de o pedido ficar concluído, só sem avaliação e sem devolução.",
            partes=(_texto(TEXTO_POS_CONCLUSAO),),
            atraso_min=240,
            validade=_h(24),
            lojas_duoke=SHOPEE_CAMPANHAS,
            condicoes={"so_sem_avaliacao": True, "so_com_conversa": True},
            campanha=True,
            comparar=("evento", _h(3), _h(8)),
            atraso_max_duoke=_h(8),
        ),
        Automacao(
            codigo="tiktok_aguarde",
            plataforma="tiktok",
            canal=CANAL_CHAT,
            nome='"Recebemos sua mensagem, aguarde"',
            tipo=TIPO_AGUARDE,
            gatilho=GATILHO_MENSAGEM,
            alvo=ALVO_CONVERSA,
            familia=FAMILIA_CONVERSA,
            descricao=(
                "1ª mensagem do comprador sem resposta de pessoa → 10 min; no máximo um a cada 8 h."
            ),
            partes=(_texto(TEXTO_AGUARDE),),
            atraso_min=10,
            validade=_min(30),
            lojas_duoke=TIKTOK_AGUARDE,
            condicoes={"intervalo_h": 8, "so_se_comprador_por_ultimo": True},
            espera_duoke=_min(2),
            atraso_max_duoke=_h(8),
        ),
        Automacao(
            codigo="tiktok_convite",
            plataforma="tiktok",
            canal=CANAL_CHAT,
            nome="Convite para seguir a loja",
            tipo=TIPO_CONVITE,
            gatilho=GATILHO_MENSAGEM,
            alvo=ALVO_CONVERSA,
            familia=FAMILIA_CONVERSA,
            descricao="1ª mensagem do comprador na conversa → 1 min; uma vez por conversa.",
            partes=(_texto(TEXTO_CONVITE_TIKTOK),),
            atraso_min=1,
            validade=_min(30),
            lojas_duoke=TIKTOK_CONVITE,
            espera_duoke=_min(2),
            atraso_max_duoke=_min(20),
        ),
        Automacao(
            codigo="tiktok_duvida_2h",
            plataforma="tiktok",
            canal=CANAL_CHAT,
            nome='"Ficou alguma dúvida?" (2 h)',
            tipo=TIPO_DUVIDA_1,
            gatilho=GATILHO_MENSAGEM,
            alvo=ALVO_CONVERSA,
            familia=FAMILIA_CONVERSA,
            descricao=(
                "2 h depois da ÚLTIMA mensagem do comprador, em conversa em que ele mandou o "
                "cartão de um produto; ciclo de 7 dias."
            ),
            partes=(_texto(TEXTO_DUVIDA_1),),
            atraso_min=120,
            validade=_h(3),
            lojas_duoke=TIKTOK_DUVIDA,
            condicoes={
                "ciclo_dias": 7,
                "so_com_cartao_de_produto": True,
                "so_quem_nunca_comprou": True,
                "nao_se_pessoa_respondeu": False,
            },
            seguinte="tiktok_duvida_24h",
            espera_duoke=_min(5),
            comparar=("devido", _min(-30), _min(45)),
            atraso_max_duoke=_h(3),
        ),
        Automacao(
            codigo="tiktok_duvida_24h",
            plataforma="tiktok",
            canal=CANAL_CHAT,
            nome='"Caso ainda esteja em dúvida" (24 h)',
            tipo=TIPO_DUVIDA_2,
            gatilho=GATILHO_SEGUINTE,
            alvo=ALVO_CONVERSA,
            familia=FAMILIA_CONVERSA,
            descricao='24 h depois do "ficou alguma dúvida"; as mesmas condições.',
            partes=(_texto(TEXTO_DUVIDA_2),),
            atraso_min=24 * 60,
            validade=_h(3),
            lojas_duoke=TIKTOK_DUVIDA,
            condicoes={"so_quem_nunca_comprou": True, "nao_se_pessoa_respondeu": False},
            espera_duoke=_min(5),
            comparar=("devido", _min(-30), _min(45)),
            atraso_max_duoke=_h(27),
        ),
        Automacao(
            codigo="ml_menu",
            plataforma="ml",
            canal=CANAL_POS_VENDA,
            nome="Menu do pós-venda",
            tipo=TIPO_MENU,
            gatilho=GATILHO_MENSAGEM,
            alvo=ALVO_CONVERSA,
            familia=FAMILIA_CONVERSA,
            descricao=(
                "Mensagem do comprador no pack sem robô (menu ou opção) nas últimas 12 h → 1 min."
            ),
            partes=(_texto(TEXTO_MENU),),
            atraso_min=1,
            validade=_min(30),
            lojas_duoke=ML_MENU,
            condicoes={"sessao_h": 12, "pular_em_disputa_com_pessoa": True},
            espera_duoke=_min(2),
            atraso_max_duoke=_min(20),
        ),
        *_opcoes("ml", CANAL_POS_VENDA, ML_MENU),
    ]
    return {a.codigo: a for a in lista}


CATALOGO: dict[str, Automacao] = _montar()


def automacao(codigo: str) -> Automacao | None:
    return CATALOGO.get(codigo)


def por_plataforma(plataforma: str) -> list[Automacao]:
    return [a for a in CATALOGO.values() if a.plataforma == plataforma]


def partes_padrao(aut: Automacao, nome_loja: str | None = None) -> list[dict]:
    """O texto padrão da automação NESTA loja (o entregue muda de celular para mala)."""
    partes = [dict(p) for p in aut.partes]
    if aut.codigo == "shopee_entregue" and nome_normalizado(nome_loja) in SHOPEE_MALA:
        partes = [dict(PARTE_CARTAO), _texto(TEXTO_ENTREGUE_MALA)]
    return partes


def modo_semente(aut: Automacao, nome_loja: str | None) -> str:
    """`simular` onde o Duoke manda hoje; `desligado` no resto (e na opção 4)."""
    if aut.travada:
        return MODO_DESLIGADO
    return MODO_SIMULAR if nome_normalizado(nome_loja) in aut.lojas_duoke else MODO_DESLIGADO


def regra_semente(aut: Automacao, nome_loja: str | None) -> dict:
    """Os campos da regra que a semente (migration 0366 e o botão da tela) cria."""
    inicio, fim = aut.janela if aut.janela else (None, None)
    return {
        "automacao": aut.codigo,
        "plataforma": aut.plataforma,
        "modo": modo_semente(aut, nome_loja),
        "partes": partes_padrao(aut, nome_loja),
        "atraso_min": aut.atraso_min,
        "janela_inicio": inicio,
        "janela_fim": fim,
        "condicoes": dict(aut.condicoes),
    }


def plataforma_da_integracao(valor: Any) -> str:
    """O valor da plataforma de `integrations.platform` (enum ou texto)."""
    return str(getattr(valor, "value", valor) or "").lower()


# ── Assinaturas do Duoke (o começo do texto normalizado) ──────────────────
# (tipo, opção, como casa, trecho). Os textos do Duoke, medidos em produção
# (levantamento de 05/10/2026). "contem" para o que começa pelo usuário do
# comprador ou pela saudação com o nome.
ASSINATURAS_DUOKE: tuple[tuple[str, int | None, str, str], ...] = (
    (TIPO_MENU, None, "inicio", "ola, por favor selecione sua duvida"),
    (TIPO_OPCAO, 1, "inicio", "a entrega e feita pela shopee, nao temos acesso ao transporte"),
    (
        TIPO_OPCAO,
        1,
        "inicio",
        "a entrega e feita pelo mercado livre, nao temos acesso ao transporte",
    ),
    (TIPO_OPCAO, 2, "inicio", "todos nossos produtos sao enviados com nota fiscal"),
    (TIPO_OPCAO, 3, "inicio", "o pedido pode ser encerrado a qualquer momento antes do envio"),
    (TIPO_OPCAO, 5, "inicio", "por favor, descreva qual e o defeito do produto"),
    (TIPO_OPCAO, 6, "inicio", "descreva sua duvida que assim que um atendente"),
    (TIPO_AGUARDE, None, "inicio", "ola, a sua mensagem foi recebida"),
    (TIPO_CONVITE, None, "contem", "ja segue nossa loja aqui"),
    (TIPO_DUVIDA_1, None, "inicio", "ficou alguma duvida sobre o produto"),
    (TIPO_DUVIDA_2, None, "inicio", "tudo bem? caso ainda esteja em duvida"),
    (TIPO_PEDIDO_RECEBIDO, None, "inicio", "oi! recebemos seu pedido e ja estamos preparando"),
    (TIPO_ENTREGUE, None, "contem", "confirmamos a entrega do seu pedido"),
    (TIPO_ENTREGUE, None, "contem", "que alegria saber que sua mala ja chegou"),
    (TIPO_POS, None, "contem", "so passando para saber se esta tudo certo com o seu produto"),
)


def assinatura(texto: str | None) -> tuple[str, int | None] | None:
    """O modelo do Duoke (tipo, opção) que o texto é, ou None. PURA."""
    n = normalizar_inicio((texto or "")[:400])
    if not n:
        return None
    for tipo, opcao, como, trecho in ASSINATURAS_DUOKE:
        if n.startswith(trecho) if como == "inicio" else trecho in n:
            return tipo, opcao
    return None


def digito_opcao(texto: str | None) -> int | None:
    """O comprador escreveu só o número da opção (1 a 6)? PURA."""
    m = _DIGITO.match(texto or "")
    return int(m.group(1)) if m else None


# ── A mensagem classificada (sem texto) ───────────────────────────────────


@dataclass(frozen=True)
class Msg:
    """Uma mensagem da conversa, só com o que a decisão precisa — nunca o texto."""

    id: Any
    autor: str
    origem: str
    em: datetime
    visto_em: datetime
    status: str = ""
    digito: int | None = None
    # Comprador: o cartão que ele mandou ("produto" | "pedido").
    cartao: str | None = None
    # Loja/sistema: automática pela régua (texto + payload).
    automatica: bool = False
    # O modelo do Duoke (tipo, opção) — só de fora (`externo`/`sistema`).
    tipo_duoke: str | None = None
    opcao_duoke: int | None = None
    # A nossa (motor de automações): o código da automação.
    nossa: str | None = None
    # O cartão de pedido (da loja ou do comprador): o número do pedido.
    pedido: str | None = None
    # Shopee: o comprador mexeu no chat sem escrever (a pergunta pronta que o
    # robô da Shopee respondeu, `bundle_message` do `server`, gravada como
    # `sistema`). O convite do Duoke conta isso como a 1ª mensagem (medido: 31
    # dos 43 convites sem mensagem do comprador nos 10 min antes, em 7 dias).
    interacao: bool = False

    @property
    def do_comprador(self) -> bool:
        return self.autor == AUTOR_CLIENTE

    @property
    def pessoa_da_loja(self) -> bool:
        """Resposta de PESSOA da loja: a régua da fila "Falta responder"."""
        return (
            self.autor == AUTOR_LOJA
            and self.status != MSG_FALHOU
            and self.origem in (ORIGEM_EXTERNO, ORIGEM_HUMANO, ORIGEM_IA)
            and not self.automatica
        )

    @property
    def tipo_auto(self) -> str | None:
        """O tipo da mensagem automática (do Duoke ou nossa)."""
        if self.nossa:
            aut = CATALOGO.get(self.nossa)
            return aut.tipo if aut else None
        return self.tipo_duoke

    @property
    def opcao_auto(self) -> int | None:
        if self.nossa:
            aut = CATALOGO.get(self.nossa)
            return aut.opcao if aut else None
        return self.opcao_duoke


def classificar(
    *,
    id: Any,  # noqa: A002 — o id da mensagem
    autor: str,
    origem: str,
    status: str | None,
    texto: str | None,
    payload: object,
    em: datetime,
    visto_em: datetime | None = None,
) -> Msg:
    """Lê a mensagem e devolve só os sinais (sem texto). PURA."""
    payload = payload if isinstance(payload, dict) else {}
    em = _utc(em)
    visto = _utc(visto_em) if visto_em is not None else em
    conteudo = payload.get("content") if isinstance(payload.get("content"), dict) else {}
    pedido = None
    if payload.get("message_type") == "order" or payload.get("type") == "ORDER_CARD":
        pedido = str(conteudo.get("order_sn") or conteudo.get("order_id") or "").strip() or None
    if autor == AUTOR_CLIENTE:
        cartao = None
        if e_cartao_do_comprador(payload):
            produto = payload.get("message_type") in ("item", "variation_card") or (
                payload.get("type") == "PRODUCT_CARD"
            )
            cartao = "produto" if produto else "pedido"
        return Msg(
            id=id,
            autor=autor,
            origem=origem,
            em=em,
            visto_em=visto,
            status=status or "",
            digito=digito_opcao(texto) if cartao is None else None,
            cartao=cartao,
            pedido=pedido,
        )
    marca = marca_automacao(payload)
    nossa = None
    if origem == ORIGEM_AUTO or marca is not None:
        codigo = str((marca or {}).get("codigo") or "")
        nossa = codigo if codigo in CATALOGO else (codigo or "desconhecida")
    tipo_duoke = opcao_duoke = None
    if nossa is None and origem in (ORIGEM_EXTERNO, ORIGEM_SISTEMA):
        achado = assinatura(texto)
        if achado:
            tipo_duoke, opcao_duoke = achado
    return Msg(
        id=id,
        autor=autor,
        origem=origem,
        em=em,
        visto_em=visto,
        status=status or "",
        interacao=payload.get("message_type") == "bundle_message"
        and payload.get("source") == "server",
        automatica=e_mensagem_automatica(texto, payload),
        tipo_duoke=tipo_duoke,
        opcao_duoke=opcao_duoke,
        nossa=nossa,
        pedido=pedido,
    )


# ── Relógio e horário ─────────────────────────────────────────────────────


def _utc(quando: datetime) -> datetime:
    if quando.tzinfo is None:
        return quando.replace(tzinfo=UTC)
    return quando.astimezone(UTC)


def ajustar_janela(devido: datetime, inicio: time | None, fim: time | None) -> datetime:
    """O `devido` que cai fora da janela de horário (São Paulo) anda para a próxima abertura."""
    devido = _utc(devido)
    if inicio is None or fim is None:
        return devido
    local = devido.astimezone(SP)
    abre = local.replace(hour=inicio.hour, minute=inicio.minute, second=0, microsecond=0)
    fecha = local.replace(hour=fim.hour, minute=fim.minute, second=0, microsecond=0)
    if abre <= local < fecha:
        return devido
    if local < abre:
        return abre.astimezone(UTC)
    return (abre + timedelta(days=1)).astimezone(UTC)


def validade_ate(aut: Automacao, devido: datetime, janela: tuple[time, time] | None) -> datetime:
    """Até quando a linha ainda vale. O entregue vale até o fim da janela do dia seguinte."""
    devido = _utc(devido)
    if aut.tipo == TIPO_ENTREGUE and janela:
        local = devido.astimezone(SP) + timedelta(days=1)
        fim = local.replace(hour=janela[1].hour, minute=janela[1].minute, second=0, microsecond=0)
        return fim.astimezone(UTC)
    return devido + aut.validade


def janela_comparacao(
    aut: Automacao, evento_em: datetime | None, devido_em: datetime
) -> tuple[datetime, datetime]:
    """Onde procurar a mensagem do Duoke desta linha."""
    base, de, ate = aut.comparar
    ref = _utc(evento_em) if base == "evento" and evento_em is not None else _utc(devido_em)
    return ref + de, ref + ate


# ── Renderização e validação ──────────────────────────────────────────────


def _sem_nome(texto: str) -> str:
    """Tira o `{comprador}` sem deixar a frase quebrada.

    "Oi, {comprador}! …" → "Oi! …"; "{comprador} já segue…" → "Já segue…".
    """
    t = re.sub(r",\s*\{comprador\}\s*(?=[!?.,])", "", texto)
    t = re.sub(
        r"^\s*\{comprador\}\s*,?\s*(\w)",
        lambda m: m.group(1).upper(),
        t,
    )
    t = t.replace(PLACEHOLDER_COMPRADOR, "")
    return re.sub(r"[ \t]{2,}", " ", t).strip()


def renderizar_texto(texto: str, comprador: str | None) -> str:
    nome = " ".join((comprador or "").split())
    if not nome:
        return _sem_nome(texto)
    return texto.replace(PLACEHOLDER_COMPRADOR, nome)


def renderizar(
    partes: Iterable[dict], *, comprador: str | None, plataforma: str, canal: str
) -> tuple[list[dict], list[str]]:
    """As partes prontas para sair + os motivos do validador (vazio = pode).

    Com o nome, o texto que não passa no validador (o usuário do comprador
    parece telefone ou perfil) tenta de novo SEM o nome — o Duoke manda de
    qualquer jeito, e a mensagem sem o nome continua a mesma.
    """
    from app.services.atendimento import validador

    saida: list[dict] = []
    motivos: list[str] = []
    for parte in partes:
        if not isinstance(parte, dict):
            motivos.append("parte inválida")
            continue
        tipo = parte.get("tipo")
        if tipo != "texto":
            if tipo not in TIPOS_PARTE:
                motivos.append(f"parte desconhecida ({tipo})")
            saida.append(dict(parte))
            continue
        bruto = str(parte.get("texto") or "")
        tentativas = [renderizar_texto(bruto, comprador)]
        if comprador and PLACEHOLDER_COMPRADOR in bruto:
            tentativas.append(renderizar_texto(bruto, None))
        ultimo: list[str] = []
        pronto = None
        for t in tentativas:
            normalizado = validador.normalizar(t, plataforma=plataforma, canal=canal)
            ultimo = list(
                validador.validar(
                    normalizado, plataforma=plataforma, canal=canal, origem=ORIGEM_AUTO
                )
            )
            if not ultimo:
                pronto = normalizado
                break
        if pronto is None:
            motivos.extend(ultimo or ["texto vazio"])
            continue
        saida.append({"tipo": "texto", "texto": pronto})
    if not any(p.get("tipo") == "texto" for p in saida) and not motivos:
        motivos.append("sem texto")
    return saida, list(dict.fromkeys(motivos))


# ── Gatilhos que nascem de uma conversa (PURO) ────────────────────────────


@dataclass(frozen=True)
class Linha:
    """Uma linha do registro da conversa, como a descoberta e a decisão a veem."""

    automacao: str
    estado: str
    chave: str
    evento_em: datetime | None
    devido_em: datetime
    gatilho_mensagem_id: Any = None


@dataclass(frozen=True)
class Candidato:
    automacao: str
    chave: str
    alvo: str
    gatilho_mensagem_id: Any
    evento_em: datetime
    devido_em: datetime
    # TikTok "ficou alguma dúvida": a linha agendada anda com a ÚLTIMA mensagem.
    atualizar_agendado: bool = False


@dataclass
class Conversa:
    """O que a descoberta sabe da conversa."""

    id: Any
    plataforma: str
    canal: str
    comprador_id: str | None
    msgs: list[Msg]
    registro: list[Linha]
    ativas: dict[str, Any]  # codigo → regra (com `condicoes`, `atraso_min`, `janela_*`)
    desde: datetime


def _cond(regra: Any, aut: Automacao, chave: str, padrao: Any = None) -> Any:
    condicoes = getattr(regra, "condicoes", None) if regra is not None else None
    if isinstance(condicoes, dict) and chave in condicoes:
        return condicoes[chave]
    return aut.condicoes.get(chave, padrao)


def _atraso(regra: Any, aut: Automacao) -> timedelta:
    valor = getattr(regra, "atraso_min", None) if regra is not None else None
    return timedelta(minutes=int(valor if valor is not None else aut.atraso_min))


def _janela(regra: Any, aut: Automacao) -> tuple[time, time] | None:
    if regra is not None:
        ini, fim = getattr(regra, "janela_inicio", None), getattr(regra, "janela_fim", None)
        if ini is not None and fim is not None:
            return ini, fim
        if hasattr(regra, "janela_inicio"):
            return None
    return aut.janela


def janela_da_regra(regra: Any, aut: Automacao) -> tuple[time, time] | None:
    return _janela(regra, aut)


def _envios(
    conv: Conversa, tipos: set[str], plataforma: str, *, do_duoke: bool = True
) -> list[datetime]:
    """Quando saiu (ou vai sair) uma mensagem destes tipos na conversa.

    O Duoke e o DaVinci (a mensagem nossa que voltou) pelas mensagens; o modo
    seco pelas linhas do registro que contam como saída. `do_duoke=False`:
    só as nossas (mensagem e registro) — o modo seco não se cala pelo que o
    Duoke mandou DEPOIS do gatilho.
    """
    saida = [
        m.em
        for m in conv.msgs
        if not m.do_comprador and m.tipo_auto in tipos and (do_duoke or m.nossa)
    ]
    for linha in conv.registro:
        aut = CATALOGO.get(linha.automacao)
        if (
            aut
            and aut.plataforma == plataforma
            and aut.tipo in tipos
            and linha.estado in ESTADOS_QUE_SAEM
        ):
            saida.append(linha.devido_em)
    return sorted(saida)


def _cand(
    aut: Automacao,
    conv: Conversa,
    b: Msg,
    devido: datetime,
    *,
    chave: str | None = None,
    atualizar: bool = False,
) -> Candidato:
    return Candidato(
        automacao=aut.codigo,
        chave=chave or f"conversa:{conv.id}:msg:{b.id}",
        alvo=aut.alvo,
        gatilho_mensagem_id=b.id,
        evento_em=b.em,
        devido_em=devido,
        atualizar_agendado=atualizar,
    )


def _sessao_do_robo(
    conv: Conversa,
    plataforma: str,
    em: datetime,
    sessao: timedelta,
    *,
    chave: str | None = None,
    pendentes: Iterable[tuple[datetime, datetime]] = (),
) -> bool:
    """Há robô (menu ou opção) valendo para a mensagem do comprador em `em`?

    Vale o que o comprador VIU (mensagem do Duoke ou nossa, nas `sessao`
    horas ANTES dela) e o que o motor já agendou ou mandou por um gatilho
    ANTERIOR — mesmo que saia depois dela: a 2ª mensagem do mesmo minuto não
    ganha outro menu (o Duoke também manda um só). A mensagem do Duoke DEPOIS
    dela não conta: no modo seco é o Duoke respondendo a ela mesma.
    """
    tipos = {TIPO_MENU, TIPO_OPCAO}
    if any(
        not m.do_comprador and m.tipo_auto in tipos and em - sessao <= m.em < em for m in conv.msgs
    ):
        return True
    for linha in conv.registro:
        aut = CATALOGO.get(linha.automacao)
        if (
            aut is not None
            and aut.plataforma == plataforma
            and aut.tipo in tipos
            and linha.estado in ESTADOS_QUE_SAEM
            and linha.chave != chave
            and linha.evento_em is not None
            and linha.evento_em <= em
            and linha.devido_em >= em - sessao
        ):
            return True
    # `<=`: duas mensagens no mesmo segundo (o cartão e o texto) abrem UMA sessão.
    return any(evento <= em and devido >= em - sessao for evento, devido in pendentes)


def _menu_antes(conv: Conversa, plataforma: str, em: datetime) -> datetime | None:
    """O último menu (do Duoke, nosso, ou linha nossa) ANTES de `em`, de qualquer idade."""
    tempos = [
        m.em for m in conv.msgs if not m.do_comprador and m.tipo_auto == TIPO_MENU and m.em < em
    ]
    for linha in conv.registro:
        aut = CATALOGO.get(linha.automacao)
        if (
            aut is not None
            and aut.plataforma == plataforma
            and aut.tipo == TIPO_MENU
            and linha.estado in ESTADOS_QUE_SAEM
            and linha.devido_em < em
        ):
            tempos.append(linha.devido_em)
    return max(tempos) if tempos else None


def _opcao_vale(conv: Conversa, plataforma: str, b: Msg) -> bool:
    """O dígito do comprador é uma OPÇÃO? Com o menu valendo (12 h) — ou, como o
    Duoke, com qualquer menu antes na conversa (o "robô liberado": medido, o Duoke
    responde a opção na hora quem já conhece o menu, sem mandar outro)."""
    codigo = f"{plataforma}_opcao_{b.digito}"
    if not b.digito or codigo not in conv.ativas:
        return False
    aut = CATALOGO[codigo]
    regra = conv.ativas[codigo]
    ultimo = _menu_antes(conv, plataforma, b.em)
    if ultimo is None:
        return False
    sessao = timedelta(hours=float(_cond(regra, aut, "sessao_h", 12)))
    return b.em - ultimo <= sessao or bool(_cond(regra, aut, "opcao_sem_sessao", True))


def _menu(conv: Conversa, aut: Automacao, regra: Any) -> list[Candidato]:
    sessao = timedelta(hours=float(_cond(regra, aut, "sessao_h", 12)))
    saida = []
    novos: list[tuple[datetime, datetime]] = []
    for b in (m for m in conv.msgs if m.do_comprador):
        chave = f"conversa:{conv.id}:msg:{b.id}"
        if _sessao_do_robo(conv, aut.plataforma, b.em, sessao, chave=chave, pendentes=novos):
            continue
        if _opcao_vale(conv, aut.plataforma, b):
            # O dígito de quem já conhece o menu é respondido pela opção (sem menu
            # novo), e a resposta da opção abre a sessão do robô.
            novos.append((b.em, b.em + timedelta(minutes=1)))
            continue
        if b.visto_em < conv.desde:
            continue
        devido = ajustar_janela(b.em + _atraso(regra, aut), *(_janela(regra, aut) or (None, None)))
        saida.append(_cand(aut, conv, b, devido))
        novos.append((b.em, devido))
    return saida


def _opcao(conv: Conversa, plataforma: str) -> list[Candidato]:
    saida = []
    menus = _envios(conv, {TIPO_MENU}, plataforma)
    for b in (m for m in conv.msgs if m.do_comprador and m.digito):
        codigo = f"{plataforma}_opcao_{b.digito}"
        if codigo not in conv.ativas or b.visto_em < conv.desde:
            continue
        # Um "2" solto pode ser "quero 2": sem menu nenhum antes, nada.
        if not _opcao_vale(conv, plataforma, b):
            continue
        aut = CATALOGO[codigo]
        regra = conv.ativas[codigo]
        saida.append(_cand(aut, conv, b, b.em + _atraso(regra, aut)))
    del menus
    return saida


def _loja_falou_por_ultimo(conv: Conversa, ate: datetime) -> bool:
    """A última fala (comprador ou loja; o sistema não conta) até `ate` é da loja?

    O "aguarde" do TikTok (`so_se_comprador_por_ultimo`), medido: o Duoke manda
    10 min depois da 1ª mensagem do turno só se a ÚLTIMA fala ainda é do
    comprador — o "já segue" (que sai pela conta de atendimento 1 min depois da
    1ª mensagem) conta como fala da loja; se o comprador escreve depois dele, o
    "aguarde" sai do mesmo jeito, no mesmo horário.
    """
    falas = [
        m
        for m in conv.msgs
        if m.em <= ate
        and (m.do_comprador or (m.autor == AUTOR_LOJA and m.status != MSG_FALHOU))
        and m.tipo_auto != TIPO_AGUARDE
    ]
    return bool(falas) and not max(falas, key=lambda m: m.em).do_comprador


def _aguarde(conv: Conversa, aut: Automacao, regra: Any) -> list[Candidato]:
    """M = 1ª mensagem do comprador depois de max(última pessoa, último "aguarde")."""
    intervalo = timedelta(hours=float(_cond(regra, aut, "intervalo_h", 4)))
    envios = _envios(conv, {TIPO_AGUARDE}, aut.plataforma)
    pessoas = [m.em for m in conv.msgs if m.pessoa_da_loja]
    compradores = [m for m in conv.msgs if m.do_comprador]
    saida = []
    for b in compradores:
        refs = [t for t in pessoas if t < b.em] + [t for t in envios if t < b.em]
        ref = max(refs) if refs else None
        primeira = next((m for m in compradores if ref is None or m.em > ref), None)
        if primeira is not b or b.visto_em < conv.desde:
            continue
        devido = b.em + _atraso(regra, aut)
        ultimos = [t for t in envios if t < b.em]
        if ultimos:
            devido = max(devido, ultimos[-1] + intervalo)
        saida.append(_cand(aut, conv, b, devido))
        envios = sorted([*envios, devido])
    return saida


def _convite(conv: Conversa, aut: Automacao, regra: Any) -> list[Candidato]:
    envios = _envios(conv, {TIPO_CONVITE}, aut.plataforma)
    compradores = [m for m in conv.msgs if m.do_comprador]
    if aut.plataforma == "tiktok":
        # Uma vez por conversa, na 1ª mensagem do comprador.
        if not compradores or envios and envios[0] < compradores[0].em:
            return []
        b = compradores[0]
        if b.visto_em < conv.desde:
            return []
        return [_cand(aut, conv, b, b.em + _atraso(regra, aut), chave=f"conversa:{conv.id}")]
    # Shopee: a pergunta pronta do chat (sem texto) também abre o convite.
    compradores = sorted(
        [m for m in conv.msgs if m.do_comprador or m.interacao], key=lambda m: (m.em, str(m.id))
    )
    for b in compradores:
        if any(t < b.em for t in envios):
            return []
        if b.visto_em < conv.desde:
            continue
        chave = f"comprador:{conv.comprador_id}" if conv.comprador_id else f"conversa:{conv.id}"
        return [_cand(aut, conv, b, b.em + _atraso(regra, aut), chave=chave)]
    return []


def _ancoras_do_ciclo(conv: Conversa, aut: Automacao) -> list[datetime]:
    """O começo dos ciclos do "ficou alguma dúvida": o do Duoke e as nossas linhas.

    As nossas em qualquer estado (até a pulada): o ciclo conta da 1ª mensagem.
    """
    saida = [m.em for m in conv.msgs if not m.do_comprador and m.tipo_auto == aut.tipo]
    saida += [
        linha.evento_em
        for linha in conv.registro
        if linha.automacao == aut.codigo and linha.evento_em is not None
    ]
    return sorted(saida)


def _duvida(conv: Conversa, aut: Automacao, regra: Any) -> list[Candidato]:
    ciclo = timedelta(days=float(_cond(regra, aut, "ciclo_dias", 7)))
    ancoras = _ancoras_do_ciclo(conv, aut)
    compradores = [m for m in conv.msgs if m.do_comprador]
    saida: list[Candidato] = []
    for i, b in enumerate(compradores):
        if any(b.em - ciclo <= a <= b.em for a in ancoras):
            continue
        ancoras = sorted([*ancoras, b.em])
        if aut.plataforma == "tiktok":
            # Conta da ÚLTIMA mensagem do ciclo: a linha agendada anda junto.
            fim_ciclo = b.em + ciclo
            ultima = [m for m in compradores[i:] if m.em < fim_ciclo][-1]
            if ultima.visto_em < conv.desde:
                continue
            saida.append(
                _cand(
                    aut,
                    conv,
                    ultima,
                    ultima.em + _atraso(regra, aut),
                    chave=f"conversa:{conv.id}:ciclo:{b.id}",
                    atualizar=True,
                )
            )
            continue
        if b.visto_em < conv.desde:
            continue
        saida.append(_cand(aut, conv, b, b.em + _atraso(regra, aut)))
    return saida


def gatilhos_da_conversa(conv: Conversa) -> list[Candidato]:
    """Os candidatos que as mensagens novas da conversa disparam. PURA.

    Idempotente: rodar de novo com a mesma conversa dá as mesmas chaves (o
    `ON CONFLICT DO NOTHING` descarta). Só nascem candidatos de mensagens
    vistas a partir de `conv.desde` (a regra ligada e o motor rodando).
    """
    conv.msgs.sort(key=lambda m: (m.em, str(m.id)))
    saida: list[Candidato] = []
    for codigo, regra in conv.ativas.items():
        aut = CATALOGO.get(codigo)
        if aut is None or aut.plataforma != conv.plataforma or aut.canal != conv.canal:
            continue
        if aut.tipo == TIPO_MENU:
            saida += _menu(conv, aut, regra)
        elif aut.tipo == TIPO_AGUARDE:
            saida += _aguarde(conv, aut, regra)
        elif aut.tipo == TIPO_CONVITE:
            saida += _convite(conv, aut, regra)
        elif aut.tipo == TIPO_DUVIDA_1:
            saida += _duvida(conv, aut, regra)
    if any(CATALOGO[c].tipo == TIPO_OPCAO for c in conv.ativas if c in CATALOGO):
        saida += _opcao(conv, conv.plataforma)
    return saida


# ── Fatos da conversa para a decisão (PURO) ───────────────────────────────


def fatos_da_conversa(
    aut: Automacao,
    *,
    msgs: list[Msg],
    registro: list[Linha],
    chave: str,
    evento_em: datetime,
    agora: datetime,
    regra: Any,
    gatilho_id: Any = None,
) -> dict:
    """O que a linha de conversa precisa saber da própria conversa. PURA."""
    evento_em = _utc(evento_em)
    agora = _utc(agora)
    pessoas = [m.em for m in msgs if m.pessoa_da_loja]
    outras = [linha for linha in registro if linha.chave != chave]
    fatos: dict = {
        "pessoa_respondeu": any(t >= evento_em for t in pessoas),
        "loja_respondeu": False,
        "pessoa_respondeu_24h": any(t >= agora - timedelta(hours=24) for t in pessoas),
        "tem_cartao_produto": any(m.do_comprador and m.cartao == "produto" for m in msgs),
        # Cartão de pedido na conversa (do comprador ou o da campanha do pedido).
        "tem_cartao_pedido": any(
            m.cartao == "pedido" or (not m.do_comprador and m.pedido) for m in msgs
        ),
        "ja_mandado": False,
        "opcao_ja_respondida": False,
        "duoke_depois": [],
    }
    conv = Conversa(
        id=None,
        plataforma=aut.plataforma,
        canal=aut.canal,
        comprador_id=None,
        msgs=msgs,
        registro=outras,
        ativas={},
        desde=evento_em,
    )
    if aut.tipo == TIPO_MENU:
        sessao = timedelta(hours=float(_cond(regra, aut, "sessao_h", 12)))
        fatos["ja_mandado"] = _sessao_do_robo(conv, aut.plataforma, evento_em, sessao, chave=chave)
    elif aut.tipo == TIPO_AGUARDE:
        intervalo = timedelta(hours=float(_cond(regra, aut, "intervalo_h", 4)))
        envios = _envios(conv, {TIPO_AGUARDE}, aut.plataforma)
        nossos = _envios(conv, {TIPO_AGUARDE}, aut.plataforma, do_duoke=False)
        fatos["ja_mandado"] = any(evento_em <= t < agora for t in nossos) or any(
            agora - intervalo < t < evento_em for t in envios
        )
        if _cond(regra, aut, "so_se_comprador_por_ultimo", False):
            fatos["loja_respondeu"] = _loja_falou_por_ultimo(conv, agora)
    elif aut.tipo == TIPO_OPCAO:
        menu_em = _menu_antes(conv, aut.plataforma, evento_em)
        if menu_em is not None:
            respondidas = [
                m.em
                for m in msgs
                if not m.do_comprador
                and m.tipo_auto == TIPO_OPCAO
                and m.opcao_auto == aut.opcao
                and menu_em <= m.em < evento_em
            ]
            respondidas += [
                linha.devido_em
                for linha in outras
                if linha.automacao == aut.codigo
                and linha.estado in ESTADOS_QUE_SAEM
                and menu_em <= linha.devido_em < evento_em + timedelta(minutes=1)
            ]
            fatos["opcao_ja_respondida"] = bool(respondidas)
    elif aut.tipo == TIPO_CONVITE:
        fatos["ja_mandado"] = any(
            not m.do_comprador and m.tipo_auto == TIPO_CONVITE and m.em < evento_em for m in msgs
        )
    # O Duoke mandou o mesmo DEPOIS do gatilho (só conta no modo enviar).
    fatos["duoke_depois"] = [
        m.em
        for m in msgs
        if m.tipo_duoke == aut.tipo
        and (aut.opcao is None or m.opcao_duoke == aut.opcao)
        and m.em >= evento_em
    ]
    return fatos


# ── A decisão (PURA) ──────────────────────────────────────────────────────

_ETIQUETAS_RECLAMACAO = (ETIQUETA_RECLAMACAO, ETIQUETA_DEVOLUCAO, ETIQUETA_AG_CANCELAMENTO)
_ETIQUETAS_DISPUTA = (ETIQUETA_RECLAMACAO, ETIQUETA_DEVOLUCAO)


def decidir(aut: Automacao, f: dict, regra: Any = None) -> str | None:
    """O motivo para NÃO mandar (código de `MOTIVOS`), ou None = manda. PURA.

    `f` é o dicionário de fatos que o motor lê do banco (chaves ausentes =
    falso). A ordem é a da explicação na tela: primeiro o que é da loja e da
    conversa, depois as exclusões das campanhas, por fim o que é desta
    automação.
    """
    if aut.travada or not (getattr(regra, "partes", None) or aut.partes):
        return "sem_texto"
    if f.get("canal_sem_acesso"):
        return "loja_sem_acesso"
    if f.get("canal_auto"):
        return "ia_no_automatico"
    if f.get("conversa_bloqueada"):
        return "conversa_bloqueada"
    if aut.plataforma == "ml":
        if f.get("via_agente"):
            return "via_agente"
        if f.get("reclamacao_ml"):
            return "reclamacao_aberta"
    campanha = aut.tipo in (
        TIPO_CONVITE,
        TIPO_DUVIDA_1,
        TIPO_DUVIDA_2,
        TIPO_PEDIDO_RECEBIDO,
        TIPO_ENTREGUE,
        TIPO_POS,
    )
    if campanha:
        if f.get("pedido_cancelado"):
            return "pedido_cancelado"
        # Entregue e pós-conclusão: QUALQUER devolução/reembolso do pedido,
        # aberta ou encerrada (medido: o Duoke não manda para quem devolveu).
        if f.get("devolucao_qualquer") and aut.tipo in (TIPO_ENTREGUE, TIPO_POS):
            return "devolucao"
        if f.get("devolucao_aberta"):
            return "devolucao"
        if f.get("reclamacao_aberta"):
            return "reclamacao_aberta"
        if f.get("etiqueta") in _ETIQUETAS_RECLAMACAO:
            return "devolucao" if f.get("etiqueta") == ETIQUETA_DEVOLUCAO else "reclamacao_aberta"
    if aut.tipo == TIPO_MENU:
        if f.get("ja_mandado"):
            return "ja_mandado"
        if (
            _cond(regra, aut, "pular_em_disputa_com_pessoa", True)
            and f.get("etiqueta") in _ETIQUETAS_DISPUTA
            and f.get("pessoa_respondeu_24h")
        ):
            return "disputa_com_pessoa"
    elif aut.tipo == TIPO_OPCAO:
        if f.get("pessoa_respondeu"):
            return "pessoa_respondeu"
        if f.get("opcao_ja_respondida"):
            return "opcao_ja_respondida"
    elif aut.tipo == TIPO_AGUARDE:
        if f.get("pessoa_respondeu"):
            return "pessoa_respondeu"
        if f.get("loja_respondeu"):
            return "loja_respondeu"
        if f.get("ja_mandado"):
            return "ja_mandado"
    elif aut.tipo == TIPO_CONVITE:
        if f.get("ja_recebeu") or f.get("ja_mandado"):
            return "ja_recebeu"
        if aut.plataforma == "tiktok" and f.get("nao_e_primeira"):
            return "nao_e_primeira"
    elif aut.tipo in (TIPO_DUVIDA_1, TIPO_DUVIDA_2):
        if _cond(regra, aut, "so_quem_nunca_comprou", True) and (
            f.get("ja_comprou") or f.get("tem_cartao_pedido")
        ):
            return "ja_comprou"
        if _cond(regra, aut, "nao_se_pessoa_respondeu", False) and f.get("pessoa_respondeu"):
            return "pessoa_respondeu"
        if (
            aut.tipo == TIPO_DUVIDA_1
            and _cond(regra, aut, "so_com_cartao_de_produto", False)
            and not f.get("tem_cartao_produto")
        ):
            return "sem_cartao_produto"
    elif aut.tipo == TIPO_ENTREGUE:
        status = f.get("status_pedido")
        if status == "COMPLETED":
            return "ja_concluido"
        if status != "TO_CONFIRM_RECEIVE":
            return "status_mudou"
    elif aut.tipo == TIPO_POS:
        if _cond(regra, aut, "so_sem_avaliacao", True) and f.get("avaliou"):
            return "avaliou"
        # Medido (simulação de 05/10): sem conversa com o comprador, o Duoke não
        # manda o pós (5 de 5). A amostra é pequena: conferir no painel (§10).
        if _cond(regra, aut, "so_com_conversa", False) and not f.get("tem_conversa"):
            return "sem_conversa"
        if f.get("status_pedido") != "COMPLETED":
            return "status_mudou"
    if aut.plataforma == "shopee" and f.get("janela_shopee_ok") is False:
        return "fora_da_janela_shopee"
    return None


_TIPOS_CAMPANHA = (
    TIPO_CONVITE,
    TIPO_DUVIDA_1,
    TIPO_DUVIDA_2,
    TIPO_PEDIDO_RECEBIDO,
    TIPO_ENTREGUE,
    TIPO_POS,
)


def divergencia_do_motivo(motivo: str | None, aut: Automacao | None = None) -> str | None:
    """O `pulado` que é diferença combinada (fora da %), não decisão da regra.

    A campanha que o DaVinci NÃO manda para quem está em reclamação ou devolução
    (`exclusao_disputa`) é de propósito: o Duoke manda (a simulação de 05/10
    mostrou o convite, a dúvida e o pedido recebido indo para essas conversas).
    """
    if (
        aut is not None
        and aut.tipo in _TIPOS_CAMPANHA
        and motivo in ("reclamacao_aberta", "devolucao")
    ):
        return "exclusao_disputa"
    return {
        "atrasado": "motor_atrasado",
        "teto_dia": "teto_dia",
        "teto_comprador": "teto_comprador",
        "disputa_com_pessoa": "disputa_com_pessoa",
        "regra_desligada": "regra_desligada",
    }.get(motivo or "")
