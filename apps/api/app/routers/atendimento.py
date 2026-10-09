"""Pós-venda › Atendimento: a caixa única de Shopee, ML, TikTok e Amazon (25/09/2026).

"O Duoke nosso" (plano em `docs/atendimento-unificado.md`). A tela lê a fila,
abre a conversa com o pedido ao lado, mostra a sugestão da IA e responde —
sempre pelo `services/atendimento/enviar.py`, o caminho único de saída.

Permissão: recurso `atendimento` — view lê, edit responde e mexe no manual
e nas respostas prontas, delete apaga regra/modelo. Modo `auto` (a IA envia
sozinha) só admin liga — e, com alguma loja em `auto`, só admin mexe nas
regras do manual que valem para ela (a regra muda o que sai sozinho).

FASE DE OBSERVAÇÃO (`SO_ADMIN`, logo abaixo do `router`): a caixa nasceu só
para os admins de ATENDIMENTO_USUARIOS (Eduardo, 30/09/2026). Desde
07/10/2026 ("pode liberar pras outras pessoas do DaVinci verem pra já
obtermos feedbacks, mas claro por enquanto só leitura"), TODA pessoa ativa
do DaVinci LÊ (`acesso.pode_ver`) — e pode pedir a sugestão da IA e dar
👍/👎 nela (`ROTAS_DE_QUEM_LE`) —, e só a lista MEXE (`acesso.pode_mexer`):
qualquer outra escrita de quem só lê responde 403 `atendimento_so_leitura`,
mesmo para os outros admins. As permissões finas (view/edit/delete)
continuam nas rotas e passam a valer quando `SO_ADMIN` voltar para False.

ESCOPO POR EQUIPE: como Integrações e Anúncios, pela `integration_id` da
conversa/canal (`deps/team_scope.py`). Admin e quem não tem equipe veem
tudo; quem tem equipe vê só as lojas dela. As DMs do Instagram não são de
loja nenhuma (são das marcas): só aparecem para quem vê tudo.

A lista mistura as conversas do marketplace com as DMs do Instagram (id
`ig:<uuid>`, SÓ LEITURA), paginando as duas pela mesma chave
(`ultima_mensagem_em < antes_de`).

O cérebro (validador, contexto, ia) é importado na hora de usar: o app
carrega este router sempre, e ele não precisa do cérebro para subir.

Tela "igual ao Duoke" (28/09/2026): a barra de lojas vem do /resumo (toda
loja conectada, com as não lidas da plataforma e a saúde do canal); o
detalhe traz o retrato do pedido na plataforma (`pedido_mkt`) e o cartão do
anúncio (`produto`), guardados em `conversa.dados` pelo enriquecimento
(`services/atendimento/enriquecer.py`), e as sugestões da IA que não saíram
pelo DaVinci com a resposta real ao lado — o "IA × equipe" do primeiro teste
em produção, só observando (quem responde é o Duoke).

Parte 2 (28/09/2026): a caixa mostra o nome da LOJA, não o da integração
(`lojas.nome_da_loja`: "mega" → "Marquezini", como no Duoke); o detalhe
traz o cartão "Cliente" (`cliente.cartao_cliente`: histórico de compra,
avaliações, perguntas antes de comprar, sinais) e diz se o "atualizar" do
pedido tem o que fazer; o manual ganha tipo/categoria/prioridade, e a API
não deixa nascer regra batendo com regra (`manual.conflitos_da_regra` →
409 `regra_conflitante`). Esses três módulos também entram na hora de usar,
pelo nome: sem eles o router sobe e a tela cai no que já tinha.

Etiqueta = status atual (RF1, 01/10/2026; `services/atendimento/etiqueta`):
a lista e o detalhe trazem a etiqueta, o menu Filtrar filtra por ela
(`filtro=reclamacao|devolucao|ag_cancelamento|pre_venda|pos_venda` ou
`?etiqueta=`, que junta com qualquer filtro) e o /resumo conta as abertas
por etiqueta. A troca à mão é o POST /conversas/{id}/etiqueta (fica na
linha do tempo, `etiqueta_historico` do detalhe). A aba "Falta responder"
vem pelo prazo mais curto (cursor `prazo:`), e a busca acha também pelo nº
do Bling e pelo SKU.

CAIXA HUMANO (09/10/2026, `services/atendimento/humano.py`): ao lado da
Caixa, só o que falta responder e a IA NÃO pode responder — a régua da IA,
sem chamar o modelo, calculada pelo cron `atendimento_humano` e guardada em
`dados.humano` (só os códigos dos motivos). A lista filtra com
`?caixa=humano` (junto de plataforma, loja, canal, etiqueta, busca e
filtro; sem o Direct do Instagram), toda linha que está nela traz `humano`
(motivos e rótulos — o chip da Caixa normal também) e o /resumo conta
`humano` por plataforma, por loja e no total, no escopo da equipe. Só
leitura: nenhuma rota nova de escrita.
"""

from __future__ import annotations

import importlib
import re
import statistics
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from math import ceil
from typing import Annotated, Any
from urllib.parse import urlsplit
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.encoders import jsonable_encoder
from sqlalchemy import String, and_, case, cast, exists, false, func, not_, or_, select, text, true
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app import worker_pool
from app.config import get_settings
from app.db import get_session
from app.deps.auth import require_active_user, require_permission
from app.deps.team_scope import TeamScope, resolve_team_scope
from app.models import (
    AtendimentoAvaliacao,
    AtendimentoCanal,
    AtendimentoConversa,
    AtendimentoMensagem,
    AtendimentoModelo,
    AtendimentoRascunho,
    AtendimentoRegra,
    BlingOrder,
    Integration,
    User,
    UserRole,
)
from app.redis_client import redis
from app.schemas.atendimento import (
    AvaliacaoIn,
    AvaliacaoOut,
    AvaliacaoResumoOut,
    AvaliacaoUnicaOut,
    CanalOut,
    CanalPatch,
    CategoriaOut,
    ConferirIn,
    ConferirOut,
    ConversaDetalheOut,
    ConversaOut,
    ConversaPatch,
    ConversaUnicaOut,
    CopiaAmazonIn,
    CopiaAmazonOut,
    DescartarIn,
    EnvioOut,
    EtiquetaHistoricoOut,
    EtiquetaIn,
    EtiquetaTrocaOut,
    FlagsOut,
    LeituraParadaOut,
    ListaConversasOut,
    ListaRegrasOut,
    LojaResumoOut,
    MensagemOut,
    MetricaIaOut,
    MetricaLojaOut,
    MetricasOut,
    ModeloIn,
    ModeloOut,
    ModeloPatch,
    PedidoAtualizarOut,
    PlataformaResumoOut,
    RascunhoOut,
    RascunhoUnicoOut,
    RegraIn,
    RegraOut,
    RegraPatch,
    ResponderIn,
    ResponderOut,
    RespostaRealOut,
    ResumoOut,
    SincronizarOut,
    SugestaoOut,
)
from app.services import vigia_leitura_atendimento
from app.services.atendimento import (
    acesso,
    amazon_email,
    canais_externos,
    clientes,
    enviar,
    gravar,
    instagram,
    robo,
)
from app.services.atendimento import etiqueta as etiqueta_svc
from app.services.atendimento.constantes import (
    ACAO_OBSERVOU,
    AUTOR_CLIENTE,
    AUTOR_LOJA,
    CANAIS_POR_PLATAFORMA,
    CANAIS_SEMPRE_POS_VENDA,
    CANAL_COMENTARIO,
    CANAL_EMAIL,
    CANAL_PERGUNTA,
    CATEGORIAS,
    CATEGORIAS_INFO,
    CATEGORIAS_SO_HUMANO,
    CONVERSA_ABERTA,
    CONVERSA_FECHADA,
    ETIQUETA_AVALIACAO,
    ETIQUETA_MIDIA,
    ETIQUETA_POS_VENDA,
    ETIQUETA_PRE_VENDA,
    ETIQUETAS,
    FONTE_TUTA,
    MODO_AUTO,
    MODO_OBSERVAR,
    MODOS_QUE_ENVIAM,
    MSG_ENVIADA,
    MSG_FALHOU,
    MSG_REVISAR,
    ORIGEM_HUMANO,
    ORIGEM_NOTA,
    ORIGENS_DAVINCI,
    PLATAFORMA_SITE,
    PLATAFORMAS,
    PLATAFORMAS_EXTERNAS,
    PLATAFORMAS_LISTA,
    PLATAFORMAS_REDE,
    PLATAFORMAS_ROBO,
    PRIORIDADE_REGRA_PADRAO,
    RASCUNHO_BLOQUEADO,
    RASCUNHO_DESCARTADO,
    RASCUNHO_EDITADO,
    RASCUNHO_ENVIADO,
    RASCUNHO_PENDENTE,
    RASCUNHO_SUBSTITUIDO,
    STATUS_CANAL_PARADO,
    STATUS_CANAL_SEM_ENDPOINT,
    STATUS_CANAL_SESSAO_CAIU,
    TIPO_NOTA,
    TIPO_REGRA_CATEGORIA,
    limite_caracteres,
    motivo_canal_sem_envio,
    rotulo_etiqueta,
    sla_horas,
)
from app.services.atendimento.enviar import EnvioRecusado
from app.services.mail_atendimento.constantes import ROTULO_TIPO_CAIXA

logger = structlog.get_logger()

# Fase de observação (`SO_ADMIN`): vale para todas as rotas deste router (e
# dos routers irmãos do mesmo prefixo, que usam a mesma dependência), antes
# da permissão fina de cada uma. O /api/atendimento/robo/* é outro router
# (atendimento_robo.py), com token próprio, e não passa por aqui.
#   - 30/09/2026: só os admins de ATENDIMENTO_USUARIOS (vazio = todo admin).
#   - 07/10/2026: a equipe LÊ. Toda pessoa ativa (admin ou não; menos o
#     operador de estoque, que o web prende no /controle-estoque) passa nos
#     métodos de leitura e nas rotas de `ROTAS_DE_QUEM_LE`; o resto é só de
#     quem está na lista (`acesso.pode_mexer`) — 403 `atendimento_so_leitura`.
# Com SO_ADMIN = False volta o fluxo normal de permissões (o recurso
# `atendimento` view/edit/delete de cada rota): no web, o recurso volta para a
# tela de Permissões (RESOURCE_GROUPS do composables/useCan.ts), o item do
# menu para `resource: 'atendimento'` (components/AppSidebar.vue), a página
# para o middleware `permission` (pages/atendimento.vue) e o /api/auth/me
# para a permissão (routers/auth.py).
SO_ADMIN = True

_METODOS_DE_LEITURA = frozenset({"GET", "HEAD", "OPTIONS"})
# As escritas que quem só lê faz (Eduardo, 07/10/2026: "continua só sugerindo
# ali se clicar"). Nada delas sai para a plataforma nem muda a conversa:
#   - "Sugerir agora": a IA escreve uma sugestão para conferir (o pedido pela
#     tela nunca envia — `ia.gerar_rascunho(forcar=True)` só guarda);
#   - 👍/👎 na sugestão: o feedback que o dono quer (quem só lê não troca a
#     nota que OUTRA pessoa deu — `avaliar_rascunho`);
#   - a prévia do texto de uma automática: só renderiza o exemplo (a aba
#     Automáticas a pede ao abrir a regra, até para ver);
#   - o registro de quem abriu o perfil da loja no AdsPower: é só o log de
#     uma abertura que acontece no computador da pessoa ("Abrir na
#     plataforma"), e o perfil só vem para quem vê o cadastro de Lojas.
# A nota interna NÃO entra: fica para sempre na linha do tempo da conversa
# (não há como apagar), e o recado de quem só lê sobre a IA já vai na
# correção do 👎. O caminho é o MOLDE da rota, como no decorador.
ROTAS_DE_QUEM_LE = frozenset(
    {
        ("POST", "/api/atendimento/conversas/{conversa_id}/rascunho"),
        ("POST", "/api/atendimento/rascunhos/{rascunho_id}/avaliacao"),
        ("POST", "/api/atendimento/automacoes/previa"),
        ("POST", "/api/atendimento/adspower/aberto"),
    }
)
SO_LEITURA = {
    "code": "atendimento_so_leitura",
    "detail": "Só leitura por enquanto — sugestões e 👍/👎 liberados. Responder e mudar a "
    "caixa ficam com quem cuida do Atendimento.",
}


def _quem_le_passa(request: Request) -> bool:
    if request.method in _METODOS_DE_LEITURA:
        return True
    rota = request.scope.get("route")
    return (request.method, getattr(rota, "path", None)) in ROTAS_DE_QUEM_LE


async def _so_admin(
    request: Request, user: Annotated[User, Depends(require_active_user)]
) -> User:
    """A trava da caixa (o nome é de 30/09/2026, quando ela era só de admin)."""
    if not SO_ADMIN or acesso.pode_mexer(user):
        return user
    if not acesso.pode_ver(user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail={"code": "atendimento_restrito"})
    if _quem_le_passa(request):
        return user
    raise HTTPException(status.HTTP_403_FORBIDDEN, detail=SO_LEITURA)


router = APIRouter(
    prefix="/api/atendimento", tags=["atendimento"], dependencies=[Depends(_so_admin)]
)

_view_fino = require_permission("atendimento", "view")
_edit_fino = require_permission("atendimento", "edit")
_edit = _edit_fino
_delete = require_permission("atendimento", "delete")


async def _view(user: Annotated[User, Depends(require_active_user)]) -> User:
    """Ler. Na fase de observação, quem a trava deixa ver (`acesso.pode_ver`):
    o recurso fino saiu da tela de Permissões e ninguém o tem. Fora dela, o
    `atendimento.view` de sempre."""
    if SO_ADMIN:
        if not acesso.pode_ver(user):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, detail={"code": "atendimento_restrito"}
            )
        return user
    return await _view_fino(user)


async def _quem_le(user: Annotated[User, Depends(require_active_user)]) -> User:
    """As escritas de `ROTAS_DE_QUEM_LE` (sugerir, 👍/👎, AdsPower): na fase de
    observação, quem vê também faz; fora dela, o `atendimento.edit` de sempre."""
    if SO_ADMIN:
        return await _view(user)
    return await _edit_fino(user)


FILTROS = (
    "todas",
    "aguardando",
    "vencendo",
    "vencidas",
    "com_rascunho",
    "a_conferir",
    "minhas",
    "fechadas",
    # 01/10/2026 (menu Filtrar, como o do Duoke):
    "automatica",
    # Etiqueta = status atual (RF1, 01/10/2026): o menu filtra também pela
    # etiqueta, com os códigos de `constantes.ETIQUETAS` (pré/pós-venda já
    # eram filtro e passaram a ser a etiqueta).
    "pre_venda",
    "pos_venda",
    "reclamacao",
    "devolucao",
    "ag_cancelamento",
    # Avaliação de venda sem resposta da loja (RF8, 02/10/2026).
    "avaliacao",
    # Carrinho abandonado dos sites (RF9) e Mídia — comentário, menção e
    # Direct das redes (RF7), 02/10/2026.
    "carrinho",
    "midia",
    # O e-mail das lojas que ainda não achou o pedido (RF5, 09/10/2026) — a
    # fila "E-mail sem vínculo" de E-mail › Filas, aqui por conversa.
    "email_sem_vinculo",
)
FILTRO_EMAIL_SEM_VINCULO = "email_sem_vinculo"
# A caixa da lista (09/10/2026): "" = a Caixa; "humano" = a Caixa Humano (só o
# que falta responder e a IA não pode — `services/atendimento/humano.py`).
CAIXA_HUMANO = "humano"
CAIXAS = ("", CAIXA_HUMANO)
# Os tipos do chamado dos sites (RF6): os chips SAC / Atacado / Dúvidas e
# sugestões do grupo Site (`?tipo_chamado=`, sobre `dados.mail.caixa`).
TIPOS_CHAMADO = tuple(ROTULO_TIPO_CAIXA)
# "Vencendo" = prazo da plataforma em menos de 2 h (e ainda não vencido).
VENCENDO = timedelta(hours=2)
# A aba "Falta responder" vem pelo PRAZO mais curto (sem prazo no fim), não
# pela recência (decisão de 01/10/2026): a conversa que vence primeiro é a
# primeira da fila. A página seguinte vem por este cursor (`prazo:` + o
# prazo e o id do último item), em vez da `ultima_mensagem_em`.
FILTROS_PELO_PRAZO = ("aguardando",)
_CURSOR_PRAZO = "prazo:"
# "Pergunta vai para o topo" (RF7, 02/10/2026) — só ordena, não tira nada da
# fila. No "Falta responder", o comentário COMUM das redes (elogio, emoji:
# sem pergunta) vai para o FIM, depois de tudo o que pede resposta; no filtro
# Mídia, a pergunta pendente vem PRIMEIRO e o resto pela recência. O cursor
# leva o grupo: `prazo:comum:<prazo>|<id>` (já nos comentários comuns) e
# `pergunta:<ISO>` (ainda nas perguntas do Mídia).
_CURSOR_COMUM = "comum:"
_CURSOR_PERGUNTA = "pergunta:"
FILTROS_PERGUNTA_PRIMEIRO = ("midia",)
# As DMs do Instagram esperando entram na mesma ordem pelo prazo: o
# Instagram não pagina por prazo, então vêm todas (até este teto) e o
# cursor é aplicado aqui.
MAX_INSTAGRAM_PELO_PRAZO = 500
# Busca por SKU (`bling_orders.item_codigo`): só pedidos deste período, e só
# a partir de 3 letras (o espelho tem ~100 mil linhas-item).
JANELA_BUSCA_SKU = timedelta(days=365)
MIN_BUSCA_SKU = 3
MAX_MENSAGENS_DETALHE = 300

# Botão "Sincronizar": um pedido por minuto basta — o cron já lê a cada 2.
CHAVE_SINCRONIZAR = "atendimento:sincronizar:manual"
TRAVA_SINCRONIZAR_S = 60

# Métricas: quanto antes do período ler para saber se o primeiro "turno" do
# cliente dentro dele começou lá atrás (e então não é dele).
_MARGEM_METRICAS = timedelta(days=3)

_ACAO_PELO_STATUS = {
    RASCUNHO_ENVIADO: "enviou_igual",
    RASCUNHO_EDITADO: "editou",
    RASCUNHO_DESCARTADO: "descartou",
    RASCUNHO_SUBSTITUIDO: "escreveu_do_zero",
    RASCUNHO_BLOQUEADO: "descartou",
}
# `ACAO_OBSERVOU` (constantes) = nota dada a uma sugestão AINDA pendente
# (modo observação: nada sai pelo DaVinci, a pessoa só diz se a IA acertou).
# Não é "enviou"/"editou" — nada saiu; o que ensina é a NOTA: o 👍 faz o
# texto da sugestão virar exemplo aprovado, o 👎 com correção vira correção
# no prompt (parte 2, P3, em `ia.py`). Quando a sugestão sai da caixa (a loja
# respondeu por fora, alguém descartou), a próxima nota promove a ação à de
# verdade — e o envio pelo DaVinci também promove (enviar._avaliar_rascunho).

# Sugestões que NÃO saíram pelo DaVinci: o que a IA teria respondido.
_STATUS_SUGESTAO = (
    RASCUNHO_PENDENTE,
    RASCUNHO_SUBSTITUIDO,
    RASCUNHO_BLOQUEADO,
    RASCUNHO_DESCARTADO,
)
MAX_SUGESTOES_DETALHE = 50

# Da pior para a melhor. A loja aparece na barra com o PIOR estado dos canais
# dela: o ML com a pós-venda sem permissão não está sendo lido por inteiro.
# `parado`/`sessao_caiu` só nas lojas do robô do Mac mini (Temu/AliExpress):
# o robô sem sinal é o pior — a loja inteira não está sendo lida.
_GRAVIDADE_STATUS = (
    STATUS_CANAL_PARADO,
    STATUS_CANAL_SESSAO_CAIU,
    "sem_escopo",
    # Só do site: a rota do carrinho ainda não está publicada (o site não é
    # lido até o pacote subir) — tão grave quanto o erro.
    STATUS_CANAL_SEM_ENDPOINT,
    "erro",
    "desligado",
    "novo",
    "ok",
)
_ROTULO_CANAL = {
    "chat": "Chat",
    "pergunta": "Perguntas",
    "pos_venda": "Pós-venda",
    "email": "E-mail",
    "carrinho": "Carrinho",
    "comentario": "Comentários",
}

# Botão "atualizar" do painel Pedido: uma ida à loja por conversa por minuto.
# O retrato já se renova sozinho a cada 30 min no sync; o botão é para a
# pessoa que está com o cliente na frente — não para virar rajada na API.
PLATAFORMAS_COM_PEDIDO = ("shopee", "ml")
TRAVA_PEDIDO_S = 60
_ENRIQUECER = "app.services.atendimento.enriquecer"
# A trava por conversa não segura quem percorre a lista clicando (ou um
# script com a sessão de alguém): 200 conversas × até 3 GET na mesma loja em
# segundos, e de novo a cada minuto. Então há também um teto por LOJA (é
# onde a API da plataforma conta) e por PESSOA, por minuto. Acima dele, o
# painel fica com o retrato que tem (`motivo="limite"`), sem ir à loja.
LIMITE_PEDIDO_POR_LOJA = 10
LIMITE_PEDIDO_POR_PESSOA = 20
JANELA_LIMITE_PEDIDO_S = 60
# O status do canal que o sync nem lê (`sync.STATUS_DESLIGADO`; o router não
# importa o sync ao subir).
_CANAL_DESLIGADO = "desligado"

# Parte 2: módulos de outros lotes, importados na hora de usar e pelo NOME
# (como o enriquecimento) — o router sobe sem eles, e o teste troca o módulo
# inteiro em `sys.modules`.
_LOJAS = "app.services.atendimento.lojas"
_CLIENTE = "app.services.atendimento.cliente"
# As avaliações de venda (RF8): a pior nota pendente no selo da lista.
_AVALIACOES = "app.services.atendimento.avaliacoes"
_MANUAL = "app.services.atendimento.manual"
# Trava da TRANSAÇÃO de quem grava regra: a conferência de conflito e o
# INSERT/UPDATE ficam juntos. Sem ela, dois "Salvar" ao mesmo tempo (duas
# abas, o importador do manual rodando) passavam os dois pela conferência e
# nasciam as duas regras que batem. O importador deve pegar a mesma chave.
TRAVA_REGRAS = "atendimento:regras"


