"""Integrar empresa do grupo na NFE.io (Emissão de Serviço › Empresas, 01/10/2026).

Eduardo: "algumas empresas nossas não estão integradas no nfe.io, precisa
integrar, aí podemos deixar lá em emissão de serviços - empresas, e com filtro
para ver só os pendentes".

O botão "Integrar na NFE.io" (ou "Completar integração") faz, nesta ordem:

1. Procura a empresa na NFE.io (pelo id ligado ou pela lista + CNPJ) — só GET.
   CNPJ que já existe lá NÃO é criado de novo: só se completa o que falta.
2. Cria a empresa (POST /v2/companies) com os dados da Receita conferidos na tela.
3. Liga no DaVinci JÁ (grava `nfeio_company_id` + commit) — se algo falhar
   depois, a empresa fica ligada e "incompleta", e "Completar integração"
   retoma sem criar outra.
4. Manda o certificado A1 guardado em Cadastros › Empresas (arquivo e senha
   saem do servidor só para a NFE.io; nunca para a tela, log, histórico ou banco).
5. Cadastra a Inscrição Municipal, sempre em TESTE (Development): a NFE.io
   cria toda IM assim e passar para Produção (nota real) é da contabilidade,
   no painel da NFE.io, depois de conferir a inscrição. Aqui não há PUT.
6. Relê tudo com `empresas.ligar`.

Nenhum POST repete (timeout → lista de novo pelo CNPJ, na v1 E na v2: quem
cria é a v2 e a v1 não garante mostrar na hora a empresa nova). Empresa já
ligada nunca é criada de novo: se o id ligado sumiu, para e pede conferência.
Uma trava por empresa no Redis (com dono, renovada antes de cada POST) impede
dois cliques ao mesmo tempo. E nada disso roda fora do servidor
oficial liberado (`ambiente.integrar_liberado`): o .env do localhost tem a
chave REAL da NFE.io.
"""

from __future__ import annotations

import re
import secrets
import unicodedata
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import date
from typing import TYPE_CHECKING, Any
from uuid import UUID

import structlog
from email_validator import EmailNotValidError, validate_email
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Company
from app.models.company_certificate import CompanyCertificate
from app.models.nfse import CompanyFiscal
from app.security.cipher import decrypt_bytes
from app.services.nfse import ambiente, certificado, chamadas, empresas, nfeio, receita
from app.services.nfse import texto as T  # noqa: N812
from app.services.nfse.erros import NfseError, explicar
from app.services.nfse.municipios import nome_do_codigo

if TYPE_CHECKING:
    from app.schemas.nfse import DadosIntegrar

logger = structlog.get_logger()

REGIMES = ("SimplesNacional", "LucroPresumido", "LucroReal", "MicroempreendedorIndividual")
REGIME_ESPECIAL = {
    "SimplesNacional": "MicroempresarioEmpresaPequenoPorte",
    "MicroempreendedorIndividual": "MicroempreendedorIndividual",
}  # resto "Nenhum"
AMBIENTE_NOVA, SERIE_RPS = "Development", "IO"
# 01/10/2026 (revisão): 300 s era menos que o pior caso só das leituras antes do
# 1º POST (cada GET tenta 3x com até 75 s). 900 s + renovação antes de cada POST.
TRAVA_SEGUNDOS = 900
SAO_PAULO = "3550308"
AVISO_CERTIFICADO_DIAS = 30

# Código Concla da natureza jurídica (Receita) → nome no enum da NFE.io.
NATUREZA_POR_CODIGO = {
    "2011": "EmpresaPublica",
    "2038": "SociedadeEconomiaMista",
    "2046": "SociedadeAnonimaAberta",
    "2054": "SociedadeAnonimaFechada",
    "2062": "SociedadeEmpresariaLimitada",
    "2070": "SociedadeEmpresariaEmNomeColetivo",
    "2089": "SociedadeEmpresariaEmComanditaSimples",
    "2097": "SociedadeEmpresariaEmComanditaporAcoes",
    "2127": "SociedadeemContaParticipacao",
    "2135": "Empresario",
    "2143": "Cooperativa",
    "2151": "ConsorcioSociedades",
    "2160": "GrupoSociedades",
    "2232": "SociedadeSimplesPura",
    "2240": "SociedadeSimplesLimitada",
    "2259": "SociedadeSimplesEmNomeColetivo",
    "2267": "SociedadeSimplesEmComanditaSimples",
    "2305": "EireliNaturezaEmpresaria",
    "2313": "EireliNaturezaSimples",
    "2321": "SociedadeUnipessoaldeAdvogados",
    "2330": "CooperativaDeConsumo",
    "2348": "EmpresaSimplesDeInovacao",
}
# Enum COMPLETO de municipalTax.legalNature (OpenAPI da v2, baixado em 01/10/2026).
LEGAL_NATURE_NFEIO: tuple[str, ...] = (
    "None",
    "EmpresaPublica",
    "SociedadeEconomiaMista",
    "SociedadeAnonimaAberta",
    "SociedadeAnonimaFechada",
    "SociedadeEmpresariaLimitada",
    "SociedadeEmpresariaEmNomeColetivo",
    "SociedadeEmpresariaEmComanditaSimples",
    "SociedadeEmpresariaEmComanditaporAcoes",
    "SociedadeemContaParticipacao",
    "Empresario",
    "Cooperativa",
    "ConsorcioSociedades",
    "GrupoSociedades",
    "SociedadeEstrangeiraNoBrasil",
    "EmpresaBinacionalArgentinoBrasileira",
    "EmpresaDomiciliadaExterior",
    "ClubeFundoInvestimento",
    "SociedadeSimplesPura",
    "SociedadeSimplesLimitada",
    "SociedadeSimplesEmNomeColetivo",
    "SociedadeSimplesEmComanditaSimples",
    "EmpresaBinacional",
    "ConsorcioEmpregadores",
    "ConsorcioSimples",
    "EireliNaturezaEmpresaria",
    "EireliNaturezaSimples",
    "SociedadeUnipessoaldeAdvogados",
    "CooperativaDeConsumo",
    "EmpresaSimplesDeInovacao",
    "InvestidorNaoResidente",
    "ServicoNotarial",
    "FundacaoPrivada",
    "ServicoSocialAutonomo",
    "CondominioEdilicio",
    "ComissaoConciliacaoPrevia",
    "EntidadeMediacaoArbitragem",
    "PartidoPolitico",
    "EntidadeSindical",
    "EstabelecimentoBrasilFundacaoAssociacaoEstrangeiras",
    "FundacaoAssociacaoDomiciliadaExterior",
    "OrganizacaoReligiosa",
    "ComunidadeIndigena",
    "FundoPrivado",
    "OrgaoDirecaoNacionalPartidoPolitico",
    "OrgaoDirecaoRegionalPartidoPolitico",
    "OrgaoDirecaoLocalPartidoPolitico",
    "ComiteFinanceiroDePartidoPolitico",
    "FrentePlebiscitariaOuReferendaria",
    "OrganizacaoSocial",
    "DemaisCondominios",
    "PlanoBeneficiosPrevidenciaComplementarFechada",
    "AssociacaoPrivada",
    "EmpresaIndividualImobiliaria",
    "SeguradoEspecial",
    "ContribuinteIndividual",
    "CandidatoCargoPoliticoEletivo",
    "Leiloeiro",
    "ProdutorRural",
    "OrganizacaoInternacional",
    "RepresentacaoDiplomaticaEstrangeira",
    "OutrasInstituicoesExtraterritoriais",
)

