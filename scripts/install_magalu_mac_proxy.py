#!/usr/bin/python3
"""Instala os dois LaunchAgents Magalu; por padrão apenas confere os arquivos.

Compatível com o Python 3.9 do macOS. Use --apply para gravar e ativar os jobs.
Cada bootstrap é seguido de kickstart, pois o macOS pode adiar o RunAtLoad.
Os overrides de diretório permitem validar tudo em uma pasta temporária.
"""

import argparse
import ast
import json
import os
from pathlib import Path
import plistlib
import secrets
import stat
import subprocess
import sys
import tempfile

PYTHON = "/usr/bin/python3"
LAUNCHCTL = "/bin/launchctl"
LABELS = ("com.davinci.magalu-proxy", "com.davinci.magalu-tunnel")
SOURCE_DIR = Path(__file__).resolve().parent
USERNAME = "davinci-magalu"


class InstallError(Exception):
    pass


def _regular_file(path, private=False):
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
        raise InstallError("Arquivo inválido ou de outro usuário: " + str(path))
    if private and stat.S_IMODE(info.st_mode) != 0o600:
        raise InstallError("credentials.json deve ter permissão 0600.")
    return True


def _directory_chain(home, target):
    for path in (home,) + tuple(home / part for part in _relative_chain(target, home)):
        if path.is_symlink() or (path.exists() and not path.is_dir()):
            raise InstallError("Diretório inválido ou simbólico: " + str(path))
        if path.exists() and path.stat().st_uid != os.getuid():
            raise InstallError("Diretório pertence a outro usuário: " + str(path))


def _relative_chain(target, home):
    parts = target.relative_to(home).parts
    return (Path(*parts[:index]) for index in range(1, len(parts) + 1))


def _read_credentials(path):
    if not _regular_file(path, private=True):
        return None
    if path.stat().st_size > 4096:
        raise InstallError("credentials.json excede o tamanho permitido.")
    try:
        credentials = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        raise InstallError("credentials.json não contém JSON válido.") from None
    if not isinstance(credentials, dict) or set(credentials) != {"username", "password"}:
        raise InstallError("credentials.json deve conter somente username e password.")
    if credentials["username"] != USERNAME:
        raise InstallError("credentials.json contém um usuário inesperado.")
    password = credentials["password"]
    if not isinstance(password, str) or not 1 <= len(password) <= 1024 or any(
        ord(char) < 33 or ord(char) > 126 for char in password
    ):
        raise InstallError("credentials.json contém uma senha inválida.")
    return credentials


def _plist(label, runtime):
    proxy = label == LABELS[0]
    stem = "proxy" if proxy else "tunnel"
    arguments = [PYTHON, str(runtime / (stem + ".py"))]
    if proxy:
        arguments += ["--credentials", str(runtime / "credentials.json"), "--port", "13129"]
    return plistlib.dumps({
        "Label": label,
        "ProgramArguments": arguments,
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 10,
        "Umask": 0o077,
        "WorkingDirectory": str(runtime),
        "StandardOutPath": str(runtime / (stem + ".stdout.log")),
        "StandardErrorPath": str(runtime / (stem + ".stderr.log")),
    }, sort_keys=True)