def _chave_pedido(conversa_id: UUID) -> str:
    return f"atd:pedido:atualizar:{conversa_id}"


def _chave_limite_loja(integration_id: UUID) -> str:
    return f"atd:pedido:atualizar:loja:{integration_id}"


def _chave_limite_pessoa(user_id: UUID) -> str:
    return f"atd:pedido:atualizar:pessoa:{user_id}"


# "Sugerir agora" de quem só lê (fase de observação, 07/10/2026): a caixa
# abriu para a equipe inteira. Dois freios, só para quem só lê (quem mexe
# segue sem nenhum, como antes):
#   - um teto por PESSOA por hora, para ninguém gastar o dia do servidor
#     sozinho;
#   - o teto DIÁRIO da IA (`atendimento_ia_teto_diario`, o freio de gasto do
#     cron — `ia._dentro_do_teto`): o clique de quem só lê conta no mesmo
#     contador do dia e para quando ele acaba. Conta 1 chamada por clique (a
#     classificação, quando há regra de assunto, é a 2ª e fica de fora).
SUGERIR_POR_HORA_QUEM_LE = 30
_JANELA_SUGERIR_S = 3600
TETO_DIARIO_IA = {
    "code": "teto_diario_ia",
    "detail": "As sugestões da IA de hoje acabaram (teto diário do servidor) — amanhã voltam.",
}


def _so_le(user: User) -> bool:
    """Fase de observação e a pessoa NÃO está na lista de quem mexe."""
    return SO_ADMIN and not acesso.pode_mexer(user)


def _chave_sugerir_pessoa(user_id: UUID) -> str:
    return f"atd:sugerir:pessoa:{user_id}"


async def _dentro_do_limite_sugerir(user: User) -> bool:
    """Quem só lê pede até SUGERIR_POR_HORA_QUEM_LE sugestões por hora.

    Quem mexe não conta. A validade é reposta se a chave ficou sem ela (o
    processo caiu entre o INCR e o EXPIRE) — senão a pessoa ficaria barrada
    para sempre, como no `_contar_na_janela`. Redis fora do ar = deixa passar
    (o limite por minuto do provedor segura)."""
    if not _so_le(user):
        return True
    chave = _chave_sugerir_pessoa(user.id)
    try:
        usadas = int(await redis.incr(chave))
        if usadas == 1 or await redis.ttl(chave) < 0:
            await redis.expire(chave, _JANELA_SUGERIR_S)
    except Exception as e:  # noqa: BLE001
        logger.warning("atendimento_sugerir_limite_indisponivel", err=type(e).__name__)
        return True
    return usadas <= SUGERIR_POR_HORA_QUEM_LE


# ── Ajudantes ─────────────────────────────────────────────────────────────


def _nome(user: User | None) -> str | None:
    if user is None:
        return None
    return (user.name or user.email or "").strip() or None


def _utc(quando: datetime | None) -> datetime | None:
    if quando is None:
        return None
    return quando if quando.tzinfo else quando.replace(tzinfo=UTC)


def _no_escopo(scope: TeamScope, integration_id: UUID | None) -> bool:
    return scope.unrestricted or (
        integration_id is not None and integration_id in scope.integration_ids
    )


def _clausula_escopo(scope: TeamScope, coluna):
    """WHERE da equipe sobre uma coluna `integration_id`; None = sem filtro."""
    if scope.unrestricted:
        return None
    if not scope.integration_ids:
        return false()
    return coluna.in_(scope.integration_ids)


async def _nomes(session: AsyncSession, ids: set[UUID | None]) -> dict[UUID, str | None]:
    ids = {i for i in ids if i is not None}
    if not ids:
        return {}
    return {
        u.id: _nome(u)
        for u in (await session.execute(select(User).where(User.id.in_(ids)))).scalars()
    }


def _modulo(nome: str) -> Any:
    """O módulo pelo nome, na hora de usar; None se ele (ainda) não existe."""
    try:
        return importlib.import_module(nome)
    except ImportError as e:
        logger.warning("atendimento_modulo_ausente", modulo=nome, err=type(e).__name__)
        return None


async def _nomes_das_lojas(session: AsyncSession, ids: set[UUID | None]) -> dict[UUID, str]:
    """O nome da LOJA de cada integração — o que o Duoke mostra (parte 2, P2).

    A integração "mega" é a loja "Shopee Marquezini", que o Duoke chama de
    "Marquezini": quem atende reconhece a loja, não o apelido da conexão.
    Achar a loja ligada e tirar o prefixo da plataforma é com
    `lojas.nome_da_loja` (uma função só, a mesma que grava `conversa.conta`
    no sync); aqui ela é chamada uma vez por integração da resposta — assim a
    conversa antiga e a loja renomeada saem com o nome de hoje.

    Sem o módulo, ou se ele falhar, fica o nome da integração: o nome nunca
    derruba a fila. O SAVEPOINT impede que uma consulta que falhe lá dentro
    deixe a transação abortada para o resto da resposta. Quem chama lê os
    atributos dos objetos ANTES (o rollback do SAVEPOINT pode expirá-los).
    """
    ids = {i for i in ids if i is not None}
    if not ids:
        return {}
    integracoes = list(
        (await session.execute(select(Integration).where(Integration.id.in_(ids)))).scalars()
    )
    nomes = {i.id: i.name for i in integracoes}
    lojas_svc = _modulo(_LOJAS)
    if lojas_svc is None:
        return nomes
    try:
        async with session.begin_nested():
            for integ in integracoes:
                nome = await lojas_svc.nome_da_loja(session, integ)
                if isinstance(nome, str) and nome.strip():
                    nomes[integ.id] = nome.strip()
    except Exception as e:  # noqa: BLE001 — fica o nome da integração
        logger.warning("atendimento_nome_da_loja_falhou", err=type(e).__name__)
    return nomes


async def _com_nome_da_loja(session: AsyncSession, itens: list[dict[str, Any]]) -> None:
    """Troca `conta` pelo nome da loja nas linhas da lista (as do Instagram ficam)."""
    nomes = await _nomes_das_lojas(
        session, {i.get("integration_id") for i in itens if not i.get("somente_leitura")}
    )
    for item in itens:
        nome = nomes.get(item.get("integration_id"))
        if nome and not item.get("somente_leitura"):
            item["conta"] = nome


def _recusa_http(e: EnvioRecusado) -> HTTPException:
    """Trava do envio → 409 (texto reprovado → 422), com o `code` estável."""
    codigo = 422 if e.code == enviar.RECUSA_TEXTO_INVALIDO else 409
    return HTTPException(codigo, detail={"code": e.code, "detail": e.detail})


def _somente_leitura() -> HTTPException:
    return _recusa_http(
        EnvioRecusado(
            enviar.RECUSA_SOMENTE_LEITURA,
            "Instagram é só leitura aqui: responda pela caixa de entrada do Instagram.",
        )
    )


def _uuid_ou_404(texto: str, code: str) -> UUID:
    try:
        return UUID(str(texto))
    except ValueError as e:
        raise HTTPException(404, detail={"code": code}) from e


async def _conversa_ou_404(
    session: AsyncSession, conversa_id: str, scope: TeamScope
) -> AtendimentoConversa:
    uid = _uuid_ou_404(conversa_id, "conversa_nao_encontrada")
    c = await session.get(AtendimentoConversa, uid)
    if c is None or not _no_escopo(scope, c.integration_id):
        raise HTTPException(404, detail={"code": "conversa_nao_encontrada"})
    return c


def _pendente_existe():
    return (
        exists()
        .where(
            AtendimentoRascunho.conversa_id == AtendimentoConversa.id,
            AtendimentoRascunho.status == RASCUNHO_PENDENTE,
        )
        .correlate(AtendimentoConversa)
    )


def _a_conferir_existe():
    """Há resposta NOSSA em `revisar` (pode ter saído): alguém precisa conferir.

    Ela conta como resposta (a conversa sai da fila para ninguém responder por
    cima), então sem este sinal a conversa sumiria sem ninguém saber se o
    comprador recebeu — timeout, deploy no meio do envio, envio automático.
    """
    return (
        exists()
        .where(
            AtendimentoMensagem.conversa_id == AtendimentoConversa.id,
            AtendimentoMensagem.status == MSG_REVISAR,
            AtendimentoMensagem.origem.in_(ORIGENS_DAVINCI),
        )
        .correlate(AtendimentoConversa)
    )


def _pendentes():
    """Mensagens do comprador desde a última resposta de VERDADE da loja.

    A bolinha vermelha da conversa (01/10/2026, a do Duoke): o "não lida" da
    Shopee zera quando o robô do Duoke responde sozinho; este número não —
    `ultima_da_loja_em` ignora a resposta automática (`gravar.recalcular`).
    Só vale para quem está esperando resposta (`_resumo_dict` zera o resto).
    """
    momento = func.coalesce(AtendimentoMensagem.enviada_em, AtendimentoMensagem.created_at)
    return (
        select(func.count())
        .where(
            AtendimentoMensagem.conversa_id == AtendimentoConversa.id,
            AtendimentoMensagem.autor == AUTOR_CLIENTE,
            or_(
                AtendimentoConversa.ultima_da_loja_em.is_(None),
                momento > AtendimentoConversa.ultima_da_loja_em,
            ),
        )
        .correlate(AtendimentoConversa)
        .scalar_subquery()
    )


def _ultimo_tipo():
    """O tipo da última mensagem da conversa (subconsulta correlacionada).

    Sem coluna própria: a lista mostra 50 conversas por vez, e o índice por
    `conversa_id` acha a última de cada uma sem varrer. Mesma ordem do
    detalhe (relógio da plataforma; sem ele, quando a linha nasceu).
    """
    momento = func.coalesce(AtendimentoMensagem.enviada_em, AtendimentoMensagem.created_at)
    return (
        select(AtendimentoMensagem.tipo)
        .where(
            AtendimentoMensagem.conversa_id == AtendimentoConversa.id,
            # A nota interna (só a equipe vê) não é a "última mensagem" da
            # prévia — como em `gravar.recalcular` (`constantes.e_nota`).
            AtendimentoMensagem.origem != ORIGEM_NOTA,
            AtendimentoMensagem.tipo != TIPO_NOTA,
        )
        .order_by(momento.desc(), AtendimentoMensagem.created_at.desc())
        .limit(1)
        .correlate(AtendimentoConversa)
        .scalar_subquery()
    )


def _etiqueta_efetiva():
    """A etiqueta da conversa; a ainda não calculada (NULL) pela regra de pré/pós-venda.

    É por ela que o menu Filtrar filtra e conta (01/10/2026): a conversa que
    o motor ainda não classificou (antes do preenchimento, ou do primeiro
    recálculo) não some do Pré-venda/Pós-venda. A regra é a mesma base do
    motor (`etiqueta_fatos.e_pos_venda`): pós-venda/SAC/e-mail/reclamação
    sempre depois da compra; pergunta, sempre antes; chat, pelo pedido. O
    comentário/menção das redes (02/10/2026) é sempre Mídia.
    """
    pedido = func.coalesce(func.btrim(AtendimentoConversa.pedido_marketplace), "")
    pos = or_(
        AtendimentoConversa.canal.in_(CANAIS_SEMPRE_POS_VENDA),
        and_(AtendimentoConversa.canal != CANAL_PERGUNTA, pedido != ""),
    )
    return func.coalesce(
        AtendimentoConversa.etiqueta,
        case(
            (AtendimentoConversa.canal == CANAL_COMENTARIO, ETIQUETA_MIDIA),
            (pos, ETIQUETA_POS_VENDA),
            else_=ETIQUETA_PRE_VENDA,
        ),
    )


def _email_sem_vinculo():
    """O filtro "E-mail sem vínculo" (RF5, 09/10/2026), pela CONVERSA.

    A conversa de e-mail da PONTE (canal e-mail, `dados.fonte = 'tuta'` com
    `dados.mail`) que ainda não achou o pedido — a mesma régua do estado
    `sem_vinculo` da ponte (`ponte.py`: gravado só com pedido). O chamado do
    site (plataforma `site`) nunca tem pedido e fica de fora (ele tem os chips
    do tipo). A situação (aberta/fechada) fica com quem chama.
    """
    pedido = func.coalesce(func.btrim(AtendimentoConversa.pedido_marketplace), "")
    return and_(
        AtendimentoConversa.canal == CANAL_EMAIL,
        AtendimentoConversa.dados["fonte"].astext == FONTE_TUTA,
        AtendimentoConversa.dados.has_key("mail"),
        pedido == "",
        AtendimentoConversa.plataforma != PLATAFORMA_SITE,
    )


def _tipo_do_chamado():
    """O tipo do chamado do site (RF6): `dados.mail.caixa` — sac | atacado | duvidas."""
    return AtendimentoConversa.dados["mail"]["caixa"].astext


async def _chamados_por_tipo(session: AsyncSession, cond) -> dict[str, int]:
    """Os chamados NÃO fechados dos sites por tipo (os números dos chips do
    grupo Site), no escopo de quem pede — uma consulta, só quando há site."""
    tipo = _tipo_do_chamado()
    q = select(*[func.count().filter(tipo == t) for t in TIPOS_CHAMADO]).where(
        AtendimentoConversa.plataforma == PLATAFORMA_SITE,
        AtendimentoConversa.canal == CANAL_EMAIL,
        AtendimentoConversa.situacao != CONVERSA_FECHADA,
    )
    if cond is not None:
        q = q.where(cond)
    linha = (await session.execute(q)).one()
    return {t: int(n or 0) for t, n in zip(TIPOS_CHAMADO, linha, strict=True)}


def _cursor(
    antes_de: str | None, *, pelo_prazo: bool
) -> tuple[datetime | None, str | None, bool, int]:
    """O cursor da página (`antes_de`) → (momento, id, sem_prazo, grupo). Inválido → 422.

    Ordem por recência: ISO da `ultima_mensagem_em` → (momento, None, False,
    1); `pergunta:<ISO>` (Mídia, ainda nas perguntas) → grupo 0.
    Ordem pelo prazo: `prazo:<ISO ou vazio>|<id>` → (prazo, id, prazo vazio,
    0); `prazo:comum:…` (já nos comentários comuns) → grupo 1.
    Sem cursor: o começo (grupo 0).
    """
    bruto = (antes_de or "").strip()
    if not bruto:
        return None, None, False, 0
    invalido = HTTPException(422, detail={"code": "cursor_invalido"})
    if pelo_prazo != bruto.startswith(_CURSOR_PRAZO):
        raise invalido
    grupo = 0
    if pelo_prazo:
        prazo_txt, sep, ident = bruto[len(_CURSOR_PRAZO) :].partition("|")
        if prazo_txt.startswith(_CURSOR_COMUM):
            grupo = 1
            prazo_txt = prazo_txt[len(_CURSOR_COMUM) :]
        if not sep or not ident.strip():
            raise invalido
        if not prazo_txt.strip():
            return None, ident.strip(), True, grupo
        bruto = prazo_txt
    else:
        ident = None
        if bruto.startswith(_CURSOR_PERGUNTA):
            bruto = bruto[len(_CURSOR_PERGUNTA) :]
        else:
            grupo = 1
    texto = bruto.strip().replace("Z", "+00:00")
    if "T" in texto:
        # O "+" do fuso que chegou sem escape na URL vira espaço.
        texto = texto.replace(" ", "+")
    try:
        momento = datetime.fromisoformat(texto)
    except ValueError as e:
        raise invalido from e
    return _utc(momento), (ident.strip() if ident else None), False, grupo


def _grupo_do_prazo(item: dict[str, Any]) -> int:
    """No "Falta responder": 1 = comentário comum das redes (vai para o fim), 0 = o resto."""
    return int(item.get("canal") == CANAL_COMENTARIO and not item.get("eh_pergunta"))


def _cursor_do_prazo(item: dict[str, Any]) -> str:
    prazo = item.get("prazo_resposta_em")
    grupo = _CURSOR_COMUM if _grupo_do_prazo(item) else ""
    return f"{_CURSOR_PRAZO}{grupo}{prazo.isoformat() if prazo else ''}|{item['id']}"


def _chave_do_prazo(item: dict[str, Any]) -> tuple:
    """Ordem da aba "Falta responder": o comentário comum das redes no fim; dentro de
    cada grupo, prazo mais curto primeiro, sem prazo no fim, id no empate."""
    prazo = item.get("prazo_resposta_em")
    return (
        _grupo_do_prazo(item),
        prazo is None,
        prazo.timestamp() if prazo else 0.0,
        str(item["id"]),
    )


def _depois_do_cursor(
    item: dict[str, Any],
    prazo: datetime | None,
    ident: str | None,
    sem_prazo: bool,
    grupo: int = 0,
) -> bool:
    """O item vem DEPOIS do cursor na ordem pelo prazo? (o mesmo corte do SQL)."""
    if ident is None:
        return True
    return _chave_do_prazo(item) > (grupo, sem_prazo, prazo.timestamp() if prazo else 0.0, ident)


def _pergunta_pendente():
    """SQL: conversa de comentário com pergunta ainda sem resposta da marca (RF7).

    `dados.eh_pergunta` é de `redes.atualizar_pergunta`. Sempre verdadeiro ou
    falso (nunca NULL): o NOT do cursor não perde linha.
    """
    return and_(
        AtendimentoConversa.aguardando_resposta.is_(True),
        AtendimentoConversa.canal == CANAL_COMENTARIO,
        AtendimentoConversa.dados["eh_pergunta"].as_boolean().is_(True),
    )


def _comentario_comum():
    """SQL: conversa de comentário das redes que NÃO é pergunta (o fim do "Falta responder")."""
    return and_(
        AtendimentoConversa.canal == CANAL_COMENTARIO,
        AtendimentoConversa.dados["eh_pergunta"].as_boolean().is_not(True),
    )


def _busca_no_bling(termo: str):
    """Os nº na plataforma dos pedidos do Bling com este nº do Bling ou com este SKU.

    A busca da lista (01/10/2026) acha a conversa também pelo nº do Bling
    (`bling_orders.numero`, exato) e pelo SKU do item (`item_codigo`, pedaço,
    nos pedidos do último ano). CTE: o Postgres lê o espelho uma vez para as
    três colunas que casam (pedido, pack e order do ML).
    """
    conds = [BlingOrder.numero == termo]
    if len(termo) >= MIN_BUSCA_SKU:
        conds.append(
            and_(
                BlingOrder.data >= datetime.now(UTC) - JANELA_BUSCA_SKU,
                BlingOrder.item_codigo.ilike(f"%{_escapar_like(termo)}%", escape="\\"),
            )
        )
    return (
        select(BlingOrder.numeroloja)
        .where(or_(*conds), BlingOrder.numeroloja.is_not(None))
        .cte("atd_busca_bling")
    )


def _do_dados(c: AtendimentoConversa, chave: str) -> dict[str, Any] | None:
    """Um cartão guardado em `conversa.dados` (pedido_mkt, produto); formato estranho → None."""
    valor = (c.dados or {}).get(chave) if isinstance(c.dados, dict) else None
    return dict(valor) if isinstance(valor, dict) and valor else None


# Os mesmos hosts que o leitor da caixa aceita ao gravar
# (`amazon_email._links_do_rodape`). Aqui é o cinto da saída: `conversa.dados`
# é JSON livre, e o que sai daqui vira botão/link que a pessoa clica.
_HOSTS_SELLER_CENTRAL = frozenset({"sellercentral.amazon.com.br", "sellercentral.amazon.com"})
# Id do caso (o real é um UUID): letra, dígito e hífen — nada que mexa no
# resto da URL que a tela monta com ele.
_RE_CASO_AMAZON = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{7,63}")


def _link_seller_central(valor: Any) -> str | None:
    """O link como veio, se for https do próprio Seller Central; senão None.

    Nada de "consertar" o link: o "não precisa de resposta" é assinado
    (`h=`), e mexer nele o invalida.
    """
    if not isinstance(valor, str) or not valor.strip():
        return None
    link = valor.strip()
    try:
        partes = urlsplit(link)
    except ValueError:
        return None
    if (
        partes.scheme != "https"
        or partes.hostname not in _HOSTS_SELLER_CENTRAL
        or partes.username
        or partes.password
    ):
        return None
    return link


def _copias_a_conferir(c: AtendimentoConversa) -> list[CopiaAmazonOut]:
    """As cópias do Seller Central que empataram nesta conversa — se ainda vale avisar.

    O leitor da caixa (`amazon_email._marcar_a_conferir`) guarda a cópia nas
    conversas do mesmo pedido/nome quando ela empata entre duas ou mais:
    nenhuma sai da fila (seria chute), mas a pessoa precisa saber que talvez
    já tenha sido respondida — e escolhe com 1 clique. Só enquanto a
    conversa aguarda e nenhuma pergunta MAIS NOVA que a cópia chegou —
    depois disso o aviso é sobre outra pergunta.
    """
    if c.plataforma != "amazon" or not c.aguardando_resposta:
        return []
    do_cliente = _utc(c.ultima_do_cliente_em)
    return [
        CopiaAmazonOut(
            message_id=copia.message_id,
            em=copia.em,
            texto=copia.texto,
            pedido=copia.pedido,
            outras=len([i for i in copia.candidatas if i != str(c.id)]),
            pode_escolher=not copia.legada,
        )
        for copia in amazon_email.copias_a_conferir(c.dados)
        if do_cliente is None or do_cliente <= copia.em
    ]


