"""SAC do site na garantia (28/10/2026; o caso do SAC desde 06/11/2026).

Pedido do Eduardo: "lembrando que tem o SAC também, que precisamos ter ali e
ver o e-mail do SAC". O formulário de uranyx.com.br/sac chega por e-mail (de
sac@ para sac@, ou pelo Brevo) e a ponte já faz dele uma conversa por
PROTOCOLO (`mail-protocolo:US-26-NNNN`, plataforma `site`, canal `email`) — o
corpo do formulário fica numa mensagem, uma linha por campo, o RÓTULO colado
no VALOR ("CPF123.456.789-09", "Número do pedido261027QC4PXFRR", "Número da
nota fiscal (se disponível)—"; o travessão é campo vazio).

Especificação "Painel de Garantia: alterações" (05/11/2026): "o atendimento
abre sempre pelo formulário" e "só o e-mail do formulário, identificado pelo
protocolo, abre atendimento" — os outros e-mails de sac@ (Reclame Aqui,
propostas) continuam na fila de E-mail. Daí o caso (`garantia_sac`, um por
conversa de protocolo), que `sincronizar_sac` mantém:

  1. só conversa de protocolo do SAC da Uranyx (`US-AA-NNNN`) cujo 1º e-mail
     é o formulário ("SAC de Produto" no corpo) — nunca a resposta da equipe,
     a nota interna ou o e-mail que chegou depois (o protocolo é sequencial:
     outra pessoa pode escrever com ele e mandar o CPF e o pedido DELA);
  2. a conversa em que outra pessoa escreveu (alerta da ponte) não abre caso
     e, se o caso existe sem confirmação e foi o robô que ligou, sai da
     garantia (o vínculo à mão e o confirmado ficam: a pessoa já decidiu);
  3. "Ligação do SAC com o pedido. O cliente informa o número do pedido da
     plataforma, não o do Bling. Ordem de busca: 1. Número do pedido na
     plataforma 2. Número da nota fiscal 3. CPF + produto" — vale o 1º passo
     que acha UMA garantia viva; o passo que acha mais de uma não liga e passa
     para o próximo (nenhum achou uma só: fica em "SAC sem pedido" para a
     pessoa vincular). A pessoa confere tudo no "Confirmar" antes de virar
     Garantia acionada.
     Revisão de 06/11/2026: o nº curto e a NF se repetem entre os 13 CNPJs e
     com outros vendedores (o formulário aceita compra em outra loja) — ligar
     só por eles punha o formulário (CPF, endereço, telefone) de uma pessoa
     na garantia de OUTRA. Sem o CPF do formulário, só o nº da plataforma
     longo (10+ caracteres, único) e os pedidos do pack do ML ligam; o
     `pedido_bling` e a NF só ligam quando o CPF válido do formulário é o
     CPF da garantia.

O caso confirmado sai da aba Atendimento quando a pessoa encerra ou quando
termina sozinho (`encerrar_terminados`).

Só lê o que a ponte já gravou; nada sai para fora. NÃO commita.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import UTC, date, datetime

from sqlalchemy import and_, case, delete, exists, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.atendimento import AtendimentoConversa, AtendimentoMensagem
from app.models.garantia import Garantia, GarantiaConversa, GarantiaSac
from app.models.nf import NfNota
from app.services import garantia as svc
from app.services.atendimento.constantes import AUTOR_CLIENTE, AUTOR_SISTEMA
from app.services.mail_atendimento.constantes import (
    ALERTA_OUTRO_REMETENTE,
    ALERTA_PROTOCOLO_REPETIDO,
)
from app.services.mail_atendimento.ponte import (
    PREFIXO_PROTOCOLO,
    clientes_da_conversa,
    dados_mail,
)

# O campo vazio do e-mail do site.
_VAZIO = {"", "—", "–", "-"}
# Pedido de verdade tem 5+ caracteres (o "9" do formulário de teste não é).
PEDIDO_MIN = 5
# O nº da plataforma que liga SEM o CPF (revisão de 06/11/2026): o longo é
# único (Shopee 14, ML 13–16, TikTok 18 caracteres); o curto pode ser o pedido
# de outra loja ou de outro vendedor.
PEDIDO_LONGO = 10
# Outro endereço escreveu com o protocolo, ou a conversa é a de outra cliente
# que citou este protocolo: tem outra pessoa na conversa.
ALERTAS_DE_OUTRA_PESSOA = frozenset({ALERTA_OUTRO_REMETENTE, ALERTA_PROTOCOLO_REPETIDO})
# Só o SAC de produto da Uranyx abre caso (o atacado "UA" e as dúvidas "UDS"
# não são garantia).
PREFIXO_SAC = PREFIXO_PROTOCOLO + "US-"
RX_PROTOCOLO_SAC = re.compile(r"^US-\d{2}-\d{4,}$")
# A marca do formulário no corpo do e-mail (o cabeçalho "SAC de Produto · …").
MARCA_DO_FORMULARIO = "SAC de Produto"
ROTULO_ROBO = svc.ROTULO_GARANTIA_AUTOMATICA
VINCULO_ROTULOS = {
    "pedido": "pelo nº do pedido",
    "nf": "pela nota fiscal",
    "cpf_produto": "pelo CPF e o produto",
    "manual": "à mão",
}

# ── O formulário ─────────────────────────────────────────────────────────────
#
# Os rótulos do formulário, NA ORDEM em que ele os escreve (levantamento de
# 06/11/2026 na produção + os da tela, `ROTULOS_SAC` de apps/web/lib/
# garantias.ts). Cada rótulo vale uma vez e só depois do anterior: a
# continuação da descrição ("Correios entregou…") nunca vira "Cor". A
# explicação entre parênteses só é aceita onde o valor nunca começa por "("
# (o telefone começa: "(11) 9…").
_PARENTESES = r"(?:\s*\([^)\n]*\))?"
_ROTULOS: tuple[tuple[str, str], ...] = (
    ("nome", r"Nome completo" + _PARENTESES),
    ("cpf", r"CPF(?![A-Za-zÀ-ÿ])"),
    ("email", r"E-?mail"),
    ("telefone", r"Telefone(?:\s*/\s*WhatsApp)?"),
    ("cep", r"CEP"),
    ("endereco", r"Endere[çc]o(?: completo)?"),
    ("local_compra", r"Local da compra" + _PARENTESES),
    ("loja_vendedor", r"Nome da loja\s*/\s*vendedor"),
    ("pedido", r"N[uú]mero do pedido" + _PARENTESES),
    ("data_compra", r"Data da compra"),
    ("data_recebimento", r"Data de recebimento"),
    ("nota_fiscal", r"N[uú]mero da nota fiscal" + _PARENTESES),
    ("valor_pago", r"Valor pago"),
    ("modelo", r"Modelo"),
    ("cor", r"Cor"),
    ("voltagem", r"Voltagem" + _PARENTESES),
    ("serie", r"N[uú]mero de s[ée]rie(?: ou lote)?" + _PARENTESES),
    # O fim do rótulo não foi confirmado na produção ("Data em que o problema
    # foi identificado"): vai até o 1º dígito ou travessão (o valor é a data).
    ("data_problema", r"Data em que[^0-9—–\n-]*"),
    ("descricao", r"Descri[çc][ãa]o detalhada do defeito"),
    (
        "queda_liquido_voltagem",
        r"O produto sofreu queda(?:[^?\n]*\?|, contato com l[íi]quidos?[^\n]*?voltagem"
        r"(?: diferente da indicada)?)",
    ),
    ("tipo_conexao", r"Tipo de conex[ãa]o(?: utilizada)?" + _PARENTESES),
)
# A documentação (seção 5): o nome do arquivo vem na linha seguinte (ou na
# mesma, colado). A chave é o papel que a tela mostra.
ARQUIVOS: tuple[tuple[str, str], ...] = (
    ("Foto do produto", r"Foto do produto" + _PARENTESES),
    ("Foto da etiqueta", r"Foto da etiqueta" + _PARENTESES),
    ("Foto da embalagem", r"Foto da embalagem" + _PARENTESES),
    ("Foto do defeito 1", r"Foto do defeito 1" + _PARENTESES),
    ("Foto do defeito 2", r"Foto do defeito 2" + _PARENTESES),
    # O formulário aceita até 4 fotos do defeito e o comprovante (opcional) —
    # visto na página do /sac em 06/11/2026.
    ("Foto do defeito 3", r"Foto do defeito 3" + _PARENTESES),
    ("Foto do defeito 4", r"Foto do defeito 4" + _PARENTESES),
    ("Comprovante", r"Comprovante(?: d[ae] compra)?" + _PARENTESES),
    ("Vídeo", r"V[íi]deo" + _PARENTESES),
)
CHAVES_DO_FORMULARIO = ("protocolo", "aberto_em", *(c for c, _ in _ROTULOS))
_RX_ROTULOS = [
    (chave, re.compile(rf"^\s*{rx}\s*:?\s*(.*)$", re.IGNORECASE)) for chave, rx in _ROTULOS
]
_RX_ARQUIVOS = [
    (papel, re.compile(rf"^\s*{rx}\s*:?\s*(.*)$", re.IGNORECASE)) for papel, rx in ARQUIVOS
]
_RX_SECAO = re.compile(r"^\s*\d+\.\s+\S")
_RX_PROTOCOLO = re.compile(r"\b([UC7L](?:DS|S|A)-\d{2}-\d{4,})\b")
_RX_ABERTO = re.compile(r"^\s*Aberto em\s*:?\s*(.*)$", re.IGNORECASE)


_RX_ARQUIVO = re.compile(r"\.[A-Za-z0-9]{2,5}$")


def _valor(texto: str | None) -> str | None:
    t = (texto or "").strip()
    return None if t in _VAZIO else t


def _nome_de_arquivo(texto: str) -> tuple[bool, str | None]:
    """(é o valor da foto, o nome do arquivo): o nome (termina em extensão) ou
    o travessão (a foto não foi enviada). Outro texto não é o valor (é o resto
    do rótulo, ou já é o rodapé)."""
    t = texto.strip()
    if _RX_ARQUIVO.search(t):
        return True, t
    if t and t[-1] in "—–-":
        return True, None
    return False, None


def campos_do_formulario(texto: str | None) -> dict:
    """O formulário inteiro: {protocolo, aberto_em, nome, cpf, email, telefone,
    cep, endereco, local_compra, loja_vendedor, pedido, data_compra,
    data_recebimento, nota_fiscal, valor_pago, modelo, cor, voltagem, serie,
    data_problema, descricao, queda_liquido_voltagem, tipo_conexao,
    arquivos: {papel: nome do arquivo}} — o texto como o cliente escreveu
    (sem normalizar), campo vazio ("—") ou ausente = None. O valor de várias
    linhas continua até o próximo rótulo ou a próxima seção; o nome do arquivo
    de cada foto vem colado ou na linha seguinte. PURA."""
    saida: dict = dict.fromkeys(CHAVES_DO_FORMULARIO)
    arquivos: dict[str, str | None] = {}
    partes: dict[str, list[str]] = {}
    proximo = 0  # o índice do próximo rótulo aceito (a ordem do formulário)
    atual: str | None = None  # o campo de texto que ainda pode continuar
    foto: str | None = None  # a foto cujo nome ainda pode vir na linha seguinte
    for linha in (texto or "").splitlines():
        limpa = linha.strip()
        if foto is not None:
            if not limpa:
                continue
            e_valor, nome = _nome_de_arquivo(limpa)
            foto_antes, foto = foto, None
            if e_valor:
                arquivos[foto_antes] = nome
                continue
        if saida["protocolo"] is None and (m := _RX_PROTOCOLO.search(linha)):
            saida["protocolo"] = m.group(1)
            if limpa == m.group(1):
                continue
        if saida["aberto_em"] is None and proximo == 0 and (m := _RX_ABERTO.match(linha)):
            saida["aberto_em"] = _valor(m.group(1))
            continue
        rotulo = next(
            (
                (i, chave, m)
                for i in range(proximo, len(_RX_ROTULOS))
                for chave, rx in (_RX_ROTULOS[i],)
                if (m := rx.match(linha))
            ),
            None,
        )
        if rotulo is not None:
            i, chave, m = rotulo
            partes[chave] = [m.group(1)]
            atual, proximo = chave, i + 1
            continue
        doc = next(
            ((p, m) for p, rx in _RX_ARQUIVOS if p not in arquivos and (m := rx.match(linha))),
            None,
        )
        if doc is not None:
            papel, m = doc
            atual, proximo = None, len(_RX_ROTULOS)
            e_valor, nome = _nome_de_arquivo(m.group(1))
            arquivos[papel] = nome
            if not e_valor:
                foto = papel
            continue
        if _RX_SECAO.match(linha) or limpa.startswith(MARCA_DO_FORMULARIO):
            atual = None
            continue
        # A ponte grava um campo por parágrafo: a linha em branco no meio do
        # endereço ou da descrição não é do cliente.
        if atual is not None and limpa:
            partes[atual].append(linha)
    for chave, pedacos in partes.items():
        saida[chave] = _valor("\n".join(pedacos))
    saida["arquivos"] = arquivos
    return saida


def campos_do_sac(texto: str | None) -> dict[str, str | None]:
    """CPF (11 dígitos válidos), nota fiscal (só dígitos, sem zero à
    esquerda), pedido (letras e números, maiúsculo) e local da compra do corpo
    do formulário — o que a ligação com a garantia usa. Campo vazio, ausente
    ou sem cara do que é → None. PURA."""
    c = campos_do_formulario(texto)
    return {
        "cpf": cpf_do_formulario(c),
        "nota_fiscal": nf_do_formulario(c),
        "pedido": pedido_do_formulario(c),
        "local_compra": c.get("local_compra"),
    }


def cpf_do_formulario(campos: dict) -> str | None:
    cpf = svc.so_digitos(campos.get("cpf"))
    return cpf if svc.cpf_valido(cpf) else None


def nf_do_formulario(campos: dict) -> str | None:
    """O nº da NF do formulário — UM número só ("000.002.611" = 2611). Números
    separados ("NF 2611 série 1") não se juntam: virariam 26111, a NF de outra
    pessoa (revisão de 06/11/2026). Mais de um número → None."""
    grupos = re.findall(r"\d+(?:\.\d+)*", campos.get("nota_fiscal") or "")
    if len(grupos) != 1:
        return None
    nf = svc.normalizar_nf(grupos[0])
    return nf if nf and len(nf) <= 9 else None


def pedido_do_formulario(campos: dict) -> str | None:
    pedido = re.sub(r"[^0-9A-Za-z]", "", campos.get("pedido") or "").upper()
    return pedido if len(pedido) >= PEDIDO_MIN else None


# ── O modelo do formulário × o produto da garantia ───────────────────────────

# Palavras que não dizem qual é o produto ("Celular Uranyx A17" = "A17").
_GENERICAS = frozenset(
    {
        "celular",
        "smartphone",
        "aparelho",
        "telefone",
        "uranyx",
        "modelo",
        "de",
        "da",
        "do",
        "com",
        "e",
        "o",
        "a",
    }
)


def _tokens(texto: str | None) -> list[str]:
    t = unicodedata.normalize("NFKD", texto or "")
    t = "".join(ch for ch in t if not unicodedata.combining(ch)).lower()
    return [p for p in re.split(r"[^0-9a-z]+", t) if p and p not in _GENERICAS]


def modelo_casa(modelo: str | None, nomes: list[str | None]) -> bool:
    """O "Modelo" do formulário é um dos `nomes` do produto (o da Tabela de
    Preços ou a descrição do item)? Sem acento nem caixa, por palavras: todas
    as palavras de um estão no outro (o cliente escreve "A17 Pro Max", a
    Tabela diz "A17 Pro Max - Laranja"), ou um está colado dentro do outro
    ("a17promax"). PURA."""
    a = _tokens(modelo)
    if not a:
        return False
    for nome in nomes:
        b = _tokens(nome)
        if not b:
            continue
        if set(a) <= set(b) or set(b) <= set(a):
            return True
        ja, jb = "".join(a), "".join(b)
        if len(ja) >= 3 and len(jb) >= 3 and (ja in jb or jb in ja):
            return True
    return False


# ── A conversa do protocolo ──────────────────────────────────────────────────


def protocolo_da_conversa(c: AtendimentoConversa) -> str | None:
    """`mail-protocolo:US-26-0004` → `US-26-0004` (None se não é do SAC)."""
    ext = c.externo_id or ""
    if not ext.startswith(PREFIXO_PROTOCOLO):
        return None
    dados = c.dados if isinstance(c.dados, dict) else {}
    mail = dados.get("mail") if isinstance(dados.get("mail"), dict) else {}
    return (mail.get("protocolo") or ext[len(PREFIXO_PROTOCOLO) :].split(":", 1)[0]) or None


def com_outra_pessoa(c: AtendimentoConversa) -> bool:
    """A ponte avisou que outra pessoa escreveu na conversa do protocolo (ou
    ela junta e-mails de mais de um cliente)."""
    alertas = {a for a in (dados_mail(c).get("alertas") or []) if isinstance(a, str)}
    return bool(alertas & ALERTAS_DE_OUTRA_PESSOA) or len(clientes_da_conversa(c)) > 1


async def mensagem_do_formulario(
    session: AsyncSession, c: AtendimentoConversa
) -> AtendimentoMensagem | None:
    """O e-mail do formulário: a 1ª mensagem da conversa do protocolo, de
    texto, do cliente (como a ponte grava o de sac@ para sac@ e o do Brevo) ou
    do sistema (o de antes de 27/10), com o nº do protocolo no corpo. None se
    a 1ª mensagem não for o formulário — nunca procura nas seguintes."""
    protocolo = protocolo_da_conversa(c)
    if not protocolo:
        return None
    m = AtendimentoMensagem
    primeira = (
        await session.execute(
            select(m)
            .where(m.conversa_id == c.id)
            .order_by(func.coalesce(m.enviada_em, m.created_at), m.id)
            .limit(1)
        )
    ).scalar_one_or_none()
    if primeira is None or primeira.tipo != "texto":
        return None
    if primeira.autor not in (AUTOR_CLIENTE, AUTOR_SISTEMA):
        return None
    return primeira if protocolo in (primeira.texto or "") else None


def _quando(m: AtendimentoMensagem) -> datetime:
    q = m.enviada_em or m.created_at
    return q if q.tzinfo else q.replace(tzinfo=UTC)


def ordem_dos_casos(s=GarantiaSac) -> tuple:
    """Qual caso a garantia mostra quando tem mais de um (o cliente mandou o
    formulário de novo) — a MESMA escolha na linha da lista, no Resumo do SAC
    e na aba Atendimento (revisão de 06/11/2026: o formulário novo, ainda sem
    confirmação, tomava o lugar do caso confirmado e aberto, e o Encerrar
    sumia): 1º o confirmado e aberto mais antigo; 2º o aberto sem confirmação
    mais antigo; 3º o encerrado mais recente. `order_by(*ordem_dos_casos())`."""
    aberto = s.encerrado_em.is_(None)
    grupo = case((~aberto, 2), (s.confirmado_em.is_(None), 1), else_=0)
    return (
        grupo,
        case((aberto, s.recebido_em)).asc().nulls_last(),
        s.recebido_em.desc(),
        case((aberto, s.id)).asc().nulls_last(),
        s.id.desc(),
    )


# ── A ligação com a garantia ─────────────────────────────────────────────────


def _pedido_normalizado(coluna):
    return func.upper(func.regexp_replace(coluna, r"[^0-9A-Za-z]", "", "g"))


async def _garantias_pelo_pedido(session: AsyncSession, pedido: str, cpf: str | None) -> list[int]:
    """As vivas do nº do pedido. Sem conferir o CPF: o nº da plataforma longo
    (`PEDIDO_LONGO`) e o pack do ML pelos pedidos dele (e o pedido pelo pack).
    Com o CPF válido do formulário igual ao da garantia: também o nº curto da
    plataforma e o do Bling (que se repete entre lojas)."""
    g = Garantia
    da_plataforma = _pedido_normalizado(g.pedido_marketplace) == pedido
    conds = [
        g.pedido_marketplace.in_(svc.pedidos_do_pack(pedido)),
        g.pedido_marketplace.in_(svc.pack_do_pedido(pedido)),
    ]
    if len(pedido) >= PEDIDO_LONGO:
        conds.append(da_plataforma)
    if cpf:
        conds.append(and_(g.cpf == cpf, or_(da_plataforma, g.pedido_bling == pedido)))
    q = select(g.id).where(g.descartada_em.is_(None), or_(*conds)).order_by(g.id)
    return list((await session.execute(q)).scalars())


async def _garantias_pela_nf(session: AsyncSession, nf: str, cpf: str) -> list[int]:
    """As vivas do CPF do formulário com a NF: a da garantia ou qualquer NF do
    mesmo pedido (a de embalagem que o cliente recebeu por fora). Só com o
    CPF: o nº da NF se repete entre os CNPJs e com outros vendedores."""
    g = Garantia
    q = (
        select(g.id)
        .where(
            g.descartada_em.is_(None),
            g.cpf == cpf,
            or_(
                g.nf_numero == nf,
                exists().where(
                    NfNota.pedido_bling == g.pedido_bling,
                    func.ltrim(func.regexp_replace(NfNota.numero, r"\D", "", "g"), "0") == nf,
                ),
            ),
        )
        .order_by(g.id)
    )
    return list((await session.execute(q)).scalars())


async def _garantias_pelo_cpf_e_produto(
    session: AsyncSession, cpf: str, modelo: str | None, tabela: dict | None
) -> list[int]:
    """As vivas do CPF cujo produto (o nome na Tabela de Preços ou a descrição
    do item principal) casa com o "Modelo" do formulário."""
    if not modelo:
        return []
    from app.services import garantia_resumo

    g = Garantia
    linhas = (
        await session.execute(
            select(g.id, g.itens).where(g.cpf == cpf, g.descartada_em.is_(None)).order_by(g.id)
        )
    ).all()
    if not linhas:
        return []
    if tabela is None:
        tabela = await garantia_resumo._tabela_de_precos(session)
    nomes_da_tabela = {pid: nome for pid, nome, _bases in tabela.values()}
    saida = []
    for gid, itens in linhas:
        item = garantia_resumo.item_principal(itens) or {}
        chave = garantia_resumo._linha_do_sku(item.get("sku"), tabela)
        nomes = [garantia_resumo.nome_do_produto(item.get("descricao"))]
        if chave.startswith("tabela:"):
            nomes.insert(0, nomes_da_tabela.get(chave.split(":", 1)[1]))
        if modelo_casa(modelo, nomes):
            saida.append(gid)
    return saida


async def garantia_do_formulario(
    session: AsyncSession, campos: dict, *, tabela: dict | None = None
) -> tuple[int | None, str | None]:
    """(garantia, como) pela ordem da especificação — o 1º passo que acha UMA
    garantia viva liga; o que acha mais de uma não liga (ambíguo) e passa para
    o próximo. Nenhum achou uma só: (None, None) = SAC sem pedido (a pessoa
    vincula). A NF só com o CPF do formulário (`_garantias_pela_nf`)."""
    pedido, nf, cpf = (
        pedido_do_formulario(campos),
        nf_do_formulario(campos),
        cpf_do_formulario(campos),
    )
    for como in ("pedido", "nf", "cpf_produto"):
        if como == "pedido" and pedido:
            ids = await _garantias_pelo_pedido(session, pedido, cpf)
        elif como == "nf" and nf and cpf:
            ids = await _garantias_pela_nf(session, nf, cpf)
        elif como == "cpf_produto" and cpf:
            ids = await _garantias_pelo_cpf_e_produto(session, cpf, campos.get("modelo"), tabela)
        else:
            continue
        if len(ids) == 1:
            return ids[0], como
    return None, None


async def ligar_conversa(
    session: AsyncSession,
    caso: GarantiaSac,
    garantia_id: int,
    como: str,
    *,
    ator=None,
    agora: datetime | None = None,
) -> None:
    """Liga o caso à garantia: o vínculo, a conversa no histórico da garantia
    (`garantia_conversas`, como='pedido') e o "vinculou o SAC" no log."""
    agora = agora or datetime.now(UTC)
    caso.garantia_id = garantia_id
    caso.vinculo = como
    caso.vinculado_em = agora
    caso.vinculado_por = ator.id if ator is not None else None
    await session.execute(
        pg_insert(GarantiaConversa)
        .values(garantia_id=garantia_id, conversa_id=caso.conversa_id, como="pedido")
        .on_conflict_do_nothing()
    )
    svc.registrar_log(
        session,
        garantia_id,
        "vinculou_sac",
        ator,
        detalhe=f"SAC do site · protocolo {caso.protocolo} · {VINCULO_ROTULOS.get(como, como)}",
        robo=ROTULO_ROBO,
    )


async def desligar_conversa(session: AsyncSession, caso: GarantiaSac) -> None:
    """Tira o caso (ainda não confirmado) da garantia: volta para SAC sem
    pedido e a conversa sai do histórico dela."""
    if caso.garantia_id is not None:
        await session.execute(
            delete(GarantiaConversa).where(
                GarantiaConversa.garantia_id == caso.garantia_id,
                GarantiaConversa.conversa_id == caso.conversa_id,
            )
        )
    caso.garantia_id = None
    caso.vinculo = None
    caso.vinculado_em = None
    caso.vinculado_por = None


async def sincronizar_sac(session: AsyncSession, desde: datetime | None = None) -> dict:
    """Cria/atualiza os casos do SAC pelas conversas de protocolo e liga à
    garantia os que ainda não têm. `desde` = só as conversas criadas/mexidas
    depois (a volta de 15 min); sem ele, todas (a rodada diária e o
    retroativo). NÃO commita. Devolve {casos_novos, ligados, desligados}."""
    from app.services import garantia_resumo

    c = AtendimentoConversa
    q = select(c).where(c.externo_id.like(PREFIXO_SAC + "%"))
    if desde is not None:
        q = q.where(
            or_(c.created_at >= desde, c.updated_at >= desde, c.ultima_mensagem_em >= desde)
        )
    conversas = list((await session.execute(q)).scalars())
    contagem = {"casos_novos": 0, "ligados": 0, "desligados": 0}
    if not conversas:
        return contagem
    casos = {
        k.conversa_id: k
        for k in (
            await session.execute(
                select(GarantiaSac).where(GarantiaSac.conversa_id.in_([x.id for x in conversas]))
            )
        ).scalars()
    }
    tabela = None
    for conversa in conversas:
        protocolo = protocolo_da_conversa(conversa)
        if not protocolo or not RX_PROTOCOLO_SAC.match(protocolo):
            continue
        caso = casos.get(conversa.id)
        if com_outra_pessoa(conversa):
            # O robô só desfaz o que ele mesmo ligou: a decisão da pessoa (o
            # vínculo à mão, o Confirmar) vale mais que o alerta (revisão de
            # 06/11/2026 — antes o à mão voltava para "SAC sem pedido" toda
            # rodada, sem log).
            if (
                caso is not None
                and caso.confirmado_em is None
                and caso.garantia_id is not None
                and caso.vinculo != "manual"
            ):
                await desligar_conversa(session, caso)
                contagem["desligados"] += 1
            elif caso is None:
                # A ligação antiga (o robô de antes de 06/11) também sai.
                await session.execute(
                    delete(GarantiaConversa).where(GarantiaConversa.conversa_id == conversa.id)
                )
            continue
        formulario = await mensagem_do_formulario(session, conversa)
        if formulario is None or MARCA_DO_FORMULARIO not in (formulario.texto or ""):
            continue
        campos = campos_do_formulario(formulario.texto)
        if caso is None:
            caso = GarantiaSac(conversa_id=conversa.id)
            session.add(caso)
            contagem["casos_novos"] += 1
        caso.mensagem_id = formulario.id
        caso.protocolo = protocolo
        caso.recebido_em = _quando(formulario)
        caso.campos = campos
        await session.flush()
        if caso.garantia_id is None and caso.vinculo is None:
            if tabela is None:
                tabela = await garantia_resumo._tabela_de_precos(session)
            gid, como = await garantia_do_formulario(session, campos, tabela=tabela)
            if gid is not None:
                await ligar_conversa(session, caso, gid, como)
                contagem["ligados"] += 1
    await session.flush()
    return contagem


async def encerrar_terminados(session: AsyncSession, dia: date | None = None) -> int:
    """O caso confirmado e aberto cuja garantia TERMINOU sozinha depois da
    confirmação (o aparelho consertado ou perdido, a devolução com a condição
    lançada — `garantia_atendimento.terminou`) sai da aba Atendimento: o robô
    encerra (motivo 'terminou') e o tempo em aberto para de contar.

    O desfecho só tem a data de quando a devolução foi LANÇADA (`desde`); o
    reembolso pago não tem data nenhuma. O critério é o mais seguro: só
    encerra quando o desfecho tem data e ela é DEPOIS da confirmação (o
    conserto de antes do SAC não fecha o caso novo); sem data, fica para a
    pessoa encerrar. NÃO commita. Devolve quantos encerrou."""
    from app.services import garantia_atendimento, garantia_resumo

    s, g = GarantiaSac, Garantia
    abertos = list(
        (
            await session.execute(
                select(s).where(
                    s.confirmado_em.is_not(None),
                    s.encerrado_em.is_(None),
                    s.garantia_id.is_not(None),
                )
            )
        ).scalars()
    )
    if not abertos:
        return 0
    registros = (
        await session.execute(
            select(g.id, g.pedido_bling, g.pedido_marketplace, g.itens).where(
                g.id.in_({k.garantia_id for k in abertos})
            )
        )
    ).all()
    desfechos = await garantia_resumo.desfechos(session, registros, dia or svc.hoje())
    agora = datetime.now(UTC)
    encerrados = 0
    for caso in abertos:
        f = desfechos.get(caso.garantia_id)
        if f is None or not garantia_atendimento.terminou(f) or f.desde is None:
            continue
        desde = f.desde if f.desde.tzinfo else f.desde.replace(tzinfo=UTC)
        if desde <= caso.confirmado_em:
            continue
        feito = await session.execute(
            update(GarantiaSac)
            .where(GarantiaSac.id == caso.id, GarantiaSac.encerrado_em.is_(None))
            .values(encerrado_em=agora, encerrado_motivo="terminou", encerrado_por=None)
        )
        if not feito.rowcount:
            continue
        encerrados += 1
        svc.registrar_log(
            session,
            caso.garantia_id,
            "encerrou_atendimento",
            None,
            detalhe=f"protocolo {caso.protocolo} · terminou sozinho"
            + (f" ({f.condicao})" if f.condicao else ""),
            robo=ROTULO_ROBO,
        )
    return encerrados
