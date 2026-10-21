"""Piloto dos celulares: somente administradores ativos e aparelhos liberados."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response

from app.deps.auth import require_admin
from app.models import User
from app.services import celulares, celulares_stream

router = APIRouter(prefix="/api/celulares", tags=["celulares"])
Admin = Annotated[User, Depends(require_admin)]
PRIVATE_HEADERS = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}


def _headers(response: Response) -> None:
    response.headers.update(PRIVATE_HEADERS)


def _public_error(exc: celulares.CelularesError) -> HTTPException:
    return HTTPException(
        exc.status, detail={"code": exc.code, "message": exc.message},
        headers=PRIVATE_HEADERS,
    )


@router.get("", response_model=celulares.PhonesOut)
async def list_phones(response: Response, _admin: Admin) -> celulares.PhonesOut:
    _headers(response)
    try:
        result = await celulares.listar()
        result.stream_enabled = result.enabled and celulares_stream.configuration() is not None
        return result
    except celulares.CelularesError as exc:
        raise _public_error(exc) from None


async def _action(name: str, action: celulares.Action) -> celulares.ActionOut:
    try:
        return await celulares.executar(name, action)
    except celulares.CelularesError as exc:
        raise _public_error(exc) from None


@router.post("/{name}/start", response_model=celulares.ActionOut)
async def start_phone(name: str, response: Response, _admin: Admin) -> celulares.ActionOut:
    _headers(response)
    return await _action(name, "start")


@router.post("/{name}/stop", response_model=celulares.ActionOut)
async def stop_phone(name: str, response: Response, _admin: Admin) -> celulares.ActionOut:
    _headers(response)
    return await _action(name, "stop")


@router.post("/{name}/restart", response_model=celulares.ActionOut)
async def restart_phone(name: str, response: Response, _admin: Admin) -> celulares.ActionOut:
    _headers(response)
    return await _action(name, "restart")


@router.post("/{name}/session")
async def open_session(name: str, response: Response, admin: Admin) -> dict[str, str]:
    _headers(response)
    config = celulares_stream.configuration()
    if config is None:
        raise HTTPException(
            503, detail={"code": "celulares_acesso_em_preparacao",
                         "message": "O acesso remoto ainda está em preparação."},
            headers=PRIVATE_HEADERS,
        )
    try:
        _agent, phone = await celulares.verificar_aparelho(name)
    except celulares.CelularesError as exc:
        raise _public_error(exc) from None
    isolation = phone.get("isolamento")
    if not isinstance(isolation, dict):
        isolation = {}
    regular_ready = (name != "vrcamera" and phone.get("read_only") is not True
                     and phone.get("egress") == "proxy"
                     and all(isolation.get(key) is True
                             for key in ("celguard", "bridge_ipv6_off", "dns_proprio")))
    if (phone.get("running") is not True or phone.get("boot_completed") is not True
            or not (regular_ready or celulares.is_camera_pilot(phone))):
        raise HTTPException(
            409, detail={"code": "celular_nao_pronto",
                         "message": "Aguarde o aparelho iniciar e confirmar o isolamento "
                                    "antes de abrir."},
            headers=PRIVATE_HEADERS,
        )
    origin, key = config
    try:
        ticket = celulares_stream.issue_ticket(
            user_id=str(admin.id), phone=name,
            udid=str(phone.get("adb_serial") or ""), key=key,
        )
    except ValueError:
        raise HTTPException(
            502, detail={"code": "celular_acesso_invalido",
                         "message": "Não foi possível preparar o acesso ao aparelho."},
            headers=PRIVATE_HEADERS,
        ) from None
    return {"url": origin + "/session", "ticket": ticket}