def _links_amazon(c: AtendimentoConversa) -> dict[str, Any]:
    """Os links do rodapé do e-mail da Amazon para o cabeçalho da conversa.

    Outra plataforma (ou `dados` estranho) → tudo None: a tela esconde os
    botões. Quem grava é `amazon_email._dados_do_email` (e, a cópia
    ambígua, `amazon_email._marcar_a_conferir`; `amazon_copia_a_conferir_em`
    é a hora da mais nova delas, para a tela antiga).
    """
    vazio: dict[str, Any] = {
        "amazon_link_sem_resposta": None,
        "amazon_link_caso": None,
        "amazon_caso_id": None,
        "amazon_copia_a_conferir_em": None,
        "amazon_copias_a_conferir": [],
    }
    if c.plataforma != "amazon" or not isinstance(c.dados, dict):
        return vazio
    caso_id = c.dados.get("amazon_caso_id")
    caso_id = caso_id.strip() if isinstance(caso_id, str) else None
    copias = _copias_a_conferir(c)
    return {
        "amazon_link_sem_resposta": _link_seller_central(c.dados.get("amazon_link_sem_resposta")),
        "amazon_link_caso": _link_seller_central(c.dados.get("amazon_link_caso")),
        "amazon_caso_id": caso_id if caso_id and _RE_CASO_AMAZON.fullmatch(caso_id) else None,
        "amazon_copia_a_conferir_em": copias[-1].em if copias else None,
        "amazon_copias_a_conferir": copias,
    }


def _resumo_dict(
    c: AtendimentoConversa,
    *,
    tem_rascunho: bool,
    atribuido_nome: str | None,
    a_conferir: bool = False,
    ultimo_tipo: str | None = None,
    pendentes: int = 0,
    humano: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": str(c.id),
        "plataforma": c.plataforma,
        "canal": c.canal,
        "conta": c.conta,
        "integration_id": c.integration_id,
        "comprador_nome": c.comprador_nome,
        "comprador_avatar": c.comprador_avatar,
        "pedido_marketplace": c.pedido_marketplace,
        "anuncio_titulo": c.anuncio_titulo,
        "ultima_mensagem_em": _utc(c.ultima_mensagem_em),
        "ultima_mensagem_resumo": c.ultima_mensagem_resumo,
        "ultima_mensagem_tipo": gravar.tipo_previa(ultimo_tipo),
        "ultima_autor": c.ultima_autor,
        "aguardando_resposta": c.aguardando_resposta,
        "prazo_resposta_em": _utc(c.prazo_resposta_em),
        "situacao": c.situacao,
        "nao_lidas": c.nao_lidas or 0,
        "pendentes": int(pendentes or 0) if c.aguardando_resposta else 0,
        "tem_rascunho": bool(tem_rascunho),
        "atribuido_a": c.atribuido_a,
        "atribuido_a_nome": atribuido_nome,
        "ia_pausada": c.ia_pausada,
        "sem_resposta_necessaria": c.sem_resposta_necessaria,
        # Comentário das redes com pergunta ainda sem resposta (RF7): o selo
        # "pergunta" na lista e a frente da fila no Mídia/"Falta responder".
        "eh_pergunta": bool(
            c.canal == CANAL_COMENTARIO
            and c.aguardando_resposta
            and isinstance(c.dados, dict)
            and c.dados.get("eh_pergunta") is True
        ),
        "somente_leitura": False,
        "envio_a_conferir": bool(a_conferir),
        # Etiqueta = status atual: quem grava é `services/atendimento/etiqueta`.
        "etiqueta": c.etiqueta,
        "etiqueta_desde": _utc(c.etiqueta_desde),
        "etiquetas_secundarias": [
            e for e in (c.etiquetas_secundarias or []) if isinstance(e, str)
        ],
        "etiqueta_manual": bool(c.etiqueta_manual),
        # Caixa Humano (09/10/2026): os motivos, só se ela está lá AGORA
        # (`humano.para_tela` sobre o `humano.triagem_da_linha_sql`).
        "humano": humano,
    }


async def _conversa_out(session: AsyncSession, c: AtendimentoConversa) -> ConversaOut:
    tem = bool(
        await session.scalar(
            select(AtendimentoRascunho.id)
            .where(
                AtendimentoRascunho.conversa_id == c.id,
                AtendimentoRascunho.status == RASCUNHO_PENDENTE,
            )
            .limit(1)
        )
    )
    a_conferir = bool(
        await session.scalar(
            select(AtendimentoMensagem.id)
            .where(
                AtendimentoMensagem.conversa_id == c.id,
                AtendimentoMensagem.status == MSG_REVISAR,
                AtendimentoMensagem.origem.in_(ORIGENS_DAVINCI),
            )
            .limit(1)
        )
    )
    nomes = await _nomes(session, {c.atribuido_a})
    ultimo_tipo = await session.scalar(
        select(_ultimo_tipo()).where(AtendimentoConversa.id == c.id)
    )
    pendentes = await session.scalar(select(_pendentes()).where(AtendimentoConversa.id == c.id))
    from app.services.atendimento import humano as humano_svc

    na_caixa_humano = await session.scalar(
        select(humano_svc.triagem_da_linha_sql()).where(AtendimentoConversa.id == c.id)
    )
    base = _resumo_dict(
        c,
        tem_rascunho=tem,
        atribuido_nome=nomes.get(c.atribuido_a),
        a_conferir=a_conferir,
        ultimo_tipo=ultimo_tipo,
        pendentes=pendentes or 0,
        humano=humano_svc.para_tela(na_caixa_humano, ia_pausada=c.ia_pausada),
    )
    base.update(
        comprador_id=c.comprador_id,
        anuncio_id=c.anuncio_id,
        bloqueio_motivo=c.bloqueio_motivo,
        pode_enviar_ate=_utc(c.pode_enviar_ate),
        # Vai também no PATCH e no "conferir" (mesmo `_conversa_out`): a tela
        # junta com spread, e o link do caso não pode sumir depois de um clique.
        **_links_amazon(c),
        email_da_ponte=_email_da_ponte(c),
    )
    # O cabeçalho com o nome da loja (lido DEPOIS dos atributos: ver
    # `_nomes_das_lojas`).
    await _com_nome_da_loja(session, [base])
    await _com_estrelas_da_avaliacao(session, [base])
    return ConversaOut(**base)


async def _com_estrelas_da_avaliacao(session: AsyncSession, itens: list[dict[str, Any]]) -> None:
    """`avaliacao_estrelas` (a pior nota PENDENTE) nos itens com a etiqueta Avaliação.

    O selo "AVALIAÇÃO ★★" da lista (RF8). Só os itens com a etiqueta (ou o
    indicador) de avaliação — uma consulta de conversas e uma de avaliações
    para a página toda. Enfeite: um erro aqui deixa o selo sem as estrelas,
    nunca derruba a lista (SAVEPOINT).
    """
    alvo = [
        i
        for i in itens
        if not i.get("somente_leitura")
        and (
            i.get("etiqueta") == ETIQUETA_AVALIACAO
            or ETIQUETA_AVALIACAO in (i.get("etiquetas_secundarias") or [])
        )
    ]
    if not alvo:
        return
    try:
        ids = [UUID(str(i["id"])) for i in alvo]
        async with session.begin_nested():
            conversas = (
                (
                    await session.execute(
                        select(AtendimentoConversa).where(AtendimentoConversa.id.in_(ids))
                    )
                )
                .scalars()
                .all()
            )
            estrelas = await _modulo(_AVALIACOES).estrelas_pendentes_em_lote(session, conversas)
    except Exception as e:  # noqa: BLE001 — o selo fica sem estrelas
        logger.warning("atendimento_lista_estrelas_falhou", erro=type(e).__name__)
        return
    for i in alvo:
        i["avaliacao_estrelas"] = estrelas.get(UUID(str(i["id"])))


def _simulado(m: AtendimentoMensagem) -> bool:
    """Esta resposta "saiu" pelo simulador (só local) — não chegou a ninguém.

    Vem do que o envio gravou (`payload.envio.simulador`), não da chave de
    hoje: com o simulador ligado e a Amazon na exceção, a resposta da Amazon
    CHEGA ao comprador, e a tela não pode dizer o contrário.
    """
    envio = (m.payload or {}).get("envio") if isinstance(m.payload, dict) else None
    return isinstance(envio, dict) and envio.get("simulador") is True


def _mensagem_out(
    m: AtendimentoMensagem, *, conversa: AtendimentoConversa, nomes: dict[UUID, str | None]
) -> MensagemOut:
    if m.autor == AUTOR_CLIENTE:
        autor_nome = conversa.comprador_nome
    elif m.origem in (ORIGEM_HUMANO, ORIGEM_NOTA):
        # Quem da equipe enviou — ou escreveu a nota interna.
        autor_nome = nomes.get(m.autor_user_id) if m.autor_user_id else None
    else:
        autor_nome = None
    return MensagemOut(
        id=str(m.id),
        autor=m.autor,
        origem=m.origem,
        autor_nome=autor_nome,
        tipo=m.tipo,
        texto=m.texto,
        anexos=list(m.anexos or []),
        # Resposta que não saiu não tem relógio da plataforma: vale a hora
        # em que a pessoa apertou "Enviar".
        enviada_em=_utc(m.enviada_em or m.created_at),
        status=m.status,
        erro=m.erro,
        simulado=_simulado(m),
        email=_email_da_mensagem(m),
    )


def _email_da_ponte(c: AtendimentoConversa) -> bool:
    """A conversa de e-mail é da PONTE da Central (não a da Amazon pelo Gmail)?"""
    from app.services.mail_atendimento import ponte as mail_ponte

    return mail_ponte.e_conversa_da_ponte(c)


def _email_da_mensagem(m: AtendimentoMensagem) -> dict[str, Any] | None:
    """O resumo do e-mail da mensagem (a ponte da Central de e-mail), sem o texto."""
    payload = m.payload if isinstance(m.payload, dict) else {}
    for chave in ("mail", "mail_envio"):
        valor = payload.get(chave)
        if isinstance(valor, dict):
            return {"tipo": "recebido" if chave == "mail" else "resposta", **valor}
    return None


def _rascunho_out(r: AtendimentoRascunho | None) -> RascunhoOut | None:
    if r is None:
        return None
    return RascunhoOut(
        id=r.id,
        texto=r.texto,
        categoria=r.categoria,
        confianca=r.confianca,
        precisa_humano=r.precisa_humano,
        motivo=r.motivo,
        validador_erros=list(r.validador_erros or []),
        status=r.status,
        created_at=_utc(r.created_at),
    )


def _canal_out(
    canal: AtendimentoCanal, conta: str | None, *, integracao: str | None = None
) -> CanalOut:
    status_canal, ultimo_erro = canal.status, canal.ultimo_erro
    if robo.eh_do_robo(canal):
        # Loja do robô do Mac mini (sem integração): o nome vem da config do
        # robô, e a saúde é calculada AGORA — sem pulso há minutos é `parado`
        # (o robô morto não avisa que morreu).
        status_canal, ultimo_erro = robo.status_efetivo(canal)
        conta = conta or robo.nome_da_loja(canal)
        integracao = integracao or f"robô do Mac mini · perfil {canal.robo_perfil_id}"
    elif canais_externos.eh_externo(canal):
        # Site ou rede social (0362): o nome vem do leitor (`cursor`), e "por
        # onde lê" é a origem externa.
        conta = conta or canais_externos.nome_do_canal(canal)
        integracao = integracao or canais_externos.descricao_da_origem(canal)
    return CanalOut(
        id=canal.id,
        integration_id=canal.integration_id,
        robo_perfil_id=canal.robo_perfil_id,
        externo_ref=canal.externo_ref,
        rede_social_id=canal.rede_social_id,
        plataforma=canal.plataforma,
        canal=canal.canal,
        conta=conta,
        integracao=integracao,
        modo=canal.modo,
        status=status_canal,
        nao_lidas_plataforma=canal.nao_lidas_plataforma,
        ultimo_ok_em=_utc(canal.ultimo_ok_em),
        ultimo_erro_em=_utc(canal.ultimo_erro_em),
        ultimo_erro=ultimo_erro,
        auto_categorias=list(canal.auto_categorias or []),
        sla_horas=sla_horas(canal.plataforma, canal.canal),
        limite_caracteres=limite_caracteres(canal.plataforma, canal.canal),
    )


async def _canais(session: AsyncSession, scope: TeamScope) -> list[CanalOut]:
    """Os canais do escopo, com o nome da LOJA (e o da integração ao lado).

    OUTER join: o canal das lojas do robô (Temu/AliExpress) e o EXTERNO
    (sites e redes) não têm integração — e, como a conversa sem integração,
    só aparecem para quem vê tudo.
    """
    consulta = select(AtendimentoCanal, Integration.name).outerjoin(
        Integration, Integration.id == AtendimentoCanal.integration_id
    )
    cond = _clausula_escopo(scope, AtendimentoCanal.integration_id)
    if cond is not None:
        consulta = consulta.where(cond)
    canais = [
        _canal_out(c, nome, integracao=nome) for c, nome in (await session.execute(consulta)).all()
    ]
    nomes = await _nomes_das_lojas(session, {c.integration_id for c in canais})
    for c in canais:
        c.conta = nomes.get(c.integration_id) or c.conta
    return sorted(
        canais,
        key=lambda c: (
            c.plataforma,
            (c.conta or "").lower(),
            (c.integracao or "").lower(),
            str(c.integration_id),
            c.canal,
        ),
    )


def _validar_par(plataforma: str | None, canal: str | None) -> None:
    """Canal tem que existir na plataforma (o ML não tem `chat`, a Shopee não tem `pergunta`)."""
    if plataforma and canal and canal not in CANAIS_POR_PLATAFORMA.get(plataforma, ()):
        raise HTTPException(422, detail={"code": "canal_invalido"})


def _escapar_like(termo: str) -> str:
    return termo.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")


# ── Resumo ────────────────────────────────────────────────────────────────


def _gravidade(status_canal: str) -> int:
    # Estado desconhecido (vocabulário novo que o router ainda não conhece)
    # conta como erro: melhor a loja aparecer com alerta do que "tudo certo".
    if status_canal in _GRAVIDADE_STATUS:
        return _GRAVIDADE_STATUS.index(status_canal)
    return _GRAVIDADE_STATUS.index("erro")


def _motivo_do_status(canal: CanalOut) -> str | None:
    """Por que o canal não está sendo lido — para o `title` da barra de lojas."""
    erro = (canal.ultimo_erro or "").strip()[:200]
    if canal.status == "ok":
        return None
    if canal.status == "sem_escopo" and canal.plataforma in PLATAFORMAS_REDE:
        # Rede social (02/10/2026): o token do usuário de sistema do "DaVinci
        # Publicador" sem o escopo de comentários (o `ultimo_erro` diz qual).
        texto = (
            "Sem permissão no token do DaVinci Publicador (falta o escopo de comentários: "
            "gere o token novo)"
        )
    elif canal.status == "sem_escopo" and canal.plataforma == PLATAFORMA_SITE:
        # 401: o token não confere OU a hospedagem cortou o cabeçalho (o
        # `ultimo_erro` diz qual — carrinhos._falha_http).
        texto = "O site recusou o token do DaVinci"
    elif canal.status == "sem_escopo":
        texto = (
            "Sem permissão de atendimento na plataforma (reautorize a loja com o escopo "
            "de mensagens)"
        )
    elif canal.status == STATUS_CANAL_SEM_ENDPOINT:
        # Site (02/10/2026): o 404 da rota de leitura do carrinho.
        texto = (
            "O site ainda não tem a rota de carrinhos: publicar o pacote do carrinho "
            "abandonado na Hostinger"
        )
    elif canal.status == "desligado" and canal.plataforma == PLATAFORMA_SITE:
        texto = "O DaVinci não tem o token deste site (SITES_ESTOQUE_TOKENS): o site não é lido"
    elif canal.status == "desligado":
        texto = "Leitura desligada para esta loja"
    elif canal.status == STATUS_CANAL_PARADO:
        texto = "Leitura parada"
    elif canal.status == STATUS_CANAL_SESSAO_CAIU:
        texto = "O Seller Center saiu da conta no AdsPower"
    elif canal.status == "novo":
        texto = "Ainda não foi lida"
    else:
        texto = "A última leitura falhou"
    return f"{texto}: {erro}" if erro and canal.status != "novo" else texto


def _saude_da_loja(canais: list[CanalOut]) -> tuple[str, str | None]:
    """O pior estado entre os canais da loja e o porquê (com o canal, se há mais de um)."""
    pior = min(canais, key=lambda c: _gravidade(c.status))
    motivo = _motivo_do_status(pior)
    if motivo and len(canais) > 1:
        motivo = f"{_ROTULO_CANAL.get(pior.canal, pior.canal)}: {motivo}"
    return pior.status, motivo


def _juntar_externas(
    lojas: list[LojaResumoOut],
    canais_da_loja: dict[tuple[UUID | None, str, UUID | None], list[CanalOut]],
) -> list[LojaResumoOut]:
    """Uma linha por ORIGEM externa: as caixas do mesmo site viram uma loja só.

    Hoje cada site tem uma caixa (o carrinho) e cada conta de rede uma (os
    comentários); quando o site ganhar outra (o SAC), a barra continua com
    UMA linha "Charlots", com o pior estado entre as caixas — e o filtro é
    pela origem (`externo_ref`), não pelo canal.
    """
    saida: list[LojaResumoOut] = []
    por_ref: dict[str, LojaResumoOut] = {}
    canais_da_ref: dict[str, list[CanalOut]] = {}
    for chave, cs in canais_da_loja.items():
        for c in cs:
            if c.externo_ref and chave[0] is None:
                canais_da_ref.setdefault(c.externo_ref, []).append(c)
    for lj in lojas:
        ref = lj.externo_ref
        if not ref or lj.integration_id is not None:
            saida.append(lj)
            continue
        alvo = por_ref.get(ref)
        if alvo is None:
            por_ref[ref] = lj
            saida.append(lj)
            continue
        alvo.aguardando += lj.aguardando
        alvo.vencidas += lj.vencidas
        alvo.nao_lidas += lj.nao_lidas
        alvo.email_sem_vinculo += lj.email_sem_vinculo
        alvo.etiquetas = {
            e: alvo.etiquetas.get(e, 0) + lj.etiquetas.get(e, 0)
            for e in {*alvo.etiquetas, *lj.etiquetas}
        }
        # A linha é da ORIGEM (todas as caixas): o filtro vai pelo `externo_ref`.
        alvo.canal_id = None
    for ref, alvo in por_ref.items():
        cs = canais_da_ref.get(ref) or []
        if len(cs) > 1:
            alvo.status_canal, alvo.status_motivo = _saude_da_loja(cs)
    return saida


def _com_direct(lojas: list[LojaResumoOut], contas: list[dict]) -> list[LojaResumoOut]:
    """O Direct de cada conta do Instagram na linha DA CONTA (02/10/2026).

    Antes era uma linha "Direct" juntando 7buyers, Charlots e Uranyx. Agora a
    conta que tem a caixa de comentários soma o Direct na mesma linha
    (`rede_social_id`); a que só tem Direct (7buyers) ganha a linha dela,
    sem canal (o adaptador só lê `dm_conversas`). O nome é o @ do cadastro
    (`instagram.contar_por_conta`); as DMs de conta que saiu do cadastro
    ficam numa linha "Direct (conta fora do cadastro)", sem `rede_social_id`
    (não há por onde filtrar a conta: a barra mostra a linha, e clicar nela
    filtra o Instagram inteiro — a soma das linhas bate com o número da
    plataforma). `direct_aguardando` separa, no
    title, o Direct dos comentários — e diz à tela que a linha continua
    lendo o Direct mesmo com a caixa de comentários sem permissão.
    """
    saida = list(lojas)
    for d in contas:
        if not d.get("total"):
            continue
        rs = d.get("rede_social_id")
        alvo = next(
            (
                lj
                for lj in saida
                if rs is not None
                and lj.plataforma == instagram.PLATAFORMA
                and lj.rede_social_id == rs
            ),
            None,
        )
        if alvo is None:
            alvo = LojaResumoOut(
                plataforma=instagram.PLATAFORMA,
                rede_social_id=rs,
                conta=d.get("conta") or "Direct",
                integracao="Direct do Instagram (só leitura)",
                aguardando=0,
                vencidas=0,
                etiquetas=dict.fromkeys(ETIQUETAS, 0),
            )
            saida.append(alvo)
        alvo.aguardando += int(d.get("aguardando") or 0)
        alvo.vencidas += int(d.get("vencidas") or 0)
        alvo.direct_aguardando += int(d.get("aguardando") or 0)
        alvo.direct_total += int(d.get("total") or 0)
        alvo.etiquetas = {
            **alvo.etiquetas,
            ETIQUETA_MIDIA: alvo.etiquetas.get(ETIQUETA_MIDIA, 0) + int(d.get("abertas") or 0),
        }
    return saida


