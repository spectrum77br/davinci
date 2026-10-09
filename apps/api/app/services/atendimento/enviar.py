"""O caminho ÚNICO de saída de uma resposta do atendimento.

Pessoa pela tela e IA no automático saem por aqui — e por nenhum outro lugar.
Mensagem para comprador não se desenvia; por isso a ordem abaixo é rígida:

  0. A CONVERSA É TRAVADA (FOR NO KEY UPDATE) e RELIDA do banco antes de
     tudo: duas abas, duas pessoas ou duas rodadas da IA na mesma conversa
     passam aqui uma de cada vez, e cada uma decide sobre o estado de AGORA
     (não sobre o objeto que ela leu minutos antes). A pessoa espera no
     máximo 5 s pela trava (`conversa_ocupada`: a rodada do sync está
     gravando esta conversa — tente de novo).

  E-MAIL (08/10/2026, RF5/RF6): a conversa que a PONTE da Central de e-mail
  criou (`mail_atendimento/ponte.e_conversa_da_ponte`: canal `email` +
  `dados.fonte = 'tuta'` + `dados.mail`) NÃO passa pelos adaptadores — nunca
  pelo chat do marketplace nem pelo SMTP da Amazon. `enviar_resposta` a
  entrega ANTES de tudo a `mail_atendimento/responder.enfileirar_resposta`
  (as travas do e-mail, a mensagem `enviando` + o job na fila da Central,
  que o Mac envia). Qualquer outro caminho (foto, automática) que chegar ao
  `_destino` com ela é recusado ali. A resposta `enviando` com o job VIVO
  na fila (`responder.fila_viva_existe`) não é "envio preso".

  1. TRAVAS (`EnvioRecusado`, nada vai à plataforma):
       canal_sem_envio     — e-mail do Tuta ou Zap (05/10/2026): o envio deles
                             ainda não existe no DaVinci, e sem esta trava
                             cairiam no adaptador do marketplace da venda
                             (`constantes.motivo_canal_sem_envio`);
       somente_leitura     — plataforma sem adaptador de envio (Instagram) ou
                             lida pelo robô do Mac mini (Temu, AliExpress:
                             "responda no Seller Center");
       simulador_em_producao — o simulador (só local) ligado em produção;
       envio_desligado     — `settings.atendimento_envio_ativo` desligado;
       conversa_bloqueada  — a plataforma não deixa mais responder (ou a
                             janela de resposta dela fechou);
       sem_integracao      — conversa sem canal/loja ativa por trás (Amazon
                             sem conta identificada: escolher a conta);
       canal_em_observacao — o canal está em `observar` (o Duoke responde);
       auto_desligado      — origem IA sem o automático ligado no canal;
       nao_aguarda         — origem IA numa conversa que não espera mais a
                             IA (pausada, fechada, "não precisa de resposta",
                             ou a loja já respondeu depois da pergunta);
       texto_invalido      — o validador reprovou (detail = motivos);
       envio_repetido      — a MESMA resposta saiu nesta conversa há menos de
                             2 min (clique duplo, aba duplicada);
       conversa_mudou      — alguém respondeu depois da última mensagem que a
                             pessoa viu (a tela manda `ultima_vista_id`;
                             `confirmar` envia mesmo assim);
       envio_em_andamento  — já há uma resposta EM VOO nesta conversa.

  2. EM VOO: a mensagem nasce `enviando` e é COMMITADA antes de falar com a
     plataforma. O índice parcial `uq_atendimento_envio_em_voo` é quem barra
     duas abas (ou a pessoa e o automático) apertando "Enviar" juntas: só um
     INSERT passa, o outro vira `envio_em_andamento`. Commitar ANTES também
     deixa a linha visível para o sync, que a ADOTA se a plataforma a
     devolver antes de nós terminarmos (gravar.gravar_mensagem).

  3. A PLATAFORMA responde e a linha vira:
       enviada  — saiu (com o id da plataforma quando ela devolve);
       revisar  — AMBÍGUO (timeout, erro sem código): pode ter saído. NUNCA
                  se retenta em cima — a tela mostra e a pessoa confere;
       falhou   — não saiu (o cliente continua esperando na fila);
     e, com `bloqueio`, a conversa vira `bloqueada` com o motivo.

  4. O RASCUNHO da IA ganha o que a pessoa fez com ele (avaliação), que é o
     material do aprendizado: enviou igual, editou, ou escreveu do zero.

`settings.atendimento_simulador` (SÓ LOCAL) troca o passo 3 por um sucesso
fingido (`sim:<uuid>`): é como a tela se testa de ponta a ponta sem escrever
para comprador nenhum. Em produção ele RECUSA o envio: "Enviar" que diz que
saiu e não saiu é pior que "não saiu". `settings.atendimento_simulador_exceto`
(também só local) lista plataformas que escapam do simulador e saem de
verdade — é o teste de responder pela tela local a um e-mail real da Amazon
com as outras lojas ainda no simulador.

AVALIAÇÃO (RF8, 02/10/2026): a conversa `canal = 'avaliacao'` responde a
avaliação da venda — resposta PÚBLICA, no anúncio — pelo MESMO caminho
(travas, linha em voo, validador, régua de resultado); muda a chamada
(`responder_avaliacao` do adaptador, hoje só a Shopee `reply_comment`) e
três travas: o envio desligado diz "resposta pública"; a IA nunca responde
avaliação sozinha; e a loja vem da integração da conversa (ela não tem
canal próprio), com o modo do canal da loja. Saiu: a avaliação é dada por
respondida na hora (`avaliacoes.depois_da_resposta`).

MENSAGEM AUTOMÁTICA (05/10/2026, docs/atendimento-automacoes.md):
`enviar_automatica` é o mesmo caminho para uma PARTE de texto do motor de
automações (`automacoes.py`): mesma trava da conversa, mesma linha em voo
commitada antes da plataforma, mesmo validador (as regras de todos, não as
da IA: o texto fixo é da loja), mesma conferência de repetida, mesma régua
de resultado. A linha nasce `origem = davinci_auto` com a marca
`payload.automacao`. Travas próprias, além das que não dependem de quem
escreve (canal sem envio, somente leitura, simulador em produção, conversa
bloqueada, sem integração):
       envio_desligado            — o FREIO ÚNICO `atendimento_envio_ativo`
                                    (desligado, nada sai: pessoa, IA ou automação);
       automacoes_envio_desligado — `atendimento_automacoes_envio` desligado;
       shopee_mensagens_desligadas — `shopee_mensagens_comprador` desligado
                                    (o freio de mão da mensagem proativa na Shopee);
       regra_nao_envia            — a regra da loja, RELIDA aqui, não está em `enviar`;
       campanha_sem_auto_reply    — campanha da Shopee sem a resposta automática:
                                    falta a chave confirmada
                                    (`atendimento_automacoes_shopee_auto_reply`, o
                                    teste de permissão passou) OU o envio por ela
                                    no adaptador (`enviar_parte(auto_reply=True)`,
                                    `auto_reply_no_adaptador`). Sem os dois, a
                                    campanha sairia como mensagem NORMAL — o que
                                    a trava existe para evitar;
       so_simulacao               — a automação SÓ SIMULA (o pedido não pago com
                                    cupom, a resposta da avaliação, o "pedido
                                    recebido" do TikTok — `Automacao.so_simular`):
                                    nunca sai, com qualquer chave e regra.
Cada PARTE é uma mensagem: o texto, o cartão do pedido e a figurinha (as duas
últimas só na Shopee, no formato que o Duoke manda); o texto das campanhas da
Shopee sai como resposta automática (`send_autoreply_message`, pela
`enviar_parte(auto_reply=True)` do adaptador — nunca pelo `enviar_texto`).
Sem conversa no DaVinci (o pedido recebido de quem nunca escreveu à loja),
`enviar_automatica_sem_conversa` manda pelo `to_id` do comprador do pedido e
grava a conversa que a Shopee devolve.
O canal em `observar` NÃO segura a automática (de propósito: a equipe segue
no Duoke enquanto ela sai por aqui), nem as travas da IA (automático, vez).

FOTO (01/10/2026): `enviar_foto` é o mesmo caminho para UMA imagem (Shopee,
TikTok e pós-venda do ML; `foto.py`): mesmas travas, mesma linha em voo,
mesma régua de resultado. O upload para a plataforma só acontece aqui —
com o envio desligado, nada sobe.

Texto de comprador (e o nosso) nunca vai para o log — só ids e estados.
"""

from __future__ import annotations

import asyncio
import importlib
import inspect
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import ModuleType
from typing import Any
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import is_unique_violation
from app.models import (
    AtendimentoAutomacaoRegra,
    AtendimentoAvaliacao,
    AtendimentoCanal,
    AtendimentoConversa,
    AtendimentoMensagem,
    AtendimentoRascunho,
    Integration,
    User,
)
from app.services.atendimento import clientes, gravar
from app.services.atendimento.constantes import (
    AUTOR_LOJA,
    AVALIACAO_OBSERVOU,
    CANAL_AVALIACAO,
    CONVERSA_BLOQUEADA,
    CONVERSA_FECHADA,
    MODO_AUTO,
    MODOS_QUE_ENVIAM,
    MSG_ENVIADA,
    MSG_ENVIANDO,
    MSG_FALHOU,
    MSG_REVISAR,
    ORIGEM_AUTO,
    ORIGEM_EXTERNO,
    ORIGEM_HUMANO,
    ORIGEM_IA,
    ORIGENS_DAVINCI,
    PLATAFORMA_SITE,
    PLATAFORMAS_REDE,
    PLATAFORMAS_RESPONDEM_AVALIACAO,
    PLATAFORMAS_ROBO,
    PLATAFORMAS_SEM_AUTO,
    RASCUNHO_EDITADO,
    RASCUNHO_ENVIADO,
    RASCUNHO_PENDENTE,
    RASCUNHO_SUBSTITUIDO,
    ResultadoEnvio,
    motivo_canal_sem_envio,
    reclamacao_aberta,
)

logger = structlog.get_logger()

_SP = ZoneInfo("America/Sao_Paulo")

# Import TARDIO por nome de módulo: o envio não carrega os adaptadores
# à toa, e o teste troca um adaptador com monkeypatch neste dicionário.
ADAPTADORES: dict[str, str] = {
    "shopee": "app.services.atendimento.shopee",
    "ml": "app.services.atendimento.ml",
    "tiktok": "app.services.atendimento.tiktok",
    "amazon": "app.services.atendimento.amazon_email",
    # Pergunta, chat e SAC pela API do vendedor. Toda resposta passa pela
    # MODERAÇÃO da Magalu: "enviada" aqui é "a Magalu recebeu" (o payload do
    # envio diz `moderacao`), e o validador já barra o que ela barraria.
    "magalu": "app.services.atendimento.magalu",
}