# Código de erro da NFE.io no cadastro da empresa → campo da tela.
CAMPO_DO_CODIGO = {
    "40017": "razao_social",
    "40020": "razao_social",
    "40022": "endereco.logradouro",
    "40023": "endereco.numero",
    "40024": "endereco.bairro",
    "40027": "endereco.cmun_ibge",
    "40028": "endereco.cmun_ibge",
    "40029": "endereco.cmun_ibge",
    "40030": "endereco.cep",
}
CODIGOS_CNPJ = ("40031", "40032")

MSG_RECEITA_FORA = (
    "A Receita (BrasilAPI) não respondeu: preencha o endereço à mão ou feche e abra de novo."
)
MSG_NFEIO_FORA = (
    "Não deu para consultar a NFE.io agora; ao confirmar, o DaVinci procura de novo pelo CNPJ."
)
MSG_TESTE = (
    "A empresa entra em TESTE na NFE.io (notas simuladas). Para emitir nota real, a "
    "contabilidade confere a inscrição municipal e passa para Produção no painel da NFE.io."
)
MSG_INCERTA = (
    "A NFE.io não respondeu se criou a empresa. Espere 1 minuto e clique em Integrar de novo: "
    "o DaVinci procura pelo CNPJ antes e não cria em dobro."
)
MSG_RELEITURA = (
    "A empresa ficou ligada, mas a NFE.io não devolveu os dados agora. Use Atualizar na "
    "gaveta da empresa em alguns minutos."
)
MSG_CERT_INCERTO = (
    "A NFE.io não respondeu se recebeu o certificado. Clique em Completar integração em "
    "alguns minutos (mandar de novo só substitui)."
)
MSG_IM_INCERTA = (
    "A NFE.io não respondeu se cadastrou a inscrição. Completar integração confere antes de "
    "cadastrar de novo."
)
MSG_IM_SEM_RESPOSTA = "A NFE.io não respondeu; clique em Completar integração em alguns minutos."
MSG_SEM_TRAVA = "Não deu para começar a integração agora. Tente de novo em alguns minutos."
MSG_TRAVA_PERDIDA = (
    "A integração demorou demais e parou aqui por segurança. Clique em Completar integração "
    "em alguns minutos."
)
MSG_RECEITA_NAO_ACHOU = (
    "A Receita não achou este CNPJ: confira o CNPJ em Cadastros › Empresas antes de integrar."
)
MSG_NFEIO_FORA_LIGADA = (
    "Não deu para consultar a NFE.io agora (nada foi feito). Feche e abra de novo em alguns "
    "minutos."
)
MSG_LIGADA_SUMIU = (
    "A empresa ligada não foi achada na NFE.io; confira no painel da NFE.io antes de integrar "
    "de novo (o DaVinci não cria outra)."
)

BLOQUEIOS_CERT = {
    "sem_certificado_guardado": (
        "Não há certificado digital (A1) guardado no DaVinci para esta empresa. Peça ao "
        "administrador para subir o arquivo .pfx com a senha em Cadastros › Empresas."
    ),
    "certificado_sem_senha": (
        "O certificado guardado não tem a senha. O administrador guarda a senha em "
        "Cadastros › Empresas."
    ),
}


# --- trava por empresa (Redis) -------------------------------------------------------


async def _redis() -> Any:
    from app.redis_client import redis

    return redis


# Solta/renova só se a trava ainda for DESTE pedido (o valor é um token).
_SOLTAR = (
    "if redis.call('get', KEYS[1]) == ARGV[1] then "
    "return redis.call('del', KEYS[1]) else return 0 end"
)
_RENOVAR = (
    "if redis.call('get', KEYS[1]) == ARGV[1] then "
    "return redis.call('expire', KEYS[1], ARGV[2]) else return 0 end"
)


@dataclass
class Trava:
    r: Any
    chave: str
    token: str = field(repr=False)

    async def renovar(self) -> bool:
        """Antes de cada POST: a trava ainda é nossa? (renova o prazo). False =
        venceu e talvez outro pedido já entrou — não mandar nada."""
        try:
            return bool(await self.r.eval(_RENOVAR, 1, self.chave, self.token, TRAVA_SEGUNDOS))
        except Exception as e:  # noqa: BLE001 — sem Redis não dá pra garantir: não manda
            logger.warning("nfse_integrar_trava_sem_renovar", erro=type(e).__name__)
            return False


@asynccontextmanager
async def trava(company_id: UUID) -> AsyncIterator[Trava]:
    """Uma integração por empresa de cada vez. Redis fora do ar = não começa
    (falha fechado: sem trava dois cliques poderiam criar em dobro). A chave
    guarda um token: só quem pegou solta (ou renova)."""
    chave = f"davinci:nfse:integrar:{company_id}"
    token = secrets.token_hex(16)
    try:
        r = await _redis()
        pegou = await r.set(chave, token, nx=True, ex=TRAVA_SEGUNDOS)
    except Exception as e:  # noqa: BLE001 — qualquer falha do Redis fecha a porta
        logger.warning("nfse_integrar_sem_trava", erro=type(e).__name__)
        raise NfseError(503, "integracao_sem_trava", MSG_SEM_TRAVA) from e
    if not pegou:
        raise NfseError(
            409,
            "integracao_em_andamento",
            "Alguém está integrando esta empresa agora. Espere um minuto e atualize a tela.",
        )
    try:
        yield Trava(r, chave, token)
    finally:
        try:
            await r.eval(_SOLTAR, 1, chave, token)
        except Exception as e:  # noqa: BLE001 — expira sozinha em TRAVA_SEGUNDOS
            logger.warning("nfse_integrar_trava_presa", erro=type(e).__name__)


# --- passos ------------------------------------------------------------------------


@dataclass
class Passo:
    id: str
    titulo: str
    situacao: str
    detalhe: str | None = None
    erros: list[dict] | None = None

    def out(self) -> dict:
        return {
            "id": self.id,
            "titulo": self.titulo,
            "situacao": self.situacao,
            "detalhe": self.detalhe,
            "erros": self.erros,
        }


def _passos_out(passos: list[Passo], *, fim: bool = False) -> list[dict]:
    """`fim`: o que ainda estava para fazer não chegou a rodar."""
    out = []
    for p in passos:
        d = p.out()
        if fim and d["situacao"] == "fazer":
            d["situacao"] = "nao_feito"
        out.append(d)
    return out


def _passos_de_criacao() -> list[Passo]:
    return [
        Passo("criar_empresa", "Criar a empresa na NFE.io", "fazer"),
        Passo("certificado", "Enviar o certificado digital guardado no DaVinci", "fazer"),
        Passo("inscricao", "Cadastrar a inscrição municipal (em TESTE)", "fazer"),
        Passo("ligar", "Ligar no DaVinci e ler os dados de volta", "fazer"),
    ]


def _por_id(passos: list[Passo]) -> dict[str, Passo]:
    return {p.id: p for p in passos}


# --- dados -------------------------------------------------------------------------


def _apelido(c: Company) -> str:
    return c.apelido or c.razao_social


