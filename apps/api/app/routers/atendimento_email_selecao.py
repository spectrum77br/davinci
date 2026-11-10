"""Seleção integral das Filas para a lixeira, sem depender do cursor de horário."""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Response
from pydantic import AwareDatetime, BaseModel, model_validator
from sqlalchemy import func, select
from sqlalchemy import text as sql_text

from app.models import AtendimentoConversa, AtendimentoMensagem, MailMessage
from app.models.mail_atendimento import MailMessageMeta
from app.routers.atendimento import (
    _busca_conversa_sql,
    _clausula_escopo,
    _tipo_do_chamado,
    _tipo_do_email_sql,
)
from app.routers.atendimento_email import (
    MarcarTodosLidosIn,
    Session,
    _aba_inteira,
    _escopo_da_aba,
    _filtro_da_fila,
    _le_filas,
    _recorte_email,
    _tipo_da_meta_sql,
)
from app.routers.mail import MailManager
from app.services.atendimento import separa_email
from app.services.atendimento.constantes import CONVERSA_FECHADA, PLATAFORMA_SITE
from app.services.atendimento.escopo import escopo_do_atendimento
from app.services.mail_atendimento import administracao

router = APIRouter(prefix="/api/atendimento/email", tags=["atendimento"])
MAX_SELECAO = 10_000
FILAS_DE_CONVERSA = frozenset({"chamados", "aguardando", "todas"})


class SelecaoEmailIn(MarcarTodosLidosIn):
    fila: Literal[
        "chamados", "aguardando", "todas", "sem_loja", "sem_vinculo", "suspeito", "resumo", "erro"
    ]

    @model_validator(mode="after")
    def busca_so_na_conversa(self):
        if self.fila not in FILAS_DE_CONVERSA and self.q and self.q.strip():
            raise ValueError("q só se aplica às filas de conversas")
        return self


class SelecaoEmailItemOut(BaseModel):
    id: UUID
    ate: AwareDatetime | None = None


class SelecaoEmailOut(BaseModel):
    tipo: Literal["conversas", "emails"]
    itens: list[SelecaoEmailItemOut]


@router.post("/lixeira-selecao", response_model=SelecaoEmailOut, response_model_exclude_none=True)
async def selecionar_para_lixeira(
    body: SelecaoEmailIn, session: Session, user: MailManager, response: Response
) -> SelecaoEmailOut:
    """Só IDs e limites de chegada, nos mesmos filtros e acesso da lista.

    Não lê o corpo, marca como visto ou exclui. O corte explícito recusa a
    seleção inteira, em vez de chamar uma parte dos resultados de "todos".
    """
    _aba_inteira(user)
    response.headers["Cache-Control"] = "no-store"
    if body.fila in FILAS_DE_CONVERSA:
        scope = await escopo_do_atendimento(session, user)
        visao = await administracao.visao_rapida(session, user)
        await session.execute(sql_text("SET LOCAL jit = off"))
        c, m = AtendimentoConversa, AtendimentoMensagem
        filtros = [separa_email.conversa_de_email_sql(), visao.filtro_conversa()]
        cond = _clausula_escopo(scope, c.integration_id)
        if cond is not None:
            filtros.append(cond)
        if body.fila == "aguardando":
            filtros.append(c.aguardando_resposta.is_(True))
        else:
            filtros.append(
                c.situacao == CONVERSA_FECHADA
                if body.fechadas else c.situacao != CONVERSA_FECHADA
            )
        if body.fila == "chamados":
            filtros.append(c.plataforma == PLATAFORMA_SITE)
        if body.tipo_chamado:
            filtros.append(_tipo_do_chamado() == body.tipo_chamado)
        if body.tipo_email:
            filtros.append(_tipo_do_email_sql() == body.tipo_email)
        if body.q and body.q.strip():
            filtros.append(_busca_conversa_sql(body.q))
        consulta = (
            select(c.id, func.max(m.created_at).label("ate"))
            .outerjoin(m, m.conversa_id == c.id)
            .where(*filtros)
            .group_by(c.id)
            .order_by(c.id)
        )
        linhas = (await session.execute(consulta.limit(MAX_SELECAO + 1))).all()
        tipo = "conversas"
    else:
        _le_filas(user)
        scope = await _escopo_da_aba(session, user)
        filtros = [
            *_filtro_da_fila(body.fila),
            administracao.filtro_mensagem(user, MailMessageMeta.message_id),
            *_recorte_email(scope, MailMessageMeta.message_id),
        ]
        if body.tipo_email:
            filtros.append(_tipo_da_meta_sql() == body.tipo_email)
        consulta = (
            select(MailMessage.id)
            .join(MailMessageMeta, MailMessageMeta.message_id == MailMessage.id)
            .where(*filtros)
            .order_by(MailMessage.id)
        )
        ids = (await session.scalars(consulta.limit(MAX_SELECAO + 1))).all()
        linhas = [(ident, None) for ident in ids]
        tipo = "emails"
    if len(linhas) > MAX_SELECAO:
        raise HTTPException(409, detail={
            "code": "email_selecao_grande",
            "detail": "A seleção tem mais de 10.000 itens. Refine os filtros antes de selecionar.",
        })
    if tipo == "conversas" and any(ate is None for _, ate in linhas):
        raise HTTPException(409, detail={
            "code": "email_selecao_sem_snapshot",
            "detail": "Há conversa sem mensagens nesta seleção. Confira a fila antes de excluir.",
        })
    return SelecaoEmailOut(
        tipo=tipo,
        itens=[SelecaoEmailItemOut(id=ident, ate=ate) for ident, ate in linhas],
    )
