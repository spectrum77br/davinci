"""O que o Claude (chat do chefe) consegue fazer no DaVinci via conector:
por enquanto só CRIAR tarefa (Eduardo, 08/09: "só criar por enquanto").

Regras:
  * responsável = quem o Claude disser (nome ou e-mail de usuário do DaVinci);
    sem responsável, é o dono do conector. Nome ambíguo ou desconhecido NÃO
    vira chute — devolve a lista pra o Claude perguntar. O casamento parcial
    é por PALAVRA ("ana" acha "Ana Paula" e "Ana Clara", nunca "Juliana") e
    ignora acento/maiúscula ("João" acha "Joao Silva").
  * quem não é admin só cria tarefa pra si mesmo (mesma regra da tela, onde
    só admin cria) — e nem fica sabendo quem mais existe.
  * data de início = hoje (Brasília). A tabela não tem coluna de prazo — o
    prazo falado vai pra Observação ("Prazo: dd/mm/aaaa").
  * o responsável recebe o mesmo aviso da tela (alerta + Telegram) —
    SEMPRE, inclusive quando é a própria pessoa (confirmação). Tudo numa
    transação só: se o aviso falhar, a tarefa ainda é
    gravada e o texto diz que o aviso não saiu — nunca "tente de novo" com a
    tarefa já criada (duplicaria).
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Tarefa, User, UserRole, UserStatus
from app.models.enums import AlertSeverity, AlertType
from app.services.alerts import emit_alert

logger = structlog.get_logger()

SAO_PAULO = ZoneInfo("America/Sao_Paulo")
MAX_TEXTO = 2000
MAX_NOMES_NA_RESPOSTA = 15

# Descrição que o Claude lê pra decidir quando e como chamar — em pt-BR e
# explicando o que perguntar antes (é o "manual" do comando).
TOOL_CRIAR_TAREFA: dict[str, Any] = {
    "name": "criar_tarefa",
    "title": "Criar tarefa no DaVinci",
    "description": (
        "Cria uma tarefa na aba Tarefas do DaVinci (sistema interno da empresa). "
        "Use quando o usuário pedir para anotar, registrar, criar ou mandar uma tarefa, "
        "inclusive por áudio ditado. A data de início é hoje. Se o usuário citar uma pessoa "
        "('para o Eduardo', 'manda pra Joana'), passe o nome em `responsavel`; se não citar, "
        "deixe vazio (a tarefa fica com o próprio usuário). Se citar prazo ('até sexta', "
        "'dia 20'), converta para AAAA-MM-DD em `prazo`. Se a resposta disser que o nome é "
        "ambíguo ou não existe, pergunte ao usuário e chame de novo. Não invente "
        "responsável nem prazo."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "descricao": {
                "type": "string",
                "description": (
                    "O que precisa ser feito, em português, do jeito que o usuário disse "
                    "(limpo de vícios de fala)."
                ),
            },
            "responsavel": {
                "type": "string",
                "description": (
                    "Nome (ou e-mail) da pessoa responsável, só se o usuário citou. "
                    "Vazio = o próprio usuário."
                ),
            },
            "prazo": {
                "type": "string",
                "description": "Prazo no formato AAAA-MM-DD, só se o usuário citou.",
            },
            "observacao": {
                "type": "string",
                "description": (
                    "Detalhes extras que não são a tarefa em si (contexto, links, valores)."
                ),
            },
        },
        "required": ["descricao"],
    },
    # Escrita, mas não destrutiva: o app do Claude mostra "Permitir" na 1ª
    # vez e deixa marcar "sempre" — a confirmação humana é bem-vinda.
    "annotations": {
        "title": "Criar tarefa no DaVinci",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
}


class TarefaInvalidaError(Exception):
    """Mensagem pro Claude repassar ao usuário (não é falha do sistema)."""


def _norm(s: str) -> str:
    """minúsculas, sem acento, espaços colapsados — o áudio vem 'João', o
    cadastro pode estar 'Joao'."""
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", s).strip().casefold()


def _casa_por_palavra(alvo: str, nome: str) -> bool:
    """'ana' casa 'Ana Paula'; 'ana paula' casa 'Ana Paula Souza'; 'ana' NÃO
    casa 'Juliana'. Cada palavra do alvo tem que ser igual (ou prefixo) de
    uma palavra do nome, na ordem."""
    palavras_alvo = _norm(alvo).split(" ")
    palavras_nome = _norm(nome).split(" ")
    i = 0
    for pa in palavras_alvo:
        while i < len(palavras_nome) and not palavras_nome[i].startswith(pa):
            i += 1
        if i >= len(palavras_nome):
            return False
        i += 1
    return True


async def usuarios_atribuiveis(session: AsyncSession) -> list[User]:
    """Quem pode ser responsável: ativo, não desligado, não usuário-sistema
    (mesmo filtro da lista de usuários da tela)."""
    return list(
        (
            await session.execute(
                select(User).where(
                    User.status == UserStatus.ACTIVE,
                    User.disabled_at.is_(None),
                    User.open_id.notlike("system:%"),
                )
            )
        )
        .scalars()
        .all()
    )


def _e_o_proprio(texto: str, dono: User) -> bool:
    alvo = _norm(texto)
    return alvo in (_norm(dono.name or ""), _norm(dono.email)) or (
        bool(dono.name) and _casa_por_palavra(texto, dono.name)
    )


async def _resolver_responsavel(session: AsyncSession, texto: str, dono: User) -> User:
    """Acha o usuário pelo nome/e-mail. Exato primeiro; depois por palavra.
    Mais de um candidato ou nenhum => TarefaInvalidaError com a lista (só
    nomes, no máximo MAX_NOMES_NA_RESPOSTA), pra o Claude perguntar em vez
    de chutar. Não-admin não vê a lista: só pode ser ele mesmo."""
    if not _norm(texto):
        return dono
    if _e_o_proprio(texto, dono):
        return dono
    if dono.role != UserRole.ADMIN:
        raise TarefaInvalidaError(
            "Este usuário só pode criar tarefas para si mesmo no DaVinci "
            "(atribuir a outras pessoas é só para administradores). Chame de novo "
            "sem `responsavel`."
        )
    ativos = await usuarios_atribuiveis(session)
    alvo = _norm(texto)
    exatos = [u for u in ativos if _norm(u.name or "") == alvo or _norm(u.email) == alvo]
    if len(exatos) == 1:
        return exatos[0]
    parciais = [u for u in ativos if u.name and _casa_por_palavra(texto, u.name)]
    if len(parciais) == 1:
        return parciais[0]
    nomes = sorted({u.name for u in (parciais or ativos) if u.name})[:MAX_NOMES_NA_RESPOSTA]
    if parciais:
        raise TarefaInvalidaError(
            f"Mais de uma pessoa combina com '{texto}': {', '.join(nomes)}. "
            "Pergunte ao usuário qual delas e chame de novo com o nome exato."
        )
    raise TarefaInvalidaError(
        f"Não existe usuário '{texto}' no DaVinci. Pessoas cadastradas: {', '.join(nomes)}. "
        "Pergunte ao usuário e chame de novo, ou deixe `responsavel` vazio para ficar com "
        "ele mesmo."
    )


def _parse_prazo(raw: str | None) -> date | None:
    s = (raw or "").strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    raise TarefaInvalidaError(
        f"Prazo '{raw}' não está em AAAA-MM-DD. Converta a data e chame de novo."
    )


def _texto(v: Any, campo: str) -> str:
    s = str(v or "").strip()
    if len(s) > MAX_TEXTO:
        raise TarefaInvalidaError(
            f"O campo `{campo}` está longo demais ({len(s)} caracteres; máximo {MAX_TEXTO}). "
            "Resuma e chame de novo."
        )
    return s


async def criar_tarefa(session: AsyncSession, *, dono: User, args: dict[str, Any]) -> str:
    """Executa o comando. Devolve o texto que o Claude mostra ao usuário.
    Levanta TarefaInvalidaError quando falta/erra informação (o Claude pergunta)."""
    descricao = _texto(args.get("descricao"), "descricao")
    if not descricao:
        raise TarefaInvalidaError(
            "Faltou a descrição da tarefa. Pergunte ao usuário o que precisa ser feito."
        )
    if dono.status != UserStatus.ACTIVE or dono.disabled_at is not None:
        raise TarefaInvalidaError("O usuário deste conector está inativo no DaVinci.")

    responsavel = await _resolver_responsavel(
        session, _texto(args.get("responsavel"), "responsavel"), dono
    )
    prazo = _parse_prazo(args.get("prazo"))
    obs = _texto(args.get("observacao"), "observacao")

    partes: list[str] = []
    if prazo:
        partes.append(f"Prazo: {prazo.strftime('%d/%m/%Y')}")
    if obs:
        partes.append(obs)
    partes.append("Criada pelo Claude (áudio/chat)")

    hoje = datetime.now(SAO_PAULO).date()
    t = Tarefa(
        responsavel_id=responsavel.id,
        data_inicio=hoje,
        tarefa=descricao,
        observacao=" · ".join(partes),
        created_by=dono.id,
    )
    session.add(t)
    await session.flush()

    # Aviso na MESMA transação da tarefa: se falhar, a tarefa fica (e o texto
    # avisa), em vez de "tente de novo" com a tarefa já gravada. Diferente da
    # tela, avisa SEMPRE — inclusive quando é pra própria pessoa: Eduardo
    # (08/09) testou ditando pra si e estranhou não chegar nada; aqui o aviso é
    # a confirmação de que o áudio virou tarefa.
    avisou = False
    if responsavel is not None:
        try:
            async with session.begin_nested():  # falha no aviso não desfaz a tarefa
                await emit_alert(
                    session,
                    user_id=responsavel.id,
                    type=AlertType.TAREFA_ATRIBUIDA,
                    title="📋 Nova tarefa atribuída a você",
                    severity=AlertSeverity.INFO,
                    message=t.tarefa,
                    payload={
                        "tarefa_id": str(t.id),
                        "atribuida_por": dono.name or dono.email,
                        "data_inicio": hoje.isoformat(),
                        "origem": "claude",
                    },
                    notify_telegram=True,
                )
            avisou = True
        except Exception as e:  # noqa: BLE001 — aviso é acessório
            logger.warning("tarefa_claude_aviso_falhou", tarefa=str(t.id), err=str(e)[:200])
    await session.commit()
    logger.info(
        "tarefa_created_via_claude",
        id=str(t.id),
        responsavel_id=str(responsavel.id),
        created_by=str(dono.id),
    )

    quem = responsavel.name or responsavel.email
    linhas = [f"Tarefa criada no DaVinci: {descricao}", f"Responsável: {quem}"]
    linhas.append(f"Início: {hoje.strftime('%d/%m/%Y')}")
    if prazo:
        linhas.append(f"Prazo: {prazo.strftime('%d/%m/%Y')} (na observação)")
    if avisou:
        linhas.append(
            "Você foi avisado(a) no DaVinci."
            if responsavel.id == dono.id
            else f"{quem} foi avisado(a) no DaVinci."
        )
    else:
        linhas.append(f"A tarefa foi gravada, mas não consegui avisar {quem} agora.")
    return "\n".join(linhas)


# ---- listar / concluir (Eduardo, 08/09: "perguntar sobre as tarefas que enviei") -----

TOOL_LISTAR_TAREFAS: dict[str, Any] = {
    "name": "listar_tarefas",
    "title": "Listar tarefas do DaVinci",
    "description": (
        "Lista tarefas da aba Tarefas do DaVinci. Use quando o usuário perguntar quais "
        "tarefas existem, o que está pendente, o que ele mandou/criou, ou as tarefas de "
        "alguém. Por padrão vem só as PENDENTES; `filtro` pode ser 'pendentes', "
        "'concluidas' ou 'todas'. Cada linha traz um código curto entre colchetes — use "
        "esse código em `concluir_tarefa`. Administrador vê tudo; usuário comum só as "
        "próprias."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "filtro": {
                "type": "string",
                "enum": ["pendentes", "concluidas", "todas"],
                "description": "Quais tarefas mostrar (padrão: pendentes).",
            },
            "responsavel": {
                "type": "string",
                "description": "Nome da pessoa, só se o usuário pediu as tarefas de alguém.",
            },
            "so_minhas": {
                "type": "boolean",
                "description": (
                    "true = só as tarefas que o próprio usuário criou (ex.: 'as que eu mandei')."
                ),
            },
            "dias": {
                "type": "integer",
                "description": "Só tarefas iniciadas nos últimos N dias (padrão: sem limite).",
            },
        },
    },
    "annotations": {
        "title": "Listar tarefas do DaVinci",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
}

TOOL_CONCLUIR_TAREFA: dict[str, Any] = {
    "name": "concluir_tarefa",
    "title": "Marcar tarefa como concluída no DaVinci",
    "description": (
        "Marca uma tarefa como concluída (data de conclusão = hoje, ou a data informada). "
        "Passe o código curto que veio em `listar_tarefas` em `codigo`, ou um trecho da "
        "descrição em `descricao`. Se houver mais de uma tarefa parecida, a resposta lista "
        "as opções — pergunte ao usuário e chame de novo com o código. Confirme com o "
        "usuário qual tarefa é antes de concluir; a ação pode ser desfeita na tela."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "codigo": {"type": "string", "description": "Código curto da tarefa (8 letras)."},
            "descricao": {
                "type": "string",
                "description": "Trecho da descrição, quando não se tem o código.",
            },
            "data_conclusao": {
                "type": "string",
                "description": "Data da conclusão em AAAA-MM-DD (padrão: hoje).",
            },
        },
    },
    "annotations": {
        "title": "Concluir tarefa no DaVinci",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
}

MAX_LISTA = 25


def _codigo(t: Tarefa) -> str:
    return str(t.id)[:8]


def _linha_tarefa(t: Tarefa, nomes: dict[Any, str]) -> str:
    quem = nomes.get(t.responsavel_id, "?")
    partes = [
        f"[{_codigo(t)}] {t.tarefa}",
        f"resp.: {quem}",
        f"início {t.data_inicio.strftime('%d/%m')}",
    ]
    if t.data_conclusao:
        partes.append(f"concluída {t.data_conclusao.strftime('%d/%m')}")
    obs = (t.observacao or "").replace(" · Criada pelo Claude (áudio/chat)", "").strip(" ·")
    if obs:
        partes.append(obs[:120])
    return " — ".join(partes)


async def _nomes_usuarios(session: AsyncSession) -> dict[Any, str]:
    rows = (await session.execute(select(User))).scalars().all()
    return {u.id: (u.name or u.email) for u in rows}


async def listar_tarefas(session: AsyncSession, *, dono: User, args: dict[str, Any]) -> str:
    filtro = str(args.get("filtro") or "pendentes").strip().lower()
    if filtro not in ("pendentes", "concluidas", "todas"):
        raise TarefaInvalidaError("`filtro` deve ser 'pendentes', 'concluidas' ou 'todas'.")
    stmt = select(Tarefa)
    if dono.role != UserRole.ADMIN:
        stmt = stmt.where(Tarefa.responsavel_id == dono.id)
    else:
        alvo = _texto(args.get("responsavel"), "responsavel")
        if alvo:
            pessoa = await _resolver_responsavel(session, alvo, dono)
            stmt = stmt.where(Tarefa.responsavel_id == pessoa.id)
    if args.get("so_minhas"):
        stmt = stmt.where(Tarefa.created_by == dono.id)
    if filtro == "pendentes":
        stmt = stmt.where(Tarefa.data_conclusao.is_(None))
    elif filtro == "concluidas":
        stmt = stmt.where(Tarefa.data_conclusao.is_not(None))
    dias = args.get("dias")
    if isinstance(dias, int) and dias > 0:
        stmt = stmt.where(
            Tarefa.data_inicio >= datetime.now(SAO_PAULO).date() - timedelta(days=dias)
        )
    stmt = stmt.order_by(
        Tarefa.data_conclusao.is_not(None), Tarefa.data_inicio.desc(), Tarefa.created_at.desc()
    )
    rows = list((await session.execute(stmt.limit(MAX_LISTA + 1))).scalars().all())
    if not rows:
        return {
            "pendentes": "Nenhuma tarefa pendente.",
            "concluidas": "Nenhuma tarefa concluída.",
            "todas": "Nenhuma tarefa.",
        }[filtro]
    nomes = await _nomes_usuarios(session)
    linhas = [_linha_tarefa(t, nomes) for t in rows[:MAX_LISTA]]
    cab = {
        "pendentes": "Tarefas pendentes",
        "concluidas": "Tarefas concluídas",
        "todas": "Tarefas",
    }[filtro]
    texto = f"{cab} ({min(len(rows), MAX_LISTA)}):\n" + "\n".join(linhas)
    if len(rows) > MAX_LISTA:
        texto += "\n… e mais. Peça um filtro (pessoa, período) para ver o resto."
    return texto


async def concluir_tarefa(session: AsyncSession, *, dono: User, args: dict[str, Any]) -> str:
    codigo = _texto(args.get("codigo"), "codigo").lower()
    trecho = _texto(args.get("descricao"), "descricao")
    if not codigo and not trecho:
        raise TarefaInvalidaError(
            "Informe o código da tarefa (de `listar_tarefas`) ou um trecho da descrição."
        )
    stmt = select(Tarefa).where(Tarefa.data_conclusao.is_(None))
    if dono.role != UserRole.ADMIN:
        stmt = stmt.where(Tarefa.responsavel_id == dono.id)
    pendentes = list((await session.execute(stmt)).scalars().all())
    if codigo:
        cands = [t for t in pendentes if str(t.id).lower().startswith(codigo)]
    else:
        alvo = _norm(trecho)
        cands = [t for t in pendentes if alvo in _norm(t.tarefa)]
    nomes = await _nomes_usuarios(session)
    if not cands:
        raise TarefaInvalidaError(
            "Não achei tarefa pendente com isso. Use `listar_tarefas` e pegue o código "
            "entre colchetes."
        )
    if len(cands) > 1:
        opcoes = "\n".join(_linha_tarefa(t, nomes) for t in cands[:10])
        raise TarefaInvalidaError(
            "Mais de uma tarefa combina — pergunte ao usuário qual e chame de novo com o "
            f"código:\n{opcoes}"
        )
    t = cands[0]
    quando = _parse_prazo(args.get("data_conclusao")) or datetime.now(SAO_PAULO).date()
    t.data_conclusao = quando
    await session.commit()
    logger.info("tarefa_concluida_via_claude", id=str(t.id), por=str(dono.id))
    return (
        f"Tarefa concluída no DaVinci em {quando.strftime('%d/%m/%Y')}: {t.tarefa} "
        f"(resp.: {nomes.get(t.responsavel_id, '?')})."
    )


# Nome da ferramenta -> (schema anunciado no tools/list, função que executa).
FERRAMENTAS: dict[str, tuple[dict[str, Any], Any]] = {
    TOOL_CRIAR_TAREFA["name"]: (TOOL_CRIAR_TAREFA, criar_tarefa),
    TOOL_LISTAR_TAREFAS["name"]: (TOOL_LISTAR_TAREFAS, listar_tarefas),
    TOOL_CONCLUIR_TAREFA["name"]: (TOOL_CONCLUIR_TAREFA, concluir_tarefa),
}
