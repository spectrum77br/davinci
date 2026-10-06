"""App Uranyx (06/10/2026): o painel do app dos clientes da Uranyx, por dentro do DaVinci.

Contrato: `docs/integracao/conteudo-e-catalogo-v1.md`, seção 6, no repositório
app-uranyx. Uma rota só, que repassa para a API do app:

  /api/app-uranyx/<resto>  →  {APP_URANYX_API_URL}/admin/<resto>

com o token da equipe posto AQUI (o navegador nunca o vê), e só quando <resto>
começa por uma das rotas do contrato (`repasse.ROTAS`); o resto é 404
`app_uranyx_rota_desconhecida`.

ACESSO: só admin com o e-mail em APP_URANYX_USUARIOS (`acesso.liberado`, a mesma
regra da chave `app_uranyx` do /api/auth/me). Os outros: 403 `app_uranyx_restrito`.

CORPO: JSON até 2 MB; multipart (upload de foto/PDF) até 25 MB de arquivo, com
uma folga para o envelope do multipart. Acima: 413. O corpo é lido aos pedaços e
para no teto. Outro tipo de corpo, ou Content-Type fora do ASCII: 415.

RESPOSTAS: o status e o JSON da API do app passam como vieram
(`{erro, mensagem, campos}` nos erros). Sem configuração, fora do ar, sem
resposta no tempo, token recusado (401, ou o 429 `bloqueado_temporariamente`
que vem depois) ou endereço errado (404 sem JSON): 503 `app_uranyx_indisponivel`
com a mensagem em português (`services/app_uranyx/repasse.py`). TODA resposta da
rota, inclusive 401/403/404/413/415/503, leva `X-Content-Type-Options: nosniff`.

REGISTRO: o único registro é o log `app_uranyx_acao`: toda ação que não é GET,
com o e-mail de quem fez, o método, o caminho (com o id), o status e, na
criação, o id novo. Nunca o corpo nem o token. O Histórico do DaVinci NÃO marca
estas ações: o HistoricoMiddleware descarta o pedido que não muda o banco do
DaVinci e não está em `historico/nomes.ACOES`, e o repasse só muda a API do app.
"""

from __future__ import annotations

import json
from typing import Annotated
from urllib.parse import quote

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.routing import APIRoute

from app.deps.auth import require_active_user
from app.models import User
from app.services.app_uranyx import acesso, repasse

logger = structlog.get_logger()

PREFIXO = "/api/app-uranyx"
METODOS = ["GET", "POST", "PUT", "PATCH", "DELETE"]

# O PDF do manual vai até 25 MB na API do app (contrato, seção 3). O teto do
# corpo dá uma folga para as bordas e o campo `nome` do multipart: quem decide o
# tamanho do ARQUIVO é a API do app (422 `arquivo_invalido`).
UPLOAD_MAX_BYTES = 25 * 1024 * 1024
FOLGA_MULTIPART_BYTES = 256 * 1024
JSON_MAX_BYTES = 2 * 1024 * 1024


async def _so_liberado(user: Annotated[User, Depends(require_active_user)]) -> User:
    if not acesso.liberado(user):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail={
                "code": "app_uranyx_restrito",
                "message": "O módulo App Uranyx não está liberado para este usuário.",
            },
        )
    return user


class _RotaNosniff(APIRoute):
    """Põe `X-Content-Type-Options: nosniff` em toda resposta, inclusive nos
    erros das dependências (401 sem login, 403): o navegador nunca adivinha o
    tipo de um corpo que veio da API do app pela origem do DaVinci."""

    def get_route_handler(self):
        original = super().get_route_handler()

        async def handler(request: Request) -> Response:
            try:
                resposta = await original(request)
            except HTTPException as e:
                e.headers = {**(e.headers or {}), "X-Content-Type-Options": "nosniff"}
                raise
            resposta.headers["X-Content-Type-Options"] = "nosniff"
            return resposta

        return handler


router = APIRouter(
    prefix=PREFIXO,
    tags=["app-uranyx"],
    dependencies=[Depends(_so_liberado)],
    route_class=_RotaNosniff,
)


