"""Abre o certificado A1 (.pfx/.p12) guardado, só para conferir (01/10/2026).

Eduardo: "algumas empresas nossas não estão integradas no nfe.io, precisa
integrar". Antes de mandar o certificado guardado em Cadastros › Empresas para
a NFE.io, o servidor abre o arquivo com a senha guardada e lê a validade REAL
(a do cadastro é digitada) e o CNPJ do titular. Nada sai daqui: nem o arquivo,
nem a senha, nem a mensagem da exceção (só o tipo vai pro log).

Certificado antigo (criptografia RC2/3DES) pode não abrir no OpenSSL 3: aí
devolve None e quem chamou trata como aviso, não como bloqueio.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

import structlog
from cryptography import x509
from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID

logger = structlog.get_logger()

# ICP-Brasil: otherName do e-CNPJ com o CNPJ da pessoa jurídica.
OID_CNPJ = "2.16.76.1.3.3"
_CNPJ = re.compile(rb"\d{14}")
_CN_CNPJ = re.compile(r":(\d{14})$")


@dataclass(frozen=True)
class InfoCertificado:
    validade: date  # not_valid_after_utc.date()
    cnpj: str | None  # SAN otherName 2.16.76.1.3.3; senão CN terminando em ":<14 dígitos>"


def _cnpj(cert: x509.Certificate) -> str | None:
    try:
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    except x509.ExtensionNotFound:
        san = None
    if san is not None:
        for nome in san.get_values_for_type(x509.OtherName):
            if nome.type_id.dotted_string == OID_CNPJ:
                achado = _CNPJ.search(nome.value)
                if achado:
                    return achado.group(0).decode()
    for atributo in cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME):
        no_cn = _CN_CNPJ.search(str(atributo.value).strip())
        if no_cn:
            return no_cn.group(1)
    return None


def ler(pfx: bytes, senha: str | None) -> InfoCertificado | None:
    """None = não abriu (senha errada OU criptografia antiga). Nunca loga a exceção (só o tipo)."""
    try:
        _chave, cert, _outros = pkcs12.load_key_and_certificates(
            pfx, senha.encode() if senha else None
        )
    except (ValueError, TypeError, UnsupportedAlgorithm) as e:
        logger.info("nfse_certificado_nao_abriu", erro=type(e).__name__)
        return None
    if cert is None:
        return None
    return InfoCertificado(validade=cert.not_valid_after_utc.date(), cnpj=_cnpj(cert))
