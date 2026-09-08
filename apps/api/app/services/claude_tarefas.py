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
  * responsável diferente do criador recebe o mesmo aviso da tela (alerta +
    Telegram). Tudo numa transação só: se o aviso falhar, a tarefa ainda é
    gravada e o texto diz que o aviso não saiu — nunca "tente de novo" com a
    tarefa já criada (duplicaria).
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime
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
    # avisa), em vez de "tente de novo" com a tarefa já gravada.
    avisou = False
    if responsavel.id != dono.id:
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
    if responsavel.id != dono.id:
        linhas.append(
            f"{quem} foi avisado(a) no DaVinci."
            if avisou
            else f"A tarefa foi gravada, mas não consegui avisar {quem} agora."
        )
    return "\n".join(linhas)