def cnpj_ok(cnpj: str | None) -> bool:
    """Só CNPJ numérico válido: a NFE.io v2 pede `federalTaxNumber` número."""
    doc = T.normalizar_documento(cnpj)
    return doc.isdigit() and T.cnpj_valido(doc)


def msg_cnpj_invalido(c: Company) -> str:
    return (
        f"A {_apelido(c)} não tem CNPJ brasileiro válido ({c.cnpj or 'sem CNPJ'}). A NFE.io "
        "só cadastra empresa com CNPJ: corrija em Cadastros › Empresas."
    )


def _sem_acento(txt: str | None) -> str:
    s = "".join(
        ch for ch in unicodedata.normalize("NFKD", txt or "") if not unicodedata.combining(ch)
    )
    return re.sub(r"[^A-Z0-9]", "", s.upper())


def normalizar_im(v: str | None) -> str | None:
    if v is None:
        return None
    t = re.sub(r"[.\-/\s]", "", str(v)).upper()
    return t or None


def _regime_da_receita(rec: Mapping[str, Any]) -> tuple[str | None, str]:
    if rec.get("mei") is True:
        return "MicroempreendedorIndividual", "a Receita diz que é MEI"
    if rec.get("simples") is True:
        return "SimplesNacional", "a Receita diz que é do Simples Nacional"
    forma = str(rec.get("regime_tributario_receita") or "").upper()
    ano = rec.get("regime_tributario_ano")
    quando = f" ({ano})" if ano else ""
    if "PRESUMIDO" in forma:
        return "LucroPresumido", f"a Receita diz {forma}{quando}"
    if "REAL" in forma:
        return "LucroReal", f"a Receita diz {forma}{quando}"
    return None, "a Receita não informou: escolha com a contabilidade"


def _cortar(v: Any, n: int) -> str | None:
    return str(v)[:n].strip() or None if v else None


def sugestao(
    c: Company, f: CompanyFiscal | None, rec: Mapping[str, Any] | None
) -> tuple[dict, dict]:
    """(dados para o formulário, sugestões/motivos). Tudo vem da Receita;
    a pessoa confere na tela."""
    rec = rec or {}
    end = rec.get("endereco") or {}
    if rec:
        regime, motivo = _regime_da_receita(rec)
    else:
        regime, motivo = None, "a Receita não respondeu: escolha com a contabilidade"
    cod = rec.get("natureza_juridica_codigo")
    natureza = NATUREZA_POR_CODIGO.get(cod or "")
    natureza_texto = None
    if natureza:
        natureza_texto = f"{cod} · {rec.get('natureza_juridica') or natureza}"
    dados = {
        "razao_social": rec.get("razao_social") or c.razao_social,
        "nome_fantasia": rec.get("nome_fantasia"),
        "regime": regime,
        "natureza_juridica": natureza,
        # Cortados no tamanho que a tela aceita (a Receita às vezes manda complemento longo).
        "endereco": {
            "logradouro": _cortar(end.get("logradouro"), 120),
            "numero": _cortar(end.get("numero"), 20),
            "complemento": _cortar(end.get("complemento"), 60),
            "bairro": _cortar(end.get("bairro"), 60),
            "cep": end.get("cep"),
            "cmun_ibge": end.get("cmun_ibge"),
            "municipio_nome": end.get("municipio_nome"),
            "uf": end.get("uf"),
        },
        "inscricao_municipal": (f.nfeio_im if f is not None else None) or None,
        "email": rec.get("email") or (f.email if f is not None else None),
    }
    return dados, {"regime_motivo": motivo, "natureza_texto": natureza_texto}


def _cidade_do_codigo(cmun: str | None) -> dict | None:
    oficial = nome_do_codigo(cmun)
    if oficial is None:
        return None
    return {"code": cmun, "name": oficial[0], "state": oficial[1]}


def cidade_da_nfeio(achada: Mapping[str, Any] | None) -> dict | None:
    """Cidade do endereço da empresa na NFE.io ({code, name, state}) ou None."""
    if not achada:
        return None
    end = achada.get("address") if isinstance(achada.get("address"), Mapping) else {}
    cid = end.get("city") if isinstance(end.get("city"), Mapping) else {}
    code = str(cid.get("code") or "").strip()
    if not code:
        return None
    oficial = nome_do_codigo(code)
    return {
        "code": code,
        "name": str(cid.get("name") or (oficial[0] if oficial else "")),
        "state": str(end.get("state") or (oficial[1] if oficial else "")).upper()[:2],
    }


def _regime_da_nfeio(achada: Mapping[str, Any] | None) -> str | None:
    r = str((achada or {}).get("taxRegime") or "").strip()
    return r if r and r != "None" else None


def corpo_empresa(c: Company, d: DadosIntegrar) -> dict:
    """Corpo do POST /v2/companies. Nunca manda `accountId`."""
    e = d.endereco
    oficial = nome_do_codigo(e.cmun_ibge) or (e.municipio_nome or "", e.uf or "")
    empresa: dict[str, Any] = {"name": d.razao_social}
    if d.nome_fantasia:
        empresa["tradeName"] = d.nome_fantasia
    empresa["federalTaxNumber"] = int(T.so_digitos(c.cnpj))
    empresa["taxRegime"] = d.regime
    empresa["address"] = {
        "state": oficial[1],
        "city": {"code": e.cmun_ibge, "name": oficial[0]},
        "district": e.bairro,
        "street": e.logradouro,
        "number": e.numero or "S/N",
        "additionalInformation": e.complemento or "",
        "postalCode": T.so_digitos(e.cep),
        "country": "BRA",
    }
    return {"company": empresa}


def corpo_inscricao(d: DadosIntegrar, cidade: Mapping[str, Any], regime: str | None) -> dict:
    """Corpo do POST /v2/companies/{id}/municipaltaxes. Sempre em TESTE; sem
    login/senha da prefeitura, alíquota, determinações nem `accountId`."""
    im: dict[str, Any] = {
        "city": {
            "code": cidade["code"],
            "name": cidade["name"],
            "state": cidade["state"],
            "country": "BRA",
        },
        "taxNumber": normalizar_im(d.inscricao_municipal),
        "environment": AMBIENTE_NOVA,
        "specialTaxRegime": REGIME_ESPECIAL.get(regime or "", "Nenhum"),
        "legalNature": d.natureza_juridica,
    }
    if d.email:
        im["email"] = d.email
    im["rpsSerialNumber"] = SERIE_RPS
    im["rpsNumber"] = 1
    im["lastRpsSent"] = 0
    return {"municipalTax": im}


