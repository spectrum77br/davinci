"""Consulta pública do ProdCert, por CNPJ, com conferência integral das páginas.

O portal oficial publica este serviço legado em HTTP. O formulário consulta
certificados em quatro situações separadas; nenhuma delas pode ser omitida ou
interpretada como vazia quando a página não puder ser validada.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime
from html.parser import HTMLParser
from typing import Any

import httpx

SOURCE_URL = "http://www.inmetro.gov.br/prodcert/"
QUERY_URL = SOURCE_URL + "certificados/lista.asp"
MAKISA_CNPJ = "40191104000145"
STATUS_FILTERS = ("ativo", "vencido", "suspenso", "naoativo")
STATUS_LABELS = {
    "ativo": {"ativo"},
    "vencido": {"expirado", "vencido"},
    "suspenso": {"suspenso"},
    "naoativo": {"cancelado", "não-ativo", "não ativo"},
}
MAX_PAGE_BYTES = 2 * 1024 * 1024
MAX_DOWNLOAD_BYTES = 8 * 1024 * 1024
MAX_PAGES_PER_STATUS = 20
TOTAL_TIMEOUT_SECONDS = 180
REQUEST_TIMEOUT_SECONDS = 45


class InmetroSourceError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _invalid(message: str = "A estrutura da consulta do Inmetro mudou ou está incompleta."):
    return InmetroSourceError("inmetro_source_invalid", message)


def _text(value: str) -> str:
    return " ".join(value.split())


def normalizar_identidade(value: str) -> str:
    return _text(value).upper()


def _cnpj(value: str) -> str:
    return re.sub(r"[\s./-]", "", value)


def _date(value: str) -> str | None:
    if not value:
        return None
    try:
        if re.fullmatch(r"[0-9]{2}/[0-9]{2}/[0-9]{4}", value):
            return datetime.strptime(value, "%d/%m/%Y").date().isoformat()
    except ValueError:
        pass
    raise _invalid("O Inmetro retornou uma data inválida; os dados anteriores foram preservados.")


@dataclass
class _Cell:
    text: list[str] = field(default_factory=list)
    nested: bool = False
    css: str = ""


@dataclass
class _Row:
    sequence: int
    cells: list[_Cell] = field(default_factory=list)


class _PageParser(HTMLParser):
    """Retém linhas/células sem executar scripts ou depender de HTML bem formado."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[_Row] = []
        self.row_stack: list[_Row] = []
        self.cell_stack: list[_Cell] = []
        self.text: list[str] = []
        self.inputs: dict[str, str] = {}
        self.pages: dict[int, tuple[int, int, int]] = {}
        self.ignored = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag in {"script", "style"}:
            self.ignored += 1
        if self.ignored:
            return
        if tag == "input" and values.get("type", "").lower() == "hidden":
            name = values.get("name", "").lower()
            if name in {"cnpj_cpf", "status_certificado"}:
                value = values.get("value") or ""
                if name in self.inputs and self.inputs[name] != value:
                    raise _invalid()
                self.inputs[name] = value
        for name in ("onclick", "href"):
            value = values.get(name) or ""
            if "Pagina(" not in value:
                continue
            match = re.fullmatch(
                r"\s*(?:javascript:\s*)?Pagina\(\s*['\"]?([0-9]+)['\"]?\s*,"
                r"\s*['\"]?([0-9]+)['\"]?\s*,\s*['\"]?([0-9]+)['\"]?\s*\)\s*;?\s*",
                value,
            )
            if not match:
                raise _invalid("A paginação da consulta do Inmetro não pôde ser validada.")
            args = tuple(int(item) for item in match.groups())
            if any(item < 1 or item > MAX_PAGES_PER_STATUS for item in args):
                raise _invalid("A consulta do Inmetro excedeu o limite de páginas permitido.")
            if args[0] in self.pages and self.pages[args[0]] != args:
                raise _invalid()
            self.pages[args[0]] = args
        if tag == "table" and self.cell_stack:
            self.cell_stack[-1].nested = True
        if tag == "tr":
            row = _Row(sequence=len(self.rows))
            self.rows.append(row)
            self.row_stack.append(row)
        if tag in {"td", "th"} and self.row_stack:
            cell = _Cell(css=values.get("class") or "")
            self.row_stack[-1].cells.append(cell)
            self.cell_stack.append(cell)
        self.text.append(" ")
        if self.cell_stack:
            self.cell_stack[-1].text.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"}:
            self.ignored = max(0, self.ignored - 1)
        if self.ignored:
            return
        if tag in {"td", "th"} and self.cell_stack:
            self.cell_stack.pop()
        if tag == "tr" and self.row_stack:
            self.row_stack.pop()
        self.text.append(" ")
        if self.cell_stack:
            self.cell_stack[-1].text.append(" ")

    def handle_data(self, data: str) -> None:
        if self.ignored:
            return
        self.text.append(data)
        if self.cell_stack:
            self.cell_stack[-1].text.append(data)


