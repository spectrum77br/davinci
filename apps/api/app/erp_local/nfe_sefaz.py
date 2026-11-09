"""SEFAZ web services for NF-e 4.00. Production is closed here until someone opens it on purpose.

Authorization outcomes are parsed, never assumed: anything unknown stays pending for a consult,
because a note sent may exist even when the reply was lost.
"""

import base64
import gzip
import io
import re
from uuid import uuid4

import httpx

from app.erp_local.nfe_xml import NFE_NS, UF_CODES

HOMOLOGATION = {
    "SP": {
        "authorize": "https://homologacao.nfe.fazenda.sp.gov.br/ws/nfeautorizacao4.asmx",
        "status": "https://homologacao.nfe.fazenda.sp.gov.br/ws/nfestatusservico4.asmx",
        "consult": "https://homologacao.nfe.fazenda.sp.gov.br/ws/nfeconsultaprotocolo4.asmx",
        "event": "https://homologacao.nfe.fazenda.sp.gov.br/ws/nferecepcaoevento4.asmx",
        "void": "https://homologacao.nfe.fazenda.sp.gov.br/ws/nfeinutilizacao4.asmx",
    },
    "SVRS": {
        "authorize": "https://nfe-homologacao.svrs.rs.gov.br/ws/NfeAutorizacao/NFeAutorizacao4.asmx",
        "status": "https://nfe-homologacao.svrs.rs.gov.br/ws/NfeStatusServico/NfeStatusServico4.asmx",
        "consult": "https://nfe-homologacao.svrs.rs.gov.br/ws/NfeConsulta/NfeConsulta4.asmx",
        "event": "https://nfe-homologacao.svrs.rs.gov.br/ws/recepcaoevento/recepcaoevento4.asmx",
        "void": "https://nfe-homologacao.svrs.rs.gov.br/ws/nfeinutilizacao/nfeinutilizacao4.asmx",
    },
}
SERVICES = {
    "authorize": "NFeAutorizacao4",
    "status": "NFeStatusServico4",
    "consult": "NFeConsultaProtocolo4",
    "event": "NFeRecepcaoEvento4",
    "void": "NFeInutilizacao4",
}
# National environment, for what does not belong to one state. Still to be proven in homologation.
NATIONAL = {
    "distribution": "https://hom1.nfe.fazenda.gov.br/NFeDistribuicaoDFe/NFeDistribuicaoDFe.asmx",
    "manifest": "https://hom1.nfe.fazenda.gov.br/NFeRecepcaoEvento4/NFeRecepcaoEvento4.asmx",
}
DOCUMENT_LIMIT = 8 * 1024 * 1024
AUTHORIZED = {"100", "150"}
DENIED = {"110", "205", "301", "302", "303"}
PENDING = {"103", "105"}
ALREADY_SENT = {"204", "539"}


class ProductionClosedError(RuntimeError):
    """Raised for any attempt to reach a production endpoint from this module."""


def _envelope(service, body):
    namespace = "http://www.portalfiscal.inf.br/nfe/wsdl/" + service
    return (
        '<soap12:Envelope xmlns:soap12="http://www.w3.org/2003/05/soap-envelope"><soap12:Body>'
        f'<nfeDadosMsg xmlns="{namespace}">{body}</nfeDadosMsg></soap12:Body></soap12:Envelope>'
    )


def _value(xml, name):
    match = re.search(rf"<(?:\w+:)?{name}>(.*?)</(?:\w+:)?{name}>", xml, re.S)
    return match.group(1) if match else None