def validar(
    passos: list[Passo], d: DadosIntegrar, c: Company, *, cidade: Mapping | None = None
) -> None:
    """Confere só os campos dos passos que vão acontecer. `cidade`: a da
    empresa na NFE.io (modo completar)."""
    p = _por_id(passos)
    campos: dict[str, str] = {}
    e = d.endereco
    criar = p["criar_empresa"].situacao == "fazer"
    if criar:
        rs = (d.razao_social or "").strip()
        if not 2 <= len(rs) <= 60:
            campos["razao_social"] = (
                "A razão social precisa ter de 2 a 60 letras (limite da NFE.io). Abrevie se "
                "for maior (ex.: LTDA, COM., SERV.)."
            )
        if len((d.nome_fantasia or "").strip()) > 60:
            campos["nome_fantasia"] = (
                "O nome fantasia pode ter até 60 letras (limite da NFE.io). Abrevie ou deixe vazio."
            )
        if d.regime not in REGIMES:
            campos["regime"] = "Escolha o regime tributário (confira com a contabilidade)."
        if len((e.logradouro or "").strip()) < 5:
            campos["endereco.logradouro"] = "Preencha a rua com o tipo (ex.: Rua Exemplo)."
        elif len(e.logradouro or "") > 120:
            campos["endereco.logradouro"] = "A rua pode ter até 120 letras."
        if not (e.numero or "").strip():
            campos["endereco.numero"] = "Preencha o número (ou S/N)."
        elif len(e.numero or "") > 20:
            campos["endereco.numero"] = "O número pode ter até 20 letras."
        if len(e.complemento or "") > 60:
            campos["endereco.complemento"] = "O complemento pode ter até 60 letras."
        if len((e.bairro or "").strip()) < 2:
            campos["endereco.bairro"] = "Preencha o bairro."
        elif len(e.bairro or "") > 60:
            campos["endereco.bairro"] = "O bairro pode ter até 60 letras."
        if len(T.so_digitos(e.cep)) != 8:
            campos["endereco.cep"] = "O CEP tem 8 dígitos."
        if nome_do_codigo(e.cmun_ibge) is None:
            campos["endereco.cmun_ibge"] = "Código IBGE do município não encontrado (7 dígitos)."
    if p["inscricao"].situacao == "fazer":
        cod = (cidade or {}).get("code") if cidade else None
        if not criar and not cod:
            if nome_do_codigo(e.cmun_ibge) is None:
                campos["endereco.cmun_ibge"] = (
                    "Código IBGE do município não encontrado (7 dígitos)."
                )
            cod = e.cmun_ibge
        if criar:
            cod = e.cmun_ibge
        im = normalizar_im(d.inscricao_municipal) or ""
        if not re.fullmatch(r"[0-9A-Z]{1,20}", im):
            campos["inscricao_municipal"] = (
                "Preencha a inscrição municipal (em São Paulo, o CCM de 8 dígitos)."
            )
        elif cod == SAO_PAULO and not re.fullmatch(r"\d{8}", im):
            campos["inscricao_municipal"] = "Em São Paulo o CCM tem 8 dígitos."
        if d.natureza_juridica not in LEGAL_NATURE_NFEIO or d.natureza_juridica == "None":
            campos["natureza_juridica"] = "Escolha a natureza jurídica."
        if d.email:
            try:
                validate_email(d.email, check_deliverability=False)
            except EmailNotValidError:
                campos["email"] = "E-mail inválido."
    if campos:
        raise NfseError(
            422,
            "dados_incompletos",
            "Faltam dados para integrar: confira os campos marcados.",
            campos=campos,
            passos=_passos_out(passos),
        )


# --- certificado guardado --------------------------------------------------------------


@dataclass
class Escolha:
    cert: CompanyCertificate | None = None
    pfx: bytes | None = field(default=None, repr=False)
    senha: str | None = field(default=None, repr=False)
    validade: date | None = None
    validade_conferida: bool = False
    cnpj_confere: bool | None = None
    bloqueio: tuple[str, str] | None = None
    avisos: list[str] = field(default_factory=list)

    def esquecer(self) -> None:
        self.pfx = None
        self.senha = None


@dataclass
class _Candidato:
    cert: CompanyCertificate
    pfx: bytes = field(repr=False)
    senha: str = field(repr=False)
    info: certificado.InfoCertificado | None
    validade: date | None


def _abrir(cert: CompanyCertificate) -> _Candidato:
    try:
        pfx = decrypt_bytes(cert.blob)
        senha = decrypt_bytes(cert.password_enc or b"").decode()
    except Exception as e:  # noqa: BLE001 — chave trocada, blob corrompido...
        logger.warning("nfse_certificado_ilegivel", cert=str(cert.id), erro=type(e).__name__)
        raise NfseError(
            500,
            "certificado_ilegivel",
            "Não deu para abrir o certificado guardado. Fale com o administrador do DaVinci.",
        ) from e
    info = certificado.ler(pfx, senha)
    return _Candidato(cert, pfx, senha, info, info.validade if info else cert.expires_at)


def _bloqueio(code: str, msg: str) -> Escolha:
    return Escolha(bloqueio=(code, msg))


def _vencido(validade: date | None) -> Escolha:
    quando = f" em {validade:%d/%m/%Y}" if validade else ""
    return _bloqueio(
        "certificado_vencido",
        f"O certificado guardado venceu{quando}. Suba o certificado novo em Cadastros › Empresas.",
    )


def _outro_cnpj(cnpj: str | None) -> Escolha:
    return _bloqueio(
        "certificado_outro_cnpj",
        f"O certificado guardado é de outro CNPJ ({cnpj}). Suba o certificado desta empresa "
        "em Cadastros › Empresas.",
    )


async def escolher_certificado(
    session: AsyncSession, company_id: UUID, hoje: date, preferido: UUID | None = None
) -> Escolha:
    """O certificado guardado que vai para a NFE.io: com senha, não vencido, do
    CNPJ da empresa; o de maior validade (empate: o mais novo). O arquivo é
    aberto aqui pra conferir validade e CNPJ; se não abrir, vale a validade
    digitada e fica um aviso."""
    c = await session.get(Company, company_id)
    doc = T.normalizar_documento(c.cnpj if c is not None else None)
    certs = list(
        (
            await session.execute(
                select(CompanyCertificate)
                .where(CompanyCertificate.company_id == company_id)
                .order_by(CompanyCertificate.created_at.desc())
            )
        ).scalars()
    )
    if preferido is not None:
        escolhido = next((x for x in certs if x.id == preferido), None)
        if escolhido is None:
            raise NfseError(
                422,
                "certificado_invalido",
                "O certificado escolhido não é desta empresa. Feche e abra de novo.",
            )
        certs = [escolhido]
    if not certs:
        return _bloqueio("sem_certificado_guardado", BLOQUEIOS_CERT["sem_certificado_guardado"])
    com_senha = [x for x in certs if x.password_enc is not None]
    if not com_senha:
        return _bloqueio("certificado_sem_senha", BLOQUEIOS_CERT["certificado_sem_senha"])
    candidatos = [_abrir(x) for x in com_senha]
    nao_vencidos = [x for x in candidatos if x.validade is None or x.validade >= hoje]
    if not nao_vencidos:
        return _vencido(max(x.validade for x in candidatos if x.validade is not None))
    do_cnpj = [x for x in nao_vencidos if not (x.info and x.info.cnpj and x.info.cnpj != doc)]
    if not do_cnpj:
        return _outro_cnpj(nao_vencidos[0].info.cnpj if nao_vencidos[0].info else None)
    do_cnpj.sort(
        key=lambda x: (
            x.validade is None,
            -(x.validade.toordinal() if x.validade else 0),
            -(x.cert.created_at.timestamp() if x.cert.created_at else 0),
        )
    )
    melhor = do_cnpj[0]
    avisos: list[str] = []
    if melhor.info is None:
        avisos.append(
            "Não deu para abrir o certificado aqui para conferir a senha e a validade (vale a "
            "validade digitada no cadastro). A NFE.io confere quando receber."
        )
    if melhor.validade is not None and (melhor.validade - hoje).days <= AVISO_CERTIFICADO_DIAS:
        avisos.append(
            f"O certificado vence em {(melhor.validade - hoje).days} dias "
            f"({melhor.validade:%d/%m/%Y})."
        )
    return Escolha(
        cert=melhor.cert,
        pfx=melhor.pfx,
        senha=melhor.senha,
        validade=melhor.validade,
        validade_conferida=melhor.info is not None,
        cnpj_confere=(melhor.info.cnpj == doc) if melhor.info and melhor.info.cnpj else None,
        avisos=avisos,
    )


