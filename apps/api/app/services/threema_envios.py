"""Cadastro dos envios do Threema (Cadastros › Envios Threema, tarefa 16).

O CÓDIGO DESCREVE cada envio (`services/threema_catalogo`); o BANCO decide quem
recebe, se está ligado e a observação (tabelas da migration 0506). Este
módulo é o NÚCLEO que junta os dois:

* `legado(chave)` — quem recebe HOJE, montado exatamente como cada ponto de
  envio monta (o .env, as linhas do Informar, a lista "Avisar" do robô da
  Ouvidoria, o 👤 da regra de status, o usuário pelo nome), mais o
  liga/desliga de hoje. É a mesma função na hora do envio, na semente e na
  conferência (testes de paridade: `tests/test_threema_paridade.py`).
* `resolver(chave)` — quem recebe AGORA. Envio não migrado (`migrado=False`
  no catálogo) devolve o legado exato, sem ler o banco.
  Migrado: o Cadastro é a única fonte (ninguém válido = ninguém, sem cair no
  legado). O legado só volta na emergência (linha `usar_legado` ou
  THREEMA_CADASTRO_ATIVO=false), com o banco fora sem cópia boa recente
  (última resolução boa do processo, até 24 h) e na linha sem semente. A
  leitura do Cadastro tem teto de 2 s e sessão própria (a de quem chama não
  fica envenenada).
* `enviar(chave, texto)` — resolve e manda. Nunca chama `send_to_all([])`
  (lista vazia cai no THREEMA_RECIPIENTS); sem ninguém válido grava o
  registro `ok=false` e acende o sino dos admins (dedupe diário).
* `semear`, `conferir`, `sincronizar` — os comandos de cada fatia.

Cada fatia (F1–F4) troca o portão `if not <env>: return` do ponto de envio
por `enviar(K.X, …)` (ou `resolver(K.X)`) no mesmo commit que vira
`migrado=True`. Depois da F4 os 47 envios gerenciados do servidor leem o
Cadastro; as travas da limpeza (C12: `send_to_all` só aqui ou numa marca,
lista do .env só no legado, chave só do catálogo) estão em
`tests/test_threema_estatico.py`.

Linha de comando (no container da API):

    python -m app.services.threema_envios sincronizar
    python -m app.services.threema_envios semear --fatia F1 [--chaves a,b]
        [--aplicar] [--sobrescrever]
    python -m app.services.threema_envios conferir [--fatia F1] [--chaves a,b]

`semear` sem `--aplicar` só imprime o plano (IDs mascarados) e roda com o
código DA fatia (publicada, ou a imagem nova num container avulso), nunca
com o de antes: a regra da semente pode mudar na própria fatia (ver
`semear`). `conferir` compara o legado com o Cadastro, chave por chave, e
sai com 1 se houver diferença — é o portão de cada fatia, depois da semente,
e a saída é o roteiro para refazer à mão numa reversão.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import delete, exists, func, literal_column, or_, select, text, tuple_, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import (
    LogisticaStatus,
    OuvidoriaRobo,
    ThreemaCadastroEstado,
    ThreemaContato,
    ThreemaEnvio,
    ThreemaEnvioDestinatario,
    ThreemaGrupo,
    ThreemaGrupoMembro,
    ThreemaInformarConfig,
    User,
    UserRole,
    UserStatus,
)
from app.services import threema, threema_registro
from app.services.threema_catalogo import CATALOGO, ENVIOS, FATIAS, EnvioDef, chave_ouvidoria

logger = structlog.get_logger()

# As colunas que o CÓDIGO escreve (o resto é da pessoa ou de controle).
COLUNAS_DO_CODIGO = (
    "painel",
    "nome",
    "descricao",
    "frequencia_tipo",
    "frequencia_texto",
    "onde_roda",
)
# Teto da leitura do Cadastro (o pool do banco é 10+20: envio nenhum espera
# conexão para sempre).
TIMEOUT_CADASTRO_S = 2.0
# Cópia da última resolução boa (por chave e escopo), usada com o banco fora.
CACHE_VALIDADE_S = 24 * 3600
_TIMEOUT_SINO_S = 5.0
_FUSO = ZoneInfo("America/Sao_Paulo")

# Por que a lista veio de onde veio (Resolucao.motivo).
NAO_MIGRADO = "nao_migrado"
EMERGENCIA = "emergencia"
CADASTRO_INDISPONIVEL = "cadastro_indisponivel"
SEM_SEMENTE = "sem_semente"
DESLIGADO = "desligado"
NINGUEM_VALIDO = "ninguem_valido"
NAO_GERENCIADO = "nao_gerenciado"


# ─────────────────────────────────────────────────────────── tipos


@dataclass(frozen=True)
class Destino:
    """Quem recebe, já com o ID do Threema lido na hora."""

    id: str
    nome: str | None
    # usuario | contato | env (só nome do .env) | desconhecido
    tipo: str
    ref_id: uuid.UUID | None = None


@dataclass(frozen=True)
class ListaLegado:
    """Quem recebe hoje e se está ligado hoje (antes do Cadastro)."""

    ids: tuple[str, ...]
    ativo: bool
    # De onde veio, em frase curta para a tela e o `conferir`.
    fonte: str


@dataclass(frozen=True)
class Resolucao:
    chave: str
    ativo: bool
    # legado | cadastro | cache (os valores de `threema_registro.VIAS`)
    via: str
    ids: tuple[str, ...]
    destinos: tuple[Destino, ...] = ()
    motivo: str | None = None
    avisos: tuple[str, ...] = ()
    escopo: str | None = None
    fonte: str | None = None


class CadastroError(RuntimeError):
    """Comando recusado (semente com conflito, chave desconhecida...)."""


def _envio(chave: str) -> EnvioDef:
    try:
        return CATALOGO[chave]
    except KeyError:
        raise KeyError(f"threema_envios: envio desconhecido {chave!r}") from None


def _escopo(escopo: object | None) -> str | None:
    return str(escopo) if escopo not in (None, "") else None


def _uuid(valor: str | None) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(valor)) if valor else None
    except ValueError:
        return None


def mascara(threema_id: str | None) -> str:
    """Só as 2 primeiras letras (como o Histórico): nunca o ID inteiro."""
    rid = (threema_id or "").strip().upper()
    return f"{rid[:2]}…" if rid else "—"


def _dedupe(ids) -> tuple[str, ...]:
    vistos: list[str] = []
    for rid in ids:
        if rid and rid not in vistos:
            vistos.append(rid)
    return tuple(vistos)


# ─────────────────────────────────────────────────────────── sincronização


def _linhas_do_catalogo() -> list[dict]:
    return [
        {
            "chave": d.chave,
            "painel": d.painel,
            "nome": d.nome,
            "descricao": d.descricao,
            "frequencia_tipo": d.frequencia_tipo,
            "frequencia_texto": d.frequencia_texto,
            "onde_roda": d.onde_roda,
        }
        for d in ENVIOS
    ]


async def sincronizar(session: AsyncSession) -> dict[str, int]:
    """Upsert do catálogo em `threema_envio`. Só reescreve as colunas do
    código, e só nas linhas em que algo mudou (o gatilho do Histórico e o
    `updated_at` não acusam mudança que não houve). Chave que saiu do código
    ganha `descontinuado_em` (nunca é apagada: o registro e o Histórico a
    citam); chave que voltou perde o carimbo. NUNCA semeia. Flush, sem commit.

    Devolve {"novos", "atualizados", "descontinuados"}."""
    tabela = ThreemaEnvio.__table__
    linhas = _linhas_do_catalogo()
    stmt = pg_insert(tabela).values(linhas)
    exc = stmt.excluded
    mudou = or_(
        tuple_(*(tabela.c[c] for c in COLUNAS_DO_CODIGO)).is_distinct_from(
            tuple_(*(exc[c] for c in COLUNAS_DO_CODIGO))
        ),
        tabela.c.descontinuado_em.is_not(None),
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[tabela.c.chave],
        set_={
            **{c: exc[c] for c in COLUNAS_DO_CODIGO},
            "descontinuado_em": None,
            "updated_at": func.now(),
        },
        where=mudou,
    ).returning(
        tabela.c.chave,
        # xmax = 0 só na linha que o INSERT criou (a do UPDATE tem xmax).
        literal_column("(xmax = 0)").label("novo"),
    )
    escritas = (await session.execute(stmt)).all()
    novos = sum(1 for r in escritas if r.novo)
    codigo = [linha["chave"] for linha in linhas]
    sumidas = await session.execute(
        update(tabela)
        .where(tabela.c.chave.not_in(codigo), tabela.c.descontinuado_em.is_(None))
        .values(descontinuado_em=func.now(), updated_at=func.now())
        .returning(tabela.c.chave)
    )
    descontinuados = [r[0] for r in sumidas.all()]
    await session.flush()
    resumo = {
        "novos": novos,
        "atualizados": len(escritas) - novos,
        "descontinuados": len(descontinuados),
    }
    if novos or resumo["atualizados"] or descontinuados:
        logger.info("threema_envios_sincronizado", **resumo, sairam=descontinuados[:20])
    return resumo


# ─────────────────────────────────────────────────────────── legado


async def _linha_informar(session: AsyncSession, contexto: str) -> ThreemaInformarConfig | None:
    return (
        await session.execute(
            select(ThreemaInformarConfig).where(ThreemaInformarConfig.contexto == contexto)
        )
    ).scalar_one_or_none()


async def _interruptor_legado(d: EnvioDef, session: AsyncSession) -> bool:
    """O liga/desliga de HOJE (antes do Cadastro)."""
    from app.services import ouvidoria

    if d.interruptor == "robo_modo" and d.interruptor_alvo:
        # denuncia_relatorio_threema.enviar_pendente: só manda `ligado`.
        return await ouvidoria.modo_efetivo(session, d.interruptor_alvo) == "ligado"
    if d.interruptor == "robo_config" and d.interruptor_alvo:
        robo_chave, _, chave_config = d.interruptor_alvo.partition(".")
        robo = await session.get(OuvidoriaRobo, robo_chave)
        # margem_auto_hold._alertar_margem_alta: a caixinha da config do robô.
        return bool(ouvidoria.config_do_robo(robo, robo_chave).get(chave_config, False))
    if d.interruptor == "proprio" and d.legado.interruptor_env:
        # conferencia_shopee/threema_aviso.ligado antes da F2/C9e: a chave do .env.
        return bool(getattr(get_settings(), d.legado.interruptor_env, False))
    return True


def _lista_vazia_desliga(d: EnvioDef) -> bool:
    """O desligado de HOJE deste envio é a própria lista vazia: o ponto tinha
    `if not <lista>: return` (os portões de .env, §0.7 do desenho) e não há
    outra chave de liga/desliga. Com a lista vazia ele fica calado: sem
    mensagem, sem registro e sem sino (ex.: o item 24, que nasceu vazio de
    propósito).

    Vale para a lista do .env e para a linha do Informar de um envio
    AUTOMÁTICO (F2: os prazos da Amazon, o prazo de resposta das Devoluções,
    a IA de Chamado parada e o robô da Denúncia — linha vazia ou ausente =
    ninguém, calado), e para a lista do robô na Ouvidoria (a "Avisar", com o
    .env do robô e o OUVIDORIA geral de reserva): o aviso das pendências de
    cada robô (F3/C10a: `avisar_pendentes` sem ninguém voltava
    `sem_destinatarios`, calado) e os avisos na hora do Robô da Margem
    (F3/C10b, 25 a 27: `_recipients_margem_auto` vazio era o `if not
    recipients: return`, calado). O modo do robô é o "depende de", lido
    antes. No 27 o liga/desliga é a caixinha "avisar margem alta" (gravada
    direto no robô): sem ninguém na lista ele também ficava calado com a
    caixinha marcada, e é assim que continua (ver `_interruptor_cadastro`).
    Não vale para o botão (`manual`: a lista vazia é o 422 do botão, não um
    desligado), para o liga/desliga de outra fonte (o modo do robô do
    relatório da Denúncia, a chave da conferência no .env) nem para o usuário
    pelo nome (o 12: "thatcher" sem Threema é falha, não desligado).

    No Cadastro, ligado sem ninguém grava o registro `ok=false` e acende o
    sino dos admins a cada rodada. Por isso a semente grava `ativo=false`
    quando a lista de hoje sai vazia (no 27, que não tem `ativo` próprio,
    não grava linha nenhuma), a conferência trata o legado vazio desses
    envios como desligado e, para a semente rodada com um código que ainda
    não conhecia a regra (`ativo` NULL e nenhuma linha), o
    `_interruptor_cadastro` também lê desligado."""
    if d.legado.interruptor_env or d.interruptor in ("robo_modo", "fixo"):
        return False
    if d.legado.robo:
        return True
    if d.interruptor != "proprio":
        return False
    if d.legado.envs:
        return True
    return bool(d.legado.informar) and d.frequencia_tipo != "manual"


def _lista_de_hoje(d: EnvioDef) -> str:
    """De onde vem a lista que, vazia, é o desligado de hoje (textos)."""
    if d.legado.envs:
        return "do .env"
    if d.legado.robo:
        return "do robô na Ouvidoria"
    return "do Informar"


async def _ids_legado(d: EnvioDef, escopo: str | None, session: AsyncSession):
    """(ids, fonte) de hoje. Cada ramo reproduz o ponto de envio citado."""
    from app.services import ouvidoria

    L = d.legado  # noqa: N806
    s = get_settings()
    if d.onde_roda == "mac":
        return [], f".env do Mac ({L.env_mac})" if L.env_mac else ".env do Mac"
    if d.onde_roda != "servidor" or not d.gerenciado:
        return [], "informativo (nada sai pelo DaVinci)"
    if L.regra_status:
        # logistica_bling.enviar_threema_em_lote e routers/logistica (botões):
        # `parse(rule.threema_recipients) or None` → o send_to_all cai no
        # THREEMA_RECIPIENTS (services/threema.py) quando a regra não tem 👤.
        regra_id = _uuid(escopo)
        regra = await session.get(LogisticaStatus, regra_id) if regra_id else None
        ids = threema.parse_recipients(regra.threema_recipients) if regra else []
        if ids:
            return ids, "👤 da regra de status"
        return threema.parse_recipients(s.threema_recipients), "THREEMA_RECIPIENTS do servidor"
    if L.robo:
        # ouvidoria.destinatarios: Avisar do robô → env do robô → OUVIDORIA geral.
        robo = await session.get(OuvidoriaRobo, L.robo)
        ids, origem = ouvidoria.destinatarios(robo, L.robo)
        nome = ouvidoria.ROBOS[L.robo].nome if L.robo in ouvidoria.ROBOS else L.robo
        env_robo = (
            ouvidoria.ROBOS[L.robo].env_threema_recipients if L.robo in ouvidoria.ROBOS else None
        )
        fonte = {
            "robo": f"Avisar do robô '{nome}' (Ouvidoria › Robôs)",
            "env": f"{(env_robo or '').upper()} do servidor (reserva do robô)",
            "geral": "OUVIDORIA_THREEMA_RECIPIENTS do servidor",
        }.get(origem or "", f"ninguém no Avisar do robô '{nome}' nem no .env")
        return ids, fonte
    if L.informar:
        # Linhas de threema_informar_config: a 1ª LINHA EXISTENTE vence e linha
        # vazia = ninguém (o `denuncia_robo_aviso.destinatarios` de antes da
        # F2/C9d; com um contexto só é o `parse(row.recipients if row else "")`
        # de todos os outros).
        for i, ctx in enumerate(L.informar):
            row = await _linha_informar(session, ctx)
            if row is not None:
                reserva = f" (reserva de '{L.informar[0]}')" if i else ""
                return threema.parse_recipients(row.recipients), f"Informar '{ctx}'{reserva}"
        return [], f"nenhuma linha do Informar ({', '.join(L.informar)})"
    if L.usuario_nome:
        # tuta_devolucoes.entregar_resultado: o usuário pelo nome, com Threema.
        user = (
            (
                await session.execute(
                    select(User)
                    .where(func.lower(User.name) == L.usuario_nome)
                    .where(User.threema.is_not(None))
                )
            )
            .scalars()
            .first()
        )
        return (
            threema.parse_recipients(user.threema if user else None),
            f"Threema do usuário '{L.usuario_nome}'",
        )
    if L.envs:
        # `parse(a or b)`: o 1º campo não vazio vence (getattr: o item 24 lê
        # um campo que não existe em Settings — hoje ninguém recebe).
        for nome in L.envs:
            valor = getattr(s, nome, "")
            if valor:
                return threema.parse_recipients(valor), f"{nome.upper()} do servidor"
        return [], f"{' / '.join(n.upper() for n in L.envs)} vazio(s) no servidor"
    return [], "sem fonte"


async def legado(
    chave: str, *, escopo: object | None = None, session: AsyncSession | None = None
) -> ListaLegado:
    """Quem recebe HOJE e se o envio está ligado HOJE, montado como o ponto de
    envio monta. Lê com a sessão de quem chama (como as funções de hoje); sem
    sessão, abre uma. Mac e informativos: o servidor não conhece a lista
    (ids vazio)."""
    d = _envio(chave)
    if session is None:
        from app import db as _db

        async with _db.session_scope() as s:
            return await legado(chave, escopo=escopo, session=s)
    ids, fonte = await _ids_legado(d, _escopo(escopo), session)
    ativo = await _interruptor_legado(d, session)
    # Sem dedupe: é a lista EXATA de hoje (ID repetido no .env sai duas vezes
    # hoje também). Quem dedupe é a expansão do Cadastro.
    return ListaLegado(ids=tuple(ids), ativo=ativo, fonte=fonte)


async def dependencias(chave: str, session: AsyncSession) -> list[tuple[str, bool]]:
    """[(texto, ok)] do que, fora do Cadastro, também precisa estar ligado
    (modo do robô, chave do .env, canal do Threema). Só leitura."""
    from app.services import ouvidoria

    d = _envio(chave)
    s = get_settings()
    out = []
    for dep in d.depende_de:
        if dep.tipo == "robo_modo" and chave_ouvidoria(dep.alvo) == d.chave:
            # O aviso do próprio robô (ouvidoria.avisar_pendentes): sem linha
            # no banco não sai nada (`robo_nao_cadastrado`), e só `ligado` avisa.
            modo = (
                await session.execute(
                    select(OuvidoriaRobo.modo).where(OuvidoriaRobo.chave == dep.alvo)
                )
            ).scalar_one_or_none()
            ok = modo == "ligado"
        elif dep.tipo == "robo_modo":
            # margem_auto_hold._robo_ligado (os avisos na hora do Robô da
            # Margem): sem linha = `ligado`.
            ok = await ouvidoria.modo(session, dep.alvo) == "ligado"
        elif dep.tipo == "env":
            ok = bool(getattr(s, dep.alvo, False))
        else:  # canal
            ok = not threema.ThreemaClient(contexto=dep.alvo).disabled
        out.append((dep.texto, ok))
    return out


def canal(contexto: str) -> str:
    """A conversa por onde um remetente sai DE VERDADE: o contexto passa pelos
    apelidos e pelo THREEMA_CONTEXT_CHANNELS do servidor — a regra do próprio
    `ThreemaClient` (só monta o cliente, não envia nada). `desativado` = o
    canal está desligado no servidor."""
    c = threema.ThreemaClient(contexto=contexto)
    return "desativado" if c.disabled else (c.canal or "geral")


async def _nomear(session: AsyncSession, ids: Sequence[str]) -> tuple[Destino, ...]:
    """Nomes para a lista do legado: usuário do cadastro (o ativo vence),
    depois contato externo, depois o apelido do .env."""
    if not ids:
        return ()
    alvo = set(ids)
    users = (
        (
            await session.execute(
                select(User)
                .where(func.upper(func.trim(User.threema)).in_(sorted(alvo)))
                .order_by(User.created_at)
            )
        )
        .scalars()
        .all()
    )
    por_id: dict[str, Destino] = {}
    for u in sorted(users, key=lambda u: not _usuario_valido(u)):
        rid = (u.threema or "").strip().upper()
        por_id.setdefault(rid, Destino(rid, u.name or u.email, "usuario", u.id))
    contatos = (
        (await session.execute(select(ThreemaContato).where(ThreemaContato.threema_id.in_(alvo))))
        .scalars()
        .all()
    )
    for c in contatos:
        por_id.setdefault(c.threema_id, Destino(c.threema_id, c.nome, "contato", c.id))
    s = get_settings()
    for e in threema.parse_recipient_directory(s.threema_recipient_names, None):
        por_id.setdefault(e["id"], Destino(e["id"], e["nome"], "env"))
    return tuple(por_id.get(rid) or Destino(rid, None, "desconhecido") for rid in ids)


async def _pelo_legado(
    d: EnvioDef,
    escopo: str | None,
    session: AsyncSession | None,
    *,
    motivo: str,
    com_nomes: bool,
) -> Resolucao:
    if session is None:
        from app import db as _db

        async with _db.session_scope() as s:
            return await _pelo_legado(d, escopo, s, motivo=motivo, com_nomes=com_nomes)
    leg = await legado(d.chave, escopo=escopo, session=session)
    destinos = await _nomear(session, leg.ids) if com_nomes else ()
    if not d.gerenciado:
        motivo = NAO_GERENCIADO
    return Resolucao(
        chave=d.chave,
        ativo=leg.ativo,
        via="legado",
        ids=leg.ids,
        destinos=destinos,
        motivo=motivo,
        escopo=escopo,
        fonte=leg.fonte,
    )


# ─────────────────────────────────────────────────────────── o Cadastro


def _usuario_valido(u: User) -> bool:
    return (
        u.status == UserStatus.ACTIVE
        and u.disabled_at is None
        and not (u.open_id or "").startswith("system:")
        and threema.threema_id_valido(u.threema)
    )


def _motivo_usuario(u: User) -> str:
    nome = u.name or u.email
    if (u.open_id or "").startswith("system:"):
        return f"{nome} é usuário do sistema"
    if u.disabled_at is not None:
        return f"{nome} está desativado"
    if u.status != UserStatus.ACTIVE:
        return (
            f"{nome} está suspenso" if u.status == UserStatus.SUSPENDED else f"{nome} está pendente"
        )
    if not (u.threema or "").strip():
        return f"{nome} sem Threema"
    return f"{nome} com Threema inválido"


@dataclass
class _Expansao:
    destinos: list[Destino] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)
    linhas: int = 0


async def _expandir(session: AsyncSession, linhas: Sequence[ThreemaEnvioDestinatario]) -> _Expansao:
    """Abre as linhas de quem recebe em IDs: usuário ativo, sem `disabled_at`
    e com Threema válido; contato ativo; grupo aberto nos membros (na ordem).
    Dedupe mantendo a ordem. Quem ficou de fora vira aviso."""
    out = _Expansao(linhas=len(linhas))
    if not linhas:
        return out
    grupo_ids = {r.grupo_id for r in linhas if r.grupo_id}
    grupos = (
        {
            g.id: g
            for g in (
                await session.execute(select(ThreemaGrupo).where(ThreemaGrupo.id.in_(grupo_ids)))
            ).scalars()
        }
        if grupo_ids
        else {}
    )
    membros: dict[uuid.UUID, list[ThreemaGrupoMembro]] = {}
    if grupo_ids:
        for m in (
            await session.execute(
                select(ThreemaGrupoMembro)
                .where(ThreemaGrupoMembro.grupo_id.in_(grupo_ids))
                .order_by(
                    ThreemaGrupoMembro.ordem,
                    ThreemaGrupoMembro.created_at,
                    ThreemaGrupoMembro.id,
                )
            )
        ).scalars():
            membros.setdefault(m.grupo_id, []).append(m)
    todos = [*linhas, *(m for ms in membros.values() for m in ms)]
    user_ids = {r.user_id for r in todos if r.user_id}
    contato_ids = {r.contato_id for r in todos if r.contato_id}
    users = (
        {
            u.id: u
            for u in (await session.execute(select(User).where(User.id.in_(user_ids)))).scalars()
        }
        if user_ids
        else {}
    )
    contatos = (
        {
            c.id: c
            for c in (
                await session.execute(
                    select(ThreemaContato).where(ThreemaContato.id.in_(contato_ids))
                )
            ).scalars()
        }
        if contato_ids
        else {}
    )
    vistos: set[str] = set()

    def _um(ref) -> None:
        if ref.user_id:
            u = users.get(ref.user_id)
            if u is None:
                return
            if not _usuario_valido(u):
                out.avisos.append(_motivo_usuario(u))
                return
            rid = u.threema.strip().upper()
            if rid not in vistos:
                vistos.add(rid)
                out.destinos.append(Destino(rid, u.name or u.email, "usuario", u.id))
        elif ref.contato_id:
            c = contatos.get(ref.contato_id)
            if c is None:
                return
            if not c.ativo:
                out.avisos.append(f"contato {c.nome} inativo")
                return
            if c.threema_id not in vistos:
                vistos.add(c.threema_id)
                out.destinos.append(Destino(c.threema_id, c.nome, "contato", c.id))

    for r in linhas:
        if r.grupo_id:
            ms = membros.get(r.grupo_id, [])
            if not ms:
                g = grupos.get(r.grupo_id)
                out.avisos.append(f"grupo {g.nome if g else '?'} sem ninguém")
            for m in ms:
                _um(m)
        else:
            _um(r)
    return out


async def _linhas_destinatario(
    session: AsyncSession, chave: str, regra: uuid.UUID | None
) -> list[ThreemaEnvioDestinatario]:
    filtro = (
        ThreemaEnvioDestinatario.logistica_status_id == regra
        if regra is not None
        else ThreemaEnvioDestinatario.logistica_status_id.is_(None)
    )
    return list(
        (
            await session.execute(
                select(ThreemaEnvioDestinatario)
                .where(ThreemaEnvioDestinatario.envio_chave == chave, filtro)
                .order_by(
                    ThreemaEnvioDestinatario.ordem,
                    ThreemaEnvioDestinatario.created_at,
                    ThreemaEnvioDestinatario.id,
                )
            )
        ).scalars()
    )


@dataclass(frozen=True)
class _Lido:
    usar_legado: bool = False
    semeado: bool = False
    ativo: bool = True
    destinos: tuple[Destino, ...] = ()
    avisos: tuple[str, ...] = ()


async def _sem_linha_padrao(session: AsyncSession, chave: str) -> bool:
    """A lista padrão do envio não tem NENHUMA linha (nem grupo)."""
    return not (
        await session.execute(
            select(
                exists().where(
                    ThreemaEnvioDestinatario.envio_chave == chave,
                    ThreemaEnvioDestinatario.logistica_status_id.is_(None),
                )
            )
        )
    ).scalar_one()


async def _interruptor_cadastro(d: EnvioDef, linha: ThreemaEnvio, session: AsyncSession) -> bool:
    """Liga/desliga do envio migrado: `proprio` = a coluna `ativo` (NULL =
    o interruptor de hoje: a chave do .env, se houver, senão ligado);
    `robo_modo` e `robo_config` leem direto da fonte que já existe (a tela
    grava lá).

    NULL no envio cujo desligado de hoje é a lista vazia
    (`_lista_vazia_desliga`) e SEM NENHUMA linha de quem recebe = desligado,
    o mesmo desligado de hoje. É o que fica de uma semente rodada com o
    código de antes da regra (a da F1 não conhecia a linha do Informar
    vazia; a de antes da revisão da F1 não gravava `ativo`): sem isto, o
    envio ficaria ligado com ninguém e acenderia o sino dos admins a cada
    rodada, o que hoje não acontece. Lista com gente que não vale (suspenso,
    grupo vazio) continua ligada: é o "ninguém válido", com sino.

    No 27 (`robo_config`, a caixinha "avisar margem alta") vale o mesmo por
    cima da caixinha: sem nenhuma linha é o desligado de hoje (a lista vazia
    calava o alerta com a caixinha marcada)."""
    if d.interruptor == "proprio":
        if linha.ativo is not None:
            return bool(linha.ativo)
        if d.legado.interruptor_env:
            return bool(getattr(get_settings(), d.legado.interruptor_env, False))
        if _lista_vazia_desliga(d) and await _sem_linha_padrao(session, d.chave):
            return False
        return True
    if (
        d.interruptor == "robo_config"
        and _lista_vazia_desliga(d)
        and await _sem_linha_padrao(session, d.chave)
    ):
        return False
    return await _interruptor_legado(d, session)


async def _ler_cadastro(
    d: EnvioDef,
    escopo: str | None,
    session: AsyncSession,
    *,
    olhar_emergencia: bool = True,
    so_lista: bool = False,
) -> _Lido:
    """Lê o Cadastro de um envio (sem decidir nada sobre o legado).

    `olhar_emergencia=False` e `so_lista=True` são da conferência: ela quer
    ver a lista que está no Cadastro mesmo com a emergência ligada ou com o
    envio desligado."""
    if olhar_emergencia:
        estado = await session.get(ThreemaCadastroEstado, 1)
        if estado is not None and estado.usar_legado:
            return _Lido(usar_legado=True)
    linha = await session.get(ThreemaEnvio, d.chave, populate_existing=True)
    if linha is None or linha.semeado_em is None:
        return _Lido()
    ativo_ = await _interruptor_cadastro(d, linha, session)
    if not ativo_ and not so_lista:
        return _Lido(semeado=True, ativo=False)
    avisos: list[str] = []
    regra = _uuid(escopo) if d.escopo_tipo == "regra_status" else None
    exp = _Expansao()
    if regra is not None:
        exp = await _expandir(session, await _linhas_destinatario(session, d.chave, regra))
        avisos.extend(exp.avisos)
        if exp.linhas and not exp.destinos:
            avisos.append("ninguém válido na lista da regra: vale a lista padrão do envio")
    if not exp.destinos:
        exp = await _expandir(session, await _linhas_destinatario(session, d.chave, None))
        avisos.extend(exp.avisos)
    return _Lido(
        semeado=True, ativo=ativo_, destinos=tuple(exp.destinos), avisos=tuple(_dedupe(avisos))
    )


async def _ler_cadastro_isolado(d: EnvioDef, escopo: str | None) -> _Lido:
    """A leitura do envio de verdade: sessão própria, `statement_timeout` de
    2 s no banco. Quem chama põe o teto de tempo em volta."""
    from app import db as _db

    async with _db.SessionLocal() as s:
        try:
            await s.execute(text("SET LOCAL statement_timeout = '2s'"))
            return await _ler_cadastro(d, escopo, s)
        finally:
            await s.rollback()


# Última resolução boa do Cadastro, por (chave, escopo): {..: (monotonic, Resolucao)}.
_CACHE: dict[tuple[str, str | None], tuple[float, Resolucao]] = {}


def limpar_cache() -> None:
    _CACHE.clear()


def _do_cache(chave: str, escopo: str | None) -> Resolucao | None:
    item = _CACHE.get((chave, escopo))
    if item is None:
        return None
    quando, res = item
    if time.monotonic() - quando > CACHE_VALIDADE_S:
        _CACHE.pop((chave, escopo), None)
        return None
    return res


async def resolver(
    chave: str,
    *,
    escopo: object | None = None,
    session: AsyncSession | None = None,
    com_nomes: bool = False,
) -> Resolucao:
    """Quem recebe AGORA o envio `chave` (ver a docstring do módulo).

    `escopo`: o id da regra de status (item 1). `session`: a de quem chama,
    usada SÓ para montar o legado (como as funções de hoje); o Cadastro é
    lido em sessão própria. `com_nomes`: preenche `destinos` também no
    legado (a tela pede; o envio não precisa)."""
    d = _envio(chave)
    esc = _escopo(escopo)
    if not d.migrado:
        return await _pelo_legado(d, esc, session, motivo=NAO_MIGRADO, com_nomes=com_nomes)
    if not get_settings().threema_cadastro_ativo:
        return await _pelo_legado(d, esc, session, motivo=EMERGENCIA, com_nomes=com_nomes)
    try:
        async with asyncio.timeout(TIMEOUT_CADASTRO_S):
            lido = await _ler_cadastro_isolado(d, esc)
    except Exception as exc:  # noqa: BLE001 — banco fora/lento não pode calar o aviso
        guardada = _do_cache(chave, esc)
        if guardada is not None:
            logger.warning("threema_cadastro_usou_cache", chave=chave, erro=type(exc).__name__)
            return replace(
                guardada,
                via="cache",
                avisos=(*guardada.avisos, "Cadastro fora do ar: valeu a última lista boa"),
            )
        logger.warning("threema_cadastro_indisponivel", chave=chave, erro=type(exc).__name__)
        return await _pelo_legado(
            d, esc, session, motivo=CADASTRO_INDISPONIVEL, com_nomes=com_nomes
        )
    if lido.usar_legado:
        return await _pelo_legado(d, esc, session, motivo=EMERGENCIA, com_nomes=com_nomes)
    if not lido.semeado:
        logger.warning("threema_cadastro_sem_semente", chave=chave)
        return await _pelo_legado(d, esc, session, motivo=SEM_SEMENTE, com_nomes=com_nomes)
    if not lido.ativo:
        res = Resolucao(
            chave=chave, ativo=False, via="cadastro", ids=(), motivo=DESLIGADO, escopo=esc
        )
    else:
        res = Resolucao(
            chave=chave,
            ativo=True,
            via="cadastro",
            ids=tuple(x.id for x in lido.destinos),
            destinos=lido.destinos,
            motivo=None if lido.destinos else NINGUEM_VALIDO,
            avisos=lido.avisos,
            escopo=esc,
        )
    _CACHE[(chave, esc)] = (time.monotonic(), res)
    return res


async def ativo(chave: str, *, session: AsyncSession | None = None) -> bool:
    """O envio está ligado agora? (a conferência e o worker perguntam)."""
    return (await resolver(chave, session=session)).ativo


async def quem_recebe(
    chave: str, *, escopo: object | None = None, session: AsyncSession | None = None
) -> Resolucao:
    """A resolução com os nomes de quem recebe (para a tela)."""
    return await resolver(chave, escopo=escopo, session=session, com_nomes=True)


async def escopo_das_regras(
    session: AsyncSession, chave: str
) -> dict[uuid.UUID, tuple[str, ...]] | None:
    """Item 1 (F4/C11): a lista própria de cada regra de status no Cadastro,
    num formato comparável (`("grupo:<id>", "usuario:<id>")`, sem ordem e sem
    ID do Threema) — a junção das regras repetidas da aba Status só junta
    regras com o MESMO escopo, e o log da junção o leva.

    Regra fora do mapa = usa a lista padrão do envio. None = o envio ainda
    segue o 👤 de cada regra (não migrado ou sem semente): quem compara usa o
    👤 (a mesma fonte do `legado`). A emergência não conta: ela não muda o
    que está guardado no Cadastro. Só leitura, com a sessão de quem chama."""
    d = _envio(chave)
    if d.escopo_tipo != "regra_status" or not d.migrado:
        return None
    linha = await session.get(ThreemaEnvio, d.chave)
    if linha is None or linha.semeado_em is None:
        return None
    out: dict[uuid.UUID, set[str]] = {}
    for r in (
        await session.execute(
            select(ThreemaEnvioDestinatario).where(
                ThreemaEnvioDestinatario.envio_chave == d.chave,
                ThreemaEnvioDestinatario.logistica_status_id.is_not(None),
            )
        )
    ).scalars():
        ref = r.user_id or r.grupo_id or r.contato_id
        out.setdefault(r.logistica_status_id, set()).add(f"{r.tipo}:{ref}")
    return {regra: tuple(sorted(refs)) for regra, refs in out.items()}


# ─────────────────────────────────────────────────────────── envio


def _dia_brt() -> str:
    return datetime.now(_FUSO).strftime("%Y%m%d")


async def _sino_ninguem(d: EnvioDef, res: Resolucao) -> None:
    """Acende o sino dos administradores, um por envio e dia. M-19 (main, 23/11/2026): UM alerta
    ADM, que todo administrador vê (antes, uma cópia por administrador).
    Melhor esforço: o sino nunca derruba o ponto de envio."""
    from app import db as _db
    from app.models import AlertSeverity, AlertType
    from app.models.alert import ESCOPO_ADM
    from app.services.alerts import emit_alert

    try:
        async with asyncio.timeout(_TIMEOUT_SINO_S), _db.session_scope() as s:
            detalhe = "; ".join(res.avisos[:5])
            await emit_alert(
                s,
                type=AlertType.GENERIC,
                severity=AlertSeverity.ERROR,
                title=f"Threema: ninguém recebe '{d.nome}'",
                message=(
                    "Este aviso não está saindo: ninguém da lista em Cadastros › Envios "
                    "Threema tem Threema válido e ativo." + (f" ({detalhe})" if detalhe else "")
                ),
                payload={"chave": d.chave, "escopo": res.escopo, "link": "/envios-threema"},
                dedupe_key=f"threema_ninguem:{d.chave}:{_dia_brt()}",
                escopo=ESCOPO_ADM,
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("threema_sino_ninguem_falhou", chave=d.chave, erro=type(exc).__name__)


async def enviar(
    chave: str,
    texto: str,
    *,
    escopo: object | None = None,
    origem: str | None = None,
    forcar_para: Sequence[str] | str | None = None,
    contexto: str | None = None,
    client=None,
    session: AsyncSession | None = None,
    um_por_vez: bool = False,
) -> dict:
    """Resolve quem recebe e manda. Devolve {sent, failed, via, motivo}.

    - `forcar_para` (teste ou admin): manda para estes IDs, ignorando o
      `ativo` (via `forcado`).
    - Desligado: não chama nada e não grava registro (só log).
    - Ninguém para receber: grava o registro `ok=false`
      (`sem_destinatario_valido`); se o Cadastro disse ninguém válido, acende
      o sino dos admins. NUNCA chama `send_to_all([])`.
    - Exceções do envio sobem como hoje: cada ponto mantém o seu try/except e
      a sua regra de carimbo.
    - `um_por_vez` (a Conferência, F2/C9e): um `send_to_all` por destinatário
      (um lote do registro por pessoa, como o ponto já fazia). O
      `send_to_all` só segura o erro do Threema (ThreemaSendError): um timeout
      de rede no 2º escaparia depois de o 1º já ter recebido, e o ponto, sem
      carimbo, reenviaria a todos. Aqui o erro de um (rede ou configuração)
      vira `failed` daquele e NÃO sobe; a resposta ganha `erro` (o último).

    O client sai de `threema.ThreemaClient` (pelo módulo, para os fakes dos
    testes valerem) com o remetente do catálogo, e o `send_to_all` é chamado
    com os IDs POSICIONAIS (os fakes não têm `**kw`)."""
    d = _envio(chave)
    esc = _escopo(escopo)
    if forcar_para is not None:
        bruto = forcar_para if isinstance(forcar_para, str) else ",".join(forcar_para)
        res = Resolucao(
            chave=chave,
            ativo=True,
            via="forcado",
            ids=_dedupe(threema.parse_recipients(bruto)),
            escopo=esc,
        )
    else:
        res = await resolver(chave, escopo=esc, session=session)
        if not res.ativo:
            logger.info("threema_envio_desligado", chave=chave, motivo=res.motivo, via=res.via)
            return {"sent": [], "failed": [], "via": res.via, "motivo": DESLIGADO}
    cli = client if client is not None else threema.ThreemaClient(contexto=contexto or d.remetente)
    if not res.ids:
        with threema_registro.envio(chave, escopo=esc, origem=origem, via=res.via):
            await threema_registro.gravar(
                contexto=getattr(cli, "contexto", None),
                canal=getattr(cli, "canal", None),
                texto=texto,
                tentativas=[
                    threema_registro.Tentativa(None, False, None, None, "sem_destinatario_valido")
                ],
                segredos=(getattr(cli, "secret", "") or "",),
            )
        if res.motivo == NINGUEM_VALIDO:
            await _sino_ninguem(d, res)
        logger.warning(
            "threema_envio_sem_destinatarios", chave=chave, via=res.via, motivo=res.motivo
        )
        return {"sent": [], "failed": [], "via": res.via, "motivo": "sem_destinatarios"}
    erro: str | None = None
    with threema_registro.envio(chave, escopo=esc, origem=origem, via=res.via):
        if not um_por_vez:
            resultado = await cli.send_to_all(texto, list(res.ids))
        else:
            resultado = {"sent": [], "failed": []}
            for rid in res.ids:
                try:
                    um = await cli.send_to_all(texto, [rid]) or {}
                except Exception as exc:  # noqa: BLE001 — um destinatário não derruba os outros
                    erro = (
                        str(exc)
                        if isinstance(exc, threema.ThreemaConfigError)
                        else f"{type(exc).__name__}: {exc}"[:200]
                    )
                    logger.warning(
                        "threema_envio_um_falhou", chave=chave, para=mascara(rid), erro=erro
                    )
                    um = {"sent": [], "failed": [rid]}
                resultado["sent"] += list(um.get("sent") or [])
                resultado["failed"] += list(um.get("failed") or [])
    resultado = resultado or {}
    out = {
        "sent": list(resultado.get("sent") or []),
        "failed": list(resultado.get("failed") or []),
        "via": res.via,
        "motivo": None,
    }
    if um_por_vez:
        out["erro"] = erro
    return out


# ─────────────────────────────────────────────────────────── semente


def _gerenciados_do_servidor(fatia: str | None = None) -> list[EnvioDef]:
    # Pelo CATALOGO (a mesma fonte do `resolver`), na ordem do catálogo.
    defs = [CATALOGO[d.chave] for d in ENVIOS]
    return [
        d
        for d in defs
        if d.onde_roda == "servidor" and d.gerenciado and (fatia is None or d.fatia == fatia)
    ]


def _selecionar(fatia: str | None, chaves: Sequence[str] | None) -> list[EnvioDef]:
    if fatia is not None and fatia not in FATIAS:
        raise CadastroError(f"fatia desconhecida: {fatia}")
    base = _gerenciados_do_servidor(fatia)
    if not chaves:
        return base
    por_chave = {d.chave: d for d in base}
    fora = [c for c in chaves if c not in por_chave]
    if fora:
        raise CadastroError(
            "chave fora da seleção (não existe, não é do servidor, não é gerenciada "
            f"ou não é desta fatia): {', '.join(fora)}"
        )
    return [por_chave[c] for c in chaves]


async def _regras_com_pessoa(session: AsyncSession) -> list[LogisticaStatus]:
    """Regras de status com 👤 (com ou sem Mensagem Threema)."""
    regras = (
        (await session.execute(select(LogisticaStatus).order_by(LogisticaStatus.created_at)))
        .scalars()
        .all()
    )
    return [r for r in regras if threema.parse_recipients(r.threema_recipients)]


@dataclass
class Ref:
    """Um ID do legado já mapeado para o Cadastro."""

    threema_id: str
    nome: str
    # usuario | contato | contato_novo
    tipo: str
    ref_id: uuid.UUID | None = None


@dataclass
class PlanoEnvio:
    chave: str
    nome: str
    escopo: str | None
    # semear | ja_semeado | recusado
    situacao: str
    fonte: str = ""
    grupo: str | None = None
    refs: list[Ref] = field(default_factory=list)
    # Liga/desliga que a semente grava (só nos envios de `_lista_vazia_desliga`
    # com `ativo` próprio): False = a lista de hoje saiu vazia (o desligado de
    # hoje); None = segue o interruptor de hoje (ligado). Sem valor nos outros
    # envios (não mexe).
    ativo: bool | None = None
    grava_ativo: bool = False
    # A lista de hoje saiu vazia num envio de `_lista_vazia_desliga`: nasce
    # sem linha nenhuma (calado, como hoje).
    vazia: bool = False


@dataclass
class ResultadoSemente:
    fatia: str
    aplicado: bool = False
    planos: list[PlanoEnvio] = field(default_factory=list)
    contatos_novos: dict[str, Ref] = field(default_factory=dict)
    grupos: dict[str, list[Ref]] = field(default_factory=dict)
    casos: list[str] = field(default_factory=list)
    erros: list[str] = field(default_factory=list)
    # Grupos que já existem com outras pessoas e que esta rodada refaz
    # (`sobrescrever`, quando só chaves desta rodada usam o grupo).
    grupos_refazer: set[str] = field(default_factory=set)


class _Mapa:
    """ID do Threema → usuário ativo, contato existente ou contato novo (§3.4)."""

    def __init__(self, users: Sequence[User], contatos: Sequence[ThreemaContato], nomes_env):
        self.ativos: dict[str, list[User]] = {}
        self.inativos: dict[str, list[User]] = {}
        for u in users:
            rid = (u.threema or "").strip().upper()
            if not rid:
                continue
            if _usuario_valido(u):
                self.ativos.setdefault(rid, []).append(u)
            else:
                self.inativos.setdefault(rid, []).append(u)
        self.contatos = {c.threema_id: c for c in contatos}
        self.nomes_env = {e["id"]: e["nome"] for e in nomes_env}
        self.casos: list[str] = []
        self.novos: dict[str, Ref] = {}

    def ref(self, rid: str) -> Ref | None:
        if not threema.threema_id_valido(rid):
            self.casos.append(f"{mascara(rid)} não tem o formato do Threema: fica de fora")
            return None
        ativos = self.ativos.get(rid, [])
        if ativos:
            ordem = sorted(ativos, key=lambda u: (u.role != UserRole.ADMIN, u.created_at))
            u = ordem[0]
            if len(ordem) > 1:
                outros = ", ".join(x.name or x.email for x in ordem[1:])
                self.casos.append(
                    f"{mascara(rid)} é de {len(ordem)} usuários ativos: ficou com "
                    f"{u.name or u.email} (antes: admin, depois o mais antigo; também: {outros})"
                )
            return Ref(rid, u.name or u.email, "usuario", u.id)
        c = self.contatos.get(rid)
        if c is not None:
            if not c.ativo:
                self.casos.append(
                    f"{mascara(rid)} é o contato {c.nome}, que está INATIVO: o Cadastro não "
                    "manda para ele (o conferir vai mostrar a diferença)"
                )
            return Ref(rid, c.nome, "contato", c.id)
        if rid in self.novos:
            return self.novos[rid]
        nome = self.nomes_env.get(rid) or f"Sem nome ({mascara(rid)})"
        de_quem = self.inativos.get(rid)
        extra = (
            f" (o ID é de {de_quem[0].name or de_quem[0].email}, fora de ativo)" if de_quem else ""
        )
        self.casos.append(f"{mascara(rid)} não é de ninguém ativo: vira o contato '{nome}'{extra}")
        novo = Ref(rid, nome[:80], "contato_novo")
        self.novos[rid] = novo
        return novo


async def _membros_do_grupo_no_banco(session: AsyncSession, grupo: ThreemaGrupo) -> list[str]:
    membros = (
        (
            await session.execute(
                select(ThreemaGrupoMembro).where(ThreemaGrupoMembro.grupo_id == grupo.id)
            )
        )
        .scalars()
        .all()
    )
    ids: list[str] = []
    for m in membros:
        if m.user_id:
            u = await session.get(User, m.user_id)
            ids.append((u.threema or "").strip().upper() if u else "?")
        elif m.contato_id:
            c = await session.get(ThreemaContato, m.contato_id)
            ids.append(c.threema_id if c else "?")
    return ids


async def semear(
    session: AsyncSession,
    *,
    fatia: str,
    chaves: Sequence[str] | None = None,
    aplicar: bool = False,
    sobrescrever: bool = False,
) -> ResultadoSemente:
    """Semeia o Cadastro das chaves da fatia a partir de `legado(chave)` — a
    mesma função do envio. Sem `aplicar` não grava nada (só o plano).

    Roda com o código DA FATIA, nunca com o de antes: a regra da semente pode
    mudar na própria fatia (a F2 passou a desligar a linha do Informar vazia
    de envio automático e a tirar do grupo a lista que nasce desligada; com o
    código da F1, a linha `denuncia_robo` vazia recusava a F2 inteira pelo
    grupo "IA de Chamado"). Do jeito de sempre:
      1. publicar a fatia — chave migrada sem semente usa o legado exato, e a
         lista antiga já está congelada (o PUT do Informar responde 410);
      2. `semear --fatia Fn` (plano), depois `--aplicar` (sem
         `--sobrescrever`);
      3. `conferir --fatia Fn` com 0 diferenças.
    (Também serve rodar 2 e 3 num container avulso da imagem nova antes de
    trocar os serviços: o código velho não lê o Cadastro dessas chaves.)

    - Item 1: uma lista por regra com 👤 (com ou sem mensagem); a padrão é o
      THREEMA_RECIPIENTS de hoje.
    - Envio com `Legado.grupo`: a lista vira o grupo (mesmo nome com pessoas
      diferentes, na rodada ou no banco, PARA com erro e nada é gravado).
    - Não mexe em `ativo` (NULL = interruptor de hoje), fora o envio cujo
      desligado de hoje é a lista vazia (`_lista_vazia_desliga`: o .env, a
      linha do Informar de um envio automático ou a lista do robô na
      Ouvidoria): com a lista de hoje vazia ele nasce `ativo=false` (calado,
      como hoje;
      NULL seria "ligado com ninguém", com registro e sino a cada rodada) e
      sem entrar no grupo dele; com gente, `ativo` NULL. O 27 (a caixinha do
      robô é o liga/desliga) com a lista vazia nasce só sem linha nenhuma.
      Carimba `semeado_em`.
    - Chave já semeada fica como está (rodar duas vezes não muda nada), a
      menos de `sobrescrever` — que só vale para chave ainda não migrada no
      código em execução; chave migrada se acerta na tela.
    Commit fica com quem chama. Erros levantam CadastroError com o resultado
    em `.args[1]`."""
    if fatia not in FATIAS or fatia == "mac":
        raise CadastroError(f"fatia inválida para semear: {fatia} (use F1, F2, F3 ou F4)")
    selecionadas = _selecionar(fatia, chaves)
    await sincronizar(session)
    res = ResultadoSemente(fatia=fatia, aplicado=False)
    users = (await session.execute(select(User).where(User.threema.is_not(None)))).scalars().all()
    contatos = (await session.execute(select(ThreemaContato))).scalars().all()
    s = get_settings()
    mapa = _Mapa(
        users, contatos, threema.parse_recipient_directory(s.threema_recipient_names, None)
    )
    linhas = {
        r.chave: r
        for r in (
            await session.execute(
                select(ThreemaEnvio)
                .where(ThreemaEnvio.chave.in_([d.chave for d in selecionadas]))
                .execution_options(populate_existing=True)
            )
        ).scalars()
    }
    regras = None
    usos_grupo: dict[str, list[tuple[str, list[Ref]]]] = {}
    for d in selecionadas:
        linha = linhas[d.chave]
        if sobrescrever and d.migrado:
            res.erros.append(
                f"{d.chave}: --sobrescrever recusado — o envio já está migrado no código em "
                "execução (o Cadastro é a fonte; refaça pela tela)"
            )
            res.planos.append(PlanoEnvio(d.chave, d.nome, None, "recusado"))
            continue
        if linha.semeado_em is not None and not sobrescrever:
            res.planos.append(PlanoEnvio(d.chave, d.nome, None, "ja_semeado"))
            continue
        escopos: list[LogisticaStatus | None] = [None]
        if d.escopo_tipo == "regra_status":
            if regras is None:
                regras = await _regras_com_pessoa(session)
            escopos.extend(regras)
        for regra in escopos:
            if regra is None:
                leg = await legado(d.chave, session=session)
                ids, fonte, grupo = leg.ids, leg.fonte, d.legado.grupo
            else:
                ids = _dedupe(threema.parse_recipients(regra.threema_recipients))
                fonte = f"👤 da regra '{regra.status_plataforma or regra.id}'"
                grupo = None
            # ID repetido na lista de hoje vira UMA linha (o índice único não
            # deixa duas; a conferência compara conjuntos).
            refs = [r for r in (mapa.ref(rid) for rid in _dedupe(ids)) if r is not None]
            vazia_desliga = regra is None and _lista_vazia_desliga(d)
            if vazia_desliga and not ids:
                # Lista de hoje vazia = o envio nasce desligado e sem ninguém:
                # não entra no grupo. Ex.: a linha `denuncia_robo` salva vazia
                # (o 44 não lê a reserva `chamados_ia`) não pode virar "IA de
                # Chamado com ninguém" nem brigar com o 15, que lê a
                # `chamados_ia` com gente. O 27 não tem `ativo` próprio (a
                # caixinha do robô): sem linha nenhuma, o
                # `_interruptor_cadastro` o lê desligado.
                grupo = None
            grava_ativo = vazia_desliga and d.interruptor == "proprio"
            plano = PlanoEnvio(
                d.chave,
                d.nome,
                str(regra.id) if regra is not None else None,
                "semear",
                fonte=fonte,
                grupo=grupo,
                refs=refs,
                ativo=(False if not ids else None) if grava_ativo else None,
                grava_ativo=grava_ativo,
                vazia=vazia_desliga and not ids,
            )
            res.planos.append(plano)
            if grupo:
                usos_grupo.setdefault(grupo, []).append((d.chave, refs))
    res.casos = list(dict.fromkeys(mapa.casos))
    res.contatos_novos = dict(mapa.novos)
    for nome, usos in usos_grupo.items():
        conjuntos = {frozenset(r.threema_id for r in refs) for _, refs in usos}
        if len(conjuntos) > 1:
            detalhe = "; ".join(
                f"{chave}: {', '.join(mascara(r.threema_id) for r in refs) or 'ninguém'}"
                for chave, refs in usos
            )
            res.erros.append(f"grupo '{nome}' sairia com pessoas diferentes — {detalhe}")
            continue
        res.grupos[nome] = usos[0][1]
        existente = (
            await session.execute(
                select(ThreemaGrupo).where(func.lower(ThreemaGrupo.nome) == nome.lower())
            )
        ).scalar_one_or_none()
        if existente is not None:
            no_banco = await _membros_do_grupo_no_banco(session, existente)
            if set(no_banco) == set(conjuntos.pop()):
                continue
            usam = (
                (
                    await session.execute(
                        select(ThreemaEnvioDestinatario.envio_chave)
                        .where(ThreemaEnvioDestinatario.grupo_id == existente.id)
                        .distinct()
                    )
                )
                .scalars()
                .all()
            )
            desta_rodada = {p.chave for p in res.planos if p.situacao == "semear"}
            fora = sorted(set(usam) - desta_rodada)
            if sobrescrever and not fora:
                # semente velha: só chaves desta rodada usam o grupo → refaz
                res.grupos_refazer.add(nome)
                continue
            res.erros.append(
                f"grupo '{nome}' já existe com outras pessoas "
                f"({', '.join(mascara(x) for x in no_banco) or 'ninguém'})"
                + (f" e é usado também por {', '.join(fora)}" if fora else "")
                + ": acerte na tela"
                + ("" if sobrescrever else " ou rode com --sobrescrever")
            )
    if res.erros:
        raise CadastroError("semente recusada", res)
    if aplicar:
        await _aplicar_semente(session, res)
        res.aplicado = True
    return res


async def _aplicar_semente(session: AsyncSession, res: ResultadoSemente) -> None:
    autor = f"semente {res.fatia}"
    contato_id: dict[str, uuid.UUID] = {}
    for rid, ref in res.contatos_novos.items():
        c = ThreemaContato(threema_id=rid, nome=ref.nome, ativo=True, origem="semeadura")
        session.add(c)
        await session.flush()
        contato_id[rid] = c.id

    def _destino_ref(ref: Ref) -> dict:
        if ref.tipo == "usuario":
            return {"tipo": "usuario", "user_id": ref.ref_id}
        cid = ref.ref_id if ref.tipo == "contato" else contato_id[ref.threema_id]
        return {"tipo": "contato", "contato_id": cid}

    grupo_id: dict[str, uuid.UUID] = {}
    for nome, refs in res.grupos.items():
        g = (
            await session.execute(
                select(ThreemaGrupo).where(func.lower(ThreemaGrupo.nome) == nome.lower())
            )
        ).scalar_one_or_none()
        if g is None:
            g = ThreemaGrupo(nome=nome, descricao=f"Criado pela {autor} com a lista de hoje.")
            session.add(g)
            await session.flush()
        elif nome in res.grupos_refazer:
            await session.execute(
                delete(ThreemaGrupoMembro).where(ThreemaGrupoMembro.grupo_id == g.id)
            )
        else:
            grupo_id[nome] = g.id
            continue
        for i, ref in enumerate(refs):
            session.add(ThreemaGrupoMembro(grupo_id=g.id, ordem=i, **_destino_ref(ref)))
        grupo_id[nome] = g.id
    await session.flush()

    a_semear = [p for p in res.planos if p.situacao == "semear"]
    for chave in dict.fromkeys(p.chave for p in a_semear):
        await session.execute(
            delete(ThreemaEnvioDestinatario).where(ThreemaEnvioDestinatario.envio_chave == chave)
        )
    for p in a_semear:
        regra = _uuid(p.escopo)
        if p.grupo:
            session.add(
                ThreemaEnvioDestinatario(
                    envio_chave=p.chave,
                    logistica_status_id=regra,
                    tipo="grupo",
                    grupo_id=grupo_id[p.grupo],
                    ordem=0,
                    criado_por=autor,
                )
            )
            continue
        for i, ref in enumerate(p.refs):
            session.add(
                ThreemaEnvioDestinatario(
                    envio_chave=p.chave,
                    logistica_status_id=regra,
                    ordem=i,
                    criado_por=autor,
                    **_destino_ref(ref),
                )
            )
    await session.flush()
    if contato_id:
        # contato novo entra também no espelho antigo (services/threema, C4)
        await threema.espelhar_contatos(session)
    agora = datetime.now(UTC)
    liga_desliga = {p.chave: p.ativo for p in a_semear if p.grava_ativo}
    for chave in dict.fromkeys(p.chave for p in a_semear):
        valores: dict = {"semeado_em": agora, "updated_at": func.now()}
        if chave in liga_desliga:
            # Antes de migrar, só a semente escreve o `ativo` do envio do
            # servidor (a tela recusa envio não migrado): refazer com
            # --sobrescrever acompanha a lista de hoje.
            valores["ativo"] = liga_desliga[chave]
        await session.execute(
            update(ThreemaEnvio).where(ThreemaEnvio.chave == chave).values(**valores)
        )
    await session.flush()


def _texto_semente(res: ResultadoSemente) -> list[str]:
    out = [
        f"semear {res.fatia}: "
        + ("APLICADO" if res.aplicado else "só o plano (rode com --aplicar para gravar)")
    ]
    for p in res.planos:
        rotulo = f"{p.chave}" + (f" [regra {p.escopo}]" if p.escopo else "")
        if p.situacao != "semear":
            out.append(f"  - {rotulo}: {p.situacao.replace('_', ' ')}")
            continue
        quem = ", ".join(f"{r.nome} ({mascara(r.threema_id)})" for r in p.refs) or "ninguém"
        via = f"grupo '{p.grupo}': " if p.grupo else ""
        desligado = (
            f" — nasce DESLIGADO (hoje a lista vazia {_lista_de_hoje(CATALOGO[p.chave])} é o"
            " desligado; ligue na tela quando escolher alguém)"
            if p.grava_ativo and p.ativo is False
            else f" — fica CALADO enquanto a lista estiver vazia (hoje a lista vazia"
            f" {_lista_de_hoje(CATALOGO[p.chave])} é o desligado)"
            if p.vazia
            else ""
        )
        out.append(f"  - {rotulo} — {p.nome}: {via}{quem}{desligado}  [hoje: {p.fonte}]")
    for rid, ref in res.contatos_novos.items():
        out.append(f"  + contato novo: {ref.nome} ({mascara(rid)})")
    for caso in res.casos:
        out.append(f"  ! {caso}")
    for erro in res.erros:
        out.append(f"  ✗ {erro}")
    return out


# ─────────────────────────────────────────────────────────── conferência


@dataclass
class Conferencia:
    chave: str
    nome: str
    escopo: str | None
    migrado: bool
    # igual | diferente | sem_semente
    situacao: str
    fonte: str = ""
    so_no_legado: list[Destino] = field(default_factory=list)
    so_no_cadastro: list[Destino] = field(default_factory=list)
    ativo_legado: bool = True
    ativo_cadastro: bool | None = None
    # O legado está desligado só porque a lista de hoje (.env ou linha do
    # Informar, `_lista_vazia_desliga`) está vazia.
    desligado_pela_lista: bool = False


async def conferir(
    session: AsyncSession, *, fatia: str | None = None, chaves: Sequence[str] | None = None
) -> list[Conferencia]:
    """Compara, por chave e escopo, `legado(chave)` com a expansão do
    Cadastro (quem recebe e se está ligado). Chave sem semente sai como
    `sem_semente` (usa o legado). No envio de `_lista_vazia_desliga`, a lista
    de hoje vazia conta como desligado: Cadastro ligado sem ninguém é
    diferença.
    Só leitura (fora a sincronização)."""
    selecionadas = _selecionar(fatia, chaves)
    await sincronizar(session)
    linhas = {
        r.chave: r
        for r in (
            await session.execute(select(ThreemaEnvio).execution_options(populate_existing=True))
        ).scalars()
    }
    out: list[Conferencia] = []
    regras_pessoa: list[LogisticaStatus] | None = None
    for d in selecionadas:
        linha = linhas.get(d.chave)
        escopos: list[str | None] = [None]
        if d.escopo_tipo == "regra_status":
            if regras_pessoa is None:
                regras_pessoa = await _regras_com_pessoa(session)
            com_linha = (
                (
                    await session.execute(
                        select(ThreemaEnvioDestinatario.logistica_status_id)
                        .where(
                            ThreemaEnvioDestinatario.envio_chave == d.chave,
                            ThreemaEnvioDestinatario.logistica_status_id.is_not(None),
                        )
                        .distinct()
                    )
                )
                .scalars()
                .all()
            )
            escopos.extend(
                dict.fromkeys([*(str(r.id) for r in regras_pessoa), *map(str, com_linha)])
            )
        for esc in escopos:
            leg = await legado(d.chave, escopo=esc, session=session)
            # Lista de hoje vazia = o desligado de hoje desses envios: o
            # Cadastro tem de estar desligado também (ligado com ninguém
            # acende o sino a cada rodada, o que hoje não acontece).
            leg_ativo = leg.ativo and (bool(leg.ids) or not _lista_vazia_desliga(d))
            if linha is None or linha.semeado_em is None:
                out.append(
                    Conferencia(
                        d.chave,
                        d.nome,
                        esc,
                        d.migrado,
                        "sem_semente",
                        fonte=leg.fonte,
                        ativo_legado=leg_ativo,
                        desligado_pela_lista=leg.ativo and not leg_ativo,
                    )
                )
                continue
            # A emergência não conta e o desligado também mostra a lista: a
            # conferência compara o que ESTÁ no Cadastro com o legado.
            lido = await _ler_cadastro(d, esc, session, olhar_emergencia=False, so_lista=True)
            cad_ids = {x.id for x in lido.destinos}
            leg_ids = set(leg.ids)
            so_leg = [x for x in await _nomear(session, leg.ids) if x.id not in cad_ids]
            so_cad = [x for x in lido.destinos if x.id not in leg_ids]
            igual = not so_leg and not so_cad and leg_ativo == lido.ativo
            out.append(
                Conferencia(
                    d.chave,
                    d.nome,
                    esc,
                    d.migrado,
                    "igual" if igual else "diferente",
                    fonte=leg.fonte,
                    so_no_legado=so_leg,
                    so_no_cadastro=so_cad,
                    ativo_legado=leg_ativo,
                    ativo_cadastro=lido.ativo,
                    desligado_pela_lista=leg.ativo and not leg_ativo,
                )
            )
    return out


def _texto_conferencia(itens: Sequence[Conferencia]) -> list[str]:
    out = []
    for c in itens:
        rotulo = c.chave + (f" [regra {c.escopo}]" if c.escopo else "")
        estado = "migrado" if c.migrado else "não migrado"
        if c.situacao == "sem_semente":
            ligado = (
                ""
                if c.ativo_legado
                else f" · desligado hoje (lista {_lista_de_hoje(CATALOGO[c.chave])} vazia)"
                if c.desligado_pela_lista
                else " · desligado hoje"
            )
            out.append(
                f"  · {rotulo} — {c.nome}: {estado}, sem semente (usa o legado: {c.fonte}){ligado}"
            )
        elif c.situacao == "igual":
            out.append(f"  ✓ {rotulo} — {c.nome}: {estado}, Cadastro igual ao legado")
        else:
            partes = []
            if c.so_no_legado:
                partes.append(
                    "só no legado: "
                    + ", ".join(f"{x.nome or '?'} ({mascara(x.id)})" for x in c.so_no_legado)
                )
            if c.so_no_cadastro:
                partes.append(
                    "só no Cadastro: "
                    + ", ".join(f"{x.nome or '?'} ({mascara(x.id)})" for x in c.so_no_cadastro)
                )
            if c.ativo_legado != c.ativo_cadastro:
                partes.append(
                    f"ligado: legado={'sim' if c.ativo_legado else 'não'}"
                    + (
                        f" (lista {_lista_de_hoje(CATALOGO[c.chave])} vazia)"
                        if c.desligado_pela_lista
                        else ""
                    )
                    + f", Cadastro={'sim' if c.ativo_cadastro else 'não'}"
                    + (
                        " — desligue em Cadastros › Envios Threema (ligado sem ninguém"
                        " acende o sino dos admins a cada rodada)"
                        if c.desligado_pela_lista and c.ativo_cadastro
                        else ""
                    )
                )
            out.append(f"  ✗ {rotulo} — {c.nome}: {estado}; " + "; ".join(partes))
    return out


# ─────────────────────────────────────────────────────────── linha de comando


def _lista(valor: str | None) -> list[str] | None:
    if not valor:
        return None
    return [c.strip() for c in valor.split(",") if c.strip()]


async def _cmd_sincronizar(_args: argparse.Namespace, imprimir: Callable[[str], None]) -> int:
    from app import db as _db

    async with _db.session_scope() as session:
        resumo = await sincronizar(session)
    imprimir(
        f"threema_envios: catálogo com {len(ENVIOS)} envios — {resumo['novos']} novo(s), "
        f"{resumo['atualizados']} atualizado(s), {resumo['descontinuados']} descontinuado(s)"
    )
    return 0


async def _cmd_semear(args: argparse.Namespace, imprimir: Callable[[str], None]) -> int:
    from app import db as _db

    try:
        async with _db.session_scope() as session:
            res = await semear(
                session,
                fatia=args.fatia,
                chaves=_lista(args.chaves),
                aplicar=args.aplicar,
                sobrescrever=args.sobrescrever,
            )
    except CadastroError as e:
        if len(e.args) > 1 and isinstance(e.args[1], ResultadoSemente):
            for linha in _texto_semente(e.args[1]):
                imprimir(linha)
        imprimir(f"semear: RECUSADO, nada foi gravado ({e.args[0]})")
        return 1
    for linha in _texto_semente(res):
        imprimir(linha)
    return 0


async def _cmd_conferir(args: argparse.Namespace, imprimir: Callable[[str], None]) -> int:
    from app import db as _db

    chaves = _lista(args.chaves)
    try:
        async with _db.session_scope() as session:
            itens = await conferir(session, fatia=args.fatia, chaves=chaves)
    except CadastroError as e:
        imprimir(f"conferir: {e.args[0]}")
        return 1
    filtrado = bool(args.fatia or chaves)
    migrados = sum(1 for d in _selecionar(args.fatia, chaves) if d.migrado)
    imprimir(
        f"conferir: {len({c.chave for c in itens})} envio(s) do servidor, {migrados} migrado(s)"
        + (f", fatia {args.fatia}" if args.fatia else "")
    )
    for linha in _texto_conferencia(itens):
        imprimir(linha)
    diferentes = [c for c in itens if c.situacao == "diferente"]
    sem_semente = [c for c in itens if c.situacao == "sem_semente"]
    imprimir(f"diferenças: {len(diferentes)} · sem semente: {len(sem_semente)}")
    # Com --fatia/--chaves, chave sem semente também reprova: é o portão da fatia.
    return 1 if diferentes or (filtrado and sem_semente) else 0


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m app.services.threema_envios",
        description="Cadastro dos envios do Threema (Cadastros › Envios Threema).",
    )
    sub = p.add_subparsers(dest="comando", required=True)
    sub.add_parser("sincronizar", help="upsert do catálogo do código em threema_envio")
    sem = sub.add_parser("semear", help="semeia o Cadastro de uma fatia a partir do legado")
    sem.add_argument("--fatia", required=True, choices=("F1", "F2", "F3", "F4"))
    sem.add_argument("--chaves", help="só estas chaves da fatia (separadas por vírgula)")
    sem.add_argument("--aplicar", action="store_true", help="grava (sem isto, só o plano)")
    sem.add_argument(
        "--sobrescrever",
        action="store_true",
        help="refaz a semente de chave já semeada (só chave ainda não migrada)",
    )
    con = sub.add_parser("conferir", help="compara o legado com o Cadastro; exit 1 se diferir")
    con.add_argument("--fatia", choices=("F1", "F2", "F3", "F4"))
    con.add_argument("--chaves", help="só estas chaves (separadas por vírgula)")
    return p


_COMANDOS = {"sincronizar": _cmd_sincronizar, "semear": _cmd_semear, "conferir": _cmd_conferir}


async def executar(argv: Sequence[str], imprimir: Callable[[str], None] = print) -> int:
    """A linha de comando sem fechar o engine (o teste chama direto)."""
    args = _parser().parse_args(list(argv))
    return await _COMANDOS[args.comando](args, imprimir)


async def _main(argv: Sequence[str]) -> int:
    from app.db import engine

    try:
        return await executar(argv)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    sys.exit(asyncio.run(_main(sys.argv[1:])))
