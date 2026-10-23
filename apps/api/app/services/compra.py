"""Cadastros › Contabilidade › Compra (23/10/2026) — regras.

A empresa do grupo (comprador) compra de uma das contas Bling de NF (vendedora)
o produto do catálogo (nasce "Embalagem", NCM 3923.21.10), com base no
faturamento do mês da compradora. A compra registrada vira a planilha de
pedidos do Bling da vendedora (o CSV de 41 colunas de `nf_relatorio`); a NF-e
sai lá. O CSV não leva IE, NCM nem CFOP: o NCM vem do produto no Bling (casado
pelo SKU), o CFOP da natureza de operação e a IE do contato — a tela avisa.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import date, datetime
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Company, CompanyFiscal, NfNota
from app.models.compra import CompraPedido, CompraPedidoItem, CompraProduto, CompraVendedora
from app.schemas.compra import CompraIn, Endereco
from app.services.nf_emissao import NfLinha
from app.services.nf_relatorio import PedidoInfo, montar_csv
from app.services.nfse import contas_bling
from app.services.nfse import faturamento as svc_fat
from app.services.nfse.erros import NfseError
from app.services.nfse.municipios import nome_do_codigo

SP = ZoneInfo("America/Sao_Paulo")
CENTAVO = Decimal("0.01")
MAX_TOTAL = Decimal("999999999999.99")  # NUMERIC(14, 2)
# Sem isso a planilha do Bling não monta a NF (endereço do destinatário).
CAMPOS_ENDERECO = {
    "logradouro": "rua",
    "numero": "número",
    "bairro": "bairro",
    "cep": "CEP",
    "municipio": "cidade",
    "uf": "UF",
}


def _digitos(v: object) -> str:
    return re.sub(r"\D", "", str(v or ""))


def hoje_sp() -> date:
    return datetime.now(SP).date()


def fmt_cnpj(c: str) -> str:
    d = _digitos(c)
    if len(d) != 14:
        return d
    return f"{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:]}"


def cfop_sugerido(uf_vendedor: str | None, uf_comprador: str | None) -> str:
    """5102 dentro do estado, 6102 para outro estado (venda de mercadoria de
    terceiros, como a NF-e 13917 do modelo). UF desconhecida = 5102."""
    if uf_vendedor and uf_comprador and uf_vendedor.upper() != uf_comprador.upper():
        return "6102"
    return "5102"


def quantidade_pelo_percentual(faturamento: Decimal, percentual: Decimal, preco: Decimal) -> int:
    """Quantas unidades cabem em `percentual`% do faturamento ao `preco` cada
    (arredonda para baixo: nunca passa do percentual)."""
    if faturamento <= 0 or percentual <= 0 or preco <= 0:
        return 0
    alvo = faturamento * percentual / Decimal(100)
    return int((alvo / preco).to_integral_value(rounding=ROUND_FLOOR))


def total_da_linha(quantidade: int, unitario: Decimal) -> Decimal:
    return (Decimal(quantidade) * unitario).quantize(CENTAVO, rounding=ROUND_HALF_UP)


# --- quem vende ---------------------------------------------------------------------------


async def vendedoras(session: AsyncSession, *, todas: bool = False) -> list[dict]:
    """As contas Bling de NF de onde se compra: o cadastro `compra_vendedora`
    (nasce com as 4 — BEZERRA, TELES, DL, PEDRO FIORAVANTE). Município e as notas
    dos últimos 90 dias vêm das NFs dela no DaVinci (`contas_bling`), quando há.
    `todas` inclui as desativadas (tela de cadastro)."""
    q = select(CompraVendedora).order_by(CompraVendedora.automatico.desc(), CompraVendedora.nome)
    if not todas:
        q = q.where(CompraVendedora.ativo.is_(True))
    cad = list((await session.execute(q)).scalars())
    if not cad:
        return []
    notas = {c.cnpj: c for c in await contas_bling.contas_de_nf(session)}
    out = []
    for v in cad:
        c = notas.get(v.cnpj)
        lugar = nome_do_codigo((c.endereco or {}).get("cmun_ibge")) if c else None
        out.append(
            {
                "cnpj": v.cnpj,
                "nome": v.nome,
                "uf": v.uf or (lugar[1] if lugar else None),
                "municipio": lugar[0] if lugar else None,
                "notas_90d": c.notas if c else 0,
                "ultima": c.ultima if c else None,
                "adspower_serial": v.adspower_serial,
                "automatico": v.automatico,
                "ativo": v.ativo,
                "observacao": v.observacao,
            }
        )
    return out


async def vendedora(session: AsyncSession, cnpj: str) -> dict | None:
    d = _digitos(cnpj)
    return next((v for v in await vendedoras(session) if v["cnpj"] == d), None)


# --- quem compra -----------------------------------------------------------------------------


def _endereco_nfeio(f: CompanyFiscal | None) -> dict:
    end = ((f.nfeio_resumo or {}).get("endereco") or {}) if f is not None else {}
    if not end:
        return {}
    cidade = end.get("city") or {}
    out = {
        "logradouro": end.get("street"),
        "numero": end.get("number"),
        "complemento": end.get("additionalInformation"),
        "bairro": end.get("district"),
        "cep": _digitos(end.get("postalCode"))[:8] or None,
        "municipio": cidade.get("name"),
        "uf": (end.get("state") or "").upper() or None,
    }
    return {k: str(v).strip() for k, v in out.items() if v not in (None, "")}


def _endereco_seguro(d: dict) -> dict:
    """Endereco validado campo a campo: o que vier torto (CEP com 7 números, UF
    estranha) fica vazio para a tela pedir, em vez de derrubar a rota."""
    out = {}
    for k, v in d.items():
        if v in (None, ""):
            continue
        try:
            val = getattr(Endereco(**{k: str(v)}), k)
        except ValueError:
            continue
        if val is not None:
            out[k] = val
    return Endereco(**out).model_dump()


def _ie_do_emitente(xml: bytes | None) -> str | None:
    if not xml:
        return None
    try:
        raiz = ET.fromstring(xml)  # noqa: S314 — XML nosso (nf_nota), só leitura
    except ET.ParseError:
        return None
    emit = next((el for el in raiz.iter() if el.tag.rsplit("}", 1)[-1] == "emit"), None)
    if emit is None:
        return None
    ie = next((el for el in emit if el.tag.rsplit("}", 1)[-1] == "IE"), None)
    d = _digitos(ie.text if ie is not None else "")
    return d or None


async def _nota_mais_nova_da_empresa(session: AsyncSession, cnpj: str) -> NfNota | None:
    alvo = func.regexp_replace(func.coalesce(NfNota.emitente_cnpj, ""), r"\D", "", "g")
    return (
        await session.execute(
            select(NfNota)
            .where(alvo == cnpj, NfNota.xml.is_not(None))
            .order_by(NfNota.data_emissao.desc().nulls_last())
            .limit(1)
        )
    ).scalar_one_or_none()


async def comprador(session: AsyncSession, company_id: UUID) -> dict:
    """Razão, CNPJ, IE e endereço da empresa do grupo como vão na planilha.
    Endereço: o da NFE.io (empresa ligada) → o da última NF que a própria
    empresa emitiu → nada (a tela pede para digitar)."""
    empresa = await session.get(Company, company_id)
    if empresa is None:
        raise NfseError(404, "empresa_nao_encontrada", "Empresa não encontrada.")
    cnpj = _digitos(empresa.cnpj)
    if len(cnpj) != 14:
        raise NfseError(
            422, "sem_cnpj", "A empresa não tem CNPJ cadastrado (Cadastros › Empresas)."
        )
    fiscal = await session.get(CompanyFiscal, company_id)
    endereco = _endereco_nfeio(fiscal)
    origem = "nfeio" if endereco.get("logradouro") else None
    ie = _digitos(empresa.inscricao_estadual) or None
    if origem is None or ie is None:
        nota = await _nota_mais_nova_da_empresa(session, cnpj)
        if nota is not None:
            if origem is None:
                e = contas_bling.endereco_do_emitente(nota.xml)
                if e.get("logradouro"):
                    lugar = nome_do_codigo(e.get("cmun_ibge"))
                    endereco = {
                        k: v
                        for k, v in {
                            "logradouro": e.get("logradouro"),
                            "numero": e.get("numero"),
                            "complemento": e.get("complemento"),
                            "bairro": e.get("bairro"),
                            "cep": e.get("cep"),
                            "municipio": lugar[0] if lugar else None,
                            "uf": lugar[1] if lugar else None,
                        }.items()
                        if v
                    }
                    origem = "nota"
            if ie is None:
                ie = _ie_do_emitente(nota.xml)
    if not endereco.get("uf") and empresa.uf:
        endereco["uf"] = empresa.uf.upper()
    return {
        "company_id": empresa.id,
        "razao_social": empresa.razao_social,
        "apelido": empresa.apelido or None,
        "cnpj": cnpj,
        "inscricao_estadual": ie,
        "endereco": _endereco_seguro(endereco),
        "endereco_origem": origem,
    }


async def faturamento(session: AsyncSession, company_id: UUID, competencia: date) -> dict:
    """Faturamento do mês da compradora (mesma régua da Emissão de Serviço e da
    aba Faturamento). `tem_loja` False = nenhuma loja com o CNPJ dela."""
    comp = competencia.replace(day=1)
    f = await svc_fat.faturamento_da_empresa(session, company_id, comp)
    if f is None:
        return {
            "company_id": company_id,
            "competencia": comp,
            "valor": Decimal("0.00"),
            "pedidos": 0,
            "lojas": [],
            "tem_loja": False,
        }
    r = f.resumo()
    return {
        "company_id": company_id,
        "competencia": comp,
        "valor": Decimal(r["valor"]),
        "pedidos": r["pedidos"],
        "lojas": r["lojas"],
        "tem_loja": True,
    }


# --- produtos ---------------------------------------------------------------------------------


async def produtos(session: AsyncSession, *, so_ativos: bool = False) -> list[dict]:
    usados = dict(
        (
            await session.execute(
                select(CompraPedidoItem.produto_id, func.count())
                .where(CompraPedidoItem.produto_id.is_not(None))
                .group_by(CompraPedidoItem.produto_id)
            )
        ).all()
    )
    q = select(CompraProduto).order_by(CompraProduto.ativo.desc(), func.lower(CompraProduto.nome))
    if so_ativos:
        q = q.where(CompraProduto.ativo.is_(True))
    out = []
    for p in (await session.execute(q)).scalars():
        d = {
            c: getattr(p, c)
            for c in ("id", "nome", "sku", "ncm", "cfop", "unidade", "preco_padrao", "ativo")
        }
        d["usado"] = int(usados.get(p.id, 0))
        out.append(d)
    return out


async def nome_em_uso(session: AsyncSession, nome: str, *, exceto: UUID | None = None) -> bool:
    q = (
        select(func.count())
        .select_from(CompraProduto)
        .where(func.lower(CompraProduto.nome) == nome.strip().lower())
    )
    if exceto is not None:
        q = q.where(CompraProduto.id != exceto)
    return bool((await session.execute(q)).scalar_one())


# --- compras ---------------------------------------------------------------------------------


def _faltando_no_endereco(end: dict) -> list[str]:
    return [rotulo for campo, rotulo in CAMPOS_ENDERECO.items() if not end.get(campo)]


async def montar(
    session: AsyncSession,
    dados: CompraIn,
    *,
    vendedora_atual: dict | None = None,
    comprador_atual: dict | None = None,
) -> dict:
    """Confere e monta tudo o que a compra grava (sem gravar). Na edição:
    `vendedora_atual` deixa manter a vendedora que já estava, mesmo que ela
    tenha saído da janela de 90 dias das contas Bling; `comprador_atual` (a
    foto gravada, só quando a empresa não mudou) é a base do endereço quando a
    tela não manda endereço — senão o endereço digitado na compra voltaria ao
    automático (revisão 23/10)."""
    hoje = hoje_sp()
    comp = dados.competencia.replace(day=1)
    if comp > hoje.replace(day=1):
        raise NfseError(
            422, "mes_futuro", "O mês do faturamento não pode ser depois do mês de hoje."
        )
    compr = await comprador(session, dados.company_id)
    vend = await vendedora(session, dados.vendedor_cnpj)
    if vend is None and vendedora_atual and vendedora_atual["cnpj"] == dados.vendedor_cnpj:
        vend = vendedora_atual
    if vend is None:
        raise NfseError(
            422,
            "vendedora_invalida",
            "Essa empresa não está entre as vendedoras ativas "
            "(Contabilidade › Compra › Vendedoras).",
        )
    if vend["cnpj"] == compr["cnpj"]:
        raise NfseError(
            422, "mesma_empresa", "A empresa que compra não pode ser a mesma que vende."
        )

    fat = await faturamento(session, dados.company_id, comp)
    if dados.modo == "percentual":
        if dados.percentual is None:
            raise NfseError(422, "sem_percentual", "Digite o % do faturamento.")
        if not fat["tem_loja"] or fat["valor"] <= 0:
            raise NfseError(
                422,
                "sem_faturamento",
                "Essa empresa não tem faturamento nesse mês: use a quantidade direto.",
            )

    base = compr
    if comprador_atual and comprador_atual.get("endereco") and dados.endereco is None:
        base = comprador_atual
    endereco = dict(base["endereco"])
    origem = base.get("endereco_origem")
    if dados.endereco is not None:
        # o que veio na tela vale campo a campo, inclusive vazio (apagar o complemento)
        endereco.update(dados.endereco.model_dump(exclude_unset=True))
        origem = "digitado"
    falta = _faltando_no_endereco(endereco)
    if falta:
        raise NfseError(
            422,
            "endereco_incompleto",
            f"Falta no endereço da empresa que compra: {', '.join(falta)}.",
            faltando=falta,
        )
    ie = dados.inscricao_estadual or compr["inscricao_estadual"]
    cfop = dados.cfop or cfop_sugerido(vend.get("uf"), endereco.get("uf"))

    itens = []
    total = Decimal("0.00")
    for ordem, it in enumerate(dados.itens):
        if it.produto_id is not None and await session.get(CompraProduto, it.produto_id) is None:
            raise NfseError(
                422, "produto_nao_encontrado", f"O produto da linha {ordem + 1} não existe mais."
            )
        vt = total_da_linha(it.quantidade, it.valor_unitario)
        total += vt
        if total > MAX_TOTAL:
            raise NfseError(
                422,
                "total_grande",
                "O total da compra passou do limite: confira a quantidade e o preço.",
            )
        itens.append(
            {
                "produto_id": it.produto_id,
                "ordem": ordem,
                "nome": it.nome,
                "sku": it.sku,
                "ncm": it.ncm,
                "unidade": it.unidade,
                "quantidade": it.quantidade,
                "valor_unitario": it.valor_unitario,
                "valor_total": vt,
            }
        )
    return {
        "pedido": {
            "company_id": dados.company_id,
            "vendedor_cnpj": vend["cnpj"],
            "vendedor_nome": vend["nome"],
            "vendedor_uf": vend.get("uf"),
            "competencia": comp,
            "data_pedido": dados.data_pedido or hoje,
            "faturamento": fat["valor"] if fat["tem_loja"] else None,
            "modo": dados.modo,
            "percentual": dados.percentual if dados.modo == "percentual" else None,
            "cfop": cfop,
            "comprador": {
                "razao_social": compr["razao_social"],
                "apelido": compr["apelido"],
                "cnpj": compr["cnpj"],
                "inscricao_estadual": ie,
                "endereco": endereco,
                "endereco_origem": origem,
            },
            "total": total,
            "observacoes": dados.observacoes,
        },
        "itens": itens,
    }


async def itens_de(session: AsyncSession, ids: list[UUID]) -> dict[UUID, list[CompraPedidoItem]]:
    if not ids:
        return {}
    out: dict[UUID, list[CompraPedidoItem]] = {i: [] for i in ids}
    for it in (
        await session.execute(
            select(CompraPedidoItem)
            .where(CompraPedidoItem.pedido_id.in_(ids))
            .order_by(CompraPedidoItem.pedido_id, CompraPedidoItem.ordem)
        )
    ).scalars():
        out[it.pedido_id].append(it)
    return out


def nome_da_planilha(pedidos: list[CompraPedido]) -> str:
    vend = (
        re.sub(r"[^A-Za-z0-9]+", "-", pedidos[0].vendedor_nome).strip("-").lower()[:40]
        or "vendedora"
    )
    if len(pedidos) == 1:
        return f"compra_{pedidos[0].numero}_{vend}.csv"
    return f"compras_{len(pedidos)}_{vend}.csv"


def planilha(pedidos: list[CompraPedido], itens: dict[UUID, list[CompraPedidoItem]]) -> bytes:
    """O CSV de pedidos do Bling (41 colunas) — um pedido por compra."""
    blocos = []
    for p in pedidos:
        c = p.comprador or {}
        end = c.get("endereco") or {}
        info = PedidoInfo(
            numero=str(p.numero),
            data=p.data_pedido,
            nome_destinatario=c.get("razao_social"),
            cep_destino=end.get("cep"),
            endereco_destino=end.get("logradouro"),
            numero_destino=end.get("numero"),
            complemento_destino=end.get("complemento"),
            bairro_destino=end.get("bairro"),
            cidade_destino=end.get("municipio"),
            uf_destino=end.get("uf"),
            documento=fmt_cnpj(c.get("cnpj") or ""),
            tipo_pessoa="J",
            observacao=p.observacoes,
        )
        linhas = [
            NfLinha(
                sku=i.sku,
                nome=i.nome,
                ncm=i.ncm,
                quantidade=i.quantidade,
                valor_unitario=i.valor_unitario,
                valor_total=i.valor_total,
                unidade=i.unidade,
            )
            for i in itens.get(p.id, [])
        ]
        blocos.append((info, linhas))
    return montar_csv(blocos)
