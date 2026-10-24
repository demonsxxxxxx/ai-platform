"""Forward Docker Engine HTTP over a private volume-backed Unix socket."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import selectors
import signal
import socket
import socketserver
import threading


_BUFFER_LIMIT = 1024 * 1024
_CHUNK_SIZE = 64 * 1024


class _ForwardingServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True
    block_on_close = True
    allow_reuse_address = True

    def __init__(self, socket_path: str, docker_socket_path: str) -> None:
        self.docker_socket_path = docker_socket_path
        self.stopping = threading.Event()
        super().__init__(socket_path, _ForwardingHandler)


class _ForwardingHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        server: _ForwardingServer = self.server  # type: ignore[assignment]
        upstream = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        selector = selectors.DefaultSelector()
        sockets = (self.request, upstream)
        buffers = {self.request: bytearray(), upstream: bytearray()}
        read_open = {self.request: True, upstream: True}
        write_open = {self.request: True, upstream: True}
        shutdown_when_empty = {self.request: False, upstream: False}
        try:
            upstream.connect(server.docker_socket_path)
            for item in sockets:
                item.setblocking(False)
            self._refresh_selector(selector, sockets, buffers, read_open, write_open)
            while not server.stopping.is_set():
                events = selector.select(timeout=0.5)
                if not events and not any(read_open.values()) and not any(buffers.values()):
                    break
                for key, mask in events:
                    source: socket.socket = key.fileobj
                    target = upstream if source is self.request else self.request
                    if mask & selectors.EVENT_READ:
                        try:
                            payload = source.recv(_CHUNK_SIZE)
                        except BlockingIOError:
                            continue
                        except (ConnectionError, OSError):
                            payload = b""
                        if payload:
                            buffers[target].extend(payload)
                        else:
                            read_open[source] = False
                            shutdown_when_empty[target] = True
                            if not buffers[target]:
                                try:
                                    target.shutdown(socket.SHUT_WR)
                                except OSError:
                                    pass
                                write_open[target] = False
                    if mask & selectors.EVENT_WRITE and buffers[source]:
                        try:
                            written = source.send(buffers[source])
                        except BlockingIOError:
                            continue
                        except (ConnectionError, OSError):
                            written = 0
                        if written <= 0:
                            return
                        del buffers[source][:written]
                        if shutdown_when_empty[source] and not buffers[source]:
                            try:
                                source.shutdown(socket.SHUT_WR)
                            except OSError:
                                pass
                            write_open[source] = False
                    self._refresh_selector(
                        selector, sockets, buffers, read_open, write_open
                    )
        except OSError:
            # One Docker API client may close early; the proxy stays available.
            return
        finally:
            selector.close()
            for item in sockets:
                try:
                    item.close()
                except OSError:
                    pass

    @staticmethod
    def _refresh_selector(
        selector: selectors.BaseSelector,
        sockets: tuple[socket.socket, socket.socket],
        buffers: dict[socket.socket, bytearray],
        read_open: dict[socket.socket, bool],
        write_open: dict[socket.socket, bool],
    ) -> None:
        for item in sockets:
            events = 0
            peer = sockets[1] if item is sockets[0] else sockets[0]
            if read_open[item] and len(buffers[peer]) < _BUFFER_LIMIT:
                events |= selectors.EVENT_READ
            if buffers[item] and write_open[item]:
                events |= selectors.EVENT_WRITE
            try:
                selector.modify(item, events)
            except KeyError:
                if events:
                    selector.register(item, events)
            except ValueError:
                pass
            if events == 0:
                try:
                    selector.unregister(item)
                except (KeyError, ValueError):
                    pass


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--socket-path", default="/socket/docker.sock")
    parser.add_argument("--docker-socket-path", default="/var/run/docker.sock")
    parser.add_argument("--uid", type=int, default=10001)
    parser.add_argument("--gid", type=int, default=10001)
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    socket_path = Path(args.socket_path)
    socket_path.parent.mkdir(parents=True, exist_ok=True)
    os.chown(socket_path.parent, args.uid, args.gid)
    os.chmod(socket_path.parent, 0o755)
    try:
        socket_path.unlink()
    except FileNotFoundError:
        pass

    server = _ForwardingServer(str(socket_path), args.docker_socket_path)
    os.chown(socket_path, args.uid, args.gid)
    os.chmod(socket_path, 0o660)

    def stop(_signum: int, _frame: object) -> None:
        server.stopping.set()
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    print("lifecycle-docker-proxy-ready", flush=True)
    try:
        server.serve_forever(poll_interval=0.1)
    finally:
        server.stopping.set()
        server.server_close()
        try:
            socket_path.unlink()
        except FileNotFoundError:
            pass


if __name__ == "__main__":
    main()