def _titulo_certificado(escolha: Escolha | None) -> str:
    if escolha is None or escolha.cert is None:
        return "Enviar o certificado digital guardado no DaVinci"
    vence = f", vence {escolha.validade:%d/%m/%Y}" if escolha.validade else ""
    return f"Enviar o certificado digital {escolha.cert.filename}{vence}"


def _titulo_inscricao(cidade: Mapping | None) -> str:
    if cidade and cidade.get("name"):
        return (
            f"Cadastrar a inscrição municipal em {cidade['name']}/{cidade.get('state') or ''}"
            " (em TESTE)"
        )
    return "Cadastrar a inscrição municipal (em TESTE)"


def nome_do_arquivo(filename: str | None) -> str:
    """A NFE.io só aceita nome terminando em .pfx/.p12."""
    nome = re.sub(r"[^A-Za-z0-9._-]", "_", filename or "")
    if not nome.lower().endswith((".pfx", ".p12")):
        return "certificado.pfx"
    return nome


def _mascarar(txt: Any, senha: str | None) -> Any:
    if isinstance(txt, str) and senha:
        return txt.replace(senha, "***")
    return txt


def _mascarar_msgs(msgs: list[dict], senha: str | None) -> list[dict]:
    return [{k: _mascarar(v, senha) for k, v in m.items()} for m in msgs]


def motivo_certificado(r: nfeio.Resposta, senha: str | None = None) -> str:
    """Por que a NFE.io recusou o certificado, em português. A senha nunca volta."""
    if r.incerta:
        return MSG_CERT_INCERTO
    texto = _mascarar(nfeio.texto_do_erro(r), senha) or f"HTTP {r.status}"
    baixo = texto.lower()
    codigos = set(r.codigos)
    if "password is invalid" in baixo:
        return (
            "A NFE.io recusou a senha do certificado guardado no DaVinci. Confira a senha em "
            "Cadastros › Empresas e clique em Completar integração."
        )
    if "expired" in baixo:
        return (
            "A NFE.io diz que o certificado está vencido. Suba o certificado novo em "
            "Cadastros › Empresas."
        )
    if "federal tax number is invalid" in baixo:
        return "A NFE.io diz que o certificado é de outro CNPJ."
    if "type is invalid" in baixo:
        return "O certificado não é um e-CNPJ A1."
    if "extension" in baixo:
        return "A NFE.io recusou o arquivo (precisa ser .pfx ou .p12)."
    if "null or empty" in baixo or "40034" in codigos:
        return "O certificado está sem senha guardada."
    if "not active" in baixo or "40019" in codigos:
        return "A empresa não está ativa na NFE.io."
    return f"A NFE.io recusou o certificado: {texto}"


# --- NFE.io: procurar e planejar (só GET) --------------------------------------------


def _id(e: Mapping[str, Any] | None) -> str:
    """Id da empresa na NFE.io, minúsculo (aceita `id` ou `Id`)."""
    return nfeio._campo(e or {}, "id").strip().lower()


def _ativa(e: Mapping[str, Any]) -> bool:
    return str(e.get("status") or "") != "Inactive"


async def _pelo_cnpj(cli: nfeio.ClienteNfeio, doc: str) -> tuple[bool, list[dict]]:
    """(as DUAS listagens responderam?, empresas com o CNPJ). Lista na v1 e na
    v2 (quem cria é a v2; a v1 é um adaptador e não garante mostrar na hora
    uma empresa nova ainda sem IM). Mesmo id nas duas = uma empresa só."""
    r1, v1 = await cli.listar_empresas()
    if r1.status in (401, 403):
        empresas._checar(r1)
    r2, v2 = await cli.listar_empresas_v2()
    if r2.status in (401, 403):
        empresas._checar(r2)
    iguais: dict[str, dict] = {}
    for e in [*v1, *v2]:
        e = nfeio.chaves_camel(e)
        if empresas.cnpj_da_nfeio(e) == doc and _id(e):
            iguais.setdefault(_id(e), e)
    return r1.ok and r2.ok, list(iguais.values())


async def procurar_na_nfeio(
    session: AsyncSession, c: Company, f: CompanyFiscal | None, cli: nfeio.ClienteNfeio
) -> dict | None:
    """A empresa na NFE.io (pelo id ligado ou pelo CNPJ), ou None = não existe
    e pode criar. Só GET. Empresa JÁ LIGADA nunca devolve None: se o id ligado
    sumiu e o CNPJ não aparece, para com 409 (nunca criar outra por cima)."""
    doc = T.normalizar_documento(c.cnpj)
    ligada = (f.nfeio_company_id or "") if f is not None else ""
    if ligada:
        e = await empresas.empresa_por_id(cli, ligada)
        if e is not None:
            if empresas.cnpj_da_nfeio(e) != doc:
                raise NfseError(
                    422,
                    "cnpj_diferente",
                    f"A empresa ligada na NFE.io ({empresas.nome_da_nfeio(e)}, CNPJ "
                    f"{empresas.cnpj_da_nfeio(e)}) não é {_apelido(c)} (CNPJ {c.cnpj}). "
                    "Ligue a certa pela gaveta da empresa.",
                )
            return e
    listou, iguais = await _pelo_cnpj(cli, doc)
    if not listou:
        # Busca anti-duplicata pela metade não serve: sem ela, não cria nada.
        raise NfseError(502, "nfeio_sem_resposta", empresas.MSG_SEM_RESPOSTA)
    ativas = [e for e in iguais if _ativa(e)]
    if len(ativas) > 1:
        raise NfseError(
            409,
            "nfeio_cnpj_duplicado",
            "Há mais de uma empresa com este CNPJ na NFE.io: ligue pela gaveta da empresa "
            "(Colar link da NFE.io).",
        )
    if not ativas:
        if iguais:
            raise NfseError(
                409,
                "nfeio_empresa_inativa",
                "Na NFE.io existe uma empresa com este CNPJ, mas desativada. Reative no painel "
                "da NFE.io e use Procurar pelo CNPJ na gaveta.",
            )
        if ligada:
            raise NfseError(409, "nfeio_ligada_sumiu", MSG_LIGADA_SUMIU)
        return None
    e = dict(ativas[0])
    outra = await empresas._outra_ligada(session, c.id, _id(e))
    if outra is not None:
        raise NfseError(
            409,
            "ja_ligada",
            f"Essa empresa da NFE.io já está ligada a {outra.apelido or outra.razao_social}.",
        )
    return e


def _tem_certificado(achada: Mapping[str, Any]) -> bool:
    """01/10/2026 (revisão): a data "0001-01-01" (vazio do .NET) não conta."""
    cert = achada.get("certificate") if isinstance(achada.get("certificate"), Mapping) else {}
    st = str(cert.get("status") or "")
    data = empresas._data(cert.get("expiresOn") or cert.get("validUntil"))
    return bool((st and st != "None") or data)


