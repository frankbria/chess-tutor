"""Exit non-zero, naming the env var to change, if any NAME=PORT argument is already in use."""

import socket
import sys
from contextlib import closing

# Binding each address separately catches a listener on any one interface, which a
# plain connect or a single 127.0.0.1 bind would miss.
HOSTS = ("0.0.0.0", "127.0.0.1")


def is_free(port: int) -> bool:
    for host in HOSTS:
        with closing(socket.socket()) as probe:
            try:
                probe.bind((host, port))
            except OSError:
                return False
    return True


def main(labelled_ports: list[str]) -> int:
    busy = []
    for labelled_port in labelled_ports:
        name, _, port = labelled_port.partition("=")
        if not is_free(int(port)):
            busy.append((name, int(port)))
    for name, port in busy:
        print(f"error: port {port} is already in use. Set {name} to a free port.", file=sys.stderr)
    return 1 if busy else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