@router.get("/resumo", response_model=ResumoOut)
async def resumo(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_view)],
) -> ResumoOut:
    """Contadores da fila (por plataforma e por loja), saúde dos canais e as chaves."""
    from app.services.atendimento import humano as humano_svc

    scope = await resolve_team_scope(session, user)
    agora = datetime.now(UTC)
    aguardando = AtendimentoConversa.aguardando_resposta.is_(True)
    prazo = AtendimentoConversa.prazo_resposta_em
    vencendo = and_(aguardando, prazo >= agora, prazo < agora + VENCENDO)
    vencidas = and_(aguardando, prazo < agora)
    cond = _clausula_escopo(scope, AtendimentoConversa.integration_id)

    nao_lidas = func.coalesce(func.sum(AtendimentoConversa.nao_lidas), 0)
    # Conversas NÃO fechadas por etiqueta (o menu Filtrar), na mesma ordem de
    # `ETIQUETAS`; a ainda não calculada conta pela regra de pré/pós-venda.
    aberta = AtendimentoConversa.situacao != CONVERSA_FECHADA
    efetiva = _etiqueta_efetiva()
    por_etiqueta = [func.count().filter(aberta, efetiva == e) for e in ETIQUETAS]
    sem_vinculo = func.count().filter(aberta, _email_sem_vinculo())
    # A Caixa Humano (09/10/2026): falta responder e a IA não pode.
    na_caixa_humano = func.count().filter(humano_svc.caixa_humano_sql())

    def _etiquetas(contagens) -> dict[str, int]:
        return {e: int(n or 0) for e, n in zip(ETIQUETAS, contagens, strict=True)}

    q_plat = select(
        AtendimentoConversa.plataforma,
        func.count().filter(aguardando),
        func.count().filter(vencendo),
        func.count().filter(vencidas),
        func.count().filter(_a_conferir_existe()),
        nao_lidas,
        sem_vinculo,
        na_caixa_humano,
        *por_etiqueta,
    ).group_by(AtendimentoConversa.plataforma)
    if cond is not None:
        q_plat = q_plat.where(cond)
    por_plataforma = {
        p: PlataformaResumoOut(
            plataforma=p,
            aguardando=a or 0,
            vencendo=v or 0,
            vencidas=x or 0,
            a_conferir=r or 0,
            nao_lidas=int(n or 0),
            etiquetas=_etiquetas(etq),
            email_sem_vinculo=int(sv or 0),
            humano=int(h or 0),
        )
        for p, a, v, x, r, n, sv, h, *etq in (await session.execute(q_plat)).all()
    }
    if PLATAFORMA_SITE in por_plataforma:
        # Os chips SAC / Atacado / Dúvidas e sugestões do grupo Site (RF6).
        por_plataforma[PLATAFORMA_SITE].chamados = await _chamados_por_tipo(session, cond)
    canais = await _canais(session, scope)
    # As do robô (Temu/AliExpress) e as externas (sites, redes — 02/10/2026)
    # só quando existem: loja com canal ou conversa. O Direct do Instagram
    # (adaptador só leitura) só para quem vê todas as equipes.
    existem = {c.plataforma for c in canais} | set(por_plataforma)
    # Uma consulta só para o Direct: por conta (a barra) e o total (a
    # plataforma) é a soma das contas.
    contas_ig = await instagram.contar_por_conta(session) if scope.unrestricted else []
    ig = instagram.somar(contas_ig) if scope.unrestricted else None
    if ig and ig["total"]:
        existem.add(instagram.PLATAFORMA)
    plataformas = [
        por_plataforma.get(p)
        or PlataformaResumoOut(plataforma=p, aguardando=0, vencendo=0, vencidas=0)
        for p in (
            *PLATAFORMAS,
            *(p for p in (*PLATAFORMAS_ROBO, *PLATAFORMAS_EXTERNAS) if p in existem),
        )
    ]
    if ig and ig["total"]:
        # O Direct soma com os comentários do Instagram (a caixa) numa linha
        # só da plataforma; as DMs não fechadas contam na etiqueta MÍDIA.
        p_ig = next(p for p in plataformas if p.plataforma == instagram.PLATAFORMA)
        p_ig.aguardando += ig["aguardando"]
        p_ig.vencendo += ig["vencendo"]
        p_ig.vencidas += ig["vencidas"]
        p_ig.etiquetas = {
            **p_ig.etiquetas,
            ETIQUETA_MIDIA: p_ig.etiquetas.get(ETIQUETA_MIDIA, 0) + ig["abertas"],
        }

    # A loja SEM integração é o CANAL: a do robô (Temu/AliExpress) e a
    # externa (site, conta de rede). Sem esta chave, as quatro lojas Temu
    # virariam uma só linha na barra. A conversa sem integração e sem canal
    # (Amazon sem conta) fica com a chave nula, como sempre.
    # Rótulo: o GROUP BY vai pelo nome (a expressão repetida teria outros
    # parâmetros e o Postgres não a reconheceria como a mesma).
    canal_sem_integracao = case(
        (
            and_(
                AtendimentoConversa.integration_id.is_(None),
                AtendimentoConversa.canal_id.is_not(None),
            ),
            AtendimentoConversa.canal_id,
        ),
        else_=None,
    ).label("canal_sem_integracao")
    q_loja = select(
        AtendimentoConversa.integration_id,
        AtendimentoConversa.plataforma,
        canal_sem_integracao,
        func.max(AtendimentoConversa.conta),
        func.count().filter(aguardando),
        func.count().filter(vencidas),
        nao_lidas,
        sem_vinculo,
        na_caixa_humano,
        *por_etiqueta,
    ).group_by(
        AtendimentoConversa.integration_id, AtendimentoConversa.plataforma, canal_sem_integracao
    )
    if cond is not None:
        q_loja = q_loja.where(cond)
    lojas: dict[tuple[UUID | None, str, UUID | None], LojaResumoOut] = {
        (i, p, rc): LojaResumoOut(
            integration_id=i,
            canal_id=rc,
            plataforma=p,
            conta=conta,
            nao_lidas=int(n or 0),
            aguardando=a or 0,
            vencidas=x or 0,
            etiquetas=_etiquetas(etq),
            email_sem_vinculo=int(sv or 0),
            humano=int(h or 0),
        )
        for i, p, rc, conta, a, x, n, sv, h, *etq in (await session.execute(q_loja)).all()
    }
    canais_da_loja: dict[tuple[UUID | None, str, UUID | None], list[CanalOut]] = {}
    for c in canais:
        chave_canal = c.id if c.integration_id is None else None
        canais_da_loja.setdefault((c.integration_id, c.plataforma, chave_canal), []).append(c)
    for chave, da_loja in canais_da_loja.items():
        # Loja conectada sem conversa ainda também aparece na barra — com zero.
        loja = lojas.get(chave)
        if loja is None:
            loja = lojas[chave] = LojaResumoOut(
                integration_id=chave[0],
                canal_id=chave[2],
                plataforma=chave[1],
                conta=da_loja[0].conta,
                aguardando=0,
                vencidas=0,
            )
        else:
            loja.conta = da_loja[0].conta or loja.conta
        loja.integracao = da_loja[0].integracao
        loja.externo_ref = da_loja[0].externo_ref
        loja.rede_social_id = da_loja[0].rede_social_id
        loja.status_canal, loja.status_motivo = _saude_da_loja(da_loja)
    # O nome da LOJA (P2): o dos canais já veio de `lojas.nome_da_loja`; a
    # loja que só tem conversa (canal apagado) pede o dela aqui — a
    # `conversa.conta` gravada é o retrato de quando o sync passou.
    sem_canal = {
        lj.integration_id
        for chave, lj in lojas.items()
        if chave not in canais_da_loja and lj.integration_id is not None
    }
    nomes = await _nomes_das_lojas(session, sem_canal)
    for lj in lojas.values():
        if lj.integration_id in sem_canal:
            lj.conta = nomes.get(lj.integration_id) or lj.conta
    barra = _juntar_externas(list(lojas.values()), canais_da_loja)
    if scope.unrestricted:
        barra = _com_direct(barra, contas_ig)
    leitura_parada = await _leitura_parada(session, scope, canais)
    s = get_settings()
    return ResumoOut(
        plataformas=plataformas,
        a_conferir=sum(p.a_conferir for p in plataformas),
        etiquetas={e: sum(p.etiquetas.get(e, 0) for p in plataformas) for e in ETIQUETAS},
        email_sem_vinculo=sum(p.email_sem_vinculo for p in plataformas),
        humano=sum(p.humano for p in plataformas),
        lojas=sorted(
            barra,
            key=lambda lj: (
                lj.plataforma,
                (lj.conta or "").lower(),
                (lj.integracao or "").lower(),
                str(lj.integration_id),
            ),
        ),
        canais=canais,
        flags=FlagsOut(
            leitura_ativa=s.atendimento_leitura_ativa,
            envio_ativo=s.atendimento_envio_ativo,
            ia_ativa=s.atendimento_ia_ativa,
            auto_ativo=s.atendimento_auto_ativo,
            simulador=s.atendimento_simulador,
            simulador_exceto=enviar.fora_do_simulador(),
            alerta_telegram=s.atendimento_alerta_telegram,
            humano_ativa=s.atendimento_leitura_ativa and s.atendimento_humano_ativa,
        ),
        leitura_parada=leitura_parada,
    )


async def _leitura_parada(
    session: AsyncSession, scope: TeamScope, canais: list[CanalOut]
) -> list[LeituraParadaOut]:
    """A faixa "lojas sem ler" da Caixa (05/10/2026): o retrato de agora de
    `vigia_leitura_atendimento.leitura_parada` (só leitura: banco + 2 HGETALL).

    Decisão do Eduardo: o aviso de leitura parada fica aqui, no próprio
    /atendimento, e não na Ouvidoria — só quem vê esta tela vê. Com o nome de
    cada loja que o resumo já tem (`canais`). Quem não vê todas as equipes só
    vê as lojas dele (a do robô, o site, as redes e as rodadas são de todas,
    como o canal sem integração). A faixa nunca derruba o resumo: falhou, ela
    some e o log diz por quê.
    """
    nomes = {c.integration_id: c.conta for c in canais if c.integration_id and c.conta}
    try:
        linhas = await vigia_leitura_atendimento.leitura_parada(session, nomes=nomes)
    except Exception:  # noqa: BLE001 — a faixa é acessória; a Caixa não pode cair
        logger.exception("atendimento_leitura_parada_falhou")
        try:
            await session.rollback()
        except Exception:  # noqa: BLE001, S110 — só leitura; nada a desfazer
            pass
        return []
    if not scope.unrestricted:
        permitidas = set(scope.integration_ids or ())
        linhas = [lp for lp in linhas if lp.integration_id in permitidas]
    return [LeituraParadaOut(**asdict(lp)) for lp in linhas]


# ── Lista e detalhe ───────────────────────────────────────────────────────


async def _listar_marketplace(
    session: AsyncSession,
    *,
    scope: TeamScope,
    user: User,
    plataforma: tuple[str, ...],
    integration_id: UUID | None,
    canal: str | None,
    filtro: str,
    q: str | None,
    antes_de: str | None,
    limite: int,
    canal_id: UUID | None = None,
    etiqueta: str | None = None,
    externo_ref: str | None = None,
    rede_social_id: UUID | None = None,
    perguntas_primeiro: bool = False,
    tipo_chamado: str | None = None,
    caixa_humano: bool = False,
) -> list[dict[str, Any]]:
    from app.services.atendimento import humano as humano_svc

    agora = datetime.now(UTC)
    tem = _pendente_existe()
    a_conferir = _a_conferir_existe()
    consulta = select(
        AtendimentoConversa,
        tem.label("tem_rascunho"),
        a_conferir.label("a_conferir"),
        _ultimo_tipo().label("ultimo_tipo"),
        _pendentes().label("pendentes"),
        # Caixa Humano (09/10/2026): o motivo de quem está nela (None fora).
        humano_svc.triagem_da_linha_sql().label("humano"),
        User,
    ).outerjoin(User, User.id == AtendimentoConversa.atribuido_a)
    cond = _clausula_escopo(scope, AtendimentoConversa.integration_id)
    if cond is not None:
        consulta = consulta.where(cond)
    if caixa_humano:
        # Só o que falta responder e a IA não pode (junto de qualquer filtro).
        consulta = consulta.where(humano_svc.caixa_humano_sql())
    if len(plataforma) == 1:
        consulta = consulta.where(AtendimentoConversa.plataforma == plataforma[0])
    elif plataforma:
        # Um GRUPO da caixa (08/10/2026): "Redes" = Instagram + Facebook.
        consulta = consulta.where(AtendimentoConversa.plataforma.in_(plataforma))
    if integration_id:
        consulta = consulta.where(AtendimentoConversa.integration_id == integration_id)
    if canal_id:
        # Uma loja do robô (Temu/AliExpress): sem integração, ela é o canal.
        consulta = consulta.where(AtendimentoConversa.canal_id == canal_id)
    if externo_ref:
        # Um site (todas as caixas dele): a origem externa do canal.
        consulta = consulta.where(
            AtendimentoConversa.canal_id.in_(
                select(AtendimentoCanal.id).where(AtendimentoCanal.externo_ref == externo_ref)
            )
        )
    if rede_social_id:
        # Uma conta de rede social: os comentários dela (o Direct vem do
        # adaptador, com o mesmo filtro).
        consulta = consulta.where(
            AtendimentoConversa.canal_id.in_(
                select(AtendimentoCanal.id).where(
                    AtendimentoCanal.rede_social_id == rede_social_id
                )
            )
        )
    if canal:
        consulta = consulta.where(AtendimentoConversa.canal == canal)
    aguardando = AtendimentoConversa.aguardando_resposta.is_(True)
    prazo = AtendimentoConversa.prazo_resposta_em
    if filtro == "todas":
        # Fechada = resolvida: sai da frente (volta sozinha se o cliente escrever).
        consulta = consulta.where(AtendimentoConversa.situacao != CONVERSA_FECHADA)
    elif filtro == "aguardando":
        consulta = consulta.where(aguardando)
    elif filtro == "vencendo":
        consulta = consulta.where(aguardando, prazo >= agora, prazo < agora + VENCENDO)
    elif filtro == "vencidas":
        consulta = consulta.where(aguardando, prazo < agora)
    elif filtro == "com_rascunho":
        consulta = consulta.where(tem)
    elif filtro == "a_conferir":
        consulta = consulta.where(a_conferir)
    elif filtro == "minhas":
        consulta = consulta.where(AtendimentoConversa.atribuido_a == user.id)
    elif filtro == "fechadas":
        consulta = consulta.where(AtendimentoConversa.situacao == CONVERSA_FECHADA)
    elif filtro == "automatica":
        # Esperando, mas a ÚLTIMA mensagem é da loja: só a mensagem automática
        # (robô ou campanha do Duoke, cartão da Shopee, senha da devolução —
        # `constantes.e_mensagem_automatica`) falou depois do comprador.
        consulta = consulta.where(aguardando, AtendimentoConversa.ultima_autor == AUTOR_LOJA)
    elif filtro == FILTRO_EMAIL_SEM_VINCULO:
        # O e-mail das lojas sem pedido (RF5): as abertas, como em "todas".
        consulta = consulta.where(
            AtendimentoConversa.situacao != CONVERSA_FECHADA, _email_sem_vinculo()
        )
    elif filtro in ETIQUETAS:
        # Pela ETIQUETA (status atual): Pré-venda, Pós-venda, Reclamação,
        # Devolução, Ag. cancelamento — as abertas (a fechada sai, como em
        # "todas"). A ainda não calculada vale pela regra de pré/pós-venda
        # (`_etiqueta_efetiva`).
        consulta = consulta.where(
            AtendimentoConversa.situacao != CONVERSA_FECHADA, _etiqueta_efetiva() == filtro
        )
    if etiqueta:
        # `?etiqueta=` junto de qualquer filtro (ex.: Falta responder + Reclamação).
        consulta = consulta.where(_etiqueta_efetiva() == etiqueta)
    if tipo_chamado:
        # Os chips do grupo Site (RF6): o chamado do tipo, junto de qualquer
        # filtro (Todas, Falta responder…). Só o chamado da ponte tem o tipo.
        consulta = consulta.where(
            AtendimentoConversa.plataforma == PLATAFORMA_SITE,
            AtendimentoConversa.canal == CANAL_EMAIL,
            _tipo_do_chamado() == tipo_chamado,
        )
    if q and q.strip():
        termo_exato = q.strip()
        termo = f"%{_escapar_like(termo_exato)}%"
        no_bling = select(_busca_no_bling(termo_exato).c.numeroloja)
        consulta = consulta.where(
            or_(
                *(
                    col.ilike(termo, escape="\\")
                    for col in (
                        AtendimentoConversa.comprador_nome,
                        AtendimentoConversa.pedido_marketplace,
                        AtendimentoConversa.anuncio_titulo,
                        AtendimentoConversa.conta,
                        AtendimentoConversa.externo_id,
                        AtendimentoConversa.ultima_mensagem_resumo,
                    )
                ),
                # O nº do Bling e o SKU (01/10/2026): o pedido achado no
                # espelho do Bling, pelo nº na plataforma (ou o pack/order do ML).
                AtendimentoConversa.pedido_marketplace.in_(no_bling),
                AtendimentoConversa.dados["pack_id"].astext.in_(no_bling),
                AtendimentoConversa.dados["order_id"].astext.in_(no_bling),
                # Os chamados dos sites (RF6): TODOS os protocolos do chamado —
                # o principal e os agrupados nele (o 2º sumia de "Todas" assim
                # que chegava mensagem nova: só o resumo da última o achava).
                AtendimentoConversa.dados["mail"]["protocolo"].astext.ilike(
                    termo, escape="\\"
                ),
                AtendimentoConversa.dados["mail"]["protocolos"].astext.ilike(
                    termo, escape="\\"
                ),
            )
        )
    if filtro in FILTROS_PELO_PRAZO:
        # O comentário comum das redes no fim (RF7: a pergunta passa à
        # frente); dentro de cada grupo, prazo mais curto primeiro, sem prazo
        # no fim; o id (como texto, a mesma ordem das DMs do Instagram)
        # desempata e é o cursor.
        momento, ident, sem_prazo, grupo = _cursor(antes_de, pelo_prazo=True)
        id_txt = cast(AtendimentoConversa.id, String)
        comum = _comentario_comum()
        if ident is not None:
            if sem_prazo:
                depois = and_(prazo.is_(None), id_txt > ident)
            else:
                depois = or_(
                    prazo > momento,
                    and_(prazo == momento, id_txt > ident),
                    prazo.is_(None),
                )
            if grupo == 0:
                consulta = consulta.where(or_(and_(not_(comum), depois), comum))
            else:
                consulta = consulta.where(comum, depois)
        consulta = consulta.order_by(
            case((comum, 1), else_=0), prazo.asc().nulls_last(), id_txt.asc()
        )
    else:
        momento, _ident, _sem, grupo = _cursor(antes_de, pelo_prazo=False)
        ultima = AtendimentoConversa.ultima_mensagem_em
        if perguntas_primeiro:
            # Mídia (RF7): a pergunta pendente primeiro; depois, pela recência.
            pergunta = _pergunta_pendente()
            if momento is not None and grupo == 0:
                consulta = consulta.where(or_(and_(pergunta, ultima < momento), not_(pergunta)))
            elif momento is not None:
                consulta = consulta.where(not_(pergunta), ultima < momento)
            consulta = consulta.order_by(
                case((pergunta, 0), else_=1),
                ultima.desc().nulls_last(),
                AtendimentoConversa.id.desc(),
            )
        else:
            if momento is not None:
                consulta = consulta.where(ultima < momento)
            consulta = consulta.order_by(
                ultima.desc().nulls_last(),
                AtendimentoConversa.id.desc(),
            )
    return [
        _resumo_dict(
            c,
            tem_rascunho=bool(tem_r),
            atribuido_nome=_nome(u),
            a_conferir=bool(r),
            ultimo_tipo=tipo,
            pendentes=pend,
            humano=humano_svc.para_tela(hum, ia_pausada=c.ia_pausada),
        )
        for c, tem_r, r, tipo, pend, hum, u in (
            await session.execute(consulta.limit(limite))
        ).all()
    ]