async def planejar(
    session: AsyncSession,
    c: Company,
    f: CompanyFiscal | None,
    cli: nfeio.ClienteNfeio,
    achada: Mapping[str, Any] | None,
) -> list[Passo]:
    """O que vai acontecer. Só GET (as Inscrições Municipais da empresa achada)."""
    if achada is None:
        return _passos_de_criacao()
    nid = _id(achada)
    cert = achada.get("certificate") if isinstance(achada.get("certificate"), Mapping) else {}
    ri, ims = await cli.inscricoes_municipais(nid)
    if ri.status in (401, 403):
        raise NfseError(503, "chave_nfeio", nfeio.CHAVE_RECUSADA)
    if not ri.ok:
        raise NfseError(
            502,
            "nfeio_sem_resposta",
            "A NFE.io não respondeu agora (nada foi feito). Tente de novo em alguns minutos.",
        )
    cidade = cidade_da_nfeio(achada)
    return [
        Passo("criar_empresa", "A empresa já existe na NFE.io (não criamos outra)", "pular"),
        (
            Passo("certificado", "O certificado já está ativo na NFE.io", "pular")
            if cert.get("status") == "Active"
            else Passo("certificado", _titulo_certificado(None), "fazer")
        ),
        (
            Passo("inscricao", _titulo_inscricao(cidade), "fazer")
            if not ims
            else Passo("inscricao", "A inscrição municipal já está na NFE.io", "pular")
        ),
        Passo("ligar", "Ligar no DaVinci e ler os dados de volta", "fazer"),
    ]


# --- prévia ----------------------------------------------------------------------------


async def previa(session: AsyncSession, company_id: UUID, cli: nfeio.ClienteNfeio) -> dict:
    """O que vai para a NFE.io. Nunca faz POST; bloqueio de negócio vira
    `bloqueios` (a tela mostra e trava o botão), não erro."""
    c = await session.get(Company, company_id)
    if c is None:
        raise NfseError(404, "empresa_nao_encontrada", "Empresa não encontrada.")
    f = await session.get(CompanyFiscal, company_id)
    if f is not None:
        await session.refresh(f)
    base: dict[str, Any] = {
        "company_id": c.id,
        "apelido": _apelido(c),
        "cnpj": c.cnpj,
        "liberado": ambiente.integrar_liberado(),
    }
    if not cnpj_ok(c.cnpj):
        dados, _sug = sugestao(c, f, None)
        return {
            **base,
            "modo": "criar",
            "na_nfeio": None,
            "dados": dados,
            "regime_motivo": None,
            "natureza_texto": None,
            "receita_ok": False,
            "certificado": None,
            "passos": [],
            "bloqueios": [msg_cnpj_invalido(c)],
            "avisos": [],
        }
    if empresas.situacao_integracao(f) == "ok":
        raise NfseError(409, "ja_integrada", f"A {_apelido(c)} já está integrada na NFE.io.")
    hoje = date.today()
    avisos: list[str] = []
    bloqueios: list[str] = []
    rec, erro_receita = await _consultar_receita(c.cnpj)
    if erro_receita is not None and erro_receita.detail.get("code") != "cnpj_nao_encontrado":
        avisos.append(MSG_RECEITA_FORA)
    achada: dict | None = None
    try:
        achada = await procurar_na_nfeio(session, c, f, cli)
        passos = await planejar(session, c, f, cli, achada)
    except NfseError as e:
        if e.status == 503:
            raise
        if f is not None and f.nfeio_company_id:
            # 01/10/2026 (revisão): empresa JÁ LIGADA nunca mostra "Criar a
            # empresa" (o servidor só completa): sem a NFE.io, só o bloqueio.
            dados, sug = sugestao(c, f, rec)
            return {
                **base,
                "modo": "completar",
                "na_nfeio": None,
                "dados": dados,
                "regime_motivo": sug["regime_motivo"],
                "natureza_texto": sug["natureza_texto"],
                "receita_ok": rec is not None,
                "certificado": None,
                "passos": [],
                "bloqueios": [
                    e.detail["mensagem"] if 400 <= e.status < 500 else MSG_NFEIO_FORA_LIGADA
                ],
                "avisos": avisos,
            }
        achada = None
        passos = _passos_de_criacao()
        if 400 <= e.status < 500:
            bloqueios.append(e.detail["mensagem"])
        else:
            avisos.append(MSG_NFEIO_FORA)
    p = _por_id(passos)
    dados, sug = sugestao(c, f, rec)
    cidade = cidade_da_nfeio(achada) or _cidade_do_codigo(dados["endereco"]["cmun_ibge"])
    if p["inscricao"].situacao == "fazer":
        p["inscricao"].titulo = _titulo_inscricao(cidade)
    cert_out = None
    if p["certificado"].situacao == "fazer":
        escolha = await escolher_certificado(session, c.id, hoje)
        try:
            if escolha.bloqueio:
                bloqueios.append(escolha.bloqueio[1])
            avisos.extend(escolha.avisos)
            if escolha.cert is not None:
                p["certificado"].titulo = _titulo_certificado(escolha)
                cert_out = {
                    "id": escolha.cert.id,
                    "filename": escolha.cert.filename,
                    "validade": escolha.validade,
                    "validade_conferida": escolha.validade_conferida,
                    "cnpj_confere": escolha.cnpj_confere,
                }
        finally:
            escolha.esquecer()
    if cidade and cidade.get("code") != SAO_PAULO:
        avisos.append(
            f"A empresa fica em {cidade.get('name')}/{cidade.get('state')} (fora da capital de "
            "SP): cada prefeitura tem a sua regra. Depois de integrar, a linha mostra se a "
            "NFE.io atende essa prefeitura; algumas pedem login da prefeitura, que se cadastra "
            "no painel da NFE.io."
        )
    if rec:
        rs = rec.get("razao_social")
        if rs and _sem_acento(rs) != _sem_acento(c.razao_social):
            avisos.append(
                f"Na Receita a razão social é '{rs}'; no DaVinci está '{c.razao_social}'. Vai "
                "para a NFE.io a da Receita."
            )
        situacao = rec.get("situacao_cadastral")
        if situacao and situacao not in ("ATIVA", "BAIXADA", "NULA"):
            avisos.append(f"A Receita diz que este CNPJ está {situacao}.")
    barrada = bloqueio_da_receita(rec, erro_receita, criar=p["criar_empresa"].situacao == "fazer")
    if barrada:
        bloqueios.append(barrada[1])
    elif erro_receita is not None and erro_receita.detail.get("code") == "cnpj_nao_encontrado":
        avisos.append("A Receita não achou este CNPJ: confira em Cadastros › Empresas.")
    if p["inscricao"].situacao == "fazer":
        avisos.append(MSG_TESTE)
    na_nfeio = None
    if achada is not None:
        na_nfeio = {
            "nfeio_id": _id(achada),
            "nome": empresas.nome_da_nfeio(achada),
            "ambiente": achada.get("environment"),
            "tem_certificado": _tem_certificado(achada),
            "tem_inscricao": p["inscricao"].situacao == "pular",
        }
    return {
        **base,
        "modo": "completar" if achada is not None else "criar",
        "na_nfeio": na_nfeio,
        "dados": dados,
        "regime_motivo": sug["regime_motivo"],
        "natureza_texto": sug["natureza_texto"],
        "receita_ok": rec is not None,
        "certificado": cert_out,
        "passos": [x.out() for x in passos],
        "bloqueios": bloqueios,
        "avisos": avisos,
    }