_HEADER = re.compile(
    r"^Certificador:\s*(.*?)\s+N[º°]\s*Certificado:\s*(.*?)\s+Tipo:\s*(.*?)"
    r"\s+Emissão:\s*(.*?)\s+Validade:\s*(.*?)\s+Status do Certificado:\s*(.*?)"
    r"(?:\s+Doc\.Normativo)?$",
)
_COMPANY_COLUMNS = (
    "CNPJ/CPF", "Razão Social / Nome (PF)", "Nome fantasia", "Endereço", "Status",
    "Papel da empresa",
)
_PRODUCT_COLUMNS = ("Marca", "Modelo", "Importado", "Descrição")


def _records_for_certificate(block: dict) -> list[dict[str, Any]]:
    certificate = block["certificado"]
    companies = block["empresas"]
    applicants = [company for company in companies if (
        _cnpj(company["CNPJ/CPF"]) == MAKISA_CNPJ
        and normalizar_identidade(company["Papel da empresa"]) in {
            "SOLICITANTE", "SOLICITANTE/FABRICANTE",
        }
    )]
    if not applicants or not block["produtos"] or not block["company_header"]:
        raise _invalid("Não foi possível confirmar a Makisa como solicitante dos certificados.")
    names = {company["Razão Social / Nome (PF)"] for company in applicants}
    if len(names) != 1 or not next(iter(names)):
        raise _invalid("A identificação da empresa no Inmetro está incompleta ou divergente.")
    start = _date(certificate["Emissão"])
    end = _date(certificate["Validade"])
    if start and end and start > end:
        raise _invalid("O Inmetro retornou um período de validade inconsistente.")
    alerts = []
    confirmed = ["modelo", "certificado", "numero"]
    for field_name, value, label in (("inicio", start, "Emissão"), ("fim", end, "Validade")):
        if value:
            confirmed.append(field_name)
        else:
            alerts.append(f"{label} do certificado não informada na fonte.")
    records = []
    for product in block["produtos"]:
        identity = [certificate["Certificador"], certificate["Número"], product["Modelo"]]
        if any(not value or "|" in value or len(value) > 500 for value in identity):
            raise _invalid(
                "O Inmetro retornou uma identificação de produto incompleta ou inválida.",
            )
        records.append({
            "chave": "|".join(normalizar_identidade(value) for value in identity),
            "cnpj": MAKISA_CNPJ,
            "nome_empresa": next(iter(names)),
            "certificador": certificate["Certificador"],
            "numero": certificate["Número"],
            "modelo": product["Modelo"],
            "marca": product["Marca"],
            "descricao": product["Descrição"],
            "produto": product["Descrição"] or product["Modelo"],
            "inicio": start,
            "fim": end,
            "situacao_certificado": certificate["Situação"],
            "source_url": SOURCE_URL,
            "source_updated_at": None,
            "dados_origem": {
                "certificado": certificate,
                "empresas": companies,
                "produto": product,
            },
            "alertas": alerts,
            "campos_confirmados": confirmed,
        })
    return records