# ── Códigos de recusa (estáveis: a tela traduz, o router devolve 409/422) ──
# E-mail do Tuta e Zap (05/10/2026): sem frase própria na tela (`ERROS`) DE
# PROPÓSITO — sem tradução, a tela mostra o `detail`, que diz qual dos dois e
# onde responder (`constantes.motivo_canal_sem_envio`).
RECUSA_CANAL_SEM_ENVIO = "canal_sem_envio"
RECUSA_SOMENTE_LEITURA = "somente_leitura"
# O e-mail da ponte por um caminho que não é a resposta de texto (foto,
# automática): não sai.
MOTIVO_EMAIL_SO_RESPOSTA = (
    "E-mail: só a resposta de texto sai pelo DaVinci (na fila da Central de e-mail)."
)
# Os canais de fora dos marketplaces (02/10/2026): a frase da caixa de baixo.
MOTIVO_CARRINHO_SO_LEITURA = (
    "Carrinho do site: nada é mandado ao lojista pelo DaVinci. Fale com ele pelo "
    "contato do cartão e marque como resolvido."
)
MOTIVO_COMENTARIO_PELO_CARTAO = (
    "Comentário de rede social: responda pelo cartão da publicação (comentário "
    "público ou Direct), não por esta caixa."
)
RECUSA_ENVIO_DESLIGADO = "envio_desligado"
RECUSA_CONVERSA_BLOQUEADA = "conversa_bloqueada"
RECUSA_SEM_INTEGRACAO = "sem_integracao"
RECUSA_CANAL_EM_OBSERVACAO = "canal_em_observacao"
RECUSA_AUTO_DESLIGADO = "auto_desligado"
RECUSA_TEXTO_INVALIDO = "texto_invalido"
RECUSA_ENVIO_EM_ANDAMENTO = "envio_em_andamento"
RECUSA_SIMULADOR_EM_PRODUCAO = "simulador_em_producao"
RECUSA_NAO_AGUARDA = "nao_aguarda"
RECUSA_ENVIO_REPETIDO = "envio_repetido"
RECUSA_CONVERSA_MUDOU = "conversa_mudou"
RECUSA_CONVERSA_OCUPADA = "conversa_ocupada"
# Foto na resposta (01/10/2026, `enviar_foto` e `foto.py`):
#   foto_nao_suportada — a plataforma/caixa não aceita foto pelo DaVinci;
#   foto_invalida      — o arquivo não serve (tipo, tamanho) ou a legenda
#                        não cabe nesta plataforma (Shopee/TikTok: foto sozinha).
RECUSA_FOTO_NAO_SUPORTADA = "foto_nao_suportada"
RECUSA_FOTO_INVALIDA = "foto_invalida"
# Mensagem automática do motor (05/10/2026, `enviar_automatica`).
RECUSA_AUTOMACOES_ENVIO_DESLIGADO = "automacoes_envio_desligado"
RECUSA_SHOPEE_MENSAGENS_DESLIGADAS = "shopee_mensagens_desligadas"
RECUSA_REGRA_NAO_ENVIA = "regra_nao_envia"
RECUSA_CAMPANHA_SEM_AUTO_REPLY = "campanha_sem_auto_reply"
# A automação que só simula (05/10/2026, à noite: "deixe só pra mostrar que ele
# enviaria mesmo corretamente, mas não enviar"): nunca sai.
RECUSA_SO_SIMULACAO = "so_simulacao"
# Só estas plataformas têm automação (e adaptador de texto em conversa).
PLATAFORMAS_AUTOMACAO = ("shopee", "tiktok", "ml")
# Recusa que passa sozinha: a próxima rodada tenta de novo (até a validade).
RECUSAS_TEMPORARIAS = ("conversa_ocupada", "envio_em_andamento")
# Avaliação (RF8, 02/10/2026): a avaliação da conversa `avaliacao` já tem
# resposta da loja (de fora ou do DaVinci) — a mesma recusa da rota
# POST /avaliacoes/{id}/responder, para a caixa de baixo não responder duas vezes.
RECUSA_AVALIACAO_JA_RESPONDIDA = "ja_respondida"

# Temu/AliExpress (lidas pelo robô do Mac mini): nada sai pelo DaVinci.
_NOME_SELLER_CENTER = {"temu": "Temu", "aliexpress": "AliExpress"}


def motivo_seller_center(plataforma: str) -> str:
    """O porquê de não enviar numa loja do robô — o texto que a tela mostra."""
    nome = _NOME_SELLER_CENTER.get(plataforma, plataforma)
    return (
        f"{nome}: responda no Seller Center — aqui o DaVinci só lê (pelo robô do "
        "Mac mini) e mostra a sugestão da IA para copiar."
    )


# A resposta à avaliação é PÚBLICA (RF8): o envio desligado diz isso.
MOTIVO_AVALIACAO_ENVIO_DESLIGADO = (
    "Resposta pública (aparece no anúncio): o envio pelo DaVinci está desligado "
    "(ATENDIMENTO_ENVIO_ATIVO)."
)
MOTIVO_AVALIACAO_SO_PESSOA = (
    "A resposta à avaliação é pública: só pessoa responde (a IA não envia)."
)

# Amazon cujo e-mail não disse a conta: a pessoa escolhe a conta na tela
# (PATCH /conversas/{id} com `integration_id`), e aí dá para responder.
MOTIVO_AMAZON_SEM_CONTA = "Escolha de qual conta Amazon é esta conversa."

# Similaridade (difflib, 0–1) a partir da qual "a pessoa enviou a sugestão
# como veio". Abaixo disso ela EDITOU — e a versão dela é a que ensina.
LIMIAR_ENVIOU_IGUAL = 0.97

# Teto de espera pela plataforma. Os clientes têm timeout próprio; este é o
# cinto de segurança para um adaptador que pendure. Estourou = ambíguo.
TEMPO_MAXIMO_ENVIO_S = 90

# Linha `enviando` mais velha que isto é de um processo que morreu no meio
# (deploy, kill). Vira `revisar` — pode ter saído — e solta a trava de
# "uma em voo por conversa", senão a conversa ficaria sem resposta para sempre.
ENVIO_PRESO = timedelta(minutes=10)

# A mesma resposta de novo nesta janela é clique duplo (ou aba duplicada),
# não intenção: o comprador receberia a frase duas vezes.
JANELA_REPETIDO = timedelta(minutes=2)
_STATUS_QUE_SAIRAM = (MSG_ENVIANDO, MSG_ENVIADA, MSG_REVISAR)

# Erros de banco no passo 3 que se resolvem tentando de novo: deadlock,
# serialização e o UNIQUE do id da plataforma (o sync gravou a mesma
# mensagem no meio). A mensagem JÁ SAIU — nunca devolver 500 por isso.
_SQLSTATE_TRANSITORIO = ("40P01", "40001", "23505")
TENTATIVAS_RESULTADO = 3