@router.get("/conversas", response_model=ListaConversasOut)
async def listar_conversas(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_view)],
    plataforma: Annotated[str | None, Query(max_length=200)] = None,
    integration_id: Annotated[UUID | None, Query()] = None,
    canal: Annotated[str | None, Query()] = None,
    filtro: Annotated[str, Query()] = "todas",
    q: Annotated[str | None, Query(max_length=200)] = None,
    antes_de: Annotated[str | None, Query(max_length=120)] = None,
    limite: Annotated[int, Query(ge=1, le=200)] = 50,
    canal_id: Annotated[UUID | None, Query()] = None,
    etiqueta: Annotated[str | None, Query(max_length=24)] = None,
    externo_ref: Annotated[str | None, Query(max_length=191)] = None,
    rede_social_id: Annotated[UUID | None, Query()] = None,
    tipo_chamado: Annotated[str | None, Query(max_length=16)] = None,
    caixa: Annotated[str | None, Query(max_length=16)] = None,
) -> ListaConversasOut:
    """A fila, mais recente primeiro; página seguinte com `antes_de=<proximo>`.

    `canal_id` filtra uma loja do robô do Mac mini (Temu/AliExpress), que não
    tem integração (o `canal_id` vem da barra de lojas do /resumo).
    `externo_ref` filtra um site ("site:charlots" — todas as caixas dele) e
    `rede_social_id` uma conta de rede social: os comentários E o Direct da
    conta (02/10/2026; os dois vêm da barra de lojas do /resumo).
    `plataforma` aceita uma plataforma ou várias separadas por vírgula — o
    filtro por plataforma do topo da lista (08/10/2026): "Redes" é
    `instagram,facebook`. Sempre dentro do escopo da equipe de quem pede.
    `etiqueta` filtra pela etiqueta (status atual), junto de qualquer filtro;
    os filtros `pre_venda`/`pos_venda`/`reclamacao`/`devolucao`/
    `ag_cancelamento`/`avaliacao` são a mesma coisa vinda do menu Filtrar.
    Na etiqueta Avaliação, `avaliacao_estrelas` traz a pior nota pendente. A aba
    "Falta responder" (`aguardando`) vem pelo PRAZO mais curto, sem prazo no
    fim — e o cursor (`proximo`) é o do prazo.
    `tipo_chamado` (sac | atacado | duvidas) filtra os chamados dos sites pelo
    tipo (os chips do grupo Site, RF6); `filtro=email_sem_vinculo` traz as
    conversas de e-mail das lojas ainda sem pedido (RF5).
    `caixa=humano` (09/10/2026) = a Caixa Humano: só o que falta responder e a
    IA não pode (`services/atendimento/humano.py`), junto de qualquer outro
    filtro; o Direct do Instagram não entra.
    """
    plataformas = _plataformas_do_filtro(plataforma)
    canal = (canal or "").strip().lower() or None
    filtro = (filtro or "todas").strip().lower()
    etiqueta = (etiqueta or "").strip().lower() or None
    externo_ref = (externo_ref or "").strip() or None
    if any(p not in PLATAFORMAS_LISTA for p in plataformas):
        raise HTTPException(422, detail={"code": "plataforma_invalida"})
    if externo_ref and canais_externos.partes(externo_ref) is None:
        raise HTTPException(422, detail={"code": "externo_ref_invalido"})
    if filtro not in FILTROS:
        raise HTTPException(422, detail={"code": "filtro_invalido"})
    if etiqueta and etiqueta not in ETIQUETAS:
        raise HTTPException(422, detail={"code": "etiqueta_invalida"})
    tipo_chamado = (tipo_chamado or "").strip().lower() or None
    if tipo_chamado and tipo_chamado not in TIPOS_CHAMADO:
        raise HTTPException(422, detail={"code": "tipo_chamado_invalido"})
    caixa = (caixa or "").strip().lower()
    if caixa not in CAIXAS:
        raise HTTPException(422, detail={"code": "caixa_invalida"})
    caixa_humano = caixa == CAIXA_HUMANO
    pelo_prazo = filtro in FILTROS_PELO_PRAZO
    perguntas_primeiro = filtro in FILTROS_PERGUNTA_PRIMEIRO
    # Cursor inválido (ou da outra ordem) → 422 antes de ir ao banco.
    momento, ident, sem_prazo, grupo = _cursor(antes_de, pelo_prazo=pelo_prazo)
    scope = await resolve_team_scope(session, user)

    itens: list[dict[str, Any]] = []
    # A caixa (marketplaces, sites e os COMENTÁRIOS das redes) — menos quando
    # o pedido é só o Direct do Instagram (`canal=dm`).
    if not (plataformas == (instagram.PLATAFORMA,) and canal == instagram.CANAL):
        itens += await _listar_marketplace(
            session,
            scope=scope,
            user=user,
            plataforma=plataformas,
            integration_id=integration_id,
            canal=canal,
            filtro=filtro,
            q=q,
            antes_de=antes_de,
            limite=limite + 1,
            canal_id=canal_id,
            etiqueta=etiqueta,
            externo_ref=externo_ref,
            rede_social_id=rede_social_id,
            perguntas_primeiro=perguntas_primeiro,
            tipo_chamado=tipo_chamado,
            caixa_humano=caixa_humano,
        )
    quer_instagram = (
        (not plataformas or instagram.PLATAFORMA in plataformas)
        and integration_id is None
        and canal_id is None
        and externo_ref is None
        and canal in (None, instagram.CANAL)
        and scope.unrestricted
        # O DM do Instagram não tem etiqueta gravada nem pedido: só entra na
        # MÍDIA (RF7: a mensagem privada das redes é Mídia).
        and etiqueta in (None, ETIQUETA_MIDIA)
        and (filtro not in ETIQUETAS or filtro == ETIQUETA_MIDIA)
        # O e-mail das lojas e o chamado do site nunca são o Direct.
        and filtro != FILTRO_EMAIL_SEM_VINCULO
        and tipo_chamado is None
        # A Caixa Humano é a régua da IA da caixa: o Direct (adaptador só
        # leitura, sem IA) não entra nela.
        and not caixa_humano
    )
    # No filtro Mídia, o DM vale como "todas" (as não silenciadas).
    filtro_dm = "todas" if filtro == ETIQUETA_MIDIA else filtro
    if quer_instagram and pelo_prazo:
        # O Instagram pagina por recência: vêm as DMs esperando (até o teto)
        # e o corte do cursor de prazo é feito aqui.
        dms = await instagram.listar_conversas(
            session,
            limite=MAX_INSTAGRAM_PELO_PRAZO,
            q=q,
            filtro=filtro_dm,
            user_id=user.id,
            rede_social_id=rede_social_id,
        )
        itens += [i for i in dms if _depois_do_cursor(i, momento, ident, sem_prazo, grupo)]
    elif quer_instagram:
        itens += await instagram.listar_conversas(
            session,
            # No Mídia o Direct é do grupo de baixo (nunca é pergunta): com a
            # página ainda nas perguntas, ele vem do começo.
            antes_de=None if perguntas_primeiro and grupo == 0 else momento,
            limite=limite + 1,
            q=q,
            filtro=filtro_dm,
            user_id=user.id,
            rede_social_id=rede_social_id,
        )
    if pelo_prazo:
        itens.sort(key=_chave_do_prazo)
        pagina = itens[:limite]
        proximo = _cursor_do_prazo(pagina[-1]) if len(itens) > limite and pagina else None
    else:
        piso = datetime.min.replace(tzinfo=UTC)
        itens.sort(key=lambda i: i["ultima_mensagem_em"] or piso, reverse=True)
        chave = None
        if perguntas_primeiro:
            # A pergunta pendente na frente (sort estável: a recência fica).
            itens.sort(key=_grupo_da_pergunta)
            chave = _chave_da_pergunta
        pagina, ultima = _paginar(itens, limite, chave=chave)
        proximo = None
        if ultima is not None and ultima["ultima_mensagem_em"] is not None:
            proximo = ultima["ultima_mensagem_em"].isoformat()
            if perguntas_primeiro and _grupo_da_pergunta(ultima) == 0:
                proximo = f"{_CURSOR_PERGUNTA}{proximo}"
    # Só as da página: no máximo uma consulta de nome por loja que aparece.
    await _com_nome_da_loja(session, pagina)
    await _com_estrelas_da_avaliacao(session, pagina)
    return ListaConversasOut(itens=pagina, proximo=proximo)


def _plataformas_do_filtro(texto: str | None) -> tuple[str, ...]:
    """`?plataforma=` → as plataformas, sem repetir e na ordem: "ml",
    "instagram,facebook" (um grupo da caixa). Vazio → nenhuma (todas)."""
    saida: list[str] = []
    for parte in (texto or "").split(","):
        p = parte.strip().lower()
        if p and p not in saida:
            saida.append(p)
    return tuple(saida)


def _grupo_da_pergunta(item: dict[str, Any]) -> int:
    """No Mídia: 0 = pergunta pendente (vem primeiro), 1 = o resto."""
    return 0 if item.get("eh_pergunta") else 1


def _chave_da_pergunta(item: dict[str, Any]) -> tuple:
    """O que é EMPATE no corte da página do Mídia: o mesmo grupo e o mesmo horário."""
    return (_grupo_da_pergunta(item), item["ultima_mensagem_em"])


def _paginar(
    itens: list[dict[str, Any]], limite: int, *, chave=None
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """Corta a página sem partir um EMPATE de horário ao meio → (página, último item).

    O relógio da Shopee é em segundos: duas conversas com a última mensagem
    no mesmo segundo são comuns. Se o corte caísse no meio delas, a próxima
    página (`< antes_de`) pularia a segunda — conversa sumindo da fila sem
    erro nenhum. Então as empatadas com a primeira que ficou de fora vão
    TODAS para a página seguinte (salvo se a página inteira for um empate só).
    `chave` diz o que é empate (padrão: o horário; no Mídia, grupo + horário).
    """
    chave = chave or (lambda i: i["ultima_mensagem_em"])
    pagina = itens[:limite]
    if len(itens) <= limite or not pagina:
        return pagina, None
    corte = chave(itens[limite])
    sem_horario = itens[limite]["ultima_mensagem_em"] is None
    aparada = list(pagina)
    while len(aparada) > 1 and not sem_horario and chave(aparada[-1]) == corte:
        aparada.pop()
    if chave(aparada[-1]) != corte:
        pagina = aparada
    return pagina, pagina[-1]


async def _contexto(session: AsyncSession, conversa: AtendimentoConversa) -> dict[str, Any]:
    """O pedido por trás da conversa. Falha aqui não pode esconder a conversa."""
    # Mesmas chaves de `contexto.vazio()` (inclusive `nota_fiscal`), escritas
    # aqui porque é justamente o import do cérebro que pode ter falhado.
    vazio: dict[str, Any] = {
        "pedido": None,
        "logistica": None,
        "chamados": [],
        "devolucoes": [],
        "nota_fiscal": None,
        "outras_perguntas": [],
        "reclamacoes": [],
        "avaliacoes": [],
    }
    try:
        from app.services.atendimento import contexto as contexto_svc

        return dict(await contexto_svc.contexto_da_conversa(session, conversa) or vazio)
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "atendimento_contexto_falhou", conversa_id=str(conversa.id), err=type(e).__name__
        )
        return vazio


async def _cliente(session: AsyncSession, conversa: AtendimentoConversa) -> dict[str, Any]:
    """O cartão "Cliente" (parte 2, P5). Falha aqui não pode esconder a conversa.

    `cliente.cartao_cliente` já promete não levantar; mesmo assim a chamada
    vai num SAVEPOINT e com o erro engolido: é o último pedaço do detalhe, e
    uma consulta que falhe lá dentro não pode abortar a transação nem virar
    500 — a tela mostra a conversa sem o cartão. Só o tipo do erro no log
    (o cartão tem histórico de compra e avaliação do comprador).
    """
    cliente_svc = _modulo(_CLIENTE)
    if cliente_svc is None:
        return {}
    conversa_id = str(conversa.id)
    try:
        async with session.begin_nested():
            cartao = await cliente_svc.cartao_cliente(session, conversa)
    except Exception as e:  # noqa: BLE001
        logger.warning("atendimento_cliente_falhou", conversa_id=conversa_id, err=type(e).__name__)
        return {}
    return dict(cartao) if isinstance(cartao, dict) else {}


async def _pedido_atualizavel(session: AsyncSession, conversa: AtendimentoConversa) -> bool:
    """O "atualizar" do painel Pedido iria à loja? (P4: a tela esconde o botão.)

    Os mesmos portões do POST /pedido/atualizar, na mesma ordem, menos as
    travas de minuto (essas dependem do clique): plataforma com retrato,
    loja conectada, leitura ligada e canal não desligado.
    """
    if conversa.plataforma not in PLATAFORMAS_COM_PEDIDO or conversa.integration_id is None:
        return False
    if not get_settings().atendimento_leitura_ativa:
        return False
    integration = await session.get(Integration, conversa.integration_id)
    if integration is None or integration.archived_at is not None:
        return False
    if conversa.canal_id is None:
        return True
    canal = await session.get(AtendimentoCanal, conversa.canal_id)
    return canal is None or canal.status != _CANAL_DESLIGADO


def _limite_da_resposta(conversa: AtendimentoConversa) -> int:
    """O tamanho máximo da resposta: o do canal; no e-mail da ponte, o do e-mail
    (`mail_atendimento.constantes.RESPOSTA_MAX_CARACTERES` — o mesmo que o
    envio confere), não os 1000 do canal."""
    if _email_da_ponte(conversa):
        from app.services.mail_atendimento.constantes import RESPOSTA_MAX_CARACTERES

        return RESPOSTA_MAX_CARACTERES
    return limite_caracteres(conversa.plataforma, conversa.canal)


async def _envio(session: AsyncSession, conversa: AtendimentoConversa) -> EnvioOut:
    # Modo observação = quem responde é o Duoke/Seller Center: a loja em
    # `observar` ou o envio desligado no servidor. A tela troca a caixa de
    # envio pelo "O que a IA responderia" (com 👍/👎 e "Copiar").
    envio_desligado = not get_settings().atendimento_envio_ativo
    sem_envio = motivo_canal_sem_envio(conversa.canal, conversa.plataforma, conversa.dados)
    if conversa.plataforma == "amazon" and conversa.integration_id is None and not sem_envio:
        # O e-mail não disse de qual conta Amazon é: antes de qualquer outra
        # trava (envio desligado...), a tela precisa pedir a conta. O e-mail
        # do Tuta não: escolher a conta não o faria sair (`canal_sem_envio`).
        return EnvioOut(
            pode_enviar=False,
            motivo=enviar.MOTIVO_AMAZON_SEM_CONTA,
            codigo=enviar.RECUSA_SEM_INTEGRACAO,
            limite_caracteres=limite_caracteres(conversa.plataforma, conversa.canal),
            modo=None,
            sla_horas=sla_horas(conversa.plataforma, conversa.canal),
            modo_observacao=envio_desligado,
        )
    recusa = await enviar.motivo_para_nao_enviar(session, conversa)
    canal = (
        await session.get(AtendimentoCanal, conversa.canal_id)
        if conversa.canal_id is not None
        else None
    )
    # Loja do robô (Temu/AliExpress): nada sai por aqui em modo nenhum — a
    # tela mostra a sugestão para COPIAR no Seller Center.
    so_le = conversa.plataforma in PLATAFORMAS_ROBO
    return EnvioOut(
        pode_enviar=recusa is None,
        motivo=str(recusa.detail) if recusa else None,
        codigo=recusa.code if recusa else None,
        limite_caracteres=_limite_da_resposta(conversa),
        # Sem canal não há modo: "observar" diria que a loja existe e só lê.
        modo=canal.modo if canal is not None else None,
        sla_horas=sla_horas(conversa.plataforma, conversa.canal),
        # E-mail do Tuta e Zap (05/10/2026): nem observação — a caixa dela é a
        # sugestão da IA, que não os pega. A tela mostra a trava, com a frase
        # dela (qual dos dois e onde responder).
        modo_observacao=(
            not sem_envio
            and (envio_desligado or so_le or (canal is not None and canal.modo == MODO_OBSERVAR))
        ),
    )


async def _sugestoes(
    session: AsyncSession,
    conversa_id: UUID,
    *,
    so_rascunho: UUID | None = None,
    user_id: UUID | None = None,
) -> list[SugestaoOut]:
    """As sugestões da IA que NÃO saíram pelo DaVinci, cada uma com a resposta real.

    É o "IA × equipe" do modo observação: o Duoke responde, o DaVinci lê e
    guarda o que a IA teria dito. `resposta_real` é a primeira mensagem da
    LOJA (qualquer origem, menos a que falhou e a mensagem automática —
    `gravar.mensagem_automatica_sql`, 05/10/2026: a figurinha da campanha não
    é "a equipe respondeu") a partir da mensagem do cliente que a sugestão
    responde — a mesma régua de `gravar._aposentar_rascunho`; sem gatilho (a
    mensagem sumiu), vale a hora da sugestão. Uma consulta só (LATERAL), não
    uma por sugestão.
    `so_rascunho` = só aquela sugestão (a avaliação quer a resposta real dela).
    `user_id` = quem está vendo: a nota dada por OUTRA pessoa vem marcada
    (`de_outra_pessoa`) — quem só lê não a troca (`_pode_trocar_a_nota`).
    """
    gatilho = aliased(AtendimentoMensagem)
    resposta = aliased(AtendimentoMensagem)
    referencia = func.coalesce(
        gatilho.enviada_em, gatilho.created_at, AtendimentoRascunho.created_at
    )
    momento = func.coalesce(resposta.enviada_em, resposta.created_at)
    real = (
        select(
            resposta.id.label("id"),
            resposta.texto.label("texto"),
            momento.label("em"),
            resposta.origem.label("origem"),
        )
        .where(
            resposta.conversa_id == AtendimentoRascunho.conversa_id,
            resposta.autor == AUTOR_LOJA,
            resposta.status != MSG_FALHOU,
            momento >= referencia,
            ~gravar.mensagem_automatica_sql(resposta.texto, resposta.payload),
        )
        .order_by(momento.asc(), resposta.created_at.asc())
        .limit(1)
        .lateral("resposta_real")
    )
    consulta = (
        select(
            AtendimentoRascunho,
            AtendimentoAvaliacao.nota,
            AtendimentoAvaliacao.correcao,
            AtendimentoAvaliacao.user_id,
            real.c.id,
            real.c.texto,
            real.c.em,
            real.c.origem,
        )
        .outerjoin(
            AtendimentoAvaliacao, AtendimentoAvaliacao.rascunho_id == AtendimentoRascunho.id
        )
        .outerjoin(gatilho, gatilho.id == AtendimentoRascunho.mensagem_gatilho_id)
        .outerjoin(real, true())
        .where(
            AtendimentoRascunho.conversa_id == conversa_id,
            AtendimentoRascunho.status.in_(_STATUS_SUGESTAO),
        )
    )
    if so_rascunho is not None:
        consulta = consulta.where(AtendimentoRascunho.id == so_rascunho)
    linhas = (
        await session.execute(
            consulta.order_by(
                AtendimentoRascunho.created_at.desc(), AtendimentoRascunho.id.desc()
            ).limit(MAX_SUGESTOES_DETALHE)
        )
    ).all()
    saida: list[SugestaoOut] = []
    # As mais novas na consulta (limite), da mais velha para a mais nova na
    # tela — a mesma ordem das mensagens.
    for r, nota, correcao, autor_id, real_id, real_texto, real_em, real_origem in reversed(linhas):
        saida.append(
            SugestaoOut(
                id=r.id,
                texto=r.texto,
                categoria=r.categoria,
                confianca=r.confianca,
                status=r.status,
                created_at=_utc(r.created_at),
                precisa_humano=r.precisa_humano,
                validador_erros=list(r.validador_erros or []),
                mensagem_gatilho_id=(
                    str(r.mensagem_gatilho_id) if r.mensagem_gatilho_id is not None else None
                ),
                avaliacao=(
                    AvaliacaoResumoOut(
                        nota=nota,
                        correcao=correcao,
                        de_outra_pessoa=_de_outra_pessoa(autor_id, user_id),
                    )
                    if nota is not None or correcao is not None
                    else None
                ),
                resposta_real=(
                    RespostaRealOut(
                        mensagem_id=str(real_id),
                        texto=real_texto,
                        enviada_em=_utc(real_em),
                        origem=real_origem,
                    )
                    if real_id is not None
                    else None
                ),
            )
        )
    return saida


@router.get("/conversas/{conversa_id}", response_model=ConversaDetalheOut)
async def detalhe_conversa(
    conversa_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_view)],
) -> ConversaDetalheOut | dict[str, Any]:
    """A conversa aberta: mensagens, sugestão da IA, pedido e se dá para enviar."""
    scope = await resolve_team_scope(session, user)
    if instagram.e_instagram(conversa_id):
        dados = await instagram.detalhe(session, conversa_id) if scope.unrestricted else None
        if dados is None:
            raise HTTPException(404, detail={"code": "conversa_nao_encontrada"})
        return dados
    c = await _conversa_ou_404(session, conversa_id, scope)
    # Envio que morreu no meio (deploy) com a leitura desligada: ninguém mais
    # o aposentaria, e a linha `enviando` ficaria "enviando…" para sempre.
    # Aqui ele vira `revisar` (e aparece em "A conferir").
    if await enviar.aposentar_envios_presos(session, conversa_id=c.id):
        await session.commit()
    momento = func.coalesce(AtendimentoMensagem.enviada_em, AtendimentoMensagem.created_at)
    mensagens = list(
        reversed(
            (
                await session.execute(
                    select(AtendimentoMensagem)
                    .where(AtendimentoMensagem.conversa_id == c.id)
                    .order_by(momento.desc(), AtendimentoMensagem.created_at.desc())
                    .limit(MAX_MENSAGENS_DETALHE)
                )
            )
            .scalars()
            .all()
        )
    )
    nomes = await _nomes(session, {m.autor_user_id for m in mensagens})
    rascunho = (
        await session.execute(
            select(AtendimentoRascunho)
            .where(
                AtendimentoRascunho.conversa_id == c.id,
                AtendimentoRascunho.status == RASCUNHO_PENDENTE,
            )
            .order_by(AtendimentoRascunho.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    detalhe = {
        "conversa": await _conversa_out(session, c),
        "mensagens": [_mensagem_out(m, conversa=c, nomes=nomes) for m in mensagens],
        "rascunho": _rascunho_out(rascunho),
        "contexto": await _contexto(session, c),
        "envio": await _envio(session, c),
        "pedido_mkt": _do_dados(c, "pedido_mkt"),
        "produto": _do_dados(c, "produto"),
        "sugestoes": await _sugestoes(session, c.id, user_id=user.id),
        "pedido_atualizavel": await _pedido_atualizavel(session, c),
        "etiqueta_historico": await _historico_etiqueta(session, c.id),
    }
    # Por ÚLTIMO: o cartão pode ir à loja (ML ao vivo, com cache) e, se
    # falhar, nada depois dele depende da sessão.
    detalhe["cliente"] = await _cliente(session, c)
    return ConversaDetalheOut(**detalhe)


async def _historico_etiqueta(
    session: AsyncSession, conversa_id: UUID
) -> list[EtiquetaHistoricoOut]:
    """A linha do tempo da etiqueta (de → para, o porquê, quem trocou à mão)."""
    linhas = await etiqueta_svc.historico_da_conversa(session, conversa_id)
    nomes = await _nomes(session, {h.por_user_id for h in linhas})
    return [
        EtiquetaHistoricoOut(
            id=h.id,
            de=h.de,
            para=h.para,
            de_rotulo=rotulo_etiqueta(h.de) if h.de else None,
            para_rotulo=rotulo_etiqueta(h.para),
            motivo=h.motivo,
            por_user_id=h.por_user_id,
            por_nome=nomes.get(h.por_user_id) if h.por_user_id else None,
            em=_utc(h.em),
        )
        for h in linhas
    ]


# ── Ações na conversa ─────────────────────────────────────────────────────


@router.post("/conversas/{conversa_id}/etiqueta", response_model=EtiquetaTrocaOut)
async def trocar_etiqueta(
    conversa_id: str,
    body: EtiquetaIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_edit)],
) -> EtiquetaTrocaOut:
    """Troca à mão da etiqueta (RF1). Fica no histórico com o nome de quem trocou.

    Vale até o próximo acontecimento automático (a etiqueta que o motor
    calcula mudar); escolher a mesma que o motor dá = voltar ao automático.
    Só muda o DaVinci: nada sai para a plataforma nem para o Bling.
    """
    if instagram.e_instagram(conversa_id):
        raise HTTPException(
            409,
            detail={"code": "somente_leitura", "detail": "DM do Instagram não tem etiqueta."},
        )
    escolhida = (body.etiqueta or "").strip().lower()
    if escolhida not in ETIQUETAS:
        raise HTTPException(422, detail={"code": "etiqueta_invalida"})
    scope = await resolve_team_scope(session, user)
    c = await _conversa_ou_404(session, conversa_id, scope)
    # Trava e relê: o recálculo do sync/cron não passa por cima desta troca
    # (e esta não passa por cima do que ele acabou de gravar).
    await _travar_ou_409(session, c, "conversa_ocupada")
    de = c.etiqueta
    mudou = await etiqueta_svc.trocar_etiqueta_manual(
        session, c, escolhida, user_id=user.id, motivo=body.motivo
    )
    await session.commit()
    logger.info(
        "atendimento_etiqueta_trocada",
        conversa_id=str(c.id),
        de=de,
        para=c.etiqueta,
        manual=bool(c.etiqueta_manual),
        mudou=mudou,
        user_id=str(user.id),
    )
    return EtiquetaTrocaOut(
        conversa=await _conversa_out(session, c),
        etiqueta_historico=await _historico_etiqueta(session, c.id),
    )



@router.post("/conversas/{conversa_id}/responder", response_model=ResponderOut)
async def responder(
    conversa_id: str,
    body: ResponderIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_edit)],
) -> ResponderOut:
    """Envia a resposta. Trava → 409 com o `code` (texto reprovado → 422).

    Erro da PLATAFORMA não é erro HTTP: volta 200 com a mensagem em
    `falhou`/`revisar` — a linha existe e a tela mostra o que houve.
    """
    if instagram.e_instagram(conversa_id):
        raise _somente_leitura()
    scope = await resolve_team_scope(session, user)
    c = await _conversa_ou_404(session, conversa_id, scope)
    try:
        m = await enviar.enviar_resposta(
            session,
            c,
            body.texto,
            user=user,
            rascunho_id=body.rascunho_id,
            origem=ORIGEM_HUMANO,
            ultima_vista_id=body.ultima_vista_id,
            confirmar=body.confirmar,
            confirmar_nao_responde=body.confirmar_nao_responde,
            mail_message_id=body.mail_message_id,
        )
    except EnvioRecusado as e:
        logger.info(
            "atendimento_envio_recusado",
            conversa_id=str(c.id),
            code=e.code,
            user_id=str(user.id),
        )
        raise _recusa_http(e) from e
    return ResponderOut(mensagem=_mensagem_out(m, conversa=c, nomes={user.id: _nome(user)}))