# --- integrar --------------------------------------------------------------------------


def msg_cnpj_baixado(situacao: str) -> str:
    return f"A Receita diz que este CNPJ está {situacao}: não dá para emitir nota por ele."


def bloqueio_da_receita(
    rec: Mapping[str, Any] | None, erro: NfseError | None, *, criar: bool
) -> tuple[str, str] | None:
    """(code, mensagem) quando a Receita impede integrar; None = pode seguir.
    01/10/2026 (revisão): a mesma regra na prévia e no POST. BAIXADA/NULA
    bloqueia sempre (D14); "a Receita não achou" bloqueia só quando a empresa
    vai ser CRIADA (CNPJ digitado errado com dígito válido); Receita sem
    resposta não bloqueia (vira aviso na prévia)."""
    if erro is not None:
        if criar and erro.detail.get("code") == "cnpj_nao_encontrado":
            return "cnpj_nao_encontrado", MSG_RECEITA_NAO_ACHOU
        return None
    situacao = (rec or {}).get("situacao_cadastral")
    if situacao in ("BAIXADA", "NULA"):
        return "cnpj_baixado", msg_cnpj_baixado(situacao)
    return None


async def _consultar_receita(cnpj: str | None) -> tuple[dict | None, NfseError | None]:
    try:
        return await receita.consultar(cnpj or ""), None
    except NfseError as e:
        return None, e


def _erro_antes_de_ligar(
    passos: list[Passo], status: int, code: str, msg: str, **extra: Any
) -> NfseError:
    return NfseError(status, code, msg, passos=_passos_out(passos, fim=True), **extra)


async def _relistar(cli: nfeio.ClienteNfeio, doc: str) -> tuple[bool, list[dict]]:
    """Depois de um POST sem resposta: (as duas listagens responderam?, empresas
    ATIVAS com o CNPJ — v1 e v2)."""
    try:
        listou, iguais = await _pelo_cnpj(cli, doc)
    except NfseError:
        return False, []
    return listou, [e for e in iguais if _ativa(e)]


async def integrar(
    session: AsyncSession,
    company_id: UUID,
    d: DadosIntegrar,
    user_id: UUID | None,
    cli: nfeio.ClienteNfeio,
    hoje: date | None = None,
) -> dict:
    """Cria (ou acha) a empresa na NFE.io, manda o certificado guardado,
    cadastra a IM em TESTE e liga. Nunca loga `d`, corpo de POST, arquivo ou senha."""
    hoje = hoje or date.today()
    c = await session.get(Company, company_id)
    if c is None:
        raise NfseError(404, "empresa_nao_encontrada", "Empresa não encontrada.")
    if not ambiente.integrar_liberado():
        raise NfseError(403, "integrar_travado", ambiente.MSG_INTEGRAR_TRAVADO)
    if not cnpj_ok(c.cnpj):
        raise NfseError(422, "cnpj_invalido", msg_cnpj_invalido(c))
    apelido = _apelido(c)
    doc = T.normalizar_documento(c.cnpj)
    async with trava(c.id) as t:
        f = await session.get(CompanyFiscal, c.id)
        if f is not None:
            await session.refresh(f)
        if empresas.situacao_integracao(f) == "ok":
            raise NfseError(409, "ja_integrada", f"A {apelido} já está integrada na NFE.io.")
        achada = await procurar_na_nfeio(session, c, f, cli)
        passos = await planejar(session, c, f, cli, achada)
        p = _por_id(passos)
        if f is not None and f.nfeio_company_id and p["criar_empresa"].situacao == "fazer":
            # Não acontece (`procurar_na_nfeio` para antes); trava extra contra empresa em dobro.
            raise _erro_antes_de_ligar(passos, 409, "nfeio_ligada_sumiu", MSG_LIGADA_SUMIU)
        cidade_nfeio = cidade_da_nfeio(achada)
        validar(passos, d, c, cidade=cidade_nfeio)
        escolha: Escolha | None = None
        if p["certificado"].situacao == "fazer":
            escolha = await escolher_certificado(
                session, c.id, hoje, preferido=getattr(d, "certificado_id", None)
            )
            if escolha.bloqueio:
                escolha.esquecer()
                code, msg = escolha.bloqueio
                raise _erro_antes_de_ligar(passos, 422, code, msg)
            if getattr(d, "certificado_id", None) is None:
                # O certificado que vai é o que a pessoa viu na prévia.
                escolha.esquecer()
                raise _erro_antes_de_ligar(
                    passos,
                    422,
                    "dados_incompletos",
                    "Faltam dados para integrar: confira os campos marcados.",
                    campos={
                        "certificado_id": "Feche e abra de novo para conferir o certificado "
                        "que vai para a NFE.io."
                    },
                )
            p["certificado"].titulo = _titulo_certificado(escolha)
        cidade_im = cidade_nfeio or _cidade_do_codigo(d.endereco.cmun_ibge)
        if p["inscricao"].situacao == "fazer":
            p["inscricao"].titulo = _titulo_inscricao(cidade_im)
        try:
            # 01/10/2026 (revisão): a regra da Receita (CNPJ BAIXADA/NULA, ou não
            # achado quando vai criar) é conferida de novo aqui, antes do 1º POST —
            # não só na prévia (chamada direta ou tela antiga não passam).
            rec, erro_receita = await _consultar_receita(c.cnpj)
            barrada = bloqueio_da_receita(
                rec, erro_receita, criar=p["criar_empresa"].situacao == "fazer"
            )
            if barrada:
                raise _erro_antes_de_ligar(passos, 422, barrada[0], barrada[1])
            return await _executar(
                session, c, f, d, user_id, cli, passos, achada, escolha, cidade_im, apelido, doc, t
            )
        finally:
            if escolha is not None:
                escolha.esquecer()


