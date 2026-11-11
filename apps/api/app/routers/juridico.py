"""Ouvidoria › Jurídico — os processos judiciais (11/11/2026).

Vinicius: painel no padrão da Conciliação/Chamados, "de forma simples, sem informação
desnecessária": data, nº do processo, origem (Denúncia / Logística / à mão), autor, réu,
advogado, última movimentação, Jusbrasil; escolheu a Opção B (+ Assunto, Situação e
Raio-X). No mesmo dia: o Próximo prazo saiu e entrou o Pedido / referência.

De onde vêm as linhas:
- **Denúncia** — todo caso que foi ao advogado entra sozinho quando a lista abre. O nº do
  processo, o link do Jusbrasil e a última movimentação já digitados no caso vêm junto
  na primeira vez; daí em diante quem manda é este painel. A referência dele é o próprio
  caso (Caso 006) — 11/11, Vinicius: o pedido da compra de prova "vai confundir".
- **Logística** — os chamados encaminhados ao jurídico (Chamados › "Encaminhar ao
  jurídico") entram sozinhos; um processo de uma venda também pode ser incluído à mão.
- **Manual** — o "+ novo processo".

Permissão: a mesma da Denúncia (`denuncia`), que já é quem vê o jurídico hoje.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Annotated, Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.deps.auth import require_permission
from app.models.chamado import Chamado
from app.models.denuncia import DenunciaCaso, DenunciaJuridicoPasta, DenunciaRemetente
from app.models.juridico import (
    LADOS,
    SITUACOES,
    JuridicoLeitura,
    JuridicoMovimentacao,
    JuridicoPedido,
    JuridicoProcesso,
)
from app.models.user import User
from app.routers import denuncia as den
from app.services import chamados_juridico, juridico_diario

router = APIRouter(prefix="/api/juridico", tags=["juridico"])
sync_router = APIRouter(prefix="/api/juridico/sync", tags=["juridico"], include_in_schema=False)

_ver = require_permission("denuncia", "view")
_editar = require_permission("denuncia", "edit")
_apagar = require_permission("denuncia", "delete")

_BRT = ZoneInfo("America/Sao_Paulo")

# Os casos da Denúncia que já são do advogado (o status de cima do "Com jurídico" também)
_STATUS_JURIDICO = ("Com jurídico", "Ajuizado")
# Quem entra com o processo nos casos da Denúncia (o certificado Anatel e a marca são dela)
AUTOR_DENUNCIA = "MAKISA Trading"

_CAMPOS_TEXTO = (
    "pedido", "numero", "autor", "autor_obs", "reu", "reu_obs", "advogado",
    "advogado_escritorio", "assunto", "vara", "jusbrasil_link", "observacao",
)
_CAMPOS_DATA = ("encaminhado_em",)


def _nome(u: User) -> str:
    return u.name or u.email


def _data(v: Any) -> date | None:
    """'2026-10-13', '2026-10-13T09:00:00' ou datetime → date (no horário de Brasília)."""
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return (v if v.tzinfo else v.replace(tzinfo=UTC)).astimezone(_BRT).date()
    if isinstance(v, date):
        return v
    s = str(v).strip()
    try:
        if len(s) > 10:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            return _data(dt) if dt.tzinfo else dt.date()
        return datetime.strptime(s[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _assunto_do_caso(c: dict) -> str:
    """Caso com anúncio no certificado nosso = uso do certificado Anatel; sem ele (outro nº ou
    nenhum), o caso é a marca Uranyx no título (ex.: Caso 004). Dá pra trocar no Raio-X."""
    if (c.get("nosso") or 0) > 0 or c.get("grupo") == "GRUPO 1":
        return "Certificado Anatel"
    return "Marca Uranyx"


async def sincronizar(session: AsyncSession) -> int:
    """Põe no painel os casos da Denúncia que foram ao advogado e os chamados encaminhados ao
    jurídico. Não muda o que já foi editado: só preenche o que está vazio. Devolve quantas
    linhas novas entraram."""
    novos = 0
    existentes = {
        p.denuncia_caso_id: p
        for p in (
            await session.execute(
                select(JuridicoProcesso).where(JuridicoProcesso.denuncia_caso_id.is_not(None))
            )
        ).scalars()
    }
    casos = (await den._casos(session))["itens"]
    for c in casos:
        enviado = _data(c.get("juridico_enviado_em"))
        if not enviado and c.get("status") not in _STATUS_JURIDICO:
            continue
        p = existentes.get(c["id"])
        if p is not None:
            # só o que está vazio (o caso pode ter recebido a data depois)
            if p.encaminhado_em is None and enviado:
                p.encaminhado_em = enviado
            if not p.reu and c.get("loja"):
                p.reu = c["loja"]
                p.reu_obs = p.reu_obs or c.get("marketplace")
            if not p.advogado and c.get("juridico"):
                p.advogado = c["juridico"]
            continue
        extra = c.get("extra") or {}
        numero = (extra.get("processo_numero") or "").strip() or None
        ins = (
            pg_insert(JuridicoProcesso)
            .values(
                origem="denuncia",
                denuncia_caso_id=c["id"],
                encaminhado_em=enviado,
                numero=numero,
                lado="autor",
                autor=AUTOR_DENUNCIA,
                autor_obs="nós",
                reu=c.get("loja"),
                reu_obs=c.get("marketplace"),
                advogado=c.get("juridico"),
                assunto=_assunto_do_caso(c),
                situacao="em_andamento" if numero else "aguardando_numero",
                jusbrasil_link=(extra.get("processo_link") or "").strip() or None,
                criado_por="DaVinci (caso da Denúncia)",
            )
            .on_conflict_do_nothing(
                index_elements=["denuncia_caso_id"],
                index_where=text("denuncia_caso_id IS NOT NULL"),
            )
            .returning(JuridicoProcesso.id)
        )
        pid = (await session.execute(ins)).scalar_one_or_none()
        if pid is None:
            continue
        novos += 1
        # a última movimentação que já estava digitada no caso
        texto = (extra.get("mov_texto") or extra.get("mov_status") or "").strip()
        if texto:
            session.add(
                JuridicoMovimentacao(
                    processo_id=pid,
                    data=_data(extra.get("mov_data")) or enviado or datetime.now(_BRT).date(),
                    texto=texto,
                    fonte="manual",
                    criado_por=extra.get("atualizado_por"),
                )
            )

    chamados = (
        await session.execute(select(Chamado).where(Chamado.juridico_enviado_at.is_not(None)))
    ).scalars().all()
    for ch in chamados:
        ins = (
            pg_insert(JuridicoProcesso)
            .values(
                origem="logistica",
                chamado_id=ch.id,
                pedido=ch.pedido_bling,
                encaminhado_em=_data(ch.juridico_enviado_at),
                lado="autor",
                assunto=None,
                situacao="aguardando_numero",
                observacao=ch.juridico_obs,
                criado_por="DaVinci (chamado encaminhado ao jurídico)",
            )
            .on_conflict_do_nothing(
                index_elements=["chamado_id"], index_where=text("chamado_id IS NOT NULL")
            )
            .returning(JuridicoProcesso.id)
        )
        if (await session.execute(ins)).scalar_one_or_none() is not None:
            novos += 1
    await session.commit()
    return novos


def _mov_dict(m: JuridicoMovimentacao | None, completo: bool = False) -> dict | None:
    if m is None:
        return None
    out = {
        "id": m.id, "data": m.data.isoformat(), "texto": m.texto, "fonte": m.fonte,
        "criado_por": m.criado_por, "orgao": m.orgao, "tipo": m.tipo, "link": m.link,
    }
    if completo:  # o texto inteiro da publicação só na janela do processo
        out["texto_completo"] = m.texto_completo
    return out


def _dict(
    p: JuridicoProcesso, ultima: JuridicoMovimentacao | None, caso_codigo: str | None
) -> dict:
    return {
        "id": p.id,
        "origem": p.origem,
        "denuncia_caso_id": p.denuncia_caso_id,
        "caso_codigo": caso_codigo,
        "chamado_id": str(p.chamado_id) if p.chamado_id else None,
        "pedido": p.pedido,
        "encaminhado_em": p.encaminhado_em.isoformat() if p.encaminhado_em else None,
        "numero": p.numero,
        "lado": p.lado,
        "autor": p.autor,
        "autor_obs": p.autor_obs,
        "reu": p.reu,
        "reu_obs": p.reu_obs,
        "advogado": p.advogado,
        "advogado_escritorio": p.advogado_escritorio,
        "assunto": p.assunto,
        "situacao": p.situacao,
        "vara": p.vara,
        "valor_causa": float(p.valor_causa) if p.valor_causa is not None else None,
        "jusbrasil_link": p.jusbrasil_link,
        "jusbrasil_tentado_em": (
            p.jusbrasil_tentado_em.isoformat() if p.jusbrasil_tentado_em else None
        ),
        "jusbrasil_erro": p.jusbrasil_erro,
        "observacao": p.observacao,
        "ultima_mov": _mov_dict(ultima),
        "criado_por": p.criado_por,
        "atualizado_por": p.atualizado_por,
        "atualizado_em": p.atualizado_em.isoformat() if p.atualizado_em else None,
    }


async def _ultimas(session: AsyncSession, ids: list[int]) -> dict[int, JuridicoMovimentacao]:
    if not ids:
        return {}
    mov = JuridicoMovimentacao
    rows = (
        await session.execute(
            select(mov).where(mov.processo_id.in_(ids)).order_by(mov.data.desc(), mov.id.desc())
        )
    ).scalars().all()
    out: dict[int, JuridicoMovimentacao] = {}
    for m in rows:  # a primeira de cada processo é a mais nova
        out.setdefault(m.processo_id, m)
    return out


async def _codigos_casos(session: AsyncSession, ids: set[int]) -> dict[int, str | None]:
    if not ids:
        return {}
    q = select(DenunciaCaso.id, DenunciaCaso.codigo).where(DenunciaCaso.id.in_(ids))
    return dict((await session.execute(q)).all())


async def _processo(session: AsyncSession, processo_id: int) -> JuridicoProcesso:
    p = await session.get(JuridicoProcesso, processo_id)
    if p is None:
        raise HTTPException(404, detail={"code": "juridico_processo_nao_encontrado"})
    return p


@router.get("/processos")
async def listar_processos(
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(_ver)],
) -> dict:
    await sincronizar(session)
    procs = (
        await session.execute(
            select(JuridicoProcesso).order_by(
                JuridicoProcesso.encaminhado_em.desc().nulls_last(), JuridicoProcesso.id.desc()
            )
        )
    ).scalars().all()
    ultimas = await _ultimas(session, [p.id for p in procs])
    casos = {p.denuncia_caso_id for p in procs if p.denuncia_caso_id}
    codigos = await _codigos_casos(session, casos)
    leituras = {
        origem: (
            await session.execute(
                select(JuridicoLeitura)
                .where(JuridicoLeitura.origem == origem)
                .order_by(JuridicoLeitura.quando.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        for origem in ("mac_mini", "navegador", "jusbrasil")
    }
    ultimo_pedido = (
        await session.execute(select(JuridicoPedido).order_by(JuridicoPedido.id.desc()).limit(1))
    ).scalar_one_or_none()
    return {
        "hoje": datetime.now(_BRT).date().isoformat(),
        "itens": [_dict(p, ultimas.get(p.id), codigos.get(p.denuncia_caso_id)) for p in procs],
        # o topo do painel: a última leitura do Diário pelo Mac mini (07:30) e pelo botão
        "diario": {k: _leitura_dict(v) for k, v in leituras.items()},
        # o último pedido de leitura ao Mac mini (06:00 ou botão Atualizar) e como ele está
        "pedido": _pedido_dict(ultimo_pedido),
    }


@router.get("/processos/{processo_id}")
async def ver_processo(
    processo_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(_ver)],
) -> dict:
    p = await _processo(session, processo_id)
    movs = (
        await session.execute(
            select(JuridicoMovimentacao)
            .where(JuridicoMovimentacao.processo_id == p.id)
            .order_by(JuridicoMovimentacao.data.desc(), JuridicoMovimentacao.id.desc())
        )
    ).scalars().all()
    codigos = await _codigos_casos(session, {p.denuncia_caso_id} if p.denuncia_caso_id else set())
    out = _dict(p, movs[0] if movs else None, codigos.get(p.denuncia_caso_id))
    out["movimentacoes"] = [_mov_dict(m, completo=True) for m in movs]
    docs: dict[str, Any] = {"pasta": None, "pasta_url": None, "dossie_url": None}
    if p.denuncia_caso_id:
        pasta = (
            await session.execute(
                select(DenunciaJuridicoPasta)
                .where(DenunciaJuridicoPasta.caso_id == p.denuncia_caso_id)
                .order_by(DenunciaJuridicoPasta.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        docs["pasta"] = den._pasta_resumo(pasta)
        if pasta is not None and pasta.arquivo_local:
            docs["pasta_url"] = f"/api/denuncia/casos/{p.denuncia_caso_id}/pasta/arquivo"
    if p.chamado_id:
        ch = await session.get(Chamado, p.chamado_id)
        if ch is not None and ch.juridico_token and ch.juridico_enviado_at:
            docs["dossie_url"] = chamados_juridico.link_dossie(ch.juridico_token)
    out["documentos"] = docs
    return out


def _aplicar(p: JuridicoProcesso, corpo: dict) -> None:
    """Só os campos enviados mudam; "" apaga."""
    for k in _CAMPOS_TEXTO:
        if k in corpo:
            v = str(corpo[k] or "").strip()[:2000]
            setattr(p, k, v or None)
    for k in _CAMPOS_DATA:
        if k in corpo:
            v = str(corpo[k] or "").strip()
            if v and _data(v) is None:
                raise HTTPException(422, detail={"code": "juridico_data_invalida", "campo": k})
            setattr(p, k, _data(v))
    if "lado" in corpo:
        if corpo["lado"] not in LADOS:
            raise HTTPException(422, detail={"code": "juridico_lado_invalido"})
        p.lado = corpo["lado"]
    if "valor_causa" in corpo:
        v = corpo["valor_causa"]
        if v is None or str(v).strip() == "":
            p.valor_causa = None
        else:
            s = str(v).strip().replace("R$", "").replace(" ", "")
            if "," in s:  # 1.249,90
                s = s.replace(".", "").replace(",", ".")
            try:
                p.valor_causa = Decimal(s).quantize(Decimal("0.01"))
            except InvalidOperation:
                raise HTTPException(422, detail={"code": "juridico_valor_invalido"}) from None
    if "situacao" in corpo:
        if corpo["situacao"] not in SITUACOES:
            raise HTTPException(422, detail={"code": "juridico_situacao_invalida"})
        p.situacao = corpo["situacao"]
    elif "numero" in corpo and p.numero and p.situacao == "aguardando_numero":
        # chegou o nº: o processo passa a andar
        p.situacao = "em_andamento"
    if p.jusbrasil_link and not p.jusbrasil_link.startswith(("https://", "http://")):
        raise HTTPException(422, detail={"code": "juridico_link_invalido"})


@router.post("/processos")
async def criar_processo(
    session: Annotated[AsyncSession, Depends(get_session)],
    u: Annotated[User, Depends(_editar)],
    corpo: Annotated[dict, Body()],
) -> dict:
    """+ novo processo: origem 'manual' (padrão) ou 'logistica' (com o pedido)."""
    origem = corpo.get("origem") or "manual"
    if origem not in ("manual", "logistica"):
        raise HTTPException(422, detail={"code": "juridico_origem_invalida"})
    p = JuridicoProcesso(
        origem=origem, lado="autor", situacao="aguardando_numero", criado_por=_nome(u),
        atualizado_por=_nome(u), encaminhado_em=datetime.now(_BRT).date(),
    )
    _aplicar(p, {k: v for k, v in corpo.items() if k != "origem"})
    if p.numero and "situacao" not in corpo:
        p.situacao = "em_andamento"
    session.add(p)
    await session.commit()
    await session.refresh(p)
    return {"ok": True, "processo": _dict(p, None, None)}


@router.put("/processos/{processo_id}")
async def editar_processo(
    processo_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    u: Annotated[User, Depends(_editar)],
    corpo: Annotated[dict, Body()],
) -> dict:
    p = await _processo(session, processo_id)
    _aplicar(p, corpo)
    p.atualizado_por = _nome(u)
    await session.commit()
    await session.refresh(p)
    ultima = (await _ultimas(session, [p.id])).get(p.id)
    codigos = await _codigos_casos(session, {p.denuncia_caso_id} if p.denuncia_caso_id else set())
    return {"ok": True, "processo": _dict(p, ultima, codigos.get(p.denuncia_caso_id))}


@router.delete("/processos/{processo_id}")
async def apagar_processo(
    processo_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(_apagar)],
) -> dict:
    """Só o que foi incluído à mão: o que veio de um caso ou de um chamado voltaria sozinho."""
    p = await _processo(session, processo_id)
    if p.denuncia_caso_id or p.chamado_id:
        raise HTTPException(409, detail={"code": "juridico_processo_automatico"})
    await session.delete(p)
    await session.commit()
    return {"ok": True}


@router.post("/processos/{processo_id}/diario")
async def lancar_do_diario(
    processo_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(_editar)],
    corpo: Annotated[dict, Body()],
) -> dict:
    """Botão "Buscar no Diário": o NAVEGADOR consulta o DJEN (o servidor leva 403, fica fora do
    Brasil) e manda aqui as comunicações do processo — {itens: [...]}, como o DJEN devolve."""
    p = await _processo(session, processo_id)
    try:
        itens = juridico_diario.itens_validos(corpo)
    except ValueError:
        raise HTTPException(422, detail={"code": "juridico_itens_invalidos"}) from None
    novas = await juridico_diario.lancar(session, p, itens)
    await session.commit()
    return {"ok": True, "novas": novas, "publicacoes": len(itens)}


# ── Mac mini da Makisa (Vigia do Jurídico, 07:30): o mesmo token da cópia da Denúncia ─────────
@sync_router.get("/processos")
async def sync_processos(
    session: Annotated[AsyncSession, Depends(get_session)],
    _r: Annotated[DenunciaRemetente, Depends(den._remetente)],
) -> dict:
    """Os processos que o mini deve consultar no DJEN: com nº de 20 dígitos, fora os encerrados."""
    procs = (
        await session.execute(
            select(JuridicoProcesso)
            .where(JuridicoProcesso.numero.is_not(None), JuridicoProcesso.situacao != "encerrado")
            .order_by(JuridicoProcesso.id)
        )
    ).scalars().all()
    com_jus = set(
        (
            await session.execute(
                select(JuridicoMovimentacao.processo_id)
                .where(JuridicoMovimentacao.fonte == "jusbrasil")
                .distinct()
            )
        ).scalars()
    )
    return {
        "processos": [
            {
                "id": p.id,
                "numero": juridico_diario.numero_20(p.numero),
                "jusbrasil_link": p.jusbrasil_link,
                # já leu o histórico inteiro no Jusbrasil uma vez? (senão carrega o "Mostrar mais")
                "tem_jusbrasil": p.id in com_jus,
            }
            for p in procs
            if juridico_diario.numero_20(p.numero)
        ]
    }


@sync_router.post("/processos/{processo_id}/diario")
async def sync_lancar_do_diario(
    processo_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    _r: Annotated[DenunciaRemetente, Depends(den._remetente)],
    corpo: Annotated[dict, Body()],
) -> dict:
    p = await _processo(session, processo_id)
    try:
        itens = juridico_diario.itens_validos(corpo)
    except ValueError:
        raise HTTPException(422, detail={"code": "juridico_itens_invalidos"}) from None
    novas = await juridico_diario.lancar(session, p, itens)
    await session.commit()
    return {"ok": True, "novas": novas, "publicacoes": len(itens)}


@sync_router.post("/leitura")
async def sync_leitura(
    session: Annotated[AsyncSession, Depends(get_session)],
    r: Annotated[DenunciaRemetente, Depends(den._remetente)],
    corpo: Annotated[dict, Body()],
) -> dict:
    """Fim de uma leitura do mini: {origem?, processos, publicacoes, novas, falhas, erro?} —
    `origem` 'mac_mini' (o Diário, padrão) ou 'jusbrasil'."""
    origem = "jusbrasil" if corpo.get("origem") == "jusbrasil" else "mac_mini"
    await juridico_diario.registrar_leitura(session, origem, r.nome, corpo)
    await session.commit()
    return {"ok": True}


@sync_router.post("/processos/{processo_id}/jusbrasil")
async def sync_lancar_do_jusbrasil(
    processo_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    _r: Annotated[DenunciaRemetente, Depends(den._remetente)],
    corpo: Annotated[dict, Body()],
) -> dict:
    """O que o robô do mini leu na página do processo no Jusbrasil: {link, andamentos: [{data
    'dd/mm/aaaa', tipo, texto}]}. O link entra no campo Jusbrasil se estiver vazio. Com `erro`
    (não achou o processo, a página falhou), só registra a tentativa — a coluna Jusbrasil mostra."""
    p = await _processo(session, processo_id)
    andamentos = corpo.get("andamentos")
    if not isinstance(andamentos, list):
        raise HTTPException(422, detail={"code": "juridico_itens_invalidos"})
    p.jusbrasil_tentado_em = datetime.now(UTC)
    erro = str(corpo.get("erro") or "").strip()[:300]
    if erro:
        p.jusbrasil_erro = erro
        await session.commit()
        return {"ok": True, "novas": 0, "andamentos": 0}
    p.jusbrasil_erro = None
    novas = await juridico_diario.lancar_jusbrasil(session, p, corpo.get("link"), andamentos)
    await session.commit()
    return {"ok": True, "novas": novas, "andamentos": len(andamentos)}


@sync_router.get("/pedido")
async def sync_pegar_pedido(
    session: Annotated[AsyncSession, Depends(get_session)],
    _r: Annotated[DenunciaRemetente, Depends(den._remetente)],
) -> dict:
    """O vigia do mini pergunta a cada minuto: o pedido mais antigo ainda não pego (≤ 3 h) é
    dele — marca `pego_em` e devolve."""
    agora = datetime.now(UTC)
    ped = (
        await session.execute(
            select(JuridicoPedido)
            .where(
                JuridicoPedido.pego_em.is_(None),
                JuridicoPedido.terminado_em.is_(None),
                JuridicoPedido.pedido_em >= agora - juridico_diario.PEDIDO_MORTO,
            )
            .order_by(JuridicoPedido.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
    ).scalar_one_or_none()
    if ped is None:
        return {"pedido": None}
    ped.pego_em = agora
    ped.progresso = "o Mac mini pegou o pedido"
    await session.commit()
    return {"pedido": _pedido_dict(ped)}


@sync_router.post("/pedido/{pedido_id}")
async def sync_andamento_do_pedido(
    pedido_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    _r: Annotated[DenunciaRemetente, Depends(den._remetente)],
    corpo: Annotated[dict, Body()],
) -> dict:
    """{progresso?, terminado?, ok?, resultado?} — o topo do painel mostra."""
    ped = await session.get(JuridicoPedido, pedido_id)
    if ped is None:
        raise HTTPException(404, detail={"code": "juridico_pedido_nao_encontrado"})
    if corpo.get("progresso"):
        ped.progresso = str(corpo["progresso"])[:300]
    if corpo.get("terminado"):
        ped.terminado_em = datetime.now(UTC)
        ped.ok = bool(corpo.get("ok", True))
        ped.resultado = str(corpo.get("resultado") or "")[:500] or None
    await session.commit()
    return {"ok": True}


def _pedido_dict(ped: JuridicoPedido | None) -> dict | None:
    if ped is None:
        return None
    return {
        "id": ped.id, "origem": ped.origem, "pedido_por": ped.pedido_por,
        "jusbrasil": ped.jusbrasil, "processo_id": ped.processo_id,
        "pedido_em": ped.pedido_em.isoformat(),
        "pego_em": ped.pego_em.isoformat() if ped.pego_em else None,
        "progresso": ped.progresso,
        "terminado_em": ped.terminado_em.isoformat() if ped.terminado_em else None,
        "ok": ped.ok, "resultado": ped.resultado,
    }


@router.post("/atualizar")
async def pedir_atualizacao(
    session: Annotated[AsyncSession, Depends(get_session)],
    u: Annotated[User, Depends(_editar)],
) -> dict:
    """Botão Atualizar: o Diário o navegador busca na hora; aqui só o pedido do Jusbrasil ao
    Mac mini (no máximo um a cada 2 h)."""
    ped, motivo = await juridico_diario.pedir_leitura(session, "botao", _nome(u))
    await session.commit()
    return {"pedido": _pedido_dict(ped), "motivo": motivo}


@router.post("/processos/{processo_id}/atualizar")
async def pedir_atualizacao_do_processo(
    processo_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    u: Annotated[User, Depends(_editar)],
) -> dict:
    """Atualizar da janela do processo: o Diário o navegador busca na hora; aqui o pedido do
    Jusbrasil SÓ deste processo ao Mac mini (no máximo um a cada 30 min por processo)."""
    p = await _processo(session, processo_id)
    if not juridico_diario.numero_20(p.numero):
        raise HTTPException(422, detail={"code": "juridico_sem_numero"})
    ped, motivo = await juridico_diario.pedir_leitura(session, "botao", _nome(u), processo=p)
    await session.commit()
    return {"pedido": _pedido_dict(ped), "motivo": motivo}


@router.post("/leituras")
async def registrar_leitura_do_navegador(
    session: Annotated[AsyncSession, Depends(get_session)],
    u: Annotated[User, Depends(_editar)],
    corpo: Annotated[dict, Body()],
) -> dict:
    """Fim do "Buscar no Diário" do topo do painel: quantos processos, quantas publicações novas."""
    await juridico_diario.registrar_leitura(session, "navegador", _nome(u), corpo)
    await session.commit()
    return {"ok": True}


def _leitura_dict(lei: JuridicoLeitura | None) -> dict | None:
    if lei is None:
        return None
    return {
        "quando": lei.quando.isoformat(), "origem": lei.origem, "por": lei.por,
        "processos": lei.processos, "publicacoes": lei.publicacoes, "novas": lei.novas,
        "falhas": lei.falhas, "erro": lei.erro,
    }


@router.post("/processos/{processo_id}/movimentacoes")
async def nova_movimentacao(
    processo_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    u: Annotated[User, Depends(_editar)],
    corpo: Annotated[dict, Body()],
) -> dict:
    p = await _processo(session, processo_id)
    texto = str(corpo.get("texto") or "").strip()[:2000]
    if not texto:
        raise HTTPException(422, detail={"code": "juridico_movimentacao_vazia"})
    bruto = str(corpo.get("data") or "").strip()
    data = _data(bruto) if bruto else datetime.now(_BRT).date()
    if data is None:
        raise HTTPException(422, detail={"code": "juridico_data_invalida", "campo": "data"})
    m = JuridicoMovimentacao(
        processo_id=p.id, data=data, texto=texto, fonte="manual", criado_por=_nome(u)
    )
    session.add(m)
    p.atualizado_por = _nome(u)
    await session.commit()
    await session.refresh(m)
    return {"ok": True, "movimentacao": _mov_dict(m)}


@router.delete("/movimentacoes/{mov_id}")
async def apagar_movimentacao(
    mov_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    _u: Annotated[User, Depends(_editar)],
) -> dict:
    m = await session.get(JuridicoMovimentacao, mov_id)
    if m is None:
        raise HTTPException(404, detail={"code": "juridico_movimentacao_nao_encontrada"})
    await session.delete(m)
    await session.commit()
    return {"ok": True}