@router.post("/conversas/{conversa_id}/rascunho", response_model=RascunhoUnicoOut)
async def pedir_rascunho(
    conversa_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_quem_le)],
) -> RascunhoUnicoOut:
    """Pede uma sugestão da IA agora (mesmo sem o cliente estar esperando).

    Na fase de observação quem só lê também pede (`ROTAS_DE_QUEM_LE`): a
    sugestão fica guardada para conferir, nada sai para a plataforma.
    """
    if instagram.e_instagram(conversa_id):
        raise _somente_leitura()
    if not get_settings().atendimento_ia_ativa:
        raise HTTPException(
            409,
            detail={"code": "ia_desligada", "detail": "A IA do atendimento está desligada."},
        )
    scope = await resolve_team_scope(session, user)
    c = await _conversa_ou_404(session, conversa_id, scope)
    if c.plataforma in PLATAFORMAS_EXTERNAS:
        # Carrinho do site e comentário das redes (02/10/2026): o prompt e o
        # validador da IA são de marketplace — nada de gastar o provedor com
        # o "carrinho parado" nem com resposta PÚBLICA de rede social.
        raise HTTPException(
            409,
            detail={
                "code": "canal_sem_ia",
                "detail": "A IA não sugere resposta para carrinho de site nem comentário de rede.",
            },
        )
    sem_envio = motivo_canal_sem_envio(c.canal, c.plataforma, c.dados)
    if sem_envio:
        # E-mail do Tuta e Zap (05/10/2026): a IA não os pega (`ia._gerar`
        # também corta). O mesmo código e a mesma frase da trava do envio.
        raise HTTPException(
            409, detail={"code": enviar.RECUSA_CANAL_SEM_ENVIO, "detail": sem_envio}
        )
    from app.services.atendimento import ia as ia_svc

    if not await _dentro_do_limite_sugerir(user):
        raise HTTPException(
            429,
            detail={
                "code": "limite_sugestoes",
                "detail": f"Você já pediu {SUGERIR_POR_HORA_QUEM_LE} sugestões nesta hora — "
                "tente de novo daqui a pouco.",
            },
        )
    # O pedido pela tela (`forcar=True`) não passa pelo teto diário dentro da
    # IA; o de quem só lê passa aqui (ver SUGERIR_POR_HORA_QUEM_LE).
    if _so_le(user) and not await ia_svc._dentro_do_teto(1):
        raise HTTPException(429, detail=TETO_DIARIO_IA)

    r = await ia_svc.gerar_rascunho(session, c, forcar=True)
    await session.commit()
    motivo = None
    if r is not None:
        await session.refresh(r)
    else:
        motivo = await ia_svc.motivo_sem_rascunho(session, c)
    logger.info(
        "atendimento_rascunho_pedido",
        conversa_id=str(c.id),
        gerado=r is not None,
        motivo=motivo,
        user_id=str(user.id),
    )
    return RascunhoUnicoOut(rascunho=_rascunho_out(r), motivo=motivo)


async def _travar_ou_409(session: AsyncSession, obj, code: str) -> None:
    """Trava a linha (esperando no máximo 5 s) e a relê; ocupada → 409 "tente de novo".

    A rodada do sync segura a conversa (e o canal) enquanto grava. Sem a
    trava, fechar a conversa no meio da rodada perdia a mensagem nova do
    cliente (o sync derivava a fila do retrato velho); sem o limite, a tela
    pendurava até a rodada acabar.
    """
    if not await gravar.travar_linha(session, obj, espera=gravar.ESPERA_TRAVA_TELA):
        raise HTTPException(
            409,
            detail={
                "code": code,
                "detail": "A leitura da loja está gravando isto agora. Tente de novo em "
                "alguns segundos.",
            },
        )


def _sem_pedido_na_plataforma(plataforma: str) -> HTTPException:
    """409 limpo: esta plataforma não tem retrato de pedido pela API (ainda)."""
    if plataforma == instagram.PLATAFORMA:
        detalhe = "DM do Instagram não tem pedido de loja."
    else:
        # TikTok: a loja não deu o escopo; Amazon: a caixa ainda não existe.
        # O painel continua com o que o DaVinci já sabe (Bling/Logística).
        detalhe = (
            "O pedido desta plataforma não é lido pela API da loja: o painel mostra o que "
            "o DaVinci já sabe (Bling e Logística)."
        )
    return HTTPException(409, detail={"code": "sem_pedido_na_plataforma", "detail": detalhe})


async def _soltar_trava_pedido(chave: str) -> None:
    try:
        await redis.delete(chave)
    except Exception:  # noqa: BLE001, S110 — o TTL de 60 s solta sozinho
        pass


async def _contar_na_janela(chave: str) -> int:
    """Soma 1 no contador da janela de 1 min e devolve o total (INCR + EXPIRE).

    A validade só é posta no primeiro da janela (o INCR não mexe nela) — e
    reposta se a chave ficou sem (o processo caiu entre os dois comandos):
    contador sem validade travaria o botão da loja para sempre.
    """
    n = int(await redis.incr(chave))
    if n == 1 or await redis.ttl(chave) < 0:
        await redis.expire(chave, JANELA_LIMITE_PEDIDO_S)
    return n


async def _dentro_do_limite_pedido(integration_id: UUID, user_id: UUID) -> bool:
    """Ainda cabe uma ida à loja neste minuto, para esta loja e esta pessoa?

    A tentativa barrada também conta (quem insiste não abre a janela antes)
    — mas a barrada pela LOJA não gasta o teto da PESSOA: a loja cheia não
    impede a pessoa de atualizar o pedido de outra loja. Levanta se o Redis
    cair — quem chama recusa.
    """
    if await _contar_na_janela(_chave_limite_loja(integration_id)) > LIMITE_PEDIDO_POR_LOJA:
        return False
    return await _contar_na_janela(_chave_limite_pessoa(user_id)) <= LIMITE_PEDIDO_POR_PESSOA


@router.post("/conversas/{conversa_id}/pedido/atualizar", response_model=PedidoAtualizarOut)
async def atualizar_pedido(
    conversa_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_edit)],
) -> PedidoAtualizarOut:
    """Renova AGORA o retrato do pedido na plataforma (botão "atualizar" do painel Pedido).

    Só LEITURA na loja (pedido, itens, rastreio) — nada marca lido nem mexe
    no pedido. Uma ida por conversa por minuto (trava no Redis): o clique
    repetido devolve o retrato que já existe, com `atualizado=false`.
    Shopee e ML têm o retrato; TikTok (sem escopo), Amazon (sem caixa) e
    Instagram ficam com o que o DaVinci já sabe — recusa 409, sem ir à loja.

    A conversa fica TRAVADA enquanto a loja responde, como na rodada do
    sync: o retrato vai para `dados`, e sem a trava a leitura que gravasse
    outra chave de `dados` no meio (a reclamação do ML, que tira a conversa
    do automático) seria desfeita pelo `dados` velho desta sessão. A trava
    que o enriquecimento pega antes de mesclar é a mesma (mesma transação).
    Assim quem espera é a rodada do sync, em segundo plano — não a tela: sync
    gravando a conversa agora → 409 "tente de novo" (espera no máximo 5 s),
    antes de gastar a chamada à loja.

    Falha da loja não é erro HTTP: volta 200 com o retrato que já havia e
    `motivo` — o painel não pode ficar em branco porque a API caiu.

    As chaves de leitura valem aqui também: com a leitura desligada (geral,
    `atendimento_leitura_ativa`, ou o canal da loja `desligado`) → 409, sem
    ir à loja — é o interruptor que se usa quando a plataforma começa a
    devolver 429. E há teto por loja e por pessoa por minuto
    (`motivo="limite"`), além da trava por conversa.
    """
    if instagram.e_instagram(conversa_id):
        raise _sem_pedido_na_plataforma(instagram.PLATAFORMA)
    scope = await resolve_team_scope(session, user)
    c = await _conversa_ou_404(session, conversa_id, scope)
    if c.plataforma not in PLATAFORMAS_COM_PEDIDO:
        raise _sem_pedido_na_plataforma(c.plataforma)
    integration = (
        await session.get(Integration, c.integration_id) if c.integration_id is not None else None
    )
    if integration is None or integration.archived_at is not None:
        raise HTTPException(
            409,
            detail={
                "code": enviar.RECUSA_SEM_INTEGRACAO,
                "detail": "A loja desta conversa não está mais conectada ao DaVinci.",
            },
        )
    if not get_settings().atendimento_leitura_ativa:
        raise HTTPException(
            409,
            detail={
                "code": "leitura_desligada",
                "detail": "A leitura das lojas está desligada no DaVinci: o painel fica com o "
                "último retrato do pedido.",
            },
        )
    canal = await session.get(AtendimentoCanal, c.canal_id) if c.canal_id is not None else None
    if canal is not None and canal.status == _CANAL_DESLIGADO:
        raise HTTPException(
            409,
            detail={
                "code": "canal_desligado",
                "detail": "A leitura desta loja está desligada: o painel fica com o último "
                "retrato do pedido.",
            },
        )

    chave = _chave_pedido(c.id)
    try:
        pegou = await redis.set(chave, "1", nx=True, ex=TRAVA_PEDIDO_S)
    except Exception as e:  # noqa: BLE001
        # Sem a trava não há como segurar a rajada na API da loja: melhor não ir.
        logger.warning("atendimento_pedido_trava_falhou", err=type(e).__name__)
        raise HTTPException(503, detail={"code": "trava_indisponivel"}) from e
    if not pegou:
        return PedidoAtualizarOut(
            pedido_mkt=_do_dados(c, "pedido_mkt"),
            produto=_do_dados(c, "produto"),
            atualizado=False,
            motivo="recente",
        )
    try:
        cabe = await _dentro_do_limite_pedido(integration.id, user.id)
    except Exception as e:  # noqa: BLE001
        await _soltar_trava_pedido(chave)
        logger.warning("atendimento_pedido_limite_falhou", err=type(e).__name__)
        raise HTTPException(503, detail={"code": "trava_indisponivel"}) from e
    if not cabe:
        # Não foi à loja: a trava da conversa sai (passado o minuto, o
        # clique nesta mesma conversa vale de novo).
        await _soltar_trava_pedido(chave)
        logger.info(
            "atendimento_pedido_limite",
            conversa_id=str(c.id),
            integration_id=str(integration.id),
            user_id=str(user.id),
        )
        return PedidoAtualizarOut(
            pedido_mkt=_do_dados(c, "pedido_mkt"),
            produto=_do_dados(c, "produto"),
            atualizado=False,
            motivo="limite",
        )
    if not await gravar.travar_linha(session, c, espera=gravar.ESPERA_TRAVA_TELA):
        # Não foi à loja: a pessoa pode tentar de novo já, sem esperar o minuto.
        await _soltar_trava_pedido(chave)
        raise HTTPException(
            409,
            detail={
                "code": enviar.RECUSA_CONVERSA_OCUPADA,
                "detail": "A leitura da loja está gravando esta conversa agora. Tente de novo "
                "em alguns segundos.",
            },
        )

    motivo: str | None = None
    atualizado = False
    # Os ids antes: o rollback expira os objetos, e ler atributo expirado
    # numa sessão assíncrona estoura fora do greenlet.
    ids_log = {"conversa_id": str(c.id), "integration_id": str(integration.id)}
    user_id = str(user.id)
    try:
        cliente = await clientes.cliente_da_integracao(integration)
        # Import tardio pelo nome: o router sobe sem o enriquecimento, e o
        # teste troca o módulo inteiro.
        enriquecer_svc = importlib.import_module(_ENRIQUECER)
        atualizado = bool(
            await enriquecer_svc.enriquecer_conversa(
                session, c, integration, cliente, forcar=True
            )
        )
        await session.commit()
        if not atualizado:
            motivo = "sem_alteracao"
    except Exception as e:  # noqa: BLE001 — o painel fica com o retrato que já tinha
        await session.rollback()
        motivo = "falhou"
        logger.warning("atendimento_pedido_atualizar_falhou", **ids_log, err=type(e).__name__)
    await session.refresh(c)
    logger.info(
        "atendimento_pedido_atualizado",
        **ids_log,
        atualizado=atualizado,
        motivo=motivo,
        user_id=user_id,
    )
    return PedidoAtualizarOut(
        pedido_mkt=_do_dados(c, "pedido_mkt"),
        produto=_do_dados(c, "produto"),
        atualizado=atualizado,
        motivo=motivo,
    )


async def _ligar_conta_amazon(
    session: AsyncSession, c: AtendimentoConversa, integration_id: UUID, scope: TeamScope
) -> None:
    """Conversa da Amazon sem conta identificada → a conta que a pessoa escolheu.

    Só para essa conversa (Amazon e SEM integração): trocar a loja de uma
    conversa que já tem loja mudaria por onde a resposta sai. A integração
    tem de ser Amazon, ativa e do escopo da pessoa; o canal `email` dela é
    garantido (nasce em `observar`, como todo canal).
    """
    if c.plataforma != "amazon" or c.integration_id is not None:
        raise HTTPException(409, detail={"code": "integracao_fixa"})
    integ = await session.get(Integration, integration_id)
    plataforma = getattr(getattr(integ, "platform", None), "value", None)
    if (
        integ is None
        or plataforma != "amazon"
        or integ.archived_at is not None
        or not _no_escopo(scope, integ.id)
    ):
        raise HTTPException(422, detail={"code": "integracao_invalida"})
    canal = (
        await session.execute(
            select(AtendimentoCanal).where(
                AtendimentoCanal.integration_id == integ.id,
                AtendimentoCanal.canal == CANAL_EMAIL,
            )
        )
    ).scalar_one_or_none()
    if canal is None:
        from app.services.atendimento import sync as sync_svc

        await sync_svc.garantir_canais(session)
        canal = (
            await session.execute(
                select(AtendimentoCanal).where(
                    AtendimentoCanal.integration_id == integ.id,
                    AtendimentoCanal.canal == CANAL_EMAIL,
                )
            )
        ).scalar_one_or_none()
    if canal is None:
        raise HTTPException(422, detail={"code": "integracao_invalida"})
    duplicada = await session.scalar(
        select(AtendimentoConversa.id).where(
            AtendimentoConversa.integration_id == integ.id,
            AtendimentoConversa.canal == c.canal,
            AtendimentoConversa.externo_id == c.externo_id,
        )
    )
    if duplicada is not None:
        # A mesma thread já existe na conta escolhida (o e-mail chegou pelas
        # duas caixas): juntar conversas não é coisa de um PATCH.
        raise HTTPException(409, detail={"code": "conversa_duplicada", "id": str(duplicada)})
    # O nome ANTES de mexer na conversa (ver `_nomes_das_lojas`): o mesmo
    # nome de loja que o sync grava nas outras conversas dessa conta.
    integ_id, canal_id, integ_nome = integ.id, canal.id, integ.name
    conta = (await _nomes_das_lojas(session, {integ_id})).get(integ_id) or integ_nome
    c.integration_id = integ_id
    c.canal_id = canal_id
    c.conta = conta


@router.patch("/conversas/{conversa_id}", response_model=ConversaUnicaOut)
async def editar_conversa(
    conversa_id: str,
    body: ConversaPatch,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_edit)],
) -> ConversaUnicaOut:
    """Atribuir, pausar a IA, fechar/reabrir, "não precisa de resposta" e a conta Amazon."""
    if instagram.e_instagram(conversa_id):
        raise _somente_leitura()
    scope = await resolve_team_scope(session, user)
    c = await _conversa_ou_404(session, conversa_id, scope)
    # Trava e relê: o que o sync gravou até agora (mensagem nova do cliente)
    # entra no recálculo abaixo, e o que ele gravar depois vê o nosso fechar.
    await _travar_ou_409(session, c, "conversa_ocupada")
    campos = body.model_fields_set
    if "integration_id" in campos and body.integration_id is not None:
        await _ligar_conta_amazon(session, c, body.integration_id, scope)
    if "atribuido_a" in campos:
        if body.atribuido_a is not None and await session.get(User, body.atribuido_a) is None:
            raise HTTPException(422, detail={"code": "usuario_inexistente"})
        c.atribuido_a = body.atribuido_a
    if "ia_pausada" in campos and body.ia_pausada is not None:
        c.ia_pausada = body.ia_pausada
    if "situacao" in campos and body.situacao is not None:
        if body.situacao == "fechada":
            c.situacao = CONVERSA_FECHADA
        else:
            # Reabrir à mão vale também para a bloqueada (a plataforma
            # liberou): o recálculo decide entre aberta e respondida.
            c.situacao = CONVERSA_ABERTA
            c.bloqueio_motivo = None
    if "sem_resposta_necessaria" in campos and body.sem_resposta_necessaria is not None:
        c.sem_resposta_necessaria = body.sem_resposta_necessaria
    # Fila e prazo derivam da situação e do "não precisa": refaz do banco.
    await gravar.recalcular_conversa(session, c)
    await session.commit()
    logger.info(
        "atendimento_conversa_editada",
        conversa_id=str(c.id),
        campos=sorted(campos),
        user_id=str(user.id),
    )
    return ConversaUnicaOut(conversa=await _conversa_out(session, c))


@router.post("/conversas/{conversa_id}/amazon-copia", response_model=ConversaUnicaOut)
async def escolher_copia_amazon(
    conversa_id: str,
    body: CopiaAmazonIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_edit)],
) -> ConversaUnicaOut:
    """A cópia do Seller Central que empatou: "Esta foi a respondida" / "Não foi esta".

    A pessoa conferiu no Seller Central (07/10/2026: o DaVinci não sabia
    qual das duas conversas do mesmo comprador a loja tinha respondido).
    "Foi esta" grava a cópia como resposta da loja nesta conversa (sai da
    fila se for a resposta mais nova) e tira a marca das outras; "não foi
    esta" só tira a marca desta. Só quem mexe na caixa (a trava do router e
    o `atendimento.edit`). Ver `amazon_email.resolver_copia`.
    """
    scope = await resolve_team_scope(session, user)
    c = await _conversa_ou_404(session, conversa_id, scope)
    if c.plataforma != "amazon":
        raise HTTPException(409, detail={"code": "nao_e_amazon"})
    await _travar_ou_409(session, c, "conversa_ocupada")
    resultado = await amazon_email.resolver_copia(
        session, c, body.message_id, foi_esta=body.foi_esta, user_id=user.id
    )
    if resultado is None:
        # Outra pessoa já resolveu (ou a leitura reavaliou): a tela relê.
        raise HTTPException(404, detail={"code": "copia_nao_encontrada"})
    await session.commit()
    logger.info(
        "atendimento_amazon_copia_resolvida",
        conversa_id=str(c.id),
        foi_esta=body.foi_esta,
        resultado=resultado,
        user_id=str(user.id),
    )
    return ConversaUnicaOut(conversa=await _conversa_out(session, c))


@router.post("/mensagens/{mensagem_id}/conferir", response_model=ConferirOut)
async def conferir_mensagem(
    mensagem_id: UUID,
    body: ConferirIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_edit)],
) -> ConferirOut:
    """A resposta em `revisar` saiu ou não? A pessoa conferiu na plataforma.

    Saiu → `enviada`. Não saiu → `falhou`: a conversa volta para a fila (o
    cliente continua esperando) e dá para responder de novo. Até alguém
    conferir, a `revisar` conta como resposta — responder por cima de uma
    que pode ter saído é o erro que o comprador vê.
    """
    scope = await resolve_team_scope(session, user)
    m = await session.get(AtendimentoMensagem, mensagem_id)
    c = await session.get(AtendimentoConversa, m.conversa_id) if m is not None else None
    if m is None or c is None or not _no_escopo(scope, c.integration_id):
        raise HTTPException(404, detail={"code": "mensagem_nao_encontrada"})
    await _travar_ou_409(session, c, "conversa_ocupada")
    await session.refresh(m)
    if m.status != MSG_REVISAR or m.origem not in ORIGENS_DAVINCI:
        # O sync pode ter adotado (virou `enviada`) enquanto a pessoa olhava.
        raise HTTPException(409, detail={"code": "mensagem_nao_revisar", "status": m.status})
    # E-mail (08/10/2026): a resposta pela fila da Central em `revisar` é um
    # job "incerto" lá — conferir aqui resolve o job DELA (sem reenviar).
    from app.services.mail_atendimento import responder as mail_responder

    ligacao = await mail_responder.ligacao_da_mensagem(session, m.id)
    if ligacao is not None:
        _liga, job = ligacao
        if job.status in ("uncertain", "leased"):
            try:
                await mail_responder.resolver_job(session, job, user, saiu=body.saiu)
            except mail_responder.mail_central.MailError as erro:
                raise HTTPException(409, detail={"code": erro.code}) from None
            await session.refresh(m)
    conferido = {
        "saiu": body.saiu,
        "user_id": str(user.id),
        "em": datetime.now(UTC).isoformat(),
        "erro_antes": m.erro,
    }
    m.payload = {**(m.payload or {}), "conferido": conferido}
    if body.saiu:
        m.status = MSG_ENVIADA
        m.erro = None
    else:
        m.status = MSG_FALHOU
        m.erro = "conferido: não saiu"
    await gravar.recalcular_conversa(session, c)
    await session.commit()
    logger.info(
        "atendimento_mensagem_conferida",
        mensagem_id=str(m.id),
        conversa_id=str(c.id),
        saiu=body.saiu,
        user_id=str(user.id),
    )
    nomes = await _nomes(session, {m.autor_user_id, user.id})
    return ConferirOut(
        mensagem=_mensagem_out(m, conversa=c, nomes=nomes),
        conversa=await _conversa_out(session, c),
    )


