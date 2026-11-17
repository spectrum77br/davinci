"""Reembolso ao cliente pela conversa do Atendimento (Novos procedimentos 3/6, 17/11/2026).

O dono aprovou em 17/11/2026: o reembolso tipo Cliente se lança pela aba
PEDIDO da conversa (o bloco "Reembolso ao cliente", abaixo do "Valor pago pelo
comprador"), gravado na MESMA tabela `refunds` da tela Reembolso — a margem e a
conciliação não mudam (a linha nova nasce `conferido = false`, como as da tela).
As regras que as duas telas dividem (limite do ADM, situação, Pix protegido)
estão em `services/reembolso_cliente.py`; aqui fica o lado da conversa:

  • O PEDIDO vem da PRÓPRIA conversa (`etiqueta_fatos.pedido_bling_da_conversa`,
    o mesmo do painel) — nunca de número ou conta digitados. A conta sai no
    formato da tela Reembolso ("Shopee ATV"), pela mesma regra da
    `vw_bling_pedidos.loja_nome` que o "adicionar pedido" de lá usa:
    `stores.apelido_override`, senão `store_info.account_name`, pela loja do
    pedido no Bling. A data é a do pedido no Bling.
  • O VALOR PAGO é o que a aba mostra ("Valor pago pelo comprador", o retrato
    do pedido na plataforma: Shopee e ML — só se for DESTE pedido, 17/11/2026);
    sem retrato (TikTok, Amazon, Magalu), o total do pedido no Bling. Sem
    nenhum dos dois, não há teto a conferir e o reembolso vai para o ADM
    (`reembolso_cliente.requer_aprovacao`).
  • QUEM LANÇA (decisão 4): quem responde a conversa — a loja no escopo da
    pessoa (`_conversa_ou_404` no router) — E é ADM ou tem `reembolso.edit`.
  • A PLATAFORMA JÁ REEMBOLSOU (decisão 5): a mesma fonte que a aba Pedido
    mostra — as devoluções/reclamações da conversa (`reclamacoes.
    reclamacoes_da_conversa`, o GET /reclamacoes) DO PEDIDO com o reembolso
    pago (Shopee ACCEPTED/REFUND_PAID, TikTok concluída, ML com o dinheiro
    devolvido ou encerrada a favor do comprador), o retrato do pedido
    cancelado depois de pago e (17/11/2026) o pedido Cancelado no Bling, em
    qualquer plataforma. Aí só o ADM lança; para os outros, o motivo.
  • O AVISO (decisão 6) é a lista: o bloco mostra os reembolsos que o pedido
    já tem (qualquer tipo, qualquer conta), sem bloquear.
  • Os DADOS DO PIX (decisão 7) vão cifrados e só voltam para ADM; nunca
    para log (aqui só ids, códigos e centavos).

Duplo clique: o lançamento IGUAL da mesma pessoa no mesmo pedido em
`JANELA_REPETIDO` devolve o que já existe (uma trava por pedido serializa os
dois cliques), em vez de gravar duas vezes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import desc, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AtendimentoConversa,
    AtendimentoReclamacao,
    BlingOrder,
    Refund,
    Store,
    StoreInfo,
    User,
    UserRole,
)
from app.services import reembolso_cliente as regras
from app.services.atendimento import acesso, etiqueta_fatos, painel, reclamacoes
from app.services.atendimento.enriquecer import CHAVE_CARIMBOS, CHAVE_PEDIDO
from app.services.bling_situacoes import SITUACAO_CANCELADO
from app.services.reembolso_sync import sync_reembolso_for_pedido

logger = structlog.get_logger()

# O mesmo lançamento (pessoa, pedido, valor, motivo, forma) dentro disto é o
# segundo clique do mesmo botão: devolve o primeiro.
JANELA_REPETIDO = timedelta(seconds=30)
# Até quantos reembolsos do pedido o bloco lista (em produção, no máximo ~40
# Extraviados num pedido; o normal é 0 a 2).
MAX_ITENS = 50

SEM_PEDIDO = "A conversa não tem pedido ligado"
SEM_BLING = "O pedido desta conversa ainda não está no Bling: o reembolso precisa do nº do Bling."
SEM_LOJA = "A loja do pedido não está no cadastro de Lojas: sem ela não há a conta do reembolso."
SEM_PERMISSAO = (
    "Lançar reembolso ao cliente é do ADM ou de quem tem a permissão de editar o Reembolso."
)
SO_QUEM_RESPONDE = "Só quem responde esta conversa lança reembolso."
SO_ADM_APROVA = "Só o ADM aprova reembolso ao cliente."
# 17/11/2026: o diálogo mostrou um pedido e a conversa já está em outro.
PEDIDO_MUDOU = "O pedido da conversa mudou. Confira e lance de novo."
# 17/11/2026: o pedido Cancelado no Bling (situação 12), em qualquer plataforma.
BLING_CANCELADO = "Bling: pedido cancelado"


def so_adm_plataforma(porque: str) -> str:
    """O aviso de quem não é ADM quando a plataforma já reembolsou (decisão 5)."""
    return f"A plataforma já reembolsou este pedido ({porque}): só o ADM lança outro reembolso."


# TikTok: o caso concluído com o reembolso pago (`reclamacoes_devolucoes.STATUS_TELA`).
TIKTOK_REEMBOLSADA = frozenset(
    {"RETURN_OR_REFUND_REQUEST_SUCCESS", "RETURN_OR_REFUND_REQUEST_COMPLETE"}
)
# O pedido cancelado (ou devolvido em parte) na plataforma DEPOIS de pago: o
# comprador já recebeu de volta. Os códigos crus do retrato (`enriquecer`).
_RETRATO_CANCELADO = {
    "shopee": frozenset({"CANCELLED"}),
    "ml": frozenset({"cancelled", "partially_refunded"}),
}
_NOME_PLATAFORMA = {**reclamacoes.NOME_PLATAFORMA}


class ReembolsoRecusado(Exception):  # noqa: N818 — nome do domínio, como EnvioRecusado
    """A trava que recusou: o router devolve `status` com `{code, detail}`."""

    def __init__(self, status: int, code: str, detail: str) -> None:
        super().__init__(code)
        self.status = status
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class PedidoDoReembolso:
    """O pedido da conversa, no formato da tela Reembolso."""

    bling: str | None
    marketplace: str | None
    plataforma: str | None
    conta: str | None
    valor_pago: float | None
    data: datetime | None = None
    # A situação do pedido no Bling (id em texto, como `bling_orders.situacao`):
    # o Cancelado (12) conta como "a plataforma já reembolsou" (17/11/2026).
    situacao_bling: str | None = None
    # Todos os números do pedido (Bling e plataforma, o pack do ML): acham os
    # reembolsos e as devoluções dele.
    numeros: frozenset[str] = field(default_factory=frozenset)
    # O retrato do pedido na plataforma (o status diz se foi cancelado depois de pago).
    retrato: dict | None = field(default=None, compare=False, repr=False)

    def para_tela(self) -> dict[str, Any]:
        return {
            "bling": self.bling,
            "marketplace": self.marketplace,
            "plataforma": self.plataforma,
            "conta": self.conta,
            "valor_pago": self.valor_pago,
        }


# ── Quem pode ─────────────────────────────────────────────────────────────


def e_admin(user: User) -> bool:
    return user.role == UserRole.ADMIN


def pode_aprovar(user: User) -> bool:
    """Aprovar (e ver os dados do Pix): só ADM (decisões 3 e 7)."""
    return e_admin(user)


def tem_permissao_de_lancar(user: User) -> bool:
    """ADM sempre; os outros, com `reembolso.edit` (decisão 4). A loja no escopo
    da pessoa já foi conferida ao abrir a conversa (`_conversa_ou_404`)."""
    if e_admin(user):
        return True
    return bool(((user.permissions or {}).get("reembolso") or {}).get("edit"))


# ── O pedido da conversa ──────────────────────────────────────────────────


def _numero(valor: Any) -> float | None:
    """Número positivo (Decimal/str/int/float) → float; o resto → None."""
    c = regras.centavos(valor)
    return c / 100 if c is not None and c > 0 else None


def valor_pago_do_retrato(retrato: Any) -> float | None:
    """O "Valor pago pelo comprador" que a aba Pedido mostra (`pm.valor_pago`). PURA."""
    if not isinstance(retrato, dict):
        return None
    return _numero(retrato.get("valor_pago"))


def _texto_minusculo(valor: Any) -> str:
    return str(valor or "").strip().lower()


def retrato_do_mesmo_pedido(
    retrato: Any, conversa: AtendimentoConversa, numeroloja: str | None = None
) -> dict | None:
    """O retrato só vale se for do MESMO pedido da conversa; senão None. PURA.

    17/11/2026: o chat da Shopee é um só por comprador e muda de pedido (o
    cartão de outra compra, o foco da devolução) antes de o retrato ser
    refeito. O retrato da outra compra dava o valor pago (o teto e o limite do
    ADM) e o "cancelado depois de pago" errados. A regra de
    `acao_pessoa.retrato_do_pedido` e da tela (`retratoDoPedido`): o `pedido`
    do retrato é o `pedido_marketplace`, o nº na loja do pedido no Bling
    (`numeroloja`) ou o pack/order do ML. No ML o retrato é do pack: vale
    também um dos pedidos dele ou o carimbo da leitura (o nº que foi lido,
    como `enriquecer._retrato_de_outro`).
    """
    if not isinstance(retrato, dict) or not retrato:
        return None
    dados = conversa.dados if isinstance(conversa.dados, dict) else {}
    chaves = {
        _texto_minusculo(c)
        for c in (
            conversa.pedido_marketplace,
            numeroloja,
            dados.get("pack_id"),
            dados.get("order_id"),
        )
    }
    chaves.discard("")
    numeros = {_texto_minusculo(retrato.get("pedido"))}
    if conversa.plataforma == "ml":
        pedidos = retrato.get("pedidos")
        if isinstance(pedidos, list):
            numeros |= {_texto_minusculo(p) for p in pedidos}
        carimbos = dados.get(CHAVE_CARIMBOS) if isinstance(dados.get(CHAVE_CARIMBOS), dict) else {}
        marca = carimbos.get(CHAVE_PEDIDO) if isinstance(carimbos.get(CHAVE_PEDIDO), dict) else {}
        numeros.add(_texto_minusculo(marca.get("id")))
    numeros.discard("")
    return retrato if numeros & chaves else None


async def _retrato(session: AsyncSession, conversa: AtendimentoConversa) -> dict | None:
    """O retrato do pedido na plataforma: o da conversa ou, na `reclamacao` da
    Shopee sem o dela, o do chat do mesmo pedido (como o detalhe e o painel).
    Quem usa confere se é do pedido da conversa (`retrato_do_mesmo_pedido`)."""
    dados = conversa.dados if isinstance(conversa.dados, dict) else {}
    retrato = dados.get("pedido_mkt")
    if isinstance(retrato, dict) and retrato:
        return retrato
    if painel.devolucao_sem_retrato(conversa):
        chat = await painel.chat_que_empresta_o_retrato(session, conversa)
        dados_chat = chat.dados if chat is not None and isinstance(chat.dados, dict) else {}
        emprestado = dados_chat.get("pedido_mkt")
        return emprestado if isinstance(emprestado, dict) and emprestado else None
    return None


async def _loja(session: AsyncSession, loja: str | None) -> tuple[str | None, str | None]:
    """(plataforma, conta) da loja do pedido no Bling, como a `vw_bling_pedidos`:
    a conta é `stores.apelido_override` ("Shopee ATV"), senão o nome da ficha."""
    loja = (loja or "").strip()
    if not loja:
        return None, None
    apelido = marketplace = None
    if loja.isdigit():
        linha = (
            await session.execute(
                select(Store.apelido_override, Store.marketplace)
                .where(Store.bling_store_id == int(loja))
                .order_by(Store.apelido_override.is_(None))
                .limit(1)
            )
        ).first()
        if linha is not None:
            apelido = regras.texto_limpo(linha.apelido_override)
            marketplace = getattr(linha.marketplace, "value", linha.marketplace)
    ficha = (
        await session.execute(
            select(StoreInfo.account_name, StoreInfo.platform)
            .where(StoreInfo.bling_store_id == loja)
            .order_by(StoreInfo.account_name.is_(None))
            .limit(1)
        )
    ).first()
    conta = apelido or (regras.texto_limpo(ficha.account_name) if ficha is not None else None)
    plataforma = marketplace or (regras.texto_limpo(ficha.platform) if ficha is not None else None)
    return (str(plataforma).strip().lower() if plataforma else None), conta


async def pedido_da_conversa(
    session: AsyncSession, conversa: AtendimentoConversa
) -> PedidoDoReembolso | None:
    """O pedido da conversa para o reembolso; None = a conversa não tem pedido.

    Com o nº na plataforma mas sem o pedido no Bling, volta o pedido SEM `bling`
    nem `conta` (a tela mostra o número e o porquê de não lançar).

    O retrato de OUTRO pedido (17/11/2026) não conta: o valor pago cai no
    total do Bling e o status dele não diz se a plataforma reembolsou.
    """
    chaves = [c for c in etiqueta_fatos.chaves_do_pedido(conversa) if c]
    marketplace = regras.texto_limpo(conversa.pedido_marketplace)
    pb = await etiqueta_fatos.pedido_bling_da_conversa(session, conversa) if chaves else None
    if pb is None and marketplace is None:
        return None
    retrato = retrato_do_mesmo_pedido(
        await _retrato(session, conversa), conversa, pb.numeroloja if pb is not None else None
    )
    pago_retrato = valor_pago_do_retrato(retrato)
    if pb is None:
        return PedidoDoReembolso(
            bling=None,
            marketplace=marketplace,
            plataforma=regras.texto_limpo(conversa.plataforma),
            conta=None,
            valor_pago=pago_retrato,
            numeros=frozenset(chaves),
            retrato=retrato,
        )
    data, total = (
        await session.execute(
            select(func.max(BlingOrder.data), func.max(BlingOrder.total)).where(
                BlingOrder.numero == pb.numero
            )
        )
    ).one()
    plataforma, conta = await _loja(session, pb.loja)
    numeros = {pb.numero, *chaves}
    if pb.numeroloja:
        numeros.add(pb.numeroloja)
    return PedidoDoReembolso(
        bling=pb.numero,
        marketplace=regras.texto_limpo(pb.numeroloja) or marketplace,
        plataforma=plataforma or regras.texto_limpo(conversa.plataforma),
        conta=conta,
        valor_pago=pago_retrato if pago_retrato is not None else _numero(total),
        data=data,
        situacao_bling=pb.situacao,
        numeros=frozenset(n for n in numeros if n),
        retrato=retrato,
    )


# ── A plataforma já reembolsou? ───────────────────────────────────────────


def reembolso_da_reclamacao(r: AtendimentoReclamacao) -> str | None:
    """O caso da plataforma diz que o comprador JÁ recebeu o dinheiro? O porquê, ou None. PURA.

    Shopee ACCEPTED/REFUND_PAID ("reembolso pago ao comprador"), TikTok
    concluída com o reembolso pago, ML com a devolução `refunded` ou a
    reclamação encerrada a favor do comprador (inclusive a cobertura do ML).
    """
    status = (r.status or "").strip()
    dados = r.dados if isinstance(r.dados, dict) else {}
    nome = _NOME_PLATAFORMA.get(r.plataforma, r.plataforma)
    rotulo = reclamacoes.status_para_tela(r) or status
    if r.plataforma == "shopee":
        from app.services.atendimento.reclamacoes_devolucoes import SHOPEE_REEMBOLSADA

        if status.upper() in SHOPEE_REEMBOLSADA:
            return f"{nome}: {rotulo}"
        return None
    if r.plataforma == "tiktok":
        return f"{nome}: {rotulo}" if status.upper() in TIKTOK_REEMBOLSADA else None
    if r.plataforma == "ml":
        devolucao = dados.get("devolucao") if isinstance(dados.get("devolucao"), dict) else {}
        if str(devolucao.get("status_dinheiro") or "").strip().lower() == "refunded":
            return f"{nome}: dinheiro devolvido ao comprador"
        resolucao = dados.get("resolucao") if isinstance(dados.get("resolucao"), dict) else {}
        beneficiados = {str(b).strip().lower() for b in resolucao.get("beneficiado") or []}
        if r.encerrada_em is not None and "complainant" in beneficiados:
            return f"{nome}: reclamação encerrada a favor do comprador"
    return None


def reembolso_do_retrato(plataforma: str | None, retrato: Any) -> str | None:
    """O pedido foi cancelado na plataforma DEPOIS de pago? O porquê, ou None. PURA."""
    if not isinstance(retrato, dict):
        return None
    status = str(retrato.get("status") or "").strip()
    cancelados = _RETRATO_CANCELADO.get(plataforma or "", frozenset())
    if status not in cancelados and status.lower() not in cancelados:
        return None
    if not retrato.get("pago_em") and valor_pago_do_retrato(retrato) is None:
        return None  # nunca foi pago: não houve dinheiro a devolver
    nome = _NOME_PLATAFORMA.get(plataforma or "", plataforma)
    texto = str(retrato.get("status_texto") or "").strip() or status
    return f"{nome}: pedido {texto.lower()} depois de pago"


def reembolso_do_bling(situacao: Any) -> str | None:
    """O pedido está Cancelado no Bling (situação 12)? O porquê, ou None. PURA.

    17/11/2026: vale em todas as plataformas — TikTok, Amazon e Magalu não têm
    retrato, e o da Shopee/ML pode estar velho; o cancelado depois de pago a
    plataforma devolve sozinha. O raro cancelado nunca pago também trava (só
    para quem não é ADM).
    """
    return BLING_CANCELADO if str(situacao or "").strip() == str(SITUACAO_CANCELADO) else None


async def plataforma_reembolsou(
    session: AsyncSession, conversa: AtendimentoConversa, pedido: PedidoDoReembolso
) -> str | None:
    """A plataforma já devolveu o dinheiro deste pedido? O porquê (para a tela), ou None.

    A fonte é a que a aba Pedido mostra: as devoluções/reclamações da conversa
    (só as DESTE pedido — o chat do comprador pode ter a de outra compra), o
    status do retrato do pedido e, por último, o pedido Cancelado no Bling.
    """
    # Teto maior que o do cartão (20): o chat do comprador junta os casos de
    # todas as compras dele, e o deste pedido não pode ficar de fora.
    for r in await reclamacoes.reclamacoes_da_conversa(session, conversa, limite=MAX_ITENS):
        if not (etiqueta_fatos.pedidos_da_reclamacao(r) & pedido.numeros):
            continue
        porque = reembolso_da_reclamacao(r)
        if porque:
            return porque
    return reembolso_do_retrato(conversa.plataforma, pedido.retrato) or reembolso_do_bling(
        pedido.situacao_bling
    )


# ── Os reembolsos do pedido ───────────────────────────────────────────────


async def reembolsos_do_pedido(session: AsyncSession, pedido: PedidoDoReembolso) -> list[Refund]:
    """Todos os reembolsos do pedido (qualquer tipo, conta ou autor), o mais novo primeiro.

    Pelo nº do Bling OU da plataforma em qualquer dos dois campos — como o aviso
    da tela Reembolso (`routers/refunds._reembolsos_ja_lancados`): à mão, o
    número às vezes foi parar no campo do outro.
    """
    if not pedido.numeros:
        return []
    numeros = sorted(pedido.numeros)
    return list(
        (
            await session.execute(
                select(Refund)
                .where(
                    or_(Refund.pedido_bling.in_(numeros), Refund.pedido_marketplace.in_(numeros))
                )
                .order_by(desc(Refund.created_at))
                .limit(MAX_ITENS)
            )
        )
        .scalars()
        .all()
    )


async def _nomes(session: AsyncSession, ids: set[UUID | None]) -> dict[UUID, str | None]:
    ids = {i for i in ids if i is not None}
    if not ids:
        return {}
    return {
        uid: regras.texto_limpo(nome) or regras.texto_limpo(email)
        for uid, nome, email in (
            await session.execute(select(User.id, User.name, User.email).where(User.id.in_(ids)))
        ).all()
    }


def item(r: Refund, nomes: dict[UUID, str | None], *, ve_pix: bool) -> dict[str, Any]:
    """Um reembolso no formato do bloco. `valor` positivo (para mostrar); o Pix só com `ve_pix`."""
    return {
        "id": r.id,
        "tipo": r.tipo,
        "valor": round(abs(float(r.reembolso or 0.0)), 2),
        "motivo": r.motivo,
        "motivo_detalhe": r.motivo_detalhe,
        "forma": r.forma_pagamento,
        "situacao": regras.situacao(r),
        "criado_por": nomes.get(r.created_by) if r.created_by else None,
        "criado_em": r.created_at,
        "aprovado_por": nomes.get(r.aprovado_por) if r.aprovado_por else None,
        "aprovado_em": r.aprovado_em,
        "dados_pagamento": (
            regras.ler_dados_pagamento(r.dados_pagamento, refund_id=r.id) if ve_pix else None
        ),
        "conta": r.conta,
        "origem": r.origem,
    }


async def _itens(
    session: AsyncSession, linhas: list[Refund], *, ve_pix: bool
) -> list[dict[str, Any]]:
    nomes = await _nomes(session, {r.created_by for r in linhas} | {r.aprovado_por for r in linhas})
    return [item(r, nomes, ve_pix=ve_pix) for r in linhas]


# ── O GET do bloco ────────────────────────────────────────────────────────


LIMITE = {"valor": regras.LIMITE_VALOR, "pct": regras.LIMITE_PCT}


def _bloqueio(user: User, pedido: PedidoDoReembolso | None, plataforma: str | None) -> str | None:
    """Por que esta pessoa não lança neste pedido (None = pode). A ordem é a do POST."""
    if not acesso.pode_responder(user):
        return SO_QUEM_RESPONDE
    if not tem_permissao_de_lancar(user):
        return SEM_PERMISSAO
    if pedido is None:
        return SEM_PEDIDO
    if pedido.bling is None:
        return SEM_BLING
    if pedido.conta is None:
        return SEM_LOJA
    if plataforma and not e_admin(user):
        return so_adm_plataforma(plataforma)
    return None


def sem_pedido(user: User) -> dict[str, Any]:
    """O bloco de quem não tem pedido (o Direct do Instagram, a conversa sem pedido)."""
    return {
        "pedido": None,
        "plataforma_reembolsou": False,
        "plataforma_detalhe": None,
        "limite": dict(LIMITE),
        "pode_lancar": False,
        "motivo_bloqueio": SEM_PEDIDO,
        "pode_aprovar": pode_aprovar(user),
        "itens": [],
    }


async def bloco_da_conversa(
    session: AsyncSession, conversa: AtendimentoConversa, user: User
) -> dict[str, Any]:
    """O GET /conversas/{id}/reembolsos: o pedido, os reembolsos dele e se a pessoa lança."""
    admin = e_admin(user)
    pedido = await pedido_da_conversa(session, conversa)
    if pedido is None:
        return sem_pedido(user)
    plataforma = await plataforma_reembolsou(session, conversa, pedido)
    linhas = await reembolsos_do_pedido(session, pedido)
    motivo = _bloqueio(user, pedido, plataforma)
    return {
        "pedido": pedido.para_tela(),
        "plataforma_reembolsou": plataforma is not None,
        "plataforma_detalhe": plataforma,
        "limite": dict(LIMITE),
        "pode_lancar": motivo is None,
        "motivo_bloqueio": motivo,
        "pode_aprovar": pode_aprovar(user),
        "itens": await _itens(session, linhas, ve_pix=admin),
    }


# ── O POST: lançar ────────────────────────────────────────────────────────


def _brl(centavos: int) -> str:
    inteiro, resto = divmod(abs(centavos), 100)
    return f"R$ {inteiro:,}".replace(",", ".") + f",{resto:02d}"


async def _refresh_margem(session: AsyncSession, pedido_bling: str) -> None:
    """O snapshot da Margem relido para o pedido, como o POST da tela Reembolso
    (`routers/refunds._refresh_margem_snapshot`): a "Margem Pós Reembolso" conta
    o reembolso em aberto. Tolerante: o reembolso já está gravado."""
    from app.services.verificar_margem import refresh_for_pedido

    try:
        await refresh_for_pedido(session, pedido_bling)
    except Exception as e:  # noqa: BLE001 — o cron de 30 min e o "atualizar" são o plano B
        logger.warning(
            "reembolso_cliente_margem_snapshot_falhou",
            pedido_bling=pedido_bling,
            err=type(e).__name__,
        )
        await session.rollback()


async def lancar(
    session: AsyncSession,
    conversa: AtendimentoConversa,
    user: User,
    *,
    valor: Any,
    motivo: str,
    motivo_detalhe: str | None,
    forma: str,
    dados_pagamento: str | None,
    pedido_bling: str | None = None,
) -> dict[str, Any]:
    """Grava o reembolso ao cliente do pedido da conversa e devolve o item do bloco.

    As travas, na ordem: permissão (403 `sem_permissao`), o pedido que o
    diálogo mostrou não é mais o da conversa (409 `pedido_mudou`), pedido (409
    `sem_pedido`), plataforma já reembolsou (403 `so_adm_plataforma_reembolsou`,
    menos para ADM), detalhe do "outro" (422 `detalhe_obrigatorio`), dados do
    Pix (422 `pix_sem_dados`) e o teto do valor pago (422 `valor_acima_do_pago`).

    `pedido_bling` (17/11/2026) é o nº do Bling que o diálogo mostrou: só
    confere — o pedido, a conta e o valor pago continuam saindo da conversa.
    """
    admin = e_admin(user)
    user_id = user.id
    conversa_id = conversa.id
    if not acesso.pode_responder(user):
        raise ReembolsoRecusado(403, "sem_permissao", SO_QUEM_RESPONDE)
    if not tem_permissao_de_lancar(user):
        raise ReembolsoRecusado(403, "sem_permissao", SEM_PERMISSAO)
    pedido = await pedido_da_conversa(session, conversa)
    # O comprador citou outra compra (ou o foco da devolução trocou o pedido)
    # com o diálogo aberto: não grava no pedido que a pessoa não viu.
    mostrado = regras.texto_limpo(pedido_bling)
    if mostrado is not None and (pedido is None or pedido.bling != mostrado):
        raise ReembolsoRecusado(409, "pedido_mudou", PEDIDO_MUDOU)
    if pedido is None:
        raise ReembolsoRecusado(409, "sem_pedido", SEM_PEDIDO)
    if pedido.bling is None:
        raise ReembolsoRecusado(409, "sem_pedido", SEM_BLING)
    if pedido.conta is None:
        raise ReembolsoRecusado(409, "sem_pedido", SEM_LOJA)
    plataforma = await plataforma_reembolsou(session, conversa, pedido)
    if plataforma and not admin:
        raise ReembolsoRecusado(403, "so_adm_plataforma_reembolsou", so_adm_plataforma(plataforma))

    if motivo not in regras.MOTIVOS:
        raise ReembolsoRecusado(422, "motivo_invalido", "Escolha o motivo na lista.")
    if forma not in regras.FORMAS:
        raise ReembolsoRecusado(422, "forma_invalida", "Escolha Pix ou Cupom.")
    detalhe = regras.texto_limpo(motivo_detalhe)
    if motivo == regras.MOTIVO_OUTRO and detalhe is None:
        raise ReembolsoRecusado(
            422, "detalhe_obrigatorio", 'Com o motivo "Outro", escreva o detalhe.'
        )
    if detalhe is not None and len(detalhe) > regras.MAX_DETALHE:
        raise ReembolsoRecusado(
            422, "detalhe_longo", f"O detalhe passa de {regras.MAX_DETALHE} caracteres."
        )
    # Pix: os dados são obrigatórios; no cupom, nada de Pix guardado.
    pix = regras.texto_limpo(dados_pagamento) if forma == regras.FORMA_PIX else None
    if forma == regras.FORMA_PIX and pix is None:
        raise ReembolsoRecusado(
            422, "pix_sem_dados", "Preencha os dados do Pix (chave, banco e nome de quem recebe)."
        )
    if pix is not None and len(pix) > regras.MAX_DADOS_PAGAMENTO:
        raise ReembolsoRecusado(
            422,
            "pix_longo",
            f"Os dados do Pix passam de {regras.MAX_DADOS_PAGAMENTO} caracteres.",
        )
    valor_c = regras.centavos(valor)
    if valor_c is None or valor_c <= 0:
        raise ReembolsoRecusado(422, "valor_invalido", "Informe um valor maior que zero.")
    pago_c = regras.centavos(pedido.valor_pago) if pedido.valor_pago is not None else None
    if pago_c is not None and pago_c > 0 and valor_c > pago_c:
        raise ReembolsoRecusado(
            422,
            "valor_acima_do_pago",
            f"O reembolso ({_brl(valor_c)}) passa do valor pago pelo comprador ({_brl(pago_c)}).",
        )
    requer = regras.requer_aprovacao(valor_c, pago_c)
    reembolso = -(valor_c / 100)

    # Dois cliques no mesmo botão: a trava do pedido serializa, e o segundo
    # acha o primeiro (o mesmo lançamento da mesma pessoa há instantes).
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:chave))"),
        {"chave": f"reembolso_cliente:{pedido.bling}"},
    )
    repetido = (
        await session.execute(
            select(Refund)
            .where(
                Refund.pedido_bling == pedido.bling,
                Refund.created_by == user_id,
                Refund.origem == regras.ORIGEM_ATENDIMENTO,
                Refund.tipo == regras.TIPO_CLIENTE,
                Refund.reembolso == reembolso,
                Refund.motivo == motivo,
                Refund.forma_pagamento == forma,
                Refund.created_at >= func.now() - JANELA_REPETIDO,
            )
            .order_by(desc(Refund.created_at))
            .limit(1)
        )
    ).scalar_one_or_none()
    if repetido is not None:
        logger.info("reembolso_cliente_repetido", id=str(repetido.id), conversa_id=str(conversa_id))
        saida = (await _itens(session, [repetido], ve_pix=admin))[0]
        await session.rollback()
        return saida

    agora = datetime.now(UTC)
    row = Refund(
        data=pedido.data,
        pedido_bling=pedido.bling,
        pedido_marketplace=pedido.marketplace,
        plataforma=pedido.plataforma,
        conta=pedido.conta,
        tipo=regras.TIPO_CLIENTE,
        # Como os 75 lançados à mão: o valor negativo em `reembolso`, prejuízo 0.
        prejuizo=0.0,
        reembolso=reembolso,
        reembolso_at=agora,
        conferido=False,
        created_by=user_id,
        motivo=motivo,
        motivo_detalhe=detalhe,
        forma_pagamento=forma,
        dados_pagamento=regras.proteger_dados_pagamento(pix),
        requer_aprovacao=requer,
        # O ADM que lança já aprova (decisão 3).
        aprovado_por=user_id if admin else None,
        aprovado_em=agora if admin else None,
        origem=regras.ORIGEM_ATENDIMENTO,
        conversa_id=str(conversa_id),
    )
    session.add(row)
    await session.flush()
    refund_id = row.id
    # A mesma volta do POST da tela Reembolso: bling_orders (só os finalizados
    # contam, então o em aberto não muda nada lá) e o snapshot da Margem.
    await sync_reembolso_for_pedido(session, pedido.bling)
    await session.commit()
    await _refresh_margem(session, pedido.bling)
    logger.info(
        "reembolso_cliente_lancado",
        id=str(refund_id),
        conversa_id=str(conversa_id),
        pedido_bling=pedido.bling,
        valor_centavos=valor_c,
        requer_aprovacao=requer,
        aprovado_na_hora=admin,
        plataforma_reembolsou=plataforma is not None,
    )
    salvo = await session.get(Refund, refund_id, populate_existing=True)
    return (await _itens(session, [salvo], ve_pix=admin))[0]


# ── O POST: aprovar ───────────────────────────────────────────────────────


async def aprovar(session: AsyncSession, refund_id: UUID, user: User) -> dict[str, Any]:
    """O ADM aprova o reembolso que aguarda (decisão 3). 404 sem a linha, 403 para
    quem não é ADM, 409 `ja_aprovado` se já estava (ou se nunca precisou)."""
    admin = e_admin(user)
    user_id = user.id
    existe = await session.scalar(select(Refund.id).where(Refund.id == refund_id))
    if existe is None:
        raise ReembolsoRecusado(404, "reembolso_nao_encontrado", "Reembolso não encontrado.")
    if not admin:
        raise ReembolsoRecusado(403, "sem_permissao", SO_ADM_APROVA)
    row = (
        await session.execute(
            select(Refund)
            .where(Refund.id == refund_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    if row.aprovado_em is not None:
        raise ReembolsoRecusado(409, "ja_aprovado", "Este reembolso já foi aprovado.")
    if not row.requer_aprovacao:
        raise ReembolsoRecusado(
            409, "ja_aprovado", "Este reembolso não precisa da aprovação do ADM."
        )
    row.aprovado_por = user_id
    row.aprovado_em = datetime.now(UTC)
    await session.commit()
    logger.info("reembolso_cliente_aprovado", id=str(refund_id), aprovado_por=str(user_id))
    salvo = await session.get(Refund, refund_id, populate_existing=True)
    return (await _itens(session, [salvo], ve_pix=True))[0]
