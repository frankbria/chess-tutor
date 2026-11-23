import socket
import subprocess
import sys
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_ports.py"


def free_port() -> int:
    with closing(socket.socket()) as probe:
        probe.bind(("127.0.0.1", 0))
        port: int = probe.getsockname()[1]
        return port


@contextmanager
def occupied(host: str) -> Iterator[int]:
    with closing(socket.socket()) as listener:
        listener.bind((host, 0))
        listener.listen()
        yield listener.getsockname()[1]


def run_check(*labelled_ports: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *labelled_ports], capture_output=True, text=True
    )


def test_free_ports_pass() -> None:
    result = run_check(f"CHESS_TUTOR_BACKEND_PORT={free_port()}", f"FRONTEND_PORT={free_port()}")

    assert result.returncode == 0


def test_busy_port_names_the_port_and_the_variable_to_change() -> None:
    with occupied("127.0.0.1") as busy:
        result = run_check(f"CHESS_TUTOR_BACKEND_PORT={free_port()}", f"FRONTEND_PORT={busy}")

    assert result.returncode != 0
    assert f"port {busy}" in result.stderr
    assert "FRONTEND_PORT" in result.stderr


def test_port_busy_on_all_interfaces_only_is_detected() -> None:
    with occupied("0.0.0.0") as busy:
        result = run_check(f"FRONTEND_PORT={busy}")

    assert result.returncode != 0


def test_two_variables_sharing_a_port_are_reported_by_name() -> None:
    port = free_port()
    result = run_check(f"CHESS_TUTOR_BACKEND_PORT={port}", f"FRONTEND_PORT={port}")

    assert result.returncode != 0
    assert "CHESS_TUTOR_BACKEND_PORT" in result.stderr
    assert "FRONTEND_PORT" in result.stderr


def test_bind_host_that_is_not_an_address_of_this_machine_is_reported() -> None:
    result = run_check("--host", "192.0.2.1", f"FRONTEND_PORT={free_port()}")

    assert result.returncode != 0
    assert "192.0.2.1" in result.stderr
    assert "DEV_HOST" in result.stderr


def test_busy_port_is_detected_when_the_bind_host_is_given() -> None:
    with occupied("127.0.0.1") as busy:
        result = run_check("--host", "127.0.0.1", f"FRONTEND_PORT={busy}")

    assert result.returncode != 0
    assert f"port {busy}" in result.stderr