async def _executar(
    session: AsyncSession,
    c: Company,
    f: CompanyFiscal | None,
    d: DadosIntegrar,
    user_id: UUID | None,
    cli: nfeio.ClienteNfeio,
    passos: list[Passo],
    achada: dict | None,
    escolha: Escolha | None,
    cidade_im: dict | None,
    apelido: str,
    doc: str,
    t: Trava,
) -> dict:
    p = _por_id(passos)
    criar = p["criar_empresa"]
    cid = c.id  # depois de um rollback `c` expira: não reler atributo dele
    nid: str | None = _id(achada) or None
    criada_agora = False

    # --- 1. criar (POST, nunca repete) -----------------------------------------------
    if criar.situacao == "fazer":
        if not await t.renovar():
            raise _erro_antes_de_ligar(passos, 503, "integracao_sem_trava", MSG_SEM_TRAVA)
        r = await cli.criar_empresa(corpo_empresa(c, d))
        chamadas.registrar(session, r, c.id, None, None)
        await session.commit()
        if r.status in (401, 403):
            criar.situacao, criar.detalhe = "falhou", nfeio.CHAVE_RECUSADA
            raise _erro_antes_de_ligar(passos, 503, "chave_nfeio", nfeio.CHAVE_RECUSADA)
        if r.ok:
            nid = nfeio.normalizar_id(_id(nfeio.envelope(r.corpo, "company", "companies")))
        if r.ok and nid:
            criar.situacao = "feito"
            criada_agora = True
        elif r.ok or r.incerta or r.status == 409:
            # Pode ter criado: procura pelo CNPJ antes de qualquer outra coisa.
            listou, achou = await _relistar(cli, doc)
            if len(achou) > 1:
                criar.situacao, criar.detalhe = "falhou", MSG_INCERTA
                raise _erro_antes_de_ligar(
                    passos,
                    409,
                    "nfeio_cnpj_duplicado",
                    "Há mais de uma empresa com este CNPJ na NFE.io: ligue pela gaveta da "
                    "empresa (Colar link da NFE.io).",
                )
            if not listou or not achou:
                criar.situacao, criar.detalhe = "falhou", MSG_INCERTA
                raise _erro_antes_de_ligar(passos, 502, "nfeio_incerta", MSG_INCERTA)
            nid = _id(achou[0])
            criar.situacao = "ja_estava"
            criar.detalhe = "A NFE.io criou a empresa mesmo sem responder: seguimos com ela."
            criada_agora = True
        else:
            erros = explicar(r.msgs)
            codigos = set(r.codigos)
            if codigos & set(CODIGOS_CNPJ):
                msg, campo = "A NFE.io recusou o CNPJ: confira em Cadastros › Empresas.", None
            else:
                msg = (
                    f"A NFE.io recusou o cadastro da empresa: {nfeio.texto_do_erro(r) or r.status}"
                )
                campo = next((CAMPO_DO_CODIGO[x] for x in r.codigos if x in CAMPO_DO_CODIGO), None)
            criar.situacao, criar.detalhe, criar.erros = "falhou", msg, erros
            raise _erro_antes_de_ligar(
                passos, 422, "nfeio_recusou_empresa", msg, erros=erros, campo=campo
            )
        logger.info("nfse_nfeio_empresa_criada", company=str(c.id), nfeio=nid, http=r.status)
    if not nid:  # não acontece: criar devolve id ou levanta erro
        raise _erro_antes_de_ligar(passos, 502, "nfeio_incerta", MSG_INCERTA)

    # --- 2. ligar cedo -------------------------------------------------------------------
    if f is None:
        f = empresas.novo_fiscal(c.id)
        session.add(f)
    f.nfeio_company_id = nid
    f.updated_by = user_id
    try:
        await session.commit()
    except IntegrityError as e:
        await session.rollback()
        outra = await empresas._outra_ligada(session, cid, nid)
        nome = (outra.apelido or outra.razao_social) if outra is not None else "outra empresa"
        raise _erro_antes_de_ligar(
            passos, 409, "ja_ligada", f"Essa empresa da NFE.io já está ligada a {nome}."
        ) from e

    # --- 3. certificado (POST, nunca repete; mandar de novo só substitui) --------------
    pc = p["certificado"]
    if pc.situacao == "fazer" and escolha is not None and escolha.cert is not None:
        if not await t.renovar():
            pc.situacao, pc.detalhe = "nao_feito", MSG_TRAVA_PERDIDA
            escolha.esquecer()
    if pc.situacao == "fazer" and escolha is not None and escolha.cert is not None:
        senha = escolha.senha
        try:
            r = await cli.enviar_certificado(
                nid, escolha.pfx or b"", senha or "", nome_do_arquivo(escolha.cert.filename)
            )
            # Erro que ecoa a senha: some daqui antes de qualquer registro.
            r.msgs = _mascarar_msgs(r.msgs, senha)
            r.corpo, r.bruto = None, b""
            chamadas.registrar(session, r, c.id, None, None)
            await session.commit()
            if r.ok:
                pc.situacao = "feito"
            else:
                pc.situacao = "falhou"
                pc.detalhe = motivo_certificado(r, senha)
                pc.erros = explicar(r.msgs) or None
        finally:
            del senha
            escolha.esquecer()

    # --- 4. inscrição municipal (sempre em TESTE) ---------------------------------------
    pi = p["inscricao"]
    if pi.situacao == "fazer":
        postar = True
        if criar.situacao != "feito":
            ri, ims = await cli.inscricoes_municipais(nid)
            if not ri.ok:
                pi.situacao, pi.detalhe = "nao_feito", MSG_IM_SEM_RESPOSTA
                postar = False
            elif ims:
                pi.situacao = "ja_estava"
                postar = False
        if postar and not await t.renovar():
            pi.situacao, pi.detalhe = "nao_feito", MSG_TRAVA_PERDIDA
            postar = False
        if postar:
            regime = d.regime if criar.situacao != "pular" else _regime_da_nfeio(achada)
            r = await cli.criar_inscricao(nid, corpo_inscricao(d, cidade_im or {}, regime))
            chamadas.registrar(session, r, c.id, None, AMBIENTE_NOVA)
            await session.commit()
            if r.ok:
                pi.situacao = "feito"
            elif r.incerta:
                pi.situacao, pi.detalhe = "falhou", MSG_IM_INCERTA
            else:
                pi.situacao = "falhou"
                pi.detalhe = (
                    f"A NFE.io recusou a inscrição municipal: {nfeio.texto_do_erro(r) or r.status}"
                )
                pi.erros = explicar(r.msgs) or None

    # --- 5. releitura ----------------------------------------------------------------------
    pl = p["ligar"]
    try:
        await empresas.ligar(session, c.id, nid, cli)
        pl.situacao = "feito"
    except NfseError:
        # `ligar` só grava depois de ler tudo: aqui nada ficou pela metade.
        pl.situacao, pl.detalhe = "falhou", MSG_RELEITURA
    f = await session.get(CompanyFiscal, c.id)
    if f is not None:
        await session.refresh(f)
        f.updated_by = user_id
        await session.commit()

    for x in passos:
        if x.situacao == "fazer":
            x.situacao = "nao_feito"
    ok = all(x.situacao in ("feito", "ja_estava", "pular") for x in passos)
    if ok and criada_agora:
        mensagem = (
            f"{apelido} integrada na NFE.io, em TESTE (notas simuladas). Para emitir nota "
            "real, passe para Produção no painel da NFE.io."
        )
    elif ok:
        amb = "Produção" if f is not None and f.nfeio_ambiente == "Production" else "Teste"
        mensagem = f"Integração da {apelido} completa ({amb})."
    else:
        falhou = next(x for x in passos if x.situacao in ("falhou", "nao_feito"))
        if falhou.id == "ligar":
            # 01/10/2026 (revisão): os passos na NFE.io deram certo; só a releitura
            # não respondeu (MSG_RELEITURA já diz o que fazer — não repetir).
            mensagem = (
                f"{apelido} ficou ligada na NFE.io, mas a NFE.io não devolveu os dados agora. "
                "Use Atualizar na gaveta da empresa em alguns minutos."
            )
        else:
            det = (falhou.detalhe or falhou.titulo).rstrip(".")
            det = det[:1].lower() + det[1:]
            sufixo = (
                "A empresa não é criada de novo."
                if "completar integração" in det.lower()
                else "Corrija e clique em Completar integração: a empresa não é criada de novo."
            )
            mensagem = f"{apelido} ficou ligada na NFE.io, mas {det}. {sufixo}"
    logger.info(
        "nfse_nfeio_integrar",
        company=str(c.id),
        nfeio=nid,
        ok=ok,
        passos={x.id: x.situacao for x in passos},
    )
    return {"ok": ok, "mensagem": mensagem, "passos": [x.out() for x in passos]}