# ── Sugestão da IA: descartar e avaliar ───────────────────────────────────


async def _rascunho_ou_404(
    session: AsyncSession, rascunho_id: UUID, scope: TeamScope
) -> AtendimentoRascunho:
    r = await session.get(AtendimentoRascunho, rascunho_id)
    conversa = await session.get(AtendimentoConversa, r.conversa_id) if r is not None else None
    if r is None or conversa is None or not _no_escopo(scope, conversa.integration_id):
        raise HTTPException(404, detail={"code": "rascunho_nao_encontrado"})
    return r


async def _avaliacao_de(session: AsyncSession, rascunho_id: UUID) -> AtendimentoAvaliacao | None:
    return (
        await session.execute(
            select(AtendimentoAvaliacao).where(AtendimentoAvaliacao.rascunho_id == rascunho_id)
        )
    ).scalar_one_or_none()


def _avaliacao_out(av: AtendimentoAvaliacao, user_id: UUID | None) -> AvaliacaoOut:
    return AvaliacaoOut(
        id=av.id,
        rascunho_id=av.rascunho_id,
        acao=av.acao,
        texto_final=av.texto_final,
        similaridade=av.similaridade,
        motivo=av.motivo,
        nota=av.nota,
        correcao=av.correcao,
        de_outra_pessoa=_de_outra_pessoa(av.user_id, user_id),
    )


@router.post("/rascunhos/{rascunho_id}/descartar", response_model=RascunhoUnicoOut)
async def descartar_rascunho(
    rascunho_id: UUID,
    body: DescartarIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_edit)],
) -> RascunhoUnicoOut:
    """A sugestão não serve — e o PORQUÊ fica para a revisão do manual."""
    r = await _rascunho_ou_404(session, rascunho_id, await resolve_team_scope(session, user))
    if r.status not in (RASCUNHO_PENDENTE, RASCUNHO_BLOQUEADO):
        raise HTTPException(409, detail={"code": "rascunho_nao_pendente"})
    r.status = RASCUNHO_DESCARTADO
    av = await _avaliacao_de(session, r.id)
    if av is None:
        session.add(
            AtendimentoAvaliacao(
                rascunho_id=r.id, acao="descartou", motivo=body.motivo, user_id=user.id
            )
        )
    else:
        av.acao = "descartou"
        av.motivo = body.motivo
    await session.commit()
    await session.refresh(r)
    logger.info("atendimento_rascunho_descartado", rascunho_id=str(r.id), user_id=str(user.id))
    return RascunhoUnicoOut(rascunho=_rascunho_out(r))


async def _texto_final(session: AsyncSession, r: AtendimentoRascunho) -> str | None:
    """O que saiu no lugar da sugestão, para a avaliação.

    A resposta que saiu DELA (enviada igual/editada); na sugestão
    `substituido`, a resposta da loja que tomou o lugar dela (Duoke, Seller
    Center ou a equipe escrevendo do zero) — é a comparação IA × equipe.
    Descartada/bloqueada: nada saiu dela, fica None.
    """
    proprio = await session.scalar(
        select(AtendimentoMensagem.texto)
        .where(
            AtendimentoMensagem.rascunho_id == r.id,
            AtendimentoMensagem.status != MSG_FALHOU,
        )
        .order_by(AtendimentoMensagem.created_at.desc())
        .limit(1)
    )
    if proprio is not None or r.status != RASCUNHO_SUBSTITUIDO:
        return proprio
    sugestoes = await _sugestoes(session, r.conversa_id, so_rascunho=r.id)
    real = sugestoes[0].resposta_real if sugestoes else None
    return real.texto if real is not None else None


async def _preencher_acao(
    session: AsyncSession, av: AtendimentoAvaliacao, r: AtendimentoRascunho, acao: str
) -> None:
    """A ação e, quando algo saiu no lugar da sugestão, o texto e a similaridade."""
    av.acao = acao
    if acao == ACAO_OBSERVOU:
        # Pendente: nada saiu ainda — não há texto final a comparar.
        return
    texto_final = await _texto_final(session, r)
    av.texto_final = texto_final
    av.similaridade = (
        enviar.similaridade(texto_final, r.texto) if texto_final is not None else None
    )


def _pode_trocar_a_nota(user: User, av: AtendimentoAvaliacao) -> bool:
    """A avaliação que já existe pode ganhar a nota DESTA pessoa?

    Quem mexe, sempre (o de antes: a linha passa a ser de quem avaliou por
    último). Quem só lê (fase de observação): a linha sem dono, a dela, e a
    linha SEM NOTA — a do "descartar" de quem mexe ou a do envio, que só
    guardam a ação (ela só preenche a nota; a ação e o motivo ficam).
    """
    if not _so_le(user):
        return True
    return av.nota is None or av.user_id is None or av.user_id == user.id


def _de_outra_pessoa(autor_id: UUID | None, user_id: UUID | None) -> bool:
    """A nota é de OUTRA pessoa (a tela de quem só lê esconde o 👍/👎 dela)."""
    return autor_id is not None and user_id is not None and autor_id != user_id


@router.post("/rascunhos/{rascunho_id}/avaliacao", response_model=AvaliacaoUnicaOut)
async def avaliar_rascunho(
    rascunho_id: UUID,
    body: AvaliacaoIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_quem_le)],
) -> AvaliacaoUnicaOut:
    """👍/👎 na sugestão — é o que ensina a IA. Uma por sugestão (upsert).

    Vale para qualquer estado — inclusive a sugestão AINDA pendente: no modo
    observação nada sai pelo DaVinci (quem responde é o Duoke), e a pessoa
    avalia o que a IA teria dito sem enviar. O 👍 faz o texto da sugestão
    virar exemplo aprovado; o 👎 com correção vira correção no prompt (P3,
    em `ia.py`). Como a regra do manual, a nota de admin vale na plataforma
    e a de quem não é admin só na mesma loja, nunca num canal em `auto`
    (`ia._avaliacao_vale_aqui`) — por isso aqui basta o `edit` e o escopo
    da equipe. A pendente ganha a ação `observou`; quando ela sai da
    caixa (a loja respondeu por fora → `substituido`; bloqueada/descartada),
    a próxima nota promove a ação à de verdade, com a resposta real ao lado.

    Sugestão enviada pelo AUTOMÁTICO não tem avaliação (exemplo aprovado é o
    que pessoa aprovou): a nota de uma pessoa aqui é que cria a linha.

    Quem só lê (fase de observação, 07/10/2026) avalia a sugestão sem nota e
    troca a PRÓPRIA nota, mas não a que outra pessoa deu (409
    `avaliacao_de_outra_pessoa`): o "IA × equipe" de quem cuida do
    Atendimento não pode ser apagado por um clique de quem está se
    ambientando.
    """
    r = await _rascunho_ou_404(session, rascunho_id, await resolve_team_scope(session, user))
    acao = _ACAO_PELO_STATUS.get(r.status, ACAO_OBSERVOU)
    av = await _avaliacao_de(session, r.id)
    criada = False
    if av is None:
        nova = AtendimentoAvaliacao(
            rascunho_id=r.id, user_id=user.id, nota=body.nota, correcao=body.correcao
        )
        await _preencher_acao(session, nova, r, acao)
        await session.flush()  # pendências alheias fora do SAVEPOINT
        try:
            # Dois cliques (ou duas abas) ao mesmo tempo: o UNIQUE barra o
            # segundo INSERT, e ele vira atualização da linha do primeiro.
            async with session.begin_nested():
                session.add(nova)
                await session.flush()
            av, criada = nova, True
        except IntegrityError:
            av = await _avaliacao_de(session, r.id)
            if av is None:
                raise
    autor_anterior = av.user_id
    if not criada and not _pode_trocar_a_nota(user, av):
        raise HTTPException(
            409,
            detail={
                "code": "avaliacao_de_outra_pessoa",
                "detail": "Esta sugestão já foi avaliada por outra pessoa — a nota dela fica.",
            },
        )
    if not criada:
        if av.acao == ACAO_OBSERVOU and acao != ACAO_OBSERVOU:
            await _preencher_acao(session, av, r, acao)
        av.nota = body.nota
        av.correcao = body.correcao
        # A nota e a correção são de quem as deu AGORA: se outra pessoa
        # refaz a avaliação, a linha passa a ser dela (o `updated_at` anda
        # junto). Manter o primeiro autor punha o 👎 e o texto de B no nome
        # de A — e o "IA × equipe" do teste em observação é o que decide se
        # a IA passa a responder. A troca fica no log (a tabela está fora
        # do Histórico).
        av.user_id = user.id
    await session.commit()
    await session.refresh(av)
    logger.info(
        "atendimento_rascunho_avaliado",
        rascunho_id=str(r.id),
        nota=body.nota,
        user_id=str(user.id),
        autor_anterior=(
            str(autor_anterior)
            if autor_anterior is not None and autor_anterior != user.id
            else None
        ),
    )
    return AvaliacaoUnicaOut(avaliacao=_avaliacao_out(av, user.id))


# ── Canais (lojas e modo) ─────────────────────────────────────────────────


@router.get("/canais", response_model=list[CanalOut])
async def listar_canais(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_view)],
) -> list[CanalOut]:
    return await _canais(session, await resolve_team_scope(session, user))


@router.patch("/canais/{canal_id}", response_model=CanalOut)
async def editar_canal(
    canal_id: UUID,
    body: CanalPatch,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_edit)],
) -> CanalOut:
    """Troca o modo da loja (observar/humano/copiloto/auto) e as categorias do automático.

    Ligar `auto` — ou mexer nas categorias que saem sozinhas — só admin:
    é resposta a comprador sem pessoa conferindo. Desligar qualquer um pode.
    Categoria só-humano (troca, reembolso, defeito...) nunca entra no automático.
    """
    canal = await session.get(AtendimentoCanal, canal_id)
    scope = await resolve_team_scope(session, user)
    if canal is None or not _no_escopo(scope, canal.integration_id):
        raise HTTPException(404, detail={"code": "canal_nao_encontrado"})
    # Desligar o `auto` numa emergência não pode pendurar atrás da rodada do
    # sync (que grava o contador de não lidas no canal): 409 e tenta de novo.
    await _travar_ou_409(session, canal, "canal_ocupado")
    eh_admin = user.role == UserRole.ADMIN
    campos = body.model_fields_set
    if canais_externos.eh_externo(canal) and (
        ("modo" in campos and not canais_externos.modo_permitido(canal.plataforma, body.modo))
        or ("auto_categorias" in campos and body.auto_categorias)
    ):
        # Site (sem por onde responder) e rede social (a resposta é pela tela,
        # sempre por pessoa): nem copiloto nem automático.
        raise HTTPException(
            422,
            detail={
                "code": "modo_invalido_externo",
                "detail": "Site: só observar. Rede social: observar ou humano "
                "(a resposta é sempre de uma pessoa).",
            },
        )
    if canal.plataforma in PLATAFORMAS_ROBO and (
        ("modo" in campos and not robo.modo_permitido(canal.plataforma, body.modo))
        or ("auto_categorias" in campos and body.auto_categorias)
    ):
        # Loja do robô do Mac mini: nada sai pelo DaVinci (a resposta é no
        # Seller Center), então nem `humano` nem `auto` fazem sentido.
        raise HTTPException(
            422,
            detail={
                "code": "modo_invalido_robo",
                "detail": "Loja lida pelo robô do Mac mini: só observar ou copiloto "
                "(a resposta é dada no Seller Center).",
            },
        )
    if "modo" in campos and body.modo is not None and body.modo != canal.modo:
        if body.modo == MODO_AUTO and not eh_admin:
            raise HTTPException(403, detail={"code": "so_admin"})
        if canais_externos.eh_externo(canal) and body.modo in MODOS_QUE_ENVIAM and not eh_admin:
            # Rede social em `humano` = responder e ocultar EM PÚBLICO como a
            # marca (com o envio ligado no servidor): só admin liga, como o
            # automático. Voltar para observar qualquer um pode.
            raise HTTPException(
                403,
                detail={
                    "code": "so_admin",
                    "detail": "Só admin libera responder em público pela conta da marca.",
                },
            )
        canal.modo = body.modo
    if (
        "auto_categorias" in campos
        and body.auto_categorias is not None
        and set(body.auto_categorias) != set(canal.auto_categorias or [])
    ):
        if not eh_admin:
            raise HTTPException(403, detail={"code": "so_admin"})
        # Os assuntos são os do manual base (a mesma lista com que a IA
        # classifica; sem manual, as constantes): um assunto que só existe no
        # manual pode ir para o automático, e um que não existe não entra.
        manual = await _categorias_do_manual(session)
        ordem = [str(c["id"]) for c in manual]
        # Só-humano pelas constantes E pelo manual base (a tabela pode marcar
        # outro assunto como só-humano): nunca menos trava que as constantes.
        # Vem antes do "desconhecido": é a recusa que importa mais.
        so_humano_manual = {c["id"] for c in manual if c.get("so_humano")}
        so_humano = [
            c for c in body.auto_categorias if c in CATEGORIAS_SO_HUMANO or c in so_humano_manual
        ]
        if so_humano:
            raise HTTPException(
                422, detail={"code": "categoria_so_humano", "categorias": so_humano}
            )
        desconhecidas = [c for c in body.auto_categorias if c not in ordem]
        if desconhecidas:
            raise HTTPException(
                422,
                detail={
                    "code": "categoria_invalida",
                    "detail": "Assunto desconhecido (ou desativado) no manual: "
                    + ", ".join(desconhecidas),
                    "categorias": desconhecidas,
                },
            )
        # Ordem do manual e sem repetição: o JSONB compara igual entre edições.
        escolhidas = set(body.auto_categorias)
        canal.auto_categorias = [c for c in ordem if c in escolhidas]
    await session.commit()
    await session.refresh(canal)
    integration = (
        await session.get(Integration, canal.integration_id)
        if canal.integration_id is not None
        else None
    )
    logger.info(
        "atendimento_canal_editado",
        canal_id=str(canal.id),
        modo=canal.modo,
        auto_categorias=len(canal.auto_categorias or []),
        user_id=str(user.id),
    )
    integracao = integration.name if integration else None
    saida = _canal_out(canal, integracao, integracao=integracao)
    nomes = await _nomes_das_lojas(session, {saida.integration_id})
    saida.conta = nomes.get(saida.integration_id) or saida.conta
    return saida


# ── Manual da IA (regras QUANDO → FAÇA) ───────────────────────────────────


async def _so_admin_se_vale_para_auto(
    session: AsyncSession, user: User, *escopos: tuple[str | None, str | None]
) -> None:
    """Regra que vale para algum canal em `auto` só admin cria, muda, liga ou apaga.

    O manual entra no prompt de todas as lojas do escopo da regra — inclusive
    das que estão no automático, que só admin liga. Sem isto, quem não pode
    ligar o `auto` mudaria o que ele manda sozinho ("QUANDO agradecer → FAÇA
    peça para avaliar"). Regra de plataforma/canal sem loja em `auto`
    continua com quem tem `edit`: ela só muda sugestão que PESSOA confere.
    """
    if user.role == UserRole.ADMIN:
        return
    for plataforma, canal in escopos:
        consulta = select(AtendimentoCanal.id).where(AtendimentoCanal.modo == MODO_AUTO)
        if plataforma:
            consulta = consulta.where(AtendimentoCanal.plataforma == plataforma)
        if canal:
            consulta = consulta.where(AtendimentoCanal.canal == canal)
        if await session.scalar(consulta.limit(1)) is not None:
            raise HTTPException(
                403,
                detail={
                    "code": "so_admin",
                    "detail": "Esta regra vale para uma loja no envio automático: só admin "
                    "muda.",
                },
            )


async def _categorias_do_manual(session: AsyncSession) -> list[dict[str, Any]]:
    """Os assuntos oficiais (`manual.categorias_ativas`); sem o manual, as constantes.

    A tabela `atendimento_categorias` (o manual base importado) é a lista
    que a IA usa para classificar; vazia, o próprio serviço cai em
    `constantes.CATEGORIAS`. Aqui a queda vale também para o serviço
    ausente ou falhando (SAVEPOINT: a transação segue viva) — a tela do
    manual e a trava das categorias do automático não podem parar por isso.
    """
    manual_svc = _modulo(_MANUAL)
    categorias: list[dict[str, Any]] = []
    if manual_svc is not None:
        try:
            async with session.begin_nested():
                categorias = [
                    dict(c)
                    for c in await manual_svc.categorias_ativas(session) or []
                    if isinstance(c, dict) and c.get("id")
                ]
        except Exception as e:  # noqa: BLE001 — cai nas constantes
            logger.warning("atendimento_categorias_falhou", err=type(e).__name__)
            categorias = []
    if categorias:
        return categorias
    return [
        {
            "id": c,
            "nome": CATEGORIAS_INFO.get(c, (c, ""))[0],
            "descricao": CATEGORIAS_INFO.get(c, (c, ""))[1],
            "so_humano": c in CATEGORIAS_SO_HUMANO,
            "ordem": (i + 1) * 10,
        }
        for i, c in enumerate(CATEGORIAS)
    ]


async def _conferir_categoria(session: AsyncSession, categoria: str | None) -> None:
    """Regra só aponta para assunto que existe (e está ativo) no manual → senão 422."""
    if categoria is None:
        return
    if categoria not in {str(c["id"]) for c in await _categorias_do_manual(session)}:
        raise HTTPException(
            422,
            detail={
                "code": "categoria_invalida",
                "detail": f"Assunto desconhecido (ou desativado) no manual: {categoria}.",
            },
        )


def _dados_da_regra(r: AtendimentoRegra) -> dict[str, Any]:
    """A regra no formato de `manual.conflitos_da_regra` (o mesmo do importador)."""
    return {
        "quando": r.quando,
        "faca": r.faca,
        "tipo": r.tipo,
        "categoria": r.categoria,
        "plataforma": r.plataforma,
        "canal": r.canal,
        "prioridade": r.prioridade,
        "ativa": r.ativa,
    }


async def _recusar_conflito(
    session: AsyncSession, dados: dict[str, Any], *, ignorar_id: UUID | None = None
) -> None:
    """Regra ativa que bate com outra ativa → 409 `regra_conflitante` com a existente.

    Conflito (P7) = duas regras ATIVAS do tipo `categoria` para o mesmo
    (assunto, plataforma, canal): a IA receberia duas ordens para o mesmo
    caso e escolheria sozinha. Quem decide o que bate é
    `manual.conflitos_da_regra` (o mesmo que o importador do manual usa) —
    aqui só a trava e o 409. A trava da transação (`TRAVA_REGRAS`) fica
    até o commit de quem chama: a conferência e o INSERT/UPDATE andam
    juntos. Sem o serviço, recusa (503) em vez de gravar sem conferir.
    """
    manual_svc = _modulo(_MANUAL)
    if manual_svc is None:
        raise HTTPException(
            503,
            detail={
                "code": "manual_indisponivel",
                "detail": "A conferência de conflito do manual não está disponível agora.",
            },
        )
    travar = getattr(manual_svc, "travar_regras", None)
    if travar is not None:
        await travar(session)  # a mesma chave `TRAVA_REGRAS`, do lado do manual
    else:
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": TRAVA_REGRAS}
        )
    conflitos = list(
        await manual_svc.conflitos_da_regra(session, dados, ignorar_id=ignorar_id) or []
    )
    if conflitos:
        conflitos = jsonable_encoder(conflitos)
        raise HTTPException(
            409,
            detail={
                "code": "regra_conflitante",
                "detail": "Já existe uma regra ativa para este assunto, plataforma e canal: "
                "edite ou desative a outra antes.",
                "regra": conflitos[0],
                "conflitos": conflitos,
            },
        )


def _ids_citados(conflito: Any, conhecidos: set[str]) -> set[str]:
    """Os ids de regra citados num conflito, em qualquer profundidade.

    O formato de `manual.conflitos_existentes` é do serviço (hoje um grupo
    `{categoria, plataforma, canal, regra_ids, regras}`); aqui só interessa
    QUAIS regras aparecem juntas — e isso não quebra se o formato mudar.
    """
    achados: set[str] = set()
    pilha = [conflito]
    while pilha:
        valor = pilha.pop()
        if isinstance(valor, dict):
            pilha.extend(valor.values())
        elif isinstance(valor, list | tuple | set):
            pilha.extend(valor)
        elif valor is not None and str(valor) in conhecidos:
            achados.add(str(valor))
    return achados


async def _conflitos_atuais(session: AsyncSession, r: AtendimentoRegra) -> set[str] | None:
    """Com quem a regra (já gravada) bate AGORA — para a resposta do PATCH.

    Editar só o texto de uma regra que já estava em conflito passa, e a tela
    tem de continuar vendo o vermelho. Só leitura (sem trava); se a
    conferência falhar, a resposta sai sem a marca (o GET /regras a traz).
    """
    if not r.ativa:
        return None
    dados, regra_id = _dados_da_regra(r), r.id
    manual_svc = _modulo(_MANUAL)
    if manual_svc is None:
        return None
    try:
        async with session.begin_nested():
            outras = await manual_svc.conflitos_da_regra(session, dados, ignorar_id=regra_id)
    except Exception as e:  # noqa: BLE001 — a resposta sai sem a marca
        logger.warning("atendimento_conflitos_falhou", regra_id=str(regra_id), err=type(e).__name__)
        return None
    ids = {str(o.get("id")) for o in outras or [] if isinstance(o, dict) and o.get("id")}
    return ids or None