def parse_certificacoes_page(data: bytes, *, status_filtro: str, pagina: int = 1) -> dict:
    """Lê uma página e seus totais; completude é conferida pelo cliente após paginar."""
    if status_filtro not in STATUS_FILTERS:
        raise _invalid()
    if len(data) > MAX_PAGE_BYTES:
        raise InmetroSourceError("inmetro_source_too_large", "A página do Inmetro é muito grande.")
    parser = _PageParser()
    parser.feed(data.decode("iso-8859-1"))
    parser.close()
    visible = _text(" ".join(parser.text))
    totals = re.search(
        r"Resultado da Consulta:\s*([0-9]+)\s+Certificado\(s\)\s*"
        r"([0-9]+)\s+Produtos\(s\)\s*([0-9]+)\s+Serviços\(s\)", visible,
    )
    current = re.search(r"\bPágina\s+([0-9]+)\b", visible)
    if (
        not totals or not current or int(current[1]) != pagina
        or _cnpj(parser.inputs.get("cnpj_cpf", "")) != MAKISA_CNPJ
        or parser.inputs.get("status_certificado") != status_filtro
        or parser.row_stack or parser.cell_stack
    ):
        raise _invalid()
    certificate_total, product_total, service_total = map(int, totals.groups())
    if service_total or certificate_total > 1000 or product_total > 10000:
        raise _invalid("A consulta do Inmetro retornou uma estrutura não suportada.")
    blocks: list[dict] = []
    block = None
    section = None
    for row in parser.rows:
        if any(cell.nested for cell in row.cells):
            continue
        cells = [_text(" ".join(cell.text)) for cell in row.cells]
        if not cells or not any(cells):
            continue
        if len(cells) == 1 and cells[0].startswith("Certificador:"):
            match = _HEADER.fullmatch(cells[0])
            if not match:
                raise _invalid()
            certifier, number, kind, start, end, state = match.groups()
            if kind != "Produto" or state.casefold() not in STATUS_LABELS[status_filtro]:
                raise _invalid("A situação do certificado diverge do filtro oficial consultado.")
            block = {
                "certificado": {"Certificador": certifier, "Número": number,
                                "Tipo": kind, "Emissão": start, "Validade": end,
                                "Situação": state},
                "empresas": [], "produtos": [], "company_header": False,
            }
            blocks.append(block)
            section = None
            continue
        if block is None:
            continue
        if tuple(cells) == _COMPANY_COLUMNS:
            section = "company"
            block["company_header"] = True
        elif tuple(cells) == _PRODUCT_COLUMNS:
            if not block["empresas"]:
                raise _invalid()
            section = "product"
        elif all(cell.css == "listagem" for cell in row.cells):
            if section == "company" and len(cells) == len(_COMPANY_COLUMNS):
                block["empresas"].append(dict(zip(_COMPANY_COLUMNS, cells, strict=True)))
            elif section == "product" and len(cells) == len(_PRODUCT_COLUMNS):
                block["produtos"].append(dict(zip(_PRODUCT_COLUMNS, cells, strict=True)))
            else:
                raise _invalid()
    records = [record for item in blocks for record in _records_for_certificate(item)]
    if (len(blocks) > certificate_total or len(records) > product_total
            or (certificate_total == 0) != (product_total == 0)
            or certificate_total and not blocks):
        raise _invalid("Os totais da consulta do Inmetro não conferem com os registros recebidos.")
    return {
        "records": records,
        "certificados_total": certificate_total,
        "produtos_total": product_total,
        "certificados_pagina": len(blocks),
        "produtos_pagina": len(records),
        "paginas": parser.pages,
    }


