#!/usr/bin/python3
"""Mantém a saída Magalu do servidor ligada ao proxy de loopback deste Mac.

O launchd reinicia este processo quando o SSH termina. Um heartbeat libera o
próprio inode remoto após 75s sem sinal, permitindo reconexão mesmo se o sshd
antigo ainda aguardar seu timeout TCP. Os destinos são fixos; nenhuma credencial
OAuth ou senha do proxy passa pela linha de comando.
"""

import shlex
import signal
import subprocess
import sys

SSH = "/usr/bin/ssh"
SSH_HOST = "davinci-prod"
REMOTE_SOCKET = "/opt/davinci/data/magalu-proxy/magalu.sock"
LOCAL_DESTINATION = "127.0.0.1:13129"
HEARTBEAT_SECONDS = 15
SSH_OPTIONS = [
    "-T",
    "-o", "BatchMode=yes",
    "-o", "ControlMaster=no",
    "-o", "ControlPath=none",
    "-o", "ConnectTimeout=10",
    "-o", "ServerAliveInterval=30",
    "-o", "ServerAliveCountMax=3",
]

# Executado pelo Python padrão do servidor via stdin. A mesma função pode ser
# testada com sockets locais temporários, sem SSH ou acesso ao servidor.
REMOTE_PREPARE_SCRIPT = r'''
import errno
import os
from pathlib import Path
import socket
import stat
import sys


def prepare_socket(path):
    path = Path(path)
    directory = path.parent
    # Não atravessa diretórios simbólicos nem troca um arquivo por diretório.
    for parent in reversed((directory,) + tuple(directory.parents)):
        if parent.is_symlink():
            raise RuntimeError("diretório do socket não pode ser um link simbólico")
        if parent.exists() and not parent.is_dir():
            raise RuntimeError("diretório do socket inválido")
    directory.mkdir(mode=0o700, exist_ok=True)
    os.chmod(str(directory), 0o700)
    try:
        original = path.lstat()
    except FileNotFoundError:
        return "ausente"
    if not stat.S_ISSOCK(original.st_mode):
        raise RuntimeError("destino remoto existe e não é um socket")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
        probe.settimeout(2.0)
        try:
            probe.connect(str(path))
        except OSError as exc:
            if exc.errno != errno.ECONNREFUSED:
                raise RuntimeError("não foi possível verificar o socket; preservado") from None
        else:
            raise RuntimeError("socket remoto já tem listener ativo; preservado")
    # Uma outra partida pode ter substituído o socket durante a prova.
    try:
        current = path.lstat()
    except FileNotFoundError:
        return "ausente"
    if (current.st_dev, current.st_ino) != (original.st_dev, original.st_ino):
        raise RuntimeError("socket remoto mudou durante a verificação; preservado")
    path.unlink()
    return "obsoleto removido"


if __name__ == "__main__":
    try:
        prepare_socket(sys.argv[1])
    except (OSError, RuntimeError) as exc:
        print("Preparação Magalu: " + str(exc), file=sys.stderr)
        raise SystemExit(1)
'''


REMOTE_WATCH_SCRIPT = r'''
import os
from pathlib import Path
import select
import stat
import sys
import time


def watch_socket(path, heartbeat_fd=0):
    path = Path(path)
    # O comando só acompanha o socket criado pelo -R desta conexão. Aguarda
    # a confirmação do forward; nunca inicia sobre um caminho não socket.
    deadline = time.monotonic() + 10
    while True:
        try:
            original = path.lstat()
            break
        except FileNotFoundError:
            if time.monotonic() >= deadline:
                raise RuntimeError("socket do túnel não apareceu") from None
            time.sleep(0.1)
    if not stat.S_ISSOCK(original.st_mode):
        raise RuntimeError("destino do túnel não é um socket")
    try:
        while select.select([heartbeat_fd], [], [], 75)[0]:
            if not os.read(heartbeat_fd, 1):
                break
    finally:
        try:
            current = path.lstat()
        except FileNotFoundError:
            current = None
        if (current is not None and stat.S_ISSOCK(current.st_mode)
                and (current.st_dev, current.st_ino) == (original.st_dev, original.st_ino)):
            path.unlink()


if __name__ == "__main__":
    try:
        watch_socket("/opt/davinci/data/magalu-proxy/magalu.sock")
    except (OSError, RuntimeError) as exc:
        print("Monitor Magalu: " + str(exc), file=sys.stderr)
        raise SystemExit(1)
'''


def prepare_command():
    return [SSH] + SSH_OPTIONS + [SSH_HOST, "/usr/bin/python3", "-", REMOTE_SOCKET]


def tunnel_command():
    return [SSH] + SSH_OPTIONS + [
        # O alias pode herdar ClearAllForwardings=yes; este comando precisa
        # habilitar explicitamente seu -R, sem alterar a configuração global.
        "-o", "ClearAllForwardings=no",
        "-o", "ExitOnForwardFailure=yes",
        "-R", REMOTE_SOCKET + ":" + LOCAL_DESTINATION,
        SSH_HOST,
        "exec /usr/bin/python3 -c " + shlex.quote(REMOTE_WATCH_SCRIPT),
    ]


def _stop(signum, frame):
    raise SystemExit(128 + signum)


def _run_tunnel(command):
    child = subprocess.Popen(command, stdin=subprocess.PIPE)
    try:
        while child.poll() is None:
            try:
                child.stdin.write(b".")
                child.stdin.flush()
                child.wait(timeout=HEARTBEAT_SECONDS)
            except subprocess.TimeoutExpired:
                continue
            except (BrokenPipeError, OSError):
                return 1
        return child.returncode
    finally:
        try:
            child.stdin.close()
        except OSError:
            pass
        if child.poll() is None:
            try:
                child.terminate()
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=5)
            except ProcessLookupError:
                child.wait(timeout=5)


def main():
    if len(sys.argv) != 1:
        print("Este túnel usa destinos fixos e não aceita argumentos.", file=sys.stderr)
        return 2
    previous_handlers = {}
    try:
        for signum in (signal.SIGTERM, signal.SIGINT):
            previous_handlers[signum] = signal.signal(signum, _stop)
        result = subprocess.run(
            prepare_command(),
            input=REMOTE_PREPARE_SCRIPT,
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
        if result.returncode:
            print("Não foi possível preparar o socket remoto da Magalu.", file=sys.stderr)
            if result.stderr:
                print(result.stderr.strip(), file=sys.stderr)
            return 1
        return _run_tunnel(tunnel_command())
    except subprocess.TimeoutExpired:
        print("Tempo esgotado ao preparar o túnel da Magalu.", file=sys.stderr)
        return 1
    except OSError as exc:
        print("Falha ao iniciar o túnel da Magalu: " + type(exc).__name__, file=sys.stderr)
        return 1
    finally:
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)


if __name__ == "__main__":
    raise SystemExit(main())