def _regra_out(r: AtendimentoRegra, *, conflita_com: set[str] | None = None) -> RegraOut:
    """`conflita_com=None` = não está em conflito; conjunto (até vazio) = está."""
    return RegraOut(
        id=r.id,
        quando=r.quando,
        faca=r.faca,
        plataforma=r.plataforma,
        canal=r.canal,
        ativa=r.ativa,
        tipo=r.tipo or TIPO_REGRA_CATEGORIA,
        categoria=r.categoria,
        prioridade=r.prioridade if r.prioridade is not None else PRIORIDADE_REGRA_PADRAO,
        updated_at=_utc(r.updated_at),
        em_conflito=conflita_com is not None,
        conflita_com=[UUID(i) for i in sorted(conflita_com or ())],
    )


@router.get("/regras", response_model=ListaRegrasOut)
async def listar_regras(
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[User, Depends(_view)],
) -> ListaRegrasOut:
    """O manual inteiro e os conflitos que JÁ existem (a tela pinta de vermelho).

    A API não deixa nascer conflito novo (409 no POST/PATCH) e o importador
    do manual recusa o arquivo que traria um, mas o que veio de antes da
    trava — regra criada antes da parte 2, ou uma regra geral que ganhou
    assunto pelo banco — continua lá até alguém resolver. Se a conferência
    falhar, o manual aparece sem os conflitos (e o log diz).
    """
    regras = (
        (await session.execute(select(AtendimentoRegra).order_by(AtendimentoRegra.created_at)))
        .scalars()
        .all()
    )
    conflitos: list[dict[str, Any]] = []
    manual_svc = _modulo(_MANUAL)
    if manual_svc is not None:
        try:
            async with session.begin_nested():
                conflitos = [
                    dict(c)
                    for c in await manual_svc.conflitos_existentes(session) or []
                    if isinstance(c, dict)
                ]
        except Exception as e:  # noqa: BLE001 — o manual aparece sem os conflitos
            logger.warning("atendimento_conflitos_falhou", err=type(e).__name__)
            conflitos = []
    conhecidos = {str(r.id) for r in regras}
    vizinhos: dict[str, set[str]] = {}
    for conflito in conflitos:
        ids = _ids_citados(conflito, conhecidos)
        for i in ids:
            vizinhos.setdefault(i, set()).update(ids - {i})
    return ListaRegrasOut(
        regras=[_regra_out(r, conflita_com=vizinhos.get(str(r.id))) for r in regras],
        conflitos=jsonable_encoder(conflitos),
    )


@router.get("/categorias", response_model=list[CategoriaOut])
async def listar_categorias(
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[User, Depends(_view)],
) -> list[CategoriaOut]:
    """Os assuntos oficiais do manual (o seletor de categoria da regra e do automático)."""
    saida: list[CategoriaOut] = []
    for c in await _categorias_do_manual(session):
        try:
            saida.append(
                CategoriaOut.model_validate(
                    {**c, "exemplos": c.get("exemplos") or [], "lacunas": c.get("lacunas") or []}
                )
            )
        except ValueError as e:
            # Uma linha estranha não esconde as outras (só o id no log).
            logger.warning(
                "atendimento_categoria_invalida", categoria=str(c.get("id"))[:40], err=str(e)[:200]
            )
    return saida


@router.post("/regras", response_model=RegraOut, status_code=status.HTTP_201_CREATED)
async def criar_regra(
    body: RegraIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_edit)],
) -> RegraOut:
    """Nova regra. Ativa e batendo com outra ativa → 409 `regra_conflitante`."""
    _validar_par(body.plataforma, body.canal)
    # Assunto só tem efeito no tipo `categoria`: segurança e estilo valem
    # para toda mensagem, e gravar um assunto ali só confundiria a leitura.
    categoria = body.categoria if body.tipo == TIPO_REGRA_CATEGORIA else None
    await _conferir_categoria(session, categoria)
    if body.ativa:
        await _so_admin_se_vale_para_auto(session, user, (body.plataforma, body.canal))
    r = AtendimentoRegra(
        quando=body.quando,
        faca=body.faca,
        plataforma=body.plataforma,
        canal=body.canal,
        ativa=body.ativa,
        tipo=body.tipo,
        categoria=categoria,
        prioridade=body.prioridade,
        criado_por=user.id,
        atualizado_por=user.id,
    )
    if r.ativa:
        await _recusar_conflito(session, _dados_da_regra(r))
    session.add(r)
    await session.commit()
    await session.refresh(r)
    logger.info(
        "atendimento_regra_criada",
        regra_id=str(r.id),
        tipo=r.tipo,
        categoria=r.categoria,
        user_id=str(user.id),
    )
    return _regra_out(r)


@router.patch("/regras/{regra_id}", response_model=RegraOut)
async def editar_regra(
    regra_id: UUID,
    body: RegraPatch,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_edit)],
) -> RegraOut:
    """Muda a regra. Ativar ou mudá-la de lugar para cima de outra ativa → 409.

    Mexer só no texto/prioridade de uma regra que JÁ estava em conflito
    passa (o conflito não é novo, e é editando que se resolve); desativar
    passa sempre.
    """
    r = await session.get(AtendimentoRegra, regra_id)
    if r is None:
        raise HTTPException(404, detail={"code": "regra_nao_encontrada"})
    antes = (r.plataforma, r.canal) if r.ativa else None
    lugar_antes = (r.ativa, r.tipo, r.categoria, r.plataforma, r.canal)
    campos = body.model_fields_set
    if "quando" in campos:
        if not body.quando:
            raise HTTPException(422, detail={"code": "quando_vazio"})
        r.quando = body.quando
    if "faca" in campos:
        if not body.faca:
            raise HTTPException(422, detail={"code": "faca_vazio"})
        r.faca = body.faca
    if "plataforma" in campos:
        r.plataforma = body.plataforma
    if "canal" in campos:
        r.canal = body.canal
    if "ativa" in campos and body.ativa is not None:
        r.ativa = body.ativa
    if "tipo" in campos and body.tipo is not None:
        r.tipo = body.tipo
    if "categoria" in campos:
        r.categoria = body.categoria
    if "prioridade" in campos and body.prioridade is not None:
        r.prioridade = body.prioridade
    if r.tipo != TIPO_REGRA_CATEGORIA:
        r.categoria = None
    _validar_par(r.plataforma, r.canal)
    # Só confere o assunto que MUDOU: a regra cujo assunto saiu do manual
    # (desativado, ou o manual base reimportado sem ele) continua editável —
    # corrigir o texto ou desativá-la não pode voltar 422 por um assunto
    # que a pessoa nem tocou.
    if "categoria" in campos and r.categoria != lugar_antes[2]:
        await _conferir_categoria(session, r.categoria)
    # Vale onde ela valia (tirar/mudar a regra de uma loja em `auto`) e onde
    # passa a valer (levar uma regra para uma loja em `auto`).
    escopos = [e for e in (antes, (r.plataforma, r.canal) if r.ativa else None) if e]
    await _so_admin_se_vale_para_auto(session, user, *escopos)
    if r.ativa and (r.ativa, r.tipo, r.categoria, r.plataforma, r.canal) != lugar_antes:
        await _recusar_conflito(session, _dados_da_regra(r), ignorar_id=r.id)
    r.atualizado_por = user.id
    await session.commit()
    await session.refresh(r)
    return _regra_out(r, conflita_com=await _conflitos_atuais(session, r))


@router.delete("/regras/{regra_id}", status_code=status.HTTP_204_NO_CONTENT)
async def apagar_regra(
    regra_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_delete)],
) -> Response:
    r = await session.get(AtendimentoRegra, regra_id)
    if r is None:
        raise HTTPException(404, detail={"code": "regra_nao_encontrada"})
    if r.ativa:
        await _so_admin_se_vale_para_auto(session, user, (r.plataforma, r.canal))
    await session.delete(r)
    await session.commit()
    logger.info("atendimento_regra_apagada", regra_id=str(regra_id), user_id=str(user.id))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ── Respostas prontas (modelos) ───────────────────────────────────────────


def _modelo_out(m: AtendimentoModelo) -> ModeloOut:
    return ModeloOut(
        id=m.id,
        titulo=m.titulo,
        texto=m.texto,
        plataforma=m.plataforma,
        canal=m.canal,
        categoria=m.categoria,
        ativo=m.ativo,
        ordem=m.ordem,
    )


@router.get("/modelos", response_model=list[ModeloOut])
async def listar_modelos(
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[User, Depends(_view)],
    plataforma: Annotated[str | None, Query()] = None,
    canal: Annotated[str | None, Query()] = None,
) -> list[ModeloOut]:
    """Todas as respostas prontas; com plataforma/canal, as que valem ali (e as gerais)."""
    consulta = select(AtendimentoModelo)
    if plataforma:
        consulta = consulta.where(
            or_(AtendimentoModelo.plataforma.is_(None), AtendimentoModelo.plataforma == plataforma)
        )
    if canal:
        consulta = consulta.where(
            or_(AtendimentoModelo.canal.is_(None), AtendimentoModelo.canal == canal)
        )
    modelos = (
        (
            await session.execute(
                consulta.order_by(AtendimentoModelo.ordem, AtendimentoModelo.titulo)
            )
        )
        .scalars()
        .all()
    )
    return [_modelo_out(m) for m in modelos]


@router.post("/modelos", response_model=ModeloOut, status_code=status.HTTP_201_CREATED)
async def criar_modelo(
    body: ModeloIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_edit)],
) -> ModeloOut:
    _validar_par(body.plataforma, body.canal)
    await _conferir_categoria(session, body.categoria)
    m = AtendimentoModelo(
        titulo=body.titulo,
        texto=body.texto,
        plataforma=body.plataforma,
        canal=body.canal,
        categoria=body.categoria,
        ativo=body.ativo,
        ordem=body.ordem,
        criado_por=user.id,
    )
    session.add(m)
    await session.commit()
    await session.refresh(m)
    return _modelo_out(m)


@router.patch("/modelos/{modelo_id}", response_model=ModeloOut)
async def editar_modelo(
    modelo_id: UUID,
    body: ModeloPatch,
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[User, Depends(_edit)],
) -> ModeloOut:
    m = await session.get(AtendimentoModelo, modelo_id)
    if m is None:
        raise HTTPException(404, detail={"code": "modelo_nao_encontrado"})
    campos = body.model_fields_set
    if "titulo" in campos:
        if not body.titulo:
            raise HTTPException(422, detail={"code": "titulo_vazio"})
        m.titulo = body.titulo
    if "texto" in campos:
        if not body.texto:
            raise HTTPException(422, detail={"code": "texto_vazio"})
        m.texto = body.texto
    if "plataforma" in campos:
        m.plataforma = body.plataforma
    if "canal" in campos:
        m.canal = body.canal
    if "categoria" in campos and body.categoria != m.categoria:
        await _conferir_categoria(session, body.categoria)
        m.categoria = body.categoria
    if "ativo" in campos and body.ativo is not None:
        m.ativo = body.ativo
    if "ordem" in campos and body.ordem is not None:
        m.ordem = body.ordem
    _validar_par(m.plataforma, m.canal)
    await session.commit()
    await session.refresh(m)
    return _modelo_out(m)


@router.delete("/modelos/{modelo_id}", status_code=status.HTTP_204_NO_CONTENT)
async def apagar_modelo(
    modelo_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[User, Depends(_delete)],
) -> Response:
    m = await session.get(AtendimentoModelo, modelo_id)
    if m is None:
        raise HTTPException(404, detail={"code": "modelo_nao_encontrado"})
    await session.delete(m)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ── Sincronizar agora ─────────────────────────────────────────────────────


@router.post("/sincronizar", response_model=SincronizarOut)
async def sincronizar_agora(
    user: Annotated[User, Depends(_edit)],
) -> SincronizarOut:
    """Pede uma leitura já, sem esperar o próximo minuto ímpar.

    A trava de 60 s no Redis impede que clique repetido vire rajada na API
    das lojas (a trava por canal do sync segura o resto).
    """
    if not get_settings().atendimento_leitura_ativa:
        return SincronizarOut(enfileirado=False, motivo="leitura_desligada")
    try:
        pegou = await redis.set(CHAVE_SINCRONIZAR, "1", nx=True, ex=TRAVA_SINCRONIZAR_S)
    except Exception as e:  # noqa: BLE001
        logger.warning("atendimento_sincronizar_redis_falhou", err=type(e).__name__)
        raise HTTPException(503, detail={"code": "fila_indisponivel"}) from e
    if not pegou:
        return SincronizarOut(enfileirado=False, motivo="recente")
    try:
        await (await worker_pool.get_arq_pool()).enqueue_job("atendimento_sincronizar")
    except Exception as e:  # noqa: BLE001
        logger.warning("atendimento_sincronizar_fila_falhou", err=type(e).__name__)
        # Não enfileirou: solta a trava, senão o botão fica travado por nada.
        try:
            await redis.delete(CHAVE_SINCRONIZAR)
        except Exception:  # noqa: BLE001, S110 — o TTL solta sozinho
            pass
        raise HTTPException(503, detail={"code": "fila_indisponivel"}) from e
    logger.info("atendimento_sincronizar_enfileirado", user_id=str(user.id))
    return SincronizarOut(enfileirado=True)


# ── Métricas ──────────────────────────────────────────────────────────────


def _p90(valores: list[float]) -> float:
    """Percentil 90 pelo posto mais próximo (sem interpolar: é um tempo que aconteceu)."""
    ordenados = sorted(valores)
    return ordenados[max(0, ceil(0.9 * len(ordenados)) - 1)]


async def _metricas_lojas(
    session: AsyncSession, scope: TeamScope, desde: datetime, agora: datetime
) -> list[MetricaLojaOut]:
    """Primeira resposta por loja, em "turnos" do cliente.

    Um turno começa quando o cliente fala e a última palavra era da loja (ou
    a conversa começou), junta as mensagens seguidas dele, e termina na
    primeira resposta da loja que não falhou — pelo DaVinci ou por fora.
    Conta o turno que COMEÇOU no período. `pct_no_prazo` é sobre os turnos
    já decididos: respondidos, e os sem resposta cujo prazo já venceu (o
    que ainda está no prazo não é nem acerto nem erro).

    Mensagem automática não é resposta (05/10/2026) — a MESMA régua da fila
    "Falta responder" (`gravar.recalcular`): o robô e as campanhas do Duoke,
    a campanha que começa pelo usuário do comprador ("fulano já segue nossa
    loja"), a figurinha da campanha e os cartões da Shopee (pelo `payload`) e
    a que o DaVinci manda sozinho fora do /atendimento (senha da devolução,
    e-mail de logística da Amazon): `gravar.mensagem_automatica_sql`, no
    próprio SQL (nem chega ao Python). Medido em produção (7 dias): eram automáticas
    1.921 das 2.648 primeiras respostas da Shopee, e a mediana de 1 min
    escondia a de quem responde de verdade (com a régua inteira: Shopee
    ~297 min e 48,6% no prazo; TikTok ~753 min e 88,8%).
    """
    filtro = [AtendimentoConversa.ultima_mensagem_em >= desde]
    cond = _clausula_escopo(scope, AtendimentoConversa.integration_id)
    if cond is not None:
        filtro.append(cond)
    consulta_conversas = select(
        AtendimentoConversa.id,
        AtendimentoConversa.integration_id,
        AtendimentoConversa.canal_id,
        AtendimentoConversa.plataforma,
        AtendimentoConversa.canal,
        AtendimentoConversa.conta,
        AtendimentoConversa.sem_resposta_necessaria,
    ).where(*filtro)
    conversas = {row.id: row for row in (await session.execute(consulta_conversas)).all()}
    if not conversas:
        return []

    momento = func.coalesce(AtendimentoMensagem.enviada_em, AtendimentoMensagem.created_at)
    linhas = (
        await session.execute(
            select(AtendimentoMensagem.conversa_id, AtendimentoMensagem.autor, momento)
            .where(
                # SUBCONSULTA, não a lista de ids: o asyncpg aceita no máximo
                # 32.767 parâmetros, e 90 dias de todas as lojas passam disso.
                AtendimentoMensagem.conversa_id.in_(
                    select(AtendimentoConversa.id).where(*filtro)
                ),
                momento >= desde - _MARGEM_METRICAS,
                or_(
                    AtendimentoMensagem.autor == AUTOR_CLIENTE,
                    and_(
                        AtendimentoMensagem.autor == AUTOR_LOJA,
                        AtendimentoMensagem.status != MSG_FALHOU,
                        ~gravar.mensagem_automatica_sql(
                            AtendimentoMensagem.texto, AtendimentoMensagem.payload
                        ),
                    ),
                ),
            )
            .order_by(AtendimentoMensagem.conversa_id, momento, AtendimentoMensagem.created_at)
        )
    ).all()

    por_conversa: dict[UUID, list[tuple[str, datetime]]] = {}
    for conversa_id, autor, quando in linhas:
        por_conversa.setdefault(conversa_id, []).append((autor, _utc(quando)))

    lojas: dict[tuple[UUID | None, str, UUID | None], dict[str, Any]] = {}
    for conversa_id, msgs in por_conversa.items():
        info = conversas.get(conversa_id)
        if info is None:  # entrou no período entre as duas consultas
            continue
        prazo = timedelta(hours=sla_horas(info.plataforma, info.canal))
        # A loja do robô (sem integração) é o canal: uma linha por loja Temu.
        robo_canal = (
            info.canal_id
            if info.integration_id is None and info.plataforma in PLATAFORMAS_ROBO
            else None
        )
        loja = lojas.setdefault(
            (info.integration_id, info.plataforma, robo_canal),
            {"conta": info.conta, "recebidas": 0, "tempos": [], "no_prazo": 0, "vencidas": 0},
        )
        loja["conta"] = loja["conta"] or info.conta
        inicio: datetime | None = None
        for autor, quando in msgs:
            if autor == AUTOR_CLIENTE:
                inicio = inicio or quando
                continue
            if inicio is not None:
                if inicio >= desde:
                    loja["recebidas"] += 1
                    tempo = quando - inicio
                    loja["tempos"].append(tempo.total_seconds() / 60)
                    loja["no_prazo"] += int(tempo <= prazo)
                inicio = None
        if inicio is not None and inicio >= desde and not info.sem_resposta_necessaria:
            loja["recebidas"] += 1
            loja["vencidas"] += int(agora - inicio > prazo)

    # O nome de HOJE da loja (P2), não o retrato gravado na conversa.
    nomes = await _nomes_das_lojas(session, {integration_id for integration_id, _, _ in lojas})
    saida: list[MetricaLojaOut] = []
    for (integration_id, plataforma, _robo_canal), loja in lojas.items():
        tempos: list[float] = loja["tempos"]
        decididas = len(tempos) + loja["vencidas"]
        saida.append(
            MetricaLojaOut(
                integration_id=integration_id,
                conta=nomes.get(integration_id) or loja["conta"],
                plataforma=plataforma,
                recebidas=loja["recebidas"],
                respondidas=len(tempos),
                mediana_primeira_resposta_min=(
                    round(statistics.median(tempos), 1) if tempos else None
                ),
                p90_primeira_resposta_min=round(_p90(tempos), 1) if tempos else None,
                pct_no_prazo=(
                    round(100 * loja["no_prazo"] / decididas, 1) if decididas else None
                ),
            )
        )
    return sorted(saida, key=lambda m: (m.plataforma, (m.conta or "").lower()))


async def _metricas_ia(session: AsyncSession, scope: TeamScope, desde: datetime) -> MetricaIaOut:
    cond = _clausula_escopo(scope, AtendimentoConversa.integration_id)
    q_rascunhos = (
        select(func.count())
        .select_from(AtendimentoRascunho)
        .join(AtendimentoConversa, AtendimentoConversa.id == AtendimentoRascunho.conversa_id)
        .where(AtendimentoRascunho.created_at >= desde)
    )
    q_acoes = (
        select(AtendimentoAvaliacao.acao, func.count())
        .join(AtendimentoRascunho, AtendimentoRascunho.id == AtendimentoAvaliacao.rascunho_id)
        .join(AtendimentoConversa, AtendimentoConversa.id == AtendimentoRascunho.conversa_id)
        .where(AtendimentoAvaliacao.created_at >= desde)
        .group_by(AtendimentoAvaliacao.acao)
    )
    # A nota vem DEPOIS da ação (a pessoa avalia horas depois): conta pela
    # última mudança da avaliação, não por quando ela nasceu.
    q_notas = (
        select(AtendimentoAvaliacao.nota, func.count())
        .join(AtendimentoRascunho, AtendimentoRascunho.id == AtendimentoAvaliacao.rascunho_id)
        .join(AtendimentoConversa, AtendimentoConversa.id == AtendimentoRascunho.conversa_id)
        .where(AtendimentoAvaliacao.updated_at >= desde, AtendimentoAvaliacao.nota.is_not(None))
        .group_by(AtendimentoAvaliacao.nota)
    )
    if cond is not None:
        q_rascunhos = q_rascunhos.where(cond)
        q_acoes = q_acoes.where(cond)
        q_notas = q_notas.where(cond)
    rascunhos = int(await session.scalar(q_rascunhos) or 0)
    acoes = {acao: int(n) for acao, n in (await session.execute(q_acoes)).all()}
    notas = {nota: int(n) for nota, n in (await session.execute(q_notas)).all()}
    return MetricaIaOut(
        rascunhos=rascunhos,
        enviou_igual=acoes.get("enviou_igual", 0),
        editou=acoes.get("editou", 0),
        descartou=acoes.get("descartou", 0),
        escreveu_do_zero=acoes.get("escreveu_do_zero", 0),
        nota_ok=notas.get("ok", 0),
        nota_erro=notas.get("erro", 0),
    )


@router.get("/metricas", response_model=MetricasOut)
async def metricas(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_view)],
    dias: Annotated[int, Query(ge=1, le=90)] = 7,
) -> MetricasOut:
    """Tempo de primeira resposta e % no prazo por loja; o que a pessoa fez com a IA."""
    scope = await resolve_team_scope(session, user)
    agora = datetime.now(UTC)
    desde = agora - timedelta(days=dias)
    return MetricasOut(
        dias=dias,
        lojas=await _metricas_lojas(session, scope, desde, agora),
        ia=await _metricas_ia(session, scope, desde),
    )
