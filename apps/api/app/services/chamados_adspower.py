"""Botão Servidor do histórico do chamado (09/11/2026, Vinicius: "quando clicava
você vai no adspower, abre o perfil, e deixa na tela do chamado na minha
máquina — cada um tem que abrir na sua máquina").

O mesmo arranjo do botão AdsPower do Atendimento (RF11): o DaVinci NÃO fala com
o AdsPower, que roda no computador de cada pessoa. Daqui sai só QUAL perfil
abrir — o campo "Servidor" da loja (store-info = `serial_number` no AdsPower);
o navegador de quem clicou chama a API local do AdsPower dele
(`components/AtendimentoAdsPower.vue`).

A loja do chamado sai pela MESMA régua da cerca da equipe
(`routers/chamados._clausula_equipe`): com pedido no espelho do Bling (o da
linha ou os do lote), a loja do Bling do pedido manda; sem pedido no espelho,
a conta da linha pela plataforma + nome (`team_scope.conta_no_escopo`).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps.team_scope import TeamScope, _chave_da_conta, conta_no_escopo
from app.models import BlingOrder, Chamado, ChamadoPedido, StoreInfo
from app.services.atendimento.painel import (
    MOTIVO_SEM_CADASTRO,
    MOTIVO_SEM_PERFIL,
    _perfil_no_espelho,
)

MOTIVO_SEM_CADASTRO_PEDIDO = (
    "A loja do Bling deste pedido não está no cadastro de Lojas (store-info) — sem "
    "ele não se sabe qual perfil do AdsPower abrir."
)
MOTIVO_AMBIGUO = (
    "Este chamado bate com mais de uma loja do cadastro, com perfis diferentes no "
    "AdsPower (campo Servidor) — não dá para saber qual abrir."
)


async def _lojas_pelo_pedido(session: AsyncSession, ch: Chamado) -> tuple[bool, list[StoreInfo]]:
    """(achou pedido no espelho?, lojas do cadastro das lojas do Bling desses pedidos)."""
    numeros = {(ch.pedido_bling or "").strip()} - {""}
    numeros |= {
        (n or "").strip()
        for n in (
            await session.execute(
                select(ChamadoPedido.pedido_bling).where(ChamadoPedido.chamado_id == ch.id)
            )
        ).scalars()
    } - {""}
    if not numeros:
        return False, []
    lojas_bling = [
        loja
        for (loja,) in (
            await session.execute(
                select(func.distinct(BlingOrder.loja)).where(BlingOrder.numero.in_(sorted(numeros)))
            )
        ).all()
    ]
    if not lojas_bling:
        return False, []
    ids = sorted({str(x).strip() for x in lojas_bling if x is not None and str(x).strip()})
    if not ids:
        return True, []
    lojas = (
        (
            await session.execute(
                select(StoreInfo).where(
                    StoreInfo.bling_store_id.in_(ids), StoreInfo.archived_at.is_(None)
                )
            )
        )
        .scalars()
        .all()
    )
    return True, list(lojas)


async def _lojas_pela_conta(session: AsyncSession, ch: Chamado) -> list[StoreInfo]:
    """As lojas do cadastro cuja conta casa com a da linha, com a régua da cerca."""
    if not (ch.conta or "").strip():
        return []
    todas = (
        (await session.execute(select(StoreInfo).where(StoreInfo.archived_at.is_(None))))
        .scalars()
        .all()
    )
    chaves = {s.id: _chave_da_conta(s.platform, s.account_name) for s in todas}
    vezes: dict[str, int] = {}
    for k in chaves.values():
        if k:
            vezes[k[1]] = vezes.get(k[1], 0) + 1
    achadas = []
    for s in todas:
        k = chaves[s.id]
        if not k:
            continue
        # Uma "equipe" de uma loja só: o nome sozinho (linha sem plataforma) só
        # vale se nenhuma outra loja do cadastro usa o mesmo nome.
        escopo = TeamScope(
            unrestricted=False,
            platform_accounts={k},
            unshared_account_keys={k[1]} if vezes.get(k[1]) == 1 else set(),
        )
        if conta_no_escopo(escopo, ch.plataforma, ch.conta):
            achadas.append(s)
    return achadas


async def perfil_do_chamado(session: AsyncSession, ch: Chamado) -> dict[str, Any]:
    """Qual perfil do AdsPower abre a loja deste chamado. A saída é a do
    Atendimento (`painel.perfil_adspower`): `{perfil, perfil_id, perfil_nome,
    loja, store_info_id, fonte, robo, aviso, motivo, codigo, no_espelho}`; sem
    perfil, `perfil` None e o `motivo` (a frase do botão desligado)."""
    saida: dict[str, Any] = {
        "perfil": None,
        "perfil_id": None,
        "perfil_nome": None,
        "loja": ch.conta,
        "store_info_id": None,
        "fonte": None,
        "robo": False,
        "aviso": None,
        "motivo": None,
        "codigo": None,
        "no_espelho": None,
    }
    no_espelho, lojas = await _lojas_pelo_pedido(session, ch)
    if no_espelho:
        saida["fonte"] = "pedido"
        if not lojas:
            saida.update(motivo=MOTIVO_SEM_CADASTRO_PEDIDO, codigo="sem_cadastro")
            return saida
    else:
        saida["fonte"] = "conta"
        lojas = await _lojas_pela_conta(session, ch)
        if not lojas:
            saida.update(motivo=MOTIVO_SEM_CADASTRO, codigo="sem_cadastro")
            return saida

    servidores = {(s.server or "").strip() for s in lojas} - {""}
    if len(servidores) > 1:
        # Duas lojas (lote de lojas diferentes, nome repetido): nunca chuta.
        saida.update(motivo=MOTIVO_AMBIGUO, codigo="ambiguo")
        return saida
    loja = sorted(lojas, key=lambda s: not (s.server or "").strip())[0]
    saida["store_info_id"] = str(loja.id)
    saida["loja"] = saida["loja"] or loja.account_name
    serial = (loja.server or "").strip()
    if not serial:
        saida.update(motivo=MOTIVO_SEM_PERFIL, codigo="sem_perfil")
        return saida
    saida["perfil"] = serial
    espelho = await _perfil_no_espelho(session, serial=serial)
    if espelho is None:
        saida["no_espelho"] = None
    elif not espelho:
        saida["no_espelho"] = False
        saida["aviso"] = (
            f"O nº {serial} não aparece na lista de perfis do AdsPower que o DaVinci "
            "conhece — se não abrir, confira o campo Servidor da loja."
        )
    else:
        saida["no_espelho"] = True
        saida["perfil_nome"] = espelho.get("nome")
        saida["perfil_id"] = espelho.get("id")
    return saida
