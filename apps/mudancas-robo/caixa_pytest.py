"""Plugin do pytest usado SÓ dentro da caixa do robô de mudanças (testar.sh).

A caixa não tem rede — nem 127.0.0.1 (o Redis, o AdsPower e outros programas
deste Mac escutam ali). Teste que precisa abrir ou conectar um socket TCP local
(ex.: sobe um HTTPServer de mentira) falharia SÓ por estar na caixa: ele é
PULADO com o motivo, e o robô avisa no resultado. Qualquer outra falha continua
falha. Revisão de 17/11/2026.
"""

from __future__ import annotations

import errno
import traceback

import pytest

_FUNCOES = {
    "bind",
    "connect",
    "connect_ex",
    "create_connection",
    "create_server",
    "server_bind",
    "start_server",
    "sock_connect",
    "open_connection",
}
_TRECHOS = (".bind(", ".connect(", "create_connection(", "create_server(", "start_server(")


def _bloqueio_de_rede(excinfo: pytest.ExceptionInfo) -> bool:
    exc: BaseException | None = excinfo.value
    vistos: set[int] = set()
    while exc is not None and id(exc) not in vistos:
        vistos.add(id(exc))
        if isinstance(exc, OSError) and exc.errno in (errno.EPERM, errno.EACCES):
            for quadro in traceback.extract_tb(exc.__traceback__):
                if quadro.name in _FUNCOES or any(t in (quadro.line or "") for t in _TRECHOS):
                    return True
        exc = exc.__cause__ or exc.__context__
    return False


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo):  # noqa: ANN201
    resultado = yield
    rep = resultado.get_result()
    if rep.failed and call.excinfo is not None and _bloqueio_de_rede(call.excinfo):
        rep.outcome = "skipped"
        rep.longrepr = (
            str(item.path),
            item.location[1] or 0,
            "Skipped: pulado na caixa do robô — o teste precisa de rede local",
        )
