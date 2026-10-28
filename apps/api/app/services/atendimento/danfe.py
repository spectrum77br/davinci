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
    bc_icms: str = ""
    v_icms: str = ""
    v_ipi: str = ""
    aliq_icms: str = ""


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
    info: str
    fisco: str
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
    d = _dig(v)
    return f"{d[:5]}-{d[5:]}" if len(d) == 8 else v


def _fone(v: str) -> str:
    d = _dig(v)
    if len(d) in (10, 11):
        return f"({d[:2]}) {d[2:-4]}-{d[-4:]}"
    return v


def chave_formatada(chave: str) -> str:
    return " ".join(chave[i : i + 4] for i in range(0, len(chave), 4))


def numero_formatado(numero: str) -> str:
    d = _dig(numero).rjust(9, "0")[-9:]
    return f"{d[:3]}.{d[3:6]}.{d[6:]}"


_MOD_FRETE = {
    "0": "0 - Por conta do Remetente (CIF)",
    "1": "1 - Por conta do Destinatário (FOB)",
    "2": "2 - Por conta de Terceiros",
    "3": "3 - Próprio por conta do Remetente",
    "4": "4 - Próprio por conta do Destinatário",
    "9": "9 - Sem Ocorrência de Transporte",
}


def _endereco(no, prefixo: str) -> dict[str, str]:
    end = no.find(f"n:ender{prefixo}", NS) if no is not None else None
    lgr = ", ".join(x for x in (_t(end, "n:xLgr"), _t(end, "n:nro")) if x)
    cpl = _t(end, "n:xCpl")
    return {
        "logradouro": f"{lgr} - {cpl}" if cpl else lgr,
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
    transp = inf.find("n:transp", NS)
    vol = transp.find("n:vol", NS) if transp is not None else None
    emissao, _ = _data(_t(ide, "n:dhEmi"))
    saida_data, saida_hora = _data(_t(ide, "n:dhSaiEnt"))
    n_prot = _t(prot, "n:nProt")
    receb = " ".join(x for x in _data(_t(prot, "n:dhRecbto")) if x)
    protocolo = f"{n_prot} - {receb}" if n_prot and receb else n_prot
    if _t(prot, "n:cStat") not in ("", SITUACAO_AUTORIZADA):
        protocolo = ""  # protocolo de rejeição/denegação não autoriza o uso
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
        protocolo=protocolo,
        emit={
            "nome": _t(emit, "n:xNome"),
            "doc": doc_formatado(_t(emit, "n:CNPJ") or _t(emit, "n:CPF")),
            "ie": _t(emit, "n:IE"),
            "iest": _t(emit, "n:IEST"),
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
            k: _num(_t(icms_tot, f"n:{k}"))
            for k in (
                "vBC",
                "vICMS",
                "vBCST",
                "vST",
                "vProd",
                "vFrete",
                "vSeg",
                "vDesc",
                "vOutro",
                "vIPI",
                "vNF",
                "vTotTrib",
            )
        },
        transp={
            "nome": _t(transp, "n:transporta/n:xNome"),
            "doc": doc_formatado(
                _t(transp, "n:transporta/n:CNPJ") or _t(transp, "n:transporta/n:CPF")
            ),
            "frete": _MOD_FRETE.get(_t(transp, "n:modFrete"), _t(transp, "n:modFrete")),
            "qvol": _t(vol, "n:qVol"),
            "esp": _t(vol, "n:esp"),
            "peso_b": _num(_t(vol, "n:pesoB"), 3),
            "peso_l": _num(_t(vol, "n:pesoL"), 3),
        },
        info=_t(inf, "n:infAdic/n:infCpl"),
        fisco=_t(inf, "n:infAdic/n:infAdFisco"),
    )
    for det in inf.findall("n:det", NS):
        prod = det.find("n:prod", NS)
        icms = det.find("n:imposto/n:ICMS", NS)
        grupo = icms[0] if icms is not None and len(icms) else None
        cst = _t(grupo, "n:CSOSN") or _t(grupo, "n:CST")
        nota.itens.append(
            _Item(
                codigo=_t(prod, "n:cProd"),
                descricao=_t(prod, "n:xProd"),
                ncm=_t(prod, "n:NCM"),
                cst=f"{_t(grupo, 'n:orig')}{cst}",
                cfop=_t(prod, "n:CFOP"),
                un=_t(prod, "n:uCom"),
                qtd=_num(_t(prod, "n:qCom"), 4),
                v_unit=_num(_t(prod, "n:vUnCom"), 4),
                v_total=_num(_t(prod, "n:vProd")),
                bc_icms=_num(_t(grupo, "n:vBC")) or "0,00",
                v_icms=_num(_t(grupo, "n:vICMS")) or "0,00",
                v_ipi=_num(_t(det, "n:imposto/n:IPI/n:IPITrib/n:vIPI")) or "0,00",
                aliq_icms=_num(_t(grupo, "n:pICMS")) or "0,00",
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


# ── Desenho (PyMuPDF) ─────────────────────────────────────────────────────

_A4 = (595.0, 842.0)
_M = 18.0  # margem
_L = _A4[0] - 2 * _M  # largura útil
_FONTE, _NEGRITO = "helv", "hebo"
_ROTULO = 5.2


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
    """Uma página da DANFE com os ajudantes de caixa e texto."""

    def __init__(self, doc, nota: _Nota) -> None:
        self.p = doc.new_page(width=_A4[0], height=_A4[1])
        self.nota = nota

    def texto(
        self,
        x: float,
        y: float,
        w: float,
        texto: str,
        *,
        tam: float = 7.5,
        negrito: bool = False,
        alinhar: int = 0,
        linhas_max: int = 1,
    ) -> float:
        """Escreve até `linhas_max` linhas em [x, x+w] a partir da linha de base `y`;
        encolhe a fonte até caber (mínimo 4,5). Devolve a altura usada."""
        import pymupdf

        fonte = _NEGRITO if negrito else _FONTE
        while True:
            linhas = _cabe(texto, tam, w, fonte, ate=linhas_max)
            if len(linhas) <= linhas_max or tam <= 4.5:
                break
            tam -= 0.5
        linhas = linhas[:linhas_max]
        for i, linha in enumerate(linhas):
            larg = pymupdf.get_text_length(linha, fontname=fonte, fontsize=tam)
            dx = 0.0 if alinhar == 0 else (w - larg) / (2 if alinhar == 1 else 1)
            self.p.insert_text((x + dx, y + i * tam * 1.15), linha, fontname=fonte, fontsize=tam)
        return len(linhas) * tam * 1.15

    def caixa(
        self,
        x: float,
        y: float,
        w: float,
        h: float,
        rotulo: str,
        valor: str = "",
        *,
        tam: float = 7.5,
        negrito: bool = False,
        alinhar: int = 0,
        linhas_max: int = 1,
    ) -> None:
        import pymupdf

        self.p.draw_rect(pymupdf.Rect(x, y, x + w, y + h), width=0.5)
        if rotulo:
            self.texto(x + 2, y + 6, w - 4, rotulo, tam=_ROTULO)
        if valor:
            self.texto(
                x + 2,
                y + 6 + tam + 1,
                w - 4,
                valor,
                tam=tam,
                negrito=negrito,
                alinhar=alinhar,
                linhas_max=linhas_max,
            )

    def titulo(self, y: float, texto: str) -> float:
        self.texto(_M, y + 6, _L, texto, tam=6, negrito=True)
        return y + 8

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
            pymupdf.Rect(_M, 360, _A4[0] - _M, 500),
            texto,
            fontname=_NEGRITO,
            fontsize=26,
            align=1,
            color=(0.8, 0.1, 0.1),
            fill_opacity=0.35,
        )


def _cabecalho(f: _Folha, y: float, folha: int, folhas: int) -> float:
    """Emitente + caixa DANFE + código de barras/chave + natureza/protocolo + IE/CNPJ."""
    n = f.nota
    e = n.emit
    h = 104.0
    w_emit, w_danfe = 228.0, 102.0
    w_chave = _L - w_emit - w_danfe
    f.caixa(_M, y, w_emit, h, "IDENTIFICAÇÃO DO EMITENTE")
    yy = y + 22
    yy += f.texto(_M + 4, yy, w_emit - 8, e["nome"], tam=9.5, negrito=True, alinhar=1, linhas_max=2)
    endereco = "\n".join(
        x
        for x in (
            e["logradouro"],
            " - ".join(v for v in (e["bairro"], e["cep"]) if v),
            " - ".join(v for v in (e["municipio"], e["uf"]) if v),
            f"Fone: {e['fone']}" if e["fone"] else "",
        )
        if x
    )
    f.texto(_M + 4, yy + 4, w_emit - 8, endereco, tam=7, alinhar=1, linhas_max=5)

    x = _M + w_emit
    f.caixa(x, y, w_danfe, h, "")
    f.texto(x, y + 16, w_danfe, "DANFE", tam=13, negrito=True, alinhar=1)
    f.texto(
        x + 4,
        y + 26,
        w_danfe - 8,
        "Documento Auxiliar da Nota Fiscal Eletrônica",
        tam=6.5,
        alinhar=1,
        linhas_max=2,
    )
    f.texto(x + 8, y + 50, 60, "0 - ENTRADA", tam=6.5)
    f.texto(x + 8, y + 58, 60, "1 - SAÍDA", tam=6.5)
    import pymupdf

    f.p.draw_rect(pymupdf.Rect(x + 72, y + 44, x + 88, y + 60), width=0.5)
    f.texto(x + 72, y + 56, 16, n.tp_nf, tam=10, negrito=True, alinhar=1)
    f.texto(
        x, y + 74, w_danfe, f"Nº {numero_formatado(n.numero)}", tam=8.5, negrito=True, alinhar=1
    )
    f.texto(x, y + 84, w_danfe, f"SÉRIE {n.serie}", tam=8, negrito=True, alinhar=1)
    f.texto(x, y + 95, w_danfe, f"FOLHA {folha}/{folhas}", tam=7, alinhar=1)

    x += w_danfe
    f.caixa(x, y, w_chave, 42, "")
    f.barras(x + 8, y + 5, w_chave - 16, 32, n.chave)
    f.caixa(
        x,
        y + 42,
        w_chave,
        22,
        "CHAVE DE ACESSO",
        chave_formatada(n.chave),
        tam=7.5,
        negrito=True,
        alinhar=1,
    )
    f.caixa(x, y + 64, w_chave, h - 64, "")
    f.texto(
        x + 4,
        y + 76,
        w_chave - 8,
        "Consulta de autenticidade no portal nacional da NF-e www.nfe.fazenda.gov.br/portal "
        "ou no site da Sefaz Autorizadora",
        tam=7,
        alinhar=1,
        linhas_max=3,
    )
    y += h
    f.caixa(_M, y, _L - w_chave, 22, "NATUREZA DA OPERAÇÃO", n.nat_op)
    f.caixa(
        _M + _L - w_chave,
        y,
        w_chave,
        22,
        "PROTOCOLO DE AUTORIZAÇÃO DE USO",
        n.protocolo,
        negrito=True,
        alinhar=1,
    )
    y += 22
    terco = _L / 3
    f.caixa(_M, y, terco, 22, "INSCRIÇÃO ESTADUAL", e["ie"])
    f.caixa(_M + terco, y, terco, 22, "INSCRIÇÃO ESTADUAL DO SUBST. TRIBUT.", e["iest"])
    f.caixa(_M + 2 * terco, y, terco, 22, "CNPJ / CPF", e["doc"])
    return y + 22


def _linha(f: _Folha, y: float, h: float, campos: list[tuple[str, str, float]], **kw) -> float:
    """Uma fileira de caixas; as larguras são proporções de `_L`."""
    x = _M
    soma = sum(p for _, _, p in campos)
    for rotulo, valor, prop in campos:
        w = _L * prop / soma
        f.caixa(x, y, w, h, rotulo, valor, **kw)
        x += w
    return y + h


# Colunas da tabela de produtos: (rótulo, atributo, largura, alinhamento).
_COLUNAS = (
    ("CÓDIGO", "codigo", 50, 0),
    ("DESCRIÇÃO DO PRODUTO / SERVIÇO", "descricao", 175, 0),
    ("NCM/SH", "ncm", 36, 1),
    ("CSOSN / CST", "cst", 27, 1),
    ("CFOP", "cfop", 22, 1),
    ("UN", "un", 18, 1),
    ("QUANT.", "qtd", 34, 2),
    ("VALOR UNIT.", "v_unit", 38, 2),
    ("VALOR TOTAL", "v_total", 38, 2),
    ("B.CÁLC. ICMS", "bc_icms", 34, 2),
    ("VALOR ICMS", "v_icms", 32, 2),
    ("VALOR IPI", "v_ipi", 30, 2),
    ("ALÍQ. ICMS", "aliq_icms", 25, 2),
)
_TAM_ITEM = 6.3
_ALT_LINHA_ITEM = _TAM_ITEM * 1.15


def _alturas_dos_itens(itens: list[_Item]) -> list[float]:
    desc = next(c for c in _COLUNAS if c[1] == "descricao")
    return [
        max(1, len(_cabe(i.descricao, _TAM_ITEM, desc[2] - 4))) * _ALT_LINHA_ITEM + 4 for i in itens
    ]


def _tabela(f: _Folha, y: float, itens: list[_Item], alturas: list[float], fim: float) -> float:
    """O cabeçalho da tabela e os itens (já cabendo em `fim`); devolve o y final."""
    import pymupdf

    y = f.titulo(y, "DADOS DOS PRODUTOS / SERVIÇOS")
    x = _M
    for rotulo, _, w, _ in _COLUNAS:
        f.p.draw_rect(pymupdf.Rect(x, y, x + w, y + 14), width=0.5)
        f.texto(x + 1, y + 6, w - 2, rotulo, tam=_ROTULO, alinhar=1, linhas_max=2)
        x += w
    y += 14
    topo = y
    for item, alt in zip(itens, alturas, strict=True):
        x = _M
        for _, attr, w, alinhar in _COLUNAS:
            valor = getattr(item, attr)
            if attr == "descricao":
                linhas = _cabe(valor, _TAM_ITEM, w - 4)
                for i, ln in enumerate(linhas):
                    f.p.insert_text(
                        (x + 2, y + _TAM_ITEM + 1 + i * _ALT_LINHA_ITEM),
                        ln,
                        fontname=_FONTE,
                        fontsize=_TAM_ITEM,
                    )
            else:
                f.texto(x + 2, y + _TAM_ITEM + 1, w - 4, valor, tam=_TAM_ITEM, alinhar=alinhar)
            x += w
        y += alt
    # As colunas vão até o fim do bloco (o desenho da DANFE: a tabela ocupa o espaço).
    x = _M
    for _, _, w, _ in _COLUNAS:
        f.p.draw_rect(pymupdf.Rect(x, topo, x + w, fim), width=0.5)
        x += w
    return fim


def gerar_pdf(xml: bytes) -> bytes:
    """O PDF da DANFE (retrato, A4) da NF-e do XML. Levanta `DanfeInvalida`.

    DETERMINÍSTICO (revisão de 28/10): o mesmo XML dá os mesmos bytes (sem
    data de impressão, sem /ID novo) — é o que deixa a trava de "anexo
    repetido em 2 minutos" (sha256) pegar a NF mandada duas vezes.
    """
    import pymupdf

    nota = ler_nota(xml)
    alturas = _alturas_dos_itens(nota.itens)
    # Layout da 1ª folha (até a tabela) e o que sobra para os itens.
    topo_tabela_1 = 418.0
    rodape_1 = _A4[1] - _M - 80  # dados adicionais
    topo_tabela_n = _M + 150 + 8 + 14
    fim_n = _A4[1] - _M
    # Distribui os itens pelas folhas.
    folhas: list[list[int]] = [[]]
    livre = rodape_1 - (topo_tabela_1 + 8 + 14)
    for i, alt in enumerate(alturas):
        if alt > livre and folhas[-1]:
            folhas.append([])
            livre = fim_n - topo_tabela_n
        folhas[-1].append(i)
        livre -= alt
    total = len(folhas)

    doc = pymupdf.open()
    for nf, indices in enumerate(folhas, start=1):
        f = _Folha(doc, nota)
        y = _M
        if nf == 1:
            y = _canhoto(f, y)
        y = _cabecalho(f, y, nf, total)
        if nf == 1:
            y = _destinatario(f, y + 2)
            y = _impostos(f, y + 2)
            y = _transporte(f, y + 2)
            fim = rodape_1 - 2
        else:
            fim = fim_n
        itens = [nota.itens[i] for i in indices]
        _tabela(f, y + 2, itens, [alturas[i] for i in indices], fim)
        if nf == 1:
            _adicionais(f, rodape_1)
        if nota.tp_amb == "2":
            f.marca_dagua("SEM VALOR FISCAL\nEmitida em ambiente de homologação")
        elif not nota.protocolo:
            f.marca_dagua("SEM VALOR FISCAL\nNota sem protocolo de autorização")
    pdf = doc.tobytes(garbage=3, deflate=True, no_new_id=True)
    doc.close()
    return pdf


def _canhoto(f: _Folha, y: float) -> float:
    import pymupdf

    n = f.nota
    w_nfe = 96.0
    w = _L - w_nfe
    recebemos = (
        f"RECEBEMOS DE {n.emit['nome']} OS PRODUTOS E/OU SERVIÇOS CONSTANTES DA NOTA FISCAL "
        f"ELETRÔNICA INDICADA AO LADO. EMISSÃO: {n.emissao} VALOR TOTAL: R$ {n.tot['vNF']} "
        f"DESTINATÁRIO: {n.dest['nome']}"
    )
    f.p.draw_rect(pymupdf.Rect(_M, y, _M + w, y + 22), width=0.5)
    f.texto(_M + 2, y + 7, w - 4, recebemos, tam=6, linhas_max=2)
    f.caixa(_M, y + 22, 110, 22, "DATA DE RECEBIMENTO")
    f.caixa(_M + 110, y + 22, w - 110, 22, "IDENTIFICAÇÃO E ASSINATURA DO RECEBEDOR")
    x = _M + w
    f.caixa(x, y, w_nfe, 44, "")
    f.texto(x, y + 13, w_nfe, "NF-e", tam=11, negrito=True, alinhar=1)
    f.texto(x, y + 26, w_nfe, f"Nº {numero_formatado(n.numero)}", tam=8, negrito=True, alinhar=1)
    f.texto(x, y + 37, w_nfe, f"SÉRIE {n.serie}", tam=8, negrito=True, alinhar=1)
    y += 48
    f.p.draw_line((_M, y), (_A4[0] - _M, y), width=0.5, dashes="[2 2] 0")
    return y + 4


def _destinatario(f: _Folha, y: float) -> float:
    d = f.nota.dest
    n = f.nota
    y = f.titulo(y, "DESTINATÁRIO / REMETENTE")
    y = _linha(
        f,
        y,
        22,
        [
            ("NOME / RAZÃO SOCIAL", d["nome"], 62),
            ("CNPJ / CPF", d["doc"], 22),
            ("DATA DA EMISSÃO", n.emissao, 16),
        ],
    )
    y = _linha(
        f,
        y,
        22,
        [
            ("ENDEREÇO", d["logradouro"], 46),
            ("BAIRRO / DISTRITO", d["bairro"], 26),
            ("CEP", d["cep"], 12),
            ("DATA DA SAÍDA/ENTRADA", n.saida_data, 16),
        ],
    )
    return _linha(
        f,
        y,
        22,
        [
            ("MUNICÍPIO", d["municipio"], 40),
            ("FONE / FAX", d["fone"], 18),
            ("UF", d["uf"], 6),
            ("INSCRIÇÃO ESTADUAL", d["ie"], 20),
            ("HORA DA SAÍDA/ENTRADA", n.saida_hora, 16),
        ],
    )


def _impostos(f: _Folha, y: float) -> float:
    t = f.nota.tot
    y = f.titulo(y, "CÁLCULO DO IMPOSTO")
    kw = {"alinhar": 2}
    y = _linha(
        f,
        y,
        22,
        [
            ("BASE DE CÁLC. DO ICMS", t["vBC"], 1),
            ("VALOR DO ICMS", t["vICMS"], 1),
            ("BASE DE CÁLC. ICMS S.T.", t["vBCST"], 1),
            ("VALOR DO ICMS SUBST.", t["vST"], 1),
            ("V. APROX. DOS TRIBUTOS", t["vTotTrib"], 1),
            ("VALOR TOTAL DOS PRODUTOS", t["vProd"], 1),
        ],
        **kw,
    )
    return _linha(
        f,
        y,
        22,
        [
            ("VALOR DO FRETE", t["vFrete"], 1),
            ("VALOR DO SEGURO", t["vSeg"], 1),
            ("DESCONTO", t["vDesc"], 1),
            ("OUTRAS DESPESAS", t["vOutro"], 1),
            ("VALOR TOTAL DO IPI", t["vIPI"], 1),
            ("VALOR TOTAL DA NOTA", t["vNF"], 1),
        ],
        negrito=False,
        **kw,
    )


def _transporte(f: _Folha, y: float) -> float:
    t = f.nota.transp
    y = f.titulo(y, "TRANSPORTADOR / VOLUMES TRANSPORTADOS")
    return _linha(
        f,
        y,
        22,
        [
            ("NOME / RAZÃO SOCIAL", t["nome"], 30),
            ("FRETE POR CONTA", t["frete"], 22),
            ("CNPJ / CPF", t["doc"], 16),
            ("QUANTIDADE", t["qvol"], 8),
            ("ESPÉCIE", t["esp"], 8),
            ("PESO BRUTO", t["peso_b"], 8),
            ("PESO LÍQUIDO", t["peso_l"], 8),
        ],
    )


def _adicionais(f: _Folha, y: float) -> None:
    n = f.nota
    y = f.titulo(y, "DADOS ADICIONAIS")
    w_info = _L * 0.66
    f.caixa(_M, y, w_info, 64, "INFORMAÇÕES COMPLEMENTARES", n.info, tam=6.3, linhas_max=8)
    f.caixa(_M + w_info, y, _L - w_info, 64, "RESERVADO AO FISCO", n.fisco, tam=6.3, linhas_max=8)