class Sefaz:
    """`environment` is fixed by whoever builds it; only homologation is implemented."""

    def __init__(self, uf, *, environment="homologation", cert=None, verify=True, transport=None):
        if environment != "homologation":
            raise ProductionClosedError("Emissão em produção não está liberada neste núcleo.")
        self.uf, self.environment = uf, environment
        self.urls = HOMOLOGATION.get(uf, HOMOLOGATION["SVRS"])
        self.options = {"cert": cert, "verify": verify, "timeout": 40}
        if transport is not None:
            self.options = {"transport": transport, "timeout": 40}

    def _post(self, operation, body):
        url = self.urls[operation]
        if "homolog" not in url:
            raise ProductionClosedError("Endereço fora do ambiente de homologação.")
        with httpx.Client(**self.options) as client:
            response = client.post(
                url,
                content=_envelope(SERVICES[operation], body).encode("utf-8"),
                headers={"Content-Type": "application/soap+xml; charset=utf-8"},
            )
        response.raise_for_status()
        return response.text

    def status(self):
        body = (
            f'<consStatServ versao="4.00" xmlns="{NFE_NS}"><tpAmb>2</tpAmb>'
            f"<cUF>{UF_CODES[self.uf]}</cUF><xServ>STATUS</xServ></consStatServ>"
        )
        reply = self._post("status", body)
        return {"code": _value(reply, "cStat"), "message": _value(reply, "xMotivo")}

    def authorize(self, signed_xml):
        """Synchronous single-note send. Network failure raises: the caller must consult first."""
        batch = str(uuid4().int)[:15]
        body = (
            f'<enviNFe versao="4.00" xmlns="{NFE_NS}"><idLote>{batch}</idLote>'
            f"<indSinc>1</indSinc>{signed_xml}</enviNFe>"
        )
        reply = self._post("authorize", body)
        protocol = re.search(r"<protNFe\b.*?</protNFe>", reply, re.S)
        scope = protocol.group(0) if protocol else reply
        code = _value(scope, "cStat")
        return {
            "code": code,
            "message": _value(scope, "xMotivo"),
            "protocol": _value(scope, "nProt"),
            "received_at": _value(scope, "dhRecbto"),
            "protocol_xml": protocol.group(0) if protocol else None,
            "outcome": outcome(code),
        }

    def _post_national(self, url, envelope):
        if not url.startswith("https://hom1."):
            raise ProductionClosedError("Endereço fora do ambiente de homologação.")
        with httpx.Client(**self.options) as client:
            response = client.post(
                url,
                content=envelope.encode("utf-8"),
                headers={"Content-Type": "application/soap+xml; charset=utf-8"},
            )
        response.raise_for_status()
        return response.text

    def distribution(self, cnpj, uf, last_nsu):
        """Documents issued against this CNPJ after the last sequence already read."""
        namespace = "http://www.portalfiscal.inf.br/nfe/wsdl/NFeDistribuicaoDFe"
        inner = (
            f'<distDFeInt versao="1.01" xmlns="{NFE_NS}"><tpAmb>2</tpAmb>'
            f"<cUFAutor>{UF_CODES[uf]}</cUFAutor><CNPJ>{cnpj}</CNPJ>"
            f"<distNSU><ultNSU>{int(last_nsu):015d}</ultNSU></distNSU></distDFeInt>"
        )
        reply = self._post_national(
            NATIONAL["distribution"],
            '<soap12:Envelope xmlns:soap12="http://www.w3.org/2003/05/soap-envelope"><soap12:Body>'
            f'<nfeDistDFeInteresse xmlns="{namespace}"><nfeDadosMsg>{inner}</nfeDadosMsg>'
            "</nfeDistDFeInteresse></soap12:Body></soap12:Envelope>",
        )
        documents = []
        for match in re.finditer(r'<docZip NSU="(\d+)" schema="([^"]+)">([^<]+)</docZip>', reply):
            with gzip.GzipFile(fileobj=io.BytesIO(base64.b64decode(match.group(3)))) as packed:
                raw = packed.read(DOCUMENT_LIMIT + 1)
            if len(raw) > DOCUMENT_LIMIT:
                raise ValueError("Documento acima do limite")
            documents.append(
                {"nsu": match.group(1), "schema": match.group(2), "xml": raw.decode("utf-8")}
            )
        return {
            "code": _value(reply, "cStat"),
            "message": _value(reply, "xMotivo"),
            "last_nsu": _value(reply, "ultNSU"),
            "max_nsu": _value(reply, "maxNSU"),
            "documents": documents,
        }

    def manifest(self, signed_event):
        batch = str(uuid4().int)[:15]
        reply = self._post_national(
            NATIONAL["manifest"],
            _envelope(
                SERVICES["event"],
                f'<envEvento versao="1.00" xmlns="{NFE_NS}"><idLote>{batch}</idLote>'
                f"{signed_event}</envEvento>",
            ),
        )
        scope = re.search(r"<retEvento\b.*?</retEvento>", reply, re.S)
        block = scope.group(0) if scope else reply
        code = _value(block, "cStat")
        return {
            "code": code,
            "message": _value(block, "xMotivo"),
            "protocol": _value(block, "nProt"),
            "accepted": code in EVENT_ACCEPTED,
        }

    def send_event(self, signed_event):
        batch = str(uuid4().int)[:15]
        body = (
            f'<envEvento versao="1.00" xmlns="{NFE_NS}"><idLote>{batch}</idLote>'
            f"{signed_event}</envEvento>"
        )
        reply = self._post("event", body)
        scope = re.search(r"<retEvento\b.*?</retEvento>", reply, re.S)
        block = scope.group(0) if scope else reply
        code = _value(block, "cStat")
        return {
            "code": code,
            "message": _value(block, "xMotivo"),
            "protocol": _value(block, "nProt"),
            "event_xml": scope.group(0) if scope else None,
            "accepted": code in EVENT_ACCEPTED,
        }

    def void_numbers(self, signed_request):
        reply = self._post("void", signed_request)
        code = _value(reply, "cStat")
        return {
            "code": code,
            "message": _value(reply, "xMotivo"),
            "protocol": _value(reply, "nProt"),
            "accepted": code in VOID_ACCEPTED,
        }

    def consult(self, key):
        body = (
            f'<consSitNFe versao="4.00" xmlns="{NFE_NS}"><tpAmb>2</tpAmb>'
            f"<xServ>CONSULTAR</xServ><chNFe>{key}</chNFe></consSitNFe>"
        )
        reply = self._post("consult", body)
        protocol = re.search(r"<protNFe\b.*?</protNFe>", reply, re.S)
        scope = protocol.group(0) if protocol else reply
        code = _value(scope, "cStat")
        return {
            "code": code,
            "message": _value(scope, "xMotivo"),
            "protocol": _value(scope, "nProt"),
            "protocol_xml": protocol.group(0) if protocol else None,
            "outcome": outcome(code),
        }


EVENT_ACCEPTED = {"135", "136", "155"}
VOID_ACCEPTED = {"102"}


def outcome(code):
    if code in AUTHORIZED:
        return "authorized"
    if code in DENIED:
        return "denied"
    if code in PENDING or code is None:
        return "pending"
    if code in ALREADY_SENT:
        return "consult"
    return "rejected"
