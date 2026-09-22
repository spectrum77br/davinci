"""Ouvidoria › Robôs — o núcleo: catálogo, rodada, ocorrência e aviso.

Vinicius, 21/09/2026: uma seção só pra enxergar o que os robôs da casa estão
fazendo e o que eles encontraram e ninguém tratou. Este módulo é o contrato
que TODO robô usa; a lógica de cada robô (o que ele procura) fica no serviço
dele (ex.: `vigia_importacao.py`), que só faz:

    async with Rodada(session, "vigia_importacao") as r:
        ...
        r.contadores["pedidos"] += 1
        await r.registrar(chave="tiktok:586…", titulo="Pago 14:08 e não caiu no Bling", …)
        ...
        await r.fechar_nao_vistas(excluir_contas={"TikTok injox"})
        r.resumo = "230 pedidos conferidos · 1 nova · 1 conta falhou"
    await avisar_pendentes(session, "vigia_importacao")

## As três regras que importam

1. **Idempotência é a `chave`.** Só existe UMA ocorrência aberta por (robô,
   chave) — índice único parcial no banco. Rodada seguinte que vê o mesmo
   problema só carimba `ultima_vista_em`; rodada que NÃO vê fecha como
   `sumiu`. É assim que "fechou sozinha" acontece sem o robô saber contar.
2. **Gente manda mais que robô.** `ignorada` por uma pessoa não reabre nunca;
   `tratada` há menos de 24 h também não (dá tempo do Bling/plataforma
   refletir o que a pessoa fez). Passou disso e o problema voltou → linha
   nova, o histórico fica. Ocorrência que a rodada NÃO re-vê (a que um hook
   abre no ponto da falha) é fechada pelo ponto de sucesso, com
   `fechar_por_chave`, e fica de fora do `fechar_nao_vistas` pelo `prefixo`.
3. **Aviso é por robô, não por ocorrência.** Uma mensagem no Threema com
   tudo que está pendente daquele robô; re-aviso a cada `reaviso_horas`
   enquanto persistir. Falha no envio não carimba — retenta no próximo tick.
   Modo `silencioso` registra e cala; `desligado` nem roda.

Quem chama commita (os robôs rodam dentro de `session_scope`). A exceção é
`Rodada.__aexit__`, que commita sozinho pra rodada com erro não se perder no
rollback do `session_scope`.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from types import TracebackType
from uuid import UUID
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import (
    FECHADA_PELO_ROBO,
    MODOS,
    OuvidoriaOcorrencia,
    OuvidoriaRobo,
    OuvidoriaRodada,
)
from app.services import threema

logger = structlog.get_logger()

_TZ_BR = ZoneInfo("America/Sao_Paulo")

# Marcada `tratada` há menos que isso e o robô viu de novo → não reabre
# (a pessoa acabou de agir; o espelho/plataforma ainda vai refletir).
CARENCIA_TRATADA = timedelta(hours=24)
# Linhas de ocorrência numa mensagem Threema — o resto vira "… e mais N".
MAX_LINHAS_AVISO = 15
# Sem rodada há mais que isto × cadência = "parado" na coluna Saúde.
PARADO_MULTIPLO = 3
CADENCIA_PADRAO_MIN = 60


class OuvidoriaError(Exception):
    """Erro de regra com código estável (o router traduz em 404/409/422).
    `message`, quando vem, é a frase em linguagem de operação pra tela."""

    def __init__(self, code: str, message: str | None = None) -> None:
        self.code = code
        self.message = message
        super().__init__(message or code)


# ─── catálogo ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Parametro:
    """Uma chave numérica da config do robô, com rótulo e limites — o PATCH
    recusa fora deles e a leitura (`config_do_robo`) aperta pra dentro. Sem
    isso um dedo a mais na tela (720 h em vez de 72) faria o robô listar um
    mês de pedidos a cada rodada, e um texto no lugar do número derrubaria
    a tela e todas as rodadas."""

    rotulo: str
    minimo: int
    maximo: int
    unidade: str = ""


@dataclass(frozen=True)
class RoboDef:
    chave: str
    nome: str
    descricao: str
    area: str
    cadencia_texto: str
    plataformas: tuple[str, ...]
    config_padrao: dict
    # Nome do campo em Settings com os Threema IDs do robô (fallback quando a
    # tela não tem override). None = só o OUVIDORIA_THREEMA_RECIPIENTS geral.
    env_threema_recipients: str | None
    # Assunto do robô, pra o aviso sair pelo REMETENTE certo quando as
    # conversas estão separadas (services/threema.py). A Ouvidoria é o lugar
    # central dos avisos dos robôs: cada robô novo declara o seu aqui e não
    # precisa mexer em mais nada. Vazio = remetente geral.
    contexto: str = ""
    # Limites das chaves numéricas da config (chave → Parametro).
    parametros: dict[str, Parametro] = field(default_factory=dict)
    # Modo com que a linha NASCE em ouvidoria_robos (só no INSERT: o
    # `sincronizar_catalogo` nunca mexe no modo de quem já existe). Robô novo
    # entra `silencioso` — registra no painel e não manda Threema — até o
    # Vinicius ligar na tela; quem já era da casa continua `ligado`.
    modo_padrao: str = "ligado"


ROBOS: dict[str, RoboDef] = {
    "vigia_importacao": RoboDef(
        chave="vigia_importacao",
        nome="Vigia de importação",
        descricao=(
            "Pedido pago no marketplace que não caiu no Bling. Confere no Bling ao "
            "vivo antes de abrir ocorrência; conta cuja API falhou fica de fora da "
            "rodada (quem cobra a credencial é o Vigia de credenciais)."
        ),
        area="pedidos",
        cadencia_texto="a cada 1 h (:09)",
        plataformas=("ml", "shopee", "tiktok", "amazon"),
        config_padrao={
            "tolerancia_min": 90,
            "janela_horas": 72,
            "cadencia_min": 60,
            "amazon_a_cada_rodadas": 3,
        },
        env_threema_recipients="vigia_importacao_threema_recipients",
        contexto="importacao",
        parametros={
            "tolerancia_min": Parametro("Tolerância", 5, 24 * 60, "min"),
            "janela_horas": Parametro("Janela", 1, 7 * 24, "h"),
            "cadencia_min": Parametro("Cadência esperada", 1, 24 * 60, "min"),
            "amazon_a_cada_rodadas": Parametro("Amazon a cada N rodadas", 1, 48),
        },
    ),
    # ── Os 6 robôs aprovados em 22/09/2026 ────────────────────────────────
    # Todos nascem `silencioso`: registram no painel e não mandam Threema até
    # o Vinicius ligar cada um (o aviso de um robô novo costuma chegar em
    # dezena na primeira semana, enquanto a operação limpa o passivo).
    "vigia_credenciais": RoboDef(
        chave="vigia_credenciais",
        nome="Vigia de credenciais",
        descricao=(
            "Conta de marketplace ou do Bling que perdeu o acesso à API (token "
            "vencido, chave do app expirada, 403 de escopo) — enquanto isso nenhum "
            "robô enxerga aquela loja. Avisa também quando a autorização está perto "
            "de vencer."
        ),
        area="contas",
        cadencia_texto="a cada 1 h (:21)",
        plataformas=("ml", "shopee", "tiktok", "amazon", "magalu", "bling"),
        config_padrao={"cadencia_min": 60, "vencimento_dias": 7},
        env_threema_recipients=None,
        parametros={
            "cadencia_min": Parametro("Cadência esperada", 1, 24 * 60, "min"),
            "vencimento_dias": Parametro("Aviso de vencimento", 1, 60, "dias"),
        },
        modo_padrao="silencioso",
    ),
    "vigia_ingest_bling": RoboDef(
        chave="vigia_ingest_bling",
        nome="Pedido do Bling que não entra",
        descricao=(
            "Pedido cujo webhook do Bling falhou em todas as tentativas e que "
            "continua fora do DaVinci — ninguém vê na Margem, na NF nem na "
            "Logística. Fecha sozinho quando o pedido entra."
        ),
        area="pedidos",
        cadencia_texto="a cada 15 min (:03/:18/:33/:48)",
        plataformas=("bling",),
        config_padrao={"cadencia_min": 15, "idade_min": 30},
        env_threema_recipients=None,
        parametros={
            "cadencia_min": Parametro("Cadência esperada", 1, 24 * 60, "min"),
            "idade_min": Parametro("Idade mínima", 5, 24 * 60, "min"),
        },
        modo_padrao="silencioso",
    ),
    "vigia_correios": RoboDef(
        chave="vigia_correios",
        nome="Ocorrência grave nos Correios",
        descricao=(
            "Apreensão fiscal, extravio, roubo, avaria ou devolução ao remetente "
            "que o rastreio da Logística leu; 17track sem saldo (nada mais "
            "atualiza); rastreio recusado. Fecha sozinho quando o pedido chega a "
            "Entregue/Resolvido/Cancelado/Perdimento."
        ),
        area="logistica",
        cadencia_texto="a cada 15 min (:07/:22/:37/:52)",
        plataformas=("ml", "shopee", "tiktok", "amazon"),
        config_padrao={"cadencia_min": 15},
        env_threema_recipients=None,
        parametros={"cadencia_min": Parametro("Cadência esperada", 1, 24 * 60, "min")},
        modo_padrao="silencioso",
    ),
    "vigia_marketing_comandos": RoboDef(
        chave="vigia_marketing_comandos",
        nome="Comandos de Ads não aplicados",
        descricao=(
            "Pausar/retomar anúncios, orçamento ou Oferta Relâmpago que não foi "
            "aplicado (o executor do Mac falhou, ficou pendente ou está sem "
            "sinal) — enquanto isso a agenda da Shopee não acontece."
        ),
        area="marketing",
        cadencia_texto="a cada 10 min (:06/:16/:26/:36/:46/:56)",
        # A fila tem comando de Ads do ML também (executor "api", drenado pelo
        # worker do agente), e a ocorrência dele nasce com plataforma "ml".
        plataformas=("shopee", "ml"),
        config_padrao={"cadencia_min": 10, "pendente_min": 30, "executor_offline_min": 10},
        env_threema_recipients=None,
        parametros={
            "cadencia_min": Parametro("Cadência esperada", 1, 24 * 60, "min"),
            "pendente_min": Parametro("Pendente há mais de", 5, 24 * 60, "min"),
            "executor_offline_min": Parametro("Executor sem sinal há", 2, 240, "min"),
        },
        modo_padrao="silencioso",
    ),
    "vigia_margem": RoboDef(
        chave="vigia_margem",
        nome="Robô da Margem",
        descricao=(
            "Pedido que o robô segurou no Bling e ninguém decidiu, falha do robô "
            "ao segurar/liberar e margem fora do normal (custo suspeito no "
            "cadastro do produto)."
        ),
        area="margem",
        cadencia_texto="a cada 30 min (:17/:47)",
        plataformas=("ml", "shopee", "tiktok", "amazon"),
        config_padrao={"cadencia_min": 30, "segurado_horas": 24},
        env_threema_recipients=None,
        parametros={
            "cadencia_min": Parametro("Cadência esperada", 1, 24 * 60, "min"),
            "segurado_horas": Parametro("Segurado sem decisão", 1, 720, "h"),
        },
        modo_padrao="silencioso",
    ),
    "vigia_chamados": RoboDef(
        chave="vigia_chamados",
        nome="Chamados: réplica e monitoramento",
        descricao=(
            "Réplica ou abertura de chamado que não foi pra plataforma, caso que a "
            "consulta não consegue mais ler (o status da aba fica defasado) e "
            "chamado Encerrado esperando alguém concluir pelo Resolver."
        ),
        area="chamados",
        cadencia_texto="a cada 30 min (:27/:57)",
        plataformas=("ml", "shopee", "tiktok"),
        config_padrao={
            "cadencia_min": 30,
            "encerrado_dias": 3,
            "consultas_falhas_seguidas": 3,
        },
        env_threema_recipients=None,
        parametros={
            "cadencia_min": Parametro("Cadência esperada", 1, 24 * 60, "min"),
            "encerrado_dias": Parametro("Encerrado há mais de", 1, 60, "dias"),
            "consultas_falhas_seguidas": Parametro("Consultas falhas seguidas", 1, 20),
        },
        modo_padrao="silencioso",
    ),
}

# Threema ID: 8 caracteres (A-Z, 0-9); ID de gateway começa com "*".
_THREEMA_ID = re.compile(r"^(?:[A-Z0-9]{8}|\*[A-Z0-9]{7})$")
MAX_DESTINATARIOS = 20


def validar_config(chave: str, config: dict) -> dict:
    """Config que veio do PATCH, normalizada: as chaves com `Parametro`
    viram int e precisam estar nos limites; o resto passa como veio. Fora
    disso levanta `config_invalida` com a frase pra tela ("Tolerância precisa
    ser entre 5 e 1440 min")."""
    d = ROBOS.get(chave)
    parametros = d.parametros if d else {}
    out = dict(config or {})
    for k, p in parametros.items():
        if k not in out:
            continue
        try:
            v = int(out[k])
            if isinstance(out[k], bool) or (isinstance(out[k], float) and out[k] != v):
                raise ValueError
        except (TypeError, ValueError):
            raise OuvidoriaError(
                "config_invalida", f"{p.rotulo} precisa ser um número inteiro"
            ) from None
        if not p.minimo <= v <= p.maximo:
            unidade = f" {p.unidade}" if p.unidade else ""
            raise OuvidoriaError(
                "config_invalida",
                f"{p.rotulo} precisa ser entre {p.minimo} e {p.maximo}{unidade}",
            )
        out[k] = v
    return out


def resolver_destinatarios(
    raw: str | None, diretorio: list[dict[str, str]]
) -> str | None:
    """Campo Threema da tela → texto limpo de IDs, aceitando NOME ou ID.

    A tela mostra pessoas pelo nome (Admin › Usuários / `.env`), então quem
    digita "cairo sa" está apontando pra uma pessoa, não pra um código —
    casamos pelo nome (sem acento/maiúsculas, espaços colapsados) contra o
    diretório de `threema.diretorio`. O que não é ID nem nome conhecido vira
    erro com a frase que explica onde cadastrar. Depois passa pela validação
    de formato/teto de `validar_destinatarios`."""
    if raw is None:
        return None
    por_nome = {_chave_nome(d["nome"]): d["id"] for d in diretorio if d.get("nome")}
    ids: list[str] = []
    for parte in raw.replace(";", ",").split(","):
        parte = parte.strip()
        if not parte:
            continue
        if _THREEMA_ID.match(parte.upper()):
            ids.append(parte.upper())
            continue
        rid = por_nome.get(_chave_nome(parte))
        if rid is None:
            # "ABCDEFGH IJKLMNOP" (IDs separados por espaço, formato antigo do
            # campo) — só quando TODOS os pedaços são IDs; senão é nome.
            pedacos = [t.upper() for t in parte.split()]
            if len(pedacos) > 1 and all(_THREEMA_ID.match(t) for t in pedacos):
                ids.extend(pedacos)
                continue
            raise OuvidoriaError(
                "destinatarios_invalidos",
                f"Não conheço \"{parte}\" — cadastre o Threema dessa pessoa em "
                "Admin › Usuários (ou informe o ID de 8 letras/números)",
            )
        ids.append(rid)
    return validar_destinatarios(", ".join(ids))


def _chave_nome(nome: str) -> str:
    """Nome normalizado pra casar o que a pessoa digitou com o cadastro."""
    base = unicodedata.normalize("NFKD", nome or "")
    sem_acento = "".join(ch for ch in base if not unicodedata.combining(ch))
    return " ".join(sem_acento.lower().split())


def validar_destinatarios(raw: str | None) -> str | None:
    """Override de Threema IDs vindo da tela → texto limpo ("A, B") ou None
    quando vazio. ID fora do formato ou mais de MAX_DESTINATARIOS → erro com
    frase pra tela (cada ID vira um POST no gateway dentro da rodada)."""
    ids = threema.parse_recipients(raw)
    if not ids:
        return None
    for rid in ids:
        if not _THREEMA_ID.match(rid):
            raise OuvidoriaError(
                "destinatarios_invalidos",
                f"Threema ID inválido: {rid} (são 8 letras/números, ex.: ABCDEFGH)",
            )
    if len(ids) > MAX_DESTINATARIOS:
        raise OuvidoriaError(
            "destinatarios_invalidos", f"No máximo {MAX_DESTINATARIOS} destinatários"
        )
    return ", ".join(dict.fromkeys(ids))


def _agora(agora: datetime | None) -> datetime:
    return agora or datetime.now(UTC)


async def sincronizar_catalogo(session: AsyncSession) -> None:
    """Upsert de `ROBOS` em ouvidoria_robos. Escreve o que é do código (nome,
    descrição, área, cadência, plataformas); NÃO mexe em modo, destinatários
    nem config já salvos — só completa chaves de config que o código ganhou
    depois (um robô que passa a ter `amazon_a_cada_rodadas` não pode quebrar
    porque a linha antiga não tinha a chave). O `modo_padrao` do catálogo vale
    só no INSERT: robô novo nasce silencioso, e quem a pessoa já ligou (ou
    desligou) na tela fica como está. Commit fica com o caller."""
    existentes = {
        r.chave: r
        for r in (await session.execute(select(OuvidoriaRobo))).scalars().all()
    }
    for d in ROBOS.values():
        row = existentes.get(d.chave)
        if row is None:
            session.add(
                OuvidoriaRobo(
                    chave=d.chave,
                    nome=d.nome,
                    descricao=d.descricao,
                    area=d.area,
                    cadencia_texto=d.cadencia_texto,
                    plataformas=list(d.plataformas),
                    modo=d.modo_padrao if d.modo_padrao in MODOS else "ligado",
                    config=dict(d.config_padrao),
                )
            )
            continue
        row.nome = d.nome
        row.descricao = d.descricao
        row.area = d.area
        row.cadencia_texto = d.cadencia_texto
        row.plataformas = list(d.plataformas)
        faltando = {k: v for k, v in d.config_padrao.items() if k not in (row.config or {})}
        if faltando:
            row.config = {**(row.config or {}), **faltando}
    await session.flush()


async def modo(session: AsyncSession, chave: str) -> str:
    """'ligado' | 'silencioso' | 'desligado'. Linha ainda não existe (worker
    subiu antes da migração/catálogo) → 'ligado', o comportamento de sempre."""
    m = (
        await session.execute(select(OuvidoriaRobo.modo).where(OuvidoriaRobo.chave == chave))
    ).scalar_one_or_none()
    return m if m in MODOS else "ligado"


async def ativo(session: AsyncSession, chave: str) -> bool:
    """O robô pode registrar agora? (modo != `desligado`). É o que os HOOKS
    olham antes de abrir ocorrência no meio de outra operação (o envio da
    réplica, o hold da Margem): `silencioso` registra e cala — o aviso é
    decidido no `avisar_pendentes` —, `desligado` não grava nada."""
    return await modo(session, chave) != "desligado"


def config_do_robo(robo: OuvidoriaRobo | None, chave: str) -> dict:
    """Config efetiva: o que está salvo por cima do padrão do catálogo. As
    chaves com `Parametro` saem SEMPRE como int dentro dos limites — valor
    que não converte cai no padrão, fora da faixa é apertado pra dentro. É a
    defesa em profundidade do `validar_config`: uma linha antiga ou gravada
    por fora nunca derruba a tela nem a rodada."""
    d = ROBOS.get(chave)
    padrao = d.config_padrao if d else {}
    out = {**padrao, **((robo.config if robo else None) or {})}
    for k, p in (d.parametros if d else {}).items():
        try:
            v = int(out.get(k))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            v = int(padrao.get(k, p.minimo))
        out[k] = min(max(v, p.minimo), p.maximo)
    return out


def rotulos_config(chave: str) -> dict[str, str]:
    """chave da config → rótulo com a unidade ("cadencia_min" → "Cadência
    esperada (min)"), pra tela não ter que conhecer robô por robô: são os
    mesmos `Parametro` que já governam os limites. Chave sem `Parametro` fica
    fora e a tela mostra a chave crua — nada some por falta de rótulo."""
    d = ROBOS.get(chave)
    return {
        k: f"{p.rotulo} ({p.unidade})" if p.unidade else p.rotulo
        for k, p in (d.parametros if d else {}).items()
    }


# ─── ocorrências ───────────────────────────────────────────────────────────


async def _aberta(
    session: AsyncSession, robo_chave: str, chave: str
) -> OuvidoriaOcorrencia | None:
    return (
        await session.execute(
            select(OuvidoriaOcorrencia).where(
                OuvidoriaOcorrencia.robo_chave == robo_chave,
                OuvidoriaOcorrencia.chave == chave,
                OuvidoriaOcorrencia.fechada_em.is_(None),
            )
        )
    ).scalar_one_or_none()


async def _ultima_fechada(
    session: AsyncSession, robo_chave: str, chave: str
) -> OuvidoriaOcorrencia | None:
    return (
        await session.execute(
            select(OuvidoriaOcorrencia)
            .where(
                OuvidoriaOcorrencia.robo_chave == robo_chave,
                OuvidoriaOcorrencia.chave == chave,
                OuvidoriaOcorrencia.fechada_em.is_not(None),
            )
            .order_by(OuvidoriaOcorrencia.fechada_em.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def registrar(
    session: AsyncSession,
    robo_chave: str,
    chave: str,
    *,
    titulo: str,
    plataforma: str | None = None,
    conta: str | None = None,
    pedido: str | None = None,
    detalhe: str | None = None,
    acao: str | None = None,
    link: str | None = None,
    severidade: str = "pessoa",
    precisa_pessoa: bool = True,
    dados: dict | None = None,
    agora: datetime | None = None,
) -> OuvidoriaOcorrencia:
    """O robô viu um problema. Aberta existe → carimba `ultima_vista_em` (e
    atualiza texto/dados se mudaram — o valor do pedido pode ter chegado
    depois). Não existe → cria, salvo quando a última fechada foi `ignorada`
    (nunca reabre) ou `tratada` há menos de CARENCIA_TRATADA — nesses dois
    casos devolve a fechada e não grava nada. Flush, sem commit."""
    agora = _agora(agora)
    dados = dados or {}
    row = await _aberta(session, robo_chave, chave)
    if row is not None:
        row.ultima_vista_em = agora
        # O robô manda a foto completa a cada rodada (título, detalhe, ação,
        # link, gravidade) e ela substitui a anterior — o valor do pedido pode
        # ter chegado depois. Só grava o que MUDOU, pra o updated_at não
        # mentir a cada rodada. Plataforma/conta/pedido identificam a linha:
        # só trocam quando vêm preenchidos.
        for campo, novo in (
            ("titulo", titulo[:200]),
            ("detalhe", detalhe),
            ("acao", acao),
            ("link", link),
            ("severidade", severidade),
            ("precisa_pessoa", precisa_pessoa),
        ):
            if getattr(row, campo) != novo:
                setattr(row, campo, novo)
        for campo, novo in (("plataforma", plataforma), ("conta", conta), ("pedido", pedido)):
            if novo is not None and getattr(row, campo) != novo:
                setattr(row, campo, novo)
        # `dados` mescla (o que a rodada não mandou não some).
        if dados and row.dados != {**(row.dados or {}), **dados}:
            row.dados = {**(row.dados or {}), **dados}
        await session.flush()
        return row

    fechada = await _ultima_fechada(session, robo_chave, chave)
    if fechada is not None:
        if fechada.fechamento == "ignorada":
            return fechada
        if (
            fechada.fechamento == "tratada"
            and fechada.fechada_em is not None
            and agora - fechada.fechada_em < CARENCIA_TRATADA
        ):
            return fechada

    row = OuvidoriaOcorrencia(
        robo_chave=robo_chave,
        chave=chave,
        plataforma=plataforma,
        conta=conta,
        pedido=pedido,
        titulo=titulo[:200],
        detalhe=detalhe,
        acao=acao,
        link=link,
        severidade=severidade,
        precisa_pessoa=precisa_pessoa,
        dados=dados,
        aberta_em=agora,
        ultima_vista_em=agora,
    )
    session.add(row)
    await session.flush()
    return row


async def fechar_por_chave(
    session: AsyncSession,
    robo_chave: str,
    chave: str,
    *,
    fechamento: str = "sumiu",
    por: str = FECHADA_PELO_ROBO,
    agora: datetime | None = None,
) -> bool:
    """Fecha a aberta desta (robô, chave). É o par do `registrar` standalone
    pras ocorrências de EVENTO — as que um HOOK abre no meio de outra
    operação e que a rodada não re-vê: quem fecha é o ponto de SUCESSO da
    mesma operação (a réplica foi, o hold deu certo), não a passagem do
    tempo. Sem aberta devolve False (nada a fazer). Flush, sem commit."""
    row = await _aberta(session, robo_chave, chave)
    if row is None:
        return False
    row.fechada_em = _agora(agora)
    row.fechamento = fechamento
    row.fechada_por = ((por or "").strip() or FECHADA_PELO_ROBO)[:120]
    await session.flush()
    return True


async def fechar_nao_vistas(
    session: AsyncSession,
    robo_chave: str,
    vistas: Iterable[str],
    *,
    excluir_contas: Iterable[str] = (),
    prefixo: str | None = None,
    agora: datetime | None = None,
) -> int:
    """Fecha como `sumiu` toda aberta do robô cuja chave NÃO foi vista nesta
    rodada. Ocorrência de conta que falhou (em `excluir_contas`) fica: o robô
    não conseguiu olhar aquela conta, então "não vi" não quer dizer "sumiu".

    `prefixo` limita o julgamento às chaves que começam com ele (ex.:
    `segurado:`): robô que mistura ocorrência de ESTADO (a rodada re-vê) com
    ocorrência de EVENTO (hook abre, `fechar_por_chave` fecha) chamaria o
    fechamento em cima das de evento e mataria como "sumiu" justamente a
    falha que ninguém tratou ainda. Devolve quantas fechou."""
    agora = _agora(agora)
    vistas = set(vistas)
    excluir = {c for c in excluir_contas if c}
    abertas = (
        (
            await session.execute(
                select(OuvidoriaOcorrencia).where(
                    OuvidoriaOcorrencia.robo_chave == robo_chave,
                    OuvidoriaOcorrencia.fechada_em.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    n = 0
    for o in abertas:
        if prefixo is not None and not o.chave.startswith(prefixo):
            continue
        if o.chave in vistas or (o.conta and o.conta in excluir):
            continue
        o.fechada_em = agora
        o.fechamento = "sumiu"
        o.fechada_por = FECHADA_PELO_ROBO
        n += 1
    await session.flush()
    return n


async def _get_ocorrencia(session: AsyncSession, ocorrencia_id: UUID) -> OuvidoriaOcorrencia:
    row = await session.get(OuvidoriaOcorrencia, ocorrencia_id)
    if row is None:
        raise OuvidoriaError("ocorrencia_nao_encontrada")
    return row


async def tratar(
    session: AsyncSession,
    ocorrencia_id: UUID,
    *,
    usuario: str,
    fechamento: str,
    agora: datetime | None = None,
) -> OuvidoriaOcorrencia:
    """Uma pessoa fecha a ocorrência como `tratada` ou `ignorada`. Já fechada
    → `ocorrencia_ja_fechada` (o botão da tela some, mas duas abas abertas
    existem)."""
    if fechamento not in ("tratada", "ignorada"):
        raise OuvidoriaError("fechamento_invalido")
    row = await _get_ocorrencia(session, ocorrencia_id)
    if row.fechada_em is not None:
        raise OuvidoriaError("ocorrencia_ja_fechada")
    row.fechada_em = _agora(agora)
    row.fechamento = fechamento
    row.fechada_por = (usuario or "").strip()[:120] or "usuário"
    await session.flush()
    return row


async def reabrir(
    session: AsyncSession,
    ocorrencia_id: UUID,
    *,
    usuario: str,
    agora: datetime | None = None,
) -> OuvidoriaOcorrencia:
    """Desfaz um Tratado/Ignorar. Se enquanto isso o robô já abriu OUTRA linha
    da mesma chave, reabrir esta violaria o índice único → `ocorrencia_duplicada`
    (a pessoa vai achar a nova na lista). Quem reabriu fica em `dados`."""
    row = await _get_ocorrencia(session, ocorrencia_id)
    if row.fechada_em is None:
        raise OuvidoriaError("ocorrencia_aberta")
    if await _aberta(session, row.robo_chave, row.chave) is not None:
        raise OuvidoriaError("ocorrencia_duplicada")
    agora = _agora(agora)
    row.fechada_em = None
    row.fechamento = None
    row.fechada_por = None
    row.ultima_vista_em = agora
    row.dados = {
        **(row.dados or {}),
        "reaberta_por": (usuario or "").strip()[:120] or "usuário",
        "reaberta_em": agora.isoformat(),
    }
    await session.flush()
    return row


# ─── rodada ────────────────────────────────────────────────────────────────


class Rodada:
    """Uma execução do robô, como context manager assíncrono.

    Ao sair grava `ouvidoria_rodadas` e os campos `ultima_*` do robô, e
    COMMITA — inclusive quando o corpo levantou: aí a sessão é revertida
    primeiro (o que a rodada quebrada tinha feito se perde, que é o correto)
    e a rodada é gravada com `ok=False` + erro, e a exceção sobe do mesmo
    jeito. Sem esse commit próprio, o `session_scope` do sweep faria
    rollback e a falha sumiria do painel — justamente a rodada que a coluna
    Saúde precisa ver.
    """

    def __init__(self, session: AsyncSession, robo_chave: str) -> None:
        self.session = session
        self.robo_chave = robo_chave
        self.contadores: Counter[str] = Counter()
        self.resumo: str | None = None
        self.vistas: set[str] = set()
        self.iniciada_em: datetime = _agora(None)
        self.terminada_em: datetime | None = None
        self.ok: bool | None = None

    async def __aenter__(self) -> Rodada:
        self.iniciada_em = _agora(None)
        # Worker que subiu antes do catálogo ser sincronizado: a rodada e as
        # ocorrências têm FK pro robô, então a linha precisa existir antes.
        if await self.session.get(OuvidoriaRobo, self.robo_chave) is None:
            await sincronizar_catalogo(self.session)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool:
        self.terminada_em = _agora(None)
        self.ok = exc is None
        erro = str(exc)[:500] if exc is not None else None
        if exc is not None:
            # A transação pode estar quebrada (erro de banco): limpa antes
            # de gravar a rodada, senão o INSERT abaixo também falha.
            await self.session.rollback()
            logger.warning(
                "ouvidoria_rodada_falhou", robo=self.robo_chave, erro=erro
            )
        duracao_ms = int((self.terminada_em - self.iniciada_em).total_seconds() * 1000)
        self.session.add(
            OuvidoriaRodada(
                robo_chave=self.robo_chave,
                iniciada_em=self.iniciada_em,
                terminada_em=self.terminada_em,
                ok=self.ok,
                resumo=self.resumo if self.ok else (self.resumo or erro),
                contadores=dict(self.contadores),
                erro=erro,
            )
        )
        robo = await self.session.get(OuvidoriaRobo, self.robo_chave)
        if robo is not None:
            robo.ultima_rodada_em = self.terminada_em
            robo.ultima_rodada_ok = self.ok
            robo.ultima_rodada_resumo = self.resumo if self.ok else (self.resumo or erro)
            robo.ultima_rodada_duracao_ms = duracao_ms
            if not self.ok:
                robo.ultima_falha_em = self.terminada_em
                robo.ultima_falha_erro = erro
        await self.session.commit()
        return False  # a exceção sobe

    async def registrar(self, chave: str, **campos) -> OuvidoriaOcorrencia:
        """`ouvidoria.registrar(...)` + marca a chave como vista nesta rodada."""
        self.vistas.add(chave)
        return await registrar(self.session, self.robo_chave, chave, **campos)

    def rever(self, row: OuvidoriaOcorrencia, *, agora: datetime | None = None) -> None:
        """O robô ainda vê o problema, mas sem a foto nova pra `registrar`
        (ex.: pedido que saiu da listagem e continua fora do Bling): só
        carimba `ultima_vista_em` e marca como vista — não fecha no
        `fechar_nao_vistas`."""
        row.ultima_vista_em = _agora(agora)
        self.vistas.add(row.chave)

    async def fechar_nao_vistas(
        self, *, excluir_contas: Iterable[str] = (), prefixo: str | None = None
    ) -> int:
        return await fechar_nao_vistas(
            self.session,
            self.robo_chave,
            self.vistas,
            excluir_contas=excluir_contas,
            prefixo=prefixo,
        )


# ─── aviso ─────────────────────────────────────────────────────────────────


def destinatarios(robo: OuvidoriaRobo | None, chave: str) -> tuple[list[str], str | None]:
    """(IDs, origem). Override da tela → env do robô → OUVIDORIA geral.
    Origem 'robo' | 'env' | 'geral' | None — a tela mostra de onde veio."""
    if robo is not None:
        ids = threema.parse_recipients(robo.threema_recipients)
        if ids:
            return ids, "robo"
    s = get_settings()
    d = ROBOS.get(chave)
    if d is not None and d.env_threema_recipients:
        ids = threema.parse_recipients(getattr(s, d.env_threema_recipients, None))
        if ids:
            return ids, "env"
    ids = threema.parse_recipients(s.ouvidoria_threema_recipients)
    return (ids, "geral") if ids else ([], None)


def _linha(o: OuvidoriaOcorrencia) -> str:
    partes = [p for p in (o.conta, o.pedido) if p]
    partes.append(o.titulo)
    return " · ".join(partes)


def link_painel(robo_chave: str) -> str:
    """Aba Ocorrências já filtrada no robô — vai no fim do aviso do Threema
    pra quem recebe no celular chegar na linha e marcar Tratado/Ignorar."""
    base = (get_settings().app_url or "").rstrip("/")
    return f"{base}/ouvidoria/robos?aba=ocorrencias&robo={robo_chave}"


def texto_aviso(
    nome_robo: str, pendentes: list[OuvidoriaOcorrencia], *, link: str | None = None
) -> str:
    """"<robô> — N ocorrência(s):" + uma linha por ocorrência
    (conta · pedido · título) e "→ ação" quando a ação muda em relação à
    linha anterior (as linhas vêm agrupadas por ação, então cada ação sai
    uma vez), e o `link` do painel por último. Corta em MAX_LINHAS_AVISO
    ("… e mais N") e, se ainda passar do limite de bytes do Threema, vai
    tirando linhas do fim — o painel tem o resto."""
    ordenadas = sorted(pendentes, key=lambda o: (o.acao or "", o.conta or "", o.pedido or ""))
    total = len(ordenadas)
    plural = "s" if total != 1 else ""
    cabecalho = f"{nome_robo} — {total} ocorrência{plural}:"

    def _montar(n: int) -> str:
        linhas: list[str] = []
        acao_anterior: object = object()
        for o in ordenadas[:n]:
            linhas.append(_linha(o))
            if o.acao and o.acao != acao_anterior:
                linhas.append(f"→ {o.acao}")
                acao_anterior = o.acao
        resto = total - n
        if resto > 0:
            linhas.append(f"… e mais {resto}")
        if link:
            linhas.append(link)
        return cabecalho + "\n" + "\n".join(linhas)

    n = min(total, MAX_LINHAS_AVISO)
    texto = _montar(n)
    while n > 1 and len(texto.encode("utf-8")) > threema._MAX_TEXT_BYTES:
        n -= 1
        texto = _montar(n)
    return texto


async def pendentes_de_aviso(
    session: AsyncSession, robo: OuvidoriaRobo, agora: datetime | None = None
) -> list[OuvidoriaOcorrencia]:
    """Abertas que precisam de pessoa e nunca foram avisadas, ou cujo último
    aviso/re-aviso já passou de `reaviso_horas`."""
    agora = _agora(agora)
    corte = agora - timedelta(hours=max(1, int(robo.reaviso_horas or 24)))
    abertas = (
        (
            await session.execute(
                select(OuvidoriaOcorrencia)
                .where(
                    OuvidoriaOcorrencia.robo_chave == robo.chave,
                    OuvidoriaOcorrencia.fechada_em.is_(None),
                    OuvidoriaOcorrencia.precisa_pessoa.is_(True),
                )
                .order_by(OuvidoriaOcorrencia.aberta_em)
            )
        )
        .scalars()
        .all()
    )
    out = []
    for o in abertas:
        if o.avisada_em is None:
            out.append(o)
            continue
        ultimo = max(o.avisada_em, o.reavisada_em or o.avisada_em)
        if ultimo <= corte:
            out.append(o)
    return out


async def avisar_pendentes(
    session: AsyncSession, robo_chave: str, *, agora: datetime | None = None
) -> dict:
    """UMA mensagem Threema por robô com tudo que está pendente. Só em modo
    `ligado`. Sucesso carimba `avisada_em` (1º aviso) ou `reavisada_em`;
    falha no envio (ou sem destinatário) não carimba nada — o próximo tick
    tenta de novo. Devolve {"avisadas": n} (+ "motivo" quando não mandou).
    Flush, sem commit."""
    agora = _agora(agora)
    robo = await session.get(OuvidoriaRobo, robo_chave)
    if robo is None:
        return {"avisadas": 0, "motivo": "robo_nao_cadastrado"}
    if robo.modo != "ligado":
        return {"avisadas": 0, "motivo": f"modo_{robo.modo}"}
    alvos, _origem = destinatarios(robo, robo_chave)
    if not alvos:
        return {"avisadas": 0, "motivo": "sem_destinatarios"}
    pendentes = await pendentes_de_aviso(session, robo, agora)
    if not pendentes:
        return {"avisadas": 0}
    texto = texto_aviso(robo.nome, pendentes, link=link_painel(robo_chave))
    try:
        # O assunto vem do CATÁLOGO (RoboDef), não da linha do banco: é
        # ele que sabe de qual conversa do Threema o robô faz parte.
        contexto = (ROBOS[robo_chave].contexto if robo_chave in ROBOS else "") or None
        resultado = await threema.ThreemaClient(contexto=contexto).send_to_all(
            texto, recipients=alvos
        )
    except Exception as e:  # noqa: BLE001 — aviso é best-effort; sem carimbo retenta
        logger.warning("ouvidoria_threema_falhou", robo=robo_chave, err=str(e)[:200])
        return {"avisadas": 0, "motivo": "threema_falhou", "erro": str(e)[:200]}
    if not (resultado or {}).get("sent"):
        logger.warning("ouvidoria_threema_ninguem_recebeu", robo=robo_chave, **(resultado or {}))
        return {"avisadas": 0, "motivo": "threema_falhou"}
    for o in pendentes:
        if o.avisada_em is None:
            o.avisada_em = agora
        else:
            o.reavisada_em = agora
    await session.flush()
    logger.info(
        "ouvidoria_aviso",
        robo=robo_chave,
        ocorrencias=len(pendentes),
        enviados=len(resultado.get("sent") or []),
        falhados=len(resultado.get("failed") or []),
    )
    return {"avisadas": len(pendentes)}


# ─── leitura pro painel ────────────────────────────────────────────────────


def saude(robo: OuvidoriaRobo, agora: datetime | None = None) -> str:
    """'desligado' | 'parado' | 'falhando' | 'ok'. Parado = ligado/silencioso
    e sem rodada há mais de PARADO_MULTIPLO × `config.cadencia_min` (ou nunca
    rodou) — vem antes de falhando porque "não está rodando" é pior que
    "rodou e deu erro"."""
    agora = _agora(agora)
    if robo.modo == "desligado":
        return "desligado"
    # Robô sem `Parametro` pra cadencia_min pode ter qualquer coisa salva:
    # a coluna Saúde não pode derrubar a lista inteira por isso.
    try:
        cadencia = int(config_do_robo(robo, robo.chave).get("cadencia_min") or CADENCIA_PADRAO_MIN)
    except (TypeError, ValueError):
        cadencia = CADENCIA_PADRAO_MIN
    limite = timedelta(minutes=cadencia * PARADO_MULTIPLO)
    if robo.ultima_rodada_em is None or agora - robo.ultima_rodada_em > limite:
        return "parado"
    if robo.ultima_rodada_ok is False:
        return "falhando"
    return "ok"


def inicio_do_dia_br(agora: datetime | None = None) -> datetime:
    """00:00 de hoje em São Paulo, em UTC — "Novas hoje" e "Rodadas hoje"
    contam pelo dia da operação, não pelo dia UTC."""
    local = _agora(agora).astimezone(_TZ_BR)
    return local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)


async def contagens_por_robo(session: AsyncSession, agora: datetime | None = None) -> dict:
    """{chave: {abertas, abertas_pessoa, rodadas_hoje, rodadas_hoje_ok}}."""
    agora = _agora(agora)
    out: dict[str, dict] = {}
    rows = (
        await session.execute(
            select(
                OuvidoriaOcorrencia.robo_chave,
                func.count(),
                func.count().filter(OuvidoriaOcorrencia.precisa_pessoa.is_(True)),
            )
            .where(OuvidoriaOcorrencia.fechada_em.is_(None))
            .group_by(OuvidoriaOcorrencia.robo_chave)
        )
    ).all()
    for chave, abertas, pessoa in rows:
        out.setdefault(chave, {})
        out[chave]["abertas"] = int(abertas)
        out[chave]["abertas_pessoa"] = int(pessoa)
    hoje = inicio_do_dia_br(agora)
    rows = (
        await session.execute(
            select(
                OuvidoriaRodada.robo_chave,
                func.count(),
                func.count().filter(OuvidoriaRodada.ok.is_(True)),
            )
            .where(OuvidoriaRodada.iniciada_em >= hoje)
            .group_by(OuvidoriaRodada.robo_chave)
        )
    ).all()
    for chave, total, ok in rows:
        out.setdefault(chave, {})
        out[chave]["rodadas_hoje"] = int(total)
        out[chave]["rodadas_hoje_ok"] = int(ok)
    return out


async def resumo_ocorrencias(session: AsyncSession, agora: datetime | None = None) -> dict:
    """Os StatCards da aba Ocorrências. `contas_sem_vigilancia` = abertas de
    chave `conta:…` (a ocorrência que o robô abre quando a API da conta
    falhou — nenhum robô enxerga aquela loja enquanto isso)."""
    agora = _agora(agora)
    hoje = inicio_do_dia_br(agora)
    corte_7d = agora - timedelta(days=7)
    Oc = OuvidoriaOcorrencia  # noqa: N806
    row = (
        await session.execute(
            select(
                func.count().filter(Oc.fechada_em.is_(None)),
                func.count().filter(Oc.fechada_em.is_(None), Oc.precisa_pessoa.is_(True)),
                func.count().filter(Oc.aberta_em >= hoje),
                func.count().filter(Oc.fechamento == "sumiu", Oc.fechada_em >= corte_7d),
                func.count().filter(Oc.fechamento == "tratada", Oc.fechada_em >= corte_7d),
                func.count().filter(Oc.fechada_em.is_(None), Oc.chave.like("conta:%")),
            ).select_from(Oc)
        )
    ).one()
    return {
        "abertas": int(row[0]),
        "abertas_pessoa": int(row[1]),
        "novas_hoje": int(row[2]),
        "sumiram_7d": int(row[3]),
        "tratadas_7d": int(row[4]),
        "contas_sem_vigilancia": int(row[5]),
    }


async def gc_rodadas(session: AsyncSession, dias: int = 30) -> int:
    """Apaga rodadas mais velhas que `dias`. Devolve quantas."""
    corte = datetime.now(UTC) - timedelta(days=dias)
    res = await session.execute(
        delete(OuvidoriaRodada).where(OuvidoriaRodada.iniciada_em < corte)
    )
    await session.flush()
    return int(res.rowcount or 0)


# Retenção das ocorrências FECHADAS. Com 7 robôs a rotatividade mudou de
# patamar (conta instável abre e fecha de hora em hora, `rastreio:` por dia,
# `margem_alta:` por pedido), e o histórico só crescia — inclusive por baixo do
# `_ultima_fechada`, consultado em todo `registrar`.
RETENCAO_OCORRENCIAS_DIAS = 180


async def gc_ocorrencias(session: AsyncSession, dias: int = RETENCAO_OCORRENCIAS_DIAS) -> int:
    """Apaga ocorrências FECHADAS há mais de `dias`. Devolve quantas.

    As `ignorada` NUNCA são apagadas: é a memória de "uma pessoa disse pra não
    cobrar mais isto" e é o `_ultima_fechada` que a lê — apagar faria o robô
    reabrir a linha na rodada seguinte, desfazendo a decisão de quem mandou.
    Aberta também nunca sai (só fecha quem o robô ou a pessoa fecharam)."""
    corte = datetime.now(UTC) - timedelta(days=max(1, int(dias)))
    res = await session.execute(
        delete(OuvidoriaOcorrencia).where(
            OuvidoriaOcorrencia.fechada_em.is_not(None),
            OuvidoriaOcorrencia.fechada_em < corte,
            func.coalesce(OuvidoriaOcorrencia.fechamento, "") != "ignorada",
        )
    )
    await session.flush()
    return int(res.rowcount or 0)