class EnvioRecusado(Exception):  # noqa: N818 — nome do contrato (spec seção 5)
    """O envio NÃO aconteceu, por uma trava nossa (nada foi à plataforma).

    `code` é estável (a tela traduz); `detail` é o texto para a pessoa ou,
    em `texto_invalido`, a lista de motivos do validador.
    """

    def __init__(self, code: str, detail: str | list[str] = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(code)


def adaptador(plataforma: str) -> ModuleType:
    """O módulo adaptador da plataforma (import tardio pelo `ADAPTADORES`)."""
    return importlib.import_module(ADAPTADORES[plataforma])


def _sabe_auto_reply(enviar_parte: Any) -> bool:
    """A `enviar_parte` do adaptador manda como RESPOSTA AUTOMÁTICA (o `auto_reply`)?"""
    if not callable(enviar_parte):
        return False
    try:
        return "auto_reply" in inspect.signature(enviar_parte).parameters
    except (TypeError, ValueError):
        return False


def auto_reply_no_adaptador(plataforma: str = "shopee") -> bool:
    """O adaptador sabe mandar como RESPOSTA AUTOMÁTICA (`enviar_parte(auto_reply=True)`)?

    Só lê o módulo — o mesmo `adaptador` que manda (nada vai à plataforma). O
    da Shopee sabe desde 05/10/2026 (`send_autoreply_message`); sem ele, a
    campanha não sai nem com a chave ligada (nunca como mensagem normal).
    """
    try:
        modulo = adaptador(plataforma)
    except (KeyError, ImportError):
        return False
    return _sabe_auto_reply(getattr(modulo, "enviar_parte", None))


def campanha_por_auto_reply() -> bool:
    """A campanha da Shopee pode sair: o `auto_reply` confirmado E o envio por ele.

    A CHAVE primeiro (`atendimento_automacoes_shopee_auto_reply`, o teste de
    permissão passou): desligada, a campanha não sai — com o adaptador pronto
    ou não.
    """
    return bool(get_settings().atendimento_automacoes_shopee_auto_reply) and (
        auto_reply_no_adaptador("shopee")
    )


@dataclass
class _Destino:
    """Por onde a resposta sai: o canal (modo) e a loja (credenciais)."""

    canal: AtendimentoCanal
    integration: Integration


# ── Travas ────────────────────────────────────────────────────────────────


async def _destino(
    session: AsyncSession, conversa: AtendimentoConversa, *, origem: str
) -> _Destino:
    """Confere as travas que não dependem do texto; levanta `EnvioRecusado`.

    A ordem é a da explicação para a pessoa: primeiro o que é do sistema
    (somente leitura, envio desligado), depois o que é da conversa
    (bloqueada), depois o que é da loja (sem integração, em observação).

    Canal e loja são RELIDOS do banco (`populate_existing`): a IA roda
    minutos com a mesma sessão, e a loja posta em `observar` (ou a categoria
    tirada do automático) no meio disso tem que valer já.
    """
    from app.services.mail_atendimento.ponte import e_conversa_da_ponte

    if e_conversa_da_ponte(conversa):
        # E-mail da ponte (08/10/2026): só sai pela fila da Central
        # (`enviar_resposta` → `responder.enfileirar_resposta`). Quem chega
        # aqui com ele (foto, automática, um caminho novo) é recusado.
        raise EnvioRecusado(RECUSA_CANAL_SEM_ENVIO, MOTIVO_EMAIL_SO_RESPOSTA)
    motivo_sem_envio = motivo_canal_sem_envio(conversa.canal, conversa.plataforma, conversa.dados)
    if motivo_sem_envio:
        # E-mail do Tuta e Zap (05/10/2026): a PRIMEIRA trava, antes até do
        # envio desligado (a tela diz o porquê de verdade) e de qualquer
        # adaptador — com a plataforma da venda e o canal da loja, a resposta
        # iria pelo chat do marketplace para o e-mail/telefone do contato.
        raise EnvioRecusado(RECUSA_CANAL_SEM_ENVIO, motivo_sem_envio)
    if conversa.plataforma in PLATAFORMAS_ROBO:
        # Temu/AliExpress: o robô do Mac mini só LÊ o Seller Center (enviar
        # marcaria a conversa como lida e desligaria o robô da Temu). A
        # sugestão da IA fica na tela para copiar.
        raise EnvioRecusado(RECUSA_SOMENTE_LEITURA, motivo_seller_center(conversa.plataforma))
    if conversa.plataforma == PLATAFORMA_SITE:
        # Carrinho abandonado do site (02/10/2026): nada vai ao lojista pelo
        # DaVinci (sem lembrete por Zap/e-mail por enquanto).
        raise EnvioRecusado(RECUSA_SOMENTE_LEITURA, MOTIVO_CARRINHO_SO_LEITURA)
    if conversa.plataforma in PLATAFORMAS_REDE:
        # Comentário das redes (02/10/2026): a resposta é pelo cartão da
        # publicação (routers/atendimento_redes.py), não pela caixa do chat.
        raise EnvioRecusado(RECUSA_SOMENTE_LEITURA, MOTIVO_COMENTARIO_PELO_CARTAO)
    if conversa.plataforma not in ADAPTADORES:
        raise EnvioRecusado(
            RECUSA_SOMENTE_LEITURA,
            "Esta conversa é só de leitura aqui: responda pela própria plataforma.",
        )
    settings = get_settings()
    if settings.atendimento_simulador and settings.is_prod:
        # O simulador finge que saiu. Em produção isso é a conversa sair da
        # fila com o comprador sem resposta — recusa, e alto no log.
        logger.error("atendimento_simulador_em_producao", conversa_id=str(conversa.id))
        raise EnvioRecusado(
            RECUSA_SIMULADOR_EM_PRODUCAO,
            "O simulador de envio (só para teste local) está ligado em produção: "
            "nada foi enviado. Desligue ATENDIMENTO_SIMULADOR.",
        )
    if not settings.atendimento_envio_ativo:
        raise EnvioRecusado(
            RECUSA_ENVIO_DESLIGADO,
            MOTIVO_AVALIACAO_ENVIO_DESLIGADO
            if conversa.canal == CANAL_AVALIACAO
            else "O envio pelo DaVinci está desligado (ATENDIMENTO_ENVIO_ATIVO).",
        )
    if conversa.situacao == CONVERSA_BLOQUEADA:
        raise EnvioRecusado(
            RECUSA_CONVERSA_BLOQUEADA,
            conversa.bloqueio_motivo
            or "A plataforma não deixa mais responder nesta conversa.",
        )
    limite = conversa.pode_enviar_ate
    if limite is not None and limite.tzinfo is None:
        limite = limite.replace(tzinfo=UTC)
    if limite is not None and datetime.now(UTC) > limite:
        raise EnvioRecusado(
            RECUSA_CONVERSA_BLOQUEADA,
            "A janela de resposta da plataforma para esta conversa já fechou.",
        )
    if conversa.canal == CANAL_AVALIACAO:
        return await _destino_avaliacao(session, conversa, origem=origem)
    if conversa.plataforma == "amazon" and conversa.integration_id is None:
        raise EnvioRecusado(RECUSA_SEM_INTEGRACAO, MOTIVO_AMAZON_SEM_CONTA)
    canal = (
        await session.get(AtendimentoCanal, conversa.canal_id, populate_existing=True)
        if conversa.canal_id is not None
        else None
    )
    integration = (
        await session.get(Integration, canal.integration_id, populate_existing=True)
        if canal is not None
        else None
    )
    if canal is None or integration is None or integration.archived_at is not None:
        raise EnvioRecusado(
            RECUSA_SEM_INTEGRACAO,
            "A loja desta conversa não está mais conectada ao DaVinci.",
        )
    if canal.modo not in MODOS_QUE_ENVIAM:
        raise EnvioRecusado(
            RECUSA_CANAL_EM_OBSERVACAO,
            "Esta loja está em modo observar: quem responde é o Duoke/Seller Center.",
        )
    if origem == ORIGEM_IA:
        if not settings.atendimento_auto_ativo or canal.modo != MODO_AUTO:
            # Cinto de segurança: quem decide o automático é a IA (ia.py), mas
            # resposta sem pessoa só sai com o automático ligado nos DOIS lugares.
            raise EnvioRecusado(
                RECUSA_AUTO_DESLIGADO,
                "Envio automático desligado para esta loja.",
            )
        motivo = _motivo_ia_sem_vez(conversa)
        if motivo:
            raise EnvioRecusado(RECUSA_NAO_AGUARDA, motivo)
    return _Destino(canal=canal, integration=integration)


async def _destino_avaliacao(
    session: AsyncSession, conversa: AtendimentoConversa, *, origem: str
) -> _Destino:
    """As travas da resposta à AVALIAÇÃO (a conversa não tem canal próprio).

    Só pessoa (a resposta é pública); só onde a plataforma deixa responder
    pela API; a loja é a integração da conversa, e o modo é o do canal da
    loja (em `observar`, quem responde é o de fora — como no chat).
    """
    if origem == ORIGEM_IA:
        raise EnvioRecusado(RECUSA_AUTO_DESLIGADO, MOTIVO_AVALIACAO_SO_PESSOA)
    if conversa.plataforma not in PLATAFORMAS_RESPONDEM_AVALIACAO:
        raise EnvioRecusado(
            RECUSA_SOMENTE_LEITURA,
            conversa.bloqueio_motivo
            or "Esta plataforma não deixa responder a avaliação pela API.",
        )
    from app.services.atendimento import avaliacoes

    avaliacao = await avaliacoes.avaliacao_da_conversa(session, conversa)
    if avaliacao is not None and avaliacao.resposta_loja:
        raise EnvioRecusado(RECUSA_AVALIACAO_JA_RESPONDIDA, "Esta avaliação já foi respondida.")
    integration = (
        await session.get(Integration, conversa.integration_id, populate_existing=True)
        if conversa.integration_id is not None
        else None
    )
    if integration is None or integration.archived_at is not None:
        raise EnvioRecusado(
            RECUSA_SEM_INTEGRACAO,
            "A loja desta avaliação não está mais conectada ao DaVinci.",
        )
    canais = (
        (
            await session.execute(
                select(AtendimentoCanal)
                .where(AtendimentoCanal.integration_id == integration.id)
                .order_by(AtendimentoCanal.canal)
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .all()
    )
    canal = next((c for c in canais if c.modo in MODOS_QUE_ENVIAM), None)
    if canal is None:
        raise EnvioRecusado(
            RECUSA_CANAL_EM_OBSERVACAO,
            "Esta loja está em modo observar: quem responde a avaliação é o de fora "
            "(Seller Center ou o robô da loja).",
        )
    return _Destino(canal=canal, integration=integration)


def _motivo_ia_sem_vez(conversa: AtendimentoConversa) -> str | None:
    """Por que a IA não responde ESTA conversa agora (None = pode).

    A pessoa pode escrever de novo numa conversa respondida ou fechada — ela
    sabe o que faz. A IA não: a tela pode ter pausado a IA, fechado a conversa
    ou marcado "não precisa de resposta" enquanto o modelo escrevia, e a loja
    (inclusive a própria IA, numa rodada paralela) pode já ter respondido.
    """
    if conversa.ia_pausada:
        return "A IA está pausada nesta conversa."
    if conversa.situacao == CONVERSA_FECHADA:
        return "A conversa foi fechada."
    if conversa.sem_resposta_necessaria:
        return "A conversa foi marcada como não precisa de resposta."
    if not conversa.aguardando_resposta:
        return "A conversa já foi respondida."
    if reclamacao_aberta(conversa.dados):
        return "Há reclamação/mediação aberta no ML: quem responde é pessoa."
    if conversa.plataforma in PLATAFORMAS_ROBO:
        return motivo_seller_center(conversa.plataforma)
    if conversa.plataforma in PLATAFORMAS_SEM_AUTO:
        return (
            "Na Amazon a IA não responde sozinha: a resposta dada no Seller Central"
            " não chega ao DaVinci."
        )
    return None


def _momento_col():
    return func.coalesce(AtendimentoMensagem.enviada_em, AtendimentoMensagem.created_at)


async def _tem_envio_em_voo(session: AsyncSession, conversa_id: UUID) -> bool:
    """Há resposta `enviando` RECENTE? A presa (processo morto) não trava a tela.

    A linha `enviando` mais velha que `ENVIO_PRESO` é de um envio que morreu
    no meio; o próprio envio a aposenta (vira `revisar`) antes de gravar a
    nova. Sem este corte, com a leitura desligada ninguém a aposentaria, e a
    tela travaria a caixa de resposta para sempre. A resposta de e-mail com o
    job VIVO na fila da Central (o Mac pode levar mais) continua em voo.
    """
    from app.services.mail_atendimento.responder import fila_viva_existe

    return (
        await session.scalar(
            select(AtendimentoMensagem.id)
            .where(
                AtendimentoMensagem.conversa_id == conversa_id,
                AtendimentoMensagem.status == MSG_ENVIANDO,
                or_(
                    AtendimentoMensagem.created_at >= datetime.now(UTC) - ENVIO_PRESO,
                    fila_viva_existe(),
                ),
            )
            .limit(1)
        )
    ) is not None


async def motivo_para_nao_enviar(
    session: AsyncSession, conversa: AtendimentoConversa, *, origem: str = ORIGEM_HUMANO
) -> EnvioRecusado | None:
    """A recusa que o envio daria AGORA (sem olhar o texto); None = pode enviar.

    É o que a tela mostra na faixa acima da caixa de resposta — as MESMAS
    travas do envio, para a faixa nunca dizer "pode" e o botão dizer "não".
    No e-mail da ponte, as travas dele (`responder.motivo_sem_envio`).
    """
    from app.services.mail_atendimento.ponte import e_conversa_da_ponte

    if e_conversa_da_ponte(conversa):
        from app.services.mail_atendimento import responder

        recusa = await responder.motivo_sem_envio(session, conversa, origem=origem)
        if recusa is not None:
            return recusa
        if await _tem_envio_em_voo(session, conversa.id):
            return EnvioRecusado(
                RECUSA_ENVIO_EM_ANDAMENTO, "Há uma resposta de e-mail na fila nesta conversa."
            )
        return None
    try:
        await _destino(session, conversa, origem=origem)
    except EnvioRecusado as recusa:
        return recusa
    if await _tem_envio_em_voo(session, conversa.id):
        return EnvioRecusado(
            RECUSA_ENVIO_EM_ANDAMENTO, "Há uma resposta sendo enviada nesta conversa."
        )
    return None


async def travar_conversa(session: AsyncSession, conversa: AtendimentoConversa) -> None:
    """Trava e relê a conversa, esperando no máximo 5 s; ocupada → `conversa_ocupada`.

    A rodada do sync segura a conversa enquanto grava (e fala com a API da
    loja): em vez de a tela pendurar, a pessoa ouve "tente de novo".
    """
    if not await gravar.travar_linha(session, conversa, espera=gravar.ESPERA_TRAVA_TELA):
        raise EnvioRecusado(
            RECUSA_CONVERSA_OCUPADA,
            "A leitura da loja está gravando esta conversa agora. Tente de novo em "
            "alguns segundos.",
        )


async def aposentar_envios_presos(
    session: AsyncSession, *, conversa_id: UUID | None = None
) -> int:
    """`enviando` esquecido por processo morto vira `revisar`; devolve quantos.

    Sem isto, um deploy no meio de um envio deixaria a conversa com uma linha
    em voo para sempre — e o índice de "uma em voo" recusaria toda resposta
    seguinte. `revisar`, e não `falhou`: a plataforma pode ter recebido.
    A resposta de e-mail com o job VIVO na fila da Central não é "parada":
    o Mac está cuidando dela (o recibo dele decide). Nunca commita.
    """
    from app.services.mail_atendimento.responder import fila_viva_existe

    q = (
        update(AtendimentoMensagem)
        .where(
            AtendimentoMensagem.status == MSG_ENVIANDO,
            AtendimentoMensagem.created_at < datetime.now(UTC) - ENVIO_PRESO,
            ~fila_viva_existe(),
        )
        .values(status=MSG_REVISAR, erro="envio_interrompido")
        .execution_options(synchronize_session=False)
    )
    if conversa_id is not None:
        q = q.where(AtendimentoMensagem.conversa_id == conversa_id)
    resultado = await session.execute(q)
    presos = int(resultado.rowcount or 0)
    if presos:
        logger.warning(
            "atendimento_envio_preso_revisar",
            conversa_id=str(conversa_id) if conversa_id else None,
            quantidade=presos,
        )
    return presos


# ── Texto ─────────────────────────────────────────────────────────────────


def _preparar_texto(texto: str | None, *, conversa: AtendimentoConversa, origem: str) -> str:
    """Normaliza e valida; levanta `texto_invalido` com os motivos.

    O validador é do lote do cérebro e é importado na hora: o router (que o
    app carrega sempre) não depende dele para subir.
    """
    from app.services.atendimento import validador

    normalizado = validador.normalizar(
        texto or "", plataforma=conversa.plataforma, canal=conversa.canal
    )
    motivos = list(
        validador.validar(
            normalizado, plataforma=conversa.plataforma, canal=conversa.canal, origem=origem
        )
    )
    if not (normalizado or "").strip() and not motivos:
        motivos = ["A resposta está vazia."]
    if motivos:
        raise EnvioRecusado(RECUSA_TEXTO_INVALIDO, motivos)
    return normalizado


# Mora no `gravar` (a resposta de fora também é comparada lá); o nome fica
# aqui porque é o que o router e a semente usam.
similaridade = gravar.similaridade


# ── Plataforma ────────────────────────────────────────────────────────────


def fora_do_simulador() -> list[str]:
    """As plataformas que escapam do simulador ligado (vazio sem o simulador).

    Um lugar só para o envio (`vai_para_o_simulador`) e a tela (`/resumo`):
    se cada um normalizasse a lista do seu jeito, a faixa diria "NÃO chega
    ao comprador" numa plataforma que manda de verdade.
    """
    s = get_settings()
    if not s.atendimento_simulador:
        return []
    return sorted(
        {p.strip().lower() for p in (s.atendimento_simulador_exceto or "").split(",") if p.strip()}
    )


def vai_para_o_simulador(plataforma: str) -> bool:
    """O envio desta plataforma cai no simulador (e não sai de verdade)?

    Com `atendimento_simulador`, tudo cai nele MENOS as plataformas de
    `atendimento_simulador_exceto`. A exceção existe para o teste local de
    responder pela tela a um e-mail REAL da Amazon (SMTP do Gmail) com a
    Shopee, o ML e o TikTok ainda fingindo — sem ela, ligar o envio real de
    uma loja exigiria desligar o simulador de todas.
    """
    if not get_settings().atendimento_simulador:
        return False
    return plataforma not in fora_do_simulador()


async def _chamar_plataforma(
    session: AsyncSession,
    conversa: AtendimentoConversa,
    integration: Integration,
    texto: str,
) -> ResultadoEnvio:
    """Fala com a plataforma. Nunca levanta: o que der errado vira resultado.

    A mensagem automática não passa por aqui: `_chamar_plataforma_automatica`
    (a parte, o `to_id`, o `auto_reply` da campanha).
    """
    s = get_settings()
    if s.atendimento_simulador and s.is_prod:
        # Cinto (o `_destino` já recusa): em produção o simulador NUNCA
        # finge sucesso — e também não manda de verdade, nem para plataforma
        # da exceção, porque quem ligou o simulador achava que nada sairia.
        return ResultadoEnvio(ok=False, erro="simulador_em_producao")
    if vai_para_o_simulador(conversa.plataforma):
        return ResultadoEnvio(
            ok=True, externo_id=f"sim:{uuid4()}", payload={"simulador": True}
        )
    try:
        # Amazon responde por e-mail (SMTP pelos settings), não pela API da
        # loja: montar o cliente SP-API ali só daria um jeito a mais de falhar.
        cliente = (
            None
            if conversa.plataforma == "amazon"
            else await clientes.cliente_da_integracao(integration)
        )
    except Exception as e:  # noqa: BLE001
        # Nada saiu: a falha foi ao montar o cliente (credencial, token).
        return ResultadoEnvio(ok=False, erro=f"cliente_indisponivel: {type(e).__name__}")
    try:
        modulo = adaptador(conversa.plataforma)
        if conversa.canal == CANAL_AVALIACAO:
            # A resposta PÚBLICA à avaliação (RF8): outro endpoint que o chat.
            responder = getattr(modulo, "responder_avaliacao", None)
            if responder is None:
                return ResultadoEnvio(ok=False, erro="sem_resposta_de_avaliacao")
            return await asyncio.wait_for(
                responder(session, conversa, integration, cliente, texto),
                timeout=TEMPO_MAXIMO_ENVIO_S,
            )
        return await asyncio.wait_for(
            modulo.enviar_texto(session, conversa, integration, cliente, texto),
            timeout=TEMPO_MAXIMO_ENVIO_S,
        )
    except TimeoutError:
        return ResultadoEnvio(ok=False, ambiguo=True, erro="timeout")
    except Exception as e:  # noqa: BLE001
        # O adaptador promete não levantar; se levantou, não sabemos se a
        # plataforma recebeu — é exatamente o caso ambíguo.
        return ResultadoEnvio(ok=False, ambiguo=True, erro=f"erro_inesperado: {type(e).__name__}")


async def _externo_id_livre(
    session: AsyncSession, mensagem: AtendimentoMensagem, externo_id: str
) -> bool:
    """O id da plataforma ainda não está em OUTRA linha desta conversa?

    Só acontece se o sync gravou a nossa resposta como `externo` (texto que a
    plataforma devolveu diferente demais para ser adotado) no meio do envio.
    Gravar o mesmo id aqui estouraria o UNIQUE e deixaria a linha `enviando`.
    """
    dono = await session.scalar(
        select(AtendimentoMensagem.id).where(
            AtendimentoMensagem.conversa_id == mensagem.conversa_id,
            AtendimentoMensagem.externo_id == externo_id,
            AtendimentoMensagem.id != mensagem.id,
        )
    )
    return dono is None


async def _aplicar_resultado(
    session: AsyncSession,
    conversa: AtendimentoConversa,
    mensagem: AtendimentoMensagem,
    resultado: ResultadoEnvio,
) -> None:
    """Grava o que a plataforma disse na linha (e na conversa, se bloqueou)."""
    payload = {**(mensagem.payload or {}), "envio": dict(resultado.payload or {})}
    adotada = mensagem.status == MSG_ENVIADA and mensagem.externo_id is not None
    if adotada:
        # O sync já trouxe a nossa resposta de volta e a adotou: é a prova de
        # que saiu, valha o que valer o retorno (mesmo um timeout).
        mensagem.payload = payload
        return
    if resultado.ok:
        mensagem.status = MSG_ENVIADA
        mensagem.erro = None
        mensagem.enviada_em = datetime.now(UTC)
        externo_id = str(resultado.externo_id) if resultado.externo_id else None
        if externo_id and await _externo_id_livre(session, mensagem, externo_id):
            mensagem.externo_id = externo_id
        elif externo_id:
            logger.warning(
                "atendimento_envio_externo_id_repetido",
                conversa_id=str(conversa.id),
                mensagem_id=str(mensagem.id),
            )
    elif resultado.ambiguo:
        mensagem.status = MSG_REVISAR
        mensagem.erro = (resultado.erro or "envio_ambiguo")[:500]
    else:
        mensagem.status = MSG_FALHOU
        mensagem.erro = (resultado.erro or resultado.bloqueio or "envio_falhou")[:500]
    mensagem.payload = payload
    url = (resultado.payload or {}).get("imagem_url")
    if resultado.ok and mensagem.tipo == "imagem" and isinstance(url, str) and url.startswith("https://"):
        # A foto que saiu: o balão mostra a imagem pela URL da plataforma
        # (a nossa cópia nunca é guardada).
        primeiro = (mensagem.anexos or [{}])[0]
        mensagem.anexos = [{**(primeiro if isinstance(primeiro, dict) else {}), "url": url}]
    if resultado.bloqueio:
        conversa.situacao = CONVERSA_BLOQUEADA
        conversa.bloqueio_motivo = resultado.bloqueio[:500]


# ── Rascunho ──────────────────────────────────────────────────────────────


async def _rascunho_da_conversa(
    session: AsyncSession, conversa: AtendimentoConversa, rascunho_id: UUID | None
) -> AtendimentoRascunho | None:
    if rascunho_id is None:
        return None
    rascunho = await session.get(AtendimentoRascunho, rascunho_id)
    if rascunho is None or rascunho.conversa_id != conversa.id:
        # Rascunho de outra conversa (aba velha, id trocado): não se avalia
        # sugestão alheia com o texto desta.
        logger.warning(
            "atendimento_envio_rascunho_alheio",
            conversa_id=str(conversa.id),
            rascunho_id=str(rascunho_id),
        )
        return None
    return rascunho


def _promover_observacao(av: AtendimentoAvaliacao, campos: dict) -> None:
    """A nota do modo observação ganha a ação de verdade; a nota e a correção ficam.

    A pessoa deu 👍/👎 na sugestão ainda pendente (`observou`) e depois ela
    saiu pelo DaVinci (ou foi substituída pela resposta escrita do zero): o
    que conta para a IA é o que saiu — `enviou_igual`/`editou` com o texto
    final vira exemplo, desde que a nota não tenha sido 👎.
    """
    av.acao = campos["acao"]
    av.texto_final = campos.get("texto_final")
    av.similaridade = campos.get("similaridade")
    if av.user_id is None:
        av.user_id = campos.get("user_id")


async def _gravar_avaliacao(session: AsyncSession, **campos) -> None:
    """Uma avaliação por rascunho (UNIQUE): a segunda tentativa não derruba o envio.

    A que já existe só muda se for a provisória do modo observação (`observou`).
    """
    ja_tem = await _avaliacao_de(session, campos["rascunho_id"])
    if ja_tem is not None:
        if ja_tem.acao == AVALIACAO_OBSERVOU:
            _promover_observacao(ja_tem, campos)
        return
    await session.flush()
    try:
        async with session.begin_nested():
            session.add(AtendimentoAvaliacao(**campos))
            await session.flush()
    except IntegrityError:
        # Corrida com a nota da tela (dois cliques): a linha dela ganhou.
        # Se for a provisória, recebe a ação de verdade.
        logger.info("atendimento_avaliacao_ja_existia", rascunho_id=str(campos["rascunho_id"]))
        outra = await _avaliacao_de(session, campos["rascunho_id"])
        if outra is not None and outra.acao == AVALIACAO_OBSERVOU:
            _promover_observacao(outra, campos)


async def _pendente_da_conversa(
    session: AsyncSession, conversa: AtendimentoConversa
) -> AtendimentoRascunho | None:
    return (
        await session.execute(
            select(AtendimentoRascunho)
            .where(
                AtendimentoRascunho.conversa_id == conversa.id,
                AtendimentoRascunho.status == RASCUNHO_PENDENTE,
            )
            .limit(1)
        )
    ).scalar_one_or_none()


async def _avaliar_rascunho(
    session: AsyncSession,
    conversa: AtendimentoConversa,
    mensagem: AtendimentoMensagem,
    *,
    rascunho: AtendimentoRascunho | None,
    texto_digitado: str,
    user: User | None,
    origem: str,
) -> None:
    """Registra o que a pessoa fez com a sugestão da IA.

    - veio `rascunho_id`: similaridade ≥ 0.97 → `enviou_igual`, senão
      `editou` — com o texto que SAIU (é ele que vira exemplo). Vale também
      para a sugestão que ficou `substituido` enquanto a pessoa escrevia (aba
      aberta antes de a IA refazer): ela USOU aquele texto, e é isso que conta;
    - não veio, mas havia sugestão pendente: ela fica `substituido` e a
      avaliação é `escreveu_do_zero`;
    - veio uma sugestão velha e há OUTRA pendente (a IA refez no meio): a
      pendente sai da caixa (`substituido`) sem avaliação — a pessoa nem a
      viu, não é um "não serviu".

    Em todos os casos, depois de uma resposta da loja nenhuma sugestão fica
    pendente: ela seria para uma pergunta já respondida, e a tela a poria na
    caixa de novo (o comprador receberia duas respostas).

    Envio AUTOMÁTICO (origem IA) marca o rascunho `enviado` e NÃO cria
    avaliação: exemplo aprovado é o que PESSOA aprovou. Se a IA contasse
    as próprias respostas como aprovadas, ela se ensinaria sozinha — e o
    manual só muda por pessoa.
    """
    if origem == ORIGEM_IA:
        if rascunho is not None and rascunho.status == RASCUNHO_PENDENTE:
            rascunho.status = RASCUNHO_ENVIADO
        return
    avaliado = False
    if (
        rascunho is not None
        and rascunho.status in (RASCUNHO_PENDENTE, RASCUNHO_SUBSTITUIDO)
        and not await _ja_avaliado(session, rascunho.id)
    ):
        sim = similaridade(texto_digitado, rascunho.texto)
        igual = sim >= LIMIAR_ENVIOU_IGUAL
        rascunho.status = RASCUNHO_ENVIADO if igual else RASCUNHO_EDITADO
        await _gravar_avaliacao(
            session,
            rascunho_id=rascunho.id,
            acao="enviou_igual" if igual else "editou",
            texto_final=mensagem.texto,
            similaridade=sim,
            user_id=user.id if user else None,
        )
        avaliado = True
    if origem != ORIGEM_HUMANO:
        return
    pendente = await _pendente_da_conversa(session, conversa)
    if pendente is None or (rascunho is not None and pendente.id == rascunho.id):
        return
    pendente.status = RASCUNHO_SUBSTITUIDO
    if rascunho is None and not avaliado:
        # A sugestão estava na caixa e a pessoa escreveu outra coisa.
        await _gravar_avaliacao(
            session,
            rascunho_id=pendente.id,
            acao="escreveu_do_zero",
            texto_final=mensagem.texto,
            similaridade=similaridade(texto_digitado, pendente.texto),
            user_id=user.id if user else None,
        )


async def _avaliacao_de(
    session: AsyncSession, rascunho_id: UUID
) -> AtendimentoAvaliacao | None:
    return (
        await session.execute(
            select(AtendimentoAvaliacao).where(AtendimentoAvaliacao.rascunho_id == rascunho_id)
        )
    ).scalar_one_or_none()


async def _ja_avaliado(session: AsyncSession, rascunho_id: UUID) -> bool:
    """Já tem a avaliação DE VERDADE? A nota do modo observação não conta."""
    av = await _avaliacao_de(session, rascunho_id)
    return av is not None and av.acao != AVALIACAO_OBSERVOU


# ── Conferências sob a trava ──────────────────────────────────────────────


async def _conferir_repetido(
    session: AsyncSession, conversa: AtendimentoConversa, normalizado: str
) -> None:
    """A mesma resposta já saiu (ou está saindo) nesta conversa há pouco? → `envio_repetido`.

    Duplo clique, aba duplicada, a pessoa e a IA com a mesma frase: o índice
    de "uma em voo" só barra o que é SIMULTÂNEO; depois que a primeira saiu,
    a segunda passaria. Compara pelo texto normalizado (espaço, acento e
    pontuação não fazem duas respostas diferentes).
    """
    alvo = gravar.normalizar_para_comparar(normalizado)
    if not alvo:
        return
    textos = (
        await session.execute(
            select(AtendimentoMensagem.texto).where(
                AtendimentoMensagem.conversa_id == conversa.id,
                AtendimentoMensagem.origem.in_(ORIGENS_DAVINCI),
                AtendimentoMensagem.status.in_(_STATUS_QUE_SAIRAM),
                AtendimentoMensagem.created_at >= datetime.now(UTC) - JANELA_REPETIDO,
            )
        )
    ).scalars()
    if any(gravar.normalizar_para_comparar(t) == alvo for t in textos):
        raise EnvioRecusado(
            RECUSA_ENVIO_REPETIDO,
            "Esta mesma resposta acabou de ser enviada nesta conversa (há menos de "
            "2 minutos). Ela não foi enviada de novo.",
        )


async def _resposta_da_loja_depois(
    session: AsyncSession, conversa: AtendimentoConversa, referencia: AtendimentoMensagem
) -> AtendimentoMensagem | None:
    """A resposta da loja (que não falhou) mais recente DEPOIS de `referencia`."""
    momento = _momento_col()
    quando = referencia.enviada_em or referencia.created_at
    return (
        await session.execute(
            select(AtendimentoMensagem)
            .where(
                AtendimentoMensagem.conversa_id == conversa.id,
                AtendimentoMensagem.autor == AUTOR_LOJA,
                AtendimentoMensagem.status != MSG_FALHOU,
                AtendimentoMensagem.id != referencia.id,
                momento >= quando,
            )
            .order_by(momento.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def _conferir_mudou(
    session: AsyncSession, conversa: AtendimentoConversa, ultima_vista_id: UUID
) -> None:
    """Alguém respondeu depois da última mensagem que a pessoa VIU? → `conversa_mudou`.

    Duas pessoas no mesmo filtro abrem a mesma conversa; uma envia, a outra
    continua escrevendo em cima da sugestão. Sem esta conferência o comprador
    recebe duas respostas. Id estranho (de outra conversa, apagado) não trava:
    a tela antiga que não manda o id também não trava.
    """
    vista = await session.get(AtendimentoMensagem, ultima_vista_id)
    if vista is None or vista.conversa_id != conversa.id:
        return
    outra = await _resposta_da_loja_depois(session, conversa, vista)
    if outra is None:
        return
    if outra.origem == ORIGEM_HUMANO and outra.autor_user_id is not None:
        autor = await session.get(User, outra.autor_user_id)
        quem = (autor.name or autor.email or "Alguém da equipe") if autor else "Alguém da equipe"
    elif outra.origem == ORIGEM_IA:
        quem = "A IA"
    elif outra.origem == ORIGEM_AUTO:
        quem = "A mensagem automática do DaVinci"
    elif outra.origem == ORIGEM_EXTERNO:
        quem = "Alguém fora do DaVinci (Duoke/Seller Center)"
    else:
        quem = "A loja"
    hora = (outra.enviada_em or outra.created_at).astimezone(_SP).strftime("%H:%M")
    raise EnvioRecusado(
        RECUSA_CONVERSA_MUDOU,
        f"{quem} respondeu às {hora} enquanto você escrevia. Confira a conversa antes de "
        "enviar.",
    )


async def _conferir_gatilho_ia(
    session: AsyncSession, conversa: AtendimentoConversa, rascunho: AtendimentoRascunho | None
) -> None:
    """IA: a loja já respondeu a pergunta que esta sugestão responde? → `nao_aguarda`.

    Duas rodadas da IA na mesma conversa (a do minuto anterior demorou)
    geram duas sugestões para a MESMA pergunta. A primeira sai; a segunda
    chega aqui com a trava da conversa e encontra a resposta dela.
    """
    if rascunho is None or rascunho.mensagem_gatilho_id is None:
        return
    if rascunho.status != RASCUNHO_PENDENTE:
        raise EnvioRecusado(RECUSA_NAO_AGUARDA, "A sugestão não está mais pendente.")
    gatilho = await session.get(AtendimentoMensagem, rascunho.mensagem_gatilho_id)
    if gatilho is not None and await _resposta_da_loja_depois(session, conversa, gatilho):
        raise EnvioRecusado(RECUSA_NAO_AGUARDA, "A conversa já foi respondida.")


# ── Envio ─────────────────────────────────────────────────────────────────


async def _preparar_envio(
    session: AsyncSession,
    conversa: AtendimentoConversa,
    texto: str,
    *,
    user: User | None,
    rascunho_id: UUID | None,
    origem: str,
    ultima_vista_id: UUID | None,
    confirmar: bool,
) -> tuple[AtendimentoMensagem, _Destino, str, AtendimentoRascunho | None]:
    """Passos 0 e 1 do envio: trava, confere e grava a linha em voo (sem commit)."""
    await travar_conversa(session, conversa)
    destino = await _destino(session, conversa, origem=origem)
    normalizado = _preparar_texto(texto, conversa=conversa, origem=origem)
    rascunho = await _rascunho_da_conversa(session, conversa, rascunho_id)
    if rascunho is not None:
        await session.refresh(rascunho)
    if origem == ORIGEM_IA:
        await _conferir_gatilho_ia(session, conversa, rascunho)
    await _conferir_repetido(session, conversa, normalizado)
    if ultima_vista_id is not None and not confirmar:
        await _conferir_mudou(session, conversa, ultima_vista_id)

    # ── 1. a linha em voo (commitada por quem chamou ANTES da plataforma) ──
    await aposentar_envios_presos(session, conversa_id=conversa.id)
    mensagem = AtendimentoMensagem(
        conversa_id=conversa.id,
        externo_id=None,
        autor=AUTOR_LOJA,
        origem=origem,
        autor_user_id=user.id if user is not None else None,
        tipo="texto",
        texto=normalizado,
        anexos=[],
        # Relógio da PLATAFORMA: só existe depois que ela aceitar.
        enviada_em=None,
        status=MSG_ENVIANDO,
        rascunho_id=rascunho.id if rascunho is not None else None,
        payload={},
    )
    await session.flush()  # pendências de quem chamou fora do SAVEPOINT
    try:
        async with session.begin_nested():
            session.add(mensagem)
            await session.flush()
    except IntegrityError as e:
        if not is_unique_violation(e):
            raise  # FK (conversa apagada no meio) não é "envio em andamento"
        raise EnvioRecusado(
            RECUSA_ENVIO_EM_ANDAMENTO, "Há uma resposta sendo enviada nesta conversa."
        ) from e
    return mensagem, destino, normalizado, rascunho


async def enviar_resposta(
    session: AsyncSession,
    conversa: AtendimentoConversa,
    texto: str,
    *,
    user: User | None,
    rascunho_id: UUID | None = None,
    origem: str = ORIGEM_HUMANO,
    ultima_vista_id: UUID | None = None,
    confirmar: bool = False,
    confirmar_nao_responde: bool = False,
    mail_message_id: UUID | None = None,
) -> AtendimentoMensagem:
    """Envia a resposta pela plataforma; devolve a mensagem gravada.

    Levanta `EnvioRecusado` quando uma trava impede o envio (nada saiu).
    Erro da plataforma NÃO levanta: vira `falhou`/`revisar` na mensagem.
    COMMITA duas vezes — antes de chamar a plataforma (a linha em voo) e
    depois (o resultado) —, então quem chama não deve ter nada pendente
    que não queira commitar junto. Numa recusa, nada fica gravado nem
    travado (o savepoint do passo 0 é desfeito).

    `ultima_vista_id` é a última mensagem que a pessoa viu na tela; se a loja
    respondeu depois dela, recusa com `conversa_mudou` (outra pessoa, a IA ou
    o Duoke respondeu enquanto ela escrevia). `confirmar=True` é a pessoa
    dizendo "vi, envie mesmo assim".

    E-MAIL DA PONTE: vai para a fila da Central (`responder.enfileirar_resposta`),
    com `confirmar_nao_responde` (o "enviar mesmo assim" do remetente "não
    responder") e `mail_message_id` (qual e-mail responder; sem ele, o mais
    novo que não é nosso). A mensagem volta `enviando` — quem envia é o Mac.
    """
    from app.services.mail_atendimento.ponte import e_conversa_da_ponte

    if e_conversa_da_ponte(conversa):
        from app.services.mail_atendimento import responder

        return await responder.enfileirar_resposta(
            session,
            conversa,
            texto,
            user=user,
            rascunho_id=rascunho_id,
            origem=origem,
            ultima_vista_id=ultima_vista_id,
            confirmar=confirmar,
            confirmar_nao_responde=confirmar_nao_responde,
            mail_message_id=mail_message_id,
        )
    # ── 0. a conversa travada e relida: daqui em diante, o estado é o de AGORA
    # Tudo até a linha em voo roda num SAVEPOINT: numa recusa ele é desfeito
    # e a trava da conversa SOLTA na hora (trava pega num savepoint desfeito
    # não fica), sem estragar a transação nem os objetos de quem chamou.
    ponto = await session.begin_nested()
    try:
        mensagem, destino, normalizado, rascunho = await _preparar_envio(
            session,
            conversa,
            texto,
            user=user,
            rascunho_id=rascunho_id,
            origem=origem,
            ultima_vista_id=ultima_vista_id,
            confirmar=confirmar,
        )
    except BaseException:
        await ponto.rollback()
        raise
    await ponto.commit()
    # Em voo já conta como resposta na fila: a IA não gera por cima e a
    # conversa sai de "aguardando" enquanto a plataforma responde.
    gravar.recalcular(conversa, [mensagem])
    await session.commit()
    logger.info(
        "atendimento_envio_iniciado",
        conversa_id=str(conversa.id),
        mensagem_id=str(mensagem.id),
        plataforma=conversa.plataforma,
        origem=origem,
        simulador=get_settings().atendimento_simulador,
    )

    # ── 2. a plataforma (ou o simulador) ──────────────────────────────────
    resultado = await _chamar_plataforma(session, conversa, destino.integration, normalizado)

    # ── 3. o resultado, sobre o estado ATUAL do banco ─────────────────────
    await _gravar_resultado(
        session,
        conversa,
        mensagem,
        resultado,
        rascunho=rascunho,
        texto_digitado=texto,
        user=user,
        origem=origem,
    )
    logger.info(
        "atendimento_envio_concluido",
        conversa_id=str(conversa.id),
        mensagem_id=str(mensagem.id),
        plataforma=conversa.plataforma,
        status=mensagem.status,
        bloqueio=bool(resultado.bloqueio),
    )
    if conversa.canal == CANAL_AVALIACAO and mensagem.status == MSG_ENVIADA:
        await _avaliacao_respondida(session, conversa, mensagem)
    return mensagem


async def _avaliacao_respondida(
    session: AsyncSession, conversa: AtendimentoConversa, mensagem: AtendimentoMensagem
) -> None:
    """A resposta à avaliação SAIU: a avaliação sai da pendência agora (não na próxima leitura).

    A resposta já foi; um erro aqui só atrasa até a leitura seguinte (que
    acha a resposta no `get_comment`) — nunca vira erro do envio.
    """
    from app.services.atendimento import avaliacoes

    try:
        await avaliacoes.depois_da_resposta(session, conversa, mensagem)
    except Exception as e:  # noqa: BLE001
        await session.rollback()
        logger.warning(
            "atendimento_avaliacao_pos_envio_falhou",
            conversa_id=str(conversa.id),
            mensagem_id=str(mensagem.id),
            err=type(e).__name__,
        )
        for obj in (conversa, mensagem):
            try:
                await session.refresh(obj)
            except Exception:  # noqa: BLE001, S110 — a sessão quebrada já foi logada
                pass


def _sqlstate(e: BaseException) -> str | None:
    orig = getattr(e, "orig", None)
    causa = getattr(orig, "__cause__", None) or orig
    return getattr(causa, "sqlstate", None)


async def _gravar_resultado(
    session: AsyncSession,
    conversa: AtendimentoConversa,
    mensagem: AtendimentoMensagem,
    resultado: ResultadoEnvio,
    *,
    rascunho: AtendimentoRascunho | None,
    texto_digitado: str,
    user: User | None,
    origem: str,
    avaliar: bool = True,
) -> None:
    """Passo 3: grava o que a plataforma disse. A mensagem JÁ SAIU (ou pode ter saído).

    `avaliar=False` (a foto): a foto não é resposta escrita no lugar da
    sugestão da IA — não vira avaliação "escreveu do zero" (o rascunho que
    ela tornar velho sai da caixa pelo `recalcular_conversa`, como sempre).

    A ordem das travas é a do sync (`gravar`): primeiro a CONVERSA, depois a
    mensagem. O sync que traz a nossa resposta de volta trava a conversa e
    depois adota a mensagem; se aqui fosse ao contrário, os dois se
    travariam um ao outro (deadlock) e o envio que saiu devolveria erro.

    Relê tudo depois de travar: o sync pode ter adotado a linha (ou mexido
    na conversa) enquanto a plataforma respondia. Deadlock, serialização ou
    o UNIQUE do id da plataforma (o sync gravou a mesma mensagem no meio)
    desfazem só este passo e ele roda de novo — ele é idempotente. Se nem
    assim gravar, a linha fica `enviando` (vira `revisar` em 10 min e aparece
    em "A conferir") e a pessoa recebe a mensagem como está, nunca um 500
    por um envio que pode ter saído.
    """
    for tentativa in range(1, TENTATIVAS_RESULTADO + 1):
        try:
            await gravar.travar_linha(session, conversa)
            await session.refresh(mensagem)
            if rascunho is not None:
                await session.refresh(rascunho)
            await _aplicar_resultado(session, conversa, mensagem, resultado)
            if avaliar and mensagem.status in (MSG_ENVIADA, MSG_REVISAR):
                await _avaliar_rascunho(
                    session,
                    conversa,
                    mensagem,
                    rascunho=rascunho,
                    texto_digitado=texto_digitado,
                    user=user,
                    origem=origem,
                )
            await gravar.recalcular_conversa(session, conversa)
            await session.commit()
            return
        except DBAPIError as e:
            estado = _sqlstate(e)
            await session.rollback()
            logger.warning(
                "atendimento_envio_resultado_repetir",
                conversa_id=str(conversa.id),
                mensagem_id=str(mensagem.id),
                tentativa=tentativa,
                sqlstate=estado,
                err=type(e).__name__,
            )
            if estado not in _SQLSTATE_TRANSITORIO or tentativa == TENTATIVAS_RESULTADO:
                logger.error(
                    "atendimento_envio_resultado_nao_gravado",
                    conversa_id=str(conversa.id),
                    mensagem_id=str(mensagem.id),
                    ok=resultado.ok,
                    ambiguo=resultado.ambiguo,
                )
                # Rollback expirou tudo: devolve os objetos legíveis (o router
                # monta a resposta com eles).
                for obj in (conversa, mensagem):
                    try:
                        await session.refresh(obj)
                    except Exception:  # noqa: BLE001, S110 — sessão quebrada; o log já está
                        pass
                return


# ── Foto (01/10/2026) ─────────────────────────────────────────────────────
# A foto sai pelo MESMO caminho do texto: as mesmas travas (`_destino`), a
# mesma linha em voo commitada antes da plataforma, o mesmo "uma em voo por
# conversa", a mesma régua de resultado (enviada / revisar / falhou). O que
# muda: a plataforma (upload + mensagem com a imagem, `foto.enviar_imagem`),
# a conferência de repetida (pela impressão digital da imagem, não pelo
# texto) e a avaliação da sugestão da IA (foto não é resposta escrita).
# Upload para a plataforma SÓ aqui — com o envio desligado, a trava
# `envio_desligado` recusa antes de qualquer byte sair do DaVinci.

TEMPO_MAXIMO_FOTO_S = 150


async def _conferir_foto_repetida(
    session: AsyncSession, conversa: AtendimentoConversa, sha256: str
) -> None:
    """A mesma imagem já saiu (ou está saindo) nesta conversa há pouco? → `envio_repetido`."""
    repetida = await session.scalar(
        select(AtendimentoMensagem.id)
        .where(
            AtendimentoMensagem.conversa_id == conversa.id,
            AtendimentoMensagem.origem.in_(ORIGENS_DAVINCI),
            AtendimentoMensagem.status.in_(_STATUS_QUE_SAIRAM),
            AtendimentoMensagem.created_at >= datetime.now(UTC) - JANELA_REPETIDO,
            AtendimentoMensagem.payload["foto"]["sha256"].astext == sha256,
        )
        .limit(1)
    )
    if repetida is not None:
        raise EnvioRecusado(
            RECUSA_ENVIO_REPETIDO,
            "Esta mesma foto acabou de ser enviada nesta conversa (há menos de 2 minutos). "
            "Ela não foi enviada de novo.",
        )


async def _preparar_foto(
    session: AsyncSession,
    conversa: AtendimentoConversa,
    foto,
    *,
    legenda: str | None,
    user: User | None,
    ultima_vista_id: UUID | None,
    confirmar: bool,
) -> tuple[AtendimentoMensagem, _Destino, str | None]:
    """Passos 0 e 1 da foto: trava, confere e grava a linha em voo (sem commit)."""
    from app.services.atendimento import foto as foto_mod

    await travar_conversa(session, conversa)
    destino = await _destino(session, conversa, origem=ORIGEM_HUMANO)
    motivo = foto_mod.motivo_sem_foto(conversa)
    if motivo:
        raise EnvioRecusado(RECUSA_FOTO_NAO_SUPORTADA, motivo)
    legenda_ok: str | None = None
    if foto_mod.legenda_obrigatoria(conversa):
        # ML: a foto vai DENTRO de uma mensagem — o texto passa pelo validador
        # como qualquer resposta (vazio = `texto_invalido`).
        legenda_ok = _preparar_texto(legenda, conversa=conversa, origem=ORIGEM_HUMANO)
    elif (legenda or "").strip():
        raise EnvioRecusado(
            RECUSA_FOTO_INVALIDA,
            "Nesta plataforma a foto vai sozinha: mande o texto pela caixa de resposta.",
        )
    await _conferir_foto_repetida(session, conversa, foto.sha256)
    if ultima_vista_id is not None and not confirmar:
        await _conferir_mudou(session, conversa, ultima_vista_id)

    await aposentar_envios_presos(session, conversa_id=conversa.id)
    mensagem = AtendimentoMensagem(
        conversa_id=conversa.id,
        externo_id=None,
        autor=AUTOR_LOJA,
        origem=ORIGEM_HUMANO,
        autor_user_id=user.id if user is not None else None,
        tipo="imagem",
        texto=legenda_ok,
        # A URL só existe depois do upload (`_aplicar_resultado` põe).
        anexos=[{"tipo": "imagem", "nome": foto.nome, "mime": foto.mime, "tamanho": foto.tamanho}],
        enviada_em=None,
        status=MSG_ENVIANDO,
        rascunho_id=None,
        payload={"foto": {"sha256": foto.sha256, "mime": foto.mime, "bytes": foto.tamanho}},
    )
    await session.flush()
    try:
        async with session.begin_nested():
            session.add(mensagem)
            await session.flush()
    except IntegrityError as e:
        if not is_unique_violation(e):
            raise
        raise EnvioRecusado(
            RECUSA_ENVIO_EM_ANDAMENTO, "Há uma resposta sendo enviada nesta conversa."
        ) from e
    return mensagem, destino, legenda_ok


async def _chamar_plataforma_foto(
    session: AsyncSession,
    conversa: AtendimentoConversa,
    integration: Integration,
    foto,
    legenda: str | None,
) -> ResultadoEnvio:
    """Sobe a foto e manda a mensagem. Nunca levanta (como `_chamar_plataforma`)."""
    from app.services.atendimento import foto as foto_mod

    s = get_settings()
    if s.atendimento_simulador and s.is_prod:
        return ResultadoEnvio(ok=False, erro="simulador_em_producao")
    if vai_para_o_simulador(conversa.plataforma):
        return ResultadoEnvio(ok=True, externo_id=f"sim:{uuid4()}", payload={"simulador": True})
    try:
        cliente = await clientes.cliente_da_integracao(integration)
    except Exception as e:  # noqa: BLE001
        return ResultadoEnvio(ok=False, erro=f"cliente_indisponivel: {type(e).__name__}")
    try:
        return await asyncio.wait_for(
            foto_mod.enviar_imagem(session, conversa, integration, cliente, foto, legenda),
            timeout=TEMPO_MAXIMO_FOTO_S,
        )
    except TimeoutError:
        return ResultadoEnvio(ok=False, ambiguo=True, erro="timeout")
    except Exception as e:  # noqa: BLE001 — sem saber se saiu: ambíguo
        return ResultadoEnvio(ok=False, ambiguo=True, erro=f"erro_inesperado: {type(e).__name__}")


async def enviar_foto(
    session: AsyncSession,
    conversa: AtendimentoConversa,
    foto,
    *,
    legenda: str | None = None,
    user: User | None,
    ultima_vista_id: UUID | None = None,
    confirmar: bool = False,
) -> AtendimentoMensagem:
    """Envia UMA foto (já conferida: `foto.validar_foto`) na conversa; devolve a mensagem.

    O mesmo contrato do `enviar_resposta`: `EnvioRecusado` = nada saiu (trava
    nossa); erro da plataforma vira `falhou`/`revisar` na mensagem; COMMITA
    antes de falar com a plataforma e depois. Só pessoa manda foto (a IA
    nunca). `legenda` só no ML (obrigatória lá); Shopee/TikTok: foto sozinha.
    """
    ponto = await session.begin_nested()
    try:
        mensagem, destino, legenda_ok = await _preparar_foto(
            session,
            conversa,
            foto,
            legenda=legenda,
            user=user,
            ultima_vista_id=ultima_vista_id,
            confirmar=confirmar,
        )
    except BaseException:
        await ponto.rollback()
        raise
    await ponto.commit()
    # Em voo já conta como resposta na fila (como o texto).
    gravar.recalcular(conversa, [mensagem])
    await session.commit()
    logger.info(
        "atendimento_foto_iniciada",
        conversa_id=str(conversa.id),
        mensagem_id=str(mensagem.id),
        plataforma=conversa.plataforma,
        bytes=foto.tamanho,
        simulador=get_settings().atendimento_simulador,
    )
    resultado = await _chamar_plataforma_foto(
        session, conversa, destino.integration, foto, legenda_ok
    )
    await _gravar_resultado(
        session,
        conversa,
        mensagem,
        resultado,
        rascunho=None,
        texto_digitado=legenda_ok or "",
        user=user,
        origem=ORIGEM_HUMANO,
        avaliar=False,
    )
    logger.info(
        "atendimento_foto_concluida",
        conversa_id=str(conversa.id),
        mensagem_id=str(mensagem.id),
        plataforma=conversa.plataforma,
        status=mensagem.status,
        bloqueio=bool(resultado.bloqueio),
    )
    return mensagem


# ── Mensagem automática (05/10/2026) ──────────────────────────────────────
# O motor de automações (`automacoes.py`) manda cada PARTE por aqui: o texto,
# o cartão do pedido e a figurinha (Shopee). Só sai com TUDO ligado: o freio
# único (`atendimento_envio_ativo`), a chave nova (`atendimento_automacoes_envio`)
# e a regra da loja em `enviar`, relida do banco agora. O modo seco nunca
# chega aqui (o motor nem importa este módulo nele); o teste prova.
#
# SEM CONVERSA no DaVinci (o pedido recebido de quem nunca escreveu à loja):
# `enviar_automatica_sem_conversa` manda pelo `to_id` (o comprador do pedido),
# a Shopee abre a conversa e devolve o `conversation_id` — a conversa e a
# mensagem nascem aqui, já com a marca, e a leitura seguinte as acha pelo id.

RECUSA_PARTE_NAO_SUPORTADA = "parte_nao_suportada"
RECUSA_SEM_COMPRADOR = "sem_comprador"
# As partes sem texto (cartão e figurinha) só existem na Shopee.
PARTES_SO_SHOPEE = ("cartao_pedido", "figurinha")


def _automacao_do_codigo(codigo: str):
    from app.services.atendimento import automacoes_catalogo as catalogo

    return catalogo.automacao(codigo)


def _travas_gerais_automatica(plataforma: str, codigo: str, canal_nome: str):
    """As travas que não dependem da conversa; devolve a automação do catálogo."""
    if plataforma not in PLATAFORMAS_AUTOMACAO or plataforma not in ADAPTADORES:
        raise EnvioRecusado(
            RECUSA_SOMENTE_LEITURA, "Mensagem automática só na Shopee, no TikTok e no ML."
        )
    settings = get_settings()
    if settings.atendimento_simulador and settings.is_prod:
        logger.error("atendimento_simulador_em_producao", automacao=codigo)
        raise EnvioRecusado(
            RECUSA_SIMULADOR_EM_PRODUCAO,
            "O simulador de envio (só para teste local) está ligado em produção.",
        )
    if not settings.atendimento_envio_ativo:
        raise EnvioRecusado(
            RECUSA_ENVIO_DESLIGADO,
            "O envio pelo DaVinci está desligado (ATENDIMENTO_ENVIO_ATIVO): nada sai, "
            "nem as mensagens automáticas.",
        )
    if not settings.atendimento_automacoes_envio:
        raise EnvioRecusado(
            RECUSA_AUTOMACOES_ENVIO_DESLIGADO,
            "O envio das mensagens automáticas está desligado (ATENDIMENTO_AUTOMACOES_ENVIO).",
        )
    if plataforma == "shopee" and not settings.shopee_mensagens_comprador:
        raise EnvioRecusado(
            RECUSA_SHOPEE_MENSAGENS_DESLIGADAS,
            "As mensagens automáticas para o comprador da Shopee estão desligadas "
            "(SHOPEE_MENSAGENS_COMPRADOR).",
        )
    aut = _automacao_do_codigo(codigo)
    if aut is None or aut.plataforma != plataforma or aut.canal != canal_nome:
        raise EnvioRecusado(RECUSA_REGRA_NAO_ENVIA, "Automação desconhecida para esta conversa.")
    if aut.so_simular:
        raise EnvioRecusado(
            RECUSA_SO_SIMULACAO,
            "Esta automação só simula (o DaVinci mostra o que mandaria): nada sai por ela.",
        )
    if aut.campanha and not campanha_por_auto_reply():
        raise EnvioRecusado(
            RECUSA_CAMPANHA_SEM_AUTO_REPLY,
            "Campanha da Shopee: só sai como resposta automática (auto_reply) — falta a "
            "chave confirmada ou o envio por ela no adaptador.",
        )
    return aut


async def _regra_em_enviar(session: AsyncSession, codigo: str, integration_id: UUID) -> None:
    """A regra da loja, RELIDA agora, está em `enviar`? Senão `regra_nao_envia`."""
    from app.services.atendimento import automacoes_catalogo as catalogo

    regra = (
        await session.execute(
            select(AtendimentoAutomacaoRegra)
            .where(
                AtendimentoAutomacaoRegra.automacao == codigo,
                AtendimentoAutomacaoRegra.integration_id == integration_id,
            )
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if regra is None or regra.modo != catalogo.MODO_ENVIAR:
        raise EnvioRecusado(
            RECUSA_REGRA_NAO_ENVIA, "A regra desta automação nesta loja não está em enviar."
        )


async def _destino_automatica(
    session: AsyncSession, conversa: AtendimentoConversa, *, codigo: str
) -> _Destino:
    """As travas da mensagem automática; levanta `EnvioRecusado`."""
    motivo_sem_envio = motivo_canal_sem_envio(conversa.canal, conversa.plataforma, conversa.dados)
    if motivo_sem_envio:
        raise EnvioRecusado(RECUSA_CANAL_SEM_ENVIO, motivo_sem_envio)
    _travas_gerais_automatica(conversa.plataforma, codigo, conversa.canal)
    if conversa.situacao == CONVERSA_BLOQUEADA:
        raise EnvioRecusado(
            RECUSA_CONVERSA_BLOQUEADA,
            conversa.bloqueio_motivo or "A plataforma não deixa mais responder nesta conversa.",
        )
    limite = conversa.pode_enviar_ate
    if limite is not None and limite.tzinfo is None:
        limite = limite.replace(tzinfo=UTC)
    if limite is not None and datetime.now(UTC) > limite:
        raise EnvioRecusado(
            RECUSA_CONVERSA_BLOQUEADA,
            "A janela de resposta da plataforma para esta conversa já fechou.",
        )
    canal = (
        await session.get(AtendimentoCanal, conversa.canal_id, populate_existing=True)
        if conversa.canal_id is not None
        else None
    )
    integration = (
        await session.get(Integration, canal.integration_id, populate_existing=True)
        if canal is not None and canal.integration_id is not None
        else None
    )
    if canal is None or integration is None or integration.archived_at is not None:
        raise EnvioRecusado(
            RECUSA_SEM_INTEGRACAO, "A loja desta conversa não está mais conectada ao DaVinci."
        )
    await _regra_em_enviar(session, codigo, integration.id)
    return _Destino(canal=canal, integration=integration)


def _parte_pronta(parte: dict | None, texto: str | None) -> dict:
    """A parte a mandar: a de `parte`, ou um texto (o contrato antigo: só `texto`)."""
    if isinstance(parte, dict) and parte.get("tipo"):
        return dict(parte)
    return {"tipo": "texto", "texto": texto or ""}


def _marca(
    codigo: str,
    parte: dict,
    *,
    registro_id: UUID | None,
    regra_versao: int | None,
    indice: int,
    pedido: str | None,
) -> dict:
    """A marca do motor (`payload.automacao`): a régua, a leitura e o comparador leem."""
    marca = {
        "codigo": codigo,
        "registro_id": str(registro_id) if registro_id else None,
        "regra_versao": regra_versao,
        "parte": parte["tipo"],
        "indice": indice,
    }
    if parte["tipo"] == "cartao_pedido":
        marca["pedido"] = str(pedido or "")
    elif parte["tipo"] == "figurinha":
        marca["figurinha"] = str(parte.get("figurinha") or "")
        marca["pacote"] = str(parte.get("pacote") or "")
    return marca


def _linha_da_parte(parte: dict, *, pedido: str | None) -> tuple[str, str | None, list]:
    """(tipo, texto, anexos) da linha de `atendimento_mensagens` — como a leitura grava."""
    from app.services.atendimento import enriquecer
    from app.services.atendimento.constantes import rotulo_tipo_plataforma

    if parte["tipo"] == "cartao_pedido":
        return "pedido", None, [enriquecer.cartao_pedido_vazio(pedido or "")]
    if parte["tipo"] == "figurinha":
        return "outro", rotulo_tipo_plataforma("sticker"), []
    return "texto", parte.get("texto"), []


def _conferir_parte(plataforma: str, parte: dict, *, pedido: str | None) -> None:
    """Cartão e figurinha só na Shopee; o cartão precisa do pedido."""
    tipo = parte.get("tipo")
    if tipo == "texto":
        return
    if tipo not in PARTES_SO_SHOPEE or plataforma != "shopee":
        raise EnvioRecusado(
            RECUSA_PARTE_NAO_SUPORTADA, f"Parte {tipo!r} não sai por esta plataforma."
        )
    if tipo == "cartao_pedido" and not str(pedido or "").strip():
        raise EnvioRecusado(RECUSA_PARTE_NAO_SUPORTADA, "Cartão do pedido sem o número do pedido.")


async def _chamar_plataforma_automatica(
    session: AsyncSession,
    conversa: AtendimentoConversa | None,
    integration: Integration,
    plataforma: str,
    parte: dict,
    *,
    to_id: str | None = None,
    pedido: str | None = None,
    auto_reply: bool = False,
) -> ResultadoEnvio:
    """Fala com a plataforma para UMA parte. Nunca levanta (como `_chamar_plataforma`).

    O texto comum de uma conversa sai pelo `enviar_texto` de sempre; o cartão,
    a figurinha, a resposta automática e o envio sem conversa só pela
    `enviar_parte` do adaptador (Shopee). A resposta automática (a campanha
    da Shopee) só sai pela `enviar_parte(auto_reply=True)`: sem ela,
    `sem_auto_reply` e nada sai — nunca como mensagem normal.
    """
    s = get_settings()
    if s.atendimento_simulador and s.is_prod:
        return ResultadoEnvio(ok=False, erro="simulador_em_producao")
    if vai_para_o_simulador(plataforma):
        return ResultadoEnvio(
            ok=True,
            externo_id=f"sim:{uuid4()}",
            payload={"simulador": True, "conversation_id": f"sim:{uuid4()}"},
        )
    try:
        cliente = await clientes.cliente_da_integracao(integration)
    except Exception as e:  # noqa: BLE001
        return ResultadoEnvio(ok=False, erro=f"cliente_indisponivel: {type(e).__name__}")
    try:
        modulo = adaptador(plataforma)
        enviar_parte = getattr(modulo, "enviar_parte", None)
        if auto_reply and not _sabe_auto_reply(enviar_parte):
            return ResultadoEnvio(ok=False, erro="sem_auto_reply")
        simples = parte.get("tipo") == "texto" and not auto_reply and conversa is not None
        if simples and enviar_parte is None:
            return await asyncio.wait_for(
                modulo.enviar_texto(session, conversa, integration, cliente, parte["texto"]),
                timeout=TEMPO_MAXIMO_ENVIO_S,
            )
        if enviar_parte is None:
            return ResultadoEnvio(ok=False, erro=f"{plataforma} parte_nao_suportada")
        return await asyncio.wait_for(
            enviar_parte(
                session,
                conversa,
                integration,
                cliente,
                parte,
                to_id=to_id,
                pedido=pedido,
                auto_reply=auto_reply,
            ),
            timeout=TEMPO_MAXIMO_ENVIO_S,
        )
    except TimeoutError:
        return ResultadoEnvio(ok=False, ambiguo=True, erro="timeout")
    except Exception as e:  # noqa: BLE001
        return ResultadoEnvio(ok=False, ambiguo=True, erro=f"erro_inesperado: {type(e).__name__}")


def _vai_como_auto_reply(aut, plataforma: str, parte: dict) -> bool:
    """O TEXTO da campanha da Shopee sai como resposta automática (como o Duoke).

    O cartão e a figurinha vão como mensagem normal — também como o Duoke
    (medido: 1.940 cartões e 969 figurinhas `status=normal` em 7 dias).
    """
    return bool(
        aut is not None and aut.campanha and plataforma == "shopee" and parte["tipo"] == "texto"
    )


async def enviar_automatica(
    session: AsyncSession,
    conversa: AtendimentoConversa,
    *,
    codigo: str,
    texto: str | None = None,
    parte: dict | None = None,
    pedido: str | None = None,
    registro_id: UUID | None,
    regra_versao: int | None,
    indice: int = 0,
) -> AtendimentoMensagem:
    """Envia UMA parte da mensagem automática na conversa; devolve a mensagem gravada.

    A parte é `parte` (`{"tipo": "texto"|"cartao_pedido"|"figurinha", ...}`)
    ou, no contrato antigo, o `texto`. O cartão leva o `pedido`.

    O contrato do `enviar_resposta`: `EnvioRecusado` = nada saiu (trava
    nossa; `RECUSAS_TEMPORARIAS` passam sozinhas); erro da plataforma vira
    `falhou`/`revisar` na mensagem; COMMITA antes de falar com a plataforma e
    depois. A mensagem nasce `davinci_auto` com `payload.automacao`, que a
    régua reconhece (não fecha a vez do comprador, a IA não aprende com ela)
    e a leitura adota quando a plataforma a devolve (o texto pelo texto; o
    cartão e a figurinha pela marca — `gravar.parte_da_plataforma`).
    """
    pronta = _parte_pronta(parte, texto)
    ponto = await session.begin_nested()
    try:
        await travar_conversa(session, conversa)
        destino = await _destino_automatica(session, conversa, codigo=codigo)
        _conferir_parte(conversa.plataforma, pronta, pedido=pedido)
        if pronta["tipo"] == "texto":
            pronta["texto"] = _preparar_texto(
                pronta.get("texto"), conversa=conversa, origem=ORIGEM_AUTO
            )
            await _conferir_repetido(session, conversa, pronta["texto"])
        await aposentar_envios_presos(session, conversa_id=conversa.id)
        tipo, texto_linha, anexos = _linha_da_parte(pronta, pedido=pedido)
        mensagem = AtendimentoMensagem(
            conversa_id=conversa.id,
            externo_id=None,
            autor=AUTOR_LOJA,
            origem=ORIGEM_AUTO,
            tipo=tipo,
            texto=texto_linha,
            anexos=anexos,
            enviada_em=None,
            status=MSG_ENVIANDO,
            payload={
                "automacao": _marca(
                    codigo,
                    pronta,
                    registro_id=registro_id,
                    regra_versao=regra_versao,
                    indice=indice,
                    pedido=pedido,
                )
            },
        )
        await session.flush()
        try:
            async with session.begin_nested():
                session.add(mensagem)
                await session.flush()
        except IntegrityError as e:
            if not is_unique_violation(e):
                raise
            raise EnvioRecusado(
                RECUSA_ENVIO_EM_ANDAMENTO, "Há uma resposta sendo enviada nesta conversa."
            ) from e
    except BaseException:
        await ponto.rollback()
        raise
    await ponto.commit()
    gravar.recalcular(conversa, [mensagem])
    await session.commit()
    logger.info(
        "atendimento_automatica_iniciada",
        conversa_id=str(conversa.id),
        mensagem_id=str(mensagem.id),
        plataforma=conversa.plataforma,
        automacao=codigo,
        parte=pronta["tipo"],
        registro_id=str(registro_id) if registro_id else None,
    )
    resultado = await _chamar_plataforma_automatica(
        session,
        conversa,
        destino.integration,
        conversa.plataforma,
        pronta,
        pedido=pedido,
        auto_reply=_vai_como_auto_reply(_automacao_do_codigo(codigo), conversa.plataforma, pronta),
    )
    await _gravar_resultado(
        session,
        conversa,
        mensagem,
        resultado,
        rascunho=None,
        texto_digitado=pronta.get("texto") or "",
        user=None,
        origem=ORIGEM_AUTO,
        avaliar=False,
    )
    logger.info(
        "atendimento_automatica_concluida",
        conversa_id=str(conversa.id),
        mensagem_id=str(mensagem.id),
        automacao=codigo,
        parte=pronta["tipo"],
        status=mensagem.status,
        bloqueio=bool(resultado.bloqueio),
    )
    return mensagem


@dataclass
class EnvioSemConversa:
    """O que saiu (ou não) pelo `to_id`, sem conversa no DaVinci.

    `status` é o da mensagem (`enviada`/`revisar`/`falhou`); com `enviada`,
    `conversa_id` e `mensagem_id` são as linhas que nasceram (ou foram
    adotadas) aqui. `saiu` = a Shopee ACEITOU a parte, mesmo sem a linha
    gravada (`revisar` por falha do banco depois): o motor não manda as
    partes seguintes e trata a mensagem como pela metade.
    """

    status: str
    conversa_id: UUID | None = None
    mensagem_id: UUID | None = None
    erro: str | None = None
    saiu: bool = False


async def _destino_sem_conversa(
    session: AsyncSession, *, integration_id: UUID, codigo: str
) -> _Destino:
    """As travas do envio pelo `to_id` (sem conversa); levanta `EnvioRecusado`."""
    integration = await session.get(Integration, integration_id, populate_existing=True)
    if integration is None or integration.archived_at is not None:
        raise EnvioRecusado(RECUSA_SEM_INTEGRACAO, "A loja não está mais conectada ao DaVinci.")
    plataforma = str(getattr(integration.platform, "value", integration.platform) or "").lower()
    if plataforma != "shopee":
        # Só a Shopee manda para o comprador sem conversa (pelo `to_id`).
        raise EnvioRecusado(
            RECUSA_SOMENTE_LEITURA, "Sem conversa, só a Shopee manda (pelo comprador do pedido)."
        )
    aut = _travas_gerais_automatica(plataforma, codigo, "chat")
    if aut.alvo != "pedido":
        raise EnvioRecusado(RECUSA_REGRA_NAO_ENVIA, "Sem conversa só saem as automações do pedido.")
    canal = (
        await session.execute(
            select(AtendimentoCanal)
            .where(
                AtendimentoCanal.integration_id == integration_id,
                AtendimentoCanal.canal == aut.canal,
            )
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if canal is None:
        raise EnvioRecusado(RECUSA_SEM_INTEGRACAO, "A loja não tem a caixa do chat no DaVinci.")
    await _regra_em_enviar(session, codigo, integration_id)
    return _Destino(canal=canal, integration=integration)


async def _gravar_sem_conversa(
    session: AsyncSession,
    destino: _Destino,
    *,
    conversation_id: str,
    externo_id: str | None,
    to_id: str,
    comprador_nome: str | None,
    pedido: str | None,
    pronta: dict,
    marca: dict,
    resposta: dict,
) -> tuple[AtendimentoConversa, AtendimentoMensagem]:
    """A conversa que a Shopee devolveu e a nossa mensagem nela (ou a da leitura, adotada)."""
    conversa, _ = await gravar.upsert_conversa(
        session,
        canal=destino.canal,
        integration=destino.integration,
        plataforma="shopee",
        canal_nome=destino.canal.canal,
        externo_id=conversation_id,
        comprador_id=to_id,
        comprador_nome=comprador_nome or None,
        pedido_marketplace=pedido or None,
    )
    await gravar.travar_linha(session, conversa)
    existente = (
        (
            await session.execute(
                select(AtendimentoMensagem).where(
                    AtendimentoMensagem.conversa_id == conversa.id,
                    AtendimentoMensagem.externo_id == externo_id,
                )
            )
        ).scalar_one_or_none()
        if externo_id
        else None
    )
    if existente is not None:
        # A leitura trouxe a mensagem antes de nós: ela é a nossa.
        existente.origem = ORIGEM_AUTO
        existente.autor = AUTOR_LOJA
        existente.status = MSG_ENVIADA
        existente.payload = {**(existente.payload or {}), "automacao": marca, "envio": resposta}
        mensagem = existente
    else:
        tipo, texto_linha, anexos = _linha_da_parte(pronta, pedido=pedido)
        mensagem = AtendimentoMensagem(
            conversa_id=conversa.id,
            externo_id=externo_id,
            autor=AUTOR_LOJA,
            origem=ORIGEM_AUTO,
            tipo=tipo,
            texto=texto_linha,
            anexos=anexos,
            enviada_em=datetime.now(UTC),
            status=MSG_ENVIADA,
            payload={"automacao": marca, "envio": resposta},
        )
        session.add(mensagem)
        await session.flush()
    await gravar.recalcular_conversa(session, conversa)
    await session.commit()
    return conversa, mensagem


async def enviar_automatica_sem_conversa(
    session: AsyncSession,
    *,
    integration_id: UUID,
    codigo: str,
    to_id: str,
    pedido: str | None,
    parte: dict,
    registro_id: UUID | None,
    regra_versao: int | None,
    indice: int = 0,
    comprador_nome: str | None = None,
) -> EnvioSemConversa:
    """Envia UMA parte pelo `to_id` (Shopee), sem conversa no DaVinci.

    As mesmas travas da `enviar_automatica` (freio único, chave nova,
    `shopee_mensagens_comprador`, `auto_reply` das campanhas, a regra RELIDA
    em `enviar`), mais: só Shopee, só as automações do pedido. Sem conversa
    não há linha em voo: quem trava contra duplicar é o registro `enviando`
    do motor (chave única), commitado antes daqui.

    A Shopee devolve `conversation_id` e `message_id` (medido nos envios da
    senha da devolução: os mesmos ids que a leitura grava). Com eles, a
    conversa nasce (`gravar.upsert_conversa`, com o comprador e o pedido) e a
    mensagem nasce `davinci_auto` com a marca e o `externo_id` — a leitura
    seguinte acha as duas pelo id e não duplica. Se a leitura chegou antes
    (corrida), a mensagem dela é ADOTADA: ganha a origem e a marca. Sem
    `conversation_id` na resposta, saiu mas não dá para ligar: `revisar`.
    `EnvioRecusado` = nada saiu.
    """
    pronta = _parte_pronta(parte, None)
    destino = await _destino_sem_conversa(session, integration_id=integration_id, codigo=codigo)
    destinatario = str(to_id or "").strip()
    if not destinatario:
        raise EnvioRecusado(RECUSA_SEM_COMPRADOR, "Sem o comprador do pedido.")
    _conferir_parte("shopee", pronta, pedido=pedido)
    if pronta["tipo"] == "texto":
        alvo = _ConversaDaLoja(plataforma="shopee", canal=destino.canal.canal)
        pronta["texto"] = _preparar_texto(pronta.get("texto"), conversa=alvo, origem=ORIGEM_AUTO)
    marca = _marca(
        codigo,
        pronta,
        registro_id=registro_id,
        regra_versao=regra_versao,
        indice=indice,
        pedido=pedido,
    )
    await session.commit()
    logger.info(
        "atendimento_automatica_sem_conversa_iniciada",
        integration_id=str(integration_id),
        automacao=codigo,
        parte=pronta["tipo"],
        registro_id=str(registro_id) if registro_id else None,
    )
    resultado = await _chamar_plataforma_automatica(
        session,
        None,
        destino.integration,
        "shopee",
        pronta,
        to_id=destinatario,
        pedido=pedido,
        auto_reply=_vai_como_auto_reply(_automacao_do_codigo(codigo), "shopee", pronta),
    )
    if not resultado.ok:
        status = MSG_REVISAR if resultado.ambiguo else MSG_FALHOU
        erro = (resultado.erro or resultado.bloqueio or "envio_falhou")[:500]
        logger.info(
            "atendimento_automatica_sem_conversa_concluida",
            integration_id=str(integration_id),
            automacao=codigo,
            status=status,
        )
        return EnvioSemConversa(status=status, erro=erro)
    resposta = dict(resultado.payload or {})
    conversation_id = str(resposta.get("conversation_id") or "").strip()
    if not conversation_id:
        logger.warning(
            "atendimento_automatica_sem_conversation_id",
            integration_id=str(integration_id),
            automacao=codigo,
        )
        return EnvioSemConversa(status=MSG_REVISAR, erro="shopee sem_conversation_id", saiu=True)
    try:
        conversa, mensagem = await _gravar_sem_conversa(
            session,
            destino,
            conversation_id=conversation_id,
            externo_id=str(resultado.externo_id) if resultado.externo_id else None,
            to_id=destinatario,
            comprador_nome=comprador_nome,
            pedido=pedido,
            pronta=pronta,
            marca=marca,
            resposta=resposta,
        )
    except DBAPIError as e:
        # A mensagem SAIU: não dá para dizer que não. Sem a linha gravada, vira
        # `revisar` (nunca retentada) — e a conferência é de pessoa.
        await session.rollback()
        logger.error(
            "atendimento_automatica_sem_conversa_nao_gravada",
            integration_id=str(integration_id),
            automacao=codigo,
            err=type(e).__name__,
        )
        return EnvioSemConversa(
            status=MSG_REVISAR, erro=f"gravar_falhou: {type(e).__name__}", saiu=True
        )
    logger.info(
        "atendimento_automatica_sem_conversa_concluida",
        integration_id=str(integration_id),
        conversa_id=str(conversa.id),
        mensagem_id=str(mensagem.id),
        automacao=codigo,
        status=MSG_ENVIADA,
    )
    return EnvioSemConversa(status=MSG_ENVIADA, conversa_id=conversa.id, mensagem_id=mensagem.id)


@dataclass
class _ConversaDaLoja:
    """O bastante de uma conversa para o validador (plataforma e caixa)."""

    plataforma: str
    canal: str