def plan_install(home, source_dir):
    """Valida todos os inputs e destinos antes de permitir qualquer gravação."""
    home = Path(home).expanduser().absolute()
    source_dir = Path(source_dir).expanduser().absolute()
    runtime = home / "Library" / "Application Support" / "DaVinci" / "magalu-proxy"
    agents = home / "Library" / "LaunchAgents"
    _directory_chain(home, runtime)
    _directory_chain(home, agents)
    artifacts = {}
    for source, destination in (
        ("magalu_mac_proxy.py", "proxy.py"),
        ("magalu_mac_tunnel.py", "tunnel.py"),
    ):
        path = source_dir / source
        if not path.is_file():
            raise InstallError("Fonte ausente: " + str(path))
        content = path.read_bytes()
        try:
            ast.parse(content, filename=str(path), feature_version=(3, 9))
        except (SyntaxError, ValueError):
            raise InstallError("Fonte incompatível com Python 3.9: " + str(path)) from None
        artifacts[runtime / destination] = content
    credentials_path = runtime / "credentials.json"
    credentials = _read_credentials(credentials_path)
    for label in LABELS:
        artifacts[agents / (label + ".plist")] = _plist(label, runtime)
    logs = [runtime / (stem + "." + stream + ".log")
            for stem in ("proxy", "tunnel") for stream in ("stdout", "stderr")]
    issues = []
    if not runtime.exists():
        issues.append("diretório de execução ausente")
    elif stat.S_IMODE(runtime.stat().st_mode) != 0o700:
        issues.append("diretório de execução precisa de permissão 0700")
    if credentials is None:
        issues.append("credenciais locais ausentes")
    for path, content in artifacts.items():
        if not _regular_file(path):
            issues.append("arquivo ausente: " + path.name)
        elif path.read_bytes() != content or stat.S_IMODE(path.stat().st_mode) != 0o600:
            issues.append("arquivo precisa de atualização: " + path.name)
    for path in logs:
        if not _regular_file(path):
            issues.append("log ausente: " + path.name)
        elif stat.S_IMODE(path.stat().st_mode) != 0o600:
            issues.append("log precisa de permissão 0600: " + path.name)
    return {
        "runtime": runtime, "agents": agents, "artifacts": artifacts,
        "credentials_path": credentials_path, "credentials": credentials,
        "logs": logs, "issues": issues,
    }


def _atomic_write(path, content):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=str(path.parent), delete=False) as output:
            temporary = Path(output.name)
            os.fchmod(output.fileno(), 0o600)
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(str(temporary), str(path))
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def apply_install(plan):
    runtime, agents = plan["runtime"], plan["agents"]
    runtime.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(str(runtime), 0o700)
    agents.mkdir(parents=True, exist_ok=True)
    if plan["credentials"] is None:
        credentials = {"username": USERNAME, "password": secrets.token_urlsafe(32)}
        # O_EXCL impede substituir credenciais criadas entre a validação e aqui.
        descriptor = os.open(str(plan["credentials_path"]), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump(credentials, output)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
    for path, content in plan["artifacts"].items():
        if not path.exists() or path.read_bytes() != content:
            _atomic_write(path, content)
        os.chmod(str(path), 0o600)
    for path in plan["logs"]:
        descriptor = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        os.close(descriptor)
        os.chmod(str(path), 0o600)
    domain = "gui/" + str(os.getuid())
    for label in reversed(LABELS):
        result = subprocess.run(
            [LAUNCHCTL, "bootout", domain + "/" + label],
            capture_output=True, text=True, check=False,
        )
        if result.returncode not in (0, 3):  # 3 = serviço ainda não carregado.
            raise InstallError("Não foi possível parar o serviço " + label + ".")
    for label in LABELS:
        result = subprocess.run(
            [LAUNCHCTL, "bootstrap", domain, str(agents / (label + ".plist"))],
            capture_output=True, text=True, check=False,
        )
        if result.returncode:
            raise InstallError("Não foi possível iniciar o serviço " + label + ".")
        # Bootstrap pode registrar sem iniciar (spawn especulativo do launchd).
        # Sem -k: solicita a partida sem matar um processo que já esteja ativo.
        result = subprocess.run(
            [LAUNCHCTL, "kickstart", domain + "/" + label],
            capture_output=True, text=True, check=False,
        )
        if result.returncode:
            raise InstallError("Não foi possível ativar o serviço " + label + ".")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="somente conferir (padrão)")
    mode.add_argument("--apply", action="store_true", help="instalar e ativar os dois serviços")
    parser.add_argument("--home", type=Path, default=Path.home(), help="diretório do usuário")
    parser.add_argument("--source-dir", type=Path, default=SOURCE_DIR, help="pasta dos scripts versionados")
    args = parser.parse_args(argv)
    try:
        plan = plan_install(args.home, args.source_dir)
        if not args.apply:
            if plan["issues"]:
                print("Instalação Magalu pendente; nenhuma alteração realizada:")
                for issue in plan["issues"]:
                    print("- " + issue)
                return 1
            print("Arquivos e permissões da Magalu conferidos; nenhuma alteração realizada.")
            return 0
        apply_install(plan)
        print("Proxy e túnel Magalu instalados e ativados. Credenciais locais preservadas.")
        return 0
    except (InstallError, OSError) as exc:
        print("Instalação Magalu: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
