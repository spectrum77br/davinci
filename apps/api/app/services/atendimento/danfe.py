"""A NF do pedido da conversa, em PDF, para anexar na resposta (28/10/2026).

Pedido do Eduardo, no 1º dia da equipe respondendo pelo DaVinci: "ele vai ter
que poder anexar a NF que é normalmente um PDF pelo DaVinci… hoje não
conseguimos anexar e foi o primeiro empecilho encontrado".

QUAL NF: a NOTA CHEIA (a "NF produto" do Pós Vendas, a que vai na caixa) —
nunca a de embalagem. Nas contas de faturador 1%/2% o envio tem as duas; a
de embalagem é simbólica e não é a do cliente. A régua é a do
`pos_vendas._eh_embalagem` pelo VALOR: a nota cheia passa de
`FRACAO_NOTA_CHEIA` do total do pedido (100%, ou 70% na poofy). Na faixa do
meio (30%–60%) ou sem valor, o DaVinci NÃO escolhe: a pessoa baixa no Bling e
anexa pelo botão. Só nota AUTORIZADA (`situacao = '100'`), de SAÍDA e NORMAL
(nunca a devolução que o coletor também liga ao pedido pelo CPF); pedido
cancelado no Bling e pacote do ML com vários pedidos também ficam com a
pessoa (o `nf_nota` não sabe se a NF foi cancelada).

DE ONDE: o XML autorizado do `nf_nota` (o coletor sobe desde 19/08/2026; em
produção, 28/10: 93% dos pedidos de 7 a 21 dias atrás têm NF ali e 81% têm a
cheia). O PDF da caixa (`nf_etiqueta_arquivo.nf_caixa_pdf`) quase nunca
existe (1 pedido em 14 dias) e as contas do Bling da nota cheia estão sem
token — por isso a DANFE é DESENHADA aqui, do XML (`gerar_pdf`), com o
PyMuPDF que a API já usa. A DANFE é só a representação impressa: o documento
fiscal é o XML autorizado, e qualquer sistema emissor desenha a sua.

O desenho segue os blocos da DANFE retrato (canhoto, emitente, DANFE com
número/série/folha, código de barras CODE-128C da chave, chave, natureza da
operação, protocolo, destinatário, cálculo do imposto, transportador, produtos
e dados adicionais). Ambiente de homologação ou nota sem protocolo levam a
marca d'água "SEM VALOR FISCAL".

Nada do cliente vai para o log: só ids, o número da NF e contagens.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from xml.etree import ElementTree

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AtendimentoConversa, BlingOrder, NfNota
from app.services.bling_situacoes import SITUACAO_CANCELADO

logger = structlog.get_logger()

NS = {"n": "http://www.portalfiscal.inf.br/nfe"}

# A nota cheia: a partir desta fração do total do pedido (a mesma régua do
# `pos_vendas._FRACAO_PRODUTO_MIN`).
FRACAO_NOTA_CHEIA = Decimal("0.60")
SITUACAO_AUTORIZADA = "100"
# As notas do pedido lidas (com o XML, para conferir saída/finalidade).
MAX_NOTAS_DO_PEDIDO = 10


class NfIndisponivel(Exception):  # noqa: N818 — nome do domínio, como EnvioRecusado
    """Não há NF cheia para anexar. `code` estável; `detail` = frase da tela."""

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(code)


@dataclass(frozen=True)
class NfDoPedido:
    """A nota cheia do pedido da conversa (sem o PDF: ele é desenhado sob demanda)."""

    id: str
    numero: str
    serie: str | None
    pedido_bling: str
    emitida_em: datetime | None

    @property
    def nome_arquivo(self) -> str:
        return f"NF_{self.numero}.pdf"


async def _pedido_bling_da_conversa(
    session: AsyncSession, conversa: AtendimentoConversa
) -> str | None:
    """O nº do pedido no Bling, pelas MESMAS chaves do contexto (pedido, pack, order).

    Revisão de 28/10: o pack do ML (carrinho) com MAIS de um pedido no Bling
    tem uma NF por pedido — o atalho não escolhe uma delas ("a NF deste
    pedido" levaria só uma): `nf_varios_pedidos`.
    """
    from app.services.atendimento.contexto import _chaves_do_pedido
    from app.services.chamados import lookup_pedido

    if not (conversa.pedido_marketplace or "").strip():
        return None
    for chave in _chaves_do_pedido(conversa):
        info = await lookup_pedido(session, chave)
        numero = str((info or {}).get("pedido_bling") or "").strip()
        if not numero:
            continue
        if numero != chave:
            pedidos = await session.scalar(
                select(func.count(func.distinct(BlingOrder.numero))).where(
                    BlingOrder.numeroloja == chave
                )
            )
            if (pedidos or 0) > 1:
                raise NfIndisponivel(
                    "nf_varios_pedidos",
                    f"Este pacote tem {pedidos} pedidos no Bling (carrinho), cada um com a sua "
                    "NF. Baixe as NFs no Bling e anexe pelo botão Anexar.",
                )
        return numero
    return None


def _saida_normal(xml: bytes | None) -> bool:
    """NF de SAÍDA (`tpNF=1`) e NORMAL (`finNFe=1`) — não devolução, ajuste ou complementar.

    Revisão de 28/10: o coletor liga a nota ao pedido pelo CPF do
    destinatário, então a NF de devolução de venda (entrada, valor cheio, o
    cliente como destinatário) também cai no pedido — e era a mais nova.
    """
    try:
        inf = ElementTree.fromstring(xml or b"").find(".//n:infNFe", NS)  # noqa: S314 — nf_nota
    except ElementTree.ParseError:
        return False
    ide = inf.find("n:ide", NS) if inf is not None else None
    return _t(ide, "n:tpNF") == "1" and _t(ide, "n:finNFe") in ("", "1")


async def nf_da_conversa(session: AsyncSession, conversa: AtendimentoConversa) -> NfDoPedido:
    """A nota cheia autorizada do pedido da conversa; levanta `NfIndisponivel`."""
    pedido = await _pedido_bling_da_conversa(session, conversa)
    if not pedido:
        raise NfIndisponivel(
            "nf_sem_pedido",
            "A conversa não tem pedido ligado no Bling: não dá para achar a NF. "
            "Baixe a DANFE no Bling e anexe pelo botão Anexar.",
        )
    total, situacoes = (
        await session.execute(
            select(
                func.max(BlingOrder.total), func.array_agg(func.distinct(BlingOrder.situacao))
            ).where(BlingOrder.numero == pedido)
        )
    ).one()
    if str(SITUACAO_CANCELADO) in {str(x or "").strip() for x in situacoes or []}:
        # O `nf_nota` não guarda o cancelamento da NF: com o pedido cancelado, a
        # nota pode estar cancelada na SEFAZ — quem confere é a pessoa, no Bling.
        raise NfIndisponivel(
            "nf_pedido_cancelado",
            f"O pedido {pedido} está cancelado no Bling: confira no Bling se a NF não foi "
            "cancelada antes de mandar (anexe pelo botão Anexar).",
        )
    notas = (
        await session.execute(
            select(
                NfNota.id,
                NfNota.numero,
                NfNota.serie,
                NfNota.valor,
                NfNota.data_emissao,
                NfNota.xml,
            )
            .where(NfNota.pedido_bling == pedido, NfNota.situacao == SITUACAO_AUTORIZADA)
            .order_by(NfNota.data_emissao.desc().nulls_last())
            .limit(MAX_NOTAS_DO_PEDIDO)
        )
    ).all()
    if not notas:
        raise NfIndisponivel(
            "nf_nao_chegou",
            f"O pedido {pedido} ainda não tem NF autorizada no DaVinci (o XML pode não ter "
            "chegado). Baixe a DANFE no Bling e anexe pelo botão Anexar.",
        )
    cheias = [n for n in notas if _eh_cheia(n.valor, total) and _saida_normal(n.xml)]
    if not cheias:
        raise NfIndisponivel(
            "nf_so_embalagem",
            f"Do pedido {pedido} o DaVinci só tem NF que não é a de venda do produto (a de "
            "embalagem, uma devolução, ou sem valor para conferir). Baixe a NF do produto no "
            "Bling e anexe pelo botão Anexar.",
        )
    n = cheias[0]
    return NfDoPedido(
        id=str(n.id),
        numero=str(n.numero),
        serie=n.serie,
        pedido_bling=pedido,
        emitida_em=n.data_emissao,
    )


def _eh_cheia(valor: object, total: object) -> bool:
    try:
        v, t = Decimal(str(valor)), Decimal(str(total))
    except (InvalidOperation, TypeError, ValueError):
        return False
    return t > 0 and v >= 0 and v / t >= FRACAO_NOTA_CHEIA


async def pdf_da_nf(session: AsyncSession, nf: NfDoPedido) -> bytes:
    """A DANFE da nota (desenhada do XML autorizado)."""
    xml = await session.scalar(select(NfNota.xml).where(NfNota.id == nf.id))
    if not xml:
        raise NfIndisponivel("nf_nao_chegou", "O XML desta NF não está no DaVinci.")
    try:
        # PyMuPDF é síncrono (centenas de ms com muitos itens): fora do laço.
        return await asyncio.to_thread(gerar_pdf, bytes(xml))
    except DanfeInvalida as e:
        logger.warning("atendimento_danfe_invalida", nf_id=nf.id, code=e.code)
        raise NfIndisponivel(
            "nf_xml_invalido",
            "Não consegui montar a DANFE desta NF. Baixe no Bling e anexe pelo botão Anexar.",
        ) from e


# ── Leitura do XML (PURA) ─────────────────────────────────────────────────


class DanfeInvalida(ValueError):  # noqa: N818
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass
class _Item:
    codigo: str = ""
    descricao: str = ""
    ncm: str = ""
    cst: str = ""
    cfop: str = ""
    un: str = ""
    qtd: str = ""
    v_unit: str = ""
    v_total: str = ""
    desconto: str = ""
    bc_icms: str = ""
    v_icms: str = ""
    v_ipi: str = ""
    aliq_icms: str = ""
    aliq_ipi: str = ""


@dataclass
class _Nota:
    chave: str
    numero: str
    serie: str
    tp_nf: str
    tp_amb: str
    nat_op: str
    emissao: str
    saida_data: str
    saida_hora: str
    protocolo: str
    emit: dict[str, str]
    dest: dict[str, str]
    tot: dict[str, str]
    transp: dict[str, str]
    issqn: dict[str, str]
    info: str
    fisco: str
    faturas: list[tuple[str, str, str]] = field(default_factory=list)
    itens: list[_Item] = field(default_factory=list)


def _t(node, path: str) -> str:
    if node is None:
        return ""
    achado = node.find(path, NS)
    if achado is None or achado.text is None:
        return ""
    return " ".join(achado.text.split())


def _dig(v: str) -> str:
    return "".join(c for c in v if c.isdigit())


def _data(iso: str) -> tuple[str, str]:
    """('dd/mm/aaaa', 'hh:mm:ss') do jeito que veio no XML (hora local da nota)."""
    if not iso:
        return "", ""
    try:
        d = datetime.fromisoformat(iso)
    except ValueError:
        return iso[:10], ""
    return d.strftime("%d/%m/%Y"), d.strftime("%H:%M:%S")


def _num(v: str, casas: int = 2) -> str:
    """'1234.5' → '1.234,50'; vazio fica vazio."""
    if not v:
        return ""
    try:
        d = Decimal(v)
    except InvalidOperation:
        return v
    texto = f"{d:,.{casas}f}"
    return texto.replace(",", "_").replace(".", ",").replace("_", ".")


def doc_formatado(v: str) -> str:
    d = _dig(v)
    if len(d) == 14:
        return f"{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:]}"
    if len(d) == 11:
        return f"{d[:3]}.{d[3:6]}.{d[6:9]}-{d[9:]}"
    return v


def _cep(v: str) -> str:
    """'28994868' → '28.994-868' (como o Bling escreve)."""
    d = _dig(v)
    return f"{d[:2]}.{d[2:5]}-{d[5:]}" if len(d) == 8 else v


def _fone(v: str) -> str:
    d = _dig(v)
    if len(d) in (10, 11):
        return f"({d[:2]}) {d[2:-4]}-{d[-4:]}"
    return v


def _cfop(v: str) -> str:
    """'6102' → '6.102'."""
    d = _dig(v)
    return f"{d[0]}.{d[1:]}" if len(d) == 4 else v


def chave_formatada(chave: str) -> str:
    return " ".join(chave[i : i + 4] for i in range(0, len(chave), 4))


def numero_formatado(numero: str) -> str:
    """'2009' → '002009' (o 'Nº 002009' do Bling; número maior fica inteiro)."""
    return _dig(numero).rjust(6, "0")


def _protocolo(prot) -> str:
    """'135264584865135 28/10/2026 07:59:42-03:00' — o nProt e o recebimento, como o Bling."""
    n_prot = _t(prot, "n:nProt")
    if not n_prot or _t(prot, "n:cStat") not in ("", SITUACAO_AUTORIZADA):
        return ""  # sem protocolo, ou de rejeição/denegação: não autoriza o uso
    iso = _t(prot, "n:dhRecbto")
    data, hora = _data(iso)
    fuso = iso[-6:] if len(iso) >= 6 and iso[-6] in "+-" and iso[-3] == ":" else ""
    return " ".join(x for x in (n_prot, data, f"{hora}{fuso}" if hora else "") if x)


# Os textos do "Frete por conta" (tabela modFrete da NF-e), como o Bling escreve.
_MOD_FRETE = {
    "0": "0 - Contratação do Frete por conta do Remetente (CIF)",
    "1": "1 - Contratação do Frete por conta do Destinatário (FOB)",
    "2": "2 - Contratação do Frete por conta de Terceiros",
    "3": "3 - Transporte Próprio por conta do Remetente",
    "4": "4 - Transporte Próprio por conta do Destinatário",
    "9": "9 - Sem Ocorrência de Transporte",
}


def _endereco(no, prefixo: str) -> dict[str, str]:
    end = no.find(f"n:ender{prefixo}", NS) if no is not None else None
    lgr = ", ".join(x for x in (_t(end, "n:xLgr"), _t(end, "n:nro")) if x)
    cpl = _t(end, "n:xCpl")
    return {
        "logradouro": f"{lgr} - {cpl}" if cpl else lgr,
        "rua": lgr,
        "bairro": _t(end, "n:xBairro"),
        "municipio": _t(end, "n:xMun"),
        "uf": _t(end, "n:UF"),
        "cep": _cep(_t(end, "n:CEP")),
        "fone": _fone(_t(end, "n:fone")),
    }


def ler_nota(xml: bytes) -> _Nota:
    """O XML da NF-e (`nfeProc` ou `NFe` solta) → os campos da DANFE. Levanta `DanfeInvalida`."""
    try:
        raiz = ElementTree.fromstring(xml)  # noqa: S314 — o XML do nosso coletor (nf_nota)
    except ElementTree.ParseError as e:
        raise DanfeInvalida("xml_invalido") from e
    inf = raiz.find(".//n:infNFe", NS)
    if inf is None:
        raise DanfeInvalida("xml_sem_nfe")
    prot = raiz.find(".//n:protNFe/n:infProt", NS)
    chave = _dig(inf.get("Id") or "") or _dig(_t(prot, "n:chNFe"))
    if len(chave) != 44:
        raise DanfeInvalida("xml_sem_chave")
    ide = inf.find("n:ide", NS)
    emit = inf.find("n:emit", NS)
    dest = inf.find("n:dest", NS)
    icms_tot = inf.find("n:total/n:ICMSTot", NS)
    iss_tot = inf.find("n:total/n:ISSQNtot", NS)
    transp = inf.find("n:transp", NS)
    vol = transp.find("n:vol", NS) if transp is not None else None
    emissao, _ = _data(_t(ide, "n:dhEmi"))
    saida_data, saida_hora = _data(_t(ide, "n:dhSaiEnt"))
    nota = _Nota(
        chave=chave,
        numero=_t(ide, "n:nNF"),
        serie=_t(ide, "n:serie"),
        tp_nf=_t(ide, "n:tpNF") or "1",
        tp_amb=_t(ide, "n:tpAmb") or "1",
        nat_op=_t(ide, "n:natOp"),
        emissao=emissao,
        saida_data=saida_data,
        saida_hora=saida_hora,
        protocolo=_protocolo(prot),
        emit={
            "nome": _t(emit, "n:xNome"),
            "doc": doc_formatado(_t(emit, "n:CNPJ") or _t(emit, "n:CPF")),
            "ie": _t(emit, "n:IE"),
            "iest": _t(emit, "n:IEST"),
            "im": _t(emit, "n:IM"),
            **_endereco(emit, "Emit"),
        },
        dest={
            "nome": _t(dest, "n:xNome"),
            "doc": doc_formatado(
                _t(dest, "n:CNPJ") or _t(dest, "n:CPF") or _t(dest, "n:idEstrangeiro")
            ),
            "ie": _t(dest, "n:IE"),
            **_endereco(dest, "Dest"),
        },
        tot={
            k: _num(_t(icms_tot, f"n:{k}")) or "0,00"
            for k in (
                "vBC",
                "vICMS",
                "vBCST",
                "vST",
                "vFCPST",
                "vProd",
                "vFrete",
                "vSeg",
                "vDesc",
                "vOutro",
                "vIPI",
                "vNF",
            )
        },
        transp={
            "nome": _t(transp, "n:transporta/n:xNome"),
            "doc": doc_formatado(
                _t(transp, "n:transporta/n:CNPJ") or _t(transp, "n:transporta/n:CPF")
            ),
            "ie": _t(transp, "n:transporta/n:IE"),
            "endereco": _t(transp, "n:transporta/n:xEnder"),
            "municipio": _t(transp, "n:transporta/n:xMun"),
            "uf": _t(transp, "n:transporta/n:UF"),
            "antt": _t(transp, "n:veicTransp/n:RNTC"),
            "placa": _t(transp, "n:veicTransp/n:placa"),
            "placa_uf": _t(transp, "n:veicTransp/n:UF"),
            "frete": _MOD_FRETE.get(_t(transp, "n:modFrete"), _t(transp, "n:modFrete")),
            "qvol": _t(vol, "n:qVol") or "0",
            "esp": _t(vol, "n:esp"),
            "marca": _t(vol, "n:marca"),
            "nvol": _t(vol, "n:nVol"),
            "peso_b": _num(_t(vol, "n:pesoB"), 3) or "0,000",
            "peso_l": _num(_t(vol, "n:pesoL"), 3) or "0,000",
        },
        issqn={
            "vServ": _num(_t(iss_tot, "n:vServ")) or "0,00",
            "vBC": _num(_t(iss_tot, "n:vBC")) or "0,00",
            "vISS": _num(_t(iss_tot, "n:vISS")) or "0,00",
        },
        # O Bling quebra a linha depois do "Fonte IBPT." dos tributos (o resto
        # das observações vem colado no XML).
        info=re.sub(r"(Fonte:?\s*IBPT\.?)\s*(?=\S)", r"\1\n", _t(inf, "n:infAdic/n:infCpl")),
        fisco=_t(inf, "n:infAdic/n:infAdFisco"),
    )
    for dup in inf.findall("n:cobr/n:dup", NS):
        nota.faturas.append(
            (_t(dup, "n:nDup"), _data(_t(dup, "n:dVenc"))[0], _num(_t(dup, "n:vDup")))
        )
    for det in inf.findall("n:det", NS):
        prod = det.find("n:prod", NS)
        icms = det.find("n:imposto/n:ICMS", NS)
        grupo = icms[0] if icms is not None and len(icms) else None
        cst = _t(grupo, "n:CSOSN") or _t(grupo, "n:CST")
        ipi = det.find("n:imposto/n:IPI/n:IPITrib", NS)
        nota.itens.append(
            _Item(
                codigo=_t(prod, "n:cProd"),
                descricao=_t(prod, "n:xProd"),
                ncm=_t(prod, "n:NCM"),
                cst=f"{_t(grupo, 'n:orig')}{cst}",
                cfop=_cfop(_t(prod, "n:CFOP")),
                un=_t(prod, "n:uCom"),
                qtd=_num(_t(prod, "n:qCom")),
                v_unit=_num(_t(prod, "n:vUnCom")),
                v_total=_num(_t(prod, "n:vProd")),
                desconto=_num(_t(prod, "n:vDesc")) or "0,00",
                bc_icms=_num(_t(grupo, "n:vBC")) or "0,00",
                v_icms=_num(_t(grupo, "n:vICMS")) or "0,00",
                v_ipi=_num(_t(ipi, "n:vIPI")) or "0,00",
                aliq_icms=_num(_t(grupo, "n:pICMS"), 4) or "0,0000",
                aliq_ipi=_num(_t(ipi, "n:pIPI")) or "0,00",
            )
        )
    return nota


# ── CODE-128 (subconjunto C: pares de dígitos) ────────────────────────────
# As larguras (barra, espaço, barra, espaço, barra, espaço) dos 107 símbolos.
_CODE128 = (
    "212222 222122 222221 121223 121322 131222 122213 122312 132212 221213 "
    "221312 231212 112232 122132 122231 113222 123122 123221 223211 221132 "
    "221231 213212 223112 312131 311222 321122 321221 312212 322112 322211 "
    "212123 212321 232121 111323 131123 131321 112313 132113 132311 211313 "
    "231113 231311 112133 112331 132131 113123 113321 133121 313121 211331 "
    "231131 213113 213311 213131 311123 311321 331121 312113 312311 332111 "
    "314111 221411 431111 111224 111422 121124 121421 141122 141221 112214 "
    "112412 122114 122411 142112 142211 241211 221114 413111 241112 134111 "
    "111242 121142 121241 114212 124112 124211 411212 421112 421211 212141 "
    "214121 412121 111143 111341 131141 114113 114311 411113 411311 113141 "
    "114131 311141 411131 211412 211214 211232 2331112"
).split()
_START_C, _STOP = 105, 106


def code128c(digitos: str) -> list[int]:
    """As larguras (em módulos, começando por barra) do CODE-128C de `digitos` (par)."""
    if not digitos or len(digitos) % 2 or not digitos.isdigit():
        raise ValueError("code128c: só número com quantidade par de dígitos")
    valores = [int(digitos[i : i + 2]) for i in range(0, len(digitos), 2)]
    soma = _START_C + sum(i * v for i, v in enumerate(valores, start=1))
    simbolos = [_START_C, *valores, soma % 103, _STOP]
    return [int(c) for s in simbolos for c in _CODE128[s]]


# ── Desenho (PyMuPDF): o padrão visual da DANFE do Bling ──────────────────
# Eduardo, 28/10/2026, com a DANFE do Bling ao lado da nossa: "as infos bateram
# mas dá para seguir o mesmo padrão visual". As medidas abaixo são as do PDF
# que o Bling gera (folha CARTA; rótulo 6 pt, valor 7 pt, título de bloco 8 pt
# negrito, cabeçalho de tabela 7 pt negrito; as mesmas caixas e colunas, em
# pontos). Sem o logotipo da loja (o DaVinci não tem o do emitente).

_PAG = (612.0, 792.0)
_X0, _X1 = 29.2, 582.4
_FIM = 763.0  # o fundo útil da folha
_FONTE, _NEGRITO = "helv", "hebo"
_ASC = 0.86  # do tamanho da fonte: do topo do texto à linha de base
_TRACO = 0.75
_PASSO = 7.5  # a entrelinha do texto de 7 pt
# O texto de um campo é cortado antes de quebrar (o `infCpl` vai até 5.000
# caracteres no XML): a caixa mostra no máximo umas 8 linhas.
MAX_TEXTO_CAMPO = 1500


def _cabe(
    texto: str, tam: float, largura: float, fonte: str = _FONTE, *, ate: int | None = None
) -> list[str]:
    """Quebra `texto` em linhas que cabem em `largura` (palavra longa é cortada).

    Com `ate`, para ao passar de `ate` linhas (quem chama só quer saber se
    cabe). Revisão de 28/10: o corte de palavra é por busca binária — o corte
    de 1 em 1 caractere levava minutos num `infCpl` sem espaço.
    """
    import pymupdf

    def larg(t: str) -> float:
        return pymupdf.get_text_length(t, fontname=fonte, fontsize=tam)

    linhas: list[str] = []
    for paragrafo in (texto or "")[:MAX_TEXTO_CAMPO].split("\n"):
        atual = ""
        for palavra in paragrafo.split():
            if ate is not None and len(linhas) > ate:
                return linhas
            tentativa = f"{atual} {palavra}".strip()
            if larg(tentativa) <= largura:
                atual = tentativa
                continue
            if atual:
                linhas.append(atual)
            while larg(palavra) > largura:
                if ate is not None and len(linhas) > ate:
                    return linhas
                baixo, alto = 1, len(palavra)  # o maior prefixo que cabe (mínimo 1)
                while baixo < alto:
                    meio = (baixo + alto + 1) // 2
                    if larg(palavra[:meio]) <= largura:
                        baixo = meio
                    else:
                        alto = meio - 1
                linhas.append(palavra[:baixo])
                palavra = palavra[baixo:]
            atual = palavra
        linhas.append(atual)
    return linhas


class _Folha:
    """Uma página da DANFE com os ajudantes de caixa e texto (coordenadas do Bling)."""

    def __init__(self, doc, nota: _Nota) -> None:
        self.p = doc.new_page(width=_PAG[0], height=_PAG[1])
        self.nota = nota

    def texto(
        self,
        x: float,
        topo: float,
        w: float,
        texto: str,
        *,
        tam: float = 7.0,
        negrito: bool = False,
        alinhar: int = 0,
        linhas_max: int = 1,
        passo: float | None = None,
    ) -> float:
        """Até `linhas_max` linhas em [x, x+w], a 1ª com o TOPO em `topo`; encolhe a
        fonte até caber (mínimo 4,5). `alinhar`: 0 esquerda, 1 centro, 2 direita."""
        import pymupdf

        fonte = _NEGRITO if negrito else _FONTE
        while True:
            linhas = _cabe(texto, tam, w, fonte, ate=linhas_max)
            if len(linhas) <= linhas_max or tam <= 4.5:
                break
            tam -= 0.5
        linhas = linhas[:linhas_max]
        passo = passo or tam * 1.07
        for i, linha in enumerate(linhas):
            larg = pymupdf.get_text_length(linha, fontname=fonte, fontsize=tam)
            dx = 0.0 if alinhar == 0 else (w - larg) / (2 if alinhar == 1 else 1)
            self.p.insert_text(
                (x + dx, topo + i * passo + tam * _ASC), linha, fontname=fonte, fontsize=tam
            )
        return len(linhas) * passo

    def borda(self, x0: float, y0: float, x1: float, y1: float) -> None:
        import pymupdf

        self.p.draw_rect(pymupdf.Rect(x0, y0, x1, y1), width=_TRACO)

    def campo(
        self,
        x0: float,
        y0: float,
        x1: float,
        y1: float,
        rotulo: str,
        valor: str = "",
        *,
        tam: float = 7.0,
        alinhar: int = 0,
        linhas_max: int = 1,
    ) -> None:
        """Caixa com rótulo (6 pt) em cima e valor (7 pt) embaixo; sem valor, o
        rótulo fica no meio da caixa — como no Bling."""
        self.borda(x0, y0, x1, y1)
        w = x1 - x0 - 4.6
        if valor:
            self.texto(x0 + 2.3, y0 + 0.6, w, rotulo, tam=6)
            self.texto(
                x0 + 2.3,
                # O valor miúdo (o "Frete por conta", 5,2 pt em 2 linhas) sobe.
                y0 + (8.0 if tam < 6 else 9.4),
                w,
                valor,
                tam=tam,
                alinhar=alinhar,
                linhas_max=linhas_max,
                passo=tam * 1.02,
            )
        else:
            self.texto(x0 + 2.3, y0 + (y1 - y0) / 2 - 4.8, w, rotulo, tam=6)

    def fileira(self, y0: float, y1: float, campos: list[tuple[float, str, str]], **kw) -> float:
        """Caixas lado a lado de `_X0`; cada uma termina no x dado."""
        x = _X0
        for fim, rotulo, valor in campos:
            self.campo(x, y0, fim, y1, rotulo, valor, **kw)
            x = fim
        return y1

    def titulo(self, topo: float, texto: str) -> None:
        self.texto(_X0, topo, _X1 - _X0, texto, tam=8, negrito=True)

    def barras(self, x: float, y: float, w: float, h: float, digitos: str) -> None:
        import pymupdf

        larguras = code128c(digitos)
        modulo = w / sum(larguras)
        cx = x
        for i, larg in enumerate(larguras):
            if i % 2 == 0:
                self.p.draw_rect(
                    pymupdf.Rect(cx, y, cx + larg * modulo, y + h), color=None, fill=(0, 0, 0)
                )
            cx += larg * modulo

    def marca_dagua(self, texto: str) -> None:
        import pymupdf

        self.p.insert_textbox(
            pymupdf.Rect(_X0, 340, _X1, 480),
            texto,
            fontname=_NEGRITO,
            fontsize=26,
            align=1,
            color=(0.8, 0.1, 0.1),
            fill_opacity=0.35,
        )


def _canhoto(f: _Folha) -> None:
    import pymupdf

    n = f.nota
    f.borda(_X0, 30.0, 507.4, 48.8)
    f.texto(
        _X0 + 2.3,
        35.8,
        474,
        f"RECEBEMOS DE {n.emit['nome'].upper()} OS PRODUTOS CONSTANTES DA NOTA FISCAL "
        "INDICADA AO LADO",
        tam=6,
    )
    f.campo(_X0, 48.8, 198.4, 74.6, "Data de recebimento")
    f.campo(198.4, 48.8, 507.4, 74.6, "Identificação e assinatura do recebedor")
    f.borda(510.8, 29.2, _X1, 75.8)
    f.texto(510.8, 39.4, _X1 - 510.8, "NF-e", tam=7, negrito=True, alinhar=1)
    f.texto(
        510.8,
        47.4,
        _X1 - 510.8,
        f"Nº {numero_formatado(n.numero)}",
        tam=9.8,
        negrito=True,
        alinhar=1,
    )
    f.texto(510.8, 58.2, _X1 - 510.8, f"Série {n.serie}", tam=7, negrito=True, alinhar=1)
    f.p.draw_line(
        pymupdf.Point(_X0, 83.6), pymupdf.Point(_X1, 83.6), width=_TRACO, dashes="[2.3 1.5] 0"
    )


def _cabecalho(f: _Folha, folha: int, folhas: int) -> None:
    """Emitente, o quadro DANFE (sem borda, como no Bling), código de barras e chave."""
    import pymupdf

    n, e = f.nota, f.nota.emit
    f.borda(_X0, 91.5, 259.9, 184.9)
    linhas = [
        ", ".join(x for x in (e["rua"], e["bairro"]) if x),
        " - ".join(x for x in (e["cep"], e["municipio"], e["uf"]) if x),
        f"Fone - {e['fone']}" if e["fone"] else "",
    ]
    corpo = [ln for ln in linhas if ln]
    w = 259.9 - _X0 - 14
    altura = (
        len(_cabe(e["nome"], 7, w, _NEGRITO)[:2])
        + sum(min(len(_cabe(ln, 7, w)), 2) for ln in corpo)
    ) * _PASSO
    y = 138.2 - altura / 2
    y += f.texto(_X0 + 7, y, w, e["nome"], tam=7, negrito=True, linhas_max=2, passo=_PASSO)
    for ln in corpo:
        y += f.texto(_X0 + 7, y, w, ln, tam=7, linhas_max=2, passo=_PASSO)

    cx0, cx1 = 259.9, 342.8
    largura = cx1 - cx0
    f.texto(cx0, 100.7, largura, "DANFE", tam=9.8, negrito=True, alinhar=1)
    for i, ln in enumerate(("Documento Auxiliar", "da Nota Fiscal", "Eletrônica")):
        f.texto(cx0, 111.4 + i * _PASSO, largura, ln, tam=7, alinhar=1)
    f.texto(275.2, 134.7, 40, "0-Entrada", tam=7)
    f.texto(275.2, 142.2, 40, "1-Saída", tam=7)
    f.p.draw_rect(pymupdf.Rect(315.0, 134.2, 334.9, 149.6), width=_TRACO)
    f.texto(315.0, 138.4, 19.9, n.tp_nf, tam=7, alinhar=1)
    f.texto(267.8, 150.2, 74, f"Nº {numero_formatado(n.numero)}", tam=9.8, negrito=True)
    f.texto(267.8, 160.9, 74, f"SERIE: {n.serie}", tam=7)
    f.texto(267.8, 168.4, 74, f"Página: {folha} de {folhas}", tam=7)

    f.borda(cx1, 92.2, _X1, 135.0)
    f.texto(cx1 + 2.2, 92.8, 100, "Controle do Fisco", tam=6)
    f.barras(359.2, 100.5, 207.0, 30.0, n.chave)
    f.borda(cx1, 135.0, _X1, 153.8)
    f.texto(cx1 + 2.2, 135.6, 100, "Chave de acesso", tam=6)
    f.texto(cx1, 144.4, _X1 - cx1, chave_formatada(n.chave), tam=7, alinhar=1)
    f.borda(cx1, 153.8, _X1, 184.9)
    for i, ln in enumerate(
        (
            "Consulta de autenticidade no portal nacional da NF-e",
            "www.nfe.fazenda.gov.br/portal",
            "ou no site da Sefaz autorizadora",
        )
    ):
        f.texto(347.2, 157.9 + i * _PASSO, 230, ln, tam=7)


def _identificacao(f: _Folha) -> None:
    n, e = f.nota, f.nota.emit
    f.fileira(
        193.5,
        212.2,
        [
            (207.4, "Natureza da operação", n.nat_op),
            (_X1, "Protocolo de autorização de uso", n.protocolo),
        ],
    )
    f.fileira(
        212.2,
        231.0,
        [
            (189.4, "Inscrição Estadual", e["ie"]),
            (382.1, "Inscr.est. do subst.trib.", e["iest"]),
            (_X1, "CNPJ", e["doc"]),
        ],
    )


def _destinatario(f: _Folha) -> None:
    n, d = f.nota, f.nota.dest
    f.titulo(235.0, "Destinatário/Remetente")
    cols = (300.4, 399.4, 498.4)
    linhas = (
        (
            ("Nome / Razão Social", d["nome"]),
            ("CNPJ/CPF", d["doc"]),
            ("Inscrição Estadual", d["ie"]),
            ("Data emissão", n.emissao),
        ),
        (
            ("Endereço", d["logradouro"]),
            ("Bairro", d["bairro"]),
            ("CEP", d["cep"]),
            ("Data saída", n.saida_data),
        ),
        (
            ("Município", d["municipio"]),
            ("UF", d["uf"]),
            ("Fone/Fax", d["fone"]),
            ("Hora saída", n.saida_hora),
        ),
    )
    for i, (a, b, c, lado) in enumerate(linhas):
        y0, y1 = 243.8 + i * 18.7, 243.8 + (i + 1) * 18.7
        f.fileira(y0, y1, [(cols[0], *a), (cols[1], *b), (cols[2], *c)])
        f.campo(506.2, y0, _X1, y1, *lado)


def extra_das_faturas(nota: _Nota) -> float:
    """Quanto as duplicatas (3 por fileira, até 9) empurram o resto para baixo."""
    if not nota.faturas:
        return 0.0
    fileiras = (min(len(nota.faturas), 9) + 2) // 3
    return fileiras * 9.0 - 3.7


def _faturas(f: _Folha) -> None:
    import pymupdf

    f.titulo(304.0, "Faturas")
    cols = (_X0, 88.9, 174.4, 214.1, 272.6, 358.1, 397.9, 457.1, 542.6, _X1)
    rotulos = ("Número", "Vencimento", "Valor") * 3
    for i, rotulo in enumerate(rotulos):
        f.p.draw_rect(pymupdf.Rect(cols[i], 312.8, cols[i + 1], 322.5), width=_TRACO)
        f.texto(cols[i] + 1.6, 313.9, cols[i + 1] - cols[i] - 3, rotulo, tam=7, negrito=True)
    linhas = list(range(0, min(len(f.nota.faturas), 9), 3)) or [0]
    y = 322.5
    for ini in linhas:
        grupo = f.nota.faturas[ini : ini + 3]
        alt = 9.0 if grupo else 3.7
        for i in range(9):
            f.p.draw_rect(pymupdf.Rect(cols[i], y, cols[i + 1], y + alt), width=_TRACO)
        for j, fat in enumerate(grupo):
            for k, valor in enumerate(fat):
                c = j * 3 + k
                f.texto(cols[c] + 1.6, y + 1.2, cols[c + 1] - cols[c] - 3, valor, tam=6.5)
        y += alt


def _impostos(f: _Folha, dy: float) -> None:
    t = f.nota.tot
    f.titulo(330.3 + dy, "Cálculo do imposto")
    f.fileira(
        339.0 + dy,
        357.8 + dy,
        [
            (133.1, "Base de cálculo do ICMS", t["vBC"]),
            (194.6, "Valor do ICMS", t["vICMS"]),
            (325.9, "Base de cálculo do ICMS Subst.", t["vBCST"]),
            (414.4, "Valor do ICMS Subst.", t["vST"]),
            (484.9, "Valor do FCP ST", t["vFCPST"]),
            (_X1, "Valor total dos produtos", t["vProd"]),
        ],
    )
    f.fileira(
        357.8 + dy,
        376.5 + dy,
        [
            (106.9, "Valor do frete", t["vFrete"]),
            (197.6, "Valor do seguro", t["vSeg"]),
            (254.6, "Desconto", t["vDesc"]),
            (411.4, "Outras despesas acessórias", t["vOutro"]),
            (479.6, "Valor do IPI", t["vIPI"]),
            (_X1, "Valor total da nota", t["vNF"]),
        ],
    )


def _transporte(f: _Folha, dy: float) -> None:
    t = f.nota.transp
    f.titulo(380.5 + dy, "Transportador/Volumes transportados")
    y = f.fileira(389.2 + dy, 408.8 + dy, [(203.6, "Nome", t["nome"])])
    f.campo(
        203.6, 389.2 + dy, 301.9, 408.8 + dy, "Frete por conta", t["frete"], tam=5.2, linhas_max=2
    )
    x = 301.9
    for fim, rotulo, valor in (
        (392.6, "Código ANTT", t["antt"]),
        (483.4, "Placa do veículo", t["placa"]),
        (506.6, "UF", t["placa_uf"]),
        (_X1, "CNPJ/CPF", t["doc"]),
    ):
        f.campo(x, 389.2 + dy, fim, 408.8 + dy, rotulo, valor)
        x = fim
    # A 2ª fileira é baixa no Bling (só os rótulos); com transportadora, ganha o valor.
    tem = any(t[k] for k in ("endereco", "municipio", "uf", "ie"))
    y1 = y + (18.7 if tem else 11.2)
    for x0, fim, rotulo, valor in (
        (_X0, 161.6, "Endereço", t["endereco"]),
        (161.6, 293.6, "Município", t["municipio"]),
        (293.6, 346.2, "UF", t["uf"]),
        (346.2, _X1, "Inscrição Estadual", t["ie"]),
    ):
        if tem:
            f.campo(x0, y, fim, y1, rotulo, valor)
        else:
            f.borda(x0, y, fim, y1)
            f.texto(x0 + 2.3, y + 0.5, fim - x0 - 4.6, rotulo, tam=6)
    f.fileira(
        y1,
        y1 + 18.8,
        [
            (133.8, "Quantidade", t["qvol"]),
            (208.9, "Espécie", t["esp"]),
            (270.4, "Marca", t["marca"]),
            (373.8, "Numeração", t["nvol"]),
            (472.1, "Peso bruto", t["peso_b"]),
            (_X1, "Peso líquido", t["peso_l"]),
        ],
    )


# Colunas dos itens: (rótulo, atributo, x final, alinhamento do valor).
_COLUNAS = (
    ("Código", "codigo", 55.8, 0),
    ("Descrição do produto/serviço", "descricao", 244.1, 0),
    ("NCM/SH", "ncm", 275.6, 2),
    ("CSOSN", "cst", 303.4, 2),
    ("CFOP", "cfop", 325.1, 2),
    ("UN", "un", 337.1, 0),
    ("Qtde", "qtd", 355.1, 2),
    ("Preço\nun", "v_unit", 385.8, 2),
    ("Preço\ntotal", "v_total", 422.6, 2),
    ("Desconto", "desconto", 456.4, 2),
    ("BC\nICMS", "bc_icms", 487.1, 2),
    ("Vlr.ICMS", "v_icms", 517.8, 2),
    ("Vlr.IPI", "v_ipi", 539.6, 2),
    ("%ICMS", "aliq_icms", 565.1, 2),
    ("%IPI", "aliq_ipi", _X1, 2),
)
_ALT_CAB_ITENS = 17.3
_MIN_CORPO_ITENS = 61.4
# ISSQN + dados adicionais, depois dos itens (títulos, caixas e respiros).
_BLOCO_FINAL = 4.1 + 8.7 + 17.2 + 4.1 + 8.7 + 60.8 + 2


def _larguras_colunas() -> list[tuple[str, str, float, float, int]]:
    x, saida = _X0, []
    for rotulo, attr, fim, alinhar in _COLUNAS:
        saida.append((rotulo, attr, x, fim, alinhar))
        x = fim
    return saida


def _alturas_dos_itens(itens: list[_Item]) -> list[float]:
    _, _, x0, x1, _ = _larguras_colunas()[1]
    return [max(1, len(_cabe(i.descricao, 7, x1 - x0 - 2))) * _PASSO + 1.5 for i in itens]


def _tabela(f: _Folha, topo: float, itens: list[_Item], alturas: list[float], fim: float) -> None:
    """Título, cabeçalho e itens; as colunas descem até `fim` (sem linha entre itens)."""
    f.titulo(topo, "Itens da nota fiscal")
    y = topo + 8.7
    colunas = _larguras_colunas()
    for rotulo, _, x0, x1, alinhar in colunas:
        f.borda(x0, y, x1, y + _ALT_CAB_ITENS)
        partes = rotulo.split("\n")
        cima = y + (1.2 if len(partes) > 1 else 4.9)
        for i, parte in enumerate(partes):
            f.texto(
                x0 + 1.6,
                cima + i * _PASSO,
                x1 - x0 - 2.6,
                parte,
                tam=7,
                negrito=True,
                alinhar=alinhar,
            )
    y += _ALT_CAB_ITENS
    corpo = y
    for item, alt in zip(itens, alturas, strict=True):
        for _, attr, x0, x1, alinhar in colunas:
            valor = getattr(item, attr)
            if attr == "descricao":
                for i, ln in enumerate(_cabe(valor, 7, x1 - x0 - 2)):
                    f.texto(x0 + 0.8, y + 0.4 + i * _PASSO, x1 - x0 - 1, ln, tam=7)
            else:
                meio = y + (alt - _PASSO) / 2
                f.texto(x0 + 0.8, meio, x1 - x0 - 1.6, valor, tam=7, alinhar=alinhar)
        y += alt
    for _, _, x0, x1, _ in colunas:
        f.borda(x0, corpo, x1, fim)


def _issqn_e_adicionais(f: _Folha, y: float) -> None:
    n, i = f.nota, f.nota.issqn
    f.titulo(y + 4.1, "Cálculo do ISSQN")
    y += 12.8
    f.fileira(
        y,
        y + 17.2,
        [
            (202.1, "Inscrição Municipal", n.emit["im"]),
            (323.6, "Valor total dos serviços", i["vServ"]),
            (463.1, "Base de cálculo do ISSQN", i["vBC"]),
            (_X1, "Valor do ISSQN", i["vISS"]),
        ],
    )
    y += 17.2
    f.titulo(y + 4.1, "Dados adicionais")
    y += 12.8
    f.borda(_X0, y, 306.4, y + 60.8)
    f.texto(_X0 + 2.3, y + 0.6, 270, "Observações", tam=6)
    f.texto(_X0 + 0.8, y + 7.9, 306.4 - _X0 - 3, n.info, tam=7, linhas_max=7, passo=_PASSO)
    f.borda(306.4, y, _X1, y + 60.8)
    f.texto(306.4 + 1.5, y + 0.6, 200, "Reservado ao fisco", tam=6)
    if n.fisco:
        f.texto(306.4 + 1.5, y + 7.9, _X1 - 306.4 - 3, n.fisco, tam=7, linhas_max=7, passo=_PASSO)


def gerar_pdf(xml: bytes) -> bytes:
    """O PDF da DANFE (o padrão visual do Bling) da NF-e do XML. Levanta `DanfeInvalida`.

    DETERMINÍSTICO (revisão de 28/10): o mesmo XML dá os mesmos bytes (sem
    data de impressão, sem /ID novo) — é o que deixa a trava de "anexo
    repetido em 2 minutos" (sha256) pegar a NF mandada duas vezes.
    """
    import pymupdf

    nota = ler_nota(xml)
    alturas = _alturas_dos_itens(nota.itens)
    tem_transp = any(nota.transp[k] for k in ("endereco", "municipio", "uf", "ie"))
    # Onde a tabela de itens começa na 1ª folha (depois do transporte) e nas outras.
    dy = extra_das_faturas(nota)
    topo_1 = 442.8 + dy + (7.5 if tem_transp else 0.0)
    topo_n = 193.5
    corpo = _ALT_CAB_ITENS + 8.7
    # Distribui os itens; a última folha leva o ISSQN e os dados adicionais.
    folhas: list[list[int]] = [[]]
    y = topo_1 + corpo
    for i, alt in enumerate(alturas):
        if y + alt > _FIM and folhas[-1]:
            folhas.append([])
            y = topo_n + corpo
        folhas[-1].append(i)
        y += alt
    fim_itens = max(y, (topo_1 if len(folhas) == 1 else topo_n) + corpo + _MIN_CORPO_ITENS)
    if fim_itens + _BLOCO_FINAL > _FIM:
        # O bloco final não cabe: vai para uma folha nova, com o último item
        # (a tabela da última folha nunca fica vazia).
        folhas.append([folhas[-1].pop()] if len(folhas[-1]) > 1 else [])
    total = len(folhas)

    doc = pymupdf.open()
    for nf, indices in enumerate(folhas, start=1):
        f = _Folha(doc, nota)
        if nf == 1:
            _canhoto(f)
        _cabecalho(f, nf, total)
        if nf == 1:
            _identificacao(f)
            _destinatario(f)
            _faturas(f)
            _impostos(f, dy)
            _transporte(f, dy)
        topo = topo_1 if nf == 1 else topo_n
        ultima = nf == total
        itens = [nota.itens[i] for i in indices]
        alts = [alturas[i] for i in indices]
        if ultima:
            fim = max(topo + corpo + sum(alts), topo + corpo + _MIN_CORPO_ITENS)
        else:
            fim = _FIM
        _tabela(f, topo, itens, alts, fim)
        if ultima:
            _issqn_e_adicionais(f, fim)
        if nota.tp_amb == "2":
            f.marca_dagua("SEM VALOR FISCAL\nEmitida em ambiente de homologação")
        elif not nota.protocolo:
            f.marca_dagua("SEM VALOR FISCAL\nNota sem protocolo de autorização")
    pdf = doc.tobytes(garbage=3, deflate=True, no_new_id=True)
    doc.close()
    return pdf
