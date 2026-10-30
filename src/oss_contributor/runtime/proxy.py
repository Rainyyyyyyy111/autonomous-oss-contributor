"""TLS CONNECT relay restricted to PyPI; deployed on an internal + egress network."""

import http.server
import ipaddress
import select
import socket

ALLOWED = {"pypi.org", "files.pythonhosted.org"}


class Proxy(http.server.BaseHTTPRequestHandler):
    def do_CONNECT(self):
        host, _, port = self.path.rpartition(":")
        if host.lower() not in ALLOWED or port != "443":
            self.send_error(403)
            return
        addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        address = next((x for x in addresses if ipaddress.ip_address(x[4][0]).is_global), None)
        if address is None:
            self.send_error(403)
            return
        upstream = socket.socket(address[0], address[1], address[2])
        upstream.settimeout(20)
        try:
            upstream.connect(address[4])
            self.send_response(200)
            self.end_headers()
            self.connection.setblocking(False)
            upstream.setblocking(False)
            while True:
                readable, _, _ = select.select([self.connection, upstream], [], [], 30)
                if not readable:
                    break
                for source in readable:
                    chunk = source.recv(65536)
                    if not chunk:
                        return
                    target = upstream if source is self.connection else self.connection
                    target.settimeout(20)
                    target.sendall(chunk)
                    target.setblocking(False)
        except (OSError, TimeoutError):
            return
        finally:
            upstream.close()

    def log_message(self, *_args):
        pass


if __name__ == "__main__":
    http.server.ThreadingHTTPServer(("0.0.0.0", 8888), Proxy).serve_forever()
