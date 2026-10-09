"""Destinatário → loja (RF5 "como descobrir a loja") e caixa de site → chamado (RF6).

    plataforma (da pasta) + endereço NOSSO que recebeu → loja no cadastro do DaVinci

1. Os endereços NOSSOS do e-mail: os que RECEBERAM (o `delivered_to` do
   conector v2, depois o Para e o Cc) que estão na lista de endereços da
   CAIXA (o config dela na Central: o principal + os aliases).
2. O cadastro: `store_info.email` (só a parte antes do @ hoje: "21max" →
   21max@tuta.com; "sac@poofy" casa com sac@poofy.com.br, ver `enderecos`) e
   `marca_emails` (sac@, duvidas@, atacado@ das marcas). A loja segue o
   store_info, nunca o pricing_accounts.
3. A INTEGRAÇÃO da ficha (crítica de 08/10: em produção só 13 das 83 fichas
   com e-mail têm o FK): primeiro `store_info.integration_id` (ativa); sem
   ele, o PAR (nome da conta sem espaços nas pontas e minúsculo, plataforma
   normalizada) contra as integrações não arquivadas — o mesmo pareamento de
   `deps/team_scope.py` e de `routers/pricing._resolve_linked_integration`
   (o selo "Integração" de Cadastros › Lojas). Nem FK nem par (Temu,
   AliExpress, Magalu, a ficha com nome diferente): a LOJA continua sendo a
   ficha — o e-mail entra na conversa dela, sem integração (RF5: são contas
   que "só têm a aba E-mail"), e a Saúde lista a ficha para o dono ligar.
4. Loja = (plataforma da pasta, alias):
     1 loja                         → a loja (com ou sem integração);
     2+ lojas                       → sem loja (ambíguo), com a escolha;
     nenhuma                        → sem loja (alias sem cadastro).
   Na Entrada (sem plataforma pela pasta): o REMETENTE OFICIAL de uma
   plataforma (mercadolivre.com.br → ML) decide a plataforma quando o alias
   é de uma loja dela (crítica de 08/10: o aviso do ML para o sac@ da marca
   que também é o login da loja vai para a LOJA, nunca vira chamado do
   site). Sem isso, a loja só vale se o alias é de UMA loja de UMA
   plataforma; senão sem loja, com a sugestão pelo domínio do remetente.
5. CAIXA DE SITE (RF6) → o chamado do site, só para: o endereço de
   `marca_emails`, ou sac@/atacado@/duvidas@/support@ (`LOCAIS_DE_SITE`) num
   domínio de marca (ou numa pasta "*marca" de marca que EXISTE no
   cadastro). Qualquer outro endereço do domínio da marca (gabrieli@,
   ouvidoria@…) NÃO é site: vai para "sem loja" (só quem mexe vê). A marca:
   a da pasta "*marca" primeiro; senão a do domínio (ordem fixa pelo slug);
   domínio em DUAS marcas sem pasta → `marca_ambigua` (a ponte tenta o
   protocolo; senão a pessoa escolhe).
6. Endereço que não é da caixa (encaminhado de fora; ou só em Cco/lista) → sem loja.

Veio do wt-tuta (`tuta/rotear.py`), agora com a lista de endereços da caixa.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Integration, Marca, MarcaEmail, StoreInfo
from app.services.atendimento.lojas import _PLATAFORMA_DA_FICHA
from app.services.mail_atendimento import enderecos, suspeito
from app.services.mail_atendimento.constantes import (
    LOCAIS_DE_SITE,
    PLATAFORMA_SITE,
    SEM_LOJA_ALIAS_SEM_CADASTRO,
    SEM_LOJA_AMBIGUO,
    SEM_LOJA_ENCAMINHADO,
    SEM_LOJA_ENTRADA,
    SEM_LOJA_MARCA_AMBIGUA,
    SEM_LOJA_SEM_ALIAS,
)

_CHAVE_CACHE = "mail_atendimento_cadastro"
# A caixa do site pela parte antes do @ (sac@, atacado@, duvidas@, support@).
CAIXA_DO_LOCAL = LOCAIS_DE_SITE


def plataforma_da_ficha(valor: str | None) -> str:
    plat = " ".join((valor or "").split()).lower()
    return _PLATAFORMA_DA_FICHA.get(plat, plat)


def _nome_do_par(valor: str | None) -> str:
    """O nome da conta para o PAR ficha × integração (sem espaços nas pontas, minúsculo)."""
    return (valor or "").strip().lower()


@dataclass(frozen=True)
class Loja:
    store_info_id: UUID
    plataforma: str
    # O endereço que o cadastro quer dizer ("16tr" → 16tr@tuta.com); vazio
    # quando o cadastro é parcial.
    endereco: str
    integration_id: UUID | None
    nome: str
    # O cadastro estava sem domínio ("16tr"): ambíguo se houver outro alias igual.
    sem_dominio: bool
    # O cadastro "sac@poofy" (sem o fim do domínio): casa com sac@poofy.<…>.
    parcial: tuple[str, str] | None = None
    # A integração veio do PAR (nome, plataforma), não do FK da ficha.
    integracao_pelo_par: bool = False

    def casa(self, endereco: str | None) -> bool:
        if not endereco:
            return False
        if self.parcial is not None:
            return enderecos.casa_parcial(endereco, self.parcial)
        return endereco == self.endereco

    @property
    def descricao(self) -> str:
        return self.endereco or (f"{self.parcial[0]}@{self.parcial[1]}" if self.parcial else "")


@dataclass(frozen=True)
class CaixaMarca:
    marca_id: UUID
    slug: str
    nome: str
    tipo: str | None


@dataclass
class Cadastro:
    lojas: list[Loja] = field(default_factory=list)
    caixas: dict[str, CaixaMarca] = field(default_factory=dict)
    # slug → (id, nome, domínios)
    marcas: dict[str, tuple[UUID, str, tuple[str, ...]]] = field(default_factory=dict)
    integracoes_ativas: set[UUID] = field(default_factory=set)
    # As lojas com o e-mail do cadastro que não se lê (a saúde avisa).
    incompletas: list[tuple[UUID, str]] = field(default_factory=list)

    def e_de_loja(self, endereco: str | None) -> bool:
        """O endereço é de alguma loja do cadastro? (o filtro da caixa privada)"""
        return any(lj.casa(endereco) for lj in self.lojas)

    def enderecos_completos(self) -> set[str]:
        return {lj.endereco for lj in self.lojas if lj.endereco}

    def sem_integracao(self) -> list[Loja]:
        """As fichas com e-mail que não têm integração nem pelo FK nem pelo par (a Saúde)."""
        return [lj for lj in self.lojas if lj.integration_id is None]


def _dominios_da_marca(m: Marca) -> tuple[str, ...]:
    saida: list[str] = []
    for bruto in (m.dominio_br, m.dominio):
        for parte in (bruto or "").replace(";", ",").split(","):
            d = parte.strip().lower().removeprefix("https://").removeprefix("http://")
            d = d.removeprefix("www.").split("/")[0].strip(".")
            if d and "." in d and d not in saida:
                saida.append(d)
    return tuple(saida)


def integracao_pelo_par(
    pares: dict[tuple[str, str], list[UUID]], nome: str | None, plataforma: str
) -> UUID | None:
    """A integração do PAR (nome, plataforma) — só quando há UMA (duas = ninguém adivinha)."""
    achadas = pares.get((_nome_do_par(nome), plataforma)) or []
    return achadas[0] if len(achadas) == 1 else None


async def _pares_das_integracoes(
    session: AsyncSession,
) -> tuple[set[UUID], dict[tuple[str, str], list[UUID]]]:
    """(as integrações não arquivadas, o PAR (nome, plataforma) → ids)."""
    ativas: set[UUID] = set()
    pares: dict[tuple[str, str], list[UUID]] = {}
    for iid, nome, plat in (
        await session.execute(
            select(Integration.id, Integration.name, Integration.platform).where(
                Integration.archived_at.is_(None)
            )
        )
    ).all():
        ativas.add(iid)
        chave = (_nome_do_par(nome), str(getattr(plat, "value", plat) or "").lower())
        pares.setdefault(chave, []).append(iid)
    return ativas, pares


async def integracao_da_ficha(session: AsyncSession, ficha: StoreInfo) -> tuple[UUID | None, bool]:
    """A integração da ficha (FK ativo, senão o par) → (id, veio_do_par). Sem → (None, False)."""
    ativas, pares = await _pares_das_integracoes(session)
    if ficha.integration_id is not None and ficha.integration_id in ativas:
        return ficha.integration_id, False
    pelo_par = integracao_pelo_par(pares, ficha.account_name, plataforma_da_ficha(ficha.platform))
    return pelo_par, pelo_par is not None


async def cadastro(session: AsyncSession) -> Cadastro:
    """Lojas (com e-mail), caixas das marcas e marcas — cache por sessão."""
    cache = session.info.get(_CHAVE_CACHE)
    if isinstance(cache, Cadastro):
        return cache
    c = Cadastro()
    marcas = (
        (
            await session.execute(
                select(Marca).where(Marca.ativo.is_(True)).order_by(Marca.slug, Marca.id)
            )
        )
        .scalars()
        .all()
    )
    por_id = {m.id: m for m in marcas}
    for m in marcas:
        c.marcas[(m.slug or "").lower()] = (m.id, m.nome, _dominios_da_marca(m))
    c.integracoes_ativas, pares = await _pares_das_integracoes(session)
    for s in (
        (
            await session.execute(
                select(StoreInfo)
                .where(
                    StoreInfo.archived_at.is_(None),
                    StoreInfo.email.is_not(None),
                    StoreInfo.email != "",
                )
                .order_by(StoreInfo.id)
            )
        )
        .scalars()
        .all()
    ):
        nome = " ".join((s.account_name or "").split()) or s.platform
        endereco = enderecos.endereco_do_cadastro(s.email)
        parcial = None if endereco else enderecos.parcial_do_cadastro(s.email)
        if not endereco and parcial is None:
            c.incompletas.append((s.id, nome))
            continue
        plataforma = plataforma_da_ficha(s.platform)
        integracao: UUID | None = None
        pelo_par = False
        if s.integration_id is not None and s.integration_id in c.integracoes_ativas:
            integracao = s.integration_id
        else:
            integracao = integracao_pelo_par(pares, s.account_name, plataforma)
            pelo_par = integracao is not None
        c.lojas.append(
            Loja(
                store_info_id=s.id,
                plataforma=plataforma,
                endereco=endereco,
                integration_id=integracao,
                nome=nome,
                sem_dominio="@" not in (s.email or ""),
                parcial=parcial,
                integracao_pelo_par=pelo_par,
            )
        )
    for e in (await session.execute(select(MarcaEmail).order_by(MarcaEmail.email))).scalars().all():
        m = por_id.get(e.marca_id)
        endereco = enderecos.normalizar(e.email)
        if m is None or not endereco:
            continue
        c.caixas[endereco] = CaixaMarca(marca_id=m.id, slug=m.slug, nome=m.nome, tipo=e.tipo)
    session.info[_CHAVE_CACHE] = c
    return c


def esquecer(session: AsyncSession) -> None:
    session.info.pop(_CHAVE_CACHE, None)


@dataclass
class Rota:
    """Para onde o e-mail vai: a loja (ou o site), ou a fila "sem loja" com o porquê."""

    alias: str | None = None
    store_info_id: UUID | None = None
    integration_id: UUID | None = None
    plataforma: str | None = None
    motivo_sem_loja: str | None = None
    # [{store_info_id, nome, plataforma, integration_id, sugestao: bool}] — e,
    # na marca ambígua, [{marca_id, marca_slug, nome, plataforma: 'site'}].
    sugestoes: list[dict] = field(default_factory=list)
    # O nome da ficha (a conversa da loja SEM integração leva este nome).
    loja_nome: str | None = None
    # Site (RF6).
    marca_id: UUID | None = None
    marca_slug: str | None = None
    marca_nome: str | None = None
    tipo_caixa: str | None = None

    @property
    def site(self) -> bool:
        return self.plataforma == PLATAFORMA_SITE

    @property
    def tem_loja(self) -> bool:
        return self.motivo_sem_loja is None and (
            self.integration_id is not None or self.store_info_id is not None or self.site
        )

    @property
    def so_ficha(self) -> bool:
        """A loja é a FICHA do cadastro, sem integração (a conversa fica sem integração)."""
        return (
            self.motivo_sem_loja is None
            and not self.site
            and self.integration_id is None
            and self.store_info_id is not None
        )


def _sugestao(loja: Loja, *, confirmar: bool = False) -> dict:
    return {
        "store_info_id": str(loja.store_info_id),
        "nome": loja.nome,
        "plataforma": loja.plataforma,
        "integration_id": str(loja.integration_id) if loja.integration_id else None,
        "sugestao": confirmar,
    }


def _sugestao_marca(slug: str, cad: Cadastro) -> dict:
    mid, nome, _ = cad.marcas[slug]
    return {
        "store_info_id": None,
        "marca_id": str(mid),
        "marca_slug": slug,
        "nome": nome,
        "plataforma": PLATAFORMA_SITE,
        "integration_id": None,
        "sugestao": True,
    }


def nossos_do_email(*, recebeu: list[str], aliases: set[str]) -> list[str]:
    """Os endereços DA CAIXA que receberam o e-mail, na ordem (delivered_to, Para, Cc)."""
    saida: list[str] = []
    for e in recebeu:
        if e in aliases and e not in saida:
            saida.append(e)
    return saida


def marcas_do_dominio(endereco: str | None, cad: Cadastro) -> list[str]:
    """Os slugs das marcas do domínio do endereço, em ordem FIXA (pelo slug)."""
    dom = enderecos.dominio(endereco)
    if not dom:
        return []
    return sorted(
        slug
        for slug, (_mid, _nome, dominios) in cad.marcas.items()
        if any(dom == d or dom.endswith("." + d) for d in dominios)
    )


def e_caixa_de_site(endereco: str | None, cad: Cadastro, *, marca_pasta: str | None = None) -> bool:
    """O endereço é uma CAIXA DE SITE (RF6)? `marca_emails`, ou sac@/atacado@/duvidas@/
    support@ num domínio de marca (ou numa pasta "*marca" de marca do cadastro)."""
    if not endereco:
        return False
    if endereco in cad.caixas:
        return True
    if enderecos.e_do_tuta(endereco) or enderecos.local(endereco) not in LOCAIS_DE_SITE:
        return False
    return bool(marcas_do_dominio(endereco, cad)) or (
        marca_pasta is not None and marca_pasta in cad.marcas
    )


def _rota_do_site(
    alias: str | None, cad: Cadastro, *, marca_pasta: str | None, caixa_pasta: str | None
) -> Rota | None:
    """A caixa de site (RF6) → o chamado da marca; o resto do domínio da marca não é site."""
    if not alias:
        return None
    if alias in cad.caixas:
        cx = cad.caixas[alias]
        return Rota(
            alias=alias,
            plataforma=PLATAFORMA_SITE,
            marca_id=cx.marca_id,
            marca_slug=cx.slug,
            marca_nome=cx.nome,
            tipo_caixa=cx.tipo or LOCAIS_DE_SITE.get(enderecos.local(alias)),
        )
    if not e_caixa_de_site(alias, cad, marca_pasta=marca_pasta):
        return None
    tipo = LOCAIS_DE_SITE.get(enderecos.local(alias)) or caixa_pasta
    do_dominio = marcas_do_dominio(alias, cad)
    # A pasta "*marca" (de marca que EXISTE) diz a marca antes do domínio.
    if marca_pasta is not None and marca_pasta in cad.marcas:
        slug = marca_pasta
    elif len(do_dominio) == 1:
        slug = do_dominio[0]
    elif do_dominio:
        # O mesmo domínio em duas marcas (charlots.com.br): a ponte tenta o
        # protocolo; senão a pessoa escolhe (nunca pela ordem da tabela).
        return Rota(
            alias=alias,
            plataforma=PLATAFORMA_SITE,
            motivo_sem_loja=SEM_LOJA_MARCA_AMBIGUA,
            tipo_caixa=tipo,
            sugestoes=[_sugestao_marca(s, cad) for s in do_dominio],
        )
    else:
        return None
    mid, nome, _ = cad.marcas[slug]
    return Rota(
        alias=alias,
        plataforma=PLATAFORMA_SITE,
        marca_id=mid,
        marca_slug=slug,
        marca_nome=nome,
        tipo_caixa=tipo,
    )


def marca_pelo_protocolo(rota: Rota, slugs_da_letra: tuple[str, ...], cad: Cadastro) -> Rota:
    """A marca ambígua resolvida pela letra do protocolo (C → charlots-park)."""
    if rota.motivo_sem_loja != SEM_LOJA_MARCA_AMBIGUA:
        return rota
    candidatas = [s.get("marca_slug") for s in rota.sugestoes]
    for slug in slugs_da_letra:
        if slug in candidatas and slug in cad.marcas:
            mid, nome, _ = cad.marcas[slug]
            return Rota(
                alias=rota.alias,
                plataforma=PLATAFORMA_SITE,
                marca_id=mid,
                marca_slug=slug,
                marca_nome=nome,
                tipo_caixa=rota.tipo_caixa,
            )
    return rota


def _lojas_do_alias(alias: str, cad: Cadastro, aliases: set[str]) -> tuple[list[Loja], bool]:
    """As lojas cujo cadastro aponta para o alias; True = ambíguo (16tr sem domínio, C4)."""
    achadas = [lj for lj in cad.lojas if lj.casa(alias)]
    ambiguo = any(
        lj.sem_dominio and lj.endereco and enderecos.ambiguo_no_tuta(lj.endereco, aliases)
        for lj in achadas
    )
    return achadas, ambiguo


def _pela_plataforma(alias: str, plataforma: str, cad: Cadastro, aliases: set[str]) -> Rota:
    """A loja do alias NESTA plataforma (a da pasta, ou a do remetente oficial)."""
    lojas, ambiguo = _lojas_do_alias(alias, cad, aliases)
    da_plataforma = [lj for lj in lojas if lj.plataforma == plataforma]
    if ambiguo and da_plataforma:
        return Rota(
            alias=alias,
            motivo_sem_loja=SEM_LOJA_AMBIGUO,
            sugestoes=[_sugestao(lj) for lj in da_plataforma],
        )
    if not da_plataforma:
        return Rota(
            alias=alias,
            motivo_sem_loja=SEM_LOJA_ALIAS_SEM_CADASTRO,
            # O alias é de loja de OUTRA plataforma: mostra, sem escolher.
            sugestoes=[_sugestao(lj, confirmar=True) for lj in lojas],
        )
    if len(da_plataforma) > 1:
        return Rota(
            alias=alias,
            motivo_sem_loja=SEM_LOJA_AMBIGUO,
            sugestoes=[_sugestao(lj) for lj in da_plataforma],
        )
    return _com_loja(alias, da_plataforma[0], cad)


def _juntar(resultados: list[Rota], nossos: list[str], cad: Cadastro) -> Rota:
    com_loja = [r for r in resultados if r.motivo_sem_loja is None]
    lojas_distintas = {r.store_info_id for r in com_loja}
    if len(lojas_distintas) == 1:
        return com_loja[0]
    if len(lojas_distintas) > 1:
        # Dois aliases nossos de lojas diferentes no mesmo e-mail: a pessoa escolhe.
        por_loja = {lj.store_info_id: lj for lj in cad.lojas}
        sugestoes = [
            _sugestao(por_loja[r.store_info_id]) for r in com_loja if r.store_info_id in por_loja
        ]
        return Rota(alias=nossos[0], motivo_sem_loja=SEM_LOJA_AMBIGUO, sugestoes=sugestoes)
    # Nenhum alias deu loja: o primeiro motivo (o do Para) é o que se mostra.
    return resultados[0]


def rotear(
    *,
    recebeu: list[str],
    de: str | None,
    enviado_por_nos: bool,
    plataforma_pasta: str | None,
    marca_pasta: str | None,
    caixa_pasta: str | None,
    aliases: set[str],
    cad: Cadastro,
) -> Rota:
    """A loja do e-mail (PURA sobre o cadastro já lido).

    `recebeu`: o delivered_to, o Para e o Cc, nessa ordem (no enviado, o De);
    `aliases`: os endereços da caixa (o principal + os aliases do config).
    """
    if enviado_por_nos:
        nossos = [de] if de else []
    else:
        nossos = nossos_do_email(recebeu=recebeu, aliases=aliases)
    sem_pasta_de_plataforma = plataforma_pasta in (None, PLATAFORMA_SITE)
    # O aviso OFICIAL de uma plataforma para um alias que é login de loja dela
    # vai para a LOJA — antes do site (o sac@ da marca que é também o login da
    # loja) e mesmo na Entrada.
    pelo_remetente = (
        None if enviado_por_nos else suspeito.plataforma_do_dominio(enderecos.dominio(de))
    )
    if sem_pasta_de_plataforma and pelo_remetente and nossos:
        da_plataforma = [
            alias
            for alias in nossos
            if any(lj.plataforma == pelo_remetente for lj in cad.lojas if lj.casa(alias))
        ]
        if da_plataforma:
            return _juntar(
                [_pela_plataforma(a, pelo_remetente, cad, aliases) for a in da_plataforma],
                da_plataforma,
                cad,
            )
    # Site (RF6): a caixa de site (marca_emails, sac@/atacado@/duvidas@ da marca).
    if sem_pasta_de_plataforma:
        for alias in nossos:
            rota = _rota_do_site(
                alias,
                cad,
                marca_pasta=marca_pasta if plataforma_pasta == PLATAFORMA_SITE else None,
                caixa_pasta=caixa_pasta,
            )
            if rota is not None:
                return rota
    if not nossos:
        de_fora = [e for e in recebeu if e and not enderecos.e_do_tuta(e)]
        if not enviado_por_nos and any(marcas_do_dominio(e, cad) for e in de_fora):
            return Rota(motivo_sem_loja=SEM_LOJA_ENCAMINHADO)
        return Rota(motivo_sem_loja=SEM_LOJA_SEM_ALIAS)
    resultados: list[Rota] = []
    for alias in nossos:
        if plataforma_pasta and plataforma_pasta != PLATAFORMA_SITE:
            resultados.append(_pela_plataforma(alias, plataforma_pasta, cad, aliases))
            continue
        # Entrada/Enviados: o alias tem de ser de UMA loja de UMA plataforma.
        lojas, ambiguo = _lojas_do_alias(alias, cad, aliases)
        if len(lojas) == 1 and not ambiguo:
            resultados.append(_com_loja(alias, lojas[0], cad))
            continue
        sugestoes = [_sugestao(lj) for lj in lojas]
        if pelo_remetente:
            for s in sugestoes:
                if s["plataforma"] == pelo_remetente:
                    s["sugestao"] = True
        resultados.append(
            Rota(
                alias=alias,
                motivo_sem_loja=SEM_LOJA_ENTRADA if lojas else SEM_LOJA_ALIAS_SEM_CADASTRO,
                sugestoes=sugestoes,
            )
        )
    return _juntar(resultados, nossos, cad)


def _com_loja(alias: str, loja: Loja, cad: Cadastro) -> Rota:
    """A loja achada: com a integração (FK ativo ou par) ou só a ficha (sem integração)."""
    integracao = (
        loja.integration_id
        if loja.integration_id is not None and loja.integration_id in cad.integracoes_ativas
        else None
    )
    return Rota(
        alias=alias,
        store_info_id=loja.store_info_id,
        integration_id=integracao,
        plataforma=loja.plataforma,
        loja_nome=loja.nome,
    )