async def _download(client: httpx.AsyncClient, data: dict[str, str]) -> bytes:
    async with client.stream("POST", QUERY_URL, data=data) as response:
        if response.status_code != 200:
            raise InmetroSourceError(
                "inmetro_source_http", "A consulta oficial do Inmetro está indisponível.",
            )
        length = response.headers.get("content-length", "")
        if length.isdecimal() and int(length) > MAX_PAGE_BYTES:
            raise InmetroSourceError(
                "inmetro_source_too_large", "A página do Inmetro é muito grande.",
            )
        content = bytearray()
        async for chunk in response.aiter_bytes(chunk_size=64 * 1024):
            if len(content) + len(chunk) > MAX_PAGE_BYTES:
                raise InmetroSourceError(
                    "inmetro_source_too_large", "A página do Inmetro é muito grande.",
                )
            content.extend(chunk)
    return bytes(content)


async def fetch_certificacoes() -> dict[str, Any]:
    """Consulta sequencialmente todos os estados e páginas, sem publicar lote parcial."""
    records: dict[str, dict] = {}
    checksum = hashlib.sha256()
    downloaded = 0
    try:
        async with asyncio.timeout(TOTAL_TIMEOUT_SECONDS):
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(REQUEST_TIMEOUT_SECONDS, connect=15),
                follow_redirects=False,
                headers={"Accept-Encoding": "identity"},
            ) as client:
                for status in STATUS_FILTERS:
                    pending = {1: None}
                    visited: set[int] = set()
                    totals = None
                    certificate_count = product_count = 0
                    while pending:
                        page = min(pending)
                        args = pending.pop(page)
                        if page in visited:
                            continue
                        if len(visited) >= MAX_PAGES_PER_STATUS:
                            raise _invalid("A consulta do Inmetro excedeu o limite de páginas.")
                        form = {"tipo_pessoa": "J", "cnpj_cpf": MAKISA_CNPJ,
                                "status_certificado": status, "papel_empresa": "22"}
                        if args:
                            form.update(dict(zip(
                                ("pagina", "paginaini", "paginafim"), map(str, args), strict=True,
                            )))
                        data = await _download(client, form)
                        downloaded += len(data)
                        if downloaded > MAX_DOWNLOAD_BYTES:
                            raise InmetroSourceError(
                                "inmetro_source_too_large", "A consulta do Inmetro é muito grande.",
                            )
                        parsed = parse_certificacoes_page(data, status_filtro=status, pagina=page)
                        page_totals = (parsed["certificados_total"], parsed["produtos_total"])
                        if totals is not None and totals != page_totals:
                            raise _invalid(
                                "A base do Inmetro mudou durante a consulta; tente depois.",
                            )
                        totals = page_totals
                        visited.add(page)
                        checksum.update(f"{status}:{page}:".encode())
                        checksum.update(data)
                        certificate_count += parsed["certificados_pagina"]
                        product_count += parsed["produtos_pagina"]
                        for record in parsed["records"]:
                            previous = records.get(record["chave"])
                            if previous is not None and previous != record:
                                raise _invalid(
                                    "Há produtos com a mesma identificação e dados divergentes.",
                                )
                            records[record["chave"]] = record
                        for number, paging_args in parsed["paginas"].items():
                            if number not in visited:
                                pending[number] = paging_args
                    if (totals != (certificate_count, product_count)
                            or visited != set(range(1, max(visited) + 1))):
                        raise _invalid("Não foi possível confirmar todas as páginas do Inmetro.")
    except (httpx.HTTPError, TimeoutError) as exc:
        raise InmetroSourceError(
            "inmetro_source_network",
            "Não foi possível consultar o Inmetro; tente novamente depois.",
        ) from exc
    if not records:
        raise InmetroSourceError(
            "inmetro_source_empty",
            "O Inmetro não retornou produtos da Makisa; os dados anteriores foram preservados.",
        )
    return {
        "source_url": SOURCE_URL,
        # A página ASP não publica uma data de atualização da base. O horário
        # HTTP da resposta é só o horário da consulta e não deve substituí-la.
        "source_updated_at": None,
        "sha256": checksum.hexdigest(),
        "records": [records[key] for key in sorted(records)],
    }
