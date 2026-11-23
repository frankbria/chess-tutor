"""Exit non-zero, naming the env var to change, if any NAME=PORT argument is already in use.

Usage: check_ports.py [--host BIND_HOST] NAME=PORT ..."""

import socket
import sys
from contextlib import closing

# Binding each address separately catches a listener on any one interface, which a
# plain connect or a single 127.0.0.1 bind would miss.
HOSTS = ("0.0.0.0", "127.0.0.1")


def is_free(port: int, bind_host: str) -> bool:
    for host in {*HOSTS, bind_host}:
        with closing(socket.socket()) as probe:
            try:
                probe.bind((host, port))
            except OSError:
                return False
    return True


def bind_host_exists(bind_host: str) -> bool:
    with closing(socket.socket()) as probe:
        try:
            probe.bind((bind_host, 0))
        except OSError:
            return False
    return True


def main(arguments: list[str]) -> int:
    bind_host = "127.0.0.1"
    if arguments[:1] == ["--host"]:
        bind_host, arguments = arguments[1], arguments[2:]
    labelled_ports = arguments
    if not bind_host_exists(bind_host):
        print(
            f"error: cannot bind {bind_host} on this machine. Set DEV_HOST to 127.0.0.1, 0.0.0.0 "
            "or one of this machine's addresses.",
            file=sys.stderr,
        )
        return 1
    busy = []
    claimed: dict[int, str] = {}
    for labelled_port in labelled_ports:
        name, _, port = labelled_port.partition("=")
        if int(port) in claimed:
            print(
                f"error: {claimed[int(port)]} and {name} are both set to port {port}. "
                "Give them different ports.",
                file=sys.stderr,
            )
            return 1
        claimed[int(port)] = name
        if not is_free(int(port), bind_host):
            busy.append((name, int(port)))
    for name, port in busy:
        print(f"error: port {port} is already in use. Set {name} to a free port.", file=sys.stderr)
    return 1 if busy else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