def _caminho_cru(request: Request, resto: str) -> str:
    """O que vem depois de /api/app-uranyx/, AINDA codificado (`%2F` continua `%2F`)."""
    cru = request.scope.get("raw_path")
    if cru:
        texto = cru.decode("latin-1").split("?", 1)[0]
        if texto.startswith(PREFIXO + "/"):
            return texto[len(PREFIXO) + 1 :]
    # Servidor sem `raw_path`: recodifica o caminho já decodificado.
    return quote(resto, safe="/")


def _grande_demais(upload: bool) -> HTTPException:
    if upload:
        return HTTPException(
            status.HTTP_413_CONTENT_TOO_LARGE,
            detail={"code": "app_uranyx_arquivo_grande", "message": "O arquivo passa de 25 MB."},
        )
    return HTTPException(
        status.HTTP_413_CONTENT_TOO_LARGE,
        detail={"code": "app_uranyx_corpo_grande", "message": "O pedido passa de 2 MB."},
    )


def _tipo_nao_suportado() -> HTTPException:
    return HTTPException(
        status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
        detail={
            "code": "app_uranyx_tipo_nao_suportado",
            "message": "Mande JSON ou um arquivo (multipart/form-data).",
        },
    )


async def _corpo(request: Request, tipo: str | None) -> bytes:
    """O corpo cru, com teto (413) — pelo cabeçalho e, sem ele, lendo aos pedaços."""
    if tipo and not tipo.isascii():
        # O httpx só manda cabeçalho ASCII: sem isto, 500 no repasse.
        raise _tipo_nao_suportado()
    upload = repasse.e_upload(tipo)
    teto = UPLOAD_MAX_BYTES + FOLGA_MULTIPART_BYTES if upload else JSON_MAX_BYTES
    declarado = request.headers.get("content-length", "")
    if declarado.isdigit() and int(declarado) > teto:
        raise _grande_demais(upload)
    partes = bytearray()
    async for pedaco in request.stream():
        partes.extend(pedaco)
        if len(partes) > teto:
            raise _grande_demais(upload)
    if partes and not upload and not repasse.e_json(tipo):
        raise _tipo_nao_suportado()
    return bytes(partes)


def _id_criado(resp: repasse.Resposta) -> str | None:
    """O id do que acabou de ser criado (o caminho do POST ainda não o tem)."""
    if resp.status not in (200, 201) or not repasse.e_json(resp.cabecalhos.get("content-type")):
        return None
    try:
        dados = json.loads(resp.conteudo)
    except ValueError:
        return None
    valor = dados.get("id") if isinstance(dados, dict) else None
    return str(valor) if isinstance(valor, str | int) else None


async def repassar(
    resto: str,
    request: Request,
    user: Annotated[User, Depends(_so_liberado)],
) -> Response:
    """Repassa para `{APP_URANYX_API_URL}/admin/<resto>` (só as rotas do contrato)."""
    caminho = _caminho_cru(request, resto)
    if not repasse.caminho_permitido(caminho):
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail={
                "code": "app_uranyx_rota_desconhecida",
                "message": "Esta rota não existe no módulo App Uranyx.",
            },
        )
    metodo = request.method
    situacao = status.HTTP_500_INTERNAL_SERVER_ERROR
    id_criado: str | None = None
    try:
        tipo = request.headers.get("content-type")
        corpo = await _corpo(request, tipo)
        resp = await repasse.repassar(metodo, caminho, request.url.query, corpo=corpo, tipo=tipo)
        situacao = resp.status
        if metodo == "POST":
            id_criado = _id_criado(resp)
        return Response(content=resp.conteudo, status_code=resp.status, headers=resp.cabecalhos)
    except repasse.Indisponivel as e:
        situacao = status.HTTP_503_SERVICE_UNAVAILABLE
        raise HTTPException(
            situacao, detail={"code": "app_uranyx_indisponivel", "message": e.mensagem}
        ) from None
    except HTTPException as e:
        situacao = e.status_code
        raise
    finally:
        if metodo != "GET":
            logger.info(
                "app_uranyx_acao",
                email=user.email,
                metodo=metodo,
                caminho=f"/{resto}",
                status=situacao,
                id_criado=id_criado,
            )


# Uma rota por método, cada uma com o seu operation_id (no OpenAPI, um
# `api_route` com vários métodos repete o mesmo id).
for _metodo in METODOS:
    router.add_api_route(
        "/{resto:path}",
        repassar,
        methods=[_metodo],
        operation_id=f"app_uranyx_repassar_{_metodo.lower()}",
    )
